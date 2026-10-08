"""Data-section dashboard (SPEC §6.3.4): one row per compound, one column per field.

Each cell has a state (shown with a colour *and* an icon) and a short summary:

    FILLED    ✔   the field has a value
    EMPTY     ○   empty, not required
    MISSING   ✖   empty and required
    NOT_USED  —   the compound does not use this field
    plus      ⚑   flagged by a person
              ⚠   an automatic check found a problem
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import checks
from . import fields as F
from .markup import parse

FILLED, EMPTY, MISSING, NOT_USED = "filled", "empty", "missing", "not_used"
STATE_ICONS = {FILLED: "✔", EMPTY: "○", MISSING: "✖", NOT_USED: "—"}
STATE_LABELS = {FILLED: "filled", EMPTY: "empty", MISSING: "empty but required",
                NOT_USED: "field not used for this compound"}

# ---------------------------------------------------------------------- status colours
# The colours of the dashboards' cells (SPEC §13k.3), stored in project.json as
# "status_colors": {status: "#rrggbb" or "" for no colour}, only where they differ from
# these. Cells are painted with a see-through tint of the colour (CELL_ALPHA), so their
# text stays readable in light and dark mode; charts use the colour itself.

FLAGGED, WARNING = "flagged", "warning"
STATUS_KEYS = (FILLED, EMPTY, MISSING, FLAGGED, WARNING, NOT_USED)
STATUS_SETTING = "status_colors"
STATUS_ICONS = {**STATE_ICONS, FLAGGED: "⚑", WARNING: "⚠"}
STATUS_NAMES = {FILLED: "Filled", EMPTY: "Empty (not required)",
                MISSING: "Missing (required but empty)", FLAGGED: "Flagged by a person",
                WARNING: "Has a warning", NOT_USED: "Field not used for this compound"}
DEFAULT_STATUS_COLOURS = {FILLED: "#27ae60", EMPTY: "", MISSING: "#e74c3c",
                          FLAGGED: "#e67e22", WARNING: "#f1c40f", NOT_USED: "#7f7f7f"}
CELL_ALPHA = {FILLED: 60, EMPTY: 60, MISSING: 95, FLAGGED: 120, WARNING: 110, NOT_USED: 45}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def status_colours(settings: dict) -> dict[str, str]:
    """Every status's colour ("#rrggbb", or "" for none): the project's own where it has
    a usable one, else the standard colour."""
    stored = settings.get(STATUS_SETTING)
    stored = stored if isinstance(stored, dict) else {}
    out = dict(DEFAULT_STATUS_COLOURS)
    for key in STATUS_KEYS:
        value = stored.get(key)
        if isinstance(value, str) and (value == "" or _HEX.fullmatch(value)):
            out[key] = value.lower()
    return out


def store_status_colours(settings: dict, colours: dict[str, str]) -> None:
    """Keep only the colours that differ from the standard ones (none: no key at all)."""
    own = {k: str(colours[k]).lower() for k in STATUS_KEYS
           if k in colours and (colours[k] == "" or _HEX.fullmatch(str(colours[k])))
           and str(colours[k]).lower() != DEFAULT_STATUS_COLOURS[k]}
    if own:
        settings[STATUS_SETTING] = own
    else:
        settings.pop(STATUS_SETTING, None)


def cell_status(cell: "Cell") -> str:
    """Which colour a cell is painted with: a warning wins over a flag, which wins over
    the cell's own state."""
    if cell.warnings:
        return WARNING
    if cell.flag is not None:
        return FLAGGED
    return cell.state

FILTERS = {"all": "All compounds", "incomplete": "Incomplete only", "flagged": "Flagged only",
           "warnings": "With warnings only"}

FIXED_KEYS = ("structure", "name")  # shown as fixed columns, not field columns
HIDDEN_KEYS = ("notes",)


@dataclass
class Cell:
    state: str
    summary: str = ""
    flag: dict[str, str] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def icons(self) -> str:
        out = STATE_ICONS[self.state]
        if self.flag is not None:
            out += " ⚑"
        if self.warnings:
            out += " ⚠"
        return out

    @property
    def tooltip(self) -> str:
        lines = [STATE_LABELS[self.state].capitalize()]
        if self.flag is not None:
            lines.append(f"⚑ Flagged: {self.flag.get('comment') or '(no comment)'}"
                         + (f" — {self.flag['by']}" if self.flag.get("by") else ""))
        lines += [f"⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


@dataclass
class Row:
    compound: Any
    display_id: str
    id_warnings: list[str]
    filled: int
    total: int
    missing: list[str]
    cells: dict[str, Cell]
    checks: dict[str, int] = field(default_factory=dict)   # active warnings by check

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def flag_count(self) -> int:
        return len(self.compound.flags)

    @property
    def warning_count(self) -> int:
        # Per-nucleus cells ("nmr:13C") repeat their field's warnings: count each field once.
        return len(self.id_warnings) + sum(len(c.warnings) for k, c in self.cells.items()
                                           if ":" not in k)


# ---------------------------------------------------------------------- columns

def available_columns(section) -> list[str]:
    """Every field key used by the section's defaults or any of its compounds, in a
    sensible order: defaults first, then the library order, then custom fields."""
    keys: list[str] = []
    for entry in section.default_fields:
        if entry["key"] not in keys:
            keys.append(entry["key"])
    library_order = [d.key for d in F.CORE_FIELDS + F.LIBRARY_FIELDS]
    used = {e["key"] for c in section.compounds for e in c.fields}
    keys += [k for k in library_order if k in used and k not in keys]
    keys += sorted(k for k in used if k not in keys)
    return [k for k in keys if k not in FIXED_KEYS + HIDDEN_KEYS]


# ---- "split by nucleus": one column per NMR nucleus / spectrum type (keys "nmr:13C",
#      "spectra:2D NMR"). The columns menu still shows/hides NMR and spectra as groups.

SPLIT_FIELDS = ("nmr", "spectra")


def view_settings(section) -> dict:
    """A section's (or the project's) dashboard view choices; a hand-edited value that is
    not a dictionary counts as none."""
    view = section.data.get("dashboard")
    return view if isinstance(view, dict) else {}


def is_split(section) -> bool:
    return bool(view_settings(section).get("split_nuclei"))


def _nucleus_order(n: str) -> tuple:
    return (F.NUCLEI.index(n) if n in F.NUCLEI else len(F.NUCLEI), n)


def nmr_nuclei(section) -> list[str]:
    found = set(F.DEFAULT_NMR_NUCLEI)
    for c in section.compounds:
        found |= {r.get("nucleus", "") for r in c.values.get("nmr") or [] if r.get("nucleus")}
    return sorted(found, key=_nucleus_order)


def spectrum_types(section) -> list[str]:
    nmr = nmr_nuclei(section)
    found = {n for n in nmr if n in F.SPECTRUM_TYPES}
    for c in section.compounds:
        found |= {i.get("type", "") for i in c.values.get("spectra") or [] if i.get("type")}
    order = list(F.SPECTRUM_TYPES)
    return sorted(found, key=lambda t: (order.index(t) if t in order else len(order), t))


def split_keys(section, key: str) -> list[str]:
    if key == "nmr":
        return [f"nmr:{n}" for n in nmr_nuclei(section)]
    if key == "spectra":
        return [f"spectra:{t}" for t in spectrum_types(section)]
    return [key]


def chosen_columns(section) -> list[str]:
    """The fields chosen in the Columns menu (NMR and spectra count as one each)."""
    available = available_columns(section)
    saved = view_settings(section).get("columns")
    if not isinstance(saved, list):   # none chosen (or a hand-edited value that is not a list)
        return available
    return [k for k in saved if isinstance(k, str) and k in available]


def shown_columns(section) -> list[str]:
    """The actual table columns (NMR/spectra expanded per nucleus when split)."""
    keys = chosen_columns(section)
    if not is_split(section):
        return keys
    return [sub for k in keys for sub in split_keys(section, k)]


def field_label(section, key: str) -> str:
    if ":" in key:
        base, sub = key.split(":", 1)
        return f"{F.nucleus_label(sub)} NMR" if base == "nmr" else f"{F.nucleus_label(sub)} spectrum"
    entry = next((e for e in section.default_fields if e["key"] == key), None)
    if entry is None:
        entry = next((e for c in section.compounds for e in c.fields if e["key"] == key),
                     {"key": key})
    defn = F.field_def(entry)
    return defn.label if defn else key


# ---------------------------------------------------------------------- summaries

def _plain(markup_text: str, limit: int = 40, project=None) -> str:
    if project is not None:
        from .links import plain_text
        text = plain_text(project, markup_text)
    else:
        text = " ".join(p.plain_text for p in parse(markup_text)).strip()
    return _shorten(text, limit)


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def field_summary(project, compound, defn: F.FieldDef) -> str:
    value = compound.values.get(defn.key)
    if defn.key == "procedure":
        own = _plain(project.read_compound_text(compound, defn.key), 200, project)
        gp = project.compound_gp(compound)
        return _shorten(f"{gp.get('name', '')} · {own}" if gp and own else
                        (gp.get("name", "") if gp else own), 40)
    if defn.kind == "richtext":
        return _plain(project.read_compound_text(compound, defn.key), project=project)
    if defn.kind == "citations":
        ids = (value or {}).get("refs") or []
        from . import references
        labels = [references.short_label(project.reference(i)) if project.reference(i)
                  else "⚠ missing" for i in ids]
        return _shorten("; ".join(labels), 40)
    if defn.kind == "structure":
        info = project.structure_info(compound)
        return info.formula if info else ""

    if defn.kind == "nmr":
        reports = value or []
        done = [F.nucleus_label(r.get("nucleus", "")) for r in reports if str(r.get("text", "")).strip()]
        todo = [F.nucleus_label(r.get("nucleus", "")) for r in reports if not str(r.get("text", "")).strip()]
        text = " ".join(done)
        if todo:
            text += f" (empty: {' '.join(todo)})"
        return text.strip()
    if defn.kind == "private":
        items = value or []
        return f"{len(items)} item{'s' if len(items) != 1 else ''}" if items else ""
    if defn.kind == "attachments":
        items = value or []
        if not items:
            return ""
        if defn.key == "spectra":
            return ", ".join(F.nucleus_label(i.get("type", "")) for i in items)
        return f"{len(items)} file{'s' if len(items) != 1 else ''}"
    if defn.kind == "parts":
        value = value or {}
        if defn.key == "hrms":
            found = str(value.get("found", "")).strip()
            if not found:
                return ""
            result, _problem = checks.compound_hrms(project, compound)
            ppm = f" (Δ {result.ppm:+.1f} ppm)" if result and result.ppm is not None else ""
            return f"{found}{ppm}"
        pieces = []
        for part in defn.parts:
            v = str(value.get(part.key, "")).strip()
            if not v or (part.default and v == part.default and part.key not in defn.required_parts()):
                continue
            if part.key.endswith("unit") and pieces:
                pieces[-1] += f" {v}"  # "28.4" + "mg" -> "28.4 mg"
                continue
            pieces.append(v + (part.suffix if part.suffix in ("%", "°C") else
                               f" {part.suffix}" if part.suffix else ""))
        return re.sub(r"\s+", " ", " · ".join(pieces))
    return ""


# ---------------------------------------------------------------------- rows

def build_rows(project, section, id_report=None) -> list[Row]:
    id_report = id_report or project.id_report()
    required = section.required_keys()
    # (worked out once for the section, not per compound)
    columns = available_columns(section) + list(FIXED_KEYS)
    split = [k for base in SPLIT_FIELDS for k in split_keys(section, base)]
    rows = []
    for compound in section.compounds:
        active = checks.active_warnings(project, compound, id_report)
        warnings: dict[str, list[str]] = {}
        for w in active:
            warnings.setdefault(w.field, []).append(w.message)
        defs = {d.key: d for d in project.field_defs(compound)}
        cells: dict[str, Cell] = {}
        for key in columns:
            defn = defs.get(key)
            if defn is None:
                cells[key] = Cell(NOT_USED)
                continue
            if project.field_filled(compound, defn):
                state = FILLED
            else:
                state = MISSING if key in required else EMPTY
            cells[key] = Cell(state, field_summary(project, compound, defn),
                              compound.flags.get(key), warnings.get(key, []))
        for key in split:
            cells[key] = _split_cell(compound, key, defs, required, cells)
        filled, total, missing = project.completion(compound)
        rows.append(Row(compound, id_report.display_id(compound.id),
                        warnings.get(checks.ID_KEY, []), filled, total, missing, cells,
                        dict(Counter(w.check for w in active))))
    return rows


def _split_cell(compound, key: str, defs: dict, required: set, cells: dict) -> Cell:
    base, sub = key.split(":", 1)
    parent = cells.get(base)
    if base not in defs or parent is None:
        return Cell(NOT_USED)
    flag, warnings = parent.flag, parent.warnings
    reports = [r for r in compound.values.get("nmr") or [] if r.get("nucleus") == sub] \
        if "nmr" in defs else []
    if base == "nmr":
        if not reports:
            return Cell(NOT_USED, flag=flag, warnings=warnings)
        done = [r for r in reports if str(r.get("text", "")).strip()]
        if done:
            labels = [r.get("label", "") for r in done if r.get("label")]
            summary = ", ".join(labels) if labels else (f"{len(done)} reports" if len(done) > 1 else "")
            return Cell(FILLED, summary, flag, warnings)
        return Cell(MISSING if "nmr" in required else EMPTY, flag=flag, warnings=warnings)
    items = [i for i in compound.values.get("spectra") or [] if i.get("type") == sub]
    if items:
        return Cell(FILLED, f"{len(items)} files" if len(items) > 1 else "", flag, warnings)
    if reports:  # there is an NMR report for this nucleus, so a spectrum is expected
        return Cell(MISSING if "spectra" in required else EMPTY, flag=flag, warnings=warnings)
    return Cell(NOT_USED, flag=flag, warnings=warnings)


def filter_rows(rows: list[Row], mode: str) -> list[Row]:
    if mode == "incomplete":
        return [r for r in rows if not r.complete]
    if mode == "flagged":
        return [r for r in rows if r.flag_count]
    if mode == "warnings":
        return [r for r in rows if r.warning_count]
    return rows


def summary_text(rows: list[Row]) -> str:
    complete = sum(r.complete for r in rows)
    flags = sum(r.flag_count for r in rows)
    warnings = sum(r.warning_count for r in rows)
    return (f"{complete} / {len(rows)} compounds complete · {flags} flag{'s' if flags != 1 else ''}"
            f" · {warnings} warning{'s' if warnings != 1 else ''}")


# ---------------------------------------------------------------------- project dashboard
# (SPEC §13k.3) The whole project at a glance: progress overall and per Data section,
# missing required data by field, warnings and flags by type, and a grid of every
# compound. Its view choices are stored in project.json as "project_dashboard":
# {"columns", "filter", "compact", "split_nuclei" (as a section's dashboard), "chart"}.

PROJECT_SETTING = "project_dashboard"
CHART_TYPES = {"bars": "Bars", "pie": "Donut"}
SECTION_SEPARATOR = " › "

# Plain names for the checks (core/checks.py), in the order they are shown.
CHECK_LABELS = {
    "id": "Compound IDs", "hrms": "HRMS", "c13": "¹³C signal count",
    "h1": "¹H integration", "impurity": "Solvent or impurity peaks",
    "coupling": "Large J values",
    "yield": "Yield vs mass", "spectra": "Spectra vs NMR reports",
    "frequency": "NMR frequencies", "heteroatom": "Missing heteroatom NMR",
    "gp": "General procedures", "mention": "Deleted mentions",
    "citation": "Missing references", "label": "Duplicate labels",
    "object": "Missing figure or table files",
    "gp_name": "Duplicate procedure names",
}
FLAGS_LABEL = "Flagged by a person"


def percent(done: int, total: int) -> int:
    """A progress percentage, rounded down, so that 100% only shows when nothing is left
    (1199 of 1200 is 99%, not 100%). 0 of 0 counts as 100%."""
    if total <= 0:
        return 100
    return min(100, max(0, (100 * done) // total))


def share_percent(value: int, total: int) -> int:
    """One part's share of a whole, rounded, but never shown as 0% for a part that is
    there or 100% for a part that is not everything (1 of 200 is 1%, 199 of 200 is 99%)."""
    if total <= 0 or value <= 0:
        return 0
    if value >= total:
        return 100
    return min(99, max(1, round(100 * value / total)))


def project_view(settings: dict) -> dict:
    view = settings.get(PROJECT_SETTING)
    return view if isinstance(view, dict) else {}


def chart_type(settings: dict) -> str:
    value = project_view(settings).get("chart")
    return value if value in CHART_TYPES else "bars"


class ProjectScope:
    """Stands in for a Data section so that the dashboard's column functions
    (available_columns, shown_columns, field_label…) work over every Data section at once.
    Its view choices come from project.json ("project_dashboard")."""

    def __init__(self, project):
        self.project = project
        self.sections = project.data_sections()
        self.id = ""
        self.name = str(project.settings.get("title") or project.folder.name)

    @property
    def default_fields(self) -> list[dict[str, Any]]:
        out, seen = [], set()
        for s in self.sections:
            for entry in s.default_fields:
                if entry["key"] not in seen:
                    seen.add(entry["key"])
                    out.append(entry)
        return out

    @property
    def compounds(self) -> list:
        return [c for s in self.sections for c in s.compounds]

    @property
    def data(self) -> dict:
        return {"dashboard": project_view(self.project.settings)}


def section_path(project, section) -> str:
    """"Container › Products" for a Data section inside a custom section."""
    names = [s.name for s in reversed(project.ancestors(section))] + [section.name]
    return SECTION_SEPARATOR.join(names)


@dataclass
class SectionProgress:
    section: Any
    path: str
    compounds: int
    complete: int
    filled: int      # required fields filled, over all its compounds
    required: int
    warnings: int
    flags: int

    @property
    def fraction(self) -> float:
        """Share of compounds complete (0 with no compounds)."""
        return self.complete / self.compounds if self.compounds else 0.0

    @property
    def field_fraction(self) -> float:
        return self.filled / self.required if self.required else 1.0


@dataclass
class ProjectSummary:
    sections: list[SectionProgress]
    missing: list[tuple[str, int]]     # (field, compounds missing it), in field order
    warnings: list[tuple[str, int]]    # (check, active warnings), in CHECK_LABELS order
    flags: int

    @property
    def compounds(self) -> int:
        return sum(s.compounds for s in self.sections)

    @property
    def complete(self) -> int:
        return sum(s.complete for s in self.sections)

    @property
    def filled(self) -> int:
        return sum(s.filled for s in self.sections)

    @property
    def required(self) -> int:
        return sum(s.required for s in self.sections)

    @property
    def warning_count(self) -> int:
        return sum(n for _label, n in self.warnings)

    def text(self) -> str:
        """"12 / 20 compounds complete · 86% of required fields filled · 7 warnings · 2 flags"."""
        n, w = self.flags, self.warning_count
        fields = (f" · {percent(self.filled, self.required)}% of required fields filled"
                  if self.required else "")
        return (f"{self.complete} / {self.compounds} compounds complete{fields} · {w} warning"
                f"{'s' if w != 1 else ''} · {n} flag{'s' if n != 1 else ''}")


def project_rows(project, id_report=None) -> list[tuple[Any, list[Row]]]:
    """Every Data section (nested ones too, in tree order) with its dashboard rows."""
    id_report = id_report or project.id_report()
    return [(s, build_rows(project, s, id_report)) for s in project.data_sections()]


def missing_fields(section, row: Row) -> list[str]:
    """The required data a compound is missing, by field. NMR and spectra are counted per
    nucleus ("¹³C NMR", "¹³C spectrum": a report left empty, or a report with no spectrum),
    so they can be listed here for a compound whose NMR field as a whole counts as filled."""
    out = []
    for key in (e["key"] for e in section.default_fields if e.get("required")):
        cell = row.cells.get(key)
        if cell is None or cell.state == NOT_USED:
            continue
        subs = [k for k, c in row.cells.items()
                if key in SPLIT_FIELDS and k.startswith(key + ":") and c.state == MISSING]
        if subs:
            out += [field_label(section, k) for k in subs]
        elif cell.state == MISSING:
            out.append(field_label(section, key))
    return out


def summarize(project, section_rows=None, project_warnings=None) -> ProjectSummary:
    """The numbers behind the project dashboard. `section_rows` (from project_rows) and
    `project_warnings` (checks.project_warnings) can be passed in when already worked out."""
    if section_rows is None:
        section_rows = project_rows(project)
    if project_warnings is None:
        project_warnings = checks.project_warnings(project)
    sections, missing, by_check, flags = [], Counter(), Counter(), 0
    for section, rows in section_rows:
        for row in rows:
            missing.update(missing_fields(section, row))
            by_check.update(row.checks)
        sections.append(SectionProgress(
            section, section_path(project, section), len(rows),
            sum(r.complete for r in rows), sum(r.filled for r in rows),
            sum(r.total for r in rows), sum(sum(r.checks.values()) for r in rows),
            sum(r.flag_count for r in rows)))
        flags += sum(r.flag_count for r in rows)
    by_check.update(w.check for w in project_warnings)
    # Fields in the order they appear in the sections' defaults (so a chart's colours follow
    # the field, not its count).
    order = [field_label(s, k) for s, _rows in section_rows for e in s.default_fields
             for k in [e["key"], *(split_keys(s, e["key"]) if e["key"] in SPLIT_FIELDS else [])]]
    rank = {label: i for i, label in reversed(list(enumerate(order)))}
    missing_list = sorted(missing.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))
    known = list(CHECK_LABELS)
    keys = sorted((k for k, n in by_check.items() if n),
                  key=lambda k: (known.index(k) if k in known else len(known), k))
    warnings_list = [(CHECK_LABELS.get(k, k), by_check[k]) for k in keys]
    return ProjectSummary(sections, missing_list, warnings_list, flags)
