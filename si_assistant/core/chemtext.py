"""Recognising chemistry in text: formulas, units, ranges, nuclei, deuterated solvents.

Shared by the compile formatting helpers (compile/text.py, SPEC §7.3), the editors'
formatting check (blue underline with a right-click "Fix", SPEC §13k.1ai) and the
"Chemical formula" toggle that subscripts formulas as you type (§13k.1b).

Everything here works on plain strings plus, for the check, the current format of each
character, so it can be unit-tested without Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, Sequence

from .markup import NORMAL, SUB, SUPER

ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
            "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
            "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi D T").split()
GROUPS = ["Me", "Et", "Pr", "Bu", "Ph", "Bn", "Bz", "Ac", "Ts", "Ms", "Tf", "Cy", "Cp", "Boc",
          "Cbz", "Fmoc", "Piv", "Tol", "Mes", "TMS", "TES"]
# (TBS and TIPS need no entry: they are read as the elements T, B, S and T, I, P, S. Each
# entry must not be readable as a run of other units, or a long word could take the
# matcher a very long time.)
# Ligand abbreviations written in lower case. They count as part of a formula inside
# brackets (Pd2(dba)3, Ni(cod)2, Ru(bpy)3Cl2, [Ir(cod)Cl]2); "pin" also outside (B2pin2).
LIGANDS = ["dba", "cod", "nbd", "bpy", "bipy", "dtbbpy", "dtbpy", "ppy", "phen", "py", "acac",
           "pin", "cat", "dppf", "dppe", "dppp", "dppb", "tmeda"]
BARE_LIGANDS = ["pin"]   # only straight after B: Bpin, B2pin2


def _alternatives(words) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


# One unit of a formula: an element, a group, or an alkyl group with an i/n/s/t prefix
# (iPr2NEt, nBu4NI, tBu3P, i-Pr2NEt, N(iPr)2, Ti(OiPr)4). The prefix is only allowed where
# it cannot also be read as the end of an element (Sn·Bu or S·nBu), so the matcher never
# has to try both.
_UNIT = (rf"(?:(?<![A-Za-z])[inst]-?(?:Pr|Bu)|(?<=O)[int](?:Pr|Bu)"
         rf"|{_alternatives(ELEMENTS + GROUPS)})")
_INNER = rf"(?:{_alternatives(LIGANDS)}|{_UNIT})"
_PAREN = rf"\((?:{_INNER}\d*)+\)\d*"
_BRACKET = rf"\[(?:{_INNER}\d*|{_PAREN})+\]\d*"
_ITEM = rf"(?:{_UNIT}\d*|(?:(?<=B)|(?<=B\d))(?:{_alternatives(BARE_LIGANDS)})\d*|{_PAREN}|{_BRACKET})"

# Words that look like formulas but are not: SN1/SN2/SE2 mechanisms, transition states,
# IC50/CC50/ID50 values, photosystems, optical density (OD600), supplementary labels (SI1)
# and common protein, cell-line and virus names (CDK2, PI3K, HCT116, NIH3T3, COVID19…).
# (S1, C18, E2, 3a, Table S2 never match anyway: a formula needs two units.) Anything else
# can be kept as written with "Leave … as written" (the project's dictionary.txt).
FORMULA_EXCEPTIONS = (r"S[NE][12]|TS\d+|IC\d+|CC\d+|[IL]D\d+|PS[12]|OD\d+|SI\d+|NIH3T3|"
                      r"COVID\d*|HCT\d+|HT\d+|PC\d+|PI3K\w*|CDK\d+|CDC\d+|CYP\w+|CB\d+|"
                      r"P2[XY]\d+|INT\d+|HSP\d+|NS\d+|HIV\d*|NK\d+|SH[23]|PDK\d+|CK\d+|TBK\d+")
# Molecules of a single element. Their names are also atom labels ("1H, H2", "N2-alkylation",
# "Pd1–Cl2", "D2 receptor"), so they count as formulas only in context (see _molecule):
# halogens and ozone unless written next to a dash or prime, the others only when the
# words around them show the gas ("under N2", "H2 (1 atm)", "an O2 atmosphere").
ALWAYS_MOLECULES = ("Cl2", "Br2", "I2", "O3")
CONTEXT_MOLECULES = ("H2", "D2", "N2", "O2", "F2")
SINGLE_ELEMENT = ALWAYS_MOLECULES + CONTEXT_MOLECULES
MOLECULE_BEFORE = frozenset(
    "under atmosphere balloon atm bar psi purged flushed backfilled sparged bubbled degassed "
    "evacuated dry anhydrous gaseous liquid pressurized pressure stream flow filled charged "
    "saturated excess hydrogenated hydrogenation".split())
MOLECULE_BRIDGE = frozenset("of with a an the".split())   # "atmosphere of N2", "purged with N2"
MOLECULE_AFTER = frozenset(
    "gas atmosphere atmospheres balloon atm bar psi mpa kpa torr pressure stream flow line "
    "inlet purge uptake evolution sparging bubbling manifold".split())
MOLECULE_HYPHEN = frozenset("filled free saturated purged flushed atmosphere sparged".split())

# A formula-like word: at least two units, with a digit after one of them (CDCl3, Na2SO4,
# CH2Cl2, Et3N, Pd(PPh3)4, Pd2(dba)3, [Ir(cod)Cl]2), or a single-element molecule (N2,
# Br2; checked in context by formula_matches). Use formula_matches(), not this regex alone.
FORMULA = re.compile(
    rf"(?<![A-Za-z0-9])(?=\S{{1,100}}(?!\S))"   # (no formula is longer: keeps it quick)
    rf"(?!(?:{FORMULA_EXCEPTIONS})(?![A-Za-z0-9]))"
    rf"(?:(?=[A-Za-z()\[\]-]*\d)(?:{_ITEM}{{2,}}"
    rf"|\[(?:{_INNER}\d*|{_PAREN}){{2,}}\]\d*{_ITEM}*)"
    rf"|(?:{'|'.join(SINGLE_ELEMENT)}))"
    rf"(?![A-Za-z0-9])")
# Deuterated solvents by name: "DMSO-d6", "Methanol-d4", "acetone-d6", "chloroform-d" (one
# deuterium: no digits). The name may have any capitals (it is printed as written); the d
# is the small d of the convention. Group 1 = the name, group 2 = the digits ("" for
# chloroform-d).
_DEUTERATED_NAMES = (r"DMSO|DMF|acetone|THF|toluene|benzene|methanol|MeOH|MeOD|MeCN|"
                     r"acetonitrile|pyridine|DCM|dichloromethane|chloroform|dioxane|"
                     r"cyclohexane|nitromethane")
DEUTERATED = re.compile(rf"\b((?i:{_DEUTERATED_NAMES}))-d(\d+|(?<=(?i:chloroform)-d))(?![A-Za-z0-9])")
# The same written the other way round: "d4-MeOH", "d6-DMSO" (SPEC §13l.14).
DEUTERATED_PREFIX = re.compile(rf"(?<![A-Za-z0-9])d(\d+)-(?=(?i:{_DEUTERATED_NAMES})\b)")
# Deuterated solvents written as formulas (CDCl3…): the check calls these solvents.
SOLVENT_FORMULAS = ("CDCl3", "CD2Cl2", "CD3OD", "CD3CN", "C6D6", "D2O", "CD3SOCD3")
# A nucleus only before "NMR" or a decoupling brace, so integrations like "(m, 2H)" are
# left alone.
NUCLEUS = re.compile(r"(?<![A-Za-z0-9])(\d{1,3})(H|C|F|P|B|N|Si|Se|Sn|Pt|Li|O|Al)"
                     r"(?=\s*NMR\b|\{|\})")
RANGE = re.compile(r"(?<=\d)-(?=\d)")   # any hyphen between digits; see range_matches
_RANGE_CHAIN = re.compile(r"\d[\d.]*(?:-\d[\d.]*)+")
_HYPHEN = re.compile("-")

# Units written after a number with a space (ACS: "1.2 mmol", "8.0 Hz", "25 °C",
# "5 mol %"). Single-letter units (g, h, s, L, M, K) are only flagged after a decimal
# number (or for the capitals, any number), so compound IDs such as 3g or 12h are left alone.
UNITS = ("mmol", "µmol", "μmol", "nmol", "mol", "mg", "µg", "μg", "kg", "mL", "µL", "μL", "nL",
         "mM", "µM", "μM", "nM", "Hz", "kHz", "MHz", "GHz", "min", "nm", "cm", "mm", "µm", "μm",
         "°C", "equiv", "ppm", "psi", "mbar", "bar", "atm", "Torr", "kcal", "kJ", "wt%", "mol%")
_LONG_UNITS = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
GLUED_UNITS = (  # (number)(unit), each a whole word
    re.compile(rf"(?<![\w.,])(\d+(?:\.\d+)?)({_LONG_UNITS})(?![A-Za-z%])"),
    re.compile(r"(?<![\w.,])(\d+\.\d+)([ghs])(?![A-Za-z%])"),
    re.compile(r"(?<![\w.,])(\d+(?:\.\d+)?)([LMK])(?![A-Za-z%])"),
)
_URL = re.compile(r"(?<!\S)\S*?(?:https?://|www\.|doi\.org|doi:)\S*|\b10\.\d{4,}/\S+",
                  re.IGNORECASE)   # (starting at a word start keeps long words quick)

SUB_DIGITS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
SUPER_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


_WORD_BEFORE = re.compile(r"([A-Za-z]+)[\s(]*$")
_WORD_AFTER = re.compile(r"\s*\(?\s*(?:\d+(?:\.\d+)?\s*)?([A-Za-z]+)")
_DASHES = "-–—‐'′’"


def _molecule(text: str, start: int, end: int) -> bool:
    """Is the single-element word text[start:end] (N2, Cl2…) the molecule here, rather
    than an atom label (H2 in "1H, H2", N2 in "N2-alkylation", Cl2 in "Pd1–Cl2")?"""
    word = text[start:end]
    after = text[end:end + 30]
    m = re.match(r"-([A-Za-z]+)", after)
    if m and m.group(1).lower() in MOLECULE_HYPHEN:
        return True            # "N2-filled glovebox", "O2-free"
    if (after and after[0] in _DASHES) or (start > 0 and text[start - 1] in _DASHES):
        return False
    if word in ALWAYS_MOLECULES:
        return True
    before = text[max(0, start - 50):start]
    for _ in range(3):         # "under N2", "purged with N2", "under an atmosphere of N2"
        m = _WORD_BEFORE.search(before)
        if not m:
            break
        prev = m.group(1).lower()
        if prev in MOLECULE_BEFORE:
            return True
        if prev not in MOLECULE_BRIDGE:
            break
        before = before[:m.start()]
    m = _WORD_AFTER.match(after)
    return bool(m) and m.group(1).lower() in MOLECULE_AFTER


def formula_matches(text: str, leave: Collection[str] = ()) -> list[re.Match]:
    """The formulas in `text` (regex matches, in order), with the context rules applied.
    Words in `leave` (lower case: the project's dictionary.txt and "Ignore") are skipped."""
    out = []
    for m in FORMULA.finditer(text):
        word = m.group(0)
        if leave and word.lower() in leave:
            continue
        if re.search(r"[DT]\d{3}", word):
            continue   # OD600, HCT116: no formula has 100+ deuterium or tritium atoms
        if word in SINGLE_ELEMENT and not _molecule(text, m.start(), m.end()):
            continue
        out.append(m)
    return out


class Finder:
    """Wraps a matching function so it can stand in for a compiled regex's finditer
    (compile/text.py applies its helpers through `pattern.finditer(text)`)."""

    def __init__(self, find):
        self._find = find

    def finditer(self, text: str):
        return iter(self._find(text))


def is_formula(word: str) -> bool:
    """Is this word, standing on its own, a formula whose digits should be subscripts?
    (H2, N2, O2… on their own are not: they need context, see formula_matches.)"""
    return any(m.start() == 0 and m.end() == len(word) for m in formula_matches(word))


def formula_digits(text: str, end: int | None = None, leave: Collection[str] = ()) -> list[int]:
    """Positions of the digits to subscript in the formulas in `text` (only in the one
    that ends at `end`, when given: the word just typed)."""
    out = []
    for m in formula_matches(text, leave):
        if end is not None and m.end() != end:
            continue
        out += [m.start() + i for i, ch in enumerate(m.group(0)) if ch.isdigit()]
    return out


_UNIT_START = re.compile(rf"(?:{_LONG_UNITS}|wt|[ghsLMK])(?![A-Za-z])")


def range_matches(text: str) -> list[re.Match]:
    """Hyphens in number ranges ("7.36-7.29", "1-3"), as regex matches of the hyphen.
    Left alone: chains with more than one hyphen (CAS numbers 7647-01-0, dates
    2024-10-04, ISBNs, notebook codes NPO-2-117), numbers stuck to a word before them
    (C2-3, ABC-12) and numbers followed by letters that are not a unit (6-31G)."""
    out = []
    for chain in _RANGE_CHAIN.finditer(text):
        start, end = chain.start(), chain.end()
        if chain.group(0).count("-") > 1:
            continue
        before = text[max(0, start - 2):start]
        if re.search(r"[A-Za-z][\-–]?$|\d[\-–]$", before):
            continue
        rest = text[end:end + 12]
        if re.match(r"[\-–]\d", rest):
            continue
        if re.match(r"\+*[A-Za-z]", rest) and not _UNIT_START.match(rest):
            continue
        out.append(_HYPHEN.search(text, start, end))
    return out


# ---------------------------------------------------------------------- the formatting check

@dataclass(frozen=True)
class Edit:
    """One change made by a Fix, on characters start..end of the checked text: replace
    them with `text` (if given), then set their vertical alignment and/or italics."""
    start: int
    end: int
    valign: str | None = None
    italic: bool | None = None
    text: str | None = None


@dataclass(frozen=True)
class Issue:
    kind: str           # "formula", "solvent", "unit", "range", "nucleus"
    start: int
    end: int
    message: str        # what is wrong, in plain words
    fixed: str          # how it will look, for the menu ("PdCl₂")
    edits: tuple[Edit, ...]


ISSUE_MESSAGES = {
    "formula": "Formula without subscripts",
    "solvent": "Deuterated solvent not formatted (italic d, subscript digits)",
    "unit": "Number written without a space before its unit",
    "range": "Hyphen in a number range (use an en dash)",
    "nucleus": "Nucleus before “NMR” not superscripted",
}


def find_issues(text: str, formats: Sequence[tuple[str, bool]] | None = None,
                leave: Collection[str] = ()) -> list[Issue]:
    """Chemistry formatting problems in `text`. `formats[i]` is (vertical alignment,
    italic) of character i as it is now (NORMAL/False when not given). Issues do not
    overlap and are in text order. An issue whose text (lower case) is in `leave` (the
    project's dictionary.txt, words ignored this session) is not reported."""
    def fmt(i: int) -> tuple[str, bool]:
        return formats[i] if formats is not None and i < len(formats) else (NORMAL, False)

    skip = [False] * len(text)
    for m in _URL.finditer(text):
        for i in range(m.start(), m.end()):
            skip[i] = True
    found: list[Issue] = []
    taken = bytearray(len(text))

    def add(kind: str, start: int, end: int, fixed: str, edits: list[Edit]) -> None:
        if any(skip[start:end]) or not edits or text[start:end].lower() in leave:
            return
        if any(taken[start:end]):
            return   # overlaps an issue found before
        taken[start:end] = b"\x01" * (end - start)
        found.append(Issue(kind, start, end, ISSUE_MESSAGES[kind], fixed, tuple(edits)))

    for m in DEUTERATED.finditer(text):
        d, digits = m.start(2) - 1, (m.start(2), m.end(2))
        if fmt(d)[1] and all(fmt(i)[0] == SUB for i in range(*digits)):
            continue
        fixed = f"{m.group(1)}-d{m.group(2).translate(SUB_DIGITS)}"
        edits = [Edit(d, d + 1, italic=True)]
        if m.group(2):   # (chloroform-d has none)
            edits.append(Edit(*digits, valign=SUB))
        add("solvent", m.start(), m.end(), fixed, edits)
    for m in formula_matches(text, leave):
        digits = [m.start() + i for i, ch in enumerate(m.group(0)) if ch.isdigit()]
        todo = [i for i in digits if fmt(i)[0] != SUB]
        if not todo:
            continue
        kind = "solvent" if m.group(0) in SOLVENT_FORMULAS else "formula"
        add(kind, m.start(), m.end(), m.group(0).translate(SUB_DIGITS),
            [Edit(i, i + 1, valign=SUB) for i in todo])
    for m in NUCLEUS.finditer(text):
        todo = [i for i in range(m.start(1), m.end(1)) if fmt(i)[0] != SUPER]
        if todo:
            add("nucleus", m.start(), m.end(), m.group(1).translate(SUPER_DIGITS) + m.group(2),
                [Edit(i, i + 1, valign=SUPER) for i in todo])
    for m in (m for pattern in GLUED_UNITS for m in pattern.finditer(text)):
        unit_start = m.end(1)
        if fmt(unit_start)[0] != NORMAL or fmt(m.start(1))[0] != NORMAL:
            continue
        unit = text[unit_start:m.end()]
        new_unit = "mol %" if unit == "mol%" else unit
        add("unit", m.start(), m.end(), f"{m.group(1)} {new_unit}",
            [Edit(unit_start, m.end(), text=" " + new_unit)])
    for m in range_matches(text):
        if fmt(m.start())[0] != NORMAL:
            continue
        start = m.start()
        while start > 0 and (text[start - 1].isdigit() or text[start - 1] == "."):
            start -= 1
        end = m.end() + len(re.match(r"(?:[\d.]*\d)?", text[m.end():m.end() + 40]).group(0))
        add("range", start, end, text[start:m.start()] + "–" + text[m.end():end],
            [Edit(m.start(), m.end(), text="–")])
    return sorted(found, key=lambda i: i.start)


def apply_edits(text: str, edits: Sequence[Edit]) -> str:
    """The plain text after a fix's replacements (formats are left to the caller)."""
    for e in sorted(edits, key=lambda e: e.start, reverse=True):
        if e.text is not None:
            text = text[:e.start] + e.text + text[e.end:]
    return text
