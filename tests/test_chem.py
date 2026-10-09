"""Mass and formula calculations checked against hand-calculated values.

Hand calculations use: C 12.000000, H 1.007825, N 14.003074, O 15.994915,
Na 22.989770, Br 78.918338, Cl 34.968853, F 18.998403, D 2.014102,
electron 0.000549.
"""

import pytest

from si_assistant.core import chem


def info(smiles):
    return chem.mol_info(chem.parse_structure(smiles))


def mz(smiles, adduct):
    return round(chem.ion_mz(info(smiles), adduct).mz, 4)


# ---------------------------------------------------------------------- formula / MW / mass

def test_spec_example_ethyl_2_methylfuran_3_carboxylate():
    i = info("CCOC(=O)c1ccoc1C")
    assert i.formula == "C8H10O3"
    r = chem.ion_mz(i, "[M+H]+")
    assert r.formula == "C8H11O3"
    assert round(r.mz, 4) == 155.0703  # SPEC §9.2


def test_spec_example_c12h14no():
    # A C12H13NO structure -> [M+H]+ C12H14NO 188.1070 (SPEC §8.1)
    i = info("CC(=O)N1CC=C(c2ccccc2)C1")
    assert i.formula == "C12H13NO"
    r = chem.ion_mz(i, "[M+H]+")
    assert (r.formula, round(r.mz, 4)) == ("C12H14NO", 188.1070)


def test_formula_typo_example():
    # A typo of the kind seen in published SIs (an invented example): "calcd for C10H41NO
    # [M+H]+: 164.1070"; the mass fits C10H14NO (digits swapped).
    i = info("CC(=O)N(C)Cc1ccccc1")  # N-benzyl-N-methylacetamide, C10H13NO
    assert i.formula == "C10H13NO"
    r = chem.ion_mz(i, "[M+H]+")
    assert (r.formula, round(r.mz, 4)) == ("C10H14NO", 164.107)


def test_hill_order_and_no_carbon():
    assert info("ClCCBr").formula == "C2H4BrCl"
    assert info("O").formula == "H2O"
    assert info("[Na+].[Cl-]").formula == "ClNa"


def test_molecular_weight_average():
    assert round(info("c1ccccc1").mw, 3) == 78.114


def test_exact_mass_monoisotopic():
    assert round(info("c1ccccc1").exact_mass, 4) == 78.0470
    assert round(info("Brc1ccccc1").exact_mass, 4) == 155.9575  # 79Br


def test_deuterium_label():
    i = info("[2H]C([2H])([2H])OC(=O)c1ccccc1")  # methyl-d3 benzoate
    assert i.formula == "C8H5D3O2"
    assert round(i.exact_mass, 4) == 139.0713  # 96 + 5.039125 + 6.042306 + 31.98983


def test_elements():
    assert info("FC(F)(F)c1ccc(P(=O)(O)O)cc1").elements == {"C", "H", "F", "O", "P"}


# ---------------------------------------------------------------------- adducts

@pytest.mark.parametrize("adduct,expected", [
    ("[M+H]+", 155.0703),
    ("[M+Na]+", 177.0522),      # 154.062994 + 22.989770 - e
    ("[M+K]+", 193.0262),       # + 38.963707 - e
    ("[M+NH4]+", 172.0968),     # + 18.034374 - e
    ("[M]+•", 154.0624),        # - e
    ("[M]+", 154.0624),
    ("[M+H−H2O]+", 137.0597),   # unicode minus accepted
    ("[2M+Na]+", 331.1152),     # 2 x 154.062994 + 22.989770 - e
    ("[M+2H]2+", 78.0388),      # (154.062994 + 2.015650 - 2e) / 2
])
def test_positive_adducts(adduct, expected):
    assert mz("CCOC(=O)c1ccoc1C", adduct) == expected


@pytest.mark.parametrize("adduct,expected", [
    ("[M-H]-", 121.0295),       # benzoic acid 122.036779 - H + e
    ("[M−H]−", 121.0295),
    ("[M+Cl]-", 157.0062),      # + 34.968853 + e
    ("[M+HCOO]-", 167.0350),    # + 44.997654 + e
])
def test_negative_adducts(adduct, expected):
    assert mz("OC(=O)c1ccccc1", adduct) == expected


def test_adduct_removing_missing_atoms():
    with pytest.raises(chem.ChemError):
        chem.ion_mz(info("C"), "[M-Na]-")


@pytest.mark.parametrize("bad", ["M+H", "[M+H]", "[M+Xx]+", "[M+H]+junk",
                                 "[M+H]0+", "[M+H]00+", "[M]0-", "[0M+H]+"])
def test_bad_adducts(bad):
    with pytest.raises(chem.ChemError):
        chem.parse_adduct(bad)


def test_adduct_display():
    assert chem.adduct_display("[M+H]+") == "[M+H]⁺"
    assert chem.adduct_display("[M-H]-") == "[M−H]⁻"
    assert chem.adduct_display("[M+2H]2+") == "[M+2H]²⁺"
    assert chem.adduct_display("[M]+•") == "[M]⁺•"


# ---------------------------------------------------------------------- isotope patterns

def pattern(smiles, adduct, **kw):
    return [(round(p.mz, 4), round(p.abundance, 1), p.formula)
            for p in chem.isotope_pattern(info(smiles), adduct, **kw)]


def test_bromobenzene_matches_chemdraw():
    # ChemDraw: 156.9648 (100.0%), 158.9627 (97.3%), 157.9681 (6.5%), 159.9661 (6.3%).
    # Our masses agree within 0.0001 (ChemDraw's are uniformly ~0.00004 higher, i.e. 0.3 ppm,
    # most likely slightly different atomic-mass tables).
    peaks = chem.isotope_pattern(info("Brc1ccccc1"), "[M+H]+")
    chemdraw = [(156.9648, 100.0), (158.9627, 97.3), (157.9681, 6.5), (159.9661, 6.3)]
    assert len(peaks) == 4
    for peak, (mz, pct) in zip(peaks, chemdraw):
        assert peak.mz == pytest.approx(mz, abs=0.0001)
        assert round(peak.abundance, 1) == pct
    assert [p.formula for p in peaks] == ["C6H6Br", "C6H6[81Br]", "C5[13C]H6Br",
                                          "C5[13C]H6[81Br]"]
    assert peaks[0].monoisotopic and not peaks[1].monoisotopic
    # The first peak is the same number as the plain [M+H]+ calculation.
    assert peaks[0].mz == pytest.approx(chem.ion_mz(info("Brc1ccccc1"), "[M+H]+").mz, abs=1e-9)


def test_dibromide_most_intense_is_mixed_isotope():
    peaks = pattern("BrCCBr", "[M]+")
    assert peaks[0][2] == "C2H4Br[81Br]" and peaks[0][1] == 100.0
    assert [p[1] for p in peaks[:3]] == [100.0, 51.4, 48.6]  # 1 : 0.51 : 0.49 for Br2


def test_chlorine_pattern():
    peaks = pattern("Clc1ccccc1", "[M]+•")
    assert (peaks[0][1], peaks[1][2], peaks[1][1]) == (100.0, "C6H5[37Cl]", 32.0)


def test_large_molecule_13c_envelope():
    # C40 hydrocarbon: M+1 is about 40 x 1.07/98.93 = 43% of M.
    peaks = chem.isotope_pattern(info("C" * 40), "[M+H]+", min_percent=1)
    assert peaks[0].monoisotopic
    assert peaks[1].abundance == pytest.approx(43.3, abs=0.6)


def test_labelled_atoms_are_fixed():
    peaks = chem.isotope_pattern(info("[2H]C([2H])([2H])O"), "[M+H]+")  # CD3OH + H+
    assert peaks[0].formula == "CH2D3O"
    assert peaks[0].mz == pytest.approx(chem.ion_mz(info("[2H]C([2H])([2H])O"), "[M+H]+").mz)


def test_nearest_peak():
    peaks = chem.isotope_pattern(info("Brc1ccccc1"), "[M+H]+")
    assert chem.nearest_peak(peaks, 158.9630).formula == "C6H6[81Br]"


def test_ppm_error():
    assert round(chem.ppm_error(188.1073, 188.1070), 1) == 1.6


# ---------------------------------------------------------------------- reading structures

def test_an_empty_mol_block_is_reported_not_a_crash():
    # (what a drawing program copies for an empty drawing)
    empty = "\n  Editor\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    with pytest.raises(chem.ChemError, match="no atoms"):
        chem.parse_structure(empty)


def test_parse_smiles_with_trailing_name():
    assert info("CCO ethanol").formula == "C2H6O"


def test_parse_molblock_keeps_coordinates():
    mol = chem.parse_structure("CCO")
    block = chem.mol_to_block(mol)
    again = chem.parse_structure(block)
    assert chem.mol_info(again).formula == "C2H6O"
    assert again.GetConformer().GetAtomPosition(0).x == pytest.approx(
        mol.GetConformer().GetAtomPosition(0).x, abs=1e-3)


def test_parse_sdf_takes_first_record():
    block = chem.mol_to_block(chem.parse_structure("CCO"))
    sdf = block + "$$$$\n" + chem.mol_to_block(chem.parse_structure("CCCC")) + "$$$$\n"
    assert chem.mol_info(chem.parse_structure(sdf)).formula == "C2H6O"


@pytest.mark.parametrize("bad", ["", "C1CC", "C(C)(C)(C)(C)C", "not a smiles"])
def test_bad_structures(bad):
    with pytest.raises(chem.ChemError):
        chem.parse_structure(bad)


def test_mdlct_clipboard_format():
    lines = ["", "  ChemDraw", "", "  1  0  0  0  0  0  0  0  0  0999 V2000",
             "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0", "M  END"]
    data = b"".join(bytes([len(l)]) + l.encode() for l in lines)
    assert chem.mol_info(chem.parse_structure(chem.mdlct_to_molblock(data))).formula == "CH4"


def test_drawings():
    mol = chem.parse_structure("CCOC(=O)c1ccoc1C")
    assert chem.structure_png(mol)[:8] == b"\x89PNG\r\n\x1a\n"
    assert "<svg" in chem.structure_svg(mol)
