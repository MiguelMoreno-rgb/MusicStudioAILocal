"""Windows acceptance of the candidate with tiny weights, before any music."""
import os
import sys
import traceback
import unittest

from .common import isolate, new_run, write_json


def main():
    isolate()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    run = new_run("validacion-fases-sintetica")
    report = {"status": "failed", "musical_checkpoints_loaded": False, "music_generated": False}
    print("Validacion sintetica de la candidata. Registros:", run, flush=True)
    with (run / "consola.log").open("x", encoding="utf-8") as log:
        try:
            # Existing native CUDA/INT8/codec/source-integrity checks. This
            # function does not install packages or write validation.json.
            from .validate import run_checks
            report["environment_checks"] = run_checks(run)
            suite = unittest.defaultTestLoader.loadTestsFromNames([
                "pruebas.test_phased_pipeline", "pruebas.test_phased_runtime",
                "pruebas.test_vocal_conditioning", "pruebas.test_vocal_native",
            ])
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            report.update(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                          skipped=[reason for _, reason in result.skipped])
            if not result.wasSuccessful() or result.skipped or not result.testsRun:
                raise RuntimeError("Aceptacion Windows incompleta: se exigen cero fallos y cero omisiones. Revisar consola.log.")
            report["status"] = "NATIVE_SYNTHETIC_PHASE_CHECKS_PASSED_NOT_MUSICAL_GENERATION"
        except Exception:
            report["error"] = traceback.format_exc()
            log.write(report["error"])
            print(report["error"], flush=True)
        finally:
            write_json(run / "informe.json", report)
    if report["status"] == "failed":
        return 1
    print("Pruebas nativas sinteticas completas. Todavia no se ha generado musica.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
