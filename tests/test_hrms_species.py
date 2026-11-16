"""Salts (choose the piece used as M) and a manually typed M formula."""

import pytest

from si_assistant.core import chem, checks

SALT = "C(c1ccccc1)[n+]1ccccc1.[Br-]"   # N-benzylpyridinium bromide


def test_salt_pieces_largest_first():
    pieces = chem.species(chem.parse_structure(SALT))
    assert [(info.formula, info.charge) for _smi, info in pieces] == [("C12H12N", 1), ("Br", -1)]


def test_salt_defaults_to_cation_and_suggests_m_plus():
    sp = checks.hrms_species(chem.parse_structure(SALT), {})
    assert sp.info.formula == "C12H12N" and sp.key == ""
    assert [c[1] for c in sp.choices] == ["Whole structure (C12H12BrN)", "C12H12N+", "Br−"]
    assert checks.suggested_adduct(sp.info) == "[M]+"
    r = checks.hrms_calc(sp.info, "[M]+", "170.0966", 5.0)
    # 144 + 12 x 1.007825 + 14.003074 - electron = 170.0964
    assert round(r.monoisotopic.mz, 4) == 170.0964 and abs(r.ppm) < 2


def test_salt_anion_and_whole_structure_choices():
    mol = chem.parse_structure(SALT)
    anion = next(c for c in checks.hrms_species(mol, {}).choices if c[1] == "Br−")
    sp = checks.hrms_species(mol, {"species": anion[0]})
    assert sp.info.formula == "Br" and checks.suggested_adduct(sp.info) == "[M]-"
    assert round(chem.ion_mz(sp.info, "[M]-").mz, 4) == 78.9189  # 78.918338 + electron
    whole = checks.hrms_species(mol, {"species": "whole"})
    assert whole.info.formula == "C12H12BrN"


def test_charged_adduct_hint():
    sp = checks.hrms_species(chem.parse_structure(SALT), {})
    assert "already charged" in checks.charged_adduct_hint(sp.info, "[M+H]+")
    assert checks.charged_adduct_hint(sp.info, "[M]+") == ""
    neutral = chem.mol_info(chem.parse_structure("CCO"))
    assert checks.charged_adduct_hint(neutral, "[M+H]+") == ""


def test_ordinary_structure_has_no_choices():
    sp = checks.hrms_species(chem.parse_structure("CCO"), {})
    assert sp.choices is None and sp.info.formula == "C2H6O"


@pytest.mark.parametrize("typed", ["C12H13NO", "C₁₂H₁₃NO", " C12 H13 N O ", "C12H13NO+"])
def test_manual_m_formula_overrides_structure(typed):
    sp = checks.hrms_species(chem.parse_structure("CCO"), {"formula": typed})
    assert sp.manual and sp.info.formula == "C12H13NO"
    r = checks.hrms_calc(sp.info, "[M+H]+", "", 5.0)
    assert round(r.monoisotopic.mz, 4) == 188.1070
    assert r.monoisotopic.formula == "C12H14NO"


def test_manual_formula_digits_after_charge_kept():
    # "N2+" must keep the 2 (two nitrogens), only the "+" is dropped.
    assert chem.hill_formula(chem.parse_m_formula("C6H8N2+")) == "C6H8N2"


def test_manual_formula_without_structure_and_isotopes():
    sp = checks.hrms_species(None, {"formula": "C5[13C]H6Br"})
    assert sp.info.formula == "C5[13C]H6Br"
    with pytest.raises(chem.ChemError):
        checks.hrms_species(None, {"formula": "C12Xx"})


def test_manual_formula_used_by_compound_checks(project):
    section = project.add_section("data", "Products")
    c = project.add_compound(section.id)
    project.set_structure(c, SALT)
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "170.0966",
                        "species": "", "formula": ""}
    project.save_compound(c)
    from si_assistant.core.compound_ids import IdReport
    warnings = checks.compound_warnings(project, c, IdReport())
    assert any("already charged" in w for w in warnings["hrms"])
    c.values["hrms"]["adduct"] = "[M]+"
    project.save_compound(c)
    assert "hrms" not in checks.compound_warnings(project, c, IdReport())


# ---- the ion as printed (SPEC §13f.3): [M]+ or, for M = the whole salt, [M−Br]+

DIBROMIDE = "C[n+]1ccc(cc1)-c1cc[n+](C)cc1.[Br-].[Br-]"   # methyl viologen dibromide


@pytest.mark.parametrize("smiles, adduct, label", [
    (SALT, "[M]+", "[M-Br]+"),
    (DIBROMIDE, "[M]2+", "[M-2Br]2+"),
    ("C[n+]1ccccc1.[O-]S(=O)(=O)C(F)(F)F", "[M]+", "[M-OTf]+"),
    ("C[n+]1ccccc1.F[B-](F)(F)F", "[M]+", "[M-BF4]+"),
    ("CCCCCCCCCCCC(=O)[O-].[Na+]", "[M]-", "[M-Na]-"),
    ("C[n+]1ccccc1.[O-]C(=O)C(F)(F)F", "[M]+", "[M-CF3CO2]+"),
    ("C(c1ccccc1)[n+]1ccccc1.CC(=O)[O-]", "[M]+", "[M-C2H3O2]+"),   # (by formula)
])
def test_salt_ion_label(smiles, adduct, label):
    assert checks.salt_ion_label(chem.parse_structure(smiles), {}, adduct) == label


@pytest.mark.parametrize("smiles, value, adduct", [
    ("CCO", {}, "[M+H]+"),                                 # one piece
    (SALT, {}, "[M+H]+"),                                  # adduct adds atoms
    (SALT, {"species": "whole"}, "[M]+"),                  # whole salt chosen as M
    (SALT, {"formula": "C12H12N+"}, "[M]+"),               # typed M formula
    (DIBROMIDE, {}, "[M]+"),                               # charge differs from the piece's
    ("C[n+]1ccccc1.C[n+]1ccccc1.[O-]S(=O)(=O)[O-]", {}, "[M]+"),   # two copies of M
    ("C[n+]1ccccc1.[Br-].[Br-]", {}, "[M]+"),              # not neutral as drawn
])
def test_salt_ion_label_does_not_apply(smiles, value, adduct):
    assert checks.salt_ion_label(chem.parse_structure(smiles), value, adduct) is None


class _Project:
    def __init__(self, style=None):
        self.settings = {} if style is None else {checks.SALT_LABEL_KEY: style}


def test_printed_ion_label_follows_the_setting_and_the_typed_label():
    mol = chem.parse_structure(SALT)
    value = {"adduct": "[M]+"}
    # Unchanged by default: the adduct as chosen.
    assert checks.printed_ion_label(_Project(), mol, value) == "[M]+"
    assert checks.printed_ion_label(_Project("piece"), mol, value) == "[M]+"
    assert checks.printed_ion_label(_Project("whole"), mol, value) == "[M-Br]+"
    # Not a salt, or an adduct it does not apply to: the adduct.
    assert checks.printed_ion_label(_Project("whole"), chem.parse_structure("CCO"),
                                    {"adduct": "[M+Na]+"}) == "[M+Na]+"
    assert checks.printed_ion_label(_Project("whole"), mol, {"adduct": "[M+H]+"}) == "[M+H]+"
    # The compound's own label wins over both.
    typed = {"adduct": "[M]+", checks.ION_LABEL_KEY: " [M−Br]+ "}
    assert checks.printed_ion_label(_Project(), mol, typed) == "[M−Br]+"
    assert checks.printed_ion_label(_Project("whole"), None, typed) == "[M−Br]+"


def test_counter_ion_names_are_all_readable():
    # (each name in the table belongs to a structure RDKit reads)
    assert len(chem._COUNTER_ION_NAME_KEYS) == len(chem._COUNTER_ION_NAMES)
    assert chem.piece_name("[Br-]") == "Br" and chem.piece_name("[Na+]") == "Na"


def test_project_settings_box_saves_the_salt_label_choice(qapp, tmp_path):
    from si_assistant.core.project import Project
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.salt_label.currentData() == checks.SALT_LABEL_PIECE
    dlg.salt_label.setCurrentIndex(dlg.salt_label.findData(checks.SALT_LABEL_WHOLE))
    dlg.apply()
    assert Project.open(p.folder).settings[checks.SALT_LABEL_KEY] == checks.SALT_LABEL_WHOLE
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.salt_label.currentData() == checks.SALT_LABEL_WHOLE
    dlg.salt_label.setCurrentIndex(dlg.salt_label.findData(checks.SALT_LABEL_PIECE))
    dlg.apply()
    assert checks.SALT_LABEL_KEY not in Project.open(p.folder).settings
