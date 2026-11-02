"""``pcdiag guide``: step-by-step self-repair guides for phones and game consoles.

Added in v1.0.0. pcdiag can't scan these devices, so the guides ask what is
wrong and list checks in order: free and safe first, then steps that need a
tool or a part, and clear signs to stop and see a technician. The data lives
in ``pcdiag/knowledge/*.json``.
"""
from __future__ import annotations

import json
import textwrap
from functools import lru_cache
from importlib import resources

DEVICES = {"phone": "phones", "console": "consoles"}
_NEEDS = {"nothing": "free", "tool": "needs a tool", "part": "needs a part"}


@lru_cache(maxsize=None)
def load(device: str) -> dict:
    """Return the guide data for ``device`` ("phone" or "console")."""
    name = DEVICES[device] + ".json"
    return json.loads(resources.files("pcdiag.knowledge").joinpath(name).read_text(encoding="utf-8"))


def symptom_ids(device: str) -> list[str]:
    return [s["id"].replace("_", "-") for s in load(device)["symptoms"]]


def _wrap(text: str, indent: str) -> str:
    return textwrap.fill(text, width=88, initial_indent=indent, subsequent_indent=" " * len(indent))


def render_list(device: str) -> str:
    data = load(device)
    lines = [f"Repair guides for {device}s ({', '.join(data['applies_to'])}):", ""]
    for s in data["symptoms"]:
        lines.append(f"  {s['id'].replace('_', '-'):<18} {s['title']}")
    lines += ["", f"Open one with: pcdiag guide {device} <problem>, for example: pcdiag guide {device} {symptom_ids(device)[0]}"]
    return "\n".join(lines)


def render_guide(device: str, symptom: str) -> str:
    data = load(device)
    key = symptom.replace("-", "_")
    s = next((x for x in data["symptoms"] if x["id"] == key), None)
    if s is None:
        raise KeyError(symptom)
    lines = [f"{s['title']} ({device}: {', '.join(data['applies_to'])})", "",
             "Safety first:"]
    lines += [_wrap(rule, "  - ") for rule in data["safety"]]
    lines += ["", "Steps (start at the top):"]
    for i, step in enumerate(s["steps"], 1):
        lines.append(_wrap(f"{step['do']} [{_NEEDS[step['needs']]}]", f"  {i}. "))
        lines.append(_wrap(f"Why: {step['why']}", "     "))
    if s["stop_if"]:
        lines += ["", "Stop and see a technician if:"]
        lines += [_wrap(x, "  - ") for x in s["stop_if"]]
    lines += ["", "This is advice only. pcdiag doesn't change anything on your device."]
    return "\n".join(lines)
