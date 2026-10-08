"""Scheme page (SPEC §6.3.5): a reaction scheme, drawn with the scheme editor (SPEC §13n.4,
ui/scheme_editor_ui.py), and its compounds as tabs (sub-pages) underneath. (One pasted
picture of the whole scheme, with draggable ID labels, was removed: SPEC §13p.5.)"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QSplitter, QTabBar, QToolButton, QVBoxLayout, QWidget)

from ..core import scheme_editor as se
from ..core.project import Project, ProjectError, SchemePage
from .compound_panel import CompoundPanel


class SchemePanel(QWidget):
    labels_changed = Signal()            # IDs/names may have changed: refresh the tree
    saved = Signal(str)
    compound_selected = Signal(str)      # tab changed: keep the tree selection in step
    view_spectra = Signal(str)           # a compound's "View spectra page": compound id

    def __init__(self, project: Project, scheme: SchemePage, user_name: Callable[[], str],
                 select_compound: str | None = None, collapsed: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.scheme = scheme
        self.user_name = user_name
        self.compound_panel: CompoundPanel | None = None
        ro = project.read_only

        self.name_edit = QLineEdit(scheme.name)
        self.name_edit.setToolTip("The scheme's name, printed as its caption")
        self.name_edit.setReadOnly(ro)
        self.name_edit.editingFinished.connect(self._commit_name)
        # Next to the live preview the box can be narrow: keep room for a few words, show
        # the start of the name (not its end) and the whole name in the tooltip.
        self.name_edit.setMinimumWidth(self.name_edit.fontMetrics().averageCharWidth() * 16)
        self._show_name_start()
        # Kept short (the live preview pane shares the width); the rest is in the tooltip.
        kind = QLabel("Scheme page")
        kind.setToolTip("A scheme page has no ID of its own; its compounds take IDs from the "
                        "section's ID table")
        kind.setStyleSheet("color: palette(placeholder-text);")
        self.collapse_btn = QToolButton()
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.setToolTip("Hide or show the scheme (more room for the compounds' "
                                     "data)")
        self.collapse_btn.toggled.connect(self.set_collapsed)
        # The label printed with the scheme (SPEC §13k.5a): automatic unless typed over;
        # only printed when the scheme's Layout ▸ "Label and caption" is on (SPEC §13p.1).
        self.label_edit = QLineEdit(str(scheme.data.get("label", "")))
        self.label_edit.setReadOnly(ro)
        self.label_edit.editingFinished.connect(self._commit_label)
        # The name and label: only when the scheme prints its label and caption (the name is
        # the caption; otherwise it is renamed in the tree), SPEC §13q.6b.
        self.name_row = QWidget(self)   # (a parent at once: never a window of its own)
        top = QHBoxLayout(self.name_row)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel("Name (caption):"))
        top.addWidget(self.name_edit, 1)
        top.addWidget(QLabel("Label:"))
        top.addWidget(self.label_edit)
        self.kind = kind

        # Sub-pages
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(not ro)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.add_btn = QToolButton()
        self.add_btn.setText("+ Add compound")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_btn.setEnabled(not ro)
        self.add_menu = QMenu(self.add_btn)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)
        tab_row = QHBoxLayout()
        tab_row.addWidget(self.tabs, 1)
        tab_row.addWidget(self.add_btn)

        self.body = QVBoxLayout()
        self.empty = QLabel("No compounds in this scheme yet. Tick “Compound” under a product "
                            "to give it an ID and a data page.")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.empty)

        # The scheme editor (SPEC §13n.4).
        from .scheme_editor_ui import SchemeEditorWidget
        self.editor = SchemeEditorWidget(project, scheme, own_bar=False)
        self.editor.saved.connect(self.saved)
        self.editor.compounds_changed.connect(self._editor_compounds_changed)
        self.editor.height_changed.connect(self.fit_divider)
        self.editor.layout_changed.connect(self._update_label_placeholder)
        self.scheme_area = QWidget()
        area_layout = QVBoxLayout(self.scheme_area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.addWidget(self.editor)

        # The scheme above, the compound tabs below, with a divider between them: the
        # scheme gets the height it needs (up to about half the page at first), and the
        # divider can be dragged to give either more room (SPEC §13n.4) — never more than
        # the scheme needs, so there is no empty gap under it (SPEC §13p.2).
        self.lower = QWidget()
        lower = QVBoxLayout(self.lower)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.addLayout(tab_row)
        lower.addLayout(self.body, 1)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.scheme_area)
        self.splitter.addWidget(self.lower)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._divider_dragged)
        self._divider_by_hand = False
        # One row on top: ▾ Scheme, the editor's buttons, and "Scheme page" (SPEC §13q.6b).
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.collapse_btn, 0, Qt.AlignmentFlag.AlignTop)
        bar.addLayout(self.editor.bar, 1)
        bar.addWidget(kind, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.name_row)
        layout.addWidget(self.splitter, 1)
        self._update_label_placeholder()

        self._fill_tabs(select_compound)
        self.collapse_btn.setChecked(collapsed)
        self.set_collapsed(collapsed)

    def _editor_compounds_changed(self, show: str) -> None:
        """The scheme editor added, removed or reordered sub-compounds."""
        self._fill_tabs(show or self.current_compound_id())
        self.labels_changed.emit()

    # ------------------------------------------------------------------ collapse

    # ---- the divider between the scheme and the compound tabs

    LOWER_MIN = 240   # px kept for the compound tabs when the scheme is tall

    def _divider_dragged(self, *_args) -> None:
        self._divider_by_hand = True

    def scheme_need(self) -> int:
        """The height the scheme editor needs at the page's width (its buttons wrap onto
        more lines on a narrow page)."""
        area = self.scheme_area
        width = area.width() if area.width() > 50 else self.width()
        need = area.heightForWidth(width) if area.hasHeightForWidth() else -1
        return need if need > 0 else area.sizeHint().height()

    def fit_divider(self) -> None:
        """Give the scheme the height it needs (all of it if the page has room; else at
        least about half the page, the compounds the rest), and never more, so the
        compound page starts right under it. The divider dragged by hand stays where it
        was (unless the scheme no longer needs that much)."""
        if not self.scheme_area.isVisible():
            return
        want = self.scheme_need()
        self.scheme_area.setMaximumHeight(max(want, self.scheme_area.minimumSizeHint().height()))
        if self._divider_by_hand:
            return
        total = sum(self.splitter.sizes())
        if total < 100:
            return
        room = max(total - self.LOWER_MIN, int(total * 0.5))
        top = max(min(want, room), self.scheme_area.minimumSizeHint().height())
        self.splitter.setSizes([top, max(1, total - top)])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self, self.fit_divider)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_divider()

    def set_collapsed(self, collapsed: bool) -> None:
        self.scheme_area.setVisible(not collapsed)
        self.editor.set_folded(collapsed)
        self.collapse_btn.blockSignals(True)
        self.collapse_btn.setChecked(collapsed)
        self.collapse_btn.blockSignals(False)
        self.collapse_btn.setText("▸ Scheme" if collapsed else "▾ Scheme")
        if not collapsed:
            QTimer.singleShot(0, self, self.fit_divider)

    # ------------------------------------------------------------------ name

    def _show_name_start(self) -> None:
        self.name_edit.setCursorPosition(0)
        self.name_edit.setToolTip(self.scheme.name)

    def _commit_name(self) -> None:
        name = self.name_edit.text().strip()
        if not self.name_edit.hasFocus():
            self.name_edit.setCursorPosition(0)   # (clicked away: show the start again)
        if self.project.read_only or name == self.scheme.name:
            return
        if not name:
            self.name_edit.setText(self.scheme.name)
            self._show_name_start()
            return
        self.project.rename_scheme(self.scheme.id, name)
        self.name_edit.setToolTip(self.scheme.name)
        self.labels_changed.emit()
        self.saved.emit("Saved")

    def _update_label_placeholder(self) -> None:
        from ..core.labels import compute
        printed = se.scheme_layout(self.project.settings, self.scheme)["show_label"]
        labels = compute(self.project)
        item = labels.by_id.get(self.scheme.id)
        self.name_row.setVisible(printed)
        if printed:
            self.label_edit.setPlaceholderText(f"{item.auto} (automatic)" if item
                                               else "(automatic)")
            self.label_edit.setToolTip(
                "The label printed under the scheme (and wherever it is mentioned). Leave "
                "empty to number it automatically (Project settings ▸ Labels); its name is "
                "the caption.")
        else:
            self.label_edit.setPlaceholderText("not printed")
            self.label_edit.setToolTip(
                "This scheme is printed without a label or caption (the default). To number "
                "it (Scheme S1…) with its name as the caption, tick “Label and caption” in "
                "its Layout… (or in Project settings ▸ Schemes for every scheme).")
        self.label_edit.setEnabled(printed or bool(self.label_edit.text()))
        self.label_edit.setFixedWidth(self.label_edit.fontMetrics().horizontalAdvance(
            self.label_edit.placeholderText() + "M") + 16)   # (fits beside the live preview)
        duplicate = item is not None and labels.is_duplicate(item.label)
        self.label_edit.setStyleSheet("QLineEdit { color: #d35400; }" if duplicate else "")

    def _commit_label(self) -> None:
        label = self.label_edit.text().strip()
        if self.project.read_only or label == str(self.scheme.data.get("label", "")):
            return
        try:
            self.project.set_scheme_label(self.scheme.id, label)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save the label", str(e))
            return
        self._update_label_placeholder()
        self.labels_changed.emit()
        self.saved.emit("Saved")

    # ------------------------------------------------------------------ sub-pages (tabs)

    def _fill_tabs(self, select: str | None = None) -> None:
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        report = self.project.id_report()
        for child in self.scheme.children:
            i = self.tabs.addTab(f"{report.display_id(child.id)}  {child.name or '(unnamed)'}")
            self.tabs.setTabData(i, child.id)
        self.tabs.blockSignals(False)
        ids = [c.id for c in self.scheme.children]
        index = ids.index(select) if select in ids else (0 if ids else -1)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        self._show_compound(ids[index] if index >= 0 else None)

    def _refresh_tab_texts(self) -> None:
        report = self.project.id_report()
        for i in range(self.tabs.count()):
            cid = self.tabs.tabData(i)
            try:
                c = self.project.compound(cid)
            except KeyError:
                continue
            self.tabs.setTabText(i, f"{report.display_id(cid)}  {c.name or '(unnamed)'}")

    def undo_context(self) -> tuple:
        """(merge key, label) of the undo step for this page's saves (SPEC §13k.11): the
        compound tab's while one of its fields was just edited, else the scheme page's."""
        panel = self.compound_panel
        if panel is not None and panel._changed_keys:
            return panel.undo_context()
        scheme = self.scheme
        return ("scheme", scheme.id), lambda: f"Edit scheme page “{scheme.name}”"

    def current_compound_id(self) -> str | None:
        i = self.tabs.currentIndex()
        return self.tabs.tabData(i) if i >= 0 else None

    def has_compound(self, cid: str) -> bool:
        return any(self.tabs.tabData(i) == cid for i in range(self.tabs.count()))

    def select_compound(self, cid: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == cid:
                self.tabs.setCurrentIndex(i)
                return

    def _tab_changed(self, index: int) -> None:
        self.flush()
        cid = self.tabs.tabData(index) if index >= 0 else None
        self._show_compound(cid)
        if cid:
            self.compound_selected.emit(cid)

    def _show_compound(self, cid: str | None) -> None:
        if self.compound_panel is not None:
            self.body.removeWidget(self.compound_panel)
            self.compound_panel.deleteLater()
            self.compound_panel = None
        self.empty.setVisible(cid is None)
        if cid is None:
            return
        panel = CompoundPanel(self.project, self.project.compound(cid), self.user_name)
        panel.labels_changed.connect(self._compound_labels_changed)
        panel.saved.connect(self.saved)
        panel.saved.connect(self._compound_saved)
        panel.structure_updated.connect(lambda _cid: self.editor.refresh_boxes())
        panel.view_spectra.connect(self.view_spectra)
        self.compound_panel = panel
        self.body.addWidget(panel, 1)

    def _compound_labels_changed(self, _cid: str) -> None:
        self._refresh_tab_texts()
        self.editor.refresh_boxes(redraw=False)
        self.labels_changed.emit()

    def _compound_saved(self, _message: str) -> None:
        """A compound tab saved (e.g. its yield): the scheme editor shows it."""
        self.editor.refresh_boxes(redraw=False)

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        act = self.add_menu.addAction("Blank compound (not drawn in the scheme)",
                                      lambda: self.add_compound())
        act.setToolTip("Usually: tick “Compound” under a product in the scheme instead")
        self.add_menu.setToolTipsVisible(True)

    def add_compound(self) -> None:
        self.flush()
        index = self.tabs.currentIndex() + 1 if self.tabs.currentIndex() >= 0 else None
        try:
            compound = self.project.add_compound(self.scheme.section_id, index,
                                                 scheme_id=self.scheme.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not add compound", str(e))
            return
        self._fill_tabs(compound.id)
        self.labels_changed.emit()
        if self.compound_panel is not None:
            self.compound_panel.focus_field("name")

    def _close_tab(self, index: int) -> None:
        cid = self.tabs.tabData(index)
        compound = self.project.compound(cid)
        if QMessageBox.question(
                self, "Delete compound",
                f"Delete “{self.project.id_report().display_id(cid)} "
                f"{compound.name or '(unnamed)'}” from this scheme?\n\nIt will be moved to the "
                "project Trash and can be restored. Compounds after it will be renumbered."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.flush()
        self._show_compound(None)
        try:
            self.project.delete_compound(cid)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not delete", str(e))
        self._fill_tabs()
        self.editor.rebuild()   # (its box keeps a copy of the structure, unlinked)
        self.labels_changed.emit()

    # ------------------------------------------------------------------ saving

    def flush(self) -> None:
        self._commit_name()
        self._commit_label()
        self.editor.flush()
        if self.compound_panel is not None:
            self.compound_panel.flush()
