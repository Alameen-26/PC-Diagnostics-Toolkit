"""Review fixes for v0.3.0 (plan, compat, report plan sections).

Added in v0.3.0 (review). Each test pins one bug found while reviewing the
release before it merged: crashes on missing readings, "Windows" wording on
other systems, wrong SSD/drive advice, misleading headlines, double-reporting
in the reports, and the CPU temperature card ignoring ``is_cpu``.
"""
from __future__ import annotations

from pcdiag.analysis import analyze, compat, recommend_upgrades
from pcdiag.analysis import plan as plan_mod
from pcdiag.analysis.findings import Category, Finding, Severity
from pcdiag.analysis.plan import build_plan
from pcdiag.fixtures import failing_disk_machine, healthy_gaming_pc, slow_old_laptop
from pcdiag.models import (BatteryInfo, DeviceInfo, DiskInfo, DiskKind, MemoryModule, SmartHealth, SmartStatus,
                           TemperatureReading)
from pcdiag.report import summary
from pcdiag.report.html import render_html
from pcdiag.report.markdown import render_markdown


def _plan(snap, extra=()):
    f = analyze(snap) + list(extra)
    return build_plan(snap, f, recommend_upgrades(snap, f))


def _ids(plan):
    return [st.id for st in plan.steps]


# --- None-safe evidence ---------------------------------------------------------------

def test_cleanup_with_missing_total_doesnt_crash():
    """A full-disk finding with total_gb=None still gives a cleanup step ('?' for the gap)."""
    snap = healthy_gaming_pc()
    bad = Finding(id="storage.system_disk_full", title="System drive is almost full", severity=Severity.WARNING,
                  category=Category.STORAGE, evidence={"drive": "C:\\", "used_percent": None, "free_gb": None,
                                                       "total_gb": None})
    plan = _plan(snap, [bad])
    step = next(st for st in plan.steps if st.id == "free.cleanup")
    assert "?" in step.evidence[0]


def test_driver_and_uptime_with_missing_numbers():
    """age_days / uptime_hours = None -> plain wording, no crash, no '0.0 years'."""
    snap = healthy_gaming_pc()
    extra = [Finding(id="gpu.driver_outdated", title="Old driver", severity=Severity.WARNING, category=Category.GPU,
                     evidence={"gpu": "NVIDIA GeForce", "driver_version": "1", "driver_date": None,
                               "age_days": None}),
             Finding(id="system.long_uptime", title="Not restarted in a long time", severity=Severity.INFO,
                     category=Category.SYSTEM, evidence={"uptime_hours": None})]
    plan = _plan(snap, extra)
    drv = next(st for st in plan.steps if st.id == "free.driver")
    assert "age unknown" in drv.evidence[0]
    upd = next(st for st in plan.steps if st.id == "free.updates")
    assert "Not restarted in a long time" in upd.evidence


def test_sustained_line_with_missing_threshold():
    """_sustained_line never raises on None values."""
    f = Finding(id="x", title="t", severity=Severity.WARNING, category=Category.CPU,
                evidence={"duration_s": 300, "threshold": None, "pct_above_threshold": None, "unit": "%"})
    assert "above ?%" in plan_mod._sustained_line(f, "CPU use")


def test_plan_failure_is_shown_not_hidden(monkeypatch):
    """If the planner crashes, the report says so instead of silently dropping the section."""
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(plan_mod, "build_plan", boom)
    snap = slow_old_laptop()
    f = analyze(snap)
    plan = summary.improvement_plan(snap, f, recommend_upgrades(snap, f))
    assert plan is not None and not plan.nothing_needed and plan.steps == []
    assert "couldn't be built" in plan.headline
    html = render_html(snap, f, recommend_upgrades(snap, f))
    assert "couldn&#x27;t be built" in html or "couldn't be built" in html


# --- OS wording ---------------------------------------------------------------------

def test_linux_plan_doesnt_say_windows_drive():
    """On Linux the cleanup / SSD steps don't claim Windows; the headline notes the menus are Windows'."""
    snap = slow_old_laptop()
    snap.os.system = "Linux"
    plan = _plan(snap)
    text = " ".join([st.title for st in plan.steps] + [e for st in plan.steps for e in st.evidence])
    assert "Windows drive" not in text and "Windows runs" not in text
    assert "Linux runs from a hard disk" in text
    assert "Menu paths are for Windows" in plan.headline


def test_windows_plan_has_no_os_note():
    """Windows plans keep the plain headline."""
    assert "Menu paths" not in _plan(slow_old_laptop()).headline


# --- compat ---------------------------------------------------------------------------

def test_unknown_kind_on_nvme_bus_is_already_ssd():
    """Drive type not reported but the bus is NVMe -> it's an SSD; no SSD swap suggested."""
    snap = healthy_gaming_pc()
    d = snap.system_drive
    d.kind, d.bus_type = DiskKind.UNKNOWN, "NVMe"
    opt = compat.ssd_upgrade(snap)
    assert opt.needed is False


def test_unknown_kind_on_sata_explains_the_real_doubt():
    """Unknown drive type on SATA: summary says the type is unknown (not the M.2 slot)."""
    snap = slow_old_laptop()
    d = snap.system_drive
    d.kind, d.bus_type = DiskKind.UNKNOWN, "SATA"
    opt = compat.ssd_upgrade(snap)
    assert opt.possible is None
    assert "drive type wasn't reported" in opt.summary and "M.2" not in opt.summary


def test_big_failing_drive_gets_same_size_replacement():
    """A failing 8 TB data disk is offered 8 TB, not 4 TB; 4 TB SSDs aren't priced as 2 TB."""
    snap = failing_disk_machine()
    d = next(x for x in snap.disks if not x.is_system_drive)
    d.total_gb, d.used_gb = 7452.0, 5000.0
    opt = compat.replace_disk(snap, d)
    assert opt.part.startswith("8 TB") and opt.cost_low == compat.HDD_PRICES[8000][0]
    ssd = DiskInfo(device="C:\\", mountpoint="C:\\", total_gb=3726.0, used_gb=3000.0, kind=DiskKind.NVME,
                   bus_type="NVMe", is_system_drive=True)
    opt = compat.replace_disk(snap, ssd)
    assert opt.part.startswith("4 TB") and opt.cost_low == compat.SSD_PRICES["NVMe"][4000][0]


def test_full_slot_kit_uses_real_stick_sizes():
    """3 full slots: the kit is power-of-two sticks (3 x 8 GB), never '3 x 5 GB'."""
    snap = slow_old_laptop()
    m = snap.memory
    m.slots_total = m.slots_used = 3
    m.max_capacity_gb = None
    m.total_gb = 6
    opt = compat.ram_upgrade(snap)
    assert "3 × 8 GB" in opt.part and "5 GB" not in opt.part


def test_desktop_battery_is_not_a_laptop_battery():
    """A battery reported on a desktop (a UPS) never gets 'fits this laptop' or a battery step."""
    snap = failing_disk_machine()  # desktop chassis
    snap.battery = BatteryInfo(percent=90, plugged_in=True, wear_percent=50)
    opt = compat.battery_option(snap)
    assert opt.needed is False and "laptop" not in opt.summary
    assert "cheap.battery" not in _ids(_plan(snap))


# --- plan headline / steps ----------------------------------------------------------

def test_worn_drive_headline_doesnt_say_failing():
    """SMART WARNING only: back up first, but the headline says 'wear', not 'failing'."""
    snap = failing_disk_machine()
    next(d for d in snap.disks if d.smart and d.smart.status is SmartStatus.FAILING).smart.status = SmartStatus.WARNING
    plan = _plan(snap)
    assert _ids(plan)[0].startswith("urgent.backup")
    assert "wear" in plan.headline and "failing" not in plan.headline


def test_bad_sectors_with_ok_status_get_a_backup_step():
    """smart_warning from reallocated sectors (status still OK) -> backup step, not 'nothing needed'."""
    snap = healthy_gaming_pc()
    d = snap.system_drive
    d.smart = SmartHealth(status=SmartStatus.OK, reallocated_sectors=24)
    plan = _plan(snap)
    assert any(i.startswith("urgent.backup") for i in _ids(plan))
    assert not plan.nothing_needed


def test_two_bad_disks_each_point_to_their_own_finding():
    """Two bad drives: each backup step cites that drive's finding, not the first one found."""
    snap = failing_disk_machine()
    sysd = snap.system_drive
    sysd.smart = SmartHealth(status=SmartStatus.WARNING)
    findings = analyze(snap)
    plan = build_plan(snap, findings, recommend_upgrades(snap, findings))
    backups = [st for st in plan.steps if st.id.startswith("urgent.backup")]
    assert len(backups) == 2
    assert {tuple(st.related_findings) for st in backups} == {("storage.smart_failing",), ("storage.smart_warning",)}


def test_power_plan_doesnt_claim_plugged_in_when_unknown():
    """plugged_in=None: evidence says the charger state is unknown."""
    snap = slow_old_laptop()
    snap.battery.plugged_in = None
    step = next((st for st in _plan(snap).steps if st.id == "free.power_plan"), None)
    assert step is not None and "while plugged in" not in step.evidence[0]


def test_problems_without_steps_are_not_nothing_needed():
    """A WARNING the planner has no step for must not produce 'Nothing needed'."""
    snap = healthy_gaming_pc()
    odd = Finding(id="network.odd", title="Something odd", severity=Severity.WARNING, category=Category.SYSTEM)
    plan = _plan(snap, [odd])
    assert plan.steps == [] and not plan.nothing_needed
    assert not plan.headline.startswith("Nothing needed")


def test_admin_blocked_and_missing_sections_are_cant_tell():
    """Administrator errors and missing core sections are listed under 'can't check'."""
    snap = healthy_gaming_pc()
    snap.errors.append("battery: Access denied. Run as Administrator to read this.")
    snap.memory = None
    gaps = " ".join(_plan(snap).cant_tell)
    assert "Battery readings (need Administrator" in gaps
    assert "Memory (not collected" in gaps


def test_replace_only_headline_reads_naturally():
    """A plan with only the replace step doesn't say 'Start with a new PC only if that isn't enough'."""
    plan = plan_mod.ImprovementPlan()
    snap = healthy_gaming_pc()
    f = analyze(snap)
    real = build_plan(snap, f, [])
    assert real.nothing_needed and plan.headline == ""
    step = plan_mod.PlanStep(id="replace.pc", tier=plan_mod.PlanTier.REPLACE, title="New PC", impact=1)
    orig = dict(plan_mod.PLAN_RULES)
    try:
        plan_mod.PLAN_RULES.clear()
        plan_mod.PLAN_RULES["only"] = lambda ctx: [step]
        got = build_plan(snap, f, [])
    finally:
        plan_mod.PLAN_RULES.clear()
        plan_mod.PLAN_RULES.update(orig)
    assert "only if that isn't enough" not in got.headline and "new PC" in got.headline


# --- reports: no double-reporting --------------------------------------------------

def test_what_to_do_skips_items_in_the_plan():
    """Fixes/upgrades covered by plan steps aren't listed again under 'What to do'."""
    snap = slow_old_laptop()
    f = analyze(snap)
    recs = recommend_upgrades(snap, f)
    plan = build_plan(snap, f, recs)
    fixes, upgrades, filtered = summary.what_to_do(f, recs, plan)
    covered = summary.plan_covered_ids(plan)
    assert filtered and covered
    assert all(x.id not in covered for x, _ in fixes)
    assert all(not (r.related_findings and r.related_findings[0] in covered) for r in upgrades)
    md = render_markdown(snap, f, recs, plan=plan)
    assert summary.PLAN_DEDUP_NOTE in md and "### Free fixes first" in md
    what = md[md.index("## What to do"):md.index("## Findings")] if "## Findings" in md else md
    assert "Startup" not in what or "startup.too_many" not in covered


def test_soldered_ram_not_suggested_under_the_plan():
    """Plan says RAM can't be added (soldered); 'What to do' doesn't suggest buying RAM."""
    snap = slow_old_laptop()
    m = snap.memory
    m.soldered, m.form_factor, m.slots_used, m.slots_total = True, "soldered", None, None
    m.modules = [MemoryModule(capacity_gb=4, type="LPDDR3", form_factor="soldered")]
    f = analyze(snap)
    recs = recommend_upgrades(snap, f)
    plan = build_plan(snap, f, recs)
    assert "replace.pc" in _ids(plan) and "cheap.ram" not in _ids(plan)
    _, upgrades, _ = summary.what_to_do(f, recs, plan)
    assert not any("RAM" in r.title for r in upgrades)


def test_no_plan_keeps_old_lists():
    """Without a plan (or with no steps) the lists are exactly free_fixes / recs."""
    snap = slow_old_laptop()
    f = analyze(snap)
    recs = recommend_upgrades(snap, f)
    fixes, upgrades, filtered = summary.what_to_do(f, recs, None)
    assert not filtered and fixes == summary.free_fixes(f) and upgrades == recs


def test_html_dedup_note_and_escaping():
    """HTML shows the note once and still escapes plan text."""
    snap = slow_old_laptop()
    snap.gpus[0].name = "<script>x</script> GPU"
    f = analyze(snap)
    html = render_html(snap, f, recommend_upgrades(snap, f))
    assert html.count(summary.PLAN_DEDUP_NOTE.replace("'", "&#x27;")) == 1
    assert "<script>x</script>" not in html


# --- temperature card ---------------------------------------------------------------

def test_temperature_card_uses_is_cpu_flag():
    """Card and rule agree: a hot non-CPU sensor (is_cpu=False) is ignored; a confirmed CPU sensor counts."""
    snap = healthy_gaming_pc()
    snap.temperatures = [TemperatureReading(label="CPU-adjacent hotspot", current_c=97.0, is_cpu=False),
                         TemperatureReading(label="CCD1", current_c=52.0, is_cpu=True)]
    card = next(c for c in summary.metric_cards(snap) if c.name == "Temperature")
    assert card.value.startswith("52")
