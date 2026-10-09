"""Help ▸ Check for updates, and the automatic check when the program starts (SPEC §13m.3).

The check runs in a background thread and never holds up the program; when it cannot
reach GitHub at start-up it says nothing. The logic (versions, which file to download) is
in si_assistant/updates.py.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox,
                               QProgressDialog, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import APP_NAME, updates

CHECK_AT_START_KEY = "updates/check_at_start"
SKIPPED_KEY = "updates/skipped_version"
START_DELAY_MS = 3000


# ---------------------------------------------------------------------- per-computer settings

def check_at_start(prefs) -> bool:
    """"Check for updates when the program starts": on unless turned off."""
    value = prefs.value(CHECK_AT_START_KEY, True)
    return value if isinstance(value, bool) else str(value).lower() != "false"


def set_check_at_start(prefs, on: bool) -> None:
    prefs.set_value(CHECK_AT_START_KEY, bool(on))


def skipped_version(prefs) -> str:
    return str(prefs.value(SKIPPED_KEY, "") or "")


# ---------------------------------------------------------------------- newer projects

def newer_format_message(project) -> str:
    made = f" (version {project.saved_by})" if project.saved_by else ""
    return (f"This project was saved by a newer version of {APP_NAME}{made} than the one "
            f"you are using ({updates.__version__}).\n\nPlease update the program first "
            "(Help ▸ Check for updates…). Editing the project with this version could lose "
            "information that only the newer version knows about.\n\nYou can open it "
            "read-only to look at it.")


def ask_about_newer_format(parent, project) -> str:
    """"read_only" (the default) or "cancel"."""
    box = QMessageBox(QMessageBox.Icon.Warning, "Project made with a newer version",
                      newer_format_message(project), parent=parent)
    read_only = box.addButton("Open read-only", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(read_only)
    box.exec()
    return "read_only" if box.clickedButton() is read_only else "cancel"


# ---------------------------------------------------------------------- the background check

class UpdateChecker(QObject):
    """Runs updates.check_latest in a thread; `finished` brings the UpdateInfo back to the
    main thread."""
    finished = Signal(object)

    def __init__(self, fetch: updates.Fetcher | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.fetch = fetch or updates.fetch_json
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        def work() -> None:
            info = updates.check_latest(fetch=self.fetch)
            self.finished.emit(info)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def run_now(self) -> updates.UpdateInfo:
        """The same check without a thread (tests)."""
        return updates.check_latest(fetch=self.fetch)


# ---------------------------------------------------------------------- the dialog

class UpdateDialog(QDialog):
    """"A new version is available": version, release notes and the three choices."""
    INSTALL, LATER, SKIP = "install", "later", "skip"

    def __init__(self, info: updates.UpdateInfo, can_install: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.choice = self.LATER
        head = QLabel(f"<b>{APP_NAME} {info.latest}</b> is available. You have version "
                      f"{info.current}.")
        head.setWordWrap(True)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes.strip() or "_No release notes._")
        notes.setMinimumSize(460, 220)
        self.notes = notes
        how = {"windows": "The program saves your work, closes and starts the installer.",
               "macos": "The new version is downloaded to your Downloads folder.",
               "linux": "The new version is downloaded to your Downloads folder."}
        hint = QLabel(how.get(updates.platform_key() or "", "") if can_install else
                      "The download page opens in your web browser.")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox()
        self.install_btn = buttons.addButton("Download and install" if can_install
                                             else "Open the download page",
                                             QDialogButtonBox.ButtonRole.AcceptRole)
        self.later_btn = buttons.addButton("Remind me later", QDialogButtonBox.ButtonRole.RejectRole)
        self.skip_btn = buttons.addButton("Skip this version",
                                          QDialogButtonBox.ButtonRole.DestructiveRole)
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(lambda: self._done(self.INSTALL))
        self.later_btn.clicked.connect(lambda: self._done(self.LATER))
        self.skip_btn.clicked.connect(lambda: self._done(self.SKIP))
        layout = QVBoxLayout(self)
        layout.addWidget(head)
        layout.addWidget(QLabel("What's new:"))
        layout.addWidget(notes, 1)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept() if choice == self.INSTALL else self.reject()


# ---------------------------------------------------------------------- downloading

class _Download(QObject):
    progress = Signal(int, int)
    finished = Signal(object)    # the Path, or the exception

    def __init__(self, asset: updates.Asset, dest: Path):
        super().__init__()
        self.asset, self.dest = asset, dest
        self.cancelled = False

    def start(self) -> None:
        def report(done: int, total: int) -> bool:
            self.progress.emit(done, total)
            return not self.cancelled

        def work() -> None:
            try:
                path = updates.download(self.asset.url, self.dest, self.asset.size, report)
            except Exception as e:  # noqa: BLE001
                self.finished.emit(e)
                return
            self.finished.emit(path)

        threading.Thread(target=work, name="update-download", daemon=True).start()


def download_folder(key: str | None) -> Path:
    """Windows: a temporary folder (the installer is started from there). macOS/Linux:
    the Downloads folder, where the user finds the new version."""
    if key == "windows":
        folder = Path(tempfile.gettempdir()) / "Chem SI Assistant update"
    else:
        found = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        folder = Path(found) if found else Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def remove_old_installers(folder: Path, keep: Path) -> None:
    """Windows only (never the macOS/Linux Downloads folder): delete earlier installers and
    .part files of interrupted downloads from the program's own temporary update folder.
    An installer that is still running is locked and is skipped; this never stops the
    download."""
    pattern = updates.ASSET_PATTERNS["windows"] + "*"   # the .exe and its .exe.part
    skip = {keep.name, keep.name + ".part"}
    try:
        old = list(folder.glob(pattern))
    except OSError:
        return
    for p in old:
        if p.name in skip:
            continue
        try:
            if p.is_file() and not p.is_symlink():
                p.unlink()
        except OSError:
            pass


def reveal(path: Path) -> None:
    """Show a file in Finder / its folder in the file manager."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])   # noqa: S603, S607
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class Updater:
    """Downloads the new version and hands over to it (Windows: installer; macOS/Linux: the
    file in Downloads). `launch`, `reveal_file`, `quit_app` and `ask` can be replaced in
    tests."""

    def __init__(self, window, info: updates.UpdateInfo, key: str | None = None):
        self.window = window
        self.info = info
        self.key = key if key is not None else updates.platform_key()
        self.asset = updates.pick_asset(info.assets, self.key)
        self.launch: Callable[[Path], None] = (lambda p: os.startfile(str(p))   # type: ignore[attr-defined]
                                               if sys.platform.startswith("win") else None)
        self.reveal_file: Callable[[Path], None] = reveal
        self.quit_app: Callable[[], None] = self._quit
        self.open_url: Callable[[str], None] = lambda u: QDesktopServices.openUrl(QUrl(u))
        self.download: _Download | None = None
        self.progress_dialog: QProgressDialog | None = None
        self.result: Path | Exception | None = None

    def start(self) -> None:
        if self.asset is None:
            # No installer for this computer in the release: show the release page.
            self.open_url(self.info.page_url)
            return
        folder = download_folder(self.key)
        dest = folder / self.asset.name
        if self.key == "windows":
            remove_old_installers(folder, keep=dest)
        self.download = _Download(self.asset, dest)
        dlg = QProgressDialog(f"Downloading {self.asset.name}…", "Cancel", 0, 100, self.window)
        dlg.setWindowTitle("Downloading the update")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(self._cancel)
        self.progress_dialog = dlg
        self.download.progress.connect(self._progress)
        self.download.finished.connect(self._finished)
        dlg.show()
        self.download.start()

    def _cancel(self) -> None:
        if self.download is not None:
            self.download.cancelled = True

    def _progress(self, done: int, total: int) -> None:
        if self.progress_dialog is None:
            return
        if total:
            self.progress_dialog.setMaximum(100)
            self.progress_dialog.setValue(min(100, int(done * 100 / total)))
        mb = done / 1_048_576
        of = f" of {total / 1_048_576:.1f}" if total else ""
        self.progress_dialog.setLabelText(f"Downloading {self.asset.name}…\n{mb:.1f}{of} MB")

    def _finished(self, result) -> None:
        self.result = result
        # Read the user's choice before closing: closing a QProgressDialog emits `canceled`,
        # which would otherwise mark every finished download as cancelled.
        user_cancelled = self.download is not None and self.download.cancelled
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog = None
        if isinstance(result, Exception):
            if user_cancelled:
                return
            box = QMessageBox(QMessageBox.Icon.Warning, "Update not downloaded",
                              f"{result}\n\nYou can download the new version from the "
                              "release page instead.", parent=self.window)
            page = box.addButton("Open the release page", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is page:
                self.open_url(self.info.page_url)
            return
        if user_cancelled:
            # Cancel clicked just as the last part arrived: do as the user asked, never
            # close the project and start the installer. (The file is left where it is;
            # on Windows the next download removes it.)
            return
        self.hand_over(Path(result))

    def hand_over(self, path: Path) -> None:
        if self.key == "windows":
            # Save everything, close the project (releasing its lock), start the installer
            # and quit, so the installer can replace the program's files.
            if hasattr(self.window, "close_project") and not self.window.close_project():
                return
            try:
                self.launch(path)
            except OSError as e:
                QMessageBox.warning(self.window, "Could not start the installer",
                                    f"The installer is here:\n{path}\n\n{e}")
                self.reveal_file(path)
                return
            self.quit_app()
            return
        if self.key == "linux":
            try:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass
            text = (f"The new version was saved as:\n{path}\n\nClose {APP_NAME}, then start "
                    "the new file (you can delete the old one).")
        else:
            text = (f"The new version was saved as:\n{path}\n\nQuit {APP_NAME}, open the "
                    "file and drag the app into Applications, replacing the old one.")
        box = QMessageBox(QMessageBox.Icon.Information, "Update downloaded", text,
                          parent=self.window)
        show = box.addButton("Show the file", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is show:
            self.reveal_file(path)

    def _quit(self) -> None:
        if self.window is not None:
            self.window.close()
        QApplication.quit()


# ---------------------------------------------------------------------- the whole flow

class UpdateFlow(QObject):
    """What the main window uses: `check(manual)` starts a check; the answer arrives in
    `show_result`. `fetch` can be replaced (tests); `ask` returns the UpdateDialog choice."""

    def __init__(self, window, prefs, fetch: updates.Fetcher | None = None):
        super().__init__(window)
        self.window = window
        self.prefs = prefs
        self.checker = UpdateChecker(fetch, self)
        self.checker.finished.connect(self._checked)
        self.manual = False
        self.last: updates.UpdateInfo | None = None
        self.updater: Updater | None = None
        self.ask: Callable[[UpdateDialog], str] = self._ask
        self.inform: Callable[[str, str, str], None] = self._inform

    def check(self, manual: bool = False) -> None:
        self.manual = manual or self.manual
        if manual and hasattr(self.window, "statusBar"):
            self.window.statusBar().showMessage("Checking for updates…", 8000)
        self.checker.start()

    def _checked(self, info: updates.UpdateInfo) -> None:
        self.checker.running = False
        manual, self.manual = self.manual, False
        self.show_result(info, manual)

    def show_result(self, info: updates.UpdateInfo, manual: bool) -> str:
        """React to a check; returns what happened (for tests): "dialog:<choice>",
        "silent", "up_to_date", "not_public", "unreachable" or "deferred"."""
        self.last = info
        if not manual and QApplication.activeModalWidget() is not None:
            # Another question is open (e.g. at start-up): ask a bit later.
            QTimer.singleShot(5000, lambda: self.show_result(info, manual))
            return "deferred"
        if info.status == "newer":
            if not manual and info.latest == skipped_version(self.prefs):
                return "silent"
            return "dialog:" + self.offer(info)
        if not manual:
            return "silent"
        if info.status == "up_to_date":
            self.inform("info", "No update available",
                        f"You have the latest version of {APP_NAME} ({info.current}).")
            return "up_to_date"
        if info.status == "not_public":
            self.inform("info", "Can't check for updates yet",
                        f"{APP_NAME} can't check for updates until its GitHub project is "
                        f"public (or has its first release).\n\nYou have version "
                        f"{info.current}.")
            return "not_public"
        self.inform("warning", "Couldn't check for updates",
                    "Couldn't reach GitHub to check for updates. Check your internet "
                    f"connection and try again later.\n\nNew versions are also listed at "
                    f"{updates.RELEASES_URL}")
        return "unreachable"

    def offer(self, info: updates.UpdateInfo) -> str:
        asset = updates.pick_asset(info.assets, updates.platform_key())
        dialog = UpdateDialog(info, asset is not None, self.window)
        choice = self.ask(dialog)
        if choice == UpdateDialog.SKIP:
            self.prefs.set_value(SKIPPED_KEY, info.latest)
        elif choice == UpdateDialog.INSTALL:
            self.updater = Updater(self.window, info)
            self.updater.start()
        return choice

    @staticmethod
    def _ask(dialog: UpdateDialog) -> str:
        dialog.exec()
        return dialog.choice

    def _inform(self, kind: str, title: str, text: str) -> None:
        if kind == "warning":
            QMessageBox.warning(self.window, title, text)
        else:
            QMessageBox.information(self.window, title, text)
