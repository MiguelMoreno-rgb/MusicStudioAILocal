"""Opt-in, one-shot component lifetimes for the pinned text2music pipeline.

Only metadata/tokenizer/silence are resident at initialization. The existing
ACE context boundaries materialize one component and retire it without a CPU
round-trip. This changes ownership, not sampling or musical weights.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
import threading
import time
import weakref

from .streaming_offload import storage_bytes
from .text_encoder_memory import load_qwen_encoder

PROFILE = "phased_v1"
GIB = 1024**3


def validate_phase_request(request):
    profile = request.get("memory_profile", "streaming_aqt_v1")
    if profile not in ("streaming_aqt_v1", PROFILE):
        raise ValueError("Perfil de memoria desconocido.")
    if profile == PROFILE and (request.get("use_lm") is not False or request.get("duration") != 30):
        raise ValueError("La candidata por fases exige sin LM y exactamente 30 segundos.")
    return profile == PROFILE


@dataclass(frozen=True)
class DeferredComponent:
    """Verified loader reservation; deliberately has no tensor/model methods.

    Upstream's readiness test checks for None. A reservation means its loader
    is configured, NOT that weights/INT8/CUDA have already been validated.
    Real checks run in the context before compute is allowed.
    """
    name: str
    config: object = None


def instrument_model_operations(model, trace):
    """Keep operation traces without a closure retaining a bound model method."""
    for name in ("prepare_condition", "generate_audio"):
        original = getattr(model, name)
        weak_method = weakref.WeakMethod(original)

        def wrapper(method_ref, operation, method):
            @wraps(method)
            def measured(*args, **kwargs):
                bound = method_ref()
                if bound is None:
                    raise RuntimeError("El componente ya fue liberado: " + operation)
                with trace.span(operation):
                    return bound(*args, **kwargs)
            # functools.wraps sets __wrapped__ to the bound method. Remove
            # that reference too, otherwise it keeps the entire model alive.
            del measured.__wrapped__
            return measured

        setattr(model, name, wrapper(weak_method, "dit." + name, original))


def component_payload_bytes(model):
    # named_* removes tied parameters; views can still overlap. This is a
    # conservative tensor-payload estimate, never RSS/private bytes.
    return sum(storage_bytes(t) for t in (*model.parameters(), *model.buffers()))


class PhasedPipelineMixin:
    """CUDA/offload only; disposable handler, no LM, LoRA, cover or retakes."""

    def _phase_setup(self):
        if not hasattr(self, "phase_specs"):
            self.phase_specs = {}
            self.phase_report = {"profile": PROFILE, "loads": {}, "releases": {}, "components": {},
                                 "load_seconds": {}, "release_seconds": {}, "dit_cuda_validated": False}
            self._phase_lock = threading.Lock()
            self._phase_active = None
            self._phase_stage = 0
            self._phase_failed = False
            self._phase_decode_completed = False

    def _register_component(self, name, spec):
        self._phase_setup()
        if self._phase_active is not None or name in self.phase_specs:
            raise RuntimeError("No se puede reinicializar un handler por fases.")
        self.phase_specs[name] = spec
        setattr(self, name, DeferredComponent(name, self.config if name == "model" else None))

    def _load_main_model_from_checkpoint(self, *, model_checkpoint_path, device,
                                         use_flash_attention, compile_model, quantization):
        import torch
        from transformers import AutoConfig
        if (str(device) != "cuda" or not self.offload_to_cpu or not self.offload_dit_to_cpu
                or self.dtype != torch.bfloat16 or compile_model or use_flash_attention
                or quantization != "int8_weight_only"):
            raise RuntimeError("Perfil por fases incompatible: requiere CUDA/BF16/INT8/offload sin compile.")
        path = Path(model_checkpoint_path)
        self.config = AutoConfig.from_pretrained(str(path), trust_remote_code=True, local_files_only=True)
        if not self.config.is_turbo:
            raise RuntimeError("La candidata solo admite el checkpoint Turbo fijado.")
        self.config._attn_implementation = "sdpa"
        self._sync_alignment_config()
        self.silence_latent = torch.load(str(path / "silence_latent.pt"), map_location="cpu", weights_only=True).transpose(1, 2).to(dtype=self.dtype)
        self._register_component("model", str(path))
        self.memory_trace.emit("phase.register", "metadata_only", component="model")
        return "sdpa"

    def _load_vae_model(self, *, checkpoint_dir, device, compile_model, vae_variant=None):
        if vae_variant not in (None, "official") or compile_model or str(device) != "cuda":
            raise RuntimeError("Solo se admite VAE oficial sin compile en la candidata.")
        path = Path(checkpoint_dir) / "vae"
        if not path.is_dir():
            raise FileNotFoundError(path)
        self._register_component("vae", str(path))
        return str(path)

    def _load_text_encoder_and_tokenizer(self, *, checkpoint_dir, device):
        from transformers import AutoTokenizer
        path = Path(checkpoint_dir) / "Qwen3-Embedding-0.6B"
        if not path.is_dir():
            raise FileNotFoundError(path)
        self.text_tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        self._register_component("text_encoder", str(path))
        return str(path)

    def _verify_decoder_device_dtype(self):
        if isinstance(self.model, DeferredComponent):
            raise RuntimeError("DiT aun no cargado; no se admite LoRA/verificacion fuera de su fase.")
        # Actual validation uses the streaming transfer, never Module.to(AQT).
        self._assert_phase_tensors("model", "cuda", self.dtype)

    @staticmethod
    def _build_quantization_config(quantization):
        if quantization != "int8_weight_only":
            raise RuntimeError("La candidata no admite cambiar la cuantizacion.")
        from torchao.quantization import Int8WeightOnlyConfig
        return Int8WeightOnlyConfig(version=1)

    def _phase_preflight(self):
        import psutil
        if psutil.virtual_memory().available < 4 * GIB:
            raise RuntimeError("Menos de 4 GiB RAM disponible antes de cargar la siguiente fase.")

    def _assert_phase_tensors(self, name, device, dtype, *, buffer_dtype=True):
        model = getattr(self, name)
        count = 0
        for tensor, is_buffer in [(p, False) for p in model.parameters()] + [(b, True) for b in model.buffers()]:
            count += 1
            if tensor.device.type != device:
                raise RuntimeError(f"{name}: tensor en {tensor.device}, se requiere {device}.")
            if (not is_buffer or buffer_dtype) and tensor.is_floating_point() and tensor.dtype != dtype:
                raise RuntimeError(f"{name}: dtype {tensor.dtype}, se requiere {dtype}.")
        if not count:
            raise RuntimeError(name + ": modelo sin tensores.")

    def _materialize_component(self, name):
        import torch
        path = self.phase_specs[name]
        # Match initialization's normal (non-inference) tensor creation even
        # when ACE's service thread calls this under inference_mode().
        # Moving initialization into inference must not consume the sampler's
        # CPU/CUDA RNG stream. The worker owns only cuda:0 and runs one take.
        with torch.random.fork_rng(devices=[torch.cuda.current_device()]), torch.inference_mode(False), torch.no_grad():
            if name == "model":
                from transformers import AutoModel
                self.model = AutoModel.from_pretrained(path, dtype=self.dtype, local_files_only=True,
                                                       trust_remote_code=True, attn_implementation="sdpa")
                self.config = self.model.config
                self.config._attn_implementation = "sdpa"
                self._sync_alignment_config()
                self._apply_cuda_bool_argsort_workaround()
                self._assert_phase_tensors(name, "cpu", self.dtype, buffer_dtype=False)
                # Match ACE's pre-quantization buffer normalization. Parameters
                # already have this dtype; this is not a full converted copy.
                self.model.to(dtype=self.dtype)
                self.phase_report["dit_before_quantization_payload_bytes"] = component_payload_bytes(self.model)
                self.phase_report["dit_largest_dense_parameter_bytes"] = max(storage_bytes(p) for p in self.model.parameters())
                with self.memory_trace.span("phase.model.quantize"):
                    # The pinned ACE filter quantizes decoder linears only,
                    # excluding tokenizer/detokenizer. Do not widen its scope.
                    self._apply_dit_quantization("int8_weight_only")
                from .validate import int8_layers
                count = int8_layers(self.model.decoder)
                if not count:
                    raise RuntimeError("INT8 no efectivo; no se permite fallback BF16.")
                self.phase_report["int8_linear_layers"] = count
                instrument_model_operations(self.model, self.memory_trace)
            elif name == "text_encoder":
                self.text_encoder = load_qwen_encoder(path, self.dtype)
            elif name == "vae":
                from diffusers.models import AutoencoderOobleck
                self.vae = AutoencoderOobleck.from_pretrained(
                    path, torch_dtype=self._get_vae_dtype(), local_files_only=True, low_cpu_mem_usage=True,
                )
            else:
                raise RuntimeError("Componente no admitido: " + name)
            getattr(self, name).eval()
        dtype = self._get_vae_dtype() if name == "vae" else self.dtype
        self._assert_phase_tensors(name, "cpu", dtype, buffer_dtype=False)

    def _phase_vram_preflight(self, name):
        import torch
        payload = component_payload_bytes(getattr(self, name))
        headroom = int(1.5 * GIB) if name == "model" else GIB // 2
        free, _ = torch.cuda.mem_get_info()
        remaining = min(free - 768 * 1024**2,
                        self.phase_allocator_budget_bytes - torch.cuda.memory_allocated())
        self.phase_report["components"][name] = dict(tensor_payload_bytes=payload,
            activation_reserve_bytes=headroom, available_transfer_budget_bytes=int(remaining))
        self.memory_trace.emit("phase.vram_preflight", "check", component=name,
                               **self.phase_report["components"][name])
        if payload + headroom > remaining:
            raise RuntimeError(name + ": no hay margen VRAM para pesos y reserva de activaciones; no se transfiere.")

    def _retire_component(self, name):
        # No local model/bound-method reference may survive into GC here.
        started = time.perf_counter()
        self.memory_trace.emit("phase.release", "begin", component=name)
        try:
            self._synchronize()
        finally:
            setattr(self, name, DeferredComponent(name, self.config if name == "model" else None))
            self._release_system_memory()
            self.phase_report["releases"][name] = self.phase_report["releases"].get(name, 0) + 1
            elapsed = time.perf_counter() - started
            self.phase_report["release_seconds"][name] = self.phase_report["release_seconds"].get(name, 0) + elapsed
            self.current_offload_cost += elapsed
            self.memory_trace.emit("phase.release", "end", component=name, cpu_return_copy=False)

    def _check_phase_order(self, name):
        if self._phase_failed or self._phase_decode_completed:
            raise RuntimeError("Handler finalizado o fallido; no se reintenta en este proceso.")
        if getattr(self, "lora_loaded", False) or getattr(self, "use_lora", False):
            raise RuntimeError("La candidata por fases no admite LoRA.")
        if (name == "model" and self._phase_stage != 1
                or name == "text_encoder" and self._phase_stage != 0
                or name == "vae" and self._phase_stage not in (0, 2)):
            raise RuntimeError("Orden de componentes incompatible con la candidata text2music.")

    @contextmanager
    def _load_model_context(self, model_name):
        self._phase_setup()
        if model_name not in self.phase_specs:
            raise RuntimeError("Componente no registrado: " + model_name)
        if not self._phase_lock.acquire(blocking=False):
            raise RuntimeError("No se permiten fases simultaneas o anidadas.")
        failed = False
        self._phase_active = model_name
        started = time.perf_counter()
        try:
            self._check_phase_order(model_name)
            self._phase_preflight()
            with self.memory_trace.span("phase.load." + model_name):
                self._materialize_component(model_name)
                self.phase_report["loads"][model_name] = self.phase_report["loads"].get(model_name, 0) + 1
                self._phase_vram_preflight(model_name)
                dtype = self._get_vae_dtype() if model_name == "vae" else self.dtype
                self._recursive_to_device(getattr(self, model_name), self.device, dtype)
                self._assert_phase_tensors(model_name, "cuda", dtype)
                if model_name == "model":
                    self.phase_report["dit_cuda_validated"] = True
                self._release_system_memory()
            elapsed = time.perf_counter() - started
            self.current_offload_cost += elapsed
            self.phase_report["load_seconds"][model_name] = self.phase_report["load_seconds"].get(model_name, 0) + elapsed
            with self.memory_trace.span("compute." + model_name):
                yield
            if model_name == "text_encoder":
                self._phase_stage = 1
            elif model_name == "model":
                self._phase_stage = 2
            elif model_name == "vae" and self._phase_stage == 2:
                self._phase_decode_completed = True
        except BaseException:
            failed = self._phase_failed = True
            raise
        finally:
            try:
                self._retire_component(model_name)
            except Exception as exc:
                self._phase_failed = True
                self.memory_trace.emit("phase.release", "error", component=model_name, error=repr(exc))
                if not failed:
                    raise
            finally:
                self._phase_active = None
                self._phase_lock.release()
