"""Undo / redo of saved changes (SPEC §13k.11): core/undo.py and the Project around it."""

import json
import os

import pytest

from si_assistant.core import fileio, undo
from si_assistant.core.project import Project, ProjectError


@pytest.fixture
def p(project):
    project.enable_undo()
    return project


def compound_json(c):
    return json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))


def files_of(folder):
    """Every file under `folder` (except build/ and the lock) with its contents."""
    out = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if rel.parts[0] in ("build", ".lock") or not path.is_file():
            continue
        out[rel.as_posix()] = path.read_bytes()
    return out


def test_a_compound_edit_is_undone_and_redone(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    with p.undo_step("Edit 1 ▸ Name"):
        c.values["name"]["text"] = "Ethyl ester"
        p.save_compound(c)
    assert p.can_undo and p.journal.undo_label == "Edit 1 ▸ Name"
    step = p.undo()
    assert step.label == "Edit 1 ▸ Name"
    assert p.compound(c.id).name == "Ester"            # reloaded from the files
    assert compound_json(c)["values"]["name"]["text"] == "Ester"
    assert p.can_redo and p.journal.redo_label == "Edit 1 ▸ Name"
    p.redo()
    assert p.compound(c.id).name == "Ethyl ester"
    assert p.can_undo and not p.can_redo


def test_delete_and_undo_brings_back_the_folder_and_ids(p):
    s = p.add_section("data", "Products")
    p.set_id_table(s.id, ["1", "2", "3"])
    a, b, c = (p.add_compound(s.id, name=n) for n in ("A", "B", "C"))
    p.write_compound_text(b, "procedure", "Stirred overnight.")
    before = files_of(p.folder)
    with p.undo_step("Delete compound 2", where=[("compound", b.id)]):
        p.delete_compound(b.id)
    assert not b.folder.exists() and p.list_trash()
    assert p.id_report().display_id(c.id) == "2"
    step = p.undo()
    assert step.where == [("compound", b.id)]
    assert b.folder.is_dir() and not p.list_trash()
    assert files_of(p.folder) == before            # exactly as before, Trash entry gone
    assert [x.id for x in p.section(s.id).compounds] == [a.id, b.id, c.id]
    assert p.id_report().display_id(c.id) == "3"
    assert p.read_compound_text(p.compound(b.id), "procedure") == "Stirred overnight."
    p.redo()
    assert not b.folder.exists() and len(p.list_trash()) == 1
    with pytest.raises(KeyError):
        p.compound(b.id)


def test_moving_a_compound_between_sections_is_undone(p):
    s1, s2 = p.add_section("data", "One"), p.add_section("data", "Two")
    c = p.add_compound(s1.id, name="Mover")
    old_folder = c.folder
    with p.undo_step("Move Mover"):
        p.move_compound(c.id, s2.id, 0)
    assert not old_folder.exists()
    p.undo()
    assert old_folder.is_dir()
    assert [x.id for x in p.section(s1.id).compounds] == [c.id]
    assert p.section(s2.id).compounds == []
    p.redo()
    assert p.compound(c.id).section_id == s2.id


def test_a_section_with_sub_sections_is_deleted_and_undone(p):
    methods = p.add_section("text", "Methods")
    inner = p.add_section("text", "Inner", parent_id=methods.id)
    data = p.add_section("data", "Data", parent_id=inner.id)
    p.add_compound(data.id, name="X")
    p.write_section_text(inner, "Some text.")
    with p.undo_step("Delete section “Methods”"):
        p.delete_section(methods.id)
    assert [s.name for s in p.sections] == []
    p.undo()
    assert [(p.depth(s), s.name) for s in p.sections] == [(1, "Methods"), (2, "Inner"),
                                                           (3, "Data")]
    assert p.read_section_text(p.section(inner.id)) == "Some text."
    assert p.list_trash() == []


def test_settings_and_references_are_undone(p):
    with p.undo_step("Change project settings"):
        p.settings["journal_style"] = "RSC"
        p.save_settings()
    with p.undo_step("Edit references"):
        p.add_reference({"id": "r1", "type": "article", "title": "A paper", "doi": "10.1/x"})
    p.undo()
    assert p.references == []
    assert p.settings["journal_style"] == "RSC"
    p.undo()
    assert p.settings["journal_style"] == "ACS"
    assert not p.can_undo


def test_autosaves_of_the_same_page_merge(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id)
    p.journal.break_merge()
    context = undo.UndoContext(("compound", c.id, "yield"), lambda: "Edit 1 ▸ Yield",
                               [("compound", c.id)])
    p.journal.context = lambda: context
    n = len(p.journal.undo_stack)
    before = compound_json(c)["values"].get("yield")
    for text in ("8", "85", "85%"):   # typing, saved every 800 ms
        c.values["yield"] = {"text": text}
        with p.autosave():
            p.save_compound(c)
    assert len(p.journal.undo_stack) == n + 1
    assert p.journal.undo_label == "Edit 1 ▸ Yield"
    # Another field: a step of its own.
    context = undo.UndoContext(("compound", c.id, "name"), "Edit 1 ▸ Name")
    c.values["name"] = {"text": "Ester"}
    with p.autosave():
        p.save_compound(c)
    assert len(p.journal.undo_stack) == n + 2
    p.undo()
    assert compound_json(c)["values"]["yield"] == {"text": "85%"}
    p.undo()   # the whole typing in one go
    assert compound_json(c)["values"].get("yield") == before
    # Saving the same thing again is not a change (no new step, redo is kept).
    p.save_compound(p.compound(c.id))
    assert p.can_redo


def test_a_save_after_an_undo_does_not_merge_into_an_older_step(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id)
    key = ("compound", c.id, "yield")
    p.journal.context = lambda: undo.UndoContext(key, "Edit ▸ Yield")
    c.values["yield"] = {"text": "1"}
    with p.autosave():
        p.save_compound(c)
    p.journal.break_merge()   # (another page was opened in between)
    c.values["yield"] = {"text": "2"}
    with p.autosave():
        p.save_compound(c)
    p.undo()
    c = p.compound(c.id)
    c.values["yield"] = {"text": "3"}
    with p.autosave():
        p.save_compound(c)
    p.undo()   # only the "3": the "1" is a step of its own
    assert compound_json(c)["values"]["yield"] == {"text": "1"}


def test_a_new_change_clears_redo(p):
    s = p.add_section("text", "Notes")
    with p.undo_step("Rename"):
        p.rename_section(s.id, "Notes 2")
    p.undo()
    assert p.can_redo
    with p.undo_step("Edit"):
        p.write_section_text(p.section(s.id), "New text")
    assert not p.can_redo
    assert p.journal.undo_label == "Edit"


def test_the_history_keeps_fifty_steps(p):
    s = p.add_section("text", "Notes")
    for i in range(60):
        with p.undo_step(f"Edit {i}"):
            p.write_section_text(s, f"Version {i}")
    assert len(p.journal.undo_stack) == undo.MAX_STEPS
    assert p.journal.undo_label == "Edit 59"
    folders = [d for d in p.journal.folder.iterdir()]
    assert len(folders) == undo.MAX_STEPS   # old copies are removed too
    for _ in range(undo.MAX_STEPS):
        p.undo()
    assert not p.can_undo
    assert p.read_section_text(p.section(s.id)) == "Version 9"


def test_large_files_are_copied_to_disk_not_kept_in_memory(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id)
    big = p.folder.parent / "spectrum.pdf"
    big.write_bytes(b"%PDF-1.4\n" + os.urandom(3_000_000))
    item = p.attach_compound_file(c, "spectra", big)
    path = p.attachment_path(c, "spectra", item["file"])
    with p.undo_step("Re-render"):
        fileio.atomic_write_bytes(path, b"%PDF-1.4 replaced")
    step = p.journal.undo_stack[-1]
    copies = [r for r in step.records if r[0] == "file"]
    assert copies and all(isinstance(r[2], type(path)) for r in copies)   # paths, no bytes
    assert copies[0][2].stat().st_size == big.stat().st_size
    assert copies[0][2].is_relative_to(p.folder / "build" / "undo")
    p.undo()
    assert path.read_bytes() == big.read_bytes()


def test_emptying_the_trash_can_be_undone(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Gone")
    p.delete_compound(c.id)
    with p.undo_step("Empty the Trash"):
        p.empty_trash()
    assert p.list_trash() == []
    p.undo()
    assert [e.name for e in p.list_trash()] == ["Gone"]
    p.restore_from_trash(p.list_trash()[0].id)
    assert p.compound(c.id).name == "Gone"


def test_restoring_from_the_trash_is_undone(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Back")
    p.delete_compound(c.id)
    entry = p.list_trash()[0]
    with p.undo_step("Restore “Back” from the Trash"):
        p.restore_from_trash(entry.id)
    p.undo()
    assert [e.name for e in p.list_trash()] == ["Back"]
    assert p.section(s.id).compounds == []


def test_a_failed_undo_leaves_the_project_as_it_was(p, monkeypatch):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Locked")
    p.write_compound_text(c, "procedure", "Text")
    with p.undo_step("Delete compound"):
        p.delete_compound(c.id)
    before = files_of(p.folder)
    real = undo._rename
    calls = []

    def flaky(source, target):
        calls.append(source)
        if len(calls) == 2:   # the second rename fails (a file open elsewhere)
            raise PermissionError(13, "Access is denied", str(source))
        real(source, target)

    monkeypatch.setattr(undo, "_rename", flaky)
    with pytest.raises(ProjectError, match="could not be undone, so nothing was changed"):
        p.undo()
    monkeypatch.setattr(undo, "_rename", real)
    assert files_of(p.folder) == before
    assert p.can_undo and p.journal.undo_label == "Delete compound"
    p.undo()   # works once the file is free
    assert p.compound(c.id).name == "Locked"


def test_the_history_is_cleared_on_close_and_open(project):
    project.enable_undo()
    with project.undo_step("Add"):
        project.add_section("text", "A")
    folder = project.journal.folder
    assert folder.exists()
    project.close()
    assert not folder.exists()
    again = Project.open(project.folder)
    again.acquire_lock("Tester")
    (folder / "00001").mkdir(parents=True)   # left over from a crash
    again.enable_undo()
    assert not folder.exists() and not again.can_undo
    again.close()


def test_read_only_projects_have_no_undo(project):
    ro = Project.open(project.folder, read_only=True)
    assert ro.enable_undo() is None and not ro.can_undo
    with ro.undo_step("Nothing"):
        pass
    with pytest.raises(ProjectError):
        ro.undo()


def test_build_and_snapshots_are_not_part_of_the_history(p):
    (p.folder / "build").mkdir(exist_ok=True)
    fileio.atomic_write_text(p.folder / "build" / "x.typ", "generated")
    p.save_snapshot("v1")
    assert not p.can_undo
    assert not p.journal.claims(p.folder / ".lock")
    assert p.journal.claims(p.folder / "trash" / "x")


def test_pictures_and_their_originals_are_undone(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id)
    p.set_structure(c, "CCO")
    with p.undo_step("Paste structure picture"):
        p.set_structure_image(c, data=b"\x89PNG fake", ext=".png", vector=b"EMF", vector_ext=".emf")
    assert (c.folder / "structure_image.emf").exists()
    p.undo()
    assert not (c.folder / "structure_image.png").exists()
    assert not (c.folder / "structure_image.emf").exists()
    assert p.structure_image_path(p.compound(c.id)) is None
    p.redo()
    assert p.structure_image_path(p.compound(c.id)).read_bytes() == b"\x89PNG fake"


def _typing(p, c, key, text):
    """An autosave of typing in field `key` of compound `c`."""
    p.journal.context = lambda: undo.UndoContext(("compound", c.id, key), f"Edit ▸ {key}")
    c.values[key] = {"text": text}
    with p.autosave():
        p.save_compound(c)


def test_an_action_between_autosaves_is_a_step_of_its_own(p):
    """Typing, then an action on the same page (no command, no step), then more typing:
    three steps. Undoing the action leaves the typing before and after it alone."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id)
    p.journal.break_merge()
    n = len(p.journal.undo_stack)
    _typing(p, c, "yield", "85%")
    p.set_structure(c, "CCO")          # e.g. a structure pasted: an action of its own
    p.write_compound_text(c, "procedure", "Stirred.")   # (the same action, until it ends)
    p.journal.break_merge()           # (the window: the click has been handled)
    p.set_structure(c, "CCCO")        # another action: a step of its own
    p.journal.break_merge()
    _typing(p, c, "yield", "90%")      # typing again: not merged with the typing before
    assert len(p.journal.undo_stack) == n + 4
    p.undo()                          # the typing after
    assert compound_json(c)["values"]["yield"] == {"text": "85%"}
    p.undo()                          # the second structure only
    assert p.structure_info(p.compound(c.id)).smiles == "CCO"
    p.undo()                          # the first action: structure and procedure together
    c2 = p.compound(c.id)
    assert p.read_structure(c2) is None
    assert p.read_compound_text(c2, "procedure") == ""
    assert compound_json(c)["values"]["yield"] == {"text": "85%"}   # typing kept


def test_an_action_ends_when_the_window_says_so(p):
    """The window ends an action once its event has been handled (end_action)."""
    pending = []
    p.journal.end_action = pending.append
    s = p.add_section("text", "Notes")
    p.journal.break_merge()
    n = len(p.journal.undo_stack)
    p.write_section_text(s, "One")
    p.write_section_text(s, "One two")   # the same click: one step
    assert len(p.journal.undo_stack) == n + 1
    for done in pending:
        done()
    p.write_section_text(s, "Three")     # a new click
    assert len(p.journal.undo_stack) == n + 2


def test_then_merge_takes_only_the_next_autosave(p):
    """A figure removed (then_merge): the text's next autosave joins it; typing saved
    after that is a step of its own."""
    s = p.add_section("text", "Notes")
    p.write_section_text(s, "Before.")
    p.journal.context = lambda: undo.UndoContext(("section", s.id), "Edit section")
    p.journal.break_merge()
    n = len(p.journal.undo_stack)
    with p.undo_step("Remove Figure S1", then_merge=True):
        p.add_section("text", "Stand-in for the picture going to the Trash")
    with p.autosave():
        p.write_section_text(s, "Before, without the figure.")
    with p.autosave():
        p.write_section_text(s, "Before, without the figure. Typed later.")
    assert len(p.journal.undo_stack) == n + 2
    assert p.journal.undo_label == "Edit section"
    p.undo()
    assert p.read_section_text(p.section(s.id)) == "Before, without the figure."
    p.undo()
    assert p.read_section_text(p.section(s.id)) == "Before."


def test_typing_undone_in_its_box_leaves_no_empty_step(p):
    """Typing autosaved, then undone in its text box and autosaved again: the step
    changes nothing any more, so it is dropped (Edit ▸ Undo then goes further back)."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    with p.undo_step("Change project settings"):
        p.settings["hrms_tolerance_ppm"] = 3.0
        p.save_settings()
    n = len(p.journal.undo_stack)
    before = files_of(p.folder)
    _typing(p, c, "name", "Ester XYZ")
    assert p.journal.undo_label == "Edit ▸ name" and len(p.journal.undo_stack) == n + 1
    _typing(p, c, "name", "Ester")   # (the box's own Ctrl+Z, then its autosave)
    assert files_of(p.folder) == before
    assert p.journal.undo_label == "Change project settings"
    assert len(p.journal.undo_stack) == n
    assert len(list(p.journal.folder.iterdir())) == n   # its copies are gone too


def test_a_failed_command_that_changed_nothing_is_not_a_step(p, monkeypatch):
    """A delete that could not move the folder to the Trash puts everything back: no
    step is added, and what could be redone is kept."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Open elsewhere")
    with p.undo_step("Rename"):
        p.rename_section(s.id, "Products 2")
    p.undo()
    assert p.can_redo
    n = len(p.journal.undo_stack)

    def refuse(source, dest):
        raise PermissionError(13, "Access is denied", str(source))

    monkeypatch.setattr(fileio, "rename", refuse)
    with p.undo_step("Delete compound"):
        with pytest.raises(ProjectError, match="nothing was deleted"):
            p.delete_compound(c.id)
    assert len(p.journal.undo_stack) == n and p.can_redo
    # A command that fails part-way through and leaves a change behind is still a step.
    monkeypatch.undo()
    with pytest.raises(RuntimeError):
        with p.undo_step("Half done"):
            p.write_section_text(p.section(s.id), "Changed")
            raise RuntimeError("stopped")
    assert p.journal.undo_label == "Half done"


def test_someone_else_taking_over_keeps_their_history(project):
    """The first window loses the lock: closing it must not delete build/undo/, which now
    holds the new editor's history."""
    project.enable_undo()
    bob = Project.open(project.folder)
    bob.acquire_lock("Bob")           # takes over (the lock file is now Bob's)
    bob.enable_undo()
    s = bob.add_section("text", "Bob's")
    with bob.undo_step("Delete section"):
        bob.delete_section(s.id)
    with bob.undo_step("Empty the Trash"):
        bob.empty_trash()
    project.close()                   # Alice's window closes
    assert bob.journal.folder.is_dir()
    bob.undo()
    assert [e.name for e in bob.list_trash()] == ["Bob's"]
    bob.close()


def test_a_missing_undo_copy_is_explained(p):
    s = p.add_section("text", "Notes")
    with p.undo_step("Delete section"):
        p.delete_section(s.id)
    import shutil
    shutil.rmtree(p.journal.folder)   # (e.g. the project opened elsewhere)
    with pytest.raises(ProjectError, match="opened somewhere else"):
        p.undo()


def test_nested_steps_join_the_outer_one(p):
    with p.undo_step("Outer"):
        p.add_section("text", "A")
        with p.undo_step("Inner"):
            p.add_section("text", "B")
    assert p.journal.undo_label == "Outer" and len(p.journal.undo_stack) == 1
    p.undo()
    assert p.sections == []
