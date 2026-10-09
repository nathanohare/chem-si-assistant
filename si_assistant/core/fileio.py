"""Safe reading and writing of project files.

Every write goes to a temporary file in the same folder and is then renamed
over the real file. A rename is all-or-nothing, so a crash (or OneDrive
syncing mid-write) can never leave a half-written file behind.
"""

from __future__ import annotations

import codecs
import json
import os
import shutil
import stat
import tempfile
import time
import weakref
from pathlib import Path
from typing import Any

# Cloud-sync clients (OneDrive, Dropbox) sometimes hold a file open for a
# moment; on Windows that makes the rename fail, so we retry briefly.
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY_S = 0.1

# mkstemp() makes owner-only (0600) files; the umask is read once (at import, before any
# threads start) so saved files keep the usual permissions on macOS and Linux.
if os.name != "nt":
    _UMASK = os.umask(0)
    os.umask(_UMASK)
else:
    _UMASK = 0

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
    if journal is not None:
        journal.made_tree(Path(dest))


def _rename_retrying(source: Path, dest: Path) -> None:
    """Rename; a brief hold (OneDrive, antivirus) is retried, a real lock raises."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            Path(source).rename(dest)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY_S)


def move(source: Path, dest: Path) -> None:
    """Move a file or folder inside the Project Folder. Always a rename: it happens
    completely or not at all (never copy-then-delete, which can stop half-way when a file
    in it is open in another program). `dest` must not exist yet."""
    if os.path.lexists(dest):
        # (macOS and Linux would silently replace a file or empty folder; Windows refuses)
        raise FileExistsError(17, "File exists", str(dest))
    _rename_retrying(source, dest)
    journal = _journal_for(source, dest)
    if journal is not None:
        journal.moved(Path(source), Path(dest))


def rename(source: Path, dest: Path) -> None:
    """Rename a file or folder; fails (OSError) if it is in use, e.g. open elsewhere."""
    _rename_retrying(source, dest)
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
        except OSError:
            if not ignore_errors:
                raise
            # Something in it is in use. Deleting it for good here would bypass the undo
            # history, so what can be moved goes into it one item at a time (e.g. a Trash
            # entry's entry.json, so the entry is no longer listed), and only what is in
            # use stays (a leftover Trash folder is listed as an unfinished deletion);
            # unless the change being saved made it, with nothing else in it since.
            if journal.made_by_current_step(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                _remove_piecewise(journal, path)
        return
    shutil.rmtree(path, ignore_errors=ignore_errors)


def _remove_piecewise(journal, folder: Path) -> None:
    """Move what can be moved of `folder` into the undo history, item by item."""
    try:
        children = sorted(folder.iterdir())
    except OSError:
        return
    for child in children:
        try:
            journal.remove(child)
        except OSError:
            if child.is_dir() and not child.is_symlink():
                _remove_piecewise(journal, child)
    try:
        journal.remove(folder)   # (empty now, unless something in it is still in use)
    except OSError:
        pass


def remove_file(path: Path) -> None:
    """Delete a file. Inside a project with undo, it is moved into the undo history
    instead, so Edit ▸ Undo can bring it back."""
    path = Path(path)
    journal = _journal_for(path)
    if journal is not None and path.exists():
        journal.remove(path)
        return
    path.unlink(missing_ok=True)


def same_contents(path: Path, data: bytes) -> bool:
    """Whether `path` is a file that already holds exactly `data` (size checked first)."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size != len(data):
            return False
        with open(path, "rb") as f:
            return f.read() == data
    except OSError:
        return False


def _match_permissions(tmp_name: str, path: Path) -> None:
    """Give the temporary file the permissions (and group) of the file it replaces, or the
    usual ones for a new file, so a folder shared with colleagues on macOS or Linux stays
    readable for them. Best effort: never makes a save fail (some drives refuse chmod)."""
    if os.name == "nt":
        return
    try:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            os.chmod(tmp_name, 0o666 & ~_UMASK)
            return
        os.chmod(tmp_name, stat.S_IMODE(st.st_mode) & 0o777)
        if st.st_gid != os.stat(tmp_name).st_gid:
            os.chown(tmp_name, -1, st.st_gid)   # keep a shared group's ownership
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    # Writing the same bytes again changes nothing: skipped, so the file's date stays put
    # (OneDrive has nothing new to sync) and caches keyed on its date stay valid.
    if same_contents(path, data):
        return
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
        _match_permissions(tmp_name, path)
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


def _cp1252_bytes(error: UnicodeDecodeError) -> tuple[str, int]:
    """Bytes that are not UTF-8 are read as Windows-1252 (0xB0 → °, 0xB5 → µ). The five
    bytes Windows-1252 has no character for (0x81, 0x8D, 0x8F, 0x90, 0x9D) are kept as
    the characters of the same number, so writing the file back loses nothing."""
    bad = error.object[error.start:error.end]
    return bad.decode("cp1252", errors="si-latin1"), error.end


def _latin1_bytes(error: UnicodeDecodeError) -> tuple[str, int]:
    return error.object[error.start:error.end].decode("latin-1"), error.end


codecs.register_error("si-latin1", _latin1_bytes)


codecs.register_error("si-cp1252", _cp1252_bytes)


def decode_text(data: bytes) -> str:
    """Project text is UTF-8, but a file saved by another editor (Notepad's "ANSI" =
    Windows-1252, or "Unicode" = UTF-16) is still read rather than failing. Only the
    bytes that are not UTF-8 are read as Windows-1252, so a UTF-8 file with a ° typed in
    later by an older editor keeps its δ and ₃. The file is written back as UTF-8 the next
    time it is edited (never on reading)."""
    try:
        return data.decode("utf-8-sig")   # (strict: a real UTF-8 file is read exactly)
    except UnicodeDecodeError:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return data.decode("utf-16", errors="replace")
        return data.decode("utf-8-sig", errors="si-cp1252")


def read_text(path: Path, default: str = "") -> str:
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return default
    return decode_text(data).replace("\r\n", "\n")


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