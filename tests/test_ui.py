"""Smoke tests of the user interface (run without showing windows)."""

from PySide6.QtCore import QMimeData, QSettings

from si_assistant.core.project import Project


def test_rich_text_editor_roundtrip(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    ed = RichTextEditor()
    text = "# General Information\n**¹H NMR** spectra in CDCl_{3}; *J* in Hz; ^{13}C\n\nend"
    ed.set_markup(text)
    assert ed.markup() == text


def test_paste_keeps_only_simple_formatting(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    ed = RichTextEditor()
    mime = QMimeData()
    mime.setHtml('<p><span style="font-family:Arial;color:red;font-size:20pt">CDCl</span>'
                 '<sub>3</sub> <b>bold</b> <i>J</i></p>')
    ed.edit.insertFromMimeData(mime)
    assert ed.markup() == "CDCl_{3} **bold** *J*"


def test_formatting_buttons(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    from PySide6.QtGui import QTextCursor
    ed = RichTextEditor()
    ed.set_markup("CDCl3")
    cur = ed.edit.textCursor()
    cur.setPosition(4)
    cur.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    ed.edit.setTextCursor(cur)
    ed.sub_act.trigger()
    assert ed.markup() == "CDCl_{3}"
    cur.setPosition(0)
    cur.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    ed.edit.setTextCursor(cur)
    ed.edit.set_heading(1)
    assert ed.markup() == "# CDCl_{3}"


def test_formatting_shortcuts_work_while_typing(qapp):
    """Ctrl+B, Ctrl+I, Ctrl+= and Ctrl+Shift+= work with the cursor in the text box (not
    only on the toolbar), and two text boxes on one page don't make the keys ambiguous."""
    import pytest
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from si_assistant.ui.rich_text import RichTextEditor
    page = QWidget()
    layout = QVBoxLayout(page)
    first, ed = RichTextEditor(), RichTextEditor()
    layout.addWidget(first)
    layout.addWidget(ed)
    page.show()
    page.activateWindow()
    QTest.qWaitForWindowActive(page, 2000)
    ed.edit.setFocus()
    qapp.processEvents()
    if qapp.focusWidget() is not ed.edit:
        pytest.skip("this platform gives no keyboard focus")
    Ctrl, Shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier

    def select(text, a, b):
        ed.set_markup(text)
        cur = ed.edit.textCursor()
        cur.setPosition(a)
        cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        ed.edit.setTextCursor(cur)

    try:
        select("bold word", 0, 4)
        QTest.keyClick(ed.edit, Qt.Key.Key_B, Ctrl)
        assert ed.markup() == "**bold** word"
        QTest.keyClick(ed.edit, Qt.Key.Key_B, Ctrl)          # and off again
        assert ed.markup() == "bold word"
        select("J value", 0, 1)
        QTest.keyClick(ed.edit, Qt.Key.Key_I, Ctrl)
        assert ed.markup() == "*J* value"
        select("CDCl3", 4, 5)
        QTest.keyClick(ed.edit, Qt.Key.Key_Equal, Ctrl)
        assert ed.markup() == "CDCl_{3}"
        select("13C", 0, 2)
        QTest.keyClick(ed.edit, Qt.Key.Key_Equal, Ctrl | Shift)
        assert ed.markup() == "^{13}C"
        assert first.markup() == ""                          # the other box is untouched
    finally:
        page.close()


def test_main_window_open_edit_reorder(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "UI SI")
    a = p.add_section("text", "General Information")
    b = p.add_section("file", "Optimization")

    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    assert win.project.current_lock().user == "Tester"
    # The project item (§13k.3), front matter, the two sections and the Spectra section,
    # added on opening (§13k.12), then the Manuscript check (§13o) and the References
    # item, always last (§13n.10).
    assert win.tree.topLevelItemCount() == 7
    assert win.tree.topLevelItem(4).text(0) == "Spectra"
    assert win.tree.topLevelItem(5).text(0) == "Manuscript check"
    assert win.tree.topLevelItem(6).text(0) == "References"
    assert win.tree.topLevelItem(0).text(0) == "UI SI"
    assert win.tree.topLevelItem(1).text(0) == "Front matter"

    # Type into the text section and make sure it is saved when switching away.
    win.tree.setCurrentItem(win.tree.topLevelItem(2))
    win.panel.editor.edit.setPlainText("Hello")
    win.tree.setCurrentItem(win.tree.topLevelItem(3))
    assert win.project.read_section_text(win.project.section(a.id)) == "Hello"

    win.move_item(-1)  # move "Optimization" up
    assert [s.id for s in Project.open(p.folder).sections][:2] == [b.id, a.id]

    win.close_project()
    assert Project.open(p.folder).current_lock() is None
    win.close()


def test_compound_pages_in_main_window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.data_panel import DataSectionPanel
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "UI SI")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b", "3c"])

    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    assert isinstance(win.panel, DataSectionPanel)
    win.new_compound()
    win.new_compound()
    assert isinstance(win.panel, CompoundPanel)
    proj = win.project
    first, second = proj.section(data.id).compounds

    # Type a name and a manual ID into the second compound, then switch away (autosave).
    name_box = next(b for b in win.panel.boxes if b.defn.key == "name")
    name_box.editor.widgets["text"].setText("Ethyl ester")
    name_box.editor.changed.emit()
    win.panel.override_edit.setText("3a")  # duplicate of the first compound's ID
    win.panel.override_edit.textEdited.emit("3a")
    win.select("compound", first.id)
    reopened = Project.open(p.folder)
    assert reopened.compound(second.id).name == "Ethyl ester"
    report = reopened.id_report()
    assert "Duplicate ID" in report.compound_warnings[first.id][0]
    assert "3a" in win._find_item("compound", second.id).text(0)

    # Flag a field.
    nmr_box = next(b for b in win.panel.boxes if b.defn.key == "nmr")
    nmr_box.flag_btn.setChecked(True)
    nmr_box.flag_comment.setText("re-run 13C")
    win.panel.flush()
    assert Project.open(p.folder).compound(first.id).flags["nmr"]["comment"] == "re-run 13C"

    # Reorder: the second compound moves up and takes position 1.
    win.select("compound", second.id)
    win.move_item(-1)
    assert [c.id for c in Project.open(p.folder).section(data.id).compounds] == [second.id, first.id]
    win.close_project()
    win.close()


def test_structure_editor_updates_nmr_and_hrms(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.structure_widgets import HrmsEditor, StructureEditor
    p = Project.create(tmp_path, "S")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    box = lambda kind: next(b.editor for b in panel.boxes if isinstance(b.editor, kind))  # noqa: E731

    # Type the found mass first; it must survive the structure being set.
    hrms = box(HrmsEditor)
    hrms.widgets["found"].setText("155.0701")
    hrms.changed.emit()
    assert "Add a structure" in hrms.calc.text()

    structure = box(StructureEditor)
    structure.smiles.setText("CCOC(=O)c1ccoc1C(F)(F)F")  # CF3 analogue: should add 19F
    structure._apply_smiles()
    qapp.processEvents()  # let the page rebuild
    assert [r["nucleus"] for r in c.values["nmr"]] == ["1H", "13C", "19F"]
    hrms = box(HrmsEditor)
    assert hrms.widgets["found"].text() == "155.0701"
    assert "C8H8F3O3" in hrms.calc.text().replace("<sub>", "").replace("</sub>", "")

    # Bad SMILES: warning shown, structure unchanged.
    structure = box(StructureEditor)
    structure.smiles.setText("C1CC")
    structure._apply_smiles()
    assert structure.error.isVisibleTo(structure)
    assert p.structure_info(c).formula == "C8H7F3O3"
    panel.flush()
    p.close()


def test_dashboard_click_opens_field(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.dashboard_view import CID_ROLE, KEY_ROLE
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "D")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b"])
    a = p.add_compound(data.id, name="A")
    b = p.add_compound(data.id, name="B")
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    dash = win.panel.dashboard
    assert dash.table.rowCount() == 2
    assert dash.summary.text() == "0 / 2 compounds complete · 0 flags · 0 warnings"

    # Hide a column, then filter.
    dash._toggle_column("rf", False)
    headers = [dash.table.horizontalHeaderItem(c).text() for c in range(dash.table.columnCount())]
    assert "Rf" not in headers and "Yield" in headers
    win.project.set_flag(win.project.compound(b.id), "yield", True, "check mass")
    dash.filter_box.setCurrentIndex(dash.filter_box.findData("flagged"))
    assert dash.table.rowCount() == 1

    # Click the Yield cell of B: opens B's page at the Yield field.
    col = headers.index("Yield")
    assert dash.table.item(0, col).data(CID_ROLE) == b.id
    assert dash.table.item(0, col).data(KEY_ROLE) == "yield"
    assert "⚑" in dash.table.item(0, col).text()
    dash._cell_clicked(0, col)
    qapp.processEvents()
    assert isinstance(win.panel, CompoundPanel) and win.panel.compound.id == b.id
    win.close_project()
    win.close()


def test_drop_positions(qapp, tmp_path, monkeypatch):
    """Dropping reports a slot *before* removal; check it lands where the user aimed."""
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "UI SI")
    d1 = p.add_section("data", "Products")
    d2 = p.add_section("data", "Derivatives")
    a, b, c = (p.add_compound(d1.id, name=n) for n in "ABC")
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    proj = win.project
    names = lambda sid: [x.name for x in proj.section(sid).compounds]  # noqa: E731

    win._on_item_dropped("compound", a.id, d1.id, "", 3)   # drag A below C
    assert names(d1.id) == ["B", "C", "A"]
    win._on_item_dropped("compound", a.id, d1.id, "", 0)   # drag A back above B
    assert names(d1.id) == ["A", "B", "C"]
    win._on_item_dropped("compound", b.id, d2.id, "", 0)   # drag B into the other section
    assert names(d1.id) == ["A", "C"] and names(d2.id) == ["B"]
    win._on_section_dropped(d1.id, "", 2)      # drag Products below Derivatives
    assert [s.name for s in proj.sections] == ["Derivatives", "Products", "Spectra"]
    qapp.processEvents()
    win.close_project()
    win.close()
