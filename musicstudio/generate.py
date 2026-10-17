"""Parent monitor. Only its own child is stopped under severe memory pressure."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time

from .common import ROOT, STATE, isolate, new_run, nvidia_inventory, read_json, sha256, write_json
from .memory_trace import LowMemoryGuard, last_event, sample_memory
from .phased_pipeline import validate_phase_request
from .vocal_conditioning import validate_vocal_request


def supervise(request_path):
    import psutil
    request = read_json(request_path)
    if validate_phase_request(request):
        validate_vocal_request(request)
    if not isinstance(request.get("seed"), int) or not 0 <= request["seed"] <= 2**32 - 1:
        raise ValueError("La semilla debe ser un entero de 0 a 4294967295.")
    if not isinstance(request.get("use_lm"), bool):
        raise ValueError("Perfil LM invalido.")
    for field, limit in (("prompt", 512), ("lyrics", 4096)):
        value = request.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) >= limit:
            raise ValueError(f"{field}: debe contener texto y menos de {limit} caracteres.")
    validation = read_json(STATE / "validation.json")
    if validation.get("status") != "LOCAL_ENVIRONMENT_CHECKS_PASSED_NOT_MODEL_GENERATION" or validation.get("requirements_sha256") != sha256(ROOT / "instalacion/requirements-windows.lock.txt"):
        raise RuntimeError("Falta validacion local vigente; ejecutar 07_VALIDAR_ENTORNO.bat.")
    if validation.get("streaming_int8_offload", {}).get("transfer_method") != "move_streaming":
        raise RuntimeError("La nueva transferencia INT8 requiere 07_VALIDAR_ENTORNO.bat antes de autorizar una generacion; no se revalida automaticamente.")
    ram = psutil.virtual_memory().available / 1024**3
    gpus = nvidia_inventory()
    gpu = next((g for g in gpus if g["index"] == 0 and "4060" in g["name"]), None)
    if not gpu:
        raise RuntimeError("No se encontro la RTX 4060 como GPU 0.")
    if ram < 4 or gpu["free_mib"] < 4608:
        raise RuntimeError("Memoria libre insuficiente para iniciar: se requieren 4 GiB RAM y 4.5 GiB VRAM como guardas conservadoras, no garantias.")
    if request["use_lm"] and (ram < 8 or gpu["free_mib"] < 6144):
        if not request.get("experimental_lm_confirmed", False):
            raise RuntimeError("Poco margen para LM. Usar el perfil sin LM o confirmar explicitamente la prueba experimental en el BAT.")
    run = new_run("con-lm" if request["use_lm"] else "sin-lm")
    write_json(run / "solicitud.json", request)
    write_json(run / "hardware-inicial.json", dict(ram_available_gib=ram, gpu=gpus))
    print("Resultados y registros:", run, flush=True)
    started = time.perf_counter()
    killed_reason = None
    samples = 0
    minimum_ram = ram
    peak_rss = 0
    memory_guard = LowMemoryGuard()
    last_sample = None
    interruption_event = None
    peak_private = None
    peak_commit = None
    with (run / "consola.log").open("w", encoding="utf-8") as log, (run / "memoria.csv").open("w", newline="", encoding="utf-8") as memory:
        writer = csv.DictWriter(memory, fieldnames=["elapsed_s", "process_rss_bytes", "process_private_bytes", "process_vms_bytes", "process_page_faults", "system_available_bytes", "system_total_bytes", "system_commit_bytes", "system_commit_limit_bytes", "system_commit_peak_bytes", "commit_sampling_error", "system_swap_used_bytes", "worker_event_sequence", "worker_operation", "worker_event", "worker_telemetry_read_error", "gpu0_used_mib", "gpu0_free_mib", "gpu_sampling_error", "gpu_sample_age_s"])
        writer.writeheader()
        child = subprocess.Popen([sys.executable, "-I", "-B", str(ROOT / "instalacion/ejecutar.py"), "worker", str(run / "solicitud.json"), str(run)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
        # Drain logs concurrently so long native output cannot deadlock a full pipe.
        import threading
        def drain():
            for line in child.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        proc = psutil.Process(child.pid)
        # A slow nvidia-smi must not delay the physical-RAM safety loop.
        gpu_stop = threading.Event()
        gpu_lock = threading.Lock()
        gpu_latest = {}
        def poll_gpu():
            while not gpu_stop.is_set():
                try:
                    snapshot = next(g for g in nvidia_inventory() if g["index"] == 0)
                    update = dict(gpu0_used_mib=snapshot["used_mib"], gpu0_free_mib=snapshot["free_mib"], gpu_sampling_error="")
                except Exception as exc:
                    update = dict(gpu0_used_mib="", gpu0_free_mib="", gpu_sampling_error=str(exc))
                with gpu_lock:
                    gpu_latest.update(update, sampled_at=time.perf_counter())
                gpu_stop.wait(1)
        gpu_reader = threading.Thread(target=poll_gpu, daemon=True)
        gpu_reader.start()
        try:
            while child.poll() is None:
                try:
                    measurement = sample_memory(proc)
                    rss = measurement["process_rss_bytes"]
                    peak_rss = max(peak_rss, rss)
                except psutil.NoSuchProcess:
                    break
                minimum_ram = min(minimum_ram, measurement["system_available_bytes"] / 1024**3)
                event_io = {}
                event = last_event(run, diagnostics=event_io)
                for field, current in (("process_private_bytes", peak_private), ("system_commit_bytes", peak_commit)):
                    value = measurement[field]
                    if value is not None:
                        if field == "process_private_bytes": peak_private = max(current or 0, value)
                        else: peak_commit = max(current or 0, value)
                row = dict(elapsed_s=round(time.perf_counter() - started, 3), **measurement,
                           system_swap_used_bytes=psutil.swap_memory().used,
                           worker_event_sequence=event.get("sequence") if event else None,
                           worker_operation=event.get("operation") if event else None,
                           worker_event=event.get("event") if event else None,
                           worker_telemetry_read_error=json.dumps(event_io) if event_io else "",
                           gpu0_used_mib="", gpu0_free_mib="", gpu_sampling_error="Muestra GPU pendiente")
                with gpu_lock:
                    cached = dict(gpu_latest)
                sampled_at = cached.pop("sampled_at", None)
                row.update(cached, gpu_sample_age_s=time.perf_counter()-sampled_at if sampled_at else None)
                writer.writerow(row)
                memory.flush()
                samples += 1
                last_sample = row
                if memory_guard.observe(measurement["system_available_bytes"]):
                    killed_reason = "RAM disponible inferior a 1 GiB en tres muestras; detenido solo el proceso de generacion iniciado."
                    interruption_event = event
                    try:
                        write_json(run / "memoria-interrupcion.json", dict(reason=killed_reason, sample=row, last_worker_event=event))
                    finally:
                        child.kill()  # Low-RAM stop must not depend on successful logging.
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            killed_reason = "Cancelado por el usuario."
            child.kill()
        except Exception as exc:
            killed_reason = (killed_reason + " Fallo adicional del registro: " if killed_reason else "Fallo del monitor; detenido el hijo por seguridad: ") + repr(exc)
            child.kill()
        finally:
            gpu_stop.set()
            code = child.wait()
            reader.join(timeout=15)
    if killed_reason:
        (run / "interrupcion.txt").write_text(killed_reason, encoding="utf-8")
    result_file = run / "resultado.json"
    success = code == 0 and result_file.is_file() and read_json(result_file).get("success") is True and not killed_reason
    write_json(run / "monitor.json", dict(success=bool(success), exit_code=code, stopped_reason=killed_reason,
               elapsed_s=time.perf_counter() - started, samples=samples, peak_process_rss_bytes=peak_rss,
               minimum_system_available_gib=minimum_ram, peak_process_private_bytes=peak_private,
               peak_system_commit_bytes=peak_commit, last_sample=last_sample, last_worker_event=interruption_event or last_event(run),
               guard=dict(available_physical_ram_bytes=1024**3, consecutive_samples=3),
               note="RSS=working set; private=commit privado Windows; commit del sistema via GetPerformanceInfo. VMS y swap no equivalen a RAM fisica. VRAM nvidia-smi global, no solo del hijo; muestras pueden perder picos."))
    if not success:
        print("Prueba incompleta. Revisar error.txt/consola.log/monitor.json. La alternativa sin LM usa un proceso nuevo; no se reintenta automaticamente.")
        return 1
    print("Primera toma guardada en resultados. Escuchar antes de evaluar voz, idioma o calidad.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    isolate()
    raise SystemExit(supervise(args.request))
