"""Model round-tripping, fixture sanity and the legacy --demo flag.

Added in v0.1.0
"""
import json

import pytest

from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import (
    CPUInfo, DiskInfo, DiskKind, SmartHealth, SmartStatus, SystemSnapshot,
)
from pcdiag.__main__ import main


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_fixture_round_trip(name):
    """Fixture round trip."""
    snap = ALL_FIXTURES[name]()
    text = snap.to_json()
    json.loads(text)  # valid JSON
    again = SystemSnapshot.from_json(text)
    assert again == snap
    assert again.to_dict() == snap.to_dict()


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_fixture_is_sane(name):
    """Fixture is sane."""
    snap = ALL_FIXTURES[name]()
    assert snap.os and snap.cpu and snap.memory and snap.disks
    assert snap.system_drive is not None
    for d in snap.disks:
        assert isinstance(d.kind, DiskKind)
        assert abs(d.used_gb + d.free_gb - d.total_gb) < 1.0
        assert 0 <= d.percent <= 100


def test_slow_laptop_matches_spec():
    """Slow laptop matches spec."""
    s = ALL_FIXTURES["slow_old_laptop"]()
    assert s.system_drive.kind is DiskKind.HDD
    assert s.memory.total_gb == 4.0
    assert s.system_drive.percent >= 95
    assert len(s.startup_items) == 25
    assert s.battery.wear_percent >= 40


def test_failing_disk_present():
    """Failing disk present."""
    s = ALL_FIXTURES["failing_disk_machine"]()
    assert any(d.smart and d.smart.status is SmartStatus.FAILING for d in s.disks)
    assert s.errors


def test_empty_and_partial_snapshot():
    """Empty and partial snapshot."""
    empty = SystemSnapshot()
    assert SystemSnapshot.from_dict(empty.to_dict()) == empty
    partial = SystemSnapshot.from_dict({"cpu": {"logical_cores": 4}, "errors": ["memory: boom"], "extra": 1})
    assert partial.cpu == CPUInfo(logical_cores=4)
    assert partial.memory is None and partial.errors == ["memory: boom"]


def test_enum_and_nested_parsing():
    """Enum and nested parsing."""
    d = DiskInfo.from_dict({"device": "/dev/sda", "kind": "weird", "smart": {"status": "warning"}})
    assert d.kind is DiskKind.UNKNOWN
    assert d.smart == SmartHealth(status=SmartStatus.WARNING)


def test_cli_demo(capsys):
    """Cli demo."""
    assert main(["--demo", "healthy_gaming_pc"]) == 0
    assert SystemSnapshot.from_json(capsys.readouterr().out).os.hostname == "GAMING-RIG"
