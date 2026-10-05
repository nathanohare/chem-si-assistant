import pytest

from si_assistant.core import fields as F
from si_assistant.core.project import Project, ProjectError


@pytest.fixture
def data(project):
    return project.add_section("data", "Products")


def ids(project):
    report = project.id_report()
    return [report.display_id(c.id) for c in project.all_compounds()]


def reopen(project):
    return Project.open(project.folder)


# ---------------------------------------------------------------------- fields

def test_nucleus_label():
    assert F.nucleus_label("1H") == "¹H"
    assert F.nucleus_label("13C") == "¹³C"
    assert F.nucleus_label("Other") == "Other"


def test_is_filled():
    yld = F.ALL_FIELDS["yield"]
    assert not F.is_filled(yld, {"percent": "92", "mass": "", "mass_unit": "mg"})
    assert F.is_filled(yld, {"percent": "92", "mass": "28.4", "mass_unit": "mg"})
    hrms = F.ALL_FIELDS["hrms"]
    assert not F.is_filled(hrms, F.empty_value(hrms))  # defaults alone don't count
    assert F.is_filled(hrms, {"ionization": "ESI", "adduct": "[M+H]+", "found": "188.1073"})
    nmr = F.ALL_FIELDS["nmr"]
    assert not F.is_filled(nmr, [{"nucleus": "1H", "text": " "}])
    assert F.is_filled(nmr, [{"nucleus": "1H", "text": "δ 7.25"}])
    assert F.is_filled(F.ALL_FIELDS["procedure"], None, "Prepared…")
    assert not F.is_filled(F.ALL_FIELDS["spectra"], [])


def test_custom_field_defs():
    d = F.field_def({"key": "custom-1", "custom": {"label": "Catalyst lot", "type": "number_unit"}})
    assert d.label == "Catalyst lot" and d.kind == "parts"
    assert F.field_def({"key": "custom-2", "custom": {"label": "X", "type": "file"}}).kind == "attachments"
    assert F.field_def({"key": "unknown"}) is None


# ---------------------------------------------------------------------- compounds

def test_new_compound_gets_section_defaults(project, data):
    c = project.add_compound(data.id, name="Ethyl ester")
    assert c.field_keys == [e["key"] for e in F.DEFAULT_SECTION_FIELDS] + ["notes"]
    assert [r["nucleus"] for r in c.values["nmr"]] == ["1H", "13C"]
    assert c.values["hrms"]["ionization"] == "ESI"
    r = reopen(project)
    assert r.compound(c.id).name == "Ethyl ester"
    assert r.section(data.id).compounds[0].id == c.id


def test_ids_follow_position_on_reorder(project, data):
    project.set_id_table(data.id, ["3a", "3b", "3c"])
    a, b, c = (project.add_compound(data.id, name=n) for n in "ABC")
    assert ids(project) == ["3a", "3b", "3c"]
    project.move_compound(c.id, data.id, 0)
    assert [x.name for x in project.all_compounds()] == ["C", "A", "B"]
    assert ids(project) == ["3a", "3b", "3c"]
    assert [x.id for x in reopen(project).all_compounds()] == [c.id, a.id, b.id]


def test_delete_renumbers_following_compounds(project, data):
    project.set_id_table(data.id, ["3a", "3b", "3c"])
    a, b, c = (project.add_compound(data.id, name=n) for n in "ABC")
    project.delete_compound(b.id)
    report = project.id_report()
    assert report.display_id(c.id) == "3b"  # was 3c
    entry = project.list_trash()[0]
    assert entry.kind == "compound"
    project.restore_from_trash(entry.id)
    assert [x.name for x in project.all_compounds()] == ["A", "B", "C"]
    assert project.id_report().display_id(c.id) == "3c"


def test_override_persists_and_keeps_position(project, data):
    project.set_id_table(data.id, ["3a", "3b", "3c"])
    a, b, c = (project.add_compound(data.id) for _ in range(3))
    b.data["id_override"] = "12"
    project.save_compound(b)
    assert ids(reopen(project)) == ["3a", "12", "3c"]


def test_move_compound_between_sections(project, data):
    other = project.add_section("data", "Starting materials")
    project.set_id_table(data.id, ["3a", "3b"])
    project.set_id_table(other.id, ["1a", "1b"])
    a = project.add_compound(data.id, name="A")
    b = project.add_compound(data.id, name="B")
    project.write_compound_text(b, "procedure", "text survives the move")
    project.move_compound(b.id, other.id, 0)
    assert b.folder.parent.parent == other.folder
    r = reopen(project)
    assert [c.name for c in r.section(data.id).compounds] == ["A"]
    assert [c.name for c in r.section(other.id).compounds] == ["B"]
    assert r.id_report().display_id(b.id) == "1a"
    assert r.read_compound_text(r.compound(b.id), "procedure") == "text survives the move"


def test_cannot_move_compound_into_text_section(project, data):
    text = project.add_section("text", "General")
    c = project.add_compound(data.id)
    with pytest.raises(ProjectError):
        project.move_compound(c.id, text.id, 0)
    assert project.compound(c.id).section_id == data.id


def test_remove_and_readd_field_keeps_value(project, data):
    c = project.add_compound(data.id)
    c.values["scale"] = {"value": "0.20", "unit": "mmol"}
    project.save_compound(c)
    project.remove_field(c, "scale")
    assert "scale" not in reopen(project).compound(c.id).field_keys
    project.add_field(c, {"key": "scale"})
    assert reopen(project).compound(c.id).values["scale"]["value"] == "0.20"
    assert c.field_keys[-1] == "notes"  # notes stays last


def test_notes_cannot_be_removed(project, data):
    c = project.add_compound(data.id)
    with pytest.raises(ProjectError):
        project.remove_field(c, "notes")


def test_custom_field(project, data):
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "custom-ab12", "custom": {"label": "Lot", "type": "short_text"}})
    reopened = reopen(project)
    r = reopened.compound(c.id)
    assert "custom-ab12" in r.field_keys
    assert [d.label for d in reopened.field_defs(r)][-2] == "Lot"


def test_flags(project, data):
    c = project.add_compound(data.id)
    project.set_flag(c, "nmr", True, "re-run 13C, poor S/N")
    flag = reopen(project).compound(c.id).flags["nmr"]
    assert flag["comment"] == "re-run 13C, poor S/N" and flag["by"] == "Tester"
    project.set_flag(c, "nmr", False)
    assert "nmr" not in reopen(project).compound(c.id).flags


def test_compound_text_files(project, data):
    c = project.add_compound(data.id)
    project.write_compound_text(c, "procedure", "Prepared from **1a**.")
    project.write_compound_text(c, "notes", "private")
    assert (c.folder / "procedure.txt").read_text(encoding="utf-8") == "Prepared from **1a**."
    assert (c.folder / "notes.txt").is_file()


def test_attachments_add_remove_restore(project, data, tmp_path):
    src = tmp_path / "3a 1H.pdf"
    src.write_bytes(b"%PDF one")
    c = project.add_compound(data.id)
    item = project.attach_compound_file(c, "spectra", src, {"type": "1H"})
    assert item == {"file": "3a 1H.pdf", "type": "1H", "title": "", "struct_caption": ""}
    assert (c.folder / "spectra" / "3a 1H.pdf").read_bytes() == b"%PDF one"
    item2 = project.attach_compound_file(c, "spectra", src)  # same name -> renamed copy
    assert item2["file"] == "3a 1H (2).pdf"
    project.remove_compound_file(c, "spectra", "3a 1H.pdf")
    assert [i["file"] for i in c.values["spectra"]] == ["3a 1H (2).pdf"]
    entry = project.list_trash()[0]
    project.restore_from_trash(entry.id)
    restored = reopen(project).compound(c.id).values["spectra"]
    assert {"file": "3a 1H.pdf", "type": "1H", "title": "", "struct_caption": ""} in restored


def test_attachment_type_check(project, data, tmp_path):
    src = tmp_path / "x.docx"
    src.write_bytes(b"x")
    c = project.add_compound(data.id)
    with pytest.raises(ProjectError):
        project.attach_compound_file(c, "spectra", src)


def test_default_fields_and_apply(project, data):
    c = project.add_compound(data.id)
    project.remove_field(c, "rf")
    project.set_default_fields(data.id, [{"key": "structure", "required": True},
                                         {"key": "mp", "required": False},
                                         {"key": "nmr", "required": True}])
    assert project.apply_default_fields(data.id) == 1
    keys = reopen(project).compound(c.id).field_keys
    assert keys[:3] == ["structure", "mp", "nmr"] and keys[-1] == "notes"
    assert "procedure" in keys  # kept (not removing others)
    project.apply_default_fields(data.id, remove_others=True)
    assert reopen(project).compound(c.id).field_keys == ["structure", "mp", "nmr", "notes"]
    assert project.apply_default_fields(data.id, remove_others=True) == 0


def test_completion(project, data):
    project.set_default_fields(data.id, [{"key": "procedure", "required": True},
                                         {"key": "yield", "required": True},
                                         {"key": "rf", "required": False}])
    c = project.add_compound(data.id)
    assert project.completion(c) == (0, 2, ["Experimental procedure", "Yield"])
    project.write_compound_text(c, "procedure", "Done.")
    c.values["yield"] = {"percent": "90", "mass": "10.0", "mass_unit": "mg"}
    assert project.completion(c) == (2, 2, [])
    project.remove_field(c, "yield")  # not used -> not counted
    assert project.completion(c) == (1, 1, [])


def test_deleting_data_section_and_restoring_keeps_compounds(project, data):
    c = project.add_compound(data.id, name="Keep")
    project.delete_section(data.id)
    assert project.all_compounds() == []
    project.restore_from_trash(project.list_trash()[0].id)
    assert [x.name for x in project.all_compounds()] == ["Keep"]
