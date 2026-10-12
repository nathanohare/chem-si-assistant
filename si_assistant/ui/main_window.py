"""Main window: welcome screen, section tree and main panel (SPEC §4.1, §5)."""

from __future__ import annotations

import copy
import getpass
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import (QEvent, QItemSelectionModel, QSettings, QStandardPaths, Qt,
                            QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
                               QApplication, QCheckBox, QDialog, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSplitter, QStackedWidget, QStyle, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, resources
from ..core import front
from ..core import spectra as spectra_mod
from ..core.fileio import path_length_warning
from ..core.lock import computer_name
from ..core.project import (FORMAT_VERSION, PROJECT_FILE, SECTION_TYPE_LABELS,
                            SPECTRA_NOT_DELETED, SPECTRA_SECTION, BoxCopyFailed, Project,
                            ProjectError, SchemePage)
from ..core.undo import UndoContext
from . import (feedback_ui, help_ui, restart, rich_text, text_checks, undo_keys, update_ui,
               wheel_guard)
from ..compile.document import CompileOptions
from .compile_ui import CompileDialog, PreviewWindow, open_file, run_compile, show_warnings
from .compound_panel import CompoundPanel, make_read_only
from .warnings_panel import WarningsPanel
from .scheme_panel import SchemePanel
from .data_panel import DataSectionPanel
from .dialogs import ProjectSettingsDialog, TrashDialog, ask_about_lock
from .front_matter import PANELS as FRONT_PANELS, FrontMatterOverview
from .live_preview import MIN_WIDTH as MIN_PREVIEW_WIDTH, LivePreviewPane
from .panels import FileSectionPanel, SectionPanel, TextSectionPanel
from .project_dashboard import ProjectDashboard
from . import hrms_reports_ui as hrms_ui
from .spectra_page import SpectraPagePanel
from .spectra_panel import SpectraSectionPanel

LOCK_CHECK_INTERVAL_MS = 20_000
MAX_RECENT = 10
ID_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1   # "project" (the dashboard, id "project"),
                                           # "section", "scheme", "compound", "front_root",
                                           # "front" (id "cover", "toc" or "general") or
                                           # "spectra_page" (a compound's spectra; its id)
                                           # or "references" (the reference list) or
                                           # "manuscript" (the Manuscript check)
TYPE_ROLE = Qt.ItemDataRole.UserRole + 2   # section type: "text", "file", "data", "spectra"
PROJECT_ITEM = "project"                   # the project item's kind and id (SPEC §13k.3)
REFERENCES_ITEM = "references"             # its kind and id (SPEC §13n.10): always last
MANUSCRIPT_ITEM = "manuscript"             # the Manuscript check (SPEC §13o): just above it
# The Spectra section's own items: one page per compound with spectra, and "HRMS reports"
# (SPEC §13n.3); they follow the compounds and can't be moved, renamed or deleted.
HRMS_ITEM = "hrms_reports"
SPECTRA_CHILDREN = ("spectra_page", HRMS_ITEM)
FIXED_KINDS = (PROJECT_ITEM, "front_root", "front", MANUSCRIPT_ITEM,   # never moved,
               REFERENCES_ITEM)                                     # renamed or deleted
BOTTOM_KINDS = (MANUSCRIPT_ITEM, REFERENCES_ITEM)   # fixed items below the sections
PANEL_TYPES = {"text": TextSectionPanel, "file": FileSectionPanel, "data": DataSectionPanel,
               SPECTRA_SECTION: SpectraSectionPanel}
DELETE_TIP = "Delete the selected item (it goes to the project Trash)"
EXAMPLE_TIP = ("Open your copy of a finished example project (real compounds from the "
               "literature, simulated spectra) to see what the program does. The copy is yours "
               "to change; Help ▸ Start the example over gives you a fresh one.")
SIZE_TIP = ("The size of everything in the window (text, buttons, pictures), on top of this "
            "computer's display scaling. Saved on this computer; used when the program starts.")


def menu_text(text: str) -> str:
    """A name typed by the user, for the label of a menu item or button: "&" would
    otherwise be hidden and underline the next letter (an Alt+ shortcut), so it is
    doubled. (Not for tooltips, which show "&" as it is.)"""
    return text.replace("&", "&&")


@lru_cache(maxsize=1)
def spectrum_icon() -> QIcon:
    """The Spectra section's icon in the tree: a few peaks on a baseline (made when first
    needed, once the application exists)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#1f4e9a"), 2.4))
    p.drawLine(QPointF(2, 27), QPointF(30, 27))
    for x, h in ((6, 8), (10, 18), (13, 11), (18, 22), (22, 9), (26, 14)):
        p.drawLine(QPointF(x, 27), QPointF(x, 27 - h))
    p.end()
    return QIcon(pixmap)


class Preferences:
    """Per-computer preferences (not project data): your name, recent projects."""

    def __init__(self):
        self._s = QSettings(APP_NAME, APP_NAME)

    @property
    def user_name(self) -> str:
        return str(self._s.value("user_name", "") or "")

    @user_name.setter
    def user_name(self, value: str) -> None:
        self._s.setValue("user_name", value)

    @property
    def recent(self) -> list[str]:
        value = self._s.value("recent_projects", []) or []
        return [value] if isinstance(value, str) else list(value)

    def add_recent(self, folder: Path) -> None:
        path = str(Path(folder).resolve())
        items = [p for p in self.recent if p != path]
        self._s.setValue("recent_projects", [path, *items][:MAX_RECENT])

    def remove_recent(self, folder: str) -> None:
        self._s.setValue("recent_projects", [p for p in self.recent if p != folder])

    def last_project(self) -> Path | None:
        """The most recently opened project, if its folder is still there."""
        recent = self.recent
        if not recent:
            return None
        path = Path(recent[0])
        return path if path.is_dir() else None

    @property
    def open_last_at_start(self) -> bool:
        """"Open the last project when the program starts" (SPEC §13l.3). Off by default."""
        value = self._s.value("open_last_at_start", False)
        return value if isinstance(value, bool) else str(value).lower() == "true"

    @open_last_at_start.setter
    def open_last_at_start(self, on: bool) -> None:
        self._s.setValue("open_last_at_start", bool(on))

    @property
    def live_preview(self) -> bool:
        """Is the live preview pane shown (not collapsed)? On by default."""
        value = self._s.value("live_preview/shown", True)
        return value if isinstance(value, bool) else str(value).lower() != "false"

    @live_preview.setter
    def live_preview(self, shown: bool) -> None:
        self._s.setValue("live_preview/shown", bool(shown))

    @property
    def live_preview_width(self) -> int:
        try:
            return max(MIN_PREVIEW_WIDTH, int(self._s.value("live_preview/width", 420)))
        except (TypeError, ValueError):
            return 420

    @live_preview_width.setter
    def live_preview_width(self, width: int) -> None:
        self._s.setValue("live_preview/width", int(width))

    @property
    def interface_size(self) -> int:
        """View ▸ Interface size in percent (SPEC §13l.2); applied when the program starts."""
        return restart.read_size(self._s)

    @interface_size.setter
    def interface_size(self, percent: int) -> None:
        self._s.setValue(restart.SETTING, int(percent))

    def value(self, key: str, default=None):
        return self._s.value(key, default)

    def set_value(self, key: str, value) -> None:
        self._s.setValue(key, value)

    def sync(self) -> None:
        """Write the settings now (before another copy of the program reads them)."""
        self._s.sync()


# ---------------------------------------------------------------------- welcome page

def _same_folder(a: Path, b: Path) -> bool:
    """Whether two paths name the same folder (case and spelling of the path aside)."""
    try:
        return Path(a).resolve() == Path(b).resolve() or Path(a).samefile(b)
    except OSError:
        return False


class WelcomePage(QWidget):
    new_requested = Signal()
    open_requested = Signal()
    example_requested = Signal()   # open a copy of the example project
    example_pdf_requested = Signal()   # view the example's compiled SI (PDF)
    recent_chosen = Signal(str)
    open_last_toggled = Signal(bool)   # "Open the last project when the program starts"

    def __init__(self, prefs: Preferences, parent: QWidget | None = None):
        super().__init__(parent)
        self.prefs = prefs
        title = QLabel(f"<h1>{APP_NAME}</h1><p>Build the Supporting Information for your paper.</p>")
        new_btn = QPushButton("New Project…")
        open_btn = QPushButton("Open Project…")
        # A finished example to look around in, shown prominently so a first-time user
        # can see straight away what the program makes (a copy is opened; also in the
        # Help menu).
        self.example_btn = QPushButton("Open the Example Project")
        self.example_btn.setToolTip(EXAMPLE_TIP)
        self.example_btn.setDefault(True)
        example_pdf_btn = QPushButton("View Its Finished SI (PDF)")
        example_pdf_btn.setToolTip("The PDF the program compiled from the example project")
        example_pdf_btn.clicked.connect(self.example_pdf_requested)
        # The most recent project, one click away (SPEC §13l.3); hidden when there is none
        # or its folder is gone.
        self.last_btn = QPushButton()
        for b in (new_btn, open_btn, self.example_btn, example_pdf_btn, self.last_btn):
            b.setMinimumHeight(36)
        new_btn.clicked.connect(self.new_requested)
        open_btn.clicked.connect(self.open_requested)
        self.example_btn.clicked.connect(self.example_requested)
        self.last_btn.clicked.connect(self._open_last)
        self.open_last_box = QCheckBox("Open the last project when the program starts")
        self.open_last_box.setToolTip("Saved on this computer. Also in the View menu.")
        self.open_last_box.toggled.connect(self.open_last_toggled)
        self.recent_list = QListWidget()
        self.recent_list.itemActivated.connect(lambda item: self.recent_chosen.emit(item.data(ID_ROLE)))

        buttons = QHBoxLayout()
        buttons.addWidget(new_btn)
        buttons.addWidget(open_btn)
        buttons.addWidget(self.last_btn)
        buttons.addStretch()

        # The example, in a panel of its own.
        self.example_card = QFrame()
        self.example_card.setObjectName("exampleCard")
        self.example_card.setStyleSheet(
            "QFrame#exampleCard { border: 1px solid palette(highlight); border-radius: 8px; "
            "background: palette(alternate-base); }")
        card_text = QLabel(
            "<b style='font-size: 13pt'>New here? Start with the example SI</b><br>"
            "A finished Supporting Information to look around in: six compounds made by a "
            "general procedure, a reaction scheme, an optimisation table, NMR spectra and "
            "references, and the PDF compiled from them. A copy is opened, so you can change "
            "anything and see what happens.")
        card_text.setWordWrap(True)
        card_buttons = QHBoxLayout()
        card_buttons.addWidget(self.example_btn)
        card_buttons.addWidget(example_pdf_btn)
        card_buttons.addStretch()
        card = QVBoxLayout(self.example_card)
        card.setContentsMargins(14, 12, 14, 12)
        card.addWidget(card_text)
        card.addLayout(card_buttons)

        inner = QVBoxLayout()
        inner.addWidget(title)
        inner.addLayout(buttons)
        inner.addWidget(self.open_last_box)
        inner.addSpacing(12)
        inner.addWidget(self.example_card)
        inner.addSpacing(16)
        inner.addWidget(QLabel("<b>Recent projects</b> (double-click to open)"))
        inner.addWidget(self.recent_list)
        box = QWidget()
        box.setLayout(inner)
        box.setMaximumWidth(720)
        outer = QHBoxLayout(self)
        outer.addStretch()
        outer.addWidget(box, 3)
        outer.addStretch()
        self.refresh()

    def _open_last(self) -> None:
        last = self.prefs.last_project()
        if last is not None:
            self.recent_chosen.emit(str(last))

    def refresh(self) -> None:
        last = self.prefs.last_project()
        self.last_btn.setVisible(last is not None)
        if last is not None:
            self.last_btn.setText(f"Open “{menu_text(last.name)}”")
            self.last_btn.setToolTip(f"Open the project you had open last:\n{last}")
        self.open_last_box.blockSignals(True)
        self.open_last_box.setChecked(self.prefs.open_last_at_start)
        self.open_last_box.blockSignals(False)
        self.recent_list.clear()
        for path in self.prefs.recent:
            item = QListWidgetItem(f"{Path(path).name}    —    {path}")
            item.setData(ID_ROLE, path)
            if not Path(path).exists():
                item.setText(item.text() + "   (not found)")
                item.setForeground(Qt.GlobalColor.gray)
            self.recent_list.addItem(item)


# ---------------------------------------------------------------------- section tree

class SectionTree(QTreeWidget):
    """The project item first (its dashboard, SPEC §13k.3), then the fixed "Front
    matter" item (Cover page, Table of contents, General Information), then the sections,
    then the fixed "References" item (the reference list, SPEC §13n.10). Custom sections
    contain sub-sections (any type, down to 1.1.1); Data sections contain compounds and
    scheme pages; scheme pages contain their compounds.

    Drag-and-drop rules: sections move before/after other sections or into custom
    sections; the project, front matter and References items cannot be moved and nothing
    can be dropped into them (a section dropped on the first two goes first, after the
    front matter; one dropped on References goes last, just before it); scheme
    pages move within or between Data sections; compounds move within or between Data
    sections and scheme pages. The tree only reports where something was dropped; the
    main window updates the project and rebuilds the tree.
    """

    section_dropped = Signal(str, str, int)        # section id, parent id or "", slot
    item_dropped = Signal(str, str, str, str, int)  # kind, id, section id, scheme id or "", slot

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # (section id, parent id or "") -> may it go there? Set by the main window.
        self.can_place_section = lambda _section_id, _parent_id: True

    @staticmethod
    def _index(item: QTreeWidgetItem) -> int:
        return item.parent().indexOfChild(item)

    def top_section_count(self) -> int:
        """How many sections there are at the top level (the fixed items don't count)."""
        return sum(1 for i in range(self.topLevelItemCount())
                   if self.topLevelItem(i).data(0, KIND_ROLE) == "section")

    def section_index(self, item: QTreeWidgetItem) -> int:
        """A section item's position among its sibling sections (the project and front
        matter items at the very top do not count)."""
        if item.parent() is not None:
            return item.parent().indexOfChild(item)
        first = 0
        while first < self.topLevelItemCount() and \
                self.topLevelItem(first).data(0, KIND_ROLE) in FIXED_KINDS:
            first += 1
        return self.indexOfTopLevelItem(item) - first

    def _section_drop(self, dragged, target, where):
        """("section", parent id or "", slot) for a dragged section, or None."""
        DIP = QAbstractItemView.DropIndicatorPosition
        if target is None or target.data(0, KIND_ROLE) in BOTTOM_KINDS:
            spot = ("section", "", self.top_section_count())   # last, before the fixed
            #                                                    items at the bottom
        elif target.data(0, KIND_ROLE) in FIXED_KINDS:
            spot = ("section", "", 0)   # right after the front matter
        else:
            while target.data(0, KIND_ROLE) != "section":   # a compound: after its section
                target, where = target.parent(), DIP.BelowItem
            custom = target.data(0, TYPE_ROLE) == "text"
            parent = target.parent()
            parent_id = parent.data(0, ID_ROLE) if parent is not None else ""
            index = self.section_index(target)
            if custom and where == DIP.OnItem:
                spot = ("section", target.data(0, ID_ROLE), target.childCount())
            elif custom and where == DIP.BelowItem and target.isExpanded() and target.childCount():
                spot = ("section", target.data(0, ID_ROLE), 0)
            else:
                spot = ("section", parent_id, index if where == DIP.AboveItem else index + 1)
        if not self.can_place_section(dragged.data(0, ID_ROLE), spot[1]):
            return None
        return spot

    def _drop_target(self, event):
        dragged = self.currentItem()
        if dragged is None:
            return None
        target = self.itemAt(event.position().toPoint())
        where = self.dropIndicatorPosition()
        DIP = QAbstractItemView.DropIndicatorPosition
        kind = dragged.data(0, KIND_ROLE)
        if kind == "section":
            return self._section_drop(dragged, target, where)
        if kind not in ("compound", "scheme") or target is None:
            return None
        t_kind = target.data(0, KIND_ROLE)
        if t_kind in (*FIXED_KINDS, *SPECTRA_CHILDREN):
            return None
        first_inside = where == DIP.BelowItem and target.isExpanded() and target.childCount()

        if t_kind == "section":
            if target.data(0, TYPE_ROLE) != "data" or where == DIP.AboveItem:
                return None
            sid = target.data(0, ID_ROLE)
            return (kind, sid, "", 0 if first_inside else target.childCount())

        if kind == "scheme":  # scheme pages only live directly in a Data section
            if target.parent().data(0, KIND_ROLE) == "scheme":  # a sub-compound: after its scheme
                target, where = target.parent(), DIP.BelowItem
            sid = target.parent().data(0, ID_ROLE)
            i = self._index(target)
            return (kind, sid, "", i if where == DIP.AboveItem else i + 1)

        # A compound.
        if t_kind == "scheme":
            sid = target.parent().data(0, ID_ROLE)
            kid = target.data(0, ID_ROLE)
            i = self._index(target)
            if where == DIP.AboveItem:
                return (kind, sid, "", i)
            if where == DIP.OnItem:
                return (kind, sid, kid, target.childCount())
            return (kind, sid, kid, 0) if first_inside else (kind, sid, "", i + 1)
        parent = target.parent()
        i = self._index(target)
        slot = i if where == DIP.AboveItem else i + 1
        if parent.data(0, KIND_ROLE) == "scheme":
            return (kind, parent.parent().data(0, ID_ROLE), parent.data(0, ID_ROLE), slot)
        return (kind, parent.data(0, ID_ROLE), "", slot)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        super().dragMoveEvent(event)  # works out the drop-indicator position
        if self._drop_target(event) is None:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        target = self._drop_target(event)
        dragged = self.currentItem()
        # We never let Qt move the items itself: the project is updated, then the
        # tree is rebuilt from it.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if target is None or dragged is None:
            return
        if target[0] == "section":
            self.section_dropped.emit(dragged.data(0, ID_ROLE), target[1], target[2])
        else:
            kind, sid, kid, slot = target
            self.item_dropped.emit(kind, dragged.data(0, ID_ROLE), sid, kid, slot)


# ---------------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.prefs = Preferences()
        self._wheel_guard = wheel_guard.install(QApplication.instance())
        self.project: Project | None = None
        self.panel: QWidget | None = None
        self._panel_where: list[tuple[str, str]] = []   # the page's tree place (see _show)
        self._updating_tree = False
        self._collapsed: set[str] = set()  # Data sections the user collapsed
        # The tree's warnings, kept only while one compound page is on show (see
        # _refresh_labels): {compound id: (its ID-report messages, checks.all_warnings)},
        # valid while _warnings_guard() gives the same value.
        self._warn_cache: dict[str, tuple] = {}
        self._warn_guard = None

        self.resize(1200, 800)
        geometry = self.prefs.value("window_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.welcome = WelcomePage(self.prefs)
        self.welcome.new_requested.connect(self.new_project)
        self.welcome.open_requested.connect(self.choose_and_open_project)
        self.welcome.example_requested.connect(self.open_example_project)
        self.welcome.example_pdf_requested.connect(self.open_example_pdf)
        self.welcome.recent_chosen.connect(lambda p: self.open_project(Path(p)))
        self.welcome.open_last_toggled.connect(self.set_open_last_at_start)
        self.stack.addWidget(self.welcome)

        self.tree = SectionTree()
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        self.tree.section_dropped.connect(self._on_section_dropped)
        self.tree.item_dropped.connect(self._on_item_dropped)
        self.tree.itemCollapsed.connect(lambda i: self._collapsed.add(i.data(0, ID_ROLE)))
        self.tree.itemExpanded.connect(lambda i: self._collapsed.discard(i.data(0, ID_ROLE)))
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.can_place_section = self._can_place_section
        self.panel_holder = QStackedWidget()
        self.empty_panel = QLabel("Select a section on the left, or create one with “New section”.")
        self.empty_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.panel_holder.addWidget(self.empty_panel)

        # The live preview of the page on show (SPEC §13k.8), on the right; collapsed to a
        # thin strip when the user hides it, hidden for pages without a preview.
        self.preview_pane = LivePreviewPane()
        self.preview_pane.set_collapsed(not self.prefs.live_preview)
        self.preview_pane.collapsed_changed.connect(self._preview_collapsed)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.panel_holder)
        self.splitter.addWidget(self.preview_pane)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, False)
        self.splitter.setSizes([280, 900, self.prefs.live_preview_width])
        # Which state (collapsed or not) the splitter's preview slot was last sized for;
        # None: not yet (the pane starts hidden, so it is sized when it first appears).
        self._preview_slot: bool | None = None
        self.splitter.splitterMoved.connect(self._remember_preview_width)
        # (sized only once the splitter is on screen with its real width, see eventFilter)
        self.splitter.installEventFilter(self)
        self.stack.addWidget(self.splitter)

        self.ro_label = QLabel()
        self.statusBar().addPermanentWidget(self.ro_label)

        self.warnings_panel = WarningsPanel()
        self.warnings_panel.open_requested.connect(self.open_compound_at)
        self.warnings_panel.open_place.connect(self.select)
        self.warnings_panel.changed.connect(self._after_warnings_changed)
        self.warnings_dock = QDockWidget("Warnings", self)
        self.warnings_dock.setObjectName("warnings_dock")
        self.warnings_dock.setWidget(self.warnings_panel)
        self.warnings_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea
                                           | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.warnings_dock)
        self.warnings_dock.hide()
        self.warnings_dock.visibilityChanged.connect(self._warnings_visibility)
        # Refresh the panel shortly after edits (not on every keystroke).
        self._warnings_timer = QTimer(self)
        self._warnings_timer.setSingleShot(True)
        self._warnings_timer.setInterval(600)
        self._warnings_timer.timeout.connect(self.warnings_panel.refresh)

        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(LOCK_CHECK_INTERVAL_MS)
        self._lock_timer.timeout.connect(self._check_lock)

        self._build_actions()
        # Ctrl+Z in a text box undoes its typing first, then the project's saved changes.
        self._undo_keys = undo_keys.install(self, self.act_undo, self.act_redo)
        self._update_ui_state()

    # ------------------------------------------------------------------ actions/menus

    def _build_actions(self) -> None:
        style = self.style()

        def act(text, slot, shortcut=None, tip=None, icon=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            if icon is not None:
                a.setIcon(style.standardIcon(icon))
            return a

        SP = QStyle.StandardPixmap
        self.act_new_project = act("&New Project…", self.new_project, QKeySequence.StandardKey.New)
        self.act_open_project = act("&Open Project…", self.choose_and_open_project,
                                    QKeySequence.StandardKey.Open)
        self.act_close_project = act("&Close Project", self.close_project)
        self.act_snapshot = act("Save &Snapshot…", self.save_snapshot,
                                tip="Save a zipped copy of the whole project, e.g. 'submitted version'")
        self.act_trash = act("&Trash…", self.show_trash, icon=SP.SP_TrashIcon)
        self.act_show_folder = act("Show Project &Folder", self.show_folder)
        self.act_quit = act("&Quit", self.close, QKeySequence.StandardKey.Quit)

        self.act_new_section = {
            t: act(f"New {label.lower() if t == 'text' else label} section",
                   lambda _=False, t=t: self.new_section(t), tip=tip)
            for (t, label), tip in zip(SECTION_TYPE_LABELS.items(), (
                "A section of your own: text, pictures and sub-sections",
                "A section showing a PDF or picture made elsewhere (e.g. an X-ray report)",
                "A section of compound pages with their own ID numbering"))}
        # Sub-sections go inside the selected custom section (SPEC §13k.5).
        self.act_new_sub = {
            t: act(f"{label.capitalize() if t == 'text' else label} sub-section",
                   lambda _=False, t=t: self.new_section(t, inside=True))
            for t, label in SECTION_TYPE_LABELS.items()}
        self.act_new_compound = act("New &Compound", self.new_compound, "Ctrl+Shift+N",
                                    tip="Add a compound to the selected Data section",
                                    icon=SP.SP_FileDialogNewFolder)
        self.act_new_scheme = act("New Scheme &Page", self.new_scheme,
                                  tip="Add a reaction scheme page (a scheme with its compounds as "
                                      "sub-pages) to the selected Data section",
                                  icon=SP.SP_FileDialogContentsView)
        self.act_rename = act("&Rename…", self.rename_item, "F2")
        self.act_delete = act("&Delete…", self.delete_item, QKeySequence.StandardKey.Delete)
        if sys.platform == "darwin":
            # The Mac's delete key (⌫) is Backspace: ⌘⌫ as in Finder (shown in the menu),
            # ⌫ itself, and forward-delete (fn+⌫). Deleting always asks first.
            self.act_delete.setShortcuts([QKeySequence("Ctrl+Backspace"),
                                          QKeySequence(Qt.Key.Key_Backspace),
                                          QKeySequence(QKeySequence.StandardKey.Delete)])
        self.act_up = act("Move &Up", lambda: self.move_item(-1), "Ctrl+Up")
        self.act_down = act("Move Do&wn", lambda: self.move_item(1), "Ctrl+Down")
        # These shortcuts only apply when the tree has focus, so Delete/Backspace/F2
        # still work normally while typing in a text box.
        for a in (self.act_rename, self.act_delete, self.act_up, self.act_down):
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.tree.addAction(a)
        self.act_settings = act("Project &Settings…", self.show_settings, "Ctrl+,",
                                icon=SP.SP_FileDialogDetailedView)

        self.act_compile = act("Compile SI", self.compile_si, "Ctrl+Shift+B",
                               tip="Make the SI PDF (build/SI.pdf) from the whole project")
        self.act_preview = act("Preview", self.preview_current, "Ctrl+Shift+P",
                               tip="See the selected compound, scheme page or section as it will "
                                   "appear in the SI")
        self.act_live = act("Live preview", lambda on: self._preview_collapsed(not on, True),
                            "Ctrl+Shift+L", tip="Show or hide the live preview of the page you "
                                                "are editing, on the right")
        self.act_live.setCheckable(True)
        self.act_live.setChecked(self.prefs.live_preview)
        self.act_warnings = act("Warnings", lambda on: self.warnings_dock.setVisible(on),
                                "Ctrl+Shift+W", tip="Show every warning and flag in the project",
                                icon=SP.SP_MessageBoxWarning)
        self.act_warnings.setCheckable(True)
        # (The References page at the bottom of the tree, SPEC §13n.10; no toolbar button.)
        self.act_references = act("References", self.show_references, "Ctrl+Shift+R",
                                  tip="The project's reference list (the References item at "
                                      "the bottom of the tree): add by DOI, by hand or from "
                                      "BibTeX/RIS", icon=SP.SP_FileDialogInfoView)
        self._preview: PreviewWindow | None = None

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([self.act_new_project, self.act_open_project])
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addAction(self.act_close_project)
        file_menu.addSeparator()
        file_menu.addActions([self.act_snapshot, self.act_trash, self.act_show_folder,
                              self.act_references, self.act_settings])
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        self.new_sub_menu = QMenu("New sub-section", self)
        self.new_sub_menu.setToolTipsVisible(True)
        self.new_sub_menu.addActions(list(self.act_new_sub.values()))
        self.act_use_as_general = act("Use as General Information…", self.use_as_general_information,
                                      tip="Move this section's text into Front matter ▸ "
                                          "General Information")

        # Undo / redo of every saved change (SPEC §13k.11); the texts name the step.
        self.act_undo = act("&Undo", self.undo)
        self.act_undo.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Undo))
        self.act_redo = act("&Redo", self.redo)
        redo_keys = [QKeySequence("Ctrl+Y")]
        redo_keys += [k for k in QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)
                      if k not in redo_keys]
        self.act_redo.setShortcuts(redo_keys)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions([self.act_undo, self.act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions(list(self.act_new_section.values()))
        edit_menu.addMenu(self.new_sub_menu)
        edit_menu.addActions([self.act_new_compound, self.act_new_scheme])
        edit_menu.addSeparator()
        edit_menu.addActions([self.act_rename, self.act_delete, self.act_up, self.act_down])
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_use_as_general)

        # (SPEC §13l.3) A preference of this computer; also a tick box on the start page.
        self.act_open_last = act("Open the last project when the program starts",
                                 lambda on: self.set_open_last_at_start(on),
                                 tip="Start with the project you had open last, instead of "
                                     "the start page")
        self.act_open_last.setCheckable(True)
        self.act_open_last.setChecked(self.prefs.open_last_at_start)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.setToolTipsVisible(True)   # (its items explain themselves in tooltips)
        view_menu.addActions([self.act_live, self.act_warnings])
        view_menu.addSeparator()
        # (SPEC §13l.2) A preference of this computer, applied when the program starts.
        self.size_menu = view_menu.addMenu("Interface size")
        self.size_menu.setToolTipsVisible(True)
        self.size_menu.setToolTip(SIZE_TIP)
        sizes = QActionGroup(self)
        sizes.setExclusive(True)
        self.act_sizes: dict[int, QAction] = {}
        for size in restart.SIZES:
            a = act(f"{size}%" + ("  (normal)" if size == restart.DEFAULT_SIZE else ""),
                    lambda _=False, s=size: self.choose_interface_size(s), tip=SIZE_TIP)
            a.setCheckable(True)
            sizes.addAction(a)
            self.size_menu.addAction(a)
            self.act_sizes[size] = a
        self.act_sizes[self.prefs.interface_size].setChecked(True)
        # Sizes at which the window would not fit on this screen are greyed.
        self.size_menu.aboutToShow.connect(self.update_size_choices)
        view_menu.addSeparator()
        view_menu.addAction(self.act_open_last)

        # Updates and feedback (SPEC §13m.3, §13m.4). The start-up check is a preference
        # of this computer, on by default.
        self.update_flow = update_ui.UpdateFlow(self, self.prefs)
        self.act_check_updates_at_start = act(
            "Check for updates when the program starts",
            lambda on: update_ui.set_check_at_start(self.prefs, on),
            tip="Ask GitHub for a newer version each time the program starts (nothing about "
                "you or your projects is sent)")
        self.act_check_updates_at_start.setCheckable(True)
        self.act_check_updates_at_start.setChecked(update_ui.check_at_start(self.prefs))
        view_menu.addAction(self.act_check_updates_at_start)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.setToolTipsVisible(True)
        self.act_user_guide = act("User guide", self.show_user_guide, "F1",
                                  tip="How to use the program, step by step")
        help_menu.addAction(self.act_user_guide)
        self.act_open_example = act("Open example project",
                                    lambda: self.open_example_project(), tip=EXAMPLE_TIP)
        help_menu.addAction(self.act_open_example)
        self.act_example_over = act(
            "Start the example over…", self.start_example_over,
            tip="Replace your copy of the example project with a fresh one")
        help_menu.addAction(self.act_example_over)
        self.act_open_example_pdf = act(
            "View the example SI (PDF)", self.open_example_pdf,
            tip="Open the Supporting Information compiled from the example project, as a "
                "PDF (simulated spectra; not a real SI)")
        help_menu.addAction(self.act_open_example_pdf)
        help_menu.addSeparator()
        self.act_check_updates = act("Check for updates…", lambda: self.update_flow.check(True),
                                     tip="See whether a newer version is available on GitHub")
        self.act_feedback = act("Send feedback…", self.send_feedback,
                                tip="Report a problem or suggest an improvement (opens a "
                                    "GitHub page in your web browser)")
        help_menu.addActions([self.act_check_updates, self.act_feedback])
        help_menu.addSeparator()
        help_menu.addAction("About", lambda: feedback_ui.show_about(self))

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        new_btn = QToolButton()
        new_btn.setText("New section")
        new_btn.setIcon(style.standardIcon(SP.SP_FileDialogNewFolder))
        new_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        new_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        new_menu = QMenu(new_btn)
        new_menu.setToolTipsVisible(True)
        new_menu.addActions(list(self.act_new_section.values()))
        new_menu.addMenu(self.new_sub_menu)
        new_btn.setMenu(new_menu)
        self.new_section_button = new_btn
        toolbar.addWidget(new_btn)
        toolbar.addActions([self.act_new_compound, self.act_new_scheme])
        toolbar.addSeparator()
        toolbar.addActions([self.act_compile, self.act_preview, self.act_live, self.act_warnings])
        toolbar.addSeparator()
        toolbar.addAction(self.act_settings)
        toolbar.addAction(self.act_trash)

    def set_open_last_at_start(self, on: bool) -> None:
        """"Open the last project when the program starts": saved on this computer; the
        View menu and the start page show the same choice."""
        self.prefs.open_last_at_start = on
        for widget in (self.act_open_last, self.welcome.open_last_box):
            widget.blockSignals(True)
            widget.setChecked(bool(on))
            widget.blockSignals(False)

    def open_last_project_at_start(self) -> bool:
        """At start-up: open the last project if that option is on and its folder is still
        there (otherwise the start page stays, with nothing to say)."""
        last = self.prefs.last_project() if self.prefs.open_last_at_start else None
        return last is not None and self.open_project(last)

    # ------------------------------------------------------------------ updates, feedback (§13m)

    def check_for_updates_at_start(self) -> bool:
        """At start-up, a little after the window shows: look for a newer version in the
        background, if that is turned on (never in the start-up test or in tests). Says
        nothing unless a newer version is found. True if a check was scheduled."""
        from .. import updates
        if not (update_ui.check_at_start(self.prefs) and updates.automatic_check_allowed()):
            return False
        QTimer.singleShot(update_ui.START_DELAY_MS, self, lambda: self.update_flow.check(False))
        return True

    def send_feedback(self) -> None:
        feedback_ui.FeedbackDialog(self).exec()

    def show_user_guide(self) -> None:
        """Help ▸ User guide: the guide shipped with the program (one window, kept when it
        is closed and brought back to the front), or its GitHub page if the guide is
        missing."""
        window = getattr(self, "guide_window", None)
        if window is not None:   # (closing it only hid it)
            window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
            window.show()
            window.raise_()
            window.activateWindow()
            return
        self.guide_window = help_ui.open_user_guide(self)

    def error_report_paths(self) -> dict[str, str]:
        """Paths to hide in an error report: the open project's folder."""
        return {str(self.project.folder): "<project folder>"} if self.project is not None else {}

    # ------------------------------------------------------------------ interface size (§13l.2)

    def choose_interface_size(self, percent: int) -> None:
        """View ▸ Interface size: saved on this computer, used from the next start; offers
        to restart now (at the same project and page)."""
        self.prefs.interface_size = percent
        self.prefs.sync()
        for size, action in self.act_sizes.items():
            action.setChecked(size == percent)
        if restart.user_scale:
            QMessageBox.information(
                self, "Interface size",
                f"Saved: {percent}%.\n\nThis computer sets the size itself (the setting "
                f"QT_SCALE_FACTOR = {restart.user_scale}), which is used instead for as long "
                "as it is set.")
            return
        if percent == restart.applied_size:
            self.statusBar().showMessage(f"Interface size: {percent}% (as now)", 4000)
            return
        where = ("Your work is saved first, and the program opens again at this page."
                 if self.project is not None else "")
        answer = QMessageBox.question(
            self, "Interface size",
            f"The interface will be shown at {percent}% from the next time the program "
            f"starts.\n\nRestart now? {where}".rstrip(),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.restart_program()
        else:
            self.statusBar().showMessage(f"Interface size {percent}%: used from the next start",
                                         6000)

    def update_size_choices(self, available=None) -> None:
        """View ▸ Interface size: grey the sizes at which the window would not fit on this
        screen (its free area, scaled to that size, smaller than the window's smallest
        size), saying why. The size in use and the saved one stay available. `available`:
        the screen's free area in logical pixels (default: the window's screen)."""
        if available is None:
            screen = self.screen()
            if screen is None:
                return
            geometry = screen.availableGeometry()
            available = (geometry.width(), geometry.height())
        need = self.minimumSizeHint().expandedTo(self.minimumSize())
        window_min = (need.width(), need.height())
        for size, action in self.act_sizes.items():
            fits = restart.fits_screen(size, available, window_min)
            keep = size in (restart.applied_size, self.prefs.interface_size)
            action.setEnabled(fits or keep)
            tip = SIZE_TIP if fits else (
                f"Too large for this screen: at {size}% the window would not fit on it (parts "
                "of it would be cut off). Choose a smaller size, or use a larger screen.")
            action.setToolTip(tip)
            action.setStatusTip(tip)

    def restore_info(self, save: bool = True) -> dict:
        """Where the user is, for a restart (ui/restart.py): the open project, the selected
        tree item and the items it sits in (nearest first), the page's tab, and the live
        preview and Warnings panes. Pending edits are saved first (unless the project was
        taken over meanwhile: it then reopens read-only); with save=False they are not,
        because close_project follows and saves them (a save that fails is then tried, and
        reported, once)."""
        info: dict = {"preview_collapsed": bool(self.preview_pane.collapsed),
                      "warnings": self.warnings_dock.isVisible()}
        if self.project is None:
            return info
        self._stop_if_lock_lost()
        if save:
            self._flush_panel()
        info["folder"] = str(self.project.folder)
        info["read_only"] = bool(self.project.read_only)
        info["where"] = [[kind, item_id] for kind, item_id in self._where()]
        panel = self.panel
        if isinstance(panel, DataSectionPanel):
            info["page"] = {"tab": panel.tabs.currentIndex()}
        elif isinstance(panel, SchemePanel):
            # (its compound tab is the tree item; whether the scheme is folded away)
            info["page"] = {"scheme_collapsed": panel.collapse_btn.isChecked()}
        return info

    def apply_restore_info(self, info: dict) -> bool:
        """After a restart: reopen the project and select the same item (or the nearest
        one that still exists) and tab. Returns True if the project was opened."""
        if "preview_collapsed" in info:
            self._preview_collapsed(bool(info["preview_collapsed"]), from_action=True)
        folder = info.get("folder")
        if not isinstance(folder, str) or not folder or not Path(folder).is_dir():
            return False
        if not self.open_project(Path(folder),
                                 lock_choice="read_only" if info.get("read_only") else None):
            return False
        where = info.get("where")
        for place in where if isinstance(where, list) else []:   # (anything else: ignored)
            if not (isinstance(place, (list, tuple)) and len(place) == 2):
                continue
            item = self._find_item(str(place[0]), str(place[1]))
            if item is not None:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(item)
                break
        page = info.get("page") if isinstance(info.get("page"), dict) else {}
        panel = self.panel
        if isinstance(panel, DataSectionPanel) and isinstance(page.get("tab"), int) \
                and 0 <= page["tab"] < panel.tabs.count():
            panel.tabs.setCurrentIndex(page["tab"])
        elif isinstance(panel, SchemePanel) and isinstance(page.get("scheme_collapsed"), bool):
            panel.set_collapsed(page["scheme_collapsed"])
        if info.get("warnings"):
            self.warnings_dock.setVisible(True)
        return True

    def restart_program(self) -> bool:
        """Close the project cleanly (edits saved, lock released), start a new copy of the
        program that reopens it where the user was, and quit. If the new copy cannot be
        started, the project is reopened here and the user is told."""
        info = self.restore_info(save=False)   # (close_project saves the page)
        if not self.close_project():
            return False
        self.prefs.set_value("window_geometry", self.saveGeometry())
        self.prefs.sync()
        args, cwd = restart.restart_command(info)
        try:
            restart.launcher(args, restart.child_environment(), cwd)
        except OSError as e:
            QMessageBox.warning(self, "Could not restart",
                                f"The program could not be started again ({e}).\n\nClose it "
                                "and start it yourself; the new interface size is used then.")
            self.apply_restore_info(info)
            return False
        self.close()   # (the last window: the program quits)
        return True

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.prefs.recent:
            self.recent_menu.addAction(menu_text(path), lambda p=path: self.open_project(Path(p)))
        if not self.prefs.recent:
            self.recent_menu.addAction("(none)").setEnabled(False)

    def _update_ui_state(self) -> None:
        has_project = self.project is not None
        writable = has_project and not self.project.read_only
        kind, _id = self._current()
        for a in (self.act_close_project, self.act_trash, self.act_show_folder, self.act_settings,
                  self.act_warnings, self.act_references, self.act_live):
            a.setEnabled(has_project)
        if not has_project and self.warnings_dock.isVisible():
            self.warnings_dock.hide()
        self.act_snapshot.setEnabled(writable)
        self.act_compile.setEnabled(writable)  # compiling writes to the project's build/ folder
        self.act_preview.setEnabled(writable and kind not in (None, "front_root", PROJECT_ITEM,
                                                              *BOTTOM_KINDS))
        for a in self.act_new_section.values():
            a.setEnabled(writable)
        self.new_section_button.setEnabled(writable)
        sub_ok = writable and self._sub_section_parent() is not None
        for a in self.act_new_sub.values():
            a.setEnabled(sub_ok)
        self.new_sub_menu.setEnabled(sub_ok)
        self.act_new_compound.setEnabled(writable and self._target_data_section() is not None)
        self.act_new_scheme.setEnabled(writable and self._target_data_section() is not None)
        # The project item and the front matter are fixed: they cannot be renamed, deleted
        # or moved; nor can the compounds' spectra pages (they follow the compounds).
        movable = writable and kind not in (None, *FIXED_KINDS, *SPECTRA_CHILDREN)
        for a in (self.act_rename, self.act_delete, self.act_up, self.act_down):
            a.setEnabled(movable)
        # The Spectra section can be moved and renamed, but never deleted (SPEC §13k.12).
        spectra = kind == "section" and self._is_spectra(_id)
        if spectra:
            self.act_delete.setEnabled(False)
        tip = SPECTRA_NOT_DELETED if spectra else DELETE_TIP
        self.act_delete.setToolTip(tip)
        self.act_delete.setStatusTip(tip)
        self.act_use_as_general.setEnabled(
            writable and kind == "section" and self._is_custom(_id))
        self.tree.setDragEnabled(writable)
        self._update_undo_actions()

        if not has_project:
            self.setWindowTitle(APP_NAME)
            self.ro_label.setText("")
            self.welcome.refresh()
            self.stack.setCurrentWidget(self.welcome)
            return
        title = str(self.project.settings.get("title") or self.project.folder.name)
        item = self._find_item(PROJECT_ITEM, PROJECT_ITEM)
        if item is not None and item.text(0) != title:
            item.setText(0, title)   # the title is typed on the Cover page
        suffix = "  [read-only]" if self.project.read_only else ""
        self.setWindowTitle(f"{title} — {APP_NAME}{suffix}")
        self.ro_label.setText("READ-ONLY" if self.project.read_only else "")
        self.ro_label.setStyleSheet("color: #b00; font-weight: bold;" if self.project.read_only else "")
        self.stack.setCurrentWidget(self.splitter)

    # ------------------------------------------------------------------ user name

    def ensure_user_name(self) -> str:
        if not self.prefs.user_name:
            try:
                default = getpass.getuser()
            except Exception:  # noqa: BLE001 — any failure just means no suggestion
                default = ""
            name, ok = QInputDialog.getText(
                self, "Your name",
                "What name should be shown to others when you have a project open?\n"
                "(Stored on this computer. You can change it in Project Settings.)",
                text=default)
            self.prefs.user_name = (name.strip() if ok and name.strip() else default) or "Unknown"
        return self.prefs.user_name

    # ------------------------------------------------------------------ opening/closing

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New Project",
                                        "Project name. It becomes the folder name, and the\n"
                                        "manuscript title printed on the cover page until you\n"
                                        "change it (Front matter ▸ Cover page):")
        if not ok or not name.strip():
            return
        parent = QFileDialog.getExistingDirectory(
            self, f"Choose where to create the “{name.strip()}” folder")
        if not parent:
            return
        if not self.close_project():
            return
        try:
            project = Project.create(Path(parent), name)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not create project", str(e))
            return
        self.open_project(project.folder)

    def open_example_project(self, fresh: bool = False) -> None:
        """Help ▸ Open example project (and the start page's button): open the user's copy
        of the example project, kept in this computer's folder for the program's own files
        (resources.example_copy_folder), without asking where. The first time, or with
        `fresh` (Help ▸ Start the example over), the copy is made from the example shipped
        with the program; after an update that brings a different example, the user is
        asked whether to replace their copy. The shipped example itself is never opened or
        changed, so this also works when the program's own folder is read-only."""
        source = resources.example_project()
        if not (source / PROJECT_FILE).is_file():
            QMessageBox.warning(self, "Example project not found",
                                "The example project that comes with the program is missing:\n"
                                f"{source}\n\nInstalling the program again brings it back.")
            return
        folder = resources.example_copy_folder()
        try:
            state = resources.example_copy_state(source)
        except OSError:
            state = "outdated"
        is_open = self.project is not None and _same_folder(self.project.folder, folder)
        if not fresh and state == "outdated":
            answer = QMessageBox.question(
                self, "Updated example project",
                "This version of Chem SI Assistant comes with an updated example project.\n\n"
                "Replace your copy of the example with the new one? Any changes you made "
                "to your copy are lost.\n\n(If you keep your copy, Help ▸ Start the example "
                "over gives you the new example later.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            fresh = answer == QMessageBox.StandardButton.Yes
            if not fresh:
                try:
                    resources.mark_example_copy_current(source)   # (not asked again)
                except OSError:
                    pass
        if not fresh and state != "missing":
            if is_open:
                self.statusBar().showMessage("The example project is already open.", 10_000)
            elif self.close_project():
                self.open_project(folder)
            return
        # The new copy first, beside the old one: if it fails, nothing changes.
        try:
            new = resources.prepare_example_copy(source)
        except OSError as e:
            QMessageBox.warning(self, "Could not copy the example project",
                                f"The example project could not be copied into\n"
                                f"{folder.parent}\n\n{e}")
            return
        if not self.close_project():
            resources._remove_tree(new)
            return
        try:
            folder = resources.install_example_copy(new, source)
        except OSError as e:
            resources._remove_tree(new)
            QMessageBox.warning(self, "Could not replace the example project",
                                f"Your copy of the example project could not be replaced:\n"
                                f"{folder}\n\n{e}\n\nClose any program that has one of its "
                                "files open, then try again.")
            return
        if self.open_project(folder):
            self.statusBar().showMessage(
                "This is your own copy of the example project: change anything you like. "
                "Help ▸ Start the example over gives you a fresh copy.", 30_000)

    def start_example_over(self) -> None:
        """Help ▸ Start the example over: replace the user's copy of the example project by
        a fresh copy of the one shipped with the program (after asking)."""
        if resources.example_copy_folder().is_dir():
            answer = QMessageBox.question(
                self, "Start the example over",
                "Replace your copy of the example project with a fresh one? Any changes "
                "you made to it are lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.open_example_project(fresh=True)

    def open_example_pdf(self) -> None:
        """Help ▸ View the example SI (PDF): the SI compiled from the example project,
        in the computer's PDF viewer (only read, never changed)."""
        pdf = resources.example_pdf()
        if not pdf.is_file():
            QMessageBox.warning(self, "Example SI not found",
                                "The example SI that comes with the program is missing:\n"
                                f"{pdf}\n\nInstalling the program again brings it back.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf))):
            QMessageBox.warning(self, "Could not open the example SI",
                                f"No program to show PDF files was found. The file is:\n{pdf}")

    def choose_and_open_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open a Project Folder")
        if folder:
            self.open_project(Path(folder))

    def open_project(self, folder: Path, lock_choice: str | None = None) -> bool:
        """Open a Project Folder. `lock_choice` answers the question asked when someone
        else has it open ("read_only"), e.g. when a restart reopens a project that was
        open read-only."""
        if not folder.exists():
            QMessageBox.warning(self, "Project not found", f"This folder no longer exists:\n{folder}")
            self.prefs.remove_recent(str(folder))
            self.welcome.refresh()
            return False
        if not self.close_project():
            return False
        try:
            project = Project.open(folder)
        except (ProjectError, OSError, ValueError) as e:
            QMessageBox.warning(self, "Could not open project", str(e))
            return False

        if project.newer_format:
            # Saved by a newer version of the program (SPEC §13m.3): editing it here could
            # lose what this version doesn't know about.
            if update_ui.ask_about_newer_format(self, project) == "cancel":
                return False
            project.read_only = True

        user = self.ensure_user_name()
        existing = None if project.read_only else project.current_lock()
        if existing is not None:
            same = existing.user == user and existing.computer == computer_name()
            choice = lock_choice or ask_about_lock(self, existing, same)
            if choice == "cancel":
                return False
            if choice == "read_only":
                project.read_only = True
        if not project.read_only:
            try:
                project.acquire_lock(user)
            except OSError as e:
                QMessageBox.warning(self, "Opening read-only",
                                    f"Could not write the lock file, so the project is opened "
                                    f"read-only.\n\n{e}")
                project.read_only = True
        try:
            self._finish_opening(folder, project)
        except BaseException:
            # Something unexpected went wrong half-way: let go of the lock and leave no
            # half-opened project in the window, then report the error as usual.
            if self.project is project:
                try:
                    self.close_project()
                except Exception:
                    self._lock_timer.stop()
                    project.read_only = True   # (nothing more is written from here)
            try:
                project.close()   # (does nothing when close_project already did it)
            except OSError:
                pass
            raise
        if project.load_warnings:
            QMessageBox.warning(self, "Project opened with warnings",
                                "\n".join(project.load_warnings))
        long_path = path_length_warning(project.folder)
        if long_path and not self.prefs.value(f"path_warned/{project.folder}"):
            self.prefs.set_value(f"path_warned/{project.folder}", True)  # say it once
            QMessageBox.information(self, "Long folder path", long_path)
        return True

    def _finish_opening(self, folder: Path, project: Project) -> None:
        """open_project once the lock is taken: the automatic upgrades, then the window
        shows the project (its tree, pages and panels)."""
        if not project.read_only:
            # Every project has a Spectra section (SPEC §13k.12): a new project gets it now,
            # an older one at the end of its tree, with its spectra settings carried over.
            try:
                project.ensure_spectra_section()
            except (ProjectError, OSError) as e:
                QMessageBox.warning(self, "Could not add the Spectra section",
                                    f"The Spectra section could not be added, so the spectra "
                                    f"are printed at the end of the SI as before.\n\n{e}")
            # Pictures in texts from before figures existed become figures (labels and
            # mentions), here and not when their page is shown (see panels.text_with_objects).
            try:
                project.upgrade_all_pictures()
            except (ProjectError, OSError, KeyError) as e:
                QMessageBox.warning(self, "Could not update the pictures",
                                    "Some older pictures in the texts could not be turned into "
                                    f"figures. They still show and print as before.\n\n{e}")

        self.project = project
        if not project.read_only:
            # (from now on: a takeover is noticed even if something below goes wrong)
            self._lock_timer.start()
            # From here on every saved change can be undone (SPEC §13k.11); the automatic
            # changes made while opening (the Spectra section) are not part of it.
            journal = project.enable_undo()
            journal.context = self._undo_context
            # A click, paste or drop is one step: it is over once its event is handled.
            journal.end_action = lambda done: QTimer.singleShot(0, self, done)
            journal.listeners.append(self._update_undo_actions)
        self.preview_pane.set_project(project)
        rich_text.set_link_context(rich_text.LinkContext(project))
        rich_text.manage_references = self.show_references
        text_checks.set_project(project)   # its dictionary.txt (SPEC §13k.1a)
        self._collapsed = set()
        self.warnings_panel.set_project(project if self.warnings_dock.isVisible() else None)
        self.prefs.add_recent(folder)
        self._populate_tree()
        self._update_ui_state()

    def close_project(self) -> bool:
        """Save pending edits and release the lock. Returns False if cancelled."""
        if self.project is None:
            return True
        self._stop_if_lock_lost()   # (taken over since the last check: nothing is saved)
        if not self._flush_or_stay("Close the project", closing=True):
            return False
        self._lock_timer.stop()
        self._set_panel(None)
        self.preview_pane.set_project(None)   # waits for a running preview compile
        if self._preview is not None:
            # Let go of build/preview.pdf (Windows keeps the file, and so the project
            # folder, locked while it is shown), and don't keep showing the old page.
            self._preview.release()
            self._preview.hide()
        try:
            self.project.close()
        except OSError:
            pass  # a stale lock can be cleared next time; never block closing
        self.project = None
        rich_text.set_link_context(None)
        rich_text.manage_references = None
        text_checks.set_project(None)
        self.warnings_panel.set_project(None)
        self._updating_tree = True
        self.tree.clear()
        self._updating_tree = False
        self._update_ui_state()
        return True

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self.close_project():
            event.ignore()   # (kept open: the page's changes could not be saved)
            return
        self.preview_pane.shutdown()
        # (The start-up test mode must leave this computer's settings as they are.)
        if not os.environ.get("SI_ASSISTANT_SMOKE_TEST"):
            self.prefs.set_value("window_geometry", self.saveGeometry())
        event.accept()

    def _check_lock(self) -> None:
        """Notice if someone else took over the project, or it was closed or moved
        elsewhere, while we had it open: the window then becomes read-only. A lock that
        disappeared is not taken back: the files may have changed since this window read
        them (e.g. someone took over on another computer, edited and closed), and its next
        save would put its old copy back over their work."""
        lost = self._lock_lost()
        if lost is None:
            return
        p = self.project
        pending = self._stop_writing()
        self._show_lock_lost(p, lost, pending)

    def _lock_lost(self) -> tuple[str, object] | None:
        """Why this window may no longer write to its project, or None while its lock is
        still there: ("taken", their lock) when someone else took it over; ("moved", None)
        when the project folder is no longer where it was opened (renamed, moved or
        deleted); ("gone", None) when our lock disappeared (the project was closed on
        another computer). None also when the folder can't be read just now: it is asked
        again next time. That includes a drive or share that is not there at the moment
        (a USB stick unplugged, a network or mapped drive dropped): its files look missing
        too, but the folder around the project is then missing as well. (Saves meanwhile
        fail with a message; nothing is written into a re-created folder.)"""
        p = self.project
        if p is None or p.read_only:
            return None
        try:
            current = p.current_lock()
            if current is not None:
                return None if p.lock_still_ours() else ("taken", current)
            if not p.folder.parent.is_dir():
                return None   # (the drive is not there just now, or can't be read)
            if not (p.folder / PROJECT_FILE).is_file():
                return "moved", None
        except OSError:
            return None
        return "gone", None

    def _stop_writing(self) -> bool:
        """The lock was lost: from now on nothing is written (pending autosaves check
        read_only), and build/undo/ (now someone else's history, or no one's) is left
        alone. Returns whether typing on the page was not saved yet (it never will be).
        The menus and buttons that change the project are greyed at once (also while a
        menu or dialog is open; the page itself is rebuilt only after it closes)."""
        p = self.project
        pending = self._panel_has_unsaved_typing()
        p.read_only = True
        p.stop_undo()
        p.lock = None
        self._lock_timer.stop()
        self._update_ui_state()   # (also the undo actions and the "[read-only]" title)
        return pending

    def _panel_has_unsaved_typing(self) -> bool:
        """Typing on the page not saved yet: an autosave still waiting, edits whose save
        failed (kept waiting to be tried again: e.g. the folder was just moved, or a file
        was held by OneDrive), or the box being typed in edited and not yet left (some
        boxes save only then)."""
        panel = self.panel
        if panel is None:
            return False
        if any(t.isActive() and t.isSingleShot() for t in panel.findChildren(QTimer)):
            return True
        if self._panel_waits_to_save(panel):
            return True
        focus = QApplication.focusWidget()
        return (isinstance(focus, QLineEdit) and panel.isAncestorOf(focus)
                and focus.isModified() and not focus.isReadOnly())

    @staticmethod
    def _panel_waits_to_save(panel: QWidget) -> bool:
        """The page (or a part of it) has edits whose save is still waiting: not tried yet,
        or tried and failed (kept to be tried again). A compound page, also on a scheme
        page's tab, and the general procedures say so with has_unsaved(); the spectra, text
        and front-matter pages mark it with _unsaved."""
        def waits(w: QWidget) -> bool:
            has_unsaved = getattr(w, "has_unsaved", None)
            if callable(has_unsaved):
                return bool(has_unsaved())
            return getattr(w, "_dirty", False) is True or getattr(w, "_unsaved", False) is True
        return any(waits(w) for w in [panel, *panel.findChildren(QWidget)])

    def _panel_save_failed(self) -> bool:
        """After _flush_panel: the page's latest changes could still not be saved (the page
        has already said so). Not in read-only mode: nothing was tried then."""
        p, panel = self.project, self.panel
        return (panel is not None and p is not None and not p.read_only
                and self._panel_waits_to_save(panel))

    def _flush_or_stay(self, doing: str = "Leave the page", closing: bool = False) -> bool:
        """Before the page on show is thrown away (another page shown, the tree rebuilt,
        the project closed): save its changes. True to go on; False to stay on the page
        (the caller then leaves it, and the tree, as they are)."""
        self._flush_panel()
        return self._leave_unsaved_page(doing, closing)

    def _leave_unsaved_page(self, doing: str = "Leave the page", closing: bool = False) -> bool:
        """After _flush_panel, before the page is thrown away: True to go on. When its
        latest changes could not be saved, asks first, so the page can be kept open to
        copy them or to close the program holding the file, and then try again (warnings
        never block: going on is always possible). Going on drops the page at once, and
        (unless the project is being closed) reads the project back from its files: the
        page had already copied those changes into the project in memory (e.g. a compound
        page's values), where they would show again as if saved, and be written by the
        next save of that item."""
        if not self._panel_save_failed():
            return True
        answer = QMessageBox.question(
            self, "Changes not saved",
            "The latest changes on this page could not be saved (see the message before "
            f"this one).\n\n{doing} anyway? Those changes will then be lost.\n\nChoose No to "
            "stay on the page: you can copy them, or close the program that has the file "
            "open, and then try again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        return True if closing else self._drop_unsaved_page()

    def _drop_unsaved_page(self) -> bool:
        """Throw away the page whose changes could not be saved, and what it had copied
        into the project in memory: the project is read back from its files (the tree's
        labels follow; its items keep their ids). True when that worked. If the files could
        not all be read back, what is in memory no longer matches them, so the project is
        made read-only (never saved over the files) and shown again; False then."""
        panel = self.panel
        for timer in panel.findChildren(QTimer):
            timer.stop()   # (nothing more is tried on it)
        self._set_panel(None)
        for timer in panel.findChildren(QTimer):
            timer.stop()   # (also one started as its box lost the focus)
        return self._read_back("The changes on the page were not kept")

    def _read_back(self, what: str) -> bool:
        """Read the project back from its files, so what is in memory matches them (after
        changes that could not be saved: `what` says so). The page must be gone already
        (_set_panel(None)); the tree's labels follow (its items keep their ids). True when
        that worked; notices that only inform (e.g. a OneDrive conflict copy that appeared
        meanwhile) are shown, nothing more. If a file could not be read back, what is in
        memory no longer matches the files, so the project is made read-only (never saved
        over the files) and shown again; False then."""
        p = self.project
        before, notes_before = set(p.read_failures), set(p.load_warnings)
        try:
            p.reload()
            problem = next((w for w in p.read_failures if w not in before), None)
        except (ProjectError, OSError, ValueError) as e:
            problem = str(e)
        if problem is None:
            updating, self._updating_tree = self._updating_tree, True
            try:
                self._refresh_labels()   # (and the spectra pages: e.g. a spectrum not saved)
            finally:
                self._updating_tree = updating
            notes = [w for w in p.load_warnings if w not in notes_before]
            if notes:
                QMessageBox.information(self, "Project read back from its files",
                                        "\n\n".join(notes))
            return True
        p.read_only = True
        QMessageBox.warning(
            self, "Project now read-only",
            f"{what}, but the project could not be read back from its files completely, so "
            "it is now read-only (saving now could write those changes after all, or leave "
            "out what could not be read). Close the project and open it again.\n\n"
            f"{problem}")
        self._update_ui_state()
        QTimer.singleShot(0, self, lambda: self.project is p and self._populate_tree(
            self._current()))
        return False

    def _scheme_change_failed(self) -> None:
        """A scheme editor change could not be saved (a file held, e.g. by OneDrive), maybe
        part-way (e.g. a new compound's folder written, scheme.json not): once the editor
        is done, the project is read back from its files and the page shown again from
        them, so nothing shows as done that is not saved (nor is written unseen later)."""
        panel = self.panel

        def read_back() -> None:
            if self.panel is not panel or self.project is None or self.project.read_only:
                return
            where = self._current()
            for timer in panel.findChildren(QTimer):
                timer.stop()
            self._set_panel(None)
            if self._read_back("The change to the scheme was not kept"):
                self._populate_tree(where)
        QTimer.singleShot(0, self, read_back)

    def _keep_page_selected(self, previous) -> None:
        """Staying on the page (its changes could not be saved) after another tree item was
        picked: select the page's item again, at once (so commands act on it) and again
        once the click or key press that picked the other item is over (the tree selects
        that item only after telling us, which would leave it highlighted)."""
        panel = self.panel
        if previous is not None:
            place = (previous.data(0, KIND_ROLE), previous.data(0, ID_ROLE))
        elif self._panel_where:
            place = self._panel_where[0]
        else:
            return

        def reselect() -> None:
            if self.panel is not panel or self.project is None:
                return
            item = self._find_item(*place)
            if item is None:
                return
            self._updating_tree = True
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self._updating_tree = False
            self._update_ui_state()
        reselect()
        QTimer.singleShot(0, self, reselect)

    @staticmethod
    def _lock_lost_text(p: Project, lost) -> tuple[str, str]:
        """(title, what happened) for a lost lock (see _lock_lost)."""
        kind, current = lost
        if kind == "taken":
            return ("Project taken over",
                    f"{current.describe()}.\n\nThey have taken over editing, so this window "
                    "is now read-only.")
        if kind == "moved":
            return ("Project folder moved",
                    f"The project folder is no longer at:\n{p.folder}\n\nIt was renamed, "
                    "moved or deleted (e.g. in Explorer or Finder, or by OneDrive), so this "
                    "window is now read-only. Open the project from its new place (File ▸ "
                    "Open Project).")
        return ("Project closed elsewhere",
                "This project was closed on another computer while it was open here (its "
                "lock file disappeared). Someone may have changed it there, so this window "
                "is now read-only: saving here could undo their changes.\n\nOpen the "
                "project again (File ▸ Open Recent) to go on from the latest files.")

    def _show_lock_lost(self, p: Project, lost, pending: bool) -> None:
        """Show the page read-only and say why the lock was lost, but never while a dialog,
        menu or name lookup is open (a nested event loop): rebuilding the page then would
        delete it, and any dialog on it, while still in use. Waits until they are closed."""
        if self.project is not p:   # closed, or another project opened, meanwhile
            return
        if (QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None
                or QThread.currentThread().loopLevel() > 1):
            QTimer.singleShot(300, self, lambda: self._show_lock_lost(p, lost, pending))
            return
        if pending and self.panel is not None:
            # Keep the page with the typing on it (read-only), so it can still be copied,
            # instead of building it again from the files.
            self._populate_tree(select=self._current(), rebuild_panel=False)
            self._make_page_read_only(self.panel)
        else:
            self._set_panel(None)   # (a scheme page would otherwise be kept, still editable)
            self._populate_tree(select=self._current())
        self._update_ui_state()
        title, text = self._lock_lost_text(p, lost)
        if pending:
            text += ("\n\nYour latest typing on this page may not have been saved (saving it "
                     "now could overwrite newer files). It is still shown, so you can copy "
                     "it; it goes when you leave this page.")
        elif lost[0] == "taken":
            text += " Your earlier changes were already saved."
        if lost[0] == "moved" and p.folder.exists():
            text += ("\n\nChanges saved in the last moments may have gone into a new folder "
                     "at the old place: copy anything you need from there, then delete it.")
        QMessageBox.warning(self, title, text)

    @staticmethod
    def _make_page_read_only(panel: QWidget) -> None:
        """A page kept after the lock was lost (for its typing to be copied): nothing on it
        can be changed any more. Besides the text boxes, lists and buttons (make_read_only):
        number boxes and sliders (e.g. the spectra page's settings), and the page's own
        read-only switch where it has one (the spectra page: also its drawing, where
        structures are dragged)."""
        make_read_only(panel)
        for w in [panel, *panel.findChildren(QWidget)]:
            if isinstance(w, QAbstractSpinBox):
                w.setReadOnly(True)
            elif isinstance(w, QAbstractSlider):
                w.setEnabled(False)
            if getattr(w, "read_only", None) is False:
                w.read_only = True
            if getattr(w, "editable", None) is True:
                w.editable = False

    def _stop_if_lock_lost(self) -> None:
        """Before the page is saved on the way out (closing, restarting, opening another
        project): if the lock was lost since the last check, nothing is written, and the
        user is told if that drops typing not saved yet."""
        lost = self._lock_lost()
        if lost is None:
            return
        if self._stop_writing():
            title, text = self._lock_lost_text(self.project, lost)
            QMessageBox.warning(self, title, text + "\n\nSo your latest typing on this page "
                                "was not saved (that could overwrite newer files).")

    # ------------------------------------------------------------------ tree

    def _populate_tree(self, select: tuple[str | None, str | None] | None = None,
                       rebuild_panel: bool = True) -> None:
        """Rebuild the tree from the project and select `select` = (kind, id).
        With rebuild_panel=False the page on the right is left as it is."""
        self._updating_tree = True
        self.tree.clear()
        style = self.style()
        icons = {"text": QStyle.StandardPixmap.SP_FileIcon,
                 "file": QStyle.StandardPixmap.SP_FileLinkIcon,
                 "data": QStyle.StandardPixmap.SP_DirIcon}
        scheme_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        writable = not self.project.read_only
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if writable:
            flags |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        to_select = None

        def make(parent, kind, item_id, text="", item_flags=None):
            nonlocal to_select
            item = QTreeWidgetItem([text])
            item.setData(0, ID_ROLE, item_id)
            item.setData(0, KIND_ROLE, kind)
            item.setFlags(flags if item_flags is None else item_flags)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            if select == (kind, item_id):
                to_select = item
            return item

        # The project itself (its dashboard), then the front matter: fixed at the top,
        # never dragged, nothing dropped into them.
        fixed = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        top = make(None, PROJECT_ITEM, PROJECT_ITEM,
                   str(self.project.settings.get("title") or self.project.folder.name), fixed)
        top.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon))
        top.setToolTip(0, "The whole project: progress, missing data, warnings and every "
                          "compound at a glance")
        font = top.font(0)
        font.setBold(True)
        top.setFont(0, font)
        fm = make(None, "front_root", front.FRONT_ROOT, "Front matter", fixed)
        fm.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        fm.setToolTip(0, "Cover page, table of contents and General Information (always first)")
        for key, name in front.FRONT_PAGES.items():
            page = make(fm, "front", key, name, fixed)
            page.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        fm.setExpanded(front.FRONT_ROOT not in self._collapsed)

        def add_section(parent, s, depth):
            sec = make(parent, "section", s.id, s.name)
            sec.setData(0, TYPE_ROLE, s.type)
            # The full name first: long names of nested sections are cut off in the tree.
            sec.setToolTip(0, f"{s.name}\n{s.type_label} section"
                           + (f", level {depth}" if depth > 1 else "")
                           + ("\nEvery compound's spectra are printed here; it can be moved "
                              "but not deleted." if s.type == SPECTRA_SECTION else ""))
            sec.setIcon(0, spectrum_icon() if s.type == SPECTRA_SECTION
                        else style.standardIcon(icons.get(s.type, icons["text"])))
            for child in s.children:
                add_section(sec, child, depth + 1)
            if s.type == SPECTRA_SECTION:
                # One page per compound with spectra, in compound order (labelled by
                # _refresh_labels; kept up to date by _sync_spectra_pages).
                for c in spectra_mod.compounds_with_spectra(self.project):
                    make(sec, "spectra_page", c.id, "", fixed).setIcon(0, spectrum_icon())
                if hrms_ui.any_reports(self.project):
                    self._hrms_tree_item(make(sec, HRMS_ITEM, HRMS_ITEM, "HRMS reports",
                                              fixed))
            for item in s.items:
                if isinstance(item, SchemePage):
                    k = make(sec, "scheme", item.id, item.name)
                    k.setIcon(0, scheme_icon)
                    k.setToolTip(0, "Scheme page")
                    for c in item.children:
                        make(k, "compound", c.id)
                    k.setExpanded(item.id not in self._collapsed)
                else:
                    make(sec, "compound", item.id)
            sec.setExpanded(s.id not in self._collapsed)

        for s in self.project.top_sections:
            add_section(None, s, 1)
        # The Manuscript check (SPEC §13o) and the reference list (SPEC §13n.10): always
        # last, fixed like the front matter.
        ms_item = make(None, MANUSCRIPT_ITEM, MANUSCRIPT_ITEM, "Manuscript check", fixed)
        ms_item.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        ms_item.setToolTip(0, "Compare the manuscript (PDF or Word, and its ChemDraw files) "
                              "with the SI: IDs, structures, yields, ee/er, dr, names and "
                              "footnoted conditions. Never printed.")
        refs_item = make(None, REFERENCES_ITEM, REFERENCES_ITEM, "References", fixed)
        refs_item.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView))
        refs_item.setToolTip(0, "The project's reference list: add by DOI, by hand or from "
                                "BibTeX/RIS. Only cited references are printed, at the end "
                                "of the SI.")
        self._refresh_labels()
        if not rebuild_panel:
            if to_select is not None:
                self.tree.setCurrentItem(to_select)
            self._updating_tree = False
            self._update_ui_state()
            return
        self._updating_tree = False
        if to_select is None and self.tree.topLevelItemCount():
            # The first section; the front matter in a new project (which has only the
            # Spectra section).
            items = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
            to_select = next((i for i in items if i.data(0, KIND_ROLE) == "section"
                              and i.data(0, TYPE_ROLE) != SPECTRA_SECTION),
                             fm)
        if to_select is not None:
            # clear() above reset the current item, so this rebuilds the panel, except for
            # a compound of the scheme page on show (only its tab is chosen again, see
            # _on_tree_selection): after changing its data, call _set_panel(None) first.
            self.tree.setCurrentItem(to_select)
        else:
            self._set_panel(None)
        self._update_ui_state()

    def _all_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(j) for j in range(item.childCount()))

    def _warnings_visibility(self, visible: bool) -> None:
        self.act_warnings.setChecked(visible)
        if visible:
            self.warnings_panel.set_project(self.project)

    def _after_warnings_changed(self) -> None:
        """A warning was dismissed/restored in the panel (or the project settings changed):
        update the page and tree. A page whose latest changes could not be saved is kept as
        it is (only the tree is updated): rebuilding it would throw them away, and saving is
        tried again with the next edit or when the page is left."""
        self._refresh_labels()
        kind, item_id = self._current()
        if kind is not None:
            self._flush_panel()
            if self._panel_save_failed():
                self.statusBar().showMessage("The page was not updated: its latest changes "
                                             "could not be saved yet.", 6000)
                return
            self._show(kind, item_id)

    def _manuscript_changed(self) -> None:
        """The Manuscript check read files or a result was dismissed: the Warnings panel's
        "Manuscript" group follows."""
        if self.warnings_dock.isVisible():
            self._warnings_timer.start()

    def _labels(self):
        """The figure, table and scheme labels (core/labels.py), worked out afresh."""
        ctx = rich_text.link_context()
        if ctx is not None and ctx.project is self.project:
            ctx.invalidate()   # (they may have changed; shared with the text boxes)
            return ctx.labels()
        from ..core.labels import compute
        return compute(self.project)

    def _warnings_guard(self, labels) -> tuple:
        """What a compound's warnings depend on besides its own data and its ID-report
        messages: what texts can mention (figure, table and scheme labels; compounds), the
        references, the general procedures and the project settings."""
        p = self.project
        gps = [(gp.get("id"), gp.get("name"), gp.get("sentence"))
               for _s, gp in p.all_general_procedures()]
        return (frozenset(labels.by_id), frozenset(c.id for c in p.all_compounds()),
                tuple(r.get("id") for r in p.references),
                json.dumps([gps, p.settings], sort_keys=True, default=str))

    def _warnings_files(self, c, read: bool = False) -> tuple | None:
        """(modified time, size) of the files a compound's warnings read: its structure.mol,
        its HRMS report files and its text fields' .txt files (procedure, notes…: read
        from disk by each check; None for one that is not there). With `read`, the
        structure and reports are also read (each is remembered until it changes, so
        checking it then reads it no more): None when one can't be read just now (offline,
        or open in another program), as the warnings worked out from it would then be
        wrong."""
        from ..core import ms_reports
        p = self.project
        reports = [ms_reports.report_path(c, i["file"]) for i in ms_reports.report_items(c)]
        texts = [p.compound_text_path(c, d.key) for d in p.field_defs(c) if d.kind == "richtext"]
        paths = [p.structure_path(c), *reports, *texts]
        try:
            if read:
                p.read_structure(c, strict=True)
                if any(ms_reports.read_report(path).transient for path in reports):
                    return None
            stamps = []
            for path in paths:
                try:
                    st = path.stat()
                except FileNotFoundError:
                    stamps.append(None)
                    continue
                stamps.append((st.st_mtime_ns, st.st_size))
        except (OSError, ValueError):   # (ValueError: a name with a NUL character)
            return None
        return tuple(stamps)

    def _refresh_labels(self, changed_id: str | None = None) -> None:
        """Update compound and scheme labels (ID, name, warnings, flags) without rebuilding,
        and the Warnings panel if it is open (from the same results).

        `changed_id`: only that compound's page was saved (a compound page's autosave).
        The other compounds' warnings are then taken from the last pass when nothing
        else they depend on changed: their ID-report messages, _warnings_guard() and the
        files they were read from (_warnings_files: e.g. changed by a OneDrive sync) are
        the same. A result worked out while one of those files could not be read is not
        kept. Every other call, and every other page shown (_set_panel), checks
        everything again, so changes made elsewhere (other pages, undo, Trash, reload)
        are always picked up."""
        if self.project is None:
            return
        ctx = rich_text.link_context()
        if ctx is not None:
            ctx.invalidate()   # labels (Figure S2…) may have changed too
        self._sync_spectra_pages()
        report = self.project.id_report()
        labels = self._labels()
        guard = self._warnings_guard(labels)
        partial = changed_id is not None and guard == self._warn_guard
        if not partial:
            self._warn_cache = {}
        self._warn_guard = guard
        computed = {}   # compound id: checks.all_warnings (for the Warnings panel too)
        warn_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        from ..core import checks  # warnings shown in the tree are the active (undismissed) ones
        for item in self._all_items():
            kind, item_id = item.data(0, KIND_ROLE), item.data(0, ID_ROLE)
            if kind == "spectra_page":
                try:
                    c = self.project.compound(item_id)
                except KeyError:
                    continue
                shown = report.display_id(item_id)
                item.setText(0, f"{shown} — {c.name or '(unnamed)'}")
                item.setToolTip(0, f"The spectra of {shown}: drag their structures, change "
                                   "single spectra")
                continue
            if kind == "scheme":
                try:
                    item.setText(0, self.project.scheme(item_id).name)
                except KeyError:
                    pass
                continue
            if kind != "compound":
                continue
            try:
                c = self.project.compound(item_id)
            except KeyError:
                continue
            id_msgs = tuple(report.compound_warnings.get(item_id, []))
            hit = self._warn_cache.get(item_id)
            files = self._warnings_files(c) if partial and item_id != changed_id else None
            if files is not None and hit is not None and hit[0] == (id_msgs, files):
                every = hit[1]
            else:
                before = self._warnings_files(c, read=True)
                every = checks.all_warnings(self.project, c, report, labels)
                if before is not None and before == self._warnings_files(c):
                    self._warn_cache[item_id] = ((id_msgs, before), every)
                else:   # (a file could not be read just now, or changed meanwhile)
                    self._warn_cache.pop(item_id, None)
            computed[item_id] = every
            problems = [w.message for w in every if not checks.is_dismissed(c, w)]
            text = f"{report.display_id(item_id)}{' 🔒' if c.id_override else ''}   " \
                   f"{c.name or '(unnamed)'}{'  ⚑' if c.flags else ''}"
            item.setText(0, text)
            item.setIcon(0, warn_icon if problems else QIcon())
            tips = problems + ([f"⚑ {len(c.flags)} flagged field(s)"] if c.flags else [])
            item.setToolTip(0, "\n".join(tips))
        if self.warnings_dock.isVisible():
            # The panel shows the same results now (not checked a second time).
            self._warnings_timer.stop()
            self.warnings_panel.show_computed(self.project, report, computed, labels)

    def _sync_spectra_pages(self) -> None:
        """Keep the Spectra section's pages (one per compound with spectra, in compound
        order) in step after spectra were added or removed, without rebuilding the tree.
        If the page on show has gone, the Spectra section itself is shown."""
        section = self.project.spectra_section if self.project is not None else None
        parent = self._find_item("section", section.id) if section is not None else None
        if parent is None:
            return
        wanted = [("spectra_page", c.id)
                  for c in spectra_mod.compounds_with_spectra(self.project)]
        if hrms_ui.any_reports(self.project):   # (SPEC §13n.3)
            wanted.append((HRMS_ITEM, HRMS_ITEM))
        have = [(parent.child(i).data(0, KIND_ROLE), parent.child(i).data(0, ID_ROLE))
                for i in range(parent.childCount())]
        if wanted == have:
            return
        current = self.tree.currentItem()
        lost = current is not None and current.parent() is parent and \
            (current.data(0, KIND_ROLE), current.data(0, ID_ROLE)) not in wanted
        updating, self._updating_tree = self._updating_tree, True
        kept = {(parent.child(i).data(0, KIND_ROLE), parent.child(i).data(0, ID_ROLE)):
                parent.child(i) for i in range(parent.childCount())}
        while parent.childCount():
            parent.takeChild(0)
        fixed = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for kind, cid in wanted:
            item = kept.get((kind, cid))
            if item is None:
                item = QTreeWidgetItem(["HRMS reports" if kind == HRMS_ITEM else ""])
                item.setData(0, ID_ROLE, cid)
                item.setData(0, KIND_ROLE, kind)
                item.setFlags(fixed)
                if kind == HRMS_ITEM:
                    self._hrms_tree_item(item)
                else:
                    item.setIcon(0, spectrum_icon())
            parent.addChild(item)
        if current is not None and not lost and current.parent() is parent:
            self.tree.setCurrentItem(current)   # (taking it out cleared the selection)
        self._updating_tree = updating
        if lost and not updating:
            self.tree.setCurrentItem(parent)

    def _hrms_tree_item(self, item: QTreeWidgetItem) -> None:
        item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        item.setToolTip(0, "The compounds' HRMS report files, printed at the end of the "
                           "Spectra section when “Print HRMS reports” is on (shows what will "
                           "print)")

    def _current(self) -> tuple[str | None, str | None]:
        item = self.tree.currentItem()
        if item is None:
            return None, None
        return item.data(0, KIND_ROLE), item.data(0, ID_ROLE)

    def _target_container(self) -> tuple[str, str | None, int | None] | None:
        """Where "New compound" adds: (section id, scheme id or None, index or None=end)."""
        if self.project is None:
            return None
        kind, item_id = self._current()
        if kind == "compound":
            sid, kid, i = self.project.position(self.project.compound(item_id))
            return sid, kid, i + 1
        if kind == "scheme":
            return self.project.scheme(item_id).section_id, item_id, None
        if kind == "section" and self.project.section(item_id).type == "data":
            return item_id, None, None
        return None

    def _target_data_section(self) -> str | None:
        target = self._target_container()
        return target[0] if target else None

    def _is_custom(self, section_id: str | None) -> bool:
        try:
            return self.project.section(section_id).type == "text"
        except (KeyError, AttributeError):
            return False

    def _is_spectra(self, section_id: str | None) -> bool:
        try:
            return self.project.section(section_id).type == SPECTRA_SECTION
        except (KeyError, AttributeError):
            return False

    def _sub_section_parent(self) -> str | None:
        """The selected custom section, if a sub-section can be added inside it."""
        kind, item_id = self._current()
        if self.project is None or kind != "section" or not self._is_custom(item_id):
            return None
        return item_id if self.project.can_place_section(item_id) else None

    def _can_place_section(self, section_id: str, parent_id: str) -> bool:
        if self.project is None:
            return False
        try:
            return self.project.can_place_section(parent_id or None,
                                                  self.project.section(section_id))
        except KeyError:
            return False

    def _find_item(self, kind: str, item_id: str) -> QTreeWidgetItem | None:
        for item in self._all_items():
            if item.data(0, KIND_ROLE) == kind and item.data(0, ID_ROLE) == item_id:
                return item
        return None

    def select(self, kind: str, item_id: str) -> None:
        item = self._find_item(kind, item_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    def open_compound_at(self, compound_id: str, field_key: str = "") -> None:
        """Select a compound and scroll its page to a field (used by the dashboard)."""
        self.select("compound", compound_id)
        if field_key:
            QTimer.singleShot(0, lambda: self._focus_field(field_key))

    def show_spectra_page(self, compound_id: str) -> None:
        """"View spectra page" on a compound page: show the compound's page under the
        Spectra section (after the click that asked for it has finished)."""
        def go():
            if self.project is None:
                return
            # (saved, or asked about, here: selecting the item below then has nothing
            # left to save, so a save that fails is tried, and reported, once)
            if not self._flush_or_stay():
                return
            self._sync_spectra_pages()
            section = self.project.spectra_section
            item = self._find_item("spectra_page", compound_id)
            if section is None or item is None:
                if self.panel is None and self.tree.currentItem() is not None:
                    # (left anyway above, and the spectrum went with the changes)
                    self._on_tree_selection(self.tree.currentItem(), None)
                QMessageBox.information(
                    self, "Spectra page",
                    "This compound has no spectra page yet: add a spectrum to its Spectra "
                    "field first." if section is not None else
                    "This project has no Spectra section yet (it is added when the project "
                    "is opened for editing).")
                return
            parent = item.parent()
            while parent is not None:   # (the Spectra section may sit inside another)
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(item)
        QTimer.singleShot(0, go)

    def _focus_field(self, key: str) -> None:
        panel = self.panel
        if isinstance(panel, SchemePanel):
            panel = panel.compound_panel
        if isinstance(panel, CompoundPanel):
            panel.focus_field(key)

    def _on_tree_selection(self, current, previous) -> None:
        if self._updating_tree:
            return
        if current is None or self.project is None:
            self._flush_panel()
            self._set_panel(None)
            self._update_ui_state()
            return
        kind, item_id = current.data(0, KIND_ROLE), current.data(0, ID_ROLE)
        # A sub-compound of the scheme page already shown: just switch tab.
        if kind == "compound" and isinstance(self.panel, SchemePanel):
            compound = self.project.compound(item_id)
            if compound.scheme_id == self.panel.scheme.id and self.panel.has_compound(item_id):
                if self.panel.current_compound_id() != item_id:
                    self.panel.select_compound(item_id)
                self._panel_where = self._places("compound", item_id)
                self.panel.set_collapsed(True)  # picked a compound in the tree: make room
                self._update_ui_state()
                self._update_preview_target()
                return
        panel = self.panel
        if not self._flush_or_stay():
            if self.panel is panel:
                # Stay on the page (its changes could not be saved): select its item again.
                self._keep_page_selected(previous)
            return   # (else it could not be read back: shown again read-only, see there)
        if panel is not None and self.panel is None and self._find_item(kind, item_id) is None:
            # Left anyway, and the item went with the changes thrown away (e.g. the spectra
            # page of a spectrum not saved): rebuild once the click is over.
            p = self.project
            QTimer.singleShot(0, self, lambda: self.project is p and self._populate_tree(
                (kind, item_id)))
            return
        if self.project.journal is not None:
            self.project.journal.break_merge()   # edits on another page: a step of their own
        self._show(kind, item_id)
        self._update_ui_state()

    def _on_section_dropped(self, section_id: str, parent_id: str, slot: int) -> None:
        """A section was dropped at `slot` among the sub-sections of `parent_id` (or the
        top-level sections when ""), counted before it was taken out."""
        if self.project is None or self.project.read_only:
            return
        if not self._flush_or_stay():
            return
        section = self.project.section(section_id)
        if (section.parent_id or "") == parent_id:
            old = self.project.siblings(section).index(section)
            slot = slot - 1 if slot > old else slot
        if parent_id:
            self._collapsed.discard(parent_id)   # show where it went
        with self._step(f"Move section “{section.name}”", [("section", section_id)]):
            self._save(lambda: self.project.move_section(section_id, slot, parent_id or None))
        # Rebuild after the drop has fully finished.
        QTimer.singleShot(0, lambda: self.project and self._populate_tree(("section", section_id)))

    def _on_item_dropped(self, kind: str, item_id: str, section_id: str, scheme_id: str,
                         slot: int) -> None:
        if self.project is None or self.project.read_only:
            return
        if not self._flush_or_stay():
            return
        self._set_panel(None)  # moving may move folders
        with self._step(self._move_label(kind, item_id), [(kind, item_id)]):
            if kind == "compound":
                self._move_compound_to(item_id, section_id, scheme_id or None, slot)
            else:
                scheme = self.project.scheme(item_id)
                if scheme.section_id == section_id:
                    old = self.project.section(section_id).items.index(scheme)
                    slot = slot - 1 if slot > old else slot
                self._save(lambda: self.project.move_scheme(item_id, section_id, slot))
        QTimer.singleShot(0, lambda: self.project and self._populate_tree((kind, item_id)))

    def _move_compound_to(self, compound_id: str, section_id: str, scheme_id: str | None,
                          slot: int) -> None:
        compound = self.project.compound(compound_id)
        sid, kid, old = self.project.position(compound)
        if sid == section_id and kid == scheme_id and slot > old:
            slot -= 1  # the slot was counted before the compound was taken out
        self._save(lambda: self.project.move_compound(compound_id, section_id, slot, scheme_id))

    def _tree_menu(self, pos) -> None:
        if self.project is None:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addActions(list(self.act_new_section.values()))
        menu.addMenu(self.new_sub_menu)
        menu.addActions([self.act_new_compound, self.act_new_scheme])
        item = self.tree.itemAt(pos)
        if item is not None and item.data(0, KIND_ROLE) == "spectra_page":
            menu.addSeparator()
            menu.addAction("Open the compound page",
                           lambda cid=item.data(0, ID_ROLE): self.select("compound", cid))
        elif item is not None and item.data(0, KIND_ROLE) in (*FIXED_KINDS, HRMS_ITEM):
            pass
        elif item is not None:
            menu.addSeparator()
            menu.addActions([self.act_rename, self.act_delete, self.act_up, self.act_down])
            kind, item_id = item.data(0, KIND_ROLE), item.data(0, ID_ROLE)
            if kind in ("compound", "scheme") and not self.project.read_only:
                menu.addAction("Duplicate…", lambda: self.duplicate_item(kind, item_id))
                self._add_move_menu(menu, kind, item_id)
            if kind == "section" and not self.project.read_only:
                self._add_section_move_menu(menu, item_id)
                if self._is_custom(item_id):
                    menu.addSeparator()
                    menu.addAction(self.act_use_as_general)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        # Free the menu, its "Move…" submenus and their actions (the chosen one has run
        # inside exec(); the shared actions and new_sub_menu belong to the window).
        menu.deleteLater()

    def _add_section_move_menu(self, menu: QMenu, section_id: str) -> None:
        """"Move into section ▸" (any custom section it may go in) and "Move to the top
        level" — the same as dragging, for those who prefer menus."""
        section = self.project.section(section_id)

        def go(parent_id: str | None) -> None:
            if not self._flush_or_stay():
                return
            container = self.project.section(parent_id).children if parent_id \
                else self.project.top_sections
            if parent_id:
                self._collapsed.discard(parent_id)
            with self._step(f"Move section “{section.name}”", [("section", section_id)]):
                self._save(lambda: self.project.move_section(section_id, len(container),
                                                             parent_id))
            QTimer.singleShot(0, lambda: self.project and
                              self._populate_tree(("section", section_id)))

        targets = [s for s in self.project.sections
                   if s.type == "text" and s.id != section.parent_id
                   and self.project.can_place_section(s.id, section)]
        if targets:
            into = menu.addMenu("Move into section")
            for s in targets:
                indent = "    " * (self.project.depth(s) - 1)
                into.addAction(indent + menu_text(s.name), lambda s=s: go(s.id))
        if section.parent_id:
            menu.addAction("Move out to the top level", lambda: go(None))

    def _add_move_menu(self, menu: QMenu, kind: str, item_id: str) -> None:
        def rebuild():
            QTimer.singleShot(0, lambda: self.project and self._populate_tree((kind, item_id)))

        def moving():
            return self._step(self._move_label(kind, item_id), [(kind, item_id)])

        if kind == "compound":
            compound = self.project.compound(item_id)
            if compound.scheme_id:
                def out():
                    if not self._flush_or_stay():
                        return
                    self._set_panel(None)
                    scheme = self.project.scheme(compound.scheme_id)
                    index = self.project.section(scheme.section_id).items.index(scheme) + 1
                    with moving():
                        self._save(lambda: self.project.move_compound(item_id, scheme.section_id,
                                                                      index))
                    rebuild()
                menu.addAction("Move out of scheme", out)
            else:
                menu.addAction("Change to reaction scheme…", lambda: self.convert_to_scheme(item_id))
            here = compound.section_id
            others = [s for s in self.project.data_sections() if s.id != here]
            schemes = [k for k in self.project.all_schemes() if k.id != compound.scheme_id]
            if schemes:
                into = menu.addMenu("Move into scheme")
                for k in schemes:
                    def go(k=k):
                        if not self._flush_or_stay():
                            return
                        self._set_panel(None)
                        with moving():
                            self._save(lambda: self.project.move_compound(
                                item_id, k.section_id, len(k.children), k.id))
                        rebuild()
                    into.addAction(menu_text(k.name), go)
        else:
            here = self.project.scheme(item_id).section_id
            others = [s for s in self.project.data_sections() if s.id != here]
        if others:
            move = menu.addMenu("Move to section")
            for s in others:
                def go_section(s=s):
                    if not self._flush_or_stay():
                        return
                    self._set_panel(None)
                    with moving():
                        if kind == "compound":
                            self._save(lambda: self.project.move_compound(item_id, s.id,
                                                                          len(s.items)))
                        else:
                            self._save(lambda: self.project.move_scheme(item_id, s.id,
                                                                        len(s.items)))
                    rebuild()
                move.addAction(menu_text(s.name), go_section)

    # ------------------------------------------------------------------ main panel

    def _show(self, kind: str, item_id: str) -> None:
        # The page's place in the tree: what its saves are about (undo), even once the tree
        # has moved on to another item (a click elsewhere saves the page it leaves).
        self._panel_where = self._places(kind, item_id)
        if kind == "compound":
            compound = self.project.compound(item_id)
            if compound.scheme_id:
                self._show_scheme(compound.scheme_id, item_id)
                return
            panel = CompoundPanel(self.project, compound, lambda: self.prefs.user_name)
            panel.labels_changed.connect(lambda cid: self._refresh_labels(cid))
            panel.convert_requested.connect(self.convert_to_scheme)
            panel.view_spectra.connect(self.show_spectra_page)
        elif kind == "scheme":
            self._show_scheme(item_id, None)
            return
        elif kind == "spectra_page":
            panel = SpectraPagePanel(self.project, self.project.compound(item_id))
            # (a pasted spectrum may answer a warning, e.g. a 13C report with no spectrum;
            # not on `saved`, which comes with every drag of a box)
            panel.spectra_changed.connect(self._refresh_labels)
            # (after the click has finished: the page is replaced)
            panel.open_compound.connect(
                lambda cid: QTimer.singleShot(0, lambda: self.select("compound", cid)))
        elif kind == HRMS_ITEM:
            panel = hrms_ui.HrmsReportsPage(self.project)
            panel.open_compound.connect(
                lambda cid: QTimer.singleShot(0, lambda: self.select("compound", cid)))
        elif kind == PROJECT_ITEM:
            panel = ProjectDashboard(self.project)
            panel.open_compound.connect(self.open_compound_at)
            # (after the click on the link has finished: the page is replaced)
            panel.open_section.connect(
                lambda sid: QTimer.singleShot(0, lambda: self.select("section", sid)))
        elif kind == "front_root":
            panel = FrontMatterOverview(self.project)
            panel.open_page.connect(lambda key: self.select("front", key))
        elif kind == "front":
            panel = FRONT_PANELS[item_id](self.project)
            # The title and authors appear in the window title and page header example.
            panel.saved.connect(lambda _msg: self._update_ui_state())
        elif kind == MANUSCRIPT_ITEM:
            from .manuscript_ui import ManuscriptPage
            panel = ManuscriptPage(self.project)
            panel.open_compound.connect(self.open_compound_at)
            panel.changed.connect(self._manuscript_changed)
        elif kind == REFERENCES_ITEM:
            from .references_ui import ReferencesPage
            panel = ReferencesPage(self.project)
            # Citation chips and "missing reference" warnings may change with the list.
            panel.saved.connect(lambda _msg: self._refresh_labels())
        else:
            section = self.project.section(item_id)
            panel = PANEL_TYPES[section.type](self.project, section)
            panel.renamed.connect(self._on_renamed)
            if isinstance(panel, DataSectionPanel):
                panel.ids_changed.connect(self._refresh_labels)
                panel.open_compound.connect(self.open_compound_at)
                panel.new_compound_requested.connect(self.new_compound)
                panel.pages_requested.connect(self.pages_for_ids)
        panel.saved.connect(lambda msg: self.statusBar().showMessage(msg, 2500))
        panel.saved.connect(lambda _msg: self.preview_pane.schedule())
        self._set_panel(panel)

    def _show_scheme(self, scheme_id: str, compound_id: str | None) -> None:
        self._panel_where = (self._places("compound", compound_id) if compound_id
                             else self._places("scheme", scheme_id))
        # Opened from one of its compounds: start with the scheme collapsed (more room for
        # the data). Opened from the scheme itself: show it.
        panel = SchemePanel(self.project, self.project.scheme(scheme_id),
                            lambda: self.prefs.user_name, compound_id,
                            collapsed=compound_id is not None)
        panel.labels_changed.connect(self._on_scheme_changed)
        panel.compound_selected.connect(self._sync_tree_to_tab)
        panel.view_spectra.connect(self.show_spectra_page)
        panel.read_back.connect(self._scheme_change_failed)
        panel.saved.connect(lambda msg: self.statusBar().showMessage(msg, 2500))
        # Pasting a scheme or placing labels changes the page without a "saved" message.
        panel.saved.connect(lambda _msg: self.preview_pane.schedule())
        panel.labels_changed.connect(self.preview_pane.schedule)
        self._set_panel(panel)

    def _on_scheme_changed(self) -> None:
        """Something changed on a scheme page: relabel the tree in place, or rebuild it
        quietly when compounds were added to or removed from the scheme (or reordered)."""
        if not isinstance(self.panel, SchemePanel):
            return
        scheme = self.panel.scheme
        current = self.panel.current_compound_id()
        sel = ("compound", current) if current else ("scheme", scheme.id)
        item = self._find_item("scheme", scheme.id)
        if item is not None and [item.child(i).data(0, ID_ROLE) for i in range(
                item.childCount())] == [c.id for c in scheme.children]:
            # Only labels changed (e.g. a compound tab saved a field): no rebuild, so the
            # tree keeps where it was scrolled to.
            self._refresh_labels()
            selected = self._find_item(*sel)
            if selected is not None and self.tree.currentItem() is not selected:
                self._updating_tree = True
                self.tree.setCurrentItem(selected)
                self._updating_tree = False
            self._update_ui_state()
        else:
            self._populate_tree(sel, rebuild_panel=False)  # keep the scheme page as it is
        # The tab on show may have changed (a compound added or deleted, quietly): the
        # preview follows it, never a compound that no longer exists.
        self._update_preview_target()

    def _sync_tree_to_tab(self, compound_id: str) -> None:
        item = self._find_item("compound", compound_id)
        if item is not None and self.tree.currentItem() is not item:
            self._updating_tree = True
            self.tree.setCurrentItem(item)
            self._updating_tree = False
            self._update_ui_state()
        self._panel_where = self._places("compound", compound_id)   # (the tab on show)
        self._update_preview_target()

    def _set_panel(self, panel: QWidget | None) -> None:
        if self.panel is not None:
            # Files the page has open (a File section's PDF) are let go now, not when the
            # page is deleted later: an undo, Delete or Move may move them straight away.
            release = getattr(self.panel, "release_files", None)
            if callable(release):
                release()
            self.panel_holder.removeWidget(self.panel)
            self.panel.deleteLater()
        self.panel = panel
        # Another page: the next relabelling checks every compound again (changes made on
        # other pages are not reported to _refresh_labels one by one).
        self._warn_cache, self._warn_guard = {}, None
        if panel is None:
            self._panel_where = []
            self.panel_holder.setCurrentWidget(self.empty_panel)
        else:
            self.panel_holder.addWidget(panel)
            self.panel_holder.setCurrentWidget(panel)
        same = self._preview_target() == self.preview_pane.target
        self._update_preview_target()
        if same:
            # The page was rebuilt (settings, references, a dismissed warning…), which may
            # change how it prints.
            self.preview_pane.schedule()

    # ------------------------------------------------------------------ live preview

    def _preview_target(self) -> tuple[str, str] | None:
        """What the live preview shows for the page on show (CompileOptions.only), or
        None for pages without one: the Data section overview and the Front matter item.
        On a scheme page it is always the whole scheme page, also while one of its
        compounds is being edited (SPEC §13p.3): the compound prints with its scheme."""
        panel = self.panel
        if self.project is None or panel is None:
            return None
        kind, item_id = self._current()
        if isinstance(panel, SchemePanel):
            return ("scheme", panel.scheme.id)
        if isinstance(panel, CompoundPanel):
            if panel.compound.scheme_id:
                return ("scheme", panel.compound.scheme_id)
            return ("compound", panel.compound.id)
        if isinstance(panel, SpectraPagePanel):
            return ("spectra", panel.compound.id)
        if isinstance(panel, hrms_ui.HrmsReportsPage):
            return ("hrms", "")
        if isinstance(panel, (DataSectionPanel, FrontMatterOverview, ProjectDashboard)):
            return None
        if kind in BOTTOM_KINDS:
            return None   # (the list itself is the page; compiled, only cited ones print;
            #               the manuscript check is never printed)
        if isinstance(panel, SectionPanel):
            return ("section", panel.section.id)
        for key, cls in FRONT_PANELS.items():
            if type(panel) is cls:
                return ("front", key)
        return None

    def _update_preview_target(self) -> None:
        self.preview_pane.set_target(self._preview_target())
        self._fit_preview_slot()

    def _fit_preview_slot(self) -> None:
        """Size the splitter's preview slot for the pane as it now is: the thin strip when
        collapsed, the remembered width when shown. The splitter does not do this by itself
        (it keeps the slot's old width, leaving an empty gap beside the strip), so this runs
        whenever the pane appears or is hidden/shown. While the pane is hidden (pages with
        no preview) nothing is done: it is sized when it appears again."""
        pane = self.preview_pane
        if pane.isHidden() or pane.collapsed == self._preview_slot:
            return
        if not self.splitter.isVisible():
            return   # its sizes are out of date until it is shown (see eventFilter)
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return
        if pane.collapsed:
            new = pane.strip.sizeHint().width()
        else:
            # The page in the middle gives back the preview's remembered width, keeping
            # at least 400 px for itself.
            new = max(MIN_PREVIEW_WIDTH, min(self.prefs.live_preview_width,
                                             sizes[2] + max(0, sizes[1] - 400)))
        self.splitter.setSizes([sizes[0], max(0, sizes[1] + sizes[2] - new), new])
        self._preview_slot = pane.collapsed

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.splitter and event.type() in (QEvent.Type.Show, QEvent.Type.Resize):
            # Shown (a project opened) or resized: once it has laid itself out, size the
            # preview slot if it isn't yet.
            QTimer.singleShot(0, self, self._fit_preview_slot)
        return super().eventFilter(obj, event)

    def _preview_collapsed(self, collapsed: bool, from_action: bool = False) -> None:
        """The pane was hidden or shown (its own buttons, or View ▸ Live preview);
        remembered on this computer."""
        pane = self.preview_pane
        if from_action:
            pane.set_collapsed(collapsed)
        if collapsed and pane.expanded_width >= MIN_PREVIEW_WIDTH:
            self.prefs.live_preview_width = pane.expanded_width
        self._fit_preview_slot()   # (or when the pane next appears, if hidden now)
        self.prefs.live_preview = not collapsed
        self.act_live.blockSignals(True)
        self.act_live.setChecked(not collapsed)
        self.act_live.blockSignals(False)

    def _remember_preview_width(self, *_args) -> None:
        if not self.preview_pane.collapsed and not self.preview_pane.isHidden():
            self.prefs.live_preview_width = self.preview_pane.width()

    def _flush_panel(self) -> None:
        if self.panel is not None and self.project is not None and not self.project.read_only:
            self._save(self.panel.flush)

    def _on_renamed(self, section_id: str, name: str) -> None:
        item = self._find_item("section", section_id)
        if item is not None:
            self._updating_tree = True
            item.setText(0, name)
            self._updating_tree = False
        if isinstance(self.panel, SectionPanel) and self.panel.section.id == section_id:
            self.panel.name_edit.setText(name)
        self._refresh_labels()  # ID warnings mention section names
        self.statusBar().showMessage("Saved", 2500)
        self.preview_pane.schedule()

    def _save(self, func) -> bool:
        """Run a saving operation, reporting (never hiding) any error."""
        try:
            func()
            return True
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return False

    # ------------------------------------------------------------------ undo / redo (§13k.11)

    def _where(self) -> list[tuple[str, str]]:
        """The selected tree item and the items it sits in, as (kind, id): where an undo
        step was made, best first (the nearest one that still exists is shown after an
        undo or redo)."""
        return self._places_of(self.tree.currentItem())

    @staticmethod
    def _places_of(item) -> list[tuple[str, str]]:
        out = []
        while item is not None:
            out.append((item.data(0, KIND_ROLE), item.data(0, ID_ROLE)))
            item = item.parent()
        return out

    def _places(self, kind: str, item_id: str) -> list[tuple[str, str]]:
        """(kind, id) and the tree items it sits in, best first."""
        item = self.tree.currentItem()
        if item is None or (item.data(0, KIND_ROLE), item.data(0, ID_ROLE)) != (kind, item_id):
            item = self._find_item(kind, item_id)
        return self._places_of(item) if item is not None else [(kind, item_id)]

    def _step(self, label: str, where=None):
        """`with self._step("Delete compound 3a"):` — one step of Edit ▸ Undo."""
        if self.project is None:
            from contextlib import nullcontext
            return nullcontext()
        where = list(where or []) + [w for w in self._where() if w not in (where or [])]
        return self.project.undo_step(label, where)

    @staticmethod
    def _place(step, kind: str, item_id: str) -> None:
        """The step is about a new item: show it after a redo (and what was selected
        before after an undo, when it is gone)."""
        if step is not None:
            step.where.insert(0, (kind, item_id))

    def _move_label(self, kind: str, item_id: str) -> str:
        if kind == "section":
            return f"Move section “{self.project.section(item_id).name}”"
        if kind == "scheme":
            return f"Move scheme page “{self.project.scheme(item_id).name}”"
        return f"Move {self.project.id_report().display_id(item_id)}"

    def _describe(self, kind: str | None, item_id: str | None) -> str:
        """A name for a step made by saving the page on show ("Edit 3a")."""
        p = self.project
        try:
            if kind == "compound":
                return f"Edit {p.id_report().display_id(item_id)}"
            if kind == "spectra_page":
                return f"Edit the spectra of {p.id_report().display_id(item_id)}"
            if kind == HRMS_ITEM:
                return "HRMS reports"
            if kind == "scheme":
                return f"Edit scheme page “{p.scheme(item_id).name}”"
            if kind == "section":
                section = p.section(item_id)
                if section.type == SPECTRA_SECTION:
                    return "Change the spectra defaults"
                return f"Edit section “{section.name}”"
            if kind == "front":
                return f"Edit {front.FRONT_PAGES[item_id]}"
            if kind == REFERENCES_ITEM:
                return "Edit references"
            if kind == MANUSCRIPT_ITEM:
                return "Manuscript check"
            if kind == PROJECT_ITEM:
                return "Change the project dashboard"
        except (KeyError, AttributeError):
            pass
        return "Change"

    def _undo_context(self) -> UndoContext:
        """What a save made outside a command is about: the page on show (its own
        description if it has one, e.g. the field of a compound page). Worked out from the
        page, not the tree: clicking another item saves the page being left."""
        where = list(self._panel_where) if self.panel is not None and self._panel_where \
            else self._where()
        kind, item_id = where[0] if where else (None, None)
        own = getattr(self.panel, "undo_context", None)
        if callable(own):
            key, label = own()
            return UndoContext(key, label, where)
        return UndoContext((kind, item_id), lambda: self._describe(kind, item_id), where)

    def _update_undo_actions(self) -> None:
        p = self.project
        can_undo = p is not None and p.can_undo
        can_redo = p is not None and p.can_redo
        self.act_undo.setEnabled(can_undo)
        self.act_redo.setEnabled(can_redo)
        self.act_undo.setText("&Undo " + menu_text(p.journal.undo_label)
                              if can_undo else "&Undo")
        self.act_redo.setText("&Redo " + menu_text(p.journal.redo_label)
                              if can_redo else "&Redo")
        undo_key = undo_keys.key_text(self.act_undo)   # (⌘Z on macOS)
        for action, what in ((self.act_undo, "Undo the last saved change"),
                             (self.act_redo, "Redo what was undone")):
            tip = (f"{what} ({undo_keys.key_text(action)}). In a text box, {undo_key} first "
                   "undoes its typing.")
            action.setToolTip(tip)
            action.setStatusTip(tip)

    def undo(self) -> None:
        self._undo_or_redo(redo=False)

    def redo(self) -> None:
        self._undo_or_redo(redo=True)

    def _undo_or_redo(self, redo: bool) -> None:
        """Edit ▸ Undo / Redo: put the project's files back, reload it, rebuild the tree
        and show the page the step was about (or the nearest one that still exists)."""
        p = self.project
        if p is None or p.read_only or p.journal is None:
            return
        self._check_lock()    # (taken over by someone else: their history, not ours)
        if p.read_only or p.journal is None:
            return
        # (typing not saved yet is saved first, so it can be undone)
        if not self._flush_or_stay("Undo" if not redo else "Redo"):
            return
        p.journal.break_merge()   # (an action still being saved is over)
        if not (p.can_redo if redo else p.can_undo):
            self._update_undo_actions()
            if self.panel is None:   # (the page whose changes could not be saved was left)
                self._populate_tree(self._current())
            return
        current = self._where()
        self._set_panel(None)   # release open files (e.g. a PDF shown on a page)
        if not (p.can_redo if redo else p.can_undo):
            step = None   # (an edit saved as the page closed took the step: just rebuild)
        else:
            try:
                step = p.redo() if redo else p.undo()
            except ProjectError as e:
                QMessageBox.warning(self, "Could not redo" if redo else "Could not undo",
                                    str(e))
                step = None
            except OSError as e:   # (reloading the project failed)
                QMessageBox.warning(self, "Could not reload the project", str(e))
                step = None
        # Everything is reloaded from the files: rebuild what was made from them.
        text_checks.set_project(p)   # the project's dictionary.txt may have changed
        ctx = rich_text.link_context()
        if ctx is not None:
            ctx.invalidate()
        self._populate_tree(None, rebuild_panel=False)
        candidates = (step.where if step is not None else []) + current
        for kind, item_id in candidates:
            item = self._find_item(kind, item_id)
            if item is not None:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(item)
                break
        else:
            self._populate_tree()
        if self.tree.currentItem() is not None and self.panel is None:
            self._on_tree_selection(self.tree.currentItem(), None)
        # (The Warnings panel was refreshed with the tree, from the reloaded project.)
        self._update_ui_state()
        if step is not None:
            self.statusBar().showMessage(f"{'Redone' if redo else 'Undone'}: {step.label}", 4000)

    # ------------------------------------------------------------------ section/compound commands

    def new_section(self, type_: str, inside: bool = False) -> None:
        """A new section after the selected one, at the same level; or, with `inside`, a
        sub-section at the end of the selected custom section."""
        if self.project is None or self.project.read_only:
            return
        label = SECTION_TYPE_LABELS[type_]
        label = label.lower() if type_ == "text" else label
        defaults = {"text": "Supplementary Notes", "file": "Optimization Table",
                    "data": "Products"}
        parent_id, index = None, None
        kind, item_id = self._current()
        if inside:
            parent_id = self._sub_section_parent()
            if parent_id is None:
                QMessageBox.information(self, "New sub-section",
                                        "Select a custom section first (sub-sections go inside "
                                        "custom sections, at most three levels deep).")
                return
        else:
            if kind == "compound":
                item_id, kind = self.project.compound(item_id).section_id, "section"
            elif kind == "scheme":
                item_id, kind = self.project.scheme(item_id).section_id, "section"
            elif kind in SPECTRA_CHILDREN and self.project.spectra_section is not None:
                item_id, kind = self.project.spectra_section.id, "section"
            if kind == "section":
                section = self.project.section(item_id)
                parent_id = section.parent_id
                index = self.project.siblings(section).index(section) + 1
            elif kind in BOTTOM_KINDS:
                index = None   # last, just before the Manuscript check and References
            elif kind in FIXED_KINDS:
                index = 0   # just after the front matter
        title = f"New {label} {'sub-section' if inside else 'section'}"
        name, ok = QInputDialog.getText(self, title, "Section name:", text=defaults[type_])
        if not ok or not name.strip():
            return
        if not self._flush_or_stay():
            return
        try:
            with self._step(f"New section “{name.strip()}”") as step:
                section = self.project.add_section(type_, name.strip(), index, parent_id)
                self._place(step, "section", section.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not create section", str(e))
            return
        if parent_id:
            self._collapsed.discard(parent_id)
        self._populate_tree(("section", section.id))

    def use_as_general_information(self) -> None:
        """Move the selected custom section's text into Front matter ▸ General Information
        (for projects made before the front matter existed), then trash the section."""
        kind, item_id = self._current()
        if self.project is None or self.project.read_only or kind != "section" \
                or not self._is_custom(item_id):
            return
        section = self.project.section(item_id)
        if section.children:
            QMessageBox.information(self, "Use as General Information",
                                    f"“{section.name}” has sub-sections. Move them out of it "
                                    "first (right-click ▸ Move out to the top level), so they "
                                    "are not sent to the Trash with it.")
            return
        if not self._flush_or_stay():
            return
        has_text = bool(self.project.read_front_text("general").strip())
        where = ("added after the text already on the General Information page"
                 if has_text else "put on the General Information page (Front matter), "
                 "which starts on a new page after the table of contents")
        if QMessageBox.question(
                self, "Use as General Information",
                f"Move the text and pictures of “{section.name}” into the front matter?\n\n"
                f"They will be {where}. The section itself then goes to the project Trash "
                "(it can be restored from File ▸ Trash).") != QMessageBox.StandardButton.Yes:
            return
        self._set_panel(None)
        # (undone: the section is back and shown; redone: General Information is shown)
        with self._step(f"Use “{section.name}” as General Information",
                        [("section", item_id), ("front", "general")]):
            done = self._save(lambda: self.project.use_as_general_information(item_id))
        if done:
            self._populate_tree(("front", "general"))
            self.statusBar().showMessage("Moved to General Information", 4000)
        else:
            self._populate_tree(("section", item_id))

    def new_compound(self) -> None:
        if self.project is None or self.project.read_only:
            return
        target = self._target_container()
        if target is None:
            QMessageBox.information(self, "New compound",
                                    "Select a Data section (or a compound in one) first.")
            return
        section_id, scheme_id, index = target
        if not self._flush_or_stay():
            return
        try:
            with self._step("New compound") as step:
                compound = self.project.add_compound(section_id, index, scheme_id=scheme_id)
                self._place(step, "compound", compound.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not create compound", str(e))
            return
        self._collapsed.discard(section_id)
        if scheme_id:
            self._collapsed.discard(scheme_id)
        self._populate_tree(("compound", compound.id))
        self._focus_field("name")

    def new_scheme(self) -> None:
        if self.project is None or self.project.read_only:
            return
        target = self._target_container()
        if target is None:
            QMessageBox.information(self, "New scheme page",
                                    "Select a Data section (or a compound in one) first.")
            return
        section_id = target[0]
        kind, item_id = self._current()
        index = None
        if kind in ("compound", "scheme"):
            # after the selected top-level item (a sub-compound counts as its scheme)
            obj = (self.project.compound(item_id) if kind == "compound"
                   else self.project.scheme(item_id))
            top = self.project.scheme(obj.scheme_id) if kind == "compound" and obj.scheme_id else obj
            index = self.project.section(section_id).items.index(top) + 1
        if not self._flush_or_stay():
            return
        try:
            with self._step("New scheme page") as step:
                # (drawn with the scheme editor: no blank compound — a product becomes one
                # when its "Compound" box is ticked, SPEC §13n.4)
                scheme = self.project.add_scheme(section_id, index)
                self._place(step, "scheme", scheme.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not create scheme page", str(e))
            return
        self._collapsed.discard(section_id)
        self._populate_tree(("scheme", scheme.id))

    def pages_for_ids(self, section_id: str) -> None:
        """The ID table's "Make pages for unused IDs…" (SPEC §13q.4)."""
        if self.project is None or self.project.read_only:
            return
        section = self.project.section(section_id)
        ids = self.project.ids_without_pages(section)
        if not ids:
            return
        shown = ", ".join(i or "(empty row)" for i in ids[:10]) + (", …" if len(ids) > 10 else "")
        empty = ids.count("")
        if QMessageBox.question(
                self, "Make pages for unused IDs",
                f"Make {len(ids)} blank compound page(s) at the end of “{section.name}”, "
                f"for {shown}?"
                + (f"\n\n{empty} row(s) of the table are empty: those pages will have no "
                   "ID until you fill them in." if empty else "")
                + "\n\nThey start with the section's default fields; Edit ▸ Undo removes "
                  "them again.") != QMessageBox.StandardButton.Yes:
            return
        if not self._flush_or_stay():
            return
        tab = self.panel.tabs.currentIndex() if isinstance(self.panel, DataSectionPanel) else 0
        try:
            with self._step(f"Make pages for the IDs of “{section.name}”",
                            [("section", section_id)]):
                made = self.project.add_pages_for_ids(section_id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not make the pages", str(e))
            made = []
        self._collapsed.discard(section_id)
        self._populate_tree(("section", section_id))
        if isinstance(self.panel, DataSectionPanel):
            self.panel.tabs.setCurrentIndex(tab)
        self.statusBar().showMessage(f"Made {len(made)} compound page(s).", 4000)

    def duplicate_item(self, kind: str, item_id: str) -> None:
        """Duplicate… a compound page or a scheme page (SPEC §13q.3)."""
        if self.project is None or self.project.read_only:
            return
        from ..core import duplicate as dup
        from .duplicate_ui import DuplicateDialog
        if not self._flush_or_stay():
            return
        item = (self.project.scheme(item_id) if kind == "scheme"
                else self.project.compound(item_id))
        dialog = DuplicateDialog(self.project, item, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        copies, keys, place = dialog.copies.value(), dialog.keys(), dialog.place()
        what = (f"scheme page “{item.name}”" if kind == "scheme"
                else self.project.id_report().display_id(item_id))
        self._set_panel(None)
        try:
            with self._step(f"Duplicate {what}" + (f" ({copies} copies)" if copies > 1 else ""),
                            [(kind, item_id)]) as step:
                if kind == "scheme":
                    made = dup.duplicate_scheme(self.project, item, keys, copies, place)
                else:
                    made = dup.duplicate_compound(self.project, item, keys, copies, place)
                self._place(step, kind, made[0].id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not duplicate", str(e))
            self._populate_tree((kind, item_id))
            return
        self._collapsed.discard(item.section_id)
        self._populate_tree((kind, made[0].id))
        self.statusBar().showMessage(f"Made {len(made)} cop{'y' if len(made) == 1 else 'ies'} "
                                     f"of {what}.", 4000)

    def convert_to_scheme(self, compound_id: str) -> None:
        if self.project is None or self.project.read_only:
            return
        compound = self.project.compound(compound_id)
        if QMessageBox.question(
                self, "Change to reaction scheme",
                f"Make a scheme page for “{compound.name or 'this compound'}”?\n\nThe compound "
                "becomes the first sub-page of the new scheme page (its data and ID are kept). "
                "You can then draw the scheme and add the other compounds in it."
        ) != QMessageBox.StandardButton.Yes:
            return
        if not self._flush_or_stay():
            return
        self._set_panel(None)
        shown = self.project.id_report().display_id(compound_id)
        try:
            with self._step(f"Change {shown} to a reaction scheme",
                            [("compound", compound_id)]) as step:
                scheme = self.project.convert_to_scheme(compound_id)
                self._place(step, "scheme", scheme.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not change to a scheme", str(e))
            self._populate_tree(("compound", compound_id))
            return
        self._populate_tree(("scheme", scheme.id))

    def rename_item(self) -> None:
        kind, item_id = self._current()
        if kind == "section":
            section = self.project.section(item_id)
            name, ok = QInputDialog.getText(self, "Rename section", "New name:", text=section.name)
            if not ok or not name.strip():
                return
            self._flush_panel()
            with self._step(f"Rename section “{section.name}”", [("section", item_id)]):
                done = self._save(lambda: self.project.rename_section(item_id, name))
            if done:
                self._on_renamed(item_id, name.strip())
        elif kind == "scheme":
            # (a name typed on the page is saved first: the dialog starts with it)
            if not self._flush_or_stay():
                return
            scheme = self.project.scheme(item_id)
            name, ok = QInputDialog.getText(self, "Rename scheme page", "Name:", text=scheme.name)
            if ok and name.strip():
                with self._step(f"Rename scheme page “{scheme.name}”", [("scheme", item_id)]):
                    done = self._save(lambda: self.project.rename_scheme(item_id, name))
                if done:
                    # The page on show still has the old name in its box: saving it (when
                    # the tree is rebuilt) would rename the scheme straight back.
                    self._set_panel(None)
                self._populate_tree(("scheme", item_id))
        elif kind == "compound":
            if not self._flush_or_stay():
                return
            compound = self.project.compound(item_id)
            name, ok = QInputDialog.getText(self, "Compound name", "Name:", text=compound.name)
            if not ok:
                return
            before = copy.deepcopy(compound.data)   # (put back if it can't be saved)
            value = compound.values.setdefault("name", {"text": ""})
            value["text"] = name.strip()
            shown = self.project.id_report().display_id(item_id)

            def write() -> None:
                if "name" not in compound.field_keys:
                    self.project.add_field(compound, {"key": "name"})   # (saves it too)
                else:
                    self.project.save_compound(compound)
            with self._step(f"Rename {shown}", [("compound", item_id)]):
                done = self._save(write)
            if not done:
                # Not saved (compound.json held, e.g. by OneDrive): memory keeps what the
                # file has, or the tree and page would show the new name as if saved (and
                # the compound's next save would write it unseen).
                compound.data.clear()
                compound.data.update(before)
            # A scheme page on show would be kept (only its tab is chosen again), with the
            # old name in its Name box, which its next autosave would write back.
            self._set_panel(None)
            self._populate_tree(("compound", item_id))

    def _mentions_of(self, kind: str, item_id: str) -> str:
        """A note for the delete question if other texts mention what is being deleted."""
        from ..core import links
        from ..core.markup import mentions, parse
        if kind == "compound":
            ids = [item_id]
        elif kind == "scheme":
            ids = [c.id for c in self.project.scheme(item_id).children]
        else:
            ids = [c.id for s in self.project.subtree(self.project.section(item_id))
                   for c in s.compounds]
        # (as links.mention_uses for each compound, but every text is read and parsed once)
        found = [(place.where, set(mentions(parse(place.text))))
                 for place in links.all_texts(self.project)] if ids else []
        places = []
        for cid in ids:
            places += [w for w, cids in found if cid in cids and w not in places]
        if not places:
            return ""
        shown = "\n• ".join(places[:6]) + ("\n…" if len(places) > 6 else "")
        return (f"\n\nIt is mentioned in:\n• {shown}\nThose mentions will show “??” and a "
                "warning until it is restored.")

    def delete_item(self) -> None:
        kind, item_id = self._current()
        if kind in (None, *FIXED_KINDS, *SPECTRA_CHILDREN):
            return
        if kind == "section":
            section = self.project.section(item_id)
            held = [s for s in self.project.subtree(section) if s.type == SPECTRA_SECTION]
            if held:   # never deleted (SPEC §13k.12); say why instead
                QMessageBox.information(
                    self, "Delete", SPECTRA_NOT_DELETED if held[0] is section else
                    f"“{section.name}” holds the Spectra section, which cannot be deleted. "
                    "Move the Spectra section out of it first (right-click ▸ Move out to the "
                    "top level), then delete it.")
                return
            below = self.project.subtree(section)[1:]
            n_compounds = sum(len(s.compounds) for s in self.project.subtree(section))
            what = f"the section “{section.name}”"
            extras = ([f"its {len(below)} sub-section(s)"] if below else []) + \
                ([f"{n_compounds} compound(s)"] if n_compounds else [])
            if extras:
                what += " with " + " and ".join(extras)
        elif kind == "scheme":
            scheme = self.project.scheme(item_id)
            what = f"the scheme page “{scheme.name}”"
            if scheme.children:
                what += f" and its {len(scheme.children)} compound(s)"
        else:
            c = self.project.compound(item_id)
            what = f"the compound “{self.project.id_report().display_id(item_id)} " \
                   f"{c.name or '(unnamed)'}”"
        renumber = kind != "section"
        if not self._flush_or_stay():
            return
        mentioned = self._mentions_of(kind, item_id)
        if QMessageBox.question(
                self, "Delete",
                f"Delete {what}?\n\nIt will be moved to the project Trash and can be restored "
                "from File ▸ Trash." + ("\n\nCompounds after it will be renumbered."
                                        if renumber else "") + mentioned
        ) != QMessageBox.StandardButton.Yes:
            return
        self._flush_panel()
        self._set_panel(None)  # release any open files before moving the folder
        where = self._where()   # (undone: the item is shown again; redone: what follows)
        box_problem = []   # (a scheme compound in the Trash whose box kept no copy)
        if kind == "section":
            section = self.project.section(item_id)
            parent = self.project.parent(section)
            siblings = self.project.siblings(section)
            index = siblings.index(section)
            with self._step(f"Delete section “{section.name}”", where) as step:
                deleted = self._save(lambda: self.project.delete_section(item_id))
            if siblings:   # the next section at the same level, or the one before
                select = ("section", siblings[min(index, len(siblings) - 1)].id)
            elif parent is not None:
                select = ("section", parent.id)
            else:
                select = ("front_root", front.FRONT_ROOT)
        elif kind == "scheme":
            scheme = self.project.scheme(item_id)
            section = self.project.section(scheme.section_id)
            with self._step(f"Delete scheme page “{scheme.name}”", where) as step:
                deleted = self._save(lambda: self.project.delete_scheme(item_id))
            select = ("section", section.id)
        else:
            compound = self.project.compound(item_id)
            sid, kid, index = self.project.position(compound)
            shown = self.project.id_report().display_id(item_id)
            def delete() -> None:
                try:
                    self.project.delete_compound(item_id)
                except BoxCopyFailed as e:   # (deleted: only its box is left empty)
                    box_problem.append(e)
            with self._step(f"Delete compound {shown}", where) as step:
                deleted = self._save(delete)
            container = (self.project.scheme(kid).children if kid
                         else self.project.section(sid).items)
            if container:
                nxt = container[min(index, len(container) - 1)]
                select = ("scheme" if isinstance(nxt, SchemePage) else "compound", nxt.id)
            else:
                select = ("scheme", kid) if kid else ("section", sid)
        if not deleted:   # (nothing was moved to the Trash: show it again, as it was)
            self._populate_tree((kind, item_id))
            return
        if step is not None:
            step.where.insert(1, select)
        self._populate_tree(select)
        self.statusBar().showMessage("Moved to Trash", 4000)
        for problem in box_problem:
            QMessageBox.warning(self, "Scheme box left empty", str(problem))

    def move_item(self, step: int) -> None:
        kind, item_id = self._current()
        if kind in (None, *FIXED_KINDS, *SPECTRA_CHILDREN):
            return
        if not self._flush_or_stay():
            return
        with self._step(self._move_label(kind, item_id), [(kind, item_id)]):
            if kind == "section":   # among the sections at the same level
                section = self.project.section(item_id)
                siblings = self.project.siblings(section)
                target = siblings.index(section) + step
                if 0 <= target < len(siblings):
                    self._save(lambda: self.project.move_section(item_id, target))
            elif kind == "scheme":
                scheme = self.project.scheme(item_id)
                items = self.project.section(scheme.section_id).items
                target = items.index(scheme) + step
                if 0 <= target < len(items):
                    self._set_panel(None)
                    self._save(lambda: self.project.move_scheme(item_id, scheme.section_id,
                                                                target))
            else:
                compound = self.project.compound(item_id)
                sid, kid, index = self.project.position(compound)
                size = len(self.project.scheme(kid).children if kid
                           else self.project.section(sid).items)
                target = index + step
                if 0 <= target < size:
                    self._set_panel(None)
                    self._save(lambda: self.project.move_compound(item_id, sid, target, kid))
        self._populate_tree((kind, item_id))

    # ------------------------------------------------------------------ project commands

    def show_settings(self) -> None:
        if self.project is None:
            return
        self._flush_panel()
        # (the page's latest changes could not be saved, and it has said so: the dialog is
        # modal, so nothing changes meanwhile, and saving it again would only say it twice)
        held = self._panel_save_failed()

        def after() -> None:
            if not held:
                self._after_warnings_changed()
                return
            self._refresh_labels()
            self.statusBar().showMessage("The page was not updated: its latest changes "
                                         "could not be saved yet.", 6000)
        dlg = ProjectSettingsDialog(self.project, self.prefs.user_name, self)
        writable = not self.project.read_only
        # One undo step for the dialog (including "Re-render pasted pictures" in it).
        with self._step("Change project settings"):
            ok = dlg.exec()
            accepted = ok and not self.project.read_only
            saved = accepted and self._save(dlg.apply)
            if saved:
                # Another HRMS tolerance may change a found m/z filled from the report
                # files (SPEC §13n.3a); a value typed by hand is never changed.
                self._save(self.project.refresh_hrms_found)
        if accepted:
            if dlg.user_name():
                self.prefs.user_name = dlg.user_name()
            if saved:
                self.statusBar().showMessage("Settings saved", 2500)
                # Tolerances / impurity table affect checks; drawing settings the pictures.
                after()
            self._update_ui_state()
        elif dlg.pictures_changed:
            after()  # re-rendered pictures: show the new ones
        if ok and writable and self.project.read_only:
            QMessageBox.warning(self, "Settings not saved",
                                "The project became read-only while Project settings was "
                                "open (someone else took it over, or it was closed or moved "
                                "elsewhere), so these settings were not saved.")

    def show_references(self, parent: QWidget | None = None) -> None:
        """File ▸ References (Ctrl+Shift+R): the References page at the bottom of the tree
        (SPEC §13n.10). From a text box's Cite list ("Add or edit references…", with
        parent) the list opens in a window instead, so the page being typed on stays as
        it is."""
        if self.project is None:
            return
        if isinstance(parent, QWidget):
            from .references_ui import ReferencesDialog
            with self._step("Edit references"):
                ReferencesDialog(self.project, parent).exec()
            return
        self.select(REFERENCES_ITEM, REFERENCES_ITEM)

    def show_trash(self) -> None:
        if self.project is None:
            return
        if not self._flush_or_stay():
            return
        current = self._current()
        self._set_panel(None)  # restoring a file may replace one being previewed
        try:
            TrashDialog(self.project, self).exec()
        finally:
            self._populate_tree(current)   # (also after an error: never a blank page)

    def save_snapshot(self) -> None:
        if self.project is None or self.project.read_only:
            return
        self._flush_panel()
        label, ok = QInputDialog.getText(
            self, "Save snapshot",
            "Optional label for this snapshot (e.g. “submitted version”):")
        if not ok or self.project is None or self.project.read_only:
            return   # (taken over while the question was open: _check_lock has said so)
        try:
            path = self.project.save_snapshot(label)
        except (ProjectError, OSError, ValueError) as e:   # (ValueError: an odd file name)
            QMessageBox.warning(self, "Could not save snapshot", str(e))
            return
        QMessageBox.information(self, "Snapshot saved",
                                f"A zipped copy of the project was saved to:\n{path}")

    def compile_si(self) -> None:
        if self.project is None or self.project.read_only:
            return
        self._flush_panel()
        dlg = CompileDialog(self.project, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._check_lock()   # (compiling writes to build/: not once someone else has it)
        if self.project is None or self.project.read_only:
            self.statusBar().showMessage("The project is now read-only; nothing was compiled.",
                                         6000)
            return
        with self._step("Change the compile choices"):
            # Remembering the choices is a convenience: if project.json can't be written
            # (held by OneDrive or a virus scanner, a read-only disk) say so and compile
            # anyway, as the choices are taken from the window (dlg.options()).
            self._save(dlg.remember)
        if self._preview is not None:
            self._preview.release()
        result = run_compile(self, self.project, dlg.options())
        if result is None:
            return
        self.statusBar().showMessage(f"SI compiled: {result.pdf}", 6000)
        if dlg.open_pdf.isChecked():
            open_file(result.pdf)
        show_warnings(self, result, "SI compiled")

    def preview_current(self) -> None:
        if self.project is None or self.project.read_only:
            return
        kind, item_id = self._current()
        if kind in (None, "front_root", PROJECT_ITEM, *BOTTOM_KINDS):
            return
        self._flush_panel()
        if kind == "compound" and isinstance(self.panel, SchemePanel):
            item_id = self.panel.current_compound_id() or item_id
        options = CompileOptions.from_settings(self.project.settings)
        options.only = ({"spectra_page": "spectra", HRMS_ITEM: "hrms"}.get(kind, kind),
                        item_id)
        if self._preview is None:
            self._preview = PreviewWindow(self)
        self._preview.release()  # free preview.pdf so it can be rewritten
        result = run_compile(self, self.project, options, name="preview")
        if result is None:
            return
        if kind == "compound":
            what = self.project.id_report().display_id(item_id)
        elif kind == "spectra_page":
            what = f"the spectra of {self.project.id_report().display_id(item_id)}"
        elif kind == HRMS_ITEM:
            what = "the HRMS reports"
        elif kind == "scheme":
            what = self.project.scheme(item_id).name
        elif kind == "front":
            what = "the " + front.FRONT_PAGES[item_id]
        else:
            what = self.project.section(item_id).name
        self._preview.show_pdf(result.pdf, what)
        show_warnings(self, result, "Preview")

    def show_folder(self) -> None:
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.folder)))
