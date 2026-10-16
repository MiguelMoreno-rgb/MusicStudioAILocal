"""Relocation write-plan, uv trailer and rollback tests; no Windows execution."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest

from musicstudio import relocate as repair


OLD = r'C:\Users\Usuario\Downloads\MusicStudioAILocal-main'
NEW = r'C:\Users\Usuario\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
PINS = json.loads((Path(__file__).resolve().parents[1] / 'instalacion/reparacion.lock.json').read_text())
# Exact body from the SHA256-verified numba 0.63.1 cp312 Windows wheel.
NUMBA_BODY = (b'# -*- coding: UTF-8 -*-\r\n'
              b'from __future__ import print_function, division, absolute_import\r\n\r\n'
              b'from numba.misc.numba_entry import main\r\n\r\n'
              b'if __name__ == "__main__":\r\n    main()\r\n')


class RelocationTests(unittest.TestCase):
    def numba_fixture(self, root, old):
        # Match uv 0.9.6's handling of the wheel CRLF shebang: extra LF remains.
        data = b'#!' + str(old / '.venv/Scripts/python.exe').replace('/', '\\').encode() + b'\r\n\n' + NUMBA_BODY
        (root / '.venv/Scripts/numba').write_bytes(data)
        record = next((root / '.venv/Lib/site-packages').glob('numba-*.dist-info/RECORD'))
        hashed = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        with record.open('a', newline='') as f:
            csv.writer(f).writerow(['../../Scripts/numba', hashed, str(len(data))])
        return data, record

    def test_numba_uv_wheel_shebang_preserves_exact_body(self):
        for body in (NUMBA_BODY, b'\n' + NUMBA_BODY):
            with self.subTest(extra_lf=body.startswith(b'\n')):
                data = b'#!' + OLD.encode() + b'\\.venv\\Scripts\\python.exe\r\n' + body
                out = repair.rewrite_wheel_script(data, OLD, NEW, PINS['wheel_scripts']['numba'])
                self.assertEqual(out, b'#!' + NEW.encode() + b'\\.venv\\Scripts\\python.exe\r\n' + body)
                self.assertEqual(repair.rewrite_wheel_script(out, OLD, NEW, PINS['wheel_scripts']['numba']), out)

    def test_numba_unknown_body_or_interpreter_stops(self):
        good = b'#!' + OLD.encode() + b'\\.venv\\Scripts\\python.exe\r\n' + NUMBA_BODY
        for data in (good + b'# changed\r\n', good.replace(b'numba.misc', b'other.misc'),
                     good.replace(b'python.exe', b'python.exe --flag'),
                     b'#!C:\\OtherApp\\python.exe\r\n' + NUMBA_BODY,
                     good.replace(b'\r\n', b'\n')):
            with self.subTest(data=data[:80]), self.assertRaises(repair.StopRepair):
                repair.rewrite_wheel_script(data, OLD, NEW, PINS['wheel_scripts']['numba'])

    def test_numba_complete_preflight_transaction_and_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root, old = self.installed_fixture(temp)
            data, record = self.numba_fixture(root, old)
            pins = dict(PINS, required_code={}, launcher_prefixes=[])
            plan, inventory, versions = repair.preflight(root, old, pins)
            name = '.venv/Scripts/numba'
            record_name = record.relative_to(root).as_posix()
            self.assertEqual((root / name).read_bytes(), data)  # Still read-only.
            self.assertIn(record_name, plan)
            self.assertEqual(plan[name].partition(b'\r\n')[2], b'\n' + NUMBA_BODY)
            repair.apply_transaction(root, plan, inventory, lambda: None)
            self.assertEqual((root / name).read_bytes(), plan[name])
            distributions, after_versions = repair.installed_packages(root, repair.expected_packages(root / 'instalacion/requirements-windows.lock.txt'))
            self.assertEqual(versions, after_versions)
            self.assertEqual(repair.record_changes(root, distributions, {}, repair.snapshot(root)), {})
            backups = list((root / '.state/repair-backups').glob('*/originals/.venv/Scripts/numba'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), data)

    def test_numba_missing_owner_or_corrupt_record_stops_before_backup(self):
        for fault in ('unowned', 'wrong_hash', 'wrong_size'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                root, old = self.installed_fixture(temp)
                data, record = self.numba_fixture(root, old)
                text = record.read_text()
                if fault == 'unowned':
                    text = '\n'.join(line for line in text.splitlines() if '../../Scripts/numba' not in line) + '\n'
                elif fault == 'wrong_hash':
                    text = re.sub(r'(../../Scripts/numba,sha256=)[^,]+', r'\g<1>invalid', text)
                else:
                    text = text.replace(',' + str(len(data)), ',999999')
                record.write_text(text)
                with self.assertRaises(repair.StopRepair):
                    repair.preflight(root, old, dict(PINS, required_code={}, launcher_prefixes=[]))
                self.assertEqual((root / '.venv/Scripts/numba').read_bytes(), data)
                self.assertFalse((root / '.state/repair-backups').exists())

    def test_numba_rollback_restores_script_and_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root, old = self.installed_fixture(temp)
            data, record = self.numba_fixture(root, old)
            original_record = record.read_bytes()
            plan, inventory, _ = repair.preflight(root, old, dict(PINS, required_code={}, launcher_prefixes=[]))
            def fail():
                raise RuntimeError('postcheck failure')
            with self.assertRaises(RuntimeError):
                repair.apply_transaction(root, plan, inventory, fail)
            self.assertEqual((root / '.venv/Scripts/numba').read_bytes(), data)
            self.assertEqual(record.read_bytes(), original_record)

    def test_all_other_unknown_scripts_are_reported_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, old = self.installed_fixture(temp)
            self.numba_fixture(root, old)
            for name in ('unknown-a', 'unknown-z'):
                (root / '.venv/Scripts' / name).write_text(str(old))
            with self.assertRaisesRegex(repair.StopRepair, 'unknown-a, unknown-z'):
                repair.preflight(root, old, dict(PINS, required_code={}, launcher_prefixes=[]))
            self.assertFalse((root / '.state/repair-backups').exists())

    def installed_fixture(self, temp):
        root = Path(temp) / 'new'
        old = Path(temp) / 'old'
        for name in ['.state', 'musicstudio', 'instalacion', '.venv/Scripts', '.venv/Lib/site-packages', str(repair.RUNTIME)]:
            (root / name).mkdir(parents=True, exist_ok=True)
        lock = (Path(__file__).resolve().parents[1] / 'instalacion/requirements-windows.lock.txt').read_bytes()
        (root / 'instalacion/requirements-windows.lock.txt').write_bytes(lock)
        (root / '.state/owner.json').write_text(json.dumps({'project': 'MusicStudioAILocal', 'root': str(old)}))
        (root / '.venv/pyvenv.cfg').write_text('home = ' + str(old / repair.RUNTIME) + '\ninclude-system-site-packages = false\n')
        (root / '.venv/Scripts/python.exe').write_bytes(b'fixture of non-uv redirector; never executed')
        (root / repair.RUNTIME / 'python.exe').write_bytes(b'private runtime fixture; never executed')
        site = root / '.venv/Lib/site-packages'
        for name, version in repair.expected_packages(root / 'instalacion/requirements-windows.lock.txt').items():
            meta = site / f'{name}-{version}.dist-info'
            meta.mkdir()
            metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'.encode()
            (meta / 'METADATA').write_bytes(metadata)
            hashed = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(metadata).digest()).rstrip(b'=').decode()
            buf = io.StringIO()
            csv.writer(buf).writerows([[f'{meta.name}/METADATA', hashed, str(len(metadata))], [f'{meta.name}/RECORD', '', '']])
            (meta / 'RECORD').write_text(buf.getvalue())
        return root, old

    def test_full_preflight_of_57_distributions_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root, old = self.installed_fixture(temp)
            before = {p.relative_to(root).as_posix(): repair.file_digest(p) for p in root.rglob('*') if p.is_file()}
            plan, _, versions = repair.preflight(root, old, {'required_code': {}, 'launcher_prefixes': []})
            after = {p.relative_to(root).as_posix(): repair.file_digest(p) for p in root.rglob('*') if p.is_file()}
            self.assertEqual(len(versions), 57)
            self.assertIn('.venv/pyvenv.cfg', plan)
            self.assertIn('.state/owner.json', plan)
            self.assertEqual(before, after)
            self.assertFalse((root / '.state/repair-backups').exists())

    def test_incomplete_package_stops_full_preflight_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, old = self.installed_fixture(temp)
            next((root / '.venv/Lib/site-packages').glob('*/METADATA')).unlink()
            with self.assertRaisesRegex(repair.StopRepair, 'METADATA'):
                repair.preflight(root, old, {'required_code': {}, 'launcher_prefixes': []})
            self.assertFalse((root / '.state/repair-backups').exists())

    def launcher(self, magic, target=None):
        header = b'MZ' + b'offical-format-test-fixture' * 3
        payload = header + (b'PK\x03\x04unchanged-python-entrypoint' if magic == b'UVSC' else b'')
        path = (target or OLD + r'\.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe').encode()
        data = payload + path + struct.pack('<I', len(path)) + magic
        pin = [{'bytes': len(header), 'sha256': repair.digest(header)}]
        return data, pin, payload

    def test_uvpy_trailer_updates_only_path_and_length(self):
        data, pins, payload = self.launcher(b'UVPY')
        out = repair.rewrite_launcher(data, OLD, NEW, pins)
        length = struct.unpack('<I', out[-8:-4])[0]
        self.assertEqual(out[:-8-length], payload)
        self.assertEqual(out[-4:], b'UVPY')
        self.assertEqual(out[-8-length:-8].decode(), NEW + r'\.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe')

    def test_uvsc_script_payload_is_preserved(self):
        data, pins, payload = self.launcher(b'UVSC', OLD + r'\.venv\Scripts\python.exe')
        out = repair.rewrite_launcher(data, OLD, NEW, pins)
        length = struct.unpack('<I', out[-8:-4])[0]
        self.assertEqual(out[:-8-length], payload)
        self.assertEqual(out[-8-length:-8].decode(), NEW + r'\.venv\Scripts\python.exe')

    def test_unknown_pe_and_malformed_footer_stop(self):
        data, pins, _ = self.launcher(b'UVPY')
        with self.assertRaises(repair.StopRepair):
            repair.rewrite_launcher(b'XX' + data[2:], OLD, NEW, pins)
        with self.assertRaises(repair.StopRepair):
            repair.rewrite_launcher(data[:-8] + struct.pack('<I', 999999) + b'UVPY', OLD, NEW, pins)
        with self.assertRaises(repair.StopRepair):
            repair.rewrite_launcher(data, OLD, NEW, [{'bytes': pins[0]['bytes'], 'sha256': '0' * 64}])

    def test_unrelated_launcher_is_not_retargeted(self):
        data, pins, _ = self.launcher(b'UVPY', r'C:\OtherApp\python.exe')
        with self.assertRaises(repair.StopRepair):
            repair.rewrite_launcher(data, OLD, NEW, pins)
        with self.assertRaises(repair.StopRepair):
            repair.replace_root(OLD + '-other\\python.exe', OLD, NEW)

    def test_cfg_updates_known_fields_and_keeps_rest(self):
        home = OLD + r'\.runtime\python\cpython-3.12.12-windows-x86_64-none'
        data = ('home = ' + home + '\r\nbase-executable = ' + home + '\\python.exe\r\ninclude-system-site-packages = false\r\nuv = 0.9.6\r\n').encode()
        out = repair.read_cfg(data, OLD, NEW)
        self.assertNotIn(OLD.encode(), out)
        self.assertIn(NEW.encode(), out)
        self.assertIn(b'uv = 0.9.6\r\n', out)
        self.assertEqual(repair.read_cfg(out, OLD, NEW), out)

    def test_cfg_global_or_foreign_python_stops(self):
        with self.assertRaises(repair.StopRepair):
            repair.read_cfg(b'home = C:\\Python312\r\n', OLD, NEW)
        data = ('home = ' + OLD + r'\.runtime\python\cpython-3.12.12-windows-x86_64-none' + '\ninclude-system-site-packages = true\n').encode()
        with self.assertRaises(repair.StopRepair):
            repair.read_cfg(data, OLD, NEW)

    def test_activation_rewrites_path_without_changing_commands(self):
        data = ('@set "VIRTUAL_ENV=' + OLD + '\\.venv"\r\n@echo unchanged\r\n').encode()
        out = repair.rewrite_activation(data, OLD, NEW)
        self.assertIn(NEW.encode(), out)
        self.assertIn(b'@echo unchanged\r\n', out)

    def test_record_checks_payload_and_updates_only_changed_launcher_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / '.venv/Lib/site-packages'
            meta = site / 'example-1.0.dist-info'
            meta.mkdir(parents=True)
            script = root / '.venv/Scripts/example.exe'
            script.parent.mkdir(parents=True)
            script.write_bytes(b'old-launcher')
            (site / 'example.py').write_bytes(b'unchanged-package')
            rows = []
            for name, data in [('../../Scripts/example.exe', script.read_bytes()), ('example.py', b'unchanged-package')]:
                rows.append([name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data))])
            rows.append(['example-1.0.dist-info/RECORD', '', ''])
            record = meta / 'RECORD'
            buf = io.StringIO(); csv.writer(buf).writerows(rows); record.write_text(buf.getvalue())
            class Dist:
                _path = meta
                metadata = {'Name': 'example'}
            plan = {'.venv/Scripts/example.exe': b'new-launcher-longer'}
            updates = repair.record_changes(root, [Dist()], plan, repair.snapshot(root))
            self.assertEqual(script.read_bytes(), b'old-launcher')
            self.assertEqual((site / 'example.py').read_bytes(), b'unchanged-package')
            new_rows = list(csv.reader(io.StringIO(next(iter(updates.values())).decode())))
            self.assertEqual(new_rows[1], rows[1])
            self.assertEqual(new_rows[0][2], str(len(plan['.venv/Scripts/example.exe'])))
            script.write_bytes(b'corrupt')
            with self.assertRaises(repair.StopRepair):
                repair.record_changes(root, [Dist()], plan, repair.snapshot(root))

    def fixture(self, temp):
        root = Path(temp)
        (root / '.venv').mkdir()
        (root / '.state').mkdir()
        (root / '.venv/pyvenv.cfg').write_bytes(b'old config')
        (root / '.venv/package.py').write_bytes(b'protected package')
        (root / '.state/owner.json').write_bytes(b'old owner')
        return root

    def test_transaction_rolls_back_targets_on_failed_cuda_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            plan = {'.venv/pyvenv.cfg': b'new config', '.state/owner.json': b'new owner'}
            def failure():
                raise repair.StopRepair('CUDA probe failed')
            with self.assertRaises(repair.StopRepair):
                repair.apply_transaction(root, plan, repair.snapshot(root), failure)
            self.assertEqual((root / '.venv/pyvenv.cfg').read_bytes(), b'old config')
            self.assertEqual((root / '.state/owner.json').read_bytes(), b'old owner')
            self.assertEqual((root / '.venv/package.py').read_bytes(), b'protected package')
            journal = next((root / '.state/repair-backups').glob('*/journal.json'))
            self.assertEqual(json.loads(journal.read_text())['status'], 'rolled_back')

    def test_prewrite_concurrent_change_stops_without_even_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            before = repair.snapshot(root)
            (root / '.venv/package.py').write_bytes(b'changed by sync')
            with self.assertRaises(repair.StopRepair):
                repair.apply_transaction(root, {'.venv/pyvenv.cfg': b'new'}, before, lambda: None)
            self.assertFalse((root / '.state/repair-backups').exists())
            self.assertEqual((root / '.venv/pyvenv.cfg').read_bytes(), b'old config')

    def test_owner_change_since_preview_stops_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            plan = repair.RepairPlan({'.state/owner.json': b'new owner'})
            plan.originals = {'.state/owner.json': b'old owner'}
            (root / '.state/owner.json').write_bytes(b'other owner')
            with self.assertRaises(repair.StopRepair):
                repair.apply_transaction(root, plan, repair.snapshot(root), lambda: None)
            self.assertFalse((root / '.state/repair-backups').exists())

    def test_snapshot_refuses_a_link_to_external_packages(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as external:
            root = Path(temp)
            target = Path(external)
            (target / 'package.py').write_bytes(b'other project')
            try:
                (root / '.venv').symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest('No se puede crear un enlace de prueba en este sistema.')
            with self.assertRaises(repair.StopRepair):
                repair.snapshot(root)
            self.assertEqual((target / 'package.py').read_bytes(), b'other project')

    def test_success_preserves_packages_and_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            (root / '.runtime').mkdir(); (root / '.runtime/python.exe').write_bytes(b'do not replace')
            backup = repair.apply_transaction(root, {'.venv/pyvenv.cfg': b'new config'}, repair.snapshot(root), lambda: None)
            self.assertEqual((root / '.venv/package.py').read_bytes(), b'protected package')
            self.assertEqual((root / '.runtime/python.exe').read_bytes(), b'do not replace')
            self.assertEqual(json.loads((backup / 'journal.json').read_text())['status'], 'committed')

    def test_rollback_does_not_overwrite_a_third_party_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            def interference():
                (root / '.state/owner.json').write_bytes(b'external change')
                raise repair.StopRepair('Concurrent change')
            plan = {'.venv/pyvenv.cfg': b'new config', '.state/owner.json': b'new owner'}
            with self.assertRaises(repair.StopRepair):
                repair.apply_transaction(root, plan, repair.snapshot(root), interference)
            self.assertEqual((root / '.state/owner.json').read_bytes(), b'external change')
            self.assertEqual((root / '.venv/pyvenv.cfg').read_bytes(), b'old config')
            journal = next((root / '.state/repair-backups').glob('*/journal.json'))
            self.assertEqual(json.loads(journal.read_text())['status'], 'rollback_failed')


if __name__ == '__main__':
    unittest.main()
