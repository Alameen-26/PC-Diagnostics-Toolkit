"""Extra hardware details for macOS and Linux. Added in v1.0.0.

psutil already gives usage figures (CPU, RAM, disk space, battery charge)
on every system. This module fills in the details psutil can't see, using
tools that ship with the operating system and need no admin rights:

* macOS: ``sysctl`` (CPU name), ``system_profiler`` (Mac model, battery
  health, graphics, RAM) and ``diskutil`` (SSD or hard drive, health),
  plus the LaunchAgents folders for startup items.
* Linux: ``/sys/class/power_supply`` (battery wear and cycles, the same
  numbers ``upower`` shows) and ``lspci`` (graphics card names).

Every function here follows the project rule: it never raises, and a
value it can't read stays ``None``, which the report shows as
"can't tell". The ``parse_*`` functions are pure (text in, values out)
so the tests can feed them saved Mac output on any computer.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..models import DeviceInfo, DiskKind, GPUInfo, MemoryModule, SmartHealth, SmartStatus, StartupItem
from . import _shell
from ._util import to_int

# ---------------------------------------------------------------------------
# macOS: run a tool, return parsed data (or None)
# ---------------------------------------------------------------------------


def _system_profiler(data_type: str, errors: Optional[list[str]] = None) -> Optional[dict]:
    """Run ``system_profiler <type> -json`` and return the parsed JSON."""
    exe = _shell.which("system_profiler")
    if exe is None:
        return None
    out = _shell.run_command([exe, data_type, "-json"], timeout=30,
                             on_error=errors.append if errors is not None else None)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None


def mac_cpu_name() -> Optional[str]:
    """CPU name, e.g. "Apple M2" or "Intel(R) Core(TM) i5-8257U"."""
    exe = _shell.which("sysctl")
    if exe is None:
        return None
    out = _shell.run_command([exe, "-n", "machdep.cpu.brand_string"], timeout=5)
    return out.strip() if out and out.strip() else None


# ---------------------------------------------------------------------------
# macOS: device (which Mac is this?)
# ---------------------------------------------------------------------------


def parse_mac_hardware(data: Any) -> Optional[DeviceInfo]:
    """``SPHardwareDataType`` JSON -> DeviceInfo (laptop/desktop, model)."""
    items = (data or {}).get("SPHardwareDataType") if isinstance(data, dict) else None
    if not items or not isinstance(items[0], dict):
        return None
    hw = items[0]
    name = str(hw.get("machine_name") or "").strip()      # "MacBook Air"
    ident = str(hw.get("machine_model") or hw.get("model_number") or "").strip()  # "Mac14,2"
    # Every MacBook is a laptop; every other Mac (iMac, mini, Studio, Pro) is a desktop.
    chassis = "laptop" if "macbook" in name.lower() else ("desktop" if name else None)
    model = " ".join(x for x in (name, f"({ident})" if ident else "") if x) or None
    return DeviceInfo(chassis=chassis, chassis_types=[], manufacturer="Apple", model=model)


def mac_device() -> Optional[DeviceInfo]:
    """Which Mac this is, or None when system_profiler isn't usable."""
    return parse_mac_hardware(_system_profiler("SPHardwareDataType"))


# ---------------------------------------------------------------------------
# macOS: battery health
# ---------------------------------------------------------------------------


def parse_mac_power(data: Any) -> dict:
    """``SPPowerDataType`` JSON -> {"cycle_count", "wear_percent", "condition"}.

    Newer macOS shows "Maximum Capacity" as a percent of the new battery
    (e.g. "87%"), so wear is 100 minus that. Older versions only give the
    cycle count and a condition word ("Normal", "Service Recommended").
    """
    result: dict = {}
    items = (data or {}).get("SPPowerDataType") if isinstance(data, dict) else None
    for item in items or []:
        health = item.get("sppower_battery_health_info") if isinstance(item, dict) else None
        if not isinstance(health, dict):
            continue
        result["cycle_count"] = to_int(health.get("sppower_battery_cycle_count"))
        result["condition"] = health.get("sppower_battery_health") or None
        max_cap = str(health.get("sppower_battery_health_maximum_capacity") or "").strip().rstrip("%")
        cap = to_int(max_cap)
        if cap is not None and 0 < cap <= 100:
            result["wear_percent"] = float(100 - cap)
        break
    return result


def mac_battery() -> dict:
    """Battery health fields for a MacBook (empty dict when unknown)."""
    return parse_mac_power(_system_profiler("SPPowerDataType"))


# ---------------------------------------------------------------------------
# macOS: graphics and RAM
# ---------------------------------------------------------------------------


def parse_mac_displays(data: Any) -> list[GPUInfo]:
    """``SPDisplaysDataType`` JSON -> GPUInfo list."""
    gpus = []
    items = (data or {}).get("SPDisplaysDataType") if isinstance(data, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = item.get("sppci_model") or item.get("_name")
        vram = str(item.get("spdisplays_vram") or item.get("spdisplays_vram_shared") or "")
        vram_mb = None
        parts = vram.split()
        if parts and to_int(parts[0]) is not None:
            vram_mb = to_int(parts[0]) * (1024 if len(parts) > 1 and parts[1].upper().startswith("GB") else 1)
        # Apple chips share memory with the CPU, so they count as integrated.
        bus = str(item.get("sppci_bus") or "").lower()
        integrated = True if ("builtin" in bus or str(name).startswith("Apple")) else (False if bus else None)
        gpus.append(GPUInfo(name=name, vram_mb=vram_mb, is_integrated=integrated))
    return gpus


def mac_gpus() -> list[GPUInfo]:
    """Graphics chips on this Mac."""
    return parse_mac_displays(_system_profiler("SPDisplaysDataType"))


def parse_mac_memory(data: Any) -> dict:
    """``SPMemoryDataType`` JSON -> MemoryInfo field values.

    Apple-chip Macs report one block of soldered memory ("LPDDR5") with no
    slots. Older Intel Macs list each slot, and some (iMac, Mac mini) have
    sticks you can upgrade.
    """
    items = (data or {}).get("SPMemoryDataType") if isinstance(data, dict) else None
    if not items or not isinstance(items[0], dict):
        return {}
    top = items[0]
    fields: dict = {}
    slots = top.get("_items")
    if isinstance(slots, list) and slots:
        modules = []
        for s in slots:
            size = str(s.get("dimm_size") or "")
            gb = to_int(size.split()[0]) if size and size.split()[0].isdigit() else None
            speed = to_int(str(s.get("dimm_speed") or "0").split()[0])
            modules.append(MemoryModule(slot=s.get("_name"), capacity_gb=float(gb) if gb else None,
                                        speed_mhz=speed or None, type=s.get("dimm_type") or None,
                                        manufacturer=s.get("dimm_manufacturer") or None))
        filled = [m for m in modules if m.capacity_gb]
        fields.update(modules=filled, slots_total=len(modules), slots_used=len(filled))
        if filled:
            fields["type"] = filled[0].type
            fields["speed_mhz"] = filled[0].speed_mhz
        upgradeable = str(top.get("is_memory_upgradeable") or "").lower()
        if upgradeable == "no":
            fields.update(soldered=True, form_factor="soldered")
    else:
        mem_type = top.get("dimm_type") or None
        fields.update(type=mem_type, soldered=True, form_factor="soldered")
    return fields


def mac_memory() -> dict:
    """RAM details for this Mac."""
    return parse_mac_memory(_system_profiler("SPMemoryDataType"))


# ---------------------------------------------------------------------------
# macOS: drive type and health
# ---------------------------------------------------------------------------


def parse_diskutil_info(raw: Optional[bytes]) -> Optional[tuple[DiskKind, Optional[str], Optional[str], SmartHealth]]:
    """``diskutil info -plist <disk>`` output -> (kind, bus, model, health).

    ``SMARTStatus`` is "Verified" (healthy), "Failing", or "Not Supported"
    (common for external USB drives, so that stays unknown).
    """
    if not raw:
        return None
    try:
        info = plistlib.loads(raw)
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    bus = info.get("BusProtocol") or None             # "Apple Fabric", "PCI-Express", "USB"
    solid = info.get("SolidState")
    kind = DiskKind.UNKNOWN
    if solid is True:
        kind = DiskKind.NVME if bus and ("pci" in bus.lower() or "fabric" in bus.lower()) else DiskKind.SSD
    elif solid is False:
        kind = DiskKind.HDD
    status = {"verified": SmartStatus.OK, "failing": SmartStatus.FAILING}.get(
        str(info.get("SMARTStatus") or "").strip().lower(), SmartStatus.UNKNOWN)
    model = (info.get("MediaName") or info.get("IORegistryEntryName") or "").strip() or None
    return kind, bus, model, SmartHealth(status=status, source="diskutil")


def mac_disk(device: str) -> Optional[tuple[DiskKind, Optional[str], Optional[str], SmartHealth]]:
    """Drive details for one mounted device such as /dev/disk3s1."""
    exe = _shell.which("diskutil")
    if exe is None:
        return None
    try:
        proc = subprocess.run([exe, "info", "-plist", device], capture_output=True, timeout=15)
    except Exception:
        return None
    return parse_diskutil_info(proc.stdout) if proc.returncode == 0 else None


# ---------------------------------------------------------------------------
# macOS: startup items
# ---------------------------------------------------------------------------


def mac_startup(folders: Optional[list[Path]] = None) -> list[StartupItem]:
    """Apps that start at login, from the LaunchAgents folders.

    Only reads the files; pcdiag never turns anything off itself. Items
    added through System Settings > Login Items are stored in a private
    Apple database that apps can't read, so those don't appear here.
    """
    if folders is None:
        folders = [Path.home() / "Library" / "LaunchAgents", Path("/Library/LaunchAgents")]
    items: list[StartupItem] = []
    for folder in folders:
        try:
            files = sorted(folder.glob("*.plist"))
        except OSError:
            continue
        for f in files:
            try:
                data = plistlib.loads(f.read_bytes())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            args = data.get("ProgramArguments") or []
            command = data.get("Program") or (" ".join(str(a) for a in args) if args else None)
            items.append(StartupItem(name=str(data.get("Label") or f.stem), command=command,
                                     location=str(folder), enabled=not bool(data.get("Disabled", False))))
    return items


# ---------------------------------------------------------------------------
# Linux: battery health and graphics
# ---------------------------------------------------------------------------


def linux_battery(root: Path = Path("/sys/class/power_supply")) -> dict:
    """Battery design/full capacity and cycles from sysfs (no root needed).

    Drivers publish either energy (µWh) or charge (µAh) files. Both give the
    same wear ratio; only energy converts to mWh for the report.
    """
    try:
        batteries = sorted(p for p in root.iterdir() if p.name.startswith("BAT"))
    except OSError:
        return {}
    for bat in batteries:
        def read(name: str) -> Optional[int]:
            try:
                return to_int((bat / name).read_text().strip())
            except OSError:
                return None
        result: dict = {}
        design, full = read("energy_full_design"), read("energy_full")
        if design and full:
            result["design_capacity_mwh"] = design // 1000
            result["full_charge_capacity_mwh"] = full // 1000
        else:
            design, full = read("charge_full_design"), read("charge_full")
        if design and full:
            result["wear_percent"] = round(max(0.0, 100.0 * (1 - full / design)), 1)
        cycles = read("cycle_count")
        if cycles:  # many drivers report 0 when they don't count cycles
            result["cycle_count"] = cycles
        return result
    return {}


def parse_lspci(text: Optional[str]) -> list[GPUInfo]:
    """``lspci`` output -> GPUInfo for each display controller."""
    gpus = []
    for line in (text or "").splitlines():
        lower = line.lower()
        if "vga compatible controller" in lower or "3d controller" in lower or "display controller" in lower:
            name = line.split(":", 2)[-1].strip()
            integrated = None
            if "intel" in lower:
                integrated = True
            elif "nvidia" in lower:
                integrated = False
            gpus.append(GPUInfo(name=name or None, is_integrated=integrated))
    return gpus


def linux_gpus() -> list[GPUInfo]:
    """Graphics card names from lspci (when installed)."""
    exe = _shell.which("lspci")
    return parse_lspci(_shell.run_command([exe], timeout=10)) if exe else []
