import pytest

from si_assistant.compile.blocks import (compound_block, generated_paragraphs, hrms_sentence,
                                         nmr_sentence)
from si_assistant.core.markup import Paragraph, serialize


def show(paragraphs):
    return serialize(paragraphs)


@pytest.fixture
def compound(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["3g"])
    c = project.add_compound(s.id, name="Ethyl 2-methylfuran-3-carboxylate")
    project.set_structure(c, "CCOC(=O)c1ccoc1C")
    project.write_compound_text(c, "procedure", "Prepared from **1g** (0.20 mmol) in CH2Cl2.")
    c.values["yield"] = {"percent": "92", "mass": "28.4", "mass_unit": "mg"}
    c.values["physical_state"] = {"text": "colorless oil"}
    c.values["rf"] = {"value": "0.35", "eluent": "30% EtOAc/hexanes", "visualization": "UV"}
    c.values["nmr"][0]["text"] = "1H NMR (400 MHz, CDCl3) δ 7.25 (d, J = 2.0 Hz, 1H), 4.28 (q, 2H)"
    c.values["nmr"][1]["text"] = "δ 164.2, 159.0"
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "155.0701",
                        "species": "", "formula": ""}
    project.save_compound(c)
    return c


def test_generated_block_reads_like_an_acs_si(project, compound):
    block = compound_block(project, compound, "3g")
    assert show([Paragraph(block.title)]) == "**Ethyl 2-methylfuran-3-carboxylate (3g).**"
    assert show(block.paragraphs) == (
        "Prepared from **1g** (0.20 mmol) in CH_{2}Cl_{2}.\n"
        "Colorless oil (28.4 mg, 92%). *R_{f}* = 0.35 (30% EtOAc/hexanes; UV). "
        "**^{1}H NMR** (400 MHz, CDCl_{3}) δ 7.25 (d, *J* = 2.0 Hz, 1H), 4.28 (q, 2H). "
        "**^{13}C NMR** δ 164.2, 159.0. "
        "**HRMS** (ESI) *m/z*: [M+H]^{+} calcd for C_{8}H_{11}O_{3} 155.0703; found 155.0701.")
    assert not block.custom_text


def test_nmr_label_goes_into_the_conditions():
    report = {"nucleus": "1H", "label": "major rotamer", "text": "1H NMR (400 MHz, CDCl3) δ 7.2"}
    assert show([Paragraph(nmr_sentence(report, True))]) == \
        "**^{1}H NMR** (400 MHz, CDCl_{3}, major rotamer) δ 7.2."
    report = {"nucleus": "19F", "label": "minor", "text": "δ −63.1"}
    assert show([Paragraph(nmr_sentence(report, True))]) == "**^{19}F NMR** (minor) δ −63.1."


def test_hrms_uses_matched_isotope_peak_and_manual_formula(project, compound):
    project.set_structure(compound, "CCOC(=O)c1ccc(Br)cc1")
    compound.values["hrms"]["found"] = "230.9841"  # the 81Br peak
    assert "calcd for C_{9}H_{10}^{81}BrO_{2} 230.9838; found 230.9841" in \
        show([Paragraph(hrms_sentence(project, compound))])
    compound.values["hrms"].update({"formula": "C12H13NO", "found": "188.1073"})
    assert "calcd for C_{12}H_{14}NO 188.1070; found 188.1073" in \
        show([Paragraph(hrms_sentence(project, compound))])


def test_removed_fields_are_not_printed(project, compound):
    project.remove_field(compound, "rf")
    assert "R" not in show(generated_paragraphs(project, compound)).split("Colorless")[1][:30]


def test_custom_si_text_replaces_generated_text(project, compound):
    project.add_field(compound, {"key": "si_text"})
    project.write_compound_text(compound, "si_text", "My own text with CDCl3 kept as typed.")
    block = compound_block(project, compound, "3g")
    assert block.custom_text
    assert show(block.paragraphs) == "My own text with CDCl3 kept as typed."


def test_inline_figures(project, compound, tmp_path):
    img = tmp_path / "hplc.png"
    img.write_bytes(b"\x89PNG")
    project.attach_compound_file(compound, "files", img, {"caption": "HPLC trace",
                                                         "placement": "inline"})
    other = tmp_path / "photo.png"
    other.write_bytes(b"\x89PNG")
    project.attach_compound_file(compound, "files", other, {"placement": "exclude"})
    figs = compound_block(project, compound, "3g").figures
    assert [(f.path.name, f.caption) for f in figs] == [("hplc.png", "HPLC trace")]
