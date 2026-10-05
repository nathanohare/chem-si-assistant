"""Structure drawing settings (SPEC §13k.7) and header/footer settings (SPEC §13k.2a)."""

import re
from datetime import date

import pytest

from si_assistant.core import chem
from si_assistant.core import page

S = chem.DrawingSettings
SAMPLE = "C[C@H](NC(C)=O)c1ccc(F)cc1"   # stereocentre, F, N–H, methyls


def svg_size(svg: str) -> tuple[float, float]:
    m = re.search(r"width='([\d.]+)px' height='([\d.]+)px'", svg)
    return float(m.group(1)), float(m.group(2))


# ---------------------------------------------------------------------- drawing settings

def test_default_drawing_is_acs_1996():
    mol = chem.parse_structure(SAMPLE)
    svg = chem.structure_svg(mol)
    assert svg == chem.structure_svg(mol, settings=S.from_settings({}))
    assert "stroke-width:0.6px" in svg                 # ACS line width 0.6 pt
    assert "#FF0000" not in svg and "#0000FF" not in svg  # black atoms
    assert "CIP_Code" not in svg
    d = S()
    assert d.bond_pt == pytest.approx(14.4)            # 0.508 cm


def test_bond_length_scales_the_drawing():
    mol = chem.parse_structure(SAMPLE)
    w1, h1 = svg_size(chem.structure_svg(mol))
    w2, h2 = svg_size(chem.structure_svg(mol, settings=S(bond_length_cm=1.016)))
    assert w2 / w1 == pytest.approx(2, rel=0.1) and h2 / h1 == pytest.approx(2, rel=0.15)


def test_drawing_options_change_the_svg():
    mol = chem.parse_structure(SAMPLE)
    plain = chem.structure_svg(mol)
    assert "stroke-width:1.2px" in chem.structure_svg(mol, settings=S(line_width_pt=1.2))
    coloured = chem.structure_svg(mol, settings=S(colour_atoms=True))
    assert "#FF0000" in coloured and "#0000FF" in coloured   # O red, N blue
    assert "CIP_Code" in chem.structure_svg(mol, settings=S(stereo_labels=True))
    # Atom 0 is a methyl: unlabelled by default, "CH3" (several glyph paths) when chosen.
    assert plain.count("class='atom-0'") == 0
    assert chem.structure_svg(mol, settings=S(label_methyls=True)).count("class='atom-0'") >= 2
    # Atom 2 is the N–H: without explicit H only the "N" glyph is drawn.
    assert plain.count("class='atom-2'") > chem.structure_svg(
        mol, settings=S(heteroatom_h=False)).count("class='atom-2'")
    assert chem.structure_svg(mol, settings=S(font="Telex (built in)")) != plain
    assert chem.structure_svg(mol, settings=S(font_size_pt=14)) != plain


def test_drawing_settings_from_project_settings():
    d = S.from_settings({"structure_drawing": {"bond_length_cm": 0.6, "colour_atoms": True,
                                               "line_width_pt": "nonsense", "font_size_pt": -3,
                                               "unknown": 1}})
    assert d.bond_length_cm == 0.6 and d.colour_atoms
    assert d.line_width_pt == 0.6 and d.font_size_pt == 10.0   # bad values: defaults
    assert S.from_settings({"structure_drawing": d.to_dict()}) == d


def test_missing_font_falls_back_to_built_in():
    assert chem.font_file("No Such Font") == "BuiltinRobotoRegular"
    assert chem.font_file("Telex (built in)") == "BuiltinTelexRegular"
    assert chem.available_fonts()[:2] == ["Roboto (built in)", "Telex (built in)"]


def test_screen_drawing_uses_settings():
    mol = chem.parse_structure(SAMPLE)
    png = chem.structure_png(mol, 300, 220)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert chem.structure_png(mol, 300, 220, S(colour_atoms=True)) != png


# ---------------------------------------------------------------------- page settings

def test_page_numbers():
    assert page.format_number(4) == "S4"
    assert page.format_number(4, "S", "i") == "Siv"
    assert page.format_number(14, "", "I") == "XIV"
    assert page.roman(1994) == "MCMXCIV"


def test_fill_ins_are_split_and_unknown_words_kept():
    assert page.split_fill_ins("Page {page} of {pages}") == [
        ("text", "Page "), ("page", ""), ("text", " of "), ("pages", "")]
    assert page.split_fill_ins("{nope}") == [("text", "{nope}")]


def test_page_defaults_follow_the_journal_style():
    acs = page.page_settings({"journal_style": "ACS"})
    assert acs["footer"] == {"left": "", "center": "{page}", "right": ""}
    assert acs["header"] == {"left": "", "center": "", "right": ""}
    assert acs["footer_is_style"] and acs["number_prefix"] == "S"
    assert not acs["number_on_cover"] and acs["number_size"] == 0
    wiley = page.page_settings({"journal_style": "Wiley"})
    assert wiley["footer"]["right"] == "{page}"


def test_style_footer_is_not_stored():
    s = {"journal_style": "Wiley"}
    v = page.page_settings(s)
    page.store_page_settings(s, v)
    assert "footer" not in s["page"]          # keeps following the style
    v["footer"] = {"left": "", "center": "Page {page} of {pages}", "right": ""}
    page.store_page_settings(s, v)
    assert s["page"]["footer"]["center"] == "Page {page} of {pages}"
    assert not page.page_settings(s)["footer_is_style"]


def test_example_line():
    s = {"title": "My SI", "authors": [{"name": "A. Chemist"}, {"name": "B. Chemist"}],
         "page": {"header": {"left": "{title}", "center": "", "right": "{authors}"},
                  "footer": {"left": "", "center": "Page {page} of {pages}", "right": ""},
                  "number_style": "I"}}
    text = page.example(s)
    assert "My SI | – | A. Chemist, B. Chemist" in text
    assert "Page SII of SXX" in text
    assert page.fill_in("{date}", s, 1, 1, today=date(2026, 10, 3)) == "3 October 2026"
