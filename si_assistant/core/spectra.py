"""The Spectra section (SPEC §13k.12): where every compound's spectra are printed, and how.

One section of type "spectra" per project. It replaces the automatic appendix at the end
of the SI: the spectra are printed where the section sits in the tree. Its section.json
holds the layout defaults:

    {"id", "type": "spectra", "name": "Spectra",
     "defaults": {"per_page": 1, "orientation": "portrait", "header_line": true, ...}}

(see DEFAULTS). Each spectrum can override any default except the links in the PDF
(SECTION_ONLY_KEYS, SPEC §13l.7), which are for the whole SI. The overrides are kept on the
spectrum's own entry in compound.json (the Spectra field, or an Additional files item
placed in the Spectra section):

    {"file": "3a_1H.pdf", "type": "1H",
     "layout": {...overrides for every page of this file...},
     "pages": {"1": {"x": 0.8, "y": 0.2, ...overrides for page 1 only...}}}

"x"/"y" (page overrides only) put the centre of the structure + ID box at that fraction of
the visible spectrum's width/height; without them the box sits in the default corner.
Each page of a multi-page PDF is a spectrum of its own ("1", "2", …; pictures have "1").

Which spectra, in which order: compounds in tree order; within a compound the Spectra
field (¹H, ¹³C, ¹⁹F, other nuclei, 2D NMR, HPLC/SFC, IR, other) and then Additional files
set to "Spectra section" (stored as placement "appendix"). A compound's order can be set
by hand on its spectra page; it is then kept in compound.json as

    "spectra_order": [["spectra", "3a_13C.pdf"], ["spectra", "3a_1H.pdf"], ["files", …]]

(files not in it, e.g. added later, follow in the automatic order). Every one can show
the structure + ID ("struct_box"); the automatic caption (12.9) is for NMR spectra only.

No user-interface code here, so it can be tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fields as F
from . import solvents as solvents_mod
from .fileio import atomic_write_json, read_json
from .markup import parse

SPECTRA_TYPE = "spectra"
DEFAULT_NAME = "Spectra"
PER_PAGE = (1, 2, 4)
ORIENTATIONS = {"portrait": "Portrait", "landscape": "Landscape"}
CORNERS = {"top-left": "Top left", "top-right": "Top right",
           "bottom-left": "Bottom left", "bottom-right": "Bottom right"}

# The defaults reproduce the earlier automatic appendix: one spectrum per portrait page,
# the header line above it, the structure + ID on a white box in the top-left corner.
DEFAULTS: dict[str, Any] = {
    "per_page": 1,                    # 12.1: 1, 2 or 4 spectra on a page
    "orientation": "portrait",        # 12.1
    "header_line": True,              # "2 — ¹H NMR (400 MHz, CDCl₃)" above each spectrum
    "struct_box": True,               # the structure + ID box is printed on the spectrum
    "corner": "top-left",             # where the structure + ID box goes (unless dragged)
    "struct_frame": False,            # 12.2: a frame around the structure + ID
    "struct_frame_color": "#000000",
    "struct_frame_width": 0.5,        # pt
    "struct_bg": "#ffffff",           # 12.3: the background behind them
    "id_size": 0.0,                   # 12.4: pt; 0 = the journal style's text size
    "id_bold": True,
    "struct_scale": 1.0,              # 12.4: 1.0 = up to 1.5 × 1.1 in
    "spectrum_frame": False,          # 12.5: a frame around each spectrum
    "spectrum_frame_color": "#000000",
    "spectrum_frame_width": 0.5,      # pt
    "auto_crop": False,               # 12.8: cut white margins, then fill the slot
    "caption": False,                 # 12.9: "¹H NMR (CDCl₃, 500 MHz)" under the structure
    "caption_size": 9.0,              # its size in pt (SPEC §13q.1)
    # SPEC §13l.7: links in the PDF. For the whole section, never for one spectrum.
    "nmr_links": False,               # "¹H NMR" at the start of a peak report → its spectrum
    "link_style": "plain",            # how the links look (LINK_STYLES)
    "link_back": False,               # a spectrum's header line → the compound's data
    # SPEC §13n.3: the compounds' HRMS report files, printed in a sub-section "HRMS
    # reports" at the end of this section. Each compound can say otherwise (compound.json
    # hrms.print_reports, core/ms_reports.prints_reports).
    "hrms_reports": False,
}
SECTION_ONLY_KEYS = ("nmr_links", "link_style", "link_back", "hrms_reports")
LINK_STYLES = {"plain": "Like normal text", "blue": "Blue",
               "blue_underline": "Blue and underlined"}
POSITION_KEYS = ("x", "y")            # a dragged box (page overrides only)
SCALE_RANGE = (0.25, 4.0)
ID_SIZE_RANGE = (5.0, 24.0)
CAPTION_SIZE_RANGE = (5.0, 18.0)
FRAME_WIDTH_RANGE = (0.1, 5.0)
STRUCTURE_BOX_IN = (1.5, 1.1)         # the structure's largest size at scale 1.0 (inches)

# Settings used before the Spectra section existed (project.json); carried over into
# the section's defaults when it is added.
LEGACY_KEYS = {"spectra_per_page": "per_page", "structure_corner": "corner"}

_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


# ---------------------------------------------------------------------- settings

def _clean_value(key: str, value: Any) -> Any:
    """`value` checked for `key`, or None if it is not usable (it is then ignored)."""
    default = DEFAULTS.get(key)
    try:
        if key in POSITION_KEYS:
            v = float(value)
            return min(1.0, max(0.0, v))
        if key == "per_page":
            v = int(value)
            return v if v in PER_PAGE else None
        if key == "orientation":
            return value if value in ORIENTATIONS else None
        if key == "corner":
            return value if value in CORNERS else None
        if key == "link_style":
            return value if value in LINK_STYLES else None
        if isinstance(default, bool):
            return value if isinstance(value, bool) else None
        if key.endswith("_color") or key == "struct_bg":
            return value.lower() if isinstance(value, str) and _COLOUR.fullmatch(value) else None
        if key == "struct_scale":
            return min(SCALE_RANGE[1], max(SCALE_RANGE[0], float(value)))
        if key == "id_size":
            v = float(value)
            return 0.0 if v <= 0 else min(ID_SIZE_RANGE[1], max(ID_SIZE_RANGE[0], v))
        if key == "caption_size":
            return min(CAPTION_SIZE_RANGE[1], max(CAPTION_SIZE_RANGE[0], float(value)))
        if key.endswith("_width"):
            return min(FRAME_WIDTH_RANGE[1], max(FRAME_WIDTH_RANGE[0], float(value)))
    except (TypeError, ValueError):
        return None
    return None


def clean(values: Any, positions: bool = False, spectrum: bool = False) -> dict[str, Any]:
    """Only the known settings with usable values (anything else is dropped, so a
    hand-edited file can never break compiling). `positions`: also keep "x"/"y".
    `spectrum`: one spectrum's own settings, which never include the section-wide ones
    (SECTION_ONLY_KEYS: the links)."""
    if not isinstance(values, dict):
        return {}
    keys = [k for k in DEFAULTS if not ((spectrum or positions) and k in SECTION_ONLY_KEYS)]
    keys += list(POSITION_KEYS) if positions else []
    out = {}
    for key in keys:
        if key in values:
            v = _clean_value(key, values[key])
            if v is not None:
                out[key] = v
    return out


def legacy_defaults(settings: dict) -> dict[str, Any]:
    """The defaults as the older project settings set them (spectra per page, corner)."""
    return clean({new: settings[old] for old, new in LEGACY_KEYS.items() if old in settings})


def section_defaults(section, settings: dict | None = None) -> dict[str, Any]:
    """The defaults in force: DEFAULTS, then the older project settings (only for a
    project without a Spectra section), then the section's own "defaults"."""
    out = dict(DEFAULTS)
    if section is None:
        out.update(legacy_defaults(settings or {}))
    else:
        out.update(clean(section.data.get("defaults")))
    return out


def page_key(page: int | None) -> str:
    """The key of one page of a spectrum file ("1" for a picture)."""
    return str(page or 1)


def overrides(item: dict, page: str | None = None) -> dict[str, Any]:
    """What a spectrum changes from the defaults: for the whole file, or (with `page`)
    the file's settings plus that page's own ones (and its dragged position)."""
    out = clean(item.get("layout"), spectrum=True)
    if page is not None:
        pages = item.get("pages")
        if isinstance(pages, dict):
            out.update(clean(pages.get(page), positions=True))
    return out


def effective(defaults: dict, item: dict, page: str) -> dict[str, Any]:
    """The settings for one page of one spectrum: defaults + the file's overrides + the
    page's overrides (with "x"/"y" when its box was dragged)."""
    return {**defaults, **overrides(item, page)}


def has_overrides(item: dict) -> bool:
    return bool(item.get("layout")) or bool(item.get("pages"))


# ---------------------------------------------------------------------- which spectra

ORDER_KEY = "spectra_order"   # compound.json: the order set by hand (see the top)


def manual_order(compound) -> list[tuple[str, str]]:
    """The order of a compound's spectrum files set by hand: (field key, file name), or
    [] for the automatic order."""
    raw = getattr(compound, "data", {}).get(ORDER_KEY)
    if not isinstance(raw, list):
        return []
    return [(str(e[0]), str(e[1])) for e in raw
            if isinstance(e, (list, tuple)) and len(e) == 2]


def spectrum_items(project, compound, automatic: bool = False) -> list[tuple[str, dict]]:
    """(field key, item) of everything printed in the Spectra section for a compound, in
    order: the order set by hand if there is one (files not in it follow), or else the
    Spectra field by type, then files placed in the Spectra section. `automatic`: always
    the automatic order."""
    order = {t: i for i, t in enumerate(F.SPECTRUM_TYPES)}
    items = [("spectra", item) for item in (compound.values.get("spectra") or [])
             if "spectra" in compound.field_keys and isinstance(item, dict)]
    items.sort(key=lambda t: order.get(t[1].get("type", ""), len(order)))
    for defn in project.field_defs(compound):
        if defn.kind == "attachments" and defn.key != "spectra":
            items += [(defn.key, i) for i in compound.values.get(defn.key) or []
                      if isinstance(i, dict) and i.get("placement") == "appendix"]
    manual = {} if automatic else {k: n for n, k in enumerate(manual_order(compound))}
    if manual:   # (a stable sort: files not in the order keep theirs, after the rest)
        items.sort(key=lambda t: manual.get((t[0], str(t[1].get("file", ""))), len(manual)))
    return items


def moved(project, compound, index: int, step: int) -> list[tuple[str, str]] | None:
    """The compound's spectrum files in print order, (field key, file name), with the one
    at `index` moved `step` places (-1 up, +1 down); None if it can't move that way."""
    files = [(k, str(i.get("file", ""))) for k, i in spectrum_items(project, compound)]
    new = index + step
    if not (0 <= index < len(files) and 0 <= new < len(files)):
        return None
    files.insert(new, files.pop(index))
    return files


PASTE_GUESS_ORDER = ("1H", "13C", "19F", "31P", "11B")


def guess_pasted_type(compound) -> str:
    """The type a spectrum pasted into this compound probably is (SPEC §13l.1): the first
    nucleus of ¹H, ¹³C, ¹⁹F, ³¹P, ¹¹B that has a peak report with text but no spectrum
    yet; else ¹H when the compound has no spectra at all; else "Other". The user confirms
    it in a chooser, so it only saves a click."""
    have = {str(i.get("type", "")) for i in compound.values.get("spectra") or []
            if isinstance(i, dict)}
    reported = {str(r.get("nucleus", "")) for r in compound.values.get("nmr") or []
                if isinstance(r, dict) and str(r.get("text", "")).strip()}
    for nucleus in PASTE_GUESS_ORDER:
        if nucleus in reported and nucleus not in have:
            return nucleus
    return "1H" if not have else "Other"


def compounds_with_spectra(project) -> list:
    """The compounds that have something in the Spectra section, in tree order: each gets
    a page of its own under the Spectra section in the tree (SPEC §13k.12)."""
    return [c for c in project.all_compounds() if spectrum_items(project, c)]


def pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


@dataclass
class Spectrum:
    """One spectrum: a picture, or one page of a PDF (`page` from 1; None for a picture)."""
    compound: Any
    field_key: str
    item: dict
    path: Path
    page: int | None
    page_no: int          # 1, 2, … within its file (for "(continued)")
    problem: str = ""     # the file can't be read (kept only when asked for; see spectra_of)

    @property
    def key(self) -> str:
        return page_key(self.page)


def unreadable_message(name: str) -> str:
    """What is said when a spectrum PDF (or SVG) can't be opened (in plain words)."""
    if name.lower().endswith(".svg"):
        return (f"{name} could not be read as an SVG drawing (it may be damaged). Paste or "
                "export it again and replace it on the compound page.")
    return (f"{name} could not be opened as a PDF (it may be damaged or not really a PDF). "
            "Export it again and replace it on the compound page.")


_svg_checked: dict[tuple, bool] = {}


def svg_readable(path: Path) -> bool:
    """Whether an SVG file is a whole, readable SVG drawing (a damaged one would stop
    Typst from making the PDF at all). Remembered until the file changes."""
    import xml.etree.ElementTree as ET
    try:
        st = Path(path).stat()
    except OSError:
        return False
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _svg_checked:
        try:
            root = ET.parse(path).getroot()
            ok = root.tag.rsplit("}", 1)[-1] == "svg"
        except (ET.ParseError, OSError, ValueError):
            ok = False
        if len(_svg_checked) > 500:
            _svg_checked.clear()
        _svg_checked[key] = ok
    return _svg_checked[key]


def spectra_of(project, compound, page_count=pdf_page_count, problems: list | None = None,
               keep_unreadable: bool = False) -> list[Spectrum]:
    """Every spectrum page of a compound, in print order. A PDF that can't be read is
    skipped (with a note in `problems`), or with `keep_unreadable` kept as one entry
    whose `problem` says so; a missing file is kept (the caller reports it)."""
    out = []
    for key, item in spectrum_items(project, compound):
        path = project.attachment_path(compound, key, item.get("file", ""))
        pages: list[int | None] = [None]
        if path.suffix.lower() == ".svg" and path.exists() and not svg_readable(path):
            message = unreadable_message(path.name)
            if problems is not None:
                problems.append(message)
            if keep_unreadable:
                out.append(Spectrum(compound, key, item, path, None, 1, message))
            continue
        if path.suffix.lower() == ".pdf" and path.exists():
            try:
                pages = list(range(1, page_count(path) + 1))
            except Exception:  # noqa: BLE001 — report and keep going
                message = unreadable_message(path.name)
                if problems is not None:
                    problems.append(message)
                if keep_unreadable:
                    out.append(Spectrum(compound, key, item, path, None, 1, message))
                continue
        for n, page in enumerate(pages, start=1):
            out.append(Spectrum(compound, key, item, path, page, n))
    return out


# ---------------------------------------------------------------------- captions

# Each NMR solvent's usual name, with the ways a report may write it: one table for the
# whole program (core/solvents.py, SPEC §13l.14).
SOLVENT_NAMES = solvents_mod.SOLVENT_NAMES
SOLVENTS = solvents_mod.SOLVENTS
OTHER_NMR = "Other NMR"


def is_nucleus(text: str) -> bool:
    """Whether `text` names an NMR nucleus ("1H", "29Si", …)."""
    return text in F.NUCLEI or bool(re.fullmatch(r"\d+[A-Z][a-z]?", text))


def nmr_nucleus(item: dict, compound=None) -> str | None:
    """The nucleus of an NMR spectrum ("1H"), or None for anything else (HPLC, IR, …,
    2D NMR). For "Other NMR" it is the nucleus in the file name (e.g. "3a_29Si.pdf"), or
    else the compound's only NMR report for a nucleus that has no type of its own in the
    Spectra field (e.g. its ²⁹Si report); None if that can't be told."""
    kind = str(item.get("type", "") or "")
    if is_nucleus(kind):
        return kind
    if kind != OTHER_NMR:
        return None
    own_type = set(F.SPECTRUM_TYPES)
    others = [n for n in F.NUCLEI if n not in own_type]
    stem = Path(str(item.get("file", ""))).stem
    for n in sorted(others, key=len, reverse=True):
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(n)}(?![a-z])", stem):
            return n
    if compound is not None:
        found = {str(r.get("nucleus", "")) for r in compound.values.get("nmr") or []
                 if isinstance(r, dict)}
        found = sorted(n for n in found if n and n not in own_type and is_nucleus(n))
        if len(found) == 1:
            return found[0]
    return None


def find_solvent(text: str) -> str:
    """The NMR solvent a report names ("CDCl3"), in its usual form, whichever way it is
    written (CDCl₃, chloroform-d, (CD3)2SO, DMSO-d6, MeOD…); "" if none is found. When
    several are named, the first one in the text."""
    found = solvents_mod.find(text)
    return found.name if found is not None else ""


def written_solvent(text: str) -> str:
    """The NMR solvent as the report writes it ("MeOD", "CDCl3", "methanol-d4"; subscript
    digits made plain, so the printed text subscripts them like any formula); "" if none.
    The first one named wins."""
    found = solvents_mod.find(text)
    return found.written if found is not None else ""


def nmr_conditions(compound, nucleus: str) -> tuple[str, str]:
    """(frequency "400 MHz", solvent as written in the report, e.g. "CDCl3" or "MeOD")
    from the compound's NMR report for this nucleus, as far as it says."""
    for report in compound.values.get("nmr") or []:
        if report.get("nucleus") != nucleus:
            continue
        text = " ".join(p.plain_text for p in parse(str(report.get("text", ""))))
        freq = re.search(r"(\d{2,4}(?:\.\d+)?)\s*MHz", text)
        return (f"{freq.group(1)} MHz" if freq else "", written_solvent(text))
    return "", ""


CAPTION_KEY = "struct_caption"   # a caption typed for one spectrum (the Spectra field)
HEADER_KEY = "title"             # a header typed for one spectrum ("Header in the SI")
TEXT_KEYS = (HEADER_KEY, CAPTION_KEY)


def header_parts(compound, item: dict, display_id: str, page_no: int) -> list[str]:
    """The line above a spectrum, in pieces: "2 — 1H NMR", " (400 MHz, CDCl3)", or the
    header typed for it; later pages of a PDF add " (continued)"."""
    custom = str(item.get(HEADER_KEY, "")).strip()
    if custom:
        parts = [custom]
    else:
        kind = str(item.get("type", "") or item.get("caption", "") or item.get("file", ""))
        nucleus = nmr_nucleus(item, compound) or (kind if is_nucleus(kind) else None)
        if nucleus:
            parts = [f"{display_id} — {nucleus} NMR"]
            freq, solvent = nmr_conditions(compound, nucleus)
            conditions = ", ".join(x for x in (freq, solvent) if x)
            if conditions:
                parts.append(f" ({conditions})")
        else:
            parts = [f"{display_id} — {kind}"]
    if page_no > 1:
        parts.append(" (continued)")
    return parts


def spectrum_name(item: dict, compound=None) -> str:
    """A short name for a spectrum in lists: "1H NMR", "HPLC/SFC", "29Si NMR", or the file's
    caption (Additional files), or else its file name (as the printed header)."""
    nucleus = nmr_nucleus(item, compound)
    if nucleus:
        return f"{nucleus} NMR"
    name = str(item.get("type", "") or item.get("caption", "") or "").strip()
    return name or Path(str(item.get("file", "") or "")).name or "File"


def automatic_caption(compound, item: dict) -> str:
    """The automatic caption (12.9), e.g. "1H NMR (CDCl3, 500 MHz)": nucleus, then solvent,
    then frequency, from the matching peak report. "" when not NMR."""
    nucleus = nmr_nucleus(item, compound)
    if nucleus is None:
        return ""
    freq, solvent = nmr_conditions(compound, nucleus)
    inside = ", ".join(x for x in (solvent, freq) if x)
    return f"{nucleus} NMR" + (f" ({inside})" if inside else "")


def caption_text(compound, item: dict) -> str:
    """The caption under the structure (12.9): the one typed for this spectrum (any type
    of spectrum), or else the automatic one (NMR spectra only; "" otherwise)."""
    typed = str(item.get(CAPTION_KEY, "") or "").strip()
    return typed or automatic_caption(compound, item)


# ---------------------------------------------------------------------- resetting

def apply_defaults_to_all(project) -> int:
    """Remove every spectrum's own settings and dragged positions, so all of them follow
    the section's defaults ("Apply defaults to all spectra"). Returns how many spectrum
    files were changed."""
    changed = 0
    for compound in project.all_compounds():
        touched = False
        for _key, item in spectrum_items(project, compound):
            if "layout" in item or "pages" in item:
                item.pop("layout", None)
                item.pop("pages", None)
                changed += 1
                touched = True
        if touched:
            project.save_compound(compound)
    return changed


def count_overridden(project) -> int:
    """How many spectrum files have settings of their own."""
    return sum(1 for c in project.all_compounds() for _k, item in spectrum_items(project, c)
               if has_overrides(item))


# ---------------------------------------------------------------------- printed size
# Used by a compound's spectra page to draw a spectrum and its structure + ID box in the
# same proportions as the printed page (styles/*.typ si-spectra-page and si-spectrum).

# (paper width, height), (side margin, top/bottom margin) in inches, for each style.
_A4 = ((8.27, 11.69), (2.2 / 2.54, 2.5 / 2.54))
PAPER_IN = {"ACS": ((8.5, 11.0), (1.0, 1.0)), "Wiley": _A4, "RSC": _A4}
GUTTERS_IN = (0.6 / 2.54, 0.8 / 2.54)   # between slots: across, down
HEADER_IN = 0.24                        # the header line above a spectrum, with its gap
CORNER_INSET_IN = 0.4 / 2.54            # the box's distance from the spectrum's corner
STYLE_TEXT_PT = {"ACS": 11.0, "Wiley": 11.0, "RSC": 10.5}   # the ID's size when "auto"
CAPTION_PT = DEFAULTS["caption_size"]    # the caption under the structure (its default)
BOX_INSET_PT = 3.0                      # the box's padding


def grid(per_page: int, landscape: bool) -> tuple[int, int]:
    """(columns, rows) of the slots on a spectra page."""
    if per_page == 4:
        return 2, 2
    if per_page == 2:
        return (2, 1) if landscape else (1, 2)
    return 1, 1


def slot_size_in(style: str, per_page: int, orientation: str) -> tuple[float, float]:
    """The size of one spectrum's slot (inches) on a page of the journal style."""
    (pw, ph), (mx, my) = PAPER_IN.get(style, PAPER_IN["ACS"])
    landscape = orientation == "landscape"
    if landscape:
        pw, ph = ph, pw
    cols, rows = grid(per_page, landscape)
    bw, bh = pw - 2 * mx, ph - 2 * my
    return ((bw - GUTTERS_IN[0] * (cols - 1)) / cols, (bh - GUTTERS_IN[1] * (rows - 1)) / rows)


def shown_size_in(slot: tuple[float, float], aspect: float | None,
                  crop: tuple[float, float, float, float] | None = None,
                  header: bool = False) -> tuple[float, float]:
    """The printed size (inches) of the part of a spectrum that is shown: as large as fits
    in its slot (below the header line), keeping its shape; `aspect` = the whole page's
    height / width, `crop` = the part shown."""
    w, h = slot
    if header:
        h = max(0.1, h - HEADER_IN)
    if not aspect:
        return w, h
    l, t, r, b = crop or (0.0, 0.0, 1.0, 1.0)
    shape = aspect * (b - t) / (r - l)
    width = min(w, h / shape)
    return width, width * shape


def structure_size_in(natural: tuple[float, float] | None, scale: float) -> tuple[float, float]:
    """The printed size of the structure on a spectrum: its natural size but no bigger
    than STRUCTURE_BOX_IN, then times `scale` (as compile/document.Builder.image)."""
    box_w, box_h = STRUCTURE_BOX_IN
    if not natural or natural[0] <= 0 or natural[1] <= 0:
        return box_w * scale, box_h * scale
    w, h = natural
    fit = min(1.0, box_w / w, box_h / h) * scale
    return w * fit, h * fit


# ---------------------------------------------------------------------- auto-crop cache

CROP_CACHE_FILE = "crop_cache.json"
CROP_VERSION = 1   # bump when the detection changes, so old results are not reused


class CropCache:
    """Where the content of each spectrum page is (12.8), as fractions (left, top, right,
    bottom) of the page, remembered in build/crop_cache.json (generated output, safe to
    delete). Keyed by the file's path in the project, size, modification time and page,
    so a replaced file is measured again."""

    def __init__(self, project, detect=None):
        self.project = project
        self.detect = detect   # (path, page or None) -> (l, t, r, b) | None, needs Qt
        self.path = Path(project.folder) / "build" / CROP_CACHE_FILE
        self._data: dict[str, Any] | None = None
        self._dirty = False

    def _key(self, path: Path, page: int | None) -> str | None:
        try:
            st = path.stat()
            rel = path.resolve().relative_to(Path(self.project.folder).resolve()).as_posix()
        except (OSError, ValueError):
            return None
        return f"v{CROP_VERSION}|{rel}|{st.st_size}|{st.st_mtime_ns}|{page_key(page)}"

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                data = read_json(self.path, {})
            except (OSError, ValueError):
                data = {}
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def box(self, path: Path, page: int | None, measure: bool = True
            ) -> tuple[float, float, float, float] | None:
        """The content box of one spectrum page, measured once (None: nothing to cut, or
        it can't be measured here). `measure=False`: only what was measured before."""
        key = self._key(path, page)
        data = self._load()
        if key is not None and key in data:
            return _valid_box(data[key])
        if self.detect is None or not measure:
            return None
        try:
            found = _valid_box(self.detect(path, page))
        except Exception:  # noqa: BLE001 — an unreadable file is simply not cropped
            found = None
        if key is not None:
            data[key] = list(found) if found else None
            self._dirty = True
        return found

    def _stale(self, key: str) -> bool:
        """Whether a remembered result is for a file that is gone or has changed since (or
        from an older way of measuring)."""
        version, _, rest = key.partition("|")
        parts = [version] + rest.rsplit("|", 3)   # (a file name may hold "|" on Linux)
        if len(parts) != 5 or version != f"v{CROP_VERSION}":
            return True
        try:
            st = (Path(self.project.folder) / parts[1]).stat()
        except OSError:
            return True
        return f"{st.st_size}|{st.st_mtime_ns}" != f"{parts[2]}|{parts[3]}"

    def save(self) -> None:
        """Write the remembered boxes (quietly skipped if build/ can't be written), leaving
        out those of files that are gone or have changed."""
        if not self._dirty or self._data is None or getattr(self.project, "read_only", False):
            return
        for key in [k for k in self._data if self._stale(k)]:
            del self._data[key]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, self._data)
            self._dirty = False
        except OSError:
            pass


def _valid_box(value) -> tuple[float, float, float, float] | None:
    try:
        l, t, r, b = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
        return None
    return (l, t, r, b)


def content_box_of(gray, white: int = 245, margin_fraction: float = 0.01,
                   min_margin_px: int = 4) -> tuple[float, float, float, float] | None:
    """The box around everything darker than `white` in a greyscale picture (a 2-D
    numpy array, 0 = black), with a small margin, as fractions of the picture. None if the
    picture is blank or nothing would be cut."""
    import numpy as np
    h, w = gray.shape
    if not h or not w:
        return None
    ink = gray < white
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if not len(rows) or not len(cols):
        return None
    mx = max(min_margin_px, round(w * margin_fraction))
    my = max(min_margin_px, round(h * margin_fraction))
    left = max(0, cols[0] - mx)
    right = min(w, cols[-1] + 1 + mx)
    top = max(0, rows[0] - my)
    bottom = min(h, rows[-1] + 1 + my)
    box = (left / w, top / h, right / w, bottom / h)
    if box == (0.0, 0.0, 1.0, 1.0):
        return None
    return tuple(round(v, 4) for v in box)
