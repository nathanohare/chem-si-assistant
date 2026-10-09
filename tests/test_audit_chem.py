"""Fixes from the audit of the chemistry helpers: generic atoms, adducts typed with
sub/superscripts, dictionary files, nested brackets in NMR reports, Qt positions after
non-BMP characters, ¹³C symmetry, ¹H counts, drawing settings and typed M formulas."""

import math
from pathlib import Path

import pytest
from rdkit import Chem

from si_assistant.core import chem, checks, fields, nmr_parse, spelling
from si_assistant.core.compound_ids import IdReport


def molblock(smiles: str, add_hs: bool = False) -> str:
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToMolBlock(Chem.AddHs(mol) if add_hs else mol)


# ---------------------------------------------------------------------- generic atoms

def test_generic_atoms_raise_chem_error_not_runtime_error():
    r_block = molblock("*CC")                    # RDKit writes the dummy atom as "R"
    assert " R " in r_block
    for text in (r_block, "*C", r_block.replace(" R ", " Q ")):
        mol = chem.parse_structure(text)         # still readable (general schemes need it)
        assert chem.generic_atoms(mol)
        with pytest.raises(chem.ChemError, match="generic atom"):
            chem.element_counts(mol)
        with pytest.raises(chem.ChemError, match="generic atom"):
            chem.mol_info(mol)
        with pytest.raises(chem.ChemError, match="generic atom"):
            chem.check_specific(mol)
    ordinary = chem.parse_structure("CCO")
    assert chem.generic_atoms(ordinary) == []
    chem.check_specific(ordinary)                # no error
    assert chem.mol_info(ordinary).formula == "C2H6O"


def test_canonical_smiles_works_with_generic_atoms():
    r_mol = chem.parse_structure(molblock("*CCF"))
    assert chem.canonical_smiles(r_mol) == Chem.MolToSmiles(Chem.MolFromSmiles("*CCF"))
    ordinary = chem.parse_structure("OCC")
    assert chem.canonical_smiles(ordinary) == chem.mol_info(ordinary).smiles == "CCO"


# ---------------------------------------------------------------------- adducts

DIOL = chem.mol_info(chem.parse_structure("OCC(O)c1ccccc1"))


@pytest.mark.parametrize("typed, plain", [
    ("[M+NH₄]+", "[M+NH4]+"),
    ("[M+H-H₂O]+", "[M+H-H2O]+"),
    ("[M+H–H2O]+", "[M+H-H2O]+"),           # en dash, as pasted from Word
    ("[M+K]⁺", "[M+K]+"),
    ("[M−H]⁻", "[M-H]-"),
    ("[M]⁺•", "[M]+•"),
    ("[M+2H]²⁺", "[M+2H]2+"),
    ("[M+Li]⁺", "[M+Li]+"),
])
def test_adducts_with_sub_and_superscripts(typed, plain):
    a, b = chem.ion_mz(DIOL, typed), chem.ion_mz(DIOL, plain)
    assert (a.formula, a.adduct.charge, a.adduct.radical) == \
        (b.formula, b.adduct.charge, b.adduct.radical)
    assert a.mz == pytest.approx(b.mz, abs=1e-9)
    assert a.adduct.label == typed               # kept as typed


def test_adduct_known_values_and_display_forms():
    assert chem.ion_mz(DIOL, "[M+H-H₂O]+").formula == "C8H9O"
    assert round(chem.ion_mz(DIOL, "[M+H–H2O]+").mz, 4) == 121.0648
    assert chem.ion_mz(DIOL, "[M+NH₄]+").formula == "C8H14NO2"
    assert chem.parse_adduct("[M+2H]²⁺").charge == 2
    assert chem.parse_adduct("[M]⁺•").radical
    for label in fields.ADDUCTS:                 # the list's own display form reads back
        a, b = chem.parse_adduct(chem.adduct_display(label)), chem.parse_adduct(label)
        assert (a.delta, a.charge, a.multimer, a.radical) == \
            (b.delta, b.charge, b.multimer, b.radical)


@pytest.mark.parametrize("typed, symbol, mass_number, mz", [
    ("[M+⁷Li]+", "Li", 7, 145.0835), ("[M+¹⁰⁷Ag]+", "Ag", 107, 244.9726),
    ("[M+¹⁰⁹Ag]⁺", "Ag", 109, 246.9723), ("[M+³⁵Cl]−", "Cl", 35, 173.0375),
])
def test_superscript_mass_number_in_an_adduct_is_an_isotope(typed, symbol, mass_number, mz):
    # (it used to be read as a count: 107 Ag atoms, m/z 11576.9)
    ion = chem.ion_mz(DIOL, typed)
    assert dict(ion.adduct.delta) == {(symbol, mass_number): 1}
    assert f"[{mass_number}{symbol}]" in ion.formula
    assert round(ion.mz, 4) == mz
    assert chem.isotope_pattern(DIOL, typed)[0].mz == pytest.approx(ion.mz)


@pytest.mark.parametrize("label", ["[M+H.H2O]+", "[M+Na,]+", "[M+(CH3)2CO+H]+",
                                   "[M+⁹⁹⁹Ag]+", "[M+H²O]+", "[M+Na⁺]+"])
def test_adducts_with_unreadable_parts_are_errors(label):
    with pytest.raises(chem.ChemError):
        chem.parse_adduct(label)


@pytest.mark.parametrize("label, words", [("[M+H]0+", "charge"), ("[M+H]00+", "charge"),
                                          ("[M]0-", "charge"), ("[0M+H]+", "number of M")])
def test_adducts_with_zero_charge_or_no_m_are_errors(label, words):
    # (a 0 charge used to divide by zero in ion_mz; 0 M gave the m/z of the adduct alone)
    with pytest.raises(chem.ChemError, match=words):
        chem.ion_mz(DIOL, label)


# ---------------------------------------------------------------------- dictionary files

def test_unreadable_dictionary_is_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "my_dictionary.txt"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    real = Path.read_bytes

    def denied(self):
        if self == path:
            raise PermissionError("locked")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert spelling.read_words(path) == set()    # never stops the program
    with pytest.raises(OSError):
        spelling.add_word(path, "delta")
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be"])
def test_utf16_dictionary_is_read_and_rewritten_as_utf8(tmp_path, encoding):
    path = tmp_path / "dictionary.txt"
    data = "Chemdrawish\r\nfoobarine\r\n".encode(encoding)
    if encoding == "utf-16-be":
        data = b"\xfe\xff" + data                # Notepad's "UTF-16 BE" has a BOM
    path.write_bytes(data)
    assert spelling.read_words(path) == {"Chemdrawish", "foobarine"}
    assert spelling.add_word(path, "newword")
    assert path.read_bytes().decode("utf-8") == "Chemdrawish\nfoobarine\nnewword\n"


def test_missing_dictionary_is_created(tmp_path):
    path = tmp_path / "sub" / "dictionary.txt"
    assert spelling.read_words(path) == set()
    assert spelling.add_word(path, "quxone")
    assert path.read_text(encoding="utf-8") == "quxone\n"


# ---------------------------------------------------------------------- NMR brackets

def test_nested_brackets_keep_the_peak_details():
    text = ("1H NMR (400 MHz, CDCl3) δ 7.30 (d, J = 8.0 Hz, 2H), "
            "4.10 (sept, J = 7.0 Hz, 1H, CH(CH3)2), 1.45 (s, 9H, C(CH3)3), "
            "1.20 (d, J = 7.0 Hz, 6H, CH(CH3)2)")
    peaks = nmr_parse.peaks(text)
    assert [(p.shift, p.multiplicity, p.integration) for p in peaks] == [
        (7.3, "d", 2.0), (4.1, "sept", 1.0), (1.45, "s", 9.0), (1.2, "d", 6.0)]
    assert peaks[2].details == "s, 9H, C(CH3)3"
    assert nmr_parse.proton_count(text) == 18
    assert nmr_parse.peaks("δ 4.46 (dt, J(H,F) = 47.3, J = 5.9 Hz, 2H)")[0].integration == 2
    assert nmr_parse.carbon_signal_count("13C NMR δ 162.3 (d, J(C,F) = 247 Hz), 20.8") == 2


def test_unbalanced_brackets_read_as_before():
    assert nmr_parse.carbon_signal_count("δ 171.2 (C=O)), 128.9 (2C), 20.8") == 3
    assert [p.shift for p in nmr_parse.peaks("δ 7.30 (d, 2H)), 1.20 (s, 3H)")] == [7.3, 1.2]
    (only,) = nmr_parse.peaks("δ 7.30 (d, 2H, 1.20 (s, 3H)")    # no closing bracket
    assert (only.shift, only.details) == (7.3, "")
    # Flat brackets, as before
    text = ("1H NMR (400 MHz, CDCl3) δ 7.36 – 7.29 (m, 5H), 6.52 (d, J = 7.9 Hz, 1H), "
            "2.08 (s, 3H)")
    assert [(p.shift, p.end, p.details) for p in nmr_parse.peaks(text)] == [
        (7.36, 7.29, "m, 5H"), (6.52, None, "d, J = 7.9 Hz, 1H"), (2.08, None, "s, 3H")]


# ---------------------------------------------------------------------- ¹³C symmetry and ¹H

@pytest.mark.parametrize("smiles, carbons", [
    ("C[C@@H](O)[C@H](C)O", 2),                  # meso-2,3-butanediol
    ("C[C@@H](O)[C@@H](C)O", 2),                 # (R,R)-2,3-butanediol
    ("C[C@@H]1CCCC[C@@H]1C", 4),                 # cis-1,2-dimethylcyclohexane
    ("C[C@H]1CC[C@H](C)CC1", 3),                 # trans-1,4-dimethylcyclohexane
    ("O[C@@H](c1ccccc1)[C@H](O)c1ccccc1", 5),    # meso-hydrobenzoin
    ("N[C@@H]1CCCC[C@H]1N", 3),                  # (R,R)-DACH
    ("[CH3:1][CH2:2]c1ccc(CC)cc1", 4),           # atom-map numbers are ignored
    ("[13CH3]c1ccc(C)cc1", 6),                   # a ¹³C label still counts
    ("Cc1ccccc1", 5),
])
def test_unique_carbons_ignore_stereo(smiles, carbons):
    assert chem.unique_carbons(chem.parse_structure(smiles)) == carbons


def test_hydrogen_counts_leave_out_d_and_count_explicit_h():
    count = chem.hydrogen_counts
    assert count(chem.parse_structure("[2H]C([2H])([2H])O")) == (1, 1)
    assert count(chem.parse_structure("[2H]C([2H])([2H])c1ccccc1")) == (5, 0)
    assert count(chem.parse_structure("CO[2H]")) == (3, 0)
    assert count(chem.parse_structure("[3H]C")) == (3, 0)
    assert count(chem.parse_structure(molblock("CO", add_hs=True))) == (4, 1)
    assert count(chem.parse_structure(molblock("NCC(=O)O", add_hs=True))) == (5, 3)
    assert count(chem.parse_structure("[H]Oc1ccccc1")) == (6, 1)
    assert count(chem.parse_structure("OCC(N)=O")) == (5, 3)     # unchanged
    assert count(chem.parse_structure("CO")) == (4, 1)


def test_checks_no_false_warnings_for_meso_deuterated_and_assignments(project):
    section = project.add_section("data", "Products")
    project.set_id_table(section.id, ["3a", "3b", "3c"])

    def make(smiles, *reports):
        c = project.add_compound(section.id)
        project.set_structure(c, smiles)
        c.values["nmr"] = [{"id": f"r{i}", "nucleus": n, "label": "", "text": t}
                           for i, (n, t) in enumerate(reports)]
        project.save_compound(c)
        return c

    def found(c, check):
        return [w for w in checks.active_warnings(project, c, IdReport()) if w.check == check]

    meso = make("C[C@@H](O)[C@H](C)O", ("13C", "13C NMR (101 MHz, CDCl3) δ 71.0, 16.7"))
    assert found(meso, "c13") == []
    tbu = make("CC(C)(C)c1ccccc1",
               ("1H", "1H NMR (400 MHz, CDCl3) δ 7.40 – 7.15 (m, 5H), 1.32 (s, 9H, C(CH3)3)"))
    assert found(tbu, "h1") == []
    d3 = make("[2H]C([2H])([2H])c1ccccc1",
              ("1H", "1H NMR (400 MHz, CDCl3) δ 7.30 – 7.10 (m, 5H)"))
    assert found(d3, "h1") == []


# ---------------------------------------------------------------------- drawing settings

def settings(**raw):
    return chem.DrawingSettings.from_settings({"structure_drawing": raw})


def test_drawing_settings_read_hand_edited_on_off_values():
    assert settings(colour_atoms="false").colour_atoms is False
    assert settings(colour_atoms="0", heteroatom_h="0").heteroatom_h is False
    assert settings(colour_atoms="TRUE").colour_atoms is True
    assert settings(label_methyls=1).label_methyls is True
    assert settings(colour_atoms="maybe").colour_atoms is False          # the default
    assert settings(heteroatom_h="maybe").heteroatom_h is True           # the default
    assert settings(colour_atoms=True, heteroatom_h=False).heteroatom_h is False


@pytest.mark.parametrize("name", ["bond_length_cm", "line_width_pt", "font_size_pt"])
def test_drawing_settings_ignore_impossible_sizes(name):
    default = getattr(chem.DrawingSettings(), name)
    for bad in (math.inf, math.nan, 1e300, "inf", -3, 0, "nonsense"):
        assert getattr(settings(**{name: bad}), name) == default
    for bad in (True, False):                     # (float(True) is a size of 1)
        assert getattr(settings(**{name: bad}), name) == default
    assert getattr(settings(**{name: 3.0}), name) == 3.0                # hand-edited, kept
    mol = chem.parse_structure("Oc1ccccc1")
    top = settings(**{name: chem._DRAWING_MAX[name]})
    assert "<svg" in chem.structure_svg(mol, settings=top)
    assert chem.structure_png(mol, settings=top)[:4] == b"\x89PNG"
    assert "<svg" in chem.structure_svg(mol, settings=settings(**{name: math.inf}))


# ---------------------------------------------------------------------- typed M formulas

def test_typed_m_formula_with_an_unknown_isotope_is_an_error():
    assert chem.parse_m_formula("C5[13C]H6Br")[("C", 13)] == 1
    for typed in ("C5[999C]H6", "C6[2D]H5"):    # (used to give a mass of 0 for that atom)
        with pytest.raises(chem.ChemError, match="isotope"):
            chem.parse_m_formula(typed)


def test_tritium_can_be_typed_and_d_equals_2h():
    tritiated = chem.mol_info(chem.parse_structure("[3H]C"))
    typed = chem.parse_m_formula(tritiated.formula)               # "CH3T", as shown
    assert chem.counts_mass(typed) == pytest.approx(tritiated.exact_mass, abs=1e-9)
    assert chem.same_formula(typed, tritiated.counts)
    d3 = chem.mol_info(chem.parse_structure("[2H]C([2H])([2H])OC(=O)c1ccccc1"))
    typed = chem.parse_m_formula("C8H5[2H]3O2")
    assert chem.hill_formula(typed) == "C8H5[2H]3O2"                # notation is kept
    assert chem.same_formula(typed, d3.counts)
    assert chem.same_formula(chem.parse_m_formula("CH3[3H]"), tritiated.counts)
    assert not chem.same_formula(chem.parse_m_formula("C8H6[2H]2O2"), d3.counts)
    assert chem.parse_formula("T") == {("T", 0): 1}
    assert chem.parse_formula("Ti") == {("Ti", 0): 1}                # titanium is still Ti


# ---------------------------------------------------------------------- Qt positions

@pytest.fixture
def editor(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    ed = RichTextEditor()
    ed.resize(700, 300)
    yield ed
    ed.deleteLater()


def test_qt_offsets_round_trip():
    from si_assistant.ui import text_checks
    q = text_checks.qt_offsets("a𝐽b🧪c")
    assert q == [0, 1, 3, 4, 6, 7]
    assert [text_checks.python_index(q, p) for p in q] == [0, 1, 2, 3, 4, 5]
    assert text_checks.qt_offsets("plain") == [0, 1, 2, 3, 4, 5]


def test_formatting_checks_after_a_non_bmp_character(editor):
    from si_assistant.ui import text_checks
    doc = editor.edit.document()
    editor.set_markup("𝐽 = 7.2 Hz, in CDCl_{3}")
    assert text_checks.all_issues(doc) == []          # already subscripted: no problem
    editor.set_markup("𝐽 = 7.2 Hz in CDCl3 and Na2SO4")
    assert text_checks.fix_all(doc) == 2
    assert editor.markup() == "𝐽 = 7.2 Hz in CDCl_{3} and Na_{2}SO_{4}"
    editor.set_markup("🧪 25°C")                       # an edit that inserts text
    assert text_checks.fix_all(doc) == 1
    assert editor.markup() == "🧪 25 °C"


def underlined(editor, colour):
    from PySide6.QtGui import QTextCharFormat
    from si_assistant.ui import text_checks
    out = []
    block = editor.edit.document().begin()
    while block.isValid():
        q = text_checks.qt_offsets(block.text())
        for r in block.layout().formats():
            if (r.format.underlineStyle() == QTextCharFormat.UnderlineStyle.WaveUnderline
                    and r.format.underlineColor().name() == colour):
                a = text_checks.python_index(q, r.start)
                b = text_checks.python_index(q, r.start + r.length)
                out.append(block.text()[a:b])
        block = block.next()
    return out


def test_underlines_and_suggestions_after_a_non_bmp_character(editor):
    from si_assistant.ui import text_checks
    editor.set_markup("🧪 The mixure was stirred with PdCl2")
    assert underlined(editor, text_checks.SPELLING_COLOUR) == ["mixure"]
    assert underlined(editor, text_checks.FORMATTING_COLOUR) == ["PdCl2"]
    edit = editor.edit
    text = edit.toPlainText()
    q = text_checks.qt_offsets(text)
    menu = edit.createStandardContextMenu()
    text_checks.add_check_actions(menu, edit, q[text.index("mixure")] + 1)
    next(a for a in menu.actions() if a.text() == "mixture").trigger()
    assert editor.markup() == "🧪 The mixture was stirred with PdCl2"
    text = edit.toPlainText()
    q = text_checks.qt_offsets(text)
    menu = edit.createStandardContextMenu()
    text_checks.add_check_actions(menu, edit, q[text.index("PdCl2")] + 1)
    next(a for a in menu.actions() if a.text().startswith("Fix:")).trigger()
    assert editor.markup() == "🧪 The mixture was stirred with PdCl_{2}"


def test_formula_toggle_after_a_non_bmp_character(editor):
    from si_assistant.ui import text_checks
    editor.set_markup("𝛼-pinene in CH2Cl2 ")
    doc = editor.edit.document()
    assert text_checks.subscript_formula(doc, doc.characterCount() - 2)
    assert editor.markup() == "𝛼-pinene in CH_{2}Cl_{2} "
