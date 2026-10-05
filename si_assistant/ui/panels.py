"""Main-panel views for each kind of tree item (SPEC §5, §6)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..core.project import (FILE_SECTION_EXTENSIONS, Project, ProjectError, Section)
from .rich_text import RichTextEditor
from .structure_widgets import PasteFrame, PasteTargetMixin  # noqa: F401 (PasteFrame: here before)

AUTOSAVE_DELAY_MS = 800


class SectionPanel(QWidget):
    """Common parts: the section name box (renames the tree item)."""

    renamed = Signal(str, str)  # section id, new name
    saved = Signal(str)         # status-bar message

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.section = section

        self.name_edit = QLineEdit(section.name)
        self.name_edit.setReadOnly(project.read_only)
        self.name_edit.editingFinished.connect(self._commit_name)
        f = self.name_edit.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        self.name_edit.setFont(f)

        kind = QLabel(f"{section.type_label} section")
        kind.setStyleSheet("color: palette(placeholder-text);")

        self.layout_ = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self.name_edit, 1)
        top.addWidget(kind)
        self.layout_.addLayout(top)

    def _commit_name(self) -> None:
        name = self.name_edit.text().strip()
        if self.project.read_only or name == self.section.name:
            return
        if not name:
            self.name_edit.setText(self.section.name)
            return
        with self.project.undo_step(f"Rename section “{self.section.name}”"):
            self.project.rename_section(self.section.id, name)
        self.renamed.emit(self.section.id, name)
        self.saved.emit("Saved")

    def flush(self) -> None:
        """Save anything pending. Called before switching away or closing."""
        self._commit_name()


# ---------------------------------------------------------------------- text section

def text_with_objects(editor: RichTextEditor, project: Project, owner_key: str,
                      parent: QWidget) -> None:
    """Set up `editor` for a text that holds figures and tables (a custom section or
    General Information, SPEC §13k.5) and load the text. Older pictures become figures
    first, so they get labels and can be mentioned. (In the window this is done when the
    project is opened, before its undo history starts: done here, showing the page again
    after an undo would make a new undo step every time, so Undo could never get past it.)"""
    from .clipboard_image import paste_dpi
    from .text_objects import ObjectStore
    if not project.read_only and project.journal is None:
        try:
            project.upgrade_pictures(project.text_owner(owner_key))
        except (ProjectError, OSError) as e:
            QMessageBox.warning(parent, "Could not update the pictures", str(e))
    editor.enable_alignment()   # paragraphs, figures and tables can be aligned (§13l.10)
    editor.enable_objects(ObjectStore(project, owner_key))
    editor.edit.paste_dpi = paste_dpi(project.settings)   # pasted ChemDraw pictures
    editor.set_markup(project.read_owner_text(project.text_owner(owner_key)))
    editor.set_read_only(project.read_only)


def after_text_saved(editor: RichTextEditor) -> None:
    """Labels and mentions may have changed (a figure added, moved or deleted)."""
    from . import rich_text
    ctx = rich_text.link_context()
    if ctx is not None:
        ctx.invalidate()
    editor.edit.refresh_links()
    editor.edit.refresh_objects()


class TextSectionPanel(SectionPanel):
    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(project, section, parent)
        self.editor = RichTextEditor(self)
        text_with_objects(self.editor, project, section.id, self)
        self.layout_.addWidget(self.editor, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_DELAY_MS)
        self._timer.timeout.connect(self._save_text)
        self.editor.changed.connect(self._timer.start)

    def _save_text(self) -> None:
        self._timer.stop()
        if self.project.read_only:
            return
        with self.project.autosave():   # (typing: merged into one undo step)
            self.project.write_section_text(self.section, self.editor.markup())
        after_text_saved(self.editor)
        self.saved.emit("Saved")

    def flush(self) -> None:
        super().flush()
        if self._timer.isActive():
            self._save_text()


# ---------------------------------------------------------------------- attachment section
# (stored as type "file"; shown as "Attachment section", SPEC §13l.8)

class FilePreview(QStackedWidget):
    """Shows a PDF (all pages, scrollable) or an image, or a hint when empty."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from .structure_widgets import paste_key
        self._empty_text = ("Drop a PDF or picture here, click “Choose file…”, or paste:\n"
                            f"click here, then {paste_key()} (a copied PDF or picture file, "
                            "a picture, or a ChemDraw drawing).\n\n"
                            f"Accepted: {', '.join(FILE_SECTION_EXTENSIONS)}")
        self.empty = QLabel(self._empty_text)
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setFrameShape(QFrame.Shape.StyledPanel)
        self.empty.setStyleSheet("QLabel { border: 2px dashed palette(mid); padding: 40px; }")

        self.pdf_doc = QPdfDocument(self)
        self._pdf_open = False
        self.pdf_view = QPdfView(self)
        self.pdf_view.setDocument(self.pdf_doc)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setWidget(self.image_label)
        self._pixmap: QPixmap | None = None

        for w in (self.empty, self.pdf_view, self.image_scroll):
            self.addWidget(w)

    def show_file(self, path: Path | None) -> None:
        # Let go of the PDF on show: close() alone keeps the file open on Windows, which
        # then refuses to move it (to the Trash, or back by Edit ▸ Undo).
        if self._pdf_open:
            from .live_preview import release_pdf
            release_pdf(self.pdf_doc)
            self._pdf_open = False
        self._pixmap = None
        if path is None or not path.exists():
            self.empty.setText(self._empty_text if path is None else
                               f"The attached file is missing from the project folder:\n{path}")
            self.setCurrentWidget(self.empty)
            return
        if path.suffix.lower() == ".pdf":
            self.pdf_doc.load(str(path))
            self._pdf_open = True
            self.setCurrentWidget(self.pdf_view)
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image_label.setText(f"Cannot preview {path.name}, but it is attached.")
        else:
            self._pixmap = pixmap
            self._fit_image()
        self.setCurrentWidget(self.image_scroll)

    def _fit_image(self) -> None:
        if self._pixmap is None:
            return
        avail = self.image_scroll.viewport().size() - QSize(8, 8)
        if self._pixmap.width() > avail.width() or self._pixmap.height() > avail.height():
            self.image_label.setPixmap(self._pixmap.scaled(
                avail, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.image_label.setPixmap(self._pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_image()


class FileSectionPanel(PasteTargetMixin, SectionPanel):
    """An Attachment section (stored type "file"): one PDF or picture made elsewhere,
    with an optional heading and caption. Ctrl+V (or right-click ▸ Paste) puts the
    clipboard into it: a copied PDF or picture file, a picture, or a ChemDraw drawing
    (rendered like pasted structures, its original kept next to it). SPEC §13l.8."""

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(project, section, parent)
        ro = project.read_only
        self.setAcceptDrops(not ro)

        form = QFormLayout()
        self.heading_edit = QLineEdit(section.data.get("heading", ""))
        self.heading_edit.setPlaceholderText("Optional — shown as a heading and in the table of contents")
        self.caption_edit = QLineEdit(section.data.get("caption", ""))
        self.caption_edit.setPlaceholderText("Optional — printed under the file")
        for w in (self.heading_edit, self.caption_edit):
            w.setReadOnly(ro)
            w.editingFinished.connect(self._save_fields)
        form.addRow("Heading:", self.heading_edit)
        form.addRow("Caption:", self.caption_edit)
        self.layout_.addLayout(form)

        buttons = QHBoxLayout()
        self.file_label = QLabel()
        self.file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.choose_btn = QPushButton("Choose file…")
        self.choose_btn.clicked.connect(self._choose)
        from .structure_widgets import paste_key
        self.paste_btn = QPushButton("Paste")
        self.paste_btn.setToolTip("Put what you copied here: a PDF or picture file (copied in "
                                  "File Explorer or Finder), a picture, or a ChemDraw drawing. "
                                  f"Or click the file below and press {paste_key()} (right-click "
                                  "offers the same).")
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        self.open_btn = QPushButton("Open in default viewer")
        self.open_btn.clicked.connect(self._open_external)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove)
        buttons.addWidget(self.file_label)
        for b in (self.choose_btn, self.paste_btn, self.open_btn, self.remove_btn):
            buttons.addWidget(b)

        # The file block (buttons and preview) is the paste target: click it, then Ctrl+V.
        self.preview = FilePreview(self)
        self.paste_frame = PasteFrame()
        frame_layout = QVBoxLayout(self.paste_frame)
        frame_layout.setContentsMargins(2, 2, 2, 2)
        frame_layout.addWidget(self.preview)
        self.file_block = QWidget()
        hint = self._init_paste_target(self.paste_frame, [
            ("Paste (PDF or picture file, picture, ChemDraw drawing)", self.paste_from_clipboard),
            ("Choose file…", self._choose)], scope=self.file_block)
        block = QVBoxLayout(self.file_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.addLayout(buttons)
        block.addWidget(hint)
        block.addWidget(self.paste_frame, 1)
        self.layout_.addWidget(self.file_block, 1)
        self._refresh()

    def _refresh(self) -> None:
        path = self.project.section_file_path(self.section)
        has_file = path is not None
        ro = self.project.read_only
        self.file_label.setText(f"Attached: <b>{path.name}</b>" if has_file else "No file attached")
        self.choose_btn.setText("Replace…" if has_file else "Choose file…")
        # The right-click menu names it as the button does (it is read when the menu opens).
        self._paste_menu = [self._paste_menu[0], (self.choose_btn.text(), self._choose)]
        self.choose_btn.setEnabled(not ro)
        self.paste_btn.setEnabled(not ro)
        self.remove_btn.setEnabled(has_file and not ro)
        self.open_btn.setEnabled(has_file)
        self.preview.show_file(path)

    def release_files(self) -> None:
        """The page is going away: let go of the attached file at once (see
        MainWindow._set_panel)."""
        self.preview.show_file(None)

    def _save_fields(self) -> None:
        if self.project.read_only:
            return
        heading, caption = self.heading_edit.text(), self.caption_edit.text()
        if (heading, caption) != (self.section.data.get("heading", ""),
                                  self.section.data.get("caption", "")):
            self.section.data["heading"] = heading
            self.section.data["caption"] = caption
            self.project.save_section(self.section)
            self.saved.emit("Saved")

    def flush(self) -> None:
        super().flush()
        self._save_fields()

    def _choose(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in FILE_SECTION_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a PDF or image", "",
                                              f"PDF or image ({patterns})")
        if path:
            self._attach(Path(path))

    def _already_attached(self, path: Path) -> bool:
        """The section's own file (e.g. copied from the project folder and pasted back):
        say so, and change nothing."""
        if not self.project.is_section_file(self.section, path):
            return False
        QMessageBox.information(self, "Already attached",
                                f"“{path.name}” is already the file of this section; nothing "
                                "was changed.")
        return True

    def _attach(self, path: Path) -> None:
        if self._already_attached(path):
            return
        self.preview.show_file(None)  # release the old PDF so it can be moved to Trash
        try:
            with self.project.undo_step(f"Attach {path.name} to “{self.section.name}”"):
                self.project.attach_file(self.section, path)
            self.saved.emit("File attached (a copy is stored in the project folder)")
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not attach file", str(e))
        self._refresh()

    # ---- pasting (SPEC §13l.8)

    def paste_from_clipboard(self) -> None:
        """Put the clipboard into this section: a copied PDF or picture file, else a
        picture (a ChemDraw drawing is rendered at the project's paste resolution, with its
        original kept). Replaces the current file (it goes to the Trash), asking first."""
        if self.project.read_only:
            return
        from PySide6.QtGui import QGuiApplication
        from .clipboard_image import clipboard_picture, paste_dpi
        data = QGuiApplication.clipboard().mimeData()
        files = [Path(u.toLocalFile()) for u in (data.urls() if data is not None and
                                                  data.hasUrls() else []) if u.isLocalFile()]
        path = next((f for f in files if f.suffix.lower() in FILE_SECTION_EXTENSIONS), None)
        picture = None
        if path is None:
            picture = clipboard_picture(paste_dpi(self.project.settings))
        if path is None and picture is None:
            if files:
                QMessageBox.information(
                    self, "Cannot paste this file",
                    f"{files[0].name} is not a PDF or a picture. An Attachment section holds "
                    f"one PDF or picture ({', '.join(FILE_SECTION_EXTENSIONS)}).")
            else:
                QMessageBox.information(
                    self, "Nothing to paste",
                    "The clipboard has no PDF, picture or drawing. Copy a PDF or picture file "
                    "(in File Explorer or Finder), a picture, or a drawing in ChemDraw first.")
            return
        if path is not None and self._already_attached(path):
            return
        if not self._confirm_replace():
            return
        self.flush()
        self.preview.show_file(None)  # release the old PDF so it can be moved to Trash
        try:
            with self.project.undo_step(f"Paste into “{self.section.name}”"):
                if path is not None:
                    self.project.attach_file(self.section, path)
                else:
                    self.project.attach_pasted_picture(self.section, picture.png,
                                                       picture.source, picture.source_ext)
            self.saved.emit("Pasted (a copy is stored in the project folder)")
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not paste", str(e))
        self._refresh()

    def _confirm_replace(self) -> bool:
        """Ask before a paste replaces the attached file (nothing to ask when there is none)."""
        current = self.project.section_file_path(self.section)
        if current is None or not current.exists():
            return True
        return QMessageBox.question(
            self, "Replace the attached file",
            f"Replace “{current.name}” with what you copied? The current file goes to the "
            "project Trash and can be restored.") == QMessageBox.StandardButton.Yes

    def _remove(self) -> None:
        if QMessageBox.question(self, "Remove file",
                                "Remove the attached file? It will be moved to the project Trash "
                                "and can be restored.") != QMessageBox.StandardButton.Yes:
            return
        self.preview.show_file(None)
        try:
            with self.project.undo_step(f"Remove the file of “{self.section.name}”"):
                self.project.remove_file(self.section)
            self.saved.emit("File moved to Trash")
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove file", str(e))
        self._refresh()

    def _open_external(self) -> None:
        path = self.project.section_file_path(self.section)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped_path(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._dropped_path(event)
        if path is not None:
            event.acceptProposedAction()
            self._attach(path)

    def _dropped_path(self, event) -> Path | None:
        if self.project.read_only or not event.mimeData().hasUrls():
            return None
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = Path(urls[0].toLocalFile())
        return path if path.suffix.lower() in FILE_SECTION_EXTENSIONS else None
