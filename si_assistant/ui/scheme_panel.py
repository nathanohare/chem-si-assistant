"""Scheme page (SPEC §6.3.5): a reaction scheme, drawn with the scheme editor (SPEC §13n.4,
ui/scheme_editor_ui.py), and its compounds as tabs (sub-pages) underneath. (One pasted
picture of the whole scheme, with draggable ID labels, was removed: SPEC §13p.5.)"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QSplitter, QTabBar, QToolButton, QVBoxLayout, QWidget)

from ..core import scheme_editor as se
from ..core.project import BoxCopyFailed, Project, ProjectError, SchemePage
from .compound_panel import CompoundPanel


class SchemePanel(QWidget):
    labels_changed = Signal()            # IDs/names may have changed: refresh the tree
    saved = Signal(str)
    compound_selected = Signal(str)      # tab changed: keep the tree selection in step
    view_spectra = Signal(str)           # a compound's "View spectra page": compound id
    read_back = Signal()                 # a scheme change failed part-way (see the editor)

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
        self.editor.read_back.connect(self.read_back)
        self.editor.before_change = self._before_editor_change
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
        """The name typed (its box was left): saved, or a warning if it can't be."""
        try:
            self._save_name()
        except (ProjectError, OSError) as e:
            self._say_once(self.name_edit, "Could not save the name", e)

    def _say_once(self, edit: QLineEdit, title: str, e: Exception) -> None:
        """The save of the text typed in `edit` (the name or label) failed: said, unless it
        was said for the same text a moment ago. Remembered, so leaving the page doesn't
        say it again (the main window then only asks whether to leave anyway)."""
        text = edit.text().strip()
        if getattr(edit, "_said", None) == text:
            return
        edit._said = text
        QMessageBox.warning(self, title, f"{e}\n\nWhat you typed is still in its box; "
                            "saving it is tried again when you change it or leave the page.")

    def _save_name(self) -> None:
        """Raises ProjectError/OSError when scheme.json can't be saved (e.g. held)."""
        name = self.name_edit.text().strip()
        if not self.name_edit.hasFocus():
            self.name_edit.setCursorPosition(0)   # (clicked away: show the start again)
        if self.project.read_only:
            return
        if name == self.scheme.name:
            self.name_edit._unsaved = False   # (nothing (more) to save)
            return
        if not name:
            self.name_edit.setText(self.scheme.name)
            self._show_name_start()
            self.name_edit._unsaved = False
            return
        self._saving_scheme_value("name", lambda: self.project.rename_scheme(self.scheme.id,
                                                                             name),
                                  self.name_edit)
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
        try:
            self._save_label()
        except (ProjectError, OSError) as e:
            self._say_once(self.label_edit, "Could not save the label", e)

    def _save_label(self) -> None:
        """Raises ProjectError/OSError when scheme.json can't be saved (e.g. held)."""
        label = self.label_edit.text().strip()
        if self.project.read_only:
            return
        if label == str(self.scheme.data.get("label", "")):
            self.label_edit._unsaved = False   # (nothing (more) to save)
            return
        self._saving_scheme_value("label", lambda: self.project.set_scheme_label(
            self.scheme.id, label), self.label_edit)
        self._update_label_placeholder()

    _MISSING = object()

    def _saving_scheme_value(self, key: str, save: Callable[[], None],
                             edit: QLineEdit) -> None:
        """`save` puts the value typed in `edit` in scheme.json. If that fails (the file is
        held), the scheme in memory goes back to what scheme.json still has: the box keeps
        the typing, which is tried again (and is not written unseen by another save); its
        _unsaved tells the main window, which asks before the page is closed."""
        before = self.scheme.data.get(key, self._MISSING)
        try:
            save()
        except (ProjectError, OSError):
            if before is self._MISSING:
                self.scheme.data.pop(key, None)
            else:
                self.scheme.data[key] = before
            edit._unsaved = True
            raise
        edit._unsaved, edit._said = False, None
        self.labels_changed.emit()
        self.saved.emit("Saved")

    # ------------------------------------------------------------------ sub-pages (tabs)

    def _fill_tabs(self, select: str | None = None, held: str | None = None) -> None:
        """`held`: the compound on show whose typing could not be saved a moment ago, in
        the same action: it stays on show, without trying (and waiting) again."""
        # Save typing still waiting on the page on show (it is replaced below), unless that
        # compound has just been removed: saving it would re-create its folder.
        old, keep = self.compound_panel, None
        if old is not None:
            try:
                alive = self.project.compound(old.compound.id) is old.compound
            except KeyError:
                alive = False
            if alive and old.compound.id == held:
                keep = held
            elif alive:
                try:
                    old.flush()
                except (ProjectError, OSError) as e:
                    # (its file is held, e.g. by OneDrive: that page stays on show with its
                    # typing, which is tried again with the next edit or when it is left)
                    keep = old.compound.id
                    self.saved.emit(f"Could not save ({e}). It is tried again with your next "
                                    "edit or when you leave this page.")
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        report = self.project.id_report()
        for child in self.scheme.children:
            # ('&&': a name like "R&D" shows its "&", not an Alt+ shortcut)
            i = self.tabs.addTab(f"{report.display_id(child.id)}  "
                                 f"{(child.name or '(unnamed)').replace('&', '&&')}")
            self.tabs.setTabData(i, child.id)
        ids = [c.id for c in self.scheme.children]
        if keep in ids:
            select = keep
        index = ids.index(select) if select in ids else (0 if ids else -1)
        if index >= 0:
            self.tabs.setCurrentIndex(index)   # (quietly: the page is built once, below)
        self.tabs.blockSignals(False)
        cid = ids[index] if index >= 0 else None
        if cid is None or cid != keep:
            self._show_compound(cid)
        if cid:
            # (the main window keeps the tree and its page place, used for undo, on this tab)
            self.compound_selected.emit(cid)

    def _refresh_tab_texts(self) -> None:
        report = self.project.id_report()
        for i in range(self.tabs.count()):
            cid = self.tabs.tabData(i)
            try:
                c = self.project.compound(cid)
            except KeyError:
                continue
            self.tabs.setTabText(i, f"{report.display_id(cid)}  "
                                    f"{(c.name or '(unnamed)').replace('&', '&&')}")

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
        return cid in self._tab_ids()

    def _tab_ids(self) -> list[str]:
        return [self.tabs.tabData(i) for i in range(self.tabs.count())]

    def select_compound(self, cid: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == cid:
                self.tabs.setCurrentIndex(i)
                return

    def _tab_changed(self, index: int) -> None:
        try:
            self._flush_scheme()
        except (ProjectError, OSError) as e:
            # (the scheme's own file: the tab can still change, the compound page on show
            # is saved by itself below)
            self.saved.emit(f"Could not save the scheme page ({e}). It is tried again with "
                            "your next edit or when you leave this page.")
        try:
            self._flush_compound()
        except (ProjectError, OSError) as e:
            # (the tab on show can't be saved, e.g. held by OneDrive: it stays on show with
            # its typing, which is tried again with the next edit or when it is left)
            self.saved.emit(f"Could not save the compound page on show ({e}). It is tried "
                            "again with your next edit or when you leave this page.")
            shown = self.compound_panel.compound.id if self.compound_panel else None
            if shown is not None:
                self.tabs.blockSignals(True)
                self.select_compound(shown)
                self.tabs.blockSignals(False)
                # The tree, and the page's place for undo, go back to it too (the switch may
                # have come from a click in the tree, which sets both to the compound
                # clicked once this returns: so this is done just after).
                QTimer.singleShot(0, self, lambda: self._still_on_show(shown))
            return
        cid = self.tabs.tabData(index) if index >= 0 else None
        self._show_compound(cid)
        if cid:
            self.compound_selected.emit(cid)

    def _still_on_show(self, cid: str) -> None:
        """After a tab change that could not happen: the main window's tree selection
        follows the compound still on show."""
        panel = self.compound_panel
        if panel is not None and panel.compound.id == cid \
                and self.current_compound_id() == cid:
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
        # (typing waiting to be saved: its own undo step first)
        try:
            self._flush_scheme()
        except (ProjectError, OSError) as e:
            # (the new compound is listed in scheme.json: it can't be added either now)
            QMessageBox.warning(
                self, "Could not save",
                f"The scheme page could not be saved yet ({e}), so no compound was added. "
                "Try again in a moment (its file may be open in another program or being "
                "synced).")
            return
        held = None
        try:
            self._flush_compound()
        except (ProjectError, OSError) as e:
            # (carries on: the page on show then stays on show with its typing)
            held = self.compound_panel.compound.id if self.compound_panel else None
            QMessageBox.warning(
                self, "Could not save",
                f"The typing on the compound page on show could not be saved yet ({e}). "
                "The new compound is added as a tab; the page you were typing on stays open "
                "until it can be saved (its file may be open in another program or being "
                "synced). Try again in a moment.")
        index = self.tabs.currentIndex() + 1 if self.tabs.currentIndex() >= 0 else None
        scheme, shown = self.scheme, self.current_compound_id()
        # (undone: the tab that was on show; redone: the new compound)
        where = ([("compound", shown)] if shown else []) + \
            [("scheme", scheme.id), ("section", scheme.section_id)]
        try:
            with self.project.undo_step("New compound", where) as step:
                compound = self.project.add_compound(scheme.section_id, index,
                                                     scheme_id=scheme.id)
                if step is not None:   # (None when the project keeps no history)
                    step.where.insert(0, ("compound", compound.id))
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not add compound", str(e))
            if [c.id for c in scheme.children] != self._tab_ids():
                # (added in memory but not saved: its tab shows it as it is)
                self._fill_tabs(shown, held=held)
                self.labels_changed.emit()
            return
        self._fill_tabs(compound.id, held=held)
        self.labels_changed.emit()
        if self.compound_panel is not None and self.compound_panel.compound is compound:
            self.compound_panel.focus_field("name")

    def _close_tab(self, index: int) -> None:
        cid = self.tabs.tabData(index)
        compound = self.project.compound(cid)
        shown = self.project.id_report().display_id(cid)   # (before renumbering)
        if QMessageBox.question(
                self, "Delete compound",
                f"Delete “{shown} "
                f"{compound.name or '(unnamed)'}” from this scheme?\n\nIt will be moved to the "
                "project Trash and can be restored. Compounds after it will be renumbered."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._flush_scheme()
        except (ProjectError, OSError) as e:
            # (deleting changes scheme.json too: it would stop half-way)
            QMessageBox.warning(
                self, "Could not save",
                f"The scheme page could not be saved yet ({e}), so nothing was deleted. Try "
                "again in a moment (its file may be open in another program or being "
                "synced).")
            return
        on_show = self.current_compound_id()
        held = None
        try:
            self._flush_compound()
        except (ProjectError, OSError) as e:
            panel = self.compound_panel
            held = panel.compound.id if panel is not None else None
            if panel is None or panel.compound.id != cid:
                # (closing the page on show would throw its typing away: nothing deleted)
                QMessageBox.warning(
                    self, "Could not save",
                    f"The typing on the compound page on show could not be saved yet ({e}), "
                    "so nothing was deleted. Try again in a moment (its file may be open in "
                    "another program or being synced).")
                return
            # The typing waiting is on the very compound being deleted: it goes with it. Its
            # page stays until the delete has worked (a held file usually stops the move to
            # the Trash too: the page, and its typing still waiting, are then kept).
        else:
            self._show_compound(None)
        scheme = self.scheme
        try:
            with self.project.undo_step(f"Delete compound {shown}",
                                        [("compound", cid), ("scheme", scheme.id),
                                         ("section", scheme.section_id)]):
                self.project.delete_compound(cid)
        except BoxCopyFailed as e:   # (deleted: only its box in the scheme is left empty)
            QMessageBox.warning(self, "Scheme box left empty", str(e))
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not delete", str(e))
        # (the page that was on show, if it is still there; its typing that could not be
        # saved just now is not tried, and waited on, again)
        self._fill_tabs(on_show, held=held)
        self.editor.rebuild()   # (its box keeps a copy of the structure, unlinked)
        self.labels_changed.emit()

    # ------------------------------------------------------------------ saving

    def flush(self) -> None:
        """Save all typing waiting on the page. Both parts are tried (a held scheme.json
        doesn't stop the compound tab's typing from being saved); the first failure is
        raised (ProjectError/OSError). A failure of the scheme's own typing that was said a
        moment ago (as its box was left, or by the conditions' autosave), with nothing typed
        since, is not raised again: the main window sees it waiting (_unsaved) and only
        asks whether to leave anyway."""
        failed = None
        for part in (self._flush_scheme, self._flush_compound):
            try:
                part()
            except (ProjectError, OSError) as e:
                if part == self._flush_scheme and self._scheme_failures_said():
                    continue
                failed = failed or e
        if failed is not None:
            raise failed

    def _scheme_failures_said(self) -> bool:
        """Each name, label or conditions box whose save failed was said, with nothing
        typed in it since (False if none is marked as waiting)."""
        edits = [e for e in (self.name_edit, self.label_edit)
                 if getattr(e, "_unsaved", False) is True]
        steps = [s for s in self.editor.steps if s._unsaved]
        if not edits and not steps:
            return False
        return (all(getattr(e, "_said", None) == e.text().strip() for e in edits)
                and all(s.failures_said() for s in steps))

    def _flush_scheme(self) -> None:
        """Save the scheme page's own typing: its name, label, and the scheme editor's. All
        are tried (the first failure is raised)."""
        failed = None
        for part in (self._save_name, self._save_label, self.editor.flush):
            try:
                part()
            except (ProjectError, OSError) as e:
                failed = failed or e
        if failed is not None:
            raise failed

    def _before_editor_change(self) -> None:
        """Before a scheme editor change: the scheme's name and label typed are saved first
        (a change that fails part-way reads the project back from its files, which would
        drop them), then the compound tab's typing. Raises ProjectError/OSError if one
        can't be saved yet: the change is then not made."""
        self._save_name()
        self._save_label()
        self._flush_compound()

    def _flush_compound(self) -> None:
        """Save the compound tab's typing now (before the scheme editor changes the
        compounds: its tab is then rebuilt without saving)."""
        if self.compound_panel is not None:
            self.compound_panel.flush()
