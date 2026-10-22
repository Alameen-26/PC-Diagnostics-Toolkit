"""Quick pure-Python benchmark (about 10-20 s) for comparing runs over time.

Scores are RELATIVE: they are only meaningful compared with other runs of
this same tool (ideally the same version and Python), e.g. "my CPU score
dropped 30% since last month, so something is throttling". They are not
comparable with Cinebench, CrystalDiskMark or Geekbench numbers.

* CPU single-core: how many fixed integer/float work units one process
  completes per second (score = units/s / 1000).
* CPU multi-core: the same work in one process per logical core
  (``multiprocessing``, because Python threads can't use several cores
  for pure-Python code due to the GIL).
* Disk: sequential write then read of a temporary file (default 256 MB)
  in the OS temp folder, which is normally on the system drive. The file
  is always deleted afterwards.
* Memory: ``memoryview`` copy throughput of a 64 MB buffer.

Added in v0.1.0
"""
from __future__ import annotations

import multiprocessing as mp
import os
import tempfile
import time
from typing import Callable, Optional

from .models import BenchmarkResult

#: Default size of the disk test file in MB. Bigger is more accurate (less
#: cache influence) but slower; ``--bench-disk-mb`` overrides it.
DEFAULT_DISK_MB = 256
_CHUNK = 4 * 1024 * 1024  # 4 MiB writes: large enough to be "sequential"


def _work_unit() -> int:
    """One fixed chunk of mixed integer/float work (~0.1 ms in CPython)."""
    acc = 0
    x = 1.0001
    for i in range(200):
        acc = (acc * 31 + i) % 1_000_003
        x = x * 1.0000001 + 0.5 / (i + 1)
    return acc + int(x)


def _cpu_worker(seconds: float) -> int:
    """Run work units for ``seconds`` and return how many completed.

    Module-level (not a closure) so it can be pickled for Windows'
    ``spawn`` start method, which re-imports this module in each child.
    """
    deadline = time.perf_counter() + seconds
    done = 0
    while time.perf_counter() < deadline:
        _work_unit()
        done += 1
    return done


def cpu_single(seconds: float = 2.0) -> float:
    """Single-core score: work units per second / 1000."""
    return round(_cpu_worker(seconds) / seconds / 1000, 1)


def cpu_multi(seconds: float = 2.0, processes: Optional[int] = None) -> float:
    """Multi-core score: total work units per second across all cores / 1000."""
    n = processes or os.cpu_count() or 1
    # "spawn" is the only start method on Windows; using it everywhere keeps
    # Linux results comparable and avoids fork-with-threads warnings.
    ctx = mp.get_context("spawn")
    with ctx.Pool(n) as pool:
        # Warm-up: spawning n Python interpreters takes ~1 s on Windows and
        # must not count against the measured window.
        pool.map(_cpu_worker, [0.01] * n)
        counts = pool.map(_cpu_worker, [seconds] * n)
    return round(sum(counts) / seconds / 1000, 1)


def _drop_cache(fd: int) -> None:
    """Ask the OS to evict a file from its page cache, where supported.

    Without this the read test measures RAM, not the disk, because the
    file was just written. Linux supports it via posix_fadvise. Windows
    has no equivalent from pure Python, so read speed there is likely to
    be inflated by caching; the README says so.
    """
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def disk_sequential(size_mb: int = DEFAULT_DISK_MB, directory: Optional[str] = None) -> tuple[float, float]:
    """Return (read MB/s, write MB/s) for a temp file of ``size_mb``."""
    # Random bytes defeat drive/filesystem compression, which would
    # otherwise make zero-filled writes look impossibly fast.
    block = os.urandom(_CHUNK)
    chunks = max(1, size_mb * 1024 * 1024 // _CHUNK)
    fd, path = tempfile.mkstemp(prefix="pcdiag-bench-", suffix=".tmp", dir=directory)
    try:
        t0 = time.perf_counter()
        with os.fdopen(fd, "wb", buffering=0) as fh:
            for _ in range(chunks):
                fh.write(block)
            os.fsync(fh.fileno())  # include the flush to disk, not just to cache
        write_s = time.perf_counter() - t0

        with open(path, "rb", buffering=0) as fh:
            _drop_cache(fh.fileno())
            t0 = time.perf_counter()
            while fh.read(_CHUNK):
                pass
            read_s = time.perf_counter() - t0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    total_mb = chunks * _CHUNK / (1024 * 1024)
    return round(total_mb / max(read_s, 1e-6), 1), round(total_mb / max(write_s, 1e-6), 1)


def memory_copy(size_mb: int = 64, repeats: int = 10) -> float:
    """Memory copy throughput in MB/s (bytes copied, not read+written)."""
    src = bytearray(size_mb * 1024 * 1024)
    dst = bytearray(len(src))
    dst[:] = src  # warm-up: touch every page so first-use page faults aren't timed
    t0 = time.perf_counter()
    for _ in range(repeats):
        memoryview(dst)[:] = memoryview(src)  # a single C-level memcpy
    elapsed = time.perf_counter() - t0
    return round(size_mb * repeats / max(elapsed, 1e-6), 1)


def run_benchmark(disk_mb: int = DEFAULT_DISK_MB, cpu_seconds: float = 2.0,
                  on_error: Optional[Callable[[str], None]] = None) -> BenchmarkResult:
    """Run all benchmarks. Never raises.

    A part that fails leaves its fields as None and, if ``on_error`` is
    given, reports why (e.g. ``snapshot.add_error`` bound to "benchmark").
    ``disk_mb=0`` skips the disk test.
    """
    started = time.perf_counter()
    result = BenchmarkResult()

    def attempt(label: str, fn: Callable[[], None]) -> None:
        """Run one sub-benchmark, turning any exception into a report."""
        try:
            fn()
        except Exception as exc:  # e.g. multiprocessing blocked by security software, temp drive full
            if on_error is not None:
                on_error(f"{label} failed: {type(exc).__name__}: {exc}")

    def _single() -> None:
        """Single-core CPU part."""
        result.cpu_single_score = cpu_single(cpu_seconds)

    def _multi() -> None:
        """Multi-core CPU part."""
        result.cpu_multi_score = cpu_multi(cpu_seconds)

    def _disk() -> None:
        """Sequential disk part."""
        result.disk_read_mbps, result.disk_write_mbps = disk_sequential(disk_mb)

    def _mem() -> None:
        """Memory-copy part."""
        result.memory_mbps = memory_copy()

    attempt("cpu single-core", _single)
    attempt("cpu multi-core", _multi)
    if disk_mb > 0:
        attempt("disk", _disk)
    attempt("memory", _mem)
    result.duration_seconds = round(time.perf_counter() - started, 1)
    return result
