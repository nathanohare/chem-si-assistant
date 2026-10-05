"""The compound field library (SPEC §7.1, §7.2).

Every field on a Compound Page is described by a `FieldDef`. How a field's
value is stored in compound.json depends on its kind:

    parts        {"value": "0.20", "unit": "mmol"}   one dict of short inputs
    richtext     stored in <key>.txt in the compound folder (markup.py format)
    nmr          [{"id", "nucleus": "1H", "label": "", "text": "..."}]
    attachments  [{"file": "3a_1H.pdf", "type": "1H", ...}]   files in spectra/ or files/
    structure    structure.mol (+ optional image override)
    notes        notes.txt, never printed

Numbers are stored as the text the user typed ("0.20", not 0.2) so that
significant figures are printed exactly as entered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------- choices

AMOUNT_UNITS = ("mmol", "µmol", "mol")
MASS_UNITS = ("mg", "g", "µg")
IONIZATION_METHODS = ("ESI", "APCI", "EI", "CI", "APPI", "DART", "FAB", "MALDI")
ADDUCTS = ("[M+H]+", "[M+Na]+", "[M+K]+", "[M+NH4]+", "[M]+", "[M]+•",
           "[M−H]−", "[M+Cl]−", "[M+HCOO]−")
NUCLEI = ("1H", "13C", "19F", "31P", "11B", "15N", "29Si", "2H", "77Se", "119Sn", "195Pt")
SPECTRUM_TYPES = ("1H", "13C", "19F", "31P", "11B", "Other NMR", "2D NMR", "HPLC/SFC", "IR",
                  "Other")
# How the types are shown (the Spectra field's Type drop-down, the paste chooser); the
# stored values stay as in SPECTRUM_TYPES.
SPECTRUM_TYPE_LABELS = {"1H": "¹H NMR", "13C": "¹³C NMR", "19F": "¹⁹F NMR", "31P": "³¹P NMR",
                        "11B": "¹¹B NMR"}
# The stored value "appendix" is kept for older projects; it is shown as "Spectra section".
PLACEMENTS = {"inline": "Inline with compound", "appendix": "Spectra section",
              "exclude": "Don't include in SI"}
PHYSICAL_STATES = ("colorless oil", "pale yellow oil", "yellow oil", "white solid",
                   "off-white solid", "pale yellow solid", "yellow solid", "orange solid",
                   "red solid", "brown solid", "colorless crystals")
IR_METHODS = ("ATR", "neat", "film", "KBr")
EE_METHODS = ("HPLC", "SFC", "GC")
TLC_VISUALIZATION = ("UV (254 nm)", "KMnO₄", "CAM", "p-anisaldehyde", "vanillin",
                     "phosphomolybdic acid (PMA)", "ninhydrin", "I₂", "UV + KMnO₄", "UV + CAM",
                     "UV + p-anisaldehyde")

_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def nucleus_label(nucleus: str) -> str:
    """"13C" -> "¹³C" for display. Anything not starting with digits is unchanged."""
    digits = len(nucleus) - len(nucleus.lstrip("0123456789"))
    return nucleus[:digits].translate(_SUPERSCRIPT_DIGITS) + nucleus[digits:]


# The "ee or er" box (SPEC §13l.12a): a bare percentage is an ee, a bare ratio an er.
_EE_PERCENT = re.compile(r"[<>≥≤~≈]?\s*\d+(?:[.,]\d+)?\s*%")
_ER_RATIO = re.compile(r"[<>≥≤~≈]?\s*\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?")


def ee_with_unit(value: str) -> str:
    """"99%" -> "99% ee", "97:3" -> "97:3 er" (also "97.5:2.5", ">99%"). Anything else,
    e.g. a value that already ends in ee or er (any capitals) or a plain "99", is left as
    it is."""
    text = str(value or "").strip()
    if _EE_PERCENT.fullmatch(text):
        return f"{text} ee"
    if _ER_RATIO.fullmatch(text):
        return f"{text} er"
    return text


# ---------------------------------------------------------------------- definitions

@dataclass(frozen=True)
class Part:
    key: str
    label: str = ""
    kind: str = "text"            # text | number | choice (fixed list) | combo (list or typed)
    choices: tuple[str, ...] = ()
    default: str = ""
    placeholder: str = ""
    suffix: str = ""              # shown after the input, e.g. "°C"
    choice_labels: dict[str, str] = field(default_factory=dict)
    # A "drop-down that learns" (SPEC §13l.13, combo parts only): besides `choices`, it
    # lists every other value of this part used by a compound of the project (see
    # Project.used_part_values), so a value typed once can be picked on other pages.
    learn: bool = False


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: str                     # parts | richtext | nmr | attachments | structure | notes |
                                  # private | citations
    parts: tuple[Part, ...] = ()
    filled_parts: tuple[str, ...] | None = None  # which parts must be filled (default: all text/number)
    subdir: str = ""              # attachments: "spectra" or "files"
    item_parts: tuple[Part, ...] = ()            # attachments: per-file inputs
    description: str = ""
    removable: bool = True
    custom: dict[str, str] | None = None         # for user-defined fields

    def required_parts(self) -> tuple[str, ...]:
        if self.filled_parts is not None:
            return self.filled_parts
        return tuple(p.key for p in self.parts if p.kind in ("text", "number", "combo"))


def distinct(values) -> list[str]:
    """Non-empty values (trimmed), each once: the first spelling is kept, ignoring
    capitals ("Chiralpak AD-H" and "chiralpak ad-h" are one choice)."""
    out: dict[str, str] = {}
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in out:
            out[text.casefold()] = text
    return list(out.values())


def learned_choices(part: Part, used: list[str]) -> list[str]:
    """A learning drop-down's list (SPEC §13l.13): its built-in choices in their order,
    then the other values `used` in the project, A to Z."""
    builtin = distinct(part.choices)
    known = {c.casefold() for c in builtin}
    extra = [v for v in distinct(used) if v.casefold() not in known]
    return builtin + sorted(extra, key=str.casefold)


def _num(key, label="", **kw) -> Part:
    return Part(key, label, "number", **kw)


# A file shown "Inline with compound" is a figure: its label ("Figure S3") is automatic
# unless typed here (SPEC §13k.5a).
FIGURE_LABEL_PART = Part("label", "Label (empty = automatic)", placeholder="automatic")


CORE_FIELDS: tuple[FieldDef, ...] = (
    FieldDef("structure", "Structure", "structure",
             description="SMILES, MOL file or drawing (Phase 3)"),

    FieldDef("name", "Name", "parts", (Part("text", placeholder="e.g. Ethyl 2-methylfuran-3-carboxylate"),),
             description="Optional; paste from ChemDraw"),
    FieldDef("procedure", "Experimental procedure", "richtext"),
    FieldDef("scale", "Reaction scale", "parts",
             (_num("value", placeholder="0.20"), Part("unit", kind="choice", choices=AMOUNT_UNITS))),
    FieldDef("yield", "Yield", "parts",
             (_num("percent", placeholder="92", suffix="%"), _num("mass", placeholder="28.4"),
              Part("mass_unit", kind="choice", choices=MASS_UNITS))),
    FieldDef("physical_state", "Physical state", "parts",
             (Part("text", kind="combo", choices=PHYSICAL_STATES, learn=True),)),
    FieldDef("nmr", "NMR peak reports", "nmr"),
    FieldDef("hrms", "HRMS", "parts",
             (Part("ionization", "Ionization", "combo", IONIZATION_METHODS, default="ESI"),
              Part("adduct", "Adduct", "combo", ADDUCTS, default="[M+H]+"),
              _num("found", "Found m/z", placeholder="188.1073")),
             filled_parts=("found",),
             description="Calculated m/z and ppm error are added in Phase 3"),
    FieldDef("spectra", "Spectra", "attachments", subdir="spectra",
             item_parts=(Part("type", "Type", "choice", SPECTRUM_TYPES,
                              choice_labels=SPECTRUM_TYPE_LABELS),
                         Part("title", "Header in the SI (empty = automatic)",
                              placeholder="automatic, e.g. 3a — 1H NMR (400 MHz, CDCl3)"),
                         # SPEC §13k.12.9: printed under the structure when the Spectra
                         # section's "caption" is on (core/spectra.CAPTION_KEY).
                         Part("struct_caption", "Caption under the structure (empty = automatic)",
                              placeholder="automatic, e.g. 1H NMR (CDCl3, 400 MHz)")),
             description="PDFs or images; placed in the spectra section"),
    FieldDef("files", "Additional files", "attachments", subdir="files",
             item_parts=(Part("caption", "Caption"),
                         Part("placement", "Placement", "choice", tuple(PLACEMENTS),
                              default="inline", choice_labels=PLACEMENTS),
                         FIGURE_LABEL_PART)),
    FieldDef("notes", "Notes (private — never printed)", "notes", removable=False),
)

LIBRARY_FIELDS: tuple[FieldDef, ...] = (
    FieldDef("mp", "Melting point", "parts",
             (Part("value", placeholder="102–104", suffix="°C"),
              Part("solvent", "Recryst. solvent", placeholder="optional")),
             filled_parts=("value",)),
    FieldDef("rf", "Rf", "parts",
             (_num("value", placeholder="0.35"), Part("eluent", "Eluent", placeholder="30% EtOAc/hexanes"),
              Part("visualization", "Visualization", "combo", TLC_VISUALIZATION,
                   placeholder="e.g. UV, KMnO₄", learn=True)),
             filled_parts=("value", "eluent")),
    FieldDef("ir", "IR", "parts",
             (Part("method", "Method", "combo", IR_METHODS, default="ATR"),
              Part("bands", "Bands (cm⁻¹)", placeholder="2925, 1715, 1250")),
             filled_parts=("bands",)),
    FieldDef("rotation", "Optical rotation [α]D", "parts",
             (_num("value", "[α]D", placeholder="+23.5"), _num("temperature", "T", placeholder="20", suffix="°C"),
              _num("c", "c", placeholder="1.0"), Part("solvent", "Solvent", placeholder="CHCl3")),
             filled_parts=("value", "c", "solvent")),
    FieldDef("ee", "Enantiomeric excess / ratio", "parts",
             (Part("value", "ee or er", placeholder="94% ee  or  97:3 er"),
              Part("method", "Method", "combo", EE_METHODS, default="HPLC"),
              # No columns or eluents built in: the lists learn from the project.
              Part("column", "Column", "combo", placeholder="Chiralpak AD-H", learn=True),
              Part("eluent", "Eluent", "combo", placeholder="10% iPrOH/hexanes", learn=True),
              _num("flow", "Flow", placeholder="1.0", suffix="mL/min"),
              _num("wavelength", "λ", placeholder="254", suffix="nm"),
              _num("t_major", "tR major", placeholder="12.3", suffix="min"),
              _num("t_minor", "tR minor", placeholder="14.8", suffix="min"))),
    FieldDef("dr", "Diastereomeric ratio", "parts",
             (Part("value", "d.r.", placeholder="10:1"),
              Part("method", "Determined by", placeholder="¹H NMR of the crude mixture")),
             filled_parts=("value",)),
    FieldDef("ea", "Elemental analysis", "parts",
             (Part("calcd", "Calcd", placeholder="C, 65.21; H, 5.47; N, 6.08"),
              Part("found", "Found", placeholder="C, 65.03; H, 5.52; N, 6.01"))),
    FieldDef("xray", "X-ray (CCDC number)", "parts", (Part("ccdc", placeholder="2345678"),)),
    FieldDef("references", "References (known compound)", "citations",
             description="For known compounds: a sentence such as “Spectral data matched those "
                         "reported in the literature.” followed by the citations."),
    FieldDef("si_text", "SI text (replaces the generated text)", "richtext",
             description="Optional. When filled in, this text is printed in the SI instead of "
                         "the text the program generates from the fields — exactly as typed. "
                         "Use “Fill with generated text” to start from the program's version."),
    FieldDef("private_files", "Store additional files and folders (never printed)", "private",
             description="Keep raw NMR data, .mnova files, HRMS spectra, photos… with the "
                         "compound. Copies are stored in the compound's private/ folder."),
)

ALL_FIELDS = {f.key: f for f in CORE_FIELDS + LIBRARY_FIELDS}

# Fields a brand-new Data section gives its compounds, and which are required.
DEFAULT_SECTION_FIELDS: list[dict[str, Any]] = [
    {"key": "structure", "required": True},
    {"key": "name", "required": False},
    {"key": "procedure", "required": True},
    {"key": "scale", "required": True},
    {"key": "yield", "required": True},
    {"key": "physical_state", "required": True},
    {"key": "rf", "required": False},
    {"key": "nmr", "required": True},
    {"key": "hrms", "required": True},
    {"key": "spectra", "required": True},
    {"key": "files", "required": False},
]

DEFAULT_NMR_NUCLEI = ("1H", "13C")

CUSTOM_TYPES = {"short_text": "Short text", "long_text": "Long text",
                "number_unit": "Number with unit", "file": "File(s)"}


def custom_field_def(key: str, label: str, type_: str) -> FieldDef:
    custom = {"label": label, "type": type_}
    if type_ == "long_text":
        return FieldDef(key, label, "richtext", custom=custom)
    if type_ == "number_unit":
        return FieldDef(key, label, "parts",
                        (_num("value"), Part("unit", "Unit", placeholder="unit")),
                        filled_parts=("value",), custom=custom)
    if type_ == "file":
        return FieldDef(key, label, "attachments", subdir="files",
                        item_parts=(Part("caption", "Caption"),
                                    Part("placement", "Placement", "choice", tuple(PLACEMENTS),
                                         default="inline", choice_labels=PLACEMENTS),
                                    FIGURE_LABEL_PART),
                        custom=custom)
    return FieldDef(key, label, "parts", (Part("text"),), custom=custom)


def field_def(entry: dict[str, Any]) -> FieldDef | None:
    """Look up the definition for a field entry from compound.json / section.json."""
    key = entry.get("key", "")
    if key in ALL_FIELDS:
        return ALL_FIELDS[key]
    custom = entry.get("custom")
    if isinstance(custom, dict):
        return custom_field_def(key, custom.get("label", key), custom.get("type", "short_text"))
    return None


KNOWN_COMPOUND_SENTENCE = "Spectral data matched those reported in the literature."


def empty_value(defn: FieldDef) -> Any:
    if defn.kind == "parts":
        return {p.key: p.default for p in defn.parts}
    if defn.kind == "citations":
        return {"text": KNOWN_COMPOUND_SENTENCE, "refs": []}
    if defn.kind in ("nmr", "attachments", "private"):
        return []
    return None


def is_filled(defn: FieldDef, value: Any, text: str = "") -> bool:
    """Whether a field counts as filled in (drives the "complete" status).

    `text` is the stored text for richtext/notes fields; `value` is the
    compound.json value for everything else. For structure fields, `value` is
    True when a structure exists.
    """
    if defn.kind in ("richtext", "notes"):
        return bool(text.strip())
    if defn.kind == "structure":
        return bool(value)
    if defn.kind == "parts":
        value = value or {}
        return all(str(value.get(k, "")).strip() for k in defn.required_parts())
    if defn.kind == "nmr":
        return any(str(r.get("text", "")).strip() for r in (value or []))
    if defn.kind in ("attachments", "private"):
        return bool(value)
    if defn.kind == "citations":
        return bool((value or {}).get("refs"))
    return False
