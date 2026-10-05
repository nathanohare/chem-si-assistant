"""Edit ▸ Undo / Redo in the main window, and where Ctrl+Z goes (SPEC §13k.11)."""

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit, QMessageBox

from si_assistant.core.project import Project
from tests.sample_project import FACTS, build_sample


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    wins = []

    def make(p):
        p.close()
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        assert win.open_project(p.folder)
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


@pytest.fixture
def sample(tmp_path):
    return build_sample(tmp_path)


def test_edit_menu_starts_with_undo_and_redo(window, sample):
    p, s = sample
    win = window(p)
    edit_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Edit")
    assert edit_menu.actions()[:2] == [win.act_undo, win.act_redo]
    # Opening the project (adding nothing) leaves nothing to undo.
    assert not win.act_undo.isEnabled() and win.act_undo.text() == "&Undo"
    assert not win.act_redo.isEnabled()
    assert "Ctrl+Y" in [k.toString() for k in win.act_redo.shortcuts()]


def test_delete_a_compound_undo_and_redo(window, sample):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    win.delete_item()
    assert win._find_item("compound", cid) is None
    assert win.act_undo.isEnabled()
    assert win.act_undo.text() == "&Undo Delete compound 2"
    win.undo()
    assert win.project.compound(cid).name == FACTS["c2_name"]
    assert win._current() == ("compound", cid)          # the page it was about
    assert win.panel.compound.id == cid
    assert win.act_redo.text() == "&Redo Delete compound 2"
    assert win.project.list_trash() == []
    win.redo()
    assert win._find_item("compound", cid) is None
    assert win._current()[0] in ("compound", "scheme", "section")   # what follows it
    assert len(win.project.list_trash()) == 1


def test_field_edits_merge_into_one_named_step(window, sample):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    old = win.project.compound(cid).name
    box = next(b for b in win.panel.boxes if b.defn.key == "name")
    for text in ("E", "Enam", "Enamide 2"):   # three autosaves while typing
        box.editor.widgets["text"].setText(text)
        box.editor.changed.emit()
        win.panel.save()
    assert win.act_undo.text() == "&Undo Edit 2 ▸ Name"
    assert len(win.project.journal.undo_stack) == 1
    # Another field of the same page: a step of its own.
    win.panel.override_edit.setText("2x")
    win.panel.override_edit.textEdited.emit("2x")
    win.panel.save()
    assert win.act_undo.text() == "&Undo Edit 2x ▸ Manual ID"
    assert len(win.project.journal.undo_stack) == 2
    win.undo()
    win.undo()
    assert win.project.compound(cid).name == old
    shown = next(b for b in win.panel.boxes if b.defn.key == "name")
    assert shown.editor.widgets["text"].text() == old   # the page was rebuilt
    assert win._find_item("compound", cid).text(0).startswith("2 ")
    win.redo()
    assert win.project.compound(cid).name == "Enamide 2"


def test_pending_typing_is_saved_before_an_undo(window, sample):
    p, s = sample
    win = window(p)
    win.select("section", s["notes"].id)
    win.panel.editor.edit.setPlainText("New text, not saved yet")
    win.undo()   # saves it first (one step), then undoes that step
    assert "Conditions were screened" in win.project.read_section_text(
        win.project.section(s["notes"].id))
    assert win.act_redo.isEnabled()
    assert win.act_redo.text() == "&Redo Edit section “Reaction Optimization”"


@pytest.mark.parametrize("page", ["compound", "scheme", "section", "front", "cover", "toc",
                                  "front_root", "spectra", "spectra_page", "project", "data"])
def test_showing_a_page_after_an_undo_keeps_redo(window, sample, page):
    """Building a page must not save anything: otherwise redo would be lost at once."""
    p, s = sample
    win = window(p)
    where = {"compound": ("compound", s["c2"].id), "scheme": ("scheme", s["scheme"].id),
             "section": ("section", s["notes"].id), "front": ("front", "general"),
             "spectra_page": ("spectra_page", s["c2"].id), "project": ("project", "project"),
             "data": ("section", s["data"].id), "cover": ("front", "cover"),
             "toc": ("front", "toc"), "front_root": ("front_root", "front"),
             "spectra": ("section", s["spectra"].id)}[page]
    win.select(*where)
    with win._step("Change project settings", [where]):   # (not shown on any page)
        win.project.settings["hrms_tolerance_ppm"] = 3.0
        win.project.save_settings()
    win.undo()
    assert win.project.settings["hrms_tolerance_ppm"] == 5.0
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    win._flush_panel()
    assert win._current() == where
    assert win.project.can_redo, win.project.journal.undo_label
    win.redo()
    assert win.project.settings["hrms_tolerance_ppm"] == 3.0
    assert win._current() == where


def test_settings_and_new_section_are_undone(window, sample):
    p, s = sample
    win = window(p)
    from si_assistant.ui import main_window as mw
    n = len(win.project.sections)
    import PySide6.QtWidgets as qw
    orig = qw.QInputDialog.getText
    qw.QInputDialog.getText = staticmethod(lambda *a, **k: ("Extra", True))
    try:
        win.select("section", s["notes"].id)
        win.new_section("text")
    finally:
        qw.QInputDialog.getText = orig
    assert len(win.project.sections) == n + 1
    assert win.act_undo.text() == "&Undo New section “Extra”"
    win.undo()
    assert len(win.project.sections) == n
    assert win._current() == ("section", s["notes"].id)
    win.redo()
    assert win._current()[0] == "section" and win.panel.section.name == "Extra"
    assert mw  # (module imported for the QInputDialog patch)


def files_of(folder):
    out = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if rel.parts[0] in ("build", ".lock"):
            continue
        out[rel.as_posix()] = path.read_bytes() if path.is_file() else None
    return out


def test_a_session_of_changes_is_undone_and_redone_exactly(window, sample, monkeypatch):
    """Many kinds of change, made as the window makes them; undoing them one by one gives
    back exactly the files of each earlier moment, and redoing gives them again."""
    from si_assistant.ui import text_objects
    from si_assistant.ui.dialogs import TrashDialog
    p, s = sample
    win = window(p)
    proj = lambda: win.project   # noqa: E731 (reloaded objects after each undo)
    states = [files_of(p.folder)]

    def done():
        win._flush_panel()
        states.append(files_of(p.folder))

    # 1. Typing in a custom section (two autosaves: one step).
    win.select("section", s["notes"].id)
    edit = win.panel.editor.edit
    edit.moveCursor(edit.textCursor().MoveOperation.End)
    edit.insertPlainText(" More")
    win.panel.flush()
    edit.insertPlainText(" text.")
    done()
    # 2. Remove a figure (picture to the Trash + its paragraph): one step.
    text_objects.remove_object(edit, "figure", s["figure"])
    done()
    # 3. A picture of a structure pasted on a compound page.
    win.select("compound", s["c2"].id)
    assert win._current() == ("compound", s["c2"].id), (win._current(), type(win.panel))
    assert not win.project.compound(s["c2"].id).scheme_id, win.project.compound(s["c2"].id)
    win.project.set_structure_image(win.panel.compound, data=b"\x89PNG pasted", ext=".png",
                                    vector=b"EMF", vector_ext=".emf")
    done()
    # 4. A spectrum's own settings, on its spectra page.
    win.select("spectra_page", s["c2"].id)
    win.project.set_spectrum_overrides(win.panel.compound, "spectra", "2_1H.pdf",
                                       {"per_page": 2}, page="1")
    done()
    # 5. Delete a compound (its folder goes to the Trash).
    win.select("compound", s["c2"].id)
    win.delete_item()
    done()
    # 6. Move a section, add a scheme page.
    win.select("section", s["notes"].id)
    win.move_item(-1)
    done()
    win.select("section", s["data"].id)
    win.new_scheme()
    done()
    # 7. References and settings (dialogs).
    with win._step("Edit references"):
        win.project.add_reference({"id": "r9", "type": "article", "title": "T"})
    done()
    # 8. Restore the compound from the Trash, then empty the Trash.
    dlg = TrashDialog(win.project, win)
    row = next(i for i, e in enumerate(dlg.entries) if e.kind == "compound")
    dlg.table.selectRow(row)
    monkeypatch.setattr(text_objects.QMessageBox, "information", staticmethod(lambda *a: None))
    from si_assistant.ui import dialogs
    monkeypatch.setattr(dialogs.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(dialogs.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dlg._restore()
    done()
    dlg._empty()
    dlg.close()
    win._populate_tree(("section", s["data"].id))
    done()
    assert len(win.project.journal.undo_stack) == len(states) - 1
    assert win.project.list_trash() == []

    for i in range(len(states) - 2, -1, -1):
        win.undo()
        assert files_of(p.folder) == states[i], f"after undoing step {i + 1}"
    assert not win.act_undo.isEnabled()
    assert proj().compound(s["c2"].id).name   # all back
    for i in range(1, len(states)):
        win.redo()
        assert files_of(p.folder) == states[i], f"after redoing step {i}"
    assert not win.act_redo.isEnabled()


def _shortcut_override(widget, key=Qt.Key.Key_Z):
    return QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.ControlModifier)


def test_ctrl_z_in_a_text_box_undoes_typing_first(window, sample, monkeypatch):
    from si_assistant.ui import undo_keys
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    keys = win._undo_keys
    edit = win.panel.override_edit
    monkeypatch.setattr(undo_keys.QApplication, "focusWidget", staticmethod(lambda: edit))
    # Nothing typed yet: Ctrl+Z goes to Edit ▸ Undo (the filter takes the question away
    # from the text box, so the window's shortcut fires).
    event = _shortcut_override(edit)
    assert keys.eventFilter(edit, event) is True and not event.isAccepted()
    edit.insert("9z")   # typing: now the text box has its own undo
    event = _shortcut_override(edit)
    assert keys.eventFilter(edit, event) is False
    # Ctrl+Y: nothing to redo in the box yet, so it goes to Edit ▸ Redo.
    assert keys.eventFilter(edit, _shortcut_override(edit, Qt.Key.Key_Y)) is True
    edit.undo()
    assert keys.eventFilter(edit, _shortcut_override(edit, Qt.Key.Key_Y)) is False
    # Other keys are never touched.
    assert keys.eventFilter(edit, _shortcut_override(edit, Qt.Key.Key_C)) is False


def test_ctrl_z_reaches_the_window_when_the_box_has_nothing_to_undo(window, sample, qapp):
    """End to end: a real Ctrl+Z in an empty-history text box undoes the project step;
    in a box with typing it undoes the typing only."""
    from PySide6.QtTest import QTest
    p, s = sample
    win = window(p)
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 2000)
    win.select("section", s["notes"].id)
    import PySide6.QtWidgets as qw
    orig = qw.QInputDialog.getText
    qw.QInputDialog.getText = staticmethod(lambda *a, **k: ("Renamed", True))
    try:
        win.rename_item()
    finally:
        qw.QInputDialog.getText = orig
    assert win.act_undo.text() == "&Undo Rename section “Reaction Optimization”"
    name = win.panel.name_edit
    name.setFocus()
    qapp.processEvents()
    if qapp.focusWidget() is not name:
        pytest.skip("this platform gives no keyboard focus")
    name.insert("!")
    QTest.keyClick(name, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not name.text().endswith("!")            # the typing was undone
    assert win.project.section(s["notes"].id).name == "Renamed"
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert win.project.section(s["notes"].id).name == "Reaction Optimization"


def _labels(win):
    return [s.label for s in win.project.journal.undo_stack]


def test_actions_on_a_compound_page_are_steps_of_their_own(window, sample, tmp_path, qapp):
    """Typing, two files added, a structure drawn, a field removed and added: each is a
    step, and undoing one leaves the others (and the typing) alone."""
    from tests.sample_project import fake_spectrum_pdf
    p, s = sample
    c13 = fake_spectrum_pdf(tmp_path / "x_13C.pdf", "13C")
    f19 = fake_spectrum_pdf(tmp_path / "x_19F.pdf", "19F")
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    n = len(win.project.journal.undo_stack)
    name = next(b for b in win.panel.boxes if b.defn.key == "name")
    name.editor.widgets["text"].setText("Enamide 2")   # typed, not saved yet
    name.editor.changed.emit()
    spectra = next(b for b in win.panel.boxes if b.defn.key == "spectra")
    spectra.editor.add_files([c13])
    qapp.processEvents()
    spectra = next(b for b in win.panel.boxes if b.defn.key == "spectra")
    spectra.editor.add_files([f19])
    qapp.processEvents()
    structure = next(b for b in win.panel.boxes if b.defn.key == "structure")
    old_formula = win.project.structure_info(win.project.compound(cid)).formula
    structure.editor.set_structure_text("CC(=O)N1CCCC=C1")
    qapp.processEvents()
    win.panel._remove_field("physical_state")
    qapp.processEvents()
    win.panel._add_field({"key": "physical_state"})
    qapp.processEvents()
    assert _labels(win)[n:] == ["Edit 2 ▸ Name", "Add x_13C.pdf to 2 ▸ Spectra",
                                "Add x_19F.pdf to 2 ▸ Spectra", "Change the structure of 2",
                                "Remove field “Physical state” from 2",
                                "Add field “Physical state” to 2"]
    win.undo()
    win.undo()
    assert "physical_state" in win.project.compound(cid).field_keys
    win.undo()   # the structure only
    c = win.project.compound(cid)
    assert win.project.structure_info(c).formula == old_formula
    assert [i["file"] for i in c.values["spectra"]][-2:] == ["x_13C.pdf", "x_19F.pdf"]
    assert c.name == "Enamide 2"   # the typing before is kept
    win.undo()   # the 19F file only
    assert [i["file"] for i in win.project.compound(cid).values["spectra"]][-1] == "x_13C.pdf"
    win.undo()
    win.undo()   # the typing, on its own
    assert win.project.compound(cid).name == FACTS["c2_name"]


def test_typing_then_removing_a_procedure_keeps_the_typing(window, sample, qapp):
    p, s = sample
    a = p.add_general_procedure(s["data"].id, name="A")
    b = p.add_general_procedure(s["data"].id, name="B")
    p.write_gp_text(a["id"], "Original GP text.")
    win = window(p)
    win.select("section", s["data"].id)
    procs = win.panel.procedures
    procs.list.setCurrentRow(0)
    procs.text.set_markup("Typed GP A text.")
    procs._edited()
    procs.flush()                       # (its autosave)
    procs.list.setCurrentRow(1)         # B
    procs._remove()
    qapp.processEvents()
    procs._add()
    qapp.processEvents()
    assert _labels(win)[-3:] == [f"Edit section “{FACTS['data_section']}”",
                                 "Remove general procedure “B”", "Add a general procedure"]
    win.undo()
    win.undo()
    assert win.project.general_procedure(b["id"]) is not None     # B is back
    assert win.project.read_gp_text(a["id"]) == "Typed GP A text."   # the typing is kept
    win.undo()
    assert win.project.read_gp_text(a["id"]) == "Original GP text."


def test_typing_after_removing_a_figure_is_a_step_of_its_own(window, sample):
    from si_assistant.ui import text_objects
    p, s = sample
    win = window(p)
    win.select("section", s["notes"].id)
    edit = win.panel.editor.edit
    text_objects.remove_object(edit, "figure", s["figure"])
    win._flush_panel()                  # (the paragraph taken out: joins the removal)
    edit.moveCursor(edit.textCursor().MoveOperation.End)
    edit.insertPlainText(" A whole new paragraph typed a minute later.")
    win._flush_panel()
    assert _labels(win)[-2:] == ["Remove Figure S1", "Edit section “Reaction Optimization”"]
    win.undo()                          # the typing only
    text = win.project.read_section_text(win.project.section(s["notes"].id))
    assert "minute later" not in text and f"!figure{{{s['figure']}}}" not in text
    win.undo()                          # the figure, with its paragraph
    text = win.project.read_section_text(win.project.section(s["notes"].id))
    assert f"!figure{{{s['figure']}}}" in text


def test_a_click_elsewhere_saves_typing_under_the_page_left(window, sample):
    p, s = sample
    win = window(p)
    win.select("section", s["notes"].id)
    win.panel.editor.edit.setPlainText("Typed quickly, not yet saved")
    win.select("compound", s["c2"].id)  # (the autosave had not run yet)
    assert win.act_undo.text() == "&Undo Edit section “Reaction Optimization”"
    assert win.project.journal.undo_stack[-1].where[0] == ("section", s["notes"].id)
    win.undo()
    assert win._current() == ("section", s["notes"].id)   # shows what was undone


def test_a_file_section_on_screen_can_be_undone_redone_and_deleted(window, sample, tmp_path):
    """The PDF shown on a File section's page is let go before an undo, redo or delete
    (Windows refuses to move a file that is open)."""
    from tests.sample_project import fake_spectrum_pdf
    p, s = sample
    section = p.add_section("file", "Optimization Table")
    pdf = fake_spectrum_pdf(tmp_path / "opt.pdf", "Table")
    win = window(p)
    win.select("section", section.id)
    win.panel._attach(pdf)
    assert win.act_undo.text() == "&Undo Attach opt.pdf to “Optimization Table”"
    assert win.panel.preview.currentWidget() is win.panel.preview.pdf_view
    win.undo()
    assert win.project.section_file_path(win.project.section(section.id)) is None
    win.redo()
    path = win.project.section_file_path(win.project.section(section.id))
    assert path is not None and path.exists()
    assert win.panel.preview.currentWidget() is win.panel.preview.pdf_view
    win.undo()
    assert not path.exists()
    win.redo()
    win.delete_item()
    assert win._find_item("section", section.id) is None
    win.undo()
    assert win.project.section_file_path(win.project.section(section.id)).exists()


def test_a_failed_delete_adds_no_step(window, sample, monkeypatch):
    from si_assistant.core import fileio
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    with win._step("Change project settings"):
        win.project.settings["hrms_tolerance_ppm"] = 3.0
        win.project.save_settings()
    win.undo()
    labels = _labels(win)

    def refuse(source, dest):
        raise PermissionError(13, "Access is denied", str(source))

    monkeypatch.setattr(fileio, "rename", refuse)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    win.select("compound", s["c2"].id)
    win.delete_item()
    assert _labels(win) == labels and win.act_redo.isEnabled()
    assert win.act_redo.text() == "&Redo Change project settings"


def test_typing_undone_in_its_box_leaves_nothing_to_undo_twice(window, sample):
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    win.panel.save()   # (a first save fills in a default the sample left out: scale unit)
    with win._step("Change project settings"):
        win.project.settings["hrms_tolerance_ppm"] = 3.0
        win.project.save_settings()
    box = next(b for b in win.panel.boxes if b.defn.key == "name").editor.widgets["text"]
    old = box.text()
    box.insert(" XYZ")
    win.panel._changed_keys.add("name")
    win.panel.save()
    assert win.act_undo.text() == "&Undo Edit 2 ▸ Name"
    while box.isUndoAvailable():        # the box's own Ctrl+Z
        box.undo()
    assert box.text() == old
    win.panel._changed_keys.add("name")
    win.panel.save()                     # its autosave
    assert win.act_undo.text() == "&Undo Change project settings"
    win.undo()
    assert win.project.settings["hrms_tolerance_ppm"] == 5.0


def test_someone_taking_over_keeps_their_undo_history(window, sample, monkeypatch):
    p, s = sample
    win = window(p)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    bob = Project.open(p.folder)
    bob.acquire_lock("Bob")
    win._check_lock()
    assert win.project.read_only and win.project.journal is None
    assert not win.act_undo.isEnabled()
    bob.enable_undo()
    with bob.undo_step("Delete compound 2"):
        bob.delete_compound(s["c2"].id)
    with bob.undo_step("Empty the Trash"):
        bob.empty_trash()
    win.close_project()                 # must not remove Bob's history
    bob.undo()
    assert len(bob.list_trash()) == 1
    bob.close()


def test_the_undo_tips_show_this_computers_keys(window, sample):
    from si_assistant.ui import undo_keys
    p, s = sample
    win = window(p)
    keys = undo_keys.key_text(win.act_undo)
    assert keys and keys in win.act_undo.toolTip()
    assert undo_keys.key_text(win.act_redo) in win.act_redo.toolTip()


def test_read_only_projects_have_no_undo(window, tmp_path, qapp):
    from si_assistant.ui import main_window as mw
    p = Project.create(tmp_path, "RO")
    p.close()
    p2 = Project.open(p.folder)
    p2.acquire_lock("Someone else")   # another person has it open
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    import si_assistant.ui.main_window as m
    orig = m.ask_about_lock
    m.ask_about_lock = lambda *a: "read_only"
    try:
        assert win.open_project(p.folder)
    finally:
        m.ask_about_lock = orig
    assert win.project.read_only and win.project.journal is None
    assert not win.act_undo.isEnabled()
    win.undo()   # does nothing
    win.close_project()
    win.close()
    p2.close()
