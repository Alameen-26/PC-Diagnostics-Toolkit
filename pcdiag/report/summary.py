# pcdiag/report/summary.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): "run as Administrator" card text, "Can't tell" label for data gaps, read_errors in disk-health card, bus type in overview, raw benchmark rows.
"""Shared report logic used by both the HTML and Markdown renderers.

Contents
--------
* ``verdict``       - one overall status (Healthy / Needs attention / Action
                      needed / Can't tell), a 0-100 health score and a
                      plain-language summary sentence.
* ``metric_cards``  - CPU, memory, disk space, temperature, battery and disk
                      health, each with a traffic-light status that is never
                      green for unknown data.
* ``free_fixes``    - the no-cost actions pulled from problem findings, shown
                      before any purchase.
* ``group_findings``, ``evidence_label``, ``reading_times``, ``overview_rows``.

Keeping this logic here guarantees the two report formats always agree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..analysis import rules as R
from ..analysis.findings import Category, Confidence, Finding, Severity
from ..analysis.recommend import Recommendation
from ..analysis.rules import admin_blocked_collectors, missing_core_sections
from ..models import SmartStatus, SystemSnapshot

# --------------------------------------------------------------------------
# Status vocabulary (colour is never the only signal: each has icon + label)
# --------------------------------------------------------------------------

GOOD, WARN, BAD, UNKNOWN = "good", "warn", "bad", "unknown"

# status -> (colour name used by CSS, icon, short accessible label)
STATUS_STYLE = {
    GOOD: ("green", "✔", "Good"),
    WARN: ("amber", "!", "Warning"),
    BAD: ("red", "✖", "Problem"),
    UNKNOWN: ("grey", "?", "Unknown"),
}

SEVERITY_LABEL = {Severity.CRITICAL: "Critical", Severity.WARNING: "Warning",
                  Severity.INFO: "Info", Severity.OK: "OK"}

# Group headings for findings, worst first.
SEVERITY_GROUP_TITLE = {Severity.CRITICAL: "Act now", Severity.WARNING: "Needs attention",
                        Severity.INFO: "Good to know", Severity.OK: "Looks good"}


# --------------------------------------------------------------------------
# Overall verdict + health score
# --------------------------------------------------------------------------

@dataclass
class Verdict:
    """Overall result shown in the top banner."""
    light: str             # "green" | "amber" | "red" | "grey"
    label: str             # "Healthy" | "Needs attention" | "Action needed" | "Can't tell"
    sentence: str          # one plain-language summary sentence
    score: Optional[int]   # 0-100 health score; None when there isn't enough data


# Short phrases used to build "Your PC is slow mainly because ..." sentences.
# Ordered by how much each problem typically hurts everyday speed, so the
# sentence names the biggest causes first (not just the first by severity).
_SLOWDOWN_PHRASES: list[tuple[str, str]] = [
    ("storage.hdd_system_drive", "the boot drive is a hard disk"),
    ("memory.pressure", "RAM is nearly full"),
    ("memory.low_total", "it has too little RAM"),
    ("storage.system_disk_full", "the system drive is almost full"),
    ("thermal.cpu", "the CPU is overheating and slowing itself down"),
    ("storage.disk_busy", "the disk is maxed out"),
    ("cpu.busy", "the CPU is constantly busy"),
    ("startup.too_many", "too many apps start at sign-in"),  # Changed in v0.1.0: OS-neutral
    ("gpu.driver_outdated", "the graphics driver is very old"),
    ("thermal.gpu", "the graphics card is running hot"),
]
# Problems that aren't about speed get their own wording.
_OTHER_PHRASES = {
    "storage.smart_warning": "a drive is showing signs of wear",
    "battery.worn": "the battery is worn",
    "battery.replace": "the battery needs replacing",
    "storage.data_disk_full": "a data drive is nearly full",
}

# Health score (rule of thumb). Bands line up with the verdict colours:
#   80-100 = Healthy (green), 41-79 = Needs attention (amber), 0-40 = Action needed (red).
# Each finding subtracts a penalty; the result is then clamped into the band
# that matches the worst severity, so score and colour can never disagree.
_PENALTY = {Severity.CRITICAL: 35, Severity.WARNING: 8, Severity.INFO: 1, Severity.OK: 0}
_CRITICAL_SCORE_CAP = 40   # any critical issue: score <= 40 (red band)
_WARNING_SCORE_CAP = 79    # any warning: score <= 79 (out of the green band)
_WARNING_SCORE_FLOOR = 41  # warnings alone never reach the red band


def health_score(snapshot: SystemSnapshot, findings: list[Finding]) -> Optional[int]:
    """0-100 overall score. None if core data is missing and no problem was found."""
    has_problem = any(f.is_problem for f in findings)
    if missing_core_sections(snapshot) and not has_problem:
        return None  # can't score what we couldn't measure
    score = 100 - sum(_PENALTY[f.severity] for f in findings)
    if any(f.severity is Severity.CRITICAL for f in findings):
        score = min(score, _CRITICAL_SCORE_CAP)
    elif has_problem:
        score = max(_WARNING_SCORE_FLOOR, min(score, _WARNING_SCORE_CAP))
    return max(0, min(100, score))


def _join(phrases: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]


def summary_sentence(findings: list[Finding]) -> Optional[str]:
    """Plain-language sentence naming the main causes, or None if no problems."""
    probs = {f.id: f for f in findings if f.is_problem}
    if not probs:
        return None
    crit = [f for f in findings if f.severity is Severity.CRITICAL]
    if crit:
        first = crit[0]
        if first.id == "storage.smart_failing":
            return (f"A drive is failing ({first.evidence.get('drive', 'unknown drive')}): "
                    "back up your files now, then replace it.")
        return f"Act now: {first.title}."
    slow = [phrase for fid, phrase in _SLOWDOWN_PHRASES if fid in probs][:2]
    if slow:
        return f"Your PC is slow mainly because {_join(slow)}."
    other = [phrase for fid, phrase in _OTHER_PHRASES.items() if fid in probs]
    if other:
        return f"Your PC runs fine, but {_join(other[:2])}."
    return f"Worth a look: {next(iter(probs.values())).title}."


def verdict(snapshot: SystemSnapshot, findings: list[Finding],
            recs: Optional[list[Recommendation]] = None) -> Verdict:
    """Overall verdict from the WORST finding severity.

    critical -> red "Action needed"; warning -> amber "Needs attention";
    otherwise green "Healthy" -- but only if the core sections were actually
    collected. With missing core data and no problems we say grey "Can't
    tell", so an empty snapshot is never reported as healthy.
    """
    score = health_score(snapshot, findings)
    worst = max((f.severity.rank for f in findings), default=0)
    sentence = summary_sentence(findings)
    if worst >= Severity.CRITICAL.rank:
        return Verdict("red", "Action needed", sentence or "", score)
    if worst >= Severity.WARNING.rank:
        return Verdict("amber", "Needs attention", sentence or "", score)
    missing = missing_core_sections(snapshot)
    if missing:
        return Verdict("grey", "Can't tell",
                       f"Not enough information was collected ({', '.join(missing)} missing) "
                       "to judge this PC's health.", score)
    return Verdict("green", "Healthy",
                   "No problems found: nothing here is slowing your PC down.", score)


# --------------------------------------------------------------------------
# Metric cards
# --------------------------------------------------------------------------

@dataclass
class MetricCard:
    """One dashboard tile."""
    name: str        # "CPU", "Memory", ...
    value: str       # "88%", "82°C", "unknown"
    status: str      # GOOD / WARN / BAD / UNKNOWN
    meaning: str     # one line, e.g. "High - may cause slowdowns"

    @property
    def colour(self) -> str:
        return STATUS_STYLE[self.status][0]

    @property
    def icon(self) -> str:
        return STATUS_STYLE[self.status][1]

    @property
    def status_label(self) -> str:
        return STATUS_STYLE[self.status][2]


def _band(value: float, warn_at: float, bad_at: float) -> str:
    """GOOD below warn_at, WARN below bad_at, else BAD."""
    if value >= bad_at:
        return BAD
    return WARN if value >= warn_at else GOOD


# Card thresholds. Where a rule exists we reuse its constants so the cards
# and the findings always agree.
CPU_CARD_WARN_PERCENT = 70.0            # sustained 70%+ is noticeable
CPU_CARD_BAD_PERCENT = R.CPU_BUSY_WARN_PERCENT
DISK_CARD_WARN_PERCENT = 85.0           # 15% free is the usual comfort margin
_UNKNOWN_ADMIN = "Unknown - may need admin rights"
# Changed in v0.1.0: when a collector error says Administrator rights are
# needed, the matching card says so explicitly (still grey, never a fault).
_RUN_AS_ADMIN = "Unknown - run as Administrator"
# Which collector(s) feed each card, to match "collector: reason" errors.
_CARD_COLLECTORS = {"CPU": {"cpu"}, "Memory": {"memory"}, "Disk space": {"disks"},
                    "Temperature": {"temperatures"}, "Battery": {"battery"},
                    "Disk health": {"disks", "smart", "smartctl"}}


def metric_cards(s: SystemSnapshot) -> list[MetricCard]:
    """Build the six status cards. Missing data is always grey, never green.

    Changed in v0.1.0: unknown cards whose collector reported an
    "Administrator" error read "Unknown - run as Administrator".
    """
    cards = _base_metric_cards(s)
    blocked = admin_blocked_collectors(s)
    for c in cards:
        if blocked & _CARD_COLLECTORS.get(c.name, set()):
            if c.status == UNKNOWN:
                c.meaning = _RUN_AS_ADMIN
            elif c.name == "Disk health" and c.status == GOOD:
                # Basic OS health status is known, but detailed counters aren't.
                c.meaning = "Normal (full details need Administrator)"
    return cards


def _base_metric_cards(s: SystemSnapshot) -> list[MetricCard]:
    """Cards from the readings alone (no error-message context)."""
    cards: list[MetricCard] = []

    # CPU load
    cpu = s.cpu.total_usage_percent if s.cpu else None
    if cpu is None:
        cards.append(MetricCard("CPU", "unknown", UNKNOWN, "Unknown - not measured"))
    else:
        st = _band(cpu, CPU_CARD_WARN_PERCENT, CPU_CARD_BAD_PERCENT)
        cards.append(MetricCard("CPU", f"{cpu:.0f}% in use", st,
                                {GOOD: "Normal", WARN: "Busy - may feel sluggish",
                                 BAD: "Very high - slows everything"}[st]))

    # Memory in use
    mem = s.memory.percent if s.memory else None
    if mem is None:
        cards.append(MetricCard("Memory", "unknown", UNKNOWN, "Unknown - not measured"))
    else:
        total = s.memory.total_gb
        st = _band(mem, R.RAM_USED_INFO_PERCENT, R.RAM_USED_WARN_PERCENT)
        val = f"{mem:.0f}% of {total:g} GB" if total is not None else f"{mem:.0f}% in use"
        cards.append(MetricCard("Memory", val, st,
                                {GOOD: "Normal", WARN: "High - may cause slowdowns",
                                 BAD: "Nearly full - PC is paging to disk"}[st]))

    # System drive space
    sd = s.system_drive
    if sd is None or sd.percent is None:
        cards.append(MetricCard("Disk space", "unknown", UNKNOWN, "Unknown - system drive not measured"))
    else:
        st = _band(sd.percent, DISK_CARD_WARN_PERCENT, R.SYSTEM_DISK_WARN_PERCENT)
        free = f", {sd.free_gb:.0f} GB free" if sd.free_gb is not None else ""
        cards.append(MetricCard("Disk space", f"{sd.percent:.0f}% used{free}", st,
                                {GOOD: "Plenty of room", WARN: "Getting full - clean up soon",
                                 BAD: "Almost full - slows the system and updates"}[st]))

    # CPU temperature (hottest CPU sensor)
    temps = [t.current_c for t in s.temperatures
             if t.current_c is not None and R._CPU_SENSOR_RE.search(f"{t.label} {t.sensor or ''}")]
    if not temps:
        cards.append(MetricCard("Temperature", "unknown", UNKNOWN, _UNKNOWN_ADMIN))
    else:
        t = max(temps)
        st = _band(t, R.CPU_TEMP_WARN_C, R.CPU_TEMP_CRITICAL_C)
        cards.append(MetricCard("Temperature", f"{t:.0f}°C (CPU)", st,
                                {GOOD: "Normal", WARN: "Hot - may be throttling",
                                 BAD: "Too hot - check cooling now"}[st]))

    # Battery health. No battery at all is "not applicable", still grey.
    b = s.battery
    health = R._battery_health(s)
    if b is None:
        cards.append(MetricCard("Battery", "none detected", UNKNOWN, "No battery (desktop) or not reported"))
    elif health is None:
        cards.append(MetricCard("Battery", "unknown", UNKNOWN, "Unknown - capacity not reported"))
    else:
        if health < R.BATTERY_REPLACE_HEALTH_PERCENT:
            st, meaning = BAD, "Replace - very short runtime"
        elif health < R.BATTERY_WORN_HEALTH_PERCENT:
            st, meaning = WARN, "Worn - shorter runtime"
        else:
            st, meaning = GOOD, "Normal"
        cards.append(MetricCard("Battery", f"{health:.0f}% health", st, meaning))

    # Disk health: worst SMART status across all drives that report it.
    smart = [d.smart.status for d in s.disks if d.smart is not None and d.smart.status is not SmartStatus.UNKNOWN]
    if not smart:
        cards.append(MetricCard("Disk health", "unknown", UNKNOWN, _UNKNOWN_ADMIN))
    elif SmartStatus.FAILING in smart:
        cards.append(MetricCard("Disk health", "failing", BAD, "A drive is failing - back up now"))
    elif SmartStatus.WARNING in smart or any(
            # Changed in v0.1.0: read_errors also counts as degradation.
            (d.smart and ((d.smart.reallocated_sectors or 0) > 0 or (d.smart.pending_sectors or 0) > 0
                          or (d.smart.read_errors or 0) > 0))
            for d in s.disks):
        cards.append(MetricCard("Disk health", "degrading", WARN, "Wear detected - check backups"))
    else:
        n = len(smart)
        cards.append(MetricCard("Disk health", f"{n} drive{'s' if n > 1 else ''} OK", GOOD, "Normal"))
    return cards


# --------------------------------------------------------------------------
# Findings helpers
# --------------------------------------------------------------------------

def evidence_label(f: Finding) -> str:
    """'Confirmed' for directly measured/reported findings, else 'Possible cause'.

    Changed in v0.1.0: data-gap findings (collector errors, needs-Administrator,
    missing sections) are labelled "Can't tell" -- they describe what we
    couldn't measure, not a cause.
    """
    if f.category is Category.DATA:
        return "Can't tell"
    return "Confirmed" if f.confidence is Confidence.HIGH else "Possible cause"


def group_findings(findings: list[Finding]) -> list[tuple[Severity, list[Finding]]]:
    """Findings grouped by severity, worst group first; empty groups omitted."""
    order = (Severity.CRITICAL, Severity.WARNING, Severity.INFO, Severity.OK)
    return [(sev, [f for f in findings if f.severity is sev])
            for sev in order if any(f.severity is sev for f in findings)]


# Markers that end the "Free:" part of a fix sentence (rules write fixes as
# "Free: ... Cheap: ... / Real fix: ...").
_PAID_MARKERS = re.compile(r"\s(?:Cheap:|Real fix:|Lasting fix:|Last resort:|If it keeps happening)")


def free_fix_text(f: Finding) -> Optional[str]:
    """The no-cost part of a finding's fix, or None if the fix needs a purchase."""
    fix = (f.fix or "").strip()
    if not fix:
        return None
    if fix.startswith("Free:"):
        return _PAID_MARKERS.split(fix[len("Free:"):].strip())[0].strip()
    return fix if f.free_fix else None


def free_fixes(findings: list[Finding]) -> list[tuple[Finding, str]]:
    """(finding, free action) for every WARNING/CRITICAL problem with a free fix."""
    out = []
    for f in findings:
        if f.is_problem:
            text = free_fix_text(f)
            if text:
                out.append((f, text))
    return out


# --------------------------------------------------------------------------
# Readings metadata + system overview
# --------------------------------------------------------------------------

def reading_times(s: SystemSnapshot) -> list[str]:
    """Plain lines describing when readings were taken and how long they sampled."""
    lines = [f"Readings taken: {s.timestamp.strftime('%Y-%m-%d %H:%M %Z').strip()}"
             if s.timestamp else "Readings taken: unknown time"]
    samples = sorted({io.sample_seconds for io in s.disk_io if io.sample_seconds is not None})
    if samples:
        lines.append(f"Disk activity sampled over {', '.join(f'{x:g}' for x in samples)} s")
    if s.benchmark and s.benchmark.duration_seconds is not None:
        lines.append(f"Benchmark ran for {s.benchmark.duration_seconds:g} s")
    lines.append("CPU, memory and temperature values are a single snapshot, not an average.")
    return lines


def _fmt(value, unit: str = "", digits: int = 1) -> str:
    """Format a value for display; None becomes 'unknown' (never 0)."""
    if value is None or value == "":
        return "unknown"
    if isinstance(value, float):
        value = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return f"{value}{unit}"


def overview_rows(s: SystemSnapshot) -> list[tuple[str, str]]:
    """(label, value) rows describing the machine, all values as plain text."""
    rows: list[tuple[str, str]] = []
    o = s.os
    rows.append(("Computer", _fmt(o.hostname if o else None)))
    rows.append(("Operating system",
                 " ".join(x for x in (o.system, o.release, f"({o.version})" if o.version else "") if x)
                 if o else "unknown"))
    c = s.cpu
    rows.append(("Processor", f"{_fmt(c.model)}, {_fmt(c.physical_cores)} cores / {_fmt(c.logical_cores)} threads"
                 if c else "unknown"))
    m = s.memory
    if m:
        extra = " ".join(x for x in (m.type, f"{m.speed_mhz} MHz" if m.speed_mhz else None) if x)
        slots = f", {m.slots_used}/{m.slots_total} slots used" if m.slots_used is not None and m.slots_total else ""
        rows.append(("Memory", f"{_fmt(m.total_gb, ' GB')} {extra}".strip() + f" ({_fmt(m.percent, '%')} in use{slots})"))
    else:
        rows.append(("Memory", "unknown"))
    if s.disks:
        for d in s.disks:
            tag = " [system]" if d.is_system_drive else ""
            bus = f" via {d.bus_type}" if d.bus_type else ""   # Changed in v0.1.0: show bus type
            rows.append((f"Disk {d.mountpoint or d.device}{tag}",
                         f"{d.kind.value.upper()}{bus} {_fmt(d.model)}, {_fmt(d.total_gb, ' GB', 0)}, "
                         f"{_fmt(d.percent, '%')} full"))
    else:
        rows.append(("Disks", "unknown"))
    for g in s.gpus:
        rows.append(("Graphics", f"{_fmt(g.name)} (driver {_fmt(g.driver_version)}, {_fmt(g.driver_date)})"))
    if s.battery:
        rows.append(("Battery", f"{_fmt(s.battery.wear_percent, '%')} worn, {_fmt(s.battery.cycle_count)} cycles"))
    return rows


# --------------------------------------------------------------------------
# Benchmark (Added in v0.1.0, follow-up)
# --------------------------------------------------------------------------

BENCHMARK_NOTE = ("Benchmark scores are relative to this toolkit and only meaningful when compared with "
                  "earlier runs on this same PC (use `pcdiag compare old.json new.json`). "
                  "They are not judged against fixed thresholds.")


def benchmark_rows(s: SystemSnapshot) -> list[tuple[str, str]]:
    """Raw benchmark numbers for display only; empty if no benchmark ran.

    Deliberately no good/bad colouring: real scores vary hugely between
    machines (and from the example fixtures), so judging them would mislead.
    """
    b = s.benchmark
    if b is None:
        return []
    rows = [("CPU single-core score", _fmt(b.cpu_single_score, digits=0)),
            ("CPU multi-core score", _fmt(b.cpu_multi_score, digits=0)),
            ("Disk read", _fmt(b.disk_read_mbps, " MB/s", 0)),
            ("Disk write", _fmt(b.disk_write_mbps, " MB/s", 0)),
            ("Memory copy", _fmt(b.memory_mbps, " MB/s", 0))]
    return rows
