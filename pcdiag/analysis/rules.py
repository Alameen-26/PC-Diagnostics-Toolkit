# pcdiag/analysis/rules.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): OS-aware wording, SMART read_errors, USB system-drive rule, disk_io all-disks handling, needs-Administrator errors as "can't tell".
"""Built-in diagnostic rules.

Every rule here is a *pure function* of a ``SystemSnapshot``: it reads data,
returns zero or more ``Finding`` objects, and never mutates the snapshot or
touches the real system. That makes rules trivially testable with fixtures.

Design principles
-----------------
* ``None`` means "unknown". A rule with no data either returns nothing or a
  low-confidence INFO "can't tell" finding. It only returns an OK finding
  when real measurements show things are fine.
* Free fixes first: each ``fix`` text lists no-cost actions (cleanup,
  trimming startup apps, dusting, re-pasting) before anything you must buy.
* Thresholds are *rules of thumb* from common IT-support practice, not
  vendor specifications. They are named constants below so they are easy to
  tune in later releases (annotate changes with "Changed in vX.Y.Z").
* Benchmark scores are never judged against fixed thresholds (Changed in
  v0.1.0): they are toolkit-relative and vary hugely between machines, so
  they are only useful for comparing runs over time on the same PC. No rule
  reads ``snapshot.benchmark``.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Optional

from ..models import DiskInfo, DiskKind, SmartStatus, SystemSnapshot
from .findings import Category, Confidence, Finding, Severity
from .registry import rule

# --------------------------------------------------------------------------
# Thresholds (rules of thumb)
# --------------------------------------------------------------------------

# RAM in use. Above ~90% Windows starts paging heavily to disk, which is the
# single most common cause of "everything is sluggish". 80-90% is a warning sign.
RAM_USED_WARN_PERCENT = 90.0
RAM_USED_INFO_PERCENT = 80.0
# Page file use above this, combined with high RAM use, confirms real pressure.
SWAP_HEAVY_PERCENT = 50.0
# Windows 10/11 plus a browser needs ~8 GB to be comfortable; below that is
# a hard bottleneck. 16 GB is today's comfortable baseline for multitasking.
RAM_TOTAL_LOW_GB = 8.0
RAM_TOTAL_COMFORT_GB = 16.0

# Disk fullness. The system drive needs headroom for the page file, Windows
# Update and temp files, so it gets stricter limits than data drives.
SYSTEM_DISK_WARN_PERCENT = 90.0
SYSTEM_DISK_CRITICAL_PERCENT = 97.0
SYSTEM_DISK_CRITICAL_FREE_GB = 5.0   # under 5 GB free, updates start failing
DATA_DISK_INFO_PERCENT = 90.0
DATA_DISK_WARN_PERCENT = 95.0

# Disk "100% active" (the classic Task Manager symptom on HDD machines).
DISK_BUSY_WARN_PERCENT = 90.0

# CPU temperatures (Celsius). Most desktop/laptop CPUs throttle around
# 90-100 C; sustained 80 C+ at normal load usually means dust or dried paste.
CPU_TEMP_WARN_C = 80.0
CPU_TEMP_CRITICAL_C = 90.0
GPU_TEMP_WARN_C = 85.0
# If the CPU runs below 75% of its base clock while busy, it is probably
# throttling (thermal or power limits).
THROTTLE_FREQ_RATIO = 0.75
THROTTLE_MIN_USAGE_PERCENT = 50.0
# Labels that identify CPU sensors in psutil/WMI output.
_CPU_SENSOR_RE = re.compile(r"cpu|package|core|tctl|tdie|k10temp|coretemp", re.I)

# Constant CPU usage high enough that the machine feels unresponsive.
CPU_BUSY_WARN_PERCENT = 85.0

# Startup apps: each one adds boot time and background load. 10+ is worth a
# look; 20+ almost always slows boot noticeably.
STARTUP_INFO_COUNT = 10
STARTUP_WARN_COUNT = 20

# Battery health = full-charge capacity / design capacity. Common rule of
# thumb: below 80% the battery is "worn"; below 40% it should be replaced.
BATTERY_WORN_HEALTH_PERCENT = 80.0
BATTERY_REPLACE_HEALTH_PERCENT = 40.0

# SSD life used (SMART wear) at which to plan a replacement.
SSD_WEAR_WARN_PERCENT = 90.0

# Graphics drivers older than this are likely missing performance and
# security fixes. One year = "check for updates", two years = clearly stale.
GPU_DRIVER_INFO_DAYS = 365
GPU_DRIVER_WARN_DAYS = 730

# A single process is "heavy" if it uses this share of total CPU capacity
# (cpu_percent normalised by logical cores) or of RAM.
PROCESS_CPU_SHARE_PERCENT = 15.0
PROCESS_MEM_PERCENT = 20.0

# Uptime after which a restart is a cheap first fix (memory leaks, pending
# updates). One week.
UPTIME_RESTART_HOURS = 168.0


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _disk_label(d: DiskInfo) -> str:
    """Human label for a disk, e.g. 'C:\\ (Samsung 990 PRO)'."""
    name = d.mountpoint or d.device or "unknown disk"
    return f"{name} ({d.model})" if d.model else name


# Changed in v0.1.0 (follow-up): user-facing text names the actual OS instead
# of always saying "Windows", so Linux/macOS reports read correctly.
_OS_DISPLAY = {"windows": "Windows", "linux": "Linux", "darwin": "macOS"}


def _os_name(s: SystemSnapshot) -> str:
    """Display name of the OS ("Windows", "Linux", "macOS").

    Falls back to the neutral "Your operating system" when the OS is unknown,
    so we never claim a platform we didn't detect.
    """
    system = (s.os.system if s.os else None) or ""
    if not system or system.lower() == "unknown":
        return "Your operating system"
    return _OS_DISPLAY.get(system.lower(), system)


def _os_noun(s: SystemSnapshot) -> str:
    """OS name for use mid-sentence ("the operating system" when unknown)."""
    name = _os_name(s)
    return "the operating system" if name == "Your operating system" else name


def _parse_date(text: Optional[str]) -> Optional[date]:
    """Parse an ISO-ish date string; return None if it can't be read."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _battery_health(snapshot: SystemSnapshot) -> Optional[float]:
    """Battery health % (100 = as new), from wear_percent or the capacities."""
    b = snapshot.battery
    if b is None:
        return None
    if b.wear_percent is not None:
        return 100.0 - b.wear_percent
    if b.design_capacity_mwh and b.full_charge_capacity_mwh is not None:
        return 100.0 * b.full_charge_capacity_mwh / b.design_capacity_mwh
    return None


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------

@rule("memory_pressure")
def memory_pressure(s: SystemSnapshot) -> Iterable[Finding]:
    """RAM currently in use is high, so Windows is paging to disk."""
    m = s.memory
    if m is None or m.percent is None:
        return []  # unknown; low-RAM rule / data-coverage rule cover this
    ev = {"ram_used_percent": m.percent, "available_gb": m.available_gb,
          "swap_used_percent": m.swap_percent}
    swap_heavy = m.swap_percent is not None and m.swap_percent >= SWAP_HEAVY_PERCENT
    if m.percent >= RAM_USED_WARN_PERCENT:
        return [Finding(
            id="memory.pressure", title=f"Memory is almost full ({m.percent:.0f}% in use)",
            severity=Severity.WARNING, category=Category.MEMORY,
            # Changed in v0.1.0: OS-neutral wording.
            cause=(f"When RAM is nearly full, {_os_noun(s)} moves data to the much slower disk "
                   "(paging), which makes apps freeze and switch slowly."
                   + (" The page file is also heavily used, confirming this." if swap_heavy else "")),
            fix=("Free: close unused browser tabs and apps, remove startup apps you don't need, "
                 "restart if the PC has been on for days. If it keeps happening, add more RAM."),
            evidence=ev, confidence=Confidence.HIGH if swap_heavy else Confidence.MEDIUM)]
    if m.percent >= RAM_USED_INFO_PERCENT:
        return [Finding(
            id="memory.pressure", title=f"Memory use is fairly high ({m.percent:.0f}%)",
            severity=Severity.INFO, category=Category.MEMORY,
            cause="This is a single sample; it only matters if it stays this high.",
            fix="Keep an eye on it; close apps you are not using.",
            evidence=ev, confidence=Confidence.MEDIUM)]
    return [Finding(
        id="memory.pressure", title=f"Memory use is fine ({m.percent:.0f}% in use)",
        severity=Severity.OK, category=Category.MEMORY,
        cause="Plenty of free RAM at the time of the scan.", fix="",
        evidence=ev, confidence=Confidence.MEDIUM)]


@rule("low_total_ram")
def low_total_ram(s: SystemSnapshot) -> Iterable[Finding]:
    """Installed RAM is too small for a modern Windows desktop."""
    m = s.memory
    if m is None or m.total_gb is None:
        return []
    ev = {"total_gb": m.total_gb, "slots_used": m.slots_used,
          "slots_total": m.slots_total, "type": m.type, "speed_mhz": m.speed_mhz}
    # Mention a free slot when we know there is one: that makes the upgrade cheap.
    free_slot = (m.slots_used is not None and m.slots_total is not None
                 and m.slots_used < m.slots_total)
    slot_note = " There is a free RAM slot, so adding a stick is easy." if free_slot else ""
    if m.total_gb < RAM_TOTAL_LOW_GB:
        return [Finding(
            id="memory.low_total", title=f"Only {m.total_gb:g} GB of RAM installed",
            severity=Severity.WARNING, category=Category.MEMORY,
            # Changed in v0.1.0: OS-neutral wording.
            cause=(f"A modern {_os_noun(s)} desktop with a web browser needs about {RAM_TOTAL_LOW_GB:g} GB to run "
                   "smoothly (rule of thumb). With less, the PC pages to disk constantly." + slot_note),
            fix=("Free: run fewer apps at once and trim startup apps. "
                 f"Lasting fix: upgrade to at least {RAM_TOTAL_COMFORT_GB:g} GB if the machine supports it."),
            evidence=ev, confidence=Confidence.HIGH, free_fix=False)]
    if m.total_gb < RAM_TOTAL_COMFORT_GB and m.percent is not None and m.percent >= RAM_USED_INFO_PERCENT:
        # 8-16 GB is fine for light use; only flag it when it is actually filling up.
        return [Finding(
            id="memory.low_total", title=f"{m.total_gb:g} GB of RAM is getting tight",
            severity=Severity.INFO, category=Category.MEMORY,
            cause="RAM is filling up during normal use." + slot_note,
            fix=f"Consider upgrading to {RAM_TOTAL_COMFORT_GB:g} GB if you multitask heavily.",
            evidence=ev, confidence=Confidence.MEDIUM, free_fix=False)]
    return []


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

@rule("system_drive_type")
def system_drive_type(s: SystemSnapshot) -> Iterable[Finding]:
    """The OS booting from a spinning hard drive is the #1 cause of slow PCs.

    Changed in v0.1.0: titles use the detected OS name ("Linux runs from ...")
    or "Your operating system runs from ..." when the OS is unknown.
    """
    os_name = _os_name(s)
    d = s.system_drive
    if d is None:
        if not s.disks:
            return []  # no disk data at all; covered by data_coverage
        return [Finding(
            id="storage.system_drive_unknown", title=f"Couldn't tell which disk {_os_noun(s)} runs from",
            severity=Severity.INFO, category=Category.STORAGE,
            cause="No disk was marked as the system drive.", fix="",
            evidence={"disks": [x.mountpoint or x.device for x in s.disks]}, confidence=Confidence.LOW)]
    ev = {"drive": _disk_label(d), "kind": d.kind.value}
    if d.kind is DiskKind.HDD:
        return [Finding(
            id="storage.hdd_system_drive", title=f"{os_name} runs from a hard disk drive ({_disk_label(d)})",
            severity=Severity.WARNING, category=Category.STORAGE,
            cause=(f"Hard drives are 5-50x slower than SSDs for the small random reads {_os_noun(s)} does "
                   "constantly, so boot, app launches and updates crawl."),
            fix=("Free: trim startup apps and keep 15%+ free space to soften it. "
                 f"Real fix: clone {_os_noun(s)} onto an SSD (the biggest upgrade for the money)."),
            evidence=ev, confidence=Confidence.HIGH, free_fix=False)]
    if d.kind is DiskKind.UNKNOWN:
        return [Finding(
            id="storage.system_drive_unknown", title="Couldn't tell if the system drive is an SSD or HDD",
            severity=Severity.INFO, category=Category.STORAGE,
            cause="The drive type was not reported.", fix="Check the drive model online.",
            evidence=ev, confidence=Confidence.LOW)]
    return [Finding(
        id="storage.system_drive_type", title=f"{os_name} runs from a fast {d.kind.value.upper()} drive",
        severity=Severity.OK, category=Category.STORAGE, cause="", fix="",
        evidence=ev, confidence=Confidence.HIGH)]


@rule("disk_space")
def disk_space(s: SystemSnapshot) -> Iterable[Finding]:
    """Nearly-full drives; the system drive gets stricter limits."""
    out: list[Finding] = []
    for d in s.disks:
        if d.percent is None:
            continue  # unknown usage: say nothing rather than guess
        ev = {"drive": _disk_label(d), "used_percent": d.percent, "free_gb": d.free_gb,
              "total_gb": d.total_gb, "is_system_drive": d.is_system_drive}
        label = _disk_label(d)
        if d.is_system_drive:
            very_low = d.free_gb is not None and d.free_gb < SYSTEM_DISK_CRITICAL_FREE_GB
            if d.percent >= SYSTEM_DISK_CRITICAL_PERCENT or very_low:
                sev = Severity.CRITICAL
            elif d.percent >= SYSTEM_DISK_WARN_PERCENT:
                sev = Severity.WARNING
            else:
                continue
            out.append(Finding(
                id="storage.system_disk_full", title=f"System drive {label} is {d.percent:.0f}% full",
                severity=sev, category=Category.STORAGE,
                cause=(f"{_os_name(s).replace('Your operating system', 'The operating system')} needs free space "
                       "for the page/swap file, updates and temp files. "
                       "A nearly-full system drive slows everything and can make updates fail."),
                fix=("Free: run Disk Cleanup (include 'Clean up system files'), empty Downloads and the "
                     "Recycle Bin, uninstall unused programs, move photos/videos to another drive or cloud."),
                evidence=ev, confidence=Confidence.HIGH))
        else:
            if d.percent >= DATA_DISK_WARN_PERCENT:
                sev = Severity.WARNING
            elif d.percent >= DATA_DISK_INFO_PERCENT:
                sev = Severity.INFO
            else:
                continue
            out.append(Finding(
                id="storage.data_disk_full", title=f"Drive {label} is {d.percent:.0f}% full",
                severity=sev, category=Category.STORAGE,
                cause="A full data drive won't slow the system much, but you'll run out of room soon.",
                fix="Free: delete or archive files you no longer need.",
                evidence=ev, confidence=Confidence.HIGH))
    return out


@rule("smart_health")
def smart_health(s: SystemSnapshot) -> Iterable[Finding]:
    """Drive self-reported health (S.M.A.R.T.). Failing drives mean data loss risk."""
    out: list[Finding] = []
    for d in s.disks:
        sm = d.smart
        if sm is None:
            continue  # SMART not collected for this disk
        label = _disk_label(d)
        ev = {"drive": label, "status": sm.status.value, "reallocated_sectors": sm.reallocated_sectors,
              "pending_sectors": sm.pending_sectors, "power_on_hours": sm.power_on_hours,
              "wear_percent": sm.wear_percent, "read_errors": sm.read_errors, "source": sm.source}
        # Reallocated/pending sectors > 0 mean the surface is already degrading,
        # even if the drive's overall verdict is still "OK".
        bad_sectors = (sm.reallocated_sectors or 0) > 0 or (sm.pending_sectors or 0) > 0
        # Changed in v0.1.0: uncorrectable read errors (SmartHealth.read_errors,
        # from Get-StorageReliabilityCounter / smartctl) mean data could not be
        # read back at all, so any nonzero count is a warning. None = unknown.
        read_errors = (sm.read_errors or 0) > 0
        worn_ssd = sm.wear_percent is not None and sm.wear_percent >= SSD_WEAR_WARN_PERCENT
        if sm.status is SmartStatus.FAILING:
            out.append(Finding(
                id="storage.smart_failing", title=f"Drive {label} is failing",
                severity=Severity.CRITICAL, category=Category.STORAGE,
                cause="The drive itself reports it is about to fail. Data on it can be lost at any time.",
                fix=("Free: back up everything on this drive NOW (copy to another disk or cloud) "
                     "and avoid heavy use until the backup is done. Real fix: replace the drive."),
                evidence=ev, confidence=Confidence.HIGH, free_fix=False))
        elif sm.status is SmartStatus.WARNING or bad_sectors or worn_ssd or read_errors:
            out.append(Finding(
                id="storage.smart_warning", title=f"Drive {label} shows early signs of wear",
                severity=Severity.WARNING, category=Category.STORAGE,
                cause=("The drive reports degraded health (bad sectors, read errors or high wear). "
                       "Drives in this state often fail within months."),
                fix="Free: make sure everything on it is backed up. Real fix: plan to replace the drive.",
                evidence=ev, confidence=Confidence.HIGH if sm.status is SmartStatus.WARNING else Confidence.MEDIUM,
                free_fix=False))
        elif sm.status is SmartStatus.UNKNOWN:
            out.append(Finding(
                id="storage.smart_unknown", title=f"Couldn't read health data for {label}",
                severity=Severity.INFO, category=Category.STORAGE,
                cause="SMART data was not readable (often needs admin rights or a USB adapter blocks it).",
                fix="Run the toolkit as administrator, or check with CrystalDiskInfo.",
                evidence=ev, confidence=Confidence.LOW))
        else:
            out.append(Finding(
                id="storage.smart_ok", title=f"Drive {label} reports healthy",
                severity=Severity.OK, category=Category.STORAGE, cause="", fix="",
                evidence=ev, confidence=Confidence.HIGH))
    return out


@rule("disk_busy")
def disk_busy(s: SystemSnapshot) -> Iterable[Finding]:
    """Disk pegged near 100% active time: a bottleneck everything waits on.

    Changed in v0.1.0: ``disk_io`` can hold an all-disks entry (``device=None``,
    usually ``disk_io[0]``) plus per-device entries. Per-device entries are
    more specific, so they are used when any of them is busy; the all-disks
    entry is only reported when no single device explains it, which avoids
    reporting the same bottleneck twice. ``busy_percent`` is always None on
    Windows (psutil doesn't expose busy time there): that means unknown, so
    the rule stays silent rather than reporting a fault or claiming health.
    """
    def busy(io) -> bool:
        return io.busy_percent is not None and io.busy_percent >= DISK_BUSY_WARN_PERCENT

    per_device = [io for io in s.disk_io if io.device is not None and busy(io)]
    combined = [io for io in s.disk_io if io.device is None and busy(io)]
    hits = per_device or combined[:1]   # at most one combined finding
    out: list[Finding] = []
    for io in hits:
        which = io.device or "all disks combined"
        out.append(Finding(
            id="storage.disk_busy", title=f"Disk activity is maxed out ({io.busy_percent:.0f}% busy, {which})",
            severity=Severity.WARNING, category=Category.STORAGE,
            cause=("The disk can't keep up, so apps wait for it. Common causes: a hard drive as system disk, "
                   "low RAM forcing paging, a failing drive retrying reads, or indexing/updates."),
            fix="Free: let updates finish, trim startup apps, close heavy apps. See related storage/memory findings.",
            evidence={"device": io.device, "busy_percent": io.busy_percent,
                      "read_mbps": io.read_mbps, "write_mbps": io.write_mbps},
            confidence=Confidence.MEDIUM))  # single sample, so medium
    return out


@rule("usb_system_drive")
def usb_system_drive(s: SystemSnapshot) -> Iterable[Finding]:
    """Added in v0.1.0 (follow-up): OS booted from a USB-attached drive.

    USB enclosures/sticks are slower for random I/O, often lack TRIM/SMART
    pass-through, and can disconnect, so they're a poor home for the OS.
    ``bus_type`` None means unknown -> no finding.
    """
    d = s.system_drive
    if d is None or not d.bus_type or d.bus_type.strip().upper() != "USB":
        return []
    return [Finding(
        id="storage.usb_system_drive", title=f"{_os_name(s)} runs from a USB drive ({_disk_label(d)})",
        severity=Severity.WARNING, category=Category.STORAGE,
        cause=("USB drives are slower for the small random reads an operating system does, often hide "
               "health data, and can disconnect, risking a corrupted system."),
        fix=("Free: plug it into a USB 3 port directly (not a hub). "
             "Real fix: move the system onto an internal SSD."),
        evidence={"drive": _disk_label(d), "bus_type": d.bus_type, "kind": d.kind.value},
        confidence=Confidence.HIGH, free_fix=False)]


# --------------------------------------------------------------------------
# CPU and thermals
# --------------------------------------------------------------------------

@rule("cpu_thermal")
def cpu_thermal(s: SystemSnapshot) -> Iterable[Finding]:
    """High CPU temperature and/or signs of thermal throttling."""
    readings = [t for t in s.temperatures if t.current_c is not None]
    cpu_readings = [t for t in readings if _CPU_SENSOR_RE.search(f"{t.label} {t.sensor or ''}")]
    if not cpu_readings:
        # Never claim temps are fine without data. Data errors are surfaced
        # separately, so only add an INFO when there's no explanation already.
        if any(e.lower().startswith("temperatures") for e in s.errors):
            return []
        return [Finding(
            id="thermal.unknown", title="CPU temperature couldn't be read",
            severity=Severity.INFO, category=Category.THERMAL,
            cause="No CPU temperature sensor was available, so overheating can't be ruled out.",
            fix="Try running as administrator or check with HWiNFO.", confidence=Confidence.LOW)]
    hottest = max(cpu_readings, key=lambda t: t.current_c)
    temp = hottest.current_c
    # Use the sensor's own "high" mark when it is lower than our generic limit.
    warn_at = min(CPU_TEMP_WARN_C, hottest.high_c) if hottest.high_c else CPU_TEMP_WARN_C
    c = s.cpu
    # Throttling heuristic: busy CPU running well under its base clock.
    throttling = bool(
        c and c.current_freq_mhz and c.base_freq_mhz and c.total_usage_percent is not None
        and c.total_usage_percent >= THROTTLE_MIN_USAGE_PERCENT
        and c.current_freq_mhz < THROTTLE_FREQ_RATIO * c.base_freq_mhz)
    ev = {"sensor": hottest.label, "cpu_temp_c": temp, "sensor_high_c": hottest.high_c,
          "current_freq_mhz": c.current_freq_mhz if c else None,
          "base_freq_mhz": c.base_freq_mhz if c else None, "likely_throttling": throttling}
    if temp >= CPU_TEMP_CRITICAL_C or (hottest.critical_c and temp >= hottest.critical_c):
        sev = Severity.CRITICAL
    elif temp >= warn_at or (throttling and temp >= CPU_TEMP_WARN_C - 10):
        # Throttling plus a merely-warm CPU still points at cooling.
        sev = Severity.WARNING
    else:
        return [Finding(
            id="thermal.cpu", title=f"CPU temperature is fine ({temp:.0f}°C)",
            severity=Severity.OK, category=Category.THERMAL, cause="", fix="",
            evidence=ev, confidence=Confidence.MEDIUM)]
    title = f"CPU is running hot ({temp:.0f}°C)" + (" and slowing itself down" if throttling else "")
    return [Finding(
        id="thermal.cpu", title=title, severity=sev, category=Category.THERMAL,
        cause=("When a CPU gets too hot it lowers its speed to protect itself (thermal throttling)."
               + (f" It is running at {c.current_freq_mhz:.0f} MHz, below its {c.base_freq_mhz:.0f} MHz base speed."
                  if throttling else "")),
        fix=("Free: clean dust from vents and fans (compressed air), keep laptops on a hard surface, "
             "check fans spin. Cheap: replace thermal paste (old laptops often need it). "
             "Last resort: a better cooler or laptop cooling pad."),
        evidence=ev, confidence=Confidence.HIGH if throttling else Confidence.MEDIUM)]


@rule("gpu_thermal")
def gpu_thermal(s: SystemSnapshot) -> Iterable[Finding]:
    """GPU running hot (only when the GPU reports a temperature)."""
    out = []
    for g in s.gpus:
        if g.temperature_c is not None and g.temperature_c >= GPU_TEMP_WARN_C:
            out.append(Finding(
                id="thermal.gpu", title=f"Graphics card is hot ({g.temperature_c:.0f}°C)",
                severity=Severity.WARNING, category=Category.THERMAL,
                cause="A hot GPU lowers its clocks, causing stutter in games and video.",
                fix="Free: clean dust from the card and case, improve airflow. Cheap: re-paste or re-pad.",
                evidence={"gpu": g.name, "temperature_c": g.temperature_c}, confidence=Confidence.MEDIUM))
    return out


@rule("cpu_busy")
def cpu_busy(s: SystemSnapshot) -> Iterable[Finding]:
    """CPU near 100% at the moment of the scan."""
    c = s.cpu
    if c is None or c.total_usage_percent is None or c.total_usage_percent < CPU_BUSY_WARN_PERCENT:
        return []
    return [Finding(
        id="cpu.busy", title=f"CPU is very busy ({c.total_usage_percent:.0f}% in use)",
        severity=Severity.WARNING, category=Category.CPU,
        cause="Something is keeping the processor fully occupied, so everything else waits.",
        fix=("Free: check the 'heavy processes' finding, let Windows Update/antivirus scans finish, "
             "remove unwanted startup apps and trial software."),
        evidence={"cpu_usage_percent": c.total_usage_percent, "load_avg_1m": c.load_avg_1m,
                  "logical_cores": c.logical_cores},
        confidence=Confidence.MEDIUM)]  # one sample; could be a momentary spike


# --------------------------------------------------------------------------
# Software: startup, processes, drivers, uptime
# --------------------------------------------------------------------------

@rule("startup_items")
def startup_items(s: SystemSnapshot) -> Iterable[Finding]:
    """Too many programs launching at sign-in."""
    # enabled=None (unknown) is counted: most collectors only list active entries.
    active = [i for i in s.startup_items if i.enabled is not False]
    n = len(active)
    if n >= STARTUP_WARN_COUNT:
        sev = Severity.WARNING
    elif n >= STARTUP_INFO_COUNT:
        sev = Severity.INFO
    else:
        return []
    return [Finding(
        id="startup.too_many", title=f"{n} programs start automatically",
        severity=sev, category=Category.STARTUP,
        cause="Every startup program slows boot and keeps using memory and CPU in the background.",
        fix=("Free: open Task Manager > Startup apps and disable anything you don't need right "
             "at sign-in (updaters, trial software, chat apps you rarely use)."),
        evidence={"count": n, "items": [i.name for i in active][:30]},
        confidence=Confidence.HIGH)]


@rule("heavy_processes")
def heavy_processes(s: SystemSnapshot) -> Iterable[Finding]:
    """Individual processes using a big share of CPU or RAM."""
    cores = (s.cpu.logical_cores if s.cpu and s.cpu.logical_cores else None)
    heavy = []
    for p in s.top_processes:
        # psutil cpu_percent is per-core (can exceed 100), so normalise by cores
        # to get the share of the whole CPU. Unknown core count -> assume 1 core.
        cpu_share = p.cpu_percent / cores if (p.cpu_percent is not None and cores) else p.cpu_percent
        cpu_hit = cpu_share is not None and cpu_share >= PROCESS_CPU_SHARE_PERCENT
        mem_hit = p.memory_percent is not None and p.memory_percent >= PROCESS_MEM_PERCENT
        if cpu_hit or mem_hit:
            heavy.append({"name": p.name, "pid": p.pid, "cpu_share_percent": cpu_share,
                          "memory_mb": p.memory_mb, "memory_percent": p.memory_percent})
    if not heavy:
        return []
    names = ", ".join(h["name"] for h in heavy)
    return [Finding(
        id="processes.heavy", title=f"Heavy programs running: {names}",
        severity=Severity.INFO, category=Category.PROCESSES,
        cause="These programs were using a large share of CPU or memory during the scan.",
        fix=("Free: close them if you don't need them (e.g. fewer browser tabs). If it's an antivirus or "
             "updater, let it finish. Unknown names are worth looking up."),
        evidence={"processes": heavy}, confidence=Confidence.MEDIUM)]


@rule("gpu_driver_age")
def gpu_driver_age(s: SystemSnapshot) -> Iterable[Finding]:
    """Graphics driver age, when the driver date is known."""
    out = []
    ref = s.timestamp.date() if s.timestamp else date.today()
    for g in s.gpus:
        d = _parse_date(g.driver_date)
        if d is None:
            continue  # no date -> can't judge; don't guess from version strings
        age_days = (ref - d).days
        if age_days >= GPU_DRIVER_WARN_DAYS:
            sev = Severity.WARNING
        elif age_days >= GPU_DRIVER_INFO_DAYS:
            sev = Severity.INFO
        else:
            continue
        years = age_days / 365.25
        out.append(Finding(
            id="gpu.driver_outdated", title=f"Graphics driver for {g.name or 'GPU'} is {years:.1f} years old",
            severity=sev, category=Category.GPU,
            cause="Old graphics drivers can cause glitches, crashes and slower video/games.",
            fix=("Free: update via Windows Update (optional updates) or the maker's site "
                 "(Intel, AMD, NVIDIA, or the laptop manufacturer for older laptops)."),
            evidence={"gpu": g.name, "driver_version": g.driver_version,
                      "driver_date": g.driver_date, "age_days": age_days},
            confidence=Confidence.HIGH))
    return out


@rule("uptime")
def uptime(s: SystemSnapshot) -> Iterable[Finding]:
    """Long uptime: a restart is the cheapest fix there is."""
    if s.os is None or s.os.uptime_hours is None or s.os.uptime_hours < UPTIME_RESTART_HOURS:
        return []
    days = s.os.uptime_hours / 24
    return [Finding(
        id="system.long_uptime", title=f"PC hasn't restarted in {days:.0f} days",
        severity=Severity.INFO, category=Category.SYSTEM,
        cause="Long uptimes let memory leaks build up and leave updates half-installed. "
              "(Windows 'Fast startup' means shutting down doesn't count as a restart.)",
        fix="Free: use Restart (not Shut down) at least once a week.",
        evidence={"uptime_hours": s.os.uptime_hours}, confidence=Confidence.HIGH)]


# --------------------------------------------------------------------------
# Battery
# --------------------------------------------------------------------------

@rule("battery_wear")
def battery_wear(s: SystemSnapshot) -> Iterable[Finding]:
    """Battery capacity lost over time (thresholds are rules of thumb)."""
    health = _battery_health(s)
    if health is None:
        return []  # desktop, or capacity unknown
    b = s.battery
    ev = {"health_percent": round(health, 1), "wear_percent": round(100 - health, 1),
          "design_capacity_mwh": b.design_capacity_mwh,
          "full_charge_capacity_mwh": b.full_charge_capacity_mwh, "cycle_count": b.cycle_count}
    if health < BATTERY_REPLACE_HEALTH_PERCENT:
        return [Finding(
            id="battery.replace", title=f"Battery should be replaced ({health:.0f}% of original capacity)",
            severity=Severity.WARNING, category=Category.BATTERY,
            cause=(f"Below {BATTERY_REPLACE_HEALTH_PERCENT:.0f}% health (rule of thumb) runtime is very short "
                   "and the laptop may shut off suddenly or throttle on battery."),
            fix="Replace the battery. Check for swelling (a lifted touchpad or case gap): stop using it if swollen.",
            evidence=ev, confidence=Confidence.HIGH, free_fix=False)]
    if health < BATTERY_WORN_HEALTH_PERCENT:
        return [Finding(
            id="battery.worn", title=f"Battery is worn ({health:.0f}% of original capacity)",
            severity=Severity.WARNING, category=Category.BATTERY,
            cause=(f"Below {BATTERY_WORN_HEALTH_PERCENT:.0f}% health (rule of thumb) the battery is "
                   "considered worn: expect noticeably shorter runtime."),
            fix=("Free: use Windows battery saver and lower screen brightness; if the laptop has a "
                 "charge-limit option (e.g. 80%), turn it on to slow further wear. Replace when runtime is too short."),
            evidence=ev, confidence=Confidence.HIGH)]
    return [Finding(
        id="battery.ok", title=f"Battery health is good ({health:.0f}%)",
        severity=Severity.OK, category=Category.BATTERY, cause="", fix="",
        evidence=ev, confidence=Confidence.HIGH)]


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------

# Collector error text that means "needs elevated rights". Such gaps are
# shown as grey "can't tell" items, never as faults.
_ADMIN_RE = re.compile(r"administrator", re.I)


def needs_admin(error: str) -> bool:
    """True if a collector error says the reading needs Administrator rights."""
    return bool(_ADMIN_RE.search(error or ""))


def admin_blocked_collectors(s: SystemSnapshot) -> set[str]:
    """Collector names ("temperatures", "disks", ...) whose errors mention Administrator."""
    return {e.partition(":")[0].strip().lower() for e in s.errors if needs_admin(e)}


@rule("collector_errors")
def collector_errors(s: SystemSnapshot) -> Iterable[Finding]:
    """Surface every collector error so gaps in the report are explained.

    Changed in v0.1.0: errors mentioning "Administrator" become
    ``data.needs_admin.N`` findings titled "Can't tell: ..." with LOW
    confidence. They are INFO (grey), never warnings: missing rights is not a
    hardware fault.
    """
    out = []
    for i, err in enumerate(s.errors):
        collector, _, reason = err.partition(":")
        collector = collector.strip() or "unknown"
        if needs_admin(err):
            out.append(Finding(
                id=f"data.needs_admin.{i}", title=f"Can't tell: {collector} (needs Administrator)",
                severity=Severity.INFO, category=Category.DATA,
                cause=(reason.strip() or err),
                fix="Run the scan from an Administrator terminal to include these readings.",
                evidence={"error": err, "collector": collector}, confidence=Confidence.LOW))
            continue
        # A deliberately skipped check needs no action; a real failure might
        # be fixed by elevated rights (WMI/SMART often need admin on Windows).
        skipped = "skipped" in reason.lower()
        out.append(Finding(
            id=f"data.collector_error.{i}", title=f"Couldn't fully check: {collector}",
            severity=Severity.INFO, category=Category.DATA,
            cause=(reason.strip() or err),
            fix=("Nothing to do: this check was skipped." if skipped
                 else "Running as administrator may allow more checks."),
            evidence={"error": err, "collector": collector}, confidence=Confidence.HIGH))
    return out


# Sections that the verdict depends on; if any is missing we say so instead
# of implying the machine is healthy.
CORE_SECTIONS = ("os", "cpu", "memory", "disks")


def missing_core_sections(s: SystemSnapshot) -> list[str]:
    """Names of core snapshot sections that are absent/empty."""
    return [name for name in CORE_SECTIONS if not getattr(s, name)]


@rule("data_coverage")
def data_coverage(s: SystemSnapshot) -> Iterable[Finding]:
    """Warn (as INFO) when core sections are missing, so 'no problems' isn't misread as 'healthy'."""
    missing = missing_core_sections(s)
    if not missing:
        return []
    return [Finding(
        id="data.incomplete", title="Some key information couldn't be collected",
        severity=Severity.INFO, category=Category.DATA,
        cause=f"Missing: {', '.join(missing)}. Problems in these areas can't be ruled out.",
        fix="Re-run the scan, ideally as administrator.",
        evidence={"missing": missing}, confidence=Confidence.HIGH)]
