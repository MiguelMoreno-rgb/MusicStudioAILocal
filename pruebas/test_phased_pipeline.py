"""Lifecycle, safety and ownership regressions; no torch or checkpoints."""
from contextlib import contextmanager
import gc
from pathlib import Path
import threading
import tempfile
import types
import unittest
import weakref
from unittest.mock import Mock, patch

from musicstudio.phased_pipeline import DeferredComponent, PhasedPipelineMixin, instrument_model_operations, validate_phase_request


class Trace:
    def __init__(self):
        self.events = []

    def emit(self, operation, event, **details):
        self.events.append((operation, event, details))

    @contextmanager
    def span(self, operation, **details):
        self.emit(operation, "begin", **details)
        try:
            yield
        except BaseException:
            self.emit(operation, "error", **details)
            raise
        else:
            self.emit(operation, "end", **details)


class Component:
    def __init__(self, name):
        self.name = name

    def to(self, *args, **kwargs):
        raise AssertionError("Retirement must never copy a component back to CPU")

    def prepare_condition(self, value):
        return value + 1

    def generate_audio(self, value):
        return value + 2


class Host(PhasedPipelineMixin):
    def __init__(self):
        self.memory_trace = Trace()
        self.config = types.SimpleNamespace(is_turbo=True)
        self.dtype = "bf16"
        self.device = "cuda"
        self.current_offload_cost = 0.0
        self.refs = []
        self.max_live = 0
        for name in ("model", "vae", "text_encoder"):
            self._register_component(name, name)

    def _materialize_component(self, name):
        self.assert_only_reservations()
        component = Component(name)
        self.refs.append(weakref.ref(component))
        setattr(self, name, component)
        self.max_live = max(self.max_live, sum(ref() is not None for ref in self.refs))

    def assert_only_reservations(self):
        assert all(isinstance(getattr(self, n), DeferredComponent) for n in self.phase_specs)
        assert all(ref() is None for ref in self.refs)

    def _get_vae_dtype(self):
        return self.dtype

    def _phase_preflight(self):
        pass  # Tested separately against deterministic system-available values.

    def _phase_vram_preflight(self, name):
        pass  # Tested separately against deterministic CUDA-budget values.

    def _recursive_to_device(self, model, device, dtype):
        self.memory_trace.emit("synthetic.transfer", "end", target=device)

    def _assert_phase_tensors(self, *args, **kwargs):
        pass  # Real tensors are covered by the optional native runtime tests.

    def _synchronize(self):
        pass

    def _release_system_memory(self):
        gc.collect()


class LifecycleTests(unittest.TestCase):
    def test_initialization_registers_only_metadata_tokenizer_and_small_silence_tensor(self):
        host = PhasedPipelineMixin()
        host.memory_trace = Trace()
        host.offload_to_cpu = host.offload_dit_to_cpu = True
        host.dtype = "bf16"
        host._sync_alignment_config = lambda: None
        tensor = types.SimpleNamespace()
        tensor.transpose = lambda *args: tensor
        tensor.to = lambda **kwargs: tensor
        torch = types.SimpleNamespace(bfloat16="bf16", load=Mock(return_value=tensor))
        transformers = types.SimpleNamespace(
            AutoConfig=types.SimpleNamespace(from_pretrained=Mock(return_value=types.SimpleNamespace(is_turbo=True))),
            AutoModel=types.SimpleNamespace(from_pretrained=Mock(side_effect=AssertionError("weights must be deferred"))),
            AutoTokenizer=types.SimpleNamespace(from_pretrained=Mock(return_value="local tokenizer")),
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", torch=torch, transformers=transformers):
            for name in ("vae", "Qwen3-Embedding-0.6B"):
                (Path(directory) / name).mkdir()
            host._load_main_model_from_checkpoint(model_checkpoint_path=str(Path(directory) / "acestep-v15-turbo"),
                device="cuda", use_flash_attention=False, compile_model=False, quantization="int8_weight_only")
            host._load_vae_model(checkpoint_dir=directory, device="cuda", compile_model=False)
            host._load_text_encoder_and_tokenizer(checkpoint_dir=directory, device="cuda")
        transformers.AutoModel.from_pretrained.assert_not_called()
        transformers.AutoTokenizer.from_pretrained.assert_called_once_with(str(Path(directory) / "Qwen3-Embedding-0.6B"), local_files_only=True)
        torch.load.assert_called_once_with(str(Path(directory) / "acestep-v15-turbo/silence_latent.pt"), map_location="cpu", weights_only=True)
        self.assertEqual(set(host.phase_specs), {"vae", "model", "text_encoder"})
        self.assertTrue(all(isinstance(getattr(host, n), DeferredComponent) for n in host.phase_specs))
        self.assertFalse(host.phase_report["dit_cuda_validated"])

    def test_full_upstream_context_sequence_has_at_most_one_live_component(self):
        host = Host()
        for name in ("vae", "vae", "text_encoder", "model", "vae"):
            with host._load_model_context(name):
                self.assertIsInstance(getattr(host, name), Component)
                self.assertEqual(sum(ref() is not None for ref in host.refs), 1)
            host.assert_only_reservations()
        self.assertEqual(host.max_live, 1)
        self.assertEqual(host.phase_report["loads"], {"vae": 3, "text_encoder": 1, "model": 1})
        self.assertEqual(host.phase_report["loads"], host.phase_report["releases"])
        self.assertTrue(host._phase_decode_completed)

    def test_model_cannot_load_before_text_and_a_failed_handler_cannot_retry(self):
        host = Host()
        with self.assertRaisesRegex(RuntimeError, "Orden"):
            with host._load_model_context("model"):
                self.fail("Out-of-order compute must not run")
        with self.assertRaisesRegex(RuntimeError, "no se reintenta"):
            with host._load_model_context("vae"):
                self.fail()

    def test_loader_and_transfer_failures_never_enter_compute_and_release_references(self):
        for method in ("_materialize_component", "_recursive_to_device", "_phase_vram_preflight", "_assert_phase_tensors"):
            with self.subTest(method=method):
                host = Host()
                def fail(*args, **kwargs):
                    raise RuntimeError("injected failure")
                # A Mock records its model argument and would itself keep
                # the component alive after retirement. Use a real failing
                # callable so this test measures production ownership.
                with patch.object(host, method, new=fail):
                    with self.assertRaisesRegex(RuntimeError, "injected failure"):
                        with host._load_model_context("vae"):
                            self.fail("Compute must not run after a failed load/validation")
                host.assert_only_reservations()
                self.assertTrue(host._phase_failed)
                self.assertFalse(host._phase_lock.locked())

    def test_compute_failure_is_preserved_even_if_cleanup_also_fails(self):
        host = Host()
        with patch.object(host, "_synchronize", side_effect=RuntimeError("cleanup failure")):
            with self.assertRaisesRegex(ValueError, "compute failure"):
                with host._load_model_context("vae"):
                    raise ValueError("compute failure")
        host.assert_only_reservations()
        self.assertTrue(any(row[1] == "error" for row in host.memory_trace.events))

    def test_concurrent_and_nested_contexts_fail_without_releasing_the_active_model(self):
        host = Host()
        errors = []
        def concurrent():
            try:
                with host._load_model_context("text_encoder"):
                    self.fail()
            except RuntimeError as exc:
                errors.append(str(exc))
        with host._load_model_context("vae"):
            active = weakref.ref(host.vae)
            thread = threading.Thread(target=concurrent)
            thread.start(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertIsNotNone(active())
            with self.assertRaisesRegex(RuntimeError, "simultaneas"):
                with host._load_model_context("vae"):
                    self.fail()
        self.assertIn("simultaneas", errors[0])
        self.assertIsNone(active())

    def test_instrumentation_keeps_events_but_no_bound_reference_or_self_cycle(self):
        model = Component("model")
        trace = Trace()
        instrument_model_operations(model, trace)
        function = model.prepare_condition
        self.assertEqual(function(5), 6)
        self.assertEqual(model.generate_audio(5), 7)
        ref = weakref.ref(model)
        del model
        self.assertIsNone(ref())  # No GC needed: even a retained wrapper is weak.
        with self.assertRaisesRegex(RuntimeError, "liberado"):
            function(5)
        self.assertEqual([row[0] for row in trace.events], ["dit.prepare_condition"] * 2 + ["dit.generate_audio"] * 2)


class SafetyTests(unittest.TestCase):
    def test_lora_or_premature_decoder_verification_cannot_bypass_validation(self):
        host = Host()
        host.lora_loaded = True
        with self.assertRaisesRegex(RuntimeError, "LoRA"):
            with host._load_model_context("vae"):
                self.fail()
        with self.assertRaisesRegex(RuntimeError, "no se admite LoRA"):
            host._verify_decoder_device_dtype()

    def test_tensor_checks_reject_wrong_parameters_and_post_transfer_buffer_dtypes(self):
        host = Host()
        parameter = types.SimpleNamespace(device=types.SimpleNamespace(type="cpu"), dtype="bf16", is_floating_point=lambda: True)
        buffer = types.SimpleNamespace(device=types.SimpleNamespace(type="cpu"), dtype="fp32", is_floating_point=lambda: True)
        host.vae = types.SimpleNamespace(parameters=lambda: iter([parameter]), buffers=lambda: iter([buffer]))
        PhasedPipelineMixin._assert_phase_tensors(host, "vae", "cpu", "bf16", buffer_dtype=False)
        with self.assertRaisesRegex(RuntimeError, "dtype"):
            PhasedPipelineMixin._assert_phase_tensors(host, "vae", "cpu", "bf16")
        parameter.dtype = "fp32"
        with self.assertRaisesRegex(RuntimeError, "dtype"):
            PhasedPipelineMixin._assert_phase_tensors(host, "vae", "cpu", "bf16", buffer_dtype=False)
        parameter.dtype = "bf16"
        with self.assertRaisesRegex(RuntimeError, "se requiere cuda"):
            PhasedPipelineMixin._assert_phase_tensors(host, "vae", "cuda", "bf16")

    def test_profile_rejects_lm_longer_duration_and_unknown_values(self):
        self.assertFalse(validate_phase_request({}))
        self.assertTrue(validate_phase_request({"memory_profile": "phased_v1", "use_lm": False, "duration": 30}))
        for request in ({"memory_profile": "unknown"}, {"memory_profile": "phased_v1", "use_lm": True, "duration": 30},
                        {"memory_profile": "phased_v1", "use_lm": False, "duration": 240}):
            with self.assertRaises(ValueError):
                validate_phase_request(request)

    def test_ram_preflight_uses_available_physical_bytes_and_stops_before_loading(self):
        host = Host()
        host._phase_preflight = types.MethodType(PhasedPipelineMixin._phase_preflight, host)
        fake = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(available=4 * 1024**3 - 1))
        with patch.dict("sys.modules", psutil=fake), patch.object(host, "_materialize_component") as load:
            with self.assertRaisesRegex(RuntimeError, "4 GiB"):
                with host._load_model_context("vae"):
                    self.fail()
            load.assert_not_called()

    def test_vram_preflight_respects_global_free_and_allocator_ceiling(self):
        host = Host()
        host.phase_allocator_budget_bytes = 6 * 1024**3
        cuda = types.SimpleNamespace(mem_get_info=lambda: (8 * 1024**3, 8 * 1024**3), memory_allocated=lambda: 0)
        fake = types.SimpleNamespace(cuda=cuda)
        with patch.dict("sys.modules", torch=fake), patch("musicstudio.phased_pipeline.component_payload_bytes", return_value=5 * 1024**3):
            with self.assertRaisesRegex(RuntimeError, "VRAM"):
                PhasedPipelineMixin._phase_vram_preflight(host, "model")
        with patch.dict("sys.modules", torch=fake), patch("musicstudio.phased_pipeline.component_payload_bytes", return_value=1024**3):
            PhasedPipelineMixin._phase_vram_preflight(host, "model")
            cuda.mem_get_info = lambda: (2 * 1024**3, 8 * 1024**3)
            with self.assertRaisesRegex(RuntimeError, "VRAM"):
                PhasedPipelineMixin._phase_vram_preflight(host, "model")


if __name__ == "__main__":
    unittest.main()
