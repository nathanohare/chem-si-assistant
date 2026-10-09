"""Regression tests for the audit of the Manuscript check (core/manuscript.py,
core/ms_parse.py, core/ms_read.py, ui/manuscript_ui.py). All data are made up
(tests/manuscript_samples.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from si_assistant.core import manuscript as M
from si_assistant.core import ms_parse as P
from si_assistant.core import ms_read

sys.path.insert(0, str(Path(__file__).parent))
import manuscript_samples as S  # noqa: E402


@pytest.fixture
def si(tmp_path):
    p, compounds = S.build_si(tmp_path / "si")
    S.make_pdf(tmp_path / "ms.pdf")
    S.make_cdxml(tmp_path / "scheme.cdxml")
    yield p, compounds, tmp_path
    M.forget(p)
    if p.lock is not None:
        p.close()


def _problems(result):
    return {(f.kind, f.label) for f in result.problems}


def _count_reads(monkeypatch):
    read = []
    real = ms_read.read_file

    def counting(path, progress=None):
        read.append(path.name)
        return real(path, progress)
    monkeypatch.setattr(ms_read, "read_file", counting)
    return read


# ---------------------------------------------------------------------- manuscript-1

def test_page_deleted_while_reading_caches_nothing(qapp, si, monkeypatch):
    """The page replaced (another tree item clicked) during the first page of a PDF: no
    error, and no healthy file is remembered as 'damaged'."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    M.add_file(p, S.make_pdf(tmp / "second.pdf"))
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[1]))
    holder = QWidget()
    page = U.ManuscriptPage(p, holder, auto=True)       # reads at the next event
    QTimer.singleShot(0, page.deleteLater)              # (as MainWindow._set_panel does)
    for _ in range(50):
        qapp.processEvents()
    assert errors == []
    assert M.cached_sources(p) is None
    assert not (M.folder(p) / M.CACHE_FILE).exists()
    holder.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------- manuscript-2

def _table(rows):
    """A table as find_tables / _word_table give it, from {column title: cell} rows."""
    return {"rows": [{"fields": P.row_fields(r)} for r in rows]}


@pytest.mark.parametrize("cell", ["–", "—", "-", "n.d.", "trace", "nr", "N.R."])
def test_table_placeholders_are_not_values(cell):
    recs = P.table_records(_table([{"Entry": "1", "Product": "3a", "Yield (%)": cell,
                                    "ee (%)": cell, "er": cell, "dr": cell}]))
    assert len(recs) == 1
    assert not {"yield", "ee", "er", "dr"} & set(recs[0])
    assert cell.lower() in recs[0]["text"].lower() or cell in ("–", "—")   # (still shown)


def test_table_numbers_are_still_kept():
    recs = P.table_records(_table([
        {"Product": "3a", "Yield (%)": "<5", "ee (%)": ">99", "dr": ">20:1"},
        {"Product": "3b", "Yield (%)": "0", "er": "97:3", "dr": "10"}]))
    assert (recs[0]["yield"], recs[0]["ee"], recs[0]["dr"]) == ("<5", ">99", ">20:1")
    assert (recs[1]["yield"], recs[1]["er"], recs[1]["dr"]) == ("0", "97:3", "10")


def test_table_placeholders_give_no_false_problems(tmp_path):
    p, _c = S.build_si(tmp_path)
    try:
        recs = P.table_records(_table([
            {"Entry": "1", "Product": "3a", "Yield (%)": "92", "ee (%)": "–"},
            {"Entry": "2", "Product": "3b", "Yield (%)": "n.d.", "ee (%)": "n.d."},
            {"Entry": "3", "Product": "3c", "Yield (%)": "70", "dr": "—"},
            {"Entry": "4", "Product": "9z", "Yield (%)": "trace", "ee (%)": "–"}]))
        records = [ms_read._clean_record(r, "Table 1", "ms.pdf", "table", "high")
                   for r in recs]
        result = M.check(p, [ms_read.Source("ms.pdf", "pdf", records=records)])
        assert _problems(result) == set()
        assert ("yield", "3a") in {(f.kind, f.label) for f in result.findings
                                   if f.status == M.OK}
        note = next(f for f in result.findings if f.label == "9z")
        assert note.kind == "mention" and note.status == M.NOTE
    finally:
        p.close()


@pytest.mark.parametrize("cell", ["quant.", "quant", "Quantitative", " quant. "])
def test_table_quantitative_yield_is_a_value(cell):
    (rec,) = P.table_records(_table([{"Product": "3a", "Yield (%)": cell}]))
    assert rec["yield"] == "quant."


@pytest.mark.parametrize("column", ["ee (%)", "er"])
@pytest.mark.parametrize("cell", ["rac", "rac.", "racemic"])
def test_table_racemic_is_a_value(cell, column):
    (rec,) = P.table_records(_table([{"Product": "3a", "Yield (%)": "80", column: cell}]))
    assert rec["ee"] == "rac" and "er" not in rec
    assert M.ee_value(rec["ee"]) == ("", 0.0, False)


def test_same_yield_with_quant():
    assert M.same_yield("quant.", "quant") and M.same_yield("92", "92")
    for si in ("99", ">99", "≥99", "100"):
        assert M.same_yield("quant.", si) and M.same_yield(si, "quant.")
    for si in ("92", "<99", "98.5", ""):
        assert not M.same_yield("quant.", si)
    assert M._pct("quant.") == "quant." and M._pct("92") == "92%"


def test_quant_and_rac_are_compared_with_the_si(tmp_path):
    p, c = S.build_si(tmp_path)
    try:
        c["3f"].values["yield"]["percent"] = ">99"
        p.save_compound(c["3f"])
        recs = P.table_records(_table([
            {"Entry": "1", "Product": "3a", "Yield (%)": "quant.", "ee (%)": "rac"},
            {"Entry": "2", "Product": "3c", "Yield (%)": "70", "ee (%)": "rac"},
            {"Entry": "3", "Product": "3f", "Yield (%)": "quant"},
            {"Entry": "4", "Product": "9z", "Yield (%)": "quant."}]))
        records = [ms_read._clean_record(r, "Table 1", "ms.pdf", "table", "high")
                   for r in recs]
        result = M.check(p, [ms_read.Source("ms.pdf", "pdf", records=records)])
        assert _problems(result) == {("yield", "3a"), ("ee", "3a"), ("missing_si", "9z")}
        by = {(f.kind, f.label): f for f in result.findings}
        assert by["yield", "3a"].message == "3a: the manuscript gives quant. yield, the SI 92%."
        assert "rac" in by["ee", "3a"].message and "95% ee" in by["ee", "3a"].message
        assert "quant. yield" in by["missing_si", "9z"].message
        assert by["yield", "3f"].status == M.OK          # quant. and >99% agree
        assert ("ee", "3c") not in by                    # racemic, no ee in the SI: fine
    finally:
        p.close()


def test_reader_version_was_raised():
    # What an older reader stored (with "–" read as an ee, or "quant." left out) is
    # read again.
    assert ms_read.READER_VERSION >= 4


# ---------------------------------------------------------------------- manuscript-3

def _two_footnotes():
    return [ms_read.Source("ms.pdf", "pdf", records=[
                {"id": "3b", "footnotes": ["b"], "where": "Scheme 2",
                 "conditions": {"b": "Reaction run for 48 h."}}]),
            ms_read.Source("ms.docx", "docx", records=[
                {"id": "3b", "footnotes": ["b"], "where": "Table 3",
                 "conditions": {"b": "Reaction run for 72 h."}}])]


def _conditions(result):
    return sorted((f for f in result.findings if f.kind == "conditions"),
                  key=lambda f: f.message)


def test_two_findings_with_one_key_are_dismissed_separately(tmp_path):
    p, _c = S.build_si(tmp_path)
    try:
        sources = _two_footnotes()
        a, b = _conditions(M.check(p, sources))
        assert a.key != b.key and a.status == b.status == M.PROBLEM
        M.dismiss(p, a, "known")
        M.dismiss(p, b, "known too")
        assert [f.status for f in _conditions(M.check(p, sources))] == [M.DISMISSED] * 2
        # Only one source: its footnote stays dismissed.
        (one,) = _conditions(M.check(p, sources[:1]))
        assert one.status == M.DISMISSED
        # Restore one: the other stays dismissed.
        a2, b2 = _conditions(M.check(p, sources))
        M.restore(p, a2.key)
        a3, b3 = _conditions(M.check(p, sources))
        assert (a3.status, b3.status) == (M.PROBLEM, M.DISMISSED)
    finally:
        p.close()


def test_dismissal_under_the_old_key_still_counts_and_can_be_restored(tmp_path):
    p, _c = S.build_si(tmp_path)
    try:
        sources = _two_footnotes()
        a, _b = _conditions(M.check(p, sources))
        old = a.key.rsplit(":", 1)[0]
        assert old == "conditions:3b:b"
        M.folder(p).mkdir(exist_ok=True)
        (M.folder(p) / M.DISMISSED_FILE).write_text(json.dumps(
            {old: {"fingerprint": a.fingerprint, "reason": "old"}}), encoding="utf-8")
        a2, b2 = _conditions(M.check(p, sources))
        assert (a2.status, a2.key, a2.reason) == (M.DISMISSED, old, "old")
        assert b2.status == M.PROBLEM
        M.restore(p, a2.key)
        assert M.dismissed(p) == {}
    finally:
        p.close()


# ---------------------------------------------------------------------- manuscript-4

def test_dismiss_after_a_file_changed_reads_it_again(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    page = U.ManuscriptPage(p, auto=False)
    page.run_check()
    rows = page.table.rowCount()
    assert rows > 1
    pdf = M.folder(p) / "ms.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n")          # saved again in another program
    changed = []
    page.changed.connect(lambda: changed.append(1))
    page.table.selectRow(0)
    page.dismiss_selected("x")
    assert not page.summary.text().startswith("0 compounds")
    assert page.summary.text().endswith("(1 dismissed)")
    assert page.table.rowCount() == rows - 1 and changed
    # Restore after another change: everything comes back.
    pdf.write_bytes(pdf.read_bytes() + b"\n")
    page.show_dismissed.setChecked(True)
    row = next(r for r in range(page.table.rowCount())
               if page.table.item(r, 5).text() == "Dismissed")
    page.table.selectRow(row)
    page.restore_selected()
    page.show_dismissed.setChecked(False)
    assert page.table.rowCount() == rows and "dismissed" not in page.summary.text()


# ---------------------------------------------------------------------- manuscript-5

def test_only_new_or_changed_files_are_read_again(si, monkeypatch):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    M.add_file(p, tmp / "scheme.cdxml")
    first = M.read_all(p)
    read = _count_reads(monkeypatch)
    # A ChemDraw file added: only that one is read.
    M.add_file(p, S.make_cdxml(tmp / "more.cdxml"))
    sources = M.read_all(p)
    assert read == ["more.cdxml"]
    assert [s.file for s in sources] == [f.name for f in M.files(p)]
    assert {s.file: s for s in sources}["ms.pdf"] is {s.file: s for s in first}["ms.pdf"]
    fresh = M.read_all(p, force=True)                  # "Check again" reads them all
    assert sorted(read[1:]) == ["more.cdxml", "ms.pdf", "scheme.cdxml"]
    assert M.check(p, sources).summary() == M.check(p, fresh).summary()
    # A file removed: nothing read. A file changed: only it.
    read.clear()
    M.remove_file(p, "more.cdxml")
    M.read_all(p)
    assert read == []
    pdf = M.folder(p) / "ms.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n")
    M.read_all(p)
    assert read == ["ms.pdf"]
    # Another session (only check.json left): a new file is the only one read.
    read.clear()
    M.forget(p)
    M.add_file(p, S.make_cdxml(tmp / "late.cdxml"))
    M.read_all(p)
    assert read == ["late.cdxml"]
    # A new reader: everything is read again.
    read.clear()
    M.forget(p)
    monkeypatch.setattr(ms_read, "READER_VERSION", ms_read.READER_VERSION + 1)
    M.read_all(p)
    assert sorted(read) == ["late.cdxml", "ms.pdf", "scheme.cdxml"]


# ---------------------------------------------------------------------- manuscript-6

def _compound(p, label_id):
    return next(c for s in p.data_sections() for c in s.compounds if c.id == label_id)


def test_structure_keys_follow_the_si_structure_through_undo(si):
    p, c, _tmp = si
    p.enable_undo()
    cid = c["3d"].id
    with p.undo_step("Structure"):
        p.set_structure(_compound(p, cid), "C[C@H](O)c1ccccc1")
    ms = "C[C@H](O)c1ccccc1"
    mol = p.read_structure(_compound(p, cid))
    assert M.compare_structures(ms, mol) == M.compare_structures(ms, mol)
    assert M.compare_structures(ms, mol)[0] == "same"
    with p.undo_step("Other enantiomer"):
        p.set_structure(_compound(p, cid), "C[C@@H](O)c1ccccc1")
    assert M.compare_structures(ms, p.read_structure(_compound(p, cid)))[0] == "stereo"
    p.undo()
    assert M.compare_structures(ms, p.read_structure(_compound(p, cid)))[0] == "same"
    p.redo()
    assert M.compare_structures(ms, p.read_structure(_compound(p, cid)))[0] == "stereo"


def test_general_procedure_read_once_per_check_and_edits_followed(si, monkeypatch):
    p, c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    sources = M.read_all(p)
    before = M.check(p, sources)
    reads = []
    real = p.read_gp_text
    monkeypatch.setattr(p, "read_gp_text", lambda gp_id: reads.append(gp_id) or real(gp_id))
    again = M.check(p, sources)
    assert len(reads) <= 1
    assert [(f.kind, f.label, f.status, f.message, f.fingerprint) for f in before.findings] == \
        [(f.kind, f.label, f.status, f.message, f.fingerprint) for f in again.findings]
    # The general procedure edited: the next check uses the new text.
    gp = p.compound_gp(c["3d"])
    assert ("deviation", "3d") in _problems(again)
    p.write_gp_text(gp["id"], "The aryl halide (0.20 mmol), the catalyst (5 mol%) and "
                              "K3PO4 (2.0 equiv) in THF were stirred at 60 °C for 36 h.")
    assert ("deviation", "3d") not in _problems(M.check(p, sources))


# ---------------------------------------------------------------------- manuscript-7

def test_hand_edited_dismissed_json_does_not_stop_the_check(si):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    sources = M.read_all(p)
    f = next(f for f in M.check(p, sources).problems if f.kind == "yield")
    (M.folder(p) / M.DISMISSED_FILE).write_text(json.dumps({f.key: "typo, fine"}),
                                                encoding="utf-8")
    assert ("yield", "3b") in _problems(M.check(p, sources))
    assert M.cached_result(p) is not None


def test_dismissed_json_with_a_bom_keeps_its_entries(si):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    problems = M.check(p, M.read_all(p)).problems
    M.dismiss(p, problems[0], "one")
    path = M.folder(p) / M.DISMISSED_FILE
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())     # saved by Notepad
    assert problems[0].key in M.dismissed(p)
    M.dismiss(p, problems[1], "two")
    assert {problems[0].key, problems[1].key} <= set(M.dismissed(p))


def test_damaged_dismissed_json_is_kept_and_not_listed(si):
    p, _c, tmp = si
    p.enable_undo()
    M.add_file(p, tmp / "ms.pdf")
    sources = M.read_all(p)
    problems = M.check(p, sources).problems
    path = M.folder(p) / M.DISMISSED_FILE
    damaged = '{"yield:3b": {"fingerprint": "x", "reason": "a reason",}'
    path.write_text(damaged, encoding="utf-8")
    with p.undo_step("Dismiss", where=[("manuscript", "manuscript")]):
        M.dismiss(p, problems[0], "new")
    [kept] = M.folder(p).glob("dismissed.damaged-*.json")   # (dated, as references.json's)
    assert kept.read_text(encoding="utf-8") == damaged
    assert [f.name for f in M.files(p)] == ["ms.pdf"]
    assert M.cached_sources(p) is not None            # (the copy is not a manuscript file)
    p.undo()
    assert path.read_text(encoding="utf-8") == damaged
    assert not kept.exists()


@pytest.mark.parametrize("damage", ["no name", "string source"])
def test_hand_edited_check_json_is_read_again(si, damage):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    M.read_all(p)
    M.forget(p)
    cache = M.folder(p) / M.CACHE_FILE
    data = json.loads(cache.read_text(encoding="utf-8"))
    if damage == "no name":
        data["sources"][0]["names"] = [{"id": "3a"}]
    else:
        data["sources"].append("not a source")
    cache.write_text(json.dumps(data), encoding="utf-8")
    assert M.cached_sources(p) is None
    assert M.cached_result(p) is None
    assert M.check(p, M.read_all(p)).checked == 6   # read again from the files


# ---------------------------------------------------------------------- manuscript-8

def test_removing_a_file_already_gone_updates_the_list(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    M.add_file(p, tmp / "scheme.cdxml")
    page = U.ManuscriptPage(p, auto=False)
    page.run_check()
    assert any(f.kind == "structure" for f in page.result.findings)
    told = []
    monkeypatch.setattr(U.QMessageBox, "question",
                        lambda *a, **k: U.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(U.QMessageBox, "warning", lambda *a, **k: told.append(a[2]))
    changed = []
    page.changed.connect(lambda: changed.append(1))
    (M.folder(p) / "scheme.cdxml").unlink()           # deleted outside the program
    page.files.setCurrentRow(1)
    page.remove_selected()
    assert len(told) == 1 and "no longer" in told[0] and "scheme.cdxml" in told[0]
    assert [page.files.item(i).data(U.ROLE) for i in range(page.files.count())] == ["ms.pdf"]
    assert not any(f.kind == "structure" for f in page.result.findings)
    assert changed and not p.list_trash()


# ---------------------------------------------------------------------- hunt-undo-7

def test_files_added_together_are_one_undo_step(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    p.enable_undo()
    page = U.ManuscriptPage(p, auto=False)
    told = []
    monkeypatch.setattr(U.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    (tmp / "notes.txt").write_text("x", encoding="utf-8")
    page.add_files("chemdraw", [str(tmp / "scheme.cdxml"), str(S.make_cdxml(tmp / "b.cdxml")),
                                str(tmp / "notes.txt")])
    assert [f.name for f in M.files(p)] == ["b.cdxml", "scheme.cdxml"]
    assert any("notes.txt" in t for t in told)
    step = p.undo()
    assert step.label == "Add 2 manuscript files"
    assert M.files(p) == []
    p.redo()
    assert [f.name for f in M.files(p)] == ["b.cdxml", "scheme.cdxml"]
    # One file: the label names it, as before.
    page.add_files("manuscript", [str(tmp / "ms.pdf")])
    assert p.undo().label == "Add manuscript file ms.pdf"


# ---------------------------------------------------------------------- hunt-external-5

def test_a_file_that_cannot_be_opened_just_now_is_read_again(si, monkeypatch):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    M.add_file(p, tmp / "scheme.cdxml")
    real = Path.read_bytes
    locked = {"ms.pdf"}

    def read_bytes(self):
        if self.name in locked:
            raise PermissionError(13, "locked")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    sources = M.read_all(p)
    pdf = next(s for s in sources if s.file == "ms.pdf")
    assert pdf.transient and "just now" in pdf.error and "damaged" not in pdf.error
    stored = json.loads((M.folder(p) / M.CACHE_FILE).read_text(encoding="utf-8"))
    assert list(stored["files"]) == ["scheme.cdxml"]
    assert [s["file"] for s in stored["sources"]] == ["scheme.cdxml"]
    M.forget(p)
    assert M.cached_sources(p) is None                 # a new session reads it again
    # Unlocked: the next (not forced) check reads it, and only it.
    locked.clear()
    read = _count_reads(monkeypatch)
    sources = M.read_all(p)
    assert read == ["ms.pdf"]
    assert not any(s.transient or s.error for s in sources)
    assert M.check(p, sources).checked == 6
    M.forget(p)
    assert M.cached_sources(p) is not None


def test_page_reads_a_file_that_could_not_be_opened_last_time(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    real = Path.read_bytes
    locked = {"ms.pdf"}
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(
        PermissionError(13, "locked")) if self.name in locked else real(self))
    page = U.ManuscriptPage(p, auto=False)
    page.run_check()
    assert page.result.checked == 0 and any("just now" in n for n in page.result.notes)
    locked.clear()
    page.run_check()                                   # (not "Check again")
    assert page.result.checked == 6


def test_a_damaged_file_is_still_remembered_as_damaged(si, monkeypatch):
    p, _c, tmp = si
    (tmp / "bad.pdf").write_bytes(b"%PDF-1.4 not really")
    M.add_file(p, tmp / "bad.pdf")
    (src,) = M.read_all(p)
    assert src.error and not src.transient
    M.forget(p)
    read = _count_reads(monkeypatch)
    assert M.read_all(p)[0].error == src.error and read == []


# ---------------------------------------------------------------------- handoffs (project)

def test_structure_file_not_readable_just_now_is_not_no_structure(si, monkeypatch):
    """An SI structure.mol that can't be read just now (offline, locked) is said so, not
    reported as 'the SI compound has none yet', and not compared."""
    p, _c, tmp = si
    M.add_file(p, tmp / "scheme.cdxml")
    sources = M.read_all(p)
    before = [f for f in M.check(p, sources).findings if f.kind == "structure"]
    assert before and not any("none yet" in f.message for f in before)
    asked = []

    def offline(compound, strict=False):
        asked.append(strict)
        raise OSError(22, "online-only file")
    monkeypatch.setattr(p, "read_structure", offline)
    after = [f for f in M.check(p, sources).findings if f.kind == "structure"]
    assert asked and all(asked)
    assert after and all(f.status == M.NOTE and "just now" in f.message for f in after)
    assert {f.label for f in after} == {f.label for f in before}


def test_onedrive_conflict_copies_of_own_files_are_not_manuscript_files(si):
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    for name in ("dismissed-OFFICE-PC.json", "check-LAPTOP.json", "Dismissed-PC.JSON"):
        (M.folder(p) / name).write_text("{}", encoding="utf-8")
    assert [f.name for f in M.files(p)] == ["ms.pdf"]
    # A user's own file of that name, added (renamed " (2)"), stays in the list.
    (tmp / "dismissed.json").write_text("{}", encoding="utf-8")
    assert M.add_file(p, tmp / "dismissed.json").name == "dismissed (2).json"
    assert [f.name for f in M.files(p)] == ["dismissed (2).json", "ms.pdf"]


# ---------------------------------------------------------------------- review (dismiss locked)

def test_dismiss_and_restore_with_dismissed_json_locked(qapp, si, monkeypatch):
    """dismissed.json that can't be opened just now is not written over: Dismiss and
    Restore on the Manuscript page say so in a plain message, and nothing changes."""
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    p.enable_undo()
    M.add_file(p, tmp / "ms.pdf")
    page = U.ManuscriptPage(p, auto=False)
    page.run_check()
    page.table.selectRow(0)
    page.dismiss_selected("first")
    path = M.folder(p) / M.DISMISSED_FILE
    before = path.read_bytes()
    told = []
    monkeypatch.setattr(U.QMessageBox, "warning", lambda *a, **k: told.append(a[2]))
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[1]))
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(
        PermissionError(13, "locked")) if self.name == M.DISMISSED_FILE else real(self))
    page.table.selectRow(0)
    page.dismiss_selected("second")
    page.show_dismissed.setChecked(True)
    row = next(r for r in range(page.table.rowCount())
               if page.table.item(r, 5).text() == "Dismissed")
    page.table.selectRow(row)
    page.restore_selected()
    assert errors == [] and len(told) == 2 and all("dismissed.json" in t for t in told)
    with pytest.raises(OSError):
        M.restore(p, next(iter(json.loads(before))))
    monkeypatch.setattr(Path, "read_bytes", real)
    assert path.read_bytes() == before
