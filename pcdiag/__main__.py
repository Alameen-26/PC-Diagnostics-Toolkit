"""Command-line interface: ``python -m pcdiag`` or the ``pcdiag`` script.

Subcommands::

    pcdiag                      same as "pcdiag scan"
    pcdiag scan [--benchmark] [--quick] [--json] [--no-save] [--output-dir DIR]
    pcdiag demo <fixture> [--json]
    pcdiag compare OLD.json NEW.json

``scan`` prints a summary and saves ``reports/snapshot-YYYYmmdd-HHMMSS.json``.
When the analysis and report packages are importable, it also prints
findings and writes ``reports/report-YYYYmmdd-HHMMSS.html``. Those imports
are defensive, so a scan still works if either package is missing or broken.

Added in v0.1.0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from .fixtures import ALL_FIXTURES
from .models import SystemSnapshot

SUBCOMMANDS = ("scan", "demo", "compare")


def load_pipeline() -> tuple[Optional[Callable], Optional[Callable], Optional[Callable], Optional[Callable]]:
    """Import (analyze, recommend_upgrades, render_html, verdict), each None if unavailable.

    Catches every exception, not just ImportError: a syntax error in a
    half-finished rules file must not stop the user from scanning.
    """
    analyze = recommend = render_html = verdict = None
    try:
        from .analysis import analyze, recommend_upgrades as recommend  # type: ignore[no-redef]
    except Exception:
        analyze = recommend = None
    try:
        from .report import render_html, verdict  # type: ignore[no-redef]
    except Exception:
        render_html = verdict = None
    return analyze, recommend, render_html, verdict


def run_pipeline(snap: SystemSnapshot) -> tuple[list[Any], list[Any], Optional[str], Any]:
    """Return (findings, recommendations, html or None, verdict or None); never raises.

    Each stage is guarded separately so, for example, a bug in the HTML
    renderer still leaves the findings printed in the terminal.
    """
    analyze, recommend, render_html, verdict = load_pipeline()
    findings: list[Any] = []
    recs: list[Any] = []
    html = summary = None
    if analyze is None:
        return findings, recs, html, summary

    def guarded(stage: str, fn: Callable[[], Any], default: Any) -> Any:
        """Run one stage; on failure warn on stderr and return ``default``."""
        try:
            return fn()
        except Exception as exc:  # analysis bugs shouldn't hide the raw scan
            print(f"warning: {stage} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return default

    findings = guarded("analysis", lambda: list(analyze(snap)), [])
    if recommend:
        recs = guarded("upgrade recommender", lambda: list(recommend(snap, findings)), [])
    if verdict:
        summary = guarded("verdict", lambda: verdict(snap, findings, recs), None)
    if render_html:
        html = guarded("HTML report", lambda: render_html(snap, findings, recs), None)
    return findings, recs, html, summary


def _stamp(snap: SystemSnapshot) -> str:
    """File-name timestamp in local time, e.g. 20261022-153000."""
    return snap.timestamp.astimezone().strftime("%Y%m%d-%H%M%S")


def _print_lines(lines: list[str]) -> None:
    """Print lines, replacing anything the console can't encode.

    Windows quirk: device or process names can contain characters that the
    console code page can't represent, which would raise UnicodeEncodeError.
    """
    enc = sys.stdout.encoding or "utf-8"
    for line in lines:
        print(line.encode(enc, "replace").decode(enc, "replace"))


def present(snap: SystemSnapshot, output_dir: Optional[Path], save_snapshot: bool, label: str = "") -> None:
    """Print summary + findings, and optionally write the JSON and HTML files."""
    from .terminal import analysis_lines, summary_lines

    findings, recs, html, summary = run_pipeline(snap)
    lines = summary_lines(snap) + analysis_lines(findings, recs, summary)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = label or _stamp(snap)
        lines.append("")
        if save_snapshot:
            path = output_dir / f"snapshot-{stamp}.json"
            path.write_text(snap.to_json(), encoding="utf-8")
            lines.append(f"Snapshot saved: {path}")
        if html:
            path = output_dir / f"report-{stamp}.html"
            path.write_text(html, encoding="utf-8")
            lines.append(f"Report saved:   {path}")
    _print_lines(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    """``scan``: collect live data."""
    from .collectors import collect_snapshot  # lazy: keeps "demo"/"compare" fast

    if not args.json:
        print("Scanning (about 5 s" + (", plus ~20 s benchmark" if args.benchmark else "") + ")...", file=sys.stderr)
    snap = collect_snapshot(include_benchmark=args.benchmark, quick=args.quick, benchmark_disk_mb=args.bench_disk_mb)
    if args.json:
        # JSON-only mode writes nothing to disk, so it can be piped:
        #   python -m pcdiag scan --json > snap.json
        print(snap.to_json())
        return 0
    present(snap, None if args.no_save else Path(args.output_dir), save_snapshot=True)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """``demo``: run the summary/analysis on a built-in example snapshot."""
    snap = ALL_FIXTURES[args.fixture]()
    if args.json:
        print(snap.to_json())
        return 0
    # Demo reports are named after the fixture and no snapshot JSON is saved.
    present(snap, None if args.no_save else Path(args.output_dir), save_snapshot=False, label=f"demo-{args.fixture}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """``compare``: diff two saved snapshot files."""
    from .compare import compare_lines

    snaps = []
    for path in (args.old, args.new):
        try:
            snaps.append(SystemSnapshot.from_json(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            print(f"error: can't read snapshot {path}: {exc}", file=sys.stderr)
            return 2
    _print_lines(compare_lines(*snaps))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands."""
    p = argparse.ArgumentParser(prog="pcdiag", description="PC Diagnostics Toolkit: find out what slows your PC down.")
    p.add_argument("--version", action="version", version=f"pcdiag {__version__}")
    # Kept from the v0.1.0 scaffold stub for backward compatibility.
    p.add_argument("--demo", choices=sorted(ALL_FIXTURES), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    def outputs(sp: argparse.ArgumentParser) -> None:
        """Options shared by commands that write report files."""
        sp.add_argument("--json", action="store_true", help="print the snapshot as JSON only (writes no files)")
        sp.add_argument("--output-dir", default="reports", help="where to save files (default: ./reports)")
        sp.add_argument("--no-save", action="store_true", help="don't write any files")

    scan = sub.add_parser("scan", help="scan this PC (default)")
    scan.add_argument("--benchmark", action="store_true", help="also run the ~20 s relative benchmark")
    scan.add_argument("--bench-disk-mb", type=int, default=256, metavar="MB",
                      help="size of the benchmark's temporary disk file (default 256, 0 = skip disk test)")
    scan.add_argument("--quick", action="store_true", help="shorter sampling, skip smartctl (noisier)")
    outputs(scan)
    scan.set_defaults(func=cmd_scan)

    demo = sub.add_parser("demo", help="show results for a built-in example PC")
    demo.add_argument("fixture", choices=sorted(ALL_FIXTURES))
    outputs(demo)
    demo.set_defaults(func=cmd_demo)

    comp = sub.add_parser("compare", help="compare two saved snapshot JSON files")
    comp.add_argument("old")
    comp.add_argument("new")
    comp.set_defaults(func=cmd_compare)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # "pcdiag" or "pcdiag --quick" with no subcommand means "scan".
    if not any(a in SUBCOMMANDS for a in argv) and not any(a in ("-h", "--help", "--version") or a.startswith("--demo") for a in argv):
        argv = ["scan"] + argv
    args = parser.parse_args(argv)
    if args.demo:  # legacy flag: print fixture JSON
        print(ALL_FIXTURES[args.demo]().to_json())
        return 0
    return args.func(args)


if __name__ == "__main__":
    # The __main__ guard is required for multiprocessing on Windows: the
    # benchmark's child processes re-import this module and must not
    # start another scan.
    sys.exit(main())
