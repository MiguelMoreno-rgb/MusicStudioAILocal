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


def supervise(request_path):
    import psutil
    request = read_json(request_path)
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
    consecutive_low = 0
    with (run / "consola.log").open("w", encoding="utf-8") as log, (run / "memoria.csv").open("w", newline="", encoding="utf-8") as memory:
        writer = csv.DictWriter(memory, fieldnames=["elapsed_s", "process_rss_bytes", "system_available_bytes", "system_swap_used_bytes", "gpu0_used_mib", "gpu0_free_mib", "gpu_sampling_error"])
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
        try:
            while child.poll() is None:
                vm = psutil.virtual_memory()
                minimum_ram = min(minimum_ram, vm.available / 1024**3)
                try:
                    rss = proc.memory_info().rss
                    peak_rss = max(peak_rss, rss)
                except psutil.NoSuchProcess:
                    break
                row = dict(elapsed_s=round(time.perf_counter() - started, 3), process_rss_bytes=rss,
                           system_available_bytes=vm.available, system_swap_used_bytes=psutil.swap_memory().used,
                           gpu0_used_mib="", gpu0_free_mib="", gpu_sampling_error="")
                try:
                    sample = next(g for g in nvidia_inventory() if g["index"] == 0)
                    row.update(gpu0_used_mib=sample["used_mib"], gpu0_free_mib=sample["free_mib"])
                except Exception as exc:
                    row["gpu_sampling_error"] = str(exc)
                writer.writerow(row)
                memory.flush()
                samples += 1
                consecutive_low = consecutive_low + 1 if vm.available < 1024**3 else 0
                if consecutive_low >= 3:
                    killed_reason = "RAM disponible inferior a 1 GiB en tres muestras; detenido solo el proceso de generacion iniciado."
                    child.kill()
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            killed_reason = "Cancelado por el usuario."
            child.kill()
        finally:
            code = child.wait()
            reader.join(timeout=15)
    if killed_reason:
        (run / "interrupcion.txt").write_text(killed_reason, encoding="utf-8")
    result_file = run / "resultado.json"
    success = code == 0 and result_file.is_file() and read_json(result_file).get("success") is True and not killed_reason
    write_json(run / "monitor.json", dict(success=bool(success), exit_code=code, stopped_reason=killed_reason,
               elapsed_s=time.perf_counter() - started, samples=samples, peak_process_rss_bytes=peak_rss,
               minimum_system_available_gib=minimum_ram, note="VRAM global muestreada; picos breves pueden perderse. RSS no equivale a RAM comprometida y swap es global."))
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
