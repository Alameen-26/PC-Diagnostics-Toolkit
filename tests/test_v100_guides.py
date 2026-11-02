"""v1.0.0: ``pcdiag guide`` repair guides for phones and consoles."""
import pytest

from pcdiag import guides
from pcdiag.__main__ import main


@pytest.mark.parametrize("device", list(guides.DEVICES))
def test_list_and_every_guide_render(device, capsys):
    assert main(["guide", device]) == 0
    out = capsys.readouterr().out
    ids = guides.symptom_ids(device)
    assert ids and all(i in out for i in ids)
    for i in ids:
        assert main(["guide", device, i]) == 0
        text = capsys.readouterr().out
        assert "Safety first:" in text and "1. " in text and "advice only" in text


def test_unknown_problem_is_a_clear_error(capsys):
    assert main(["guide", "phone", "nope"]) == 2
    assert "Choose one of" in capsys.readouterr().err


def test_guides_load_from_the_package():
    # The data must ship inside pcdiag so the exe and Store app have it.
    assert guides.load("phone")["device"] and guides.load("console")["symptoms"]
