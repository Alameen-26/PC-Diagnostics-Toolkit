"""Disk collector: partitions, free space, drive type and drive health.

Sources, in order of preference:

1. psutil for every mounted volume (works everywhere, no admin).
2. Windows: ``Get-PhysicalDisk`` for MediaType/BusType/HealthStatus and
   ``Get-StorageReliabilityCounter`` for wear, temperature, power-on hours
   and read errors. HealthStatus works without admin; the reliability
   counters usually need an elevated PowerShell, and come back empty
   otherwise.
3. ``smartctl`` (smartmontools), when installed, for raw SMART attributes
   such as reallocated and pending sectors. Also typically needs admin/root.
4. Linux: sysfs (``/sys/block/*/queue/rotational``) for SSD vs HDD.

Added in v0.1.0
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import psutil

from ..models import DiskInfo, DiskKind, SmartHealth, SmartStatus, SystemSnapshot
from . import _shell
from ._util import bytes_to_gb, rnd, to_float, to_int

# Get-PhysicalDisk returns enum *names* when cast with [string] in the script
# below, but older builds / remoting can still yield the numeric values, so
# both spellings are mapped. Numbers are from the MSFT_PhysicalDisk docs.
_MEDIA_TYPES = {"hdd": DiskKind.HDD, "3": DiskKind.HDD, "ssd": DiskKind.SSD, "4": DiskKind.SSD,
                "scm": DiskKind.SSD, "5": DiskKind.SSD}
_BUS_TYPES = {"17": "NVMe", "11": "SATA", "7": "USB", "8": "RAID", "10": "SAS", "3": "ATA",
              "1": "SCSI", "15": "File Backed Virtual", "18": "SCM"}
_HEALTH = {"healthy": SmartStatus.OK, "0": SmartStatus.OK,
           "warning": SmartStatus.WARNING, "1": SmartStatus.WARNING,
           "unhealthy": SmartStatus.FAILING, "2": SmartStatus.FAILING}

#: Worst-wins ordering used when two sources disagree about health.
_SEVERITY = {SmartStatus.UNKNOWN: 0, SmartStatus.OK: 1, SmartStatus.WARNING: 2, SmartStatus.FAILING: 3}

# Linux pseudo/readonly filesystems that aren't real user storage.
_SKIP_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "iso9660", "udf", "ramfs"}

# One PowerShell call gathers physical disks, their reliability counters and
# the drive-letter -> disk-number map. Reliability counters are wrapped in
# try/catch because without admin the cmdlet errors per disk.
_PS_DISKS = r"""
$disks = @(Get-PhysicalDisk | ForEach-Object {
  $r = $null
  try { $r = $_ | Get-StorageReliabilityCounter -ErrorAction Stop } catch {}
  [pscustomobject]@{
    DeviceId = [string]$_.DeviceId; FriendlyName = $_.FriendlyName; Model = $_.Model
    MediaType = [string]$_.MediaType; BusType = [string]$_.BusType
    HealthStatus = [string]$_.HealthStatus; Size = $_.Size
    Wear = $r.Wear; Temperature = $r.Temperature; PowerOnHours = $r.PowerOnHours
    ReadErrorsTotal = $r.ReadErrorsTotal; ReadErrorsUncorrected = $r.ReadErrorsUncorrected
  }
})
$parts = @(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object {
  [pscustomobject]@{ DriveLetter = [string]$_.DriveLetter; DiskNumber = $_.DiskNumber }
})
ConvertTo-Json -Depth 4 -Compress -InputObject @{ disks = $disks; partitions = $parts }
"""


# ---------------------------------------------------------------------------
# Windows parsing (pure functions, tested with canned JSON)
# ---------------------------------------------------------------------------

def parse_physical_disk(raw: dict) -> tuple[DiskKind, Optional[str], Optional[str], SmartHealth]:
    """Map one Get-PhysicalDisk record to (kind, bus_type, model, health)."""
    media = str(raw.get("MediaType") or "").strip().lower()
    bus_raw = str(raw.get("BusType") or "").strip()
    bus = _BUS_TYPES.get(bus_raw, bus_raw or None)
    kind = _MEDIA_TYPES.get(media, DiskKind.UNKNOWN)
    if bus and bus.lower() == "nvme":
        kind = DiskKind.NVME  # NVMe drives are SSDs, but the faster class is what matters
    model = (raw.get("FriendlyName") or raw.get("Model") or "").strip() or None

    temp = to_float(raw.get("Temperature"))
    health = SmartHealth(
        status=_HEALTH.get(str(raw.get("HealthStatus") or "").strip().lower(), SmartStatus.UNKNOWN),
        # Wear is "percentage of rated life used" (0 = new). HDDs report 0
        # or nothing; only trust it for solid-state media.
        wear_percent=to_float(raw.get("Wear")) if kind in (DiskKind.SSD, DiskKind.NVME) else None,
        # Many drives report 0 when they have no sensor; 0 C is not real.
        temperature_c=temp if temp else None,
        power_on_hours=to_int(raw.get("PowerOnHours")),
        read_errors=to_int(raw.get("ReadErrorsUncorrected")),
        source="Get-PhysicalDisk",
    )
    return kind, bus, model, health


def parse_windows_disks(data: Any) -> dict[str, tuple[DiskKind, Optional[str], Optional[str], SmartHealth]]:
    """Return {drive letter "C": parsed physical-disk tuple} from the PS JSON."""
    if not isinstance(data, dict):
        return {}
    by_id = {}
    for raw in _shell.as_list(data.get("disks")):
        if isinstance(raw, dict) and raw.get("DeviceId") is not None:
            by_id[str(raw["DeviceId"])] = parse_physical_disk(raw)
    result = {}
    for part in _shell.as_list(data.get("partitions")):
        if not isinstance(part, dict):
            continue
        letter = str(part.get("DriveLetter") or "").strip().upper()[:1]
        number = part.get("DiskNumber")
        if letter and number is not None and str(number) in by_id:
            result[letter] = by_id[str(number)]
    return result


# ---------------------------------------------------------------------------
# smartctl parsing
# ---------------------------------------------------------------------------

def parse_smartctl(data: Any) -> Optional[tuple[Optional[str], Optional[str], SmartHealth]]:
    """Parse ``smartctl -a -j`` output into (model, protocol, SmartHealth)."""
    if not isinstance(data, dict):
        return None
    health = SmartHealth(source="smartctl")
    passed = (data.get("smart_status") or {}).get("passed")
    if passed is True:
        health.status = SmartStatus.OK
    elif passed is False:
        health.status = SmartStatus.FAILING

    # ATA attributes by ID: 5 = Reallocated_Sector_Ct, 197 = Current_Pending_Sector.
    # The "raw" value is the actual count; "value" is a vendor-normalised score.
    for attr in ((data.get("ata_smart_attributes") or {}).get("table") or []):
        raw_val = to_int((attr.get("raw") or {}).get("value"))
        if attr.get("id") == 5:
            health.reallocated_sectors = raw_val
        elif attr.get("id") == 197:
            health.pending_sectors = raw_val
        elif attr.get("id") == 187:  # Reported_Uncorrect
            health.read_errors = raw_val

    nvme = data.get("nvme_smart_health_information_log") or {}
    if nvme:
        health.wear_percent = to_float(nvme.get("percentage_used"))
        health.read_errors = to_int(nvme.get("media_errors"))
        if (nvme.get("critical_warning") or 0) and health.status is SmartStatus.OK:
            health.status = SmartStatus.WARNING

    health.power_on_hours = to_int((data.get("power_on_time") or {}).get("hours"))
    health.temperature_c = to_float((data.get("temperature") or {}).get("current"))
    # A drive that is "passing" but has reallocated or pending sectors is
    # wearing out; flag it so it isn't reported as fully healthy.
    if health.status is SmartStatus.OK and ((health.reallocated_sectors or 0) > 0 or (health.pending_sectors or 0) > 0):
        health.status = SmartStatus.WARNING
    protocol = (data.get("device") or {}).get("protocol")
    return data.get("model_name"), protocol, health


def merge_health(base: Optional[SmartHealth], extra: SmartHealth) -> SmartHealth:
    """Combine two health readings: fill gaps, and the worse status wins."""
    if base is None:
        return extra
    for name in ("reallocated_sectors", "pending_sectors", "power_on_hours", "wear_percent",
                 "temperature_c", "read_errors"):
        if getattr(base, name) is None and getattr(extra, name) is not None:
            setattr(base, name, getattr(extra, name))
    if _SEVERITY[extra.status] > _SEVERITY[base.status]:
        base.status = extra.status
    base.source = f"{base.source}+{extra.source}" if base.source and extra.source else (base.source or extra.source)
    return base


def _smartctl_devices(errors: list[str]) -> list[tuple[str, Optional[str], SmartHealth, str]]:
    """Run smartctl on every device it can see: [(device, model, health, protocol)]."""
    exe = _shell.which("smartctl")
    if exe is None:
        return []
    scan = _shell.run_command([exe, "--scan", "-j"], on_error=errors.append)
    try:
        devices = json.loads(scan).get("devices", []) if scan else []
    except ValueError:
        return []
    results = []
    for dev in devices:
        name, dtype = dev.get("name"), dev.get("type")
        if not name:
            continue
        args = [exe, "-a", "-j", name] + (["-d", dtype] if dtype else [])
        # smartctl's exit status is a bitmask: bits 0-1 mean the command
        # itself failed, higher bits report disk problems while still
        # producing valid JSON, so accept any code and inspect the output.
        out = _shell.run_command(args, on_error=errors.append, accept_returncodes=range(256))
        try:
            parsed = parse_smartctl(json.loads(out)) if out else None
        except ValueError:
            parsed = None
        if parsed:
            model, protocol, health = parsed
            results.append((name, model, health, protocol or ""))
    return results


# ---------------------------------------------------------------------------
# Linux helpers
# ---------------------------------------------------------------------------

def _linux_parent_block(device: str) -> Optional[str]:
    """Return the whole-disk name for a partition, e.g. /dev/sda1 -> "sda"."""
    name = os.path.basename(device)
    sys_path = Path("/sys/class/block") / name
    if not sys_path.exists():
        return None
    # Partitions have a "partition" file and live inside their parent's dir.
    if (sys_path / "partition").exists():
        return sys_path.resolve().parent.name
    return name


def _linux_disk_kind(device: str) -> tuple[DiskKind, Optional[str]]:
    """SSD/HDD/NVMe and model for a Linux block device via sysfs."""
    parent = _linux_parent_block(device)
    if not parent:
        return DiskKind.UNKNOWN, None
    if parent.startswith("nvme"):
        kind = DiskKind.NVME
    else:
        try:
            rotational = Path(f"/sys/block/{parent}/queue/rotational").read_text().strip()
            kind = DiskKind.HDD if rotational == "1" else DiskKind.SSD
        except OSError:
            kind = DiskKind.UNKNOWN
    try:
        model = Path(f"/sys/block/{parent}/device/model").read_text().strip() or None
    except OSError:
        model = None
    return kind, model


def _system_mountpoint() -> str:
    """Mountpoint of the OS drive: "C:\\" on Windows, "/" elsewhere."""
    if _shell.is_windows():
        drive = os.environ.get("SystemDrive", "C:")  # e.g. "C:"
        return drive.rstrip("\\").upper() + "\\"
    return "/"


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def _list_partitions(snapshot: SystemSnapshot) -> list:
    """Real, mounted partitions with duplicate bind mounts removed."""
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception as exc:
        snapshot.add_error("disks", f"partition list unavailable: {exc}")
        return []
    system_mp = _system_mountpoint()
    chosen: dict[str, Any] = {}
    for p in parts:
        # Windows: empty fstype = empty card reader / no disc in drive;
        # "cdrom" in opts = optical drive. Neither is real storage.
        if not p.fstype or "cdrom" in (p.opts or "") or p.fstype.lower() in _SKIP_FSTYPES:
            continue
        if p.mountpoint.startswith(("/snap/", "/boot/efi")):
            continue
        # Linux containers bind-mount the same device in many places
        # (/etc/hosts etc.). Keep one mount per device, preferring "/".
        key = p.device if not _shell.is_windows() else p.mountpoint
        current = chosen.get(key)
        if current is None or p.mountpoint == system_mp or (
            current.mountpoint != system_mp and len(p.mountpoint) < len(current.mountpoint)
        ):
            chosen[key] = p
    return list(chosen.values())


def collect(snapshot: SystemSnapshot, use_smartctl: bool = True) -> list[DiskInfo]:
    """Collect every mounted volume with type and health where possible."""
    disks: list[DiskInfo] = []
    system_mp = _system_mountpoint().lower()
    for p in _list_partitions(snapshot):
        disk = DiskInfo(device=p.device, mountpoint=p.mountpoint, fstype=p.fstype or None)
        try:
            usage = psutil.disk_usage(p.mountpoint)
            disk.total_gb = bytes_to_gb(usage.total)
            disk.used_gb = bytes_to_gb(usage.used)
            disk.free_gb = bytes_to_gb(usage.free)
            disk.percent = rnd(usage.percent)
        except Exception as exc:  # e.g. BitLocker-locked or disconnected drive
            snapshot.add_error("disks", f"usage for {p.mountpoint} unavailable: {exc}")
        disk.is_system_drive = p.mountpoint.lower() == system_mp
        if not _shell.is_windows():
            disk.kind, disk.model = _linux_disk_kind(p.device)
        disks.append(disk)

    errors: list[str] = []
    if _shell.is_windows():
        physical = parse_windows_disks(_shell.run_powershell_json(_PS_DISKS, on_error=errors.append))
        if not physical and disks:
            snapshot.add_error("disks", "drive type/health unavailable (Get-PhysicalDisk): " + ("; ".join(errors) or "no data"))
        for disk in disks:
            match = physical.get(disk.mountpoint[:1].upper())
            if match:
                disk.kind, disk.bus_type, disk.model, health = match
                disk.smart = health
        # Note once (not per disk) when the admin-only counters were empty.
        if physical and all(
            h.power_on_hours is None and h.temperature_c is None and h.wear_percent is None
            for _, _, _, h in physical.values()
        ):
            snapshot.add_error("disks", "reliability counters (wear, temperature, power-on hours) need an "
                                        "Administrator PowerShell; showing Windows health status only")
        errors.clear()

    if use_smartctl:
        for dev_name, model, health, protocol in _smartctl_devices(errors):
            for disk in disks:
                same = (disk.model and model and disk.model.strip().lower() == model.strip().lower()) or (
                    not _shell.is_windows() and disk.device.startswith(dev_name))
                if same:
                    disk.smart = merge_health(disk.smart, health)
                    if disk.bus_type is None and protocol:
                        disk.bus_type = protocol
                    if disk.model is None:
                        disk.model = model
        if errors:
            snapshot.add_error("disks", "smartctl: " + "; ".join(dict.fromkeys(errors)))
    return disks
