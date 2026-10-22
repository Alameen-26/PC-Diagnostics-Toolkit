"""Process collector: the heaviest processes by CPU and by memory.

Per-process CPU usage needs two readings, so every process is "primed"
once, the collector waits ``interval`` seconds, then reads again. The result
is the union of the top ``top_n`` by CPU and the top ``top_n`` by memory,
sorted by CPU, so a memory hog that's idle still shows up.

Added in v0.1.0
"""
from __future__ import annotations

import time

import psutil

from ..models import ProcessInfo, SystemSnapshot
from ._util import MIB

#: Pseudo-processes whose numbers are bookkeeping, not real load. Windows'
#: "System Idle Process" (pid 0) shows idle time as ~100% CPU per core.
_SKIP_NAMES = {"system idle process", "idle"}


def collect(snapshot: SystemSnapshot, top_n: int = 10, interval: float = 1.0) -> list[ProcessInfo]:
    """Return the top processes by CPU and memory."""
    try:
        procs = list(psutil.process_iter(["pid", "name", "username"]))
        for p in procs:
            try:
                p.cpu_percent(None)  # first call always returns 0.0; it starts the clock
            except (psutil.Error, OSError):
                pass
        time.sleep(interval)
    except Exception as exc:
        snapshot.add_error("processes", f"process list unavailable: {exc}")
        return []

    results: list[ProcessInfo] = []
    for p in procs:
        try:
            name = p.info.get("name") or ""
            if p.pid == 0 or name.lower() in _SKIP_NAMES:
                continue
            with p.oneshot():  # batch the underlying OS calls per process
                cpu = p.cpu_percent(None)  # % of one core; can exceed 100 on multi-core
                mem = p.memory_info().rss
                mem_pct = p.memory_percent()
            results.append(ProcessInfo(
                pid=p.pid, name=name, cpu_percent=round(cpu, 1),
                memory_mb=round(mem / MIB, 1), memory_percent=round(mem_pct, 2),
                username=p.info.get("username"),
            ))
        except psutil.AccessDenied:
            # Windows: protected/system processes refuse access without admin.
            continue
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue  # exited between the two samples
        except Exception:
            continue  # one odd process must not sink the whole collector
    if not results:
        snapshot.add_error("processes", "no process details readable")
        return []

    by_cpu = sorted(results, key=lambda r: r.cpu_percent or 0, reverse=True)[:top_n]
    by_mem = sorted(results, key=lambda r: r.memory_mb or 0, reverse=True)[:top_n]
    merged = {r.pid: r for r in by_cpu + by_mem}
    return sorted(merged.values(), key=lambda r: (r.cpu_percent or 0, r.memory_mb or 0), reverse=True)
