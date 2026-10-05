"""Compiling the front matter, nested sections and the export choice (SPEC §13k.4, 5, 10)."""

import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.core import front
from tests.sample_project import FACTS, build_sample


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def typst(p, **kw) -> tuple[str, list[str]]:
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    result = build(p, options, image_info, convert_to_png)
    return result.typ_path.read_text(encoding="utf-8"), result.warnings


def pdf_pages(p, **kw) -> tuple[list[str], list[str]]:
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    result = compile_pdf(p, options, image_info, convert_to_png)
    return [pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages], result.warnings


@pytest.fixture
def nested(qapp, project):
    """Methods ▸ Solvents ▸ Drying, plus a Data section inside Methods with one GP."""
    p = project
    p.settings.update({"title": "Nested SI", "authors": [{"name": "A. Chemist",
                                                          "affiliations": [], "email": ""}]})
    p.save_settings()
    methods = p.add_section("text", "Methods")
    p.write_section_text(methods, "Intro.\n# Typed heading\nText.")
    solvents = p.add_section("text", "Solvents", parent_id=methods.id)
    drying = p.add_section("text", "Drying", parent_id=solvents.id)
    p.write_section_text(drying, "Over sieves.")
    products = p.add_section("data", "Products", parent_id=methods.id)
    p.set_id_table(products.id, ["1"])
    gp = p.add_general_procedure(products.id)
    p.write_gp_text(gp["id"], "Stir.")
    c = p.add_compound(products.id, name="Ester")
    p.set_structure(c, "CCOC(C)=O")
    return p, dict(methods=methods, solvents=solvents, drying=drying, products=products, c=c)


# ---------------------------------------------------------------------- settings helpers

def test_toc_settings_follow_the_old_compound_option():
    assert front.toc_settings({})["depth"] == "procedures"
    assert front.toc_settings({"list_compounds_in_toc": True})["depth"] == "compounds"
    settings = {"list_compounds_in_toc": True}
    front.store_toc(settings, {**front.DEFAULT_TOC, "depth": "sections"})
    assert "list_compounds_in_toc" not in settings
    assert front.toc_settings(settings)["depth"] == "sections"
    assert front.toc_settings({"toc": {"depth": "nonsense", "font_size": "x"}})["depth"] == \
        "procedures"


def test_cover_heading_follows_the_journal_style_unless_changed():
    s = {"journal_style": "RSC"}
    assert front.cover_heading(s) == "Electronic Supplementary Information"
    front.store_cover_heading(s, "Electronic Supplementary Information")
    assert "cover" not in s                                # the style's own: not stored
    front.store_cover_heading(s, "Supplementary Material")
    s["journal_style"] = "ACS"
    assert front.cover_heading(s) == "Supplementary Material"
    front.store_cover_heading(s, "")
    assert front.cover_heading(s) == "Supporting Information"


def test_general_heading_can_be_changed_or_removed():
    s = {}
    assert front.general_heading(s) == "General Information"
    front.store_general_heading(s, "")
    assert front.general_heading(s) == ""
    front.store_general_heading(s, "General Information")
    assert s == {}
    assert front.general_numbered(s) is False              # unnumbered by default
    front.store_general_heading(s, "General Information", numbered=True)
    assert s == {"general_information": {"numbered": True}}
    front.store_general_heading(s, "General Information")     # (None: left as it is)
    assert front.general_numbered(s) is True
    front.store_general_heading(s, "General Information", numbered=False)
    assert s == {}


# ---------------------------------------------------------------------- Typst source

def test_heading_levels_follow_the_tree(nested):
    p, parts = nested
    src, warnings = typst(p)
    assert warnings == []
    assert '#heading(level: 1, outlined: true)[#"Methods"]' in src
    assert '#heading(level: 2, outlined: true, numbering: none)[#"Typed heading"]' in src
    assert '#heading(level: 2, outlined: true)[#"Solvents"]' in src
    assert '#heading(level: 3, outlined: true)[#"Drying"]' in src
    assert '#heading(level: 2, outlined: true)[#"Products"]' in src
    # The Data section's GP and compound titles sit one level below it, unnumbered.
    assert '#heading(level: 3, outlined: true, numbering: none)[#"General Procedure A"]' in src
    assert '#si-compound-title(3, false)[' in src
    assert src.index('"Drying"') < src.index('"Products"')


def test_toc_depth_decides_what_is_listed(nested):
    p, _parts = nested
    front.store_toc(p.settings, {**front.DEFAULT_TOC, "depth": "sections"})
    src, _ = typst(p)
    assert '#heading(level: 2, outlined: false)[#"Solvents"]' in src
    assert '#heading(level: 2, outlined: false, numbering: none)[#"Typed heading"]' in src
    assert '"General Procedure A"' in src and 'numbering: none)[#"General Procedure A"]' in src
    assert '#heading(level: 3, outlined: false, numbering: none)[#"General Procedure A"]' in src
    front.store_toc(p.settings, {**front.DEFAULT_TOC, "depth": "compounds", "dots": False,
                                 "font_size": 9, "schemes": True, "title": "Contents"})
    src, warnings = typst(p)
    assert '#si-compound-title(3, true)[' in src
    assert "#set outline.entry(fill: none)" in src
    assert "#show outline.entry: set text(size: 9pt)" in src
    assert '#outline(title: [#"Contents"], depth: none, indent: auto)' in src
    assert 'target: figure.where(kind: "scheme")' not in src       # no schemes to list
    assert any("no list of schemes" in w for w in warnings)


def test_exclusions_leave_out_sub_trees_and_their_spectra(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    src, _ = typst(p)
    assert "si-spectrum(" in src
    # Leave out the Data section: its compounds, and their spectra, go too.
    front.store_excluded(p.settings, [parts["data"].id, "toc"])
    src, warnings = typst(p)
    assert FACTS["data_section"] not in src and "si-spectrum(" not in src
    assert "#outline(" not in src and "#si-cover(" in src
    assert "General Information" in src
    # A preview ignores the choice.
    src, _ = typst(p, only=("section", parts["data"].id))
    assert FACTS["data_section"] in src and "si-spectrum(" in src
    # Leaving out the cover numbers the first page.
    front.store_excluded(p.settings, ["cover"])
    src, _ = typst(p)
    assert "number-on-cover: true" in src and "#si-cover(" not in src
    p.close()


def test_excluding_a_parent_excludes_its_sub_sections(nested):
    p, parts = nested
    front.store_excluded(p.settings, [parts["solvents"].id, "spectra"])
    src, _ = typst(p)
    assert '"Methods"' in src and '"Products"' in src
    assert '"Solvents"' not in src and '"Drying"' not in src


def test_everything_excluded_gives_a_warning(nested):
    p, parts = nested
    front.store_excluded(p.settings, ["front", parts["methods"].id, "spectra"])
    src, warnings = typst(p)
    assert any("Nothing was chosen" in w for w in warnings)


# ---------------------------------------------------------------------- the PDF

def test_front_matter_page_order(nested):
    """Cover alone on S1; the TOC starts S2; General Information starts the next page."""
    p, _parts = nested
    p.write_front_text("general", "All reactions under argon.\n# Instruments\nNMR at 400 MHz.")
    p.write_front_text("cover", "Contents of this SI: *everything*.")
    pages, warnings = pdf_pages(p)
    assert warnings == []
    assert "NestedSI" in squash(pages[0]) and "Contentsofthis" in squash(pages[0])
    assert "TableofContents" not in squash(pages[0])
    assert squash(pages[1]).startswith("TableofContents")
    toc = squash(pages[1])
    assert toc.index("GeneralInformation") < toc.index("Methods") < toc.index("Drying")
    assert squash(pages[2]).startswith("GeneralInformationAllreactionsunderargon")
    assert "Methods" in squash(pages[2])     # sections follow General Information directly


def test_numbered_styles_number_sections_only(nested):
    p, _parts = nested
    p.settings["journal_style"] = "Wiley"
    pages, warnings = pdf_pages(p)
    assert warnings == []
    body = squash("".join(pages[2:]))
    assert "1.Methods" in body and "1.1.Solvents" in body and "1.1.1.Drying" in body
    assert "1.2.Products" in body
    assert "GeneralProcedureA" in body and "1.2.1.GeneralProcedureA" not in body
    assert "Typedheading" in body and "1.1.Typedheading" not in body   # typed: unnumbered
    assert "1.2.Products" in body          # …and it does not use up a number
    p.settings["journal_style"] = "RSC"
    pages, _ = pdf_pages(p)
    body = squash("".join(pages[2:]))
    assert "1.1.1Drying" in body and "ElectronicSupplementaryInformation" in squash(pages[0])


def test_general_information_is_unnumbered_unless_chosen(nested):
    """Front matter is not a section: in Wiley the first real section is "1." unless
    the General Information heading is set to be numbered."""
    p, _parts = nested
    p.settings["journal_style"] = "Wiley"
    p.write_front_text("general", "Argon.")
    src, _ = typst(p)
    assert '#heading(level: 1, outlined: true, numbering: none)[#"General Information"]' in src
    pages, warnings = pdf_pages(p)
    assert warnings == []
    body = squash("".join(pages[2:]))
    assert body.startswith("GeneralInformationArgon") and "1.Methods" in body
    front.store_general_heading(p.settings, "General Information", numbered=True)
    pages, _ = pdf_pages(p)
    body = squash("".join(pages[2:]))
    assert "1.GeneralInformation" in body and "2.Methods" in body and "2.1.Solvents" in body


def test_cover_heading_can_be_changed(nested):
    p, _parts = nested
    front.store_cover_heading(p.settings, "Supplementary Data")
    pages, _ = pdf_pages(p)
    assert squash(pages[0]).startswith("SupplementaryData")


def test_preview_of_front_pages(nested):
    p, _parts = nested
    p.write_front_text("general", "Argon.")
    src, _ = typst(p, only=("front", "general"))
    assert '"General Information"' in src and "#si-cover(" not in src
    src, _ = typst(p, only=("front", "cover"))
    assert "#si-cover(" in src and "#outline(" not in src
