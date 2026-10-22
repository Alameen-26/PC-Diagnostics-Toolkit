"""Shared pytest fixtures: fake psutil pieces and a "pretend this is Windows" switch.

Added in v0.1.0
"""
from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

from pcdiag.collectors import _shell

DATA = Path(__file__).parent / "data"

# Minimal stand-ins for psutil's named tuples.
Partition = namedtuple("Partition", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")
GiB = 1024 ** 3


@pytest.fixture
def windows(monkeypatch):
    """Make every collector believe it's running on Windows.

    Also blocks real subprocesses, so a test that forgets to stub a command
    fails loudly instead of trying to launch powershell.exe on Linux.
    """
    monkeypatch.setattr(_shell, "is_windows", lambda: True)
    monkeypatch.setattr(_shell, "which", lambda name: None)

    def no_commands(*a, **k):
        """Guard against unstubbed external commands."""
        raise AssertionError(f"unexpected external command: {a}")

    monkeypatch.setattr(_shell, "run_command", no_commands)
    return monkeypatch


@pytest.fixture
def ps_json(monkeypatch):
    """Return a function that makes run_powershell_json return canned data.

    ``ps_json(data)`` answers every PowerShell call with ``data``;
    ``ps_json(None, error="Access denied")`` simulates a failure.
    """
    def install(data, error=None):
        """Patch the helper with this canned response."""
        def fake(script, timeout=None, on_error=None):
            """Canned PowerShell response."""
            if error and on_error:
                on_error(error)
            return data
        monkeypatch.setattr(_shell, "run_powershell_json", fake)
    return install


def load_json(name: str):
    """Load a canned JSON file from tests/data."""
    return json.loads((DATA / name).read_text())
