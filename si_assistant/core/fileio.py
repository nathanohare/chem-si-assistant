"""Safe reading and writing of project files.

Every write goes to a temporary file in the same folder and is then renamed
over the real file. A rename is all-or-nothing, so a crash (or OneDrive
syncing mid-write) can never leave a half-written file behind.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import weakref
from pathlib import Path
from typing import Any

# Cloud-sync clients (OneDrive, Dropbox) sometimes hold a file open for a
# moment; on Windows that makes the rename fail, so we retry briefly.
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY_S = 0.1

# ---------------------------------------------------------------------- undo (SPEC §13k.11)
#
# Every change to a project's files goes through this module, so the undo journal of an
# open project (core/undo.py) can keep what was there before. Projects opened for
# editing register their journal; files outside them are written as usual.

_journals: "weakref.WeakSet" = weakref.WeakSet()


def register(journal) -> None:
    """Changes inside `journal.root` are recorded by `journal` from now on. The newest
    journal of a folder wins (the project was opened again, e.g. taken over), so an
    older one never records someone else's changes."""
    root = os.path.normcase(os.path.abspath(str(journal.root)))
    for other in list(_journals):
        if other is not journal and os.path.normcase(os.path.abspath(str(other.root))) == root:
            _journals.discard(other)
    _journals.add(journal)


def unregister(journal) -> None:
    _journals.discard(journal)


def _journal_for(*paths: Path):
    for journal in list(_journals):
        if any(journal.claims(p) for p in paths):
            return journal
    return None


def make_dirs(path: Path, exist_ok: bool = True) -> None:
    """Make a folder (and the folders above it)."""
    path = Path(path)
    if path.is_dir():
        if not exist_ok:
            raise FileExistsError(17, "File exists", str(path))
        return
    journal = _journal_for(path)
    if journal is not None:
        journal.before_write(path)
    path.mkdir(parents=True, exist_ok=exist_ok)


def copy_file(source: Path, dest: Path) -> None:
    """Copy a file (with its date) to `dest`, which is made or replaced."""
    dest = Path(dest)
    journal = _journal_for(dest)
    if journal is not None:
        journal.before_write(dest)
    shutil.copy2(source, dest)


def copy_tree(source: Path, dest: Path) -> None:
    """Copy a whole folder to `dest`, which must not exist yet."""
    journal = _journal_for(dest)
    if journal is not None:
        journal.before_write(Path(dest))
    shutil.copytree(source, dest)


def move(source: Path, dest: Path) -> None:
    """Move a file or folder (both inside the Project Folder: a rename)."""
    shutil.move(str(source), str(dest))
    journal = _journal_for(source, dest)
    if journal is not None:
        journal.moved(Path(source), Path(dest))


def rename(source: Path, dest: Path) -> None:
    """Rename a file or folder; fails (OSError) if it is in use, e.g. open elsewhere."""
    Path(source).rename(dest)
    journal = _journal_for(source, dest)
    if journal is not None:
        journal.moved(Path(source), Path(dest))


def remove_tree(path: Path, ignore_errors: bool = False) -> None:
    """Delete a folder. Inside a project with undo, it is moved into the undo history
    (build/undo/) instead, so Edit ▸ Undo can bring it back."""
    path = Path(path)
    journal = _journal_for(path)
    if journal is not None and path.exists():
        try:
            journal.remove(path)
            return
        except OSError:
            if not ignore_errors:
                raise
    shutil.rmtree(path, ignore_errors=ignore_errors)


def remove_file(path: Path) -> None:
    """Delete a file. Inside a project with undo, it is moved into the undo history
    instead, so Edit ▸ Undo can bring it back."""
    path = Path(path)
    journal = _journal_for(path)
    if journal is not None and path.exists():
        journal.remove(path)
        return
    path.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    journal = _journal_for(path)
    if journal is not None:
        journal.before_write(path, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_S)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def atomic_write_text(path: Path, text: str) -> None:
    # Always UTF-8 with "\n" line endings so files are identical on every OS.
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    except FileNotFoundError:
        return default


def read_json(path: Path, default: Any = None) -> Any:
    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        return default
    return json.loads(text)


# ---------------------------------------------------------------------- Windows path limit

PATH_WARNING_LENGTH = 120  # project folder paths longer than this risk the 260-character limit


def windows_long_paths_enabled() -> bool:
    """Whether Windows' long-path support is on (always True on macOS/Linux). Read only."""
    if os.name != "nt":
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return False


def path_length_warning(folder: Path) -> str:
    """A plain-language warning if a project folder is deep enough that files inside it
    may hit Windows' 260-character path limit, else ""."""
    length = len(str(Path(folder).resolve()))
    if length <= PATH_WARNING_LENGTH or windows_long_paths_enabled():
        return ""
    return (f"This project's folder path is {length} characters long. Windows limits file "
            "paths to 260 characters, and files inside a project (spectra, raw data folders) "
            "add up to about 130 more, so some files may fail to save.\n\n"
            "If that happens, move the project to a shorter location (e.g. directly inside "
            "your OneDrive folder), or ask an administrator to turn on Windows long-path "
            "support.")