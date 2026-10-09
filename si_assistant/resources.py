"""Where the program's own files are, from the source code or in a packaged program
(SPEC §13m, Phase 8).

The program's built-in files (journal styles, word lists, icons, the example project) sit
inside the `si_assistant` package; LICENSE, THIRD_PARTY_NOTICES.md and the user guide (docs/) sit
next to it. Run from the source code, that is the project folder.
In a program packaged with PyInstaller (the installers), the same files are unpacked into
the program's own data folder (`sys._MEIPASS`) with the same layout, so every path is
found through `bundle_root()`.

Nothing here imports Qt, so it can be used before the application exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE = "si_assistant"
USER_GUIDE = ("docs", "user-guide.md")


def is_frozen() -> bool:
    """True in a program packaged with PyInstaller (the installers), False from the source."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """The folder holding the `si_assistant` package and the top-level files (LICENSE,
    THIRD_PARTY_NOTICES.md, docs/): the source code's folder, or a packaged program's
    data folder."""
    if is_frozen():
        return Path(sys._MEIPASS)   # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    """A file or folder inside the package, e.g. resource("styles", "acs.typ")."""
    return bundle_root().joinpath(PACKAGE, *parts)


def top_level(*parts: str) -> Path:
    """A file or folder next to the package, e.g. top_level("LICENSE")."""
    return bundle_root().joinpath(*parts)


def user_guide() -> Path:
    """The user guide (Markdown; its pictures are in docs/images/)."""
    return top_level(*USER_GUIDE)


# The example project shipped with the program (Help ▸ Open example project…): made by
# tools/make_example_project.py. It is never opened or changed itself: the program copies
# it to a folder the user chooses and opens the copy.
EXAMPLE = ("example", "Example project")
EXAMPLE_PDF = ("example", "Example SI.pdf")   # the SI compiled from it (Help menu)
EXAMPLE_COPY_NAME = "Chem SI Assistant example"
# Left behind by a program that had the example open (the build/ folder with the undo
# history, snapshots, Trash, the lock, half-written files): never shipped, never copied.
NOT_COPIED = ("build", "trash", "snapshots", "*.lock", ".tmp-*")


def example_project() -> Path:
    """The example Project Folder shipped with the program."""
    return resource(*EXAMPLE)


def example_pdf() -> Path:
    """The SI compiled from the example project (Help ▸ View the example SI (PDF))."""
    return resource(*EXAMPLE_PDF)


def free_folder(parent: Path, name: str = EXAMPLE_COPY_NAME) -> Path:
    """`parent`/`name`, or "name (2)", "name (3)"… when that is taken: a folder that
    does not exist yet (nothing is ever written over)."""
    parent = Path(parent)
    folder, n = parent / name, 2
    while folder.exists():
        folder = parent / f"{name} ({n})"
        n += 1
    return folder


def copy_example_project(parent: Path, source: Path | None = None) -> Path:
    """Copy the example project into a new folder in `parent` (see free_folder) and
    return that folder. Every copied file and folder is made writable: the program's own
    folder may be read-only (Program Files, a macOS app, an AppImage), and its files would
    otherwise be copied read-only too. Raises OSError (e.g. no permission to write in
    `parent`); a half-made copy is removed again."""
    import shutil
    import stat
    source = example_project() if source is None else Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"The example project is missing:\n{source}")
    dest = free_folder(parent)
    try:
        shutil.copytree(source, dest, copy_function=shutil.copyfile,
                        ignore=shutil.ignore_patterns(*NOT_COPIED))
        for path in [dest, *dest.rglob("*")]:
            mode = path.stat().st_mode
            if not mode & stat.S_IWUSR:
                path.chmod(stat.S_IMODE(mode) | stat.S_IWUSR)
    except OSError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def user_data_dir(environ=None, platform: str | None = None, home: Path | None = None) -> Path:
    """This computer's folder for the program's own files (not for project data, which
    always stays in the Project Folder), for anything that can't go in the program's own
    folder (a macOS app or an AppImage can't be written to)."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    name = "Chem SI Assistant"
    if platform.startswith("win"):
        base = environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / name
    if platform == "darwin":
        return home / "Library" / "Application Support" / name
    base = environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / "chem-si-assistant"


# Windows: the program holds a mutex of this name while it runs, so the installer (an
# update started from Help ▸ Check for updates) can wait for it to close
# (packaging/windows/installer.iss uses the same name).
RUNNING_MUTEX = "ChemSIAssistant.Running"
_mutex_handle = None


def hold_running_mutex(platform: str | None = None) -> bool:
    """Windows only: hold RUNNING_MUTEX until the program ends. True if it is held."""
    global _mutex_handle
    platform = sys.platform if platform is None else platform
    if not platform.startswith("win") or _mutex_handle:
        return bool(_mutex_handle)
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _mutex_handle = kernel32.CreateMutexW(None, False, RUNNING_MUTEX)
    except Exception:  # noqa: BLE001 — never stop the program over this
        _mutex_handle = None
    return bool(_mutex_handle)
