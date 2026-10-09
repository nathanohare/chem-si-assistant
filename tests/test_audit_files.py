"""Fixes from the audit of saving, undo, the lock, references and the self-test
(core/fileio.py, core/undo.py, core/lock.py, core/references.py, ui/references_ui.py,
ui/panels.py, ui/front_matter.py, selftest.py). Made-up data only."""

import json
import os
import pathlib
import tempfile
import time
from pathlib import Path

import pytest

from si_assistant.core import fileio, undo
from si_assistant.core import lock as lockmod
from si_assistant.core import references as R
from si_assistant.core.project import Project, ProjectError


@pytest.fixture
def p(project, monkeypatch):
    monkeypatch.setattr(fileio, "_REPLACE_DELAY_S", 0)   # (no waiting in the retries)
    monkeypatch.setattr(undo, "_RENAME_DELAY_S", 0)
    project.enable_undo()
    return project


def files_of(folder):
    out = {}
    for path in sorted(Path(folder).rglob("*")):
        rel = path.relative_to(folder)
        if rel.parts[0] in ("build", ".lock") or not path.is_file():
            continue
        out[rel.as_posix()] = path.read_bytes()
    return out


# ---------------------------------------------------------------------- undo-files-1
# A move inside the project is a rename or nothing (never copy-then-delete).

def test_a_blocked_compound_move_leaves_the_compound_where_it_was(p, monkeypatch):
    a = p.add_section("data", "A")
    b = p.add_section("data", "B")
    c = p.add_compound(a.id, name="X")
    p.write_compound_text(c, "procedure", "Stirred.")
    source = c.folder
    real_rename = pathlib.Path.rename

    def blocked(self, target):
        if Path(self) == source:
            raise PermissionError(13, "in use (test)", str(self))
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", blocked)
    steps = len(p.journal.undo_stack)
    with pytest.raises(PermissionError):
        with p.undo_step("Move"):
            p.move_compound(c.id, b.id, 0)
    monkeypatch.setattr(pathlib.Path, "rename", real_rename)
    assert (source / "compound.json").is_file() and (source / "procedure.txt").is_file()
    assert not list((b.folder / "compounds").glob("*/compound.json")) \
        if (b.folder / "compounds").exists() else True
    assert p.compound(c.id).section_id == a.id
    again = Project.open(p.folder, read_only=True)
    assert again.compound(c.id).section_id == a.id
    assert len(p.journal.undo_stack) == steps   # (nothing changed: no step)


def test_move_never_replaces_what_is_already_there(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    with pytest.raises(FileExistsError):
        fileio.move(tmp_path / "a.txt", tmp_path / "b.txt")
    assert (tmp_path / "a.txt").read_text() == "a" and (tmp_path / "b.txt").read_text() == "b"
    fileio.move(tmp_path / "a.txt", tmp_path / "c.txt")
    assert (tmp_path / "c.txt").read_text() == "a" and not (tmp_path / "a.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")
def test_a_spectrum_open_in_a_viewer_does_not_split_the_compound(p):
    import ctypes
    from ctypes import wintypes
    a = p.add_section("data", "A")
    b = p.add_section("data", "B")
    c = p.add_compound(a.id, name="X")
    pdf = c.folder / "h1.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    create = ctypes.windll.kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    handle = create(str(pdf), 0x80000000, 1, None, 3, 0x80, None)   # read, share read
    try:
        with pytest.raises(OSError):
            with p.undo_step("Move"):
                p.move_compound(c.id, b.id, 0)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    assert (c.folder / "compound.json").is_file() and pdf.is_file()
    assert Project.open(p.folder, read_only=True).compound(c.id).section_id == a.id


# ---------------------------------------------------------------------- undo-files-2,
# hunt-unusual-characters-and-paths-3: no reference can stop the SI from compiling

BAD_VALUES = [("year", "in press"), ("year", "2010a"), ("year", "(2010)"),
              ("year", "2019–2020"), ("year", "2010-13"), ("year", "2010-02-30"),
              ("url", "www.example.com/page"), ("url", "doi.org/10.1/x"),
              ("url", "https://exa mple.com"), ("url", "http://"),
              ("url", "https://x.com:99999/"), ("title", "a } b"),
              ("title", "Reactions of {M}2(CO)10} with x"), ("title", "bad \u0092 quote"),
              ("title", "del \x7f"), ("title", "odd ￾"), ("journal", "J } X"),
              ("publisher", "P }"), ("note", "N }"),
              ("authors", ["Smith, J.; Jones, K.; Lee, M."]), ("authors", ["A, B, C, D"]),
              ("authors", ["Smith, J., Jones, K."])]


def _compiles(yml: str, cite: str, style: str) -> None:
    import typst
    folder = Path(tempfile.mkdtemp())
    (folder / "refs.yml").write_text(yml, encoding="utf-8")
    (folder / "m.typ").write_text(f'Text @{cite}\n#bibliography("refs.yml", style: "{style}")\n',
                                  encoding="utf-8")
    assert typst.compile(str(folder / "m.typ")).startswith(b"%PDF")


@pytest.mark.parametrize("style", ["american-chemical-society", "royal-society-of-chemistry",
                                   "angewandte-chemie"])
def test_a_reference_typst_cannot_read_no_longer_stops_the_compile(style):
    good = {"id": "r-good", "authors": ["Fulmer, G. R."], "title": "Good", "journal": "Org. Lett.",
            "year": "2010"}
    for field, value in BAD_VALUES:
        bad = {"id": "r-bad", "authors": ["Doe, J."], "title": "T", "journal": "J", "year": "2011",
               field: value}
        yml = R.to_hayagriva([good, bad])
        _compiles(yml, "r-good", style)   # (an uncited bad reference broke it too)
        _compiles(yml, "r-bad", style)


def test_what_was_typed_is_kept_and_named():
    out = json.loads(R.to_hayagriva([{"id": "r-1", "year": "in press", "url": "www.x.org/a",
                                      "note": "SI"}]))["r-1"]
    assert "date" not in out and out["url"] == "https://www.x.org/a"
    assert out["note"] == "SI; in press"
    out = json.loads(R.to_hayagriva([{"id": "r-1", "year": "2010a", "url": "doi.org/1"}]))["r-1"]
    assert out["date"] == "2010" and out["note"] == "2010a; doi.org/1" and "url" not in out
    problems = R.printing_problems({"year": "in press", "url": "doi.org/1",
                                    "authors": ["Smith, J.; Jones, K."]})
    assert len(problems) == 3 and "in press" in problems[0] and "left out" in problems[0]
    assert R.printing_problems({"year": "2010", "url": "https://x.org",
                                "authors": ["Smith, John, Jr."]}) == []


def test_ordinary_references_are_written_as_before():
    ref = {"id": "r-1", "type": "article", "authors": ["Fulmer, Gregory R.", "van der Berg, Jan"],
           "title": "Shifts of {CO} Complexes", "journal": "Organometallics", "year": "2010",
           "volume": "29", "issue": "9", "pages": "2176-2179", "doi": "10.1021/om100106e",
           "url": "https://pubs.acs.org/x", "publisher": "", "note": "A note"}
    out = json.loads(R.to_hayagriva([ref]))["r-1"]
    assert out == {"type": "article", "title": "Shifts of {CO} Complexes",
                   "author": ["Fulmer, Gregory R.", "van der Berg, Jan"], "date": "2010",
                   "page-range": "2176–2179", "serial-number": {"doi": "10.1021/om100106e"},
                   "url": "https://pubs.acs.org/x", "note": "A note",
                   "parent": {"type": "periodical", "title": "Organometallics",
                              "volume": "29", "issue": "9"}}
    for year in ("2010", "2010-05", "2010-05-12"):
        assert json.loads(R.to_hayagriva([{"id": "r", "year": year}]))["r"]["date"] == year
    # Characters YAML refuses are escaped, and read back as typed.
    assert "\u0092" not in R.to_hayagriva([{"id": "r", "title": "a\u0092b"}])
    assert json.loads(R.to_hayagriva([{"id": "r", "title": "a\u0092b"}]))["r"]["title"] == "a\u0092b"


# ---------------------------------------------------------------------- data-safety-1
# A failed undo never deletes the newer files.

def _edit_step(p):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    with p.undo_step("Edit compound"):
        c.values["name"]["text"] = "Ethyl ester"
        p.save_compound(c)                                  # (compound.json: recorded first)
        p.write_compound_text(c, "procedure", "Newer text.")   # (new file)
    return c


def test_a_file_that_cannot_be_replaced_leaves_everything_as_it_was(p, monkeypatch):
    c = _edit_step(p)
    before = files_of(p.folder)
    real_replace = os.replace

    def blocked(src, dst):
        if Path(dst).name == "compound.json":
            raise PermissionError(13, "in use (test)", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(undo.os, "replace", blocked)
    with pytest.raises(ProjectError, match="nothing was changed"):
        p.undo()
    assert files_of(p.folder) == before
    monkeypatch.setattr(undo.os, "replace", real_replace)
    p.undo()
    assert p.read_compound_text(p.compound(c.id), "procedure") == ""
    assert p.compound(c.id).name == "Ester"


def test_when_putting_back_fails_too_the_newer_files_go_to_the_trash(p, monkeypatch):
    _edit_step(p)
    real_replace, real_rename = os.replace, undo._rename

    def blocked_replace(src, dst):
        if Path(dst).name == "compound.json":
            raise PermissionError(13, "in use (test)", str(dst))
        return real_replace(src, dst)

    def blocked_rename(source, target):
        # (moving a kept file back out of the undo history fails; whole folders move fine)
        if Path(source).parent.parent == p.journal.folder:
            raise PermissionError(13, "in use (test)", str(source))
        return real_rename(source, target)

    monkeypatch.setattr(undo.os, "replace", blocked_replace)
    monkeypatch.setattr(undo, "_rename", blocked_rename)
    with pytest.raises(ProjectError, match="Trash") as e:
        p.undo()
    assert "could not put everything back" in str(e.value)
    kept = [f for f in (p.folder / "trash").rglob("*") if f.is_file()
            and f.read_bytes() == b"Newer text."]
    assert kept, "the newer procedure text was deleted"
    entry = next(t for t in p.list_trash() if "Newer files kept" in t.name)
    assert "procedure.txt" in " ".join(entry.info["files"].values())
    p.journal.close()   # (closing empties build/undo/: the kept files stay in the Trash)
    p.journal = None
    assert kept[0].read_bytes() == b"Newer text."


# ---------------------------------------------------------------------- checks-dashboard-2

def test_text_saved_by_another_editor_is_still_read(tmp_path):
    path = tmp_path / "procedure.txt"
    path.write_bytes("Stirred at 0 °C, 5 µL\r\n".encode("cp1252"))
    assert fileio.read_text(path) == "Stirred at 0 °C, 5 µL\n"
    path.write_bytes("δ 7.26 (CDCl₃)".encode("utf-16"))
    assert fileio.read_text(path) == "δ 7.26 (CDCl₃)"
    # UTF-8 with a ° added later by an "ANSI" editor: only that byte is read as cp1252.
    path.write_bytes("δ 7.26, CDCl₃, ".encode("utf-8") + "0 °C".encode("cp1252"))
    assert fileio.read_text(path) == "δ 7.26, CDCl₃, 0 °C"
    path.write_bytes("﻿plain UTF-8 °C\r\n".encode("utf-8"))
    assert fileio.read_text(path) == "plain UTF-8 °C\n"
    assert fileio.read_text(tmp_path / "missing.txt", "x") == "x"


def test_a_project_with_an_ansi_procedure_still_opens(project):
    s = project.add_section("data", "Products")
    c = project.add_compound(s.id, name="Ester")
    project.write_compound_text(c, "procedure", "x")
    (c.folder / "procedure.txt").write_bytes("Stirred at 0 °C.".encode("cp1252"))
    again = Project.open(project.folder, read_only=True)
    assert again.read_compound_text(again.compound(c.id), "procedure") == "Stirred at 0 °C."


# ---------------------------------------------------------------------- undo-files-3

def _refs(n):
    return [{"id": f"r-{i}", "authors": [f"Author{i}, A."], "journal": "J. Org. Chem.",
             "year": "2020", "title": f"Title {i}"} for i in range(n)]


def test_the_reference_list_is_filled_with_the_same_layout(qapp, project):
    from PySide6.QtWidgets import QHeaderView
    from si_assistant.ui.references_ui import ReferencesPage
    project.references = _refs(40)
    page = ReferencesPage(project)
    page.resize(900, 500)
    page.show()
    qapp.processEvents()
    table = page.table
    header = table.horizontalHeader()
    auto, stretch = QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch
    assert [header.sectionResizeMode(c) for c in range(4)] == [auto, auto, stretch, auto]
    assert table.rowCount() == 40 and table.item(39, 0).text() == "Author39 2020"
    narrow = header.sectionSize(0)
    assert narrow >= table.sizeHintForColumn(0)
    # A longer name: the column grows when the list is filled again.
    project.references[5]["authors"] = ["Averyveryverylongsurnameindeed, A."]
    page.widget._fill("r-5")
    qapp.processEvents()
    assert header.sectionSize(0) > narrow and table.currentRow() == 5
    assert [header.sectionResizeMode(c) for c in range(4)] == [auto, auto, stretch, auto]
    page.close()


# ---------------------------------------------------------------------- undo-files-4

def test_a_lock_that_is_busy_for_a_moment_is_still_ours(tmp_path, monkeypatch):
    monkeypatch.setattr(lockmod, "_READ_DELAY_S", 0)
    mine = lockmod.write_lock(tmp_path, "Nathan")
    real = lockmod.read_json
    calls = []

    def busy_twice(path, default=None):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "in use (test)", str(path))
        return real(path, default)

    monkeypatch.setattr(lockmod, "read_json", busy_twice)
    assert lockmod.is_ours(tmp_path, mine.session)


def test_an_unreadable_lock_is_never_taken_for_someone_else(tmp_path, monkeypatch):
    monkeypatch.setattr(lockmod, "_READ_DELAY_S", 0)
    mine = lockmod.write_lock(tmp_path, "Nathan")
    (tmp_path / lockmod.LOCK_NAME).write_text("")   # (empty, e.g. half-synced)
    with pytest.raises(OSError):
        lockmod.is_ours(tmp_path, mine.session)
    lockmod.release_lock(tmp_path, mine.session)    # (can't tell: left alone, no error)
    assert (tmp_path / lockmod.LOCK_NAME).exists()
    # The open-project question still shows it as present but unknown.
    assert lockmod.read_lock(tmp_path).user == "unknown user"
    # Someone else's readable lock is still someone else's.
    lockmod.write_lock(tmp_path, "Bob")
    assert not lockmod.is_ours(tmp_path, mine.session)


def test_no_lock_is_written_into_a_vanished_project_folder(tmp_path):
    # (handoff from main, data-safety-5: moved or deleted meanwhile; no "ghost" folder)
    gone = tmp_path / "Moved away"
    with pytest.raises(FileNotFoundError):
        lockmod.write_lock(gone, "Nathan")
    assert not gone.exists()
    assert lockmod.write_lock(str(tmp_path), "Nathan").user == "Nathan"   # (str is fine)
    assert (tmp_path / lockmod.LOCK_NAME).exists()


def test_the_window_check_sees_an_unreadable_lock_as_try_again(project, monkeypatch):
    monkeypatch.setattr(lockmod, "_READ_DELAY_S", 0)

    def busy(path, default=None):
        raise PermissionError(13, "in use (test)", str(path))

    monkeypatch.setattr(lockmod, "read_json", busy)
    with pytest.raises(OSError):   # (MainWindow._check_lock then looks again later)
        project.lock_still_ours()


# ---------------------------------------------------------------------- undo-files-5

@pytest.mark.parametrize("entry", [{"id": "r-1", "authors": None}, {"id": "r-1", "type": ["article"]},
                                   {"id": "r-1", "authors": 3}, {"id": "r-1", "type": {"a": 1}},
                                   {"id": 5, "authors": [None, "Doe, J."]}])
def test_a_hand_edited_reference_list_does_not_stop_the_project_opening(project, entry):
    (project.folder / "references.json").write_text(json.dumps([entry]), encoding="utf-8")
    again = Project.open(project.folder, read_only=True)
    ref = again.references[0]
    assert ref["type"] == "article" and ref["id"] in ("r-1", "5")
    assert ref["authors"] in ([], ["Doe, J."])
    good = {"id": "r-2", "type": "book", "authors": "Doe, J.; Roe, K.", "title": " T "}
    assert R.clean(good)["authors"] == ["Doe, J.", "Roe, K."] and R.clean(good)["id"] == "r-2"


# ---------------------------------------------------------------------- undo-files-6

def test_new_references_follow_the_duplicate_rules():
    existing = [{"id": "r-1", "doi": "10.1/ABC", "title": "Old", "year": "2000"},
                {"id": "r-2", "doi": "", "title": "No DOI", "year": "2001"}]
    found = [{"id": "r-1", "doi": "https://doi.org/10.1/abc", "title": "x"},   # same DOI
             {"doi": "", "title": "no doi ", "year": "2001"},                   # same title+year
             {"doi": "", "title": "No DOI", "year": "2002"},                    # other year
             {"doi": "", "title": "No DOI", "year": "2002"},                    # twice in the file
             {"doi": "10.2/X", "title": "Old", "year": "2000"},                 # DOI: by DOI only
             {"doi": "10.2/x", "title": "Again"}]                               # its DOI again
    new = R.new_references(existing, found)
    assert [(r["title"], r["year"]) for r in new] == [("No DOI", "2002"), ("Old", "2000")]
    assert len({r["id"] for r in new} | {"r-1", "r-2"}) == 4


def _import(qapp, project, tmp_path, monkeypatch, text):
    from si_assistant.ui import references_ui
    bib = tmp_path / "refs.bib"
    bib.write_text(text, encoding="utf-8")
    monkeypatch.setattr(references_ui.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(bib), ""))
    shown = {"info": [], "warning": []}
    monkeypatch.setattr(references_ui.QMessageBox, "information",
                        lambda *a, **k: shown["info"].append(a[2]))
    monkeypatch.setattr(references_ui.QMessageBox, "warning",
                        lambda *a, **k: shown["warning"].append(a[2]))
    widget = references_ui.ReferencesWidget(project)
    widget._import()
    return widget, shown


BIB = "\n".join(f"@article{{k{i}, author={{Doe, J.}}, title={{Paper {i}}}, journal={{J}}, "
                f"year={{2020}}, doi={{10.1/p{i}}}}}" for i in range(30)) + \
    "\n@article{dup, author={Doe, J.}, title={Paper 3}, year={2020}, doi={10.1/P3}}"


def test_an_import_saves_the_reference_list_once(qapp, project, tmp_path, monkeypatch):
    from si_assistant.core import project as projmod
    project.enable_undo()
    writes = []
    real = projmod.atomic_write_json
    monkeypatch.setattr(projmod, "atomic_write_json",
                        lambda path, obj: (writes.append(Path(path).name), real(path, obj)))
    widget, shown = _import(qapp, project, tmp_path, monkeypatch, BIB)
    assert writes.count("references.json") == 1
    assert len(project.references) == 30 and widget.table.rowCount() == 30
    assert shown["info"] == ["Added 30 reference(s). 1 were already in the list."]
    project.undo()
    assert project.references == []


def test_an_import_that_cannot_be_saved_adds_nothing(qapp, project, tmp_path, monkeypatch):
    from si_assistant.core import project as projmod

    def fail(path, obj):
        raise PermissionError(13, "in use (test)", str(path))

    monkeypatch.setattr(projmod, "atomic_write_json", fail)
    widget, shown = _import(qapp, project, tmp_path, monkeypatch, BIB)
    assert len(shown["warning"]) == 1 and shown["info"] == []
    assert project.references == [] and widget.table.rowCount() == 0


# ---------------------------------------------------------------------- undo-files-7

def _no_crash_dialog(monkeypatch, module):
    warnings = []
    monkeypatch.setattr(module.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[1]))
    return warnings


def test_a_text_page_that_cannot_save_says_so_and_saves_later(qapp, project, monkeypatch):
    from si_assistant.ui import panels
    notes = project.add_section("text", "Notes")
    panel = panels.TextSectionPanel(project, notes)
    warnings = _no_crash_dialog(monkeypatch, panels)
    saved = []
    panel.saved.connect(saved.append)
    real = Project.write_section_text

    def fail(self, section, text):
        raise PermissionError(13, "in use (test)", "content.txt")

    monkeypatch.setattr(Project, "write_section_text", fail)
    panel.editor.set_markup("Typed text.")
    panel._save_text()        # (what the autosave timer runs)
    assert warnings == ["Could not save"] and saved == []
    monkeypatch.setattr(Project, "write_section_text", real)
    panel.flush()             # (leaving the page: tried again)
    assert project.read_section_text(notes) == "Typed text." and saved == ["Saved"]
    panel.close()


def test_a_front_matter_page_that_cannot_save_says_so_and_saves_later(qapp, project, monkeypatch):
    from si_assistant.ui import front_matter
    panel = front_matter.GeneralInfoPanel(project)
    warnings = _no_crash_dialog(monkeypatch, front_matter)
    real = Project.write_front_text

    def fail(self, key, text):
        raise PermissionError(13, "in use (test)", "general_information.txt")

    monkeypatch.setattr(Project, "write_front_text", fail)
    panel.editor.set_markup("Materials were bought.")
    panel._save()
    assert warnings == ["Could not save"]
    monkeypatch.setattr(Project, "write_front_text", real)
    panel.flush()
    assert "Materials were bought." in project.read_owner_text(
        project.text_owner(front_matter.GENERAL_OWNER))
    panel.close()


def test_a_rename_that_cannot_save_keeps_the_old_name(qapp, project, monkeypatch):
    from si_assistant.ui import panels
    notes = project.add_section("text", "Notes")
    panel = panels.TextSectionPanel(project, notes)
    warnings = _no_crash_dialog(monkeypatch, panels)
    real = Project.save_section

    def fail(self, section):
        raise PermissionError(13, "in use (test)", "section.json")

    monkeypatch.setattr(Project, "save_section", fail)
    panel.name_edit.setText("Methods")
    panel._commit_name()
    assert warnings == ["Could not save"]
    assert notes.name == "Notes" and panel.name_edit.text() == "Notes"
    monkeypatch.setattr(Project, "save_section", real)
    panel.close()


def test_reference_saved_is_only_said_when_it_was(qapp, project, monkeypatch):
    from si_assistant.ui import references_ui
    project.references = _refs(1)
    widget = references_ui.ReferencesWidget(project)
    warnings = _no_crash_dialog(monkeypatch, references_ui)
    said = []
    widget.changed.connect(said.append)
    widget.table.selectRow(0)
    monkeypatch.setattr(references_ui.ReferenceEditDialog, "exec", lambda self: True)

    def fail(ref):
        raise PermissionError(13, "in use (test)", "references.json")

    monkeypatch.setattr(project, "update_reference", fail)
    widget._edit()
    assert warnings == ["Could not save"] and said == []
    monkeypatch.undo()


# ---------------------------------------------------------------------- data-safety-3
# Undo never takes away files put in the project from outside the program.

def _private_step(p, tmp_path, folder=False):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    source = tmp_path / ("raw" if folder else "nmr.fid")
    if folder:
        (source / "pdata").mkdir(parents=True)
        (source / "pdata" / "1r").write_bytes(b"spectrum")
    else:
        source.write_bytes(b"fid")
    with p.undo_step("Add files to Ester"):
        p.add_private_item(c, source)
    return c


def test_undo_refuses_to_take_away_a_file_added_by_hand(p, tmp_path):
    c = _private_step(p, tmp_path)
    mine = p.private_dir(c) / "my notebook page.jpg"
    mine.write_bytes(b"photo")
    with pytest.raises(ProjectError, match="outside the program") as e:
        p.undo()
    assert "my notebook page.jpg" in str(e.value) and "nothing was changed" in str(e.value)
    assert mine.read_bytes() == b"photo" and (p.private_dir(c) / "nmr.fid").exists()
    assert p.can_undo
    moved = tmp_path / "kept.jpg"
    mine.rename(moved)   # (the user moves it out first)
    p.undo()
    assert not p.private_dir(c).exists()
    p.redo()
    assert (p.private_dir(c) / "nmr.fid").read_bytes() == b"fid"


def test_system_files_never_stop_an_undo(p, tmp_path):
    c = _private_step(p, tmp_path)
    (p.private_dir(c) / ".DS_Store").write_bytes(b"finder")
    (p.private_dir(c) / "Thumbs.db").write_bytes(b"explorer")
    p.undo()
    assert not p.private_dir(c).exists()


def test_a_folder_copied_in_whole_is_the_programs_own(p, tmp_path):
    c = _private_step(p, tmp_path, folder=True)
    assert (p.private_dir(c) / "raw" / "pdata" / "1r").exists()
    p.undo()
    assert not p.private_dir(c).exists()


def test_a_file_dropped_into_a_copied_folder_is_kept(p, tmp_path):
    c = _private_step(p, tmp_path, folder=True)
    notes = p.private_dir(c) / "raw" / "notes.txt"
    notes.write_text("mine")
    with pytest.raises(ProjectError, match="outside the program"):
        p.undo()
    assert notes.read_text() == "mine"


def test_redo_refuses_to_replace_a_folder_made_again_by_hand(p, tmp_path):
    c = _private_step(p, tmp_path)
    p.undo()
    p.private_dir(c).mkdir()   # ("Open storage folder"), then a file put there
    (p.private_dir(c) / "mine.txt").write_text("mine")
    with pytest.raises(ProjectError, match="outside the program"):
        p.redo()
    assert (p.private_dir(c) / "mine.txt").read_text() == "mine"


# ---------------------------------------------------------------------- undo-files-8

def test_the_lock_time_is_english_and_complete():
    info = lockmod.LockInfo("Nathan", "pc", "2026-09-05T00:05:00", "s")
    assert info.describe() == "Opened by Nathan on pc since 12:05 AM on 5 Sep 2026"
    info.opened_at = "2026-09-30T12:00:00"
    assert info.describe() == "Opened by Nathan on pc since 12:00 PM on 30 Sep 2026"
    info.opened_at = "2026-09-30T15:12:00"
    assert info.describe() == "Opened by Nathan on pc since 3:12 PM on 30 Sep 2026"
    assert lockmod.LockInfo("unknown user", "unknown computer", "", "").describe() == \
        "Opened by unknown user on unknown computer"
    assert lockmod.LockInfo("u", "c", "yesterday", "").describe() == "Opened by u on c since yesterday"


def test_the_lock_time_does_not_follow_the_computers_language():
    import locale
    before = locale.setlocale(locale.LC_TIME)
    for name in ("de_DE.UTF-8", "de_DE", "German_Germany.1252", "German"):
        try:
            locale.setlocale(locale.LC_TIME, name)
            break
        except locale.Error:
            continue
    else:
        pytest.skip("no German locale on this computer")
    try:
        info = lockmod.LockInfo("N", "pc", "2026-03-05T15:12:00", "s")
        assert info.describe() == "Opened by N on pc since 3:12 PM on 5 Mar 2026"
    finally:
        locale.setlocale(locale.LC_TIME, before)


# ---------------------------------------------------------------------- undo-files-9

def test_writing_the_same_bytes_again_leaves_the_file_alone(tmp_path):
    path = tmp_path / "project.json"
    fileio.atomic_write_bytes(path, b'{"a": 1}')
    stamp = os.stat(path).st_mtime_ns
    time.sleep(0.02)
    os.utime(path, ns=(stamp - 10_000_000, stamp - 10_000_000))   # (an older date to compare)
    old = os.stat(path).st_mtime_ns
    fileio.atomic_write_bytes(path, b'{"a": 1}')
    assert os.stat(path).st_mtime_ns == old
    assert not list(tmp_path.glob(".tmp-*"))
    fileio.atomic_write_bytes(path, b'{"a": 2}')   # (same length, other bytes)
    assert path.read_bytes() == b'{"a": 2}'
    fileio.atomic_write_bytes(tmp_path / "new.txt", b"")
    assert (tmp_path / "new.txt").read_bytes() == b""


def test_an_unchanged_save_is_no_undo_step(p):
    steps = len(p.journal.undo_stack)
    p.save_settings()
    p.save_settings()
    assert len(p.journal.undo_stack) == steps


# ---------------------------------------------------------------------- app-shell-8

def test_the_self_test_never_takes_over_an_open_project(qapp, project):
    from si_assistant import selftest
    s = project.add_section("data", "Products")
    project.add_compound(s.id, name="Ester")
    before = (project.folder / lockmod.LOCK_NAME).read_bytes()
    detail = selftest._compile(project.folder)
    assert "pages" in detail and "Opened by Tester" in detail
    assert (project.folder / lockmod.LOCK_NAME).read_bytes() == before
    assert project.lock_still_ours()


# ---------------------------------------------------------------------- data-safety-10

def test_a_folder_in_use_is_never_deleted_past_the_undo_history(p, monkeypatch):
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    p.write_compound_text(c, "procedure", "Stirred.")
    folder = c.folder
    real = undo._rename

    def blocked(source, target):
        if Path(source) == folder:
            raise PermissionError(13, "in use (test)", str(source))
        return real(source, target)

    monkeypatch.setattr(undo, "_rename", blocked)
    with p.undo_step("Tidy"):
        fileio.remove_tree(folder, ignore_errors=True)
    # (review-7: what can be moved goes into the undo history one item at a time)
    assert not (folder / "compound.json").exists()
    assert [f for f in p.journal.folder.rglob("*") if f.is_file() and f.read_bytes() == b"Stirred."]
    monkeypatch.setattr(undo, "_rename", real)
    p.undo()
    assert (folder / "compound.json").is_file() and (folder / "procedure.txt").is_file()


def test_a_folder_made_by_the_same_change_can_still_be_cleaned_up(p, monkeypatch):
    real = undo._rename
    target = p.folder / "trash" / "x-entry"

    def blocked(source, dest):
        if Path(source) == target:
            raise PermissionError(13, "in use (test)", str(source))
        return real(source, dest)

    monkeypatch.setattr(undo, "_rename", blocked)
    steps = len(p.journal.undo_stack)
    with p.undo_step("Delete"):
        fileio.make_dirs(target)
        fileio.atomic_write_json(target / "entry.json", {"pending": True})
        fileio.remove_tree(target, ignore_errors=True)
    assert not target.exists()
    assert len(p.journal.undo_stack) == steps   # (nothing left: no step)


# ---------------------------------------------------------------------- cross-platform-5

@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_saving_keeps_the_files_permissions(tmp_path):
    path = tmp_path / "project.json"
    path.write_text("{}")
    os.chmod(path, 0o664)
    fileio.atomic_write_text(path, '{"a": 1}')
    assert os.stat(path).st_mode & 0o777 == 0o664
    fileio.atomic_write_text(tmp_path / "new.json", "{}")
    assert os.stat(tmp_path / "new.json").st_mode & 0o777 == 0o666 & ~fileio._UMASK


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_a_drive_that_refuses_chmod_still_saves(tmp_path, monkeypatch):
    def refuse(*args):
        raise PermissionError(1, "not permitted (test)")

    monkeypatch.setattr(fileio.os, "chmod", refuse)
    fileio.atomic_write_text(tmp_path / "a.txt", "text")
    assert (tmp_path / "a.txt").read_text() == "text"
    assert not list(tmp_path.glob(".tmp-*"))


# ---------------------------------------------------------------------- review of the fixes

def test_undo_after_a_redo_still_refuses_files_added_by_hand(p, tmp_path):
    """review-1: undo, redo, a file put in by hand, undo again: refused, as the first time."""
    c = _private_step(p, tmp_path)
    p.undo()
    p.redo()
    mine = p.private_dir(c) / "mine.jpg"
    mine.write_bytes(b"photo")
    with pytest.raises(ProjectError, match="outside the program") as e:
        p.undo()
    assert "mine.jpg" in str(e.value)
    assert mine.read_bytes() == b"photo" and (p.private_dir(c) / "nmr.fid").exists()
    mine.rename(tmp_path / "kept.jpg")
    for _ in range(2):   # (and again: the history stays with the newest step)
        p.undo()
        assert not p.private_dir(c).exists()
        assert list(p.journal.redo_stack[-1].folder.rglob("nmr.fid"))
        p.redo()
        assert (p.private_dir(c) / "nmr.fid").read_bytes() == b"fid"


def test_a_failed_undo_after_a_redo_puts_everything_back_in_place(p, tmp_path, monkeypatch):
    """review-1: the rollback of an undo that stopped part-way puts what came out of the
    history back where the step expects it, so it can still be undone."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    (tmp_path / "nmr.fid").write_bytes(b"fid")
    with p.undo_step("Add"):
        p.add_private_item(c, tmp_path / "nmr.fid")
        p.write_compound_text(c, "procedure", "Stirred.")
    p.undo()
    p.redo()   # (its folder came back out of the history: undone first, below)
    before = files_of(p.folder)
    real = undo.Journal._apply_one
    calls = []

    def fail_second(self, record, into, rollback=False):
        calls.append(record)
        if len(calls) == 2 and not rollback:
            raise PermissionError(13, "in use (test)", str(record[1]))
        return real(self, record, into, rollback)

    monkeypatch.setattr(undo.Journal, "_apply_one", fail_second)
    with pytest.raises(ProjectError, match="nothing was changed"):
        p.undo()
    monkeypatch.setattr(undo.Journal, "_apply_one", real)
    assert p.journal._in_history(calls[0][1])
    assert files_of(p.folder) == before
    p.undo()
    assert not p.private_dir(p.compound(c.id)).exists()
    assert p.read_compound_text(p.compound(c.id), "procedure") == ""
    p.redo()
    assert (p.private_dir(p.compound(c.id)) / "nmr.fid").read_bytes() == b"fid"
    assert p.read_compound_text(p.compound(c.id), "procedure") == "Stirred."


def test_the_trash_folder_kept_after_a_failed_undo_is_named_as_such(p, monkeypatch):
    """review-4: an undo that would take away the files kept in the Trash after a failed
    undo is refused, saying what they are (not "added outside the program")."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Ester")
    acid = p.add_compound(s.id, name="Acid")
    p.write_compound_text(c, "procedure", "Older text.")
    with p.undo_step("Delete Acid"):
        p.delete_compound(acid.id)   # (makes trash/)
    with p.undo_step("Edit"):
        p.write_compound_text(c, "procedure", "Newer text.")
        fileio.atomic_write_text(c.folder / "extra.txt", "new file")
    real = undo._rename
    calls = []

    def flaky(source, target):
        calls.append(source)
        if len(calls) > 1:
            raise PermissionError(13, "in use (test)", str(source))
        return real(source, target)

    real_replace = os.replace

    def blocked_replace(src, dst):
        if Path(dst).name == "procedure.txt":
            raise PermissionError(13, "in use (test)", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(undo, "_rename", flaky)
    monkeypatch.setattr(undo.os, "replace", blocked_replace)
    with pytest.raises(ProjectError, match="Trash"):
        p.undo()
    monkeypatch.setattr(undo, "_rename", real)
    monkeypatch.setattr(undo.os, "replace", real_replace)
    kept = next(t for t in p.list_trash() if "Newer files kept" in t.name)
    while p.journal.undo_label != "Delete Acid":
        p.undo()
    with pytest.raises(ProjectError, match="newer files kept in the project's Trash") as e:
        p.undo()
    assert "outside the program" not in str(e.value)
    assert kept.folder.relative_to(p.folder).as_posix() in str(e.value)
    assert (kept.folder / "entry.json").is_file()


def test_a_full_width_year_is_printed(tmp_path):
    """review-2: digits typed in another script are the same year (Typst reads only 0-9)."""
    assert R.hayagriva_date("２０１０") == ("2010", False)
    assert R.hayagriva_date("٢٠١٠-٠٥") == ("2010-05", False)
    assert R.hayagriva_date("c. ２０１０") == ("2010", True)
    out = json.loads(R.to_hayagriva([{"id": "r-1", "year": "２０１０"}]))["r-1"]
    assert out["date"] == "2010" and "note" not in out
    assert R.printing_problems({"year": "２０１０"}) == []


@pytest.mark.parametrize("url", ["https://例え.jp/a", "https://my_host.example.org",
                                 "https://[::1]/", "https://x.com?q=a b", "https://x.com/a b",
                                 "https://xn--r8jz45g.jp/", "https://مثال.com/",
                                 "https://אב.a-b.com/", "https://1.2.3.4/"])
def test_addresses_typst_prints_are_kept(url):
    """review-5: only what Typst refuses is left out of the printed address."""
    assert R.hayagriva_url(url) == url and R.printing_problems({"url": url}) == []
    yml = R.to_hayagriva([{"id": "r-1", "title": "T", "year": "2010", "url": url}])
    assert json.loads(yml)["r-1"]["url"] == url
    _compiles(yml, "r-1", "american-chemical-society")


@pytest.mark.parametrize("url", ["https://a b.com/", "https://abא.com/", "https://a.123/",
                                 "https://אב.1x.com/", "https://١٢٣.com/",
                                 "https://[zz]/", "https://xn--zz.com/", "https://́a.com/",
                                 "https://a|b.com/", "https://x.com/\x01"])
def test_addresses_typst_refuses_never_stop_the_compile(url):
    assert R.hayagriva_url(url) == ""
    assert "cannot be printed as a link" in R.printing_problems({"url": url})[0]
    _compiles(R.to_hayagriva([{"id": "r-1", "title": "T", "year": "2010", "url": url}]),
              "r-1", "american-chemical-society")


def test_a_reference_that_cannot_be_deleted_stays_in_the_list(qapp, project, monkeypatch):
    """review-3: a failed save puts the list back, so a later save does not write it."""
    from si_assistant.core import project as projmod
    from si_assistant.ui import references_ui
    project.add_reference(_refs(1)[0])
    widget = references_ui.ReferencesWidget(project)
    warnings = _no_crash_dialog(monkeypatch, references_ui)
    monkeypatch.setattr(references_ui.QMessageBox, "question",
                        lambda *a, **k: references_ui.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(references_ui.ReferenceEditDialog, "exec", lambda self: True)
    monkeypatch.setattr(references_ui.ReferenceEditDialog, "value",
                        lambda self: {**_refs(1)[0], "title": "Changed"})
    real = projmod.atomic_write_json

    def fail(path, obj):
        raise PermissionError(13, "in use (test)", str(path))

    monkeypatch.setattr(projmod, "atomic_write_json", fail)
    widget.table.selectRow(0)
    widget._delete()
    widget.table.selectRow(0)
    widget._edit()
    assert warnings == ["Could not save"] * 2
    assert widget.table.rowCount() == 1 and project.references[0]["title"] == "Title 0"
    monkeypatch.setattr(projmod, "atomic_write_json", real)
    project.add_reference({"id": "r-new", "title": "Other", "year": "2021"})
    saved = json.loads((project.folder / "references.json").read_text("utf-8"))
    assert [(r["id"], r["title"]) for r in saved] == [("r-0", "Title 0"), ("r-new", "Other")]


def test_leaving_a_page_that_cannot_save_promises_nothing(qapp, project, monkeypatch):
    """review-6: when the save on leaving fails too, the message says the changes are not
    saved (the page is about to close), and the page says so to the main window."""
    from si_assistant.ui import front_matter, panels
    texts = []
    for module in (panels, front_matter):
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *a, **k: texts.append(a[2]))

    def fail(self, *args):
        raise PermissionError(13, "in use (test)", "content.txt")

    monkeypatch.setattr(Project, "write_section_text", fail)
    monkeypatch.setattr(Project, "write_front_text", fail)
    notes = project.add_section("text", "Notes")
    for panel in (panels.TextSectionPanel(project, notes), front_matter.GeneralInfoPanel(project)):
        panel.editor.set_markup("Typed text.")
        panel._timer.start()
        panel.flush()
        assert panel._unsaved
        assert "could not be saved, so they are lost if the page is closed" in texts[-1]
        assert "tried again" not in texts[-1]
        panel.close()


def test_a_trash_entry_in_use_still_leaves_the_trash_list_when_restored(p, monkeypatch):
    """review-7: a restored Trash entry whose folder cannot be moved away (a file held for
    a moment) still goes into the undo history item by item, so it is no longer listed."""
    s = p.add_section("data", "Products")
    c = p.add_compound(s.id, name="Acid")
    with p.undo_step("Delete Acid"):
        entry = p.delete_compound(c.id)
    real = undo._rename
    blocked = []

    def once(source, target):
        if Path(source) == entry.folder and not blocked:
            blocked.append(source)
            raise PermissionError(13, "in use (test)", str(source))
        return real(source, target)

    monkeypatch.setattr(undo, "_rename", once)
    with p.undo_step("Restore Acid"):
        p.restore_from_trash(entry.id)
    assert blocked and p.list_trash() == [] and not entry.folder.exists()
    assert p.compound(c.id).name == "Acid"
    p.undo()
    assert [t.name for t in p.list_trash()] == [entry.name]


def test_bytes_with_no_windows_1252_character_are_kept():
    """review-8: 0x81, 0x8D, 0x8F, 0x90 and 0x9D are kept (as U+0081…), so writing the
    file back as UTF-8 loses nothing."""
    data = "0 °C ".encode("cp1252") + b"\x81\x8d\x8f\x90\x9d"
    text = fileio.decode_text(data)
    assert text == "0 °C \x81\x8d\x8f\x90\x9d" and "�" not in text
    assert text.encode("cp1252", errors="ignore") + text[-5:].encode("latin-1") == data


def test_save_waiting_is_the_ordinary_autosave(qapp, project, monkeypatch):
    """late2: the text pages' save_waiting() (used before adding or removing a figure or
    table) saves typing still waiting with the ordinary autosave, says False when that
    failed (with the 'tried again' message, not the 'lost if closed' one), and True after."""
    from si_assistant.ui import front_matter, panels
    from si_assistant.ui.text_objects import save_typing_first
    texts = []
    for module in (panels, front_matter):
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *a, **k: texts.append(a[2]))
    real_section, real_front = Project.write_section_text, Project.write_front_text

    def fail(self, *args):
        raise PermissionError(13, "in use (test)", "content.txt")

    notes = project.add_section("text", "Notes")
    for panel in (panels.TextSectionPanel(project, notes), front_matter.GeneralInfoPanel(project)):
        assert panel.save_waiting() is True and texts == []   # (nothing waiting)
        monkeypatch.setattr(Project, "write_section_text", fail)
        monkeypatch.setattr(Project, "write_front_text", fail)
        panel.editor.set_markup("Typed text.")
        panel._timer.start()
        assert save_typing_first(panel.editor) is False
        assert "tried again" in texts[-1] and "lost if" not in texts[-1]
        monkeypatch.setattr(Project, "write_section_text", real_section)
        monkeypatch.setattr(Project, "write_front_text", real_front)
        assert panel.save_waiting() is True and not panel._timer.isActive()
        texts.clear()
        panel.close()
    assert project.read_section_text(notes) == "Typed text."
    assert "Typed text." in project.read_owner_text(
        project.text_owner(front_matter.GENERAL_OWNER))
