"""Run HID I/O on a dedicated thread with a CoreFoundation run loop.

macOS hidapi uses IOHIDManager, which schedules callbacks on the current
thread's CFRunLoop. Calling hid.enumerate() from a bare ThreadPoolExecutor
worker crashes in CFRunLoopAddSource (__CFCheckCFInfoPACSignature).
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, TypeVar

T = TypeVar("T")

_ready = threading.Event()
_jobs: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None


def _ensure_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    def _loop() -> None:
        import CoreFoundation  # noqa: WPS433 — macOS-only worker thread

        CoreFoundation.CFRunLoopGetCurrent()
        _ready.set()
        while True:
            job = _jobs.get()
            if job is None:
                break
            fn, reply = job
            try:
                reply.put((True, fn()))
            except Exception as exc:  # noqa: BLE001
                reply.put((False, exc))
            finally:
                _jobs.task_done()

    _ready.clear()
    _thread = threading.Thread(target=_loop, name="g-hid-loop", daemon=True)
    _thread.start()
    _ready.wait(timeout=5.0)


def run_hid(fn: Callable[[], T], *, timeout: float = 8.0) -> T:
    """Execute ``fn`` on the HID worker thread; raise on timeout or error."""
    _ensure_worker()
    reply: queue.Queue = queue.Queue(maxsize=1)
    _jobs.put((fn, reply))
    try:
        ok, val = reply.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"HID worker timed out after {timeout:.0f}s") from exc
    if ok:
        return val
    raise val
