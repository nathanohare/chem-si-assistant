"""Alignment of paragraphs, figures and tables (SPEC §13l.10): the stored markup, the
editor, copy and paste, and the compiled Typst."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from si_assistant.compile.text import aligned_object, aligned_paragraph, paragraphs_to_typst
from si_assistant.core.markup import (Paragraph, Run, effective_align, parse, serialize,
                                      stored_align)
from si_assistant.core.tables import Cell, Table
from si_assistant.ui import rich_text
from si_assistant.ui.panels import TextSectionPanel
from si_assistant.ui.rich_text import RichTextEditor

AF = Qt.AlignmentFlag


# ---------------------------------------------------------------------- stored markup

def test_alignment_prefix_is_read():
    p = parse("!align{center} Centred **text**\n!align{right} # A heading\n"
              "!align{left} !figure{f-1a} Caption\n!align{right} !table{t-2b}\nplain")
    assert (p[0].align, p[0].plain_text, p[0].runs[1].bold) == ("center", "Centred text", True)
    assert (p[1].align, p[1].heading, p[1].plain_text) == ("right", 1, "A heading")
    assert (p[2].align, p[2].figure, p[2].plain_text) == ("left", "f-1a", "Caption")
    assert (p[3].align, p[3].table, p[3].runs) == ("right", "t-2b", [])
    assert (p[4].align, p[4].plain_text) == ("", "plain")


def test_alignment_spellings_and_unknown_words():
    assert parse("!align{centre} x")[0].align == "center"
    assert parse("!align{Justified} x")[0].align == "justify"
    # Not an alignment: kept as text, and written back escaped.
    (p,) = parse("!align{middle} x")
    assert p.align == "" and p.plain_text == "!align{middle} x"
    assert serialize([p]) == "\\!align{middle\\} x" and parse(serialize([p])) == [p]


def test_defaults_are_not_written():
    """Text is justified and figures/tables centred by default: old files stay as they are."""
    assert serialize([Paragraph([Run("a")], align="justify")]) == "a"
    assert serialize([Paragraph([Run("a")], figure="f-1", align="center")]) == "!figure{f-1} a"
    assert serialize([Paragraph([], table="t-1", align="justify")]) == "!table{t-1}"
    assert serialize([Paragraph([Run("a")], align="left")]) == "!align{left} a"
    assert serialize([Paragraph([], align="center")]) == "!align{center}"
    text = "# Head\nplain\n!figure{f-1} cap"
    assert serialize(parse(text)) == text


@pytest.mark.parametrize("paragraphs", [
    [Paragraph([Run("Centred")], align="center"), Paragraph([Run("x")])],
    [Paragraph([Run("Title", italic=True)], heading=2, align="right")],
    [Paragraph([Run("!align{left} not a prefix")], align="right")],
    [Paragraph([Run("# not a heading")], align="center")],
    [Paragraph([Run(" leading space")], align="left")],
    [Paragraph([Run("cap")], figure="f-1", align="right"), Paragraph([], table="t-1",
                                                                     align="left")],
    [Paragraph([], heading=1, align="center")],
])
def test_roundtrip_with_alignment(paragraphs):
    assert parse(serialize(paragraphs)) == paragraphs


def test_effective_and_stored_alignment():
    assert effective_align("", False) == "justify" and effective_align("", True) == "center"
    assert effective_align("justify", True) == "center"   # a figure can't be justified
    assert stored_align("justify", False) == "" and stored_align("center", True) == ""
    assert stored_align("center", False) == "center" and stored_align("left", True) == "left"
    assert Paragraph([], figure="f", align="right").alignment == "right"


# ---------------------------------------------------------------------- Typst

def test_typst_for_aligned_paragraphs_and_objects():
    out = paragraphs_to_typst([Paragraph([Run("a")], align="center"),
                               Paragraph([Run("b")]),
                               Paragraph([Run("H")], heading=1, align="right")])
    parts = out.split("\n\n")
    assert parts[0] == '#align(center)[#set par(justify: false)\n#"a"]'
    assert parts[1] == '#"b"'
    assert parts[2].startswith("#align(right)[#set par(justify: false)\n#heading(level: 2")
    assert aligned_paragraph('#"x"', "justify") == '#"x"'
    assert aligned_object("#si-figure(x)", "") == "#si-figure(x)"
    assert aligned_object("#si-figure(x)", "left") == \
        "#[#show figure: set align(left)\n#si-figure(x)]"


def test_compiled_si_aligns_text_figures_and_tables(qapp, tmp_path):
    from tests.sample_project import build_sample
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    p, parts = build_sample(tmp_path)
    notes = parts["notes"]
    table, figure = parts["table"], parts["figure"]
    p.write_section_text(notes, f"!align{{center}} Centred text.\n"
                                f"!align{{left}} !table{{{table}}} Left table\n"
                                f"!align{{right}} !figure{{{figure}}} Right figure\n"
                                "!align{right} ## A right heading\nJustified text.")
    p.write_front_text("general", "!align{right} Right in General Information.")
    p.write_front_text("cover", "!align{center} Centred under the affiliations.")
    # A compound's own SI text follows the journal style: an alignment there is ignored.
    c2 = parts["c2"]
    p.add_field(c2, {"key": "si_text"})
    p.write_compound_text(c2, "si_text", "!align{center} Own text of 2.")
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                         convert_to_png)
    assert result.pdf.exists()
    typ = result.typ.read_text(encoding="utf-8")
    assert '#align(center)[#set par(justify: false)\n#"Centred text."]' in typ
    assert "#[#show figure: set align(left)\n#si-table(" in typ
    assert "#[#show figure: set align(right)\n#si-figure(" in typ
    assert '#align(right)[#set par(justify: false)\n#"Right in General Information."]' in typ
    assert '#align(center)[#set par(justify: false)\n#"Centred under the affiliations."]' in typ
    assert '#"Own text of 2."' in typ and '#align(center)[#set par(justify: false)\n#"Own' \
        not in typ
    assert result.warnings == []
    p.close()


def test_use_as_general_information_copies_aligned_figures(project, tmp_path):
    from PySide6.QtGui import QImage
    img = QImage(40, 20, QImage.Format.Format_RGB32)
    img.fill(0x3366FF)
    img.save(str(tmp_path / "f.png"))
    s = project.add_section("text", "Old general")
    owner = project.text_owner(s.id)
    f = project.add_figure(owner, source=tmp_path / "f.png")
    t = project.add_table(owner, Table.new(2, 2))
    project.write_section_text(s, f"!align{{right}} !figure{{{f}}} cap\n!align{{left}} !table{{{t}}}")
    project.use_as_general_information(s.id)
    general = project.text_owner("general")
    assert f in project.figures(general) and project.read_table(general, t) is not None
    assert project.read_front_text("general").startswith(f"!align{{right}} !figure{{{f}}}")


# ---------------------------------------------------------------------- the editor

@pytest.fixture
def section(qapp, project, tmp_path):
    from PySide6.QtGui import QImage
    p = project
    notes = p.add_section("text", "Notes")
    owner = p.text_owner(notes.id)
    table = Table.new(2, 2)
    table.rows[0] = [Cell("Entry"), Cell("Yield")]
    t1 = p.add_table(owner, table)
    img = QImage(300, 120, QImage.Format.Format_RGB32)
    img.fill(0xFF3366)
    img.save(str(tmp_path / "pic.png"))
    f1 = p.add_figure(owner, source=tmp_path / "pic.png")
    p.write_section_text(notes, f"Intro text\n!align{{center}} Centred line\n"
                                f"!table{{{t1}}} Table caption\n"
                                f"!align{{right}} !figure{{{f1}}} Figure caption\nEnd")
    rich_text.set_link_context(rich_text.LinkContext(p))
    yield p, notes, t1, f1
    rich_text.set_link_context(None)


def block(edit, n):
    return edit.document().findBlockByNumber(n)


def cursor_in(edit, n, offset=0):
    c = QTextCursor(block(edit, n))
    c.setPosition(c.position() + offset)
    edit.setTextCursor(c)
    return c


def test_editor_shows_and_keeps_the_alignment(section):
    p, notes, t1, f1 = section
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    assert edit.alignment_enabled and panel.editor.align_button is not None
    assert edit.markup() == p.read_section_text(notes)
    shown = [rich_text.align_name(block(edit, i).blockFormat().alignment()) for i in range(5)]
    # Intro and End justified (the default), the table centred (its default), the figure right.
    assert shown == ["justify", "center", "center", "right", "justify"]
    # The button follows the cursor; a figure or table can't be justified.
    cursor_in(edit, 3)
    assert panel.editor.align_acts["right"].isChecked()
    assert not panel.editor.align_acts["justify"].isEnabled()
    cursor_in(edit, 0)
    assert panel.editor.align_acts["justify"].isChecked()
    assert panel.editor.align_acts["justify"].isEnabled()
    panel.close()


def test_choosing_an_alignment_is_saved_and_can_be_undone(section):
    p, notes, t1, f1 = section
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    cursor_in(edit, 0)
    panel.editor.align_acts["right"].trigger()
    assert edit.markup().split("\n")[0] == "!align{right} Intro text"
    # The table: left; "justify" is ignored for it.
    cursor_in(edit, 2)
    panel.editor.align_acts["left"].trigger()
    edit.set_alignment("justify")
    assert edit.markup().split("\n")[2] == f"!align{{left}} !table{{{t1}}} Table caption"
    # Back to the default: nothing written.
    panel.editor.align_acts["center"].trigger()
    assert edit.markup().split("\n")[2] == f"!table{{{t1}}} Table caption"
    # Several paragraphs at once (a selection).
    c = cursor_in(edit, 0)
    c.setPosition(block(edit, 1).position() + 3, QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(c)
    panel.editor.align_acts["center"].trigger()
    lines = edit.markup().split("\n")
    assert lines[0] == "!align{center} Intro text" and lines[1] == "!align{center} Centred line"
    # Saved like any edit; Ctrl+Z in the text box undoes it.
    panel.flush()
    assert p.read_section_text(notes) == edit.markup()
    edit.document().undo()
    assert edit.markup().split("\n")[0] == "!align{right} Intro text"
    panel.close()


def test_enter_after_a_figure_caption_gives_plain_text(section):
    from PySide6.QtTest import QTest
    p, notes, t1, f1 = section
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    c = cursor_in(edit, 3)
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    edit.setTextCursor(c)
    QTest.keyClick(edit, Qt.Key.Key_Return)
    QTest.keyClicks(edit, "New")
    lines = edit.markup().split("\n")
    assert lines[3] == f"!align{{right}} !figure{{{f1}}} Figure caption" and lines[4] == "New"
    assert rich_text.align_name(block(edit, 4).blockFormat().alignment()) == "justify"
    # Typing in front of the (right) figure: a new line above it, the figure stays right.
    cursor_in(edit, 3)
    QTest.keyClicks(edit, "X")
    lines = edit.markup().split("\n")
    assert lines[3] == "X" and lines[4].startswith("!align{right} !figure{")
    # Text with line breaks pasted into a caption: plain text lines.
    c = cursor_in(edit, 4)
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    edit.setTextCursor(c)
    from PySide6.QtCore import QMimeData
    data = QMimeData()
    data.setText(" more\nnext line")
    edit.insertFromMimeData(data)
    lines = edit.markup().split("\n")
    assert lines[4].endswith("Figure caption more") and lines[5] == "next line"
    assert rich_text.align_name(block(edit, 5).blockFormat().alignment()) == "justify"
    panel.close()


def test_new_figure_is_centred_and_takes_an_empty_lines_alignment(section):
    p, notes, t1, f1 = section
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    store = edit.objects
    t2 = store.new_table(Table.new(1, 1))
    cursor_in(edit, 0)
    edit.insert_object("table", t2)          # after a line of text: a new, centred line
    assert edit.markup().split("\n")[1] == f"!table{{{t2}}}"
    t3 = store.new_table(Table.new(1, 1))
    c = QTextCursor(edit.document().lastBlock())   # "End"
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    c.insertBlock()
    edit.setTextCursor(c)
    edit.set_alignment("right")              # an empty line set to the right…
    edit.insert_object("table", t3)          # …keeps it for the table put there
    assert edit.markup().split("\n")[-1] == f"!align{{right}} !table{{{t3}}}"
    panel.close()


def test_copy_and_paste_keep_the_alignment(section, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    told = []   # (a figure can't be pasted into a compound's text: the user is told)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    p, notes, t1, f1 = section
    panel = TextSectionPanel(p, notes)
    edit = panel.editor.edit
    # Copy the centred line and the right figure (whole paragraphs).
    c = cursor_in(edit, 1)
    c.setPosition(block(edit, 3).position() + block(edit, 3).length() - 1,
                  QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(c)
    mime = edit.createMimeDataFromSelection()
    copied = bytes(mime.data(rich_text.MARKUP_MIME)).decode("utf-8").split("\n")
    assert copied[0] == "!align{center} Centred line"
    assert copied[2].startswith("!align{right} !figure{")
    # Into another custom section, on an empty line: kept.
    other = p.add_section("text", "Other")
    panel2 = TextSectionPanel(p, other)
    panel2.editor.edit.insertFromMimeData(mime)
    pasted = panel2.editor.markup().split("\n")
    assert pasted[0] == "!align{center} Centred line"
    assert pasted[2].startswith("!align{right} !figure{")
    # Part of a line pastes as part of the line it goes in.
    c = cursor_in(edit, 1, 3)
    c.setPosition(c.position() + 4, QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(c)
    part = edit.createMimeDataFromSelection()
    assert bytes(part.data(rich_text.MARKUP_MIME)).decode("utf-8") == "tred"
    # Into a text that follows the journal style (e.g. a compound's procedure): dropped.
    procedure = RichTextEditor(headings=False)
    procedure.edit.insertFromMimeData(mime)
    assert "!align" not in procedure.markup()
    assert procedure.markup().startswith("Centred line")
    panel.close()
    panel2.close()


def test_cover_page_text_and_general_information_can_be_aligned(qapp, project):
    from si_assistant.ui.front_matter import CoverPagePanel, GeneralInfoPanel
    project.write_front_text("cover", "!align{right} Thanks to all.")
    project.write_front_text("general", "!align{center} Centred.")
    cover = CoverPagePanel(project)
    assert cover.extra.align_button is not None
    assert cover.extra.markup() == "!align{right} Thanks to all."
    general = GeneralInfoPanel(project)
    assert general.editor.align_button is not None
    assert general.editor.markup() == "!align{center} Centred."
    # Compound texts have no alignment button.
    assert RichTextEditor(headings=False).align_button is None


def test_table_editor_shows_the_cells_alignment(qapp):
    from si_assistant.ui.table_editor import TableEditorDialog
    table = Table.new(2, 2)
    table.columns = ["left", "center"]
    table.rows[1][1].align = "right"
    dlg = TableEditorDialog(table)
    checked = lambda: [k for k in ("left", "center", "right") if dlg.actions_[k].isChecked()]
    dlg.grid.setCurrentCell(0, 0)
    assert checked() == ["left"]
    dlg.grid.setCurrentCell(0, 1)
    assert checked() == ["center"]
    dlg.grid.setCurrentCell(1, 1)
    assert checked() == ["right"]
    dlg.actions_["left"].trigger()
    assert checked() == ["left"] and dlg.result_table().rows[1][1].align == "left"
