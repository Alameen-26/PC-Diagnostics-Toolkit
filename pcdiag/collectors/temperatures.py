"""Temperature collector: CPU/board sensors where the OS exposes them.

* Linux/BSD: ``psutil.sensors_temperatures()`` (hwmon), no root needed.
* Windows: psutil has no sensor support, so the ACPI thermal zone WMI class
  ``MSAcpi_ThermalZoneTemperature`` is tried. It usually needs an
  Administrator shell, and many desktops' firmware doesn't populate it at
  all. When nothing is readable, that is recorded in ``snapshot.errors``
  rather than silently returning an empty list. Real per-core temps on
  Windows need a kernel driver (e.g. LibreHardwareMonitor), deliberately
  out of scope.

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
        ))
    return readings


def collect(snapshot: SystemSnapshot) -> list[TemperatureReading]:
    """Collect temperature readings; records why when none are available."""
    readings: list[TemperatureReading] = []
    if _shell.is_windows():
        errors: list[str] = []
        readings = parse_thermal_zones(_shell.run_powershell_json(_PS_THERMAL, on_error=errors.append))
        if not readings:
            snapshot.add_error("temperatures", "not available: Windows only exposes ACPI thermal zones, which "
                                               "need an Administrator shell and aren't supported on many PCs")
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
            ))
    if not readings:
        snapshot.add_error("temperatures", "not available: no hardware sensors exposed (common in VMs/containers)")
    return readings
