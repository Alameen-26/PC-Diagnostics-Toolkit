"""Entry point for the portable Windows .exe (built by .github/workflows/release-exe.yml).

Runs exactly what ``python -m pcdiag`` runs. When the .exe is started by
double-clicking (no arguments), it waits for Enter before closing so the
console window doesn't vanish before the results can be read.

Changed in v1.0.0: after a double-click scan, the HTML report opens in the
default browser.

Added in v0.3.0 (packaging only; no change to how pcdiag works).
"""
import multiprocessing
import os
import sys
from pathlib import Path

from pcdiag.__main__ import main


def _open_latest_report() -> bool:
    """Open the newest HTML report in ./reports (Windows only). Added in v1.0.0."""
    if not hasattr(os, "startfile"):
        return False
    reports = sorted(Path("reports").glob("*.html"), key=lambda p: p.stat().st_mtime)
    if reports:
        try:
            os.startfile(str(reports[-1].resolve()))
            return True
        except OSError:
            pass
    return False


def _run() -> int:
    double_clicked = len(sys.argv) == 1
    try:
        code = main()
    except SystemExit as exc:  # argparse --help/--version exit this way
        code = exc.code if isinstance(exc.code, int) else 0
    if double_clicked:
        opened = not code and _open_latest_report()
        where = Path("reports").resolve()
        note = "Your report is open in your browser and saved in" if opened else "Reports are saved in"
        try:
            input(f"\nDone. {note} {where}. Press Enter to close...")
        except EOFError:
            pass
    return code or 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # needed for multiprocessing inside a one-file exe
    sys.exit(_run())
