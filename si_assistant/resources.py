"""Where the program's own files are, from the source code or in a packaged program
(SPEC §13m, Phase 8).

The program's built-in files (journal styles, word lists, icons, the Ketcher drawing
editor) sit inside the `si_assistant` package; LICENSE, THIRD_PARTY_NOTICES.md and the
user guide (docs/) sit next to it. Run from the source code, that is the project folder.
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


def user_data_dir(environ=None, platform: str | None = None, home: Path | None = None) -> Path:
    """This computer's folder for the program's own downloads (not for project data, which
    always stays in the Project Folder): used only when a packaged program's own folder
    can't be written to (e.g. Ketcher downloaded again for a macOS app or an AppImage)."""
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


def _read(path: str) -> str:
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def webengine_needs_no_sandbox(platform: str | None = None, read=_read) -> bool:
    """Whether Qt WebEngine (which shows Ketcher) must run without Chromium's sandbox:
    only on Linux, when the system forbids the "user namespaces" the sandbox needs (e.g.
    Ubuntu 24.04 does so for programs like an AppImage, whose own sandbox helper can't
    work either because an AppImage can't hold set-uid files). Ketcher is a local page
    shipped with the program, never a web site, so running it without the sandbox is
    safe; without this, the drawing window would stay blank or crash there."""
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return False
    if read("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") == "1":
        return True
    if read("/proc/sys/kernel/unprivileged_userns_clone") == "0":
        return True
    if read("/proc/sys/user/max_user_namespaces") == "0":
        return True
    return False


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


def prepare_environment(environ=None, platform: str | None = None, read=_read,
                        frozen: bool | None = None) -> list[str]:
    """Settings a packaged program needs before Qt starts (called first thing in
    __main__). Returns the names of the variables it set (for the tests)."""
    environ = os.environ if environ is None else environ
    frozen = is_frozen() if frozen is None else frozen
    changed = []
    if (frozen and "QTWEBENGINE_DISABLE_SANDBOX" not in environ
            and webengine_needs_no_sandbox(platform, read)):
        environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        changed.append("QTWEBENGINE_DISABLE_SANDBOX")
    return changed
