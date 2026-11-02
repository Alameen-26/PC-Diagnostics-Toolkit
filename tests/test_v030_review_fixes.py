"""Tests for the six follow-up fixes made after the v0.3.0 review.

Added in v0.3.0 (review). One section per fix:
1. the monitor only logs CPU sensors as the CPU temperature,
2. no RAM upgrade is suggested when the RAM is soldered (every output),
3. the cooling plan reads throttling from a field, not from wording,
4. the Markdown report escapes Markdown characters,
5. the battery option doesn't claim a new battery "fits" when we can't tell,
6. the changelog and release notes list the review fixes.
"""
from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

from pcdiag.analysis import analyze, compat, recommend_upgrades
from pcdiag.analysis.findings import Category, Confidence, Finding, Severity
from pcdiag.analysis.plan import build_plan
from pcdiag.analysis.sustained import resolve_overlaps
from pcdiag.collectors import _shell
from pcdiag.fixtures import healthy_gaming_pc, slow_old_laptop
from pcdiag.models import MemoryModule, MonitorSample
from pcdiag.monitor import Monitor, is_cpu_sensor
from pcdiag.report.markdown import md_escape, render_markdown
from pcdiag.terminal import analysis_lines

ROOT = Path(__file__).resolve().parents[1]
Temp = namedtuple("Temp", "label current high critical")


# --- 1. Monitor: only CPU sensors count as the CPU temperature ----------------------

class _SensorPsutil:
    """Just enough psutil for Monitor._sample_cpu_temp."""

    def __init__(self, groups):
        self.groups = groups

    def sensors_temperatures(self):
        return self.groups


def _cpu_temp(monkeypatch, groups):
    monkeypatch.setattr(_shell, "is_windows", lambda: False)
    mon = Monitor(ps=_SensorPsutil(groups))
    sample = MonitorSample()
    mon._sample_cpu_temp(sample)
    return sample


def test_monitor_ignores_gpu_and_nvme_when_no_cpu_sensor(monkeypatch):
    """Only a GPU and an NVMe drive report temperatures: the CPU reading stays empty."""
    s = _cpu_temp(monkeypatch, {"amdgpu": [Temp("edge", 88.0, None, None)],
                                "nvme": [Temp("Composite", 71.0, None, None)],
                                "acpitz": [Temp("", 50.0, None, None)]})
    assert s.cpu_temp_c is None
    assert any("CPU temperature not exposed" in n for n in s.notes)


def test_monitor_uses_cpu_sensor_and_skips_hotter_others(monkeypatch):
    """A CPU driver group wins even when a non-CPU sensor is hotter."""
    s = _cpu_temp(monkeypatch, {"k10temp": [Temp("Tctl", 61.0, None, None)],
                                "nvme": [Temp("Composite", 80.0, None, None)]})
    assert s.cpu_temp_c == 61.0


def test_monitor_accepts_cpu_labels_from_other_drivers(monkeypatch):
    """An unknown driver group with a CPU label (Package id 0) still counts."""
    s = _cpu_temp(monkeypatch, {"some_board": [Temp("Package id 0", 66.0, None, None),
                                               Temp("SYSTIN", 90.0, None, None)]})
    assert s.cpu_temp_c == 66.0
    assert is_cpu_sensor("coretemp", "") and is_cpu_sensor("x", "Tdie") and is_cpu_sensor("x", "CPU")
    assert not is_cpu_sensor("nvme", "CPU") and not is_cpu_sensor("x", "") and not is_cpu_sensor("x", "edge")


# --- 2. Soldered RAM: no RAM upgrade in the recommender itself ----------------------

def _soldered_laptop(slots_total=None, slots_used=None):
    snap = slow_old_laptop()
    m = snap.memory
    m.soldered, m.form_factor, m.slots_used, m.slots_total = True, "soldered", slots_used, slots_total
    m.modules = [MemoryModule(capacity_gb=4, type="LPDDR3", form_factor="soldered")]
    return snap


def test_recommender_skips_ram_when_soldered():
    """The terminal and JSON outputs use recommend_upgrades directly, so the filter must be there."""
    before = recommend_upgrades(slow_old_laptop(), analyze(slow_old_laptop()))
    assert any("RAM" in r.title for r in before)  # the normal laptop still gets RAM advice
    snap = _soldered_laptop()
    recs = recommend_upgrades(snap, analyze(snap))
    assert not any("RAM" in r.title for r in recs)
    assert not any("RAM" in line for line in analysis_lines([], recs))           # terminal
    as_json = json.loads(json.dumps([r.to_dict() for r in recs]))                 # JSON
    assert not any("RAM" in r["title"] for r in as_json)
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))


def test_recommender_keeps_ram_when_soldered_board_has_a_free_slot():
    """Some soldered memory plus an empty slot can still take a stick."""
    snap = _soldered_laptop(slots_total=1, slots_used=0)
    snap.memory.soldered = None  # mixed board: not fully soldered
    snap.memory.modules = [MemoryModule(capacity_gb=4, type="DDR4", form_factor="SODIMM")]
    recs = recommend_upgrades(snap, analyze(snap))
    assert any("RAM" in r.title for r in recs)


# --- 3. Cooling plan: throttling from a field, not from words -----------------------

def _sustained_hot(superseded):
    return Finding(id="thermal.cpu_sustained", title="CPU ran hot for most of the window",
                   severity=Severity.WARNING, category=Category.THERMAL, cause="", fix="",
                   evidence={"superseded": superseded}, confidence=Confidence.HIGH)


def _plan_ids(snap, extra):
    f = [x for x in analyze(snap) if not x.id.startswith("thermal.cpu")] + list(extra)
    return [st.id for st in build_plan(snap, f, recommend_upgrades(snap, f)).steps]


def test_repaste_step_uses_throttling_flag_not_title_text():
    snap = healthy_gaming_pc()
    flagged = _sustained_hot([{"id": "thermal.cpu", "severity": "warning", "title": "CPU is hot",
                               "likely_throttling": True}])
    assert "cheap.repaste" in _plan_ids(snap, [flagged])
    # The old wording alone, without the flag, no longer triggers the step.
    worded = _sustained_hot([{"id": "thermal.cpu", "severity": "warning",
                              "title": "CPU is running hot (95°C) and slowing itself down"}])
    ids = _plan_ids(snap, [worded])
    assert "free.cooling" in ids and "cheap.repaste" not in ids


def test_resolve_overlaps_keeps_the_throttling_flag():
    single = Finding(id="thermal.cpu", title="CPU is running hot (95°C)", severity=Severity.WARNING,
                     category=Category.THERMAL, evidence={"likely_throttling": True})
    sustained = Finding(id="thermal.cpu_sustained", title="CPU ran hot", severity=Severity.WARNING,
                        category=Category.THERMAL, evidence={"supersedes": "thermal.cpu"},
                        confidence=Confidence.HIGH)
    out = resolve_overlaps([single, sustained])
    assert [f.id for f in out] == ["thermal.cpu_sustained"]
    assert out[0].evidence["superseded"][0]["likely_throttling"] is True


# --- 4. Markdown escaping ------------------------------------------------------------

def test_md_escape_covers_markdown_characters():
    assert md_escape("a*b_c [x](y) `z` |") == "a\\*b\\_c \\[x\\](y) `z` \\|"
    assert md_escape("[x](y) `z`", keep_code=False) == "\\[x\\](y) \\`z\\`"
    assert md_escape("<b>&") == "&lt;b&gt;&amp;"
    assert md_escape("# title") == "\\# title" and md_escape("- item") == "\\- item"
    assert md_escape("1. first") == "1\\. first" and md_escape("4.2 GB, Wi-Fi") == "4.2 GB, Wi-Fi"
    assert md_escape("line one\nline two") == "line one line two"
    assert md_escape("back\\slash") == "back\\\\slash" and md_escape("~~gone~~") == "\\~\\~gone\\~\\~"


def test_markdown_report_escapes_snapshot_text_but_keeps_layout():
    snap = slow_old_laptop()
    snap.os.hostname = "*bold* _pc_ [click](http://example.com)"
    snap.gpus[0].name = "GPU | with ` tick and #hash"
    f = analyze(snap)
    md = render_markdown(snap, f, recommend_upgrades(snap, f))
    assert "# My PC Health Report: \\*bold\\* \\_pc\\_ \\[click\\](http://example.com)" in md
    assert "[click](" not in md.replace("\\[click\\](", "")
    assert "GPU \\| with \\` tick and #hash" in md
    # Intended formatting is untouched: headings, bold labels, tables and the maker's mark.
    for part in ("## At a glance", "| Metric | Value | Status | Meaning |", "## Your improvement plan",
                 "**Couldn't check:**" if "Couldn't check" in md else "## Findings",
                 '<p align="right"><sub>Made by NiyiSoft</sub></p>'):
        assert part in md


# --- 5. Battery wording when "user-replaceable" is unknown --------------------------

def test_battery_summary_is_cautious_when_replaceable_is_unknown():
    opt = compat.battery_option(slow_old_laptop())
    assert "fits" not in opt.summary
    assert "user-replaceable" in opt.summary and opt.how_to_check
    step = next(st for st in build_plan(slow_old_laptop(), analyze(slow_old_laptop()),
                                        recommend_upgrades(slow_old_laptop(), analyze(slow_old_laptop()))).steps
                if st.id == "cheap.battery")
    assert step.actions[0].startswith("Check if your model's battery is user-replaceable")


# --- 6. Changelog and release notes --------------------------------------------------

@pytest.mark.parametrize("name", ["CHANGELOG.md", "RELEASE_NOTES.md"])
def test_docs_list_review_fixes(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    v030 = text[text.index("v0.3.0"):]
    assert "Review fixes" in v030
    assert "test_v030_review_fixes.py" in text or name == "RELEASE_NOTES.md"
