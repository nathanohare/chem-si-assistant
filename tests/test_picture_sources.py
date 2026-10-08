"""ChemDraw's vector originals kept next to pasted pictures (SPEC §13k.6)."""

import pytest

from si_assistant.core.project import ProjectError, picture_source_path

PNG = b"\x89PNG\r\n\x1a\n fake"
EMF = b"\x01\x00\x00\x00 fake emf"


def test_structure_picture_keeps_its_original(project):
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id, name="A")
    project.set_structure(c, "CCO", picture=PNG, vector=EMF, vector_ext=".emf")
    picture = project.structure_image_path(c)
    assert picture.name == "structure_image.png"
    assert picture_source_path(picture) == c.folder / "structure_image.emf"
    assert (c.folder / "structure_image.emf").read_bytes() == EMF

    # Removing the picture sends both files to the same Trash entry...
    project.remove_structure_image(c)
    assert not (c.folder / "structure_image.png").exists()
    assert not (c.folder / "structure_image.emf").exists()
    entry = project.list_trash()[0]
    assert {p.name for p in entry.folder.iterdir()} == {"entry.json", "structure_image.png",
                                                       "structure_image.emf"}
    # ...and restoring brings both back.
    project.restore_from_trash(entry.id)
    picture = project.structure_image_path(c)
    assert picture.read_bytes() == PNG
    assert picture_source_path(picture).read_bytes() == EMF


def test_new_picture_is_never_paired_with_an_old_original(project):
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    project.set_structure(c, "CCO")
    (c.folder / "structure_image.pdf").write_bytes(b"left over")   # e.g. from a sync
    project.set_structure_image(c, data=PNG)                      # a screenshot: no original
    picture = project.structure_image_path(c)
    assert picture.name != "structure_image.png"
    assert picture_source_path(picture) is None


def test_replacing_a_pasted_picture_trashes_its_original(project):
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    project.set_structure(c, "CCO", picture=PNG, vector=EMF, vector_ext=".emf")
    project.set_structure(c, "CCC")          # typed: ChemDraw's picture no longer matches
    assert project.structure_image_path(c) is None
    assert not any(c.folder.glob("structure_image*"))


def test_replace_picture_png_stays_in_the_project(project, tmp_path):
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    project.set_structure_image(c, data=PNG)
    picture = project.structure_image_path(c)
    project.replace_picture_png(picture, b"new")
    assert picture.read_bytes() == b"new"
    with pytest.raises(ProjectError):
        project.replace_picture_png(tmp_path / "elsewhere.png", b"x")


def test_file_field_wording():
    from si_assistant.core import fields as F
    assert F.ALL_FIELDS["private_files"].label == \
        "Store additional files and folders (never printed)"
    assert F.PLACEMENTS["appendix"] == "Spectra section"   # stored value unchanged


def test_attachments_note_explains_placements(qapp, project):
    from PySide6.QtWidgets import QLabel
    from si_assistant.core import fields as F
    from si_assistant.ui.field_widgets import AttachmentsEditor
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    editor = AttachmentsEditor(project, c, F.ALL_FIELDS["files"], lambda: None)
    text = " ".join(label.text() for label in editor.findChildren(QLabel))
    assert "Spectra section" in text and "Store additional files and folders" in text
    spectra = AttachmentsEditor(project, c, F.ALL_FIELDS["spectra"], lambda: None)
    assert "Store additional" not in " ".join(lb.text() for lb in spectra.findChildren(QLabel))


def test_attachments_note_offers_to_add_the_storage_field(qapp, project):
    from PySide6.QtCore import QCoreApplication
    from si_assistant.core import fields as F
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.field_widgets import AttachmentsEditor
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    project.add_field(c, {"key": "files"})
    assert "private_files" not in c.field_keys
    panel = CompoundPanel(project, c, lambda: "Me")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "files")
    assert "add it now" in editor.placement_note.text()
    editor.placement_note.linkActivated.emit("add")
    QCoreApplication.processEvents()
    assert "private_files" in c.field_keys
    editor = next(b.editor for b in panel.boxes if b.defn.key == "files")
    assert "add it now" not in editor.placement_note.text()
    assert any(b.defn.key == "private_files" for b in panel.boxes)
    # The note always says where the field comes from.
    plain = AttachmentsEditor(project, project.add_compound(s.id), F.ALL_FIELDS["files"],
                              lambda: None)
    assert "Add field" in plain.placement_note.text()
    panel.deleteLater()
