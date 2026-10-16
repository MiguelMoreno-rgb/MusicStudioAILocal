"""Append-only telemetry regressions; native Windows tests require no GPU.

Rename attempts and unique-file creations are distinct: no attempt-count
assertion is used as a proxy for uniqueness. No music/models are loaded.
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
        return mt.MemoryTrace(run, lambda:{'system_available_bytes':6*1024**3, 'process_rss_bytes':123})

    def test_legacy_retry_count_is_not_a_unique_file_count(self):
        # Reproduce the OLD test defect deterministically: one temp, two calls.
        with tempfile.TemporaryDirectory() as run:
            created=[];attempts=[]
            fd,name=tempfile.mkstemp(dir=run);os.close(fd);created.append(name)
            def replace(source,destination):
                attempts.append(str(source))
                if len(attempts)==1:raise denied()
                return destination  # Fake success: this audit must not require Windows rename.
            source=Path(name)
            for attempt in range(2):
                try:replace(source,Path(run)/'legacy.json');break
                except PermissionError:
                    if attempt==1:raise
            self.assertEqual(len(attempts),2)
            self.assertEqual(len(created),1)
            self.assertEqual(set(attempts),set(created))
            # 2 != 1 is not evidence of a collision; creation was exclusive.

    def test_no_rename_delete_or_temporary_is_needed_for_publication(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run)
            with patch.object(Path,'replace',side_effect=AssertionError('replace forbidden')),patch.object(mt.os,'replace',side_effect=AssertionError('os.replace forbidden')),patch.object(Path,'unlink',side_effect=AssertionError('delete forbidden')),patch('tempfile.mkstemp',side_effect=AssertionError('temp creation forbidden')):
                trace.emit('worker','begin');trace.emit('transfer.tensor','begin',name='weight')
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1,2])
            self.assertEqual(mt.last_event(run)['sequence'],2);self.assertEqual(trace._errors,0)
            self.assertEqual({p.name for p in Path(run).iterdir()},{mt.CHECKPOINT,'memoria-eventos.jsonl'})

    def test_checkpoint_denial_keeps_primary_and_recovers_without_replacement(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');original=Path.open
            def fault(path,*a,**kw):
                if path.name==mt.CHECKPOINT and a and a[0]=='a':raise denied()
                return original(path,*a,**kw)
            with patch.object(Path,'open',fault),patch('sys.stderr'):
                row=trace.emit('transfer.tensor','begin',name='weight')
            self.assertGreater(row['telemetry']['io_errors'],0)
            self.assertEqual(row['telemetry']['last_error']['winerror'],5)
            self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1])
            trace.emit('worker','end')
            self.assertEqual([r['sequence'] for r in records(run)],[1,2,3])
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1,3])
            self.assertEqual(mt.last_event(run)['sequence'],3)
            self.assertEqual(records(run,'memoria-telemetria-errores.jsonl')[-1]['last_error']['stage'],'checkpoint.append')

    def test_legacy_open_json_remains_historical_and_does_not_block_new_events(self):
        with tempfile.TemporaryDirectory() as run:
            legacy=Path(run)/'memoria-ultima-etapa.json'
            legacy.write_text(json.dumps({'sequence':0,'operation':'legacy','event':'begin'}))
            original=legacy.read_bytes();trace=self.trace(run)
            failure=ValueError('real model error')
            with legacy.open('rb') as reader,patch.object(Path,'replace',side_effect=denied()):
                with self.assertRaises(ValueError) as caught:
                    with trace.span('transfer.model'):
                        trace.emit('transfer.tensor','begin',name='decoder.layers.3.self_attn.k_proj.weight')
                        raise failure
                self.assertEqual(reader.read(),original)
            self.assertIs(caught.exception,failure);self.assertEqual(trace.stack,[])
            self.assertEqual(legacy.read_bytes(),original)
            self.assertEqual(mt.last_event(run)['sequence'],3)
            self.assertEqual(mt.last_event(run)['event'],'error')
            self.assertEqual(trace._errors,0)

    def test_fsync_failure_is_visible_and_replay_tolerates_duplicate_sequence(self):
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
                self.assertEqual(mt.last_event(run)['sequence'],1)
            trace.emit('worker','end')
            self.assertEqual([r['sequence'] for r in records(run)],[1,2])
            self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual(len(trace._pending),0)

    def test_partial_append_is_isolated_in_either_stream(self):
        for filename in ('memoria-eventos.jsonl',mt.CHECKPOINT):
            with self.subTest(filename=filename),tempfile.TemporaryDirectory() as run:
                trace=self.trace(run);trace.emit('worker','begin');original=Path.open
                @contextmanager
                def partial(path,*a,**kw):
                    with original(path,*a,**kw) as f:
                        class HalfWrite:
                            def write(self,line):
                                f.write(line[:len(line)//2]);f.flush();raise denied()
                        yield HalfWrite()
                def fault(path,*a,**kw):
                    if path.name==filename and a and a[0]=='a':return partial(path,*a,**kw)
                    return original(path,*a,**kw)
                with patch.object(Path,'open',fault),patch('sys.stderr'):
                    trace.emit('transfer.tensor','begin',name='weight')
                    self.assertEqual(mt.last_event(run)['sequence'],2)
                trace.emit('worker','end')
                self.assertEqual([r['sequence'] for r in records(run)],[1,2,3])
                expected=[1,3] if filename==mt.CHECKPOINT else [1,2,3]
                self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],expected)
                self.assertEqual(mt.last_event(run)['sequence'],3)

    def test_permanent_log_failure_has_bounded_queue_and_explicit_loss(self):
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

    def test_multiple_threads_and_reader_preserve_all_events_without_replacement(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);stop=threading.Event();latest=threading.Event();observed=[];errors=[]
            def reader():
                try:
                    while not stop.is_set():
                        row=mt.last_event(run)
                        if row is not None:
                            observed.append(row['sequence'])
                            if row['sequence']==90:latest.set()
                except BaseException as exc:errors.append(exc)
            def write(n):
                with trace.span('thread.'+str(n)):
                    trace.emit('transfer.tensor','begin',name=str(n))
            thread=threading.Thread(target=reader);thread.start()
            try:
                with patch.object(Path,'replace',side_effect=AssertionError('replace forbidden')),patch('tempfile.mkstemp',side_effect=AssertionError('temps forbidden')),ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(write,range(30)))
                self.assertTrue(latest.wait(3),'reader did not observe the final published event')
            finally:stop.set();thread.join(timeout=5)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            rows=records(run)
            self.assertEqual([r['sequence'] for r in rows],list(range(1,91)))
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],list(range(1,91)))
            self.assertEqual(trace._errors,0);self.assertEqual(observed,sorted(observed))
            for row in rows:
                if row['operation']=='transfer.tensor':self.assertEqual(row['stack'],['thread.'+row['details']['name']])
            self.assertFalse(list(Path(run).glob('*.tmp')))

    def test_reader_skips_partial_tail_and_uses_newest_valid_source(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');trace.emit('transfer.tensor','begin')
            legacy=Path(run)/'memoria-ultima-etapa.json';legacy.write_text(json.dumps({'sequence':0}))
            with (Path(run)/mt.CHECKPOINT).open('ab') as f:f.write(b'{"sequence":999')
            self.assertEqual(mt.last_event(run)['sequence'],2)
            with (Path(run)/mt.CHECKPOINT).open('ab') as f:f.write(b'\n'+json.dumps({'sequence':3,'operation':'test','event':'end'}).encode()+b'\n')
            self.assertEqual(mt.last_event(run)['sequence'],3)

    def test_reader_denial_is_visible_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');shared=mt._open_shared
            @contextmanager
            def blocked(path):
                if path.name==mt.CHECKPOINT:raise denied()
                with shared(path) as f:yield f
            diagnostics={}
            with patch.object(mt,'_open_shared',blocked):row=mt.last_event(run,diagnostics)
            self.assertEqual(row['sequence'],1);self.assertIn(mt.CHECKPOINT,diagnostics)
            with patch.object(mt,'_open_shared',side_effect=denied()):
                self.assertIsNone(mt.last_event(run,diagnostics))
            self.assertEqual(len(diagnostics),3)

    def test_win32_reader_requests_shared_access_and_closes_adopted_fd(self):
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
            with patch.object(mt.os,'name','nt'),patch.object(mt.os,'O_BINARY',0x8000,create=True),patch.object(ctypes,'WinDLL',return_value=kernel,create=True),patch.dict('sys.modules',msvcrt=SimpleNamespace(open_osfhandle=adopt)):
                with mt._open_shared(path) as f:self.assertEqual(f.read(),b'hello')
            self.assertEqual(calls[0][2],7);self.assertEqual(closed,[])
            with self.assertRaises(OSError):os.fstat(adopted[0])
            def fail(handle,flags):raise OSError('adoption failed')
            with patch.object(mt.os,'name','nt'),patch.object(mt.os,'O_BINARY',0x8000,create=True),patch.object(ctypes,'WinDLL',return_value=kernel,create=True),patch.dict('sys.modules',msvcrt=SimpleNamespace(open_osfhandle=fail)):
                with self.assertRaisesRegex(OSError,'adoption failed'):
                    with mt._open_shared(path):pass
            self.assertEqual(closed,[123])

    def test_separate_crt_reader_process_observes_next_append(self):
        import subprocess
        import sys
        import queue
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin')
            code = "import json,sys; f=open(sys.argv[1],'rb'); print(json.loads(f.readline())['sequence'],flush=True); sys.stdin.readline(); print(json.loads(f.readline())['sequence'],flush=True); f.close()"
            child=subprocess.Popen([sys.executable,'-I','-S','-B','-c',code,str(Path(run)/mt.CHECKPOINT)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            output=queue.Queue();captured=[]
            def drain():
                for line in child.stdout:
                    captured.append(line);output.put(line.strip())
            thread=threading.Thread(target=drain,daemon=True);thread.start()
            try:
                self.assertEqual(output.get(timeout=5),'1')
                trace.emit('transfer.tensor','begin',name='weight')
                child.stdin.write('continue\n');child.stdin.flush()
                self.assertEqual(output.get(timeout=5),'2')
                self.assertEqual(child.wait(timeout=5),0)
                self.assertEqual(trace._errors,0)
            except BaseException as exc:
                if child.poll() is None:child.kill()
                child.wait(timeout=5);thread.join(timeout=5)
                exc.add_note('Reader child exit='+str(child.returncode)+' stdout='+repr(''.join(captured))+' stderr='+repr(child.stderr.read()))
                raise
            finally:
                if child.poll() is None:child.kill()
                child.wait(timeout=5);thread.join(timeout=5)
                for stream in (child.stdin,child.stdout,child.stderr):stream.close()

    @unittest.skipUnless(os.name=='nt','Requiere handles de Windows real; no GPU.')
    def test_native_shared_reader_observes_appends_while_open(self):
        self.native_reader_case(shared=True)

    @unittest.skipUnless(os.name=='nt','Requiere lector CRT Windows real; no GPU.')
    def test_native_crt_reader_observes_appends_while_open(self):
        self.native_reader_case(shared=False)

    def native_reader_case(self, shared):
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');path=Path(run)/mt.CHECKPOINT
            identity=path.stat().st_ino
            opening=mt._open_shared(path) if shared else path.open('rb')
            with opening as reader,patch.object(Path,'replace',side_effect=AssertionError('rename forbidden')):
                self.assertEqual(json.loads(reader.readline())['sequence'],1)
                trace.emit('transfer.tensor','begin',name='weight')
                self.assertEqual(json.loads(reader.readline())['sequence'],2)
                self.assertEqual(path.stat().st_ino,identity)
                self.assertEqual(mt.last_event(run)['sequence'],2)
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1,2])
            self.assertEqual(trace._errors,0)

    @unittest.skipUnless(os.name=='nt','Requiere CreateFileW con denegacion de escritura real; no GPU.')
    def test_native_reader_denying_write_reports_fault_and_primary_still_advances(self):
        import ctypes
        from ctypes import wintypes
        with tempfile.TemporaryDirectory() as run:
            trace=self.trace(run);trace.emit('worker','begin');path=Path(run)/mt.CHECKPOINT
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            create=kernel.CreateFileW
            create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            create.restype=wintypes.HANDLE
            close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
            handle=create(str(path),0x80000000,0x1,None,3,0x80,None)  # share READ, deny WRITE/DELETE
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            try:
                with patch('sys.stderr'):trace.emit('transfer.tensor','begin',name='weight')
                self.assertGreater(trace._errors,0)
                self.assertEqual(trace._last_error['stage'],'checkpoint.append')
                self.assertEqual(mt.last_event(run)['sequence'],2)
                self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1])
            finally:close(handle)
            trace.emit('worker','end')
            self.assertEqual(mt.last_event(run)['sequence'],3)
            self.assertEqual([r['sequence'] for r in records(run)],[1,2,3])
            self.assertEqual([r['sequence'] for r in records(run,mt.CHECKPOINT)],[1,3])
