"""Battery collector: charge, plug state and, on Windows, battery wear.

psutil gives charge level and whether the charger is connected. Wear needs
the design capacity (what the battery held new) and the full-charge
capacity (what it holds now). Windows exposes both, plus cycle count, in
``powercfg /batteryreport /xml``, which runs without admin. The report is
written to a temporary directory and deleted afterwards.

Desktops return None (no battery), which is not an error.

Added in v0.1.0
"""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional

import psutil

from ..models import BatteryInfo, SystemSnapshot
from . import _shell
from ._util import to_int


def _local(tag: str) -> str:
    """Strip the XML namespace: "{http://...battery/2012}Battery" -> "Battery"."""
    return tag.rsplit("}", 1)[-1]


def parse_battery_report(xml_text: str) -> Optional[dict]:
    """Parse powercfg's XML report into design/full capacity and cycles.

    Returns None when the report lists no batteries. Multiple batteries
    (some ThinkPads have two) are summed, since wear is about total capacity.
    Capacities are in mWh. CycleCount is often blank on consumer laptops
    whose firmware doesn't track it.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    design = full = 0
    cycles: list[int] = []
    found = False
    for elem in root.iter():
        if _local(elem.tag) != "Battery":
            continue
        values = {_local(child.tag): (child.text or "").strip() for child in elem}
        d, f = to_int(values.get("DesignCapacity")), to_int(values.get("FullChargeCapacity"))
        if not d:
            continue
        found = True
        design += d
        full += f or 0
        c = to_int(values.get("CycleCount"))
        if c:
            cycles.append(c)
    if not found:
        return None
    return {
        "design_capacity_mwh": design,
        "full_charge_capacity_mwh": full or None,
        "cycle_count": max(cycles) if cycles else None,
    }


def wear_percent(design: Optional[int], full: Optional[int]) -> Optional[float]:
    """Battery wear: 100 * (1 - full / design), clamped to 0-100.

    New batteries can briefly exceed their design capacity, which would
    give negative wear, hence the clamp.
    """
    if not design or full is None:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1 - full / design))), 1)


def _windows_report(errors: list[str]) -> Optional[dict]:
    """Run powercfg in a temp dir and parse the result."""
    exe = _shell.which("powercfg") or "powercfg"
    with tempfile.TemporaryDirectory(prefix="pcdiag-") as tmp:
        path = os.path.join(tmp, "battery.xml")
        if _shell.run_command([exe, "/batteryreport", "/xml", "/output", path], timeout=30,
                              on_error=errors.append) is None:
            return None
        try:
            # powercfg writes UTF-8 with a BOM; utf-8-sig strips it.
            with open(path, encoding="utf-8-sig") as fh:
                return parse_battery_report(fh.read())
        except OSError as exc:
            errors.append(f"battery report not written: {exc}")
            return None


def collect(snapshot: SystemSnapshot) -> Optional[BatteryInfo]:
    """Collect battery info, or None if the machine has no battery."""
    try:
        batt = psutil.sensors_battery()
    except Exception as exc:
        snapshot.add_error("battery", f"battery status unavailable: {exc}")
        batt = None
    if batt is None:
        return None  # desktop, VM or unsupported; not an error

    info = BatteryInfo(percent=round(batt.percent, 1), plugged_in=batt.power_plugged)
    # psutil uses negative sentinels for "charging/unlimited" and "unknown".
    if isinstance(batt.secsleft, (int, float)) and batt.secsleft >= 0:
        info.seconds_left = int(batt.secsleft)

    if _shell.is_windows():
        errors: list[str] = []
        report = _windows_report(errors)
        if report:
            info.design_capacity_mwh = report["design_capacity_mwh"]
            info.full_charge_capacity_mwh = report["full_charge_capacity_mwh"]
            info.cycle_count = report["cycle_count"]
            info.wear_percent = wear_percent(info.design_capacity_mwh, info.full_charge_capacity_mwh)
        else:
            snapshot.add_error("battery", "battery health report unavailable: " + ("; ".join(errors) or "no data"))
    return info
