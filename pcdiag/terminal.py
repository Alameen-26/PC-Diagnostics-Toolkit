"""Plain-text terminal summary of a snapshot (and findings, when available).

Output is deliberately ASCII-only: the legacy Windows console (conhost with
code page 437/850/1252) raises UnicodeEncodeError on characters such as
arrows or check marks, and a crash at the very end of a scan is the worst
possible user experience.

Added in v0.1.0. v0.2.0 added the live monitor view (``LiveView``) and the
monitor summary.
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
    # Changed in v0.2.0: header carries the "Made by NiyiSoft" maker's mark (ASCII only).
    lines = [f"PC Diagnostics Toolkit {__version__} - Made by NiyiSoft",
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


#: Finding ids produced from monitor data by pcdiag/analysis/sustained.py.
#: Added in v0.2.0; only used to pick lines for the terminal's "Over time" block.
OVER_TIME_IDS = ("memory.sustained", "cpu.sustained", "storage.disk_filling",
                 "storage.disk_busy_sustained", "thermal.cpu_sustained")


def over_time_lines(snap: SystemSnapshot, findings: Iterable[Any]) -> list[str]:
    """Short "Over time" block for snapshots that carry monitor data.

    Added in v0.2.0. The top-5 findings list can push sustained findings out
    of view (e.g. the slow laptop has many one-off warnings), so when a
    monitor session is attached, its findings are always listed here.
    Without monitor data nothing is printed.
    """
    session = getattr(snap, "monitor", None)
    if session is None:
        return []
    mins = session.duration_seconds / 60
    lines = ["", f"Over time ({mins:.1f} min monitored, {session.sample_count} readings):"]
    found = [f for f in findings if getattr(f, "id", "") in OVER_TIME_IDS]
    for f in found:
        lines.append(f"  [{f.severity.value.upper()}] {f.title}")
    if not found:
        lines.append("  (no over-time checks ran; see the HTML report)")
    return lines


# ---------------------------------------------------------------------------
# Live monitor view (``pcdiag monitor`` / ``scan --monitor``)
# Added in v0.2.0. Same ASCII-only rule as above: bars use "#" and ".".
# ---------------------------------------------------------------------------

#: Thresholds used only for the monitor's *display* ("time at/above 90%").
#: The real judgement (what counts as sustained pressure) lives in
#: pcdiag.analysis. Added in v0.2.0.
DISPLAY_THRESHOLDS = {"cpu_percent": 90.0, "ram_percent": 90.0, "swap_percent": 50.0, "disk_busy_percent": 90.0}


def bar(percent: Optional[float], width: int = 20) -> str:
    """ASCII usage bar, e.g. ``[######..............]``; unknown -> ``[   unavailable    ]``."""
    if percent is None:
        return "[" + "unavailable".center(width) + "]"
    filled = int(round(max(0.0, min(100.0, percent)) / 100 * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def monitor_live_lines(session: Any, sample: Any) -> list[str]:
    """One screenful for the live view: current readings plus the running average.

    Duck-typed on MonitorSession / MonitorSample (pcdiag.models) so this
    module keeps importing only SystemSnapshot at the top.
    """
    total = session.requested_duration_seconds
    progress = f"{sample.elapsed_seconds:.0f}s / {total:.0f}s" if total else f"{sample.elapsed_seconds:.0f}s (Ctrl+C to stop)"
    lines = [f"PC Diagnostics Toolkit {__version__} - live monitor  {progress}  "
             f"(every {fmt(session.interval_seconds, 's')}, {session.sample_count} samples)", ""]

    def row(label: str, value: Optional[float], metric: str, extra: str = "") -> str:
        """Bar + current value + session average for one percent metric."""
        avg = session.mean(metric)
        return f"  {label:<6}{bar(value)} {fmt(value, '%'):>6}   avg {fmt(avg, '%'):>6}{extra}"

    ram_extra = (f"   {fmt(sample.ram_used_gb, ' GB')} used, {fmt(sample.ram_available_gb, ' GB')} free"
                 if sample.ram_used_gb is not None else "")
    lines.append(row("CPU", sample.cpu_percent, "cpu_percent"))
    if sample.per_core_percent:
        cores = " ".join(f"{v:.0f}" for v in sample.per_core_percent[:16])
        more = " ..." if len(sample.per_core_percent) > 16 else ""
        lines.append(f"  {'':<6}cores % {cores}{more}")
    lines.append(row("RAM", sample.ram_percent, "ram_percent", ram_extra))
    lines.append(row("Swap", sample.swap_percent, "swap_percent"))
    for d in sample.disks:
        lines.append(f"  {'Disk':<6}{bar(d.used_percent)} {fmt(d.used_percent, '%'):>6}   "
                     f"{d.mountpoint} {fmt(d.free_gb, ' GB')} free of {fmt(d.total_gb, ' GB')}")
    busy = f", busiest disk {fmt(sample.disk_busy_percent, '%')} busy" if sample.disk_busy_percent is not None else ""
    lines.append(f"  {'I/O':<6}read {fmt(sample.disk_read_mbps, ' MB/s')}, write {fmt(sample.disk_write_mbps, ' MB/s')}{busy}")
    lines += ["", "  Top CPU:    " + (", ".join(f"{p.name} {fmt(p.cpu_percent, '%')}" for p in sample.top_cpu[:5]) or "?"),
              "  Top memory: " + (", ".join(f"{p.name} {fmt(p.memory_mb, ' MB', 0)}" for p in sample.top_memory[:5]) or "?")]
    if sample.notes:
        lines += ["", "  Unavailable: " + "; ".join(sample.notes)]
    return lines


def monitor_summary_lines(session: Any) -> list[str]:
    """End-of-session summary: average / 95th percentile / peak and time spent high."""
    how = " (stopped early with Ctrl+C)" if session.interrupted else ""
    lines = [f"Monitor summary: {session.sample_count} samples over {fmt(session.duration_seconds, ' s', 0)}"
             f" every {fmt(session.interval_seconds, ' s')}{how}"]
    if not session.samples:
        return lines + ["  No samples were taken."]
    lines.append(f"  {'':<10}{'avg':>8}{'p95':>8}{'peak':>8}   time at/above threshold (longest stretch)")
    for metric, label in (("cpu_percent", "CPU"), ("ram_percent", "RAM"), ("swap_percent", "Swap"),
                          ("disk_busy_percent", "Disk busy")):
        st = session.stats(metric, DISPLAY_THRESHOLDS[metric])
        if st.count == 0:
            lines.append(f"  {label:<10}{'unavailable':>24}")
            continue
        lines.append(f"  {label:<10}{fmt(st.mean, '%'):>8}{fmt(st.p95, '%'):>8}{fmt(st.max, '%'):>8}   "
                     f"{fmt(st.percent_above, '%', 0)} of the time >= {fmt(st.threshold, '%', 0)} "
                     f"({fmt(st.longest_streak_seconds, ' s', 0)})")
    for mp in session.mountpoints():
        last = next((d for s in reversed(session.samples) for d in s.disks if d.mountpoint == mp and d.free_gb is not None), None)
        if last:
            lines.append(f"  Disk {mp}: {fmt(last.free_gb, ' GB')} free of {fmt(last.total_gb, ' GB')} ({fmt(last.used_percent, '%')} used)")
    rd, wr = session.mean("disk_read_mbps"), session.mean("disk_write_mbps")
    if rd is not None or wr is not None:
        lines.append(f"  Disk I/O avg: read {fmt(rd, ' MB/s')}, write {fmt(wr, ' MB/s')}")
    top_cpu = session.process_summary(by="cpu", n=3)
    top_mem = session.process_summary(by="memory", n=3)
    if top_cpu:
        lines.append("  Busiest processes (avg CPU, % of one core): "
                     + ", ".join(f"{p.name} {fmt(p.mean_cpu_percent, '%')}" for p in top_cpu))
    if top_mem:
        lines.append("  Biggest memory users (avg): "
                     + ", ".join(f"{p.name} {fmt(p.mean_memory_mb, ' MB', 0)}" for p in top_mem))
    if session.notes:
        lines += ["", "  Not available during monitoring:"] + [f"    - {n}" for n in session.notes]
    return lines


def enable_ansi() -> bool:
    """Turn on ANSI escape codes; True if the console will honour them.

    Windows 10+ consoles (PowerShell, Windows Terminal) support them, but
    Python's console needs ENABLE_VIRTUAL_TERMINAL_PROCESSING switched on
    first. Without that the live view falls back to plain scrolling lines.
    """
    import os
    import sys
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


class LiveView:
    """Redraws the monitor screen in place (ANSI) or prints one line per sample.

    In-place mode moves the cursor back up over the previous frame and clears
    to the end of the screen, so earlier terminal output stays visible. The
    one-line fallback is used when output is piped/redirected or the console
    can't do ANSI (very old Windows). Added in v0.2.0.
    """

    def __init__(self, stream: Any = None, ansi: Optional[bool] = None) -> None:
        import sys
        self.stream = stream or sys.stdout
        self.ansi = enable_ansi() if ansi is None else ansi
        self._last_height = 0

    def _write(self, text: str) -> None:
        """Write, replacing characters the console code page can't encode."""
        enc = getattr(self.stream, "encoding", None) or "utf-8"
        self.stream.write(text.encode(enc, "replace").decode(enc, "replace"))
        self.stream.flush()

    def update(self, session: Any, sample: Any) -> None:
        """Show the latest sample (callback for ``Monitor.run``)."""
        if self.ansi:
            import shutil
            # Cut lines to the window width: a wrapped line would make the
            # "move up N lines" redraw miscount and leave stale rows behind.
            width = max(40, shutil.get_terminal_size((100, 24)).columns - 1)
            frame = [line[:width] for line in monitor_live_lines(session, sample)]
            prefix = f"\x1b[{self._last_height}F\x1b[J" if self._last_height else ""
            self._write(prefix + "\n".join(frame) + "\n")
            self._last_height = len(frame)
        else:
            self._write(f"[{sample.elapsed_seconds:>5.0f}s] CPU {fmt(sample.cpu_percent, '%'):>5}  "
                        f"RAM {fmt(sample.ram_percent, '%'):>5}  swap {fmt(sample.swap_percent, '%'):>5}  "
                        f"I/O r {fmt(sample.disk_read_mbps, ' MB/s')} w {fmt(sample.disk_write_mbps, ' MB/s')}  "
                        f"top {sample.top_cpu[0].name if sample.top_cpu else '?'}\n")
