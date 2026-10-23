# pcdiag/report/html.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): raw benchmark section with comparison-only note.
# Changed in v0.2.0: "Over time" section with inline-SVG sparklines.
# Changed in v0.2.0: "Made by NiyiSoft" mark in the bottom-right corner (see brand.py).
"""Self-contained HTML report ("My PC Health Report").

Layout, top to bottom:
1. Verdict banner: one overall status (colour + icon + word), a plain-language
   summary sentence and a 0-100 health score.
2. Metric cards: CPU, memory, disk space, temperature, battery, disk health.
3. When the readings were taken (and sample durations).
4. Fixes: "Free fixes first", then "Upgrades worth buying" (ranked).
5. Findings grouped by severity, each tagged Confirmed / Possible cause, with
   a "What to do" line.
6. Over time (Added in v0.2.0): monitor window, per-metric stats, CPU/RAM
   sparklines and the confidence in plain words; or a note on how to get it.
7. System overview.
8. Maker's mark (Added in v0.2.0): small "Made by NiyiSoft" badge, bottom right.

Rules for this file:
* No external assets or scripts; CSS is inline and adapts to dark mode via
  ``prefers-color-scheme``.
* Status is never shown by colour alone: every coloured element also has an
  icon and a text label (accessibility for colour-blind users).
* Every string from the snapshot is HTML-escaped (a hostile process name must
  not inject markup).
"""
from __future__ import annotations

from html import escape

from ..analysis.findings import Finding, Severity
from ..analysis.recommend import Recommendation
from ..models import SystemSnapshot
from .brand import BRAND_CSS, brand_html  # Added in v0.2.0
from .summary import (
    BAD, BENCHMARK_NOTE, GOOD, SEVERITY_GROUP_TITLE, STATUS_STYLE, UNKNOWN, WARN, benchmark_rows,
    NO_MONITOR_NOTE, OverTime, evidence_label, free_fixes, group_findings, metric_cards, over_time, overview_rows,
    reading_times, verdict,
)

# Verdict light -> card status, so the banner reuses the card styling.
_LIGHT_TO_STATUS = {"green": GOOD, "amber": WARN, "red": BAD, "grey": UNKNOWN}
# Severity -> status for finding badges.
_SEV_TO_STATUS = {Severity.CRITICAL: BAD, Severity.WARNING: WARN, Severity.INFO: UNKNOWN, Severity.OK: GOOD}
_SEV_ICON = {Severity.CRITICAL: "✖", Severity.WARNING: "!", Severity.INFO: "i", Severity.OK: "✔"}

# CSS custom properties give one palette for light mode and one for dark
# mode; components only reference the variables.
_CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1f2328;--muted:#656d76;--border:#d8dbe0;
--green:#1f883d;--amber:#b35900;--red:#cf222e;--grey:#6e7781;--info:#0969da;
--green-bg:#dafbe1;--amber-bg:#fff1e0;--red-bg:#ffebe9;--grey-bg:#eef1f4}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--card:#161b22;--text:#e6edf3;--muted:#9198a1;
--border:#30363d;--green:#3fb950;--amber:#f0a030;--red:#f85149;--grey:#8b949e;--info:#58a6ff;
--green-bg:#12261a;--amber-bg:#2d2110;--red-bg:#2d1214;--grey-bg:#21262d}}
*{box-sizing:border-box}
body{font-family:"Segoe UI",system-ui,-apple-system,sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.45}
main{max-width:1000px;margin:24px auto;padding:0 16px}
h1{font-size:26px;margin:0 0 12px}h2{font-size:19px;margin:28px 0 10px}h3{font-size:16px;margin:18px 0 8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.banner{display:flex;gap:18px;align-items:center;border-left:8px solid var(--c);background:var(--cbg)}
.banner .big{font-size:28px;font-weight:700;color:var(--c)}
.score{margin-left:auto;text-align:center;min-width:110px}
.score b{display:block;font-size:34px;color:var(--c)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.metric{border-top:5px solid var(--c)}
.metric .name{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.metric .value{font-size:20px;font-weight:650;margin:4px 0}
.pill{display:inline-flex;gap:4px;align-items:center;font-size:12px;font-weight:700;padding:2px 8px;
border-radius:999px;color:var(--c);background:var(--cbg);border:1px solid var(--c);white-space:nowrap}
.s-good{--c:var(--green);--cbg:var(--green-bg)}.s-warn{--c:var(--amber);--cbg:var(--amber-bg)}
.s-bad{--c:var(--red);--cbg:var(--red-bg)}.s-unknown{--c:var(--grey);--cbg:var(--grey-bg)}
.finding{margin-bottom:10px;border-left:5px solid var(--c)}
.finding .head{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.todo{margin:6px 0 0;padding:6px 10px;border-radius:6px;background:var(--grey-bg)}
.muted{color:var(--muted);font-size:13px}
ol,ul{margin:0;padding-left:22px}ol.card{padding-left:40px}li{margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--border);vertical-align:top}
.spark{display:flex;gap:10px;align-items:center;margin:6px 0}.spark svg{flex:none;width:260px;height:44px;
background:var(--grey-bg);border-radius:6px}.spark .line{fill:none;stroke:var(--info);stroke-width:1.6}
.spark .thr{stroke:var(--amber);stroke-width:1;stroke-dasharray:4 3}
"""


# --- Over time (Added in v0.2.0) -------------------------------------------

_SPARK_W, _SPARK_H, _SPARK_PAD = 260.0, 44.0, 3.0  # sparkline viewBox size and inner padding


def sparkline_svg(label: str, values: list, threshold: float, scale_max: float = 100.0) -> str:
    """Inline SVG sparkline: no scripts or external assets.

    * The y axis is fixed at 0..``scale_max`` (100 for percentages), so a flat
      30% line doesn't look like a spike.
    * ``None`` values are gaps: the line is split there rather than drawn
      across missing data.
    * A dashed line marks the threshold. ``role="img"`` with an aria-label and
      a <title> describes the chart for screen readers; the label is escaped.
    """
    n = len(values)
    def xy(i: int, v: float) -> str:
        x = _SPARK_PAD + (i / (n - 1) if n > 1 else 0.5) * (_SPARK_W - 2 * _SPARK_PAD)
        y = _SPARK_H - _SPARK_PAD - (max(0.0, min(v, scale_max)) / scale_max) * (_SPARK_H - 2 * _SPARK_PAD)
        return f"{x:.1f},{y:.1f}"
    runs, cur = [], []
    for i, v in enumerate(values):  # split into runs of consecutive real readings
        if v is None:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(xy(i, v))
    if cur:
        runs.append(cur)
    lines = "".join(f'<polyline class="line" points="{" ".join(r)}"/>' if len(r) > 1 else
                    f'<circle cx="{r[0].split(",")[0]}" cy="{r[0].split(",")[1]}" r="1.8" fill="currentColor"/>'
                    for r in runs)
    ty = _SPARK_H - _SPARK_PAD - (min(threshold, scale_max) / scale_max) * (_SPARK_H - 2 * _SPARK_PAD)
    desc = escape(f"{label} over the monitor window; dashed line = {threshold:.0f}% threshold")
    return (f'<svg viewBox="0 0 {_SPARK_W:.0f} {_SPARK_H:.0f}" role="img" aria-label="{desc}" '
            f'preserveAspectRatio="none"><title>{desc}</title>'
            f'<line class="thr" x1="0" x2="{_SPARK_W:.0f}" y1="{ty:.1f}" y2="{ty:.1f}"/>{lines}</svg>')


def _over_time_html(ot: OverTime | None) -> list[str]:
    """HTML for the "Over time" section (or the how-to note when there's no monitor data)."""
    out = ["<h2>Over time</h2>"]
    if ot is None:
        out.append(f'<p class="card" data-monitor="none">{escape(NO_MONITOR_NOTE)}</p>')
        return out
    out.append(f'<div class="card" data-monitor="yes" data-confidence="{escape(ot.confidence.lower())}">')
    out.append(f"<p><strong>Monitor window:</strong> started {escape(ot.start)} · {escape(ot.duration)} · "
               f"{ot.sample_count} samples</p>")
    out.append(f"<p><strong>Confidence: {escape(ot.confidence)}.</strong> {escape(ot.explanation)}</p>")
    for label, values, thr, top in ot.sparklines:
        out.append(f'<div class="spark">{sparkline_svg(label, values, thr, top)}<span>{escape(label)}</span></div>')
    if ot.rows:
        out.append("<table><thead><tr><th>Metric</th><th>Mean</th><th>p95</th><th>Max</th>"
                   "<th>Time past threshold</th><th>Longest stretch</th><th>Readings</th></tr></thead><tbody>")
        out.extend(f"<tr><th>{escape(r.label)}</th><td>{escape(r.mean)}</td><td>{escape(r.p95)}</td>"
                   f"<td>{escape(r.max)}</td><td>{escape(r.above)}</td><td>{escape(r.streak)}</td>"
                   f"<td>{escape(r.readings)}</td></tr>" for r in ot.rows)
        out.append("</tbody></table>")
    out.extend(f'<p class="muted">{escape(n)}</p>' for n in ot.notes)
    out.append("</div>")
    return out


def _pill(status: str, text: str | None = None) -> str:
    """Status pill: icon + text label inside a coloured outline."""
    _, icon, label = STATUS_STYLE[status]
    return f'<span class="pill s-{status}"><span aria-hidden="true">{icon}</span>{escape(text or label)}</span>'


def _finding(f: Finding) -> str:
    """One finding card: badge, Confirmed/Possible tag, cause, What to do."""
    st = _SEV_TO_STATUS[f.severity]
    sev_pill = (f'<span class="pill s-{st}"><span aria-hidden="true">{_SEV_ICON[f.severity]}</span>'
                f"{escape(f.severity.value.capitalize())}</span>")
    parts = [f'<div class="card finding s-{st}"><div class="head">{sev_pill}'
             f'<span class="pill s-unknown">{escape(evidence_label(f))}</span>'
             f"<strong>{escape(f.title)}</strong></div>"]
    if f.cause:
        parts.append(f"<div>{escape(f.cause)}</div>")
    if f.fix:
        parts.append(f'<p class="todo"><strong>What to do:</strong> {escape(f.fix)}</p>')
    parts.append("</div>")
    return "".join(parts)


def render_html(snapshot: SystemSnapshot, findings: list[Finding], recs: list[Recommendation]) -> str:
    """Render a complete standalone HTML document for the given results."""
    v = verdict(snapshot, findings, recs)
    vst = _LIGHT_TO_STATUS[v.light]
    host = snapshot.os.hostname if snapshot.os and snapshot.os.hostname else "this PC"
    score = "?" if v.score is None else str(v.score)

    p: list[str] = [
        "<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>My PC Health Report: {escape(host)}</title>",
        # Changed in v0.2.0: BRAND_CSS appended for the NiyiSoft mark.
        f"<style>{_CSS}{BRAND_CSS}</style></head><body><main>",
        f"<h1>My PC Health Report <span class=\"muted\">· {escape(host)}</span></h1>",
        # 1. Verdict banner. data-light lets tests/tools read the result.
        f'<section class="card banner s-{vst}" data-light="{v.light}" role="status">'
        f'<div><div class="big"><span aria-hidden="true">{STATUS_STYLE[vst][1]}</span> {escape(v.label)}</div>'
        f"<div>{escape(v.sentence)}</div></div>"
        f'<div class="score"><b>{score}</b><span class="muted">health score / 100</span></div></section>',
    ]

    # 2. Metric cards
    p.append('<h2>At a glance</h2><div class="grid">')
    for c in metric_cards(snapshot):
        p.append(f'<div class="card metric s-{c.status}" data-status="{c.status}">'
                 f'<div class="name">{escape(c.name)}</div><div class="value">{escape(c.value)}</div>'
                 f"{_pill(c.status)}<div class=\"muted\" style=\"margin-top:6px\">{escape(c.meaning)}</div></div>")
    p.append("</div>")

    # 3. When the readings were taken
    p.append('<p class="muted">' + " · ".join(escape(x) for x in reading_times(snapshot)) + "</p>")

    # 4. Fixes: free first, then purchases
    p.append("<h2>What to do</h2><h3>Free fixes first</h3>")
    fixes = free_fixes(findings)
    if fixes:
        p.append('<ol class="card">')
        p.extend(f"<li><strong>{escape(f.title)}:</strong> {escape(text)}</li>" for f, text in fixes)
        p.append("</ol>")
    else:
        p.append('<p class="card">Nothing to fix right now.</p>')
    p.append("<h3>Upgrades worth buying</h3>")
    if recs:
        p.append('<ol class="card">')
        for r in recs:
            urgent = " " + _pill(BAD, "Urgent") if r.urgent else ""
            p.append(f"<li><strong>{escape(r.title)}</strong>{urgent} "
                     f'<span class="muted">impact {r.expected_impact}/100 · cost {escape(r.cost_tier.value)}</span>'
                     f"<br>{escape(r.reason)}</li>")
        p.append("</ol>")
    else:
        p.append('<p class="card">No upgrades needed.</p>')

    # 5. Findings grouped by severity
    p.append("<h2>Findings</h2>")
    groups = group_findings(findings)
    if not groups:
        p.append('<p class="card">No checks produced results.</p>')
    for sev, items in groups:
        p.append(f"<h3>{escape(SEVERITY_GROUP_TITLE[sev])} ({len(items)})</h3>")
        p.extend(_finding(f) for f in items)

    # 6. Over time (Added in v0.2.0)
    p.extend(_over_time_html(over_time(snapshot)))

    # 7. System overview
    p.append('<h2>System overview</h2><div class="card"><table><tbody>')
    p.extend(f"<tr><th>{escape(k)}</th><td>{escape(val)}</td></tr>" for k, val in overview_rows(snapshot))
    p.append("</tbody></table></div>")

    # 8. Benchmark (Added in v0.1.0, follow-up): raw numbers only, no colours,
    # because scores are only comparable between runs on the same PC.
    bench = benchmark_rows(snapshot)
    if bench:
        p.append('<h2>Benchmark</h2><div class="card"><table><tbody>')
        p.extend(f"<tr><th>{escape(k)}</th><td>{escape(val)}</td></tr>" for k, val in bench)
        p.append(f'</tbody></table><p class="muted">{escape(BENCHMARK_NOTE)}</p></div>')
    # Changed in v0.2.0: "Possible cause" may now come from a monitor window, not only a single reading.
    p.append('<p class="muted">Thresholds are rules of thumb. "Confirmed" = measured or reported by the device; '
             '"Possible cause" = inferred, not directly measured. Generated by pcdiag.</p>')
    # 9. Maker's mark (Added in v0.2.0): bottom-right "Made by NiyiSoft" badge, after all content.
    p.append(brand_html())
    p.append("</main></body></html>")
    return "\n".join(p)
