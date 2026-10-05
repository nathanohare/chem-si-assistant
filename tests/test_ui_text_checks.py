"""Underlines, right-click fixes and the Chemical formula toggle in the editors (SPEC §13k.1)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest

from si_assistant.ui import rich_text, text_checks
from si_assistant.ui.rich_text import RichTextEditor

WAVE = QTextCharFormat.UnderlineStyle.WaveUnderline


def underlines(editor, colour):
    """The underlined words (all blocks) in one colour."""
    out = []
    block = editor.edit.document().begin()
    while block.isValid():
        for r in block.layout().formats():
            if r.format.underlineStyle() == WAVE and r.format.underlineColor().name() == colour:
                out.append(block.text()[r.start:r.start + r.length])
        block = block.next()
    return out


def red(editor):
    return underlines(editor, text_checks.SPELLING_COLOUR)


def blue(editor):
    return underlines(editor, text_checks.FORMATTING_COLOUR)


def menu_at(editor, word, occurrence=0):
    """The right-click menu for a word, as the editor would build it."""
    edit = editor.edit
    text = edit.toPlainText()
    pos = -1
    for _ in range(occurrence + 1):
        pos = text.index(word, pos + 1)
    menu = edit.createStandardContextMenu()
    text_checks.add_check_actions(menu, edit, pos + 1)
    return menu


def action(menu, starts):
    return next(a for a in menu.actions() if a.text().startswith(starts))


@pytest.fixture
def editor(qapp):
    ed = RichTextEditor()
    ed.resize(700, 300)
    yield ed
    ed.deleteLater()


def test_underlines_do_not_change_the_text(editor):
    changes = []
    editor.changed.connect(lambda: changes.append(1))
    stored = "**Teh** crude PdCl2 in CDCl_{3} was purified by chromatograhpy (8.0Hz)."
    editor.set_markup(stored)
    assert red(editor) == ["Teh", "chromatograhpy"]
    assert blue(editor) == ["PdCl2", "8.0Hz"]          # CDCl₃ is already right
    assert editor.markup() == stored
    text_checks.checks().ignore("teh")                  # underlines everything again
    assert red(editor) == ["chromatograhpy"]
    assert editor.markup() == stored
    assert not editor.edit.document().isModified() and not changes
    assert not editor.edit.document().isUndoAvailable()


def test_nmr_reports_get_formatting_checks_but_no_spelling(qapp):
    ed = RichTextEditor(links=False)
    ed.set_markup("1H NMR (400 MHz, CDCl3) δ 7.36-7.29 (m, 2H), teh")
    assert red(ed) == []
    assert blue(ed) == ["1H", "CDCl3", "7.36-7.29"]


@pytest.mark.parametrize("stored,word,expected", [
    ("dried over Na2SO4.", "Na2SO4", "dried over Na_{2}SO_{4}."),
    ("in DMSO-d6", "DMSO", "in DMSO-*d*_{6}"),
    ("**1H NMR**", "1H", "**^{1}H NMR**"),
    ("J = 8.0Hz", "8.0Hz", "J = 8.0 Hz"),
    ("5mol% PdCl_{2}", "5mol", "5 mol % PdCl_{2}"),
    ("δ 7.36-7.29", "7.36", "δ 7.36–7.29"),
])
def test_each_fix_edits_the_text_as_one_undo_step(editor, stored, word, expected):
    editor.set_markup(stored)
    menu = menu_at(editor, word)
    action(menu, "Fix:").trigger()
    assert editor.markup() == expected
    assert blue(editor) == []
    editor.edit.undo()
    assert editor.markup() == stored


def test_fix_all(editor):
    editor.set_markup("PdCl2 at 25°C\n1H NMR δ 1.2-1.0, see @{c-missing}")
    menu = menu_at(editor, "PdCl2")
    every = action(menu, "Fix all 4")
    every.trigger()
    assert editor.markup() == "PdCl_{2} at 25 °C\n^{1}H NMR δ 1.2–1.0, see @{c-missing}"
    editor.edit.undo()
    assert editor.markup() == "PdCl2 at 25°C\n1H NMR δ 1.2-1.0, see @{c-missing}"


def test_suggestions_replace_the_word(editor):
    editor.set_markup("Purified by *chromatograhpy*.")
    menu = menu_at(editor, "chromatograhpy")
    action(menu, "chromatography").trigger()
    assert editor.markup() == "Purified by *chromatography*."
    assert red(editor) == []


def test_adding_words_to_the_dictionaries(qapp, project, monkeypatch):
    text_checks.set_project(project)
    ed = RichTextEditor()
    other = RichTextEditor()
    ed.set_markup("The fooxane and the barzyne")
    other.set_markup("fooxane")
    assert red(ed) == ["fooxane", "barzyne"] and red(other) == ["fooxane"]
    action(menu_at(ed, "fooxane"), "Add “fooxane” to this project").trigger()
    assert (project.folder / "dictionary.txt").read_text(encoding="utf-8") == "fooxane\n"
    assert red(ed) == ["barzyne"] and red(other) == []   # every open text box is updated
    action(menu_at(ed, "barzyne"), "Add “barzyne” to my dictionary").trigger()
    assert text_checks.personal_dictionary_path().read_text(encoding="utf-8") == "barzyne\n"
    assert red(ed) == []
    # A new session reads both dictionaries back.
    monkeypatch.setattr(text_checks, "_hub", None)
    text_checks.set_project(project)
    assert text_checks.checks().speller.misspelled("fooxane barzyne quxone") == [(16, 22)]
    # Read-only projects can't take words; "my dictionary" still can.
    project.read_only = True
    ed.set_markup("quxone")
    menu = menu_at(ed, "quxone")
    assert not action(menu, "Add “quxone” to this project").isEnabled()
    assert action(menu, "Add “quxone” to my dictionary").isEnabled()
    text_checks.set_project(None)


def test_right_click_shows_the_checks(editor, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    shown = []
    monkeypatch.setattr(text_checks, "show_menu", lambda menu, *a: shown.append(
        [act.text() for act in menu.actions()]))
    editor.set_markup("teh PdCl2")
    editor.show()
    cursor = QTextCursor(editor.edit.document())
    cursor.setPosition(1)
    point = editor.edit.cursorRect(cursor).center()
    editor.edit.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                                   editor.edit.viewport().mapToGlobal(point)))
    assert shown and shown[0][0] == "the"
    assert any(t.startswith("Ignore “teh”") for t in shown[0])
    assert any(t.startswith("Copy") or "Copy" in t for t in shown[0])   # Qt's own items stay
    cursor.setPosition(6)
    point = editor.edit.cursorRect(cursor).center()
    editor.edit.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                                   editor.edit.viewport().mapToGlobal(point)))
    assert "Fix: PdCl₂" in shown[1]


def test_ignore_lasts_for_the_session(editor, monkeypatch):
    editor.set_markup("quxone")
    action(menu_at(editor, "quxone"), "Ignore “quxone”").trigger()
    assert red(editor) == []
    monkeypatch.setattr(text_checks, "_hub", None)
    editor.set_markup("quxone")
    assert red(editor) == ["quxone"]


def test_read_only_text_offers_no_edits(editor):
    editor.set_markup("teh PdCl2")
    editor.set_read_only(True)
    assert not action(menu_at(editor, "teh"), "the").isEnabled()
    assert not action(menu_at(editor, "PdCl2"), "Fix:").isEnabled()


def test_the_notes_box_gets_spelling_only(qapp, project):
    from PySide6.QtWidgets import QPlainTextEdit
    edit = QPlainTextEdit()
    text_checks.install_spelling(edit)
    edit.setPlainText("teh PdCl2 sample")
    formats = edit.document().begin().layout().formats()
    assert [(r.start, r.length) for r in formats if r.format.underlineStyle() == WAVE] == [(0, 3)]


# ---------------------------------------------------------------------- Chemical formula toggle

def type_text(editor, text):
    editor.edit.setFocus()
    QTest.keyClicks(editor.edit, text)


def test_formulas_are_subscripted_as_you_type(editor):
    editor.set_markup("")
    assert editor.formula_act.isChecked()
    type_text(editor, "Dried over Na2SO4, then H2O.")
    assert editor.markup() == "Dried over Na_{2}SO_{4}, then H_{2}O."
    editor.edit.undo()            # one Ctrl+Z: the last subscript only
    assert editor.markup() == "Dried over Na_{2}SO_{4}, then H2O."
    editor.edit.document().clear()
    type_text(editor, "Under N2")
    QTest.keyClick(editor.edit, Qt.Key.Key_Return)
    assert editor.markup() == "Under N_{2}\n"
    QTest.keyClick(editor.edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.markup() == "Under N2\n"


def test_exceptions_are_not_subscripted(editor):
    editor.set_markup("")
    text = "An SN2 and E2 step on a C18 column gave 3a (Table S1, Figure S2, TS1, IC50). "
    type_text(editor, text)
    assert editor.markup() == text


def test_mentions_are_not_touched(qapp, project):
    rich_text.set_link_context(rich_text.LinkContext(project))
    try:
        ed = RichTextEditor()
        ed.set_markup("@{c-123456}")
        ed.edit.moveCursor(QTextCursor.MoveOperation.End)
        type_text(ed, " in CH2Cl2 ")
        assert ed.markup() == "@{c-123456} in CH_{2}Cl_{2} "
    finally:
        rich_text.set_link_context(None)


def test_the_toggle_is_shared_and_remembered(qapp, editor):
    other = RichTextEditor()
    editor.formula_act.trigger()             # off
    assert not other.formula_act.isChecked()
    assert text_checks._settings().value(text_checks.FORMULA_KEY) in (False, "false")
    other.set_markup("")
    type_text(other, "H2SO4 ")
    assert other.markup() == "H2SO4 "
    # A new session (fresh editors) starts with the remembered choice.
    text_checks._hub = None
    fresh = RichTextEditor()
    assert not fresh.formula_act.isChecked()
    fresh.formula_act.trigger()
    assert text_checks.checks().formula_on
    assert text_checks._settings().value(text_checks.FORMULA_KEY) in (True, "true")


def test_existing_subscripts_and_pasted_text_are_left_alone(editor):
    editor.set_markup("Pd_{2}(dba)_{3} in")
    editor.edit.moveCursor(QTextCursor.MoveOperation.End)
    type_text(editor, " ")
    assert editor.markup() == "Pd_{2}(dba)_{3} in "
    editor.edit.insertPlainText("CH2Cl2")     # pasting is not typing
    assert editor.markup() == "Pd_{2}(dba)_{3} in CH2Cl2"


# ---------------------------------------------------------------------- review fixes (S7)

def test_menus_show_their_tooltips(editor):
    editor.set_markup("teh PdCl2")
    assert menu_at(editor, "teh").toolTipsVisible()
    assert menu_at(editor, "PdCl2").toolTipsVisible()


def test_a_formatting_underline_can_be_ignored(editor, monkeypatch):
    editor.set_markup("with PdCl2 and NiCl2")
    assert blue(editor) == ["PdCl2", "NiCl2"]
    menu = menu_at(editor, "PdCl2")
    action(menu, "Ignore “PdCl2”").trigger()
    assert blue(editor) == ["NiCl2"]
    assert editor.markup() == "with PdCl2 and NiCl2"
    # The toggle leaves an ignored word alone too.
    editor.set_markup("")
    type_text(editor, "PdCl2 and NiCl2 ")
    assert editor.markup() == "PdCl2 and NiCl_{2} "
    monkeypatch.setattr(text_checks, "_hub", None)       # a new session underlines it again
    editor.set_markup("PdCl2")
    assert blue(editor) == ["PdCl2"]


def test_leave_as_written_in_this_project(qapp, project):
    from si_assistant.compile.text import format_chemistry, leave_alone
    from si_assistant.core.markup import Paragraph, Run, serialize
    text_checks.set_project(project)
    try:
        ed = RichTextEditor()
        ed.set_markup("Cs2CO3 and MnO2")
        assert blue(ed) == ["Cs2CO3", "MnO2"]
        action(menu_at(ed, "MnO2"), "Leave “MnO2” as written in this project").trigger()
        assert blue(ed) == ["Cs2CO3"]
        assert "MnO2" in project.dictionary_words()
        ed.set_markup("")
        type_text(ed, "MnO2 and Cs2CO3 ")
        assert ed.markup() == "MnO2 and Cs_{2}CO_{3} "
        with leave_alone(project.dictionary_words()):     # compiling prints it as written
            runs = format_chemistry([Run("MnO2 and Cs2CO3")])
        assert serialize([Paragraph(runs)]) == "MnO2 and Cs_{2}CO_{3}"
        project.read_only = True
        ed.set_markup("Cs2CO3")
        assert not action(menu_at(ed, "Cs2CO3"), "Leave").isEnabled()
        assert action(menu_at(ed, "Cs2CO3"), "Ignore").isEnabled()
    finally:
        text_checks.set_project(None)


def test_labels_are_not_subscripted_but_gases_are(editor):
    editor.set_markup("")
    type_text(editor, "assigned to H2 and H6, N2-alkylation, ")
    assert editor.markup() == "assigned to H2 and H6, N2-alkylation, "
    editor.set_markup("")
    type_text(editor, "under N2 and then an O2 atmosphere ")
    assert editor.markup() == "under N_{2} and then an O_{2} atmosphere "
    editor.edit.undo()                                  # one Ctrl+Z
    assert editor.markup() == "under N_{2} and then an O2 atmosphere "
    editor.edit.moveCursor(QTextCursor.MoveOperation.End)
    type_text(editor, "with ")                          # not subscripted again
    assert editor.markup() == "under N_{2} and then an O2 atmosphere with "
    editor.set_markup("")
    type_text(editor, "with H2 (1 atm) ")
    assert editor.markup() == "with H_{2} (1 atm) "


def test_ligand_formulas_are_subscripted_as_you_type(editor):
    editor.set_markup("")
    type_text(editor, "Pd2(dba)3, Ni(cod)2 and B2pin2 with iPr2NEt ")
    assert editor.markup() == ("Pd_{2}(dba)_{3}, Ni(cod)_{2} and B_{2}pin_{2} with "
                               "iPr_{2}NEt ")


def test_a_bad_dictionary_file_never_stops_the_program(qapp, project):
    project.dictionary_path.write_bytes("Buchwald\nna\xefve\n".encode("cp1252"))
    text_checks.personal_dictionary_path().write_bytes("Hartwig\ncaf\xe9\n".encode("cp1252"))
    text_checks.set_project(project)                    # builds the hub: reads both
    try:
        assert text_checks.checks().speller.misspelled("Buchwald naïve café teh") == [(20, 23)]
        ed = RichTextEditor()
        ed.set_markup("naïve")
        assert red(ed) == []
    finally:
        text_checks.set_project(None)
