"""Fixes from the audit of the compile step (group "compile"): made-up data only."""

import json
import os
from pathlib import Path

import pytest

from si_assistant.compile import document as doc
from si_assistant.compile import qt_images
from si_assistant.compile.blocks import _parts_sentence, typed_formula_runs
from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import CompileError, compile_pdf, failed_file, run_typst
from si_assistant.compile.text import charge_runs
from si_assistant.core import fields as F
from si_assistant.core import front
from si_assistant.core import ms_reports as ms
from si_assistant.core import scheme_editor as se
from si_assistant.core.markup import SUB, SUPER
from si_assistant.core.project import Project
from tests.sample_project import build_sample, fake_spectrum_pdf


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def options(p, **kw) -> CompileOptions:
    o = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def source(p, name="t", converter=convert_to_png, **kw):
    result = build(p, options(p, **kw), image_info, converter, name=name)
    return result.typ_path.read_text(encoding="utf-8"), result


def picture_bytes(fmt: str, w: int = 60, h: int = 40, dpi: float = 300) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0x3366aa)
    img.setDotsPerMeterX(round(dpi / 0.0254))
    img.setDotsPerMeterY(round(dpi / 0.0254))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert img.save(buffer, fmt)
    return bytes(buffer.data())


# A 1 × 1 pixel GIF (Qt can read GIFs but not write them).
GIF_1PX = bytes.fromhex("474946383961010001008000000000" "00ffffff21f90401000000002c00000000"
                        "010001000002024401003b")


def figure_file(p, parts) -> Path:
    """The PNG of the figure in the sample's "Reaction Optimization" section."""
    found = [f for f in parts["notes"].folder.rglob("*.png")]
    assert len(found) == 1
    return found[0]


def touch_later(path: Path) -> None:
    """Make sure a rewritten file has a newer modification time (coarse clocks)."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


# ---------------------------------------------------------------------- compile-1

def test_damaged_rasters_are_found_without_rejecting_what_typst_prints(qapp, tmp_path):
    png, jpg = picture_bytes("PNG"), picture_bytes("JPG")
    gif = GIF_1PX
    cases = {
        "good.png": (png, True), "good.jpg": (jpg, True), "good.gif": (gif, True),
        "cut.png": (png[:len(png) // 3], False), "empty.png": (b"", False),
        "html.png": (b"<html><body>Not found</body></html>", False),
        "jpeg_named.png": (jpg, False),            # (Typst reads a .png as a PNG)
        "tail.jpg": (jpg + b"\0" * 5000, True),     # data after the end: Typst prints it
        "half.jpg": (jpg[:len(jpg) // 2 + 300], True),   # cut short: Typst prints it too
        "no_trailer.gif": (gif[:-1], True),
        "other.tif": (b"anything", True),           # (not checked: converted instead)
    }
    for name, (data, ok) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert doc.raster_readable(tmp_path / name) is ok, name
    assert doc.raster_kind(tmp_path / "jpeg_named.png") == ".jpg"
    # Remembered until the file changes: a repaired file is checked again.
    fixed = tmp_path / "cut.png"
    fixed.write_bytes(png)
    touch_later(fixed)
    assert doc.raster_readable(fixed)
    assert doc.raster_readable(tmp_path / "missing.png") is None   # (not "damaged")


def test_a_damaged_picture_is_left_out_with_a_warning_naming_it(sample):
    p, parts = sample
    figure = figure_file(p, parts)
    figure.write_bytes(figure.read_bytes()[:200])
    result = compile_pdf(p, options(p), image_info, convert_to_png)   # (no CompileError)
    assert result.pdf.exists()
    rel = figure.relative_to(p.folder).as_posix()
    assert any(rel in w and "could not be read as a picture" in w for w in result.warnings)


def test_a_jpeg_saved_as_png_is_still_printed(sample):
    p, parts = sample
    figure = figure_file(p, parts)
    figure.write_bytes(picture_bytes("JPG", 600, 300))
    src, result = source(p, name="SI")
    assert not any("could not be read" in w for w in result.warnings)
    assert figure.name not in src                    # (Typst would stop at it)
    assert f"/{figure.stem}_" in src                 # printed from a PNG copy in assets/
    compile_pdf(p, options(p), image_info, convert_to_png)


def test_typst_failing_on_a_picture_names_the_file(qapp, tmp_path):
    bad = tmp_path / "pics" / "figure.png"
    bad.parent.mkdir()
    bad.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
    typ = tmp_path / "a.typ"
    typ.write_text('#image("/pics/figure.png", width: 2in)\n', encoding="utf-8")
    with pytest.raises(CompileError) as info:
        run_typst(typ, tmp_path / "a.pdf", tmp_path)
    assert "pics/figure.png" in str(info.value)
    assert failed_file(info.value.__cause__) == "pics/figure.png"
    # An error about something else names no file.
    typ.write_text('#image("/pics/figure.png" + )\n', encoding="utf-8")
    with pytest.raises(CompileError) as info:
        run_typst(typ, tmp_path / "a.pdf", tmp_path)
    assert "The file it could not read" not in str(info.value)


# ---------------------------------------------------------------------- compile-2

def test_an_encrypted_pdf_is_called_password_protected_not_damaged(qapp, tmp_path, monkeypatch):
    import pypdf
    from pypdf.errors import DependencyError

    class Locked:
        def __init__(self, *a, **k):
            raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    pdf = fake_spectrum_pdf(tmp_path / "enc_report.pdf", "locked")
    monkeypatch.setattr(pypdf, "PdfReader", Locked)
    p = Project.create(tmp_path, "Enc")
    b = doc.Builder(p, CompileOptions(), image_info)
    assert b.pdf_pages(pdf) == []
    assert "password-protected" in b.warnings[0] and "damaged" not in b.warnings[0]
    with pytest.raises(doc.PdfUnreadable) as info:
        doc.pdf_page_count(pdf)
    assert info.value.encrypted


# ---------------------------------------------------------------------- compile-8

def test_pdf_pages_are_read_once_until_the_file_changes(sample, monkeypatch):
    import pypdf
    p, parts = sample
    opened = []
    real = pypdf.PdfReader

    def counting(*a, **k):
        opened.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(pypdf, "PdfReader", counting)
    first, _ = source(p)
    n = len(opened)
    second, _ = source(p)
    assert len(opened) == n                         # nothing opened again
    assert first.count("si-spectrum(") == second.count("si-spectrum(") == 3
    # The 2-page ¹³C spectrum replaced by a 1-page one (as the program writes: a new file).
    c2 = parts["c2"]
    item = next(i for i in c2.values["spectra"] if i["type"] == "13C")
    path = p.attachment_path(c2, "spectra", item["file"])
    new = fake_spectrum_pdf(path.with_name("new.pdf"), "13C again", pages=1)
    os.replace(new, path)
    touch_later(path)
    third, _ = source(p)
    assert third.count("si-spectrum(") == 2
    # Damaged: the same note as before, and read again once it is repaired.
    good = path.read_bytes()
    path.write_bytes(b"not a pdf at all")
    src, result = source(p)
    assert src.count("si-spectrum(") == 1
    assert any("could not be opened as a PDF" in w for w in result.warnings)
    path.write_bytes(good)
    touch_later(path)
    assert source(p)[0].count("si-spectrum(") == 2


def test_pdf_page_sizes_follow_crop_box_and_rotation(qapp, tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject
    pdf = fake_spectrum_pdf(tmp_path / "s.pdf", "x", pages=2)   # 11 x 8.5 in
    w = PdfWriter(clone_from=PdfReader(str(pdf)))
    w.pages[0].cropbox = RectangleObject((0, 0, 396, 612))
    w.pages[1].rotate(90)
    out = tmp_path / "t.pdf"
    with open(out, "wb") as f:
        w.write(f)
    assert doc.pdf_page_sizes(out) == [(396, 612), (612, 792)]
    assert doc.pdf_page_size(out, 3) is None
    assert doc.pdf_size_in(out) == (5.5, 8.5)
    b = doc.Builder(Project.create(tmp_path, "Sz"), CompileOptions(), image_info)
    assert b.page_aspect(out, 2) == pytest.approx(792 / 612)
    with pytest.raises(OSError):   # (a file that can't be read now is not remembered)
        doc.pdf_page_sizes(tmp_path / "missing.pdf")


# ---------------------------------------------------------------------- compile-7

def add_printed_reports(p, compounds, tmp_path, n=2):
    for c in compounds:
        for i in range(n):
            src = tmp_path / f"r_{c.id}_{i}.png"
            src.write_bytes(picture_bytes("PNG", 300, 200))
            p.add_hrms_report(c, src)
        c.values["hrms"][ms.PRINT_KEY] = True
        p.save_compound(c)


def test_the_spectra_sample_shows_only_a_few_hrms_reports(sample, tmp_path):
    p, parts = sample
    add_printed_reports(p, [parts["c2"], parts["c4"]], tmp_path)
    live, result = source(p, only=("section", parts["spectra"].id), preview_spectra=False,
                          spectra_limit=2)
    assert live.count("HRMS report: ") == doc.HRMS_SAMPLE
    assert any("first 2 HRMS reports" in w for w in result.warnings)
    assert any("first 2 of 3 spectra" in w for w in result.warnings)   # (as before)
    toolbar, result = source(p, only=("section", parts["spectra"].id))
    assert toolbar.count("HRMS report: ") == 4
    assert not any("HRMS reports;" in w for w in result.warnings)
    assert source(p, only=("hrms", ""))[0].count("HRMS report: ") == 4


# ---------------------------------------------------------------------- compile-3

def test_the_toc_preview_leaves_out_what_the_compile_window_leaves_out(sample, tmp_path):
    p, parts = sample
    add_printed_reports(p, [parts["c2"]], tmp_path, n=1)
    front.store_excluded(p.settings, {parts["data"].id})
    full, _ = source(p, name="SI")
    toc, _ = source(p, only=("front", "toc"))
    assert full.count("si-spectrum(") == toc.count("si-spectrum(") == 0
    # The HRMS reports sub-section unticked on its own.
    front.store_excluded(p.settings, {front.HRMS_KEY})
    full, _ = source(p, name="SI")
    toc, _ = source(p, only=("front", "toc"))
    assert doc.HRMS_HEADING not in full and doc.HRMS_HEADING not in toc
    assert full.count("si-spectrum(") == toc.count("si-spectrum(") == 3
    # Other previews still show everything.
    assert "HRMS report: " in source(p, only=("hrms", ""))[0]
    assert source(p, only=("section", parts["spectra"].id))[0].count("si-spectrum(") == 3


# ---------------------------------------------------------------------- compile-9

def test_a_structure_picture_is_converted_once_per_build(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.set_structure_image(c2, data=picture_bytes("TIFF", 300, 150), ext=".tif")
    calls = []

    def counting(src, dest):
        calls.append(src)
        return convert_to_png(src, dest)

    src, result = source(p, converter=counting)
    picture = p.structure_image_path(c2)
    assert calls.count(picture) == 1
    # Under the compound and on each of its 3 spectrum pages, all from that one copy.
    copy = f"/{picture.stem}_"
    assert src.count(copy) == 4
    _src, without = source(p, converter=None)
    # Said once for the block and once for the spectrum boxes, not once per spectrum page.
    assert sum("Cannot place" in w and picture.name in w for w in without.warnings) == 2


# ---------------------------------------------------------------------- compile-4

def test_hand_edited_values_of_the_wrong_kind_do_not_stop_the_compile(sample, tmp_path):
    p, parts = sample
    c2 = parts["c2"]
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(picture_bytes("PNG"))
    path = c2.folder / "compound.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["values"].update({"nmr": ["x", 3], "hrms": "x", "rf": "0.3", "yield": [1],
                           "physical_state": "oil", "references": {"refs": [1, "", 2]},
                           "files": [{"file": str(outside), "placement": "inline"}]})
    path.write_text(json.dumps(data), encoding="utf-8")
    p.settings["journal_style"] = None
    p.save_settings()
    p.close()
    p = Project.open(p.folder)
    p.acquire_lock("Tester")
    try:   # (the compound's block; its spectra pages are core/spectra's)
        src, result = source(p, only=("compound", c2.id), preview_spectra=False)
        assert "si-compound-title" in src and "NMR" not in src
        assert any("outside the project folder" in w for w in result.warnings)
        assert not any("style is not available" in w for w in result.warnings)
    finally:
        p.close()


def test_an_unexpected_error_in_a_compound_names_the_compound(sample, monkeypatch):
    p, parts = sample

    def broken(*a, **k):
        raise TypeError("unexpected")

    monkeypatch.setattr(doc, "compound_block", broken)
    with pytest.raises(CompileError) as info:
        source(p, only=("compound", parts["c2"].id))
    assert str(info.value).startswith(p.id_report().display_id(parts["c2"].id) + " (")


# ---------------------------------------------------------------------- compile-6

def test_a_preview_keeps_the_drawings_of_si_typ(sample):
    p, parts = sample
    opts = options(p, structures="acs")
    si = compile_pdf(p, opts, image_info, convert_to_png)
    before = sorted(x.name for x in (p.folder / "build" / "assets").iterdir())
    assert before
    compile_pdf(p, options(p, structures="acs", only=("compound", parts["c2"].id)),
                image_info, convert_to_png, name="preview")
    assert sorted(x.name for x in (p.folder / "build" / "assets").iterdir()) == before
    assert (p.folder / "build" / "assets-preview" / f"{parts['c2'].id}.svg").exists()
    run_typst(si.typ, si.typ.with_name("again.pdf"), p.folder)   # (still compiles by hand)


# ---------------------------------------------------------------------- compile-10

def test_compile_runs_typst_in_a_worker_and_reports_problems(sample, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from si_assistant.ui import compile_ui
    p, parts = sample
    window = QWidget()
    result = compile_ui.run_compile(window, p, options(p))
    assert result is not None and result.pdf.exists() and result.pdf.name == "SI.pdf"
    shown = []
    monkeypatch.setattr(compile_ui.QMessageBox, "warning", lambda *a: shown.append(a[2]))

    def failing(*a, **k):
        raise CompileError("Typst said no")

    monkeypatch.setattr(compile_ui, "typeset_pdf", failing)
    assert compile_ui.run_compile(window, p, options(p)) is None
    assert shown == ["Typst said no"]
    assert compile_ui.in_background(lambda: 42) == 42
    with pytest.raises(ValueError):
        compile_ui.in_background(lambda: int("x"))
    # The main window is not closed under a compile that is still running.
    import time
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMainWindow
    main = QMainWindow()
    main.show()
    ticks = []
    QTimer.singleShot(20, main.close)
    QTimer.singleShot(40, lambda: ticks.append(1))   # (the window keeps working)
    compile_ui.in_background(lambda: time.sleep(0.4) or 1)
    assert main.isVisible() and ticks == [1]
    assert main.close() and not main.isVisible()     # (closes normally afterwards)


# ---------------------------------------------------------------------- compile-11

def test_image_info_is_remembered_until_the_picture_changes(qapp, tmp_path):
    jpg = tmp_path / "f.jpg"
    jpg.write_bytes(picture_bytes("JPG", 120, 60, dpi=600))
    assert image_info(jpg) == pytest.approx((120, 60, 600), rel=1e-3)
    jpg.write_bytes(picture_bytes("JPG", 120, 60, dpi=1200))
    touch_later(jpg)
    assert image_info(jpg) == pytest.approx((120, 60, 1200), rel=1e-3)
    junk = tmp_path / "g.jpg"
    junk.write_bytes(b"junk")
    assert image_info(junk) is None
    junk.write_bytes(picture_bytes("JPG", 30, 20))
    assert image_info(junk) == pytest.approx((30, 20, 300), rel=1e-3)
    # The same answers as reading the whole picture.
    big = tmp_path / "big.jpg"
    big.write_bytes(picture_bytes("JPG", 900, 700, dpi=450))
    assert qt_images._read_image_info(big) == pytest.approx((900, 700, 450), rel=1e-3)


# ---------------------------------------------------------------------- scheme-9

def test_a_condition_structure_with_a_damaged_picture_is_drawn_instead(qapp, tmp_path):
    from tests.test_review13n_fixes import scheme_with_inline
    from tests.test_scheme_editor import new_project
    from tests.test_scheme_editor import source as scheme_source
    p, data = new_project(tmp_path)
    k, sid = scheme_with_inline(p, data, "A")
    se.inline_path(k, sid).write_text("<svg this is damaged", encoding="utf-8")
    src, result = scheme_source(p, p.scheme(k.id))
    assert f"{k.id}_{sid}.svg" in src
    assert not any("structure in the conditions could not be drawn" in w
                   for w in result.warnings)


# ---------------------------------------------------------------------- hunt-1

def test_the_preview_of_a_scheme_compound_shows_its_structure_as_the_si_does(sample):
    p, parts = sample
    k, c4 = parts["scheme"], parts["c4"]
    side = p.add_compound(parts["data"].id, name="side product", scheme_id=k.id)
    p.set_structure(side, "c1ccncc1")
    assert "#si-structure(" in source(p, only=("compound", side.id))[0]
    assert "#si-structure(" not in source(p, only=("compound", c4.id))[0]
    product = se.steps(p.scheme(k.id))[0]["products"][0]["id"]
    se.unlink_box(p, p.scheme(k.id), product)
    assert "#si-structure(" in source(p, only=("compound", c4.id))[0]


# ---------------------------------------------------------------------- hunt-4

def show(runs) -> str:
    return "".join(f"^{{{r.text}}}" if r.valign == SUPER else r.text for r in runs)


@pytest.mark.parametrize("label, shown", [
    ("[M]+.", "[M]^{+•}"), ("[M]+·", "[M]^{+•}"), ("[M]+•", "[M]^{+•}"),
    ("[M]-.", "[M]^{−•}"), ("[M+H]+.", "[M+H]^{+•}"), ("[M+H]+", "[M+H]^{+}"),
    ("[M-H]-", "[M−H]^{−}"), ("[M+2H]2+", "[M+2H]^{2+}"), ("odd", "odd")])
def test_adduct_charges_print_as_the_page_shows_them(label, shown):
    assert show(charge_runs(label)) == shown


# ---------------------------------------------------------------------- hunt-5

class _Info:
    formula = "C2H4O2"


class _Project:
    def structure_info(self, compound, strict=False):
        return _Info()


class _Compound:
    def __init__(self, ea):
        self.values = {"ea": ea}


EA = F.FieldDef("ea", "Elemental analysis", "parts",
                (F.Part("calcd", "Calcd"), F.Part("found", "Found")))


def ea(**value) -> str:
    runs = _parts_sentence(_Project(), _Compound(value), EA, True)
    return "".join(f"_{{{r.text}}}" if r.valign == SUB else r.text for r in runs)


def test_elemental_analysis_without_calculated_values_prints_no_empty_calcd():
    assert ea(found="C, 40.10; H, 6.65") == "Anal. Found: C, 40.10; H, 6.65."
    assert ea(calcd="C, 40.00; H, 6.71") == "Anal. Calcd for C_{2}H_{4}O_{2}: C, 40.00; H, 6.71."
    assert ea(calcd="C, 40.00; H, 6.71", found="C, 40.10; H, 6.65") == \
        "Anal. Calcd for C_{2}H_{4}O_{2}: C, 40.00; H, 6.71. Found: C, 40.10; H, 6.65."
    assert ea() == ""
    # A formula typed for the values (a hydrate) replaces the structure's.
    assert ea(calcd="C, 39.1", formula="C2H4O2·0.25H2O") == \
        "Anal. Calcd for C_{2}H_{4}O_{2}·0.25H_{2}O: C, 39.1."


def test_typed_formulas_keep_their_dots_and_coefficients():
    def plain(text):
        return "".join(f"_{{{r.text}}}" if r.valign == SUB else r.text
                       for r in typed_formula_runs(text))
    assert plain("C12H15NO·0.25H2O") == "C_{12}H_{15}NO·0.25H_{2}O"
    assert plain("C12H15NO • HCl") == "C_{12}H_{15}NO•HCl"
    assert plain("C12H15NO + 0.25 H2O") == "C12H15NO + 0.25 H2O"   # (as typed)


# ---------------------------------------------------------------------- handoffs from other groups

def _offline_structures(p, monkeypatch):
    """structure.mol can't be read just now (an online-only OneDrive file while offline)."""
    from si_assistant.core import project as project_mod
    real = project_mod.read_text

    def read_text(path, *a, **k):
        if Path(path).name == "structure.mol":
            raise PermissionError("offline")
        return real(path, *a, **k)

    monkeypatch.setattr(project_mod, "read_text", read_text)
    p._structure_cache.clear()


def test_an_unreadable_structure_is_said_and_the_compile_goes_on(sample, monkeypatch):
    """Never left out silently, but never stopping the whole SI either (warnings never
    block; integration review): one plain note, as for an unreadable spectrum."""
    from si_assistant.compile import blocks
    p, parts = sample
    c2 = parts["c2"]
    _offline_structures(p, monkeypatch)
    assert p.read_structure(c2) is None          # (elsewhere: shown as no structure)
    text, result = source(p, only=("compound", c2.id), structures="acs")
    notes = [w for w in result.warnings if "could not be read just now" in w]
    assert len(notes) == 1 and "structure file" in notes[0]
    assert "calcd for" not in blocks._plain(blocks.hrms_sentence(p, c2))   # (found only)
    ea_def = F.FieldDef("ea", "Elemental analysis", "parts",
                        (F.Part("calcd", "Calcd"), F.Part("found", "Found")))
    c2.values["ea"] = {"calcd": "C, 73.6"}
    assert " for " not in blocks._plain(_parts_sentence(p, c2, ea_def, True))


def test_a_hand_edited_hrms_tolerance_does_not_stop_the_compile(sample):
    from si_assistant.compile import blocks
    p, parts = sample
    for bad in ("5 ppm", None, [5]):
        p.settings["hrms_tolerance_ppm"] = bad
        text = "".join(r.text for r in blocks.hrms_sentence(p, parts["c2"]))
        assert "calcd for" in text and "found" in text


def test_null_values_in_compound_json_print_nothing(sample):
    from si_assistant.compile import blocks
    p, parts = sample
    c2 = parts["c2"]

    def plain(runs):
        return "".join(r.text for r in runs)

    c2.values["yield"] = {"mass": None, "mass_unit": "mg", "percent": "92"}
    c2.values["physical_state"] = {"text": None}
    assert plain(blocks.state_and_yield(c2)) == "Yield: 92%."
    c2.values["yield"] = {"mass": 12.5, "mass_unit": "mg", "percent": None}
    assert plain(blocks.state_and_yield(c2)) == "Yield: 12.5 mg."
    c2.values["yield"] = "92"                    # (not a set of parts: ignored)
    assert blocks.state_and_yield(c2) == []
    rf = F.FieldDef("rf", "Rf", "parts", (F.Part("value", "Rf"), F.Part("eluent", "Eluent")))
    c2.values["rf"] = {"value": "0.3", "eluent": None}
    assert plain(_parts_sentence(p, c2, rf, True)) == "Rf = 0.3."
    c2.values["rf"] = {"value": None}
    assert _parts_sentence(p, c2, rf, True) == []
    assert blocks.nmr_sentence({"text": None, "label": None, "nucleus": None}, True) == []
    c2.values["hrms"] = {"found": None, "ionization": None, "adduct": None}
    text = plain(blocks.hrms_sentence(p, c2))
    assert "None" not in text and "calcd for" in text and "found" not in text
    c2.values["references"] = {"text": None, "refs": None}
    assert blocks.citation_sentence(p, c2) == []


def test_files_under_a_windows_device_name_are_printed_from_a_copy(sample, monkeypatch):
    p, parts = sample
    b = doc.Builder(p, options(p), image_info)
    b.assets.mkdir(parents=True, exist_ok=True)
    # (Windows won't make a folder called "aux": one is played by "devdir")
    monkeypatch.setattr(doc, "is_device_name", lambda name: name.lower() == "devdir")
    pic = p.folder / "devdir" / "pic.png"
    pic.parent.mkdir()
    pic.write_bytes(picture_bytes("PNG"))
    copy = b.usable_image(pic)
    assert copy.parent == b.assets and copy.suffix == ".png"
    assert copy.read_bytes() == pic.read_bytes()
    assert b.usable_image(pic) == copy           # (copied once per build)
    assert "devdir" not in b.rel(copy)
    pic.write_bytes(picture_bytes("PNG", 30, 30))   # the next build copies it again
    b2 = doc.Builder(p, options(p), image_info)
    assert b2.usable_image(pic).read_bytes() == pic.read_bytes()
    other = figure_file(p, parts)
    assert b.usable_image(other) == other        # (anything else: used where it is)
    from si_assistant.core.ids import is_device_name
    assert is_device_name("Aux") and is_device_name("con.png") and not is_device_name("auxin")


def test_an_svg_that_cannot_be_read_just_now_is_not_called_damaged(sample, monkeypatch):
    p, parts = sample
    b = doc.Builder(p, options(p), image_info)
    svg = p.folder / "drawing.svg"
    svg.write_text("<svg", encoding="utf-8")
    assert b.usable_image(svg) is None
    assert "damaged" in b.warnings[-1]
    monkeypatch.setattr(doc.spectra_mod, "svg_state", lambda path: None)
    assert b.usable_image(svg) is None
    assert "could not be read just now" in b.warnings[-1]


def test_a_read_only_project_is_not_compiled_from_the_compile_window(sample, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from si_assistant.ui import compile_ui
    p, parts = sample
    p.close()
    ro = Project.open(p.folder, read_only=True)
    shown = []
    monkeypatch.setattr(compile_ui.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(compile_ui, "build", lambda *a, **k: pytest.fail("built"))
    try:
        assert compile_ui.run_compile(QWidget(), ro, options(ro)) is None
        assert shown and "read-only" in shown[0]
    finally:
        ro.close()


# ---------------------------------------------------------------------- review of compile-1

def png_chunks(data: bytes) -> dict[bytes, int]:
    """Where each chunk's checksum starts, by kind."""
    import struct
    pos, found = 8, {}
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        found[kind] = pos + 8 + n
        pos += 12 + n
    return found


def test_picture_checks_are_no_stricter_than_typst(qapp, tmp_path):
    png, jpg = picture_bytes("PNG"), picture_bytes("JPG")
    crc = png_chunks(png)
    app0_end = 4 + (jpg[4] << 8 | jpg[5])
    cases = {
        # Stray bytes between a JPEG's headers: Typst (and Qt) skip them.
        "stray.jpg": (jpg[:app0_end] + bytes(3) + jpg[app0_end:], True),
        # A wrong checksum: Typst refuses it on IHDR and IDAT, not on other chunks.
        "ihdr_crc.png": (png[:crc[b"IHDR"]] + bytes(4) + png[crc[b"IHDR"] + 4:], False),
        "idat_crc.png": (png[:crc[b"IDAT"]] + bytes(4) + png[crc[b"IDAT"] + 4:], False),
        "iend_crc.png": (png[:crc[b"IEND"]] + bytes(4), True),
    }
    for name, (data, ok) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert doc.raster_readable(tmp_path / name) is ok, name
        typ = tmp_path / f"{name}.typ"
        typ.write_text(f'#image("/{name}", width: 1in)\n', encoding="utf-8")
        if ok:
            run_typst(typ, tmp_path / f"{name}.pdf", tmp_path)
        else:
            with pytest.raises(CompileError):
                run_typst(typ, tmp_path / f"{name}.pdf", tmp_path)
    # A folder named like a picture can't be opened: "not just now", not "damaged".
    (tmp_path / "folder.png").mkdir()
    assert doc.raster_readable(tmp_path / "folder.png") is None


def _png(w, h, raw=None, kind=2, depth=8, interlace=0, stream=None, palette=False,
         split=0) -> bytes:
    """A PNG made by hand (every checksum right), with picture data `raw` (default: w × h
    grey RGB rows) or the zlib `stream` as given; IDAT in pieces of `split` bytes."""
    import struct
    import zlib

    def chunk(kind_, data):
        return (struct.pack(">I", len(data)) + kind_ + data
                + struct.pack(">I", zlib.crc32(kind_ + data)))

    if raw is None:
        raw = (bytes(1) + bytes([0x60]) * (3 * w)) * h
    z = zlib.compress(raw) if stream is None else stream
    pieces = [z[i:i + split] for i in range(0, len(z), split)] if split else [z]
    return (bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, kind, 0, 0, interlace))
            + (chunk(b"PLTE", bytes(48)) if palette else b"")
            + b"".join(chunk(b"IDAT", p) for p in pieces) + chunk(b"IEND", b""))


def _adam7(w, h, bits) -> bytes:
    """All-zero picture data of an interlaced PNG (each pass's rows, filter 0)."""
    out = b""
    for x0, dx, y0, dy in doc._ADAM7:
        pw, ph = -(-(w - x0) // dx), -(-(h - y0) // dy)
        if pw > 0 and ph > 0:
            out += (bytes(1) + bytes(-(-(pw * bits) // 8))) * ph
    return out


def test_pictures_with_good_checksums_but_damaged_contents_are_found(qapp, tmp_path):
    """Damage made with the checksums worked out again (so the CRC check passes): each
    case is checked as Typst reads it, so a picture Typst would refuse is left out with
    a warning instead of stopping the whole PDF."""
    import zlib
    w, h = 20, 10
    raw = (bytes(1) + bytes([0x60]) * (3 * w)) * h
    z = zlib.compress(raw)
    jpg = picture_bytes("JPG")
    sof = jpg.find(bytes([0xFF, 0xC0]))
    sos = jpg.find(bytes([0xFF, 0xDA]))
    zero_h, zero_w = bytearray(jpg), bytearray(jpg)
    zero_h[sof + 5:sof + 7] = bytes(2)
    zero_w[sof + 7:sof + 9] = bytes(2)
    cases = {
        # Typst prints these:
        "good.png": (_png(w, h), True),
        "pieces.png": (_png(w, h, split=1), True),
        "no_adler.png": (_png(w, h, stream=z[:-4]), True),         # (checksum not read)
        "bad_adler.png": (_png(w, h, stream=z[:-4] + bytes(4)), True),
        "extra_rows.png": (_png(w, h, raw=raw + bytes(500)), True),
        "after_end.png": (_png(w, h, stream=z + b"junk"), True),
        "small_window.png": (_png(w, h, stream=bytes([0x08, 0x1D]) + z[2:]), True),
        "filter4.png": (_png(w, h, raw=(bytes([4]) + bytes(3 * w)) * h), True),
        "palette.png": (_png(5, 4, raw=(bytes(6)) * 4, kind=3, palette=True), True),
        "grey16.png": (_png(5, 4, raw=bytes(11) * 4, kind=0, depth=16), True),
        "interlaced.png": (_png(9, 9, raw=_adam7(9, 9, 24), interlace=1), True),
        "interlaced_1bit.png": (_png(13, 7, raw=_adam7(13, 7, 1), kind=0, depth=1,
                                     interlace=1), True),
        "picture.png": (picture_bytes("PNG", 900, 700), True),
        "good.jpg": (jpg, True),
        # Typst refuses these:
        "zero_width.png": (_png(0, h, raw=raw), False),
        "colour_type_5.png": (_png(w, h, kind=5), False),
        "bit_depth_3.png": (_png(w, h, depth=3), False),
        "interlace_2.png": (_png(w, h, interlace=2), False),
        "cut_stream.png": (_png(w, h, stream=z[:len(z) // 2]), False),
        "short_data.png": (_png(w, h, raw=raw[:-1]), False),
        "bad_zlib_header.png": (_png(w, h, stream=bytes([0x78, 0x00]) + z[2:]), False),
        "bad_deflate.png": (_png(w, h, stream=z[:2] + bytes([0xFF]) * 8 + z[10:]), False),
        "unknown_filter.png": (_png(w, h, raw=(bytes([9]) + bytes(3 * w)) * h), False),
        "no_palette.png": (_png(5, 4, raw=(bytes(6)) * 4, kind=3), False),
        "interlaced_short.png": (_png(9, 9, raw=_adam7(9, 9, 24)[:-1], interlace=1), False),
        "no_frame.jpg": (jpg[:2] + jpg[sos:], False),
        "zero_height.jpg": (bytes(zero_h), False),
        "zero_width.jpg": (bytes(zero_w), False),
    }
    for name, (data, ok) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert doc.raster_readable(tmp_path / name) is ok, name
        typ = tmp_path / f"{name}.typ"
        typ.write_text(f'#image("/{name}", width: 1in)\n', encoding="utf-8")
        if ok:
            run_typst(typ, tmp_path / f"{name}.pdf", tmp_path)
        else:
            with pytest.raises(CompileError):
                run_typst(typ, tmp_path / f"{name}.pdf", tmp_path)


def test_a_picture_that_fails_the_check_is_converted_before_it_is_left_out(sample):
    p, parts = sample
    figure = figure_file(p, parts)
    good = picture_bytes("PNG", 600, 300)
    crc = png_chunks(good)[b"IDAT"]
    figure.write_bytes(good[:crc] + bytes(4) + good[crc + 4:])

    def lenient(source_path, dest):   # (a converter that can read it)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(good)
        return True

    src, result = source(p, name="SI", converter=lenient)
    rel = figure.relative_to(p.folder).as_posix()
    assert f"/{figure.stem}_" in src and f"/{figure.name}" not in src
    assert any(rel in w and "may be damaged" in w for w in result.warnings)
    # Qt can't read it either: left out, with a warning naming it.
    src, result = source(p, name="SI")
    assert figure.stem not in src
    assert any(rel in w and "could not be read as a picture" in w for w in result.warnings)


def test_a_picture_that_cannot_be_opened_just_now_is_not_called_damaged(sample):
    p, parts = sample
    figure = figure_file(p, parts)
    figure.unlink()
    figure.mkdir()   # (exists, but can't be opened: as a locked or online-only file)
    src, result = source(p, name="SI")
    rel = figure.relative_to(p.folder).as_posix()
    assert any(rel in w and "could not be read just now" in w for w in result.warnings)
    assert not any("damaged" in w for w in result.warnings)


def test_no_pdf_is_saved_when_the_project_turns_read_only_during_the_compile(sample,
                                                                              monkeypatch):
    from PySide6.QtWidgets import QWidget
    from si_assistant.ui import compile_ui
    p, parts = sample
    shown = []
    monkeypatch.setattr(compile_ui.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    real = compile_ui.typeset_pdf

    def lock_lost_meanwhile(*a, **k):
        warnings = real(*a, **k)
        p.read_only = True   # (as MainWindow._check_lock does when the lock is taken over)
        return warnings

    monkeypatch.setattr(compile_ui, "typeset_pdf", lock_lost_meanwhile)
    try:
        assert compile_ui.run_compile(QWidget(), p, options(p)) is None
    finally:
        p.read_only = False
    build_dir = p.folder / "build"
    assert not (build_dir / "SI.pdf").exists()
    assert not list(build_dir.glob("*.partial.pdf"))
    assert shown and "read-only" in shown[0]


def test_elemental_analysis_without_a_formula_from_the_structure_leaves_it_out():
    """A structure whose formula can't be worked out (ChemError): the EA sentence is
    printed without " for <formula>" instead of stopping the compile."""
    from si_assistant.core import chem

    class GenericProject:
        def structure_info(self, compound, strict=False):
            raise chem.ChemError("generic atom")

    runs = _parts_sentence(GenericProject(), _Compound({"calcd": "C, 40.0"}), EA, True)
    assert "".join(r.text for r in runs) == "Anal. Calcd: C, 40.0."


GENERIC_MOL = """
  generic

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
    2.0000    0.0000    0.0000 R   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""


def give_generic_structure(p, compound) -> None:
    """A structure.mol with an R atom (an older file or a hand edit)."""
    p.structure_path(compound).write_text(GENERIC_MOL, encoding="utf-8")
    p._structure_cache.clear()
    assert p.read_structure(compound, strict=True) is None
    assert p.generic_structure_atoms(compound) == ["R"]


def test_elemental_analysis_of_a_generic_structure_leaves_out_the_formula(sample):
    """A real project with an R atom in structure.mol: no formula, so the EA sentence is
    printed without " for <formula>", and the SI still compiles."""
    from si_assistant.compile import blocks
    p, parts = sample
    c2 = parts["c2"]
    if "ea" not in c2.field_keys:
        p.add_field(c2, {"key": "ea"})
    c2.values["ea"] = {"calcd": "C, 40.0", "found": "C, 40.1"}
    give_generic_structure(p, c2)
    defn = next(d for d in p.field_defs(c2) if d.key == "ea")
    text = "".join(r.text for r in blocks._parts_sentence(p, c2, defn, True))
    assert text == "Anal. Calcd: C, 40.0. Found: C, 40.1."
    result = compile_pdf(p, options(p), image_info, convert_to_png,
                         crop=qt_images.content_box)
    assert result.pdf.exists()


@pytest.mark.parametrize("structures", ["pictures", "acs"])
def test_a_generic_structure_is_drawn_and_said_in_the_notes(sample, structures):
    """A structure.mol with a generic atom is drawn as it is (as on the compound's
    page), not silently left out, and the compile notes say why it has no formula."""
    p, parts = sample
    c2 = parts["c2"]
    picture = p.structure_image_path(c2)
    if picture is not None and picture.exists():
        picture.unlink()
    give_generic_structure(p, c2)
    src, result = source(p, name="SI", structures=structures)
    assert f"assets/{c2.id}.svg" in src
    notes = [w for w in result.warnings if "generic atom (R)" in w]
    assert len(notes) == 1   # (once, although it is shown again with its spectra)
    assert notes[0].startswith(p.id_report().display_id(c2.id) + " (")


def test_a_hand_edited_spectrum_names_its_compound(sample, monkeypatch):
    """A value of the wrong kind in the Spectra field (e.g. "file": 5 typed into
    compound.json) stops the compile with a message naming the compound, rather than a
    bare Python error escaping the Compile command."""
    from si_assistant.core import spectra as spectra_mod
    p, parts = sample
    c2 = parts["c2"]
    real = spectra_mod.spectra_of

    def broken(project, compound, *a, **k):
        if compound.id == c2.id:
            raise TypeError("unsupported operand type(s) for /: 'Path' and 'int'")
        return real(project, compound, *a, **k)

    monkeypatch.setattr(spectra_mod, "spectra_of", broken)
    with pytest.raises(CompileError) as info:
        source(p, name="SI")
    message = str(info.value)
    assert message.startswith(p.id_report().display_id(c2.id) + " (")
    assert "spectra" in message and "TypeError" in message


def test_inline_figures_are_the_items_labels_numbers(sample):
    from si_assistant.compile import blocks
    from si_assistant.core.labels import is_inline_figure
    p, parts = sample
    c2 = parts["c2"]
    key = next(d.key for d in p.field_defs(c2) if d.kind == "attachments" and d.key != "spectra")
    c2.values[key] = [{"file": "a.png", "caption": "A"},
                      {"file": "b.png", "placement": "end"},
                      {"file": ""}, "not an item", {"file": 0}]
    figures = blocks.inline_figures(p, c2)
    assert len(figures) == sum(is_inline_figure(i) for i in c2.values[key]) == 2
    assert [f.caption for f in figures][0] == "A"


def test_svg_size_in_skips_malformed_and_infinite_sizes():
    from si_assistant.compile.document import svg_size_in
    # A malformed width or an infinite one falls back to the viewBox (no crash, no inf).
    assert svg_size_in('<svg width="1.2.3" height="10" viewBox="0 0 72 144"></svg>') == (1.0, 2.0)
    assert svg_size_in('<svg width="1e999" height="10" viewBox="0 0 72 144"></svg>') == (1.0, 2.0)
    assert svg_size_in('<svg width="1e999pt" height="1e999pt"></svg>') is None
    assert svg_size_in('<svg viewBox="0 0 1e999 72"></svg>') is None
    assert svg_size_in('<svg viewBox="0 0 1.2.3 72"></svg>') is None
    # Normal sizes are read as before.
    assert svg_size_in('<svg width="72pt" height="1in"></svg>') == (1.0, 1.0)
    assert svg_size_in('<svg width=" .5in " height="36"></svg>') == (0.5, 0.5)
