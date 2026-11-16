"""Pictures in table cells (core/tables.py): a structure or scheme printed above a cell's
text, e.g. a list of ligands drawn with their names, or a reaction scheme in a merged cell
across the top of an optimisation table."""

import pytest

from si_assistant.compile.document import Builder, CompileOptions
from si_assistant.core.tables import Cell, Table
from tests.test_figures_tables import png


def builder(p):
    return Builder(p, CompileOptions.from_settings(p.settings), lambda path: (4, 3, 300))


@pytest.fixture
def section(project):
    p = project
    s = p.add_section("text", "Ligands")
    owner = p.text_owner(s.id)
    table_id = p.add_table(owner)
    p.write_section_text(s, f"!table{{{table_id}}} Ligands tested")
    return p, s, owner, table_id


def test_cell_picture_round_trips_and_is_optional():
    cell = Cell("(R)-BINAP", image="images/binap.svg", image_width=3.5)
    data = cell.to_json()
    assert data == {"text": "(R)-BINAP", "image": "images/binap.svg", "image_width": 3.5}
    assert Cell.from_json(data) == cell
    assert Cell("x").to_json() == {"text": "x"}                  # nothing new in old files
    assert Cell.from_json({"text": "x", "image_width": 3}).image_width is None
    assert Cell.from_json({"image": "images/a.png", "image_width": "big"}).image_width is None
    assert Cell.from_json({"image": 5}).image == ""             # (a hand-edited file)


def test_a_cell_with_only_a_picture_is_not_empty():
    t = Table.new(2, 2)
    assert t.is_empty()
    t.rows[1][1].image = "images/a.png"
    assert not t.is_empty() and t.pictures() == ["images/a.png"]


def test_merging_keeps_the_first_picture_and_splitting_leaves_it_top_left():
    t = Table.new(2, 3)
    t.rows[0][1] = Cell("b", image="images/b.png", image_width=2)
    t.rows[0][2] = Cell("c", image="images/c.png")
    assert t.merge(0, 0, 0, 2)
    merged = t.rows[0][0]
    assert merged.colspan == 3 and merged.image == "images/b.png" and merged.image_width == 2
    assert t.split(0, 0)
    assert t.rows[0][0].image == "images/b.png" and not t.rows[0][1].image


def test_table_picture_is_stored_next_to_the_figures(section, tmp_path):
    p, _s, owner, _t = section
    pasted = p.add_table_picture(owner, data=png(), ext=".png",
                                 vector=b"%PDF-1.4 fake", vector_ext=".pdf")
    assert pasted.startswith("images/") and (owner.folder / pasted).exists()
    assert (owner.folder / pasted).with_suffix(".pdf").exists()     # ChemDraw's original
    src = tmp_path / "dppf ligand.png"
    src.write_bytes(png())
    from_file = p.add_table_picture(owner, source=src)
    assert from_file != pasted and (owner.folder / from_file).exists()
    assert p.table_picture_path(owner, "../../outside.png") is None


def test_cell_pictures_print_above_their_text(section):
    p, s, owner, table_id = section
    image = p.add_table_picture(owner, data=png(), ext=".png")
    t = Table.new(2, 2, table_id)
    t.rows[0] = [Cell("Ligand"), Cell("ee")]
    t.rows[1] = [Cell("(R)-BINAP", image=image), Cell("92%")]
    p.write_table(owner, t)
    b = builder(p)
    code = b.text_section(s)
    assert '[#image("' in code and '#linebreak()#"(R)-BINAP"]' in code
    assert not b.warnings
    # A width typed for it (in cm), never wider than the text.
    t.rows[1][0].image_width = 2.54
    p.write_table(owner, t)
    assert "width: 1.0in" in builder(p).text_section(s)
    # A picture alone (e.g. a scheme in a merged cell across the top of the table).
    t.rows[1][0].text = ""
    p.write_table(owner, t)
    code = builder(p).text_section(s)
    assert "linebreak" not in code.split("#si-table(")[1].split("[#\"Table")[0]


def test_a_missing_cell_picture_warns_and_is_left_out(section):
    p, s, owner, table_id = section
    t = Table.new(1, 1, table_id)
    t.rows[0][0] = Cell("dppf", image="images/gone.png")
    p.write_table(owner, t)
    b = builder(p)
    code = b.text_section(s)
    assert "gone.png" not in code.split("#si-table(")[1].split("[#\"Table")[0]
    assert any("picture in a table cell is no longer in the project" in w for w in b.warnings)


def test_cell_pictures_are_re_rendered_with_the_others(section):
    p, _s, owner, table_id = section
    image = p.add_table_picture(owner, data=png(), ext=".png",
                                vector=b"%PDF-1.4 fake", vector_ext=".pdf")
    t = Table.new(1, 1, table_id)
    t.rows[0][0] = Cell("", image=image)
    p.write_table(owner, t)
    path = owner.folder / image
    found = dict(p.pasted_pictures())
    assert found[path] == path.with_suffix(".pdf")
    # Re-rendered as an SVG: the cell follows the new file.
    new = p.replace_pasted_picture(path, b"<svg xmlns='http://www.w3.org/2000/svg'/>", ".svg")
    assert p.read_table(owner, table_id).rows[0][0].image == new.relative_to(owner.folder).as_posix()


def test_table_editor_pastes_a_picture_into_the_selected_cell(qapp, section):
    from PySide6.QtGui import QGuiApplication, QImage
    from si_assistant.ui.table_editor import CellPictures, TableEditorDialog
    p, _s, owner, table_id = section
    pictures = CellPictures(owner.folder,
                            lambda **kw: p.add_table_picture(owner, **kw))
    dlg = TableEditorDialog(Table.new(2, 2, table_id), pictures=pictures)
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    QGuiApplication.clipboard().setImage(image)
    dlg.grid.setCurrentCell(1, 1)
    assert dlg.paste_picture()
    cell = dlg.result_table().rows[1][1]
    assert cell.image.startswith("images/") and (owner.folder / cell.image).exists()
    assert not dlg.thumbnail(cell.image).isNull()
    assert dlg.actions_["picture_remove"].isEnabled()
    dlg.picture_width.setValue(3)
    assert dlg.result_table().rows[1][1].image_width == 3
    dlg.remove_picture()
    assert not dlg.result_table().rows[1][1].image
    dlg.reject = lambda: None   # (no "discard changes?" question in the test)
    # A table that cannot hold pictures (e.g. X-ray crystal data) has no picture buttons.
    plain = TableEditorDialog(Table.new(1, 1))
    assert "picture_paste" not in plain.actions_ and not plain.paste_picture()
