# tests/test_report.py
# Added in v0.1.0
"""Tests for the report layer: verdict mapping, health score, metric cards,
free-fix extraction, and the HTML / Markdown renderers (including escaping)."""
import pytest

from pcdiag.analysis import Confidence, Finding, Severity, analyze, recommend_upgrades
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import (
    BatteryInfo, CPUInfo, DiskInfo, DiskKind, MemoryInfo, OSInfo, ProcessInfo, SmartHealth,
    SmartStatus, SystemSnapshot, TemperatureReading,
)
from pcdiag.report import (
    evidence_label, free_fixes, health_score, metric_cards, render_html, render_markdown,
    summary_sentence, verdict,
)


def run(snapshot):
    """analyze + recommend, returned in render_* argument order."""
    f = analyze(snapshot)
    return snapshot, f, recommend_upgrades(snapshot, f)


def full_snapshot():
    """Minimal snapshot with every core section present (so 'Healthy' is allowed)."""
    return SystemSnapshot(os=OSInfo(system="Windows"), cpu=CPUInfo(), memory=MemoryInfo(),
                          disks=[DiskInfo(mountpoint="C:\\", is_system_drive=True)])


def finding(sev, fid="x.test", conf=Confidence.MEDIUM, fix="", free=True):
    return Finding(id=fid, title=f"{sev.value} thing", severity=sev, confidence=conf, fix=fix, free_fix=free)


# ---- verdict mapping ---------------------------------------------------------

@pytest.mark.parametrize("sevs,light,label", [
    ([], "green", "Healthy"),
    ([Severity.OK, Severity.INFO], "green", "Healthy"),
    ([Severity.INFO, Severity.WARNING], "amber", "Needs attention"),
    ([Severity.WARNING, Severity.CRITICAL, Severity.OK], "red", "Action needed"),
])
def test_verdict_maps_worst_severity(sevs, light, label):
    v = verdict(full_snapshot(), [finding(s) for s in sevs])
    assert (v.light, v.label) == (light, label)


@pytest.mark.parametrize("name,light", [("healthy_gaming_pc", "green"),
                                        ("slow_old_laptop", "amber"),
                                        ("failing_disk_machine", "red")])
def test_fixture_verdicts(name, light):
    assert verdict(*run(ALL_FIXTURES[name]())).light == light


def test_missing_data_is_grey_never_green():
    v = verdict(*run(SystemSnapshot()))
    assert v.light == "grey" and v.label == "Can't tell" and v.score is None


def test_health_score_bands_match_verdict():
    snap = full_snapshot()
    assert health_score(snap, []) == 100
    warn = health_score(snap, [finding(Severity.WARNING)] * 20)   # many warnings
    assert 41 <= warn <= 79
    assert health_score(snap, [finding(Severity.CRITICAL)]) <= 40
    assert health_score(*run(ALL_FIXTURES["healthy_gaming_pc"]())[:2]) >= 80


def test_summary_sentences():
    _, f, _ = run(ALL_FIXTURES["slow_old_laptop"]())
    s = summary_sentence(f)
    assert s.startswith("Your PC is slow mainly because") and "hard disk" in s and "RAM" in s
    _, f, _ = run(ALL_FIXTURES["failing_disk_machine"]())
    assert "failing" in summary_sentence(f) and "back up" in summary_sentence(f)
    assert summary_sentence([finding(Severity.OK)]) is None


# ---- metric cards ------------------------------------------------------------

def test_cards_unknown_are_grey():
    cards = metric_cards(SystemSnapshot())
    assert [c.name for c in cards] == ["CPU", "Memory", "Disk space", "Temperature", "Battery", "Disk health"]
    assert all(c.status == "unknown" and c.colour == "grey" for c in cards)
    assert all(c.status_label == "Unknown" and c.icon == "?" for c in cards)


def test_cards_all_none_sections_still_grey():
    s = SystemSnapshot(cpu=CPUInfo(), memory=MemoryInfo(), battery=BatteryInfo(),
                       temperatures=[TemperatureReading(label="CPU")],
                       disks=[DiskInfo(is_system_drive=True, smart=SmartHealth(status=SmartStatus.UNKNOWN))])
    assert all(c.status == "unknown" for c in metric_cards(s))


def test_cards_traffic_lights():
    s = SystemSnapshot(
        cpu=CPUInfo(total_usage_percent=10.0),
        memory=MemoryInfo(percent=85.0, total_gb=8.0),
        disks=[DiskInfo(mountpoint="C:\\", is_system_drive=True, kind=DiskKind.SSD, percent=96.0,
                        smart=SmartHealth(status=SmartStatus.FAILING))],
        temperatures=[TemperatureReading(label="CPU", current_c=95.0)],
        battery=BatteryInfo(wear_percent=10.0),
    )
    st = {c.name: c.status for c in metric_cards(s)}
    assert st == {"CPU": "good", "Memory": "warn", "Disk space": "bad", "Temperature": "bad",
                  "Battery": "good", "Disk health": "bad"}


def test_slow_laptop_cards():
    st = {c.name: c.status for c in metric_cards(ALL_FIXTURES["slow_old_laptop"]())}
    assert st["Memory"] == "bad" and st["Temperature"] == "warn" and st["Battery"] == "warn"


# ---- findings helpers ----------------------------------------------------------

def test_confirmed_vs_possible():
    assert evidence_label(finding(Severity.WARNING, conf=Confidence.HIGH)) == "Confirmed"
    assert evidence_label(finding(Severity.WARNING, conf=Confidence.MEDIUM)) == "Possible cause"
    assert evidence_label(finding(Severity.WARNING, conf=Confidence.LOW)) == "Possible cause"


def test_free_fix_extraction_strips_paid_part():
    f = finding(Severity.WARNING, fix="Free: clean the fans. Cheap: replace thermal paste.", free=False)
    assert free_fixes([f])[0][1] == "clean the fans."
    paid_only = finding(Severity.WARNING, fix="Replace the battery.", free=False)
    assert free_fixes([paid_only]) == []
    assert free_fixes([finding(Severity.OK, fix="Free: nothing")]) == []   # only problems


def test_failing_disk_free_fix_is_backup():
    _, f, _ = run(ALL_FIXTURES["failing_disk_machine"]())
    texts = [t for _, t in free_fixes(f)]
    assert texts[0].startswith("back up everything")


# ---- renderers -------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_html_structure(name):
    html = render_html(*run(ALL_FIXTURES[name]()))
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    for section in ("My PC Health Report", "At a glance", "Free fixes first",
                    "Upgrades worth buying", "Findings", "System overview", "Readings taken"):
        assert section in html
    assert "prefers-color-scheme: dark" in html
    # Changed in v0.2.0: the embedded NiyiSoft badge uses src="data:..." URIs, so "self-contained"
    # now means every src= is an inline data: URI (still no external files, scripts or links).
    assert "<script" not in html and "<link" not in html   # self-contained
    assert html.count("src=") == html.count('src="data:image/png;base64,')
    assert "health score" in html


def test_html_failing_disk_details():
    html = render_html(*run(ALL_FIXTURES["failing_disk_machine"]()))
    assert 'data-light="red"' in html and "Action needed" in html and "Urgent" in html
    assert "Confirmed" in html and "What to do:" in html
    assert html.index("Act now") < html.index("Needs attention") < html.index("Good to know")


def test_html_unknown_cards_grey_with_label():
    html = render_html(*run(SystemSnapshot()))
    assert 'data-light="grey"' in html
    assert 'data-status="good"' not in html and 'data-status="unknown"' in html
    assert "Unknown" in html


def test_html_sample_duration_shown():
    html = render_html(*run(ALL_FIXTURES["slow_old_laptop"]()))
    assert "sampled over 1 s" in html and "Benchmark ran for 31 s" in html


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_markdown_structure(name):
    md = render_markdown(*run(ALL_FIXTURES[name]()))
    assert md.startswith("# My PC Health Report")
    for section in ("## At a glance", "### Free fixes first", "### Upgrades worth buying",
                    "## Findings", "## System overview", "health score"):
        assert section in md


def test_markdown_mirrors_verdict_and_labels():
    md = render_markdown(*run(ALL_FIXTURES["slow_old_laptop"]()))
    assert "Needs attention" in md and "Your PC is slow mainly because" in md
    assert "**What to do:**" in md and "(Confirmed)" in md


def _evil_snapshot():
    evil = '<script>alert("x")</script>|pipe'
    s = ALL_FIXTURES["slow_old_laptop"]()
    s.os = OSInfo(system="Windows", hostname=evil)
    s.top_processes = [ProcessInfo(pid=1, name=evil, memory_percent=50.0)]
    s.errors = [f"collector: {evil}"]
    return s


def test_html_escapes_untrusted_text():
    html = render_html(*run(_evil_snapshot()))
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_markdown_escapes_untrusted_text():
    md = render_markdown(*run(_evil_snapshot()))
    assert "<script>" not in md
    for line in md.splitlines():   # pipes inside table cells must be escaped
        if line.startswith("|") and "pipe" in line:
            assert "\\|pipe" in line


def test_empty_snapshot_renders_both():
    s, f, r = run(SystemSnapshot())
    assert "Can&#x27;t tell" in render_html(s, f, r)
    assert "Can't tell" in render_markdown(s, f, r)
