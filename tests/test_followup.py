# tests/test_followup.py
# Added in v0.1.0 (follow-up)
"""Tests for the v0.1.0 follow-up: OS-aware wording, benchmark handling,
read_errors / bus_type fields, real Windows data shapes and Administrator errors."""
import dataclasses

import pytest

from pcdiag.analysis import Severity, analyze, needs_admin, recommend_upgrades
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import (
    BenchmarkResult, CPUInfo, DiskInfo, DiskIOStats, DiskKind, MemoryInfo, OSInfo,
    SmartHealth, SmartStatus, SystemSnapshot,
)
from pcdiag.report import (
    BENCHMARK_NOTE, benchmark_rows, evidence_label, metric_cards, render_html, render_markdown, verdict,
)


def ids(findings):
    return {f.id for f in findings}


def by_id(findings, fid):
    return [f for f in findings if f.id == fid]


def hdd_snap(system):
    return SystemSnapshot(os=OSInfo(system=system) if system is not None else None,
                          disks=[DiskInfo(device="/dev/sda2", mountpoint="/", kind=DiskKind.HDD,
                                          is_system_drive=True, percent=40.0)])


# ---- 1. OS-aware wording -----------------------------------------------------

def test_hdd_wording_linux():
    f = by_id(analyze(hdd_snap("Linux")), "storage.hdd_system_drive")[0]
    assert f.title.startswith("Linux runs from a hard disk drive (/")
    assert "Windows" not in f.title + f.cause + f.fix


def test_hdd_wording_macos_and_unknown():
    assert by_id(analyze(hdd_snap("Darwin")), "storage.hdd_system_drive")[0].title.startswith("macOS runs")
    for system in (None, "unknown", ""):
        f = by_id(analyze(hdd_snap(system)), "storage.hdd_system_drive")[0]
        assert f.title.startswith("Your operating system runs from"), system


def test_hdd_wording_windows_unchanged():
    f = by_id(analyze(ALL_FIXTURES["slow_old_laptop"]()), "storage.hdd_system_drive")[0]
    assert f.title.startswith("Windows runs from")


def test_linux_report_has_no_windows_wording_for_ssd_rec():
    s = hdd_snap("Linux")
    recs = recommend_upgrades(s, analyze(s))
    assert recs and "Windows" not in recs[0].reason


# ---- 2. Benchmarks: comparison only, never judged ------------------------------

REAL_BOX = BenchmarkResult(cpu_single_score=88, cpu_multi_score=696, disk_read_mbps=2400,
                           disk_write_mbps=850, memory_mbps=15600, duration_seconds=20.0)


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
@pytest.mark.parametrize("bench", [None, REAL_BOX, BenchmarkResult(cpu_single_score=1, cpu_multi_score=1,
                                                                   disk_read_mbps=1, disk_write_mbps=1,
                                                                   memory_mbps=1)])
def test_benchmark_never_changes_findings(name, bench):
    base = ALL_FIXTURES[name]()
    other = dataclasses.replace(base, benchmark=bench)
    strip = lambda fs: [(f.id, f.severity) for f in fs]
    assert strip(analyze(base)) == strip(analyze(other))
    assert verdict(base, analyze(base)).light == verdict(other, analyze(other)).light


def test_benchmark_shown_raw_with_note():
    s = dataclasses.replace(ALL_FIXTURES["healthy_gaming_pc"](), benchmark=REAL_BOX)
    f = analyze(s)
    rows = dict(benchmark_rows(s))
    assert rows["CPU single-core score"] == "88" and rows["Disk read"] == "2400 MB/s"
    html, md = render_html(s, f, []), render_markdown(s, f, [])
    assert "Benchmark" in html and "2400 MB/s" in html and "same PC" in html
    assert "## Benchmark" in md and "15600 MB/s" in md and "same PC" in BENCHMARK_NOTE
    # Healthy machine with weak scores is still green.
    assert verdict(s, f).light == "green"


def test_no_benchmark_section_when_absent():
    s, f = ALL_FIXTURES["failing_disk_machine"](), None
    f = analyze(s)
    assert benchmark_rows(s) == [] and "<h2>Benchmark</h2>" not in render_html(s, f, [])


# ---- 3. New fields: read_errors, bus_type ------------------------------------

def smart_disk(**kw):
    return SystemSnapshot(disks=[DiskInfo(mountpoint="E:\\", smart=SmartHealth(status=SmartStatus.OK, **kw))])


def test_read_errors_nonzero_is_warning():
    f = by_id(analyze(smart_disk(read_errors=3)), "storage.smart_warning")
    assert f and f[0].severity is Severity.WARNING and f[0].evidence["read_errors"] == 3


@pytest.mark.parametrize("value", [0, None])
def test_read_errors_zero_or_unknown_is_fine(value):
    assert "storage.smart_ok" in ids(analyze(smart_disk(read_errors=value)))


def test_read_errors_card_degrading():
    card = {c.name: c for c in metric_cards(smart_disk(read_errors=1))}["Disk health"]
    assert card.status == "warn"


def test_usb_system_drive_warning():
    s = SystemSnapshot(os=OSInfo(system="Windows"),
                       disks=[DiskInfo(mountpoint="C:\\", kind=DiskKind.SSD, is_system_drive=True, bus_type="USB")])
    f = by_id(analyze(s), "storage.usb_system_drive")
    assert f and f[0].severity is Severity.WARNING and "USB" in f[0].title


@pytest.mark.parametrize("bus,system", [("NVMe", True), ("SATA", True), (None, True), ("USB", False)])
def test_usb_rule_only_for_usb_system_drive(bus, system):
    s = SystemSnapshot(disks=[DiskInfo(mountpoint="X", is_system_drive=system, bus_type=bus)])
    assert "storage.usb_system_drive" not in ids(analyze(s))


def test_bus_type_in_overview():
    s = SystemSnapshot(disks=[DiskInfo(mountpoint="C:\\", kind=DiskKind.NVME, bus_type="NVMe", is_system_drive=True)])
    assert "via NVMe" in render_markdown(s, analyze(s), [])


# ---- 4. Real Windows data shapes ---------------------------------------------

def windows_shape():
    """What a real non-admin Windows scan looks like: several unknowns."""
    return SystemSnapshot(
        os=OSInfo(system="Windows", release="11", hostname="WIN-BOX"),
        cpu=CPUInfo(model="Intel i5", logical_cores=8, base_freq_mhz=2400, current_freq_mhz=2400,
                    max_freq_mhz=None, total_usage_percent=15.0,
                    load_avg_1m=None, load_avg_5m=None, load_avg_15m=None),
        memory=MemoryInfo(total_gb=16.0, percent=40.0),
        disks=[DiskInfo(device="C:\\", mountpoint="C:\\", kind=DiskKind.SSD, bus_type="NVMe",
                        is_system_drive=True, percent=50.0, free_gb=200.0, total_gb=400.0,
                        smart=SmartHealth(status=SmartStatus.OK, source="Get-PhysicalDisk"))],
        disk_io=[DiskIOStats(device=None, read_mbps=5.0, write_mbps=1.0, busy_percent=None, sample_seconds=1.0),
                 DiskIOStats(device="PhysicalDrive0", read_mbps=5.0, busy_percent=None, sample_seconds=1.0)],
        errors=["temperatures: not available: Windows only exposes ACPI thermal zones, which need an "
                "Administrator shell and aren't supported on many PCs",
                "disks: reliability counters (wear, temperature, power-on hours) need an Administrator "
                "PowerShell; showing Windows health status only"],
    )


def test_windows_unknowns_no_crash_no_fault():
    s = windows_shape()
    f = analyze(s)
    assert not [x for x in f if x.is_problem]
    assert "storage.disk_busy" not in ids(f) and "cpu.busy" not in ids(f)
    assert verdict(s, f).light == "green"   # all core data present; unknown extras aren't faults
    render_html(s, f, recommend_upgrades(s, f))


def test_disk_io_all_disks_entry_used_once():
    s = SystemSnapshot(disk_io=[DiskIOStats(device=None, busy_percent=98.0),
                                DiskIOStats(device="sda", busy_percent=10.0)])
    f = by_id(analyze(s), "storage.disk_busy")
    assert len(f) == 1 and "all disks combined" in f[0].title


def test_disk_io_per_device_preferred_over_combined():
    s = SystemSnapshot(disk_io=[DiskIOStats(device=None, busy_percent=99.0),
                                DiskIOStats(device="sdb", busy_percent=99.0),
                                DiskIOStats(device="sda", busy_percent=5.0)])
    f = by_id(analyze(s), "storage.disk_busy")
    assert [x.evidence["device"] for x in f] == ["sdb"]


def test_throttle_check_handles_missing_freqs():
    s = SystemSnapshot(cpu=CPUInfo(total_usage_percent=95.0, current_freq_mhz=None, base_freq_mhz=None,
                                   max_freq_mhz=None),
                       temperatures=[])
    analyze(s)  # must not raise


# ---- 5. Administrator errors -> "can't tell", grey ------------------------------

def test_needs_admin_detection():
    assert needs_admin("temperatures: need an Administrator shell")
    assert needs_admin("disks: run as administrator")
    assert not needs_admin("benchmark: skipped (not requested)")


def test_admin_errors_are_cant_tell_info():
    f = analyze(windows_shape())
    admin = [x for x in f if x.id.startswith("data.needs_admin.")]
    assert len(admin) == 2
    for x in admin:
        assert x.severity is Severity.INFO and not x.is_problem
        assert x.title.startswith("Can't tell") and evidence_label(x) == "Can't tell"
    assert "thermal.unknown" not in ids(f)       # not reported twice
    assert not any(x.id.startswith("data.collector_error") for x in f)


def test_admin_cards_say_run_as_administrator():
    cards = {c.name: c for c in metric_cards(windows_shape())}
    t = cards["Temperature"]
    assert t.status == "unknown" and t.colour == "grey" and t.meaning == "Unknown - run as Administrator"
    # Basic health is known (OK from Windows), details need admin: green but says so.
    assert cards["Disk health"].status == "good" and "Administrator" in cards["Disk health"].meaning
    # Unrelated unknown cards keep their normal wording.
    assert cards["Battery"].meaning != "Unknown - run as Administrator"


def test_admin_card_when_disk_health_unknown():
    s = windows_shape()
    s.disks[0].smart = None
    assert {c.name: c for c in metric_cards(s)}["Disk health"].meaning == "Unknown - run as Administrator"


def test_admin_shown_grey_in_reports():
    s = windows_shape()
    f = analyze(s)
    html, md = render_html(s, f, []), render_markdown(s, f, [])
    assert "Unknown - run as Administrator" in html and "Unknown - run as Administrator" in md
    assert "Can&#x27;t tell: temperatures (needs Administrator)" in html
    assert "(Can't tell)" in md
