"""Row-strip threads for per-pixel work (F4).

A per-pixel function gives a pixel the same value whatever array the pixel sits in. So running it
on horizontal strips in threads and joining the strips is bit-identical to one call over the
frame; tests/test_parallel.py holds every op to that. numpy releases the GIL inside its loops, so
the strips really run side by side. A function that reads neighbouring pixels or a statistic of the
whole frame (a blur, dehaze) must not go through here. Never call `rows` from inside `rows`.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

MIN_PIXELS = 1_500_000   # a smaller frame is one call: threads would cost more than they save
STRIP_ROWS = 256
_threads: int | None = None
_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def threads() -> int:
    """Threads for strip work: set_threads(), else AUTOEDIT_THREADS, else one per core."""
    if _threads is not None:
        return _threads
    try:
        n = int(os.environ.get("AUTOEDIT_THREADS", "0"))
    except ValueError:
        n = 0
    return min(n, 256) if n > 0 else (os.cpu_count() or 1)


def set_threads(n: int | None) -> None:
    """Fix the thread count of this process. None or 0 means automatic. A strip call that is
    running keeps the pool it holds; the pool is replaced, never shut down under it."""
    global _threads, _pool
    value = n if n and n > 0 else None
    with _lock:
        if value == _threads:
            return
        _threads = value
        _pool = None


def each(fn, *arrays: np.ndarray) -> list:
    """`fn` on every row strip of the arrays, results as a list in strip order (for a reduction
    that needs one small value per strip). Below the size limits: one call, a list of one."""
    global _pool
    h, w = arrays[0].shape[:2]
    n = threads()
    if n <= 1 or h * w < MIN_PIXELS or h < 2 * STRIP_ROWS:
        return [fn(*arrays)]
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="autoedit-rows")
        pool = _pool
    return list(pool.map(lambda y: fn(*(a[y:y + STRIP_ROWS] for a in arrays)), range(0, h, STRIP_ROWS)))


def rows(fn, *arrays: np.ndarray) -> np.ndarray:
    """`fn(*arrays)`, computed strip by strip. The arrays share their first two dimensions."""
    parts = each(fn, *arrays)
    return parts[0] if len(parts) == 1 else np.concatenate(parts, axis=0)
