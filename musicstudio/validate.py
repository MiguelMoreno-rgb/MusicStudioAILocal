"""Local hardware tests. No model download and no CPU fallback for CUDA."""
from __future__ import annotations

import importlib.metadata
import platform
import struct
import subprocess
import sys
import traceback

from .common import ROOT, STATE, enable_source, ffmpeg_bin, isolate, new_run, nvidia_inventory, read_json, safe_path, sha256, write_json

EXPECTED = {
    "torch": "2.10.0+cu128", "torchvision": "0.25.0+cu128",
    "torchaudio": "2.10.0+cu128", "torchcodec": "0.10.0", "torchao": "0.16.0",
    "transformers": "4.57.6", "diffusers": "0.37.1",
}


def int8_layers(model):
    import torch
    count = 0
    for module in model.modules():
        if not isinstance(module, torch.nn.Linear):
            continue
        impl = getattr(module.weight, "tensor_impl", None)
        data = getattr(impl, "int_data", None)
        if data is not None and data.dtype == torch.int8:
            count += 1
    return count


def run_checks(run):
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("La validacion real requiere Windows x64.")
    if sys.version_info[:3] != (3, 12, 12):
        raise RuntimeError("Se requiere el Python privado 3.12.12 fijado.")
    if not sys.prefix or str(ROOT / ".venv").lower() != str(__import__('pathlib').Path(sys.prefix)).lower():
        raise RuntimeError("Python no pertenece al .venv exclusivo.")
    versions = {name: importlib.metadata.version(name) for name in EXPECTED}
    if versions != EXPECTED:
        raise RuntimeError(f"Versiones distintas del stack fijado: {versions}")
    source = enable_source()
    codec_manifest = read_json(STATE / "ffmpeg-files.json")
    for name, digest in codec_manifest.items():
        path = safe_path(ROOT / ".runtime/ffmpeg", name)
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError(f"FFmpeg privado incompleto o modificado: {name}")
    codec_bin = ffmpeg_bin()
    if not codec_bin or not list(codec_bin.glob("avcodec-*.dll")):
        raise RuntimeError("Faltan las DLL compartidas privadas de FFmpeg.")
    import torch
    import torchaudio  # verify binary import, not just metadata
    import torchvision
    if not torch.cuda.is_available() or torch.version.cuda != "12.8":
        raise RuntimeError("CUDA 12.8 de este PyTorch no esta disponible; no se cambia el driver ni se usa CPU en silencio.")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("No se verifico soporte BF16 en la GPU seleccionada.")
    if "4060" not in torch.cuda.get_device_name(0):
        raise RuntimeError("La GPU cuda:0 no es la RTX 4060 esperada; revisar la seleccion de GPU.")
    torch.manual_seed(40601)
    matrix = torch.randn(64, 64, device="cuda", dtype=torch.bfloat16)
    assert torch.isfinite(matrix @ matrix).all().item(), "Matmul CUDA no finita"
    from .int8_offload import exercise_int8_offload
    # Match ACE-Step's actual offload path, including its AQT dispatch fallback.
    # Raw Module.to() under inference_mode is not the transfer API of this commit.
    int8_report = exercise_int8_offload("cuda", cycles=2)
    torch.cuda.synchronize()
    # Actual DLL/codec decode test using a local diagnostic sine, not generated music.
    import numpy as np
    import soundfile as sf
    import torchcodec
    from torchcodec.decoders import AudioDecoder
    wave = run / "prueba_codec.wav"
    sf.write(str(wave), np.sin(np.arange(4800) * (2 * np.pi * 440 / 48000)).astype('float32') * .1, 48000)
    decoded = AudioDecoder(str(wave)).get_all_samples()
    if decoded.data.numel() < 4000 or not torch.isfinite(decoded.data).all().item():
        raise RuntimeError("TorchCodec no decodifico el WAV de prueba.")
    mp3 = run / "prueba_codec.mp3"
    conversion = subprocess.run([str(codec_bin / "ffmpeg.exe"), "-nostdin", "-v", "error", "-y", "-i", str(wave), "-codec:a", "libmp3lame", str(mp3)], capture_output=True, text=True, timeout=60)
    if conversion.returncode or not mp3.is_file() or mp3.stat().st_size == 0:
        raise RuntimeError("FFmpeg privado fallo al exportar MP3: " + conversion.stderr)
    # Import the real inference APIs; catches missing dependencies before model downloads.
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music
    import inspect
    required = {"quantization", "offload_to_cpu", "offload_dit_to_cpu", "compile_model"}
    if not required.issubset(inspect.signature(AceStepHandler.initialize_service).parameters):
        raise RuntimeError("La API ACE-Step no coincide con el adaptador.")
    del matrix
    torch.cuda.empty_cache()
    return dict(versions=versions, python=platform.python_version(), cuda=torch.version.cuda,
                gpu=torch.cuda.get_device_name(0), gpu_inventory=nvidia_inventory(),
                int8_forward_and_offload=True, int8_offload=int8_report, codec_decode=True, mp3_export=True,
                source_commit=read_json(ROOT / "instalacion/bootstrap.lock.json")["source"]["commit"],
                requirements_sha256=sha256(ROOT / "instalacion/requirements-windows.lock.txt"),
                status="LOCAL_ENVIRONMENT_CHECKS_PASSED_NOT_MODEL_GENERATION")


def main():
    isolate()
    # Prevent accidental network activity during validation/imports.
    import os
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    run = new_run("validacion")
    marker = STATE / "validation.json"
    marker.unlink(missing_ok=True)  # only our own success marker; never reuse a failed run
    try:
        data = run_checks(run)
        write_json(run / "validacion.json", data)
        write_json(marker, data)
        print("Pruebas locales de CUDA, INT8/offload, codecs e imports completadas. Falta validar la generacion con pesos.")
        return 0
    except Exception:
        error = traceback.format_exc()
        (run / "error.txt").write_text(error, encoding="utf-8")
        print(error, file=sys.stderr)
        print("No se descargan modelos. Revisar resultados de esta validacion.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
