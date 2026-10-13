"""An X-ray structure section of a project (SPEC §13v): its CIF, how it is drawn, its
captions and its crystal data table, for its page (ui/xray_panel.py) and the compiler.

section.json keys (see Project.add_section): "file" (the CIF, in the section's folder),
"block" (the CIF data block shown; "" = the first with atoms), "view" (ViewSettings JSON;
{} = not set yet: the best view, about 12 cm wide), "compound" (the compound it shows:
its ID in the captions), "anchored" (shown and printed under that compound), "caption" /
"table_caption" ("" = automatic), "table" (print the crystal data table), "table_values"
({row key: value typed over}), "table_hidden" (row keys left out).

No Qt: unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import cif, crystal
from . import xray_draw as X
from .project import Project, Section


@dataclass
class Loaded:
    block: cif.CifBlock | None = None
    structure: crystal.Structure | None = None
    grown: crystal.Grown | None = None
    error: str = ""                    # why nothing can be drawn, in plain words
    blocks: list[str] | None = None    # the file's data blocks with atoms (names)


_CACHE: dict = {}


def load(project: Project, section: Section) -> Loaded:
    """The section's structure, read from its CIF (kept while the file is unchanged)."""
    path = project.section_file_path(section)
    if path is None:
        return Loaded(error="No CIF file has been chosen yet.")
    try:
        stat = path.stat()
    except OSError:
        return Loaded(error=f"The CIF file {path.name} is missing from the section's folder.")
    wanted = str(section.data.get("block") or "")
    key = (str(path), stat.st_size, stat.st_mtime_ns, wanted)
    if key in _CACHE:
        return _CACHE[key]
    try:
        blocks = [b for b in cif.read_cif_file(path) if b.rows("_atom_site_label")]
    except OSError as e:
        return Loaded(error=f"{path.name} could not be read: {e}")
    if not blocks:
        out = Loaded(error=f"{path.name} holds no structure (no _atom_site_ table).")
    else:
        block = next((b for b in blocks if b.name == wanted), blocks[0])
        try:
            s = crystal.structure_from_cif(block)
            out = Loaded(block, s, crystal.grow(s), blocks=[b.name for b in blocks])
        except crystal.CrystalError as e:
            out = Loaded(block, error=str(e), blocks=[b.name for b in blocks])
    if len(_CACHE) > 20:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def view_of(section: Section, grown: crystal.Grown | None) -> X.ViewSettings:
    """How the section's structure is drawn: as stored, or (nothing stored yet) the best
    view, about 12 cm wide."""
    stored = section.data.get("view")
    if isinstance(stored, dict) and stored.get("rotation") is not None:
        return X.ViewSettings.from_json(stored)
    view = X.ViewSettings.from_json(stored if isinstance(stored, dict) else {})
    if grown is not None and grown.atoms:
        view.rotation = X.best_rotation(np.array([a.xyz for a in grown.atoms])).tolist()
        view.zoom = X.fitting_zoom(grown, view)
    return view


def compound_ref(project: Project, section: Section) -> str:
    """The compound the structure shows, as a mention (printed as its current ID), or ""."""
    cid = section.data.get("compound")
    if not isinstance(cid, str) or not cid:
        return ""
    try:
        project.compound(cid)
    except KeyError:
        return ""
    return "@{" + cid + "}"


def auto_caption(project: Project, section: Section, view: X.ViewSettings,
                 block: cif.CifBlock | None, grown: crystal.Grown | None = None) -> str:
    """"Molecular structure of **3a** (thermal ellipsoids at 50% probability; hydrogen
    atoms omitted for clarity). CCDC 2345678." — from how it is drawn."""
    ref = compound_ref(project, section)
    what = f"Molecular structure of {ref}" if ref else "Molecular structure"
    notes = []
    if view.style == "ortep":
        notes.append(f"thermal ellipsoids at {view.probability}% probability")
    else:
        notes.append("ball-and-stick model")
    if view.hydrogens == "hide":
        notes.append("hydrogen atoms omitted for clarity")
    if view.hidden_molecules:
        notes.append("solvent molecules omitted for clarity")
    if view.hidden_parts:
        notes.append("the minor disorder component omitted for clarity")
    text = f"{what} ({'; '.join(notes)})."
    ccdc = _ccdc(section, block)
    if ccdc:
        text += f" CCDC {ccdc}."
    return text


def _ccdc(section: Section, block: cif.CifBlock | None) -> str:
    """The CCDC number: the CIF's, or the one typed into the table."""
    return next((v for k, _l, v, _p in table_rows(section, block, all_rows=True)
                 if k == "ccdc"), "")


def caption(project: Project, section: Section, view: X.ViewSettings,
            block: cif.CifBlock | None) -> str:
    typed = str(section.data.get("caption") or "").strip()
    return typed or auto_caption(project, section, view, block)


def auto_table_caption(project: Project, section: Section) -> str:
    ref = compound_ref(project, section)
    return f"Crystal data and structure refinement for {ref}." if ref else \
        "Crystal data and structure refinement."


def table_caption(project: Project, section: Section) -> str:
    typed = str(section.data.get("table_caption") or "").strip()
    return typed or auto_table_caption(project, section)


def table_rows(section: Section, block: cif.CifBlock | None,
               all_rows: bool = False) -> list[tuple[str, str, str, bool]]:
    """[(key, label, value, printed)] of the crystal data table: the CIF's values, with
    those typed over; `printed` False for a row left out or with no value (all_rows:
    those too, e.g. a CCDC number the CIF does not have yet)."""
    if block is None:
        return []
    values = typed_values(section)
    hidden = section.data.get("table_hidden")
    hidden = set(hidden) if isinstance(hidden, list) else set()
    out = []
    for key, label, value in crystal.crystal_table(block, keep_empty=True):
        value = values.get(key) or value
        printed = key not in hidden and bool(value.strip())
        if printed or all_rows:
            out.append((key, label, value, printed))
    return out


def typed_values(section: Section) -> dict[str, str]:
    """{row key: value} typed over the CIF's (or where it has none)."""
    values = section.data.get("table_values")
    return {str(k): v for k, v in values.items() if isinstance(v, str) and v.strip()} \
        if isinstance(values, dict) else {}


# The table's values on the screen: one line of plain text, sub- and superscript digits
# as their Unicode characters ("P2₁/c", "Å³"); typed back the same way.
_SUB = str.maketrans("0123456789+−-()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₍₎")
_SUP = str.maketrans("0123456789+−-()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁽⁾")
_FROM_SUB = {v: k for k, v in zip("0123456789+−()", "₀₁₂₃₄₅₆₇₈₉₊₋₍₎")}
_FROM_SUP = {v: k for k, v in zip("0123456789+−()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾")}


def plain(text: str) -> str:
    """Stored text (core/markup.py) as one plain line (italics are lost)."""
    from .markup import SUB, SUPER, parse
    out = []
    for p in parse(text or ""):
        for r in p.runs:
            out.append(r.text.translate(_SUB) if r.valign == SUB else
                       r.text.translate(_SUP) if r.valign == SUPER else r.text)
    return "".join(out)


def from_plain(text: str) -> str:
    """A value typed in the table, stored: Unicode sub- and superscript characters become
    sub- and superscripts ("C₁₀H₈" → C_{10}H_{8})."""
    from .markup import NORMAL, SUB, SUPER, Paragraph, Run, serialize
    runs: list[Run] = []
    for ch in " ".join(text.split()):
        valign, ch = ((SUB, _FROM_SUB[ch]) if ch in _FROM_SUB else
                      (SUPER, _FROM_SUP[ch]) if ch in _FROM_SUP else (NORMAL, ch))
        if runs and runs[-1].valign == valign:
            runs[-1] = Run(runs[-1].text + ch, valign=valign)
        else:
            runs.append(Run(ch, valign=valign))
    return serialize([Paragraph(runs)]) if runs else ""


def table_json(section: Section, block: cif.CifBlock | None) -> dict:
    """The printed crystal data table (core/tables.py JSON): label | value, no header."""
    rows = [[{"text": label, "align": "left"}, {"text": value, "align": "left"}]
            for _k, label, value, printed in table_rows(section, block) if printed]
    return {"header_rows": 0, "columns": ["left", "left"], "rows": rows, "footnotes": []}


def svg(grown: crystal.Grown, view: X.ViewSettings) -> str:
    return X.to_svg(X.shapes(grown, view))
