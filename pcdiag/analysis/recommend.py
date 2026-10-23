# pcdiag/analysis/recommend.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): OS-neutral wording in reasons.
# Changed in v0.2.0: sustained memory/disk-busy findings count as evidence.
"""Upgrade recommender: which purchase would actually help this PC?

``recommend_upgrades`` reads the snapshot plus the findings from ``analyze``
and returns hardware recommendations ranked by expected impact.

Ranking logic (higher ``expected_impact`` = do it first)
-------------------------------------------------------
1. Data-safety purchases come first and are marked ``urgent``: a drive that
   SMART reports as failing (impact 100) or degrading (85).
2. HDD -> SSD for the system drive (90): the biggest everyday speed-up per
   dollar on almost any older PC.
3. More RAM (75 when under 8 GB, 55 when 8-16 GB and filling up).
4. Bigger system drive (40), only after free cleanup has been suggested.
5. Battery replacement (35 when below 40% health, 15 when merely worn).
6. GPU upgrade (20) only with evidence the GPU is the bottleneck.

Free fixes (cleanup, startup trim, dusting, re-pasting) are never turned
into purchases here; they live in each Finding's ``fix`` text, and a
recommendation's ``reason`` says when to try them first. A healthy PC
therefore gets few or no recommendations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import DiskKind, Model, SystemSnapshot
from .findings import Finding

# Impact scores for each recommendation type (0-100). Kept as constants so
# the ranking is explicit and easy to retune in a later release.
IMPACT_REPLACE_FAILING_DISK = 100
IMPACT_REPLACE_DEGRADING_DISK = 85
IMPACT_HDD_TO_SSD = 90
IMPACT_RAM_LOW = 75
IMPACT_RAM_TIGHT = 55
IMPACT_BIGGER_DRIVE = 40
IMPACT_BATTERY_REPLACE = 35
IMPACT_BATTERY_WORN = 15
IMPACT_GPU = 20
# GPU counts as a bottleneck only if it is this busy during the scan.
GPU_BOTTLENECK_USAGE_PERCENT = 95.0


class CostTier(str, Enum):
    """Rough price bands (USD, 2026) so users can budget without exact quotes."""
    FREE = "free"
    LOW = "$"          # under ~$60: RAM stick, 500 GB SATA SSD, battery
    MEDIUM = "$$"      # ~$60-200: 1-2 TB SSD, 16-32 GB RAM kit
    HIGH = "$$$"       # $200+: graphics card, new machine


@dataclass
class Recommendation(Model):
    """One suggested upgrade or replacement."""

    title: str = ""
    expected_impact: int = 0          # 0-100, higher = bigger improvement / more important
    reason: str = ""                  # plain-language why, citing the evidence
    cost_tier: CostTier = CostTier.LOW
    related_findings: list[str] = field(default_factory=list)  # Finding.id values
    urgent: bool = False              # data-safety issue: do before anything else
    rank: int = 0                     # 1 = do first; set by recommend_upgrades


def _ids(findings: list[Finding], prefix: str) -> list[str]:
    """Ids of findings whose id starts with ``prefix``."""
    return [f.id for f in findings if f.id.startswith(prefix)]


def _first(findings: list[Finding], fid: str) -> Finding | None:
    """First finding with exactly this id, or None."""
    return next((f for f in findings if f.id == fid), None)


def recommend_upgrades(snapshot: SystemSnapshot, findings: list[Finding]) -> list[Recommendation]:
    """Return upgrade recommendations ranked by expected impact (best first).

    Recommendations are only made from findings (problems), never from
    missing data, so a snapshot full of ``None`` produces no purchases.
    """
    recs: list[Recommendation] = []

    # --- 1. Failing / degrading drives: protect data first -----------------
    for f in findings:
        if f.id not in ("storage.smart_failing", "storage.smart_warning"):
            continue
        failing = f.id == "storage.smart_failing"
        drive = f.evidence.get("drive", "the drive")
        recs.append(Recommendation(
            title=f"{'Replace' if failing else 'Plan to replace'} failing drive {drive}",
            expected_impact=IMPACT_REPLACE_FAILING_DISK if failing else IMPACT_REPLACE_DEGRADING_DISK,
            reason=("Back up this drive first. " + (
                "It reports imminent failure, so data loss could happen any time. A failing drive also "
                "causes freezes as it retries reads." if failing else
                "It shows bad sectors or heavy wear; drives in this state often fail within months.")
                + " Replace it with an SSD of similar or larger size."),
            cost_tier=CostTier.MEDIUM, related_findings=[f.id], urgent=True))

    # --- 2. HDD system drive -> SSD ----------------------------------------
    hdd = _first(findings, "storage.hdd_system_drive")
    if hdd:
        sd = snapshot.system_drive
        # Size the SSD to fit the data comfortably; 500 GB SATA SSDs are cheap.
        need_gb = sd.used_gb if sd and sd.used_gb is not None else None
        size_hint = ("a 1 TB SSD" if need_gb and need_gb > 350 else "a 500 GB SSD")
        cost = CostTier.MEDIUM if need_gb and need_gb > 350 else CostTier.LOW
        # Changed in v0.2.0: the over-time disk-busy finding counts as evidence too.
        related = ([hdd.id] + _ids(findings, "storage.disk_busy") + _ids(findings, "storage.disk_busy_sustained")
                   + _ids(findings, "storage.system_disk_full"))
        recs.append(Recommendation(
            title="Replace the system hard drive with an SSD",
            expected_impact=IMPACT_HDD_TO_SSD,
            # Changed in v0.1.0: OS-neutral wording.
            reason=(f"The operating system runs from a hard drive. Moving it to {size_hint} typically makes boot and app "
                    "launches several times faster; it is the single best upgrade for an older PC. "
                    "Clone the existing drive so nothing needs reinstalling."),
            cost_tier=cost, related_findings=related))

    # --- 3. RAM -------------------------------------------------------------
    low_ram = _first(findings, "memory.low_total")
    # Changed in v0.2.0: when monitor data exists, "memory.sustained"
    # replaces "memory.pressure", so either one counts as memory pressure.
    pressure = _first(findings, "memory.sustained") or _first(findings, "memory.pressure")
    if low_ram and low_ram.is_problem:
        m = snapshot.memory
        slot_text = ""
        if m and m.slots_used is not None and m.slots_total is not None:
            slot_text = (f" {m.slots_total - m.slots_used} of {m.slots_total} slots are free, so you can add a stick"
                         if m.slots_used < m.slots_total else " All slots are full, so replace the sticks with larger ones")
            slot_text += f" (match the type: {m.type})." if m and m.type else "."
        recs.append(Recommendation(
            title="Add more RAM (aim for 16 GB, at least 8 GB)",
            expected_impact=IMPACT_RAM_LOW,
            reason=("With this little memory, the system constantly swaps to disk." + slot_text
                    + " Laptops with soldered RAM may not be upgradable: check the model first."),
            cost_tier=CostTier.LOW,
            related_findings=[low_ram.id] + ([pressure.id] if pressure and pressure.is_problem else [])))
    elif (low_ram or (pressure and pressure.is_problem)):
        # 8-16 GB machine that's filling up, or high pressure on a big machine.
        total = snapshot.memory.total_gb if snapshot.memory else None
        if total is not None and total < 16:
            recs.append(Recommendation(
                title="Upgrade RAM to 16 GB",
                expected_impact=IMPACT_RAM_TIGHT,
                reason=("Memory fills up during normal use. Try the free fixes first (fewer tabs, "
                        "trim startup apps); upgrade if it keeps happening."),
                cost_tier=CostTier.LOW,
                related_findings=[f.id for f in (low_ram, pressure) if f]))

    # --- 4. Bigger system drive (only when not already moving HDD->SSD) -----
    full = _first(findings, "storage.system_disk_full")
    if full and not hdd:
        recs.append(Recommendation(
            title="Get a larger system SSD (after cleanup)",
            expected_impact=IMPACT_BIGGER_DRIVE,
            reason=("The system drive is nearly full. Free cleanup comes first (Disk Cleanup, uninstall unused "
                    "apps, move media). If it is still above 85% afterwards, a larger SSD is worth it."),
            cost_tier=CostTier.MEDIUM, related_findings=[full.id]))

    # --- 5. Battery ---------------------------------------------------------
    bat_replace = _first(findings, "battery.replace")
    bat_worn = _first(findings, "battery.worn")
    if bat_replace or bat_worn:
        f = bat_replace or bat_worn
        recs.append(Recommendation(
            title="Replace the laptop battery" if bat_replace else "Consider a new battery (optional)",
            expected_impact=IMPACT_BATTERY_REPLACE if bat_replace else IMPACT_BATTERY_WORN,
            reason=(f"Battery holds {f.evidence.get('health_percent')}% of its original charge. "
                    + ("That's below the 40% replace-it rule of thumb." if bat_replace else
                       "Only worth it if you need longer unplugged runtime; it won't make the PC faster.")),
            cost_tier=CostTier.LOW, related_findings=[f.id]))

    # --- 6. GPU (needs evidence it's the bottleneck) ------------------------
    for g in snapshot.gpus:
        if (g.is_integrated is False and g.usage_percent is not None
                and g.usage_percent >= GPU_BOTTLENECK_USAGE_PERCENT):
            recs.append(Recommendation(
                title=f"Consider a faster graphics card (current: {g.name or 'unknown'})",
                expected_impact=IMPACT_GPU,
                reason=("The graphics card was fully loaded during the scan. Only worth upgrading if games or "
                        "creative apps are too slow; update the driver and check temperatures first."),
                cost_tier=CostTier.HIGH, related_findings=_ids(findings, "gpu.") + _ids(findings, "thermal.gpu")))

    # Rank: urgent (data safety) first, then by impact; stable for ties.
    recs.sort(key=lambda r: (not r.urgent, -r.expected_impact))
    for i, r in enumerate(recs, start=1):
        r.rank = i
    return recs
