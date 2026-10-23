# pcdiag/analysis/registry.py
# Added in v0.1.0
# Changed in v0.2.0: registers the sustained rules and resolves snapshot/over-time overlaps.
"""Rule registry and the ``analyze`` entry point.

A rule is a pure function ``SystemSnapshot -> Iterable[Finding]``. Rules are
registered with the ``@rule("name")`` decorator and run in registration order.
Rules must never raise on missing data: ``None`` means "unknown", so a rule
either skips or emits a low-confidence INFO finding, never an OK one.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..models import SystemSnapshot
from .findings import Category, Confidence, Finding, Severity, sort_findings

Rule = Callable[[SystemSnapshot], Iterable[Finding]]

# name -> rule function; insertion order is the run order.
RULES: dict[str, Rule] = {}


def rule(name: str) -> Callable[[Rule], Rule]:
    """Decorator registering a rule under ``name`` (registration order = run order)."""
    def deco(fn: Rule) -> Rule:
        if name in RULES:
            raise ValueError(f"duplicate rule name: {name}")
        RULES[name] = fn
        return fn
    return deco


def analyze(snapshot: SystemSnapshot, rules: dict[str, Rule] | None = None) -> list[Finding]:
    """Run every registered rule and return findings, most severe first.

    A rule that raises never takes down the whole analysis; it becomes an
    INFO finding so the bug is visible in the report.
    """
    from . import rules as _builtin  # noqa: F401  (ensures built-in rules are registered)
    # Changed in v0.2.0: the sustained (over-time) rules live in their own
    # module; importing it registers them after the snapshot rules.
    from . import sustained as _sustained

    out: list[Finding] = []
    for name, fn in (rules if rules is not None else RULES).items():
        try:
            out.extend(fn(snapshot) or [])
        except Exception as exc:  # pragma: no cover - defensive
            out.append(Finding(
                id=f"internal.rule_error.{name}", title=f"Check '{name}' could not run",
                severity=Severity.INFO, category=Category.DATA,
                cause=f"{type(exc).__name__}: {exc}", fix="Report this as a bug.",
                evidence={"rule": name}, confidence=Confidence.LOW))
    # Changed in v0.2.0: an over-time finding supersedes (or, on a short
    # window, merges into) the single-reading finding for the same metric,
    # so nothing is reported twice.
    return sort_findings(_sustained.resolve_overlaps(out))
