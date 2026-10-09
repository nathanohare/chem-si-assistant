"""Audit fixes for the program shell: updates, Project settings and the Trash window."""

import json
import time

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from si_assistant import updates
from si_assistant.core.project import Project
from si_assistant.updates import Asset, UpdateInfo


def info_for(assets):
    return UpdateInfo("newer", "1.0.0", "9.0.0", notes="", assets=list(assets))


def run_until_done(qapp, up):
    deadline = time.monotonic() + 5
    while up.result is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------- a failed update download

@pytest.mark.parametrize("key,name", [("windows", "Chem-SI-Assistant-9.0.0-Windows-Setup.exe"),
                                      ("linux", "Chem-SI-Assistant-9.0.0-x86_64.AppImage")])
def test_a_failed_download_is_reported(qapp, tmp_path, monkeypatch, key, name):
    from si_assistant.ui import update_ui
    monkeypatch.setattr(update_ui, "download_folder", lambda k: tmp_path)

    def fail(url, dest, size, report):
        report(10, 100)
        raise updates.DownloadError("The download failed: connection reset")
    monkeypatch.setattr(updates, "download", fail)
    shown = []
    # (the message, not the window title: macOS message boxes have none)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    win = update_ui.QWidget()
    up = update_ui.Updater(win, info_for([Asset(name, "https://example.invalid/x", 100)]), key=key)
    up.start()
    run_until_done(qapp, up)
    assert isinstance(up.result, updates.DownloadError)
    assert len(shown) == 1 and "connection reset" in shown[0]
    win.deleteLater()


def test_a_cancelled_download_is_not_reported(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import update_ui
    monkeypatch.setattr(update_ui, "download_folder", lambda k: tmp_path)
    started = []

    def slow(url, dest, size, report):
        started.append(True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not report(10, 100):
                raise updates.DownloadError("The download was cancelled.")
            time.sleep(0.01)
        return dest
    monkeypatch.setattr(updates, "download", slow)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.windowTitle()) or 0)
    win = update_ui.QWidget()
    up = update_ui.Updater(win, info_for([Asset("Chem-SI-Assistant-9.0.0-x86_64.AppImage",
                                                "https://example.invalid/x", 100)]), key="linux")
    up.start()
    while not started:
        qapp.processEvents()
    cancel = next(b for b in up.progress_dialog.findChildren(QPushButton) if b.text() == "Cancel")
    cancel.click()   # the user's Cancel
    run_until_done(qapp, up)
    assert isinstance(up.result, updates.DownloadError) and shown == []
    win.deleteLater()


def test_cancel_clicked_as_the_download_finishes_does_not_install(qapp, tmp_path, monkeypatch):
    """The last part arrives, then Cancel is clicked before the "finished" signal is
    handled: the project is not closed and the installer is not started."""
    from si_assistant.ui import update_ui
    monkeypatch.setattr(update_ui, "download_folder", lambda k: tmp_path)
    done = []

    def quick(url, dest, size, report):
        report(100, 100)
        dest.write_bytes(b"exe")
        done.append(True)
        return dest
    monkeypatch.setattr(updates, "download", quick)
    events = []

    class Window(update_ui.QWidget):
        def close_project(self):
            events.append("project closed")
            return True
    win = Window()
    up = update_ui.Updater(win, info_for([Asset("Chem-SI-Assistant-9.0.0-Windows-Setup.exe",
                                                "https://example.invalid/w.exe", 3)]),
                           key="windows")
    up.launch = lambda path: events.append("installer started")
    up.quit_app = lambda: events.append("program quit")
    up.start()
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        time.sleep(0.01)   # (no events handled: "finished" waits in the queue)
    cancel = next(b for b in up.progress_dialog.findChildren(QPushButton) if b.text() == "Cancel")
    cancel.click()   # the user's Cancel
    run_until_done(qapp, up)
    assert up.result == tmp_path / "Chem-SI-Assistant-9.0.0-Windows-Setup.exe"
    assert events == []
    win.deleteLater()


# ---------------------------------------------------------------- old Windows installers

def test_old_windows_installers_are_removed(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import update_ui
    for name in ("Chem-SI-Assistant-0.9.0-Windows-Setup.exe",
                 "Chem-SI-Assistant-0.9.1-Windows-Setup.exe.part",
                 "Chem-SI-Assistant-9.0.0-Windows-Setup.exe.part",   # being written now
                 "notes.txt"):
        (tmp_path / name).write_bytes(b"old")
    monkeypatch.setattr(update_ui, "download_folder", lambda k: tmp_path)
    monkeypatch.setattr(updates, "download", lambda url, dest, size, report: dest)
    win = update_ui.QWidget()
    up = update_ui.Updater(win, info_for([Asset("Chem-SI-Assistant-9.0.0-Windows-Setup.exe",
                                                "https://example.invalid/w.exe", 3)]),
                           key="windows")
    up.hand_over = lambda path: None
    up.start()
    run_until_done(qapp, up)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Chem-SI-Assistant-9.0.0-Windows-Setup.exe.part", "notes.txt"]
    win.deleteLater()


def test_downloads_folder_is_left_alone_on_linux(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import update_ui
    old = tmp_path / "Chem-SI-Assistant-0.9.0-Windows-Setup.exe"   # e.g. kept for a colleague
    old.write_bytes(b"old")
    (tmp_path / "Chem-SI-Assistant-0.9.0-x86_64.AppImage").write_bytes(b"old")
    monkeypatch.setattr(update_ui, "download_folder", lambda k: tmp_path)
    monkeypatch.setattr(updates, "download", lambda url, dest, size, report: dest)
    win = update_ui.QWidget()
    up = update_ui.Updater(win, info_for([Asset("Chem-SI-Assistant-9.0.0-x86_64.AppImage",
                                                "https://example.invalid/x", 3)]), key="linux")
    up.hand_over = lambda path: None
    up.start()
    run_until_done(qapp, up)
    assert old.exists() and (tmp_path / "Chem-SI-Assistant-0.9.0-x86_64.AppImage").exists()
    win.deleteLater()


def test_a_locked_installer_does_not_stop_the_cleanup(tmp_path, monkeypatch):
    from si_assistant.ui import update_ui
    for v in ("0.8.0", "0.9.0"):
        (tmp_path / f"Chem-SI-Assistant-{v}-Windows-Setup.exe").write_bytes(b"old")
    real_unlink = type(tmp_path).unlink

    def unlink(self, *a, **k):
        if "0.8.0" in self.name:
            raise PermissionError("in use")   # a running installer
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(type(tmp_path), "unlink", unlink)
    update_ui.remove_old_installers(tmp_path, keep=tmp_path / "Chem-SI-Assistant-9.0.0-Windows-Setup.exe")
    assert [p.name for p in tmp_path.iterdir()] == ["Chem-SI-Assistant-0.8.0-Windows-Setup.exe"]


# ---------------------------------------------------------------- hand-edited project.json

def damaged_project(**values):
    import tempfile
    from pathlib import Path
    p = Project.create(Path(tempfile.mkdtemp()), "P")
    p.close()
    path = p.folder / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(values)
    path.write_text(json.dumps(data), encoding="utf-8")
    return Project.open(p.folder, read_only=True)


@pytest.mark.parametrize("values,tolerance,style", [
    ({"hrms_tolerance_ppm": None}, 5.0, "ACS"),
    ({"hrms_tolerance_ppm": "5 ppm"}, 5.0, "ACS"),
    ({"hrms_tolerance_ppm": "nan"}, 5.0, "ACS"),
    ({"hrms_tolerance_ppm": "3"}, 3.0, "ACS"),
    ({"hrms_tolerance_ppm": 10 ** 400}, 5.0, "ACS"),   # float() raises OverflowError
    ({"journal_style": 3}, 5.0, "ACS"),
    ({"journal_style": None}, 5.0, "ACS"),
    ({"journal_style": ["ACS"]}, 5.0, "ACS"),
    ({"journal_style": {"a": 1}}, 5.0, "ACS"),
    ({"impurity_table": [1, "x", {"solvent": "CDCl3", "nucleus": "1H", "shift": 7.26,
                                  "what": "CHCl3", "on": True}]}, 5.0, "ACS"),
])
def test_project_settings_open_with_hand_edited_values(qapp, values, tolerance, style):
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    p = damaged_project(**values)
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.tolerance.value() == tolerance
    assert dlg.style_box.currentText() == style
    if "impurity_table" in values:
        assert dlg.impurities.table.table.rowCount() == 1
    dlg.deleteLater()
    p.close()


def test_a_list_as_journal_style_is_saved_as_acs(qapp):
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    import tempfile
    from pathlib import Path
    p = Project.create(Path(tempfile.mkdtemp()), "P")
    p.settings["journal_style"] = ["RSC"]
    dlg = ProjectSettingsDialog(p, "Tester")
    dlg.apply()
    assert p.settings["journal_style"] == "ACS"
    dlg.deleteLater()
    p.close()


@pytest.mark.parametrize("stored,shown", [(0.05, 0.05), (2.25, 2.25), (250, 250.0),
                                          (0.001, 0.01), (5000, 1000.0)])
def test_a_hand_set_hrms_tolerance_survives_an_unrelated_ok(qapp, stored, shown):
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    import tempfile
    from pathlib import Path
    p = Project.create(Path(tempfile.mkdtemp()), "P")
    p.settings["hrms_tolerance_ppm"] = stored
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.tolerance.value() == shown
    dlg.auto_31p.setChecked(True)   # something else changed
    dlg.apply()
    assert p.settings["hrms_tolerance_ppm"] == stored
    dlg.tolerance.setValue(3.5)     # the box changed: its value is saved
    dlg.apply()
    assert p.settings["hrms_tolerance_ppm"] == 3.5
    dlg.deleteLater()
    p.close()


# ---------------------------------------------------------------- the Trash window

def trashed_project(n: int = 2):
    import tempfile
    from pathlib import Path
    p = Project.create(Path(tempfile.mkdtemp()), "P")
    for i in range(n):
        s = p.add_section("data", f"Section {i}")
        p.delete_section(s.id)
    return p


def edit_entry(p, entry, **values):
    path = entry.folder / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(values)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("values", [{"deleted_at": None}, {"deleted_at": 7}, {"name": 5},
                                    {"subsections": "x"}, {"subsections": [1]},
                                    {"kind": ["section"]}, {"deleted_by": {"a": 1}}])
def test_a_damaged_trash_description_does_not_hide_the_trash(qapp, values):
    from si_assistant.ui.dialogs import TrashDialog
    p = trashed_project()
    edit_entry(p, p.list_trash()[0], **values)
    dlg = TrashDialog(p)
    assert dlg.table.rowCount() == 2
    assert all(dlg.table.item(r, 0).text() for r in range(2))
    # The undamaged item can still be restored.
    good = p.list_trash()[1]
    p.restore_from_trash(good.id)
    assert len(p.list_trash()) == 1
    dlg.deleteLater()
    p.close()


def test_restoring_a_damaged_trash_item_shows_a_plain_warning(qapp, monkeypatch):
    from si_assistant.ui.dialogs import TrashDialog
    p = trashed_project(1)
    entry = p.list_trash()[0]
    path = entry.folder / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["item"]
    path.write_text(json.dumps(data), encoding="utf-8")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dlg = TrashDialog(p)
    dlg.table.selectRow(0)
    dlg._restore()
    assert warned and "damaged" in warned[0]
    dlg.deleteLater()
    p.close()


def test_trash_kind_column_is_readable_for_figures_tables_and_scheme_structures(qapp):
    from si_assistant.ui.dialogs import TrashDialog
    p = trashed_project(3)
    entries = p.list_trash()
    edit_entry(p, entries[0], kind="figure", owner_name="Optimization")
    edit_entry(p, entries[1], kind="table", owner_name="Optimization")
    edit_entry(p, entries[2], kind="scheme_box", scheme_name="Synthesis")
    dlg = TrashDialog(p)
    kinds = sorted(dlg.table.item(r, 1).text() for r in range(3))
    assert kinds == ["Figure (from “Optimization”)", "Structure (from scheme “Synthesis”)",
                     "Table (from “Optimization”)"]
    dlg.deleteLater()
    p.close()


def test_empty_trash_question_names_the_undo_limit(qapp, monkeypatch):
    from si_assistant.ui.dialogs import TrashDialog
    p = trashed_project(1)
    p.enable_undo()
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel))
    dlg = TrashDialog(p)
    dlg._empty()
    assert f"last {p.journal.max_steps} changes" in asked[0]
    assert "until the project is closed" in asked[0]
    assert len(p.list_trash()) == 1   # cancelled: nothing deleted
    dlg.deleteLater()
    p.close()


@pytest.mark.parametrize("item", [None, 3, ["x"], ""])
def test_a_damaged_item_name_is_caught_before_anything_moves(qapp, monkeypatch, item):
    from si_assistant.ui.dialogs import TrashDialog
    p = trashed_project(1)
    p.enable_undo()
    edit_entry(p, p.list_trash()[0], item=item)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dlg = TrashDialog(p)
    dlg.table.selectRow(0)
    dlg._restore()
    assert warned and "damaged" in warned[0]
    assert len(p.list_trash()) == 1 and not p.can_undo   # nothing was changed
    dlg.deleteLater()
    p.close()


def deleted_section_and_compound():
    import tempfile
    from pathlib import Path
    p = Project.create(Path(tempfile.mkdtemp()), "P")
    p.add_section("data", "Stays")
    gone = p.add_section("data", "Keep me")
    data = p.add_section("data", "Data")
    p.add_compound(data.id, name="Benzene")
    p.enable_undo()
    p.delete_section(gone.id)
    p.delete_compound(p.all_compounds()[0].id)
    return p


def state_of(p):
    from pathlib import Path
    sections = sorted(s.name for s in p.top_sections)
    on_disk = sorted(f.name for f in (p.folder / "sections").iterdir())
    return (sections, [c.name for c in p.all_compounds()], sorted(e.name for e in p.list_trash()),
            on_disk, sorted(str(f.relative_to(p.folder)) for f in Path(p.folder).rglob("compound.json")))


@pytest.mark.parametrize("kind", ["section", "compound"])
def test_a_restore_that_fails_after_moving_is_taken_back(qapp, monkeypatch, kind):
    """An unexpected fault after the folder has left the Trash: the restore is undone
    (nothing half put back), and the fault still reaches the error report."""
    from si_assistant.ui.dialogs import TrashDialog
    p = deleted_section_and_compound()
    before = state_of(p)

    def fault(*args):
        raise ValueError("a fault in the program")
    # (both are saved after the folder has moved out of the Trash)
    monkeypatch.setattr(p, "save_settings", fault)
    monkeypatch.setattr(p, "_save_owner", fault)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dlg = TrashDialog(p)
    dlg.table.selectRow(next(i for i, e in enumerate(dlg.entries) if e.kind == kind))
    with pytest.raises(ValueError):   # (shown by the error reporter, as before)
        dlg._restore()
    assert state_of(p) == before
    assert not dlg.restored_any and dlg.table.rowCount() == 2
    reopened = Project.open(p.folder, read_only=True)
    assert state_of(reopened) == before
    reopened.close()
    dlg.deleteLater()
    p.close()


@pytest.mark.parametrize("kind", ["section", "compound"])
def test_a_hand_edited_position_in_the_trash_still_restores(qapp, monkeypatch, kind):
    """original_index edited into something that is not a number: put back at the end."""
    from si_assistant.ui.dialogs import TrashDialog
    p = deleted_section_and_compound()
    entry = next(e for e in p.list_trash() if e.kind == kind)
    edit_entry(p, entry, original_index="first")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dlg = TrashDialog(p)
    dlg.table.selectRow(next(i for i, e in enumerate(dlg.entries) if e.kind == kind))
    dlg._restore()
    assert dlg.restored_any and dlg.table.rowCount() == 1
    if kind == "section":
        assert [s.name for s in p.top_sections][-1] == "Keep me"
    else:
        assert [c.name for c in p.all_compounds()] == ["Benzene"]
    dlg.deleteLater()
    p.close()


def test_a_restore_error_after_moving_says_nothing_was_changed(qapp, monkeypatch):
    from si_assistant.ui.dialogs import TrashDialog
    p = deleted_section_and_compound()
    before = state_of(p)

    def locked():
        raise OSError("project.json is open in another program")
    monkeypatch.setattr(p, "save_settings", locked)   # (after the section folder has moved)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dlg = TrashDialog(p)
    dlg.table.selectRow(next(i for i, e in enumerate(dlg.entries) if e.kind == "section"))
    dlg._restore()
    monkeypatch.undo()
    assert "open in another program" in warned[0] and "nothing was changed" in warned[0]
    assert state_of(p) == before
    dlg.deleteLater()
    p.close()


@pytest.mark.parametrize("fault", [OSError, ValueError])
def test_a_restore_that_was_taken_back_cannot_be_redone(qapp, monkeypatch, fault):
    """The half-finished restore is not left on the redo list: redoing it would bring the
    section back but leave an empty entry in the Trash."""
    from si_assistant.ui.dialogs import TrashDialog
    p = deleted_section_and_compound()
    undo_label = p.journal.undo_label

    def failing():
        raise fault("project.json is open in another program")
    monkeypatch.setattr(p, "save_settings", failing)   # (after the section folder has moved)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    dlg = TrashDialog(p)
    dlg.table.selectRow(next(i for i, e in enumerate(dlg.entries) if e.kind == "section"))
    if fault is OSError:
        dlg._restore()
    else:
        with pytest.raises(ValueError):
            dlg._restore()
    monkeypatch.undo()
    assert not p.can_redo and p.journal.redo_label == ""
    assert p.journal.undo_label == undo_label   # (the history before it is untouched)
    assert not any(f.name.isdigit() and f.name not in {s.id for s in p.journal.undo_stack}
                   for f in p.journal.folder.iterdir())   # (no leftover step folder)
    dlg.deleteLater()
    p.close()


# ---------------------------------------------------------------- start-up

def test_main_puts_back_the_library_path_after_creating_the_application():
    import inspect
    from si_assistant import __main__ as main_module
    source = inspect.getsource(main_module.main)
    assert source.index("QApplication(sys.argv)") < source.index("forget_bundle_library_path()")


def test_main_puts_back_the_library_path_after_the_scale_variable():
    import inspect
    from si_assistant import __main__ as main_module
    source = inspect.getsource(main_module.main)
    assert source.index("forget_scale_variable()") < source.index("forget_bundle_library_path()")


# ---------------------------------------------------------------- throw-away settings for tests

SETTINGS_PROBE = r"""
import json, sys
from si_assistant import APP_NAME
from si_assistant import __main__ as m
from si_assistant.ui import carry_over, restart, text_checks
if sys.argv[1] != "-":
    m.use_settings_folder(sys.argv[1])
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
app = QApplication([])
app.setApplicationName(APP_NAME)
app.setOrganizationName(APP_NAME)
print(json.dumps({
    "restart": restart.settings().fileName(),
    "text_checks": text_checks._settings().fileName(),
    "old_name": carry_over.QSettings(carry_over.OLD_APP_NAME, carry_over.OLD_APP_NAME).fileName(),
    "imported_later": QSettings("Org", "Prog").fileName(),
    "scoped": QSettings(QSettings.Scope.UserScope, "Org", "Prog").fileName(),
    "own_file": QSettings(sys.argv[2], QSettings.Format.IniFormat).fileName(),
    "dictionary": str(text_checks.personal_dictionary_path()),
    "old_app_folder": str(carry_over.app_data_folder(carry_over.OLD_APP_NAME)),
    "native": QSettings(APP_NAME, APP_NAME).format().name,
}))
"""


def run_settings_probe(folder, own_file):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("SI_ASSISTANT_SETTINGS_DIR", None)
    result = subprocess.run([sys.executable, "-c", SETTINGS_PROBE, str(folder), str(own_file)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_settings_folder_keeps_every_setting_in_the_test_folder(tmp_path):
    """Nothing is written here: only where each setting / file would go is looked at."""
    from pathlib import Path
    folder = tmp_path / "prefs"
    own = tmp_path / "mine.ini"
    found = run_settings_probe(folder, own)
    inside = [k for k in found if k not in ("own_file", "native")]
    for key in inside:
        assert Path(found[key]).resolve().is_relative_to(folder.resolve()), (key, found[key])
    assert found["old_name"] != found["restart"]   # old and new name stay apart
    assert found["old_app_folder"] != str(Path(found["dictionary"]).parent)
    assert Path(found["own_file"]).resolve() == own.resolve()   # an explicit file: unchanged
    assert found["native"] == "IniFormat"


def test_without_the_settings_folder_the_real_settings_are_used(tmp_path):
    """A normal start-up: the program's settings are where they always were (only looked at)."""
    found = run_settings_probe("-", tmp_path / "mine.ini")
    assert found["native"] == "NativeFormat"
    assert str(tmp_path) not in found["restart"] and str(tmp_path) not in found["dictionary"]
