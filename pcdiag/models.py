"""Data models for PC Diagnostics Toolkit.

These dataclasses are the stable contract between collectors (which fill
them in) and the analysis / recommendation / report layers (which read them).

Conventions
-----------
* Units are in the field name: ``_gb``, ``_mb``, ``_mhz``, ``_c`` (Celsius),
  ``_percent`` (0-100), ``_mbps`` (megabytes per second), ``_mwh``.
* Anything a collector may not be able to read is ``Optional`` and defaults
  to ``None``. ``None`` means "unknown", never "zero".
* A snapshot can be partial. Collectors that fail record a message in
  ``SystemSnapshot.errors`` instead of raising.
* ``to_dict()`` produces JSON-safe data; ``from_dict()`` rebuilds the object.
  Unknown keys are ignored, missing keys take the field default.
* Optional fields may be added in later versions; existing fields are never
  renamed or removed within a schema version (``SCHEMA_VERSION``).

Added in v0.1.0
"""
from __future__ import annotations

import dataclasses
import json
import sys
import types
import typing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TypeVar, Union

SCHEMA_VERSION = 1

T = TypeVar("T", bound="Model")


class DiskKind(str, Enum):
    """Physical storage technology. NVMe is kept separate from SATA SSD because it is several times faster."""
    SSD = "ssd"
    HDD = "hdd"
    NVME = "nvme"
    UNKNOWN = "unknown"


class SmartStatus(str, Enum):
    """Overall drive health, normalised across Get-PhysicalDisk, WMI and smartctl."""
    OK = "ok"            # drive reports healthy
    WARNING = "warning"  # degraded / pre-fail attributes
    FAILING = "failing"  # drive predicts failure
    UNKNOWN = "unknown"  # SMART not readable


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------

def _to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, enums and datetimes to JSON-safe values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return value


def _from_jsonable(tp: Any, value: Any) -> Any:
    """Rebuild a value of type ``tp`` from JSON data, following Optional/list/dict/Enum/dataclass hints."""
    if value is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    # Optional[X] is Union[X, None]; "X | None" (PEP 604) is types.UnionType.
    if origin is Union or (sys.version_info >= (3, 10) and origin is types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        return _from_jsonable(non_none[0], value) if len(non_none) == 1 else value
    if origin in (list, tuple):
        inner = args[0] if args else Any
        return [_from_jsonable(inner, v) for v in value]
    if origin is dict:
        inner = args[1] if len(args) == 2 else Any
        return {k: _from_jsonable(inner, v) for k, v in value.items()}
    if isinstance(tp, type):
        if issubclass(tp, Enum):
            # Unrecognised enum strings (e.g. from a newer version) degrade
            # to "unknown" instead of failing the whole load.
            try:
                return tp(value)
            except ValueError:
                return tp("unknown") if "unknown" in tp._value2member_map_ else value
        if issubclass(tp, datetime):
            return value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if dataclasses.is_dataclass(tp):
            return tp.from_dict(value)
        # JSON doesn't distinguish 3 from 3.0; keep float fields as floats so
        # round-tripped snapshots compare equal.
        if tp is float and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
    return value


class Model:
    """Mixin giving every dataclass dict/JSON round-tripping.

    ``from_dict`` relies on the type hints, so field annotations must use
    real types (Optional[X], list[X], nested Model subclasses, Enums).
    """

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict (enums as strings, datetimes as ISO 8601)."""
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        """Build an instance from ``to_dict()`` output. Unknown keys are ignored; missing keys use defaults."""
        hints = typing.get_type_hints(cls)
        kwargs = {}
        for f in dataclasses.fields(cls):  # type: ignore[arg-type]
            if f.name in data:
                kwargs[f.name] = _from_jsonable(hints[f.name], data[f.name])
        return cls(**kwargs)

    def to_json(self, indent: Optional[int] = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls: type[T], text: str) -> T:
        """Parse a JSON string produced by ``to_json()``."""
        return cls.from_dict(json.loads(text))


# --------------------------------------------------------------------------
# Component models
# --------------------------------------------------------------------------

@dataclass
class OSInfo(Model):
    """Operating system identity and uptime."""
    system: str = "unknown"                # "Windows", "Linux", "Darwin"
    release: Optional[str] = None          # "10", "11", "6.8.0-45-generic"
    version: Optional[str] = None          # build string, e.g. "10.0.22631"
    architecture: Optional[str] = None     # "AMD64", "x86_64"
    hostname: Optional[str] = None
    boot_time: Optional[datetime] = None
    uptime_hours: Optional[float] = None


@dataclass
class CPUInfo(Model):
    """Processor identity, clock speeds and sampled load. Usage percents are 0-100."""
    model: Optional[str] = None
    physical_cores: Optional[int] = None
    logical_cores: Optional[int] = None
    base_freq_mhz: Optional[float] = None
    current_freq_mhz: Optional[float] = None
    max_freq_mhz: Optional[float] = None
    total_usage_percent: Optional[float] = None
    per_core_usage_percent: list[float] = field(default_factory=list)
    load_avg_1m: Optional[float] = None    # psutil.getloadavg (emulated on Windows)
    load_avg_5m: Optional[float] = None
    load_avg_15m: Optional[float] = None


@dataclass
class MemoryModule(Model):
    """One physical RAM stick (Windows: Win32_PhysicalMemory)."""
    slot: Optional[str] = None             # "DIMM1", "ChannelA-DIMM0"
    capacity_gb: Optional[float] = None
    speed_mhz: Optional[int] = None
    type: Optional[str] = None             # "DDR4", "DDR5"
    manufacturer: Optional[str] = None


@dataclass
class MemoryInfo(Model):
    """RAM and swap usage, plus optional per-stick details (mainly from Windows CIM)."""
    total_gb: Optional[float] = None
    available_gb: Optional[float] = None
    used_gb: Optional[float] = None
    percent: Optional[float] = None
    swap_total_gb: Optional[float] = None
    swap_used_gb: Optional[float] = None
    swap_percent: Optional[float] = None
    # Optional module details (often only available on Windows)
    slots_used: Optional[int] = None
    slots_total: Optional[int] = None
    speed_mhz: Optional[int] = None
    type: Optional[str] = None             # "DDR4", "DDR5", ...
    modules: list[MemoryModule] = field(default_factory=list)


@dataclass
class SmartHealth(Model):
    """Drive health from SMART / Windows storage reliability counters. None = not reported."""
    status: SmartStatus = SmartStatus.UNKNOWN
    reallocated_sectors: Optional[int] = None
    pending_sectors: Optional[int] = None
    power_on_hours: Optional[int] = None
    wear_percent: Optional[float] = None   # SSD life used, 0 = new, 100 = worn out
    temperature_c: Optional[float] = None
    source: Optional[str] = None           # "smartctl", "wmi", "Get-PhysicalDisk"
    # Added in v0.1.0 (collectors step): uncorrectable read errors reported by
    # Get-StorageReliabilityCounter (ReadErrorsUncorrected) or smartctl.
    read_errors: Optional[int] = None


@dataclass
class DiskInfo(Model):
    """One mounted volume (drive letter or mountpoint) with usage, drive type and health."""
    device: str = ""                       # "C:\\", "/dev/sda1"
    mountpoint: str = ""
    fstype: Optional[str] = None
    total_gb: Optional[float] = None
    used_gb: Optional[float] = None
    free_gb: Optional[float] = None
    percent: Optional[float] = None
    kind: DiskKind = DiskKind.UNKNOWN
    model: Optional[str] = None
    is_system_drive: bool = False
    smart: Optional[SmartHealth] = None
    # Added in v0.1.0 (collectors step): "NVMe", "SATA", "USB", ... from
    # Get-PhysicalDisk BusType or smartctl. Lets analysis spot USB boot drives.
    bus_type: Optional[str] = None


@dataclass
class DiskIOStats(Model):
    """Disk I/O counters; rates are averaged over sample_seconds."""
    device: Optional[str] = None           # None = all disks combined
    read_bytes: Optional[int] = None
    write_bytes: Optional[int] = None
    read_count: Optional[int] = None
    write_count: Optional[int] = None
    read_mbps: Optional[float] = None
    write_mbps: Optional[float] = None
    busy_percent: Optional[float] = None
    sample_seconds: Optional[float] = None


@dataclass
class GPUInfo(Model):
    """One graphics adapter. Live usage/temperature are only filled when a vendor tool reports them."""
    name: Optional[str] = None
    vram_mb: Optional[int] = None
    driver_version: Optional[str] = None
    driver_date: Optional[str] = None      # ISO date string if known
    usage_percent: Optional[float] = None
    temperature_c: Optional[float] = None
    is_integrated: Optional[bool] = None


@dataclass
class TemperatureReading(Model):
    """One temperature sensor reading in Celsius."""
    label: str = ""                        # "Package id 0", "CPU", "acpitz"
    current_c: Optional[float] = None
    high_c: Optional[float] = None
    critical_c: Optional[float] = None
    sensor: Optional[str] = None           # psutil group name, e.g. "coretemp"


@dataclass
class BatteryInfo(Model):
    """Laptop battery charge and wear. Capacities are in mWh."""
    percent: Optional[float] = None
    plugged_in: Optional[bool] = None
    seconds_left: Optional[int] = None
    design_capacity_mwh: Optional[int] = None
    full_charge_capacity_mwh: Optional[int] = None
    wear_percent: Optional[float] = None   # 100 * (1 - full / design)
    cycle_count: Optional[int] = None


@dataclass
class ProcessInfo(Model):
    """One running process, as sampled by the processes collector."""
    pid: int = 0
    name: str = ""
    cpu_percent: Optional[float] = None    # can exceed 100 on multi-core
    memory_mb: Optional[float] = None
    memory_percent: Optional[float] = None
    username: Optional[str] = None


@dataclass
class StartupItem(Model):
    """One program configured to start automatically. ``enabled=None`` means unknown."""
    name: str = ""
    command: Optional[str] = None
    location: Optional[str] = None         # "HKCU\\...\\Run", "Startup folder", "~/.config/autostart"
    enabled: Optional[bool] = None


@dataclass
class BenchmarkResult(Model):
    """Relative benchmark scores: compare runs of this tool over time, not with other benchmarks."""
    cpu_single_score: Optional[float] = None   # higher is better, toolkit-relative
    cpu_multi_score: Optional[float] = None
    disk_read_mbps: Optional[float] = None
    disk_write_mbps: Optional[float] = None
    memory_mbps: Optional[float] = None
    duration_seconds: Optional[float] = None


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------

def _now() -> datetime:
    """Current time in UTC (default for ``SystemSnapshot.timestamp``)."""
    return datetime.now(timezone.utc)


@dataclass
class SystemSnapshot(Model):
    """Everything collected in one scan. Any section may be missing; see ``errors`` for why."""
    timestamp: datetime = field(default_factory=_now)
    schema_version: int = SCHEMA_VERSION
    os: Optional[OSInfo] = None
    cpu: Optional[CPUInfo] = None
    memory: Optional[MemoryInfo] = None
    disks: list[DiskInfo] = field(default_factory=list)
    disk_io: list[DiskIOStats] = field(default_factory=list)
    gpus: list[GPUInfo] = field(default_factory=list)
    temperatures: list[TemperatureReading] = field(default_factory=list)
    battery: Optional[BatteryInfo] = None   # None = no battery / unknown
    top_processes: list[ProcessInfo] = field(default_factory=list)
    startup_items: list[StartupItem] = field(default_factory=list)
    benchmark: Optional[BenchmarkResult] = None
    errors: list[str] = field(default_factory=list)  # "collector: reason"

    @property
    def system_drive(self) -> Optional[DiskInfo]:
        """The disk marked ``is_system_drive`` (where the OS lives), or None."""
        return next((d for d in self.disks if d.is_system_drive), None)

    def add_error(self, collector: str, reason: str) -> None:
        """Record that ``collector`` failed or was limited, as "collector: reason"."""
        self.errors.append(f"{collector}: {reason}")


__all__ = [
    "SCHEMA_VERSION", "DiskKind", "SmartStatus", "Model",
    "OSInfo", "CPUInfo", "MemoryModule", "MemoryInfo", "SmartHealth", "DiskInfo",
    "DiskIOStats", "GPUInfo", "TemperatureReading", "BatteryInfo", "ProcessInfo",
    "StartupItem", "BenchmarkResult", "SystemSnapshot",
]
