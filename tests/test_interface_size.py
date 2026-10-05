"""View ▸ Interface size and restarting at the same place (SPEC §13l.2). Nothing is
started for real: the launcher is replaced, and the settings are a file in tmp_path."""

import json
import sys

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from si_assistant.core.project import Project
from si_assistant.ui import restart


def ini(path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


@pytest.fixture
def startup_state(monkeypatch):
    """apply_saved_size changes module state: put it back after each test."""
    for name in ("applied_size", "scale_set_by_us", "user_scale"):
        monkeypatch.setattr(restart, name, getattr(restart, name))


# ---------------------------------------------------------------------- start-up

def test_saved_size_sets_qt_scale_factor(tmp_path, startup_state):
    store = ini(tmp_path / "prefs.ini")
    env = {}
    assert restart.apply_saved_size(env, store) == 100 and env == {}
    store.setValue(restart.SETTING, 125)
    assert restart.apply_saved_size(env, store) == 125
    assert env == {"QT_SCALE_FACTOR": "1.25"} and restart.scale_set_by_us
    # A restarted copy does not inherit it (it sets it again from the saved size).
    assert "QT_SCALE_FACTOR" not in restart.child_environment(env)
    store.setValue(restart.SETTING, 75)
    env = {}
    restart.apply_saved_size(env, store)
    assert env["QT_SCALE_FACTOR"] == "0.75"


def test_users_own_scale_factor_wins(tmp_path, startup_state):
    store = ini(tmp_path / "prefs.ini")
    store.setValue(restart.SETTING, 200)
    env = {"QT_SCALE_FACTOR": "1.5"}
    assert restart.apply_saved_size(env, store) == 150
    assert env == {"QT_SCALE_FACTOR": "1.5"} and restart.user_scale == "1.5"
    assert restart.child_environment(env)["QT_SCALE_FACTOR"] == "1.5"


@pytest.mark.parametrize("value", ["abc", "130", "", "-5"])
def test_unusable_saved_sizes_mean_normal(tmp_path, value):
    store = ini(tmp_path / "prefs.ini")
    store.setValue(restart.SETTING, value)
    assert restart.read_size(store) == 100


def test_restore_argument_and_command(monkeypatch):
    info = {"folder": "C:/Projects/Enamides – ü", "where": [["section", "s-1"]]}
    args, cwd = restart.restart_command(info)
    assert args[:3] == [sys.executable, "-m", "si_assistant"] and cwd
    assert args[3] == restart.RESTORE_ARG and json.loads(args[4]) == info
    found, rest = restart.split_restore_arg(["x", *args[3:], "y"])
    assert found == info and rest == ["x", "y"]
    assert restart.split_restore_arg(["--restore", "{broken"]) == (None, [])
    assert restart.split_restore_arg(["folder"]) == (None, ["folder"])
    # A packaged program (PyInstaller) is started directly.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args, cwd = restart.restart_command(info)
    assert args[0] == sys.executable and args[1] == restart.RESTORE_ARG and cwd is None


# ---------------------------------------------------------------------- the window

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings", lambda *a: ini(tmp_path / "prefs.ini"))
    started = []
    monkeypatch.setattr(restart, "launcher",
                        lambda args, env, cwd: started.append((args, env, cwd)))
    p = Project.create(tmp_path, "Sizes")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["1", "2", "3"])
    c1 = p.add_compound(data.id, name="first")
    scheme = p.add_scheme(data.id, name="Scheme")
    c2 = p.add_compound(data.id, name="in scheme", scheme_id=scheme.id)
    p.add_compound(data.id, name="also in scheme", scheme_id=scheme.id)
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield mw, win, p, {"data": data, "c1": c1, "scheme": scheme, "c2": c2}, started
    win.close_project()
    win.close()


def test_view_menu_lists_the_sizes(window):
    mw, win, p, parts, started = window
    texts = [a.text() for a in win.size_menu.actions()]
    assert texts[0] == "75%" and texts[2] == "100%  (normal)" and texts[-1] == "200%"
    assert len(texts) == 8 and win.act_sizes[100].isChecked()
    assert win.size_menu.menuAction() in win.menuBar().actions()[2].menu().actions()


def test_choosing_a_size_saves_it_and_restarts_where_the_user_was(window, monkeypatch,
                                                                   startup_state):
    mw, win, p, parts, started = window
    win.select("section", parts["data"].id)
    win.panel.tabs.setCurrentIndex(2)                 # "Default fields"
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    win.act_sizes[150].trigger()
    assert win.prefs.interface_size == 150 and win.act_sizes[150].isChecked()
    assert "150%" in asked[0] and "Restart now?" in asked[0]
    # Restarted: the project closed cleanly (lock released), a new copy started with
    # where the user was; this window closed.
    assert win.project is None
    assert Project.open(p.folder).current_lock() is None
    (args, env, cwd), = started
    info = json.loads(args[args.index(restart.RESTORE_ARG) + 1])
    assert info["folder"] == str(p.folder)
    assert info["where"][0] == ["section", parts["data"].id]
    assert info["page"] == {"tab": 2} and info["read_only"] is False
    # The new copy: the same project, item and tab.
    win2 = mw.MainWindow()
    assert win2.apply_restore_info(info)
    assert win2._current() == ("section", parts["data"].id)
    assert win2.panel.tabs.currentIndex() == 2
    win2.close_project()
    win2.close()


def test_restart_comes_back_to_a_scheme_compound_tab(window):
    mw, win, p, parts, started = window
    win.select("compound", parts["c2"].id)
    win.panel.set_collapsed(False)                     # the scheme picture shown
    assert win.restart_program()
    info = json.loads(started[0][0][-1])
    assert info["where"][:2] == [["compound", parts["c2"].id], ["scheme", parts["scheme"].id]]
    win2 = mw.MainWindow()
    win2.apply_restore_info(info)
    assert win2._current() == ("compound", parts["c2"].id)
    assert win2.panel.current_compound_id() == parts["c2"].id
    assert not win2.panel.collapse_btn.isChecked()
    win2.close_project()
    win2.close()


def test_a_deleted_item_falls_back_to_the_nearest_parent(window):
    mw, win, p, parts, started = window
    info = {"folder": str(p.folder), "where": [["compound", "c-gone"],
                                               ["scheme", parts["scheme"].id],
                                               ["section", parts["data"].id]],
            "preview_collapsed": True, "warnings": True}
    win.close_project()
    assert win.apply_restore_info(info)
    assert win._current() == ("scheme", parts["scheme"].id)
    assert win.preview_pane.collapsed and not win.warnings_dock.isHidden()
    # A project folder that is gone: the start page, quietly.
    win.close_project()
    assert not win.apply_restore_info({"folder": str(p.folder / "missing")})
    assert win.project is None


def test_same_size_or_no_restart(window, monkeypatch):
    mw, win, p, parts, started = window
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("no question for the size in use"))
    win.choose_interface_size(100)                     # the size in use: nothing to do
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    win.choose_interface_size(90)                      # saved, used from the next start
    assert win.prefs.interface_size == 90 and win.project is not None and not started


def test_failed_restart_reopens_the_project(window, monkeypatch):
    mw, win, p, parts, started = window
    win.select("compound", parts["c1"].id)

    def fail(*_a):
        raise OSError("no such program")

    monkeypatch.setattr(restart, "launcher", fail)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    assert not win.restart_program()
    assert warned and "could not be started again" in warned[0]
    assert win.project is not None and win._current() == ("compound", parts["c1"].id)
