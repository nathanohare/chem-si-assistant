"""Compile SI dialog and the Preview window (SPEC §9)."""

from __future__ import annotations

from PySide6.QtCore import (QCoreApplication, QEvent, QEventLoop, QObject, QThread, Qt, QUrl,
                            Signal)
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..compile.document import CompileOptions, build
from ..compile.qt_images import content_box, convert_to_png, image_info
from ..compile.run import CompileError, CompileResult, partial_pdf, place_pdf, typeset_pdf
from ..core import front
from ..core import ms_reports
from ..core.project import SPECTRA_SECTION, Project


KEY_ROLE = Qt.ItemDataRole.UserRole


class ExportTree(QTreeWidget):
    """What goes into the SI (SPEC §13k.10): a tick box for each front matter page, every
    section and sub-section (the Spectra section among them). Unticking an item greys out
    (and leaves out) everything inside it; its sub-items keep their own ticks for when it is
    ticked again. There is no per-compound choice here."""

    changed = Signal()

    def __init__(self, project: Project, excluded: set[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._own: dict[str, bool] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        root = self._add(None, front.FRONT_ROOT, "Front matter", excluded)
        for key, name in front.FRONT_PAGES.items():
            self._add(root, key, name, excluded)

        # Older projects left the spectra out with the key "spectra": it unticks the
        # Spectra section (and is replaced by the section's own ID when saved).
        spectra_out = front.SPECTRA_KEY in excluded
        report = project.id_report()

        def add_sections(parent, sections):
            for s in sections:
                if s.type == SPECTRA_SECTION:
                    text = f"{s.name}   (the spectra of the compounds in the ticked Data sections)"
                    out = excluded | {s.id} if spectra_out else excluded
                else:
                    text, out = f"{s.name}   ({s.type_label} section)", excluded
                item = self._add(parent, s.id, text, out)
                add_sections(item, s.children)
                for c in s.compounds:   # X-ray structures under compounds (SPEC §13v.2)
                    for x in project.anchored.get(c.id, []):
                        self._add(item, x.id, f"{x.name}   (X-ray structure, after "
                                              f"{report.display_id(c.id)})", excluded)
                if s.type == SPECTRA_SECTION and any(
                        ms_reports.prints_reports(project, c) for c in project.all_compounds()):
                    # SPEC §13n.3: its sub-section of HRMS report files (when any print).
                    self._add(item, front.HRMS_KEY, "HRMS reports   (the compounds' HRMS "
                                                    "report files, after the spectra)",
                              excluded)
        add_sections(None, project.top_sections)
        if project.spectra_section is None:   # (a project opened read-only, made before it)
            self._add(None, front.SPECTRA_KEY, "Spectra (every compound's spectra, at the end)",
                      excluded)
        self.expandAll()
        for i in range(self.topLevelItemCount()):
            self._sync(self.topLevelItem(i), True)
        self.itemChanged.connect(self._toggled)

    def _add(self, parent, key: str, text: str, excluded: set[str]) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, KEY_ROLE, key)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        self._own[key] = key not in excluded
        item.setCheckState(0, Qt.CheckState.Checked if self._own[key] else Qt.CheckState.Unchecked)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items[key] = item
        return item

    def _toggled(self, item: QTreeWidgetItem, _column: int) -> None:
        if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        self._own[item.data(0, KEY_ROLE)] = item.checkState(0) == Qt.CheckState.Checked
        parent = item.parent()
        self._sync(item, True if parent is None else
                   self._own[parent.data(0, KEY_ROLE)] and bool(parent.flags()
                                                               & Qt.ItemFlag.ItemIsEnabled))
        self.changed.emit()

    def _sync(self, item: QTreeWidgetItem, parent_on: bool) -> None:
        """Show `item` (and below) as ticked/greyed according to its own tick and its
        parent's."""
        self.blockSignals(True)
        key = item.data(0, KEY_ROLE)
        flags = Qt.ItemFlag.ItemIsUserCheckable | (Qt.ItemFlag.ItemIsEnabled if parent_on
                                                   else Qt.ItemFlag.NoItemFlags)
        item.setFlags(flags)
        on = parent_on and self._own[key]
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.blockSignals(False)
        for i in range(item.childCount()):
            self._sync(item.child(i), on)

    def set_all(self, on: bool) -> None:
        for key in self._own:
            self._own[key] = on
        for i in range(self.topLevelItemCount()):
            self._sync(self.topLevelItem(i), True)
        self.changed.emit()

    def excluded(self) -> set[str]:
        """The items unticked themselves (whatever is inside them is left out too)."""
        return {key for key, on in self._own.items() if not on}

    def is_included(self, key: str) -> bool:
        item = self._items[key]
        return item.checkState(0) == Qt.CheckState.Checked

    def nothing_included(self) -> bool:
        """True when nothing that actually prints is ticked. "Front matter" is only a
        container (it prints nothing by itself), so it does not count."""
        return not any(self.is_included(k) for k in self._own if k != front.FRONT_ROOT)


class CompileDialog(QDialog):
    """Choices made when compiling. They are remembered in the project settings."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Compile SI")
        self.resize(640, 640)
        self.project = project
        options = CompileOptions.from_settings(project.settings)

        from .dialogs import COMPILE_ACS, COMPILE_PICTURES
        self.pictures = QRadioButton(COMPILE_PICTURES)
        self.acs = QRadioButton(COMPILE_ACS)
        group = QButtonGroup(self)
        group.addButton(self.pictures)
        group.addButton(self.acs)
        (self.acs if options.structures == "acs" else self.pictures).setChecked(True)
        note = QLabel("A compound with only a structure (SMILES, MOL) always gets the "
                      "program's ACS-style drawing. Pasted pictures print at the bond length "
                      "of Project settings ▸ Structures.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")

        spectra_note = QLabel("How the spectra are laid out (how many per page, orientation, "
                              "structure and ID…) is set on the Spectra section's page in the "
                              "tree.")
        spectra_note.setWordWrap(True)
        spectra_note.setStyleSheet("color: palette(placeholder-text);")
        self.export = ExportTree(project, set(options.excluded))
        self.export.setMinimumHeight(180)
        self.nothing = QLabel("⚠ Nothing is ticked: the SI would be empty.")
        self.nothing.setStyleSheet("color: #b36b00;")
        self.export.changed.connect(
            lambda: self.nothing.setVisible(self.export.nothing_included()))
        self.nothing.setVisible(self.export.nothing_included())
        all_btn = QPushButton("Tick everything")
        all_btn.clicked.connect(lambda: self.export.set_all(True))
        self.format_text = QCheckBox("Format chemistry text automatically (CDCl3 → CDCl₃, "
                                     "1H NMR → ¹H NMR, italic J, en-dashes)")
        self.format_text.setChecked(options.format_text)
        self.open_pdf = QCheckBox("Open the PDF when it's ready")
        self.open_pdf.setChecked(True)

        form = QFormLayout()
        structures = QVBoxLayout()
        for w in (self.pictures, self.acs, note):
            structures.addWidget(w)
        holder = QWidget()
        holder.setLayout(structures)
        form.addRow("Structures:", holder)
        form.addRow("Spectra:", spectra_note)
        form.addRow(self.format_text)
        form.addRow(self.open_pdf)
        buttons = QDialogButtonBox()
        buttons.addButton("Compile", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        include_row = QHBoxLayout()
        include_row.addWidget(QLabel("<b>Include in the SI</b> (unticking a section also leaves "
                                     "out its sub-sections and its compounds' spectra):"), 1)
        include_row.addWidget(all_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("The SI is saved as <b>build/SI.pdf</b> in the project folder "
                                "(with SI.typ, the source, next to it)."))
        layout.addLayout(form)
        layout.addLayout(include_row)
        layout.addWidget(self.export, 1)
        layout.addWidget(self.nothing)
        layout.addWidget(buttons)

    def options(self) -> CompileOptions:
        return CompileOptions(
            structures="acs" if self.acs.isChecked() else "pictures",
            format_text=self.format_text.isChecked(),
            excluded=frozenset(self.export.excluded()))

    def remember(self) -> None:
        o = self.options()
        self.project.settings.update({
            "compile_structures": o.structures, "format_text": o.format_text})
        front.store_excluded(self.project.settings, o.excluded)
        if not self.project.read_only:
            self.project.save_settings()


class _TypstJob(QThread):
    """Runs `work()` (Typst making the PDF) away from the window, keeping its result or
    the error it raised."""

    def __init__(self, work):
        super().__init__()
        self.work = work
        self.result = None
        self.error: BaseException | None = None

    def run(self) -> None:  # noqa: D401 — QThread
        try:
            self.result = self.work()
        except BaseException as e:  # noqa: BLE001 — handed back to the window's thread
            self.error = e


class _HoldClose(QObject):
    """While a compile finishes, the main window is not closed (its project would be
    closed under it): the close is ignored; the user can close it once it is done."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Close and isinstance(obj, QMainWindow):
            event.ignore()
            return True
        return False


def in_background(work):
    """`work()` run in a worker thread while the window keeps drawing itself (so Windows
    doesn't call it "Not responding" during a long compile); clicks and key presses wait
    until it is done, as they did before. Returns its result or raises its error."""
    job = _TypstJob(work)
    loop = QEventLoop()
    job.finished.connect(loop.quit)
    hold = _HoldClose()
    app = QCoreApplication.instance()
    app.installEventFilter(hold)
    try:
        job.start()
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        job.wait()   # (also when the loop ended early, e.g. the program is quitting)
    finally:
        app.removeEventFilter(hold)
    if job.error is not None:
        raise job.error
    return job.result


def run_compile(parent: QWidget, project: Project, options: CompileOptions,
                name: str = "SI") -> CompileResult | None:
    """Compile with a busy cursor; problems are shown to the user, never raised. The
    document is built here (it reads the project and uses Qt for pictures); Typst then
    makes the PDF in a worker thread, and it is put in place back here (compile.run.
    compile_pdf does all three in one go)."""
    if project.read_only:   # (a second safety net: the menu commands check this first)
        QMessageBox.warning(parent, "Could not compile", "This project is open read-only, so "
                            "the SI cannot be compiled into its build folder.")
        return None
    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        built = build(project, options, image_info, convert_to_png, name=name,
                      crop=content_box)
        warnings = in_background(lambda: typeset_pdf(built.typ_path, project.folder, name,
                                                     list(built.warnings)))
        if project.read_only:
            # (the window lost the right to write while Typst worked: e.g. the project was
            # taken over on another computer; the lock check runs during the compile)
            partial_pdf(built.typ_path, name).unlink(missing_ok=True)
            QMessageBox.warning(parent, "Could not compile", "The project became read-only "
                                "while the SI was being compiled, so the new PDF was not "
                                "saved.")
            return None
        return place_pdf(built.typ_path, name, warnings)
    except CompileError as e:
        QMessageBox.warning(parent, "Could not compile", str(e))
    except OSError as e:
        QMessageBox.warning(parent, "Could not compile",
                            f"A file could not be read or written:\n\n{e}")
    finally:
        QGuiApplication.restoreOverrideCursor()
    return None


def show_warnings(parent: QWidget, result: CompileResult, title: str) -> None:
    if result.warnings:
        QMessageBox.information(parent, title, "The PDF was made, with these notes:\n\n• "
                                + "\n• ".join(result.warnings))


def open_file(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class PreviewWindow(QDialog):
    """A quick look at one compound, scheme or section, as it will appear in the SI."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Preview")
        self.resize(820, 980)
        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.info = QLabel()
        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(self.view, 1)

    def release(self) -> None:
        """Let go of the PDF on show, so the next preview can be written over it.
        (QPdfDocument.close() alone keeps the file open on Windows.)"""
        from .live_preview import release_pdf
        release_pdf(self.doc)

    def show_pdf(self, path, what: str) -> None:
        self.release()
        self.doc.load(str(path))
        self.info.setText(f"Preview of {what} — page, section and scheme numbers are only "
                          "final in the full SI.")
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.release()  # so the next preview can replace the file
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self.release()  # Escape closes the window without a closeEvent
        super().done(result)
