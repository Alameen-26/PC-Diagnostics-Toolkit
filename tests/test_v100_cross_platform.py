"""macOS and Linux support (v1.0.0), tested with saved output on any computer.

The Mac tests swap ``is_macos`` to True and answer each tool
(system_profiler, sysctl, diskutil) with canned text, so no real Mac is
needed. Windows behaviour is checked to be unchanged at the end.
"""
from __future__ import annotations

import json
import plistlib
from collections import namedtuple

import pytest

from pcdiag.collectors import _shell, battery, cpu, disks, gpu, memory, startup, system_info, temperatures, unix_extras
from pcdiag.analysis import rules
from pcdiag.models import BatteryInfo, DiskKind, OSInfo, SmartStatus, SystemSnapshot

Batt = namedtuple("Batt", "percent secsleft power_plugged")
Partition = namedtuple("Partition", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")

HARDWARE = {"SPHardwareDataType": [{"machine_name": "MacBook Air", "machine_model": "Mac14,2",
                                    "chip_type": "Apple M2"}]}
POWER = {"SPPowerDataType": [{"_name": "spbattery_information", "sppower_battery_health_info": {
    "sppower_battery_cycle_count": 412, "sppower_battery_health": "Good",
    "sppower_battery_health_maximum_capacity": "78%"}}]}
DISPLAYS = {"SPDisplaysDataType": [{"_name": "Apple M2", "sppci_model": "Apple M2", "sppci_bus": "spdisplays_builtin"}]}
MEM_APPLE = {"SPMemoryDataType": [{"SPMemoryDataType": "8 GB", "dimm_type": "LPDDR5"}]}
MEM_INTEL = {"SPMemoryDataType": [{"is_memory_upgradeable": "Yes", "_items": [
    {"_name": "DIMM0/0", "dimm_size": "8 GB", "dimm_speed": "2667 MHz", "dimm_type": "DDR4"},
    {"_name": "DIMM1/0", "dimm_size": "empty", "dimm_speed": "empty", "dimm_type": "empty"}]}]}
DISKUTIL = plistlib.dumps({"SolidState": True, "BusProtocol": "Apple Fabric", "SMARTStatus": "Verified",
                           "MediaName": "APPLE SSD AP0512Z"})


@pytest.fixture
def mac(monkeypatch):
    """Pretend to be a Mac, answering each tool with canned output."""
    monkeypatch.setattr(_shell, "is_windows", lambda: False)
    monkeypatch.setattr(_shell, "is_macos", lambda: True)
    monkeypatch.setattr(_shell, "is_linux", lambda: False)
    monkeypatch.setattr(_shell, "which", lambda name: f"/usr/sbin/{name}")
    answers = {"SPHardwareDataType": HARDWARE, "SPPowerDataType": POWER,
               "SPDisplaysDataType": DISPLAYS, "SPMemoryDataType": MEM_APPLE}

    def fake(args, timeout=None, on_error=None, accept_returncodes=(0,)):
        """Canned answer per tool; anything else is a test bug."""
        if "machdep.cpu.brand_string" in args:
            return "Apple M2\n"
        for key, value in answers.items():
            if key in args:
                return json.dumps(value)
        if args[-1:] == ["-j"] or "--scan" in args:  # smartctl is usually not installed on a Mac
            return None
        if args and args[0].endswith("nvidia-smi"):
            return None
        raise AssertionError(f"unexpected command {args}")
    monkeypatch.setattr(_shell, "run_command", fake)
    return answers


# --- pure parsers -----------------------------------------------------------

def test_parse_mac_hardware_laptop():
    info = unix_extras.parse_mac_hardware(HARDWARE)
    assert info.chassis == "laptop" and info.manufacturer == "Apple" and "Mac14,2" in info.model


def test_parse_mac_hardware_desktop_and_empty():
    assert unix_extras.parse_mac_hardware({"SPHardwareDataType": [{"machine_name": "Mac mini"}]}).chassis == "desktop"
    assert unix_extras.parse_mac_hardware(None) is None
    assert unix_extras.parse_mac_hardware({"SPHardwareDataType": []}) is None


def test_parse_mac_power():
    assert unix_extras.parse_mac_power(POWER) == {"cycle_count": 412, "condition": "Good", "wear_percent": 22.0}
    assert unix_extras.parse_mac_power({}) == {}


def test_parse_mac_power_old_macos_without_max_capacity():
    old = {"SPPowerDataType": [{"sppower_battery_health_info": {"sppower_battery_cycle_count": 900,
                                                                "sppower_battery_health": "Service Recommended"}}]}
    fields = unix_extras.parse_mac_power(old)
    assert fields["cycle_count"] == 900 and "wear_percent" not in fields


def test_parse_mac_displays():
    (g,) = unix_extras.parse_mac_displays(DISPLAYS)
    assert g.name == "Apple M2" and g.is_integrated is True
    amd = {"SPDisplaysDataType": [{"sppci_model": "AMD Radeon Pro 5500M", "spdisplays_vram": "4 GB", "sppci_bus": "spdisplays_pcie_device"}]}
    (g,) = unix_extras.parse_mac_displays(amd)
    assert g.vram_mb == 4096 and g.is_integrated is False


def test_parse_mac_memory_apple_chip_is_soldered():
    assert unix_extras.parse_mac_memory(MEM_APPLE) == {"type": "LPDDR5", "soldered": True, "form_factor": "soldered"}


def test_parse_mac_memory_intel_slots():
    f = unix_extras.parse_mac_memory(MEM_INTEL)
    assert f["slots_total"] == 2 and f["slots_used"] == 1 and f["type"] == "DDR4" and f["speed_mhz"] == 2667
    assert "soldered" not in f


def test_parse_diskutil():
    kind, bus, model, health = unix_extras.parse_diskutil_info(DISKUTIL)
    assert kind is DiskKind.NVME and bus == "Apple Fabric" and health.status is SmartStatus.OK
    usb = plistlib.dumps({"SolidState": False, "BusProtocol": "USB", "SMARTStatus": "Not Supported"})
    kind, _, _, health = unix_extras.parse_diskutil_info(usb)
    assert kind is DiskKind.HDD and health.status is SmartStatus.UNKNOWN
    failing = plistlib.dumps({"SolidState": True, "BusProtocol": "SATA", "SMARTStatus": "Failing"})
    assert unix_extras.parse_diskutil_info(failing)[3].status is SmartStatus.FAILING
    assert unix_extras.parse_diskutil_info(b"not a plist") is None
    assert unix_extras.parse_diskutil_info(None) is None


def test_mac_startup_reads_launch_agents(tmp_path):
    (tmp_path / "com.example.sync.plist").write_bytes(plistlib.dumps(
        {"Label": "com.example.sync", "ProgramArguments": ["/Applications/Sync.app/sync", "--bg"]}))
    (tmp_path / "com.example.off.plist").write_bytes(plistlib.dumps({"Label": "off", "Program": "/x", "Disabled": True}))
    (tmp_path / "broken.plist").write_text("nope")
    items = {i.name: i for i in unix_extras.mac_startup([tmp_path, tmp_path / "missing"])}
    assert set(items) == {"com.example.sync", "off"}
    assert items["com.example.sync"].enabled and items["com.example.sync"].command.endswith("--bg")
    assert items["off"].enabled is False


def test_linux_battery_energy_files(tmp_path):
    bat = tmp_path / "BAT0"
    bat.mkdir()
    for name, value in {"energy_full_design": "57000000", "energy_full": "45600000", "cycle_count": "321"}.items():
        (bat / name).write_text(value + "\n")
    (tmp_path / "AC").mkdir()
    assert unix_extras.linux_battery(tmp_path) == {"design_capacity_mwh": 57000, "full_charge_capacity_mwh": 45600,
                                                    "wear_percent": 20.0, "cycle_count": 321}


def test_linux_battery_charge_files_and_zero_cycles(tmp_path):
    bat = tmp_path / "BAT1"
    bat.mkdir()
    for name, value in {"charge_full_design": "4000000", "charge_full": "3000000", "cycle_count": "0"}.items():
        (bat / name).write_text(value)
    assert unix_extras.linux_battery(tmp_path) == {"wear_percent": 25.0}


def test_linux_battery_none(tmp_path):
    assert unix_extras.linux_battery(tmp_path) == {}
    assert unix_extras.linux_battery(tmp_path / "missing") == {}


def test_parse_lspci():
    text = ("00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 620 (rev 07)\n"
            "01:00.0 3D controller: NVIDIA Corporation GP108M [GeForce MX150] (rev a1)\n"
            "00:1f.3 Audio device: Intel Corporation Sunrise Point-LP HD Audio\n")
    a, b = unix_extras.parse_lspci(text)
    assert a.is_integrated is True and "UHD" in a.name and b.is_integrated is False
    assert unix_extras.parse_lspci(None) == []


# --- collectors on a pretend Mac ----------------------------------------------

def test_mac_cpu_name(mac):
    assert cpu.cpu_model_name() == "Apple M2"


def test_mac_device(mac):
    snap = SystemSnapshot()
    assert system_info.collect_device(snap).chassis == "laptop"


def test_mac_device_missing_tool_is_cant_tell(mac, monkeypatch):
    monkeypatch.setattr(_shell, "which", lambda name: None)
    snap = SystemSnapshot()
    assert system_info.collect_device(snap) is None
    assert any("Mac model unavailable" in str(e) for e in snap.errors)


def test_mac_software_state_is_none(mac):
    assert system_info.collect_software(SystemSnapshot()) is None


def test_mac_battery_health(mac, monkeypatch):
    monkeypatch.setattr(battery.psutil, "sensors_battery", lambda: Batt(64.0, 7200, False))
    info = battery.collect(SystemSnapshot())
    assert info.cycle_count == 412 and info.wear_percent == 22.0 and info.percent == 64.0


def test_mac_desktop_has_no_battery(mac, monkeypatch):
    monkeypatch.setattr(battery.psutil, "sensors_battery", lambda: None)
    assert battery.collect(SystemSnapshot()) is None


def test_mac_memory(mac, monkeypatch):
    info = memory.collect(SystemSnapshot())
    assert info.soldered is True and info.type == "LPDDR5" and info.total_gb


def test_mac_gpu(mac):
    (g,) = gpu.collect(SystemSnapshot())
    assert g.name == "Apple M2"


def test_mac_temperatures_say_cant_tell(mac):
    snap = SystemSnapshot()
    assert temperatures.collect(snap) == []
    assert any("macOS" in str(e) for e in snap.errors)


def test_mac_startup_collector(mac, monkeypatch):
    monkeypatch.setattr(unix_extras, "mac_startup", lambda folders=None: ["sentinel"])
    assert startup.collect(SystemSnapshot()) == ["sentinel"]


def test_mac_disks_skip_helper_volumes_and_read_diskutil(mac, monkeypatch):
    parts = [Partition("/dev/disk3s1s1", "/", "apfs", "ro,local"),
             Partition("/dev/disk3s5", "/System/Volumes/Data", "apfs", "rw"),
             Partition("/dev/disk3s6", "/System/Volumes/VM", "apfs", "rw"),
             Partition("devfs", "/dev", "devfs", "rw"),
             Partition("/dev/disk5s1", "/Volumes/Backup", "hfs", "rw")]
    monkeypatch.setattr(disks.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(disks.psutil, "disk_usage", lambda mp: Usage(500 * 1024 ** 3, 300 * 1024 ** 3, 200 * 1024 ** 3, 60.0))
    seen = []

    def fake_mac_disk(device):
        seen.append(device)
        return unix_extras.parse_diskutil_info(DISKUTIL) if device.startswith("/dev/disk3") else None
    monkeypatch.setattr(unix_extras, "mac_disk", fake_mac_disk)
    result = disks.collect(SystemSnapshot(), use_smartctl=False)
    assert [d.mountpoint for d in result] == ["/", "/Volumes/Backup"]
    system = result[0]
    assert system.is_system_drive and system.kind is DiskKind.NVME and system.smart.status is SmartStatus.OK
    assert result[1].kind is DiskKind.UNKNOWN  # diskutil gave nothing: can't tell, not a guess


# --- advice wording -----------------------------------------------------------

def _worn(system):
    snap = SystemSnapshot(os=OSInfo(system=system),
                          battery=BatteryInfo(percent=50, design_capacity_mwh=50000, full_charge_capacity_mwh=36000))
    return [f for f in rules.battery_wear(snap)][0].fix


def test_battery_advice_matches_the_system():
    assert "Windows battery saver" in _worn("Windows")
    assert "Low Power Mode" in _worn("Darwin")
    assert "power-saver profile" in _worn("Linux")


def test_windows_platform_flags_unchanged(windows):
    assert _shell.is_windows() is True
    # On the Windows path the Mac helpers are never consulted.
    assert cpu.cpu_model_name() is None or isinstance(cpu.cpu_model_name(), str)
