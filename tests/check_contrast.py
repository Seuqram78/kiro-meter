#!/usr/bin/env python3
"""Measured-contrast gate for the render palette (design evaluation A28).

The design review's dominant finding was that the most important figures were
the least legible: the credits column sat at 1.45:1 and the verdict line at
1.92:1 on the fixed ``#f2ecbc`` terminal background, because they were drawn
with ``bright_*`` ANSI colours whose high luminance collapses toward a light
background. This gate makes that class of regression fail ``lint.sh``.

It encodes the design's colour contract, all of it statically checkable:

* **No bright half (A25/A26).** No token may use a ``bright_*`` colour as a
  foreground or as a background fill. Bright ANSI variants are defined as
  high-luminance, so on a light theme they collapse toward the background -
  this is the measured root cause. Banned outright, no maths required.

* **The legible channel clears the text floor (§2.2 rec 1).** The tokens that
  carry the primary quantitative payload - the credits value and the muted
  body/reference text - must be pure default-foreground (optionally ``bold`` /
  ``dim`` / ``italic``), never a hue. The report measured default foreground at
  6.19:1 (versus 1.45:1 for the old bright-cyan credits), so requiring these to
  be hue-free *is* the contrast fix, and is verified by rejecting any colour
  word on them and confirming the default foreground clears 4.5:1.

* **Decorative hue stays in the sanctioned low-luminance half (A24) and is
  redundant, never the sole carrier (A34).** Colour tokens may only use the
  palette the report sanctions - ``red``/``green``/``yellow``/``blue``/
  ``magenta``/non-bright ``cyan`` - because on a light background even these sit
  below the non-text floor, so they must always be paired with a glyph or a
  bar length (which they are: the ✓/✗ verdicts, the runout ✗, the sort arrow,
  the bar fills). The gate enforces the palette restriction; the redundant
  glyph pairing is covered by the render tests.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# The fixed terminal background the whole design targets (§ "Fixed constraint")
# and the report's measured default body foreground on that theme.
_BACKGROUND_HEX = "f2ecbc"
_DEFAULT_FG_HEX = "545464"
# WCAG 2.2 minimum contrast ratios.
_TEXT_MIN = 4.5  # 1.4.3, small text
# sRGB linearisation threshold from the WCAG relative-luminance definition.
_SRGB_LINEAR_THRESHOLD = 0.03928
# The low-luminance palette half the report sanctions for decorative hue
# (A24): the non-bright colours, plus attribute-only words that carry no hue.
_SANCTIONED_HUES = frozenset({"red", "green", "yellow", "blue", "magenta", "cyan"})
_ATTRIBUTES = frozenset(
    {"bold", "italic", "dim", "underline", "blink", "reverse", "strike", ""}
)
# Tokens that carry the primary quantitative/text payload: they must be pure
# default foreground (no hue), which is the only channel that clears the text
# floor on the light theme.
_LEGIBLE_TOKENS = ("STYLE_VALUE", "STYLE_MUTED", "_TRACK_STYLE", "_EVEN_FILL_STYLE")
# Tokens that carry decorative or redundantly-coded hue: they must avoid the
# bright half and stay within the sanctioned palette.
_DECORATIVE_TOKENS = (
    "STYLE_OVER",
    "STYLE_UNDER",
    "STYLE_NEAR",
    "_SHARE_STYLE",
    "_SORT_COLUMN_STYLE",
)


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of an ``rrggbb`` hex string."""
    channels = []
    for i in range(0, 6, 2):
        value = int(hex_colour[i : i + 2], 16) / 255.0
        linear = (
            value / 12.92
            if value <= _SRGB_LINEAR_THRESHOLD
            else ((value + 0.055) / 1.055) ** 2.4
        )
        channels.append(linear)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(fg_hex: str, bg_hex: str) -> float:
    """WCAG 2.2 contrast ratio between two ``rrggbb`` hex colours."""
    lighter = max(_relative_luminance(fg_hex), _relative_luminance(bg_hex))
    darker = min(_relative_luminance(fg_hex), _relative_luminance(bg_hex))
    return (lighter + 0.05) / (darker + 0.05)


def _colour_words(style: str) -> tuple[list[str], list[str]]:
    """Split ``style`` into (foreground colour words, background colour words).

    Attribute words (``bold``/``dim``/``italic``/…) are dropped; the token
    after ``on`` is a background, everything else non-attribute is a foreground.
    """
    fg: list[str] = []
    bg: list[str] = []
    skip_next = False
    for part in style.split():
        if skip_next:
            bg.append(part)
            skip_next = False
        elif part == "on":
            skip_next = True
        elif part not in _ATTRIBUTES:
            fg.append(part)
    return fg, bg


def _check_no_bright(name: str, style: str) -> list[str]:
    """Fail if any colour word in ``style`` is a bright_* variant (A25/A26)."""
    fg, bg = _colour_words(style)
    return [
        f"{name}={style!r} uses banned bright colour {word!r}"
        for word in (*fg, *bg)
        if word.startswith("bright_")
    ]


def _check_legible(name: str, style: str) -> list[str]:
    """Fail if a payload token carries a hue instead of pure default foreground."""
    fg, bg = _colour_words(style)
    if fg or bg:
        message = (
            f"{name}={style!r} carries a hue; payload text must be default"
            " foreground (bold/dim/italic only) to hold the text-contrast floor"
        )
        return [message]
    return []


def _outside_palette_message(name: str, style: str, word: str) -> str:
    """Build the "colour outside the sanctioned palette" diagnostic (A24)."""
    return (
        f"{name}={style!r} uses colour {word!r} outside the sanctioned"
        " low-luminance palette"
    )


def _check_decorative(name: str, style: str) -> list[str]:
    """Fail if a decorative token strays outside the sanctioned palette (A24)."""
    fg, bg = _colour_words(style)
    return [
        _outside_palette_message(name, style, word)
        for word in (*fg, *bg)
        if word not in _SANCTIONED_HUES
    ]


def main() -> int:
    """Audit every semantic token; return non-zero on any violation."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    render = importlib.import_module("kiro_meter.render")
    problems: list[str] = []
    default_contrast = _contrast(_DEFAULT_FG_HEX, _BACKGROUND_HEX)
    if default_contrast < _TEXT_MIN:
        problems.append(
            f"default foreground #{_DEFAULT_FG_HEX} contrast {default_contrast:.2f}:1"
            f" on #{_BACKGROUND_HEX} is below the {_TEXT_MIN:.1f}:1 text floor"
        )
    for name in _LEGIBLE_TOKENS:
        style = getattr(render, name)
        problems.extend(_check_no_bright(name, style))
        problems.extend(_check_legible(name, style))
    for name in _DECORATIVE_TOKENS:
        style = getattr(render, name)
        problems.extend(_check_no_bright(name, style))
        problems.extend(_check_decorative(name, style))
    if problems:
        sys.stderr.write("contrast gate failed:\n")
        for problem in problems:
            sys.stderr.write(f"  {problem}\n")
        return 1
    sys.stdout.write(
        "contrast gate passed: payload tokens are default foreground"
        f" ({default_contrast:.2f}:1 on #{_BACKGROUND_HEX}), decorative hue is"
        " bright-free and within the sanctioned palette\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
