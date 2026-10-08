"""The editor's figures and tables, the table editor, labels in the UI (SPEC §13k.5, 5a)."""

import json

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication, QImage, QTextCursor
from PySide6.QtWidgets import QMessageBox, QTableWidgetSelectionRange

from si_assistant.core import scheme_editor as se
from si_assistant.core import labels
from si_assistant.core.tables import Cell, Table
from si_assistant.ui import rich_text, text_objects
from si_assistant.ui.panels import TextSectionPanel
from si_assistant.ui.table_editor import TableEditorDialog, block_from_html, markup_html


def picture(path, color=0xFF3366):
    img = QImage(300, 120, QImage.Format.Format_RGB32)
    img.fill(color)
    img.save(str(path))
    return path


@pytest.fixture
def setup(qapp, project, tmp_path):
    p = project
    notes = p.add_section("text", "Optimization")
    owner = p.text_owner(notes.id)
    table = Table.new(2, 2)
    table.rows[0] = [Cell("Entry"), Cell("Yield")]
    table.rows[1] = [Cell("1"), Cell("**90%**")]
    t1 = p.add_table(owner, table)
    f1 = p.add_figure(owner, source=picture(tmp_path / "trace.png"))
    p.write_section_text(notes, f"See @{{{t1}}}.\n!table{{{t1}}} Screening\n"
                                f"!figure{{{f1}}} HPLC *trace*")
    rich_text.set_link_context(rich_text.LinkContext(p))
    yield p, notes, t1, f1, tmp_path
    rich_text.set_link_context(None)


def chips(edit):
    out = []
    block = edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.objectType() == rich_text.LINK_OBJECT:
                out.append(fmt.property(rich_text.LINK_LABEL))
            it += 1
        block = block.next()
    return out


def test_panel_shows_figures_and_tables_and_keeps_the_text(setup):
    p, notes, t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    assert edit.markup() == p.read_section_text(notes)
    assert chips(edit) == ["Table S1"]
    assert edit.objects.label("table", t1) == ("Table S1", False)
    assert edit.objects.label("figure", f1) == ("Figure S1", False)
    assert edit.object_caption("figure", f1)[1].italic
    # The toolbar offers figures and tables.
    assert panel.editor.picture_act.text() == "Figure…" and panel.editor.table_act.isEnabled()
    # Drawing works (the objects have a size and paint without errors).
    panel.resize(700, 600)
    panel.show()
    qapp_process()
    assert panel.editor.edit.document().size().height() > 150
    panel.close()


def qapp_process():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def test_labels_follow_unsaved_edits(setup):
    p, notes, t1, f1, tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    store = edit.objects
    new = store.add_file(picture(tmp / "first.png", 0x3366FF))
    edit.moveCursor(QTextCursor.MoveOperation.Start)
    edit.insert_object("figure", new)
    assert store.label("figure", new) == ("Figure S1", False)
    assert store.label("figure", f1) == ("Figure S2", False)   # not saved yet
    panel.flush()
    assert labels.compute(p).label(f1) == "Figure S2"
    text = edit.markup()
    assert text.index(f"!figure{{{new}}}") < text.index(f"!figure{{{f1}}}")


def test_older_pictures_are_upgraded_when_opened(qapp, project, tmp_path):
    p = project
    s = p.add_section("text", "Old")
    rel = p.add_section_image(s, source=picture(tmp_path / "old.png"))
    p.write_section_text(s, f"!image{{{rel}}} Old one")
    panel = TextSectionPanel(p, s)
    text = p.read_section_text(s)
    assert text.startswith("!figure{f-") and text.endswith("} Old one")
    assert panel.editor.markup() == text


def test_copy_and_paste_figures_and_tables(setup):
    p, notes, t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    edit.selectAll()
    mime = edit.createMimeDataFromSelection()
    found = json.loads(bytes(mime.data(rich_text.OBJECTS_MIME)).decode("utf-8"))
    assert set(found) == {t1, f1}
    # Into another section: copies with new IDs (the originals are still in their text).
    other = p.add_section("text", "Elsewhere")
    panel2 = TextSectionPanel(p, other)
    panel2.editor.edit.insertFromMimeData(mime)
    pasted = panel2.editor.markup()
    owner2 = p.text_owner(other.id)
    new_ids = [x for x in p.figures(owner2)]
    assert len(new_ids) == 1 and new_ids[0] != f1 and f"!figure{{{new_ids[0]}}}" in pasted
    assert "HPLC *trace*" in pasted and f"!table{{{t1}}}" not in pasted
    assert p.figure_path(owner2, new_ids[0]).exists()
    # Cut and paste within the same text: the same figure (mentions stay right).
    edit.selectAll()
    mime = edit.createMimeDataFromSelection()
    edit.textCursor().removeSelectedText()
    edit.insertFromMimeData(mime)
    assert f"!figure{{{f1}}}" in edit.markup() and f"!table{{{t1}}}" in edit.markup()
    # Pasting again while it is still there: a copy.
    edit.moveCursor(QTextCursor.MoveOperation.End)
    edit.insertFromMimeData(mime)
    assert len(p.figures(p.text_owner(notes.id))) == 2


def test_remove_figure_goes_to_trash(setup, monkeypatch):
    p, notes, t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    monkeypatch.setattr(text_objects.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    menu = text_objects.object_menu(edit, "figure", f1)
    assert [a.text() for a in menu.actions() if a.text()] == [
        "Edit label…", "Printed size…", "Replace picture with a file…",
        "Replace picture with the copied picture", "Remove figure (picture to the Trash)"]
    text_objects.remove_object(edit, "figure", f1)
    assert "!figure" not in edit.markup()
    entry = p.list_trash()[0]
    assert entry.kind == "figure" and entry.info["caption"] == "HPLC *trace*"
    panel.flush()
    p.restore_from_trash(entry.id)
    assert p.read_section_text(notes).endswith(f"!figure{{{f1}}} HPLC *trace*")


def test_edit_label_and_table_from_the_editor(setup, monkeypatch):
    p, notes, t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    monkeypatch.setattr(text_objects.QInputDialog, "getText",
                        lambda *a, **k: ("Figure S1", True))
    text_objects.edit_label(edit, "figure", f1)
    assert p.figures(p.text_owner(notes.id))[f1]["label"] == "Figure S1"

    def fake_exec(dlg):
        dlg.grid.setCurrentCell(1, 1)
        dlg.cell_edit.set_markup("*85%*")
        dlg._cell_text_changed()
        dlg._insert_row(1)
        dlg.caption_edit.set_markup("New **caption**")
        dlg.label_edit.setText("Table S9")
        return True

    monkeypatch.setattr(TableEditorDialog, "exec", fake_exec)
    text_objects.edit_table(edit, t1)
    saved = p.read_table(p.text_owner(notes.id), t1)
    assert saved.n_rows == 3 and saved.rows[1][1].text == "*85%*" and saved.label == "Table S9"
    assert f"!table{{{t1}}} New **caption**" in edit.markup()
    edit.refresh_links()
    panel.flush()
    rich_text.link_context().invalidate()
    edit.refresh_links()
    assert chips(edit) == ["Table S9"]


def test_new_table_from_the_toolbar(setup, monkeypatch):
    p, notes, _t1, _f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    monkeypatch.setattr(TableEditorDialog, "exec", lambda dlg: True)
    panel.editor.edit.moveCursor(QTextCursor.MoveOperation.End)
    text_objects.new_table(panel.editor.edit)
    tables = [x for x in panel.editor.markup().split("\n") if x.startswith("!table")]
    assert len(tables) == 2


def test_table_editor_operations(qapp):
    t = Table.new(3, 3)
    dlg = TableEditorDialog(t, "Cap", "Table S4")
    assert dlg.label_edit.placeholderText().startswith("Table S4")
    dlg.grid.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 1), True)
    dlg._merge()
    assert dlg.grid.columnSpan(0, 0) == 2
    dlg.grid.setCurrentCell(0, 0)
    dlg._split()
    assert dlg.grid.columnSpan(0, 0) == 1
    dlg.grid.setCurrentCell(2, 2)
    dlg._insert_column(1)
    dlg._delete_rows()
    dlg.header_rows.setValue(2)
    dlg.grid.clearSelection()
    dlg.grid.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 0), True)
    dlg._align("right")
    dlg.notes_edit.set_markup("^{a} One.\n^{b} Two.")
    result = dlg.result_table()
    assert (result.n_rows, result.n_cols, result.header_rows) == (2, 4, 2)
    assert result.columns[0] == "right"
    assert result.footnotes == ["^{a} One.", "^{b} Two."]
    assert dlg.caption() == "Cap"


def test_paste_a_table_from_word_or_excel(qapp):
    html = ("<table><tr><td><b>Entry</b></td><td colspan='2'>Yield (%)</td></tr>"
            "<tr><td rowspan='2'>1</td><td>CH<sub>2</sub>Cl<sub>2</sub></td><td>90</td></tr>"
            "<tr><td>THF</td><td>12</td></tr></table>")
    block = block_from_html(html)
    assert block[0][0].text == "**Entry**" and block[0][1].colspan == 2 and block[0][2] is None
    assert block[1][0].rowspan == 2 and block[2][0] is None
    assert block[1][1].text == "CH_{2}Cl_{2}"
    dlg = TableEditorDialog(Table.new(1, 1), "", "")
    data = QMimeData()
    data.setHtml(html)
    QGuiApplication.clipboard().setMimeData(data)
    dlg.grid.setCurrentCell(0, 0)
    dlg.paste_table()
    result = dlg.result_table()
    assert (result.n_rows, result.n_cols) == (3, 3) and result.rows[1][0].rowspan == 2
    # Tab-separated text (Excel's plain text) works too.
    QGuiApplication.clipboard().setText("a\tb\n1\t2")
    dlg.grid.setCurrentCell(2, 2)
    dlg.paste_table()
    assert dlg.result_table().n_cols == 4
    assert "<sub>2</sub>" in markup_html("CH_{2}Cl_{2}")


def test_pasting_a_word_table_into_the_text_makes_a_table(setup, monkeypatch):
    p, notes, _t1, _f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    monkeypatch.setattr(TableEditorDialog, "exec", lambda dlg: True)
    data = QMimeData()
    data.setHtml("<table><tr><td>a</td><td>b</td></tr></table>")
    panel.editor.edit.moveCursor(QTextCursor.MoveOperation.End)
    panel.editor.edit.insertFromMimeData(data)
    new = [x for x in panel.editor.markup().split("\n") if x.startswith("!table")][-1]
    table = p.read_table(p.text_owner(notes.id), new[len("!table{"):new.index("}")])
    assert table.rows[0][0].text == "a"


def test_mention_picker_lists_figures_tables_and_schemes(setup):
    p, notes, t1, f1, _tmp = setup
    data = p.add_section("data", "Products")
    scheme = p.add_scheme(data.id, name="Route")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme), "show_label": True})   # (numbered: SPEC §13p.1)
    groups = dict(rich_text.link_context().targets())
    assert [i for i, _t in groups["Tables"]] == [t1]
    assert [i for i, _t in groups["Figures"]] == [f1]
    assert groups["Schemes"][0][0] == scheme.id and "Scheme S1" in groups["Schemes"][0][1]
    # Mentions in a caption are shown as they look.
    p.write_section_text(notes, p.read_section_text(notes) + f" (see @{{{t1}}})")
    rich_text.link_context().invalidate()
    figures = dict(rich_text.link_context().targets())["Figures"]
    assert "HPLC trace (see Table S1)" in figures[0][1]
    dlg = rich_text.PickerDialog("Mention", [(None, "Tables"), (t1, "Table S1 Screening"),
                                             (None, "Figures"), (f1, "Figure S1 trace")])
    assert dlg.values() == [t1]          # the group titles cannot be picked
    dlg.search.setText("trace")
    assert dlg.list.item(0).isHidden() and not dlg.list.item(2).isHidden()
    assert dlg.values() == [f1]


def test_duplicate_label_is_flagged_in_chips_and_warnings(setup):
    p, notes, t1, f1, _tmp = setup
    from si_assistant.ui.warnings_panel import WarningsPanel
    data = p.add_section("data", "Products")
    scheme = p.add_scheme(data.id, name="Route")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme), "show_label": True})   # (numbered: SPEC §13p.1)
    p.set_scheme_label(scheme.id, "Table S1")
    ctx = rich_text.link_context()
    ctx.invalidate()
    assert ctx.duplicate("c:" + t1) and not ctx.duplicate("c:c-000000")
    panel = WarningsPanel()
    opened = []
    panel.open_place.connect(lambda kind, item_id: opened.append((kind, item_id)))
    panel.set_project(p)
    group = panel.tree.topLevelItem(0)
    assert group.text(0).startswith("Figures, tables, schemes and texts")
    assert group.childCount() == 2
    panel._open(group.child(0))
    assert opened == [("section", notes.id)]


def test_labels_tab(qapp, project):
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    dlg = ProjectSettingsDialog(project, "Tester")
    tab = dlg.labels_tab
    assert tab.examples["figure"].text().startswith("Figure S1, Figure S2")
    tab.style["table"].setCurrentIndex(tab.style["table"].findData("I"))
    tab.prefix["table"].setText("")
    assert tab.examples["table"].text().startswith("Table I, Table II")
    dlg.style_box.setCurrentText("RSC")
    assert tab.word["figure"].text() == "Fig."
    dlg.apply()
    assert project.settings["labels"] == {"table": {"prefix": "", "style": "I"}}
    assert labels.label_rules(project.settings)["figure"]["word"] == "Fig."


def test_scheme_label_and_inline_file_label(qapp, project, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.scheme_panel import SchemePanel
    p = project
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["1"])
    scheme = p.add_scheme(data.id, name="Route")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme), "show_label": True})   # (numbered: SPEC §13p.1)
    panel = SchemePanel(p, scheme, lambda: "Tester")
    assert panel.label_edit.placeholderText() == "Scheme S1 (automatic)"
    panel.label_edit.setText("Scheme 2b")
    panel.flush()
    assert p.scheme(scheme.id).data["label"] == "Scheme 2b"
    c = p.add_compound(data.id, name="X")
    p.add_field(c, {"key": "files"})
    p.attach_compound_file(c, "files", picture(tmp_path / "hplc.png"))
    cp = CompoundPanel(p, c, lambda: "Tester")
    editor = next(w for w in cp.findChildren(text_objects.QWidget)
                  if type(w).__name__ == "AttachmentsEditor" and w.defn.key == "files")
    label_box = editor.row_widgets[0][1]["label"]
    assert label_box.placeholderText() == "Figure S1 (automatic)"


# ---------------------------------------------------------------------- review fixes (S3)

def _two_objects(p, notes, t1, f1):
    p.write_section_text(notes, f"Intro text\n!figure{{{f1}}} Fig caption\n"
                                f"!table{{{t1}}} Tab caption\nAfter")
    return TextSectionPanel(p, notes)


def _cursor_at_block(edit, n):
    c = QTextCursor(edit.document().findBlockByNumber(n))
    edit.setTextCursor(c)
    return c


def test_figures_and_tables_are_never_joined_and_lost(setup):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    p, notes, t1, f1, _tmp = setup
    panel = _two_objects(p, notes, t1, f1)
    edit = panel.editor.edit
    before = edit.markup()
    # Backspace at the start of the table line (or of the figure line): nothing is joined.
    for n in (2, 1):
        _cursor_at_block(edit, n)
        QTest.keyClick(edit, Qt.Key.Key_Backspace)
        assert edit.markup() == before
    # Delete at the end of the line above a figure: nothing is joined either.
    c = _cursor_at_block(edit, 0)
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    edit.setTextCursor(c)
    QTest.keyClick(edit, Qt.Key.Key_Delete)
    assert edit.markup() == before
    # Typing in front of a figure starts a new line above it.
    _cursor_at_block(edit, 1)
    QTest.keyClicks(edit, "X")
    assert edit.markup() == before.replace("Intro text\n", "Intro text\nX\n")
    # Even if blocks do get joined (e.g. a selection deleted across them), every object
    # and its caption is kept as a paragraph of its own.
    edit.set_markup(before)
    c = QTextCursor(edit.document().findBlockByNumber(2))
    c.deletePreviousChar()            # table block joined onto the figure's caption
    c = QTextCursor(edit.document().findBlockByNumber(1))
    c.deletePreviousChar()            # figure block joined onto "Intro text"
    assert edit.markup() == before
    panel.flush()
    assert p.read_section_text(notes) == before


def test_pasting_an_object_into_a_line_keeps_the_line_apart(setup):
    p, notes, t1, f1, _tmp = setup
    panel = _two_objects(p, notes, t1, f1)
    edit = panel.editor.edit
    c = _cursor_at_block(edit, 1)
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(c)
    mime = edit.createMimeDataFromSelection()          # the figure paragraph
    c = _cursor_at_block(edit, 0)
    c.setPosition(c.position() + len("Intro"))
    edit.setTextCursor(c)
    edit.insertFromMimeData(mime)                       # a copy, in the middle of a line
    lines = edit.markup().split("\n")
    assert lines[0] == "Intro" and lines[1].startswith("!figure{f-")
    assert lines[1].endswith("} Fig caption") and lines[2] == " text"


def test_vector_only_clipboard_is_pasted_as_a_figure(setup, monkeypatch):
    """ChemDraw's EMF (Windows) / PDF (macOS) is not reported as an image by Qt."""
    from si_assistant.ui import clipboard_image
    p, notes, _t1, _f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    png = clipboard_image.image_to_png(QImage(40, 20, QImage.Format.Format_RGB32))
    monkeypatch.setattr(clipboard_image, "clipboard_has_vector", lambda: True)
    monkeypatch.setattr(clipboard_image, "clipboard_picture", lambda dpi=1200:
                        clipboard_image.PastedPicture(png, b"EMF bytes", ".emf"))
    QGuiApplication.clipboard().setMimeData(QMimeData())   # no formats Qt understands
    assert edit.canPaste()
    edit.moveCursor(QTextCursor.MoveOperation.End)
    edit.paste()
    owner = p.text_owner(notes.id)
    new = [i for i in edit.object_ids() if i.startswith("f-") and i != _f1]
    assert len(new) == 1
    assert p.figure_source(owner, new[0]).read_bytes() == b"EMF bytes"
    # A ChemDraw copy that also carries text is still a picture; Word text is text.
    chemdraw = QMimeData()
    chemdraw.setText("C1=CC=CC=C1")
    chemdraw.setData('application/x-qt-windows-mime;value="ChemDraw Interchange Format"', b"x")
    assert edit._picture_paste(chemdraw)
    word = QMimeData()
    word.setText("Some text")
    word.setImageData(QImage(10, 10, QImage.Format.Format_RGB32))
    assert not edit._picture_paste(word)
    # Editors without figures (compound texts) are not affected.
    plain = rich_text.RichTextEditor()
    assert not plain.edit._picture_paste(QMimeData())


def test_html_with_a_table_keeps_the_text_around_it(setup, monkeypatch):
    p, notes, _t1, _f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit

    def no_dialog(dlg):
        raise AssertionError("the table editor should not open for a mixed paste")

    monkeypatch.setattr(TableEditorDialog, "exec", no_dialog)
    data = QMimeData()
    data.setHtml("<p>First paragraph (important).</p><table><tr><td>a</td><td>b</td></tr>"
                 "<tr><td>1</td><td>2</td></tr></table><p>Second paragraph.</p>")
    data.setText("First paragraph (important).\na\tb\n1\t2\nSecond paragraph.")
    edit.moveCursor(QTextCursor.MoveOperation.End)
    edit.textCursor().insertBlock()     # a new empty line under the last figure's caption
    edit.insertFromMimeData(data)
    lines = edit.markup().split("\n")
    i = lines.index("First paragraph (important).")
    assert lines[i + 1].startswith("!table{t-") and lines[i + 2] == "Second paragraph."
    new_id = lines[i + 1][len("!table{"):lines[i + 1].index("}")]
    table = p.read_table(p.text_owner(notes.id), new_id)
    assert (table.n_rows, table.n_cols, table.header_rows) == (2, 2, 1)
    # One Excel cell is just its text.
    cell = QMimeData()
    cell.setHtml("<table><tr><td>88%</td></tr></table>")
    cell.setText("88%")
    edit.insertFromMimeData(cell)
    assert edit.markup().endswith("Second paragraph.88%")


def test_figures_cannot_be_pasted_where_they_cannot_be_kept(setup, monkeypatch):
    p, notes, t1, f1, _tmp = setup
    panel = _two_objects(p, notes, t1, f1)
    edit = panel.editor.edit
    edit.selectAll()
    mime = edit.createMimeDataFromSelection()
    told = []
    monkeypatch.setattr(rich_text.QMessageBox, "information",
                        lambda *a, **k: told.append(a[2]))
    plain = rich_text.RichTextEditor()                  # e.g. a compound's text field
    plain.edit.insertFromMimeData(mime)
    assert plain.markup() == "Intro text\nAfter"        # no captions without their objects
    assert told and "still on the clipboard" in told[0]


def test_remove_cannot_be_undone_into_a_broken_figure(setup, monkeypatch):
    p, notes, t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    monkeypatch.setattr(text_objects.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    text_objects.remove_object(edit, "table", t1)
    after = edit.markup()
    assert not edit.document().isUndoAvailable()
    edit.undo()
    assert edit.markup() == after and f"!table{{{t1}}}" not in after


def test_a_users_picture_file_gets_no_unrelated_original(setup):
    p, notes, _t1, _f1, tmp = setup
    panel = TextSectionPanel(p, notes)
    store = panel.editor.edit.objects
    (tmp / "hplc.pdf").write_bytes(b"%PDF-1.4 instrument report")
    figure_id = store.add_file(picture(tmp / "hplc.png"))
    owner = p.text_owner(notes.id)
    assert p.figure_source(owner, figure_id) is None
    assert not (owner.folder / "images" / "hplc.pdf").exists()


def test_figure_printed_size_from_the_menu(setup, monkeypatch):
    p, notes, _t1, f1, _tmp = setup
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    assert text_objects.parse_cm(" 8,5 cm") == 8.5 and text_objects.parse_cm("") is None
    with pytest.raises(ValueError):
        text_objects.parse_cm("big")
    monkeypatch.setattr(text_objects.QInputDialog, "getText", lambda *a, **k: ("8,5 cm", True))
    text_objects.edit_width(edit, f1)
    assert p.figure_width(p.text_owner(notes.id), f1) == 8.5
    w, _h = edit._figure_handler._picture_size(edit.document(), f1)
    assert abs(w - 8.5 / 2.54 * (edit.logicalDpiX() or 96)) < 1 or w <= text_objects.MAX_FIGURE_WIDTH
    monkeypatch.setattr(text_objects.QInputDialog, "getText", lambda *a, **k: ("", True))
    text_objects.edit_width(edit, f1)
    assert p.figure_width(p.text_owner(notes.id), f1) is None


def test_table_editor_asks_before_throwing_changes_away(qapp, monkeypatch):
    from si_assistant.ui import table_editor
    asked = []

    def answer(button):
        def question(*a, **k):
            asked.append(a[2])
            return button
        return question

    dlg = TableEditorDialog(Table.new(2, 2), "", "Table S1")
    dlg.show()
    monkeypatch.setattr(table_editor.QMessageBox, "question", answer(None))
    dlg.reject()                      # nothing changed: closes without asking
    assert not dlg.isVisible() and not asked
    dlg = TableEditorDialog(Table.new(2, 2), "", "Table S1")
    dlg.show()
    dlg.grid.setCurrentCell(0, 0)
    dlg.cell_edit.set_markup("hello")
    dlg._cell_text_changed()
    monkeypatch.setattr(table_editor.QMessageBox, "question",
                        answer(QMessageBox.StandardButton.Cancel))
    dlg.reject()                      # e.g. Escape pressed twice: asks, and stays open
    assert dlg.isVisible() and asked
    monkeypatch.setattr(table_editor.QMessageBox, "question",
                        answer(QMessageBox.StandardButton.Discard))
    dlg.reject()
    assert not dlg.isVisible()
    # A new table pasted from Word counts as a change too.
    pasted = Table.new(1, 2)
    pasted.rows[0][0].text = "Entry"
    dlg = TableEditorDialog(pasted, "", "", new=True)
    assert dlg.has_changes()
    dlg.split_box.setChecked(True)
    assert dlg.result_table().breakable
