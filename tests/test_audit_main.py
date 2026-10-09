"""Audit fixes in the main window, the Warnings panel, the live preview and restarting.

Everything here is made up (the sample project of tests/sample_project.py, or small
projects built in a temporary folder)."""

import os
import time
import types
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox, QWidget

from si_assistant.core import checks
from si_assistant.core import lock as lockmod
from si_assistant.core.project import Project
from tests.sample_project import FACTS, build_sample


@pytest.fixture
def warnings(monkeypatch):
    """The titles and texts of QMessageBox.warning calls (none is shown)."""
    seen = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _parent, title, text, *a, **k: seen.append((title, text))))
    return seen


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, warnings):
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


def wait_until(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def type_manual_id(panel, text="99"):
    panel.override_edit.setText(text)
    panel.override_edit.textEdited.emit(text)   # (as typing does: the autosave waits)


# ---------------------------------------------------------------------- renaming in the tree

def test_renaming_a_scheme_compound_survives_its_next_autosave(window, sample, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    win = window(p)
    cid = s["c4"].id
    win.select("compound", cid)
    assert isinstance(win.panel, mw.SchemePanel)
    monkeypatch.setattr(mw.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Renamed alcohol", True)))
    win.rename_item()
    tab = win.panel.compound_panel
    box = next(b for b in tab.boxes if b.defn.key == "name")
    assert box.editor.widgets["text"].text() == "Renamed alcohol"   # the page shows it
    tab._schedule_save()
    tab.flush()                       # an autosave of that tab (e.g. after typing a yield)
    assert win.project.compound(cid).name == "Renamed alcohol"
    assert Project.open(p.folder).compound(cid).name == "Renamed alcohol"


def test_renaming_a_scheme_page_from_the_tree_sticks_and_undoes(window, sample, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    win = window(p)
    kid = s["scheme"].id
    win.select("scheme", kid)
    monkeypatch.setattr(mw.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("New scheme name", True)))
    win.rename_item()
    assert win.project.scheme(kid).name == "New scheme name"
    assert Project.open(p.folder).scheme(kid).name == "New scheme name"
    assert win._find_item("scheme", kid).text(0) == "New scheme name"
    assert win.panel.name_edit.text() == "New scheme name"
    assert win.act_undo.text().startswith("&Undo Rename scheme page")
    win.undo()
    assert win.project.scheme(kid).name == FACTS["scheme_caption"]
    assert win.panel.name_edit.text() == FACTS["scheme_caption"]
    assert not win.act_undo.isEnabled()


# ---------------------------------------------------------------------- losing the lock

def test_a_vanished_lock_is_not_taken_back(window, sample, warnings):
    """Someone took the project over on another computer, changed it and closed it (their
    lock is gone too): this window must not go on saving its old copy."""
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    laptop = Project.open(p.folder)
    laptop.acquire_lock("Tester")
    c = laptop.compound(cid)
    c.values["name"]["text"] = "EDITED ON LAPTOP"
    laptop.save_compound(c)
    laptop.close()
    assert not (p.folder / lockmod.LOCK_NAME).exists()
    win._check_lock()
    assert win.project.read_only and win.project.journal is None
    assert not win.act_undo.isEnabled()
    assert not (p.folder / lockmod.LOCK_NAME).exists()          # not claimed again
    assert [t for t, _ in warnings] == ["Project closed elsewhere"]
    assert win.panel.override_edit.isReadOnly()
    win.panel.save()                                              # (nothing is written)
    win.close_project()
    assert Project.open(p.folder).compound(cid).name == "EDITED ON LAPTOP"


def test_a_moved_project_folder_makes_the_window_read_only(window, sample, warnings):
    p, s = sample
    win = window(p)
    old = p.folder
    try:
        os.rename(old, old.with_name("Moved SI"))
    except OSError:
        pytest.skip("the folder can't be renamed while open on this computer")
    win._check_lock()
    assert win.project.read_only and win.project.journal is None
    assert not old.exists()                     # no "ghost" folder with a new lock
    assert [t for t, _ in warnings] == ["Project folder moved"]
    assert "new folder at the old place" not in warnings[0][1]
    win.close_project()
    assert not old.exists()


def test_a_drive_that_is_gone_for_a_moment_is_not_a_moved_folder(window, tmp_path, warnings):
    """A USB stick unplugged or a network drive dropped: the project's files look missing,
    but so does the folder around it. The check is simply made again later."""
    p, d = small_project(tmp_path / "drive")
    win = window(p)
    drive = tmp_path / "drive"
    try:
        os.rename(drive, tmp_path / "away")      # (the whole drive is not there)
    except OSError:
        pytest.skip("the folder can't be renamed while open on this computer")
    win._check_lock()
    assert not win.project.read_only and win._lock_timer.isActive()
    assert warnings == []
    os.rename(tmp_path / "away", drive)          # back again
    win._check_lock()
    assert not win.project.read_only and warnings == []
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "5")
    win.panel.save()
    assert Project.open(p.folder).compound(d["a"].id).id_override == "5"


def test_typing_whose_save_failed_is_kept_when_the_folder_moves(window, sample, warnings):
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    panel = win.panel
    type_manual_id(panel, "77")
    try:
        os.rename(p.folder, p.folder.with_name("Moved SI"))
    except OSError:
        pytest.skip("the folder can't be renamed while open on this computer")
    panel.save()                                   # fails: the folder is not there
    assert panel._dirty and not panel._timer.isActive()
    win._check_lock()
    assert win.panel is panel and panel.override_edit.text() == "77"
    assert panel.override_edit.isReadOnly()
    [(title, text)] = warnings
    assert title == "Project folder moved" and "may not have been saved" in text


def test_typing_whose_save_failed_is_kept_when_taken_over(window, sample, warnings):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    panel = win.panel

    def held(_compound):
        raise PermissionError(13, "held by another program")
    win.project.save_compound = held
    type_manual_id(panel, "77")
    panel.save()                                   # fails (e.g. OneDrive has the file)
    del win.project.save_compound
    Project.open(p.folder).acquire_lock("Bob")
    win._check_lock()
    assert win.panel is panel and panel.override_edit.text() == "77"
    [(title, text)] = warnings
    assert title == "Project taken over" and "may not have been saved" in text
    assert "already saved" not in text
    assert Project.open(p.folder).compound(cid).id_override == ""


def test_closing_after_a_failed_save_and_a_takeover_says_the_typing_was_lost(
        window, sample, warnings):
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    panel = win.panel
    win.project.save_compound = lambda _c: (_ for _ in ()).throw(PermissionError(13, "held"))
    type_manual_id(panel, "77")
    panel.save()                                   # fails
    del win.project.save_compound
    Project.open(p.folder).acquire_lock("Bob")
    win.close_project()
    [(title, text)] = warnings
    assert title == "Project taken over" and "not saved" in text


def test_a_takeover_while_a_dialog_is_open_waits_for_it(qapp, window, sample, warnings):
    """Rebuilding the page inside the dialog's event loop would delete the page and the
    dialog on it while still in use (a crash with a message box)."""
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    panel = win.panel
    dlg = QDialog(panel)
    seen = {}

    def take_over():
        assert win.act_delete.isEnabled()
        Project.open(p.folder).acquire_lock("Bob")
        win._check_lock()
        seen["read_only"] = win.project.read_only
        seen["warned"] = list(warnings)
        seen["delete"] = win.act_delete.isEnabled()
        QTimer.singleShot(50, dlg.reject)
    QTimer.singleShot(50, take_over)
    dlg.exec()
    assert shiboken6.isValid(panel) and shiboken6.isValid(dlg)
    # Writes stop at once, and Delete, Move… are greyed (a menu may be open meanwhile).
    assert seen == {"read_only": True, "warned": [], "delete": False}
    assert wait_until(qapp, lambda: warnings)                  # then, after the dialog
    assert [t for t, _ in warnings] == ["Project taken over"]
    assert win.panel is not panel and win.panel.override_edit.isReadOnly()


def test_a_takeover_keeps_unsaved_typing_on_show_and_says_so(window, sample, warnings):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    panel = win.panel
    type_manual_id(panel)
    Project.open(p.folder).acquire_lock("Bob")
    win._check_lock()
    assert win.panel is panel                        # kept, so the text can be copied
    assert panel.override_edit.text() == "99" and panel.override_edit.isReadOnly()
    [(title, text)] = warnings
    assert title == "Project taken over" and "may not have been saved" in text
    assert "already saved" not in text
    assert Project.open(p.folder).compound(cid).id_override == ""


def test_a_takeover_with_nothing_pending_rebuilds_the_page(window, sample, warnings):
    p, s = sample
    win = window(p)
    win.select("compound", s["c4"].id)           # a compound of a scheme page
    panel = win.panel
    Project.open(p.folder).acquire_lock("Bob")
    win._check_lock()
    assert win.panel is not panel                  # (a kept scheme page stayed editable)
    assert win.panel.compound_panel.override_edit.isReadOnly()
    [(title, text)] = warnings
    assert title == "Project taken over" and "already saved" in text


def test_closing_after_a_takeover_does_not_write_over_their_files(window, sample, warnings):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    other = Project.open(p.folder)
    other.acquire_lock("Other")
    c = other.compound(cid)
    c.values["name"]["text"] = "NAME BY OTHER"
    other.save_compound(c)
    type_manual_id(win.panel)                   # before the 20-second lock check
    win.close_project()
    again = Project.open(p.folder)
    assert again.compound(cid).name == "NAME BY OTHER"
    assert again.compound(cid).id_override == ""
    assert [t for t, _ in warnings] == ["Project taken over"]
    assert "not saved" in warnings[0][1]
    assert lockmod.read_lock(p.folder).user == "Other"


def test_closing_normally_still_saves_the_typing(window, sample, warnings):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    type_manual_id(win.panel)
    win.close_project()
    assert Project.open(p.folder).compound(cid).id_override == "99"
    assert warnings == []


def test_a_restart_after_a_takeover_reopens_read_only(window, sample, warnings):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    Project.open(p.folder).acquire_lock("Other")
    type_manual_id(win.panel)
    info = win.restore_info()
    assert info["read_only"] is True
    assert Project.open(p.folder).compound(cid).id_override == ""


def test_compile_after_a_takeover_during_the_dialog_writes_nothing(window, sample, warnings,
                                                                   monkeypatch):
    from si_assistant.ui import compile_ui
    from si_assistant.ui import main_window as mw
    p, s = sample
    win = window(p)

    def exec_(dlg):
        lockmod.write_lock(p.folder, "Someone else")   # (taken over meanwhile)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(compile_ui.CompileDialog, "exec", exec_)
    compiled = []
    monkeypatch.setattr(mw, "run_compile", lambda *a, **k: compiled.append(a))
    win.compile_si()
    assert compiled == []
    assert win.project.read_only
    assert not (p.folder / "build" / "SI.pdf").exists()
    assert [t for t, _ in warnings] == ["Project taken over"]


def test_settings_not_saved_after_a_takeover_during_the_dialog(window, sample, warnings,
                                                               monkeypatch):
    from si_assistant.ui import dialogs
    p, s = sample
    win = window(p)

    def exec_(dlg):
        lockmod.write_lock(p.folder, "Someone else")
        win._check_lock()              # (the 20-second check, while the dialog is open)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialogs.ProjectSettingsDialog, "exec", exec_)
    win.show_settings()
    assert [t for t, _ in warnings] == ["Project taken over", "Settings not saved"]


# ---------------------------------------------------------------------- warnings in the tree

def small_project(folder: Path) -> tuple[Project, dict]:
    """A Data section with three compounds; B cites a reference."""
    p = Project.create(folder, "Small SI")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["1", "2", "3"])
    a = p.add_compound(data.id, name="Alpha")
    b = p.add_compound(data.id, name="Beta")
    c = p.add_compound(data.id, name="Gamma")
    ref = p.add_reference({"title": "A made-up paper", "authors": ["A. Person"],
                           "year": "2020"})
    p.write_compound_text(b, "procedure", f"As before.@cite{{{ref['id']}}}")
    return p, {"a": a, "b": b, "c": c, "ref": ref, "data": data}


def compound_labels(win):
    from si_assistant.ui.main_window import ID_ROLE, KIND_ROLE
    return {item.data(0, ID_ROLE): (item.text(0), item.toolTip(0), item.icon(0).isNull())
            for item in win._all_items() if item.data(0, KIND_ROLE) == "compound"}


@pytest.fixture
def count_checks(monkeypatch):
    calls = []
    real = checks.all_warnings

    def counted(project, compound, *args):
        calls.append(compound.id)
        return real(project, compound, *args)
    monkeypatch.setattr(checks, "all_warnings", counted)
    return calls


def test_a_compound_save_checks_only_that_compound_and_matches_a_full_pass(
        window, tmp_path, count_checks):
    p, d = small_project(tmp_path)
    win = window(p)
    a = d["a"].id
    win.select("compound", a)
    panel = win.panel
    type_manual_id(panel, "7")
    panel.save()                        # (the first save on a page checks everything)
    count_checks.clear()
    type_manual_id(panel, "2")          # A now has the same ID as B: both get a warning
    panel.save()
    # A (the page and the tree), and B, whose ID-report message changed; not C.
    assert set(count_checks) == {a, d["b"].id}
    partial = compound_labels(win)
    win._refresh_labels()               # everything checked again
    assert compound_labels(win) == partial
    assert not partial[d["b"].id][2]    # (B shows the warning icon)


def test_a_change_other_compounds_depend_on_checks_everything_again(
        window, tmp_path, count_checks):
    p, d = small_project(tmp_path)
    win = window(p)
    b = d["b"].id
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "6")
    win.panel.save()
    assert compound_labels(win)[b][2]           # B: no warning yet
    win.project.delete_reference(d["ref"]["id"])  # (e.g. from a Cite list's window)
    count_checks.clear()
    type_manual_id(win.panel, "7")
    win.panel.save()
    assert set(count_checks) == {c.id for c in win.project.all_compounds()}
    assert not compound_labels(win)[b][2]       # B now cites a missing reference
    assert "References list" in compound_labels(win)[b][1]


def test_a_structure_that_could_not_be_read_is_checked_again_next_save(
        window, tmp_path, count_checks):
    """structure.mol online-only while offline, or open in another program, for a moment:
    the warnings worked out without it are not kept for the later saves."""
    p, d = small_project(tmp_path)
    b = d["b"].id
    p.set_structure(d["b"], "CCO")
    win = window(p)
    real = win.project.read_structure
    busy = {"on": True}

    def read_structure(c, strict=False):
        if busy["on"] and c.id == b:
            if strict:
                raise OSError("structure.mol is open in another program")
            return None
        return real(c, strict)
    win.project.read_structure = read_structure
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "7")
    win.panel.save()                       # (checks everything: B's structure unreadable)
    busy["on"] = False
    count_checks.clear()
    type_manual_id(win.panel, "8")
    win.panel.save()
    assert b in count_checks               # checked again, now with its structure
    partial = compound_labels(win)
    win._refresh_labels()
    assert compound_labels(win) == partial
    count_checks.clear()
    type_manual_id(win.panel, "9")
    win.panel.save()
    assert set(count_checks) == {d["a"].id}  # (now kept: only A, the page and the tree)


def test_a_structure_changed_outside_the_program_is_checked_again(
        window, tmp_path, count_checks):
    """e.g. a OneDrive sync, or the file replaced in Explorer."""
    p, d = small_project(tmp_path)
    b = d["b"]
    p.set_structure(b, "CCO")
    win = window(p)
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "7")
    win.panel.save()
    path = win.project.structure_path(win.project.compound(b.id))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    count_checks.clear()
    type_manual_id(win.panel, "8")
    win.panel.save()
    assert set(count_checks) == {d["a"].id, b.id}


def test_showing_another_page_checks_everything_again(window, tmp_path, count_checks):
    p, d = small_project(tmp_path)
    win = window(p)
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "7")
    win.panel.save()
    win.select("compound", d["c"].id)
    count_checks.clear()
    type_manual_id(win.panel, "8")
    win.panel.save()
    assert set(count_checks) == {c.id for c in win.project.all_compounds()}   # everything
    count_checks.clear()
    type_manual_id(win.panel, "9")
    win.panel.save()
    assert set(count_checks) == {d["c"].id}


def test_the_open_warnings_panel_uses_the_trees_results(qapp, window, tmp_path, count_checks):
    p, d = small_project(tmp_path)
    win = window(p)
    win.show()
    win.warnings_dock.setVisible(True)
    qapp.processEvents()
    win.select("compound", d["a"].id)
    type_manual_id(win.panel, "7")
    win.panel.save()
    count_checks.clear()
    type_manual_id(win.panel, "2")
    win.panel.save()
    qapp.processEvents()
    # A (the page and the tree) and B (its ID message changed), not once more for the panel
    assert sorted(count_checks) == sorted([d["a"].id, d["a"].id, d["b"].id])
    assert not win._warnings_timer.isActive()

    def shown():
        tree = win.warnings_panel.tree
        out, stack = [], [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            out.append(item.text(0))
            stack.extend(item.child(j) for j in range(item.childCount()))
        return out, win.warnings_panel.summary.text()
    from_tree = shown()
    win.warnings_panel.refresh()                 # checked again from scratch
    assert shown() == from_tree
    assert any("2" in line for line in from_tree[0])


def test_show_dismissed_checks_each_compound_once(qapp, tmp_path, count_checks):
    from si_assistant.ui.warnings_panel import WarningsPanel
    p, d = small_project(tmp_path)
    b = d["b"]
    p.delete_reference(d["ref"]["id"])
    [w] = [w for w in checks.all_warnings(p, b, p.id_report()) if w.check == "citation"]
    p.dismiss_warning(b, w, "made up")
    panel = WarningsPanel()
    panel.show_dismissed.setChecked(True)
    count_checks.clear()
    panel.set_project(p)
    assert sorted(count_checks) == sorted(c.id for c in p.all_compounds())
    texts = [panel.tree.topLevelItem(0).child(0).child(j).text(0)
             for j in range(panel.tree.topLevelItem(0).child(0).childCount())]
    assert any(t.startswith("✓ dismissed") for t in texts)
    p.close()


def test_saving_a_scheme_compound_relabels_without_rebuilding_the_tree(window, sample):
    p, s = sample
    win = window(p)
    win.select("compound", s["c4"].id)
    section_item = win._find_item("section", s["data"].id)
    tab = win.panel.compound_panel
    type_manual_id(tab, "X9")
    tab.save()
    assert win._find_item("section", s["data"].id) is section_item     # not rebuilt
    item = win._find_item("compound", s["c4"].id)
    assert item.text(0).startswith("X9") and win.tree.currentItem() is item
    # Adding a compound to the scheme page still rebuilds it.
    win.panel.add_compound()
    assert win._find_item("section", s["data"].id) is not section_item
    assert len(win._find_item("scheme", s["scheme"].id).takeChildren()) == 2


# ---------------------------------------------------------------------- other fixes

def test_delete_question_reads_every_text_once(window, tmp_path, monkeypatch):
    from si_assistant.core import links
    p, d = small_project(tmp_path)
    p.write_compound_text(d["c"], "procedure", f"From @{{{d['a'].id}}} and @{{{d['b'].id}}}.")
    p.write_compound_text(d["b"], "procedure", f"From @{{{d['a'].id}}}.")
    win = window(p)
    expected = []
    for cid in (d["a"].id, d["b"].id, d["c"].id):
        expected += [w for w in links.mention_uses(win.project, cid) if w not in expected]
    passes = []
    real = links.all_texts
    monkeypatch.setattr(links, "all_texts", lambda project: passes.append(1) or real(project))
    note = win._mentions_of("section", d["data"].id)
    assert len(passes) == 1
    assert all(w in note for w in expected) and len(expected) == 2
    assert note.index(expected[0]) < note.index(expected[1])


def test_the_table_of_contents_preview_builds_in_its_own_folder(qapp, project, monkeypatch):
    from si_assistant.ui import live_preview as lp
    folders = []

    def build(*a, folder=None, **k):
        folders.append(folder)
        raise RuntimeError("stop here")
    monkeypatch.setattr(lp, "build", build)
    pane = lp.LivePreviewPane()
    pane.set_project(project)
    pane.set_target(("front", "toc"))
    pane._start()
    pane.set_target(("front", "cover"))
    pane._start()
    assert folders[0].name == lp.TOC_FOLDER and folders[0].parent == folders[1]
    pane.shutdown()


def test_right_click_menus_are_freed(window, sample, monkeypatch):
    from si_assistant.ui import main_window as mw

    class QuickMenu(QMenu):
        def exec(self, *_a):   # the user closes it at once
            return None
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)
    monkeypatch.setattr(mw, "QMenu", QuickMenu)
    pos = win.tree.visualItemRect(win._find_item("compound", s["c2"].id)).center()

    def menus():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return len(win.findChildren(QMenu))
    before = menus()
    for _ in range(5):
        win._tree_menu(pos)
    assert menus() == before
    assert len(win.new_sub_menu.actions()) == 3


def test_the_user_guide_window_is_shown_again_after_closing(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import help_ui
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    made = []

    def open_guide(parent):
        made.append(QWidget(parent, mw.Qt.WindowType.Window))
        made[-1].show()
        return made[-1]
    monkeypatch.setattr(help_ui, "open_user_guide", open_guide)
    win = mw.MainWindow()
    win.act_user_guide.trigger()
    made[0].close()
    win.act_user_guide.trigger()
    assert len(made) == 1 and win.guide_window is made[0] and made[0].isVisible()
    made[0].showMinimized()
    win.act_user_guide.trigger()
    assert not made[0].isMinimized()
    win.close()


def test_closing_the_project_lets_go_of_the_preview_pdf(window, sample):
    from si_assistant.ui.compile_ui import PreviewWindow
    from tests.sample_project import fake_spectrum_pdf
    p, s = sample
    win = window(p)
    (p.folder / "build").mkdir(exist_ok=True)
    pdf = fake_spectrum_pdf(p.folder / "build" / "preview.pdf", "Preview")
    win._preview = PreviewWindow(win)
    win._preview.show_pdf(pdf, "a page")
    assert win._preview.isVisible()
    win.close_project()
    assert not win._preview.isVisible()
    pdf.unlink()            # (Windows refuses while the window still has it open)


def test_the_mac_delete_key_deletes_in_the_tree(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(mw, "sys", types.SimpleNamespace(platform="darwin"))
    win = mw.MainWindow()
    keys = [k.toString() for k in win.act_delete.shortcuts()]
    assert keys[0] == "Ctrl+Backspace" and "Backspace" in keys and "Del" in keys
    win.close()
    monkeypatch.setattr(mw, "sys", types.SimpleNamespace(platform="win32"))
    win = mw.MainWindow()
    assert [k.toString() for k in win.act_delete.shortcuts()] == ["Del"]
    win.close()


def test_ampersands_in_names_show_in_menus(window, sample, monkeypatch):
    from si_assistant.ui import main_window as mw
    assert mw.menu_text("Synthesis & Characterization") == "Synthesis && Characterization"
    assert QKeySequence.mnemonic(mw.menu_text("R&D")).isEmpty()
    p, s = sample
    p.add_section("data", "Synthesis & Characterization")
    win = window(p)
    shown = []

    class QuickMenu(QMenu):
        def exec(self, *_a):
            shown.append([a.text() for m in [self, *self.findChildren(QMenu)]
                          for a in m.actions()])
            return None
    monkeypatch.setattr(mw, "QMenu", QuickMenu)
    win.select("compound", s["c2"].id)
    win._tree_menu(win.tree.visualItemRect(win._find_item("compound", s["c2"].id)).center())
    assert "Synthesis && Characterization" in shown[0]
    win.welcome.refresh()
    assert win.welcome.last_btn.text() == f"Open “{mw.menu_text(p.folder.name)}”"


@pytest.mark.parametrize("platform, inside, expected", [
    ("linux", True, "image"), ("linux", False, None), ("win32", True, None),
    ("darwin", True, None)])
def test_a_restart_from_an_appimage_starts_the_appimage(tmp_path, platform, inside, expected):
    from si_assistant.ui import restart
    mount = tmp_path / "mount"
    exe = (mount if inside else tmp_path / "elsewhere") / "usr" / "lib" / "app" / "app"
    env = {"APPIMAGE": str(tmp_path / "Chem.AppImage"), "APPDIR": str(mount)}
    found = restart.appimage_file(env, str(exe), platform)
    assert found == (env["APPIMAGE"] if expected else None)
    assert restart.appimage_file({}, str(exe), "linux") is None


def test_restart_command_uses_the_appimage_when_frozen(tmp_path, monkeypatch):
    import sys
    from si_assistant.ui import restart
    mount = tmp_path / "mount"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", str(mount / "usr" / "lib" / "app" / "app"))
    monkeypatch.setenv("APPDIR", str(mount))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "Chem.AppImage"))
    args, cwd = restart.restart_command({"folder": "x"})
    assert args[0] == str(tmp_path / "Chem.AppImage") and cwd is None
    assert args[1] == restart.RESTORE_ARG
    monkeypatch.delenv("APPIMAGE")
    args, _cwd = restart.restart_command({"folder": "x"})
    assert args[0] == sys.executable


# ---------------------------------------------------------------------- fixes asked for by other parts

def test_an_error_half_way_through_opening_leaves_no_half_open_project(
        window, sample, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, _s = sample
    win = window(p)   # (opens it once, with the settings of a test)
    win.close_project()
    real = mw.MainWindow._populate_tree

    def broken(self, *a, **k):
        raise RuntimeError("something unexpected")
    monkeypatch.setattr(mw.MainWindow, "_populate_tree", broken)
    with pytest.raises(RuntimeError):
        win.open_project(p.folder)
    assert win.project is None and not win._lock_timer.isActive()
    assert Project.open(p.folder).current_lock() is None   # the lock was let go
    # Opening again works, without "someone else has it open".
    monkeypatch.setattr(mw.MainWindow, "_populate_tree", real)
    monkeypatch.setattr(mw, "ask_about_lock", lambda *a: pytest.fail("lock left behind"))
    assert win.open_project(p.folder)
    assert win._lock_timer.isActive()


def test_an_error_before_the_window_shows_the_project_lets_go_of_the_lock(
        window, sample, monkeypatch):
    p, _s = sample
    win = window(p)
    win.close_project()

    def broken(self):
        raise RuntimeError("something unexpected")
    monkeypatch.setattr(Project, "upgrade_all_pictures", broken)
    with pytest.raises(RuntimeError):
        win.open_project(p.folder)
    assert win.project is None
    assert Project.open(p.folder).current_lock() is None


def test_no_snapshot_after_a_takeover_while_asking_for_its_label(window, sample, warnings,
                                                                 monkeypatch):
    from si_assistant.ui import main_window as mw
    p, _s = sample
    win = window(p)

    def ask(*a, **k):
        win.project.read_only = True   # (as _check_lock does after a takeover)
        return "submitted", True
    monkeypatch.setattr(mw.QInputDialog, "getText", staticmethod(ask))
    win.save_snapshot()
    assert not list(p.folder.rglob("*.zip")) and warnings == []


def test_a_snapshot_that_cannot_be_zipped_says_so(window, sample, warnings, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, _s = sample
    win = window(p)
    monkeypatch.setattr(mw.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))

    def bad(label=""):
        raise ValueError("a file name the zip can't store")
    monkeypatch.setattr(win.project, "save_snapshot", bad)
    win.save_snapshot()
    assert [t for t, _ in warnings] == ["Could not save snapshot"]


def test_a_failed_delete_shows_the_item_again(window, tmp_path, warnings, monkeypatch):
    from si_assistant.core.project import ProjectError
    p, d = small_project(tmp_path)
    win = window(p)
    cid = d["b"].id
    win.select("compound", cid)

    def refuse(_id):
        raise ProjectError("A file in it is open in another program.")
    monkeypatch.setattr(win.project, "delete_compound", refuse)
    win.delete_item()
    assert [t for t, _ in warnings] == ["Could not save"]
    assert win.statusBar().currentMessage() != "Moved to Trash"
    assert win._current() == ("compound", cid) and win.panel is not None
    assert win.project.compound(cid).name == "Beta"


def test_pasting_a_spectrum_updates_the_trees_warnings(window, sample):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    c = win.project.compound(cid)
    every = list(c.values["spectra"])
    c.values["spectra"] = [e for e in every if e.get("type") != "13C"]
    win._refresh_labels()
    win.select("spectra_page", cid)
    assert "spectrum attached" in compound_labels(win)[cid][1]
    c.values["spectra"] = every   # (as a paste on the page does)
    win.panel.spectra_changed.emit()
    assert "spectrum attached" not in compound_labels(win)[cid][1]


def test_an_edit_saved_as_the_page_closes_does_not_break_redo(window, sample, warnings,
                                                              monkeypatch):
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)

    def rename(text):
        c = win.project.compound(cid)
        c.values["name"]["text"] = text
        with win.project.undo_step(f"Rename to {text}", where=[("compound", cid)]):
            win.project.save_compound(c)
    rename("First")
    win.undo()
    assert win.project.can_redo
    real = win._set_panel
    done = []

    def closing(panel):
        if panel is None and not done:   # (a box that saves when it loses focus)
            done.append(1)
            rename("Typed as the page closed")
        real(panel)
    monkeypatch.setattr(win, "_set_panel", closing)
    win.redo()
    assert done and warnings == []
    assert win.panel is not None   # (not a blank page)
    assert win.project.compound(cid).name == "Typed as the page closed"


def test_an_error_in_the_trash_window_does_not_leave_a_blank_page(window, sample,
                                                                  monkeypatch):
    from si_assistant.ui import main_window as mw
    p, s = sample
    win = window(p)
    win.select("compound", s["c2"].id)

    class Broken:
        def __init__(self, *a):
            pass

        def exec(self):
            raise RuntimeError("something unexpected")
    monkeypatch.setattr(mw, "TrashDialog", Broken)
    with pytest.raises(RuntimeError):
        win.show_trash()
    assert win.panel is not None and win._current() == ("compound", s["c2"].id)


@pytest.mark.parametrize("env, frozen, platform, expected", [
    ({"LD_LIBRARY_PATH": "/b"}, True, "linux", {}),
    ({"LD_LIBRARY_PATH": "/b:/usr/local/lib", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"},
     True, "linux", {"LD_LIBRARY_PATH": "/usr/local/lib",
                     "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"}),
    ({"LD_LIBRARY_PATH": "/opt/mine"}, True, "linux", {"LD_LIBRARY_PATH": "/opt/mine"}),
    ({"LD_LIBRARY_PATH": "/b"}, False, "linux", {"LD_LIBRARY_PATH": "/b"}),
    ({"LD_LIBRARY_PATH": "/b"}, True, "darwin", {"LD_LIBRARY_PATH": "/b"}),
    ({"LD_LIBRARY_PATH": "/b"}, True, "win32", {"LD_LIBRARY_PATH": "/b"}),
    ({}, True, "linux", {})])
def test_programs_opened_from_the_linux_bundle_get_their_own_libraries(env, frozen, platform,
                                                                       expected):
    from si_assistant.ui import restart
    env = dict(env)
    restart.forget_bundle_library_path(env, frozen, platform, "/b")
    assert env == expected

# ---------------------------------------------------------------------- a save that keeps failing

def test_a_failed_spectra_settings_save_counts_as_unsaved_typing(window, sample):
    p, s = sample
    win = window(p)
    win.select("section", s["spectra"].id)
    assert type(win.panel).__name__ == "SpectraSectionPanel"
    assert not win._panel_has_unsaved_typing()
    win.panel._unsaved = True   # (as after a failed save, kept to be tried again)
    assert win._panel_has_unsaved_typing()
    win.panel._unsaved = False


@pytest.fixture
def failing_text_page(window, sample, monkeypatch):
    """A text page whose save keeps failing (its file is held by another program), and
    the questions asked (answered No)."""
    p, s = sample
    win = window(p)
    notes = s["notes"]
    win.select("section", notes.id)
    page = win.panel

    def held(*_a, **_k):
        raise PermissionError(13, "held by another program")
    win.project.write_section_text = held
    page._unsaved = True
    asked = []

    def question(_parent, title, text, *a, **k):
        asked.append((title, text))
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    yield win, page, notes, asked
    # (the window fixture closes it: let the save work again)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    if "write_section_text" in vars(win.project):
        del win.project.write_section_text


def test_a_page_whose_save_failed_is_kept_when_another_item_is_picked(
        failing_text_page, sample, warnings):
    win, page, notes, asked = failing_text_page
    _p, s = sample
    win.select("compound", s["c2"].id)
    assert win.panel is page and win._current() == ("section", notes.id)
    assert [t for t, _ in warnings] == ["Could not save"]
    [(title, text)] = asked
    assert title == "Changes not saved" and "Leave the page anyway" in text
    # Once the file can be written again, leaving the page saves it.
    del win.project.write_section_text
    win.select("compound", s["c2"].id)
    assert win.panel is not page and win._current() == ("compound", s["c2"].id)
    assert len(asked) == 1


def test_a_page_whose_save_failed_can_be_left_anyway(failing_text_page, sample, monkeypatch):
    win, page, _notes, _asked = failing_text_page
    _p, s = sample
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.select("compound", s["c2"].id)
    assert win.panel is not page and win._current() == ("compound", s["c2"].id)


def test_undo_keeps_a_page_whose_save_failed(failing_text_page):
    win, page, _notes, asked = failing_text_page
    win.undo()
    assert win.panel is page
    [(title, text)] = asked
    assert "Undo anyway" in text


def test_closing_keeps_a_project_whose_page_could_not_be_saved(failing_text_page):
    win, page, _notes, asked = failing_text_page
    assert win.close_project() is False
    assert win.project is not None and win.panel is page
    assert "Close the project anyway" in asked[0][1]
    assert win._lock_timer.isActive() and not win.project.read_only


def test_closing_the_window_is_cancelled_when_the_page_could_not_be_saved(failing_text_page):
    win, _page, _notes, _asked = failing_text_page
    event = types.SimpleNamespace(ignored=False, accepted=False)
    event.ignore = lambda: setattr(event, "ignored", True)
    event.accept = lambda: setattr(event, "accepted", True)
    win.closeEvent(event)
    assert event.ignored and not event.accepted and win.project is not None


def test_read_only_pages_are_left_without_asking(failing_text_page, sample):
    win, page, _notes, asked = failing_text_page
    _p, s = sample
    win.project.read_only = True   # (nothing is tried, so nothing failed)
    win.select("compound", s["c2"].id)
    assert asked == [] and win.panel is not page
    win.project.read_only = False


# ---------------------------------------------------------------------- Warnings panel saves

def _panel_item(panel, kind):
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    from si_assistant.ui import warnings_panel as W
    it = QTreeWidgetItemIterator(panel.tree)
    while it.value():
        data = it.value().data(0, W.ROLE)
        if data and data[0] == kind:
            return it.value()
        it += 1
    return None


@pytest.fixture
def ms_panel(qapp, tmp_path, monkeypatch, warnings):
    from si_assistant.core import manuscript as M
    from si_assistant.ui import warnings_panel as W
    from tests import manuscript_samples as S
    p, _c = S.build_si(tmp_path / "si")
    S.make_pdf(tmp_path / "ms.pdf")
    M.add_file(p, tmp_path / "ms.pdf")
    res = M.check(p, M.read_all(p))
    M.dismiss(p, res.problems[0], "earlier")
    monkeypatch.setattr(W, "ask_reason", lambda *a, **k: "ok")
    panel = W.WarningsPanel()
    panel.show_dismissed.setChecked(True)
    panel.set_project(p)
    hooks = []
    monkeypatch.setattr("sys.excepthook", lambda *a: hooks.append(a))
    real = Path.read_bytes

    def locked(path):
        if path.name == M.DISMISSED_FILE:
            raise PermissionError(13, "locked by another program")
        return real(path)
    yield p, panel, M, locked, hooks
    panel.deleteLater()
    p.close()


def test_a_manuscript_dismissal_that_cannot_be_saved_says_so(ms_panel, warnings, monkeypatch):
    p, panel, M, locked, hooks = ms_panel
    before = (M.folder(p) / M.DISMISSED_FILE).read_bytes()
    panel.tree.setCurrentItem(_panel_item(panel, "ms"))
    with monkeypatch.context() as m:
        m.setattr(Path, "read_bytes", locked)
        panel._dismiss_selected()
    [(title, text)] = warnings
    assert title == "Dismiss" and "could not be saved" in text and hooks == []
    assert (M.folder(p) / M.DISMISSED_FILE).read_bytes() == before


def test_a_manuscript_restore_that_cannot_be_saved_says_so(ms_panel, warnings, monkeypatch):
    p, panel, M, locked, hooks = ms_panel
    before = (M.folder(p) / M.DISMISSED_FILE).read_bytes()
    panel.tree.setCurrentItem(_panel_item(panel, "ms_dismissed"))
    with monkeypatch.context() as m:
        m.setattr(Path, "read_bytes", locked)
        panel._restore_selected()
    [(title, text)] = warnings
    assert title == "Restore" and "could not be restored" in text and hooks == []
    assert (M.folder(p) / M.DISMISSED_FILE).read_bytes() == before


def test_a_compound_dismissal_that_cannot_be_saved_says_so(qapp, tmp_path, warnings,
                                                           monkeypatch):
    from si_assistant.ui import warnings_panel as W
    p = Project.create(tmp_path, "W")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    c = p.add_compound(data.id, name="Toluene")
    p.set_structure(c, "Cc1ccccc1")
    c.values["nmr"][1]["text"] = "13C NMR (101 MHz, CDCl3) δ 137.9, 129.1, 21.5"
    p.save_compound(c)
    monkeypatch.setattr(W, "ask_reason", lambda *a, **k: "ok")
    panel = W.WarningsPanel()
    panel.set_project(p)
    panel.tree.setCurrentItem(_panel_item(panel, "warning"))

    def held(_compound):
        raise PermissionError(13, "held by another program")
    with monkeypatch.context() as m:
        m.setattr(p, "save_compound", held)
        panel._dismiss_selected()
    [(title, text)] = warnings
    assert title == "Dismiss" and "could not be saved" in text
    panel.deleteLater()
    p.close()


def test_a_compound_dismissal_that_cannot_be_saved_is_not_kept_in_memory(
        qapp, tmp_path, warnings, monkeypatch):
    """Told it was not saved: the next save of the compound (e.g. typing on its page) must
    not write it after all."""
    from si_assistant.ui import warnings_panel as W
    p = Project.create(tmp_path, "W")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    c = p.add_compound(data.id, name="Toluene")
    p.set_structure(c, "Cc1ccccc1")
    c.values["nmr"][1]["text"] = "13C NMR (101 MHz, CDCl3) δ 137.9, 129.1, 21.5"
    p.save_compound(c)
    monkeypatch.setattr(W, "ask_reason", lambda *a, **k: "ok")
    panel = W.WarningsPanel()
    panel.set_project(p)
    panel.tree.setCurrentItem(_panel_item(panel, "warning"))
    before = c.data.get("dismissed")

    def held(_compound):
        raise PermissionError(13, "held by another program")
    with monkeypatch.context() as m:
        m.setattr(p, "save_compound", held)
        panel._dismiss_selected()
    assert [t for t, _ in warnings] == ["Dismiss"]
    assert c.data.get("dismissed") == before            # (put back as it was)
    p.save_compound(c)                                  # an unrelated save later
    assert not Project.open(p.folder).compound(c.id).data.get("dismissed")
    # A restore that cannot be saved is put back the same way.
    panel.tree.setCurrentItem(_panel_item(panel, "warning"))
    panel._dismiss_selected()                           # (this one is saved)
    dismissed = dict(c.data["dismissed"])
    panel.show_dismissed.setChecked(True)
    panel.refresh()
    panel.tree.setCurrentItem(_panel_item(panel, "dismissed"))
    with monkeypatch.context() as m:
        m.setattr(p, "save_compound", held)
        panel._restore_selected()
    assert [t for t, _ in warnings] == ["Dismiss", "Restore"]
    assert c.data["dismissed"] == dismissed
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- a compound page that can't be saved

@pytest.fixture
def failing_compound_page(window, sample, monkeypatch):
    """Compound 2's page with a Manual ID typed whose save keeps failing (compound.json held
    by another program), and the questions asked (answered with answer["value"])."""
    p, s = sample
    win = window(p)
    cid = s["c2"].id
    win.select("compound", cid)
    page = win.panel

    def held(_compound):
        raise PermissionError(13, "held by another program")
    win.project.save_compound = held
    type_manual_id(page, "77")
    asked = []
    answer = {"value": QMessageBox.StandardButton.No}

    def question(_parent, title, text, *a, **k):
        asked.append((title, text))
        if title == "Changes not saved":
            return answer["value"]
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    yield win, page, cid, asked, answer
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    if win.project is not None and "save_compound" in vars(win.project):
        del win.project.save_compound


def test_a_warning_change_keeps_a_compound_page_whose_save_failed(failing_compound_page,
                                                                  warnings):
    win, page, cid, asked, _answer = failing_compound_page
    folder = win.project.folder
    win._after_warnings_changed()          # (a warning dismissed in the Warnings panel)
    assert win.panel is page and page.override_edit.text() == "77" and page._dirty
    assert asked == [] and [t for t, _ in warnings] == ["Could not save"]
    del win.project.save_compound          # the file is free again: closing saves it
    assert win.close_project()
    assert Project.open(folder).compound(cid).id_override == "77"


@pytest.mark.parametrize("command", ["show_trash", "move_down", "new_compound", "delete",
                                     "duplicate", "convert"])
def test_commands_that_leave_the_page_ask_first(failing_compound_page, monkeypatch, command):
    from si_assistant.ui import main_window as mw
    win, page, cid, asked, _answer = failing_compound_page
    opened = []

    def dialog(*a, **k):
        opened.append(a)
        return types.SimpleNamespace(exec=lambda: 0)
    monkeypatch.setattr(mw, "TrashDialog", dialog)
    monkeypatch.setattr("si_assistant.ui.duplicate_ui.DuplicateDialog", dialog)
    count = len(list(win.project.all_compounds()))
    {"show_trash": win.show_trash, "move_down": lambda: win.move_item(1),
     "new_compound": win.new_compound, "delete": win.delete_item,
     "duplicate": lambda: win.duplicate_item("compound", cid),
     "convert": lambda: win.convert_to_scheme(cid)}[command]()
    assert win.panel is page and page.override_edit.text() == "77"
    assert win._current() == ("compound", cid)
    assert [t for t, _ in asked] == (["Changes not saved"] if command != "convert"
                                     else ["Change to reaction scheme", "Changes not saved"])
    assert opened == [] and len(list(win.project.all_compounds())) == count


def test_leaving_anyway_throws_the_changes_away_in_memory_too(failing_compound_page, sample):
    """The page had copied its values into the compound in memory before the save failed:
    they must not come back as if saved (nor be written by a later save)."""
    win, page, cid, asked, answer = failing_compound_page
    _p, s = sample
    folder = win.project.folder
    answer["value"] = QMessageBox.StandardButton.Yes
    win.select("section", s["notes"].id)
    assert len(asked) == 1 and win.panel is not page
    assert win._current() == ("section", s["notes"].id)
    assert win.project.compound(cid).id_override == ""
    del win.project.save_compound
    win.select("compound", cid)
    assert win.panel.override_edit.text() == "" and not win.panel._dirty
    win.project.save_compound(win.project.compound(cid))   # an unrelated save later
    assert win.close_project()
    assert Project.open(folder).compound(cid).id_override == ""


def test_leaving_anyway_by_the_trash_throws_the_changes_away(failing_compound_page,
                                                            monkeypatch):
    from si_assistant.ui import main_window as mw
    win, page, cid, asked, answer = failing_compound_page
    answer["value"] = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(mw, "TrashDialog",
                        lambda *a, **k: types.SimpleNamespace(exec=lambda: 0))
    win.show_trash()
    assert len(asked) == 1 and win.panel is not page
    assert win._current() == ("compound", cid)
    assert win.panel.override_edit.text() == "" and not win.panel._dirty


def test_the_project_cannot_be_read_back_after_leaving_anyway(failing_compound_page, sample,
                                                              warnings, monkeypatch, qapp):
    win, page, _cid, _asked, answer = failing_compound_page
    _p, s = sample
    answer["value"] = QMessageBox.StandardButton.Yes

    def unreadable():
        raise OSError(13, "held by another program")
    monkeypatch.setattr(win.project, "reload", unreadable)
    win.select("section", s["notes"].id)
    assert win.project.read_only                  # (memory no longer matches the files)
    assert [t for t, _ in warnings] == ["Could not save", "Project now read-only"]
    assert wait_until(qapp, lambda: win.panel is not None)
    assert win.panel is not page


def test_a_click_on_another_item_keeps_the_page_selected(failing_text_page, sample, qapp):
    """The tree selects the clicked item only after telling the window: the page's item
    must end up both current and highlighted (Delete, Rename and Move act on it)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win, page, notes, asked = failing_text_page
    _p, s = sample
    win.show()
    item = win._find_item("compound", s["c2"].id)
    win.tree.scrollToItem(item)
    QTest.mouseClick(win.tree.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, win.tree.visualItemRect(item).center())
    for _ in range(3):
        qapp.processEvents()
    assert len(asked) == 1 and win.panel is page
    assert win._current() == ("section", notes.id)
    assert win.tree.selectedItems() == [win.tree.currentItem()]
    # The arrow keys too.
    QTest.keyClick(win.tree, Qt.Key.Key_Down)
    for _ in range(3):
        qapp.processEvents()
    assert len(asked) == 2 and win.panel is page
    assert win.tree.selectedItems() == [win.tree.currentItem()]
    assert win._current() == ("section", notes.id)


def test_moving_a_text_page_whose_save_failed_asks_first(failing_text_page):
    win, page, notes, asked = failing_text_page
    win.move_item(1)
    assert len(asked) == 1 and win.panel is page
    assert win._current() == ("section", notes.id)


def test_a_failed_save_is_reported_once_on_restart(failing_text_page, warnings, monkeypatch):
    from si_assistant.ui import restart
    win, page, _notes, asked = failing_text_page
    started = []
    monkeypatch.setattr(restart, "launcher", lambda *a: started.append(a))
    assert win.restart_program() is False          # (No: kept open)
    assert [t for t, _ in warnings] == ["Could not save"]
    assert len(asked) == 1 and started == [] and win.panel is page


def test_a_failed_save_is_reported_once_on_view_spectra_page(failing_compound_page, warnings,
                                                            qapp):
    win, page, cid, asked, _answer = failing_compound_page
    win.show_spectra_page(cid)
    for _ in range(3):
        qapp.processEvents()
    assert [t for t, _ in warnings] == ["Could not save"]
    assert len(asked) == 1 and win.panel is page


def test_a_spectra_page_kept_after_a_takeover_cannot_be_changed(window, sample, warnings):
    from PySide6.QtWidgets import QAbstractSpinBox
    p, s = sample
    win = window(p)
    win.select("spectra_page", s["c2"].id)
    page = win.panel
    page._unsaved = True        # (a settings save that failed, kept to be tried again)
    Project.open(p.folder).acquire_lock("Bob")
    win._check_lock()
    assert win.panel is page
    assert page.read_only and not page.canvas.editable
    spins = page.findChildren(QAbstractSpinBox)
    assert spins and all(spin.isReadOnly() for spin in spins)
    spins[0].stepUp()
    page.flush()
    assert [t for t, _ in warnings] == ["Project taken over"]
    page._unsaved = False


def test_a_page_that_says_it_has_unsaved_edits_counts_as_waiting(qapp):
    """Pages with has_unsaved() (the compound page, the general procedures) are asked;
    others are still read from _dirty / _unsaved."""
    from si_assistant.ui.main_window import MainWindow
    page = QWidget()
    part = QWidget(page)
    assert not MainWindow._panel_waits_to_save(page)
    part.has_unsaved = lambda: True
    assert MainWindow._panel_waits_to_save(page)
    part.has_unsaved = lambda: False
    part._dirty = True   # (has_unsaved() is what counts where it exists)
    assert not MainWindow._panel_waits_to_save(page)
    del part.has_unsaved
    assert MainWindow._panel_waits_to_save(page)
    part._dirty = False
    page._unsaved = True
    assert MainWindow._panel_waits_to_save(page)
    page.deleteLater()
