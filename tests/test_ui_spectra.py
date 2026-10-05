"""The Spectra section in the window: its page of defaults, the tree item that cannot be
deleted, and the Compile window's tick box (SPEC §13k.12)."""

import json

import pytest
from PySide6.QtCore import QSettings

from si_assistant.core import front
from si_assistant.core import spectra as S
from si_assistant.core.project import Project
from tests.sample_project import build_sample


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def test_the_page_saves_the_defaults(sample):
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, parts = sample
    panel = SpectraSectionPanel(p, parts["spectra"])
    saved = []
    panel.saved.connect(saved.append)
    assert panel.values() == S.DEFAULTS                  # the defaults as stored
    assert not panel.id_size.isEnabled() and not panel.struct_colour.isEnabled()
    panel.per_page.setCurrentIndex(panel.per_page.findData(4))
    panel.orientation.setCurrentIndex(panel.orientation.findData("landscape"))
    panel.caption.setChecked(True)
    panel.same_size.setChecked(False)
    assert panel.id_size.isEnabled()
    panel.id_size.setValue(9.5)
    panel.struct_frame.setChecked(True)
    assert panel.struct_colour.isEnabled()
    panel.struct_colour.set_colour("#AA0000")
    panel.struct_colour.changed.emit("#aa0000")
    panel.scale.setValue(60)
    assert panel.sample.values["per_page"] == 4          # the sketch follows at once
    panel.flush()
    assert saved == ["Saved"]
    d = Project.open(p.folder).spectra_defaults()
    assert (d["per_page"], d["orientation"], d["caption"], d["id_size"]) == \
        (4, "landscape", True, 9.5)
    assert d["struct_frame"] and d["struct_frame_color"] == "#aa0000"
    assert d["struct_scale"] == 0.6
    panel.sample.grab()                                  # the sketch draws without error
    panel.deleteLater()


def test_a_narrow_page_scrolls_sideways(sample, qapp):
    """In a narrow window (e.g. with the live preview open) nothing is cut off: the page
    scrolls sideways to reach the controls on the right."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, parts = sample
    panel = SpectraSectionPanel(p, parts["spectra"])
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.resize(300, 600)
    panel.show()
    qapp.processEvents()
    scroll = panel.findChild(QScrollArea)
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.horizontalScrollBar().maximum() > 0
    panel.close()
    panel.deleteLater()


def test_apply_defaults_to_all_asks_first(sample, monkeypatch):
    from si_assistant.ui import spectra_panel
    p, parts = sample
    c2 = parts["c2"]
    p.set_spectrum_overrides(c2, "spectra", c2.values["spectra"][0]["file"], {"per_page": 2})
    panel = spectra_panel.SpectraSectionPanel(p, parts["spectra"])
    assert "1 spectrum file" in panel.overridden.text()
    answers = iter([spectra_panel.QMessageBox.StandardButton.No,
                    spectra_panel.QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(spectra_panel.QMessageBox, "question", lambda *a, **k: next(answers))
    panel.apply_to_all()                                 # "No": nothing changes
    assert S.count_overridden(p) == 1
    panel.apply_to_all()
    assert S.count_overridden(p) == 0
    assert "follows these defaults" in panel.overridden.text()
    panel.deleteLater()


def test_a_read_only_page_cannot_be_changed(sample):
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, parts = sample
    ro = Project.open(p.folder, read_only=True)
    panel = SpectraSectionPanel(ro, ro.spectra_section)
    assert not panel.per_page.isEnabled() and not panel.apply_btn.isEnabled()
    panel.flush()
    panel.deleteLater()


def test_the_main_window_adds_and_protects_the_section(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "Old")            # an older project: no Spectra section
    data = p.add_section("data", "Products")
    p.settings["spectra_per_page"] = 2
    p.save_settings()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    spectra = win.project.spectra_section
    assert spectra is not None and win.project.spectra_defaults()["per_page"] == 2
    assert win._current() == ("section", data.id)        # still opens on the first section
    item = win._find_item("section", spectra.id)
    assert item is not None and "not deleted" in item.toolTip(0)
    win.select("section", spectra.id)
    assert isinstance(win.panel, SpectraSectionPanel)
    assert win.preview_pane.target == ("section", spectra.id)
    assert not win.act_delete.isEnabled() and "cannot be deleted" in win.act_delete.toolTip()
    assert win.act_rename.isEnabled() and win.act_up.isEnabled()
    told = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    win.delete_item()                                    # (e.g. from a shortcut): explained
    assert told and "cannot be deleted" in told[0]
    assert win.project.spectra_section is spectra and win.project.list_trash() == []
    win.move_item(-1)                                    # it can be moved
    assert [s.id for s in Project.open(p.folder).top_sections] == [spectra.id, data.id]
    win.select("section", data.id)
    assert win.act_delete.isEnabled()
    win.close_project()
    win.close()


def test_a_new_project_opens_on_the_front_matter(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "New")
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    assert [s.type for s in win.project.sections] == ["spectra"]
    assert win._current() == ("front_root", "front")
    win.close_project()
    win.close()


def test_the_compile_window_ticks_the_spectra_section(sample):
    from si_assistant.ui.compile_ui import CompileDialog
    p, parts = sample
    spectra = parts["spectra"]
    front.store_excluded(p.settings, ["spectra"])        # an older project's choice
    dlg = CompileDialog(p)
    tree = dlg.export
    assert front.SPECTRA_KEY not in tree._items          # the section stands for it
    assert not tree.is_included(spectra.id)
    assert tree.excluded() == {spectra.id}
    tree.set_all(True)
    assert tree.is_included(spectra.id)
    assert not hasattr(dlg, "per_page")                  # moved to the section's page
    dlg.remember()
    saved = json.loads((p.folder / "project.json").read_text(encoding="utf-8"))
    assert "export_excluded" not in saved and "spectra_per_page" not in saved
    dlg.deleteLater()
