"""Selecting across figures and tables must keep them (SPEC §13l.11).

The bug: dragging a selection upward from the text below a table (or Shift+Up, or a
selection ending just after a mention) passed Qt the object's format, and the editor's
"type ordinary text after a chip" fix applied it to the whole selection: every figure,
table and mention in it became a plain character (invisible) and was dropped from the
saved text."""

import json

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from si_assistant.core.tables import Cell, Table
from si_assistant.ui import rich_text
from si_assistant.ui.panels import TextSectionPanel


@pytest.fixture
def page(qapp, project, tmp_path):
    p = project
    notes = p.add_section("text", "Notes")
    owner = p.text_owner(notes.id)
    table = Table.new(3, 2)
    table.rows[0] = [Cell("Entry"), Cell("Yield")]
    table.rows[1] = [Cell("1"), Cell("*90%*")]
    t1 = p.add_table(owner, table)
    img = QImage(300, 120, QImage.Format.Format_RGB32)
    img.fill(0x3366FF)
    img.save(str(tmp_path / "pic.png"))
    f1 = p.add_figure(owner, source=tmp_path / "pic.png")
    text = (f"See @{{{t1}}} and *this*.\n!table{{{t1}}} Screening *conditions*\n"
            f"!align{{right}} !figure{{{f1}}} The CH_{{2}} trace (see @{{{t1}}}).\n"
            "Some text below the figure, to select from.\nThe last line.")
    p.write_section_text(notes, text)
    rich_text.set_link_context(rich_text.LinkContext(p))
    panel = TextSectionPanel(p, notes)
    panel.resize(800, 900)
    panel.show()
    QApplication.processEvents()
    yield p, notes, panel, text, t1, f1
    panel._timer.stop()
    panel.close()
    rich_text.set_link_context(None)


def objects_intact(edit, t1, f1) -> bool:
    found = {(kind, item_id) for kind, item_id, _pos in edit._object_positions()}
    return found == {("table", t1), ("figure", f1)}


def object_heights(edit):
    """The laid-out height of the table's and the figure's paragraphs (an object turned
    into a plain character would leave only a line of text)."""
    layout = edit.document().documentLayout()
    return [layout.blockBoundingRect(edit.document().findBlockByNumber(n)).height()
            for n in (1, 2)]


def test_dragging_up_over_a_table_keeps_it(page):
    p, notes, panel, text, t1, f1 = page
    edit = panel.editor.edit
    tall = object_heights(edit)
    assert min(tall) > 80
    vp = edit.viewport()
    start = edit.cursorRect(QTextCursor(edit.document().lastBlock())).center()
    start = QPointF(start.x() + 60, start.y())

    def send(kind, y, buttons):
        pos = QPointF(start.x(), y)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove \
            else Qt.MouseButton.LeftButton
        QApplication.sendEvent(vp, QMouseEvent(kind, pos, vp.mapToGlobal(pos), button,
                                               buttons, Qt.KeyboardModifier.NoModifier))

    send(QEvent.Type.MouseButtonPress, start.y(), Qt.MouseButton.LeftButton)
    y = start.y()
    while y > 5:   # up over the figure and the table, to the first line
        y -= 12
        send(QEvent.Type.MouseMove, y, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseButtonRelease, y, Qt.MouseButton.NoButton)
    QApplication.processEvents()
    c = edit.textCursor()
    assert c.hasSelection() and c.selectionStart() < edit.document().findBlockByNumber(1).position()
    assert edit.markup() == text
    assert objects_intact(edit, t1, f1)
    assert object_heights(edit) == tall      # still drawn at their size
    # Painted while selected: the table's area is not just the selection colour.
    image = vp.grab().toImage()
    rect = edit.document().documentLayout().blockBoundingRect(
        edit.document().findBlockByNumber(1)).toRect()
    colours = {image.pixel(x, yy) for x in range(rect.left() + 5, min(rect.right(), 600), 7)
               for yy in range(rect.top() + 25, rect.bottom() - 5, 3)}
    assert len(colours) > 2
    panel.flush()
    assert p.read_section_text(notes) == text


def test_shift_up_and_select_all_keep_figures_tables_and_mentions(page):
    p, notes, panel, text, t1, f1 = page
    edit = panel.editor.edit
    edit.setFocus()
    edit.moveCursor(QTextCursor.MoveOperation.End)
    for _ in range(8):
        QTest.keyClick(edit, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    assert edit.textCursor().hasSelection()
    assert edit.markup() == text and objects_intact(edit, t1, f1)
    # A selection whose moving end is just after a mention (or a figure).
    for position in (len("See ") + 1, edit.document().findBlockByNumber(2).position() + 1):
        c = QTextCursor(edit.document())
        c.movePosition(QTextCursor.MoveOperation.End)
        c.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(c)
        assert edit.markup() == text and objects_intact(edit, t1, f1)
    edit.selectAll()
    assert edit.markup() == text and objects_intact(edit, t1, f1)


def test_copying_a_selection_with_a_table_and_figure(page):
    p, notes, panel, text, t1, f1 = page
    edit = panel.editor.edit
    c = QTextCursor(edit.document())
    c.movePosition(QTextCursor.MoveOperation.End)
    c.setPosition(edit.document().findBlockByNumber(1).position(),
                  QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(c)
    mime = edit.createMimeDataFromSelection()
    copied = bytes(mime.data(rich_text.MARKUP_MIME)).decode("utf-8")
    assert copied == text.split("\n", 1)[1]
    assert set(json.loads(bytes(mime.data(rich_text.OBJECTS_MIME)).decode("utf-8"))) == {t1, f1}
    # Typing over the selection gives ordinary text (and the objects go only from the
    # text, as with any deletion).
    QTest.keyClicks(edit, "Z")
    assert edit.markup().split("\n")[-1] == "Z"
    fmt = edit.currentCharFormat()
    assert fmt.objectType() == 0 and not fmt.hasProperty(rich_text.OBJECT_ID)
