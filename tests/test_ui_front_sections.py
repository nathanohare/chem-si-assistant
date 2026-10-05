"""Front matter pages, sub-sections in the tree, and the export choice in the Compile
window (SPEC §13k.4, 5, 10)."""

import pytest
from PySide6.QtCore import QSettings, Qt

from si_assistant.core import front
from si_assistant.core.project import Project


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))

    def make(p):
        p.close()
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        assert win.open_project(p.folder)
        return win
    wins = []
    yield lambda p: wins.append(make(p)) or wins[-1]
    for win in wins:
        win.close_project()
        win.close()


def test_front_matter_is_first_and_fixed(window, tmp_path):
    from si_assistant.ui.front_matter import CoverPagePanel, FrontMatterOverview, TocPanel
    p = Project.create(tmp_path, "F")
    p.acquire_lock("Tester")
    win = window(p)
    fm = win.tree.topLevelItem(1)   # under the project item (SPEC §13k.3)
    assert fm.text(0) == "Front matter"
    assert [fm.child(i).text(0) for i in range(3)] == ["Cover page", "Table of contents",
                                                       "General Information"]
    assert isinstance(win.panel, FrontMatterOverview)     # a new project: nothing else yet
    assert not fm.flags() & Qt.ItemFlag.ItemIsDragEnabled
    for a in (win.act_delete, win.act_rename, win.act_up, win.act_down):
        assert not a.isEnabled()
    win.panel.open_page.emit("cover")
    assert isinstance(win.panel, CoverPagePanel)
    win.select("front", "toc")
    assert isinstance(win.panel, TocPanel)
    win.delete_item()   # does nothing to the front matter
    assert win.tree.topLevelItem(1).childCount() == 3


def test_cover_page_saves_title_authors_and_extra_text(window, tmp_path):
    p = Project.create(tmp_path, "F")
    p.acquire_lock("Tester")
    win = window(p)
    win.select("front", "cover")
    panel = win.panel
    assert panel.heading_edit.text() == "Supporting Information"
    panel.title_edit.setText("Enamides")
    panel.title_edit.textEdited.emit("Enamides")
    panel.authors.add_row(["A. Chemist", "a", True, "a@x.org"])
    panel.affiliations.add_row(["a", "Example University"])
    panel.extra.edit.setPlainText("Extra words")
    win.select("front", "toc")          # switching away saves
    again = Project.open(p.folder)
    assert again.settings["title"] == "Enamides"
    assert again.settings["authors"][0] == {"name": "A. Chemist", "affiliations": ["a"],
                                            "corresponding": True, "email": "a@x.org"}
    assert again.settings["affiliations"] == [{"marker": "a", "text": "Example University"}]
    assert again.read_front_text("cover") == "Extra words"
    assert "cover" not in again.settings          # heading left at the style's own
    assert "Enamides" in win.windowTitle()


def test_toc_and_general_information_pages_save(window, tmp_path):
    p = Project.create(tmp_path, "F")
    p.acquire_lock("Tester")
    p.settings["list_compounds_in_toc"] = True     # an older project
    p.save_settings()
    win = window(p)
    win.select("front", "toc")
    toc = win.panel
    assert toc.depth.currentData() == "compounds"
    toc.depth.setCurrentIndex(toc.depth.findData("subsections"))
    toc.lists["schemes"].setChecked(True)
    toc.dots.setChecked(False)
    toc.same_size.setChecked(False)
    toc.size.setValue(9.5)
    win.select("front", "general")
    saved = Project.open(p.folder).settings
    assert saved["toc"]["depth"] == "subsections" and saved["toc"]["schemes"]
    assert saved["toc"]["dots"] is False and saved["toc"]["font_size"] == 9.5
    assert "list_compounds_in_toc" not in saved
    gi = win.panel
    gi.editor.edit.setPlainText("Argon atmosphere.")
    gi.heading_edit.setText("General Methods")
    gi.heading_edit.textEdited.emit("General Methods")
    win.select("front", "cover")
    again = Project.open(p.folder)
    assert again.read_front_text("general") == "Argon atmosphere."
    assert front.general_heading(again.settings) == "General Methods"


def test_sub_sections_in_the_tree(window, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    p = Project.create(tmp_path, "S")
    p.acquire_lock("Tester")
    methods = p.add_section("text", "Methods")
    data = p.add_section("data", "Products")
    win = window(p)
    proj = win.project
    # New sub-section inside the selected custom section.
    names = iter(["Solvents", "Drying", "Too deep"])
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: (next(names), True))
    win.select("section", methods.id)
    assert win.act_new_sub["data"].isEnabled()
    win.new_section("text", inside=True)
    solvents = proj.section(win._current()[1])
    assert solvents.parent_id == methods.id
    win.new_section("text", inside=True)
    drying = proj.section(win._current()[1])
    assert proj.depth(drying) == 3
    assert not win.act_new_sub["text"].isEnabled()      # already three levels deep
    item = win._find_item("section", drying.id)
    assert item.parent().parent() is win._find_item("section", methods.id)
    # Data sections cannot hold sections, so the tree refuses such a drop.
    assert not win.tree.can_place_section(methods.id, data.id)
    assert not win.tree.can_place_section(solvents.id, drying.id)   # inside itself
    assert win.tree.can_place_section(data.id, methods.id)
    # Drop the Data section into Methods (slot 0), then move it up/down among siblings.
    win._on_section_dropped(data.id, methods.id, 0)
    qapp_process()
    assert [s.id for s in proj.section(methods.id).children] == [data.id, solvents.id]
    win.select("section", data.id)
    win.move_item(1)
    assert [s.id for s in proj.section(methods.id).children] == [solvents.id, data.id]
    assert Project.open(p.folder).section(data.id).parent_id == methods.id
    # Deleting Methods takes everything inside it (one Trash item).
    win.select("section", methods.id)
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    win.delete_item()
    # (the Spectra section stays: it is never deleted, SPEC §13k.12)
    assert [s.type for s in proj.sections] == ["spectra"] and len(proj.list_trash()) == 1
    assert win._current() == ("section", proj.spectra_section.id)


def test_use_as_general_information_from_the_tree(window, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.front_matter import GeneralInfoPanel
    p = Project.create(tmp_path, "G")
    p.acquire_lock("Tester")
    old = p.add_section("text", "General Information")
    p.write_section_text(old, "Dry THF.")
    win = window(p)
    win.select("section", old.id)
    assert win.act_use_as_general.isEnabled()
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    win.use_as_general_information()
    assert isinstance(win.panel, GeneralInfoPanel)
    assert win.panel.editor.markup() == "Dry THF."
    assert [s.type for s in win.project.sections] == ["spectra"]


def test_compile_dialog_export_tree(qapp, project):
    from si_assistant.ui.compile_ui import CompileDialog
    methods = project.add_section("text", "Methods")
    sub = project.add_section("data", "Products", parent_id=methods.id)
    other = project.add_section("file", "X-ray")
    dlg = CompileDialog(project)
    tree = dlg.export
    items = tree._items
    assert set(items) == {"front", "cover", "toc", "general", methods.id, sub.id, other.id,
                          "spectra"}
    assert all(tree.is_included(k) for k in items)
    items[methods.id].setCheckState(0, Qt.CheckState.Unchecked)
    assert not tree.is_included(sub.id)                       # greyed out and unticked
    assert not items[sub.id].flags() & Qt.ItemFlag.ItemIsEnabled
    assert tree.excluded() == {methods.id}                    # only the parent is stored
    items["toc"].setCheckState(0, Qt.CheckState.Unchecked)
    dlg.remember()
    assert project.settings["export_excluded"] == sorted([methods.id, "toc"])
    assert dlg.options().excluded == {methods.id, "toc"}
    # Ticking the parent again brings back the sub-section's own tick.
    items[methods.id].setCheckState(0, Qt.CheckState.Checked)
    assert tree.is_included(sub.id)
    assert not dlg.nothing.isVisibleTo(dlg)
    tree.set_all(False)
    assert tree.nothing_included() and dlg.nothing.isVisibleTo(dlg)
    # Reopening shows the saved choice.
    again = CompileDialog(project).export
    assert not again.is_included(methods.id) and not again.is_included(sub.id)
    assert not again.is_included("toc") and again.is_included("cover")


def test_nothing_ticked_note_ignores_the_front_matter_container(qapp, project):
    """"Front matter" prints nothing by itself, so with all three of its pages, every
    section and the spectra unticked, the note shows even though it is still ticked."""
    from si_assistant.ui.compile_ui import CompileDialog
    methods = project.add_section("text", "Methods")
    dlg = CompileDialog(project)
    items = dlg.export._items
    for key in ("cover", "toc", "general", methods.id, "spectra"):
        items[key].setCheckState(0, Qt.CheckState.Unchecked)
    assert dlg.export.is_included("front")
    assert dlg.export.nothing_included() and dlg.nothing.isVisibleTo(dlg)
    items["toc"].setCheckState(0, Qt.CheckState.Checked)
    assert not dlg.export.nothing_included() and not dlg.nothing.isVisibleTo(dlg)


def test_cover_cell_being_typed_is_saved_on_close(window, tmp_path):
    """A table cell still open for typing (no Enter) is not lost when the project is
    closed or compiled."""
    from PySide6.QtWidgets import QApplication, QLineEdit
    p = Project.create(tmp_path, "F")
    p.acquire_lock("Tester")
    p.settings["authors"] = [{"name": "Old Name", "affiliations": [], "email": ""}]
    p.save_settings()
    win = window(p)
    win.select("front", "cover")
    table = win.panel.authors.table
    table.setCurrentCell(0, 0)
    table.editItem(table.item(0, 0))
    QApplication.processEvents()
    editor = table.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("New Name")
    win._flush_panel()                      # what Compile SI does first
    assert Project.open(p.folder).settings["authors"][0]["name"] == "New Name"
    from PySide6.QtCore import QEvent
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # old editor gone
    table.setCurrentCell(0, 3)
    table.editItem(table.item(0, 3))
    QApplication.processEvents()
    [editor] = table.viewport().findChildren(QLineEdit)
    editor.setText("new@example.org")
    win.close_project()
    assert Project.open(p.folder).settings["authors"][0]["email"] == "new@example.org"


def test_general_information_numbering_and_old_section_note(window, tmp_path):
    from si_assistant.ui.front_matter import GeneralInfoPanel
    p = Project.create(tmp_path, "G")
    p.acquire_lock("Tester")
    p.add_section("text", "General Information")
    win = window(p)
    assert win.act_use_as_general in win.menuBar().actions()[1].menu().actions()  # Edit menu
    win.select("front", "general")
    gi = win.panel
    assert isinstance(gi, GeneralInfoPanel)
    assert not gi.numbered.isChecked()
    assert gi.old_section_note.isVisibleTo(gi)               # points to the old section
    assert "Use as General Information" in gi.old_section_note.text()
    gi.numbered.setChecked(True)
    gi.editor.edit.setPlainText("Argon.")
    gi.flush()
    assert not gi.old_section_note.isVisibleTo(gi)           # the page has text now
    saved = Project.open(p.folder).settings
    assert front.general_numbered(saved) is True


def test_section_tooltip_shows_the_full_name(window, tmp_path):
    p = Project.create(tmp_path, "T")
    p.acquire_lock("Tester")
    top = p.add_section("text", "Experimental Procedures and Characterisation Data")
    sub = p.add_section("data", "Synthesis of Substituted Enamide Products", parent_id=top.id)
    win = window(p)
    tip = win._find_item("section", sub.id).toolTip(0)
    assert tip.splitlines()[0] == "Synthesis of Substituted Enamide Products"
    assert "level 2" in tip


def qapp_process():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
