# tests/test_analysis.py
# Added in v0.1.0
"""Tests for pcdiag.analysis rules and the analyze() entry point."""
import pytest

from pcdiag.analysis import RULES, Finding, Severity, analyze
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import (
    BatteryInfo, CPUInfo, DiskInfo, DiskKind, GPUInfo, MemoryInfo, OSInfo, ProcessInfo,
    SmartHealth, SmartStatus, StartupItem, SystemSnapshot, TemperatureReading,
)


def ids(findings):
    """Set of finding ids, for concise assertions."""
    return {f.id for f in findings}


def problems(findings):
    """Only WARNING/CRITICAL findings."""
    return [f for f in findings if f.is_problem]


# ---- fixtures --------------------------------------------------------------

def test_healthy_pc_has_no_problems():
    findings = analyze(ALL_FIXTURES["healthy_gaming_pc"]())
    assert problems(findings) == []
    # Positive checks are backed by real data.
    assert {"memory.pressure", "storage.system_drive_type", "storage.smart_ok", "thermal.cpu"} <= ids(findings)
    assert all(f.severity is Severity.OK for f in findings)


def test_slow_laptop_hits_expected_rules():
    findings = analyze(ALL_FIXTURES["slow_old_laptop"]())
    expected = {"storage.hdd_system_drive", "memory.low_total", "memory.pressure",
                "storage.system_disk_full", "startup.too_many", "battery.worn",
                "thermal.cpu", "gpu.driver_outdated"}
    got = {f.id for f in problems(findings)}
    assert expected <= got
    thermal = next(f for f in findings if f.id == "thermal.cpu")
    assert thermal.evidence["likely_throttling"] is True
    assert "processes.heavy" in ids(findings)       # chrome at 33% RAM
    assert "system.long_uptime" in ids(findings)    # 13 days


def test_failing_disk_machine_is_critical():
    findings = analyze(ALL_FIXTURES["failing_disk_machine"]())
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].id == "storage.smart_failing"
    assert "D:" in findings[0].evidence["drive"]
    # Collector errors surface as INFO; temps aren't claimed fine or reported twice.
    errs = [f for f in findings if f.id.startswith("data.collector_error")]
    assert len(errs) == 2 and all(f.severity is Severity.INFO for f in errs)
    assert "thermal.unknown" not in ids(findings)
    assert not any(f.id == "thermal.cpu" for f in findings)


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_findings_sorted_by_severity(name):
    ranks = [f.severity.rank for f in analyze(ALL_FIXTURES[name]())]
    assert ranks == sorted(ranks, reverse=True)


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_findings_json_roundtrip(name):
    for f in analyze(ALL_FIXTURES[name]()):
        assert Finding.from_json(f.to_json()) == f


# ---- None handling -----------------------------------------------------------

def test_empty_snapshot_does_not_crash_or_claim_healthy():
    findings = analyze(SystemSnapshot())
    assert not any(f.severity is Severity.OK for f in findings)
    assert "data.incomplete" in ids(findings)


def test_all_none_sections_do_not_crash():
    """Every section present but every value unknown."""
    s = SystemSnapshot(
        os=OSInfo(), cpu=CPUInfo(), memory=MemoryInfo(),
        disks=[DiskInfo(is_system_drive=True, smart=SmartHealth())],
        gpus=[GPUInfo()], temperatures=[TemperatureReading(label="CPU")],
        battery=BatteryInfo(), top_processes=[ProcessInfo()], startup_items=[],
    )
    findings = analyze(s)
    assert not any(f.severity is Severity.OK for f in findings)
    assert problems(findings) == []
    assert {"storage.system_drive_unknown", "storage.smart_unknown", "thermal.unknown"} <= ids(findings)


def test_every_rule_tolerates_empty_snapshot():
    for name, fn in RULES.items():
        list(fn(SystemSnapshot()))  # must not raise


# ---- individual rules with hand-built snapshots ------------------------------

def _snap(**kw):
    return SystemSnapshot(**kw)


def test_battery_thresholds():
    def bat(wear):
        return analyze(_snap(battery=BatteryInfo(wear_percent=wear)))
    assert "battery.ok" in ids(bat(10))
    assert "battery.worn" in ids(bat(25))      # 75% health < 80%
    assert "battery.replace" in ids(bat(65))   # 35% health < 40%


def test_battery_health_from_capacities():
    s = _snap(battery=BatteryInfo(design_capacity_mwh=50000, full_charge_capacity_mwh=15000))
    f = next(f for f in analyze(s) if f.id.startswith("battery."))
    assert f.id == "battery.replace" and f.evidence["health_percent"] == 30.0


def test_smart_warning_and_bad_sectors():
    disks = [DiskInfo(mountpoint="E:\\", smart=SmartHealth(status=SmartStatus.WARNING)),
             DiskInfo(mountpoint="F:\\", smart=SmartHealth(status=SmartStatus.OK, reallocated_sectors=8))]
    found = [f for f in analyze(_snap(disks=disks)) if f.id == "storage.smart_warning"]
    assert len(found) == 2 and all(f.severity is Severity.WARNING for f in found)


def test_disk_full_system_critical_and_data_drive():
    disks = [DiskInfo(mountpoint="C:\\", percent=98.0, free_gb=2.0, is_system_drive=True, kind=DiskKind.SSD),
             DiskInfo(mountpoint="D:\\", percent=96.0, free_gb=40.0)]
    f = {x.id: x for x in analyze(_snap(disks=disks))}
    assert f["storage.system_disk_full"].severity is Severity.CRITICAL
    assert f["storage.data_disk_full"].severity is Severity.WARNING


def test_cpu_critical_temperature():
    s = _snap(temperatures=[TemperatureReading(label="Package id 0", current_c=97.0)])
    f = next(x for x in analyze(s) if x.id == "thermal.cpu")
    assert f.severity is Severity.CRITICAL


def test_startup_disabled_items_not_counted():
    items = [StartupItem(name=f"a{i}", enabled=False) for i in range(30)]
    assert "startup.too_many" not in ids(analyze(_snap(startup_items=items)))


def test_heavy_process_cpu_normalised_by_cores():
    procs = [ProcessInfo(pid=1, name="miner.exe", cpu_percent=400.0)]
    # 400% on 8 cores = 50% of the whole CPU -> heavy
    assert "processes.heavy" in ids(analyze(_snap(cpu=CPUInfo(logical_cores=8), top_processes=procs)))
    # 20% on 16 cores = 1.25% -> not heavy
    procs = [ProcessInfo(pid=1, name="steam.exe", cpu_percent=20.0)]
    assert "processes.heavy" not in ids(analyze(_snap(cpu=CPUInfo(logical_cores=16), top_processes=procs)))


def test_gpu_driver_without_date_is_skipped():
    s = _snap(gpus=[GPUInfo(name="X", driver_version="1.0")])
    assert "gpu.driver_outdated" not in ids(analyze(s))


def test_low_ram_mentions_free_slot():
    s = _snap(memory=MemoryInfo(total_gb=4.0, percent=50.0, slots_used=1, slots_total=2))
    f = next(x for x in analyze(s) if x.id == "memory.low_total")
    assert "free RAM slot" in f.cause
