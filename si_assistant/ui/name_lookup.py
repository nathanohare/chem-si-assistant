"""The Name field with its "Look up name (PubChem)" button (SPEC §13n.5).

Nothing is sent anywhere until the button is clicked. Before the first lookup in a
session, a note says that the structure's InChIKey is sent to PubChem, an NIH database
("Don't show this again" is remembered on this computer). The lookup runs in a thread
while the window waits (with a wait cursor), so the window keeps drawing itself. The name
found fills the box (after asking, when it holds another name) and stays editable; a
small note under it says it came from PubChem.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QEventLoop, QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QMessageBox, QPushButton

from .. import APP_NAME
from ..core import pubchem
from ..core import fields as F
from .field_widgets import PartsEditor, get_part_value, set_part_value

FROM_KEY = "from_pubchem"        # compound.json: name.from_pubchem = {"cid", "name", "stereo"}
SKIP_NOTE_KEY = "pubchem/skip_privacy_note"

# The lookup: pubchem.lookup_name with this fetcher (tests put a fake one here), in a
# thread unless USE_THREAD is False (tests).
fetcher = pubchem.fetch_json
USE_THREAD = True
_note_shown = False              # the privacy note was shown in this session


def _settings() -> QSettings:
    return QSettings(APP_NAME, APP_NAME)


def privacy_text() -> str:
    return ("To look up the name, the structure's InChIKey — a short code calculated from "
            "the structure — is sent to PubChem, a public database of the US National "
            "Institutes of Health (NIH). Nothing else is sent.\n\nPubChem only knows "
            "compounds that are already public. Only look up an unpublished compound if "
            "you are happy for its InChIKey to be sent.")


def ask_privacy(parent) -> tuple[bool, bool]:
    """Show the note: (go ahead, "Don't show this again" ticked)."""
    box = QMessageBox(QMessageBox.Icon.Information, "Look up name in PubChem",
                      privacy_text(), parent=parent)
    go = box.addButton("Look up", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    skip = QCheckBox("Don't show this again (on this computer)")
    box.setCheckBox(skip)
    box.setDefaultButton(go)
    box.exec()
    return box.clickedButton() is go, skip.isChecked()


def confirm_privacy(parent) -> bool:
    """The one-time note before the first lookup of a session. False = cancelled."""
    global _note_shown
    settings = _settings()
    if _note_shown or str(settings.value(SKIP_NOTE_KEY, "false")).lower() == "true":
        return True
    go, skip = ask_privacy(parent)
    if not go:
        return False
    _note_shown = True
    if skip:
        settings.setValue(SKIP_NOTE_KEY, True)
    return True


class _Done(QObject):
    finished = Signal(object)


def run_lookup(mol) -> pubchem.NameResult:
    """pubchem.lookup_name in a thread; the window keeps drawing itself meanwhile."""
    if not USE_THREAD:
        return pubchem.lookup_name(mol, fetch=fetcher)
    box: dict = {}
    done = _Done()
    loop = QEventLoop()
    done.finished.connect(lambda r: (box.setdefault("result", r), loop.quit()),
                          Qt.ConnectionType.QueuedConnection)
    fetch = fetcher

    def work():
        try:
            result = pubchem.lookup_name(mol, fetch=fetch)
        except Exception as e:  # noqa: BLE001 — said in plain words, never a crash
            result = pubchem.NameResult("error", message=f"The lookup failed ({e}).")
        done.finished.emit(result)

    thread = threading.Thread(target=work, name="pubchem-lookup", daemon=True)
    thread.start()
    if "result" not in box:
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    return box["result"]


class NameEditor(PartsEditor):
    """The Name field: the name typed or pasted (e.g. from ChemDraw), plus "Look up name
    (PubChem)"."""

    status = Signal(str)   # a line for the status bar

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        grid = self.layout()
        self.lookup_btn = QPushButton("Look up name (PubChem)")
        self.lookup_btn.clicked.connect(self.look_up)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.note.setOpenExternalLinks(True)
        self.note.setStyleSheet("color: palette(placeholder-text);")
        grid.addWidget(self.lookup_btn, 0, grid.columnCount())
        grid.addWidget(self.note, grid.rowCount(), 0, 1, -1)
        stored = self.compound.values.get(self.defn.key) or {}
        source = stored.get(FROM_KEY)
        self._from = dict(source) if isinstance(source, dict) else None
        self.changed.connect(self._show_note)
        self.refresh_structure()
        self._show_note()

    def refresh_structure(self) -> None:
        """The button works once the compound has a structure."""
        has = self.project.read_structure(self.compound) is not None
        self.lookup_btn.setEnabled(has and not self.project.read_only)
        self.lookup_btn.setToolTip(
            "Look this exact structure up in PubChem and fill in its IUPAC name (it stays "
            "editable). Only the structure's InChIKey is sent, and only when you click."
            if has else "Add a structure first: the name is looked up by the structure.")

    def store(self) -> None:
        super().store()
        value = self.compound.values[self.defn.key]
        if self._from is not None and value.get("text", "") == self._from.get("name"):
            value[FROM_KEY] = dict(self._from)

    def _show_note(self) -> None:
        text = get_part_value(self.widgets["text"])
        if self._from is not None and text == self._from.get("name"):
            cid = self._from.get("cid")
            link = (f" <a href='{pubchem.PUBCHEM_PAGE}{cid}'>CID {cid}</a>" if cid else "")
            extra = ("" if self._from.get("stereo", True) else
                     " — found without stereochemistry: check the stereodescriptors")
            self.note.setText(f"↳ Name from PubChem{link}{extra}. You can edit it.")
            self.note.setVisible(True)
        else:
            self.note.setText("")
            self.note.setVisible(False)

    def look_up(self) -> None:
        mol = self.project.read_structure(self.compound)
        if mol is None or self.project.read_only:
            return
        if not confirm_privacy(self):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.lookup_btn.setEnabled(False)
        try:
            result = run_lookup(mol)
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_structure()
        self.apply(result)

    def apply(self, result: pubchem.NameResult) -> bool:
        """Use the lookup's answer (asks before replacing a name). True if filled in."""
        if result.status == "error":
            QMessageBox.warning(self, "Look up name in PubChem", result.message)
            return False
        if result.status == "not_found":
            QMessageBox.information(self, "Look up name in PubChem", result.message)
            return False
        current = get_part_value(self.widgets["text"])
        if result.status == "no_stereo":
            ask = (f"{result.message}\n\nPubChem's name:\n{result.name}\n\nUse it"
                   + (f" instead of “{current}”?" if current else "?"))
            if QMessageBox.question(self, "Look up name in PubChem", ask) != \
                    QMessageBox.StandardButton.Yes:
                return False
        elif current and current != result.name:
            if QMessageBox.question(
                    self, "Look up name in PubChem",
                    f"Replace the name\n“{current}”\nwith PubChem's name\n“{result.name}”?"
                    ) != QMessageBox.StandardButton.Yes:
                return False
        self._from = {"cid": result.cid, "name": result.name,
                      "stereo": result.status == "found"}
        set_part_value(self.widgets["text"], F.Part("text"), result.name)
        self.changed.emit()
        self.status.emit(result.message)
        return True

