# pcdiag/analysis/sustained.py
# Added in v0.2.0
"""Sustained-pressure checks: judge how the PC behaved *over time*.

A single snapshot can catch a PC mid-spike (an update unpacking, a browser
tab loading) and call it overloaded, or catch it idle and miss a real
problem. ``pcdiag scan --monitor S`` records a series of timestamped samples
over S seconds. This module reads those samples and decides whether a
pressure is **sustained** (worth acting on) or a **brief spike** (normal).

Layout
------
1. **Adapter** (``_monitor_view``). When this module was written, the
   ``MonitorSession`` model was still being built in ``pcdiag/models.py``,
   so nothing here imports it. The adapter duck-types the session and turns
   it into a small, stable ``MonitorView``. If the real names differ from
   the assumed ones (listed in ``_SESSION_ATTRS``, ``_SAMPLES_ATTRS``,
   ``_TIME_ATTRS`` and ``_METRIC_ATTRS``), only those tuples need to change.
2. **Statistics** (``series_stats``): mean, max, p95, % of time above a
   threshold and the longest continuous streak above it. ``None`` samples
   mean "no reading": they are left out of every statistic, and they break a
   streak because we can't claim the value stayed high across a gap.
3. **Confidence** (``window_confidence``): how much the window can be
   trusted, based on its length and the number of valid readings.
4. **Rules**, registered like every other rule: ``memory.sustained``,
   ``cpu.sustained``, ``storage.disk_filling``,
   ``storage.disk_busy_sustained`` and ``thermal.cpu_sustained``.
5. **Overlap resolution** (``resolve_overlaps``), called by ``analyze``. A
   sustained finding *supersedes* the single-reading finding for the same
   metric, so the report never shows both.

No monitor data means these rules return nothing. The report's "Over time"
section explains how to collect it. Missing data is never read as
"healthy".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from ..models import SystemSnapshot
from .findings import Category, Confidence, Finding, Severity
from .registry import rule
from .rules import CPU_TEMP_CRITICAL_C, CPU_TEMP_WARN_C, SYSTEM_DISK_WARN_PERCENT, _os_name

# ---------------------------------------------------------------------------
# Thresholds. Each one is explained so a reviewer can argue with it.
# ---------------------------------------------------------------------------

# RAM: above 85% used, Windows starts trimming working sets and paging to
# disk. Being there for most of the window means the PC lives in that state.
RAM_SUSTAINED_THRESHOLD_PERCENT = 85.0
RAM_SUSTAINED_TIME_PERCENT = 50.0      # >= half the window above the threshold -> warning
RAM_SUSTAINED_P95_PERCENT = 90.0       # or the top 5% of readings at 90%+ -> warning
RAM_BORDERLINE_TIME_PERCENT = 20.0     # 20-50% of the time above -> info ("borderline")
RAM_BORDERLINE_P95_PERCENT = 85.0      # or p95 just over the threshold -> info

# CPU: >= 90% is "pegged"; there is no headroom left for the foreground app.
CPU_SUSTAINED_THRESHOLD_PERCENT = 90.0
CPU_SUSTAINED_TIME_PERCENT = 50.0      # >= half the window pegged -> warning
CPU_SUSTAINED_STREAK_S = 60.0          # or one unbroken minute pegged -> warning (N = 60 s)
CPU_BORDERLINE_TIME_PERCENT = 20.0     # pegged 20-50% of the time -> info

# A "long streak" upgrades the wording (not the severity) of a RAM or CPU
# warning: two unbroken minutes is clearly not a blip.
LONG_STREAK_S = 120.0

# Disk busy (active time): same shape as CPU. A disk at 90%+ makes
# everything wait on I/O.
DISK_BUSY_SUSTAINED_THRESHOLD_PERCENT = 90.0
DISK_BUSY_SUSTAINED_TIME_PERCENT = 50.0
DISK_BUSY_SUSTAINED_STREAK_S = 60.0

# CPU temperature: reuses the snapshot rule's 80 C warn / 90 C critical.
TEMP_SUSTAINED_TIME_PERCENT = 50.0     # hot for >= half the window -> warning
TEMP_CRITICAL_P95_C = CPU_TEMP_CRITICAL_C  # ...and p95 at 90 C+ -> critical

# Disk filling. A trend only counts as "meaningful" when every one of these
# holds; otherwise it is treated as noise (temp files come and go).
DISK_FILL_MIN_WINDOW_S = 60.0          # under a minute, extrapolating is guesswork
DISK_FILL_MIN_SAMPLES = 5              # need enough points to fit a line
DISK_FILL_MIN_DROP_GB = 0.5            # free space must fall by at least 0.5 GB...
DISK_FILL_MIN_R2 = 0.7                 # ...and fall steadily (linear fit R^2 >= 0.7)
DISK_FILL_CRITICAL_HOURS = 1.0         # projected full within 1 h -> critical
DISK_FILL_WARN_HOURS = 24.0            # within a day -> warning; later -> info

# Confidence tiers for a monitor window.
CONF_LOW_MAX_S = 60.0                  # < 60 s ...
CONF_LOW_MIN_SAMPLES = 10              # ... or < 10 valid readings -> LOW
CONF_HIGH_MIN_S = 300.0                # >= 5 min -> HIGH; in between -> MEDIUM

MONITOR_HINT = "pcdiag scan --monitor 300"

# ---------------------------------------------------------------------------
# 1. Adapter. These are ASSUMED MonitorSession names; see the module docstring.
# ---------------------------------------------------------------------------

# Attribute on SystemSnapshot that holds the session (first match wins).
_SESSION_ATTRS = ("monitor", "monitor_session")
# Attribute on the session that holds the list of samples.
_SAMPLES_ATTRS = ("samples",)
# Per-sample time: a datetime, ISO string, or seconds (absolute or relative).
_TIME_ATTRS = ("timestamp", "time", "t", "elapsed_s", "offset_s")
# Session-level metadata (all optional).
_START_ATTRS = ("started_at", "start", "start_time")
_DURATION_ATTRS = ("duration_s", "duration_seconds")
_INTERVAL_ATTRS = ("interval_s", "interval_seconds")
# metric key -> candidate sample attribute names.
_METRIC_ATTRS: dict[str, tuple[str, ...]] = {
    "cpu": ("cpu_percent", "cpu_usage_percent", "total_usage_percent"),
    "memory": ("memory_percent", "ram_percent", "mem_percent"),
    "disk_free": ("system_disk_free_gb", "disk_free_gb", "free_gb"),
    "disk_busy": ("disk_busy_percent", "busy_percent"),
    "cpu_temp": ("cpu_temp_c", "cpu_temperature_c", "temperature_c"),
}

# Human labels and units, used by findings and by the report.
METRIC_LABELS = {
    "cpu": ("CPU usage", "%"),
    "memory": ("Memory (RAM) used", "%"),
    "disk_used": ("System drive used", "%"),
    "disk_free": ("System drive free", " GB"),
    "disk_busy": ("Disk busy (active time)", "%"),
    "cpu_temp": ("CPU temperature", " °C"),
}


def _get(obj: Any, names: Iterable[str]) -> Any:
    """Return the first present, non-None attribute (or dict key) from ``names``."""
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _to_seconds(v: Any) -> Optional[float]:
    """Convert a sample time (datetime, ISO string, or number) to epoch-like seconds."""
    if isinstance(v, datetime):
        return v.timestamp()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _to_float(v: Any) -> Optional[float]:
    """Numeric reading or None. NaN and non-numbers count as "no reading"."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if math.isnan(f) else f


@dataclass
class MonitorView:
    """Normalised, read-only view of a monitor session.

    ``times`` are seconds from the first sample (0.0 first). ``series`` maps a
    metric key to one value per sample, with ``None`` where the sample had
    no reading. A metric that never appears in any sample is absent from
    ``series``.
    """
    start: Optional[datetime]
    times: list[float]
    series: dict[str, list[Optional[float]]]
    duration_s: float
    interval_s: Optional[float]

    @property
    def sample_count(self) -> int:
        """Number of samples in the window (including ones with no readings)."""
        return len(self.times)


def _monitor_view(snapshot: SystemSnapshot) -> Optional[MonitorView]:
    """Build a ``MonitorView`` from ``snapshot.monitor``, or None if there is no usable data.

    It reads ``getattr(snapshot, 'monitor', None)`` because the field is optional
    and may not exist on older snapshots. Samples without a usable time are
    dropped; we can't place them on the timeline.
    """
    session = _get(snapshot, _SESSION_ATTRS)
    if session is None:
        return None
    samples = _get(session, _SAMPLES_ATTRS)
    if samples is None and isinstance(session, (list, tuple)):
        samples = session  # a bare list of samples is accepted too
    if not samples:
        return None

    timed: list[tuple[float, Any]] = []
    for smp in samples:
        t = _to_seconds(_get(smp, _TIME_ATTRS))
        if t is not None:
            timed.append((t, smp))
    if not timed:
        return None
    timed.sort(key=lambda p: p[0])
    t0 = timed[0][0]
    times = [t - t0 for t, _ in timed]

    series: dict[str, list[Optional[float]]] = {}
    for key, names in _METRIC_ATTRS.items():
        vals = [_to_float(_get(smp, names)) for _, smp in timed]
        if any(v is not None for v in vals):
            series[key] = vals
        elif any(_has_attr(smp, names) for _, smp in timed):
            series[key] = vals  # field exists but was always None: keep it so the report can say "no readings"

    # Interval: the session's own value, otherwise the median gap between samples.
    interval = _to_float(_get(session, _INTERVAL_ATTRS))
    if interval is None and len(times) >= 2:
        gaps = sorted(b - a for a, b in zip(times, times[1:]))
        interval = gaps[len(gaps) // 2]
    # Duration: the session's own value, otherwise first-to-last plus one
    # interval (each sample stands for one interval of time).
    duration = _to_float(_get(session, _DURATION_ATTRS))
    if duration is None:
        duration = times[-1] + (interval or 0.0)

    start_raw = _get(session, _START_ATTRS)
    if start_raw is None:
        start_raw = _get(timed[0][1], _TIME_ATTRS)
    start = start_raw if isinstance(start_raw, datetime) else None
    if start is None and isinstance(start_raw, str):
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            start = None
    return MonitorView(start=start, times=times, series=series, duration_s=duration, interval_s=interval)


def _has_attr(obj: Any, names: Iterable[str]) -> bool:
    """True when ``obj`` defines any of ``names``, even with a None value."""
    return any((n in obj) if isinstance(obj, dict) else hasattr(obj, n) for n in names)


# ---------------------------------------------------------------------------
# 2. Statistics
# ---------------------------------------------------------------------------

@dataclass
class SeriesStats:
    """Summary of one metric over the window.

    ``pct_above`` is the share of *valid* readings past the threshold (above,
    or below when ``above=False``, as for free space). ``longest_streak_s``
    is the longest run of consecutive valid readings past it, in seconds
    (each reading counts for one interval).
    """
    key: str
    threshold: float
    above: bool
    sample_count: int
    valid_count: int
    mean: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    p95: Optional[float] = None
    pct_above: Optional[float] = None
    longest_streak_s: float = 0.0
    values: list[Optional[float]] = field(default_factory=list, repr=False)

    @property
    def missing_count(self) -> int:
        """Samples that had no reading for this metric."""
        return self.sample_count - self.valid_count


def _p95(vals: list[float]) -> float:
    """95th percentile, nearest-rank method (always an actual reading)."""
    s = sorted(vals)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def series_stats(view: MonitorView, key: str, threshold: float, above: bool = True,
                 values: Optional[list[Optional[float]]] = None) -> SeriesStats:
    """Compute mean/max/p95/% past threshold/longest streak for one metric.

    ``values`` overrides ``view.series[key]`` (used for derived series such as
    disk used %). With no valid readings, only the counts are filled in.
    """
    vals = values if values is not None else view.series.get(key, [None] * view.sample_count)
    valid = [v for v in vals if v is not None]
    st = SeriesStats(key=key, threshold=threshold, above=above,
                     sample_count=len(vals), valid_count=len(valid), values=list(vals))
    if not valid:
        return st
    past = (lambda v: v > threshold) if above else (lambda v: v < threshold)
    st.mean = sum(valid) / len(valid)
    st.max, st.min = max(valid), min(valid)
    st.p95 = _p95(valid)
    st.pct_above = 100.0 * sum(1 for v in valid if past(v)) / len(valid)

    # Longest streak: consecutive samples past the threshold. A None sample
    # breaks the streak because we don't know what happened during the gap.
    step = view.interval_s or 0.0
    best = run_start = None
    best_len = 0.0
    for i, v in enumerate(vals):
        if v is not None and past(v):
            if run_start is None:
                run_start = i
            length = view.times[i] - view.times[run_start] + step
            if length > best_len:
                best_len, best = length, run_start
        else:
            run_start = None
    st.longest_streak_s = best_len if best is not None else 0.0
    return st


# ---------------------------------------------------------------------------
# 3. Confidence and plain-language uncertainty
# ---------------------------------------------------------------------------

def window_confidence(duration_s: float, valid_count: int) -> Confidence:
    """LOW under 60 s or under 10 readings, MEDIUM for 1-5 min, HIGH for 5 min or more."""
    if duration_s < CONF_LOW_MAX_S or valid_count < CONF_LOW_MIN_SAMPLES:
        return Confidence.LOW
    if duration_s < CONF_HIGH_MIN_S:
        return Confidence.MEDIUM
    return Confidence.HIGH


def fmt_duration(seconds: float) -> str:
    """Short duration such as '45 s', '3 min 20 s' or '1 h 5 min'."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} min" + (f" {r} s" if r else "")
    h, r = divmod(s, 3600)
    return f"{h} h" + (f" {r // 60} min" if r >= 60 else "")


def confidence_explanation(conf: Confidence, duration_s: float, valid_count: int) -> str:
    """Explain what a confidence tier means for this window, in plain words."""
    window = f"{fmt_duration(duration_s)} and {valid_count} readings"
    if conf is Confidence.LOW:
        return (f"Low confidence: only {window} (under 1 minute or under 10 readings). Treat this as a hint, "
                f"not a diagnosis; a short window easily mistakes a spike for a trend. Run `{MONITOR_HINT}` "
                "for a reliable picture.")
    if conf is Confidence.MEDIUM:
        return (f"Medium confidence: {window}. Good enough to tell a spike from steady load, but a 5-minute "
                f"run (`{MONITOR_HINT}`) would confirm it.")
    return (f"High confidence: {window}. That's long enough to show how this PC really behaves under what you "
            "were doing during the scan. Results reflect that workload only.")


def _uncertainty(conf: Confidence, st: SeriesStats, duration_s: float, extra: str = "") -> str:
    """Plain-language uncertainty note attached to every sustained finding."""
    note = confidence_explanation(conf, duration_s, st.valid_count)
    if st.missing_count:
        note += f" {st.missing_count} of {st.sample_count} samples had no reading and were left out."
    return (note + (" " + extra if extra else "")).strip()


def _evidence(view: MonitorView, st: SeriesStats, conf: Confidence, unit: str, extra: str = "") -> dict:
    """Standard evidence block: every sustained finding carries all of these keys."""
    r = lambda v: None if v is None else round(v, 1)  # noqa: E731  (one decimal is plenty for a report)
    return {
        "metric": st.key,
        "unit": unit.strip(),
        "duration_s": round(view.duration_s, 1),
        "sample_count": st.sample_count,
        "valid_samples": st.valid_count,
        "mean": r(st.mean), "max": r(st.max), "p95": r(st.p95),
        "threshold": st.threshold,
        "pct_above_threshold": r(st.pct_above),
        "longest_streak_s": round(st.longest_streak_s, 1),
        "uncertainty": _uncertainty(conf, st, view.duration_s, extra),
    }


def _stats_line(st: SeriesStats, unit: str, label: str = "above") -> str:
    """'Averaged 88%, p95 93%, max 97%; above 85% for 70% of the time (longest 3 min).'"""
    return (f"Averaged {st.mean:.0f}{unit}, p95 {st.p95:.0f}{unit}, max {st.max:.0f}{unit}; "
            f"{label} {st.threshold:.0f}{unit} for {st.pct_above:.0f}% of the time "
            f"(longest unbroken stretch {fmt_duration(st.longest_streak_s)}).")


def _top_processes(s: SystemSnapshot, by: str, n: int = 3) -> list[str]:
    """Names of the heaviest processes from the snapshot (``by`` = 'cpu_percent' or 'memory_mb')."""
    procs = [p for p in s.top_processes if getattr(p, by, None) is not None and p.name]
    procs.sort(key=lambda p: getattr(p, by), reverse=True)
    return [p.name for p in procs[:n]]


# ---------------------------------------------------------------------------
# 4. Rules
# ---------------------------------------------------------------------------

@rule("sustained_memory")
def sustained_memory(s: SystemSnapshot) -> Iterable[Finding]:
    """RAM above 85% for most of the monitor window. Supersedes ``memory.pressure``."""
    view = _monitor_view(s)
    if view is None or "memory" not in view.series:
        return []
    st = series_stats(view, "memory", RAM_SUSTAINED_THRESHOLD_PERCENT)
    if st.valid_count == 0:
        return []  # every sample was None: unknown, so say nothing rather than "fine"
    conf = window_confidence(view.duration_s, st.valid_count)
    ev = _evidence(view, st, conf, "%")
    ev["supersedes"] = "memory.pressure"
    stats = _stats_line(st, "%")
    procs = _top_processes(s, "memory_mb")
    proc_text = f" Biggest memory users at scan time: {', '.join(procs)}." if procs else ""
    long = st.longest_streak_s >= LONG_STREAK_S

    if st.pct_above >= RAM_SUSTAINED_TIME_PERCENT or st.p95 >= RAM_SUSTAINED_P95_PERCENT:
        title = "Memory stays nearly full" + (f" ({fmt_duration(st.longest_streak_s)} straight)" if long else "")
        return [Finding(
            id="memory.sustained", title=title, severity=Severity.WARNING, category=Category.MEMORY,
            cause=(f"RAM was above {st.threshold:.0f}% for most of the {fmt_duration(view.duration_s)} window, so "
                   f"{_os_name(s)} keeps swapping to disk and everything slows down. {stats}"
                   + (" The long unbroken stretch shows this is the normal state, not a blip." if long else "")
                   + proc_text),
            fix=("Free: close browser tabs and apps you aren't using, disable startup apps you don't need, and "
                 "restart to clear leaks. Real fix: add RAM if it stays this high during normal work."),
            evidence=ev, confidence=conf, free_fix=True)]
    if st.pct_above >= RAM_BORDERLINE_TIME_PERCENT or st.p95 >= RAM_BORDERLINE_P95_PERCENT:
        return [Finding(
            id="memory.sustained", title="Memory is borderline at times", severity=Severity.INFO,
            category=Category.MEMORY,
            cause=f"RAM went past {st.threshold:.0f}% now and then, but not most of the time. {stats}{proc_text}",
            fix="Free: keep an eye on it; close heavy apps when you notice slowdowns.",
            evidence=ev, confidence=conf, free_fix=True)]
    return [Finding(
        id="memory.sustained", title="Memory had headroom over time", severity=Severity.OK,
        category=Category.MEMORY, cause=f"RAM stayed comfortably below {st.threshold:.0f}%. {stats}",
        fix="Nothing to do.", evidence=ev, confidence=conf)]


def _load_rule(s: SystemSnapshot, key: str, threshold: float, time_pct: float, streak_s: float,
               borderline_pct: Optional[float]) -> Optional[tuple[str, SeriesStats, Confidence, MonitorView]]:
    """Shared classifier for CPU and disk-busy: 'sustained', 'borderline', 'spike' or 'ok'.

    * **sustained**: past the threshold for >= ``time_pct`` of the time, or
      one unbroken streak of >= ``streak_s`` seconds.
    * **borderline**: past it for >= ``borderline_pct`` of the time (if given).
    * **spike**: hit the threshold at some point, but only briefly.
    * **ok**: never got there.
    Returns None when there's no usable data.
    """
    view = _monitor_view(s)
    if view is None or key not in view.series:
        return None
    st = series_stats(view, key, threshold)
    if st.valid_count == 0:
        return None
    conf = window_confidence(view.duration_s, st.valid_count)
    if st.pct_above >= time_pct or st.longest_streak_s >= streak_s:
        kind = "sustained"
    elif borderline_pct is not None and st.pct_above >= borderline_pct:
        kind = "borderline"
    elif st.max > threshold:
        kind = "spike"
    else:
        kind = "ok"
    return kind, st, conf, view


@rule("sustained_cpu")
def sustained_cpu(s: SystemSnapshot) -> Iterable[Finding]:
    """CPU pegged (>= 90%) for most of the window, or one long streak. Tells spikes apart. Supersedes ``cpu.busy``."""
    res = _load_rule(s, "cpu", CPU_SUSTAINED_THRESHOLD_PERCENT, CPU_SUSTAINED_TIME_PERCENT,
                     CPU_SUSTAINED_STREAK_S, CPU_BORDERLINE_TIME_PERCENT)
    if res is None:
        return []
    kind, st, conf, view = res
    ev = _evidence(view, st, conf, "%")
    ev["supersedes"] = "cpu.busy"
    ev["pattern"] = kind
    stats = _stats_line(st, "%")
    procs = _top_processes(s, "cpu_percent")
    proc_text = (f" Top CPU users at scan time: {', '.join(procs)}." if procs
                 else " No per-process data was collected, so the culprit is unknown; check Task Manager.")
    if kind == "sustained":
        long = st.longest_streak_s >= LONG_STREAK_S
        return [Finding(
            id="cpu.sustained",
            title="CPU is pegged most of the time" + (f" ({fmt_duration(st.longest_streak_s)} straight)" if long else ""),
            severity=Severity.WARNING, category=Category.CPU,
            cause=(f"This is sustained load, not a spike: the processor had no headroom left for what you're doing. "
                   f"{stats}{proc_text}"),
            fix=("Free: find the busy process in Task Manager (sort by CPU) and close, update or uninstall it; "
                 "scan for malware if you don't recognise it. Real fix: a faster CPU only if it's your own normal workload."),
            evidence=ev, confidence=conf, free_fix=True)]
    if kind == "borderline":
        return [Finding(
            id="cpu.sustained", title="CPU is busy for part of the time", severity=Severity.INFO,
            category=Category.CPU,
            cause=f"The CPU was pegged in several bursts, but not most of the time. {stats}{proc_text}",
            fix="Free: if it feels slow during those bursts, check which app is busy in Task Manager.",
            evidence=ev, confidence=conf, free_fix=True)]
    if kind == "spike":
        return [Finding(
            id="cpu.sustained", title="CPU spikes are brief, not sustained", severity=Severity.INFO,
            category=Category.CPU,
            cause=(f"The CPU hit {st.max:.0f}% but dropped back quickly. Short spikes are normal "
                   f"(apps opening, updates). {stats}"),
            fix="Nothing to do.", evidence=ev, confidence=conf)]
    return [Finding(
        id="cpu.sustained", title="CPU had headroom over time", severity=Severity.OK, category=Category.CPU,
        cause=f"The CPU never reached {st.threshold:.0f}%. {stats}", fix="Nothing to do.",
        evidence=ev, confidence=conf)]


@rule("sustained_disk_busy")
def sustained_disk_busy(s: SystemSnapshot) -> Iterable[Finding]:
    """Disk active time >= 90% most of the window (if sampled). Supersedes ``storage.disk_busy``.

    Disk busy is often None on Windows (psutil has no busy time there). With
    no readings, this rule says nothing.
    """
    res = _load_rule(s, "disk_busy", DISK_BUSY_SUSTAINED_THRESHOLD_PERCENT, DISK_BUSY_SUSTAINED_TIME_PERCENT,
                     DISK_BUSY_SUSTAINED_STREAK_S, None)
    if res is None:
        return []
    kind, st, conf, view = res
    ev = _evidence(view, st, conf, "%")
    ev["supersedes"] = "storage.disk_busy"
    ev["pattern"] = kind
    stats = _stats_line(st, "%")
    if kind == "sustained":
        return [Finding(
            id="storage.disk_busy_sustained", title="Disk is maxed out most of the time", severity=Severity.WARNING,
            category=Category.STORAGE,
            cause=f"The disk was nearly 100% busy for long stretches, so apps wait on it. {stats}",
            fix=("Free: check which process is reading or writing (Resource Monitor > Disk); pause indexing or sync "
                 "if it's them. Real fix: an SSD if the system runs from a hard drive."),
            evidence=ev, confidence=conf, free_fix=True)]
    if kind == "spike":
        return [Finding(
            id="storage.disk_busy_sustained", title="Disk activity spikes are brief", severity=Severity.INFO,
            category=Category.STORAGE, cause=f"The disk was busy only in short bursts, which is normal. {stats}",
            fix="Nothing to do.", evidence=ev, confidence=conf)]
    return [Finding(
        id="storage.disk_busy_sustained", title="Disk had headroom over time", severity=Severity.OK,
        category=Category.STORAGE, cause=f"Disk active time stayed below {st.threshold:.0f}%. {stats}",
        fix="Nothing to do.", evidence=ev, confidence=conf)]


@rule("sustained_cpu_temp")
def sustained_cpu_temp(s: SystemSnapshot) -> Iterable[Finding]:
    """CPU hot (>= 80 C) for most of the window. Supersedes ``thermal.cpu``.

    Temperatures are often None on Windows without Administrator rights. With
    no readings, the rule stays silent and the collector error explains it.
    """
    view = _monitor_view(s)
    if view is None or "cpu_temp" not in view.series:
        return []
    st = series_stats(view, "cpu_temp", float(CPU_TEMP_WARN_C))
    if st.valid_count == 0:
        return []
    conf = window_confidence(view.duration_s, st.valid_count)
    ev = _evidence(view, st, conf, " °C")
    ev["supersedes"] = "thermal.cpu"
    stats = _stats_line(st, " °C")
    fix = ("Free: clean dust from vents and fans, keep the vents clear (not on a bed or lap), and check the fan "
           "spins. Cheap: new thermal paste. Lasting fix: a better cooler or laptop cooling pad.")
    if st.pct_above >= TEMP_SUSTAINED_TIME_PERCENT:
        crit = st.p95 >= TEMP_CRITICAL_P95_C
        return [Finding(
            id="thermal.cpu_sustained",
            title="CPU runs dangerously hot for long periods" if crit else "CPU runs hot for long periods",
            severity=Severity.CRITICAL if crit else Severity.WARNING, category=Category.THERMAL,
            cause=f"The processor stayed hot for most of the window, so it likely slows itself down to cool off. {stats}",
            fix=fix, evidence=ev, confidence=conf, free_fix=True)]
    if st.max > st.threshold:
        return [Finding(
            id="thermal.cpu_sustained", title="CPU gets hot in short bursts", severity=Severity.INFO,
            category=Category.THERMAL, cause=f"Brief temperature peaks, which are normal under load. {stats}",
            fix="Nothing to do unless it gets worse; " + fix, evidence=ev, confidence=conf, free_fix=True)]
    return [Finding(
        id="thermal.cpu_sustained", title="CPU temperature stayed normal", severity=Severity.OK,
        category=Category.THERMAL, cause=f"Stayed below {st.threshold:.0f} °C. {stats}", fix="Nothing to do.",
        evidence=ev, confidence=conf)]


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope and R^2 of ``ys`` against ``xs``. Flat data gives (0.0, 0.0)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    return slope, (sxy * sxy) / (sxx * syy)


def disk_fill_trend(s: SystemSnapshot, view: Optional[MonitorView] = None) -> Optional[dict]:
    """Fit the system drive's free space over time. Returns None if not sampled.

    Result keys: ``slope_gb_per_hour`` (negative = filling), ``r2``,
    ``drop_gb``, ``latest_free_gb``, ``meaningful`` (all noise filters passed)
    and ``hours_to_full`` (None unless meaningful and filling).
    """
    view = view or _monitor_view(s)
    if view is None or "disk_free" not in view.series:
        return None
    pts = [(t, v) for t, v in zip(view.times, view.series["disk_free"]) if v is not None]
    if len(pts) < 2:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    slope_s, r2 = _linear_fit(xs, ys)
    latest = ys[-1]
    drop = ys[0] - latest
    meaningful = (view.duration_s >= DISK_FILL_MIN_WINDOW_S and len(pts) >= DISK_FILL_MIN_SAMPLES
                  and drop >= DISK_FILL_MIN_DROP_GB and r2 >= DISK_FILL_MIN_R2 and slope_s < 0)
    hours = (latest / (-slope_s) / 3600.0) if meaningful else None
    return {"slope_gb_per_hour": slope_s * 3600.0, "r2": r2, "drop_gb": drop, "latest_free_gb": latest,
            "meaningful": meaningful, "hours_to_full": hours}


def _disk_used_values(s: SystemSnapshot, view: MonitorView) -> Optional[list[Optional[float]]]:
    """Derive system-drive used % per sample from free GB and the drive's total size (None if unknown)."""
    d = s.system_drive
    total = d.total_gb if d else None
    if not total or "disk_free" not in view.series:
        return None
    return [None if v is None else max(0.0, min(100.0, 100.0 * (total - v) / total)) for v in view.series["disk_free"]]


@rule("disk_filling")
def disk_filling(s: SystemSnapshot) -> Iterable[Finding]:
    """System drive losing free space steadily during the window, with a time-until-full projection.

    Small or jittery changes are ignored (see the DISK_FILL_* noise filters).
    This doesn't supersede ``storage.system_disk_full``: that finding is about
    how full the drive is now, and this one is about how fast it is filling.
    """
    view = _monitor_view(s)
    trend = disk_fill_trend(s, view)
    if trend is None or not trend["meaningful"]:
        return []
    used = _disk_used_values(s, view)
    if used is not None:
        st = series_stats(view, "disk_used", float(SYSTEM_DISK_WARN_PERCENT), values=used)
        unit = "%"
    else:  # drive total unknown: report the free-GB series itself
        st = series_stats(view, "disk_free", 0.0, above=False)
        unit = " GB"
    conf = window_confidence(view.duration_s, st.valid_count)
    hours = trend["hours_to_full"]
    extra = (f"The projection assumes the current rate of {abs(trend['slope_gb_per_hour']):.1f} GB/hour continues; "
             "a download or update that finishes will stop it.")
    ev = _evidence(view, st, conf, unit, extra)
    ev.update({"slope_gb_per_hour": round(trend["slope_gb_per_hour"], 2), "trend_r2": round(trend["r2"], 2),
               "drop_gb": round(trend["drop_gb"], 2), "latest_free_gb": round(trend["latest_free_gb"], 2),
               "projected_hours_to_full": round(hours, 1)})
    if hours <= DISK_FILL_CRITICAL_HOURS:
        sev, title = Severity.CRITICAL, f"System drive will be full in about {fmt_duration(hours * 3600)}"
    elif hours <= DISK_FILL_WARN_HOURS:
        sev, title = Severity.WARNING, f"System drive is filling up (full in ~{hours:.0f} h at this rate)"
    else:
        sev, title = Severity.INFO, "System drive is slowly filling"
    return [Finding(
        id="storage.disk_filling", title=title, severity=sev, category=Category.STORAGE,
        cause=(f"Free space fell by {trend['drop_gb']:.1f} GB in {fmt_duration(view.duration_s)} "
               f"({abs(trend['slope_gb_per_hour']):.1f} GB/hour), leaving {trend['latest_free_gb']:.1f} GB. "
               f"Something is writing a lot: a download, an update, a log or a cache running away. "
               f"A full system drive makes {_os_name(s)} unstable."),
        fix=("Free: find what's growing (Storage settings or a tool like WinDirStat), pause or cancel big "
             "downloads/syncs, empty the Recycle Bin and run Disk Cleanup. Real fix: a bigger drive if normal use fills it."),
        evidence=ev, confidence=conf, free_fix=True)]


# ---------------------------------------------------------------------------
# 5. Overlap resolution (called by registry.analyze)
# ---------------------------------------------------------------------------

def resolve_overlaps(findings: list[Finding]) -> list[Finding]:
    """Make sure a metric is never reported twice (single reading *and* over time).

    For each sustained finding whose evidence names ``supersedes`` = X:

    * **Medium/high confidence**: the over-time finding wins. Every finding
      with id X is dropped, the dropped reading goes into
      ``evidence['superseded']``, and the cause says which check was replaced.
    * **Low confidence** (window too short): the short window isn't trusted
      over a direct reading, so the two are merged. The snapshot finding X
      stays and gains ``evidence['sustained']`` with the window stats plus a
      sentence in its cause; the sustained finding is dropped. If there's no
      X (for example the snapshot section is missing), the sustained finding
      stays on its own.
    """
    by_id: dict[str, list[Finding]] = {}
    for f in findings:
        by_id.setdefault(f.id, []).append(f)
    drop: set[int] = set()
    for f in findings:
        target = f.evidence.get("supersedes") if isinstance(f.evidence, dict) else None
        olds = by_id.get(target, []) if target else []
        if not olds:
            continue
        if f.confidence is Confidence.LOW:
            for old in olds:
                old.evidence = dict(old.evidence, sustained={k: v for k, v in f.evidence.items() if k != "supersedes"})
                old.cause = (old.cause.rstrip() + f" Over time (short window, low confidence): {f.title.lower()}. "
                             f"Run `{MONITOR_HINT}` to confirm.")
            drop.add(id(f))
        else:
            f.evidence = dict(f.evidence, superseded=[
                {"id": o.id, "severity": o.severity.value, "title": o.title} for o in olds])
            f.cause = (f.cause.rstrip() + f" (Replaces the single-reading check '{olds[0].title}', which only saw "
                       "one moment.)")
            drop.update(id(o) for o in olds)
    return [f for f in findings if id(f) not in drop]


def over_time_summary(s: SystemSnapshot) -> Optional[dict]:
    """Everything the report's "Over time" section needs, or None without monitor data.

    Keys: ``view``, ``confidence``, ``explanation``, ``stats`` (list of
    ``(label, unit, SeriesStats)``, only for metrics with at least one
    reading), ``unread`` (labels of sampled metrics that never had a reading)
    and ``fill`` (the ``disk_fill_trend`` dict or None).
    """
    view = _monitor_view(s)
    if view is None:
        return None
    specs = [("cpu", CPU_SUSTAINED_THRESHOLD_PERCENT, True, None),
             ("memory", RAM_SUSTAINED_THRESHOLD_PERCENT, True, None),
             ("disk_used", float(SYSTEM_DISK_WARN_PERCENT), True, _disk_used_values(s, view)),
             ("disk_busy", DISK_BUSY_SUSTAINED_THRESHOLD_PERCENT, True, None),
             ("cpu_temp", float(CPU_TEMP_WARN_C), True, None)]
    stats, unread = [], []
    for key, thr, above, vals in specs:
        if vals is None and key not in view.series:
            if key == "disk_used" and "disk_free" in view.series:
                key, thr, above = "disk_free", 0.0, False  # total unknown: show free GB instead
            else:
                continue
        st = series_stats(view, key, thr, above, values=vals)
        label, unit = METRIC_LABELS[key]
        (stats if st.valid_count else unread).append((label, unit, st) if st.valid_count else label)
    best_valid = max((st.valid_count for _, _, st in stats), default=0)
    conf = window_confidence(view.duration_s, best_valid)
    return {"view": view, "confidence": conf, "explanation": confidence_explanation(conf, view.duration_s, best_valid),
            "stats": stats, "unread": unread, "fill": disk_fill_trend(s, view)}
