"""v0.3.0: ``pcdiag plan``, ``pcdiag troubleshoot``, and the "Your improvement
plan" section in the terminal, Markdown and HTML outputs.

Added in v0.3.0.
"""
from __future__ import annotations

import json

import pytest

import pcdiag.__main__ as cli
from pcdiag.analysis import analyze, recommend_upgrades
from pcdiag.analysis.plan import build_plan
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.report import render_html, render_markdown
from pcdiag.terminal import ascii_text, plan_lines
from pcdiag.troubleshoot import GUIDES, SYMPTOMS, guide, guide_lines


def _run(name):
    """Snapshot, findings, recs and plan for a fixture."""
    snap = ALL_FIXTURES[name]()
    findings = analyze(snap)
    recs = recommend_upgrades(snap, findings)
    return snap, findings, recs, build_plan(snap, findings, recs)


# --- CLI: plan ---------------------------------------------------------------------

def test_subcommands_registered():
    """plan and troubleshoot are real subcommands (not swallowed as 'scan')."""
    assert {"plan", "troubleshoot"} <= set(cli.SUBCOMMANDS)


def test_plan_demo_prints_and_saves(tmp_path, capsys):
    """plan --demo slow_old_laptop: tiered plan on screen + plan-demo-*.md on disk."""
    assert cli.main(["plan", "--demo", "slow_old_laptop", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Your improvement plan" in out and "FREE FIXES" in out and "CHEAP UPGRADES" in out
    assert out.index("FREE FIXES") < out.index("CHEAP UPGRADES")
    assert "Fits?" in out and "Why:" in out
    out.encode("ascii")  # terminal output stays ASCII for old Windows consoles
    md = (tmp_path / "plan-demo-slow_old_laptop.md").read_text(encoding="utf-8")
    assert md.startswith("# Improvement plan: OLD-LAPTOP") and "## Your improvement plan" in md
    assert not list(tmp_path.glob("snapshot-*.json"))


def test_plan_no_save_writes_nothing(tmp_path, capsys):
    """--no-save: no files."""
    assert cli.main(["plan", "--demo", "healthy_gaming_pc", "--no-save", "--output-dir", str(tmp_path)]) == 0
    assert not list(tmp_path.iterdir())
    assert "Plan saved" not in capsys.readouterr().out


def test_plan_json(capsys, tmp_path):
    """--json prints only parseable JSON, writes no files."""
    assert cli.main(["plan", "--demo", "failing_disk_machine", "--json", "--output-dir", str(tmp_path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["steps"][0]["urgent"] is True and data["steps"][0]["id"].startswith("urgent.backup")
    assert not list(tmp_path.iterdir())


def test_plan_from_snapshot_file(tmp_path, capsys):
    """--snapshot FILE plans from a saved scan."""
    path = tmp_path / "snap.json"
    path.write_text(ALL_FIXTURES["slow_old_laptop"]().to_json(), encoding="utf-8")
    assert cli.main(["plan", "--snapshot", str(path), "--no-save"]) == 0
    assert "Swap the hard disk for an SSD" in capsys.readouterr().out


def test_plan_bad_snapshot_returns_2(tmp_path, capsys):
    """Unreadable snapshot: exit code 2 and a clear error."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["plan", "--snapshot", str(bad)]) == 2
    assert "can't read snapshot" in capsys.readouterr().err
    assert cli.main(["plan", "--snapshot", str(tmp_path / "missing.json")]) == 2


def test_plan_demo_with_monitor_uses_sustained_data(capsys):
    """--demo X --monitor attaches the fixture's monitor session; the plan says so."""
    assert cli.main(["plan", "--demo", "slow_old_laptop", "--monitor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["used_monitor"] is True


def test_plan_live_scan_path(monkeypatch, capsys):
    """No source flag -> a live scan (stubbed here)."""
    import pcdiag.collectors as collectors
    monkeypatch.setattr(collectors, "collect_snapshot", lambda **kw: ALL_FIXTURES["healthy_gaming_pc"]())
    assert cli.main(["plan", "--no-save"]) == 0
    assert "nothing" in capsys.readouterr().out.lower()


def test_demo_output_includes_compact_plan(tmp_path, capsys):
    """The normal demo/scan output ends with the short plan block."""
    assert cli.main(["demo", "slow_old_laptop", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Your improvement plan" in out and "pcdiag plan" in out
    html = (tmp_path / "report-demo-slow_old_laptop.html").read_text(encoding="utf-8")
    assert 'id="plan"' in html


# --- CLI: troubleshoot --------------------------------------------------------------

@pytest.mark.parametrize("symptom", list(SYMPTOMS))
def test_troubleshoot_every_symptom(symptom, capsys):
    """Every symptom prints numbered steps, ASCII only, with the scan tip."""
    assert cli.main(["troubleshoot", symptom]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Troubleshooting: {SYMPTOMS[symptom]}")
    assert "1. " in out and "--scan" in out
    out.encode("ascii")


def test_troubleshoot_requires_valid_symptom(capsys):
    """Unknown symptom -> argparse error."""
    with pytest.raises(SystemExit):
        cli.main(["troubleshoot", "blue-smoke"])


def test_troubleshoot_demo_marks_findings(capsys):
    """--demo marks steps pcdiag saw; disk noise always starts with the backup stop."""
    assert cli.main(["troubleshoot", "disk-noise", "--demo", "failing_disk_machine"]) == 0
    out = capsys.readouterr().out
    assert "pcdiag saw: Drive D:" in out
    assert out.index("<- start here") < out.index("pcdiag saw:")
    assert "1. Back up now  <- start here" in out and "STOP: back up" in out


def test_troubleshoot_bad_snapshot(tmp_path):
    """Unreadable snapshot -> 2."""
    assert cli.main(["troubleshoot", "freezes", "--snapshot", str(tmp_path / "nope.json")]) == 2


# --- troubleshoot guides --------------------------------------------------------------

def test_every_symptom_has_a_guide_with_a_stop_point():
    """Each guide has steps and at least one stop point."""
    assert set(GUIDES) == set(SYMPTOMS)
    for name, steps in GUIDES.items():
        assert steps, name
        assert any(s.stop for s in steps), f"{name} has no stop point"


def test_guide_unknown_symptom():
    """KeyError lists the choices."""
    with pytest.raises(KeyError, match="slow-boot"):
        guide("nope")


def test_guide_marks_only_problems():
    """Healthy PC: nothing is marked; slow laptop: slow-boot marks startup + HDD."""
    _, healthy, _, _ = _run("healthy_gaming_pc")
    assert not any(s.seen for s in guide("slow-boot", healthy))
    _, slow, _, _ = _run("slow_old_laptop")
    steps = guide("slow-boot", slow)
    assert steps[0].seen and steps[1].seen  # too many startup apps; Windows on an HDD


def test_guide_does_not_mutate_templates():
    """Annotating a guide leaves GUIDES untouched."""
    _, slow, _, _ = _run("slow_old_laptop")
    guide("slow-boot", slow)
    assert not any(s.seen for s in GUIDES["slow-boot"])


def test_conditional_stop_wording():
    """A conditional stop that didn't match is phrased 'If this applies'."""
    _, healthy, _, _ = _run("healthy_gaming_pc")
    text = "\n".join(guide_lines("slow-boot", healthy, scanned=True))
    assert "If this applies: STOP" in text and "--scan" not in text


# --- reports --------------------------------------------------------------------------

def test_markdown_has_plan_before_what_to_do():
    """Markdown: plan section sits before the existing fix lists, which remain."""
    snap, findings, recs, plan = _run("slow_old_laptop")
    md = render_markdown(snap, findings, recs, plan=plan)
    assert "## Your improvement plan" in md and "### Free fixes first" in md
    assert md.index("## Your improvement plan") < md.index("## What to do")
    assert "(Free, Free" not in md  # the 'Free' cost isn't repeated after the tier label


def test_markdown_builds_plan_when_omitted():
    """render_markdown without plan= still includes it."""
    snap, findings, recs, _ = _run("failing_disk_machine")
    assert "## Your improvement plan" in render_markdown(snap, findings, recs)


def test_html_plan_section():
    """HTML: plan section with tiered cards, stop point and Fits? pills."""
    snap, findings, recs, plan = _run("slow_old_laptop")
    html = render_html(snap, findings, recs, plan=plan)
    assert '<h2 id="plan">Your improvement plan</h2>' in html
    assert f'data-steps="{len(plan.steps)}"' in html
    assert 'data-tier="free"' in html and 'data-tier="cheap"' in html
    assert 'class="stop"' in html and "Fits?" in html
    assert html.index('id="plan"') < html.index("Free fixes first")


def test_html_plan_healthy_says_nothing_needed():
    """Healthy PC: plan section says nothing is needed, no upgrade cards."""
    snap, findings, recs, plan = _run("healthy_gaming_pc")
    html = render_html(snap, findings, recs, plan=plan)
    assert 'id="plan"' in html and 'data-tier="cheap"' not in html


def test_plan_lines_compact_and_ascii():
    """Compact terminal block: headline + one line per step, capped, ASCII."""
    _, _, _, plan = _run("slow_old_laptop")
    lines = plan_lines(plan, max_steps=3)
    text = "\n".join(lines)
    text.encode("ascii")
    assert "Your improvement plan" in text and "pcdiag plan" in text
    assert plan_lines(None) == []


def test_ascii_text():
    """Non-ASCII punctuation is mapped, not dropped."""
    assert ascii_text("82°C – 4 → 12 GB × 2 “ok”") == '82 C - 4 -> 12 GB x 2 "ok"'
