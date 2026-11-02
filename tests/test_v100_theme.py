"""v1.0.0: light/dark theme for the HTML report.

Added in v1.0.0. The theme must load after the built-in CSS, follow the system
setting with no scripts, keep the report self-contained, print in light, and
keep every text colour readable (WCAG AA) in both palettes.
"""
from __future__ import annotations

import base64
import re
import struct
from pathlib import Path

import pytest

from pcdiag.analysis import analyze, recommend_upgrades
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.report import niyisoft_badge_data, render_html
from pcdiag.report.brand import BRAND_CSS
from pcdiag.report.theme import THEME_CSS

ROOT = Path(__file__).resolve().parents[1]


def _html(name: str = "slow_old_laptop") -> str:
    snap = ALL_FIXTURES[name]() if callable(ALL_FIXTURES[name]) else ALL_FIXTURES[name]
    findings = analyze(snap)
    return render_html(snap, findings, recommend_upgrades(snap, findings))


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _light() -> dict[str, str]:
    return _tokens(THEME_CSS[THEME_CSS.index(":root{"):THEME_CSS.index("/* ---------- Dark tokens")])


def _dark() -> dict[str, str]:
    start = THEME_CSS.index(':root[data-theme="dark"]{')
    return _tokens(THEME_CSS[start:THEME_CSS.index("}", start)])


def _lum(hex_: str) -> float:
    def ch(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("fixture", sorted(ALL_FIXTURES))
def test_theme_is_in_every_report_after_the_builtin_css(fixture):
    html = _html(fixture)
    style = html[html.index("<style>"):html.index("</style>")]
    assert THEME_CSS in style
    assert style.index(BRAND_CSS) < style.index(THEME_CSS)   # theme comes last, so it wins
    assert style.count("<style>") == 1


def test_report_stays_self_contained_and_script_free():
    html = _html()
    assert "<script" not in html and "<link" not in html
    assert "@import" not in THEME_CSS and "@font-face" not in THEME_CSS
    for url in re.findall(r"url\(([^)]*)\)", THEME_CSS):
        assert url.strip("\"'").startswith("data:")          # nothing fetched over the network
    assert "http://" not in THEME_CSS.replace("http://www.w3.org/2000/svg", "").replace("http%3A//www.w3.org/2000/svg", "")
    assert "https://" not in THEME_CSS


def test_theme_follows_the_system_setting_and_honours_data_theme():
    assert "@media (prefers-color-scheme: dark)" in THEME_CSS
    assert ':root:not([data-theme="light"])' in THEME_CSS
    assert ':root[data-theme="dark"]' in THEME_CSS


def test_light_and_dark_palettes_define_the_same_tokens():
    light, dark = _light(), _dark()
    for key in ("bg", "card", "text", "muted", "green", "amber", "red", "border", "band"):
        assert key in light and key in dark


@pytest.mark.parametrize("palette", ["light", "dark"])
def test_text_colours_meet_wcag_aa(palette):
    t = _light() if palette == "light" else _dark()
    for fg in ("text", "muted", "green", "amber", "red"):
        for bg in ("card", "bg"):
            assert _contrast(t[fg], t[bg]) >= 4.5, (palette, fg, bg)
    for fg, bg in (("green", "green-bg"), ("amber", "amber-bg"), ("red", "red-bg"), ("muted", "grey-bg")):
        assert _contrast(t[fg], t[bg]) >= 4.5, (palette, fg, bg)   # pills on their tint
    assert _contrast(t["band-text"], t["band"]) >= 4.5
    assert _contrast(t["border-ui"], t["card"]) >= 3.0             # pill outlines (UI parts)


def test_print_always_uses_the_light_palette():
    block = THEME_CSS[THEME_CSS.index("@media print{"):]
    # Same specificity as the dark selectors and later in the file, so print wins.
    assert ':root:not([data-theme="light"])' in block and ":root[data-theme]" in block
    assert "--bg:#fff;" in block and "--card:#fff;" in block
    assert _tokens(block)["text"] == _light()["text"]
    assert ".brand .on-dark{display:none!important}" in block


def test_wide_tables_scroll_on_phones_instead_of_the_page():
    block = THEME_CSS[THEME_CSS.index("@media (max-width:640px)"):]
    assert "overflow-x:auto" in block.split("@media print")[0]


def test_dark_badge_is_a_real_image_not_a_css_swap():
    # The white/gold badge is the dark-mode PNG itself, so it works in Firefox too
    # and the CSS carries no extra copy of it.
    assert "content:url" not in THEME_CSS and "image/png" not in THEME_CSS
    raw = base64.b64decode(niyisoft_badge_data.BADGE_LIGHT_PNG_B64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", raw[16:24])
    assert abs(w / h - 140 / 76) < 0.02                            # same shape as the <img> box
    assert len(niyisoft_badge_data.BADGE_LIGHT_PNG_B64) < 12_000


def test_readme_uses_light_and_dark_images_with_picture():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("banner", "report_screenshot"):
        # Changed in v1.0.0: the images are SVG text files wrapping a base64 PNG (no binaries in the repo)
        assert f'srcset="docs/brand/{name}_dark.svg"' in readme
        assert f'src="docs/brand/{name}_light.svg"' in readme
        for variant in ("light", "dark"):
            svg = (ROOT / "docs" / "brand" / f"{name}_{variant}.svg").read_text(encoding="utf-8")
            assert svg.startswith("<svg") and 'href="data:image/png;base64,' in svg
            assert not (ROOT / "docs" / "brand" / f"{name}_{variant}.png").exists()
    assert readme.count('<source media="(prefers-color-scheme: dark)"') >= 3
    assert "All rights reserved" in readme and "MIT" not in readme


def test_changelog_has_the_v040_theme_entry():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v1.0.0 (2026-11-02)" in text
    assert "Light/dark report theme" in text
    assert text.index("## v1.0.0") < text.index("## v0.3.0")
