"""Issue #2: formulas in table cells are subscripted when the cell is left, and cells keep
their formatting when copied and pasted (SPEC §13k.1b, §13k.5)."""

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidgetSelectionRange

from si_assistant.core.tables import Cell, Table
from si_assistant.ui import text_checks
from si_assistant.ui.rich_text import RichTextEditor
from si_assistant.ui.table_editor import (CELLS_MIME, TableEditorDialog, block_from_cells_json,
                                          cells_json)
from si_assistant.ui.text_objects import table_block_from_mime


@pytest.fixture
def formula_on(qapp):
    hub = text_checks.checks()
    was = hub.formula_on
    hub.set_formula_on(True)
    yield
    hub.set_formula_on(was)


def typed(dlg, row, col, text):
    dlg.grid.setCurrentCell(row, col)
    dlg.cell_edit.edit.setFocus()
    QTest.keyClicks(dlg.cell_edit.edit, text)


def test_enter_and_tab_subscript_the_formula_typed_last(formula_on):
    dlg = TableEditorDialog(Table.new(3, 2), "", "")
    typed(dlg, 0, 0, "K2CO3")
    QTest.keyClick(dlg.cell_edit.edit, Qt.Key.Key_Return)     # to the cell below
    QTest.keyClicks(dlg.cell_edit.edit, "Cs2CO3")
    QTest.keyClick(dlg.cell_edit.edit, Qt.Key.Key_Tab)        # to the cell on the right
    QTest.keyClicks(dlg.cell_edit.edit, "THF")
    t = dlg.result_table()
    assert t.rows[0][0].text == "K_{2}CO_{3}"
    assert t.rows[1][0].text == "Cs_{2}CO_{3}"
    assert t.rows[1][1].text == "THF"
    assert dlg.grid.currentRow() == 1 and dlg.grid.currentColumn() == 1


def test_clicking_another_cell_or_ok_subscripts_it(formula_on):
    dlg = TableEditorDialog(Table.new(2, 2), "", "")
    typed(dlg, 0, 0, "Na2SO4")
    dlg.grid.setCurrentCell(1, 1)            # the formula goes to the cell it was typed in
    t = dlg.result_table()
    assert t.rows[0][0].text == "Na_{2}SO_{4}" and t.rows[1][1].text == ""
    QTest.keyClicks(dlg.cell_edit.edit, "Et3N")
    dlg.accept()
    assert dlg.result_table().rows[1][1].text == "Et_{3}N"


def test_a_toolbar_action_subscripts_before_rows_move(formula_on):
    dlg = TableEditorDialog(Table.new(2, 2), "", "")
    typed(dlg, 1, 0, "K3PO4")
    dlg.actions_["row_above"].trigger()
    t = dlg.result_table()
    assert t.rows[2][0].text == "K_{3}PO_{4}" and t.rows[1][0].text == ""


def test_nothing_happens_with_the_toggle_off_or_after_a_click(qapp):
    hub = text_checks.checks()
    was = hub.formula_on
    hub.set_formula_on(False)
    try:
        dlg = TableEditorDialog(Table.new(2, 2), "", "")
        typed(dlg, 0, 0, "K2CO3")
        QTest.keyClick(dlg.cell_edit.edit, Qt.Key.Key_Return)
        assert dlg.result_table().rows[0][0].text == "K2CO3"
    finally:
        hub.set_formula_on(was)
    # Only a word just typed: a cell's stored text is never changed by opening and leaving
    # it (the user may have taken the subscripts off by hand).
    hub.set_formula_on(True)
    try:
        t = Table.new(2, 2)
        t.rows[0][0] = Cell("K2CO3")
        dlg = TableEditorDialog(t, "", "")
        dlg.grid.setCurrentCell(0, 0)
        dlg.cell_edit.edit.setFocus()
        dlg.cell_edit.edit.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClick(dlg.cell_edit.edit, Qt.Key.Key_Return)
        assert dlg.result_table().rows[0][0].text == "K2CO3"
    finally:
        hub.set_formula_on(was)


def test_other_text_boxes_finish_the_word_when_left(formula_on):
    ed = RichTextEditor()
    ed.set_markup("")
    ed.edit.setFocus()
    QTest.keyClicks(ed.edit, "dried over MgSO4")
    assert ed.edit.finish_word()
    assert ed.markup() == "dried over MgSO_{4}"
    assert not ed.edit.finish_word()          # once only
    ed.deleteLater()


def test_copied_cells_keep_their_formatting(qapp):
    t = Table.new(2, 3)
    t.rows[0] = [Cell("K_{2}CO_{3}", colspan=2), None, Cell("**90**")]
    t.rows[1] = [Cell("Cs_{2}CO_{3}"), Cell("@{c-123456}"), Cell("12")]
    dlg = TableEditorDialog(t, "", "")
    dlg.grid.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 1), True)
    dlg._copy_cells()
    data = QGuiApplication.clipboard().mimeData()
    assert data.hasFormat(CELLS_MIME)
    assert "<sub>2</sub>" in data.html()
    assert data.text().splitlines()[0].startswith("K2CO3")
    dlg.grid.clearSelection()
    dlg.grid.setCurrentCell(1, 2)
    dlg.paste_table()
    r = dlg.result_table()
    assert r.rows[1][2].text == "K_{2}CO_{3}" and r.rows[1][2].colspan == 2
    assert r.rows[2][2].text == "Cs_{2}CO_{3}" and r.rows[2][3].text == "@{c-123456}"


def test_cells_json_cuts_merges_and_leaves_pictures_out():
    block = [[Cell("a", colspan=3, image="images/x.svg"), None]]
    back = block_from_cells_json(cells_json(block, 1, 2))
    assert back[0][0].colspan == 2 and back[0][0].image == "" and back[0][1] is None
    assert block_from_cells_json(b"not json") is None
    assert block_from_cells_json(b"[]") is None


def test_pasting_copied_cells_into_the_text_makes_a_table(qapp):
    data = QMimeData()
    data.setData(CELLS_MIME, cells_json([[Cell("CH_{2}Cl_{2}"), Cell("90")]], 1, 2))
    block = table_block_from_mime(data)
    assert block[0][0].text == "CH_{2}Cl_{2}"
