"""Text and table formatting for the whole SI (Project settings ▸ Text and tables).

Every value is optional: None (the default) keeps what the journal style does, so a
project that never changes them prints exactly as before. Only values that were set are
stored, in project.json:

    "formatting": {"line_spacing": 1.15,       body text: 1 = the style's own spacing
                   "space_before": 0,          pt above each paragraph
                   "space_after": 6,           pt below each paragraph
                   "caption_size": 9,          figure, table and scheme captions (pt)
                   "table_size": 9,            table cells (pt)
                   "table_header_size": 9,     the header rows' cells (pt)
                   "table_header_bold": true,  the header rows in bold
                   "table_notes_size": 8}      table footnotes (pt)

What each style does by itself: body text 11 pt (RSC 10.5 pt) with a line spacing of
0.6 em between lines and 1 em between paragraphs; captions 10 pt (RSC 9.5 pt); table
cells 10 pt (RSC 9.5 pt), header rows not bold; table footnotes 9 pt.

A single caption, table or cell can have its own size (tables: core/tables.py; figures:
the "caption_size" of their entry; schemes: their layout's "caption_size_pt"); those win
over these defaults. This module has no Qt code, so everything here is unit-tested.
"""

from __future__ import annotations

import math
from typing import Any

PROJECT_KEY = "formatting"

SIZE_KEYS = ("caption_size", "table_size", "table_header_size", "table_notes_size")
SPACE_KEYS = ("space_before", "space_after")
KEYS = ("line_spacing", *SPACE_KEYS, *SIZE_KEYS, "table_header_bold")

# Sensible ranges; anything outside them (a hand-edited file) is brought inside.
SIZE_RANGE = (4.0, 30.0)          # pt, any text size
LINE_SPACING_RANGE = (0.5, 4.0)   # times the style's own
SPACE_RANGE = (0.0, 72.0)         # pt

# The styles' own line spacing: 0.6 em between lines, i.e. about 1.3 em from one
# baseline to the next (Typst measures the gap from the baseline of a line to the top
# of capitals on the next, and capitals are about 0.7 em tall).
STYLE_LEADING_EM = 0.6
CAP_HEIGHT_EM = 0.7
# The styles leave 1 em between paragraphs: half of it counts as "before", half as "after".
STYLE_PARAGRAPH_EM = 1.0


def clean_size(value: Any) -> float | None:
    """A text size in pt read from a file or typed: a number within SIZE_RANGE (rounded
    to 0.1 pt), or None (= the default)."""
    return _clean_number(value, SIZE_RANGE, 1)


def _clean_number(value: Any, limits: tuple[float, float], digits: int) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    lo, hi = limits
    return round(min(max(number, lo), hi), digits)


def _clean(key: str, value: Any) -> Any:
    if key == "table_header_bold":
        return value if isinstance(value, bool) else None
    if key == "line_spacing":
        return _clean_number(value, LINE_SPACING_RANGE, 2)
    if key in SPACE_KEYS:
        return _clean_number(value, SPACE_RANGE, 1)
    if key in SIZE_KEYS:
        return clean_size(value)
    return None


def formatting(settings: dict) -> dict[str, Any]:
    """Every formatting value: the number (or True/False) set, or None for the style's own."""
    stored = settings.get(PROJECT_KEY)
    stored = stored if isinstance(stored, dict) else {}
    return {key: _clean(key, stored.get(key)) for key in KEYS}


def store_formatting(settings: dict, values: dict) -> None:
    """Keep the values that are set (not None); a line spacing of 1 and an unticked
    "header in bold" are the style's own, so they are not stored either."""
    out = {}
    for key in KEYS:
        value = _clean(key, values.get(key))
        if value is None or (key == "line_spacing" and value == 1.0) \
                or (key == "table_header_bold" and value is False):
            continue
        out[key] = value
    if out:
        settings[PROJECT_KEY] = out
    else:
        settings.pop(PROJECT_KEY, None)


def _pt(value: float) -> str:
    return f"{value:g}pt"


def leading_em(line_spacing: float) -> float:
    """Typst's `leading` for a line spacing given as a multiple of the style's own (1 =
    0.6 em; 2 = double the distance from one line to the next)."""
    return round((STYLE_LEADING_EM + CAP_HEIGHT_EM) * line_spacing - CAP_HEIGHT_EM, 3)


def paragraph_spacing(values: dict) -> str:
    """Typst's `spacing` between paragraphs: the space after one plus the space before
    the next (each half of the style's 1 em unless set); "" if neither is set."""
    before, after = values.get("space_before"), values.get("space_after")
    if before is None and after is None:
        return ""
    half = f"{STYLE_PARAGRAPH_EM / 2:g}em"
    parts = [_pt(before) if before is not None else half,
             _pt(after) if after is not None else half]
    return " + ".join(parts)


def typst_rules(settings: dict) -> str:
    """The Typst rules for the whole document, placed after the style's own (so they win);
    "" when nothing is set (the SI is printed exactly as the style does)."""
    v = formatting(settings)
    rules = []
    par = []
    if v["line_spacing"] is not None and v["line_spacing"] != 1.0:
        par.append(f"leading: {leading_em(v['line_spacing']):g}em")
    spacing = paragraph_spacing(v)
    if spacing:
        par.append(f"spacing: {spacing}")
    if par:
        rules.append(f"#set par({', '.join(par)})")
    if v["caption_size"] is not None:
        rules.append(f"#show figure.caption: set text(size: {_pt(v['caption_size'])})")
    return "\n".join(rules)


def caption_size_code(code: str, size: float | None) -> str:
    """A figure's or table's Typst call (`#si-table(…)`) with its own caption size: its
    caption printed at `size` pt. Unchanged when `size` is None. Works with any style
    (also one copied and changed by the user), as it only adds a show rule around it."""
    size = clean_size(size)
    if size is None or not code.startswith("#"):
        return code
    return f"#{{ show figure.caption: set text(size: {_pt(size)}); {code[1:]} }}"


def text_size_code(body: str, size: float | None, bold: bool = False) -> str:
    """Typst markup `body` printed at `size` pt (and in bold): a set rule at its start."""
    rules = []
    if size is not None:
        rules.append(f"size: {_pt(size)}")
    if bold:
        rules.append('weight: "bold"')
    if not rules:
        return body
    return f"#set text({', '.join(rules)}); {body}" if body else body


def table_defaults(settings: dict) -> dict[str, Any]:
    """The defaults every table is printed with: "size", "header_size", "header_bold",
    "notes_size" (None / False = the style's own)."""
    v = formatting(settings)
    return {"size": v["table_size"], "header_size": v["table_header_size"],
            "header_bold": bool(v["table_header_bold"]), "notes_size": v["table_notes_size"]}
