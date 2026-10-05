"""Interface size and restarting the program (SPEC §13l.2).

**Interface size** (View ▸ Interface size: 75–200 %) is a preference of this computer,
saved with the other per-computer settings (QSettings key `interface_size`, a whole
percentage). Qt can only scale the whole interface when the program starts, so the size
is applied in __main__ *before* the QApplication exists, by setting the environment
variable QT_SCALE_FACTOR (QSettings is read with the program's name given explicitly,
which works before the application exists), and taken away again as soon as the
application exists (Qt has read it by then), so programs started from this one don't
inherit it. A QT_SCALE_FACTOR the user set themselves is left alone (it wins). The size
is on top of the computer's own display scaling.

**Restarting** ("Restart now?" after choosing a size): the main window saves pending
edits, records where the user is (`restore info`: the open project folder, the selected
tree item and the items it sits in, the page's tab, the live preview and Warnings
panes), closes the project (releasing its lock), starts a new copy of the program and
quits. The restore info travels as a command-line argument of the new copy
(`--restore {json}`), so nothing is stored on disk and it is used once: the new copy
reopens the project and selects the same item (or the nearest one that still exists).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from .. import APP_NAME

SIZES = (75, 90, 100, 110, 125, 150, 175, 200)   # percent
DEFAULT_SIZE = 100
SETTING = "interface_size"
SCALE_ENV = "QT_SCALE_FACTOR"
RESTORE_ARG = "--restore"

# What __main__ did at start-up: the size in use (percent) and whether QT_SCALE_FACTOR
# was set by this program (then a restarted copy must not inherit it).
applied_size = DEFAULT_SIZE
scale_set_by_us = False
user_scale = ""   # a QT_SCALE_FACTOR set by the user ("" = none)


def settings() -> QSettings:
    """The per-computer settings (the same place as main_window.Preferences)."""
    return QSettings(APP_NAME, APP_NAME)


def read_size(store: QSettings | None = None) -> int:
    """The saved interface size in percent (100 when none, or not one of SIZES)."""
    store = settings() if store is None else store
    try:
        value = int(float(store.value(SETTING, DEFAULT_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_SIZE
    return value if value in SIZES else DEFAULT_SIZE


def apply_saved_size(environ=None, store: QSettings | None = None) -> int:
    """At start-up, before the QApplication exists: scale the interface to the saved size
    by setting QT_SCALE_FACTOR, unless the user set that variable (then theirs is used).
    Returns the size in use, in percent (for the user's variable, its value × 100)."""
    global applied_size, scale_set_by_us, user_scale
    environ = os.environ if environ is None else environ
    own = environ.get(SCALE_ENV, "").strip()
    if own:
        user_scale = own
        scale_set_by_us = False
        try:
            applied_size = round(float(own) * 100)
        except ValueError:
            applied_size = DEFAULT_SIZE
        return applied_size
    size = read_size(store)
    user_scale = ""
    scale_set_by_us = size != DEFAULT_SIZE
    if scale_set_by_us:
        environ[SCALE_ENV] = f"{size / 100:g}"
    applied_size = size
    return size


def fits_screen(size: int, available_now: tuple[float, float],
                window_min: tuple[float, float], size_now: int | None = None) -> bool:
    """Whether the window, at least `window_min` (logical pixels, the same at every size),
    fits on a screen whose free area is `available_now` (logical pixels at the size in use,
    `size_now`, default: the one this program started with) when shown at `size` percent."""
    size_now = applied_size if size_now is None else size_now
    k = (size_now / 100) / (size / 100)   # free area at `size`, in its logical pixels
    return (available_now[0] * k >= window_min[0]) and (available_now[1] * k >= window_min[1])


def forget_scale_variable(environ=None) -> None:
    """Once the QApplication exists (Qt has read QT_SCALE_FACTOR): take away the variable
    this program set, so programs started from it (a .mnova file opened in Mnova, a PDF
    viewer) are not scaled too. A QT_SCALE_FACTOR the user set is left alone."""
    environ = os.environ if environ is None else environ
    if scale_set_by_us:
        environ.pop(SCALE_ENV, None)


def child_environment(environ=None) -> dict[str, str]:
    """The environment for a restarted copy: as this one, without the QT_SCALE_FACTOR
    this program set (the new copy sets it again from the saved size)."""
    env = dict(os.environ if environ is None else environ)
    if scale_set_by_us:
        env.pop(SCALE_ENV, None)
    return env


def restart_command(info: dict) -> tuple[list[str], str | None]:
    """(arguments, working folder) that start a new copy of the program with `info`.
    Normally `python -m si_assistant` (run from the folder holding the package); a
    program packaged with PyInstaller (Phase 8) is started directly."""
    restore = [RESTORE_ARG, json.dumps(info, ensure_ascii=False)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *restore], None
    package = Path(__file__).resolve().parents[1]
    return [sys.executable, "-m", package.name, *restore], str(package.parent)


def launch_detached(args: list[str], env: dict[str, str], cwd: str | None) -> None:
    """Start `args` as a separate program that keeps running after this one quits.
    Raises OSError if it could not be started."""
    kwargs: dict = {"env": env, "cwd": cwd, "close_fds": True,
                    "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)   # noqa: S603 — our own program


# Replaced in tests, so a restart can be checked without starting anything.
launcher = launch_detached


def split_restore_arg(argv: list[str]) -> tuple[dict | None, list[str]]:
    """(restore info or None, the other arguments) from the command line (without the
    program name). Unreadable restore info is ignored: the program starts as usual."""
    if RESTORE_ARG not in argv:
        return None, list(argv)
    i = argv.index(RESTORE_ARG)
    rest = argv[:i] + argv[i + 2:]
    try:
        info = json.loads(argv[i + 1])
    except (IndexError, ValueError):
        return None, rest
    return (info if isinstance(info, dict) else None), rest
