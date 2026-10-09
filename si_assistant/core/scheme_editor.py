"""The scheme editor (SPEC §13n.4): a scheme page drawn as starting materials, then steps
(an arrow with conditions above and below it, then the products). It is the only way a
scheme page is made: one pasted picture of the whole scheme was removed (SPEC §13p.5).

Everything is kept in the scheme page's scheme.json, readable, under "editor"; the
pictures and structures of the boxes are files in the scheme's folder, under editor/:

    {"mode": "editor",                    (scheme pages made before the editor have no
                                          "mode", or "picture": see upgrade_old_scheme)
     "editor": {
       "starting": [box, …],              starting materials, left to right
       "steps": [{"id": "st-…",
                  "top": "markup",        conditions above the arrow (core/markup.py; may
                                          hold small structures: @struct{s-…})
                  "bottom": "markup",     conditions under the arrow
                  "top_align": "left",    optional: this box's own alignment
                  "bottom_align": "",
                  "products": [box, …]}, …],
       "inline": {"s-…": {"picture": "editor/s-….svg", "structure": "editor/s-….mol",
                          "image_bond_pt": 7.2}},   structures in the conditions
       "layout": {…},                     this scheme's own layout settings (only those
                                          that differ from Project settings ▸ Schemes)
       "locked": true}}                   "Save scheme" locked the editor (absent: not
                                          locked; an old picture scheme's "saved" is
                                          not used)

    box = {"id": "b-…",
           "compound": "c-…",             a product marked "Compound": its sub-compound
                                          page (ID, data page). Its structure and picture
                                          are the compound's own (one source of truth);
                                          the box's own files are not used meanwhile.
           "structure": "editor/b-….mol", the box's own structure (unlinked boxes)
           "picture": "editor/b-….svg",   … and picture (ChemDraw's, its original .emf
           "picture_source": "chemdraw",  next to it), with its measured bond length
           "image_bond_pt": 7.2,          (and the user's own, "image_bond_override",
                                          as on compounds, SPEC §13n.2a)
           "label": "S1" or "@{c-…}",     an ID label typed, or a mention of a compound
                                          elsewhere (boxes that are not compounds: no
                                          data page)
           "label_dx": 0, "label_dy": 0,  where the ID was dragged, in pt from its place
                                          centred under the structure
           "yield": "92%"}                typed yield (a compound's is its Yield field's
                                          unless typed here)

Nothing here needs Qt. The sub-compounds of an editor scheme are ordered as their boxes,
left to right and step by step, so their IDs follow the scheme.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import chem
from . import drawing_scale
from . import fileio
from .fileio import atomic_write_text, read_text
from .ids import new_id

MODE_KEY = "mode"
EDITOR_KEY = "editor"
EDITOR = "editor"
FILES_DIR = "editor"                     # the boxes' files, in the scheme's folder
START = "start"                          # where a box is: START or a step's ID

ALIGNS = ("left", "center", "right")
FITS = ("wrap", "shrink")
YIELD_PLACES = ("product", "conditions", "none")

# Project settings ▸ Schemes, and each scheme's Layout… (only differences are stored).
DEFAULT_LAYOUT: dict[str, Any] = {
    "cond_size_pt": 9.0,          # conditions text size
    "cond_structure_pct": 60.0,   # structures in the conditions, % of their scheme size
    "top_align": "center",        # conditions above the arrow
    "bottom_align": "center",     # … and below it
    "arrow_before_mm": 2.0,       # arrow beyond the conditions text: before it
    "arrow_after_mm": 3.0,        # … and after it (the head)
    "arrow_min_mm": 12.0,         # shortest arrow
    "fit": "wrap",                # too wide: new row at an arrow, or "shrink" to one line
    "scale_pct": 100.0,           # the whole scheme
    "yields": "product",          # under each product, under its step's conditions, none
    "gap_mm": 2.0,                # between structures, plus signs and arrows
    "align_ids": True,            # the IDs of a row on one line, under its lowest structure
    "show_label": False,          # print "Scheme S1. Name" under it (numbered, listed);
                                  # off by default: a data section's scheme stands alone
}
MIN_TEXT_PT = 6.0                 # conditions printed smaller than this: a compile warning
LIMITS: dict[str, tuple[float, float]] = {
    "cond_size_pt": (5.0, 20.0), "cond_structure_pct": (10.0, 300.0),
    "arrow_before_mm": (0.0, 50.0), "arrow_after_mm": (0.0, 50.0),
    "arrow_min_mm": (2.0, 150.0), "scale_pct": (20.0, 300.0), "gap_mm": (0.0, 20.0)}
CHOICES: dict[str, tuple[str, ...]] = {"top_align": ALIGNS, "bottom_align": ALIGNS,
                                       "fit": FITS, "yields": YIELD_PLACES}
SWITCHES = ("align_ids", "show_label")         # on/off settings (true/false in the JSON)
PROJECT_KEY = "schemes"   # project.json: the project's defaults (differences only)


class SchemeEditorError(Exception):
    """A problem the user should be told about in plain language."""


# ---------------------------------------------------------------------- layout settings

def clean_value(key: str, value) -> Any:
    """`value` for layout setting `key` if it is a sensible one, else None."""
    if key in SWITCHES:
        return value if isinstance(value, bool) else None
    if key in CHOICES:
        value = {"centre": "center"}.get(str(value).lower(), str(value).lower())
        return value if value in CHOICES[key] else None
    if key in LIMITS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        lo, hi = LIMITS[key]
        return round(min(max(number, lo), hi), 3) if math.isfinite(number) else None
    return None


def _cleaned(values) -> dict[str, Any]:
    out = {}
    for key, value in (values or {}).items() if isinstance(values, dict) else []:
        good = clean_value(key, value)
        if good is not None:
            out[key] = good
    return out


def project_layout(settings: dict) -> dict[str, Any]:
    """The project's scheme layout defaults (Project settings ▸ Schemes)."""
    return {**DEFAULT_LAYOUT, **_cleaned(settings.get(PROJECT_KEY))}


def layout_overrides(scheme) -> dict[str, Any]:
    """This scheme's own layout settings (its Layout… window), cleaned."""
    data = scheme.data.get(EDITOR_KEY)
    return _cleaned(data.get("layout")) if isinstance(data, dict) else {}


def scheme_layout(settings: dict, scheme) -> dict[str, Any]:
    """The layout a scheme is printed with: its own settings over the project's."""
    return {**project_layout(settings), **layout_overrides(scheme)}


def _differences(values: dict, base: dict) -> dict[str, Any]:
    out = {}
    for key, value in _cleaned(values).items():
        if value != base.get(key):
            out[key] = value
    return out


def set_project_layout(settings: dict, values: dict) -> None:
    """Keep the project's defaults (only what differs from the built-in ones)."""
    diff = _differences(values, DEFAULT_LAYOUT)
    if diff:
        settings[PROJECT_KEY] = diff
    else:
        settings.pop(PROJECT_KEY, None)


def set_scheme_layout(project, scheme, values: dict) -> None:
    """Keep a scheme's own layout (only what differs from the project's defaults)."""
    diff = _differences(values, project_layout(project.settings))
    data = editor_data(scheme)
    if diff:
        data["layout"] = diff
    else:
        data.pop("layout", None)
    project.save_scheme(scheme)


# ---------------------------------------------------------------------- reading the data

def upgrade_old_scheme(scheme) -> None:
    """A scheme page made as one pasted picture (before the scheme editor, or with the
    "One pasted picture" choice since removed, SPEC §13p.5) opens in the scheme editor:
    boxes and steps it already had are used; with none, one empty starting material and
    one step whose products are the page's compounds (to be rearranged and their
    structures filled in from their pages). Only in memory: written with the page's next
    save. Its picture file is left in its folder, unused."""
    if scheme.data.get(MODE_KEY) == EDITOR:
        return
    scheme.data[MODE_KEY] = EDITOR
    data = editor_data(scheme)
    if not data["starting"] and not data["steps"]:
        start_empty(scheme)
        if scheme.children:
            data["steps"][0]["products"] = [{"id": new_id("b"), "compound": c.id}
                                            for c in scheme.children]


def editor_data(scheme) -> dict:
    """The editor's part of scheme.json (made, empty, if there is none yet). A hand-edited
    file is tidied as it is read: lists that aren't lists become empty, texts that aren't
    texts become text, and every step and box gets an ID. The lists are tidied in place,
    so a list a caller already holds (find_box's) stays the one in the data."""
    data = scheme.data.get(EDITOR_KEY)
    if not isinstance(data, dict):
        data = scheme.data[EDITOR_KEY] = {}
    for key, empty in (("starting", list), ("steps", list), ("inline", dict)):
        if not isinstance(data.get(key), empty):
            data[key] = empty()
    data["starting"][:] = [b for b in data["starting"] if _is_box(b)]
    steps = []
    for step in data["steps"]:
        if isinstance(step, dict):
            if not isinstance(step.get("id"), str) or not step["id"]:
                step["id"] = new_id("st")
            products = step.get("products")
            if isinstance(products, list):
                products[:] = [b for b in products if _is_box(b)]
            else:
                step["products"] = []
            for which in ("top", "bottom"):
                step[which] = _text(step.get(which))
            steps.append(step)
    data["steps"][:] = steps
    return data


def _text(value) -> str:
    """A stored text: None → "", a number (hand-edited) → its digits."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _is_box(box) -> bool:
    if not isinstance(box, dict):
        return False
    if not isinstance(box.get("id"), str) or not box["id"]:
        box["id"] = new_id("b")
    for key in ("label", "yield"):
        if key in box and not isinstance(box[key], str):
            box[key] = _text(box[key])
    for key in ("picture", "structure", "compound"):   # (hand-edited: not a name or ID)
        if key in box and not isinstance(box[key], str):
            del box[key]
    return True


def is_locked(scheme) -> bool:
    """"Save scheme" locked the editor (its own key: a picture scheme's "saved" is not
    the editor's)."""
    data = scheme.data.get(EDITOR_KEY)
    return isinstance(data, dict) and data.get("locked") is True


def set_locked(project, scheme, locked: bool) -> None:
    """Lock ("Save scheme") or unlock ("Edit scheme") the editor. If scheme.json can't be
    saved (e.g. held by OneDrive), the scheme in memory is put back as it was."""
    data = editor_data(scheme)
    was = data.get("locked", _MISSING)
    if locked:
        data["locked"] = True
    else:
        data.pop("locked", None)
    try:
        project.save_scheme(scheme)
    except BaseException:
        _put_back(data, "locked", was)
        raise


_MISSING = object()


def _put_back(data: dict, key: str, old) -> None:
    """Undo `data[key] = …` after a save failed (`old` is _MISSING: it was not there), so
    memory still matches the file and a later save can't write it unseen."""
    if old is _MISSING:
        data.pop(key, None)
    else:
        data[key] = old


def starting(scheme) -> list[dict]:
    return editor_data(scheme)["starting"]


def steps(scheme) -> list[dict]:
    return editor_data(scheme)["steps"]


def all_boxes(scheme) -> list[tuple[dict, str]]:
    """(box, where) for every box, left to right: where is START or the step's ID."""
    data = editor_data(scheme)
    out = [(b, START) for b in data["starting"]]
    for step in data["steps"]:
        out += [(b, step["id"]) for b in step["products"]]
    return out


def find_box(scheme, box_id: str) -> tuple[dict, list, str]:
    """(box, the list it is in, where); KeyError if there is no such box."""
    data = editor_data(scheme)
    for b in data["starting"]:
        if b["id"] == box_id:
            return b, data["starting"], START
    for step in data["steps"]:
        for b in step["products"]:
            if b["id"] == box_id:
                return b, step["products"], step["id"]
    raise KeyError(box_id)


def find_step(scheme, step_id: str) -> dict:
    for step in steps(scheme):
        if step["id"] == step_id:
            return step
    raise KeyError(step_id)


def step_number(scheme, step_id: str) -> int:
    """1, 2, 3… (for messages)."""
    return next((i + 1 for i, s in enumerate(steps(scheme)) if s["id"] == step_id), 0)


def path_of(scheme, rel: str | None) -> Path | None:
    """A file named in scheme.json (relative to the scheme's folder), or None (also for a
    hand-edited name that is not text)."""
    return scheme.folder / rel if isinstance(rel, str) and rel else None


def box_compound(project, scheme, box: dict):
    """The sub-compound a box is linked to (it must still be one of this scheme's), or
    None."""
    cid = box.get("compound")
    if not cid:
        return None
    return next((c for c in scheme.children if c.id == cid), None)


_mols: dict[Path, tuple[tuple[int, int], Any]] = {}


def read_mol(path: Path | None):
    """The RDKit molecule in a MOL file (cached until it changes), or None."""
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _mols.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        mol = chem.parse_structure(read_text(path))
    except chem.ChemError:
        mol = None
    if len(_mols) > 500:
        _mols.clear()
    _mols[path] = (stamp, mol)
    return mol


@dataclass
class BoxView:
    """What a box shows and prints: a linked compound's structure and picture, or the
    box's own."""
    box: dict
    compound: Any                  # the linked sub-compound, or None
    mol: Any                       # RDKit molecule, or None
    picture: Path | None           # an existing picture file, or None
    scale_info: dict               # where the picture's bond lengths are (compound or box)

    @property
    def empty(self) -> bool:
        return self.mol is None and self.picture is None


def box_view(project, scheme, box: dict) -> BoxView:
    compound = box_compound(project, scheme, box)
    if compound is not None:
        picture = project.structure_image_path(compound)
        return BoxView(box, compound, project.structure_for_drawing(compound),
                       picture if picture is not None and picture.exists() else None,
                       compound.values.get("structure") or {})
    picture = path_of(scheme, box.get("picture"))
    return BoxView(box, None, read_mol(path_of(scheme, box.get("structure"))),
                   picture if picture is not None and picture.exists() else None, box)


def compound_yield(compound) -> str:
    """"92%" from a compound's Yield field ("" if it has none)."""
    if compound is None or "yield" not in compound.field_keys:
        return ""
    pct = str((compound.values.get("yield") or {}).get("percent", "")).strip()
    return f"{pct}%" if pct else ""


def box_yield(project, scheme, box: dict) -> str:
    """The yield printed for a box: typed on the scheme, else (a compound) its Yield
    field's percentage. Starting materials have none."""
    if "yield" in box:
        return str(box.get("yield") or "").strip()
    return compound_yield(box_compound(project, scheme, box))


def yield_is_typed(box: dict) -> bool:
    return "yield" in box


def conditions_yield_parts(project, scheme, step: dict) -> list[tuple[str, str]]:
    """A step's yields as printed under its conditions (Layout ▸ yields under the
    conditions): [(yield, ID label markup)] for each product with a yield. The label is ""
    when only one product has a yield ("92%"), else "92% (3b), 5% (3c)". The scheme editor
    and the compiler both use this, so what the editor shows is what prints."""
    found = [(y, label_markup(project, scheme, box)) for box in step["products"]
             if (y := box_yield(project, scheme, box))]
    if len(found) == 1:
        return [(found[0][0], "")]
    return found


def label_markup(project, scheme, box: dict) -> str:
    """The box's ID label as stored markup: a compound's own ID (as a mention, so it
    follows renumbering), the typed label, or "" (none)."""
    compound = box_compound(project, scheme, box)
    if compound is not None:
        return "@{" + compound.id + "}"
    return str(box.get("label") or "").strip()


LABEL_OFFSET_MAX = 200.0   # pt: an ID dragged (or hand-edited) further is kept at this


def label_offset(box: dict) -> tuple[float, float]:
    """Where the ID was dragged (pt from its usual place); nonsense (text, NaN, infinity)
    counts as 0 and huge values are kept within ±LABEL_OFFSET_MAX."""
    out = []
    for key in ("label_dx", "label_dy"):
        value = box.get(key) or 0.0
        try:
            v = 0.0 if isinstance(value, bool) else float(value)
        except (TypeError, ValueError):
            v = 0.0
        if not math.isfinite(v):
            v = 0.0
        out.append(min(max(v, -LABEL_OFFSET_MAX), LABEL_OFFSET_MAX))
    return out[0], out[1]


def linked_compound_ids(scheme) -> list[str]:
    """The sub-compounds linked to boxes, left to right, step by step."""
    ids = [c.id for c in scheme.children]
    out = []
    for box, _where in all_boxes(scheme):
        cid = box.get("compound")
        if cid in ids and cid not in out:
            out.append(cid)
    return out


def unlinked_children(scheme) -> list:
    linked = set(linked_compound_ids(scheme))
    return [c for c in scheme.children if c.id not in linked]


def inline_path(scheme, sid: str) -> Path | None:
    entry = editor_data(scheme)["inline"].get(sid)
    path = path_of(scheme, entry.get("picture")) if isinstance(entry, dict) else None
    return path if path is not None and path.exists() else None


def inline_entry(scheme, sid: str) -> dict:
    entry = editor_data(scheme)["inline"].get(sid)
    return entry if isinstance(entry, dict) else {}


def box_print_factor(settings: dict, info: dict, picture: Path | None) -> float:
    """How much a box's (or inline structure's) picture is enlarged so its bonds print at
    the project's bond length (SPEC §13n.2a); 1.0 when not measurable."""
    return drawing_scale.structure_print_factor(settings, info, picture)


# ---------------------------------------------------------------------- changing it

def start_empty(scheme) -> None:
    """A new editor scheme: one starting material, one step with one product."""
    data = editor_data(scheme)
    data["starting"].append(new_box())
    data["steps"].append(new_step())


def new_box() -> dict:
    return {"id": new_id("b")}


def new_step() -> dict:
    return {"id": new_id("st"), "top": "", "bottom": "", "products": [new_box()]}


def add_box(project, scheme, where: str, index: int | None = None) -> dict:
    """A new empty box: a starting material (where=START) or a product of a step."""
    target = starting(scheme) if where == START else find_step(scheme, where)["products"]
    box = new_box()
    target.insert(len(target) if index is None else max(0, min(index, len(target))), box)
    project.save_scheme(scheme)
    return box


def add_step(project, scheme, index: int | None = None) -> dict:
    """A new step (an arrow, its conditions and one empty product box) at the end, or
    before the step at `index`."""
    data = editor_data(scheme)
    step = new_step()
    data["steps"].insert(len(data["steps"]) if index is None
                         else max(0, min(index, len(data["steps"]))), step)
    project.save_scheme(scheme)
    return step


def move_box(project, scheme, box_id: str, delta: int) -> bool:
    """Move a box left (-1) or right (+1) among the starting materials or its step's
    products. False if it can't go further."""
    box, container, _where = find_box(scheme, box_id)
    i = container.index(box)
    j = i + delta
    if not 0 <= j < len(container):
        return False
    container[i], container[j] = container[j], container[i]
    sync_children(project, scheme, save=False)
    project.save_scheme(scheme)
    return True


def box_has_files(scheme, box: dict) -> bool:
    return any(path_of(scheme, box.get(k)) is not None and path_of(scheme, box.get(k)).exists()
               for k in ("picture", "structure"))


def remove_box(project, scheme, box_id: str, delete_compound: bool = False):
    """Take a box out of the scheme. Its own picture and structure go to the Trash (one
    item, restorable). A linked compound page is deleted too (to the Trash) only with
    `delete_compound`; otherwise it stays a compound of the scheme page, unlinked.
    Returns the Trash entry of the box's files, or None."""
    box, container, where = find_box(scheme, box_id)
    if box.get("compound"):
        unlink_box(project, scheme, box_id, delete_compound=delete_compound, copy=False)
        box, container, where = find_box(scheme, box_id)
    entry = _trash_box(project, scheme, box, where)
    container.remove(box)
    sync_children(project, scheme, save=False)
    project.save_scheme(scheme)
    return entry


def remove_step(project, scheme, step_id: str, delete_compounds: bool = False) -> None:
    """Take a step (its arrow, conditions and products) out of the scheme."""
    step = find_step(scheme, step_id)
    for box in list(step["products"]):
        remove_box(project, scheme, box["id"], delete_compound=delete_compounds)
    steps(scheme).remove(step)
    project.save_scheme(scheme)


def step_compounds(project, scheme, step_id: str) -> list:
    return [c for c in (box_compound(project, scheme, b)
                        for b in find_step(scheme, step_id)["products"]) if c is not None]


def _box_files(scheme, box: dict) -> list[Path]:
    """The box's own files that exist: picture, its original, structure."""
    from .project import picture_source_path
    out = []
    picture = path_of(scheme, box.get("picture"))
    if picture is not None and picture.exists():
        out.append(picture)
        original = picture_source_path(picture)
        if original is not None:
            out.append(original)
    structure = path_of(scheme, box.get("structure"))
    if structure is not None and structure.exists():
        out.append(structure)
    return out


def _trash_box(project, scheme, box: dict, where: str):
    files = _box_files(scheme, box)
    if not files:
        return None
    info = {"scheme_id": scheme.id, "scheme_name": scheme.name, "where": where,
            "box": {k: v for k, v in box.items() if k != "compound"},
            "files": {k: p.name for k in ("picture", "structure")
                      if (p := path_of(scheme, box.get(k))) is not None and p.exists()}}
    return project._move_to_trash(files[0], kind="scheme_box", name="structure from scheme "
                                  f"“{scheme.name}”", extra=info,
                                  companions=tuple(files[1:]))


def restore_box(project, entry) -> str:
    """Put a box that went to the Trash back into its scheme (in its step if that is
    still there, else as a product of the last step, or a starting material)."""
    try:
        scheme = project.scheme(entry.info.get("scheme_id", ""))
    except KeyError:
        from .project import ProjectError
        raise ProjectError("The scheme page this structure belonged to no longer exists. "
                           f"Its files are still in the Trash folder:\n{entry.folder}") from None
    box = copy.deepcopy(entry.info.get("box") or {})
    box["id"] = new_id("b")
    for key in ("picture", "structure"):
        box.pop(key, None)
    folder = scheme.folder / FILES_DIR
    names = entry.info.get("files") or {}
    from .project import VECTOR_EXTENSIONS, _unique_picture_path
    for key, name in names.items():
        source = entry.folder / name
        if not source.exists():
            continue
        dest = _unique_picture_path(folder, box["id"], source.suffix.lower())
        fileio.make_dirs(folder)
        fileio.copy_file(source, dest)
        box[key] = f"{FILES_DIR}/{dest.name}"
        if key == "picture":
            for ext in VECTOR_EXTENSIONS:
                original = source.with_suffix(ext)
                if original != source and original.exists():
                    fileio.copy_file(original, dest.with_suffix(ext))
    where = entry.info.get("where", START)
    if where == START:
        starting(scheme).append(box)
    else:
        try:
            find_step(scheme, where)["products"].append(box)
        except KeyError:
            if not steps(scheme):
                steps(scheme).append({"id": new_id("st"), "top": "", "bottom": "",
                                      "products": []})
            steps(scheme)[-1]["products"].append(box)
    project.save_scheme(scheme)
    return f"Restored a structure of the scheme “{scheme.name}”."


def _clear_own_files(project, scheme, box: dict) -> None:
    """Delete the box's own picture (with its original) and structure (an Edit ▸ Undo
    brings them back), and forget them."""
    for path in _box_files(scheme, box):
        fileio.remove_file(path)
    for key in ("picture", "structure", "picture_source", drawing_scale.MEASURED_KEY,
                drawing_scale.OVERRIDE_KEY):
        box.pop(key, None)


def _write_box_picture(project, scheme, box: dict, data: bytes | None, ext: str,
                       vector, vector_ext: str, source: Path | None, reference) -> None:
    from .project import PICTURE_FILE_EXTENSIONS
    source, ext = project._check_picture(source, ext, PICTURE_FILE_EXTENSIONS)
    folder = scheme.folder / FILES_DIR
    fileio.make_dirs(folder)
    dest = project._write_picture(folder, box["id"], source, data, ext, vector, vector_ext)
    box["picture"] = f"{FILES_DIR}/{dest.name}"
    box.pop(drawing_scale.OVERRIDE_KEY, None)
    measure_picture(box, dest, reference)


def measure_picture(info: dict, path: Path, reference=None) -> None:
    """Store the bond length of a vector picture (null: none found); none for raster."""
    if Path(path).suffix.lower() in drawing_scale.VECTOR_PICTURES:
        bond = drawing_scale.measure_file(Path(path), reference)
        info[drawing_scale.MEASURED_KEY] = round(bond, 3) if bond else None
    else:
        info.pop(drawing_scale.MEASURED_KEY, None)


def set_box_structure(project, scheme, box_id: str, text: str, picture=None) -> list[str]:
    """Store a structure (SMILES/MOL text; raises chem.ChemError) with ChemDraw's picture
    of it (`picture`: a clipboard PastedPicture, or None: the program draws it). A box
    linked to a compound changes the compound's structure (one source of truth). Returns
    the NMR nuclei added to the compound (e.g. ["19F"])."""
    box, _container, _where = find_box(scheme, box_id)
    compound = box_compound(project, scheme, box)
    if compound is not None:
        if picture is not None:
            return project.set_structure(compound, text, picture=picture.data,
                                         vector=picture.source, vector_ext=picture.source_ext,
                                         picture_ext=picture.ext)
        return project.set_structure(compound, text)
    mol = chem.parse_structure(text)
    if picture is not None:   # (checked before the old files are removed)
        from .project import PICTURE_FILE_EXTENSIONS
        project._check_picture(None, picture.ext, PICTURE_FILE_EXTENSIONS)
    old_pictures = _box_files(scheme, box)
    for path in old_pictures:
        fileio.remove_file(path)
    for key in ("picture", "picture_source", drawing_scale.MEASURED_KEY,
                drawing_scale.OVERRIDE_KEY):
        box.pop(key, None)
    folder = scheme.folder / FILES_DIR
    fileio.make_dirs(folder)
    from .project import _unique_picture_path
    dest = _unique_picture_path(folder, box["id"], ".mol")
    atomic_write_text(dest, chem.mol_to_block(mol))
    box["structure"] = f"{FILES_DIR}/{dest.name}"
    if picture is not None:
        reference = (drawing_scale.reference_from_mol(mol)
                     if chem.looks_like_molblock(text) else None)
        _write_box_picture(project, scheme, box, picture.data, picture.ext, picture.source,
                           picture.source_ext, None, reference)
        box["picture_source"] = "chemdraw"
    project.save_scheme(scheme)
    return []


def set_box_picture(project, scheme, box_id: str, data: bytes | None = None,
                    ext: str = ".png", vector=None, vector_ext: str = "",
                    source: Path | None = None) -> None:
    """A picture only (a screenshot, an image file, or a ChemDraw drawing without its
    structure). The structure (if any) stays; a compound's picture is the compound's."""
    box, _container, _where = find_box(scheme, box_id)
    compound = box_compound(project, scheme, box)
    if compound is not None:
        project.set_structure_image(compound, source=source, data=data, ext=ext,
                                    vector=vector, vector_ext=vector_ext)
        return
    from .project import PICTURE_FILE_EXTENSIONS, picture_source_path
    # (checked before the old picture is removed: a file that can't be used changes nothing)
    source, ext = project._check_picture(source, ext, PICTURE_FILE_EXTENSIONS)
    old = path_of(scheme, box.get("picture"))
    if old is not None and old.exists():
        original = picture_source_path(old)
        fileio.remove_file(old)
        if original is not None:
            fileio.remove_file(original)
    _write_box_picture(project, scheme, box, data, ext, vector, vector_ext, source, None)
    box["picture_source"] = "user"
    project.save_scheme(scheme)


def clear_box(project, scheme, box_id: str) -> None:
    """Remove the box's structure and picture (a linked compound's are left alone: the
    compound page's Structure field changes those)."""
    box, _container, _where = find_box(scheme, box_id)
    _clear_own_files(project, scheme, box)
    project.save_scheme(scheme)


def set_box_bond(project, scheme, box_id: str, own: float | None) -> None:
    """The printed size of a box's own picture: its bonds are `own` pt (0: print at its
    own size) or None (measured)."""
    box, _c, _w = find_box(scheme, box_id)
    if own is None:
        box.pop(drawing_scale.OVERRIDE_KEY, None)
    else:
        box[drawing_scale.OVERRIDE_KEY] = round(max(0.0, float(own)), 3)
    project.save_scheme(scheme)


def set_box_label(project, scheme, box_id: str, label: str) -> None:
    """The ID label of a box that is not a compound: typed text, a compound mention
    "@{c-…}", or "" for none."""
    box, _c, _w = find_box(scheme, box_id)
    label = label.strip()
    if label:
        box["label"] = label
    else:
        box.pop("label", None)
    project.save_scheme(scheme)


def set_label_offset(project, scheme, box_id: str, dx: float, dy: float) -> None:
    """Where the ID (and yield) under a structure was dragged: pt from its usual place."""
    box, _c, _w = find_box(scheme, box_id)
    dx, dy = label_offset({"label_dx": dx, "label_dy": dy})
    dx, dy = round(dx, 2), round(dy, 2)
    if abs(dx) < 0.05 and abs(dy) < 0.05:
        box.pop("label_dx", None)
        box.pop("label_dy", None)
    else:
        box["label_dx"], box["label_dy"] = dx, dy
    project.save_scheme(scheme)


def set_box_yield(project, scheme, box_id: str, text: str | None) -> None:
    """The yield printed for a box; None = automatic (a compound's Yield field)."""
    box, _c, _w = find_box(scheme, box_id)
    if text is None:
        box.pop("yield", None)
    else:
        box["yield"] = text.strip()
    project.save_scheme(scheme)


def set_conditions(project, scheme, step_id: str, which: str, text: str) -> None:
    """The conditions above ("top") or under ("bottom") a step's arrow (markup)."""
    step = find_step(scheme, step_id)
    key = "top" if which == "top" else "bottom"
    old = step.get(key, _MISSING)
    step[key] = text
    try:
        project.save_scheme(scheme)
    except BaseException:
        _put_back(step, key, old)   # (the typing stays in its box, to be tried again)
        raise


def set_conditions_align(project, scheme, step_id: str, which: str, align: str) -> None:
    """A conditions box's own alignment ("" = the scheme's Layout)."""
    step = find_step(scheme, step_id)
    key = "top_align" if which == "top" else "bottom_align"
    if align in ALIGNS:
        step[key] = align
    else:
        step.pop(key, None)
    project.save_scheme(scheme)


def conditions_align(settings: dict, scheme, step: dict, which: str) -> str:
    key = "top_align" if which == "top" else "bottom_align"
    own = step.get(key)
    return own if own in ALIGNS else scheme_layout(settings, scheme)[key]


def add_inline_structure(project, scheme, text: str, picture) -> str:
    """A structure for the conditions above an arrow (SPEC §13n.4): its picture (and
    structure, if one came with it) stored under editor/. Returns its ID for the text
    (@struct{s-…}). Raises SchemeEditorError when there is nothing to show."""
    mol = None
    if text and text.strip():
        try:
            mol = chem.parse_structure(text)
        except chem.ChemError:
            mol = None
    if picture is None and mol is None:
        raise SchemeEditorError("The clipboard has no structure or picture to put in the "
                                "conditions. Copy it in ChemDraw first.")
    sid = new_id("s")
    entry: dict[str, Any] = {}
    folder = scheme.folder / FILES_DIR
    fileio.make_dirs(folder)
    from .project import _unique_picture_path
    if mol is not None:
        dest = _unique_picture_path(folder, sid, ".mol")
        atomic_write_text(dest, chem.mol_to_block(mol))
        entry["structure"] = f"{FILES_DIR}/{dest.name}"
    if picture is not None:
        reference = (drawing_scale.reference_from_mol(mol)
                     if mol is not None and chem.looks_like_molblock(text) else None)
        holder = {"id": sid}
        _write_box_picture(project, scheme, holder, picture.data, picture.ext, picture.source,
                           picture.source_ext, None, reference)
        holder.pop("id")
        entry.update(holder)
    editor_data(scheme)["inline"][sid] = entry
    project.save_scheme(scheme)
    return sid


def set_inline_bond(project, scheme, sid: str, own: float | None) -> None:
    """The printed size of a structure in the conditions (its picture's bonds are `own`
    pt; 0: its own size; None: measured), as for boxes and compounds (SPEC §2.7)."""
    entry = editor_data(scheme)["inline"].get(sid)
    if not isinstance(entry, dict):
        raise SchemeEditorError("That structure is no longer in the conditions.")
    if own is None:
        entry.pop(drawing_scale.OVERRIDE_KEY, None)
    else:
        entry[drawing_scale.OVERRIDE_KEY] = round(max(0.0, float(own)), 3)
    project.save_scheme(scheme)


def export_inline(scheme, sid: str) -> dict | None:
    """Where a structure in the conditions keeps its files (full paths), for copying it
    with the text into another scheme (paste_inline). None if it has none."""
    entry = inline_entry(scheme, sid)
    out: dict[str, Any] = {"scheme": scheme.id}
    for key in ("picture", "structure"):
        path = path_of(scheme, entry.get(key)) if isinstance(entry.get(key), str) else None
        if path is not None and path.is_file():
            out[key] = str(path)
    if "picture" not in out and "structure" not in out:
        return None
    for key in (drawing_scale.MEASURED_KEY, drawing_scale.OVERRIDE_KEY):
        if key in entry:
            out[key] = entry[key]
    return out


def paste_inline(project, scheme, sid: str, info: dict | None) -> str | None:
    """A structure of copied conditions, pasted into `scheme`'s conditions: the same one
    if it is this scheme's, else a copy of its files (picture with its ChemDraw original,
    structure) under a new ID. None when its files can't be found (it is then left out
    of the pasted text)."""
    if sid in editor_data(scheme)["inline"] and (
            inline_path(scheme, sid) is not None
            or read_mol(path_of(scheme, inline_entry(scheme, sid).get("structure")))):
        return sid
    if not isinstance(info, dict):
        return None
    from .project import _unique_picture_path, picture_source_path
    sources = {}
    for key in ("picture", "structure"):
        value = info.get(key)
        if isinstance(value, str) and value and Path(value).is_file():
            sources[key] = Path(value)
    if not sources:
        return None
    new = new_id("s")
    folder = scheme.folder / FILES_DIR
    fileio.make_dirs(folder)
    entry: dict[str, Any] = {}
    for key, source in sources.items():
        dest = _unique_picture_path(folder, new, source.suffix.lower())
        fileio.copy_file(source, dest)
        entry[key] = f"{FILES_DIR}/{dest.name}"
        if key == "picture":
            original = picture_source_path(source)
            if original is not None:
                fileio.copy_file(original, dest.with_suffix(original.suffix.lower()))
    if "picture" in entry:
        for key in (drawing_scale.MEASURED_KEY, drawing_scale.OVERRIDE_KEY):
            if key in info:
                entry[key] = info[key]
    editor_data(scheme)["inline"][new] = entry
    project.save_scheme(scheme)
    return new


def inline_ids(text: str) -> list[str]:
    from .markup import parse
    return [r.structure for p in parse(text) for r in p.runs if r.structure]


# ---------------------------------------------------------------------- compounds

def sync_children(project, scheme, save: bool = True) -> bool:
    """Order the scheme's sub-compounds as their boxes (left to right, step by step);
    compounds not linked to a box follow in their order. True if anything moved."""
    order = linked_compound_ids(scheme)
    by_id = {c.id: c for c in scheme.children}
    new = [by_id[i] for i in order] + [c for c in scheme.children if c.id not in order]
    if [c.id for c in new] == [c.id for c in scheme.children]:
        return False
    scheme.children[:] = new
    if save:
        project.save_scheme(scheme)
    return True


def _children_index(scheme, box_id: str) -> int:
    """Where a compound linked to this box goes among the sub-compounds."""
    seen = 0
    ids = {c.id for c in scheme.children}
    for b, _w in all_boxes(scheme):
        if b["id"] == box_id:
            return seen
        if b.get("compound") in ids:
            seen += 1
    return seen


def link_new_compound(project, scheme, box_id: str, name: str = ""):
    """Mark a box as a compound: a new sub-compound page (with an ID from the section's ID
    table and a data page) takes the box's structure and picture. Returns it."""
    box, _c, where = find_box(scheme, box_id)
    if box_compound(project, scheme, box) is not None:
        return box_compound(project, scheme, box)
    # (A box with a generic structure (R, X, *: a general scheme) becomes a compound too:
    # its structure is kept and drawn, without formula or masses, and the page warns.)
    compound = project.add_compound(scheme.section_id, _children_index(scheme, box_id),
                                    name=name, scheme_id=scheme.id)
    _give_box_files_to(project, scheme, box, compound)
    box["compound"] = compound.id
    box.pop("label", None)   # (its ID is the compound's now)
    sync_children(project, scheme, save=False)
    project.save_scheme(scheme)
    return compound


def link_compound(project, scheme, box_id: str, compound_id: str) -> None:
    """Link a box to a sub-compound of this scheme that has no box yet. A compound with
    no structure takes the box's; otherwise the compound's is shown (the box's own is
    kept back, and comes back if they are unlinked)."""
    box, _c, _w = find_box(scheme, box_id)
    compound = next((c for c in scheme.children if c.id == compound_id), None)
    if compound is None:
        raise SchemeEditorError("That compound is not on this scheme page.")
    if compound_id in linked_compound_ids(scheme):
        raise SchemeEditorError("That compound is already shown by another box.")
    # (strict: a structure.mol that can't be read just now, e.g. offline, is not "no
    # structure" — the box's must not be written over it; nor is one with an R/X/* atom,
    # which the box then shows)
    if (project.structure_for_drawing(compound, strict=True) is None
            and project.structure_image_path(compound) is None):
        _give_box_files_to(project, scheme, box, compound)
    box["compound"] = compound_id
    box.pop("label", None)
    sync_children(project, scheme, save=False)
    project.save_scheme(scheme)


def _give_box_files_to(project, scheme, box: dict, compound) -> None:
    """The box's structure and picture become the compound's (then the box's own files
    are removed: one source of truth)."""
    mol_path = path_of(scheme, box.get("structure"))
    picture = path_of(scheme, box.get("picture"))
    has_mol = mol_path is not None and mol_path.exists()
    has_picture = picture is not None and picture.exists()
    if not has_mol and not has_picture:
        return
    from .project import picture_source_path
    original = picture_source_path(picture) if has_picture else None
    vector = original.read_bytes() if original is not None else None
    vector_ext = original.suffix if original is not None else ""
    if has_mol:
        text = read_text(mol_path)
        if has_picture and box.get("picture_source", "chemdraw") == "chemdraw":
            project.set_structure(compound, text, picture=picture.read_bytes(),
                                  vector=vector, vector_ext=vector_ext,
                                  picture_ext=picture.suffix.lower())
        else:
            project.set_structure(compound, text)
            if has_picture:
                project.set_structure_image(compound, data=picture.read_bytes(),
                                            ext=picture.suffix.lower(), vector=vector,
                                            vector_ext=vector_ext)
    elif has_picture:
        project.set_structure_image(compound, data=picture.read_bytes(),
                                    ext=picture.suffix.lower(), vector=vector,
                                    vector_ext=vector_ext)
    # The box's typed bond length read as printing reads it (a hand-edited "abc" or true
    # in scheme.json must not fail or become 1 pt).
    bond, typed = drawing_scale.picture_bond(
        {drawing_scale.OVERRIDE_KEY: box[drawing_scale.OVERRIDE_KEY]}
        if drawing_scale.OVERRIDE_KEY in box else {}, None)
    if has_picture and typed:
        project.set_structure_picture_bond(compound, bond or 0.0)
    _clear_own_files(project, scheme, box)


def _take_compound_files(project, scheme, box: dict, compound,
                         keep_old: bool = False) -> list[Path]:
    """A copy of the compound's structure and picture becomes the box's own (unlinking:
    the scheme still shows the same structure). All or nothing: if a file can't be copied
    (e.g. held by OneDrive), the copies made are removed and the box is left as it was
    (OSError). Returns the files written. The box's own files from before are deleted, or
    with `keep_old` only forgotten (the caller deletes them once the scheme is saved)."""
    folder = scheme.folder / FILES_DIR
    from .project import _unique_picture_path, picture_source_path
    new: dict = {}
    written: list[Path] = []
    try:
        mol_path = project.structure_path(compound)
        if mol_path.exists():
            fileio.make_dirs(folder)
            dest = _unique_picture_path(folder, box["id"], ".mol")
            fileio.copy_file(mol_path, dest)
            written.append(dest)
            new["structure"] = f"{FILES_DIR}/{dest.name}"
        picture = project.structure_image_path(compound)
        if picture is not None and picture.exists():
            fileio.make_dirs(folder)
            dest = _unique_picture_path(folder, box["id"], picture.suffix.lower())
            fileio.copy_file(picture, dest)
            written.append(dest)
            original = picture_source_path(picture)
            if original is not None:
                kept = dest.with_suffix(original.suffix.lower())
                fileio.copy_file(original, kept)
                written.append(kept)
            new["picture"] = f"{FILES_DIR}/{dest.name}"
            value = compound.values.get("structure") or {}
            new["picture_source"] = project.structure_picture_source(compound) or "user"
            for key in (drawing_scale.MEASURED_KEY, drawing_scale.OVERRIDE_KEY):
                if key in value:
                    new[key] = value[key]
    except BaseException:
        for path in written:
            try:
                fileio.remove_file(path)
            except OSError:
                pass   # (the first error is the one reported)
        raise
    if keep_old:
        for key in ("picture", "structure", "picture_source", drawing_scale.MEASURED_KEY,
                    drawing_scale.OVERRIDE_KEY):
            box.pop(key, None)
    else:
        _clear_own_files(project, scheme, box)
    box.update(new)
    return written


def unlink_box(project, scheme, box_id: str, delete_compound: bool = False,
               copy: bool = True) -> None:
    """Unmark a box as a compound. The box keeps a copy of the compound's structure and
    picture (`copy`). The compound page is deleted (to the Trash, restorable) only with
    `delete_compound`; otherwise it stays on the scheme page, unlinked."""
    box, _c, _w = find_box(scheme, box_id)
    compound = box_compound(project, scheme, box)
    box.pop("compound", None)
    if compound is not None and delete_compound:
        # The page goes to the Trash first: if it can't (a file open in another program,
        # or being synced), nothing has changed and the box stays linked. (Unlinked only
        # in memory meanwhile, so deleting it doesn't copy its files into the box too.)
        try:
            entry = project.delete_compound(compound.id)
        except Exception:
            box["compound"] = compound.id
            try:   # (the scheme may have been saved, unlinked, before the move failed)
                project.save_scheme(scheme)
            except Exception:
                pass   # (the first error is the one reported)
            raise
        if copy:   # (the box's copy is taken from the compound's folder in the Trash)
            folder = compound.folder
            compound.folder = entry.folder / folder.name
            try:
                _take_compound_files(project, scheme, box, compound)
            except OSError as e:
                # (the page is in the Trash: the delete is done; the scheme is saved with
                # the box unlinked and empty, and the user is told)
                sync_children(project, scheme, save=False)
                project.save_scheme(scheme)
                from .project import BoxCopyFailed
                raise BoxCopyFailed(entry, compound.name or "unnamed compound", e) from e
            finally:
                compound.folder = folder
    elif compound is not None and copy:
        _take_compound_files(project, scheme, box, compound)
    sync_children(project, scheme, save=False)
    project.save_scheme(scheme)


def compound_leaving(project, scheme, compound_id: str) -> None:
    """A sub-compound is about to be moved off this scheme page: the box that showed it
    keeps a copy of its structure and picture, unlinked, and the scheme is saved. All or
    nothing: if a file can't be copied or scheme.json can't be saved (e.g. held by
    OneDrive), the box is put back as it was (still linked, as in scheme.json) and the
    copies made are removed (OSError)."""
    leaving = leave_boxes(project, scheme, compound_id)
    if leaving is None:
        return
    try:
        project.save_scheme(scheme)
    except BaseException:
        leaving.put_back()
        raise
    leaving.done()


class Leaving:
    """The boxes leave_boxes unlinked: put_back() if the scheme could not be saved, done()
    once it is."""

    def __init__(self, scheme, boxes: list[dict]):
        self.boxes = boxes
        self.before = [copy.deepcopy(b) for b in boxes]
        self.old_files = [p for b in boxes for p in _box_files(scheme, b)]
        self.written: list[Path] = []

    def put_back(self) -> None:
        """The boxes as they were (linked, as in scheme.json); the copies made removed."""
        for box, was in zip(self.boxes, self.before):
            box.clear()
            box.update(copy.deepcopy(was))
        for path in self.written:
            try:
                fileio.remove_file(path)
            except OSError:
                pass   # (the first error is the one reported; a spare copy does no harm)
        self.written = []

    def done(self) -> None:
        """Saved: the boxes' own files from before (not used while linked) are removed."""
        for path in self.old_files:
            try:
                fileio.remove_file(path)
            except OSError:
                pass   # (no longer used by any box: a spare file does no harm)


def leave_boxes(project, scheme, compound_id: str) -> Leaving | None:
    """compound_leaving without the save: the boxes linked to the compound are unlinked in
    memory, each keeping a copy of its structure and picture. None if no box showed it.
    The caller saves the scheme, then calls done() (or put_back() if the save failed). If
    a file can't be copied, the boxes are put back as they were and the OSError raised."""
    if not isinstance(scheme.data.get(EDITOR_KEY), dict):
        return None
    boxes = [b for b, _w in all_boxes(scheme) if b.get("compound") == compound_id]
    if not boxes:
        return None
    leaving = Leaving(scheme, boxes)
    try:
        for box in boxes:
            compound = box_compound(project, scheme, box)
            box.pop("compound", None)
            if compound is not None:
                leaving.written += _take_compound_files(project, scheme, box, compound,
                                                        keep_old=True)
    except BaseException:
        leaving.put_back()
        raise
    return leaving


def start_with_compound(project, scheme, compound) -> None:
    """A scheme page made from a compound page ("Change to reaction scheme…"): an empty
    starting material, one step, and the compound as its product."""
    data = editor_data(scheme)
    data["starting"] = [new_box()]
    step = new_step()
    step["products"][0]["compound"] = compound.id
    data["steps"] = [step]
    scheme.data[MODE_KEY] = EDITOR
    project.save_scheme(scheme)


# ---------------------------------------------------------------------- pictures

def picture_slots(project, scheme):
    """(picture, its original or None, moved(new path)) for each box's own picture and
    each structure in the conditions ("Re-render pasted pictures", SPEC §13n.1)."""
    from .project import picture_source_path
    if not isinstance(scheme.data.get(EDITOR_KEY), dict):
        return
    holders = [b for b, _w in all_boxes(scheme)]
    holders += [e for e in editor_data(scheme)["inline"].values() if isinstance(e, dict)]
    for holder in holders:
        path = path_of(scheme, holder.get("picture"))
        if path is None or not path.exists():
            continue

        def moved(new, h=holder, sc=scheme):
            h["picture"] = f"{FILES_DIR}/{Path(new).name}"
            mol = read_mol(path_of(sc, h.get("structure")))
            reference = (drawing_scale.reference_from_mol(mol)
                         if mol is not None and h.get("picture_source", "chemdraw") == "chemdraw"
                         else None)
            measure_picture(h, Path(new), reference)
            project.save_scheme(sc)
        yield path, picture_source_path(path), moved


def warnings(project, scheme, report=None) -> list[str]:
    """Plain-language problems of an editor scheme (printed as compile warnings)."""
    out = []
    data = editor_data(scheme)
    if not data["starting"] and not data["steps"]:
        return [f"Scheme “{scheme.name}” is empty: add starting materials and steps."]
    for box, where in all_boxes(scheme):
        what = ("a starting material" if where == START
                else f"a product of step {step_number(scheme, where)}")
        if box.get("compound") and box_compound(project, scheme, box) is None:
            out.append(f"Scheme “{scheme.name}”: {what} was linked to a compound that is no "
                       "longer on this scheme page.")
        view = box_view(project, scheme, box)
        if view.empty:
            out.append(f"Scheme “{scheme.name}”: {what} has no structure yet; left out.")
    for i, step in enumerate(data["steps"]):
        if not step["products"]:
            out.append(f"Scheme “{scheme.name}”: step {i + 1} has no products.")
        for which in ("top", "bottom"):
            for sid in inline_ids(step.get(which, "")):
                if inline_path(scheme, sid) is None and read_mol(
                        path_of(scheme, inline_entry(scheme, sid).get("structure"))) is None:
                    out.append(f"Scheme “{scheme.name}”: a structure in the conditions of step "
                               f"{i + 1} is missing; left out.")
    return out
