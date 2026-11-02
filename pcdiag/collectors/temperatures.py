"""Temperature collector: CPU/board sensors where the OS exposes them.

* Linux/BSD: ``psutil.sensors_temperatures()`` (hwmon), no root needed.
* Windows: psutil has no sensor support, so the ACPI thermal zone WMI class
  ``MSAcpi_ThermalZoneTemperature`` is tried. It usually needs an
  Administrator shell, and many desktops' firmware doesn't populate it at
  all. When nothing is readable, that is recorded in ``snapshot.errors``
  rather than silently returning an empty list. Real per-core temps on
  Windows need a kernel driver, which this toolkit never installs.
* Changed in v0.3.0: if LibreHardwareMonitor (or OpenHardwareMonitor) is
  already running, it publishes its sensors over WMI; those readings are read
  first and flagged ``is_cpu=True`` for real CPU sensors. Nothing is installed
  or started by pcdiag; without it the old ACPI path is used.
* Changed in v0.3.0: every reading carries ``is_cpu`` (True / False / None =
  can't tell), so an ACPI or motherboard zone is never mistaken for the CPU.

GPU temperature is reported by the gpu collector instead.

Added in v0.1.0
"""
from __future__ import annotations

from typing import Any, Optional

import psutil

from ..models import SystemSnapshot, TemperatureReading
from . import _shell
from ._util import to_float

_PS_THERMAL = r"""
$t = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
  Select-Object InstanceName, CurrentTemperature, CriticalTripPoint)
ConvertTo-Json -Compress -InputObject $t
"""


# Added in v0.3.0: LibreHardwareMonitor / OpenHardwareMonitor WMI providers.
# They only exist while that app is running; SilentlyContinue keeps a missing
# namespace from being an error.
_PS_HWMON = r"""
$r = @()
foreach ($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor') {
  $r += @(Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction SilentlyContinue |
    Where-Object { $_.SensorType -eq 'Temperature' } |
    Select-Object Name, Identifier, Value, Max, @{n='Source';e={$ns.Split('/')[1]}})
}
ConvertTo-Json -Compress -InputObject $r
"""

#: psutil sensor groups that are definitely the CPU. Added in v0.3.0.
CPU_SENSOR_GROUPS = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpi-cpu"}
#: psutil groups that are definitely not the CPU. Added in v0.3.0.
NON_CPU_GROUPS = {"nvme", "amdgpu", "nouveau", "iwlwifi", "iwlwifi_1", "pch_skylake", "pch_cannonlake", "BAT0"}


def parse_hwmon_sensors(data: Any) -> list[TemperatureReading]:
    """Parse LibreHardwareMonitor/OpenHardwareMonitor WMI JSON. Added in v0.3.0.

    Identifiers look like ``/intelcpu/0/temperature/0`` or
    ``/amdcpu/0/temperature/2``; the hardware part says what the sensor is.
    """
    readings = []
    for raw in _shell.as_list(data):
        if not isinstance(raw, dict) or "Identifier" not in raw:
            continue
        value = to_float(raw.get("Value"))
        if value is None or value <= 0:
            continue
        ident = str(raw.get("Identifier") or "").lower()
        hw = ident.strip("/").split("/")[0] if ident else ""
        is_cpu = "cpu" in hw if hw else None
        name = str(raw.get("Name") or "sensor")
        readings.append(TemperatureReading(
            label=f"{'CPU' if is_cpu else hw or 'sensor'} {name}".strip(), current_c=round(value, 1),
            high_c=None, critical_c=None,
            sensor=str(raw.get("Source") or "LibreHardwareMonitor"), is_cpu=is_cpu,
        ))
    return readings


def decikelvin_to_c(value: Any) -> Optional[float]:
    """Convert ACPI tenths-of-Kelvin to Celsius (2982 -> 25.05 C)."""
    num = to_float(value)
    if not num:
        return None
    return round(num / 10.0 - 273.15, 1)


def parse_thermal_zones(data: Any) -> list[TemperatureReading]:
    """Parse MSAcpi_ThermalZoneTemperature JSON into readings."""
    readings = []
    for raw in _shell.as_list(data):
        if not isinstance(raw, dict):
            continue
        current = decikelvin_to_c(raw.get("CurrentTemperature"))
        # Some firmware reports a fixed placeholder (e.g. 27.8 C forever);
        # we can't detect that reliably, so the label says what it is.
        if current is None or current <= 0:
            continue
        name = str(raw.get("InstanceName") or "ThermalZone").split("\\")[-1]
        readings.append(TemperatureReading(
            label=f"ACPI {name}", current_c=current,
            critical_c=decikelvin_to_c(raw.get("CriticalTripPoint")), sensor="acpi-wmi",
            is_cpu=None,  # Added in v0.3.0: an ACPI zone may or may not be the CPU
        ))
    return readings


def collect(snapshot: SystemSnapshot) -> list[TemperatureReading]:
    """Collect temperature readings; records why when none are available."""
    readings: list[TemperatureReading] = []
    if _shell.is_windows():
        errors: list[str] = []
        # Changed in v0.3.0: real CPU sensors from a running LibreHardwareMonitor first.
        readings = parse_hwmon_sensors(_shell.run_powershell_json(_PS_HWMON, on_error=lambda e: None))
        readings += parse_thermal_zones(_shell.run_powershell_json(_PS_THERMAL, on_error=errors.append))
        if not readings:
            snapshot.add_error("temperatures", "not available: Windows only exposes ACPI thermal zones, which "
                                               "need an Administrator shell and aren't supported on many PCs")
        return readings

    if _shell.is_macos():
        # Added in v1.0.0: macOS only shares temperatures with admin tools
        # (powermetrics needs sudo), so say so instead of guessing.
        snapshot.add_error("temperatures", "not available: macOS doesn't share temperature sensors with apps "
                                           "without admin rights")
        return readings
    sensors_fn = getattr(psutil, "sensors_temperatures", None)  # absent on macOS/Windows
    try:
        groups = sensors_fn() if sensors_fn else {}
    except Exception as exc:
        snapshot.add_error("temperatures", f"sensor read failed: {exc}")
        return []
    for group, entries in (groups or {}).items():
        for i, e in enumerate(entries):
            if e.current is None:
                continue
            readings.append(TemperatureReading(
                label=e.label or f"{group} {i}", current_c=round(e.current, 1),
                # psutil uses None or 0 when the driver doesn't publish limits.
                high_c=e.high or None, critical_c=e.critical or None, sensor=group,
                # Added in v0.3.0: CPU-sensor flag from the driver group name.
                is_cpu=True if group in CPU_SENSOR_GROUPS else (False if group in NON_CPU_GROUPS else None),
            ))
    if not readings:
        snapshot.add_error("temperatures", "not available: no hardware sensors exposed (common in VMs/containers)")
    return readings
