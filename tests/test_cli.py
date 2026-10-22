"""CLI, compare and benchmark tests, plus a real ``python -m pcdiag scan --json`` smoke run.

Added in v0.1.0
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pcdiag.__main__ as cli
from pcdiag import benchmark
from pcdiag.compare import compare_rows
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import SystemSnapshot

ROOT = Path(__file__).resolve().parents[1]


def test_demo_prints_verdict_and_writes_report(tmp_path, capsys):
    """Demo prints verdict and writes report."""
    assert cli.main(["demo", "slow_old_laptop", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "OLD-LAPTOP" in out and "VERDICT:" in out and "Top findings" in out
    assert (tmp_path / "report-demo-slow_old_laptop.html").exists()
    assert not list(tmp_path.glob("snapshot-*.json"))  # demos don't save snapshots


def test_scan_is_default_and_saves_snapshot_and_report(tmp_path, capsys, monkeypatch):
    """Scan is default and saves snapshot and report."""
    import pcdiag.collectors as collectors

    monkeypatch.setattr(collectors, "collect_snapshot", lambda **kw: ALL_FIXTURES["healthy_gaming_pc"]())
    assert cli.main(["--output-dir", str(tmp_path)]) == 0  # no subcommand -> scan
    snaps = list(tmp_path.glob("snapshot-*.json"))
    reports = list(tmp_path.glob("report-*.html"))
    assert len(snaps) == 1 and len(reports) == 1
    assert SystemSnapshot.from_json(snaps[0].read_text()).os.hostname == "GAMING-RIG"
    assert "GAMING-RIG" in capsys.readouterr().out


def test_scan_works_without_analysis_package(tmp_path, capsys, monkeypatch):
    # A None entry in sys.modules makes "import pcdiag.analysis" raise ImportError.
    """Scan works without analysis package."""
    monkeypatch.setitem(sys.modules, "pcdiag.analysis", None)
    monkeypatch.setitem(sys.modules, "pcdiag.report", None)
    assert cli.load_pipeline() == (None, None, None, None)
    assert cli.main(["demo", "failing_disk_machine", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "OFFICE-PC" in out and "VERDICT" not in out
    assert not list(tmp_path.glob("report-*.html"))


def test_analysis_crash_is_contained(capsys, monkeypatch):
    """Analysis crash is contained."""
    def broken(snapshot):
        """An analysis function with a bug."""
        raise ValueError("bad rule")

    monkeypatch.setattr(cli, "load_pipeline", lambda: (broken, None, None, None))
    assert cli.main(["demo", "healthy_gaming_pc", "--no-save"]) == 0
    captured = capsys.readouterr()
    assert "analysis failed" in captured.err and "GAMING-RIG" in captured.out


def test_compare_detects_regressions(tmp_path, capsys):
    """Compare detects regressions."""
    old, new = ALL_FIXTURES["healthy_gaming_pc"](), ALL_FIXTURES["slow_old_laptop"]()
    rows = {r["label"]: r for r in compare_rows(old, new)}
    assert rows["RAM installed"]["verdict"] == "worse"
    assert rows["Startup items enabled"]["delta"] == 22
    assert rows["Battery wear"]["delta"] is None  # gaming PC has no battery
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(old.to_json())
    b.write_text(new.to_json())
    assert cli.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "System drive used" in out and "worse" in out and "New startup items" in out


def test_compare_bad_file(tmp_path, capsys):
    """Compare bad file."""
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert cli.main(["compare", str(bad), str(bad)]) == 2


def test_benchmark_small_run_cleans_up(tmp_path, monkeypatch):
    """Benchmark small run cleans up."""
    monkeypatch.setattr(benchmark.tempfile, "tempdir", str(tmp_path))
    result = benchmark.run_benchmark(disk_mb=8, cpu_seconds=0.2)
    assert result.cpu_single_score > 0 and result.cpu_multi_score >= result.cpu_single_score * 0.5
    assert result.disk_read_mbps > 0 and result.disk_write_mbps > 0 and result.memory_mbps > 0
    assert list(tmp_path.iterdir()) == []  # temp file deleted


def test_benchmark_reports_failures(monkeypatch):
    """Benchmark reports failures."""
    def fail(*a, **k):
        """Simulate a full temp drive."""
        raise OSError("No space left on device")

    monkeypatch.setattr(benchmark, "disk_sequential", fail)
    monkeypatch.setattr(benchmark, "cpu_multi", lambda s: 1.0)
    errors = []
    result = benchmark.run_benchmark(disk_mb=8, cpu_seconds=0.05, on_error=errors.append)
    assert result.disk_read_mbps is None and result.cpu_multi_score == 1.0
    assert errors == ["disk failed: OSError: No space left on device"]


def test_real_scan_json_smoke():
    """Run the real CLI on this machine, as a user would."""
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, "-m", "pcdiag", "scan", "--json"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)  # stdout must be pure JSON
    snap = SystemSnapshot.from_dict(data)
    assert snap.cpu.logical_cores and snap.memory.total_gb and snap.disks
