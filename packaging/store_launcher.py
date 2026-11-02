"""Entry point for the Microsoft Store (MSIX) build (built by .github/workflows/store-msix.yml).

Same as exe_launcher.py, with one difference: a Store app is installed in a
read-only folder. When the app is started from the Start menu, Windows runs it
from that folder, so reports could not be saved there. In that case this
switches to Documents\\PC Diagnostics Toolkit first. When run from a terminal
(the ``pcdiag`` command), reports go next to where you ran it, as usual.

Copyright (c) NiyiSoft. All rights reserved.
"""
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path


def _writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def _pick_working_folder() -> None:
    here = Path.cwd()
    if "WindowsApps" not in str(here) and _writable(here):
        return
    docs = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "PC Diagnostics Toolkit"
    try:
        docs.mkdir(parents=True, exist_ok=True)
        os.chdir(docs)
    except OSError:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _pick_working_folder()
    from exe_launcher import _run
    sys.exit(_run())
