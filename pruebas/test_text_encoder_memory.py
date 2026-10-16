"""Synthetic tests for the CPU-offloaded text-encoder dtype path; no checkpoints."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from musicstudio.text_encoder_memory import TextEncoderCPUOffloadMixin


class FakeParameter:
    def __init__(self, dtype, device_type="cpu", floating=True):
        self.dtype = dtype
        self.device = types.SimpleNamespace(type=device_type)
        self._floating = floating

    def is_floating_point(self):
        return self._floating


class FakeEncoder:
    def __init__(self, parameters):
        self.parameters = parameters
        self.evaluating = False

    def named_parameters(self):
        return ((f"weight_{index}", parameter) for index, parameter in enumerate(self.parameters))

    def eval(self):
        self.evaluating = True
        return self

    def to(self, *_args, **_kwargs):
        raise AssertionError("The loader must not allocate a second converted copy.")


class UpstreamLoader:
    def _load_text_encoder_and_tokenizer(self, *, checkpoint_dir, device):
        self.upstream_call = (checkpoint_dir, device)
        return "upstream"


class TextEncoderCPUOffloadTests(unittest.TestCase):
    dtype = object()  # Sentinel: verifies the exact configured CUDA dtype is passed through.

    def handler(self, *, offload=True):
        cls = type("Handler", (TextEncoderCPUOffloadMixin, UpstreamLoader), {})
        value = cls()
        value.offload_to_cpu = offload
        value.dtype = self.dtype
        return value

    @staticmethod
    def transformers_module(model):
        module = types.ModuleType("transformers")
        module.AutoModel = types.SimpleNamespace(from_pretrained=Mock(return_value=model))
        module.AutoTokenizer = types.SimpleNamespace(from_pretrained=Mock(return_value="tokenizer"))
        return module

    def test_loads_directly_to_cuda_compute_dtype_while_staying_on_cpu(self):
        model = FakeEncoder([FakeParameter(self.dtype), FakeParameter(self.dtype)])
        transformers = self.transformers_module(model)
        handler = self.handler()
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "Qwen3-Embedding-0.6B"
            checkpoint.mkdir()
            with patch.dict(sys.modules, {"transformers": transformers}):
                result = handler._load_text_encoder_and_tokenizer(
                    checkpoint_dir=directory, device="cuda"
                )

        self.assertEqual(result, str(checkpoint))
        transformers.AutoModel.from_pretrained.assert_called_once_with(
            str(checkpoint), dtype=self.dtype
        )
        transformers.AutoTokenizer.from_pretrained.assert_called_once_with(str(checkpoint))
        self.assertIs(handler.text_encoder, model)
        self.assertEqual(handler.text_tokenizer, "tokenizer")
        self.assertTrue(model.evaluating)

    def test_rejects_a_parameter_that_was_not_materialized_in_compute_dtype(self):
        model = FakeEncoder([FakeParameter(object())])
        transformers = self.transformers_module(model)
        handler = self.handler()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Qwen3-Embedding-0.6B").mkdir()
            with patch.dict(sys.modules, {"transformers": transformers}):
                with self.assertRaisesRegex(RuntimeError, "expected inference dtype"):
                    handler._load_text_encoder_and_tokenizer(
                        checkpoint_dir=directory, device="cuda"
                    )

    def test_rejects_weights_loaded_off_cpu(self):
        model = FakeEncoder([FakeParameter(self.dtype, device_type="cuda")])
        transformers = self.transformers_module(model)
        handler = self.handler()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Qwen3-Embedding-0.6B").mkdir()
            with patch.dict(sys.modules, {"transformers": transformers}):
                with self.assertRaisesRegex(RuntimeError, "expected CPU offload"):
                    handler._load_text_encoder_and_tokenizer(
                        checkpoint_dir=directory, device="cuda"
                    )

    def test_non_offload_mode_keeps_upstream_loader(self):
        handler = self.handler(offload=False)
        self.assertEqual(
            handler._load_text_encoder_and_tokenizer(checkpoint_dir="root", device="cuda"),
            "upstream",
        )
        self.assertEqual(handler.upstream_call, ("root", "cuda"))

    def test_non_cuda_offload_keeps_upstream_loader(self):
        handler = self.handler()
        self.assertEqual(
            handler._load_text_encoder_and_tokenizer(checkpoint_dir="root", device="cpu"),
            "upstream",
        )
        self.assertEqual(handler.upstream_call, ("root", "cpu"))

    def test_missing_checkpoint_fails_before_loading(self):
        model = FakeEncoder([FakeParameter(self.dtype)])
        transformers = self.transformers_module(model)
        handler = self.handler()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"transformers": transformers}):
                with self.assertRaises(FileNotFoundError):
                    handler._load_text_encoder_and_tokenizer(
                        checkpoint_dir=directory, device="cuda"
                    )
        transformers.AutoModel.from_pretrained.assert_not_called()

    def test_bfloat16_checkpoint_values_roundtrip_through_fp32_exactly(self):
        try:
            import torch
        except ImportError:
            self.skipTest("PyTorch no está instalado en este intérprete.")
        source = torch.tensor([0.0, 1.0, -1.0, 0.333984375], dtype=torch.bfloat16)
        promoted = source.to(torch.float32)
        restored = promoted.to(torch.bfloat16)
        self.assertTrue(torch.equal(source, restored))


if __name__ == "__main__":
    unittest.main()
