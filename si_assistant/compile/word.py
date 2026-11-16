"""The SI as a Word document (.docx), next to the PDF (SPEC §13z).

The Word file holds the same SI as the PDF, built from the same project by the same
rules (compile/document.Builder decides what is printed, in what order, with which
labels, IDs, links and warnings), but written as editable Word text:

    cover page, table of contents (a Word field: Word fills in the page numbers when the
        file is opened), General Information, every section in tree order
    headings in Word's own Heading 1, 2, 3… styles (numbered as the SI is numbered), so
        Word's navigation pane and table of contents work
    compound blocks, general procedures and texts as formatted text (bold, italic,
        super/subscripts, mentions as the compounds' IDs, citations numbered)
    tables as Word tables (merged cells, rules, header rows repeated on each page,
        column widths, text sizes)
    figures, structures and schemes as pictures with their labels and captions as text
    the Spectra section's pages (and PDF pages of attachments and HRMS reports) as
        pictures, laid out exactly as in the PDF, each on a page of its own
    page numbers S1, S2… and the header and footer of Project settings ▸ Page
    the References, numbered in order of first citation

Project settings ▸ Text and tables (core/formatting.py) applies as in the PDF: line and
paragraph spacing, caption sizes, table text sizes, bold header rows.

Word cannot show SVG drawings or PDF pages from Python, so those (structures drawn by the
program, pasted ChemDraw pictures, schemes, spectra) are drawn by Typst (the same engine
as the PDF, so they look exactly the same) into PNG pictures at PICTURE_PPI. PNG, JPEG and
GIF pictures go in as they are. Nothing here uses Qt, except the image_info and converter
the UI layer hands over (as for the PDF); the pictures are drawn and the file is written
in finish_docx, which can run in a worker thread.

Writing the .docx uses python-docx (MIT licence).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import struct
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

import typst
from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph as DocxParagraph

from ..core import formatting, front, headings, ms_reports, page_flow
from ..core import page as page_setup
from ..core import scheme_editor
from ..core.markup import SUB, SUPER, Paragraph, Run, parse
from ..core.project import (GENERAL_OWNER, SPECTRA_SECTION, Compound, Project,
                            SchemePage, Section, TextOwner, figure_uncaptioned,
                            figure_width_cm)
from ..core.tables import Table
from .blocks import CompoundBlock, compound_block
from .document import (_BAD_VALUE, ATTACHMENT_MAX_HEIGHT_IN, CM_PER_IN, HRMS_HEADING,
                       HRMS_PAGE_IN, SPECTRA_HEADING, STRUCTURE_MAX, STYLES_DIR,
                       TEXT_WIDTH_IN, Builder, CompileError, CompileOptions,
                       note_auto_unplaced)
from .reaction_scheme import SchemeWriter
from .text import format_chemistry, is_emphasis_line, leave_alone, link_target

DOCX_NAME = "SI.docx"
PICTURE_PPI = 300         # structures, schemes, figures drawn by Typst
PAGE_PPI = 300            # whole pages: spectra, PDF pages of attachments and HRMS reports
RENDER_SOURCE = "word-pictures.typ"   # the Typst source of those pictures (in build/)
PNG_DIRECT = {".png", ".jpg", ".jpeg", ".gif"}   # put into Word as they are
PAGE_SAFETY_IN = 0.25     # a full-page picture is this much shorter than the text area,
                          # so Word never pushes it onto a page of its own
HEADING_ROOM_IN = 0.6     # room left for a heading above the first spectra page
LISTING_PT = 7            # an HRMS report's text listing
MONO_FONT = "Courier New"


# ---------------------------------------------------------------------- the journal styles

@dataclass(frozen=True)
class Look:
    """What a journal style (styles/*.typ) looks like, for Word. Must match the .typ files."""
    paper: tuple[float, float]        # inches (portrait)
    margins: tuple[float, float]      # inches (left/right, top/bottom)
    font: str
    body: float                       # pt
    headings: tuple[float, ...]       # level 1, 2, 3, deeper (pt)
    compound_title: float
    caption: float
    caption_sep: str                  # after the label: "Figure S1." / "Figure S1:" / "Figure S1"
    table: float
    notes: float
    cover_align: str                  # "center" or "left"
    cover_sizes: tuple[float, float]  # cover heading, title
    cover_small: float                # affiliations and e-mail
    references_title: str
    references_size: float
    citation: str                     # "acs", "rsc" or "angewandte"
    numbering: str | None             # the style's own heading numbers (Typst pattern)
    head_font: str = ""               # headings (and the cover) in another font


A4 = (8.27, 11.69)
LOOKS = {
    "acs": Look((8.5, 11.0), (1.0, 1.0), "Times New Roman", 11, (13, 11.5, 11, 11), 11, 10,
                ".", 10, 9, "center", (16, 14), 10, "References", 10, "acs", None),
    "rsc": Look(A4, (2.2 / CM_PER_IN, 2.5 / CM_PER_IN), "Times New Roman", 10.5,
                (12.5, 11, 10.5, 10.5), 10.5, 9.5, "", 9.5, 8.5, "left", (14, 13), 9.5,
                "Notes and references", 9.5, "rsc", "1.1", head_font="Arial"),
    "wiley": Look(A4, (2.2 / CM_PER_IN, 2.5 / CM_PER_IN), "Times New Roman", 11,
                  (13, 11.5, 11, 11), 11, 10, ":", 10, 9, "center", (15, 14), 10,
                  "References", 10, "angewandte", "1.1."),
}
# Space above and below headings (em of the body text), as the styles' show rules.
HEADING_SPACE = {1: (1.6, 0.9), 2: (1.2, 0.6), 3: (1.1, 0.5)}
DEEPER_SPACE = (1.0, 0.5)
COMPOUND_TITLE_SPACE = (1.4, 0.5)

# Word's paragraph styles made or restyled here.
FIGURE_CAPTION = "SI Figure Caption"
TABLE_CAPTION = "SI Table Caption"
SCHEME_CAPTION = "SI Scheme Caption"
COMPOUND_TITLE = "SI Compound Title"
TABLE_NOTE = "SI Table Note"
PICTURE = "SI Picture"
CAPTION_STYLES = {"si-figure": FIGURE_CAPTION, "table": TABLE_CAPTION,
                  "scheme": SCHEME_CAPTION}


def style_key(project: Project) -> str:
    style = str(project.settings.get("journal_style") or "ACS").strip().lower() or "acs"
    return style if style in LOOKS else "acs"


# ---------------------------------------------------------------------- small helpers

_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")


def xml_text(text: str) -> str:
    """`text` without the characters a Word file cannot hold (control characters)."""
    return _NOT_XML.sub("", text)


def bookmark_name(label: str) -> str:
    """A Word bookmark name for a Typst label (at most 40 letters, digits and _; a leading
    _ hides it from Word's bookmark list)."""
    return "_si" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:20]


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels of a PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def heading_number(counts: list[int], pattern: str | None, mode: str) -> str:
    """The printed number of a heading whose counter is `counts` (e.g. [1, 2]): Typst's
    pattern ("1.1" → "1.2", "1.1." → "1.2."), or the outline (only its own number:
    1. / A. / a. / i. by level)."""
    if mode == "outline":
        n, level = counts[-1], len(counts)
        if level == 1:
            return f"{n}."
        if level == 2:
            return _letters(n).upper() + "."
        if level == 3:
            return _letters(n) + "."
        return page_setup.roman(n).lower() + "."
    if not pattern:
        return ""
    text = ".".join(str(n) for n in counts)
    return text + "." if pattern.endswith(".") else text


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _initials(given: str) -> str:
    """"Gregory R." → "G. R."; "Jean-Luc" → "J.-L."."""
    out = []
    for word in given.replace(".", ". ").split():
        parts = [p for p in word.split("-") if p]
        out.append("-".join(p[0] + "." for p in parts if p[0].isalpha()) or word)
    return " ".join(x for x in out if x)


def _name_parts(author: str) -> tuple[str, str]:
    author = author.strip()
    if "," in author:
        last, given = author.split(",", 1)
        return last.strip(), given.strip()
    parts = author.split()
    if len(parts) < 2:
        return author, ""
    return parts[-1], " ".join(parts[:-1])


def citation_numbers(numbers: list[int]) -> str:
    """"1,2" or "1–3,5": three or more in a row become a range."""
    numbers = sorted(set(numbers))
    out, i = [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f"{numbers[i]}–{numbers[j]}")
        else:
            out += [str(n) for n in numbers[i:j + 1]]
        i = j + 1
    return ",".join(out)


def reference_runs(ref: dict, style: str) -> list[Run]:
    """One reference as text, in the journal's way (ACS, RSC or Angewandte/Wiley):

    ACS:  Fulmer, G. R.; Miller, A. J. M. Title. *Organometallics* **2010**, *29* (9), 2176–2179. DOI: 10.1021/om100106e.
    RSC:  G. R. Fulmer and A. J. M. Miller, *Organometallics*, 2010, **29**, 2176–2179.
    Wiley: G. R. Fulmer, A. J. M. Miller, *Organometallics* **2010**, *29*, 2176–2179.

    A book, thesis, web page or other reference gets its authors, title, publisher, year
    and address in the same way. Made by the program (the PDF uses Typst's CSL styles), so
    small differences from the PDF are possible."""
    from ..core.references import _split_authors, clean
    r = clean(ref)
    names = []
    for a in _split_authors(r["authors"]):
        last, given = _name_parts(a)
        ini = _initials(given)
        names.append(f"{last}, {ini}".strip(", ") if style == "acs" else f"{ini} {last}".strip())
    out: list[Run] = []
    if names:
        if style == "acs":
            who = "; ".join(names)
        elif style == "rsc" and len(names) > 1:
            who = ", ".join(names[:-1]) + " and " + names[-1]
        else:
            who = ", ".join(names)
        out.append(Run(who + (" " if style == "acs" else ", ")))
    article = r["type"] == "article" and r["journal"]
    title = r["title"].strip()
    if title and (style == "acs" or not article):
        italic_title = r["type"] in ("book", "thesis")
        out.append(Run(title.rstrip(".") + (". " if style == "acs" else ", "),
                       italic=italic_title))
    if article:
        journal = r["journal"]
        if style == "acs":
            out.append(Run(journal, italic=True))
            if r["year"]:
                out += [Run(" "), Run(r["year"], bold=True)]
            if r["volume"]:
                out += [Run(", "), Run(r["volume"], italic=True)]
                if r["issue"]:
                    out.append(Run(f" ({r['issue']})"))
        elif style == "rsc":
            out.append(Run(journal, italic=True))
            if r["year"]:
                out.append(Run(f", {r['year']}"))
            if r["volume"]:
                out += [Run(", "), Run(r["volume"], bold=True)]
        else:
            out.append(Run(journal, italic=True))
            if r["year"]:
                out += [Run(" "), Run(r["year"], bold=True)]
            if r["volume"]:
                out += [Run(", "), Run(r["volume"], italic=True)]
        if r["pages"]:
            out.append(Run(f", {r['pages']}"))
    else:
        bits = [x for x in (r["journal"], r["publisher"], r["year"]) if x]
        if bits:
            out.append(Run(", ".join(bits)))
        if r["pages"]:
            out.append(Run(f", {r['pages']}"))
    text = "".join(x.text for x in out).rstrip(" ,;")
    while out and out[-1].text.strip(" ,;") == "":
        out.pop()
    if out:
        last = out[-1]
        out[-1] = last.but(text=last.text.rstrip(" ,;"))
    if text:
        out.append(Run("."))
    if r["doi"]:
        out.append(Run(f" DOI: {r['doi']}." if style == "acs" else f" DOI: {r['doi']}."))
    elif r["url"]:
        out.append(Run(f" {r['url']}"))
    if r["note"]:
        out.append(Run(f" {r['note']}"))
    return [x for x in out if x.text]


# ---------------------------------------------------------------------- XML helpers

# Word reads only files whose elements are in the order its schema gives: what may follow
# an element added here.
_PPR_AFTER_OUTLINE = ("w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
_TCPR_AFTER_BORDERS = ("w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
                       "w:vAlign", "w:hideMark", "w:headers", "w:cellIns", "w:cellDel",
                       "w:cellMerge", "w:tcPrChange")
_SECTPR_AFTER_PGNUM = ("w:cols", "w:formProt", "w:vAlign", "w:noEndnote", "w:titlePg",
                       "w:textDirection", "w:bidi", "w:rtlGutter", "w:docGrid",
                       "w:printerSettings", "w:sectPrChange")
_SETTINGS_AFTER_UPDATE = ("w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat",
                          "w:docVars", "w:rsids", "m:mathPr", "w:attachedSchema",
                          "w:themeFontLang", "w:clrSchemeMapping",
                          "w:doNotIncludeSubdocsInStats", "w:doNotAutoCompressPictures",
                          "w:forceUpgrade", "w:captions", "w:readModeInkLockDown",
                          "w:smartTagType", "sl:schemaLibrary", "w:shapeDefaults",
                          "w:doNotEmbedSmartTags", "w:decimalSymbol", "w:listSeparator")


def _set_fonts(rpr_parent, name: str) -> None:
    """All four font slots of a run or style set to `name` (no theme fonts)."""
    _set_rpr_fonts(rpr_parent.get_or_add_rPr(), name)


def _set_rpr_fonts(rpr, name: str) -> None:
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        style = rpr.find(qn("w:rStyle"))
        rpr.insert(0 if style is None else rpr.index(style) + 1, fonts)
    for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{slot}"), name)
    for theme in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn(f"w:{theme}"), None)


def _outline_level(paragraph_or_style, level: int | None) -> None:
    """Set (0-based level) or remove (None → body text) a paragraph's outline level."""
    ppr = paragraph_or_style._element.get_or_add_pPr()
    old = ppr.find(qn("w:outlineLvl"))
    if old is not None:
        ppr.remove(old)
    el = OxmlElement("w:outlineLvl")
    el.set(qn("w:val"), str(9 if level is None else max(0, min(8, level))))
    ppr.insert_element_before(el, *_PPR_AFTER_OUTLINE)


def _field(paragraph, instruction: str, shown: str = "", font=None) -> None:
    """A Word field (PAGE, NUMPAGES…) in `paragraph`; `shown` is its text until updated."""
    def run_with(child):
        r = paragraph.add_run()
        if font is not None:
            font(r)
        r._r.append(child)
        return r
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run_with(begin)
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run_with(instr)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run_with(sep)
    if shown:
        r = paragraph.add_run(shown)
        if font is not None:
            font(r)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run_with(end)


def _cell_borders(cell, **sides) -> None:
    """Rules on a table cell's sides: top=0.8 (pt) etc."""
    tcpr = cell._tc.get_or_add_tcPr()
    borders = tcpr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcpr.insert_element_before(borders, *_TCPR_AFTER_BORDERS)
    for side in ("top", "left", "bottom", "right"):
        if side not in sides:
            continue
        el = borders.find(qn(f"w:{side}"))
        if el is None:
            el = OxmlElement(f"w:{side}")
            borders.append(el)
        width = sides[side]
        if width:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(max(2, round(width * 8))))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "000000")
        else:
            el.set(qn("w:val"), "nil")
    # (Word wants the sides in this order)
    order = {qn(f"w:{x}"): i for i, x in enumerate(("top", "left", "bottom", "right"))}
    for el in sorted(borders, key=lambda e: order.get(e.tag, 9)):
        borders.append(el)


def _no_table_borders(table) -> None:
    tblpr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    old = tblpr.find(qn("w:tblBorders"))
    if old is not None:
        tblpr.remove(old)
    tblpr.insert_element_before(borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
                                "w:tblCaption", "w:tblDescription", "w:tblPrChange")


def _column_widths(table, widths: list[float | None]) -> None:
    """Each column as wide as given (inches), or as wide as its text needs (None): Word
    and LibreOffice then fit the table to its contents, as Typst does."""
    tbl = table._tbl
    tblpr = tbl.tblPr
    tblpr.get_or_add_tblLayout().set(qn("w:type"), "autofit")
    grid = tbl.tblGrid
    for col, w in zip(grid.findall(qn("w:gridCol")), widths):
        if w is None:
            col.attrib.pop(qn("w:w"), None)
        else:
            col.set(qn("w:w"), str(round(w * 1440)))
    for tr in tbl.findall(qn("w:tr")):
        for c, tc in enumerate(tr.findall(qn("w:tc"))):
            tcpr = tc.get_or_add_tcPr()
            tcw = tcpr.find(qn("w:tcW"))
            if tcw is None:
                tcw = OxmlElement("w:tcW")
                tcpr.insert(0, tcw)
            w = widths[c] if c < len(widths) else None
            if w is None:
                tcw.set(qn("w:type"), "auto")
                tcw.set(qn("w:w"), "0")
            else:
                tcw.set(qn("w:type"), "dxa")
                tcw.set(qn("w:w"), str(round(w * 1440)))


def _row_flag(row, tag: str) -> None:
    trpr = row._tr.get_or_add_trPr()
    if trpr.find(qn(f"w:{tag}")) is None:
        trpr.append(OxmlElement(f"w:{tag}"))


# ---------------------------------------------------------------------- pictures

@dataclass
class _Picture:
    """A picture to put in once all pictures are drawn: into `run`, at `width_in` (None =
    its own size), no taller than `max_height_in`."""
    run: object
    path: Path | None = None       # a PNG/JPEG/GIF put in as it is
    page: str = ""                 # or a Typst page that draws it
    ppi: int = PICTURE_PPI
    width_in: float | None = None
    max_in: tuple[float, float] | None = None
    exact: tuple[float, float] | None = None   # a full page: exactly this size


_IMAGE_PATH = re.compile(r'image\(\s*"((?:[^"\\]|\\.)*)"')


# ---------------------------------------------------------------------- the writer

@dataclass
class WordBuild:
    """A Word SI made by prepare_docx, waiting for its pictures (finish_docx)."""
    writer: "WordWriter"
    warnings: list[str] = field(default_factory=list)


class WordWriter:
    """Writes the SI into a python-docx Document, using a Builder for every decision."""

    def __init__(self, builder: Builder):
        self.b = builder
        self.project = builder.project
        self.options = builder.options
        self.look = LOOKS[style_key(self.project)]
        self.fmt = formatting.formatting(self.project.settings)
        self.table_defaults = formatting.table_defaults(self.project.settings)
        self.doc = Document()
        self.pictures: list[_Picture] = []
        self.cited: list[str] = []            # reference IDs in order of first citation
        self.counts: list[int] = []           # heading counter (numbered headings only)
        self.numbering_mode = headings.heading_numbers(self.project.settings)
        self.toc_entries: list[tuple[int, str, str]] = []   # (level, text, bookmark)
        self.caption_entries: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        self._toc_marks = 0
        self.bookmarks: set[str] = set()
        self.hyperlinks: list[tuple[object, str]] = []   # (w:hyperlink element, bookmark)
        self._bookmark_id = 0
        self.toc_anchor = None   # the paragraph the table of contents goes before
        self.portrait = True
        self._setup()

    # ------------------------------------------------------------------ document setup

    @property
    def text_width_in(self) -> float:
        return (self.look.paper[1] if not self.portrait else self.look.paper[0]) \
            - 2 * self.look.margins[0]

    @property
    def text_height_in(self) -> float:
        return (self.look.paper[0] if not self.portrait else self.look.paper[1]) \
            - 2 * self.look.margins[1]

    def _setup(self) -> None:
        look, doc = self.look, self.doc
        section = doc.sections[0]
        self._page(section, portrait=True)
        styles = doc.styles
        normal = styles["Normal"]
        _set_fonts(normal.element, look.font)
        normal.font.size = Pt(look.body)
        pf = normal.paragraph_format
        spacing = self.fmt["line_spacing"] or 1.0
        pf.line_spacing = spacing
        half = look.body / 2   # the styles leave 1 em between paragraphs (half each side)
        pf.space_before = Pt(self.fmt["space_before"] if self.fmt["space_before"] is not None
                             else half)
        pf.space_after = Pt(self.fmt["space_after"] if self.fmt["space_after"] is not None
                            else half)
        pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        # Page flow (core/page_flow.py): Word's widow/orphan control has no "strong" level.
        pf.widow_control = self.b.flow.widows != "off"
        # (the document defaults too: a style Word adds itself starts from these)
        defaults = styles.element.find(qn("w:docDefaults"))
        rpr = defaults.find(qn("w:rPrDefault") + "/" + qn("w:rPr")) \
            if defaults is not None else None
        if rpr is not None:
            _set_rpr_fonts(rpr, look.font)
        head_font = look.head_font or look.font
        for level in range(1, 10):
            st = styles[f"Heading {level}"]
            self._heading_look(st, level, head_font)
            plain = self._style(f"SI Heading {level} (not listed)")
            self._heading_look(plain, level, head_font)
            _outline_level(plain, None)
        title = self._style(COMPOUND_TITLE)
        _set_fonts(title.element, look.font)
        title.font.size = Pt(look.compound_title)
        title.font.bold = True
        self._space(title, COMPOUND_TITLE_SPACE)
        title.paragraph_format.keep_with_next = True
        title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        caption_size = self.fmt["caption_size"] or look.caption
        for name in (FIGURE_CAPTION, TABLE_CAPTION, SCHEME_CAPTION):
            st = self._style(name)
            st.font.size = Pt(caption_size)
            st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            st.paragraph_format.space_before = Pt(look.body * 0.3)
            st.paragraph_format.space_after = Pt(look.body * 0.8)
        doc.styles[TABLE_CAPTION].paragraph_format.keep_with_next = True
        note = self._style(TABLE_NOTE)
        note.font.size = Pt(self.table_defaults["notes_size"] or look.notes)
        note.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        note.paragraph_format.space_before = Pt(0)
        note.paragraph_format.space_after = Pt(0)
        pic = self._style(PICTURE)
        pic.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic.paragraph_format.space_before = Pt(look.body * 0.4)
        pic.paragraph_format.space_after = Pt(look.body * 0.4)
        pic.paragraph_format.line_spacing = 1.0
        # Word fills in the table of contents (and page numbers) when the file is opened.
        settings = doc.settings.element
        update = OxmlElement("w:updateFields")
        update.set(qn("w:val"), "true")
        settings.insert_element_before(update, *_SETTINGS_AFTER_UPDATE)
        core = doc.core_properties
        core.title = str(self.project.settings.get("title") or "")
        core.author = page_setup.author_names(self.project.settings, "full")
        from .. import APP_NAME, __version__
        core.comments = f"Made with {APP_NAME} {__version__}"
        core.last_modified_by = APP_NAME

    def _style(self, name: str):
        styles = self.doc.styles
        try:
            return styles[name]
        except KeyError:
            st = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            st.base_style = styles["Normal"]
            st.quick_style = True
            return st

    def _space(self, style, em: tuple[float, float]) -> None:
        style.paragraph_format.space_before = Pt(round(em[0] * self.look.body, 1))
        style.paragraph_format.space_after = Pt(round(em[1] * self.look.body, 1))

    def _heading_look(self, st, level: int, font: str) -> None:
        look = self.look
        size = look.headings[min(level, len(look.headings)) - 1]
        _set_fonts(st.element, font)
        st.font.size = Pt(size)
        st.font.bold = level <= 3
        st.font.italic = level >= 3
        st.font.color.rgb = RGBColor(0, 0, 0)
        self._space(st, HEADING_SPACE.get(level, DEEPER_SPACE))
        pf = st.paragraph_format
        pf.keep_with_next = True
        pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pf.line_spacing = 1.0

    def _page(self, section, portrait: bool) -> None:
        w, h = self.look.paper
        mx, my = self.look.margins
        section.orientation = WD_ORIENT.PORTRAIT if portrait else WD_ORIENT.LANDSCAPE
        section.page_width = Inches(w if portrait else h)
        section.page_height = Inches(h if portrait else w)
        section.left_margin = section.right_margin = Inches(mx)
        section.top_margin = section.bottom_margin = Inches(my)
        section.header_distance = section.footer_distance = Inches(min(0.5, my / 2))

    def new_section(self, portrait: bool) -> None:
        """A new page in Word section of its own (for landscape spectra pages)."""
        section = self.doc.add_section(WD_SECTION.NEW_PAGE)
        section.different_first_page_header_footer = False
        self.portrait = portrait
        self._page(section, portrait)

    @staticmethod
    def _starts_page(last) -> bool:
        """Whether what follows body element `last` is at the top of a page anyway (None =
        the document is still empty)."""
        if last is None:
            return True
        if last.tag == qn("w:p"):
            br = last.find(".//" + qn("w:br"))
            if br is not None and br.get(qn("w:type")) == "page" and \
                    not "".join(t.text or "" for t in last.iter(qn("w:t"))):
                return True   # (already a page break: "weak", as in Typst)
            if last.find(qn("w:pPr")) is not None and \
                    last.find(qn("w:pPr")).find(qn("w:sectPr")) is not None:
                return True   # (a section break just started a new page)
        return False

    def page_break(self) -> None:
        """Start a new page (unless the document is still empty)."""
        body = self.doc.element.body
        if self._starts_page(body[-2] if len(body) > 1 else None):   # (last: the sectPr)
            return
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(0)
        p.add_run().add_break(WD_BREAK.PAGE)

    def body_mark(self) -> int:
        """Where the next thing written goes among the body's elements (for with_page_flow)."""
        return len(self.doc.element.body) - 1   # (the last element is the sectPr)

    def with_page_flow(self, start: int, data: dict, keep_default: bool) -> None:
        """A compound's or section's own page flow (Builder.with_page_flow), applied to
        what was written since body_mark() gave `start`: kept on one page (Word's "keep
        with next" and "keep lines together"; a table's rows don't split) and/or on a new
        page ("page break before")."""
        body = self.doc.element.body
        items = list(body)[start:-1]
        if not items:
            return
        breaks = any(br.get(qn("w:type")) == "page" for el in items
                     for br in el.iter(qn("w:br"))) or \
            any(True for el in items for _ in el.iter(qn("w:sectPr")))
        if page_flow.keeps_together(data, keep_default) and not breaks:
            for n, el in enumerate(items):
                last = n == len(items) - 1
                if el.tag == qn("w:tbl"):
                    for tr in el.iter(qn("w:tr")):
                        trpr = tr.get_or_add_trPr()
                        if trpr.find(qn("w:cantSplit")) is None:
                            trpr.append(OxmlElement("w:cantSplit"))
                    paragraphs = [] if last else list(el.iter(qn("w:p")))
                else:
                    paragraphs = [el] if el.tag == qn("w:p") else []
                for q in paragraphs:
                    pf = DocxParagraph(q, self.doc._body).paragraph_format
                    pf.keep_together = True
                    if not (last and q is el):
                        pf.keep_with_next = True
        if page_flow.item_flow(data)["new_page"]:
            before = body[start - 1] if start > 0 else None
            if not self._starts_page(before):
                first = items[0]
                if first.tag == qn("w:p"):
                    DocxParagraph(first, self.doc._body).paragraph_format.page_break_before = True
                else:   # (a table first: a page break paragraph before it)
                    p = OxmlElement("w:p")
                    first.addprevious(p)
                    para = DocxParagraph(p, self.doc._body)
                    para.paragraph_format.space_before = Pt(0)
                    para.paragraph_format.space_after = Pt(0)
                    para.add_run().add_break(WD_BREAK.PAGE)

    def keep_emphasis(self, paragraph, runs: list[Run], has_next: bool) -> None:
        """Page flow: a short line wholly in bold or italic (a heading typed as text) is
        kept with what follows, like a heading (paragraphs_to_typst's keep_lines)."""
        if has_next and self.b.flow.emphasis_lines and is_emphasis_line(runs):
            paragraph.paragraph_format.keep_with_next = True

    # ------------------------------------------------------------------ text

    def add_runs(self, paragraph, runs: list[Run], size: float | None = None,
                 bold: bool = False, font: str = "") -> None:
        """Runs into a Word paragraph: bold, italic, underline, super/subscript; citations
        as numbers; runs linked to a place in the SI as a link to its bookmark."""
        group: list[Run] = []
        target = ""

        def flush():
            nonlocal group
            if not group:
                return
            link = OxmlElement("w:hyperlink")
            name = bookmark_name(target)
            link.set(qn("w:anchor"), name)
            link.set(qn("w:history"), "1")
            for r in group:
                for el in self._word_runs(paragraph, r, size, bold, font):
                    link.append(el)
            paragraph._p.append(link)
            self.hyperlinks.append((link, name))
            group = []

        for r in runs:
            t = link_target(r)
            if t != target:
                flush()
                target = t
            if t:
                group.append(r)
                continue
            self._word_runs(paragraph, r, size, bold, font)
        flush()

    def _word_runs(self, paragraph, r: Run, size, bold, font) -> list:
        """One Run as Word run(s), added at the end of `paragraph`; returns their elements."""
        if r.ref:
            ids = r.citations
            if not ids:
                return []
            numbers = []
            for c in ids:
                if c not in self.cited:
                    self.cited.append(c)
                numbers.append(self.cited.index(c) + 1)
            text = citation_numbers(numbers)
            if self.look.citation == "angewandte":
                text = f"[{text}]"
            run = paragraph.add_run(text)
            run.font.superscript = True
            if size:
                run.font.size = Pt(size)
            return [run._r]
        text = xml_text(r.text)
        if not text:
            return []
        out = []
        # (a combining overline, as in P1̄, stays as it is: Word draws it)
        run = paragraph.add_run(text)
        run.bold = bool(r.bold or bold) or None
        run.italic = r.italic or None
        run.underline = r.underline or None
        if r.valign == SUPER:
            run.font.superscript = True
        elif r.valign == SUB:
            run.font.subscript = True
        if size:
            run.font.size = Pt(size)
        if font:
            _set_fonts(run._r, font)
        out.append(run._r)
        return out

    def paragraph(self, runs: list[Run], align: str = "", style: str | None = None,
                  size: float | None = None, bold: bool = False):
        p = self.doc.add_paragraph(style=style)
        if align:
            p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                           "right": WD_ALIGN_PARAGRAPH.RIGHT,
                           "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}.get(align)
        self.add_runs(p, runs, size, bold)
        return p

    def bookmark(self, paragraph, label: str) -> None:
        """A bookmark (Word's link target) for a Typst label, at the start of `paragraph`;
        each only once."""
        name = bookmark_name(label)
        if name in self.bookmarks:
            return
        self.bookmarks.add(name)
        self._add_bookmark(paragraph, name)

    def toc_mark(self, paragraph) -> str:
        """A bookmark on a heading or caption, for its page number in the table of contents."""
        self._toc_marks += 1
        name = f"_Toc{self._toc_marks:08d}"
        self._add_bookmark(paragraph, name)
        return name

    def _add_bookmark(self, paragraph, name: str) -> None:
        self._bookmark_id += 1
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(self._bookmark_id))
        start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(self._bookmark_id))
        ppr = paragraph._p.find(qn("w:pPr"))
        at = 1 if ppr is not None else 0
        paragraph._p.insert(at, start)
        paragraph._p.insert(at + 1, end)

    def unlink_missing(self) -> None:
        """A link whose target is not in the document (a spectrum not printed) becomes
        plain text, as si-link does in the PDF."""
        for link, name in self.hyperlinks:
            if name in self.bookmarks:
                continue
            parent = link.getparent()
            if parent is None:
                continue
            at = parent.index(link)
            for child in list(link):
                parent.insert(at, child)
                at += 1
            parent.remove(link)

    # ------------------------------------------------------------------ headings

    def heading(self, level: int, runs: list[Run], outlined: bool = True,
                numbered: bool = True, align: str = "") -> None:
        """A heading, as Builder.heading: numbered as the SI's headings are; in the table
        of contents (Word's Heading N style) only when `outlined`."""
        number = ""
        mode = self.numbering_mode
        pattern = {"style": self.look.numbering, "decimal": "1.1.", "none": None,
                   "outline": "outline"}.get(mode)
        if numbered and pattern:
            if len(self.counts) >= level:
                self.counts[level - 1] += 1
                del self.counts[level:]
            else:
                while len(self.counts) < level:
                    self.counts.append(1)
            number = heading_number(self.counts, pattern, mode)
        level = max(1, min(9, level))
        style = f"Heading {level}" if outlined else f"SI Heading {level} (not listed)"
        p = self.doc.add_paragraph(style=style)
        if align in ("center", "right"):
            p.alignment = (WD_ALIGN_PARAGRAPH.CENTER if align == "center"
                           else WD_ALIGN_PARAGRAPH.RIGHT)
        if number:
            p.add_run(number + " ")
        self.add_runs(p, runs)
        if outlined:
            text = (number + " " if number else "") + "".join(r.text for r in runs)
            self.toc_entries.append((level, xml_text(text), self.toc_mark(p)))
        return p

    def section_heading(self, level: int, runs: list[Run]):
        return self.heading(level, runs, outlined=level == 1 or self.b.toc_rank >= 2)

    # ------------------------------------------------------------------ pictures

    def picture_paragraph(self, align: str = "center", keep_with_next: bool = False):
        p = self.doc.add_paragraph(style=PICTURE)
        p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,
                       "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(align, WD_ALIGN_PARAGRAPH.CENTER)
        p.paragraph_format.keep_with_next = keep_with_next
        return p

    def typst_picture(self, run, code: str, max_in: tuple[float, float] | None = None,
                      ppi: int = PICTURE_PPI) -> None:
        """A picture Typst draws from `code` (an image(...) call or other content), at its
        own size (no bigger than `max_in`)."""
        body = code if code.startswith("#") else "#" + code
        self.pictures.append(_Picture(
            run, page=f"#page(width: auto, height: auto, margin: 0pt)[{body}]", ppi=ppi,
            max_in=max_in or (self.text_width_in, self.text_height_in)))

    def image_code_picture(self, run, img: tuple[str, float, float]) -> None:
        """A picture from Builder.image's result (code, width, height): a PNG, JPEG or GIF
        file goes in as it is; anything else is drawn by Typst."""
        code, w, h = img
        m = _IMAGE_PATH.search(code)
        if m and "page:" not in code:
            rel = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            path = self.b.root / rel.lstrip("/")
            if path.suffix.lower() in PNG_DIRECT and path.exists():
                self.pictures.append(_Picture(run, path=path, width_in=w,
                                              max_in=(self.text_width_in, 99)))
                return
        self.pictures.append(_Picture(
            run, page=f"#page(width: auto, height: auto, margin: 0pt)[#{code}]",
            width_in=w, max_in=(self.text_width_in, 99)))

    def structure(self, compound: Compound, max_in=STRUCTURE_MAX, keep: bool = True) -> bool:
        code = self.b.structure_image(compound, max_in)
        if not code:
            return False
        # (on the left, as in the PDF: the style's si-structure block is only as wide
        # as the drawing, so its align(center) has no effect)
        p = self.picture_paragraph("left", keep_with_next=keep)
        self.typst_picture(p.add_run(), code, max_in)
        return True

    def pdf_page_picture(self, path: Path, page: int, max_h: float, align: str = "center"):
        """One PDF page, as wide as the text (at most `max_h` inches high)."""
        usable = self.b.usable_image(path)
        if usable is None:
            return None
        aspect = self.b.page_aspect(usable, page) or (11 / 8.5)
        width = min(self.text_width_in, max_h / aspect)
        p = self.picture_paragraph(align)
        rel = self.b.rel(usable).replace("\\", "\\\\").replace('"', '\\"')
        self.pictures.append(_Picture(
            p.add_run(), page=(f'#page(width: auto, height: auto, margin: 0pt)'
                               f'[#image("{rel}", page: {page}, width: {width:.3f}in)]'),
            ppi=PAGE_PPI, width_in=width, max_in=(self.text_width_in, max_h)))
        return p

    def caption(self, kind: str, label: str, caption: list[Run], align: str = "",
                size: float | None = None) -> None:
        """"Figure S1. Caption" (the label in bold) under a figure or scheme, above a table."""
        style = CAPTION_STYLES[kind]
        p = self.doc.add_paragraph(style=style)
        if align in ("left", "right"):
            p.alignment = (WD_ALIGN_PARAGRAPH.LEFT if align == "left"
                           else WD_ALIGN_PARAGRAPH.RIGHT)
        size = formatting.clean_size(size)
        p.add_run(xml_text(label + self.look.caption_sep)).bold = True
        if size:
            p.runs[-1].font.size = Pt(size)
        text = "".join(r.text for r in caption).strip()
        if text:
            r = p.add_run(" ")
            if size:
                r.font.size = Pt(size)
            self.add_runs(p, caption, size)
        self.caption_entries[kind].append(
            (1, xml_text(label + self.look.caption_sep + (" " + text if text else "")),
             self.toc_mark(p)))

    def figure(self, img, caption: list[Run], label: str, align: str = "",
               caption_size: float | None = None) -> None:
        """A picture with its label and caption under it (Builder.inline_figure)."""
        p = self.picture_paragraph(align, keep_with_next=True)
        self.image_code_picture(p.add_run(), img)
        self.caption("si-figure", label or "Figure", caption, align, caption_size)

    # ------------------------------------------------------------------ front matter

    def cover(self) -> None:
        s = self.project.settings
        look = self.look
        align = "center" if look.cover_align == "center" else "left"
        font = look.head_font
        spacing = front.cover_spacing(s)
        body = look.body

        def line(runs, size=None, bold=False, space_after=0.0, fnt=""):
            p = self.doc.add_paragraph()
            p.alignment = (WD_ALIGN_PARAGRAPH.CENTER if align == "center"
                           else WD_ALIGN_PARAGRAPH.LEFT)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(space_after * body * spacing)
            self.add_runs(p, runs, size, bold, fnt)
            return p

        line([Run(front.cover_heading(s))], look.cover_sizes[0], True, 1.0, font)
        line([Run(str(s.get("title", "") or ""))], look.cover_sizes[1], True, 1.0, font)
        runs: list[Run] = []
        authors = front.authors(s)
        for i, a in enumerate(authors):
            if i:
                runs.append(Run(" and " if i == len(authors) - 1 and len(authors) == 2
                                else (", and " if i == len(authors) - 1 else ", ")))
            runs.append(Run(str(a.get("name", "") or "")))
            marks = ",".join(a["affiliations"]) + ("*" if a.get("corresponding") else "")
            if marks:
                runs.append(Run(marks, valign=SUPER))
        if runs:
            line(runs, space_after=0.6)
        affs = front.affiliations(s)
        for i, a in enumerate(affs):
            line([Run(str(a.get("marker", "") or ""), valign=SUPER),
                  Run(str(a.get("text", "") or ""))], look.cover_small,
                 space_after=0.4 if i == len(affs) - 1 else 0)
        emails = [str(a.get("email", "")) for a in authors if a.get("corresponding")
                  and a.get("email")]
        if emails:
            line([Run("*Email: " + "; ".join(emails))], look.cover_small)
        extra = self.project.read_front_text("cover")
        if extra.strip():
            self.doc.add_paragraph().paragraph_format.space_after = Pt(0.6 * body * spacing)
            self.rich_text(parse(extra), "Cover page", self.project.front_folder,
                           outlined_levels=0)

    def table_of_contents_anchor(self) -> None:
        """Where the table of contents goes; it is written last (write_toc)."""
        self.toc_anchor = self.doc.add_paragraph()

    def write_toc(self) -> None:
        """The table of contents (and lists of figures, tables, schemes), before the anchor:
        made last, so it knows every heading and caption."""
        if self.toc_anchor is None:
            return
        t = self.b.toc
        anchor = self.toc_anchor
        width = self.look.paper[0] - 2 * self.look.margins[0]
        levels = max([e[0] for e in self.toc_entries] or [1])
        for level in range(1, max(levels, 3) + 1):
            st = self._style(f"toc {level}")
            st.paragraph_format.left_indent = Inches(0.25 * (level - 1))
            st.paragraph_format.space_before = Pt(2 if level > 1 else 6)
            st.paragraph_format.space_after = Pt(0)
            st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
            st.font.bold = level == 1 or None
            if t["font_size"] > 0:
                st.font.size = Pt(t["font_size"])
            tabs = st.paragraph_format.tab_stops
            tabs.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT,
                              WD_TAB_LEADER.DOTS if t["dots"] else WD_TAB_LEADER.SPACES)

        prefix = page_setup.page_settings(self.project.settings)["number_prefix"]

        def block(title: str, entries: list[tuple[int, str, str]]) -> None:
            if title.strip():
                p = anchor.insert_paragraph_before(style="TOC Heading")
                p.add_run(xml_text(title))
                _set_fonts(p.style.element, self.look.head_font or self.look.font)
                p.style.font.color.rgb = RGBColor(0, 0, 0)
                p.style.font.size = Pt(self.look.headings[0])
                p.style.font.bold = True
            # Written out entry by entry (not as Word's TOC field, which can't print the
            # "S" before the page numbers): each entry links to its heading, and its page
            # number is a field Word fills in when the file is opened (or with F9).
            for level, text, mark in entries:
                p = anchor.insert_paragraph_before(style=f"toc {min(level, 9)}")
                link = OxmlElement("w:hyperlink")
                link.set(qn("w:anchor"), mark)
                link.set(qn("w:history"), "1")
                link.append(p.add_run(text)._r)
                p._p.append(link)
                p.add_run("\t" + xml_text(prefix))
                _field(p, f"PAGEREF {mark} \\h", "")

        block(t["title"], self.toc_entries)
        for key, (list_title, kind) in front.TOC_LISTS.items():
            if not t[key]:
                continue
            entries = self.caption_entries[kind]
            if not entries:
                self.b.warnings.append(f"The table of contents has no {list_title.lower()}: "
                                       f"the SI has no {key} yet.")
                continue
            block(list_title, entries)

    def general_information(self, level: int = 1) -> bool:
        text = self.project.read_front_text("general")
        if not text.strip():
            return False
        s = self.project.settings
        title = front.general_heading(s).strip()
        outline = headings.numbers_text_headings(s)
        numbered = front.general_numbered(s) or outline
        if title:
            self.heading(level, [Run(title)], numbered=numbered)
        self.rich_text(parse(text), title or "General Information", self.project.front_folder,
                       heading_offset=level,
                       outlined_levels=level + 1 if self.b.toc_rank >= 2 else 0,
                       owner=self.project.text_owner(GENERAL_OWNER),
                       numbered_headings=outline)
        return True

    # ------------------------------------------------------------------ texts

    def rich_text(self, paragraphs: list[Paragraph], where: str, folder: Path | None = None,
                  heading_offset: int = 1, outlined_levels: int = 2,
                  owner: TextOwner | None = None, numbered_headings: bool = False) -> None:
        """Stored text → Word (Builder.rich_text): links resolved, formatting helpers,
        figures and tables."""
        b = self.b
        paragraphs = b.link_paragraphs(paragraphs, where)
        if self.options.format_text:
            paragraphs = [replace(p, runs=format_chemistry(p.runs)) for p in paragraphs]
        index = 0
        for i, p in enumerate(paragraphs):
            if not p.is_object:
                if not any(r.text or r.ref for r in p.runs):
                    continue
                if p.heading:
                    level = p.heading + heading_offset
                    self.heading(level, p.runs, outlined=level <= outlined_levels,
                                 numbered=numbered_headings, align=p.stored_align)
                else:
                    has_next = i + 1 < len(paragraphs) and not paragraphs[i + 1].is_object
                    self.keep_emphasis(self.paragraph(p.runs, p.stored_align), p.runs,
                                       has_next)
                continue
            item = b.labels.at(owner.key, index) if owner is not None else None
            label = item.label if item is not None else ""
            index += 1
            if p.table:
                self.table(owner, p.table, label, p.runs, where, p.stored_align)
                continue
            path, width_cm, bare = None, None, False
            if p.figure and owner is not None:
                path = self.project.figure_path(owner, p.figure)
                entry = self.project.figures(owner).get(p.figure, {})
                width_cm = figure_width_cm(entry)
                bare = figure_uncaptioned(entry)
                if path is None:
                    b.warnings.append(f"{where}: a figure's picture is no longer in the "
                                      "project; left out.")
            elif p.image and folder is not None:
                path = folder / p.image
            if path is None:
                img = None
            elif width_cm:
                img = b.image(path, (round(min(TEXT_WIDTH_IN, width_cm / CM_PER_IN), 3), 9.0),
                              natural=False)
            else:
                img = b.image(path, (TEXT_WIDTH_IN, 7.0))
            if img and bare:   # just the picture: no number, no caption (issue #3)
                where_to = p.stored_align if p.stored_align in ("left", "right") else "center"
                self.image_code_picture(self.picture_paragraph(where_to).add_run(), img)
            elif img:
                size = (self.project.figures(owner).get(p.figure, {}).get("caption_size")
                        if p.figure and owner is not None else None)
                self.figure(img, p.runs, label, p.stored_align, size)

    # ------------------------------------------------------------------ tables

    def table(self, owner: TextOwner | None, table_id: str, label: str, caption: list[Run],
              where: str, align: str = "") -> None:
        table = self.project.read_table(owner, table_id) if owner is not None else None
        if table is None or not table.n_rows or not table.n_cols:
            self.b.warnings.append(f"{where}: a table could not be found or is empty "
                                   f"(tables/{table_id}.json); left out.")
            return
        self.table_figure(table, label, caption, where, owner.folder, align)

    def cell_paragraphs(self, text: str, where: str) -> list[list[Run]]:
        out = []
        for p in self.b.link_paragraphs(parse(text), where):
            runs = format_chemistry(p.runs) if self.options.format_text else p.runs
            if any(r.text or r.ref for r in runs):
                out.append(runs)
        return out

    def table_figure(self, table: Table, label: str, caption: list[Run], where: str,
                     folder: Path | None = None, align: str = "") -> None:
        """A table (Builder.table_figure): label and caption above, the table with rules
        above and below the header rows and at the bottom, footnotes below."""
        self.caption("table", label or "Table", caption, align, table.caption_size)
        t = table.copy()
        header = t.header_rows
        for r, _c, cell in t.cells():
            if r < header:
                cell.rowspan = min(cell.rowspan, header - r)
        t.normalize()
        d = self.table_defaults
        size_all = d["size"] or self.look.table
        notes = [n for n in (self.cell_paragraphs(x, where) for x in t.footnotes) if n]
        # (the footnotes are a last row of their own, across the table: they start at its
        # left edge and stay with it, as in the PDF)
        word = self.doc.add_table(rows=t.n_rows + (1 if notes else 0), cols=t.n_cols)
        _no_table_borders(word)
        word.alignment = {"left": WD_TABLE_ALIGNMENT.LEFT,
                          "right": WD_TABLE_ALIGNMENT.RIGHT}.get(align, WD_TABLE_ALIGNMENT.CENTER)
        widths = [w for w in t.widths] + [None] * (t.n_cols - len(t.widths))
        total = sum((w or 2.5) for w in widths) / CM_PER_IN
        shrink = (min(1.0, self.text_width_in / total)
                  if t.fit_width and total > 0 else 1.0)
        wanted = [None if w is None else w / CM_PER_IN * shrink for w in widths]
        _column_widths(word, self.estimate_widths(t, wanted, size_all, folder, where))
        aligns = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                  "right": WD_ALIGN_PARAGRAPH.RIGHT}
        last = t.n_rows - 1
        for r, c, cell in t.cells():
            target = word.cell(r, c)
            if cell.rowspan > 1 or cell.colspan > 1:
                target = target.merge(word.cell(min(last, r + cell.rowspan - 1),
                                                min(t.n_cols - 1, c + cell.colspan - 1)))
            target.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            in_header = r < header
            size = cell.size if cell.size is not None else (
                d["header_size"] if in_header and d["header_size"] is not None else size_all)
            bold = in_header and d["header_bold"]
            paragraph = target.paragraphs[0]
            first = True
            col_align = t.columns[c] if c < len(t.columns) else "center"
            alignment = aligns.get(cell.align or col_align, WD_ALIGN_PARAGRAPH.CENTER)

            def next_paragraph():
                nonlocal first, paragraph
                if not first:
                    paragraph = target.add_paragraph()
                first = False
                pf = paragraph.paragraph_format
                pf.alignment = alignment
                pf.space_before = Pt(1.5)
                pf.space_after = Pt(1.5)
                pf.line_spacing = 1.0
                pf.keep_with_next = not t.breakable and r < last
                return paragraph

            if cell.image:
                self.cell_picture(next_paragraph(), cell, folder, where)
            for runs in self.cell_paragraphs(cell.text, where):
                self.add_runs(next_paragraph(), runs, size, bold)
            if first:
                next_paragraph()
        # Rules: above the table, under the header rows, under the table.
        for c in range(t.n_cols):
            _cell_borders(word.cell(0, c), top=0.8)
            if header and header <= last:
                _cell_borders(word.cell(header - 1, c), bottom=0.5)
            _cell_borders(word.cell(last, c), bottom=0.8)
        for r, row in enumerate(word.rows):
            if r < header:
                _row_flag(row, "tblHeader")   # repeated at the top of each page
            _row_flag(row, "cantSplit")
        if notes:
            cell = word.cell(last + 1, 0)
            if t.n_cols > 1:
                cell = cell.merge(word.cell(last + 1, t.n_cols - 1))
            size = t.notes_size
            for i, lines in enumerate(notes):
                p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
                p.style = self.doc.styles[TABLE_NOTE]
                p.paragraph_format.space_before = Pt(3 if i == 0 else 0)
                for k, runs in enumerate(lines):
                    if k:
                        p.add_run().add_break()
                    self.add_runs(p, runs, size)
            if not t.breakable:   # (kept with the table)
                for c in range(t.n_cols):
                    for q in word.cell(last, c).paragraphs:
                        q.paragraph_format.keep_with_next = True
        # (space after the table, like the figure's)
        spacer = self.doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(0)
        spacer.paragraph_format.space_after = Pt(self.look.body * 0.4)
        spacer.paragraph_format.line_spacing = 0.5

    def estimate_widths(self, t: Table, wanted: list[float | None], size: float,
                        folder: Path | None, where: str) -> list[float]:
        """Column widths (inches): those typed in the table editor, the others as wide as
        their longest line needs (a guess from the number of characters; Word refits
        them when it opens the file, LibreOffice uses them as they are), all shrunk to the
        text width when wider."""
        need = [0.0] * t.n_cols
        for _r, c, cell in t.cells():
            if cell.colspan > 1 or c >= t.n_cols:
                continue
            em = (cell.size or size) / 72
            longest = 0.0
            for runs in self.cell_paragraphs(cell.text, where):
                longest = max(longest, sum(len(r.text) * (0.4 if r.valign != "normal"
                                                          else 0.55) for r in runs))
            width = longest * em
            if cell.image and cell.image_width:
                width = max(width, cell.image_width / CM_PER_IN)
            elif cell.image:
                width = max(width, 1.5)
            need[c] = max(need[c], width)
        out = [w if w is not None else need[c] + 0.25 for c, w in enumerate(wanted)]
        total = sum(out)
        if total > self.text_width_in:
            out = [w * self.text_width_in / total for w in out]
        return out

    def cell_picture(self, paragraph, cell, folder: Path | None, where: str) -> None:
        path = None
        if folder is not None:
            path = (folder / cell.image).resolve()
            try:
                path.relative_to(folder.resolve())
            except ValueError:
                path = None
        if path is None or not path.exists():
            self.b.warnings.append(f"{where}: a picture in a table cell is no longer in the "
                                   f"project ({cell.image}); left out.")
            return
        if cell.image_width:
            width = round(min(TEXT_WIDTH_IN, cell.image_width / CM_PER_IN), 3)
            img = self.b.image(path, (width, 9.0), natural=False)
        else:
            img = self.b.image(path, (TEXT_WIDTH_IN, 7.0))
        if img:
            self.image_code_picture(paragraph.add_run(), img)

    # ------------------------------------------------------------------ sections

    def text_section_body(self, section: Section, level: int) -> None:
        self.rich_text(parse(self.project.read_section_text(section)), section.name,
                       section.folder, heading_offset=level,
                       outlined_levels=level + 1 if self.b.toc_rank >= 2 else 0,
                       owner=self.project.text_owner(section.id),
                       numbered_headings=headings.numbers_text_headings(self.project.settings))

    def file_section(self, section: Section, level: int) -> None:
        b = self.b
        heading = str(section.data.get("heading", "")).strip()
        if heading:
            self.section_heading(level, [Run(heading)])
        path = self.project.section_file_path(section)
        if path is None:
            b.warnings.append(f"Attachment section “{section.name}” has no file attached.")
            return
        caption = str(section.data.get("caption", "")).strip()
        if path.suffix.lower() == ".pdf":
            for i, page in enumerate(b.pdf_pages(path)):
                if i:
                    self.page_break()
                self.pdf_page_picture(path, page, ATTACHMENT_MAX_HEIGHT_IN)
        else:
            img = b.image(path, (TEXT_WIDTH_IN, ATTACHMENT_MAX_HEIGHT_IN))
            if img:
                p = self.picture_paragraph()
                self.image_code_picture(p.add_run(), img)
        if caption:
            size = self.fmt["caption_size"] or 10
            p = self.paragraph([Run(caption)], "center", size=size)

    def compound(self, compound: Compound, show_structure: bool = True, level: int = 2) -> None:
        """A compound block (Builder.compound)."""
        b = self.b
        display_id = b.report.display_id(compound.id)
        if b.structure_readable(compound):
            b._note_generic(compound)
        try:
            block: CompoundBlock = compound_block(self.project, compound, display_id,
                                                  self.options.format_text,
                                                  b.nmr_link(compound))
        except _BAD_VALUE as e:
            raise b.bad_value(compound, e) from e
        start = self.body_mark()
        p = self.doc.add_paragraph(style=COMPOUND_TITLE)
        self.add_runs(p, block.title)
        if b.toc_rank >= 4:
            _outline_level(p, level - 1)
            self.toc_entries.append((level, xml_text("".join(r.text for r in block.title)),
                                     self.toc_mark(p)))
        self.bookmark(p, b.compound_label(compound))
        if show_structure:
            self.structure(compound)
        where = b.compound_notes(compound, block, display_id)
        paras = b.link_paragraphs(block.paragraphs, where)
        for i, para in enumerate(paras):
            if not any(r.text or r.ref for r in para.runs):
                continue
            if para.heading:
                self.heading(level + para.heading, para.runs, outlined=False, numbered=False)
            else:
                self.keep_emphasis(self.paragraph(para.runs), para.runs, i + 1 < len(paras))
        for index, fig in enumerate(block.figures):
            img = b.image(fig.path, (TEXT_WIDTH_IN, 4.0))
            item = b.labels.at(compound.id, index)
            if img:
                caption = [Run(fig.caption)]
                if self.options.format_text:
                    caption = format_chemistry(caption)
                self.figure(img, caption, item.label if item else "")
        self.with_page_flow(start, compound.data, b.flow.keep_compounds)

    def scheme(self, scheme: SchemePage, level: int = 2) -> None:
        writer = SchemeWriter(self.b, scheme)
        code = writer.code(labelled=False)
        if code:
            p = self.picture_paragraph(keep_with_next=writer.caption() is not None)
            width = self.text_width_in
            self.pictures.append(_Picture(
                p.add_run(),
                page=f"#page(width: {width:.3f}in, height: auto, margin: 0pt)[{code}]",
                width_in=width, max_in=(width, self.text_height_in)))
            caption = writer.caption()
            if caption is not None:
                size = float(writer.layout.get("caption_size_pt") or 0)
                self.caption("scheme", caption[0], caption[1], size=size if size > 0 else None)
        drawn = set(scheme_editor.linked_compound_ids(scheme))
        for child in scheme.children:
            self.compound(child, show_structure=child.id not in drawn, level=level)
            self.anchored_xray(child, level)

    def anchored_xray(self, compound: Compound, level: int) -> None:
        for x in self.project.anchored.get(compound.id) or []:
            if x.id not in self.options.excluded:
                self.xray_block(x, level, heading=False)

    def xray_block(self, section: Section, level: int, heading: bool = True) -> None:
        from ..core import xray as xr
        b = self.b
        if heading:
            self.section_heading(level, [Run(section.name)])
        where = f"X-ray structure “{section.name}”"
        loaded = xr.load(self.project, section)
        if loaded.grown is None:
            b.warnings.append(f"{where}: {loaded.error} Left out.")
            return
        view = xr.view_of(section, loaded.grown)
        svg = b.assets / f"{section.id}.svg"
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text(xr.svg(loaded.grown, view), encoding="utf-8")
        img = b.image(svg, (TEXT_WIDTH_IN, 9.0))
        if img:
            item = b.labels.at(section.id, 0)
            caption = [r for p in parse(xr.caption(self.project, section, view, loaded.block))
                       for r in p.runs]
            self.figure(img, b.links(caption, where), item.label if item is not None else "")
        if section.data.get("table", True):
            data = xr.table_json(section, loaded.block)
            if data["rows"]:
                item = b.labels.at(section.id, 1)
                caption = [r for p in parse(xr.table_caption(self.project, section))
                           for r in p.runs]
                self.table_figure(Table.from_json(data),
                                  item.label if item is not None else "",
                                  b.links(caption, where), where)

    def general_procedures(self, section: Section, level: int) -> None:
        b = self.b
        for gp in self.project.general_procedures(section):
            name = str(gp.get("name", "")).strip() or "General Procedure"
            title = str(gp.get("title", "")).strip()
            runs = [Run(name + (f": {title}" if title else ""))]
            if self.options.format_text:
                runs = format_chemistry(runs)
            self.heading(level, runs, outlined=b.toc_rank >= 3, numbered=False)
            text = self.project.read_gp_text(gp["id"])
            if not text.strip():
                b.warnings.append(f"“{name}” in “{section.name}” has no text yet.")
            self.rich_text([replace(p, align="") for p in parse(text)],
                           f"{section.name} ▸ {name}", heading_offset=level,
                           outlined_levels=0)

    def data_section(self, section: Section, level: int) -> None:
        self.general_procedures(section, level + 1)
        for item in section.items:
            if isinstance(item, SchemePage):
                self.scheme(item, level + 1)
            else:
                self.compound(item, level=level + 1)
                self.anchored_xray(item, level + 1)

    def _has_own(self, section: Section) -> bool:
        """Whether a section prints anything besides its heading (Builder.section_block's
        test, made without writing it)."""
        if section.type == "text":
            return any(p.is_object or any(r.text or r.ref for r in p.runs)
                       for p in parse(self.project.read_section_text(section)))
        if section.type == "data":
            return bool(self.project.general_procedures(section) or section.items)
        return True

    def section_block(self, section: Section, level: int, compounds: list[Compound],
                      skip: frozenset[str], lead: list) -> list:
        """A section and its sub-sections (Builder.section_block). `lead`: headings of
        sections that have nothing of their own, waiting for what comes next (so they go
        on the first spectra page, not alone at the foot of the page before). Returns the
        headings still waiting."""
        if section.id in skip:
            return lead
        start = self.body_mark()
        if section.type == SPECTRA_SECTION:
            if front.SPECTRA_KEY in skip:
                return lead
            self.spectra_section(section, level, lead)
            lead = []
        else:
            heading = None
            if section.type in ("text", "data") or (section.type == "xray"
                                                     and not section.anchored_to):
                heading = (level, [Run(section.name)])
            children = [c for c in section.children if c.id not in skip]
            if heading and not self._has_own(section) and children:
                lead = lead + [heading]
            else:
                self.flush_lead(lead)
                lead = []
                if section.type == "text":
                    self.section_heading(*heading)
                    self.text_section_body(section, level)
                elif section.type == "file":
                    self.file_section(section, level)
                elif section.type == "xray":
                    self.xray_block(section, level, heading=heading is not None)
                else:
                    self.section_heading(*heading)
                    self.data_section(section, level)
            if section.type == "data":
                compounds += section.compounds
        for child in section.children:
            if child.id in skip:
                continue
            lead = self.section_block(child, level + 1, compounds, skip, lead)
        if section.type != SPECTRA_SECTION:   # (always starts a page of its own)
            data = section.data
            if any(s.type == SPECTRA_SECTION for s in self.project.subtree(section)):
                # (the spectra pages are never squeezed onto one page with the rest)
                data = {page_flow.KEY: {**page_flow.item_flow(data), "keep": "split"}}
            self.with_page_flow(start, data, False)
        return lead

    def flush_lead(self, lead: list) -> None:
        for level, runs in lead:
            self.section_heading(level, runs)

    # ------------------------------------------------------------------ spectra

    def spectra_section(self, section: Section, level: int, lead: list) -> None:
        b = self.b
        if section is not self.project.spectra_section:
            b.warnings.append(f"“{section.name}”: a project has only one Spectra section; "
                              "this one was left out.")
            self.flush_lead(lead)
            return
        skip = frozenset(self.options.excluded)
        compounds = b.included_compounds(self.project.top_sections, skip)
        heads = lead + [(level, [Run(section.name)])]
        pages = self.spectra_pages(compounds, heads)
        hrms_on = front.HRMS_KEY not in skip
        if hrms_on and self.has_hrms_reports(compounds):
            if not pages:
                self.page_break()
                self.flush_lead(heads)
            self.hrms_reports_block(compounds, level + 1, new_page=bool(pages))
        elif not pages:
            self.flush_lead(lead)

    def spectra_pages(self, compounds: list[Compound], heads: list) -> bool:
        """Each spectra page as a picture of the page (drawn by Typst exactly as in the
        PDF), on a page of its own; a landscape page gets a landscape Word section. The
        headings `heads` go on top of the first page. False if there are none."""
        pages = self.b.spectra_page_list(compounds)
        if not pages:
            return False
        for i, (orientation, per_page, slots) in enumerate(pages):
            portrait = orientation != "landscape"
            if portrait != self.portrait:
                self.new_section(portrait)
            else:
                self.page_break()
            room = 0.0
            if i == 0 and heads:
                self.flush_lead(heads)
                room = HEADING_ROOM_IN * len(heads)
            w = self.text_width_in
            h = self.text_height_in - PAGE_SAFETY_IN - room
            landscape = "false" if portrait else "true"
            p = self.picture_paragraph()
            p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(0)
            for label in re.findall(r"<(si-s-[A-Za-z0-9_-]+)>", " ".join(slots)):
                self.bookmark(p, label)
            self.pictures.append(_Picture(
                p.add_run(),
                page=(f"#page(width: {w:.3f}in, height: {h:.3f}in, margin: 4pt)"
                      f"[#si-spectra-page({per_page}, {landscape}, ({', '.join(slots)},))]"),
                ppi=PAGE_PPI, exact=(w, h)))
        if not self.portrait:
            self.new_section(True)
        return True

    def has_hrms_reports(self, compounds: list[Compound]) -> bool:
        return any(ms_reports.prints_reports(self.project, c) and ms_reports.report_items(c)
                   for c in compounds)

    def hrms_reports_block(self, compounds: list[Compound], level: int,
                           new_page: bool = True) -> None:
        items = [(c, item) for c in compounds if ms_reports.prints_reports(self.project, c)
                 for item in ms_reports.report_items(c)]
        if not items:
            return
        if new_page:
            self.page_break()
        self.section_heading(level, [Run(HRMS_HEADING)])
        first = True
        for compound, item in items:
            self.hrms_report(compound, str(item.get("file", "")), first)
            first = False

    def hrms_title(self, compound: Compound, name: str, more: str = "") -> None:
        p = self.paragraph([Run(self.b.report.display_id(compound.id), bold=True),
                            Run(f" — HRMS report: {name}{more}")], "left")
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(self.look.body * 1.2)

    def hrms_report(self, compound: Compound, name: str, first: bool) -> None:
        b = self.b
        path = ms_reports.report_path(compound, name)
        shown = b.report.display_id(compound.id)
        if not path.exists():
            b.warnings.append(f"{shown}: the HRMS report {name} is missing; left out.")
            return
        kind = ms_reports.print_kind(path)
        if kind == "none":
            b.warnings.append(f"{shown}: the HRMS report {name} can't be printed (raw data "
                              "or a kind of file that can't be shown); left out.")
            return
        if kind == "pdf":
            pages = b.pdf_pages(path)
            for i, page in enumerate(pages):
                if not (first and i == 0):
                    self.page_break()
                more = f" (page {i + 1} of {len(pages)})" if len(pages) > 1 else ""
                self.hrms_title(compound, name, more)
                self.pdf_page_picture(path, page, HRMS_PAGE_IN)
            return
        if kind == "picture":
            img = b.image(path, (TEXT_WIDTH_IN, HRMS_PAGE_IN))
            if img is None:
                return
            self.hrms_title(compound, name)
            p = self.picture_paragraph()
            self.image_code_picture(p.add_run(), img)
            return
        if kind == "peaks":
            self.hrms_peaks_table(compound, name, path)
            return
        lines, left = ms_reports.listing_lines(path)
        if ms_reports.listing_failed(lines):
            b.warnings.append(f"{shown}: the HRMS report {name} could not be read (it may "
                              "be damaged); only a note is printed for it.")
        self.hrms_title(compound, name)
        box = self.doc.add_table(rows=1, cols=1)
        _no_table_borders(box)
        cell = box.cell(0, 0)
        _cell_borders(cell, top=0.5, left=0.5, bottom=0.5, right=0.5)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        for k, line in enumerate(lines):
            if k:
                p.add_run().add_break()
            r = p.add_run(xml_text(line))
            r.font.size = Pt(LISTING_PT)
            _set_fonts(r._r, MONO_FONT)
        if left:
            note = (f"… {left} more line{'s' if left != 1 else ''} not printed (the whole file "
                    "is kept in the project folder).")
            q = self.paragraph([Run(note, italic=True)], "left", size=8)
            q.paragraph_format.space_before = Pt(2)

    def hrms_peaks_table(self, compound: Compound, name: str, path: Path) -> None:
        b = self.b
        data = ms_reports.read_report(path)
        try:
            peaks = ms_reports.expected_peaks(self.project, compound)
        except OSError:
            b.structure_readable(compound)
            peaks = None
        centre = None
        if peaks:
            centre = next((p.mz for p in peaks if p.monoisotopic), peaks[0].mz)
        rows = ms_reports.peaks_near(data, centre)
        self.hrms_title(compound, name)
        if not rows:
            text = (data.message if not data.readable else
                    "No peaks were found near the expected m/z in this file.")
            self.paragraph([Run(text)], "left", size=9)
            return
        found = str((compound.values.get("hrms") or {}).get("found", "") or "").strip()
        near = (f" The largest peaks near m/z {centre:.4f}:" if centre is not None
                else " The largest peaks:")
        self.paragraph([Run(ms_reports.summary(data) + near)], "left", size=9)
        table = Table.from_json({"header_rows": 1, "columns": ["right", "right", "left"],
                                 "rows": [[{"text": "*m/z*"}, {"text": "Intensity (%)"},
                                           {"text": ""}]] + [
                                     [{"text": f"{mz:.4f}"}, {"text": f"{rel:.1f}"},
                                      {"text": "found m/z" if ms_reports.format_mz(mz) == found
                                       else ""}] for mz, rel in rows]})
        self.plain_table(table, 9)

    def plain_table(self, table: Table, size: float) -> None:
        """A small table without label or caption (HRMS peaks)."""
        word = self.doc.add_table(rows=table.n_rows, cols=table.n_cols)
        _no_table_borders(word)
        word.alignment = WD_TABLE_ALIGNMENT.CENTER
        aligns = {"left": WD_ALIGN_PARAGRAPH.LEFT, "right": WD_ALIGN_PARAGRAPH.RIGHT}
        for r, c, cell in table.cells():
            p = word.cell(r, c).paragraphs[0]
            p.alignment = aligns.get(table.columns[c], WD_ALIGN_PARAGRAPH.CENTER)
            p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(1)
            for runs in self.cell_paragraphs(cell.text, "HRMS report"):
                self.add_runs(p, runs, size)
        last = table.n_rows - 1
        for c in range(table.n_cols):
            _cell_borders(word.cell(0, c), top=0.5, bottom=0.5)
            _cell_borders(word.cell(last, c), bottom=0.5)

    # ------------------------------------------------------------------ references

    def references(self) -> None:
        if not self.cited:
            return
        refs = {r["id"]: r for r in self.project.references}
        self.page_break()
        self.heading(1, [Run(self.look.references_title)], numbered=False)
        size = self.look.references_size
        for n, rid in enumerate(self.cited, start=1):
            ref = refs.get(rid)
            if ref is None:
                continue
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf = p.paragraph_format
            pf.left_indent = Inches(0.4)
            pf.first_line_indent = Inches(-0.4)
            pf.space_before = Pt(0)
            pf.space_after = Pt(size * 0.4)
            pf.tab_stops.add_tab_stop(Inches(0.4))
            mark = {"acs": f"({n})", "rsc": f"{n}", "angewandte": f"[{n}]"}[self.look.citation]
            r = p.add_run(mark + "\t")
            r.font.size = Pt(size)
            self.add_runs(p, reference_runs(ref, self.look.citation), size)

    # ------------------------------------------------------------------ header and footer

    def page_lines(self) -> None:
        """Header and footer (Project settings ▸ Page) in every Word section; none on the
        cover unless chosen (as the PDF)."""
        s = self.project.settings
        v = page_setup.page_settings(s)
        first = self.doc.sections[0]
        cover = front.front_page_included("cover", set(self.options.excluded))
        hide_cover = cover and not v["number_on_cover"]
        first.different_first_page_header_footer = hide_cover
        width = self.look.paper[0] - 2 * self.look.margins[0]
        known = {"title": str(s.get("title", "")), "authors": page_setup.author_names(s),
                 "date": page_setup.date_text(self.b.today)}
        switch = {"1": "Arabic", "i": "roman", "I": "ROMAN"}[v["number_style"]]
        number_size = v["number_size"] if v["number_size"] > 0 else None

        def number_font(r):
            if number_size:
                r.font.size = Pt(number_size)
            if v["number_bold"]:
                r.bold = True

        for name in ("Header", "Footer"):   # (the template's own tab stops would clash)
            ppr = self.doc.styles[name].element.get_or_add_pPr()
            for tabs in ppr.findall(qn("w:tabs")):
                ppr.remove(tabs)

        def fill(paragraph, texts: dict[str, str]) -> None:
            paragraph.text = ""
            pf = paragraph.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf.tab_stops.add_tab_stop(Inches(width / 2), WD_TAB_ALIGNMENT.CENTER)
            pf.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
            for i, pos in enumerate(page_setup.POSITIONS):
                if i:
                    paragraph.add_run("\t")
                runs: list[Run] = []
                parts = page_setup.split_fill_ins(texts.get(pos, "")) + [("end", "")]
                for kind, value in parts:
                    if kind in ("text", "title", "authors", "date"):
                        runs.append(Run(value if kind == "text" else known[kind]))
                        continue
                    if runs:
                        self.add_runs(paragraph, format_chemistry(runs)
                                      if self.options.format_text else runs)
                        runs = []
                    if kind in ("page", "pages"):
                        if v["number_prefix"]:
                            r = paragraph.add_run(xml_text(v["number_prefix"]))
                            number_font(r)
                        _field(paragraph, ("PAGE" if kind == "page" else "NUMPAGES")
                               + f" \\* {switch}", "1", number_font)

        header = v["header"] if any(t.strip() for t in v["header"].values()) else None
        footer = v["footer"]
        if header:
            fill(first.header.paragraphs[0], header)
        fill(first.footer.paragraphs[0], footer)
        for section in self.doc.sections[1:]:
            section.header.is_linked_to_previous = True
            section.footer.is_linked_to_previous = True
        # The number style on every section too, so the table of contents' page numbers
        # (PAGEREF fields) print as the pages do.
        fmt = {"1": "decimal", "i": "lowerRoman", "I": "upperRoman"}[v["number_style"]]
        for section in self.doc.sections:
            sectpr = section._sectPr
            num = sectpr.find(qn("w:pgNumType"))
            if num is None:
                num = OxmlElement("w:pgNumType")
                sectpr.insert_element_before(num, *_SECTPR_AFTER_PGNUM)
            num.set(qn("w:fmt"), fmt)

    # ------------------------------------------------------------------ whole document

    def document(self) -> None:
        p = self.project
        excluded = set(self.options.excluded)
        if front.front_page_included("cover", excluded):
            self.cover()
            self.page_break()
        if front.front_page_included("toc", excluded):
            self.table_of_contents_anchor()
            self.page_break()
        if front.front_page_included("general", excluded):
            self.general_information()
        compounds: list[Compound] = []
        for s in p.top_sections:
            lead = self.section_block(s, 1, compounds, frozenset(excluded), [])
            self.flush_lead(lead)
        if p.spectra_section is None and front.SPECTRA_KEY not in excluded:
            self.spectra_pages(compounds, [(1, [Run(SPECTRA_HEADING)])])
        self.references()
        self.write_toc()
        self.page_lines()
        self.unlink_missing()
        if not self.pictures and not self.doc.tables and \
                not any(x.text.strip() for x in self.doc.paragraphs):
            self.b.warnings.append("Nothing was chosen to be printed in the Compile window, "
                                   "so the SI is empty. Tick what to include and compile "
                                   "again.")

    # ------------------------------------------------------------------ pictures

    def render_header(self) -> str:
        return ('#import "style.typ": *\n'
                "#let si-link(target, body) = body\n"
                f"#set text(font: si-font, size: {self.look.body:g}pt, lang: \"en\")\n"
                "#set page(fill: white)\n")

    def render(self) -> list[str]:
        """Draw every picture Typst has to draw (one Typst run per resolution; a picture
        that stops Typst is drawn on its own, and left out with a note if it still fails)
        and put all pictures into the document. Returns notes. No Qt, no project data:
        safe in a worker thread."""
        notes: list[str] = []
        groups: dict[int, list[_Picture]] = defaultdict(list)
        for pic in self.pictures:
            if pic.path is None:
                groups[pic.ppi].append(pic)
        drawn: dict[int, bytes | None] = {}
        source = self.b.build / RENDER_SOURCE
        for ppi, pics in groups.items():
            pngs = self._render(source, [p.page for p in pics], ppi)
            if pngs is None:   # one of them stops Typst: each on its own
                pngs = []
                for p in pics:
                    one = self._render(source, [p.page], ppi)
                    if one is None:
                        notes.append("A picture could not be drawn for the Word file "
                                     f"({self._culprit(p.page)}); left out.")
                    pngs.append(one[0] if one else None)
            for p, png in zip(pics, pngs):
                drawn[id(p)] = png
        for pic in self.pictures:
            try:
                if pic.path is not None:
                    self._put(pic, pic.path.read_bytes(), None)
                else:
                    png = drawn.get(id(pic))
                    if png:
                        self._put(pic, png, pic.ppi)
            except Exception as e:  # noqa: BLE001 — one picture never stops the file
                notes.append(f"A picture could not be put in the Word file ({e}); left out.")
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
        return notes

    def _culprit(self, page: str) -> str:
        m = _IMAGE_PATH.search(page)
        return Path(m.group(1)).name if m else "a scheme or spectra page"

    def _render(self, source: Path, pages: list[str], ppi: int) -> list[bytes] | None:
        source.write_text(self.render_header() + "\n".join(pages) + "\n", encoding="utf-8")
        try:
            out = typst.compile(str(source), root=str(self.b.root), format="png", ppi=ppi)
        except Exception:  # noqa: BLE001 — Typst reports problems as plain exceptions
            return None
        out = out if isinstance(out, list) else [out]
        return out if len(out) == len(pages) else None

    def _put(self, pic: _Picture, data: bytes, ppi: int | None) -> None:
        if pic.exact is not None:
            pic.run.add_picture(io.BytesIO(data), width=Inches(pic.exact[0]),
                                height=Inches(pic.exact[1]))
            return
        if ppi is not None:
            pw, ph = png_size(data)
            w, h = pw / ppi, ph / ppi
        else:
            from docx.image.image import Image
            image = Image.from_blob(data)
            w, h = image.width.inches, image.height.inches
            if pic.width_in and w:
                h, w = h * pic.width_in / w if w else h, pic.width_in
        if pic.max_in is not None and w > 0 and h > 0:
            fit = min(1.0, pic.max_in[0] / w, pic.max_in[1] / h)
            w, h = w * fit, h * fit
        pic.run.add_picture(io.BytesIO(data), width=Inches(w), height=Inches(h))


# ---------------------------------------------------------------------- building

def prepare_docx(project: Project, options: CompileOptions, image_info, converter=None,
                 crop=None, ink=None) -> WordBuild:
    """Build the Word SI (everything but the pictures) from the project, with a Builder
    as for the PDF (so the same notes come back). Reads the project and uses Qt (through
    image_info and converter), so it runs on the window's thread."""
    options = replace(options, only=None)
    b = Builder(project, options, image_info, name="word")
    b.converter = converter
    b.crops.detect = crop
    b.inks.detect = ink   # (the structure's auto placement on spectra, as for the PDF)
    b.build.mkdir(parents=True, exist_ok=True)
    if b.assets.exists():
        shutil.rmtree(b.assets, ignore_errors=True)
    b.assets.mkdir(parents=True, exist_ok=True)
    style = str(project.settings.get("journal_style") or "ACS").strip().lower() or "acs"
    style_file = STYLES_DIR / f"{style}.typ"
    if not style_file.exists():
        b.warnings.append(f"The {project.settings.get('journal_style')} style is not available "
                          "yet; the ACS style was used.")
        style_file = STYLES_DIR / "acs.typ"
    shutil.copy2(style_file, b.build / "style.typ")
    writer = WordWriter(b)
    with leave_alone(project.dictionary_words()):
        writer.document()
    b.crops.save()
    b.inks.save()
    note_auto_unplaced(b)
    return WordBuild(writer, b.warnings)


def finish_docx(built: WordBuild, name: str = "SI") -> tuple[Path, list[str]]:
    """Draw the pictures and save build/<name>.docx (through a temporary file, so a Word
    file open elsewhere is never left half-written; if it is locked, the new file gets a
    numbered name). Touches no Qt objects and no project data: safe in a worker thread.
    Returns the file and the notes. Raises CompileError."""
    writer = built.writer
    warnings = list(built.warnings) + writer.render()
    folder = writer.b.build
    tmp = folder / f".{name}.partial.docx"
    try:
        writer.doc.save(str(tmp))
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise CompileError(f"The Word file could not be written:\n\n{e}") from e
    final = folder / f"{name}.docx"
    try:
        os.replace(tmp, final)
    except PermissionError:
        n = 2
        while True:
            alt = final.with_name(f"{name} ({n}).docx")
            try:
                os.replace(tmp, alt)
                final = alt
                warnings.append(f"{name}.docx is open in another program, so the new Word "
                                f"file was saved as {alt.name}.")
                break
            except PermissionError:
                n += 1
                if n > 50:
                    tmp.unlink(missing_ok=True)
                    raise CompileError(f"Could not save {name}.docx: the file is locked.") \
                        from None
    return final, warnings


def compile_docx(project: Project, options: CompileOptions, image_info, converter=None,
                 crop=None, name: str = "SI", ink=None) -> tuple[Path, list[str]]:
    """prepare_docx and finish_docx in one go: build/<name>.docx and the notes."""
    return finish_docx(prepare_docx(project, options, image_info, converter, crop, ink), name)
