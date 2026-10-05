"""Make the pictures of the user guide (docs/images/*.png) from the MADE-UP sample project
(tests/sample_project.py) — never from real data.

    python tools/make_guide_screenshots.py

Uses Qt's own platform (for the computer's fonts) without showing any window on screen,
and a throw-away settings file (this computer's real settings are not touched). Each
picture is kept small (at most about 150 KB).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images"
MAX_BYTES = 150_000
WIDTH = 1180
HEIGHT = 760


def settle(app, seconds: float = 1.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name: str, width: int = 960) -> Path:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    image = widget.grab().toImage()
    if image.width() > width:
        image = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    path = OUT / name
    image.save(str(path), "PNG", 9)
    if path.stat().st_size > MAX_BYTES:   # fewer colours: still crisp for a screenshot
        image.convertToFormat(QImage.Format.Format_Indexed8,
                              Qt.ImageConversionFlag.ThresholdDither).save(str(path), "PNG", 9)
    print(f"{path.name}: {image.width()}×{image.height()}, {path.stat().st_size / 1000:.0f} KB")
    return path


def main() -> None:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
    import PySide6.QtWebEngineWidgets  # noqa: F401  (must come before the application)
    app = QApplication(sys.argv)
    app.setApplicationName("Chem SI Assistant")
    tmp = Path(tempfile.mkdtemp(prefix="csa_shots_"))
    try:
        from si_assistant.ui import main_window as mw
        from si_assistant.ui import text_checks
        ini = tmp / "settings.ini"
        mw.QSettings = lambda *a: QSettings(str(ini), QSettings.Format.IniFormat)
        if hasattr(text_checks, "QSettings"):
            text_checks.QSettings = mw.QSettings
        from tests.sample_project import build_sample
        project, parts = build_sample(tmp)
        # Two deliberate slips, so the Warnings panel has something to show.
        c4 = parts["c4"]
        c4.values["nmr"][1]["text"] = "13C NMR (100 MHz, CDCl3) δ 159.0, 138.1, 126.7, 70.0, 55.3, 25.1"
        project.save_compound(c4)
        folder = project.folder
        project.close()

        OUT.mkdir(parents=True, exist_ok=True)
        win = mw.MainWindow()
        win.prefs.user_name = "A. Researcher"
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        win.resize(WIDTH, HEIGHT)
        win.show()
        settle(app)
        save(win, "start-page.png")

        win.open_project(folder)
        settle(app)

        def select(kind: str, item_id: str, wait: float = 2.5) -> None:
            item = win._find_item(kind, item_id)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            win.tree.setCurrentItem(item)
            settle(app, wait)

        select("project", "project")
        save(win, "project-overview.png")
        select("compound", parts["c2"].id, 6)
        save(win, "compound-page.png")
        select("scheme", parts["scheme"].id, 6)
        save(win, "scheme-page.png")
        select("section", parts["spectra"].id, 4)
        save(win, "spectra-section.png")
        win.act_warnings.setChecked(True)
        win.warnings_dock.setVisible(True)
        settle(app, 1)
        select("compound", c4.id, 6)
        win.panel.update()
        settle(app, 2)
        save(win, "warnings.png")
        win.warnings_dock.setVisible(False)

        from si_assistant.ui.compile_ui import CompileDialog
        dlg = CompileDialog(win.project, win)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dlg.show()
        settle(app, 0.5)
        save(dlg, "compile-dialog.png", 640)
        dlg.close()
        win.close_project()
        win.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
