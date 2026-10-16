"""Git object based updater; stdlib only, no checkout, pip or project imports.

Run with the private *base* Python, even while .venv relocation is pending.
Git metadata, staging and backups live in .musicstudio-update, never .state.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import uuid

ORIGIN = 'https://github.com/MiguelMoreno-rgb/MusicStudioAILocal.git'
META = '.musicstudio-update'
RUNTIME = '.runtime/python/cpython-3.12.12-windows-x86_64-none/python.exe'
CLOUD = 0x1000 | 0x40000 | 0x400000
PROTECTED = {'.git', META, '.venv', '.runtime', '.cache', '.state', 'checkpoints',
             'resultados', 'results', 'outputs', 'logs', 'models', 'modelos', 'venv',
             'env', 'cache', 'downloads', 'tmp', 'temp', '__pycache__'}
DIRS = {'instalacion', 'musicstudio', 'diagnostico', 'fuentes', 'config', 'pruebas'}
SUFFIXES = {'.py', '.ps1', '.bat', '.md', '.txt', '.json', '.toml', '.lock', '.in'}

class Blocked(RuntimeError):
    pass


class ChangePlan(dict):
    observed: dict
    expected: dict


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def oid(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (not parts or name != '/'.join(parts) or any(p in ('..', '.') or ':' in p or '\\' in p or p.endswith((' ', '.')) for p in parts)
            or any(p.lower() in PROTECTED or p.startswith('.') and p not in ('.gitignore', '.gitattributes') for p in parts)
            or any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))} for p in parts)
            or parts[0] == 'config' and name not in ('config/prompt.txt', 'config/letra_ejemplo.txt')
            or len(parts) > 1 and parts[0] not in DIRS
            or Path(name).suffix.lower() not in SUFFIXES and name not in ('.gitignore', '.gitattributes')):
        raise Blocked(f'Ruta remota no permitida: {name}. Requiere revision del actualizador.')
    return name


def local(path):
    # Inspect parent components too: never traverse a junction to reach a backup.
    for component in [*reversed(path.parents), path]:
        if not (component.exists() or component.is_symlink()):
            continue
        st = component.lstat()
        attrs = getattr(st, 'st_file_attributes', 0)
        tag = getattr(st, 'st_reparse_tag', 0)
        cloud_tag = tag & 0xFFFF0FFF == 0x9000001A
        if component.is_symlink() or attrs & CLOUD or attrs & 0x400 and not cloud_tag:
            raise Blocked(f'Enlace/reparse point o archivo solo en nube: {component}')


def target(root, name):
    safe_name(name)
    path = root
    local(root)
    for part in PurePosixPath(name).parts:
        path = path / part
        local(path)
    if path.exists() and not path.is_file():
        raise Blocked(f'Un directorio ocupa el archivo previsto: {name}')
    return path


def read(path):
    local(path)
    return path.read_bytes() if path.exists() else None


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.update-' + uuid.uuid4().hex)
    try:
        with tmp.open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, obj):
    atomic(path, (json.dumps(obj, indent=2, ensure_ascii=True) + '\n').encode())


class Repository:
    def __init__(self, root, git='git', remote=ORIGIN):
        self.root, self.git, self.remote = Path(root), git, remote
        self.store = self.root / META
        self.repo = self.store / 'repo.git'
        self.empty_hooks = self.store / 'no-hooks'
        self.env = {k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
        self.env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')

    def call(self, *args):
        cmd = [self.git, '--no-optional-locks', '-c', 'core.autocrlf=false', '-c',
               'core.hooksPath=' + str(self.empty_hooks), '-c', 'fetch.fsckObjects=true',
               '-c', 'transfer.fsckObjects=true', '-c', 'http.sslVerify=true',
               '-c', 'protocol.ext.allow=never', '-c', 'protocol.version=2',
               '--git-dir=' + str(self.repo), *args]
        p = subprocess.run(cmd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode:
            raise Blocked('Git fallo: ' + p.stderr.decode('utf-8', 'replace')[-2500:])
        if b'filtering not recognized' in p.stderr.lower():
            raise Blocked('El servidor no admite descarga parcial; no se aplican archivos.')
        return p.stdout

    def connect(self):
        local(self.store)
        local(self.repo)
        if not self.repo.exists():
            self.empty_hooks.mkdir(parents=True, exist_ok=True)
            self.call('init', '--bare', '--template=')
            self.call('remote', 'add', 'origin', self.remote)
            self.call('config', 'remote.origin.promisor', 'true')
            self.call('config', 'remote.origin.partialclonefilter', 'blob:none')
        if self.call('remote', 'get-url', 'origin').decode().strip() != self.remote:
            raise Blocked('El origin de control no es el repositorio autorizado.')
        self.call('fetch', '--filter=blob:none', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
        return self.call('rev-parse', 'refs/remotes/origin/main^{commit}').decode().strip()

    def tree(self, commit):
        result, folded = {}, set()
        for row in self.call('ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
            if not row: continue
            header, name = row.split(b'\t', 1)
            mode, kind, obj = header.decode().split()
            name = name.decode('utf-8')
            safe_name(name)
            if mode != '100644' or kind != 'blob' or name.casefold() in folded:
                raise Blocked('Arbol no soportado (enlace, ejecutable o colision Windows): ' + name)
            result[name] = obj; folded.add(name.casefold())
        return result

    def blob(self, obj):
        if int(self.call('cat-file', '-s', obj)) > 32*1024**2:
            raise Blocked('Archivo remoto mayor de 32 MiB; no se trata como codigo actualizable.')
        data = self.call('cat-file', 'blob', obj)
        if oid(data) != obj:
            raise Blocked('Integridad del objeto Git no coincide: ' + obj)
        return data

    def history(self, commit):
        known = {}
        for revision in self.call('rev-list', commit).decode().splitlines():
            for name, obj in self.tree(revision).items():
                known.setdefault(name, set()).add(obj)
        return known

    def manifest(self, tree):
        if 'SHA256SUMS.txt' not in tree:
            raise Blocked('Version remota sin manifiesto SHA256.')
        hashes = {}
        for line in self.blob(tree['SHA256SUMS.txt']).decode('utf-8').splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
            if not match or match[2] in hashes:
                raise Blocked('Manifiesto SHA256 malformado/duplicado.')
            safe_name(match[2]); hashes[match[2]] = match[1]
        if set(hashes) != set(tree) - {'SHA256SUMS.txt'}:
            raise Blocked('El manifiesto no cubre exactamente el arbol publicado.')
        return hashes


def plan(repo, commit, initial=False):
    """Read-only code preflight. Git's separate object cache may be populated."""
    state_path = repo.store / 'baseline.json'
    state = read(state_path)
    tree = repo.tree(commit)
    hashes = repo.manifest(tree)
    if initial:
        if state is not None:
            raise Blocked('Ya esta vinculado. Usa ACTUALIZAR_MUSICSTUDIO.bat.')
        known = repo.history(commit)
        previous = {}
        for name, objects in known.items():
            data = read(target(repo.root, name))
            if data is not None:
                if oid(data) not in objects:
                    raise Blocked(f'Archivo local distinto: {name}. Conservarlo fuera de su ruta de codigo y revisar diferencias; no se sobrescribe.')
                previous[name] = oid(data)
    else:
        if state is None:
            raise Blocked('Falta vinculacion inicial. Ejecuta CONFIGURAR_ACTUALIZACIONES.bat.')
        baseline = json.loads(state)
        old = baseline['commit']
        if baseline.get('schema') != 1 or baseline.get('origin') != ORIGIN or not re.fullmatch(r'[0-9a-f]{40}', old):
            raise Blocked('Estado de vinculacion no reconocido. Conservarlo y solicitar revision.')
        # A rewritten main is a conflict, not a reason to discard local history.
        if repo.call('merge-base', old, commit).decode().strip() != old:
            raise Blocked('main no desciende de la version vinculada. Requiere revision.')
        previous = repo.tree(old)
        for name, obj in previous.items():
            data = read(target(repo.root, name))
            if data is None or oid(data) != obj:
                raise Blocked(f'Archivo local modificado o ausente: {name}. No se actualiza nada; revisar/restaurar solo ese codigo, conservando copia personal.')
    changes, originals = ChangePlan(), {}
    changes.observed = {}
    changes.expected = dict(tree)
    for name in sorted(set(tree) | set(previous)):
        path = target(repo.root, name)
        data = read(path)
        changes.observed[name] = sha(data)
        if name not in previous and data is not None:
            raise Blocked(f'Archivo personal ocupa una ruta nueva: {name}. Moverlo manualmente a una carpeta personal tras revisarlo.')
        obj = tree.get(name)
        if obj is None:
            after = None
        elif data is not None and oid(data) == obj:
            # SHA256 verification doesn't require downloading unchanged blobs.
            if name in hashes and sha(data) != hashes[name]:
                raise Blocked('SHA256 no coincide: ' + name)
            continue
        else:
            after = repo.blob(obj)
            if name in hashes and sha(after) != hashes[name]:
                raise Blocked('SHA256 remoto no coincide: ' + name)
        if data != after:
            changes[name], originals[name] = after, data
    new_state = (json.dumps({'schema': 1, 'origin': ORIGIN, 'commit': commit}, indent=2) + '\n').encode()
    return changes, originals, state, new_state


def transactions(store):
    local(store / 'backups')
    result = []
    for path in sorted((store / 'backups').glob('*/journal.json')):
        local(path.parent); local(path)
        result.append((path, json.loads(path.read_text())))
    return result


def no_pending(store):
    for path, journal in transactions(store):
        if journal['status'] not in ('committed', 'rolled_back'):
            raise Blocked(f'Actualizacion interrumpida: {path.parent.name}. Ejecuta RECUPERAR_ACTUALIZACION.bat; no reinstales.')


def restore(root, folder, journal, guard=lambda: None):
    """Validate all recovery material and current files before changing any."""
    store = root / META
    restore_plan = []
    for name, entry in journal['files'].items():
        path = store / 'baseline.json' if name == '@baseline' else target(root, name)
        current = read(path)
        # A crash while preparing backups cannot have changed project files.
        # Already-original files also need no backup to be restored safely.
        if sha(current) == entry['before']:
            restore_plan.append((path, current, current))
            continue
        before_file = folder / 'before' / ('baseline.json' if name == '@baseline' else name)
        local(before_file)
        before = read(before_file) if entry['before'] is not None else None
        if sha(before) != entry['before']:
            raise Blocked('Respaldo incompleto/modificado: ' + name)
        if sha(current) not in (entry['before'], entry['after']):
            raise Blocked('Cambio local posterior impide restaurar: ' + name + '. Se conserva el respaldo.')
        restore_plan.append((path, before, current))
    guard()
    journal['status'] = 'recovering'
    write_json(folder / 'journal.json', journal)
    for path, before, current in reversed(restore_plan):
        guard()
        if read(path) != current:
            raise Blocked('Cambio concurrente durante recuperacion; conservar respaldo.')
        if current != before:
            if before is None: path.unlink()
            else: atomic(path, before)
    journal['status'] = 'rolled_back'
    write_json(folder / 'journal.json', journal)


def apply(root, changes, originals, old_state, new_state, guard=lambda: None, fault=lambda: None):
    store = root / META
    guard()
    for name, checksum in getattr(changes, 'observed', {}).items():
        if sha(read(target(root, name))) != checksum:
            raise Blocked('Cambio concurrente desde la revision: ' + name)
    # Fail before backup if anything changed during review.
    for name, before in originals.items():
        if read(target(root, name)) != before:
            raise Blocked('Cambio concurrente antes de escribir: ' + name)
    if read(store / 'baseline.json') != old_state:
        raise Blocked('El estado de vinculacion cambio durante la revision.')
    needed = sum(len(b) for b in [*originals.values(), *changes.values(), old_state, new_state] if b is not None) + 64*1024**2
    if shutil.disk_usage(root).free < needed:
        raise Blocked('Disco insuficiente para staging y respaldo completo.')
    folder = store / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f-') + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    before_map = dict(originals, **{'@baseline': old_state})
    after_map = dict(changes, **{'@baseline': new_state})
    journal = {'schema': 1, 'status': 'preparing', 'files': {n: {'before': sha(b), 'after': sha(after_map[n])} for n,b in before_map.items()}}
    write_json(folder / 'journal.json', journal)
    for n, b in before_map.items():
        relative = 'baseline.json' if n == '@baseline' else n
        if b is not None: atomic(folder / 'before' / relative, b)
        if after_map[n] is not None: atomic(folder / 'after' / relative, after_map[n])
    journal['status'] = 'prepared'; write_json(folder / 'journal.json', journal)
    try:
        for n, after in after_map.items():
            guard()
            path = store / 'baseline.json' if n == '@baseline' else target(root, n)
            if read(path) != before_map[n]:
                raise Blocked('Cambio concurrente: ' + n)
            if after is None: path.unlink()
            else: atomic(path, after)
            fault()
        for n, after in after_map.items():
            path = store / 'baseline.json' if n == '@baseline' else target(root, n)
            if read(path) != after:
                raise Blocked('Verificacion posterior fallo: ' + n)
        for name, expected in getattr(changes, 'expected', {}).items():
            data = read(target(root, name))
            if data is None or oid(data) != expected:
                raise Blocked('El codigo completo cambio durante la actualizacion: ' + name)
        journal['status'] = 'committed'; write_json(folder / 'journal.json', journal)
    except BaseException:
        try: restore(root, folder, journal, guard)
        except BaseException:
            journal['status'] = 'recovery_required'; write_json(folder / 'journal.json', journal)
            print('RECUPERACION PENDIENTE. Conservar respaldo:', folder, flush=True)
        raise
    return folder


def windows_guard(root):
    """Independently recheck running processes on each write, fail closed."""
    # Constant PowerShell code. Paths/data travel as base64 JSON, not shell code.
    payload = base64.b64encode(json.dumps({'root':str(root), 'pid':os.getpid(), 'old':str(Path(os.environ['USERPROFILE']) / 'Downloads/MusicStudioAILocal-main')}).encode()).decode()
    script = r'''$ErrorActionPreference='Stop'
$d=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD'))|ConvertFrom-Json
$p=@(Get-CimInstance Win32_Process -ErrorAction Stop); $skip=@([int]$PID,[int]$d.pid); $id=[int]$d.pid
while($id -gt 0){$row=$p|Where-Object ProcessId -eq $id|Select-Object -First 1;if(-not $row){break};$id=[int]$row.ParentProcessId;if($skip -contains $id){break};$skip+=$id}
foreach($row in $p){if($skip -contains [int]$row.ProcessId){continue};foreach($r in @($d.root,$d.old)){
if(($row.ExecutablePath -and $row.ExecutablePath.StartsWith($r+'\',[StringComparison]::OrdinalIgnoreCase)) -or ($row.CommandLine -and $row.CommandLine.IndexOf($r,[StringComparison]::OrdinalIgnoreCase) -ge 0)){throw ('Proceso activo del proyecto: '+$row.ProcessId+' '+$row.Name)}}}
'''.replace('PAYLOAD', payload)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    p = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise Blocked('Comprobacion de procesos fallo/bloqueo: ' + p.stderr.decode('utf-8','replace')[-1500:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--mode', choices=['Setup','Update','Recover'], required=True)
    parser.add_argument('--git', required=True)
    args = parser.parse_args()
    root = Path(args.root).absolute()
    if sys.platform != 'win32' or sys.version_info[:3] != (3,12,12):
        raise Blocked('Se requiere el Python privado Windows 3.12.12 existente. No se instala.')
    expected = Path(os.environ['USERPROFILE']) / 'OneDrive/Documentos/MusicStudioAILocal/MusicStudioAILocal-main'
    if os.path.normcase(str(root)) != os.path.normcase(str(expected)):
        raise Blocked('Solo se autoriza la carpeta definitiva de OneDrive Documentos.')
    if os.path.normcase(sys.executable) != os.path.normcase(str(root / RUNTIME)):
        raise Blocked('El actualizador debe usar el runtime privado base, no .venv ni Python global.')
    local(root)
    owner_path = root / '.state/owner.json'
    local(owner_path)
    if not owner_path.is_file():
        raise Blocked('Falta el marcador de la instalacion existente. No se crea ni modifica .state.')
    owner = json.loads(owner_path.read_text(encoding='utf-8-sig'))
    allowed_owners = [root, Path(os.environ['USERPROFILE']) / 'Downloads/MusicStudioAILocal-main']
    if owner.get('project') != 'MusicStudioAILocal' or not any(os.path.normcase(owner.get('root', '')) == os.path.normcase(str(p)) for p in allowed_owners):
        raise Blocked('El marcador existente no corresponde a este proyecto/traslado. No se modifica.')
    store = root / META
    local(store)
    guard = lambda: windows_guard(root)
    guard()
    if args.mode == 'Setup' and any(j['status'] not in ('committed', 'rolled_back') for _, j in transactions(store)):
        print('Se detecto una actualizacion interrumpida; recuperar antes de vincular.')
        args.mode = 'Recover'
    if args.mode == 'Recover':
        records = transactions(store)
        if not records: raise Blocked('No hay respaldos de actualizacion.')
        pending = [(p,j) for p,j in records if j['status'] not in ('committed','rolled_back')]
        if pending:
            if len(pending) != 1: raise Blocked('Varios diarios pendientes. Conservar respaldos y solicitar revision.')
            path, journal = pending[0]
        else:
            current = sha(read(store / 'baseline.json'))
            matches = [(p,j) for p,j in records if j['status'] == 'committed' and j['files'].get('@baseline',{}).get('after') == current]
            if len(matches) != 1: raise Blocked('No hay un unico respaldo confirmado de la version actual. No se elige por fecha.')
            path, journal = matches[0]
        print('Recuperar:', path.parent.name, '\nArchivos:', ', '.join(journal['files']))
        if input('Escribe RECUPERAR para restaurar solo estos archivos: ').strip() != 'RECUPERAR': return
        restore(root, path.parent, journal, guard)
        print('Restauracion terminada. Codigo y vinculacion regresaron al estado anterior; dependencias intactas.')
        return
    no_pending(store)
    repo = Repository(root, args.git)
    commit = repo.connect()
    print('Repositorio:', ORIGIN, '\nRama: main\nCommit:', commit)
    changes, originals, old_state, new_state = plan(repo, commit, args.mode == 'Setup')
    if not changes and old_state == new_state:
        print('Ya tienes la version publicada. No se modifico ningun archivo del proyecto.'); return
    print('Archivos a actualizar:')
    for n, after in changes.items(): print(('ELIMINAR codigo retirado' if after is None else 'ANADIR' if originals[n] is None else 'REEMPLAZAR'), n)
    print('Se registra la version en .musicstudio-update/baseline.json. No se ejecuta codigo nuevo ni reparacion/instalacion.')
    if input('Escribe ACTUALIZAR para aplicar con respaldo: ').strip() != 'ACTUALIZAR':
        print('Cancelado. Solo puede quedar la cache Git de control.'); return
    backup = apply(root, changes, originals, old_state, new_state, guard)
    print('Actualizacion terminada. Respaldo:', backup)
    print('Si el traslado sigue pendiente, ejecuta 08_REPARAR_TRASLADO.bat. No se ejecuto automaticamente.')


if __name__ == '__main__':
    try: main()
    except (Blocked, OSError, ValueError, KeyError) as exc:
        print('ACTUALIZACION DETENIDA:', exc, flush=True)
        raise SystemExit(1)
