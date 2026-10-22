# pcdiag/report/html.py
# Added in v0.1.0
# Changed in v0.1.0 (follow-up): raw benchmark section with comparison-only note.
"""Self-contained HTML report ("My PC Health Report").

Layout, top to bottom:
1. Verdict banner: one overall status (colour + icon + word), a plain-language
   summary sentence and a 0-100 health score.
2. Metric cards: CPU, memory, disk space, temperature, battery, disk health.
3. When the readings were taken (and sample durations).
4. Fixes: "Free fixes first", then "Upgrades worth buying" (ranked).
5. Findings grouped by severity, each tagged Confirmed / Possible cause, with
   a "What to do" line.
6. System overview.

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
from .summary import (
    BAD, BENCHMARK_NOTE, GOOD, SEVERITY_GROUP_TITLE, STATUS_STYLE, UNKNOWN, WARN, benchmark_rows,
    evidence_label, free_fixes, group_findings, metric_cards, overview_rows, reading_times, verdict,
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
"""


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
        f"<style>{_CSS}</style></head><body><main>",
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

    # 6. System overview
    p.append('<h2>System overview</h2><div class="card"><table><tbody>')
    p.extend(f"<tr><th>{escape(k)}</th><td>{escape(val)}</td></tr>" for k, val in overview_rows(snapshot))
    p.append("</tbody></table></div>")

    # 7. Benchmark (Added in v0.1.0, follow-up): raw numbers only, no colours,
    # because scores are only comparable between runs on the same PC.
    bench = benchmark_rows(snapshot)
    if bench:
        p.append('<h2>Benchmark</h2><div class="card"><table><tbody>')
        p.extend(f"<tr><th>{escape(k)}</th><td>{escape(val)}</td></tr>" for k, val in bench)
        p.append(f'</tbody></table><p class="muted">{escape(BENCHMARK_NOTE)}</p></div>')
    p.append('<p class="muted">Thresholds are rules of thumb. "Confirmed" = measured or reported by the device; '
             '"Possible cause" = inferred from a single reading. Generated by pcdiag.</p></main></body></html>')
    return "\n".join(p)
