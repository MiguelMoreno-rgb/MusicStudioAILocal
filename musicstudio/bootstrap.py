"""Extract verified source/codec archives only inside the private runtime."""
from pathlib import Path
import argparse
import zipfile

from .common import ROOT, STATE, read_json, safe_path, sha256, write_json


def extract_verified(kind, archive):
    pins = read_json(ROOT / "instalacion/bootstrap.lock.json")
    if sha256(archive) != pins[kind]["sha256"]:
        raise RuntimeError(f"Checksum incorrecto: {kind}. No se extrae el archivo.")
    destination = ROOT / ".runtime" / kind
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            safe_path(destination, item.filename)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Enlace simbolico inesperado en ZIP.")
        # Al reinstalar, no sobrescribir archivos locales modificados.
        for item in bundle.infolist():
            if item.is_dir():
                continue
            target = safe_path(destination, item.filename)
            data = bundle.read(item)
            if target.exists() and target.read_bytes() != data:
                raise RuntimeError(f"Archivo privado cambiado: {target.name}; no se sobrescribe.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(data)
    if kind == "ffmpeg":
        files = {p.relative_to(destination).as_posix(): sha256(p) for p in destination.rglob("*") if p.is_file()}
        write_json(STATE / "ffmpeg-files.json", files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["source", "ffmpeg"])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    extract_verified(args.kind, args.archive)
