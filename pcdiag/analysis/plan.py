# pcdiag/analysis/plan.py
# Added in v0.3.0 (roadmap phase 5: "Make my PC better without buying a new one").
# Area: analysis / recommender.
"""One prioritised improvement plan: free -> cheap -> replace.

``build_plan(snapshot, findings, recs)`` turns the findings from ``analyze``
(and the upgrade ideas from ``recommend_upgrades``) into ordered
:class:`PlanStep` objects:

1. **Urgent first**: a failing or degrading drive puts "Back up now" and
   "Replace the drive" at the top, whatever they cost. Those are stop points:
   do them before anything else.
2. **Free**: startup apps, heavy programs, disk cleanup, graphics driver,
   power plan, Windows Update / restart, cooling. Sorted by expected impact.
3. **Cheap** upgrades: SSD, RAM, battery, thermal paste, a replacement or
   bigger drive. Only when :mod:`pcdiag.analysis.compat` says the part fits
   (or says "can't tell", which the step states plainly). Never an
   upgrade that is known to be impossible.
4. **Replace**: a new PC only when the bottleneck can't be fixed by an upgrade
   (soldered or maxed-out RAM that is too small, or a CPU that stays maxed
   out on a laptop).

Each step lists the evidence behind it (from sustained monitor data when the
snapshot has it), a cost range and its confidence. The plan is advice only:
pcdiag never changes the system.

Step builders are registered with :func:`plan_rule` so each can be tested on
its own (``PLAN_RULES``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional

from ..models import DiskInfo, DiskKind, Model, SmartStatus, SystemSnapshot
from . import compat
from .compat import CompatCheck, UpgradeOption
from .findings import Finding, Severity
from .recommend import Recommendation
from .rules import _disk_label, _os_name, _os_noun, admin_blocked_collectors, missing_core_sections  # Added in v0.3.0 (review)

# Expected-impact scores (0-100) for ordering inside a tier.
IMPACT_BACKUP = 100
IMPACT_REPLACE_FAILING = 99
IMPACT_STARTUP_MANY = 60
IMPACT_STARTUP_SOME = 30
IMPACT_HOGS = 50
IMPACT_CLEANUP_FULL = 55
IMPACT_CLEANUP_FILLING = 45
IMPACT_POWER_PLAN = 40
IMPACT_UPDATES = 35
IMPACT_RESTART = 25
IMPACT_DRIVER = 30
IMPACT_COOLING = 35
IMPACT_SSD = 90
IMPACT_RAM_LOW = 75
IMPACT_RAM_TIGHT = 55
IMPACT_BIGGER_DRIVE = 40
IMPACT_BATTERY_REPLACE = 35
IMPACT_BATTERY_WORN = 15
IMPACT_REPASTE = 20
IMPACT_REPLACE_PC = 10

#: Windows Update is "overdue" after this many days without an installed update.
UPDATE_OVERDUE_DAYS = 60
#: Free-space goal for the cleanup step (percent of the drive).
CLEANUP_TARGET_FREE_PERCENT = 15
#: Known security tools: never tell users to disable these, only to let a scan finish.
SECURITY_PROCESSES = {"msmpeng.exe": "Windows Security (Microsoft Defender)"}
#: Trial/duplicate antivirus startup entries that are safe to uninstall when Defender runs too.
TRIAL_AV_HINTS = ("mcafee", "norton", "avast", "avg ", "trial")


class PlanTier(str, Enum):
    """Where a step sits in the free -> cheap -> replace order."""
    FREE = "free"
    CHEAP = "cheap"
    REPLACE = "replace"


TIER_TITLE = {PlanTier.FREE: "Free fixes", PlanTier.CHEAP: "Cheap upgrades", PlanTier.REPLACE: "Replace the PC"}
_TIER_ORDER = {PlanTier.FREE: 0, PlanTier.CHEAP: 1, PlanTier.REPLACE: 2}


@dataclass
class PlanStep(Model):
    """One thing to do, with why, the evidence, the cost and what to buy (if anything)."""
    id: str = ""                         # "free.startup", "cheap.ssd", "urgent.backup", ...
    tier: PlanTier = PlanTier.FREE
    title: str = ""
    why: str = ""                        # one plain sentence: what it fixes
    actions: list[str] = field(default_factory=list)    # concrete steps
    evidence: list[str] = field(default_factory=list)   # the measurements behind it
    cost: str = "Free"                   # "Free", "$65-105", "$400-900"
    impact: int = 0                      # 0-100, for ordering inside a tier
    urgent: bool = False                 # data safety: before everything else
    stop_point: Optional[str] = None     # "Back up now" style checkpoint
    confidence: str = "medium"           # high / medium / low
    compatibility: list[CompatCheck] = field(default_factory=list)
    related_findings: list[str] = field(default_factory=list)
    order: int = 0                       # 1 = do first; set by build_plan


@dataclass
class ImprovementPlan(Model):
    """The whole plan plus a headline and what couldn't be checked."""
    headline: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    nothing_needed: bool = False
    used_monitor: bool = False           # evidence includes a monitor window
    cant_tell: list[str] = field(default_factory=list)   # gaps, in plain words

    def tier(self, tier: PlanTier) -> list[PlanStep]:
        """Steps of one tier (urgent steps are listed under their own tier too)."""
        return [s for s in self.steps if s.tier is tier]


# --------------------------------------------------------------------------
# Context + registry
# --------------------------------------------------------------------------

@dataclass
class PlanContext:
    """What a plan rule can read: the snapshot, findings by id, recs and compat answers."""
    snapshot: SystemSnapshot
    findings: list[Finding]
    recs: list[Recommendation]
    ram: UpgradeOption
    ssd: UpgradeOption
    battery: UpgradeOption

    def get(self, *ids: str) -> Optional[Finding]:
        """First finding with one of these ids, in the order given."""
        for fid in ids:
            f = next((x for x in self.findings if x.id == fid), None)
            if f is not None:
                return f
        return None

    def problem(self, *ids: str) -> Optional[Finding]:
        """Like ``get`` but only WARNING/CRITICAL findings."""
        for fid in ids:
            f = next((x for x in self.findings if x.id == fid and x.is_problem), None)
            if f is not None:
                return f
        return None

    def has(self, *ids: str, min_severity: Severity = Severity.INFO) -> Optional[Finding]:
        """First finding with one of these ids at or above ``min_severity``."""
        for fid in ids:
            f = next((x for x in self.findings if x.id == fid and x.severity.rank >= min_severity.rank), None)
            if f is not None:
                return f
        return None


PlanRule = Callable[[PlanContext], Iterable[PlanStep]]
PLAN_RULES: dict[str, PlanRule] = {}


def plan_rule(name: str) -> Callable[[PlanRule], PlanRule]:
    """Register a plan step builder under ``name``."""
    def deco(fn: PlanRule) -> PlanRule:
        PLAN_RULES[name] = fn
        return fn
    return deco


# --------------------------------------------------------------------------
# Evidence helpers (sustained monitor data first, then the single reading)
# --------------------------------------------------------------------------

def _mins(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


def _num(x: object, fmt: str = "g", unknown: str = "?") -> str:
    """Format a number for evidence text; anything missing or non-numeric shows as ``unknown``.

    Added in v0.3.0 (review): evidence dicts can hold None (a reading that wasn't
    collected). ``f"{None:g}"`` raises TypeError, which used to make the whole
    plan disappear from the reports.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return unknown
    return format(x, fmt)


def _sustained_line(f: Finding, what: str) -> Optional[str]:
    """'RAM above 85% for 100% of 5 min (average 92%, peak 94.5%)' from a sustained finding."""
    ev = f.evidence if isinstance(f.evidence, dict) else {}
    if not isinstance(ev.get("duration_s"), (int, float)):  # Changed in v0.3.0 (review): None-safe
        return None
    unit = ev.get("unit", "")
    return (f"{what} above {_num(ev.get('threshold'))}{unit} for {_num(ev.get('pct_above_threshold', 0))}% of "
            f"{_mins(ev['duration_s'])} monitored (average {_num(ev.get('mean'))}{unit}, "
            f"peak {_num(ev.get('max'))}{unit})")


def _conf(f: Optional[Finding], default: str = "medium") -> str:
    return f.confidence.value if f is not None else default


def _drive_name(d: DiskInfo) -> str:
    name = d.mountpoint or d.device or "drive"
    return f"{name} ({d.model})" if d.model else name


# --------------------------------------------------------------------------
# Urgent: data safety
# --------------------------------------------------------------------------

def _bad_disks(s: SystemSnapshot, findings: Optional[list[Finding]] = None) -> list[tuple[DiskInfo, SmartStatus]]:
    """Disks that need a backup step.

    Changed in v0.3.0 (review): also includes disks whose overall SMART verdict is
    still OK but which got a ``storage.smart_warning`` finding (reallocated or
    pending sectors, read errors, a worn SSD). Before, those had a WARNING in the
    findings but no step in the plan.
    """
    flagged = {f.evidence.get("drive") for f in (findings or [])
               if f.id in ("storage.smart_failing", "storage.smart_warning") and f.is_problem
               and isinstance(f.evidence, dict)}
    out = []
    for d in s.disks:
        if d.smart is None:
            continue
        if d.smart.status in (SmartStatus.FAILING, SmartStatus.WARNING):
            out.append((d, d.smart.status))
        elif _disk_label(d) in flagged:
            out.append((d, SmartStatus.WARNING))
    return out


def _smart_finding(ctx: "PlanContext", d: DiskInfo) -> Optional[Finding]:
    """The SMART finding for this disk (matched by its label). Added in v0.3.0 (review).

    Before, the first failing/warning finding was used for every disk, so with two
    bad drives both steps pointed at the same finding.
    """
    label = _disk_label(d)
    for f in ctx.findings:
        if f.id in ("storage.smart_failing", "storage.smart_warning") and isinstance(f.evidence, dict) \
                and f.evidence.get("drive") == label:
            return f
    return ctx.get("storage.smart_failing", "storage.smart_warning")


@plan_rule("backup_failing_disk")
def backup_failing_disk(ctx: PlanContext) -> Iterable[PlanStep]:
    """A failing/degrading drive: back up now, then replace it (both urgent)."""
    steps = []
    for d, status in _bad_disks(ctx.snapshot, ctx.findings):  # Changed in v0.3.0 (review)
        f = _smart_finding(ctx, d)  # Changed in v0.3.0 (review): this disk's own finding
        sm = d.smart
        ev = [f"{_drive_name(d)} reports SMART status {status.value.upper()}"]
        if sm.reallocated_sectors:
            ev.append(f"{sm.reallocated_sectors:,} reallocated sectors (worn-out spots the drive has replaced)")
        if sm.pending_sectors:
            ev.append(f"{sm.pending_sectors:,} pending sectors (spots it can't read reliably)")
        if sm.power_on_hours:
            ev.append(f"{sm.power_on_hours:,} power-on hours (about {sm.power_on_hours / 8760:.1f} years running)")
        busy = ctx.get("storage.disk_busy_sustained", "storage.disk_busy")
        if busy is not None and busy.is_problem:
            line = _sustained_line(busy, "Disk activity") or busy.title
            ev.append(line + " (a failing drive retries reads, which looks like a busy disk)")
        name = d.mountpoint or d.device
        failing = status is SmartStatus.FAILING
        steps.append(PlanStep(
            id=f"urgent.backup.{name}", tier=PlanTier.FREE, urgent=True, impact=IMPACT_BACKUP,
            title=f"Back up {name} now",
            why=("The drive says it is failing; files on it can disappear without warning."
                 if failing else "The drive reports early signs of wear; back up before it gets worse."),
            actions=[f"Copy everything you care about from {name} to another drive or cloud storage today.",
                     "Copy the most important files first (documents, photos); a failing drive can stop mid-copy.",
                     "Don't run disk repair (chkdsk /r) or defrag on it before the backup: heavy reads can finish it off."],
            evidence=ev, cost="Free, or ~$50-70 for an external backup drive",
            stop_point=f"STOP: back up {name} now, before any other step.",
            confidence=_conf(f, "high"), related_findings=[f.id] if f else []))
        opt = compat.replace_disk(ctx.snapshot, d)
        steps.append(PlanStep(
            id=f"urgent.replace_disk.{name}", tier=PlanTier.CHEAP, urgent=failing,
            impact=IMPACT_REPLACE_FAILING if failing else IMPACT_REPLACE_FAILING - 10,
            title=f"Replace the {'failing' if failing else 'wearing'} drive {name}",
            why="After the backup, swap the drive so the data has a healthy home again.",
            actions=[f"Buy a {opt.part}.", "Fit it, copy the backup onto it, then retire the old drive."]
                    # Changed in v0.3.0 (review): names the detected OS instead of always "Windows".
                    + ([f"This is the system drive: clone it or reinstall {_os_noun(ctx.snapshot)} on the new one."]
                       if d.is_system_drive else []),
            evidence=ev[:1], cost=opt.cost_text, compatibility=opt.checks,
            stop_point="Only after the backup is finished." if failing else None,
            confidence=_conf(f, "high"), related_findings=[f.id] if f else []))
    return steps


# --------------------------------------------------------------------------
# Free fixes
# --------------------------------------------------------------------------

@plan_rule("startup_apps")
def startup_apps(ctx: PlanContext) -> Iterable[PlanStep]:
    """Too many programs launching at sign-in."""
    f = ctx.has("startup.too_many")
    if f is None:
        return []
    names = list(f.evidence.get("items") or [])
    trial_av = [n for n in names if any(h in n.lower() for h in TRIAL_AV_HINTS)]
    actions = ["Open Task Manager (Ctrl+Shift+Esc) > Startup apps and set everything you don't need at sign-in "
               "to Disabled. It only stops auto-start; the programs still work when opened."]
    if names:
        actions.append("Start with the ones you don't use daily, e.g. " + ", ".join(names[:4]) + ".")
    if trial_av:
        actions.append(f"Uninstall {', '.join(trial_av)}: an expired trial antivirus next to Windows Security "
                       "slows everything and adds no protection.")
    actions.append("Keep security software, touchpad/audio drivers and cloud sync you rely on.")
    return [PlanStep(
        id="free.startup", tier=PlanTier.FREE, title="Turn off unneeded startup apps",
        why="Fewer programs at sign-in means faster boot and more free RAM and CPU all day.",
        actions=actions, evidence=[f"{f.evidence.get('count', len(names))} programs start automatically"],
        impact=IMPACT_STARTUP_MANY if f.is_problem else IMPACT_STARTUP_SOME,
        confidence=_conf(f), related_findings=[f.id])]


@plan_rule("resource_hogs")
def resource_hogs(ctx: PlanContext) -> Iterable[PlanStep]:
    """Programs eating RAM/CPU, using monitor process data when present."""
    s = ctx.snapshot
    mem = ctx.problem("memory.sustained", "memory.pressure")
    cpu = ctx.problem("cpu.sustained", "cpu.busy")
    heavy = ctx.has("processes.heavy")
    if mem is None and cpu is None and heavy is None:
        return []
    ev: list[str] = []
    if mem is not None:
        ev.append(_sustained_line(mem, "RAM use") or
                  f"RAM {mem.evidence.get('ram_used_percent')}% in use, {mem.evidence.get('available_gb')} GB free")
    if cpu is not None:
        ev.append(_sustained_line(cpu, "CPU use") or f"CPU {cpu.evidence.get('cpu_usage_percent')}% busy")
    procs = sorted([p for p in s.top_processes if p.name],
                   key=lambda p: (p.memory_mb or 0) + 20 * (p.cpu_percent or 0), reverse=True)[:3]
    if s.monitor is not None and s.monitor.samples:
        top = s.monitor.process_summary(by="memory", n=3)
        if top:
            ev.append("Busiest over the monitor window: " + ", ".join(
                f"{t.name} (avg {t.mean_memory_mb:.0f} MB)" for t in top if t.mean_memory_mb is not None))
    elif procs:
        ev.append("Heaviest now: " + ", ".join(
            f"{p.name} ({p.cpu_percent or 0:.0f}% CPU, {p.memory_mb or 0:,.0f} MB)" for p in procs))
    actions = ["Close programs and browser tabs you aren't using; in Chrome/Edge turn on Memory Saver "
               "(Settings > Performance)."]
    names = {p.name.lower(): p.name for p in s.top_processes if p.name}
    for exe, label in SECURITY_PROCESSES.items():
        if exe in names:
            actions.append(f"{names[exe]} is {label} scanning: don't turn it off. Let the scan finish while plugged in; "
                           "it uses far less once the PC is caught up.")
    actions.append("Uninstall programs you no longer use (Settings > Apps > Installed apps).")
    return [PlanStep(
        id="free.hogs", tier=PlanTier.FREE, title="Tame the programs using the most memory and CPU",
        why="Less background load leaves room for what you're actually doing.",
        actions=actions, evidence=[e for e in ev if e], impact=IMPACT_HOGS,
        confidence=_conf(mem or cpu or heavy),
        related_findings=[f.id for f in (mem, cpu, heavy) if f is not None])]


@plan_rule("disk_cleanup")
def disk_cleanup(ctx: PlanContext) -> Iterable[PlanStep]:
    """Full or filling drives: free up space."""
    steps = []
    full = ctx.problem("storage.system_disk_full")
    filling = ctx.problem("storage.disk_filling")
    if full is not None or filling is not None:
        f = full or filling
        ev = []
        if full is not None:
            e = full.evidence
            # Changed in v0.3.0 (review): None-safe; a missing total used to crash the plan.
            ev.append(f"{e.get('drive') or 'The system drive'} is {_num(e.get('used_percent'))}% full "
                      f"({_num(e.get('free_gb'))} GB free of {_num(e.get('total_gb'))} GB)")
        if filling is not None:
            ev.append(filling.title)
        d = ctx.snapshot.system_drive
        goal = (f" (aim for at least {d.total_gb * CLEANUP_TARGET_FREE_PERCENT / 100:.0f} GB free)"
                if d and d.total_gb else "")
        steps.append(PlanStep(
            # Changed in v0.3.0 (review): OS-neutral title (was "the Windows drive" on Linux too).
            id="free.cleanup", tier=PlanTier.FREE, title="Free up space on the system drive" + goal,
            why="The computer slows down and updates fail when the system drive is nearly full.",
            actions=["Settings > System > Storage > Temporary files: remove temporary files, the Recycle Bin and "
                     "old Windows Update files.",
                     "Turn on Storage Sense so this happens automatically.",
                     "Uninstall big programs and games you don't use; move videos and photos to another drive or "
                     "cloud storage."],
            evidence=ev, impact=IMPACT_CLEANUP_FULL if full else IMPACT_CLEANUP_FILLING,
            confidence=_conf(f), related_findings=[x.id for x in (full, filling) if x]))
    data = [f for f in ctx.findings if f.id == "storage.data_disk_full" and f.is_problem]
    for f in data:
        steps.append(PlanStep(
            id=f"free.cleanup_data.{f.evidence.get('drive')}", tier=PlanTier.FREE,
            title=f"Free up space on {f.evidence.get('drive')}",
            why="A full data drive can't take new files or backups.",
            actions=["Delete or move files you don't need; empty the Recycle Bin."],
            evidence=[f.title], impact=IMPACT_CLEANUP_FILLING - 10, confidence=_conf(f), related_findings=[f.id]))
    return steps


@plan_rule("graphics_driver")
def graphics_driver(ctx: PlanContext) -> Iterable[PlanStep]:
    """Outdated graphics driver (age from the driver date)."""
    f = ctx.has("gpu.driver_outdated")
    if f is None:
        return []
    e = f.evidence
    gpu = e.get("gpu") or "graphics"
    vendor = ("Intel Driver & Support Assistant" if "intel" in gpu.lower() else
              "NVIDIA app" if "nvidia" in gpu.lower() else
              "AMD Software: Adrenalin Edition" if ("amd" in gpu.lower() or "radeon" in gpu.lower()) else
              "the graphics maker's website")
    return [PlanStep(
        id="free.driver", tier=PlanTier.FREE, title=f"Update the {gpu} driver",
        why="Old graphics drivers cause glitches, crashes and slow video playback; updates are free.",
        actions=[f"Get the latest driver with {vendor}, or Windows Update > Advanced options > Optional updates.",
                 "On laptops, the PC maker's support page may have the tested driver; use that if the generic "
                 "one won't install."],
        evidence=[f"Driver {e.get('driver_version')} is dated {e.get('driver_date')} "
                  # Changed in v0.3.0 (review): None-safe age.
                  + (f"({e['age_days'] / 365:.1f} years old)" if isinstance(e.get("age_days"), (int, float))
                     else "(age unknown)")],
        impact=IMPACT_DRIVER, confidence=_conf(f), related_findings=[f.id])]


@plan_rule("power_plan")
def power_plan(ctx: PlanContext) -> Iterable[PlanStep]:
    """Power saver plan while plugged in (or on a desktop) holds the CPU back."""
    sw = ctx.snapshot.software
    if sw is None or not sw.power_plan:
        return []
    plan = sw.power_plan.lower()
    if "saver" not in plan:
        return []
    b = ctx.snapshot.battery
    if b is not None and b.plugged_in is False:
        return []  # on battery, saving power is reasonable
    # Changed in v0.3.0 (review): don't claim "plugged in" when the charger state is unknown.
    where = ("while plugged in" if b is not None and b.plugged_in is True else
             "on a PC without a battery" if b is None else "(charger state unknown)")
    return [PlanStep(
        id="free.power_plan", tier=PlanTier.FREE, title="Switch the power plan from Power saver to Balanced",
        why="Power saver caps the processor speed; Balanced runs fast when needed and still saves power.",
        actions=["Control Panel > Hardware and Sound > Power Options > choose Balanced "
                 "(Windows 11: Settings > System > Power & battery > Power mode > Balanced)."],
        evidence=[f"Active power plan is \"{sw.power_plan}\" {where}"],
        impact=IMPACT_POWER_PLAN, confidence="high")]


@plan_rule("windows_update")
def windows_update(ctx: PlanContext) -> Iterable[PlanStep]:
    """Pending restart, overdue updates, or a long uptime."""
    sw = ctx.snapshot.software
    uptime = ctx.has("system.long_uptime")
    ev, actions = [], []
    impact = 0
    if sw is not None and sw.reboot_pending:
        ev.append("Windows Update is waiting for a restart to finish installing")
        impact = IMPACT_UPDATES
    if sw is not None and sw.days_since_update is not None and sw.days_since_update >= UPDATE_OVERDUE_DAYS:
        ev.append(f"Newest installed update is from {sw.last_update_installed} ({sw.days_since_update} days ago)")
        impact = IMPACT_UPDATES
    if uptime is not None:
        hours = uptime.evidence.get("uptime_hours")  # Changed in v0.3.0 (review): None-safe
        ev.append(f"Not restarted for {hours / 24:.0f} days" if isinstance(hours, (int, float))
                  else uptime.title)
        impact = max(impact, IMPACT_RESTART)
    if not ev:
        return []
    actions.append("Save your work and choose Restart (not Shut down: fast startup keeps old state on shut down).")
    if sw is not None and sw.days_since_update is not None and sw.days_since_update >= UPDATE_OVERDUE_DAYS:
        actions.append("Then Settings > Windows Update > Check for updates, install everything and restart again "
                       "if asked. Plug in a laptop first.")
    title = ("Restart and catch up on Windows Update" if impact == IMPACT_UPDATES else "Restart the PC")
    return [PlanStep(
        id="free.updates", tier=PlanTier.FREE, title=title,
        why="A restart clears memory leaks and finishes updates; updates fix bugs and security holes.",
        actions=actions, evidence=ev, impact=impact, confidence="high",
        related_findings=[uptime.id] if uptime else [])]


@plan_rule("cooling")
def cooling(ctx: PlanContext) -> Iterable[PlanStep]:
    """Hot CPU: free cleaning first; re-pasting is a cheap step when it throttles."""
    f = ctx.problem("thermal.cpu_sustained", "thermal.cpu") or ctx.has("thermal.cpu_sustained")
    if f is None or f.severity is Severity.OK:
        return []
    single = ctx.get("thermal.cpu")
    sustained_ev = ctx.get("thermal.cpu_sustained")
    ev = []
    if sustained_ev is not None:
        line = _sustained_line(sustained_ev, "CPU temperature")
        if line:
            ev.append(line)
    if single is not None and single.severity is not Severity.OK:
        ev.append(single.title)
    for g in ctx.findings:
        if g.id == "thermal.cpu" and g.evidence.get("likely_throttling"):
            ev.append(f"Running at {g.evidence.get('current_freq_mhz')} MHz under load, below its "
                      f"{g.evidence.get('base_freq_mhz')} MHz base speed (throttling)")
    for g in ctx.findings:  # superseded single reading kept in the sustained evidence
        for sup in (g.evidence.get("superseded") or []) if g.id == "thermal.cpu_sustained" else []:
            ev.append(f"Single reading: {sup.get('title')}")
    # Changed in v0.3.0 (review): throttling comes from the "likely_throttling"
    # field, on the single reading or on the reading the over-time check
    # replaced. Before, it matched the words "slowing itself down" in the
    # evidence text, which would break if that wording ever changed.
    throttling = any(g.evidence.get("likely_throttling") for g in ctx.findings if g.id == "thermal.cpu") or \
        any(sup.get("likely_throttling") for g in ctx.findings if g.id == "thermal.cpu_sustained"
            for sup in (g.evidence.get("superseded") or []) if isinstance(sup, dict))
    steps = [PlanStep(
        id="free.cooling", tier=PlanTier.FREE, title="Clean the vents and fans",
        why="A hot processor slows itself down to stay safe; dust is the usual cause.",
        actions=["Shut down and unplug. Blow compressed air through the vents in short bursts "
                 "(hold the fan still so it doesn't over-spin).",
                 "Use laptops on a hard, flat surface, not a bed or lap."],
        evidence=ev or [f.title], impact=IMPACT_COOLING if f.is_problem else IMPACT_COOLING - 15,
        confidence=_conf(f), related_findings=[f.id])]
    if throttling:
        steps.append(PlanStep(
            id="cheap.repaste", tier=PlanTier.CHEAP, title="Refresh the CPU thermal paste (if cleaning didn't help)",
            why="Old paste dries out after 5+ years; new paste can drop temperatures by 10°C or more.",
            actions=["DIY with a $5-15 tube and a guide for your model, or ask a repair shop (~$40-80)."],
            evidence=ev[:2] or [f.title], cost="$5-15 DIY, $40-80 at a shop", impact=IMPACT_REPASTE,
            confidence=_conf(f), stop_point="Only if the PC still runs hot after cleaning the vents.",
            related_findings=[f.id]))
    return steps


# --------------------------------------------------------------------------
# Cheap upgrades (only when compat says it fits or can't tell)
# --------------------------------------------------------------------------

@plan_rule("ssd_upgrade")
def ssd_upgrade(ctx: PlanContext) -> Iterable[PlanStep]:
    """HDD system drive -> SSD."""
    f = ctx.problem("storage.hdd_system_drive")
    opt = ctx.ssd
    if f is None or not opt.needed or opt.possible is False:
        return []
    ev = [f"{_os_name(ctx.snapshot)} runs from a hard disk: {f.evidence.get('drive')}"]  # Changed in v0.3.0 (review)
    busy = ctx.problem("storage.disk_busy_sustained", "storage.disk_busy")
    if busy is not None:
        ev.append(_sustained_line(busy, "Disk activity") or busy.title)
    bench = ctx.snapshot.benchmark
    if bench and bench.disk_read_mbps:
        ev.append(f"Benchmark: {bench.disk_read_mbps:g} MB/s read (a SATA SSD does ~500 MB/s)")
    d = ctx.snapshot.system_drive
    if d and d.used_gb:
        ev.append(f"{d.used_gb:g} GB in use, so the new drive is sized for it with room to spare")
    title = "Swap the hard disk for an SSD" + (f": {opt.part}" if opt.part else "")
    actions = [f"Buy a {opt.part}." if opt.part else "Buy an SSD with the same connector."]
    actions.append("Clone the old drive (USB-to-SATA cable + the SSD maker's free cloning tool) or do a fresh "
                   f"{_os_noun(ctx.snapshot)} install; back up first either way.")  # Changed in v0.3.0 (review)
    if opt.possible is None:
        actions.insert(0, "Check first: " + (opt.how_to_check or opt.summary))
    return [PlanStep(
        id="cheap.ssd", tier=PlanTier.CHEAP, title=title,
        why="The biggest speed-up for an older PC: boot, app launches and updates get several times faster.",
        actions=actions, evidence=ev, cost=opt.cost_text, impact=IMPACT_SSD,
        confidence="high" if opt.possible else "low", compatibility=opt.checks,
        stop_point="Back up your files before swapping drives.", related_findings=[f.id])]


def _ram_needed(ctx: PlanContext) -> tuple[Optional[Finding], int]:
    low = ctx.problem("memory.low_total")
    press = ctx.problem("memory.sustained", "memory.pressure")
    m = ctx.snapshot.memory
    if low is not None:
        return low, IMPACT_RAM_LOW
    if press is not None and m and m.total_gb and m.total_gb < compat.RAM_TARGET_GB + 0.5:
        return press, IMPACT_RAM_TIGHT
    return None, 0


@plan_rule("ram_upgrade")
def ram_upgrade(ctx: PlanContext) -> Iterable[PlanStep]:
    """Add RAM, matched to slots, type, form factor and the board's maximum."""
    f, impact = _ram_needed(ctx)
    opt = ctx.ram
    if f is None or opt.possible is False or not opt.needed:
        return []
    m = ctx.snapshot.memory
    ev = [f"{m.total_gb:g} GB installed" + (f" ({m.type})" if m.type else "")]
    press = ctx.problem("memory.sustained", "memory.pressure")
    if press is not None:
        ev.append(_sustained_line(press, "RAM use") or
                  f"RAM {press.evidence.get('ram_used_percent')}% in use; swap "
                  f"{press.evidence.get('swap_used_percent')}% used (the system is using the slow disk as memory)")
    actions = [f"Buy {opt.part}." if opt.part else "Buy matching RAM."]
    if opt.possible is None:
        actions.insert(0, "Check first: " + (opt.how_to_check or opt.summary))
    elif opt.how_to_check:
        actions.append(opt.how_to_check)
    actions.append("Unplug (and on a laptop disconnect the battery if the manual says so) before opening the case.")
    return [PlanStep(
        id="cheap.ram", tier=PlanTier.CHEAP, title="Add more RAM" + (f": {opt.part.split(';')[0]}" if opt.part else ""),
        why="With more memory the system stops swapping to the disk, so switching apps and browsing stop stalling.",
        actions=actions, evidence=ev, cost=opt.cost_text, impact=impact,
        confidence="high" if opt.possible else "low", compatibility=opt.checks, related_findings=[f.id])]


@plan_rule("bigger_drive")
def bigger_drive(ctx: PlanContext) -> Iterable[PlanStep]:
    """Bigger system SSD, only when the drive is already an SSD (else the SSD step is sized for it)."""
    full = ctx.problem("storage.system_disk_full")
    d = ctx.snapshot.system_drive
    if full is None or d is None or d.kind not in (DiskKind.SSD, DiskKind.NVME):
        return []
    opt = compat.replace_disk(ctx.snapshot, d)
    return [PlanStep(
        id="cheap.bigger_drive", tier=PlanTier.CHEAP, title=f"If cleanup isn't enough: a bigger SSD ({opt.part})",
        why="When the files you keep really don't fit, more space is the only lasting fix.",
        actions=[f"Buy a {opt.part}; clone the current drive onto it."],
        evidence=[full.title], cost=opt.cost_text, impact=IMPACT_BIGGER_DRIVE, compatibility=opt.checks,
        stop_point="Only after the free cleanup step.", confidence=_conf(full), related_findings=[full.id])]


@plan_rule("battery")
def battery(ctx: PlanContext) -> Iterable[PlanStep]:
    """Worn battery replacement (laptops only)."""
    f = ctx.problem("battery.replace", "battery.worn")
    opt = ctx.battery
    # Changed in v0.3.0 (review): also skip when compat says no battery swap is needed
    # (a desktop reporting a UPS battery).
    if f is None or opt.possible is False or not opt.needed:
        return []
    replace = f.id == "battery.replace"
    return [PlanStep(
        id="cheap.battery", tier=PlanTier.CHEAP,
        title="Replace the battery" + ("" if replace else " (optional, if it no longer lasts long enough)"),
        why="A worn battery runs out fast and can make a laptop shut off suddenly.",
        # Changed in v0.3.0 (review): checking comes before buying, because we
        # can't tell whether this model's battery is user-replaceable.
        actions=(["Check if your model's battery is user-replaceable (see its service manual): internal "
                  "batteries need a screwdriver or a repair shop.", f"Then buy a {opt.part}."]
                 if opt.how_to_check else
                 [f"Buy a {opt.part}.", "Check the model's service manual: internal batteries need a screwdriver "
                  "or a repair shop."]),
        evidence=[f.title + (f", {f.evidence.get('cycle_count')} charge cycles" if f.evidence.get("cycle_count") else "")],
        cost=opt.cost_text, impact=IMPACT_BATTERY_REPLACE if replace else IMPACT_BATTERY_WORN,
        confidence=_conf(f), compatibility=opt.checks, related_findings=[f.id])]


@plan_rule("gpu_upgrade")
def gpu_upgrade(ctx: PlanContext) -> Iterable[PlanStep]:
    """GPU upgrade only when the recommender found the GPU is the bottleneck (desktops only)."""
    rec = next((r for r in ctx.recs if "graphics" in r.title.lower() or "gpu" in r.title.lower()), None)
    if rec is None:
        return []
    laptop = compat._is_laptop(ctx.snapshot)
    if laptop:
        # Changed in v0.3.0 (review): comment corrected. Laptop GPUs can't be swapped,
        # and replace_pc does not cover them either: a slow laptop GPU gets no step.
        return []
    return [PlanStep(
        id="cheap.gpu", tier=PlanTier.CHEAP, title=rec.title, why=rec.reason,
        actions=["Check the power supply wattage and case length before buying."],
        evidence=[rec.reason], cost="$250-600", impact=rec.expected_impact,
        confidence="low" if laptop is None else "medium",
        compatibility=[CompatCheck("Desktop with a PCIe slot", compat.CANT_TELL if laptop is None else compat.YES,
                                   "desktop chassis" if laptop is False else "chassis not detected")],
        related_findings=list(rec.related_findings))]


# --------------------------------------------------------------------------
# Replace: only when upgrades can't fix the bottleneck
# --------------------------------------------------------------------------

@plan_rule("replace_pc")
def replace_pc(ctx: PlanContext) -> Iterable[PlanStep]:
    """A new PC, only for bottlenecks no upgrade can fix."""
    reasons, related = [], []
    f, _ = _ram_needed(ctx)
    if f is not None and ctx.ram.possible is False and ctx.ram.needed:
        reasons.append(ctx.ram.summary)
        related.append(f.id)
    cpu = ctx.problem("cpu.sustained")
    laptop = compat._is_laptop(ctx.snapshot)
    if cpu is not None and cpu.confidence.value in ("medium", "high") and laptop:
        reasons.append((_sustained_line(cpu, "CPU use") or cpu.title)
                       + "; a laptop's processor can't be upgraded")
        related.append(cpu.id)
    if not reasons:
        return []
    return [PlanStep(
        id="replace.pc", tier=PlanTier.REPLACE, title="Consider a new PC if it is still too slow after the steps above",
        why="Some limits can't be fixed with parts on this machine.",
        actions=["Do the free fixes first and re-scan; only buy if the PC still can't keep up.",
                 "When buying: 16 GB RAM (ideally socketed), an NVMe SSD, and a CPU from the last 3 years."],
        evidence=reasons, cost="$450-900 (new laptop or desktop)", impact=IMPACT_REPLACE_PC,
        confidence="medium", related_findings=related)]


# --------------------------------------------------------------------------
# Build the plan
# --------------------------------------------------------------------------

def _cant_tell(ctx: PlanContext) -> list[str]:
    s = ctx.snapshot
    gaps = []
    if ctx.get("thermal.unknown") or any(e.lower().startswith("temperatures") for e in s.errors):
        gaps.append("CPU temperature (no sensor readable; overheating can't be ruled out)")
    for g in s.gpus:
        if g.name and not g.driver_date:
            gaps.append(f"Age of the {g.name} driver (no driver date reported)")
    if s.software is None and s.os is not None and s.os.system == "Windows":
        gaps.append("Power plan and Windows Update status (not collected)")
    blocked = admin_blocked_collectors(s)  # Added in v0.3.0 (review)
    if ctx.get("storage.smart_unknown"):
        # Changed in v0.3.0 (review): only blame Administrator rights when the error says so.
        gaps.append("Health of some drives (SMART not readable; "
                    + ("run as Administrator)" if "disks" in blocked or "smart" in blocked
                       else "try running as Administrator, or the drive is behind a USB adapter)"))
    # Added in v0.3.0 (review): every Administrator-blocked reading and every missing core
    # section is listed as "can't tell", never left out (a gap must not read as healthy).
    for name in sorted(blocked - {"temperatures", "disks", "smart"}):
        gaps.append(f"{name.replace('_', ' ').capitalize()} readings (need Administrator: run as Administrator)")
    for name in missing_core_sections(s):
        if name not in blocked:
            gaps.append(f"{name.replace('_', ' ').capitalize()} (not collected; problems here can't be ruled out)")
    if any(f.id == "memory.low_total" for f in ctx.findings) and ctx.ram.possible is None:
        gaps.append("Whether RAM can be added (" + ctx.ram.summary.rstrip(".") + ")")
    return gaps


# Explained in v0.3.0: how the plan is ordered. Every plan rule adds PlanSteps, then one sort
# with a tuple key does the prioritising:
#     (not urgent, tier order, -impact)
# Python compares tuples left to right, and False sorts before True, so urgent
# steps (a failing drive) come first; then FREE (0) before CHEAP (1) before
# REPLACE (2); and inside a tier the higher impact wins (hence the minus sign).
# Python's sort is stable, so ties keep the order the rules ran in.
def build_plan(snapshot: SystemSnapshot, findings: list[Finding],
               recs: Optional[list[Recommendation]] = None) -> ImprovementPlan:
    """Build the ordered free -> cheap -> replace plan for this PC (advice only)."""
    if recs is None:
        from .recommend import recommend_upgrades
        recs = recommend_upgrades(snapshot, findings)
    ctx = PlanContext(snapshot=snapshot, findings=list(findings), recs=list(recs),
                      ram=compat.ram_upgrade(snapshot), ssd=compat.ssd_upgrade(snapshot),
                      battery=compat.battery_option(snapshot))
    steps: list[PlanStep] = []
    for fn in PLAN_RULES.values():
        steps.extend(fn(ctx))
    # Urgent first, then free -> cheap -> replace, then by impact (stable for ties).
    steps.sort(key=lambda st: (not st.urgent, _TIER_ORDER[st.tier] if not st.urgent else 0, -st.impact))
    for i, st in enumerate(steps, 1):
        st.order = i
    plan = ImprovementPlan(steps=steps, used_monitor=snapshot.monitor is not None, cant_tell=_cant_tell(ctx))
    free = len(plan.tier(PlanTier.FREE))
    cheap = len(plan.tier(PlanTier.CHEAP))
    # Changed in v0.3.0 (review): problems with no matching step are never summed up as
    # "nothing needed"; a worn (WARNING) drive isn't called "failing"; a plan with only
    # the replace step reads naturally.
    unplanned = [f for f in ctx.findings if f.is_problem]
    if not steps and unplanned:
        plan.headline = (f"No fix or upgrade steps to suggest, but {len(unplanned)} problem"
                         f"{'s were' if len(unplanned) != 1 else ' was'} found: see the findings below.")
    elif not steps:
        plan.nothing_needed = True
        plan.headline = ("Nothing needed: no slowdowns or faults were found that a fix or upgrade would help."
                         + (" Some things couldn't be checked (listed below), so this isn't a clean bill of health."
                            if plan.cant_tell else ""))
    elif any(st.urgent for st in steps):
        if any(st.urgent and st.id.startswith("urgent.replace_disk") for st in steps):
            plan.headline = "Back up first: a drive is failing. Then replace it before anything else."
        else:
            plan.headline = "Back up first: a drive shows signs of wear. Then plan to replace it."
    else:
        parts = [f"{free} free fix{'es' if free != 1 else ''}"] if free else []
        if cheap:
            parts.append(f"{cheap} upgrade{'s' if cheap != 1 else ''} worth buying")
        if plan.tier(PlanTier.REPLACE):
            parts.append("a new PC only if that isn't enough" if parts else "a new PC: upgrades can't fix the "
                         "main bottleneck")
        plan.headline = "Start with " + ", then ".join(parts) + "."
    # Added in v0.3.0 (review): the steps are written for Windows (menus, tools); say so elsewhere.
    system = (snapshot.os.system if snapshot.os else None) or ""
    if steps and system and system.lower() not in ("windows", "unknown"):
        plan.headline += f" (Menu paths are for Windows; on {_os_name(snapshot)} use the matching settings.)"
    return plan


__all__ = ["PlanTier", "PlanStep", "ImprovementPlan", "PlanContext", "PLAN_RULES", "plan_rule", "build_plan",
           "TIER_TITLE"]
