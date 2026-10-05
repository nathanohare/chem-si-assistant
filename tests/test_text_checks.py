"""Spelling and chemistry-formatting checks without the user interface (SPEC §13k.1)."""

import pytest

from si_assistant.compile.text import format_chemistry
from si_assistant.core import chemtext, spelling
from si_assistant.core.markup import NORMAL, SUB, SUPER, Paragraph, Run, serialize
from si_assistant.core.project import DICTIONARY_FILE, Project, ProjectError


def kinds(text, formats=None):
    return [(i.kind, text[i.start:i.end], i.fixed) for i in chemtext.find_issues(text, formats)]


# ---------------------------------------------------------------------- formatting check

@pytest.mark.parametrize("text,expected", [
    ("with PdCl2 (5 mol %)", [("formula", "PdCl2", "PdCl₂")]),
    ("in CH2Cl2", [("formula", "CH2Cl2", "CH₂Cl₂")]),
    ("under N2", [("formula", "N2", "N₂")]),
    ("Pd(PPh3)4", [("formula", "Pd(PPh3)4", "Pd(PPh₃)₄")]),
    ("in CDCl3", [("solvent", "CDCl3", "CDCl₃")]),
    ("in DMSO-d6", [("solvent", "DMSO-d6", "DMSO-d₆")]),
    ("1.2mmol", [("unit", "1.2mmol", "1.2 mmol")]),
    ("J = 8.0Hz", [("unit", "8.0Hz", "8.0 Hz")]),
    ("at 25°C", [("unit", "25°C", "25 °C")]),
    ("5mol% catalyst", [("unit", "5mol%", "5 mol %")]),
    ("1.5g and 1M HCl", [("unit", "1.5g", "1.5 g"), ("unit", "1M", "1 M")]),
    ("δ 7.36-7.29 (m)", [("range", "7.36-7.29", "7.36–7.29")]),
    ("1H NMR and 13C NMR", [("nucleus", "1H", "¹H"), ("nucleus", "13C", "¹³C")]),
])
def test_formatting_problems_are_found(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize("text", [
    "Table S1 and Figure S2, C18 column, SN2 and E2 reactions, TS1, IC50",
    "compound 3a and 3g, 12h, (m, 2H), 2-methylfuran, 92% yield, THF, 25 °C, 1.2 mmol",
    "see https://doi.org/10.1021/acs.orglett.0c01234-5 for details",
    "7.36–7.29 (en dash already)",
])
def test_things_that_are_fine_are_left_alone(text):
    assert kinds(text) == []


def test_already_formatted_text_is_not_flagged():
    text = "PdCl2 in DMSO-d6, 1H NMR"
    formats = [(NORMAL, False)] * len(text)
    formats[4] = (SUB, False)                                   # PdCl₂
    formats[text.index("d6")] = (NORMAL, True)                  # italic d
    formats[text.index("d6") + 1] = (SUB, False)                # ₆
    formats[text.index("1H")] = (SUPER, False)                  # ¹H
    assert kinds(text, formats) == []
    formats[text.index("d6")] = (NORMAL, False)                 # d no longer italic
    assert kinds(text, formats) == [("solvent", "DMSO-d6", "DMSO-d₆")]


def test_fix_edits():
    issue = chemtext.find_issues("8.0Hz")[0]
    assert chemtext.apply_edits("8.0Hz", issue.edits) == "8.0 Hz"
    issue = chemtext.find_issues("5mol%")[0]
    assert chemtext.apply_edits("5mol%", issue.edits) == "5 mol %"
    issue = chemtext.find_issues("DMSO-d6")[0]
    assert {(e.start, e.end, e.valign, e.italic) for e in issue.edits} == {
        (5, 6, None, True), (6, 7, SUB, None)}


def test_formula_words_and_their_exceptions():
    for word in ("H2SO4", "Et3N", "NH4Cl", "Cl2", "CO2Me", "Cs2CO3"):
        assert chemtext.is_formula(word), word
    for word in ("SN2", "SN1", "S1", "C18", "E2", "3a", "12b", "TS2", "IC50", "THF", "B3LYP",
                 "N2", "H2"):                           # H2, N2… need context (see below)
        assert not chemtext.is_formula(word), word
    assert chemtext.formula_digits("dried over Na2SO4", end=17) == [13, 16]
    assert chemtext.formula_digits("Na2SO4 and", end=10) == []


def test_compiling_uses_the_same_exceptions():
    show = lambda runs: serialize([Paragraph(runs)])  # noqa: E731
    assert show(format_chemistry([Run("an SN2 reaction under N2")])) == \
        "an SN2 reaction under N_{2}"


# ---------------------------------------------------------------------- spelling

def words(text):
    return [text[a:b] for a, b in spelling.check_spans(text)]


def test_what_the_spelling_check_skips():
    text = ("The (E)-alkene, 2-methylfuran, N,N-dimethyl, n-BuLi and EtOAc/hexanes in THF "
            "and DMSO (see https://doi.org/10.1/x, a@b.org) gave 3a; α-pinene, x and ￼.")
    assert words(text) == ["The", "and", "hexanes", "in", "and", "see", "gave", "and"]


def test_hyphenated_words_are_checked_part_by_part():
    assert words("tert-butyl well-known") == ["tert", "butyl", "well", "known"]
    assert words("O-benzyl t-butyl") == []                 # locants and prefixes
    assert words("CHROMATOGRAPHY") == ["CHROMATOGRAPHY"]   # long capitals are words
    assert words("Nathan's") == ["Nathan"]


def test_the_speller_knows_english_chemistry_and_systematic_names():
    s = spelling.Speller()
    for w in ("purified", "Silica", "hexanes", "rotamers", "eluent", "vacuo", "mmol",
              "dichloromethane", "tetrahydrofuran", "triethylamine", "thiophene", "tosyl"):
        assert s.known(w), w
    for w in ("teh", "recieve", "chromatograhpy", "seperate"):
        assert not s.known(w), w
    text = "Purified by flash chromatograhpy on silica (teh crude)."
    assert [text[a:b] for a, b in s.misspelled(text)] == ["chromatograhpy", "teh"]
    assert s.suggestions("chromatograhpy")[0] == "chromatography"
    assert s.suggestions("Teh")[0] == "The"                 # keeps the capital


def test_own_dictionaries_and_ignore():
    s = spelling.Speller()
    assert not s.known("Fooxane")
    s.set_project_words(["fooxane"])
    assert s.known("Fooxane") and s.known("fooxanes")
    s.set_project_words([])
    s.set_personal_words(["Barzyne"])
    assert s.known("barzyne") and not s.known("quxone")
    s.ignore("Quxone")
    assert s.known("quxone")
    assert "barzyne" in s.suggestions("barzyme")


def test_the_chemistry_word_list_is_built_in():
    words_ = spelling.chem_words()
    assert len(words_) > 200   # (people's names left out since §13m: 249 words)
    assert {"chemdraw", "mestrenova", "phos"} <= words_
    assert all(w == w.lower() for w in words_)
    # Nothing is listed twice: words of the chemistry dictionary and the named-reaction
    # list are left out of chem_words.txt (tools/build_chem_words.py), but still known.
    assert not words_ & spelling.chem_dictionary()
    assert not words_ & spelling.name_reactions()
    s = spelling.Speller()
    for w in ("etoac", "eluent", "rotamers", "vacuo"):
        assert s.known(w), w


# ---------------------------------------------------------------------- SPEC §13l.6

def test_the_chemistry_dictionary_is_bundled_with_its_licence():
    words_ = spelling.chem_dictionary()
    assert len(words_) > 100000
    assert {"diastereotopic", "regioselective", "pyrrolidine", "celite"} <= words_
    licence = (spelling.DATA_FOLDER / "azman_chemistry_dictionary_LICENSE.txt").read_text(
        encoding="utf-8")
    for needed in ("Azman", "10.1021/ed2002994", "creativecommons.org/licenses/by/3.0/us",
                   "Version 3.0"):
        assert needed in licence


def test_the_chemistry_dictionary_is_read_only_when_needed():
    s = spelling.Speller()
    assert s._dictionary is None                  # not at start-up
    s.known("hello")
    assert s.dictionary is spelling.chem_dictionary()


def test_words_from_the_chemistry_dictionary_and_name_reactions():
    s = spelling.Speller()
    for w in ("diastereotopic", "Regioselective", "pyrrolidines", "Suzuki", "Miyaura",
              "Buchwald", "Hartwig", "Sonogashira", "Mitsunobu", "Horner", "Wadsworth",
              "Emmons", "Swern", "Bestmann", "Ohira", "McMurry", "Fétizon"):
        assert s.known(w), w
    text = "Suzuki–Miyaura coupling, then a Horner–Wadsworth–Emmons olefination"
    assert s.misspelled(text) == []
    # Suggestions also come from the chemistry dictionary.
    assert "diastereotopic" in s.suggestions("diastereotopci")


def test_a_small_chemistry_dictionary_can_be_given():
    s = spelling.Speller(chem=[], dictionary=["Fooxazole"])
    assert s.known("fooxazole") and not s.known("barxazole")


@pytest.mark.parametrize("word", [
    "MeCN", "EtOAc", "nBuLi", "iPrOH", "tBuOK", "PhMe", "PhMgBr", "LiAlH", "NaOtBu", "BnBr",
    "KOtBu", "iPrMgCl", "TBSCl", "TBDPSCl", "pTsOH", "MeOD", "AcOH", "BocNH", "NaHMDS",
    "secBuLi", "PhNTf"])
def test_abbreviations_made_of_element_symbols_and_groups(word):
    assert spelling.is_abbreviation(word)
    assert words(f"with {word} in") == ["with", "in"]      # accepted as written


@pytest.mark.parametrize("word", [
    "Me", "Bu", "Ph", "Cu", "Hello", "Teh", "THe", "MeCNx", "AbcDef", "McMurry", "DavePhos",
    "Etoac", "nBuLiQ", "Me-CN", "MeC2N"])
def test_what_is_not_an_abbreviation(word):
    # One capital (a mistyped first word of a sentence, "Bu") is not covered; nor are words
    # with letters that are no element or group.
    assert not spelling.is_abbreviation(word)


def test_words_with_capitals_inside_are_checked():
    s = spelling.Speller()
    text = "AbcDef and MeCNx, but MeCN, mCPBA, NMRs, iPr, tBu, pH, ChemDraw, TopSpin, ReactIR, " \
           "XPhos, RuPhos and tBuXPhos are fine"
    assert [text[a:b] for a, b in s.misspelled(text)] == ["AbcDef", "MeCNx"]
    s.set_personal_words(["AbcDef"])
    assert [text[a:b] for a, b in s.misspelled(text)] == ["MeCNx"]


def test_words_with_capitals_inside_can_be_skipped_as_before(monkeypatch):
    monkeypatch.setattr(spelling, "CHECK_INNER_CAPITALS", False)
    assert words("AbcDef MeCNx Word") == ["Word"]


def test_dictionary_files(tmp_path):
    path = tmp_path / "words.txt"
    assert spelling.add_word(path, "Zeta")
    assert spelling.add_word(path, "alpha")
    assert not spelling.add_word(path, "ZETA")              # already there
    assert path.read_text(encoding="utf-8") == "alpha\nZeta\n"
    assert spelling.read_words(path) == {"alpha", "Zeta"}
    assert spelling.read_words(tmp_path / "missing.txt") == set()


def test_the_project_dictionary(project):
    assert project.dictionary_words() == set()
    assert project.add_dictionary_word("Fooxane")
    assert not project.add_dictionary_word("fooxane")
    assert (project.folder / DICTIONARY_FILE).read_text(encoding="utf-8") == "Fooxane\n"
    again = Project.open(project.folder)
    assert again.dictionary_words() == {"Fooxane"}
    again.read_only = True
    with pytest.raises(ProjectError):
        again.add_dictionary_word("other")


# ---------------------------------------------------------------------- review fixes (S7)

show = lambda runs: serialize([Paragraph(runs)])  # noqa: E731


@pytest.mark.parametrize("word,expected", [
    ("Pd2(dba)3", "Pd₂(dba)₃"), ("Ni(cod)2", "Ni(cod)₂"), ("Ru(bpy)3Cl2", "Ru(bpy)₃Cl₂"),
    ("B2pin2", "B₂pin₂"), ("Boc2O", "Boc₂O"), ("Ti(OiPr)4", "Ti(OiPr)₄"),
    ("iPr2NEt", "iPr₂NEt"), ("nBu4NI", "nBu₄NI"), ("tBu3P", "tBu₃P"), ("nBu3SnH", "nBu₃SnH"),
    ("(iPr)2NEt", "(iPr)₂NEt"), ("[Ir(cod)Cl]2", "[Ir(cod)Cl]₂"), ("Cp2ZrCl2", "Cp₂ZrCl₂"),
    ("PdCl2(PPh3)2", "PdCl₂(PPh₃)₂"), ("i-Pr2NEt", "i-Pr₂NEt"),
])
def test_formulas_with_ligands_and_alkyl_prefixes(word, expected):
    assert chemtext.is_formula(word), word
    assert kinds(word) == [("formula", word, expected)]


@pytest.mark.parametrize("word", [
    "OD600", "NIH3T3", "COVID19", "SI1", "HCT116", "PC3", "PI3K", "CDK2", "CYP2D6", "CB1",
    "P2Y12", "INT1", "ID50", "LD50", "HSP90", "NS3", "HIV1", "Spin2", "Copy2"])
def test_biology_and_other_labels_are_not_formulas(word):
    assert not chemtext.is_formula(word)
    assert kinds(f"with {word} cells") == []


@pytest.mark.parametrize("text,expected", [
    # one-element molecules: only when the words around them say it is the gas or reagent
    ("under N2.", "under N_{2}."),
    ("under an atmosphere of N2", "under an atmosphere of N_{2}"),
    ("purged with N2", "purged with N_{2}"),
    ("H2 (1 atm)", "H_{2} (1 atm)"),
    ("an N2 atmosphere in an O2-free, N2-filled glovebox",
     "an N_{2} atmosphere in an O_{2}-free, N_{2}-filled glovebox"),
    ("Br2 (1.1 equiv) in CH2Cl2", "Br_{2} (1.1 equiv) in CH_{2}Cl_{2}"),
    # …and not when they are atom labels or other names
    ("δ 7.25 (d, J = 8.4 Hz, 2H, H2, H6)", "δ 7.25 (d, J = 8.4 Hz, 2H, H2, H6)"),
    ("NOE between H2 and H8", "NOE between H2 and H8"),
    ("N1/N2 ratio; N2-alkylation", "N1/N2 ratio; N2-alkylation"),
    ("dopamine D2 receptor, F2 dimension", "dopamine D2 receptor, F2 dimension"),
    ("Pd1–Cl2 distance", "Pd1–Cl2 distance"),
])
def test_one_element_molecules_need_context(text, expected):
    assert show(format_chemistry([Run(text)])) == expected
    # The check flags exactly what compiling would subscript.
    flagged = sum(1 for k, _, _ in kinds(text) if k == "formula")
    assert flagged == sum(1 for w in expected.split() if "_{" in w)


def test_which_one_element_molecules_are_flagged():
    assert [t for _, t, _ in kinds("1H, H2), under N2 with H2 (balloon)")] == ["N2", "H2"]
    assert kinds("δ 8.12 (d, J = 2.0 Hz, 1H, H2), 7.9 (s, 1H, H3)") == []


@pytest.mark.parametrize("text", [
    "CAS 7647-01-0", "Pd(OAc)2 (CAS 3375-31-3)", "B3LYP/6-31G(d) and 6-311+G(d,p)",
    "dated 2023-10-04", "notebook NPO-2-117", "ISBN 978-0-19-850346-0", "C2-3 bond"])
def test_codes_dates_and_basis_sets_are_not_ranges(text):
    assert [k for k, _, _ in kinds(text) if k == "range"] == []


def test_real_ranges_are_still_found():
    assert kinds("entries 1-3, 2-3 h, pages 123-130.") == [
        ("range", "1-3", "1–3"), ("range", "2-3", "2–3"), ("range", "123-130", "123–130")]


def test_words_left_as_written():
    leave = {"cdk2", "pdcl2", "7.36-7.29"}
    text = "CDK2 with PdCl2, δ 7.36-7.29, Na2SO4"
    assert [t for _, t, _ in [(i.kind, text[i.start:i.end], i.fixed)
                              for i in chemtext.find_issues(text, leave=leave)]] == ["Na2SO4"]
    assert chemtext.formula_digits("PdCl2 Na2SO4", leave=leave) == [8, 11]
    from si_assistant.compile.text import leave_alone
    with leave_alone(["PdCl2", "DMSO-d6"]):
        assert show(format_chemistry([Run("PdCl2, Na2SO4, DMSO-d6")])) == \
            "PdCl2, Na_{2}SO_{4}, DMSO-d6"
    assert show(format_chemistry([Run("PdCl2")])) == "PdCl_{2}"   # only while building


def test_compiling_leaves_the_project_dictionary_words_alone(project):
    from si_assistant.compile import document
    project.add_dictionary_word("CDK2")
    project.add_dictionary_word("PdCl2")
    seen = []
    real = document.Builder.document
    monkey = lambda self: seen.append(format_chemistry([Run("PdCl2 and CH2Cl2")])) or real(self)  # noqa: E731
    document.Builder.document = monkey
    try:
        document.build(project, document.CompileOptions(), lambda p: None)
    finally:
        document.Builder.document = real
    assert show(seen[0]) == "PdCl2 and CH_{2}Cl_{2}"


def test_dictionary_files_in_other_encodings_and_with_comments(tmp_path):
    path = tmp_path / "dictionary.txt"
    path.write_bytes("Buchwald\r\nna\xefve\r\n".encode("cp1252"))   # Notepad "ANSI"
    assert spelling.read_words(path) == {"Buchwald", "naïve"}
    path.write_bytes(b"\xff\xfe\x00garbage")                       # never raises
    assert isinstance(spelling.read_words(path), set)
    path.write_text("# words for our group\n# keep sorted\nalpha\nzeta\n", encoding="utf-8")
    assert spelling.add_word(path, "Gamma")
    assert path.read_text(encoding="utf-8") == \
        "# words for our group\n# keep sorted\nalpha\nGamma\nzeta\n"
    assert spelling.read_words(path) == {"alpha", "Gamma", "zeta"}


def test_project_with_an_ansi_dictionary_opens(project):
    (project.folder / DICTIONARY_FILE).write_bytes("Buchwald\nna\xefve\n".encode("cp1252"))
    assert project.dictionary_words() == {"Buchwald", "naïve"}


def test_everyday_si_words_are_known():
    s = spelling.Speller()
    for w in ("electrospray", "butyllithium", "diisobutylaluminum", "sonication", "photoredox",
              "propanol", "tetramethylsilane", "pinacolborane", "epoxide", "diene", "enone",
              "acetophenone", "azetidine", "triturated", "portionwise", "Weinreb", "Orbitrap",
              "Avance", "Aldrich", "Kugelrohr", "olefination", "tributyltin"):
        assert s.known(w), w
    for w in ("areaction", "byield", "couldn", "solvant", "mixure", "stired"):
        assert not s.known(w), w
