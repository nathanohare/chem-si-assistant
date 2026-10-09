"""Figures and tables inside custom sections and General Information (SPEC §13k.5).

In the editor each figure or table is one object character followed by its caption on
the same paragraph (see ui/rich_text.py). This module:

- `ObjectStore`: reads and writes one text owner's figures and tables (through
  core/project.py) and works out their labels, following unsaved edits;
- `FigureObject` / `TableObject`: draw them in the editor (the picture or a preview of
  the table, with the label; a label used twice is shown in orange);
- `object_menu`, `edit_table`, `new_table`, `choose_figure_file`, `edit_width`: the
  commands.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPen, QPyTextObject,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
                           QTextOption)
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox, QWidget

from ..compile.qt_images import image_info, read_image
from ..core import markup
from ..core.labels import compute
from ..core.markup import Paragraph, Run
from ..core.project import FIGURE_EXTENSIONS, Project, ProjectError, figure_width_cm
from ..core.tables import Table, block_from_text
from . import rich_text
from .rich_text import DUPLICATE_COLOUR, OBJECT_ID

LABEL_GAP = 4                 # pixels between a picture and its label
TOP_GAP = 22                  # space above a figure or table (sets it apart from the
                              # caption of the one before)
MAX_FIGURE_WIDTH = 520        # pixels on screen
MAX_TABLE_WIDTH = 640
PREVIEW_ROWS = 12             # table rows drawn in the editor (the rest are counted)
THUMB_SIDE = 1600             # pictures are shown from a copy at most this large


class ObjectStore:
    """The figures and tables of one text owner (a custom section, or "general")."""

    def __init__(self, project: Project, owner_key: str):
        self.project = project
        self.owner = project.text_owner(owner_key)
        self.folder = self.owner.folder
        self.editable = not project.read_only
        # The editor's current text (set by RichTextEdit.enable_objects), so labels follow
        # figures and tables added, moved or deleted before the text is saved.
        self.live_text: Callable[[], str] | None = None
        self._labels = None
        self._figures: dict | None = None
        self._tables: dict[str, tuple[tuple, Table | None]] = {}

    # ---- labels

    def changed(self) -> None:
        """The text was edited (or something was saved): work the labels out again."""
        self._labels = None

    def labels(self):
        if self._labels is None:
            live = {self.owner.key: self.live_text()} if self.live_text is not None else None
            self._labels = compute(self.project, live)
        return self._labels

    def label(self, kind: str, item_id: str) -> tuple[str, bool]:
        """(label shown, used by another item too)."""
        labels = self.labels()
        item = labels.by_id.get(item_id)
        if item is None:
            return ("Figure ?" if kind == "figure" else "Table ?"), False
        return item.label, labels.is_duplicate(item.label)

    def auto_label(self, item_id: str) -> str:
        item = self.labels().by_id.get(item_id)
        return item.auto if item is not None else ""

    def _saved(self) -> None:
        self._figures = None
        self._labels = None
        ctx = rich_text.link_context()
        if ctx is not None:
            ctx.invalidate()

    # ---- figures

    def figures(self) -> dict[str, dict[str, str]]:
        if self._figures is None:
            self._figures = self.project.figures(self.owner)
        return self._figures

    def figure_path(self, figure_id: str) -> Path | None:
        name = self.figures().get(figure_id, {}).get("file")
        return self.folder / name if name else None

    def figure_label(self, figure_id: str) -> str:
        """The typed-over label ("" = automatic)."""
        return self.figures().get(figure_id, {}).get("label", "")

    def figure_width(self, figure_id: str) -> float | None:
        """The printed width typed for a figure, in cm (None = the picture's own size)."""
        return figure_width_cm(self.figures().get(figure_id, {}))

    def set_figure_width(self, figure_id: str, width_cm: float | None) -> None:
        with self.project.undo_step(f"Change the size of {self.label('figure', figure_id)[0]}"):
            self.project.set_figure_width(self.owner, figure_id, width_cm)
        self._saved()

    def adding(self, label: str):
        """One undo step for figures or tables put into the text: the text's next autosave
        (with their new paragraphs) joins it, so undo takes both away together."""
        return self.project.undo_step(label, then_merge=True)

    def add_file(self, path: Path) -> str:
        # A picture file chosen by the user: no "ChemDraw original" (a same-named .pdf
        # next to it, e.g. an instrument report, is not one).
        with self.adding(f"Add the figure {Path(path).name}"):
            figure_id = self.project.add_figure(self.owner, source=Path(path))
        self._saved()
        return figure_id

    def add_pasted(self, picture) -> str:
        """A picture from the clipboard (ui/clipboard_image.PastedPicture): the PNG, and
        ChemDraw's vector original when there is one (kept for re-rendering)."""
        with self.adding("Paste a figure"):
            figure_id = self.project.add_figure(self.owner, data=picture.data, ext=picture.ext,
                                                vector=picture.source,
                                                vector_ext=picture.source_ext)
        self._saved()
        return figure_id

    def add_image(self, image: QImage) -> str:
        from .clipboard_image import image_to_png
        with self.adding("Paste a figure"):
            figure_id = self.project.add_figure(self.owner, data=image_to_png(image),
                                                ext=".png")
        self._saved()
        return figure_id

    def replace_picture(self, figure_id: str, path: Path | None = None, picture=None) -> None:
        with self.project.undo_step(f"Replace the picture of {self.label('figure', figure_id)[0]}"):
            if path is not None:   # (a user's file: no vector original, as for add_file)
                self.project.replace_figure_picture(self.owner, figure_id, source=Path(path))
            else:
                self.project.replace_figure_picture(self.owner, figure_id, data=picture.data,
                                                    ext=picture.ext, vector=picture.source,
                                                    vector_ext=picture.source_ext)
        self._saved()

    # ---- tables

    def table(self, table_id: str) -> Table | None:
        path = self.project.table_path(self.owner, table_id)
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        hit = self._tables.get(table_id)
        if hit is None or hit[0] != key:
            hit = (key, self.project.read_table(self.owner, table_id))
            self._tables[table_id] = hit
        return hit[1]

    def new_table(self, table: Table | None = None) -> str:
        with self.adding("Add a table"):
            table_id = self.project.add_table(self.owner, table)
        self._saved()
        return table_id

    def save_table(self, table: Table) -> None:
        with self.project.undo_step(f"Edit {self.label('table', table.id)[0]}"):
            self.project.write_table(self.owner, table)
        self._tables.pop(table.id, None)
        self._saved()

    # ---- both

    def set_label(self, kind: str, item_id: str, label: str) -> None:
        with self.project.undo_step(f"Change the label of {self.label(kind, item_id)[0]}"):
            if kind == "figure":
                self.project.set_figure_label(self.owner, item_id, label)
            else:
                table = self.table(item_id) or Table.new(table_id=item_id)
                table.label = label.strip()
                self.project.write_table(self.owner, table)
                self._tables.pop(item_id, None)
        self._saved()

    def remove(self, kind: str, item_id: str, caption: str = "") -> None:
        if kind == "figure":
            self.project.remove_figure(self.owner, item_id, caption)
        else:
            self.project.remove_table(self.owner, item_id, caption)
            self._tables.pop(item_id, None)
        self._saved()

    def export(self, kind: str, item_id: str) -> dict | None:
        """What the clipboard needs to paste a copy of this figure or table elsewhere."""
        if kind == "figure":
            path = self.figure_path(item_id)
            if path is None or not path.exists():
                return None
            vector = self.project.figure_source(self.owner, item_id)
            return {"kind": "figure", "file": str(path), "label": self.figure_label(item_id),
                    "vector": str(vector) if vector is not None else "",
                    "width": self.figure_width(item_id) or 0}
        table = self.table(item_id)
        return {"kind": "table", "table": table.to_json()} if table is not None else None

    def adopt(self, kind: str, item_id: str, info: dict | None,
              in_document: set[str]) -> str | None:
        """The ID to use for a pasted figure or table. Cut and pasted (it is no longer in
        any text): the same item, so mentions of it stay right. Copied: a new copy with a
        new ID. None if it can't be pasted here (then only its caption is pasted)."""
        if not self.editable:
            return None
        if kind == "figure":
            own = item_id in self.figures()
        else:
            own = self.project.table_path(self.owner, item_id).exists()
        if own and item_id not in in_document:
            return item_id
        if not info or info.get("kind") != kind:
            return None
        elsewhere = item_id in in_document or (
            self.project.object_owners().get(item_id) not in (None, self.owner.key))
        keep = "" if elsewhere or own else item_id
        try:
            if kind == "figure":
                path = Path(str(info.get("file", "")))
                if not path.is_file():
                    return None
                vector = Path(str(info["vector"])) if info.get("vector") else None
                new = self.project.add_figure(
                    self.owner, source=path,
                    vector=vector if vector is not None and vector.is_file() else None,
                    label=str(info.get("label", "")), figure_id=keep,
                    width=figure_width_cm(info))
            else:
                table = Table.from_json(info.get("table"))
                table.id = keep
                new = self.project.add_table(self.owner, table)
        except (ProjectError, OSError):
            return None
        self._saved()
        return new


# ---------------------------------------------------------------------- pictures on screen

THUMB_COUNT = 60                    # pictures kept for the screen, at most
THUMB_BYTES = 192 * 1024 * 1024     # and at most this much memory
_thumbs: OrderedDict = OrderedDict()
_inches: OrderedDict = OrderedDict()


def thumbnail(path: Path) -> QImage:
    """A copy of the picture for the screen (pasted pictures are 1200 dpi), kept until
    the file changes. PDFs show their first page (at 100 dpi: picture_inches relies on
    it)."""
    try:
        st = path.stat()
    except OSError:
        return QImage()
    name = str(path)
    key = (name, st.st_mtime_ns, st.st_size)
    image = _thumbs.get(key)
    if image is not None:
        _thumbs.move_to_end(key)   # (the least recently shown goes first)
        return image
    if path.suffix.lower() == ".pdf":
        from .clipboard_image import render_pdf
        try:
            image = render_pdf(path.read_bytes(), 100) or QImage()
        except OSError:
            image = QImage()
    else:
        image = read_image(path, THUMB_SIDE)
    if image.isNull():
        return image   # (not kept: a file being synced or locked is read again next time)
    for old in [k for k in _thumbs if k[0] == name]:   # older versions of this file
        del _thumbs[old]
    _thumbs[key] = image
    total = sum(i.sizeInBytes() for i in _thumbs.values())
    # (the newest is always kept, even alone over the limit: not read again on every paint)
    while len(_thumbs) > 1 and (len(_thumbs) > THUMB_COUNT or total > THUMB_BYTES):
        _old, dropped = _thumbs.popitem(last=False)
        total -= dropped.sizeInBytes()
    return image


def picture_inches(path: Path) -> tuple[float, float] | None:
    """The printed size of a picture in inches, if it is known. Kept until the file
    changes: reading a JPEG's or TIFF's resolution decodes the whole picture, and this is
    asked on every layout and paint."""
    if path.suffix.lower() == ".pdf":
        image = thumbnail(path)
        return (image.width() / 100, image.height() / 100) if not image.isNull() else None
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    size = _inches.get(key)
    if size is not None:
        _inches.move_to_end(key)
        return size
    info = image_info(path)
    if info is None:
        return None   # (not kept: a file being synced or locked is read again next time)
    w, h, dpi = info
    size = _inches[key] = (w / dpi, h / dpi)
    while len(_inches) > 200:
        _inches.popitem(last=False)
    return size


def _available_width(doc) -> float:
    width = doc.textWidth()
    return width - 2 * doc.documentMargin() - 8 if width > 0 else MAX_TABLE_WIDTH


def _label_font(edit) -> QFont:
    font = QFont(edit.font())
    font.setBold(True)
    return font


def _selected_ink(fmt: QTextCharFormat) -> QColor | None:
    """While a figure or table is selected, Qt gives it the selection's text colour (its
    own format has none): its label is then drawn in that colour, to be readable on the
    selection's highlight."""
    if fmt.hasProperty(QTextFormat.Property.ForegroundBrush):
        return fmt.foreground().color()
    return None


def _draw_label(painter, rect: QRectF, text: str, duplicate: bool, font: QFont,
                align=Qt.AlignmentFlag.AlignLeft, ink: QColor | None = None) -> None:
    painter.setFont(font)
    if ink is None:   # (light or dark)
        ink = QColor(DUPLICATE_COLOUR) if duplicate else QColor("#3a7bd5")
    painter.setPen(ink)
    shown = text + ("   ⚠ another item has the same label" if duplicate else "")
    painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, shown)


def _object_alignment(doc, pos: int) -> str:
    """How the figure or table at `pos` is aligned (its paragraph's alignment, §13l.10)."""
    return markup.effective_align(rich_text.block_align(doc.findBlock(pos)), True)


def _label_box(rect: QRectF, top: float, height: float, align: str):
    """Where an object's label goes (under a figure, over a table), following its
    alignment: (box, text alignment). The box may be wider than the object (a label with
    "⚠ another item has the same label" is), staying within the line."""
    extra = 400
    if align == "center":
        return (QRectF(rect.center().x() - rect.width() / 2 - extra / 2, top,
                       rect.width() + extra, height), Qt.AlignmentFlag.AlignHCenter)
    if align == "right":
        return (QRectF(rect.right() - rect.width() - extra, top, rect.width() + extra, height),
                Qt.AlignmentFlag.AlignRight)
    return QRectF(rect.left(), top, rect.width() + extra, height), Qt.AlignmentFlag.AlignLeft


class FigureObject(QPyTextObject):
    """A figure in the editor: the picture (at about its printed size), its label under it."""

    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit

    def _picture_size(self, doc, figure_id: str) -> tuple[float, float]:
        store = self.edit.objects
        path = store.figure_path(figure_id) if store is not None else None
        if path is None or not path.exists():
            return 300.0, 60.0
        size = picture_inches(path)
        dpi = self.edit.logicalDpiX() or 96
        if size is None:
            image = thumbnail(path)
            w, h = (image.width(), image.height()) if not image.isNull() else (300, 60)
        else:
            w, h = size[0] * dpi, size[1] * dpi
        width_cm = store.figure_width(figure_id)
        if width_cm:   # a printed width typed for it ("Printed size…")
            new_w = width_cm / 2.54 * dpi
            w, h = new_w, h * new_w / max(1.0, w)
        limit = min(MAX_FIGURE_WIDTH, _available_width(doc))
        scale = min(1.0, limit / max(1.0, w))
        return max(20.0, w * scale), max(20.0, h * scale)

    def intrinsicSize(self, doc, pos, fmt):  # noqa: N802 (Qt naming)
        cf = QTextCharFormat(fmt)
        w, h = self._picture_size(doc, str(cf.property(OBJECT_ID) or ""))
        label_h = QFontMetricsF(_label_font(self.edit)).height() + LABEL_GAP
        return QSizeF(max(w, 160.0), TOP_GAP + h + label_h)

    def drawObject(self, painter, rect, doc, pos, fmt):  # noqa: N802
        cf = QTextCharFormat(fmt)
        figure_id = str(cf.property(OBJECT_ID) or "")
        store = self.edit.objects
        w, h = self._picture_size(doc, figure_id)
        rect = QRectF(rect).adjusted(0, TOP_GAP, 0, 0)
        align = _object_alignment(doc, pos)
        # (a picture narrower than the object's smallest width sits as it is aligned)
        offset = {"center": (rect.width() - w) / 2, "right": rect.width() - w}.get(align, 0.0)
        picture = QRectF(rect.left() + max(0.0, offset), rect.top(), w, h)
        painter.save()
        path = store.figure_path(figure_id) if store is not None else None
        image = thumbnail(path) if path is not None else QImage()
        if image.isNull():
            painter.setPen(QPen(QColor("#999999"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(picture.adjusted(1, 1, -1, -1))
            painter.drawText(picture, Qt.AlignmentFlag.AlignCenter,
                             "Picture missing" + (f": {path.name}" if path is not None else ""))
        else:
            painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(picture, image)
        label, duplicate = store.label("figure", figure_id) if store is not None else ("Figure",
                                                                                         False)
        label_rect, label_align = _label_box(rect, picture.bottom() + LABEL_GAP,
                                             rect.bottom() - picture.bottom() - LABEL_GAP, align)
        _draw_label(painter, label_rect, label, duplicate, _label_font(self.edit), label_align,
                    _selected_ink(cf))
        painter.restore()


def cell_plain(text: str) -> str:
    """A cell's text as shown in the preview: links as they look (3a, Table S1)."""
    out = []
    for p in markup.parse(text):
        out.append("".join(rich_text.link_label(r.ref) if r.ref else r.text for r in p.runs))
    return " / ".join(x for x in out if x)


_markup_docs: OrderedDict = OrderedDict()


def _draw_markup(painter, box: QRectF, text: str, font: QFont, align, colour: QColor) -> None:
    """Draw a cell's (or footnote's) stored text in `box` on one line, with its formatting,
    centred vertically and cut off at the box's edge. The laid-out text is kept for the
    next paint (a table is drawn again on every key typed above it), found by what is
    drawn: the HTML (with the links' current labels and the colour), font, alignment and
    width, so it can never show something old."""
    from .table_editor import markup_html
    html = (f"<span style='color:{colour.name()}'>"
            + markup_html(text).replace("<br>", " / ") + "</span>")
    key = (html, font.key(), int(getattr(align, "value", align)), round(box.width(), 3))
    doc = _markup_docs.get(key)
    if doc is None:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(font)
        option = QTextOption(align)
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        doc.setDefaultTextOption(option)
        doc.setHtml(html)
        doc.setTextWidth(box.width())
        _markup_docs[key] = doc
        while len(_markup_docs) > 1000:
            _markup_docs.popitem(last=False)
    else:
        _markup_docs.move_to_end(key)
    painter.save()
    painter.setClipRect(box)
    painter.translate(box.left(), box.top() + max(0.0, (box.height() - doc.size().height()) / 2))
    doc.drawContents(painter, QRectF(0, 0, box.width(), box.height()))
    painter.restore()


class TableObject(QPyTextObject):
    """A table in the editor: its label, then a small preview of the grid (and footnotes).
    Double-click to edit it."""

    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit

    def _layout(self, doc, table: Table | None):
        fm = QFontMetricsF(self.edit.font())
        row_h = fm.height() + 6
        width = min(MAX_TABLE_WIDTH, _available_width(doc))
        label_h = QFontMetricsF(_label_font(self.edit)).height() + LABEL_GAP
        rows = min(PREVIEW_ROWS, table.n_rows) if table is not None else 0
        extra = 0
        if table is not None:
            extra += 1 if table.n_rows > PREVIEW_ROWS else 0
            extra += min(3, len(table.footnotes))
        hint_h = fm.height() + 4
        return width, label_h, row_h, rows, extra, hint_h

    def intrinsicSize(self, doc, pos, fmt):  # noqa: N802
        cf = QTextCharFormat(fmt)
        store = self.edit.objects
        table = store.table(str(cf.property(OBJECT_ID) or "")) if store is not None else None
        width, label_h, row_h, rows, extra, hint_h = self._layout(doc, table)
        body = rows * row_h if table is not None else row_h * 2
        return QSizeF(width, TOP_GAP + label_h + body + extra * (row_h - 4) + hint_h + 4)

    def drawObject(self, painter, rect, doc, pos, fmt):  # noqa: N802
        cf = QTextCharFormat(fmt)
        table_id = str(cf.property(OBJECT_ID) or "")
        store = self.edit.objects
        table = store.table(table_id) if store is not None else None
        width, label_h, row_h, rows, extra, hint_h = self._layout(doc, table)
        rect = QRectF(rect).adjusted(0, TOP_GAP, 0, 0)
        x0, y = rect.left(), rect.top()
        painter.save()
        label, duplicate = store.label("table", table_id) if store is not None else ("Table",
                                                                                       False)
        label_rect, label_align = _label_box(QRectF(x0, y, width, rect.height()), y,
                                             label_h - LABEL_GAP, _object_alignment(doc, pos))
        _draw_label(painter, label_rect, label, duplicate, _label_font(self.edit), label_align,
                    _selected_ink(cf))
        y += label_h
        font = QFont(self.edit.font())
        grey = QColor("#888888")
        if table is None:
            painter.setPen(QPen(grey, 1, Qt.PenStyle.DashLine))
            box = QRectF(x0, y, width, row_h * 2)
            painter.drawRect(box.adjusted(1, 1, -1, -1))
            painter.setFont(font)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "Table missing")
            painter.restore()
            return
        # Column widths in proportion to their longest text.
        lengths = [3] * table.n_cols
        for r, c, cell in table.cells():
            if cell.colspan == 1 and r < PREVIEW_ROWS:
                lengths[c] = max(lengths[c], min(40, len(cell_plain(cell.text))))
        total = sum(lengths) or 1
        xs = [x0]
        for n in lengths:
            xs.append(xs[-1] + width * n / total)
        top = y
        bold = QFont(font)
        bold.setBold(True)
        if table.header_rows:
            painter.fillRect(QRectF(x0, top, width, row_h * min(table.header_rows, rows)),
                             QColor(127, 127, 127, 30))
        for r, c, cell in table.cells():
            if r >= rows:
                continue
            last_row = min(rows, r + cell.rowspan)
            box = QRectF(xs[c], top + r * row_h, xs[c + cell.colspan] - xs[c],
                         (last_row - r) * row_h)
            painter.setPen(QPen(QColor(127, 127, 127, 70), 1))
            painter.drawRect(box)
            painter.setPen(self.edit.palette().text().color())
            align = cell.align or table.columns[c]
            flag = {"left": Qt.AlignmentFlag.AlignLeft, "right": Qt.AlignmentFlag.AlignRight
                    }.get(align, Qt.AlignmentFlag.AlignHCenter)
            # With its formatting (sub- and superscripts, italic), as in the table editor,
            # so "CH₂Cl₂" or a footnote mark "ᵃ" don't look like typing mistakes.
            _draw_markup(painter, box.adjusted(4, 0, -4, 0), cell.text,
                         bold if r < table.header_rows else font, flag,
                         self.edit.palette().text().color())
        # Rules as printed: top, under the header, bottom.
        ink = QPen(self.edit.palette().text().color(), 1.4)
        painter.setPen(ink)
        bottom = top + rows * row_h
        painter.drawLine(int(x0), int(top), int(x0 + width), int(top))
        if table.header_rows and table.header_rows < rows:
            mid = top + table.header_rows * row_h
            painter.setPen(QPen(ink.color(), 0.8))
            painter.drawLine(int(x0), int(mid), int(x0 + width), int(mid))
            painter.setPen(ink)
        painter.drawLine(int(x0), int(bottom), int(x0 + width), int(bottom))
        y = bottom + 2
        small = QFont(font)
        small.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        painter.setFont(small)
        painter.setPen(grey)
        if table.n_rows > PREVIEW_ROWS:
            painter.drawText(QRectF(x0, y, width, row_h - 4),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"… {table.n_rows - PREVIEW_ROWS} more rows")
            y += row_h - 4
        for note in table.footnotes[:3]:
            _draw_markup(painter, QRectF(x0, y, width, row_h - 4), note, small,
                         Qt.AlignmentFlag.AlignLeft, grey)
            y += row_h - 4
        painter.drawText(QRectF(x0, y, width, hint_h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"▼ Caption of {label}, on the next line (printed above the table)"
                         "   ·   double-click the table to edit it")
        painter.restore()


# ---------------------------------------------------------------------- commands

def caption_runs(text: str) -> list[Run]:
    """A caption typed in the table editor as one line of runs."""
    runs: list[Run] = []
    for p in markup.parse(text):
        if p.runs:
            runs += ([Run(" ")] if runs else []) + p.runs
    return runs


def caption_markup(edit, kind: str, item_id: str) -> str:
    runs = edit.object_caption(kind, item_id)
    return markup.serialize([Paragraph(runs)]) if runs else ""


def object_menu(edit, kind: str, item_id: str) -> QMenu:
    """The right-click menu of a figure or table."""
    store = edit.objects
    menu = QMenu(edit)
    ro = store is None or not store.editable or edit.isReadOnly()
    if kind == "table":
        act = menu.addAction("Edit table…", lambda: edit_table(edit, item_id))
        act.setEnabled(not ro)
    act = menu.addAction("Edit label…", lambda: edit_label(edit, kind, item_id))
    act.setEnabled(not ro)
    if kind == "figure":
        act = menu.addAction("Printed size…", lambda: edit_width(edit, item_id))
        act.setEnabled(not ro)
        act = menu.addAction("Replace picture with a file…", lambda: replace_with_file(edit,
                                                                                       item_id))
        act.setEnabled(not ro)
        act = menu.addAction("Replace picture with the copied picture",
                             lambda: replace_with_clipboard(edit, item_id))
        act.setEnabled(not ro)
    menu.addSeparator()
    what = "figure (picture to the Trash)" if kind == "figure" else "table (to the Trash)"
    act = menu.addAction(f"Remove {what}", lambda: remove_object(edit, kind, item_id))
    act.setEnabled(not ro)
    return menu


def edit_label(edit, kind: str, item_id: str) -> None:
    store = edit.objects
    current = (store.figure_label(item_id) if kind == "figure"
               else (store.table(item_id).label if store.table(item_id) else ""))
    auto = store.auto_label(item_id) or ("Figure S?" if kind == "figure" else "Table S?")
    text, ok = QInputDialog.getText(
        edit, "Edit label",
        f"The label printed for this {kind} (and wherever it is mentioned).\n"
        f"Leave it empty to number it automatically (now: {auto}).", text=current)
    if not ok:
        return
    try:
        store.set_label(kind, item_id, text)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not save the label", str(e))
        return
    edit.refresh_objects()


def parse_cm(text: str) -> float | None:
    """A width typed in cm ("8", "8.5", "8,5 cm"); None if empty. Raises ValueError."""
    text = text.strip().lower().removesuffix("cm").strip().replace(",", ".")
    if not text:
        return None
    value = float(text)
    if not 0.2 <= value <= 100:
        raise ValueError(text)
    return value


def edit_width(edit, figure_id: str) -> None:
    """Right-click ▸ Printed size…: the width the figure is printed at, in cm. Empty = the
    picture's own size (a ChemDraw paste prints at its ChemDraw size)."""
    store = edit.objects
    current = store.figure_width(figure_id)
    path = store.figure_path(figure_id)
    own = picture_inches(path) if path is not None and path.exists() else None
    own_text = f"about {own[0] * 2.54:.1f} cm" if own else "unknown"
    text, ok = QInputDialog.getText(
        edit, "Printed size",
        "Width of this figure in the SI, in cm. It is never printed wider than the text\n"
        "(about 16.5 cm on a US Letter page).\n"
        f"Leave it empty to use the picture's own size ({own_text}).",
        text=f"{current:g}" if current else "")
    if not ok:
        return
    try:
        width = parse_cm(text)
    except ValueError:
        QMessageBox.warning(edit, "Printed size", f"“{text}” is not a width in cm (e.g. 8.5).")
        return
    try:
        store.set_figure_width(figure_id, width)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not save the size", str(e))
        return
    edit.refresh_objects()


def choose_figure_file(parent: QWidget) -> Path | None:
    patterns = " ".join(f"*{e}" for e in FIGURE_EXTENSIONS)
    path, _ = QFileDialog.getOpenFileName(parent, "Insert a figure", "",
                                          f"Pictures ({patterns})")
    return Path(path) if path else None


def insert_figure_file(edit) -> None:
    store = edit.objects
    if store is None or not store.editable:
        return
    path = choose_figure_file(edit)
    if path is None:
        return
    save_typing_first(edit)
    try:
        figure_id = store.add_file(path)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not add the picture", str(e))
        return
    edit.insert_object("figure", figure_id)


def replace_with_file(edit, figure_id: str) -> None:
    path = choose_figure_file(edit)
    if path is None:
        return
    try:
        edit.objects.replace_picture(figure_id, path=path)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not replace the picture", str(e))
        return
    edit.refresh_objects()


def replace_with_clipboard(edit, figure_id: str) -> None:
    from .clipboard_image import clipboard_picture
    picture = clipboard_picture(getattr(edit, "paste_dpi", 1200))
    if picture is None:
        QMessageBox.information(edit, "Replace picture", "There is no picture on the "
                                "clipboard. Copy one (e.g. a scheme in ChemDraw) first.")
        return
    try:
        edit.objects.replace_picture(figure_id, picture=picture)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not replace the picture", str(e))
        return
    edit.refresh_objects()


def save_typing_first(edit) -> bool:
    """Before adding or removing a figure or table: save typing not saved yet (the page's
    autosave), so it stays an undo step of its own and is not undone with the figure.
    Returns False if that save failed: the page has said so, and the typing stays on the
    page, tried again with the next change or when the page is left."""
    page = edit.parentWidget()
    while page is not None and not callable(getattr(page, "flush", None)):
        page = page.parentWidget()
    if page is None:
        return True
    save_waiting = getattr(page, "save_waiting", None)
    if callable(save_waiting):
        return save_waiting() is not False
    # The pages with figures and tables (a text section, General Information): their
    # ordinary autosave. Not flush(), the save made when leaving the page, whose warning
    # says the changes are lost if the page is closed (here the page stays open).
    autosave = getattr(page, "_save_text", None) or getattr(page, "_save", None)
    timer = getattr(page, "_timer", None)
    if callable(autosave) and timer is not None and hasattr(page, "_unsaved"):
        if timer.isActive() or page._unsaved:
            autosave()   # (says so itself if it fails)
        return not page._unsaved
    try:
        page.flush()
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not save", f"{e}\n\nWhat you typed is still on the "
                            "page; saving it is tried again with your next change or when "
                            "you leave the page.")
        return False
    return True


def remove_object(edit, kind: str, item_id: str) -> None:
    store = edit.objects
    label, _dup = store.label(kind, item_id)
    what = "picture" if kind == "figure" else "table"
    from .undo_keys import key_text
    if QMessageBox.question(edit, f"Remove {kind}",
                            f"Remove {label}? The {what} goes to the project Trash (with its "
                            "caption), and restoring it puts it back at the end of this text.\n\n"
                            f"Edit ▸ Undo ({key_text()}) brings it back in its place."
                            ) != QMessageBox.StandardButton.Yes:
        return
    if not save_typing_first(edit):
        # Removed now, the typing still waiting would be saved as part of the removal, and
        # Edit ▸ Undo of the removal would take it back too. Nothing is lost by waiting.
        QMessageBox.warning(edit, f"Remove {kind}",
                            f"{label} was not removed, because your latest typing on this "
                            "page could not be saved first (see the previous message). "
                            "Try again once saving works; nothing has been lost.")
        return
    caption = caption_markup(edit, kind, item_id)
    # One undo step for the picture or table and its paragraph: the text's autosave that
    # follows joins it (then_merge: that one only, so typing after it is a step of its own).
    with store.project.undo_step(f"Remove {label}", then_merge=True):
        try:
            store.remove(kind, item_id, caption)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(edit, f"Could not remove the {kind}", str(e))
            return
    edit.remove_object_paragraph(kind, item_id)
    # The file is already in the Trash, which the text box's own Ctrl+Z cannot reach: it
    # would bring back the paragraph without its picture or table. So the text's typing
    # history ends here, and Ctrl+Z goes to Edit ▸ Undo, which puts back both.
    edit.document().clearUndoRedoStacks()


def edit_table(edit, table_id: str) -> None:
    """Open the table editor for a table in the text; save it when OK is pressed."""
    from .table_editor import TableEditorDialog
    store = edit.objects
    if store is None or not store.editable or edit.isReadOnly():
        return
    table = store.table(table_id)
    if table is None:
        QMessageBox.warning(edit, "Edit table", "This table's file is missing from the project "
                            "folder, so it cannot be edited.")
        return
    dlg = TableEditorDialog(table, caption_markup(edit, "table", table_id),
                            store.auto_label(table_id), edit)
    if not dlg.exec():
        return
    new = dlg.result_table()
    new.id = table_id
    try:
        store.save_table(new)
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not save the table", str(e))
        return
    edit.set_object_caption("table", table_id, caption_runs(dlg.caption()))
    edit.refresh_objects()


def new_table(edit, block=None) -> None:
    """Toolbar "Table…": make a table in the editor, then put it in the text."""
    from .table_editor import TableEditorDialog
    store = edit.objects
    if store is None or not store.editable or edit.isReadOnly():
        return
    table = table_from_block(block, edit) if block else Table.new()
    dlg = TableEditorDialog(table, "", "", edit, new=True)
    if not dlg.exec():
        return
    if block is None:   # (from the toolbar; a paste is still being handled otherwise)
        save_typing_first(edit)
    try:
        table_id = store.new_table(dlg.result_table())
    except (ProjectError, OSError) as e:
        QMessageBox.warning(edit, "Could not save the table", str(e))
        return
    edit.insert_object("table", table_id, caption_runs(dlg.caption()))


def table_from_block(block, parent: QWidget | None = None) -> Table:
    """A new table from cells copied from Word or Excel (the first row as its header). With
    a `parent`, says so if the block was too large to paste whole."""
    table = Table.new(1, 1)
    left_out = table.paste(0, 0, block)
    table.header_rows = 1 if table.n_rows > 1 else 0
    if parent is not None:
        from .table_editor import warn_left_out
        warn_left_out(parent, left_out)
    return table


def html_with_tables(edit, doc) -> list[Paragraph] | None:
    """Pasted HTML (already read into the QTextDocument `doc`) as paragraphs, each table in
    it becoming a table of this text, in its place, with the text around it kept.

    Only a table (e.g. cells copied from Excel or a table from Word): it is opened in the
    table editor first and put in the text from there; returns None. A "table" of one cell
    (e.g. one Excel cell) is pasted as its text."""
    from .table_editor import block_from_qtable
    pieces: list = []          # Paragraphs, and blocks of cells (lists) for tables
    seen: set[int] = set()
    block = doc.begin()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is None:
            end = block.position() + block.length() - 1
            pieces += rich_text.document_to_paragraphs(doc, block.position(), end)
        elif table.firstPosition() not in seen:
            seen.add(table.firstPosition())
            cells = block_from_qtable(doc, table)
            if cells and (len(cells) > 1 or len(cells[0]) > 1):
                pieces.append(cells)
            else:   # one cell: just its text
                pieces += [p for p in rich_text.document_to_paragraphs(
                    doc, table.firstPosition(), table.lastPosition()) if p.runs]
        block = block.next()
    while seen and pieces and isinstance(pieces[0], Paragraph) and not (
            pieces[0].runs or pieces[0].is_object):
        pieces.pop(0)   # (Qt's own empty line before a table at the start)
    tables = [p for p in pieces if isinstance(p, list)]
    if not tables:
        return pieces
    text = [p for p in pieces if isinstance(p, Paragraph) and (p.runs or p.is_object)]
    if len(tables) == 1 and not text:
        new_table(edit, tables[0])
        return None
    out: list[Paragraph] = []
    with edit.objects.adding("Paste tables"):   # (one undo step, with the text)
        for piece in pieces:
            if isinstance(piece, Paragraph):
                if not (piece.runs or piece.is_object) and out and out[-1].table:
                    continue   # (Qt's own empty line after a table)
                out.append(piece)
                continue
            try:
                out.append(Paragraph([], 0,
                                     table=edit.objects.new_table(table_from_block(piece, edit))))
            except (ProjectError, OSError) as e:
                QMessageBox.warning(edit, "Could not save the table", str(e))
    return out


def table_block_from_mime(source) -> list | None:
    """A table copied from Word or Excel (HTML table, or tab-separated text), or None."""
    if source.hasHtml() and "<table" in source.html().lower():
        from .table_editor import block_from_html
        block = block_from_html(source.html())
        if block:
            return block
    if source.hasText() and "\t" in source.text():
        return block_from_text(source.text())
    return None
