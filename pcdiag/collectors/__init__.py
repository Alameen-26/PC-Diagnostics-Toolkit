"""Collectors: read the live system into a :class:`~pcdiag.models.SystemSnapshot`.

One module per area (``os_info``, ``cpu``, ``memory``, ``disks``,
``disk_io``, ``gpu``, ``temperatures``, ``battery``, ``processes``,
``startup``). Each exposes ``collect(snapshot, ...)``, returns model objects
and never raises: problems are recorded with ``snapshot.add_error``.
Everything external goes through :mod:`pcdiag.collectors._shell`.

Use :func:`collect_snapshot` for a full scan.

Added in v0.1.0
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..models import SystemSnapshot
from . import battery, cpu, disk_io, disks, gpu, memory, os_info, processes, startup, temperatures

__all__ = ["collect_snapshot", "safe_collect"]


def safe_collect(snapshot: SystemSnapshot, name: str, fn: Callable[..., Any], default: Any, **kwargs: Any) -> Any:
    """Call ``fn(snapshot, **kwargs)``; on any exception, record it and return ``default``.

    Each collector already catches its own expected failures; this is the
    last line of defence so one buggy collector can't abort a whole scan.
    """
    try:
        return fn(snapshot, **kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        snapshot.add_error(name, f"unexpected {type(exc).__name__}: {exc}")
        return default


def collect_snapshot(include_benchmark: bool = False, quick: bool = False,
                     benchmark_disk_mb: int = 256) -> SystemSnapshot:
    """Run every collector and return a (possibly partial) snapshot.

    ``quick`` shortens the sampling windows from 1 s to 0.25 s and skips
    smartctl, for tests and fast previews; readings are noisier.
    ``include_benchmark`` adds :func:`pcdiag.benchmark.run_benchmark` at
    the end (roughly 10-20 s), with a ``benchmark_disk_mb`` test file.

    Two phases, each run in threads:

    1. The sampling collectors (cpu, disk_io, processes). They mostly sleep
       between counter reads, so they overlap well.
    2. The rest, which launch PowerShell/powercfg/smartctl. These run
       *after* sampling on purpose: starting powershell.exe costs real CPU
       and disk, which would make the PC look busier than it is.
    """
    snap = SystemSnapshot()
    interval = 0.25 if quick else 1.0

    # snapshot attribute -> (error label, collector, default on failure, kwargs)
    sampling = {
        "cpu": ("cpu", cpu.collect, None, {"interval": interval}),
        "disk_io": ("disk_io", disk_io.collect, [], {"interval": interval}),
        "top_processes": ("processes", processes.collect, [], {"interval": interval}),
    }
    others = {
        "os": ("os", os_info.collect, None, {}),
        "memory": ("memory", memory.collect, None, {}),
        "disks": ("disks", disks.collect, [], {"use_smartctl": not quick}),
        "gpus": ("gpu", gpu.collect, [], {}),
        "temperatures": ("temperatures", temperatures.collect, [], {}),
        "battery": ("battery", battery.collect, None, {}),
        "startup_items": ("startup", startup.collect, [], {}),
    }
    for phase in (sampling, others):
        with ThreadPoolExecutor(max_workers=len(phase)) as pool:
            futures = {
                attr: pool.submit(safe_collect, snap, label, fn, default, **kw)
                for attr, (label, fn, default, kw) in phase.items()
            }
            for attr, fut in futures.items():
                setattr(snap, attr, fut.result())

    if include_benchmark:
        from ..benchmark import run_benchmark  # imported lazily: multiprocessing is heavy

        snap.benchmark = safe_collect(
            snap, "benchmark",
            lambda s: run_benchmark(disk_mb=benchmark_disk_mb, on_error=lambda msg: s.add_error("benchmark", msg)),
            None,
        )

    # Threads append errors in completion order; sort for stable output.
    snap.errors.sort()
    return snap
