"""How headings are numbered in the SI (Project settings ▸ Output ▸ Heading numbers).

  "style"    the journal style's own: ACS none; Wiley and RSC 1, 1.1, 1.1.1 for sections
             (headings typed in a text are never numbered);
  "outline"  an outline: sections 1, 2, 3…; one level down A, B, C…; then a, b, c…; then
             i, ii, iii…;
  "decimal"  1, 1.1, 1.1.1 in any style;
  "none"     no numbers in any style.

With "outline" and "decimal" the headings typed in a section's text (and in General
Information) are part of the outline, numbered one level below their section, and the
General Information heading is numbered like a section. General procedure headings,
compound titles and headings typed inside a compound's text are never numbered. Stored
as `"heading_numbers": "outline"` in project.json, only when not "style".
"""

from __future__ import annotations

HEADING_NUMBERS = {
    "style": "As the journal style does",
    "outline": "Outline (1, A, a, i)",
    "decimal": "Numbers (1, 1.1, 1.1.1)",
    "none": "None",
}

# Typst numbering for each choice; "style" sets nothing (the style's own `set heading`).
# si-outline-numbering is defined in every style (styles/*.typ).
_TYPST = {"outline": "si-outline-numbering", "decimal": '"1.1."', "none": "none"}


def heading_numbers(settings: dict) -> str:
    """The chosen numbering (a key of HEADING_NUMBERS); anything else = "style"."""
    value = settings.get("heading_numbers", "style")
    return value if value in HEADING_NUMBERS else "style"


def store_heading_numbers(settings: dict, value: str) -> None:
    if value in HEADING_NUMBERS and value != "style":
        settings["heading_numbers"] = value
    else:
        settings.pop("heading_numbers", None)


def numbers_text_headings(settings: dict) -> bool:
    """Whether headings typed in a section's text (and the General Information heading)
    are numbered: in the "outline" and "decimal" choices."""
    return heading_numbers(settings) in ("outline", "decimal")


def typst_set_rule(settings: dict) -> str:
    """The Typst rule that sets the numbering, placed after the style's own; "" for
    "style"."""
    numbering = _TYPST.get(heading_numbers(settings))
    return f"#set heading(numbering: {numbering})" if numbering else ""
