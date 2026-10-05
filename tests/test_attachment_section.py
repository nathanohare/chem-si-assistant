"""Attachment sections (stored type "file"): the new name and pasting (SPEC §13l.8)."""

import pytest
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QMessageBox

from si_assistant.core.project import SECTION_TYPE_LABELS, Project
from tests.sample_project import fake_spectrum_pdf


def png_bytes(w=40, h=20):
    from si_assistant.ui.clipboard_image import image_to_png
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    return image_to_png(image)


def test_shown_as_attachment_section(project):
    assert SECTION_TYPE_LABELS["file"] == "Attachment"
    section = project.add_section("file", "X-ray")
    assert section.type == "file" and section.type_label == "Attachment"


# ---------------------------------------------------------------------- project data

def test_pasted_picture_keeps_its_original_and_goes_to_the_trash_with_it(project):
    section = project.add_section("file", "X-ray")
    first_png = png_bytes()
    path = project.attach_pasted_picture(section, first_png, b"EMF bytes", ".emf")
    assert path.name == "pasted picture.png" and section.data["file"] == path.name
    original = path.with_suffix(".emf")
    assert original.read_bytes() == b"EMF bytes"
    assert project.section_file_source(section) == original
    assert (path, original) in project.pasted_pictures()     # "Re-render pasted pictures"
    # Replacing it (here by a second paste) sends both to the Trash, as one item.
    second = project.attach_pasted_picture(section, png_bytes(30, 30))
    assert not original.exists() and project.section_file_source(section) is None
    entry = project.list_trash()[0]
    assert entry.kind == "file" and entry.info["companions"] == ["pasted picture.emf"]
    # Restoring brings the picture back with its original.
    project.restore_from_trash(entry.id)
    restored = project.section_file_path(section)
    assert restored.suffix == ".png" and project.section_file_source(section) is not None
    assert project.section_file_source(section).read_bytes() == b"EMF bytes"
    assert restored.read_bytes() == first_png and second.name == "pasted picture.png"
    (moved,) = project.list_trash()       # the second paste went to the Trash in its place
    assert moved.kind == "file" and "companions" not in moved.info


def test_an_attached_picture_file_is_not_a_pasted_picture(project, tmp_path):
    section = project.add_section("file", "Photo")
    own = tmp_path / "photo.png"
    own.write_bytes(png_bytes())
    project.attach_file(section, own)
    assert project.section_file_source(section) is None
    assert all(p != project.section_file_path(section) for p, _s in project.pasted_pictures())


# ---------------------------------------------------------------------- the page

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *a, **k: asked.append("info: " + (a[2] if len(a) > 2 else ""))))
    p = Project.create(tmp_path, "Paste")
    section = p.add_section("file", "Crystal structure")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    win.select("section", section.id)
    yield win, section.id, asked
    win.close_project()
    win.close()


def copy_files(*paths):
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    QGuiApplication.clipboard().setMimeData(data)


def test_page_is_an_attachment_section_and_a_paste_target(window):
    from si_assistant.ui.panels import FileSectionPanel
    win, sid, _asked = window
    panel = win.panel
    assert isinstance(panel, FileSectionPanel)
    item = win._find_item("section", sid)
    assert "Attachment section" in item.toolTip(0)
    assert "Attachment section" in [a.text() for a in win.act_new_section.values()][1]
    assert "Ctrl+V" in panel.paste_hint.text() or "⌘V" in panel.paste_hint.text()
    panel._set_paste_active(True)                     # what clicking the box does
    assert "pastes here" in panel.paste_hint.text()
    assert "solid" in panel.paste_frame.styleSheet() and "3px" in panel.paste_frame.styleSheet()


def test_paste_a_copied_file_then_a_picture_and_undo(window, tmp_path, monkeypatch):
    from si_assistant.ui import clipboard_image
    win, sid, asked = window
    panel = win.panel
    project = win.project
    section = project.section(sid)
    pdf = fake_spectrum_pdf(tmp_path / "xray report.pdf", "X-ray")

    copy_files(tmp_path / "notes.docx", pdf)          # the first PDF or picture is used
    panel.paste_from_clipboard()
    assert project.section_file_path(section).name == "xray report.pdf"
    assert asked == []                                 # nothing to replace: nothing asked
    assert win.act_undo.text() == "&Undo Paste into “Crystal structure”"
    assert panel.preview.currentWidget() is panel.preview.pdf_view

    # A ChemDraw drawing: rendered like structures (the project's paste resolution) and
    # kept with its original; the PDF goes to the Trash after a short question.
    dpis = []

    def fake_picture(dpi=1200):
        dpis.append(dpi)
        return clipboard_image.PastedPicture(png_bytes(), b"EMF bytes", ".emf")
    monkeypatch.setattr(clipboard_image, "clipboard_picture", fake_picture)
    QGuiApplication.clipboard().setMimeData(QMimeData())
    panel.paste_from_clipboard()
    assert dpis == [1200]
    assert len(asked) == 1 and "xray report.pdf" in asked[0]
    path = project.section_file_path(section)
    assert path.name == "pasted picture.png" and path.with_suffix(".emf").exists()
    assert panel.preview.currentWidget() is panel.preview.image_scroll
    assert any(e.name == "xray report.pdf" for e in project.list_trash())

    win.undo()                                         # the PDF is back, the picture gone
    section = win.project.section(sid)
    assert win.project.section_file_path(section).name == "xray report.pdf"
    assert not path.exists() and not path.with_suffix(".emf").exists()


def test_paste_answered_no_or_nothing_to_paste(window, tmp_path, monkeypatch):
    from si_assistant.ui import clipboard_image
    win, sid, asked = window
    panel = win.panel
    project = win.project
    pdf = fake_spectrum_pdf(tmp_path / "a.pdf", "A")
    copy_files(pdf)
    panel.paste_from_clipboard()
    # "No" to replacing: nothing changes.
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    copy_files(fake_spectrum_pdf(tmp_path / "b.pdf", "B"))
    panel.paste_from_clipboard()
    assert project.section_file_path(project.section(sid)).name == "a.pdf"
    # Nothing usable on the clipboard: said in plain words, nothing changes.
    monkeypatch.setattr(clipboard_image, "clipboard_picture", lambda dpi=1200: None)
    QGuiApplication.clipboard().setMimeData(QMimeData())
    panel.paste_from_clipboard()
    copy_files(tmp_path / "notes.docx")
    panel.paste_from_clipboard()
    assert [a for a in asked if a.startswith("info: ")][-2:] == [
        "info: The clipboard has no PDF, picture or drawing. Copy a PDF or picture file (in "
        "File Explorer or Finder), a picture, or a drawing in ChemDraw first.",
        "info: notes.docx is not a PDF or a picture. An Attachment section holds one PDF or "
        "picture (.pdf, .png, .jpg, .jpeg, .tif, .tiff, .svg)."]
    assert project.section_file_path(project.section(sid)).name == "a.pdf"


def test_ctrl_v_on_the_selected_section_pastes(window, tmp_path, monkeypatch):
    win, sid, _asked = window
    panel = win.panel
    calls = []
    monkeypatch.setattr(panel, "paste_from_clipboard", lambda: calls.append(1))
    panel._shortcut_paste()
    assert calls == [1]
    win.project.read_only = True
    panel._shortcut_paste()
    assert calls == [1]                               # read-only: nothing pasted
    win.project.read_only = False
