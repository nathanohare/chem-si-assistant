"""Importing an SI, phase I4 (SPEC §13u.9, §13u.16): the cover page, the table of
contents, the sections with their headings, figures and tables, the reference list and
the citations. Made-up SIs only (a Word file built here, and the shipped example PDF)."""

import io
import struct
import zipfile
import zlib

import pytest

from si_assistant import resources
from si_assistant.core import si_import as I
from si_assistant.core import si_import_apply as A
from si_assistant.core import si_import_read as R
from si_assistant.core import si_import_refs as S
from si_assistant.core import si_import_text as T
from si_assistant.core.project import Project

# ---------------------------------------------------------------------- references


@pytest.mark.parametrize("text, expected", [
    ("Smith, A. B.; Jones, C. D. A Made-Up Title on Drying Solvents. J. Org. Chem. 2001, 66, "
     "1234–1240. https://doi.org/10.1021/jo0000001.",
     {"authors": ["Smith, A. B.", "Jones, C. D."], "title": "A Made-Up Title on Drying Solvents",
      "journal": "J. Org. Chem.", "year": "2001", "volume": "66", "pages": "1234–1240",
      "doi": "10.1021/jo0000001"}),
    # a PDF's lost spaces ("Chem.2001"), a DOI broken over a line, an issue
    ("Brown, E. F.; O'Neil, G.; McDonald, H. Another Invented Study. Org. Lett.2010, 12 (3), "
     "456–459. https:// doi.org/10.1021/ol0000002",
     {"authors": ["Brown, E. F.", "O'Neil, G.", "McDonald, H."], "journal": "Org. Lett.",
      "year": "2010", "volume": "12", "issue": "3", "pages": "456–459",
      "doi": "10.1021/ol0000002"}),
    # RSC / Wiley: initials first, no title
    ("A. B. Smith, C. D. Jones and E. F. Brown, Chem. Commun., 2015, 51, 7890.",
     {"authors": ["Smith, A. B.", "Jones, C. D.", "Brown, E. F."], "title": "",
      "journal": "Chem. Commun.", "year": "2015", "volume": "51", "pages": "7890"}),
    # Nature
    ("Smith, A. B., Jones, C. D. & Brown, E. A short synthesis of everything. Nature 465, "
     "123–128 (2010).",
     {"authors": ["Smith, A. B.", "Jones, C. D.", "Brown, E."],
      "title": "A short synthesis of everything", "journal": "Nature", "year": "2010",
      "volume": "465", "pages": "123–128"}),
    # the year after the authors (Wiley Online, Cell)
    ("Green, G. H. and White, I. (2004) Invented Couplings of Amines. Chem. Eur. J. 10, 101-108.",
     {"authors": ["Green, G. H.", "White, I."], "title": "Invented Couplings of Amines",
      "journal": "Chem. Eur. J.", "year": "2004", "volume": "10", "pages": "101–108"}),
])
def test_references_are_read_in_the_common_styles(text, expected):
    ref = S.parse_reference(text)
    assert ref["type"] == "article"
    for key, value in expected.items():
        assert ref[key] == value, key


def test_what_cannot_be_read_is_kept_whole():
    ref = S.parse_reference("Staining Solutions for TLC. A web page, 2006. "
                            "https://www.example.org/stains/")
    assert ref["type"] == "misc" and ref["title"].startswith("Staining Solutions for TLC")
    assert ref["url"] == "https://www.example.org/stains/"


def test_reference_lists_are_split_into_entries():
    # (a PDF ran entries together and cut one in two)
    assert S.split_entries([
        "(1) Smith, A. Title one. J. Org. Chem. 2001, 66, 1. (2) Jones, B.;",
        "Brown, C. Title two. Org. Lett. 2002, 4, 2.",
        "(3) Green, D. Title three. Science 2003, 300, 3."]) == [
        ("1", "Smith, A. Title one. J. Org. Chem. 2001, 66, 1."),
        ("2", "Jones, B.; Brown, C. Title two. Org. Lett. 2002, 4, 2."),
        ("3", "Green, D. Title three. Science 2003, 300, 3.")]
    # a list continuing the main text's numbers; "100 2" inside an entry is no number
    assert [n for n, _t in S.split_entries([
        "12. Smith, A. Title. Org. Synth. 2001, 100 13-20.", "13. Jones, B. Title. "
        "Org. Lett. 2002, 4, 2."])] == ["12", "13"]
    # parts a), b): each its own reference; "[1]" cites both
    found = S.split_entries(["[1] a) Smith, A. J. Org. Chem. 2001, 66, 1; b) Jones, B. "
                             "Org. Lett. 2002, 4, 2.", "[2] Green, D. Science 2003, 300, 3."])
    assert [n for n, _t in found] == ["1a", "1b", "2"]
    # Word's own numbered list: no numbers in the text
    assert [n for n, _t in S.split_entries(["Smith, A. One.", "Jones, B. Two."])] == ["1", "2"]


def test_citations_are_linked_to_the_references():
    found = [S.ImportedReference(n, "") for n in ("1a", "1b", "2", "3", "4")]
    numbers = S.citation_map(found, {k: f"r-{k}" for k in range(5)})
    assert numbers["1"] == ["r-0", "r-1"] and numbers["4"] == ["r-4"]
    link = lambda t: S.link_citations(t, numbers)   # noqa: E731
    assert link("as reported.^{2,3}") == "as reported.@cite{r-2,r-3}"
    assert link("procedures.^{2–4} Then") == "procedures.@cite{r-2,r-3,r-4} Then"
    assert link("by TLC [3].") == "by TLC@cite{r-3}."
    assert link("literature.^{1}") == "literature.@cite{r-0,r-1}"
    # not citations: isotopes, units, substituents, numbers with no reference
    for text in ("the ^{13}C NMR", "^{1}H spectra", "cm^{2}", "R^{2} group", "Hz^{2}",
                 "(Table S1)^{9}", "[12]"):
        assert link(text) == text


# ---------------------------------------------------------------------- a Word SI

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def png(w: int = 4, h: int = 3) -> bytes:
    rows = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def r(text, bold=False, valign=""):
    rpr = ("<w:b/>" if bold else "") + (f'<w:vertAlign w:val="{valign}"/>' if valign else "")
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def p(*runs, style=""):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def cell(*runs, merge="", span=1):
    tcpr = (f'<w:gridSpan w:val="{span}"/>' if span > 1 else "") + \
        ('<w:vMerge w:val="restart"/>' if merge == "restart" else
         "<w:vMerge/>" if merge == "continue" else "")
    return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{p(*runs)}</w:tc>"


PICTURE = ('<w:p><w:r><w:drawing><wp:inline><wp:extent cx="2743200" cy="914400"/>'
           '<a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rIdP1"/>'
           '</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
           '</w:r></w:p>')

REFERENCES = [
    "Smith, A. B.; Jones, C. D. A Made-Up Title on Drying Solvents. J. Org. Chem. 2001, 66, "
    "1234–1240. https://doi.org/10.1021/jo0000001.",
    "Brown, E. F. Another Invented Study. Org. Lett. 2010, 12 (3), 456–459.",
    "Green, G. H. A Third Paper Nobody Wrote. Angew. Chem. Int. Ed. 2015, 54, 7890.",
]


def word_si() -> bytes:
    """A made-up SI: cover, contents, General Information, an optimisation section with a
    table and a figure, a synthesis section with sub-sections, a general procedure and
    three compounds, an NMR spectra section and the reference list."""
    H1, H2 = "Heading1", "Heading2"
    body = "".join([
        p(r("Supporting Information")),
        p(r("Invented Esters: A Test SI", bold=True)),
        p(r("Ada Lovelace"), r("a", valign="superscript"), r(", Charles Babbage"),
          r("a,b", valign="superscript"), r(" and Grace Hopper"), r("b,*", valign="superscript")),
        p(r("a", valign="superscript"), r("Department of Chemistry, Example University, "
                                          "Exampletown")),
        p(r("b", valign="superscript"), r("Institute of Testing, Testville")),
        p(r("*E-mail: grace.hopper@example.org")),
        p(r("Table of Contents", bold=True)),
        p(r("1. General Information 2")), p(r("2. Optimization 3")), p(r("3. Synthesis 4")),
        p(r("1. General Information"), style=H1),
        p(r("Solvents were dried as reported."), r("1", valign="superscript"),
          r(" Reactions were monitored by TLC [3].")),
        p(r("1.1 Solvents"), style=H2),
        p(r("THF was distilled from sodium.")),
        p(r("2. Optimization"), style=H1),
        p(r("The base was screened (Table S1).")),
        p(r("Table S1.", bold=True), r(" Screening of bases.")),
        "<w:tbl>"
        "<w:tr>" + cell(r("Entry", bold=True)) + cell(r("Base", bold=True))
        + cell(r("Yield", bold=True), r("a", bold=True, valign="superscript")) + "</w:tr>"
        "<w:tr>" + cell(r("1"), merge="restart") + cell(r("K"), r("2", valign="subscript"),
                                                         r("CO"), r("3", valign="subscript"))
        + cell(r("92%")) + "</w:tr>"
        "<w:tr>" + cell(merge="continue") + cell(r("Cs2CO3")) + cell(r("88%")) + "</w:tr>"
        "<w:tr>" + cell(r("no base"), span=2) + cell(r("0%")) + "</w:tr>"
        "</w:tbl>",
        p(r("a", valign="superscript"), r(" Isolated yield.")),
        PICTURE,
        p(r("Figure S1.", bold=True), r(" The reaction setup.")),
        p(r("3. Synthesis"), style=H1),
        p(r("All reactions were run under nitrogen.")),
        p(r("3.1 Starting Materials"), style=H2),
        p(r("The acids were bought from suppliers.")),
        p(r("Ethyl benzoate (1a).", bold=True)),
        p(r("Colorless oil (12.0 mg, 80%). Spectral data matched those reported in the "
            "literature."), r("2", valign="superscript")),
        p(r("3.2 Products"), style=H2),
        p(r("General Procedure A: Esterification.", bold=True)),
        p(r("The acid (0.50 mmol) was heated in the alcohol (2 mL) for 2 h.")),
        p(r("Methyl benzoate (2a).", bold=True)),
        p(r("Prepared according to General Procedure A from benzoic acid (61.0 mg, 0.50 mmol)."
            " Colorless oil (55.0 mg, 81%).")),
        p(r("Propyl benzoate (2b).", bold=True)),
        p(r("Prepared according to General Procedure A from benzoic acid (61.0 mg, 0.50 mmol)."
            " Colorless oil (60.1 mg, 73%).")),
        p(r("4. NMR Spectra"), style=H1),
        p(r("Copies of the spectra follow.")),
        p(r("5. References"), style=H1),
        *[p(r(ref)) for ref in REFERENCES],
    ])
    styles = ('<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              '</w:style><w:style w:type="paragraph" w:styleId="Heading2">'
              '<w:name w:val="heading 2"/></w:style></w:styles>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdP1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/image" Target="media/image1.png"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/image1.png", png())
    return buf.getvalue()


@pytest.fixture
def word_plan(tmp_path):
    path = tmp_path / "Invented SI.docx"
    path.write_bytes(word_si())
    read = R.read_file(path)
    assert not read.error
    return read, I.analyse(read.paras)


def outline_of(plan) -> list[tuple]:
    by_key = {s.key: s for s in plan.sections}
    return [(s.kind, s.name, by_key[s.parent].name if s.parent else "", s.keep)
            for s in plan.sections]


def test_the_word_reader_takes_pictures_and_table_cells(word_plan):
    read, _plan = word_plan
    pictures = [o for q in read.paras for o in q.objects if o["kind"] == "picture"]
    assert len(pictures) == 1 and pictures[0]["picture"][:4] == b"\x89PNG"
    assert pictures[0]["width_in"] == pytest.approx(3.0)
    cells = [q.cell for q in read.paras if q.cell]
    assert cells[0] == (1, 0, 0, 1, "") and (1, 2, 0, 1, "continue") in cells
    assert (1, 3, 0, 2, "") in cells and (1, 3, 2, 1, "") in cells


def test_the_outline_of_a_word_si(word_plan):
    _read, plan = word_plan
    assert outline_of(plan) == [
        ("toc", "Table of Contents", "", False),
        ("general", "General Information", "", True),
        ("text", "Optimization", "", True),
        ("text", "Synthesis", "", True),
        ("text", "Starting Materials", "Synthesis", True),
        ("data", "Compounds", "Starting Materials", True),
        ("data", "Products", "Synthesis", True),
        ("spectra", "NMR Spectra", "", False),
        ("references", "References", "", True),
    ]
    sections = {s.name: s for s in plan.sections}
    assert sections["General Information"].text == (
        "Solvents were dried as reported.^{1} Reactions were monitored by TLC [3].\n"
        "# Solvents\nTHF was distilled from sodium.")
    assert sections["Synthesis"].text == "All reactions were run under nitrogen."
    assert [c.cid for c in sections["Compounds"].compounds] == ["1a"]
    assert [c.cid for c in sections["Products"].compounds] == ["2a", "2b"]
    assert [g.name for g in sections["Products"].gps] == ["General Procedure A"]


def test_tables_and_figures_with_their_captions(word_plan):
    _read, plan = word_plan
    s = next(s for s in plan.sections if s.name == "Optimization")
    table, figure = s.figures.values()
    assert s.text == (f"The base was screened (Table S1).\n!table{{{table.key}}} Screening of "
                      f"bases.\n!figure{{{figure.key}}} The reaction setup.")
    assert (table.kind, table.label, table.caption) == ("table", "Table S1", "Screening of bases.")
    assert table.table["header_rows"] == 1
    assert table.table["rows"] == [
        [{"text": "**Entry**"}, {"text": "**Base**"}, {"text": "**Yield^{a}**"}],
        [{"text": "1", "rowspan": 2}, {"text": "K_{2}CO_{3}"}, {"text": "92%"}],
        [None, {"text": "Cs2CO3"}, {"text": "88%"}],
        [{"text": "no base", "colspan": 2}, None, {"text": "0%"}]]
    assert table.table["footnotes"] == ["^{a} Isolated yield."]
    assert (figure.kind, figure.label, figure.ext) == ("picture", "Figure S1", ".png")
    assert figure.width_cm == pytest.approx(7.62)


def test_the_cover_page(word_plan):
    _read, plan = word_plan
    cover = plan.cover
    assert cover.title == "Invented Esters: A Test SI"
    assert [(a["name"], a["affiliations"], a["corresponding"], a["email"]) for a in cover.authors] \
        == [("Ada Lovelace", ["a"], False, ""), ("Charles Babbage", ["a", "b"], False, ""),
            ("Grace Hopper", ["b"], True, "grace.hopper@example.org")]
    assert cover.affiliations == [
        {"marker": "a", "text": "Department of Chemistry, Example University, Exampletown"},
        {"marker": "b", "text": "Institute of Testing, Testville"}]


def test_the_references_of_a_word_si(word_plan):
    _read, plan = word_plan
    assert [r.number for r in plan.references] == ["1", "2", "3"]
    assert plan.references[0].ref["doi"] == "10.1021/jo0000001"
    assert plan.references[2].ref["journal"] == "Angew. Chem. Int. Ed."
    c = next(c for c in plan.compounds if c.cid == "1a")
    assert c.cites == ["2"]


def test_a_heading_level_changed_by_hand(word_plan):
    """The review window can change any heading's level (SPEC §2.7): the sections are
    worked out again, keeping names and ticks changed so far."""
    _read, plan = word_plan
    optimization = next(s for s in plan.sections if s.name == "Optimization")
    optimization.name = "Screening"
    sub = next(q for q in plan.headings if q.text == "3.1 Starting Materials")
    plan.level_overrides[id(sub)] = 3                  # a heading in the text
    T.outline(plan, plan.body_size)
    assert outline_of(plan)[2:7] == [
        ("text", "Screening", "", True),
        ("text", "Synthesis", "", True),
        ("data", "Compounds", "Synthesis", True),
        ("data", "Products", "Synthesis", True),
        ("spectra", "NMR Spectra", "", False)]
    synthesis = next(s for s in plan.sections if s.name == "Synthesis")
    assert "## Starting Materials" in synthesis.text
    general = next(q for q in plan.headings if q.text == "1. General Information")
    plan.level_overrides[id(general)] = 0              # not a heading: its text goes on
    T.outline(plan, plan.body_size)
    assert not any(s.kind == "general" for s in plan.sections)


# ---------------------------------------------------------------------- writing


def new_project(tmp_path, name="P") -> Project:
    p = Project.create(tmp_path / name, name)
    p.acquire_lock("Tester")
    p.enable_undo()
    return p


def test_a_word_si_into_a_new_project(word_plan, tmp_path):
    _read, plan = word_plan
    p = new_project(tmp_path)
    p.ensure_spectra_section()
    res = A.apply_plan(p, plan, "Invented SI.docx")
    assert (res.general, res.references, res.figures, res.tables) == (True, 3, 1, 1)
    assert res.cover == ["title", "authors", "affiliations"]
    s = p.settings
    assert s["title"] == "Invented Esters: A Test SI"
    assert [a["name"] for a in s["authors"]] == ["Ada Lovelace", "Charles Babbage", "Grace Hopper"]
    assert s["authors"][2]["email"] == "grace.hopper@example.org"

    def tree(sections):
        return [(x.name, x.type, tree(x.children)) for x in sections]
    assert tree(p.top_sections) == [
        ("Optimization", "text", []),
        ("Synthesis", "text", [("Starting Materials", "text", [("Compounds", "data", [])]),
                               ("Products", "data", [])]),
        ("Spectra", "spectra", [])]                 # (new sections before the spectra)
    ids = [x["id"] for x in p.references]
    general = p.read_front_text("general")
    assert general == (f"Solvents were dried as reported.@cite{{{ids[0]}}} Reactions were "
                       f"monitored by TLC@cite{{{ids[2]}}}.\n# Solvents\n"
                       "THF was distilled from sodium.")
    optimization = p.top_sections[0]
    text = p.read_section_text(optimization)
    owner = p.text_owner(optimization.id)
    figure_id = next(iter(p.figures(owner)))
    entry = p.figures(owner)[figure_id]
    assert (entry["label"], entry["width"]) == ("Figure S1", pytest.approx(7.62))
    table_id = text.split("!table{")[1].split("}")[0]
    table = p.read_table(owner, table_id)
    assert table.label == "Table S1" and table.footnotes == ["^{a} Isolated yield."]
    assert f"!figure{{{figure_id}}} The reaction setup." in text
    by_id = {p.id_report().display_id(c.id): c for c in p.all_compounds()}
    assert by_id["1a"].values["references"]["refs"] == [ids[1]]
    assert by_id["2a"].values["procedure"]["gp"]          # (linked to its procedure)
    p.undo()                                              # (one step)
    assert [x.name for x in p.top_sections] == ["Spectra"]
    assert not p.references and not p.read_front_text("general")
    p.close()


def test_a_word_si_into_the_open_project(word_plan, tmp_path):
    """General Information is added after the project's own; a reference it has already
    (same DOI) is not added again, and citations of it are linked to the one it has; its
    own cover page is left alone."""
    _read, plan = word_plan
    p = new_project(tmp_path)
    p.write_front_text("general", "Our own text.")
    have = p.add_reference({"type": "article", "title": "Ours", "doi": "10.1021/jo0000001"})
    p.settings["authors"] = [{"name": "Someone Else", "affiliations": [], "corresponding": True,
                              "email": ""}]
    p.save_settings()
    section = p.add_section("data", "Our compounds")
    res = A.apply_plan(p, plan, "Invented SI.docx", section_id=section.id)
    assert (res.references, res.references_known) == (2, 1)
    assert p.read_front_text("general").startswith(
        f"Our own text.\n\nSolvents were dried as reported.@cite{{{have['id']}}}")
    assert [a["name"] for a in p.settings["authors"]] == ["Someone Else"]
    assert "authors" not in res.cover
    # all the compounds go into the chosen section; the other sections are added
    assert len(section.compounds) == 3
    assert [x.name for x in p.top_sections] == ["Our compounds", "Optimization", "Synthesis"]
    p.close()


def test_left_out_sections_figures_and_references(word_plan, tmp_path):
    _read, plan = word_plan
    sections = {s.name: s for s in plan.sections}
    sections["Optimization"].keep = False
    sections["Synthesis"].keep = False                  # (its sub-sections move up)
    sections["NMR Spectra"].keep = True                 # (ticked by hand: imported)
    plan.references[1].keep = False
    plan.cover.keep = False
    p = new_project(tmp_path)
    res = A.apply_plan(p, plan, "Invented SI.docx")
    assert [x.name for x in p.top_sections] == ["Starting Materials", "Products",
                                                "NMR Spectra"]
    assert res.references == 2 and not res.cover
    by_id = {p.id_report().display_id(c.id): c for c in p.all_compounds()}
    assert by_id["1a"].values["references"].get("refs", []) == []   # (its reference left out)
    p.close()


# ---------------------------------------------------------------------- the example PDF


@pytest.fixture(scope="module")
def example():
    read = R.read_file(resources.example_pdf())
    return read, I.analyse(read.paras)


def test_the_outline_of_a_pdf_si(example):
    read, plan = example
    assert outline_of(plan) == [
        ("toc", "Table of Contents", "", False),
        ("general", "General Information", "", True),
        ("text", "Reaction Optimization", "", True),
        ("text", "Synthesis and Characterization", "", True),
        ("data", "Compounds", "Synthesis and Characterization", True),
        ("references", "References", "", True)]
    general = plan.sections[1].text
    assert general.startswith("**About this example.**")
    assert "\n# *General methods*\n" in general and "\n# *Abbreviations*\n" in general
    assert plan.cover.title.startswith("Sodium Borohydride Reduction of Aryl Methyl Ketones")
    assert [a["name"] for a in plan.cover.authors] == ["Alex Example", "Blair Sample"]
    assert len(plan.references) == 7 and all(r.ref["doi"] for r in plan.references)
    # a table's rows are not run into its caption
    table = next(f for s in plan.sections for f in s.figures.values() if f.label == "Table S1")
    assert table.caption == "Optimization of the reduction of 4′-bromoacetophenone"
    assert table.is_table and table.bottom > table.top + 100


def test_figures_and_tables_are_cut_out_of_a_pdf(qapp, example):
    from si_assistant.core import si_import_pdf
    read, plan = example
    plan = I.analyse(read.paras)
    assert si_import_pdf.add_figures(resources.example_pdf(), plan) == 3
    figures = {f.label: f for s in plan.sections for f in s.figures.values()}
    assert set(figures) == {"Table S1", "Scheme S1", "Scheme S2"}
    assert all(f.data[:4] == b"%PDF" and 5 < f.width_cm < 17 for f in figures.values())
    # the scheme's picture starts below the general procedure above it on its page
    gp = plan.gps[0]
    scheme = figures["Scheme S1"]
    assert gp.end[0] == scheme.page and scheme.top > gp.end[1]


# ---------------------------------------------------------------------- the review window


def test_the_review_window_pages(qapp, word_plan):
    from PySide6.QtCore import Qt
    from si_assistant.ui import si_import_ui as U
    read, plan = word_plan
    d = U.ImportDialog(plan, read, None)
    tops = [d.tree.topLevelItem(i).text(0) for i in range(d.tree.topLevelItemCount())]
    assert tops == ["General procedures", "Compounds", "Sections", "Cover page",
                    "References (3)"]
    sections = d.tree.topLevelItem(2)
    assert [sections.child(k).text(0) for k in range(sections.childCount())] == [
        "Table of Contents", "General Information", "Optimization", "Synthesis",
        "Starting Materials", "NMR Spectra"]
    # a section left out in the list
    sections.child(2).setCheckState(0, Qt.CheckState.Unchecked)
    assert next(s for s in plan.sections if s.name == "Optimization").keep is False
    # a reference changed by hand
    page = d.references_page
    page.table.item(1, 3).setText("A Better Title")
    assert plan.references[1].ref["title"] == "A Better Title"
    # a heading's level: the sections are worked out again (the change above is kept)
    sub = next(q for q in plan.headings if q.text == "3.1 Starting Materials")
    d.set_level(sub, 3)
    assert [sections.child(k).text(0) for k in range(sections.childCount())] == [
        "Table of Contents", "General Information", "Optimization", "Synthesis",
        "NMR Spectra"]
    assert sections.child(2).checkState(0) == Qt.CheckState.Unchecked
    d.close()
