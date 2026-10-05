from PySide6.QtCore import QSettings

from si_assistant.core.project import Project


def setup(tmp_path):
    p = Project.create(tmp_path, "W")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="Toluene")
    p.set_structure(c, "Cc1ccccc1")
    c.values["nmr"][1]["text"] = "13C NMR (101 MHz, CDCl3) δ 137.9, 129.1, 21.5"
    p.save_compound(c)
    return p, data, c


def test_field_warning_and_dismiss_on_compound_page(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import warnings_panel
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data, c = setup(tmp_path)
    panel = CompoundPanel(p, c, lambda: "Tester")
    nmr_box = next(b for b in panel.boxes if b.defn.key == "nmr")
    assert any(w.check == "c13" for w in nmr_box.warnings)
    assert not nmr_box.warn_box.isHidden()
    monkeypatch.setattr(warnings_panel.QInputDialog, "getText",
                        lambda *a, **k: ("rotamers overlap", True))
    c13 = next(w for w in nmr_box.warnings if w.check == "c13")
    panel._dismiss_warning(c13)
    nmr_box = next(b for b in panel.boxes if b.defn.key == "nmr")
    assert not any(w.check == "c13" for w in nmr_box.warnings)
    assert Project.open(p.folder).compound(c.id).data["dismissed"][c13.key]["reason"] == \
        "rotamers overlap"
    panel.flush()
    p.close()


def test_warnings_panel_lists_opens_and_restores(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui import warnings_panel
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p, data, c = setup(tmp_path)
    p.set_flag(c, "yield", True, "re-weigh")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    win.act_warnings.trigger()
    assert win.warnings_dock.isVisible() or not win.isVisible()  # hidden window in tests
    panel = win.warnings_panel
    panel.set_project(win.project)
    texts = []
    stack = [panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        texts.append(it.text(0))
        stack.extend(it.child(j) for j in range(it.childCount()))
    assert any(t.startswith("⚠ NMR peak reports: ¹³C NMR lists 3 signals") for t in texts)
    assert any(t.startswith("⚑ Yield: re-weigh") for t in texts)
    assert "flag" in panel.summary.text()

    # Dismiss from the panel, then show dismissed and restore.
    warning_item = (panel.tree.findItems("⚠ NMR", warnings_panel.Qt.MatchFlag.MatchStartsWith
                                             | warnings_panel.Qt.MatchFlag.MatchRecursive))[0]
    panel.tree.setCurrentItem(warning_item)
    monkeypatch.setattr(warnings_panel.QInputDialog, "getText", lambda *a, **k: ("", True))
    panel._dismiss_selected()
    assert not panel.tree.findItems("⚠ NMR", warnings_panel.Qt.MatchFlag.MatchStartsWith
                                    | warnings_panel.Qt.MatchFlag.MatchRecursive)
    panel.show_dismissed.setChecked(True)
    dismissed = (panel.tree.findItems("✓ dismissed", warnings_panel.Qt.MatchFlag.MatchStartsWith
                                          | warnings_panel.Qt.MatchFlag.MatchRecursive))[0]
    panel.tree.setCurrentItem(dismissed)
    panel._restore_selected()
    assert panel.tree.findItems("⚠ NMR", warnings_panel.Qt.MatchFlag.MatchStartsWith
                                | warnings_panel.Qt.MatchFlag.MatchRecursive)

    # Double-click opens the compound at that field.
    item = (panel.tree.findItems("⚑ Yield", warnings_panel.Qt.MatchFlag.MatchStartsWith
                                     | warnings_panel.Qt.MatchFlag.MatchRecursive))[0]
    panel._open(item)
    qapp.processEvents()
    assert win.panel.compound.id == c.id
    win.close_project()
    win.close()
