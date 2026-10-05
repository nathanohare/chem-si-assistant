"""The program's own icon: window title bar, taskbar and Alt+Tab.

The pictures are in si_assistant/icons/ (app_icon.svg is the editable master; the PNGs
are made from it at the sizes the operating system asks for; app_icon.ico is for Windows
shortcuts and a packaged .exe).
"""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

from .. import resources

ICONS_DIR = resources.resource("icons")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
WINDOWS_APP_ID = "ChemSIAssistant.App"


def icon_files() -> list[Path]:
    """The PNG files of the icon, smallest first (only those that exist)."""
    files = (ICONS_DIR / f"app_icon_{size}.png" for size in ICON_SIZES)
    return [f for f in files if f.exists()]


def app_icon() -> QIcon:
    """The program's icon, with every size so it stays sharp wherever it is shown.
    Call only after the QApplication exists."""
    icon = QIcon()
    for f in icon_files():
        icon.addFile(str(f))
    return icon


def use_own_taskbar_icon() -> None:
    """Windows shows a program started with pythonw.exe under Python's own icon on the
    taskbar. Giving the program its own ID makes Windows show this program's icon instead.
    Call before the QApplication is created. Does nothing on macOS and Linux."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:  # never stop the program over an icon
        pass
