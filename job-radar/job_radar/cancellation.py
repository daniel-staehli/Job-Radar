"""Cooperative cancellation scoped to one generation worker.

Only processes launched inside the active scope are terminated. Independent
imports and other application tasks have no token and keep their normal paths.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import signal
import locale
import tempfile
import subprocess
import threading
import time


class GenerationCancelled(RuntimeError):
    """The user stopped this run; callers must not publish it as completed."""


class CancellationToken:
    def __init__(self):
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._outputs: list[Path] = []

    def cancel(self) -> None:
        # UI callbacks only set an event; cleanup runs on the worker.
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self.is_cancelled:
            raise GenerationCancelled("Generation was aborted.")

    def track_output(self, path: Path) -> None:
        with self._lock:
            path = Path(path)
            if path not in self._outputs:
                self._outputs.append(path)

    @property
    def outputs(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(self._outputs)


_CURRENT: ContextVar[CancellationToken | None] = ContextVar("job_radar_cancellation", default=None)


def current_token() -> CancellationToken | None:
    return _CURRENT.get()


def check_cancelled() -> None:
    token = current_token()
    if token is not None:
        token.check()


@contextmanager
def cancellation_scope(token: CancellationToken):
    handle = _CURRENT.set(token)
    try:
        token.check()
        yield token
    finally:
        _CURRENT.reset(handle)


class _WindowsJob:
    """Keep a suspended child and all descendants in a kill-on-close job."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k.SetInformationJobObject.restype = w.BOOL
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.AssignProcessToJobObject.restype = w.BOOL
        k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        k.TerminateJobObject.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("minimum_working", ctypes.c_size_t),
                        ("maximum_working", ctypes.c_size_t), ("active_processes", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not k.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def resume(self, process):
        # Popen closes the initial thread handle. Find only threads belonging to
        # our still-suspended process, using documented Toolhelp/ResumeThread APIs.
        ctypes, k = self.ctypes, self.kernel
        from ctypes import wintypes as w
        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread_id", w.DWORD),
                        ("process_id", w.DWORD), ("base_priority", w.LONG),
                        ("delta_priority", w.LONG), ("flags", w.DWORD)]
        k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        k.CreateToolhelp32Snapshot.restype = w.HANDLE
        k.Thread32First.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        k.Thread32First.restype = w.BOOL
        k.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        k.Thread32Next.restype = w.BOOL
        k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenThread.restype = w.HANDLE
        k.ResumeThread.argtypes = [w.HANDLE]
        k.ResumeThread.restype = w.DWORD
        snapshot = k.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        owned_threads = []
        try:
            entry = ThreadEntry(); entry.size = ctypes.sizeof(entry)
            exists = k.Thread32First(snapshot, ctypes.byref(entry))
            while exists:
                if entry.process_id == process.pid:
                    owned_threads.append(entry.thread_id)
                entry.size = ctypes.sizeof(entry)
                exists = k.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            k.CloseHandle(snapshot)
        if not owned_threads:
            raise RuntimeError("The generation helper could not be started safely.")
        for thread_id in owned_threads:
            handle = k.OpenThread(0x2, False, thread_id)  # THREAD_SUSPEND_RESUME
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if k.ResumeThread(handle) == 0xffffffff:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                k.CloseHandle(handle)

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _stop_process_tree(process: subprocess.Popen, job=None) -> None:
    """Stop only the owned tree and reap its pipes before reporting stopped."""
    if os.name == "nt":
        if job is not None:
            job.terminate()
        elif process.poll() is None:
            # Startup failed while the process was suspended; it has no children.
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The generation helper did not stop cleanly. Wait before closing Job Radar.") from exc


def run_process(*args, **kwargs):
    """subprocess.run with cancellable waiting only inside a generation scope."""
    token = current_token()
    if token is None:
        return subprocess.run(*args, **kwargs)
    token.check()
    options = dict(kwargs)
    input_data = options.pop("input", None)
    timeout = options.pop("timeout", None)
    check = options.pop("check", False)
    capture_output = options.pop("capture_output", False)
    input_file = None
    if input_data is not None and "stdin" in options:
        raise ValueError("stdin and input arguments may not both be used.")
    if capture_output:
        if "stdout" in options or "stderr" in options:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name == "nt":
        options["creationflags"] = (options.get("creationflags", 0)
                                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                    | 0x4)  # CREATE_SUSPENDED: assign job before any child can run
    else:
        options["start_new_session"] = True
    if input_data is not None:
        # communicate(input=...) can block writing to a Windows pipe before its
        # timeout starts if a broken helper never reads stdin. A private file
        # gives identical stdin bytes without an uninterruptible writer.
        text_mode = options.get("text") or options.get("universal_newlines") or options.get("encoding") or options.get("errors")
        if text_mode:
            input_data = input_data.encode(options.get("encoding") or locale.getpreferredencoding(False),
                                           options.get("errors") or "strict")
        input_file = tempfile.TemporaryFile(mode="w+b")
        try:
            input_file.write(input_data)
            input_file.seek(0)
            options["stdin"] = input_file
        except BaseException:
            input_file.close()
            raise
    try:
        process = subprocess.Popen(*args, **options)
    except BaseException:
        if input_file is not None:
            input_file.close()
        raise
    job = None
    started = time.monotonic()
    try:
        if os.name == "nt":
            job = _WindowsJob(process)
            token.check()
            job.resume(process)
        while True:
            token.check()
            remaining = None if timeout is None else timeout - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            interval = .1 if remaining is None else min(.1, remaining)
            try:
                stdout, stderr = process.communicate(timeout=interval)
            except subprocess.TimeoutExpired:
                continue
            token.check()
            if check and process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args, output=stdout, stderr=stderr)
            return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    except BaseException:
        _stop_process_tree(process, job)
        raise
    finally:
        if job is not None:
            job.close()
        if input_file is not None:
            input_file.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
