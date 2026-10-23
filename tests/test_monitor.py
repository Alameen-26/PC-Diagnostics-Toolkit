"""Tests for live monitoring (roadmap phase 2): models, stats helpers, sampler, CLI.

The sampler is driven with a fake psutil and a fake clock, so nothing here
sleeps or depends on the machine running the tests.

Added in v0.2.0
"""
from __future__ import annotations

import json
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import psutil
import pytest

from pcdiag import __main__ as cli
from pcdiag.fixtures import ALL_FIXTURES, ALL_MONITOR_FIXTURES, with_monitor
from pcdiag.models import (DiskUsageSample, MonitorSample, MonitorSession, ProcessInfo,
                           SystemSnapshot)
from pcdiag.monitor import Monitor
from pcdiag.terminal import LiveView, bar, monitor_live_lines, monitor_summary_lines

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def session_of(values, metric="cpu_percent", interval=2.0):
    """Session whose samples carry ``values`` (None allowed) for one metric."""
    samples = [MonitorSample(timestamp=T0 + timedelta(seconds=interval * (i + 1)),
                             elapsed_seconds=interval * (i + 1), **{metric: v}) for i, v in enumerate(values)]
    return MonitorSession(started_at=T0, ended_at=T0 + timedelta(seconds=interval * len(values)),
                          interval_seconds=interval, samples=samples)


# --- models ------------------------------------------------------------------

def test_session_json_round_trip():
    s = ALL_MONITOR_FIXTURES["slow_old_laptop"]()
    back = MonitorSession.from_json(s.to_json())
    assert back == s
    assert isinstance(back.samples[0].timestamp, datetime)
    assert isinstance(back.samples[0].top_cpu[0], ProcessInfo)
    assert isinstance(back.samples[0].disks[0], DiskUsageSample)


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_snapshot_round_trip_keeps_monitor(name):
    snap = with_monitor(name)
    back = SystemSnapshot.from_json(snap.to_json())
    assert back.monitor == snap.monitor and back.monitor.sample_count == 150


def test_snapshot_without_monitor_still_loads_and_defaults_to_none():
    data = ALL_FIXTURES["healthy_gaming_pc"]().to_dict()
    data.pop("monitor")  # a v0.1.0 snapshot file has no such key
    assert SystemSnapshot.from_dict(data).monitor is None


def test_base_fixtures_have_no_monitor_data():
    # New Bot's tests rely on this: the plain fixtures cover the no-monitor path.
    assert all(fn().monitor is None for fn in ALL_FIXTURES.values())


def test_duration_from_end_time_or_last_sample():
    s = session_of([1, 2, 3])
    assert s.duration_seconds == 6.0 and s.sample_count == 3
    s.ended_at = None
    assert s.duration_seconds == 6.0
    assert MonitorSession().duration_seconds == 0.0 or MonitorSession().ended_at is None


# --- stats helpers -------------------------------------------------------------

def test_basic_stats_skip_none():
    s = session_of([10.0, None, 30.0, 20.0])
    assert s.values("cpu_percent") == [10.0, 30.0, 20.0]
    assert s.missing_count("cpu_percent") == 1
    assert s.mean("cpu_percent") == 20.0
    assert (s.min("cpu_percent"), s.max("cpu_percent")) == (10.0, 30.0)


def test_p95_nearest_rank():
    s = session_of([float(v) for v in range(1, 101)])
    assert s.p95("cpu_percent") == 95.0
    assert session_of([5.0]).p95("cpu_percent") == 5.0


def test_percent_above_counts_known_samples_only():
    s = session_of([95.0, 50.0, None, 90.0])
    assert s.percent_above("cpu_percent", 90) == pytest.approx(200 / 3)


def test_longest_streak_is_broken_by_none_and_low_values():
    s = session_of([95, 96, None, 97, 98, 99, 10, 91])
    assert s.longest_streak_above("cpu_percent", 90) == 6.0  # three samples x 2 s
    st = s.stats("cpu_percent", 90)
    assert st.longest_streak_samples == 3 and st.missing == 1 and st.count == 7


def test_empty_metric_gives_none_not_zero():
    s = session_of([None, None])
    st = s.stats("cpu_percent", 90)
    assert st.mean is None and st.p95 is None and st.percent_above is None and st.longest_streak_seconds == 0
    assert MonitorSession().mean("ram_percent") is None


def test_disk_metric_by_mountpoint_and_unknown_metric():
    smp = MonitorSample(disks=[DiskUsageSample(mountpoint="C:\\", used_percent=80.0)])
    s = MonitorSession(samples=[smp])
    assert s.mountpoints() == ["C:\\"]
    assert s.values("disk_used_percent:C:\\") == [80.0]
    assert s.values("disk_used_percent:D:\\") == []
    with pytest.raises(KeyError):
        s.values("nonsense")


def test_process_summary_aggregates_by_name():
    a = ProcessInfo(pid=1, name="chrome.exe", cpu_percent=10.0, memory_mb=500.0)
    b = ProcessInfo(pid=2, name="chrome.exe", cpu_percent=20.0, memory_mb=300.0)
    c = ProcessInfo(pid=3, name="idle.exe", cpu_percent=1.0, memory_mb=50.0)
    s = MonitorSession(samples=[MonitorSample(top_cpu=[a, b, c], top_memory=[a, b]),
                                MonitorSample(top_cpu=[a], top_memory=[a])])
    top = s.process_summary(by="cpu")
    assert top[0].name == "chrome.exe" and top[0].samples_seen == 2
    assert top[0].mean_cpu_percent == 20.0 and top[0].max_memory_mb == 800.0  # pid dedupe per sample
    assert s.process_summary(by="memory", n=1)[0].name == "chrome.exe"
    with pytest.raises(ValueError):
        s.process_summary(by="disk")


def test_fixture_patterns_match_their_story():
    laptop = ALL_MONITOR_FIXTURES["slow_old_laptop"]()
    assert laptop.percent_above("ram_percent", 85) == 100.0
    assert 25 <= laptop.percent_above("cpu_percent", 90) <= 40       # bursts, not constant
    assert laptop.duration_seconds == 300 and laptop.interval_seconds == 2.0
    gaming = ALL_MONITOR_FIXTURES["healthy_gaming_pc"]()
    assert gaming.max("ram_percent") < 40
    office = ALL_MONITOR_FIXTURES["failing_disk_machine"]()
    assert office.values("cpu_temp_c") == [] and office.notes


# --- sampler with a fake psutil -------------------------------------------------

VM = namedtuple("VM", "total available percent")
SW = namedtuple("SW", "total used percent")
Part = namedtuple("Part", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")
IO = namedtuple("IO", "read_bytes write_bytes read_count write_count busy_time")
GiB = 1024 ** 3


class FakeProc:
    """psutil.Process stand-in; ``error`` is raised by every reading."""

    def __init__(self, pid, name, cpu=5.0, rss=100 * 1024 ** 2, error=None):
        self.pid, self.info, self._cpu, self._rss, self.error = pid, {"name": name, "username": None}, cpu, rss, error
        self.cpu_calls = 0

    def _check(self):
        """Raise the configured error, if any."""
        if self.error:
            raise self.error

    def cpu_percent(self, interval=None):
        self.cpu_calls += 1
        self._check()
        return 0.0 if self.cpu_calls == 1 else self._cpu

    def memory_info(self):
        self._check()
        return namedtuple("M", "rss")(self._rss)

    def memory_percent(self):
        self._check()
        return 1.0

    def name(self):
        return self.info["name"]


class FakePsutil:
    """Enough of psutil for Monitor; flags switch individual readings off."""

    def __init__(self, procs, broken=()):
        self.procs, self.broken, self.io_reads = procs, set(broken), 0

    def _maybe(self, what):
        if what in self.broken:
            raise RuntimeError(f"{what} broken")

    def cpu_count(self, logical=True):
        return 4

    def cpu_percent(self, interval=None, percpu=False):
        self._maybe("cpu")
        return [10.0, 20.0, 30.0, 40.0] if percpu else 25.0

    def virtual_memory(self):
        self._maybe("ram")
        return VM(total=8 * GiB, available=2 * GiB, percent=75.0)

    def swap_memory(self):
        return SW(total=0, used=0, percent=0.0)  # no page file

    def disk_partitions(self, all=False):
        return [Part("/dev/sda1", "/", "ext4", "rw"), Part("/dev/sr0", "/media/cd", "iso9660", "ro,cdrom"),
                Part("/dev/sdb1", "/media/usb", "vfat", "rw,removable")]

    def disk_usage(self, mp):
        self._maybe("disk")
        return Usage(total=100 * GiB, used=40 * GiB, free=60 * GiB, percent=40.0)

    def disk_io_counters(self, perdisk=False):
        self._maybe("io")
        self.io_reads += 1
        n = self.io_reads
        return {"sda": IO(n * 10 * 1024 ** 2, n * 1024 ** 2, n, n, n * 1000)}

    def process_iter(self, attrs=None):
        return list(self.procs)


class FakeClock:
    """Monotonic clock that only moves when ``sleep`` is called."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_monitor(fake, duration=6.0, interval=2.0, **kw):
    clock = FakeClock()
    return Monitor(interval=interval, duration=duration, ps=fake, clock=clock, sleep=clock.sleep,
                   wall_clock=lambda: T0 + timedelta(seconds=clock.now - 1000.0), **kw)


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    """Run the sampler as on Linux (system drive "/"), whatever the host OS."""
    from pcdiag.collectors import _shell
    monkeypatch.setattr(_shell, "is_windows", lambda: False)


def test_sampler_takes_expected_samples_and_primes_cpu():
    procs = [FakeProc(10, "chrome", cpu=50.0), FakeProc(11, "code", cpu=5.0)]
    s = make_monitor(FakePsutil(procs)).run()
    assert s.sample_count == 3 and [x.elapsed_seconds for x in s.samples] == [2.0, 4.0, 6.0]
    first = s.samples[0]
    assert first.cpu_percent == 25.0 and first.per_core_percent == [10.0, 20.0, 30.0, 40.0]
    assert first.ram_percent == 75.0 and first.ram_used_gb == 6.0 and first.ram_available_gb == 2.0
    assert first.swap_percent is None and "no swap / page file configured" in s.notes
    # Process CPU was primed in start(), so the first sample already has real values.
    assert first.top_cpu[0].name == "chrome" and first.top_cpu[0].cpu_percent == 50.0
    assert s.duration_seconds == 6.0 and not s.interrupted


def test_sampler_keeps_only_fixed_disks_and_fills_system_drive():
    smp = make_monitor(FakePsutil([]), duration=2).run().samples[0]
    assert [d.mountpoint for d in smp.disks] == ["/"]
    assert smp.system_disk_free_gb == 60.0 and smp.system_disk_used_percent == 40.0


def test_disk_io_rates_between_samples():
    smp = make_monitor(FakePsutil([]), duration=2).run().samples[0]
    assert smp.disk_read_mbps == pytest.approx(5.0) and smp.disk_busy_percent == pytest.approx(50.0)


def test_unavailable_readings_become_none_with_notes():
    s = make_monitor(FakePsutil([], broken={"cpu", "ram", "disk", "io"}), duration=2).run()
    smp = s.samples[0]
    assert smp.cpu_percent is None and smp.ram_percent is None and smp.disk_read_mbps is None
    assert smp.disks[0].used_percent is None and smp.system_disk_free_gb is None
    joined = " ".join(s.notes)
    for word in ("CPU usage unavailable", "RAM usage unavailable", "disk space", "I/O counters", "no process details"):
        assert word in joined


def test_access_denied_and_vanished_processes_are_skipped():
    procs = [FakeProc(1, "ok", cpu=12.0),
             FakeProc(2, "protected", error=psutil.AccessDenied(2)),
             FakeProc(3, "gone", error=psutil.NoSuchProcess(3))]
    s = make_monitor(FakePsutil(procs), duration=2).run()
    assert [p.name for p in s.samples[0].top_memory] == ["ok"]
    assert any("access denied" in n for n in s.notes)


def test_new_process_is_primed_not_ranked_on_first_sight():
    fake = FakePsutil([FakeProc(1, "old", cpu=10.0)])
    mon = make_monitor(fake, duration=4)
    mon.start()
    fake.procs.append(FakeProc(2, "new", cpu=80.0))
    mon.clock.sleep(2)
    first = mon.take_sample()
    assert [p.name for p in first.top_cpu] == ["old"]           # "new" has no valid CPU% yet
    assert {p.name for p in first.top_memory} == {"old", "new"}  # but its memory is known
    mon.clock.sleep(2)
    assert mon.take_sample().top_cpu[0].name == "new"


def test_pid_zero_and_idle_are_ignored():
    s = make_monitor(FakePsutil([FakeProc(0, "System Idle Process", cpu=400.0), FakeProc(5, "x")]), duration=2).run()
    assert [p.name for p in s.samples[0].top_cpu] == ["x"]


def test_ctrl_c_keeps_samples_and_marks_interrupted():
    calls = []

    def on_sample(sess, smp):
        calls.append(smp)
        if len(calls) == 2:
            raise KeyboardInterrupt

    s = make_monitor(FakePsutil([]), duration=None).run(on_sample=on_sample)
    assert s.interrupted and s.sample_count == 2 and s.requested_duration_seconds is None


def test_invalid_interval_rejected():
    with pytest.raises(ValueError):
        Monitor(interval=0)


def test_real_psutil_smoke():
    # One quick real sample: must not raise on whatever machine runs the tests.
    s = Monitor(interval=0.5, duration=0.5).run()
    assert s.sample_count == 1


# --- terminal view ---------------------------------------------------------------

def test_bar_and_live_lines_are_ascii():
    assert bar(50, 10) == "[#####.....]" and "unavailable" in bar(None)
    s = ALL_MONITOR_FIXTURES["slow_old_laptop"]()
    lines = monitor_live_lines(s, s.samples[-1]) + monitor_summary_lines(s)
    text = "\n".join(lines)
    text.encode("ascii")
    assert "RAM" in text and "chrome.exe" in text and "of the time >= 90%" in text


def test_summary_handles_no_samples_and_missing_metrics():
    assert "No samples" in "\n".join(monitor_summary_lines(MonitorSession()))
    s = ALL_MONITOR_FIXTURES["failing_disk_machine"]()
    assert "Not available during monitoring" in "\n".join(monitor_summary_lines(s))


def test_live_view_redraws_in_place_with_ansi():
    import io
    buf = io.StringIO()
    view = LiveView(stream=buf, ansi=True)
    s = ALL_MONITOR_FIXTURES["healthy_gaming_pc"]()
    view.update(s, s.samples[0])
    view.update(s, s.samples[1])
    assert "\x1b[" in buf.getvalue() and "F\x1b[J" in buf.getvalue()
    plain = io.StringIO()
    LiveView(stream=plain, ansi=False).update(s, s.samples[0])
    assert "\x1b" not in plain.getvalue() and plain.getvalue().count("\n") == 1


# --- CLI -------------------------------------------------------------------------

def test_monitor_args_defaults_and_validation():
    p = cli.build_parser()
    a = p.parse_args(["monitor"])
    assert (a.duration, a.interval, a.json, a.no_save, a.output_dir, a.top) == (60.0, 2.0, False, False, "reports", 5)
    a = p.parse_args(["monitor", "--duration", "0", "--interval", "1", "--json", "--no-save", "--output-dir", "x"])
    assert a.duration == 0 and a.interval == 1.0 and a.json and a.no_save and a.output_dir == "x"
    for bad in (["monitor", "--interval", "0.1"], ["monitor", "--duration", "abc"], ["monitor", "--duration", "-5"]):
        with pytest.raises(SystemExit):
            p.parse_args(bad)


def test_scan_monitor_accepts_300():
    a = cli.build_parser().parse_args(["scan", "--monitor", "300"])
    assert a.monitor == 300.0 and a.monitor_interval == 2.0
    assert cli.build_parser().parse_args(["scan"]).monitor is None


def test_demo_monitor_flag():
    assert cli.build_parser().parse_args(["demo", "slow_old_laptop", "--monitor"]).monitor


def fake_session(*a, **k):
    """Stand-in for a real monitor run."""
    return ALL_MONITOR_FIXTURES["slow_old_laptop"]()


def test_cmd_monitor_saves_json_and_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run_monitor_session", fake_session)
    assert cli.main(["monitor", "--duration", "10", "--output-dir", str(tmp_path)]) == 0
    files = list(tmp_path.glob("monitor-*.json"))
    assert len(files) == 1 and MonitorSession.from_json(files[0].read_text()).sample_count == 150
    assert "Monitor summary" in capsys.readouterr().out


def test_cmd_monitor_json_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run_monitor_session", fake_session)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["monitor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["interval_seconds"] == 2.0
    assert not list(tmp_path.iterdir())


def test_scan_monitor_attaches_session(monkeypatch, capsys):
    import pcdiag.collectors as collectors
    seen = {}
    monkeypatch.setattr(cli, "_run_monitor_session", lambda d, i, n, live: seen.update(d=d, live=live) or fake_session())
    monkeypatch.setattr(collectors, "collect_snapshot", lambda **k: ALL_FIXTURES["slow_old_laptop"]())
    assert cli.main(["scan", "--monitor", "300", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert seen == {"d": 300.0, "live": False} and len(data["monitor"]["samples"]) == 150


def test_demo_with_monitor_shows_sustained_findings(tmp_path, capsys):
    assert cli.main(["demo", "slow_old_laptop", "--monitor", "--output-dir", str(tmp_path)]) == 0
    html = next(tmp_path.glob("report-demo-slow_old_laptop-monitor.html")).read_text(encoding="utf-8")
    assert "Over time" in html
    out = capsys.readouterr().out
    assert "Over time (5.0 min monitored, 150 readings):" in out
    assert "[WARNING] Memory stays nearly full" in out


def test_demo_without_monitor_has_no_over_time_block(tmp_path, capsys):
    assert cli.main(["demo", "slow_old_laptop", "--no-save"]) == 0
    assert "Over time" not in capsys.readouterr().out
