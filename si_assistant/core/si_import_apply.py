"""Writing an imported SI into a project (SPEC §13u.4, §13u.6, §13u.16).

`apply_plan` writes what the review window kept of an ImportPlan (core/si_import.py), as
one undo step:

* the references (each one the project already has, by its DOI, is not added again),
  so that the citations in everything written next can be linked to them;
* the cover page's title, authors and affiliations (only where the project has none);
* the SI's sections in reading order (core/si_import_text.py): the General Information
  page, custom sections with their figures and tables, and a Data section for each part
  with compounds, holding its general procedures and compounds;
* or, into an existing Data section: all the compounds there (a compound whose ID is
  already there is merged: only its empty fields are filled, nothing typed is
  overwritten), and the other sections as new sections.

New sections go before the Spectra section, so they print before the spectra.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from . import fields as F
from . import si_import_refs
from .si_import import ImportedCompound, ImportedGP, ImportPlan

PRINT_FROM_FIELDS = "fields"        # the program writes the text from the fields
PRINT_AS_IMPORTED = "as_imported"   # each compound's imported text, in its SI text field

# The order of the fields on an imported compound's page (= the order they print in).
FIELD_ORDER = ("structure", "name", "procedure", "scale", "yield", "physical_state",
               "purification", "rf", "mp", "ir", "rotation", "ee", "dr", "nmr", "hrms", "ea",
               "xray", "references", "spectra", "si_text")
DEFAULT_SECTION_FIELDS = ("structure", "name", "procedure", "yield", "physical_state", "nmr",
                          "hrms", "spectra")
REQUIRED = {"structure", "procedure", "yield", "physical_state", "nmr", "hrms", "spectra"}
NOTES_HEADING = "Imported from {file}, not recognised:"


@dataclass
class ApplyResult:
    section_id: str = ""                                  # the (first) Data section
    created: list[str] = field(default_factory=list)      # new compounds' internal ids
    merged: dict[str, list[str]] = field(default_factory=dict)   # ID -> fields kept as they were
    gps: list[str] = field(default_factory=list)          # new general procedures' ids
    notes: list[str] = field(default_factory=list)
    spectra: int = 0                                      # spectra attached
    sections: list[str] = field(default_factory=list)     # new sections' ids (text and data)
    general: bool = False                                 # General Information written
    references: int = 0                                   # references added
    references_known: int = 0                             # …already in the project
    figures: int = 0
    tables: int = 0
    cover: list[str] = field(default_factory=list)        # cover values filled in


def _used_keys(compounds: list[ImportedCompound], mode: str) -> list[str]:
    keys = set(DEFAULT_SECTION_FIELDS)
    for c in compounds:
        keys.update(k for k in c.values if k in F.ALL_FIELDS)
    if mode == PRINT_AS_IMPORTED:
        keys.add("si_text")
    return [k for k in FIELD_ORDER if k in keys]


def apply_plan(project, plan: ImportPlan, source_name: str, section_id: str | None = None,
               section_name: str = "", mode: str = PRINT_FROM_FIELDS,
               picture_for=None, spectrum_for=None, progress=None,
               figure_for=None) -> ApplyResult:
    """Write what the review window kept of `plan` (see the module's text). The compounds
    go into `section_id` (an existing Data section), or into new Data sections, one for
    each part of the SI with compounds (named `section_name` when there is just one and
    it is given). `picture_for(emf)` makes a ChemDraw picture to store from an EMF,
    `figure_for(emf)` a figure's picture from an embedded drawing's EMF, `spectrum_for(emf)`
    a spectrum from a MestReNova picture, all (data, ext, original, original_ext) or None
    (the window passes them; without them, structures are stored without their ChemDraw
    picture, such figures and Word spectra are left out). `progress(done, total, text)` is
    told how the spectra (the slow part) are going."""
    gps = [g for g in plan.gps if g.keep]
    compounds = [c for c in plan.compounds if c.keep]
    result = ApplyResult()
    with project.undo_step(f"Import SI from {source_name}"):
        cite = _Citer(_add_references(project, plan, result))
        _set_cover(project, plan, result)
        data_sections = _write_sections(project, plan, result, figure_for, cite,
                                         make_data=section_id is None)
        gp_ids: dict[str, str] = {}
        by_cid: dict = {}
        if section_id is not None:
            section = project.section(section_id)
            result.section_id = section.id
            gp_ids.update(_write_gps(project, section, gps, result, cite))
            report = project.id_report()
            existing = {}
            for comp in section.compounds:
                shown = report.ids.get(comp.id)
                if shown:
                    existing.setdefault(shown, comp)
            for c in compounds:
                target = existing.get(c.cid)
                if target is not None:
                    result.merged[c.cid] = _merge(project, target, c, gp_ids, mode,
                                                  source_name, picture_for, cite)
                    by_cid.setdefault(c.cid, target)
                else:
                    _make_room_for_id(project, section, c.cid)
                    comp = project.add_compound(section.id, name=c.name)
                    _fill(project, comp, c, gp_ids, mode, source_name, picture_for, cite)
                    result.created.append(comp.id)
                    by_cid.setdefault(c.cid, comp)
        else:
            placed = {id(c) for _s, imp in data_sections for c in imp.compounds} | \
                {id(g) for _s, imp in data_sections for g in imp.gps}
            loose_c = [c for c in compounds if id(c) not in placed]
            loose_g = [g for g in gps if id(g) not in placed]
            if loose_c or loose_g or not data_sections:
                # (no outline, or compounds outside it: one Data section, as before)
                section = project.add_section(
                    "data", section_name or plan.section_name or "Synthesis and Characterization",
                    _before_spectra(project))
                result.sections.append(section.id)
                data_sections.append((section, _Loose(loose_c, loose_g)))
            elif section_name and len(data_sections) == 1:
                project.rename_section(data_sections[0][0].id, section_name)
            for section, imp in data_sections:
                mine = [c for c in imp.compounds if c.keep]
                project.set_default_fields(section.id, [
                    {"key": k, "required": k in REQUIRED} for k in _used_keys(mine, mode)])
                project.set_id_table(section.id, [c.cid for c in mine])
                gp_ids.update(_write_gps(project, section, [g for g in imp.gps if g.keep],
                                         result, cite))
            result.section_id = data_sections[0][0].id if data_sections else ""
            for section, imp in data_sections:
                for c in imp.compounds:
                    if not c.keep:
                        continue
                    comp = project.add_compound(section.id, name=c.name)
                    _fill(project, comp, c, gp_ids, mode, source_name, picture_for, cite)
                    result.created.append(comp.id)
                    by_cid.setdefault(c.cid, comp)
        result.spectra = _attach_spectra(project, plan, by_cid, spectrum_for, result,
                                         progress)
    return result


@dataclass
class _Loose:
    compounds: list
    gps: list


def _before_spectra(project) -> int | None:
    """The top-level place just before the Spectra section (None: at the end)."""
    spectra = project.spectra_section
    if spectra is None or spectra.parent_id:
        return None
    return project.top_sections.index(spectra)


# ---------------------------------------------------------------------- references, cover

def _add_references(project, plan: ImportPlan, result: ApplyResult) -> dict[str, list[str]]:
    """Add the kept references; {number as cited: [reference IDs]} for the citations."""
    found = getattr(plan, "references", []) or []
    ids: dict[int, str] = {}
    for k, r in enumerate(found):
        if not r.keep:
            continue
        before = len(project.references)
        stored = project.add_reference(r.ref)
        ids[k] = stored["id"]
        if len(project.references) > before:
            result.references += 1
        else:
            result.references_known += 1
    return si_import_refs.citation_map(found, ids)


def _set_cover(project, plan: ImportPlan, result: ApplyResult) -> None:
    """The cover page's title, authors and affiliations, where the project has none (a
    title still the project's name counts as none)."""
    cover = getattr(plan, "cover", None)
    if cover is None or not cover.keep:
        return
    s = project.settings
    changed = False
    title = str(s.get("title") or "").strip()
    if cover.title and (not title or title == project.folder.name):
        s["title"] = cover.title
        result.cover.append("title")
        changed = True
    if cover.authors and not s.get("authors"):
        s["authors"] = [dict(a) for a in cover.authors]
        result.cover.append("authors")
        changed = True
    if cover.affiliations and not s.get("affiliations"):
        s["affiliations"] = [dict(a) for a in cover.affiliations]
        result.cover.append("affiliations")
        changed = True
    if changed:
        project.save_settings()


# ---------------------------------------------------------------------- sections

def _write_sections(project, plan: ImportPlan, result: ApplyResult, figure_for, cite,
                    make_data: bool) -> list:
    """The kept sections, in order; [(project Data section, imported section)] for the
    Data sections made (`make_data`)."""
    from . import front
    from .project import GENERAL_OWNER
    made: dict[str, object] = {}         # imported key -> project section
    data: list = []
    for s in getattr(plan, "sections", []) or []:
        if s.kind in ("references", "toc") or (s.kind == "spectra" and not s.keep):
            continue
        if s.kind == "data":
            if not make_data or not any(c.keep for c in s.compounds) and \
                    not any(g.keep for g in s.gps):
                continue
        elif not s.keep:
            continue
        parent = made.get(s.parent)
        if s.parent and parent is None:
            parent = _nearest_made(plan, s.parent, made)
        if s.kind == "general":
            owner = project.text_owner(GENERAL_OWNER)
            text = _resolve(project, owner, s, result, figure_for, cite)
            old = project.read_front_text("general")
            if old.strip():
                text = old.rstrip("\n") + "\n\n" + text
            elif s.name and s.name.strip().lower() != "general information":
                front.store_general_heading(project.settings, s.name)
                project.save_settings()
            project.write_front_text("general", text)
            result.general = True
            continue
        kind = "data" if s.kind == "data" else "text"
        name = s.name.strip() or ("Compounds" if kind == "data" else "Imported text")
        if parent is not None:
            section = project.add_section(kind, name, parent_id=parent.id)
        else:
            section = project.add_section(kind, name, _before_spectra(project))
        made[s.key] = section
        result.sections.append(section.id)
        if kind == "data":
            data.append((section, s))
        else:
            owner = project.text_owner(section.id)
            project.write_section_text(section, _resolve(project, owner, s, result,
                                                         figure_for, cite))
    return data


def _nearest_made(plan: ImportPlan, key: str, made: dict):
    """The nearest section above `key` that was made (its parent was left out)."""
    by_key = {s.key: s for s in plan.sections}
    while key:
        if key in made:
            return made[key]
        key = by_key[key].parent if key in by_key else ""
    return None


_PLACEHOLDER = re.compile(r"^(?P<prefix>(?:!align\{\w+\}\s*)?)!(?P<kind>figure|table)\{(?P<key>\w+)\}"
                          r"(?P<caption>.*)$")


def _resolve(project, owner, s, result: ApplyResult, figure_for, cite) -> str:
    """The section's text with its figures and tables stored (their lines pointing to
    them) and its citations linked. A figure that can't be stored (left out, or a PDF's
    picture that could not be cut out) leaves its caption as text."""
    from .tables import Table
    out = []
    skip: list[str] = []          # (a PDF figure's labels, under its caption: in its picture)
    for line in (s.text or "").split("\n"):
        if skip and line == skip[0]:
            skip.pop(0)
            continue
        skip = []
        m = _PLACEHOLDER.match(line)
        if not m or m.group("key") not in s.figures:
            out.append(cite(line))
            continue
        fig = s.figures[m.group("key")]
        if getattr(fig, "cut_below", False) and fig.data is not None:
            skip = list(fig.below_labels)
        caption = cite(m.group("caption").strip())
        if not fig.keep:
            continue
        stored = None
        if fig.kind == "table" and fig.table:
            data = dict(fig.table)
            data["label"] = fig.label
            data["rows"] = [[None if c is None else {**c, "text": cite(c.get("text", ""))}
                             for c in row] for row in data.get("rows", [])]
            data["footnotes"] = [cite(n) for n in data.get("footnotes", [])]
            stored = project.add_table(owner, Table.from_json(data))
            result.tables += 1
            out.append(f"{m.group('prefix')}!table{{{stored}}} {caption}".rstrip())
            continue
        picture = _figure_picture(fig, figure_for)
        if picture is not None:
            data_, ext, vector, vector_ext = picture
            stored = project.add_figure(owner, data=data_, ext=ext, vector=vector,
                                        vector_ext=vector_ext, label=fig.label,
                                        width=fig.width_cm)
            result.figures += 1
            out.append(f"{m.group('prefix')}!figure{{{stored}}} {caption}".rstrip())
        elif fig.label or caption:
            out.append(f"**{fig.label}.** {caption}".strip() if fig.label else caption)
    return "\n".join(out).strip("\n")


def _figure_picture(fig, figure_for):
    """(data, ext, vector, vector_ext) to store for a figure, or None."""
    if fig.data is None:
        return None
    if fig.kind == "object" or fig.ext in (".emf", ".wmf"):
        if figure_for is None or fig.ext == ".wmf":
            return None
        return figure_for(fig.data)
    return fig.data, fig.ext or ".png", None, ""


def _attach_spectra(project, plan: ImportPlan, by_cid: dict, spectrum_for,
                    result: "ApplyResult", progress=None) -> int:
    """The kept spectra of the plan (SPEC §13u.8), each in its compound's Spectra field:
    a Word MestReNova picture converted like a pasted Mnova spectrum (its EMF kept beside
    it); a spectrum cut out of a PDF page as a one-page PDF (printed without the
    program's structure box: the page shows the structure already). Returns how many."""
    import tempfile
    from pathlib import Path
    count = 0
    skipped = 0
    todo = [s for s in getattr(plan, "spectra", []) or [] if s.keep and s.cid in by_cid]
    for k, s in enumerate(todo):
        if progress is not None:
            progress(k, len(todo), f"Spectrum {k + 1} of {len(todo)} ({s.cid}, {s.type})")
        comp = by_cid[s.cid]
        if "spectra" not in comp.field_keys:
            project.add_fields(comp, [{"key": "spectra"}])
        if s.kind == "emf":
            picture = spectrum_for(s.data) if spectrum_for else None
            if picture is None:
                skipped += 1
                continue
            data, ext, original, original_ext = picture
            project.attach_pasted_spectrum(comp, data, ext, s.type, original or s.data,
                                           original_ext or ".emf")
        else:
            with tempfile.TemporaryDirectory(prefix="csa-spec-") as tmp:
                path = Path(tmp) / f"{s.cid or 'spectrum'} {s.type.replace('/', '-')}.pdf"
                path.write_bytes(s.data)
                item = project.attach_compound_file(comp, "spectra", path, {"type": s.type})
            if s.shows_structure:
                project.set_spectrum_overrides(comp, "spectra", item["file"],
                                               {"struct_box": False})
        count += 1
    if skipped:
        result.notes.append(f"{skipped} spectrum picture(s) could not be converted here and "
                            "were left out.")
    return count


class _Citer:
    """Links numbered citations in imported text to the imported references."""

    def __init__(self, numbers: dict[str, list[str]] | None = None):
        self.numbers = numbers or {}

    def __call__(self, markup: str) -> str:
        return si_import_refs.link_citations(markup, self.numbers) if self.numbers else markup

    def ids(self, cited: list[str]) -> list[str]:
        out: list[str] = []
        for n in cited:
            for rid in self.numbers.get(n, []):
                if rid not in out:
                    out.append(rid)
        return out


_same = _Citer()


def _write_gps(project, section, gps: list[ImportedGP], result: ApplyResult,
               cite=_same) -> dict[str, str]:
    """{plan key: GP id}: new general procedures; one whose name the section already has
    is not made again, and its compounds keep their own sentence (not linked)."""
    have = {g.get("name", "").strip().lower() for g in project.general_procedures(section)}
    out: dict[str, str] = {}
    for g in gps:
        if g.name.strip().lower() in have:
            result.notes.append(f"{g.name} is already in this section, so it was not imported "
                                "again; its compounds keep their own sentence.")
            continue
        gp = project.add_general_procedure(section.id, g.name)
        project.update_general_procedure(gp["id"], title=g.title, sentence=cite(g.sentence))
        project.write_gp_text(gp["id"], cite(g.text))
        out[g.key] = gp["id"]
        result.gps.append(gp["id"])
    return out


def _make_room_for_id(project, section, cid: str) -> None:
    """A compound added at the end of an existing section takes the ID table's row at its
    position: put the imported ID there (the rows after it, IDs no page has yet, move
    down one)."""
    table = list(section.id_table)
    pos = len(section.compounds)
    while len(table) < pos:
        table.append("")
    if pos < len(table) and str(table[pos]).strip() == cid:
        return
    table.insert(pos, cid)
    project.set_id_table(section.id, table)


def _procedure_text(c: ImportedCompound, linked: bool) -> str:
    if linked or not c.gp_sentence:
        return c.procedure
    return (c.gp_sentence.strip() + (" " + c.procedure if c.procedure else "")).strip()


def _values_for(c: ImportedCompound, project, cite=_same) -> dict:
    values = copy.deepcopy(c.values)
    for k in list(values):
        if k not in F.ALL_FIELDS:
            values.pop(k)
    refs = values.get("references")
    if isinstance(refs, dict) and c.cites:
        # the literature sentence's citation numbers → the imported references
        refs["refs"] = cite.ids(c.cites) or refs.get("refs", [])
    return values


def _set_structure(project, comp, c: ImportedCompound, picture_for) -> bool:
    """The imported structure (and its ChemDraw picture, made by `picture_for(emf)` →
    (data, ext, vector, vector_ext) or None). A typed HRMS formula that the structure now
    gives is dropped (the structure gives it). Returns whether it was set."""
    from . import chem, checks
    if c.picture_pdf and c.use_structure and not c.structure:
        # From a PDF: only the drawing, as the structure's picture (its formula, for the
        # HRMS, is the typed one).
        if not isinstance(comp.values.get("structure"), dict):
            comp.values["structure"] = {}
        project.set_structure_image(comp, data=c.picture_pdf, ext=".pdf")
        return True
    if not (c.structure and c.use_structure):
        return False
    picture = picture_for(c.structure_picture) if (picture_for and c.structure_picture) else None
    try:
        if picture is not None:
            data, ext, vector, vector_ext = picture
            project.set_structure(comp, c.structure, picture=data, picture_ext=ext,
                                  vector=vector, vector_ext=vector_ext)
        else:
            project.set_structure(comp, c.structure)
    except chem.ChemError:
        return False
    hrms = comp.values.get("hrms")
    if isinstance(hrms, dict) and hrms.get("formula"):
        try:
            typed = checks.hrms_species(None, hrms).info.counts
            drawn = checks.hrms_species(project.read_structure(comp), {**hrms, "formula": ""})
            if drawn is not None and chem.same_formula(typed, drawn.info.counts):
                hrms["formula"] = ""
                project.save_compound(comp)
        except chem.ChemError:
            pass
    return True


def _fill(project, comp, c: ImportedCompound, gp_ids: dict[str, str], mode: str,
          source_name: str, picture_for=None, cite=_same) -> None:
    linked = bool(c.gp_key and c.gp_key in gp_ids)
    values = _values_for(c, project, cite)
    comp.values.update(values)
    if c.name:
        comp.values["name"] = {"text": c.name}
    if linked:
        comp.values["procedure"] = {"gp": gp_ids[c.gp_key], "values": dict(c.gp_values)}
    keys = [k for k in FIELD_ORDER if k in values or (k == "procedure")]
    if mode == PRINT_AS_IMPORTED:
        keys.append("si_text")
    project.add_fields(comp, [{"key": k} for k in keys])
    project.write_compound_text(comp, "procedure", cite(_procedure_text(c, linked)))
    if mode == PRINT_AS_IMPORTED:
        project.write_compound_text(comp, "si_text", cite(c.original))
    _write_notes(project, comp, c, source_name)
    _set_structure(project, comp, c, picture_for)


def _merge(project, comp, c: ImportedCompound, gp_ids: dict[str, str], mode: str,
           source_name: str, picture_for=None, cite=_same) -> list[str]:
    """Fill the existing compound's empty fields; the labels of those left as they were."""
    kept: list[str] = []
    values = _values_for(c, project, cite)
    added: list[str] = []
    if c.name and not comp.name:
        comp.values["name"] = {"text": c.name}
        added.append("name")
    for key, value in values.items():
        defn = F.ALL_FIELDS[key]
        if key in comp.field_keys and project.field_filled(comp, defn):
            kept.append(defn.label)
            continue
        comp.values[key] = value
        added.append(key)
    proc = F.ALL_FIELDS["procedure"]
    if "procedure" in comp.field_keys and project.field_filled(comp, proc):
        if c.procedure or c.gp_sentence:
            kept.append(proc.label)
    else:
        linked = bool(c.gp_key and c.gp_key in gp_ids)
        if linked:
            comp.values["procedure"] = {"gp": gp_ids[c.gp_key], "values": dict(c.gp_values)}
        project.write_compound_text(comp, "procedure", cite(_procedure_text(c, linked)))
        added.append("procedure")
    if mode == PRINT_AS_IMPORTED and not project.read_compound_text(comp, "si_text").strip():
        project.write_compound_text(comp, "si_text", cite(c.original))
        added.append("si_text")
    project.add_fields(comp, [{"key": k} for k in FIELD_ORDER if k in added])
    if (c.structure or c.picture_pdf) and c.use_structure:
        if project.structure_path(comp).is_file() or project.structure_image_path(comp):
            kept.append(F.ALL_FIELDS["structure"].label)
        else:
            _set_structure(project, comp, c, picture_for)
    _write_notes(project, comp, c, source_name)
    return kept


def _write_notes(project, comp, c: ImportedCompound, source_name: str) -> None:
    if not c.unrecognised:
        return
    old = project.read_compound_text(comp, "notes").rstrip()
    text = NOTES_HEADING.format(file=source_name) + "\n" + "\n".join(c.unrecognised)
    project.write_compound_text(comp, "notes", (old + "\n\n" + text) if old else text)
