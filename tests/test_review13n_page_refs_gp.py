"""Nathan's third review list (SPEC §13n), items 7–10: the compound page's one-line
Structure field and sharp header picture, closer fields, duplicate general-procedure
names, {scale} filling the Reaction scale field, and the References page in the tree."""

import pytest
from PySide6.QtCore import QSettings, QSize

from si_assistant.core import checks, procedures
from si_assistant.core.project import Project


def new_project(tmp_path):
    p = Project.create(tmp_path, "N")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b"])
    return p, data


SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="1in" viewBox="0 0 144 72">'
       '<line x1="10" y1="36" x2="134" y2="36" stroke="black" stroke-width="2"/></svg>')


# ---------------------------------------------------------------------- 9. {scale} (core)

@pytest.mark.parametrize("text, expected", [
    ("0.20 mmol", ("0.20", "mmol")),
    ("0.2mmol", ("0.2", "mmol")),
    ("20 µmol", ("20", "µmol")),
    ("20 μmol", ("20", "µmol")),        # (Greek mu)
    ("20 umol", ("20", "µmol")),
    ("1.5 mol", ("1.5", "mol")),
    ("0,50 mmol", ("0.50", "mmol")),
    (".5 MMOL", ("0.5", "mmol")),
    ("0.20 mmol of 1a", None),
    ("1.0 g", None),
    ("0.20", None),
    ("", None),
])
def test_parse_scale(text, expected):
    assert procedures.parse_scale(text) == expected


def test_scale_to_fill():
    fill = procedures.scale_to_fill
    assert fill("0.20 mmol", ("", "mmol"), None) == ("0.20", "mmol")          # empty field
    assert fill("0.25 mmol", ("0.20", "mmol"), ("0.20", "mmol")) == ("0.25", "mmol")  # ours
    assert fill("0.25 mmol", ("0.20", "mmol"), None) is None                  # typed by hand
    assert fill("0.25 mmol", ("0.30", "mmol"), ("0.20", "mmol")) is None      # changed by hand
    assert fill("0.20 mmol", ("0.20", "mmol"), None) is None                  # already so
    assert fill("about 0.2 mmol", ("", "mmol"), None) is None                 # not a scale
    assert procedures.same_scale(("0.20", "mmol"), ("200", "µmol"))
    assert not procedures.same_scale(("0.20", "mmol"), ("0.21", "mmol"))


def test_scale_field_wins_and_differences_warn(project):
    data = project.add_section("data", "Products")
    c = project.add_compound(data.id)
    gp = project.add_general_procedure(data.id)
    c.values["procedure"] = {"gp": gp["id"], "values": {"scale": "0.20 mmol",
                                                        "starting material": "x"}}
    c.values["scale"] = {"value": "", "unit": "mmol"}
    assert procedures.scale_conflict(project, c) == ""            # field empty: no warning
    c.values["scale"] = {"value": "200", "unit": "µmol"}
    assert procedures.scale_conflict(project, c) == ""            # the same amount
    c.values["scale"] = {"value": "0.25", "unit": "mmol"}
    message = procedures.scale_conflict(project, c)
    assert "“0.20 mmol”" in message and "0.25 mmol" in message
    assert any(w.key == "gp:scale-differs" and w.field == "procedure"
               for w in checks.all_warnings(project, c, project.id_report()))
    sentence = procedures.fill(gp["sentence"], c.values["procedure"]["values"],
                               procedures.auto_values(project, c, gp))
    assert "(0.25 mmol)" in "".join(r.text for r in sentence)   # the field is printed


# ---------------------------------------------------------------------- 8. duplicate GP names

def test_duplicate_gp_names_anywhere_in_the_project(qapp, project):
    from si_assistant.ui.data_panel import DataSectionPanel
    a = project.add_section("data", "Products")
    b = project.add_section("data", "Substrates")
    gp1 = project.add_general_procedure(a.id, "General Procedure A")
    project.add_general_procedure(b.id, "general procedure  a")
    project.add_general_procedure(b.id, "General Procedure B")
    assert procedures.name_key("General  Procedure A") == procedures.name_key("generalprocedurea")
    warnings = [w for w in checks.project_warnings(project) if w.check == "gp_name"]
    assert len(warnings) == 2
    assert {w.place for w in warnings} == {("section", a.id), ("section", b.id)}
    assert "“general procedure  a” in “Substrates”" in warnings[0].message

    panel = DataSectionPanel(project, a)
    gps = panel.procedures
    assert gps.list.item(0).text().startswith("⚠ ")
    assert not gps.duplicate.isHidden() and "Substrates" in gps.duplicate.text()
    # Renaming (while typing) clears it.
    gps.name.setText("General Procedure C")
    gps._edited()
    assert gps.duplicate.isHidden() and not gps.list.item(0).text().startswith("⚠")
    panel.flush()
    assert not [w for w in checks.project_warnings(project) if w.check == "gp_name"]
    assert project.general_procedure(gp1["id"])[1]["name"] == "General Procedure C"


# ---------------------------------------------------------------------- 7. compound page

def structure_box(panel):
    from si_assistant.ui.structure_widgets import StructureEditor
    return next(b for b in panel.boxes if isinstance(b.editor, StructureEditor))


def test_structure_field_is_one_line_once_filled(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    box = structure_box(panel)
    ed = box.editor
    assert not ed.picture_box.isHidden() and not ed.smiles.isHidden()   # empty: everything
    ed.smiles.setText("CC(=O)N(C)Cc1ccccc1")
    ed._apply_smiles()
    # One line: formula and masses, and the Edit or Remove menu; no picture.
    assert ed.picture_box.isHidden() and ed.smiles.isHidden() and ed.controls.isHidden()
    assert not ed.edit_btn.isHidden() and "C<sub>10</sub>H<sub>13</sub>NO" in ed.info.text()
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()
    assert ed.height() < 60
    panel.hide()
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    ed.locked_menu(menu)
    menu_texts = [a.text() for a in menu.actions()]
    assert not any("Ketcher" in t or "drawing" in t.lower() for t in menu_texts)
    assert "Show all editing controls (type SMILES…)" in menu_texts
    ed.set_editing(True)                        # it can still be opened up
    assert not ed.picture_box.isHidden() and not ed.smiles.isHidden()
    ed.set_editing(False)
    # Clearing the structure opens the field again, even when it was collapsed (▸).
    box.set_collapsed(True)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    ed._clear()
    assert not box.collapsed and not ed.picture_box.isHidden() and not ed._paste_locked
    assert "Draw…" not in ed.buttons
    panel.flush()
    p.close()


def test_header_picture_paste(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import structure_widgets as sw
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    messages = []
    panel.saved.connect(messages.append)
    monkeypatch.setattr(sw, "clipboard_structure_text", lambda: "CCO")
    monkeypatch.setattr(sw, "clipboard_picture_for", lambda project: None)
    assert panel.thumb.text() == "no structure"
    panel.paste_structure()                     # Ctrl+V on the header picture
    assert p.structure_info(c).formula == "C2H6O"
    assert panel.thumb.pixmap() is not None and not panel.thumb.pixmap().isNull()
    monkeypatch.setattr(sw, "clipboard_structure_text", lambda: "CCCO")
    panel.paste_structure()                     # now it says where to replace it instead
    assert p.structure_info(c).formula == "C2H6O"
    assert "Edit or Remove" in messages[-1]
    panel.flush()
    p.close()


def test_header_picture_is_sharp(qapp, tmp_path):
    from si_assistant.core import chem
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.structure_widgets import mol_pixmap, picture_pixmap
    # The program's drawing and an SVG picture at device pixel ratio 2: twice the pixels.
    pm = mol_pixmap(chem.parse_structure("CCO"), QSize(110, 80), None, 2.0)
    assert (pm.width(), pm.height()) == (220, 160) and pm.devicePixelRatio() == 2.0
    svg = tmp_path / "s.svg"
    svg.write_text(SVG, encoding="utf-8")
    pm = picture_pixmap(svg, QSize(100, 100), 2.0)
    assert (pm.width(), pm.height()) == (200, 100) and pm.devicePixelRatio() == 2.0
    # A PNG with an SVG of the same name beside it: drawn from the SVG.
    from PySide6.QtGui import QColor, QImage
    png = tmp_path / "pic.png"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    image.save(str(png))
    (tmp_path / "pic.svg").write_text(SVG, encoding="utf-8")
    pm = picture_pixmap(png, QSize(100, 100), 1.5)
    assert (pm.width(), pm.height()) == (150, 75)
    (tmp_path / "pic.svg").unlink()
    pm = picture_pixmap(png, QSize(100, 100), 1.5)   # the PNG alone, smoothly scaled
    assert (pm.width(), pm.height()) == (150, 75)
    # On the compound page: a picture without a structure shows in the header too.
    p, data = new_project(tmp_path / "p")
    c = p.add_compound(data.id)
    p.set_structure_image(c, svg)
    panel = CompoundPanel(p, c, lambda: "Tester")
    assert not panel.thumb.pixmap().isNull()
    assert structure_box(panel).editor.is_compact()
    assert "picture only" in structure_box(panel).editor.info.text()
    panel.flush()
    p.close()


def test_fields_sit_close_together(qapp, tmp_path):
    from si_assistant.ui import compound_panel
    from si_assistant.ui.field_widgets import FieldBox
    p, data = new_project(tmp_path)
    panel = compound_panel.CompoundPanel(p, p.add_compound(data.id), lambda: "Tester")
    assert panel.fields_layout.spacing() == compound_panel.FIELD_SPACING <= 4
    margins = panel.boxes[0].layout().contentsMargins()
    assert (margins.top(), margins.bottom()) == FieldBox.MARGINS[1::2] and margins.top() <= 4
    assert panel.boxes[0].layout().spacing() == FieldBox.SPACING
    panel.flush()
    p.close()


# ---------------------------------------------------------------------- 10. References page

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"),
                                             QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "R SI")
    p.add_section("text", "Notes")
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield win
    win.close_project()
    win.close()


def test_references_item_is_last_and_fixed(window):
    from PySide6.QtWidgets import QAbstractItemView
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.references_ui import ReferencesPage
    tree = window.tree
    last = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert last.text(0) == "References" and last.data(0, mw.KIND_ROLE) == mw.REFERENCES_ITEM
    assert mw.REFERENCES_ITEM in mw.FIXED_KINDS
    # No toolbar button; File ▸ References (Ctrl+Shift+R) opens the page.
    toolbar = window.findChild(type(window.addToolBar("x")), "main_toolbar")
    assert window.act_references not in toolbar.actions()
    window.act_references.trigger()
    assert isinstance(window.panel, ReferencesPage)
    assert not window.act_delete.isEnabled() and not window.act_rename.isEnabled()
    assert window.preview_pane.target is None
    # A section dropped on it goes last (before it); nothing else can go below it.
    notes = window._find_item("section", window.project.top_sections[0].id)
    DIP = QAbstractItemView.DropIndicatorPosition
    assert tree._section_drop(notes, last, DIP.BelowItem) == ("section", "",
                                                             tree.top_section_count())
    # A new section with References selected goes last too, before References.
    window.select(mw.REFERENCES_ITEM, mw.REFERENCES_ITEM)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mw.QInputDialog, "getText", lambda *a, **k: ("Extra", True))
        window.new_section("text")
    names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert names[-3:] == ["Extra", "Manuscript check", "References"]


def test_references_page_adds_with_undo(window):
    from si_assistant.core import references as R
    from si_assistant.ui.references_ui import ReferencesPage
    window.select("references", "references")
    page = window.panel
    assert isinstance(page, ReferencesPage)
    page.widget._add(R.clean({"authors": ["Fulmer, Gregory R."], "title": "Impurities",
                              "journal": "Organometallics", "year": "2010"}))
    assert len(window.project.references) == 1 and page.table.rowCount() == 1
    assert "Add reference" in window.act_undo.text()
    window.undo()
    assert window.project.references == []
    assert isinstance(window.panel, ReferencesPage) and window.panel.table.rowCount() == 0
