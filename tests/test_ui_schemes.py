"""UI tests: pasting, scheme pages, and their place in the main window."""

from PySide6.QtCore import QMimeData, QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage

from si_assistant.core import chem
from si_assistant.core.project import Project
from tests.test_structures import scheme_molblock


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


def scheme_picture(*smiles) -> tuple[QImage, str]:
    """A scheme as ChemDraw would copy it: a picture of the structures side by side plus the
    MOL block of the same layout. The picture is drawn by RDKit from that same layout."""
    block = scheme_molblock(*smiles)
    png = chem.structure_png(chem.parse_structure(block), 1200, 300)
    image = QImage()
    image.loadFromData(png, "PNG")
    return image, block


def test_paste_scheme_places_labels_under_structures(qapp, tmp_path):
    from si_assistant.ui.scheme_panel import SchemePanel
    p = new_project(tmp_path)
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b"])
    scheme = p.add_scheme(data.id)
    product = p.add_compound(data.id, name="ester", scheme_id=scheme.id)
    panel = SchemePanel(p, scheme, lambda: "Tester")

    image, block = scheme_picture("OC(=O)c1ccccc1", "CCOC(=O)c1ccc(F)cc1")
    mime = QMimeData()
    mime.setImageData(image)
    mime.setText(block)
    QGuiApplication.clipboard().setMimeData(mime)
    panel.paste_from_clipboard()
    qapp.processEvents()

    assert p.scheme_image_path(scheme).exists()
    # The only compound had no structure: it gets the right-most one (the ester).
    assert p.structure_info(product).formula == "C9H9FO2"
    # Add the acid from the scheme's structures, via the "+ Add compound" menu choice.
    panel.add_compound(0)
    acid = p.scheme(scheme.id).children[1]
    assert p.structure_info(acid).formula == "C7H6O2"
    labels = p.scheme(scheme.id).labels
    assert set(labels) == {product.id, acid.id}
    assert labels[acid.id]["x"] < 0.5 < labels[product.id]["x"]  # acid left, ester right
    assert not labels[acid.id]["manual"]

    # Dragging a label keeps it in place when labels are auto-placed again.
    panel._label_moved(acid.id, 0.1, 0.2)
    panel.auto_place()
    assert p.scheme(scheme.id).labels[acid.id] == {"x": 0.1, "y": 0.2, "manual": True}
    panel.flush()
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

    # Drag A into the scheme, first position.
    win._on_item_dropped("compound", a.id, data.id, scheme.id, 0)
    qapp.processEvents()
    assert [c.name for c in proj.scheme(scheme.id).children] == ["A", "B", ""]
    win.close_project()
    win.close()
