"""Plain-text terminal summary of a snapshot (and findings, when available).

Output is deliberately ASCII-only: the legacy Windows console (conhost with
code page 437/850/1252) raises UnicodeEncodeError on characters such as
arrows or check marks, and a crash at the very end of a scan is the worst
possible user experience.

Added in v0.1.0
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import __version__
from .models import SystemSnapshot


def fmt(value: Optional[float], unit: str = "", digits: int = 1) -> str:
    """Format a number with a unit, or "?" when the value is unknown."""
    if value is None:
        return "?"
    text = f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"
    return f"{text}{unit}"


def _row(label: str, text: str) -> str:
    """One aligned "Label    value" line."""
    return f"  {label:<11}{text}"


def _os_line(s: SystemSnapshot) -> str:
    """Hostname, OS name and uptime for the header."""
    o = s.os
    if o is None:
        return "unknown OS"
    parts = [o.system, o.release or ""]
    if o.version and o.system == "Windows":
        parts.append(f"(build {o.version})")
    up = f", up {fmt(o.uptime_hours / 24 if o.uptime_hours else None, ' days')}" if o.uptime_hours else ""
    return f"{o.hostname or 'this PC'}: {' '.join(p for p in parts if p)}{up}"


def summary_lines(s: SystemSnapshot) -> list[str]:
    """Readable multi-line summary of the hardware and current load."""
    lines = [f"PC Diagnostics Toolkit {__version__}",
             f"Scan of {_os_line(s)} at {s.timestamp.astimezone().strftime('%Y-%m-%d %H:%M %Z')}", ""]

    if s.cpu:
        c = s.cpu
        cores = f"{c.physical_cores or '?'} cores / {c.logical_cores or '?'} threads"
        clock = f", base {fmt(c.base_freq_mhz / 1000 if c.base_freq_mhz else None, ' GHz', 2)}" if c.base_freq_mhz else ""
        lines.append(_row("CPU", f"{c.model or 'unknown'} ({cores}{clock})"))
        lines.append(_row("", f"usage {fmt(c.total_usage_percent, '%')}"
                              + (f", load {c.load_avg_1m}" if c.load_avg_1m is not None else "")))
    if s.memory:
        m = s.memory
        lines.append(_row("Memory", f"{fmt(m.used_gb, ' GB')} of {fmt(m.total_gb, ' GB')} in use ({fmt(m.percent, '%')}), "
                                    f"swap {fmt(m.swap_used_gb, ' GB')} of {fmt(m.swap_total_gb, ' GB')}"))
        if m.slots_used or m.type:
            lines.append(_row("", f"{m.slots_used or '?'} of {m.slots_total or '?'} slots used, "
                                  f"{m.type or 'type ?'}{'-' + str(m.speed_mhz) if m.speed_mhz else ''}"))
    for d in s.disks:
        health = f", health {d.smart.status.value}" if d.smart else ""
        tag = " [system]" if d.is_system_drive else ""
        lines.append(_row("Disk" if d is s.disks[0] else "",
                          f"{d.mountpoint} {d.kind.value.upper()}{tag}: {fmt(d.free_gb, ' GB')} free of "
                          f"{fmt(d.total_gb, ' GB')} ({fmt(d.percent, '%')} used){health}"))
    total_io = next((io for io in s.disk_io if io.device is None), None)
    if total_io:
        busy = f", busiest disk {fmt(total_io.busy_percent, '%')} busy" if total_io.busy_percent is not None else ""
        lines.append(_row("Disk I/O", f"read {fmt(total_io.read_mbps, ' MB/s')}, write {fmt(total_io.write_mbps, ' MB/s')}{busy}"))
    for g in s.gpus:
        extras = [x for x in (
            f"{fmt(g.vram_mb, ' MB', 0)} VRAM" if g.vram_mb else "",
            f"driver {g.driver_version}" + (f" ({g.driver_date})" if g.driver_date else "") if g.driver_version else "",
            f"{fmt(g.usage_percent, '%')} busy" if g.usage_percent is not None else "",
            f"{fmt(g.temperature_c, ' C')}" if g.temperature_c is not None else "",
        ) if x]
        lines.append(_row("GPU" if g is s.gpus[0] else "", f"{g.name or 'unknown'}" + (f" ({', '.join(extras)})" if extras else "")))
    if s.temperatures:
        hottest = sorted(s.temperatures, key=lambda t: t.current_c or 0, reverse=True)[:4]
        lines.append(_row("Temps", ", ".join(f"{t.label} {fmt(t.current_c, ' C')}" for t in hottest)))
    if s.battery:
        b = s.battery
        wear = f", wear {fmt(b.wear_percent, '%')}" if b.wear_percent is not None else ""
        cycles = f", {b.cycle_count} cycles" if b.cycle_count else ""
        lines.append(_row("Battery", f"{fmt(b.percent, '%')}, {'plugged in' if b.plugged_in else 'on battery'}{wear}{cycles}"))
    enabled = sum(1 for i in s.startup_items if i.enabled is not False)
    lines.append(_row("Startup", f"{len(s.startup_items)} items ({enabled} enabled)"))
    if s.top_processes:
        top = s.top_processes[:5]
        lines.append(_row("Top procs", ", ".join(f"{p.name} {fmt(p.cpu_percent, '%')} CPU/{fmt(p.memory_mb, ' MB', 0)}" for p in top)))
    if s.benchmark:
        b = s.benchmark
        lines.append(_row("Benchmark", f"CPU {fmt(b.cpu_single_score)} single / {fmt(b.cpu_multi_score)} multi, "
                                       f"disk {fmt(b.disk_read_mbps, ' MB/s', 0)} read / {fmt(b.disk_write_mbps, ' MB/s', 0)} write, "
                                       f"memory {fmt(b.memory_mbps, ' MB/s', 0)}  (relative scores)"))
    if s.errors:
        lines += ["", "Not collected / limited:"] + [f"  - {e}" for e in s.errors]
    return lines


def analysis_lines(findings: Iterable[Any], recs: Iterable[Any], verdict: Any = None,
                   max_findings: int = 5) -> list[str]:
    """Verdict banner, top findings and top upgrades from pcdiag.analysis/report.

    Duck-typed (only ``severity``, ``title``, ``rank``, ``label``, ``score``
    and ``sentence`` are read) so it keeps working if those classes gain
    fields, and works with ``verdict=None`` when pcdiag.report is missing.
    """
    findings, recs = list(findings), list(recs)
    lines: list[str] = []
    if verdict is not None:
        score = getattr(verdict, "score", None)
        score_txt = f"  (health score {score}/100)" if score is not None else "  (health score: not enough data)"
        lines += ["", "=" * 60, f"  VERDICT: {getattr(verdict, 'label', '?')}{score_txt}"]
        if getattr(verdict, "sentence", ""):
            lines.append(f"  {verdict.sentence}")
        lines.append("=" * 60)
    problems = [f for f in findings if getattr(getattr(f, "severity", None), "value", "") in ("warning", "critical")]
    if findings:
        lines += ["", f"Top findings ({len(problems)} problem(s) from {len(findings)} checks):"]
        # Findings arrive worst-first from analyze(); when nothing is wrong,
        # show a few of the OK/INFO results so the user sees what was checked.
        shown = (problems or findings)[:max_findings]
        for f in shown:
            lines.append(f"  [{f.severity.value.upper()}] {f.title}")
        hidden = len(problems or findings) - len(shown)
        if hidden > 0:
            lines.append(f"  ... and {hidden} more in the HTML report")
    if recs:
        lines += ["", "Suggested upgrades (best first):"]
        for r in recs[:5]:
            urgent = " [URGENT]" if getattr(r, "urgent", False) else ""
            lines.append(f"  {getattr(r, 'rank', '-')}. {r.title}{urgent}")
    return lines
