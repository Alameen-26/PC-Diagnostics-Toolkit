# pcdiag/analysis/__init__.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): export needs_admin / admin_blocked_collectors.
# Changed in v0.2.0: export the sustained (over-time) helpers.
"""Analysis layer: turn a SystemSnapshot into findings and upgrade advice.

Public API::

    findings = analyze(snapshot)                    # list[Finding], worst first
    recs = recommend_upgrades(snapshot, findings)   # list[Recommendation], best first
"""
from .findings import Category, Confidence, Finding, Severity, sort_findings
from .registry import RULES, analyze, rule
from . import rules as _rules  # noqa: F401  (registers the built-in rules)
from .rules import admin_blocked_collectors, missing_core_sections, needs_admin
from .recommend import CostTier, Recommendation, recommend_upgrades
# Changed in v0.2.0: importing sustained also registers the over-time rules.
from .sustained import MonitorView, SeriesStats, over_time_summary, series_stats, window_confidence

__all__ = [
    "Category", "Confidence", "Finding", "Severity", "sort_findings",
    "RULES", "analyze", "rule", "missing_core_sections", "needs_admin", "admin_blocked_collectors",
    "CostTier", "Recommendation", "recommend_upgrades",
    "MonitorView", "SeriesStats", "over_time_summary", "series_stats", "window_confidence",
]
