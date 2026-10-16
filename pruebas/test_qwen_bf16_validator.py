import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "instalacion" / "validar_qwen_bf16.py"
SPEC = importlib.util.spec_from_file_location("qwen_bf16_validator", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class GuardTests(unittest.TestCase):
    def test_preflight_requires_four_gib_and_accepts_exact_boundary(self):
        required = validator.MIN_PREFLIGHT_BYTES
        self.assertFalse(validator.preflight_memory_ok(required - 1))
        self.assertTrue(validator.preflight_memory_ok(required))

    def test_stops_on_third_consecutive_sample_strictly_below_one_gib(self):
        guard = validator.LowAvailableMemoryGuard()
        one_gib = 1024**3
        self.assertFalse(guard.observe(one_gib))
        self.assertFalse(guard.observe(one_gib - 1))
        self.assertFalse(guard.observe(one_gib - 1))
        self.assertTrue(guard.observe(one_gib - 1))
        self.assertEqual(guard.low_samples, 3)

    def test_normal_sample_resets_consecutive_low_count(self):
        guard = validator.LowAvailableMemoryGuard()
        one_gib = 1024**3
        self.assertFalse(guard.observe(one_gib - 1))
        self.assertFalse(guard.observe(one_gib - 1))
        self.assertFalse(guard.observe(one_gib))
        self.assertEqual(guard.consecutive_low, 0)
        self.assertFalse(guard.observe(one_gib - 1))


class VersionTests(unittest.TestCase):
    def test_accepts_expected_private_stack(self):
        self.assertEqual(validator.validate_versions((3, 12, 12), {"torch": "2.10.0+cu128", "transformers": "4.57.6"}), [])

    def test_rejects_wrong_python_or_transformers(self):
        errors = validator.validate_versions((3, 12, 11), {"torch": "2.10.0+cu128", "transformers": "4.57.5"})
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("Python requerido 3.12.12" in error for error in errors))
        self.assertTrue(any("transformers requerido 4.57.6" in error for error in errors))


class ParameterSummaryTests(unittest.TestCase):
    class Device:
        type = "cpu"

    class Parameter:
        def __init__(self, dtype="torch.bfloat16", device=None, numel=4, element_size=2):
            self.dtype = dtype
            self.device = device or ParameterSummaryTests.Device()
            self._numel = numel
            self._element_size = element_size

        def numel(self):
            return self._numel

        def element_size(self):
            return self._element_size

    def test_reports_bf16_cpu_payload_and_theoretical_fp32_difference(self):
        summary = validator.summarize_parameters([self.Parameter(numel=10), self.Parameter(numel=5)])
        self.assertTrue(summary["valid_bf16_cpu"])
        self.assertEqual(summary["parameter_tensors"], 2)
        self.assertEqual(summary["parameter_elements"], 15)
        self.assertEqual(summary["parameter_storage_bytes"], 30)
        self.assertEqual(summary["fp32_equivalent_bytes"], 60)
        self.assertEqual(summary["bf16_payload_saving_vs_fp32_bytes"], 30)

    def test_rejects_fp32_and_non_cpu_parameters(self):
        cuda = type("Device", (), {"type": "cuda"})()
        summary = validator.summarize_parameters([self.Parameter(dtype="torch.float32"), self.Parameter(device=cuda)])
        self.assertFalse(summary["valid_bf16_cpu"])
        self.assertTrue(any("dtype inesperado torch.float32" in error for error in summary["validation_errors"]))
        self.assertTrue(any("dispositivo inesperado cuda" in error for error in summary["validation_errors"]))

    def test_rejects_empty_model(self):
        summary = validator.summarize_parameters([])
        self.assertFalse(summary["valid_bf16_cpu"])
        self.assertIn("El modelo no contiene parámetros.", summary["validation_errors"])


class ProcessMemoryTests(unittest.TestCase):
    def test_uses_windows_private_bytes_when_available(self):
        info = type("Info", (), {"rss": 100, "private": 60})()
        process = type("Process", (), {"memory_info": lambda self: info})()
        self.assertEqual(validator.process_memory_metrics(process), {
            "rss_bytes": 100, "private_bytes": 60, "private_bytes_source": "psutil.memory_info.private",
        })

    def test_falls_back_to_uss_and_labels_it(self):
        info = type("Info", (), {"rss": 100})()
        full = type("Full", (), {"uss": 50})()
        process = type("Process", (), {"memory_info": lambda self: info, "memory_full_info": lambda self: full})()
        self.assertEqual(validator.process_memory_metrics(process), {
            "rss_bytes": 100, "private_bytes": 50, "private_bytes_source": "psutil.memory_full_info.uss_fallback",
        })


class ManifestTests(unittest.TestCase):
    def test_verifies_synthetic_sha256_and_git_blob_entries(self):
        payload = b"synthetic local model file"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            blob = hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()
            self.assertTrue(validator.manifest_file_matches(path, {"bytes": len(payload), "sha256": sha}))
            self.assertTrue(validator.manifest_file_matches(path, {"bytes": len(payload), "git_blob": blob}))
            self.assertFalse(validator.manifest_file_matches(path, {"bytes": len(payload), "sha256": "0" * 64}))
            self.assertFalse(validator.manifest_file_matches(path, {"bytes": len(payload) + 1, "sha256": sha}))


if __name__ == "__main__":
    unittest.main()
