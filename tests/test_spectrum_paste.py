"""Pasting spectra, e.g. from Mnova (SPEC §13l.1): metafiles drawn with real Windows calls
are converted to SVG and checked against Windows' own drawing; the fall-backs; how pasted
spectra are stored; the paste target, type chooser and undo; SVG spectra on the spectra
page and in the compiled PDF."""

import os
import sys

import pytest
from PySide6.QtCore import QMimeData, QSettings, QSize, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QMessageBox

from si_assistant.compile.qt_images import png_dpi
from si_assistant.core import emf_svg
from si_assistant.core.project import Project
from tests import test_emf_svg as T

windows = pytest.mark.skipif(sys.platform != "win32",
                             reason="Enhanced Metafiles are drawn by Windows")
# Comparisons with Windows' own drawing depend on the computer's screen (the metafile's
# reference device) and fonts, so they are left out on GitHub's test computers (CI).
windows_desktop = pytest.mark.skipif(sys.platform != "win32" or bool(os.environ.get("CI")),
                                     reason="needs Windows' drawing on a real desktop")


def samples():
    from tests import emf_samples
    return emf_samples


def dpi_of(image: QImage) -> float:
    return image.dotsPerMeterX() * 0.0254


def label_emf() -> bytes:
    """A small metafile made by hand (any system): a line and a text label."""
    return T.emf(T.font(1, -20, "Arial"), T.select(1), T.set_attr(18, emf_svg.TRANSPARENT),
                 T.set_attr(22, emf_svg.TA_BASELINE), T.text_out(20, 40, "Pasted label 7.26"),
                 T.polyline16([(10, 60), (190, 60), (190, 20)]))


# ---------------------------------------------------------------------- with Windows' drawing
# Checks that compare Windows' text with Qt's need the computer's fonts, which the tests'
# "offscreen" Qt platform doesn't have: they run in a separate process on Qt's Windows
# platform, as the program does (tests/native_spectrum_checks.py).

@pytest.fixture(scope="module")
def native():
    import json
    import os
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    proc = subprocess.run([sys.executable, "-m", "tests.native_spectrum_checks"], cwd=root,
                          env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@windows_desktop
def test_a_spectrum_becomes_svg_and_passes_the_check_quickly(native):
    from si_assistant.ui import spectrum_paste as sp
    r = native["spectrum"]   # 65,536-point trace, 300 labels, a picture, a clip
    assert native["platform"] == "windows"
    assert r["kind"] == "vector" and r["message"] == sp.VECTOR_MESSAGE and r["passed"]
    assert min(r["coverage"]) > 0.999 and min(r["worst_tile"]) > 0.99
    assert r["total_s"] < 2.0 and r["convert_s"] < 0.5, r
    assert r["label"] and r["texts"] > 300
    assert r["bytes"] < 700_000   # compact path data


@windows_desktop
@pytest.mark.parametrize("name", ["shapes_emf", "himetric_emf", "lines_emf"])
def test_other_drawing_commands_pass_the_check(native, name):
    r = native[name]
    assert r["ok"] and r["passed"], r["reason"]
    assert min(r["coverage"]) > 0.999 and min(r["worst_tile"]) > 0.99


@windows_desktop
def test_the_check_passes_whatever_screen_the_metafile_was_made_on(native):
    """Hatched fills and one-pixel dashed lines: Windows draws them in its own output
    pixels, so the stored SVG gives them a fixed size and the self-check compares them at
    Windows' size — the same result for metafiles made on any screen."""
    screens = native["other screens"]
    assert {k: v["passed"] for k, v in screens.items()} == {k: True for k in screens}, screens
    first = next(iter(screens.values()))
    assert first["patterns"] and first["dashes"]
    assert all(v["patterns"] == first["patterns"] and v["dashes"] == first["dashes"]
               for v in screens.values())


@windows_desktop
def test_the_check_fails_for_a_broken_conversion(native):
    variants = native["variants"]
    assert variants.pop("good") is True
    assert variants == {k: False for k in variants}, variants
    assert native["colours swapped"] is False


@windows_desktop
def test_glyph_number_text_is_read_back_with_qt(native):
    """Text written as glyph numbers (ETO_GLYPH_INDEX) is turned back into characters."""
    assert native["glyphs"] == {"ok": True, "text": True, "outline": True}


@windows
def test_without_the_font_glyph_numbers_are_not_guessed(qapp):
    """A stand-in font's glyph numbers mean nothing: then they are not read back."""
    from si_assistant.ui import spectrum_paste as sp
    helper = sp.QtTextHelper()
    missing = emf_svg.Font(face="No Such Font 123")
    assert helper.glyph_text(missing, [36, 37]) is None
    assert helper.glyph_outline(missing, [36], [0.0], 20) is None
    assert helper.metrics(missing) is None


@windows_desktop
def test_a_drawing_without_text_is_kept_as_svg(qapp):
    from si_assistant.ui import spectrum_paste as sp
    data = samples().lines_emf()
    result = sp.convert_emf(data)
    assert result.kind == "vector" and result.data.startswith(b"<?xml")


@windows_desktop
def test_typst_prints_the_svg_like_windows_draws_the_metafile(qapp, tmp_path):
    import typst
    from si_assistant.ui import clipboard_image as ci
    from si_assistant.ui import spectrum_paste as sp
    data = samples().shapes_emf()
    conv = emf_svg.convert(data, sp.QtTextHelper())
    # (hatches at the spacing Windows draws them at 150 dpi, as in the self-check)
    (tmp_path / "s.svg").write_text(sp.check_svg(data, conv.svg, 150), encoding="utf-8")
    (tmp_path / "s.typ").write_text(
        f"#set page(width: {conv.width_mm}mm, height: {conv.height_mm}mm, margin: 0mm)\n"
        '#image("s.svg", width: 100%)\n', encoding="utf-8")
    png = typst.compile(str(tmp_path / "s.typ"), root=str(tmp_path), format="png", ppi=150)
    printed = QImage.fromData(png)
    reference = ci.render_emf_bytes(data, 150)
    assert printed.size() == reference.size()
    assert sp.compare(reference, printed).passed()


@windows
def test_a_bitmap_only_copy_is_kept_at_its_own_resolution(qapp, tmp_path):
    from si_assistant.ui import spectrum_paste as sp
    data = samples().bitmap_emf(640, 480, 160, 120)   # Mnova's default copy
    result = sp.convert_emf(data)
    assert result.kind == "bitmap" and result.ext == ".png" and result.original == data
    image = QImage.fromData(result.data)
    assert image.size() == QSize(640, 480)             # not enlarged
    (tmp_path / "b.png").write_bytes(result.data)
    assert png_dpi(tmp_path / "b.png") == pytest.approx(640 / (160 / 25.4), abs=0.1)
    assert "only about 102 dpi" in result.message and "Enhanced Windows Metafile" in result.message


@windows
def test_drawing_that_cannot_be_converted_is_drawn_at_600_dpi(qapp):
    from si_assistant.ui import spectrum_paste as sp
    result = sp.convert_emf(samples().unsupported_emf())
    assert result.kind == "picture" and result.ext == ".png"
    image = QImage.fromData(result.data)
    assert dpi_of(image) == pytest.approx(600, abs=0.5)
    assert image.width() == pytest.approx(60 / 25.4 * 600, abs=2)
    assert result.message.startswith("Kept as a picture (600 dpi)")


@windows
def test_an_emf_plus_only_copy_is_drawn_by_gdiplus(qapp):
    from si_assistant.ui import spectrum_paste as sp
    data = samples().emf_plus_only()
    conv = emf_svg.convert(data)
    assert conv.emf_plus and not conv.drawn
    result = sp.convert_emf(data)
    assert result.kind == "picture" and "EMF+" in result.message
    assert not sp._blank(QImage.fromData(result.data))


@windows
def test_damaged_metafiles_never_crash(qapp):
    from si_assistant.ui import spectrum_paste as sp
    data = samples().shapes_emf()
    for cut in (100, len(data) // 2, len(data) - 12):
        result = sp.convert_emf(data[:cut])
        assert result is None or result.kind == "picture"
    garbled = bytearray(data)
    garbled[400:440] = b"\xff" * 40
    result = sp.convert_emf(bytes(garbled))
    assert result is None or result.kind in ("vector", "picture")


def test_without_windows_a_complete_conversion_can_be_kept_unchecked(qapp):
    from si_assistant.ui import spectrum_paste as sp
    result = sp.convert_emf(label_emf(), check=False)
    assert result.kind == "vector" and b"Pasted label 7.26" in result.data


# ---------------------------------------------------------------------- the Qt view of an SVG

def test_qt_friendly_svg():
    from si_assistant.compile.qt_images import qt_friendly_svg
    svg = ('<svg><defs><clipPath id="c1"><path d="M0 0h9v9z" clip-rule="evenodd"/></clipPath>'
           '</defs><g clip-path="url(#c1)"><text xml:space="preserve" x="0 10 20 30" y="5" '
           'font-family="\'Arial\', \'Liberation Sans\', sans-serif" fill="#000">a b&amp;'
           '</text></g></svg>')
    out = qt_friendly_svg(svg)
    assert '<mask id="c1"><g fill="#ffffff" stroke="none"><path d="M0 0h9v9z" ' \
           'fill-rule="evenodd"/></g></mask>' in out
    assert 'mask="url(#c1)"' in out and "clip-path" not in out
    assert out.count("<text") == 3 and 'x="10"' not in out    # (the space is left out)
    assert '<text xml:space="preserve" x="30" y="5" font-family="Arial" fill="#000">' \
           '&amp;</text>' in out
    assert qt_friendly_svg('<text x="5">one position</text>') == \
        '<text x="5">one position</text>'


def test_svg_spectra_are_drawn_measured_and_sized(qapp, tmp_path):
    from si_assistant.compile.document import svg_size_in
    from si_assistant.compile.qt_images import content_box, image_info, read_image, render_page
    svg = emf_svg.convert(label_emf()).svg
    path = tmp_path / "pasted 1H.svg"
    path.write_text(svg, encoding="utf-8")
    page = render_page(path, None, 100)
    assert page.width() == round(50 / 25.4 * 100) and not page.isNull()
    box = content_box(path, None)
    assert box is not None and 0 < box[0] < 0.2 and 0.8 < box[2] <= 1
    w, h, dpi = image_info(path)
    assert (w / dpi, h / dpi) == pytest.approx((50 / 25.4, 20 / 25.4), abs=0.01)
    assert read_image(path, max_side=200).width() == 200
    assert svg_size_in('<svg width="10mm" height="1in">') == pytest.approx((10 / 25.4, 1))
    assert svg_size_in("<svg viewBox='0 0 144 72'>") == (2, 1)
    assert svg_size_in("<svg width='72px' height='36px' viewBox='0 0 9 9'>") == (1, 0.5)


# ---------------------------------------------------------------------- storage

@pytest.fixture
def compound(project):
    s = project.add_section("data", "Compounds")
    return project.add_compound(s.id, name="3-Phenylpropanal")


def test_a_pasted_spectrum_is_stored_with_its_original(project, compound):
    item = project.attach_pasted_spectrum(compound, b"<svg/>", ".svg", "13C", b"EMF", ".emf")
    assert item["file"] == "pasted 13C.svg" and item["original"] == "pasted 13C.emf"
    assert item["type"] == "13C" and "id" not in item
    folder = compound.folder / "spectra"
    assert (folder / "pasted 13C.svg").read_bytes() == b"<svg/>"
    assert (folder / "pasted 13C.emf").read_bytes() == b"EMF"
    again = project.attach_pasted_spectrum(compound, b"<svg/>", ".svg", "13C", b"EMF", ".emf")
    assert again["file"] == "pasted 13C (2).svg" and again["original"] == "pasted 13C (2).emf"
    names = [project.attach_pasted_spectrum(compound, b"x", ".png", t)["file"]
             for t in ("Other", "HPLC/SFC", "Other NMR", "2D NMR")]
    assert names == ["pasted spectrum.png", "pasted HPLC-SFC.png", "pasted other NMR.png",
                     "pasted 2D NMR.png"]
    assert project.attachment_original(compound, "spectra", item) == folder / "pasted 13C.emf"
    assert len(project.pasted_spectra()) == 2


def test_a_pasted_spectrum_and_its_original_go_to_the_trash_together(project, compound):
    project.attach_pasted_spectrum(compound, b"<svg/>", ".svg", "1H", b"EMF", ".emf")
    project.remove_compound_file(compound, "spectra", "pasted 1H.svg")
    folder = compound.folder / "spectra"
    assert not (folder / "pasted 1H.svg").exists() and not (folder / "pasted 1H.emf").exists()
    (entry,) = project.list_trash()
    assert sorted(p.name for p in entry.folder.iterdir() if p.suffix != ".json") == \
        ["pasted 1H.emf", "pasted 1H.svg"]
    project.restore_from_trash(entry.id)
    (item,) = compound.values["spectra"]
    assert item["file"] == "pasted 1H.svg" and item["original"] == "pasted 1H.emf"
    assert (folder / "pasted 1H.emf").read_bytes() == b"EMF"


def test_an_ordinary_file_is_never_paired_with_another(project, compound, tmp_path):
    png, pdf = tmp_path / "3a_1H.png", tmp_path / "3a_1H.pdf"
    png.write_bytes(b"png")
    pdf.write_bytes(b"%PDF")
    project.attach_compound_file(compound, "spectra", png, {"type": "1H"})
    project.attach_compound_file(compound, "spectra", pdf, {"type": "1H"})
    project.remove_compound_file(compound, "spectra", "3a_1H.png")
    assert (compound.folder / "spectra" / "3a_1H.pdf").exists()   # (not its "original")
    assert project.pasted_spectra() == []


def test_a_spectrum_converted_again_keeps_its_place_and_settings(project, compound):
    item = project.attach_pasted_spectrum(compound, b"png", ".png", "1H", b"EMF", ".emf")
    project.attach_pasted_spectrum(compound, b"<svg/>", ".svg", "13C")
    item["layout"] = {"per_page": 2}
    project.set_spectra_order(compound, [["spectra", "pasted 13C.svg"],
                                         ["spectra", "pasted 1H.png"]])
    new = project.replace_pasted_spectrum(compound, "spectra", item, b"<svg/>", ".svg")
    assert new.name == "pasted 1H.svg" and item["file"] == "pasted 1H.svg"
    assert not (compound.folder / "spectra" / "pasted 1H.png").exists()
    assert compound.data["spectra_order"][1] == ["spectra", "pasted 1H.svg"]
    assert item["layout"] == {"per_page": 2} and item["original"] == "pasted 1H.emf"


@windows_desktop
def test_re_render_converts_pasted_spectra_again(qapp, project, compound):
    from si_assistant.ui import spectrum_paste as sp
    data = samples().lines_emf()
    project.attach_pasted_spectrum(compound, b"old picture", ".png", "1H", data, ".emf")
    assert sp.reconvert_pasted_spectra(project) == (1, 1, 0)
    (item,) = compound.values["spectra"]
    assert item["file"] == "pasted 1H.svg" and item["original"] == "pasted 1H.emf"
    assert (compound.folder / "spectra" / "pasted 1H.svg").read_text("utf-8").startswith("<?xml")


# ---------------------------------------------------------------------- the type guess

def test_the_type_guess(project, compound):
    from si_assistant.core.spectra import guess_pasted_type
    assert guess_pasted_type(compound) == "1H"                  # no spectra at all
    compound.values["nmr"] = [{"nucleus": "1H", "text": "δ 7.26"},
                              {"nucleus": "13C", "text": "δ 77.2"},
                              {"nucleus": "19F", "text": ""}]
    assert guess_pasted_type(compound) == "1H"
    compound.values["spectra"] = [{"file": "a.pdf", "type": "1H"}]
    assert guess_pasted_type(compound) == "13C"                 # ¹H has its spectrum
    compound.values["spectra"].append({"file": "b.pdf", "type": "13C"})
    assert guess_pasted_type(compound) == "Other"               # (¹⁹F has no text)
    compound.values["nmr"][2]["text"] = "δ −63.1"
    assert guess_pasted_type(compound) == "19F"


def test_the_chooser_takes_one_click_or_enter(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from si_assistant.ui.spectrum_paste import SpectrumTypeDialog
    dialog = SpectrumTypeDialog(None, "3a", "13C", "Guessed.")
    dialog.show()
    assert dialog.buttons["13C"].isDefault() and dialog.buttons["13C"].font().bold()
    assert list(dialog.buttons) == ["1H", "13C", "19F", "31P", "11B", "Other NMR", "2D NMR",
                                    "HPLC/SFC", "IR", "Other"]
    QTest.keyClick(dialog.buttons["13C"], Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.choice == "13C"
    other = SpectrumTypeDialog(None, "3a", "1H")
    other.show()
    other.buttons["IR"].click()
    assert other.choice == "IR"
    cancelled = SpectrumTypeDialog(None, "3a", "1H")
    cancelled.show()
    QTest.keyClick(cancelled, Qt.Key.Key_Escape)
    assert cancelled.result() == cancelled.DialogCode.Rejected and cancelled.choice is None
    for d in (dialog, other, cancelled):
        d.close()
        d.deleteLater()


# ---------------------------------------------------------------------- pasting in the window

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    told = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *a, **k: told.append(a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: told.append("warning: " + (a[2] if len(a) > 2 else ""))))
    p = Project.create(tmp_path / "proj", "Paste spectra")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Compounds")
    c = p.add_compound(s.id, name="3-Phenylpropanal")
    c.values["nmr"] = [{"id": "r1", "nucleus": "1H", "label": "", "text": "δ 9.81 (s, 1H)"},
                       {"id": "r2", "nucleus": "13C", "label": "", "text": "δ 201.5"}]
    p.save_compound(c)
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    win.select("compound", c.id)
    yield win, c.id, told
    win.close_project()
    win.close()


def spectra_editor(win):
    box = next(b for b in win.panel.boxes if b.defn.key == "spectra")
    return box.editor


def fake_clipboard(monkeypatch, result, kind="emf"):
    """Pretend Mnova copied something (the real clipboard is not used in tests)."""
    from si_assistant.ui import spectrum_paste as sp
    calls = []
    monkeypatch.setattr(sp, "clipboard_kind", lambda: kind)
    monkeypatch.setattr(sp, "clipboard_spectrum",
                        lambda k=None: calls.append(k) or result)
    QGuiApplication.clipboard().setMimeData(QMimeData())
    return calls


def test_the_spectra_field_is_a_paste_target(window):
    from si_assistant.ui.spectrum_paste import SpectraEditor
    win, _cid, _told = window
    editor = spectra_editor(win)
    assert isinstance(editor, SpectraEditor)
    assert "Click this box" in editor.paste_hint.text()
    editor._set_paste_active(True)                       # what clicking the field does
    assert "pastes here" in editor.paste_hint.text()
    assert "3px solid" in editor.rows_frame.styleSheet()
    assert "No spectra yet" in editor.rows_frame.findChildren(type(editor.paste_hint))[0].text()
    assert editor.paste_btn.text() == "Paste spectrum" and editor.paste_btn.isEnabled()
    calls = []
    editor.paste_from_clipboard = lambda: calls.append(1)
    editor._shortcut_paste()                              # Ctrl+V on the selected field
    assert calls == [1]


def test_paste_asks_the_type_stores_the_spectrum_and_undoes_in_one_step(window, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    win, cid, told = window
    data = emf_svg.convert(label_emf()).svg.encode("utf-8")
    fake_clipboard(monkeypatch, sp.PastedSpectrum(data, ".svg", b"EMF bytes", ".emf",
                                                  "vector", sp.VECTOR_MESSAGE))
    asked = []

    def choose(parent, shown, guess, note=""):
        asked.append((shown, guess, note))
        return guess
    monkeypatch.setattr(sp, "choose_spectrum_type", choose)
    editor = spectra_editor(win)
    box = next(b for b in win.panel.boxes if b.defn.key == "spectra")
    assert any("no ¹H spectrum" in w.message for w in box.warnings)
    editor.paste_from_clipboard()
    project = win.project
    compound = project.compound(cid)
    assert asked[0][1] == "1H" and asked[0][0] == project.id_report().display_id(cid)
    messages = [w.message for w in box.warnings]   # (the page's warnings follow at once)
    assert not any("no ¹H spectrum" in m for m in messages)
    assert any("no ¹³C spectrum" in m for m in messages)
    (item,) = compound.values["spectra"]
    assert item["file"] == "pasted 1H.svg" and item["type"] == "1H"
    assert item["original"] == "pasted 1H.emf"
    folder = compound.folder / "spectra"
    assert (folder / "pasted 1H.emf").read_bytes() == b"EMF bytes"
    assert editor.paste_note.text() == sp.VECTOR_MESSAGE and not editor.paste_note.isHidden()
    assert win.statusBar().currentMessage() == sp.VECTOR_MESSAGE
    assert win.act_undo.text().startswith("&Undo Paste spectrum into ")
    # The next paste guesses ¹³C (¹H has its spectrum now).
    editor = spectra_editor(win)
    editor.paste_from_clipboard()
    assert asked[1][1] == "13C"
    assert [i["file"] for i in project.compound(cid).values["spectra"]] == \
        ["pasted 1H.svg", "pasted 13C.svg"]
    # The tree has the compound's spectra page; undo takes the last paste away, whole.
    assert win._find_item("spectra_page", cid) is not None
    win.undo()
    assert [i["file"] for i in win.project.compound(cid).values["spectra"]] == ["pasted 1H.svg"]
    assert not (folder / "pasted 13C.svg").exists() and not (folder / "pasted 13C.emf").exists()
    assert told == []


def test_cancelling_the_chooser_pastes_nothing(window, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    win, cid, _told = window
    calls = fake_clipboard(monkeypatch, sp.PastedSpectrum(b"<svg/>", ".svg"))
    monkeypatch.setattr(sp, "choose_spectrum_type", lambda *a, **k: None)
    spectra_editor(win).paste_from_clipboard()
    assert not win.project.compound(cid).values.get("spectra") and calls == []


def test_nothing_usable_on_the_clipboard_is_said_plainly(window, monkeypatch, tmp_path):
    from si_assistant.ui import spectrum_paste as sp
    win, cid, told = window
    fake_clipboard(monkeypatch, None, kind="")
    spectra_editor(win).paste_from_clipboard()
    assert told[-1] == sp.NOTHING_TO_PASTE
    other = tmp_path / "notes.docx"
    other.write_bytes(b"x")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(other))])
    QGuiApplication.clipboard().setMimeData(data)
    spectra_editor(win).paste_from_clipboard()
    assert told[-1].startswith("notes.docx is not a PDF or a picture")
    assert not win.project.compound(cid).values.get("spectra")


def test_a_copy_that_cannot_be_read_is_said_plainly(window, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    win, cid, told = window
    fake_clipboard(monkeypatch, None, kind="emf")
    monkeypatch.setattr(sp, "choose_spectrum_type", lambda *a, **k: "1H")
    spectra_editor(win).paste_from_clipboard()
    assert told[-1].startswith("warning: The copied spectrum could not be read")
    assert not win.project.compound(cid).values.get("spectra")


def test_pasting_copied_files(window, monkeypatch, tmp_path):
    from si_assistant.ui import spectrum_paste as sp
    from tests.sample_project import fake_spectrum_pdf
    win, cid, _told = window
    one = fake_spectrum_pdf(tmp_path / "3a_13C.pdf", "13C")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(one))])
    QGuiApplication.clipboard().setMimeData(data)
    asked = []
    monkeypatch.setattr(sp, "choose_spectrum_type",
                        lambda parent, shown, guess, note="": asked.append(guess) or guess)
    spectra_editor(win).paste_from_clipboard()
    assert asked == ["13C"]                                  # (guessed from the file's name)
    two = [fake_spectrum_pdf(tmp_path / f"3a_{n}.pdf", n) for n in ("1H", "HSQC")]
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in two])
    QGuiApplication.clipboard().setMimeData(data)
    spectra_editor(win).paste_from_clipboard()               # several: no question
    assert asked == ["13C"]
    items = win.project.compound(cid).values["spectra"]
    assert [(i["file"], i["type"]) for i in items] == [
        ("3a_13C.pdf", "13C"), ("3a_1H.pdf", "1H"), ("3a_HSQC.pdf", "2D NMR")]
    assert win.act_undo.text().startswith("&Undo Paste 2 files into ")


def test_pasting_on_the_spectra_page(window, monkeypatch):
    from si_assistant.ui import spectrum_paste as sp
    from si_assistant.ui.spectra_page import SpectraPagePanel
    win, cid, _told = window
    project = win.project
    compound = project.compound(cid)
    project.attach_pasted_spectrum(compound, emf_svg.convert(label_emf()).svg.encode(), ".svg",
                                   "1H", b"EMF", ".emf")
    win.show_spectra_page(cid)
    for _ in range(5):
        qapp_process()
    assert isinstance(win.panel, SpectraPagePanel)
    panel = win.panel
    assert not panel.canvas.image.isNull()              # the SVG spectrum is drawn
    assert panel.list.count() == 1
    picture = QImage(200, 100, QImage.Format.Format_RGB32)
    picture.fill(0)
    from si_assistant.ui.clipboard_image import image_to_png
    fake_clipboard(monkeypatch, sp.PastedSpectrum(image_to_png(picture), ".png", None, "",
                                                  "image", "Kept the copied picture as it is."),
                   kind="image")
    monkeypatch.setattr(sp, "choose_spectrum_type", lambda *a, **k: "13C")
    panel.paste_btn.click()
    assert panel.list.count() == 2
    assert "¹³C" in panel.list.currentItem().text()         # the new one is shown
    assert not panel.canvas.image.isNull()
    assert win.statusBar().currentMessage() == "Kept the copied picture as it is."
    assert [i["file"] for i in project.compound(cid).values["spectra"]] == \
        ["pasted 1H.svg", "pasted 13C.png"]


def qapp_process():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def test_a_damaged_svg_spectrum_is_named_and_left_out(project, compound):
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    from si_assistant.core import spectra as S
    project.ensure_spectra_section()
    project.attach_pasted_spectrum(compound, b"<svg width='10pt' height='10pt'><path d='M0",
                                   ".svg", "1H")
    problems = []
    (sp,) = S.spectra_of(project, compound, problems=problems, keep_unreadable=True)
    assert "pasted 1H.svg could not be read as an SVG drawing" in sp.problem
    result = compile_pdf(project, CompileOptions.from_settings(project.settings), image_info,
                         convert_to_png)   # (Typst is not stopped by it)
    assert any("pasted 1H.svg could not be read" in w for w in result.warnings)


# ---------------------------------------------------------------------- compiling

def test_compile_with_svg_and_png_spectra(project, compound, tmp_path):
    from pypdf import PdfReader
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import content_box, convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    project.ensure_spectra_section()
    compound.values["nmr"] = [{"id": "r1", "nucleus": "1H", "label": "",
                               "text": "**¹H NMR** (400 MHz, CDCl_{3}) δ 9.81"}]
    project.save_compound(compound)
    project.attach_pasted_spectrum(compound, emf_svg.convert(label_emf()).svg.encode(), ".svg",
                                   "1H", b"EMF", ".emf")
    pixels = __import__("numpy").full((300, 600, 3), 255, "uint8")
    pixels[140:160, 50:550] = 0
    png = emf_svg.png_bytes(pixels, (round(150 / 0.0254),) * 2)
    project.attach_pasted_spectrum(compound, png, ".png", "13C", b"EMF", ".emf")
    defaults = project.spectra_defaults()
    defaults["auto_crop"] = True
    project.set_spectra_defaults(defaults)
    result = compile_pdf(project, CompileOptions.from_settings(project.settings), image_info,
                         convert_to_png, crop=content_box)
    reader = PdfReader(str(result.pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    # The SVG's text stays text in the PDF (each letter where the metafile put it; this
    # hand-made metafile spaces them evenly, so the reader sees gaps between them).
    assert "Pastedlabel7.26" in text.replace(" ", "")
    spectra_pages = [p for p in reader.pages if "NMR" in p.extract_text()]
    assert len(spectra_pages) >= 2
    assert not [w for w in result.warnings if "pasted" in w.lower()]
