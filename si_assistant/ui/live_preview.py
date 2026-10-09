"""The live preview pane (SPEC §13k.8): the page being edited, compiled on its own.

The pane sits on the right of the main window. It is shown for front matter pages,
custom and file sections, compound pages, scheme pages and a compound's spectra pages
(any `CompileOptions.only` kind), and hidden for pages with no preview of their own (a
Data section's overview).

How it updates:
- About a second after an edit is saved (`schedule()`; edits in quick succession are
  combined), and shortly after another page is selected (`set_target()`).
- The Typst source is written on the main thread (quick), then Typst itself runs in a
  worker thread, so the window never freezes. Only one compile runs at a time: an edit
  saved meanwhile is compiled as soon as the running one has finished.
- The PDFs alternate between two files (live_a.pdf / live_b.pdf), so the one on show is
  never overwritten while it is open (Windows locks open files).
- Everything is built in a temporary folder outside the project, so previews never add
  files to the Project Folder (or to its OneDrive/Dropbox sync). Typst's root is then a
  folder that holds both; if there is none (the project is on another drive), a writable
  project builds in its own build/live/ folder instead.
- Problems are shown inside the pane, never as pop-ups. Page numbers are placeholders.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QPoint, QSize, Qt, QThread, QTimer,
                            Signal)
from PySide6.QtGui import QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QStyle,
                               QStyleOptionToolButton, QToolButton, QVBoxLayout, QWidget)

from ..compile.document import CompileOptions, build
from ..compile.qt_images import content_box, convert_to_png, image_info
from ..compile.run import CompileError, keep_pages_until, run_typst
from ..core.project import BUILD_DIR, Project

EDIT_DELAY_MS = 1000     # after an edit is saved
SWITCH_DELAY_MS = 150    # after another page is selected (so arrowing through the tree
                         # doesn't compile every page on the way)
LIVE_NAME = "live"
TOC_TARGET = ("front", "toc")
TOC_FOLDER = "toc"       # where the table of contents is built (see _start)
SPECTRA_LIMIT = 8        # the Spectra section's live preview shows its first spectra only
MIN_WIDTH = 240
ZOOM_STEP = 1.25
MAX_ERROR_CHARS = 700


def release_pdf(doc: QPdfDocument) -> None:
    """Close `doc` so its file can be deleted. QPdfDocument.close() alone keeps the file
    open (Windows then refuses to delete it) until another document is loaded, so an
    empty one is loaded."""
    doc.close()
    empty = QBuffer(doc)
    empty.setData(QByteArray())
    empty.open(QIODevice.OpenModeFlag.ReadOnly)
    doc.load(empty)


class PreviewUnavailable(Exception):
    """The preview can't be made for this project (the message says why)."""


def live_folders(project: Project, temp: Path) -> tuple[Path, Path]:
    """(folder to build in, Typst root) for the live preview of `project`.

    The temporary folder `temp` is used when one folder holds both it and the project
    (always the case on macOS and Linux; on Windows when both are on the same drive). A
    writable project on another drive builds in its build/live/ instead."""
    try:
        root = Path(os.path.commonpath([str(project.folder.resolve()), str(temp.resolve())]))
        return temp, root
    except ValueError:   # different drives
        pass
    if not project.read_only:
        return project.folder / BUILD_DIR / LIVE_NAME, project.folder
    raise PreviewUnavailable(
        "The live preview isn't available for this read-only project: it is on a different "
        "drive from this computer's temporary folder, and a read-only project can't be "
        "written to.")


def target_exists(project: Project, target: tuple[str, str]) -> bool:
    """False when the page to preview has just been deleted (e.g. a scheme's compound)."""
    kind, item_id = target
    lookup = {"compound": project.compound, "scheme": project.scheme,
              "section": project.section, "spectra": project.compound}.get(kind)
    if lookup is None:
        return True
    try:
        lookup(item_id)
    except KeyError:
        return False
    return True


class _TypstJob(QThread):
    """Runs Typst on an already written source, away from the main thread. With
    `until_label`, only the pages up to that label are kept (the table of contents is
    compiled with the whole SI, so its page numbers are right, but shown on its own)."""

    def __init__(self, typ: Path, output: Path, root: Path, target, project, warnings,
                 parent: QWidget, until_label: str = ""):
        super().__init__(parent)
        self.typ, self.output, self.root = typ, output, root
        self.target, self.project, self.warnings = target, project, warnings
        self.until_label = until_label
        self.error = ""

    def run(self) -> None:
        try:
            run_typst(self.typ, self.output, self.root)
            if self.until_label:
                keep_pages_until(self.typ, self.output, self.root, self.until_label)
        except CompileError as e:
            self.error = str(e.__cause__ or e)
        except Exception as e:  # noqa: BLE001 — shown in the pane, never raised
            self.error = f"{type(e).__name__}: {e}"


class SideTab(QToolButton):
    """The thin strip left when the pane is collapsed: "◂ Live preview", written upwards."""

    TEXT = "▴  Live preview"   # (the arrow points left once the text is turned)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        return QSize(fm.height() + 10, fm.horizontalAdvance(self.TEXT) + 24)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.sizeHint().width(), 40)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option, painter, self)
        painter.translate(QPoint(0, self.height()))
        painter.rotate(-90)
        painter.setPen(self.palette().buttonText().color())
        h = self.height()
        painter.drawText(0, 0, h, self.width(), Qt.AlignmentFlag.AlignCenter, self.TEXT)
        painter.end()


class LivePreviewPane(QWidget):
    """See the module docstring. The main window calls set_project(), set_target() and
    schedule(); `collapsed_changed` reports the user's hide/show clicks."""

    collapsed_changed = Signal(bool)
    use_thread = True   # tests turn this off to compile on the main thread

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self.target: tuple[str, str] | None = None
        self.collapsed = False
        self._job: _TypstJob | None = None
        self._pending = False      # an edit was saved while Typst was running
        self._dirty = False        # something changed while the pane was hidden/collapsed
        self._temp: Path | None = None
        self._shown_file: Path | None = None
        self._shown_target: tuple[str, str] | None = None
        self.compiles = 0          # compiles started (for tests)
        self.expanded_width = 0    # its width when last collapsed

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)

        # Collapsed: just a strip to click.
        self.strip = SideTab()
        self.strip.setToolTip("Show the live preview of this page")
        self.strip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.strip.clicked.connect(lambda: self.set_collapsed(False, by_user=True))

        # Expanded: header, problems, the PDF (or a message), a note.
        self.body = QWidget()
        title = QLabel("<b>Live preview</b>")
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        def tool(text: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b

        self.zoom_out_btn = tool("−", "Zoom out", lambda: self._zoom(1 / ZOOM_STEP))
        self.zoom_in_btn = tool("+", "Zoom in", lambda: self._zoom(ZOOM_STEP))
        self.fit_btn = tool("Fit", "Fit the page width to the pane", self._fit_width)
        self.hide_btn = tool("▸", "Hide the live preview (it stays hidden until you show it "
                                  "again, on this computer)",
                             lambda: self.set_collapsed(True, by_user=True))
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 2, 2)
        header.addWidget(title)
        header.addSpacing(6)
        header.addWidget(self.status, 1)
        for b in (self.zoom_out_btn, self.zoom_in_btn, self.fit_btn, self.hide_btn):
            header.addWidget(b)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.setStyleSheet("QLabel { color: #b35900; padding: 4px; }")
        self.error_label.hide()

        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setMargin(16)
        self.message.setStyleSheet("color: palette(placeholder-text);")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.view)
        note = QLabel("Page numbers are placeholders; Compile SI gives the real ones.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text); font-size: 8pt;")
        note.setContentsMargins(4, 0, 4, 2)

        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        body.addLayout(header)
        body.addWidget(self.error_label)
        body.addWidget(self.stack, 1)
        body.addWidget(note)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.strip)
        layout.addWidget(self.body, 1)
        self._show_message("Nothing to show yet.")
        self._apply_collapsed()
        self.hide()

    # ------------------------------------------------------------------ state

    def set_project(self, project: Project | None) -> None:
        """A project was opened (or closed: None). Removes the old temporary folder."""
        if project is self.project:
            return
        self._wait()
        self._timer.stop()
        self._pending = self._dirty = False
        release_pdf(self.doc)
        self._shown_file = self._shown_target = None
        self._remove_temp()
        self.project = project
        self.target = None
        self.error_label.hide()
        self.status.setText("")
        self._show_message("Nothing to show yet.")
        self.hide()

    def set_target(self, target: tuple[str, str] | None) -> None:
        """The page now shown in the main panel: (kind, id) as in CompileOptions.only, or
        None for a page without a preview (the pane then hides)."""
        if self.project is None:
            target = None
        self.setVisible(target is not None)
        if target == self.target:
            return
        self.target = target
        self._timer.stop()
        if target is None:
            return
        if self._shown_target != target:
            self.error_label.hide()
            self._show_message("Preparing the preview…")
        self._request(SWITCH_DELAY_MS)

    def schedule(self) -> None:
        """An edit was saved: compile again in about a second (edits in quick succession
        are combined into one compile)."""
        if self.project is None or self.target is None:
            return
        self._request(EDIT_DELAY_MS)

    def set_collapsed(self, collapsed: bool, by_user: bool = False) -> None:
        if collapsed == self.collapsed:
            return
        if collapsed and not self.isHidden():
            self.expanded_width = self.width()   # for showing it again at the same width
        self.collapsed = collapsed
        self._apply_collapsed()
        if not collapsed and self._dirty:
            self._request(0)
        if by_user:
            self.collapsed_changed.emit(collapsed)

    def _apply_collapsed(self) -> None:
        self.strip.setVisible(self.collapsed)
        self.body.setVisible(not self.collapsed)
        if self.collapsed:
            self.setFixedWidth(self.strip.sizeHint().width())
        else:
            self.setMinimumWidth(MIN_WIDTH)
            self.setMaximumWidth(16_777_215)

    def busy(self) -> bool:
        return self._job is not None

    def shutdown(self) -> None:
        """The window is closing: wait for Typst, then tidy up."""
        self.set_project(None)

    # ------------------------------------------------------------------ compiling

    def _request(self, delay_ms: int) -> None:
        if self.collapsed or self.isHidden():
            self._dirty = True   # compiled when the pane is shown again
            return
        self._timer.start(delay_ms)

    def _start(self) -> None:
        self._timer.stop()
        project, target = self.project, self.target
        if project is None or target is None:
            return
        if self.collapsed or self.isHidden():
            self._dirty = True
            return
        if self._job is not None:
            self._pending = True   # compiled again as soon as this one is done
            return
        self._dirty = False
        if not target_exists(project, target):
            # (normally the main window has already moved on to another page)
            self.error_label.hide()
            self._shown_target = None
            self.status.setText("")
            self._show_message("This page no longer exists.")
            return
        try:
            if self._temp is None or not self._temp.exists():
                self._temp = Path(tempfile.mkdtemp(prefix="si-live-"))
            folder, root = live_folders(project, self._temp)
            options = CompileOptions.from_settings(project.settings)
            options.only = target
            options.preview_spectra = False   # the spectra get pages of their own
            options.spectra_limit = SPECTRA_LIMIT   # the Spectra section: a sample
            # The table of contents is built with the whole SI (for its page numbers), in
            # a folder of its own: the next page's build then doesn't first delete all of
            # its pictures (every build empties its folder's assets-live/).
            source = folder / TOC_FOLDER if tuple(target) == TOC_TARGET else folder
            result = build(project, options, image_info, convert_to_png, name=LIVE_NAME,
                           folder=source, root=root, crop=content_box)
        except PreviewUnavailable as e:
            self._show_message(str(e))
            return
        except Exception as e:  # noqa: BLE001 — e.g. the page was just deleted; never a pop-up
            self._show_problem(f"The preview could not be prepared: {e}")
            return
        if result.empty:
            self.error_label.hide()
            self._show_message("Nothing to show yet: nothing on this page would be printed.")
            self._shown_target = None
            self.status.setText("")
            return
        name = "live_b.pdf" if self._shown_file is not None and \
            self._shown_file.name == "live_a.pdf" else "live_a.pdf"
        job = _TypstJob(result.typ_path, folder / name, root, target, project,
                        result.warnings, self, until_label=result.last_page_label)
        self._job = job
        self.compiles += 1
        self.status.setText("Updating…")
        if self.use_thread:
            job.finished.connect(self._finished)
            job.start()
        else:
            job.run()
            self._finished()

    def _finished(self) -> None:
        job, self._job = self._job, None
        if job is None:
            return
        if job.isRunning():   # (only when called before the thread has quite ended)
            job.wait()
        job.deleteLater()
        pending, self._pending = self._pending, False
        if job.project is not self.project:
            return   # the project was closed meanwhile
        if job.target != self.target:
            self._start()   # another page was selected meanwhile: show that one
            return
        if job.error:
            self._show_problem("Typst could not make the preview:\n" + job.error)
        elif not job.output.exists():
            self._show_problem("The preview file was not written.")
        else:
            self._show_pdf(job.output, job.target, job.warnings)
        if pending:
            self._start()

    def _wait(self) -> None:
        """Let a running compile end, and forget it (nothing is shown or started)."""
        job, self._job = self._job, None
        self._pending = False
        if job is not None:
            job.wait()
            job.deleteLater()

    # ------------------------------------------------------------------ showing

    def _show_pdf(self, path: Path, target, warnings: list[str]) -> None:
        same = target == self._shown_target and self.stack.currentWidget() is self.view
        v = self.view.verticalScrollBar().value()
        h = self.view.horizontalScrollBar().value()
        self.doc.close()   # releases the other file, which the next compile overwrites
        self.doc.load(str(path))
        self._shown_file, self._shown_target = path, target
        self.stack.setCurrentWidget(self.view)
        self.error_label.hide()
        if same:   # an update of the same page: stay where the reader was
            QTimer.singleShot(0, lambda: self._scroll_to(v, h))
        else:
            QTimer.singleShot(0, lambda: self._scroll_to(0, 0))
        if warnings:
            n = len(warnings)
            self.status.setText(f"Up to date · {n} note{'s' if n > 1 else ''}")
            self.status.setToolTip("Notes from compiling this page:\n• " + "\n• ".join(warnings))
        else:
            self.status.setText("Up to date")
            self.status.setToolTip("")

    def _scroll_to(self, v: int, h: int) -> None:
        self.view.verticalScrollBar().setValue(v)
        self.view.horizontalScrollBar().setValue(h)

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    def _show_problem(self, text: str) -> None:
        """A problem: under the header if an earlier preview of this page is still on show
        (it stays, so the reader keeps their place), otherwise instead of the preview."""
        short = text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS] + " …"
        self.status.setText("⚠ Not updated")
        self.status.setToolTip(text)
        if self._shown_target == self.target and self.stack.currentWidget() is self.view:
            self.error_label.setText("⚠ " + short)
            self.error_label.setToolTip(text)
            self.error_label.show()
        else:
            self.error_label.hide()
            self._show_message("⚠ " + short)

    def _zoom(self, factor: float) -> None:
        current = self.view.zoomFactor()
        if self.view.zoomMode() != QPdfView.ZoomMode.Custom:
            # From "fit width": start at the zoom the pane is showing now.
            page = self.doc.pagePointSize(0)
            if page.width() > 0:
                pixels = page.width() * self.view.logicalDpiX() / 72
                room = self.view.viewport().width() - 2 * self.view.documentMargins().left()
                current = max(1, room) / pixels
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(max(0.2, min(6.0, current * factor)))

    def _fit_width(self) -> None:
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def _remove_temp(self) -> None:
        if self._temp is not None:
            shutil.rmtree(self._temp, ignore_errors=True)
            self._temp = None
