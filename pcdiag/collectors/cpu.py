"""CPU collector: model name, core counts, clock speeds, usage and load.

Usage is sampled over ``interval`` seconds (about 1 s by default) because an
instantaneous reading is meaningless: psutil compares two CPU-time
counters, so it needs a window to measure.

Added in v0.1.0
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Optional

import psutil

from ..models import CPUInfo, SystemSnapshot
from . import _shell
from ._util import rnd


def _windows_cpu_name() -> Optional[str]:
    """Read the marketing CPU name from the registry on Windows.

    ``platform.processor()`` on Windows returns something like
    "Intel64 Family 6 Model 142 Stepping 10, GenuineIntel", which is useless
    to a human. The registry key below holds "Intel(R) Core(TM) i5-8250U ..."
    and is readable without admin rights.
    """
    try:
        import winreg  # Windows-only module

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        with key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        return str(value).strip() or None
    except Exception:
        return None


def _linux_cpu_name() -> Optional[str]:
    """Read "model name" from /proc/cpuinfo on Linux."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _linux_base_freq_mhz() -> Optional[float]:
    """Base (non-turbo) clock from sysfs, if the cpufreq driver exposes it."""
    try:
        khz = Path("/sys/devices/system/cpu/cpu0/cpufreq/base_frequency").read_text().strip()
        return float(khz) / 1000.0  # sysfs reports kHz
    except (OSError, ValueError):
        return None


def cpu_model_name() -> Optional[str]:
    """Best human-readable CPU name for this platform."""
    name = _windows_cpu_name() if _shell.is_windows() else _linux_cpu_name()
    return name or platform.processor() or None


def collect(snapshot: SystemSnapshot, interval: float = 1.0) -> CPUInfo:
    """Collect CPU information, sampling usage over ``interval`` seconds."""
    info = CPUInfo()
    try:
        info.model = cpu_model_name()
        info.physical_cores = psutil.cpu_count(logical=False)
        info.logical_cores = psutil.cpu_count(logical=True)
    except Exception as exc:
        snapshot.add_error("cpu", f"core count unavailable: {exc}")

    try:
        freq = psutil.cpu_freq()
        if freq is not None:
            info.current_freq_mhz = rnd(freq.current, 0) or None
            if _shell.is_windows():
                # Windows quirk: psutil's "max" is the rated base clock, not
                # the turbo ceiling, and "current" is often just that value
                # too. Report it as base and leave max unknown.
                info.base_freq_mhz = rnd(freq.max, 0) or None
            else:
                info.max_freq_mhz = rnd(freq.max, 0) or None
                info.base_freq_mhz = _linux_base_freq_mhz()
    except Exception as exc:  # cpu_freq raises on some VMs / ARM boards
        snapshot.add_error("cpu", f"frequency unavailable: {exc}")

    try:
        per_core = psutil.cpu_percent(interval=interval, percpu=True)
        info.per_core_usage_percent = [round(p, 1) for p in per_core]
        if per_core:
            info.total_usage_percent = round(sum(per_core) / len(per_core), 1)
    except Exception as exc:
        snapshot.add_error("cpu", f"usage sampling failed: {exc}")

    try:
        load = psutil.getloadavg()
        # Windows quirk: psutil emulates load average with a background
        # thread that starts on the first call, so a fresh process always
        # sees (0, 0, 0). Treat that as "unknown" rather than "idle".
        if not (_shell.is_windows() and not any(load)):
            info.load_avg_1m, info.load_avg_5m, info.load_avg_15m = (round(x, 2) for x in load)
    except Exception as exc:
        snapshot.add_error("cpu", f"load average unavailable: {exc}")
    return info
