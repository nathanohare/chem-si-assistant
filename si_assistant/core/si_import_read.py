"""Reading an SI to import it (SPEC §13u.2): Word (.docx) or PDF, into paragraphs of
formatted text (core/markup.py runs) in reading order.

* Word: the paragraphs of word/document.xml (also inside content controls and table
  cells, the latter marked as such), with bold, italic, superscript and subscript, and
  the heading level of a paragraph styled as a heading. A line break inside a paragraph
  starts a new one (SIs often put a compound's heading and its text in one paragraph,
  separated by a line break).
* PDF: pypdf's text pieces with their fonts, sizes and positions (core/ms_read.py) are
  put together into lines (a smaller piece set higher or lower is a superscript or a
  subscript) and the lines into paragraphs. Page headers and footers (S-page numbers,
  a running head repeated on many pages) are left out, and pages that are mostly a
  spectrum's axis and peak labels are set aside (listed in `spectrum_pages`).

The file is only read, never changed. Readers never raise for a bad file: the result's
`error` says what went wrong in plain words.
"""

from __future__ import annotations

import io
import re
import statistics
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from .markup import NORMAL, SUB, SUPER, Paragraph, Run, serialize

# progress(done, total) -> False to stop reading (the result then says it was stopped)
Progress = Callable[[int, int], bool] | None


@dataclass
class Para:
    """One paragraph of the SI."""
    runs: list[Run]
    page: int = 0          # PDF: the page (1, 2, …); Word: 0
    heading: int = 0       # Word: the level of a paragraph styled as a heading (1–3)
    size: float = 0.0      # PDF: the main font size of its text (pt)
    table: bool = False    # Word: from a table's cell
    top: float = 0.0       # PDF: where its first line is on the page (pt from the top)
    bottom: float = 0.0    # PDF: where its last line ends
    # Word: objects anchored in it (ChemDraw drawings, pictures…, see _embedded_objects)
    objects: list = field(default_factory=list)
    # Word, in a table: (table number, row, column, columns spanned, "restart" /
    # "continue" / "" for a cell merged with the ones below)
    cell: tuple | None = None
    # PDF: its left and right ends (pt); whether it is a table's row (wide gaps)
    left: float = 0.0
    right: float = 0.0
    columnar: bool = False

    @property
    def text(self) -> str:
        """The plain text, spaces evened out."""
        return clean_space("".join(r.text for r in self.runs))

    @property
    def markup(self) -> str:
        """The text in the program's stored form (bold, subscripts… kept)."""
        return serialize([Paragraph(merge_runs(self.runs))]).strip()

    def bold_share(self) -> float:
        """How much of its letters and digits are bold (0–1)."""
        total = bold = 0
        for r in self.runs:
            n = sum(ch.isalnum() for ch in r.text)
            total += n
            bold += n if r.bold else 0
        return bold / total if total else 0.0


@dataclass
class ReadResult:
    file_name: str
    kind: str                       # "docx" or "pdf"
    paras: list[Para] = field(default_factory=list)
    pages: int = 0                  # PDF: the number of pages
    spectrum_pages: list[int] = field(default_factory=list)   # PDF pages set aside
    notes: list[str] = field(default_factory=list)            # what the user should know
    error: str = ""
    stopped: bool = False
    path: str = ""                  # the file read


def clean_space(text: str) -> str:
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def merge_runs(runs: list[Run]) -> list[Run]:
    """Neighbouring runs with the same format joined, spaces evened out, ends trimmed."""
    out: list[Run] = []
    for r in runs:
        text = re.sub(r"[ \t   ]+", " ", r.text)
        if not text:
            continue
        if out and out[-1].same_format(r):
            out[-1] = Run(out[-1].text + text, r.bold, r.italic, r.valign, "", r.underline)
        else:
            out.append(Run(text, r.bold, r.italic, r.valign, "", r.underline))
    # no doubled spaces where two runs meet, no spaces at the ends
    for i in range(1, len(out)):
        if out[i - 1].text.endswith(" ") and out[i].text.startswith(" "):
            out[i] = Run(out[i].text[1:], out[i].bold, out[i].italic, out[i].valign, "",
                         out[i].underline)
    # A subscript never follows a space ("CDCl _{3}" from a PDF's estimated widths).
    for i in range(1, len(out)):
        prev = out[i - 1]
        if out[i].valign == SUB and prev.text.endswith(" ") and prev.text.rstrip()[-1:].isalnum():
            out[i - 1] = Run(prev.text.rstrip(), prev.bold, prev.italic, prev.valign, "",
                             prev.underline)
    out = [r for r in out if r.text]
    if out:
        first = out[0]
        out[0] = Run(first.text.lstrip(), first.bold, first.italic, first.valign, "",
                     first.underline)
        last = out[-1]
        out[-1] = Run(last.text.rstrip(), last.bold, last.italic, last.valign, "",
                      last.underline)
    return [r for r in out if r.text]


def read_file(path: Path, progress: Progress = None) -> ReadResult:
    """Read a .docx or .pdf SI (by its extension)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return read_docx(path, progress)
    if suffix == ".pdf":
        return read_pdf(path, progress)
    return ReadResult(path.name, suffix.lstrip("."), error=(
        f"“{path.name}” is not a Word (.docx) or PDF file. Older Word files (.doc) can be "
        "saved as .docx from Word (File ▸ Save As)."))


# ---------------------------------------------------------------------- Word (.docx)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ON = {None, "1", "true", "on"}
# Characters of the Symbol font (Word's "Insert ▸ Symbol"), by their code's low byte.
_SYMBOL = {0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x44: "Δ", 0x65: "ε", 0x66: "φ",
           0x67: "γ", 0x47: "Γ", 0x68: "η", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν",
           0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x53: "Σ", 0x74: "τ", 0x77: "ω",
           0x57: "Ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0xA2: "′", 0xB2: "″", 0xA3: "≤",
           0xB3: "≥", 0xB0: "°", 0xB1: "±", 0xB4: "×", 0xAE: "→", 0xAC: "←", 0xAB: "↔",
           0xB7: "•", 0x2D: "−", 0xBB: "≈"}


def _on(el) -> bool:
    return el is not None and el.get(W + "val") in _ON


def _style_info(z: zipfile.ZipFile) -> dict[str, tuple[int, bool]]:
    """{style ID: (heading level or 0, bold)} from word/styles.xml."""
    out: dict[str, tuple[int, bool]] = {}
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return out
    for st in root.iter(W + "style"):
        sid = st.get(W + "styleId") or ""
        name_el = st.find(W + "name")
        name = (name_el.get(W + "val") if name_el is not None else sid or "").lower()
        level = 0
        m = re.match(r"heading\s*(\d)", name)
        if m:
            level = int(m.group(1))
        elif name in ("title",):
            level = 1
        rpr = st.find(W + "rPr")
        bold = rpr is not None and _on(rpr.find(W + "b"))
        out[sid] = (level, bold)
    return out


def _run_format(rpr, style_bold: bool) -> tuple[bool, bool, str]:
    bold, italic, valign = style_bold, False, NORMAL
    if rpr is not None:
        b = rpr.find(W + "b")
        if b is not None:
            bold = _on(b)
        italic = _on(rpr.find(W + "i"))
        va = rpr.find(W + "vertAlign")
        if va is not None:
            valign = {"superscript": SUPER, "subscript": SUB}.get(va.get(W + "val"), NORMAL)
    return bold, italic, valign


O_NS = "{urn:schemas-microsoft-com:office:office}"
V_NS = "{urn:schemas-microsoft-com:vml}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _member(z: zipfile.ZipFile, name: str) -> bytes | None:
    """A file of the .docx named by a relationship's target (relative to word/)."""
    if not name or "://" in name:
        return None
    try:
        return z.read("word/" + name.lstrip("/").removeprefix("word/"))
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def _embedded_objects(p, z: zipfile.ZipFile, rels: dict[str, str]) -> list[dict]:
    """The embedded objects in a paragraph (SPEC §13u.7): ChemDraw drawings (their CDX
    data, and the picture Word shows of them), other programs' objects (kind and
    picture only), in order; then the paragraph's own pictures (§13u.16: "picture",
    pasted or inserted pictures)."""
    from . import ole
    out = []
    for obj in p.iter(W + "object"):
        # One object can be a group of several (a spectrum with its structure…): each
        # OLEObject names its shape, which holds its picture. "group" tells which
        # objects were grouped together (one per w:object).
        _GROUPS[0] += 1
        shapes = {s.get("id"): s for s in obj.iter(V_NS + "shape")}
        for ole_el in obj.iter(O_NS + "OLEObject"):
            entry = _one_object(ole_el, shapes, obj, z, rels, ole)
            entry["group"] = _GROUPS[0]
            out.append(entry)
    out.extend(_pictures(p, z, rels))
    return out


_GROUPS = [0]       # (a running number for the groups of objects)


def _pictures(p, z: zipfile.ZipFile, rels: dict[str, str]) -> list[dict]:
    """A paragraph's pictures (not embedded objects): DrawingML pictures (`w:drawing`,
    the picture in its `a:blip`) and older VML ones (`w:pict` with `v:imagedata`). The
    VML copy Word keeps as a fall-back for a DrawingML picture is not counted twice."""
    out: list[dict] = []

    def add(rid: str | None, width_in: float | None) -> None:
        name = rels.get(rid or "", "")
        data = _member(z, name)
        if data:
            _GROUPS[0] += 1
            out.append({"kind": "picture", "prog": "", "name": "", "picture": data,
                        "picture_name": name, "group": _GROUPS[0], "width_in": width_in})

    def walk(el) -> None:
        for child in el:
            if child.tag in (MC_NS + "Fallback", W + "object"):
                continue
            if child.tag == W + "drawing":
                extent = next(child.iter(WP_NS + "extent"), None)
                try:            # (its size on the page, in EMU: 914400 per inch)
                    width = int(extent.get("cx")) / 914400 if extent is not None else None
                except (TypeError, ValueError):
                    width = None
                for blip in child.iter(A_NS + "blip"):
                    add(blip.get(R_NS + "embed"), width)
            elif child.tag == W + "pict":
                shape = next(child.iter(V_NS + "shape"), None)
                for img in child.iter(V_NS + "imagedata"):
                    add(img.get(R_NS + "id"), _vml_width(shape))
            else:
                walk(child)
    walk(p)
    return out


def _vml_width(shape) -> float | None:
    """A VML shape's width on the page in inches ("width:123.4pt" in its style)."""
    if shape is None:
        return None
    m = re.search(r"(?:^|;)\s*width\s*:\s*([\d.]+)\s*(pt|in|cm|mm|px)?", shape.get("style") or "")
    if not m:
        return None
    value = float(m.group(1))
    return value / {"pt": 72, "in": 1, "cm": 2.54, "mm": 25.4, "px": 96, None: 72}[m.group(2)]


def _one_object(ole_el, shapes: dict, obj, z: zipfile.ZipFile, rels: dict[str, str],
                ole) -> dict:
    """One embedded object: {"kind": "chemdraw" (with "cdx"), "mnova" or "other", "prog",
    "name" (its file in the .docx), "picture" (what Word shows: usually an EMF),
    "picture_name"}."""
    prog = ole_el.get("ProgID") or ""
    target = rels.get(ole_el.get(R_NS + "id") or "", "")
    shape = shapes.get(ole_el.get("ShapeID") or "")
    img = next((shape if shape is not None else obj).iter(V_NS + "imagedata"), None)
    picture_name = rels.get(img.get(R_NS + "id") or "", "") if img is not None else ""

    def member(name: str) -> bytes | None:
        return _member(z, name)
    entry = {"kind": "other", "prog": prog, "name": target,
             "picture": member(picture_name), "picture_name": picture_name,
             "width_in": _vml_width(shape if shape is not None
                                    else next(obj.iter(V_NS + "shape"), None))}
    if prog.lower().startswith("chemdraw"):
        data = member(target)
        cdx = ole.read_stream(data, "CONTENTS") if data else None
        if cdx is not None:
            entry.update(kind="chemdraw", cdx=cdx)
    elif "mestrenova" in prog.lower():
        entry["kind"] = "mnova"
    return entry


def _docx_paragraph(p, styles: dict[str, tuple[int, bool]]) -> list[tuple[list[Run], int]]:
    """A w:p as one or more (runs, heading level): split at its line and page breaks."""
    ppr = p.find(W + "pPr")
    level, style_bold = 0, False
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        if ps is not None:
            level, style_bold = styles.get(ps.get(W + "val") or "", (0, False))
        ol = ppr.find(W + "outlineLvl")
        if ol is not None and not level:
            try:
                level = min(3, int(ol.get(W + "val") or 9) + 1)
            except ValueError:
                pass
    pieces: list[list[Run]] = [[]]

    def walk(el):
        for child in el:
            tag = child.tag
            if tag in (W + "del", W + "moveFrom", W + "pPr", W + "rPr", W + "fldChar",
                       W + "instrText", W + "txbxContent", W + "drawing", W + "pict",
                       W + "object", W + "footnoteReference", W + "endnoteReference"):
                continue
            if tag == W + "r":
                run(child)
            else:
                walk(child)

    def run(r):
        rpr = r.find(W + "rPr")
        bold, italic, valign = _run_format(rpr, style_bold)
        for child in r:
            tag = child.tag
            if tag == W + "t":
                text = child.text or ""
            elif tag == W + "tab":
                text = " "
            elif tag in (W + "br", W + "cr"):
                pieces.append([])
                continue
            elif tag == W + "noBreakHyphen":
                text = "-"
            elif tag == W + "softHyphen":
                continue
            elif tag == W + "sym":
                try:
                    code = int(child.get(W + "char") or "0", 16) & 0xFF
                except ValueError:
                    continue
                text = _SYMBOL.get(code, "")
            else:
                continue
            if text:
                pieces[-1].append(Run(text, bold, italic, valign))

    walk(p)
    return [(merge_runs(runs), level) for runs in pieces if merge_runs(runs)]


def _own(el, tag: str):
    """`el`'s children with `tag`, also inside content controls (not in nested tables)."""
    for child in el:
        if child.tag == tag:
            yield child
        elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml"):
            yield from _own(child, tag)


def read_docx(path: Path, progress: Progress = None, data: bytes | None = None) -> ReadResult:
    res = ReadResult(Path(path).name, "docx", path=str(path))
    try:
        z = zipfile.ZipFile(io.BytesIO(data) if data is not None else str(path))
        doc = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        res.error = (f"“{res.file_name}” could not be opened as a Word document "
                     f"({type(e).__name__}). It may be damaged, or still open and locked: "
                     "save it again from Word.")
        return res
    styles = _style_info(z)
    body = doc.find(W + "body")
    if body is None:
        res.error = f"“{res.file_name}” has no text."
        return res
    rels: dict[str, str] = {}
    try:
        for r in ET.fromstring(z.read("word/_rels/document.xml.rels")):
            rels[r.get("Id")] = r.get("Target") or ""
    except (KeyError, ET.ParseError):
        pass

    tables = [0]

    def walk(parent, cell: tuple | None):
        for child in parent:
            if child.tag == W + "p":
                objects = _embedded_objects(child, z, rels)
                pieces = _docx_paragraph(child, styles)
                if not pieces and (objects or cell is not None):
                    pieces = [([], 0)]   # (only a drawing; or an empty table cell)
                for i, (runs, level) in enumerate(pieces):
                    res.paras.append(Para(runs, heading=level, table=cell is not None,
                                          objects=objects if i == 0 else [], cell=cell))
            elif child.tag == W + "tbl":
                if cell is not None:          # (a table in a table: its cells' text only)
                    for tc in child.iter(W + "tc"):
                        walk(tc, cell)
                    continue
                tables[0] += 1
                for r, tr in enumerate(_own(child, W + "tr")):
                    col = 0
                    for tc in _own(tr, W + "tc"):
                        span, merge = 1, ""
                        tcpr = tc.find(W + "tcPr")
                        if tcpr is not None:
                            gs = tcpr.find(W + "gridSpan")
                            try:
                                span = max(1, int(gs.get(W + "val") or 1)) if gs is not None else 1
                            except ValueError:
                                span = 1
                            vm = tcpr.find(W + "vMerge")
                            if vm is not None:
                                merge = "restart" if vm.get(W + "val") == "restart" else "continue"
                        walk(tc, (tables[0], r, col, span, merge))
                        col += span
            elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "ins",
                               W + "smartTag"):
                walk(child, cell)
    walk(body, None)
    if progress is not None:
        progress(1, 1)
    if not res.paras:
        res.error = f"No text was found in “{res.file_name}”."
    return res


# ---------------------------------------------------------------------- PDF

@dataclass
class _Line:
    spans: list            # ms_parse.Span
    base: float            # the baseline (pt from the top)
    size: float            # the main font size

    @property
    def x0(self) -> float:
        return min(s.x0 for s in self.spans)

    @property
    def x1(self) -> float:
        return max(s.x1 for s in self.spans)

    def runs(self) -> list[Run]:
        out: list[Run] = []
        prev = None
        for s in sorted(self.spans, key=lambda s: s.x0):
            valign = NORMAL
            if s.size < 0.85 * self.size:
                shift = (s.y1 - 0.2 * s.size) - self.base      # its baseline vs the line's
                if shift < -0.08 * self.size:
                    valign = SUPER
                elif shift > 0.05 * self.size:
                    valign = SUB
            text = s.text.replace("­", "")
            if valign != NORMAL:
                text = text.strip()
            if prev is not None:
                gap = s.x0 - prev.x1
                joined = prev.text.endswith(" ") or text.startswith(" ")
                # (Widths are estimates: next to a sub- or superscript only a clear gap
                # is a space, as in "CDCl3" or "δH 7.26".)
                scripts = valign != NORMAL or (out and out[-1].valign != NORMAL)
                need = 0.45 * self.size if scripts else 0.15 * min(self.size, max(s.size, 1.0))
                if not joined and gap > need:
                    out.append(Run(" "))
            out.append(Run(text, bool(s.bold), bool(getattr(s, "italic", False)), valign))
            prev = s
        return out

    def plain(self) -> str:
        return clean_space("".join(r.text for r in self.runs()))


def _lines(spans: list) -> list[_Line]:
    """Text pieces into lines: pieces whose baselines are within about half a line of
    each other (a superscript's or subscript's too) make one line."""
    lines: list[_Line] = []
    for s in sorted(spans, key=lambda s: (s.y1 - 0.2 * s.size, s.x0)):
        base = s.y1 - 0.2 * s.size
        line = None
        for cand in reversed(lines[-4:]):
            if abs(base - cand.base) < 0.55 * max(cand.size, s.size):
                line = cand
                break
        if line is None:
            lines.append(_Line([s], base, s.size))
            continue
        line.spans.append(s)
        if s.size > 1.15 * line.size:      # a superscript started the line: the main text
            line.base, line.size = base, s.size
    for line in lines:   # the main size: the size most of its characters have
        sizes = Counter()
        for s in line.spans:
            sizes[round(s.size, 1)] += len(s.text.strip())
        main = sizes.most_common(1)[0][0] if sizes else line.size
        mains = [s for s in line.spans if abs(s.size - main) < 0.3]
        if mains:
            line.size = main
            line.base = statistics.median(s.y1 - 0.2 * s.size for s in mains)
    lines.sort(key=lambda L: L.base)
    return lines


_PAGE_NUMBER = re.compile(r"^(?:page\s*)?S?\s?-?\d{1,4}(?:\s*(?:of|/)\s*\d+)?$", re.I)
TOP_BAND = 0.12     # running heads are in the top 12% of a page (bottom: 8%)
_NUMBERISH = re.compile(r"^[-−–+]?\d+(?:[.,]\d+)?$")


def _strip_digits(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower())


def _is_spectrum_page(lines: list[_Line]) -> bool:
    """Mostly numbers (an axis and peak labels), with a ppm axis or an NMR header; or
    hardly any text besides a spectrum's header (a spectrum placed as a picture)."""
    words = [w for L in lines for w in L.plain().split()]
    if not words:
        return False
    numbers = sum(bool(_NUMBERISH.match(w.strip("()[],;"))) for w in words)
    text = " ".join(L.plain() for L in lines)
    # (a page with lines of body text is text, whatever it mentions: e.g. a UV/vis
    # figure's caption, or an apparatus photo's description)
    body_lines = sum(1 for L in lines if len(L.plain()) > 90)
    header = re.search(r"\bNMR\b|\bHPLC\b|\bSFC\b|chromatogram|\bIR\s+spectr|\bCOSY\b|"
                       r"\bHSQC\b|\bHMBC\b|\bNOESY\b", text, re.I)
    if numbers >= 0.5 * len(words) and len(words) >= 12 and body_lines <= 1 and \
            (header or re.search(r"\bppm\b|\bf1\b", text)):
        return True
    return len(lines) <= 8 and bool(header) and body_lines == 0


def read_pdf(path: Path, progress: Progress = None, data: bytes | None = None) -> ReadResult:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    from . import ms_read
    res = ReadResult(Path(path).name, "pdf", path=str(path))
    try:
        reader = PdfReader(io.BytesIO(data) if data is not None else str(path))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:   # noqa: BLE001  (no cryptography package, or a password)
                ok = 0
            if not ok:
                res.error = (f"“{res.file_name}” is password-protected. Save a copy without "
                             "a password (e.g. print it to PDF) and import that.")
                return res
        pages = list(reader.pages)
    except (PdfReadError, OSError, ValueError, KeyError) as e:
        res.error = (f"“{res.file_name}” could not be opened as a PDF ({type(e).__name__}). "
                     "It may be damaged.")
        return res
    res.pages = len(pages)
    page_lines: list[tuple[int, float, list[_Line]]] = []
    for n, page in enumerate(pages, 1):
        if progress is not None and progress(n - 1, len(pages)) is False:
            res.stopped = True
            res.notes.append(f"Reading was stopped at page {n} of {len(pages)}.")
            break
        try:
            frames, _plain, _slanted = ms_read.pdf_spans(page)
            height = float(page.mediabox.height)
        except Exception:   # noqa: BLE001  (one bad page must not stop the import)
            res.notes.append(f"Page {n} could not be read and was left out.")
            continue
        page_lines.append((n, height, _lines(frames[0] if frames else [])))
    if progress is not None and not res.stopped:
        progress(len(pages), len(pages))

    # Headers and footers: in the top 12% or bottom 8% of a page, a page number or a text
    # (digits aside) that is on at least a quarter of the pages there (spectrum pages
    # often have no running head).
    edge_texts: Counter = Counter()
    for _n, height, lines in page_lines:
        for L in lines:
            if L.base < TOP_BAND * height or L.base > 0.92 * height:
                edge_texts[_strip_digits(L.plain())] += 1
    repeated = {t for t, k in edge_texts.items() if k >= max(3, len(page_lines) / 4)}

    sizes: Counter = Counter()
    for _n, _h, lines in page_lines:
        for L in lines:
            sizes[round(L.size, 1)] += len(L.plain())
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    for n, height, lines in page_lines:
        kept = []
        for L in lines:
            text = L.plain()
            if not text:
                continue
            at_edge = L.base < TOP_BAND * height or L.base > 0.92 * height
            # (a page number only in the outer 10%: a heading "1." can sit in the band)
            number = _PAGE_NUMBER.match(text) and (L.base < 0.1 * height or L.base > 0.92 * height)
            if at_edge and (number or _strip_digits(text) in repeated):
                continue
            kept.append(L)
        if _is_spectrum_page(kept):
            res.spectrum_pages.append(n)
            continue
        res.paras.extend(_paragraphs(kept, n, body_size))
    res.paras = _join_across_pages(res.paras)
    if not res.paras and not res.error:
        res.error = (f"No text could be read from “{res.file_name}”. If it is a scan (pictures "
                     "of pages), its text can't be imported.")
    return res


def _join_across_pages(paras: list[Para]) -> list[Para]:
    """A paragraph cut in two is joined again: by a page break when the first part does
    not end a sentence and the next page's text goes on in lower case or with a number
    ("… 1.98 –" / "1.92 (br s, 2H)."); on the same page too when the first part plainly
    stops in the middle (a comma, an opening parenthesis, "J =", a dash)."""
    out: list[Para] = []
    for p in paras:
        if out:
            prev = out[-1]
            text, prev_text = p.text, prev.text
            goes_on = text[:1].islower() or (text[:1] in "0123456789(=,–-" and
                                             not re.match(r"\d+\.\s+[A-Z]", text))
            # (never onto a heading: headings are bold, and may start with a digit)
            plain_text = prev.bold_share() < 0.5 and p.bold_share() < 0.5 and \
                not (prev.columnar or p.columnar)
            cut_short = bool(re.search(r"(?:[,(–=-]|\bJ\s?=?)$", prev_text)) and plain_text
            across = (p.page > prev.page and goes_on and plain_text
                      and not re.search(r"[.!?:;\]]$", prev_text)
                      and not (prev_text.endswith(")") and not text[:1].islower()
                               and not prev_text.endswith(("H)", "Hz)"))))
            same_page = p.page == prev.page and cut_short and (goes_on or text[:2] in ("Hz", "J ", "J="))
            if ((across or same_page or (p.page > prev.page and cut_short))
                    and abs((prev.size or 0) - (p.size or 0)) < 0.9):
                hyphen = prev_text.endswith("-") and prev_text[-2:-1].isalnum()
                runs = prev.runs + ([] if hyphen else [Run(" ")]) + p.runs
                out[-1] = Para(merge_runs(runs), page=prev.page, size=prev.size,
                               top=prev.top, bottom=p.bottom, left=prev.left,
                               right=max(prev.right, p.right))
                continue
        out.append(p)
    return out


def _columnar(line: _Line) -> bool:
    """A row of a table: at least three gaps wider than 0.9 em between its pieces (the
    gap as seen: a piece's own leading or trailing spaces are part of it; a space in
    prose is about 0.25 em, at most about 0.5 em in a loosely justified line)."""
    spans = sorted(line.spans, key=lambda s: s.x0)
    em = line.size

    def gap(a, b) -> float:
        lead = (len(b.text) - len(b.text.lstrip())) * 0.25 * em
        trail = (len(a.text) - len(a.text.rstrip())) * 0.25 * em
        return (b.x0 + lead) - (a.x1 - trail)
    wide = sum(1 for a, b in zip(spans, spans[1:]) if gap(a, b) > 0.9 * em)
    return wide >= 3


def _all_bold(line: _Line) -> bool:
    letters = sum(sum(ch.isalnum() for ch in s.text) for s in line.spans)
    bold = sum(sum(ch.isalnum() for ch in s.text) for s in line.spans if s.bold)
    return letters > 0 and bold >= 0.9 * letters


def _paragraphs(lines: list[_Line], page: int, body_size: float) -> list[Para]:
    """Lines into paragraphs: a new one after a larger gap than usual, at an indented
    line, after a line that ends well before the right margin, where the text turns
    bold or stops being bold (a heading on its own line) and where the size changes."""
    if not lines:
        return []
    left = min(L.x0 for L in lines)
    # The usual right end of a full line. (Line ends are estimated from the fonts'
    # widths, which can be several % off, so a line counts as short only well before it.)
    ends = sorted(L.x1 for L in lines)
    right = ends[int(0.75 * (len(ends) - 1))]
    short = left + 0.72 * (right - left)
    gaps = [b.base - a.base for a, b in zip(lines, lines[1:]) if b.base > a.base]
    usual = statistics.median(gaps) if gaps else 1.2 * body_size
    out: list[Para] = []
    current: list[_Line] = []

    def flush():
        if not current:
            return
        runs: list[Run] = []
        for i, L in enumerate(current):
            line_runs = L.runs()
            if i and runs:
                prev = runs[-1].text.rstrip()
                # A line ending in a hyphen (a chemical name broken at its hyphen) is
                # joined without a space, keeping the hyphen; other lines with a space.
                if not (prev.endswith("-") and len(prev) > 1 and prev[-2].isalnum()):
                    runs.append(Run(" "))
            runs.extend(line_runs)
        size = statistics.median(L.size for L in current)
        out.append(Para(merge_runs(runs), page=page, size=size,
                        top=current[0].base - current[0].size,
                        bottom=current[-1].base + 0.25 * current[-1].size,
                        left=min(L.x0 for L in current), right=max(L.x1 for L in current),
                        columnar=all(_columnar(L) for L in current)))
        current.clear()

    for L in lines:
        if current:
            prev = current[-1]
            gap = L.base - prev.base
            new = (gap > max(1.45 * usual, 1.6 * prev.size)
                   or L.x0 > left + 1.2 * L.size and L.x0 > prev.x0 + 0.8 * L.size
                   or prev.x1 < short
                   or _all_bold(prev) != _all_bold(L)
                   or abs(L.size - prev.size) > 0.9
                   or _columnar(prev) or _columnar(L))      # (each table row on its own)
            if new:
                flush()
        current.append(L)
    flush()
    return out
