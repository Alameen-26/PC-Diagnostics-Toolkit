"""Example snapshots for tests, demos (``pcdiag demo <name>``) and report design.

Each function returns a fresh object, so tests can mutate them freely.
Values are realistic for the hardware described. Benchmark numbers are on
an illustrative scale and are not calibrated against ``pcdiag.benchmark``
(real runs score lower; compare real runs only with real runs).

Added in v0.1.0
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    BatteryInfo, BenchmarkResult, CPUInfo, DiskInfo, DiskIOStats, DiskKind,
    GPUInfo, MemoryInfo, MemoryModule, OSInfo, ProcessInfo, SmartHealth,
    SmartStatus, StartupItem, SystemSnapshot, TemperatureReading,
)

# Fixed timestamp so fixture JSON (and report snapshots) are reproducible.
_TS = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
_RUN = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"


def healthy_gaming_pc() -> SystemSnapshot:
    """Modern desktop with no problems: NVMe, 32 GB DDR5, RTX GPU, no battery."""
    return SystemSnapshot(
        timestamp=_TS,
        os=OSInfo(system="Windows", release="11", version="10.0.22631",
                  architecture="AMD64", hostname="GAMING-RIG", uptime_hours=6.5),
        cpu=CPUInfo(model="AMD Ryzen 7 7800X3D", physical_cores=8, logical_cores=16,
                    base_freq_mhz=4200, current_freq_mhz=4850, max_freq_mhz=5000,
                    total_usage_percent=12.0, per_core_usage_percent=[10.0] * 16,
                    load_avg_1m=1.2, load_avg_5m=1.0, load_avg_15m=0.9),
        memory=MemoryInfo(total_gb=32.0, available_gb=22.4, used_gb=9.6, percent=30.0,
                          swap_total_gb=8.0, swap_used_gb=0.2, swap_percent=2.5,
                          slots_used=2, slots_total=4, speed_mhz=6000, type="DDR5",
                          modules=[MemoryModule(slot="DIMM_A2", capacity_gb=16, speed_mhz=6000, type="DDR5"),
                                   MemoryModule(slot="DIMM_B2", capacity_gb=16, speed_mhz=6000, type="DDR5")]),
        disks=[DiskInfo(device="C:\\", mountpoint="C:\\", fstype="NTFS", total_gb=1863.0,
                        used_gb=620.0, free_gb=1243.0, percent=33.3, kind=DiskKind.NVME,
                        model="Samsung 990 PRO 2TB", is_system_drive=True,
                        smart=SmartHealth(status=SmartStatus.OK, reallocated_sectors=0,
                                          power_on_hours=2100, wear_percent=2.0, temperature_c=41.0))],
        disk_io=[DiskIOStats(device=None, read_mbps=15.0, write_mbps=4.0, busy_percent=3.0, sample_seconds=1.0)],
        gpus=[GPUInfo(name="NVIDIA GeForce RTX 4070", vram_mb=12288, driver_version="566.14",
                      driver_date="2026-08-30", usage_percent=5.0, temperature_c=42.0, is_integrated=False)],
        temperatures=[TemperatureReading(label="CPU", current_c=48.0, high_c=89.0, critical_c=95.0)],
        battery=None,
        top_processes=[ProcessInfo(pid=4120, name="steam.exe", cpu_percent=1.5, memory_mb=420.0, memory_percent=1.3),
                       ProcessInfo(pid=8812, name="Discord.exe", cpu_percent=0.8, memory_mb=380.0, memory_percent=1.2)],
        startup_items=[StartupItem(name="Steam", command="steam.exe -silent", location=_RUN, enabled=True),
                       StartupItem(name="Discord", command="Update.exe --processStart Discord.exe", location=_RUN, enabled=True),
                       StartupItem(name="NVIDIA Overlay", command="NVIDIA Share.exe", location="HKLM Run", enabled=True)],
        benchmark=BenchmarkResult(cpu_single_score=2100, cpu_multi_score=15800,
                                  disk_read_mbps=6800, disk_write_mbps=5200, memory_mbps=48000, duration_seconds=12.0),
    )


def slow_old_laptop() -> SystemSnapshot:
    """Every classic slowdown at once: HDD boot drive 95% full, 4 GB RAM, 25 startup items, worn battery."""
    # 4 named offenders + 21 generic apps = 25 startup items (the spec).
    startup = [StartupItem(name=f"App{i:02d}", command=f"C:\\Program Files\\App{i:02d}\\app.exe /background",
                           location=_RUN if i % 2 else "Startup folder", enabled=True) for i in range(1, 22)]
    startup = [StartupItem(name="OneDrive", command="OneDrive.exe /background", location=_RUN, enabled=True),
               StartupItem(name="Spotify", command="Spotify.exe /minimized", location=_RUN, enabled=True),
               StartupItem(name="Adobe Updater", command="AdobeARM.exe", location="HKLM Run", enabled=True),
               StartupItem(name="McAfee Trial", command="mcuicnt.exe", location="HKLM Run", enabled=True)] + startup
    return SystemSnapshot(
        timestamp=_TS,
        os=OSInfo(system="Windows", release="10", version="10.0.19045",
                  architecture="AMD64", hostname="OLD-LAPTOP", uptime_hours=312.0),
        cpu=CPUInfo(model="Intel Core i3-5005U", physical_cores=2, logical_cores=4,
                    base_freq_mhz=2000, current_freq_mhz=1300, max_freq_mhz=2000,
                    total_usage_percent=88.0, per_core_usage_percent=[92.0, 85.0, 90.0, 85.0],
                    load_avg_1m=5.8, load_avg_5m=5.1, load_avg_15m=4.6),
        memory=MemoryInfo(total_gb=4.0, available_gb=0.3, used_gb=3.7, percent=92.5,
                          swap_total_gb=6.0, swap_used_gb=3.9, swap_percent=65.0,
                          slots_used=1, slots_total=2, speed_mhz=1600, type="DDR3",
                          modules=[MemoryModule(slot="ChannelA-DIMM0", capacity_gb=4, speed_mhz=1600, type="DDR3")]),
        disks=[DiskInfo(device="C:\\", mountpoint="C:\\", fstype="NTFS", total_gb=465.0,
                        used_gb=441.8, free_gb=23.2, percent=95.0, kind=DiskKind.HDD,
                        model="WDC WD5000LPCX", is_system_drive=True,
                        smart=SmartHealth(status=SmartStatus.OK, reallocated_sectors=0,
                                          power_on_hours=21000, temperature_c=44.0))],
        disk_io=[DiskIOStats(device=None, read_mbps=2.1, write_mbps=1.4, busy_percent=99.0, sample_seconds=1.0)],
        gpus=[GPUInfo(name="Intel HD Graphics 5500", vram_mb=128, driver_version="20.19.15.4531",
                      driver_date="2016-09-26", is_integrated=True)],
        temperatures=[TemperatureReading(label="CPU", current_c=82.0, high_c=95.0, critical_c=105.0)],
        battery=BatteryInfo(percent=64.0, plugged_in=True, design_capacity_mwh=41000,
                            full_charge_capacity_mwh=18450, wear_percent=55.0, cycle_count=870),
        top_processes=[ProcessInfo(pid=2212, name="MsMpEng.exe", cpu_percent=35.0, memory_mb=310.0, memory_percent=7.6),
                       ProcessInfo(pid=5120, name="chrome.exe", cpu_percent=28.0, memory_mb=1350.0, memory_percent=33.0),
                       ProcessInfo(pid=6004, name="OneDrive.exe", cpu_percent=9.0, memory_mb=160.0, memory_percent=3.9)],
        startup_items=startup,
        benchmark=BenchmarkResult(cpu_single_score=480, cpu_multi_score=1150,
                                  disk_read_mbps=95, disk_write_mbps=70, memory_mbps=9500, duration_seconds=31.0),
    )


def failing_disk_machine() -> SystemSnapshot:
    """Otherwise fine office PC whose secondary HDD reports SMART failure; temps missing."""
    return SystemSnapshot(
        timestamp=_TS,
        os=OSInfo(system="Windows", release="10", version="10.0.19045",
                  architecture="AMD64", hostname="OFFICE-PC", uptime_hours=48.0),
        cpu=CPUInfo(model="Intel Core i5-8500", physical_cores=6, logical_cores=6,
                    base_freq_mhz=3000, current_freq_mhz=3400, max_freq_mhz=4100,
                    total_usage_percent=20.0, per_core_usage_percent=[20.0] * 6),
        memory=MemoryInfo(total_gb=16.0, available_gb=9.0, used_gb=7.0, percent=43.8,
                          swap_total_gb=2.5, swap_used_gb=0.3, swap_percent=12.0),
        disks=[DiskInfo(device="C:\\", mountpoint="C:\\", fstype="NTFS", total_gb=476.0,
                        used_gb=210.0, free_gb=266.0, percent=44.1, kind=DiskKind.SSD,
                        model="Crucial MX500 500GB", is_system_drive=True,
                        smart=SmartHealth(status=SmartStatus.OK, reallocated_sectors=0,
                                          power_on_hours=14000, wear_percent=18.0, temperature_c=36.0)),
               DiskInfo(device="D:\\", mountpoint="D:\\", fstype="NTFS", total_gb=931.0,
                        used_gb=700.0, free_gb=231.0, percent=75.2, kind=DiskKind.HDD,
                        model="Seagate ST1000DM010", is_system_drive=False,
                        smart=SmartHealth(status=SmartStatus.FAILING, reallocated_sectors=1784,
                                          pending_sectors=96, power_on_hours=41200, temperature_c=52.0,
                                          source="smartctl"))],
        disk_io=[DiskIOStats(device="D:", read_mbps=3.0, write_mbps=0.5, busy_percent=97.0, sample_seconds=1.0)],
        gpus=[GPUInfo(name="Intel UHD Graphics 630", vram_mb=1024, driver_version="31.0.101.2111", is_integrated=True)],
        temperatures=[],  # sensors unavailable (typical on Windows without admin/WMI)
        battery=None,
        top_processes=[ProcessInfo(pid=900, name="SearchIndexer.exe", cpu_percent=6.0, memory_mb=95.0, memory_percent=0.6)],
        startup_items=[StartupItem(name="OneDrive", command="OneDrive.exe /background", location=_RUN, enabled=True)],
        benchmark=None,
        errors=["temperatures: no sensors exposed by psutil on Windows",
                "benchmark: skipped (not requested)"],
    )


ALL_FIXTURES = {
    "healthy_gaming_pc": healthy_gaming_pc,
    "slow_old_laptop": slow_old_laptop,
    "failing_disk_machine": failing_disk_machine,
}
