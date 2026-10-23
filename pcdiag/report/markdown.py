# pcdiag/report/markdown.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): raw benchmark section with comparison-only note.
# Changed in v0.2.0: "Over time" section with text sparklines.
# Changed in v0.2.0: right-aligned "Made by NiyiSoft" line at the end (maker's mark).
"""Markdown report mirroring the HTML layout (handy for GitHub issues and logs).

Same sections as the HTML report: verdict + score, metric cards (as a table
with icon + label so status isn't colour-only), reading times, free fixes,
ranked upgrades, findings grouped by severity, "Over time" (Added in v0.2.0)
and a system overview.
Text from the snapshot is neutralised so it can't inject HTML or break tables.
"""
from __future__ import annotations

from ..analysis.findings import Finding, Severity
from ..analysis.recommend import Recommendation
from ..models import SystemSnapshot
from .brand import BRAND_TEXT  # Added in v0.2.0
from .summary import (
    BENCHMARK_NOTE, NO_MONITOR_NOTE, SEVERITY_GROUP_TITLE, STATUS_STYLE, OverTime, benchmark_rows, evidence_label,
    free_fixes, group_findings, metric_cards, over_time, overview_rows, reading_times, verdict,
)

# Emoji stand-ins for colour, always paired with a text label.
_LIGHT_ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "grey": "⚪"}
_STATUS_ICON = {"good": "🟢", "warn": "🟠", "bad": "🔴", "unknown": "⚪"}
_SEV_ICON = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵", Severity.OK: "🟢"}


def _line(text: str) -> str:
    """Neutralise characters that could start HTML in renderers that allow inline HTML."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cell(text: str) -> str:
    """Make text safe inside a Markdown table cell (pipes and newlines break tables)."""
    return _line(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


# --- Over time (Added in v0.2.0) -------------------------------------------

_BLOCKS = "▁▂▃▄▅▆▇█"   # eight bar heights for a text sparkline
_SPARK_MAX_CHARS = 60   # long windows are averaged into at most 60 buckets


def text_sparkline(values: list, scale_max: float = 100.0) -> str:
    """Unicode block sparkline on a fixed 0..``scale_max`` scale.

    Long series are bucketed (mean of each bucket's real readings). A bucket
    with no readings at all is shown as a middle dot, so gaps stay visible
    instead of looking like zero.
    """
    n = len(values)
    if n == 0:
        return ""
    buckets = min(n, _SPARK_MAX_CHARS)
    chars = []
    for b in range(buckets):
        chunk = [v for v in values[b * n // buckets:(b + 1) * n // buckets] if v is not None]
        if not chunk:
            chars.append("·")
            continue
        frac = max(0.0, min(sum(chunk) / len(chunk), scale_max)) / scale_max
        chars.append(_BLOCKS[min(len(_BLOCKS) - 1, int(frac * len(_BLOCKS)))])
    return "".join(chars)


def _over_time_md(ot: OverTime | None) -> list[str]:
    """Markdown lines for the "Over time" section (or the how-to note)."""
    out = ["## Over time", ""]
    if ot is None:
        return out + [_line(NO_MONITOR_NOTE), ""]
    out += [f"**Monitor window:** started {_line(ot.start)} · {_line(ot.duration)} · {ot.sample_count} samples  ",
            f"**Confidence: {ot.confidence}.** {_line(ot.explanation)}", ""]
    for label, values, thr, top in ot.sparklines:
        # Inline code keeps the block characters monospaced and unformatted.
        out.append(f"- {_line(label)} (0-{top:.0f}%, threshold {thr:.0f}%): `{text_sparkline(values, top)}`")
    if ot.sparklines:
        out.append("")
    if ot.rows:
        out += ["| Metric | Mean | p95 | Max | Time past threshold | Longest stretch | Readings |",
                "|---|---|---|---|---|---|---|"]
        out += [f"| {_cell(r.label)} | {_cell(r.mean)} | {_cell(r.p95)} | {_cell(r.max)} | {_cell(r.above)} "
                f"| {_cell(r.streak)} | {_cell(r.readings)} |" for r in ot.rows]
        out.append("")
    out += [f"_{_line(n)}_  " for n in ot.notes]
    return out + [""]


def render_markdown(snapshot: SystemSnapshot, findings: list[Finding], recs: list[Recommendation]) -> str:
    """Render the report as GitHub-flavoured Markdown."""
    v = verdict(snapshot, findings, recs)
    host = snapshot.os.hostname if snapshot.os and snapshot.os.hostname else "this PC"
    score = "unknown" if v.score is None else f"{v.score}/100"
    out: list[str] = [f"# My PC Health Report: {_line(host)}", "",
                      f"## {_LIGHT_ICON[v.light]} {v.label}  (health score: {score})", "",
                      _line(v.sentence), ""]

    # Metric cards as a table
    out += ["## At a glance", "", "| Metric | Value | Status | Meaning |", "|---|---|---|---|"]
    for c in metric_cards(snapshot):
        out.append(f"| {_cell(c.name)} | {_cell(c.value)} | {_STATUS_ICON[c.status]} {STATUS_STYLE[c.status][2]} "
                   f"| {_cell(c.meaning)} |")
    out += [""] + [f"_{_line(x)}_  " for x in reading_times(snapshot)] + [""]

    # Fixes: free first, then purchases
    out += ["## What to do", "", "### Free fixes first", ""]
    fixes = free_fixes(findings)
    out += [f"{i}. **{_line(f.title)}:** {_line(t)}" for i, (f, t) in enumerate(fixes, 1)] or ["Nothing to fix right now."]
    out += ["", "### Upgrades worth buying", ""]
    if recs:
        for r in recs:
            urgent = " **(urgent)**" if r.urgent else ""
            out.append(f"{r.rank}. **{_line(r.title)}**{urgent} (impact {r.expected_impact}/100, cost {r.cost_tier.value})  ")
            out.append(f"   {_line(r.reason)}")
    else:
        out.append("No upgrades needed.")
    out.append("")

    # Findings grouped by severity
    out += ["## Findings", ""]
    groups = group_findings(findings)
    if not groups:
        out += ["No checks produced results.", ""]
    for sev, items in groups:
        out += [f"### {_SEV_ICON[sev]} {SEVERITY_GROUP_TITLE[sev]} ({len(items)})", ""]
        for f in items:
            out.append(f"- **{_line(f.title)}** _({evidence_label(f)})_  ")
            if f.cause:
                out.append(f"  {_line(f.cause)}  ")
            if f.fix:
                out.append(f"  **What to do:** {_line(f.fix)}")
        out.append("")

    out += _over_time_md(over_time(snapshot))  # Added in v0.2.0

    out += ["## System overview", "", "| | |", "|---|---|"]
    out += [f"| {_cell(k)} | {_cell(val)} |" for k, val in overview_rows(snapshot)]

    # Benchmark (Added in v0.1.0, follow-up): raw numbers + comparison-only note.
    bench = benchmark_rows(snapshot)
    if bench:
        out += ["", "## Benchmark", "", "| | |", "|---|---|"]
        out += [f"| {_cell(k)} | {_cell(val)} |" for k, val in bench]
        out += ["", f"_{_line(BENCHMARK_NOTE)}_"]
    out += ["", "_Thresholds are rules of thumb. \"Confirmed\" = measured or reported by the device; "
            "\"Possible cause\" = inferred, not directly measured. Generated by pcdiag._", ""]  # Changed in v0.2.0
    # Added in v0.2.0: maker's mark, right-aligned like the HTML badge (GitHub renders align="right").
    out += [f'<p align="right"><sub>{BRAND_TEXT}</sub></p>', ""]
    return "\n".join(out)
