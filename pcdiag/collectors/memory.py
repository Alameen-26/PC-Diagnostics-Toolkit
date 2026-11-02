"""Memory collector: RAM and swap usage, plus RAM stick details on Windows.

Usage figures come from psutil on every platform. Module details (how many
slots are filled, speed, DDR generation) come from the Windows CIM classes
``Win32_PhysicalMemory`` and ``Win32_PhysicalMemoryArray``, which don't need
admin. On Linux the equivalent (``dmidecode``) needs root, so those fields
are simply left as None there.

Added in v0.1.0. Changed in v0.3.0: also reads the stick form factor
(DIMM / SODIMM / soldered) and the board's maximum RAM (MaxCapacity) for the
upgrade-compatibility checks.
"""
from __future__ import annotations

from typing import Any, Optional

import psutil

from ..models import MemoryInfo, MemoryModule, SystemSnapshot
from . import _shell
from ._util import bytes_to_gb, rnd, to_int

#: SMBIOS memory-type codes (SMBIOS spec, table "Memory Device - Type").
#: Win32_PhysicalMemory.MemoryType is unreliable (often 0 on DDR4+), so the
#: newer SMBIOSMemoryType property is used instead.
SMBIOS_MEMORY_TYPES = {
    20: "DDR", 21: "DDR2", 22: "DDR2 FB-DIMM", 24: "DDR3", 26: "DDR4",
    27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5",
}

# Added in v0.3.0: Win32_PhysicalMemory.FormFactor (CIM codes). Chip
# packages (TSOP, SMD, BGA, FPBGA, ...) mean the RAM is soldered to the board.
FORM_FACTORS = {8: "DIMM", 12: "SODIMM", 11: "RIMM", 13: "SRIMM", 7: "SIMM", 6: "proprietary"}
SOLDERED_FORM_FACTORS = {2, 3, 4, 5, 9, 10, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
# LPDDR memory is always soldered (there are no LPDDR sticks), whatever FormFactor says.
SOLDERED_TYPES = {"LPDDR", "LPDDR2", "LPDDR3", "LPDDR4", "LPDDR5"}


def form_factor_name(code: Any, mem_type: Optional[str] = None) -> Optional[str]:
    """CIM FormFactor code (+ DDR type) -> "DIMM" / "SODIMM" / "soldered" / None. Added in v0.3.0."""
    if mem_type in SOLDERED_TYPES:
        return "soldered"
    c = to_int(code)
    if c in SOLDERED_FORM_FACTORS:
        return "soldered"
    return FORM_FACTORS.get(c) if c is not None else None


# One PowerShell round trip for both classes: each powershell.exe launch
# costs about a second, so batching queries matters.
_PS_MEMORY = r"""
$m = @(Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel, DeviceLocator, Capacity, Speed, ConfiguredClockSpeed, SMBIOSMemoryType, Manufacturer, FormFactor)
$a = @(Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MemoryDevices, MaxCapacity, MaxCapacityEx, Use)
ConvertTo-Json -Depth 3 -Compress -InputObject @{ modules = $m; arrays = $a }
"""


def parse_windows_memory(data: Any) -> dict:
    """Turn the CIM JSON into MemoryInfo field values.

    Returns a dict of fields to set (``modules``, ``slots_used``,
    ``slots_total``, ``speed_mhz``, ``type``). Pure function so it can be
    tested with canned JSON on any OS.
    """
    if not isinstance(data, dict):
        return {}
    modules: list[MemoryModule] = []
    for raw in _shell.as_list(data.get("modules")):
        if not isinstance(raw, dict):
            continue
        # ConfiguredClockSpeed is what the RAM actually runs at; Speed is
        # its rated maximum. A gap (e.g. 3200-rated running at 2133) means
        # XMP/EXPO is off, which analysis can flag.
        speed = to_int(raw.get("ConfiguredClockSpeed")) or to_int(raw.get("Speed"))
        slot = raw.get("DeviceLocator") or raw.get("BankLabel")
        manufacturer = (raw.get("Manufacturer") or "").strip() or None
        mem_type = SMBIOS_MEMORY_TYPES.get(to_int(raw.get("SMBIOSMemoryType")) or -1)
        modules.append(
            MemoryModule(
                slot=str(slot).strip() if slot else None,
                capacity_gb=bytes_to_gb(raw.get("Capacity"), 1),
                speed_mhz=speed,
                type=mem_type,
                manufacturer=manufacturer,
                form_factor=form_factor_name(raw.get("FormFactor"), mem_type),  # Added in v0.3.0
            )
        )

    # Use == 3 means "system memory"; other arrays (e.g. flash, video) are
    # ignored. Older firmware leaves Use blank, so blank counts as system.
    slots_total = 0
    max_kb = 0  # Added in v0.3.0
    for arr in _shell.as_list(data.get("arrays")):
        if isinstance(arr, dict) and to_int(arr.get("Use")) in (None, 3):
            slots_total += to_int(arr.get("MemoryDevices")) or 0
            # Added in v0.3.0: MaxCapacity is in KB and tops out at ~4 TB;
            # MaxCapacityEx (also KB) is used when larger. 0 = not reported.
            cap = max(to_int(arr.get("MaxCapacityEx")) or 0, to_int(arr.get("MaxCapacity")) or 0)
            max_kb += cap

    # Added in v0.3.0: overall form factor / soldered flag. Mixed boards
    # (some soldered + one slot) count as not fully soldered.
    factors = {m.form_factor for m in modules if m.form_factor}
    if factors == {"soldered"}:
        form, soldered = "soldered", True
    elif factors and "soldered" not in factors and len(factors) == 1:
        form, soldered = next(iter(factors)), False
    elif factors:
        form, soldered = "mixed", False  # part soldered + part socketed, or mixed stick types
    else:
        form, soldered = None, None

    speeds = [m.speed_mhz for m in modules if m.speed_mhz]
    types = [m.type for m in modules if m.type]
    return {
        "modules": modules,
        "slots_used": len(modules) if modules else None,
        "slots_total": slots_total or None,
        # Mixed sticks run at the slowest one's speed.
        "speed_mhz": min(speeds) if speeds else None,
        "type": types[0] if types else None,
        # Added in v0.3.0
        "max_capacity_gb": round(max_kb / 1024 / 1024, 1) if max_kb else None,
        "form_factor": form,
        "soldered": soldered,
    }


def collect(snapshot: SystemSnapshot) -> MemoryInfo:
    """Collect RAM/swap usage and (on Windows) module details."""
    info = MemoryInfo()
    try:
        vm = psutil.virtual_memory()
        info.total_gb = bytes_to_gb(vm.total)
        info.available_gb = bytes_to_gb(vm.available)
        info.used_gb = bytes_to_gb(vm.total - vm.available)  # "in use" as Task Manager counts it
        info.percent = rnd(vm.percent)
    except Exception as exc:
        snapshot.add_error("memory", f"RAM usage unavailable: {exc}")
    try:
        # On Windows psutil derives "swap" from the page file.
        sw = psutil.swap_memory()
        info.swap_total_gb = bytes_to_gb(sw.total)
        info.swap_used_gb = bytes_to_gb(sw.used)
        info.swap_percent = rnd(sw.percent)
    except Exception as exc:
        snapshot.add_error("memory", f"swap usage unavailable: {exc}")

    if _shell.is_windows():
        errors: list[str] = []
        data = _shell.run_powershell_json(_PS_MEMORY, on_error=errors.append)
        fields = parse_windows_memory(data)
        if fields:
            for key, value in fields.items():
                setattr(info, key, value)
        else:
            snapshot.add_error("memory", "RAM module details unavailable: " + ("; ".join(errors) or "no data"))
    elif _shell.is_macos():  # Added in v1.0.0: system_profiler needs no admin on a Mac
        from .unix_extras import mac_memory
        for key, value in mac_memory().items():
            setattr(info, key, value)
    return info
