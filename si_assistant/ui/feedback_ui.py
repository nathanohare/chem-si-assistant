"""Help ▸ Send feedback, the "Something went wrong" message after an unexpected error,
and Help ▸ About (SPEC §13m.4).

Feedback goes through GitHub's "New issue" page in the web browser, pre-filled from the
repository's issue forms (.github/ISSUE_TEMPLATE/). Nothing is sent by the program itself,
and project files are never attached.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, updates

KINDS = (("bug", "Bug report", "Something doesn't work as it should."),
         ("feature", "Feature request", "An idea for something new or better."),
         ("feedback", "Other feedback", "Anything else you'd like to tell us."))


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl.fromEncoded(url.encode("ascii")))   # (already %-encoded)


# ---------------------------------------------------------------------- Send feedback…

class FeedbackDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, opener: Callable[[str], None] = open_url):
        super().__init__(parent)
        self.setWindowTitle("Send feedback")
        self.opener = opener
        self.opened_url = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("What would you like to send?"))
        self.group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        for kind, label, tip in KINDS:
            radio = QRadioButton(f"{label} — {tip}")
            self.group.addButton(radio)
            self.radios[kind] = radio
            layout.addWidget(radio)
        self.radios["bug"].setChecked(True)
        layout.addSpacing(6)
        layout.addWidget(QLabel("Short title (optional):"))
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. HRMS value not updated after changing the adduct")
        layout.addWidget(self.title)
        note = QLabel(
            "This opens GitHub's “New issue” page in your web browser, with the program "
            f"version ({__version__}) and your operating system already filled in. You then "
            "describe it there and send it. A free GitHub account is needed.<br><br>"
            "Nothing from your projects is sent. If a file helps to show the problem, you can "
            "attach it on that page yourself.")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        self.open_btn = buttons.addButton("Open GitHub page",
                                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.send)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)

    def kind(self) -> str:
        return next(k for k, r in self.radios.items() if r.isChecked())

    def url(self) -> str:
        return updates.issue_url(self.kind(), self.title.text())

    def send(self) -> None:
        self.opened_url = self.url()
        self.opener(self.opened_url)
        self.accept()


# ---------------------------------------------------------------------- unexpected errors

class ErrorDialog(QDialog):
    """"Something went wrong": the error in one line, its details (hidden until asked
    for), and Copy details / Report this problem / Close."""

    def __init__(self, summary: str, details: str, parent: QWidget | None = None,
                 opener: Callable[[str], None] = open_url):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — something went wrong")
        self.summary, self.details, self.opener = summary, details, opener
        self.opened_url = ""
        layout = QVBoxLayout(self)
        head = QLabel(
            "<b>Something went wrong.</b><br>The program ran into an unexpected problem. "
            "What you had already typed is normally saved, so you can usually carry on. If "
            "the program behaves oddly, close it and start it again.<br><br>"
            "Reporting the problem helps to fix it: “Report this problem” opens a GitHub page "
            "with the technical details below filled in (they say where in the program it "
            "happened, not what is in your project).")
        head.setWordWrap(True)
        layout.addWidget(head)
        error = QLabel(summary)
        error.setWordWrap(True)
        error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        error.setFont(fixed)
        layout.addWidget(error)
        self.text = QPlainTextEdit(details)
        self.text.setReadOnly(True)
        self.text.setFont(fixed)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setVisible(False)
        self.text.setMinimumHeight(180)
        layout.addWidget(self.text, 1)
        buttons = QDialogButtonBox()
        self.show_btn = buttons.addButton("Show details", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_btn = buttons.addButton("Copy details", QDialogButtonBox.ButtonRole.ActionRole)
        self.report_btn = buttons.addButton("Report this problem",
                                            QDialogButtonBox.ButtonRole.ActionRole)
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.clicked.connect(self.accept)
        self.show_btn.clicked.connect(self._toggle)
        self.copy_btn.clicked.connect(self.copy)
        self.report_btn.clicked.connect(self.report)
        layout.addWidget(buttons)
        self.setMinimumWidth(620)

    def _toggle(self) -> None:
        shown = self.text.isHidden()       # (isVisible is False while the dialog is hidden)
        self.text.setVisible(shown)
        self.show_btn.setText("Hide details" if shown else "Show details")
        self.adjustSize()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.details)
        self.copy_btn.setText("Copied")

    def report_url(self) -> str:
        title = f"Error: {self.summary}"
        if len(title) > 120:
            title = title[:117] + "…"
        return updates.issue_url("bug", title, details=self.details)

    def report(self) -> None:
        self.opened_url = self.report_url()
        self.opener(self.opened_url)


class ErrorReporter(QObject):
    """Shows ErrorDialog for errors from any thread (a signal brings them to the main
    thread). One dialog at a time; errors while one is open are only printed."""
    raised = Signal(str, str)

    def __init__(self, window_getter: Callable[[], QWidget | None],
                 extra_paths: Callable[[], dict[str, str]] = dict):
        super().__init__()
        self.window_getter = window_getter
        self.extra_paths = extra_paths
        self.showing = False
        self.shown = 0
        self.raised.connect(self.show_dialog)
        self.previous = (sys.excepthook, threading.excepthook)

    def handle(self, exc_type, exc, tb) -> None:
        try:
            self.previous[0](exc_type, exc, tb)   # still printed, as before
        except Exception:  # noqa: BLE001
            pass
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        try:
            extra = self.extra_paths() or {}
        except Exception:  # noqa: BLE001
            extra = {}
        summary, details = updates.error_report(exc_type, exc, tb, extra)
        if QApplication.instance() is not None:
            self.raised.emit(summary, details)

    def handle_thread(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        self.handle(args.exc_type, args.exc_value, args.exc_traceback)

    def show_dialog(self, summary: str, details: str) -> None:
        if self.showing:
            return
        self.showing = True
        try:
            dialog = ErrorDialog(summary, details, self.window_getter())
            self.shown += 1
            dialog.exec()
        except Exception:  # noqa: BLE001 — never an error about the error message
            pass
        finally:
            self.showing = False


def install_error_handler(window_getter: Callable[[], QWidget | None],
                          extra_paths: Callable[[], dict[str, str]] = dict) -> ErrorReporter:
    """Show "Something went wrong" for unexpected errors (main thread and other threads)."""
    reporter = ErrorReporter(window_getter, extra_paths)
    sys.excepthook = reporter.handle
    threading.excepthook = reporter.handle_thread
    return reporter


# ---------------------------------------------------------------------- About

def about_text() -> str:
    return (f"<b>{APP_NAME}</b> {__version__}<br>Builds the Supporting Information for "
            "chemistry papers.<br><br>Free software under the GNU General Public License "
            "v3 or later: you may use, share and change it; it comes with no warranty."
            f"<br><br>Source code, new versions and feedback: "
            f"<a href='{updates.REPO_URL}'>{updates.REPO_URL.removeprefix('https://')}</a>"
            "<br><br>The spelling check uses the Open Source Chemistry Dictionary v3.0 by "
            "Adam M. Azman (<i>J. Chem. Educ.</i> 2012, 89, 412), under the Creative Commons "
            "Attribution 3.0 United States licence. The drawing editor is Ketcher (EPAM, "
            "Apache-2.0); the interface uses Qt (LGPL-3.0) through PySide6. See "
            "THIRD_PARTY_NOTICES.md for all components.")


def show_about(parent: QWidget | None) -> None:
    QMessageBox.about(parent, f"About {APP_NAME}", about_text())
