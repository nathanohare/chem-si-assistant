"""Regression tests for the review of SPEC §13n (one or more per finding: U = using it,
B = bugs, S = against the spec). Made-up structures and files only."""

import io
import json
import struct
import time
import zipfile

import pytest
from PySide6.QtCore import QMimeData, QPoint, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from si_assistant.compile.document import CompileOptions
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.core import drawing_scale as ds
from si_assistant.core import markup
from si_assistant.core import ms_reports as ms
from si_assistant.core import scheme_editor as se
from si_assistant.core.markup import Paragraph, Run, parse, serialize
from si_assistant.core.project import Project
from tests import structure_samples as ss
from tests.test_scheme_editor import chemdraw_picture, new_project, scheme_call, source


def editor_widget(p, k, qapp, width=1100, height=600):
    from si_assistant.ui.scheme_editor_ui import SchemeEditorWidget
    w = SchemeEditorWidget(p, k)
    w.resize(width, height)
    w.show()
    w.activateWindow()
    qapp.processEvents()
    return w


def settle(qapp, rounds=5):
    for _ in range(rounds):
        qapp.processEvents()
        QApplication.sendPostedEvents(None, 0)


# ---------------------------------------------------------------------- U1

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"),
                                             QSettings.Format.IniFormat))
    wins = []

    def make(p):
        p.close()
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        assert win.open_project(p.folder)
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def test_u1_a_new_scheme_page_has_no_blank_compound(window, tmp_path):
    p, data = new_project(tmp_path)
    win = window(p)
    win.select("section", data.id)
    win.new_scheme()
    k = win.project.section(data.id).schemes[0]
    assert k.data["mode"] == "editor" and k.children == []   # nothing unnamed, no "ID?"
    assert len(se.all_boxes(k)) == 2
    r = compile_pdf(win.project, CompileOptions.from_settings(win.project.settings),
                    image_info, convert_to_png)
    assert not any("ID?" in w or "no ID" in w for w in r.warnings)


# ---------------------------------------------------------------------- B1

def test_b1_enter_in_an_id_label_or_yield_saves_once_without_recursion(tmp_path, qapp):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CC=O")
    w = editor_widget(p, k, qapp)
    steps_before = len(p.journal.undo_stack)
    box = w.boxes[0]
    box.label_edit.setFocus()
    qapp.processEvents()
    QTest.keyClicks(box.label_edit, "S1")
    QTest.keyClick(box.label_edit, Qt.Key.Key_Return)     # (RecursionError before)
    settle(qapp)
    assert se.starting(k)[0]["label"] == "S1"
    assert len(p.journal.undo_stack) == steps_before + 1   # one undo step, saved once
    product = next(b for b in w.boxes if b.where != se.START)
    w.activateWindow()
    product.yield_edit.setFocus()
    qapp.processEvents()
    QTest.keyClicks(product.yield_edit, "92%")
    QTest.keyClick(product.yield_edit, Qt.Key.Key_Return)
    settle(qapp)
    assert se.steps(k)[0]["products"][0]["yield"] == "92%"
    # A save from elsewhere (e.g. before compiling) while the label is being typed.
    box = w.boxes[0]
    w.activateWindow()
    box.label_edit.setFocus()
    qapp.processEvents()
    assert box.label_edit.hasFocus()
    QTest.keyClicks(box.label_edit, "x")
    w.flush()
    settle(qapp)
    assert se.starting(k)[0]["label"] == "S1x"
    w.close()


# ---------------------------------------------------------------------- S1 underline

@pytest.mark.parametrize("text", [
    "__under__ plain", "a\\_b and \\_\\_init\\_\\_", "CDCl_{3}", "___{2}__ x",
    "__a__\\_b", "@{c-1} __@cite{r-1}__"])
def test_s1_underline_round_trips_with_literal_underscores_and_subscripts(text):
    assert serialize(parse(text)) == text


@pytest.mark.parametrize("text", ["**__bold under__** *__it__*", "x^{__2__}",
                                  "__**a** b__ c", "__x_{2}__"])
def test_s1_underline_with_other_markers_keeps_its_meaning(text):
    once = serialize(parse(text))
    assert parse(once) == parse(text) and serialize(parse(once)) == once


def test_s1_underline_markup_meaning():
    runs = parse("__a__ b_{2} \\_\\_c")[0].runs
    assert runs[0] == Run("a", underline=True)
    assert [r.text for r in runs] == ["a", " b", "2", " __c"]
    assert runs[2].valign == markup.SUB and not runs[2].underline
    sub = parse("___{2}__")[0].runs[0]
    assert sub.underline and sub.valign == markup.SUB and sub.text == "2"
    # Text saved before underline existed (underscores always escaped) reads as before.
    assert parse("a\\_b_{1}")[0].runs == [Run("a_b"), Run("1", valign=markup.SUB)]
    assert serialize([Paragraph([Run("x_y", underline=True)])]) == "__x\\_y__"


def test_s1_underline_compiles_and_survives_formatting():
    from si_assistant.compile.text import format_chemistry, runs_to_typst
    runs = format_chemistry([Run("in CDCl3", underline=True)])
    assert all(r.underline for r in runs)
    code = runs_to_typst(runs)
    assert code.count("#underline[") == len(runs) and "#sub[" in code


def test_s1_underline_button_ctrl_u_and_html_paste(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    ed = RichTextEditor(None)
    ed.show()
    ed.edit.setFocus()
    qapp.processEvents()
    assert ed.underline_act.text() == "U"
    QTest.keyClick(ed.edit, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(ed.edit, "abc")
    QTest.keyClick(ed.edit, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(ed.edit, " d_e")
    assert ed.markup() == "__abc__ d\\_e"
    assert ed.underline_act.isEnabled()
    # Pasted from Word: underline kept, a hyperlink's underline not.
    ed.set_markup("")
    data = QMimeData()
    data.setHtml("<p><u>under</u> and <a href='https://example.org'>a link</a></p>")
    ed.edit.insertFromMimeData(data)
    assert ed.markup() == "__under__ and a link"
    # The editor shows stored underline (round trip through the document).
    ed.set_markup("x __y__ CHCl_{3}")
    assert ed.markup() == "x __y__ CHCl_{3}"
    ed.close()


def test_s1_scheme_conditions_print_underline(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CC=O")
    se.set_conditions(p, k, se.steps(k)[0]["id"], "top", "__NaH__")
    src, result = source(p, k)
    assert '#underline[#"NaH"]' in scheme_call(src)


# ---------------------------------------------------------------------- S2

def test_s2_a_compound_not_drawn_in_the_editor_prints_its_own_structure(tmp_path):
    from si_assistant.ui.compound_panel import scheme_note
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, product, "c1ccccc1O")
    drawn = se.link_new_compound(p, k, product)
    blank = p.add_compound(data.id, scheme_id=k.id, name="side product")
    p.set_structure(blank, "c1ccncc1")
    src, _r = source(p, k)
    after = src[src.index("side product"):]
    assert "#si-structure(" in after                       # its own structure prints
    before = src[src.index("#si-reaction-scheme("):src.index("side product")]
    assert "#si-structure(" not in before                  # the drawn one: in the scheme
    assert "not drawn in the scheme" in scheme_note(k, blank)
    assert "not printed (the scheme is)" in scheme_note(k, drawn)


# ---------------------------------------------------------------------- U2

def test_u2_the_editor_is_as_tall_as_its_content_and_its_scrollbar_reachable(tmp_path, qapp):
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    for i in range(3):
        st = se.steps(k)[-1] if i == 0 else se.add_step(p, k)
        se.set_box_structure(p, k, st["products"][0]["id"], "c1ccc2ccccc2c1" + "C" * i)
        se.link_new_compound(p, k, st["products"][0]["id"])
    panel = SchemePanel(p, k, lambda: "Tester")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.resize(900, 760)
    panel.show()
    settle(qapp, 10)
    ed = panel.editor
    content = ed.strip.sizeHint().height()
    assert ed.scroll.need >= content and ed.scroll.need > 200
    ed.boxes[0].label_edit.setText("S9")
    ed.boxes[0]._label_typed()                          # (shrank to 200 px before)
    settle(qapp, 10)
    ed = panel.editor
    assert ed.scroll.need >= ed.strip.sizeHint().height()
    # The scroll area (with its horizontal scroll bar at the bottom) lies inside the page.
    bar = ed.scroll.horizontalScrollBar()
    bottom = ed.scroll.mapTo(panel, ed.scroll.rect().bottomLeft()).y()
    assert bottom <= panel.height() and ed.scroll.height() >= ed.scroll.minimumHeight()
    assert ed.scroll.height() >= min(ed.scroll.need, 150) and bar.maximum() >= 0
    # The divider gives the scheme what it needs when there is room; dragging is kept.
    top, rest = panel.splitter.sizes()
    assert top >= min(panel.scheme_area.sizeHint().height(), (top + rest) // 2) - 2
    panel.splitter.splitterMoved.emit(100, 1)
    panel.splitter.setSizes([0, top + rest])            # (as small as it goes)
    dragged = panel.splitter.sizes()
    assert dragged[0] < top
    panel.fit_divider()
    assert panel.splitter.sizes() == dragged
    panel.close()


# ---------------------------------------------------------------------- U3

def test_u3_layout_example_does_not_fill_the_product_black(qapp):
    from si_assistant.ui.scheme_editor_ui import LayoutExample
    w = LayoutExample()
    w.resize(420, 140)
    image = w.grab().toImage()
    dark = sum(1 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)
               if QColor(image.pixel(x, y)).lightness() < 60)
    assert dark / ((image.width() // 2) * (image.height() // 2)) < 0.05


# ---------------------------------------------------------------------- B2, B3, B4

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def xlsx_bytes(sheet_xml: str, shared: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", shared)
    return buf.getvalue()


def test_b2_xlsx_cells_far_right_are_ignored_or_kept_sparsely():
    import tracemalloc
    content = xlsx_bytes(f'<worksheet {NS}><sheetData><row><c r="ZZZZZZZ1"><v>1</v></c>'
                         f'<c r="A1"><v>2</v></c><c r="XFD1"><v>3</v></c></row>'
                         f'<row><c r="B2"><v>188.1073</v></c></row></sheetData></worksheet>')
    tracemalloc.start()
    started = time.perf_counter()
    rows = ms.xlsx_rows(content)[0]
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert time.perf_counter() - started < 1.0 and peak < 5e6
    assert rows == [["2", "", "3"], ["", "188.1073", ""]]   # (A, B, XFD: used columns only)
    # Shared-string indexes that aren't numbers, or negative, give empty cells.
    bad = xlsx_bytes(f'<worksheet {NS}><sheetData><row><c r="A1" t="s"><v>abc</v></c>'
                     f'<c r="B1" t="s"><v>-1</v></c></row></sheetData></worksheet>',
                     f'<sst {NS}><si><t>m/z</t></si></sst>')
    assert ms.xlsx_rows(bad) == [[["", ""]]]
    # Many rows each in another column: the cells kept are bounded.
    many = "".join(f'<row><c r="{chr(65 + i % 26)}{chr(65 + i // 26 % 26)}{i + 1}"><v>1</v></c>'
                   f'</row>' for i in range(3000))
    rows = ms.xlsx_rows(xlsx_bytes(f'<worksheet {NS}><sheetData>{many}</sheetData>'
                                   '</worksheet>'))[0]
    assert len(rows) * len(rows[0]) <= ms.XLSX_MAX_CELLS and len(rows[0]) <= ms.XLSX_MAX_COLUMNS


def _nc_name(s: str) -> bytes:
    b = s.encode()
    return struct.pack(">I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)


def netcdf_header(numrecs: int, vsize: int) -> bytes:
    out = b"CDF\x01" + struct.pack(">I", numrecs)
    out += struct.pack(">II", 10, 1) + _nc_name("t") + struct.pack(">I", 0)
    out += struct.pack(">II", 0, 0)
    names = ["mass_values", "intensity_values", "scan_index"]
    body = struct.pack(">II", 11, len(names))
    begin = len(out) + 8 + sum(len(_nc_name(v)) + 28 for v in names)
    for v in names:
        body += _nc_name(v) + struct.pack(">II", 1, 0) + struct.pack(">II", 0, 0)
        body += struct.pack(">III", 6, vsize, begin)
    return out + body + b"\0" * 64


@pytest.mark.parametrize("numrecs, vsize", [(0xFFFFFFFF, 0), (0xFFFFFFFF, 8),
                                            (4_000_000_000, 8), (100000, 0)])
def test_b3_netcdf_record_counts_are_bounded_by_the_file(numrecs, vsize):
    started = time.perf_counter()
    data = ms.read_bytes(netcdf_header(numrecs, vsize), "x.cdf") if vsize == 0 else None
    if vsize:
        try:
            data = ms.read_bytes(netcdf_header(numrecs, vsize), "x.cdf")
        except ms.ReportReadError:
            data = None
    assert time.perf_counter() - started < 1.0
    assert data is None or data.count == 0


def test_b3_jcamp_repeat_counts_are_bounded():
    text = "##TITLE=x\n##XYDATA=(X++(Y..Y))\n100 A1S99999999999\n##END=\n"
    started = time.perf_counter()
    with pytest.raises(ms.ReportReadError):
        ms.read_jcamp(text)
    assert time.perf_counter() - started < 1.0


def damaged_xlsx() -> bytes:
    good = bytearray(xlsx_bytes(f'<worksheet {NS}><sheetData>' + "".join(
        f'<row><c r="A{i}"><v>{i}.1234</v></c></row>' for i in range(1, 400))
        + '</sheetData></worksheet>'))
    start = good.index(b"xl/worksheets/sheet1.xml") + len(b"xl/worksheets/sheet1.xml")
    for i in range(start + 20, start + 60):   # (the packed sheet: a zlib error on reading)
        good[i] ^= 0x5A
    return bytes(good)


def test_b4_a_damaged_report_never_stops_the_listing_page_or_compile(tmp_path, qapp,
                                                                     monkeypatch):
    path = tmp_path / "damaged.xlsx"
    path.write_bytes(damaged_xlsx())
    lines, left = ms.listing_lines(path)
    assert ms.listing_failed(lines) and left == 0
    assert not ms.read_report(path).readable
    for error in (ValueError, IndexError, NotImplementedError, RuntimeError):
        def boom(*_a, e=error, **_k):
            raise e("damaged")
        monkeypatch.setattr(ms, "xlsx_rows", boom)
        assert ms.listing_failed(ms.listing_lines(path)[0])
    monkeypatch.undo()
    from si_assistant.ui.hrms_reports_ui import HrmsReportsPage, describe_file
    assert "could not be read" in describe_file(path)
    p = Project.create(tmp_path / "proj", "H")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.ensure_spectra_section()
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="x")
    p.set_structure(c, "CCOC(=O)c1ccccc1")
    p.add_hrms_report(c, path)
    p.set_hrms_print(c, True)
    page = HrmsReportsPage(p)                          # (crashed before)
    assert "could not be read" in page.tree.topLevelItem(0).child(0).text(1)
    # U8: the "Printed" column is short enough not to be cut off (and fills the width).
    assert page.tree.header().stretchLastSection()
    assert len(page.tree.topLevelItem(0).child(0).text(1)) <= 45
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                         convert_to_png)
    assert result.pdf.exists()
    assert any("damaged.xlsx could not be read" in w for w in result.warnings)


# ---------------------------------------------------------------------- B5

SVG_EMPTY = ("<svg xmlns='http://www.w3.org/2000/svg' width='100' height='50' "
             "viewBox='0 0 100 50'><polygon points='' fill='black'/>"
             "<polyline points='1,2' fill='black'/><polygon points='1' stroke='black'/>"
             "<line x1='0' y1='0' x2='10' y2='0' stroke='black'/></svg>")


def test_b5_svg_measuring_never_raises(tmp_path):
    assert ds.svg_segments(SVG_EMPTY) is not None
    deep = ("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'>" + "<g>" * 5000
            + "<line x1='0' y1='0' x2='5' y2='0' stroke='black'/>" + "</g>" * 5000 + "</svg>")
    assert ds.svg_segments(deep) is not None and ds.bond_length_pt(deep) is None
    assert ds.svg_segments('<?xml version="1.0" encoding="foo"?><svg/>') is None
    assert ds.bond_length_pt('<?xml version="1.0" encoding="foo"?><svg/>') is None
    bad = tmp_path / "bad.svg"
    bad.write_text(deep, encoding="utf-8")
    assert ds.picture_bond_pt({}, bad) is None


def test_b5_pictures_that_cant_be_measured_are_stored_without_orphans(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    f = tmp_path / "pic.svg"
    f.write_text(SVG_EMPTY, encoding="utf-8")
    box = se.starting(k)[0]
    se.set_box_picture(p, k, box["id"], source=f)
    se.set_box_picture(p, k, box["id"], source=f)       # (replaced: the old one goes)
    files = sorted(x.name for x in (k.folder / se.FILES_DIR).iterdir())
    assert files == [se.find_box(k, box["id"])[0]["picture"].split("/")[1]]
    c = p.add_compound(data.id, name="c")
    p.set_structure_image(c, source=f)
    assert p.structure_image_path(c).exists()


# ---------------------------------------------------------------------- S3

def test_s3_a_saved_picture_scheme_opens_the_editor_unlocked(tmp_path, qapp):
    import json
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    (k.folder / "scheme.json").write_text(json.dumps(   # (v0.1.0: a saved picture scheme)
        {"id": k.id, "name": "Old", "children": [], "image": "", "saved": True}),
        encoding="utf-8")
    p.reload()
    k = p.scheme(k.id)
    assert not se.is_locked(k)
    w = editor_widget(p, k, qapp)
    assert not w.locked and not w.save_btn.isHidden()
    w.set_locked(True)
    assert se.is_locked(k)
    w.close()


# ---------------------------------------------------------------------- U4, U5

def wide_scheme(p, data, steps=5):
    k = p.add_scheme(data.id, name="Long")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "c1ccc2ccccc2c1CCCCCCCC")
    for i in range(steps):
        st = se.steps(k)[-1] if i == 0 else se.add_step(p, k)
        se.set_conditions(p, k, st["id"], "top", "Pd(OAc)2 (5 mol%), XPhos (10 mol%), K3PO4")
        se.set_box_structure(p, k, st["products"][0]["id"], "c1ccc2ccccc2c1CCCCCC" + "C" * i)
    return k


def test_u4_shrunk_conditions_below_6_pt_give_a_compile_warning(tmp_path, qapp):
    p, data = new_project(tmp_path)
    k = wide_scheme(p, data)
    opts = CompileOptions.from_settings(p.settings)
    r = compile_pdf(p, opts, image_info, convert_to_png)
    assert not any("print at only" in w for w in r.warnings)       # wrapped: 9 pt
    se.set_scheme_layout(p, k, {"fit": "shrink"})
    r = compile_pdf(p, opts, image_info, convert_to_png)
    small = [w for w in r.warnings if "print at only" in w]
    assert len(small) == 1 and "Long" in small[0]
    # The Layout window says so for a small scale.
    from si_assistant.ui.scheme_editor_ui import LayoutForm
    form = LayoutForm({**se.DEFAULT_LAYOUT, "scale_pct": 50.0})
    assert "4.5 pt" in form.size_note.text() and "⚠" in form.size_note.text()


def test_u5_ids_of_a_row_line_up(tmp_path, qapp):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "C1CCCCC1")
    se.set_box_label(p, k, se.starting(k)[0]["id"], "1")
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "c1ccc2cc3ccccc3cc2c1CCC(C)(C)CCC")
    se.link_new_compound(p, k, product)
    assert se.scheme_layout(p.settings, k)["align_ids"] is True
    src, result = source(p, k)
    assert "align-ids: true" in scheme_call(src) and not result.warnings
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "align_ids": False})
    assert se.layout_overrides(k) == {"align_ids": False}
    src, _r = source(p, k)
    assert "align-ids: false" in scheme_call(src)
    # Project default (Project settings ▸ Schemes) off, the scheme's own on.
    se.set_project_layout(p.settings, {**se.DEFAULT_LAYOUT, "align_ids": False})
    assert p.settings["schemes"] == {"align_ids": False}
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "align_ids": True})
    assert se.scheme_layout(p.settings, k)["align_ids"] is True
    # Compiled in every style.
    for style in ("acs", "wiley", "rsc"):
        _src, result = source(p, k, style=style)
        assert not result.warnings
    # On screen: the IDs under the tallest structure.
    w = editor_widget(p, k, qapp)
    canvases = [b.canvas for b in w.boxes]
    assert len({c.label_rect().top() for c in canvases}) == 1
    w.close()


# ---------------------------------------------------------------------- B6

def test_b6_a_typed_bond_length_is_always_used(qapp, tmp_path):
    settings = {}
    target = ds.target_bond_pt(settings)
    assert ds.structure_print_factor(settings, {ds.OVERRIDE_KEY: 1.0}, None) \
        == pytest.approx(target / 1.0)                      # 14.4×: typed, so used
    assert ds.structure_print_factor(settings, {ds.MEASURED_KEY: 1.0}, None) == 1.0
    assert ds.structure_print_factor(settings, {ds.OVERRIDE_KEY: True}, None) == 1.0
    assert ds.structure_print_factor(settings, {ds.OVERRIDE_KEY: 0.01}, None) \
        == pytest.approx(target / 0.5)                      # (the dialog's own limits)
    from si_assistant.ui.structure_widgets import PictureScaleDialog
    p, _data = new_project(tmp_path)
    dialog = PictureScaleDialog(None, p, None, bond=(14.4, 1.0))
    assert not dialog.extreme.isHidden() and "1440%" in dialog.extreme.text()
    dialog.bond.setValue(14.0)
    assert dialog.extreme.isHidden()


# ---------------------------------------------------------------------- S4, B7

def scheme_with_inline(p, data, name):
    k = p.add_scheme(data.id, name=name)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CC=O")
    sid = se.add_inline_structure(p, k, ss.structure_molblock(), chemdraw_picture())
    se.set_conditions(p, k, se.steps(k)[0]["id"], "top", f"NaH @struct{{{sid}}} THF")
    return k, sid


def test_s4_structures_in_conditions_have_their_own_printed_size(tmp_path, qapp, monkeypatch):
    p, data = new_project(tmp_path)
    k, sid = scheme_with_inline(p, data, "A")
    entry = se.inline_entry(k, sid)
    picture = se.inline_path(k, sid)
    auto = se.box_print_factor(p.settings, entry, picture)
    se.set_inline_bond(p, k, sid, 28.8)
    assert se.inline_entry(k, sid)[ds.OVERRIDE_KEY] == 28.8
    assert auto == pytest.approx(ds.target_bond_pt(p.settings) / 14.4, rel=0.02)
    assert se.box_print_factor(p.settings, se.inline_entry(k, sid), picture) \
        == pytest.approx(ds.target_bond_pt(p.settings) / 28.8, rel=0.001)
    # Right-click on the structure in the text: "Printed size of the picture…".
    from si_assistant.ui import scheme_editor_ui as ui
    w = editor_widget(p, k, qapp)
    edit = w.steps[0].top.text.edit
    found = ""
    for x in range(0, edit.viewport().width(), 3):
        for y in range(0, edit.viewport().height(), 3):
            found = found or edit.structure_at(QPoint(x, y))
    assert found == sid

    class FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return ui.QDialog.DialogCode.Accepted

        def choice(self):
            return 0.0
    monkeypatch.setattr(ui, "PictureScaleDialog", FakeDialog)
    w.inline.picture_size(sid)
    assert se.inline_entry(p.scheme(k.id), sid)[ds.OVERRIDE_KEY] == 0.0
    assert p.can_undo
    w.close()


def test_b7_conditions_with_a_structure_pasted_into_another_scheme(tmp_path, qapp):
    p, data = new_project(tmp_path)
    a, sid = scheme_with_inline(p, data, "A")
    b, _other = scheme_with_inline(p, data, "B")
    se.set_conditions(p, b, se.steps(b)[0]["id"], "top", "")
    wa = editor_widget(p, a, qapp)
    wb = editor_widget(p, b, qapp)
    ea = wa.steps[0].top.text.edit
    ea.selectAll()
    copied = ea.createMimeDataFromSelection()
    eb = wb.steps[0].top.text.edit
    eb.insertFromMimeData(copied)
    wb.steps[0].save(wb.steps[0].top)
    b = p.scheme(b.id)
    top = se.steps(b)[0]["top"]
    new = se.inline_ids(top)
    assert len(new) == 1 and new[0] != sid and new[0] in se.editor_data(b)["inline"]
    assert se.inline_path(b, new[0]).parent == b.folder / se.FILES_DIR
    assert se.inline_path(b, new[0]).with_suffix(".emf").exists()   # (with its original)
    _src, result = source(p, b)
    assert not result.warnings
    # Pasted into the same scheme: the same structure.
    eb2 = wa.steps[0].bottom.text.edit       # (no structures under the arrow: left out)
    eb2.insertFromMimeData(copied)
    assert "@struct" not in wa.steps[0].bottom.text.markup()
    # A structure whose files are gone: one warning, not two.
    se.set_conditions(p, b, se.steps(b)[0]["id"], "top", "@struct{s-gone} THF")
    _src, result = source(p, b)
    assert len([w for w in result.warnings if "structure in the conditions" in w]) == 1
    wa.close()
    wb.close()


# ---------------------------------------------------------------------- B8, B9

def test_b8_a_restored_compound_follows_the_boxes(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    st = se.steps(k)[0]
    se.add_box(p, k, st["id"])
    first, second = (b["id"] for b in st["products"])
    se.set_box_structure(p, k, first, "CCO")
    se.set_box_structure(p, k, second, "CCN")
    c1 = se.link_new_compound(p, k, first)
    c2 = se.link_new_compound(p, k, second)
    entry = p.delete_compound(c1.id)                  # (its box keeps a copy, unlinked)
    p.restore_from_trash(entry.id)
    k = p.scheme(k.id)
    assert [c.id for c in k.children] == [c2.id, c1.id]   # linked ones first, as boxes
    assert json.loads((k.folder / "scheme.json").read_text(encoding="utf-8"))["children"] \
        == [c2.id, c1.id]


@pytest.mark.parametrize("mutate", [
    lambda d: d["editor"]["steps"][0].__setitem__("products", 7),
    lambda d: d["editor"]["steps"][0].__setitem__("top", 5),
    lambda d: d["editor"]["steps"][0].__setitem__("top", None),
    lambda d: d["editor"]["starting"][0].__setitem__("label", 3),
    lambda d: d["editor"]["starting"][0].__setitem__("label_dx", float("nan")),
    lambda d: d["editor"]["starting"][0].__setitem__("label_dy", float("inf")),
    lambda d: d["editor"]["starting"][0].__setitem__("label_dx", "x"),
    lambda d: d["editor"]["steps"][0].__setitem__("id", 5),
    lambda d: d["editor"].__setitem__("layout", {"scale_pct": float("inf"), "fit": 3,
                                                 "align_ids": "yes"}),
])
def test_b9_hand_edited_scheme_json_compiles(tmp_path, qapp, mutate):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CC=O")
    path = k.folder / "scheme.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")   # (NaN/Infinity as JSON allows)
    p.close()
    p = Project.open(p.folder)
    p.acquire_lock("Tester")
    k = p.scheme(k.id)
    src, _r = source(p, k)
    assert "nanpt" not in src and "infpt" not in src
    r = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png)
    assert r.pdf.exists()
    w = editor_widget(p, k, qapp)
    w.close()
    p.close()


def test_b9_label_offsets_are_finite_and_bounded():
    assert se.label_offset({"label_dx": float("nan"), "label_dy": 1e300}) == \
        (0.0, se.LABEL_OFFSET_MAX)
    assert se.label_offset({"label_dx": True}) == (0.0, 0.0)


# ---------------------------------------------------------------------- U6, U7

def test_u6_the_hrms_difference_reads_the_right_way_round(tmp_path, monkeypatch):
    from si_assistant.core import ms_reports
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="x")
    c.values["hrms"] = {"found": "200.0020"}
    match = ms_reports.Match(200.0000, "r.pdf", None, 0.0, True)
    monkeypatch.setattr(ms_reports, "compound_match", lambda *a, **k: match)
    message, _m = ms_reports.differs_message(p, c)
    assert "is +10.0 ppm from the report's value (200.0000 in r.pdf)" in message


def test_u7_box_placeholder_is_short_and_absent_when_locked(tmp_path, qapp):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    w = editor_widget(p, k, qapp)
    c = w.boxes[0].canvas
    assert c.placeholder.startswith("No structure — ") and len(c.placeholder) < 25
    w.set_locked(True)
    assert all(b.canvas.placeholder == "" for b in w.boxes)
    w.close()
