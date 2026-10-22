"""Compare two saved snapshots, e.g. this month's scan against last month's.

Prints key metrics side by side with the change and whether it got better
or worse, plus startup items that appeared or disappeared. Metrics missing
from either snapshot are shown as "?" rather than skipped, so the table
shape is stable across runs.

Added in v0.1.0
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import SystemSnapshot
from .terminal import fmt


@dataclass
class Metric:
    """One comparable number: how to read it and which direction is good."""

    label: str
    get: Callable[[SystemSnapshot], Optional[float]]
    unit: str = ""
    higher_is_better: Optional[bool] = None  # None = neutral (no verdict)


def _sys(s: SystemSnapshot):
    """The system drive, or None."""
    return s.system_drive


METRICS = [
    Metric("CPU usage", lambda s: s.cpu and s.cpu.total_usage_percent, "%", False),
    Metric("RAM in use", lambda s: s.memory and s.memory.percent, "%", False),
    Metric("RAM installed", lambda s: s.memory and s.memory.total_gb, " GB", True),
    Metric("Swap used", lambda s: s.memory and s.memory.swap_used_gb, " GB", False),
    Metric("System drive used", lambda s: _sys(s) and _sys(s).percent, "%", False),
    Metric("System drive free", lambda s: _sys(s) and _sys(s).free_gb, " GB", True),
    Metric("System drive wear", lambda s: _sys(s) and _sys(s).smart and _sys(s).smart.wear_percent, "%", False),
    Metric("Reallocated sectors (all)", lambda s: _sum_smart(s, "reallocated_sectors"), "", False),
    Metric("Pending sectors (all)", lambda s: _sum_smart(s, "pending_sectors"), "", False),
    Metric("Battery wear", lambda s: s.battery and s.battery.wear_percent, "%", False),
    Metric("Battery full charge", lambda s: s.battery and s.battery.full_charge_capacity_mwh, " mWh", True),
    Metric("Startup items enabled", lambda s: float(sum(1 for i in s.startup_items if i.enabled is not False)), "", False),
    Metric("Hottest sensor", lambda s: max((t.current_c for t in s.temperatures if t.current_c is not None), default=None), " C", False),
    Metric("Bench CPU single", lambda s: s.benchmark and s.benchmark.cpu_single_score, "", True),
    Metric("Bench CPU multi", lambda s: s.benchmark and s.benchmark.cpu_multi_score, "", True),
    Metric("Bench disk read", lambda s: s.benchmark and s.benchmark.disk_read_mbps, " MB/s", True),
    Metric("Bench disk write", lambda s: s.benchmark and s.benchmark.disk_write_mbps, " MB/s", True),
    Metric("Bench memory", lambda s: s.benchmark and s.benchmark.memory_mbps, " MB/s", True),
]


def _sum_smart(s: SystemSnapshot, attr: str) -> Optional[float]:
    """Sum a SMART counter over all disks that report it (None if none do)."""
    vals = [getattr(d.smart, attr) for d in s.disks if d.smart and getattr(d.smart, attr) is not None]
    return float(sum(vals)) if vals else None


def _value(metric: Metric, snap: SystemSnapshot) -> Optional[float]:
    """Evaluate a metric safely. The ``a and b`` chains can yield objects or
    False instead of None, so anything that isn't a number becomes None."""
    try:
        v = metric.get(snap)
    except Exception:
        return None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def compare_rows(old: SystemSnapshot, new: SystemSnapshot) -> list[dict]:
    """Return one dict per metric: label, old, new, delta, verdict."""
    rows = []
    for m in METRICS:
        a, b = _value(m, old), _value(m, new)
        delta = None if a is None or b is None else b - a
        verdict = ""
        # 1% relative tolerance so benchmark noise isn't called a change.
        if delta is not None and m.higher_is_better is not None and abs(delta) > max(abs(a) * 0.01, 1e-9):
            verdict = "better" if (delta > 0) == m.higher_is_better else "worse"
        rows.append({"label": m.label, "unit": m.unit, "old": a, "new": b, "delta": delta, "verdict": verdict})
    return rows


def compare_lines(old: SystemSnapshot, new: SystemSnapshot) -> list[str]:
    """Human-readable comparison table (ASCII only, see terminal.py)."""
    def when(s: SystemSnapshot) -> str:
        """Local-time label for a snapshot."""
        return s.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")

    lines = [f"Comparing {when(old)}  ->  {when(new)}", "",
             f"  {'Metric':<26}{'Old':>14}{'New':>14}{'Change':>12}  Verdict",
             "  " + "-" * 74]
    for r in compare_rows(old, new):
        delta = "" if r["delta"] is None else ("+" if r["delta"] > 0 else "") + fmt(r["delta"])
        lines.append(f"  {r['label']:<26}{fmt(r['old'], r['unit']):>14}{fmt(r['new'], r['unit']):>14}"
                     f"{delta:>12}  {r['verdict']}")
    before = {i.name for i in old.startup_items}
    after = {i.name for i in new.startup_items}
    if after - before:
        lines += ["", "New startup items: " + ", ".join(sorted(after - before))]
    if before - after:
        lines += ["", "Removed startup items: " + ", ".join(sorted(before - after))]
    return lines
