"""X-ray structures, phase X1 (SPEC §13v): the viewer window — opening a CIF, turning the
structure, styles, standard views, labels, molecules shown, saving and copying the
picture. Made-up structures."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest

from tests.test_crystal import TABLE_CIF, cif_text, ethane_half


@pytest.fixture
def viewer(qapp, tmp_path):
    from si_assistant.ui.xray_viewer import XrayViewer
    v = XrayViewer()
    v.resize(1000, 700)
    v.show()
    yield v
    v.close()
    v.deleteLater()


def write_cif(tmp_path, text, name="made_up.cif"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def two_molecules():
    """Ethane on an inversion centre, and a water molecule beside it."""
    return ethane_half() + [("O1W", "O", 5.0, 5.0, 5.0, 0.04),
                            ("H1W", "H", 5.96, 5.0, 5.0, 0.06), ("H2W", "H", 4.76, 5.93, 5.0, 0.06)]


def test_opening_a_cif(viewer, tmp_path):
    table = TABLE_CIF.split("_cell_length_a")[0]
    text = table + cif_text(ethane_half(), aniso=[
        ("C1", 0.03, 0.02, 0.025, 0.002, 0.001, 0.0)]).split("\n", 1)[1]
    assert viewer.open_file(write_cif(tmp_path, text))
    canvas = viewer.editor.canvas
    assert [sh.text for sh in canvas.items() if sh.kind == "text"] == ["C1"]
    assert "8 atoms" in viewer.editor.status.text() and "1 molecule" in viewer.editor.status.text()
    # the crystal data table, its sub- and superscripts as characters
    rows = {viewer.table.item(r, 0).text(): viewer.table.item(r, 1).text()
            for r in range(viewer.table.rowCount())}
    assert rows["Empirical formula"] == "C₁₄H₁₂BrNO₂"
    assert rows["Space group"] == "P2₁/c"
    # a new drawing is about 12 cm wide, so its labels print at their size (a very
    # small molecule is enlarged at most 4 times)
    from si_assistant.core import xray_draw as X
    x0, _y0, x1, _y1 = X.bounds([sh for sh in canvas.items() if sh.kind != "text"])
    assert canvas.view.zoom == 4.0 and x1 - x0 < X.DEFAULT_WIDTH_PT


def test_a_larger_molecule_is_drawn_12_cm_wide(viewer, tmp_path):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from si_assistant.core import xray_draw as X
    mol = Chem.AddHs(Chem.MolFromSmiles("CCCCCCCCCCCC(=O)OCc1ccccc1"))
    AllChem.EmbedMolecule(mol, randomSeed=3)
    pos = mol.GetConformer().GetPositions() + [8, 8, 8]
    atoms = [(f"{a.GetSymbol()}{k + 1}", a.GetSymbol(), *pos[k], 0.03)
             for k, a in enumerate(mol.GetAtoms())]
    viewer.open_file(write_cif(tmp_path, cif_text(atoms, cell=(30, 30, 30, 90, 90, 90),
                                                  ops=("x, y, z",))))
    x0, _y0, x1, _y1 = X.bounds([sh for sh in viewer.editor.canvas.items() if sh.kind != "text"])
    assert x1 - x0 == pytest.approx(X.DEFAULT_WIDTH_PT, rel=0.02)


def test_a_file_without_a_structure(viewer, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    said = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: said.append(a[1])))
    assert not viewer.open_file(write_cif(tmp_path, "data_report\n_cell_length_a 10\n"))
    assert said == ["No structure in this file"]


def test_turning_styles_and_views(viewer, tmp_path):
    viewer.open_file(write_cif(tmp_path, cif_text(ethane_half())))
    canvas = viewer.editor.canvas
    before = np.array(canvas.view.rotation)
    centre = canvas.rect().center()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=centre + QPoint(0, 80))
    QTest.mouseMove(canvas, centre + QPoint(60, 80))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=centre + QPoint(60, 80))
    after = np.array(canvas.view.rotation)
    assert not np.allclose(before, after)
    assert abs(np.linalg.det(after) - 1) < 1e-6            # (still a rotation)
    viewer.editor.ball.setChecked(True)
    assert canvas.view.style == "ball"
    assert any(sh.kind == "ball" for sh in canvas.items())
    viewer.editor.hydrogens.setChecked(False)
    assert len([sh for sh in canvas.items() if sh.kind == "ball"]) == 2
    viewer.editor.standard_view(2)                                 # (along c: c points at you)
    assert np.allclose(np.array(canvas.view.rotation) @ [0, 0, 1], [0, 0, 1])
    zoom = canvas.view.zoom
    viewer.editor.size_box.setValue(150)
    assert canvas.view.zoom == pytest.approx(zoom * 1.5)


def test_a_label_dragged(viewer, tmp_path):
    viewer.open_file(write_cif(tmp_path, cif_text(ethane_half())))
    canvas = viewer.editor.canvas
    canvas.resize(800, 600)
    canvas.fit()
    label = next(sh for sh in canvas.items() if sh.kind == "text")

    def to_screen(x, y):
        return QPoint(int(canvas.width() / 2 + (x - canvas._centre[0]) * canvas._scale),
                      int(canvas.height() / 2 + (y - canvas._centre[1]) * canvas._scale))
    start = to_screen(label.centre[0] + 2, label.centre[1] - 2)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, start + QPoint(30, 0))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(30, 0))
    moved = next(sh for sh in canvas.items() if sh.kind == "text")
    assert moved.centre[0] - label.centre[0] == pytest.approx(30 / canvas._scale, abs=1.0)
    assert "C1" in canvas.view.label_offsets
    viewer.editor._reset_labels()
    back = next(sh for sh in canvas.items() if sh.kind == "text")
    assert back.centre == pytest.approx(label.centre)


def test_molecules_can_be_hidden(viewer, tmp_path):
    viewer.open_file(write_cif(tmp_path, cif_text(two_molecules())))
    assert viewer.editor.parts_box.isVisibleTo(viewer) and viewer.editor.parts.count() == 2
    texts = [viewer.editor.parts.item(k).text() for k in range(2)]
    assert any("H2O" in t or "OH2" in t or "H2O" in t.replace(" ", "") for t in texts)
    water = next(k for k in range(2) if "O" in texts[k] and "C" not in texts[k])
    viewer.editor.parts.item(water).setCheckState(Qt.CheckState.Unchecked)
    assert not any(sh.text == "O1W" for sh in viewer.editor.canvas.items() if sh.kind == "text")


def test_saving_and_copying_the_picture(viewer, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from xml.etree import ElementTree as ET
    viewer.open_file(write_cif(tmp_path, cif_text(ethane_half())))
    for name, kind in (("pic.png", "PNG"), ("pic.svg", "SVG"), ("pic.pdf", "PDF")):
        target = tmp_path / name
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, t=target, k=kind: (str(t), f"{k} …")))
        viewer.editor.save_picture()
        assert target.is_file()
    image = QImage(str(tmp_path / "pic.png"))
    assert image.width() > 1000 and round(image.dotsPerMeterX() * 0.0254) == 600
    ET.fromstring((tmp_path / "pic.svg").read_text(encoding="utf-8"))
    assert (tmp_path / "pic.pdf").read_bytes()[:5] == b"%PDF-"
    viewer.editor.copy_picture()
    data = QGuiApplication.clipboard().mimeData()
    assert data.hasImage() and data.hasFormat("image/svg+xml")


def test_the_viewer_from_the_file_menu(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(str(tmp_path / "prefs.ini"),
                                                              QSettings.Format.IniFormat))
    win = mw.MainWindow()
    try:
        win.act_xray.trigger()
        viewer = win._xray_viewer
        assert viewer.isVisible() and viewer.windowTitle() == "X-ray Structure Viewer"
        win.act_xray.trigger()                              # (the same window again)
        assert win._xray_viewer is viewer
        viewer.close()
    finally:
        win.close()
