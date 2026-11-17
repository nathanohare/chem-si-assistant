"""Page flow controls (core/page_flow.py, SPEC §13k.2b): the project settings, shown in
Project settings ▸ Page and in the Compile window, and the page-break button on each
section's and compound's page."""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QMenu,
                               QMessageBox, QSpinBox, QToolButton, QWidget)

from ..core import page_flow
from ..core.project import Project, ProjectError


class PageFlowSettings(QGroupBox):
    """The project's page flow settings."""

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__("Page flow (how text is split over pages)", parent)
        f = page_flow.flow_settings(settings)
        self.heading_lines = QSpinBox()
        self.heading_lines.setRange(0, page_flow.MAX_HEADING_LINES)
        self.heading_lines.setSuffix(" lines")
        self.heading_lines.setSpecialValueText("Off")
        self.heading_lines.setValue(f.heading_lines)
        self.heading_lines.setToolTip(
            "A heading (section, sub-section, general procedure, compound title) moves to "
            "the next page unless at least this many lines of what follows it fit under it. "
            "Off: a heading only stays with the first line after it.")
        self.widows = QComboBox()
        for key, name in page_flow.WIDOWS.items():
            self.widows.addItem(name, key)
        self.widows.setCurrentIndex(max(0, self.widows.findData(f.widows)))
        self.widows.setToolTip(
            "How hard to avoid a paragraph's first line alone at the bottom of a page, or "
            "its last line alone at the top of the next. Strong avoids them more often, "
            "at the cost of a slightly emptier page bottom now and then.")
        self.emphasis = QCheckBox("Keep a short bold or italic line with what follows, like a "
                                  "heading")
        self.emphasis.setChecked(f.emphasis_headings)
        self.emphasis.setToolTip(
            "A line typed in bold or italic on its own (e.g. “Solvent Effect on Reaction "
            "Yield”) is treated like a heading: it is not left alone at the bottom of a page. "
            "Lines ending with “.” or “:” are ordinary text and are not affected.")
        self.keep_compounds = QCheckBox("Keep each compound on one page (moved whole to the "
                                        "next page instead of being split)")
        self.keep_compounds.setChecked(f.keep_compounds)
        self.keep_compounds.setToolTip(
            "A compound entry (title, structure, procedure, data) that fits on one page is "
            "not split over two. This leaves more empty space at the bottom of some pages. "
            "A compound longer than a page is still split. Each compound can override this "
            "with its “Page breaks” button.")
        self.data_spacing = QComboBox()
        for key, name in page_flow.DATA_SPACINGS.items():
            self.data_spacing.addItem(name, key)
        self.data_spacing.setCurrentIndex(max(0, self.data_spacing.findData(f.data_spacing)))
        self.data_spacing.setToolTip(
            "Where a compound's characterization data (“Colorless oil (57 mg, 94%). ¹H NMR "
            "…”) starts after its procedure. Compounds with their own SI text are printed "
            "as typed.")
        form = QFormLayout(self)
        form.addRow("Keep headings with:", self.heading_lines)
        form.addRow("Avoid single lines at page edges:", self.widows)
        form.addRow(self.emphasis)
        form.addRow(self.keep_compounds)
        form.addRow("Data after the procedure:", self.data_spacing)

    def flow(self) -> page_flow.Flow:
        return page_flow.Flow(heading_lines=self.heading_lines.value(),
                              widows=self.widows.currentData(),
                              emphasis_headings=self.emphasis.isChecked(),
                              keep_compounds=self.keep_compounds.isChecked(),
                              data_spacing=self.data_spacing.currentData())

    def apply(self, settings: dict) -> None:
        page_flow.store_flow_settings(settings, self.flow())


class PageFlowButton(QToolButton):
    """“Page breaks” on a section's or compound's page: start it on a new page, keep it
    on one page, or let it split (overriding the project setting)."""

    def __init__(self, project: Project, data: Callable[[], dict], save: Callable[[], None],
                 label: Callable[[], str], compound: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self._data = data
        self._save = save
        self._label = label
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip("Page breaks for this " + ("compound" if compound else "section") +
                        " in the compiled SI: start it on a new page, keep it on one page, "
                        "or let it run over a page break.")
        menu = QMenu(self)
        self.new_page = QAction("Start on a new page", menu, checkable=True)
        self.new_page.toggled.connect(lambda _on: self._changed())
        menu.addAction(self.new_page)
        menu.addSeparator()
        keep_title = menu.addAction("Keep together:")
        keep_title.setEnabled(False)
        group = QActionGroup(menu)
        self.keeps: dict[str, QAction] = {}
        names = dict(page_flow.KEEPS)
        if not compound:
            names["default"] = "May split over pages (normal)"
            names.pop("split")
        for key, name in names.items():
            action = QAction("    " + name, menu, checkable=True)
            group.addAction(action)
            action.toggled.connect(lambda on, k=key: on and self._changed())
            menu.addAction(action)
            self.keeps[key] = action
        self.setMenu(menu)
        self._loading = False
        self.refresh()
        self.setEnabled(not project.read_only)

    def refresh(self) -> None:
        self._loading = True
        f = page_flow.item_flow(self._data())
        self.new_page.setChecked(f["new_page"])
        self.keeps.get(f["keep"], self.keeps["default"]).setChecked(True)
        self._loading = False
        said = page_flow.describe_item(self._data())
        self.setText("Page breaks" + (f": {said}" if said else ""))

    def _changed(self) -> None:
        if self._loading or self.project.read_only:
            return
        keep = next((k for k, a in self.keeps.items() if a.isChecked()), "default")
        data = self._data()
        old = data.get(page_flow.KEY)
        page_flow.set_item_flow(data, self.new_page.isChecked(), keep)
        if data.get(page_flow.KEY) == old:
            return
        try:
            with self.project.undo_step(f"Page breaks of {self._label()}"):
                self._save()
        except (ProjectError, OSError) as e:
            if old is None:
                data.pop(page_flow.KEY, None)
            else:
                data[page_flow.KEY] = old
            QMessageBox.warning(self, "Could not save", str(e))
        self.refresh()
