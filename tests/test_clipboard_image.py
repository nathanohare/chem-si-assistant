import sys

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF
from PySide6.QtGui import QColor, QImage, QPageSize, QPainter, QPdfWriter


def test_trim_white_keeps_margin(qapp):
    from si_assistant.ui.clipboard_image import trim_white
    image = QImage(400, 300, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    p = QPainter(image)
    p.fillRect(100, 50, 40, 20, QColor("black"))
    p.end()
    out = trim_white(image, margin=5)
    assert (out.width(), out.height()) == (50, 30)
    assert out.pixelColor(25, 15) == QColor("black")


def test_trim_transparent_background_becomes_white(qapp):
    from si_assistant.ui.clipboard_image import trim_white
    image = QImage(50, 50, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    p = QPainter(image)
    p.fillRect(10, 10, 10, 10, QColor("blue"))
    p.end()
    out = trim_white(image, margin=2)
    assert out.size().width() == 14
    assert out.pixelColor(0, 0) == QColor("white")


def dpi_of(image: QImage) -> float:
    return image.dotsPerMeterX() * 0.0254


# ---------------------------------------------------------------------- resolution rules

def test_fit_dpi_supersamples_only_below_1200(qapp):
    from si_assistant.ui.clipboard_image import fit_dpi
    assert fit_dpi(2.0, 1.0, 600) == (600, 2)
    assert fit_dpi(2.0, 1.0, 1200) == (1200, 1)   # no extra smoothing pass at 1200 dpi


def test_fit_dpi_lowers_resolution_of_huge_pictures(qapp):
    from si_assistant.ui import clipboard_image as ci
    dpi, ss = ci.fit_dpi(12.0, 10.0, 1200)        # 12 x 10 in at 1200 dpi = 173 megapixels
    assert ss == 1 and dpi < 1200
    assert 12 * 10 * dpi ** 2 == pytest.approx(ci.MAX_PIXELS, rel=1e-6)
    # Big enough that 2x smoothing would be too much, small enough for plain 600 dpi.
    dpi, ss = ci.fit_dpi(10.0, 8.0, 600)
    assert (dpi, ss) == (600, 1)


def test_paste_dpi_setting(qapp):
    from si_assistant.ui.clipboard_image import paste_dpi
    assert paste_dpi({}) == 1200
    assert paste_dpi({"paste_dpi": 600}) == 600
    assert paste_dpi({"paste_dpi": 300}) == 1200   # not a choice: the default


def test_new_projects_paste_at_1200_dpi(project):
    assert project.settings["paste_dpi"] == 1200


# ---------------------------------------------------------------------- PDF (macOS ChemDraw)

def make_pdf(width_mm=50, height_mm=20) -> bytes:
    """A vector PDF like ChemDraw's: a 30 x 10 mm black box on a 50 x 20 mm page."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setPageSize(QPageSize(QSizeF(width_mm, height_mm), QPageSize.Unit.Millimeter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(1200)
    p = QPainter(writer)
    mm = 1200 / 25.4
    p.fillRect(round(10 * mm), round(5 * mm), round(30 * mm), round(10 * mm), QColor("black"))
    p.end()
    buffer.close()
    return bytes(data)


@pytest.mark.parametrize("dpi", [600, 1200])
def test_render_pdf_records_resolution(qapp, dpi):
    from si_assistant.ui import clipboard_image as ci
    image = ci.render_pdf(make_pdf(), dpi)
    assert image is not None
    assert image.width() == pytest.approx(50 / 25.4 * dpi, rel=0.005)  # page size is in whole pt
    assert dpi_of(image) == pytest.approx(dpi, abs=0.1)
    trimmed = ci.render_vector(make_pdf(), ".pdf", dpi)
    # The 30 mm box plus a margin that is the same physical size at either resolution.
    assert trimmed.width() / dpi * 25.4 == pytest.approx(30 + 2 * 0.508, abs=0.4)


def test_huge_pdf_keeps_its_print_size(qapp, monkeypatch):
    from si_assistant.ui import clipboard_image as ci
    monkeypatch.setattr(ci, "MAX_PIXELS", 500_000)   # pretend memory is short
    image = ci.render_pdf(make_pdf(), 1200)
    assert image.width() * image.height() <= 510_000
    assert dpi_of(image) < 1200
    # Width in mm from pixels and the recorded resolution = the real 50 mm.
    assert image.width() / dpi_of(image) * 25.4 == pytest.approx(50, abs=0.3)


# ---------------------------------------------------------------------- EMF (Windows ChemDraw)

def make_emf():
    """Build a vector picture (EMF) the way ChemDraw does: 50 x 20 mm, a 30 x 10 mm box."""
    import ctypes
    from ctypes import wintypes
    from si_assistant.ui import clipboard_image as ci

    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateEnhMetaFileW.restype = ctypes.c_void_p
    gdi32.CreateEnhMetaFileW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                         ctypes.c_void_p, wintypes.LPCWSTR]
    gdi32.CloseEnhMetaFile.restype = ctypes.c_void_p
    gdi32.CloseEnhMetaFile.argtypes = [ctypes.c_void_p]
    gdi32.GetStockObject.restype = ctypes.c_void_p
    gdi32.SelectObject.restype = ctypes.c_void_p  # handles are 64-bit: must not be truncated
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.Rectangle.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4

    frame = ci._RECT(0, 0, 5000, 2000)  # 50 mm x 20 mm, in 0.01 mm
    dc = gdi32.CreateEnhMetaFileW(None, None, ctypes.byref(frame), None)
    # Draw in 0.01 mm units (MM_HIMETRIC, y axis pointing up) so the test does not depend
    # on the screen's resolution or Windows display scaling: a 30 x 10 mm black box.
    gdi32.SetMapMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetMapMode(dc, 3)
    gdi32.SelectObject(dc, gdi32.GetStockObject(4))  # BLACK_BRUSH
    gdi32.Rectangle(dc, 1000, -500, 4000, -1500)
    return gdi32.CloseEnhMetaFile(dc)


@pytest.mark.skipif(sys.platform != "win32", reason="Enhanced Metafiles are Windows-only")
@pytest.mark.parametrize("dpi, size", [(600, (1181, 472)), (1200, (2362, 945))])
def test_render_emf_like_chemdraw(qapp, dpi, size):
    from si_assistant.ui import clipboard_image as ci
    hemf = make_emf()
    try:
        image = ci.render_emf(hemf, dpi)
        data = ci.emf_bytes(hemf)
    finally:
        ci._DeleteEnhMetaFile(hemf)
    assert image is not None
    # 50 mm x 20 mm at 600 dpi = 1181 x 472 px; at 1200 dpi twice that.
    assert abs(image.width() - size[0]) <= 2 and abs(image.height() - size[1]) <= 2
    assert dpi_of(image) == pytest.approx(dpi, abs=0.1)
    assert image.pixelColor(image.width() // 2, image.height() // 2).lightness() < 60
    assert image.pixelColor(5, 5) == QColor("white")
    trimmed = ci.trim_white(image)
    assert trimmed.width() / dpi * 25.4 == pytest.approx(30 + 2 * 0.508, abs=0.3)
    # The saved original (EMF bytes) draws the same picture again.
    assert data[:4] == b"\x01\x00\x00\x00"   # EMR_HEADER
    again = ci.render_vector(data, ".emf", dpi)
    assert (again.width(), again.height()) == (trimmed.width(), trimmed.height())


@pytest.mark.skipif(sys.platform != "win32", reason="Enhanced Metafiles are Windows-only")
def test_no_supersampling_at_1200_dpi(qapp, monkeypatch):
    """At 1200 dpi the bitmap is drawn once at full size (no 2x pass then shrink)."""
    from si_assistant.ui import clipboard_image as ci
    calls = []
    original = QImage.scaled
    monkeypatch.setattr(QImage, "scaled", lambda self, *a, **k: calls.append(a) or
                        original(self, *a, **k))
    hemf = make_emf()
    try:
        ci.render_emf(hemf, 1200)
        assert calls == []
        ci.render_emf(hemf, 600)
        assert len(calls) == 1
    finally:
        ci._DeleteEnhMetaFile(hemf)


# ---------------------------------------------------------------------- re-rendering stored originals

def test_rerender_pasted_pictures(qapp, project):
    from si_assistant.ui import clipboard_image as ci
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id, name="with original")
    plain = project.add_compound(s.id, name="screenshot")
    pdf = make_pdf()
    first = ci.render_vector(pdf, ".pdf", 600)
    project.set_structure_image(c, data=ci.image_to_png(first), vector=pdf, vector_ext=".pdf")
    project.set_structure_image(plain, data=ci.image_to_png(first))
    picture = project.structure_image_path(c)
    assert picture.with_suffix(".pdf").read_bytes() == pdf

    done, without, failed = ci.rerender_pasted_pictures(project, 1200)
    assert (done, without, failed) == (1, 1, 0)
    again = QImage(str(picture))
    assert dpi_of(again) == pytest.approx(1200, abs=0.1)
    assert again.width() == pytest.approx(2 * first.width(), abs=4)
    assert dpi_of(QImage(str(project.structure_image_path(plain)))) == pytest.approx(600, abs=0.1)


def test_rerender_can_be_stopped_part_way(qapp, project):
    from si_assistant.ui import clipboard_image as ci
    s = project.add_section("data", "Compounds")
    pdf = make_pdf()
    first = ci.image_to_png(ci.render_vector(pdf, ".pdf", 600))
    pictures = []
    for name in ("A", "B", "C"):
        c = project.add_compound(s.id, name=name)
        project.set_structure_image(c, data=first, vector=pdf, vector_ext=".pdf")
        pictures.append(project.structure_image_path(c))
    seen = []

    def progress(i, total):
        seen.append((i, total))
        return i < 1                       # "Stop" pressed after the first picture

    assert ci.rerender_pasted_pictures(project, 1200, progress) == (1, 0, 0)
    assert seen == [(0, 3), (1, 3)]
    assert dpi_of(QImage(str(pictures[0]))) == pytest.approx(1200, abs=0.1)
    for picture in pictures[1:]:           # left as they were, still whole pictures
        assert dpi_of(QImage(str(picture))) == pytest.approx(600, abs=0.1)