"""The Word export (compile/word.py, SPEC §13z): the sample project as a .docx, read back
with python-docx and checked for what the PDF has."""

import os
import re
import zipfile

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn

from si_assistant.compile import word
from si_assistant.compile.document import CompileOptions
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.word import (citation_numbers, compile_docx, heading_number,
                                       reference_runs, xml_text)
from si_assistant.core import formatting
from tests.sample_project import FACTS, build_sample


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def export(p, **kw):
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    path, warnings = compile_docx(p, options, image_info, convert_to_png)
    return path, warnings, Document(str(path))


def all_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def runs_of(doc):
    for p in doc.paragraphs:
        yield from p.runs
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield from p.runs


# ---------------------------------------------------------------------- the whole SI

def test_sample_si_as_word(sample):
    p, parts = sample
    path, warnings, doc = export(p)
    assert path.name == "SI.docx" and path.parent == p.folder / "build"
    assert warnings == []
    text = all_text(doc)
    for fact in (FACTS["title"], FACTS["email"], FACTS["data_section"], FACTS["c2_name"],
                 FACTS["c4_name"], FACTS["c2_yield"], FACTS["table_caption"],
                 FACTS["scheme_caption"], "Table of Contents", "General Information"):
        assert fact in text, fact
    assert FACTS["c2_hrms"][0] in text and FACTS["c2_hrms"][1] in text
    # Labels as in the PDF, the label in bold.
    assert re.search(r"Table S1\.\s+Optimization of the acetylation", text)
    assert "Figure S1." in text and "Scheme S1." in text
    caption = next(q for q in doc.paragraphs if q.text.startswith("Table S1."))
    assert caption.style.name == word.TABLE_CAPTION and caption.runs[0].bold
    # Sections are Word headings (so the navigation pane and TOC work).
    heads = [q.text for q in doc.paragraphs if q.style.name.startswith("Heading")]
    assert FACTS["data_section"] in heads and "Spectra" in heads
    # Compound titles, with the compound's ID; CDCl₃ with a subscript 3.
    title = next(q for q in doc.paragraphs if q.style.name == word.COMPOUND_TITLE)
    assert title.text.startswith(FACTS["c2_name"]) and "(2)" in title.text
    assert any(r.text == "3" and r.font.subscript for r in runs_of(doc))
    assert any(r.text == "1" and r.font.superscript for r in runs_of(doc))   # ¹H NMR
    # Pictures: structures, the scheme, the figure and the spectra page (new projects
    # print two spectra on a page, so compound 2's two spectra share one).
    with zipfile.ZipFile(path) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert len(media) >= 5
    # The table: a Word table with its merged cell and footnotes.
    table = doc.tables[0]
    assert "Entry" in table.rows[0].cells[0].text
    assert "0.2 mmol scale." in table.rows[-1].cells[0].text
    # Page numbers S1, S2… in the footer (a PAGE field after the "S").
    footer = doc.sections[0].footer._element.xml
    assert ">S<" in footer and "PAGE" in footer
    assert doc.sections[0].different_first_page_header_footer   # (none on the cover)
    # The table of contents lists the sections with page-number fields.
    xml = doc.element.xml
    assert "PAGEREF _Toc" in xml
    # The build folder keeps only the file (the pictures' Typst source is removed).
    assert not (p.folder / "build" / word.RENDER_SOURCE).exists()


@pytest.mark.parametrize("style", ["ACS", "RSC", "Wiley"])
def test_every_journal_style(sample, style):
    p, _parts = sample
    p.settings["journal_style"] = style
    _path, warnings, doc = export(p)
    assert warnings == []
    look = word.LOOKS[style.lower()]
    assert doc.styles["Normal"].font.size.pt == look.body
    width = doc.sections[0].page_width.inches
    assert abs(width - look.paper[0]) < 0.02
    cover = all_text(doc)
    assert ("Electronic Supplementary Information" if style == "RSC"
            else "Supporting Information") in cover
    caption = next(q.text for q in doc.paragraphs if q.text.startswith("Table S1"))
    assert caption.startswith("Table S1" + look.caption_sep)


def test_formatting_settings_are_honoured(sample):
    """Project settings ▸ Text and tables (PR #8) in the Word file too."""
    p, _parts = sample
    formatting.store_formatting(p.settings, {
        "line_spacing": 1.5, "space_before": 2, "space_after": 6, "caption_size": 8,
        "table_size": 7.5, "table_header_size": 9, "table_header_bold": True,
        "table_notes_size": 6.5})
    _path, _warnings, doc = export(p)
    normal = doc.styles["Normal"].paragraph_format
    assert normal.line_spacing == 1.5
    assert normal.space_before.pt == 2 and normal.space_after.pt == 6
    assert doc.styles[word.FIGURE_CAPTION].font.size.pt == 8
    assert doc.styles[word.TABLE_NOTE].font.size.pt == 6.5
    table = doc.tables[0]
    header = [r for c in table.rows[0].cells for q in c.paragraphs for r in q.runs if r.text]
    assert header and all(r.bold and r.font.size.pt == 9 for r in header)
    body = [r for c in table.rows[1].cells for q in c.paragraphs for r in q.runs if r.text]
    assert body and all(r.font.size.pt == 7.5 for r in body)


def test_own_table_sizes_and_widths(sample):
    """A table's own caption size, cell size and column widths (table editor) win."""
    from si_assistant.core.tables import Cell, Table
    p, parts = sample
    owner = p.text_owner(parts["notes"].id)
    t = Table.new(2, 2)
    t.rows[0] = [Cell("Wide"), Cell("Auto")]
    t.rows[1] = [Cell("SMALLCELL", size=6), Cell("x")]
    t.widths = [4.0, None]
    t.caption_size = 7
    tid = p.add_table(owner, t)
    p.write_section_text(parts["notes"], p.read_section_text(parts["notes"])
                         + f"\n!table{{{tid}}} Sized table")
    _path, _warnings, doc = export(p)
    caption = next(q for q in doc.paragraphs if "Sized table" in q.text)
    assert all(r.font.size.pt == 7 for r in caption.runs if r.text.strip())
    table = next(x for x in doc.tables if "SMALLCELL" in x.rows[1].cells[0].text)
    small = [r for q in table.rows[1].cells[0].paragraphs for r in q.runs if r.text]
    assert small[0].font.size.pt == 6
    width = table.rows[0].cells[0]._tc.tcPr.find(qn("w:tcW"))
    assert int(width.get(qn("w:w"))) == round(4.0 / 2.54 * 1440)


def test_heading_numbers_in_word(sample):
    p, _parts = sample
    p.settings["heading_numbers"] = "decimal"
    _path, _warnings, doc = export(p)
    heads = [q.text for q in doc.paragraphs if q.style.name.startswith("Heading")]
    assert f"2. {FACTS['data_section']}" in heads
    p.settings["heading_numbers"] = "outline"
    _path, _warnings, doc = export(p)
    heads = [q.text for q in doc.paragraphs if q.style.name.startswith("Heading")]
    assert f"2. {FACTS['data_section']}" in heads


def test_landscape_spectra_get_their_own_word_section(sample):
    p, _parts = sample
    d = p.spectra_defaults()
    d.update(orientation="landscape", nmr_links=True)
    p.set_spectra_defaults(d)
    _path, warnings, doc = export(p)
    assert warnings == []
    kinds = [s.orientation for s in doc.sections]
    assert WD_ORIENT.LANDSCAPE in kinds
    assert kinds[0] == WD_ORIENT.PORTRAIT and kinds[-1] == WD_ORIENT.PORTRAIT
    for s in doc.sections[1:]:
        assert not s.different_first_page_header_footer
    # The ¹H NMR of the peak report links to the spectrum's page (a bookmark there).
    xml = doc.element.xml
    anchors = set(re.findall(r'w:anchor="([^"]+)"', xml))
    marks = set(re.findall(r'w:bookmarkStart w:id="\d+" w:name="([^"]+)"', xml))
    assert any(a.startswith("_si") for a in anchors)
    assert anchors <= marks


def test_references_and_citations(sample):
    p, parts = sample
    ref = p.add_reference({"type": "article", "authors": ["Fulmer, Gregory R.",
                                                          "Miller, Alexander J. M."],
                           "title": "NMR Chemical Shifts", "journal": "Organometallics",
                           "year": "2010", "volume": "29", "pages": "2176-2179",
                           "doi": "10.1021/om100106e"})
    p.write_section_text(parts["notes"], p.read_section_text(parts["notes"])
                         + f"\nAs reported.@cite{{{ref['id']}}}")
    _path, _warnings, doc = export(p)
    cite = [r for r in runs_of(doc) if r.text == "1" and r.font.superscript]
    assert cite
    text = all_text(doc)
    assert "References" in text
    assert "(1)\tFulmer, G. R.; Miller, A. J. M. NMR Chemical Shifts. Organometallics 2010, " \
           "29, 2176–2179. DOI: 10.1021/om100106e." in text


def test_nothing_ticked_says_so(sample):
    p, parts = sample
    from si_assistant.core import front
    keys = {front.FRONT_ROOT} | {s.id for s in p.top_sections}
    _path, warnings, _doc = export(p, excluded=frozenset(keys))
    assert any("Nothing was chosen" in w for w in warnings)


def test_unusual_characters_cannot_break_the_file(sample):
    p, parts = sample
    p.write_section_text(parts["notes"], "Odd \x01\x0b characters: <&> \"quotes\" \\# ü 𝛿")
    _path, warnings, doc = export(p)
    assert "Odd  characters: <&>" in all_text(doc)


def test_a_locked_word_file_gets_a_numbered_name(sample, monkeypatch):
    p, _parts = sample
    real = os.replace

    def locked(src, dst):
        if str(dst).endswith("SI.docx"):
            raise PermissionError("open in Word")
        return real(src, dst)
    monkeypatch.setattr(word.os, "replace", locked)
    path, warnings, _doc = export(p)
    assert path.name == "SI (2).docx"
    assert any("open in another program" in w for w in warnings)


def test_a_picture_typst_cannot_draw_is_left_out_with_a_note(sample, monkeypatch):
    p, _parts = sample
    built = word.prepare_docx(p, CompileOptions.from_settings(p.settings), image_info,
                              convert_to_png)
    built.writer.pictures[0].page = "#page[#image(\"/no/such/file.png\")]"
    path, warnings = word.finish_docx(built)
    assert path.exists()
    assert any("could not be drawn" in w for w in warnings)


def test_word_xml_is_in_schema_order(sample):
    """Word refuses files whose elements are out of order: the ones added by hand."""
    p, _parts = sample
    d = p.spectra_defaults()
    d.update(orientation="landscape")
    p.set_spectra_defaults(d)
    path, _warnings, doc = export(p)
    for sect in doc.element.body.iter(qn("w:sectPr")):
        tags = [c.tag for c in sect]
        assert tags.index(qn("w:pgNumType")) < tags.index(qn("w:cols"))
    for tc in doc.element.body.iter(qn("w:tc")):
        tags = [c.tag for c in tc.tcPr]
        if qn("w:tcBorders") in tags and qn("w:vAlign") in tags:
            assert tags.index(qn("w:tcBorders")) < tags.index(qn("w:vAlign"))
    with zipfile.ZipFile(path) as z:
        settings = z.read("word/settings.xml").decode()
    assert settings.index("updateFields") < settings.index("<w:compat")


# ---------------------------------------------------------------------- small pieces

def test_heading_number_patterns():
    assert heading_number([2], "1.1", "style") == "2"
    assert heading_number([2, 3], "1.1", "style") == "2.3"
    assert heading_number([2, 3], "1.1.", "decimal") == "2.3."
    assert heading_number([1], "outline", "outline") == "1."
    assert heading_number([1, 2], "outline", "outline") == "B."
    assert heading_number([1, 2, 28], "outline", "outline") == "ab."
    assert heading_number([1, 1, 1, 4], "outline", "outline") == "iv."
    assert heading_number([3], None, "style") == ""


def test_citation_numbers():
    assert citation_numbers([1]) == "1"
    assert citation_numbers([2, 1]) == "1,2"
    assert citation_numbers([1, 2, 3, 5]) == "1–3,5"
    assert citation_numbers([4, 4, 5]) == "4,5"


REF = {"type": "article", "authors": ["Fulmer, Gregory R.", "Miller, Alexander J. M.",
                                      "Jean-Luc Picard"],
       "title": "NMR Chemical Shifts of Trace Impurities", "journal": "Organometallics",
       "year": "2010", "volume": "29", "issue": "9", "pages": "2176-2179",
       "doi": "10.1021/om100106e"}


@pytest.mark.parametrize("style, expected", [
    ("acs", "Fulmer, G. R.; Miller, A. J. M.; Picard, J.-L. NMR Chemical Shifts of Trace "
            "Impurities. Organometallics 2010, 29 (9), 2176–2179. DOI: 10.1021/om100106e."),
    ("rsc", "G. R. Fulmer, A. J. M. Miller and J.-L. Picard, Organometallics, 2010, 29, "
            "2176–2179. DOI: 10.1021/om100106e."),
    ("angewandte", "G. R. Fulmer, A. J. M. Miller, J.-L. Picard, Organometallics 2010, 29, "
                   "2176–2179. DOI: 10.1021/om100106e."),
])
def test_reference_text_in_each_style(style, expected):
    runs = reference_runs(REF, style)
    assert "".join(r.text for r in runs) == expected
    journal = next(r for r in runs if r.text == "Organometallics")
    assert journal.italic


def test_a_book_reference():
    runs = reference_runs({"type": "book", "authors": ["Clayden, Jonathan"],
                           "title": "Organic Chemistry", "publisher": "OUP",
                           "year": "2012"}, "acs")
    assert "".join(r.text for r in runs) == "Clayden, J. Organic Chemistry. OUP, 2012."
    assert next(r for r in runs if r.text.startswith("Organic Chemistry")).italic


def test_xml_text():
    assert xml_text("a\x00b\x1fc\td\ne") == "abc\td\ne"


def test_header_authors_as_first_author_et_al(sample):
    """Project settings ▸ Page: {authors} as "First author et al." in the Word header
    too; the file's own author property keeps every name."""
    from si_assistant.core import page as page_setup
    p, _parts = sample
    s = dict(p.settings)
    s["authors"] = [{"name": "Nathan P. O'Hare"}, {"name": "Jane Smith"}, {"name": "Ian Brown"}]
    values = page_setup.page_settings(s)
    values["header"] = {"left": "{authors}", "center": "", "right": ""}
    values["author_style"] = "et_al"
    page_setup.store_page_settings(s, values)
    p.settings.clear()
    p.settings.update(s)
    _path, _warnings, doc = export(p)
    header = "".join(t.text or "" for t in doc.sections[-1].header._element.iter(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
    assert "O'Hare et al." in header and "Jane Smith" not in header
    assert doc.core_properties.author == "Nathan P. O'Hare, Jane Smith, Ian Brown"
