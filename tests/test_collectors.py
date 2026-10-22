"""Cross-platform collector tests: shell helper, sampling maths, error handling, live smoke run.

Added in v0.1.0
"""
from __future__ import annotations

import sys
from collections import namedtuple
from types import SimpleNamespace

import psutil
import pytest

import pcdiag.collectors as collectors
from pcdiag.collectors import _shell, disk_io, processes, startup, temperatures
from pcdiag.models import SystemSnapshot

IO = namedtuple("IO", "read_count write_count read_bytes write_bytes busy_time")
IOWin = namedtuple("IOWin", "read_count write_count read_bytes write_bytes")  # no busy_time on Windows
MiB = 1024 ** 2


def test_run_powershell_returns_none_off_windows(monkeypatch):
    """Run powershell returns none off windows."""
    monkeypatch.setattr(_shell, "is_windows", lambda: False)
    errors = []
    assert _shell.run_powershell("Get-Date", on_error=errors.append) is None
    assert errors == []  # not applicable is not an error


def test_run_powershell_json_parses_and_reports_bad_json(monkeypatch):
    """Run powershell json parses and reports bad json."""
    monkeypatch.setattr(_shell, "run_powershell", lambda *a, **k: '\ufeff{"a": 1}')
    assert _shell.run_powershell_json("x") == {"a": 1}
    monkeypatch.setattr(_shell, "run_powershell", lambda *a, **k: "WARNING: oops")
    errors = []
    assert _shell.run_powershell_json("x", on_error=errors.append) is None
    assert "not valid JSON" in errors[0]


def test_run_command_missing_and_timeout():
    """Run command missing and timeout."""
    errors = []
    assert _shell.run_command(["definitely-not-a-real-command-xyz"], on_error=errors.append) is None
    assert "not found" in errors[0]
    assert _shell.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2,
                              on_error=errors.append) is None
    assert "timed out" in errors[1]
    assert _shell.run_command([sys.executable, "-c", "print('hi')"]).strip() == "hi"


def test_as_list():
    """As list."""
    assert _shell.as_list(None) == [] and _shell.as_list({"a": 1}) == [{"a": 1}] and _shell.as_list([1]) == [1]


def test_disk_io_rates_linux_and_filtering():
    """Disk io rates linux and filtering."""
    before = {"sda": IO(0, 0, 0, 0, 0), "sda1": IO(0, 0, 0, 0, 0), "loop0": IO(0, 0, 0, 0, 0),
              "nvme0n1": IO(0, 0, 0, 0, 0)}
    after = {"sda": IO(10, 5, 20 * MiB, 10 * MiB, 1900), "sda1": IO(10, 5, 20 * MiB, 10 * MiB, 1900),
             "loop0": IO(1, 1, MiB, MiB, 5), "nvme0n1": IO(1, 1, 2 * MiB, 0, 100)}
    total, *per = disk_io.compute_rates(before, after, 2.0)
    assert [p.device for p in per] == ["nvme0n1", "sda"]  # partition and loop device dropped
    assert total.device is None and total.read_mbps == 11.0 and total.write_mbps == 5.0
    assert total.busy_percent == 95.0  # busiest disk, not the average


def test_disk_io_rates_windows_has_no_busy():
    """Disk io rates windows has no busy."""
    total, disk = disk_io.compute_rates({"PhysicalDrive0": IOWin(0, 0, 0, 0)},
                                        {"PhysicalDrive0": IOWin(4, 4, 4 * MiB, 0)}, 1.0)
    assert disk.busy_percent is None and total.busy_percent is None and total.read_mbps == 4.0


def test_disk_io_unavailable_records_error(monkeypatch):
    """Disk io unavailable records error."""
    monkeypatch.setattr(disk_io.psutil, "disk_io_counters", lambda perdisk=True: {})
    snap = SystemSnapshot()
    assert disk_io.collect(snap, interval=0) == []
    assert snap.errors[0].startswith("disk_io:")


class FakeProc:
    """Enough of psutil.Process for the processes collector."""

    def __init__(self, pid, name, cpu, rss_mb, denied=False):
        """Store the fake process's pid, name, CPU and memory figures."""
        self.pid, self.info = pid, {"pid": pid, "name": name, "username": "me"}
        self._cpu, self._rss, self._denied = cpu, rss_mb * MiB, denied

    def cpu_percent(self, interval=None):
        """First call primes (0.0) in real psutil; tests just return the value."""
        if self._denied:
            raise psutil.AccessDenied(self.pid)
        return self._cpu

    def oneshot(self):
        """No-op context manager."""
        import contextlib
        return contextlib.nullcontext()

    def memory_info(self):
        """Resident set size only."""
        return SimpleNamespace(rss=self._rss)

    def memory_percent(self):
        """Pretend total RAM is 1000 MiB."""
        return self._rss / MiB / 10


def test_processes_top_n_union(monkeypatch):
    """Processes top n union."""
    procs = [FakeProc(0, "System Idle Process", 750.0, 0), FakeProc(4, "MsMpEng.exe", 40.0, 300),
             FakeProc(5, "chrome.exe", 5.0, 1500), FakeProc(6, "idle.exe", 1.0, 10),
             FakeProc(7, "csrss.exe", 0, 5, denied=True)]
    monkeypatch.setattr(processes.psutil, "process_iter", lambda attrs=None: list(procs))
    result = processes.collect(SystemSnapshot(), top_n=1, interval=0)
    assert [p.name for p in result] == ["MsMpEng.exe", "chrome.exe"]  # top CPU + top memory
    assert result[1].memory_mb == 1500.0


def test_linux_autostart(tmp_path):
    """Linux autostart."""
    (tmp_path / "a.desktop").write_text("[Desktop Entry]\nName=Syncthing\nExec=syncthing -no-browser\n")
    (tmp_path / "b.desktop").write_text("[Desktop Entry]\nName=Old\nExec=old\nHidden=true\n")
    (tmp_path / "c.desktop").write_text("garbage without section")
    items = startup.collect_linux(tmp_path)
    assert [(i.name, i.enabled) for i in items] == [("Syncthing", True), ("Old", False)]


def test_linux_temperatures_from_psutil(monkeypatch):
    """Linux temperatures from psutil."""
    Temp = namedtuple("Temp", "label current high critical")
    monkeypatch.setattr(temperatures._shell, "is_windows", lambda: False)
    monkeypatch.setattr(temperatures.psutil, "sensors_temperatures",
                        lambda: {"coretemp": [Temp("Package id 0", 61.0, 80.0, 100.0), Temp("", 55.0, None, 0)]},
                        raising=False)
    readings = temperatures.collect(SystemSnapshot())
    assert readings[0].label == "Package id 0" and readings[0].high_c == 80.0
    assert readings[1].label == "coretemp 1" and readings[1].critical_c is None


def test_collect_snapshot_survives_crashing_collector(monkeypatch):
    """Collect snapshot survives crashing collector."""
    def boom(snapshot, **kw):
        """A collector with a bug."""
        raise RuntimeError("kaboom")

    monkeypatch.setattr(collectors.gpu, "collect", boom)
    snap = collectors.collect_snapshot(quick=True)
    assert snap.gpus == []
    assert any(e.startswith("gpu: unexpected RuntimeError") for e in snap.errors)
    assert snap.cpu is not None and snap.memory is not None


def test_live_quick_snapshot_round_trips():
    """Smoke test on the real machine: partial data is fine, crashing isn't."""
    snap = collectors.collect_snapshot(quick=True)
    assert snap.cpu and snap.cpu.logical_cores
    assert snap.memory and snap.memory.total_gb
    assert snap.disks and any(d.is_system_drive for d in snap.disks)
    assert SystemSnapshot.from_json(snap.to_json()) == snap
