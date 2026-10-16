"""Paths, integrity and isolated process configuration (standard library only)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
RESULTS = ROOT / "resultados"
CHECKPOINTS = ROOT / "checkpoints"
_DLL_HANDLES = []


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def new_run(kind):
    run = RESULTS / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + kind)
    run.mkdir(parents=True, exist_ok=False)
    return run


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_path(base, name):
    base = Path(base).resolve()
    target = (base / name).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError(f"Ruta fuera del proyecto: {name}")
    return target


def source_root():
    commit = read_json(ROOT / "instalacion/bootstrap.lock.json")["source"]["commit"]
    return ROOT / ".runtime/source" / ("ACE-Step-1.5-" + commit)


def isolate():
    for name in ("PYTHONPATH", "PYTHONHOME"):
        os.environ.pop(name, None)
    # Todas las asignaciones pertenecen solo a este proceso y sus hijos.
    settings = {
        "HF_HOME": ROOT / ".cache/huggingface",
        "HF_HUB_CACHE": ROOT / ".cache/huggingface/hub",
        "HF_MODULES_CACHE": ROOT / ".cache/huggingface/modules",
        "TORCH_HOME": ROOT / ".cache/torch",
        "TORCHINDUCTOR_CACHE_DIR": ROOT / ".cache/inductor",
        "TRITON_CACHE_DIR": ROOT / ".cache/triton",
        "NUMBA_CACHE_DIR": ROOT / ".cache/numba",
        "MPLCONFIGDIR": ROOT / ".cache/matplotlib",
        "XDG_CACHE_HOME": ROOT / ".cache",
        "TEMP": ROOT / "tmp", "TMP": ROOT / "tmp",
        "ACESTEP_PROJECT_ROOT": ROOT,
        "ACESTEP_CHECKPOINTS_DIR": CHECKPOINTS,
    }
    for name, path in settings.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ.update({
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_DISABLE_XET": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1", "ACESTEP_LM_BACKEND": "pt",
        "ACESTEP_VAE_CHECKPOINT": "official",
    })
    codec_bin = ffmpeg_bin()
    if codec_bin:
        os.environ["PATH"] = str(codec_bin) + os.pathsep + os.environ.get("PATH", "")
        if os.name == "nt":
            _DLL_HANDLES.append(os.add_dll_directory(str(codec_bin)))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def ffmpeg_bin():
    bins = list((ROOT / ".runtime/ffmpeg").glob("*/bin"))
    return bins[0] if len(bins) == 1 else None


def verify_source():
    pin = read_json(ROOT / "instalacion/bootstrap.lock.json")
    source = source_root()
    for name, expected in pin["source_files"].items():
        path = safe_path(source, name)
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"Codigo ACE-Step ausente o modificado: {name}. No actualizar vendor manualmente.")
    return source


def enable_source():
    source = verify_source()
    sys.path.insert(0, str(source))
    return source


def nvidia_inventory():
    executable = shutil.which("nvidia-smi")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError("nvidia-smi no disponible; no se instala ni cambia el controlador.")
    result = subprocess.run([
        executable, "--query-gpu=index,name,memory.total,memory.free,memory.used,driver_version",
        "--format=csv,noheader,nounits",
    ], capture_output=True, text=True, timeout=15, check=True)
    rows = []
    for line in result.stdout.strip().splitlines():
        index, name, total, free, used, driver = [part.strip() for part in line.split(",")]
        rows.append(dict(index=int(index), name=name, total_mib=int(total), free_mib=int(free), used_mib=int(used), driver=driver))
    return rows
