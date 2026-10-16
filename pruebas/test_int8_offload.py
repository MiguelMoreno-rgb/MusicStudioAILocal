"""Real torch/AQT regression; no musical checkpoints and no mocked dispatch.

Run inside the existing .venv after applying the correction. The CPU test
reproduces the exact reported operator; CUDA round trips run only with a GPU.
Cloud-only source override is checked against the pinned source hashes.
"""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from musicstudio import common

HAS_TORCH = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("torchao") is not None


@unittest.skipUnless(HAS_TORCH, "Requiere torch y torchao del entorno exclusivo; no instala nada.")
class Int8OffloadRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        import torchao
        if torch.__version__.split("+")[0] != "2.10.0" or torchao.__version__ != "0.16.0":
            raise RuntimeError("Esta regresion debe probar torch 2.10.0 / torchao 0.16.0.")
        source = Path(os.environ.get("MUSICSTUDIO_TEST_SOURCE", common.source_root()))
        if not source.is_dir():
            raise unittest.SkipTest("Falta el codigo fuente fijado de ACE-Step, no se descarga en esta prueba.")
        cls.source = source

    def setUp(self):
        self.override = patch.object(common, "source_root", return_value=self.source)
        self.override.start()
        self.addCleanup(self.override.stop)
        import torch
        torch.manual_seed(40601)

    @staticmethod
    def tiny_model(bias=True):
        import torch
        from torchao.quantization import Int8WeightOnlyConfig, quantize_
        model = torch.nn.Sequential(torch.nn.Linear(64, 64, bias=bias)).eval().to(dtype=torch.bfloat16)
        quantize_(model, Int8WeightOnlyConfig(version=1))
        return model

    def test_raw_module_to_in_inference_mode_reproduces_exact_operator(self):
        import torch
        model = self.tiny_model()
        # Same CPU target: proves this operator failure is not CUDA-specific.
        with torch.inference_mode(), self.assertRaisesRegex(NotImplementedError, r"AffineQuantizedTensor.*aten\._has_compatible_shallow_copy_type"):
            model.to("cpu")

    def test_upstream_helper_catches_exact_error_and_keeps_int8(self):
        from musicstudio.int8_offload import exercise_int8_offload
        report = exercise_int8_offload("cpu", cycles=2)
        self.assertEqual(report["effective_int8_layers"], 1)
        self.assertTrue(report["integer_storage_and_scales_preserved"])

    def test_real_transfer_to_meta_checks_internal_storage(self):
        import torch
        from musicstudio.int8_offload import make_offload_host, move_checked
        # Meta has no backing storage; dense Parameter.data CPU -> meta is not a
        # supported stand-in for CUDA. Restrict this test to AQT internal transfer.
        model = self.tiny_model(bias=False)
        with torch.inference_mode():
            move_checked(make_offload_host(), model, "meta", torch.bfloat16)
        self.assertEqual(model[0].weight.tensor_impl.int_data.device.type, "meta")
        self.assertEqual(model[0].weight.tensor_impl.scale.device.type, "meta")

    def test_silent_wrong_device_is_rejected(self):
        from musicstudio.int8_offload import assert_int8_device
        with self.assertRaisesRegex(RuntimeError, "Offload incompleto"):
            assert_int8_device(self.tiny_model(), "meta")

    def test_cuda_round_trips_are_real(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("No hay CUDA; CPU/meta no acreditan un round trip CUDA.")
        from musicstudio.int8_offload import exercise_int8_offload
        report = exercise_int8_offload("cuda", cycles=2)
        self.assertEqual(report["cycles"], 2)
        self.assertTrue(report["integer_storage_and_scales_preserved"])


if __name__ == "__main__":
    unittest.main()
