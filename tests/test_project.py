import json
import zipfile

import pytest

from si_assistant.core.project import (Project, ProjectError, ReadOnlyError, SECTION_FILE,
                                       SECTIONS_DIR)


def names(p):
    return [s.name for s in p.sections]


def test_create_makes_expected_layout(tmp_path):
    p = Project.create(tmp_path, "My SI")
    assert (p.folder / "project.json").is_file()
    assert (p.folder / "references.json").is_file()
    assert (p.folder / "sections").is_dir()
    assert p.settings["title"] == "My SI"
    assert p.settings["hrms_tolerance_ppm"] == 5.0


def test_create_refuses_non_empty_folder(tmp_path):
    (tmp_path / "My SI").mkdir()
    (tmp_path / "My SI" / "something.txt").write_text("x")
    with pytest.raises(ProjectError):
        Project.create(tmp_path, "My SI")


def test_open_non_project_folder_fails(tmp_path):
    with pytest.raises(ProjectError):
        Project.open(tmp_path)


def test_sections_and_order_persist(project):
    a = project.add_section("text", "General Information")
    b = project.add_section("file", "Optimization")
    c = project.add_section("data", "Products")
    project.move_section(c.id, 0)
    assert names(project) == ["Products", "General Information", "Optimization"]
    reopened = Project.open(project.folder)
    assert [s.id for s in reopened.sections] == [c.id, a.id, b.id]
    assert [s.type for s in reopened.sections] == ["data", "text", "file"]


def test_add_section_at_index(project):
    project.add_section("text", "A")
    project.add_section("text", "C")
    project.add_section("text", "B", index=1)
    assert names(project) == ["A", "B", "C"]


def test_rename_keeps_folder(project):
    s = project.add_section("text", "General Information")
    folder = s.folder
    project.rename_section(s.id, "Materials and Methods")
    assert s.folder == folder and folder.exists()
    assert Project.open(project.folder).sections[0].name == "Materials and Methods"


def test_rename_to_empty_rejected(project):
    s = project.add_section("text", "A")
    with pytest.raises(ProjectError):
        project.rename_section(s.id, "   ")


def test_set_order_must_match_existing(project):
    a = project.add_section("text", "A")
    with pytest.raises(ValueError):
        project.set_order([a.id, "s-000000"])


def test_folders_missing_from_order_are_appended(project):
    a = project.add_section("text", "A")
    b = project.add_section("text", "B")
    # Simulate a sync that dropped a from the saved tree.
    data = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    data["section_tree"] = [{"id": b.id}, {"id": "s-gone00"}]
    (project.folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    assert [s.id for s in Project.open(project.folder).sections] == [b.id, a.id]


def test_text_content_roundtrip(project):
    s = project.add_section("text", "General")
    project.write_section_text(s, "**General.** Reactions in CDCl_{3}.")
    assert Project.open(project.folder).read_section_text(s) == "**General.** Reactions in CDCl_{3}."


def test_delete_goes_to_trash_and_restores_in_place(project):
    a = project.add_section("text", "A")
    b = project.add_section("text", "B")
    c = project.add_section("text", "C")
    project.write_section_text(b, "keep me")
    project.delete_section(b.id)
    assert names(project) == ["A", "C"]
    assert not b.folder.exists()
    trash = project.list_trash()
    assert len(trash) == 1 and trash[0].kind == "section" and trash[0].deleted_by == "Tester"
    project.restore_from_trash(trash[0].id)
    assert names(project) == ["A", "B", "C"]
    assert project.list_trash() == []
    restored = Project.open(project.folder)
    assert names(restored) == ["A", "B", "C"]
    assert restored.read_section_text(restored.section(b.id)) == "keep me"


def test_file_section_attach_replace_remove_restore(project, tmp_path):
    src1 = tmp_path / "table.pdf"
    src1.write_bytes(b"%PDF-1.4 one")
    src2 = tmp_path / "table v2.png"
    src2.write_bytes(b"png two")
    s = project.add_section("file", "Optimization")

    project.attach_file(s, src1)
    assert project.section_file_path(s).read_bytes() == b"%PDF-1.4 one"
    assert src1.exists()  # original untouched

    project.attach_file(s, src2)  # replace: old file goes to Trash
    assert project.section_file_path(s).name == "table v2.png"
    assert not (s.folder / "table.pdf").exists()
    assert [e.name for e in project.list_trash()] == ["table.pdf"]

    project.remove_file(s)
    assert project.section_file_path(s) is None
    assert Project.open(project.folder).sections[0].data["file"] is None

    entry = next(e for e in project.list_trash() if e.name == "table.pdf")
    project.restore_from_trash(entry.id)
    assert project.section_file_path(s).read_bytes() == b"%PDF-1.4 one"


def test_attach_rejects_unsupported_type(project, tmp_path):
    src = tmp_path / "notes.docx"
    src.write_bytes(b"x")
    s = project.add_section("file", "F")
    with pytest.raises(ProjectError):
        project.attach_file(s, src)


def test_attached_file_cannot_clobber_section_json(project, tmp_path):
    src = tmp_path / "section.json.pdf"
    src.write_bytes(b"x")
    s = project.add_section("file", "F")
    project.attach_file(s, src)
    assert (s.folder / SECTION_FILE).is_file()
    assert json.loads((s.folder / SECTION_FILE).read_text(encoding="utf-8"))["type"] == "file"


def test_read_only_blocks_writes(project):
    s = project.add_section("text", "A")
    ro = Project.open(project.folder, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.add_section("text", "B")
    with pytest.raises(ReadOnlyError):
        ro.write_section_text(ro.sections[0], "x")
    with pytest.raises(ReadOnlyError):
        ro.delete_section(s.id)


def test_lock_takeover_detected(project):
    assert project.lock_still_ours()
    other = Project.open(project.folder)
    other.acquire_lock("Alex")
    assert not project.lock_still_ours()
    project.close()  # must not remove Alex's lock
    assert other.current_lock().user == "Alex"
    other.close()
    assert other.current_lock() is None


def test_snapshot_excludes_build_lock_and_old_snapshots(project):
    project.add_section("text", "A")
    (project.folder / "build").mkdir()
    (project.folder / "build" / "SI.pdf").write_bytes(b"x")
    first = project.save_snapshot("submitted version")
    assert first.name.endswith("_submitted-version.zip")
    second = project.save_snapshot()
    with zipfile.ZipFile(second) as zf:
        members = zf.namelist()
    assert "Test SI/project.json" in members
    assert any(m.startswith(f"Test SI/{SECTIONS_DIR}/") for m in members)
    assert not any("/build/" in m or m.endswith(".lock") or "/snapshots/" in m for m in members)


def test_unknown_settings_keys_survive_save(project):
    data = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    data["future_setting"] = 42
    (project.folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    p = Project.open(project.folder)
    p.save_settings()
    assert Project.open(project.folder).settings["future_setting"] == 42
