# pcdiag/troubleshoot.py
# Added in v0.3.0 (roadmap phase 5): ``pcdiag troubleshoot SYMPTOM``.
"""Step-by-step troubleshooting by symptom, with stop points.

Symptoms: ``slow-boot``, ``restarts``, ``freezes``, ``overheating``,
``disk-noise``. Each guide is an ordered list of :class:`TroubleStep`; a step
with ``stop`` set is a checkpoint ("Back up now") the user must handle before
going further. When a snapshot is available (live scan, ``--demo`` or
``--snapshot``), steps that match its findings are marked with what pcdiag saw
("pcdiag saw: ...") so the user can start where the evidence points.

Advice only: nothing here changes the system. Crash-log evidence for random
restarts (Windows Event Log) is deferred to v0.5.0; the guide points the user
to Reliability Monitor instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SYMPTOMS = {
    "slow-boot": "Slow to start up",
    "restarts": "Random restarts or shut-offs",
    "freezes": "Freezes or stops responding",
    "overheating": "Runs hot, loud fans, or slows down when busy",
    "disk-noise": "Clicking, grinding or buzzing from the drive",
}


@dataclass
class TroubleStep:
    """One check in a troubleshooting guide."""
    title: str
    detail: str
    stop: Optional[str] = None                 # checkpoint text, e.g. "STOP: back up now"
    finding_ids: tuple[str, ...] = ()           # findings that make this step relevant
    seen: list[str] = field(default_factory=list)  # filled from the snapshot's findings


BACKUP_STOP = "STOP: back up your important files now, before trying anything else."

GUIDES: dict[str, list[TroubleStep]] = {
    "slow-boot": [
        TroubleStep("Count the startup apps",
                    "Task Manager > Startup apps: disable what you don't need at sign-in (keep security software).",
                    finding_ids=("startup.too_many",)),
        TroubleStep("Check the drive type",
                    "Windows on a hard disk (HDD) takes minutes to boot; an SSD takes seconds. See 'pcdiag plan'.",
                    finding_ids=("storage.hdd_system_drive", "storage.disk_busy", "storage.disk_busy_sustained")),
        TroubleStep("Finish pending updates",
                    "Settings > Windows Update. A pending restart makes every boot slower until it's done.",
                    finding_ids=("system.long_uptime",)),
        TroubleStep("Free up space", "Keep at least 15% of the Windows drive free (Settings > System > Storage).",
                    finding_ids=("storage.system_disk_full",)),
        TroubleStep("Check the drive's health",
                    "A failing drive retries reads and makes boot crawl.",
                    stop=BACKUP_STOP, finding_ids=("storage.smart_failing", "storage.smart_warning")),
    ],
    "restarts": [
        TroubleStep("Check the drive's health first",
                    "Restarts plus a failing drive can mean lost files.",
                    stop=BACKUP_STOP, finding_ids=("storage.smart_failing", "storage.smart_warning")),
        TroubleStep("Look at Reliability Monitor",
                    "Start > type 'reliability' > View reliability history. Red X marks show what crashed and when "
                    "(pcdiag reads crash logs from v0.5.0)."),
        TroubleStep("Rule out overheating",
                    "A PC that shuts off under load is often too hot. Clean the vents; run 'pcdiag troubleshoot overheating'.",
                    finding_ids=("thermal.cpu", "thermal.cpu_sustained")),
        TroubleStep("Check the battery and charger (laptops)",
                    "A worn battery can cut out suddenly when unplugged. Try it on the charger.",
                    finding_ids=("battery.replace", "battery.worn")),
        TroubleStep("Update drivers and Windows",
                    "Graphics drivers are the most common cause of crash restarts.",
                    finding_ids=("gpu.driver_outdated",)),
        TroubleStep("Test the memory",
                    "Start > 'Windows Memory Diagnostic' > Restart now and check. Errors mean a RAM stick needs replacing."),
        TroubleStep("Still restarting?",
                    "If it restarts with the case open/cool and drivers updated, suspect the power supply (desktops) "
                    "and ask a repair shop.", stop="STOP: make sure your backup is current before more testing."),
    ],
    "freezes": [
        TroubleStep("Check memory pressure",
                    "Freezes with lots of tabs/apps open usually mean RAM is full. Close apps; see 'pcdiag plan'.",
                    finding_ids=("memory.pressure", "memory.sustained", "memory.low_total")),
        TroubleStep("Check the disk",
                    "A maxed-out hard disk freezes everything. Run 'pcdiag scan --monitor 300' to see if it stays busy.",
                    finding_ids=("storage.disk_busy", "storage.disk_busy_sustained", "storage.hdd_system_drive")),
        TroubleStep("Check the drive's health", "Freezes plus clicking or slow file opens can mean a failing drive.",
                    stop=BACKUP_STOP, finding_ids=("storage.smart_failing", "storage.smart_warning")),
        TroubleStep("Find the program",
                    "Task Manager > Processes, sort by CPU and Memory while it's slow.",
                    finding_ids=("processes.heavy", "cpu.busy", "cpu.sustained")),
        TroubleStep("Update the graphics driver", "Screen freezes are often graphics-driver bugs.",
                    finding_ids=("gpu.driver_outdated",)),
        TroubleStep("Rule out heat", "Freezes when busy can be the CPU throttling.",
                    finding_ids=("thermal.cpu", "thermal.cpu_sustained")),
    ],
    "overheating": [
        TroubleStep("Check what pcdiag read",
                    "pcdiag shows CPU temperature when a sensor is readable; LibreHardwareMonitor running in the "
                    "background makes the reading more reliable.",
                    finding_ids=("thermal.cpu", "thermal.cpu_sustained", "thermal.unknown")),
        TroubleStep("Clean the vents and fans",
                    "Shut down, unplug, then short bursts of compressed air through the vents (hold the fan still)."),
        TroubleStep("Give it room to breathe", "Hard flat surface; nothing blocking the vents; not on a bed or lap."),
        TroubleStep("Find what keeps the CPU busy", "Task Manager > Processes, sort by CPU.",
                    finding_ids=("cpu.busy", "cpu.sustained", "processes.heavy")),
        TroubleStep("Use the Balanced power plan", "High-performance plans keep the CPU hotter for little gain."),
        TroubleStep("Still hot after cleaning?",
                    "Old thermal paste (5+ years) can be replaced for $5-15 DIY or at a repair shop.",
                    stop="STOP: if it shuts itself off from heat, stop heavy use until it's cleaned."),
    ],
    "disk-noise": [
        TroubleStep("Back up now",
                    "Clicking, grinding or repeated spin-up sounds from a hard disk can mean it is about to fail.",
                    stop=BACKUP_STOP),  # always a stop point: the noise itself is the evidence
        TroubleStep("Check the drive's SMART health",
                    "Run 'pcdiag scan' as Administrator; reallocated or pending sectors confirm wear.",
                    finding_ids=("storage.smart_failing", "storage.smart_warning", "storage.smart_unknown")),
        TroubleStep("Don't run repairs yet",
                    "Skip chkdsk /r and defrag until the backup is done: heavy reads can finish off a failing drive."),
        TroubleStep("Replace the drive",
                    "If the noise continues or SMART shows problems, replace it (an SSD is silent). See 'pcdiag plan'."),
        TroubleStep("Normal sounds",
                    "Soft ticking during file access is normal for hard disks; SSDs make no noise at all, so a noise "
                    "from an SSD-only PC is usually a fan."),
    ],
}


def guide(symptom: str, findings: Optional[list] = None) -> list[TroubleStep]:
    """The steps for ``symptom``, annotated with matching findings when given."""
    if symptom not in GUIDES:
        raise KeyError(f"unknown symptom {symptom!r}; choose from {', '.join(SYMPTOMS)}")
    steps = [TroubleStep(s.title, s.detail, s.stop, s.finding_ids) for s in GUIDES[symptom]]
    by_id: dict[str, list] = {}
    for f in findings or []:
        by_id.setdefault(f.id, []).append(f)
    for st in steps:
        for fid in st.finding_ids:
            for f in by_id.get(fid, []):
                if getattr(f, "is_problem", False) or fid == "thermal.unknown":
                    st.seen.append(f.title)
    return steps


def guide_lines(symptom: str, findings: Optional[list] = None, scanned: bool = False) -> list[str]:
    """ASCII lines for the terminal."""
    steps = guide(symptom, findings)
    out = [f"Troubleshooting: {SYMPTOMS[symptom]}", "Advice only: pcdiag doesn't change anything.", ""]
    if not scanned:
        out += ["(Tip: add --scan to check this PC first, so matching steps are marked.)", ""]
    first_seen = next((i for i, s in enumerate(steps) if s.seen), None)
    if first_seen is not None:
        # An unconditional stop point earlier in the guide (e.g. "Back up now") still comes first.
        first_seen = next((i for i, s in enumerate(steps[:first_seen]) if s.stop and not s.finding_ids), first_seen)
    for i, st in enumerate(steps, 1):
        star = "  <- start here" if first_seen is not None and i - 1 == first_seen else ""
        out.append(f"{i}. {st.title}{star}")
        out.append(f"   {st.detail}")
        for s in st.seen:
            out.append(f"   pcdiag saw: {s}")
        if st.stop and (st.seen or not st.finding_ids):
            out.append(f"   >> {st.stop}")
        elif st.stop:
            out.append(f"   >> If this applies: {st.stop}")
    return out


__all__ = ["SYMPTOMS", "GUIDES", "TroubleStep", "guide", "guide_lines"]
