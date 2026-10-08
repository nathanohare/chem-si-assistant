"""Large pasted pictures (1200 dpi, SPEC §13k.6) can be opened again, shown and measured.

Qt normally refuses pictures over 256 MB (about 67 megapixels), but a full-page scheme
pasted at 1200 dpi is bigger than that; the program raises the limit."""

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen

from si_assistant.compile.qt_images import ALLOCATION_LIMIT_MB, image_info, png_dpi, read_image

DPM_1200 = round(1200 / 0.0254)
BIG = (8200, 8400)  # 69 megapixels: about 7 x 7 inches at 1200 dpi, over Qt's default 256 MB


def save_png(path, size, dpm=DPM_1200, line=True):
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    if line:
        p = QPainter(image)
        p.setPen(QPen(QColor("black"), 12))
        p.drawLine(size[0] // 4, size[1] // 2, 3 * size[0] // 4, size[1] // 2)
        p.end()
    if dpm:
        image.setDotsPerMeterX(dpm)
        image.setDotsPerMeterY(dpm)
    assert image.save(str(path), "PNG")
    return path


@pytest.fixture(scope="module")
def big_png(qapp, tmp_path_factory):
    return save_png(tmp_path_factory.mktemp("big") / "scheme.png", BIG)


def test_limit_covers_the_largest_pasted_picture(qapp):
    from si_assistant.ui import clipboard_image as ci
    assert QImageReader.allocationLimit() >= ALLOCATION_LIMIT_MB
    assert ALLOCATION_LIMIT_MB * 2 ** 20 > 4 * ci.MAX_PIXELS     # 4 bytes per pixel


def test_big_picture_opens_again(qapp, big_png):
    assert BIG[0] * BIG[1] * 4 > 256 * 2 ** 20                   # over Qt's default limit
    image = QImage(str(big_png))
    assert not image.isNull() and (image.width(), image.height()) == BIG
    del image
    small = read_image(big_png, 3000)
    assert max(small.width(), small.height()) == 3000
    assert small.width() / small.height() == pytest.approx(BIG[0] / BIG[1], rel=0.01)


def test_image_info_reads_dpi_without_opening_the_picture(qapp, big_png, tmp_path):
    assert png_dpi(big_png) == pytest.approx(1200, abs=0.1)
    w, h, dpi = image_info(big_png)
    assert (w, h) == BIG and dpi == pytest.approx(1200, abs=0.1)
    plain = save_png(tmp_path / "plain.png", (40, 30), dpm=0, line=False)
    assert image_info(plain)[2] == 300                            # no resolution: assume 300
    assert png_dpi(tmp_path / "missing.png") is None
    (tmp_path / "x.jpg").write_bytes(b"not a png")
    assert png_dpi(tmp_path / "x.jpg") is None


def test_picture_thumbnails_are_kept_until_the_file_changes(qapp, tmp_path):
    import os
    from si_assistant.ui.structure_widgets import picture_pixmap
    path = save_png(tmp_path / "structure_image.png", (2000, 1000))
    first = picture_pixmap(path, QSize(96, 64))
    assert (first.width(), first.height()) == (96, 48)
    assert picture_pixmap(path, QSize(96, 64)) is first          # not read again
    save_png(path, (1000, 1000))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    changed = picture_pixmap(path, QSize(96, 64))
    assert (changed.width(), changed.height()) == (64, 64)
    assert picture_pixmap(tmp_path / "missing.png", QSize(96, 64)).isNull()
