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


# The example project shipped with the program (Help ▸ Open example project): made by
# tools/make_example_project.py. It is never opened or changed itself: the program copies
# it into this computer's folder for the program's own files (user_data_dir) and opens
# the copy, without asking where (SPEC §13t.1).
EXAMPLE = ("example", "Example project")
EXAMPLE_PDF = ("example", "Example SI.pdf")   # the SI compiled from it (Help menu)
EXAMPLE_COPY_NAME = "Chem SI Assistant example"
EXAMPLE_COPY_DIR = "Example project"           # the copy, in user_data_dir()
# Beside the copy: the fingerprint of the shipped example it was copied from, so a newer
# example (after an update) is noticed.
EXAMPLE_STAMP = "Example project source.txt"
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
    dest = free_folder(parent)
    _copy_example_to(dest, source)
    return dest


def _copy_example_to(dest: Path, source: Path | None = None) -> None:
    """Copy the example project to the new folder `dest`, every file made writable.
    Raises OSError; a half-made copy is removed again."""
    import shutil
    import stat
    source = example_project() if source is None else Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"The example project is missing:\n{source}")
    try:
        shutil.copytree(source, dest, copy_function=shutil.copyfile,
                        ignore=shutil.ignore_patterns(*NOT_COPIED))
        for path in [dest, *dest.rglob("*")]:
            mode = path.stat().st_mode
            if not mode & stat.S_IWUSR:
                path.chmod(stat.S_IMODE(mode) | stat.S_IWUSR)
    except OSError:
        _remove_tree(dest)
        raise


def _remove_tree(folder: Path) -> None:
    """Delete `folder` and everything in it, read-only files too; quietly does nothing if
    it is not there. Raises OSError if something can't be deleted (e.g. open elsewhere)."""
    import shutil
    import stat

    def make_writable_and_retry(func, path, *_):
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        func(path)
    if Path(folder).exists():
        handler = ({"onexc": make_writable_and_retry} if sys.version_info >= (3, 12)
                   else {"onerror": make_writable_and_retry})
        shutil.rmtree(folder, **handler)


def example_copy_folder() -> Path:
    """Where the user's copy of the example project is kept (the program's own folder can't
    be written to everywhere, and an update replaces it)."""
    return user_data_dir() / EXAMPLE_COPY_DIR


def example_fingerprint(source: Path | None = None) -> str:
    """A fingerprint of the shipped example project (its files' names and contents): it
    changes when a program update brings a different example."""
    import hashlib
    source = example_project() if source is None else Path(source)
    digest = hashlib.sha256()
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        digest.update(path.relative_to(source).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def example_copy_state(source: Path | None = None) -> str:
    """"missing" (no copy yet), "current" (copied from this program's example) or
    "outdated" (copied from an older example, or not known from which)."""
    folder = example_copy_folder()
    if not folder.is_dir():
        return "missing"
    try:
        stamp = (folder.parent / EXAMPLE_STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return "outdated"
    return "current" if stamp == example_fingerprint(source) else "outdated"


def mark_example_copy_current(source: Path | None = None) -> None:
    """Remember that the user's copy goes with this program's example (they chose to keep
    their copy rather than take an updated example: not asked again). Raises OSError."""
    (example_copy_folder().parent / EXAMPLE_STAMP).write_text(
        example_fingerprint(source) + "\n", encoding="utf-8")


def prepare_example_copy(source: Path | None = None) -> Path:
    """Step 1 of (re)making the user's copy: a fresh copy of the shipped example in a
    folder beside it, to be put in place with install_example_copy. Raises OSError."""
    folder = example_copy_folder()
    new = folder.with_name(folder.name + " (new copy)")
    folder.parent.mkdir(parents=True, exist_ok=True)
    _remove_tree(new)                       # (left over from a copy that was interrupted)
    _copy_example_to(new, source)
    return new


def install_example_copy(new: Path, source: Path | None = None) -> Path:
    """Step 2: replace the user's copy (if any) by `new` from prepare_example_copy, and
    remember which example it came from. Raises OSError (e.g. a file of the old copy is
    open in another program): the old copy is then left as it was."""
    folder = example_copy_folder()
    old = folder.with_name(folder.name + " (old copy)")
    _remove_tree(old)
    if folder.exists():
        folder.rename(old)              # (all or nothing: the old copy stays whole)
    try:
        Path(new).rename(folder)
    except OSError:
        if old.exists() and not folder.exists():
            old.rename(folder)
        raise
    mark_example_copy_current(source)
    try:
        _remove_tree(old)
    except OSError:
        pass                            # (removed next time)
    return folder


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
