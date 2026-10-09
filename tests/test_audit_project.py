"""Fixes from the audit of core/project.py and core/ids.py: deletions that are all or
nothing, damaged or unreadable files that never lose data, snapshots, Trash restores and
safe file names. Made-up data only."""

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from si_assistant.core import drawing_scale
from si_assistant.core import fileio
from si_assistant.core import scheme_editor as se
from si_assistant.core.ids import is_device_name, safe_filename, slugify
from si_assistant.core.project import (COMPOUND_FILE, Project, ProjectError, ReadOnlyError,
                                       SCHEME_FILE, SECTION_FILE)

PNG = b"\x89PNG made-up picture"


@pytest.fixture
def blocked(monkeypatch):
    """Paths that cannot be renamed, as with a file open in another program on Windows."""
    paths: set[Path] = set()
    real = fileio.rename

    def rename(source, dest):
        if Path(source) in paths:
            raise PermissionError(13, "The process cannot access the file", str(source))
        real(source, dest)

    monkeypatch.setattr(fileio, "rename", rename)
    return paths


@pytest.fixture
def data(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["1a", "1b", "1c", "1d", "1e"])
    return s


def on_disk(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reopen(project) -> Project:
    project.close()
    p = Project.open(project.folder)
    p.acquire_lock("Tester")
    return p


# ---------------------------------------------------------------- deletions (project-2, -3)

def test_deleting_a_scheme_is_all_or_nothing(project, data, blocked):
    scheme = project.add_scheme(data.id, name="Route")
    a = project.add_compound(data.id, name="A", scheme_id=scheme.id)
    b = project.add_compound(data.id, name="B", scheme_id=scheme.id)
    blocked.add(b.folder)   # e.g. a spectrum of B open in a PDF viewer
    with pytest.raises(ProjectError, match="nothing was deleted"):
        project.delete_scheme(scheme.id)
    assert scheme.folder.is_dir() and a.folder.is_dir() and b.folder.is_dir()
    assert project.list_trash() == []
    assert data.items == [scheme]
    blocked.clear()
    entry = project.delete_scheme(scheme.id)
    assert not a.folder.exists() and not b.folder.exists()
    project.restore_from_trash(entry.id)
    assert [c.name for c in project.scheme(scheme.id).children] == ["A", "B"]


def test_removing_an_open_attachment_keeps_it_listed(project, data, blocked, tmp_path):
    c = project.add_compound(data.id)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF made up")
    project.attach_compound_file(c, "spectra", pdf, {"type": "1H NMR"})
    path = project.attachment_path(c, "spectra", "x.pdf")
    blocked.add(path)
    with pytest.raises(ProjectError, match="nothing was deleted"):
        project.remove_compound_file(c, "spectra", "x.pdf")
    assert [i["file"] for i in c.values["spectra"]] == ["x.pdf"]
    saved = on_disk(c.folder / COMPOUND_FILE)["values"]["spectra"]
    assert [i["file"] for i in saved] == ["x.pdf"] and path.exists()


def test_replacing_an_open_section_file_keeps_the_old_one(project, blocked, tmp_path):
    section = project.add_section("file", "Crystal data")
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    old.write_bytes(b"%PDF old")
    new.write_bytes(b"%PDF new")
    stored = project.attach_file(section, old)
    blocked.add(stored)
    with pytest.raises(ProjectError):
        project.attach_file(section, new)
    with pytest.raises(ProjectError):
        project.remove_file(section)
    assert section.data["file"] == "old.pdf"
    assert on_disk(section.folder / SECTION_FILE)["file"] == "old.pdf"
    assert sorted(p.name for p in section.folder.iterdir()) == ["old.pdf", SECTION_FILE]


def test_removing_an_open_private_file_keeps_it_listed(project, data, blocked, tmp_path):
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"made-up raw data")
    project.add_private_item(c, raw)
    blocked.add(project.private_dir(c) / "raw.bin")
    with pytest.raises(ProjectError):
        project.remove_private_item(c, "raw.bin")
    assert [i["file"] for i in on_disk(c.folder / COMPOUND_FILE)["values"]["private_files"]] \
        == ["raw.bin"]


def test_new_structure_with_an_open_chemdraw_picture_changes_nothing(project, data, blocked):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO", picture=PNG)
    picture = project.structure_image_path(c)
    mol_before = project.structure_path(c).read_text(encoding="utf-8")
    blocked.add(picture)
    with pytest.raises(ProjectError):
        project.set_structure(c, "c1ccccc1")   # (would trash the outdated picture)
    # Not the new structure printed with the old structure's picture:
    assert project.structure_path(c).read_text(encoding="utf-8") == mol_before
    assert project.structure_picture_source(c) == "chemdraw"
    assert on_disk(c.folder / COMPOUND_FILE)["values"]["structure"]["image_override"]


def test_deleting_an_open_scheme_compound_keeps_its_box_linked(project, data, blocked):
    k = project.add_scheme(data.id)
    box = se.steps(k)[0]["products"][0]
    se.set_box_structure(project, k, box["id"], "c1ccccc1O")
    c = se.link_new_compound(project, k, box["id"])
    before = json.loads(json.dumps(k.data))
    blocked.add(c.folder)
    with pytest.raises(ProjectError):
        project.delete_compound(c.id)
    assert se.find_box(k, box["id"])[0].get("compound") == c.id
    assert k.data == before
    assert on_disk(k.folder / SCHEME_FILE)["editor"] == before["editor"]
    assert project.compound(c.id) is c


# ---------------------------------------------------------------- damaged JSON (project-4)

def test_damaged_references_file_is_kept_before_it_is_replaced(project):
    for title in ("A", "B", "C"):
        project.add_reference({"title": title})
    path = project.folder / "references.json"
    damaged = path.read_text(encoding="utf-8").rstrip().rstrip("]") + ",]"   # a trailing comma
    path.write_text(damaged, encoding="utf-8")
    p = reopen(project)
    assert any("references.json could not be read" in w for w in p.load_warnings)
    p.add_reference({"title": "New"})
    backups = list(p.folder.glob("references.damaged-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == damaged
    assert [r["title"] for r in on_disk(path)] == ["New"]
    p.add_reference({"title": "Newer"})   # (the file is fine again: no second copy)
    assert len(list(p.folder.glob("references.damaged-*.json"))) == 1
    p.close()


def test_damaged_front_json_is_kept_before_it_is_replaced(project, tmp_path):
    owner = project.text_owner("general")
    project.add_figure(owner, data=PNG, label="one")
    path = project.front_folder / "front.json"
    damaged = path.read_text(encoding="utf-8").rstrip().rstrip("}") + ', "x": 1,}'
    path.write_text(damaged, encoding="utf-8")
    p = reopen(project)
    assert any("front.json could not be read" in w for w in p.load_warnings)
    p.add_figure(p.text_owner("general"), data=PNG, label="two")
    backups = list(p.front_folder.glob("front.damaged-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == damaged
    p.close()


# ---------------------------------------------------------------- snapshots (project-6, read-only)

def test_snapshot_with_an_old_file_date_and_stored_pictures(project):
    old = project.folder / "sections" / "raw.bin"
    old.write_bytes(b"from an instrument PC without a clock")
    os.utime(old, (0, 0))
    (project.folder / "sections" / "picture.png").write_bytes(PNG)
    dest = project.save_snapshot("old dates")
    with zipfile.ZipFile(dest) as zf:
        assert zf.testzip() is None
        info = {Path(i.filename).name: i for i in zf.infolist()}
    assert info["raw.bin"].date_time[0] == 1980
    assert info["picture.png"].compress_type == zipfile.ZIP_STORED
    assert info["project.json"].compress_type == zipfile.ZIP_DEFLATED
    assert not list((project.folder / "snapshots").glob("*.partial"))


def test_failed_snapshot_leaves_no_partial_file(project, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError(13, "locked")
    monkeypatch.setattr(zipfile.ZipFile, "write", fail)
    with pytest.raises(OSError):
        project.save_snapshot()
    assert list((project.folder / "snapshots").iterdir()) == []


def test_read_only_project_writes_no_snapshot(project):
    viewer = Project.open(project.folder, read_only=True)
    with pytest.raises(ReadOnlyError):
        viewer.save_snapshot("x")
    assert not (project.folder / "snapshots").exists()


# ---------------------------------------------------------------- adding files (hrms-3)

@pytest.mark.parametrize("adder", ["hrms", "private"])
def test_failed_folder_copy_leaves_nothing_behind(project, data, tmp_path, monkeypatch, adder):
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "Sample.d"
    (raw / "AcqData").mkdir(parents=True)
    (raw / "AcqData" / "MSScan.bin").write_bytes(b"made up")
    real = shutil.copytree

    def half_copy(source, dest, *args, **kwargs):   # (e.g. a path too long for Windows)
        Path(dest).mkdir()
        (Path(dest) / "part.bin").write_bytes(b"part")
        raise shutil.Error([(str(source), str(dest), "path too long")])

    monkeypatch.setattr(fileio.shutil, "copytree", half_copy)
    add = project.add_hrms_report if adder == "hrms" else project.add_private_item
    folder = project.hrms_report_dir(c) if adder == "hrms" else project.private_dir(c)
    with pytest.raises(ProjectError, match="Could not copy 1 file"):
        add(c, raw)
    assert list(folder.iterdir()) == []
    assert project.hrms_reports(c) == [] and not c.values.get("private_files")
    monkeypatch.setattr(fileio.shutil, "copytree", real)
    assert add(c, raw)["file"] == "Sample.d"   # (no "Sample (2).d")


# ---------------------------------------------------------------- Trash restores (project-5, -8)

def test_restoring_a_cleared_structure_keeps_its_chemdraw_picture(project, data):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO", from_scheme=True, picture=PNG)
    project.clear_structure(c)
    project.restore_from_trash(project.list_trash()[0].id)
    assert project.structure_info(c).formula == "C2H6O"
    assert project.structure_picture_source(c) == "chemdraw"
    assert project.structure_from_scheme(c)
    assert project.list_trash() == []


def test_restoring_a_structure_never_keeps_another_structures_picture(project, data):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO", picture=PNG)
    project.clear_structure(c)
    first = project.list_trash()[0]
    project.set_structure(c, "c1ccccc1", picture=b"\x89PNG benzene")
    project.clear_structure(c)
    project.restore_from_trash(first.id)   # ethanol back: benzene's picture must go
    assert project.structure_info(c).formula == "C2H6O"
    assert project.structure_image_path(c) is None


def test_restoring_an_unreadable_structure_says_so(project, data):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO")
    project.structure_path(c).write_text("garbage", encoding="utf-8")
    project.clear_structure(c)
    entry = project.list_trash()[0]
    with pytest.raises(ProjectError, match="could not be read"):
        project.restore_from_trash(entry.id)
    assert [e.id for e in project.list_trash()] == [entry.id]


@pytest.mark.parametrize("adder", ["hrms", "private"])
def test_restoring_raw_data_moves_it_back(project, data, tmp_path, monkeypatch, adder):
    project.enable_undo()
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "Sample.d"
    (raw / "AcqData").mkdir(parents=True)
    (raw / "AcqData" / "MSScan.bin").write_bytes(b"made up")
    if adder == "hrms":
        project.add_hrms_report(c, raw)
        project.remove_hrms_report(c, "Sample.d")
        listed = lambda: project.hrms_reports(c)
        folder = project.hrms_report_dir(c)
    else:
        project.add_private_item(c, raw, note="kept")
        project.remove_private_item(c, "Sample.d")
        listed = lambda: c.values.get("private_files") or []
        folder = project.private_dir(c)

    def no_copy(*args, **kwargs):
        raise AssertionError("copied instead of moved back")

    monkeypatch.setattr(fileio.shutil, "copytree", no_copy)
    with project.undo_step("Restore"):
        project.restore_from_trash(project.list_trash()[0].id)
    assert [(i["file"], i["kind"]) for i in listed()] == [("Sample.d", "folder")]
    assert (folder / "Sample.d" / "AcqData" / "MSScan.bin").read_bytes() == b"made up"
    assert project.list_trash() == []
    project.undo()
    assert len(project.list_trash()) == 1 and not (folder / "Sample.d").exists()
    project.redo()
    assert (folder / "Sample.d" / "AcqData" / "MSScan.bin").exists()


def test_restoring_raw_data_copies_when_it_cannot_be_moved(project, data, tmp_path, blocked):
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"made up")
    project.add_private_item(c, raw)
    project.remove_private_item(c, "raw.bin")
    entry = project.list_trash()[0]
    blocked.add(entry.folder / "raw.bin")
    project.restore_from_trash(entry.id)
    assert (project.private_dir(c) / "raw.bin").read_bytes() == b"made up"


# ---------------------------------------------------------------- unreadable files keep their place

def damage(path: Path) -> str:
    good = path.read_text(encoding="utf-8")
    path.write_text(good[:-3], encoding="utf-8")   # cut off, as by an interrupted sync
    return good


def test_unreadable_files_keep_their_place_in_tree_and_orders(project, data):
    parent = project.add_section("text", "Parent")
    child = project.add_section("text", "Child", parent_id=parent.id)
    project.add_compound(data.id, name="c0")
    c1 = project.add_compound(data.id, name="c1")
    project.add_compound(data.id, name="c2")
    k = project.add_scheme(data.id, name="Route")
    k1 = project.add_compound(data.id, name="k1", scheme_id=k.id)
    project.add_compound(data.id, name="k2", scheme_id=k.id)
    ids_before = [project.id_report().display_id(c.id) for c in data.compounds]
    files = [parent.folder / SECTION_FILE, c1.folder / COMPOUND_FILE, k1.folder / COMPOUND_FILE]
    good = [damage(f) for f in files]

    p = reopen(project)
    assert len([w for w in p.load_warnings if "keeps its place" in w]) == 3
    assert [s.name for s in p.top_sections][:2] == ["Products", "Child"]   # (lifted up)
    p.rename_section(child.id, "Child renamed")       # ordinary edits and saves
    p.add_compound(data.id, name="c3")
    p.rename_scheme(k.id, "Route 2")
    for f, text in zip(files, good):
        f.write_text(text, encoding="utf-8")          # repaired (or downloaded) again

    r = reopen(p)
    assert r.load_warnings == []
    top = [(s.name, [x.name for x in s.children]) for s in r.top_sections]
    assert top == [("Products", []), ("Parent", ["Child renamed"])]
    rs = r.section(data.id)
    assert [type(i).__name__ for i in rs.items] == ["Compound"] * 3 + ["SchemePage", "Compound"]
    assert [c.name for c in rs.compounds] == ["c0", "c1", "c2", "k1", "k2", "c3"]
    assert [r.id_report().display_id(c.id) for c in rs.compounds][:5] == ids_before
    r.close()


def test_no_second_spectra_section_while_one_cannot_be_read(project):
    spectra = project.ensure_spectra_section()
    good = damage(spectra.folder / SECTION_FILE)
    p = reopen(project)
    assert p.ensure_spectra_section() is None and p.spectra_section is None
    (spectra.folder / SECTION_FILE).write_text(good, encoding="utf-8")
    p = reopen(p)
    assert p.ensure_spectra_section().id == spectra.id
    p.close()


def test_a_sub_section_moved_meanwhile_stays_where_it_was_put(project):
    parent = project.add_section("text", "Parent")
    child = project.add_section("text", "Child", parent_id=parent.id)
    other = project.add_section("text", "Other")
    good = damage(parent.folder / SECTION_FILE)
    p = reopen(project)
    p.move_section(child.id, 0, other.id)
    (parent.folder / SECTION_FILE).write_text(good, encoding="utf-8")
    r = reopen(p)
    assert [(s.name, [x.name for x in s.children]) for s in r.top_sections] == \
        [("Parent", []), ("Other", ["Child"])]
    r.close()


def test_an_unreadable_item_deleted_meanwhile_is_dropped(project, data):
    c0 = project.add_compound(data.id, name="c0")
    c1 = project.add_compound(data.id, name="c1")
    damage(c1.folder / COMPOUND_FILE)
    p = reopen(project)
    shutil.rmtree(c1.folder)          # deleted by hand while the project was open
    p.add_compound(data.id, name="c2")
    assert on_disk(data.folder / SECTION_FILE)["compound_order"] == \
        [c0.id, p.section(data.id).compounds[-1].id]
    p.close()


def test_offline_files_do_not_stop_the_project_opening(project, data, monkeypatch):
    c = project.add_compound(data.id, name="c")
    d = project.add_compound(data.id, name="d")
    project.set_structure(d, "CCO")
    offline = {c.folder / COMPOUND_FILE, project.folder / "references.json"}
    real = Path.read_bytes

    def read_bytes(self):
        if self in offline:
            raise OSError(22, "The cloud file provider is not running", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    p = reopen(project)
    assert any("online-only OneDrive file" in w for w in p.load_warnings)
    assert [x.name for x in p.section(data.id).compounds] == ["d"]
    offline.add(p.structure_path(p.compound(d.id)))
    assert p.read_structure(p.compound(d.id)) is None         # (no crash in the tree)
    with pytest.raises(OSError):
        p.read_structure(p.compound(d.id), strict=True)       # (compiling must not skip it)
    offline.clear()
    assert p.structure_info(p.compound(d.id)).formula == "C2H6O"   # tried again
    p.close()


def test_references_offline_at_open_are_kept_when_saving(project, monkeypatch):
    project.add_reference({"title": "Kept"})
    path = project.folder / "references.json"
    original = path.read_text(encoding="utf-8")
    real = Path.read_bytes
    offline = {path}

    def read_bytes(self):
        if self in offline:
            raise OSError(22, "The cloud file provider is not running", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    p = reopen(project)
    assert p.references == []
    offline.clear()                           # downloaded meanwhile
    p.add_reference({"title": "New"})
    backups = list(p.folder.glob("references.damaged-*.json"))
    assert [b.read_text(encoding="utf-8") for b in backups] == [original]
    p.close()


def test_undo_that_cannot_read_the_project_back_makes_it_read_only(project, monkeypatch):
    project.enable_undo()
    with project.undo_step("Add"):
        project.add_section("text", "A")
    real = Project.reload
    monkeypatch.setattr(Project, "reload", lambda self: (_ for _ in ()).throw(
        OSError(22, "The cloud file provider is not running")))
    with pytest.raises(ProjectError, match="read-only"):
        project.undo()
    assert project.read_only
    monkeypatch.setattr(Project, "reload", real)


def test_failed_reload_changes_nothing_in_memory(project, data, monkeypatch):
    project.add_compound(data.id, name="c")
    before = (project.settings, project.top_sections)
    monkeypatch.setattr(Project, "_load_references", lambda self: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        project.reload()
    assert (project.settings, project.top_sections) == before
    assert project.settings is before[0] and project.top_sections is before[1]


# ---------------------------------------------------------------- JSON of the wrong kind (project-7, profile-core-6)

@pytest.mark.parametrize("text", ["null", "[]", "5"])
def test_json_that_is_not_an_object_is_skipped(project, data, text):
    c = project.add_compound(data.id, name="c")
    k = project.add_scheme(data.id)
    text_section = project.add_section("text", "Notes")
    (c.folder / COMPOUND_FILE).write_text(text, encoding="utf-8")
    (k.folder / SCHEME_FILE).write_text(text, encoding="utf-8")
    (text_section.folder / SECTION_FILE).write_text(text, encoding="utf-8")
    p = reopen(project)
    assert len([w for w in p.load_warnings if "does not hold" in w]) == 3
    assert [s.name for s in p.top_sections] == ["Products"]
    p.close()


def test_project_json_that_is_not_an_object(project):
    (project.folder / "project.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ProjectError, match="project.json could not be read"):
        Project.open(project.folder)


def test_hand_edited_values_of_the_wrong_kind(project, data):
    from si_assistant.core import checks, dashboard, labels, links
    a = project.add_compound(data.id, name="A")
    b = project.add_compound(data.id, name="B")
    section_data = on_disk(data.folder / SECTION_FILE)
    section_data["id_table"] = [1, None, 3]
    section_data["default_fields"] = [{"key": "structure"}, "name"]
    (data.folder / SECTION_FILE).write_text(json.dumps(section_data), encoding="utf-8")
    a_data = on_disk(a.folder / COMPOUND_FILE)
    a_data["values"].update({"name": "Ethyl acetate", "hrms": "found 123.4", "nmr": {"x": 1}})
    a_data["fields"].append("yield")
    a_data["flags"] = ["oops"]
    (a.folder / COMPOUND_FILE).write_text(json.dumps(a_data), encoding="utf-8")
    b_data = on_disk(b.folder / COMPOUND_FILE)
    b_data["values"] = None
    (b.folder / COMPOUND_FILE).write_text(json.dumps(b_data), encoding="utf-8")

    p = reopen(project)
    report = p.id_report()
    assert p.section(data.id).id_table == ["1", "", "3"]
    assert report.display_id(a.id) == "1"
    pa = p.compound(a.id)
    assert pa.name == "Ethyl acetate"
    assert pa.values["hrms (could not be read)"] == "found 123.4"   # set aside, not lost
    marks = labels.compute(p)
    for compound in p.all_compounds():
        checks.active_warnings(p, compound, report, marks)
    checks.project_warnings(p, marks)
    dashboard.project_rows(p, report)
    list(links.all_texts(p))
    assert any("hrms" in w for w in p.load_warnings)
    p.save_compound(pa)   # what was set aside stays in the file
    assert on_disk(pa.folder / COMPOUND_FILE)["values"]["hrms (could not be read)"] \
        == "found 123.4"
    p.close()


def test_restoring_a_hand_edited_compound_from_the_trash(project, data):
    c = project.add_compound(data.id, name="A")
    entry = project.delete_compound(c.id)
    path = entry.folder / c.folder.name / COMPOUND_FILE
    d = on_disk(path)
    d["values"]["name"] = "renamed by hand"
    d["fields"] = "oops"
    path.write_text(json.dumps(d), encoding="utf-8")
    project.restore_from_trash(entry.id)
    assert project.compound(c.id).name == "renamed by hand"
    project.id_report()


# ---------------------------------------------------------------- duplicate IDs (profile-core-7)

def test_compound_folder_copied_into_another_section(project):
    a = project.add_section("data", "A")
    b = project.add_section("data", "B")
    project.set_id_table(a.id, ["1"])
    project.set_id_table(b.id, ["2"])
    c = project.add_compound(a.id, name="Alpha")
    shutil.copytree(c.folder, b.folder / "compounds" / c.folder.name)
    p = reopen(project)
    found = [x for x in p.all_compounds() if x.id == c.id]
    assert len(found) == 1
    assert sum("Two folders share the ID" in w for w in p.load_warnings) == 1
    kept = found[0].section_id
    expected = "1" if kept == a.id else "2"
    assert p.id_report().display_id(c.id) == expected
    p.close()


# ---------------------------------------------------------------- sync conflict copies (sync-4)

def test_sync_conflict_copies_are_reported(project, data):
    c = project.add_compound(data.id, name="c")
    project.add_field(c, {"key": "si_text"})
    project.write_compound_text(c, "si_text", "made-up text")
    project.add_compound(data.id, name="custom")
    p = reopen(project)
    assert p.load_warnings == []    # (no false alarm: program files alone)
    (c.folder / "compound-LAPTOP.json").write_text("{}", encoding="utf-8")
    (c.folder / "si_text (conflicted copy).txt").write_text("x", encoding="utf-8")
    (p.folder / "project-LAPTOP.json").write_text("{}", encoding="utf-8")
    (p.folder / "my-notes.txt").write_text("not a copy", encoding="utf-8")
    p = reopen(p)
    warning = [w for w in p.load_warnings if "two versions" in w]
    assert len(warning) == 1
    assert "compound-LAPTOP.json" in warning[0] and "si_text (conflicted copy).txt" in warning[0]
    assert "project-LAPTOP.json" in warning[0] and "my-notes" not in warning[0]
    p.close()


# ---------------------------------------------------------------- typed bond length (emf-svg-5)

@pytest.mark.parametrize("stored", [0.1, True, False, 500, float("nan"), float("inf"), -3,
                                    "5", 3.6, 0])
def test_typed_bond_length_shown_as_printed(project, data, stored):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO", picture=PNG)
    value = c.values["structure"]
    value[drawing_scale.OVERRIDE_KEY] = stored
    bond, typed = drawing_scale.picture_bond(value, None)
    own = project.structure_picture_bond(c)[1]
    assert own == ((bond or 0.0) if typed else None)


# ---------------------------------------------------------------- device names (ids)

@pytest.mark.parametrize("name, safe", [
    ("AUX.png", "AUX_.png"), ("con.tar.png", "con_.tar.png"), ("con .png", "con_ .png"),
    ("COM¹.pdf", "COM¹_.pdf"), ("lpt1", "lpt1_"), ("conin$.txt", "conin$_.txt"),
    ("conx.png", "conx.png"), ("nul data.png", "nul data.png"), ("clock$.png", "clock$.png"),
    ("com10.png", "com10.png")])
def test_windows_device_names_are_avoided(name, safe):
    assert safe_filename(name) == safe
    assert not is_device_name(safe)


def test_slugs_and_projects_avoid_device_names(tmp_path):
    assert slugify("AUX") == "aux_" and slugify("Con") == "con_"
    assert slugify("Conditions") == "conditions"
    p = Project.create(tmp_path, "nul")
    assert p.folder.name == "nul_" and p.settings["title"] == "nul"


# ================================================================ handoffs from other groups

# ---------------------------------------------------------------- main, data-safety-5

def test_no_save_re_creates_a_moved_project_folder(tmp_path):
    from si_assistant.core.project import ProjectMovedError
    p = Project.create(tmp_path, "Old name")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="c")
    old = p.folder
    old.rename(tmp_path / "New name")   # (renamed in Explorer while open)
    for save in (p.save_settings, lambda: p.save_section(s), lambda: p.save_compound(c),
                 lambda: p.write_compound_text(c, "notes", "made-up note")):
        with pytest.raises(ProjectMovedError, match="no longer at"):
            save()
    assert not old.exists()
    assert isinstance(ProjectMovedError("x"), ReadOnlyError)


# ---------------------------------------------------------------- checks, checks-dashboard-3

@pytest.mark.parametrize("stored, read", [([1, None, " 2b ", 3.5], ["1", "", "2b", "3.5"]),
                                          (None, []), ("missing", [])])
def test_a_hand_edited_id_table_is_read_as_text(project, stored, read):
    s = project.add_section("data", "Products")
    path = s.folder / SECTION_FILE
    raw = on_disk(path)
    if stored == "missing":
        raw.pop("id_table", None)
    else:
        raw["id_table"] = stored
    path.write_text(json.dumps(raw), encoding="utf-8")
    p = reopen(project)
    assert p.section(s.id).id_table == read
    assert p.load_warnings == []   # (no warning for numbers, null or no table)
    p.close()


# ---------------------------------------------------------------- chem, hunt-chemistry-numbers-verified-2

def test_a_generic_structure_is_kept_and_never_breaks_the_checks(project, data):
    # (Nathan's decision: accepted without a formula, with a warning; see test_decisions)
    from rdkit import Chem
    from si_assistant.core import checks
    c = project.add_compound(data.id, name="c")
    for text in ("*C", Chem.MolToMolBlock(Chem.MolFromSmiles("*CC"))):
        project.set_structure(c, text)
        assert project.structure_path(c).exists()
        assert project.read_structure(c) is None and project.generic_structure_atoms(c)
    # A structure.mol with an R atom (or a hand edit): no formula, so "no structure".
    project.structure_path(c).write_text(Chem.MolToMolBlock(Chem.MolFromSmiles("*CC")),
                                         encoding="utf-8")
    assert project.read_structure(c) is None and project.structure_info(c) is None
    checks.all_warnings(project, c, project.id_report())   # (does not raise)


def test_a_generic_structure_in_the_trash_is_restored(project, data):
    # (Nathan's decision: a generic structure is kept, so it can come back from the Trash)
    from rdkit import Chem
    c = project.add_compound(data.id, name="c")
    project.set_structure(c, "CCO")
    project.clear_structure(c)
    entry = project.list_trash()[0]
    item = entry.folder / entry.info["item"]
    item.write_text(Chem.MolToMolBlock(Chem.MolFromSmiles("*CC")), encoding="utf-8")
    project.restore_from_trash(entry.id)
    assert project.structure_path(c).exists()
    assert project.generic_structure_atoms(c) == ["R"]


# ---------------------------------------------------------------- files, data-safety-1

def test_a_failed_undo_leaves_memory_matching_the_files(project, data, monkeypatch):
    from si_assistant.core import undo
    c = project.add_compound(data.id, name="before")
    project.add_field(c, {"key": "si_text"})
    project.write_compound_text(c, "si_text", "old text")
    project.enable_undo()
    with project.undo_step("Edit"):
        project.write_compound_text(c, "si_text", "new text")
        c.values["name"] = {"text": "after"}
        project.save_compound(c)   # (newest: undone first)
    monkeypatch.setattr(undo, "_RENAME_DELAY_S", 0)
    real = undo.os.replace
    calls = {COMPOUND_FILE: 0}

    def replace(source, dest, *a, **kw):
        name = Path(dest).name
        if name == "si_text.txt":
            raise PermissionError(13, "open in another program (test)", str(dest))
        if name == COMPOUND_FILE:
            calls[name] += 1
            if calls[name] > 1:   # (the undo works, putting it back again does not)
                raise PermissionError(13, "open in another program (test)", str(dest))
        return real(source, dest, *a, **kw)

    monkeypatch.setattr(undo.os, "replace", replace)
    with pytest.raises(ProjectError):
        project.undo()
    monkeypatch.setattr(undo.os, "replace", real)
    assert calls[COMPOUND_FILE] > 1   # (the case where the files were left half-done)
    again = project.compound(c.id)
    assert again.values["name"] == on_disk(c.folder / COMPOUND_FILE)["values"]["name"]
    assert again.name == "before"


# ---------------------------------------------------------------- files, undo-files-4

def test_an_unreadable_lock_at_close_leaves_the_undo_history(project, data, monkeypatch):
    from si_assistant.core import lock as lockmod
    monkeypatch.setattr(lockmod, "_READ_DELAY_S", 0)
    journal = project.enable_undo()
    with project.undo_step("Add"):
        project.add_compound(data.id, name="c")
    assert journal.folder.is_dir()
    (project.folder / lockmod.LOCK_NAME).write_text("", encoding="utf-8")   # (half-synced)
    project.close()
    assert journal.folder.is_dir()   # (perhaps someone else's: removed at the next open)
    assert (project.folder / lockmod.LOCK_NAME).exists()


# ---------------------------------------------------------------- scheme, scheme-12

def test_adding_a_compound_can_leave_saving_the_section_to_the_caller(project, data):
    c = project.add_compound(data.id, name="c", save_owner=False)
    assert c in data.items and c.folder.is_dir()
    assert c.id not in on_disk(data.folder / SECTION_FILE).get("compound_order", [])
    project.save_section(data)
    assert c.id in on_disk(data.folder / SECTION_FILE)["compound_order"]


# ---------------------------------------------------------------- scheme, scheme-6

def test_deleting_a_scheme_compound_is_trash_first(project, data, blocked):
    k = project.add_scheme(data.id, name="Route")
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(project, k, product, "CC(O)c1ccccc1")
    c = se.link_new_compound(project, k, product, name="alcohol")
    files = k.folder / se.FILES_DIR

    def listed():
        return sorted(f.name for f in files.iterdir()) if files.is_dir() else []

    before = listed()
    blocked.add(c.folder)   # (a spectrum of it open in a PDF viewer)
    with pytest.raises(ProjectError, match="nothing was deleted"):
        project.delete_compound(c.id)
    assert se.find_box(k, product)[0].get("compound") == c.id
    assert on_disk(k.folder / SCHEME_FILE)["children"] == [c.id]
    assert listed() == before   # (no copies left behind)
    blocked.clear()
    entry = project.delete_compound(c.id)
    box = se.find_box(k, product)[0]
    assert "compound" not in box and k.children == []
    assert se.box_view(project, k, box).mol is not None   # (its copy, from the Trash entry)
    project.restore_from_trash(entry.id)
    assert [x.id for x in project.scheme(k.id).children] == [c.id]


# ---------------------------------------------------------------- hrms, hrms-8

def test_removing_a_listed_file_never_leaves_its_folder(project, data):
    c = project.add_compound(data.id, name="c")
    project.set_structure(c, "CCO")
    if "hrms" not in c.field_keys:
        project.add_field(c, {"key": "hrms"})
    hrms = c.values.setdefault("hrms", {})
    hrms.setdefault("reports", []).append({"file": "../structure.mol"})
    project.remove_hrms_report(c, "../structure.mol")
    assert (c.folder / "structure.mol").is_file()
    assert {"file": "../structure.mol"} in c.values["hrms"]["reports"]
    c.values.setdefault("private_files", []).append({"file": "../structure.mol"})
    project.remove_private_item(c, "../structure.mol")
    assert (c.folder / "structure.mol").is_file() and project.list_trash() == []


# ---------------------------------------------------------------- compound, hunt-undo-cross-feature-coherence-1

def test_the_found_mz_follows_the_structure_in_the_same_save(tmp_path):
    from tests import ms_samples as M
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="made-up ester")
    if "hrms" not in c.field_keys:
        p.add_field(c, {"key": "hrms"})
    report = tmp_path / "3a peaks.csv"
    report.write_text(M.csv_text(M.sample_peaks(1.2)), encoding="utf-8")
    p.add_hrms_report(c, report)
    assert not c.values["hrms"].get("found")   # (no structure: nothing to match)
    p.set_structure(c, M.SMILES)
    found = on_disk(c.folder / COMPOUND_FILE)["values"]["hrms"]["found"]
    assert found and found == c.values["hrms"]["found"]
    p.clear_structure(c)
    assert on_disk(c.folder / COMPOUND_FILE)["values"]["hrms"]["found"] == ""
    # A typed value is kept.
    c.values["hrms"]["found"] = "123.4567"
    p.save_compound(c)
    p.set_structure(c, M.SMILES)
    assert on_disk(c.folder / COMPOUND_FILE)["values"]["hrms"]["found"] == "123.4567"
    p.close()


# ---------------------------------------------------------------- compound, compound-page-7

def test_hand_edited_part_values_and_custom_fields(project, data):
    c = project.add_compound(data.id, name="c")
    project.add_field(c, {"key": "x_note", "custom": {"label": "Note", "type": "short_text"}})
    project.add_field(c, {"key": "x_temp", "custom": {"label": "T", "type": "number_unit"}})
    raw = on_disk(c.folder / COMPOUND_FILE)
    raw["values"].update({"name": {"text": None}, "x_note": "typed by hand",
                          "x_temp": [1, 2], "yield": {"percent": None, "mass": "12"}})
    (c.folder / COMPOUND_FILE).write_text(json.dumps(raw), encoding="utf-8")
    p = reopen(project)
    again = p.compound(c.id)
    assert again.name == ""   # (not "None")
    assert again.values["x_note"] == {"text": "typed by hand"}
    assert "x_temp" not in again.values and again.values["x_temp (could not be read)"] == [1, 2]
    assert again.values["yield"] == {"mass": "12"}   # (the part's default applies)
    assert sum("x_temp" in w for w in p.load_warnings) == 1
    p.close()


# ---------------------------------------------------------------- shell, app-shell-4

def test_a_hand_edited_trash_entry_lists_as_text(project, data):
    c = project.add_compound(data.id, name="c")
    entry = project.delete_compound(c.id)
    path = entry.folder / "entry.json"
    info = on_disk(path)
    info.update({"name": 42, "kind": ["compound"], "deleted_at": None, "deleted_by": 7})
    path.write_text(json.dumps(info), encoding="utf-8")
    listed = project.list_trash()[0]
    assert (listed.name, listed.kind, listed.deleted_at, listed.deleted_by) == ("42", "?", "", "")
    info["name"] = None
    path.write_text(json.dumps(info), encoding="utf-8")
    assert project.list_trash()[0].name == entry.id


# ---------------------------------------------------------------- review of the fixes above

def offline_files(monkeypatch, paths: set) -> set:
    """Files that raise OSError when read, as an online-only OneDrive file while offline."""
    real = Path.read_bytes

    def read_bytes(self):
        if self in paths:
            raise OSError(22, "The cloud file provider is not running", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return paths


def section_sorting_first(project, before):
    """A new Data section whose folder sorts before `before`'s (folder names are random)."""
    while True:
        s = project.add_section("data", "B")
        if s.folder.name < before.folder.name:
            return s
        project.delete_section(s.id)


def test_the_original_of_a_copied_compound_keeps_its_place(project):
    a = project.add_section("data", "A")
    b = section_sorting_first(project, a)
    project.set_id_table(a.id, ["1", "2", "3", "4", "5"])
    project.set_id_table(b.id, ["9", "10"])
    project.add_compound(a.id, name="c0")
    k = project.add_scheme(a.id, name="Route")
    k1 = project.add_compound(a.id, name="k1", scheme_id=k.id)
    project.add_compound(a.id, name="k2", scheme_id=k.id)
    c3 = project.add_compound(a.id, name="c3")
    ids = [project.id_report().display_id(c.id) for c in a.compounds]
    for c in (k1, c3):   # copied into B in File Explorer
        shutil.copytree(c.folder, b.folder / "compounds" / c.folder.name)

    p = reopen(project)
    warnings = [w for w in p.load_warnings if "Two folders share the ID" in w]
    assert len(warnings) == 2
    assert all("A ▸" in w.split("is shown")[0] and "B ▸" in w.split("is shown")[1]
               for w in warnings)
    pa = p.section(a.id)
    assert [c.name for c in pa.compounds] == ["c0", "k1", "k2", "c3"]
    assert p.section(b.id).items == []
    assert [p.id_report().display_id(c.id) for c in pa.compounds] == ids
    p.rename_scheme(k.id, "Route 2")      # ordinary edits and saves of both sections
    p.add_compound(a.id, name="c4")
    p.add_compound(b.id, name="b1")
    for c in (k1, c3):                    # the user deletes the copies
        shutil.rmtree(b.folder / "compounds" / c.folder.name)

    r = reopen(p)
    assert r.load_warnings == []
    ra = r.section(a.id)
    assert [c.name for c in ra.compounds] == ["c0", "k1", "k2", "c3", "c4"]
    assert [c.name for c in r.scheme(k.id).children] == ["k1", "k2"]
    assert [r.id_report().display_id(c.id) for c in ra.compounds][:4] == ids
    assert [c.name for c in r.section(b.id).compounds] == ["b1"]
    r.close()


def test_a_scheme_page_copied_with_its_compounds(project):
    a = project.add_section("data", "A")
    b = section_sorting_first(project, a)
    k = project.add_scheme(a.id, name="Route")
    k1 = project.add_compound(a.id, name="k1", scheme_id=k.id)
    k2 = project.add_compound(a.id, name="k2", scheme_id=k.id)
    for x in (k, k1, k2):
        shutil.copytree(x.folder, b.folder / "compounds" / x.folder.name)
    p = reopen(project)
    assert len([w for w in p.load_warnings if "Two folders share the ID" in w]) == 3
    assert p.section(a.id).items == [p.scheme(k.id)] and p.section(b.id).items == []
    assert [c.folder for c in p.scheme(k.id).children] == [k1.folder, k2.folder]
    p.close()


def test_front_json_offline_is_never_written_over(project, monkeypatch):
    from si_assistant.core import checks, labels
    owner = project.text_owner("general")
    figure = project.add_figure(owner, data=PNG, label="one")
    project.write_front_text("general", f"!figure{{{figure}}} A figure")
    path = project.front_folder / "front.json"
    before = path.read_bytes()
    pictures = sorted(p.name for p in (project.front_folder / "images").iterdir())
    offline = offline_files(monkeypatch, {path})
    p = reopen(project)
    warning = [w for w in p.load_warnings if "front.json" in w]
    assert len(warning) == 1 and "online-only" in warning[0] and "copy" not in warning[0]
    owner = p.text_owner("general")
    assert p.figures(owner) == {} and p.figure_path(owner, figure) is None
    labels.compute(p)                     # (no crash in the labels and checks)
    checks.project_warnings(p)
    with pytest.raises(ProjectError, match="can't be read just now"):
        p.add_figure(owner, data=PNG, label="two")
    with pytest.raises(ProjectError, match="can't be read just now"):
        p.remove_figure(owner, figure)
    offline.clear()
    assert path.read_bytes() == before
    assert sorted(x.name for x in (p.front_folder / "images").iterdir()) == pictures
    assert list(p.front_folder.glob("front.damaged-*.json")) == []
    p.close()


def test_failed_copy_with_read_only_files_leaves_nothing_behind(project, data, tmp_path,
                                                                monkeypatch):
    import stat
    c = project.add_compound(data.id)
    raw = tmp_path / "Sample.d"
    (raw / "AcqData").mkdir(parents=True)
    (raw / "AcqData" / "MSScan.bin").write_bytes(b"made up")

    def half_copy(source, dest, *args, **kwargs):   # instrument data is often read-only
        (Path(dest) / "AcqData").mkdir(parents=True)
        part = Path(dest) / "AcqData" / "part.bin"
        part.write_bytes(b"part")
        os.chmod(part, stat.S_IREAD)
        raise shutil.Error([(str(source), str(dest), "path too long")])

    monkeypatch.setattr(fileio.shutil, "copytree", half_copy)
    with pytest.raises(ProjectError, match="Could not copy 1 file") as error:
        project.add_hrms_report(c, raw)
    assert "by hand" not in str(error.value)
    assert list(project.hrms_report_dir(c).iterdir()) == []


def test_a_copy_that_cannot_be_taken_away_is_named(project, data, tmp_path, monkeypatch):
    c = project.add_compound(data.id)
    raw = tmp_path / "Sample.d"
    raw.mkdir()
    (raw / "MSScan.bin").write_bytes(b"made up")

    def half_copy(source, dest, *args, **kwargs):
        Path(dest).mkdir()
        (Path(dest) / "part.bin").write_bytes(b"part")
        raise shutil.Error([(str(source), str(dest), "path too long")])

    monkeypatch.setattr(fileio.shutil, "copytree", half_copy)
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: None)   # (e.g. in use)
    with pytest.raises(ProjectError, match="Please delete it by hand"):
        project.add_hrms_report(c, raw)


@pytest.mark.parametrize("text", ["[]", "null", "{damaged"])
def test_restoring_a_section_whose_file_was_edited_in_the_trash(project, text):
    notes = project.add_section("text", "Notes")
    entry = project.delete_section(notes.id)
    (entry.folder / notes.folder.name / SECTION_FILE).write_text(text, encoding="utf-8")
    if text.startswith("{"):
        with pytest.raises(ProjectError, match="could not be read"):
            project.restore_from_trash(entry.id)
        assert not notes.folder.exists()                 # (nothing moved)
        assert [e.id for e in project.list_trash()] == [entry.id]
    else:
        project.restore_from_trash(entry.id)
        assert [s.id for s in project.top_sections] == [notes.id]
        assert project.list_trash() == []


def test_restoring_an_incomplete_deletion_with_a_damaged_file(project):
    notes = project.add_section("text", "Notes")
    entry = project.delete_section(notes.id)
    (entry.folder / "entry.json").unlink()               # (as after a crash)
    (entry.folder / notes.folder.name / SECTION_FILE).write_text("{x", encoding="utf-8")
    incomplete = project.list_trash()[0]
    with pytest.raises(ProjectError, match="could not be read"):
        project.restore_from_trash(incomplete.id)
    assert not notes.folder.exists()


@pytest.mark.parametrize("action", ["move", "delete"])
def test_a_scheme_takes_its_unreadable_compounds_along(project, data, action):
    other = project.add_section("data", "Other")
    k = project.add_scheme(data.id, name="Route")
    k1 = project.add_compound(data.id, name="k1", scheme_id=k.id)
    project.add_compound(data.id, name="k2", scheme_id=k.id)
    good = damage(k1.folder / COMPOUND_FILE)
    p = reopen(project)
    if action == "move":
        p.move_scheme(k.id, other.id, 0)
        moved = other.folder / "compounds" / k1.folder.name
        assert not k1.folder.exists() and moved.is_dir()
        assert k1.id in on_disk(p.scheme(k.id).folder / SCHEME_FILE)["children"]
        (moved / COMPOUND_FILE).write_text(good, encoding="utf-8")
        here, empty = other.id, data.id
    else:
        entry = p.delete_scheme(k.id)
        assert not k1.folder.exists()
        message = p.restore_from_trash(entry.id)        # (still unreadable: keeps its place)
        assert "could not be read" in message
        assert [c.name for c in p.scheme(k.id).children] == ["k2"]
        p.add_compound(data.id, name="c")               # (an ordinary save)
        (k1.folder / COMPOUND_FILE).write_text(good, encoding="utf-8")
        here, empty = data.id, other.id
    r = reopen(p)
    assert r.load_warnings == []
    assert [c.name for c in r.scheme(k.id).children] == ["k1", "k2"]
    assert all(c.section_id == here for c in r.scheme(k.id).children)
    assert r.section(empty).items == []
    r.close()


@pytest.mark.parametrize("tree", [
    [{"id": ["x"]}],
    [{"id": {"x": 1}}, {"id": "REAL"}],
    [{"id": "REAL", "children": [{"id": ["y"]}]}],
])
def test_a_section_tree_with_ids_that_are_not_text(project, tree):
    notes = project.add_section("text", "Notes")
    path = project.folder / "project.json"
    d = on_disk(path)
    d["section_tree"] = json.loads(json.dumps(tree).replace('"REAL"', json.dumps(notes.id)))
    path.write_text(json.dumps(d), encoding="utf-8")
    p = reopen(project)
    assert [s.id for s in p.top_sections] == [notes.id]
    p.close()


def test_deleting_the_parent_of_an_unreadable_sub_section(project):
    project.add_section("text", "X")
    parent = project.add_section("text", "Parent")
    child = project.add_section("text", "Child", parent_id=parent.id)
    project.add_section("text", "Y")
    good = damage(child.folder / SECTION_FILE)
    p = reopen(project)
    entry = p.delete_section(parent.id)
    assert not child.folder.exists()                    # (in the Trash with its parent)
    with pytest.raises(ProjectError, match="could not be read"):
        p.restore_from_trash(entry.id)                  # (nothing moved yet)
    assert not parent.folder.exists()
    q = reopen(p)
    assert [s.name for s in q.top_sections] == ["X", "Y"]
    (entry.folder / child.folder.name / SECTION_FILE).write_text(good, encoding="utf-8")
    q.restore_from_trash(entry.id)                      # repaired in the Trash
    r = reopen(q)
    assert [(s.name, [c.name for c in s.children]) for s in r.top_sections] == \
        [("X", []), ("Parent", ["Child"]), ("Y", [])]
    r.close()


def test_restoring_a_structure_after_re_rendering_its_picture(project, data):
    c = project.add_compound(data.id)
    project.set_structure(c, "CCO", picture=PNG, vector=b"made-up EMF", vector_ext=".emf")
    project.clear_structure(c)
    picture = project.structure_image_path(c)
    project.replace_pasted_picture(picture, b"\x89PNG drawn again", ".png")   # Re-render
    project.restore_from_trash(project.list_trash()[0].id)
    assert project.structure_picture_source(c) == "chemdraw"
    assert project.structure_image_path(c).read_bytes() == b"\x89PNG drawn again"
    assert project.list_trash() == []


# ---------------------------------------------------------------- late handoffs (follow-up)

def _edit_entry(entry, **changes):
    path = entry.folder / "entry.json"
    info = on_disk(path)
    info.update(changes)
    path.write_text(json.dumps(info), encoding="utf-8")


def test_heteroatom_reports_skip_a_structure_without_a_formula(project, data, monkeypatch):
    from si_assistant.core import chem
    c = project.add_compound(data.id)
    project.set_structure(c, "CCF")

    def no_formula(*_a, **_k):
        raise chem.ChemError("This structure has a generic atom (*).")

    monkeypatch.setattr(project, "structure_info", no_formula)
    assert project.ensure_heteroatom_reports(c) == []   # (no error: the structure is stored)


def test_a_generic_atom_in_structure_mol_is_reported_not_hidden(project, data):
    from rdkit import Chem
    from si_assistant.core import chem
    c = project.add_compound(data.id)
    project.set_structure(c, "CCF")
    assert project.generic_structure_atoms(c) == []
    path = project.structure_path(c)
    path.write_text(chem.mol_to_block(Chem.MolFromSmiles("*CCF")), encoding="utf-8")
    os.utime(path, ns=(1, 1))   # (a different stamp, as after a hand edit)
    assert project.read_structure(c) is None            # (no formula or mass)
    assert project.generic_structure_atoms(c) == ["R"]   # (a * is saved as R in a MOL file)
    assert project.structure_info(c) is None
    project.clear_structure(c)
    assert project.generic_structure_atoms(c) == []


def test_restoring_a_general_procedure_whose_text_is_gone(project, data):
    gp = project.add_general_procedure(data.id, "GP A")
    entry = project.delete_general_procedure(gp["id"])
    (entry.folder / entry.info["item"]).unlink()
    with pytest.raises(ProjectError, match="nothing to restore"):
        project.restore_from_trash(entry.id)
    assert project.general_procedures(data) == []      # (no duplicate in memory)
    assert on_disk(data.folder / SECTION_FILE).get("general_procedures", []) == []


def test_restoring_a_general_procedure_that_cannot_be_moved_back(project, data, monkeypatch):
    gp = project.add_general_procedure(data.id, "GP A")
    project.write_gp_text(gp["id"], "Made-up text.")
    entry = project.delete_general_procedure(gp["id"])

    def locked(*_a, **_k):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(fileio, "move", locked)
    with pytest.raises(OSError):
        project.restore_from_trash(entry.id)
    assert project.general_procedures(data) == []
    monkeypatch.undo()
    project.restore_from_trash(entry.id)
    assert [g["name"] for g in project.general_procedures(data)] == ["GP A"]
    assert project.read_gp_text(project.general_procedures(data)[0]["id"]) == "Made-up text."


def test_a_hand_edited_position_in_the_trash_restores_at_the_end(project, data):
    a = project.add_compound(data.id, name="A")
    project.add_compound(data.id, name="B")
    project.add_compound(data.id, name="C")
    entry = project.delete_compound(a.id)
    _edit_entry(entry, original_index="first")
    project.restore_from_trash(entry.id)
    assert [c.name for c in data.items] == ["B", "C", "A"]
    x = project.add_section("text", "X")
    project.add_section("text", "Y")
    entry = project.delete_section(x.id)
    _edit_entry(entry, original_index=[0])
    project.restore_from_trash(entry.id)
    assert [s.name for s in project.top_sections][-2:] == ["Y", "X"]
    gp1 = project.add_general_procedure(data.id, "GP A")
    project.add_general_procedure(data.id, "GP B")
    entry = project.delete_general_procedure(gp1["id"])
    _edit_entry(entry, index=None, gp=["not", "a", "dict"])
    project.restore_from_trash(entry.id)
    assert len(project.general_procedures(data)) == 2
    assert project.general_procedures(data)[0]["name"] == "GP B"


@pytest.mark.parametrize("item", [5, None, "", "../sections", "a/b"])
def test_a_damaged_item_name_in_the_trash_is_refused(project, data, item):
    a = project.add_compound(data.id, name="A")
    entry = project.delete_compound(a.id)
    _edit_entry(entry, item=item)
    with pytest.raises(ProjectError, match="damaged"):
        project.restore_from_trash(entry.id)
    assert data.items == []
    assert (entry.folder / a.folder.name).is_dir()     # (still in the Trash)


def test_a_reference_change_that_cannot_be_saved_is_undone(project, monkeypatch):
    kept = project.add_reference({"title": "Made-up paper", "doi": "10.1000/xyz1"})
    import si_assistant.core.project as project_mod

    def locked(*_a, **_k):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(project_mod, "atomic_write_json", locked)
    before = [dict(r) for r in project.references]
    with pytest.raises(OSError):
        project.add_reference({"title": "Another", "doi": "10.1000/xyz2"})
    assert project.references == before
    with pytest.raises(OSError):
        project.update_reference({**kept, "title": "Changed"})
    assert project.references == before
    with pytest.raises(OSError):
        project.delete_reference(kept["id"])
    assert project.references == before


@pytest.mark.parametrize("reports", [5, "text", True, {"file": "x"}])
def test_an_hrms_report_is_added_after_a_hand_edited_report_list(project, data, tmp_path,
                                                                 reports):
    c = project.add_compound(data.id)
    c.values["hrms"] = {"reports": reports}
    source = tmp_path / "made-up report.txt"
    source.write_text("m/z 123.4567", encoding="utf-8")
    item = project.add_hrms_report(c, source)
    assert c.values["hrms"]["reports"] == [item]
    assert project.hrms_report_path(c, item["file"]).is_file()


@pytest.mark.parametrize("style", [["ACS"], {"a": 1}, 5, None])
def test_a_hand_edited_journal_style_reads_as_acs(project, style):
    path = project.folder / "project.json"
    raw = on_disk(path)
    raw["journal_style"] = style
    path.write_text(json.dumps(raw), encoding="utf-8")
    p = reopen(project)
    assert p.settings["journal_style"] == "ACS"
    p.close()


# ---------------------------------------------------------------- review of the late handoffs

def test_a_damaged_structure_is_kept_when_another_is_set(project, data):
    from rdkit import Chem
    from si_assistant.core import chem
    c = project.add_compound(data.id, name="aryl R")
    path = project.structure_path(c)
    old = chem.mol_to_block(Chem.MolFromSmiles("*c1ccccc1"))
    path.write_text(old, encoding="utf-8")
    assert project.read_structure(c) is None
    assert project.structure_for_drawing(c).GetNumAtoms() == 7   # (still drawn)
    # A generic structure (R) is an ordinary accepted structure now (Nathan's decision):
    # replacing it overwrites it, as for any structure (SPEC §13b.5; undo brings it back).
    project.set_structure(c, "CCN")
    assert project.list_trash() == []
    assert project.structure_for_drawing(c) is project.read_structure(c)
    # A damaged structure.mol is kept in the Trash; an ordinary one is simply replaced.
    path.write_text("garbage", encoding="utf-8")
    project.set_structure(c, "CCO")
    project.set_structure(c, "CCC")
    trash = project.list_trash()
    assert len(trash) == 1 and trash[0].info["field_key"] == "structure"
    assert (trash[0].folder / trash[0].info["item"]).read_text(encoding="utf-8") == "garbage"
    assert project.structure_info(c).formula == "C3H8"


def test_an_interrupted_scheme_deletion_can_be_restored(project, data, monkeypatch):
    k = project.add_scheme(data.id, name="Route")
    project.add_compound(data.id, name="A", scheme_id=k.id)
    project.add_compound(data.id, name="B", scheme_id=k.id)
    real = fileio.rename
    calls = []

    def crash(source, dest):   # the scheme folder and A went, then the computer stopped
        calls.append(source)
        if len(calls) == 3:
            raise SystemExit("crash (test)")
        real(source, dest)

    monkeypatch.setattr(fileio, "rename", crash)
    with pytest.raises(SystemExit):
        project.delete_scheme(k.id)
    monkeypatch.setattr(fileio, "rename", real)
    p = reopen(project)
    assert [c.name for c in p.section(data.id).compounds] == ["B"]   # (shown meanwhile)
    entry = p.list_trash()[0]
    assert entry.info.get("pending")
    p.restore_from_trash(entry.id)
    assert [type(i).__name__ for i in p.section(data.id).items] == ["SchemePage"]
    assert [c.name for c in p.scheme(k.id).children] == ["A", "B"]
    assert p.list_trash() == []
    r = reopen(p)
    assert [c.name for c in r.scheme(k.id).children] == ["A", "B"]
    assert [type(i).__name__ for i in r.section(data.id).items] == ["SchemePage"]
    r.close()


@pytest.mark.parametrize("companions", ["other", 5, [5], ["a/b"], "keep"])
def test_damaged_companion_names_in_the_trash_are_never_used(project, companions):
    keep = project.add_section("text", "Keep")
    x = project.add_section("text", "X")
    sub = project.add_section("text", "Sub", parent_id=x.id)
    other = project.delete_section(project.add_section("text", "Other").id)
    entry = project.delete_section(x.id)
    value = {"other": ["../" + other.id], "keep": [f"../../sections/{keep.folder.name}"]}
    _edit_entry(entry, companions=value.get(companions, companions)
                if isinstance(companions, str) else companions)
    message = project.restore_from_trash(entry.id)
    assert "still in the Trash" in message and sub.folder.name in message
    assert x.folder.is_dir() and keep.folder.is_dir()
    assert (project.trash_dir / other.id).is_dir()
    assert other.id in [e.id for e in project.list_trash()]
    assert not (project.folder / other.id).exists()


@pytest.mark.parametrize("children", [5, [5], ["../x"], None])
def test_damaged_child_names_of_a_trashed_scheme(project, data, children):
    k = project.add_scheme(data.id, name="Route")
    a = project.add_compound(data.id, name="A", scheme_id=k.id)
    entry = project.delete_scheme(k.id)
    _edit_entry(entry, children=children)
    message = project.restore_from_trash(entry.id)
    assert "still in the Trash" in message and a.folder.name in message
    assert project.scheme(k.id).children == []


@pytest.mark.parametrize("change", [{"field_key": None}, {"field_key": 5},
                                    {"item_values": 5}, {"item_values": {"note": 7}}])
def test_damaged_compound_file_descriptions_in_the_trash(project, data, tmp_path, change):
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"made up")
    project.add_private_item(c, raw, note="kept")
    project.remove_private_item(c, "raw.bin")
    entry = project.list_trash()[0]
    _edit_entry(entry, **change)
    if "field_key" in change:
        if change["field_key"] is None:   # (taken out altogether)
            info = on_disk(entry.folder / "entry.json")
            del info["field_key"]
            (entry.folder / "entry.json").write_text(json.dumps(info), encoding="utf-8")
        with pytest.raises(ProjectError, match="damaged"):
            project.restore_from_trash(entry.id)
        assert (entry.folder / "raw.bin").is_file()
    else:
        project.restore_from_trash(entry.id)
        assert c.values["private_files"] == [{"file": "raw.bin", "kind": "file", "note": ""}]


@pytest.mark.parametrize("object_id", [5, None, ["f-x"]])
def test_a_damaged_figure_id_in_the_trash_gets_a_new_one(project, object_id):
    owner = project.text_owner("general")
    figure = project.add_figure(owner, data=PNG, label="one")
    entry = project.remove_figure(owner, figure)
    _edit_entry(entry, object_id=object_id, label={"x": 1}, owner=7)
    with pytest.raises(ProjectError, match="no longer"):   # (its owner damaged too)
        project.restore_from_trash(entry.id)
    _edit_entry(entry, owner=owner.key)
    project.restore_from_trash(entry.id)
    figures = project.figures(owner)
    assert len(figures) == 1 and figure not in figures


@pytest.mark.parametrize("adder", ["hrms", "private"])
@pytest.mark.parametrize("from_trash", [True, False])
def test_a_file_whose_listing_cannot_be_saved_is_not_listed(project, data, tmp_path,
                                                           monkeypatch, adder, from_trash):
    import si_assistant.core.project as project_mod
    project.enable_undo()
    c = project.add_compound(data.id)
    project.add_field(c, {"key": "private_files"})
    report = tmp_path / "rep.txt"
    report.write_text("m/z 123.4567", encoding="utf-8")
    add = project.add_hrms_report if adder == "hrms" else project.add_private_item
    key = "hrms" if adder == "hrms" else "private_files"
    folder = project.hrms_report_dir(c) if adder == "hrms" else project.private_dir(c)
    if from_trash:
        add(c, report)
        (project.remove_hrms_report if adder == "hrms" else project.remove_private_item)(
            c, "rep.txt")
    before = json.loads(json.dumps(c.values.get(key)))
    saved = on_disk(c.folder / COMPOUND_FILE)["values"].get(key)
    real = project_mod.atomic_write_json

    def locked(path, *a, **k):
        if Path(path).name == COMPOUND_FILE:
            raise PermissionError(13, "open in another program (test)", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(project_mod, "atomic_write_json", locked)
    with pytest.raises(OSError):
        with project.undo_step("Add"):
            if from_trash:
                project.restore_from_trash(project.list_trash()[0].id)
            else:
                add(c, report)
    monkeypatch.setattr(project_mod, "atomic_write_json", real)
    assert c.values.get(key) == before                       # (memory matches the file)
    assert on_disk(c.folder / COMPOUND_FILE)["values"].get(key) == saved
    assert not (folder / "rep.txt").exists()                 # (no unlisted file)
    assert len(project.list_trash()) == (1 if from_trash else 0)
    if from_trash:
        project.restore_from_trash(project.list_trash()[0].id)   # (once, not twice)
        listed = project.hrms_reports(c) if adder == "hrms" else c.values["private_files"]
        assert [i["file"] for i in listed] == ["rep.txt"]


# ---------------------------------------------------------------- late round 2

def _lock_file(monkeypatch, name):
    """Saving `name` fails, as with a file open in another program."""
    import si_assistant.core.project as project_mod
    real = project_mod.atomic_write_json

    def locked(path, *a, **k):
        if Path(path).name == name:
            raise PermissionError(13, "open in another program (test)", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(project_mod, "atomic_write_json", locked)


@pytest.mark.parametrize("had_dismissed", [False, True])
def test_a_dismissal_that_cannot_be_saved_is_undone_in_memory(project, data, monkeypatch,
                                                              had_dismissed):
    from types import SimpleNamespace
    c = project.add_compound(data.id, name="A")
    old = SimpleNamespace(key="id:old", fingerprint="f0", message="Old warning")
    if had_dismissed:
        project.dismiss_warning(c, old)
    before = json.loads(json.dumps(c.data.get("dismissed")))
    _lock_file(monkeypatch, COMPOUND_FILE)
    new = SimpleNamespace(key="hrms:ppm", fingerprint="f1", message="HRMS off")
    with pytest.raises(OSError):
        project.dismiss_warning(c, new)
    assert c.data.get("dismissed") == before
    assert ("dismissed" in c.data) == had_dismissed
    if had_dismissed:
        with pytest.raises(OSError):
            project.restore_warning(c, "id:old")
        assert c.data.get("dismissed") == before
    assert on_disk(c.folder / COMPOUND_FILE).get("dismissed") == before


@pytest.mark.parametrize("in_scheme", [False, True])
def test_a_new_compound_whose_owner_cannot_be_saved_leaves_nothing(project, data, monkeypatch,
                                                                   in_scheme):
    scheme = project.add_scheme(data.id, name="Route") if in_scheme else None
    container = scheme.children if scheme else data.items
    project.add_compound(data.id, name="A", scheme_id=scheme.id if scheme else None)
    before = list(container)
    folders = set((data.folder / "compounds").iterdir())
    _lock_file(monkeypatch, SCHEME_FILE if in_scheme else SECTION_FILE)
    with pytest.raises(OSError):
        project.add_compound(data.id, name="B", scheme_id=scheme.id if scheme else None)
    assert container == before
    assert set((data.folder / "compounds").iterdir()) == folders


def test_a_scheme_name_or_label_that_cannot_be_saved_is_undone(project, data, monkeypatch):
    scheme = project.add_scheme(data.id, name="Route")
    project.set_scheme_label(scheme.id, "Scheme S9")
    before = dict(scheme.data)
    _lock_file(monkeypatch, SCHEME_FILE)
    with pytest.raises(OSError):
        project.rename_scheme(scheme.id, "Other route")
    with pytest.raises(OSError):
        project.set_scheme_label(scheme.id, "")
    with pytest.raises(OSError):
        project.set_scheme_label(scheme.id, "Scheme S1")
    assert scheme.data == before
    monkeypatch.undo()
    project.set_scheme_label(scheme.id, "")
    assert "label" not in scheme.data and "label" not in on_disk(scheme.folder / SCHEME_FILE)
    with monkeypatch.context() as m:
        _lock_file(m, SCHEME_FILE)
        with pytest.raises(OSError):
            project.set_scheme_label(scheme.id, "Scheme S1")
    assert "label" not in scheme.data



def _hold_file(monkeypatch, name):
    """`name` is open in another program: replacing it fails, but (as for real) saving
    the same contents again writes nothing, so it does not fail."""
    real = fileio.atomic_write_bytes

    def held(path, data):
        if Path(path).name == name and not fileio.same_contents(Path(path), data):
            raise PermissionError(13, "open in another program (test)", str(path))
        return real(path, data)

    monkeypatch.setattr(fileio, "atomic_write_bytes", held)


@pytest.mark.parametrize("in_scheme", [False, True])
def test_a_delete_whose_owner_cannot_be_saved_moves_nothing(project, data, monkeypatch,
                                                            in_scheme):
    scheme = project.add_scheme(data.id, name="Route") if in_scheme else None
    container = scheme.children if scheme else data.items
    owner = scheme or data
    owner_file = owner.folder / (SCHEME_FILE if in_scheme else SECTION_FILE)
    c = project.add_compound(data.id, name="A", scheme_id=scheme.id if scheme else None)
    before, data_before = owner_file.read_bytes(), json.loads(json.dumps(owner.data))
    project.enable_undo()
    _hold_file(monkeypatch, owner_file.name)
    with pytest.raises(OSError):
        with project.undo_step("Delete"):
            project.delete_compound(c.id)
    assert container[-1] is c and c.folder.is_dir()
    assert project.list_trash() == []
    assert owner_file.read_bytes() == before and owner.data == data_before
    assert project.journal.undo_stack == []
    monkeypatch.undo()
    project.delete_compound(c.id)   # (closed again: the delete works)
    assert c not in container and len(project.list_trash()) == 1


@pytest.mark.parametrize("in_scheme", [False, True])
def test_a_delete_whose_move_fails_puts_the_owner_file_back(project, data, blocked,
                                                            in_scheme):
    scheme = project.add_scheme(data.id, name="Route") if in_scheme else None
    owner = scheme or data
    owner_file = owner.folder / (SCHEME_FILE if in_scheme else SECTION_FILE)
    c = project.add_compound(data.id, name="A", scheme_id=scheme.id if scheme else None)
    owner.data["general_procedures" if not in_scheme else "note"] = []   # (not saved yet)
    before, data_before = owner_file.read_bytes(), json.loads(json.dumps(owner.data))
    project.enable_undo()
    blocked.add(c.folder)
    with pytest.raises(ProjectError, match="nothing was deleted"):
        with project.undo_step("Delete"):
            project.delete_compound(c.id)
    assert c in (scheme.children if scheme else data.items) and c.folder.is_dir()
    assert owner_file.read_bytes() == before and owner.data == data_before
    assert project.list_trash() == [] and project.journal.undo_stack == []
