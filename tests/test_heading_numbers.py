"""Heading numbers (core/headings.py): the style's own, an outline (1, A, a, i), 1.1.1 or
none, in any journal style."""

import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.text import paragraphs_to_typst
from si_assistant.core import headings
from si_assistant.core.markup import parse
from tests.test_compile_pdf import compile_sample, sample  # noqa: F401  (fixture)


def test_choice_is_stored_only_when_not_the_style():
    s = {}
    assert headings.heading_numbers(s) == "style" and headings.typst_set_rule(s) == ""
    headings.store_heading_numbers(s, "outline")
    assert s == {"heading_numbers": "outline"}
    assert headings.typst_set_rule(s) == "#set heading(numbering: si-outline-numbering)"
    assert headings.numbers_text_headings(s)
    headings.store_heading_numbers(s, "decimal")
    assert headings.typst_set_rule(s) == '#set heading(numbering: "1.1.")'
    headings.store_heading_numbers(s, "none")
    assert headings.typst_set_rule(s) == "#set heading(numbering: none)"
    assert not headings.numbers_text_headings(s)
    headings.store_heading_numbers(s, "style")
    assert s == {}
    assert headings.heading_numbers({"heading_numbers": "roman?"}) == "style"   # hand-edited


def test_text_headings_are_numbered_only_when_asked():
    paragraphs = parse("# Abbreviations\nDCM = dichloromethane.")
    assert "numbering: none" in paragraphs_to_typst(paragraphs, 1)
    assert "numbering" not in paragraphs_to_typst(paragraphs, 1, numbered=True)


def pages_text(p) -> list[str]:
    return [pg.extract_text() or "" for pg in PdfReader(str(compile_sample(p).pdf)).pages]


@pytest.mark.parametrize("style", ["ACS", "RSC", "Wiley"])
def test_outline_numbers_sections_text_headings_and_general_information(sample, style):
    p, _parts = sample
    p.settings["journal_style"] = style
    p.settings["general_information"] = {"heading": "Materials and Methods"}
    p.write_front_text("general", "# Abbreviations\nDCM = dichloromethane.\n"
                                  "# General\nAll reactions were run under argon.")
    headings.store_heading_numbers(p.settings, "outline")
    text = "\n".join(pages_text(p))
    lines = [line.strip() for line in text.split("\n")]
    assert "1. Materials and Methods" in lines
    assert "A. Abbreviations" in lines and "B. General" in lines
    assert any(re.fullmatch(r"2\. \S.*", line) for line in lines)   # the first section
    # (the table of contents lists them with the same numbers)
    assert re.search(r"1\. Materials and Methods \.", text)
    # Compound titles are never numbered.
    assert not re.search(r"^[0-9A-Za-z]+\. .*\(\d+[a-z]?\)\.", text, re.M)


def test_the_style_choice_leaves_acs_unnumbered(sample):
    p, _parts = sample
    p.settings["general_information"] = {"heading": "Materials and Methods"}
    p.write_front_text("general", "# Abbreviations\nDCM = dichloromethane.")
    lines = [line.strip() for line in "\n".join(pages_text(p)).split("\n")]
    assert "Materials and Methods" in lines and "Abbreviations" in lines
    assert not any(line.startswith(("1. Materials", "A. Abbreviations")) for line in lines)
    headings.store_heading_numbers(p.settings, "decimal")
    lines = [line.strip() for line in "\n".join(pages_text(p)).split("\n")]
    assert "1. Materials and Methods" in lines and "1.1. Abbreviations" in lines


def test_project_settings_box_saves_the_choice(qapp, tmp_path):
    from si_assistant.core.project import Project
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.heading_numbers.currentData() == "style"
    dlg.heading_numbers.setCurrentIndex(dlg.heading_numbers.findData("outline"))
    dlg.apply()
    assert Project.open(p.folder).settings["heading_numbers"] == "outline"
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.heading_numbers.currentData() == "outline"
    dlg.heading_numbers.setCurrentIndex(dlg.heading_numbers.findData("style"))
    dlg.apply()
    assert "heading_numbers" not in Project.open(p.folder).settings
