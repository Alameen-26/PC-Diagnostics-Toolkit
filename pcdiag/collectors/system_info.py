"""Device type, power plan and Windows Update status. Added in v0.3.0.

Read-only, no admin needed:

* ``collect_device``: chassis type (laptop / desktop / ...) from
  ``Win32_SystemEnclosure.ChassisTypes`` plus maker and model from
  ``Win32_ComputerSystem``. On Linux the same SMBIOS values are world-readable
  in ``/sys/class/dmi/id``. Used so upgrade advice fits the machine (SODIMM vs
  DIMM, 2.5" vs 3.5" drives) and so "no battery detected" can be told apart
  from "this is a desktop".
* ``collect_software`` (Windows only): the active power plan from
  ``powercfg /getactivescheme``; the newest installed update from
  ``Get-HotFix`` (it lists cumulative/security updates, not Store or Defender
  definition updates) and whether Windows Update is waiting for a restart
  (two well-known registry keys). Returns None elsewhere.

Nothing here changes a setting: pcdiag only reads.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Optional

from ..models import DeviceInfo, SoftwareState, SystemSnapshot
from . import _shell
from ._util import to_int

#: SMBIOS chassis-type codes -> coarse kind (SMBIOS spec 7.4.1).
CHASSIS_KINDS = {
    3: "desktop", 4: "desktop", 5: "desktop", 6: "desktop", 7: "desktop", 15: "desktop", 16: "desktop",
    24: "desktop", 35: "desktop", 36: "desktop",
    8: "laptop", 9: "laptop", 10: "laptop", 14: "laptop", 31: "laptop", 32: "laptop",
    11: "tablet", 30: "tablet",
    13: "all-in-one",
    17: "server", 23: "server", 28: "server", 29: "server",
}

_PS_DEVICE = r"""
$e = @(Get-CimInstance Win32_SystemEnclosure | ForEach-Object { $_.ChassisTypes })
$cs = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, PCSystemType
ConvertTo-Json -Compress -InputObject @{ chassis = $e; system = $cs }
"""

# The two registry keys are the standard "restart pending" markers and are
# readable without admin. Get-HotFix can take a few seconds on old PCs.
_PS_UPDATES = r"""
$hf = Get-HotFix -ErrorAction SilentlyContinue | Where-Object { $_.InstalledOn } |
  Sort-Object InstalledOn -Descending | Select-Object -First 1
$last = if ($hf) { $hf.InstalledOn.ToString('yyyy-MM-dd') } else { $null }
$rb = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
      (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
ConvertTo-Json -Compress -InputObject @{ last_update = $last; reboot_pending = $rb }
"""

_SCHEME_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*\(([^)]*)\)")

#: Well-known plan GUIDs, so a translated Windows still maps to English names.
KNOWN_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Power saver",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Ultimate Performance",
}


def chassis_kind(codes: list[int], pc_system_type: Any = None) -> Optional[str]:
    """Map SMBIOS chassis codes (and Win32_ComputerSystem.PCSystemType) to a kind."""
    for c in codes:
        kind = CHASSIS_KINDS.get(c)
        if kind:
            return kind
    pst = to_int(pc_system_type)  # 1 desktop, 2 mobile, 3 workstation, 4/5/7 server, 8 slate
    return {1: "desktop", 2: "laptop", 3: "desktop", 4: "server", 5: "server", 7: "server", 8: "tablet"}.get(pst) \
        if pst else ("other" if codes else None)


def parse_device(data: Any) -> Optional[DeviceInfo]:
    """Parse the _PS_DEVICE JSON into DeviceInfo (None if empty)."""
    if not isinstance(data, dict):
        return None
    codes = [c for c in (to_int(x) for x in _shell.as_list(data.get("chassis"))) if c is not None]
    system = data.get("system") if isinstance(data.get("system"), dict) else {}
    info = DeviceInfo(
        chassis=chassis_kind(codes, system.get("PCSystemType")), chassis_types=codes,
        manufacturer=(str(system.get("Manufacturer") or "").strip() or None),
        model=(str(system.get("Model") or "").strip() or None),
    )
    return info if (info.chassis or info.manufacturer or info.model) else None


def parse_power_scheme(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``powercfg /getactivescheme`` output -> (plan name, guid)."""
    if not text:
        return None, None
    m = _SCHEME_RE.search(text)
    if not m:
        return None, None
    guid = m.group(1).lower()
    return KNOWN_PLANS.get(guid, m.group(2).strip() or None), guid


def parse_updates(data: Any, now: datetime) -> dict:
    """_PS_UPDATES JSON -> SoftwareState fields (last update, age in days, reboot pending)."""
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    last = data.get("last_update")
    if isinstance(last, str):
        try:
            d = date.fromisoformat(last[:10])
            out["last_update_installed"] = d.isoformat()
            out["days_since_update"] = max(0, (now.date() - d).days)
        except ValueError:
            pass
    if isinstance(data.get("reboot_pending"), bool):
        out["reboot_pending"] = data["reboot_pending"]
    return out


def _linux_device() -> Optional[DeviceInfo]:
    """SMBIOS values from sysfs (no root needed)."""
    base = "/sys/class/dmi/id"

    def read(name: str) -> Optional[str]:
        try:
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            return None
    code = to_int(read("chassis_type"))
    codes = [code] if code is not None else []
    info = DeviceInfo(chassis=chassis_kind(codes), chassis_types=codes,
                      manufacturer=read("sys_vendor"), model=read("product_name"))
    return info if (info.chassis or info.manufacturer or info.model) else None


def collect_device(snapshot: SystemSnapshot) -> Optional[DeviceInfo]:
    """Laptop or desktop, maker and model. Never raises."""
    if not _shell.is_windows():
        return _linux_device()
    errors: list[str] = []
    info = parse_device(_shell.run_powershell_json(_PS_DEVICE, on_error=errors.append))
    if info is None:
        snapshot.add_error("device", "device type unavailable: " + ("; ".join(errors) or "no data"))
    return info


def collect_software(snapshot: SystemSnapshot) -> Optional[SoftwareState]:
    """Power plan and Windows Update status (Windows only; None elsewhere)."""
    if not _shell.is_windows():
        return None
    errors: list[str] = []
    state = SoftwareState()
    exe = _shell.which("powercfg") or "powercfg"
    state.power_plan, state.power_plan_guid = parse_power_scheme(
        _shell.run_command([exe, "/getactivescheme"], timeout=10, on_error=errors.append))
    for key, value in parse_updates(_shell.run_powershell_json(_PS_UPDATES, timeout=60, on_error=errors.append),
                                    snapshot.timestamp).items():
        setattr(state, key, value)
    if state.power_plan is None and state.last_update_installed is None and state.reboot_pending is None:
        snapshot.add_error("software", "power plan / Windows Update status unavailable: "
                           + ("; ".join(errors) or "no data"))
        return None
    return state
