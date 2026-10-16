"""Valida la carga local de Qwen3-Embedding-0.6B en CPU/BF16, sin inferencia."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = ROOT / "checkpoints"
MODEL_RELATIVE = Path("Qwen3-Embedding-0.6B")
MODEL_PATH = CHECKPOINTS / MODEL_RELATIVE
EXPECTED_PYTHON = (3, 12, 12)
EXPECTED_PACKAGES = {"torch": "2.10.0+cu128", "transformers": "4.57.6"}
MIN_PREFLIGHT_BYTES = 4 * 1024**3
RAM_GUARD_BYTES = 1 * 1024**3
RAM_GUARD_SAMPLES = 3
SAMPLE_INTERVAL_SECONDS = 1.0
WORKER_RESULT = "carga-qwen-interna.json"


class LowAvailableMemoryGuard:
    """Stop after three consecutive system-available samples below 1 GiB."""

    def __init__(self, threshold_bytes=RAM_GUARD_BYTES, consecutive_samples=RAM_GUARD_SAMPLES):
        self.threshold_bytes = int(threshold_bytes)
        self.consecutive_samples = int(consecutive_samples)
        self.consecutive_low = 0
        self.low_samples = 0

    def observe(self, available_bytes):
        if int(available_bytes) < self.threshold_bytes:
            self.consecutive_low += 1
            self.low_samples += 1
        else:
            self.consecutive_low = 0
        return self.consecutive_low >= self.consecutive_samples


def preflight_memory_ok(available_bytes):
    return int(available_bytes) >= MIN_PREFLIGHT_BYTES


def validate_versions(python_version, package_versions):
    errors = []
    if tuple(python_version) != EXPECTED_PYTHON:
        errors.append(f"Python requerido 3.12.12; encontrado {'.'.join(map(str, python_version))}.")
    for package, expected in EXPECTED_PACKAGES.items():
        actual = package_versions.get(package)
        if actual != expected:
            errors.append(f"{package} requerido {expected}; encontrado {actual or 'ausente'}.")
    return errors


def summarize_parameters(parameters):
    """Summarize real or synthetic parameter objects without importing torch."""
    dtypes, devices = Counter(), Counter()
    parameter_count = numel_total = storage_bytes = 0
    errors = []
    for parameter in parameters:
        parameter_count += 1
        dtype = str(parameter.dtype)
        device_value = parameter.device
        device = str(getattr(device_value, "type", device_value))
        dtypes[dtype] += 1
        devices[device] += 1
        numel = int(parameter.numel())
        storage_bytes += numel * int(parameter.element_size())
        numel_total += numel
        if dtype != "torch.bfloat16":
            errors.append(f"dtype inesperado {dtype}.")
        if device != "cpu":
            errors.append(f"dispositivo inesperado {device}.")
    if not parameter_count:
        errors.append("El modelo no contiene parámetros.")
    return {
        "parameter_tensors": parameter_count,
        "parameter_elements": numel_total,
        "parameter_storage_bytes": storage_bytes,
        "parameter_storage_gib": storage_bytes / 1024**3,
        "fp32_equivalent_bytes": numel_total * 4,
        "bf16_payload_saving_vs_fp32_bytes": max(0, numel_total * 4 - storage_bytes),
        "dtypes": dict(sorted(dtypes.items())),
        "devices": dict(sorted(devices.items())),
        "valid_bf16_cpu": not errors,
        "validation_errors": errors,
    }


def process_memory_metrics(process):
    """Return RSS and private/USS bytes, naming the private-memory source."""
    info = process.memory_info()
    private = getattr(info, "private", None)
    source = "psutil.memory_info.private"
    if private is None:
        full = process.memory_full_info()
        private = getattr(full, "uss", None)
        source = "psutil.memory_full_info.uss_fallback"
    if private is None:
        raise RuntimeError("psutil no expone memoria privada/USS para este proceso.")
    return {"rss_bytes": int(info.rss), "private_bytes": int(private), "private_bytes_source": source}


def manifest_file_matches(path, spec):
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]):
        return False
    if spec.get("sha256"):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == spec["sha256"]
    if spec.get("git_blob"):
        digest = hashlib.sha1()
        digest.update(f"blob {spec['bytes']}\0".encode("ascii"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == spec["git_blob"]
    return False


def verify_local_checkpoint(root=ROOT):
    checkpoint_root = root / "checkpoints"
    model_dir = checkpoint_root / MODEL_RELATIVE
    lock = json.loads((root / "instalacion" / "modelos.lock.json").read_text(encoding="utf-8"))
    selected = []
    for model in lock.get("models", []):
        if model.get("prefix"):
            continue
        for spec in model.get("files", []):
            if spec.get("path", "").startswith(MODEL_RELATIVE.as_posix() + "/"):
                target = (checkpoint_root / Path(spec["path"])).resolve()
                if not target.is_relative_to(checkpoint_root.resolve()):
                    raise RuntimeError("El manifiesto contiene una ruta fuera de checkpoints.")
                selected.append((spec, target))
    if not selected:
        raise RuntimeError("El manifiesto no contiene archivos fijados de Qwen3-Embedding-0.6B.")
    invalid = [str(path.relative_to(checkpoint_root)) for spec, path in selected if not manifest_file_matches(path, spec)]
    if invalid:
        raise RuntimeError("Checkpoint ausente, incompleto o con hash distinto al manifiesto: " + ", ".join(invalid))
    weight_path = model_dir / "model.safetensors"
    if not weight_path.is_file():
        raise RuntimeError("Falta model.safetensors en el checkpoint local de Qwen.")
    return {
        "status": "verified",
        "relative_path": MODEL_RELATIVE.as_posix(),
        "files_verified": len(selected),
        "files_bytes": sum(int(spec["bytes"]) for spec, _ in selected),
        "weight_file_bytes": weight_path.stat().st_size,
        "manifest": "instalacion/modelos.lock.json",
    }


def _new_run_directory(root=ROOT, now=None):
    now = now or datetime.now(timezone.utc)
    base = root / "resultados"
    base.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    while True:
        run = base / f"validacion-qwen-bf16-{stamp}-{uuid.uuid4().hex[:8]}"
        try:
            run.mkdir()
            return run
        except FileExistsError:
            continue


def _write_json_new(path, payload):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _available_bytes(psutil_module):
    return int(psutil_module.virtual_memory().available)


def _versions_from_metadata():
    versions = {}
    for name in EXPECTED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _worker(run_dir, model_path):
    """Child process: import the pinned stack, then load only Qwen on CPU."""
    report_path = None
    started = time.perf_counter()
    report = {
        "status": "failed",
        "task": "load_qwen3_embedding_0_6b_cpu_bf16_only",
        "dit_loaded": False,
        "vae_loaded": False,
        "music_generation_run": False,
        "target_device": "cpu",
        "requested_dtype": "torch.bfloat16",
        "cuda_transfer_requested": False,
        "model_relative_path": MODEL_RELATIVE.as_posix(),
        "python_version": list(sys.version_info[:3]),
    }
    try:
        if tuple(sys.version_info[:3]) != EXPECTED_PYTHON:
            raise RuntimeError("El worker no está usando Python privado 3.12.12.")
        run_path = Path(run_dir).resolve()
        results_root = (ROOT / "resultados").resolve()
        if not run_path.is_relative_to(results_root) or run_path == results_root:
            raise RuntimeError("El directorio de salida del worker debe ser una carpeta de esta instalación bajo resultados.")
        if Path(model_path).resolve() != MODEL_PATH.resolve():
            raise RuntimeError("El worker solo puede abrir el checkpoint local fijado de Qwen.")
        if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
            raise RuntimeError("La carga debe ejecutarse en modo offline estricto.")
        report_path = run_path / WORKER_RESULT
        import psutil
        process = psutil.Process()
        report["process_before_imports"] = process_memory_metrics(process)
        report["system_available_before_imports_bytes"] = _available_bytes(psutil)
        if not preflight_memory_ok(report["system_available_before_imports_bytes"]):
            raise RuntimeError("RAM disponible inferior a 4 GiB antes de importar PyTorch; se omite la carga.")
        import torch
        import transformers
        from transformers import AutoModel

        versions = {"torch": torch.__version__, "transformers": transformers.__version__}
        report["package_versions"] = versions
        errors = validate_versions(sys.version_info[:3], versions)
        if errors:
            raise RuntimeError("Versiones incompatibles: " + " ".join(errors))
        if not hasattr(torch, "bfloat16"):
            raise RuntimeError("Este PyTorch no ofrece torch.bfloat16.")
        report["system_available_before_load_bytes"] = _available_bytes(psutil)
        report["process_before_load"] = process_memory_metrics(process)
        if not preflight_memory_ok(report["system_available_before_load_bytes"]):
            raise RuntimeError("RAM disponible inferior a 4 GiB tras importar PyTorch/Transformers; se omite la carga de pesos.")
        report["load_started_utc"] = datetime.now(timezone.utc).isoformat()
        print("[WORKER] Cargando únicamente Qwen3-Embedding-0.6B en CPU/BF16; sin tokenizer, DiT, VAE ni generación.", flush=True)
        load_started = time.perf_counter()
        model = AutoModel.from_pretrained(
            str(model_path), dtype=torch.bfloat16, low_cpu_mem_usage=True, local_files_only=True,
        )
        load_elapsed = time.perf_counter() - load_started
        summary = summarize_parameters(model.parameters())
        report["parameter_summary"] = summary
        report["system_available_after_load_bytes"] = _available_bytes(psutil)
        report["process_after_load"] = process_memory_metrics(process)
        report["system_available_delta_during_load_bytes"] = (
            report["system_available_after_load_bytes"] - report["system_available_before_load_bytes"]
        )
        report["process_rss_delta_during_load_bytes"] = (
            report["process_after_load"]["rss_bytes"] - report["process_before_load"]["rss_bytes"]
        )
        report["process_private_delta_during_load_bytes"] = (
            report["process_after_load"]["private_bytes"] - report["process_before_load"]["private_bytes"]
        )
        report["load_elapsed_seconds"] = load_elapsed
        report["load_finished_utc"] = datetime.now(timezone.utc).isoformat()
        gib = lambda value: int(value) / 1024**3
        print(
            f"[WORKER] RAM disponible antes/después de cargar: "
            f"{gib(report['system_available_before_load_bytes']):.3f} / "
            f"{gib(report['system_available_after_load_bytes']):.3f} GiB.", flush=True,
        )
        before_process = report["process_before_load"]
        after_process = report["process_after_load"]
        print(
            f"[WORKER] RSS antes/después: {gib(before_process['rss_bytes']):.3f} / "
            f"{gib(after_process['rss_bytes']):.3f} GiB; privado antes/después: "
            f"{gib(before_process['private_bytes']):.3f} / {gib(after_process['private_bytes']):.3f} GiB "
            f"({after_process['private_bytes_source']}).", flush=True,
        )
        print(f"[WORKER] Duración de from_pretrained: {load_elapsed:.2f} s.", flush=True)
        if not summary["valid_bf16_cpu"]:
            raise RuntimeError("La estructura real del modelo no quedó completamente en BF16/CPU: " + "; ".join(summary["validation_errors"]))
        report["status"] = "passed"
        print(f"[WORKER] Carga completada en {load_elapsed:.2f} s.", flush=True)
        print(f"[WORKER] Parámetros: {summary['parameter_tensors']} tensores; dtypes={summary['dtypes']}; dispositivos={summary['devices']}.", flush=True)
        print(f"[WORKER] Payload BF16={summary['parameter_storage_gib']:.3f} GiB; ahorro teórico vs FP32={summary['bf16_payload_saving_vs_fp32_bytes'] / 1024**3:.3f} GiB.", flush=True)
        _ = model  # Keep weights resident until post-load metrics are complete.
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        print("[WORKER] ERROR: " + report["error"], flush=True)
        print(report["traceback"], flush=True)
    finally:
        report["worker_elapsed_seconds"] = time.perf_counter() - started
        if report_path is not None:
            try:
                _write_json_new(report_path, report)
            except Exception as exc:
                print(f"[WORKER] No se pudo escribir el reporte auxiliar: {type(exc).__name__}: {exc}", flush=True)
    return 0 if report["status"] == "passed" else 1


def _stream_worker_output(stream, events):
    try:
        for line in iter(stream.readline, ""):
            events.put(line.rstrip("\r\n"))
    finally:
        events.put(None)
        stream.close()


def _supervise(run_dir, log_line, psutil_module, root=ROOT, popen=subprocess.Popen):
    report = {
        "schema": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "task": "load_qwen3_embedding_0_6b_cpu_bf16_only",
        "dit_loaded": False,
        "vae_loaded": False,
        "music_generation_run": False,
        "target_device": "cpu",
        "requested_dtype": "torch.bfloat16",
        "cuda_transfer_requested": False,
        "model_relative_path": MODEL_RELATIVE.as_posix(),
        "python_version": list(sys.version_info[:3]),
        "expected_python_version": "3.12.12",
        "expected_package_versions": EXPECTED_PACKAGES,
        "ram_safety": {
            "minimum_preflight_available_bytes": MIN_PREFLIGHT_BYTES,
            "stop_below_available_bytes": RAM_GUARD_BYTES,
            "consecutive_samples_to_stop": RAM_GUARD_SAMPLES,
            "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        },
    }
    report["status"] = "preflight_failed"
    child_result_path = run_dir / WORKER_RESULT
    child = None
    sampled_rss_peak = sampled_private_peak = 0
    minimum_available = None
    samples = 0
    low_guard = LowAvailableMemoryGuard()
    try:
        import psutil as installed_psutil
        if psutil_module is None:
            psutil_module = installed_psutil
        report["system_available_at_start_bytes"] = _available_bytes(psutil_module)
        report["process_at_start"] = process_memory_metrics(psutil_module.Process())
        minimum_available = report["system_available_at_start_bytes"]
        report["minimum_available_sampled_bytes"] = minimum_available
        log_line(f"[SUPERVISOR] RAM disponible inicial: {minimum_available / 1024**3:.3f} GiB.")
        if not preflight_memory_ok(minimum_available):
            report["status"] = "stopped_preflight_low_ram"
            report["stop_reason"] = "RAM disponible menor de 4 GiB antes de iniciar el worker."
            log_line("[SUPERVISOR] " + report["stop_reason"])
            return report
        version_map = _versions_from_metadata()
        report["package_versions"] = version_map
        version_errors = validate_versions(sys.version_info[:3], version_map)
        if version_errors:
            report["status"] = "stopped_preflight_versions"
            report["stop_reason"] = " ".join(version_errors)
            log_line("[SUPERVISOR] " + report["stop_reason"])
            return report
        log_line("[SUPERVISOR] Verificando archivos locales de Qwen con el manifiesto SHA256/Git blob; no hay descarga.")
        report["checkpoint_verification"] = verify_local_checkpoint(root)
        after_hash = _available_bytes(psutil_module)
        report["system_available_after_checkpoint_verification_bytes"] = after_hash
        minimum_available = min(minimum_available, after_hash)
        report["minimum_available_sampled_bytes"] = minimum_available
        if not preflight_memory_ok(after_hash):
            report["status"] = "stopped_preflight_low_ram"
            report["stop_reason"] = "RAM disponible menor de 4 GiB después de verificar el checkpoint."
            log_line("[SUPERVISOR] " + report["stop_reason"])
            return report

        command = [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()), "--worker", str(run_dir), str(MODEL_PATH)]
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        report["worker_command"] = ["<Python privado>", "-I", "-B", "-X", "utf8", "instalacion/validar_qwen_bf16.py", "--worker"]
        report["worker_started_utc"] = datetime.now(timezone.utc).isoformat()
        log_line("[SUPERVISOR] Iniciando worker Python privado offline; RAM y memoria del worker se muestrean cada segundo.")
        worker_started = time.perf_counter()
        child = popen(command, cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        events = queue.Queue()
        reader = threading.Thread(target=_stream_worker_output, args=(child.stdout, events), daemon=True)
        reader.start()
        guard_stop = False
        monitor_error = None
        next_sample_at = time.monotonic() + SAMPLE_INTERVAL_SECONDS
        while child.poll() is None:
            try:
                timeout = max(0.0, min(0.25, next_sample_at - time.monotonic()))
                line = events.get(timeout=timeout)
                if line is not None:
                    log_line(line)
            except queue.Empty:
                pass
            if child.poll() is not None:
                break
            if time.monotonic() < next_sample_at:
                continue
            next_sample_at = time.monotonic() + SAMPLE_INTERVAL_SECONDS
            try:
                available = _available_bytes(psutil_module)
                metrics = process_memory_metrics(psutil_module.Process(child.pid))
                samples += 1
                minimum_available = min(minimum_available, available) if minimum_available is not None else available
                sampled_rss_peak = max(sampled_rss_peak, metrics["rss_bytes"])
                sampled_private_peak = max(sampled_private_peak, metrics["private_bytes"])
                report["minimum_available_sampled_bytes"] = minimum_available
                log_line(f"[MONITOR] RAM disponible={available / 1024**3:.3f} GiB; RSS worker={metrics['rss_bytes'] / 1024**3:.3f} GiB; privado worker={metrics['private_bytes'] / 1024**3:.3f} GiB.")
                if low_guard.observe(available):
                    guard_stop = True
                    report["status"] = "stopped_low_ram_guard"
                    report["stop_reason"] = f"RAM disponible por debajo de 1 GiB durante {RAM_GUARD_SAMPLES} muestras consecutivas; solo se detiene el worker de validación."
                    log_line("[SUPERVISOR] " + report["stop_reason"])
                    child.terminate()
                    break
            except (psutil_module.NoSuchProcess, psutil_module.ZombieProcess):
                break
            except Exception as exc:
                monitor_error = f"{type(exc).__name__}: {exc}"
                report["status"] = "stopped_monitor_error"
                report["stop_reason"] = "No se pudo medir RAM/proceso; se detiene el worker por seguridad. " + monitor_error
                log_line("[SUPERVISOR] " + report["stop_reason"])
                child.terminate()
                break
        try:
            returncode = child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            returncode = child.wait()
        reader.join()
        while True:
            try:
                line = events.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                log_line(line)
        report["worker_elapsed_seconds"] = time.perf_counter() - worker_started
        report["worker_return_code"] = returncode
        report["monitor_samples"] = samples
        report["monitor_low_ram_samples"] = low_guard.low_samples
        report["sampled_worker_peak_rss_bytes"] = sampled_rss_peak
        report["sampled_worker_peak_private_bytes"] = sampled_private_peak
        report["monitor_error"] = monitor_error
        report["worker_finished_utc"] = datetime.now(timezone.utc).isoformat()
        if child_result_path.exists():
            try:
                report["load_result"] = json.loads(child_result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                report["worker_report_error"] = f"{type(exc).__name__}: {exc}"
        load_result = report.get("load_result", {})
        after_load_metrics = load_result.get("process_after_load", {})
        report["worker_peak_rss_observed_bytes"] = max(sampled_rss_peak, int(after_load_metrics.get("rss_bytes", 0)))
        report["worker_peak_private_observed_bytes"] = max(sampled_private_peak, int(after_load_metrics.get("private_bytes", 0)))
        stage_available = [
            load_result.get("system_available_before_imports_bytes"),
            load_result.get("system_available_before_load_bytes"),
            load_result.get("system_available_after_load_bytes"),
        ]
        stage_available = [int(value) for value in stage_available if value is not None]
        report["minimum_available_at_worker_stages_bytes"] = min(stage_available) if stage_available else None
        if guard_stop or monitor_error:
            return report
        child_result = report.get("load_result", {})
        if returncode == 0 and child_result.get("status") == "passed":
            report["status"] = "passed"
        else:
            report["status"] = "failed"
            if not child_result:
                report["stop_reason"] = "El worker terminó sin crear su informe de carga; revisar consola.log."
            elif child_result.get("error"):
                report["stop_reason"] = child_result["error"]
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["stop_reason"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        log_line("[SUPERVISOR] ERROR: " + report["stop_reason"])
        log_line(report["traceback"])
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait()
        return report
    finally:
        if child_result_path.exists():
            try:
                child_result_path.unlink()
            except OSError:
                report["temporary_worker_report_preserved"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, metavar=("RUN_DIR", "MODEL_PATH"))
    args = parser.parse_args(argv)
    if args.worker:
        return _worker(args.worker[0], args.worker[1])
    run_dir = _new_run_directory()
    log_path = run_dir / "consola.log"
    with log_path.open("x", encoding="utf-8", newline="\n", buffering=1) as log_stream:
        def log_line(message):
            print(message, flush=True)
            log_stream.write(message + "\n")
            log_stream.flush()
        report = {
            "schema": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "task": "load_qwen3_embedding_0_6b_cpu_bf16_only",
            "dit_loaded": False,
            "vae_loaded": False,
            "music_generation_run": False,
            "target_device": "cpu",
            "requested_dtype": "torch.bfloat16",
            "cuda_transfer_requested": False,
            "model_relative_path": MODEL_RELATIVE.as_posix(),
            "console_log": "consola.log",
            "result_directory": str(run_dir.relative_to(ROOT)),
        }
        try:
            report.update(_supervise(run_dir, log_line, None))
        except BaseException as exc:
            report["status"] = "failed"
            report["stop_reason"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()
            log_line("[SUPERVISOR] ERROR FATAL: " + report["stop_reason"])
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["console_log"] = "consola.log"
        report["result_directory"] = str(run_dir.relative_to(ROOT))
        _write_json_new(run_dir / "informe.json", report)
        log_line(f"[SUPERVISOR] Estado final: {report['status']}. Informe: {run_dir.relative_to(ROOT) / 'informe.json'}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
