# pcdiag/report/__init__.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): export benchmark_rows / BENCHMARK_NOTE.
# Changed in v0.2.0: export the "Over time" helpers.
# Changed in v0.3.0: export the improvement-plan helpers.
"""Report renderers: turn snapshot + findings + recommendations into HTML or Markdown.

Usage::

    html = render_html(snapshot, findings, recs)
    md = render_markdown(snapshot, findings, recs)
"""
from .html import render_html
from .markdown import render_markdown
from .summary import (
    BENCHMARK_NOTE, benchmark_rows, MetricCard, Verdict, evidence_label, free_fixes, health_score, metric_cards,
    overview_rows, summary_sentence, verdict,
)
from .summary import NO_MONITOR_NOTE, OverTime, over_time  # Added in v0.2.0
from .summary import PLAN_NOTE, improvement_plan  # Added in v0.3.0
from .markdown import plan_markdown  # Added in v0.3.0

__all__ = ["BENCHMARK_NOTE", "benchmark_rows", "render_html", "render_markdown", "MetricCard", "Verdict", "evidence_label",
           "free_fixes", "health_score", "metric_cards", "overview_rows", "summary_sentence", "verdict",
           "NO_MONITOR_NOTE", "OverTime", "over_time",
           "PLAN_NOTE", "improvement_plan", "plan_markdown"]  # Added in v0.3.0
