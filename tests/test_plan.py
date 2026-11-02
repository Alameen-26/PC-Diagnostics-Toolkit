"""Improvement plan (pcdiag.analysis.plan): ordering, each step builder, fixtures.

Added in v0.3.0.
"""
from __future__ import annotations

import json

import pytest

from pcdiag.analysis import analyze, recommend_upgrades
from pcdiag.analysis.findings import Category, Finding, Severity
from pcdiag.analysis.plan import PLAN_RULES, ImprovementPlan, PlanTier, build_plan
from pcdiag.fixtures import ALL_FIXTURES, failing_disk_machine, healthy_gaming_pc, slow_old_laptop, with_monitor
from pcdiag.models import MemoryModule, SoftwareState


def plan_for(snap):
    """Analyze + plan in one go."""
    f = analyze(snap)
    return build_plan(snap, f, recommend_upgrades(snap, f))


def ids(plan):
    """Step ids in order."""
    return [s.id for s in plan.steps]


# --- The three fixtures ---------------------------------------------------------

@pytest.mark.parametrize("monitor", [False, True])
def test_slow_laptop_free_fixes_then_ssd_then_ram(monitor):
    """slow_old_laptop: all free fixes first, then the SSD, then RAM, as the first paid steps."""
    snap = with_monitor("slow_old_laptop") if monitor else slow_old_laptop()
    plan = plan_for(snap)
    tiers = [s.tier for s in plan.steps]
    first_paid = tiers.index(PlanTier.CHEAP)
    assert all(t is PlanTier.FREE for t in tiers[:first_paid])
    assert all(t is not PlanTier.FREE for t in tiers[first_paid:])
    assert ids(plan)[first_paid:first_paid + 2] == ["cheap.ssd", "cheap.ram"]
    assert not any(s.urgent for s in plan.steps)
    assert not plan.nothing_needed
    assert [s.order for s in plan.steps] == list(range(1, len(plan.steps) + 1))


def test_slow_laptop_covers_every_free_fix():
    """Startup apps, hogs, cleanup, driver, power plan, updates and cooling all appear."""
    got = set(ids(plan_for(slow_old_laptop())))
    assert {"free.startup", "free.hogs", "free.cleanup", "free.driver", "free.power_plan",
            "free.updates", "free.cooling"} <= got


@pytest.mark.parametrize("monitor", [False, True])
def test_healthy_pc_nothing_needed(monitor):
    """healthy_gaming_pc: no steps; headline says nothing is needed."""
    snap = with_monitor("healthy_gaming_pc") if monitor else healthy_gaming_pc()
    plan = plan_for(snap)
    assert plan.nothing_needed and plan.steps == []
    assert plan.headline.startswith("Nothing needed")


@pytest.mark.parametrize("monitor", [False, True])
def test_failing_disk_backup_then_replace_first(monitor):
    """failing_disk_machine: 1. back up (stop point), 2. replace the failing drive."""
    snap = with_monitor("failing_disk_machine") if monitor else failing_disk_machine()
    plan = plan_for(snap)
    first, second = plan.steps[0], plan.steps[1]
    assert first.id.startswith("urgent.backup") and first.urgent
    assert first.stop_point and "back up" in first.stop_point.lower()
    assert second.id.startswith("urgent.replace_disk") and second.urgent
    assert "D:" in first.title and "Back up first" in plan.headline


def test_monitor_evidence_used_when_present():
    """With a monitor session, evidence quotes the sustained stats (time above threshold)."""
    plan = plan_for(with_monitor("slow_old_laptop"))
    assert plan.used_monitor
    hogs = next(s for s in plan.steps if s.id == "free.hogs")
    assert any("monitored" in e for e in hogs.evidence)
    ssd = next(s for s in plan.steps if s.id == "cheap.ssd")
    assert any("Disk activity above" in e for e in ssd.evidence)


def test_plan_round_trips_json():
    """ImprovementPlan serialises and loads back."""
    plan = plan_for(slow_old_laptop())
    again = ImprovementPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert ids(again) == ids(plan)
    assert again.steps[0].tier is PlanTier.FREE


def test_plan_does_not_change_findings_or_score():
    """Building the plan adds no findings (health score unchanged)."""
    snap = slow_old_laptop()
    f = analyze(snap)
    before = [x.id for x in f]
    build_plan(snap, f)
    assert [x.id for x in f] == before


def test_every_fixture_plans_without_error():
    """All fixtures (and their monitor variants) produce a plan."""
    for name in ALL_FIXTURES:
        assert isinstance(plan_for(ALL_FIXTURES[name]()), ImprovementPlan)
        assert isinstance(plan_for(with_monitor(name)), ImprovementPlan)


def test_registry_has_all_builders():
    """Every step builder is registered."""
    assert {"backup_failing_disk", "startup_apps", "resource_hogs", "disk_cleanup", "graphics_driver",
            "power_plan", "windows_update", "cooling", "ssd_upgrade", "ram_upgrade", "bigger_drive",
            "battery", "gpu_upgrade", "replace_pc"} <= set(PLAN_RULES)


# --- Individual builders ----------------------------------------------------------

def test_startup_names_trial_antivirus():
    """Startup step names the offenders and suggests removing the trial antivirus."""
    st = next(s for s in plan_for(slow_old_laptop()).steps if s.id == "free.startup")
    assert any("McAfee Trial" in a and "Uninstall" in a for a in st.actions)


def test_hogs_never_disable_defender():
    """MsMpEng.exe is explained, never 'disable it'."""
    st = next(s for s in plan_for(slow_old_laptop()).steps if s.id == "free.hogs")
    text = " ".join(st.actions)
    assert "MsMpEng.exe" in text and "don't turn it off" in text
    assert "disable defender" not in text.lower()


def test_power_plan_only_when_plugged_in_or_desktop():
    """Power saver on battery is fine; plugged in it's a step."""
    snap = slow_old_laptop()
    assert "free.power_plan" in ids(plan_for(snap))
    snap.battery.plugged_in = False
    assert "free.power_plan" not in ids(plan_for(snap))


def test_power_plan_balanced_no_step():
    """Balanced plan -> no power step."""
    snap = slow_old_laptop()
    snap.software.power_plan = "Balanced"
    assert "free.power_plan" not in ids(plan_for(snap))


def test_windows_update_overdue_and_reboot():
    """Reboot pending + 135 days -> 'Restart and catch up'; neither -> only uptime restart or nothing."""
    snap = slow_old_laptop()
    st = next(s for s in plan_for(snap).steps if s.id == "free.updates")
    assert st.title.startswith("Restart and catch up")
    snap.software = SoftwareState(power_plan="Balanced", days_since_update=3, reboot_pending=False)
    st = next((s for s in plan_for(snap).steps if s.id == "free.updates"), None)
    assert st is None or st.title == "Restart the PC"


def test_driver_step_cites_driver_age():
    """GPU driver step shows the driver date and age."""
    st = next(s for s in plan_for(slow_old_laptop()).steps if s.id == "free.driver")
    assert "2016" in st.evidence[0] and "years old" in st.evidence[0]
    assert "Intel" in " ".join(st.actions)


def test_cleanup_target_free_space():
    """Cleanup step sets a free-space goal (15% of the drive)."""
    st = next(s for s in plan_for(slow_old_laptop()).steps if s.id == "free.cleanup")
    assert "70 GB" in st.title


def test_cooling_and_repaste_when_throttling():
    """Throttling CPU -> clean vents (free) and repaste (cheap, after cleaning)."""
    got = ids(plan_for(slow_old_laptop()))
    assert "free.cooling" in got and "cheap.repaste" in got
    assert got.index("free.cooling") < got.index("cheap.repaste")


def test_no_ram_step_when_soldered_suggests_replace_instead():
    """Soldered 4 GB RAM can't be upgraded -> no RAM step; 'replace' tier appears last."""
    snap = slow_old_laptop()
    snap.memory.soldered, snap.memory.form_factor = True, "soldered"
    snap.memory.modules = [MemoryModule(capacity_gb=4, type="LPDDR3", form_factor="soldered")]
    snap.memory.slots_used = snap.memory.slots_total = None
    plan = plan_for(snap)
    assert "cheap.ram" not in ids(plan)
    assert plan.steps[-1].id == "replace.pc" and plan.steps[-1].tier is PlanTier.REPLACE


def test_ram_cant_tell_is_said_not_guessed():
    """Unknown slots -> RAM step starts with 'Check first' and confidence low."""
    snap = slow_old_laptop()
    snap.memory.modules, snap.memory.slots_total, snap.memory.slots_used = [], None, None
    snap.memory.form_factor = snap.memory.soldered = None
    plan = plan_for(snap)
    st = next(s for s in plan.steps if s.id == "cheap.ram")
    assert st.actions[0].startswith("Check first") and st.confidence == "low"


def test_no_ssd_step_when_already_ssd():
    """Healthy NVMe PC never gets an SSD step."""
    assert "cheap.ssd" not in ids(plan_for(healthy_gaming_pc()))


def test_battery_step_never_on_desktop():
    """A desktop with a stray 'battery.worn' finding still gets no battery step."""
    snap = failing_disk_machine()
    f = analyze(snap) + [Finding(id="battery.worn", title="Battery is worn", severity=Severity.WARNING,
                                 category=Category.BATTERY)]
    assert "cheap.battery" not in ids(build_plan(snap, f, []))


def test_bigger_drive_only_for_full_ssd():
    """A full SSD system drive -> optional bigger SSD after cleanup."""
    snap = healthy_gaming_pc()
    d = snap.system_drive
    d.used_gb, d.free_gb, d.percent = d.total_gb * 0.96, d.total_gb * 0.04, 96.0
    got = ids(plan_for(snap))
    assert "free.cleanup" in got and "cheap.bigger_drive" in got
    assert got.index("free.cleanup") < got.index("cheap.bigger_drive")


def test_replace_pc_for_maxed_laptop_cpu():
    """Laptop CPU busy for a whole monitor window -> 'replace' step (CPU can't be upgraded)."""
    snap = with_monitor("slow_old_laptop")
    f = analyze(snap)
    cpu = next((x for x in f if x.id == "cpu.sustained"), None)
    assert cpu is not None
    cpu.severity = Severity.WARNING
    plan = build_plan(snap, f, [])
    assert plan.steps[-1].id == "replace.pc"


def test_cant_tell_lists_gaps():
    """failing_disk_machine has no CPU sensor and no driver date -> listed under 'can't check'."""
    plan = plan_for(failing_disk_machine())
    assert any("temperature" in x for x in plan.cant_tell)
    assert any("driver" in x for x in plan.cant_tell)


def test_recs_built_when_not_given():
    """build_plan(snapshot, findings) computes the recommendations itself."""
    snap = slow_old_laptop()
    assert ids(build_plan(snap, analyze(snap))) == ids(plan_for(snap))
