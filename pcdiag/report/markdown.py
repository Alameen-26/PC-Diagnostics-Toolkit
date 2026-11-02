# pcdiag/report/markdown.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): raw benchmark section with comparison-only note.
# Changed in v0.2.0: "Over time" section with text sparklines.
# Changed in v0.2.0: right-aligned "Made by NiyiSoft" line at the end (maker's mark).
# Changed in v0.3.0: "Your improvement plan" section (free -> cheap -> replace) before "What to do".
# Changed in v0.3.0 (review): Markdown formatting characters in report text are escaped.
"""Markdown report mirroring the HTML layout (handy for GitHub issues and logs).

Same sections as the HTML report: verdict + score, metric cards (as a table
with icon + label so status isn't colour-only), reading times, free fixes,
ranked upgrades, findings grouped by severity, "Over time" (Added in v0.2.0)
and a system overview.
Text from the snapshot is neutralised so it can't inject HTML, break tables
or add unintended Markdown formatting (Changed in v0.3.0 (review)).
"""
from __future__ import annotations

import re  # Added in v0.3.0 (review)

from ..analysis.findings import Finding, Severity
from ..analysis.recommend import Recommendation
from ..models import SystemSnapshot
from .brand import BRAND_TEXT  # Added in v0.2.0
from .summary import (
    BENCHMARK_NOTE, NO_MONITOR_NOTE, SEVERITY_GROUP_TITLE, STATUS_STYLE, OverTime, benchmark_rows, evidence_label,
    free_fixes, group_findings, metric_cards, over_time, overview_rows, reading_times, verdict,
)
from .summary import PLAN_NOTE, PLAN_TIER_LABEL, improvement_plan, plan_step_tier  # Added in v0.3.0
from .summary import PLAN_ALL_COVERED, PLAN_DEDUP_NOTE, what_to_do  # Added in v0.3.0 (review)

# Emoji stand-ins for colour, always paired with a text label.
_LIGHT_ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "grey": "⚪"}
_STATUS_ICON = {"good": "🟢", "warn": "🟠", "bad": "🔴", "unknown": "⚪"}
_SEV_ICON = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵", Severity.OK: "🟢"}


# Changed in v0.3.0 (review): Markdown escaping.
# Before, only & < > were neutralised, so a drive label, process name or GPU
# name containing * _ [ ] ( ) and so on could turn into bold text, a link or
# a broken list. Now every character Markdown treats as formatting is escaped
# with a backslash. Characters that only matter at the start of a line
# (# + - = and "1." list numbers) are escaped there, so ordinary text such as
# "4.2 GB" or "Wi-Fi" stays readable in the raw file. One string is always
# kept on one line, so "the start of the string" is the only place they matter.
#
# ( ) and ! are left as they are: they only make a link or an image right
# after a "[" or "]", and those are always escaped, so they can't do anything.
# That keeps text such as "Monitored for 6 min (72 samples)" readable.
#
# Our own sentences sometimes show a command in `backticks`. A matched pair of
# backticks is kept as inline code (Markdown never formats or runs anything
# inside it), so that intended formatting still works.
_MD_INLINE = set("\\`*_{}[]|~")
_MD_HTML = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_CODE_SPAN = re.compile(r"`[^`\n]+`")
# Line-start markers: "# " heading, "- " / "+ " list, "1. " numbered list,
# "===" or "---" heading underline / rule. Followed by a space (or nothing),
# as Markdown needs, so "-5" or "4.2 GB" are left alone.
_LINE_START = re.compile(r"^(\s*)(#{1,6}(?=\s|$)|[-+](?=\s|$)|\d{1,9}\.(?=\s|$)|=+\s*$|-{2,})")


def _escape_plain(text: str) -> str:
    """Backslash-escape Markdown characters and turn HTML characters into entities."""
    return "".join(_MD_HTML.get(ch) or ("\\" + ch if ch in _MD_INLINE else ch) for ch in text)


def md_escape(text: str, keep_code: bool = True) -> str:
    """Make text from the snapshot or the analysis safe to drop into Markdown.

    Added in v0.3.0 (review). Newlines become spaces (one string never
    starts a new paragraph or list item). With ``keep_code`` a matched pair of
    backticks stays inline code; table cells pass False, because a pipe inside
    inline code still splits a table cell.
    """
    text = (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    out, pos = [], 0
    if keep_code:
        for m in _CODE_SPAN.finditer(text):
            out.append(_escape_plain(text[pos:m.start()]))
            out.append(m.group(0))
            pos = m.end()
    out.append(_escape_plain(text[pos:]))
    joined = "".join(out)
    # A leading "#", "-", "+", "=" or "1." would start a heading, list or rule.
    m = _LINE_START.match(joined)
    if m:
        marker = m.group(2)
        joined = m.group(1) + (marker[:-1] + "\\." if marker.endswith(".") else "\\" + marker) + joined[m.end():]
    return joined


def _line(text: str) -> str:
    """Text for a normal Markdown line (headings, list items, paragraphs).

    Changed in v0.3.0 (review): escapes Markdown formatting characters too,
    not only the HTML ones.
    """
    return md_escape(text)


def _cell(text: str) -> str:
    """Make text safe inside a Markdown table cell (pipes and newlines break tables).

    Changed in v0.3.0 (review): uses the same escaping as normal lines (which
    already covers backslashes, pipes and newlines), without keeping inline code.
    """
    return md_escape(text, keep_code=False)


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


def plan_markdown(plan, heading: str = "##") -> list[str]:
    """ "Your improvement plan" section as Markdown lines. Added in v0.3.0.

    Also used on its own for the ``plan-*.md`` file written by ``pcdiag plan``.
    """
    if plan is None:
        return []
    out = [f"{heading} Your improvement plan", "", f"**{_line(plan.headline)}**", "", f"_{_line(PLAN_NOTE)}_", ""]
    for st in plan.steps:
        tier = PLAN_TIER_LABEL[plan_step_tier(st)]
        cost = "" if st.cost == "Free" else f", {_line(st.cost)}"
        out.append(f"{st.order}. **{_line(st.title)}** ({tier}{cost}, confidence {st.confidence})  ")
        out.append(f"   {_line(st.why)}  ")
        if st.stop_point:
            out.append(f"   ⛔ **{_line(st.stop_point)}**  ")
        if st.evidence:
            out.append("   _Evidence:_ " + "; ".join(_line(e) for e in st.evidence) + "  ")
        for a in st.actions:
            out.append(f"   - {_line(a)}")
        for c in st.compatibility:
            out.append(f"   - Fits? {_line(c.item)}: **{_line(c.status)}**" + (f" ({_line(c.detail)})" if c.detail else ""))
        out.append("")
    if plan.cant_tell:
        out += ["**Couldn't check:**", ""] + [f"- {_line(x)}" for x in plan.cant_tell] + [""]
    return out


def render_markdown(snapshot: SystemSnapshot, findings: list[Finding], recs: list[Recommendation],
                    plan=None) -> str:
    """Render the report as GitHub-flavoured Markdown.

    Changed in v0.3.0: optional ``plan`` (built from the findings when omitted).
    """
    v = verdict(snapshot, findings, recs)
    host = snapshot.os.hostname if snapshot.os and snapshot.os.hostname else "this PC"
    score = "unknown" if v.score is None else f"{v.score}/100"
    out: list[str] = [f"# My PC Health Report: {_line(host)}", "",
                      f"## {_LIGHT_ICON[v.light]} {_line(v.label)}  (health score: {score})", "",
                      _line(v.sentence), ""]

    # Metric cards as a table
    out += ["## At a glance", "", "| Metric | Value | Status | Meaning |", "|---|---|---|---|"]
    for c in metric_cards(snapshot):
        out.append(f"| {_cell(c.name)} | {_cell(c.value)} | {_STATUS_ICON[c.status]} {STATUS_STYLE[c.status][2]} "
                   f"| {_cell(c.meaning)} |")
    out += [""] + [f"_{_line(x)}_  " for x in reading_times(snapshot)] + [""]

    # Added in v0.3.0: the improvement plan, before the detailed fix lists.
    plan = plan if plan is not None else improvement_plan(snapshot, findings, recs)
    out += plan_markdown(plan)

    # Fixes: free first, then purchases
    # Changed in v0.3.0 (review): items already in the plan aren't repeated here.
    fixes, upgrades, filtered = what_to_do(findings, recs, plan)
    out += ["## What to do", ""] + ([f"_{_line(PLAN_DEDUP_NOTE)}_", ""] if filtered else [])
    out += ["### Free fixes first", ""]
    out += [f"{i}. **{_line(f.title)}:** {_line(t)}" for i, (f, t) in enumerate(fixes, 1)] or \
        [PLAN_ALL_COVERED if filtered else "Nothing to fix right now."]
    out += ["", "### Upgrades worth buying", ""]
    if upgrades:
        for r in upgrades:
            urgent = " **(urgent)**" if r.urgent else ""
            out.append(f"{r.rank}. **{_line(r.title)}**{urgent} (impact {r.expected_impact}/100, cost {r.cost_tier.value})  ")
            out.append(f"   {_line(r.reason)}")
    else:
        out.append(PLAN_ALL_COVERED if filtered else "No upgrades needed.")
    out.append("")

    # Findings grouped by severity
    out += ["## Findings", ""]
    groups = group_findings(findings)
    if not groups:
        out += ["No checks produced results.", ""]
    for sev, items in groups:
        out += [f"### {_SEV_ICON[sev]} {SEVERITY_GROUP_TITLE[sev]} ({len(items)})", ""]
        for f in items:
            out.append(f"- **{_line(f.title)}** _({_line(evidence_label(f))})_  ")
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
