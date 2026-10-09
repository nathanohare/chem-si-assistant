"""Audit fixes for HRMS reports and the PubChem name lookup (group "hrms"): the report
cache, a lookup cut short, the listing's line counts, zip files, mzXML memory and
hand-edited report names. All data are made up (tests/ms_samples.py)."""

import io
import threading
import time
import zipfile

import numpy as np
import pytest

from si_assistant.core import ms_reports as R
from si_assistant.core.project import Project
from si_assistant.ui import name_lookup
from tests import ms_samples as M


@pytest.fixture
def setup(tmp_path):
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.ensure_spectra_section()
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="made-up ester")
    p.set_structure(c, M.SMILES)
    yield p, c, tmp_path
    p.close()


def write_csv(path, offset_ppm):
    path.write_text(M.csv_text(M.sample_peaks(offset_ppm)), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- the report cache

@pytest.fixture
def counted(monkeypatch):
    """Counts the files actually read (not answered from the cache)."""
    R.clear_cache()
    reads = []
    original = R._read_uncached

    def counting(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(R, "_read_uncached", counting)
    yield reads
    R.clear_cache()


def test_more_than_64_reports_are_each_read_once(tmp_path, counted):
    """The warnings read every report in the same order on each autosave: with more files
    than a small cache holds, every file used to be read again every time."""
    files = [write_csv(tmp_path / f"r{n}.csv", 1.0 + n / 100) for n in range(100)]
    first = [R.read_report(f) for f in files]
    assert len(counted) == 100
    second = [R.read_report(f) for f in files]
    assert len(counted) == 100                      # nothing read again
    assert [d.mz.tolist() for d in first] == [d.mz.tolist() for d in second]


def test_a_changed_report_is_read_again(tmp_path, counted):
    f = write_csv(tmp_path / "r.csv", 1.2)
    before = R.read_report(f)
    f.write_text(M.csv_text([(301.1234, 100.0), (302.1267, 20.0)]), encoding="utf-8")
    after = R.read_report(f)
    assert len(counted) == 2 and after.mz.tolist() != before.mz.tolist()
    assert sorted(after.mz.tolist()) == [301.1234, 302.1267]


def test_the_cache_keeps_within_its_budget(tmp_path, counted, monkeypatch):
    monkeypatch.setattr(R, "_CACHE_BUDGET", 25)
    files = [write_csv(tmp_path / f"r{n}.csv", 1.0) for n in range(6)]   # 7 values each
    for f in files:
        R.read_report(f)
    assert R._cache_total <= 25 and len(R._CACHE) == 3
    assert R._cache_total == sum(w for _d, w in R._CACHE.values())
    # the most recent ones are kept and still give the right values
    n = len(counted)
    assert R.read_report(files[-1]).count == 6 and len(counted) == n
    assert R.read_report(files[0]).count == 6 and len(counted) == n + 1


def test_one_report_larger_than_the_budget_is_still_kept(tmp_path, counted, monkeypatch):
    monkeypatch.setattr(R, "_CACHE_BUDGET", 3)
    f = write_csv(tmp_path / "big.csv", 1.0)
    R.read_report(f)
    R.read_report(f)
    assert len(counted) == 1 and len(R._CACHE) == 1


# ---------------------------------------------------------------------- PubChem lookup cut short

class _SlowFake:
    """A made-up PubChem answer that takes a while (no network)."""

    def __init__(self, delay=0.4):
        self.delay = delay

    def __call__(self, url, timeout):
        time.sleep(self.delay)
        from si_assistant.core import pubchem
        raise pubchem.NotFound()


def _wait_for_lookup_threads():
    for t in threading.enumerate():
        if t.name == "pubchem-lookup":
            t.join(5)


def test_run_lookup_cut_short_gives_none(qapp, monkeypatch):
    """The wait ending before the answer (e.g. the program closing) used to raise
    KeyError('result')."""
    from PySide6.QtCore import QEventLoop

    class EndsAtOnce(QEventLoop):
        def exec(self, *args):
            return 0

    monkeypatch.setattr(name_lookup, "USE_THREAD", True)
    monkeypatch.setattr(name_lookup, "fetcher", _SlowFake(0.3))
    monkeypatch.setattr(name_lookup, "QEventLoop", EndsAtOnce)
    from si_assistant.core import chem
    assert name_lookup.run_lookup(chem.parse_structure("COC(=O)c1ccccc1")) is None
    _wait_for_lookup_threads()
    qapp.processEvents()


@pytest.fixture
def name_page(qapp, project, monkeypatch):
    from si_assistant.ui.compound_panel import CompoundPanel
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["3a"])
    c = project.add_compound(data.id)
    project.set_structure(c, "COC(=O)c1ccccc1")
    panel = CompoundPanel(project, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "name")
    monkeypatch.setattr(name_lookup, "USE_THREAD", True)
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (True, False))
    shown = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    yield project, panel, editor, shown
    _wait_for_lookup_threads()


def test_page_rebuilt_during_the_lookup(name_page, monkeypatch):
    """The lock check (a timer) can rebuild the page while the lookup waits: the editor is
    then gone, and touching it used to raise RuntimeError."""
    import shiboken6
    from PySide6.QtCore import QTimer
    project, panel, editor, shown = name_page
    monkeypatch.setattr(name_lookup, "fetcher", _SlowFake(0.4))

    def taken_over():
        project.read_only = True
        panel.deleteLater()

    QTimer.singleShot(50, taken_over)
    try:
        editor.look_up()                          # must not raise
        assert not shiboken6.isValid(editor) and shown == []
    finally:
        project.read_only = False


def test_project_read_only_after_the_lookup_is_not_filled_in(name_page, monkeypatch):
    from PySide6.QtCore import QTimer
    project, panel, editor, shown = name_page
    monkeypatch.setattr(name_lookup, "fetcher", _SlowFake(0.4))
    QTimer.singleShot(50, lambda: setattr(project, "read_only", True))
    try:
        editor.look_up()
        assert shown == [] and editor.widgets["text"].text() == ""
        assert not editor.lookup_btn.isEnabled()          # read-only: button off
    finally:
        project.read_only = False
        panel.deleteLater()


# ---------------------------------------------------------------------- listings

def _xlsx(sheets: list[list[list]]) -> bytes:
    """A minimal workbook with several sheets of inline-string/number cells (None: a
    formatted but empty cell, as Excel writes for a bordered or pre-formatted range)."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"><sheets/></workbook>')
        for n, rows in enumerate(sheets, 1):
            xml = []
            for r, row in enumerate(rows, 1):
                cells = "".join(
                    f'<c r="{chr(65 + c)}{r}" s="1"/>' if v is None
                    else f'<c r="{chr(65 + c)}{r}"><v>{v}</v></c>' if isinstance(v, (int, float))
                    else f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
                    for c, v in enumerate(row))
                xml.append(f'<row r="{r}">{cells}</row>')
            z.writestr(f"xl/worksheets/sheet{n}.xml",
                       f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml)}</sheetData>'
                       '</worksheet>')
    return buf.getvalue()


def _rows(n):
    return [["m/z", "Intensity"]] + [[100 + i / 1000, i] for i in range(n)]


def test_long_excel_listing_counts_every_line(tmp_path):
    """Sheets over 1150 rows used to say '1000 more lines' whatever their length."""
    from si_assistant.ui.hrms_reports_ui import _describe_file
    path = tmp_path / "long.xlsx"
    path.write_bytes(_xlsx([_rows(5000)]))
    lines, left = R.listing_lines(path)
    assert len(lines) == R.LISTING_LINES and left == 5001 - R.LISTING_LINES
    assert _describe_file(path) == f"listing: first {R.LISTING_LINES} of 5001 lines"
    # two sheets (with a blank line between them)
    path.write_bytes(_xlsx([_rows(2999), _rows(1999)]))
    lines, left = R.listing_lines(path)
    assert left == 3000 + 1 + 2000 - R.LISTING_LINES
    # short sheets are as before
    path.write_bytes(_xlsx([_rows(99)]))
    assert R.listing_lines(path) == (R.listing_lines(path)[0], 0)
    assert len(R.listing_lines(path)[0]) == 100


def _true_excel_listing(path, max_lines=R.LISTING_LINES, width=R.LISTING_WIDTH):
    """The Excel listing worked out with every row read (no cut)."""
    sheets = R.xlsx_rows(path.read_bytes(), max_rows=10 ** 9)
    lines = R._trimmed(R._aligned([r for s in sheets for r in s + [[]]]))
    left = max(0, len(lines) - max_lines)
    return [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines[:max_lines]], left


_EMPTY = [None, None]     # a row of formatted but empty cells


@pytest.mark.parametrize("sheets", [
    [_rows(100) + [_EMPTY] * 3000],                        # under the cut: nothing left out
    [_rows(5000) + [_EMPTY] * 3000],                       # over the cut
    [_rows(2000) + [_EMPTY] * 500],
    [_rows(1100) + [_EMPTY] * 200],                        # data ends just before the cut
    [_rows(100) + [_EMPTY] * 2000 + [[1, 2]]],             # blank rows before data past the cut
    [_rows(1200) + [_EMPTY] * 10 + [[1, 2]] + [_EMPTY] * 50],
    [_rows(3000) + [_EMPTY] * 40, _rows(20) + [_EMPTY] * 5],   # middle sheet's blank rows count
    [_rows(3000), [_EMPTY] * 2000],                        # a last sheet with no text at all
    [_rows(2000) + [_EMPTY] * 100, [_EMPTY] * 30, []],
], ids=lambda v: "")
def test_excel_listing_counts_formatted_empty_rows_as_blank(tmp_path, sheets):
    """Formatted but empty rows at the end print as blank lines and are left off, so they
    are not 'lines not printed' either."""
    path = tmp_path / "styled.xlsx"
    path.write_bytes(_xlsx(sheets))
    for max_lines, width in ((R.LISTING_LINES, R.LISTING_WIDTH), (0, 40), (1150, 110)):
        assert R.listing_lines(path, max_lines, width) ==             _true_excel_listing(path, max_lines, width), max_lines


def test_excel_listing_with_formatted_empty_rows_says_nothing_is_left_out(tmp_path):
    from si_assistant.ui.hrms_reports_ui import _describe_file
    path = tmp_path / "styled.xlsx"
    path.write_bytes(_xlsx([_rows(100) + [_EMPTY] * 3000]))
    lines, left = R.listing_lines(path)
    assert len(lines) == 101 and left == 0
    assert _describe_file(path) == "listing of 101 lines"
    path.write_bytes(_xlsx([_rows(5000) + [_EMPTY] * 3000]))
    assert R.listing_lines(path)[1] == 5001 - R.LISTING_LINES
    assert _describe_file(path) == f"listing: first {R.LISTING_LINES} of 5001 lines"


def _old_listing(path, max_lines=R.LISTING_LINES, width=R.LISTING_WIDTH):
    """The listing as it was worked out before (every line aligned, then cut)."""
    text = R._decode(path.read_bytes())
    if path.suffix.lower() in (".csv", ".tsv", ".tab"):
        lines = R._aligned(R._split_rows(text)[0])
    else:
        lines = [ln.expandtabs(8).rstrip() for ln in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    left = max(0, len(lines) - max_lines)
    return [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines[:max_lines]], left


def _table(n, delim=",", eol="\n", blank_every=0, tail="", final_eol=True):
    rows = ["Spectrum of made-up 3a", delim.join(["m/z", "Intensity", "Note"])]
    for i in range(n):
        rows.append(delim.join([f"{100 + i * 0.0137:.4f}", str(i * 7 % 1000),
                                "x" * (i % 23)]))
        if blank_every and i % blank_every == 0:
            rows.append("   ")
    text = eol.join(rows) + (eol if final_eol else "")
    return text + tail


@pytest.mark.parametrize("name, text", [
    ("a.csv", _table(3000)),
    ("b.csv", _table(3000, blank_every=7)),
    ("c.csv", _table(2500, tail=",,\n , ,\n\n")),            # delimiter-only rows at the end
    ("d.csv", _table(2600, eol="\r")),                       # old Mac line endings
    ("e.csv", _table(2400, final_eol=False)),
    ("f.tsv", _table(2200, delim="\t", tail="\t\t\n")),
    ("g.csv", _table(2300, delim=";", tail=";;\n")),
    ("h.tab", _table(2100, delim="  ")),                     # columns split by spaces
    ("i.csv", _table(40)),
    ("j.txt", _table(3000, tail="\n \t\n\n")),
    ("k.txt", _table(120, eol="\r\n")),
], ids=lambda v: v if isinstance(v, str) and len(v) < 10 else "")
def test_listing_is_the_same_as_before(tmp_path, name, text):
    """Only the printed lines are aligned now: the lines and the count left out must be
    exactly as before."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    for max_lines, width in ((R.LISTING_LINES, R.LISTING_WIDTH), (0, 40), (1, 2),
                             (2500, 110)):
        assert R.listing_lines(path, max_lines, width) == \
            _old_listing(path, max_lines, width), (name, max_lines)
    # after the file changes, the new content is listed
    path.write_text(text + "999.9999,1,new\n", encoding="utf-8", newline="")
    assert R.listing_lines(path) == _old_listing(path)


# ---------------------------------------------------------------------- zip files

def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zipped_raw_data_is_not_called_an_excel_file(tmp_path):
    path = tmp_path / "run 3a.zip"
    path.write_bytes(_zip({"run 3a.d/AcqData/MSScan.bin": b"\x00\x01"}))
    d = R.read_report(path)
    assert not d.readable and d.kind == "zip archive"
    assert "Excel" not in d.message and "MSConvert" in d.message
    assert R.print_kind(path) == "none"


def test_other_zip_files_say_what_to_do():
    d = R.read_bytes(_zip({"reports/3a.pdf": b"%PDF-1.4"}), "reports.zip")
    assert not d.readable and "unzip it" in d.message
    d = R.read_bytes(_zip({"content.xml": b"<x/>"}), "peaks.ods")
    assert not d.readable and "Save it as PDF, CSV or .xlsx" in d.message
    d = R.read_bytes(b"PK\x03\x04 not really a zip", "odd.zip")
    assert not d.readable


def test_workbooks_are_still_read_as_excel():
    book = M.xlsx([["m/z", "Intensity"], [301.1234, 100.0], [302.1267, 20.0]])
    for name in ("3a.xlsx", "3a", "3a.zip"):
        d = R.read_bytes(book, name)
        assert d.readable and d.kind == "Excel file" and d.count == 2, name
    with pytest.raises(R.ReportReadError, match="Excel"):     # a damaged .xlsx
        R.read_xlsx(b"PK\x03\x04junk")
    with pytest.raises(R.ReportReadError, match="Excel"):
        R.read_bytes(b"PK\x03\x04junk", "x.xlsx")


def test_text_starting_with_pk_is_read_as_text():
    d = R.read_bytes(b"PKa values of made-up 3a\nfound m/z 301.1234\n", "pka.txt")
    assert d.readable and 301.1234 in d.mz.tolist()


# ---------------------------------------------------------------------- mzXML memory

def test_mzxml_elements_are_cleared_as_read(monkeypatch):
    """Every <scan>/<peaks> element (with its base64 text) used to be kept until the
    whole file was read."""
    seen = []
    original = R.ET.iterparse

    def recording(*args, **kwargs):
        for event, elem in original(*args, **kwargs):
            seen.append(elem)
            yield event, elem

    monkeypatch.setattr(R.ET, "iterparse", recording)
    sp = np.array(M.sample_peaks())
    scans = [(sp[:, 0], sp[:, 1]), (sp[:, 0], sp[:, 1] * 2)]
    d = R.read_mzxml(M.mzxml(scans, ms2=True))
    monkeypatch.undo()
    assert d.readable and d.scans == 2 and d.count == len(sp)
    peaks_elems = [e for e in seen if R._local(e.tag) == "peaks"]
    assert peaks_elems and all(not e.text and not e.attrib for e in peaks_elems)
    # the same values as reading without the recording
    again = R.read_mzxml(M.mzxml(scans, ms2=True))
    assert again.mz.tolist() == d.mz.tolist()
    assert again.intensity.tolist() == d.intensity.tolist()


def test_mzxml_values_unchanged():
    sp = np.array(M.sample_peaks(1.2))
    expected = R.read_mzml(M.mzml([(sp[:, 0], sp[:, 1])]))
    for kwargs in ({}, {"precision": 64}, {"compress": True}, {"ms2": True}):
        d = R.read_bytes(M.mzxml([(sp[:, 0], sp[:, 1])], **kwargs), "3a.mzXML")
        assert d.readable and d.scans == 1 and d.count == expected.count, kwargs
        assert np.allclose(d.mz, expected.mz, rtol=1e-6), kwargs


# ---------------------------------------------------------------------- hand-edited names

def test_hand_edited_report_names_are_ignored(setup):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp / "3a peaks.csv", 1.2))
    bad = [{"file": 123}, {"file": "../structure.mol"}, {"file": "a\x00b"},
           {"file": ".."}, {"file": "..."}, {"file": str(tmp / "x.csv")}, {"file": "sub/x"}]
    c.values["hrms"]["reports"].extend(bad)
    assert [i["file"] for i in R.report_items(c)] == ["3a peaks.csv"]
    c.values["hrms"]["found"] = "123.4567"            # typed by hand
    c.values["hrms"].pop("found_from", None)
    message, match = R.differs_message(p, c)          # used to raise TypeError
    assert match is not None and match.file == "3a peaks.csv" and message
    assert len(R.compound_reports(p, c)) == 1
    # the hand-made entries stay in compound.json
    p.save_compound(c)
    assert all(b in c.values["hrms"]["reports"] for b in bad)
    # a number (or text, true…) typed in place of the whole list
    for odd in (5, True, "3a peaks.csv", {"file": "3a peaks.csv"}, None, 1.5):
        c.values["hrms"]["reports"] = odd
        assert R.report_items(c) == []
        assert R.compound_reports(p, c) == []
        R.differs_message(p, c)                       # (no error)


def test_safe_report_names():
    for good in ("3a peaks.csv", "run 3a.d", "HRMS (2).pdf", "x"):
        assert R.safe_report_name(good)
    for bad in (None, 5, "", ".", "..", "...", " . ", "a/b", "a\\b", "/abs.csv", "a\x00"):
        assert not R.safe_report_name(bad), bad


def test_a_name_with_a_nul_is_reported_missing(tmp_path):
    d = R.read_report(tmp_path / "a\x00b.csv")
    assert not d.readable and d.kind == "missing"


# ---------------------------------------------------------------------- handed over by other groups

def test_tolerance_must_be_a_positive_number(setup):
    """"nan" turned the out-of-tolerance check off; a negative value failed every compound."""
    p, _c, _tmp = setup
    for stored, expected in ((3, 3.0), ("2.5", 2.5), ("nan", 5.0), ("inf", 5.0), (-2, 5.0),
                             (0, 5.0), ("abc", 5.0), (None, 5.0), ([1], 5.0)):
        p.settings["hrms_tolerance_ppm"] = stored
        assert R.tolerance(p) == expected, stored


def _fails_once(monkeypatch, target):
    """Path.read_bytes raises PermissionError the first time `target` is read (a file open
    in another program, or online-only while offline)."""
    from pathlib import Path
    original = Path.read_bytes
    failed = []

    def read_bytes(self):
        if self == target and not failed:
            failed.append(self)
            raise PermissionError("locked")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return failed


@pytest.mark.parametrize("kind", ["pdf", "csv"])
def test_a_report_that_cant_be_opened_just_now_is_read_again(tmp_path, counted, monkeypatch,
                                                              kind):
    if kind == "pdf":
        f = M.agilent_like_pdf(tmp_path / "r.pdf")
    else:
        f = write_csv(tmp_path / "r.csv", 1.2)
    failed = _fails_once(monkeypatch, f)
    first = R.read_report(f)
    assert failed and not first.readable and first.transient
    assert "could not be opened just now" in first.message and not R._CACHE
    second = R.read_report(f)
    assert second.readable and second.count and len(counted) == 2
    R.read_report(f)                                  # now remembered
    assert len(counted) == 2


def test_a_damaged_pdf_is_still_remembered(tmp_path, counted):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"%PDF-1.4\nnot really a pdf")
    first = R.read_report(f)
    assert not first.readable and not first.transient and "bad.pdf" in first.message
    R.read_report(f)
    assert len(counted) == 1


def _structure_locked(monkeypatch, p, c):
    """structure.mol can't be read just now (the project's cached molecule is forgotten)."""
    from si_assistant.core import project as project_mod
    original = project_mod.read_text
    target = p.structure_path(c)

    def read_text(path, *a, **k):
        if path == target:
            raise PermissionError("locked")
        return original(path, *a, **k)

    p._structure_cache.clear()
    monkeypatch.setattr(project_mod, "read_text", read_text)


def test_an_unreadable_structure_does_not_clear_the_found_box(setup, monkeypatch):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp / "r.csv", 1.2))
    value = c.values["hrms"]
    found, source = value["found"], dict(value["found_from"])
    assert found
    _structure_locked(monkeypatch, p, c)
    assert R.update_found(p, c) == "" and p.refresh_hrms_found() == 0
    assert value["found"] == found and value["found_from"] == source
    assert R.compound_match(p, c) is None                  # (on screen: no match shown)
    value["found"] = "222.0000"                            # typed: no warning while unknown
    assert R.differs_message(p, c) == ("", None)
    with pytest.raises(OSError):
        R.expected_peaks(p, c)


def test_a_report_unreadable_just_now_does_not_clear_the_found_box(setup, monkeypatch):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp / "r.csv", 1.2))
    value = c.values["hrms"]
    found = value["found"]
    R.clear_cache()
    target = R.report_path(c, "r.csv")
    _fails_once(monkeypatch, target)
    assert R.update_found(p, c) == "" and value["found"] == found
    assert R.update_found(p, c) == "" and value["found"] == found     # read again: same


@pytest.fixture
def reports_box(qapp, setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from si_assistant.ui.hrms_reports_ui import HrmsReportsBox
    p, c, tmp = setup
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    box = HrmsReportsBox(p, c, lambda: None, lambda: "3a")
    said = []
    box.saved.connect(said.append)
    yield p, c, tmp, box, said, warned
    box.deleteLater()


def test_adding_reports_says_how_many_were_added(reports_box):
    p, c, tmp, box, said, warned = reports_box
    good = write_csv(tmp / "a.csv", 1.2)
    box.add_paths([good, tmp / "missing.csv"])
    assert said == ["Added 1 of 2 files"] and len(warned) == 1
    box.add_paths([tmp / "gone.csv"])                  # none added: only the warning
    assert said == ["Added 1 of 2 files"] and len(warned) == 2
    box.add_paths([write_csv(tmp / "b.csv", 1.0)])
    assert said[-1] == "Added b.csv"


def test_an_ampersand_in_a_report_name_is_shown(reports_box):
    from PySide6.QtWidgets import QPushButton
    p, c, tmp, box, said, warned = reports_box
    box.add_paths([write_csv(tmp / "a&b.csv", 1.2)])
    link = next(w for w in box.findChildren(QPushButton) if "a&" in w.toolTip())
    assert link.text().endswith("a&&b.csv") and link.toolTip().startswith("a&b.csv\n")


def test_the_paste_tooltip_names_this_computers_key(reports_box):
    from si_assistant.ui.structure_widgets import paste_key
    box = reports_box[3]
    assert f"press {paste_key()} does" in box.paste_btn.toolTip()


def test_a_password_protected_pdf_report_is_called_so(tmp_path, monkeypatch):
    import pypdf
    from pypdf.errors import DependencyError
    from si_assistant.ui.hrms_reports_ui import describe_file

    class Locked:
        def __init__(self, *a, **k):
            raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    f = tmp_path / "enc.pdf"
    f.write_bytes(b"%PDF-1.7\nlocked")
    monkeypatch.setattr(pypdf, "PdfReader", Locked)
    assert describe_file(f) == "password-protected PDF: not printed"
    damaged = tmp_path / "bad.pdf"
    damaged.write_bytes(b"%PDF-1.7\ndamaged")
    monkeypatch.setattr(pypdf, "PdfReader", lambda *a, **k: 1 / 0)
    assert describe_file(damaged) == "PDF that can't be opened: not printed"


def test_a_hand_edited_name_value_still_opens_the_page(qapp, project):
    """"name": "foo" in compound.json (not {"text": …}) stopped the compound page opening."""
    from si_assistant.ui.compound_panel import CompoundPanel
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["3a"])
    c = project.add_compound(data.id)
    c.values["name"] = "foo"
    panel = CompoundPanel(project, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "name")
    assert editor._from is None and editor.widgets["text"].text() == ""
    panel.deleteLater()


# ---------------------------------------------------------------------- last round

def test_a_huge_whole_number_tolerance_gives_5(setup):
    """float(10**400) raises OverflowError: every HRMS check crashed on such a project.json."""
    p, _c, _tmp = setup
    p.settings["hrms_tolerance_ppm"] = 10 ** 400
    assert R.tolerance(p) == 5.0


def test_nothing_is_written_when_the_pages_typing_could_not_be_saved(reports_box):
    """before_write returns False when the page's typing couldn't be saved (it has said so):
    adding, removing and the print choice then stop instead of going on."""
    p, c, tmp, box, said, warned = reports_box
    box.add_paths([write_csv(tmp / "a.csv", 1.2)])
    assert [i["file"] for i in R.report_items(c)] == ["a.csv"]
    said.clear()
    box.before_write = lambda: False
    box.add_paths([write_csv(tmp / "b.csv", 1.0)])
    box.remove_now("a.csv")
    assert [i["file"] for i in R.report_items(c)] == ["a.csv"] and not said and not warned
    box.print_box.setCurrentIndex(2)                   # "Don't print them"
    box._print_chosen(2)
    assert R.PRINT_KEY not in c.values["hrms"] and box.print_box.currentIndex() == 0
    assert not said
    box.before_write = lambda: None                    # (None, as in other tests: goes on)
    box._print_chosen(2)
    assert c.values["hrms"][R.PRINT_KEY] is False and said == ["Saved"]
