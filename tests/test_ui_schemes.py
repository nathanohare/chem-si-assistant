"""UI tests: pasting, scheme pages, and their place in the main window."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage

from si_assistant.core.project import Project


def new_project(tmp_path):
    p = Project.create(tmp_path, "S")
    p.acquire_lock("Tester")
    return p


def test_ctrl_v_goes_to_the_selected_structure_box(qapp, tmp_path):
    from PySide6.QtTest import QTest
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.structure_widgets import StructureEditor
    p = new_project(tmp_path)
    data = p.add_section("data", "Products")
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    panel.resize(1000, 800)
    panel.show()
    QTest.qWaitForWindowExposed(panel)
    panel.activateWindow()
    editor = next(b.editor for b in panel.boxes if isinstance(b.editor, StructureEditor))

    editor.image.setFocus()
    qapp.processEvents()
    assert editor._paste_active and "pastes here" in editor.paste_hint.text()

    QGuiApplication.clipboard().setText("CCOC(=O)c1ccoc1C")
    QTest.keyClick(editor.image, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert p.structure_info(c).formula == "C8H10O3"

    image = QImage(30, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    QGuiApplication.clipboard().setImage(image)
    editor = next(b.editor for b in panel.boxes if isinstance(b.editor, StructureEditor))
    editor.paste_from_clipboard()  # a picture alone: becomes the structure's own picture
    assert p.structure_picture_source(c) == "user"
    panel.flush()
    panel.close()
    p.close()


def test_scheme_pages_in_main_window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.scheme_panel import SchemePanel
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "UI")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b", "3c"])
    a = p.add_compound(data.id, name="A")
    b = p.add_compound(data.id, name="B")
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    win.open_project(p.folder)
    proj = win.project

    # Change B to a reaction scheme (skipping the confirmation box).
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *args, **kw: mw.QMessageBox.StandardButton.Yes)
    win.convert_to_scheme(b.id)
    assert isinstance(win.panel, SchemePanel)
    scheme = proj.section(data.id).schemes[0]
    scheme_item = win._find_item("scheme", scheme.id)
    assert scheme_item is not None and scheme_item.child(0).data(0, mw.ID_ROLE) == b.id

    # New compound while the scheme is selected goes into the scheme.
    win.new_compound()
    assert [c.name for c in proj.scheme(scheme.id).children] == ["B", ""]
    assert isinstance(win.panel, SchemePanel)
    assert win.panel.tabs.count() == 2

    # Selecting a sub-compound in the tree switches the tab, not the page.
    page = win.panel
    win.select("compound", b.id)
    assert win.panel is page and page.current_compound_id() == b.id

    # IDs still follow tree order: A, B, (new)
    report = proj.id_report()
    assert [report.display_id(c.id) for c in proj.section(data.id).compounds] == ["3a", "3b", "3c"]

    # Drag A into the scheme, first position. The scheme is drawn with the scheme editor
    # (SPEC §13n.4) and B is its product: compounds drawn in the scheme come first, in the
    # order of their boxes; the others follow in the order they were put.
    win._on_item_dropped("compound", a.id, data.id, scheme.id, 0)
    qapp.processEvents()
    assert [c.name for c in proj.scheme(scheme.id).children] == ["B", "A", ""]
    win.close_project()
    win.close()
