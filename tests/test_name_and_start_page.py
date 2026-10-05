"""The program's new name and the start page (SPEC §13l.3 and §13l.5)."""

import shutil

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QSettings

from si_assistant import APP_NAME
from si_assistant.core.project import Project


def ini(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


# ---------------------------------------------------------------------- the name (§13l.5)

def test_the_program_is_called_chem_si_assistant():
    assert APP_NAME == "Chem SI Assistant"


def test_settings_are_carried_over_once_from_the_old_name(qapp, tmp_path):
    from si_assistant.ui.carry_over import DONE_KEY, copy_settings
    old = ini(tmp_path / "old.ini")
    old.setValue("user_name", "Nathan")
    old.setValue("recent_projects", [str(tmp_path / "a"), str(tmp_path / "b")])
    old.setValue("window_geometry", QByteArray(b"\x01\x02geometry"))
    old.setValue("live_preview/shown", False)
    old.setValue("editor/chemical_formula", True)
    old.sync()
    new = ini(tmp_path / "new.ini")
    new.setValue("live_preview/width", 500)       # already set under the new name: kept

    assert copy_settings(old, new) == 5
    new = ini(tmp_path / "new.ini")
    assert new.value("user_name") == "Nathan"
    assert new.value("recent_projects") == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert bytes(new.value("window_geometry")) == b"\x01\x02geometry"
    assert str(new.value("live_preview/shown")).lower() == "false"
    assert int(new.value("live_preview/width")) == 500
    assert str(new.value(DONE_KEY)).lower() == "true"
    # Only once: later changes under the old name are not copied again.
    old.setValue("user_name", "Someone else")
    old.sync()
    new.setValue("user_name", "Nathan P.")
    assert copy_settings(old, new) == 0
    assert ini(tmp_path / "new.ini").value("user_name") == "Nathan P."
    # The old settings are left as they were.
    assert ini(tmp_path / "old.ini").value("recent_projects") == [str(tmp_path / "a"),
                                                                   str(tmp_path / "b")]


def test_existing_settings_under_the_new_name_win(qapp, tmp_path):
    from si_assistant.ui.carry_over import copy_settings
    old = ini(tmp_path / "old.ini")
    old.setValue("user_name", "Old")
    old.sync()
    new = ini(tmp_path / "new.ini")
    new.setValue("user_name", "New")
    assert copy_settings(old, new) == 0
    assert new.value("user_name") == "New"


def test_my_dictionary_is_copied_only_if_there_is_none_yet(tmp_path):
    from si_assistant.ui.carry_over import copy_file_once
    old = tmp_path / "old" / "my_dictionary.txt"
    old.parent.mkdir()
    old.write_text("Suzuki\nEtOAc\n", encoding="utf-8")
    new = tmp_path / "new" / "deeper" / "my_dictionary.txt"
    assert copy_file_once(old, new)
    assert new.read_text(encoding="utf-8") == "Suzuki\nEtOAc\n"
    new.write_text("mine\n", encoding="utf-8")
    assert not copy_file_once(old, new)               # never overwritten
    assert new.read_text(encoding="utf-8") == "mine\n"
    assert old.exists()                               # the old one stays
    assert not copy_file_once(tmp_path / "missing.txt", tmp_path / "x.txt")


def test_app_data_folder_follows_the_name_and_leaves_the_app_alone(qapp):
    from si_assistant.ui.carry_over import OLD_APP_NAME, app_data_folder
    before = (QCoreApplication.applicationName(), QCoreApplication.organizationName())
    old, new = app_data_folder(OLD_APP_NAME), app_data_folder(APP_NAME)
    assert old is not None and new is not None and old != new
    assert OLD_APP_NAME in str(old) and APP_NAME in str(new)
    assert (QCoreApplication.applicationName(), QCoreApplication.organizationName()) == before


# ---------------------------------------------------------------------- start page (§13l.3)

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings", lambda *a: ini(tmp_path / "prefs.ini"))
    wins = []

    def make():
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def test_window_title_and_about_use_the_new_name(window):
    from PySide6.QtWidgets import QLabel
    win = window()
    assert win.windowTitle() == "Chem SI Assistant"
    assert any("Chem SI Assistant" in label.text() for label in win.welcome.findChildren(QLabel))


def test_open_the_most_recent_project_from_the_start_page(window, tmp_path):
    win = window()
    assert win.welcome.last_btn.isHidden()            # no project opened yet
    p = Project.create(tmp_path, "Enamides")
    p.close()
    assert win.open_project(p.folder)
    win.close_project()
    assert not win.welcome.last_btn.isHidden()
    assert win.welcome.last_btn.text() == "Open “Enamides”"
    win.welcome.last_btn.click()
    assert win.project is not None
    assert win.project.folder.resolve() == p.folder.resolve()
    assert win.windowTitle() == "Enamides — Chem SI Assistant"
    win.close_project()
    # Its folder is gone: no button.
    shutil.rmtree(p.folder)
    win.welcome.refresh()
    assert win.welcome.last_btn.isHidden()


def test_open_last_project_at_start_option(window, tmp_path):
    win = window()
    assert not win.prefs.open_last_at_start           # off by default
    assert not win.act_open_last.isChecked() and not win.welcome.open_last_box.isChecked()
    p = Project.create(tmp_path, "Enamides")
    p.close()
    win.open_project(p.folder)
    win.close_project()
    # Off: the program starts on the start page.
    second = window()
    assert not second.open_last_project_at_start() and second.project is None
    # Ticked on the start page: the View menu shows it too, and it is remembered.
    second.welcome.open_last_box.setChecked(True)
    assert second.prefs.open_last_at_start and second.act_open_last.isChecked()
    third = window()
    assert third.act_open_last.isChecked() and third.welcome.open_last_box.isChecked()
    assert third.open_last_project_at_start() and third.project is not None
    third.close_project()
    # Unticked in the View menu: the start page's box follows.
    third.act_open_last.trigger()
    assert not third.prefs.open_last_at_start and not third.welcome.open_last_box.isChecked()
    # A project folder that is gone is not opened (and nothing is said about it).
    third.act_open_last.trigger()
    shutil.rmtree(p.folder)
    assert not window().open_last_project_at_start()


def test_view_menu_has_the_option(window):
    win = window()
    view = next(a.menu() for a in win.menuBar().actions() if a.text() == "&View")
    assert win.act_open_last in view.actions()
    assert win.act_open_last.isCheckable()
