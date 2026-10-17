"""Offline inference in a disposable child process; no web/API server."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import os
from pathlib import Path
import random
import subprocess
import time
import traceback

from .common import CHECKPOINTS, ROOT, enable_source, ffmpeg_bin, isolate, read_json, sha256, write_json
from .models import validate_configs, verify_models
from .vocal_conditioning import VocalConditioningMixin, make_generation_params, validate_vocal_request
from .memory_trace import MemoryTrace
from .streaming_offload import StreamingOffloadMixin
from .text_encoder_memory import TextEncoderCPUOffloadMixin
from .phased_pipeline import PhasedPipelineMixin, instrument_model_operations, validate_phase_request


def perform(request, run, trace):
    phased = validate_phase_request(request)
    if phased:
        validate_vocal_request(request)
    trace.emit("worker.preparation", "begin")
    isolate()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    enable_source()
    import numpy as np
    import psutil
    import torch
    import soundfile as sf
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music
    from .validate import EXPECTED, int8_layers
    import importlib.metadata
    versions = {name: importlib.metadata.version(name) for name in EXPECTED}
    if versions != EXPECTED or not torch.cuda.is_available():
        raise RuntimeError("Entorno no validado o CUDA ausente. Ejecutar 07_VALIDAR_ENTORNO.bat.")
    if "4060" not in torch.cuda.get_device_name(0):
        raise RuntimeError("cuda:0 no corresponde a la RTX 4060 esperada.")
    started = time.perf_counter()
    use_lm = request["use_lm"]
    with trace.span("checkpoints.verify"):
        verify_models(use_lm)
        checkpoint_config = validate_configs(use_lm)
    random.seed(request["seed"])
    np.random.seed(request["seed"])
    torch.manual_seed(request["seed"])
    torch.cuda.manual_seed_all(request["seed"])
    if psutil.virtual_memory().available < 4 * 1024**3:
        raise RuntimeError("Menos de 4 GiB de RAM disponible al iniciar; detener y liberar aplicaciones manualmente.")
    free, total = torch.cuda.mem_get_info()
    if free < 4.5 * 1024**3:
        raise RuntimeError("Menos de 4.5 GiB de VRAM libre; no se inicia esta prueba.")
    budget = min(free - 768 * 1024**2, total * .80)
    torch.cuda.set_per_process_memory_fraction(budget / total, 0)
    torch.cuda.reset_peak_memory_stats(0)

    class StreamingHandler(StreamingOffloadMixin, TextEncoderCPUOffloadMixin, AceStepHandler):
        pass

    handler_base = type("PhasedHandler", (VocalConditioningMixin, PhasedPipelineMixin, StreamingHandler), {}) if phased else StreamingHandler

    class SmallModelHandler(handler_base):
        def _ensure_models_present(self, *, checkpoint_path, config_path, prefer_source, vae_variant=None):
            # Upstream requires an unused LM 1.7B as part of "main". This integration
            # checks only the three core components, already hash-verified above.
            if config_path != "acestep-v15-turbo" or vae_variant not in (None, "official"):
                return "Solo se admite Turbo 2B y VAE oficial en esta primera prueba.", False
            from acestep.model_downloader import check_model_exists
            for component in ("acestep-v15-turbo", "vae", "Qwen3-Embedding-0.6B"):
                if not check_model_exists(component, checkpoint_path):
                    return f"Falta componente local: {component}. No se descarga automaticamente.", False
            return None

    dit = SmallModelHandler()
    dit.vocal_request = request
    dit.vocal_report = {"schema": 1, "status": "pending", "vocal_presence": "PENDIENTE_DE_ESCUCHA"}
    dit.vocal_report_path = run / "condicionamiento-vocal.json"
    dit.memory_trace = trace
    dit.phase_allocator_budget_bytes = int(budget)
    trace.emit("worker.preparation", "end")
    timings = {"preparacion_s": time.perf_counter() - started}
    init_start = time.perf_counter()
    with trace.span("initialize.phase_registry" if phased else "initialize.dit_vae_encoder"):
        status, ok = dit.initialize_service(
            project_root=str(ROOT), config_path="acestep-v15-turbo", device="cuda",
            use_flash_attention=False, compile_model=False, quantization="int8_weight_only",
            offload_to_cpu=True, offload_dit_to_cpu=True, use_mlx_dit=False,
        )
    print("Candidata preparada: solo metadatos/tokenizer/silence. Validacion INT8/CUDA pendiente de la fase DiT." if phased and ok else status, flush=True)
    if not ok:
        raise RuntimeError("No se pudo cargar DiT: " + status)
    quantized = None if phased else int8_layers(dit.model.decoder)
    if dit.quantization != "int8_weight_only" or quantized == 0:
        raise RuntimeError("INT8 no esta efectivo: se detiene, sin fallback silencioso a BF16.")
    if dit.device != "cuda" or not dit.offload_to_cpu or not dit.offload_dit_to_cpu or dit.compiled:
        raise RuntimeError("La configuracion efectiva de GPU/offload/compilacion no coincide.")
    timings["registro_componentes_s" if phased else "carga_dit_vae_encoder_s"] = time.perf_counter() - init_start
    lm = None
    if use_lm:
        from acestep.llm_inference import LLMHandler
        init_start = time.perf_counter()
        lm = LLMHandler()
        status, ok = lm.initialize(checkpoint_dir=str(CHECKPOINTS), lm_model_path="acestep-5Hz-lm-0.6B",
                                   backend="pt", device="cuda", offload_to_cpu=True, dtype=torch.bfloat16)
        print(status, flush=True)
        if not ok or lm.llm_backend != "pt" or lm.device != "cuda" or not lm.offload_to_cpu:
            raise RuntimeError("LM 0.6B no se inicializo con pt/offload. Usar 06_PRUEBA_SIN_LM.bat tras revisar el error.")
        timings["carga_lm_s"] = time.perf_counter() - init_start
    params = make_generation_params(GenerationParams, request, phased=phased)
    config = GenerationConfig(batch_size=1, allow_lm_batch=False, use_random_seed=False,
                              seeds=[request["seed"]], audio_format="wav")
    effective = dict(
        requested=request, params=asdict(params), generation_config=asdict(config), versions=versions,
        checkpoint_config=checkpoint_config, code_commit=read_json(ROOT / "instalacion/bootstrap.lock.json")["source"]["commit"],
        model_revisions=[{"repo": m["repo"], "revision": m["revision"]} for m in read_json(ROOT / "instalacion/modelos.lock.json")["models"] if use_lm or not m["prefix"]],
        model_manifest_sha256=sha256(ROOT / "instalacion/modelos.lock.json"),
        quantization_effective=None if phased else dit.quantization, int8_linear_layers=quantized, dtype=str(dit.dtype),
        offload_to_cpu=dit.offload_to_cpu, offload_dit_to_cpu=dit.offload_dit_to_cpu,
        compiled=dit.compiled, lm_backend=lm.llm_backend if lm else None,
        pytorch_allocator_budget_bytes=int(budget), gpu=torch.cuda.get_device_name(0),
        note="El limite del allocator no incluye todas las asignaciones CUDA ni procesos ajenos.",
    )
    # Runtime wrappers only: source hashes remain unchanged. Record the two
    # distinct engine operations after load, without logging prompt/lyrics.
    if not phased:
        instrument_model_operations(dit.model, trace)
    effective["memory_transfer_strategy"] = "phased_v1" if phased else "streaming_aqt_v1"
    effective["quantization_validation_status"] = "pending_until_model_phase" if phased else "effective"
    effective["integration_files_sha256"] = {name: sha256(ROOT / "musicstudio" / name) for name in (
        "generate_worker.py", "phased_pipeline.py", "streaming_offload.py", "text_encoder_memory.py", "vocal_conditioning.py",
    )}
    effective["memory_trace_schema"] = 1
    write_json(run / "configuracion.json", effective)
    generation_start = time.perf_counter()
    try:
        with trace.span("inference"), torch.inference_mode():
            result = generate_music(dit, lm, params, config, save_dir=str(run))
    finally:
        if phased:
            if not dit.vocal_report_path.exists():
                dit.vocal_report["status"] = "incomplete"
                write_json(dit.vocal_report_path, dit.vocal_report)
            write_json(run / "ciclo-componentes.json", dit.phase_report)
            effective["phase_lifecycle"] = dit.phase_report
            effective["int8_linear_layers"] = dit.phase_report.get("int8_linear_layers")
            cuda_validated = dit.phase_report.get("dit_cuda_validated", False)
            effective["quantization_validation_status"] = "effective_cuda" if cuda_validated else "not_validated_cuda"
            effective["quantization_effective"] = dit.quantization if cuda_validated else None
            write_json(run / "configuracion.json", effective)
    torch.cuda.synchronize()
    timings["generacion_total_s"] = time.perf_counter() - generation_start
    timings["etapas_motor"] = result.extra_outputs.get("time_costs", {})
    write_json(run / "motor-estado.json", dict(success=result.success, error=result.error, status_message=result.status_message))
    if not result.success or len(result.audios) != 1:
        raise RuntimeError(result.error or "El motor no devolvio exactamente una toma.")
    if phased and (not dit.phase_report.get("int8_linear_layers") or not dit.phase_report["dit_cuda_validated"]
                   or dit._phase_failed or not dit._phase_decode_completed
                   or dit.phase_report["loads"] != dit.phase_report["releases"]):
        raise RuntimeError("La candidata no completo/valido el ciclo INT8 por fases.")
    audio = result.audios[0]
    tensor = audio.get("tensor")
    if tensor is None or not torch.isfinite(tensor).all().item():
        raise RuntimeError("Audio ausente o con NaN/Inf.")
    wave = run / "primera_cancion.wav"
    with trace.span("export.wav"):
        sf.write(str(wave), tensor.detach().float().cpu().numpy().T, int(audio["sample_rate"]), subtype="PCM_16")
    info = sf.info(str(wave))
    if abs(info.duration - 30) > 2:
        raise RuntimeError(f"Duracion inesperada: {info.duration:.2f} s; escuchar/revisar, no marcar como toma correcta.")
    mp3_status = "no disponible"
    try:
        mp3 = run / "primera_cancion.mp3"
        process = subprocess.run([str(ffmpeg_bin() / "ffmpeg.exe"), "-nostdin", "-v", "error", "-y", "-i", str(wave),
                                  "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3)], capture_output=True, text=True, timeout=90)
        mp3_status = "exportado" if process.returncode == 0 and mp3.is_file() and mp3.stat().st_size else "error: " + process.stderr
    except Exception as exc:
        mp3_status = "error: " + str(exc)
    timings["total_worker_s"] = time.perf_counter() - started
    write_json(run / "resultado.json", dict(
        success=True, musical_quality="PENDIENTE_DE_ESCUCHA", duration_seconds=info.duration,
        sample_rate=info.samplerate, channels=info.channels, wav_bytes=wave.stat().st_size,
        wav_sha256=sha256(wave), mp3=mp3_status, timings=timings,
        cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(0),
        cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(0),
        effective_params_after_engine=asdict(params), audio_params=audio.get("params", {}),
    ))
    print("WAV guardado. Voz, letra y calidad musical pendientes de escucha.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    trace = MemoryTrace(args.run)
    try:
        with trace.span("worker"):
            perform(read_json(args.request), args.run, trace)
        return 0
    except Exception:
        error = traceback.format_exc()
        (args.run / "error.txt").write_text(error, encoding="utf-8")
        write_json(args.run / "resultado.json", {"success": False, "error": error})
        print(error, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
