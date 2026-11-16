"""Page flow (core/page_flow.py, SPEC §13k.2b): headings kept with what follows, short
bold/italic lines kept like headings, widows/orphans, compounds and sections kept together
or started on a new page, and the space between a compound's procedure and its data."""

import io
import re
import shutil

import pytest
import typst
from pypdf import PdfReader

from si_assistant.compile.blocks import compound_block, generated_paragraphs
from si_assistant.compile.document import STYLES_DIR, Builder, CompileOptions, PAGE_BREAK
from si_assistant.compile.text import is_emphasis_line, paragraphs_to_typst, typst_string
from si_assistant.core import page_flow
from si_assistant.core.markup import Paragraph, Run, serialize


# ---------------------------------------------------------------------- settings

def test_defaults_when_nothing_is_stored():
    f = page_flow.flow_settings({})
    assert (f.heading_lines, f.widows, f.emphasis_headings, f.keep_compounds,
            f.data_spacing) == (2, "normal", True, False, "paragraph")
    assert f.emphasis_lines == 2


def test_hand_edited_values_are_made_safe():
    f = page_flow.flow_settings({"page_flow": {"heading_lines": "99", "widows": "huge",
                                               "data_spacing": 3, "keep_compounds": 1}})
    assert f.heading_lines == page_flow.MAX_HEADING_LINES
    assert f.widows == "normal" and f.data_spacing == "paragraph" and f.keep_compounds
    assert page_flow.flow_settings({"page_flow": {"heading_lines": "x"}}).heading_lines == 2
    assert page_flow.flow_settings({"page_flow": {"heading_lines": -3}}).heading_lines == 0
    assert page_flow.flow_settings({"page_flow": "nonsense"}) == page_flow.Flow()


def test_settings_round_trip():
    s = {}
    flow = page_flow.Flow(heading_lines=3, widows="strong", emphasis_headings=False,
                          keep_compounds=True, data_spacing="line")
    page_flow.store_flow_settings(s, flow)
    assert page_flow.flow_settings(s) == flow
    assert flow.emphasis_lines == 0   # (bold/italic lines are not kept when switched off)


def test_item_overrides():
    data = {}
    assert page_flow.item_flow(data) == {"new_page": False, "keep": "default"}
    assert page_flow.keeps_together(data, True) and not page_flow.keeps_together(data, False)
    page_flow.set_item_flow(data, True, "split")
    assert page_flow.item_flow(data) == {"new_page": True, "keep": "split"}
    assert not page_flow.keeps_together(data, True)
    assert page_flow.describe_item(data) == "new page, may split"
    page_flow.set_item_flow(data, False, "together")
    assert page_flow.keeps_together(data, False)
    assert page_flow.describe_item(data) == "kept together"
    page_flow.set_item_flow(data, False, "default")
    assert "page_flow" not in data   # (the file stays as it was)
    assert page_flow.item_flow({"page_flow": {"keep": "??"}})["keep"] == "default"


def test_typst_definitions():
    code = page_flow.typst_definitions(page_flow.Flow())
    assert "#let si-need(" in code and "#let si-together(" in code
    assert "#show heading: it => { si-need(2, it); it }" in code
    assert "costs: (widow: 100%, orphan: 100%)" in code
    off = page_flow.typst_definitions(page_flow.Flow(heading_lines=0, widows="off"))
    assert "#show heading" not in off and "costs: (widow: 0%, orphan: 0%)" in off
    assert "600%" in page_flow.typst_definitions(page_flow.Flow(widows="strong"))


# ---------------------------------------------------------------------- bold/italic lines

def test_emphasis_lines():
    italic = [Run("Solvent Effect on Reaction Yield and Rearrangement", italic=True)]
    assert is_emphasis_line(italic)
    assert is_emphasis_line([Run("Part A", bold=True), Run(" ", bold=False),
                             Run("(new)", bold=True)])   # (spaces don't count)
    assert not is_emphasis_line([Run("Plain text line")])
    assert not is_emphasis_line([Run("Half "), Run("italic", italic=True)])
    assert not is_emphasis_line([Run("Prepared based on a reported procedure:", italic=True)])
    assert not is_emphasis_line([Run("A whole sentence in italics.", italic=True)])
    assert not is_emphasis_line([Run("x" * 120, italic=True)])   # (a paragraph, not a line)


def test_emphasis_line_is_kept_with_the_next_paragraph():
    paras = [Paragraph([Run("Solvent Effect", italic=True)]), Paragraph([Run("Solvent had…")])]
    code = paragraphs_to_typst(paras, keep_lines=2)
    assert code.startswith("#si-need(2, [#emph[")
    assert "si-need" not in paragraphs_to_typst(paras)   # (off: as before)
    # The last paragraph of a text has nothing after it to be kept with.
    assert "si-need" not in paragraphs_to_typst(paras[:1], keep_lines=2)


def test_newline_in_a_typst_string():
    assert typst_string("a\nb") == '"a\\nb"'


# ---------------------------------------------------------------------- data spacing

@pytest.fixture
def compound(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["3g"])
    c = project.add_compound(s.id, name="Ethyl 2-methylfuran-3-carboxylate")
    project.set_structure(c, "CCOC(=O)c1ccoc1C")
    project.write_compound_text(c, "procedure", "Prepared from **1g** (0.20 mmol).")
    c.values["yield"] = {"percent": "92", "mass": "28.4", "mass_unit": "mg"}
    c.values["physical_state"] = {"text": "colorless oil"}
    project.save_compound(c)
    return c


def test_data_spacing(project, compound):
    def text(spacing):
        return serialize(generated_paragraphs(project, compound, data_spacing=spacing))
    assert text("paragraph").startswith("Prepared from **1g** (0.20 mmol).\nColorless oil ")
    assert text("run_on").startswith("Prepared from **1g** (0.20 mmol). Colorless oil ")
    paras = generated_paragraphs(project, compound, data_spacing="line")
    assert len(paras) == 1 and Run("\n") in paras[0].runs
    # compound_block follows the project setting.
    page_flow.store_flow_settings(project.settings, page_flow.Flow(data_spacing="run_on"))
    assert len(compound_block(project, compound, "3g").paragraphs) == 1


def test_data_alone_is_unaffected(project, compound):
    project.write_compound_text(compound, "procedure", "")
    paras = generated_paragraphs(project, compound, data_spacing="line")
    assert len(paras) == 1 and serialize(paras).startswith("Colorless oil (28.4 mg, 92%).")
    assert Run("\n") not in paras[0].runs


# ---------------------------------------------------------------------- the document

def builder(p):
    b = Builder(p, CompileOptions.from_settings(p.settings), lambda path: (4, 3, 300))
    b.assets.mkdir(parents=True, exist_ok=True)
    return b


def test_compound_and_section_overrides(project, compound):
    section = project.section(compound.section_id)
    plain = builder(project).compound(compound)
    assert "si-together" not in plain and PAGE_BREAK not in plain
    page_flow.set_item_flow(compound.data, True, "together")
    code = builder(project).compound(compound)
    assert code.startswith(PAGE_BREAK + "\n\n#si-together[")
    # Keep every compound together, but let this one split.
    page_flow.store_flow_settings(project.settings, page_flow.Flow(keep_compounds=True))
    page_flow.set_item_flow(compound.data, False, "default")
    assert "si-together" in builder(project).compound(compound)
    page_flow.set_item_flow(compound.data, False, "split")
    assert "si-together" not in builder(project).compound(compound)
    # A section on a new page, kept together.
    page_flow.set_item_flow(section.data, True, "together")
    code = builder(project).section_block(section, 1, [])
    assert code.startswith(PAGE_BREAK + "\n\n#si-together[")


def test_document_has_the_definitions(project, compound):
    code = builder(project).document()
    assert "#let si-need(" in code and "#show heading: it => { si-need(2" in code


# ---------------------------------------------------------------------- typeset

def typeset(tmp_path, body: str, flow: page_flow.Flow) -> list[str]:
    shutil.copy(STYLES_DIR / "acs.typ", tmp_path / "style.typ")
    src = tmp_path / "t.typ"
    src.write_text('#import "style.typ": *\n#show: si-doc.with()\n'
                   + page_flow.typst_definitions(flow) + "\n" + body, encoding="utf-8")
    pdf = typst.compile(str(src))
    return [pg.extract_text() or "" for pg in PdfReader(io.BytesIO(pdf)).pages]


def page_of(pages, text):
    return [i for i, t in enumerate(pages) if text in re.sub(r"\s+", " ", t)][-1]


@pytest.mark.parametrize("words", range(560, 660, 12))
def test_heading_is_not_left_at_the_page_bottom(tmp_path, words):
    """Section C of a real SI: its heading and a one-line lead-in were left at the bottom
    of a page, the procedure on the next."""
    body = (f"#lorem({words})\n\n== Preparation of Buffered Silica Gel\n"
            "Prepared based on a reported procedure:\n\nZzz " + "word " * 120)
    pages = typeset(tmp_path, body, page_flow.Flow())
    assert page_of(pages, "Preparation of Buffered") == page_of(pages, "Prepared based") \
        == page_of(pages, "Zzz")


@pytest.mark.parametrize("words", range(560, 660, 12))
def test_italic_line_is_not_left_at_the_page_bottom(tmp_path, words):
    para = paragraphs_to_typst([Paragraph([Run("Solvent Effect on Yield", italic=True)]),
                                Paragraph([Run("Zzz " + "word " * 80)])], keep_lines=2)
    pages = typeset(tmp_path, f"#lorem({words})\n\n" + para, page_flow.Flow())
    assert page_of(pages, "Solvent Effect") == page_of(pages, "Zzz")


def test_keep_together_moves_a_short_block_and_splits_a_long_one(tmp_path):
    body = ("#lorem(560)\n\n#si-together[== Short\n" + "short " * 150 + "]\n\n"
            "#si-together[== Long\n" + "long " * 1500 + "]")
    pages = typeset(tmp_path, body, page_flow.Flow())
    short = [i for i, t in enumerate(pages) if "short" in t]
    assert len(short) == 1   # (moved whole)
    assert len([t for t in pages if "long" in t]) > 1   # (longer than a page: split)


def test_sample_si_compiles_with_every_page_flow_choice(qapp, tmp_path):
    """Everything switched on (and each section and compound with its own choices) still
    gives a PDF with every compound and its links."""
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    from tests.sample_project import build_sample
    p, parts = build_sample(tmp_path)
    try:
        page_flow.store_flow_settings(p.settings, page_flow.Flow(
            heading_lines=3, widows="strong", keep_compounds=True, data_spacing="line"))
        for s in p.top_sections:
            page_flow.set_item_flow(s.data, True, "together")
        page_flow.set_item_flow(parts["c2"].data, True, "split")
        result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                             convert_to_png)
        assert result.pdf.exists()
        text = re.sub(r"\s+", "", "".join(pg.extract_text() or ""
                                          for pg in PdfReader(str(result.pdf)).pages))
        assert re.sub(r"\s+", "", parts["c2"].name) in text
    finally:
        p.close()


def test_no_page_break_inside_a_kept_together_section(project, compound):
    section = project.section(compound.section_id)
    page_flow.set_item_flow(section.data, False, "together")
    page_flow.set_item_flow(compound.data, True, "default")
    code = builder(project).section_block(section, 1, [])
    assert "si-together" not in code and PAGE_BREAK in code


# ---------------------------------------------------------------------- the windows

def test_page_breaks_button_saves_and_undoes(qapp, project, compound):
    from si_assistant.ui.page_flow_ui import PageFlowButton
    saved = []
    button = PageFlowButton(project, lambda: compound.data,
                            lambda: saved.append(dict(compound.data)), lambda: "3g",
                            compound=True)
    assert button.text() == "Page breaks"
    button.new_page.setChecked(True)
    button.keeps["together"].setChecked(True)
    assert page_flow.item_flow(compound.data) == {"new_page": True, "keep": "together"}
    assert len(saved) == 2 and button.text() == "Page breaks: new page, kept together"
    # A section has no "split" choice (sections may always split unless kept together).
    section = project.section(compound.section_id)
    sbutton = PageFlowButton(project, lambda: section.data, lambda: None, lambda: "S",
                             compound=False)
    assert "split" not in sbutton.keeps


def test_settings_box_round_trip(qapp):
    from si_assistant.ui.page_flow_ui import PageFlowSettings
    s = {"page_flow": {"heading_lines": 3, "widows": "strong", "emphasis_headings": False,
                       "keep_compounds": True, "data_spacing": "run_on"}}
    box = PageFlowSettings(s)
    out = {}
    box.apply(out)
    assert out == s
