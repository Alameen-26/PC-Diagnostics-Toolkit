# pcdiag/report/theme.py
# Added in v1.0.0: light/dark report theme (green "health check" look, NiyiSoft palette).
"""Light and dark theme for the HTML report (Added in v1.0.0).

The CSS below is appended after the report's built-in styles (``html._CSS`` and
``brand.BRAND_CSS``), so it only overrides colour tokens and restyles the
existing classes; the report markup is unchanged.

Rules for this file:
* The report must stay one self-contained file with no scripts: the theme
  follows the system setting via ``prefers-color-scheme``. An optional
  ``<html data-theme="light|dark">`` attribute is honoured too, but pcdiag
  never sets it (there is no in-report toggle because the report has no JS).
* The top band mark is an inline SVG ``data:`` URI, so nothing is fetched.
* No web fonts: Segoe UI / Cascadia Mono on Windows, system fonts elsewhere.
* Printing always uses the light palette.
* Every text colour meets WCAG AA (4.5:1) on its background in both themes.
"""
from __future__ import annotations

THEME_CSS = r"""
/* ---------- Light tokens (default) ---------- */
:root{
  color-scheme: light;
  --bg:#F3F6F2; --card:#FFFFFF; --text:#0E1210; --muted:#5C6166; --border:#D5DBD5; --border-ui:#8E9399;
  --green:#0B6B3A; --amber:#8A6A10; --red:#B3122B; --grey:#5C6166; --info:#0B6B3A;
  --green-bg:#E3F1E8; --amber-bg:#FDF7E6; --red-bg:#FCE8EB; --grey-bg:#EEF1ED;
  --band:#0A3B24; --band-text:#FFFFFF; --band-sub:#BFD8C8; --gold:#8A6A10; --gold-line:#D4A72C;
  --zebra:#F7F9F6; --shadow:0 1px 0 rgba(14,18,16,.04),0 8px 24px -14px rgba(10,59,36,.35);
  --focus:#0E1210; --mark:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%20viewBox%3D%220%200%2064%2064%22%20width%3D%2264%22%20height%3D%2264%22%3E%3Crect%20x%3D%2220%22%20y%3D%222%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2220%22%20y%3D%2253%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2230%22%20y%3D%222%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2230%22%20y%3D%2253%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2240%22%20y%3D%222%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2240%22%20y%3D%2253%22%20width%3D%224%22%20height%3D%229%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%222%22%20y%3D%2220%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2253%22%20y%3D%2220%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%222%22%20y%3D%2230%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2253%22%20y%3D%2230%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%222%22%20y%3D%2240%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2253%22%20y%3D%2240%22%20width%3D%229%22%20height%3D%224%22%20rx%3D%222%22%20fill%3D%22%239FC7B0%22/%3E%3Crect%20x%3D%2211%22%20y%3D%2211%22%20width%3D%2242%22%20height%3D%2242%22%20rx%3D%229%22%20fill%3D%22none%22%20stroke%3D%22%23FFFFFF%22%20stroke-width%3D%224%22/%3E%3Cpolyline%20points%3D%2217%2C33%2025%2C33%2029%2C24%2035%2C42%2039%2C29%2042%2C33%2047%2C33%22%20fill%3D%22none%22%20stroke%3D%22%23FFFFFF%22%20stroke-width%3D%224%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22/%3E%3Ccircle%20cx%3D%2247%22%20cy%3D%2233%22%20r%3D%223.2%22%20fill%3D%22%23E0B43C%22/%3E%3C/svg%3E");
  --font:"Segoe UI Variable Text","Segoe UI",Inter,system-ui,-apple-system,sans-serif;
  --font-head:"Segoe UI Variable Display","Segoe UI",Inter,system-ui,sans-serif;
  --mono:"Cascadia Mono",Consolas,"JetBrains Mono",ui-monospace,monospace;
}
/* ---------- Dark tokens ---------- */
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --bg:#0B0F0D; --card:#141A17; --text:#EEF2EF; --muted:#A3ADA7; --border:#26302B; --border-ui:#6E7A73;
    --green:#3FC27A; --amber:#E0B43C; --red:#F26B78; --grey:#A3ADA7; --info:#3FC27A;
    --green-bg:#0F2A1C; --amber-bg:#2A2410; --red-bg:#2E1418; --grey-bg:#1C2420;
    --band:#060807; --band-text:#FFFFFF; --band-sub:#A3ADA7; --gold:#E0B43C; --gold-line:#E0B43C;
    --zebra:#111613; --shadow:0 1px 0 rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.9);
    --focus:#E0B43C;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#0B0F0D; --card:#141A17; --text:#EEF2EF; --muted:#A3ADA7; --border:#26302B; --border-ui:#6E7A73;
  --green:#3FC27A; --amber:#E0B43C; --red:#F26B78; --grey:#A3ADA7; --info:#3FC27A;
  --green-bg:#0F2A1C; --amber-bg:#2A2410; --red-bg:#2E1418; --grey-bg:#1C2420;
  --band:#060807; --band-text:#FFFFFF; --band-sub:#A3ADA7; --gold:#E0B43C; --gold-line:#E0B43C;
  --zebra:#111613; --shadow:0 1px 0 rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.9);
  --focus:#E0B43C;
}

/* ---------- Base ---------- */
body{font-family:var(--font);font-size:15.5px;line-height:1.55;background:var(--bg);color:var(--text);font-variant-numeric:tabular-nums}
body::before{content:"PC Diagnostics Toolkit";display:flex;align-items:center;gap:12px;height:60px;padding:0 max(24px,calc((100% - 1000px)/2 + 16px));
  padding-left:calc(max(24px,calc((100% - 1000px)/2 + 16px)) + 42px);
  background:var(--band) var(--mark) no-repeat max(24px,calc((100% - 1000px)/2 + 16px)) center/30px 30px;
  color:var(--band-text);font:650 15px/1 var(--font-head);letter-spacing:.14em;text-transform:uppercase;border-bottom:2px solid var(--gold-line)}
main{max-width:1000px;margin:32px auto 40px;padding:0 16px}
:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
h1{font:700 30px/1.15 var(--font-head);letter-spacing:-.015em;margin:0 0 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
h1 .muted{font:600 13px/1 var(--mono);color:var(--muted);background:var(--card);border:1px solid var(--border);padding:7px 10px;border-radius:999px;letter-spacing:.02em}
h2{font:700 20px/1.2 var(--font-head);margin:36px 0 12px;display:flex;align-items:center;gap:10px;letter-spacing:-.005em}
h2::before{content:"";width:8px;height:8px;background:var(--gold);transform:rotate(45deg);flex:none}
h2::after{content:"";flex:1;height:1px;background:var(--border);margin-left:6px}
h3{font:650 14px/1.2 var(--font);text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:22px 0 10px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow)}
.muted{color:var(--muted)}
a{color:var(--green)}
code{font-family:var(--mono)}

/* ---------- Verdict banner + score ring ---------- */
.banner{border:1px solid var(--border);border-left:8px solid var(--c);background:linear-gradient(100deg,var(--cbg) 0%,var(--cbg) 55%,var(--card) 100%);padding:22px 24px;gap:24px}
.banner .big{font:750 30px/1.1 var(--font-head);letter-spacing:-.01em;margin-bottom:6px}
.banner > div:first-child > div:last-child{font-size:16.5px;max-width:60ch}
.score{min-width:120px}
.score b{width:96px;height:96px;margin:0 auto 6px;border-radius:50%;display:grid;place-items:center;font:800 36px/1 var(--font-head);
  border:7px solid var(--c);background:var(--card);box-shadow:inset 0 0 0 3px var(--cbg)}
.score .muted{font-size:12px;text-transform:uppercase;letter-spacing:.08em}

/* ---------- At a glance ---------- */
.grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px}
.metric{border-top:1px solid var(--border);position:relative;overflow:hidden;padding-top:20px}
.metric::before{content:"";position:absolute;left:0;right:0;top:0;height:5px;background:var(--c)}
.metric .name{font-size:11.5px;font-weight:700;letter-spacing:.12em;color:var(--muted)}
.metric .value{font:700 18px/1.25 var(--font-head);margin:6px 0 10px}
.pill{font-size:12px;font-weight:700;padding:3px 10px;border-width:1.5px;letter-spacing:.02em}
.s-unknown.pill{color:var(--muted);border-color:var(--border-ui)}

/* ---------- Numbered lists (free fixes, upgrades) ---------- */
ol.card{list-style:none;counter-reset:n;padding:8px 18px}
ol.card li{counter-increment:n;position:relative;padding:12px 0 12px 46px;margin:0;border-bottom:1px solid var(--border)}
ol.card li:last-child{border-bottom:0}
ol.card li::before{content:counter(n);position:absolute;left:0;top:11px;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;
  font:700 14px/1 var(--font-head);color:var(--green);background:var(--green-bg);border:1.5px solid var(--green)}
ol.card li > .muted{display:inline-block;font:600 12px/1 var(--mono);padding:5px 8px;border-radius:6px;background:var(--grey-bg);color:var(--muted);margin-left:6px;vertical-align:2px}

/* ---------- Findings ---------- */
.finding{border-left:5px solid var(--c);margin-bottom:12px}
.finding .head{margin-bottom:6px}
.finding .head strong{font-size:16px}
.todo{background:var(--grey-bg);border-radius:10px;padding:9px 12px;margin-top:10px;border:1px dashed var(--border-ui)}

/* ---------- Tables (overview, benchmark, over time) ---------- */
table{font-size:14.5px}
th,td{padding:10px 12px;border-bottom:1px solid var(--border)}
thead th{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);background:var(--grey-bg)}
tbody tr:nth-child(even){background:var(--zebra)}
tbody th{font-weight:600;color:var(--muted)}
table:not(:has(thead)) tbody th{width:34%}
tr:last-child th,tr:last-child td{border-bottom:0}
.card > table{margin:-4px 0}

/* ---------- Sparklines ---------- */
.spark svg{width:300px;height:48px;overflow:visible;padding:6px 8px;background:var(--grey-bg);border-radius:8px;border:1px solid var(--border)}
.spark .line{stroke:var(--green);stroke-width:1.8}
.spark .thr{stroke:var(--red);stroke-width:1.2;stroke-dasharray:5 4}

/* ---------- Made by NiyiSoft badge ---------- */
.brand{margin:36px 0 0;padding-top:18px;border-top:1px solid var(--border)}
.brand img{width:140px;opacity:1}
.brand .on-light{display:inline}.brand .on-dark{display:none}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]) .brand .on-light{display:none}
  :root:not([data-theme="light"]) .brand .on-dark{display:inline}
}
:root[data-theme="dark"] .brand .on-light{display:none}
:root[data-theme="dark"] .brand .on-dark{display:inline}

/* ---------- Motion + print ---------- */
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
/* Phones (Added in v1.0.0): wide tables (benchmark, over time) scroll inside their card
   instead of widening the whole page; sparklines shrink to fit. */
@media (max-width:640px){
  main{padding:0 10px}
  table:has(thead){display:block;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch}
  td,th{overflow-wrap:anywhere}
  .spark{flex-wrap:wrap}.spark svg{width:100%;max-width:300px}
  .banner{flex-wrap:wrap}.score{margin-left:0}
}
/* Print (Changed in v1.0.0): always the light palette, even when the system is dark.
   The selectors match the dark-token selectors' specificity and come later, so they win. */
@media print{
  :root,:root:not([data-theme="light"]),:root[data-theme]{color-scheme:light;
    --bg:#fff;--card:#fff;--text:#0E1210;--muted:#5C6166;--border:#D5DBD5;--border-ui:#8E9399;
    --green:#0B6B3A;--amber:#8A6A10;--red:#B3122B;--grey:#5C6166;--info:#0B6B3A;
    --green-bg:#E3F1E8;--amber-bg:#FDF7E6;--red-bg:#FCE8EB;--grey-bg:#EEF1ED;
    --gold:#8A6A10;--gold-line:#D4A72C;--zebra:#F7F9F6;--shadow:none;--focus:#0E1210}
  .brand .on-light{display:inline!important}.brand .on-dark{display:none!important}
  body::before{background-color:#fff;color:#0E1210;border-bottom:2px solid #0E1210;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .card{break-inside:avoid}
}
"""
