"""Entry point for the portable Windows .exe (built by .github/workflows/release-exe.yml).

Runs exactly what ``python -m pcdiag`` runs. When the .exe is started by
double-clicking (no arguments), it waits for Enter before closing so the
console window doesn't vanish before the results can be read.

Added in v0.3.0 (packaging only; no change to how pcdiag works).
"""
import multiprocessing
import sys

from pcdiag.__main__ import main


def _run() -> int:
    double_clicked = len(sys.argv) == 1
    try:
        code = main()
    except SystemExit as exc:  # argparse --help/--version exit this way
        code = exc.code if isinstance(exc.code, int) else 0
    if double_clicked:
        try:
            input("\nDone. Reports are in the 'reports' folder next to where this ran. Press Enter to close...")
        except EOFError:
            pass
    return code or 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # needed for multiprocessing inside a one-file exe
    sys.exit(_run())
