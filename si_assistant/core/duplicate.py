"""Duplicating compound pages and scheme pages (SPEC §13q.3). No Qt.

A copy has the same fields as the original; which fields' contents are copied is the
user's choice (`keys`). Attached files never are: spectra, Additional files (and custom
attachment fields), HRMS report files, the stored private files, and the NMR peak
reports — they belong to the original compound and copying them would only cause wrong
data. Nor are the manual ID, flags or dismissed warnings. Copies take their IDs from the
ID table like any new compound: placed at the end of the section they take the next
unused IDs and nothing else is renumbered; placed right after the original, the
compounds after them move up the table.

A scheme page is copied with its drawing (boxes, steps, conditions, the structures in the
conditions and its layout, but not its typed label or its "Save scheme" lock) and all
its compounds, each copied with the same choice of fields and linked to the same box of
the copy.
"""

from __future__ import annotations

import copy
from pathlib import Path

from . import fileio
from .project import Compound, Project, ProjectError, SchemePage, picture_source_path

NEVER_COPIED_KINDS = ("attachments", "private", "nmr")
AFTER, END = "after", "end"          # where the copies go
MAX_COPIES = 50
# The HRMS value's report files (and where Found came from) belong to the original.
HRMS_FILE_KEYS = ("reports", "found_from")


def copyable_fields(project: Project, compound: Compound) -> list:
    """The compound's fields whose contents can be copied (FieldDefs, page order)."""
    return [d for d in project.field_defs(compound) if d.kind not in NEVER_COPIED_KINDS]


def never_copied_fields(project: Project, compound: Compound) -> list:
    """The compound's fields that are never copied (for the dialog to say so)."""
    return [d for d in project.field_defs(compound) if d.kind in NEVER_COPIED_KINDS]


def copy_name(name: str, number: int, copies: int) -> str:
    """"Route (copy)", or with several copies "Route (copy 1)", "Route (copy 2)"…"""
    base = name.strip() or "Scheme"
    return f"{base} (copy)" if copies == 1 else f"{base} (copy {number})"


def _copy_file(source: Path, folder: Path) -> str:
    target = folder / source.name
    fileio.copy_file(source, target)
    return target.name


def _fill_copy(project: Project, original: Compound, new: Compound, keys: set[str]) -> None:
    """Give `new` the fields of `original` and the contents of the fields in `keys`."""
    new.data["fields"] = copy.deepcopy(original.fields)
    new.values.clear()
    by_key = {d.key: d for d in project.field_defs(original)}
    for key in keys:
        defn = by_key.get(key)
        if defn is None or defn.kind in NEVER_COPIED_KINDS:
            continue
        if defn.kind in ("richtext", "notes"):
            text = project.read_compound_text(original, key)
            if text:
                project.write_compound_text(new, key, text)
        if defn.kind == "structure":
            _copy_structure(project, original, new)
            continue
        if key in original.values:
            value = copy.deepcopy(original.values[key])
            if key == "hrms" and isinstance(value, dict):
                for k in HRMS_FILE_KEYS:
                    value.pop(k, None)
            new.values[key] = value
        if key == "scale" and "scale_from_gp" in original.data:
            new.data["scale_from_gp"] = copy.deepcopy(original.data["scale_from_gp"])
    # Fields that are never copied start empty (as a new compound's do).
    for defn in project.field_defs(original):
        if defn.kind == "nmr":
            new.values[defn.key] = project.default_nmr_reports()
    project.save_compound(new)
    # A copied structure gets its automatic ¹⁹F/³¹P/¹¹B report boxes, as a structure set
    # any other way does (SPEC §13.3); none without one. (Saves the compound if it adds.)
    project.ensure_heteroatom_reports(new)


def _copy_structure(project: Project, original: Compound, new: Compound) -> None:
    source = project.structure_path(original)
    if source.exists():
        fileio.copy_file(source, project.structure_path(new))
    value = copy.deepcopy(original.values.get("structure"))
    if isinstance(value, dict):
        picture = project.structure_image_path(original)
        if picture is not None and picture.exists():
            value["image_override"] = _copy_file(picture, new.folder)
            vector = picture_source_path(picture)
            if vector is not None:
                _copy_file(vector, new.folder)
        else:
            value.pop("image_override", None)
        new.values["structure"] = value


def _place_index(project: Project, section, original_top, number: int, place: str):
    if place == END:
        return None
    return section.items.index(original_top) + number


def duplicate_compound(project: Project, compound: Compound, keys, copies: int = 1,
                       place: str = END) -> list[Compound]:
    """`copies` copies of a compound page with the contents of the fields in `keys`.
    A compound of a scheme page is copied as a compound page of its own (after the
    scheme page, or at the end of the section)."""
    project._check_writable()
    copies = max(1, min(int(copies), MAX_COPIES))
    keys = set(keys)
    section = project.section(compound.section_id)
    top = project.scheme(compound.scheme_id) if compound.scheme_id else compound
    out = []
    try:
        for n in range(1, copies + 1):
            new = project.add_compound(section.id, _place_index(project, section, top, n, place),
                                       save_owner=False)
            out.append(new)
            _fill_copy(project, compound, new, keys)
    finally:
        # section.json is saved once, also if a copy failed half-way, so its list of
        # compounds always matches the compound folders on disk.
        project.save_section(section)
    return out


def duplicate_scheme(project: Project, scheme: SchemePage, keys, copies: int = 1,
                     place: str = END) -> list[SchemePage]:
    """`copies` copies of a scheme page: its drawing and every compound on it (the
    contents of the fields in `keys`), each linked to the same box as in the original."""
    from . import scheme_editor as se
    project._check_writable()
    copies = max(1, min(int(copies), MAX_COPIES))
    keys = set(keys)
    section = project.section(scheme.section_id)
    out = []
    for n in range(1, copies + 1):
        new = project.add_scheme(section.id, _place_index(project, section, scheme, n, place),
                                 copy_name(scheme.name, n, copies))
        mapping = {}
        try:
            # The drawing's files first, then the drawing: if a file can't be copied (open
            # in another program, a full disk), the copy keeps its empty drawing, and is
            # still saved below as it is in memory (never with boxes linked to the
            # original's compounds).
            files = scheme.folder / se.FILES_DIR
            if files.is_dir():
                for f in files.iterdir():
                    if f.is_file():
                        fileio.make_dirs(new.folder / se.FILES_DIR, exist_ok=True)
                        fileio.copy_file(f, new.folder / se.FILES_DIR / f.name)
            editor = copy.deepcopy(se.editor_data(scheme))
            editor.pop("locked", None)
            new.data[se.EDITOR_KEY] = editor
            for child in scheme.children:
                copied = project.add_compound(section.id, None, scheme_id=new.id,
                                              save_owner=False)
                mapping[child.id] = copied.id   # (linked even if filling it fails)
                _fill_copy(project, child, copied, keys)
        finally:
            # Saved once, also if a copy failed half-way, so the copy's list of compounds
            # always matches the compound folders on disk, and scheme.json what is in
            # memory (its boxes linked only to the compounds copied).
            _follow_copies(new, mapping)
            se.sync_children(project, new, save=False)
            project.save_scheme(new)
        out.append(new)
    return out


def _follow_copies(new: SchemePage, mapping: dict[str, str]) -> None:
    """The copied scheme's boxes show the copied compounds; an ID label or conditions
    mentioning one of the original's compounds name its copy instead. (IDs are random,
    c-xxxxxxxx, so an exact replacement can't touch anything else.)"""
    from . import scheme_editor as se

    def follow(text: str) -> str:
        for old, copied_id in mapping.items():
            text = text.replace("@{" + old + "}", "@{" + copied_id + "}")
        return text

    for box, _where in se.all_boxes(new):
        if box.get("compound") in mapping:
            box["compound"] = mapping[box["compound"]]
        elif box.get("compound"):
            box.pop("compound")   # (linked to a compound no longer on the page)
        label = follow(str(box.get("label") or ""))   # (an ID label naming one of them)
        if label:
            box["label"] = label
    for step in se.steps(new):   # (conditions naming one of them, e.g. an intermediate)
        for which in ("top", "bottom"):
            step[which] = follow(step[which])


def check_copies(copies) -> int:
    """The number of copies asked for, as a whole number from 1 to MAX_COPIES."""
    try:
        n = int(copies)
    except (TypeError, ValueError):
        raise ProjectError("The number of copies must be a whole number.") from None
    if not 1 <= n <= MAX_COPIES:
        raise ProjectError(f"Make between 1 and {MAX_COPIES} copies at a time.")
    return n
