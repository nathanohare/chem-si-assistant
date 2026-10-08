"""Reading a manuscript's files for the manuscript check (SPEC §13o): PDF, Word (.docx)
and ChemDraw (.cdxml).

Each reader returns a `Source`: the compound records found (ID with yield, ee/er, dr,
footnote markers and the footnotes' text, a structure from ChemDraw), the body text's
"name (ID)" pairs, every word of the text (to tell whether an ID appears at all) and plain
notes about what could not be read. Readers never raise for a bad file: the Source's
`error` says what went wrong in plain words.

Text with positions:
* PDF: pypdf's text callback (`visitor_text`), which also reads text inside figures
  placed as form objects. pypdf only; PyMuPDF is AGPL and not used.
* Word: the paragraphs and tables of word/document.xml, and the text of pasted ChemDraw
  figures (Enhanced Metafiles, read with core/emf_svg.py).
* ChemDraw .cdxml: the structures (RDKit) with the text drawn under them.

The label and value parsing is ported from Nathan's chem-vision skill (MIT; see
core/ms_parse.py and THIRD_PARTY_NOTICES.md); the CDXML reading follows its `cdxml`
command (scripts/chemvision.py, Copyright (c) 2026 nathanohare, MIT License).
"""

from __future__ import annotations

import html
import io
import math
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import ms_parse as P

Progress = Callable[[str], bool] | None   # called with a message; return False to stop

# Raise this whenever reading changes, so that what an older version read (the cache in
# manuscript/check.json) is read again.
READER_VERSION = 2

CAPTION = re.compile(r"^(Scheme|Table|Figure|Fig\.|Chart)\s*(S?\d+)\b", re.I)
_BOLD_FONT = re.compile(r"(?i)(bold|black|heavy|semibold|demi|-bd\b|,bold)")


@dataclass
class Source:
    """What one manuscript file gave."""
    file: str
    kind: str                       # "pdf", "docx", "cdxml" (or "other")
    records: list[dict] = field(default_factory=list)
    names: list[dict] = field(default_factory=list)     # {"name", "id", "where"}
    mentioned: list[str] = field(default_factory=list)  # IDs clearly named in the text
    tokens: list[str] = field(default_factory=list)     # every word, as an ID key
    notes: list[str] = field(default_factory=list)      # plain-language remarks
    error: str = ""
    figure_text: bool = True        # labels were found in the figures (PDF/Word)
    pages: int = 0

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Source":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def read_file(path: Path, progress: Progress = None) -> Source:
    """Read one file of the manuscript folder (by its extension)."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return read_pdf(path, progress)
        if ext == ".docx":
            return read_docx(path, progress)
        if ext == ".cdxml":
            return read_cdxml(path)
    except Stopped:
        raise
    except Exception as e:  # noqa: BLE001 — a damaged file must never stop the check
        return Source(path.name, ext.lstrip("."), error=(
            f"“{path.name}” could not be read ({type(e).__name__}). It may be damaged; "
            "try saving it again from the program that made it."))
    if ext == ".cdx":
        return Source(path.name, "other", error=(
            f"“{path.name}” is a ChemDraw .cdx file, which this program can't read. In "
            "ChemDraw, use File ▸ Save As… and choose “CDXML” as the type, then add that "
            "file instead."))
    if ext == ".doc":
        return Source(path.name, "other", error=(
            f"“{path.name}” is an old-style Word file (.doc). Open it in Word and save it "
            "as .docx (File ▸ Save As ▸ Word Document), then add that file instead."))
    return Source(path.name, "other", error=f"“{path.name}” is not a PDF, Word .docx or "
                                            "ChemDraw .cdxml file, so it was not read.")


class Stopped(Exception):
    """The user stopped the check."""


def _tick(progress: Progress, message: str) -> None:
    if progress is not None and progress(message) is False:
        raise Stopped()


# ---------------------------------------------------------------------- shared steps

def _where(caption: str, page: int | None, fallback: str) -> str:
    parts = [p for p in (caption, f"p. {page}" if page else "") if p]
    return ", ".join(parts) if parts else fallback


def _nearest_caption(captions: list[tuple[float, str]], y: float) -> str:
    """The caption of the scheme/table/figure a label at height y belongs to: the
    nearest one above it, else the nearest below."""
    above = [(y - cy, text) for cy, text in captions if cy <= y]
    if above:
        return min(above)[1]
    below = [(cy - y, text) for cy, text in captions if cy > y]
    return min(below)[1] if below else ""


def _resolve(record: dict, notes: dict[str, str]) -> None:
    """Put the footnotes' text into the record ("conditions": {marker: text})."""
    markers = list(dict.fromkeys(record.get("footnotes") or []))
    if markers:
        record["footnotes"] = markers
        record["conditions"] = {m: notes[m] for m in markers if notes.get(m)}


def _clean_record(rec: dict, where: str, file: str, source: str, confidence: str) -> dict:
    out = {"id": rec["ids"][0], "file": file, "where": where, "source": source,
           "confidence": confidence, "text": str(rec.get("text", ""))[:300]}
    for k in ("yield", "yield_nmr", "ee", "er", "dr", "ee_from_er", "conversion",
              "alternative_results", "footnotes", "conditions", "smiles", "entry",
              "repeated", "yield_assumed"):
        if rec.get(k) not in (None, "", []):
            out[k] = rec[k]
    return out


def _body_features(src: Source, text: str, where: str) -> None:
    for name, label in P.names_with_ids(text):
        src.names.append({"name": name, "id": label, "where": where})
    src.mentioned.extend(P.mentioned_ids(text))


_HYPHEN_PREFIXES = {"tert", "sec", "iso", "neo", "cis", "trans", "endo", "exo", "syn", "anti",
                    "meso", "rac", "ent", "rel", "ortho", "meta", "para", "ipso", "gem", "vic",
                    "erythro", "threo", "sn", "tbu", "ipr", "nbu", "boc", "cbz", "fmoc"}


def _dehyphenate(text: str) -> str:
    """Join words broken at the end of a line: a chemical name's own hyphen is kept
    ("2-\\nmethyl" → "2-methyl", "tert-\\nbutyl", "N-\\nmethyl"), a word broken by the
    typesetter is joined ("4-bromo-\\nbenzoate" → "4-bromobenzoate"). What decides is the
    piece just before the break: a locant (digits, brackets, one letter) or a prefix
    such as tert-/cis-/ortho- keeps its hyphen; a longer plain syllable does not."""
    def join(m):
        before, after = m.group(1), m.group(2)
        word = re.search(r"\S*$", text[:m.start(1) + len(before)]).group(0)
        piece = word.split("-")[-1]
        keep = bool(re.search(r"[\d,()\[\]']", piece)) or len(piece) <= 1 or \
            piece.lower() in _HYPHEN_PREFIXES or after[:1].isdigit() or \
            after[:1] in "([" or after[:1].isupper()
        return before + ("-" if keep else "") + after
    text = re.sub(r"\xad\s*\n\s*", "", text).replace("\xad", "")   # soft hyphens
    text = re.sub(r"(\w)-\n(\S)", join, text)
    return re.sub(r"\s*\n\s*", " ", text)


# ---------------------------------------------------------------------- PDF

def _pdf_spans(page) -> tuple[list[list[P.Span]], str, int]:
    """The page's text pieces with their boxes (y down), its plain text, and how many
    pieces were set at an angle that could not be read. The pieces come in frames: the
    upright text first, then text turned by 90°, 180° or 270° (a sideways table, a
    rotated figure), each turned upright in a frame of its own so that it reads as on
    an upright page."""
    from pypdf.generic import RectangleObject
    box = page.mediabox if isinstance(page.mediabox, RectangleObject) else None
    top = float(box.top) if box is not None else 792.0
    frames: dict[int, list[P.Span]] = {0: []}
    slanted = [0]
    widths: dict[int, object] = {}

    def width_of(text: str, font_dict, size: float) -> float:
        if font_dict is not None:
            key = id(font_dict)
            if key not in widths:
                try:
                    from pypdf._font import Font   # (pypdf ≥ 6; estimated otherwise)
                    widths[key] = Font.from_font_resource(font_dict)
                except Exception:  # noqa: BLE001
                    widths[key] = None
            font = widths[key]
            known = getattr(font, "character_widths", None) if font is not None else None
            if isinstance(known, dict) and known:
                total = 0.0
                for ch in text:
                    w = known.get(ch)
                    total += w if isinstance(w, (int, float)) and w > 0 else                         1000 * _char_width(ch)
                return total * size / 1000.0
        return size * sum(_char_width(ch) for ch in text)

    def visit(text, cm, tm, font_dict, size):
        if not text or not text.strip():
            return
        try:
            a = tm[0] * cm[0] + tm[1] * cm[2]
            b = tm[0] * cm[1] + tm[1] * cm[3]
            c = tm[2] * cm[0] + tm[3] * cm[2]
            d = tm[2] * cm[1] + tm[3] * cm[3]
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        except (TypeError, IndexError):
            return
        scale = math.sqrt(abs(a * d - b * c)) or 1.0
        eff = float(size) * scale
        if not (0.5 < eff < 200):
            return
        angle = math.degrees(math.atan2(b, a))
        turn = int(round(angle / 90.0)) * 90 % 360
        if abs((angle - turn + 180) % 360 - 180) > 3 or a * d - b * c <= 0:
            slanted[0] += 1     # set at a slant (or mirrored): not read
            return
        if turn == 0:
            px, py = x, top - y
        else:   # the position in the text's own frame (x along it, y down across it)
            n1 = math.hypot(a, b) or 1.0
            n2 = math.hypot(c, d) or 1.0
            px = (x * a + y * b) / n1
            py = -(x * c + y * d) / n2
        base = getattr(font_dict, "get", lambda *_: None)("/BaseFont") if font_dict else None
        bold = bool(base and _BOLD_FONT.search(str(base)))
        for i, part in enumerate(str(text).split("\n")):
            if not part.strip():
                continue
            part = part.replace("�C", "°C")
            w = width_of(part.strip(), font_dict, eff)
            lead = len(part) - len(part.lstrip())
            x0 = px + (0.25 * eff * lead if lead else 0.0)
            frames.setdefault(turn, []).append(
                P.Span(part, x0, py - 0.75 * eff, x0 + w, py + 0.2 * eff, eff, bold=bold))

    plain = page.extract_text(visitor_text=visit) or ""
    out = [frames[0]] + [frames[k] for k in sorted(frames) if k and frames[k]]
    return out, plain.replace("�C", "°C"), slanted[0]


def _line_y(lines: list[dict], rec: dict) -> float:
    """Where on the page a sentence's compound is: the bottom of the first line holding
    its ID (with its footnote marker, if it has one); 0 if none is found."""
    label = rec["ids"][0]
    marks = [f"^{m}" for m in rec.get("footnotes") or []]
    hits = [L for L in lines if label in P.find_ids(P.split_footnotes(L["text"])[0])]
    best = [L for L in hits if any(m in L["text"] for m in marks)] or hits
    return best[0]["bbox"][3] if best else 0.0


def _char_width(ch: str) -> float:
    """A character's width in em, roughly (for fonts whose widths the PDF doesn't give)."""
    if ch in " .,:;'|!il1jtfr()[]-":
        return 0.3
    if ch in "mwMW%@":
        return 0.85
    if ch.isupper():
        return 0.65
    return 0.5


def read_pdf(path: Path, progress: Progress = None) -> Source:
    import pypdf
    src = Source(path.name, "pdf")
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                pass
        pages = list(reader.pages)
    except Exception as e:  # noqa: BLE001
        src.error = (f"“{path.name}” could not be opened as a PDF ({type(e).__name__}). It "
                     "may be damaged or password-protected; save it again from Word or "
                     "the journal's site and add it again.")
        return src
    src.pages = len(pages)
    tokens: set[str] = set()
    body_parts: list[str] = []
    any_text = False
    figure_labels = 0
    for number, page in enumerate(pages, start=1):
        _tick(progress, f"Reading {path.name}: page {number} of {len(pages)}…")
        try:
            frames, plain, slanted = _pdf_spans(page)
        except Exception:  # noqa: BLE001 — one bad page: go on with the others
            src.notes.append(f"Page {number} of “{path.name}” could not be read.")
            continue
        if plain.strip():
            any_text = True
        if slanted:
            src.notes.append(f"Some text on p. {number} of “{path.name}” is set at a slant, "
                             "so it was not read.")
        all_lines: list[dict] = []
        all_entries: list[list] = []
        for k, spans in enumerate(frames):
            P.mark_scripts(spans)
            lines = P.group_lines(spans)
            captions = [(L["bbox"][1], CAPTION.match(L["text"]).group(0).rstrip(".:"))
                        for L in lines if CAPTION.match(L["text"])]
            entries = P.footnote_entries(lines)
            tables = P.find_tables(lines)
            for table in tables:
                where = _where(_nearest_caption(captions, table["bbox"][1]), number,
                               f"a table on p. {number}")
                notes = P.footnotes_for(entries, table["bbox"][3])
                for rec in P.table_records(table):
                    _resolve(rec, notes)
                    src.records.append(_clean_record(rec, where, path.name, "table", "high"))
            P.mark_body(lines)
            for rec in P.label_records(lines):
                if rec["ids"][0].isdigit() and not P.has_values(rec):
                    continue   # a page number, a heading's number…
                if P.has_values(rec):
                    figure_labels += 1
                _resolve(rec, P.footnotes_for(entries, rec["bbox"][3]))
                where = _where(_nearest_caption(captions, rec["bbox"][1]), number,
                               f"p. {number}")
                src.records.append(_clean_record(rec, where, path.name, "figure", "high"))
            if k == 0:
                all_lines, all_entries = lines, entries
        body = _dehyphenate(plain)
        body_parts.append(body)
        tokens |= P.all_tokens(body)
        _body_features(src, body, f"p. {number}")
        for rec in P.sentence_records(body):
            # The footnotes of the scheme or table under the sentence's line.
            _resolve(rec, P.footnotes_for(all_entries, _line_y(all_lines, rec)))
            src.records.append(_clean_record(rec, f"text, p. {number}", path.name, "text",
                                             "medium"))
    src.tokens = sorted(tokens)
    if not any_text:
        src.error = (f"“{path.name}” has no text that can be read (it is probably a scanned "
                     "or picture-only PDF), so it could not be checked. Add the Word file "
                     "or a PDF saved from Word instead.")
        src.figure_text = False
    elif figure_labels == 0:
        src.figure_text = False
        src.notes.append(
            f"“{path.name}”: no compound labels with yields were found in the figures. Their "
            "text is probably outlined or part of a picture, so only the body text and tables "
            "could be checked. Add the ChemDraw files (.cdxml) of the schemes to check them.")
    return src


# ---------------------------------------------------------------------- Word (.docx)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
_ON = {None, "1", "true", "on"}


def _on(el) -> bool:
    return el is not None and el.get(W + "val") in _ON


def _paragraph(p) -> tuple[str, list[str], list[str]]:
    """(text with "^a" for superscript footnote letters, bold texts, image relationship
    IDs) of one w:p."""
    parts: list[str] = []
    bold: list[str] = []
    images: list[str] = []
    for el in p.iter():
        if el.tag == A + "blip" and el.get(R + "embed"):
            images.append(el.get(R + "embed"))
        elif el.tag == V + "imagedata" and el.get(R + "id"):
            images.append(el.get(R + "id"))
    for r in p.iter(W + "r"):
        rpr = r.find(W + "rPr")
        sup = rpr is not None and (rpr.find(W + "vertAlign") is not None and
                                   rpr.find(W + "vertAlign").get(W + "val") == "superscript")
        is_bold = rpr is not None and _on(rpr.find(W + "b"))
        text = ""
        for child in r:
            if child.tag == W + "t":
                text += child.text or ""
            elif child.tag in (W + "tab", W + "br", W + "cr"):
                text += " "
            elif child.tag == W + "noBreakHyphen":
                text += "-"
        if not text:
            continue
        letters = P.marker_letters(text) if sup else []
        parts.append("".join(f"^{x}" for x in letters) if letters else text)
        if is_bold and text.strip():
            bold.append(text.strip())
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip(), bold, images


def _cell_text(tc) -> str:
    return " ".join(t for t in (_paragraph(p)[0] for p in tc.iter(W + "p")) if t).strip()


def read_docx(path: Path, progress: Progress = None) -> Source:
    src = Source(path.name, "docx")
    try:
        z = zipfile.ZipFile(str(path))
        doc = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        src.error = (f"“{path.name}” could not be opened as a Word document "
                     f"({type(e).__name__}). It may be damaged; save it again from Word.")
        return src
    rels: dict[str, str] = {}
    try:
        for r in ET.fromstring(z.read("word/_rels/document.xml.rels")):
            rels[r.get("Id")] = r.get("Target") or ""
    except (KeyError, ET.ParseError):
        pass
    body = doc.find(W + "body")
    if body is None:
        src.error = f"“{path.name}” has no text."
        return src
    # The document in order: ("p", text, bold, images) and ("tbl", element).
    blocks: list[tuple] = []

    def walk(parent):
        for child in parent:
            if child.tag == W + "p":
                blocks.append(("p", *_paragraph(child)))
            elif child.tag == W + "tbl":
                blocks.append(("tbl", child))
            elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml"):
                walk(child)
    walk(body)
    # Footnotes (paragraphs starting with a marker, and a table's own last rows), in
    # document order. A plain letter ("a Isolated yield.") only counts right after a
    # table, a figure, a caption or another footnote: body text can start "a Lewis acid…".
    foot_at: list[tuple[int, str, str]] = []
    note_blocks: set[int] = set()
    for i, b in enumerate(blocks):
        if b[0] == "tbl":
            for paras in _table_note_cells(b[1]):
                for text in paras:
                    for marker, note in P.footnote_lines([_fake_line(text)], True).items():
                        foot_at.append((i, marker, note))
        elif b[1]:
            j = next((j for j in range(i - 1, -1, -1)
                      if blocks[j][0] == "tbl" or blocks[j][1] or blocks[j][3]), None)
            prev = blocks[j] if j is not None else None
            plain = prev is not None and (prev[0] == "tbl" or bool(prev[3])
                                          or bool(CAPTION.match(prev[1]))
                                          or j in note_blocks)
            found = P.footnote_lines([_fake_line(b[1])], plain)
            for marker, text in found.items():
                foot_at.append((i, marker, text))
            if found:
                note_blocks.add(i)

    def notes_for(index: int) -> dict[str, str]:
        """Footnotes that apply at block `index`: the first of each marker after it
        (a table's or scheme's footnotes follow it), else the nearest before."""
        out: dict[str, str] = {}
        for i, marker, text in sorted(foot_at, key=lambda f: (f[0] < index, abs(f[0] - index))):
            out.setdefault(marker, text)
        return out

    def caption_near(index: int, kind: str = "") -> str:
        for j in list(range(index - 1, max(-1, index - 4), -1)) + \
                list(range(index + 1, min(len(blocks), index + 4))):
            if blocks[j][0] == "p":
                m = CAPTION.match(blocks[j][1])
                if m and (not kind or m.group(1).lower().startswith(kind)):
                    return m.group(0)
        return ""

    tokens: set[str] = set()
    figures = emf_figures = emf_with_labels = 0
    table_no = paragraph_no = 0
    for i, b in enumerate(blocks):
        if b[0] == "tbl":
            table_no += 1
            rows = _table_grid(b[1])
            for row in rows:
                tokens |= P.all_tokens(" ".join(row))
            table = _word_table(rows)
            if table is None:
                continue
            where = caption_near(i, "table") or f"table {table_no}"
            for rec in P.table_records(table):
                _resolve(rec, notes_for(i))
                src.records.append(_clean_record(rec, where, path.name, "table", "high"))
            continue
        _kind, text, bold, images = b
        if text:
            paragraph_no += 1
            tokens |= P.all_tokens(text)
            if i not in note_blocks:
                where = f"paragraph {paragraph_no}"
                _body_features(src, text, where)
                for rec in P.sentence_records(text):
                    _resolve(rec, notes_for(i))
                    src.records.append(_clean_record(rec, where, path.name, "text", "medium"))
        for rid in images:
            target = rels.get(rid, "")
            if not target:
                continue
            figures += 1
            _tick(progress, f"Reading {path.name}: figure {figures}…")
            name = _part_name(target)
            ext = Path(target).suffix.lower()
            where = caption_near(i) or f"figure {figures}"
            if ext != ".emf":
                continue
            emf_figures += 1
            try:
                data = z.read(name)
            except KeyError:
                continue
            spans = emf_spans(data)
            tokens |= P.all_tokens(" ".join(s.text for s in spans))
            P.mark_scripts(spans)
            lines = P.group_lines(spans)
            entries = P.footnote_entries(lines)
            found = 0
            for rec in P.label_records(lines):
                if rec["ids"][0].isdigit() and not P.has_values(rec):
                    continue
                notes = {**notes_for(i), **P.footnotes_for(entries, rec["bbox"][3])}
                _resolve(rec, notes)
                src.records.append(_clean_record(rec, where, path.name, "figure", "high"))
                found += P.has_values(rec)
            emf_with_labels += found > 0
    src.tokens = sorted(tokens)
    if figures and not emf_with_labels:
        src.figure_text = False
        src.notes.append(
            f"“{path.name}”: no compound labels with yields were found in its figures "
            + ("(they are pictures, whose text can't be read)" if not emf_figures else
               "(pasted figures whose text could not be read)")
            + ", so only the text and tables could be checked. Add the ChemDraw files "
              "(.cdxml) of the schemes to check them.")
    return src


def _fake_line(text: str) -> dict:
    """A Word paragraph as a "line" for the footnote reader."""
    return {"text": text, "bbox": [0, 0, 1, 1], "size": 10,
            "starts_with_marker": text.startswith("^")}


def _part_name(target: str) -> str:
    """The zip name of a relationship target of word/document.xml: relative to word/
    ("media/image1.emf") or absolute ("/word/media/image1.emf")."""
    target = target.replace("\\", "/")
    name = target.lstrip("/") if target.startswith("/") else "word/" + target
    return posixpath.normpath(name)


def _children(parent, tag: str) -> list:
    """Direct children with this tag, also those inside content controls."""
    out = []
    for child in parent:
        if child.tag == tag:
            out.append(child)
        elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml"):
            out += _children(child, tag)
    return out


def _span(tc) -> int:
    pr = tc.find(W + "tcPr")
    g = pr.find(W + "gridSpan") if pr is not None else None
    try:
        return max(1, int(g.get(W + "val"))) if g is not None else 1
    except (TypeError, ValueError):
        return 1


def _merged_above(tc) -> bool:
    """Is this cell merged with the one above it (w:vMerge without "restart")?"""
    pr = tc.find(W + "tcPr")
    v = pr.find(W + "vMerge") if pr is not None else None
    return v is not None and v.get(W + "val") != "restart"


def _table_grid(tbl) -> list[list[str]]:
    """A Word table's text on its grid of columns: a cell spanning columns (gridSpan)
    gives its text to the first of them; a cell merged with the one above (vMerge)
    repeats that cell's text."""
    grid: list[list[str]] = []
    for tr in _children(tbl, W + "tr"):
        row: list[str] = []
        for tc in _children(tr, W + "tc"):
            col = len(row)
            text = _cell_text(tc)
            if _merged_above(tc) and grid and col < len(grid[-1]):
                text = grid[-1][col]
            row.append(text)
            row += [""] * (_span(tc) - 1)
        grid.append(row)
    return grid


def _table_note_cells(tbl) -> list[list[str]]:
    """The paragraphs of rows that are one cell across the table (where a table's own
    footnotes often are)."""
    out = []
    for tr in _children(tbl, W + "tr"):
        cells = _children(tr, W + "tc")
        filled = [tc for tc in cells if _cell_text(tc)]
        if len(filled) == 1 and (len(cells) == 1 or _span(filled[0]) > 1):
            out.append([t for t in (_paragraph(p)[0] for p in filled[0].iter(W + "p")) if t])
    return out


def _word_table(rows: list[list[str]]) -> dict | None:
    """A Word table as find_tables() would give it: the first row with a results column
    (Entry / Yield / ee…) is the header. Header rows above it (a "Results" spanning
    "Yield" and "ee") are joined to its titles."""
    for h, header in enumerate(rows[:4]):
        fields = [P.header_field(c) for c in header]
        if any(f in ("yield", "ee", "er", "dr") for f in fields) and \
                any(f in ("product_id", "entry", "substrate_id") for f in fields):
            titles = list(header)
            for above in rows[:h]:
                if len({c for c in above if c.strip()}) < 2:
                    continue   # a title row across the table
                for k in range(min(len(above), len(titles))):
                    extra = above[k].strip()
                    if extra and extra not in titles[k]:
                        titles[k] = f"{titles[k]} {extra}".strip() if titles[k] else extra
            out_rows = []
            for row in rows[h + 1:]:
                if not any(c.strip() for c in row):
                    continue
                cells = {titles[k]: row[k] for k in range(min(len(titles), len(row)))
                         if row[k].strip() and titles[k].strip()}
                out_rows.append({"cells": cells, "fields": P.row_fields(cells), "bbox": []})
            return {"columns": titles, "rows": out_rows, "bbox": []}
    return None


_SVG_TEXT = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S)
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')


def emf_spans(data: bytes) -> list[P.Span]:
    """The text in an Enhanced Metafile (a figure pasted from ChemDraw into Word) with
    positions, in points, y down — read with the program's EMF converter."""
    from . import emf_svg
    conv = emf_svg.convert(data)
    if conv.error or not conv.svg:
        return []
    spans: list[P.Span] = []
    for m in _SVG_TEXT.finditer(conv.svg):
        attrs = dict(_ATTR.findall(m.group(1)))
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
        if not text.strip():
            continue
        try:
            mat = [float(v) for v in re.findall(r"-?[\d.]+(?:e-?\d+)?",
                                                attrs.get("transform", "matrix(1 0 0 1 0 0)"))]
            a, b, c, d, e, f = (mat + [1, 0, 0, 1, 0, 0][len(mat):])[:6]
            xs = [float(v) for v in attrs.get("x", "0").split()]
            y = float(attrs.get("y", "0"))
            em = float(attrs.get("font-size", "10"))
        except ValueError:
            continue
        scale = math.sqrt(abs(a * d - b * c)) or 1.0
        x0, y0 = a * xs[0] + c * y + e, b * xs[0] + d * y + f
        last = xs[-1] + 0.55 * em if xs else 0.55 * em * len(text)
        x1 = a * last + c * y + e
        size = em * scale / emf_svg.UNITS_PER_PT
        pt = 1.0 / emf_svg.UNITS_PER_PT
        spans.append(P.Span(text, x0 * pt, y0 * pt - 0.75 * size, x1 * pt, y0 * pt + 0.2 * size,
                            size, bold=attrs.get("font-weight") == "bold"))
    return spans


# ---------------------------------------------------------------------- ChemDraw (.cdxml)

def read_cdxml(path: Path) -> Source:
    src = Source(path.name, "cdxml")
    try:
        data = path.read_bytes()
        root = ET.fromstring(data)
    except (ET.ParseError, OSError) as e:
        src.error = (f"“{path.name}” could not be read as a ChemDraw .cdxml file "
                     f"({type(e).__name__}). Save it again from ChemDraw (File ▸ Save As ▸ "
                     "CDXML).")
        return src
    parent = {c: p for p in root.iter() for c in p}

    def bbox(el):
        b = el.get("BoundingBox")
        try:
            v = [float(x) for x in (b or "").split()]
        except ValueError:
            return None
        if len(v) != 4:
            return None
        return [min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])]

    def inside_node(el):
        q = parent.get(el)
        while q is not None:
            if q.tag == "n":
                return True
            q = parent.get(q)
        return False

    frags = []
    for f in root.iter("fragment"):
        if inside_node(f):   # an abbreviation's expansion (Bn, Ts…) inside an atom
            continue
        b = bbox(f)
        if b is None:
            pts = []
            for n_ in f.findall("n"):
                try:
                    pts.append([float(v) for v in (n_.get("p") or "").split()][:2])
                except ValueError:
                    pass
            pts = [p for p in pts if len(p) == 2]
            if pts:
                b = [min(p[0] for p in pts), min(p[1] for p in pts),
                     max(p[0] for p in pts), max(p[1] for p in pts)]
        if b and f.find("n") is not None:
            frags.append({"id": str(f.get("id")), "box": b})
    blocks = []
    lines_all = []
    for t in root.iter("t"):
        if inside_node(t):   # atom labels (N, Me, OMe…)
            continue
        b = bbox(t)
        if b is None:
            continue
        text, bold = _cdxml_text(t)
        rows = [r.strip() for r in text.split("\n")]
        rows = [r for r in rows if r]
        if not rows:
            continue
        blk = {"text": " / ".join(rows), "rows": rows, "bbox": b, "bold": bold}
        h = (b[3] - b[1]) / len(rows)
        for k, row in enumerate(rows):
            line = {"text": row, "bbox": [b[0], b[1] + k * h, b[2], b[1] + (k + 1) * h],
                    "size": max(h, 1.0), "starts_with_marker": row.startswith("^"),
                    "block": blk}
            lines_all.append(line)
        blocks.append(blk)
    entries = P.footnote_entries(lines_all, plain=True)   # (a scheme file has no body text)
    for line in lines_all:
        if line.get("footnote"):
            line["block"]["footnote"] = True
    label_blocks = [b for b in blocks if not b.get("footnote")]
    tokens: set[str] = set()
    for blk in blocks:
        tokens |= P.all_tokens(blk["text"])
    smiles = _cdxml_smiles(path, data, src, root)
    assigned = P.assign_to_structures([f["box"] for f in frags], label_blocks,
                                      _cdxml_arrows(root, bbox))
    used = set()
    for f, blks in zip(frags, assigned):
        if not blks:
            continue
        rows = [r for blk in blks for r in blk["rows"]]
        under = [blk for blk in blks if not blk.get("fallback")] or blks
        bold = [i for blk in under for p in blk["bold"] for i in P.find_ids(p)]
        rec = P.merge_blocks(rows, bold)
        used.update(id(blk) for blk in blks)
        if not rec["ids"]:
            continue
        if f["id"] in smiles:
            rec["smiles"] = smiles[f["id"]]
        # The footnotes of this scheme: the nearest of each marker below the label.
        bottom = max([blk["bbox"][3] for blk in blks] + [f["box"][3]])
        _resolve(rec, P.footnotes_for(entries, bottom))
        src.records.append(_clean_record(rec, path.name, path.name, "cdxml", "exact"))
    # Labels with values that sit under no structure (drawn as a picture, or far away).
    for blk in label_blocks:
        if id(blk) in used:
            continue
        rec = P.merge_blocks(blk["rows"], [i for p in blk["bold"] for i in P.find_ids(p)])
        if rec["ids"] and P.has_values(rec) and len(blk["text"].split()) <= 10:
            _resolve(rec, P.footnotes_for(entries, blk["bbox"][3]))
            src.records.append(_clean_record(rec, path.name, path.name, "cdxml", "high"))
    src.tokens = sorted(tokens)
    if not frags:
        src.notes.append(f"“{path.name}”: no structures were found in this ChemDraw file.")
    return src


def _cdxml_text(t) -> tuple[str, list[str]]:
    """A text object's text, with superscript footnote letters as "^a", and its bold
    runs. (ChemDraw "face": 1 bold, 0x20 subscript, 0x40 superscript, 0x60 formula.)
    ChemDraw's LineStarts (where its wrapped lines start) count characters of the text
    as stored, so the line breaks are placed before any letter becomes "^a"."""
    runs, bold = [], []
    for s in t.iter("s"):
        text = s.text or ""
        try:
            face = int(s.get("face") or 0)
        except ValueError:
            face = 0
        sup = face & 0x60 == 0x40
        letters = P.marker_letters(text) if sup else []
        runs.append((text, "".join(f"^{x}" for x in letters) if letters else None))
        if face & 1 and text.strip():
            bold.append(text.strip())
    raw = "".join(r for r, _m in runs)
    cuts: list[int] = []
    starts = t.get("LineStarts")
    if starts and "\n" not in raw and "\r" not in raw:
        try:
            cuts = sorted({int(v) for v in starts.split() if 0 < int(v) < len(raw)})
        except ValueError:
            cuts = []
    parts, offset = [], 0
    for text, marker in runs:
        inside = [c - offset for c in cuts if offset <= c < offset + len(text)]
        if marker is not None:
            parts.append(("\n" if inside else "") + marker)
        else:
            last = 0
            for c in inside:
                parts.append(text[last:c] + "\n")
                last = c
            parts.append(text[last:])
        offset += len(text)
    return "".join(parts).replace("\r", "\n"), bold


# RDKit's CDXML reader recurses into nested objects: a file nested tens of thousands of
# levels deep (damaged, or made so on purpose) would overflow its stack and end the
# program, so such files are not given to it.
MAX_CDXML_DEPTH = 400
MAX_CDXML_BYTES = 50_000_000


def _depth(root) -> int:
    deepest, stack = 0, [(root, 1)]
    while stack:
        el, d = stack.pop()
        deepest = max(deepest, d)
        stack.extend((c, d + 1) for c in el)
    return deepest


def _cdxml_arrows(root, bbox) -> list[list[float]]:
    """The reaction arrows' lines [x0, y0, x1, y1]: <arrow> objects (Tail3D → Head3D,
    else their box) and older arrow-headed <graphic> lines."""
    out = []
    for el in root.iter():
        if el.tag == "arrow":
            try:
                head = [float(v) for v in (el.get("Head3D") or "").split()][:2]
                tail = [float(v) for v in (el.get("Tail3D") or "").split()][:2]
            except ValueError:
                head = tail = []
            if len(head) == 2 and len(tail) == 2:
                out.append(tail + head)
                continue
        elif not (el.tag == "graphic" and el.get("GraphicType") == "Line"
                  and el.get("ArrowType") not in (None, "", "NoHead")):
            continue
        b = bbox(el)
        if b is not None:
            out.append(b)
    return out


def _cdxml_smiles(path: Path, data: bytes, src: Source, root=None) -> dict[str, str]:
    """{fragment id: SMILES} from RDKit's CDXML reader (tied by the fragment ID RDKit
    keeps, CDX_FRAG_ID)."""
    depth = _depth(root) if root is not None else 0
    if depth > MAX_CDXML_DEPTH or len(data) > MAX_CDXML_BYTES:
        src.notes.append(f"“{path.name}”: the structures were not read, because the file is "
                         + ("nested unusually deeply" if depth > MAX_CDXML_DEPTH else
                            "unusually large") + " (labels and values were still read). "
                         "Save it again from ChemDraw if it should hold structures.")
        return {}
    try:
        from rdkit import Chem, RDLogger
        RDLogger.DisableLog("rdApp.*")
        try:
            mols = Chem.MolsFromCDXML(data.decode("utf-8", "replace"), sanitize=True,
                                      removeHs=True)
        finally:
            RDLogger.EnableLog("rdApp.*")
    except Exception:  # noqa: BLE001
        src.notes.append(f"“{path.name}”: the structures could not be read (labels and "
                         "values were still read).")
        return {}
    out: dict[str, str] = {}
    for m in mols or []:
        if m is None:
            continue
        for prop in ("CDX_FRAG_ID", "CDXML_FRAG_ID"):
            if m.HasProp(prop):
                try:
                    out[str(m.GetProp(prop))] = Chem.MolToSmiles(m)
                except Exception:  # noqa: BLE001
                    pass
                break
    return out
