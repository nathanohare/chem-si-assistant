"""Page header, footer and page numbers (Project settings ▸ Page, SPEC §13k.2a).

Stored in project.json as

    "page": {"header": {"left": "", "center": "", "right": ""},
             "footer": {"left": "", "center": "{page}", "right": ""},   (optional)
             "number_prefix": "S", "number_style": "1", "number_size": 0,
             "number_bold": false, "number_on_cover": false,
             "author_style": "full"}

Header and footer texts may contain the fill-ins {page}, {pages}, {title}, {authors}
and {date}. author_style says how {authors} is written: "full" lists every name as
entered ("Nathan P. O'Hare, Jane Smith, Ian Brown"); "et_al" shortens it to the first
author's surname ("O'Hare et al."; "O'Hare and Smith" for two authors, "O'Hare" for one). A footer that is not stored (or equals the journal style's own) follows the
journal style: ACS and RSC centre the number, Wiley puts it on the right.
number_size 0 means "the style's text size".
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

FILL_INS = ("page", "pages", "title", "authors", "date")
FILL_IN_RE = re.compile(r"\{(page|pages|title|authors|date)\}")
POSITIONS = ("left", "center", "right")
NUMBER_STYLES = {"1": "1, 2, 3", "i": "i, ii, iii", "I": "I, II, III"}
EMPTY_LINE = {"left": "", "center": "", "right": ""}
# Where each journal style prints the page number (must match styles/*.typ).
STYLE_FOOTERS = {"ACS": {"left": "", "center": "{page}", "right": ""},
                 "Wiley": {"left": "", "center": "", "right": "{page}"},
                 "RSC": {"left": "", "center": "{page}", "right": ""}}
DEFAULTS: dict[str, Any] = {"header": dict(EMPTY_LINE), "number_prefix": "S",
                            "number_style": "1", "number_size": 0.0, "number_bold": False,
                            "number_on_cover": False, "author_style": "full"}
# How {authors} is written (Project settings ▸ Page).
AUTHOR_STYLES = {"full": "All names (as entered)", "et_al": "First author et al."}
# Words that belong to the surname when they come just before it ("van der Waals").
SURNAME_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos",
                     "du", "la", "le", "ter", "ten", "bin", "al", "el", "st."}
# Words after the surname that are not part of it ("Smith Jr.").
NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def style_footer(journal_style: str) -> dict[str, str]:
    return dict(STYLE_FOOTERS.get(journal_style, STYLE_FOOTERS["ACS"]))


def _line(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    return {pos: str(raw.get(pos, "") or "") for pos in POSITIONS}


def page_settings(settings: dict) -> dict[str, Any]:
    """The page settings in effect, with defaults filled in. "footer" is always given
    (the journal style's own if none is stored); "footer_is_style" says which."""
    raw = settings.get("page") if isinstance(settings.get("page"), dict) else {}
    out = copy.deepcopy(DEFAULTS)
    out["header"] = _line(raw.get("header")) or dict(EMPTY_LINE)
    stored = _line(raw.get("footer"))
    default = style_footer(settings.get("journal_style", "ACS"))
    out["footer"] = stored or default
    out["footer_is_style"] = stored is None or stored == default
    out["number_prefix"] = str(raw.get("number_prefix", DEFAULTS["number_prefix"]))
    style = str(raw.get("number_style", "1"))
    out["number_style"] = style if style in NUMBER_STYLES else "1"
    try:
        out["number_size"] = max(0.0, float(raw.get("number_size", 0) or 0))
    except (TypeError, ValueError):
        out["number_size"] = 0.0
    out["number_bold"] = bool(raw.get("number_bold", False))
    out["number_on_cover"] = bool(raw.get("number_on_cover", False))
    style = str(raw.get("author_style", "full"))
    out["author_style"] = style if style in AUTHOR_STYLES else "full"
    return out


def store_page_settings(settings: dict, values: dict[str, Any]) -> None:
    """Save `values` (as from page_settings) into settings["page"]. A footer equal to the
    journal style's own is not stored, so it keeps following the style."""
    page = {"header": _line(values.get("header")) or dict(EMPTY_LINE),
            "number_prefix": str(values.get("number_prefix", "S")),
            "number_style": values.get("number_style", "1"),
            "number_size": float(values.get("number_size", 0) or 0),
            "number_bold": bool(values.get("number_bold", False)),
            "number_on_cover": bool(values.get("number_on_cover", False))}
    if values.get("author_style", "full") in AUTHOR_STYLES and \
            values.get("author_style", "full") != "full":
        page["author_style"] = values["author_style"]   # (the default is not stored)
    footer = _line(values.get("footer"))
    if footer is not None and footer != style_footer(settings.get("journal_style", "ACS")):
        page["footer"] = footer
    settings["page"] = page


def roman(n: int) -> str:
    out = []
    for value, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
                           (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
                           (4, "IV"), (1, "I")):
        while n >= value:
            out.append(letters)
            n -= value
    return "".join(out)


def format_number(n: int, prefix: str = "S", style: str = "1") -> str:
    """Page `n` as printed: "S1", "Sii", "SIV"…"""
    if style == "i":
        return prefix + roman(n).lower()
    if style == "I":
        return prefix + roman(n)
    return f"{prefix}{n}"


def date_text(d: date | None = None) -> str:
    """"3 October 2026" (always English, whatever the computer's language)."""
    d = d or date.today()
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def surname(name: str) -> str:
    """The family name in an author name as typed: "Nathan P. O'Hare" → "O'Hare",
    "Johannes D. van der Waals" → "van der Waals", "Martin Luther King Jr." → "King".
    A name typed surname-first with a comma ("O'Hare, N. P.") gives the part before
    the comma. For anything this gets wrong, type the header text by hand instead of
    using {authors}."""
    name = " ".join(name.replace("*", " ").split())
    if "," in name:
        first = name.split(",")[0].strip()
        rest = name.split(",", 1)[1].strip().lower().rstrip(".")
        if first and rest + "." not in NAME_SUFFIXES and rest not in NAME_SUFFIXES:
            return first          # "O'Hare, N. P." (not "King, Jr.")
        name = first
    words = name.split()
    while len(words) > 1 and words[-1].lower() in NAME_SUFFIXES:
        words.pop()
    if not words:
        return ""
    start = len(words) - 1
    while start > 0 and words[start - 1].lower() in SURNAME_PARTICLES:
        start -= 1
    if start == 0 and len(words) > 1:
        start = len(words) - 1    # every word looked like a particle: keep the last
    return " ".join(words[start:])


def author_names(settings: dict, style: str | None = None) -> str:
    """The text {authors} prints. `style` is "full" or "et_al" (default: the project's
    Project settings ▸ Page choice)."""
    from .front import authors   # (rows that are not dictionaries are skipped)
    names = [str(a.get("name", "")).strip() for a in authors(settings)
             if str(a.get("name", "")).strip()]
    if style is None:
        style = page_settings(settings)["author_style"]
    if style != "et_al" or not names:
        return ", ".join(names)
    first = surname(names[0]) or names[0]
    if len(names) == 1:
        return first
    if len(names) == 2:
        return f"{first} and {surname(names[1]) or names[1]}"
    return f"{first} et al."


def split_fill_ins(text: str) -> list[tuple[str, str]]:
    """[("text", "Page "), ("page", ""), ("text", " of "), ("pages", "")] for
    "Page {page} of {pages}". Unknown {words} stay as text."""
    out, pos = [], 0
    for m in FILL_IN_RE.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append((m.group(1), ""))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


def fill_in(text: str, settings: dict, page: int, pages: int, today: date | None = None,
            values: dict[str, Any] | None = None) -> str:
    """Plain-text result of a header/footer text (for the example in Project settings)."""
    v = values or page_settings(settings)
    known = {"page": format_number(page, v["number_prefix"], v["number_style"]),
             "pages": format_number(pages, v["number_prefix"], v["number_style"]),
             "title": str(settings.get("title") or ""), "authors": author_names(settings, v["author_style"]),
             "date": date_text(today)}
    return "".join(value if kind == "text" else known[kind]
                   for kind, value in split_fill_ins(text))


def example(settings: dict, values: dict[str, Any] | None = None, page: int = 2,
            pages: int = 20) -> str:
    """What page 2 of 20 will show (left | centre | right, "–" for empty)."""
    v = values or page_settings(settings)

    def line(texts: dict[str, str]) -> str:
        parts = [fill_in(texts[pos], settings, page, pages, values=v).strip()
                 for pos in POSITIONS]
        return " | ".join(p or "–" for p in parts) if any(parts) else "(empty)"

    return (f"On page {page} of {pages}:\nHeader: {line(v['header'])}\n"
            f"Footer: {line(v['footer'])}")
