"""Memory measurements with explicit Windows meanings; no model imports."""
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timezone
import ctypes
import json
from functools import lru_cache
import os
from pathlib import Path
import sys
import threading
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


CHECKPOINT = 'memoria-ultima-etapa.jsonl'


class MemoryTrace:
    """Single worker writer, serialized across threads; append-only checkpoints.

    I/O errors degrade telemetry visibly, never suppress the RAM guard or a
    model exception. Pending events are bounded: no unbounded RAM logging queue.
    """
    MAX_PENDING = 64
    MAX_PENDING_BYTES = 1024 * 1024

    def __init__(self, run, sampler=None):
        self.run = Path(run)
        self.sampler = sampler or (lambda: sample_memory(include_cuda=True))
        self.started = time.monotonic()
        self.sequence = 0
        self._lock = threading.RLock()
        self._local = threading.local()
        self._pending = deque()
        self._pending_bytes = 0
        self._journal_dirty = False
        self._next_journal = self._next_checkpoint = self._last_sync = 0
        self._checkpoint_dirty = False
        self._checkpoint_sync = 0
        self._errors = 0
        self._dropped = 0
        self._last_error = None
        self._warned = {}

    @property
    def stack(self):
        if not hasattr(self._local, 'stack'):
            self._local.stack = []
        return self._local.stack

    def _health(self):
        return dict(io_errors=self._errors, dropped_events=self._dropped,
                    pending_events=len(self._pending), last_error=self._last_error)

    def _problem(self, stage, exc):
        self._errors += 1
        self._last_error = dict(stage=stage, error=repr(exc),
                                winerror=getattr(exc, 'winerror', None), sequence=self.sequence)
        diagnostic = dict(utc=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
                          operation='telemetry.io', **self._health())
        # Independent append-only diagnostic: never replaces the contended file.
        try:
            with (self.run / 'memoria-telemetria-errores.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(diagnostic, ensure_ascii=True)+'\n')
                f.flush()
                os.fsync(f.fileno())
        except OSError as diagnostic_error:
            self._last_error["diagnostic_append_error"] = repr(diagnostic_error)
            # stderr + next successful event still carry both errors/counters.
        now = time.monotonic()
        if now - self._warned.get(stage, float('-inf')) >= 1:
            self._warned[stage] = now
            try:
                print('TELEMETRIA DEGRADADA (no se reintenta la generacion): '+
                      json.dumps(diagnostic, ensure_ascii=True), file=sys.stderr, flush=True)
            except OSError:
                pass

    def _flush_journal(self, force=False):
        now = time.monotonic()
        if not force and now < self._next_journal:
            return
        while self._pending:
            entry, size = self._pending[0]
            try:
                with (self.run / 'memoria-eventos.jsonl').open('a', encoding='utf-8') as f:
                    if self._journal_dirty:
                        f.write('\n')  # Isolate a possibly partial previous write; never truncate history.
                    f.write(json.dumps(entry, ensure_ascii=True)+'\n')
                    f.flush()
                    if force or now-self._last_sync >= 1:
                        os.fsync(f.fileno())
                        self._last_sync = now
                self._journal_dirty = False
                self._pending.popleft()
                self._pending_bytes -= size
            except OSError as exc:
                self._journal_dirty = True
                self._next_journal = now + .25
                self._problem('journal.append', exc)
                return
        self._next_journal = 0

    def _publish_checkpoint(self, entry, force=False):
        """Append without renaming, replacing, truncating or deleting any file.

        The main journal is authoritative. This independent stream preserves
        a recent event when the main append is temporarily unavailable.
        """
        now = time.monotonic()
        if not force and now < self._next_checkpoint:
            return
        try:
            with (self.run / CHECKPOINT).open('a', encoding='utf-8') as f:
                if self._checkpoint_dirty:
                    f.write('\n')  # Do not concatenate onto a failed partial record.
                f.write(json.dumps(entry, ensure_ascii=True)+'\n')
                f.flush()
                if force or now-self._checkpoint_sync >= 1:
                    os.fsync(f.fileno())
                    self._checkpoint_sync = now
            self._checkpoint_dirty = False
            self._next_checkpoint = 0
        except OSError as exc:
            self._checkpoint_dirty = True
            self._next_checkpoint = now + .25
            self._problem('checkpoint.append', exc)

    def emit(self, operation, event, **details):
        with self._lock:
            self.sequence += 1
            try:
                metrics = self.sampler()
            except Exception as exc:
                metrics = {'sampling_error': repr(exc)}
            entry = dict(sequence=self.sequence, monotonic_s=time.monotonic(),
                         elapsed_s=time.monotonic()-self.started, utc=datetime.now(timezone.utc).isoformat(),
                         pid=os.getpid(), operation=operation, event=event, stack=list(self.stack),
                         details=details, telemetry=self._health(), **metrics)
            size = len(json.dumps(entry, ensure_ascii=True).encode('utf-8')) + 1
            self._pending.append((entry, size))
            self._pending_bytes += size
            while len(self._pending) > self.MAX_PENDING or self._pending_bytes > self.MAX_PENDING_BYTES:
                _, removed_size = self._pending.popleft()
                self._pending_bytes -= removed_size
                self._dropped += 1
            if self._dropped != entry['telemetry']['dropped_events']:
                self._problem('journal.buffer', OSError('Limite del buffer: eventos antiguos descartados'))
            force = event == 'error' or operation == 'worker' and event == 'end'
            self._flush_journal(force=force)
            entry['telemetry'] = self._health()
            self._publish_checkpoint(entry, force=force)
            # Return status after a checkpoint append failure too.
            entry['telemetry'] = self._health()
            return entry

    @contextmanager
    def span(self, operation, **details):
        self.stack.append(operation)
        try:
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


@contextmanager
def _open_shared(path):
    """Readers share read/write; DELETE is retained for legacy-file compatibility.

    New telemetry appends never require rename/delete access to this handle.
    """
    if os.name != 'nt':
        with Path(path).open('rb') as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    with stream:  # fd owns the Win32 handle after adoption.
        yield stream


def last_event(run, diagnostics=None):
    """Bounded tail reads, newline commits; legacy JSON is read-only fallback."""
    candidates = []
    diagnostics = diagnostics if diagnostics is not None else {}
    for priority, (filename, journal) in enumerate((('memoria-ultima-etapa.json', False), ('memoria-eventos.jsonl', True), (CHECKPOINT, True))):
        try:
            with _open_shared(Path(run) / filename) as stream:
                if journal:
                    stream.seek(0, os.SEEK_END)
                    start = max(0, stream.tell()-256*1024)
                    stream.seek(start)
                else:
                    start = 0
                raw = stream.read(256*1024)
            if journal:
                lines = raw.split(b'\n')[:-1]  # Require a newline: ignore an in-flight append.
                if start:
                    lines = lines[1:]  # First fragment may start in the middle of a record.
                lines = reversed(lines)
            else:
                lines = [raw]
            for line in lines:
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and isinstance(value.get('sequence'), int):
                    candidates.append((value["sequence"], priority, value))
                    break
        except FileNotFoundError:
            continue  # Normal before the worker has emitted its first event.
        except OSError as exc:
            diagnostics[filename] = repr(exc)
            continue  # Visible to parent CSV; RAM sampling stays independent.
    return max(candidates, key=lambda v:v[:2])[2] if candidates else None


class LowMemoryGuard:
    """Unchanged policy: 1 GiB available PHYSICAL RAM, three samples."""
    def __init__(self):
        self.consecutive = 0

    def observe(self, available_bytes):
        self.consecutive = self.consecutive + 1 if available_bytes < 1024**3 else 0
        return self.consecutive >= 3
