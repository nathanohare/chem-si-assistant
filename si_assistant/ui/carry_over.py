"""Carrying this computer's settings over from the program's old name (SPEC §13l.5).

The program was called "SI Assistant" and is now "Chem SI Assistant". Qt keeps the
per-computer settings (your name, recent projects, window size and place, preferences such
as the live preview or the Chemical formula toggle) and "My dictionary" under the
program's name, so the first time the program starts under its new name they are copied
from the old place. The old copies are left where they are, untouched.

Project data is never involved: it always lives in the Project Folder.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths

from .. import APP_NAME

OLD_APP_NAME = "SI Assistant"
# Written into the new settings once the old ones have been looked at, so this happens
# only once (settings changed or removed later are never brought back).
DONE_KEY = "carried_over_from_old_name"


def copy_settings(old: QSettings, new: QSettings) -> int:
    """Copy every setting of `old` that `new` doesn't have yet, the first time only.
    Returns how many settings were copied."""
    if str(new.value(DONE_KEY, "")).lower() == "true":
        return 0
    copied = 0
    for key in old.allKeys():
        if key == DONE_KEY or new.contains(key):
            continue
        new.setValue(key, old.value(key))
        copied += 1
    new.setValue(DONE_KEY, True)
    new.sync()
    return copied


def copy_file_once(old: Path | None, new: Path | None) -> bool:
    """Copy `old` to `new` if `new` doesn't exist yet. Returns True if copied."""
    if old is None or new is None or new.exists() or not old.is_file():
        return False
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_bytes(old.read_bytes())
    return True


def app_data_folder(name: str) -> Path | None:
    """Where Qt keeps a program's own files (e.g. "My dictionary") when the program is
    called `name` (the same name is used as the organisation, as in __main__)."""
    app = QCoreApplication
    before = (app.applicationName(), app.organizationName())
    try:
        app.setApplicationName(name)
        app.setOrganizationName(name)
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    finally:
        app.setApplicationName(before[0])
        app.setOrganizationName(before[1])
    return Path(folder) if folder else None


def carry_over_from_old_name() -> None:
    """Called once at start-up, after the application has its name. Never stops the
    program: anything that goes wrong just means the old settings are not copied."""
    if APP_NAME == OLD_APP_NAME:
        return
    try:
        copy_settings(QSettings(OLD_APP_NAME, OLD_APP_NAME), QSettings(APP_NAME, APP_NAME))
    except Exception:  # noqa: BLE001 — settings are a convenience, never a reason to fail
        pass
    try:
        from .text_checks import PERSONAL_FILE
        old, new = app_data_folder(OLD_APP_NAME), app_data_folder(APP_NAME)
        if old is not None and new is not None and old != new:
            copy_file_once(old / PERSONAL_FILE, new / PERSONAL_FILE)
    except Exception:  # noqa: BLE001
        pass
