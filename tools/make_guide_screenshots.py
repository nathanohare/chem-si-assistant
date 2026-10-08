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


def add_editor_scheme(project, data) -> str:
    """A made-up two-step scheme drawn with the scheme editor (SPEC §13n.4)."""
    from si_assistant.core import scheme_editor as se
    project.set_id_table(data.id, list(data.id_table) + ["5", "6"])
    k = project.add_scheme(data.id, name="Synthesis of amine 6")
    sm = se.starting(k)[0]
    se.set_box_structure(project, k, sm["id"], "CC(=O)c1ccc(Cl)cc1")
    se.set_box_label(project, k, sm["id"], "S1")
    step = se.steps(k)[0]
    se.set_box_structure(project, k, step["products"][0]["id"], "CC(O)c1ccc(Cl)cc1")
    c5 = se.link_new_compound(project, k, step["products"][0]["id"], name="alcohol 5")
    c5.values["yield"] = {"percent": "91"}
    project.save_compound(c5)
    se.set_conditions(project, k, step["id"], "top", "NaBH_{4} (1.5 equiv)")
    se.set_conditions(project, k, step["id"], "bottom", "MeOH, 0 °C, 1 h")
    step2 = se.add_step(project, k)
    se.set_box_structure(project, k, step2["products"][0]["id"], "CC(N)c1ccc(Cl)cc1")
    c6 = se.link_new_compound(project, k, step2["products"][0]["id"], name="amine 6")
    c6.values["yield"] = {"percent": "64"}
    project.save_compound(c6)
    sid = se.add_inline_structure(project, k, "O=C1c2ccccc2C(=O)N1", None)
    se.set_conditions(project, k, step2["id"], "top", f"1. @struct{{{sid}}}, DIAD, PPh_{{3}}")
    se.set_conditions(project, k, step2["id"], "bottom", "2. N_{2}H_{4}, EtOH")
    return k.id


def save_compiled_scheme(project, scheme_id: str) -> None:
    """The editor scheme as it prints (docs/images/scheme-editor-printed.png)."""
    from PySide6.QtCore import QRect
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info, render_page
    from si_assistant.compile.run import compile_pdf
    options = CompileOptions.from_settings(project.settings)
    options.only = ("scheme", scheme_id)
    options.preview_spectra = False
    result = compile_pdf(project, options, image_info, convert_to_png, name="guide")
    image = render_page(result.pdf, 1, 150)
    # (the top of the page: the scheme)
    image = image.copy(QRect(0, 0, image.width(), int(image.height() * 0.32)))
    path = OUT / "scheme-editor-printed.png"
    image.save(str(path), "PNG", 9)
    print(f"{path.name}: {image.width()}×{image.height()}, {path.stat().st_size / 1000:.0f} KB")


def save_manuscript_check(app, tmp: Path) -> None:
    """The Manuscript check page (SPEC §13o), with the made-up manuscript of the tests."""
    from PySide6.QtCore import Qt
    from si_assistant.core import manuscript
    from si_assistant.ui import main_window as mw
    sys.path.insert(0, str(ROOT / "tests"))
    import manuscript_samples as samples
    base = tmp / "ms"
    base.mkdir()
    project, _compounds = samples.build_si(base)
    for name, make in (("Made-up manuscript.pdf", samples.make_pdf),
                       ("Scheme 2.cdxml", samples.make_cdxml)):
        make(base / name)
        manuscript.add_file(project, base / name)
    folder = project.folder
    project.close()
    win = mw.MainWindow()
    win.prefs.user_name = "A. Researcher"
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.resize(WIDTH, HEIGHT)
    win.show()
    win.open_project(folder)
    settle(app)
    win.select(mw.MANUSCRIPT_ITEM, mw.MANUSCRIPT_ITEM)
    settle(app, 3)
    save(win, "manuscript-check.png")
    win.close_project()
    win.close()


def main() -> None:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
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
        editor_scheme = add_editor_scheme(project, parts["data"])
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
        win.act_live.trigger()                  # (the live preview folded away: room for
        select("scheme", editor_scheme, 4)      # the whole scheme)
        win.panel.set_collapsed(False)
        save(win, "scheme-editor.png")
        win.act_live.trigger()
        save_compiled_scheme(win.project, editor_scheme)
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
        save_manuscript_check(app, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
