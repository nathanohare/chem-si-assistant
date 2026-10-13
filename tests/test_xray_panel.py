"""X-ray structures, phase X2 (SPEC §13v): the X-ray structure section's page and its
place in the tree — choosing the CIF and the compound, the drawing saved as it is
changed, captions (automatic or typed), the crystal data table (rows left out, values
typed over); put under a compound by dropping or from the menu, moved back out, undone.
Made-up projects; the CIF is a real public-domain one."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QAbstractItemView, QInputDialog, QMessageBox

from si_assistant.core.project import Project

CIF = Path(__file__).parent / "data" / "cif" / "naphthalene_cod_2311090.cif"
DIP = QAbstractItemView.DropIndicatorPosition


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    said = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: said.append(a[2])))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: said.append(a[2])))
    p = Project.create(tmp_path / "proj", "X-ray")
    p.ensure_spectra_section()
    data = p.add_section("data", "Products", 0)
    a = p.add_compound(data.id, name="Naphthalene")
    b = p.add_compound(data.id, name="Anthracene")
    p.set_id_table(data.id, ["1a", "1b"])
    s = p.add_section("xray", "Crystal structure")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    win.select("section", s.id)
    yield win, s.id, a.id, b.id, said
    win.close_project()
    win.close()


def page(win):
    from si_assistant.ui.xray_panel import XraySectionPanel
    assert isinstance(win.panel, XraySectionPanel)
    return win.panel


def test_a_new_page_asks_for_its_cif_then_shows_the_structure(window):
    win, sid, a, _b, said = window
    panel = page(win)
    assert panel.editor.grown is None and "Choose CIF" in panel.choose_btn.text()
    assert "No CIF file" in panel.editor.canvas.empty_text
    assert any("X-ray structure “Crystal structure”" in w.message and "No CIF" in w.message
               for w in __import__("si_assistant.core.checks", fromlist=["x"])
               .project_warnings(win.project))
    panel.attach(CIF)
    panel = page(win)
    section = win.project.section(sid)
    assert section.data["file"] == CIF.name and not said
    assert panel.editor.grown is not None and panel.choose_btn.text() == "Replace…"
    assert "18 atoms" in panel.editor.status.text()
    # the crystal data table: every row, those the CIF lacks empty (to be typed in)
    rows = {panel.table.item(r, 1).text(): panel.table.item(r, 2).text()
            for r in range(panel.table.rowCount())}
    assert rows["Empirical formula"] == "C₁₀H₈" and "CCDC deposition number" in rows
    # automatic captions, numbered
    assert panel.caption_auto.isChecked() and panel.figure_label.text() == "Caption of Figure S1:"
    assert "Molecular structure" in panel.caption_edit.markup()
    assert panel.table_label.text() == "Caption of Table S1:"


def test_the_drawing_is_saved_as_it_is_changed_and_the_caption_follows(window):
    win, sid, a, _b, _said = window
    panel = page(win)
    panel.attach(CIF)
    section = win.project.section(sid)
    assert section.data["view"] == {}                  # (nothing changed yet: the best view)
    panel.editor.hydrogens.setChecked(False)
    assert panel._view_timer.isActive()
    assert "hydrogen atoms omitted" in panel.caption_edit.markup()
    panel.flush()
    assert section.data["view"]["hydrogens"] == "hide"
    panel.editor.ball.setChecked(True)
    panel.flush()
    assert section.data["view"]["style"] == "ball"
    assert "ball-and-stick" in panel.caption_edit.markup()
    # shown again: as saved
    win.select("compound", a)
    win.select("section", sid)
    panel = page(win)
    assert panel.editor.ball.isChecked() and not panel.editor.hydrogens.isChecked()


def test_the_compound_and_typed_captions(window):
    win, sid, a, _b, _said = window
    panel = page(win)
    panel.attach(CIF)
    box = panel.compound_box
    assert box.itemText(0) == "(none)" and box.itemText(1) == "1a — Naphthalene"
    box.setCurrentIndex(1)
    box.activated.emit(1)
    section = win.project.section(sid)
    assert section.data["compound"] == a
    assert f"@{{{a}}}" in panel.caption_edit.markup()            # (its ID follows renumbering)
    assert f"@{{{a}}}" in panel.table_caption_edit.markup()
    # typed: starts from the automatic text
    panel.caption_auto.setChecked(False)
    assert section.data["caption"].startswith("Molecular structure of")
    assert not panel.caption_edit.edit.isReadOnly()
    panel.caption_edit.set_markup("ORTEP of the *major* conformer.")
    panel.caption_edit.changed.emit()
    panel.flush()
    assert section.data["caption"] == "ORTEP of the *major* conformer."
    panel.caption_auto.setChecked(True)                           # back to automatic
    assert section.data["caption"] == "" and panel.caption_edit.edit.isReadOnly()


def test_the_table_rows_left_out_and_values_typed_over(window):
    win, sid, _a, _b, _said = window
    panel = page(win)
    panel.attach(CIF)
    section = win.project.section(sid)
    t = panel.table
    row = {t.item(r, 1).text(): r for r in range(t.rowCount())}
    t.item(row["Identification code"], 0).setCheckState(Qt.CheckState.Unchecked)
    qapp_process()                                     # (the table is shown again)
    assert section.data["table_hidden"] == ["code"]
    assert t.item(row["Identification code"], 0).checkState() == Qt.CheckState.Unchecked
    t.item(row["CCDC deposition number"], 2).setText("2345678")   # (not in this CIF)
    qapp_process()
    assert section.data["table_values"] == {"ccdc": "2345678"}
    assert t.item(row["CCDC deposition number"], 2).font().bold()
    assert "CCDC 2345678." in panel.caption_edit.markup()
    t.item(row["Temperature/K"], 2).setText("100(2)")
    qapp_process()
    assert section.data["table_values"]["temperature"] == "100(2)"
    t.selectRow(row["Temperature/K"])
    assert panel.restore_btn.isEnabled()
    panel.restore_btn.click()
    assert section.data["table_values"] == {"ccdc": "2345678"}
    assert t.item(row["Temperature/K"], 2).text() == "80(2)"
    # typed in as shown (unchanged): nothing stored
    formula = t.item(row["Empirical formula"], 2)
    formula.setText(formula.text())
    qapp_process()
    assert "formula" not in section.data["table_values"]
    # not printed at all
    panel.print_table.setChecked(False)
    assert section.data["table"] is False and not panel.table.isEnabled()
    assert panel.table_label.text() == "Table caption:"     # (no number: not printed)


def test_put_under_a_compound_and_back_out(window):
    win, sid, a, b, _said = window
    section = win.project.section(sid)
    tree = win.tree
    dragged = win._find_item("section", sid)
    target = win._find_item("compound", a)
    assert tree._section_drop(dragged, target, DIP.OnItem) == ("anchor", a, 0)
    assert tree._section_drop(dragged, target, DIP.AboveItem) == ("anchor", a, 0)
    # (another kind of section dropped on a compound: after its Data section, as before)
    data_item = target.parent()
    assert tree._section_drop(data_item, target, DIP.OnItem)[0] == "section"
    win._on_anchor_dropped(sid, a, 0)
    qapp_process()
    item = win._find_item("section", sid)
    assert item.parent() is win._find_item("compound", a) and tree.is_anchored(item)
    assert item.data(0, Qt.ItemDataRole.UserRole + 2) == "xray"
    assert section.anchored_to == a and section.data["compound"] == a
    # the page says so; the compound chooser has no "(none)" any more
    win.select("section", sid)
    panel = page(win)
    assert "Printed right after 1a" in panel.place_note.text()
    assert panel.compound_box.itemText(0) == "1a — Naphthalene"
    # chosen another compound on the page: it moves under that one
    panel.compound_box.setCurrentIndex(1)
    panel.compound_box.activated.emit(1)
    assert win.project.section(sid).anchored_to == b
    assert win._find_item("section", sid).parent() is win._find_item("compound", b)
    # undo: back under 1a
    win.undo()
    qapp_process()
    assert win.project.section(sid).anchored_to == a
    # dropped back into the tree, after the Spectra section
    spectra = win._find_item("section", win.project.spectra_section.id)
    spot = tree._section_drop(win._find_item("section", sid), spectra, DIP.BelowItem)
    assert spot[0] == "section" and spot[1] == ""
    win._on_section_dropped(sid, spot[1], spot[2])
    qapp_process()
    section = win.project.section(sid)
    assert not section.anchored_to and win.project.anchored == {}
    assert [s.name for s in win.project.top_sections][-1] == "Crystal structure"


def test_menus_new_section_under_a_compound_and_moving(window, monkeypatch):
    win, sid, a, b, _said = window
    act = win.act_new_section["xray"]
    assert act.text() == "New X-ray structure section" and "CIF" in act.toolTip()
    assert "X-ray structure sub-section" in [x.text() for x in win.act_new_sub.values()]
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (k.get("text", "X"), True)))
    win.select("compound", b)
    act.trigger()
    new = win.project.anchored[b][0]
    assert new.name == "X-ray Structure" and new.data["compound"] == b
    assert win.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == new.id
    # right-click ▸ Put under compound ▸ 1a; ▸ Move out to the top level
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    win._add_section_move_menu(menu, new.id)
    under = next(x.menu() for x in menu.actions() if x.text() == "Put under compound")
    assert [x.text() for x in under.actions()] == ["1a   Naphthalene"]
    under.actions()[0].trigger()
    qapp_process()
    assert [x.id for x in win.project.anchored[a]] == [new.id]
    menu = QMenu()
    win._add_section_move_menu(menu, new.id)
    out = next(x for x in menu.actions() if x.text() == "Move out to the top level")
    out.trigger()
    qapp_process()
    names = [s.name for s in win.project.top_sections]
    assert names.index("X-ray Structure") == names.index("Products") + 1


def test_moving_up_and_down_under_a_compound(window, monkeypatch):
    win, sid, a, _b, _said = window
    p = win.project
    other = p.add_section("xray", "Second")
    p.anchor_section(sid, a)
    p.anchor_section(other.id, a)
    win._populate_tree(("section", other.id))
    win.move_item(-1)
    assert [x.name for x in p.anchored[a]] == ["Second", "Crystal structure"]
    assert win.project.section(other.id).anchored_to == a
    # deleting the last one under a compound selects the compound
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.select("section", sid)
    win.delete_item()
    win.select("section", other.id)
    win.delete_item()
    qapp_process()
    assert p.anchored == {}
    assert win.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == a


def test_the_compile_window_lists_a_structure_under_its_compound(window):
    from si_assistant.ui.compile_ui import ExportTree
    win, sid, a, _b, _said = window
    win.project.anchor_section(sid, a)
    tree = ExportTree(win.project, set())
    item = tree._items[sid]
    assert item.text(0).startswith("Crystal structure") and "after 1a" in item.text(0)
    assert item.parent() is tree._items[win.project.data_sections()[0].id]


def qapp_process():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
