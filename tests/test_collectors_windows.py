"""Windows parsing logic, tested on any OS with canned PowerShell/registry/powercfg output.

Added in v0.1.0
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import DATA, GiB, Partition, Usage, load_json
from pcdiag.collectors import battery, cpu, disks, gpu, memory, os_info, startup, temperatures
from pcdiag.models import DiskKind, SmartHealth, SmartStatus, SystemSnapshot

# ---------------------------------------------------------------- memory ---

MEM_JSON = {
    "modules": [
        {"BankLabel": "BANK 0", "DeviceLocator": "ChannelA-DIMM0", "Capacity": 8 * GiB, "Speed": 3200,
         "ConfiguredClockSpeed": 2667, "SMBIOSMemoryType": 26, "Manufacturer": "Samsung "},
        {"BankLabel": "BANK 2", "DeviceLocator": "ChannelB-DIMM0", "Capacity": 8 * GiB, "Speed": 3200,
         "ConfiguredClockSpeed": 3200, "SMBIOSMemoryType": 26, "Manufacturer": "Samsung"},
    ],
    "arrays": {"MemoryDevices": 4, "MaxCapacity": 67108864, "Use": 3},  # single object, not a list
}


def _fake_vm(monkeypatch):
    """Stub psutil memory calls in the memory collector."""
    monkeypatch.setattr(memory.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16 * GiB, available=6 * GiB, percent=62.5))
    monkeypatch.setattr(memory.psutil, "swap_memory",
                        lambda: SimpleNamespace(total=4 * GiB, used=1 * GiB, percent=25.0))


def test_parse_windows_memory_modules_and_slots():
    """Parse windows memory modules and slots."""
    f = memory.parse_windows_memory(MEM_JSON)
    assert f["slots_used"] == 2 and f["slots_total"] == 4
    assert f["type"] == "DDR4"
    assert f["speed_mhz"] == 2667  # slowest configured speed wins
    assert f["modules"][0].capacity_gb == 8.0 and f["modules"][0].manufacturer == "Samsung"


def test_memory_collect_windows(windows, ps_json, monkeypatch):
    """Memory collect windows."""
    _fake_vm(monkeypatch)
    ps_json(MEM_JSON)
    snap = SystemSnapshot()
    info = memory.collect(snap)
    assert info.total_gb == 16.0 and info.used_gb == 10.0 and info.swap_used_gb == 1.0
    assert info.slots_total == 4 and len(info.modules) == 2
    assert snap.errors == []


def test_memory_collect_windows_powershell_failure(windows, ps_json, monkeypatch):
    """Memory collect windows powershell failure."""
    _fake_vm(monkeypatch)
    ps_json(None, error="powershell timed out after 20s")
    snap = SystemSnapshot()
    info = memory.collect(snap)
    assert info.total_gb == 16.0 and info.modules == []
    assert snap.errors and "timed out" in snap.errors[0]


# ----------------------------------------------------------------- disks ---

def test_parse_windows_disks_maps_letters_and_health():
    """Parse windows disks maps letters and health."""
    parsed = disks.parse_windows_disks(load_json("get_physical_disk.json"))
    kind, bus, model, health = parsed["C"]
    assert kind is DiskKind.NVME and bus == "NVMe" and model.startswith("Samsung")
    assert health.status is SmartStatus.OK and health.wear_percent == 3 and health.power_on_hours == 1520
    kind, bus, _, health = parsed["D"]
    assert kind is DiskKind.HDD and bus == "SATA"
    assert health.status is SmartStatus.FAILING and health.read_errors == 37
    assert health.wear_percent is None  # HDD "Wear" is meaningless


def test_parse_physical_disk_numeric_enums_and_zero_temp():
    """Parse physical disk numeric enums and zero temp."""
    kind, bus, _, health = disks.parse_physical_disk(
        {"MediaType": 4, "BusType": 11, "HealthStatus": 1, "Temperature": 0})
    assert kind is DiskKind.SSD and bus == "SATA"
    assert health.status is SmartStatus.WARNING and health.temperature_c is None


def _fake_partitions(monkeypatch, *parts):
    """Stub psutil disk calls in the disks collector."""
    monkeypatch.setattr(disks.psutil, "disk_partitions", lambda all=False: list(parts))
    monkeypatch.setattr(disks.psutil, "disk_usage", lambda mp: Usage(1000 * GiB, 950 * GiB, 50 * GiB, 95.0))


def test_disks_collect_windows(windows, ps_json, monkeypatch):
    """Disks collect windows."""
    monkeypatch.setenv("SystemDrive", "C:")
    _fake_partitions(monkeypatch,
                     Partition("C:\\", "C:\\", "NTFS", "rw,fixed"),
                     Partition("D:\\", "D:\\", "NTFS", "rw,fixed"),
                     Partition("E:\\", "E:\\", "", "cdrom"))  # empty DVD drive: skipped
    ps_json(load_json("get_physical_disk.json"))
    snap = SystemSnapshot()
    result = disks.collect(snap)
    assert [d.mountpoint for d in result] == ["C:\\", "D:\\"]
    c, d = result
    assert c.is_system_drive and not d.is_system_drive
    assert c.kind is DiskKind.NVME and c.bus_type == "NVMe" and c.percent == 95.0
    assert d.smart.status is SmartStatus.FAILING
    assert snap.errors == []


def test_disks_collect_windows_without_admin_notes_counters(windows, ps_json, monkeypatch):
    """Disks collect windows without admin notes counters."""
    _fake_partitions(monkeypatch, Partition("C:\\", "C:\\", "NTFS", "rw,fixed"))
    ps_json({"disks": {"DeviceId": "0", "FriendlyName": "WDC WD5000LPCX", "MediaType": "HDD",
                       "BusType": "SATA", "HealthStatus": "Healthy"},  # counters all null
             "partitions": {"DriveLetter": "C", "DiskNumber": 0}})
    snap = SystemSnapshot()
    (c,) = disks.collect(snap)
    assert c.kind is DiskKind.HDD and c.smart.status is SmartStatus.OK
    assert any("Administrator" in e for e in snap.errors)


def test_parse_smartctl_flags_reallocated_sectors():
    """Parse smartctl flags reallocated sectors."""
    model, protocol, health = disks.parse_smartctl(load_json("smartctl_ata.json"))
    assert model == "ST1000DM010-2EP102" and protocol == "ATA"
    assert health.reallocated_sectors == 1784 and health.pending_sectors == 96
    assert health.status is SmartStatus.WARNING  # "passed" but sectors remapped
    assert health.power_on_hours == 41200 and health.temperature_c == 52


def test_merge_health_worst_status_wins_and_fills_gaps():
    """Merge health worst status wins and fills gaps."""
    base = SmartHealth(status=SmartStatus.OK, power_on_hours=10, source="Get-PhysicalDisk")
    merged = disks.merge_health(base, SmartHealth(status=SmartStatus.FAILING, power_on_hours=99,
                                                  reallocated_sectors=5, source="smartctl"))
    assert merged.status is SmartStatus.FAILING
    assert merged.power_on_hours == 10 and merged.reallocated_sectors == 5
    assert merged.source == "Get-PhysicalDisk+smartctl"


def test_disks_smartctl_matched_by_model_on_windows(windows, ps_json, monkeypatch):
    """Disks smartctl matched by model on windows."""
    import json as _json

    _fake_partitions(monkeypatch, Partition("D:\\", "D:\\", "NTFS", "rw,fixed"))
    ps_json(load_json("get_physical_disk.json"))
    monkeypatch.setattr(disks._shell, "which", lambda name: "smartctl" if name == "smartctl" else None)
    outputs = {"--scan": _json.dumps({"devices": [{"name": "/dev/sdb", "type": "sat"}]}),
               "-a": (DATA / "smartctl_ata.json").read_text()}
    monkeypatch.setattr(disks._shell, "run_command", lambda args, **k: outputs[args[1]])
    (d,) = disks.collect(SystemSnapshot())
    assert d.smart.status is SmartStatus.FAILING  # Windows said Unhealthy; worst wins
    assert d.smart.reallocated_sectors == 1784 and d.smart.source == "Get-PhysicalDisk+smartctl"


# ------------------------------------------------------------------- gpu ---

def test_parse_win32_video():
    """Parse win32 video."""
    gpus = gpu.parse_win32_video([
        {"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 4293918720, "DriverVersion": "32.0.15.6614",
         "DriverDate": "2026-08-30"},
        {"Name": "Intel(R) UHD Graphics 630", "AdapterRAM": 1073741824, "DriverVersion": "31.0.101.2111",
         "DriverDate": "2022-06-01"},
        {"Name": "Microsoft Basic Display Adapter", "AdapterRAM": 0},
    ])
    assert [g.name for g in gpus] == ["NVIDIA GeForce RTX 3060", "Intel(R) UHD Graphics 630"]
    assert gpus[0].vram_mb == 4095 and gpus[0].is_integrated is False  # 32-bit capped value
    assert gpus[1].vram_mb == 1024 and gpus[1].is_integrated is True and gpus[1].driver_date == "2022-06-01"


def test_gpu_collect_windows_merges_nvidia_smi(windows, ps_json, monkeypatch):
    """Gpu collect windows merges nvidia smi."""
    ps_json({"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 4293918720, "DriverVersion": "32.0.15.6614",
             "DriverDate": "2026-08-30"})
    monkeypatch.setattr(gpu._shell, "which", lambda name: "nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(gpu._shell, "run_command",
                        lambda args, **k: "NVIDIA GeForce RTX 3060, 12288, 566.14, 7, [N/A]\n")
    (g,) = gpu.collect(SystemSnapshot())
    assert g.vram_mb == 12288 and g.driver_version == "566.14" and g.driver_date == "2026-08-30"
    assert g.usage_percent == 7.0 and g.temperature_c is None


def test_guess_integrated():
    """Guess integrated."""
    assert gpu.guess_integrated("Intel(R) Arc(TM) A770 Graphics") is False
    assert gpu.guess_integrated("AMD Radeon(TM) Graphics") is True
    assert gpu.guess_integrated("AMD Radeon RX 6700 XT") is False
    assert gpu.guess_integrated("Matrox G200") is None


# ---------------------------------------------------------- temperatures ---

def test_thermal_zones_parse_and_convert():
    """Thermal zones parse and convert."""
    readings = temperatures.parse_thermal_zones([
        {"InstanceName": "ACPI\\ThermalZone\\TZ00_0", "CurrentTemperature": 3232, "CriticalTripPoint": 3732},
        {"InstanceName": "ACPI\\ThermalZone\\TZ01_0", "CurrentTemperature": 0},
    ])
    assert len(readings) == 1
    # tenths of Kelvin: 3232 -> 323.2 K -> 50.05 C
    assert readings[0].current_c == pytest.approx(50.05, abs=0.06)
    assert readings[0].critical_c == pytest.approx(100.05, abs=0.06)
    assert readings[0].label == "ACPI TZ00_0"


def test_temperatures_windows_unavailable_is_explained(windows, ps_json):
    """Temperatures windows unavailable is explained."""
    ps_json(None, error="Access denied")
    snap = SystemSnapshot()
    assert temperatures.collect(snap) == []
    assert "Administrator" in snap.errors[0]


# --------------------------------------------------------------- battery ---

def test_parse_battery_report_sample():
    """Parse battery report sample."""
    report = battery.parse_battery_report((DATA / "batteryreport.xml").read_text(encoding="utf-8"))
    assert report == {"design_capacity_mwh": 41000, "full_charge_capacity_mwh": 18450, "cycle_count": 870}
    assert battery.wear_percent(41000, 18450) == 55.0
    assert battery.wear_percent(41000, 42000) == 0.0  # new battery above design: clamped


def test_parse_battery_report_no_battery_and_garbage():
    """Parse battery report no battery and garbage."""
    assert battery.parse_battery_report("<BatteryReport><Batteries/></BatteryReport>") is None
    assert battery.parse_battery_report("not xml") is None


def test_battery_collect_windows_runs_powercfg_in_temp_dir(windows, monkeypatch):
    """Battery collect windows runs powercfg in temp dir."""
    monkeypatch.setattr(battery.psutil, "sensors_battery",
                        lambda: SimpleNamespace(percent=64.0, power_plugged=True, secsleft=-2))
    seen = {}

    def fake_powercfg(args, **kwargs):
        """Pretend to be powercfg: write the sample report where asked."""
        out = args[args.index("/output") + 1]
        seen["path"] = out
        with open(out, "w", encoding="utf-8-sig") as fh:
            fh.write((DATA / "batteryreport.xml").read_text(encoding="utf-8"))
        return ""

    monkeypatch.setattr(battery._shell, "run_command", fake_powercfg)
    snap = SystemSnapshot()
    info = battery.collect(snap)
    assert info.wear_percent == 55.0 and info.cycle_count == 870 and info.seconds_left is None
    import os
    assert not os.path.exists(seen["path"])  # temp dir cleaned up
    assert snap.errors == []


def test_battery_none_on_desktop(monkeypatch):
    """Battery none on desktop."""
    monkeypatch.setattr(battery.psutil, "sensors_battery", lambda: None)
    snap = SystemSnapshot()
    assert battery.collect(snap) is None and snap.errors == []


# --------------------------------------------------------------- startup ---

REGISTRY = {
    ("HKCU", startup._RUN): {"OneDrive": '"C:\\OneDrive.exe" /background', "Spotify": "Spotify.exe /minimized", "": "x"},
    ("HKCU", startup._APPROVED + "\\Run"): {"Spotify": bytes([3]) + bytes(11)},  # disabled in Task Manager
    ("HKLM", startup._RUN): {"SecurityHealth": "%windir%\\system32\\SecurityHealthSystray.exe"},
    ("HKLM", startup._RUN32): {"Adobe ARM": "AdobeARM.exe"},
    ("HKLM", startup._APPROVED + "\\Run32"): {"Adobe ARM": bytes([2]) + bytes(11)},
    ("HKCU", startup._APPROVED + "\\StartupFolder"): {"Old Tool.lnk": bytes([3]) + bytes(11)},
}


def test_startup_windows_registry_and_folders(tmp_path):
    """Startup windows registry and folders."""
    (tmp_path / "Old Tool.lnk").write_text("")
    (tmp_path / "Notes.lnk").write_text("")
    (tmp_path / "desktop.ini").write_text("")
    items = startup.collect_windows(reader=lambda hive, path: REGISTRY.get((hive, path), {}),
                                    startup_dirs=[("HKCU", tmp_path)])
    by_name = {i.name: i for i in items}
    assert set(by_name) == {"OneDrive", "Spotify", "SecurityHealth", "Adobe ARM", "Old Tool", "Notes"}
    assert by_name["Spotify"].enabled is False and by_name["OneDrive"].enabled is True
    assert by_name["Adobe ARM"].enabled is True and "WOW6432Node" in by_name["Adobe ARM"].location
    assert by_name["Old Tool"].enabled is False and by_name["Notes"].enabled is True


def test_startup_approved_decoding():
    """Startup approved decoding."""
    assert startup.is_approved_enabled(b"\x02" + bytes(11)) is True
    assert startup.is_approved_enabled(b"\x06" + bytes(11)) is True
    assert startup.is_approved_enabled(b"\x03" + bytes(11)) is False
    assert startup.is_approved_enabled(None) is None


# ------------------------------------------------------------ os and cpu ---

def test_windows_11_detection():
    """Windows 11 detection."""
    assert os_info._windows_release("10", "10.0.22631") == "11"
    assert os_info._windows_release("10", "10.0.19045") == "10"


def test_cpu_windows_load_average_zero_is_unknown(windows, monkeypatch):
    """Cpu windows load average zero is unknown."""
    monkeypatch.setattr(cpu, "cpu_model_name", lambda: "Intel(R) Core(TM) i5-8250U")
    monkeypatch.setattr(cpu.psutil, "cpu_count", lambda logical=True: 8 if logical else 4)
    monkeypatch.setattr(cpu.psutil, "cpu_freq", lambda: SimpleNamespace(current=1800.0, min=0.0, max=1800.0))
    monkeypatch.setattr(cpu.psutil, "cpu_percent", lambda interval=None, percpu=False: [10.0, 30.0] * 4)
    monkeypatch.setattr(cpu.psutil, "getloadavg", lambda: (0.0, 0.0, 0.0))
    info = cpu.collect(SystemSnapshot(), interval=0)
    assert info.total_usage_percent == 20.0 and info.physical_cores == 4
    assert info.base_freq_mhz == 1800 and info.max_freq_mhz is None
    assert info.load_avg_1m is None
