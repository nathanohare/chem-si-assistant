"""Scheme pages: a reaction scheme with its compounds as sub-pages (SPEC §6.3.5)."""

import pytest

from si_assistant.core.project import Project, ProjectError
from tests.test_structures import scheme_molblock


@pytest.fixture
def section(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["3a", "3b", "3c", "3d", "3e"])
    return s


def ids(project, section):
    report = project.id_report()
    return [report.display_id(c.id) for c in project.section(section.id).compounds]


def test_scheme_children_take_ids_in_tree_order(project, section):
    a = project.add_compound(section.id, name="A")
    scheme = project.add_scheme(section.id, name="Synthesis of C")
    b = project.add_compound(section.id, name="B", scheme_id=scheme.id)
    c = project.add_compound(section.id, name="C", scheme_id=scheme.id)
    d = project.add_compound(section.id, name="D")
    assert [x.name for x in section.compounds] == ["A", "B", "C", "D"]
    assert ids(project, section) == ["3a", "3b", "3c", "3d"]
    # Folders stay flat (Windows path limit): membership is recorded in scheme.json.
    assert b.folder.parent == section.folder / "compounds"
    reopened = Project.open(project.folder)
    rs = reopened.section(section.id)
    assert [type(i).__name__ for i in rs.items] == ["Compound", "SchemePage", "Compound"]
    assert [x.name for x in rs.compounds] == ["A", "B", "C", "D"]
    assert reopened.compound(b.id).scheme_id == scheme.id
    assert reopened.scheme(scheme.id).name == "Synthesis of C"


def test_move_compounds_in_and_out_of_schemes(project, section):
    a = project.add_compound(section.id, name="A")
    scheme = project.add_scheme(section.id)
    b = project.add_compound(section.id, name="B", scheme_id=scheme.id)
    project.move_compound(a.id, section.id, 1, scheme.id)       # into the scheme, after B
    assert [x.name for x in scheme.children] == ["B", "A"]
    assert a.folder.parent == section.folder / "compounds"  # no folder move needed
    project.move_compound(b.id, section.id, 0)                   # out, to the top
    assert [x.name for x in project.section(section.id).compounds] == ["B", "A"]
    assert b.scheme_id is None and b.folder.parent.parent == section.folder
    r = Project.open(project.folder)
    assert [x.name for x in r.section(section.id).compounds] == ["B", "A"]
    assert r.compound(a.id).scheme_id == scheme.id


def test_convert_compound_to_scheme(project, section):
    a = project.add_compound(section.id, name="A")
    b = project.add_compound(section.id, name="B")
    scheme = project.convert_to_scheme(b.id)
    assert section.items[1] is scheme and scheme.children == [b]
    assert scheme.name == "Scheme: B"
    assert ids(project, section) == ["3a", "3b"]  # numbering unchanged
    with pytest.raises(ProjectError):
        project.convert_to_scheme(b.id)


def test_delete_and_restore_scheme_with_children(project, section):
    scheme = project.add_scheme(section.id)
    b = project.add_compound(section.id, name="B", scheme_id=scheme.id)
    project.write_compound_text(b, "procedure", "kept")
    project.delete_scheme(scheme.id)
    assert section.compounds == []
    project.restore_from_trash(project.list_trash()[0].id)
    restored = project.compound(b.id)
    assert restored.scheme_id == scheme.id
    assert project.read_compound_text(restored, "procedure") == "kept"


def test_delete_child_and_restore_into_scheme(project, section):
    scheme = project.add_scheme(section.id)
    b = project.add_compound(section.id, name="B", scheme_id=scheme.id)
    c = project.add_compound(section.id, name="C", scheme_id=scheme.id)
    project.delete_compound(b.id)
    project.restore_from_trash(project.list_trash()[0].id)
    assert [x.name for x in project.scheme(scheme.id).children] == ["B", "C"]


def test_move_scheme_between_sections(project, section):
    other = project.add_section("data", "Derivatives")
    scheme = project.add_scheme(section.id)
    b = project.add_compound(section.id, name="B", scheme_id=scheme.id)
    project.move_scheme(scheme.id, other.id, 0)
    assert scheme.folder.parent.parent == other.folder
    assert project.compound(b.id).section_id == other.id
    r = Project.open(project.folder)
    assert [x.name for x in r.section(other.id).compounds] == ["B"]
    assert (r.compound(b.id).folder / "compound.json").is_file()


def test_scheme_picture_structures_and_matching(project, section):
    scheme = project.add_scheme(section.id)
    project.set_scheme_picture(scheme, data=b"\x89PNG scheme")
    assert project.scheme_image_path(scheme).read_bytes() == b"\x89PNG scheme"
    n = project.set_scheme_structures(scheme, scheme_molblock("OC(=O)c1ccccc1", "CCOC(=O)c1ccccc1"))
    assert n == 2
    ester = project.add_compound(section.id, name="ester", scheme_id=scheme.id)
    acid = project.add_compound(section.id, name="acid", scheme_id=scheme.id)
    other = project.add_compound(section.id, name="other", scheme_id=scheme.id)
    project.set_structure(ester, "O=C(OCC)c1ccccc1")
    project.set_structure(acid, "OC(=O)c1ccccc1")
    project.set_structure(other, "CCCC")
    assert project.match_scheme_structures(scheme) == {acid.id: 0, ester.id: 1}
    project.set_label_position(scheme, acid.id, 0.25, 1.4)
    assert project.scheme(scheme.id).labels[acid.id] == {"x": 0.25, "y": 1.0, "manual": True}
    project.remove_scheme_picture(scheme)
    project.restore_from_trash(project.list_trash()[0].id)
    assert project.scheme_image_path(scheme).exists()


def test_moving_child_out_forgets_its_label(project, section):
    scheme = project.add_scheme(section.id)
    b = project.add_compound(section.id, scheme_id=scheme.id)
    project.set_label_position(scheme, b.id, 0.5, 0.9)
    project.move_compound(b.id, section.id, 0)
    assert b.id not in project.scheme(scheme.id).labels
