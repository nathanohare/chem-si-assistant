"""Spelling and chemistry-formatting checks in the text boxes (SPEC §13k.1).

- Misspelled words get a red wavy underline (core/spelling.py decides what is wrong).
- Chemistry formatting problems get a blue wavy underline (core/chemtext.find_issues):
  a formula without subscripts, a number glued to its unit, a hyphen in a number range,
  "1H NMR" without a superscript, a deuterated solvent without subscripts.
- Right-click on an underlined word offers suggestions, "Add to dictionary" (the project's
  dictionary.txt), "Add to my dictionary" (this computer), "Ignore", or "Fix: …".

The underlines are drawn by a QSyntaxHighlighter, which only changes how the text looks:
the stored text (markup) is never changed by them. Only choosing a suggestion or a Fix
edits the text, as one Ctrl+Z step.

The "Chemical formula" toggle (§13k.1b, the "H₂" button on each text box's toolbar) is
remembered on this computer and shared by every text box; RichTextEdit uses
`subscript_formula` to subscript a formula when the word after it is started.
"""

from __future__ import annotations

import re
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, Signal
from PySide6.QtGui import (QAction, QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import QMenu, QMessageBox, QPlainTextEdit, QWidget

from .. import APP_NAME
from ..core import chemtext
from ..core.markup import NORMAL, SUB, SUPER
from ..core.spelling import Speller, add_word, read_words

PERSONAL_FILE = "my_dictionary.txt"
FORMULA_KEY = "editor/chemical_formula"
SPELLING_COLOUR = "#e0403c"     # (both readable on light and dark backgrounds)
FORMATTING_COLOUR = "#2f8cf0"
# Typing one of these after a word ends the word (the "Chemical formula" toggle then looks
# at it). Enter and Tab end a word too.
WORD_ENDS = " ,.;:!?)]}/–-\t\r\n "

_VALIGN = {QTextCharFormat.VerticalAlignment.AlignSuperScript: SUPER,
           QTextCharFormat.VerticalAlignment.AlignSubScript: SUB}
_QT_VALIGN = {SUPER: QTextCharFormat.VerticalAlignment.AlignSuperScript,
              SUB: QTextCharFormat.VerticalAlignment.AlignSubScript,
              NORMAL: QTextCharFormat.VerticalAlignment.AlignNormal}


def personal_dictionary_path() -> Path:
    """"My dictionary": this computer's own words, for every project (like the user
    name, a per-computer preference, not project data)."""
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return (Path(folder) if folder else Path.home() / ".si_assistant") / PERSONAL_FILE


def _settings() -> QSettings:
    return QSettings(APP_NAME, APP_NAME)


class TextChecks(QObject):
    """What every text box shares: the speller with its dictionaries, and the Chemical
    formula toggle. One for the program (`checks()`)."""

    changed = Signal()               # a dictionary changed: underline everything again
    formula_changed = Signal(bool)   # the Chemical formula toggle was switched

    def __init__(self):
        super().__init__()
        self.speller = Speller()
        self.project = None
        self._formula: bool | None = None
        # The highlighters to underline again when a dictionary changes. They are kept in
        # a list (not connected to `changed` one by one): a connection to a highlighter is
        # not always dropped when its text box is deleted, and calling a deleted one fails.
        self._highlighters: list = []
        self.changed.connect(self._rehighlight_all)
        self.speller.set_personal_words(read_words(personal_dictionary_path()))

    def add_highlighter(self, highlighter) -> None:
        self._prune()
        self._highlighters.append(highlighter)

    def highlighters(self) -> list:
        """The highlighters of text boxes that still exist."""
        self._prune()
        return list(self._highlighters)

    def _prune(self) -> None:
        self._highlighters = [h for h in self._highlighters if shiboken6.isValid(h)]

    def _rehighlight_all(self) -> None:
        for highlighter in self.highlighters():
            highlighter.rehighlight()

    def set_project(self, project) -> None:
        """The open project (its dictionary.txt is used), or None."""
        self.project = project
        try:
            words = project.dictionary_words() if project is not None else set()
        except Exception:  # noqa: BLE001 — a bad dictionary file must never stop a project
            words = set()
        self.speller.set_project_words(words)
        self.changed.emit()

    def leave(self) -> frozenset[str]:
        """Words the formatting check and the Chemical formula toggle leave alone."""
        return self.speller.leave()

    def can_add_to_project(self) -> bool:
        return self.project is not None and not getattr(self.project, "read_only", True)

    def add_to_project(self, word: str, parent: QWidget | None = None) -> None:
        try:
            with self.project.undo_step(f"Add “{word}” to the project's dictionary"):
                self.project.add_dictionary_word(word)
            self.speller.set_project_words(self.project.dictionary_words())
        except Exception as e:  # noqa: BLE001 — report, never lose the edit
            QMessageBox.warning(parent, "Could not add the word",
                                f"“{word}” could not be added to the project's dictionary."
                                f"\n\n{e}")
            return
        self.changed.emit()

    def add_to_personal(self, word: str, parent: QWidget | None = None) -> None:
        path = personal_dictionary_path()
        try:
            add_word(path, word)
        except Exception as e:  # noqa: BLE001 — report, never lose the edit
            QMessageBox.warning(parent, "Could not add the word",
                                f"“{word}” could not be added to your dictionary "
                                f"({path}).\n\n{e}")
            return
        self.speller.set_personal_words(read_words(path))
        self.changed.emit()

    def ignore(self, word: str) -> None:
        self.speller.ignore(word)
        self.changed.emit()

    @property
    def formula_on(self) -> bool:
        """The Chemical formula toggle (on unless switched off on this computer)."""
        if self._formula is None:
            value = _settings().value(FORMULA_KEY, True)
            self._formula = value if isinstance(value, bool) else str(value).lower() != "false"
        return self._formula

    def set_formula_on(self, on: bool) -> None:
        if bool(on) != self.formula_on:
            self._formula = bool(on)
            _settings().setValue(FORMULA_KEY, bool(on))
            self.formula_changed.emit(bool(on))


_hub: TextChecks | None = None


def checks() -> TextChecks:
    global _hub
    if _hub is None:
        _hub = TextChecks()
    return _hub


def set_project(project) -> None:
    checks().set_project(project)


# ---------------------------------------------------------------------- the underlines

def block_formats(block, length: int | None = None) -> list[tuple[str, bool]]:
    """(vertical alignment, italic) of each character of a text block."""
    n = block.length() - 1 if length is None else length
    out = [(NORMAL, False)] * n
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            fmt = frag.charFormat()
            value = (_VALIGN.get(fmt.verticalAlignment(), NORMAL), fmt.fontItalic())
            lo = frag.position() - block.position()
            for i in range(max(lo, 0), min(lo + frag.length(), n)):
                out[i] = value
        it += 1
    return out


def _underline(colour: str) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
    fmt.setUnderlineColor(QColor(colour))
    return fmt


class CheckHighlighter(QSyntaxHighlighter):
    """Draws the red (spelling) and blue (formatting) wavy underlines."""

    def __init__(self, document: QTextDocument, spelling: bool = True, formatting: bool = True):
        super().__init__(document)
        self.spelling = spelling
        self.formatting = formatting
        self._spell_fmt = _underline(SPELLING_COLOUR)
        self._format_fmt = _underline(FORMATTING_COLOUR)
        checks().add_highlighter(self)
        self.rehighlight()   # now (Qt would do it on the next turn, reporting a text change)

    def rehighlight(self) -> None:
        """Underline the whole text again. Qt reports this as a change of the text, which
        would trigger autosaves and previews, so the document stays quiet meanwhile."""
        document = self.document()
        if document is None:
            return
        quiet = document.blockSignals(True)
        try:
            super().rehighlight()
        finally:
            document.blockSignals(quiet)

    def set_checks(self, spelling: bool | None = None, formatting: bool | None = None) -> None:
        if spelling is not None:
            self.spelling = spelling
        if formatting is not None:
            self.formatting = formatting
        if self.document() is not None:
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt naming)
        if not text.strip():
            return
        if self.spelling:
            for start, end in checks().speller.misspelled(text):
                self.setFormat(start, end - start, self._spell_fmt)
        if self.formatting:
            for issue in issues_in(self.currentBlock(), text):
                self.setFormat(issue.start, issue.end - issue.start, self._format_fmt)


def issues_in(block, text: str | None = None) -> list[chemtext.Issue]:
    text = block.text() if text is None else text
    return chemtext.find_issues(text, block_formats(block, len(text)), checks().leave())


# ---------------------------------------------------------------------- changing the text

def apply_fix(document: QTextDocument, block_position: int, issue: chemtext.Issue,
              group: bool = True) -> None:
    """Make an issue's edits (one Ctrl+Z step unless `group` is False: the caller groups)."""
    cursor = QTextCursor(document)
    if group:
        cursor.beginEditBlock()
    for edit in sorted(issue.edits, key=lambda e: e.start, reverse=True):
        start = block_position + edit.start
        end = block_position + edit.end
        c = QTextCursor(document)
        if edit.text is not None:
            c.setPosition(start + 1)
            fmt = QTextCharFormat(c.charFormat())   # the replaced text's own look
            c.setPosition(start)
            c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(edit.text, fmt)
            end = start + len(edit.text)
        if edit.valign is not None or edit.italic is not None:
            c.setPosition(start)
            c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            if edit.valign is not None:
                fmt.setVerticalAlignment(_QT_VALIGN[edit.valign])
            if edit.italic is not None:
                fmt.setFontItalic(edit.italic)
            c.mergeCharFormat(fmt)
    if group:
        cursor.endEditBlock()


def all_issues(document: QTextDocument) -> list[tuple[int, chemtext.Issue]]:
    """(block position, issue) for every formatting problem in a document."""
    out = []
    block = document.begin()
    while block.isValid():
        out += [(block.position(), i) for i in issues_in(block)]
        block = block.next()
    return out


def fix_all(document: QTextDocument) -> int:
    """Fix every formatting problem (one Ctrl+Z step). Returns how many were fixed."""
    found = all_issues(document)
    if not found:
        return 0
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    for position, issue in reversed(found):   # from the end, so positions stay right
        apply_fix(document, position, issue, group=False)
    cursor.endEditBlock()
    return len(found)


def replace_word(document: QTextDocument, start: int, end: int, word: str) -> None:
    c = QTextCursor(document)
    c.setPosition(start + 1)
    fmt = QTextCharFormat(c.charFormat())
    c.setPosition(start)
    c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    c.insertText(word, fmt)


def subscript_formula(document: QTextDocument, end: int) -> bool:
    """The Chemical formula toggle: subscript the digits of the formula that ends at
    document position `end` (the word just finished), as its own Ctrl+Z step. True if
    anything changed."""
    block = document.findBlock(max(end - 1, 0))
    local = end - block.position()
    if not block.isValid() or local <= 0 or local > block.length() - 1:
        return False
    text = block.text()[:local]
    formats = block_formats(block, local)
    leave = checks().leave()
    digits = chemtext.formula_digits(text, end=local, leave=leave)
    # The word just finished can show that a word before it is a molecule ("N2 atmosphere",
    # "H2 (1 atm)"): subscript that one too, but only if it was not a formula before this
    # word was typed (otherwise the user has seen it, and perhaps undone it).
    word_start = re.search(r"\S*$", text).start()
    before = {m.start() for m in chemtext.formula_matches(text[:word_start], leave)}
    for m in chemtext.formula_matches(text, leave):
        if m.end() <= word_start and m.start() not in before:
            digits += [m.start() + i for i, ch in enumerate(m.group(0)) if ch.isdigit()]
    digits = [i for i in digits if formats[i][0] == NORMAL]
    if not digits:
        return False
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    fmt = QTextCharFormat()
    fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
    for i in digits:
        c = QTextCursor(document)
        c.setPosition(block.position() + i)
        c.setPosition(block.position() + i + 1, QTextCursor.MoveMode.KeepAnchor)
        c.mergeCharFormat(fmt)
    cursor.endEditBlock()
    return True


# ---------------------------------------------------------------------- the right-click menu

def add_check_actions(menu: QMenu, edit, position: int) -> None:
    """Put spelling suggestions and formatting fixes for the word at document `position`
    (where the user right-clicked) at the top of `edit`'s context menu. Works for
    QTextEdit and QPlainTextEdit."""
    highlighter = getattr(edit, "checks", None)
    if highlighter is None:
        return
    spelling, formatting = highlighter.spelling, highlighter.formatting
    document = edit.document()
    block = document.findBlock(position)
    at = position - block.position()
    text = block.text()
    editable = not edit.isReadOnly()
    hub = checks()
    items: list[QAction | None] = []

    if spelling:
        for start, end in hub.speller.misspelled(text):
            if start <= at <= end:
                word = text[start:end]
                suggestions = hub.speller.suggestions(word)
                for s in suggestions:
                    act = QAction(s, menu)
                    f = act.font()
                    f.setBold(True)
                    act.setFont(f)
                    act.setEnabled(editable)
                    act.triggered.connect(
                        lambda _=False, s=s, a=block.position() + start, b=block.position() + end:
                        replace_word(document, a, b, s))
                    items.append(act)
                if not suggestions:
                    none = QAction("(no suggestions)", menu)
                    none.setEnabled(False)
                    items.append(none)
                add = QAction(f"Add “{word}” to this project's dictionary", menu)
                add.setToolTip("Saved in the project's dictionary.txt, so co-authors share it")
                add.setEnabled(hub.can_add_to_project())
                add.triggered.connect(lambda _=False, w=word: hub.add_to_project(w, edit))
                mine = QAction(f"Add “{word}” to my dictionary (all projects)", menu)
                mine.setToolTip("Saved on this computer and used in every project")
                mine.triggered.connect(lambda _=False, w=word: hub.add_to_personal(w, edit))
                ignore = QAction(f"Ignore “{word}” until the program is closed", menu)
                ignore.setToolTip("Don't underline this word again in this session")
                ignore.triggered.connect(lambda _=False, w=word: hub.ignore(w))
                items += [None, add, mine, ignore]
                break

    if formatting:
        found = all_issues(document)
        here = [i for p, i in found if p == block.position() and i.start <= at <= i.end]
        for issue in here[:1]:
            fix = QAction(f"Fix: {issue.fixed}", menu)
            f = fix.font()
            f.setBold(True)
            fix.setFont(f)
            fix.setToolTip(issue.message)
            fix.setEnabled(editable)
            fix.triggered.connect(
                lambda _=False, i=issue, p=block.position(): apply_fix(document, p, i))
            if items:
                items.append(None)
            items += [_note(issue.message, menu), fix]
            # The way out when the check is wrong (SPEC §2.7): keep it as written.
            written = text[issue.start:issue.end]
            if issue.kind in ("formula", "solvent"):
                keep = QAction(f"Leave “{written}” as written in this project", menu)
                keep.setToolTip("Saved in the project's dictionary.txt: it is no longer "
                                "underlined, subscripted as you type or formatted when "
                                "compiling")
                keep.setEnabled(hub.can_add_to_project())
                keep.triggered.connect(lambda _=False, w=written: hub.add_to_project(w, edit))
                items.append(keep)
            skip = QAction(f"Ignore “{written}” until the program is closed", menu)
            skip.setToolTip("Don't underline it again in this session (compiling still "
                            "formats it, unless it is left as written in this project)")
            skip.triggered.connect(lambda _=False, w=written: hub.ignore(w))
            items.append(skip)
        if len(found) > 1 or (found and not here):
            every = QAction(f"Fix all {len(found)} formatting problems in this text", menu)
            every.setEnabled(editable)
            every.triggered.connect(lambda: fix_all(document))
            if items and items[-1] is not None:
                items.append(None)
            items.append(every)

    if not items:
        return
    menu.setToolTipsVisible(True)
    first = menu.actions()[0] if menu.actions() else None
    for item in items:
        if item is None:
            menu.insertSeparator(first)
        else:
            menu.insertAction(first, item)
    menu.insertSeparator(first)


def _note(text: str, menu: QMenu) -> QAction:
    act = QAction(text, menu)
    act.setEnabled(False)
    f = act.font()
    f.setItalic(True)
    act.setFont(f)
    return act


def install_spelling(edit: QPlainTextEdit) -> CheckHighlighter:
    """Spelling (only) for a plain text box, e.g. a compound's private notes."""
    edit.checks = CheckHighlighter(edit.document(), spelling=True, formatting=False)
    edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def menu(pos):
        m = edit.createStandardContextMenu(pos)
        add_check_actions(m, edit, edit.cursorForPosition(pos).position())
        show_menu(m, edit.viewport().mapToGlobal(pos))

    edit.customContextMenuRequested.connect(menu)
    return edit.checks


def show_menu(menu: QMenu, global_pos) -> None:
    """Show a context menu and wait for the choice (tests replace this)."""
    menu.exec(global_pos)
    menu.deleteLater()
