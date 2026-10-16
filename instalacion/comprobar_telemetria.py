"""Windows-only acceptance runner. stdlib, tiny temp files, no music/models."""
from datetime import datetime, timezone
from contextlib import redirect_stderr
import tempfile
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TESTS = 16
NATIVE_TESTS = {
    'pruebas.test_memory_io.MemoryIOTests.'+name for name in (
        'test_native_shared_reader_observes_appends_while_open',
        'test_native_crt_reader_observes_appends_while_open',
        'test_native_reader_denying_write_reports_fault_and_primary_still_advances',
    )
}


class RecordingResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.executed_tests=[]

    def startTest(self,test):
        self.executed_tests.append(test.id())
        super().startTest(test)


class Tee:
    def __init__(self,*streams):self.streams=streams
    def write(self,text):
        for stream in self.streams:stream.write(text)
        return len(text)
    def flush(self):
        for stream in self.streams:stream.flush()


def acceptance_passed(result, system):
    # A Linux pass with skipped native tests is NEVER Windows acceptance.
    return (system == 'Windows' and result.testsRun == EXPECTED_TESTS and not result.skipped
            and NATIVE_TESTS.issubset(set(getattr(result,'executed_tests',())))
            and result.wasSuccessful())


def main():
    if sys.platform != 'win32':
        print('Se requiere Windows real. Linux no acredita la validacion nativa.')
        return 2
    expected = ROOT / '.runtime/python/cpython-3.12.12-windows-x86_64-none/python.exe'
    if Path(sys.executable).resolve() != expected.resolve() or sys.version_info[:3] != (3,12,12):
        print('Usa el Python privado base 3.12.12 mediante 09_VALIDAR_TELEMETRIA_WINDOWS.bat.')
        return 2
    sys.path.insert(0,str(ROOT))
    started=time.perf_counter()
    run=ROOT/'resultados'/('validacion-io-windows-'+datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])
    run.mkdir(parents=True,exist_ok=False)
    temporary=run/'temporales'
    temporary.mkdir()
    previous_temp=tempfile.tempdir
    try:
        # Exercise this project's actual OneDrive/NTFS location, not a different
        # drive chosen by Windows TEMP. This setting affects this process only.
        tempfile.tempdir=str(temporary)
        with (run/'pruebas-io-windows.log').open('x',encoding='utf-8') as log:
            output=Tee(sys.stderr,log)
            with redirect_stderr(output):
                suite=unittest.defaultTestLoader.loadTestsFromName('pruebas.test_memory_io')
                result=unittest.TextTestRunner(stream=output,verbosity=2,resultclass=RecordingResult).run(suite)
    finally:
        tempfile.tempdir=previous_temp
    passed=acceptance_passed(result,platform.system())
    names=('musicstudio/memory_trace.py','pruebas/test_memory_io.py','instalacion/comprobar_telemetria.py')
    report=dict(status='WINDOWS_TELEMETRY_TESTS_PASSED' if passed else 'WINDOWS_TELEMETRY_TESTS_FAILED_OR_SKIPPED',
                utc=datetime.now(timezone.utc).isoformat(),windows_version=platform.version(),
                system=platform.system(),python=platform.python_version(),executable=sys.executable,
                elapsed_s=time.perf_counter()-started,tests_run=result.testsRun,
                temporary_directory=str(temporary),executed_tests=result.executed_tests,
                required_native_tests=sorted(NATIVE_TESTS),
                failures=[dict(test=str(t),traceback=tb) for t,tb in result.failures],
                errors=[dict(test=str(t),traceback=tb) for t,tb in result.errors],
                skipped=[dict(test=str(t),reason=r) for t,r in result.skipped],
                hashes={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names},
                scope='Solo E/S de telemetria; no modelos, generacion, validacion CUDA ni cambios del entorno.')
    with (run/'validacion-io-windows.json').open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2,ensure_ascii=True);f.write('\n')
    print('Informe:',run/'validacion-io-windows.json',flush=True)
    print(report['status'],flush=True)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
