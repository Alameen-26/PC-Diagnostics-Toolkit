# pcdiag/analysis/findings.py
# Added in v0.1.0
"""Finding type produced by analysis rules.

A ``Finding`` is one plain-language observation about the machine: what is
wrong (or fine), why it matters, how to fix it, and the raw numbers that led
to it. Findings inherit ``Model`` so they serialise to JSON like snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..models import Model


class Severity(str, Enum):
    """How urgent a finding is. Ordered OK < INFO < WARNING < CRITICAL."""

    OK = "ok"              # checked with real data and looks fine
    INFO = "info"          # worth knowing / can't tell
    WARNING = "warning"    # hurting performance or likely to
    CRITICAL = "critical"  # risk of data loss or damage; act now

    @property
    def rank(self) -> int:
        """Numeric order used for sorting (higher = more urgent)."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.OK: 0, Severity.INFO: 1, Severity.WARNING: 2, Severity.CRITICAL: 3}


class Confidence(str, Enum):
    """How sure the rule is, so the report can soften uncertain claims."""

    LOW = "low"        # little or indirect evidence, or data missing
    MEDIUM = "medium"  # reasonable inference / rule-of-thumb threshold
    HIGH = "high"      # directly measured or reported by the device


class Category(str, Enum):
    """Area of the PC a finding is about; used for grouping in reports."""

    MEMORY = "memory"
    STORAGE = "storage"
    CPU = "cpu"
    THERMAL = "thermal"
    STARTUP = "startup"
    BATTERY = "battery"
    GPU = "gpu"
    PROCESSES = "processes"
    SYSTEM = "system"
    DATA = "data"          # collector problems / missing data


@dataclass
class Finding(Model):
    """One diagnostic result. ``evidence`` holds the raw values behind it."""

    id: str = ""                     # stable id, e.g. "storage.hdd_system_drive"
    title: str = ""                  # one plain-language line
    severity: Severity = Severity.INFO
    category: Category = Category.SYSTEM
    cause: str = ""                  # why this matters / what's happening
    fix: str = ""                    # what to do; free fixes first
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: Confidence = Confidence.MEDIUM
    free_fix: bool = True            # can the fix be done without buying anything?

    @property
    def is_problem(self) -> bool:
        """True for WARNING/CRITICAL, i.e. something the user should act on."""
        return self.severity in (Severity.WARNING, Severity.CRITICAL)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Most severe first; stable within a severity (rule order)."""
    return sorted(findings, key=lambda f: -f.severity.rank)
