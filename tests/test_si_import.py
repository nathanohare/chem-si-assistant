"""Importing an existing SI (SPEC §13u, phase I1): reading Word and PDF files, recognising
general procedures and compounds, sorting their data into fields, the review window and
writing into a project. Made-up SI text only (in the forms real SIs use); the PDF tests
read the example SI shipped with the program."""

import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from si_assistant import resources
from si_assistant.core import chem
from si_assistant.core import si_import as I
from si_assistant.core import si_import_apply as A
from si_assistant.core import si_import_read as R
from si_assistant.core.markup import parse
from si_assistant.core.project import Project

ROOT = Path(__file__).resolve().parent.parent


def para(markup: str, page: int = 0, **kw) -> R.Para:
    return R.Para(parse(markup)[0].runs, page=page, **kw)


def calcd(formula_m: str, adduct: str) -> str:
    """The calculated m/z, as an SI prints it (4 decimals)."""
    info = chem.info_from_counts(chem.parse_formula(formula_m))
    return f"{chem.ion_mz(info, adduct).mz:.4f}"


def compound(text: str) -> I.ImportedCompound:
    c = I.ImportedCompound("1", "x")
    I.parse_data(text, c)
    return c


# ---------------------------------------------------------------------- headings

@pytest.mark.parametrize("text,expected", [
    ("1-(4-Tolyl)propan-1-one (7b).", ("1-(4-Tolyl)propan-1-one", "7b")),
    ("3,4-Dihydro-2H-pyran-2-one (4)", ("3,4-Dihydro-2H-pyran-2-one", "4")),
    ("(S)-3-Phenylbutan-2-ol ((S)-9c):", ("(S)-3-Phenylbutan-2-ol", "9c")),
    ("Methyl 2-oxocyclohexane-1-carboxylate (S12)", ("Methyl 2-oxocyclohexane-1-carboxylate", "S12")),
    ("Compound 12", ("", "12")),
    ("N-Benzyl-2-iodobenzamide (3b′)", ("N-Benzyl-2-iodobenzamide", "3b′")),
])
def test_compound_headings(text, expected):
    assert I.compound_heading(text) == expected


@pytest.mark.parametrize("text", [
    "The ketone was obtained from the alcohol (3a).",
    "Prepared according to General Procedure A (2a).",
    "pyridin-2(1H)-one",
    "See Table S2 (entry 4).",
])
def test_sentences_are_not_compound_headings(text):
    assert I.compound_heading(text) is None


@pytest.mark.parametrize("text,expected", [
    ("General Procedure B: Suzuki coupling of aryl bromides", ("B", "Suzuki coupling of aryl bromides")),
    ("General procedure for the synthesis of amides (GP-C)", ("C", "Synthesis of amides")),
    ("GP2.", ("2", "")),
    ("2.3 General Procedure A", ("A", "")),
    ("Typical procedure for the hydrogenation", ("", "Hydrogenation")),
])
def test_gp_headings(text, expected):
    assert I.gp_heading(text) == expected


def test_a_sentence_about_a_gp_is_no_heading():
    assert I.gp_heading("General procedure A was used for all substrates.") is None


# ---------------------------------------------------------------------- data values

def test_an_acs_data_paragraph():
    mz = calcd("C10H12O2", "[M+Na]+")
    c = compound(
        "White solid (41.2 mg, 83%). Rf = 0.42 (20% EtOAc/hexanes, UV). mp 88–90 °C "
        "(EtOAc/hexanes). 1H NMR (400 MHz, CDCl3) δ 7.20 (d, J = 8.0 Hz, 2H), 2.35 (s, 3H). "
        "13C{1H} NMR (101 MHz, CDCl3) δ 170.1, 21.0. IR (ATR) 2924, 1735, 1230 cm–1. "
        f"HRMS (ESI) m/z: [M + Na]+ calcd for C10H12NaO2 {mz}; found 187.0733.")
    v = c.values
    assert v["physical_state"] == {"text": "white solid"}
    assert v["yield"] == {"mass": "41.2", "mass_unit": "mg", "percent": "83"}
    assert v["rf"] == {"value": "0.42", "eluent": "20% EtOAc/hexanes", "visualization": "UV"}
    assert v["mp"] == {"value": "88–90", "solvent": "EtOAc/hexanes"}
    assert [(r["nucleus"], r["text"]) for r in v["nmr"]] == [
        ("1H", "1H NMR (400 MHz, CDCl3) δ 7.20 (d, J = 8.0 Hz, 2H), 2.35 (s, 3H)."),
        ("13C", "13C{1H} NMR (101 MHz, CDCl3) δ 170.1, 21.0.")]
    assert v["ir"] == {"method": "ATR", "bands": "2924, 1735, 1230"}
    assert v["hrms"] == {"ionization": "ESI", "adduct": "[M+Na]+", "found": "187.0733",
                         "formula": "C10H12O2"}
    assert c.hrms_calcd == mz
    assert c.unrecognised == [] and c.warnings == []


def test_hrms_in_other_forms():
    mz = calcd("C9H11NO", "[M+H]+")
    v, printed, formula = I.parse_hrms(f"HRMS (ESI) calcd for C9H12NO [M + H]+: {mz}, Found: {mz[:-1]}9.")
    assert v == {"ionization": "ESI", "adduct": "[M+H]+", "found": mz[:-1] + "9"}
    assert (printed, formula) == (mz, "C9H12NO")
    assert I.m_formula(formula, "[M+H]+", printed) == "C9H11NO"      # the H taken off
    # The printed formula may already be M's; the printed m/z tells.
    assert I.m_formula("C9H11NO", "[M+H]+", mz) == "C9H11NO"
    v, _, _ = I.parse_hrms("HRMS (EI) m/z: M+ calcd for C8H8O 120.0570, found 120.0573")
    assert v["adduct"] == "[M]+" and v["ionization"] == "EI"


def test_an_isotope_peak_m_z_still_gives_the_formula():
    info = chem.info_from_counts(chem.parse_formula("C8H9BrO"))
    br81 = max(chem.isotope_pattern(info, "[M+Na]+"), key=lambda p: "81Br" in p.formula)
    assert I.m_formula("C8H9BrNaO", "[M+Na]+", f"{br81.mz:.4f}") == "C8H9BrO"


def test_a_wrong_printed_formula_is_reported_not_used():
    c = compound("HRMS (ESI) calcd for C13H26NO [M+H]+: 202.1226, found 202.1229.")
    assert "formula" not in c.values["hrms"]
    assert any("does not give the calculated m/z" in w for w in c.warnings)


def test_rotation_ee_dr_ea_ccdc():
    c = compound("[α]D25 = −12.4 (c 1.00, CHCl3). HPLC (Chiralpak IA, 5% iPrOH/hexanes, "
                 "1.0 mL/min, 220 nm): tR (major) = 9.8 min, tR (minor) = 11.2 min; 92% ee. "
                 "d.r. = 12:1. Anal. Calcd for C10H12O2: C, 73.15; H, 7.37. Found: C, 73.02; "
                 "H, 7.41. CCDC 2101234.")
    v = c.values
    assert v["rotation"] == {"value": "-12.4", "temperature": "25", "c": "1.00", "solvent": "CHCl3"}
    assert v["ee"]["value"] == "92% ee" and v["ee"]["method"] == "HPLC"
    assert v["ee"]["column"] == "Chiralpak IA" and v["ee"]["eluent"] == "5% iPrOH/hexanes"
    assert (v["ee"]["flow"], v["ee"]["wavelength"]) == ("1.0", "220")
    assert (v["ee"]["t_major"], v["ee"]["t_minor"]) == ("9.8", "11.2")
    assert v["dr"] == {"value": "12:1"}
    assert v["ea"] == {"formula": "C10H12O2", "calcd": "C, 73.15; H, 7.37",
                       "found": "C, 73.02; H, 7.41"}
    assert v["xray"] == {"ccdc": "2101234"}


def test_labelled_nmr_reports():
    c = compound("Major rotamer: 1H NMR (500 MHz, CDCl3) δ 6.41 (d, J = 8.1 Hz, 1H); minor "
                 "rotamer: 1H NMR (500 MHz, CDCl3) δ 7.02 (br s, 1H).")
    assert [r["label"] for r in c.values["nmr"]] == ["major rotamer", "minor rotamer"]
    assert c.values["nmr"][1]["text"].startswith("1H NMR (500 MHz")
    # a sentence before a report is no label: it is kept as text
    c = compound("Both rotamers are described together: 13C NMR (126 MHz, CDCl3) δ 168.9.")
    assert c.values["nmr"][0]["label"] == ""
    assert c.unrecognised == ["Both rotamers are described together:"]


def test_literature_sentence_and_its_citations():
    c = compound("Spectral data matched those reported in the literature.4,5")
    assert c.values["references"] == {"text": "Spectral data matched those reported in the "
                                              "literature.", "refs": []}
    assert c.cites == ["4", "5"]
    c = compound("The NMR data were consistent with those reported [7–9].")
    assert c.cites == ["7", "8", "9"]


def test_what_is_not_recognised_is_kept():
    c = compound("Colorless oil (12 mg, 40%). LRMS (EI) m/z 164 (M+).")
    assert c.values["physical_state"] == {"text": "colorless oil"}
    assert c.unrecognised == ["LRMS (EI) m/z 164 (M+)."]


def test_bold_labels_style():
    """Labels in bold before each value ("Isolated Yield:", "1H NMR:", "HRMS (ESI):",
    "TLC:", "Enantiomeric Excess:"), as many group SIs write them."""
    ion = calcd("C12H12N", "[M]+")
    c = I.ImportedCompound("2c", "N-Methyl-4-phenylpyridinium iodide")
    I.parse_data(
        "Isolated Yield: 1.25 g (64%) as a pale-yellow crystalline solid (Hygroscopic!). "
        "1H NMR: (400 MHz, CD3OD) δ 8.81 (d, J = 6.6 Hz, 2H), 4.38 (s, 3H). "
        f"HRMS (ESI): m/z calculated for [C12H12N]+: {ion}, Observed: {ion[:-1]}8. "
        "TLC: Rf= 0.35 (100% Et2O), UV – 254 nm, KMnO4 stain. "
        "Enantiomeric Excess: Determined by HPLC analysis to be 95%. CHIRALPAK® - IB "
        "(4.6 x 250mm). 5% Isopropanol, 95% n-Hexane. 0.8 mL/min, λ = 254nm. Retention "
        "times: 7.1 min (minor) 8.4 min (major).", c)
    v = c.values
    assert v["yield"] == {"mass": "1.25", "mass_unit": "g", "percent": "64"}
    assert v["physical_state"] == {"text": "pale-yellow crystalline solid"}
    assert v["nmr"][0]["text"].startswith("1H NMR (400 MHz, CD3OD) δ 8.81")   # (no colon)
    # The cation of a salt, given only as the ion: [M]+ with M charged.
    assert v["hrms"] == {"ionization": "ESI", "found": ion[:-1] + "8", "adduct": "[M]+",
                         "formula": "C12H12N+"}
    assert v["rf"] == {"value": "0.35", "eluent": "100% Et2O",
                       "visualization": "UV – 254 nm, KMnO4 stain"}
    assert v["ee"] == {"value": "95% ee", "method": "HPLC",
                       "column": "CHIRALPAK® - IB (4.6 x 250mm)",
                       "eluent": "5% Isopropanol, 95% n-Hexane", "flow": "0.8",
                       "wavelength": "254", "t_major": "8.4", "t_minor": "7.1"}
    # "(Hygroscopic!)" has no field: the yield sentence is kept as text too.
    assert c.unrecognised == ["Isolated Yield: 1.25 g (64%) as a pale-yellow crystalline "
                              "solid (Hygroscopic!)."]


@pytest.mark.parametrize("text,note", [
    ("Isolated yield: 43.7 mg (57% over two steps).", "over two steps"),
    ("Isolated Yield: 72.4 mg (94%) over two steps.", "over two steps"),
    ("Yield: 12.0 mg (61% from 2a).", "from 2a"),
    ("Colorless oil (57.4 mg, 74%, 2 steps).", "2 steps"),
    ("Colorless oil (57.4 mg, 74% yield).", None),
    ("Colorless oil (57.4 mg, 74%, 92% ee).", None),
])
def test_a_yield_note(text, note):
    """"over two steps" and the like go into the Yield field's note (SPEC §13u.13)."""
    c = compound(text)
    assert c.values["yield"].get("note") == note
    assert c.unrecognised == []


def test_yield_note_is_printed_in_the_parentheses(tmp_path):
    from si_assistant.compile import blocks
    p = new_project(tmp_path)
    data = p.add_section("data", "Products")
    c = p.add_compound(data.id)
    p.add_fields(c, [{"key": "yield"}, {"key": "physical_state"}])
    c.values["yield"] = {"percent": "57", "mass": "43.7", "mass_unit": "mg",
                         "note": "over two steps"}
    c.values["physical_state"] = {"text": "white solid"}
    assert "".join(r.text for r in blocks.state_and_yield(c)) == \
        "White solid (43.7 mg, 57% over two steps)."
    from si_assistant.core import fields as F
    assert p.field_filled(c, F.ALL_FIELDS["yield"])
    c.values["yield"].pop("note")                      # (no note: as before)
    assert "".join(r.text for r in blocks.state_and_yield(c)) == "White solid (43.7 mg, 57%)."
    p.close()


def test_a_sentence_starting_with_yields_is_no_yield():
    plan = I.analyse([para("**Benzyl bromide (4).** Prepared from benzyl alcohol. Yields "
                           "obtained this way were comparable (within 5%). Isolated Yield: "
                           "1.0 g (80%) as a colorless oil.")])
    assert plan.compounds[0].values["yield"]["percent"] == "80"


def test_an_ion_formula_of_a_neutral_compound_is_its_protonated_form():
    mz = calcd("C11H15N", "[M+H]+")
    c = I.ImportedCompound("4b-Ox", "4-Phenylpiperidine")
    I.parse_data(f"HRMS (ESI): m/z calculated for [C11H16N]+: {mz}, Observed: {mz}.", c)
    assert (c.values["hrms"]["adduct"], c.values["hrms"]["formula"]) == ("[M+H]+", "C11H15N")


def test_ids_with_a_suffix():
    assert I.compound_heading("N-Methylmaleimide cycloadduct (3b-DA)") == (
        "N-Methylmaleimide cycloadduct", "3b-DA")


# ---------------------------------------------------------------------- the whole SI

def three_compounds(bold_ids: bool = False) -> list[R.Para]:
    out = [para("**General Procedure B: Methylation of phenols**"),
           para("To a solution of the phenol (0.50 mmol, 1.0 equiv) in acetone (2.0 mL) were "
                "added K_{2}CO_{3} (138 mg, 1.00 mmol) and MeI (47 µL, 0.75 mmol).")]
    data = [("5a", "1-Methoxy-4-methylbenzene", "4-methylphenol", 54.1, "colorless oil", 49.5, 81),
            ("5b", "1-Bromo-4-methoxybenzene", "4-bromophenol", 86.5, "colorless oil", 79.6, 85),
            ("5c", "4-Methoxybenzonitrile", "4-hydroxybenzonitrile", 59.6, "white solid", 58.9, 88)]
    for cid, name, sm, mass, state, got, pct in data:
        sid = f"**{cid[:-1]}{chr(ord(cid[-1]) - 1)}**" if bold_ids else cid.replace("5", "4")
        out.append(para(f"**{name} ({cid}).** Prepared according to General Procedure B from "
                        f"{sm} ({sid}, {mass} mg, 0.50 mmol). The crude product was purified "
                        f"by flash column chromatography (SiO_{{2}}, 5% EtOAc/hexanes). "
                        f"{state.capitalize()} ({got} mg, {pct}%). Rf = 0.55 (10% "
                        "EtOAc/hexanes). ^{1}H NMR (400 MHz, CDCl_{3}) δ 7.10 (d, *J* = 8.4 Hz, "
                        "2H), 3.78 (s, 3H)."))
    return out


def test_a_general_procedure_and_its_compounds():
    plan = I.analyse(three_compounds())
    assert [g.name for g in plan.gps] == ["General Procedure B"]
    gp = plan.gps[0]
    assert gp.title == "Methylation of phenols"
    assert gp.text.startswith("To a solution of the phenol")
    # The sentence the three compounds share, with blanks for what differs.
    assert gp.sentence == ("Prepared according to {GP} from {starting material} "
                           "({starting material ID}, {mass} mg, {scale}).")
    assert [c.cid for c in plan.compounds] == ["5a", "5b", "5c"]
    a = plan.compounds[0]
    assert a.gp_key == gp.key
    assert a.gp_values == {"starting material": "4-methylphenol",
                           "starting material ID": "4a", "mass": "54.1"}
    assert a.values["scale"] == {"value": "0.50", "unit": "mmol"}
    # The purification sentence, worded as the program prints it, is the Purification
    # field now; nothing is left of the procedure.
    assert a.values["purification"] == {"method": "flash column chromatography",
                                        "phase": "SiO2", "eluent": "5% EtOAc/hexanes",
                                        "sentence": ""}
    assert a.procedure == ""
    assert a.values["yield"] == {"mass": "49.5", "mass_unit": "mg", "percent": "81"}
    assert plan.compounds[2].values["physical_state"] == {"text": "white solid"}
    assert a.unrecognised == [] and not plan.warnings


def test_compounds_too_different_to_share_a_sentence_keep_their_own():
    paras = [para("**General Procedure A**"), para("Text of the procedure.")]
    sentences = ["Prepared according to General Procedure A from 2-naphthol (3.2 g).",
                 "Following General Procedure A, the reaction of the iodide with sodium azide "
                 "in wet DMSO at 80 °C for two days and an aqueous work-up gave a product.",
                 "General Procedure A was followed with acetone, filtering through Celite."]
    for i, s in enumerate(sentences, 1):
        paras.append(para(f"**Compound {i}.** {s} White solid (10 mg, 50%)."))
    plan = I.analyse(paras)
    assert all(not c.gp_key for c in plan.compounds)
    assert any("too different" in w for w in plan.warnings)
    assert plan.compounds[1].procedure.startswith("Following General Procedure A")


def test_one_compound_of_a_gp():
    plan = I.analyse([para("**General Procedure C: Oxidation**"), para("Text."),
                      para("**Hexanal (8).** Prepared according to General Procedure C from "
                           "hexan-1-ol (8). Colorless oil (20 mg, 40%).")])
    assert plan.gps[0].sentence == "Prepared according to {GP} from hexan-1-ol ({product})."


def test_headings_in_other_styles():
    plan = I.analyse([
        para("**Ethyl benzoate (2)**"),                       # a heading on its own line
        para("Colorless oil (10.0 mg, 67%)."),
        para("**S3.** To a solution of the acid (1.0 g) was added …"),   # only the ID
        para("Yellow solid (5 mg, 10%)."),
        para("Benzyl alcohol (6): Prepared from benzaldehyde. White solid (3 mg, 3%)."),
    ])
    assert [(c.cid, c.name) for c in plan.compounds] == [
        ("2", "Ethyl benzoate"), ("S3", ""), ("6", "Benzyl alcohol")]
    assert plan.compounds[1].procedure == "To a solution of the acid (1.0 g) was added …"
    assert plan.compounds[2].procedure == "Prepared from benzaldehyde."


def test_tables_of_contents_and_figure_text_are_not_compounds():
    plan = I.analyse([
        para("**Table of Contents**"),
        para("Ethyl benzoate (2) . . . . . . . . . . . . . . . . S4"),
        para("**Synthesis and Characterization**"),
        para("**Ethyl benzoate (2).** Colorless oil (10.0 mg, 67%).", page=4),
        para("Pd(OAc)_{2} (5 mol %)", page=4), para("toluene, 110 °C", page=4),
        para("**Scheme S1.** Synthesis of the esters", page=4),
    ])
    assert [c.cid for c in plan.compounds] == ["2"]
    assert plan.section_name == "Synthesis and Characterization"
    assert plan.compounds[0].unrecognised == []
    assert any("Pd(OAc)" in p.text for p in plan.other)


def test_state_and_yield_written_in_the_procedure():
    plan = I.analyse([para("**Ethyl benzoate (2).** The residue was purified to give 2 as a "
                           "colorless oil (10.0 mg, 67% yield)."),
                      para("^{1}H NMR (400 MHz, CDCl_{3}) δ 8.04 (d, *J* = 7.6 Hz, 2H).")])
    c = plan.compounds[0]
    assert c.values["yield"]["percent"] == "67"
    assert c.values["physical_state"] == {"text": "colorless oil"}
    assert any("appear twice" in w for w in c.warnings)
    assert "colorless oil (10.0 mg, 67% yield)" in c.procedure


def test_a_duplicate_id_is_left_unticked():
    plan = I.analyse([para("**Ethyl benzoate (2).** Oil (1 mg, 1%)."),
                      para("**Methyl benzoate (2).** Oil (1 mg, 1%).")])
    assert [c.keep for c in plan.compounds] == [True, False]
    assert "used by another compound" in plan.compounds[1].warnings[0]


def test_sentences_are_cut_after_full_stops_only():
    assert I.sentences("Stirred for ca. 5 min (approx. 2 h). Then filtered. 2a was obtained.") \
        == ["Stirred for ca. 5 min (approx. 2 h). ", "Then filtered. ", "2a was obtained."]


# ---------------------------------------------------------------------- reading Word

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(body: str) -> bytes:
    styles = (f'<w:styles {W_NS}><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>')
    doc = f'<w:document {W_NS}><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


def r(text, bold=False, valign=""):
    rpr = ("<w:b/>" if bold else "") + (f'<w:vertAlign w:val="{valign}"/>' if valign else "")
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def test_reading_word(tmp_path):
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' + r("Synthesis of the esters") + "</w:p>"
        "<w:p>" + r("Ethyl 4-methylbenzoate (2b).", bold=True) + "<w:r><w:br/></w:r>"
        + r("Colorless oil (16.4 mg, 50%). ") + r("1", valign="superscript") + r("H NMR (400 MHz, CDCl")
        + r("3", valign="subscript") + r(") ")
        + '<w:r><w:sym w:font="Symbol" w:char="F064"/></w:r>' + r(" 7.93 (d, 2H).") + "</w:p>"
        "<w:tbl><w:tr><w:tc><w:p>" + r("Entry 1") + "</w:p></w:tc></w:tr></w:tbl>"
        '<w:p><w:del><w:r><w:delText>deleted</w:delText></w:r></w:del></w:p>')
    path = tmp_path / "si.docx"
    path.write_bytes(make_docx(body))
    res = R.read_file(path)
    assert not res.error
    texts = [(p.text, p.heading, p.table) for p in res.paras]
    assert texts == [("Synthesis of the esters", 1, False),
                     ("Ethyl 4-methylbenzoate (2b).", 0, False),     # (split at the break)
                     ("Colorless oil (16.4 mg, 50%). 1H NMR (400 MHz, CDCl3) δ 7.93 (d, 2H).", 0, False),
                     ("Entry 1", 0, True)]
    assert "^{1}H NMR (400 MHz, CDCl_{3})" in res.paras[2].markup
    plan = I.analyse(res.paras)
    assert plan.section_name == "Synthesis of the esters"
    c = plan.compounds[0]
    assert (c.cid, c.name) == ("2b", "Ethyl 4-methylbenzoate")
    assert c.values["nmr"][0]["text"] == "1H NMR (400 MHz, CDCl3) δ 7.93 (d, 2H)."


def test_pypdf_still_gives_the_fonts_widths():
    """The PDF reader takes the characters' widths (where a space falls, how wide a
    subscript is) from pypdf's Font, which is not a public part of pypdf and moved in 6.20
    (pypdf._font → pypdf.generic._font): if it moves again, this says so."""
    from si_assistant.core import ms_read
    font = ms_read._pypdf_font()
    assert callable(font.from_font_resource)
    assert "character_widths" in getattr(font, "__dataclass_fields__", {})


def test_files_that_cannot_be_read(tmp_path):
    (tmp_path / "a.doc").write_bytes(b"old word")
    assert "Save As" in R.read_file(tmp_path / "a.doc").error
    (tmp_path / "b.docx").write_bytes(b"not a zip")
    assert "could not be opened" in R.read_file(tmp_path / "b.docx").error
    (tmp_path / "c.pdf").write_bytes(b"%PDF-1.4 broken")
    assert R.read_file(tmp_path / "c.pdf").error


# ---------------------------------------------------------------------- reading a PDF

@pytest.fixture(scope="module")
def example_plan():
    read = R.read_file(resources.example_pdf())
    return read, I.analyse(read.paras)


def test_the_example_si_pdf_is_read_back(example_plan):
    read, plan = example_plan
    assert not read.error
    assert len(read.spectrum_pages) == 15                    # set aside (phase I3)
    assert not any(R._PAGE_NUMBER.match(p.text) for p in read.paras)   # no "S4" footers
    assert plan.section_name == "Synthesis and Characterization"
    assert [c.cid for c in plan.compounds] == ["2a", "2b", "2c", "2d", "2e", "2f", "3"]
    assert all(not c.unrecognised for c in plan.compounds)
    gp = plan.gps[0]
    assert (gp.name, gp.title) == ("General Procedure A",
                                   "Reduction of aryl methyl ketones with NaBH4")
    assert gp.sentence == ("Prepared according to {GP} from {starting material} "
                           "(**{starting material ID}**, {mass} mg, {scale}).")
    # Each compound as in the project the example was made from.
    shipped = Project.open(resources.example_project(), read_only=True)
    try:
        report = shipped.id_report()
        by_id = {report.display_id(c.id): c for c in shipped.all_compounds()}
        for c in plan.compounds:
            orig = by_id[c.cid]
            assert c.name == orig.name
            for key in ("yield", "physical_state", "rf"):
                if key in orig.values:
                    want = {k: v for k, v in orig.values[key].items() if v}
                    got = {k: v for k, v in c.values.get(key, {}).items() if v}
                    assert got == want, (c.cid, key)
            assert [r["text"] for r in c.values["nmr"]] == \
                [r["text"] for r in orig.values["nmr"]], c.cid
            assert c.values["hrms"]["found"] == orig.values["hrms"]["found"]
            assert c.values["hrms"]["adduct"] == orig.values["hrms"]["adduct"]
    finally:
        shipped.close()


# ---------------------------------------------------------------------- writing

def new_project(tmp_path, name="P"):
    p = Project.create(tmp_path, name)
    p.acquire_lock("Tester")
    p.enable_undo()
    return p


def test_import_into_a_new_section_is_one_undo_step(tmp_path):
    p = new_project(tmp_path)
    plan = I.analyse(three_compounds() + [para("**Phenol (9).** Oil (1 mg, 1%). LRMS m/z 94.")])
    result = A.apply_plan(p, plan, "si.docx")
    section = p.section(result.section_id)
    assert section.name == "Synthesis and Characterization"
    assert section.id_table == ["5a", "5b", "5c", "9"]
    assert len(result.created) == 4 and len(result.gps) == 1
    gp = p.general_procedures(section)[0]
    assert gp["sentence"].startswith("Prepared according to {GP} from")
    assert p.read_gp_text(gp["id"]).startswith("To a solution of the phenol")
    c = p.compound(result.created[0])
    assert c.name == "1-Methoxy-4-methylbenzene"
    assert c.values["procedure"] == {"gp": gp["id"], "values": {
        "starting material": "4-methylphenol", "starting material ID": "4a", "mass": "54.1"}}
    assert c.values["purification"]["method"] == "flash column chromatography"
    assert [f["key"] for f in c.fields][:4] == ["structure", "name", "procedure", "scale"]
    assert p.id_report().display_id(c.id) == "5a"
    phenol = p.compound(result.created[3])
    assert "LRMS m/z 94." in p.read_compound_text(phenol, "notes")
    assert p.journal.undo_label == "Import SI from si.docx"
    p.undo()
    assert result.section_id not in [s.id for s in p.data_sections()]
    p.close()


def test_import_into_an_existing_section_fills_only_empty_fields(tmp_path):
    p = new_project(tmp_path)
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["5a", "7", "8"])           # 7 and 8: IDs no page has yet
    mine = p.add_compound(data.id, name="My name")
    mine.values["yield"] = {"percent": "90", "mass": "55.0", "mass_unit": "mg"}
    p.add_field(mine, {"key": "yield"})
    p.save_compound(mine)
    plan = I.analyse(three_compounds())
    result = A.apply_plan(p, plan, "si.pdf", section_id=data.id)
    assert "Yield" in result.merged["5a"]
    assert mine.values["yield"]["percent"] == "90"           # typed: kept
    assert mine.name == "My name"
    assert mine.values["rf"]["value"] == "0.55"              # empty: filled
    shown = [p.id_report().display_id(c.id) for c in p.section(data.id).compounds]
    assert shown == ["5a", "5b", "5c"]
    assert p.section(data.id).id_table[3:] == ["7", "8"]     # the unused IDs moved down
    p.close()


def test_printed_exactly_as_imported(tmp_path):
    p = new_project(tmp_path)
    plan = I.analyse(three_compounds())
    result = A.apply_plan(p, plan, "si.pdf", mode=A.PRINT_AS_IMPORTED)
    c = p.compound(result.created[1])
    assert "si_text" in c.field_keys
    assert p.read_compound_text(c, "si_text").startswith(
        "Prepared according to General Procedure B from 4-bromophenol")
    p.close()


def test_unticked_items_are_not_written(tmp_path):
    p = new_project(tmp_path)
    plan = I.analyse(three_compounds())
    plan.gps[0].keep = False
    plan.compounds[1].keep = False
    result = A.apply_plan(p, plan, "si.pdf")
    assert len(result.created) == 2 and not result.gps
    c = p.compound(result.created[0])
    # Its general procedure was left out: the sentence stays in its own text.
    assert "gp" not in (c.values.get("procedure") or {})
    assert p.read_compound_text(c, "procedure").startswith("Prepared according to General "
                                                           "Procedure B from 4-methylphenol")
    p.close()


def test_the_imported_example_compiles_to_the_same_text(tmp_path, example_plan, qapp):
    sys.path.insert(0, str(ROOT / "tools"))
    import make_example_project as M
    _read, plan = example_plan
    short = Path(tempfile.mkdtemp(prefix="csa-imp-"))
    p = Project.create(short, "Imported")
    try:
        A.apply_plan(p, plan, "Example SI.pdf")
        result = M.compile_si(p, "ACS")
    finally:
        p.close()
    assert result.warnings == []

    def text_of(pdf):
        t = "".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
        t = " ".join(t.replace("\xad", "-").split())
        # (a font with its own subscript digits gives "SiO₂" where another gives "SiO2")
        t = t.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789" * 2))
        # (page numbers where a page breaks)
        return re.sub(r"\s*\bS\d{1,2}\s*(?=[A-Z][a-z])", " ", t)
    # (spaces left out: fonts differ in where a PDF's text has them)
    original = re.sub(r"\s", "", text_of(resources.example_pdf()))
    imported = re.sub(r"\s", "", text_of(result.pdf))
    for cid in ("2a", "2c", "2f", "3"):
        # (from the compound's heading in the text, not in the table of contents)
        pattern = re.compile(r"\(" + cid + r"\)\.((?:Prepared|Toa).{350})")
        assert pattern.search(imported).group(1) == pattern.search(original).group(1), cid


# ---------------------------------------------------------------------- the window

def test_the_review_window(qapp, example_plan):
    from si_assistant.ui import si_import_ui as U
    read, plan = example_plan
    plan = I.analyse(read.paras)          # (a fresh plan: the window changes it)
    d = U.ImportDialog(plan, read, None)
    assert not d.to_open.isEnabled() and d.to_new.isChecked()
    assert d.mode() == A.PRINT_FROM_FIELDS and d.section_id() is None
    # the Data sections' names are on the Sections page (SPEC §13u.16)
    assert d.new_section_name() == ""
    assert [s.name for s in plan.sections if s.kind == "data"] == ["Compounds"]
    tops = [d.tree.topLevelItem(i).text(0) for i in range(d.tree.topLevelItemCount())]
    assert tops == ["General procedures", "Compounds", "Sections", "Cover page",
                    "References (7)"]
    compounds = d.tree.topLevelItem(1)
    assert compounds.childCount() == 7
    from PySide6.QtCore import Qt
    compounds.child(1).setCheckState(0, Qt.CheckState.Unchecked)
    assert plan.compounds[1].keep is False
    d.as_imported.setChecked(True)
    assert d.mode() == A.PRINT_AS_IMPORTED
    page = d.pages.widget(d._page_of[id(compounds.child(0))])
    page.name.setText("1-Phenylethanol")
    assert plan.compounds[0].name == "1-Phenylethanol"
    d.deleteLater()


def test_file_import_si_into_the_open_project(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDialog, QMessageBox
    from si_assistant.ui import main_window as mw
    from si_assistant.ui import si_import_ui as U
    p = new_project(tmp_path / "p")
    folder = p.folder
    p.close()
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(str(tmp_path / "prefs.ini"),
                                                              QSettings.Format.IniFormat))
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(resources.example_pdf()), "")))
    monkeypatch.setattr(U.ImportDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    said = []
    monkeypatch.setattr(mw.QMessageBox, "information",
                        staticmethod(lambda parent, title, text: said.append((title, text))))
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    try:
        assert win.open_project(folder)
        win.act_import_si.trigger()
        assert said and said[0][0] == "SI imported"
        assert "7 compound page(s) made" in said[0][1]
        sections = win.project.data_sections()
        assert len(sections) == 1 and len(sections[0].compounds) == 7
        win.project.undo()
        assert not win.project.data_sections()
    finally:
        win.close_project()
        win.close()
