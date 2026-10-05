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


def _centroid_x(mol: Chem.Mol) -> float:
    if not mol.GetNumConformers():
        return 0.0
    return float(mol.GetConformer().GetPositions()[:, 0].mean())


def scheme_pieces(text: str) -> list[Chem.Mol]:
    """The structures in a copied scheme, ordered left to right.

    `text` is what ChemDraw puts on the clipboard: a reaction ($RXN) or a MOL
    block holding every structure of the scheme as separate pieces. Pieces with
    fewer than two heavy atoms (e.g. a lone counter-ion) are left out. Raises ChemError.
    """
    text = (text or "").strip("﻿")
    if text.lstrip().startswith("$RXN"):
        from rdkit.Chem import rdChemReactions
        try:
            rxn = rdChemReactions.ReactionFromRxnBlock(text)
        except Exception:  # noqa: BLE001 — RDKit raises several types here
            rxn = None
        if rxn is None:
            raise ChemError("The copied reaction could not be read.")
        mols = list(rxn.GetReactants()) + list(rxn.GetProducts())
    else:
        mols = list(Chem.GetMolFrags(parse_structure(text), asMols=True, sanitizeFrags=True))
    pieces = []
    for m in mols:
        m = Chem.Mol(m)
        try:
            Chem.SanitizeMol(m)
        except Exception:  # noqa: BLE001
            continue
        if m.GetNumHeavyAtoms() >= 2:
            if not _has_2d_coords(m):
                rdDepictor.Compute2DCoords(m)
            pieces.append(m)
    return sorted(pieces, key=_centroid_x)


def rightmost_structure(text: str) -> tuple[Chem.Mol, int]:
    """The right-most structure in a copied scheme, and how many structures there were."""
    pieces = scheme_pieces(text)
    if not pieces:
        raise ChemError("No structures were found in the copied scheme.")
    return pieces[-1], len(pieces)


def same_structure_key(mol: Chem.Mol, stereo: bool = True) -> str:
    """A key that is equal for the same compound however it was drawn: canonical SMILES of
    the largest piece (so a salt matches its organic ion), optionally ignoring stereo."""
    pieces = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    largest = max(pieces, key=lambda m: m.GetNumHeavyAtoms())
    return Chem.MolToSmiles(Chem.RemoveHs(largest), isomericSmiles=stereo)

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


def element_counts(mol: Chem.Mol) -> Counter:
    """Counts keyed by (symbol, isotope); isotope 0 = natural (most abundant)."""
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


def mol_info(mol: Chem.Mol) -> MolInfo:
    counts = element_counts(mol)
    elements = {("H" if s in ("D", "T") else s) for (s, _i) in counts}
    return MolInfo(
        formula=hill_formula(counts),
        mw=Descriptors.MolWt(mol),
        exact_mass=counts_mass(counts),
        charge=Chem.GetFormalCharge(mol),
        elements=elements,
        smiles=Chem.MolToSmiles(Chem.RemoveHs(mol)),
        counts=counts,
    )


# ---------------------------------------------------------------------- adducts

_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")
_ADDUCT = re.compile(r"^\[(\d*)M(?P<mods>(?:[+\-−][^+\-−\]]+)*)\](?P<z>\d*)(?P<sign>[+\-−])(?P<rad>[•·.]?)$")
_MOD = re.compile(r"([+\-−])(\d*)([A-Za-z0-9]+)")


_ISOTOPE_TOKEN = re.compile(r"\[(\d+)([A-Z][a-z]?)\](\d*)")
_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def parse_m_formula(text: str) -> Counter:
    """Parse a formula typed by the user for M, e.g. "C12H12N", "C₁₂H₁₂N⁺", "C5[13C]H6Br".

    Spaces, subscript digits and a trailing charge sign are ignored. Raises ChemError.
    """
    cleaned = (text or "").translate(_SUBSCRIPT_DIGITS).replace(" ", "")
    # Drop a trailing charge sign only (not digits: in "C6H8N2+" the 2 belongs to N).
    cleaned = re.sub(r"[+\-−⁺⁻•·]+$", "", cleaned)
    if not cleaned:
        raise ChemError("The formula is empty.")
    counts: Counter = Counter()
    for iso, sym, n in _ISOTOPE_TOKEN.findall(cleaned):
        counts[(sym, int(iso))] += int(n or 1)
    rest = _ISOTOPE_TOKEN.sub("", cleaned)
    if rest:
        counts.update(parse_formula(rest))
    return counts


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


def unique_carbons(mol: Chem.Mol) -> int:
    """Number of symmetry-distinct carbons (topological symmetry: e.g. 4 for toluene's ring
    + 1 methyl = 5). An approximation of the number of ¹³C signals."""
    ranks = Chem.CanonicalRankAtoms(mol, breakTies=False)
    return len({ranks[a.GetIdx()] for a in mol.GetAtoms() if a.GetSymbol() == "C"})


def hydrogen_counts(mol: Chem.Mol) -> tuple[int, int]:
    """(all hydrogens, hydrogens on N/O/S — exchangeable, often not seen in ¹H NMR)."""
    total = exchangeable = 0
    for atom in mol.GetAtoms():
        h = atom.GetTotalNumHs()
        if atom.GetSymbol() == "H":
            h += 1
        total += h
        if atom.GetSymbol() in ("N", "O", "S"):
            exchangeable += atom.GetTotalNumHs()
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
        if sym == "D":
            counts[("D", 0)] += int(n or 1)
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
    text = label.strip().replace(" ", "").replace("−", "-")
    m = _ADDUCT.match(text)
    if not m:
        raise ChemError(f"“{label}” is not an adduct I can read. Use the form [M+H]+, "
                        "[M+Na]+, [M-H]-, [2M+Na]+, [M+H-H2O]+ or [M+2H]2+.")
    multimer = int(re.match(r"^\[(\d*)", text).group(1) or 1)
    delta: Counter = Counter()
    for sign, mult, formula in _MOD.findall(m.group("mods")):
        n = int(mult or 1) * (1 if sign == "+" else -1)
        for key, count in parse_formula(formula).items():
            delta[key] += n * count
    z = int(m.group("z") or 1) * (1 if m.group("sign") == "+" else -1)
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
            try:
                value = type(default)(raw[name])
            except (TypeError, ValueError):
                continue
            if isinstance(default, float) and not value > 0:
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
