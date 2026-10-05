"""End-to-end: compile the sample project to a PDF and check what it says (SPEC §12)."""

import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.document import CompileOptions
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from tests.sample_project import FACTS, build_sample


def squash(text: str) -> str:
    """PDF text without spaces/line breaks (subscripts come out as separate pieces)."""
    return re.sub(r"\s+", "", text)


# Expected text from the sample project (tests/sample_project.py), without spaces.
TITLE = squash(FACTS["title_word"])
C2_NAME = squash(FACTS["c2_name"])
SCHEME = squash(FACTS["scheme_caption"])
TABLE = squash(FACTS["table_caption"])
FIGURE = squash(FACTS["figure_caption"])


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def compile_sample(p, **kw):
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    return compile_pdf(p, options, image_info, convert_to_png)


def test_full_si_compiles(sample):
    p, parts = sample
    result = compile_sample(p)
    assert result.pdf.exists() and result.typ.exists()
    reader = PdfReader(str(result.pdf))
    pages = [pg.extract_text() or "" for pg in reader.pages]
    everything = squash("".join(pages))

    # The cover alone on S1, the table of contents on S2 (SPEC §13k.4).
    assert "SupportingInformation" in squash(pages[0])
    assert TITLE in squash(pages[0])
    assert FACTS["email"] in pages[0]
    assert "TableofContents" not in squash(pages[0])
    assert squash(pages[1]).startswith("TableofContents")
    assert "GeneralInformation" not in squash(pages[0])
    # S-numbering on every page except the cover (SPEC §13k.2a).
    assert pages[0].split()[-1] != "S1"
    for i, text in enumerate(pages[1:], start=2):
        assert text.split()[-1] == f"S{i}"
    # Sections start on a new page after the TOC.
    assert squash(pages[2]).startswith("GeneralInformation")
    # Sections, compound blocks and data in tree order.
    assert everything.index("GeneralInformation") < everything.index(squash(FACTS["data_section"]))
    assert f"{C2_NAME}(2)." in everything
    assert squash(FACTS["c2_yield"]) + "." in everything
    assert "majorrotamer" in everything and "minorrotamer" in everything
    assert "{};found{}".format(*FACTS["c2_hrms"]) in everything
    assert "{};found{}".format(*FACTS["c4_hrms"]) in everything
    # The scheme with its label and caption.
    assert f"SchemeS1.{SCHEME}" in everything
    # The custom section with a table and a figure (SPEC §13k.5).
    assert f"TableS1.{TABLE}" in everything
    assert f"FigureS1.{FIGURE}" in everything
    # Spectra appendix: one page per spectrum page (1H of 2, two 13C pages of 2), with
    # program-added headers taken from the NMR reports.
    spectra_pages = [t for t in pages if "Spectra" in t or "NMR(400MHz" in squash(t)
                     or "NMR(100MHz" in squash(t)]
    assert any("2—1HNMR(400MHz,CDCl3)" in squash(t) for t in pages)
    assert any("(continued)" in t for t in pages)
    assert len(pages) >= 5
    assert result.warnings == []


@pytest.mark.parametrize("style,figure", [("ACS", "FigureS1."), ("Wiley", "FigureS1:"),
                                          ("RSC", "Fig.S1")])
def test_figures_tables_labels_and_mentions(sample, style, figure):
    """SPEC §13k.5/5a: a table with merged cells and footnotes, a figure, their labels,
    mentions of them (and of a scheme) printed as labels, and the TOC lists."""
    p, _parts = sample
    p.settings["journal_style"] = style
    p.settings["toc"] = {"figures": True, "tables": True, "schemes": True}
    result = compile_sample(p)
    assert result.warnings == []
    pages = [squash(pg.extract_text() or "") for pg in PdfReader(str(result.pdf)).pages]
    everything = "".join(pages)
    sep = {"ACS": ".", "Wiley": ":", "RSC": ""}[style]
    assert f"TableS1{sep}{TABLE}" in everything
    assert "EntryConditionsa" in everything             # header row, merged cell
    assert "a0.2mmolscale.bIsolatedyield." in everything  # footnotes
    assert squash(FACTS["c2_mention_in_table"]) in everything                  # a compound mention in a cell
    assert f"{figure}{FIGURE}" in everything
    word = "Fig." if style == "RSC" else "Figure"
    assert f"ThebestconditionsareinTableS1and{word}S1." in everything
    assert "showninTableS1." in everything
    for title in ("ListofFigures", "ListofTables", "ListofSchemes"):
        assert title in everything
    # TOC list entries: the label and caption, dot leaders, the page number.
    assert re.search(rf"TableS1{re.escape(sep)}{re.escape(TABLE)}\.{{5,}}S\d",
                     everything)
    assert re.search(rf"{re.escape(figure + FIGURE)}\.{{5,}}S\d", everything)


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_custom_header_and_footer(sample, style):
    """SPEC §13k.2a: fill-ins, roman numbers, bold/size, nothing on the cover."""
    p, _parts = sample
    p.settings["journal_style"] = style
    p.settings["page"] = {"header": {"left": "{title}", "center": "", "right": "SI of {authors}"},
                          "footer": {"left": "", "center": "", "right": "Page {page} of {pages}"},
                          "number_prefix": "S", "number_style": "i", "number_size": 9,
                          "number_bold": True}
    result = compile_sample(p)
    assert result.warnings == []
    pages = [pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages]
    total = len(pages)
    AUTHORS = "SIof" + squash(", ".join(FACTS["authors"]))
    roman = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    assert "Page" not in pages[0] and AUTHORS not in squash(pages[0])  # cover: nothing
    for n, text in enumerate(pages[1:], start=2):
        flat = squash(text)
        assert f"PageS{roman[n - 1]}ofS{roman[total - 1]}" in flat
        assert AUTHORS in flat
        assert TITLE in flat   # {title} in the header
    typ = result.typ.read_text(encoding="utf-8")
    assert 'number-style: "i"' in typ and "number-size: 9pt" in typ


def test_page_numbers_on_cover_and_style_footer(sample):
    p, _parts = sample
    p.settings["journal_style"] = "Wiley"
    p.settings["page"] = {"number_on_cover": True, "number_prefix": ""}
    result = compile_sample(p)
    pages = [pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages]
    assert pages[0].split()[-1] == "1"            # the cover now shows its number
    assert pages[1].split()[-1] == "2"
    typ = result.typ.read_text(encoding="utf-8")
    assert "footer: none" in typ                  # Wiley's own footer (number on the right)


def test_header_text_cannot_break_the_document(sample):
    p, _parts = sample
    p.settings["page"] = {"header": {"left": '#weird [x] $y$ "q" \\ {nope} CDCl3', "center": "",
                                     "right": ""}}
    result = compile_sample(p)
    text = (PdfReader(str(result.pdf)).pages[1].extract_text() or "")
    assert "#weird" in text and "$y$" in text and "{nope}" in text
    typ = result.typ.read_text(encoding="utf-8")
    assert '#"CDCl"#sub[#"3"]' in typ   # formula subscripted like other text


def test_acs_drawings_option_and_preview(sample):
    p, parts = sample
    result = compile_sample(p, structures="acs", only=("compound", parts["c2"].id))
    text = squash("".join(pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages))
    assert f"{C2_NAME}(2)." in text
    assert "SupportingInformation" not in text  # a preview has no cover page
    assert (p.folder / "build" / "assets" / f"{parts['c2'].id}.svg").exists()


def test_two_spectra_per_page(sample):
    p, _parts = sample
    one = len(PdfReader(str(compile_sample(p).pdf)).pages)
    p.set_spectra_defaults({"per_page": 2})   # (on the Spectra section, SPEC §13k.12)
    two = len(PdfReader(str(compile_sample(p).pdf)).pages)
    assert two < one


def test_unusual_characters_cannot_break_the_document(sample):
    p, parts = sample
    c2 = parts["c2"]
    c2.values["name"] = {"text": 'Weird #name [with] $math$ *stars* "quotes" \\ <tags> @ref'}
    p.save_compound(c2)
    result = compile_sample(p, only=("compound", c2.id))
    text = "".join(pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages)
    assert "$math$" in text and "@ref" in text and "#name" in text


@pytest.mark.parametrize("style", ["ACS", "RSC"])
def test_tall_table_runs_over_pages_without_overprinting(sample, style):
    """A table taller than a page (here 20 rows of long text) continues on the next page
    with its header repeated, instead of its rows being printed on top of each other."""
    from si_assistant.core.tables import Cell, Table
    p, parts = sample
    p.settings["journal_style"] = style
    owner = p.text_owner(parts["notes"].id)
    t = Table.new(21, 2)
    t.rows[0] = [Cell("Row"), Cell("Description")]
    sentence = " ".join(["The mixture was stirred at room temperature for a while."] * 7)
    for i in range(1, 21):
        t.rows[i] = [Cell(f"ROWA{i:02d}"), Cell(sentence)]
    table_id = p.add_table(owner, t)
    p.write_section_text(parts["notes"], p.read_section_text(parts["notes"])
                         + f"\n!table{{{table_id}}} A tall table")
    result = compile_sample(p)
    assert result.warnings == []
    where = {}
    pages_with_header = 0
    for n, page in enumerate(PdfReader(str(result.pdf)).pages):
        def visit(text, cm, tm, _font, _size, n=n):
            m = re.match(r"\s*(ROWA\d\d)", text)
            if m:
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                where[m.group(1)] = (n, round(y))
        text = page.extract_text(visitor_text=visit) or ""
        if "Description" in text and "ROWA" in text:
            pages_with_header += 1
    assert len(where) == 20
    assert len(set(where.values())) == 20          # no two rows in the same place
    assert pages_with_header >= 2                  # the header is repeated
