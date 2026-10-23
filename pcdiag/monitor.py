"""Live monitoring: sample CPU, RAM, swap, disk space, disk I/O and top processes over time.

Roadmap phase 2. Where ``scan`` takes one snapshot, :class:`Monitor` takes a
reading every ``interval`` seconds for ``duration`` seconds (or until
Ctrl+C) and returns a :class:`~pcdiag.models.MonitorSession`. The
sustained-pressure rules in :mod:`pcdiag.analysis` (phase 4) use that
session to tell a short spike from a PC that is *always* struggling.

Design notes
------------
* **Never crashes on missing data.** Every reading is wrapped; anything
  unreadable becomes ``None`` plus a human-readable note on the sample (and,
  deduplicated, on the session). AccessDenied / NoSuchProcess on individual
  processes are normal on Windows without admin and are simply skipped.
* **Priming.** ``psutil.cpu_percent`` and ``Process.cpu_percent`` measure
  "since the previous call", so the first call always returns 0. The
  monitor primes them in :meth:`Monitor.start`, and a process seen for the
  first time is primed and left out of ``top_cpu`` until its next sample.
* **Testable.** psutil, the clocks and ``sleep`` are injectable, so tests
  drive the loop with a fake psutil and a fake clock (no real waiting).

Added in v0.2.0
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import psutil as _psutil

from .collectors import _shell
from .collectors._util import GIB, MIB, rnd
from .collectors.disk_io import compute_rates
from .collectors.disks import _SKIP_FSTYPES, _system_mountpoint
from .models import DiskUsageSample, MonitorSample, MonitorSession, ProcessInfo

#: Defaults used by the CLI (``pcdiag monitor``). Added in v0.2.0.
DEFAULT_INTERVAL = 2.0
DEFAULT_DURATION = 60.0
MIN_INTERVAL = 0.5   # faster than this and psutil's own overhead shows up in the readings

# Same pseudo-processes the scan's process collector skips: Windows' "System
# Idle Process" (pid 0) reports idle time as ~100% CPU per core.
_SKIP_NAMES = {"system idle process", "idle"}


def _utcnow() -> datetime:
    """Current UTC time (default wall clock)."""
    return datetime.now(timezone.utc)


class Monitor:
    """Repeatedly sample the live system into a :class:`MonitorSession`.

    Usage::

        session = Monitor(interval=2, duration=60).run(on_sample=print)

    ``duration=None`` (or 0) means "until Ctrl+C". ``ps``, ``clock``
    (monotonic seconds), ``wall_clock`` (UTC datetime) and ``sleep`` can be
    replaced for tests.
    """

    def __init__(self, interval: float = DEFAULT_INTERVAL, duration: Optional[float] = DEFAULT_DURATION,
                 top_n: int = 5, ps: Any = None, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = _utcnow, sleep: Callable[[float], None] = time.sleep) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.interval = float(interval)
        self.duration = float(duration) if duration else None   # 0/None -> until Ctrl+C
        self.top_n = top_n
        self.ps = ps or _psutil
        self.clock, self.wall_clock, self.sleep = clock, wall_clock, sleep
        self.session: Optional[MonitorSession] = None
        self._t0 = 0.0
        self._procs: dict[int, Any] = {}          # pid -> primed psutil.Process
        self._io_before: Optional[dict] = None    # previous disk_io_counters(perdisk=True)
        self._io_time = 0.0
        self._mounts: list[str] = []

    # -- helpers -----------------------------------------------------------

    def _note(self, sample: Optional[MonitorSample], text: str) -> None:
        """Record an "unavailable" note on the sample and (once) on the session."""
        if sample is not None and text not in sample.notes:
            sample.notes.append(text)
        if self.session is not None and text not in self.session.notes:
            self.session.notes.append(text)

    def _fixed_mounts(self) -> list[str]:
        """Mountpoints of fixed disks (no optical, removable, RAM or snap mounts), one per device."""
        try:
            parts = self.ps.disk_partitions(all=False)
        except Exception as exc:
            self._note(None, f"disk list unavailable: {exc}")
            return []
        system_mp = _system_mountpoint()
        chosen: dict[str, str] = {}
        for p in parts:
            opts = p.opts or ""
            # Same filters as the disks collector, plus "removable" (USB sticks,
            # SD cards on Windows) because their free space isn't a PC problem.
            if not p.fstype or "cdrom" in opts or "removable" in opts or p.fstype.lower() in _SKIP_FSTYPES:
                continue
            if p.mountpoint.startswith(("/snap/", "/boot/efi")):
                continue
            key = p.mountpoint if _shell.is_windows() else p.device  # Linux bind mounts share a device
            cur = chosen.get(key)
            if cur is None or p.mountpoint == system_mp or (cur != system_mp and len(p.mountpoint) < len(cur)):
                chosen[key] = p.mountpoint
        return list(chosen.values())

    def _prime_process(self, proc: Any) -> bool:
        """Start the CPU clock for a new process; False if it's gone or hidden."""
        try:
            proc.cpu_percent(None)
            return True
        except Exception:
            return False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> MonitorSession:
        """Create the session and prime every "since last call" counter."""
        ps = self.ps
        self.session = MonitorSession(started_at=self.wall_clock(), interval_seconds=self.interval,
                                      requested_duration_seconds=self.duration)
        self._t0 = self.clock()
        try:
            self.session.hostname = _shell_hostname()
            self.session.logical_cores = ps.cpu_count(logical=True)
        except Exception:
            pass
        try:
            self.session.ram_total_gb = round(ps.virtual_memory().total / GIB, 2)
        except Exception:
            pass
        try:
            ps.cpu_percent(None)              # prime overall CPU
            ps.cpu_percent(None, percpu=True)  # and per-core (separate internal state)
        except Exception:
            pass
        try:
            for proc in ps.process_iter(["pid", "name"]):
                if self._prime_process(proc):
                    self._procs[proc.pid] = proc
        except Exception as exc:
            self._note(None, f"process list unavailable: {exc}")
        try:
            self._io_before = ps.disk_io_counters(perdisk=True) or None
        except Exception:
            self._io_before = None
        self._io_time = self.clock()
        self._mounts = self._fixed_mounts()
        return self.session

    def take_sample(self) -> MonitorSample:
        """Read everything once and append the sample to the session."""
        if self.session is None:
            self.start()
        assert self.session is not None
        ps = self.ps
        now = self.clock()
        s = MonitorSample(timestamp=self.wall_clock(), elapsed_seconds=round(now - self._t0, 2))

        # CPU (overall and per core)
        try:
            s.cpu_percent = rnd(float(ps.cpu_percent(None)))
        except Exception as exc:
            self._note(s, f"CPU usage unavailable: {exc}")
        try:
            s.per_core_percent = [rnd(float(v)) for v in ps.cpu_percent(None, percpu=True)]  # type: ignore[misc]
        except Exception:
            s.per_core_percent = []  # overall figure is enough; no note needed

        # Memory and swap
        try:
            vm = ps.virtual_memory()
            s.ram_percent = rnd(float(vm.percent))
            s.ram_available_gb = round(vm.available / GIB, 2)
            # Same definition as Task Manager's "In use": total minus available.
            s.ram_used_gb = round((vm.total - vm.available) / GIB, 2)
        except Exception as exc:
            self._note(s, f"RAM usage unavailable: {exc}")
        try:
            sw = ps.swap_memory()
            # No page file / swap at all: percent is meaningless, so unknown.
            s.swap_percent = rnd(float(sw.percent)) if sw.total else None
            if not sw.total:
                self._note(s, "no swap / page file configured")
        except Exception as exc:
            self._note(s, f"swap usage unavailable: {exc}")

        # Disk space per fixed disk
        for mp in self._mounts:
            try:
                u = ps.disk_usage(mp)
                s.disks.append(DiskUsageSample(mountpoint=mp, total_gb=round(u.total / GIB, 2),
                                               free_gb=round(u.free / GIB, 2), used_percent=rnd(float(u.percent))))
            except Exception as exc:
                s.disks.append(DiskUsageSample(mountpoint=mp))
                self._note(s, f"disk space for {mp} unavailable: {exc}")

        sys_mp = _system_mountpoint()
        sys_disk = next((d for d in s.disks if d.mountpoint == sys_mp), None)
        if sys_disk is not None:
            s.system_disk_free_gb, s.system_disk_used_percent = sys_disk.free_gb, sys_disk.used_percent

        self._sample_cpu_temp(s)
        self._sample_disk_io(s, now)
        self._sample_processes(s)
        self.session.samples.append(s)
        return s

    def _sample_cpu_temp(self, s: MonitorSample) -> None:
        """Hottest CPU sensor via psutil (Linux/BSD). Windows has no psutil sensors.

        The scan's Windows fallback (ACPI thermal zones through PowerShell) is
        deliberately *not* polled here: launching PowerShell every couple of
        seconds would itself load the CPU we're trying to measure.
        """
        if _shell.is_windows():
            self._note(s, "CPU temperature not monitored on Windows (no live sensor access without extra drivers)")
            return
        fn = getattr(self.ps, "sensors_temperatures", None)
        try:
            groups = fn() if fn else {}
        except Exception:
            groups = {}
        cpu_groups = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")
        temps = [e.current for g in cpu_groups for e in (groups or {}).get(g, []) if e.current]
        if not temps:  # unknown sensor names: fall back to the hottest of anything
            temps = [e.current for entries in (groups or {}).values() for e in entries if e.current]
        if temps:
            s.cpu_temp_c = rnd(max(temps))
        else:
            self._note(s, "CPU temperature not exposed (common in VMs and on some laptops)")

    def _sample_disk_io(self, s: MonitorSample, now: float) -> None:
        """Disk throughput since the previous sample (reuses the scan's rate maths)."""
        try:
            after = self.ps.disk_io_counters(perdisk=True) or None
        except Exception:
            after = None
        if after is None or self._io_before is None:
            self._note(s, "disk I/O counters not exposed on this system")
        else:
            rates = compute_rates(self._io_before, after, now - self._io_time)
            total = rates[0] if rates else None
            if total is not None:
                s.disk_read_mbps, s.disk_write_mbps = total.read_mbps, total.write_mbps
                s.disk_busy_percent = total.busy_percent
                if total.busy_percent is None:
                    # Windows: psutil has no busy_time, so "how busy" can't be measured.
                    self._note(s, "disk busy % not available on this OS")
        self._io_before, self._io_time = after, now

    def _sample_processes(self, s: MonitorSample) -> None:
        """Top processes by CPU and memory; new processes are primed, not ranked, this round."""
        ps = self.ps
        try:
            current = list(ps.process_iter(["pid", "name", "username"]))
        except Exception as exc:
            self._note(s, f"process list unavailable: {exc}")
            return
        readings: list[ProcessInfo] = []
        fresh: set[int] = set()
        denied = 0
        alive: dict[int, Any] = {}
        for proc in current:
            pid = proc.pid
            known = self._procs.get(pid)
            if known is None:
                # First sighting: prime its CPU clock; CPU% only from next sample.
                if self._prime_process(proc):
                    alive[pid] = proc
                    fresh.add(pid)
                known = proc
            else:
                alive[pid] = known
            try:
                info = getattr(proc, "info", None) or {}
                name = info.get("name") or known.name() or ""
                if pid == 0 or name.lower() in _SKIP_NAMES:
                    continue
                cpu = None if pid in fresh else known.cpu_percent(None)
                mem = known.memory_info().rss
                readings.append(ProcessInfo(pid=pid, name=name, cpu_percent=rnd(cpu),
                                            memory_mb=round(mem / MIB, 1),
                                            memory_percent=rnd(known.memory_percent(), 2),
                                            username=info.get("username")))
            except _psutil.AccessDenied:
                denied += 1   # Windows: protected processes without admin
            except (_psutil.NoSuchProcess, _psutil.ZombieProcess):
                alive.pop(pid, None)   # exited mid-read
            except Exception:
                continue       # one odd process must not sink the sample
        self._procs = alive   # forget processes that exited
        if denied:
            self._note(s, "some processes were hidden (access denied); run as administrator to include them")
        if not readings:
            self._note(s, "no process details readable")
            return
        n = self.top_n
        s.top_cpu = sorted((r for r in readings if r.cpu_percent is not None),
                           key=lambda r: r.cpu_percent or 0, reverse=True)[:n]
        s.top_memory = sorted(readings, key=lambda r: r.memory_mb or 0, reverse=True)[:n]

    def finish(self, interrupted: bool = False) -> MonitorSession:
        """Close the session (end time, interrupted flag) and return it."""
        if self.session is None:
            self.start()
        assert self.session is not None
        self.session.interrupted = interrupted
        # Derive the end from the monotonic clock so a system clock change
        # mid-run can't produce a negative duration.
        self.session.ended_at = self.session.started_at + timedelta(seconds=self.clock() - self._t0)
        return self.session

    def run(self, on_sample: Optional[Callable[[MonitorSession, MonitorSample], None]] = None) -> MonitorSession:
        """Sample until ``duration`` has passed or the user presses Ctrl+C.

        Readings are scheduled on a fixed grid (start + k * interval) so slow
        samples don't make the session drift longer. Ctrl+C is not an error:
        the samples taken so far are kept and ``interrupted`` is set.
        """
        self.start()
        interrupted = False
        k = 0
        try:
            while True:
                k += 1
                target = self._t0 + k * self.interval
                if self.duration is not None and target - self._t0 > self.duration + 1e-9:
                    break
                wait = target - self.clock()
                if wait > 0:
                    self.sleep(wait)
                sample = self.take_sample()
                if on_sample is not None:
                    on_sample(self.session, sample)  # type: ignore[arg-type]
        except KeyboardInterrupt:
            interrupted = True
        return self.finish(interrupted=interrupted)


def _shell_hostname() -> Optional[str]:
    """This PC's name, or None."""
    import platform
    return platform.node() or None


def run_monitor(duration: Optional[float] = DEFAULT_DURATION, interval: float = DEFAULT_INTERVAL,
                top_n: int = 5, on_sample: Optional[Callable[[MonitorSession, MonitorSample], None]] = None
                ) -> MonitorSession:
    """Convenience wrapper: ``Monitor(...).run(on_sample)``."""
    return Monitor(interval=interval, duration=duration, top_n=top_n).run(on_sample=on_sample)


__all__ = ["Monitor", "run_monitor", "DEFAULT_INTERVAL", "DEFAULT_DURATION", "MIN_INTERVAL"]
