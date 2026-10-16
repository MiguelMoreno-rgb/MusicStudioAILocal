"""Windows sharing faults simulated on every OS; native handle tests on Windows.

Only telemetry/tiny files: no generation, musical weights, or CUDA required.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from musicstudio import memory_trace as mt


def denied():
    exc = PermissionError(13, 'Acceso denegado simulado')
    exc.winerror = 5
    return exc


def records(run, name='memoria-eventos.jsonl'):
    output=[]
    for line in (Path(run)/name).read_text().splitlines():
        try: output.append(json.loads(line))
        except ValueError: pass
    return output


class MemoryIOTests(unittest.TestCase):
    def trace(self, run):
        return mt.MemoryTrace(run, lambda:{'system_available_bytes': 6*1024**3, 'process_rss_bytes':123})

    def test_temporary_winerror5_retries_and_remains_visible(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);original=Path.replace;attempts=[]
            def replace(path,target):
                attempts.append(path)
                if len(attempts)<3:raise denied()
                # Writer's temporary handle has been closed before replacement.
                with path.open('rb') as f:self.assertTrue(json.loads(f.read()))
                return original(path,target)
            with patch.object(Path,'replace',replace),patch.object(mt.time,'sleep'),patch('sys.stderr'):
                row=trace.emit('transfer.tensor','begin',name='decoder.layers.3.self_attn.k_proj.weight')
            self.assertEqual(len(attempts),3)
            self.assertEqual(row['telemetry']['io_errors'],2)
            self.assertEqual(mt.last_event(run)['sequence'],1)
            errors=records(run,'memoria-telemetria-errores.jsonl')
            self.assertEqual(len(errors),2);self.assertEqual(errors[-1]['last_error']['winerror'],5)
            self.assertFalse(list(Path(run).glob('.memoria-ultima-etapa-*.tmp')))

    def test_persistently_locked_marker_preserves_history_and_generation_exception(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            original=(Path(run)/'memoria-ultima-etapa.json').read_bytes()
            failure=ValueError('real model error')
            with patch.object(Path,'replace',side_effect=denied()),patch.object(mt.time,'sleep'),patch('sys.stderr'):
                with self.assertRaises(ValueError) as caught:
                    with trace.span('transfer.model'):
                        trace.emit('transfer.tensor','begin',name='decoder.layers.3.self_attn.k_proj.weight')
                        raise failure
            self.assertIs(caught.exception,failure);self.assertEqual(trace.stack,[])
            self.assertEqual((Path(run)/'memoria-ultima-etapa.json').read_bytes(),original)
            self.assertEqual(mt.last_event(run)['sequence'],trace.sequence)
            self.assertEqual(mt.last_event(run)['event'],'error')
            self.assertEqual(len(records(run)),4)
            self.assertFalse(list(Path(run).glob('.memoria-ultima-etapa-*.tmp')))

    def test_windows_delete_sharing_race_simulated_with_a_live_reader(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            held=threading.Event();release=threading.Event();original=Path.replace
            def reader():
                with (Path(run)/'memoria-ultima-etapa.json').open('rb') as f:
                    held.set();release.wait(2);f.read()
            thread=threading.Thread(target=reader);thread.start()
            self.assertTrue(held.wait(1))
            def windows_rule(path,target):
                if held.is_set() and not release.is_set():raise denied()
                return original(path,target)
            try:
                with patch.object(Path,'replace',windows_rule),patch.object(mt.time,'sleep'),patch('sys.stderr'):
                    trace.emit('transfer.tensor','begin',name='decoder.layers.3.self_attn.k_proj.weight')
                    self.assertEqual(mt.last_event(run)['sequence'],2)
                    self.assertEqual(json.loads((Path(run)/'memoria-ultima-etapa.json').read_text())['sequence'],1)
            finally:release.set();thread.join(timeout=3)
            self.assertEqual(len(records(run)),2)

    def test_fsync_failure_is_visible_and_retry_tolerates_duplicate_sequence(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run)
            with patch.object(mt.os,'fsync',side_effect=denied()),patch('sys.stderr'):
                row=trace.emit('worker','begin')
            self.assertEqual(row['telemetry']['pending_events'],1)
            self.assertIn('diagnostic_append_error',row['telemetry']['last_error'])
            trace.emit('worker','end')
            self.assertEqual(set(r['sequence'] for r in records(run)),{1,2})
            self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual(len(trace._pending),0)

    def test_append_failure_recovers_pending_events_in_order(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);original=Path.open
            def fault(path,*a,**kw):
                if path.name=='memoria-eventos.jsonl' and a and a[0]=='a':raise denied()
                return original(path,*a,**kw)
            with patch.object(Path,'open',fault),patch('sys.stderr'):
                row=trace.emit('transfer.tensor','begin',name='weight')
                self.assertEqual(row['telemetry']['pending_events'],1)
                self.assertEqual(mt.last_event(run)['sequence'],1)  # Cache fallback.
            trace.emit('worker','end')  # Force a bounded retry, no generation retry.
            self.assertEqual([r['sequence'] for r in records(run)],[1,2])
            self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual(len(trace._pending),0)

    def test_partial_append_is_isolated_without_truncating_history(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');original=Path.open
            @contextmanager
            def partial(path,*a,**kw):
                with original(path,*a,**kw) as f:
                    class HalfWrite:
                        def write(self,line):
                            f.write(line[:len(line)//2]);f.flush();raise denied()
                    yield HalfWrite()
            def fault(path,*a,**kw):
                if path.name=='memoria-eventos.jsonl' and a and a[0]=='a':return partial(path,*a,**kw)
                return original(path,*a,**kw)
            with patch.object(Path,'open',fault),patch('sys.stderr'):
                trace.emit('transfer.tensor','begin',name='weight')
            trace.emit('worker','end')
            self.assertEqual([r['sequence'] for r in records(run)],[1,2,3])
            self.assertEqual(mt.last_event(run)['sequence'],3)

    def test_permanent_log_failure_has_bounded_ram_queue_and_explicit_loss(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);original=Path.open
            def fault(path,*a,**kw):
                if path.name=='memoria-eventos.jsonl' and a and a[0]=='a':raise denied()
                return original(path,*a,**kw)
            with patch.object(Path,'open',fault),patch('sys.stderr'):
                for n in range(100):last=trace.emit('transfer.tensor','begin',name=str(n))
            self.assertLessEqual(len(trace._pending),trace.MAX_PENDING)
            self.assertLessEqual(trace._pending_bytes,trace.MAX_PENDING_BYTES)
            self.assertGreater(last['telemetry']['dropped_events'],0)
            self.assertEqual(mt.last_event(run)['sequence'],100)
            self.assertTrue(records(run,'memoria-telemetria-errores.jsonl'))

    def test_cleanup_denial_is_reported_without_aborting(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);unlink=Path.unlink
            def fault(path,*a,**kw):
                if path.name.startswith('.memoria-ultima-etapa-') and path.exists():raise denied()
                return unlink(path,*a,**kw)
            with patch.object(Path,'replace',side_effect=denied()),patch.object(Path,'unlink',fault),patch.object(mt.time,'sleep'),patch('sys.stderr'):
                trace.emit('worker','end')
            errors=records(run,'memoria-telemetria-errores.jsonl')
            self.assertEqual(errors[-1]['last_error']['stage'],'marker.cleanup')
            self.assertEqual(mt.last_event(run)['event'],'end')
            self.assertEqual(len(list(Path(run).glob('.memoria-ultima-etapa-*.tmp'))),1)

    def test_multiple_threads_and_reader_have_unique_temporaries_and_complete_lines(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);replace=Path.replace;temporary=[];stop=threading.Event();observed=[]
            def record(path,target):temporary.append(str(path));return replace(path,target)
            def reader():
                while not stop.is_set():
                    row=mt.last_event(run)
                    if row is not None:observed.append(row)
            thread=threading.Thread(target=reader);thread.start()
            def write(n):
                with trace.span('thread.'+str(n)):
                    trace.emit('transfer.tensor','begin',name=str(n))
            try:
                with patch.object(Path,'replace',record),ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(write,range(30)))
            finally:stop.set();thread.join(timeout=5)
            rows=records(run)
            self.assertEqual([r['sequence'] for r in rows],list(range(1,91)))
            self.assertEqual(len(temporary),len(set(temporary)))
            for row in rows:
                if row['operation']=='transfer.tensor':self.assertEqual(row['stack'],['thread.'+row['details']['name']])
            self.assertTrue(observed);self.assertEqual(mt.last_event(run)['sequence'],90)
            self.assertFalse(list(Path(run).glob('.memoria-ultima-etapa-*.tmp')))

    def test_reader_skips_partial_tail_and_uses_newest_valid_source(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);first=trace.emit('worker','begin');second=trace.emit('transfer.tensor','begin')
            marker=Path(run)/'memoria-ultima-etapa.json'
            marker.write_text(json.dumps(first))
            with (Path(run)/'memoria-eventos.jsonl').open('ab') as f:f.write(b'{"sequence":999')
            self.assertEqual(mt.last_event(run)['sequence'],second['sequence'])
            marker.write_text(json.dumps(dict(second,sequence=3)))
            self.assertEqual(mt.last_event(run)['sequence'],3)

    def test_reader_denial_is_visible_to_supervisor_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            shared=mt._open_shared
            @contextmanager
            def blocked(path):
                if path.name=='memoria-ultima-etapa.json':raise denied()
                with shared(path) as f:yield f
            diagnostics={}
            with patch.object(mt,'_open_shared',blocked):row=mt.last_event(run,diagnostics)
            self.assertEqual(row['sequence'],1);self.assertIn('memoria-ultima-etapa.json',diagnostics)
            with patch.object(mt,'_open_shared',side_effect=denied()):
                self.assertIsNone(mt.last_event(run,diagnostics))
            self.assertEqual(len(diagnostics),2)

    def test_win32_reader_requests_share_delete_and_closes_adopted_fd(self):
        from types import SimpleNamespace
        import ctypes
        with tempfile.TemporaryDirectory() as run:
            path=Path(run)/'marker.json';path.write_bytes(b'hello')
            calls=[];closed=[];adopted=[]
            def create(*args):calls.append(args);return 123
            def close(handle):closed.append(handle);return True
            def adopt(handle,flags):
                self.assertEqual(handle,123)
                fd=os.open(path,os.O_RDONLY);adopted.append(fd);return fd
            kernel=SimpleNamespace(CreateFileW=create,CloseHandle=close)
            crt=SimpleNamespace(open_osfhandle=adopt)
            with patch.object(mt.os,'name','nt'),patch.object(mt.os,'O_BINARY',0x8000,create=True),patch.object(ctypes,'WinDLL',return_value=kernel,create=True),patch.dict('sys.modules',msvcrt=crt):
                with mt._open_shared(path) as f:self.assertEqual(f.read(),b'hello')
            self.assertEqual(calls[0][2],7)  # READ | WRITE | DELETE
            self.assertEqual(closed,[])  # Ownership transferred to fd; no double CloseHandle.
            with self.assertRaises(OSError):os.fstat(adopted[0])
            def fail(handle,flags):raise OSError('adoption failed')
            with patch.object(mt.os,'name','nt'),patch.object(mt.os,'O_BINARY',0x8000,create=True),patch.object(ctypes,'WinDLL',return_value=kernel,create=True),patch.dict('sys.modules',msvcrt=SimpleNamespace(open_osfhandle=fail)):
                with self.assertRaisesRegex(OSError,'adoption failed'):
                    with mt._open_shared(path):pass
            self.assertEqual(closed,[123])

    @unittest.skipUnless(os.name=='nt','Requiere handles y reglas de comparticion de Windows real; no depende de GPU.')
    def test_native_shared_reader_permits_atomic_replace_while_open(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            with mt._open_shared(Path(run)/'memoria-ultima-etapa.json') as f:
                trace.emit('transfer.tensor','begin',name='weight')
                self.assertEqual(json.loads(f.read())['sequence'],1)
                self.assertEqual(json.loads((Path(run)/'memoria-ultima-etapa.json').read_text())['sequence'],2)
            self.assertEqual(trace._errors,0)

    @unittest.skipUnless(os.name=='nt','Requiere lector CRT sin FILE_SHARE_DELETE de Windows real.')
    def test_native_unshared_reader_cannot_abort_the_worker(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            with (Path(run)/'memoria-ultima-etapa.json').open('rb') as blocker,patch('sys.stderr'):
                trace.emit('transfer.tensor','begin',name='weight')
                self.assertTrue(blocker.read())
                self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual(len(records(run)),2)
