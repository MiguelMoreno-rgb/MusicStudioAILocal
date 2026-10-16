"""Pinned allowlist; verify every artifact. No implicit whole-repo download."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import time

from .common import CHECKPOINTS, ROOT, STATE, enable_source, read_json, safe_path, sha256, write_json, isolate


def entries(with_lm=True):
    pin = read_json(ROOT / "instalacion/modelos.lock.json")
    for model in pin["models"]:
        if model["prefix"] and not with_lm:
            continue
        for file in model["files"]:
            yield model, file, safe_path(CHECKPOINTS, model["prefix"] + "/" + file["path"] if model["prefix"] else file["path"])


def matches(path, file):
    if not path.is_file():
        return False
    if file.get("source_path"):
        return sha256(path) == file["source_sha256"]
    if path.stat().st_size != file["bytes"]:
        return False
    if file["sha256"]:
        return sha256(path) == file["sha256"]
    digest = hashlib.sha1()  # Git blob identity from the official manifest, not TLS validation.
    digest.update(f"blob {file['bytes']}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == file["git_blob"]


def verify_models(with_lm):
    for _, file, path in entries(with_lm):
        if not matches(path, file):
            raise RuntimeError(f"Modelo ausente, incompleto o modificado: {path.relative_to(CHECKPOINTS)}. Ejecutar 04_DESCARGAR_MODELOS.bat.")


def validate_configs(with_lm):
    # Parse the exact checkpoint through the same source class as the loader, before weights.
    from acestep.models.turbo.configuration_acestep_v15 import AceStepConfig
    from transformers import AutoConfig
    from diffusers import AutoencoderOobleck
    dit = read_json(CHECKPOINTS / "acestep-v15-turbo/config.json")
    mapping = dit.get("auto_map", {})
    expected = {"AutoConfig": "configuration_acestep_v15.AceStepConfig", "AutoModel": "modeling_acestep_v15_turbo.AceStepConditionGenerationModel"}
    for key, value in expected.items():
        if mapping.get(key) != value:
            raise RuntimeError(f"auto_map {key} no coincide con el adaptador fijado: {mapping.get(key)!r}")
    config = AceStepConfig.from_dict(dit)
    hidden = getattr(config, "hidden_size", None)
    if hidden != 2048:
        raise RuntimeError(f"Arquitectura inesperada para Turbo 2B: hidden_size={hidden}.")
    encoder = AutoConfig.from_pretrained(str(CHECKPOINTS / "Qwen3-Embedding-0.6B"), local_files_only=True)
    if encoder.model_type != "qwen3":
        raise RuntimeError("El encoder no es Qwen3; revisar configuracion fijada.")
    vae = AutoencoderOobleck.load_config(str(CHECKPOINTS / "vae"), local_files_only=True)
    if vae.get("_class_name", "AutoencoderOobleck") != "AutoencoderOobleck":
        raise RuntimeError("VAE inesperado.")
    result = dict(dit_hidden_size=hidden, dit_model_type=config.model_type, encoder_model_type=encoder.model_type, vae_class="AutoencoderOobleck")
    if with_lm:
        lm = AutoConfig.from_pretrained(str(CHECKPOINTS / "acestep-5Hz-lm-0.6B"), local_files_only=True)
        if lm.model_type != "qwen3" or lm.hidden_size > 1536:
            raise RuntimeError("El LM no corresponde a la arquitectura pequena esperada.")
        result.update(lm_model_type=lm.model_type, lm_hidden_size=lm.hidden_size)
    return result


def download(with_lm):
    import importlib.metadata
    from .validate import EXPECTED
    validation = read_json(STATE / "validation.json")
    versions = {name: importlib.metadata.version(name) for name in EXPECTED}
    if (validation.get("status") != "LOCAL_ENVIRONMENT_CHECKS_PASSED_NOT_MODEL_GENERATION"
            or validation.get("requirements_sha256") != sha256(ROOT / "instalacion/requirements-windows.lock.txt")
            or versions != EXPECTED):
        raise RuntimeError("La validacion local no corresponde a este stack. Ejecutar 07_VALIDAR_ENTORNO.bat antes de descargar.")
    source = enable_source()
    from huggingface_hub import hf_hub_download
    minimum = (read_json(ROOT / "instalacion/modelos.lock.json")["total_bytes_metadata"] + 3 * 1024**3)
    if shutil.disk_usage(ROOT).free < minimum:
        raise RuntimeError("Falta espacio para modelos y margen de descarga (aprox. 11 GiB).")
    selected = list(entries(with_lm))
    # Configs and code first; multi-GB weights only after config/source checks.
    small = [(m, f, p) for m, f, p in selected if not f["path"].endswith((".safetensors", ".pt"))]
    large = [(m, f, p) for m, f, p in selected if f["path"].endswith((".safetensors", ".pt"))]
    for group in (small, large):
        for model, file, target in group:
            if matches(target, file):
                print("Verificado, se reutiliza:", target.relative_to(CHECKPOINTS), flush=True)
                continue
            if target.exists():
                raise RuntimeError(f"Archivo existente no coincide; no se sobrescribe: {target.name}. Conservar el error para revision.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if file.get("source_path"):
                original = safe_path(source, file["source_path"])
                if sha256(original) != file["source_sha256"]:
                    raise RuntimeError("Codigo de checkpoint no coincide con el commit fijado.")
                shutil.copyfile(original, target)
            else:
                print("Descargando archivo fijado:", model["repo"], file["path"], flush=True)
                base = CHECKPOINTS / model["prefix"]
                downloaded = hf_hub_download(repo_id=model["repo"], filename=file["path"], revision=model["revision"], local_dir=str(base))
                if Path(downloaded).resolve() != target.resolve():
                    raise RuntimeError("Ruta de descarga inesperada.")
            if not matches(target, file):
                raise RuntimeError(f"Integridad incorrecta: {file['path']}. No se usan esos pesos.")
        if group is small:
            config_report = validate_configs(with_lm)
            write_json(STATE / "checkpoint-configs.json", config_report)
    verify_models(with_lm)
    write_json(STATE / "models-verified.json", {"with_lm": with_lm, "time": time.time(), "manifest_sha256": sha256(ROOT / "instalacion/modelos.lock.json")})
    print("Archivos seleccionados verificados. Todavia falta cargar/generar en la GPU.", flush=True)


if __name__ == "__main__":
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--sin-lm", action="store_true")
    args = parser.parse_args()
    isolate()
    download(not args.sin_lm)
