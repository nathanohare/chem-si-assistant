"""The table editor (SPEC §13k.5): a grid like a spreadsheet, with a box under it for the
selected cell's text (bold, italic, super/subscript, mentions, citations).

Rows and columns can be added and deleted, cells merged and split, header rows chosen
(ruled off and repeated after a page break), each column or cell aligned, footnotes added,
and a table pasted from Word or Excel. A cell can also hold a picture (a structure or
scheme pasted from ChemDraw, or a picture file), printed above its text. The table itself is core/tables.Table; this dialog
only edits a copy of it, so Cancel leaves everything as it was (after asking, if anything
was changed: Escape pressed twice must not throw a table away).
"""

from __future__ import annotations

import html
from dataclasses import replace
import json

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QMimeData, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QGuiApplication, QImage, QKeySequence, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
                               QTableWidgetItem, QToolBar, QVBoxLayout, QWidget)

from ..core import markup
from ..core.markup import SUB, SUPER, Paragraph, mention_kind
from ..core.tables import Cell, Table
from . import rich_text
from .format_widgets import optional_box, set_value, size_box, value_of
from .rich_text import RichTextEditor, document_to_paragraphs
from .structure_widgets import paste_key

TEXT_ROLE = Qt.ItemDataRole.UserRole          # the cell's stored text
ALIGN_ROLE = Qt.ItemDataRole.UserRole + 1     # "left" / "center" / "right"
IMAGE_ROLE = Qt.ItemDataRole.UserRole + 2     # the cell's picture ("images/x.svg") or ""
SIZE_ROLE = Qt.ItemDataRole.UserRole + 3      # the cell's own text size in pt, or None
GRID_PT = 10.0                                # the printed size the grid's font stands for
THUMB_HEIGHT = 90                             # pixels: a cell's picture in the grid
ALIGN_NAMES = {"left": "Left", "center": "Centre", "right": "Right"}


# ---------------------------------------------------------------------- showing cell text

def markup_html(text: str) -> str:
    """Stored text as simple HTML for showing in the grid (links as they look)."""
    lines = []
    for p in markup.parse(text):
        out = []
        for r in p.runs:
            if r.ref:
                piece = html.escape(rich_text.link_label(r.ref))
                if r.mention and mention_kind(r.mention) == "compound":
                    piece = f"<b>{piece}</b>"
                piece = f"<span style='color:#3a7bd5'>{piece}</span>"
            else:
                piece = html.escape(r.text)
            if r.valign == SUPER:
                piece = f"<sup>{piece}</sup>"
            elif r.valign == SUB:
                piece = f"<sub>{piece}</sub>"
            if r.italic:
                piece = f"<i>{piece}</i>"
            if r.bold:
                piece = f"<b>{piece}</b>"
            if r.underline:
                piece = f"<u>{piece}</u>"
            out.append(piece)
        lines.append("".join(out))
    return "<br>".join(lines)


class CellDelegate(QStyledItemDelegate):
    """Draws each cell's formatted text (the grid itself has no editor: the box under it
    edits the selected cell)."""

    def __init__(self, dialog: "TableEditorDialog"):
        super().__init__(dialog)
        self.dialog = dialog

    def _doc(self, index, width: float) -> QTextDocument:
        doc = QTextDocument()
        font = self.dialog.grid.font()
        size = index.data(SIZE_ROLE)
        if size and font.pointSizeF() > 0:   # a cell given its own size looks bigger/smaller
            font.setPointSizeF(font.pointSizeF() * float(size) / GRID_PT)
        doc.setDefaultFont(font)
        align = index.data(ALIGN_ROLE) or "center"
        css = {"left": "left", "right": "right"}.get(align, "center")
        doc.setHtml(f"<div align='{css}'>{markup_html(index.data(TEXT_ROLE) or '')}</div>")
        doc.setTextWidth(max(20.0, width))
        return doc

    def _thumb_size(self, image: QImage, width: float) -> QSize:
        """The picture's size in the grid: at most THUMB_HEIGHT high and the cell wide."""
        if image.isNull() or not image.height():
            return QSize(0, 0)
        scale = min(1.0, THUMB_HEIGHT / image.height(), max(20.0, width) / image.width())
        return QSize(max(1, int(image.width() * scale)), max(1, int(image.height() * scale)))

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QGuiApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        doc = self._doc(index, opt.rect.width() - 6)
        image = self.dialog.thumbnail(index.data(IMAGE_ROLE) or "")
        thumb = self._thumb_size(image, opt.rect.width() - 6)
        has_text = bool((index.data(TEXT_ROLE) or "").strip())
        height = thumb.height() + (doc.size().height() if has_text else 0)
        top = opt.rect.top() + max(0.0, (opt.rect.height() - height) / 2)
        if not thumb.isEmpty():   # the picture above the text, centred like the PDF's
            left = opt.rect.left() + (opt.rect.width() - thumb.width()) // 2
            painter.drawImage(QRect(left, int(top), thumb.width(), thumb.height()), image)
            top += thumb.height()
        if has_text or thumb.isEmpty():
            painter.save()
            painter.translate(opt.rect.left() + 3, top)
            doc.drawContents(painter)
            painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        # (Before the grid is laid out its columns have no width yet.)
        width = max(self.dialog.grid.columnWidth(index.column()), 120)
        doc = self._doc(index, width - 6)
        thumb = self._thumb_size(self.dialog.thumbnail(index.data(IMAGE_ROLE) or ""), width - 6)
        return QSize(max(int(doc.idealWidth()), thumb.width()) + 10,
                     int(doc.size().height()) + thumb.height() + 6)

    def createEditor(self, parent, option, index):  # noqa: N802
        return None


# ---------------------------------------------------------------------- pasting from Word/Excel

# Cells copied in the table editor, with their stored text (formatting, links) and merges.
CELLS_MIME = "application/x-si-assistant-cells"


def cells_json(block: list[list[Cell | None]], n_rows: int, n_cols: int) -> bytes:
    """A block of cells (None = covered by a merged cell) for the clipboard. Merges are cut
    to the block; pictures are left out (they belong to the table's own folder)."""
    rows = []
    for r, row in enumerate(block):
        out = []
        for c, cell in enumerate(row):
            if cell is None:
                out.append(None)
                continue
            copy = replace(cell, colspan=min(cell.colspan, n_cols - c),
                           rowspan=min(cell.rowspan, n_rows - r), image="", image_width=None)
            out.append(copy.to_json())
        rows.append(out)
    return json.dumps(rows).encode("utf-8")


def block_from_cells_json(data: bytes) -> list[list[Cell | None]] | None:
    """The cells put on the clipboard by cells_json, or None if they can't be read."""
    try:
        rows = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        return None
    block = [[None if x is None else Cell.from_json(x) for x in row] for row in rows]
    return block if any(c is not None for row in block for c in row) else None


def block_html(block: list[list[Cell | None]]) -> str:
    """A block of cells as an HTML table (for Word, Excel), keeping sub/superscripts."""
    rows = []
    for row in block:
        tds = []
        for cell in row:
            if cell is None:
                continue
            span = ((f" colspan='{cell.colspan}'" if cell.colspan > 1 else "")
                    + (f" rowspan='{cell.rowspan}'" if cell.rowspan > 1 else ""))
            tds.append(f"<td{span}>{markup_html(cell.text)}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def block_from_html(source: str) -> list[list[Cell | None]] | None:
    """The first table in some HTML (as Word and Excel put on the clipboard) as a block of
    cells, keeping bold/italic/super/subscript and merged cells. None if there is none."""
    from PySide6.QtGui import QTextTable
    doc = QTextDocument()
    doc.setHtml(source)
    frames = [doc.rootFrame()]
    found = None
    while frames and found is None:
        frame = frames.pop(0)
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                found = child
                break
            frames.append(child)
    if found is None:
        return None
    return block_from_qtable(doc, found)


def block_from_qtable(doc: QTextDocument, found) -> list[list[Cell | None]] | None:
    """One table of a QTextDocument (QTextTable) as a block of cells, or None if empty."""
    if found.rows() == 0 or found.columns() == 0:
        return None
    block: list[list[Cell | None]] = []
    for r in range(found.rows()):
        row: list[Cell | None] = []
        for c in range(found.columns()):
            cell = found.cellAt(r, c)
            if cell.row() != r or cell.column() != c:
                row.append(None)   # covered by a merged cell
                continue
            start = cell.firstCursorPosition().position()
            end = cell.lastCursorPosition().position()
            paragraphs = [p for p in document_to_paragraphs(doc, start, end) if p.runs]
            text = markup.serialize([Paragraph(p.runs) for p in paragraphs]).strip()
            row.append(Cell(text, max(1, cell.columnSpan()), max(1, cell.rowSpan())))
        block.append(row)
    return block


# ---------------------------------------------------------------------- the dialog

class TableEditorDialog(QDialog):
    def __init__(self, table: Table, caption: str = "", auto_label: str = "",
                 parent: QWidget | None = None, new: bool = False,
                 pictures: "CellPictures | None" = None):
        super().__init__(parent)
        # Where the cells' pictures are kept, and how new ones are stored (None: this table
        # cannot hold pictures, e.g. an X-ray structure's crystal data).
        self.pictures = pictures
        self._thumbs: dict[str, QImage] = {}
        self.setWindowTitle("New table" if new else "Edit table")
        self.resize(940, 760)
        self._table = table.copy()
        self._table.normalize()
        # A damaged or hand-edited file can hold a table with no rows or columns: one empty
        # cell to work on, so it can be repaired (the file changes only if OK is pressed).
        if not self._table.n_rows:
            self._table.insert_row(0)      # (first: works with no columns)
        if not self._table.n_cols:
            self._table.insert_column(0)
        self._loading = False
        self._editing: tuple[int, int] | None = None   # the cell in the box under the grid

        intro = QLabel("Click a cell and type (its text is edited in the box under the grid, "
                       "with bold, italic, sub/superscript and @ mentions). Select several "
                       f"cells to merge them. {paste_key()} pastes a table copied from Word "
                       "or Excel"
                       + (", or a structure copied from ChemDraw into the selected cell "
                          "(printed above its text)." if pictures is not None else "."))
        intro.setWordWrap(True)
        intro.setStyleSheet("color: palette(placeholder-text);")

        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.actions_: dict[str, QAction] = {}
        for key, text, tip, slot in (
                ("row_above", "+ Row above", "Insert a row above the selected cell",
                 lambda: self._insert_row(0)),
                ("row_below", "+ Row below", "Insert a row below the selected cell",
                 lambda: self._insert_row(1)),
                ("col_left", "+ Column left", "Insert a column left of the selected cell",
                 lambda: self._insert_column(0)),
                ("col_right", "+ Column right", "Insert a column right of the selected cell",
                 lambda: self._insert_column(1)),
                ("del_rows", "− Rows", "Delete the selected rows", self._delete_rows),
                ("del_cols", "− Columns", "Delete the selected columns", self._delete_columns),
                (None, None, None, None),
                ("merge", "Merge cells", "Merge the selected cells into one", self._merge),
                ("split", "Split cells", "Split the selected merged cell", self._split),
                (None, None, None, None),
                ("left", "Left", "Align the selected cells (whole columns: the column) left",
                 lambda: self._align("left")),
                ("center", "Centre", "Centre the selected cells (or columns)",
                 lambda: self._align("center")),
                ("right", "Right", "Align the selected cells (or columns) right",
                 lambda: self._align("right")),
                (None, None, None, None),
                ("paste", "Paste table", "Paste a table copied from Word or Excel at the "
                 "selected cell (the table grows as needed)", self.paste_table),
                *(((None, None, None, None),
                   ("picture_paste", "Paste picture", "Put the structure or picture on the "
                    "clipboard (e.g. copied from ChemDraw) into the selected cell, above its "
                    "text", self.paste_picture),
                   ("picture_file", "Picture…", "Put a picture file into the selected cell, "
                    "above its text", self.choose_picture),
                   ("picture_remove", "Remove picture", "Take the picture out of the selected "
                    "cell (its text stays)", self.remove_picture))
                  if pictures is not None else ())):
            if key is None:
                self.toolbar.addSeparator()
                continue
            act = QAction(text, self)
            act.setToolTip(tip)
            act.setData(tip)   # (the tip back, when _update_actions changes it)
            act.triggered.connect(self._finishing_word(slot))
            if key in ALIGN_NAMES:   # (shows the selected cell's alignment)
                act.setCheckable(True)
            self.toolbar.addAction(act)
            self.actions_[key] = act

        self.grid = QTableWidget(self)
        self.grid.setItemDelegate(CellDelegate(self))
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.grid.horizontalHeader().setDefaultSectionSize(150)
        self.grid.currentCellChanged.connect(self._current_changed)
        self.grid.itemSelectionChanged.connect(self._update_actions)
        self.grid.installEventFilter(self)
        # Rows fit their text again when columns change width (e.g. the dialog is resized).
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.setInterval(50)
        self._row_timer.timeout.connect(self.grid.resizeRowsToContents)
        self.grid.horizontalHeader().sectionResized.connect(lambda *_: self._row_timer.start())

        self.cell_edit = RichTextEditor(self, headings=False, height=64)
        self.cell_edit.changed.connect(self._cell_text_changed)
        self.cell_edit.edit.installEventFilter(self)
        self.cell_label = QLabel()
        # The selected cell's picture: its printed width (0 = its own size).
        self.picture_width = QDoubleSpinBox()
        self.picture_width.setRange(0, 50)
        self.picture_width.setDecimals(1)
        self.picture_width.setSingleStep(0.5)
        self.picture_width.setSuffix(" cm")
        self.picture_width.setSpecialValueText("own size")
        self.picture_width.setToolTip("How wide the selected cell's picture is printed. "
                                      "“Own size” prints it as drawn (pasted ChemDraw "
                                      "structures at your bond length), shrunk only if it is "
                                      "wider than the text.")
        self.picture_width.valueChanged.connect(self._picture_width_changed)
        self.picture_width_label = QLabel("Picture width:")
        cell_row = QHBoxLayout()
        cell_row.addWidget(self.cell_label, 1)
        cell_row.addWidget(self.picture_width_label)
        cell_row.addWidget(self.picture_width)

        self.header_rows = QSpinBox()
        self.header_rows.setRange(0, 10)
        self.header_rows.setToolTip("Rows at the top that hold the column headings: ruled off "
                                    "from the rest and repeated if the table runs over a page.")
        self.header_rows.valueChanged.connect(self._header_changed)
        self.label_edit = QLineEdit(self._table.label)
        self.label_edit.setPlaceholderText(f"{auto_label or 'Table S…'}  (automatic)")
        self.label_edit.setToolTip("Leave empty to number the table automatically. Type a label "
                                   "only for an unusual case (e.g. “Table S2a”).")
        self.caption_edit = RichTextEditor(self, headings=False, height=48)
        self.caption_edit.set_markup(caption)
        self.notes_edit = RichTextEditor(self, headings=False, height=70)
        self.notes_edit.set_markup("\n".join(self._table.footnotes))
        self.split_box = QCheckBox("May run over a page break")
        self.split_box.setChecked(self._table.breakable)
        self.split_box.setToolTip(
            "Unticked (usual): the table is kept whole, moving to the next page if it does not "
            "fit where it is; only a table taller than a page runs over.\n"
            "Ticked: it may start at the foot of a page and continue on the next (its header "
            "rows are repeated there).")
        self._new = new

        # ---- Formatting (SPEC §13y): the selected cells' text size, the selected columns'
        # width, shrinking to the page width, and the caption's and footnotes' sizes. Every
        # "Default" follows Project settings ▸ Text and tables.
        self.size_box = size_box(
            "Default", "Text size of the selected cells. “Default” uses Project settings ▸ "
            "Text and tables (header rows have their own default there).")
        self.size_box.valueChanged.connect(self._size_changed)
        self.width_box = optional_box(
            0.5, 30, 0.1, 1, " cm", "Automatic",
            "Width of the selected columns in the printed table. “Automatic” makes a "
            "column as wide as its text needs (sharing the page width with the others); a "
            "width typed here wraps longer text inside it.")
        self.width_box.valueChanged.connect(self._width_changed)
        self.autofit_btn = QPushButton("Fit columns to their text")
        self.autofit_btn.setToolTip("Set every column back to “Automatic”: each as wide as "
                                    "its text needs.")
        self.autofit_btn.clicked.connect(self._autofit)
        self.autofit_btn.setAutoDefault(False)   # (Enter moves on in the grid, never this)
        self.fit_box = QCheckBox("Shrink to fit the page width")
        self.fit_box.setChecked(self._table.fit_width)
        self.fit_box.setToolTip(
            "If the table is wider than the text on the page, print all of it (text and "
            "columns) smaller so every cell stays on one line, instead of wrapping cells.")
        self.caption_size = size_box("Default", "Size of this table's caption. “Default” "
                                     "uses Project settings ▸ Text and tables.")
        set_value(self.caption_size, self._table.caption_size)
        self.notes_size = size_box("Default", "Size of this table's footnotes. “Default” "
                                   "uses Project settings ▸ Text and tables.")
        set_value(self.notes_size, self._table.notes_size)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Text size:"))
        fmt_row.addWidget(self.size_box)
        fmt_row.addSpacing(12)
        fmt_row.addWidget(QLabel("Column width:"))
        fmt_row.addWidget(self.width_box)
        fmt_row.addWidget(self.autofit_btn)
        fmt_row.addSpacing(12)
        fmt_row.addWidget(self.fit_box)
        fmt_row.addStretch()

        form = QFormLayout()
        form.addRow("Caption:", self.caption_edit)
        sizes = QHBoxLayout()
        sizes.addWidget(self.caption_size)
        sizes.addSpacing(20)
        sizes.addWidget(QLabel("Footnote size:"))
        sizes.addWidget(self.notes_size)
        sizes.addStretch()
        form.addRow("Caption size:", sizes)
        notes_label = QLabel("Footnotes:\n(one per line)")
        self.notes_edit.setToolTip("One footnote per line, printed under the table. Mark them "
                                   "with superscript letters, e.g. ᵃ Isolated yield (and the "
                                   "same letter in the cell).")
        form.addRow(notes_label, self.notes_edit)
        row = QHBoxLayout()
        row.addWidget(self.header_rows)
        row.addSpacing(20)
        row.addWidget(QLabel("Label:"))
        row.addWidget(self.label_edit, 1)
        row.addSpacing(20)
        row.addWidget(self.split_box)
        form.addRow("Header rows:", row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.grid, 1)
        layout.addLayout(fmt_row)
        layout.addLayout(cell_row)
        layout.addWidget(self.cell_edit)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._fill_grid()
        self.grid.setCurrentCell(0, 0)
        self._current_changed(0, 0, -1, -1)
        self._start = self._state()

    # ------------------------------------------------------------------ model <-> grid

    def _fill_grid(self, select: tuple[int, int] | None = None) -> None:
        t = self._table
        t.normalize()
        # (Rows or columns may have moved: the cell being typed in is chosen again below.)
        self._editing = None
        self.cell_edit.edit.forget_word()
        self._loading = True
        self.grid.clearSpans()
        self.grid.setRowCount(t.n_rows)
        self.grid.setColumnCount(t.n_cols)
        # A few columns fill the width; many can be widened by dragging their edges.
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch if t.n_cols <= 6 else QHeaderView.ResizeMode.Interactive)
        self.grid.setHorizontalHeaderLabels(
            [f"{_column_name(c)} ({ALIGN_NAMES[t.columns[c]].lower()}"
             + (f", {t.widths[c]:g} cm)" if t.widths[c] is not None else ")")
             for c in range(t.n_cols)])
        self.grid.setVerticalHeaderLabels(
            [("Header" if r < t.header_rows else str(r - t.header_rows + 1))
             for r in range(t.n_rows)])
        header_fill = QColor(127, 127, 127, 40)
        for r in range(t.n_rows):
            for c in range(t.n_cols):
                cell = t.rows[r][c]
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if cell is not None:
                    item.setData(TEXT_ROLE, cell.text)
                    item.setData(ALIGN_ROLE, cell.align or t.columns[c])
                    item.setData(IMAGE_ROLE, cell.image)
                    item.setData(SIZE_ROLE, cell.size)
                    if r < t.header_rows:
                        item.setBackground(header_fill)
                self.grid.setItem(r, c, item)
        for r, c, cell in t.cells():
            if cell.rowspan > 1 or cell.colspan > 1:
                self.grid.setSpan(r, c, cell.rowspan, cell.colspan)
        self.header_rows.blockSignals(True)
        self.header_rows.setMaximum(max(0, t.n_rows))
        self.header_rows.setValue(t.header_rows)
        self.header_rows.blockSignals(False)
        self.grid.resizeRowsToContents()
        self._loading = False
        if select is not None:
            r, c = (min(select[0], t.n_rows - 1), min(select[1], t.n_cols - 1))
            self.grid.setCurrentCell(*t.anchor(r, c))
            self._current_changed(*t.anchor(r, c), -1, -1)
        self._update_actions()

    def _finishing_word(self, slot):
        """`slot`, after ending the word just typed in the cell (as a space would), so a
        formula typed last is subscripted before rows or columns move."""
        def run(*_args):
            self.cell_edit.edit.finish_word()
            slot()
        return run

    def _current(self) -> tuple[int, int]:
        r, c = self.grid.currentRow(), self.grid.currentColumn()
        if r < 0 or c < 0:
            return 0, 0
        return self._table.anchor(r, c)

    def _selected_rect(self) -> tuple[int, int, int, int]:
        ranges = self.grid.selectedRanges()
        if not ranges:
            r, c = self._current()
            return r, c, r, c
        rg = ranges[0]
        return rg.topRow(), rg.leftColumn(), rg.bottomRow(), rg.rightColumn()

    def _current_changed(self, row: int, col: int, _pr: int, _pc: int) -> None:
        if self._loading or row < 0 or col < 0:
            return
        # A formula typed last in the cell being left is subscripted (into that cell).
        self.cell_edit.edit.finish_word()
        r, c = self._table.anchor(row, col)
        self._editing = (r, c)
        cell = self._table.rows[r][c]
        self.cell_label.setText(f"<b>Cell {_column_name(c)}{r + 1}</b>"
                                + (" (header row)" if r < self._table.header_rows else "")
                                + (f" · {cell.size:g} pt" if cell is not None
                                   and cell.size is not None else ""))
        set_value(self.size_box, cell.size if cell is not None else None)
        set_value(self.width_box, self._table.widths[c] if c < len(self._table.widths)
                  else None)
        self.cell_edit.blockSignals(True)
        self.cell_edit.set_markup(cell.text if cell is not None else "")
        self.cell_edit.blockSignals(False)
        self._show_picture_width(cell)
        self._update_actions()

    def _show_picture_width(self, cell: Cell | None) -> None:
        has = cell is not None and bool(cell.image)
        self.picture_width.blockSignals(True)
        self.picture_width.setValue((cell.image_width or 0) if has else 0)
        self.picture_width.blockSignals(False)
        self.picture_width.setVisible(has)
        self.picture_width_label.setVisible(has)

    def _cell_text_changed(self) -> None:
        if self._loading:
            return
        r, c = self._editing or self._current()
        if r >= self._table.n_rows or c >= self._table.n_cols:
            return
        cell = self._table.rows[r][c]
        if cell is None:
            return
        cell.text = self.cell_edit.markup().strip("\n")
        item = self.grid.item(r, c)
        if item is not None:
            item.setData(TEXT_ROLE, cell.text)
        self.grid.resizeRowToContents(r)

    def _update_actions(self) -> None:
        r0, c0, r1, c1 = self._selected_rect()
        self.actions_["merge"].setEnabled((r0, c0) != (r1, c1))
        r, c = self._current()
        cell = self._table.rows[r][c] if self._table.n_rows else None
        self.actions_["split"].setEnabled(cell is not None and (cell.rowspan > 1
                                                                or cell.colspan > 1))
        if "picture_remove" in self.actions_:
            self.actions_["picture_remove"].setEnabled(cell is not None and bool(cell.image))
        self.actions_["del_rows"].setEnabled(self._table.n_rows > 1)
        self.actions_["del_cols"].setEnabled(self._table.n_cols > 1)
        # (a table has at most MAX_ROWS x MAX_COLS: core/tables.py)
        from ..core.tables import MAX_COLS, MAX_ROWS
        for keys, room, full in (
                (("row_above", "row_below"), self._table.n_rows < MAX_ROWS,
                 f"A table can have at most {MAX_ROWS} rows: split it into several tables."),
                (("col_left", "col_right"), self._table.n_cols < MAX_COLS,
                 f"A table can have at most {MAX_COLS} columns: split it into several "
                 "tables.")):
            for key in keys:
                act = self.actions_[key]
                act.setEnabled(room)
                act.setToolTip(act.data() if room else full)
        # Left / Centre / Right show how the selected cell is aligned (its own alignment,
        # or its column's), as the alignment button of the text editors does.
        align = (cell.align or self._table.columns[c]) if cell is not None else ""
        for name in ALIGN_NAMES:
            self.actions_[name].setChecked(name == align)

    # ------------------------------------------------------------------ commands

    def _insert_row(self, below: int) -> None:
        r, c = self._current()
        cell = self._table.rows[r][c]
        at = r + (cell.rowspan if below and cell is not None else below)
        self._table.insert_row(at)
        self._fill_grid((at, c))

    def _insert_column(self, right: int) -> None:
        r, c = self._current()
        cell = self._table.rows[r][c]
        at = c + (cell.colspan if right and cell is not None else right)
        self._table.insert_column(at)
        self._fill_grid((r, at))

    def _delete_rows(self) -> None:
        r0, c0, r1, _c1 = self._selected_rect()
        self._table.delete_rows(range(r0, r1 + 1))
        self._fill_grid((r0, c0))

    def _delete_columns(self) -> None:
        r0, c0, _r1, c1 = self._selected_rect()
        self._table.delete_columns(range(c0, c1 + 1))
        self._fill_grid((r0, c0))

    def _merge(self) -> None:
        r0, c0, r1, c1 = self._selected_rect()
        if self._table.merge(r0, c0, r1, c1):
            self._fill_grid((r0, c0))

    def _split(self) -> None:
        r, c = self._current()
        if self._table.split(r, c):
            self._fill_grid((r, c))

    def _align(self, align: str) -> None:
        r0, c0, r1, c1 = self._selected_rect()
        t = self._table
        if r0 == 0 and r1 == t.n_rows - 1:   # whole columns: the columns' alignment
            for c in range(c0, c1 + 1):
                t.columns[c] = align
                for r in range(t.n_rows):
                    if t.rows[r][c] is not None:
                        t.rows[r][c].align = ""
        else:
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    cell = t.rows[r][c]
                    if cell is not None:
                        cell.align = "" if align == t.columns[c] else align
        current = self._current()
        self._fill_grid(current)
        self.grid.setRangeSelected(_range(r0, c0, r1, c1), True)

    def _size_changed(self, _value: float) -> None:
        """The text size box: every selected cell gets that size (or the default)."""
        size = value_of(self.size_box)
        r0, c0, r1, c1 = self._selected_rect()
        done = set()
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                ar, ac = self._table.anchor(r, c)
                if (ar, ac) in done:
                    continue
                done.add((ar, ac))
                self._table.rows[ar][ac].size = size
                item = self.grid.item(ar, ac)
                if item is not None:
                    item.setData(SIZE_ROLE, size)
        cr, cc = self._current()
        cell = self._table.rows[cr][cc]
        self.cell_label.setText(f"<b>Cell {_column_name(cc)}{cr + 1}</b>"
                                + (" (header row)" if cr < self._table.header_rows else "")
                                + (f" · {size:g} pt" if size is not None and cell is not None
                                   else ""))
        self.grid.resizeRowsToContents()

    def _width_changed(self, _value: float) -> None:
        """The column width box: every selected column gets that width (or automatic)."""
        width = value_of(self.width_box)
        _r0, c0, _r1, c1 = self._selected_rect()
        self._table.normalize()
        for c in range(c0, c1 + 1):
            self._table.widths[c] = width
        self._refresh_headers()

    def _autofit(self) -> None:
        self._table.widths = [None] * self._table.n_cols
        set_value(self.width_box, None)
        self._refresh_headers()

    def _refresh_headers(self) -> None:
        t = self._table
        for c in range(t.n_cols):
            item = self.grid.horizontalHeaderItem(c)
            text = (f"{_column_name(c)} ({ALIGN_NAMES[t.columns[c]].lower()}"
                    + (f", {t.widths[c]:g} cm)" if t.widths[c] is not None else ")"))
            if item is None:
                self.grid.setHorizontalHeaderItem(c, QTableWidgetItem(text))
            else:
                item.setText(text)

    def _header_changed(self, value: int) -> None:
        if self._loading:
            return
        self._table.header_rows = value
        self._fill_grid(self._current())

    def paste_table(self) -> None:
        from .text_objects import table_block_from_mime
        data = QGuiApplication.clipboard().mimeData()
        block = table_block_from_mime(data) if data is not None else None
        if not block and data is not None and data.hasText():
            from ..core.tables import block_from_text
            block = block_from_text(data.text())
        if not block:
            return
        r, c = self._current()
        left_out = self._table.paste(r, c, block)
        self._fill_grid((r, c))
        warn_left_out(self, left_out)

    # ---- pictures in cells

    def thumbnail(self, image: str) -> QImage:
        """A cell's picture for the grid (null if it has none or it cannot be read)."""
        if not image or self.pictures is None:
            return QImage()
        hit = self._thumbs.get(image)
        if hit is None:
            from ..compile.qt_images import read_image
            try:
                hit = read_image(self.pictures.folder / image, 400)
            except Exception:  # noqa: BLE001 — an unreadable picture just isn't shown
                hit = QImage()
            self._thumbs[image] = hit
        return hit

    def _set_picture(self, image: str) -> None:
        r, c = self._current()
        cell = self._table.rows[r][c]
        if cell is None:
            return
        if cell.image != image:
            cell.image, cell.image_width = image, None
        self._fill_grid((r, c))

    def _store_picture(self, store: Callable[[], str | None]) -> None:
        try:
            image = store()
        except Exception as e:  # noqa: BLE001 — report, never lose the table being edited
            QMessageBox.warning(self, "Could not add the picture", str(e))
            return
        if image:
            self._set_picture(image)

    def paste_picture(self) -> bool:
        """The clipboard's picture into the selected cell; False if there was none."""
        if self.pictures is None:
            return False
        found: list[bool] = []

        def store() -> str | None:
            image = self.pictures.paste()
            found.append(image is not None)
            if image is None:
                QMessageBox.information(self, "Paste picture", "There is no picture on the "
                                        "clipboard. Copy a structure in ChemDraw (or a "
                                        "picture) first.")
            return image
        self._store_picture(store)
        return bool(found and found[0])

    def choose_picture(self) -> None:
        if self.pictures is None:
            return
        from .text_objects import choose_figure_file
        path = choose_figure_file(self)
        if path is not None:
            self._store_picture(lambda: self.pictures.from_file(path))

    def remove_picture(self) -> None:
        self._set_picture("")

    def _picture_width_changed(self, value: float) -> None:
        r, c = self._current()
        cell = self._table.rows[r][c]
        if cell is not None and cell.image:
            cell.image_width = round(value, 2) if value > 0 else None

    def _clipboard_has_only_picture(self) -> bool:
        """A picture on the clipboard and no table or text (Ctrl+V then pastes it into the
        selected cell rather than as a table)."""
        data = QGuiApplication.clipboard().mimeData()
        if data is None or (data.hasText() and data.text().strip()) or data.hasHtml():
            return False
        if data.hasImage() or any("pdf" in f.lower() for f in data.formats()):
            return True
        from . import clipboard_image
        try:
            return clipboard_image.clipboard_has_vector()
        except Exception:  # noqa: BLE001
            return False

    def _copy_cells(self) -> None:
        """Copy the selected cells with their formatting (subscripts, bold, links…): pasted
        back into a table they keep it; Word and Excel get an HTML table, other programs
        tab-separated text."""
        r0, c0, r1, c1 = self._selected_rect()
        lines, block = [], []
        for r in range(r0, r1 + 1):
            cells, row = [], []
            for c in range(c0, c1 + 1):
                cell = self._table.rows[r][c]
                cells.append(" ".join(p.plain_text for p in markup.parse(cell.text))
                             if cell is not None else "")
                row.append(cell)
            lines.append("\t".join(cells))
            block.append(row)
        data = QMimeData()
        data.setText("\n".join(lines))
        data.setHtml(block_html(block))
        data.setData(CELLS_MIME, cells_json(block, r1 - r0 + 1, c1 - c0 + 1))
        QGuiApplication.clipboard().setMimeData(data)

    def _clear_cells(self) -> None:
        r0, c0, r1, c1 = self._selected_rect()
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if self._table.rows[r][c] is not None:
                    self._table.rows[r][c].text = ""
        self._fill_grid(self._current())

    def _move(self, dr: int, dc: int) -> None:
        r, c = self._current()
        cell = self._table.rows[r][c]
        r = min(self._table.n_rows - 1, r + (cell.rowspan if dr and cell else dr))
        c = c + (cell.colspan if dc and cell else dc)
        if c >= self._table.n_cols:   # Tab at the end of a row: the next row
            r, c = min(self._table.n_rows - 1, r + 1), 0
        self.grid.setCurrentCell(r, c)
        self.grid.setFocus()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)
        key, mods = event.key(), event.modifiers()
        if obj is self.grid:
            if event.matches(QKeySequence.StandardKey.Paste):
                if self.pictures is not None and self._clipboard_has_only_picture():
                    self.paste_picture()
                else:
                    self.paste_table()
                return True
            if event.matches(QKeySequence.StandardKey.Copy):
                self._copy_cells()
                return True
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._clear_cells()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
                self.cell_edit.edit.setFocus()
                self.cell_edit.edit.moveCursor(QTextCursor.MoveOperation.End)
                return True
            text = event.text()
            if text and text.isprintable() and not mods & (Qt.KeyboardModifier.ControlModifier
                                                           | Qt.KeyboardModifier.AltModifier):
                # Typing on a cell replaces its text, as in a spreadsheet.
                self.cell_edit.set_markup("")
                self._cell_text_changed()
                self.cell_edit.edit.setFocus()
                if text == "@":
                    self.cell_edit.edit.mention()
                else:
                    self.cell_edit.edit.insertPlainText(text)
                return True
        elif obj is self.cell_edit.edit:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                    mods & Qt.KeyboardModifier.ShiftModifier):
                self.cell_edit.edit.finish_word()
                self._move(1, 0)   # Enter: the cell below (Shift+Enter: a new line)
                return True
            if key == Qt.Key.Key_Tab:
                self.cell_edit.edit.finish_word()
                self._move(0, 1)
                return True
            if key == Qt.Key.Key_Escape:
                self.grid.setFocus()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ results

    def _state(self):
        return self.result_table().to_json(), self.caption()

    def has_changes(self) -> bool:
        """Anything to lose on Cancel: an edit, or a new table that already has text (e.g.
        pasted from Word)."""
        if self._state() != self._start:
            return True
        return self._new and not self._table.is_empty()

    def accept(self) -> None:
        self.cell_edit.edit.finish_word()   # ("K2CO3" typed last, then OK)
        super().accept()

    def reject(self) -> None:
        if self.has_changes() and QMessageBox.question(
                self, "Discard changes?",
                "Close the table editor and discard your changes to this table?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Discard:
            return
        super().reject()

    def result_table(self) -> Table:
        t = self._table.copy()
        t.label = self.label_edit.text().strip()
        t.breakable = self.split_box.isChecked()
        t.fit_width = self.fit_box.isChecked()
        t.caption_size = value_of(self.caption_size)
        t.notes_size = value_of(self.notes_size)
        t.header_rows = self.header_rows.value()
        t.footnotes = [line for line in self.notes_edit.markup().split("\n") if line.strip()]
        t.normalize()
        return t

    def caption(self) -> str:
        return self.caption_edit.markup()


def warn_left_out(parent: QWidget | None, left_out: tuple[int, int]) -> None:
    """Say what of a pasted table did not fit (Table.paste's limit), if anything."""
    from ..core.tables import MAX_COLS, MAX_ROWS, PASTE_MAX_COLS, PASTE_MAX_ROWS
    rows, cols = left_out
    if not (rows or cols):
        return
    parts = ([f"{rows} row{'s' if rows != 1 else ''}"] if rows else []) + (
        [f"{cols} column{'s' if cols != 1 else ''}"] if cols else [])
    verb = "was" if rows + cols == 1 else "were"
    QMessageBox.warning(parent, "Paste table",
                        f"{' and '.join(parts)} of the copied table {verb} not pasted: one "
                        f"paste brings in at most {PASTE_MAX_ROWS} rows and {PASTE_MAX_COLS} "
                        f"columns, and a table can have at most {MAX_ROWS} rows and "
                        f"{MAX_COLS} columns. Split it into several tables.")


def _column_name(c: int) -> str:
    from ..core.labels import alpha
    return alpha(c + 1).upper()


def _range(r0: int, c0: int, r1: int, c1: int):
    from PySide6.QtWidgets import QTableWidgetSelectionRange
    return QTableWidgetSelectionRange(r0, c0, r1, c1)


class CellPictures:
    """Where a table's cell pictures are kept (`folder`, which the cells' "image" paths are
    relative to) and how new ones are stored there. `store(source=…)` stores a file, and
    `store(data=…, ext=…, vector=…, vector_ext=…)` pasted bytes; both return the new
    picture's path relative to `folder` (core/project.Project.add_table_picture)."""

    def __init__(self, folder: Path, store: Callable[..., str], paste_dpi: int = 0):
        self.folder = Path(folder)
        self._store = store
        self.paste_dpi = paste_dpi

    def from_file(self, path: Path) -> str:
        # (a user's file: no "ChemDraw original", as for figures)
        return self._store(source=Path(path))

    def paste(self) -> str | None:
        """Store the clipboard's picture (rendered from ChemDraw's vector original, which is
        kept, as for pasted figures); None if there is none."""
        from .clipboard_image import DEFAULT_DPI, clipboard_picture, image_to_png
        picture = clipboard_picture(self.paste_dpi or DEFAULT_DPI)
        if picture is not None:
            return self._store(data=picture.data, ext=picture.ext, vector=picture.source,
                               vector_ext=picture.source_ext)
        data = QGuiApplication.clipboard().mimeData()
        image = QImage(data.imageData()) if data is not None and data.hasImage() else None
        if image is None or image.isNull():
            return None
        return self._store(data=image_to_png(image), ext=".png")
