"""X-ray structures, phase X2 (SPEC §13v): the X-ray structure section — its CIF, view,
captions and table; in the tree or under a compound (anchored); moving, deleting and
restoring; compiling. Made-up projects; the CIF is a real public-domain one."""

import shutil
from pathlib import Path

import pytest

from si_assistant.core import xray as xr
from si_assistant.core.project import Project, ProjectError

CIF = Path(__file__).parent / "data" / "cif" / "naphthalene_cod_2311090.cif"


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path, "P")
    p.acquire_lock("Tester")
    p.enable_undo()
    p.ensure_spectra_section()
    data = p.add_section("data", "Products", 0)
    a = p.add_compound(data.id, name="Naphthalene")
    b = p.add_compound(data.id, name="Anthracene")
    p.set_id_table(data.id, ["1a", "1b"])
    yield p, data, a, b
    p.close()


def xray_section(p, name="X-ray of 1a", cif=CIF):
    s = p.add_section("xray", name)
    if cif is not None:
        p.attach_file(s, cif)
    return s


def tree(p):
    return [(s.name, s.type, [c.name for c in s.children]) for s in p.top_sections]


def test_a_new_x_ray_structure(project):
    p, _data, a, _b = project
    s = xray_section(p)
    assert s.type == "xray" and s.type_label == "X-ray structure"
    assert s.data["table"] is True and s.data["caption"] == ""
    assert (s.folder / "naphthalene_cod_2311090.cif").is_file()
    loaded = xr.load(p, s)
    assert loaded.grown is not None and len(loaded.grown.atoms) == 18
    view = xr.view_of(s, loaded.grown)
    assert view.style == "ortep" and view.zoom > 0
    assert xr.caption(p, s, view, loaded.block) == \
        "Molecular structure (thermal ellipsoids at 50% probability)."
    s.data["compound"] = a.id
    view.hydrogens = "hide"
    assert xr.caption(p, s, view, loaded.block) == (
        f"Molecular structure of @{{{a.id}}} (thermal ellipsoids at 50% probability; "
        "hydrogen atoms omitted for clarity).")
    with pytest.raises(ProjectError, match="not a CIF"):
        p.attach_file(s, Path(__file__))


def test_no_cif_yet(project):
    p, *_ = project
    s = xray_section(p, cif=None)
    assert xr.load(p, s).error == "No CIF file has been chosen yet."


def test_the_table_with_values_typed_over_and_rows_left_out(project):
    p, *_ = project
    s = xray_section(p)
    block = xr.load(p, s).block
    rows = {k: v for k, _l, v, _p in xr.table_rows(s, block)}
    assert rows["formula"] == "C_{10}H_{8}"
    s.data["table_values"] = {"temperature": "80(1)"}
    s.data["table_hidden"] = ["code"]
    rows = xr.table_rows(s, block)
    assert ("temperature", "Temperature/K", "80(1)", True) in rows
    assert "code" not in [k for k, *_r in rows]
    assert ("code" in [k for k, *_r in xr.table_rows(s, block, all_rows=True)])
    printed = xr.table_json(s, block)
    assert printed["header_rows"] == 0 and len(printed["rows"]) == len(rows)


def test_under_a_compound_and_back(project):
    p, data, a, b = project
    s = xray_section(p)
    assert tree(p)[-1][0] == "X-ray of 1a"
    p.anchor_section(s.id, a.id)
    assert s.anchored_to == a.id and p.anchored == {a.id: [s]}
    assert "X-ray of 1a" not in [x[0] for x in tree(p)]
    assert p.section(s.id) is s and p.xray_sections() == [s]
    # it is saved: opened again, it is still under its compound
    p.reload()
    s = p.section(s.id)
    assert s.anchored_to == a.id and p.anchored[a.id] == [s]
    # moved into the tree, it is no longer under the compound
    p.move_section(s.id, 1, None)
    assert not s.anchored_to and p.anchored == {}
    assert tree(p)[1][0] == "X-ray of 1a"
    with p.undo_step("Put the X-ray structure under 1b"):   # (as the window does)
        p.anchor_section(s.id, b.id)
    assert p.section(s.id).anchored_to == b.id
    p.undo()
    assert not p.section(s.id).anchored_to and p.anchored == {}
    assert tree(p)[1][0] == "X-ray of 1a"
    with pytest.raises(ProjectError):
        p.anchor_section(data.id, a.id)                  # (only an X-ray structure)


def test_a_compound_deleted_leaves_its_x_ray_in_the_tree(project):
    p, data, a, _b = project
    s = xray_section(p)
    p.anchor_section(s.id, a.id)
    p.delete_compound(a.id)
    assert not s.anchored_to and p.anchored == {}
    names = [x[0] for x in tree(p)]
    assert names.index("X-ray of 1a") == names.index("Products") + 1


def test_deleting_and_restoring_an_x_ray_under_a_compound(project):
    p, _data, a, _b = project
    s = xray_section(p)
    p.anchor_section(s.id, a.id)
    entry = p.delete_section(s.id)
    assert p.anchored == {}
    assert entry.info.get("anchored_to") == a.id
    message = p.restore_from_trash(entry.id)
    assert "under its compound" in message
    assert [x.name for x in p.anchored[a.id]] == ["X-ray of 1a"]


def test_compiling(project, qapp):
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    p, _data, a, _b = project
    free = xray_section(p, "Crystal structure")
    under = xray_section(p, "X-ray of 1a")
    p.anchor_section(under.id, a.id)
    under.data.update({"compound": a.id, "table": False})
    p.save_section(under)
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png)
    typ = result.typ.read_text(encoding="utf-8")
    assert result.pdf.exists() and not [w for w in result.warnings if "X-ray" in w]
    # under the compound: right after it, figure only, with the compound's ID
    assert typ.index("Naphthalene") < typ.index(f"{under.id}.svg") < typ.index("Anthracene")
    assert "Molecular structure of" in typ and "1a" in typ
    # in the tree: its heading, its figure and its table
    assert typ.index("Crystal structure") < typ.index(f"{free.id}.svg")
    assert typ.count("#si-table(") == 1 and "Crystal data and structure refinement" in typ
    assert "Figure S1" in typ and "Figure S2" in typ and "Table S1" in typ
    from pypdf import PdfReader
    text = "".join(page.extract_text() for page in PdfReader(str(result.pdf)).pages)
    assert "Empirical formula" in text and "C10H8" in text.replace(" ", "")


def test_missing_file_is_a_warning(project, qapp):
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    p, *_ = project
    s = xray_section(p, "Empty", cif=None)
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png)
    assert any("Empty" in w and "No CIF file" in w for w in result.warnings)
