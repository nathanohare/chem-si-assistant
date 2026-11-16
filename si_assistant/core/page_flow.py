"""How text flows from page to page (Project settings ▸ Page ▸ Page flow, and the Compile
window; SPEC §13k.2b).

Stored in project.json as

    "page_flow": {"heading_lines": 2, "widows": "normal", "emphasis_headings": true,
                  "keep_compounds": false, "data_spacing": "paragraph"}

- heading_lines: a heading moves to the next page unless this many lines of what follows
  it fit under it on the same page (0 = off: headings only stay with the first line).
- widows: how hard the typesetter avoids leaving the first or last line of a paragraph
  alone at the bottom or top of a page: "off", "normal" or "strong".
- emphasis_headings: a short line set wholly in bold or italic (a heading typed as plain
  text, e.g. "Solvent Effect on Reaction Yield") is kept with what follows like a heading.
- keep_compounds: each compound entry (title, structure, procedure and data) moves whole
  to the next page rather than being split, when it fits on one page.
- data_spacing: between a compound's procedure and its characterization data:
  "paragraph" (a new paragraph, with the usual paragraph space), "line" (a new line with
  no extra space) or "run_on" (the data follows in the same paragraph).

Each section and compound can also set its own (in its section.json / compound.json):

    "page_flow": {"new_page": false, "keep": "default"}

- new_page: it starts on a new page.
- keep: "default" (the project setting: compounds follow keep_compounds, sections may
  split), "together" (kept on one page when it fits) or "split" (may run over a page
  break even when the project keeps compounds together).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KEY = "page_flow"
MAX_HEADING_LINES = 6
WIDOWS = {"off": "Off", "normal": "Normal", "strong": "Strong"}
# Typst's text.costs for widows and orphans: 0% lets them happen, 100% is its default.
WIDOW_COSTS = {"off": "0%", "normal": "100%", "strong": "600%"}
DATA_SPACINGS = {"paragraph": "New paragraph (with paragraph space)",
                 "line": "New line, no extra space",
                 "run_on": "Same paragraph (run on)"}
KEEPS = {"default": "Use the project setting", "together": "Keep on one page",
         "split": "Allow splitting over pages"}
DEFAULTS: dict[str, Any] = {"heading_lines": 2, "widows": "normal",
                            "emphasis_headings": True, "keep_compounds": False,
                            "data_spacing": "paragraph"}
# A line in bold or italic with at most this many characters can be a heading typed as
# text (a full line of body text at 11 pt is about 95 characters).
EMPHASIS_MAX_CHARS = 90


@dataclass(frozen=True)
class Flow:
    heading_lines: int = 2
    widows: str = "normal"
    emphasis_headings: bool = True
    keep_compounds: bool = False
    data_spacing: str = "paragraph"

    @property
    def emphasis_lines(self) -> int:
        """Lines kept after a bold/italic line that acts as a heading (0 = not kept)."""
        return max(1, self.heading_lines) if self.emphasis_headings else 0


def flow_settings(settings: dict) -> Flow:
    """The page flow settings in effect (defaults for anything missing or unreadable)."""
    raw = settings.get(KEY) if isinstance(settings.get(KEY), dict) else {}
    try:
        lines = int(raw.get("heading_lines", DEFAULTS["heading_lines"]))
    except (TypeError, ValueError, OverflowError):
        lines = DEFAULTS["heading_lines"]
    widows = raw.get("widows", DEFAULTS["widows"])
    spacing = raw.get("data_spacing", DEFAULTS["data_spacing"])
    return Flow(heading_lines=min(MAX_HEADING_LINES, max(0, lines)),
                widows=widows if widows in WIDOWS else DEFAULTS["widows"],
                emphasis_headings=bool(raw.get("emphasis_headings",
                                               DEFAULTS["emphasis_headings"])),
                keep_compounds=bool(raw.get("keep_compounds", DEFAULTS["keep_compounds"])),
                data_spacing=spacing if spacing in DATA_SPACINGS else DEFAULTS["data_spacing"])


def store_flow_settings(settings: dict, flow: Flow) -> None:
    settings[KEY] = {"heading_lines": flow.heading_lines, "widows": flow.widows,
                     "emphasis_headings": flow.emphasis_headings,
                     "keep_compounds": flow.keep_compounds,
                     "data_spacing": flow.data_spacing}


# ---------------------------------------------------------------------- one item

def item_flow(data: dict) -> dict[str, Any]:
    """A section's or compound's own choices: {"new_page": bool, "keep": str}."""
    raw = data.get(KEY) if isinstance(data.get(KEY), dict) else {}
    keep = raw.get("keep", "default")
    return {"new_page": bool(raw.get("new_page", False)),
            "keep": keep if keep in KEEPS else "default"}


def set_item_flow(data: dict, new_page: bool, keep: str) -> None:
    """Store an item's choices; the defaults are not stored (the file stays as it was)."""
    if not new_page and keep == "default":
        data.pop(KEY, None)
    else:
        data[KEY] = {"new_page": bool(new_page), "keep": keep if keep in KEEPS else "default"}


def keeps_together(data: dict, project_default: bool) -> bool:
    keep = item_flow(data)["keep"]
    return project_default if keep == "default" else keep == "together"


def describe_item(data: dict) -> str:
    """Short text for the page-flow button ("" = nothing set)."""
    f = item_flow(data)
    parts = []
    if f["new_page"]:
        parts.append("new page")
    if f["keep"] == "together":
        parts.append("kept together")
    elif f["keep"] == "split":
        parts.append("may split")
    return ", ".join(parts)


# ---------------------------------------------------------------------- Typst

# Space a heading needs below the page's last line before it is moved on: its own height,
# `lines` lines of text, and the spacing around them (heading above/below, paragraphs).
NEED_SLACK = "2.4em"


def typst_definitions(flow: Flow) -> str:
    """Typst code set after the journal style: `si-need(lines, before)` (move `before` to
    the next page unless it and `lines` lines fit; nothing else changes), `si-together`
    (keep content on one page when it fits), the heading rule and widow/orphan costs."""
    out = ["#let si-need(lines, before) = context {\n"
           "  let pitch = measure([X #linebreak() X]).height - measure([X]).height\n"
           f"  let h = lines * pitch + measure(before).height + {NEED_SLACK}\n"
           "  block(height: h, breakable: false, above: 0pt, below: 0pt, width: 100%)\n"
           "  v(-h)\n}",
           "#let si-together(body) = layout(size => {\n"
           "  let h = measure(block(width: size.width, body)).height\n"
           "  block(width: 100%, breakable: h > size.height, body)\n})"]
    if flow.heading_lines > 0:
        out.append(f"#show heading: it => {{ si-need({flow.heading_lines}, it); it }}")
    cost = WIDOW_COSTS[flow.widows]
    out.append(f"#set text(costs: (widow: {cost}, orphan: {cost}))")
    return "\n".join(out)
