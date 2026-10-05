"""Regression tests for the review of SPEC §13l (one or more per finding)."""

import math
import os
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QMessageBox

from si_assistant.core import emf_svg as E
from si_assistant.core import fields as F
from si_assistant.core.project import Project
from tests import test_emf_svg as T
from tests.sample_project import fake_spectrum_pdf

windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows (GDI)")


def well_formed(svg: str) -> None:
    ET.fromstring(svg.encode("utf-8"))   # (raises for invalid XML or characters)


# ---------------------------------------------------------------------- 1. damaged EMF data

def angle_arc(sweep: float, start: float = 0.0) -> bytes:
    return T.rec(41, struct.pack("<iiIff", 100, 100, 50, start, sweep))


@pytest.mark.parametrize("sweep", [1e6, 1e8, 1e30, -1e30, 3.4e38])
def test_a_huge_angle_arc_sweep_is_drawn_as_one_turn_quickly(sweep):
    started = time.perf_counter()
    c = E.convert(T.emf(T.create_pen(1, 0, 0), T.select(1), angle_arc(sweep)))
    assert time.perf_counter() - started < 1.0
    assert c.ok and c.strokes == 1 and len(c.svg) < 2000
    well_formed(c.svg)
    (d,) = T.paths(c.svg)
    assert d.count("c") == 1 and len(re.findall(r"-?\d+", d)) <= 2 + 2 + 6 * 8   # ≤ 8 curves


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_an_angle_arc_with_no_usable_angle_counts_as_damaged(value):
    for record in (angle_arc(value), angle_arc(90.0, start=value)):
        c = E.convert(T.emf(record, T.polyline16([(0, 0), (9, 9)])))
        assert c.unsupported["damaged ANGLEARC"] == 1 and c.strokes == 1
        well_formed(c.svg)


def test_more_than_a_turn_ends_where_the_angle_says():
    # 450° ends where 90° does (the current position follows the arc).
    def end(sweep):
        conv = E._Converter(T.emf(angle_arc(sweep)), None)
        conv.run()
        return conv.st.cur
    assert end(450.0) == pytest.approx(end(90.0), abs=1e-6)


def test_coordinates_far_beyond_any_drawing_count_as_damaged():
    huge = T.rec(35, struct.pack("<6f", 1e30, 0, 0, 1e30, 0, 0))   # SETWORLDTRANSFORM
    c = E.convert(T.emf(huge, T.polyline16([(1, 1), (5, 5)]),
                        T.rec(42, struct.pack("<4i", 0, 0, 9, 9)),
                        T.font(1, -20), T.select(1), T.text_out(0, 0, "x")))
    assert not c.ok and c.unsupported["damaged POLYLINE16"] == 1
    assert c.unsupported["damaged ELLIPSE"] == 1 and c.unsupported["damaged EXTTEXTOUTW"] == 1
    assert "nan" not in c.svg and "inf" not in c.svg
    well_formed(c.svg)


def test_saved_states_are_bounded():
    c = E.convert(T.emf(*[T.rec(33)] * (E.MAX_SAVED_STATES + 5)))
    assert c.unsupported["SAVEDC (too many saved states)"] == 5


# ---------------------------------------------------------------------- 2. characters XML can't hold

def test_text_and_font_names_lose_characters_xml_cannot_hold():
    record = T.text_out(0, 0, "", glyphs=[0x61, 0xD800, 0x62, 0xFFFF, 0x01, 0xFFFE, 0x63,
                                          0xD83D, 0xDE00])   # (the last two: one emoji)
    c = E.convert(T.emf(T.font(1, -20, "Ari\x01al￿"), T.select(1),
                        T.set_attr(18, E.TRANSPARENT), record))
    c.svg.encode("utf-8")                     # (no UnicodeEncodeError)
    well_formed(c.svg)
    assert ">abc\U0001F600</text>" in c.svg and "font-family=\"'Arial'" in c.svg
    assert c.ok


def test_the_svg_is_encoded_even_if_something_slipped_through():
    from si_assistant.ui import spectrum_paste as sp
    assert sp._svg_bytes("<svg>a\ud800b</svg>") == b"<svg>a?b</svg>"


@windows
@pytest.mark.parametrize("unit", [0xD800, 0xFFFF])
def test_a_copy_with_a_lone_surrogate_is_converted(qapp, unit):
    from si_assistant.ui import spectrum_paste as sp
    from tests import emf_samples
    data = bytearray(emf_samples.spectrum_emf(n_points=2000, n_labels=20, bitmap=False))
    off = struct.unpack_from("<I", data, 4)[0]
    while off + 8 <= len(data):   # the second character of the first label
        kind, size = struct.unpack_from("<II", data, off)
        if kind == 84:
            n, off_string = struct.unpack_from("<II", data, off + 44)
            if n >= 2:
                struct.pack_into("<H", data, off + off_string + 2, unit)
                break
        off += size
    result = sp.convert_emf(bytes(data))     # (raised UnicodeEncodeError before)
    assert result is not None and result.kind in ("vector", "picture")


# ---------------------------------------------------------------------- 6. clip regions

def region_and(*rects) -> bytes:
    body = b"".join(struct.pack("<4i", *r) for r in rects)
    rgn = (struct.pack("<4I", 32, 1, len(rects), 16 * len(rects))
           + struct.pack("<4i", 0, 0, 200, 80) + body)
    return T.rec(75, struct.pack("<II", len(rgn), E.RGN_AND) + rgn)


def test_each_element_has_at_most_one_clip_group():
    body = []
    for i in range(300):   # regions intersected one after another, each with a line
        body += [region_and((0, 0, 100 - i % 3, 80), (150, 0, 200, 80 - i % 2)),
                 T.polyline16([(i % 100, 0), (i % 100 + 10, 50)])]
    started = time.perf_counter()
    c = E.convert(T.emf(*body))
    assert time.perf_counter() - started < 1.0
    well_formed(c.svg)
    assert len(c.svg) < 100_000                                  # (was 60 MB for 2000)
    # (the same 6 shapes repeat: intersecting a shape again changes nothing)
    assert c.ok and c.svg.count('<g clip-path="url(#') <= 300
    depth = max(len(re.findall(r"<g clip-path", part)) for part in c.svg.split("</g>"))
    assert depth == 1


def test_too_many_clip_shapes_at_once_fall_back_to_a_picture():
    body = [region_and((0, 0, 100 - i, 80), (150, 0, 200, 80)) for i in range(40)]
    c = E.convert(T.emf(*body, T.polyline16([(0, 0), (9, 9)])))
    assert c.unsupported["clipping (too many regions at once)"] == 40 - E.MAX_CLIP_SHAPES
    well_formed(c.svg)


def test_intersected_clip_shapes_are_drawn_right_by_qt(qapp):
    from si_assistant.compile.qt_images import qt_friendly_svg, render_svg_bytes
    c = E.convert(T.emf(T.create_brush(1, 0), T.select(1), T.select(E.STOCK_BIT | 8),
                        region_and((0, 0, 100, 80), (150, 0, 200, 80)),
                        region_and((0, 0, 200, 40), (0, 60, 200, 80)),
                        T.rec(43, struct.pack("<4i", 0, 0, 200, 80))))
    assert c.ok and 'clip-path="url(#c1)">' in c.svg and c.svg.count('<g clip-path=') == 1
    assert 'mask="url(#c1)"' in qt_friendly_svg(c.svg)
    image = render_svg_bytes(c.svg.encode(), 200, 80)
    dark = {p: image.pixelColor(*p).lightness() < 128
            for p in ((50, 20), (125, 20), (50, 50), (175, 70), (175, 50))}
    assert dark == {(50, 20): True, (125, 20): False, (50, 50): False, (175, 70): True,
                    (175, 50): False}


# ---------------------------------------------------------------------- the paste window

@pytest.fixture
def paste_window(qapp, tmp_path, monkeypatch):
    """A compound whose Spectra field was removed but that has an Additional file in the
    Spectra section (so it has a spectra page)."""
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    told = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda *a, _n=name, **k: told.append(f"{_n}: " + (a[2] if len(a) > 2 else ""))))
    p = Project.create(tmp_path / "proj", "Paste")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Compounds")
    c = p.add_compound(s.id, name="3-Phenylpropanal")
    p.remove_field(c, "spectra")
    trace = fake_spectrum_pdf(tmp_path / "trace.pdf", "HPLC")
    p.attach_compound_file(c, "files", trace, {"placement": "appendix"})
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield win, c.id, told
    win.close_project()
    win.close()


def fake_clipboard(monkeypatch, result=None, raises=None):
    from si_assistant.ui import spectrum_paste as sp

    def spectrum(kind=None):
        if raises is not None:
            raise raises
        return result
    monkeypatch.setattr(sp, "clipboard_files", lambda: [])
    monkeypatch.setattr(sp, "clipboard_kind", lambda: "image")
    monkeypatch.setattr(sp, "clipboard_spectrum", spectrum)


def png_spectrum():
    from si_assistant.ui import spectrum_paste as sp
    from si_assistant.ui.clipboard_image import image_to_png
    picture = QImage(200, 100, QImage.Format.Format_RGB32)
    picture.fill(0)
    return sp.PastedSpectrum(image_to_png(picture), ".png", None, "", "image",
                             "Kept the copied picture as it is.")


# ---------------------------------------------------------------------- 3. Spectra field removed

def test_pasting_on_the_spectra_page_adds_the_spectra_field_back(paste_window, monkeypatch):
    from si_assistant.core import spectra as S
    from si_assistant.ui import spectrum_paste as sp
    from si_assistant.ui.spectra_page import SpectraPagePanel
    from PySide6.QtWidgets import QApplication
    win, cid, told = paste_window
    win.show_spectra_page(cid)
    for _ in range(5):
        QApplication.processEvents()
    assert isinstance(win.panel, SpectraPagePanel)
    fake_clipboard(monkeypatch, png_spectrum())
    monkeypatch.setattr(sp, "choose_spectrum_type", lambda *a, **k: "1H")
    win.panel.paste_btn.click()
    compound = win.project.compound(cid)
    assert "spectra" in compound.field_keys                     # shown on the compound page
    assert [i["file"] for _k, i in S.spectrum_items(win.project, compound)] == \
        ["pasted 1H.png", "trace.pdf"]                          # and printed
    assert "Spectra field was added" in win.statusBar().currentMessage()
    assert told == []
    win.undo()                                                  # one step: both go
    compound = win.project.compound(cid)
    assert "spectra" not in compound.field_keys and not compound.values.get("spectra")


# ---------------------------------------------------------------------- 2. pasting never fails silently

def test_an_unexpected_problem_while_converting_is_said_plainly(paste_window, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    win, cid, told = paste_window
    fake_clipboard(monkeypatch, raises=UnicodeEncodeError("utf-8", "\ud800", 0, 1, "bad"))
    monkeypatch.setattr(sp, "choose_spectrum_type", lambda *a, **k: "1H")
    assert sp.paste_spectrum(win, win.project, win.project.compound(cid)) is None
    assert told and told[-1].startswith("warning: The copied spectrum could not be read")
    assert "UnicodeEncodeError" in told[-1]


def test_re_render_counts_an_original_that_fails_and_goes_on(project, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id, name="x")
    project.attach_pasted_spectrum(c, b"png", ".png", "1H", b"EMF", ".emf")
    project.attach_pasted_spectrum(c, b"png", ".png", "13C", b"EMF", ".emf")
    calls = []

    def reconvert(original, check=True):
        calls.append(original.name)
        if len(calls) == 1:
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "bad")
        return sp.PastedSpectrum(b"<svg/>", ".svg", kind="vector")
    monkeypatch.setattr(sp, "reconvert_file", reconvert)
    assert sp.reconvert_pasted_spectra(project) == (1, 1, 1) and len(calls) == 2


# ---------------------------------------------------------------------- 17. the guess sees what was just typed

def test_typing_not_saved_yet_is_saved_before_the_type_is_guessed(project, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id, name="x")
    c.values["nmr"] = [{"id": "r1", "nucleus": "1H", "label": "", "text": "δ 9.81 (s, 1H)"},
                       {"id": "r2", "nucleus": "13C", "label": "", "text": ""}]
    c.values["spectra"] = [{"file": "a.pdf", "type": "1H"}]
    project.save_compound(c)

    def before_write():   # (the page saves the ¹³C report typed a moment ago)
        c.values["nmr"][1]["text"] = "δ 201.5"
        project.save_compound(c)
    asked = []
    fake_clipboard(monkeypatch, png_spectrum())
    monkeypatch.setattr(sp, "choose_spectrum_type",
                        lambda parent, shown, guess, note="": asked.append(guess) or None)
    sp.paste_spectrum(None, project, c, before_write)
    assert asked == ["13C"]


# ---------------------------------------------------------------------- 4. the section's own file

def test_attaching_the_sections_own_file_changes_nothing(project, tmp_path):
    project.enable_undo()
    section = project.add_section("file", "Att")
    source = fake_spectrum_pdf(tmp_path / "x.pdf", "X")
    project.attach_file(section, source)
    attached = project.section_file_path(section)
    content = attached.read_bytes()
    assert project.is_section_file(section, attached)
    assert not project.is_section_file(section, source)
    with project.undo_step("Paste into Att"):
        assert project.attach_file(section, attached) == attached
    assert attached.read_bytes() == content and section.data["file"] == "x.pdf"
    assert project.list_trash() == []


def test_a_new_file_is_copied_in_before_the_old_one_goes(project, tmp_path, monkeypatch):
    from si_assistant.core import fileio
    project.enable_undo()
    section = project.add_section("file", "Att")
    project.attach_file(section, fake_spectrum_pdf(tmp_path / "x.pdf", "old"))
    other = tmp_path / "other"
    other.mkdir()
    new = fake_spectrum_pdf(other / "x.pdf", "new")
    with project.undo_step("Replace"):
        dest = project.attach_file(section, new)
    assert dest.name == "x.pdf" and dest.read_bytes() == new.read_bytes()   # (same name)
    assert [e.name for e in project.list_trash()] == ["x.pdf"]
    assert not list(section.folder.glob(".incoming*"))
    project.undo()                                                # the old one is back
    again = project.section(section.id)
    assert project.section_file_path(again).read_bytes() != new.read_bytes()
    # A copy that fails leaves the section as it was.
    section = project.section(section.id)
    before = project.section_file_path(section).read_bytes()
    trash = len(project.list_trash())

    def broken(source, dest):
        raise OSError("disk full")
    monkeypatch.setattr(fileio, "copy_file", broken)
    with pytest.raises(OSError):
        project.attach_file(section, new)
    assert project.section_file_path(section).read_bytes() == before
    assert len(project.list_trash()) == trash


@pytest.fixture
def attachment_window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    told = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: told.append("question") or QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *a, **k: told.append("info: " + (a[2] if len(a) > 2 else ""))))
    p = Project.create(tmp_path / "proj", "Paste")
    section = p.add_section("file", "Crystal structure")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    win.select("section", section.id)
    yield win, section.id, told
    win.close_project()
    win.close()


def test_pasting_the_attached_file_back_says_so(attachment_window, tmp_path):
    win, sid, told = attachment_window
    panel = win.panel
    panel._attach(fake_spectrum_pdf(tmp_path / "x.pdf", "X"))
    attached = win.project.section_file_path(win.project.section(sid))
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(attached))])
    QGuiApplication.clipboard().setMimeData(data)
    panel.paste_from_clipboard()
    assert told == ["info: “x.pdf” is already the file of this section; nothing was changed."]
    assert attached.exists() and win.project.list_trash() == []


# ---------------------------------------------------------------------- 15. Choose file… / Replace…

def test_the_right_click_menu_names_the_button(attachment_window, tmp_path):
    win, _sid, _told = attachment_window
    panel = win.panel
    assert panel._paste_menu[1][0] == panel.choose_btn.text() == "Choose file…"
    panel._attach(fake_spectrum_pdf(tmp_path / "x.pdf", "X"))
    assert panel._paste_menu[1][0] == panel.choose_btn.text() == "Replace…"


# ---------------------------------------------------------------------- 5. restore info

@pytest.mark.parametrize("where", [5, True, 1.5, "abc", {"a": 1}, None, [5], [["compound"]]])
def test_unusable_restore_places_are_ignored(qapp, tmp_path, monkeypatch, where):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path / "proj", "R")
    p.add_section("data", "Products")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    try:
        assert win.apply_restore_info({"folder": str(p.folder), "where": where}) is True
    finally:
        win.close_project()
        win.close()


def test_a_restore_that_fails_never_stops_the_start():
    from si_assistant.__main__ import restore_safely

    class Broken:
        def apply_restore_info(self, info):
            raise TypeError("'int' object is not iterable")

    class Fine:
        def apply_restore_info(self, info):
            return True
    assert restore_safely(Broken(), {"where": 5}) is False
    assert restore_safely(Fine(), {}) is True


# ---------------------------------------------------------------------- 7. alignment from the Trash

def test_a_figure_or_table_restored_from_the_trash_keeps_its_alignment(project):
    s = project.add_section("text", "Optimization")
    owner = project.text_owner(s.id)
    fid = project.add_figure(owner, data=E.png_bytes(np.zeros((4, 4, 3), np.uint8)), ext=".png")
    tid = project.add_table(owner)
    project.write_owner_text(owner, f"Intro\n!align{{right}} !figure{{{fid}}} Figure caption\n"
                                    f"!align{{left}} !table{{{tid}}} Table caption")
    figure = project.remove_figure(owner, fid, "Figure caption")
    table = project.remove_table(owner, tid, "Table caption")
    project.write_owner_text(owner, "Intro")          # (the editor takes the paragraphs out)
    project.restore_from_trash(figure.id)
    project.restore_from_trash(table.id)
    lines = project.read_owner_text(owner).split("\n")
    assert re.fullmatch(r"!align\{right\} !figure\{f-[^}]+\} Figure caption", lines[1])
    assert re.fullmatch(r"!align\{left\} !table\{t-[^}]+\} Table caption", lines[2])
    # A centred one (the default) gets no prefix.
    fid = project.add_figure(owner, data=E.png_bytes(np.zeros((4, 4, 3), np.uint8)), ext=".png")
    project.write_owner_text(owner, f"!figure{{{fid}}} Plain")
    entry = project.remove_figure(owner, fid, "Plain")
    assert "align" not in entry.info
    project.write_owner_text(owner, "")
    project.restore_from_trash(entry.id)
    assert project.read_owner_text(owner).startswith("!figure{")


# ---------------------------------------------------------------------- 8. deuterated solvents

@pytest.mark.parametrize("written, digits", [
    ("Methanol-d4", "4"), ("Acetone-d6", "6"), ("Benzene-d6", "6"), ("Acetonitrile-d3", "3"),
    ("Toluene-d8", "8"), ("Pyridine-d5", "5"), ("Dichloromethane-d2", "2"), ("dmso-d6", "6"),
    ("Chloroform-d", ""), ("chloroform-d", ""), ("DMSO-d6", "6")])
def test_deuterated_solvents_in_any_capitals(written, digits):
    from si_assistant.compile.text import format_chemistry
    from si_assistant.core import chemtext
    from si_assistant.core.markup import SUB, Run
    runs = format_chemistry([Run(f"in {written} at 25 °C")])
    assert "".join(r.text for r in runs) == f"in {written} at 25 °C"   # capitals as written
    assert any(r.text == "d" and r.italic for r in runs)
    assert [r.text for r in runs if r.valign == SUB] == ([digits] if digits else [])
    (issue,) = chemtext.find_issues(f"in {written} at 25 °C")          # the check agrees
    assert issue.kind == "solvent" and issue.fixed.startswith(written[:-len(digits) or None])
    assert not chemtext.find_issues("chloroform-dx, Acetone-D6 left alone")


def test_captions_from_reports_written_with_capitals():
    from si_assistant.compile.text import format_nmr
    from si_assistant.core import spectra as S
    from si_assistant.core.markup import NORMAL, SUB, Run

    class C:
        values = {"nmr": [{"nucleus": "1H", "text": "1H NMR (500 MHz, Methanol-d4) δ 7.26"},
                          {"nucleus": "13C", "text": "13C NMR (126 MHz, Chloroform-d) δ 77"}]}
    h = S.caption_text(C, {"type": "1H"})
    assert h == "1H NMR (Methanol-d4, 500 MHz)"
    runs = format_nmr([Run(h)])
    assert [(r.text, r.italic, r.valign) for r in runs if r.italic or r.valign == SUB] == \
        [("d", True, NORMAL), ("4", False, SUB)]
    c = format_nmr([Run(S.caption_text(C, {"type": "13C"}))])
    assert "".join(r.text for r in c) == "13C NMR (Chloroform-d, 126 MHz)"
    assert [r.text for r in c if r.italic] == ["d"]


# ---------------------------------------------------------------------- 9. drop-downs keep what is typed

def test_typed_text_in_a_learning_drop_down_is_kept_as_typed(qapp):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCompleter
    from si_assistant.ui import field_widgets as FW
    column = next(p for p in F.ALL_FIELDS["ee"].parts if p.key == "column")
    box = FW.make_part_widget(column, ["Chiralpak AD-H", "Chiralcel OD-H"])
    box.show()
    completer = box.completer()
    assert completer.completionMode() == QCompleter.CompletionMode.PopupCompletion
    for typed, found in (("chiral", 2), ("ad-h", 1), ("OD", 1), ("xyz", 0)):   # (suggested)
        completer.setCompletionPrefix(typed)
        assert completer.completionCount() == found, typed
    completer.setCompletionPrefix("ad-h")
    assert completer.pathFromIndex(completer.currentIndex()) == "Chiralpak AD-H"
    box.setCurrentIndex(-1)                                   # (an empty box, as on a new page)
    box.setEditText("")
    QTest.keyClicks(box.lineEdit(), "Chiralpak AD")
    assert FW.get_part_value(box) == "Chiralpak AD"          # (was "Chiralpak AD-H")
    QTest.keyClick(box.lineEdit(), Qt.Key.Key_Tab)
    QTest.keyClick(box.lineEdit(), Qt.Key.Key_Return)
    assert FW.get_part_value(box) == "Chiralpak AD"
    box.lineEdit().clear()
    QTest.keyClicks(box.lineEdit(), "chiralpak ad-h")         # other capitals: kept as typed
    QTest.keyClick(box.lineEdit(), Qt.Key.Key_Return)
    assert FW.get_part_value(box) == "chiralpak ad-h"
    box.setCurrentIndex(1)                                    # picking from the list still works
    assert FW.get_part_value(box) == "Chiralcel OD-H"
    visualization = next(p for p in F.ALL_FIELDS["rf"].parts if p.key == "visualization")
    vis = FW.make_part_widget(visualization, F.learned_choices(visualization, []))
    vis.show()
    vis.setCurrentIndex(-1)
    vis.setEditText("")
    QTest.keyClicks(vis.lineEdit(), "UV")
    QTest.keyClick(vis.lineEdit(), Qt.Key.Key_Return)
    assert FW.get_part_value(vis) == "UV"                     # (was "UV (254 nm)")
    for b in (box, vis):
        b.completer().popup().hide()
        b.close()
        b.deleteLater()


# ---------------------------------------------------------------------- 10. the ee field's width

def test_the_ee_field_has_two_parts_per_row_and_narrow_learning_drop_downs(qapp, project):
    from PySide6.QtWidgets import QComboBox
    from si_assistant.ui import field_widgets as FW
    s = project.add_section("data", "D")
    c = project.add_compound(s.id, name="x")
    project.add_field(c, {"key": "ee"})
    c.values["ee"] = {"value": "", "method": "HPLC", "column": "Chiralpak " + "X" * 60,
                      "eluent": "", "flow": "", "wavelength": "", "t_major": "", "t_minor": ""}
    project.save_compound(c)
    editor = FW.PartsEditor(project, c, F.ALL_FIELDS["ee"], lambda: None)
    grid = editor.layout()
    assert editor.per_row == 2 and grid.rowCount() == 4 and grid.columnCount() == 2
    for key in ("column", "eluent"):
        combo = editor.widgets[key]
        assert combo.sizeAdjustPolicy() == \
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        assert combo.minimumContentsLength() == FW.COMBO_MIN_CHARS
    assert FW.get_part_value(editor.widgets["column"]) == "Chiralpak " + "X" * 60
    hrms = FW.PartsEditor(project, c, F.ALL_FIELDS["hrms"], lambda: None)
    assert hrms.per_row == 4                                  # (others as before)
    editor.deleteLater()
    hrms.deleteLater()


# ---------------------------------------------------------------------- 11. QT_SCALE_FACTOR not inherited

@pytest.fixture
def startup_state(monkeypatch):
    from si_assistant.ui import restart
    for name in ("applied_size", "scale_set_by_us", "user_scale"):
        monkeypatch.setattr(restart, name, getattr(restart, name))


def test_the_scale_variable_is_taken_away_once_the_program_runs(tmp_path, startup_state):
    from si_assistant.ui import restart
    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    store.setValue(restart.SETTING, 150)
    env = {"PATH": "x"}
    restart.apply_saved_size(env, store)
    assert env["QT_SCALE_FACTOR"] == "1.5"
    restart.forget_scale_variable(env)
    assert env == {"PATH": "x"} and restart.applied_size == 150
    users = {"QT_SCALE_FACTOR": "2"}                          # set by the user: theirs stays
    restart.apply_saved_size(users, store)
    restart.forget_scale_variable(users)
    assert users == {"QT_SCALE_FACTOR": "2"}


def test_main_takes_the_variable_away_after_creating_the_application():
    import inspect
    from si_assistant import __main__ as main_module
    source = inspect.getsource(main_module.main)
    assert source.index("QApplication(sys.argv)") < source.index("forget_scale_variable()")


def test_the_start_up_test_mode_leaves_the_settings_alone(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    path = tmp_path / "prefs.ini"
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(str(path), QSettings.Format.IniFormat))
    monkeypatch.setenv("SI_ASSISTANT_SMOKE_TEST", "1")
    win = mw.MainWindow()
    win.close()
    assert QSettings(str(path), QSettings.Format.IniFormat).value("window_geometry") is None
    monkeypatch.delenv("SI_ASSISTANT_SMOKE_TEST")
    win = mw.MainWindow()
    win.close()                                               # (a normal close saves it)
    assert QSettings(str(path), QSettings.Format.IniFormat).value("window_geometry") is not None


# ---------------------------------------------------------------------- 12. sizes too large for the screen

def test_fits_screen():
    from si_assistant.ui import restart
    # 1093 × 566 free logical pixels at 100 %; the window needs at least 774 × 360.
    fits = {s: restart.fits_screen(s, (1093, 566), (774, 360), 100) for s in restart.SIZES}
    assert fits == {75: True, 90: True, 100: True, 110: True, 125: True, 150: False,
                    175: False, 200: False}
    # The same screen seen from a program running at 125 %: the same answers.
    assert all(restart.fits_screen(s, (1093 / 1.25, 566 / 1.25), (774, 360), 125) == f
               for s, f in fits.items())


def test_sizes_too_large_for_the_screen_are_greyed(qapp, tmp_path, monkeypatch, startup_state):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui import restart
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(restart, "applied_size", 100)
    win = mw.MainWindow()
    try:
        assert win.menuBar().actions()[2].menu().toolTipsVisible()   # View menu
        win.prefs.interface_size = 200                       # (saved: stays available)
        win.update_size_choices((300, 200))
        enabled = {s: a.isEnabled() for s, a in win.act_sizes.items()}
        assert enabled[100] and enabled[200]                  # in use / saved
        assert not enabled[150] and not enabled[175]
        assert "Too large for this screen" in win.act_sizes[150].toolTip()
        win.update_size_choices((10_000, 10_000))
        assert all(a.isEnabled() for a in win.act_sizes.values())
        assert "Too large" not in win.act_sizes[150].toolTip()
    finally:
        win.close()


# ---------------------------------------------------------------------- 13. large J messages

def large(text):
    from si_assistant.core import checks, nmr_parse
    return [c for c in nmr_parse.couplings(text) if c.value > checks.LARGE_J_HZ]


def test_a_labelled_h_f_coupling_may_be_correct_without_a_structure():
    from si_assistant.core.checks import hetero_coupling, large_j_message
    assert [hetero_coupling(w) for w in ("H–F", "HF", "(H,F)", "H-P", "FH", "", "AB", "HH")] \
        == ["F", "F", "F", "P", "F", "", "", ""]
    msg = large_j_message({}, large("δ 4.50 (dt, JH–F = 47.6, J = 6.0 Hz, 2H)"), [])
    assert msg == ("¹H NMR: JH–F = 47.6 Hz is larger than 20 Hz, which may be correct: it is "
                   "labelled as an H–F coupling, and these can be this large.")
    msg = large_j_message({}, large("(d, J(H,P) = 22.1 Hz, J = 28.3 Hz)"), [])
    assert "J = 28.3 Hz is larger than 20 Hz. H–H coupling constants are rarely" in msg
    assert "J(H,P) = 22.1 Hz is larger than 20 Hz, which may be correct" in msg
    assert "H–P coupling" in msg


def test_with_f_or_p_in_the_structure_the_message_leads_with_may_be_correct():
    from si_assistant.core.checks import large_j_message
    msg = large_j_message({"label": "major rotamer"}, large("(d, J = 28.3 Hz)"), ["F"])
    assert msg == ("¹H NMR (major rotamer): J = 28.3 Hz is larger than 20 Hz, which may be "
                   "correct: the structure contains F, and H–F couplings can be this large. "
                   "If it is an H–H coupling, check for a typo.")
    labelled = large_j_message({}, large("(d, 2JHF = 47.6 Hz)"), ["F"])
    assert "may be correct" in labelled and "typo" not in labelled
    assert labelled.index("may be correct") < len(labelled) // 2


# ---------------------------------------------------------------------- 14. tooltips

def test_paste_buttons_and_alignment_say_their_keys(attachment_window, paste_window_editor):
    from si_assistant.ui.structure_widgets import paste_key
    win, _sid, _told = attachment_window
    assert paste_key() in win.panel.paste_btn.toolTip()
    assert paste_key() in paste_window_editor.paste_btn.toolTip()


@pytest.fixture
def paste_window_editor(qapp, project):
    from si_assistant.ui.spectrum_paste import SpectraEditor
    s = project.add_section("data", "D")
    c = project.add_compound(s.id, name="x")
    editor = SpectraEditor(project, c, F.ALL_FIELDS["spectra"], lambda: None)
    yield editor
    editor.deleteLater()


def test_the_alignment_button_lists_its_keys(qapp):
    from si_assistant.ui.rich_text import RichTextEditor
    editor = RichTextEditor(None)
    editor.enable_alignment()
    tip = editor.align_button.toolTip()
    for keys in ("Ctrl+L left", "Ctrl+E centred", "Ctrl+R right", "Ctrl+J justified"):
        assert keys in tip or sys.platform == "darwin"
    editor.deleteLater()


# ---------------------------------------------------------------------- 16. Spectra field wording

def test_the_type_drop_down_shows_the_pretty_labels(qapp, project, tmp_path):
    from si_assistant.ui import field_widgets as FW
    from si_assistant.ui.spectrum_paste import TYPE_LABELS, SpectraEditor
    s = project.add_section("data", "D")
    c = project.add_compound(s.id, name="x")
    project.attach_compound_file(c, "spectra", fake_spectrum_pdf(tmp_path / "a.pdf", "A"),
                                 {"type": "13C"})
    editor = SpectraEditor(project, c, F.ALL_FIELDS["spectra"], lambda: None)
    (_name, widgets), = editor.row_widgets
    combo = widgets["type"]
    assert combo.currentText() == "¹³C NMR" and FW.get_part_value(combo) == "13C"
    assert [combo.itemText(i) for i in range(combo.count())] == \
        [TYPE_LABELS[t] for t in F.SPECTRUM_TYPES]
    assert TYPE_LABELS["1H"] == "¹H NMR" and TYPE_LABELS["IR"] == "IR"
    combo.setCurrentIndex(combo.findData("19F"))
    editor.store()
    assert c.values["spectra"][0]["type"] == "19F"           # stored values unchanged
    editor.deleteLater()


def test_an_empty_spectra_field_says_how_in_one_line(paste_window_editor):
    from PySide6.QtWidgets import QLabel
    editor = paste_window_editor
    editor.show()
    shown = [w.text() for w in editor.findChildren(QLabel) if w.isVisible() and w.text()]
    assert len(shown) == 1 and shown[0].startswith("No spectra yet")
    editor._set_paste_active(True)                            # clicked: the blue hint shows
    assert editor.paste_hint.isVisible() and "pastes here" in editor.paste_hint.text()
    editor._set_paste_active(False)
    assert not editor.paste_hint.isVisible() and not editor.drop_hint.isVisible()
    editor.close()


# ---------------------------------------------------------------------- 18. pictures in Attachment sections

def test_attachment_pictures_print_at_their_own_size(qapp, project, tmp_path):
    from si_assistant.compile.document import CompileOptions, build
    from si_assistant.compile.qt_images import convert_to_png, image_info
    small = tmp_path / "small.png"                            # 600 × 300 px at 300 dpi
    small.write_bytes(E.png_bytes(np.zeros((300, 600, 3), np.uint8), (11811, 11811)))
    big = tmp_path / "big.png"                                # 30 in wide at 100 dpi
    big.write_bytes(E.png_bytes(np.zeros((100, 3000, 3), np.uint8), (3937, 3937)))
    for name, path in (("Small", small), ("Big", big)):
        project.attach_file(project.add_section("file", name), path)
    project.attach_file(project.add_section("file", "Report"),
                        fake_spectrum_pdf(tmp_path / "report.pdf", "Report"))
    src = build(project, CompileOptions.from_settings(project.settings), image_info,
                convert_to_png).typ_path.read_text(encoding="utf-8")
    assert re.search(r'image\("[^"]*small\.png", width: 2\.000in\)', src)
    assert re.search(r'image\("[^"]*big\.png", width: 6\.500in\)', src)   # (the text width)
    assert re.search(r'image\("[^"]*report\.pdf", page: 1, width: 100%, height: 8in, '
                     r'fit: "contain"\)', src)                           # (PDFs as before)


# ---------------------------------------------------------------------- 19. PDF metadata

def test_the_pdf_names_this_program_and_keeps_everything_else(qapp, tmp_path):
    from pypdf import PdfReader
    from si_assistant import APP_NAME, __version__
    from si_assistant.compile import run as R
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from tests.sample_project import build_sample
    from tests.test_spectra_links import links
    p, _parts = build_sample(tmp_path)
    try:
        p.set_spectra_defaults({**p.spectra_defaults(), "nmr_links": True, "link_back": True})
        result = R.compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                               convert_to_png)
        raw = result.pdf.with_name("raw.pdf")
        R.run_typst(result.typ, raw, p.folder)                # (Typst alone)
    finally:
        p.close()
    marked, plain = PdfReader(str(result.pdf)), PdfReader(str(raw))
    assert marked.metadata["/Creator"] == f"{APP_NAME} {__version__}"
    assert re.fullmatch(rf"{APP_NAME} {re.escape(__version__)} \(Typst [\d.]+\)",
                        marked.metadata["/Producer"])
    assert marked.metadata["/Title"] == plain.metadata["/Title"]
    xmp = marked.xmp_metadata
    assert xmp.xmp_creator_tool == marked.metadata["/Creator"]
    assert xmp.pdf_producer == marked.metadata["/Producer"]
    assert len(marked.pages) == len(plain.pages)
    assert links(marked) == links(plain) and links(marked)    # (links work as before)
    assert len(marked.outline) == len(plain.outline) > 0
    assert "/StructTreeRoot" in marked.trailer["/Root"]
    # An incremental update: Typst's file comes first, untouched (it still reads as Typst's).
    from io import BytesIO
    data = result.pdf.read_bytes()
    assert data.count(b"startxref") == 2
    original = PdfReader(BytesIO(data[:data.index(b"%%EOF") + 5]))
    assert original.metadata["/Creator"].startswith("Typst")
    assert len(original.pages) == len(marked.pages)
    # Something that isn't a PDF is left as it is.
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert R.mark_producer(junk) is False and junk.read_bytes() == b"not a pdf"
    assert not list(tmp_path.glob("*.meta"))


# ---------------------------------------------------------------------- 20. spelling

@pytest.mark.parametrize("word", [
    "XantPhos", "Xantphos", "JosiPhos", "SegPhos", "SEGPHOS", "DuPhos", "DPEphos", "DavePhos",
    "CyJohnPhos", "tBuBrettPhos", "AdBrettPhos", "tBuXPhos", "MePhos", "BippyPhos"])
def test_phosphine_ligands_are_accepted(word):
    from si_assistant.core import spelling
    s = spelling.Speller()
    assert spelling.is_phos_ligand(word) and s.misspelled(f"with {word} in") == []


def test_lab_words_multiplicities_and_names_are_known():
    from si_assistant.core import spelling
    s = spelling.Speller()
    text = ("7.26 (dd, J = 8.0 Hz), 7.1 (dt), 6.9 (td), 6.8 (tt), 6.7 (dq), 6.6 (qd), 6.5 (ddd), "
            "6.4 (ddt), 6.3 (dtd), 6.2 (tdd), 6.1 (dddd), 5.9 (quint), 5.8 (sext), 5.7 (sept), "
            "5.6 (hept), 5.5 (app t), 5.4 (br s), 5.3 (ABq) with HOBt, HOAt, CuTC, DiPEA, DIPEA, "
            "PyBOP and HATU from AcroSeal bottles (MilliporeSigma, AmBeed, Strem, Oakwood, "
            "Enamine, Combi-Blocks, Alfa Aesar, Fisher, VWR) on SiliaPlate, SiliaFlash, "
            "ChiralCel, ChiralPak, CombiFlash, RediSep, Biotage Isolera, Teledyne, Phenomenex, "
            "Daicel; Lemieux, Enders, Hajos, Eder, Parrish, Sauer and Wiechert")
    assert [text[a:b] for a, b in s.misspelled(text)] == []
    # Still underlined: made-up words, and "…phos" without a known name in front.
    wrong = "Fooophos AbcDef Xenophos Shoephos"
    assert [wrong[a:b] for a, b in s.misspelled(wrong)] == \
        ["Fooophos", "AbcDef", "Xenophos", "Shoephos"]
    assert spelling.CHECK_INNER_CAPITALS is True
