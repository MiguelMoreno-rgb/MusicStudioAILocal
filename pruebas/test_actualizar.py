"""Functional updater tests with real local Git remotes; no Windows execution."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('musicstudio_updater', Path(__file__).resolve().parents[1] / 'instalacion/actualizar.py')
u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.upstream = self.base / 'upstream'; self.upstream.mkdir()
        self.root = self.base / 'existing'; self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid'); self.git('config', 'user.name', 'Test')
        self.git('config', 'uploadpack.allowFilter', 'true')
        self.publish({'README.md': b'original\n', 'musicstudio/relocate.py': b'old known numba repair\n'})
        self.old = self.git('rev-parse', 'HEAD').decode().strip()
        for p in self.upstream.rglob('*'):
            if p.is_file() and '.git' not in p.parts:
                dest = self.root / p.relative_to(self.upstream); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(p.read_bytes())
        self.protected = {}
        for name in ('.venv/Lib/site-packages/preserved.py', '.runtime/private.exe', '.cache/object', '.state/owner.json', 'checkpoints/model', 'resultados/personal.wav', 'personal.txt'):
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'personal-unchanged'); self.protected[name] = p.read_bytes()
        self.repo = u.Repository(self.root, remote=self.upstream.as_uri())

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.upstream), *args], stderr=subprocess.PIPE)

    def publish(self, updates):
        for name, b in updates.items():
            path = self.upstream / name
            if b is None: path.unlink()
            else: path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b)
        tracked = [p for p in self.upstream.rglob('*') if p.is_file() and '.git' not in p.parts and p.name != 'SHA256SUMS.txt']
        manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.upstream).as_posix()}\n' for p in sorted(tracked))
        (self.upstream / 'SHA256SUMS.txt').write_text(manifest)
        self.git('add', '.'); self.git('commit', '-qm', 'test publication')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def assert_protected(self):
        for name, b in self.protected.items(): self.assertEqual((self.root / name).read_bytes(), b, name)

    def setup(self):
        commit = self.repo.connect()
        changes, before, old, new = u.plan(self.repo, commit, initial=True)
        u.apply(self.root, changes, before, old, new)
        return commit

    def test_zip_adoption_upgrades_known_old_numba_preserves_packages(self):
        new = self.publish({'musicstudio/relocate.py': b'numba correction\n', 'ACTUALIZAR_MUSICSTUDIO.bat': b'@echo update\r\n'})
        commit = self.repo.connect()
        changes, before, old, state = u.plan(self.repo, commit, initial=True)
        self.assertEqual((self.root / 'musicstudio/relocate.py').read_bytes(), b'old known numba repair\n')
        folder = u.apply(self.root, changes, before, old, state)
        self.assertEqual(json.loads((self.repo.store / 'baseline.json').read_text())['commit'], new)
        self.assertEqual((folder / 'before/musicstudio/relocate.py').read_bytes(), b'old known numba repair\n')
        self.assertEqual((self.root / 'musicstudio/relocate.py').read_bytes(), b'numba correction\n')
        self.assert_protected()

    def test_adoption_local_modifications_block_without_code_writes(self):
        (self.root / 'README.md').write_bytes(b'personal edit')
        self.publish({'README.md': b'new official'})
        commit = self.repo.connect()
        with self.assertRaisesRegex(u.Blocked, 'local distinto'):
            u.plan(self.repo, commit, initial=True)
        self.assertEqual((self.root / 'README.md').read_bytes(), b'personal edit')
        self.assertFalse((self.repo.store / 'backups').exists()); self.assert_protected()

    def test_normal_update_downloads_only_changed_blobs(self):
        self.setup()
        self.publish({'README.md': b'updated'})
        commit = self.repo.connect(); fetched = []; original = self.repo.blob
        def record(obj): fetched.append(obj); return original(obj)
        self.repo.blob = record
        changes, before, old, new = u.plan(self.repo, commit)
        self.assertEqual(set(changes), {'README.md', 'SHA256SUMS.txt'})
        self.assertEqual(len(fetched), 3)  # Manifest read and staged, plus README.
        u.apply(self.root, changes, before, old, new)
        self.assertEqual((self.root / 'README.md').read_bytes(), b'updated'); self.assert_protected()

    def test_any_tracked_edit_blocks_even_if_remote_did_not_change_it(self):
        self.setup(); (self.root / 'musicstudio/relocate.py').write_bytes(b'user changed')
        self.publish({'README.md': b'updated'})
        with self.assertRaisesRegex(u.Blocked, 'local modificado'):
            u.plan(self.repo, self.repo.connect())
        self.assertEqual((self.root / 'README.md').read_bytes(), b'original\n'); self.assert_protected()

    def test_new_upstream_path_collision_is_not_overwritten(self):
        self.setup(); (self.root / 'nuevo.txt').write_bytes(b'personal')
        self.publish({'nuevo.txt': b'official'})
        with self.assertRaisesRegex(u.Blocked, 'personal ocupa'):
            u.plan(self.repo, self.repo.connect())
        self.assertEqual((self.root / 'nuevo.txt').read_bytes(), b'personal')

    def test_rollback_after_partial_write_restores_code_and_baseline(self):
        self.setup(); previous = (self.repo.store / 'baseline.json').read_bytes()
        self.publish({'README.md': b'new', 'nuevo.txt': b'new'})
        changes, before, old, new = u.plan(self.repo, self.repo.connect())
        def fail(): raise RuntimeError('simulated write failure')
        with self.assertRaisesRegex(RuntimeError, 'simulated'):
            u.apply(self.root, changes, before, old, new, fault=fail)
        self.assertEqual((self.root / 'README.md').read_bytes(), b'original\n')
        self.assertEqual((self.repo.store / 'baseline.json').read_bytes(), previous)
        self.assertFalse((self.root / 'nuevo.txt').exists()); u.no_pending(self.repo.store); self.assert_protected()

    def test_manual_recovery_restores_deleted_file_and_previous_version(self):
        self.setup(); self.publish({'musicstudio/relocate.py': None})
        changes, before, old, new = u.plan(self.repo, self.repo.connect())
        folder = u.apply(self.root, changes, before, old, new)
        self.assertFalse((self.root / 'musicstudio/relocate.py').exists())
        journal = json.loads((folder / 'journal.json').read_text())
        u.restore(self.root, folder, journal)
        self.assertEqual((self.root / 'musicstudio/relocate.py').read_bytes(), b'old known numba repair\n'); self.assert_protected()

    def test_recovery_refuses_to_overwrite_edit_after_update(self):
        self.setup(); self.publish({'README.md': b'updated'})
        c,b,o,n = u.plan(self.repo,self.repo.connect()); folder=u.apply(self.root,c,b,o,n)
        (self.root / 'README.md').write_bytes(b'new personal edit')
        with self.assertRaisesRegex(u.Blocked, 'Cambio local posterior'):
            u.restore(self.root,folder,json.loads((folder/'journal.json').read_text()))
        self.assertEqual((self.root/'README.md').read_bytes(),b'new personal edit')

    def test_interrupted_recovery_remains_pending_and_can_resume(self):
        self.setup(); self.publish({'README.md':b'updated', 'nuevo.txt':b'new'})
        c,b,o,n=u.plan(self.repo,self.repo.connect());folder=u.apply(self.root,c,b,o,n)
        journal=json.loads((folder/'journal.json').read_text());calls=[]
        def fail():
            calls.append(1)
            if len(calls)==3:raise RuntimeError('interrupted recovery')
        with self.assertRaisesRegex(RuntimeError,'interrupted recovery'):u.restore(self.root,folder,journal,fail)
        saved=json.loads((folder/'journal.json').read_text())
        self.assertEqual(saved['status'],'recovering')
        with self.assertRaisesRegex(u.Blocked,'interrumpida'):u.no_pending(self.repo.store)
        u.restore(self.root,folder,saved);u.no_pending(self.repo.store)
        self.assertEqual((self.root/'README.md').read_bytes(),b'original\n')
        self.assertFalse((self.root/'nuevo.txt').exists());self.assert_protected()

    def test_corrupt_backup_cannot_be_used(self):
        self.setup(); self.publish({'README.md':b'updated'})
        c,b,o,n=u.plan(self.repo,self.repo.connect());folder=u.apply(self.root,c,b,o,n)
        (folder/'before/README.md').write_bytes(b'corrupt')
        with self.assertRaisesRegex(u.Blocked,'Respaldo incompleto'):
            u.restore(self.root,folder,json.loads((folder/'journal.json').read_text()))

    def test_guard_failure_and_concurrent_edit_stop_before_backup(self):
        self.setup(); self.publish({'README.md':b'updated'})
        c,b,o,n=u.plan(self.repo,self.repo.connect()); count=len(u.transactions(self.repo.store))
        def blocked(): raise u.Blocked('project running')
        with self.assertRaisesRegex(u.Blocked,'project running'): u.apply(self.root,c,b,o,n,guard=blocked)
        (self.root/'README.md').write_bytes(b'concurrent')
        with self.assertRaisesRegex(u.Blocked,'Cambio concurrente'): u.apply(self.root,c,b,o,n)
        self.assertEqual(len(u.transactions(self.repo.store)),count); self.assert_protected()

    def test_pending_journal_blocks_update_and_preparing_crash_is_recoverable(self):
        self.repo.store.mkdir(parents=True)
        folder=self.repo.store/'backups/1';folder.mkdir(parents=True)
        journal={'status':'preparing','files':{'README.md':{'before':u.sha(b'original\n'),'after':u.sha(b'updated')}}}
        u.write_json(folder/'journal.json',journal)
        with self.assertRaisesRegex(u.Blocked,'interrumpida'): u.no_pending(self.repo.store)
        u.restore(self.root,folder,journal);u.no_pending(self.repo.store);self.assert_protected()

    def test_remote_manifest_tampering_is_rejected(self):
        self.setup(); self.publish({'README.md':b'new'})
        (self.upstream/'README.md').write_bytes(b'tampered not in manifest');self.git('add','.');self.git('commit','-qm','bad manifest')
        with self.assertRaisesRegex(u.Blocked,'SHA256 remoto'):
            u.plan(self.repo,self.repo.connect())

    def test_remote_cannot_target_runtime_private_paths_or_links(self):
        for name in ('.venv/Scripts/python.exe','.state/owner.json','resultados/a.txt','../x.txt','C:/x.txt','config/entrada/letra.txt','instalacion/CON.txt'):
            with self.subTest(name=name),self.assertRaises(u.Blocked):u.safe_name(name)
        self.setup(); (self.root/'README.md').unlink();(self.root/'README.md').symlink_to(self.root/'personal.txt')
        with self.assertRaisesRegex(u.Blocked,'Enlace'):
            u.plan(self.repo,self.repo.connect())
        self.assert_protected()

    def test_rewritten_remote_history_is_rejected(self):
        self.setup(); self.git('checkout','--orphan','replacement'); self.git('commit','--allow-empty','-qm','unrelated')
        unrelated=self.git('rev-parse','HEAD').decode().strip()
        # Test validation independent of non-fast-forward fetch refusing first.
        self.repo.call('fetch','--filter=blob:none','origin','replacement')
        with self.assertRaises(u.Blocked):u.plan(self.repo,unrelated)

    def test_no_update_is_a_noop(self):
        self.setup(); c,b,o,n=u.plan(self.repo,self.repo.connect())
        self.assertEqual(c,{}); self.assertEqual(o,n); self.assert_protected()

    def test_guard_becomes_blocked_mid_update_then_recovery_succeeds(self):
        self.setup(); self.publish({'README.md':b'updated', 'nuevo.txt':b'new'})
        c,b,o,n=u.plan(self.repo,self.repo.connect()); calls=[]
        def guard():
            calls.append(1)
            if len(calls)>2: raise u.Blocked('process started')
        with self.assertRaisesRegex(u.Blocked,'process started'):u.apply(self.root,c,b,o,n,guard=guard)
        with self.assertRaisesRegex(u.Blocked,'interrumpida'):u.no_pending(self.repo.store)
        path,journal=u.transactions(self.repo.store)[-1]
        u.restore(self.root,path.parent,journal)
        u.no_pending(self.repo.store)
        self.assertEqual((self.root/'README.md').read_bytes(),b'original\n');self.assert_protected()

    def test_concurrent_edit_of_unchanged_code_is_also_detected(self):
        self.setup();self.publish({'README.md':b'updated'})
        c,b,o,n=u.plan(self.repo,self.repo.connect())
        (self.root/'musicstudio/relocate.py').write_bytes(b'concurrent edit')
        with self.assertRaisesRegex(u.Blocked,'Cambio concurrente desde la revision'):u.apply(self.root,c,b,o,n)
        self.assertEqual((self.root/'README.md').read_bytes(),b'original\n')

    def test_remote_protected_payload_and_changed_origin_are_rejected(self):
        self.setup();self.publish({'.state/personal.json':b'forbidden'})
        with self.assertRaisesRegex(u.Blocked,'Ruta remota no permitida'):u.plan(self.repo,self.repo.connect())
        self.repo.call('remote','set-url','origin','https://example.invalid/other.git')
        with self.assertRaisesRegex(u.Blocked,'origin'):self.repo.connect()

class PublisherPinsTests(unittest.TestCase):
    def test_bootstrap_pins_exact_current_updater_and_command_pins_bootstrap(self):
        root=Path(__file__).resolve().parents[1]
        ps=(root/'instalacion/ConfigurarActualizaciones.ps1').read_bytes()
        pinned=re.search(rb"\$sha = '([0-9a-f]{64})'",ps)[1].decode()
        self.assertEqual(pinned,u.sha((root/'instalacion/actualizar.py').read_bytes()))
        command=(root/'CONFIGURAR_DESDE_POWERSHELL.txt').read_text()
        self.assertIn(u.sha(ps),command)
        self.assertIn(command.strip(),(root/'ACTUALIZACIONES.md').read_text())

    def test_every_published_manifest_hash_matches_local_file(self):
        root=Path(__file__).resolve().parents[1]
        for line in (root/'SHA256SUMS.txt').read_text().splitlines():
            h,name=line.split('  ',1)
            self.assertEqual(h,u.sha((root/name).read_bytes()),name)

if __name__=='__main__':unittest.main()
