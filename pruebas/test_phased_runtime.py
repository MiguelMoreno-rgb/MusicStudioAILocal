"""Optional native stack tests with tiny random weights, never ACE checkpoints."""
import importlib.util
import tempfile
from pathlib import Path
import types
import unittest
import weakref
from unittest.mock import patch

HAS_STACK = all(importlib.util.find_spec(name) is not None for name in ("torch", "torchao", "transformers", "diffusers"))


@unittest.skipUnless(HAS_STACK, "Requiere el stack existente; no se instala nada.")
class NativePhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch, torchao, transformers, diffusers
        actual = (torch.__version__.split("+")[0], torchao.__version__, transformers.__version__, diffusers.__version__)
        if actual != ("2.10.0", "0.16.0", "4.57.6", "0.37.1"):
            raise RuntimeError("Stack nativo fuera de las versiones fijadas: " + str(actual))

    def test_real_transformers_loads_tiny_qwen_cpu_bf16_then_runs_cuda_forward(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("Requiere CUDA real; produccion no infiere Qwen en CPU.")
        from transformers import Qwen3Config, Qwen3Model
        from musicstudio.text_encoder_memory import load_qwen_encoder
        config = Qwen3Config(hidden_size=32, intermediate_size=64, num_hidden_layers=1,
                             num_attention_heads=4, num_key_value_heads=2, head_dim=8, vocab_size=128)
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"):
            model = Qwen3Model(config).eval()
            model.save_pretrained(directory, safe_serialization=True)
            del model
            encoder = load_qwen_encoder(Path(directory), torch.bfloat16)
            self.assertTrue(all(p.device.type == "cpu" and p.dtype == torch.bfloat16 for p in encoder.parameters()))
            encoder.to(device="cuda", dtype=torch.bfloat16)
            with torch.inference_mode():
                output = encoder(input_ids=torch.tensor([[1, 2, 3]], device="cuda")).last_hidden_state
            self.assertEqual(output.shape, (1, 3, 32))
            self.assertTrue(torch.isfinite(output).all().item())

    def test_real_cuda_int8_phase_transfer_forward_and_retirement_without_cpu_return(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("Requiere CUDA real; no se acredita por CPU/meta.")
        from musicstudio.common import enable_source
        enable_source()  # Existing, hash-verified source; never fetched by tests.
        from acestep.handler import AceStepHandler
        from musicstudio.phased_pipeline import PhasedPipelineMixin, DeferredComponent
        from musicstudio.streaming_offload import StreamingOffloadMixin
        from musicstudio.text_encoder_memory import TextEncoderCPUOffloadMixin
        from pruebas.test_phased_pipeline import Trace

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.config = types.SimpleNamespace(is_turbo=True)
                self.decoder = torch.nn.Sequential(torch.nn.Linear(64, 64, bias=False, dtype=torch.bfloat16))
                self.register_buffer("rotary_probe", torch.ones(2, dtype=torch.float32))

            def prepare_condition(self, value):
                return value

            def generate_audio(self, value):
                return self.decoder(value)

        Handler = type("SyntheticPhasedHandler", (PhasedPipelineMixin, StreamingOffloadMixin,
                                                  TextEncoderCPUOffloadMixin, AceStepHandler), {})
        host = Handler()
        host.memory_trace = Trace()
        host.phase_allocator_budget_bytes = 6 * 1024**3
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        checkpoints = Path(fixture.name) / "checkpoints"
        for name in ("vae", "acestep-v15-turbo", "Qwen3-Embedding-0.6B"):
            (checkpoints / name).mkdir(parents=True)
        # Exercise the real initialization/MRO/readiness contract. Only the
        # synthetic checkpoint IO is mocked; no musical paths are opened.
        with patch.dict("os.environ", ACESTEP_CHECKPOINTS_DIR=str(checkpoints), ACESTEP_VAE_CHECKPOINT="official"), \
                patch.object(host, "_ensure_models_present", return_value=None), \
                patch.object(host, "_sync_model_code_if_needed"), \
                patch("transformers.AutoConfig.from_pretrained", return_value=types.SimpleNamespace(is_turbo=True)), \
                patch("transformers.AutoTokenizer.from_pretrained", return_value=object()), \
                patch("torch.load", return_value=torch.zeros(1, 64, 128)):
            status, ok = host.initialize_service(project_root=fixture.name, config_path="acestep-v15-turbo",
                device="cuda", use_flash_attention=False, compile_model=False, quantization="int8_weight_only",
                offload_to_cpu=True, offload_dit_to_cpu=True, use_mlx_dit=False)
        self.assertTrue(ok, status)
        self.assertIsNone(host._validate_generate_music_readiness())
        self.assertTrue(all(isinstance(getattr(host, name), DeferredComponent) for name in host.phase_specs))
        refs = []

        def factory(*args, **kwargs):
            self.assertFalse(torch.is_inference_mode_enabled())
            if Path(args[0]).name == "acestep-v15-turbo":
                self.assertEqual(kwargs, dict(dtype=torch.bfloat16, local_files_only=True,
                                             trust_remote_code=True, attn_implementation="sdpa"))
            elif Path(args[0]).name == "vae":
                self.assertEqual(kwargs, dict(torch_dtype=torch.bfloat16, local_files_only=True, low_cpu_mem_usage=True))
            model = Tiny()
            refs.append(weakref.ref(model))
            return model

        sample = torch.randn(2, 64, dtype=torch.bfloat16)
        expected = {}
        original_preflight = host._phase_vram_preflight

        def record(name):
            original_preflight(name)
            if name == "model":
                expected["output"] = host.model.generate_audio(sample).float()
                expected["int8"] = host.model.decoder[0].weight.tensor_impl.int_data.clone()
                expected["scales"] = host.model.decoder[0].weight.tensor_impl.scale.clone()

        with patch("transformers.AutoModel.from_pretrained", side_effect=factory), \
                patch("musicstudio.phased_pipeline.load_qwen_encoder", side_effect=factory), \
                patch("diffusers.models.AutoencoderOobleck.from_pretrained", side_effect=factory), \
                patch.object(host, "_phase_vram_preflight", side_effect=record), torch.inference_mode():
            for name in ("vae", "vae", "text_encoder", "model", "vae"):
                cpu_rng = torch.get_rng_state().clone()
                cuda_rng = torch.cuda.get_rng_state().clone()
                with host._load_model_context(name):
                    self.assertEqual(sum(ref() is not None for ref in refs), 1)
                    if name == "model":
                        torch.testing.assert_close(host.model.generate_audio(sample.cuda()).float().cpu(), expected["output"], rtol=.1, atol=.05)
                        self.assertTrue(torch.equal(expected["int8"], host.model.decoder[0].weight.tensor_impl.int_data.cpu()))
                        self.assertTrue(torch.equal(expected["scales"], host.model.decoder[0].weight.tensor_impl.scale.cpu()))
                    # A CPU return would call the transfer helper again. There
                    # is exactly one CUDA transfer per loaded component.
                self.assertTrue(all(ref() is None for ref in refs))
                self.assertIsInstance(getattr(host, name), DeferredComponent)
                self.assertTrue(torch.equal(cpu_rng, torch.get_rng_state()))
                self.assertTrue(torch.equal(cuda_rng, torch.cuda.get_rng_state()))
        transfers = [row for row in host.memory_trace.events if row[0] == "transfer.model" and row[1] == "begin"]
        self.assertEqual(len(transfers), 5)
        self.assertTrue(all(row[2]["target"] == "cuda" for row in transfers))
        self.assertEqual(host.phase_report["int8_linear_layers"], 1)
        self.assertEqual(host.phase_report["loads"], host.phase_report["releases"])

    def test_real_diffusers_tiny_oobleck_local_bf16_load_encode_decode_on_cuda(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("Requiere CUDA real para convoluciones BF16.")
        from diffusers.models import AutoencoderOobleck
        from musicstudio.phased_pipeline import PhasedPipelineMixin
        model = AutoencoderOobleck(encoder_hidden_size=8, downsampling_ratios=[2, 2],
                                   channel_multiples=[1, 2], decoder_channels=8,
                                   decoder_input_channels=4, audio_channels=2, sampling_rate=48000)
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"):
            model.save_pretrained(directory, safe_serialization=True)
            del model
            host = PhasedPipelineMixin()
            host.dtype = torch.bfloat16
            host.phase_specs = {"vae": directory}
            host._get_vae_dtype = lambda: torch.bfloat16
            host._materialize_component("vae")  # Actual candidate loader, real Diffusers.
            self.assertTrue(all(p.device.type == "cpu" and p.dtype == torch.bfloat16 for p in host.vae.parameters()))
            host.vae.to("cuda")
            with torch.inference_mode():
                latent = host.vae.encode(torch.zeros(1, 2, 512, dtype=torch.bfloat16, device="cuda")).latent_dist.mean
                audio = host.vae.decode(latent).sample
            self.assertEqual(audio.shape, (1, 2, 512))
            self.assertTrue(torch.isfinite(audio).all().item())


if __name__ == "__main__":
    unittest.main()
