"""Explicit entry point used with isolated Python (-I -B)."""
import os
from pathlib import Path
import runpy
import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
if len(sys.argv) < 2:
    raise SystemExit("Falta accion.")
action = sys.argv.pop(1)
modules = {"bootstrap": "musicstudio.bootstrap", "validate": "musicstudio.validate", "models": "musicstudio.models",
           "generate": "musicstudio.generate", "worker": "musicstudio.generate_worker"}
if action not in modules:
    raise SystemExit("Accion desconocida.")
runpy.run_module(modules[action], run_name="__main__")
