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
