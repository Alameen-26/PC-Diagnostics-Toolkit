"""The guided troubleshooter data files (v1.0.0) are well formed and safe."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "pcdiag" / "knowledge"  # Changed in v1.0.0: moved into the package
FILES = sorted(ROOT.glob("*.json"))


def test_files_exist():
    assert {f.stem for f in FILES} >= {"phones", "consoles"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_shape_and_safety(path):
    data = json.loads(path.read_text())
    assert data["device"] and data["applies_to"]
    assert any("swollen" in rule.lower() for rule in data["safety"])
    assert any("warranty" in rule.lower() for rule in data["safety"])
    ids = [s["id"] for s in data["symptoms"]]
    assert len(ids) == len(set(ids))
    for symptom in data["symptoms"]:
        assert symptom["title"] and symptom["steps"] and isinstance(symptom["stop_if"], list)
        for step in symptom["steps"]:
            assert step["do"] and step["why"] and step["needs"] in {"nothing", "tool", "part"}
        # Free steps always come before ones that need a tool or part.
        order = [{"nothing": 0, "tool": 1, "part": 2}[s["needs"]] for s in symptom["steps"]]
        assert order[0] == 0
