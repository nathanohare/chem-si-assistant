"""Chemistry calculations with RDKit (SPEC §7.1 1a, §8.1).

* Reading structures from SMILES, MOL blocks or SDF text.
* Molecular formula (Hill order), molecular weight (average), exact
  (monoisotopic) mass.
* HRMS ion m/z for an adduct such as [M+H]+, [M+Na]+, [M−H]−, [2M+Na]+,
  [M+H−H2O]+, [M+2H]2+, … (custom adducts are parsed, not just looked up).
* Structure drawings (ACS 1996 style).

Mass conventions:
* Monoisotopic mass uses the most abundant isotope of each element
  (¹²C, ¹H, ¹⁴N, ¹⁶O, ³⁵Cl, ⁷⁹Br, ¹¹B, …), or the labelled isotope when the
  structure specifies one (e.g. D = ²H, ¹³C).
* Ion mass = sum of atom masses − z × electron mass (0.000549 Da), and the
  printed m/z = ion mass / |z|.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, Draw, rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")  # we report problems ourselves, in plain language
rdDepictor.SetPreferCoordGen(True)

ELECTRON_MASS = 0.000548579909
_PT = Chem.GetPeriodicTable()


class ChemError(ValueError):
    """A structure or adduct that could not be understood (message is user-facing)."""


# ---------------------------------------------------------------------- reading structures

def looks_like_molblock(text: str) -> bool:
    return "M  END" in text or "V2000" in text or "V3000" in text


def parse_structure(text: str) -> Chem.Mol:
    """Read a SMILES string, MOL block or SDF text. Raises ChemError."""
    text = (text or "").strip("﻿")
    if not text.strip():
        raise ChemError("Nothing to read: the text is empty.")
    if looks_like_molblock(text):
        block = text.split("$$$$")[0]
        if "M  END" not in block:
            block = block.rstrip() + "\nM  END\n"
        mol = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False)
        if mol is None:
            raise ChemError("This MOL/SDF text could not be read as a valid structure "
                            "(check valences and charges).")
        if not _has_2d_coords(mol):
            rdDepictor.Compute2DCoords(mol)
    else:
        smiles = text.strip().split()[0]  # ignore a trailing name, as in "CCO ethanol"
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ChemError(f"“{smiles}” could not be read as SMILES "
                            "(check brackets, ring numbers and valences).")
        rdDepictor.Compute2DCoords(mol)
    if mol.GetNumAtoms() == 0:
        raise ChemError("The structure has no atoms.")
    return mol


def _has_2d_coords(mol: Chem.Mol) -> bool:
    if not mol.GetNumConformers() or not mol.GetNumAtoms():
        return False   # (an empty MOL block is then reported as "no atoms")
    pos = mol.GetConformer().GetPositions()
    return bool((abs(pos[:, 0]) + abs(pos[:, 1])).max() > 1e-4)


def mol_to_block(mol: Chem.Mol) -> str:
    return Chem.MolToMolBlock(mol)


def mdlct_to_molblock(data: bytes) -> str:
    """Decode ChemDraw's "MDLCT" clipboard format (length-prefixed lines) to a MOL block."""
    lines, i = [], 0
    while i < len(data):
        n = data[i]
        lines.append(data[i + 1:i + 1 + n].decode("latin-1"))
        i += 1 + n
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- formula and masses

def _symbol(atom: Chem.Atom) -> str:
    if atom.GetSymbol() == "H" and atom.GetIsotope() == 2:
        return "D"
    if atom.GetSymbol() == "H" and atom.GetIsotope() == 3:
        return "T"
    return atom.GetSymbol()


def _generic_label(atom: Chem.Atom) -> str:
    """How a generic atom is named to the user, as it was drawn: "X", "A", "Q", "M" for
    ChemDraw/MOL query atoms, "R1", "R2" for numbered R groups (an R# with its number, or a
    SMILES [1*]), and "R" for a plain R, R# or * (SMILES writes R as *)."""
    if atom.HasProp("_MolFileRLabel"):
        return f"R{atom.GetIntProp('_MolFileRLabel')}"
    if atom.HasQuery() and atom.GetQueryType():
        return atom.GetQueryType()                     # A, Q, X, M (and AH, QH, …)
    symbol = atom.GetSymbol()
    if symbol in ("*", "R", "R#"):
        return f"R{atom.GetIsotope()}" if atom.GetIsotope() else "R"
    return symbol


def generic_atoms(mol: Chem.Mol) -> list[str]:
    """Labels of the generic atoms (R, R1, X, A, Q, …) in a structure, as the user drew
    them (see _generic_label): they have no element, so no formula or mass. Fine in a
    general scheme, not for a compound."""
    return sorted({_generic_label(a) for a in mol.GetAtoms() if a.GetAtomicNum() == 0})


def check_specific(mol: Chem.Mol) -> None:
    """Raise ChemError if the structure has a generic atom (see generic_atoms)."""
    bad = generic_atoms(mol)
    if bad:
        raise ChemError(f"This structure has a generic atom ({', '.join(bad)}). "
                        "Draw the actual group to get a formula and masses.")


def element_counts(mol: Chem.Mol) -> Counter:
    """Counts keyed by (symbol, isotope); isotope 0 = natural (most abundant).
    Raises ChemError for a structure with a generic atom."""
    check_specific(mol)
    counts: Counter = Counter()
    for atom in Chem.AddHs(mol).GetAtoms():
        iso = atom.GetIsotope()
        if atom.GetSymbol() == "H" and iso in (2, 3):
            counts[(_symbol(atom), 0)] += 1  # D and T are written as their own symbols
        else:
            counts[(atom.GetSymbol(), iso)] += 1
    return counts


def _atom_mass(symbol: str, isotope: int) -> float:
    if symbol == "D":
        return _PT.GetMassForIsotope("H", 2)
    if symbol == "T":
        return _PT.GetMassForIsotope("H", 3)
    if isotope:
        return _PT.GetMassForIsotope(symbol, isotope)
    return _PT.GetMostCommonIsotopeMass(symbol)


def counts_mass(counts: Counter) -> float:
    return sum(_atom_mass(sym, iso) * n for (sym, iso), n in counts.items())


def hill_formula(counts: Counter) -> str:
    """Hill order: C, then H (then D, T), then the rest alphabetically.
    Without carbon everything is alphabetical. Isotope labels as e.g. "¹³C"
    are written in brackets: [13C]."""
    merged: dict[str, int] = {}
    for (sym, iso), n in counts.items():
        if n:
            key = f"[{iso}{sym}]" if iso else sym
            merged[key] = merged.get(key, 0) + n

    def bare(k: str) -> str:
        return re.sub(r"^\[\d+|\]$", "", k)

    has_c = any(bare(k) == "C" for k in merged)
    first = ["C", "[13C]", "H", "D", "T"] if has_c else []
    order = [k for k in first if k in merged]
    order += sorted((k for k in merged if k not in order), key=lambda k: (bare(k), k))
    return "".join(k + (str(merged[k]) if merged[k] > 1 else "") for k in order)


@dataclass
class MolInfo:
    formula: str          # Hill formula of the structure as drawn, e.g. C8H10O3
    mw: float             # average molecular weight (g/mol)
    exact_mass: float     # monoisotopic mass of the neutral structure as drawn
    charge: int           # net formal charge as drawn
    elements: set[str]    # element symbols present (D/T counted as H)
    smiles: str           # canonical SMILES
    counts: Counter


def canonical_smiles(mol: Chem.Mol) -> str:
    """Canonical SMILES (as in MolInfo.smiles). Unlike mol_info, it works for a structure
    with a generic atom too (R is written as *)."""
    return Chem.MolToSmiles(Chem.RemoveHs(mol))


def mol_info(mol: Chem.Mol) -> MolInfo:
    """Formula, masses and SMILES. Raises ChemError for a structure with a generic atom."""
    counts = element_counts(mol)
    elements = {("H" if s in ("D", "T") else s) for (s, _i) in counts}
    return MolInfo(
        formula=hill_formula(counts),
        mw=Descriptors.MolWt(mol),
        exact_mass=counts_mass(counts),
        charge=Chem.GetFormalCharge(mol),
        elements=elements,
        smiles=canonical_smiles(mol),
        counts=counts,
    )


# ---------------------------------------------------------------------- adducts

_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")
_ADDUCT = re.compile(r"^\[(\d*)M(?P<mods>(?:[+\-−][^+\-−\]]+)*)\](?P<z>\d*)(?P<sign>[+\-−])(?P<rad>[•·.]?)$")
_SUPER_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_MOD = re.compile(rf"([+\-−])(\d*)([A-Za-z0-9{_SUPER_DIGITS}]+)")
# A superscript mass number in front of an element is an isotope: "⁷Li", "¹⁰⁷Ag".
_SUPER_ISOTOPE = re.compile(rf"([{_SUPER_DIGITS}]+)([A-Z][a-z]?)")


_ISOTOPE_TOKEN = re.compile(r"\[(\d+)([A-Z][a-z]?)\](\d*)")
_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
# Adducts typed or pasted with subscripts, en dashes or minus signs (e.g.
# "[M+H–H₂O]+" from Word) are read as their plain form. Superscripts are read as the
# charge only after "]" (the list's own "[M+Na]⁺"); before it they are isotope labels.
_ADDUCT_TEXT = str.maketrans({**{c: str(i) for i, c in enumerate("₀₁₂₃₄₅₆₇₈₉")},
                              "−": "-", "–": "-", "‐": "-", "‑": "-", " ": None})
_CHARGE_TEXT = str.maketrans({**{c: str(i) for i, c in enumerate(_SUPER_DIGITS)},
                              "⁺": "+", "⁻": "-"})


def _isotope(symbol: str, mass_number: int) -> tuple[str, int]:
    """The counts key for an isotope label such as [13C]. Raises ChemError."""
    try:
        known = _PT.GetMassForIsotope(symbol, mass_number) > 0
    except RuntimeError:   # not an element
        known = False
    if symbol in ("D", "T") or not known:
        raise ChemError(f"“{mass_number}{symbol}” is not an isotope I know.")
    return symbol, mass_number


def _labelled_formula(text: str) -> Counter:
    """Counts for a formula with isotopes in brackets, e.g. "C5[13C]H6Br". Raises ChemError."""
    counts: Counter = Counter()
    for iso, sym, n in _ISOTOPE_TOKEN.findall(text):
        counts[_isotope(sym, int(iso))] += int(n or 1)
    rest = _ISOTOPE_TOKEN.sub("", text)
    if rest:
        counts.update(parse_formula(rest))
    return counts


def parse_m_formula(text: str) -> Counter:
    """Parse a formula typed by the user for M, e.g. "C12H12N", "C₁₂H₁₂N⁺", "C5[13C]H6Br".

    Spaces, subscript digits and a trailing charge are ignored here (m_formula_charge
    reads the charge). Raises ChemError.
    """
    return parse_m_formula_and_charge(text)[0]


# A charge typed at the end of the M formula: superscript digits, then one or more signs
# (plain, Unicode minus or en dash, or superscript), with a radical dot before or after.
# A plain digit before a plain sign is the last element's count ("C6H8N2+": N2, 1+), so
# only superscript digits give the size of the charge ("C10H8N2²⁺"), or repeated signs
# ("C10H8N2++").
_TYPED_CHARGE = re.compile(rf"(?P<n>[{_SUPER_DIGITS}]*)(?P<signs>[+\-−–⁺⁻]+)"
                           rf"(?P<n2>[{_SUPER_DIGITS}]*)$")
# Charges written so that a plain digit can only be the size of the charge:
#   after a space ("C10H8N2 2+", "C6H6 +"), in brackets ("C10H8N2(2+)"), after a caret
#   ("C10H8N2^2+"), with the digit after the sign ("C10H8N2+2"), or after a formula in
#   square brackets ("[C10H8N2]2+").
_SIGNS = "+\\-−–⁺⁻"
_DIGITS = rf"0-9{_SUPER_DIGITS}"
_EXPLICIT_CHARGE = (
    re.compile(rf"\(\s*(?P<n>[{_DIGITS}]*)\s*(?P<signs>[{_SIGNS}]+)\s*(?P<n2>[{_DIGITS}]*)\s*\)$"),
    re.compile(rf"\^\s*(?P<n>[{_DIGITS}]*)\s*(?P<signs>[{_SIGNS}]+)\s*(?P<n2>[{_DIGITS}]*)$"),
    re.compile(rf"\s+(?P<n>[{_DIGITS}]*)\s*(?P<signs>[{_SIGNS}]+)\s*(?P<n2>[{_DIGITS}]*)$"),
    re.compile(rf"(?P<n>)(?P<signs>[{_SIGNS}])(?P<n2>[0-9]+)$"),
)
# "[C10H8N2]2+": the whole formula in square brackets (not an isotope label like [13C]).
_RADICAL_MARKS = "•·."
_BRACKETED_ION = re.compile(rf"^\[(?P<f>[A-Z].*)\][{_RADICAL_MARKS}]?(?P<n>[{_DIGITS}]*)"
                            rf"(?P<signs>[{_SIGNS}]+)(?P<n2>[{_DIGITS}]*)$")
_CHARGE_HINT = ("Write a charge at the end of the formula, as in C₁₀H₈N₂²⁺, C10H8N2++, "
                "C10H8N2 2+ or C6H6+•.")


def _charge_from(m: re.Match, text: str) -> int:
    """The signed charge from a match with groups n, signs and n2. Raises ChemError."""
    signs = m.group("signs").translate(str.maketrans("−–⁺⁻", "--+-"))
    if set(signs) == {"+", "-"}:
        raise ChemError(f"The charge at the end of “{text.strip()}” has both + and −.")
    digits = (m.group("n") + m.group("n2")).translate(_CHARGE_TEXT)
    if digits and (len(signs) > 1 or (m.group("n") and m.group("n2"))):
        raise ChemError(f"The charge at the end of “{text.strip()}” can't be read: write "
                        "it as ²⁺ or ++, not both.")
    size = int(digits) if digits else len(signs)
    if size == 0:
        raise ChemError(f"The charge at the end of “{text.strip()}” is zero. {_CHARGE_HINT}")
    return size if signs[0] == "+" else -size


def parse_m_formula_and_charge(text: str) -> tuple[Counter, int]:
    """(counts, charge) of a formula typed for M, e.g. "C6H8N2+" -> (C6H8N2, +1),
    "C10H8N2²⁺", "C10H8N2++", "C10H8N2 2+", "C10H8N2(2+)", "C10H8N2+2" or "[C10H8N2]2+"
    -> (C10H8N2, +2), "C2F3O2⁻" -> (C2F3O2, −1). A radical mark (•, · or .) before or
    after the charge is allowed.

    The charge only tells the program that M is already an ion (the "M is already
    charged" hint); it never changes a mass: the adduct gives the ion's charge, and the
    typed formula is M's atoms. Raises ChemError."""
    cleaned = (text or "").translate(_SUBSCRIPT_DIGITS).strip()
    cleaned = cleaned.rstrip(_RADICAL_MARKS + " \t")
    charge = 0
    m = _BRACKETED_ION.match(cleaned.replace(" ", ""))
    if m:
        charge = _charge_from(m, text)
        cleaned = m.group("f")
    else:
        m = next((m for rx in _EXPLICIT_CHARGE if (m := rx.search(cleaned))), None)
        if m is None:   # superscript digits or repeated signs: "C10H8N2²⁺", "C6H8N2+"
            cleaned = cleaned.replace(" ", "")
            m = _TYPED_CHARGE.search(cleaned)
        if m:
            charge = _charge_from(m, text)
            cleaned = cleaned[:m.start()]
    cleaned = cleaned.replace(" ", "").rstrip(_RADICAL_MARKS)
    if not cleaned:
        raise ChemError("The formula is empty.")
    try:
        return _labelled_formula(cleaned), charge
    except ChemError as e:
        if any(c in (text or "") for c in "+-−–⁺⁻()^"):
            raise ChemError(f"{e} {_CHARGE_HINT}") from None
        raise


def same_formula(a: Counter, b: Counter) -> bool:
    """Whether two element counts are the same composition, with D the same as [2H] and
    T the same as [3H] (a typed "[2H]" keeps its notation, but is not a different M)."""
    def norm(counts: Counter) -> Counter:
        out: Counter = Counter()
        for (sym, iso), n in counts.items():
            if sym == "H" and iso in (2, 3):
                sym, iso = ("D" if iso == 2 else "T"), 0
            out[(sym, iso)] += n
        return +out
    return norm(a) == norm(b)


def info_from_counts(counts: Counter, charge: int = 0) -> MolInfo:
    """A MolInfo for a formula without a structure (e.g. typed by the user)."""
    def avg(sym: str) -> float:
        return _PT.GetAtomicWeight("H" if sym in ("D", "T") else sym)
    mw = sum((_atom_mass(s, i) if i or s in ("D", "T") else avg(s)) * n
             for (s, i), n in counts.items())
    return MolInfo(formula=hill_formula(counts), mw=mw, exact_mass=counts_mass(counts),
                   charge=charge, elements={("H" if s in ("D", "T") else s) for s, _ in counts},
                   smiles="", counts=Counter(counts))


def species(mol: Chem.Mol) -> list[tuple[str, MolInfo]]:
    """The separate pieces of a structure (e.g. cation and anion of a salt), as
    (canonical SMILES, info), largest first. One entry for an ordinary structure."""
    seen: dict[str, tuple[int, MolInfo]] = {}
    for frag in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True):
        smi = Chem.MolToSmiles(Chem.RemoveHs(frag))
        if smi not in seen:
            seen[smi] = (frag.GetNumHeavyAtoms(), mol_info(frag))
    ordered = sorted(seen.items(), key=lambda kv: -kv[1][0])
    return [(smi, info) for smi, (_n, info) in ordered]


# ---- common counter-ions (SPEC §13f.1): skipped by the automatic HRMS "M" of a salt

# Recognised by structure: the canonical SMILES (without stereo or isotope labels) of each,
# and of the free acid where a salt is often drawn with it (TfOH, TFA, MsOH, TsOH, HCl, HBr).
_COUNTER_ION_SMILES = (
    # halides (and HCl, HBr drawn next to an amine)
    "[F-]", "[Cl-]", "[Br-]", "[I-]", "Cl", "Br",
    # inorganic anions
    "F[B-](F)(F)F", "F[P-](F)(F)(F)(F)F", "F[Sb-](F)(F)(F)(F)F", "F[As-](F)(F)(F)(F)F",
    "[O-]Cl(=O)(=O)=O", "[O-][N+](=O)[O-]", "OS(=O)(=O)[O-]",
    "[O-]S(=O)(=O)[O-]", "OS(=O)(=O)O",                     # sulfate, H2SO4
    "[O-]C([O-])=O", "OC(=O)[O-]",                          # carbonate, bicarbonate
    "[OH-]", "[H+]",                                        # hydroxide, a drawn proton
    # sulfonates, sulfates, carboxylates
    "COS(=O)(=O)[O-]",                                      # methyl sulfate (from Me2SO4)
    "[O-]S(=O)(=O)C(F)(F)F", "OS(=O)(=O)C(F)(F)F",          # triflate, TfOH
    "O=S(=O)([N-]S(=O)(=O)C(F)(F)F)C(F)(F)F",               # NTf2 (triflimide)
    "[O-]C(=O)C(F)(F)F", "OC(=O)C(F)(F)F",                  # trifluoroacetate, TFA
    "CS(=O)(=O)[O-]", "CS(=O)(=O)O",                        # mesylate, MsOH
    "Cc1ccc(S(=O)(=O)[O-])cc1", "Cc1ccc(S(=O)(=O)O)cc1",    # tosylate, TsOH
    # borates
    "FC(F)(F)c1cc(cc(c1)C(F)(F)F)[B-](c1cc(cc(c1)C(F)(F)F)C(F)(F)F)"
    "(c1cc(cc(c1)C(F)(F)F)C(F)(F)F)c1cc(cc(c1)C(F)(F)F)C(F)(F)F",          # BArF
    "Fc1c(F)c(F)c([B-](c2c(F)c(F)c(F)c(F)c2F)(c2c(F)c(F)c(F)c(F)c2F)"
    "c2c(F)c(F)c(F)c(F)c2F)c(F)c1F",                                       # B(C6F5)4
    "c1ccc([B-](c2ccccc2)(c2ccccc2)c2ccccc2)cc1",                          # BPh4
    # cations
    "[Li+]", "[Na+]", "[K+]", "[Rb+]", "[Cs+]", "[NH4+]",
    "C[N+](C)(C)C", "CC[N+](CC)(CC)CC", "CCCC[N+](CCCC)(CCCC)CCCC",        # NMe4, NEt4, NBu4
    "CC[NH+](CC)CC", "CC[NH+](C(C)C)C(C)C",                 # Et3NH, iPr2NEtH (Hünig)
    # PPN (bis(triphenylphosphine)iminium), as it is usually drawn
    "c1ccc(cc1)P(c1ccccc1)(c1ccccc1)=[N+]=P(c1ccccc1)(c1ccccc1)c1ccccc1",
    "c1ccc(cc1)[P+](c1ccccc1)(c1ccccc1)N=P(c1ccccc1)(c1ccccc1)c1ccccc1",
    "c1ccc(cc1)[P+](c1ccccc1)(c1ccccc1)[N-][P+](c1ccccc1)(c1ccccc1)c1ccccc1",
)


def _counter_ion_key(mol: Chem.Mol) -> str:
    """How a piece is compared with the counter-ion list: canonical SMILES without stereo
    or isotope labels, explicit H removed."""
    return Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=False)


def _counter_ion_keys() -> frozenset[str]:
    keys = set()
    for smi in _COUNTER_ION_SMILES:
        mol = Chem.MolFromSmiles(smi)
        if mol is not None:   # (every entry is checked by a test)
            keys.add(_counter_ion_key(mol))
    return frozenset(keys)


_COUNTER_IONS = _counter_ion_keys()


def is_counter_ion(smiles: str) -> bool:
    """Whether a piece of a structure (its SMILES, as species gives it) is a common
    counter-ion: a halide, BF4⁻, PF6⁻, SbF6⁻, AsF6⁻, ClO4⁻, NO3⁻, HSO4⁻, SO4²⁻, CO3²⁻,
    HCO3⁻, OH⁻, MeSO4⁻, OTf⁻, NTf2⁻, TFA⁻, OMs⁻, OTs⁻, BArF⁻, B(C6F5)4⁻, BPh4⁻ (or TfOH,
    TFA, MsOH, TsOH, HCl, HBr, H2SO4, or a proton drawn as H⁺), Li⁺, Na⁺, K⁺, Rb⁺, Cs⁺,
    NH4⁺, NMe4⁺, NEt4⁺, NBu4⁺, Et3NH⁺, iPr2NEtH⁺ or PPN⁺."""
    mol = Chem.MolFromSmiles(smiles)
    return mol is not None and _counter_ion_key(mol) in _COUNTER_IONS


def main_species(pieces: list[tuple[str, MolInfo]]) -> int:
    """Index (in species' list) of the automatic M of a structure with several pieces:
    the largest piece that is not a common counter-ion (so the cation of a triflate,
    NTf2 or BArF salt, and the anion of a sodium or tetrabutylammonium salt), or the
    largest piece when every piece is one (e.g. NBu4⁺ BF4⁻ itself)."""
    return next((i for i, (smi, _info) in enumerate(pieces) if not is_counter_ion(smi)), 0)


def main_piece(mol: Chem.Mol) -> Chem.Mol:
    """The piece of a structure that is "the compound" once common counter-ions are left
    out, by the same rule as main_species (the largest piece that is not a counter-ion, or
    the largest when every piece is one); the structure itself when it is one piece.
    For comparing structures (e.g. the manuscript check) the same way the HRMS M is chosen.
    Works for a molecule that RDKit has not sanitised, and one with a generic atom."""
    pieces = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    if len(pieces) < 2:
        return mol
    ordered = sorted(pieces, key=lambda m: -m.GetNumHeavyAtoms())   # (stable: as species)
    for piece in ordered:
        try:
            key = _counter_ion_key(piece)
        except Exception:  # noqa: BLE001 - an unreadable piece is not a counter-ion
            key = ""
        if key not in _COUNTER_IONS:
            return piece
    return ordered[0]


def unique_carbons(mol: Chem.Mol) -> int:
    """Number of symmetry-distinct carbons (topological symmetry: e.g. 4 for toluene's ring
    + 1 methyl = 5). An approximation of the number of ¹³C signals.
    Stereo labels and atom-map numbers are ignored, so mirror-related carbons of meso and
    cis/trans ring compounds count once; isotope labels (¹³C) still make a carbon distinct."""
    ranks = Chem.CanonicalRankAtoms(mol, breakTies=False, includeChirality=False,
                                    includeAtomMaps=False)
    return len({ranks[a.GetIdx()] for a in mol.GetAtoms() if a.GetSymbol() == "C"})


def hydrogen_counts(mol: Chem.Mol) -> tuple[int, int]:
    """(¹H hydrogens, ¹H on N/O/S — exchangeable, often not seen in ¹H NMR).
    Deuterium and tritium are left out (a ¹H spectrum does not show them); an H drawn as
    its own atom counts once, on itself (GetTotalNumHs of its neighbour leaves it out)."""
    total = exchangeable = 0
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 1:
            if atom.GetIsotope() in (2, 3):
                continue
            total += 1
            if any(n.GetSymbol() in ("N", "O", "S") for n in atom.GetNeighbors()):
                exchangeable += 1
        else:
            h = atom.GetTotalNumHs()   # implicit H and H counts are always ¹H
            total += h
            if atom.GetSymbol() in ("N", "O", "S"):
                exchangeable += h
    return total, exchangeable


def charge_text(charge: int) -> str:
    if not charge:
        return ""
    return (f"{abs(charge)}" if abs(charge) > 1 else "") + ("+" if charge > 0 else "−")


def parse_formula(text: str) -> Counter:
    if not text or _FORMULA_TOKEN.sub("", text):
        raise ChemError(f"“{text}” is not a simple formula like Na, NH4 or HCOO.")
    counts: Counter = Counter()
    for sym, n in _FORMULA_TOKEN.findall(text):
        if sym in ("D", "T"):   # deuterium, tritium (as structures write them)
            counts[(sym, 0)] += int(n or 1)
            continue
        try:
            _PT.GetAtomicNumber(sym)
        except RuntimeError:
            raise ChemError(f"Unknown element “{sym}” in “{text}”.") from None
        counts[(sym, 0)] += int(n or 1)
    return counts


@dataclass
class Adduct:
    label: str            # as typed, e.g. "[M+H]+"
    multimer: int         # 2 for [2M+Na]+
    delta: Counter        # atoms added (negative = removed)
    charge: int           # signed charge of the ion, e.g. +1, -1, +2
    radical: bool = False

    @property
    def display(self) -> str:
        return adduct_display(self.label)


def parse_adduct(label: str) -> Adduct:
    text = label.strip().translate(_ADDUCT_TEXT)
    close = text.rfind("]") + 1
    text = text[:close] + text[close:].translate(_CHARGE_TEXT)
    m = _ADDUCT.match(text)
    if not m:
        raise ChemError(f"“{label}” is not an adduct I can read. Use the form [M+H]+, "
                        "[M+Na]+, [M-H]-, [2M+Na]+, [M+H-H2O]+ or [M+2H]2+.")
    # Every part between M and "]" must be read (a skipped part would give a wrong ion).
    mods = m.group("mods")
    pieces, pos = [], 0
    for p in _MOD.finditer(mods):
        if p.start() != pos:
            break
        pieces.append(p)
        pos = p.end()
    if pos != len(mods):
        raise ChemError(f"In “{label}”, I can't read “{mods[pos:]}”. Use plain formulas "
                        "such as +Na, +NH4 or -H2O.")
    multimer = int(re.match(r"^\[(\d*)", text).group(1) or 1)
    if multimer == 0:
        raise ChemError(f"“{label}”: the number of M can't be 0 (use [M+H]+ or [2M+Na]+).")

    def isotope(i: re.Match) -> str:   # "⁷Li" -> "[7Li]"
        mass_number = int(i.group(1).translate(_CHARGE_TEXT))
        try:
            _isotope(i.group(2), mass_number)
        except ChemError:
            raise ChemError(f"In “{label}”, “{i.group(0)}” is not an isotope I know. A "
                            "superscript number before an element is read as its mass "
                            "number (as in ⁷Li); write a count as H2O or H₂O.") from None
        return f"[{mass_number}{i.group(2)}]"

    delta: Counter = Counter()
    for sign, mult, formula in (p.groups() for p in pieces):
        n = int(mult or 1) * (1 if sign == "+" else -1)
        for key, count in _labelled_formula(_SUPER_ISOTOPE.sub(isotope, formula)).items():
            delta[key] += n * count
    z = int(m.group("z") or 1) * (1 if m.group("sign") == "+" else -1)
    if z == 0:   # m/z would divide by 0
        raise ChemError(f"“{label}”: the ion's charge can't be 0. "
                        "Use e.g. [M+H]+, [M-H]- or [M+2H]2+.")
    return Adduct(label, multimer, delta, z, bool(m.group("rad")))


_SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def adduct_display(label: str) -> str:
    """"[M+NH4]+" -> "[M+NH4]⁺" (charge as superscript; the minus sign as −)."""
    m = re.match(r"^(\[.*\])(\d*[+\-−][•·.]?)$", label.strip())
    if not m:
        return label
    charge = m.group(2).replace("−", "-").replace(".", "•").replace("·", "•")
    body = m.group(1).replace("-", "−")
    return body + charge.translate(_SUPERSCRIPT)


@dataclass
class IonResult:
    adduct: Adduct
    formula: str          # ion formula, e.g. C8H11O3
    mz: float             # calculated m/z


def ion_mz(info: MolInfo, adduct_label: str) -> IonResult:
    """Calculated m/z for the structure with the given adduct. Raises ChemError."""
    adduct = parse_adduct(adduct_label)
    counts = _ion_counts(info, adduct)
    mass = counts_mass(counts) - adduct.charge * ELECTRON_MASS
    return IonResult(adduct, hill_formula(counts), mass / abs(adduct.charge))


def ppm_error(found: float, calculated: float) -> float:
    return (found - calculated) / calculated * 1e6


# ---------------------------------------------------------------------- isotope pattern

@dataclass
class Peak:
    mz: float
    abundance: float      # % of the most intense peak
    formula: str          # isotopes other than the most common are labelled, e.g. C6H6[81Br]
    monoisotopic: bool    # made only of the most common isotopes


_ISOTOPE_CACHE: dict[str, list[tuple[int, float, float]]] = {}


def natural_isotopes(symbol: str) -> list[tuple[int, float, float]]:
    """[(mass number, mass, fraction)] for an element's natural isotopes, most common first."""
    if symbol not in _ISOTOPE_CACHE:
        common = _PT.GetMostCommonIsotope(symbol)
        found = []
        for iso in range(max(1, common - 20), common + 21):
            try:
                fraction = _PT.GetAbundanceForIsotope(symbol, iso) / 100
            except RuntimeError:
                continue
            if fraction > 0:
                found.append((iso, _PT.GetMassForIsotope(symbol, iso), fraction))
        if not found:  # element with no natural isotopes listed: use its most common one
            found = [(common, _PT.GetMostCommonIsotopeMass(symbol), 1.0)]
        found.sort(key=lambda t: t[0] != common)
        _ISOTOPE_CACHE[symbol] = found
    return _ISOTOPE_CACHE[symbol]


def _ion_counts(info: MolInfo, adduct: Adduct) -> Counter:
    counts: Counter = Counter()
    for key, n in info.counts.items():
        counts[key] += n * adduct.multimer
    for key, n in adduct.delta.items():
        counts[key] += n
    if any(n < 0 for n in counts.values()):
        raise ChemError(f"{adduct.label} removes atoms the structure does not have.")
    return +counts


_PRUNE = 1e-7  # ignore isotope combinations this rare (relative to the most likely one)


_PATTERNS: dict[tuple, list[Peak]] = {}   # worked-out patterns (compiling repeats them)
_PATTERNS_MAX = 5000


def isotope_pattern(info: MolInfo, adduct_label: str, min_percent: float = 5.0,
                    max_peaks: int = 6) -> list[Peak]:
    """The ion's main isotope peaks, most intense first (like ChemDraw's "m/z" list).

    Each peak is one isotopic composition (e.g. ⁸¹Br, or one ¹³C). Peaks below
    `min_percent` of the most intense one are left out.
    """
    key = (tuple(sorted(info.counts.items())), adduct_label, min_percent, max_peaks)
    peaks = _PATTERNS.get(key)
    if peaks is None:
        peaks = _isotope_pattern(info, adduct_label, min_percent, max_peaks)
        if len(_PATTERNS) >= _PATTERNS_MAX:
            _PATTERNS.clear()
        _PATTERNS[key] = peaks
    return [replace(p) for p in peaks]   # copies: callers may keep or change them


def _isotope_pattern(info: MolInfo, adduct_label: str, min_percent: float,
                     max_peaks: int) -> list[Peak]:
    adduct = parse_adduct(adduct_label)
    counts = _ion_counts(info, adduct)

    fixed_mass = 0.0
    fixed: Counter = Counter()
    # Combined distribution: {heavy-isotope composition: (mass, probability)}
    states: dict[tuple, tuple[float, float]] = {(): (0.0, 1.0)}
    for (sym, iso), n in sorted(counts.items()):
        if iso or sym in ("D", "T"):  # isotope given in the structure: not a distribution
            fixed[(sym, iso)] += n
            fixed_mass += _atom_mass(sym, iso) * n
            continue
        isotopes = natural_isotopes(sym)
        # Distribution of this element's n atoms over its isotopes, atom by atom.
        element: dict[tuple, float] = {tuple([0] * len(isotopes)): 1.0}
        for _ in range(n):
            nxt: dict[tuple, float] = {}
            for comp, p in element.items():
                for k, (_iso, _mass, frac) in enumerate(isotopes):
                    c = list(comp)
                    c[k] += 1
                    key = tuple(c)
                    nxt[key] = nxt.get(key, 0.0) + p * frac
            top = max(nxt.values())
            element = {k: p for k, p in nxt.items() if p >= top * _PRUNE}
        combined: dict[tuple, tuple[float, float]] = {}
        for key, (mass, p) in states.items():
            for comp, q in element.items():
                heavy = tuple((sym, isotopes[k][0], c) for k, c in enumerate(comp) if k and c)
                m = mass + sum(c * isotopes[k][1] for k, c in enumerate(comp))
                combined[key + heavy] = (m, p * q)
        top = max(p for _m, p in combined.values())
        states = {k: v for k, v in combined.items() if v[1] >= top * _PRUNE}

    top = max(p for _m, p in states.values())
    peaks = []
    for heavy, (mass, p) in states.items():
        percent = p / top * 100
        if percent < min_percent:
            continue
        formula_counts = Counter(fixed)
        for (sym, iso), n in counts.items():
            if not iso and sym not in ("D", "T"):
                formula_counts[(sym, 0)] += n
        for sym, iso, c in heavy:
            formula_counts[(sym, 0)] -= c
            formula_counts[(sym, iso)] += c
        mz = (mass + fixed_mass - adduct.charge * ELECTRON_MASS) / abs(adduct.charge)
        peaks.append(Peak(mz, percent, hill_formula(formula_counts), not heavy))
    peaks.sort(key=lambda pk: (-round(pk.abundance, 6), pk.mz))
    return peaks[:max_peaks]


def nearest_peak(peaks: list[Peak], found: float) -> Peak:
    return min(peaks, key=lambda pk: abs(pk.mz - found))


# ---------------------------------------------------------------------- drawing


ACS_BOND_PT = 14.4                    # ACS 1996: bond length 0.508 cm = 0.2 in = 14.4 pt
BUILTIN_FONTS = {"Roboto (built in)": "BuiltinRobotoRegular",
                 "Telex (built in)": "BuiltinTelexRegular"}
DEFAULT_FONT = "Roboto (built in)"
# Fonts offered for structure labels, with the files that hold them on Windows, macOS and
# Linux. A font missing on this computer falls back to the built-in one.
FONT_FILES = {
    "Arial": ("arial.ttf", "Arial.ttf"),
    "Helvetica": ("Helvetica.ttc", "helvetica.ttf"),
    "Liberation Sans": ("LiberationSans-Regular.ttf",),
    "DejaVu Sans": ("DejaVuSans.ttf",),
    "Calibri": ("calibri.ttf",),
    "Times New Roman": ("times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf"),
}


# Largest values read from project.json: well above the dialog's limits (a hand-edited
# 30 pt font still works) but small enough that drawing cannot fail (inf, 1e300).
_DRAWING_MAX = {"bond_length_cm": 20.0, "line_width_pt": 50.0, "font_size_pt": 200.0}


def _setting_bool(value) -> bool | None:
    """A hand-edited on/off setting: true/false, 0/1, or the words "true", "false", "yes",
    "no", "on", "off", "1", "0". None for anything else (the default is kept)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("true", "1", "yes", "on"):
            return True
        if word in ("false", "0", "no", "off"):
            return False
    return None


@dataclass(frozen=True)
class DrawingSettings:
    """How the program draws structures (Project settings ▸ Structures, SPEC §13k.7).
    The defaults are the ACS 1996 document settings used before this was adjustable."""
    bond_length_cm: float = 0.508
    line_width_pt: float = 0.6
    font: str = DEFAULT_FONT
    font_size_pt: float = 10.0
    colour_atoms: bool = False    # False: all black (ACS); True: O red, N blue, …
    label_methyls: bool = False   # write terminal methyls as CH3
    stereo_labels: bool = False   # print (R)/(S) and (E)/(Z)
    heteroatom_h: bool = True     # NH, OH (False: just N, O)

    @classmethod
    def from_settings(cls, settings: dict | None) -> "DrawingSettings":
        raw = (settings or {}).get("structure_drawing") or {}
        out = cls()
        values = {}
        for name, default in out.__dict__.items():
            if name not in raw:
                continue
            if isinstance(default, bool):   # (before the rest: a bool is also an int)
                value = _setting_bool(raw[name])
                if value is None:
                    continue
            elif isinstance(raw[name], bool):   # (float(True) would be a size of 1)
                continue
            else:
                try:
                    value = type(default)(raw[name])
                except (TypeError, ValueError, OverflowError):
                    continue
                if isinstance(default, float) and not (
                        math.isfinite(value) and 0 < value <= _DRAWING_MAX[name]):
                    continue
            values[name] = value
        return cls(**{**out.__dict__, **values})

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @property
    def bond_pt(self) -> float:
        return self.bond_length_cm / 2.54 * 72


def font_file(name: str) -> str:
    """RDKit's font for `name`: a built-in font, or a font file on this computer
    (the built-in Roboto if it isn't installed here)."""
    if name in BUILTIN_FONTS:
        return BUILTIN_FONTS[name]
    path = _find_font(name)
    return path or BUILTIN_FONTS[DEFAULT_FONT]


def available_fonts() -> list[str]:
    """Fonts that can be used on this computer (built-in ones first)."""
    return list(BUILTIN_FONTS) + [n for n in FONT_FILES if _find_font(n)]


_font_cache: dict[str, str] = {}


def _find_font(name: str) -> str:
    if name not in _font_cache:
        _font_cache[name] = _search_font(FONT_FILES.get(name, ()))
    return _font_cache[name]


def _search_font(files: tuple[str, ...]) -> str:
    import os
    import sys
    from pathlib import Path
    if not files:
        return ""
    dirs = []
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    deep = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            Path.home() / ".local/share/fonts", Path.home() / ".fonts"]
    for d in dirs:
        for f in files:
            if (d / f).is_file():
                return str(d / f)
    if sys.platform not in ("win32", "darwin"):
        for d in deep:
            if not d.is_dir():
                continue
            for f in files:
                hit = next(d.rglob(f), None)
                if hit is not None:
                    return str(hit)
    return ""


def structure_png(mol: Chem.Mol, width: int = 300, height: int = 220,
                  settings: DrawingSettings | None = None) -> bytes:
    """PNG of the structure for the screen, in the project's drawing style. The bond
    length is enlarged so the drawing fills the box (RDKit shrinks it if the molecule
    would not fit); font size and line width keep their proportions to the bond."""
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    _prepare_and_draw(drawer, mol, settings or DrawingSettings(),
                      bond_px=max(min(width, height) / 4, 21))
    return drawer.GetDrawingText()


def structure_svg(mol: Chem.Mol, width: int = -1, height: int = -1,
                  settings: DrawingSettings | None = None) -> str:
    """SVG of the structure at its true size (1 SVG unit = 1 pt; used when compiling the
    SI). width/height of -1 lets RDKit size the drawing to the bond length."""
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    _prepare_and_draw(drawer, mol, settings or DrawingSettings())
    return drawer.GetDrawingText()


def _prepare_and_draw(drawer, mol: Chem.Mol, settings: DrawingSettings,
                      bond_px: float | None = None) -> None:
    """Draw in RDKit's ACS 1996 mode, adjusted by `settings`. `bond_px` (screen) is the
    bond length in pixels; without it the bond is settings.bond_pt points long."""
    mol = Chem.Mol(mol)
    if not _has_2d_coords(mol):
        rdDepictor.Compute2DCoords(mol)
    opts = drawer.drawOptions()
    mean = Draw.MeanBondLength(mol) or 1.5
    bond = bond_px if bond_px is not None else settings.bond_pt  # drawing units per bond
    scale = bond / settings.bond_pt          # drawing units per point
    # ACS mode draws a bond 14.4 units long; passing a scaled mean bond length gives `bond`.
    rdMolDraw2D.SetACS1996Mode(opts, mean * ACS_BOND_PT / bond)
    font_units = settings.font_size_pt * scale
    if bond_px is None and float(font_units).is_integer():
        opts.fixedFontSize = int(font_units)  # exactly as ACS mode (10 pt by default)
    else:
        # Font size as a fraction of the bond, so it shrinks with the drawing on screen.
        opts.fixedFontSize = -1
        opts.baseFontSize = font_units / bond * mean
        opts.minFontSize = 4
        opts.maxFontSize = 400
    opts.bondLineWidth = settings.line_width_pt * scale
    opts.fontFile = font_file(settings.font)
    if settings.colour_atoms:
        opts.useDefaultAtomPalette()
    opts.explicitMethyl = settings.label_methyls
    opts.addStereoAnnotation = settings.stereo_labels
    opts.setBackgroundColour((1, 1, 1, 1))
    opts.clearBackground = True
    prepared = rdMolDraw2D.PrepareMolForDrawing(mol, kekulize=True)
    if not settings.heteroatom_h:
        for atom in prepared.GetAtoms():
            if (atom.GetAtomicNum() not in (1, 6) and atom.GetTotalNumHs() > 0
                    and not atom.GetFormalCharge() and not atom.GetIsotope()
                    and not atom.GetNumRadicalElectrons()):
                opts.atomLabels[atom.GetIdx()] = atom.GetSymbol()
    drawer.DrawMolecule(prepared)
    drawer.FinishDrawing()
