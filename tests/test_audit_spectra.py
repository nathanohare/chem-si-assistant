"""Fixes from the audit of the spectra code (core/spectra.py, ui/spectra_page.py,
ui/spectrum_paste.py): remembered page counts, files that can't be read just now (locked,
or online-only OneDrive files while offline) not being called damaged or remembered as
such, the screen cache's memory budget, the box on an empty canvas, copied files that are
not pasted, and the blank-picture test."""

import os
import shutil
from collections import OrderedDict
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from si_assistant.core import spectra as S
from si_assistant.core.project import Project

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">' \
      '<line x1="0" y1="0" x2="100" y2="50" stroke="black"/></svg>'


def blank_pdf(path: Path, pages: int = 1) -> Path:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=792, height=612)
    with open(path, "wb") as f:
        w.write(f)
    return path


def bump(path: Path, seconds: int = 10) -> None:
    """Move a file's date on (so a change is seen even with coarse file-system times)."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


@pytest.fixture
def project(qapp, tmp_path):
    p = Project.create(tmp_path / "proj", "Spectra audit")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Compounds")
    c = p.add_compound(s.id, name="3-Phenylpropanal")
    if "spectra" not in c.field_keys:
        p.add_field(c, {"key": "spectra"})
    yield p, c
    p.close()


def attach(p, c, path: Path, kind: str = "1H") -> Path:
    item = p.attach_compound_file(c, "spectra", path, {"type": kind})
    return p.attachment_path(c, "spectra", item["file"])


# ---------------------------------------------------------------------- spectra-4

def test_pdf_page_counts_are_remembered_until_the_file_changes(tmp_path, monkeypatch):
    import pypdf
    opened = []
    real = pypdf.PdfReader
    monkeypatch.setattr(pypdf, "PdfReader", lambda *a, **k: opened.append(a[0]) or real(*a, **k))
    pdf = blank_pdf(tmp_path / "a.pdf")
    one = tmp_path / "one.pdf"
    shutil.copy2(pdf, one)
    assert S.pdf_page_count(pdf) == 1
    assert S.pdf_page_count(pdf) == 1
    assert len(opened) == 1                                   # read once
    blank_pdf(pdf, pages=2)                                   # replaced by a 2-page PDF
    bump(pdf)
    assert S.pdf_page_count(pdf) == 2
    pdf.write_bytes(b"not a pdf")                             # damaged: raises every time
    bump(pdf, 20)
    for _ in range(2):
        with pytest.raises(Exception):
            S.pdf_page_count(pdf)
    shutil.copy2(one, pdf)                                    # an undo puts it back
    assert S.pdf_page_count(pdf) == 1


def test_spectra_of_follows_a_replaced_pdf(project, tmp_path):
    p, c = project
    path = attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    assert len(S.spectra_of(p, c)) == 1
    blank_pdf(path, pages=3)
    bump(path)
    assert [sp.page for sp in S.spectra_of(p, c)] == [1, 2, 3]


# ---------------------------------------------------------------------- spectra-1

def test_an_svg_that_cant_be_read_just_now_is_not_remembered_as_damaged(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    svg = tmp_path / "s.svg"
    svg.write_text(SVG, encoding="utf-8")
    real = ET.parse

    def locked(*a, **k):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(ET, "parse", locked)
    assert S.svg_readable(svg) is False and S.svg_state(svg) is None
    monkeypatch.setattr(ET, "parse", real)                    # the lock is gone
    assert S.svg_readable(svg) is True
    # A damaged SVG is still remembered (until it changes).
    bad = tmp_path / "bad.svg"
    bad.write_text("<svg", encoding="utf-8")
    assert S.svg_state(bad) is False
    monkeypatch.setattr(ET, "parse", lambda *a, **k: pytest.fail("read again"))
    assert S.svg_readable(bad) is False


def test_a_page_that_cant_be_drawn_is_not_kept_on_screen(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import spectra_page as sp
    monkeypatch.setattr(sp, "_pages", OrderedDict())
    monkeypatch.setattr(sp, "_pages_bytes", 0)
    pdf = blank_pdf(tmp_path / "a.pdf")
    good = QImage(20, 10, QImage.Format.Format_RGB32)
    results = [OSError("locked"), QImage(), good]

    def render(path, page, dpi):
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(sp, "render_page", render)
    assert sp.page_image(pdf, 1).isNull()                      # locked: no error, not kept
    assert sp.page_image(pdf, 1).isNull()                      # failed to draw: not kept
    assert not sp.page_image(pdf, 1).isNull()                  # readable again: drawn
    assert not sp.page_image(pdf, 1).isNull() and not results  # (now kept)


# ---------------------------------------------------------------------- spectra-6

def test_a_locked_pdf_is_not_called_damaged(project, tmp_path):
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))

    def locked(path):
        raise PermissionError(13, "locked")

    problems: list[str] = []
    out = S.spectra_of(p, c, page_count=locked, problems=problems, keep_unreadable=True)
    assert len(out) == 1 and out[0].problem == problems[0]
    assert "could not be read just now" in problems[0] and "damaged" not in problems[0]

    def damaged(path):
        raise ValueError("bad xref")

    problems = []
    S.spectra_of(p, c, page_count=damaged, problems=problems)
    assert problems == [S.unreadable_message("c_1H.pdf")]


def test_a_locked_svg_is_not_called_damaged(project, tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    p, c = project
    svg = tmp_path / "c_13C.svg"
    svg.write_text(SVG, encoding="utf-8")
    attach(p, c, svg, "13C")
    real = ET.parse
    monkeypatch.setattr(ET, "parse", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError(13, "locked")))
    problems: list[str] = []
    assert S.spectra_of(p, c, problems=problems) == []
    assert "could not be read just now" in problems[0]
    monkeypatch.setattr(ET, "parse", real)
    problems = []
    assert len(S.spectra_of(p, c, problems=problems)) == 1 and problems == []


def test_the_spectra_page_says_a_locked_file_cant_be_read_just_now(project, tmp_path,
                                                                     monkeypatch):
    from si_assistant.ui import spectra_page as sp
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    monkeypatch.setattr(sp, "page_image", lambda path, page: QImage())
    monkeypatch.setattr(sp, "_openable", lambda path: False)
    panel = sp.SpectraPagePanel(p, c)
    assert "could not be read just now" in panel.canvas.message
    monkeypatch.setattr(sp, "_openable", lambda path: True)
    panel._show_spectrum(0)
    assert "may be damaged" in panel.canvas.message
    panel.deleteLater()


# ---------------------------------------------------------------------- spectra-2

def test_a_crop_that_failed_is_not_remembered(project, tmp_path, monkeypatch):
    p, c = project
    pdf = attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))

    def unreadable(path, page):
        raise OSError("locked")

    cache = S.CropCache(p, unreadable)
    assert cache.box(pdf, 1) is None
    cache.save()
    assert not cache.path.exists() or "c_1H.pdf" not in cache.path.read_text(encoding="utf-8")
    assert S.CropCache(p, lambda *_: (0.1, 0.1, 0.9, 0.9)).box(pdf, 1) == (0.1, 0.1, 0.9, 0.9)


def test_nothing_to_cut_from_a_file_that_cant_be_opened_is_not_remembered(project, tmp_path,
                                                                            monkeypatch):
    p, c = project
    png = tmp_path / "c_IR.png"
    QImage(40, 20, QImage.Format.Format_RGB32).save(str(png))
    png = attach(p, c, png, "IR")

    def locked(*a, **k):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(S, "open", locked, raising=False)    # (a picture reads as empty)
    cache = S.CropCache(p, lambda *_: None)
    assert cache.box(png, None) is None
    monkeypatch.delattr(S, "open")
    cache.save()
    assert S.CropCache(p, lambda *_: (0.2, 0.2, 0.8, 0.8)).box(png, None) == (0.2, 0.2, 0.8, 0.8)
    # A readable page with nothing to cut is still remembered (measured once).
    cache = S.CropCache(p, lambda *_: None)
    pdf = attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    assert cache.box(pdf, 1) is None
    cache.save()
    assert S.CropCache(p, lambda *_: pytest.fail("measured again")).box(pdf, 1) is None


# ---------------------------------------------------------------------- spectra-3

def test_pasting_on_the_spectra_page_says_the_spectra_changed(project, tmp_path, monkeypatch):
    from si_assistant.ui import spectrum_paste as paste
    from si_assistant.ui.clipboard_image import image_to_png
    from si_assistant.ui.spectra_page import SpectraPagePanel
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    picture = QImage(200, 100, QImage.Format.Format_RGB32)
    picture.fill(0)
    monkeypatch.setattr(paste, "clipboard_kind", lambda: "image")
    monkeypatch.setattr(paste, "clipboard_spectrum", lambda k=None: paste.PastedSpectrum(
        image_to_png(picture), ".png", None, "", "image", "Kept the copied picture."))
    monkeypatch.setattr(paste, "choose_spectrum_type", lambda *a, **k: "13C")
    QGuiApplication.clipboard().setMimeData(QMimeData())
    panel = SpectraPagePanel(p, c)
    changed = []
    panel.spectra_changed.connect(lambda: changed.append(1))
    panel.paste_spectrum()
    assert changed == [1] and panel.list.count() == 2
    panel.deleteLater()


# ---------------------------------------------------------------------- spectra-5

def test_the_screen_cache_keeps_to_its_memory_budget(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import spectra_page as sp
    monkeypatch.setattr(sp, "_pages", OrderedDict())
    monkeypatch.setattr(sp, "_pages_bytes", 0)
    monkeypatch.setattr(sp, "PAGE_CACHE_BYTES", 3 * 1000 * 1000)
    drawn = []

    def render(path, page, dpi):
        drawn.append(path.name)
        image = QImage(500, 500, QImage.Format.Format_RGB32)   # 1 MB each
        image.fill(QColor("white"))
        return image

    monkeypatch.setattr(sp, "render_page", render)
    files = []
    for i in range(6):
        f = tmp_path / f"s{i}.png"
        f.write_bytes(b"x" * (i + 1))
        files.append(f)
        sp.page_image(f, None)
        total = sum(im.sizeInBytes() for im in sp._pages.values())
        assert total == sp._pages_bytes <= sp.PAGE_CACHE_BYTES
        assert next(reversed(sp._pages))[0] == str(f)          # the newest is kept
    assert len(sp._pages) == 3
    sp.page_image(files[-1], None)                             # still kept: not drawn again
    assert drawn.count(files[-1].name) == 1
    files[-1].write_bytes(b"changed")                          # a changed file is drawn again
    bump(files[-1])
    sp.page_image(files[-1], None)
    assert drawn.count(files[-1].name) == 2
    # One page bigger than the whole budget is still kept (and shown).
    monkeypatch.setattr(sp, "PAGE_CACHE_BYTES", 10)
    assert not sp.page_image(files[0], None).isNull() and len(sp._pages) == 1
    assert sp._pages_bytes == sum(im.sizeInBytes() for im in sp._pages.values())


# ---------------------------------------------------------------------- spectra-7

def test_the_hidden_box_cant_be_dragged_on_an_empty_canvas(project, tmp_path):
    from si_assistant.ui.spectra_page import SpectraPagePanel
    p, c = project
    path = attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    path.unlink()                                              # the file is missing
    panel = SpectraPagePanel(p, c)
    canvas = panel.canvas
    canvas.resize(600, 450)
    assert canvas.image.isNull() and canvas.editable
    box = canvas.box_rect()
    assert not box.isEmpty()                                   # (the box is there, unseen)
    start = box.center().toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert not canvas._mode
    QTest.mouseMove(canvas, start + QPoint(80, 60))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start + QPoint(80, 60))
    panel.flush()
    item = next(i for i in p.compound(c.id).values["spectra"] if i["file"] == "c_1H.pdf")
    assert "pages" not in item
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
    panel.deleteLater()


# ---------------------------------------------------------------------- spectra-8

def test_copied_files_that_are_not_pasted_are_named(project, tmp_path, monkeypatch):
    from si_assistant.ui import spectrum_paste as paste
    p, c = project
    a = blank_pdf(tmp_path / "a_1H.pdf")
    b = tmp_path / "b.mnova"
    b.write_bytes(b"MNOVA")
    d = blank_pdf(tmp_path / "d_13C.pdf")
    monkeypatch.setattr(paste, "clipboard_files", lambda: [a, b, d])
    parent = QWidget()
    outcome = paste.paste_spectrum(parent, p, c)
    assert [i["file"] for i in outcome.items] == ["a_1H.pdf", "d_13C.pdf"]
    assert "Not pasted: b.mnova" in outcome.message
    # Many: the first five are named.
    extra = []
    for i in range(7):
        f = tmp_path / f"x{i}.mnova"
        f.write_bytes(b"MNOVA")
        extra.append(f)
    e = blank_pdf(tmp_path / "e_1H.pdf")
    monkeypatch.setattr(paste, "clipboard_files", lambda: [e, *extra])
    monkeypatch.setattr(paste, "choose_spectrum_type", lambda *a, **k: "1H")
    outcome = paste.paste_spectrum(parent, p, c)
    assert "x4.mnova and 2 more" in outcome.message
    parent.deleteLater()


# ---------------------------------------------------------------------- spectra-9

def test_the_blank_test_agrees_with_ink(qapp):
    from si_assistant.ui import spectrum_paste as paste

    def page(fill, dot=None, fmt=QImage.Format.Format_RGB32, size=(37, 23)):
        image = QImage(*size, fmt)
        image.fill(fill)
        if dot is not None:
            image.setPixelColor(30, 20, dot)
        return image

    cases = [page(QColor("white")), page(QColor(200, 200, 200)),
             page(QColor(199, 199, 199)), page(QColor(0, 0, 0, 0), fmt=QImage.Format.Format_ARGB32)]
    for channel in range(3):
        rgb = [255, 255, 255]
        rgb[channel] = 199
        cases.append(page(QColor("white"), QColor(*rgb)))
        rgb[channel] = 200
        cases.append(page(QColor("white"), QColor(*rgb)))
    cases.append(page(QColor("white"), QColor("black"), fmt=QImage.Format.Format_RGB888,
                      size=(33, 21)))
    for image in cases:
        assert paste._blank(image) == (not paste.ink(image)[0].any())
    assert paste._blank(page(QColor("white"))) is True
    assert paste._blank(page(QColor("white"), QColor(255, 199, 255))) is False
    assert paste._blank(QImage()) is True


# ---------------------------------------------------------------------- handoffs from other groups

def encrypted_pdf(path: Path) -> Path:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=792, height=612)
    w.encrypt("secret", algorithm="RC4-128")
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_a_password_protected_pdf_is_called_that(project, tmp_path, monkeypatch):
    import pypdf
    p, c = project
    pdf = attach(p, c, encrypted_pdf(tmp_path / "c_1H.pdf"))
    with pytest.raises(S.PdfUnreadable) as e:
        S.pdf_page_count(pdf)
    assert e.value.encrypted
    problems: list[str] = []
    out = S.spectra_of(p, c, problems=problems, keep_unreadable=True)
    assert problems == [S.encrypted_message("c_1H.pdf")] and out[0].problem == problems[0]
    assert "password-protected" in problems[0]
    # Remembered until the file changes (the Spectra page asks again and again).
    real = pypdf.PdfReader
    monkeypatch.setattr(pypdf, "PdfReader", lambda *a, **k: pytest.fail("read again"))
    with pytest.raises(S.PdfUnreadable):
        S.pdf_page_count(pdf)

    # The compile's own page counter says so with an `encrypted` attribute.
    def compile_count(path):
        e = ValueError("File has not been decrypted")
        e.encrypted = True
        raise e

    problems = []
    S.spectra_of(p, c, page_count=compile_count, problems=problems)
    assert problems == [S.encrypted_message("c_1H.pdf")]
    # A damaged PDF is still called damaged.
    monkeypatch.setattr(pypdf, "PdfReader", real)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(S.PdfUnreadable) as e:
        S.pdf_page_count(bad)
    assert not e.value.encrypted


def test_hand_edited_values_do_not_stop_the_spectra(project, tmp_path):
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    item = dict(c.values["spectra"][0])
    c.values["nmr"] = ["x", 3]
    assert S.nmr_conditions(c, "1H") == ("", "")
    assert S.guess_pasted_type(c) == "Other"
    assert S.nmr_nucleus({"type": S.OTHER_NMR, "file": "a.pdf"}, c) is None
    c.values["nmr"] = 5
    assert S.nmr_conditions(c, "1H") == ("", "") and S.guess_pasted_type(c) == "Other"
    assert [i for _k, i in S.spectrum_items(p, c)] == [item]
    c.values["spectra"] = 7                                    # not a list at all
    assert S.spectrum_items(p, c) == [] and S.guess_pasted_type(c) == "1H"
    c.values["nmr"] = [{"nucleus": "1H", "text": "1H NMR (400 MHz, CDCl3) δ 7.26"}]
    assert S.nmr_conditions(c, "1H") == ("400 MHz", "CDCl3")


def test_pasted_files_are_typed_knowing_the_compound_id(project, tmp_path, monkeypatch):
    from si_assistant.ui import field_widgets
    from si_assistant.ui import spectrum_paste as paste
    p, c = project
    shown = p.id_report().display_id(c.id)
    seen = []
    real = field_widgets.guess_spectrum_type
    monkeypatch.setattr(field_widgets, "guess_spectrum_type",
                        lambda name, cid="": seen.append((name, cid)) or real(name, cid))
    parent = QWidget()
    one = blank_pdf(tmp_path / "a_1H.pdf")
    monkeypatch.setattr(paste, "clipboard_files", lambda: [one])
    monkeypatch.setattr(paste, "choose_spectrum_type", lambda *a, **k: "1H")
    paste.paste_spectrum(parent, p, c)
    two = [blank_pdf(tmp_path / "b_13C.pdf"), blank_pdf(tmp_path / "d_19F.pdf")]
    monkeypatch.setattr(paste, "clipboard_files", lambda: two)
    paste.paste_spectrum(parent, p, c)
    assert seen == [("a_1H.pdf", shown), ("b_13C.pdf", shown), ("d_19F.pdf", shown)]
    parent.deleteLater()


def fail_one_write(monkeypatch):
    """The next file write fails (e.g. compound.json held by OneDrive), after the change
    is already made in memory; later writes work again."""
    from si_assistant.core import project as project_mod
    real = project_mod.atomic_write_json

    def fail(*a, **k):
        monkeypatch.setattr(project_mod, "atomic_write_json", real)
        raise PermissionError("The file is in use by another program.")

    monkeypatch.setattr(project_mod, "atomic_write_json", fail)


def test_a_spectrum_setting_that_failed_to_save_is_saved_on_flush(project, tmp_path,
                                                                     monkeypatch):
    from si_assistant.ui import spectra_page as sp
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    panel = sp.SpectraPagePanel(p, c)
    warned = []
    monkeypatch.setattr(sp.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    panel.scale.setValue(panel.scale.value() + 20)
    assert panel._timer.isActive()
    fail_one_write(monkeypatch)
    panel._save()                                              # (the timer's save) fails
    assert warned and panel._unsaved and not panel._timer.isActive()
    page = panel.current.key
    panel.flush()                                              # leaving the page: tried again
    assert not panel._unsaved
    reopened = Project.open(p.folder, read_only=True)          # (what is on disk)
    item = reopened.compound(c.id).values["spectra"][0]
    assert S.overrides(item, page).get("struct_scale")
    panel.deleteLater()


def test_spectra_defaults_that_failed_to_save_are_saved_on_flush(project, monkeypatch):
    from si_assistant.ui import spectra_panel as sp
    p, _c = project
    panel = sp.SpectraSectionPanel(p, p.ensure_spectra_section())
    warned = []
    monkeypatch.setattr(sp.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    panel.header_line.setChecked(not panel.header_line.isChecked())
    fail_one_write(monkeypatch)
    panel._save()                                              # (the timer's save) fails
    assert "saved again" in warned[0] and panel._unsaved
    panel.flush()                                              # leaving the page: tried again
    assert not panel._unsaved
    reopened = Project.open(p.folder, read_only=True)          # (what is on disk)
    assert reopened.spectra_defaults()["header_line"] == panel.header_line.isChecked()
    panel.deleteLater()


# ---------------------------------------------------------------------- late round 2

def test_a_hand_edited_file_name_that_is_not_text_does_not_stop_the_spectra(project,
                                                                            tmp_path):
    p, c = project
    attach(p, c, blank_pdf(tmp_path / "c_1H.pdf"))
    good = dict(c.values["spectra"][0])
    c.values["spectra"] += [{"file": 5, "type": "13C"}, {"file": None, "type": "19F"},
                            {"type": "IR"}]
    shown = S.spectra_of(p, c)
    assert [s.item for s in shown] == [good, {"file": 5, "type": "13C"}]
    assert shown[1].path.name == "5"            # (a missing file, reported by the checks)


def test_nothing_is_pasted_when_typing_could_not_be_saved(project, monkeypatch):
    from si_assistant.ui import spectrum_paste as paste
    p, c = project
    looked = []
    monkeypatch.setattr(paste, "clipboard_files", lambda: looked.append(1) or [])
    assert paste.paste_spectrum(None, p, c, lambda: False) is None
    assert looked == [] and not c.values.get("spectra")
    told = []                                       # (saved, or nothing to save: goes on)
    monkeypatch.setattr(paste, "clipboard_kind", lambda: "")
    monkeypatch.setattr(paste.QMessageBox, "information", lambda *a: told.append(a[1]))
    assert paste.paste_spectrum(None, p, c, lambda: None) is None
    assert looked == [1] and told == ["Nothing to paste"]


def test_moving_a_spectrum_skips_items_with_no_file_name(project, tmp_path):
    from si_assistant.ui.spectra_page import SpectraPagePanel
    p, c = project
    for name, kind in (("a_13C.pdf", "13C"), ("b_19F.pdf", "19F"), ("c_IR.pdf", "IR")):
        attach(p, c, blank_pdf(tmp_path / name), kind)
    c.values["spectra"].insert(1, {"type": "1H"})     # (hand-edited: no file; sorts first)
    files = [("spectra", "a_13C.pdf"), ("spectra", "b_19F.pdf"), ("spectra", "c_IR.pdf")]
    assert S.moved(p, c, 2, -1) == [files[0], files[2], files[1]]
    assert S.moved(p, c, 0, -1) is None and S.moved(p, c, 2, 1) is None
    panel = SpectraPagePanel(p, c)
    assert panel._files() == files
    panel.current = panel.spectra[2]                     # (c_IR.pdf shown)
    panel.move_file(-1)
    assert panel._files() == [files[0], files[2], files[1]]
    assert [tuple(k) for k in S.manual_order(c)] == [files[0], files[2], files[1]]
    panel.deleteLater()
