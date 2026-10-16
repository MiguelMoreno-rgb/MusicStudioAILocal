"""Memory measurements with explicit Windows meanings; no model imports."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import json
from functools import lru_cache
import os
from pathlib import Path
import sys
import time


def windows_commit():
    """GetPerformanceInfo system commit counters, NOT swap-file occupancy."""
    if os.name != 'nt':
        return {'system_commit_bytes': None, 'system_commit_limit_bytes': None,
                'system_commit_peak_bytes': None, 'commit_sampling_error': 'Windows GetPerformanceInfo unavailable on this OS'}
    info_type, api = _performance_api()
    counters = info_type()
    counters.cb = ctypes.sizeof(counters)
    if not api(ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'system_commit_bytes': counters.CommitTotal * counters.PageSize,
            'system_commit_limit_bytes': counters.CommitLimit * counters.PageSize,
            'system_commit_peak_bytes': counters.CommitPeak * counters.PageSize,
            'commit_sampling_error': ''}


@lru_cache(maxsize=1)
def _performance_api():
    from ctypes import wintypes
    class PerformanceInformation(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
            'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal', 'PhysicalAvailable',
            'SystemCache', 'KernelTotal', 'KernelPaged', 'KernelNonpaged', 'PageSize')]
        _fields_ += [(name, wintypes.DWORD) for name in ('HandleCount', 'ProcessCount', 'ThreadCount')]
    api = ctypes.WinDLL('psapi', use_last_error=True).GetPerformanceInfo
    api.argtypes = [ctypes.POINTER(PerformanceInformation), wintypes.DWORD]
    api.restype = wintypes.BOOL
    return PerformanceInformation, api


def sample_memory(process=None, include_cuda=False):
    import psutil
    process = process or psutil.Process()
    vm = psutil.virtual_memory()
    info = process.memory_info()
    row = dict(system_available_bytes=vm.available, system_total_bytes=vm.total,
               process_rss_bytes=info.rss, process_vms_bytes=info.vms,
               process_private_bytes=getattr(info, 'private', None),
               process_page_faults=getattr(info, 'num_page_faults', None))
    try:
        row.update(windows_commit())
    except Exception as exc:
        row.update(system_commit_bytes=None, system_commit_limit_bytes=None,
                   system_commit_peak_bytes=None, commit_sampling_error=repr(exc))
    if include_cuda:
        torch = sys.modules.get('torch')
        if torch is not None and torch.cuda.is_initialized():
            row.update(cuda_allocated_bytes=torch.cuda.memory_allocated(0),
                       cuda_reserved_bytes=torch.cuda.memory_reserved(0),
                       cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(0),
                       cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(0))
    return row


class MemoryTrace:
    """Flushed JSONL and atomic last-event marker survive worker.kill()."""
    def __init__(self, run, sampler=None):
        self.run = Path(run)
        self.sampler = sampler or (lambda: sample_memory(include_cuda=True))
        self.started = time.monotonic()
        self.sequence = 0
        self.stack = []

    def emit(self, operation, event, **details):
        self.sequence += 1
        # Sampling failure must be visible; not disguise a missing measurement as 0.
        try:
            metrics = self.sampler()
        except Exception as exc:
            metrics = {'sampling_error': repr(exc)}
        entry = dict(sequence=self.sequence, monotonic_s=time.monotonic(),
                     elapsed_s=time.monotonic()-self.started, utc=datetime.now(timezone.utc).isoformat(),
                     pid=os.getpid(), operation=operation, event=event, stack=list(self.stack),
                     details=details, **metrics)
        line = json.dumps(entry, ensure_ascii=True) + '\n'
        with (self.run / 'memoria-eventos.jsonl').open('a', encoding='utf-8') as f:
            f.write(line); f.flush()
        tmp = self.run / 'memoria-ultima-etapa.json.tmp'
        tmp.write_text(line, encoding='utf-8')
        tmp.replace(self.run / 'memoria-ultima-etapa.json')
        return entry

    @contextmanager
    def span(self, operation, **details):
        self.stack.append(operation)
        self.emit(operation, 'begin', **details)
        try:
            yield
        except BaseException as exc:
            self.emit(operation, 'error', error_type=type(exc).__name__, error=str(exc)[:1000], **details)
            raise
        else:
            self.emit(operation, 'end', **details)
        finally:
            self.stack.pop()


def last_event(run):
    try:
        return json.loads((Path(run) / 'memoria-ultima-etapa.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class LowMemoryGuard:
    """Unchanged policy: 1 GiB available PHYSICAL RAM, three samples."""
    def __init__(self):
        self.consecutive = 0

    def observe(self, available_bytes):
        self.consecutive = self.consecutive + 1 if available_bytes < 1024**3 else 0
        return self.consecutive >= 3
