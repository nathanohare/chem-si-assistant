"""The Project Folder: everything about one SI lives here (SPEC §4).

    My SI/
    ├── project.json        title, authors, settings, the section tree
    ├── references.json     bibliography (used from Phase 7)
    ├── .lock               who has the project open
    ├── front/              front matter: general_information.txt, cover_text.txt, images/,
    │                       tables/, front.json (General Information's figures)
    ├── sections/<id>_<slug>/section.json (+ content.txt or an attached file;
    │       │                custom sections: images/ and tables/<table id>.json)
    │       └── compounds/<id>_<slug>/compound.json   (Data sections only)
    ├── trash/              deleted items, restorable
    ├── snapshots/          zipped copies made with "Save snapshot"
    └── build/              generated output only (and undo/, the undo history while the
                            project is open: core/undo.py); safe to delete

This module has no user-interface code so it can be unit-tested directly.
"""

from __future__ import annotations

import copy
import os
import re
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import chem
from . import drawing_scale
from . import fields as F
from . import lock as lockmod
from . import ms_reports
from . import references as refs
from . import spectra as spectra_mod
from . import undo
from .compound_ids import IdReport, SectionIds, check_ids
from . import fileio
from .fileio import atomic_write_bytes, atomic_write_json, atomic_write_text, read_json, read_text
from .ids import new_id, safe_filename, slugify
from .markup import objects as markup_objects
from .markup import parse as parse_markup
from .markup import stored_align as markup_stored_align
from .tables import Table

# The version of the project.json format. Raise it only when a change would make older
# versions of the program misread or damage a project; they then open it read-only after
# a warning (SPEC §13m.3). Projects without the number count as format 1.
FORMAT_VERSION = 1
SAVED_BY_KEY = "saved_by_version"   # the program version that last saved project.json
PROJECT_FILE = "project.json"
REFERENCES_FILE = "references.json"
DICTIONARY_FILE = "dictionary.txt"   # the project's spelling words, one per line (§13k.1a)
SECTIONS_DIR = "sections"
TRASH_DIR = "trash"
SNAPSHOTS_DIR = "snapshots"
BUILD_DIR = "build"
SECTION_FILE = "section.json"
TEXT_CONTENT_FILE = "content.txt"
TRASH_ENTRY_FILE = "entry.json"
COMPOUNDS_DIR = "compounds"
COMPOUND_FILE = "compound.json"
SCHEME_FILE = "scheme.json"
NOTES_FILE = "notes.txt"
STRUCTURE_FILE = "structure.mol"

PRIVATE_DIR = "private"
GP_DIR = "procedures"            # general procedure texts: <section>/procedures/<gp id>.txt
SECTION_IMAGES_DIR = "images"    # pictures in Text sections
# The sentence printed in a compound's block when it uses a general procedure. Words in
# {braces} are filled in per compound (SPEC §13j); the GP's own text is printed once.
DEFAULT_GP_SENTENCE = "Prepared according to {GP} from {starting material} ({scale})."
# Folder names are "<id>_<short readable name>". Kept short because Windows limits paths to
# 260 characters and projects often live deep inside OneDrive/Dropbox folders.
SECTION_SLUG = 20
ITEM_SLUG = 14
STRUCTURE_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg")
FIGURE_EXTENSIONS = STRUCTURE_IMAGE_EXTENSIONS + (".gif", ".pdf")   # figures in texts
# Structure and scheme pictures can also be a PDF: ChemDraw's copy on macOS, kept as the
# vector drawing it is (SPEC §13n.1).
PICTURE_FILE_EXTENSIONS = STRUCTURE_IMAGE_EXTENSIONS + (".pdf",)
# ChemDraw's vector original of a pasted picture is kept next to the PNG with the same
# name, e.g. structure_image.png + structure_image.emf (SPEC §13k.6).
VECTOR_EXTENSIONS = (".emf", ".pdf")
# Every kind of picture file. A new picture gets a name no other picture in its folder has
# (whatever the extension), so two pictures are never taken for a picture and its original.
PICTURE_EXTENSIONS = tuple(dict.fromkeys(FIGURE_EXTENSIONS + VECTOR_EXTENSIONS))

SECTION_TYPES = ("text", "file", "data")   # the kinds of section the user can add
# "text" sections are shown as "custom sections" (SPEC §13k.5) and "file" sections as
# "Attachment sections" (§13l.8); the stored types are unchanged.
SECTION_TYPE_LABELS = {"text": "Custom", "file": "Attachment", "data": "Data"}
# The Spectra section (SPEC §13k.12): one per project, added by the program; it can be
# moved but not deleted.
SPECTRA_SECTION = "spectra"
TYPE_LABELS = {**SECTION_TYPE_LABELS, SPECTRA_SECTION: "Spectra"}
SPECTRA_NOT_DELETED = ("The Spectra section cannot be deleted: every project has one, and it "
                       "is where the compounds' spectra are printed. You can move it anywhere "
                       "in the tree. To leave the spectra out of the SI, untick it in the "
                       "Compile SI window.")
# Custom sections hold sub-sections of any type, down to 1.1.1 (SPEC §13k.5). The nesting
# is stored in project.json ("section_tree"); the folders stay flat under sections/.
MAX_SECTION_DEPTH = 3

# Front matter (SPEC §13k.4): texts in front/, pictures in front/images/, tables in
# front/tables/, and the General Information figures list in front/front.json.
FRONT_DIR = "front"
FRONT_TEXT_FILES = {"general": "general_information.txt", "cover": "cover_text.txt"}
FRONT_DATA_FILE = "front.json"
GENERAL_OWNER = "general"        # the text owner key of General Information
TABLES_DIR = "tables"            # <owner>/tables/<table id>.json (SPEC §13k.5)

# File sections and compound attachments accept PDFs and images only (SPEC §13.2).
FILE_SECTION_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg")
ATTACHMENT_EXTENSIONS = FILE_SECTION_EXTENSIONS
# A pasted spectrum (SPEC §13l.1) is stored as "pasted 1H.svg" (or .png/.pdf), with the
# original copy (Mnova's .emf) next to it under the same name, named on its item as
# "original". The short names used for each spectrum type:
PASTED_SPECTRUM_NAMES = {"Other NMR": "other NMR", "HPLC/SFC": "HPLC-SFC", "Other": "spectrum"}
ORIGINAL_KEY = "original"


def pasted_spectrum_stem(spectrum_type: str) -> str:
    """"pasted 1H", "pasted 2D NMR", "pasted HPLC-SFC", "pasted spectrum" (never the ID)."""
    short = PASTED_SPECTRUM_NAMES.get(spectrum_type, spectrum_type)
    return "pasted " + (safe_filename(short).strip() or "spectrum")
# A picture pasted into an Attachment section (SPEC §13l.8) is stored under this name.
PASTED_ATTACHMENT_STEM = "pasted picture"

JOURNAL_STYLES = ("ACS", "Wiley", "RSC")
STRUCTURE_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


class ProjectError(Exception):
    """A problem the user should be told about in plain language."""


class ReadOnlyError(ProjectError):
    pass


def _format_number(value) -> int:
    """project.json's "format_version": a missing or unreadable value counts as 1 (projects
    from before the number was checked)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return max(number, 1)


def _program_version() -> str:
    from .. import __version__
    return __version__


def default_settings(title: str) -> dict[str, Any]:
    return {
        "title": title,
        # Each author: {"name", "affiliations": ["a", "b"], "corresponding": bool, "email"}
        "authors": [],
        # Each affiliation: {"marker": "a", "text": "Department of ..."}
        "affiliations": [],
        "journal_style": "ACS",
        "hrms_tolerance_ppm": 5.0,
        # (The spectra layout is on the Spectra section, core/spectra.py; older projects'
        # "spectra_per_page" and "structure_corner" are carried over into it.)
        # (The table of contents, cover heading and export choice live in "toc", "cover",
        # "general_information" and "export_excluded": see core/front.py.)
        # Add 31P / 11B NMR reports automatically when the structure has P / B
        # (19F is always added for F). Used from Phase 3. SPEC §13.3.
        "auto_nmr_31P": False,
        "auto_nmr_11B": False,
        # Compiling (SPEC §9): "pictures" = pasted ChemDraw/own pictures where available,
        # "acs" = the program's ACS-style drawings for every compound.
        "compile_structures": "pictures",
        "format_text": True,
        # Resolution of pictures pasted from ChemDraw: 600 or 1200 dpi (SPEC §13k.6).
        "paste_dpi": 1200,
    }


@dataclass
class Section:
    id: str
    type: str
    name: str
    folder: Path
    data: dict[str, Any] = field(default_factory=dict)  # full section.json
    # Data sections: compounds and scheme pages, in tree order.
    items: list["Compound | SchemePage"] = field(default_factory=list)
    # Custom sections: their sub-sections, in order. parent_id: the custom section this
    # one sits in (None at the top level).
    children: list["Section"] = field(default_factory=list)
    parent_id: str | None = None

    @property
    def compounds(self) -> list["Compound"]:
        """Every compound in tree order (inside scheme pages too). IDs follow this order."""
        out: list[Compound] = []
        for item in self.items:
            out.extend(item.children if isinstance(item, SchemePage) else [item])
        return out

    @property
    def schemes(self) -> list["SchemePage"]:
        return [i for i in self.items if isinstance(i, SchemePage)]

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.type, self.type)

    @property
    def can_hold_sections(self) -> bool:
        return self.type == "text"

    @property
    def id_table(self) -> list[str]:
        return self.data.setdefault("id_table", [])

    @property
    def default_fields(self) -> list[dict[str, Any]]:
        if "default_fields" not in self.data:   # (no copy made on every call)
            self.data["default_fields"] = copy.deepcopy(F.DEFAULT_SECTION_FIELDS)
        return self.data["default_fields"]

    def required_keys(self) -> set[str]:
        return {e["key"] for e in self.default_fields if e.get("required")}


@dataclass
class Compound:
    """One Compound Page. `data` is the full compound.json:

        {"id", "id_override": "", "fields": [{"key": ...}, ...],
         "values": {key: value}, "flags": {key: {"comment", "by", "at"}}}

    Removing a field only takes it out of "fields"; its value is kept so that
    adding the field back restores it.
    """
    id: str
    folder: Path
    data: dict[str, Any]
    section_id: str
    scheme_id: str | None = None  # set when the compound is a sub-page of a scheme page

    @property
    def fields(self) -> list[dict[str, Any]]:
        return self.data.setdefault("fields", [])

    @property
    def values(self) -> dict[str, Any]:
        return self.data.setdefault("values", {})

    @property
    def flags(self) -> dict[str, dict[str, str]]:
        return self.data.setdefault("flags", {})

    @property
    def field_keys(self) -> list[str]:
        return [e["key"] for e in self.fields]

    @property
    def name(self) -> str:
        return str((self.values.get("name") or {}).get("text", "")).strip()

    @property
    def id_override(self) -> str:
        return str(self.data.get("id_override") or "").strip()


@dataclass
class SchemePage:
    """A reaction scheme with its compounds as sub-pages (SPEC §6.3.5). `data` is scheme.json:

        {"id", "name", "children": [compound ids],
         "label": "Scheme 2",   (optional: typed over the automatic "Scheme S1", §13k.5a)
         "mode": "editor",      (made with the scheme editor, SPEC §13n.4)
         "editor": {…}}         (the scheme editor's boxes and steps: core/scheme_editor.py)

    Scheme pages made before the editor were one pasted picture with ID labels on it
    ("image", "labels", no "mode"). That way of making schemes was removed (SPEC §13p.5):
    such a page opens in the scheme editor (scheme_editor.upgrade_old_scheme), its
    compounds the products of one step; its picture file is left in its folder, unused.

    Folder: sections/<section>/compounds/<id>_<slug>/ with scheme.json and the editor's
    files (editor/). Its compounds' folders sit next to it in the
    section's compounds/ folder (kept flat to stay well inside Windows' 260-character path
    limit); scheme.json's "children" says which compounds belong to it.
    The scheme itself has no ID and no data fields.
    """
    id: str
    folder: Path
    data: dict[str, Any]
    section_id: str
    children: list[Compound] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.data.get("name") or "Scheme")



@dataclass
class TextOwner:
    """A text that can hold figures and tables (SPEC §13k.5): a custom section, or the
    General Information page (key "general"). Pictures are in `folder`/images/, tables in
    `folder`/tables/."""
    key: str
    name: str
    folder: Path


@dataclass
class TrashEntry:
    id: str
    kind: str  # "section", "file", "compound", "compound_file", "scheme", "scheme_file",
    #            "scheme_box" (a scheme editor's structure), "gp", "figure", "table",
    #            "manuscript_file" (SPEC §13o) or "incomplete" (a Trash folder with no
    #            description)
    name: str
    folder: Path
    info: dict[str, Any]

    @property
    def deleted_at(self) -> str:
        return self.info.get("deleted_at", "")

    @property
    def deleted_by(self) -> str:
        return self.info.get("deleted_by", "")


class Project:
    def __init__(self, folder: Path, read_only: bool = False):
        self.folder = Path(folder)
        self.read_only = read_only
        self.lock: lockmod.LockInfo | None = None
        self.settings: dict[str, Any] = {}
        self.top_sections: list[Section] = []   # the section tree's top level
        self.references: list[dict[str, Any]] = []
        self.load_warnings: list[str] = []
        self._structure_cache: dict[Path, tuple[tuple[int, int], object]] = {}
        self.journal: undo.Journal | None = None   # see enable_undo
        self.saved_format = FORMAT_VERSION   # read from project.json by reload()
        self.saved_by = ""

    @property
    def newer_format(self) -> bool:
        """Was the project saved in a newer file format than this program understands?
        (Opening it for editing could then lose data: SPEC §13m.3.)"""
        return self.saved_format > FORMAT_VERSION

    # ------------------------------------------------------------------ open/create

    @classmethod
    def create(cls, parent: Path, name: str) -> "Project":
        """Make a new Project Folder called `name` inside `parent`."""
        name = name.strip()
        if not name:
            raise ProjectError("Please give the project a name.")
        folder = Path(parent) / safe_filename(name)
        if folder.exists() and any(folder.iterdir()):
            raise ProjectError(f"A non-empty folder already exists at:\n{folder}")
        fileio.make_dirs(folder)
        (folder / SECTIONS_DIR).mkdir(exist_ok=True)
        settings = default_settings(name)
        atomic_write_json(folder / PROJECT_FILE, {
            "format_version": FORMAT_VERSION, SAVED_BY_KEY: _program_version(), **settings,
            "section_tree": [],
        })
        atomic_write_json(folder / REFERENCES_FILE, [])
        return cls.open(folder)

    @classmethod
    def open(cls, folder: Path, read_only: bool = False) -> "Project":
        """Load a project. Locking is separate: see `acquire_lock`."""
        folder = Path(folder)
        if not (folder / PROJECT_FILE).is_file():
            raise ProjectError(
                f"This folder is not an SI project (no {PROJECT_FILE} found):\n{folder}")
        project = cls(folder, read_only=read_only)
        project.reload()
        return project

    def reload(self) -> None:
        try:
            raw = read_json(self.folder / PROJECT_FILE, {})
        except ValueError as e:
            raise ProjectError(f"{PROJECT_FILE} could not be read: {e}") from e
        tree = raw.pop("section_tree", None)
        order = raw.pop("section_order", [])   # projects saved before sub-sections existed
        if not isinstance(tree, list):
            tree = [{"id": sid} for sid in order if isinstance(sid, str)]
        # Which file format (and program version) saved the project (SPEC §13m.3): a
        # newer format than this program knows is opened read-only after a warning.
        self.saved_format = _format_number(raw.pop("format_version", None))
        self.saved_by = str(raw.pop(SAVED_BY_KEY, "") or "")
        self.settings = {**default_settings(self.folder.name), **raw}
        # NMR impurity tolerances saved by older versions: their old defaults become the
        # new defaults (SPEC §13l.9). In memory only; saved with the next settings change.
        from . import impurities
        impurities.upgrade_settings(self.settings)
        self.load_warnings = []
        self.top_sections = self._load_sections(tree)
        self.references = self._load_references()

    def _load_sections(self, tree: list) -> list[Section]:
        found: dict[str, Section] = {}
        sections_dir = self.folder / SECTIONS_DIR
        if sections_dir.is_dir():
            for sub in sorted(sections_dir.iterdir()):
                if not (sub / SECTION_FILE).is_file():
                    continue
                try:
                    data = read_json(sub / SECTION_FILE, {})
                except ValueError:
                    self.load_warnings.append(f"Could not read {sub.name}/{SECTION_FILE}; skipped.")
                    continue
                sid = data.get("id") or sub.name.split("_", 1)[0]
                if sid in found:
                    self.load_warnings.append(
                        f"Two section folders share the ID {sid}; skipped {sub.name}.")
                    continue
                found[sid] = self._make_section(sub, data)
        top = self._place_tree(tree, found, 1, None)
        # Folders not in the saved tree (e.g. added by a sync) go at the end.
        for section in found.values():
            section.parent_id = None
            top.append(section)
        return top

    def _place_tree(self, nodes: list, found: dict[str, Section], depth: int,
                    parent: Section | None) -> list[Section]:
        """Build one level of the section tree from saved nodes {"id", "children": [...]},
        taking the sections out of `found`. Sub-sections that are not allowed where they
        were saved (inside a File/Data section, or deeper than three levels) are lifted up
        to sit after their parent, so nothing is ever lost."""
        out: list[Section] = []
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, str):
                node = {"id": node}
            if not isinstance(node, dict):
                continue
            kids = node.get("children") or []
            section = found.pop(node.get("id"), None)
            if section is None:   # its folder is gone: keep its sub-sections
                out += self._place_tree(kids, found, depth, parent)
                continue
            section.parent_id = parent.id if parent else None
            out.append(section)
            if section.can_hold_sections and depth < MAX_SECTION_DEPTH:
                section.children = self._place_tree(kids, found, depth + 1, section)
            else:
                section.children = []
                out += self._place_tree(kids, found, depth, parent)
        return out

    @staticmethod
    def _tree_json(sections: list[Section]) -> list[dict[str, Any]]:
        return [{"id": s.id, **({"children": Project._tree_json(s.children)}
                                if s.children else {})} for s in sections]

    def _make_section(self, folder: Path, data: dict[str, Any]) -> Section:
        sid = data.get("id") or folder.name.split("_", 1)[0]
        section = Section(id=sid, type=data.get("type", "text"),
                          name=data.get("name", folder.name), folder=folder, data=data)
        if section.type == "data":
            section.items = self._load_items(section)
        return section

    def _read_folders(self, parent: Path, json_name: str, where: str) -> dict[str, tuple[Path, dict]]:
        """{internal id: (folder, json data)} for sub-folders of `parent` holding `json_name`."""
        found: dict[str, tuple[Path, dict]] = {}
        if not parent.is_dir():
            return found
        for sub in sorted(parent.iterdir()):
            if not (sub / json_name).is_file():
                continue
            try:
                data = read_json(sub / json_name, {})
            except ValueError:
                self.load_warnings.append(f"Could not read {where} ▸ {sub.name}/{json_name}; "
                                          "skipped.")
                continue
            item_id = data.get("id") or sub.name.split("_", 1)[0]
            if item_id in found:
                self.load_warnings.append(f"Two folders share the ID {item_id}; skipped {sub.name}.")
                continue
            data["id"] = item_id
            found[item_id] = (sub, data)
        return found

    def _load_items(self, section: Section) -> list["Compound | SchemePage"]:
        folder = section.folder / COMPOUNDS_DIR
        compounds = {cid: Compound(cid, sub, data, section.id)
                     for cid, (sub, data) in self._read_folders(folder, COMPOUND_FILE,
                                                                section.name).items()}
        found: dict[str, Compound | SchemePage] = {}
        for kid, (sub, data) in self._read_folders(folder, SCHEME_FILE, section.name).items():
            found[kid] = self._make_scheme(section, sub, data, compounds)
        found.update(compounds)  # the compounds no scheme page claimed
        # Saved order; folders missing from it (e.g. added by a sync) go at the end.
        ordered = [found.pop(i) for i in section.data.get("compound_order", []) if i in found]
        ordered.extend(found.values())
        return ordered

    def _make_scheme(self, section: Section, folder: Path, data: dict,
                     compounds: dict[str, Compound]) -> SchemePage:
        """A scheme page, taking its children out of `compounds`."""
        from . import scheme_editor
        scheme = SchemePage(data["id"], folder, data, section.id)
        for cid in data.get("children", []):
            child = compounds.pop(cid, None)
            if child is not None:
                child.scheme_id = scheme.id
                scheme.children.append(child)
        scheme_editor.upgrade_old_scheme(scheme)   # (a pasted-picture scheme: SPEC §13p.5)
        return scheme
    # ------------------------------------------------------------------ locking

    def current_lock(self) -> lockmod.LockInfo | None:
        return lockmod.read_lock(self.folder)

    def acquire_lock(self, user: str) -> None:
        self.lock = lockmod.write_lock(self.folder, user)
        self.read_only = False

    def lock_still_ours(self) -> bool:
        return self.lock is not None and lockmod.is_ours(self.folder, self.lock.session)

    def close(self) -> None:
        if self.journal is not None:
            # Its history in build/undo/ goes with it, unless someone else has taken the
            # project over meanwhile: the folder then holds their history.
            taken_over = self.lock is not None and not self._lock_ours_or_unknown()
            self.journal.close(remove=not taken_over)
            self.journal = None
        if self.lock is not None:
            lockmod.release_lock(self.folder, self.lock.session)
            self.lock = None

    # ------------------------------------------------------------------ undo (§13k.11)

    def enable_undo(self) -> undo.Journal | None:
        """Start keeping an undo history (core/undo.py) for a project opened for editing:
        from now on every change to its files can be undone, about 50 steps back. Any
        history left in build/undo/ (e.g. after a crash) is removed first."""
        if self.read_only:
            return None
        if self.journal is None:
            self.journal = undo.Journal(self.folder)
            self.journal.start()
        return self.journal

    def _lock_ours_or_unknown(self) -> bool:
        try:
            return self.lock_still_ours() or lockmod.read_lock(self.folder) is None
        except OSError:
            return True

    def stop_undo(self) -> None:
        """Someone else has taken the project over: stop keeping a history, and leave
        build/undo/ (now theirs) alone."""
        if self.journal is not None:
            self.journal.close(remove=False)
            self.journal = None

    def undo_step(self, label: str, where=None, then_merge: bool = False):
        """`with project.undo_step("Delete compound 3a"): …` makes everything saved inside
        one step of the undo history (see undo.Journal.step)."""
        if self.journal is None:
            return nullcontext()
        return self.journal.step(label, where, then_merge)

    def autosave(self):
        """`with project.autosave(): …` around a page's save of what was typed on it:
        consecutive ones of the same page merge into one undo step (undo.Journal.autosave)."""
        if self.journal is None:
            return nullcontext()
        return self.journal.autosave()

    @property
    def can_undo(self) -> bool:
        return self.journal is not None and not self.read_only and self.journal.can_undo

    @property
    def can_redo(self) -> bool:
        return self.journal is not None and not self.read_only and self.journal.can_redo

    def undo(self) -> undo.Step:
        """Undo the newest step and reload the project from its files. Returns the step
        (its label and the tree places it was about, `where`). Raises ProjectError (in
        plain words) if it could not be done; the project is then left as it was."""
        return self._undo_or_redo(redo=False)

    def redo(self) -> undo.Step:
        return self._undo_or_redo(redo=True)

    def _undo_or_redo(self, redo: bool) -> undo.Step:
        self._check_writable()
        if self.journal is None:
            raise ProjectError("This project keeps no undo history.")
        try:
            step = self.journal.redo() if redo else self.journal.undo()
        except undo.UndoError as e:
            raise ProjectError(str(e)) from e
        finally:
            self._structure_cache.clear()
        self.reload()
        return step

    # ------------------------------------------------------------------ saving

    def _check_writable(self) -> None:
        if self.read_only:
            raise ReadOnlyError("This project is open read-only.")

    def save_settings(self) -> None:
        self._check_writable()
        atomic_write_json(self.folder / PROJECT_FILE, {
            "format_version": FORMAT_VERSION,
            SAVED_BY_KEY: _program_version(),
            **self.settings,
            "section_tree": self._tree_json(self.top_sections),
        })

    def save_section(self, section: Section) -> None:
        self._check_writable()
        section.data.update({"id": section.id, "type": section.type, "name": section.name})
        if section.type == "data":
            section.data["compound_order"] = [i.id for i in section.items]
        atomic_write_json(section.folder / SECTION_FILE, section.data)

    # ------------------------------------------------------------------ sections

    @property
    def sections(self) -> list[Section]:
        """Every section and sub-section in document order (depth first)."""
        out: list[Section] = []
        for s in self.top_sections:
            out += self.subtree(s)
        return out

    def section(self, section_id: str) -> Section:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(section_id)

    def subtree(self, section: Section) -> list[Section]:
        """The section and all its sub-sections, in document order."""
        out = [section]
        for child in section.children:
            out += self.subtree(child)
        return out

    def parent(self, section: Section) -> Section | None:
        if not section.parent_id:
            return None
        try:
            return self.section(section.parent_id)
        except KeyError:
            return None

    def siblings(self, section: Section) -> list[Section]:
        """The list the section sits in (its parent's sub-sections, or the top level)."""
        parent = self.parent(section)
        return parent.children if parent is not None else self.top_sections

    def depth(self, section: Section) -> int:
        """1 for a top-level section, 2 for a sub-section (1.1), 3 for 1.1.1."""
        n, parent = 1, self.parent(section)
        while parent is not None:
            n, parent = n + 1, self.parent(parent)
        return n

    def ancestors(self, section: Section) -> list[Section]:
        """The sections this one sits in, nearest first."""
        out, parent = [], self.parent(section)
        while parent is not None:
            out.append(parent)
            parent = self.parent(parent)
        return out

    @staticmethod
    def _height(section: Section) -> int:
        """How many levels the section and its sub-sections take up (1 with none)."""
        return 1 + max((Project._height(c) for c in section.children), default=0)

    def check_section_place(self, parent_id: str | None, section: Section | None = None) -> None:
        """Raise ProjectError (in plain words) unless `section` (or a new section, of any
        type) may go inside `parent_id` (None = the top level)."""
        if not parent_id:
            return
        try:
            parent = self.section(parent_id)
        except KeyError:
            raise ProjectError("That section no longer exists.") from None
        if not parent.can_hold_sections:
            raise ProjectError(f"“{parent.name}” is a {parent.type_label} section. Only custom "
                               "sections can hold sub-sections.")
        if section is not None and parent in self.subtree(section):
            raise ProjectError("A section cannot be moved inside itself or one of its own "
                               "sub-sections.")
        levels = self.depth(parent) + (self._height(section) if section is not None else 1)
        if levels > MAX_SECTION_DEPTH:
            what = (f"“{section.name}”" + (" and its sub-sections" if section.children else "")
                    if section is not None else "A new section")
            raise ProjectError(f"{what} would be {levels} levels deep there. Sections can be "
                               f"nested at most {MAX_SECTION_DEPTH} levels deep (1, 1.1, 1.1.1).")

    def can_place_section(self, parent_id: str | None, section: Section | None = None) -> bool:
        try:
            self.check_section_place(parent_id, section)
            return True
        except ProjectError:
            return False

    def add_section(self, type_: str, name: str, index: int | None = None,
                    parent_id: str | None = None) -> Section:
        """A new section at `index` among the top-level sections, or inside the custom
        section `parent_id` (a sub-section)."""
        self._check_writable()
        if type_ not in SECTION_TYPES + (SPECTRA_SECTION,):
            raise ValueError(f"Unknown section type {type_!r}")
        if type_ == SPECTRA_SECTION and self.spectra_section is not None:
            raise ProjectError("The project already has a Spectra section (there is only one).")
        self.check_section_place(parent_id)
        sid = new_id("s")
        folder = self.folder / SECTIONS_DIR / f"{sid}_{slugify(name, SECTION_SLUG)}"
        fileio.make_dirs(folder, exist_ok=False)
        data: dict[str, Any] = {"id": sid, "type": type_, "name": name}
        if type_ == SPECTRA_SECTION:
            data["defaults"] = spectra_mod.section_defaults(None, self.settings)
        elif type_ == "file":
            data.update({"file": None, "heading": "", "caption": ""})
        elif type_ == "data":
            data.update({"id_table": [], "default_fields": copy.deepcopy(F.DEFAULT_SECTION_FIELDS),
                         "compound_order": []})
        section = Section(sid, type_, name, folder, data, parent_id=parent_id or None)
        self.save_section(section)
        if type_ == "text":
            atomic_write_text(folder / TEXT_CONTENT_FILE, "")
        container = self.section(parent_id).children if parent_id else self.top_sections
        container.insert(len(container) if index is None
                         else max(0, min(index, len(container))), section)
        self.save_settings()
        return section

    def rename_section(self, section_id: str, name: str) -> None:
        # The folder keeps its original slug: renaming folders breaks sync and paths.
        name = name.strip()
        if not name:
            raise ProjectError("Section names cannot be empty.")
        section = self.section(section_id)
        section.name = name
        self.save_section(section)

    def set_order(self, section_ids: list[str], parent_id: str | None = None) -> None:
        """Reorder the top-level sections (or the sub-sections of `parent_id`)."""
        self._check_writable()
        container = self.section(parent_id).children if parent_id else self.top_sections
        by_id = {s.id: s for s in container}
        if sorted(section_ids) != sorted(by_id):
            raise ValueError("New order must contain exactly the existing sections.")
        container[:] = [by_id[sid] for sid in section_ids]
        self.save_settings()

    _SAME_PARENT = object()

    def move_section(self, section_id: str, new_index: int, parent_id=_SAME_PARENT) -> None:
        """Move a section (with its sub-sections) to `new_index` among its siblings, or
        into another custom section (`parent_id`; None = the top level). Only project.json
        changes: section folders never move."""
        self._check_writable()
        section = self.section(section_id)
        if parent_id is self._SAME_PARENT:
            parent_id = section.parent_id
        parent_id = parent_id or None
        self.check_section_place(parent_id, section)
        self.siblings(section).remove(section)
        container = self.section(parent_id).children if parent_id else self.top_sections
        container.insert(max(0, min(new_index, len(container))), section)
        section.parent_id = parent_id
        self.save_settings()

    # ------------------------------------------------------------------ the Spectra section

    @property
    def spectra_section(self) -> Section | None:
        """The Spectra section (SPEC §13k.12), or None in a project that has none yet (a
        project made before it existed, opened read-only)."""
        return next((s for s in self.sections if s.type == SPECTRA_SECTION), None)

    def ensure_spectra_section(self) -> Section | None:
        """Add the Spectra section at the end of the tree if the project has none (new
        projects, and older ones when opened for editing). Its defaults start from the
        older settings "spectra_per_page" and "structure_corner", which are then removed
        from project.json. Returns the section (None if read-only and there is none)."""
        existing = self.spectra_section
        if existing is not None or self.read_only:
            return existing
        section = self.add_section(SPECTRA_SECTION, spectra_mod.DEFAULT_NAME)
        for key in spectra_mod.LEGACY_KEYS:
            self.settings.pop(key, None)
        self.save_settings()
        return section

    def spectra_defaults(self) -> dict[str, Any]:
        """The spectra layout defaults in force (core/spectra.py DEFAULTS, filled in)."""
        return spectra_mod.section_defaults(self.spectra_section, self.settings)

    def set_spectra_defaults(self, values: dict[str, Any]) -> None:
        """Store the Spectra section's defaults (unusable values are dropped)."""
        self._check_writable()
        section = self.spectra_section
        if section is None:
            raise ProjectError("This project has no Spectra section yet.")
        section.data["defaults"] = {**spectra_mod.DEFAULTS, **spectra_mod.clean(values)}
        self.save_section(section)

    def set_spectrum_overrides(self, compound: Compound, field_key: str, file_name: str,
                               values: dict[str, Any], page: str | None = None) -> None:
        """Replace one spectrum file's own settings (`page` None: for all its pages), or
        one page's own settings and position. Empty `values` removes them."""
        self._check_writable()
        item = next((i for i in compound.values.get(field_key) or []
                     if isinstance(i, dict) and i.get("file") == file_name), None)
        if item is None:
            raise ProjectError(f"{file_name} is no longer attached to this compound.")
        if page is None:
            cleaned = spectra_mod.clean(values, spectrum=True)
            if cleaned:
                item["layout"] = cleaned
            else:
                item.pop("layout", None)
        else:
            cleaned = spectra_mod.clean(values, positions=True)
            pages = item.get("pages") if isinstance(item.get("pages"), dict) else {}
            if cleaned:
                pages[str(page)] = cleaned
            else:
                pages.pop(str(page), None)
            if pages:
                item["pages"] = pages
            else:
                item.pop("pages", None)
        self.save_compound(compound)

    def set_spectra_order(self, compound: Compound, order: list | None) -> None:
        """The order a compound's spectrum files are printed in, set by hand: a list of
        (field key, file name); None or [] = the automatic order (by type)."""
        self._check_writable()
        if order:
            compound.data[spectra_mod.ORDER_KEY] = [[str(k), str(f)] for k, f in order]
        else:
            compound.data.pop(spectra_mod.ORDER_KEY, None)
        self.save_compound(compound)

    def set_spectrum_text(self, compound: Compound, field_key: str, file_name: str,
                          key: str, text: str) -> None:
        """The header ("title") or the caption under the structure ("struct_caption") typed
        for one spectrum file; empty = automatic."""
        self._check_writable()
        if key not in spectra_mod.TEXT_KEYS:
            raise ValueError(key)
        item = next((i for i in compound.values.get(field_key) or []
                     if isinstance(i, dict) and i.get("file") == file_name), None)
        if item is None:
            raise ProjectError(f"{file_name} is no longer attached to this compound.")
        text = text.strip()
        if text:
            item[key] = text
        elif key in item:
            item[key] = ""   # (the Spectra field keeps its parts; empty = automatic)
        self.save_compound(compound)

    # ------------------------------------------------------------------ text sections

    def read_section_text(self, section: Section) -> str:
        return read_text(section.folder / TEXT_CONTENT_FILE)

    def write_section_text(self, section: Section, text: str) -> None:
        self._check_writable()
        atomic_write_text(section.folder / TEXT_CONTENT_FILE, text)

    # ------------------------------------------------------------------ file sections

    def section_file_path(self, section: Section) -> Path | None:
        name = section.data.get("file")
        return section.folder / name if name else None

    def is_section_file(self, section: Section, path: Path) -> bool:
        """Whether `path` is the file already attached to the section (e.g. copied from the
        project folder in File Explorer and pasted back)."""
        current = self.section_file_path(section)
        if current is None or not current.exists():
            return False
        try:
            return os.path.samefile(path, current)
        except OSError:
            return Path(path).resolve() == current.resolve()

    def attach_file(self, section: Section, source: Path, vector: Path | None = None) -> Path:
        """Copy `source` into the section (replacing any current file, which goes to Trash).
        `vector`: a pasted picture's ChemDraw original, kept next to it (a restore). The
        section's own file attached again changes nothing. The new file is copied in before
        the old one goes to the Trash, so a copy that fails leaves the section as it was."""
        self._check_writable()
        source = Path(source)
        if source.suffix.lower() not in FILE_SECTION_EXTENSIONS:
            raise ProjectError(
                f"{source.name} is not a supported file type.\n"
                f"Please use a PDF or an image ({', '.join(FILE_SECTION_EXTENSIONS)}).")
        if not source.is_file():
            raise ProjectError(f"File not found:\n{source}")
        if self.is_section_file(section, source):
            return self.section_file_path(section)   # (already attached: nothing to do)
        if vector is not None and source.suffix.lower() in (".png", ".svg"):
            if section.data.get("file"):
                self.remove_file(section)
            dest = self._write_picture(section.folder, Path(safe_filename(source.name)).stem,
                                       source, None, source.suffix.lower(), vector, "")
        else:
            # Copied in under a passing name first; the final name is chosen once the old
            # file has gone to the Trash (so a file of the same name keeps its name).
            incoming = _unique_path(section.folder / f".incoming{source.suffix.lower()}")
            fileio.copy_file(source, incoming)
            try:
                if section.data.get("file"):
                    self.remove_file(section)
            except BaseException:
                fileio.remove_file(incoming)
                raise
            dest = _unique_path(section.folder / safe_filename(source.name),
                                reserved={SECTION_FILE})
            fileio.move(incoming, dest)
        section.data["file"] = dest.name
        self.save_section(section)
        return dest

    def attach_pasted_picture(self, section: Section, png: bytes, vector: bytes | None = None,
                              vector_ext: str = "", ext: str = ".png") -> Path:
        """A picture pasted into an Attachment section (SPEC §13l.8), e.g. a ChemDraw drawing
        converted to SVG or rendered at the project's paste resolution: stored as "pasted
        picture.svg" (or .png), with ChemDraw's vector original next to it (same name, .emf
        or .pdf) as for structures. Any current file goes to the Trash."""
        self._check_writable()
        ext = ext.lower()
        if ext not in FILE_SECTION_EXTENSIONS:
            raise ProjectError(f"A pasted picture can't be stored as {ext}.")
        if section.data.get("file"):
            self.remove_file(section)
        dest = self._write_picture(section.folder, PASTED_ATTACHMENT_STEM, None, png, ext,
                                   vector, vector_ext)
        section.data["file"] = dest.name
        self.save_section(section)
        return dest

    def section_file_source(self, section: Section) -> Path | None:
        """The vector original kept next to a picture pasted into an Attachment section."""
        path = self.section_file_path(section)
        if path is None or path.suffix.lower() not in (".png", ".svg"):
            return None
        return picture_source_path(path)

    def remove_file(self, section: Section) -> None:
        self._check_writable()
        path = self.section_file_path(section)
        original = self.section_file_source(section)
        section.data["file"] = None
        self.save_section(section)
        if path is not None and path.exists():
            self._move_to_trash(path, kind="file", name=path.name,
                                extra={"section_id": section.id, "section_name": section.name},
                                companions=_present(original))

    # ------------------------------------------------------------------ trash

    @property
    def trash_dir(self) -> Path:
        return self.folder / TRASH_DIR

    def delete_section(self, section_id: str) -> TrashEntry:
        """Move a section, with all its sub-sections, to the project Trash as one item
        (SPEC §5). It can be restored."""
        self._check_writable()
        section = self.section(section_id)
        held = [s for s in self.subtree(section) if s.type == SPECTRA_SECTION]
        if held:
            raise ProjectError(SPECTRA_NOT_DELETED if held[0] is section else
                               f"“{section.name}” holds the Spectra section, which cannot be "
                               "deleted. Move the Spectra section out of it first (right-click ▸ "
                               "Move out to the top level).")
        container = self.siblings(section)
        index = container.index(section)
        below = self.subtree(section)[1:]
        entry = self._move_to_trash(section.folder, kind="section", name=section.name,
                                    extra={"section_id": section.id, "type": section.type,
                                           "original_index": index,
                                           "parent_id": section.parent_id or "",
                                           "tree": self._tree_json([section])[0],
                                           "subsections": len(below)},
                                    companions=tuple(s.folder for s in below))
        container.remove(section)
        self.save_settings()
        return entry

    def _move_to_trash(self, path: Path, kind: str, name: str, extra: dict,
                       companions: tuple[Path, ...] = ()) -> TrashEntry:
        """Move `path` (and any `companions`, e.g. a picture's ChemDraw original or a
        section's sub-section folders) into one new Trash entry.

        All or nothing: each item is renamed into the Trash (never copied and then deleted,
        which can stop half-way), and if any of them cannot be moved — typically a file
        open in another program or being synced — the ones already moved are put back and
        nothing is deleted. The entry's description is written first, marked "pending", so
        even a crash part-way leaves something the Trash can show and restore."""
        entry_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + new_id("t")[2:]
        entry_folder = self.trash_dir / entry_id
        fileio.make_dirs(entry_folder, exist_ok=False)
        others = [p for p in companions if p.exists()]
        user = self.lock.user if self.lock else ""
        info = {"kind": kind, "name": name, "item": path.name,
                "deleted_at": datetime.now().isoformat(timespec="seconds"),
                "deleted_by": user, **extra}
        if others:
            info["companions"] = [p.name for p in others]
        atomic_write_json(entry_folder / TRASH_ENTRY_FILE, {**info, "pending": True})
        done: list[tuple[Path, Path]] = []
        try:
            for source in [path, *others]:
                target = entry_folder / source.name
                fileio.rename(source, target)   # same disk (both inside the Project Folder)
                done.append((source, target))
        except OSError as e:
            for source, target in reversed(done):   # put back what was already moved
                try:
                    fileio.rename(target, source)
                except OSError:
                    pass
            if not any(target.exists() for _source, target in done):
                fileio.remove_tree(entry_folder, ignore_errors=True)
            blocked = getattr(e, "filename", None) or path
            raise ProjectError(
                f"“{name}” could not be moved to the Trash, so nothing was deleted.\n\n"
                f"A file in it is probably open in another program (e.g. a PDF viewer) or "
                f"being synced. Close it and try again.\n\n({Path(blocked).name}: "
                f"{e.strerror or e})") from e
        atomic_write_json(entry_folder / TRASH_ENTRY_FILE, info)
        return TrashEntry(entry_id, kind, name, entry_folder, info)

    def list_trash(self) -> list[TrashEntry]:
        entries = []
        if self.trash_dir.is_dir():
            for sub in sorted(self.trash_dir.iterdir(), reverse=True):  # newest first
                if not sub.is_dir():
                    continue
                try:
                    info = read_json(sub / TRASH_ENTRY_FILE)
                except ValueError:
                    info = None
                if isinstance(info, dict):
                    name = info.get("name", sub.name)
                    if info.get("pending"):   # the deletion stopped part-way (e.g. a crash)
                        name = f"{name} (deletion did not finish)"
                    entries.append(TrashEntry(sub.name, info.get("kind", "?"), name, sub, info))
                elif any(sub.iterdir()):
                    # Files with no description (e.g. from a crash in an older version):
                    # still listed, so they are not silently lost.
                    entries.append(TrashEntry(sub.name, "incomplete",
                                              "Unfinished deletion: " + ", ".join(
                                                  sorted(p.name for p in sub.iterdir())),
                                              sub, {"item": ""}))
        return entries

    def restore_from_trash(self, entry_id: str) -> str:
        """Put a trashed item back. Returns a short message for the user."""
        self._check_writable()
        entry = next((e for e in self.list_trash() if e.id == entry_id), None)
        if entry is None:
            raise ProjectError("That item is no longer in the Trash.")
        item = entry.folder / entry.info["item"]
        if entry.kind == "section":
            message = self._restore_section(entry, item)
        elif entry.kind == "file":
            try:
                section = self.section(entry.info.get("section_id", ""))
            except KeyError:
                raise ProjectError(
                    "The section this file belonged to no longer exists. "
                    f"The file is still in the Trash folder:\n{item}") from None
            # (copies, with a pasted picture's original; any current file goes to Trash)
            self.attach_file(section, item, vector=_trash_companion(entry))
            message = f"Restored {item.name} to “{section.name}”."
        elif entry.kind == "compound":
            message = self._restore_compound(entry, item)
        elif entry.kind == "compound_file":
            message = self._restore_compound_file(entry, item)
        elif entry.kind == "scheme":
            message = self._restore_scheme(entry, item)
        elif entry.kind == "scheme_file":
            message = self._restore_scheme_file(entry, item)
        elif entry.kind == "scheme_box":
            from . import scheme_editor
            message = scheme_editor.restore_box(self, entry)
        elif entry.kind == "gp":
            message = self._restore_gp(entry, item)
        elif entry.kind in ("figure", "table"):
            message = self._restore_object(entry, item)
        elif entry.kind == "manuscript_file":   # (the Manuscript check's files, §13o)
            from . import manuscript
            message = manuscript.restore_file(self, entry, item)
        elif entry.kind == "incomplete":
            message = self._restore_incomplete(entry)
        else:
            raise ProjectError(f"Unknown kind of Trash item: {entry.kind}")
        fileio.remove_tree(entry.folder, ignore_errors=True)
        if entry.kind in ("section", "incomplete"):
            try:   # (a section deleted by an older version may still hold !image lines)
                self.upgrade_all_pictures()
            except (ProjectError, OSError, KeyError):
                pass   # older pictures still show and print; they are upgraded next time
        return message

    def empty_trash(self) -> None:
        self._check_writable()
        if self.trash_dir.exists():
            fileio.remove_tree(self.trash_dir)

    def _restore_section(self, entry: TrashEntry, item: Path) -> str:
        """Put a section back where it was, with the sub-sections deleted with it."""
        parent_dir = self.folder / SECTIONS_DIR
        names = [item.name] + list(entry.info.get("companions", []))
        # (A deletion that stopped part-way holds only some of them; the rest never left.)
        names = [n for n in names if (entry.folder / n).exists()]
        if not names:
            raise ProjectError("This Trash item is empty: there is nothing to restore.")
        for name in names:
            if (parent_dir / name).exists():
                raise ProjectError(f"Cannot restore: {name} already exists.")
        fileio.make_dirs(parent_dir)
        found: dict[str, Section] = {}
        for name in names:
            source = entry.folder / name
            if not source.exists():
                continue
            fileio.move(source, parent_dir / name)
            data = read_json(parent_dir / name / SECTION_FILE, {})
            if name == item.name:
                data.setdefault("id", entry.info.get("section_id"))
            section = self._make_section(parent_dir / name, data)
            found[section.id] = section
        root_id = entry.info.get("section_id") or next(iter(found), "")
        tree = entry.info.get("tree") or {"id": root_id}
        parent_id = entry.info.get("parent_id") or None
        note = ""
        if parent_id and not self.can_place_section(parent_id):
            parent_id = None
            note = " Its parent section no longer exists, so it was put at the top level."
        container = self.section(parent_id).children if parent_id else self.top_sections
        depth = self.depth(self.section(parent_id)) + 1 if parent_id else 1
        placed = self._place_tree([tree], found, depth, self.section(parent_id)
                                  if parent_id else None)
        for leftover in found.values():   # (not in the saved tree: keep it next to the rest)
            leftover.parent_id = parent_id
            placed.append(leftover)
        index = int(entry.info.get("original_index", len(container)))
        index = len(container) if note else max(0, min(index, len(container)))
        container[index:index] = placed
        self.save_settings()
        name = placed[0].name if placed else entry.name
        subs = len(names) - 1
        return (f"Restored section “{name}”" + (f" with {subs} sub-section(s)" if subs else "")
                + "." + note)

    def _restore_incomplete(self, entry: TrashEntry) -> str:
        """A Trash folder with no description: put back any section folders it holds (at
        the top level); anything else is left for the user to look at."""
        parent_dir = self.folder / SECTIONS_DIR
        contents = sorted(entry.folder.iterdir())
        sections = [p for p in contents if p.is_dir() and (p / SECTION_FILE).is_file()]
        others = [p for p in contents if p not in sections and p.name != TRASH_ENTRY_FILE]
        if others or not sections:
            raise ProjectError(
                "The program cannot tell where these files belong, so they were left in the "
                f"Trash folder for you to move by hand:\n{entry.folder}")
        known = {s.id for s in self.sections}
        for p in sections:
            sid = read_json(p / SECTION_FILE, {}).get("id") or p.name.split("_", 1)[0]
            if (parent_dir / p.name).exists() or sid in known:
                raise ProjectError(f"Cannot restore: {p.name} is already in the project.")
        fileio.make_dirs(parent_dir)
        names = []
        for p in sections:
            fileio.rename(p, parent_dir / p.name)
            section = self._make_section(parent_dir / p.name,
                                         read_json(parent_dir / p.name / SECTION_FILE, {}))
            section.parent_id = None
            self.top_sections.append(section)
            names.append(section.name)
        self.save_settings()
        return ("Restored " + ", ".join(f"“{n}”" for n in names) + " at the end of the "
                "section list." if names else "Restored the files.")

    # ------------------------------------------------------------------ compounds

    def data_sections(self) -> list[Section]:
        return [s for s in self.sections if s.type == "data"]

    def all_compounds(self) -> list[Compound]:
        return [c for s in self.data_sections() for c in s.compounds]

    def compound(self, compound_id: str) -> Compound:
        for s in self.data_sections():
            for c in s.compounds:
                if c.id == compound_id:
                    return c
        raise KeyError(compound_id)

    def _data_section(self, section_id: str) -> Section:
        section = self.section(section_id)
        if section.type != "data":
            raise ProjectError(f"“{section.name}” is not a Data section.")
        return section

    def _container(self, section: Section, scheme_id: str | None):
        """(list the compound sits in, folder its compound folders live in, owner to save)."""
        if scheme_id:
            scheme = self.scheme(scheme_id)
            if scheme.section_id != section.id:
                raise ProjectError("That scheme page is in a different section.")
            return scheme.children, section.folder / COMPOUNDS_DIR, scheme
        return section.items, section.folder / COMPOUNDS_DIR, section

    def _save_owner(self, owner) -> None:
        if isinstance(owner, SchemePage):
            self.save_scheme(owner)
        else:
            self.save_section(owner)

    def position(self, compound: Compound) -> tuple[str, str | None, int]:
        """(section id, scheme id or None, index within the section or scheme)."""
        section = self.section(compound.section_id)
        container, _folder, _owner = self._container(section, compound.scheme_id)
        return section.id, compound.scheme_id, container.index(compound)

    def default_nmr_reports(self) -> list[dict[str, str]]:
        return [{"id": new_id("r"), "nucleus": n, "label": "", "text": ""}
                for n in F.DEFAULT_NMR_NUCLEI]

    def add_compound(self, section_id: str, index: int | None = None, name: str = "",
                     scheme_id: str | None = None) -> Compound:
        """New compound in a Data section, or inside one of its scheme pages."""
        self._check_writable()
        section = self._data_section(section_id)
        container, parent, owner = self._container(section, scheme_id)
        cid = new_id("c")
        folder = parent / f"{cid}_{slugify(name or 'compound', ITEM_SLUG)}"
        fileio.make_dirs(folder, exist_ok=False)
        compound = Compound(cid, folder, {"id": cid, "id_override": "", "fields": [],
                                          "values": {}, "flags": {}}, section.id, scheme_id)
        for entry in section.default_fields:
            self._add_field_entry(compound, entry)
        if name:
            compound.values["name"] = {"text": name}
        self.save_compound(compound)
        container.insert(len(container) if index is None else max(0, min(index, len(container))),
                         compound)
        self._save_owner(owner)
        return compound

    @staticmethod
    def ids_without_pages(section: Section) -> list[str]:
        """The ID table's entries after the last compound's row, up to its last filled-in
        row ("" for an empty row in between): the IDs no compound has yet (SPEC §13q.4)."""
        table = [str(v).strip() for v in section.id_table]
        while table and not table[-1]:
            table.pop()
        return table[len(section.compounds):]

    def add_pages_for_ids(self, section_id: str) -> list[Compound]:
        """A blank compound page, at the end of the section, for each ID of the ID table
        that no compound has yet (they take those IDs, in the table's order)."""
        section = self._data_section(section_id)
        return [self.add_compound(section.id) for _id in self.ids_without_pages(section)]

    def save_compound(self, compound: Compound) -> None:
        self._check_writable()
        compound.data["id"] = compound.id
        atomic_write_json(compound.folder / COMPOUND_FILE, compound.data)

    def delete_compound(self, compound_id: str) -> TrashEntry:
        self._check_writable()
        compound = self.compound(compound_id)
        section = self.section(compound.section_id)
        container, _parent, owner = self._container(section, compound.scheme_id)
        index = container.index(compound)
        if isinstance(owner, SchemePage):
            # The scheme editor's box that showed it keeps a copy of its structure.
            from . import scheme_editor
            scheme_editor.compound_leaving(self, owner, compound.id)
        entry = self._move_to_trash(
            compound.folder, kind="compound", name=compound.name or "unnamed compound",
            extra={"compound_id": compound.id, "section_id": section.id,
                   "section_name": section.name, "scheme_id": compound.scheme_id or "",
                   "original_index": index})
        container.remove(compound)
        self._save_owner(owner)
        return entry

    def move_compound(self, compound_id: str, section_id: str, index: int,
                      scheme_id: str | None = None) -> None:
        """Move a compound to position `index` of a Data section, or of a scheme page in it
        (`scheme_id`). Works within and between sections and scheme pages."""
        self._check_writable()
        compound = self.compound(compound_id)
        source_section = self.section(compound.section_id)
        src, src_parent, src_owner = self._container(source_section, compound.scheme_id)
        target_section = self._data_section(section_id)
        dst, dst_parent, dst_owner = self._container(target_section, scheme_id)
        from . import scheme_editor
        if isinstance(src_owner, SchemePage) and src_owner is not dst_owner:
            # (leaving a scheme: the box that showed it keeps a copy of its structure)
            scheme_editor.compound_leaving(self, src_owner, compound.id)
        src.remove(compound)
        if dst_parent != src_parent:
            dest = _unique_path(dst_parent / compound.folder.name)
            fileio.make_dirs(dest.parent)
            try:
                fileio.move(compound.folder, dest)
            except OSError:
                src.append(compound)  # put it back in memory; nothing moved
                raise
            compound.folder = dest
        compound.section_id = target_section.id
        compound.scheme_id = scheme_id or None
        dst.insert(max(0, min(index, len(dst))), compound)
        if isinstance(dst_owner, SchemePage):
            # An editor scheme's compounds stay in the order of their boxes.
            scheme_editor.sync_children(self, dst_owner, save=False)
        if src_owner is not dst_owner:
            self._save_owner(src_owner)
        self._save_owner(dst_owner)

    def _restore_compound(self, entry: TrashEntry, item: Path) -> str:
        try:
            section = self._data_section(entry.info.get("section_id", ""))
        except (KeyError, ProjectError):
            raise ProjectError(
                f"The Data section “{entry.info.get('section_name', '?')}” this compound "
                "belonged to no longer exists. Restore that section first.") from None
        scheme_id = entry.info.get("scheme_id") or None
        if scheme_id and not any(s.id == scheme_id for s in section.schemes):
            scheme_id = None  # its scheme page is gone: restore it as an ordinary compound
        container, parent, owner = self._container(section, scheme_id)
        dest = parent / item.name
        if dest.exists():
            raise ProjectError(f"Cannot restore: {dest.name} already exists.")
        fileio.make_dirs(dest.parent)
        fileio.move(item, dest)
        data = read_json(dest / COMPOUND_FILE, {})
        compound = Compound(data.get("id", entry.info.get("compound_id")), dest, data,
                            section.id, scheme_id)
        index = int(entry.info.get("original_index", len(container)))
        container.insert(max(0, min(index, len(container))), compound)
        if isinstance(owner, SchemePage):
            # (a scheme made with the editor orders its compounds as its boxes, SPEC §13n.4)
            from . import scheme_editor
            scheme_editor.sync_children(self, owner, save=False)
        self._save_owner(owner)
        return f"Restored compound “{entry.name}” to “{section.name}”."

    # ------------------------------------------------------------------ scheme pages

    def all_schemes(self) -> list[SchemePage]:
        return [k for s in self.data_sections() for k in s.schemes]

    def scheme(self, scheme_id: str) -> SchemePage:
        for k in self.all_schemes():
            if k.id == scheme_id:
                return k
        raise KeyError(scheme_id)

    def save_scheme(self, scheme: SchemePage) -> None:
        self._check_writable()
        scheme.data.update({"id": scheme.id, "children": [c.id for c in scheme.children]})
        atomic_write_json(scheme.folder / SCHEME_FILE, scheme.data)

    def add_scheme(self, section_id: str, index: int | None = None,
                   name: str = "") -> SchemePage:
        """A new scheme page, drawn with the scheme editor (one empty starting material
        and one step, SPEC §13n.4)."""
        from . import scheme_editor
        self._check_writable()
        section = self._data_section(section_id)
        kid = new_id("k")
        name = name.strip() or f"Scheme {len(section.schemes) + 1}"
        folder = section.folder / COMPOUNDS_DIR / f"{kid}_{slugify(name, ITEM_SLUG)}"
        fileio.make_dirs(folder, exist_ok=False)
        scheme = SchemePage(kid, folder, {"id": kid, "name": name, "children": []},
                            section.id)
        scheme.data[scheme_editor.MODE_KEY] = scheme_editor.EDITOR
        scheme_editor.start_empty(scheme)
        self.save_scheme(scheme)
        items = section.items
        items.insert(len(items) if index is None else max(0, min(index, len(items))), scheme)
        self.save_section(section)
        return scheme

    def rename_scheme(self, scheme_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise ProjectError("Scheme names cannot be empty.")
        scheme = self.scheme(scheme_id)
        scheme.data["name"] = name
        self.save_scheme(scheme)

    def set_scheme_label(self, scheme_id: str, label: str) -> None:
        """Type over the scheme's automatic label ("Scheme S2"); "" = automatic."""
        scheme = self.scheme(scheme_id)
        scheme.data["label"] = label.strip()
        if not scheme.data["label"]:
            del scheme.data["label"]
        self.save_scheme(scheme)

    def delete_scheme(self, scheme_id: str) -> TrashEntry:
        """Move a scheme page, with its compounds, to the Trash."""
        self._check_writable()
        scheme = self.scheme(scheme_id)
        section = self.section(scheme.section_id)
        index = section.items.index(scheme)
        entry = self._move_to_trash(scheme.folder, kind="scheme", name=scheme.name,
                                    extra={"scheme_id": scheme.id, "section_id": section.id,
                                           "section_name": section.name,
                                           "original_index": index,
                                           "children": [c.folder.name for c in scheme.children]})
        for child in scheme.children:  # its compounds go into the same Trash entry
            fileio.move(child.folder, entry.folder / child.folder.name)
        section.items.remove(scheme)
        self.save_section(section)
        return entry

    def _restore_scheme(self, entry: TrashEntry, item: Path) -> str:
        try:
            section = self._data_section(entry.info.get("section_id", ""))
        except (KeyError, ProjectError):
            raise ProjectError(
                f"The Data section “{entry.info.get('section_name', '?')}” this scheme "
                "belonged to no longer exists. Restore that section first.") from None
        parent = section.folder / COMPOUNDS_DIR
        names = [item.name] + list(entry.info.get("children", []))
        for name in names:
            if (parent / name).exists():
                raise ProjectError(f"Cannot restore: {name} already exists.")
        fileio.make_dirs(parent)
        for name in names:
            if (entry.folder / name).exists():
                fileio.move(entry.folder / name, parent / name)
        dest = parent / item.name
        data = read_json(dest / SCHEME_FILE, {})
        data.setdefault("id", entry.info.get("scheme_id"))
        children = {}
        for name in entry.info.get("children", []):
            d = read_json(parent / name / COMPOUND_FILE, {})
            cid = d.get("id") or name.split("_", 1)[0]
            children[cid] = Compound(cid, parent / name, d, section.id)
        scheme = self._make_scheme(section, dest, data, children)
        index = int(entry.info.get("original_index", len(section.items)))
        section.items.insert(max(0, min(index, len(section.items))), scheme)
        self.save_section(section)
        return f"Restored scheme “{scheme.name}” to “{section.name}”."

    def move_scheme(self, scheme_id: str, section_id: str, index: int) -> None:
        self._check_writable()
        scheme = self.scheme(scheme_id)
        source = self.section(scheme.section_id)
        target = self._data_section(section_id)
        source.items.remove(scheme)
        if target is not source:
            parent = target.folder / COMPOUNDS_DIR
            fileio.make_dirs(parent)
            moved: list[tuple[object, Path]] = []
            try:
                for obj in [scheme, *scheme.children]:
                    dest = _unique_path(parent / obj.folder.name)
                    fileio.move(obj.folder, dest)
                    moved.append((obj, obj.folder))
                    obj.folder = dest
            except OSError:
                for obj, old in reversed(moved):  # undo, so nothing is left half-moved
                    fileio.move(obj.folder, old)
                    obj.folder = old
                source.items.append(scheme)
                raise
            scheme.section_id = target.id
            for child in scheme.children:
                child.section_id = target.id
            self.save_section(source)
        target.items.insert(max(0, min(index, len(target.items))), scheme)
        self.save_section(target)

    def convert_to_scheme(self, compound_id: str) -> SchemePage:
        """Turn a compound page into a scheme page with the compound as its first sub-page
        (SPEC §6.3.5). A scheme picture the compound had (older projects) goes to the
        Trash: schemes are drawn with the scheme editor (SPEC §13p.5)."""
        self._check_writable()
        compound = self.compound(compound_id)
        if compound.scheme_id:
            raise ProjectError("This compound is already part of a scheme page.")
        from . import scheme_editor
        section_id, _scheme, index = self.position(compound)
        name = f"Scheme: {compound.name}" if compound.name else ""
        scheme = self.add_scheme(section_id, index, name)
        self.move_compound(compound.id, section_id, 0, scheme.id)
        # Drawn with the scheme editor: the compound is the product of its first step.
        scheme_editor.start_with_compound(self, scheme, compound)
        legacy = self._image_path(compound, "scheme_image")
        if legacy is not None and legacy.exists():
            self._remove_image(compound, "scheme_image")
        if "scheme" in compound.field_keys:
            compound.data["fields"] = [e for e in compound.fields if e["key"] != "scheme"]
            self.save_compound(compound)
        return scheme

    def _restore_scheme_file(self, entry: TrashEntry, item: Path) -> str:
        # (a pasted scheme picture, from before schemes were drawn only with the scheme
        # editor, SPEC §13p.5: there is nowhere to put it back)
        raise ProjectError("This is a pasted scheme picture. Schemes are now drawn with the "
                           "scheme editor, so it can't be put back on a scheme page. The file "
                           f"is still in the Trash folder:\n{item}")

    # ---- pictures shared by structures and schemes

    @staticmethod
    def _check_picture(source: Path | None, ext: str,
                       allowed: tuple[str, ...] = STRUCTURE_IMAGE_EXTENSIONS
                       ) -> tuple[Path | None, str]:
        if source is not None:
            source = Path(source)
            ext = source.suffix.lower()
            if not source.is_file():
                raise ProjectError(f"File not found:\n{source}")
        if ext not in allowed:
            raise ProjectError(f"{(source.name if source else 'This')} is not a supported image "
                               f"({', '.join(allowed)}).")
        return source, ext

    @staticmethod
    def _write_picture(folder: Path, stem: str, source: Path | None, data: bytes | None,
                       ext: str, vector: bytes | Path | None, vector_ext: str) -> Path:
        """Write a picture (and its vector original, if any) into `folder`; returns it."""
        dest = _unique_picture_path(folder, stem, ext)
        if source is not None:
            fileio.copy_file(source, dest)
        else:
            atomic_write_bytes(dest, data or b"")
        if isinstance(vector, Path):
            vector_ext = vector.suffix
        vector_ext = vector_ext.lower()
        if vector and vector_ext in VECTOR_EXTENSIONS:
            target = dest.with_suffix(vector_ext)
            if isinstance(vector, Path):
                fileio.copy_file(vector, target)
            else:
                atomic_write_bytes(target, vector)
        return dest

    def pasted_pictures(self) -> list[tuple[Path, Path | None]]:
        """Every structure and scheme picture in the project, each with its ChemDraw
        original (or None), for "Re-render pasted pictures"."""
        return [(path, source) for path, source, _moved in self._picture_slots()]

    def _picture_slots(self):
        """(picture, its original or None, moved(new path)) for every structure, scheme,
        figure and pasted Attachment picture; `moved` makes whatever refers to the picture
        refer to `new` (a re-rendered picture may change its extension)."""
        for compound in self.all_compounds():
            for slot in self._IMAGE_SLOTS:
                path = self._image_path(compound, slot)
                if path is not None and path.exists():
                    yield (path, picture_source_path(path),
                           lambda new, c=compound, k=slot: self._image_moved(c, k, new))
        for scheme in self.all_schemes():
            from . import scheme_editor   # the scheme editor's structures (SPEC §13n.4)
            yield from scheme_editor.picture_slots(self, scheme)
        for owner in self.text_owners():   # figures in custom sections / General Information
            for figure_id, entry in self.figures(owner).items():
                path = self.figure_path(owner, figure_id)
                if path is not None and path.exists():
                    def figure_moved(new, o=owner, f=figure_id, e=entry):
                        e["file"] = new.relative_to(o.folder).as_posix()
                        self._set_figure_entry(o, f, e)
                    yield path, self.figure_source(owner, figure_id), figure_moved
        for section in self.sections:   # pictures pasted into Attachment sections
            source = self.section_file_source(section) if section.type == "file" else None
            if source is not None:     # (attached picture files are the user's own: left out)
                def file_moved(new, sec=section):
                    sec.data["file"] = new.name
                    self.save_section(sec)
                yield self.section_file_path(section), source, file_moved

    def replace_pasted_picture(self, path: Path, data: bytes, ext: str) -> Path:
        """Store a pasted picture drawn again from its original ("Re-render pasted
        pictures"): in place, or under the same name with another extension (e.g. a PNG
        that is now an SVG, SPEC §13n.1), whatever referred to it following. A structure
        picture's bond length is measured again. Returns the picture's path."""
        self._check_writable()
        path = Path(path)
        ext = ext.lower()
        slot = next(((p, s, moved) for p, s, moved in self._picture_slots()
                     if p == path), None)
        if slot is None or ext not in PICTURE_FILE_EXTENSIONS:
            raise ProjectError(f"Not a pasted picture in this project: {path.name}")
        new = path.with_suffix(ext)
        if new != path and (new.exists() or new == slot[1]):
            raise ProjectError(f"{new.name} is already in the project; {path.name} was left "
                               "as it is.")
        atomic_write_bytes(new, data)
        slot[2](new)
        if new != path and path.exists():
            fileio.remove_file(path)
        return new

    def _image_moved(self, compound: Compound, slot: str, new: Path) -> None:
        value_key, entry, _stem = self._IMAGE_SLOTS[slot]
        value = compound.values.get(value_key) or {}
        value[entry] = new.name
        compound.values[value_key] = value
        if slot == "structure_image":
            reference = None
            if value.get("image_source") == "chemdraw":
                reference = drawing_scale.reference_from_mol(self.read_structure(compound))
            self._measure_structure_picture(compound, new, reference, keep_own=True)
        self.save_compound(compound)

    def replace_picture_png(self, path: Path, png: bytes) -> None:
        # (kept for PNG-only callers; replace_pasted_picture also handles SVG pictures)
        """Overwrite a stored PNG picture in place (e.g. re-rendered at a new resolution)."""
        self._check_writable()
        path = Path(path)
        if path.suffix.lower() != ".png" or not path.resolve().is_relative_to(
                self.folder.resolve()):
            raise ProjectError(f"Not a picture in this project: {path.name}")
        atomic_write_bytes(path, png)

    # ---- ID table and default fields

    def set_id_table(self, section_id: str, ids: list[str]) -> None:
        section = self._data_section(section_id)
        section.data["id_table"] = [str(i).strip() for i in ids]
        self.save_section(section)

    def set_default_fields(self, section_id: str, entries: list[dict[str, Any]]) -> None:
        section = self._data_section(section_id)
        section.data["default_fields"] = copy.deepcopy(entries)
        self.save_section(section)

    def set_dashboard(self, section_id: str, columns: list[str] | None = None,
                      filter_mode: str | None = None, split_nuclei: bool | None = None) -> None:
        """Remember the dashboard's columns, filter and split-by-nucleus choice (SPEC §6.3.4)."""
        section = self._data_section(section_id)
        config = section.data.setdefault("dashboard", {})
        if columns is not None:
            config["columns"] = list(columns)
        if filter_mode is not None:
            config["filter"] = filter_mode
        if split_nuclei is not None:
            config["split_nuclei"] = split_nuclei
        self.save_section(section)

    def apply_default_fields(self, section_id: str, remove_others: bool = False) -> int:
        """Give every compound in the section the default fields, in the default order.

        Fields a compound has that are not defaults are kept at the end (or
        removed if `remove_others`; their values are kept). Returns how many
        compounds changed.
        """
        section = self._data_section(section_id)
        default_keys = [e["key"] for e in section.default_fields]
        changed = 0
        for compound in section.compounds:
            before = copy.deepcopy(compound.fields)
            for entry in section.default_fields:
                if entry["key"] not in compound.field_keys:
                    self._add_field_entry(compound, entry)
            by_key = {e["key"]: e for e in compound.fields}
            extra = [e for e in compound.fields
                     if e["key"] not in default_keys and e["key"] != "notes"]
            new = [by_key[k] for k in default_keys if k in by_key]
            if not remove_others:
                new += extra
            if "notes" in by_key:
                new.append(by_key["notes"])
            compound.data["fields"] = new
            if new != before:
                self.save_compound(compound)
                changed += 1
        return changed

    # ---- fields on one compound

    def _add_field_entry(self, compound: Compound, entry: dict[str, Any]) -> None:
        """Add a field (no save). Re-adding a removed field restores its old value."""
        defn = F.field_def(entry)
        if defn is None or defn.key in compound.field_keys:
            return
        new_entry = {"key": defn.key}
        if defn.custom:
            new_entry["custom"] = dict(defn.custom)
        fields = compound.fields
        # The reaction scheme always goes first; notes always stay last.
        if defn.key == "scheme":
            pos = 0
        else:
            pos = len(fields) - 1 if fields and fields[-1]["key"] == "notes" else len(fields)
        fields.insert(pos, new_entry)
        if defn.key not in compound.values:
            if defn.kind == "nmr":
                compound.values[defn.key] = self.default_nmr_reports()
            else:
                empty = F.empty_value(defn)
                if empty is not None:
                    compound.values[defn.key] = empty
        if "notes" not in compound.field_keys:
            fields.append({"key": "notes"})

    def add_field(self, compound: Compound, entry: dict[str, Any]) -> None:
        self._check_writable()
        self._add_field_entry(compound, entry)
        self.save_compound(compound)

    def remove_field(self, compound: Compound, key: str) -> None:
        self._check_writable()
        defn = F.field_def({"key": key}) or None
        if defn is not None and not defn.removable:
            raise ProjectError(f"The {defn.label} field cannot be removed.")
        compound.data["fields"] = [e for e in compound.fields if e["key"] != key]
        self.save_compound(compound)

    def set_flag(self, compound: Compound, key: str, flagged: bool, comment: str = "") -> None:
        self._check_writable()
        if flagged:
            old = compound.flags.get(key, {})
            compound.flags[key] = {
                "comment": comment,
                "by": old.get("by") or (self.lock.user if self.lock else ""),
                "at": old.get("at") or datetime.now().isoformat(timespec="seconds"),
            }
        else:
            compound.flags.pop(key, None)
        self.save_compound(compound)

    def dismiss_warning(self, compound: Compound, warning, reason: str = "") -> None:
        """Hide an automatic warning for this compound (SPEC §8). It comes back if the data
        it was about changes (its fingerprint no longer matches)."""
        self._check_writable()
        compound.data.setdefault("dismissed", {})[warning.key] = {
            "fingerprint": warning.fingerprint, "message": warning.message,
            "reason": reason.strip(), "by": self.lock.user if self.lock else "",
            "at": datetime.now().isoformat(timespec="seconds")}
        self.save_compound(compound)

    def restore_warning(self, compound: Compound, key: str) -> None:
        self._check_writable()
        if (compound.data.get("dismissed") or {}).pop(key, None) is not None:
            self.save_compound(compound)

    def field_defs(self, compound: Compound) -> list[F.FieldDef]:
        return [d for d in (F.field_def(e) for e in compound.fields) if d is not None]

    def used_part_values(self, field_key: str, part_key: str) -> list[str]:
        """Every value one part of a field has on the project's compounds (those showing
        the field), each once, first spelling kept: what a "drop-down that learns" offers
        besides its built-in choices (SPEC §13l.13). Worked out when asked, so a value no
        compound uses any more is not offered."""
        values = []
        for compound in self.all_compounds():
            if field_key not in compound.field_keys:
                continue
            value = compound.values.get(field_key)
            if isinstance(value, dict):
                values.append(value.get(part_key, ""))
        return F.distinct(values)

    # ---- long text stored in .txt files

    def _text_path(self, compound: Compound, key: str) -> Path:
        return compound.folder / (NOTES_FILE if key == "notes" else f"{safe_filename(key)}.txt")

    def read_compound_text(self, compound: Compound, key: str) -> str:
        return read_text(self._text_path(compound, key))

    def write_compound_text(self, compound: Compound, key: str, text: str) -> None:
        self._check_writable()
        path = self._text_path(compound, key)
        if text or path.exists():
            atomic_write_text(path, text)

    # ---- attachments (spectra, additional files)

    def attachment_path(self, compound: Compound, field_key: str, file_name: str) -> Path:
        defn = F.field_def(next((e for e in compound.fields if e["key"] == field_key),
                                {"key": field_key}))
        subdir = defn.subdir if defn else "files"
        return compound.folder / subdir / file_name

    def _attachments_def(self, compound: Compound, field_key: str) -> F.FieldDef:
        entry = next((e for e in compound.fields if e["key"] == field_key), {"key": field_key})
        defn = F.field_def(entry)
        if defn is None or defn.kind != "attachments":
            raise ProjectError(f"{field_key} is not a file field.")
        return defn

    def _add_attachment_item(self, compound: Compound, field_key: str, defn: F.FieldDef,
                             dest: Path, item_values: dict | None) -> dict[str, str]:
        item = {"file": dest.name, **{p.key: p.default for p in defn.item_parts}}
        if field_key != "spectra":   # printed inline, these are figures: an ID for mentions
            item["id"] = new_id("f")
        if item_values:
            item.update(item_values)
        compound.values.setdefault(field_key, []).append(item)
        self.save_compound(compound)
        return item

    def attach_compound_file(self, compound: Compound, field_key: str, source: Path,
                             item_values: dict[str, str] | None = None,
                             original: Path | None = None) -> dict[str, str]:
        """Copy a file into the compound and add it to an attachments field. `original`:
        a pasted spectrum's original copy (e.g. Mnova's .emf), kept next to it under the
        same name and recorded as the item's "original" (SPEC §13l.1; a restore)."""
        self._check_writable()
        source = Path(source)
        defn = self._attachments_def(compound, field_key)
        if source.suffix.lower() not in ATTACHMENT_EXTENSIONS:
            raise ProjectError(
                f"{source.name} is not a supported file type.\n"
                f"Please use a PDF or an image ({', '.join(ATTACHMENT_EXTENSIONS)}).")
        if not source.is_file():
            raise ProjectError(f"File not found:\n{source}")
        values = dict(item_values or {})
        values.pop(ORIGINAL_KEY, None)
        folder = compound.folder / defn.subdir
        fileio.make_dirs(folder)
        if original is not None and Path(original).is_file():
            dest = self._write_picture(folder, Path(safe_filename(source.name)).stem, source,
                                       None, source.suffix.lower(), Path(original), "")
            kept = dest.with_suffix(Path(original).suffix.lower())
            if kept.exists() and kept != dest:
                values[ORIGINAL_KEY] = kept.name
        else:
            dest = _unique_path(folder / safe_filename(source.name))
            fileio.copy_file(source, dest)
        return self._add_attachment_item(compound, field_key, defn, dest, values)

    def attach_pasted_spectrum(self, compound: Compound, data: bytes, ext: str,
                               spectrum_type: str, original: bytes | None = None,
                               original_ext: str = "", field_key: str = "spectra"
                               ) -> dict[str, str]:
        """A spectrum pasted from the clipboard (SPEC §13l.1), e.g. from Mnova: stored in
        the compound's spectra folder as "pasted 1H.svg" (or .png/.pdf; a number is added
        if the name is taken), with Mnova's original copy next to it under the same name
        ("pasted 1H.emf"), recorded on the item as "original" so it can be converted again
        later. Returns the new item."""
        self._check_writable()
        defn = self._attachments_def(compound, field_key)
        ext = ext.lower()
        if ext not in ATTACHMENT_EXTENSIONS:
            raise ProjectError(f"A pasted spectrum can't be stored as {ext}.")
        folder = compound.folder / defn.subdir
        fileio.make_dirs(folder)
        dest = self._write_picture(folder, pasted_spectrum_stem(spectrum_type), None, data,
                                   ext, original, original_ext)
        values: dict[str, str] = {"type": spectrum_type}
        kept = dest.with_suffix(original_ext.lower()) if original and original_ext else None
        if kept is not None and kept != dest and kept.exists():
            values[ORIGINAL_KEY] = kept.name
        return self._add_attachment_item(compound, field_key, defn, dest, values)

    def attachment_original(self, compound: Compound, field_key: str,
                            item: dict) -> Path | None:
        """The original copy kept next to a pasted spectrum (Mnova's .emf), if it is there."""
        name = item.get(ORIGINAL_KEY) if isinstance(item, dict) else None
        if not name or not isinstance(name, str):
            return None
        path = self.attachment_path(compound, field_key, name)
        return path if path.is_file() and name != item.get("file") else None

    def pasted_spectra(self) -> list[tuple[Compound, str, dict, Path, Path]]:
        """Every pasted spectrum that has its original kept next to it: (compound, field
        key, item, the stored file, the original), for "Re-render pasted pictures"."""
        out = []
        for compound in self.all_compounds():
            for defn in self.field_defs(compound):
                if defn.kind != "attachments":
                    continue
                for item in compound.values.get(defn.key) or []:
                    original = self.attachment_original(compound, defn.key, item)
                    if original is not None:
                        path = self.attachment_path(compound, defn.key, item.get("file", ""))
                        out.append((compound, defn.key, item, path, original))
        return out

    def replace_pasted_spectrum(self, compound: Compound, field_key: str, item: dict,
                                data: bytes, ext: str) -> Path:
        """Store a pasted spectrum converted again from its original (e.g. now as SVG
        rather than a picture): same name, perhaps another extension; its settings, place
        in the order and original stay. Returns the new file."""
        self._check_writable()
        ext = ext.lower()
        if ext not in ATTACHMENT_EXTENSIONS:
            raise ProjectError(f"A pasted spectrum can't be stored as {ext}.")
        old = self.attachment_path(compound, field_key, item.get("file", ""))
        new = old.with_suffix(ext)
        original = self.attachment_original(compound, field_key, item)
        if new != old and (new.exists() or (original is not None and new == original)):
            new = _unique_picture_path(old.parent, old.stem + " converted", ext)
        atomic_write_bytes(new, data)
        if new != old:
            if old.exists():
                fileio.remove_file(old)
            order = compound.data.get(spectra_mod.ORDER_KEY)
            if isinstance(order, list):
                for entry in order:
                    if (isinstance(entry, list) and len(entry) == 2 and entry[0] == field_key
                            and entry[1] == old.name):
                        entry[1] = new.name
            item["file"] = new.name
            self.save_compound(compound)
        return new

    def remove_compound_file(self, compound: Compound, field_key: str, file_name: str) -> None:
        self._check_writable()
        items = compound.values.get(field_key, [])
        item = next((i for i in items if i.get("file") == file_name), None)
        if item is None:
            return
        original = self.attachment_original(compound, field_key, item)
        items.remove(item)
        self.save_compound(compound)
        path = self.attachment_path(compound, field_key, file_name)
        if path.exists():
            # (a pasted spectrum's original goes with it, and comes back with it)
            self._move_to_trash(path, kind="compound_file", name=file_name,
                                extra={"compound_id": compound.id, "field_key": field_key,
                                       "item": path.name, "item_values": item,
                                       "compound_name": compound.name},
                                companions=_present(original))

    def _restore_compound_file(self, entry: TrashEntry, item: Path) -> str:
        try:
            compound = self.compound(entry.info.get("compound_id", ""))
        except KeyError:
            raise ProjectError("The compound this file belonged to no longer exists. "
                               f"The file is still in the Trash folder:\n{item}") from None
        key = entry.info["field_key"]
        if key == "structure":
            if self.structure_path(compound).exists():
                raise ProjectError("This compound already has a structure. Clear it first, "
                                   "then restore the old one.")
            self.set_structure(compound, read_text(item))
        elif key in self._IMAGE_SLOTS:
            self._set_image(compound, key, item, vector=_trash_companion(entry))
        elif key == "private_files":
            self.add_private_item(compound, item, entry.info.get("item_values", {}).get("note", ""))
        elif key == ms_reports.TRASH_FIELD:
            self.add_hrms_report(compound, item)
        else:
            values = {k: v for k, v in entry.info.get("item_values", {}).items() if k != "file"}
            self.attach_compound_file(compound, key, item, values,
                                      original=_trash_companion(entry))
        return f"Restored {item.name} to “{compound.name or 'unnamed compound'}”."

    # ---- HRMS report files (SPEC §13n.3)

    def hrms_report_dir(self, compound: Compound) -> Path:
        return compound.folder / ms_reports.HRMS_DIR

    def hrms_reports(self, compound: Compound) -> list[dict[str, Any]]:
        """The HRMS field's report files: [{"file", "kind"}] in the order added."""
        return ms_reports.report_items(compound)

    def hrms_report_path(self, compound: Compound, name: str) -> Path:
        return self.hrms_report_dir(compound) / name

    def _hrms_value(self, compound: Compound) -> dict[str, Any]:
        value = compound.values.get("hrms")
        if not isinstance(value, dict):
            value = F.empty_value(F.ALL_FIELDS["hrms"]) or {}
            compound.values["hrms"] = value
        return value

    def add_hrms_report(self, compound: Compound, source: Path) -> dict[str, str]:
        """Copy an HRMS report file — any type — or a whole folder (e.g. an Agilent .d data
        folder) into the compound's hrms/ folder and list it in the HRMS field. The Found
        box is then filled from the reports if it is empty (ms_reports.update_found)."""
        self._check_writable()
        source = Path(source)
        if not source.exists():
            raise ProjectError(f"Not found:\n{source}")
        dest = _unique_path(self.hrms_report_dir(compound) / safe_filename(source.name))
        fileio.make_dirs(dest.parent)
        if source.is_dir():
            fileio.copy_tree(source, dest)
        else:
            fileio.copy_file(source, dest)
        item = {"file": dest.name, "kind": "folder" if source.is_dir() else "file"}
        value = self._hrms_value(compound)
        value.setdefault(ms_reports.REPORTS_KEY, []).append(item)
        ms_reports.update_found(self, compound, value)
        self.save_compound(compound)
        return item

    def remove_hrms_report(self, compound: Compound, name: str) -> None:
        """Move a report file (or folder) to the project Trash. A found m/z filled from it
        is taken out again (or filled from the other reports)."""
        self._check_writable()
        value = self._hrms_value(compound)
        items = value.get(ms_reports.REPORTS_KEY) or []
        item = next((i for i in items if isinstance(i, dict) and i.get("file") == name), None)
        if item is None:
            return
        path = self.hrms_report_path(compound, name)
        if path.exists():
            self._move_to_trash(path, kind="compound_file", name=name,
                                extra={"compound_id": compound.id,
                                       "field_key": ms_reports.TRASH_FIELD,
                                       "item": path.name, "item_values": item,
                                       "compound_name": compound.name})
        items.remove(item)
        if not items:
            value.pop(ms_reports.REPORTS_KEY, None)
        ms_reports.update_found(self, compound, value)
        self.save_compound(compound)

    def set_hrms_print(self, compound: Compound, on: bool | None) -> None:
        """Print this compound's HRMS reports (True), not (False), or as the Spectra
        section says (None)."""
        self._check_writable()
        value = self._hrms_value(compound)
        if on is None:
            value.pop(ms_reports.PRINT_KEY, None)
        else:
            value[ms_reports.PRINT_KEY] = bool(on)
        self.save_compound(compound)

    def refresh_hrms_found(self, compounds: list[Compound] | None = None) -> int:
        """Fill (or update) the Found box from the reports for each compound where it is
        empty or was filled from a report (after the tolerance changed, for example).
        Returns how many compounds changed; nothing is written in a read-only project."""
        if self.read_only:
            return 0
        changed = 0
        for compound in compounds if compounds is not None else self.all_compounds():
            if ms_reports.report_items(compound) and ms_reports.update_found(self, compound):
                self.save_compound(compound)
                changed += 1
        return changed

    # ---- private files & folders (never printed)

    def private_dir(self, compound: Compound) -> Path:
        return compound.folder / PRIVATE_DIR

    def add_private_item(self, compound: Compound, source: Path, note: str = "") -> dict[str, str]:
        """Copy any file or whole folder into the compound's private/ folder."""
        self._check_writable()
        source = Path(source)
        if not source.exists():
            raise ProjectError(f"Not found:\n{source}")
        dest = _unique_path(self.private_dir(compound) / safe_filename(source.name))
        fileio.make_dirs(dest.parent)
        if source.is_dir():
            fileio.copy_tree(source, dest)
        else:
            fileio.copy_file(source, dest)
        item = {"file": dest.name, "kind": "folder" if source.is_dir() else "file", "note": note}
        compound.values.setdefault("private_files", []).append(item)
        self.save_compound(compound)
        return item

    def remove_private_item(self, compound: Compound, name: str) -> None:
        self._check_writable()
        items = compound.values.get("private_files", [])
        item = next((i for i in items if i.get("file") == name), None)
        if item is None:
            return
        items.remove(item)
        self.save_compound(compound)
        path = self.private_dir(compound) / name
        if path.exists():
            self._move_to_trash(path, kind="compound_file", name=name,
                                extra={"compound_id": compound.id, "field_key": "private_files",
                                       "item": path.name, "item_values": item,
                                       "compound_name": compound.name})

    # ---- structure (SPEC §7.1 field 1)

    def structure_path(self, compound: Compound) -> Path:
        return compound.folder / STRUCTURE_FILE

    def read_structure(self, compound: Compound):
        """The compound's RDKit molecule, or None. Cached until structure.mol changes."""
        path = self.structure_path(compound)
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)  # changed by this program or e.g. a sync
        cached = self._structure_cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            mol = chem.parse_structure(read_text(path))
        except chem.ChemError:
            mol = None
        self._structure_cache[path] = (stamp, mol)
        return mol

    def structure_info(self, compound: Compound) -> "chem.MolInfo | None":
        mol = self.read_structure(compound)
        return chem.mol_info(mol) if mol is not None else None

    def set_structure(self, compound: Compound, text: str, from_scheme: bool = False,
                      picture: bytes | None = None, vector: bytes | None = None,
                      vector_ext: str = "", picture_ext: str = ".png") -> list[str]:
        """Store a structure from SMILES / MOL / SDF text (raises chem.ChemError).

        `picture` is ChemDraw's drawing of it (a PNG, or an SVG/PDF as `picture_ext` says),
        pasted together with the structure; `vector` is the EMF/PDF it was made from (kept
        for converting or re-rendering).
        A ChemDraw picture kept from an earlier paste no longer matches a structure set
        any other way, so it is moved to the Trash; pictures the user added are kept.
        Returns the NMR nuclei whose reports were added automatically (e.g. ["19F"]).
        """
        self._check_writable()
        mol = chem.parse_structure(text)
        path = self.structure_path(compound)
        atomic_write_text(path, chem.mol_to_block(mol))
        self._structure_cache.pop(path, None)
        old = compound.values.get("structure") or {}
        if picture is not None:
            # ChemDraw's MOL block is drawn like the picture: its bonds cross-check the
            # picture's measured bond length (SPEC §13n.2a). SMILES has no drawing.
            reference = (drawing_scale.reference_from_mol(mol)
                         if chem.looks_like_molblock(text) else None)
            self._set_image(compound, "structure_image", data=picture, ext=picture_ext,
                            vector=vector, vector_ext=vector_ext, reference=reference)
            compound.values["structure"]["image_source"] = "chemdraw"
        elif old.get("image_override") and old.get("image_source") == "chemdraw":
            self._remove_image(compound, "structure_image")
            compound.values["structure"]["image_source"] = ""
        # (_set_image/_remove_image may have replaced the dict: always look it up again.)
        compound.values.setdefault("structure", {})["from_scheme"] = from_scheme
        self.save_compound(compound)
        return self.ensure_heteroatom_reports(compound)

    def structure_from_scheme(self, compound: Compound) -> bool:
        return bool((compound.values.get("structure") or {}).get("from_scheme"))

    def structure_picture_source(self, compound: Compound) -> str:
        """"chemdraw" (pasted with the structure), "user" (added by hand) or "" (none)."""
        value = compound.values.get("structure") or {}
        if not value.get("image_override"):
            return ""
        return value.get("image_source") or "user"

    def clear_structure(self, compound: Compound) -> None:
        """Remove the structure (the old structure.mol goes to the Trash)."""
        self._check_writable()
        path = self.structure_path(compound)
        if path.exists():
            self._move_to_trash(path, kind="compound_file", name=f"structure of "
                                f"{compound.name or 'unnamed compound'}",
                                extra={"compound_id": compound.id, "field_key": "structure",
                                       "item": path.name, "compound_name": compound.name})

    def heteroatom_nuclei(self, info: "chem.MolInfo") -> list[str]:
        """NMR reports a structure calls for beyond ¹H/¹³C (SPEC §7.1 field 7, §13.3)."""
        wanted = []
        if "F" in info.elements:
            wanted.append("19F")
        if "P" in info.elements and self.settings.get("auto_nmr_31P"):
            wanted.append("31P")
        if "B" in info.elements and self.settings.get("auto_nmr_11B"):
            wanted.append("11B")
        return wanted

    def ensure_heteroatom_reports(self, compound: Compound) -> list[str]:
        """Add empty ¹⁹F (and optionally ³¹P/¹¹B) reports the structure needs. Never removes."""
        info = self.structure_info(compound)
        if info is None or "nmr" not in compound.field_keys:
            return []
        reports = compound.values.setdefault("nmr", [])
        have = {r.get("nucleus") for r in reports}
        added = [n for n in self.heteroatom_nuclei(info) if n not in have]
        for nucleus in added:
            reports.append({"id": new_id("r"), "nucleus": nucleus, "label": "", "text": ""})
        if added:
            self.save_compound(compound)
        return added

    # ---- images on a compound: structure override and reaction scheme

    # field key -> (compound.values key, value entry, file stem, Trash label)
    _IMAGE_SLOTS = {"structure_image": ("structure", "image_override", "structure_image"),
                    "scheme_image": ("scheme", "image", "scheme")}

    def _image_path(self, compound: Compound, slot: str) -> Path | None:
        value_key, entry, _stem = self._IMAGE_SLOTS[slot]
        name = (compound.values.get(value_key) or {}).get(entry)
        return compound.folder / name if name else None

    def _set_image(self, compound: Compound, slot: str, source: Path | None = None,
                   data: bytes | None = None, ext: str = ".png",
                   vector: bytes | Path | None = None, vector_ext: str = "",
                   reference: "drawing_scale.Reference | None" = None) -> None:
        """Store an image from a file (`source`) or from raw bytes (e.g. pasted), with
        ChemDraw's vector original (`vector`) next to it if there is one. A structure
        picture that is a vector drawing (SVG, PDF) has its bond length measured
        (`reference`: the structure copied with it, as a cross-check; SPEC §13n.2a)."""
        self._check_writable()
        source, ext = self._check_picture(source, ext, PICTURE_FILE_EXTENSIONS)
        self._remove_image(compound, slot)
        value_key, entry, stem = self._IMAGE_SLOTS[slot]
        dest = self._write_picture(compound.folder, stem, source, data, ext, vector, vector_ext)
        value = compound.values.get(value_key) or {}
        value[entry] = dest.name
        compound.values[value_key] = value
        if slot == "structure_image":
            self._measure_structure_picture(compound, dest, reference)
        self.save_compound(compound)

    def _measure_structure_picture(self, compound: Compound, path: Path,
                                   reference: "drawing_scale.Reference | None" = None,
                                   keep_own: bool = False) -> None:
        """Store the typical bond length of the structure picture (pt; null when none
        was found) next to it in compound.json, so compiling needn't measure it; a
        raster picture has none (SPEC §13n.2a). A bond length typed for an earlier
        picture is forgotten (`keep_own`: the same picture drawn again keeps it)."""
        value = compound.values.setdefault("structure", {})
        if not keep_own:
            value.pop(drawing_scale.OVERRIDE_KEY, None)
        if Path(path).suffix.lower() in drawing_scale.VECTOR_PICTURES:
            bond = drawing_scale.measure_file(Path(path), reference)
            value[drawing_scale.MEASURED_KEY] = round(bond, 3) if bond else None
        else:
            value.pop(drawing_scale.MEASURED_KEY, None)

    def structure_picture_bond(self, compound: Compound) -> tuple[float | None, float | None]:
        """(the measured bond length of the structure picture, the one typed by the user)
        in pt; the typed one is 0 for "print at the picture's own size" and None when the
        measurement is used (SPEC §13n.2a, §2.7)."""
        value = compound.values.get("structure") or {}
        path = self.structure_image_path(compound)
        measured = drawing_scale.picture_bond_pt(
            {k: v for k, v in value.items() if k != drawing_scale.OVERRIDE_KEY}, path)
        own = None
        if drawing_scale.OVERRIDE_KEY in value:
            try:
                own = max(0.0, float(value[drawing_scale.OVERRIDE_KEY]))
            except (TypeError, ValueError):
                own = None
        return measured, own

    def set_structure_picture_bond(self, compound: Compound, own: float | None) -> None:
        """The user's own bond length for the structure picture (pt), 0 to print it at its
        own size, or None to use the measured one."""
        self._check_writable()
        value = compound.values.setdefault("structure", {})
        if own is None:
            value.pop(drawing_scale.OVERRIDE_KEY, None)
        else:
            value[drawing_scale.OVERRIDE_KEY] = round(max(0.0, float(own)), 3)
        self.save_compound(compound)

    def _remove_image(self, compound: Compound, slot: str) -> None:
        self._check_writable()
        path = self._image_path(compound, slot)
        if path is None:
            return
        value_key, entry, _stem = self._IMAGE_SLOTS[slot]
        compound.values[value_key][entry] = ""
        if slot == "structure_image":
            for key in (drawing_scale.MEASURED_KEY, drawing_scale.OVERRIDE_KEY):
                compound.values[value_key].pop(key, None)
        self.save_compound(compound)
        if path.exists():
            self._move_to_trash(path, kind="compound_file", name=path.name,
                                extra={"compound_id": compound.id, "field_key": slot,
                                       "item": path.name, "compound_name": compound.name},
                                companions=_present(picture_source_path(path)))

    def structure_image_path(self, compound: Compound) -> Path | None:
        return self._image_path(compound, "structure_image")

    def set_structure_image(self, compound: Compound, source: Path | None = None,
                            data: bytes | None = None, ext: str = ".png",
                            vector: bytes | None = None, vector_ext: str = "") -> None:
        """A picture of the structure added by the user (pasted or from a file)."""
        self._set_image(compound, "structure_image", source, data, ext, vector, vector_ext)
        compound.values["structure"]["image_source"] = "user"
        self.save_compound(compound)

    def remove_structure_image(self, compound: Compound) -> None:
        self._remove_image(compound, "structure_image")

    # ---- IDs and completeness

    def id_report(self) -> IdReport:
        return check_ids([
            SectionIds(s.id, s.name, s.id_table, [c.id for c in s.compounds],
                       [c.id_override or None for c in s.compounds],
                       [c.name for c in s.compounds])
            for s in self.data_sections()])

    def field_filled(self, compound: Compound, defn: F.FieldDef) -> bool:
        if defn.key == "procedure" and self.compound_gp(compound) is not None:
            return True  # "Prepared according to General Procedure A…" counts
        if defn.kind in ("richtext", "notes"):
            return F.is_filled(defn, None, self.read_compound_text(compound, defn.key))
        if defn.kind == "structure":
            return self.structure_path(compound).is_file()

        return F.is_filled(defn, compound.values.get(defn.key))

    def completion(self, compound: Compound) -> tuple[int, int, list[str]]:
        """(filled required fields, total required fields, labels of missing ones).

        A required field the compound does not use is not counted (the
        dashboard shows it as "not used for this compound").
        """
        required = self.section(compound.section_id).required_keys()
        missing, total = [], 0
        for defn in self.field_defs(compound):
            if defn.key in required:
                total += 1
                if not self.field_filled(compound, defn):
                    missing.append(defn.label)
        return total - len(missing), total, missing

    # ------------------------------------------------------------------ references (§9.5)

    def _load_references(self) -> list[dict[str, Any]]:
        try:
            raw = read_json(self.folder / REFERENCES_FILE, [])
        except ValueError:
            self.load_warnings.append(f"{REFERENCES_FILE} could not be read; the reference "
                                      "list starts empty (the file was not changed).")
            return []
        return [refs.clean(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def save_references(self) -> None:
        self._check_writable()
        atomic_write_json(self.folder / REFERENCES_FILE, self.references)

    # ------------------------------------------------------------------ spelling (§13k.1a)

    @property
    def dictionary_path(self) -> Path:
        return self.folder / DICTIONARY_FILE

    def dictionary_words(self) -> set[str]:
        """The project's own spelling words (dictionary.txt, shared by co-authors)."""
        from .spelling import read_words
        return read_words(self.dictionary_path)

    def add_dictionary_word(self, word: str) -> bool:
        """Add a word to the project's dictionary.txt; False if it was already there."""
        from .spelling import add_word
        self._check_writable()
        return add_word(self.dictionary_path, word)

    def reference(self, ref_id: str) -> dict[str, Any] | None:
        return next((r for r in self.references if r["id"] == ref_id), None)

    def add_reference(self, ref: dict[str, Any]) -> dict[str, Any]:
        """Add a reference (or return the existing one with the same DOI)."""
        self._check_writable()
        existing = refs.find_duplicate(self.references, ref)
        if existing is not None:
            return existing
        ref = refs.clean(ref)
        while self.reference(ref["id"]) is not None:
            ref["id"] = new_id("r")
        self.references.append(ref)
        self.save_references()
        return ref

    def update_reference(self, ref: dict[str, Any]) -> None:
        self._check_writable()
        ref = refs.clean(ref)
        for i, r in enumerate(self.references):
            if r["id"] == ref["id"]:
                self.references[i] = ref
                break
        else:
            self.references.append(ref)
        self.save_references()

    def delete_reference(self, ref_id: str) -> None:
        """Remove a reference. Citations of it are left in the text and flagged."""
        self._check_writable()
        self.references = [r for r in self.references if r["id"] != ref_id]
        self.save_references()

    # ------------------------------------------------------------------ general procedures (§6.3.3)

    def general_procedures(self, section: Section) -> list[dict[str, Any]]:
        return section.data.setdefault("general_procedures", [])

    def all_general_procedures(self) -> list[tuple[Section, dict[str, Any]]]:
        return [(s, gp) for s in self.data_sections() for gp in self.general_procedures(s)]

    def general_procedure(self, gp_id: str) -> tuple[Section, dict[str, Any]] | None:
        return next(((s, gp) for s, gp in self.all_general_procedures() if gp["id"] == gp_id),
                    None)

    def _gp_text_path(self, section: Section, gp_id: str) -> Path:
        return section.folder / GP_DIR / f"{safe_filename(gp_id)}.txt"

    def add_general_procedure(self, section_id: str, name: str = "") -> dict[str, Any]:
        self._check_writable()
        section = self._data_section(section_id)
        gps = self.general_procedures(section)
        if not name:
            used = {gp.get("name", "") for _s, gp in self.all_general_procedures()}
            letter = next(ch for ch in (*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", *map(str, range(1, 999)))
                          if f"General Procedure {ch}" not in used)
            name = f"General Procedure {letter}"
        gp = {"id": new_id("gp"), "name": name, "title": "", "sentence": DEFAULT_GP_SENTENCE}
        gps.append(gp)
        self.save_section(section)
        return gp

    def update_general_procedure(self, gp_id: str, **changes: Any) -> None:
        self._check_writable()
        found = self.general_procedure(gp_id)
        if found is None:
            raise KeyError(gp_id)
        section, gp = found
        gp.update({k: v for k, v in changes.items() if k in ("name", "title", "sentence")})
        self.save_section(section)

    def move_general_procedure(self, gp_id: str, step: int) -> None:
        found = self.general_procedure(gp_id)
        if found is None:
            return
        section, gp = found
        gps = self.general_procedures(section)
        i = gps.index(gp)
        j = max(0, min(len(gps) - 1, i + step))
        gps.insert(j, gps.pop(i))
        self.save_section(section)

    def delete_general_procedure(self, gp_id: str) -> TrashEntry | None:
        """Remove a GP (its text goes to the Trash). Compounds using it keep the choice and
        get a warning, so nothing is silently lost."""
        self._check_writable()
        found = self.general_procedure(gp_id)
        if found is None:
            return None
        section, gp = found
        path = self._gp_text_path(section, gp_id)
        if not path.exists():
            fileio.make_dirs(path.parent)
            atomic_write_text(path, "")
        entry = self._move_to_trash(path, "gp", gp.get("name", "General procedure"),
                                    {"section_id": section.id, "section_name": section.name,
                                     "gp": gp, "index": self.general_procedures(section).index(gp)})
        self.general_procedures(section).remove(gp)
        self.save_section(section)
        return entry

    def _restore_gp(self, entry: TrashEntry, item: Path) -> str:
        try:
            section = self._data_section(entry.info.get("section_id", ""))
        except (KeyError, ProjectError):
            raise ProjectError("The section this general procedure belonged to no longer "
                               f"exists. Its text is still in the Trash folder:\n{item}") from None
        gp = dict(entry.info.get("gp") or {})
        if not gp.get("id") or self.general_procedure(gp["id"]) is not None:
            gp["id"] = new_id("gp")
        gps = self.general_procedures(section)
        gps.insert(max(0, min(int(entry.info.get("index", len(gps))), len(gps))), gp)
        dest = self._gp_text_path(section, gp["id"])
        fileio.make_dirs(dest.parent)
        fileio.move(item, dest)
        self.save_section(section)
        return f"Restored “{gp.get('name', 'general procedure')}” to “{section.name}”."

    def read_gp_text(self, gp_id: str) -> str:
        found = self.general_procedure(gp_id)
        return read_text(self._gp_text_path(found[0], gp_id)) if found else ""

    def write_gp_text(self, gp_id: str, text: str) -> None:
        self._check_writable()
        found = self.general_procedure(gp_id)
        if found is None:
            raise KeyError(gp_id)
        path = self._gp_text_path(found[0], gp_id)
        fileio.make_dirs(path.parent)
        atomic_write_text(path, text)

    def gp_users(self, gp_id: str) -> list[Compound]:
        return [c for c in self.all_compounds()
                if (c.values.get("procedure") or {}).get("gp") == gp_id]

    @staticmethod
    def gp_choice(compound: Compound) -> dict[str, Any]:
        """{"gp": id or "", "values": {placeholder: text}} from the Procedure field."""
        value = compound.values.get("procedure")
        return value if isinstance(value, dict) else {}

    def compound_gp(self, compound: Compound) -> dict[str, Any] | None:
        gp_id = self.gp_choice(compound).get("gp")
        if not gp_id or "procedure" not in compound.field_keys:
            return None
        found = self.general_procedure(gp_id)
        return found[1] if found else None

    # ------------------------------------------------------------------ figures and tables (§13k.5)
    #
    # Custom sections and the General Information page ("text owners") hold figures and
    # tables. The text has "!figure{f-…} Caption" / "!table{t-…} Caption" paragraphs; a
    # figure's picture is in <owner>/images/ and listed in the owner's JSON (section.json,
    # or front/front.json) as "figures": {id: {"file": "images/x.png", "label": ""}}; a
    # table is <owner>/tables/<id>.json (core/tables.py). "label" is a typed-over label.

    def text_owner(self, key: str) -> TextOwner:
        """`key`: "general" (Front matter ▸ General Information) or a custom section's ID."""
        if key == GENERAL_OWNER:
            return TextOwner(GENERAL_OWNER, "General Information", self.front_folder)
        section = self.section(key)
        if section.type != "text":
            raise ProjectError(f"“{section.name}” is not a custom section.")
        return TextOwner(section.id, section.name, section.folder)

    def text_owners(self) -> list[TextOwner]:
        """General Information, then every custom section, in document order."""
        return [self.text_owner(GENERAL_OWNER)] + [self.text_owner(s.id) for s in self.sections
                                                   if s.type == "text"]

    def read_owner_text(self, owner: TextOwner) -> str:
        if owner.key == GENERAL_OWNER:
            return self.read_front_text("general")
        return self.read_section_text(self.section(owner.key))

    def write_owner_text(self, owner: TextOwner, text: str) -> None:
        if owner.key == GENERAL_OWNER:
            self.write_front_text("general", text)
        else:
            self.write_section_text(self.section(owner.key), text)

    def _owner_data(self, owner: TextOwner) -> dict[str, Any]:
        if owner.key == GENERAL_OWNER:
            try:
                data = read_json(self.front_folder / FRONT_DATA_FILE, {})
            except ValueError:
                data = {}
            return data if isinstance(data, dict) else {}
        return self.section(owner.key).data

    def _save_owner_data(self, owner: TextOwner, data: dict[str, Any]) -> None:
        self._check_writable()
        if owner.key == GENERAL_OWNER:
            fileio.make_dirs(self.front_folder)
            atomic_write_json(self.front_folder / FRONT_DATA_FILE, data)
        else:
            self.save_section(self.section(owner.key))

    def figures(self, owner: TextOwner) -> dict[str, dict[str, Any]]:
        """{figure id: {"file", "label", optional "width"}} for every figure the owner has stored (including
        figures no longer in its text, e.g. deleted by typing; their pictures are kept)."""
        figures = self._owner_data(owner).get("figures")
        return {k: dict(v) for k, v in figures.items() if isinstance(v, dict)} \
            if isinstance(figures, dict) else {}

    def figure_path(self, owner: TextOwner, figure_id: str) -> Path | None:
        name = self.figures(owner).get(figure_id, {}).get("file")
        return owner.folder / name if name else None

    def figure_source(self, owner: TextOwner, figure_id: str) -> Path | None:
        """ChemDraw's vector original kept next to a figure's picture (same name, .emf or
        .pdf), or None. A file that is itself another figure's picture is never taken for
        one (e.g. scheme.png and scheme.pdf added as two separate figures)."""
        figures = self.figures(owner)
        path = self.figure_path(owner, figure_id)
        vector = picture_source_path(path)
        if vector is None:
            return None
        for other_id, entry in figures.items():
            if other_id != figure_id and entry.get("file") and \
                    owner.folder / str(entry["file"]) == vector:
                return None
        return vector

    def figure_width(self, owner: TextOwner, figure_id: str) -> float | None:
        """The printed width typed for a figure, in cm (None = its own size)."""
        return figure_width_cm(self.figures(owner).get(figure_id, {}))

    def set_figure_width(self, owner: TextOwner, figure_id: str, width_cm: float | None) -> None:
        """Print a figure this wide (cm, shrunk to the text width if wider); None = the
        picture's own size (SPEC §13k.5)."""
        entry = self.figures(owner).get(figure_id)
        if entry is None:
            raise ProjectError("This figure's picture is no longer in the project.")
        if width_cm is None or width_cm <= 0:
            entry.pop("width", None)
        else:
            entry["width"] = round(float(width_cm), 2)
        self._set_figure_entry(owner, figure_id, entry)

    def _set_figure_entry(self, owner: TextOwner, figure_id: str, entry: dict | None) -> None:
        data = self._owner_data(owner)
        figures = data.get("figures") if isinstance(data.get("figures"), dict) else {}
        if entry is None:
            figures.pop(figure_id, None)
        else:
            figures[figure_id] = entry
        data["figures"] = figures
        self._save_owner_data(owner, data)

    def add_figure(self, owner: TextOwner, source: Path | None = None, data: bytes | None = None,
                   ext: str = ".png", vector: bytes | Path | None = None, vector_ext: str = "",
                   label: str = "", figure_id: str = "", width: float | None = None) -> str:
        """Store a picture (a file, or pasted PNG bytes with ChemDraw's vector original) as
        a new figure of `owner`; returns its ID (put "!figure{id}" in the text). Pass
        `vector` only for a picture that really was drawn from it (a paste, or a copy of a
        figure that has one): a same-named .pdf next to a user's file is not its original."""
        self._check_writable()
        source, ext = self._check_picture(source, ext, FIGURE_EXTENSIONS)
        folder = owner.folder / SECTION_IMAGES_DIR
        fileio.make_dirs(folder)
        stem = slugify(Path(source).stem, 30) if source is not None else "figure"
        dest = self._write_picture(folder, stem, source, data, ext, vector, vector_ext)
        figure_id = figure_id or new_id("f")
        entry: dict[str, Any] = {"file": f"{SECTION_IMAGES_DIR}/{dest.name}",
                                 "label": label.strip()}
        if width:
            entry["width"] = round(float(width), 2)
        self._set_figure_entry(owner, figure_id, entry)
        return figure_id

    def set_figure_label(self, owner: TextOwner, figure_id: str, label: str) -> None:
        entry = self.figures(owner).get(figure_id)
        if entry is None:
            raise ProjectError("This figure's picture is no longer in the project.")
        entry["label"] = label.strip()
        self._set_figure_entry(owner, figure_id, entry)

    def replace_figure_picture(self, owner: TextOwner, figure_id: str, source: Path | None = None,
                               data: bytes | None = None, ext: str = ".png",
                               vector: bytes | Path | None = None, vector_ext: str = "") -> None:
        """A new picture for a figure (same ID, so mentions of it stay right). The old
        picture goes to the Trash."""
        self._check_writable()
        source, ext = self._check_picture(source, ext, FIGURE_EXTENSIONS)
        entry = self.figures(owner).get(figure_id, {"label": ""})
        old = self.figure_path(owner, figure_id)
        if old is not None and old.exists():
            self._trash_object(owner, "figure", figure_id, old, "",
                               self.figure_source(owner, figure_id))
        self.add_figure(owner, source, data, ext, vector, vector_ext,
                        entry.get("label", ""), figure_id, figure_width_cm(entry))

    def remove_figure(self, owner: TextOwner, figure_id: str, caption: str = "") -> TrashEntry | None:
        """Send a figure's picture (and ChemDraw original) to the Trash. The editor takes its
        paragraph out of the text; restoring puts it back at the end of the text."""
        self._check_writable()
        path = self.figure_path(owner, figure_id)
        entry = None
        if path is not None and path.exists():
            entry = self._trash_object(owner, "figure", figure_id, path, caption,
                                       self.figure_source(owner, figure_id))
        self._set_figure_entry(owner, figure_id, None)
        return entry

    def _trash_object(self, owner: TextOwner, kind: str, item_id: str, path: Path, caption: str,
                      companion: Path | None = None) -> TrashEntry:
        figure = self.figures(owner).get(item_id, {}) if kind == "figure" else {}
        label = figure.get("label", "")
        what = "Figure" if kind == "figure" else "Table"
        plain = " ".join(p.plain_text for p in parse_markup(caption)).strip()
        # Its alignment (SPEC §13l.10) is its paragraph's, in the saved text: kept with it,
        # so restoring puts it back left/right as it was.
        align = next((p.stored_align for p in parse_markup(self.read_owner_text(owner))
                      if getattr(p, kind, "") == item_id), "")
        return self._move_to_trash(path, kind=kind,
                                   name=f"{what}: {plain[:40] or path.name} ({owner.name})",
                                   extra={"owner": owner.key, "owner_name": owner.name,
                                          "object_id": item_id, "caption": caption,
                                          "label": label,
                                          **({"align": align} if align else {}),
                                          **({"width": figure["width"]} if figure.get("width")
                                             else {})},
                                   companions=_present(companion))

    def _restore_object(self, entry: TrashEntry, item: Path) -> str:
        """Put a figure or table back, as a paragraph at the end of its text."""
        try:
            owner = self.text_owner(entry.info.get("owner", ""))
        except (KeyError, ProjectError):
            raise ProjectError(
                f"“{entry.info.get('owner_name', '?')}”, where this {entry.kind} was, no longer "
                f"exists. Restore it first. The file is still in the Trash folder:\n{item}"
            ) from None
        item_id = entry.info.get("object_id", "")
        # Its paragraph may still be in the text with nothing behind it (e.g. put back by
        # hand): then the picture or table just goes back under the same ID.
        in_text = bool(item_id) and any(
            i == item_id for _k, i in markup_objects(parse_markup(self.read_owner_text(owner))))
        has_file = (item_id in self.figures(owner) if entry.kind == "figure"
                    else self.table_path(owner, item_id).exists()) if item_id else False
        refill = in_text and not has_file
        if not refill and (not item_id or item_id in self.object_owners()):
            item_id = new_id("f" if entry.kind == "figure" else "t")   # (the ID is in use again)
        if entry.kind == "figure":
            self.add_figure(owner, source=item, vector=_trash_companion(entry),
                            label=entry.info.get("label", ""), figure_id=item_id,
                            width=figure_width_cm(entry.info))
        else:
            table = self._load_table_file(item) or Table.new()
            table.id = item_id
            self.write_table(owner, table)
        if refill:
            return f"Restored the {entry.kind} in “{owner.name}”."
        text = self.read_owner_text(owner).rstrip("\n")
        line = f"!{entry.kind}{{{item_id}}} " + str(entry.info.get("caption", ""))
        align = markup_stored_align(str(entry.info.get("align", "")), True)
        if align:   # (left or right, as it was: SPEC §13l.10)
            line = f"!align{{{align}}} " + line
        self.write_owner_text(owner, (text + "\n" if text else "") + line.rstrip())
        return f"Restored the {entry.kind} at the end of “{owner.name}”."

    # ---- tables

    def table_path(self, owner: TextOwner, table_id: str) -> Path:
        return owner.folder / TABLES_DIR / f"{safe_filename(table_id)}.json"

    @staticmethod
    def _load_table_file(path: Path) -> Table | None:
        try:
            data = read_json(path, None)
        except ValueError:
            return None
        return Table.from_json(data) if isinstance(data, dict) else None

    def read_table(self, owner: TextOwner, table_id: str) -> Table | None:
        """The table, or None if its file is missing or cannot be read."""
        table = self._load_table_file(self.table_path(owner, table_id))
        if table is not None:
            table.id = table_id
        return table

    def write_table(self, owner: TextOwner, table: Table) -> None:
        self._check_writable()
        if not table.id:
            table.id = new_id("t")
        path = self.table_path(owner, table.id)
        fileio.make_dirs(path.parent)
        atomic_write_json(path, table.to_json())

    def add_table(self, owner: TextOwner, table: Table | None = None) -> str:
        """Store a new table (empty 3 × 3 if none is given); returns its ID."""
        table = table.copy() if table is not None else Table.new()
        table.id = table.id or new_id("t")
        self.write_table(owner, table)
        return table.id

    def remove_table(self, owner: TextOwner, table_id: str, caption: str = "") -> TrashEntry | None:
        self._check_writable()
        path = self.table_path(owner, table_id)
        return self._trash_object(owner, "table", table_id, path, caption) if path.exists() \
            else None

    # ---- across the project

    def object_owners(self) -> dict[str, str]:
        """{figure/table ID: owner key} for every figure and table in the saved texts."""
        out: dict[str, str] = {}
        for owner in self.text_owners():
            for kind, item_id in markup_objects(parse_markup(self.read_owner_text(owner))):
                if kind != "image":
                    out.setdefault(item_id, owner.key)
        return out

    def upgrade_pictures(self, owner: TextOwner) -> int:
        """Turn older picture paragraphs (!image{images/x.png}) into figures, so they get
        an ID (for labels and mentions). The picture file stays where it is. Returns how
        many were changed."""
        if self.read_only:
            return 0
        lines = self.read_owner_text(owner).replace("\r\n", "\n").split("\n")
        changed = 0
        for i, line in enumerate(lines):
            m = _IMAGE_LINE.match(line)
            if not m:
                continue
            figure_id = new_id("f")
            self._set_figure_entry(owner, figure_id, {"file": m.group(2).strip(), "label": ""})
            lines[i] = m.group(1) + "!figure{" + figure_id + "}" + line[m.end():]
            changed += 1
        if changed:
            self.write_owner_text(owner, "\n".join(lines))
        return changed

    def upgrade_all_pictures(self) -> int:
        """upgrade_pictures for General Information and every custom section. Done when
        a project is opened for editing (before its undo history starts) and when sections
        come back from the Trash (as part of that step), never when a page is merely shown:
        that would make a new undo step each time the page is shown again by an undo."""
        if self.read_only:
            return 0
        return sum(self.upgrade_pictures(owner) for owner in self.text_owners())

    def add_section_image(self, section: Section, source: Path | None = None,
                          data: bytes | None = None, suffix: str = ".png") -> str:
        """Copy a picture into the section's images/ folder; returns its path relative
        to the section folder (as stored in older texts: !image{images/x.png})."""
        return self._add_image(section.folder, source, data, suffix)

    def _add_image(self, base: Path, source: Path | None, data: bytes | None,
                   suffix: str) -> str:
        self._check_writable()
        folder = base / SECTION_IMAGES_DIR
        fileio.make_dirs(folder)
        if source is not None:
            dest = _unique_path(folder / safe_filename(Path(source).name))
            fileio.copy_file(source, dest)
        else:
            dest = _unique_path(folder / f"picture{suffix}")
            atomic_write_bytes(dest, data or b"")
        return f"{SECTION_IMAGES_DIR}/{dest.name}"

    def section_image_path(self, section: Section, rel: str) -> Path:
        return section.folder / rel

    # ------------------------------------------------------------------ front matter (§13k.4)

    @property
    def front_folder(self) -> Path:
        """front/: the General Information text, the cover's extra text and their pictures."""
        return self.folder / FRONT_DIR

    def read_front_text(self, key: str) -> str:
        """`key`: "general" (General Information) or "cover" (text under the affiliations)."""
        return read_text(self.front_folder / FRONT_TEXT_FILES[key])

    def write_front_text(self, key: str, text: str) -> None:
        self._check_writable()
        path = self.front_folder / FRONT_TEXT_FILES[key]
        if text or path.exists():
            fileio.make_dirs(path.parent)
            atomic_write_text(path, text)

    def add_front_image(self, source: Path | None = None, data: bytes | None = None,
                        suffix: str = ".png") -> str:
        """Copy a picture into front/images/; returns its path relative to front/."""
        return self._add_image(self.front_folder, source, data, suffix)

    def use_as_general_information(self, section_id: str) -> TrashEntry:
        """Move a custom section's text (and its pictures) into the front matter's General
        Information page, then send the section to the Trash. Text already on that page is
        kept: the section's text is added after it."""
        self._check_writable()
        section = self.section(section_id)
        if section.type != "text":
            raise ProjectError("Only a custom section can become the General Information page.")
        if section.children:
            raise ProjectError(f"“{section.name}” has sub-sections. Move them out of it first, "
                               "so they are not sent to the Trash with it.")
        from . import front
        source_owner = self.text_owner(section.id)
        general = self.text_owner(GENERAL_OWNER)
        self.upgrade_pictures(source_owner)   # older pictures become figures first
        # Figures and tables are copied (so the trashed section stays complete) and keep
        # their IDs, so mentions of them still work.
        figures = self.figures(source_owner)
        for kind, item_id in markup_objects(parse_markup(self.read_section_text(section))):
            if kind == "figure" and item_id in figures:
                path = self.figure_path(source_owner, item_id)
                if path is not None and path.is_file():
                    self.add_figure(general, source=path,
                                    vector=self.figure_source(source_owner, item_id),
                                    label=figures[item_id].get("label", ""), figure_id=item_id,
                                    width=figure_width_cm(figures[item_id]))
            elif kind == "table":
                table = self.read_table(source_owner, item_id)
                if table is not None:
                    self.write_table(general, table)
        text = self.read_section_text(section)
        old = self.read_front_text("general")
        if old.strip():
            text = old.rstrip("\n") + "\n\n" + text
        else:
            front.store_general_heading(self.settings, section.name)
            self.save_settings()
        self.write_front_text("general", text)
        return self.delete_section(section.id)

    # ------------------------------------------------------------------ snapshots

    def save_snapshot(self, label: str = "") -> Path:
        """Zip the project (minus build output, lock and earlier snapshots)."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        name = f"{stamp}_{slugify(label)}" if label.strip() else stamp
        dest = self.folder / SNAPSHOTS_DIR / f"{name}.zip"
        fileio.make_dirs(dest.parent)
        skip_top = {BUILD_DIR, SNAPSHOTS_DIR, lockmod.LOCK_NAME}
        tmp = dest.with_suffix(".zip.partial")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.folder.rglob("*")):
                rel = path.relative_to(self.folder)
                if rel.parts[0] in skip_top or path.name.startswith(".tmp-"):
                    continue
                if path.is_file():
                    zf.write(path, Path(self.folder.name) / rel)
        tmp.replace(dest)
        return dest


# An older picture paragraph (core/markup.py), after its alignment if it has one.
_IMAGE_LINE = re.compile(r"^((?:!align\{[^{}]*\}\s?)?)!image\{([^{}]*)\}")


def _present(*paths: Path | None) -> tuple[Path, ...]:
    return tuple(p for p in paths if p is not None)


def _trash_companion(entry: TrashEntry) -> Path | None:
    """The vector original that went to the Trash together with a picture, if any."""
    for name in entry.info.get("companions", []):
        path = entry.folder / name
        if path.suffix.lower() in VECTOR_EXTENSIONS and path.exists():
            return path
    return None


def picture_source_path(picture: Path | None) -> Path | None:
    """ChemDraw's vector original kept next to a pasted picture (same name, .emf or .pdf),
    or None (pictures pasted before this was added, screenshots, image files)."""
    if picture is None:
        return None
    for ext in VECTOR_EXTENSIONS:
        candidate = picture.with_suffix(ext)
        if candidate != picture and candidate.exists():
            return candidate
    return None


def figure_width_cm(entry: dict) -> float | None:
    """The "width" (cm) stored in a figure entry, or None (the picture's own size)."""
    try:
        width = float(entry.get("width") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    return width if width > 0 else None


def _unique_picture_path(folder: Path, stem: str, ext: str) -> Path:
    """Like _unique_path, but keeps clear of every picture with the same name, whatever
    its extension: a new picture is never paired with an old .emf/.pdf, and a new .pdf
    picture is never taken for the original of an existing scheme.png (or the reverse)."""
    n = 1
    while True:
        name = stem if n == 1 else f"{stem} ({n})"
        if not any((folder / f"{name}{e}").exists()
                   for e in dict.fromkeys((ext.lower(), *PICTURE_EXTENSIONS))):
            return folder / f"{name}{ext}"
        n += 1


def _unique_path(path: Path, reserved: set[str] = frozenset()) -> Path:
    """Add " (2)", " (3)"... if a file of that name already exists."""
    candidate, n = path, 2
    while candidate.exists() or candidate.name in reserved:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate
