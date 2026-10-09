"""The Data-section dashboard grid (SPEC §6.3.4)."""

from __future__ import annotations

import html

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QTableWidget, QTableWidgetItem, QToolButton,
                               QToolTip, QVBoxLayout, QWidget)

from ..core import dashboard as D
from ..core.project import Project, ProjectError, Section
from .structure_widgets import drawing_settings, mol_pixmap, picture_pixmap

THUMB = QSize(120, 80)   # (in interface units; drawn with the screen's own pixels)
KEY_ROLE = Qt.ItemDataRole.UserRole
CID_ROLE = Qt.ItemDataRole.UserRole + 1
STRUCT_TIP_ROLE = Qt.ItemDataRole.UserRole + 2   # set: the tooltip shows the structure,
                                                 # then this text (HTML)

LEGEND = ("✔ filled · ○ empty · ✖ empty but required · — not used for this compound · "
          "⚑ flagged · ⚠ warning. Click any cell to open that compound at that field.")

# (file, screen scale) -> (file's mtime and size, drawing settings, picture). One entry
# per file: a newer picture replaces the old one, so the cache does not grow with every
# structure edit or change of drawing style.
_thumb_cache: dict[tuple, tuple[tuple, QPixmap]] = {}


def thumbnail(project: Project, compound, dpr: float = 1.0) -> QPixmap | None:
    """Small structure picture, cached until structure.mol (or the own image) changes.
    `dpr`: the screen's device pixel ratio (1.5 at 150 % display scaling), so it is drawn
    with the screen's real pixels and stays sharp (SPEC §13q.5)."""
    image = project.structure_image_path(compound)
    path = image if image is not None and image.exists() else project.structure_path(compound)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    drawing = drawing_settings(project) if path.suffix == ".mol" else None
    key = (str(path), THUMB.width(), round(dpr, 3))
    stamp = (st.st_mtime_ns, st.st_size, drawing)
    cached = _thumb_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    if path.suffix == ".mol":
        mol = project.structure_for_drawing(compound)   # (also one with an R group)
        if mol is None:
            return None
        pixmap = mol_pixmap(mol, THUMB, drawing, dpr)
    else:
        pixmap = picture_pixmap(path, THUMB, dpr)
    if pixmap.isNull():
        # (not kept: a picture that cannot be read just now - a OneDrive file not yet
        #  downloaded, or one open in another program - is tried again next time)
        return None
    _thumb_cache[key] = (stamp, pixmap)
    return pixmap


# file -> (file's mtime and size, drawing settings, the <img> tag). The picture is put in
# the tooltip itself (a data: address), so no picture files are left in the temp folder.
_tooltip_cache: dict[str, tuple[tuple, str]] = {}


def structure_tooltip(project: Project, compound) -> str:
    """Rich-text tooltip with the compound's structure picture (for the compact view)."""
    name = html.escape(compound.name or "(unnamed)", quote=False)   # shown as typed
    picture = project.structure_image_path(compound)
    source = picture if picture is not None and picture.exists() else project.structure_path(compound)
    try:
        st = source.stat()
    except FileNotFoundError:
        return f"<b>{name}</b><br><i>no structure yet</i>"
    drawing = drawing_settings(project) if source.suffix == ".mol" else None
    stamp = (st.st_mtime_ns, st.st_size, drawing)
    cached = _tooltip_cache.get(str(source))
    if cached is None or cached[0] != stamp:
        size = QSize(240, 170)
        if source.suffix == ".mol":
            mol = project.structure_for_drawing(compound)   # (also one with an R group)
            if mol is None:
                return f"<b>{name}</b>"
            pixmap = mol_pixmap(mol, size, drawing)
        else:
            pixmap = picture_pixmap(source, size)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if pixmap.isNull() or not pixmap.save(buffer, "PNG"):
            return f"<b>{name}</b>"   # (not kept: tried again on the next hover)
        img = f"<img src='data:image/png;base64,{bytes(data.toBase64()).decode()}'>"
        cached = _tooltip_cache[str(source)] = (stamp, img)
    return f"<b>{name}</b><br>{cached[1]}"


def _item(text: str = "", tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def cell_colour(colour: str, status: str) -> QColor | None:
    """The see-through tint a cell of this status is painted with (None: no colour), so
    its text reads well in light and dark mode. Always paired with an icon."""
    if not colour:
        return None
    c = QColor(colour)
    c.setAlpha(D.CELL_ALPHA.get(status, 80))
    return c


def cell_colours(project: Project) -> dict[str, QColor | None]:
    """The project's status colours (SPEC §13k.3) as cell tints."""
    return {k: cell_colour(v, k) for k, v in D.status_colours(project.settings).items()}


def _paint(item: QTableWidgetItem, cell: D.Cell, colours: dict) -> None:
    colour = colours.get(D.cell_status(cell))
    if colour is not None:
        item.setBackground(QBrush(colour))




def blend(colour: QColor | None, base: QColor) -> QColor:
    """A see-through tint as it looks over `base` (for legends and samples)."""
    if colour is None:
        return QColor(base)
    a = colour.alphaF()
    return QColor(round(colour.red() * a + base.red() * (1 - a)),
                  round(colour.green() * a + base.green() * (1 - a)),
                  round(colour.blue() * a + base.blue() * (1 - a)))


def legend_html(colours: dict, base: QColor) -> str:
    """The cell legend, each symbol on its own colour."""
    parts = []
    for key in D.STATUS_KEYS:
        shade = blend(colours.get(key), base).name()
        parts.append(f"<span style='background-color:{shade}'>&nbsp;{D.STATUS_ICONS[key]}"
                     f"&nbsp;</span>&nbsp;{D.STATUS_NAMES[key].lower()}")
    return " · ".join(parts) + ". Click any cell to open that compound at that field."


class DashboardView(QWidget):
    """The grid of a Data section's compounds. ProjectGridView (project_dashboard.py)
    shows every Data section's compounds with the same code, through the hooks
    `_rows`, `_extra_items`, `_id_note_text` and `_remember`."""

    open_compound = Signal(str, str)  # compound id, field key ("" = top of the page)
    new_compound_requested = Signal()
    saved = Signal(str)

    EXTRA: list[str] = []          # extra fixed columns after Name (the project grid: Section)
    NEW_BUTTON = True

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.section = section
        self.FIXED = ["ID", "Structure", "Name", *self.EXTRA, "Required"]
        config = D.view_settings(section)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        f = self.summary.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.summary.setFont(f)
        self.id_note = QLabel()
        self.id_note.setStyleSheet("color: #d35400;")
        self.id_note.setWordWrap(True)

        self.filter_box = QComboBox()
        for key, text in D.FILTERS.items():
            self.filter_box.addItem(text, key)
        self.filter_box.setCurrentIndex(max(0, self.filter_box.findData(config.get("filter", "all"))))
        self.filter_box.currentIndexChanged.connect(self._filter_changed)

        self.columns_btn = QToolButton()
        self.columns_btn.setText("Columns")
        self.columns_btn.setToolTip("Choose which fields are shown")
        self.columns_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.columns_menu = QMenu(self.columns_btn)
        self.columns_menu.aboutToShow.connect(self._fill_columns_menu)
        self.columns_btn.setMenu(self.columns_menu)

        self.compact_box = QCheckBox("Compact")
        self.compact_box.setToolTip("Only colours and symbols, no thumbnails or summaries — "
                                    "hover over a compound's ID or name to see its structure")
        self.compact_box.setChecked(bool(config.get("compact")))
        self.compact_box.toggled.connect(self._compact_changed)
        self.split_box = QCheckBox("Split NMR && spectra by nucleus")
        self.split_box.setToolTip("Show a separate column for each NMR nucleus (¹H, ¹³C, ¹⁹F…) "
                                  "and each spectrum type")
        self.split_box.setChecked(D.is_split(section))
        self.split_box.toggled.connect(self._split_changed)

        self.new_btn = QPushButton("+ New compound")
        self.new_btn.setEnabled(not project.read_only)
        self.new_btn.clicked.connect(self.new_compound_requested)
        self.new_btn.setVisible(self.NEW_BUTTON)

        self.top = QHBoxLayout()
        self.top.addWidget(self.summary, 1)
        self.top.addWidget(QLabel("Show:"))
        self.top.addWidget(self.filter_box)
        self.top.addWidget(self.compact_box)
        self.top.addWidget(self.split_box)
        self.top.addWidget(self.columns_btn)
        self.top.addWidget(self.new_btn)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB.height() + 6)
        self.table.setIconSize(THUMB)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(True)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.viewport().installEventFilter(self)

        self.legend = QLabel()
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addWidget(self.id_note)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.legend)

    # ------------------------------------------------------------------ hooks

    def _rows(self) -> list[D.Row]:
        return D.build_rows(self.project, self.section)

    def _extra_items(self, row: D.Row) -> list[QTableWidgetItem]:
        """Items for the EXTRA columns of a row."""
        return []

    def _id_note_text(self) -> str:
        if len(self.section.compounds) > len(self.section.id_table):
            return "⚠ There are more compounds than rows in the ID table — see the ID table tab."
        return ""

    def _empty_text(self) -> str:
        return "No compounds yet. Click “New compound” to add one."

    def _save_view(self) -> None:
        """Write the view choices (section.data["dashboard"]) to the project."""
        self.project.save_section(self.section)

    def _remember(self, **values) -> None:
        """Remember view choices (columns, filter, compact, split_nuclei) with the project;
        a read-only project keeps them for this session only."""
        self.section.data["dashboard"] = {**D.view_settings(self.section), **values}
        if not self.project.read_only:
            try:
                self._save_view()
            except (ProjectError, OSError) as e:
                self.saved.emit(f"Could not save the dashboard setting: {e}")

    # ------------------------------------------------------------------ filling

    def refresh(self, rows: list[D.Row] | None = None) -> None:
        rows = self._rows() if rows is None else rows
        colours = cell_colours(self.project)
        self.legend.setText(legend_html(colours, self.palette().base().color()))
        self.summary.setText(D.summary_text(rows) if rows else self._empty_text())
        self.id_note.setText(self._id_note_text())
        self.id_note.setVisible(bool(self.id_note.text()))
        shown = D.filter_rows(rows, self.filter_box.currentData())
        columns = D.shown_columns(self.section)
        compact = self.compact_box.isChecked()
        self.table.verticalHeader().setDefaultSectionSize(26 if compact else THUMB.height() + 6)
        self.table.setWordWrap(not compact)
        n_fixed = len(self.FIXED)

        self.table.clear()
        self.table.setColumnCount(n_fixed + len(columns))
        self.table.setHorizontalHeaderLabels(
            self.FIXED + [D.field_label(self.section, k) for k in columns])
        self.table.setColumnHidden(1, compact)  # no thumbnails in the compact view
        self.table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            cid = row.compound.id
            # ID (its structure tooltip is drawn when first hovered: see eventFilter)
            # (the compact view hides the structure column: its warnings show here)
            id_ws = row.compact_id_warnings if compact else row.id_warnings
            id_item = _item(row.display_id + (" 🔒" if row.compound.id_override else "")
                            + (" ⚠" if id_ws else ""))
            id_item.setData(STRUCT_TIP_ROLE, "".join(f"<br>⚠ {html.escape(w, quote=False)}"
                                                     for w in id_ws))
            if id_ws and colours.get(D.WARNING) is not None:
                id_item.setBackground(QBrush(colours[D.WARNING]))
            f = id_item.font()
            f.setBold(True)
            id_item.setFont(f)
            # Structure (the thumbnail is also the structure field's cell)
            s_cell = row.cells["structure"]
            # (the compact view hides this column: no thumbnails are drawn for it; the
            #  ID/name hover tooltip draws its own picture when needed)
            pixmap = None if compact else thumbnail(self.project, row.compound,
                                                    self.devicePixelRatioF())
            s_item = _item(s_cell.icons if compact else
                           ("" if pixmap else f"{s_cell.icons} no structure"), s_cell.tooltip)
            if pixmap is not None:
                s_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
                if s_cell.flag is not None or s_cell.warnings:
                    s_item.setText(s_cell.icons.replace("✔", "").strip())
            if pixmap is None or s_cell.flag is not None or s_cell.warnings:
                _paint(s_item, s_cell, colours)  # keep a drawn structure on its normal background
            s_item.setData(KEY_ROLE, "structure")
            # Name
            n_cell = row.cells["name"]
            in_scheme = (f"   ⤷ {self.project.scheme(row.compound.scheme_id).name}"
                         if row.compound.scheme_id else "")
            name_item = _item((row.compound.name or "(unnamed)") + in_scheme,
                              "" if compact else
                              n_cell.tooltip + (f"\nPart of scheme page “"
                                                f"{self.project.scheme(row.compound.scheme_id).name}”"
                                                if row.compound.scheme_id else ""))
            if compact:
                name_item.setData(STRUCT_TIP_ROLE, "")
            name_item.setData(KEY_ROLE, "name")
            # Completion
            done_text = "✔" if row.complete else f"◐ {row.filled}/{row.total}"
            done = _item(done_text if compact else ("✔ complete" if row.complete else done_text),
                         "All required fields filled" if row.complete
                         else "Missing: " + ", ".join(row.missing))
            if row.complete and colours.get(D.FILLED) is not None:
                done.setBackground(QBrush(colours[D.FILLED]))
            fixed = [id_item, s_item, name_item, *self._extra_items(row), done]
            for c, item in enumerate(fixed):
                item.setData(CID_ROLE, cid)
                self.table.setItem(r, c, item)
            # One column per field
            for c, key in enumerate(columns, start=n_fixed):
                cell = row.cells.get(key, D.Cell(D.NOT_USED))
                text = cell.icons if compact else \
                    cell.icons + (f"  {cell.summary}" if cell.summary else "")
                item = _item(text, cell.tooltip + (f"\n{cell.summary}" if cell.summary else ""))
                _paint(item, cell, colours)
                item.setData(CID_ROLE, cid)
                item.setData(KEY_ROLE, key)
                self.table.setItem(r, c, item)

        header = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        header.resizeSection(1, THUMB.width() + 12)
        for c in range(n_fixed, self.table.columnCount()):
            if compact:
                header.resizeSection(c, max(min(header.sectionSize(c), 110), 44))
            else:
                header.resizeSection(c, min(max(header.sectionSize(c), 90), 230))
        header.resizeSection(2, min(header.sectionSize(2), 180 if compact else 260))
        for c in range(3, 3 + len(self.EXTRA)):
            header.resizeSection(c, min(header.sectionSize(c), 140 if compact else 200))

    # ------------------------------------------------------------------ interaction

    def tooltip_for(self, item: QTableWidgetItem) -> str:
        """The tooltip of an item; for the ID (and the name, when compact) it shows the
        compound's structure, drawn when first needed (drawing 150 of them up front
        would slow the dashboard down)."""
        extra = item.data(STRUCT_TIP_ROLE)
        if extra is None:
            return item.toolTip()
        try:
            compound = self.project.compound(item.data(CID_ROLE))
        except KeyError:
            return item.toolTip()
        return structure_tooltip(self.project, compound) + extra

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.table.viewport() and event.type() == QEvent.Type.ToolTip:
            item = self.table.itemAt(event.pos())
            if item is not None and item.data(STRUCT_TIP_ROLE) is not None:
                QToolTip.showText(event.globalPos(), self.tooltip_for(item), obj)
                return True
        return super().eventFilter(obj, event)

    def _cell_clicked(self, row: int, col: int) -> None:
        item = self.table.item(row, col)
        if item is None:
            return
        key = (item.data(KEY_ROLE) or "").split(":")[0]  # "nmr:13C" opens the NMR field
        self.open_compound.emit(item.data(CID_ROLE), key)

    def _compact_changed(self, on: bool) -> None:
        self._remember(compact=on)
        self.refresh()

    def _split_changed(self, on: bool) -> None:
        self._remember(split_nuclei=on)
        self.refresh()

    def _filter_changed(self) -> None:
        self._remember(filter=self.filter_box.currentData())
        self.refresh()

    def _fill_columns_menu(self) -> None:
        self.columns_menu.clear()
        shown = set(D.chosen_columns(self.section))
        for key in D.available_columns(self.section):
            # ("Yield & ee" would lose its "&" to an Alt+ shortcut)
            act = self.columns_menu.addAction(D.field_label(self.section, key).replace("&", "&&"))
            act.setCheckable(True)
            act.setChecked(key in shown)
            act.toggled.connect(lambda on, k=key: self._toggle_column(k, on))
        self.columns_menu.addSeparator()
        self.columns_menu.addAction("Show all", lambda: self._set_columns(
            D.available_columns(self.section)))

    def _toggle_column(self, key: str, on: bool) -> None:
        available = D.available_columns(self.section)
        shown = set(D.chosen_columns(self.section))
        shown.add(key) if on else shown.discard(key)
        self._set_columns([k for k in available if k in shown])

    def _set_columns(self, columns: list[str]) -> None:
        self._remember(columns=columns)
        self.refresh()