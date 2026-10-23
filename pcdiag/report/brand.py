# pcdiag/report/brand.py
# Added in v0.2.0: "Made by NiyiSoft" maker's mark for the HTML report.
"""NiyiSoft maker's mark (the author's personal brand) for generated reports.

NiyiSoft comes from the author's Yoruba middle name, Adeniyi. The mark is the
stacked, calligraphic "Made by NiyiSoft" badge with a small gold crown, placed
in the bottom-right corner of the report, small and subtle, so the report's
own content always stays the focus.

Design rules for this file (Added in v0.2.0):
* The report must stay one self-contained file (no external assets), so the
  two PNGs in ``assets/`` are embedded as base64 ``data:`` URIs. Each is a
  320x174 palette PNG of about 6 KB, shown at 140 px wide (sharp on 2x screens).
* Two variants: navy text for light mode, cream text for dark mode. CSS in
  ``html.py`` shows the right one via ``prefers-color-scheme``.
* If the asset files are missing (e.g. a broken install), fall back to plain
  text so a report is never lost because of a logo.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

BRAND_NAME = "NiyiSoft"            # Added in v0.2.0
BRAND_TEXT = "Made by NiyiSoft"     # Added in v0.2.0: also the image alt text
_ASSETS = Path(__file__).with_name("assets")
_LIGHT_MODE_FILE = "niyisoft-badge.png"        # navy script, for light backgrounds
_DARK_MODE_FILE = "niyisoft-badge-light.png"   # cream script, for dark backgrounds

# Added in v0.2.0: styling for the mark. Static (not position:fixed) so it never
# covers report content or prints on top of a table.
BRAND_CSS = """
.brand{display:flex;justify-content:flex-end;margin:28px 0 8px}
.brand img{width:140px;height:auto;opacity:.92}
.brand .on-dark{display:none}
@media (prefers-color-scheme: dark){.brand .on-light{display:none}.brand .on-dark{display:inline}}
"""


@lru_cache(maxsize=None)
def _data_uri(filename: str) -> str | None:
    """Return the asset as a base64 PNG data URI, or None if it can't be read."""
    try:
        raw = (_ASSETS / filename).read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def brand_html() -> str:
    """HTML for the bottom-right "Made by NiyiSoft" mark (Added in v0.2.0)."""
    light, dark = _data_uri(_LIGHT_MODE_FILE), _data_uri(_DARK_MODE_FILE)
    alt = escape(BRAND_TEXT)
    if not (light and dark):  # fallback: text-only mark
        return f'<footer class="brand muted" data-brand="text">{alt}</footer>'
    return (f'<footer class="brand" data-brand="niyisoft">'
            f'<img class="on-light" src="{light}" alt="{alt}" width="140" height="76">'
            f'<img class="on-dark" src="{dark}" alt="" aria-hidden="true" width="140" height="76">'
            f"</footer>")
