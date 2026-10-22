"""Operating-system collector: name, version, build, hostname and uptime.

Named ``os_info`` rather than ``os`` so it can't be confused with the
standard-library ``os`` module.

Added in v0.1.0
"""
from __future__ import annotations

import platform
import time
from datetime import datetime, timezone

import psutil

from ..models import OSInfo, SystemSnapshot
from . import _shell


def _windows_release(release: str, version: str) -> str:
    """Return "11" for Windows 11 builds that report themselves as "10".

    Windows quirk: Windows 11 still says major version 10 in most APIs, and
    older Pythons' ``platform.release()`` returns "10" on it. Microsoft's own
    rule is that build 22000 and later is Windows 11.
    """
    try:
        build = int(version.split(".")[2])
    except (IndexError, ValueError):
        return release
    if release == "10" and build >= 22000:
        return "11"
    return release


def collect(snapshot: SystemSnapshot) -> OSInfo:
    """Collect OS details. Never raises; problems go to ``snapshot.errors``."""
    info = OSInfo(system=platform.system() or "unknown")
    try:
        info.release = platform.release() or None
        info.version = platform.version() or None
        info.architecture = platform.machine() or None
        info.hostname = platform.node() or None
        if _shell.is_windows() and info.release and info.version:
            info.release = _windows_release(info.release, info.version)
    except Exception as exc:  # platform calls are cheap but can fail in sandboxes
        snapshot.add_error("os", f"platform details unavailable: {exc}")
    try:
        boot = psutil.boot_time()  # seconds since the epoch
        info.boot_time = datetime.fromtimestamp(boot, tz=timezone.utc)
        info.uptime_hours = round((time.time() - boot) / 3600, 1)
    except Exception as exc:
        snapshot.add_error("os", f"boot time unavailable: {exc}")
    return info
