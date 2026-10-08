"""Front matter settings: cover page, table of contents, General Information, and the
choice of what to export (SPEC §13k.4, §13k.10).

Stored in project.json:

    "cover": {"heading": "..."}            only when it differs from the journal style's
                                           own heading ("Supporting Information")
    "toc": {"title", "depth", "figures", "tables", "schemes", "dots", "font_size"}
    "general_information": {"heading": "...",   only when not "General Information"
                            "numbered": true}   only when numbered like a section
    "export_excluded": ["cover", "toc", "general", "front", "spectra", "s-…", …]

The cover's extra text and the General Information text are .txt files in front/
(core/project.py). Title, authors and affiliations keep their old keys ("title",
"authors", "affiliations") so older projects open unchanged.
"""

from __future__ import annotations

from typing import Any

FRONT_ROOT = "front"   # the "Front matter" item itself (unticking it leaves out all three)
FRONT_PAGES = {"cover": "Cover page", "toc": "Table of contents",
               "general": "General Information"}
SPECTRA_KEY = "spectra"   # the spectra section/appendix in the export choice
# The "HRMS reports" sub-section of the Spectra section (SPEC §13n.3) in the export choice.
HRMS_KEY = "hrms_reports"

STYLE_COVER_HEADINGS = {"ACS": "Supporting Information", "Wiley": "Supporting Information",
                        "RSC": "Electronic Supplementary Information"}
DEFAULT_COVER_HEADING = "Supporting Information"
DEFAULT_GENERAL_HEADING = "General Information"

# How much the table of contents lists, from least to most.
TOC_DEPTHS = {"sections": "Sections only",
              "subsections": "Sections and sub-sections",
              "procedures": "Sections, sub-sections and general procedures",
              "compounds": "Sections, sub-sections, general procedures and every compound"}
TOC_LISTS = {"figures": ("List of Figures", "si-figure"),
             "tables": ("List of Tables", "table"),
             "schemes": ("List of Schemes", "scheme")}   # (title, Typst figure kind)
DEFAULT_TOC: dict[str, Any] = {"title": "Table of Contents", "depth": "procedures",
                               "figures": False, "tables": False, "schemes": False,
                               "dots": True, "font_size": 0.0}   # 0 = same as the text


def _stored(settings: dict, key: str) -> dict[str, Any]:
    """A stored dictionary setting; {} when it is missing or not a dictionary (e.g. the
    project.json was edited by hand), so a wrong value never stops the program."""
    value = settings.get(key)
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------- cover page

def _rows(settings: dict, key: str) -> list[dict[str, Any]]:
    value = settings.get(key)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def authors(settings: dict) -> list[dict[str, Any]]:
    """The authors table (rows that are not dictionaries are skipped). Each row's
    "affiliations" is a list of markers."""
    out = []
    for a in _rows(settings, "authors"):
        marks = a.get("affiliations")
        marks = [str(m) for m in marks] if isinstance(marks, list) else (
            [str(marks)] if marks else [])
        out.append({**a, "affiliations": marks})
    return out


def affiliations(settings: dict) -> list[dict[str, Any]]:
    return _rows(settings, "affiliations")


def style_cover_heading(style: str) -> str:
    return STYLE_COVER_HEADINGS.get(style, DEFAULT_COVER_HEADING)


def cover_heading(settings: dict) -> str:
    """The heading printed at the top of the cover (the style's own unless changed)."""
    stored = str(_stored(settings, "cover").get("heading", "") or "").strip()
    return stored or style_cover_heading(settings.get("journal_style", "ACS"))


def cover_heading_is_custom(settings: dict) -> bool:
    return bool(str(_stored(settings, "cover").get("heading", "") or "").strip())


def store_cover_heading(settings: dict, text: str) -> None:
    """Store the cover heading; the journal style's own heading (or nothing) is not
    stored, so the cover keeps following the style."""
    text = text.strip()
    cover = dict(_stored(settings, "cover"))
    if not text or text == style_cover_heading(settings.get("journal_style", "ACS")):
        cover.pop("heading", None)
    else:
        cover["heading"] = text
    if cover:
        settings["cover"] = cover
    else:
        settings.pop("cover", None)


# ---------------------------------------------------------------------- table of contents

def toc_settings(settings: dict) -> dict[str, Any]:
    """The table-of-contents settings, with defaults filled in. Projects from before the
    TOC page had only "list_compounds_in_toc"; it decides the depth until the TOC page
    is saved."""
    out = dict(DEFAULT_TOC)
    if settings.get("list_compounds_in_toc"):
        out["depth"] = "compounds"
    stored = settings.get("toc")
    if isinstance(stored, dict):
        out.update({k: v for k, v in stored.items() if k in DEFAULT_TOC})
    if out["depth"] not in TOC_DEPTHS:
        out["depth"] = DEFAULT_TOC["depth"]
    try:
        out["font_size"] = max(0.0, float(out["font_size"] or 0))
    except (TypeError, ValueError):
        out["font_size"] = 0.0
    for key in ("figures", "tables", "schemes", "dots"):
        out[key] = bool(out[key])
    out["title"] = str(out["title"] or "")
    return out


def store_toc(settings: dict, values: dict[str, Any]) -> None:
    settings["toc"] = {k: values.get(k, v) for k, v in DEFAULT_TOC.items()}
    settings.pop("list_compounds_in_toc", None)   # replaced by "depth"


def toc_rank(depth: str) -> int:
    """1 = sections only … 4 = down to compounds."""
    return list(TOC_DEPTHS).index(depth) + 1 if depth in TOC_DEPTHS else 3


# ---------------------------------------------------------------------- General Information

def general_heading(settings: dict) -> str:
    stored = _stored(settings, "general_information").get("heading")
    return DEFAULT_GENERAL_HEADING if stored is None else str(stored)


def general_numbered(settings: dict) -> bool:
    """Whether the General Information heading gets a section number in numbered styles
    (Wiley, RSC). Off by default: it is front matter, not a section, so the first real
    section is "1."."""
    return bool(_stored(settings, "general_information").get("numbered", False))


def store_general_heading(settings: dict, text: str, numbered: bool | None = None) -> None:
    """The printed heading of the General Information page ("" prints no heading), and
    (if given) whether it is numbered like a section. Defaults are not stored."""
    text = text.strip()
    info = dict(_stored(settings, "general_information"))
    if text == DEFAULT_GENERAL_HEADING:
        info.pop("heading", None)
    else:
        info["heading"] = text
    if numbered is not None:
        if numbered:
            info["numbered"] = True
        else:
            info.pop("numbered", None)
    if info:
        settings["general_information"] = info
    else:
        settings.pop("general_information", None)


# ---------------------------------------------------------------------- what to export

def excluded(settings: dict) -> set[str]:
    """Keys left out of the SI: front pages ("cover", "toc", "general" or "front" for
    all three), "spectra", and section IDs (a section's sub-sections go with it)."""
    value = settings.get("export_excluded") or []
    return {str(v) for v in value} if isinstance(value, list) else set()


def store_excluded(settings: dict, keys) -> None:
    keys = sorted({str(k) for k in keys})
    if keys:
        settings["export_excluded"] = keys
    else:
        settings.pop("export_excluded", None)


def front_page_included(key: str, excluded_keys: set[str]) -> bool:
    return key not in excluded_keys and FRONT_ROOT not in excluded_keys
