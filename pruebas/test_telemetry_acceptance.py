"""The acceptance gate cannot credit Linux or skipped native Windows tests."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

s=importlib.util.spec_from_file_location('io_acceptance',Path(__file__).resolve().parents[1]/'instalacion/comprobar_telemetria.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class AcceptanceTests(unittest.TestCase):
    def test_only_real_windows_zero_skips_success_can_pass(self):
        def result(count=16,skipped=(),success=True,executed=None):
            return SimpleNamespace(testsRun=count,skipped=skipped,executed_tests=m.NATIVE_TESTS if executed is None else executed,wasSuccessful=lambda:success)
        self.assertFalse(m.acceptance_passed(result(),'Linux'))
        self.assertFalse(m.acceptance_passed(result(skipped=['native test']),'Windows'))
        self.assertFalse(m.acceptance_passed(result(count=0),'Windows'))
        self.assertFalse(m.acceptance_passed(result(count=15),'Windows'))
        self.assertFalse(m.acceptance_passed(result(count=17),'Windows'))
        self.assertFalse(m.acceptance_passed(result(executed=[]),'Windows'))
        self.assertFalse(m.acceptance_passed(result(success=False),'Windows'))
        self.assertTrue(m.acceptance_passed(result(),'Windows'))
