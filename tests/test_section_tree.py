"""Sections and sub-sections (SPEC §13k.5): the tree in project.json, depth rules,
moving, and deleting/restoring whole sub-trees."""

import json

import pytest

from si_assistant.core import front, links
from si_assistant.core.project import Project, ProjectError, SECTIONS_DIR


def tree_of(p):
    return json.loads((p.folder / "project.json").read_text(encoding="utf-8"))["section_tree"]


def outline(p):
    """Every section as (depth, name), in document order."""
    return [(p.depth(s), s.name) for s in p.sections]


@pytest.fixture
def nested(project):
    """Methods ▸ (Solvents ▸ Drying), Products (data, inside Methods), Appendix."""
    methods = project.add_section("text", "Methods")
    solvents = project.add_section("text", "Solvents", parent_id=methods.id)
    drying = project.add_section("text", "Drying", parent_id=solvents.id)
    products = project.add_section("data", "Products", parent_id=methods.id)
    appendix = project.add_section("file", "Appendix")
    return project, dict(methods=methods, solvents=solvents, drying=drying,
                         products=products, appendix=appendix)


def test_tree_is_saved_and_reloaded(nested):
    p, s = nested
    assert outline(p) == [(1, "Methods"), (2, "Solvents"), (3, "Drying"), (2, "Products"),
                          (1, "Appendix")]
    data = json.loads((p.folder / "project.json").read_text(encoding="utf-8"))
    assert "section_order" not in data
    assert tree_of(p) == [{"id": s["methods"].id, "children": [
        {"id": s["solvents"].id, "children": [{"id": s["drying"].id}]},
        {"id": s["products"].id}]}, {"id": s["appendix"].id}]
    again = Project.open(p.folder)
    assert outline(again) == outline(p)
    assert again.section(s["drying"].id).parent_id == s["solvents"].id
    # Folders stay flat under sections/ (short paths).
    assert all(sec.folder.parent == p.folder / SECTIONS_DIR for sec in again.sections)


def test_old_flat_section_order_still_opens(project):
    a = project.add_section("text", "A")
    b = project.add_section("data", "B")
    data = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    del data["section_tree"]
    data["section_order"] = [b.id, a.id]   # as saved before sub-sections existed
    (project.folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    old = Project.open(project.folder)
    assert [s.name for s in old.sections] == ["B", "A"]
    assert all(old.depth(s) == 1 for s in old.sections)
    old.acquire_lock("Tester")
    old.save_settings()
    saved = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert saved["section_tree"] == [{"id": b.id}, {"id": a.id}]
    assert "section_order" not in saved
    old.close()


def test_only_custom_sections_hold_sub_sections(nested):
    p, s = nested
    with pytest.raises(ProjectError, match="Only custom sections"):
        p.add_section("text", "X", parent_id=s["products"].id)
    with pytest.raises(ProjectError, match="Only custom sections"):
        p.add_section("text", "X", parent_id=s["appendix"].id)
    with pytest.raises(ProjectError, match="at most 3 levels"):
        p.add_section("text", "Too deep", parent_id=s["drying"].id)
    assert not p.can_place_section(s["drying"].id)
    assert p.can_place_section(s["solvents"].id)


def test_move_checks_depth_and_cycles(nested):
    p, s = nested
    # Solvents (2 levels with Drying) cannot go under Products' sibling at level 2…
    other = p.add_section("text", "Other", parent_id=s["methods"].id)
    with pytest.raises(ProjectError, match="3 levels|levels deep"):
        p.move_section(s["solvents"].id, 0, other.id)
    with pytest.raises(ProjectError, match="inside itself"):
        p.move_section(s["methods"].id, 0, s["solvents"].id)
    # …but a Data section can go into a custom section at level 2.
    p.move_section(s["products"].id, 0, other.id)
    assert p.section(s["products"].id).parent_id == other.id
    # Out to the top level, then reorder there.
    p.move_section(s["solvents"].id, 0, None)
    assert outline(p)[:2] == [(1, "Solvents"), (2, "Drying")]
    p.move_section(s["solvents"].id, 5)   # same parent, clamped to the end
    assert outline(Project.open(p.folder))[-2:] == [(1, "Solvents"), (2, "Drying")]


def test_nested_data_sections_keep_their_compounds(nested):
    p, s = nested
    c = p.add_compound(s["products"].id, name="Ester")
    assert [d.id for d in p.data_sections()] == [s["products"].id]
    assert p.compound(c.id).name == "Ester"
    assert [x.id for x in Project.open(p.folder).all_compounds()] == [c.id]


def test_delete_and_restore_a_whole_sub_tree(nested):
    p, s = nested
    p.write_section_text(s["drying"], "Over sieves.")
    c = p.add_compound(s["products"].id, name="Ester")
    entry = p.delete_section(s["methods"].id)
    assert outline(p) == [(1, "Appendix")]
    trash = p.list_trash()
    assert len(trash) == 1 and trash[0].id == entry.id      # one Trash item for all four
    assert trash[0].info["subsections"] == 3
    assert not any(x.folder.exists() for x in (s["methods"], s["solvents"], s["drying"]))
    message = p.restore_from_trash(entry.id)
    assert "3 sub-section" in message
    assert outline(p) == [(1, "Methods"), (2, "Solvents"), (3, "Drying"), (2, "Products"),
                          (1, "Appendix")]
    again = Project.open(p.folder)
    assert outline(again) == outline(p)
    assert again.read_section_text(again.section(s["drying"].id)) == "Over sieves."
    assert again.compound(c.id).name == "Ester"
    assert p.list_trash() == []


def _block_rename_of(monkeypatch, folder):
    """Make renaming `folder` fail, as when a file in it is open in another program."""
    from pathlib import Path
    real = Path.rename

    def rename(self, target):
        if Path(self) == folder:
            raise PermissionError(32, "The process cannot access the file", str(folder))
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)


def test_delete_is_all_or_nothing_when_a_folder_is_locked(nested, monkeypatch):
    """If a sub-section's folder cannot be moved, nothing is deleted: the folders already
    moved into the Trash are put back and the project is unchanged."""
    p, s = nested
    p.write_section_text(s["drying"], "Over sieves.")
    _block_rename_of(monkeypatch, s["drying"].folder)
    with pytest.raises(ProjectError, match="nothing was deleted"):
        p.delete_section(s["methods"].id)
    monkeypatch.undo()
    assert all(s[k].folder.exists() for k in ("methods", "solvents", "drying", "products"))
    assert p.list_trash() == []
    assert not p.trash_dir.exists() or not any(p.trash_dir.iterdir())
    assert outline(Project.open(p.folder)) == outline(p) == [
        (1, "Methods"), (2, "Solvents"), (3, "Drying"), (2, "Products"), (1, "Appendix")]
    # Once it is free again, deleting works.
    p.delete_section(s["methods"].id)
    assert outline(p) == [(1, "Appendix")]


def test_delete_with_a_really_open_file(nested):
    """On Windows a file held open by another program blocks renaming its folder."""
    import sys
    if sys.platform != "win32":
        pytest.skip("Open files only block renaming on Windows")
    p, s = nested
    path = s["drying"].folder / "spectrum.pdf"
    path.write_bytes(b"%PDF-1.4")
    with open(path, "rb"):
        with pytest.raises(ProjectError, match="nothing was deleted"):
            p.delete_section(s["methods"].id)
    assert (s["drying"].folder / "section.json").exists()
    assert p.list_trash() == []
    p.delete_section(s["methods"].id)
    assert len(p.list_trash()) == 1


def test_trash_lists_and_restores_a_deletion_that_stopped_part_way(nested):
    """A crash part-way through a deletion: the entry is marked pending (or, from older
    versions, has no description at all). Either way it is listed and can be restored."""
    p, s = nested
    from si_assistant.core.project import TRASH_ENTRY_FILE
    entry = p.delete_section(s["solvents"].id)
    info = json.loads((entry.folder / TRASH_ENTRY_FILE).read_text(encoding="utf-8"))
    # Pending: Drying never left sections/ (simulate by moving it back).
    (entry.folder / s["drying"].folder.name).rename(s["drying"].folder)
    (entry.folder / TRASH_ENTRY_FILE).write_text(json.dumps({**info, "pending": True}),
                                                 encoding="utf-8")
    again = Project.open(p.folder)
    again.acquire_lock("Tester")
    [listed] = again.list_trash()
    assert "did not finish" in listed.name
    again.restore_from_trash(listed.id)
    assert {x.name for x in again.sections} >= {"Solvents", "Drying"}
    assert again.section(s["solvents"].id).parent_id == s["methods"].id
    again.close()
    # No description at all.
    p2 = Project.open(p.folder)
    p2.acquire_lock("Tester")
    entry = p2.delete_section(s["appendix"].id)
    (entry.folder / TRASH_ENTRY_FILE).unlink()
    [listed] = p2.list_trash()
    assert listed.kind == "incomplete" and s["appendix"].folder.name in listed.name
    assert "Restored" in p2.restore_from_trash(listed.id)
    assert p2.sections[-1].name == "Appendix" and p2.list_trash() == []
    assert Project.open(p.folder).sections[-1].name == "Appendix"
    p2.close()


def test_restore_sub_section_whose_parent_is_gone(nested):
    p, s = nested
    first = p.delete_section(s["solvents"].id)
    p.delete_section(s["methods"].id)
    message = p.restore_from_trash(first.id)
    assert "top level" in message
    assert outline(p) == [(1, "Appendix"), (1, "Solvents"), (2, "Drying")]


def test_sub_sections_saved_in_the_wrong_place_are_lifted(nested):
    p, s = nested
    extra = p.add_section("text", "Extra")
    data = json.loads((p.folder / "project.json").read_text(encoding="utf-8"))
    # A sync or hand edit put Appendix inside the Data section, and Extra 4 levels down.
    data["section_tree"] = [{"id": s["products"].id, "children": [{"id": s["appendix"].id}]},
                            {"id": s["solvents"].id, "children": [{"id": s["drying"].id,
                             "children": [{"id": s["methods"].id,
                                           "children": [{"id": extra.id}]}]}]}]
    (p.folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    again = Project.open(p.folder)
    assert outline(again) == [(1, "Products"), (1, "Appendix"), (1, "Solvents"),
                              (2, "Drying"), (3, "Methods"), (3, "Extra")]


def test_front_matter_texts_and_links(project):
    project.write_front_text("general", "See @{c-gone00}.")
    project.write_front_text("cover", "Extra")
    assert (project.folder / "front" / "general_information.txt").read_text(
        encoding="utf-8") == "See @{c-gone00}."
    places = [pl for pl in links.all_texts(project) if pl.kind == "front"]
    assert [pl.owner_id for pl in places] == ["cover", "general"]
    assert links.mention_uses(project, "c-gone00") == ["Front matter ▸ General Information"]
    rel = project.add_front_image(data=b"png")
    assert rel.startswith("images/") and (project.front_folder / rel).read_bytes() == b"png"


def test_use_section_as_general_information(project):
    old = project.add_section("text", "General Methods")
    rel = project.add_section_image(old, data=b"picture bytes")
    project.write_section_text(old, f"Dry solvents.\n!image{{{rel}}} An apparatus\nEnd.")
    project.use_as_general_information(old.id)
    assert project.sections == []
    text = project.read_front_text("general")
    lines = text.split("\n")
    assert lines[0] == "Dry solvents." and lines[2] == "End."
    # The older picture became a figure (SPEC §13k.5), copied to front/images/.
    assert lines[1].startswith("!figure{f-") and lines[1].endswith("} An apparatus")
    figure_id = lines[1][len("!figure{"):lines[1].index("}")]
    general = project.text_owner("general")
    assert project.figure_path(general, figure_id).read_bytes() == b"picture bytes"
    assert front.general_heading(project.settings) == "General Methods"
    assert [e.kind for e in project.list_trash()] == ["section"]
    # A second one is added after the text already there; the heading is kept.
    more = project.add_section("text", "Safety")
    project.write_section_text(more, "Wear goggles.")
    project.use_as_general_information(more.id)
    assert project.read_front_text("general").endswith("End.\n\nWear goggles.")
    assert front.general_heading(project.settings) == "General Methods"


def test_use_as_general_information_refuses_sections_with_children(nested):
    p, s = nested
    with pytest.raises(ProjectError, match="sub-sections"):
        p.use_as_general_information(s["methods"].id)
    with pytest.raises(ProjectError, match="custom section"):
        p.use_as_general_information(s["products"].id)
