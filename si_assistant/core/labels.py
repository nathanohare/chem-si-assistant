"""Figure, table and scheme labels (SPEC §13k.5a).

Labels ("Figure S1", "Table S2", "Scheme S3") are numbered automatically in the order the
items appear in the SI: General Information first, then every section and sub-section in
tree order. Figures are pictures in custom sections and General Information, plus files a
compound shows "Inline with compound"; tables are tables in those texts; schemes are
scheme pages. The program prints exactly these labels (compile/document.py), so the PDF
always matches what the editor shows.

The rule for each kind is set in Project settings ▸ Labels and stored in project.json
(only what differs from the defaults):

    "labels": {"figure": {"word": "Figure", "prefix": "S", "style": "1"}, "table": {…}, …}

style: "1" (1, 2, 3), "I" (I, II, III), "i" (i, ii, iii) or "a" (a, b, c). A figure's word
follows the journal style unless changed ("Fig." for RSC).

Any single label can be typed over (the figure's entry in its owner's JSON, the table's
JSON, scheme.json, or the attached file's "label"). A typed-over item still uses up its
number, so typing one label never renumbers the others. Two items with the same label
are reported (core/checks.project_warnings) and shown in orange in the editor.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fileio import read_json, read_text
from .markup import Paragraph, objects, parse, serialize
from .page import roman

KINDS = ("figure", "table", "scheme")
KIND_NAMES = {"figure": "Figures", "table": "Tables", "scheme": "Schemes"}
NUMBER_STYLES = {"1": "1, 2, 3", "I": "I, II, III", "i": "i, ii, iii", "a": "a, b, c"}
DEFAULT_RULES: dict[str, dict[str, str]] = {
    "figure": {"word": "Figure", "prefix": "S", "style": "1"},
    "table": {"word": "Table", "prefix": "S", "style": "1"},
    "scheme": {"word": "Scheme", "prefix": "S", "style": "1"},
}
STYLE_WORDS = {"RSC": {"figure": "Fig."}}   # where a journal style uses another word


def style_word(kind: str, journal_style: str) -> str:
    return STYLE_WORDS.get(journal_style, {}).get(kind, DEFAULT_RULES[kind]["word"])


def default_rules(journal_style: str = "ACS") -> dict[str, dict[str, str]]:
    rules = copy.deepcopy(DEFAULT_RULES)
    for kind in KINDS:
        rules[kind]["word"] = style_word(kind, journal_style)
    return rules


def label_rules(settings: dict) -> dict[str, dict[str, str]]:
    """The rule in effect for each kind: {"word", "prefix", "style"}."""
    rules = default_rules(settings.get("journal_style", "ACS"))
    raw = settings.get("labels") if isinstance(settings.get("labels"), dict) else {}
    for kind in KINDS:
        stored = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        for key in ("word", "prefix"):
            if isinstance(stored.get(key), str):
                rules[kind][key] = stored[key].strip()
        if stored.get("style") in NUMBER_STYLES:
            rules[kind]["style"] = stored["style"]
    return rules


def store_label_rules(settings: dict, rules: dict[str, dict[str, str]]) -> None:
    """Save the rules, keeping only what differs from the defaults (so a figure's word
    keeps following the journal style unless it was changed)."""
    defaults = default_rules(settings.get("journal_style", "ACS"))
    out = {}
    for kind in KINDS:
        changed = {k: v for k, v in (rules.get(kind) or {}).items()
                   if k in defaults[kind] and v != defaults[kind][k]}
        if changed:
            out[kind] = changed
    if out:
        settings["labels"] = out
    else:
        settings.pop("labels", None)


def alpha(n: int) -> str:
    """1 → a, 26 → z, 27 → aa."""
    out = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def format_number(n: int, style: str = "1") -> str:
    if style == "I":
        return roman(n)
    if style == "i":
        return roman(n).lower()
    if style == "a":
        return alpha(n)
    return str(n)


def auto_label(kind: str, n: int, rules: dict[str, dict[str, str]]) -> str:
    """E.g. auto_label("table", 2, rules) → "Table S2"."""
    rule = rules[kind]
    number = rule["prefix"] + format_number(n, rule["style"])
    return f"{rule['word']} {number}" if rule["word"] else number


def example(rules: dict[str, dict[str, str]], kind: str, count: int = 3) -> str:
    return ", ".join(auto_label(kind, n, rules) for n in range(1, count + 1)) + " …"


def same_label(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


# ---------------------------------------------------------------------- the labels in a project

@dataclass
class LabelItem:
    kind: str                  # "figure", "table" or "scheme"
    id: str                    # internal ID ("" for an older picture that has none yet)
    owner: str                 # "general", a section ID, a compound ID or a scheme ID
    index: int                 # its place among the owner's labelled items (0, 1, …)
    auto: str                  # the automatic label
    custom: str = ""           # typed-over label ("" = automatic)
    where: str = ""            # e.g. "General Information", "Optimization ▸ 3a"
    caption: str = ""          # plain caption text (mentions left out)
    place: tuple[str, str] = ("", "")   # tree item to open: ("front", "general"),
                                        # ("section", id), ("scheme", id), ("compound", id)
    caption_markup: str = ""   # the caption as stored (with its mentions)

    @property
    def label(self) -> str:
        return self.custom or self.auto


@dataclass
class Labels:
    items: list[LabelItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.by_id: dict[str, LabelItem] = {}
        self.by_place: dict[tuple[str, int], LabelItem] = {}
        for item in self.items:
            if item.id:
                self.by_id.setdefault(item.id, item)
            self.by_place[(item.owner, item.index)] = item
        groups: dict[str, list[LabelItem]] = {}
        for item in self.items:
            groups.setdefault(" ".join(item.label.split()).casefold(), []).append(item)
        self.duplicates = {group[0].label: group for group in groups.values() if len(group) > 1}
        self._duplicate_keys = {" ".join(k.split()).casefold() for k in self.duplicates}

    def label(self, item_id: str) -> str | None:
        item = self.by_id.get(item_id)
        return item.label if item is not None else None

    def at(self, owner: str, index: int) -> LabelItem | None:
        return self.by_place.get((owner, index))

    def is_duplicate(self, label: str) -> bool:
        return " ".join(label.split()).casefold() in self._duplicate_keys

    def of_kind(self, kind: str) -> list[LabelItem]:
        return [i for i in self.items if i.kind == kind]


# Files are only read again when they change (labels are worked out often while typing).
_file_cache: dict[str, tuple[tuple[int, int], Any]] = {}


def _cached(path: Path, load):
    try:
        st = path.stat()
    except OSError:
        return load(None)
    key = (st.st_mtime_ns, st.st_size)
    hit = _file_cache.get(str(path))
    if hit is not None and hit[0] == key:
        return hit[1]
    value = load(path)
    _file_cache[str(path)] = (key, value)
    if len(_file_cache) > 2000:
        _file_cache.clear()
    return value


def _text_objects(path: Path) -> list[tuple[str, str, str]]:
    """(kind, id, plain caption) of the figures/tables in a stored text."""
    def load(p):
        if p is None:
            return []
        return [(k, i, c) for k, i, c in text_objects(read_text(p))]
    return _cached(path, load)


def text_objects(text: str) -> list[tuple[str, str, str]]:
    """(kind, id, caption as stored) of each figure/table paragraph in a text."""
    out = []
    for p in parse(text):
        kind_id = objects([p])
        if kind_id:
            out.append((kind_id[0][0], kind_id[0][1], serialize([Paragraph(p.runs)])))
    return out


def _plain(caption_markup: str) -> str:
    return " ".join(p.plain_text for p in parse(caption_markup)).strip()


def _table_label(path: Path) -> str:
    def load(p):
        if p is None:
            return ""
        try:
            data = read_json(p, {})
        except ValueError:
            return ""
        return str(data.get("label", "") or "").strip() if isinstance(data, dict) else ""
    return _cached(path, load)


def compute(project, live: dict[str, str] | None = None) -> Labels:
    """Every figure, table and scheme label in document order. `live` = {owner key: text}
    uses an editor's current (unsaved) text instead of the saved one."""
    from . import scheme_editor
    from .project import GENERAL_OWNER, SchemePage, TextOwner
    from .project import FRONT_DIR, FRONT_TEXT_FILES, TEXT_CONTENT_FILE
    live = live or {}
    rules = label_rules(project.settings)
    counters = {k: 0 for k in KINDS}
    items: list[LabelItem] = []
    report = None

    def add(kind, item_id, owner, index, custom, where, caption, place, caption_markup=""):
        counters[kind] += 1
        items.append(LabelItem(kind, item_id, owner, index, auto_label(kind, counters[kind],
                                                                       rules),
                               str(custom or "").strip(), where, caption, place,
                               caption_markup))

    def text_owner(owner: TextOwner, text_path: Path, place):
        if owner.key in live:
            found = text_objects(live[owner.key])
        else:
            found = _text_objects(text_path)
        figures = project.figures(owner) if any(k == "figure" for k, _i, _c in found) else {}
        for index, (kind, item_id, caption) in enumerate(found):
            if kind == "table":
                custom = _table_label(project.table_path(owner, item_id))
                add("table", item_id, owner.key, index, custom, owner.name, _plain(caption),
                    place, caption)
            else:   # a figure, or an older picture paragraph (no ID yet)
                custom = figures.get(item_id, {}).get("label", "") if kind == "figure" else ""
                add("figure", item_id if kind == "figure" else "", owner.key, index, custom,
                    owner.name, _plain(caption), place, caption)

    def inline_files(compound, where):
        index = 0
        for defn in project.field_defs(compound):
            if defn.kind != "attachments" or defn.key == "spectra":
                continue
            for item in compound.values.get(defn.key) or []:
                if item.get("placement", "inline") != "inline":
                    continue
                add("figure", str(item.get("id", "")), compound.id, index, item.get("label", ""),
                    where, str(item.get("caption", "")), ("compound", compound.id))
                index += 1

    general = TextOwner(GENERAL_OWNER, "General Information", project.folder / FRONT_DIR)
    text_owner(general, general.folder / FRONT_TEXT_FILES["general"], ("front", "general"))
    for section in project.sections:
        if section.type == "text":
            text_owner(TextOwner(section.id, section.name, section.folder),
                       section.folder / TEXT_CONTENT_FILE, ("section", section.id))
        elif section.type == "data":
            if report is None:
                report = project.id_report()
            for entry in section.items:
                compounds = [entry]
                if isinstance(entry, SchemePage) and scheme_editor.scheme_layout(
                        project.settings, entry)["show_label"]:
                    add("scheme", entry.id, entry.id, 0, entry.data.get("label", ""),
                        f"{section.name} ▸ {entry.name}", entry.name, ("scheme", entry.id))
                if isinstance(entry, SchemePage):
                    compounds = entry.children
                for c in compounds:
                    shown = report.display_id(c.id)
                    inline_files(c, f"{section.name} ▸ {shown}" + (f" ({c.name})" if c.name
                                                                   else ""))
    return Labels(items)
