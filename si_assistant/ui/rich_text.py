"""Rich-text editor for Text sections and long compound fields (SPEC §6.1, §7.5).

Supports bold, italic, underline, superscript, subscript, two heading levels and a
palette of Greek letters/symbols. The document is stored with the simple markup in
core/markup.py, so anything the markup cannot represent (fonts, colours, sizes) is
dropped — including from pasted text, which keeps only bold/italic/underline/super/
subscript (e.g. "CDCl3" pasted from Word keeps its subscript 3).

Phase 7 adds links, shown as small coloured "chips" that behave like one character:
mentions (type @ or use "@ Mention") show a compound's current ID or a figure's, table's
or scheme's label, and citations ("Cite") show e.g. [Fulmer et al. 2010].

Custom sections and General Information also hold figures and tables (SPEC §13k.5), each
one object followed by its caption on the same paragraph (ui/text_objects.py draws them
and handles their right-click menu; ui/table_editor.py edits tables). A figure or table
always starts its own paragraph: the editor does not join one onto the text above it,
and if a paragraph ever holds text and objects together it is saved as separate
paragraphs, so nothing is lost.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontMetricsF,
                           QGuiApplication, QIcon, QImage, QImageReader, QKeySequence,
                           QPainter, QPalette, QPen, QPixmap, QPyTextObject, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
                           QTextImageFormat)
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QTextEdit, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from ..compile.qt_images import read_image
from ..core import markup
from ..core.markup import (CENTER, CITE, JUSTIFY, LEFT, MENTION, NORMAL, RIGHT, STRUCT, SUB,
                           SUPER, Paragraph, Run, effective_align, mention_kind, stored_align)
from . import text_checks

HEADING_SIZES = {1: 1.45, 2: 1.2}  # relative to the normal font size

SYMBOLS = [
    "α", "β", "γ", "δ", "ε", "ζ", "η", "θ", "κ", "λ", "μ", "ν", "ξ", "π", "ρ", "σ", "τ",
    "φ", "χ", "ψ", "ω", "Δ", "Σ", "Φ", "Ω",
    "°", "±", "×", "·", "→", "⇌", "≈", "≤", "≥", "–", "—", "Å", "‡", "†", "′", "″",
]

_LINE_SEPARATOR = " "   # Shift+Enter inside a paragraph
_OBJECT_CHAR = "￼"      # embedded object (link chip or picture)
MARKUP_MIME = "application/x-si-assistant-markup"
# Structures in copied scheme conditions (@struct{…}): where their files are, so a paste
# into another scheme can copy them (SPEC §13n.4).
STRUCTURES_MIME = "application/x-si-assistant-structures"

LINK_OBJECT = int(QTextFormat.ObjectTypes.UserObject) + 1
FIGURE_OBJECT = int(QTextFormat.ObjectTypes.UserObject) + 2   # a figure (text_objects.py)
TABLE_OBJECT = int(QTextFormat.ObjectTypes.UserObject) + 3    # a table (text_objects.py)
LINK_REF = int(QTextFormat.Property.UserProperty) + 1     # "c:<id>" or "cite:<ids>"
LINK_LABEL = int(QTextFormat.Property.UserProperty) + 2   # what the chip shows
IMAGE_REL = int(QTextFormat.Property.UserProperty) + 3    # older picture: path as stored
OBJECT_ID = int(QTextFormat.Property.UserProperty) + 4    # a figure's or table's ID
# On a paragraph (block) format: it is a figure's or table's paragraph. Qt copies a block's
# format to a line split off it, so a line of text carrying it got its alignment from a
# figure: that is not the text's own (it is put back to the default, see block_align).
OBJECT_PARAGRAPH = int(QTextFormat.Property.UserProperty) + 5
IMAGE_MAX_WIDTH = 420                                     # pixels on screen
OBJECTS_MIME = "application/x-si-assistant-objects"       # copied figures/tables (JSON)
OBJECT_KINDS = {FIGURE_OBJECT: "figure", TABLE_OBJECT: "table"}
DUPLICATE_COLOUR = "#d35400"   # labels used twice (SPEC §13k.5a), as in the Warnings panel

# Paragraph alignment (SPEC §13l.10): the stored names and how the editor shows them.
QT_ALIGN = {LEFT: Qt.AlignmentFlag.AlignLeft, CENTER: Qt.AlignmentFlag.AlignHCenter,
            RIGHT: Qt.AlignmentFlag.AlignRight, JUSTIFY: Qt.AlignmentFlag.AlignJustify}
# (the same words as the table editor's Left / Centre / Right for cells)
ALIGN_TEXTS = {LEFT: ("Left", "Ctrl+L"), CENTER: ("Centre", "Ctrl+E"),
               RIGHT: ("Right", "Ctrl+R"), JUSTIFY: ("Justified", "Ctrl+J")}
ALIGN_SHOWN = {LEFT: "left", CENTER: "centred", RIGHT: "right", JUSTIFY: "justified"}


def block_align(block) -> str:
    """The alignment set on a paragraph (a QTextBlock): "left", "center", "right",
    "justify", or "" when none was set (e.g. text put in by Qt's setPlainText) or when a
    line of text only has it because it was split off a figure's or table's paragraph
    (Enter after a caption): it then follows the default."""
    fmt = block.blockFormat()
    if not fmt.hasProperty(QTextFormat.Property.BlockAlignment):
        return ""
    if fmt.hasProperty(OBJECT_PARAGRAPH) and not starts_with_object(block):
        return ""
    return align_name(fmt.alignment())


def align_name(flags) -> str:
    """"left", "center", "right" or "justify" for a block's Qt alignment."""
    AF = Qt.AlignmentFlag
    value = int(getattr(flags, "value", flags))
    if value & int(AF.AlignJustify.value):
        return JUSTIFY
    if value & int(AF.AlignHCenter.value):
        return CENTER
    if value & int(AF.AlignRight.value):
        return RIGHT
    return LEFT


# ---------------------------------------------------------------------- what links can point to

class LinkContext:
    """What the editors can link to. The main window installs one for the open project."""

    LABELS_KEEP_S = 1.0   # labels are worked out again at most once a second

    def __init__(self, project):
        self.project = project
        self._labels = None
        self._labels_at = 0.0

    def labels(self):
        """Every figure, table and scheme label (core/labels.py), from the saved texts."""
        now = time.monotonic()
        if self._labels is None or now - self._labels_at > self.LABELS_KEEP_S:
            from ..core.labels import compute
            self._labels = compute(self.project)
            self._labels_at = now
        return self._labels

    def invalidate(self) -> None:
        """Something was saved: work the labels out again next time."""
        self._labels = None

    def label(self, ref: str) -> str:
        from ..core import links
        run = Run("", ref=ref)
        if run.mention:
            labels = self.labels() if mention_kind(run.mention) != "compound" else None
            return links.mention_label(self.project, run.mention, labels=labels)
        return links.citation_label(self.project, run.citations)

    def broken(self, ref: str) -> bool:
        from ..core import links
        run = Run("", ref=ref)
        if run.mention:
            labels = self.labels() if mention_kind(run.mention) != "compound" else None
            return not links.target_exists(self.project, run.mention, labels)
        return any(self.project.reference(c) is None for c in run.citations)

    def duplicate(self, ref: str) -> bool:
        """A mention of a figure, table or scheme whose label another item also has."""
        run = Run("", ref=ref)
        if not run.mention or mention_kind(run.mention) == "compound":
            return False
        label = self.labels().label(run.mention)
        return bool(label) and self.labels().is_duplicate(label)

    def compounds(self) -> list[tuple[str, str, str, str]]:
        """(internal id, current ID, name, section name) for every compound."""
        report = self.project.id_report()
        return [(c.id, report.display_id(c.id), c.name, s.name)
                for s in self.project.data_sections() for c in s.compounds]

    def targets(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """What "@" can mention, grouped: [(group title, [(internal id, text shown)])]."""
        groups = [("Compounds", [(cid, f"{shown}   {name or '(unnamed)'}   — {section}")
                                 for cid, shown, name, section in self.compounds()])]
        from ..core import links
        from ..core.labels import KIND_NAMES, KINDS
        labels = self.labels()
        report = self.project.id_report()

        def caption(item) -> str:   # mentions in the caption shown as they look
            runs = [r for p in markup.parse(item.caption_markup) for r in p.runs]
            text = "".join(r.text for r in links.display_runs(self.project, runs, report,
                                                              labels)) or item.caption
            return text.strip()[:60] or "(no caption)"

        for kind in KINDS:
            items = [(i.id, f"{i.label}   {caption(i)}   — {i.where}")
                     for i in labels.of_kind(kind) if i.id]
            groups.append((KIND_NAMES[kind], items))
        return groups

    def references(self) -> list[dict]:
        return list(self.project.references)


_context: LinkContext | None = None
# Opens the References dialog (set by the main window), so "Cite" can offer to add one.
manage_references: Callable[[QWidget], None] | None = None


def set_link_context(context: LinkContext | None) -> None:
    global _context
    _context = context


def link_context() -> LinkContext | None:
    return _context


def link_label(ref: str) -> str:
    if ref.startswith(STRUCT):   # (a structure in a scheme's conditions: drawn, no text)
        return "structure"
    if _context is not None:
        try:
            return _context.label(ref)
        except Exception:  # noqa: BLE001 — a label must never break the editor
            pass
    run = Run("", ref=ref)
    return run.mention or "[" + ", ".join(run.citations) + "]"


class LinkChip(QPyTextObject):
    """Draws a link as a rounded chip: mentions bold blue, citations green, broken red. A
    structure in a scheme's conditions (@struct{…}) is drawn as its picture by the
    editor's `inline_structures` (ui/scheme_editor_ui.py)."""

    def _structures(self):
        return getattr(self.parent(), "inline_structures", None)

    def _label(self, cf: QTextCharFormat) -> str:
        edit = self.parent()
        if hasattr(edit, "chip_label"):
            return edit.chip_label(cf)
        return str(cf.property(LINK_LABEL) or "?")

    def intrinsicSize(self, doc, pos, fmt):  # noqa: N802 (Qt naming)
        cf = QTextCharFormat(fmt)
        ref = str(cf.property(LINK_REF) or "")
        provider = self._structures()
        if ref.startswith(STRUCT) and provider is not None:
            size = provider.size(ref[len(STRUCT):])
            if size is not None:
                return size
        metrics = QFontMetricsF(self._font(cf))
        label = self._label(cf)
        if ref.startswith(STRUCT):
            label = MISSING_STRUCTURE
        return QSizeF(metrics.horizontalAdvance(label) + 6, metrics.height())

    def drawObject(self, painter, rect, doc, pos, fmt):  # noqa: N802
        cf = QTextCharFormat(fmt)
        ref = str(cf.property(LINK_REF) or "")
        label = self._label(cf)
        provider = self._structures()
        if ref.startswith(STRUCT) and provider is not None:
            pixmap = provider.pixmap(ref[len(STRUCT):], QRectF(rect).size(),
                                     painter.device().devicePixelRatioF()
                                     if painter.device() is not None else 1.0)
            if pixmap is not None and not pixmap.isNull():
                painter.drawPixmap(QRectF(rect).toRect(), pixmap)
                return
        if ref.startswith(STRUCT):
            label = MISSING_STRUCTURE
        broken = ref.startswith(STRUCT) or (_context is not None and _context.broken(ref))
        if broken:
            fill, ink = QColor("#fde2e1"), QColor("#c0392b")
        elif _context is not None and _context.duplicate(ref):
            fill, ink = QColor("#fdebd0"), QColor(DUPLICATE_COLOUR)
        elif ref.startswith(MENTION):
            fill, ink = QColor("#dbe9fb"), QColor("#1a4f8b")
        else:
            fill, ink = QColor("#dff3e4"), QColor("#1e6b3a")
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 1, -0.5, 0), 3, 3)
        painter.setPen(ink)
        painter.setFont(self._font(cf))
        painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    @staticmethod
    def _font(cf: QTextCharFormat) -> QFont:
        font = QFont(cf.font())
        ref = str(cf.property(LINK_REF) or "")
        # Compound IDs are bold (as printed); figure/table/scheme labels are not.
        font.setBold(ref.startswith(MENTION) and mention_kind(ref[len(MENTION):]) == "compound")
        return font


MISSING_STRUCTURE = "structure missing"
MENTION_TIPS = {"compound": "Compound mention — printed as its current ID",
                "figure": "Figure mention — printed as the figure's label (e.g. Figure S2)",
                "table": "Table mention — printed as the table's label (e.g. Table S1)",
                "scheme": "Scheme mention — printed as the scheme's label (e.g. Scheme S3)"}


def link_format(ref: str, base: QTextCharFormat | None = None) -> QTextCharFormat:
    fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
    fmt.setObjectType(LINK_OBJECT)
    fmt.setProperty(LINK_REF, ref)
    fmt.setProperty(LINK_LABEL, link_label(ref))
    fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
    if ref.startswith(STRUCT):
        fmt.setToolTip("A structure in the conditions — printed at the size set in the "
                       "scheme's Layout (structure size in conditions)")
    else:
        fmt.setToolTip(MENTION_TIPS[mention_kind(ref[len(MENTION):])]
                       if ref.startswith(MENTION)
                       else "Citation — numbered in the SI by the journal style")
    return fmt


def object_format(kind: str, item_id: str, base: QTextCharFormat | None = None
                  ) -> QTextCharFormat:
    """The one-character format of a figure or table (drawn by ui/text_objects.py)."""
    fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
    fmt.setObjectType(FIGURE_OBJECT if kind == "figure" else TABLE_OBJECT)
    fmt.setProperty(OBJECT_ID, item_id)
    fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
    fmt.setToolTip("Figure — type its caption on the line under it. Right-click for its "
                   "label, to replace the picture or to remove it." if kind == "figure" else
                   "Table — double-click to edit it. Type its caption on the line under it "
                   "(it is printed above the table). Right-click for more.")
    return fmt


def _clean_format(fmt: QTextCharFormat) -> QTextCharFormat:
    out = QTextCharFormat(fmt)
    out.setObjectType(0)
    for prop in (LINK_REF, LINK_LABEL, IMAGE_REL, OBJECT_ID):
        out.clearProperty(prop)
    out.clearProperty(QTextFormat.Property.TextToolTip)
    return out


# ---------------------------------------------------------------------- document <-> paragraphs

def _code_point_index(text: str, utf16: int) -> int:
    """The index in the Python string `text` of the character at Qt position `utf16` (Qt
    counts a character outside the Basic Multilingual Plane, e.g. an emoji or a math
    italic 𝐽 copied from a PDF, as 2; Python counts it as 1)."""
    n = 0
    for i, ch in enumerate(text):
        if n >= utf16:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def document_to_paragraphs(doc: QTextDocument, start: int = 0,
                           end: int | None = None, aligned: bool = False) -> list[Paragraph]:
    """The document (or the part from `start` to `end`, e.g. a selection) as paragraphs.
    `aligned`: the editor lets paragraphs be aligned (SPEC §13l.10), so each paragraph's
    alignment is kept (when it is not the default for its kind); otherwise it is left out."""
    paragraphs: list[Paragraph] = []
    block_align_name = ""

    def finish(p: Paragraph) -> None:
        if p.is_object and p.runs and p.runs[0].text.startswith(" "):
            first = p.runs[0]   # (the space typed after the line break under an object)
            p.runs[0] = first.but(text=first.text.lstrip())
        if block_align_name:
            p.align = stored_align(block_align_name, p.is_object)
        paragraphs.append(p)

    caption_break = False   # a line break typed in the caption being read (Shift+Enter)

    def caption_text(text: str) -> None:
        """Before `text` is added: a line break typed in a caption (a caption is one line)
        becomes a space, so the words either side are not joined (none next to a space)."""
        nonlocal caption_break
        if (caption_break and current.runs and not current.runs[-1].text[-1:].isspace()
                and not text[:1].isspace()):
            current.runs.append(Run(" "))
        caption_break = False

    block = doc.findBlock(start) if start else doc.begin()
    while block.isValid() and (end is None or block.position() <= end):
        heading = min(block.blockFormat().headingLevel(), markup.MAX_HEADING)
        block_align_name = block_align(block) if aligned else ""
        current = Paragraph([], heading)
        caption_break = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            lo, hi = frag.position(), frag.position() + frag.length()
            if frag.isValid() and (hi <= start or (end is not None and lo >= end)):
                it += 1
                continue
            if frag.isValid():
                fmt = frag.charFormat()
                text = frag.text()
                a, b = max(0, start - lo), (end - lo) if end is not None else None
                if len(text) != frag.length():   # (an emoji or a math 𝐽: 2 positions in Qt)
                    a = _code_point_index(text, a)
                    b = _code_point_index(text, b) if b is not None else None
                text = text[a:b]
                is_picture = fmt.isImageFormat() and fmt.property(IMAGE_REL)
                is_object = fmt.objectType() in OBJECT_KINDS and fmt.property(OBJECT_ID)
                if (is_picture or is_object) and (current.is_object or current.runs):
                    # Text, or another figure/table, before it in the same block (e.g. two
                    # paragraphs joined by Backspace): each keeps a paragraph of its own.
                    finish(current)
                    current = Paragraph([], 0)
                if is_picture:
                    # A picture: the rest of the block (after any line break) is its caption.
                    current.image = str(fmt.property(IMAGE_REL))
                    current.heading = 0
                    it += 1
                    continue
                if is_object:
                    # A figure or table: likewise, the rest of the block is its caption.
                    setattr(current, OBJECT_KINDS[fmt.objectType()], str(fmt.property(OBJECT_ID)))
                    current.heading = 0
                    it += 1
                    continue
                valign = {QTextCharFormat.VerticalAlignment.AlignSuperScript: SUPER,
                          QTextCharFormat.VerticalAlignment.AlignSubScript: SUB
                          }.get(fmt.verticalAlignment(), NORMAL)
                bold = fmt.fontWeight() >= QFont.Weight.DemiBold
                # (a pasted web or Word hyperlink is underlined only because it is a link)
                underline = fmt.fontUnderline() and not fmt.isAnchor()
                is_link = fmt.objectType() == LINK_OBJECT and fmt.property(LINK_REF)
                pieces = text.split(_LINE_SEPARATOR)
                for k, piece in enumerate(pieces):
                    if k > 0 and not current.is_object:  # a soft line break: a new paragraph
                        finish(current)
                        current = Paragraph([], heading)
                    elif k > 0 and current.runs:
                        caption_break = True   # (not the break straight after the object)
                    if is_link:
                        for ch in piece:
                            caption_text("x" if ch == _OBJECT_CHAR else ch)
                            if ch == _OBJECT_CHAR:
                                current.runs.append(Run(str(fmt.property(LINK_LABEL) or ""),
                                                        ref=str(fmt.property(LINK_REF))))
                            else:  # text typed straight after a chip
                                current.runs.append(Run(ch, bold, fmt.fontItalic(), valign,
                                                        underline=underline))
                        continue
                    piece = piece.replace(_OBJECT_CHAR, "")
                    if piece:
                        caption_text(piece)
                        current.runs.append(Run(piece, bold, fmt.fontItalic(), valign,
                                                underline=underline))
            it += 1
        finish(current)
        block = block.next()
    return paragraphs


def starts_with_object(block) -> bool:
    """Does this block begin with a figure or table (or an older picture)?"""
    if not block.isValid() or block.length() <= 1:
        return False
    it = block.begin()
    if it.atEnd():
        return False
    fmt = it.fragment().charFormat()
    return bool((fmt.objectType() in OBJECT_KINDS and fmt.property(OBJECT_ID))
                or (fmt.isImageFormat() and fmt.property(IMAGE_REL)))


def char_format_for(run: Run, base_size: float, heading: int) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setFontWeight(QFont.Weight.Bold if run.bold else QFont.Weight.Normal)
    fmt.setFontItalic(run.italic)
    fmt.setFontUnderline(run.underline)
    fmt.setVerticalAlignment({SUPER: QTextCharFormat.VerticalAlignment.AlignSuperScript,
                              SUB: QTextCharFormat.VerticalAlignment.AlignSubScript
                              }.get(run.valign, QTextCharFormat.VerticalAlignment.AlignNormal))
    fmt.setFontPointSize(base_size * HEADING_SIZES.get(heading, 1.0))
    return fmt


def text_block_format(aligned: bool) -> QTextBlockFormat:
    """The paragraph format of a new line of ordinary text: no heading and, where
    paragraphs can be aligned, the default alignment (justified)."""
    fmt = QTextBlockFormat()
    if aligned:
        fmt.setAlignment(QT_ALIGN[markup.TEXT_DEFAULT_ALIGN])
    return fmt


def paragraph_format(base: QTextBlockFormat, para: Paragraph, aligned: bool
                     ) -> QTextBlockFormat:
    """`base` made the format of `para`'s block: its heading level and, where paragraphs
    can be aligned, its alignment (a figure's or table's paragraph is marked as one)."""
    fmt = QTextBlockFormat(base)
    fmt.setHeadingLevel(0 if para.is_object else para.heading)
    if aligned:
        fmt.setAlignment(QT_ALIGN[para.alignment])
        if para.is_object:
            fmt.setProperty(OBJECT_PARAGRAPH, True)
        else:
            fmt.clearProperty(OBJECT_PARAGRAPH)
    return fmt


def insert_paragraphs(cursor: QTextCursor, paragraphs: list[Paragraph], base_size: float,
                      keep_heading_of_first: bool = True, edit: "RichTextEdit | None" = None
                      ) -> None:
    """Insert paragraphs at the cursor. The first one joins the current block, unless it
    or the block is a figure/table: those always keep a block of their own. In an editor
    with alignment (SPEC §13l.10) every paragraph gets its own alignment; the first one,
    joining a line, only when that line is empty (or `keep_heading_of_first` is False)."""
    aligned = edit is not None and edit.alignment_enabled
    rest_align = block_align(cursor.block())   # (of the text after the cursor)
    if paragraphs and not paragraphs[0].is_object and cursor.positionInBlock() == 0 \
            and starts_with_object(cursor.block()):
        # Pasting in front of a figure: the text goes in a new paragraph above it (the
        # figure keeps its own paragraph format, e.g. its alignment).
        cursor.insertBlock(cursor.blockFormat())
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        cursor.setBlockFormat(text_block_format(aligned))
    for k, para in enumerate(paragraphs):
        if k > 0 or (para.is_object and cursor.positionInBlock() > 0):
            cursor.insertBlock(paragraph_format(QTextBlockFormat(), para, aligned))
        elif para.is_object or not keep_heading_of_first:
            cursor.setBlockFormat(paragraph_format(cursor.blockFormat(), para, aligned))
        elif aligned and para.align and cursor.block().length() <= 1:
            # A whole paragraph pasted on an empty line keeps its alignment.
            heading = QTextBlockFormat(cursor.blockFormat()).headingLevel()
            fmt = paragraph_format(cursor.blockFormat(), para, aligned)
            fmt.setHeadingLevel(heading)
            cursor.setBlockFormat(fmt)
        heading = cursor.blockFormat().headingLevel()
        if para.is_object:
            if para.figure or para.table:
                cursor.insertText(_OBJECT_CHAR, object_format(
                    "figure" if para.figure else "table", para.figure or para.table,
                    char_format_for(Run(""), base_size, 0)))
            elif edit is not None:
                edit.insert_picture_object(cursor, para.image)
            cursor.insertText(_LINE_SEPARATOR, char_format_for(Run(""), base_size, 0))
        for run in para.runs:
            if run.ref:
                chip = edit.chip_format if edit is not None else link_format
                cursor.insertText(_OBJECT_CHAR,
                                  chip(run.ref, char_format_for(run, base_size, heading)))
                cursor.setCharFormat(char_format_for(Run(""), base_size, heading))
            else:
                cursor.insertText(run.text, char_format_for(run, base_size, heading))
        if not para.runs:
            # Give empty paragraphs the right size so typing in them looks right.
            cursor.setCharFormat(char_format_for(Run(""), base_size, heading))
    if paragraphs and paragraphs[-1].is_object and not cursor.atBlockEnd():
        # Text that was after the cursor must not become the pasted object's caption (it
        # keeps the alignment of the line it came from).
        block_fmt = QTextBlockFormat()
        if aligned:
            block_fmt.setAlignment(QT_ALIGN[rest_align or markup.TEXT_DEFAULT_ALIGN])
        cursor.insertBlock(block_fmt)
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)


# ---------------------------------------------------------------------- choosers

class PickerDialog(QDialog):
    """A small searchable list: pick a compound to mention, or references to cite."""

    def __init__(self, title: str, items: list[tuple[str, str]], parent: QWidget | None = None,
                 multi: bool = False, hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 420)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to search…")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection if multi
                                   else QListWidget.SelectionMode.SingleSelection)
        for value, text in items:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, value)
            if value is None:   # a group title (e.g. "Tables"), not something to pick
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.list.addItem(item)
        first = next((i for i in range(self.list.count())
                      if self.list.item(i).data(Qt.ItemDataRole.UserRole) is not None), None)
        if first is not None:
            self.list.setCurrentRow(first)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._accept_current)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        if hint:
            label = QLabel(hint)
            label.setWordWrap(True)
            label.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(label)
        self.extra = QVBoxLayout()
        layout.addLayout(self.extra)
        layout.addWidget(self.buttons)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        first = None
        header, header_used = None, False
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is None:   # a group title
                if header is not None:
                    header.setHidden(not header_used)
                header, header_used = item, False
                continue
            hidden = not all(w in item.text().lower() for w in words)
            item.setHidden(hidden)
            header_used = header_used or not hidden
            if not hidden and first is None:
                first = item
        if header is not None:
            header.setHidden(not header_used)
        if first is not None:
            self.list.setCurrentItem(first)

    def _visible_current(self) -> QListWidgetItem | None:
        """The current item, unless the search hid it (Qt keeps a hidden one current)."""
        item = self.list.currentItem()
        return item if item is not None and not item.isHidden() else None

    def _accept_current(self) -> None:
        if self._visible_current() is not None:
            self.accept()

    def values(self) -> list[str]:
        chosen = [i for i in self.list.selectedItems() if not i.isHidden()]
        current = self._visible_current()
        if not chosen and current is not None:
            chosen = [current]
        return [v for v in (i.data(Qt.ItemDataRole.UserRole) for i in chosen) if v is not None]


def choose_mention(parent: QWidget) -> str | None:
    """Pick a compound, figure, table or scheme to mention; returns its internal ID."""
    ctx = link_context()
    if ctx is None:
        return None
    items: list[tuple[str | None, str]] = []
    for title, group in ctx.targets():
        if group:
            items.append((None, title))
            items += group
    dlg = PickerDialog("Mention", items, parent,
                       hint="A compound is printed as its current ID; a figure, table or scheme "
                            "as its label (e.g. Table S2). Both stay right when things are "
                            "renumbered or moved.")
    if not items:
        dlg.list.addItem("(nothing to mention yet)")
        dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
    if dlg.exec() and dlg.values():
        return dlg.values()[0]
    return None


choose_compound = choose_mention   # (the name used before figures and tables existed)


def choose_references(parent: QWidget) -> list[str]:
    from ..core import references as refs
    ctx = link_context()
    if ctx is None:
        return []

    def items():
        return [(r["id"], refs.describe(r)) for r in ctx.references()]

    dlg = PickerDialog("Cite", items(), parent, multi=True,
                       hint="Select one or more references (Ctrl+click). They are numbered in "
                            "the SI in order of first citation.")
    if manage_references is not None:
        more = QPushButton("Add or edit references…")

        def manage():
            manage_references(dlg)
            dlg.list.clear()
            for value, text in items():
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, value)
                dlg.list.addItem(item)

        more.clicked.connect(manage)
        dlg.extra.addWidget(more)
    if dlg.exec():
        return [v for v in dlg.values() if v]
    return []


# ---------------------------------------------------------------------- the editor

def _is_clipboard(source) -> bool:
    """Is `source` what is on the system clipboard (rather than, e.g., a drag)?"""
    try:
        return source is QGuiApplication.clipboard().mimeData()
    except RuntimeError:
        return False


class RichTextEdit(QTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setTabChangesFocus(False)
        self.links_enabled = True
        # Paragraphs (and figures and tables) can be left, centred, right or justified:
        # custom sections, General Information, the cover page's text (SPEC §13l.10).
        # Elsewhere the text follows the journal style and pasted alignment is dropped.
        self.alignment_enabled = False
        # Custom sections and General Information: their figures and tables
        # (ui/text_objects.ObjectStore). None = no figures or tables in this editor.
        self.objects = None
        self.image_folder: Path | None = None   # where older pictures (!image{…}) are
        self.paste_dpi = 1200                    # pasted ChemDraw pictures (Project settings)
        # A scheme's conditions above the arrow can hold small structures (@struct{…},
        # SPEC §13n.4): set by ui/scheme_editor_ui.py; None = not in this text.
        self.inline_structures = None
        # The label each chip shows now (ref -> label), set by refresh_links. Kept outside
        # the document, so a new label (e.g. after renumbering) is not an edit: no Ctrl+Z
        # step of its own, no autosave. It keeps links deleted since (Ctrl+Z can bring
        # them back) until set_markup. A chip not in it shows the label it was made with.
        self.chip_labels: dict[str, str] = {}
        self._chip = LinkChip(self)
        self.document().documentLayout().registerHandler(LINK_OBJECT, self._chip)
        self.currentCharFormatChanged.connect(self._leave_link_format)
        self._fixing_format = False
        self.document().blockCountChanged.connect(self._blocks_changed)
        # Red (spelling) and blue (chemistry formatting) underlines, SPEC §13k.1. They only
        # change how the text looks, never the stored markup.
        self.checks = text_checks.CheckHighlighter(self.document())

    @property
    def base_size(self) -> float:
        size = self.font().pointSizeF()
        return size if size > 0 else 10.0

    def enable_objects(self, store) -> None:
        """Let this editor show (and, if `store.editable`, add) figures and tables."""
        from .text_objects import FigureObject, TableObject
        self.objects = store
        self.image_folder = store.folder
        layout = self.document().documentLayout()
        self._figure_handler = FigureObject(self)
        self._table_handler = TableObject(self)
        layout.registerHandler(FIGURE_OBJECT, self._figure_handler)
        layout.registerHandler(TABLE_OBJECT, self._table_handler)
        store.live_text = self.markup   # labels follow edits not saved yet
        self.document().contentsChanged.connect(store.changed)

    def set_markup(self, text: str) -> None:
        doc = self.document()
        doc.setUndoRedoEnabled(False)
        self.chip_labels = {}   # (the chips made now have the current labels)
        self.checks.setDocument(None)   # underline once at the end, not after every run
        doc.clear()
        cursor = QTextCursor(doc)
        insert_paragraphs(cursor, markup.parse(text), self.base_size,
                          keep_heading_of_first=False, edit=self)
        self.checks.setDocument(doc)
        self.checks.rehighlight()       # now, not on the next event loop turn
        doc.setUndoRedoEnabled(True)
        doc.setModified(False)
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def markup(self) -> str:
        return markup.serialize(document_to_paragraphs(self.document(),
                                                        aligned=self.alignment_enabled))

    # ---- alignment (SPEC §13l.10)

    def block_alignment(self, block=None) -> str:
        """"left", "center", "right" or "justify": how the paragraph at the cursor (or
        `block`) is aligned."""
        block = self.textCursor().block() if block is None else block
        if not self.alignment_enabled:
            return LEFT
        return effective_align(block_align(block), starts_with_object(block))

    def set_alignment(self, name: str) -> None:
        """Align the paragraph at the cursor, or every paragraph in the selection. A figure
        or table can't be justified: "justify" leaves them as they are."""
        if not self.alignment_enabled or self.isReadOnly() or name not in QT_ALIGN:
            return
        doc = self.document()
        cursor = self.textCursor()
        block = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(cursor.selectionEnd())
        c = QTextCursor(doc)
        c.beginEditBlock()
        while block.isValid():
            is_object = starts_with_object(block)
            if not (name == JUSTIFY and is_object):
                fmt = block.blockFormat()
                fmt.setAlignment(QT_ALIGN[name])
                if is_object:
                    fmt.setProperty(OBJECT_PARAGRAPH, True)
                else:
                    fmt.clearProperty(OBJECT_PARAGRAPH)   # (now the text's own)
                c.setPosition(block.position())
                c.setBlockFormat(fmt)
            if block == last:
                break
            block = block.next()
        c.endEditBlock()

    def _normalise_alignment(self) -> None:
        """A line of text split off a figure's or table's paragraph (Enter under its
        caption or in front of it, text with line breaks pasted into a caption) has the
        figure's format, so it would show centred: it gets the format of ordinary text, as
        Word's "next paragraph" after a caption. Part of the step that made it (Ctrl+Z)."""
        if not self.alignment_enabled:
            return
        doc = self.document()
        block = doc.begin()
        c = None
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.hasProperty(OBJECT_PARAGRAPH) and not starts_with_object(block):
                if c is None:
                    c = QTextCursor(doc)
                    c.joinPreviousEditBlock()
                fmt.clearProperty(OBJECT_PARAGRAPH)
                fmt.setAlignment(QT_ALIGN[markup.TEXT_DEFAULT_ALIGN])
                c.setPosition(block.position())
                c.setBlockFormat(fmt)
            block = block.next()
        if c is not None:
            c.endEditBlock()

    def _blocks_changed(self, _count: int) -> None:
        """Lines were added or removed: tidy up the new ones' alignment once the edit
        that made them is over."""
        if self.alignment_enabled and self.objects is not None:
            QTimer.singleShot(0, self, self._normalise_alignment)

    # ---- links

    def _leave_link_format(self, fmt: QTextCharFormat) -> None:
        """Typing right after a chip (or a figure or table) must give ordinary text.

        Only without a selection: with one, setCurrentCharFormat() applies the format to
        the selected text itself (Qt's rule), which turned every figure, table and chip in
        a selection into plain characters (and dropped its italic, sub/superscript…) as
        soon as the selection's moving end passed one, e.g. dragging or Shift+Up from the
        text below a table: the table "disappeared" and was gone from the saved text
        (SPEC §13l.11). Typing over a selection gives ordinary text anyway: Qt clears the
        object type of what is typed."""
        # (text typed over a selection keeps the object's other properties: cleaned too)
        leftover = fmt.hasProperty(LINK_REF) or fmt.hasProperty(OBJECT_ID)
        if self._fixing_format or (fmt.objectType() != LINK_OBJECT
                                   and fmt.objectType() not in OBJECT_KINDS and not leftover):
            return
        if self.textCursor().hasSelection():
            return
        self._fixing_format = True
        try:
            self.setCurrentCharFormat(_clean_format(fmt))
        finally:
            self._fixing_format = False

    def insert_link(self, ref: str) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        base = _clean_format(cursor.charFormat())
        cursor.insertText(_OBJECT_CHAR, self.chip_format(ref, base))
        cursor.setCharFormat(base)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(base)

    def insert_mention(self, item_id: str) -> None:
        self.insert_link(MENTION + item_id)

    def insert_citation(self, ref_ids: list[str]) -> None:
        if ref_ids:
            self.insert_link(CITE + ",".join(ref_ids))

    def mention_compound(self) -> bool:
        """Pick something to mention (a compound, figure, table or scheme) and insert it."""
        item_id = choose_mention(self)
        if item_id:
            self.insert_mention(item_id)
        self.setFocus()
        return bool(item_id)

    mention = mention_compound

    def cite(self) -> None:
        ids = choose_references(self)
        if ids:
            self.insert_citation(ids)
        self.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if (event.text() == "@" and self.links_enabled and link_context() is not None
                and not self.isReadOnly()):
            if self.mention_compound():
                return
        if not self.isReadOnly() and self._keep_objects_apart(event):
            return
        word_end = self._formula_word_end(event)
        super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.objects is not None:
            self._normalise_alignment()   # (Enter under a caption: a line of plain text)
        if word_end is not None:
            text_checks.subscript_formula(self.document(), word_end)

    def _formula_word_end(self, event) -> int | None:
        """The Chemical formula toggle (SPEC §13k.1b): when this key ends a word (space,
        punctuation, Enter), where that word ends, so its formula digits can be
        subscripted after the key is typed (as a separate Ctrl+Z step)."""
        text = event.text()
        mods = event.modifiers()
        shortcut = (mods & Qt.KeyboardModifier.ControlModifier
                    and not mods & Qt.KeyboardModifier.AltModifier)   # (AltGr types, too)
        if (len(text) != 1 or text not in text_checks.WORD_ENDS or self.isReadOnly()
                or shortcut or not text_checks.checks().formula_on):
            return None
        cursor = self.textCursor()
        return None if cursor.hasSelection() else cursor.position()

    def _keep_objects_apart(self, event) -> bool:
        """A figure or table keeps a paragraph of its own: Backspace in front of one (or
        Delete at the end of the line above it) does not join it to the text above, and
        typing in front of one starts a new line above it. True if the key was handled."""
        cursor = self.textCursor()
        if cursor.hasSelection() or event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                         | Qt.KeyboardModifier.AltModifier):
            return False
        block = cursor.block()
        key = event.key()
        if key == Qt.Key.Key_Backspace and cursor.positionInBlock() == 0:
            prev = block.previous()
            if prev.isValid() and prev.length() > 1 and starts_with_object(block):
                cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
                self.setTextCursor(cursor)   # just move to the end of the line above
                return True
        elif key == Qt.Key.Key_Delete and cursor.atBlockEnd():
            nxt = block.next()
            if nxt.isValid() and block.length() > 1 and starts_with_object(nxt):
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
                self.setTextCursor(cursor)
                return True
        elif (event.text() and event.text().isprintable() and cursor.positionInBlock() == 0
              and starts_with_object(block)):
            cursor.beginEditBlock()
            cursor.insertBlock(cursor.blockFormat())   # (the figure keeps its format)
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
            cursor.setBlockFormat(text_block_format(self.alignment_enabled))
            cursor.setCharFormat(char_format_for(Run(""), self.base_size, 0))
            cursor.insertText(event.text())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        return False

    def chip_label(self, fmt: QTextCharFormat) -> str:
        """The label a chip shows (e.g. "3a", "Figure S2")."""
        ref = str(fmt.property(LINK_REF) or "")
        return self.chip_labels.get(ref) or str(fmt.property(LINK_LABEL) or "?")

    def chip_format(self, ref: str, base: QTextCharFormat | None = None) -> QTextCharFormat:
        """link_format() for a chip put in this text: chips already there with the same
        link show its (current) label too."""
        fmt = link_format(ref, base)
        if ref in self.chip_labels:
            self.chip_labels[ref] = str(fmt.property(LINK_LABEL))
        return fmt

    def refresh_links(self) -> None:
        """Update chip labels (e.g. after renumbering) without touching the text: the
        labels are not part of the document, so this is not an edit (no Ctrl+Z step, no
        autosave)."""
        doc = self.document()
        labels: dict[str, str] = {}
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if frag.isValid() and fmt.objectType() == LINK_OBJECT:
                    ref = str(fmt.property(LINK_REF))
                    if ref not in labels:
                        labels[ref] = link_label(ref)
                it += 1
            block = block.next()
        # Links deleted since keep a (current) label too: a chip brought back by the box's
        # own Ctrl+Z must not show the label it was made with (set_markup clears these).
        for ref in self.chip_labels:
            if ref not in labels:
                labels[ref] = link_label(ref)
        self.chip_labels = labels
        doc.markContentsDirty(0, doc.characterCount())
        self.viewport().update()

    # ---- figures and tables (custom sections, General Information)

    def refresh_objects(self) -> None:
        """Draw figures and tables again (new labels, a changed table or picture)."""
        if self.objects is not None:
            self.objects.changed()
        doc = self.document()
        doc.markContentsDirty(0, doc.characterCount())
        self.viewport().update()

    def object_ids(self) -> set[str]:
        return {p.figure or p.table for p in document_to_paragraphs(self.document())
                if p.figure or p.table}

    def _object_positions(self):
        """(kind, ID, position) of every figure and table in the document."""
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if frag.isValid() and fmt.objectType() in OBJECT_KINDS:
                    yield OBJECT_KINDS[fmt.objectType()], str(fmt.property(OBJECT_ID)), \
                        frag.position()
                it += 1
            block = block.next()

    def structure_at(self, pos) -> str:
        """The structure in a scheme's conditions (@struct{…}) under the mouse (viewport
        coordinates): its ID, or ""."""
        cursor = self.cursorForPosition(pos)
        for p in (cursor.position(), cursor.position() - 1):
            if p < 0:
                continue
            c = QTextCursor(self.document())
            c.setPosition(p)
            if c.atBlockEnd():
                continue
            c.setPosition(p + 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = c.charFormat()
            ref = str(fmt.property(LINK_REF) or "") if fmt.objectType() == LINK_OBJECT else ""
            if ref.startswith(STRUCT):
                return ref[len(STRUCT):]
        return ""

    def object_at(self, pos) -> tuple[str, str] | None:
        """The figure or table under the mouse (viewport coordinates), or None."""
        cursor = self.cursorForPosition(pos)
        for p in (cursor.position(), cursor.position() - 1):
            if p < 0:
                continue
            c = QTextCursor(self.document())
            c.setPosition(p)
            if c.atBlockEnd():
                continue
            c.setPosition(p + 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = c.charFormat()
            if fmt.objectType() in OBJECT_KINDS and fmt.property(OBJECT_ID):
                return OBJECT_KINDS[fmt.objectType()], str(fmt.property(OBJECT_ID))
        return None

    def _object_block(self, kind: str, item_id: str):
        for k, i, position in self._object_positions():
            if (k, i) == (kind, item_id):
                return self.document().findBlock(position)
        return None

    def object_caption(self, kind: str, item_id: str) -> list[Run]:
        block = self._object_block(kind, item_id)
        if block is None:
            return []
        paragraphs = document_to_paragraphs(self.document(), block.position(),
                                            block.position() + block.length() - 1)
        return paragraphs[0].runs if paragraphs else []

    def set_object_caption(self, kind: str, item_id: str, runs: list[Run]) -> None:
        """Replace the caption typed after a figure or table."""
        block = self._object_block(kind, item_id)
        if block is None:
            return
        cursor = QTextCursor(block)
        # The object, then the line break; the caption is everything after them.
        start = next((pos for k, i, pos in self._object_positions() if (k, i) == (kind, item_id)))
        cursor.setPosition(start + 1)
        if self.document().characterAt(start + 1) == _LINE_SEPARATOR:
            cursor.setPosition(start + 2)
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        for run in runs:
            if run.ref:
                cursor.insertText(_OBJECT_CHAR, self.chip_format(run.ref, char_format_for(
                    run, self.base_size, 0)))
            else:
                cursor.insertText(run.text, char_format_for(run, self.base_size, 0))
        cursor.endEditBlock()

    def remove_object_paragraph(self, kind: str, item_id: str) -> None:
        """Take a figure or table (with its caption) out of the text."""
        block = self._object_block(kind, item_id)
        if block is None:
            return
        cursor = QTextCursor(block)
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        if block.next().isValid():
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                QTextCursor.MoveMode.KeepAnchor)
        elif block.previous().isValid():   # the last paragraph: remove the break before it
            cursor.setPosition(block.position() - 1)
            cursor.setPosition(block.position() + block.length() - 1,
                               QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.endEditBlock()

    def insert_object(self, kind: str, item_id: str, caption: list[Run] | None = None) -> None:
        """A figure or table paragraph at the cursor: the object, then its caption (on a line
        under it, typed there)."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        new_line = cursor.block().length() > 1
        if new_line:
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            cursor.insertBlock(QTextBlockFormat())
        fmt = cursor.blockFormat()
        fmt.setHeadingLevel(0)
        if self.alignment_enabled:
            # Centred (the default), or as chosen for the empty line it was put on.
            name = block_align(cursor.block())
            fmt.setAlignment(QT_ALIGN[CENTER if new_line or name in ("", JUSTIFY) else name])
            fmt.setProperty(OBJECT_PARAGRAPH, True)
        cursor.setBlockFormat(fmt)
        base = char_format_for(Run(""), self.base_size, 0)
        cursor.insertText(_OBJECT_CHAR, object_format(kind, item_id, base))
        cursor.insertText(_LINE_SEPARATOR, base)
        for run in caption or []:
            if run.ref:
                cursor.insertText(_OBJECT_CHAR, self.chip_format(run.ref, char_format_for(
                    run, self.base_size, 0)))
            else:
                cursor.insertText(run.text, char_format_for(run, self.base_size, 0))
        cursor.setCharFormat(base)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(base)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        found = self.object_at(event.position().toPoint())
        if found is not None and found[0] == "table" and self.objects is not None:
            from .text_objects import edit_table
            edit_table(self, found[1])
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        found = self.object_at(event.pos())
        if found is not None and self.objects is not None:
            from .text_objects import object_menu
            text_checks.show_menu(object_menu(self, *found), event.globalPos())   # (then deleted)
            return
        menu = self.createStandardContextMenu(event.pos())
        sid = self.structure_at(event.pos()) if self.inline_structures is not None else ""
        if sid:
            # A structure in a scheme's conditions: its own printed size (§2.7).
            first = menu.actions()[0] if menu.actions() else None
            act = QAction("Printed size of the picture…", menu)
            act.setEnabled(self.inline_structures.has_picture(sid) and not self.isReadOnly())
            act.triggered.connect(lambda: self.inline_structures.picture_size(sid, self))
            menu.insertAction(first, act)
            menu.insertSeparator(first)
        # Spelling suggestions and formatting fixes for the word clicked (SPEC §13k.1).
        text_checks.add_check_actions(menu, self, self.cursorForPosition(event.pos()).position())
        text_checks.show_menu(menu, event.globalPos())

    # ---- older pictures (!image{…}, before figures had IDs)

    def insert_picture_object(self, cursor: QTextCursor, rel: str) -> None:
        fmt = QTextImageFormat()
        path = (self.image_folder / rel) if self.image_folder is not None else Path(rel)
        url = QUrl.fromLocalFile(str(path))
        # A shrunk copy for the screen: pasted pictures are large (1200 dpi).
        image = read_image(path, IMAGE_MAX_WIDTH * 3)
        if not image.isNull():
            self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
            full = QImageReader(str(path)).size()
            w, h = (full.width(), full.height()) if full.isValid() else (image.width(),
                                                                         image.height())
            ratio = min(1.0, IMAGE_MAX_WIDTH / max(1, w))
            fmt.setWidth(w * ratio)
            fmt.setHeight(h * ratio)
        fmt.setName(url.toString())
        fmt.setProperty(IMAGE_REL, rel)
        fmt.setToolTip(f"Picture: {rel}. Type its caption after it.")
        cursor.insertImage(fmt)

    # ---- copy and paste

    def createMimeDataFromSelection(self):  # noqa: N802
        # Qt's own copy makes its text on demand (links would come out as boxes), so the
        # clipboard gets a fresh copy: formatted text for other programs, plus our markup.
        qt_data = super().createMimeDataFromSelection()
        data = QMimeData()
        if qt_data.hasHtml():
            data.setHtml(qt_data.html())
        cursor = self.textCursor()
        paragraphs = document_to_paragraphs(self.document(), cursor.selectionStart(),
                                            cursor.selectionEnd(), self.alignment_enabled)
        start_block = self.document().findBlock(cursor.selectionStart())
        if cursor.selectionStart() != start_block.position() and paragraphs:
            paragraphs[0].heading = 0  # part of a heading's line pastes as ordinary text
            paragraphs[0].align = ""   # (and of an aligned line: as the line it goes in)
        data.setData(MARKUP_MIME, markup.serialize(paragraphs).encode("utf-8"))
        structures = [r.structure for p in paragraphs for r in p.runs if r.structure]
        if structures and self.inline_structures is not None:
            found = {sid: self.inline_structures.export(sid)
                     for sid in dict.fromkeys(structures)}
            found = {sid: info for sid, info in found.items() if info}
            if found:
                data.setData(STRUCTURES_MIME, json.dumps(found).encode("utf-8"))
        # Figures and tables: where their picture / cells are, so a copy can be pasted
        # into another text.
        if self.objects is not None:
            found = {}
            for p in paragraphs:
                if p.figure or p.table:
                    info = self.objects.export("figure" if p.figure else "table",
                                               p.figure or p.table)
                    if info:
                        found[p.figure or p.table] = info
            if found:
                data.setData(OBJECTS_MIME, json.dumps(found).encode("utf-8"))
        # Plain text shows links as they look (e.g. "3a"), not as boxes.
        data.setText("\n".join("".join(self.chip_labels.get(r.ref, r.text) if r.ref else r.text
                                       for r in p.runs) for p in paragraphs))
        return data

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        return (source.hasFormat(MARKUP_MIME) or super().canInsertFromMimeData(source)
                or self._picture_paste(source))

    def _picture_paste(self, source) -> bool:
        """Should this paste become a figure? Yes for a picture with no text: a screenshot
        or image, or ChemDraw's drawing. ChemDraw puts a vector picture on the clipboard (an
        Enhanced Metafile on Windows, a PDF on macOS) that Qt does not report as an image,
        so the system clipboard is asked directly."""
        if self.objects is None or not self.objects.editable or source.hasFormat(MARKUP_MIME):
            return False
        formats = [f.lower() for f in source.formats()]
        if any("chemdraw" in f or "cdx" in f for f in formats):
            return True   # copied in ChemDraw (any text it adds is not for pasting here)
        if source.hasText() and source.text().strip():
            return False  # e.g. Word or Excel also put a picture of copied text
        if source.hasImage() or any("pdf" in f for f in formats):
            return True
        if formats and not _is_clipboard(source):
            return False
        from . import clipboard_image
        return clipboard_image.clipboard_has_vector()

    def _paste_picture(self, source) -> bool:
        """Paste a picture as a new figure; False if there was none after all."""
        from .clipboard_image import clipboard_picture
        # The system clipboard: rendered from ChemDraw's vector original (which is kept).
        picture = (clipboard_picture(self.paste_dpi)
                   if not source.formats() or _is_clipboard(source) else None)
        image = QImage(source.imageData()) if picture is None and source.hasImage() else None
        if picture is None and (image is None or image.isNull()):
            return False
        try:
            figure_id = (self.objects.add_pasted(picture) if picture is not None
                         else self.objects.add_image(image))
        except Exception as e:  # noqa: BLE001 — report, never lose the editor
            QMessageBox.warning(self, "Could not add the picture", str(e))
            return True
        self.insert_object("figure", figure_id)
        return True

    def _pasted_objects(self, paragraphs: list[Paragraph], source) -> list[Paragraph]:
        """Figures and tables in pasted text: kept (moved, or copied with a new ID) when
        this editor holds them. Other editors (compound texts, general procedures…)
        cannot hold them: they are left out, and stay on the clipboard to be pasted into
        a custom section or General Information."""
        try:
            info = json.loads(bytes(source.data(OBJECTS_MIME)).decode("utf-8")) \
                if source.hasFormat(OBJECTS_MIME) else {}
        except ValueError:
            info = {}
        in_document = self.object_ids() if self.objects is not None else set()
        out = []
        left_out = failed = 0
        # One undo step for the figures and tables pasted, with the text's autosave that
        # follows (their paragraphs).
        step = (self.objects.adding("Paste figures and tables")
                if self.objects is not None and any(p.figure or p.table for p in paragraphs)
                else contextlib.nullcontext())
        with step:
            out, left_out, failed = self._adopt_objects(paragraphs, info, in_document)
        if left_out:
            QMessageBox.information(
                self, "Figures and tables",
                "Figures and tables can only be pasted into a custom section or the General "
                f"Information page, so {'it was' if left_out == 1 else 'they were'} left out "
                "here (with the caption). They are still on the clipboard: paste them there.")
        elif failed and self.objects is not None and self.objects.editable:
            QMessageBox.information(
                self, "Figures and tables",
                "A figure or table could not be pasted (its picture or table file is no longer "
                "in the project), so only its caption was pasted.")
        return out

    def _pasted_structures(self, paragraphs: list[Paragraph], source) -> list[Paragraph]:
        """Structures in pasted scheme conditions: this scheme's stay; another scheme's
        are copied into this one (new IDs); any whose files are gone are left out."""
        try:
            info = json.loads(bytes(source.data(STRUCTURES_MIME)).decode("utf-8")) \
                if source.hasFormat(STRUCTURES_MIME) else {}
        except ValueError:
            info = {}
        info = info if isinstance(info, dict) else {}
        new_ids: dict[str, str | None] = {}
        # One Edit ▸ Undo step for all of them (and the text's autosave that follows), not
        # one per structure: one Undo must not leave copied structures behind.
        count = len({r.structure for p in paragraphs for r in p.runs if r.structure})
        pasting = getattr(self.inline_structures, "pasting", None)
        with pasting(count) if pasting is not None else contextlib.nullcontext():
            for p in paragraphs:
                runs = []
                for r in p.runs:
                    if r.structure:
                        if r.structure not in new_ids:
                            new_ids[r.structure] = self.inline_structures.adopt(
                                r.structure, info.get(r.structure))
                        sid = new_ids[r.structure]
                        if not sid:
                            continue
                        r = r.but(ref=markup.STRUCT + sid)
                    runs.append(r)
                p.runs = runs
        return paragraphs

    def _adopt_objects(self, paragraphs: list[Paragraph], info: dict,
                       in_document: set[str]) -> tuple[list[Paragraph], int, int]:
        out = []
        left_out = failed = 0
        for p in paragraphs:
            if p.figure or p.table:
                if self.objects is None:
                    left_out += 1
                    continue
                kind, item_id = ("figure", p.figure) if p.figure else ("table", p.table)
                new = self.objects.adopt(kind, item_id, info.get(item_id), in_document)
                if new:
                    in_document.add(new)
                    p = Paragraph(p.runs, 0, figure=new if kind == "figure" else "",
                                  table=new if kind == "table" else "", align=p.align)
                else:   # (its picture or table file could not be found): the caption only
                    failed += 1
                    p = Paragraph(p.runs, 0)
            elif p.image:   # an older picture from another text: its caption only
                p = Paragraph(p.runs, 0)
            out.append(p)
        return out, left_out, failed

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        """Paste keeping only bold/italic/underline/super/subscript (and headings). Text
        copied from another SI Assistant editor keeps its mentions and citations (and
        figures and tables, where this editor can hold them)."""
        cursor = self.textCursor()
        if (self.inline_structures is not None and not self.isReadOnly()
                and self.inline_structures.wants(source)):
            sid = self.inline_structures.paste(source)
            if sid:
                self.insert_link(STRUCT + sid)
            return
        if source.hasFormat(MARKUP_MIME):
            paragraphs = markup.parse(bytes(source.data(MARKUP_MIME)).decode("utf-8"))
            if self.inline_structures is None:   # (structures only in scheme conditions)
                paragraphs = [Paragraph([r for r in p.runs if not r.structure], p.heading,
                                        image=p.image, figure=p.figure, table=p.table,
                                        align=p.align) for p in paragraphs]
            elif any(r.structure for p in paragraphs for r in p.runs):
                paragraphs = self._pasted_structures(paragraphs, source)
            if not self.links_enabled:
                paragraphs = [Paragraph([r for r in p.runs if not r.ref], p.heading,
                                        image=p.image, figure=p.figure, table=p.table,
                                        align=p.align)
                              for p in paragraphs]
            if not self.alignment_enabled:   # (this text follows the journal style)
                for p in paragraphs:
                    p.align = ""
            paragraphs = self._pasted_objects(paragraphs, source)
            if not paragraphs:
                return
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            insert_paragraphs(cursor, paragraphs, self.base_size, edit=self)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
        elif self._picture_paste(source) and self._paste_picture(source):
            return
        elif source.hasHtml():
            tmp = QTextDocument()
            tmp.setHtml(source.html())
            if self.objects is not None and self.objects.editable:
                from .text_objects import html_with_tables
                paragraphs = html_with_tables(self, tmp)
                if paragraphs is None:   # only a table (opened in the table editor) or Cancel
                    return
            else:
                paragraphs = document_to_paragraphs(tmp)
            # Word puts an empty paragraph at the end of copied text; drop it.
            while len(paragraphs) > 1 and not paragraphs[-1].runs and not paragraphs[-1].is_object:
                paragraphs.pop()
            # Pasted headings are bold only because of their Word style; the
            # heading level already says that.
            for p in paragraphs:
                if p.heading:
                    p.runs = [r.but(bold=False) for r in p.runs]
                p.image = ""
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            insert_paragraphs(cursor, paragraphs, self.base_size, edit=self)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
        elif source.hasText():
            self.insertPlainText(source.text())
        else:
            super().insertFromMimeData(source)
        if self.objects is not None:
            self._normalise_alignment()   # (lines pasted into a caption: plain text lines)

    # ---- formatting commands

    def _merge(self, fmt: QTextCharFormat) -> None:
        if self.isReadOnly():   # (Ctrl+B… still reach a read-only box: Qt would format it)
            return
        cursor = self.textCursor()
        cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    def toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        is_bold = self.currentCharFormat().fontWeight() >= QFont.Weight.DemiBold
        fmt.setFontWeight(QFont.Weight.Normal if is_bold else QFont.Weight.Bold)
        self._merge(fmt)

    def toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.currentCharFormat().fontItalic())
        self._merge(fmt)

    def toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.currentCharFormat().fontUnderline())
        self._merge(fmt)

    def toggle_valign(self, which: QTextCharFormat.VerticalAlignment) -> None:
        fmt = QTextCharFormat()
        current = self.currentCharFormat().verticalAlignment()
        fmt.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignNormal if current == which else which)
        self._merge(fmt)

    def set_heading(self, level: int) -> None:
        if self.isReadOnly():
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = self.document().findBlock(start)
        last = self.document().findBlock(end)
        while block.isValid():
            c = QTextCursor(block)
            block_fmt = block.blockFormat()
            block_fmt.setHeadingLevel(level)
            c.setBlockFormat(block_fmt)
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            size_fmt = QTextCharFormat()
            size_fmt.setFontPointSize(self.base_size * HEADING_SIZES.get(level, 1.0))
            c.mergeCharFormat(size_fmt)
            if block == last:
                break
            block = block.next()
        cursor.endEditBlock()
        size_fmt = QTextCharFormat()
        size_fmt.setFontPointSize(self.base_size * HEADING_SIZES.get(level, 1.0))
        self.mergeCurrentCharFormat(size_fmt)


def alignment_icon(name: str, colour: QColor) -> QIcon:
    """A small picture of lines set left, centred, right or justified (for the toolbar)."""
    key = (name, colour.rgba())
    icon = _align_icons.get(key)
    if icon is None:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        lengths = (26, 18, 26, 14) if name != JUSTIFY else (26, 26, 26, 16)
        for i, length in enumerate(lengths):
            y = 7 + i * 6
            if name == CENTER:
                x = 3 + (26 - length) / 2
            elif name == RIGHT:
                x = 29 - length
            else:
                x = 3
            painter.drawLine(QPointF(x, y), QPointF(x + length, y))
        painter.end()
        icon = _align_icons[key] = QIcon(pixmap)
    return icon


_align_icons: dict = {}


class RichTextEditor(QWidget):
    """Formatting toolbar + RichTextEdit. Emits `changed` on every edit."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None, headings: bool = True,
                 height: int | None = None, links: bool = True, alignment: bool = False):
        """`headings=False` hides the paragraph-style box (e.g. for NMR reports).
        `height` fixes the text area height (for compact fields on compound pages).
        `links=False` turns off compound mentions and citations (e.g. NMR reports).
        `alignment=True` lets paragraphs be aligned (see enable_alignment)."""
        super().__init__(parent)
        self.edit = RichTextEdit(self)
        self.edit.links_enabled = links
        if not links:   # NMR reports: formatting check only, no spelling (SPEC §13k.1a)
            self.edit.checks.set_checks(spelling=False)
        if height is not None:
            self.edit.setFixedHeight(height)
        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        if height is not None:
            self.toolbar.setStyleSheet("QToolBar { padding: 0px; spacing: 1px; }")

        self.heading_box = QComboBox(self)
        self.heading_box.addItems(["Normal text", "Heading 1", "Heading 2"])
        self.heading_box.setToolTip("Paragraph style")
        self.heading_box.activated.connect(self._heading_chosen)
        self.heading_box.setMinimumWidth(self.heading_box.sizeHint().width())  # (never cut)
        if headings:
            self.toolbar.addWidget(self.heading_box)
        else:
            self.heading_box.hide()
        self._paragraph_sep = self.toolbar.addSeparator()   # (alignment goes before it)
        if not headings:
            self._paragraph_sep.setVisible(False)
        self.align_button: QToolButton | None = None
        self.align_acts: dict[str, QAction] = {}

        def make(text, tip, shortcut, slot, font_tweak=None):
            act = QAction(text, self)
            act.setCheckable(True)
            act.setToolTip(f"{tip} ({QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)})")
            act.setShortcut(QKeySequence(shortcut))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            if font_tweak:
                f = act.font()
                font_tweak(f)
                act.setFont(f)
            self.toolbar.addAction(act)
            # Also on the editor itself, so the keys work while typing in the text box
            # (a shortcut of this kind only works in the widgets its action is added to).
            self.addAction(act)
            return act

        VA = QTextCharFormat.VerticalAlignment
        self.bold_act = make("B", "Bold", "Ctrl+B", self.edit.toggle_bold,
                             lambda f: f.setBold(True))
        self.italic_act = make("I", "Italic", "Ctrl+I", self.edit.toggle_italic,
                               lambda f: f.setItalic(True))
        self.underline_act = make("U", "Underline", "Ctrl+U", self.edit.toggle_underline,
                                  lambda f: f.setUnderline(True))
        self.sup_act = make("x²", "Superscript", "Ctrl+Shift+=",
                            lambda: self.edit.toggle_valign(VA.AlignSuperScript))
        self.sub_act = make("x₂", "Subscript", "Ctrl+=",
                            lambda: self.edit.toggle_valign(VA.AlignSubScript))
        # SPEC §13k.1b: shared by every text box and remembered on this computer.
        hub = text_checks.checks()
        self.formula_act = QAction("H₂", self)
        self.formula_act.setCheckable(True)
        self.formula_act.setChecked(hub.formula_on)
        self.formula_act.setToolTip(
            "Chemical formula: subscript the numbers in formulas as you type "
            "(H2SO4 → H₂SO₄). S1, C18, SN2, E2 and 3a are left alone; Ctrl+Z undoes a "
            "subscript you don't want. Applies to every text box on this computer.")
        self.formula_act.toggled.connect(hub.set_formula_on)
        hub.formula_changed.connect(self._formula_switched)
        self.toolbar.addAction(self.formula_act)
        self._symbols_sep = self.toolbar.addSeparator()   # (Figure… and Table… go before it)

        sym_button = QToolButton(self)
        sym_button.setText("αβγ…")
        sym_button.setToolTip("Insert a Greek letter or symbol (α, β, γ, °, ±, →, …)")
        sym_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sym_menu = QMenu(sym_button)
        for sym in SYMBOLS:
            sym_menu.addAction(sym, lambda s=sym: self.edit.insertPlainText(s))
        sym_button.setMenu(sym_menu)
        self.toolbar.addWidget(sym_button)

        self.mention_act = self.cite_act = self.picture_act = self.table_act = None
        if links:
            self.toolbar.addSeparator()
            self.mention_act = QAction("@ Mention", self)
            self.mention_act.setToolTip("Mention a compound, figure, table or scheme (or type "
                                        "@). It is printed as the compound's current ID or the "
                                        "item's label, so renumbering updates it.")
            self.mention_act.triggered.connect(self.edit.mention_compound)
            self.toolbar.addAction(self.mention_act)
            self.cite_act = QAction("Cite", self)
            self.cite_act.setToolTip("Cite one or more references")
            self.cite_act.triggered.connect(self.edit.cite)
            self.toolbar.addAction(self.cite_act)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.edit)

        self.edit.textChanged.connect(self.changed)
        self.edit.currentCharFormatChanged.connect(self._sync_buttons)
        self.edit.cursorPositionChanged.connect(self._sync_buttons)
        if alignment:
            self.enable_alignment()

    def enable_alignment(self) -> None:
        """Let this editor's paragraphs be left, centred, right or justified, and its
        figures and tables left, centred or right (SPEC §13l.10): a drop-down button in
        the toolbar, and Ctrl+L / Ctrl+E / Ctrl+R / Ctrl+J. Call before set_markup."""
        if self.align_button is not None:
            return
        text = self.edit.markup()
        self.edit.alignment_enabled = True
        self.set_markup(text)   # (what is there gets the default alignment)
        button = QToolButton(self)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        menu = QMenu(button)
        group = QActionGroup(self)
        group.setExclusive(True)
        for name in (LEFT, CENTER, RIGHT, JUSTIFY):
            text, keys = ALIGN_TEXTS[name]
            act = QAction(text, self)
            act.setCheckable(True)
            act.setShortcut(QKeySequence(keys))
            # (while typing in this text box; added to it so the keys work there)
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(lambda _=False, n=name: self._alignment_chosen(n))
            group.addAction(act)
            menu.addAction(act)
            self.addAction(act)
            self.align_acts[name] = act
        button.setMenu(menu)
        # (for the button's tooltip: "Ctrl+L left, Ctrl+E centred, …", ⌘ on a Mac)
        native = QKeySequence.SequenceFormat.NativeText
        self._align_keys = ", ".join(f"{QKeySequence(k).toString(native)} {ALIGN_SHOWN[n]}"
                                     for n, (_t, k) in ALIGN_TEXTS.items())
        self.align_button = button
        self.toolbar.insertWidget(self._paragraph_sep, button)
        self._paragraph_sep.setVisible(True)
        self._sync_buttons()

    def _alignment_chosen(self, name: str) -> None:
        self.edit.set_alignment(name)
        self.edit.setFocus()
        self._sync_buttons()

    def enable_objects(self, store) -> None:
        """Let this editor hold figures and tables (custom sections, General Information;
        SPEC §13k.5). `store` is a ui/text_objects.ObjectStore."""
        from . import text_objects
        self.edit.enable_objects(store)
        if self.picture_act is None:
            # Right after the text formatting (before symbols, mentions and citations), so
            # a narrow page moves the less-used buttons into the toolbar's » menu first.
            self.picture_act = QAction("Figure…", self)
            self.picture_act.setToolTip(
                "Insert a figure from a picture file. You can also paste a picture or a "
                "ChemDraw scheme. Type its caption on the line under it.")
            self.picture_act.triggered.connect(lambda: text_objects.insert_figure_file(self.edit))
            self.table_act = QAction("Table…", self)
            self.table_act.setToolTip(
                "Insert a table (you can also paste one copied from Word or Excel). "
                "Double-click a table to edit it.")
            self.table_act.triggered.connect(lambda: text_objects.new_table(self.edit))
            self.toolbar.insertAction(self._symbols_sep, self.picture_act)
            self.toolbar.insertAction(self._symbols_sep, self.table_act)
            self.toolbar.insertSeparator(self.picture_act)
        for act in (self.picture_act, self.table_act):
            act.setEnabled(store.editable)

    def set_markup(self, text: str) -> None:
        self.edit.blockSignals(True)
        self.edit.set_markup(text)
        self.edit.blockSignals(False)
        self._sync_buttons()

    def markup(self) -> str:
        return self.edit.markup()

    def set_read_only(self, read_only: bool) -> None:
        self.edit.setReadOnly(read_only)
        self.toolbar.setEnabled(not read_only)
        # (their keys work without the toolbar: greyed out with it)
        for act in (self.bold_act, self.italic_act, self.underline_act, self.sup_act,
                    self.sub_act, *self.align_acts.values()):
            act.setEnabled(not read_only)
        self._sync_buttons()

    def _formula_switched(self, on: bool) -> None:
        """Another text box switched the Chemical formula toggle: show it here too."""
        if self.formula_act.isChecked() != on:
            self.formula_act.blockSignals(True)
            self.formula_act.setChecked(on)
            self.formula_act.blockSignals(False)

    def _heading_chosen(self, index: int) -> None:
        self.edit.set_heading(index)
        self.edit.setFocus()

    def _sync_buttons(self, *_args) -> None:
        fmt = self.edit.currentCharFormat()
        VA = QTextCharFormat.VerticalAlignment
        self.bold_act.setChecked(fmt.fontWeight() >= QFont.Weight.DemiBold)
        self.italic_act.setChecked(fmt.fontItalic())
        self.underline_act.setChecked(fmt.fontUnderline())
        self.sup_act.setChecked(fmt.verticalAlignment() == VA.AlignSuperScript)
        self.sub_act.setChecked(fmt.verticalAlignment() == VA.AlignSubScript)
        self.heading_box.setCurrentIndex(
            min(self.edit.textCursor().blockFormat().headingLevel(), markup.MAX_HEADING))
        if self.align_button is not None:
            name = self.edit.block_alignment()
            self.align_acts[name].setChecked(True)
            on_object = starts_with_object(self.edit.textCursor().block())
            self.align_acts[JUSTIFY].setEnabled(not on_object and not self.edit.isReadOnly())
            self.align_button.setIcon(alignment_icon(
                name, self.palette().color(QPalette.ColorRole.ButtonText)))
            what = "this figure or table" if on_object else "this paragraph"
            self.align_button.setToolTip(
                f"Alignment of {what} (now {ALIGN_SHOWN[name]}).\nText is justified and "
                "figures and tables are centred unless you choose otherwise.\n"
                f"Keys: {self._align_keys}.")
        has_context = link_context() is not None
        for act in (self.mention_act, self.cite_act):
            if act is not None:
                act.setVisible(has_context)
