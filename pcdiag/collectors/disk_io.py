"""Disk I/O collector: read/write throughput and how busy each disk is.

Takes two counter samples ``interval`` seconds apart and reports the rate in
between. A disk that is ~100% busy while moving only a few MB/s is the
classic sign of an HDD boot drive thrashing (or swapping).

Added in v0.1.0
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

import psutil

from ..models import DiskIOStats, SystemSnapshot
from ._util import MIB

# Linux exposes partitions and virtual devices alongside real disks; these
# would double-count traffic or add noise.
_SKIP_PREFIXES = ("loop", "ram", "zram", "fd", "sr")
_PARTITION_RE = re.compile(r"^(sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+)\d+$|^(nvme\d+n\d+|mmcblk\d+)p\d+$")


def _keep(name: str) -> bool:
    """True for whole physical disks (PhysicalDrive0, sda, nvme0n1)."""
    return not name.startswith(_SKIP_PREFIXES) and not _PARTITION_RE.match(name)


def compute_rates(before: dict[str, Any], after: dict[str, Any], seconds: float) -> list[DiskIOStats]:
    """Build per-disk and combined stats from two ``perdisk=True`` samples.

    The first element is the combined entry (``device=None``); its
    ``busy_percent`` is the busiest single disk, because averaging a pegged
    HDD with an idle SSD would hide the bottleneck.
    """
    seconds = max(seconds, 1e-6)
    per_disk: list[DiskIOStats] = []
    for name in sorted(after):
        if name not in before or not _keep(name):
            continue
        a, b = after[name], before[name]
        busy: Optional[float] = None
        # busy_time (ms spent doing I/O) exists on Linux/BSD only; psutil on
        # Windows doesn't provide it, so busy_percent stays None there.
        if hasattr(a, "busy_time") and hasattr(b, "busy_time"):
            busy = round(min(100.0, (a.busy_time - b.busy_time) / (seconds * 1000) * 100), 1)
        per_disk.append(DiskIOStats(
            device=name,
            read_bytes=a.read_bytes - b.read_bytes,
            write_bytes=a.write_bytes - b.write_bytes,
            read_count=a.read_count - b.read_count,
            write_count=a.write_count - b.write_count,
            read_mbps=round((a.read_bytes - b.read_bytes) / MIB / seconds, 2),
            write_mbps=round((a.write_bytes - b.write_bytes) / MIB / seconds, 2),
            busy_percent=busy,
            sample_seconds=round(seconds, 2),
        ))
    if not per_disk:
        return []
    busies = [d.busy_percent for d in per_disk if d.busy_percent is not None]
    total = DiskIOStats(
        device=None,
        read_bytes=sum(d.read_bytes or 0 for d in per_disk),
        write_bytes=sum(d.write_bytes or 0 for d in per_disk),
        read_count=sum(d.read_count or 0 for d in per_disk),
        write_count=sum(d.write_count or 0 for d in per_disk),
        read_mbps=round(float(sum(d.read_mbps or 0 for d in per_disk)), 2),
        write_mbps=round(float(sum(d.write_mbps or 0 for d in per_disk)), 2),
        busy_percent=max(busies) if busies else None,
        sample_seconds=round(seconds, 2),
    )
    return [total] + per_disk


def collect(snapshot: SystemSnapshot, interval: float = 1.0) -> list[DiskIOStats]:
    """Sample disk counters twice, ``interval`` seconds apart."""
    try:
        before = psutil.disk_io_counters(perdisk=True)
        t0 = time.perf_counter()
        time.sleep(interval)
        after = psutil.disk_io_counters(perdisk=True)
        elapsed = time.perf_counter() - t0
    except Exception as exc:
        snapshot.add_error("disk_io", f"I/O counters unavailable: {exc}")
        return []
    if not before or not after:
        # Windows quirk: counters can be disabled (old "diskperf -n"), and
        # some containers hide them entirely.
        snapshot.add_error("disk_io", "I/O counters not exposed on this system")
        return []
    return compute_rates(before, after, elapsed)
