"""Telemetry semantics and unchanged low-RAM policy; no generation."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from musicstudio import memory_trace as mt

class TelemetryTests(unittest.TestCase):
    def test_three_low_samples_and_recovery_reset(self):
        g=mt.LowMemoryGuard()
        self.assertFalse(g.observe(1024**3-1));self.assertFalse(g.observe(0))
        self.assertFalse(g.observe(1024**3))
        self.assertFalse(g.observe(0));self.assertFalse(g.observe(0));self.assertTrue(g.observe(0))

    def test_events_survive_kill_and_keep_nested_operation(self):
        with tempfile.TemporaryDirectory() as t:
            trace=mt.MemoryTrace(t,lambda:{'system_available_bytes':42,'process_rss_bytes':7})
            with self.assertRaisesRegex(RuntimeError,'failure'):
                with trace.span('transfer.model',target='cuda'):
                    trace.emit('transfer.tensor','begin',name='decoder.0.weight')
                    raise RuntimeError('failure')
            rows=[json.loads(l) for l in (Path(t)/'memoria-eventos.jsonl').read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows],['begin','begin','error'])
            self.assertEqual(rows[1]['stack'],['transfer.model'])
            self.assertEqual(mt.last_event(t),rows[-1]);self.assertEqual(trace.stack,[])

    def test_metrics_do_not_confuse_private_vms_rss_commit(self):
        process=SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=11,vms=999,private=44,num_page_faults=3))
        fake=SimpleNamespace(Process=lambda:process,virtual_memory=lambda:SimpleNamespace(available=55,total=100))
        with patch.dict('sys.modules',psutil=fake),patch.object(mt,'windows_commit',return_value=dict(system_commit_bytes=88,system_commit_limit_bytes=200,system_commit_peak_bytes=99,commit_sampling_error='')):
            row=mt.sample_memory(process)
        self.assertEqual(row['process_private_bytes'],44);self.assertEqual(row['process_rss_bytes'],11)
        self.assertEqual(row['process_vms_bytes'],999);self.assertEqual(row['system_commit_bytes'],88)
        self.assertEqual(row['system_available_bytes'],55)

    def test_sampling_failure_is_visible_not_zero(self):
        with tempfile.TemporaryDirectory() as t:
            def fail():raise OSError('counter unavailable')
            trace=mt.MemoryTrace(t,fail);row=trace.emit('init','begin')
            self.assertIn('sampling_error',row);self.assertNotIn('system_commit_bytes',row)
            self.assertEqual(mt.last_event(t),row)

    def test_windows_commit_counts_pages_not_swap_or_working_set(self):
        import ctypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',ctypes.c_uint32)]+[(n,ctypes.c_size_t) for n in ('CommitTotal','CommitLimit','CommitPeak','PageSize')]
        def api(pointer,size):
            c=pointer._obj;c.CommitTotal=6;c.CommitLimit=16;c.CommitPeak=8;c.PageSize=4096
            self.assertEqual(size,ctypes.sizeof(Counters));return True
        with patch.object(mt.os,'name','nt'),patch.object(mt,'_performance_api',return_value=(Counters,api)):
            row=mt.windows_commit()
        self.assertEqual(row['system_commit_bytes'],6*4096)
        self.assertEqual(row['system_commit_limit_bytes'],16*4096)
        self.assertEqual(row['system_commit_peak_bytes'],8*4096)

    def test_nonwindows_commit_is_explicitly_unavailable(self):
        with patch.object(mt.os,'name','posix'):
            row=mt.windows_commit()
        self.assertIsNone(row['system_commit_bytes']);self.assertTrue(row['commit_sampling_error'])

class MonitorTests(unittest.TestCase):
    def test_slow_gpu_sampling_cannot_delay_three_sample_ram_stop(self):
        self.run_pressure_case()

    def test_logging_failure_cannot_prevent_low_ram_kill(self):
        self.run_pressure_case(fail_logging=True)

    def run_pressure_case(self, fail_logging=False):
        from musicstudio import generate as parent
        import threading
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as t:
            run=Path(t);gate=threading.Event();gpu_started=threading.Event();calls=[]
            gpu={'index':0,'name':'RTX 4060','free_mib':7063,'used_mib':1129}
            def nvidia():
                calls.append(1)
                if len(calls)>1:
                    gpu_started.set();gate.wait(3)  # GPU reader must not block RAM loop.
                return [gpu]
            child=SimpleNamespace(pid=1234,stdout=iter([]),killed=False)
            def kill():child.killed=True;gate.set()
            child.kill=kill;child.wait=lambda:1;child.poll=lambda:1 if child.killed else None
            fake_psutil=SimpleNamespace(virtual_memory=lambda:SimpleNamespace(available=6*1024**3),Process=lambda pid:object(),swap_memory=lambda:SimpleNamespace(used=77),NoSuchProcess=ProcessLookupError)
            request={'seed':1,'use_lm':False,'prompt':'romantico','lyrics':'letra original'}
            validation={'status':'LOCAL_ENVIRONMENT_CHECKS_PASSED_NOT_MODEL_GENERATION','requirements_sha256':'hash','streaming_int8_offload':{'transfer_method':'move_streaming'}}
            metric={'process_rss_bytes':123,'process_private_bytes':456,'system_commit_bytes':789,'system_available_bytes':512*1024**2}
            original_write=parent.write_json
            def report(path,data):
                if fail_logging and Path(path).name=='memoria-interrupcion.json':raise OSError('report unavailable')
                original_write(path,data)
            def metric_sample(process):
                self.assertTrue(gpu_started.wait(1));return metric
            with patch.object(parent,'write_json',side_effect=report),patch.dict('sys.modules',psutil=fake_psutil),patch.object(parent,'read_json',side_effect=lambda p:request if p=='request' else validation),patch.object(parent,'sha256',return_value='hash'),patch.object(parent,'new_run',return_value=run),patch.object(parent,'nvidia_inventory',side_effect=nvidia),patch.object(parent,'sample_memory',side_effect=metric_sample),patch.object(parent.subprocess,'Popen',return_value=child),patch.object(parent.time,'sleep'):
                try:code=parent.supervise('request')
                finally:gate.set()
            self.assertEqual(code,1);self.assertTrue(child.killed)
            report=json.loads((run/'monitor.json').read_text())
            self.assertEqual(report['samples'],3);self.assertFalse(report['success'])
            self.assertEqual(report['guard']['available_physical_ram_bytes'],1024**3)
            if fail_logging:
                self.assertIn('RAM disponible inferior',report['stopped_reason'])
            else:
                self.assertEqual(json.loads((run/'memoria-interrupcion.json').read_text())['sample']['system_available_bytes'],512*1024**2)
            self.assertLess(report['elapsed_s'],2)

    def test_old_validation_marker_cannot_start_a_generation(self):
        from musicstudio import generate as parent
        fake_psutil=SimpleNamespace()
        request={'seed':1,'use_lm':False,'prompt':'romantico','lyrics':'original'}
        old={'status':'LOCAL_ENVIRONMENT_CHECKS_PASSED_NOT_MODEL_GENERATION','requirements_sha256':'hash'}
        with patch.dict('sys.modules',psutil=fake_psutil),patch.object(parent,'read_json',side_effect=lambda p:request if p=='request' else old),patch.object(parent,'sha256',return_value='hash'),patch.object(parent.subprocess,'Popen') as start:
            with self.assertRaisesRegex(RuntimeError,'07_VALIDAR'):
                parent.supervise('request')
            start.assert_not_called()
