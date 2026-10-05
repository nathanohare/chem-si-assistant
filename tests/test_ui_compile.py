from PySide6.QtCore import QSettings

from tests.sample_project import FACTS, build_sample


def test_compile_and_preview_from_main_window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import compile_ui
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p, parts = build_sample(tmp_path)
    p.close()
    opened = []
    monkeypatch.setattr(mw, "open_file", lambda path: opened.append(path))
    monkeypatch.setattr(mw.CompileDialog, "exec", lambda self: mw.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(compile_ui.QMessageBox, "information", lambda *a, **k: None)

    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    win.compile_si()
    assert opened and opened[0].name == "SI.pdf" and opened[0].exists()

    win.select("compound", parts["c2"].id)
    win.preview_current()
    assert win._preview is not None and win._preview.doc.pageCount() >= 1
    assert (p.folder / "build" / "preview.pdf").exists()
    win._preview.close()
    win.close_project()
    win.close()


def test_fill_with_generated_text(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.field_widgets import RichTextFieldEditor
    p, parts = build_sample(tmp_path)
    c2 = parts["c2"]
    p.add_field(c2, {"key": "si_text"})
    panel = CompoundPanel(p, c2, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes
                  if isinstance(b.editor, RichTextFieldEditor) and b.defn.key == "si_text")
    editor._fill_generated()
    panel.flush()
    text = p.read_compound_text(c2, "si_text")
    assert FACTS["c2_yield"] in text and FACTS["c2_hrms"][0] in text
    p.close()
