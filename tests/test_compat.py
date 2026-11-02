"""Upgrade-compatibility checks (pcdiag.analysis.compat).

Added in v0.3.0. The rule under test: never suggest an upgrade that can't
work; when the data can't tell, say so (possible=None) instead of guessing.
"""
from __future__ import annotations

from pcdiag.analysis import compat
from pcdiag.analysis.compat import CANT_TELL, NO, YES
from pcdiag.fixtures import failing_disk_machine, healthy_gaming_pc, slow_old_laptop
from pcdiag.models import BatteryInfo, DeviceInfo, DiskKind, MemoryModule


def test_slow_laptop_ram_fits_free_sodimm_slot():
    """4 GB in one of two SODIMM slots, board max 16 GB -> one DDR3L stick for the free slot."""
    opt = compat.ram_upgrade(slow_old_laptop())
    assert opt.possible is True and opt.needed
    assert "SODIMM" in opt.part and "DDR3L" in opt.part and "free slot" in opt.part
    assert opt.cost_low and opt.cost_high and opt.cost_text.startswith("$")
    assert any(c.status == YES for c in opt.checks)


def test_ram_soldered_and_full_is_impossible():
    """Soldered RAM with no free slot: possible=False, never a shopping suggestion."""
    s = slow_old_laptop()
    s.memory.soldered, s.memory.form_factor = True, "soldered"
    s.memory.slots_used = s.memory.slots_total = None
    s.memory.modules = [MemoryModule(capacity_gb=4, type="LPDDR3", form_factor="soldered")]
    opt = compat.ram_upgrade(s)
    assert opt.possible is False
    assert any(c.status == NO for c in opt.checks)


def test_ram_at_board_max_is_impossible():
    """Already at the firmware-reported maximum -> no RAM upgrade."""
    s = slow_old_laptop()
    s.memory.max_capacity_gb = 4
    assert compat.ram_upgrade(s).possible is False


def test_ram_unknown_slots_is_cant_tell():
    """No slot or form-factor data -> can't tell (possible None), with a how-to-check."""
    s = failing_disk_machine()
    s.memory.total_gb = 4
    opt = compat.ram_upgrade(s)
    assert opt.possible is None
    assert opt.how_to_check
    assert any(c.status == CANT_TELL for c in opt.checks)


def test_ram_enough_is_not_needed():
    """32 GB already: fits but not needed."""
    opt = compat.ram_upgrade(healthy_gaming_pc())
    assert opt.needed is False


def test_ram_full_slots_suggests_replacement_kit():
    """Both slots used and below target -> a replacement kit, not 'add a stick'."""
    s = slow_old_laptop()
    s.memory.modules = [MemoryModule(slot="A", capacity_gb=2, type="DDR3", speed_mhz=1600, form_factor="SODIMM"),
                        MemoryModule(slot="B", capacity_gb=2, type="DDR3", speed_mhz=1600, form_factor="SODIMM")]
    s.memory.slots_used, s.memory.slots_total = 2, 2
    opt = compat.ram_upgrade(s)
    assert opt.possible is True
    assert "kit" in opt.part and "free slot" not in opt.part


def test_ssd_for_sata_hdd_is_sata_ssd_sized_for_data():
    """SATA hard disk -> 2.5" SATA SSD with room for the data in use (441.8 GB * 1.25 -> 1 TB)."""
    opt = compat.ssd_upgrade(slow_old_laptop())
    assert opt.possible is True and opt.needed
    assert "SATA SSD" in opt.part and "1 TB" in opt.part
    connector = next(c for c in opt.checks if c.item == "Connector")
    assert connector.status == YES


def test_ssd_m2_slot_is_always_cant_tell():
    """Windows can't report a free M.2 slot, so that check always says can't tell."""
    opt = compat.ssd_upgrade(slow_old_laptop())
    m2 = [c for c in opt.checks if "M.2" in c.item]
    assert m2 and all(c.status == CANT_TELL for c in m2)


def test_ssd_already_ssd_not_needed():
    """NVMe system drive -> no SSD suggestion."""
    assert compat.ssd_upgrade(healthy_gaming_pc()).needed is False


def test_ssd_unknown_bus_is_cant_tell():
    """Unknown or USB bus -> can't tell which SSD fits."""
    for bus in (None, "USB"):
        s = slow_old_laptop()
        s.system_drive.bus_type = bus
        assert compat.ssd_upgrade(s).possible is None


def test_replace_disk_same_interface():
    """A failing SATA HDD data drive -> replacement on the same connector."""
    s = failing_disk_machine()
    d = next(x for x in s.disks if x.mountpoint.startswith("D"))
    opt = compat.replace_disk(s, d)
    assert "SATA" in opt.part and opt.cost_text.startswith("$")


def test_battery_desktop_impossible_laptop_ok():
    """No battery on a desktop -> impossible; worn laptop battery -> possible."""
    assert compat.battery_option(failing_disk_machine()).possible is False
    assert compat.battery_option(slow_old_laptop()).possible is True


def test_battery_laptop_without_battery_is_cant_tell():
    """A laptop chassis but no battery detected -> can't tell (it may be removed or unreadable)."""
    s = slow_old_laptop()
    s.battery = None
    assert compat.battery_option(s).possible is None


def test_unknown_chassis_with_battery_counts_as_laptop():
    """No chassis info but a battery present -> treated as a laptop."""
    s = slow_old_laptop()
    s.device = None
    assert compat._is_laptop(s) is True
    s.battery = None
    assert compat._is_laptop(s) is None


def test_desktop_chassis_beats_missing_battery():
    """Desktop chassis + no battery -> desktop, not 'can't tell'."""
    s = healthy_gaming_pc()
    s.battery = None
    s.device = DeviceInfo(chassis="desktop", chassis_types=[3])
    assert compat._is_laptop(s) is False


def test_cost_text_price_varies_when_unknown():
    """No price range -> 'price varies'."""
    assert compat.UpgradeOption(kind="x").cost_text == "price varies"


def test_ssd_kind_hdd_required():
    """Only hard disks get an SSD suggestion."""
    s = slow_old_laptop()
    s.system_drive.kind = DiskKind.SSD
    assert compat.ssd_upgrade(s).needed is False
