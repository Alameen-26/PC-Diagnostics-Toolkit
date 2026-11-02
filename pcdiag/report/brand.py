# pcdiag/report/brand.py
# Added in v0.2.0: "Made by NiyiSoft" maker's mark for the HTML report.
"""NiyiSoft maker's mark (the author's personal brand) for generated reports.

NiyiSoft comes from the author's Yoruba middle name, Adeniyi. The mark is the
stacked, calligraphic "Made by NiyiSoft" badge with a small gold crown, placed
in the bottom-right corner of the report, small and subtle, so the report's
own content always stays the focus.

Design rules for this file (Added in v0.2.0):
* The report must stay one self-contained file (no external assets), so the
  two badge PNGs are embedded as base64 ``data:`` URIs. Each is a 320x174
  palette PNG of about 6 KB, shown at 140 px wide (sharp on 2x screens).
* Changed in v0.2.0: the PNG bytes are kept as base64 text in
  ``niyisoft_badge_data.py`` instead of ``assets/*.png`` files. The repo is
  pushed through an API that only handles text files, and the strings drop
  straight into the ``data:`` URIs, so the report stays self-contained.
* Two variants: navy text for light mode, cream text for dark mode. CSS in
  ``html.py`` shows the right one via ``prefers-color-scheme``.
* If the badge data is missing or empty (e.g. a broken install), fall back to
  plain text so a report is never lost because of a logo.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape

# Changed in v0.2.0: badge images come from base64 text constants, not PNG files.
try:
    from . import niyisoft_badge_data as _badge_data
except ImportError:  # broken install: brand_html() falls back to text
    _badge_data = None

BRAND_NAME = "NiyiSoft"            # Added in v0.2.0
BRAND_TEXT = "Made by NiyiSoft"     # Added in v0.2.0: also the image alt text
# Changed in v0.2.0: names of the base64 constants in niyisoft_badge_data.py
# (previously PNG file names in assets/).
_LIGHT_MODE_FILE = "BADGE_PNG_B64"         # navy script, for light backgrounds
_DARK_MODE_FILE = "BADGE_LIGHT_PNG_B64"    # cream script, for dark backgrounds

# Added in v0.2.0: styling for the mark. Static (not position:fixed) so it never
# covers report content or prints on top of a table.
BRAND_CSS = """
.brand{display:flex;justify-content:flex-end;margin:28px 0 8px}
.brand img{width:140px;height:auto;opacity:.92}
.brand .on-dark{display:none}
@media (prefers-color-scheme: dark){.brand .on-light{display:none}.brand .on-dark{display:inline}}
"""


@lru_cache(maxsize=None)
def _data_uri(name: str) -> str | None:
    """Return the named badge as a base64 PNG data URI, or None if it's unavailable.

    Changed in v0.2.0: reads the base64 text constant ``name`` from
    ``niyisoft_badge_data`` (no file I/O and no re-encoding needed).
    """
    b64 = getattr(_badge_data, name, None) if _badge_data is not None else None
    if not isinstance(b64, str) or not b64:
        return None
    return "data:image/png;base64," + b64


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
