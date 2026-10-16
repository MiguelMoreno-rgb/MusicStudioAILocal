"""Offline, transactional repair of this project's Windows relocation.

Run with the moved standalone Python (-I -S -B), not the broken venv launcher.
No pip/uv invocation, package installation, downloads or registry changes.
"""
from __future__ import annotations

import base64
import csv
from datetime import datetime
import hashlib
import importlib.metadata
import io
import json
import ntpath
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('.runtime/python/cpython-3.12.12-windows-x86_64-none')
SENSITIVE = ('.venv', '.runtime', '.cache', 'checkpoints', 'resultados')
CLOUD_FLAGS = 0x1000 | 0x40000 | 0x400000  # OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS


class StopRepair(RuntimeError):
    pass


class RepairPlan(dict):
    originals: dict


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def win_equal(a, b):
    return ntpath.normcase(ntpath.normpath(str(a))) == ntpath.normcase(ntpath.normpath(str(b)))


def replace_root(value, old, new):
    """Replace an exact old-root prefix, never a similarly named sibling."""
    a, b = str(old).replace('/', '\\'), str(new).replace('/', '\\')
    normalized = value.replace('/', '\\')
    if win_equal(normalized, a):
        return b
    if normalized.lower().startswith(a.rstrip('\\').lower() + '\\'):
        return b.rstrip('\\') + normalized[len(a.rstrip('\\')):]
    if win_equal(normalized, b) or normalized.lower().startswith(b.rstrip('\\').lower() + '\\'):
        return value
    raise StopRepair(f'Referencia fuera de las dos raices autorizadas: {value}')


def local_file(path):
    stat = path.lstat()
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise StopRepair(f'Enlace/junction no admitido: {path}')
    if getattr(stat, 'st_file_attributes', 0) & CLOUD_FLAGS:
        raise StopRepair(f'Archivo solo en nube o pendiente de recuperar: {path}. Marcar Siempre mantener en este dispositivo y esperar.')
    return stat


def snapshot(root, progress=False):
    """Read-only census. Hash everything we promise not to lose or overwrite."""
    result = {}
    def cannot_read(error):
        raise StopRepair(f'No se puede inventariar un directorio protegido: {error}')
    for directory in SENSITIVE:
        base = root / directory
        if not base.exists():
            continue
        for current, dirs, files in os.walk(base, followlinks=False, onerror=cannot_read):
            local_file(Path(current))
            for child in dirs:
                local_file(Path(current) / child)
            for name in files:
                path = Path(current) / name
                local_file(path)
                result[path.relative_to(root).as_posix()] = file_digest(path)
                if progress and len(result) % 1500 == 0:
                    print(f'Inventario sin escribir: {len(result)} archivos comprobados...', flush=True)
    return result


def read_cfg(data, old, new):
    text = data.decode('utf-8-sig')
    home = None
    out = []
    allowed = {'home', 'base-prefix', 'base-exec-prefix', 'base-executable'}
    for line in text.splitlines(keepends=True):
        match = re.match(r'^(\s*([\w-]+)\s*=\s*)(.*?)(\r?\n)?$', line)
        if match and match[2].lower() in allowed:
            key, value = match[2].lower(), match[3]
            expected = str(Path(old) / RUNTIME)
            destination = str(Path(new) / RUNTIME)
            if key == 'base-executable':
                expected += '\\python.exe'
                destination += '\\python.exe'
            if not (win_equal(value, expected) or win_equal(value, destination)):
                raise StopRepair(f'pyvenv.cfg {key} no apunta al runtime privado esperado: {value}')
            value = replace_root(value, old, new)
            if key == 'home':
                home = value
            line = match[1] + value + (match[4] or '')
        elif str(old).lower() in line.lower() or str(old).replace('\\', '/').lower() in line.lower():
            raise StopRepair('Referencia antigua en un campo pyvenv.cfg no reconocido. No se sustituye a ciegas.')
        out.append(line)
    if home is None or re.search(r'(?mi)^include-system-site-packages\s*=\s*true', text):
        raise StopRepair('pyvenv.cfg sin home privado o con site-packages globales.')
    encoding = 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'
    return ''.join(out).encode(encoding)


def rewrite_launcher(data, old, new, pins):
    """uv 0.9.6 trailer: payload + UTF-8 path + u32 length + UVPY/UVSC.

    Leave the official PE and embedded script bytes unchanged. Reject unknown
    binaries instead of doing a global byte/string replacement inside EXEs.
    """
    if data[-4:] not in (b'UVPY', b'UVSC'):
        return None
    if len(data) < 8 or data[:2] != b'MZ':
        raise StopRepair('Launcher uv malformado.')
    length = struct.unpack('<I', data[-8:-4])[0]
    if not 0 < length <= 32768 or length > len(data) - 8:
        raise StopRepair('Longitud de ruta invalida en launcher uv.')
    prefix = data[:-8-length]
    trusted = next((p for p in pins if len(prefix) >= p['bytes'] and digest(prefix[:p['bytes']]) == p['sha256']), None)
    if trusted is None:
        raise StopRepair('PE de launcher no coincide con las plantillas oficiales fijadas de uv 0.9.6.')
    if data[-4:] == b'UVPY' and len(prefix) != trusted['bytes']:
        raise StopRepair('Payload inesperado en launcher de Python.')
    if data[-4:] == b'UVSC' and prefix[trusted['bytes']:trusted['bytes']+4] != b'PK\x03\x04':
        raise StopRepair('Payload de script uv no reconocido.')
    try:
        target = data[-8-length:-8].decode('utf-8')
    except UnicodeError as exc:
        raise StopRepair('Ruta uv no es UTF-8.') from exc
    if not ntpath.isabs(target):
        raise StopRepair('Launcher relativo inesperado en esta instalacion no relocatable.')
    changed = replace_root(target, old, new)
    allowed_targets = [str(Path(new) / RUNTIME / name) for name in ('python.exe', 'pythonw.exe')]
    allowed_targets += [str(Path(new) / '.venv/Scripts' / name) for name in ('python.exe', 'pythonw.exe')]
    if not any(win_equal(changed, p) for p in allowed_targets):
        raise StopRepair('El launcher no apunta a uno de los Python privados autorizados.')
    encoded = changed.encode('utf-8')
    if len(encoded) > 32768:
        raise StopRepair('Ruta nueva demasiado larga para uv.')
    result = prefix + encoded + struct.pack('<I', len(encoded)) + data[-4:]
    if result[:-8-len(encoded)] != prefix:
        raise StopRepair('Se altero el payload ejecutable; se cancela.')
    return result


def rewrite_activation(data, old, new):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        text = data.decode(encoding)
    except UnicodeError as exc:
        raise StopRepair('Script de activacion con codificacion desconocida.') from exc
    # These are uv-created activation scripts, never package source code.
    for separator in ('\\', '/'):
        before = str(old).replace('\\', separator).replace('/', separator)
        after = str(new).replace('\\', separator).replace('/', separator)
        pattern = re.compile(re.escape(before) + r'(?=[\\/"\s\x27]|$)', re.I)
        text = pattern.sub(lambda _: after, text)
    return text.encode(encoding)


def rewrite_wheel_script(data, old, new, pin):
    """Audited wheel .data/scripts Python text; change only its shebang.

    uv 0.9.6 install_script replaces #!python with an absolute interpreter.
    With a CRLF source it consumes CR only, retaining LF before the body.
    Both byte-exact bodies are pinned; no newline or source normalization.
    """
    first, separator, body = data.partition(b'\r\n')
    if not separator or not first.startswith(b'#!') or digest(body) not in pin['body_sha256']:
        raise StopRepair('Script wheel no coincide con el cuerpo/formato auditado. No se modifica.')
    try:
        target = first[2:].decode('utf-8')
    except UnicodeError as exc:
        raise StopRepair('Shebang wheel no es UTF-8.') from exc
    before = str(Path(old) / '.venv/Scripts/python.exe')
    after = str(Path(new) / '.venv/Scripts/python.exe')
    if not any(win_equal(target, allowed) for allowed in (before, after)):
        raise StopRepair('Shebang wheel no apunta al Python privado autorizado.')
    if win_equal(target, after):
        return data
    # Preserve every byte after the first line, including uv's extra LF.
    return b'#!' + after.replace('/', '\\').encode('utf-8') + separator + body


def check_wheel_script_owner(root, path, distributions, pin):
    """Require the exact installed distribution and a hashed RECORD entry.

    record_changes subsequently checks this hash against the original census,
    and updates it in the same transaction as the script.
    """
    matches = []
    for dist in distributions:
        if (dist.metadata['Name'].lower(), dist.version) != (pin['distribution'], pin['version']):
            continue
        record = Path(dist._path) / 'RECORD'
        if not record.is_file():
            raise StopRepair(f'Falta RECORD del script {path.name}.')
        for row in csv.reader(io.StringIO(record.read_text(encoding='utf-8'))):
            if len(row) != 3:
                raise StopRepair(f'RECORD malformado del script {path.name}.')
            if (root / '.venv/Lib/site-packages' / row[0]).resolve() == path.resolve():
                matches.append(row)
    if len(matches) != 1 or not matches[0][1].startswith('sha256=') or not matches[0][2].isdigit():
        raise StopRepair(f'Script {path.name} sin propietario RECORD unico con hash/tamano. No se modifica.')


def expected_packages(lock):
    result = {}
    for line in lock.read_text().splitlines():
        match = re.match(r'^([a-zA-Z0-9_-]+)==([^\s\\]+)', line)
        if match:
            result[match[1].lower().replace('_', '-')] = match[2]
        elif line.startswith(('torch @ ', 'torchaudio @ ', 'torchvision @ ')):
            name = line.split()[0]
            result[name] = {'torch': '2.10.0+cu128', 'torchaudio': '2.10.0+cu128', 'torchvision': '0.25.0+cu128'}[name]
    if len(result) != 57:
        raise StopRepair('El lock no contiene las 57 distribuciones esperadas.')
    return result


def installed_packages(root, expected):
    site = root / '.venv/Lib/site-packages'
    result = {}
    distributions = list(importlib.metadata.distributions(path=[str(site)]))
    for dist in distributions:
        metadata = dist.metadata
        declared_name = metadata.get('Name')
        version = metadata.get('Version')
        if not declared_name or not version:
            raise StopRepair(f'METADATA de distribucion ausente/incompleta: {dist._path}. No se modifica ni reinstala.')
        name = re.sub(r'[-_.]+', '-', declared_name).lower()
        if name in result:
            raise StopRepair(f'Distribucion duplicada: {name}')
        result[name] = version
    missing = {k: (v, result.get(k)) for k, v in expected.items() if result.get(k) != v}
    if missing:
        raise StopRepair(f'Paquetes ausentes o versiones distintas: {missing}. No se reinstala ni descarga nada.')
    return distributions, result


def record_changes(root, distributions, planned, inventory):
    """Validate wheel RECORDs first; only update hashes of repaired launchers."""
    site = root / '.venv/Lib/site-packages'
    updates = {}
    for dist in distributions:
        # All distributions installed by our wheel-only installer must have RECORD.
        record_file = Path(dist._path) / 'RECORD'
        if not record_file.is_file():
            raise StopRepair(f'Falta RECORD de {dist.metadata["Name"]}. No se puede comprobar su integridad.')
        rows = list(csv.reader(io.StringIO(record_file.read_text(encoding='utf-8'))))
        changed = False
        for row in rows:
            if len(row) != 3:
                raise StopRepair(f'RECORD malformado: {record_file.name}')
            name, expected_hash, size = row
            path = (site / name).resolve()
            if not path.is_relative_to((root / '.venv').resolve()):
                raise StopRepair(f'RECORD apunta fuera de .venv: {name}')
            relative = path.relative_to(root).as_posix()
            if relative not in inventory:
                # Bytecode is optional and never needed to preserve an installed wheel.
                if not expected_hash and name.endswith('.pyc'):
                    continue
                raise StopRepair(f'Archivo instalado ausente: {name}')
            if expected_hash:
                algorithm, encoded = expected_hash.split('=', 1)
                if algorithm != 'sha256':
                    raise StopRepair(f'Hash RECORD no admitido: {algorithm}')
                actual = base64.urlsafe_b64encode(bytes.fromhex(inventory[relative])).rstrip(b'=').decode()
                if actual != encoded or size and path.stat().st_size != int(size):
                    raise StopRepair(f'Integridad de paquete no coincide: {name}. No se toca el entorno.')
            if relative in planned:
                new_data = planned[relative]
                row[1] = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(new_data).digest()).rstrip(b'=').decode()
                row[2] = str(len(new_data))
                changed = True
        if changed:
            out = io.StringIO(newline='')
            csv.writer(out, lineterminator='\n').writerows(rows)
            updates[record_file.relative_to(root).as_posix()] = out.getvalue().encode()
    return updates


def preflight(root, old, pins):
    """Strictly read-only. Return a closed write plan and before-state inventory."""
    root, old = root.resolve(), old.resolve()
    for directory in (root, root / '.state', root / 'musicstudio', root / 'instalacion'):
        local_file(directory)
        if not directory.resolve().is_relative_to(root):
            raise StopRepair(f'Directorio fuera de la raiz autorizada: {directory}')
    backups = root / '.state/repair-backups'
    if backups.exists():
        local_file(backups)
    for journal in (root / '.state/repair-backups').glob('*/journal.json'):
        local_file(journal)
        status = json.loads(journal.read_text()).get('status')
        if status not in ('committed', 'rolled_back'):
            raise StopRepair(f'Hay una reparacion interrumpida o restauracion incompleta: {journal}. Conservar respaldo y solicitar revision antes de continuar.')
    if not (root / '.venv/Scripts/python.exe').is_file() or not (root / RUNTIME / 'python.exe').is_file():
        raise StopRepair('Falta Python del entorno o runtime privado. No se crean ni descargan.')
    owner_path = root / '.state/owner.json'
    local_file(owner_path)
    owner_data = owner_path.read_bytes()
    owner = json.loads(owner_data.decode('utf-8-sig'))
    if owner.get('project') != 'MusicStudioAILocal' or not any(win_equal(owner.get('root', ''), p) for p in (old, root)):
        raise StopRepair('El marcador no corresponde a esta instalacion y estas dos rutas.')
    for name, checksum in pins['required_code'].items():
        path = root / name
        local_file(path)
        if not path.resolve().is_relative_to(root):
            raise StopRepair(f'Codigo fuera de la raiz autorizada: {name}')
        if file_digest(path) != checksum:
            raise StopRepair(f'Falta la correccion INT8 o codigo esperado: {name}. Copiar el archivo publicado antes de reparar.')
    print('Comprobando paquetes, runtime, caches, modelos y resultados sin escribir...', flush=True)
    inventory = snapshot(root, progress=True)
    expected = expected_packages(root / 'instalacion/requirements-windows.lock.txt')
    distributions, versions = installed_packages(root, expected)
    if old.exists() and old != root and (old / '.venv').exists():
        print('La ruta original todavia existe: se compara; no se escribe en ella.', flush=True)
        _, old_versions = installed_packages(old, expected)
        if old_versions != versions:
            raise StopRepair('Las dos instalaciones tienen distribuciones distintas. Identificar la copia completa antes de continuar.')
        # Package payloads must agree if two copies still exist; don't merge them.
        for name, checksum in inventory.items():
            if name.startswith('.venv/Lib/site-packages/') and not name.endswith(('.pyc', '.dist-info/RECORD')):
                path = old / name
                if not path.is_file():
                    raise StopRepair(f'La copia original difiere o esta incompleta: {name}')
                local_file(path)
                if file_digest(path) != checksum:
                    raise StopRepair(f'Las copias difieren: {name}. No se decide automaticamente cual conservar.')
    planned = RepairPlan()
    cfg = root / '.venv/pyvenv.cfg'
    local_file(cfg)
    updated_cfg = read_cfg(cfg.read_bytes(), old, root)
    if updated_cfg != cfg.read_bytes():
        planned['.venv/pyvenv.cfg'] = updated_cfg
    scripts = root / '.venv/Scripts'
    activations = {'activate', 'activate.bat', 'activate.ps1', 'activate.csh', 'activate.fish', 'activate.nu', 'activate_this.py', 'deactivate.bat'}
    unknown = []
    for path in scripts.iterdir():
        if not path.is_file():
            continue
        local_file(path)
        data = path.read_bytes()
        rewritten = None
        if path.suffix.lower() == '.exe':
            rewritten = rewrite_launcher(data, old, root, pins['launcher_prefixes'])
            if rewritten is not None:
                length = struct.unpack('<I', rewritten[-8:-4])[0]
                target = Path(rewritten[-8-length:-8].decode('utf-8'))
                if not target.is_file():
                    raise StopRepair(f'El launcher apuntaria a un Python que no existe: {target}')
                local_file(target)
            if rewritten is None and any(token in data.lower() for token in (str(old).lower().encode(), str(old).lower().encode('utf-16-le'))):
                raise StopRepair(f'EXE con referencia antigua y formato no reconocido: {path.name}. No se modifica.')
        elif path.name.lower() in activations:
            rewritten = rewrite_activation(data, old, root)
        elif path.name in pins.get('wheel_scripts', {}):
            pin = pins['wheel_scripts'][path.name]
            check_wheel_script_owner(root, path, distributions, pin)
            rewritten = rewrite_wheel_script(data, old, root, pin)
        elif str(old).lower().encode() in data.lower():
            unknown.append(path.name)
        if rewritten is not None and rewritten != data:
            planned[path.relative_to(root).as_posix()] = rewritten
    if unknown:
        raise StopRepair('Referencias antiguas en scripts no reconocidos: ' + ', '.join(sorted(unknown)) + '. Requieren revision; no se escribe nada.')
    planned.update(record_changes(root, distributions, planned, inventory))
    new_owner = dict(owner, root=str(root))
    if not win_equal(owner['root'], root):
        planned['.state/owner.json'] = (json.dumps(new_owner, indent=2) + '\n').encode()
    # Invalidate the prior location's validation; full validation must recreate it.
    validation = root / '.state/validation.json'
    if validation.exists():
        local_file(validation)
        planned['.state/validation.json'] = None
    planned.originals = {name: (root / name).read_bytes() for name in planned}
    for name in planned:
        path = root / name
        if getattr(local_file(path), 'st_file_attributes', 0) & 1 or not os.access(path, os.W_OK):
            raise StopRepair(f'Objetivo de reparacion de solo lectura/sin permiso: {name}. No se cambia ningun permiso automaticamente.')
    if '.state/owner.json' in planned and planned.originals['.state/owner.json'] != owner_data:
        raise StopRepair('El marcador cambio durante la comprobacion; no se escribe nada.')
    return planned, inventory, versions


def atomic_write(path, data):
    temporary = path.with_name(path.name + '.repair-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_transaction(root, planned, inventory, postcheck):
    """Back up every target, then atomically replace; roll back our targets on failure."""
    before = getattr(planned, 'originals', None)
    if before is None:
        before = {name: ((root / name).read_bytes() if (root / name).exists() else None) for name in planned}
    # Detect sync/process changes after the preview/confirmation and before any write.
    if snapshot(root) != inventory:
        raise StopRepair('Archivos cambiaron desde la comprobacion. Pausar sync/cerrar procesos y repetir; no se escribe nada.')
    for name, data in before.items():
        if (root / name).exists() != (data is not None) or data is not None and (root / name).read_bytes() != data:
            raise StopRepair(f'El objetivo cambio desde la vista previa: {name}. No se escribe nada.')
    backup = root / '.state/repair-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, exist_ok=False)
    journal = {'status': 'prepared', 'targets': {name: {'before_sha256': digest(data) if data is not None else None, 'after_sha256': digest(planned[name]) if planned[name] is not None else None} for name, data in before.items()}}
    journal['protected_inventory_sha256'] = digest(json.dumps(inventory, sort_keys=True).encode())
    (backup / 'inventory-before.json').write_text(json.dumps(inventory, indent=2))
    for name, data in before.items():
        if data is not None:
            destination = backup / 'originals' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    (backup / 'journal.json').write_text(json.dumps(journal, indent=2))
    touched = []
    try:
        for name, data in planned.items():
            path = root / name
            if (path.read_bytes() if path.exists() else None) != before[name]:
                raise StopRepair(f'El archivo cambio antes de actualizarlo: {name}')
            touched.append(name)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
        expected = dict(inventory)
        for name, data in planned.items():
            if name.startswith(tuple(prefix + '/' for prefix in SENSITIVE)):
                if data is None:
                    expected.pop(name, None)
                else:
                    expected[name] = digest(data)
        if snapshot(root) != expected:
            raise StopRepair('Cambios inesperados en archivos protegidos; se restaura solo lo modificado por la herramienta.')
        postcheck()
        if snapshot(root) != expected:
            raise StopRepair('Los archivos protegidos cambiaron durante la prueba; se restaura la reparacion.')
        journal['status'] = 'committed'
        (backup / 'journal.json').write_text(json.dumps(journal, indent=2))
        return backup
    except BaseException:
        failures = []
        for name in reversed(touched):
            try:
                current = (root / name).read_bytes() if (root / name).exists() else None
                if current == before[name]:
                    continue
                if current != planned[name]:
                    failures.append(f'{name}: cambio externo; se conserva sin sobrescribir. Revisar backup.')
                    continue
                if before[name] is None:
                    (root / name).unlink(missing_ok=True)
                else:
                    atomic_write(root / name, before[name])
            except OSError as exc:
                failures.append(f'{name}: {exc}')
        journal.update(status='rollback_failed' if failures else 'rolled_back', rollback_errors=failures)
        (backup / 'journal.json').write_text(json.dumps(journal, indent=2))
        if failures:
            print('Restauracion incompleta. No continuar. Backup:', backup, failures, flush=True)
        else:
            print('Cambios de la herramienta restaurados. Conservar backup:', backup, flush=True)
        raise


def actual_main():
    if sys.platform != 'win32' or sys.version_info[:3] != (3, 12, 12) or struct.calcsize('P') != 8:
        raise StopRepair('Ejecutar con el runtime privado Windows x64 Python 3.12.12, sin usar el .venv roto.')
    profile = Path(os.environ['USERPROFILE'])
    old = profile / 'Downloads/MusicStudioAILocal-main'
    new = profile / 'OneDrive/Documentos/MusicStudioAILocal/MusicStudioAILocal-main'
    if not win_equal(ROOT.resolve(), new.resolve()):
        raise StopRepair('Esta herramienta autoriza exclusivamente la nueva ubicacion solicitada en OneDrive\\Documentos.')
    if not win_equal(sys.executable, new / RUNTIME / 'python.exe') or not win_equal(sys.prefix, new / RUNTIME):
        raise StopRepair('El Python que ejecuta la reparacion no es el runtime privado trasladado.')
    print('Ruta original:', old, 'EXISTE' if old.exists() else 'YA NO EXISTE (traslado)', flush=True)
    print('Ruta definitiva:', new, flush=True)
    print('Marca la carpeta como Siempre mantener en este dispositivo; espera a que termine y pausa OneDrive.', flush=True)
    if input('Cierra los BAT/Python de este proyecto. Escribe PAUSADO para confirmar esas condiciones: ').strip() != 'PAUSADO':
        raise StopRepair('Cancelado sin modificar archivos.')
    pins = json.loads((ROOT / 'instalacion/reparacion.lock.json').read_text())
    planned, inventory, versions = preflight(ROOT, old, pins)
    print(f'Paquetes comprobados: {len(versions)}; archivos protegidos: {len(inventory)}.', flush=True)
    print('Cambios controlados propuestos:', *planned.keys(), sep='\n  ', flush=True)
    print('No se ejecutara pip/uv ni se descargaran paquetes/modelos. Se guardara respaldo local.', flush=True)
    if input('Escribe REPARAR para aplicar este plan; otra respuesta cancela: ').strip() != 'REPARAR':
        raise StopRepair('Cancelado sin escribir cambios ni backups.')
    # Process-only environment for the postcheck. No global Python/CUDA changes.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    env['TORCH_HOME'] = str(ROOT / '.cache/torch')
    probe = "import sys,struct,json,torch; from pathlib import Path; assert sys.version_info[:3]==(3,12,12) and struct.calcsize('P')==8, 'Python privado no es 3.12.12 x64'; assert Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve(), 'sys.prefix no corresponde al venv trasladado'; assert torch.__version__=='2.10.0+cu128', 'Version PyTorch inesperada'; assert torch.cuda.is_available() and torch.version.cuda=='12.8', 'CUDA de este PyTorch no esta disponible'; assert '4060' in torch.cuda.get_device_name(0), 'GPU seleccionada inesperada'; x=torch.ones((64,64),device='cuda',dtype=torch.bfloat16); assert torch.isfinite(x@x).all().item(), 'Matmul CUDA no finita'; torch.cuda.synchronize(); print(json.dumps(dict(python=sys.version.split()[0],torch=torch.__version__,cuda=torch.version.cuda,gpu=torch.cuda.get_device_name(0))))"
    def postcheck():
        result = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-I', '-B', '-c', probe, str(ROOT / '.venv')], env=env, capture_output=True, text=True, timeout=120)
        print(result.stdout, flush=True)
        if result.returncode:
            raise StopRepair('La prueba del Python reparado/PyTorch/CUDA fallo: ' + result.stderr)
        installed_packages(ROOT, versions)
    backup = apply_transaction(ROOT, planned, inventory, postcheck)
    print('Referencias reparadas y paquetes conservados. Backup:', backup, flush=True)
    # Separate full validation: a new codec/INT8 failure does not undo a proven relocation.
    print('Ejecutando ahora validacion completa, incluida correccion INT8. Sin descargar modelos.', flush=True)
    result = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-I', '-B', str(ROOT / 'instalacion/ejecutar.py'), 'validate'], env=env, cwd=ROOT)
    if result.returncode:
        print('El traslado quedo reparado; la validacion completa sigue pendiente. No descargar modelos. Compartir el error de resultados.', flush=True)
        return 1
    print('Reparacion y validacion local completadas. Compartir validacion.json antes de descargar modelos.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(actual_main())
    except (StopRepair, OSError, ValueError, KeyError) as exc:
        print('REPARACION DETENIDA:', exc, flush=True)
        raise SystemExit(1)
