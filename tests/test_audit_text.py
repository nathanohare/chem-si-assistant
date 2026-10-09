"""Audit fixes in the rich-text editor, its figures and tables, and the table editor."""

import contextlib
import json
import os

import pytest
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QImage, QPainter, QTextCursor, \
    QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from si_assistant.core import markup, tables
from si_assistant.core.tables import Table, block_from_text
from si_assistant.ui import rich_text, table_editor, text_checks, text_objects
from si_assistant.ui.panels import TextSectionPanel
from si_assistant.ui.table_editor import TableEditorDialog


def picture(path, w=300, h=120, dpi=None, fmt=None, color=0xFF3366):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(color)
    if dpi:
        img.setDotsPerMeterX(round(dpi / 0.0254))
        img.setDotsPerMeterY(round(dpi / 0.0254))
    assert img.save(str(path), fmt)
    return path


@pytest.fixture
def setup(qapp, project, tmp_path):
    p = project
    notes = p.add_section("text", "Optimization")
    owner = p.text_owner(notes.id)
    f1 = p.add_figure(owner, source=picture(tmp_path / "trace.png"))
    p.write_section_text(notes, f"See @{{{f1}}}.\n!figure{{{f1}}} HPLC trace")
    rich_text.set_link_context(rich_text.LinkContext(p))
    yield p, notes, f1, tmp_path
    rich_text.set_link_context(None)


def chips(edit):
    out = []
    block = edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.objectType() == rich_text.LINK_OBJECT:
                out.append(edit.chip_label(fmt))
            it += 1
        block = block.next()
    return out


def utf16(text: str, k: int) -> int:
    """The Qt position of character k of `text`."""
    return len(text[:k].encode("utf-16-le")) // 2


# ---------------------------------------------------------------------- pictures (rich-text-1)

def test_picture_size_is_read_once_until_the_file_changes(qapp, tmp_path, monkeypatch):
    path = picture(tmp_path / "photo.jpg", 600, 300, dpi=150, fmt="JPG")
    calls = []
    real = text_objects.image_info

    def counting(p):
        calls.append(p)
        return real(p)

    monkeypatch.setattr(text_objects, "image_info", counting)
    text_objects._inches.clear()
    first = text_objects.picture_inches(path)
    assert first == pytest.approx((4.0, 2.0), abs=0.02)
    assert text_objects.picture_inches(path) == first
    assert len(calls) == 1
    # A new picture in the same file (e.g. replaced, or synced from another computer).
    picture(path, 900, 300, dpi=150, fmt="JPG")
    os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 10_000_000))
    assert text_objects.picture_inches(path) == pytest.approx((6.0, 2.0), abs=0.02)
    assert len(calls) == 2


def test_unreadable_picture_size_is_not_remembered(qapp, tmp_path, monkeypatch):
    path = tmp_path / "busy.jpg"
    path.write_bytes(b"not yet a picture")
    text_objects._inches.clear()
    assert text_objects.picture_inches(path) is None
    picture(path, 300, 150, dpi=100, fmt="JPG")   # (now readable, maybe same mtime)
    assert text_objects.picture_inches(path) == pytest.approx((3.0, 1.5), abs=0.02)


# ------------------------------------------------------------------ thumbnails (cross-platform-7)

def test_thumbnails_keep_one_version_per_file_and_stay_within_memory(qapp, tmp_path,
                                                                     monkeypatch):
    text_objects._thumbs.clear()
    a = picture(tmp_path / "a.png", 200, 100)
    first = text_objects.thumbnail(a)
    assert text_objects.thumbnail(a).cacheKey() == first.cacheKey()   # (from memory)
    picture(a, 220, 100)
    os.utime(a, ns=(os.stat(a).st_atime_ns, os.stat(a).st_mtime_ns + 10_000_000))
    second = text_objects.thumbnail(a)
    assert second.width() == 220
    assert [k for k in text_objects._thumbs if k[0] == str(a)] == [
        (str(a), os.stat(a).st_mtime_ns, os.stat(a).st_size)]
    # A memory limit: the least recently shown go first; the newest is always kept.
    monkeypatch.setattr(text_objects, "THUMB_BYTES", second.sizeInBytes() * 2)
    b = picture(tmp_path / "b.png", 200, 100)
    c = picture(tmp_path / "c.png", 200, 100)
    text_objects.thumbnail(b)
    text_objects.thumbnail(a)   # (shown again: kept)
    text_objects.thumbnail(c)
    names = [k[0] for k in text_objects._thumbs]
    assert names == [str(a), str(c)]
    assert sum(i.sizeInBytes() for i in text_objects._thumbs.values()) <= 2 * second.sizeInBytes()
    monkeypatch.setattr(text_objects, "THUMB_BYTES", 1)
    big = text_objects.thumbnail(b)
    assert text_objects.thumbnail(b).cacheKey() == big.cacheKey()


# ---------------------------------------------------------------------- emoji and 𝐽 (hunt-…-1)

def test_part_of_a_text_after_an_emoji_or_math_letter():
    doc = QTextDocument()
    doc.setPlainText("ab😀cd")
    assert markup.serialize(rich_text.document_to_paragraphs(doc, 4, 6)) == "cd"
    text = "Coupling 𝐽 = 7.2 Hz, δ 3.45 (s) 🧪."
    doc.setPlainText(text)
    k = text.index("7.2 Hz")
    a, b = utf16(text, k), utf16(text, k + len("7.2 Hz"))
    assert markup.serialize(rich_text.document_to_paragraphs(doc, a, b)) == "7.2 Hz"
    # (the whole text, and plain text, are as before)
    assert markup.serialize(rich_text.document_to_paragraphs(doc)) == markup.serialize(
        markup.parse(text))
    doc.setPlainText("plain text")
    assert markup.serialize(rich_text.document_to_paragraphs(doc, 2, 7)) == "ain t"


def test_cut_after_a_math_letter_puts_the_cut_text_on_the_clipboard(qapp):
    ed = rich_text.RichTextEditor()
    text = "Stirred 𝐽 🧪 for 2 h at 0 °C, then filtered."
    ed.set_markup(text)
    k = text.index("2 h")
    cursor = ed.edit.textCursor()
    cursor.setPosition(utf16(text, k))
    cursor.setPosition(utf16(text, k + len("2 h at 0 °C")), QTextCursor.MoveMode.KeepAnchor)
    ed.edit.setTextCursor(cursor)
    data = ed.edit.createMimeDataFromSelection()
    assert bytes(data.data(rich_text.MARKUP_MIME)).decode("utf-8") == "2 h at 0 °C"
    assert data.text() == "2 h at 0 °C"
    ed.edit.cut()
    ed.edit.moveCursor(QTextCursor.MoveOperation.End)
    ed.edit.paste()
    assert ed.markup() == "Stirred 𝐽 🧪 for , then filtered.2 h at 0 °C"


# ---------------------------------------------------------------------- chip labels (rich-text-2)

def test_new_chip_labels_are_not_an_undo_step(setup):
    p, notes, f1, tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    assert chips(edit) == ["Figure S1"]
    edit.moveCursor(QTextCursor.MoveOperation.Start)
    f2 = edit.objects.add_file(picture(tmp / "new.png", color=0x3366FF))
    edit.insert_object("figure", f2)   # (above the mentioned figure: it becomes S2)
    panel._save_text()
    assert chips(edit) == ["Figure S2"]
    assert not panel._timer.isActive()   # (no second save of the same text)
    # Ctrl+Z in the text box takes the new figure away, not just the label.
    edit.document().undo()
    assert f"!figure{{{f2}}}" not in edit.markup()
    assert edit.document().isRedoAvailable()
    panel._save_text()
    assert chips(edit) == ["Figure S1"]
    assert edit.document().isRedoAvailable()   # (the save did not clear it)
    edit.selectAll()
    assert edit.createMimeDataFromSelection().text().startswith("See Figure S1.")
    # A chip mentioned again later shows the current label too.
    edit.moveCursor(QTextCursor.MoveOperation.End)
    edit.insert_mention(f1)
    assert chips(edit) == ["Figure S1", "Figure S1"]


def test_a_deleted_chip_brought_back_by_undo_shows_the_current_label(setup, monkeypatch):
    p, notes, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    assert chips(edit) == ["Figure S1"]
    # Renumbered (e.g. a figure added above it in another text) and the labels refreshed.
    real = rich_text.link_label
    monkeypatch.setattr(rich_text, "link_label",
                        lambda ref: "Figure S2" if ref.endswith(f1) else real(ref))
    edit.refresh_links()
    assert chips(edit) == ["Figure S2"]
    # The chip is deleted, the text saved, then Ctrl+Z in the box brings it back.
    cursor = QTextCursor(edit.document())
    cursor.setPosition(edit.document().toPlainText().index(rich_text._OBJECT_CHAR))
    cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                        QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    panel._save_text()
    assert chips(edit) == []
    edit.document().undo()
    assert chips(edit) == ["Figure S2"]
    edit.selectAll()
    assert edit.createMimeDataFromSelection().text().startswith("See Figure S2.")


def test_reloading_the_text_forgets_old_chip_labels(setup):
    p, notes, f1, _tmp = setup
    ed = rich_text.RichTextEditor()
    ed.set_markup(f"See @{{{f1}}}.")
    ed.edit.chip_labels[f"{markup.MENTION}{f1}"] = "Figure S9"
    assert chips(ed.edit) == ["Figure S9"]
    ed.set_markup(f"Also @{{{f1}}}.")
    assert chips(ed.edit) == ["Figure S1"]


# ---------------------------------------------------------------------- large tables (rich-text-3)

def test_a_table_over_200_rows_is_read_back_whole(project):
    t = Table.new(1, 1)
    left = t.paste(0, 0, block_from_text("\n".join(f"{i}\tx{i}" for i in range(250))))
    assert left == (0, 0) and t.n_rows == 250 and t.n_cols == 2
    t.rows[0][0].rowspan = 250
    back = Table.from_json(json.loads(json.dumps(t.to_json())))
    assert back.n_rows == 250 and back.rows[0][0].rowspan == 250
    owner = project.text_owner(project.add_section("text", "Big").id)
    t2 = Table.new(1, 1)
    t2.paste(0, 0, block_from_text("\n".join(str(i) for i in range(250))))
    table_id = project.add_table(owner, t2)
    read = project.read_table(owner, table_id)
    assert read.n_rows == 250 and read.rows[-1][0].text == "249"


def test_paste_stops_at_the_limit_and_says_what_was_left_out(qapp, monkeypatch):
    monkeypatch.setattr(tables, "MAX_ROWS", 5)
    monkeypatch.setattr(tables, "MAX_COLS", 3)
    t = Table.new(2, 2)
    left = t.paste(1, 1, block_from_text("a\tb\tc\n1\t2\t3\nx\ty\tz\np\tq\tr\ns\tt\tu"))
    assert left == (1, 1) and (t.n_rows, t.n_cols) == (5, 3)
    assert [c.text for c in t.rows[4]] == ["", "p", "q"]
    warned = []
    monkeypatch.setattr(table_editor.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[2]))
    text_objects.table_from_block(block_from_text("\n".join("a\tb\tc\td"
                                                            for _ in range(7))), object())
    assert warned and "2 rows and 1 column of the copied table were not" in warned[0]
    warned.clear()
    table_editor.warn_left_out(None, (0, 0))
    assert not warned
    # One row (or one column) left out: "was", not "were" (review-1).
    table_editor.warn_left_out(None, (1, 0))
    table_editor.warn_left_out(None, (0, 1))
    assert "1 row of the copied table was not pasted" in warned[0]
    assert "1 column of the copied table was not pasted" in warned[1]


def test_one_paste_brings_in_at_most_the_paste_limit(qapp, monkeypatch):
    # (review-2: a huge Excel range pasted by mistake would freeze the table editor)
    assert tables.PASTE_MAX_ROWS <= tables.MAX_ROWS and tables.PASTE_MAX_COLS <= tables.MAX_COLS
    monkeypatch.setattr(tables, "PASTE_MAX_ROWS", 3)
    monkeypatch.setattr(tables, "PASTE_MAX_COLS", 2)
    t = Table.new(1, 1)
    left = t.paste(0, 0, block_from_text("\n".join("a\tb\tc\td" for _ in range(5))))
    assert left == (2, 2) and (t.n_rows, t.n_cols) == (3, 2)
    # Pasting again further down still works: the cap is per paste, not per table.
    left = t.paste(3, 0, block_from_text("x\ty\nz\tw"))
    assert left == (0, 0) and (t.n_rows, t.n_cols) == (5, 2) and t.rows[4][1].text == "w"
    warned = []
    monkeypatch.setattr(table_editor.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[2]))
    made = text_objects.table_from_block(block_from_text("\n".join("a\tb\tc"
                                                                   for _ in range(4))), object())
    assert (made.n_rows, made.n_cols) == (3, 2)
    assert warned and "1 row and 1 column" in warned[0] and "at most 3 rows" in warned[0]


def test_new_rows_and_columns_stop_at_the_limit(qapp, monkeypatch):
    monkeypatch.setattr(tables, "MAX_ROWS", 3)
    monkeypatch.setattr(tables, "MAX_COLS", 3)
    t = Table.new(2, 1)
    t.paste(0, 0, block_from_text("a\tb\tc"))
    t.insert_column(3)
    t.insert_row(2)
    t.insert_row(0)   # (no room left: nothing happens)
    assert (t.n_rows, t.n_cols) == (3, 3) and [c.text for c in t.rows[0]] == ["a", "b", "c"]
    back = Table.from_json(json.loads(json.dumps(t.to_json())))
    assert (back.n_rows, back.n_cols) == (3, 3)
    # The editor greys out + Row / + Column (and says why) at the limit.
    dlg = TableEditorDialog(Table.new(2, 3))
    assert not dlg.actions_["col_right"].isEnabled()
    assert "at most 3 columns" in dlg.actions_["col_left"].toolTip()
    assert dlg.actions_["row_below"].isEnabled()
    dlg._insert_row(1)
    assert not dlg.actions_["row_above"].isEnabled()
    dlg._delete_columns()
    assert dlg.actions_["col_right"].isEnabled()
    assert dlg.actions_["col_right"].toolTip() == "Insert a column right of the selected cell"


def test_paste_still_grows_merges_and_aligns_as_before():
    t = Table.new(2, 2)
    t.columns = ["left", "right"]
    t.merge(0, 0, 1, 0)
    t.paste(1, 1, block_from_text("a\tb\n1\t2\n"))
    assert (t.n_rows, t.n_cols) == (3, 3) and t.columns == ["left", "right", "right"]
    assert t.rows[0][0].rowspan == 2 and t.rows[2][2].text == "2"
    assert t.rows[1][1].text == "a"


# ---------------------------------------------------------------------- damaged files (rich-text-6)

@pytest.mark.parametrize("data", [
    {"rows": {"a": 1}}, {"columns": 3}, {"footnotes": 5},
    {"rows": [[{"colspan": float("inf")}]]}, {"header_rows": float("inf")},
    {"rows": [[{"rowspan": float("inf"), "text": "a"}]], "footnotes": [None, "n"]},
])
def test_hand_edited_table_files_do_not_crash(data):
    table = Table.from_json(data)
    assert table.n_rows >= 0
    if data.get("footnotes") == [None, "n"]:
        assert table.footnotes == ["n"] and table.rows[0][0].rowspan == 1


def test_a_missing_cell_text_reads_as_empty(project):
    assert Table.from_json({"rows": [[{"text": None}, {"text": 5}]]}).rows[0][0].text == ""
    owner = project.text_owner(project.add_section("text", "T").id)
    table_id = project.add_table(owner, Table.new(2, 2))
    path = project.table_path(owner, table_id)
    path.write_text('{"rows": {"a": 1}, "header_rows": Infinity}', encoding="utf-8")
    assert project.read_table(owner, table_id).n_rows == 0


# ---------------------------------------------------------------------- Shift+Enter (rich-text-4)

def test_a_line_break_in_a_caption_is_saved_as_a_space(setup):
    p, notes, f1, _tmp = setup
    ed = rich_text.RichTextEditor()
    ed.set_markup(f"Intro.\n!figure{{{f1}}} First line")
    ed.edit.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(ed.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(ed.edit, "second line")
    assert ed.markup() == f"Intro.\n!figure{{{f1}}} First line second line"
    # Two breaks, or a space already typed: one space.
    QTest.keyClick(ed.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(ed.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(ed.edit, " end")
    assert ed.markup().endswith("First line second line end")
    # Captions without breaks, and soft breaks in ordinary text, are as before.
    for text in (f"!figure{{{f1}}} Cap *it*", f"!figure{{{f1}}}", "Plain"):
        ed.set_markup(text)
        assert ed.markup() == text
    ed.set_markup("Plain")
    ed.edit.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(ed.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(ed.edit, "next")
    assert ed.markup() == "Plain\nnext"


# ---------------------------------------------------------------------- read-only (rich-text-8)

def test_formatting_keys_do_nothing_in_a_read_only_box(qapp):
    ed = rich_text.RichTextEditor()
    ed.set_markup("Hello world")
    changed = []
    ed.changed.connect(lambda: changed.append(1))
    ed.set_read_only(True)
    ed.edit.selectAll()
    for key in (Qt.Key.Key_B, Qt.Key.Key_I, Qt.Key.Key_U, Qt.Key.Key_Equal):
        QTest.keyClick(ed.edit, key, Qt.KeyboardModifier.ControlModifier)
    ed.edit.toggle_bold()
    ed.edit.set_heading(1)
    assert ed.markup() == "Hello world" and not changed
    assert not ed.bold_act.isEnabled()
    # Made read-only on the text box itself (as compound pages do).
    ed.set_read_only(False)
    ed.edit.setReadOnly(True)
    ed.edit.selectAll()
    ed.edit.toggle_italic()
    assert ed.markup() == "Hello world"
    ed.edit.setReadOnly(False)
    assert ed.bold_act.isEnabled()
    ed.edit.selectAll()
    ed.edit.toggle_bold()
    assert ed.markup() == "**Hello world**"


# ---------------------------------------------------------------------- empty tables (rich-text-5)

@pytest.mark.parametrize("data", [
    {"rows": []}, {"rows": [[]]}, {},
    {"rows": [], "columns": ["left", "right"], "label": "X", "footnotes": ["a"],
     "breakable": True},
])
def test_table_editor_opens_a_table_with_no_cells(qapp, data):
    dlg = TableEditorDialog(Table.from_json(data))
    result = dlg.result_table()
    assert result.n_rows >= 1 and result.n_cols >= 1
    assert result.label == data.get("label", "")
    assert result.footnotes == data.get("footnotes", [])
    assert result.breakable is bool(data.get("breakable"))
    assert not dlg.has_changes()


# ---------------------------------------------------------------------- picker (rich-text-7)

@pytest.mark.parametrize("multi", [False, True])
def test_picker_with_no_match_picks_nothing(qapp, multi):
    dlg = rich_text.PickerDialog("Mention", [(None, "Compounds"), ("c-1", "1a Benzene"),
                                             ("c-2", "2b Toluene")], multi=multi)
    assert dlg.values() == ["c-1"]
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(1))
    dlg.search.setText("zzzz")
    assert dlg.values() == []
    dlg._accept_current()
    assert not accepted
    dlg.search.setText("tol")
    assert dlg.values() == ["c-2"]
    dlg.search.setText("")
    assert dlg.values() == ["c-1"]


# ------------------------------------------------------------------ table previews (rich-text-9)

def _render(text, label, monkeypatch):
    monkeypatch.setattr(rich_text, "link_label", lambda ref: label)
    image = QImage(200, 30, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    text_objects._draw_markup(painter, QRectF(2, 2, 190, 24), text, QFont(),
                              Qt.AlignmentFlag.AlignLeft, QColor("#202020"))
    painter.end()
    return image


def test_table_cells_are_laid_out_once_and_follow_new_labels(qapp, monkeypatch):
    text_objects._markup_docs.clear()
    cell = "Yield of @{c-1} in CH_{2}Cl_{2}"
    first = _render(cell, "3a", monkeypatch)
    assert len(text_objects._markup_docs) == 1
    again = _render(cell, "3a", monkeypatch)
    assert len(text_objects._markup_docs) == 1 and again == first
    renumbered = _render(cell, "4b", monkeypatch)
    assert len(text_objects._markup_docs) == 2
    assert list(text_objects._markup_docs.values())[-1].toPlainText() == "Yield of 4b in CH2Cl2"
    text_objects._markup_docs.clear()
    assert _render(cell, "4b", monkeypatch) == renumbered   # (the same as a fresh one)


# ---------------------------------------------------------------------- menus (rich-text-11)

def test_figure_menu_is_deleted_after_use(setup, monkeypatch):
    p, notes, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    shown = []

    def fake_show(menu, pos):
        shown.append(menu.actions()[0].text())
        menu.deleteLater()

    monkeypatch.setattr(text_checks, "show_menu", fake_show)
    monkeypatch.setattr(edit, "object_at", lambda pos: ("figure", f1))
    for _ in range(3):
        edit.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                                QPoint(5, 5)))
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert shown == ["Edit label…"] * 3
    assert not edit.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)


# ------------------------------------------------------------------ structures (hunt-undo-…-4)

def test_pasted_structures_are_adopted_in_one_step(qapp):
    log = []

    class Fake:
        @contextlib.contextmanager
        def pasting(self, count):
            log.append(("open", count))
            yield
            log.append("close")

        def adopt(self, sid, info):
            log.append(("adopt", sid))
            return "new-" + sid

    edit = rich_text.RichTextEdit()
    edit.inline_structures = Fake()
    paragraphs = markup.parse("Cat. @struct{s-a} and @struct{s-b}, @struct{s-a}")
    edit._pasted_structures(paragraphs, rich_text.QMimeData())
    assert log == [("open", 2), ("adopt", "s-a"), ("adopt", "s-b"), "close"]
    assert markup.serialize(paragraphs) == "Cat. @struct{new-s-a} and @struct{new-s-b}, " \
                                           "@struct{new-s-a}"


# ---------------------------------------------------------------------- paste key (compound-page-8)

def test_table_editor_names_this_computers_paste_key(qapp, monkeypatch):
    # "Ctrl+V" on Windows and Linux, "⌘V" on a Mac: the hint uses whatever the system says.
    from PySide6.QtWidgets import QLabel
    monkeypatch.setattr(table_editor, "paste_key", lambda: "⌘V")
    dlg = TableEditorDialog(Table.from_json({"rows": [["a"]]}))
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert any("⌘V pastes a table" in t for t in texts)
    assert not any("Ctrl+V" in t for t in texts)


# ---------------------------------------------------------------------- failed save first (late-1)

def test_a_failed_save_before_adding_a_figure_is_said_not_raised(qapp, tmp_path, monkeypatch):
    # Adding or removing a figure or table first saves the page's typing; on a Compound page
    # that save can fail (e.g. a file locked by OneDrive). It is said; nothing crashes.
    from PySide6.QtWidgets import QMessageBox

    from si_assistant.core.project import Project
    from si_assistant.ui.compound_panel import CompoundPanel
    p = Project.create(tmp_path / "proj", "C")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="made-up ester")
    panel = CompoundPanel(p, c, lambda: "Tester")
    edits = panel.findChildren(rich_text.RichTextEdit)
    assert edits
    percent = next(b for b in panel.boxes if b.defn.key == "yield").editor.widgets["percent"]
    percent.setText("42")
    percent.textEdited.emit("42")

    def locked(compound):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(p, "save_compound", locked)
    panel._timer.timeout.emit()          # the autosave fails; the typing stays waiting
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    text_objects.save_typing_first(edits[0])   # no exception escapes
    assert warned and warned[0][1] == "Could not save"
    assert panel._dirty                   # still waiting, tried again later
    panel.deleteLater()
    p.close()


# ------------------------------------------------------------- failed save first, text page (review-1)

def _typing_waiting_with_a_locked_text(p, notes, monkeypatch):
    """A text section page with typing still waiting, whose text file cannot be written."""
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    edit.setTextCursor(cursor)
    edit.insertPlainText("Conditions: ")
    assert panel._timer.isActive()

    def locked(*_a, **_k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(p, "write_section_text", locked)
    from PySide6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    return panel, edit, warned


def test_a_failed_save_before_adding_a_figure_does_not_say_the_text_is_lost(setup,
                                                                            monkeypatch):
    p, notes, f1, tmp = setup
    panel, edit, warned = _typing_waiting_with_a_locked_text(p, notes, monkeypatch)
    new_pic = picture(tmp / "tlc.png", 80, 40)
    monkeypatch.setattr(text_objects, "choose_figure_file", lambda parent: new_pic)
    before = set(edit.objects.figures())
    text_objects.insert_figure_file(edit)
    # The page's ordinary autosave warning (the page stays open), not the one for leaving.
    assert warned and warned[0][1] == "Could not save"
    assert "lost if the page is closed" not in warned[0][2]
    assert "still on the page" in warned[0][2]
    assert panel._unsaved                       # tried again with the next change
    assert set(edit.objects.figures()) - before  # the figure is still added
    assert "Conditions: " in edit.toPlainText()
    panel.deleteLater()


def test_a_figure_is_not_removed_while_earlier_typing_cannot_be_saved(setup, monkeypatch):
    # Removed then, Edit ▸ Undo of "Remove Figure S1" would also take back that typing.
    from PySide6.QtWidgets import QMessageBox
    p, notes, f1, _tmp = setup
    p.enable_undo()
    panel, edit, warned = _typing_waiting_with_a_locked_text(p, notes, monkeypatch)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    text_objects.remove_object(edit, "figure", f1)
    assert [w[1] for w in warned] == ["Could not save", "Remove figure"]
    assert "lost if the page is closed" not in warned[0][2]
    assert "nothing has been lost" in warned[1][2]
    assert f1 in edit.objects.figures()          # still there, in the project and the text
    assert "Conditions: " in edit.toPlainText()
    assert not p.journal.undo_label.startswith("Remove")
    panel.deleteLater()


def test_a_figure_is_removed_once_the_typing_is_saved_as_its_own_step(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    p, notes, f1, _tmp = setup
    p.enable_undo()
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    edit.insertPlainText(" More.")
    assert panel._timer.isActive()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    text_objects.remove_object(edit, "figure", f1)
    assert not warned
    assert f1 not in edit.objects.figures()
    assert "More." in p.read_owner_text(p.text_owner(notes.id))   # saved before removing
    panel._save_text()                     # (the text's autosave after the removal)
    # Edit ▸ Undo back to before the removal: the figure is back; the typing stays.
    labels = [st.label for st in p.journal.undo_stack]
    assert any(lb.startswith("Remove") for lb in labels)
    while not p.undo().label.startswith("Remove"):
        pass
    owner = p.text_owner(notes.id)
    assert f1 in p.read_owner_text(owner) and "More." in p.read_owner_text(owner)
    panel.deleteLater()


# ------------------------------------------------------------ unreadable thumbnail (review-2)

def test_a_picture_that_cannot_be_read_yet_is_read_again(qapp, tmp_path, monkeypatch):
    # e.g. a picture locked by OneDrive for a moment: not kept as a blank picture.
    path = picture(tmp_path / "synced.png", 120, 60)
    text_objects._thumbs.clear()
    real = text_objects.read_image
    calls = []

    def busy_once(p, side):
        calls.append(p)
        return QImage() if len(calls) == 1 else real(p, side)

    monkeypatch.setattr(text_objects, "read_image", busy_once)
    assert text_objects.thumbnail(path).isNull()
    assert not text_objects._thumbs
    again = text_objects.thumbnail(path)            # same file, same mtime and size
    assert not again.isNull() and again.width() == 120
    assert len(calls) == 2


def test_a_pdf_that_cannot_be_read_yet_gets_its_size_later(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import clipboard_image
    path = tmp_path / "scheme.pdf"
    path.write_bytes(b"%PDF-1.4 stand-in")
    text_objects._thumbs.clear()
    page = QImage(250, 100, QImage.Format.Format_RGB32)
    page.fill(0xFFFFFF)
    results = [None, page]
    monkeypatch.setattr(clipboard_image, "render_pdf", lambda data, dpi: results.pop(0))
    assert text_objects.picture_inches(path) is None
    assert text_objects.picture_inches(path) == pytest.approx((2.5, 1.0))
