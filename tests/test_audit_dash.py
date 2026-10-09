"""Audit fixes for the Data-section page and its dashboard (group "dash"): only the tab
on show is rebuilt, no thumbnails in the compact view, a thumbnail cache that does not
grow, tooltip pictures without temp files, and an ID cell being typed in is saved
before Undo/Redo. Made-up data only."""

import dataclasses
import os
import re
import tempfile

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox

from si_assistant.core.project import Project


def new_project(tmp_path, n=3):
    p = Project.create(tmp_path, "P")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, [f"3{chr(97 + i)}" for i in range(n)])
    compounds = [p.add_compound(data.id, name=f"Compound {i}") for i in range(n)]
    for c in compounds:
        p.set_structure(c, "CCO")
    return p, data, compounds


@pytest.fixture
def dashboard_calls(monkeypatch):
    """Counts DashboardView.refresh calls (it still runs)."""
    from si_assistant.ui.dashboard_view import DashboardView
    calls = []
    original = DashboardView.refresh

    def counting(self, *a, **k):
        calls.append(self)
        return original(self, *a, **k)
    monkeypatch.setattr(DashboardView, "refresh", counting)
    return calls


# ---------------------------------------------------------------------- tab switches

def test_switching_tabs_rebuilds_only_the_tab_shown(qapp, tmp_path, dashboard_calls):
    from si_assistant.ui.data_panel import DataSectionPanel
    p, data, cs = new_project(tmp_path)
    panel = DataSectionPanel(p, data)
    assert len(dashboard_calls) == 1                 # opening the page fills the dashboard
    for tab in (1, 2, 3):
        panel.tabs.setCurrentIndex(tab)
    assert len(dashboard_calls) == 1                 # no dashboard rebuild behind the tabs
    # The ID table is rebuilt from the project when it is shown.
    p.set_id_table(data.id, ["7a", "7b", "7c"])
    panel.tabs.setCurrentIndex(1)
    assert panel.id_table.item(0, 0).text() == "7a"
    panel.tabs.setCurrentIndex(0)
    assert len(dashboard_calls) == 2
    assert panel.dashboard.table.item(0, 0).text().startswith("7a")
    p.close()


def test_an_id_edit_refills_the_id_table_and_the_dashboard_when_shown(qapp, tmp_path,
                                                                     dashboard_calls):
    from si_assistant.ui.data_panel import DataSectionPanel
    p, data, cs = new_project(tmp_path)
    panel = DataSectionPanel(p, data)
    panel.tabs.setCurrentIndex(1)
    before = len(dashboard_calls)
    panel.id_table.item(0, 0).setText("NEW1")        # (as typing in the cell does)
    assert p.section(data.id).id_table[0] == "NEW1"
    assert panel.id_table.item(0, 0).text() == "NEW1"
    assert "Compound 0" in panel.id_table.item(0, 1).text()
    assert len(dashboard_calls) == before            # the hidden dashboard waits
    panel.tabs.setCurrentIndex(0)
    assert panel.dashboard.table.item(0, 0).text().startswith("NEW1")
    # A cell committed after the switch to the Dashboard still shows there at once.
    panel._save_table(["NEW2", "3b", "3c"])
    assert panel.dashboard.table.item(0, 0).text().startswith("NEW2")
    assert panel.id_table.item(0, 0).text() == "NEW2"
    p.close()


def test_add_row_still_edits_the_new_row(qapp, tmp_path):
    from si_assistant.ui.data_panel import DataSectionPanel
    p, data, cs = new_project(tmp_path)
    panel = DataSectionPanel(p, data)
    panel.tabs.setCurrentIndex(1)
    panel._add_row()
    assert panel.id_table.rowCount() == 4 and panel.id_table.currentRow() == 3
    assert p.section(data.id).id_table == ["3a", "3b", "3c", ""]
    p.close()


# ---------------------------------------------------------------------- compact view

def test_compact_view_draws_no_thumbnails(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import dashboard_view as dv
    p, data, cs = new_project(tmp_path)
    drawn = []
    original = dv.thumbnail
    monkeypatch.setattr(dv, "thumbnail", lambda *a, **k: drawn.append(1) or original(*a, **k))
    view = dv.DashboardView(p, data)
    view.compact_box.setChecked(True)
    drawn.clear()
    view.refresh()
    assert drawn == []
    s_item = view.table.item(0, 1)
    assert s_item.data(Qt.ItemDataRole.DecorationRole) is None
    assert "no structure" not in s_item.text()       # (hidden, but not wrong)
    assert "<img" in view.tooltip_for(view.table.item(0, 0))   # structure on hover
    view.compact_box.setChecked(False)               # the thumbnails come back
    assert not view.table.isColumnHidden(1)
    assert isinstance(view.table.item(0, 1).data(Qt.ItemDataRole.DecorationRole), QPixmap)
    assert len(drawn) == len(cs)
    p.close()


# ---------------------------------------------------------------------- thumbnail cache

def _touch(path, step):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + step * 1_000_000_000))


def test_thumbnail_cache_keeps_one_picture_per_file(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import dashboard_view as dv
    monkeypatch.setattr(dv, "_thumb_cache", {})
    p, data, cs = new_project(tmp_path, n=1)
    c = cs[0]
    first = dv.thumbnail(p, c, 1.0)
    assert dv.thumbnail(p, c, 1.0) is first          # cached
    mol = p.structure_path(c)
    for step in (1, 2, 3):                           # the file changes three times
        _touch(mol, step)
        assert dv.thumbnail(p, c, 1.0) is not first
    assert len(dv._thumb_cache) == 1
    dv.thumbnail(p, c, 1.5)                          # another screen scale: its own entry
    assert len(dv._thumb_cache) == 2
    # A changed structure is drawn again (not an old picture of the same file).
    p.set_structure(c, "c1ccccc1CCCCCC(=O)O")
    new = dv.thumbnail(p, c, 1.0)
    assert new.toImage() != first.toImage() and len(dv._thumb_cache) == 2
    # Other drawing settings draw it again too, still in one entry.
    other = dataclasses.replace(dv.drawing_settings(p), bond_length_cm=0.8)
    monkeypatch.setattr(dv, "drawing_settings", lambda project: other)
    restyled = dv.thumbnail(p, c, 1.0)
    assert restyled is not new and len(dv._thumb_cache) == 2
    assert dv.thumbnail(p, c, 1.0) is restyled
    p.close()


def test_thumbnail_after_undo_shows_the_old_structure(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import dashboard_view as dv
    monkeypatch.setattr(dv, "_thumb_cache", {})
    p, data, cs = new_project(tmp_path, n=1)
    c = cs[0]
    old = dv.thumbnail(p, c, 1.0).toImage()
    with p.undo_step("Edit the structure"):
        p.set_structure(p.compound(c.id), "c1ccccc1CCCCCC(=O)O")
    changed = dv.thumbnail(p, p.compound(c.id), 1.0).toImage()
    assert changed != old
    p.undo()
    assert dv.thumbnail(p, p.compound(c.id), 1.0).toImage() == old
    assert len(dv._thumb_cache) == 1
    p.close()


# ---------------------------------------------------------------------- tooltips

def test_structure_tooltip_needs_no_temp_files(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import dashboard_view as dv
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr(dv, "_tooltip_cache", {})
    p, data, cs = new_project(tmp_path / "proj", n=1)
    c = cs[0]
    tip = dv.structure_tooltip(p, c)
    assert tip.startswith("<b>Compound 0</b><br><img src='data:image/png;base64,")
    assert list(temp.iterdir()) == []                # nothing written to the temp folder
    assert dv.structure_tooltip(p, c) == tip         # cached
    # The picture really shows in a tooltip (a broken image is a tiny placeholder).
    label = QLabel(tip)
    assert label.sizeHint().height() > 150 and label.sizeHint().width() > 200
    # A changed structure gives a new picture, in the same single entry.
    p.set_structure(c, "c1ccccc1CCCCCC(=O)O")
    assert dv.structure_tooltip(p, c) != tip
    assert len(dv._tooltip_cache) == 1
    p.close()


def test_structure_tooltip_without_a_picture(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import dashboard_view as dv
    monkeypatch.setattr(dv, "_tooltip_cache", {})
    p, data, cs = new_project(tmp_path, n=1)
    c = p.add_compound(data.id, name="Bare")
    assert dv.structure_tooltip(p, c) == "<b>Bare</b><br><i>no structure yet</i>"
    # A picture that cannot be drawn gives the name alone, no broken image.
    monkeypatch.setattr(dv, "mol_pixmap", lambda *a, **k: QPixmap())
    assert dv.structure_tooltip(p, cs[0]) == "<b>Compound 0</b>"
    p.close()


def test_a_picture_that_cannot_be_read_for_a_moment_is_tried_again(qapp, tmp_path,
                                                                    monkeypatch):
    """A OneDrive picture not yet downloaded (or locked by another program) is not
    remembered as 'no picture': once readable (same file date and size) it shows."""
    from PySide6.QtGui import QColor, QImage
    from si_assistant.ui import dashboard_view as dv
    from si_assistant.ui import structure_widgets as sw
    monkeypatch.setattr(dv, "_thumb_cache", {})
    monkeypatch.setattr(dv, "_tooltip_cache", {})
    p, data, cs = new_project(tmp_path / "proj", n=1)
    c = cs[0]
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    src = tmp_path / "pic.png"
    img.save(str(src))
    p.set_structure_image(c, source=src)
    picture = p.structure_image_path(c)
    stamp = (picture.stat().st_mtime_ns, picture.stat().st_size)
    real = sw.read_image
    monkeypatch.setattr(sw, "read_image", lambda path: QImage())   # cannot be read
    assert dv.structure_tooltip(p, c) == "<b>Compound 0</b>"
    assert dv.thumbnail(p, c, 1.0) is None
    assert dv._tooltip_cache == {} and dv._thumb_cache == {}
    monkeypatch.setattr(sw, "read_image", real)                    # readable again
    assert (picture.stat().st_mtime_ns, picture.stat().st_size) == stamp
    assert dv.structure_tooltip(p, c).startswith("<b>Compound 0</b><br><img ")
    thumb = dv.thumbnail(p, c, 1.0)
    assert thumb is not None and not thumb.isNull()
    assert dv.thumbnail(p, c, 1.0) is thumb                        # now kept
    p.close()


# ---------------------------------------------------------------------- ID cell + Undo/Redo

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
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        win.show()
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def _type_in_id_cell(qapp, win, data_id, text):
    """Open the editor on the first ID cell and type, without pressing Enter."""
    win.select("section", data_id)
    panel = win.panel
    panel.tabs.setCurrentIndex(1)
    table = panel.id_table
    table.setCurrentCell(0, 0)
    table.editItem(table.item(0, 0))
    qapp.processEvents()
    editor = table.indexWidget(table.model().index(0, 0))
    assert editor is not None
    editor.setText(text)
    return panel


def _settings_step_undone(win):
    with win._step("Change project settings"):
        win.project.settings["hrms_tolerance_ppm"] = 3.0
        win.project.save_settings()
    win.undo()
    assert win.act_redo.text() == "&Redo Change project settings"


def test_redo_with_an_id_cell_being_typed_in(qapp, window, tmp_path, monkeypatch):
    p, data, cs = new_project(tmp_path)
    win = window(p)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[1:3])))
    _settings_step_undone(win)
    _type_in_id_cell(qapp, win, data.id, "2zz")
    win.redo()
    assert warnings == []                            # no "There is nothing to redo"
    assert win.project.section(data.id).id_table[0] == "2zz"   # the typing is kept
    assert re.match("&Undo Edit the ID table", win.act_undo.text())
    assert not win.act_redo.isEnabled()              # (a new edit clears Redo)
    assert win.panel is not None


def test_undo_with_an_id_cell_being_typed_in(qapp, window, tmp_path, monkeypatch):
    p, data, cs = new_project(tmp_path)
    win = window(p)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[1:3])))
    _type_in_id_cell(qapp, win, data.id, "2zz")
    win.undo()                                       # undoes the typing just saved
    assert warnings == []
    assert win.project.section(data.id).id_table == ["3a", "3b", "3c"]
    assert re.match("&Redo Edit the ID table", win.act_redo.text())
    win.redo()
    assert win.project.section(data.id).id_table[0] == "2zz"


def test_flush_saves_an_id_cell_being_typed_in(qapp, tmp_path):
    from si_assistant.ui.data_panel import DataSectionPanel
    p, data, cs = new_project(tmp_path)
    panel = DataSectionPanel(p, data)
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.show()
    panel.tabs.setCurrentIndex(1)
    table = panel.id_table
    table.setCurrentCell(2, 0)
    table.editItem(table.item(2, 0))
    qapp.processEvents()
    table.indexWidget(table.model().index(2, 0)).setText("9z")
    panel.flush()
    assert p.section(data.id).id_table == ["3a", "3b", "9z"]
    assert table.state() != table.State.EditingState
    assert (table.currentRow(), table.currentColumn()) == (2, 0)
    panel.flush()                                    # nothing open: nothing happens
    assert p.section(data.id).id_table == ["3a", "3b", "9z"]
    panel.close()
    p.close()


# ---------------------------------------------------------------------- columns menu

def test_columns_menu_keeps_an_ampersand_in_a_field_name(qapp, tmp_path):
    from si_assistant.core import dashboard as D
    from si_assistant.ui.dashboard_view import DashboardView
    p, data, cs = new_project(tmp_path)
    p.add_field(cs[0], {"key": "custom-ye", "custom": {"label": "Yield & ee",
                                                       "type": "short_text"}})
    view = DashboardView(p, p.section(data.id))
    view._fill_columns_menu()
    act = next(a for a in view.columns_menu.actions() if a.text().endswith(" ee"))
    assert act.text() == "Yield && ee"               # shows as "Yield & ee", no Alt+ key
    assert act.isChecked()
    act.setChecked(False)                            # the toggle still uses the field key
    assert "custom-ye" not in D.chosen_columns(view.section)
    view._fill_columns_menu()
    act = next(a for a in view.columns_menu.actions() if a.text().endswith(" ee"))
    act.setChecked(True)
    assert "custom-ye" in D.chosen_columns(view.section)
    p.close()


def test_compact_view_shows_structure_warnings_on_the_id(qapp, tmp_path, monkeypatch):
    """The compact view hides the structure column, so the structure's own warnings
    show on the ID cell (marker, hover text and background); the full view keeps them
    on the structure cell only."""
    from si_assistant.ui import dashboard_view as dv
    p, data, cs = new_project(tmp_path)
    view = dv.DashboardView(p, data)
    original = view._rows

    def rows_with_a_structure_warning():
        rows = original()
        rows[0].cells["structure"].warnings = ["Structure has a generic atom <R>"]
        return rows
    monkeypatch.setattr(view, "_rows", rows_with_a_structure_warning)
    view.compact_box.setChecked(True)
    view.refresh()
    id_item = view.table.item(0, 0)
    assert id_item.text().endswith("⚠")
    assert "generic atom &lt;R&gt;" in id_item.data(dv.STRUCT_TIP_ROLE)
    assert id_item.background().style() != Qt.BrushStyle.NoBrush
    assert not view.table.item(1, 0).text().endswith("⚠")   # other rows unchanged
    view.compact_box.setChecked(False)
    view.refresh()
    id_item = view.table.item(0, 0)
    assert not id_item.text().endswith("⚠")
    assert id_item.data(dv.STRUCT_TIP_ROLE) == ""
    p.close()
