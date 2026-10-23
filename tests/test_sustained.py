# tests/test_sustained.py
# Added in v0.2.0
"""Tests for the v0.2.0 sustained-pressure checks and the report's "Over time" section.

``MonitorSession`` wasn't in ``pcdiag/models.py`` when these were written, so
the tests attach *fake* sessions (``FakeSession`` / ``FakeSample`` below) using
the names the adapter in ``pcdiag/analysis/sustained.py`` assumes:
``snapshot.monitor.samples[i].timestamp`` / ``.cpu_percent`` /
``.memory_percent`` / ``.system_disk_free_gb`` / ``.disk_busy_percent`` /
``.cpu_temp_c``, plus optional ``session.interval_s``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from pcdiag.analysis import Confidence, Severity, analyze, recommend_upgrades
from pcdiag.analysis import sustained as SU
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import CPUInfo, DiskInfo, DiskKind, MemoryInfo, OSInfo, ProcessInfo, SystemSnapshot
from pcdiag.report import NO_MONITOR_NOTE, over_time, render_html, render_markdown
from pcdiag.report.html import sparkline_svg
from pcdiag.report.markdown import text_sparkline

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)

# Evidence keys every sustained finding must carry (task requirement).
REQUIRED_EVIDENCE = {"duration_s", "sample_count", "valid_samples", "mean", "max", "p95", "threshold",
                     "pct_above_threshold", "longest_streak_s", "uncertainty"}
SUSTAINED_IDS = {"memory.sustained", "cpu.sustained", "storage.disk_filling",
                 "storage.disk_busy_sustained", "thermal.cpu_sustained"}


@dataclass
class FakeSample:
    """Stand-in for Grock's monitor sample (assumed field names)."""
    timestamp: datetime
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    system_disk_free_gb: Optional[float] = None
    disk_busy_percent: Optional[float] = None
    cpu_temp_c: Optional[float] = None


@dataclass
class FakeSession:
    """Stand-in for MonitorSession."""
    samples: list = field(default_factory=list)
    interval_s: Optional[float] = None


def session(n, step=5.0, **series):
    """Build a FakeSession of ``n`` samples ``step`` s apart. Each kwarg is a list or a callable(i)."""
    samples = []
    for i in range(n):
        vals = {k: (v(i) if callable(v) else v[i]) for k, v in series.items()}
        samples.append(FakeSample(timestamp=T0 + timedelta(seconds=i * step), **vals))
    return FakeSession(samples=samples, interval_s=step)


def snap(monitor=None, cpu=20.0, ram=40.0, total_ram=16.0, procs=None, disk_total=500.0, disk_free=250.0):
    """Minimal healthy snapshot, optionally with a monitor session attached."""
    s = SystemSnapshot(
        os=OSInfo(system="Windows", release="11", hostname="TEST-PC"),
        cpu=CPUInfo(model="Test CPU", logical_cores=8, total_usage_percent=cpu),
        memory=MemoryInfo(total_gb=total_ram, percent=ram),
        disks=[DiskInfo(device="C:\\", mountpoint="C:\\", kind=DiskKind.SSD, is_system_drive=True,
                        total_gb=disk_total, free_gb=disk_free, percent=100 * (1 - disk_free / disk_total))],
        top_processes=procs or [])
    if monitor is not None:
        s.monitor = monitor  # optional attribute, exactly as the adapter reads it
    return s


def ids(findings):
    return {f.id for f in findings}


def one(findings, fid):
    hits = [f for f in findings if f.id == fid]
    assert len(hits) == 1, f"expected exactly one {fid}, got {[f.id for f in findings]}"
    return hits[0]


# --- sustained vs spike ------------------------------------------------------

def test_cpu_sustained_is_warning_and_supersedes_snapshot():
    s = snap(session(72, cpu_percent=lambda i: 97.0), cpu=96.0)     # 6 min pegged
    f = analyze(s)
    c = one(f, "cpu.sustained")
    assert c.severity is Severity.WARNING and c.confidence is Confidence.HIGH
    assert c.evidence["pattern"] == "sustained"
    assert "cpu.busy" not in ids(f)                                  # superseded, not doubled
    assert c.evidence["superseded"][0]["id"] == "cpu.busy"
    assert "Replaces the single-reading check" in c.cause


def test_cpu_brief_spike_is_info_and_overrides_snapshot_warning():
    # Scan caught the one busy moment (96%), but over 6 min it was mostly idle.
    s = snap(session(72, cpu_percent=lambda i: 99.0 if i in (10, 11) else 15.0), cpu=96.0)
    f = analyze(s)
    c = one(f, "cpu.sustained")
    assert c.severity is Severity.INFO and c.evidence["pattern"] == "spike"
    assert "brief" in c.title.lower()
    assert "cpu.busy" not in ids(f)
    assert not [x for x in f if x.is_problem]


def test_cpu_long_streak_alone_is_sustained():
    # 25% of the time pegged, but in one unbroken 90 s block (> 60 s) -> warning.
    s = snap(session(72, cpu_percent=lambda i: 95.0 if 20 <= i < 38 else 10.0))
    c = one(analyze(s), "cpu.sustained")
    assert c.evidence["pct_above_threshold"] == 25.0
    assert c.evidence["longest_streak_s"] == 90.0
    assert c.severity is Severity.WARNING


def test_cpu_borderline_bursts_are_info():
    # Pegged 30% of the time in 30 s bursts: neither most of the time nor a long streak.
    s = snap(session(60, cpu_percent=lambda i: 95.0 if i % 10 < 3 else 20.0))
    c = one(analyze(s), "cpu.sustained")
    assert c.severity is Severity.INFO and c.evidence["pattern"] == "borderline"


def test_cpu_sustained_names_top_processes():
    procs = [ProcessInfo(pid=1, name="miner.exe", cpu_percent=700.0), ProcessInfo(pid=2, name="idle.exe", cpu_percent=1.0)]
    c = one(analyze(snap(session(72, cpu_percent=lambda i: 98.0), procs=procs)), "cpu.sustained")
    assert "miner.exe" in c.cause


def test_ram_sustained_most_of_window_and_long_streak():
    s = snap(session(72, memory_percent=lambda i: 89.0), ram=89.0)
    f = analyze(s)
    m = one(f, "memory.sustained")
    assert m.severity is Severity.WARNING
    assert "straight" in m.title                                     # long streak strengthens the wording
    assert "memory.pressure" not in ids(f)


def test_ram_p95_path_triggers_warning():
    # Only 10% of the time above 85%, but those readings are at 95%: p95 >= 90 -> warning.
    s = snap(session(60, memory_percent=lambda i: 95.0 if i % 10 == 0 else 60.0))
    m = one(analyze(s), "memory.sustained")
    assert m.evidence["p95"] == 95.0 and m.severity is Severity.WARNING


def test_ram_borderline_is_info_and_calm_is_ok():
    border = snap(session(60, memory_percent=lambda i: 87.0 if i % 4 == 0 else 70.0))
    assert one(analyze(border), "memory.sustained").severity is Severity.INFO
    calm = snap(session(60, memory_percent=lambda i: 50.0))
    assert one(analyze(calm), "memory.sustained").severity is Severity.OK


# --- confidence tiers ------------------------------------------------------------

@pytest.mark.parametrize("duration,count,expected", [
    (30, 30, Confidence.LOW),        # under 60 s
    (59.9, 100, Confidence.LOW),
    (120, 9, Confidence.LOW),        # under 10 readings
    (60, 10, Confidence.MEDIUM),     # 1 min boundary
    (299, 60, Confidence.MEDIUM),
    (300, 60, Confidence.HIGH),      # 5 min boundary
    (3600, 720, Confidence.HIGH),
])
def test_window_confidence_tiers(duration, count, expected):
    assert SU.window_confidence(duration, count) is expected


def test_low_confidence_is_said_explicitly_and_merges_into_snapshot_finding():
    # 8 samples x 5 s = 40 s: LOW. The snapshot memory.pressure finding stays,
    # enriched with the over-time stats; no separate memory.sustained.
    s = snap(session(8, memory_percent=lambda i: 93.0), ram=93.0)
    f = analyze(s)
    assert "memory.sustained" not in ids(f)
    p = one(f, "memory.pressure")
    assert "sustained" in p.evidence and "Low confidence" in p.evidence["sustained"]["uncertainty"]
    assert "low confidence" in p.cause and "scan --monitor 300" in p.cause


def test_low_confidence_without_snapshot_finding_stays_alone_and_says_low():
    s = snap(session(8, cpu_percent=lambda i: 97.0))
    s.cpu = None                                   # no snapshot CPU data -> no cpu.busy to merge into
    c = one(analyze(s), "cpu.sustained")
    assert c.confidence is Confidence.LOW
    assert c.evidence["uncertainty"].startswith("Low confidence")


def test_medium_confidence_window():
    c = one(analyze(snap(session(24, cpu_percent=lambda i: 97.0))), "cpu.sustained")   # 2 min
    assert c.confidence is Confidence.MEDIUM and "Medium confidence" in c.evidence["uncertainty"]


# --- evidence ----------------------------------------------------------------------

def test_every_sustained_finding_has_full_evidence():
    mon = session(72, cpu_percent=lambda i: 97.0, memory_percent=lambda i: 90.0,
                  system_disk_free_gb=lambda i: 20.0 - 0.1 * i, disk_busy_percent=lambda i: 96.0,
                  cpu_temp_c=lambda i: 85.0)
    f = [x for x in analyze(snap(mon)) if x.id in SUSTAINED_IDS]
    assert ids(f) == SUSTAINED_IDS
    for x in f:
        assert REQUIRED_EVIDENCE <= set(x.evidence), x.id
        assert x.evidence["sample_count"] == 72
        assert x.evidence["uncertainty"]


# --- no monitor fallback -----------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_no_monitor_data_means_no_sustained_findings_and_unchanged_rules(name):
    s = ALL_FIXTURES[name]()
    f = analyze(s)
    assert not ids(f) & SUSTAINED_IDS
    assert over_time(s) is None


def test_empty_or_unusable_monitor_is_treated_as_missing():
    for mon in (FakeSession(samples=[]), FakeSession(samples=[FakeSample(timestamp=None)])):
        s = snap(mon)
        assert not ids(analyze(s)) & SUSTAINED_IDS
        assert over_time(s) is None


def test_no_monitor_report_explains_how_to_get_it():
    s = snap()
    f = analyze(s)
    html, md = render_html(s, f, []), render_markdown(s, f, [])
    assert "<h2>Over time</h2>" in html and 'data-monitor="none"' in html
    assert "scan --monitor 300" in html and "## Over time" in md and "scan --monitor 300" in md
    assert "scan --monitor 300" in NO_MONITOR_NOTE


# --- None-valued samples -----------------------------------------------------------

def test_all_none_metric_gives_no_finding_and_report_says_unread():
    s = snap(session(72, cpu_percent=lambda i: 20.0, cpu_temp_c=lambda i: None, disk_busy_percent=lambda i: None))
    f = analyze(s)
    assert "thermal.cpu_sustained" not in ids(f) and "storage.disk_busy_sustained" not in ids(f)
    ot = over_time(s)
    assert any("CPU temperature" in n and "no readings" in n for n in ot.notes)
    assert any("Disk busy" in n for n in ot.notes)
    assert [r.label for r in ot.rows] == ["CPU usage"]


def test_partial_none_counts_only_valid_readings_and_breaks_streaks():
    # Pegged throughout, except every 4th sample has no reading.
    vals = [None if i % 4 == 0 else 97.0 for i in range(72)]
    s = snap(session(72, cpu_percent=vals))
    c = one(analyze(s), "cpu.sustained")
    assert c.evidence["valid_samples"] == 54 and c.evidence["sample_count"] == 72
    assert c.evidence["pct_above_threshold"] == 100.0
    assert c.evidence["longest_streak_s"] == 15.0          # 3 samples x 5 s; gaps break the streak
    assert "18 of 72 samples had no reading" in c.evidence["uncertainty"]


def test_series_stats_values():
    view = SU._monitor_view(snap(session(20, step=1.0, cpu_percent=list(range(0, 100, 5)))))
    st = SU.series_stats(view, "cpu", 50.0)
    assert st.mean == pytest.approx(47.5) and st.max == 95 and st.min == 0
    assert st.p95 == 90                                    # nearest rank: 19th of 20 sorted values
    assert st.pct_above == pytest.approx(45.0)             # 55..95 = 9 of 20
    assert st.longest_streak_s == 9.0


# --- disk filling projection -------------------------------------------------------

def test_disk_filling_warning_with_projection():
    # 60 GB free, losing 0.5 GB over 5 min = 6 GB/h -> full in ~10 h (warning band: 1-24 h).
    mon = session(61, step=5.0, system_disk_free_gb=lambda i: 60.0 - (0.5 / 60) * i)  # 0.5 GB over 5 min
    f = one(analyze(snap(mon, disk_free=60.0)), "storage.disk_filling")
    assert f.evidence["slope_gb_per_hour"] == pytest.approx(-6.0, abs=0.01)
    assert f.evidence["projected_hours_to_full"] == pytest.approx(59.5 / 6.0, abs=0.1)
    assert f.severity is Severity.WARNING
    assert "projection assumes" in f.evidence["uncertainty"]
    assert REQUIRED_EVIDENCE <= set(f.evidence)


def test_disk_filling_critical_when_nearly_out():
    mon = session(61, step=5.0, system_disk_free_gb=lambda i: 3.0 - 0.02 * i)   # 14.4 GB/h, 1.8 GB left
    f = one(analyze(snap(mon, disk_free=3.0)), "storage.disk_filling")
    assert f.severity is Severity.CRITICAL and f.evidence["projected_hours_to_full"] < 1


def test_disk_filling_slow_is_info():
    mon = session(61, step=5.0, system_disk_free_gb=lambda i: 400.0 - 0.01 * i)  # 7.2 GB/h, 400 GB free
    assert one(analyze(snap(mon)), "storage.disk_filling").severity is Severity.INFO


@pytest.mark.parametrize("free", [
    lambda i: 100.0 + (0.8 if i % 2 else -0.8),            # jitter: big swings, no trend (low R^2)
    lambda i: 100.0 - 0.001 * i,                           # steady but tiny drop (< 0.5 GB)
    lambda i: 100.0 + 0.05 * i,                            # free space growing
])
def test_disk_noise_is_ignored(free):
    assert "storage.disk_filling" not in ids(analyze(snap(session(61, system_disk_free_gb=free))))


def test_disk_filling_needs_a_minute_of_data():
    mon = session(10, step=5.0, system_disk_free_gb=lambda i: 50.0 - 1.0 * i)  # 45 s window
    assert "storage.disk_filling" not in ids(analyze(snap(mon)))


# --- optional metrics ----------------------------------------------------------------

def test_sustained_temperature_critical_and_supersedes_snapshot():
    from pcdiag.models import TemperatureReading
    s = snap(session(72, cpu_temp_c=lambda i: 93.0))
    s.temperatures = [TemperatureReading(label="CPU Package", current_c=93.0)]
    f = analyze(s)
    t = one(f, "thermal.cpu_sustained")
    assert t.severity is Severity.CRITICAL and "thermal.cpu" not in ids(f)


def test_sustained_disk_busy_supersedes_all_snapshot_entries():
    from pcdiag.models import DiskIOStats
    s = snap(session(72, disk_busy_percent=lambda i: 99.0))
    s.disk_io = [DiskIOStats(device="C:", busy_percent=99.0), DiskIOStats(device="D:", busy_percent=95.0)]
    f = analyze(s)
    assert one(f, "storage.disk_busy_sustained").severity is Severity.WARNING
    assert "storage.disk_busy" not in ids(f)


# --- no double-reporting ---------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_no_metric_reported_twice_with_monitor(name):
    s = ALL_FIXTURES[name]()
    s.monitor = session(72, cpu_percent=lambda i: 95.0, memory_percent=lambda i: 92.0,
                        disk_busy_percent=lambda i: 97.0, cpu_temp_c=lambda i: 85.0)
    got = ids(analyze(s))
    for new, old in [("memory.sustained", "memory.pressure"), ("cpu.sustained", "cpu.busy"),
                     ("storage.disk_busy_sustained", "storage.disk_busy"), ("thermal.cpu_sustained", "thermal.cpu")]:
        assert not (new in got and old in got), (new, old)
        assert new in got
    ids_list = [x.id for x in analyze(s)]
    for sid in SUSTAINED_IDS & got:
        assert ids_list.count(sid) == 1                  # each over-time finding appears once


def test_sustained_memory_feeds_ram_recommendation():
    s = snap(session(72, memory_percent=lambda i: 92.0), ram=50.0, total_ram=8.0)
    f = analyze(s)
    recs = recommend_upgrades(s, f)
    assert any("RAM" in r.title and "memory.sustained" in r.related_findings for r in recs)


# --- adapter tolerance --------------------------------------------------------------------

def test_adapter_accepts_dicts_iso_strings_numbers_and_bare_lists():
    dict_samples = [{"timestamp": (T0 + timedelta(seconds=5 * i)).isoformat(), "cpu_percent": 97.0} for i in range(72)]
    for mon in (FakeSession(samples=dict_samples), dict_samples,
                [{"elapsed_s": 5.0 * i, "ram_percent": 92.0} for i in range(72)]):
        s = snap(mon)
        view = SU._monitor_view(s)
        assert view is not None and view.sample_count == 72 and view.duration_s == pytest.approx(360.0)
        assert ids(analyze(s)) & {"cpu.sustained", "memory.sustained"}


# --- report rendering ------------------------------------------------------------------------

def _monitored_snap():
    procs = [ProcessInfo(pid=6, name="<script>alert(1)</script>|evil", cpu_percent=900.0)]
    mon = session(72, cpu_percent=lambda i: 97.0 if i % 9 else None, memory_percent=lambda i: 60.0 + i / 4,
                  system_disk_free_gb=lambda i: 250.0)
    return snap(mon, procs=procs)


def test_html_over_time_section():
    s = _monitored_snap()
    f = analyze(s)
    html = render_html(s, f, recommend_upgrades(s, f))
    sec = html[html.index("<h2>Over time</h2>"):html.index("<h2>System overview</h2>")]
    assert 'data-monitor="yes"' in sec and 'data-confidence="high"' in sec
    assert "2026-10-22 14:00:00 UTC" in sec and "6 min" in sec and "72 samples" in sec
    assert sec.count("<svg") == 2 and "<polyline" in sec          # CPU and RAM sparklines
    assert "Mean" in sec and "p95" in sec and "Time past threshold" in sec
    assert "Confidence: High." in sec
    assert "steady" in sec                                         # disk free flat -> noise note
    assert "<script" not in html.lower().replace("&lt;script", "")  # no scripts, hostile name escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_markdown_over_time_section_and_escaping():
    s = _monitored_snap()
    f = analyze(s)
    md = render_markdown(s, f, recommend_upgrades(s, f))
    sec = md[md.index("## Over time"):md.index("## System overview")]
    assert "**Monitor window:**" in sec and "72 samples" in sec and "**Confidence: High.**" in sec
    assert re.search(r"CPU usage .*`[▁▂▃▄▅▆▇█·]+`", sec)
    assert "| Metric | Mean | p95 | Max |" in sec
    assert "<script>" not in md and "&lt;script&gt;" in md


def test_sparkline_svg_gaps_scale_and_escaping():
    svg = sparkline_svg('<b>"x"</b>', [10.0, 20.0, None, 90.0, 95.0, None, 50.0], 85.0)
    assert svg.count("<polyline") == 2 and svg.count("<circle") == 1   # gaps split the line; lone point = dot
    assert "<b>" not in svg and "&lt;b&gt;" in svg
    assert "<script" not in svg and "href" not in svg                  # self-contained
    flat = sparkline_svg("flat", [30.0] * 5, 85.0)
    ys = {p.split(",")[1] for p in re.search(r'points="([^"]+)"', flat).group(1).split()}
    assert ys == {f"{44 - 3 - 0.3 * 38:.1f}"}                          # fixed 0-100 scale, not auto-zoomed


def test_text_sparkline():
    assert text_sparkline([0.0, 50.0, 100.0]) == "▁▅█"
    assert text_sparkline([None, None]) == "··"
    assert len(text_sparkline([50.0] * 600)) == 60                     # long windows are bucketed
    assert text_sparkline([]) == ""


def test_low_confidence_explained_in_report():
    s = snap(session(6, cpu_percent=lambda i: 30.0, memory_percent=lambda i: 40.0))
    html = render_html(s, analyze(s), [])
    assert 'data-confidence="low"' in html and "Treat this as a hint" in html


def test_reading_times_mention_monitor_window():
    s = _monitored_snap()
    md = render_markdown(s, analyze(s), [])
    assert "Monitored for 6 min (72 samples)" in md
