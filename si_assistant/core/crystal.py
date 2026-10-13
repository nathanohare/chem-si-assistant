"""Crystal structures for the SI (SPEC §13v): the atoms of a CIF data block in Cartesian
space, the molecule completed by symmetry, its bonds, and the crystal data table.

* `structure_from_cif(block)`: the cell, the symmetry operations, the atoms of the
  asymmetric unit (element, fractional position, displacement parameters U as a 3 × 3
  Cartesian matrix in Å², occupancy, disorder part).
* `grow(structure)`: the molecules — every atom reached from the asymmetric unit
  through bonds, also to atoms of symmetry-related copies (a molecule on an inversion
  centre is completed), each copy once. Bonds come from the covalent radii (Cordero et
  al. 2008) + 0.45 Å; atoms of two different disorder parts (1 and 2) are never bonded.
* `crystal_table(block)`: the crystal data and structure refinement table, in the order
  journals use (Olex2's "Table 1"): each row (label, value) in the stored text format;
  rows with no value in the CIF are left out.

Pure Python + numpy (no Qt): unit-tested with made-up structures.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .cif import CifBlock, cif_markup, cif_plain, number

# Covalent radii in Å (Cordero et al., Dalton Trans. 2008, 2832; sp3 C).
COVALENT = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66,
    "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41, "Al": 1.21, "Si": 1.11, "P": 1.07, "S": 1.05,
    "Cl": 1.02, "Ar": 1.06, "K": 2.03, "Ca": 1.76, "Sc": 1.70, "Ti": 1.60, "V": 1.53, "Cr": 1.39,
    "Mn": 1.39, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22, "Ga": 1.22,
    "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20, "Kr": 1.16, "Rb": 2.20, "Sr": 1.95,
    "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54, "Tc": 1.47, "Ru": 1.46, "Rh": 1.42,
    "Pd": 1.39, "Ag": 1.45, "Cd": 1.44, "In": 1.42, "Sn": 1.39, "Sb": 1.39, "Te": 1.38,
    "I": 1.39, "Xe": 1.40, "Cs": 2.44, "Ba": 2.15, "La": 2.07, "Ce": 2.04, "Pr": 2.03,
    "Nd": 2.01, "Sm": 1.98, "Eu": 1.98, "Gd": 1.96, "Tb": 1.94, "Dy": 1.92, "Ho": 1.92,
    "Er": 1.89, "Tm": 1.90, "Yb": 1.87, "Lu": 1.87, "Hf": 1.75, "Ta": 1.70, "W": 1.62,
    "Re": 1.51, "Os": 1.44, "Ir": 1.41, "Pt": 1.36, "Au": 1.36, "Hg": 1.32, "Tl": 1.45,
    "Pb": 1.46, "Bi": 1.48, "U": 1.96, "D": 0.31,
}
BOND_TOLERANCE = 0.45            # Å added to the sum of two covalent radii
MAX_ATOMS = 2000                 # growing stops here (a polymer or a network)


@dataclass
class Atom:
    label: str                   # "C1", "O2A"
    element: str                 # "C", "Cl"
    frac: np.ndarray             # fractional position
    u: np.ndarray                # 3 × 3 Cartesian U (Å²); isotropic: Uiso × I
    anisotropic: bool = False
    occupancy: float = 1.0
    part: str = ""               # disorder part ("" = none; "1", "2", "-1"…)
    riding: bool = False         # a hydrogen placed by the refinement ("calc", "riding")


@dataclass
class Structure:
    name: str
    cell: tuple[float, float, float, float, float, float]   # a, b, c (Å), α, β, γ (°)
    symops: list[tuple[np.ndarray, np.ndarray]]              # (rotation, translation)
    atoms: list[Atom]
    orth: np.ndarray = field(default_factory=lambda: np.eye(3))   # fractional → Å

    def cart(self, frac: np.ndarray) -> np.ndarray:
        return self.orth @ frac


@dataclass
class GrownAtom:
    atom: Atom                   # the asymmetric unit's atom it copies
    xyz: np.ndarray              # Cartesian position (Å)
    u: np.ndarray                # Cartesian U of the copy (rotated by the symmetry)
    op: int = 0                  # the symmetry operation (index) and lattice shift
    shift: tuple = (0, 0, 0)
    molecule: int = 0            # which molecule (connected piece) it belongs to
    own: bool = False            # the asymmetric unit's own atom (not a copy): labelled

    @property
    def label(self) -> str:
        return self.atom.label

    @property
    def element(self) -> str:
        return self.atom.element


@dataclass
class Grown:
    atoms: list[GrownAtom]
    bonds: list[tuple[int, int]]
    molecules: int = 0
    complete: bool = True         # False: stopped at MAX_ATOMS (a polymer)


# ---------------------------------------------------------------------- the structure


class CrystalError(Exception):
    """A CIF that holds no structure the program can draw, in plain words."""


def orthogonalisation(a, b, c, alpha, beta, gamma) -> np.ndarray:
    """Fractional → Cartesian (Å): a along x, b in the xy plane (the usual convention)."""
    al, be, ga = (math.radians(v) for v in (alpha, beta, gamma))
    v = math.sqrt(max(1e-12, 1 - math.cos(al) ** 2 - math.cos(be) ** 2 - math.cos(ga) ** 2
                      + 2 * math.cos(al) * math.cos(be) * math.cos(ga)))
    return np.array([
        [a, b * math.cos(ga), c * math.cos(be)],
        [0.0, b * math.sin(ga), c * (math.cos(al) - math.cos(be) * math.cos(ga)) / math.sin(ga)],
        [0.0, 0.0, c * v / math.sin(ga)],
    ])


def parse_symop(text: str) -> tuple[np.ndarray, np.ndarray]:
    """"-x+1/2, y+1/2, -z+1/2" → (rotation, translation)."""
    text = cif_plain(text).strip().strip("'\"")
    parts = [p.strip() for p in text.lower().split(",")]
    if len(parts) != 3:
        raise CrystalError(f"The symmetry operation “{text}” could not be read.")
    rot = np.zeros((3, 3))
    trans = np.zeros(3)
    for row, part in enumerate(parts):
        for sign, coef, var in re.findall(r"([+-]?)\s*([\d./]*)\s*\*?\s*([xyz]?)",
                                          part.replace(" ", "")):
            if not coef and not var:
                continue
            value = 1.0
            if coef:
                if "/" in coef:
                    num, _, den = coef.partition("/")
                    value = float(num) / float(den)
                else:
                    value = float(coef)
            if sign == "-":
                value = -value
            if var:
                rot[row, "xyz".index(var)] += value
            else:
                trans[row] += value
    return rot, trans


def element_of(label: str, type_symbol: str | None) -> str:
    """The element of an atom: its type symbol ("C", "O2-", "Cl1-" → "C", "O", "Cl"),
    else the letters its label starts with ("CL1" → "Cl", "C12A" → "C")."""
    for source in (type_symbol, label):
        if not source:
            continue
        m = re.match(r"([A-Za-z]{1,2})", source)
        if not m:
            continue
        letters = m.group(1)
        two = letters[:1].upper() + letters[1:2].lower()
        if len(letters) == 2 and two in COVALENT and (source is type_symbol or
                                                      two not in ("Cs", "Ca", "Co", "Cu",
                                                                  "Cd", "Ce", "Hf", "Os",
                                                                  "Np", "Nb", "Sc", "Sn",
                                                                  "In", "Ho", "Pb")):
            return two
        one = letters[:1].upper()
        if one in COVALENT:
            return one
        if two in COVALENT:
            return two
    return "C"


def structure_from_cif(block: CifBlock) -> Structure:
    """The structure of a CIF data block (see the module's text)."""
    cell = tuple(number(block.get(f"_cell_{k}")) for k in
                 ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma"))
    if any(v is None or v <= 0 for v in cell):
        raise CrystalError("The CIF has no complete unit cell (a, b, c, α, β, γ).")
    orth = orthogonalisation(*cell)
    ops_text = []
    for tag in ("_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz"):
        loop = block.loop_with(tag)
        if loop is not None:
            ops_text = loop[tag]
            break
    if not ops_text and block.get("_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz"):
        ops_text = [block.get("_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz")]
    symops = []
    for text in ops_text or ["x, y, z"]:
        try:
            symops.append(parse_symop(text))
        except (CrystalError, ValueError, ZeroDivisionError):
            continue
    if not symops:
        symops = [(np.eye(3), np.zeros(3))]
    rows = block.rows("_atom_site_label")
    if not rows:
        raise CrystalError("The CIF lists no atoms (no _atom_site_ table).")
    aniso = {r["_atom_site_aniso_label"]: r for r in block.rows("_atom_site_aniso_label")}
    # U (in the CIF, relative to the reciprocal axes) → Cartesian: A N U N Aᵀ
    recip = np.linalg.inv(orth).T
    n = np.diag([np.linalg.norm(recip[:, k]) for k in range(3)])
    atoms = []
    for r in rows:
        label = r["_atom_site_label"]
        frac = [number(r.get(f"_atom_site_fract_{k}")) for k in "xyz"]
        if any(v is None for v in frac):
            continue
        element = element_of(label, r.get("_atom_site_type_symbol"))
        uiso = number(r.get("_atom_site_u_iso_or_equiv"))
        if uiso is None and number(r.get("_atom_site_b_iso_or_equiv")) is not None:
            uiso = number(r.get("_atom_site_b_iso_or_equiv")) / (8 * math.pi ** 2)
        u = np.eye(3) * (uiso if uiso and uiso > 0 else 0.03)
        anisotropic = False
        a = aniso.get(label)
        if a is not None:
            vals = [number(a.get(f"_atom_site_aniso_u_{ij}")) for ij in
                    ("11", "22", "33", "12", "13", "23")]
            if all(v is not None for v in vals):
                u11, u22, u33, u12, u13, u23 = vals
                ucif = np.array([[u11, u12, u13], [u12, u22, u23], [u13, u23, u33]])
                u = orth @ n @ ucif @ n @ orth.T
                anisotropic = True
        occ = number(r.get("_atom_site_occupancy"))
        part = r.get("_atom_site_disorder_group") or ""
        flag = (r.get("_atom_site_calc_flag") or "").lower()
        atoms.append(Atom(label, element, np.array(frac, float), u, anisotropic,
                          occ if occ is not None else 1.0, "" if part in (".", "?") else part,
                          element in ("H", "D") and flag in ("calc", "c", "r", "riding")))
    if not atoms:
        raise CrystalError("The CIF's atoms have no positions.")
    return Structure(block.name, cell, symops, atoms, orth)


# ---------------------------------------------------------------------- growing


def _bonded(a: Atom, b: Atom, distance: float) -> bool:
    if distance < 0.1:
        return False
    if a.part and b.part and a.part != b.part and a.part not in ("0",) and b.part not in ("0",):
        return False                       # (two parts of a disorder)
    if a.element in ("H", "D") and b.element in ("H", "D"):
        return False
    limit = COVALENT.get(a.element, 1.5) + COVALENT.get(b.element, 1.5) + BOND_TOLERANCE
    return distance <= limit


def grow(s: Structure) -> Grown:
    """The molecules of a structure: the asymmetric unit completed through bonds to its
    symmetry-related copies (see the module's text)."""
    ops = s.symops
    # every copy is (atom index, op, lattice shift); the asymmetric unit is op 0 (x, y, z)
    identity = next((k for k, (r, t) in enumerate(ops) if np.allclose(r, np.eye(3))
                     and np.allclose(t, 0)), 0)

    def frac_of(i: int, op: int, shift: tuple) -> np.ndarray:
        r, t = ops[op]
        return r @ s.atoms[i].frac + t + np.array(shift, float)

    placed: list[GrownAtom] = []
    where: dict[tuple, int] = {}           # position (to 0.005 Å or so) → index

    def key(frac: np.ndarray) -> tuple:
        return tuple(int(round(v * 2000)) for v in frac)

    def add(i: int, op: int, shift: tuple) -> int | None:
        f = frac_of(i, op, shift)
        k = key(f)
        if k in where:                     # (an atom on a special position: once)
            return where[k]
        if len(placed) >= MAX_ATOMS:
            return None
        r, _t = ops[op]
        rc = s.orth @ r @ np.linalg.inv(s.orth)          # the rotation in Cartesian space
        placed.append(GrownAtom(s.atoms[i], s.orth @ f, rc @ s.atoms[i].u @ rc.T, op, shift))
        where[k] = len(placed) - 1
        return len(placed) - 1

    for i in range(len(s.atoms)):
        k = add(i, identity, (0, 0, 0))
        if k is not None:
            placed[k].own = True
    # Every copy of every atom that one placed atom is bonded to (each operation, at the
    # lattice shift nearest to it and the shifts next to that) is placed too, and so on.
    to_frac = np.linalg.inv(s.orth)
    fracs = np.array([a.frac for a in s.atoms])                      # n × 3
    images = [(fracs @ r.T) + t for r, t in ops]                      # per operation
    shifts = np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)],
                      float)
    reach = 2 * max(COVALENT.get(a.element, 1.5) for a in s.atoms) + BOND_TOLERANCE
    complete = True
    queue = list(range(len(placed)))
    queued = set(queue)
    while queue:
        here = placed[queue.pop(0)]
        here_frac = to_frac @ here.xyz
        for op, f0 in enumerate(images):
            near = np.round(here_frac - f0)                              # n × 3
            cand = (f0 + near)[:, None, :] + shifts[None, :, :]          # n × 27 × 3
            d = np.linalg.norm(cand @ s.orth.T - here.xyz, axis=2)        # n × 27
            for j, k in zip(*np.nonzero(d <= reach)):
                if not _bonded(here.atom, s.atoms[j], float(d[j, k])):
                    continue
                shift = tuple(int(v) for v in near[j] + shifts[k])
                g = add(int(j), op, shift)
                if g is None:
                    complete = False
                elif g not in queued:
                    queued.add(g)
                    queue.append(g)
    bonds = []
    xyz = np.array([g.xyz for g in placed])
    for i in range(len(placed)):
        d = np.linalg.norm(xyz[i + 1:] - xyz[i], axis=1)
        for k in np.nonzero(d < 4.0)[0]:
            j = i + 1 + int(k)
            if _bonded(placed[i].atom, placed[j].atom, float(d[k])):
                bonds.append((i, j))
    # molecules: connected pieces
    parent = list(range(len(placed)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in bonds:
        parent[find(i)] = find(j)
    roots: dict[int, int] = {}
    for i, g in enumerate(placed):
        g.molecule = roots.setdefault(find(i), len(roots))
    return Grown(placed, bonds, len(roots), complete)


# ---------------------------------------------------------------------- the table


def _formula_markup(text: str) -> str:
    """"C12 H14 N O2" → "C_{12}H_{14}NO_{2}"."""
    out = []
    for el, count in re.findall(r"([A-Z][a-z]?)\s*([\d.]*)", text):
        out.append(el + (f"_{{{count}}}" if count and count != "1" else ""))
    return "".join(out) or cif_markup(text)


def space_group_markup(text: str) -> str:
    """A Hermann–Mauguin symbol as printed: "P 21/c" → "*P*2_{1}/*c*", "P -1" → "*P*1̄";
    a monoclinic full symbol in its short form, as journals print it ("P 1 21/c 1" →
    *P*2_{1}/*c*)."""
    out = []
    parts = cif_plain(text).replace("_", " ").split()
    if len(parts) == 4 and parts.count("1") == 2:
        parts = [parts[0]] + [p for p in parts[1:] if p != "1"]
    for part in parts:
        part = re.sub(r"-(\d)", lambda m: m.group(1) + "̅", part)
        part = re.sub(r"(?<![\d_])(\d)(\d)(?!\d)", r"\1_{\2}", part)   # screw axes 21, 41…
        part = re.sub(r"([A-Za-z])", r"*\1*", part)
        out.append(part)
    return "".join(out).replace("**", "")


def _value(block: CifBlock, tag: str, unit: str = "") -> str | None:
    v = block.get(tag)
    return (cif_markup(v) + unit) if v is not None else None


def _join(*parts: str | None, sep: str = ", ") -> str | None:
    found = [p for p in parts if p]
    return sep.join(found) if found else None


def crystal_table(block: CifBlock, keep_empty: bool = False) -> list[tuple[str, str, str]]:
    """[(key, label, value)] of the crystal data and structure refinement table, in the
    stored text format (see the module's text). Rows the CIF has no value for are left
    out, or (keep_empty) kept with "" (to be typed in, e.g. a CCDC number given later)."""
    g = block.get
    rows: list[tuple[str, str, str | None]] = []
    ccdc = g("_database_code_depnum_ccdc_archive", "_database_code_ccdc")
    if ccdc:
        ccdc = re.sub(r"(?i)^CCDC\s*", "", cif_plain(ccdc)).strip()
    rows.append(("code", "Identification code", block.name or None))
    rows.append(("ccdc", "CCDC deposition number", ccdc))
    formula = g("_chemical_formula_sum")
    rows.append(("formula", "Empirical formula", _formula_markup(formula) if formula else None))
    rows.append(("weight", "Formula weight", _value(block, "_chemical_formula_weight")))
    temp = g("_diffrn_ambient_temperature", "_cell_measurement_temperature")
    rows.append(("temperature", "Temperature/K", cif_markup(temp) if temp else None))
    rows.append(("system", "Crystal system", _value(block, "_space_group_crystal_system")
                 or _value(block, "_symmetry_cell_setting")))
    sg = g("_space_group_name_h-m_alt", "_symmetry_space_group_name_h-m")
    rows.append(("space_group", "Space group", space_group_markup(sg) if sg else None))
    for key, label, tag in (("a", "*a*/Å", "_cell_length_a"), ("b", "*b*/Å", "_cell_length_b"),
                            ("c", "*c*/Å", "_cell_length_c"), ("alpha", "α/°", "_cell_angle_alpha"),
                            ("beta", "β/°", "_cell_angle_beta"), ("gamma", "γ/°", "_cell_angle_gamma"),
                            ("volume", "Volume/Å^{3}", "_cell_volume"),
                            ("z", "*Z*", "_cell_formula_units_z"),
                            ("density", "ρ_{calc}/g cm^{−3}", "_exptl_crystal_density_diffrn"),
                            ("mu", "μ/mm^{−1}", "_exptl_absorpt_coefficient_mu"),
                            ("f000", "*F*(000)", "_exptl_crystal_f_000")):
        rows.append((key, label, _value(block, tag)))
    size = [g(f"_exptl_crystal_size_{k}") for k in ("max", "mid", "min")]
    rows.append(("size", "Crystal size/mm^{3}",
                 " × ".join(cif_plain(s) for s in size) if all(size) else None))
    rad = g("_diffrn_radiation_type")
    wave = g("_diffrn_radiation_wavelength")
    if rad:                                    # "MoK\a", "Mo K\a", "CuKa" → "Mo Kα"
        rad = re.sub(r"^\s*([A-Z][a-z]?)\s*K\s*(?:α|a\b)", r"\1 Kα", cif_plain(rad))
    rows.append(("radiation", "Radiation", _join(
        cif_markup(rad) if rad else None,
        (f"(λ = {cif_plain(wave)})" if rad else f"λ = {cif_plain(wave)}") if wave else None,
        sep=" ")))
    t0, t1 = number(g("_diffrn_reflns_theta_min")), number(g("_diffrn_reflns_theta_max"))
    rows.append(("two_theta", "2Θ range for data collection/°",
                 f"{2 * t0:.2f} to {2 * t1:.2f}" if t0 is not None and t1 is not None else None))
    limits = []
    for axis in "hkl":
        lo, hi = g(f"_diffrn_reflns_limit_{axis}_min"), g(f"_diffrn_reflns_limit_{axis}_max")
        if lo is not None and hi is not None:
            limits.append(f"{lo.replace('-', '−')} ≤ *{axis}* ≤ {hi}")
    rows.append(("index", "Index ranges", ", ".join(limits) if len(limits) == 3 else None))
    rows.append(("collected", "Reflections collected", _value(block, "_diffrn_reflns_number")))
    total = g("_reflns_number_total")
    rint = g("_diffrn_reflns_av_r_equivalents")
    rsig = g("_diffrn_reflns_av_uneti/neti", "_diffrn_reflns_av_sigmai/neti")
    extra = _join(f"*R*_{{int}} = {rint}" if rint else None,
                  f"*R*_{{sigma}} = {rsig}" if rsig else None)
    rows.append(("independent", "Independent reflections",
                 (total + (f" [{extra}]" if extra else "")) if total else None))
    drp = [g("_refine_ls_number_reflns"), g("_refine_ls_number_restraints"),
           g("_refine_ls_number_parameters")]
    rows.append(("data", "Data/restraints/parameters",
                 "/".join(drp) if all(v is not None for v in drp) else None))
    rows.append(("gof", "Goodness-of-fit on *F*^{2}", _value(block, "_refine_ls_goodness_of_fit_ref")))
    r1, wr2 = g("_refine_ls_r_factor_gt"), g("_refine_ls_wr_factor_gt")
    rows.append(("r_gt", "Final *R* indexes [*I* ≥ 2σ(*I*)]",
                 _join(f"*R*_{{1}} = {r1}" if r1 else None, f"w*R*_{{2}} = {wr2}" if wr2 else None)))
    r1a, wr2a = g("_refine_ls_r_factor_all"), g("_refine_ls_wr_factor_ref")
    rows.append(("r_all", "Final *R* indexes [all data]",
                 _join(f"*R*_{{1}} = {r1a}" if r1a else None, f"w*R*_{{2}} = {wr2a}" if wr2a else None)))
    peak, hole = g("_refine_diff_density_max"), g("_refine_diff_density_min")
    rows.append(("peak", "Largest diff. peak/hole/e Å^{−3}",
                 f"{peak}/{hole.replace('-', '−')}" if peak and hole else None))
    rows.append(("flack", "Flack parameter", _value(block, "_refine_ls_abs_structure_flack")))
    return [(k, label, v or "") for k, label, v in rows if v or keep_empty]
