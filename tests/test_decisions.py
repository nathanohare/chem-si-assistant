"""Nathan's decisions after the audit (made-up data only):

1. A structure with a generic atom (R, X, *) is accepted for a compound, without a formula,
   with a warning (e.g. a general structure with an R group in a scheme).
2. The automatic HRMS "M" of a salt skips common counter-ions (SPEC §13f.1).
3. A charge typed at the end of the M formula says M is already an ion (for the "already
   charged" hint only); it never changes a mass.
"""

import pytest
from rdkit import Chem

from si_assistant.core import chem, checks, dashboard
from si_assistant.core import duplicate as dup
from si_assistant.core import fields as F
from si_assistant.core import scheme_editor as se
from si_assistant.core.compound_ids import IdReport
from si_assistant.core.project import Project

GENERIC = "*c1ccc(C(=O)OC)cc1"            # methyl 4-R-benzoate (R written as * in SMILES)
GENERIC_F = "*CC(F)(F)F"


def new_project(tmp_path, ids=("1a", "1b", "1c", "1d")):
    p = Project.create(tmp_path / "proj", "Decisions")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, list(ids))
    return p, data


def compound_with(p, data, smiles=GENERIC, fields=("hrms", "ea", "nmr")):
    c = p.add_compound(data.id, name="general ester")
    for key in fields:
        if key not in c.field_keys:
            p.add_field(c, {"key": key})
    p.set_structure(c, smiles)
    return c


# ====================================================================== 1. generic atoms

@pytest.mark.parametrize("text", ["*CC", Chem.MolToMolBlock(Chem.MolFromSmiles("*CC")),
                                  GENERIC])
def test_a_generic_structure_is_stored_without_a_formula(tmp_path, text):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="c")
    p.set_structure(c, text)                                     # (no error)
    assert p.structure_path(c).is_file()
    assert p.read_structure(c) is None and p.structure_info(c) is None   # no formula
    assert p.generic_structure_atoms(c) == ["R"]                 # (a * is saved as R)
    assert p.structure_for_drawing(c) is not None                # still drawn
    assert p.field_filled(c, next(d for d in p.field_defs(c) if d.key == "structure"))
    p.close()


def test_a_generic_structure_is_warned_in_plain_words_and_never_breaks_the_checks(tmp_path):
    p, data = new_project(tmp_path)
    c = compound_with(p, data)
    c.values["nmr"] = [{"id": "r1", "nucleus": "1H", "label": "",
                        "text": "1H NMR (400 MHz, CDCl3) δ 7.9 (d, J = 8 Hz, 2H), 3.9 (s, 3H)."},
                       {"id": "r2", "nucleus": "13C", "label": "",
                        "text": "13C NMR (101 MHz, CDCl3) δ 167.0, 130.0, 52.0."}]
    p.save_compound(c)
    warnings = checks.all_warnings(p, c, p.id_report())          # (does not raise)
    generic = [w for w in warnings if w.key == checks.GENERIC_STRUCTURE_KEY]
    assert len(generic) == 1
    assert generic[0].message == (
        "The structure has a generic atom (R), so no formula or masses can be calculated. "
        "Draw the actual group, or type the M formula in the HRMS field (✎) if you need the "
        "calculated m/z.")
    # Dismissable like every warning (a fingerprint to remember the dismissal by).
    assert generic[0].fingerprint
    # No ¹³C / ¹H count checks for a structure without a formula.
    assert not any(w.check == "nmr" and ("carbon" in w.message.lower()
                                         or "hydrogen" in w.message.lower())
                   for w in warnings)
    p.close()


def test_a_generic_structure_gets_no_automatic_heteroatom_reports(tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="c")
    if "nmr" not in c.field_keys:
        p.add_field(c, {"key": "nmr"})
    assert p.set_structure(c, GENERIC_F) == []                   # (no formula: no ¹⁹F box)
    p.close()


def test_a_typed_m_formula_still_gives_the_calculated_mz(tmp_path):
    from si_assistant.compile import blocks
    p, data = new_project(tmp_path)
    c = compound_with(p, data)
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+Na]+", "found": "",
                        "species": "", "formula": ""}
    p.save_compound(c)
    assert checks.compound_hrms(p, c) == (None, "")              # nothing to calculate
    c.values["hrms"]["formula"] = "C9H10O2"                      # R = CH3, typed by hand
    c.values["hrms"]["found"] = "173.0574"
    p.save_compound(c)
    result, problem = checks.compound_hrms(p, c)
    assert problem == "" and round(result.monoisotopic.mz, 4) == 173.0573
    text = "".join(r.text for r in blocks.hrms_sentence(p, c))
    assert "calcd for" in text and "173.0573" in text and "found 173.0574" in text
    p.close()


def test_a_generic_structure_is_replaced_like_any_other_and_clear_can_be_undone(tmp_path):
    p, data = new_project(tmp_path)
    c = compound_with(p, data)
    p.set_structure(c, GENERIC)                                  # typed again: no Trash entry
    p.set_structure(c, "*c1ccccc1")                              # another R structure: none
    assert p.list_trash() == []                                  # (SPEC §13b.5: overwritten)
    p.set_structure(c, "CCO")
    assert p.list_trash() == [] and p.read_structure(c) is not None
    p.set_structure(c, GENERIC)
    p.clear_structure(c)                                         # Clear: to the Trash
    trash = p.list_trash()
    assert len(trash) == 1
    p.restore_from_trash(trash[0].id)                            # a generic one is put back
    assert p.generic_structure_atoms(c) == ["R"]
    p.close()


def _mol_block_ending_in(symbol: str, extra: str = "") -> str:
    """A V2000 MOL block C-C-<symbol>, as ChemDraw would save a query or R-group atom."""
    return f"""
  test

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.0000    0.0000    0.0000 {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
{extra}M  END
"""


@pytest.mark.parametrize("text, label", [
    (_mol_block_ending_in("X"), "X"), (_mol_block_ending_in("A"), "A"),
    (_mol_block_ending_in("Q"), "Q"), (_mol_block_ending_in("M"), "M"),
    (_mol_block_ending_in("R#"), "R"), (_mol_block_ending_in("R"), "R"),
    (_mol_block_ending_in("R#", "M  RGP  1   3   2\n"), "R2"),
    ("*CC", "R"),
])
def test_a_generic_atom_is_named_as_it_was_drawn(tmp_path, text, label):
    assert chem.generic_atoms(chem.parse_structure(text)) == [label]
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="c")
    p.set_structure(c, text)                                     # (kept through the MOL file)
    assert p.generic_structure_atoms(c) == [label]
    defn = next(d for d in p.field_defs(c) if d.key == "structure")
    assert dashboard.field_summary(p, c, defn) == f"no formula ({label})"
    p.close()


def test_numbered_r_groups_are_told_apart():
    assert chem.generic_atoms(chem.parse_structure("[1*]CC[2*]")) == ["R1", "R2"]
    mol = chem.parse_structure(Chem.MolToMolBlock(Chem.MolFromSmiles("[1*]CC[2*]")))
    assert chem.generic_atoms(mol) == ["R1", "R2"]


def test_a_generic_structure_with_a_chemdraw_picture_is_said_in_the_compile_notes(
        qapp, tmp_path):
    """The note is there whatever is printed for the compound: its own drawing, or the
    pasted ChemDraw picture (probably the most common case for a general structure)."""
    from tests.sample_project import build_sample
    from tests.test_audit_compile import picture_bytes, source
    p, parts = build_sample(tmp_path)
    try:
        c2 = parts["c2"]
        p.set_structure(c2, GENERIC, picture=picture_bytes("PNG"))
        assert p.generic_structure_atoms(c2) == ["R"]
        picture = p.structure_image_path(c2)
        assert picture is not None and picture.exists()
        for structures in ("pictures", "acs"):
            src, result = source(p, name=f"SI-{structures}", structures=structures)
            assert f"assets/{c2.id}.svg" not in src            # (the picture is printed)
            notes = [w for w in result.warnings if "generic atom (R)" in w]
            assert len(notes) == 1, structures
            assert notes[0].startswith(p.id_report().display_id(c2.id) + " (")
            assert "no calculated formula or masses" in notes[0]
    finally:
        p.close()


def test_a_scheme_box_with_a_generic_structure_can_be_a_compound(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="General")
    box = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, box, GENERIC)                     # (R groups: a general scheme)
    compound = se.link_new_compound(p, k, box)                   # "Compound" ticked: accepted
    assert compound is not None and [c.id for c in k.children] == [compound.id]
    assert p.generic_structure_atoms(compound) == ["R"]
    view = se.box_view(p, k, se.find_box(k, box)[0])
    assert view.compound is compound and view.mol is not None    # the box still draws it
    assert chem.canonical_smiles(view.mol) == chem.canonical_smiles(
        chem.parse_structure(GENERIC))
    p.close()


def test_dashboard_and_duplicate_of_a_generic_structure(tmp_path):
    p, data = new_project(tmp_path)
    c = compound_with(p, data)
    defn = next(d for d in p.field_defs(c) if d.key == "structure")
    assert dashboard.field_summary(p, c, defn) == "no formula (R)"
    rows = dashboard.build_rows(p, p.section(data.id))           # (does not raise)
    assert rows[0].cells["structure"].warnings
    copy = dup.duplicate_compound(p, c, {d.key for d in dup.copyable_fields(p, c)})[0]
    assert p.generic_structure_atoms(copy) == ["R"]
    p.close()


def test_the_compound_page_shows_and_draws_a_generic_structure(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="c")
    for key in ("hrms", "ea"):
        if key not in c.field_keys:
            p.add_field(c, {"key": key})
    panel = CompoundPanel(p, c, lambda: "Tester")
    try:
        boxes = {b.defn.key: b for b in panel.boxes}
        editor = boxes["structure"].editor
        editor.set_structure_text(GENERIC)                       # typed: accepted
        assert p.generic_structure_atoms(c) == ["R"]
        assert editor.has_content()
        assert not editor.image.pixmap().isNull()
        assert "generic atom (R)" in editor.info.text()
        panel._refresh_header()
        assert "generic atom R" in panel.formula_label.text()
        assert not panel.thumb.pixmap().isNull()
        hrms = boxes["hrms"].editor
        hrms.refresh_calculation()
        assert "generic atom (R)" in hrms.calc.text()
        hrms.formula_edit.setText("C9H10O2")
        hrms.refresh_calculation()
        assert "calcd" in hrms.calc.text().lower() or "m/z" in hrms.calc.text()
        ea = boxes["ea"].editor
        ea._show_ea_formula()
        assert "generic atom (R)" in ea.widgets["formula"].placeholderText()
    finally:
        panel.deleteLater()
        p.close()


# ====================================================================== 2. salts

def automatic(smiles: str) -> chem.MolInfo:
    return checks.hrms_species(chem.parse_structure(smiles), {}).info


@pytest.mark.parametrize("smiles, formula, charge", [
    ("C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F", "C6H8N", 1),              # N-methylpyridinium OTf
    ("CCCCn1cc[n+](C)c1.O=S(=O)([N-]S(=O)(=O)C(F)(F)F)C(F)(F)F",
     "C8H15N2", 1),                                                   # BMIM NTf2
    ("c1cc[nH+]cc1.[O-]C(=O)C(F)(F)F", "C5H6N", 1),                  # pyridinium TFA
    ("CCCC[N+](CCCC)(CCCC)CCCC.[O-]C(=O)c1ccccc1", "C7H5O2", -1),    # NBu4 benzoate
    ("[K+].F[B-](F)(F)c1ccccc1", "C6H5BF3", -1),                      # K[PhBF3]
    ("[Na+].CC(=O)[O-]", "C2H3O2", -1),                               # sodium acetate
    ("CCCC[N+](CCCC)(CCCC)CCCC.F[B-](F)(F)F", "C16H36N", 1),          # NBu4 BF4: largest
    ("C[n+]1ccccc1.FC(F)(F)c1cc(cc(c1)C(F)(F)F)[B-](c1cc(cc(c1)C(F)(F)F)C(F)(F)F)"
     "(c1cc(cc(c1)C(F)(F)F)C(F)(F)F)c1cc(cc(c1)C(F)(F)F)C(F)(F)F", "C6H8N", 1),  # BArF
    ("C[n+]1ccccc1.[O-]S(=O)(=O)c1ccc(C)cc1", "C6H8N", 1),           # tosylate
    ("C(c1ccccc1)[n+]1ccccc1.[Br-]", "C12H12N", 1),                   # (as before)
    ("C[NH3+].C[NH3+].[O-]S(=O)(=O)[O-]", "CH6N", 1),                 # sulfate
    ("CCCC[N+](CCCC)(CCCC)CCCC.[OH-]", "C16H36N", 1),                 # NBu4 OH: largest
    ("C[n+]1ccccc1.OC(=O)[O-]", "C6H8N", 1),                          # bicarbonate
    ("CC[NH3+].[O-]C([O-])=O.CC[NH3+]", "C2H8N", 1),                  # carbonate
    ("C[n+]1ccccc1.[H+].[O-]S(=O)(=O)[O-]", "C6H8N", 1),              # a drawn proton
])
def test_automatic_m_skips_common_counter_ions(smiles, formula, charge):
    info = automatic(smiles)
    assert (info.formula, info.charge) == (formula, charge)


def test_counter_ions_are_recognised_however_they_are_drawn():
    # Another atom order, a Kekulé ring, explicit H: the same counter-ion.
    for smiles in ("FC(F)(F)S(=O)(=O)[O-]", "[O-]S(C(F)(F)F)(=O)=O",
                   "CC1=CC=C(C=C1)S([O-])(=O)=O", "[H][N+]([H])([H])[H]",
                   "O=[N+]([O-])[O-]", "c1ccccc1P(c1ccccc1)(c1ccccc1)=[N+]=P(c1ccccc1)"
                   "(c1ccccc1)c1ccccc1"):
        mol = Chem.MolFromSmiles(smiles)
        assert chem.is_counter_ion(Chem.MolToSmiles(mol)), smiles
    for smiles in ("CC(=O)[O-]", "C[n+]1ccccc1", "CCCC[NH3+]", "O=C([O-])c1ccccc1",
                   "F[B-](F)(F)c1ccccc1"):
        assert not chem.is_counter_ion(smiles), smiles


def test_every_listed_counter_ion_is_read_by_rdkit():
    assert all(Chem.MolFromSmiles(s) is not None for s in chem._COUNTER_ION_SMILES)
    assert len(chem._COUNTER_IONS) == len(set(chem._COUNTER_ION_SMILES))


@pytest.mark.parametrize("smiles, main", [
    ("C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F", "C[n+]1ccccc1"),          # triflate: the cation
    ("CCCC[N+](CCCC)(CCCC)CCCC.[O-]C(=O)c1ccccc1", "O=C([O-])c1ccccc1"),   # NBu4: the anion
    ("C(c1ccccc1)[n+]1ccccc1.[Br-]", "c1ccc(C[n+]2ccccc2)cc1"),
    ("CCCC[N+](CCCC)(CCCC)CCCC.F[B-](F)(F)F", "CCCC[N+](CCCC)(CCCC)CCCC"),  # all: largest
    ("CCO", "CCO"),
])
def test_the_main_piece_follows_the_counter_ion_rule(smiles, main):
    piece = chem.main_piece(Chem.MolFromSmiles(smiles))
    assert Chem.MolToSmiles(piece) == Chem.MolToSmiles(Chem.MolFromSmiles(main))
    # (the same piece as the automatic HRMS M)
    assert chem.mol_info(piece).formula == automatic(smiles).formula


def test_a_dismissed_hrms_warning_comes_back_when_the_automatic_m_changed(tmp_path):
    """A salt left on "Automatic": the M now skips the counter-ion, so the calculated m/z
    changes, and an HRMS warning dismissed before the update is about another m/z."""
    p, data = new_project(tmp_path)
    tolerance = p.settings.get("hrms_tolerance_ppm")

    def ppm_warning(c):
        return next(w for w in checks.all_warnings(p, c, IdReport()) if w.key == "hrms:ppm")
    salt = compound_with(p, data, "C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F", fields=("hrms",))
    salt.values["hrms"] = {"ionization": "ESI", "adduct": "[M]+", "found": "95.0000",
                           "species": "", "formula": ""}
    p.save_compound(salt)
    w = ppm_warning(salt)
    before = checks._hrms_fp(p.structure_info(salt).smiles, salt.values["hrms"], tolerance)
    assert w.fingerprint != before and w.legacy == ""
    salt.data["dismissed"] = {"hrms:ppm": {"fingerprint": before}}   # (dismissed before)
    assert not checks.is_dismissed(salt, w)
    # The automatic piece did not change (a bromide: the cation is the largest piece) or
    # there is a single piece: the fingerprint is as before, and so is the dismissal.
    for smiles in ("C(c1ccccc1)[n+]1ccccc1.[Br-]", "CCO"):
        c = compound_with(p, data, smiles, fields=("hrms",))
        c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "95.0000",
                            "species": "", "formula": ""}
        p.save_compound(c)
        w = ppm_warning(c)
        assert w.fingerprint == checks._hrms_fp(p.structure_info(c).smiles, c.values["hrms"],
                                                tolerance)
    p.close()


def test_the_pieces_keep_their_order_and_the_users_choice_wins():
    mol = chem.parse_structure("C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F")
    sp = checks.hrms_species(mol, {})
    # The "M is:" list is unchanged: the whole structure, then the pieces, largest first.
    assert [c[1] for c in sp.choices] == ["Whole structure (C7H8F3NO3S)", "CF3O3S−",
                                          "C6H8N+"]
    assert sp.key == "" and sp.automatic == sp.choices[2] and sp.info.formula == "C6H8N"
    anion = checks.hrms_species(mol, {"species": sp.choices[1][0]})
    assert anion.key == sp.choices[1][0] and anion.info.formula == "CF3O3S"
    whole = checks.hrms_species(mol, {"species": "whole"})
    assert whole.info.formula == "C7H8F3NO3S"
    # A single piece: no choice, no automatic piece.
    assert checks.hrms_species(chem.parse_structure("CCO"), {}).automatic is None


def test_the_drop_down_names_the_automatic_piece_and_the_adduct_is_left_alone(qapp,
                                                                              tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.field_widgets import get_part_value
    p, data = new_project(tmp_path)
    c = compound_with(p, data, "C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F", fields=("hrms",))
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "",
                        "species": "", "formula": ""}
    p.save_compound(c)
    panel = CompoundPanel(p, c, lambda: "Tester")
    try:
        hrms = next(b for b in panel.boxes if b.defn.key == "hrms").editor
        hrms.refresh_calculation()
        assert hrms.species_box.itemText(0) == "Automatic (C₆H₈N⁺)"
        assert get_part_value(hrms.widgets["adduct"]) == "[M+H]+"   # not changed on its own
    finally:
        panel.deleteLater()
        p.close()


# ====================================================================== 3. typed charge

@pytest.mark.parametrize("typed, formula, charge", [
    ("C6H8N2+", "C6H8N2", 1),          # the 2 belongs to N
    ("C6H8N2", "C6H8N2", 0),
    ("C₆H₈N₂⁺", "C6H8N2", 1),
    ("C10H8N2²⁺", "C10H8N2", 2),
    ("C10H8N2++", "C10H8N2", 2),
    ("C10H8N2⁺⁺", "C10H8N2", 2),
    ("C2F3O2-", "C2F3O2", -1),
    ("C2F3O2−", "C2F3O2", -1),         # Unicode minus
    ("C2F3O2⁻", "C2F3O2", -1),
    ("C24H20B−−", "C24H20B", -2),
    ("C6H6³⁻", "C6H6", -3),
    ("C6H6+•", "C6H6", 1),             # (a radical cation)
    ("C6H6•+", "C6H6", 1),
    ("C5[13C]H6Br+", "C5[13C]H6Br", 1),
    (" C12 H12 N + ", "C12H12N", 1),
    # A digit that can only be the size of the charge: after a space, in brackets, after
    # a caret, after the sign, or after a formula in square brackets.
    ("C10H8N2 2+", "C10H8N2", 2),
    ("C10H8N2 2-", "C10H8N2", -2),
    ("C10H8N2 2 +", "C10H8N2", 2),
    ("C10H8N2 +2", "C10H8N2", 2),
    ("C10H8N2 ²⁺", "C10H8N2", 2),
    ("C10H8N2(2+)", "C10H8N2", 2),
    ("C10H8N2 (2+)", "C10H8N2", 2),
    ("C10H8N2^2+", "C10H8N2", 2),
    ("C10H8N2^+", "C10H8N2", 1),
    ("C10H8N2+2", "C10H8N2", 2),
    ("C24H20B−2", "C24H20B", -2),
    ("[C10H8N2]2+", "C10H8N2", 2),
    ("[C10H8N2]+", "C10H8N2", 1),
    ("[C5[13C]H6Br]+", "C5[13C]H6Br", 1),
    ("[C6H6]•+", "C6H6", 1),
    ("C6H6+.", "C6H6", 1),
    ("C6H6 +•", "C6H6", 1),
    ("C6H8 N2+", "C6H8N2", 1),        # (no space before the 2: still N2)
    ("C5[13C]H6Br", "C5[13C]H6Br", 0),
])
def test_a_typed_charge_is_read(typed, formula, charge):
    counts, z = chem.parse_m_formula_and_charge(typed)
    assert (chem.hill_formula(counts), z) == (formula, charge)
    assert chem.same_formula(chem.parse_m_formula(typed), counts)


@pytest.mark.parametrize("typed", ["C6H6+-", "C6H6²⁺⁺", "+", "²⁺", "C10H8N2 2+2",
                                   "C10H8N2(0+)", "(2+)", "[C10H8N2]"])
def test_an_unreadable_typed_charge_is_an_error_not_a_crash(typed):
    with pytest.raises(chem.ChemError):
        chem.parse_m_formula_and_charge(typed)


@pytest.mark.parametrize("typed", ["C10H8N2{2+}", "C10H8N2<+>", "C10H8N2 2+ x"])
def test_an_unreadable_charge_says_how_to_write_one(typed):
    with pytest.raises(chem.ChemError) as info:
        chem.parse_m_formula_and_charge(typed)
    assert "Write a charge at the end of the formula" in str(info.value)


@pytest.mark.parametrize("adduct", ["[M]+", "[M+H]+", "[M+Na]+", "[M+2H]2+"])
def test_a_typed_charge_never_changes_a_mass(adduct):
    plain = checks.hrms_species(None, {"formula": "C12H12N"}).info
    charged = checks.hrms_species(None, {"formula": "C12H12N+"}).info
    assert charged.charge == 1 and plain.charge == 0
    assert charged.exact_mass == plain.exact_mass and charged.mw == plain.mw
    a = checks.hrms_calc(plain, adduct, "", 5.0)
    b = checks.hrms_calc(charged, adduct, "", 5.0)
    assert [(pk.mz, pk.formula) for pk in a.peaks] == [(pk.mz, pk.formula) for pk in b.peaks]


def test_a_typed_charge_switches_on_the_already_charged_hint(tmp_path):
    p, data = new_project(tmp_path)
    c = compound_with(p, data, "CCO", fields=("hrms",))
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "",
                        "species": "", "formula": "C12H12N"}
    p.save_compound(c)

    def hrms_warnings():
        return [w for w in checks.all_warnings(p, c, IdReport()) if w.check == "hrms"]
    assert not any(w.key == "hrms:charged" for w in hrms_warnings())   # neutral M
    c.values["hrms"]["formula"] = "C12H12N+"
    p.save_compound(c)
    hint = [w for w in hrms_warnings() if w.key == "hrms:charged"]
    assert len(hint) == 1 and "already charged" in hint[0].message and "[M]⁺" in hint[0].message
    c.values["hrms"]["adduct"] = "[M]+"
    p.save_compound(c)
    assert not any(w.key == "hrms:charged" for w in hrms_warnings())
    assert F.part_text(c.values["hrms"]["formula"]) == "C12H12N+"      # kept as typed
    p.close()
