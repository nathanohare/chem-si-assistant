"""Drawing a structure in Ketcher (SPEC §7.1: "draw in the built-in Ketcher editor").

Ketcher (EPAM, Apache-2.0) is a web app shown in a Qt WebEngine view. The structure goes
in and out as a MOL block, so drawings, SMILES and ChemDraw pastes all end up the same.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog,
                               QVBoxLayout, QWidget)

from .. import ketcher_setup

POLL_MS = 150
READY_TIMEOUT_MS = 30_000


def webengine_available() -> bool:
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


class _Installer(QThread):
    progress = Signal(int, int)
    failed = Signal(str)

    def run(self) -> None:
        try:
            ketcher_setup.install(lambda done, total: self.progress.emit(done, total))
        except Exception as e:  # noqa: BLE001 — shown to the user
            self.failed.emit(str(e))


def ensure_ketcher(parent: QWidget) -> bool:
    """True if Ketcher is ready; otherwise offer to download it (once, ~35 MB)."""
    if not webengine_available():
        QMessageBox.warning(parent, "Drawing not available",
                            "The drawing editor needs Qt WebEngine, which is not installed with "
                            "this copy of PySide6. You can still paste SMILES, MOL files or "
                            "structures copied from ChemDraw.")
        return False
    if ketcher_setup.installed():
        return True
    if QMessageBox.question(
            parent, "Download the drawing editor?",
            f"Drawing uses Ketcher, a free structure editor (EPAM, Apache licence). It needs a "
            f"one-time download of about 35 MB from GitHub (Ketcher {ketcher_setup.VERSION}).\n\n"
            "Download it now?") != QMessageBox.StandardButton.Yes:
        return False
    dlg = QProgressDialog("Downloading Ketcher…", "", 0, 100, parent)
    dlg.setCancelButton(None)
    dlg.setWindowTitle("Ketcher")
    dlg.setMinimumDuration(0)
    errors: list[str] = []
    worker = _Installer(parent)
    worker.progress.connect(lambda d, t: dlg.setValue(d * 100 // t if t else 0))
    worker.failed.connect(errors.append)
    worker.finished.connect(dlg.close)
    worker.start()
    dlg.exec()
    worker.wait()
    if errors or not ketcher_setup.installed():
        QMessageBox.warning(parent, "Download failed",
                            "Ketcher could not be downloaded" + (f":\n{errors[0]}" if errors else ".")
                            + "\n\nCheck your internet connection and try again.")
        return False
    return True


class KetcherDialog(QDialog):
    """Draw or edit a structure. `molfile()` holds the result after OK."""

    def __init__(self, molfile: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.setWindowTitle("Draw structure — Ketcher")
        self.resize(1100, 760)
        self._start = molfile
        self._result = ""
        self._ready = False
        self._waited = 0
        self.view = QWebEngineView(self)
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        url = QUrl.fromLocalFile(str(ketcher_setup.index_url_path()))
        url.setQuery("disableMacromoleculesEditor=true")
        self.view.load(url)
        self.status = QLabel("Starting Ketcher…")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use this structure")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._fetch)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.view, 1)
        bottom = QVBoxLayout()
        bottom.addWidget(self.status)
        bottom.addWidget(self.buttons)
        layout.addLayout(bottom)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll_ready)
        self._timer.start()

    # ---- start-up: wait for window.ketcher, then load the current structure

    def _poll_ready(self) -> None:
        self._waited += POLL_MS
        if self._waited > READY_TIMEOUT_MS:
            self._timer.stop()
            self.status.setText("⚠ Ketcher did not start. Close this window and try again.")
            return
        self.view.page().runJavaScript("!!(window.ketcher && window.ketcher.editor)",
                                       0, self._ready_answer)

    def _ready_answer(self, ready) -> None:
        if not ready or self._ready:
            return
        self._ready = True
        self._timer.stop()
        if self._start.strip():
            self.view.page().runJavaScript(
                f"window.ketcher.setMolecule({json.dumps(self._start)})", 0)
        self.status.setText("Draw the structure, then click “Use this structure”. "
                            "(Ketcher's own Open/Save buttons also read and write MOL files.)")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    # ---- OK: read the drawing back (Ketcher answers asynchronously)

    def _fetch(self) -> None:
        self.buttons.setEnabled(False)
        self.status.setText("Reading the structure…")
        self.view.page().runJavaScript(
            "window.__si = null;"
            "window.ketcher.getMolfile('v2000')"
            ".then(m => { window.__si = {ok: m}; })"
            ".catch(() => window.ketcher.getSmiles()"
            "  .then(s => { window.__si = {ok: s}; })"
            "  .catch(e => { window.__si = {err: String(e)}; }));", 0)
        self._waited = 0
        self._result_timer = QTimer(self)
        self._result_timer.setInterval(POLL_MS)
        self._result_timer.timeout.connect(self._poll_result)
        self._result_timer.start()

    def _poll_result(self) -> None:
        self._waited += POLL_MS
        if self._waited > READY_TIMEOUT_MS:
            self._result_timer.stop()
            self._failed("Ketcher did not answer.")
            return
        self.view.page().runJavaScript("window.__si ? JSON.stringify(window.__si) : ''", 0,
                                       self._result_answer)

    def _result_answer(self, answer) -> None:
        if not answer:
            return
        try:
            answer = json.loads(answer) if isinstance(answer, str) else answer
        except ValueError:
            answer = {"err": "unreadable answer"}
        self._result_timer.stop()
        if answer.get("err"):
            self._failed(answer["err"])
            return
        self._result = str(answer.get("ok") or "")
        if not _has_atoms(self._result):
            self._failed("Nothing is drawn.")
            return
        self.accept()

    def _failed(self, why: str) -> None:
        self.buttons.setEnabled(True)
        self.status.setText(f"⚠ Could not read the structure: {why}")

    def molfile(self) -> str:
        return self._result


def _has_atoms(text: str) -> bool:
    """A MOL block with at least one atom, or any SMILES."""
    lines = text.splitlines()
    if len(lines) >= 4 and ("V2000" in lines[3] or "V3000" in lines[3]):
        try:
            return int(lines[3][:3]) > 0 or "V3000" in lines[3]
        except ValueError:
            return True
    return bool(text.strip())
