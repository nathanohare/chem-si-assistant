"""X-ray structures, phase X1 (SPEC §13v): reading CIF files, the structure, the
molecule completed by symmetry, and the crystal data table. Made-up structures only."""

import math
from pathlib import Path

import numpy as np
import pytest

from si_assistant.core import cif, crystal

# ---------------------------------------------------------------------- CIF syntax


def test_cif_syntax():
    blocks = cif.read_cif(
        "#\\#CIF_1.1\n"
        "# a comment\n"
        "data_first\n"
        "_cell_length_a   10.123(4)   # comment after a value\n"
        "_diffrn_radiation_type  'Mo K\\a'\n"
        "_chemical_name_common  \"it's quoted\"\n"
        "_publ_section_title\n"
        ";\n"
        "A text field over\n"
        "two lines with 'quotes' and _not_a_tag\n"
        ";\n"
        "_exptl_crystal_colour ?\n"
        "loop_\n"
        "_atom_site_label\n"
        "_atom_site_fract_x\n"
        "C1 0.1 C2 0.2\n"
        "C3\n"
        "0.3\n"
        "data_second\n"
        "_cell_length_a 5\n")
    first, second = blocks
    assert (first.name, second.name) == ("first", "second")
    assert first.get("_CELL_LENGTH_A") == "10.123(4)"
    assert first.get("_diffrn_radiation_type") == "Mo K\\a"
    assert first.get("_chemical_name_common") == "it's quoted"
    assert first.get("_publ_section_title") == ("A text field over\n"
                                                "two lines with 'quotes' and _not_a_tag")
    assert first.get("_exptl_crystal_colour") is None          # ("?": unknown)
    assert [r["_atom_site_label"] for r in first.rows("_atom_site_label")] == ["C1", "C2", "C3"]
    assert first.rows("_atom_site_fract_x")[2]["_atom_site_fract_x"] == "0.3"
    assert second.get("_cell_length_a") == "5"


def test_cif_numbers_and_text_codes():
    assert cif.number("12.345(6)") == 12.345 and cif.uncertainty("12.345(6)") == \
        pytest.approx(0.006)
    assert cif.number("1.2e-3") == 0.0012 and cif.number("-0.5") == -0.5
    assert cif.number("?") is None and cif.number(".") is None and cif.number("abc") is None
    assert cif.cif_plain("Mo K\\a, \\q, \\%A, 90\\%") == "Mo Kα, θ, Å, 90°"
    assert cif.cif_markup("R~int~ and \\%A^3^") == "R_{int} and Å^{3}"


def test_damaged_cifs_never_raise():
    assert cif.read_cif("") == []
    # a tag with no value, a loop cut short: what can be read is read
    block = cif.read_cif("data_x\n_cell_length_a\nloop_\n_a\n_b\n1\n2\n3\n")[0]
    assert block.rows("_a") == [{"_a": "1", "_b": "2"}]
    block = cif.read_cif("data_x\n_cell_length_a 10\n")[0]
    with pytest.raises(crystal.CrystalError, match="unit cell"):
        crystal.structure_from_cif(block)


# ---------------------------------------------------------------------- the structure


def test_symmetry_operations():
    r, t = crystal.parse_symop("-x+1/2, y+1/2, -z+1/2")
    assert np.allclose(r, np.diag([-1, 1, -1])) and np.allclose(t, [0.5, 0.5, 0.5])
    r, t = crystal.parse_symop("1/2+X, 1/2-Y, Z")
    assert np.allclose(r, np.diag([1, -1, 1])) and np.allclose(t, [0.5, 0.5, 0])
    r, t = crystal.parse_symop("x-y, x, z+1/6")
    assert np.allclose(r, [[1, -1, 0], [1, 0, 0], [0, 0, 1]]) and np.allclose(t, [0, 0, 1 / 6])


@pytest.mark.parametrize("label, symbol, element", [
    ("C12A", None, "C"), ("CL1", None, "Cl"), ("Cl1", "Cl", "Cl"), ("O2", "O2-", "O"),
    ("Cu1", "Cu", "Cu"), ("Co1", None, "C"), ("BR3", None, "Br"), ("H1A", None, "H"),
    ("N1", "N", "N"), ("Si1", None, "Si")])
def test_elements(label, symbol, element):
    # ("Co1" without a type symbol is a carbon: labels like "Co1" mean C, o…; the CIF's
    # type symbol says cobalt when it is)
    assert crystal.element_of(label, symbol) == element


def test_the_cell():
    m = crystal.orthogonalisation(10, 12, 14, 90, 105, 90)
    volume = abs(np.linalg.det(m))
    assert volume == pytest.approx(10 * 12 * 14 * math.sin(math.radians(105)))
    assert np.allclose(m @ [1, 0, 0], [10, 0, 0])            # (a along x)


def cif_text(atoms, cell=(20, 20, 20, 90, 90, 90), ops=("x, y, z", "-x, -y, -z"),
             extra="", aniso=()):
    """A made-up CIF: atoms (label, element, Cartesian x, y, z in Å, Uiso[, part]) in an
    orthogonal or general cell; `aniso` rows (label, U11, U22, U33, U12, U13, U23)."""
    orth = crystal.orthogonalisation(*cell)
    inv = np.linalg.inv(orth)
    lines = ["data_made_up"] + [f"_cell_{k} {v}" for k, v in zip(
        ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma"), cell)]
    lines += ["loop_", "_space_group_symop_operation_xyz"] + [f"'{o}'" for o in ops]
    lines += ["loop_", "_atom_site_label", "_atom_site_type_symbol", "_atom_site_fract_x",
              "_atom_site_fract_y", "_atom_site_fract_z", "_atom_site_U_iso_or_equiv",
              "_atom_site_disorder_group"]
    for a in atoms:
        label, element, x, y, z, u = a[:6]
        part = a[6] if len(a) > 6 else "."
        f = inv @ [x, y, z]
        lines.append(f"{label} {element} {f[0]:.5f} {f[1]:.5f} {f[2]:.5f} {u} {part}")
    if aniso:
        lines += ["loop_", "_atom_site_aniso_label"] + [
            f"_atom_site_aniso_U_{ij}" for ij in ("11", "22", "33", "12", "13", "23")]
        lines += [" ".join(str(v) for v in row) for row in aniso]
    return "\n".join(lines) + "\n" + extra


def structure(text) -> crystal.Structure:
    return crystal.structure_from_cif(cif.read_cif(text)[0])


def test_displacement_parameters():
    # in an orthogonal cell the CIF's U is the Cartesian U
    s = structure(cif_text([("C1", "C", 1, 1, 1, 0.02)],
                           aniso=[("C1", 0.03, 0.02, 0.025, 0.002, 0.001, 0.0)]))
    assert s.atoms[0].anisotropic
    assert np.allclose(s.atoms[0].u, [[0.03, 0.002, 0.001], [0.002, 0.02, 0], [0.001, 0, 0.025]])
    # isotropic: Uiso × I; and in a monoclinic cell U stays positive definite
    s = structure(cif_text([("C1", "C", 1, 1, 1, 0.04)], cell=(9, 11, 13, 90, 110, 90),
                           aniso=[("C1", 0.03, 0.02, 0.025, 0.0, 0.004, 0.0)]))
    assert np.all(np.linalg.eigvalsh(s.atoms[0].u) > 0)
    s = structure(cif_text([("O1", "O", 1, 1, 1, 0.04)]))
    assert np.allclose(s.atoms[0].u, 0.04 * np.eye(3)) and not s.atoms[0].anisotropic


def ethane_half():
    """Half an ethane on an inversion centre (the other half is made by -x, -y, -z)."""
    return [("C1", "C", 0.76, 0, 0, 0.025), ("H1A", "H", 1.15, 1.02, 0, 0.04),
            ("H1B", "H", 1.15, -0.51, 0.88, 0.04), ("H1C", "H", 1.15, -0.51, -0.88, 0.04)]


def test_a_molecule_on_an_inversion_centre_is_completed():
    g = crystal.grow(structure(cif_text(ethane_half())))
    assert len(g.atoms) == 8 and g.molecules == 1 and g.complete
    assert sorted(a.label for a in g.atoms) == ["C1", "C1", "H1A", "H1A", "H1B", "H1B",
                                                "H1C", "H1C"]
    cc = [(i, j) for i, j in g.bonds if g.atoms[i].element == g.atoms[j].element == "C"]
    assert len(cc) == 1 and len(g.bonds) == 7
    i, j = cc[0]
    assert np.linalg.norm(g.atoms[i].xyz - g.atoms[j].xyz) == pytest.approx(1.52, abs=0.01)


def test_a_molecule_in_a_general_position_and_its_neighbours():
    """A whole molecule in the asymmetric unit, P2₁/c: its symmetry copies are other
    molecules, not bonded to it, so only it is shown."""
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol = Chem.AddHs(Chem.MolFromSmiles("OC(=O)c1ccccc1"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    pos = mol.GetConformer().GetPositions() + [5, 5, 5]
    atoms = [(f"{a.GetSymbol()}{k + 1}", a.GetSymbol(), *pos[k], 0.03)
             for k, a in enumerate(mol.GetAtoms())]
    g = crystal.grow(structure(cif_text(
        atoms, cell=(14, 15, 16, 90, 98, 90),
        ops=("x, y, z", "-x, y+1/2, -z+1/2", "-x, -y, -z", "x, -y-1/2, z-1/2"))))
    assert len(g.atoms) == mol.GetNumAtoms() and g.molecules == 1
    assert len(g.bonds) == mol.GetNumBonds()


def test_disorder_parts_are_not_bonded_to_each_other():
    atoms = [("C1", "C", 0, 0, 0, 0.03), ("C2", "C", 1.5, 0, 0, 0.03),
             ("C3A", "C", 2.2, 1.3, 0, 0.05, "1"), ("C3B", "C", 2.2, 0.9, 0.6, 0.05, "2")]
    g = crystal.grow(structure(cif_text(atoms, ops=("x, y, z",))))
    labels = {(g.atoms[i].label, g.atoms[j].label) for i, j in g.bonds}
    assert ("C2", "C3A") in labels and ("C2", "C3B") in labels
    assert ("C3A", "C3B") not in labels and ("C3B", "C3A") not in labels


# ---------------------------------------------------------------------- the table

TABLE_CIF = """data_nb_123
_database_code_depnum_ccdc_archive 'CCDC 2345678'
_chemical_formula_sum 'C14 H12 Br N O2'
_chemical_formula_weight 306.16
_diffrn_ambient_temperature 100(2)
_space_group_crystal_system monoclinic
_space_group_name_H-M_alt 'P 1 21/c 1'
_cell_length_a 7.1234(3)
_cell_length_b 12.3456(5)
_cell_length_c 14.5678(6)
_cell_angle_alpha 90
_cell_angle_beta 98.765(2)
_cell_angle_gamma 90
_cell_volume 1266.12(9)
_cell_formula_units_Z 4
_exptl_crystal_density_diffrn 1.606
_exptl_absorpt_coefficient_mu 3.245
_exptl_crystal_F_000 616
_exptl_crystal_size_max 0.32
_exptl_crystal_size_mid 0.21
_exptl_crystal_size_min 0.09
_diffrn_radiation_type 'Mo K\\a'
_diffrn_radiation_wavelength 0.71073
_diffrn_reflns_theta_min 2.15
_diffrn_reflns_theta_max 27.50
_diffrn_reflns_limit_h_min -9
_diffrn_reflns_limit_h_max 9
_diffrn_reflns_limit_k_min -16
_diffrn_reflns_limit_k_max 16
_diffrn_reflns_limit_l_min -18
_diffrn_reflns_limit_l_max 18
_diffrn_reflns_number 12345
_reflns_number_total 2901
_diffrn_reflns_av_R_equivalents 0.0412
_diffrn_reflns_av_unetI/netI 0.0298
_refine_ls_number_reflns 2901
_refine_ls_number_restraints 0
_refine_ls_number_parameters 163
_refine_ls_goodness_of_fit_ref 1.045
_refine_ls_R_factor_gt 0.0312
_refine_ls_wR_factor_gt 0.0745
_refine_ls_R_factor_all 0.0401
_refine_ls_wR_factor_ref 0.0789
_refine_diff_density_max 0.45
_refine_diff_density_min -0.52
"""


def test_the_crystal_data_table():
    rows = {k: v for k, _label, v in crystal.crystal_table(cif.read_cif(TABLE_CIF)[0])}
    assert rows["code"] == "nb_123" and rows["ccdc"] == "2345678"
    assert rows["formula"] == "C_{14}H_{12}BrNO_{2}"
    assert rows["space_group"] == "*P*2_{1}/*c*"          # (short form)
    assert rows["beta"] == "98.765(2)" and rows["z"] == "4"
    assert rows["size"] == "0.32 × 0.21 × 0.09"
    assert rows["radiation"] == "Mo Kα (λ = 0.71073)"
    assert rows["two_theta"] == "4.30 to 55.00"
    assert rows["index"] == "−9 ≤ *h* ≤ 9, −16 ≤ *k* ≤ 16, −18 ≤ *l* ≤ 18"
    assert rows["independent"] == "2901 [*R*_{int} = 0.0412, *R*_{sigma} = 0.0298]"
    assert rows["data"] == "2901/0/163"
    assert rows["r_gt"] == "*R*_{1} = 0.0312, w*R*_{2} = 0.0745"
    assert rows["r_all"] == "*R*_{1} = 0.0401, w*R*_{2} = 0.0789"
    assert rows["peak"] == "0.45/−0.52"
    assert "flack" not in rows                       # (not in the CIF: left out)
    labels = [label for _k, label, _v in crystal.crystal_table(cif.read_cif(TABLE_CIF)[0])]
    assert labels[:4] == ["Identification code", "CCDC deposition number",
                          "Empirical formula", "Formula weight"]


@pytest.mark.parametrize("symbol, printed", [
    ("P 21/c", "*P*2_{1}/*c*"), ("P -1", "*P*1̅"), ("C 2/c", "*C*2/*c*"),
    ("P 1 21/c 1", "*P*2_{1}/*c*"), ("C 1 2/c 1", "*C*2/*c*"),
    ("P 21 21 21", "*P*2_{1}2_{1}2_{1}"), ("Pbca", "*Pbca*")])
def test_space_groups_as_printed(symbol, printed):
    assert crystal.space_group_markup(symbol) == printed


# ---------------------------------------------------------------------- real structures

DATA = Path(__file__).parent / "data" / "cif"


def real(name: str) -> tuple[cif.CifBlock, crystal.Structure, crystal.Grown]:
    block = cif.read_cif_file(DATA / name)[0]
    s = crystal.structure_from_cif(block)
    return block, s, crystal.grow(s)


def test_naphthalene_on_an_inversion_centre():
    """Half the molecule in the file (5 C, 4 H): completed to C10H8 with its 19 bonds."""
    block, s, g = real("naphthalene_cod_2311090.cif")
    assert len(s.atoms) == 9 and len(g.atoms) == 18 and g.molecules == 1
    assert len(g.bonds) == 19 and g.complete
    cc = [np.linalg.norm(g.atoms[i].xyz - g.atoms[j].xyz) for i, j in g.bonds
          if g.atoms[i].element == g.atoms[j].element == "C"]
    assert len(cc) == 11 and all(1.35 < d < 1.44 for d in cc)
    rows = {k: v for k, _l, v in crystal.crystal_table(block)}
    assert rows["formula"] == "C_{10}H_{8}" and rows["space_group"] == "*P*2_{1}/*c*"
    assert rows["radiation"].startswith("λ = 0.8")        # (synchrotron: no type given)


def test_a_molecule_in_a_general_position():
    block, s, g = real("hydroxybenzaldehyde_cod_2217204.cif")
    assert len(g.atoms) == len(s.atoms) == 15 and g.molecules == 1 and len(g.bonds) == 15
    rows = {k: v for k, _l, v in crystal.crystal_table(block)}
    assert rows["radiation"] == "Mo Kα (λ = 0.71073)"
    assert len(rows) >= 25


def test_ferrocene_disordered_on_an_inversion_centre():
    """Fe on the inversion centre; each Cp ring in two orientations (parts 1 and 2),
    never bonded to each other."""
    _block, s, g = real("ferrocene_cod_7033930.cif")
    fe = [a for a in g.atoms if a.element == "Fe"]
    assert len(fe) == 1 and g.molecules == 1
    for i, j in g.bonds:
        a, b = g.atoms[i].atom, g.atoms[j].atom
        assert not (a.part and b.part and a.part != b.part)
    carbons = [a for a in g.atoms if a.element == "C"]
    assert len(carbons) == 2 * sum(1 for a in s.atoms if a.element == "C")
