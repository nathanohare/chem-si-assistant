"""The table editor (SPEC §13k.5): a grid like a spreadsheet, with a box under it for the
selected cell's text (bold, italic, super/subscript, mentions, citations).

Rows and columns can be added and deleted, cells merged and split, header rows chosen
(ruled off and repeated after a page break), each column or cell aligned, footnotes added,
and a table pasted from Word or Excel. The table itself is core/tables.Table; this dialog
only edits a copy of it, so Cancel leaves everything as it was (after asking, if anything
was changed: Escape pressed twice must not throw a table away).
"""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QSpinBox, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
                               QTableWidgetItem, QToolBar, QVBoxLayout, QWidget)

from ..core import markup
from ..core.markup import SUB, SUPER, Paragraph, mention_kind
from ..core.tables import Cell, Table
from . import rich_text
from .rich_text import RichTextEditor, document_to_paragraphs

TEXT_ROLE = Qt.ItemDataRole.UserRole          # the cell's stored text
ALIGN_ROLE = Qt.ItemDataRole.UserRole + 1     # "left" / "center" / "right"
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
        doc.setDefaultFont(self.dialog.grid.font())
        align = index.data(ALIGN_ROLE) or "center"
        css = {"left": "left", "right": "right"}.get(align, "center")
        doc.setHtml(f"<div align='{css}'>{markup_html(index.data(TEXT_ROLE) or '')}</div>")
        doc.setTextWidth(max(20.0, width))
        return doc

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QGuiApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        doc = self._doc(index, opt.rect.width() - 6)
        painter.save()
        top = opt.rect.top() + max(0.0, (opt.rect.height() - doc.size().height()) / 2)
        painter.translate(opt.rect.left() + 3, top)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        # (Before the grid is laid out its columns have no width yet.)
        width = max(self.dialog.grid.columnWidth(index.column()), 120)
        doc = self._doc(index, width - 6)
        return QSize(int(doc.idealWidth()) + 10, int(doc.size().height()) + 6)

    def createEditor(self, parent, option, index):  # noqa: N802
        return None


# ---------------------------------------------------------------------- pasting from Word/Excel

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
                 parent: QWidget | None = None, new: bool = False):
        super().__init__(parent)
        self.setWindowTitle("New table" if new else "Edit table")
        self.resize(940, 760)
        self._table = table.copy()
        self._table.normalize()
        self._loading = False

        intro = QLabel("Click a cell and type (its text is edited in the box under the grid, "
                       "with bold, italic, sub/superscript and @ mentions). Select several "
                       "cells to merge them. Ctrl+V pastes a table copied from Word or Excel.")
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
                 "selected cell (the table grows as needed)", self.paste_table)):
            if key is None:
                self.toolbar.addSeparator()
                continue
            act = QAction(text, self)
            act.setToolTip(tip)
            act.triggered.connect(slot)
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

        form = QFormLayout()
        form.addRow("Caption:", self.caption_edit)
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
        layout.addWidget(self.cell_label)
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
        self._loading = True
        self.grid.clearSpans()
        self.grid.setRowCount(t.n_rows)
        self.grid.setColumnCount(t.n_cols)
        # A few columns fill the width; many can be widened by dragging their edges.
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch if t.n_cols <= 6 else QHeaderView.ResizeMode.Interactive)
        self.grid.setHorizontalHeaderLabels(
            [f"{_column_name(c)} ({ALIGN_NAMES[t.columns[c]].lower()})" for c in range(t.n_cols)])
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
        r, c = self._table.anchor(row, col)
        cell = self._table.rows[r][c]
        self.cell_label.setText(f"<b>Cell {_column_name(c)}{r + 1}</b>"
                                + (" (header row)" if r < self._table.header_rows else ""))
        self.cell_edit.blockSignals(True)
        self.cell_edit.set_markup(cell.text if cell is not None else "")
        self.cell_edit.blockSignals(False)
        self._update_actions()

    def _cell_text_changed(self) -> None:
        if self._loading:
            return
        r, c = self._current()
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
        self.actions_["del_rows"].setEnabled(self._table.n_rows > 1)
        self.actions_["del_cols"].setEnabled(self._table.n_cols > 1)
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
        self._table.paste(r, c, block)
        self._fill_grid((r, c))

    def _copy_cells(self) -> None:
        r0, c0, r1, c1 = self._selected_rect()
        lines = []
        for r in range(r0, r1 + 1):
            cells = []
            for c in range(c0, c1 + 1):
                cell = self._table.rows[r][c]
                cells.append(" ".join(p.plain_text for p in markup.parse(cell.text))
                             if cell is not None else "")
            lines.append("\t".join(cells))
        QGuiApplication.clipboard().setText("\n".join(lines))

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
                self._move(1, 0)   # Enter: the cell below (Shift+Enter: a new line)
                return True
            if key == Qt.Key.Key_Tab:
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
        return self._new and any(cell.text.strip() for _r, _c, cell in self._table.cells())

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
        t.header_rows = self.header_rows.value()
        t.footnotes = [line for line in self.notes_edit.markup().split("\n") if line.strip()]
        t.normalize()
        return t

    def caption(self) -> str:
        return self.caption_edit.markup()


def _column_name(c: int) -> str:
    from ..core.labels import alpha
    return alpha(c + 1).upper()


def _range(r0: int, c0: int, r1: int, c1: int):
    from PySide6.QtWidgets import QTableWidgetSelectionRange
    return QTableWidgetSelectionRange(r0, c0, r1, c1)
