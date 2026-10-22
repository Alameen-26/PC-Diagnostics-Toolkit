# pcdiag/report/__init__.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): export benchmark_rows / BENCHMARK_NOTE.
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

__all__ = ["BENCHMARK_NOTE", "benchmark_rows", "render_html", "render_markdown", "MetricCard", "Verdict", "evidence_label",
           "free_fixes", "health_score", "metric_cards", "overview_rows", "summary_sentence", "verdict"]
