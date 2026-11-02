# pcdiag/analysis/compat.py
# Added in v0.3.0 (roadmap phase 5, item A: upgrade compatibility).
# Area: analysis / recommender.
"""Upgrade compatibility: is an upgrade possible on *this* PC, and which part fits?

The improvement plan (``pcdiag.analysis.plan``) asks these helpers before it
suggests buying anything. Each returns an :class:`UpgradeOption` with:

* ``possible``: True (it fits), False (it can't be done: never suggested), or
  None ("can't tell": the plan says so and tells the user how to check).
* ``checks``: the individual facts, each "yes" / "no" / "can't tell", with the
  evidence in plain words (soldered vs socketed RAM, free slots, the board's
  maximum RAM, stick form factor, drive interface, laptop vs desktop, ...).
* ``part``: what to buy, written so it matches the existing hardware.
* ``cost_low`` / ``cost_high``: a rough USD price range (2026), never a quote.

House rules: missing data is "can't tell", never "fine"; an upgrade that is
known to be impossible is never suggested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import DiskInfo, DiskKind, Model, SystemSnapshot

# Explained in v0.3.0: compatibility is three-valued on purpose: yes / no / can't tell.
# Windows often doesn't report things like an empty M.2 slot. Treating
# "unknown" as "yes" would recommend parts that may not fit, and treating it as
# "no" would hide useful upgrades, so the plan says "can't tell" and explains
# how to check. Prices are static ranges, not live quotes.
YES, NO, CANT_TELL = "yes", "no", "can't tell"

#: RAM target: 16 GB is comfortable for Windows 10/11 with a browser; 8 GB is the floor.
RAM_TARGET_GB = 16
RAM_MIN_GB = 8
#: Rough USD price per 8 GB of RAM by generation (2026 street prices; RAM prices swing, so wide ranges).
RAM_PRICE_PER_8GB = {"DDR3": (15, 30), "DDR4": (20, 40), "DDR5": (30, 55)}
_RAM_PRICE_DEFAULT = (20, 50)
#: Rough USD SSD prices by capacity (GB) and interface.
# Changed in v0.3.0 (review): 4 TB SSDs and 8/16 TB hard disks added, so a failing
# big drive is never offered a smaller replacement than what it holds.
SSD_PRICES = {
    "SATA": {500: (35, 55), 1000: (55, 90), 2000: (100, 160), 4000: (200, 320)},
    "NVMe": {500: (35, 60), 1000: (55, 100), 2000: (100, 180), 4000: (200, 350)},
}
#: Replacement data drive (HDD) prices, for big data disks.
HDD_PRICES = {1000: (45, 65), 2000: (55, 80), 4000: (80, 120), 8000: (140, 220), 16000: (260, 380)}
#: Drive sizes we suggest, smallest first. Added in v0.3.0 (review).
DRIVE_SIZES_GB = (500, 1000, 2000, 4000, 8000, 16000)
BATTERY_PRICE = (35, 90)
#: Headroom when sizing a new drive: room for what's there now plus 25%.
DRIVE_HEADROOM = 1.25
SOLDERED_TYPES = {"LPDDR", "LPDDR2", "LPDDR3", "LPDDR4", "LPDDR5"}


@dataclass
class CompatCheck(Model):
    """One compatibility fact: ``status`` is "yes", "no" or "can't tell"."""
    item: str = ""
    status: str = CANT_TELL
    detail: str = ""


@dataclass
class UpgradeOption(Model):
    """Can this upgrade be done here, and with which part?"""
    kind: str = ""                    # "ram", "ssd", "replace_disk", "battery"
    possible: Optional[bool] = None   # None = can't tell
    needed: bool = True               # False = it would fit but wouldn't help (e.g. already an SSD)
    summary: str = ""                 # one plain sentence
    part: Optional[str] = None        # what to buy, matched to the PC
    cost_low: Optional[int] = None    # USD
    cost_high: Optional[int] = None
    checks: list[CompatCheck] = field(default_factory=list)
    how_to_check: Optional[str] = None  # when possible is None: how the user can find out

    @property
    def cost_text(self) -> str:
        """'$50-90', or 'price varies' when unknown."""
        if self.cost_low is None or self.cost_high is None:
            return "price varies"
        return f"${self.cost_low}-{self.cost_high}"


def _is_laptop(s: SystemSnapshot) -> Optional[bool]:
    """True/False from the chassis type; falls back to "a battery is present" -> laptop."""
    if s.device is not None and s.device.is_laptop is not None:
        return s.device.is_laptop
    if s.battery is not None:
        return True
    return None


def _pow2_floor(x: float) -> int:
    """Largest power of two <= x (min 1)."""
    p = 1
    while p * 2 <= x:
        p *= 2
    return p


def _ram_cost(mem_type: Optional[str], gb: float) -> tuple[int, int]:
    lo, hi = RAM_PRICE_PER_8GB.get(mem_type or "", _RAM_PRICE_DEFAULT)
    k = max(gb, 4) / 8
    return int(round(lo * k)), int(round(hi * k))


def ram_soldered(s: SystemSnapshot) -> Optional[bool]:
    """True = soldered (no upgrade), False = socketed, None = can't tell."""
    m = s.memory
    if m is None:
        return None
    if m.type in SOLDERED_TYPES or any(mod.type in SOLDERED_TYPES for mod in m.modules):
        return True
    if m.soldered is not None:
        return m.soldered
    factors = {mod.form_factor for mod in m.modules if mod.form_factor}
    if factors == {"soldered"}:
        return True
    if factors and "soldered" not in factors:
        return False
    return None


def ram_upgrade(s: SystemSnapshot, target_gb: int = RAM_TARGET_GB) -> UpgradeOption:
    """Can RAM be added, and what to buy? Never suggests a stick that can't fit."""
    m = s.memory
    opt = UpgradeOption(kind="ram")
    if m is None or not m.total_gb:
        opt.summary = "Can't tell: the amount of RAM couldn't be read."
        opt.how_to_check = "Open Task Manager > Performance > Memory to see installed RAM and slots used."
        return opt
    total = round(m.total_gb)
    laptop = _is_laptop(s)

    soldered = ram_soldered(s)
    if soldered is True:
        opt.checks.append(CompatCheck("Socketed RAM", NO, "the memory is soldered to the motherboard"
                                      + (f" ({m.type})" if m.type else "")))
    elif soldered is False:
        ff = m.form_factor or next((mod.form_factor for mod in m.modules if mod.form_factor), None)
        opt.checks.append(CompatCheck("Socketed RAM", YES, f"removable {ff or 'stick'} modules"))
    else:
        opt.checks.append(CompatCheck("Socketed RAM", CANT_TELL, "the form factor wasn't reported"))

    free = None
    if m.slots_total and m.slots_used is not None:
        free = max(0, m.slots_total - m.slots_used)
        opt.checks.append(CompatCheck("Free RAM slot", YES if free else NO,
                                      f"{m.slots_used} of {m.slots_total} slots used"))
    else:
        opt.checks.append(CompatCheck("Free RAM slot", CANT_TELL, "slot count wasn't reported"))

    max_gb = m.max_capacity_gb
    if max_gb:
        opt.checks.append(CompatCheck("Room below the maximum", YES if max_gb > total else NO,
                                      f"board reports up to {max_gb:g} GB; {total} GB installed"))
    else:
        opt.checks.append(CompatCheck("Room below the maximum", CANT_TELL, "maximum RAM wasn't reported"))

    form = (m.form_factor if m.form_factor not in (None, "soldered", "mixed") else None) \
        or next((mod.form_factor for mod in m.modules if mod.form_factor not in (None, "soldered")), None)
    if form is None and laptop is not None:
        form_guess = "SODIMM (laptop size)" if laptop else "DIMM (desktop size)"
        opt.checks.append(CompatCheck("Stick size", CANT_TELL, f"probably {form_guess}; check before buying"))
    else:
        opt.checks.append(CompatCheck("Stick size", YES if form else CANT_TELL,
                                      f"{form} ({'laptop' if form == 'SODIMM' else 'desktop'} size)" if form
                                      else "not reported"))

    # Impossible cases first: never suggest these.
    if soldered is True and (free == 0 or free is None):
        opt.possible = False
        opt.summary = (f"RAM can't be upgraded: the {total} GB is soldered to the motherboard. "
                       "Free fixes are the way to make it go further.")
        return opt
    if max_gb and total >= max_gb:
        opt.possible = False
        opt.summary = f"RAM is already at the maximum this board reports ({max_gb:g} GB)."
        return opt

    goal = target_gb if not max_gb else int(min(target_gb, _pow2_floor(max_gb)))
    if total >= goal:
        opt.needed = False
        opt.summary = f"{total} GB is already enough for typical use; more RAM wouldn't help much."
        return opt

    spec = " ".join(x for x in (m.type, f"{m.speed_mhz} MHz" if m.speed_mhz else None,
                                form or ("SODIMM" if laptop else "DIMM" if laptop is False else None)) if x)
    spec = spec or "matching"
    if m.type == "DDR3" and laptop:
        # Laptops from about 2013 on (Haswell/Broadwell and later) need low-voltage DDR3L sticks.
        spec = spec.replace("DDR3", "DDR3L (1.35 V)", 1)
    if free:
        add = _pow2_floor(goal - total)
        add = max(add, 4)
        if max_gb:
            add = min(add, int(max_gb - total)) or add
        new_total = total + add
        lo, hi = _ram_cost(m.type, add)
        opt.part = f"1 × {add} GB {spec} stick for the free slot ({total} → {new_total} GB)"
        if (m.slots_total or 0) >= 2 and new_total < goal:
            klo, khi = _ram_cost(m.type, goal)
            opt.part += f"; or a 2 × {goal // 2} GB kit to replace what's there ({goal} GB, ${klo}-{khi})"
        opt.cost_low, opt.cost_high = lo, hi
    elif free == 0 and m.slots_total:
        # Changed in v0.3.0 (review): sticks come in power-of-two sizes, so 3 slots
        # and a 16 GB goal is 3 x 4 GB (12 GB), never "3 x 5 GB".
        per = max(1, _pow2_floor(goal / m.slots_total))
        if per * m.slots_total < goal and per * 2 * m.slots_total <= (max_gb or per * 2 * m.slots_total):
            per *= 2  # round up rather than fall short of the goal, if the board allows it
        goal = per * m.slots_total
        lo, hi = _ram_cost(m.type, goal)
        opt.part = f"{m.slots_total} × {per} GB {spec} kit replacing the current sticks ({total} → {goal} GB)"
        opt.cost_low, opt.cost_high = lo, hi
    else:
        lo, hi = _ram_cost(m.type, goal - total)
        opt.part = f"{spec} RAM up to {goal} GB"
        opt.cost_low, opt.cost_high = lo, hi

    unknowns = [c.item for c in opt.checks if c.status == CANT_TELL and c.item != "Stick size"]
    if soldered is None or free is None:
        opt.possible = None
        opt.summary = (f"Probably possible, but can't tell for sure ({', '.join(unknowns).lower()} unknown). "
                       f"Check before buying.")
        opt.how_to_check = ("Look up your exact model on the maker's support site, or use the Crucial System "
                            "Scanner / Kingston configurator, to confirm free slots and soldered memory.")
    else:
        opt.possible = True
        opt.summary = f"RAM can be upgraded: {opt.part}."
        if max_gb is None:
            opt.how_to_check = "The board's maximum wasn't reported; confirm it on the maker's support site."
    return opt


def _drive_size_gb(used_gb: Optional[float], current_gb: Optional[float]) -> int:
    """Smallest standard size that holds what's there now (+25%) and is no smaller than the old drive.

    Changed in v0.3.0 (review): goes up to 16 TB (was capped at 4 TB, so a failing
    8 TB drive was offered a 4 TB replacement). Drives sold as "8 TB" report a bit
    less (e.g. 7452 GB), so a 2% allowance is used when matching the current size.
    """
    need = max((used_gb or 0) * DRIVE_HEADROOM, (current_gb or 0) * 0.98, 250)
    for size in DRIVE_SIZES_GB:
        if need <= size:
            return size
    return DRIVE_SIZES_GB[-1]


def _ssd_price(iface: str, size: int) -> tuple[Optional[int], Optional[int]]:
    """SSD price range, or (None, None) for sizes we have no price for. Added in v0.3.0 (review)."""
    return SSD_PRICES[iface].get(size, (None, None))


def _size_text(gb: int) -> str:
    return f"{gb // 1000} TB" if gb >= 1000 else f"{gb} GB"


def ssd_upgrade(s: SystemSnapshot, disk: Optional[DiskInfo] = None) -> UpgradeOption:
    """HDD -> SSD for the system drive (or ``disk``): which interface, which size."""
    d = disk or s.system_drive
    opt = UpgradeOption(kind="ssd")
    if d is None:
        opt.summary = "Can't tell: the system drive wasn't identified."
        opt.how_to_check = "Open Task Manager > Performance > Disk 0 to see whether it is an HDD or SSD."
        return opt
    # Changed in v0.3.0 (review): an NVMe connection means it is already an SSD even
    # when the drive type itself wasn't reported (hard disks never use NVMe).
    if d.kind in (DiskKind.SSD, DiskKind.NVME) or (d.bus_type or "").strip().upper() == "NVME":
        opt.needed = False
        opt.summary = f"{d.mountpoint or d.device} is already an SSD; no speed-up from swapping it."
        opt.checks.append(CompatCheck("Currently a hard disk", NO,
                                      d.kind.value.upper() if d.kind is not DiskKind.UNKNOWN else "NVMe connection"))
        return opt
    if d.kind is DiskKind.UNKNOWN:
        opt.checks.append(CompatCheck("Currently a hard disk", CANT_TELL, "drive type wasn't reported"))
    else:
        opt.checks.append(CompatCheck("Currently a hard disk", YES, d.model or "HDD"))

    laptop = _is_laptop(s)
    bus = (d.bus_type or "").strip()
    size = _drive_size_gb(d.used_gb, d.total_gb)
    lo, hi = _ssd_price("SATA", size)
    if bus.upper() == "SATA":
        opt.checks.append(CompatCheck("Connector", YES, "SATA, so a SATA SSD drops into the same place"))
        if laptop:
            opt.part = f"{_size_text(size)} 2.5-inch SATA SSD (7 mm fits all bays)"
            opt.checks.append(CompatCheck("Drive size", YES, "laptop 2.5-inch bay"))
        elif laptop is False:
            opt.part = f"{_size_text(size)} 2.5-inch SATA SSD (+ a $5 bracket if the case only has 3.5-inch bays)"
            opt.checks.append(CompatCheck("Drive size", YES, "desktop bay"))
        else:
            opt.part = f"{_size_text(size)} 2.5-inch SATA SSD"
            opt.checks.append(CompatCheck("Drive size", CANT_TELL, "laptop or desktop wasn't detected"))
        # Changed in v0.3.0 (review): OS-neutral wording.
        opt.checks.append(CompatCheck("M.2 slot", CANT_TELL, "empty M.2 slots aren't reported; a SATA "
                                      "SSD works either way"))
        opt.possible = True if d.kind is DiskKind.HDD else None
        if d.kind is DiskKind.UNKNOWN:
            # Changed in v0.3.0 (review): the real doubt is the drive type, not the M.2 slot.
            opt.summary = ("Can't tell if an SSD would help: the drive type wasn't reported. If it is a hard "
                           f"disk, a {opt.part} fits.")
            opt.how_to_check = ("Open Task Manager > Performance > Disk 0 (or look up the drive model"
                                + (f" {d.model}" if d.model else "") + ") to see whether it is an HDD or SSD.")
    elif bus.upper() in ("USB", "SD", "MMC"):
        opt.checks.append(CompatCheck("Connector", NO, f"the system runs from a {bus} drive"))
        opt.part = f"{_size_text(size)} internal SSD (check which type the PC takes)"
        opt.possible = None
        opt.how_to_check = "Check the PC's manual for its internal drive bay or M.2 slot."
    else:
        opt.checks.append(CompatCheck("Connector", CANT_TELL, f"bus type {'not reported' if not bus else bus}"))
        opt.part = f"{_size_text(size)} SSD with the same connector as the current drive"
        opt.possible = None
        opt.how_to_check = ("Look up the drive model "
                            + (f"({d.model}) " if d.model else "")
                            + "or the PC model to see if it is SATA (2.5-inch) or M.2.")
    if lo is not None and hi is not None:
        opt.cost_low, opt.cost_high = lo + 10, hi + 15  # + USB-SATA cable for cloning
    if opt.possible:
        opt.summary = (f"An SSD fits: {opt.part}. Clone the old drive with a USB-to-SATA cable (~$10) "
                       "or do a fresh install of the operating system.")
    elif opt.possible is None and not opt.summary:
        opt.summary = f"Probably possible, but can't tell which SSD fits: {opt.checks[-1].detail}."
    return opt


def replace_disk(s: SystemSnapshot, disk: DiskInfo) -> UpgradeOption:
    """Replacement for a failing/degrading drive, matched to its interface and size."""
    opt = UpgradeOption(kind="replace_disk", possible=True)
    size = _drive_size_gb(disk.used_gb, disk.total_gb)
    bus = (disk.bus_type or "").upper()
    if disk.is_system_drive or disk.kind in (DiskKind.SSD, DiskKind.NVME) or size <= 1000:
        iface = "NVMe" if bus == "NVME" or disk.kind is DiskKind.NVME else "SATA"
        lo, hi = _ssd_price(iface, size)  # Changed in v0.3.0 (review): no 2 TB price for a 4 TB drive
        form = "M.2 NVMe SSD" if iface == "NVMe" else "2.5-inch SATA SSD"
        opt.part = f"{_size_text(size)} {form}"
    else:
        lo, hi = HDD_PRICES.get(size, (60, 120))
        opt.part = f"{_size_text(size)} 3.5-inch SATA hard disk, or an SSD of the same size if budget allows"
    opt.cost_low, opt.cost_high = lo, hi  # None, None -> "price varies"
    if bus:
        opt.checks.append(CompatCheck("Connector", YES, f"{disk.bus_type}: buy the same type"))
    else:
        opt.checks.append(CompatCheck("Connector", CANT_TELL, "bus type not reported; match the old drive"))
        opt.possible = None
    opt.summary = f"Replace {disk.mountpoint or disk.device} with a {opt.part}."
    return opt


def battery_option(s: SystemSnapshot) -> UpgradeOption:
    """Battery replacement: desktop vs "no battery detected" vs worn battery."""
    opt = UpgradeOption(kind="battery")
    laptop = _is_laptop(s)
    b = s.battery
    if b is None:
        if laptop is False:
            opt.possible, opt.needed = False, False
            opt.summary = "Desktop PC: no battery to replace."
            opt.checks.append(CompatCheck("Has a battery", NO, "desktop chassis"))
        elif laptop is True:
            opt.summary = "No battery detected on a laptop: it may be missing, dead or not reporting. Can't tell."
            opt.checks.append(CompatCheck("Has a battery", CANT_TELL, "laptop chassis, but no battery reported"))
            opt.how_to_check = "Unplug the charger: if it switches off at once, the battery is dead or missing."
        else:
            opt.summary = "No battery detected (desktop, or a battery that isn't reporting)."
            opt.checks.append(CompatCheck("Has a battery", CANT_TELL, "no battery reported"))
        return opt
    if laptop is False:
        # Added in v0.3.0 (review): a "battery" on a desktop is usually a UPS or a
        # wireless peripheral reporting through the OS, not a part inside this PC.
        opt.possible, opt.needed = None, False
        opt.summary = ("Desktop PC: the battery reported is probably a UPS or another device, "
                       "not one inside this PC.")
        opt.checks.append(CompatCheck("Has a battery", CANT_TELL, "desktop chassis, but a battery is reported"))
        return opt
    opt.checks.append(CompatCheck("Has a battery", YES,
                                  f"{b.wear_percent:.0f}% worn" if b.wear_percent is not None else "wear unknown"))
    opt.checks.append(CompatCheck("User-replaceable", CANT_TELL, "many laptops have an internal battery behind "
                                  "screws; check the model's service manual"))
    opt.possible = True
    model = s.device.model if s.device and s.device.model else None
    opt.part = f"replacement battery for {s.device.manufacturer + ' ' if s.device and s.device.manufacturer else ''}" \
               f"{model or 'this laptop'} (genuine or a well-reviewed brand)"
    opt.cost_low, opt.cost_high = BATTERY_PRICE
    # Changed in v0.3.0 (review): we can't see whether the battery is
    # user-replaceable, so the summary no longer says a new one "fits". A
    # replacement is still possible (a repair shop can fit an internal one),
    # but the user is told to check their model first.
    replaceable = next((c.status for c in opt.checks if c.item == "User-replaceable"), CANT_TELL)
    if replaceable == YES:
        opt.summary = f"A new battery fits this laptop: {opt.part}."
    else:
        opt.summary = (f"A new battery should help: {opt.part}. Check if your model's battery is "
                       "user-replaceable first; if it's internal, a repair shop can fit it.")
        opt.how_to_check = ("Look up your exact model's service manual or the maker's support page to see "
                            "whether the battery is user-replaceable.")
    return opt


__all__ = ["YES", "NO", "CANT_TELL", "CompatCheck", "UpgradeOption", "ram_soldered", "ram_upgrade",
           "ssd_upgrade", "replace_disk", "battery_option"]
