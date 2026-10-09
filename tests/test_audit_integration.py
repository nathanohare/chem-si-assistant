"""Fixes from the final integration review of the audit: saves that fail because a file is
held by another program (OneDrive, a virus scanner), and how they are reported.

Everything here is made up (the sample project of tests/sample_project.py, or small
projects built in a temporary folder)."""

import os
import shutil
import tempfile
import types
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from si_assistant.core import fileio
from si_assistant.core import manuscript as M
from si_assistant.core import scheme_editor as se
from si_assistant.core.project import BoxCopyFailed, Project, ProjectError
from tests.sample_project import FACTS, build_sample


def held(*_a, **_k):
    raise PermissionError(13, "held by another program")


@pytest.fixture
def short_tmp():
    """A short temporary folder (long paths can fail on Windows)."""
    folder = Path(tempfile.mkdtemp(prefix="si"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def sample(short_tmp):
    return build_sample(short_tmp)


@pytest.fixture
def warnings(monkeypatch):
    """The titles and texts of QMessageBox.warning calls (none is shown)."""
    seen = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, title, text, *a, **k: seen.append((title, text))))
    return seen


@pytest.fixture
def infos(monkeypatch, window):   # (after the window fixture's own answers)
    seen = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda _p, title, text, *a, **k: seen.append((title, text))))
    return seen


@pytest.fixture
def asked(monkeypatch, window):   # (after the window fixture's own answers)
    """The questions asked; "Changes not saved" is answered with answer["value"] (No)."""
    seen = []
    answer = {"value": QMessageBox.StandardButton.No}

    def question(_parent, title, text, *a, **k):
        seen.append((title, text))
        if title == "Changes not saved":
            return answer["value"]
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return types.SimpleNamespace(titles=lambda: [t for t, _ in seen], all=seen, answer=answer)


@pytest.fixture
def hooks(monkeypatch):
    """Exceptions that reached sys.excepthook (the crash reporter in the program)."""
    seen = []
    monkeypatch.setattr("sys.excepthook", lambda *a: seen.append(a))
    return seen


@pytest.fixture
def window(qapp, short_tmp, monkeypatch, warnings):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(short_tmp / "prefs.ini"),
                                             QSettings.Format.IniFormat))
    # (no dialog is ever shown: questions are answered Yes unless a test asks otherwise)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
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
        if win.project is not None:
            for name in ("save_scheme", "save_section", "save_settings", "save_compound"):
                vars(win.project).pop(name, None)   # (let the closing saves work)
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        win.close_project()
        win.close()


def type_conditions(step, text, which="top"):
    box = step.top if which == "top" else step.bottom
    box.text.set_markup(text)
    step._timers[which].start()   # (as typing does: the autosave waits)
    return box


def scheme_on_disk(folder, scheme_id):
    p = Project.open(folder, read_only=True)
    return p.scheme(scheme_id)


# ---------------------------------------------------------------------- integ-1: scheme editor

def test_a_conditions_autosave_with_scheme_json_held_warns_and_keeps_the_typing(
        window, sample, warnings, hooks):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    editor = win.panel.editor
    step = editor.steps[0]
    type_conditions(step, "NaBH4 (3 equiv)")
    win.project.save_scheme = held
    step._autosave(step.top)                     # (the timer's slot: must not raise)
    assert hooks == []
    assert [t for t, _ in warnings] == ["Could not save"]
    assert step.step["top"] == "NaBH4"           # memory still matches scheme.json
    assert win._panel_waits_to_save(win.panel)   # (the window asks before leaving)
    assert step.top.text.markup() == "NaBH4 (3 equiv)"
    # Once the file is free, the next flush saves it.
    del win.project.save_scheme
    editor.flush()
    assert not step._unsaved
    folder = win.project.folder
    win.close_project()
    assert se.steps(scheme_on_disk(folder, kid))[0]["top"] == "NaBH4 (3 equiv)"


def test_an_editor_action_while_typing_cannot_be_saved_changes_nothing(window, sample,
                                                                       warnings, hooks):
    p, s = sample
    win = window(p)
    win.select("scheme", s["scheme"].id)
    editor = win.panel.editor
    scheme = editor.scheme
    type_conditions(editor.steps[0], "LiAlH4")
    win.project.save_scheme = held
    starting = len(se.starting(scheme))
    editor.add_box(se.START)                     # (+ Starting material: must not raise)
    assert hooks == [] and editor.blocked
    assert len(se.starting(scheme)) == starting
    assert "could not be saved yet" in editor.status.text()
    # The typing is still in its (rebuilt) box, waiting to be saved.
    assert editor.steps[0].top.text.markup() == "LiAlH4"
    assert editor.steps[0]._unsaved and win._panel_waits_to_save(win.panel)


def test_save_scheme_with_its_file_held_does_not_lock_it(window, sample, warnings, hooks):
    p, s = sample
    win = window(p)
    win.select("scheme", s["scheme"].id)
    editor = win.panel.editor
    win.project.save_scheme = held
    editor.save_btn.click()
    assert hooks == []
    assert [t for t, _ in warnings] == ["Could not save the scheme"]
    assert not editor.locked and not se.is_locked(editor.scheme)   # (memory as on disk)


def test_set_conditions_and_set_locked_put_memory_back_when_not_saved(project, monkeypatch):
    data = project.add_section("data", "Products")
    scheme = project.add_scheme(data.id, name="S")
    step_id = se.steps(scheme)[0]["id"]
    se.set_conditions(project, scheme, step_id, "top", "NaBH4")
    monkeypatch.setattr(project, "save_scheme", held)
    with pytest.raises(PermissionError):
        se.set_conditions(project, scheme, step_id, "top", "LiAlH4")
    assert se.steps(scheme)[0]["top"] == "NaBH4"
    with pytest.raises(PermissionError):
        se.set_locked(project, scheme, True)
    assert not se.is_locked(scheme)


# ---------------------------------------------------------------------- integ-2: compile

def test_compile_goes_on_when_the_choices_cannot_be_remembered(window, sample, warnings,
                                                               monkeypatch, hooks):
    from si_assistant.ui import main_window as mw
    p, _s = sample
    win = window(p)
    monkeypatch.setattr(mw.CompileDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    compiled = []
    monkeypatch.setattr(mw, "run_compile", lambda *a, **k: compiled.append(a) or None)
    win.project.save_settings = held
    win.compile_si()                              # (must not raise)
    assert [t for t, _ in warnings] == ["Could not save"]
    assert len(compiled) == 1                     # compiled anyway, with the window's choices


# ---------------------------------------------------------------------- integ-3: leaving

def test_leaving_a_scheme_page_whose_conditions_could_not_be_saved_asks(window, sample,
                                                                        warnings, asked):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    page = win.panel
    type_conditions(page.editor.steps[0], "NaBH4 (3 equiv)")
    win.project.save_scheme = held
    win.select("compound", s["c2"].id)
    assert asked.titles() == ["Changes not saved"] and win.panel is page
    assert win.project.scheme(kid).data["editor"]["steps"][0]["top"] == "NaBH4"
    del win.project.save_scheme                   # the file is free again
    win.select("compound", s["c2"].id)
    assert win.panel is not page
    folder = win.project.folder
    win.close_project()
    assert se.steps(scheme_on_disk(folder, kid))[0]["top"] == "NaBH4 (3 equiv)"


def test_leaving_anyway_reads_the_conditions_back_from_the_file(window, sample, asked):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    type_conditions(win.panel.editor.steps[0], "NaBH4 (3 equiv)")
    win.project.save_scheme = held
    asked.answer["value"] = QMessageBox.StandardButton.Yes
    win.select("compound", s["c2"].id)
    assert asked.titles() == ["Changes not saved"]
    assert se.steps(win.project.scheme(kid))[0]["top"] == "NaBH4"


def test_leaving_a_scheme_page_whose_name_could_not_be_saved_asks(window, sample, asked):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    page = win.panel
    page.name_edit.setText("Reduction of the ketone")
    win.project.save_scheme = held
    win.select("compound", s["c2"].id)
    assert asked.titles() == ["Changes not saved"] and win.panel is page
    assert win.project.scheme(kid).name == FACTS["scheme_caption"]
    del win.project.save_scheme
    win.select("compound", s["c2"].id)
    assert win.panel is not page
    assert win.project.scheme(kid).name == "Reduction of the ketone"


def test_leaving_an_attachment_page_whose_heading_could_not_be_saved_asks(
        window, sample, warnings, asked):
    p, s = sample
    section = p.add_section("file", "Crystal data")
    win = window(p)
    win.select("section", section.id)
    page = win.panel
    page.heading_edit.setText("X-ray structure of 2")
    win.project.save_section = held
    page.heading_edit.editingFinished.emit()      # (the box is left: tried, and said)
    assert [t for t, _ in warnings] == ["Could not save"]
    win.select("compound", s["c2"].id)
    # Said once (not again on leaving), then asked.
    assert [t for t, _ in warnings] == ["Could not save"]
    assert asked.titles() == ["Changes not saved"] and win.panel is page
    del win.project.save_section
    win.select("compound", s["c2"].id)
    assert win.panel is not page
    assert win.project.section(section.id).data["heading"] == "X-ray structure of 2"
    assert Project.open(win.project.folder, read_only=True).section(
        section.id).data["heading"] == "X-ray structure of 2"


def test_leaving_a_spectra_page_whose_caption_could_not_be_saved_asks(window, sample,
                                                                      warnings, asked):
    p, s = sample
    cid = s["c2"].id
    win = window(p)
    win.select("spectra_page", cid)
    page = win.panel
    item = page.current.item
    page.caption_edit.setText("Major rotamer")
    win.project.save_compound = held
    page.caption_edit.editingFinished.emit()
    assert [t for t, _ in warnings] == ["Could not save"]
    assert not item.get("struct_caption")         # memory still matches compound.json
    win.select("section", s["notes"].id)
    assert [t for t, _ in warnings] == ["Could not save"]   # (not said twice)
    assert asked.titles() == ["Changes not saved"] and win.panel is page
    del win.project.save_compound
    win.select("section", s["notes"].id)
    assert win.panel is not page
    folder = win.project.folder
    win.close_project()
    c = Project.open(folder, read_only=True).compound(cid)
    assert any(i.get("struct_caption") == "Major rotamer" for i in c.values["spectra"])


def test_set_spectrum_text_puts_memory_back_when_not_saved(sample, monkeypatch):
    p, s = sample
    c = s["c2"]
    item = c.values["spectra"][0]
    monkeypatch.setattr(p, "save_compound", held)
    with pytest.raises(PermissionError):
        p.set_spectrum_text(c, "spectra", item["file"], "title", "My header")
    assert "title" not in item or not item["title"]


# ---------------------------------------------------------------------- integ-4: rename

def test_renaming_a_compound_whose_file_is_held_keeps_the_old_name(window, sample, warnings,
                                                                   monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    cid = s["c2"].id
    old = s["c2"].name
    win = window(p)
    win.select("compound", cid)
    monkeypatch.setattr(mw.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Renamed thing", True)))
    win.project.save_compound = held
    win.rename_item()
    assert [t for t, _ in warnings] == ["Could not save"]
    assert win.project.compound(cid).name == old
    assert old in win._find_item("compound", cid).text(0)
    del win.project.save_compound
    c = win.project.compound(cid)
    win.project.set_flag(c, "name", True)          # an unrelated save later
    assert Project.open(win.project.folder, read_only=True).compound(cid).name == old


def test_renaming_a_compound_without_a_name_field_says_so_once(window, sample, warnings,
                                                               monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    c = s["c2"]
    c.data["fields"] = [e for e in c.fields if e["key"] != "name"]
    p.save_compound(c)
    win = window(p)
    win.select("compound", c.id)
    monkeypatch.setattr(mw.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Renamed thing", True)))
    win.project.save_compound = held
    win.rename_item()
    assert [t for t, _ in warnings] == ["Could not save"]   # (once, not twice)
    assert "name" not in win.project.compound(c.id).field_keys


# ---------------------------------------------------------------------- integ-5: said once

def test_enter_on_the_cover_title_with_the_file_held_says_so_once(window, sample, warnings):
    p, _s = sample
    win = window(p)
    win.select("front", "cover")
    page = win.panel
    win.project.save_settings = held
    page.title_edit.setText("A new title")
    page.title_edit.textEdited.emit("A new title")
    page.title_edit.editingFinished.emit()       # (Enter)
    [(title, text)] = warnings
    assert title == "Could not save" and "lost if the page is closed" not in text
    page.title_edit.editingFinished.emit()       # (nothing new typed: not tried again)
    page.flush()                                 # (leaving: tried again, not said again)
    assert len(warnings) == 1 and page._unsaved
    del win.project.save_settings
    page.flush()
    assert not page._unsaved and win.project.settings["title"] == "A new title"


def test_project_settings_on_a_page_that_cannot_be_saved_says_so_once(window, sample,
                                                                      warnings, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    page = win.panel
    page.override_edit.setText("77")
    page.override_edit.textEdited.emit("77")
    win.project.save_compound = held
    dialog = types.SimpleNamespace(exec=lambda: 1, apply=lambda: None, user_name=lambda: "",
                                   pictures_changed=False)
    monkeypatch.setattr(mw, "ProjectSettingsDialog", lambda *a, **k: dialog)
    win.project.refresh_hrms_found = lambda: None
    win.show_settings()
    assert [t for t, _ in warnings] == ["Could not save"]
    assert win.panel is page and page.override_edit.text() == "77"


# ---------------------------------------------------------------------- integ-6: failed moves

@pytest.fixture
def two_sections(short_tmp, monkeypatch):
    """Products: 2, scheme K (compound 4), 5; and an empty second Data section."""
    monkeypatch.setattr(fileio, "_REPLACE_DELAY_S", 0)
    p, s = build_sample(short_tmp)
    data = s["data"]
    c5 = p.add_compound(data.id, name="Fifth")
    p.set_id_table(data.id, ["2", "4", "5"])
    other = p.add_section("data", "More compounds")
    yield p, s, c5, other
    p.close()


def _order(p, section_id):
    return [i.id for i in p.section(section_id).items]


def _ids(p):
    report = p.id_report()
    return {c.id: report.display_id(c.id) for c in p.all_compounds()}


def test_a_failed_move_to_another_section_keeps_the_numbering(two_sections, monkeypatch):
    p, s, c5, other = two_sections
    data = s["data"]
    order, ids = _order(p, data.id), _ids(p)
    monkeypatch.setattr(fileio, "move", held)
    with pytest.raises(PermissionError):
        p.move_scheme(s["scheme"].id, other.id, 0)
    assert _order(p, data.id) == order and _ids(p) == ids
    with pytest.raises(PermissionError):
        p.move_compound(s["c2"].id, other.id, 0)
    assert _order(p, data.id) == order and _ids(p) == ids
    monkeypatch.undo()
    p.add_compound(data.id, name="New")          # an unrelated save of the section
    again = Project.open(p.folder, read_only=True)
    assert _order(again, data.id)[:3] == order


def test_a_failed_move_within_a_section_keeps_the_numbering(two_sections, monkeypatch):
    p, s, c5, _other = two_sections
    data = s["data"]
    order, ids = _order(p, data.id), _ids(p)
    monkeypatch.setattr(p, "save_section", held)
    with pytest.raises(PermissionError):
        p.move_scheme(s["scheme"].id, data.id, 0)
    assert _order(p, data.id) == order and _ids(p) == ids
    with pytest.raises(PermissionError):
        p.move_compound(c5.id, data.id, 0)
    assert _order(p, data.id) == order and _ids(p) == ids


# ---------------------------------------------------------------------- integ-7: memory

def test_section_commands_that_cannot_be_saved_leave_memory_as_on_disk(project, monkeypatch):
    a = project.add_section("text", "Alpha")
    b = project.add_section("text", "Beta")
    custom = project.add_section("text", "Group")
    top = [x.id for x in project.top_sections]
    monkeypatch.setattr(project, "save_section", held)
    with pytest.raises(PermissionError):
        project.rename_section(a.id, "Renamed")
    assert project.section(a.id).name == "Alpha" and a.data["name"] == "Alpha"
    monkeypatch.setattr(project, "save_settings", held)
    with pytest.raises(PermissionError):
        project.move_section(a.id, 2)
    assert [x.id for x in project.top_sections] == top
    with pytest.raises(PermissionError):
        project.move_section(b.id, 0, custom.id)
    assert [x.id for x in project.top_sections] == top and not custom.children
    assert project.section(b.id).parent_id is None
    with pytest.raises(PermissionError):
        project.set_order(list(reversed(top)))
    assert [x.id for x in project.top_sections] == top


def test_a_scheme_change_that_cannot_be_saved_is_not_written_later(window, sample, warnings,
                                                                   qapp):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    page = win.panel
    editor = page.editor
    win.project.save_scheme = held
    assert editor.act("Add a step to", lambda: se.add_step(win.project, editor.scheme)) \
        is False
    assert [t for t, _ in warnings] == ["Could not change the scheme"]
    assert len(se.steps(win.project.scheme(kid))) == 1       # (taken back at once)
    for _ in range(3):
        qapp.processEvents()                     # (the project is read back from its files)
    assert win.panel is not page and not win.project.read_only
    del win.project.save_scheme
    editor = win.panel.editor
    step_id = se.steps(editor.scheme)[0]["id"]
    assert editor.act("Change", lambda: se.set_conditions(win.project, editor.scheme,
                                                           step_id, "bottom", "THF"))
    on_disk = scheme_on_disk(win.project.folder, kid)
    assert len(se.steps(on_disk)) == 1 and se.steps(on_disk)[0]["top"] == "NaBH4"


def test_a_scheme_change_that_failed_part_way_is_read_back(window, sample, warnings, qapp):
    """Marking a box as a compound writes the new compound's folder, then scheme.json:
    if only the first worked, memory must match the files afterwards."""
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    editor = win.panel.editor
    box = se.starting(editor.scheme)[0]
    win.project.save_scheme = held
    editor.mark_compound(box["id"])
    for _ in range(3):
        qapp.processEvents()
    del win.project.save_scheme
    on_disk = Project.open(win.project.folder, read_only=True)
    assert {c.id for c in win.project.all_compounds()} == \
        {c.id for c in on_disk.all_compounds()}
    assert [c.id for c in win.project.scheme(kid).children] == \
        [c.id for c in on_disk.scheme(kid).children]


# ---------------------------------------------------------------------- integ-8: box copy

def _fail_copies_from_trash(monkeypatch):
    real = fileio.copy_file

    def copy_file(source, dest, *a, **k):
        if "trash" in Path(source).parts:
            raise PermissionError(13, "held by another program", str(source))
        return real(source, dest, *a, **k)
    monkeypatch.setattr(fileio, "copy_file", copy_file)


def test_a_delete_whose_box_copy_fails_is_done_and_says_so(sample, monkeypatch):
    p, s = sample
    p.enable_undo()
    scheme, c4 = s["scheme"], s["c4"]
    files = scheme.folder / se.FILES_DIR
    before = sorted(files.iterdir()) if files.is_dir() else []
    _fail_copies_from_trash(monkeypatch)
    with p.undo_step("Delete compound 4"):
        with pytest.raises(BoxCopyFailed) as e:
            p.delete_compound(c4.id)
    assert e.value.entry is not None and "Edit ▸ Undo" in str(e.value)
    assert any(t.id == e.value.entry.id for t in p.list_trash())
    assert c4.id not in [c.id for c in scheme.children]
    on_disk = Project.open(p.folder, read_only=True).scheme(scheme.id)
    assert not any(b.get("compound") for b, _w in se.all_boxes(on_disk))
    assert (sorted(files.iterdir()) if files.is_dir() else []) == before   # (no half copy)
    monkeypatch.undo()
    p.undo()
    linked = [b.get("compound") for b, _w in se.all_boxes(p.scheme(scheme.id))]
    assert c4.id in linked


def test_a_move_whose_box_copy_fails_leaves_the_box_linked(sample, monkeypatch):
    p, s = sample
    scheme, c4 = s["scheme"], s["c4"]
    real = fileio.copy_file

    def copy_file(source, dest, *a, **k):
        raise PermissionError(13, "held by another program", str(source))
    monkeypatch.setattr(fileio, "copy_file", copy_file)
    with pytest.raises(PermissionError):
        p.move_compound(c4.id, s["data"].id, 0)
    monkeypatch.setattr(fileio, "copy_file", real)
    assert any(b.get("compound") == c4.id for b, _w in se.all_boxes(scheme))   # as on disk


def test_deleting_from_the_tree_when_the_box_copy_fails(window, sample, warnings,
                                                        monkeypatch):
    p, s = sample
    win = window(p)
    cid = s["c4"].id
    win.select("compound", cid)
    _fail_copies_from_trash(monkeypatch)
    win.delete_item()
    assert [t for t, _ in warnings] == ["Scheme box left empty"]
    assert win.statusBar().currentMessage() == "Moved to Trash"
    with pytest.raises(KeyError):
        win.project.compound(cid)


# ---------------------------------------------------------------------- integ-9: text files

def test_a_text_changed_outside_the_program_is_checked_again(window, short_tmp, monkeypatch):
    from si_assistant.core import checks
    from tests.test_audit_main import compound_labels, small_project, type_manual_id
    p, d = small_project(short_tmp / "p")
    calls = []
    real = checks.all_warnings

    def counted(project, compound, *args):
        calls.append(compound.id)
        return real(project, compound, *args)
    monkeypatch.setattr(checks, "all_warnings", counted)
    win = window(p)
    a, b = d["a"].id, d["b"].id
    win.select("compound", a)
    type_manual_id(win.panel, "7")
    win.panel.save()
    path = win.project.compound_text_path(win.project.compound(b), "procedure")
    path.write_text("As before.@cite{r-gone}", encoding="utf-8")   # (e.g. a OneDrive sync)
    calls.clear()
    type_manual_id(win.panel, "8")
    win.panel.save()
    assert set(calls) == {a, b}
    partial = compound_labels(win)
    win._refresh_labels()
    assert compound_labels(win) == partial
    assert "References list" in partial[b][1]
    calls.clear()
    type_manual_id(win.panel, "9")
    win.panel.save()
    assert set(calls) == {a}                     # (nothing else changed: kept)


# ---------------------------------------------------------------------- integ-10: read back

def test_leaving_anyway_with_a_new_conflict_copy_stays_writable(window, sample, warnings,
                                                                infos, asked):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    page = win.panel
    page.override_edit.setText("77")
    page.override_edit.textEdited.emit("77")
    win.project.save_compound = held
    folder = win.project.compound(cid).folder
    shutil.copy2(folder / "compound.json", folder / "compound-LAPTOP.json")
    asked.answer["value"] = QMessageBox.StandardButton.Yes
    win.select("section", s["notes"].id)
    assert asked.titles() == ["Changes not saved"] and win.panel is not page
    assert not win.project.read_only
    assert "Project now read-only" not in [t for t, _ in warnings]
    assert len(infos) == 1 and "compound-LAPTOP.json" in infos[0][1]


def test_leaving_anyway_with_a_newly_unreadable_file_is_read_only(window, sample, warnings,
                                                                  asked):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    page = win.panel
    page.override_edit.setText("77")
    page.override_edit.textEdited.emit("77")
    win.project.save_compound = held
    (win.project.compound(s["c4"].id).folder / "compound.json").write_text(
        "{not json", encoding="utf-8")
    asked.answer["value"] = QMessageBox.StandardButton.Yes
    win.select("section", s["notes"].id)
    assert win.project.read_only
    assert "Project now read-only" in [t for t, _ in warnings]


# ---------------------------------------------------------------------- integ-11: compile

def _unreadable_structure(p, c):
    path = p.structure_path(c)
    path.rename(path.with_name("structure.mol.bak"))
    path.mkdir()   # (a folder in its place: it is there, but can't be read)
    p._structure_cache.clear()


def test_an_unreadable_structure_is_left_out_with_a_warning(qapp, sample):
    from si_assistant.compile.document import CompileOptions, build
    from si_assistant.compile.qt_images import convert_to_png, image_info
    p, s = sample
    opts = CompileOptions.from_settings(p.settings)
    opts.structures = "acs"
    before = build(p, opts, image_info, convert_to_png, name="before")
    calcd = before.typ_path.read_text(encoding="utf-8").count("calcd for")
    _unreadable_structure(p, s["c2"])
    result = build(p, opts, image_info, convert_to_png, name="after")   # (no OSError)
    text = result.typ_path.read_text(encoding="utf-8")
    notes = [w for w in result.warnings if "could not be read just now" in w]
    assert len(notes) == 1 and "structure file" in notes[0]
    assert text.count("calcd for") == calcd - 1   # (compound 2's HRMS: found only)
    p.close()


def test_an_unreadable_structure_does_not_stop_an_hrms_peaks_table(qapp, sample, short_tmp):
    import numpy as np
    from si_assistant.compile import document as doc
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import image_info
    from tests import ms_samples
    p, s = sample
    peaks = np.array(ms_samples.sample_peaks())
    path = short_tmp / "2.mzML"
    path.write_bytes(ms_samples.mzml([(peaks[:, 0], peaks[:, 1])]))
    _unreadable_structure(p, s["c2"])
    b = doc.Builder(p, CompileOptions.from_settings(p.settings), image_info)
    table = b.hrms_peaks_table(s["c2"], "2.mzML", path)
    assert table and any("could not be read just now" in w for w in b.warnings)
    p.close()


# ---------------------------------------------------------------------- integ-12: Trash

def test_files_kept_after_an_unfinished_undo_are_named_and_listed(project, monkeypatch):
    from si_assistant.core import undo
    from si_assistant.ui.dialogs import TrashDialog
    monkeypatch.setattr(fileio, "_REPLACE_DELAY_S", 0)
    monkeypatch.setattr(undo, "_RENAME_DELAY_S", 0)
    p = project
    p.enable_undo()
    sec = p.add_section("data", "Products")
    c = p.add_compound(sec.id, name="Ester")
    with p.undo_step("Edit compound"):
        c.values["name"]["text"] = "Ethyl ester"
        p.save_compound(c)
        p.write_compound_text(c, "procedure", "Newer text.")
    real_replace, real_rename = os.replace, undo._rename

    def blocked_replace(src, dst):
        if Path(dst).name == "compound.json":
            raise PermissionError(13, "in use (test)", str(dst))
        return real_replace(src, dst)

    def blocked_rename(source, target):
        if Path(source).parent.parent == p.journal.folder:
            raise PermissionError(13, "in use (test)", str(source))
        return real_rename(source, target)
    monkeypatch.setattr(undo.os, "replace", blocked_replace)
    monkeypatch.setattr(undo, "_rename", blocked_rename)
    with pytest.raises(ProjectError) as e:
        p.undo()
    assert "press Restore to see the list" in str(e.value)
    monkeypatch.undo()
    entry = next(t for t in p.list_trash() if "Newer files kept" in t.name)
    assert TrashDialog._row_texts(entry)[1] == "Files kept after an unfinished undo"
    before = sorted(x.relative_to(entry.folder) for x in entry.folder.rglob("*"))
    with pytest.raises(ProjectError) as e:
        p.restore_from_trash(entry.id)
    stored = next(iter(entry.info["files"]))
    assert "procedure.txt" in str(e.value) and stored in str(e.value)
    assert sorted(x.relative_to(entry.folder) for x in entry.folder.rglob("*")) == before
    p.journal.close()
    p.journal = None


def test_an_unfinished_deletion_without_a_list_keeps_its_label():
    from si_assistant.ui.dialogs import TrashDialog
    entry = types.SimpleNamespace(kind="incomplete", name="x", info={}, deleted_at="",
                                  deleted_by="")
    assert TrashDialog._row_texts(entry)[1] == "Unfinished deletion (no description)"


# ---------------------------------------------------------------------- integ-13: dismissed.json

@pytest.fixture
def si(short_tmp):
    from tests import manuscript_samples as S
    p, _c = S.build_si(short_tmp / "si")
    S.make_pdf(short_tmp / "ms.pdf")
    M.add_file(p, short_tmp / "ms.pdf")
    problems = M.check(p, M.read_all(p)).problems
    yield p, problems
    p.close()


def test_each_damaged_dismissed_json_is_kept(si, monkeypatch):
    import si_assistant.core.manuscript as manuscript
    p, problems = si
    path = M.folder(p) / M.DISMISSED_FILE
    stamps = iter(["2026-10-09_100000", "2026-10-09_100000"])   # (the same second)

    class Clock:
        @staticmethod
        def now():
            return types.SimpleNamespace(strftime=lambda _f: next(stamps),
                                         isoformat=lambda **k: "2026-10-09T10:00:00")
    monkeypatch.setattr(manuscript, "datetime", Clock)
    path.write_text("{first damaged", encoding="utf-8")
    M.dismiss(p, problems[0], "one")
    path.write_text("{second damaged", encoding="utf-8")
    M.dismiss(p, problems[0], "two")
    kept = sorted(f.read_text(encoding="utf-8")
                  for f in M.folder(p).glob("dismissed.damaged-*.json"))
    assert kept == ["{first damaged", "{second damaged"]
    assert [f.name for f in M.files(p)] == ["ms.pdf"]   # (the copies are not listed)


def test_a_damaged_dismissed_json_that_cannot_be_copied_is_not_written_over(si, monkeypatch):
    p, problems = si
    path = M.folder(p) / M.DISMISSED_FILE
    path.write_text("{damaged", encoding="utf-8")
    monkeypatch.setattr(fileio, "copy_file", held)
    with pytest.raises(OSError):
        M.dismiss(p, problems[0], "one")
    assert path.read_text(encoding="utf-8") == "{damaged"


def test_a_users_file_never_takes_a_kept_copys_name(si, short_tmp):
    p, _problems = si
    source = short_tmp / "dismissed.damaged-notes.json"
    source.write_text("{}", encoding="utf-8")
    dest = M.add_file(p, source)
    assert dest.name == "dismissed_damaged-notes.json"
    assert dest.name in [f.name for f in M.files(p)]     # (listed, not hidden)


# ---------------------------------------------------------------------- review of the fixes

def _boxes(scheme):
    return [(b.get("compound"), b.get("structure")) for b, _w in se.all_boxes(scheme)]


def _scheme_files(scheme):
    folder = scheme.folder / se.FILES_DIR
    return sorted(x.name for x in folder.iterdir()) if folder.is_dir() else []


def test_a_move_out_of_a_scheme_with_scheme_json_held_leaves_memory_as_on_disk(
        sample, monkeypatch):
    p, s = sample
    scheme, c4 = s["scheme"], s["c4"]
    files = _scheme_files(scheme)
    monkeypatch.setattr(p, "save_scheme", held)
    with pytest.raises(PermissionError):
        p.move_compound(c4.id, s["data"].id, 0)
    monkeypatch.undo()
    on_disk = Project.open(p.folder, read_only=True).scheme(scheme.id)
    assert _boxes(scheme) == _boxes(on_disk)             # still linked, as in scheme.json
    assert c4.id in [b for b, _s in _boxes(scheme)]
    assert c4 in scheme.children
    assert _scheme_files(scheme) == files                 # (the copy made is removed)
    # The next save of the scheme doesn't write the box unlinked unseen.
    se.set_conditions(p, scheme, se.steps(scheme)[0]["id"], "top", "NaBH4 (2 equiv)")
    on_disk = Project.open(p.folder, read_only=True).scheme(scheme.id)
    assert c4.id in [b for b, _s in _boxes(on_disk)]
    # Once the file is free, the move works and the box keeps a copy of the structure.
    p.move_compound(c4.id, s["data"].id, 0)
    assert c4 not in scheme.children and not any(b for b, _s in _boxes(scheme))
    assert all(st for _b, st in _boxes(scheme))


def test_a_delete_whose_scheme_save_fails_after_the_move_says_so(sample, monkeypatch):
    """scheme.json becomes held between the first save and the last: the compound is in the
    Trash, the box stays linked in memory as in the file (showing nothing), no copy is
    left behind, and the message says the box is empty (as it is)."""
    p, s = sample
    scheme, c4 = s["scheme"], s["c4"]
    files = _scheme_files(scheme)
    real, calls = p.save_scheme, []

    def second_fails(k):
        calls.append(k)
        if len(calls) == 2:
            raise PermissionError(13, "held by another program")
        return real(k)
    monkeypatch.setattr(p, "save_scheme", second_fails)
    with pytest.raises(BoxCopyFailed) as e:
        p.delete_compound(c4.id)
    monkeypatch.undo()
    assert "now empty" in str(e.value)
    assert any(t.id == e.value.entry.id for t in p.list_trash())
    assert c4 not in scheme.children
    on_disk = Project.open(p.folder, read_only=True).scheme(scheme.id)
    assert _boxes(scheme) == _boxes(on_disk)
    assert _scheme_files(scheme) == files
    product = se.steps(scheme)[0]["products"][0]
    assert se.box_view(p, scheme, product).empty


def test_a_move_of_a_scheme_to_another_section_that_cannot_be_saved_keeps_it(
        two_sections, monkeypatch):
    p, s, _c5, other = two_sections
    kid = s["scheme"].id
    monkeypatch.setattr(p, "save_section", held)
    with pytest.raises(PermissionError):
        p.move_scheme(kid, other.id, 0)
    monkeypatch.undo()
    scheme = p.scheme(kid)                     # (not dropped from memory)
    assert scheme.section_id == other.id and _order(p, other.id) == [kid]
    assert kid not in _order(p, s["data"].id)
    assert all(c.section_id == other.id for c in scheme.children)
    new = p.add_compound(other.id, name="New")   # an unrelated save of the section
    again = Project.open(p.folder, read_only=True)
    assert _order(again, other.id) == [kid, new.id]   # (the scheme keeps its place)
    assert [c.id for c in again.scheme(kid).children] == [c.id for c in scheme.children]


def _picture_for(p, c):
    from si_assistant.core import chem
    p.set_structure_image(c, data=chem.structure_png(chem.parse_structure(
        "CC(=O)N(C)Cc1ccccc1"), 300, 200), ext=".png")


def test_an_unreadable_structure_note_says_what_was_left_out(qapp, sample):
    from si_assistant.compile.document import CompileOptions, build
    from si_assistant.compile.qt_images import convert_to_png, image_info
    p, s = sample
    opts = CompileOptions.from_settings(p.settings)
    opts.structures = "acs"
    _picture_for(p, s["c2"])                   # 2: printed from its picture instead
    _unreadable_structure(p, s["c2"])
    _unreadable_structure(p, s["c4"])          # 4: drawn by the scheme, from its file
    result = build(p, opts, image_info, convert_to_png, name="notes")
    notes = [w for w in result.warnings if "could not be read just now" in w]
    assert len(notes) == 2
    two = next(w for w in notes if w.startswith("2 "))
    four = next(w for w in notes if w.startswith("4 "))
    assert "calculated formula and masses were left out" in two and "drawing" not in two
    assert "structure drawing, calculated formula and masses were left out" in four
    p.close()


def test_choosing_another_spectrum_while_its_caption_waits_asks(window, sample, warnings,
                                                                 asked):
    p, s = sample
    win = window(p)
    win.select("spectra_page", s["c2"].id)
    page = win.panel
    assert page.list.count() >= 2 and page.list.currentRow() == 0
    page.caption_edit.setText("Major rotamer")
    win.project.save_compound = held
    page.caption_edit.editingFinished.emit()
    assert [t for t, _ in warnings] == ["Could not save"]
    page.list.setCurrentRow(1)                 # No: stays, and the typing is kept
    assert asked.titles() == ["Changes not saved"]
    assert page.list.currentRow() == 0 and page.caption_edit.text() == "Major rotamer"
    assert page._text_waits()
    # The order of the spectra changes (read again): the typing is still waiting.
    page._reload_spectra()
    assert page.caption_edit.text() == "Major rotamer" and page._text_waits()
    asked.answer["value"] = QMessageBox.StandardButton.Yes
    page.list.setCurrentRow(1)                 # Yes: the other spectrum, the typing dropped
    assert asked.titles() == ["Changes not saved"] * 2
    assert page.list.currentRow() == 1 and not page._text_waits()
    assert [t for t, _ in warnings] == ["Could not save"]


def test_an_editor_change_while_the_scheme_name_waits_changes_nothing(window, sample,
                                                                      warnings, qapp):
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    page = win.panel
    page.name_edit.setText("My new name")
    win.project.save_scheme = held
    page.name_edit.editingFinished.emit()
    assert [t for t, _ in warnings] == ["Could not save the name"]
    page.editor.add_step()
    for _ in range(3):
        qapp.processEvents()
    assert page.editor.blocked and "could not be saved yet" in page.editor.status.text()
    assert len(se.steps(win.project.scheme(kid))) == 1
    assert win.panel is page and page.name_edit.text() == "My new name"   # (kept)
    assert [t for t, _ in warnings] == ["Could not save the name"]
    del win.project.save_scheme
    page.editor.add_step()                     # the file is free: both are saved
    assert len(se.steps(win.project.scheme(kid))) == 2
    assert win.project.scheme(kid).name == "My new name"


def test_a_failed_scheme_name_is_said_once_when_leaving(window, sample, warnings, asked):
    p, s = sample
    win = window(p)
    win.select("scheme", s["scheme"].id)
    page = win.panel
    page.name_edit.setText("My new name")
    win.project.save_scheme = held
    page.name_edit.editingFinished.emit()
    assert [t for t, _ in warnings] == ["Could not save the name"]
    win.select("compound", s["c2"].id)
    assert [t for t, _ in warnings] == ["Could not save the name"]   # (not said again)
    assert asked.titles() == ["Changes not saved"] and win.panel is page
    page.name_edit.setText("Another name")     # typed again: a new failure is said
    win.select("compound", s["c2"].id)
    assert [t for t, _ in warnings] == ["Could not save the name", "Could not save"]


def test_failed_conditions_are_said_once_when_leaving(window, sample, warnings, asked):
    p, s = sample
    win = window(p)
    win.select("scheme", s["scheme"].id)
    page = win.panel
    step = page.editor.steps[0]
    type_conditions(step, "NaBH4 (3 equiv)")
    win.project.save_scheme = held
    step._autosave(step.top)
    assert [t for t, _ in warnings] == ["Could not save"]
    win.select("compound", s["c2"].id)
    assert [t for t, _ in warnings] == ["Could not save"]
    assert asked.titles() == ["Changes not saved"] and win.panel is page
