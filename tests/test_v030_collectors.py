"""v0.3.0 collectors: device type, power plan, Windows Update status, RAM
form factor / max capacity, and the CPU-sensor flag on temperatures.

Added in v0.3.0. All Windows paths run on any OS via the ``windows`` and
``ps_json`` fixtures (no real PowerShell).
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone

from pcdiag.analysis import analyze
from pcdiag.collectors import _shell, system_info, temperatures
from pcdiag.collectors.memory import form_factor_name, parse_windows_memory
from pcdiag.models import DeviceInfo, SoftwareState, SystemSnapshot, TemperatureReading

POWERCFG_SAVER = "Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)"
POWERCFG_LOCALISED = "GUID du mode de gestion de l'alimentation : 381b4222-f694-41f0-9685-ff5bb260df2e  (Utilisation normale)"
POWERCFG_CUSTOM = "Power Scheme GUID: 11111111-2222-3333-4444-555555555555  (My gaming plan)"


# --- Device type --------------------------------------------------------------

def test_chassis_kind_codes():
    """SMBIOS codes map to laptop/desktop/etc.; PCSystemType is the fallback."""
    assert system_info.chassis_kind([10]) == "laptop"
    assert system_info.chassis_kind([3]) == "desktop"
    assert system_info.chassis_kind([13]) == "all-in-one"
    assert system_info.chassis_kind([2], pc_system_type=2) == "laptop"   # "unknown" chassis, mobile system
    assert system_info.chassis_kind([2]) == "other"
    assert system_info.chassis_kind([]) is None


def test_parse_device():
    """Win32_SystemEnclosure + Win32_ComputerSystem JSON -> DeviceInfo."""
    info = system_info.parse_device({"chassis": 9, "system": {"Manufacturer": " Dell Inc. ", "Model": "Latitude 5490",
                                                              "PCSystemType": 2}})
    assert info == DeviceInfo(chassis="laptop", chassis_types=[9], manufacturer="Dell Inc.", model="Latitude 5490")
    assert info.is_laptop
    assert system_info.parse_device(None) is None
    assert system_info.parse_device({"chassis": [], "system": {}}) is None


def test_collect_device_windows(windows, ps_json):
    """collect_device on Windows parses the PowerShell JSON."""
    ps_json({"chassis": [3], "system": {"Manufacturer": "ASUS", "Model": "B650", "PCSystemType": 1}})
    info = system_info.collect_device(SystemSnapshot())
    assert info.chassis == "desktop" and not info.is_laptop


def test_collect_device_failure_records_error(windows, ps_json):
    """No data -> None plus a 'device' error."""
    ps_json(None, error="Access denied")
    snap = SystemSnapshot()
    assert system_info.collect_device(snap) is None
    assert any(e.startswith("device") and "Access denied" in e for e in snap.errors)


# --- Power plan + Windows Update --------------------------------------------------

def test_parse_power_scheme_known_and_custom():
    """Known GUIDs map to English names even on a translated Windows; custom keeps its name."""
    assert system_info.parse_power_scheme(POWERCFG_SAVER) == ("Power saver", "a1841308-3541-4fab-bc81-f71556f20b4a")
    assert system_info.parse_power_scheme(POWERCFG_LOCALISED)[0] == "Balanced"
    assert system_info.parse_power_scheme(POWERCFG_CUSTOM)[0] == "My gaming plan"
    assert system_info.parse_power_scheme("") == (None, None)
    assert system_info.parse_power_scheme("garbage") == (None, None)


def test_parse_updates():
    """Newest hotfix date -> age in days; reboot flag passed through."""
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    out = system_info.parse_updates({"last_update": "2026-06-09", "reboot_pending": True}, now)
    assert out == {"last_update_installed": "2026-06-09", "days_since_update": 135, "reboot_pending": True}
    assert system_info.parse_updates({"last_update": "not a date"}, now) == {}
    assert system_info.parse_updates(None, now) == {}


def test_collect_software_windows(windows, ps_json):
    """powercfg + update script -> SoftwareState."""
    windows.setattr(_shell, "run_command", lambda args, timeout=None, on_error=None: POWERCFG_SAVER)
    ps_json({"last_update": "2026-10-01", "reboot_pending": False})
    snap = SystemSnapshot(timestamp=datetime(2026, 10, 22, tzinfo=timezone.utc))
    sw = system_info.collect_software(snap)
    assert sw == SoftwareState(power_plan="Power saver", power_plan_guid="a1841308-3541-4fab-bc81-f71556f20b4a",
                               last_update_installed="2026-10-01", days_since_update=21, reboot_pending=False)


def test_collect_software_nothing_read(windows, ps_json):
    """Both sources fail -> None and an error that says what's missing."""
    windows.setattr(_shell, "run_command", lambda args, timeout=None, on_error=None: None)
    ps_json(None, error="timed out")
    snap = SystemSnapshot()
    assert system_info.collect_software(snap) is None
    assert any(e.startswith("software") for e in snap.errors)


def test_collect_software_not_windows(monkeypatch):
    """Not Windows -> None, no error (nothing to read)."""
    monkeypatch.setattr(_shell, "is_windows", lambda: False)
    snap = SystemSnapshot()
    assert system_info.collect_software(snap) is None and not snap.errors


# --- RAM form factor + max capacity --------------------------------------------------

def test_form_factor_names():
    """CIM FormFactor codes; LPDDR is always soldered."""
    assert form_factor_name(8) == "DIMM"
    assert form_factor_name(12) == "SODIMM"
    assert form_factor_name(12, "LPDDR4") == "soldered"
    assert form_factor_name(0) is None and form_factor_name(None) is None


def test_parse_memory_max_and_form_factor():
    """MaxCapacityEx (KB) wins over MaxCapacity; SODIMM sticks -> not soldered."""
    data = {"modules": [{"Capacity": 4 * 1024 ** 3, "SMBIOSMemoryType": 24, "FormFactor": 12, "Speed": 1600}],
            "arrays": [{"MemoryDevices": 2, "MaxCapacity": 16777216, "MaxCapacityEx": 0, "Use": 3}]}
    out = parse_windows_memory(data)
    assert out["max_capacity_gb"] == 16.0
    assert out["form_factor"] == "SODIMM" and out["soldered"] is False
    assert out["slots_total"] == 2 and out["slots_used"] == 1


def test_parse_memory_soldered_and_mixed():
    """All soldered -> soldered=True; soldered + one slot -> mixed, not soldered."""
    soldered = {"modules": [{"Capacity": 8 * 1024 ** 3, "FormFactor": 0, "SMBIOSMemoryType": 29}], "arrays": []}
    assert parse_windows_memory(soldered)["soldered"] is True  # LPDDR4 (29)
    mixed = {"modules": [{"Capacity": 8 * 1024 ** 3, "FormFactor": 23},
                         {"Capacity": 8 * 1024 ** 3, "FormFactor": 12}], "arrays": []}
    out = parse_windows_memory(mixed)
    assert out["form_factor"] == "mixed" and out["soldered"] is False


def test_parse_memory_unknown_form_factor():
    """No FormFactor data -> None (can't tell), and no max -> None."""
    out = parse_windows_memory({"modules": [{"Capacity": 8 * 1024 ** 3}], "arrays": [{"MemoryDevices": 2}]})
    assert out["form_factor"] is None and out["soldered"] is None and out["max_capacity_gb"] is None


# --- CPU sensor flag -------------------------------------------------------------

LHM = [
    {"Name": "CPU Package", "Identifier": "/intelcpu/0/temperature/6", "Value": 71.5, "Source": "LibreHardwareMonitor"},
    {"Name": "GPU Core", "Identifier": "/gpu-nvidia/0/temperature/0", "Value": 64.0, "Source": "LibreHardwareMonitor"},
    {"Name": "Temperature", "Identifier": "/nvme/0/temperature/0", "Value": 40.0, "Source": "LibreHardwareMonitor"},
    {"Name": "bad", "Identifier": "/amdcpu/0/temperature/1", "Value": None},
]


def test_parse_hwmon_sensors_flags_cpu():
    """LibreHardwareMonitor identifiers decide is_cpu; empty values are skipped."""
    r = temperatures.parse_hwmon_sensors(LHM)
    assert [(x.current_c, x.is_cpu) for x in r] == [(71.5, True), (64.0, False), (40.0, False)]
    assert r[0].label.startswith("CPU") and r[0].sensor == "LibreHardwareMonitor"


def test_windows_temperatures_prefer_lhm(windows, monkeypatch):
    """LHM readings come first; ACPI zones still added with is_cpu=None."""
    def fake(script, timeout=None, on_error=None):
        """LHM script gets sensors, ACPI script gets a zone."""
        if "LibreHardwareMonitor" in script:
            return LHM[:1]
        return [{"InstanceName": "ACPI\\ThermalZone\\TZ00_0", "CurrentTemperature": 3232}]
    monkeypatch.setattr(_shell, "run_powershell_json", fake)
    r = temperatures.collect(SystemSnapshot())
    assert r[0].is_cpu is True and r[1].sensor == "acpi-wmi" and r[1].is_cpu is None


def test_psutil_groups_set_cpu_flag(monkeypatch):
    """coretemp -> CPU, nvme -> not CPU, unknown group -> None."""
    Temp = namedtuple("Temp", "label current high critical")
    monkeypatch.setattr(temperatures._shell, "is_windows", lambda: False)
    monkeypatch.setattr(temperatures.psutil, "sensors_temperatures",
                        lambda: {"coretemp": [Temp("Package id 0", 60.0, 80.0, 100.0)],
                                 "nvme": [Temp("Composite", 45.0, 80.0, 90.0)],
                                 "acpitz": [Temp("", 50.0, None, None)]}, raising=False)
    r = temperatures.collect(SystemSnapshot())
    assert [x.is_cpu for x in r] == [True, False, None]


def _thermal(snap):
    """The thermal.cpu finding (or thermal.unknown)."""
    return next(f for f in analyze(snap) if f.id.startswith("thermal.") and f.id != "thermal.cpu_sustained")


def test_rule_uses_confirmed_cpu_sensor():
    """A hot GPU labelled oddly is ignored; the confirmed CPU sensor decides."""
    snap = SystemSnapshot(temperatures=[
        TemperatureReading(label="CPU-ish GPU hotspot", current_c=95.0, sensor="LibreHardwareMonitor", is_cpu=False),
        TemperatureReading(label="CPU Package", current_c=55.0, sensor="LibreHardwareMonitor", is_cpu=True)])
    f = _thermal(snap)
    assert f.id == "thermal.cpu" and f.evidence["cpu_temp_c"] == 55.0
    assert f.evidence["cpu_sensor"] == "confirmed" and f.evidence["sensor_source"] == "LibreHardwareMonitor"


def test_rule_falls_back_to_label_when_unflagged():
    """is_cpu None -> label regex, reported as a guess."""
    snap = SystemSnapshot(temperatures=[TemperatureReading(label="Package id 0", current_c=60.0)])
    f = _thermal(snap)
    assert f.evidence["cpu_sensor"] == "guessed from label"


def test_rule_non_cpu_only_is_unknown():
    """Only non-CPU sensors -> 'couldn't be read', mentioning LibreHardwareMonitor."""
    snap = SystemSnapshot(temperatures=[TemperatureReading(label="CPU Composite", current_c=40.0, is_cpu=False)])
    f = _thermal(snap)
    assert f.id == "thermal.unknown" and "LibreHardwareMonitor" in f.fix


def test_old_snapshot_without_v030_fields_loads():
    """A v0.2.0 snapshot (no device/software/RAM form factor/is_cpu) still loads and plans."""
    import json

    from pcdiag.analysis import recommend_upgrades
    from pcdiag.analysis.plan import build_plan
    from pcdiag.fixtures import ALL_FIXTURES

    d = json.loads(ALL_FIXTURES["slow_old_laptop"]().to_json())
    d.pop("device"), d.pop("software")
    for k in ("max_capacity_gb", "form_factor", "soldered"):
        d["memory"].pop(k, None)
    for t in d["temperatures"]:
        t.pop("is_cpu", None)
    snap = SystemSnapshot.from_json(json.dumps(d))
    assert snap.device is None and snap.software is None and snap.memory.soldered is None
    findings = analyze(snap)
    plan = build_plan(snap, findings, recommend_upgrades(snap, findings))
    assert plan.steps and not any(s.id == "free.power_plan" for s in plan.steps)  # no data -> no claim
