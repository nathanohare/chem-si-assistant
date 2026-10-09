"""Image helpers that need Qt: opening large pictures, size/resolution, converting TIFF to PNG,
and drawing SVG spectra (SPEC §13l.1)."""

from __future__ import annotations

import html
import os
import re
import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

DEFAULT_DPI = 300  # for pictures that don't say (most scanned/exported figures are ≥300 dpi)

# Qt refuses to open pictures that need more than 256 MB of memory (about 67 megapixels).
# Pictures pasted from ChemDraw at 1200 dpi can be up to 100 megapixels (400 MB; see
# ui/clipboard_image.MAX_PIXELS), so the limit is raised for this program.
ALLOCATION_LIMIT_MB = 1024


def allow_large_images() -> None:
    """Let Qt open pictures up to ALLOCATION_LIMIT_MB. Safe to call before the
    application object exists, and more than once."""
    if 0 < QImageReader.allocationLimit() < ALLOCATION_LIMIT_MB:
        QImageReader.setAllocationLimit(ALLOCATION_LIMIT_MB)


allow_large_images()  # anything that opens pictures imports this module


SVG_DPI = 150    # an SVG drawn as a picture without a size asked for


def read_image(path: Path, max_side: int = 0) -> QImage:
    """The picture at `path` (a null QImage if it can't be read). With `max_side`, a
    copy no larger than that many pixels on its longest side, for showing on screen
    (it still records the original's dpi, so don't use it to work out print sizes).
    An SVG, or the first page of a PDF (ChemDraw's copy on macOS, SPEC §13n.1), is drawn
    at SVG_DPI (or to fit `max_side`)."""
    if Path(path).suffix.lower() == ".svg":
        return render_svg_file(Path(path), SVG_DPI, max_side)
    if Path(path).suffix.lower() == ".pdf":
        return render_pdf_file(Path(path), SVG_DPI, max_side)
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if max_side and size.isValid() and max(size.width(), size.height()) > max_side:
        scaled = size.scaled(QSize(max_side, max_side), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(QSize(max(1, scaled.width()), max(1, scaled.height())))
    image = reader.read()
    return image if image is not None else QImage()


def png_dpi(path: Path) -> float | None:
    """The resolution recorded in a PNG file's pHYs chunk, or None (not a PNG, or no
    resolution). Read straight from the file, without decoding the picture."""
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return None
                length, kind = struct.unpack(">I4s", head)
                if kind == b"pHYs":
                    data = f.read(9)
                    if len(data) < 9:
                        return None
                    x, _y, unit = struct.unpack(">IIB", data)
                    return x * 0.0254 if unit == 1 and x > 0 else None
                if kind in (b"IDAT", b"IEND"):
                    return None  # pHYs must come before the picture data
                f.seek(length + 4, 1)  # skip the data and its checksum
    except (OSError, struct.error):
        return None


# image_info's answers, kept until the file changes (its size or modification time), so a
# JPEG is not decoded again at every build. A file that can't be read is not remembered
# (it may be an online-only OneDrive file, or locked, and readable next time).
_INFO: dict[tuple, tuple[int, int, float]] = {}
_INFO_MAX = 500


def image_info(path: Path) -> tuple[int, int, float] | None:
    """(width px, height px, dpi) of a raster image, or None if it can't be read. An SVG
    gives its printed size at DEFAULT_DPI (so width / dpi is its width in inches), and so
    does a PDF (its first page)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    hit = _INFO.get(key)
    if hit is not None:
        return hit
    info = _read_image_info(path)
    if info is not None:
        if len(_INFO) >= _INFO_MAX:
            _INFO.clear()
        _INFO[key] = info
    return info


def _read_image_info(path: Path) -> tuple[int, int, float] | None:
    if Path(path).suffix.lower() in (".svg", ".pdf"):
        size = (svg_file_size_in(Path(path)) if Path(path).suffix.lower() == ".svg"
                else pdf_size_in(Path(path)))
        if size is None:
            return None
        return max(1, round(size[0] * DEFAULT_DPI)), max(1, round(size[1] * DEFAULT_DPI)), \
            float(DEFAULT_DPI)
    reader = QImageReader(str(path))
    size = reader.size()
    dpi = png_dpi(path)
    if not size.isValid() or (dpi is None and bytes(reader.format()) != b"png"):
        # The resolution comes with the picture: read only a corner of it (decoding a
        # large JPEG whole takes a tenth of a second); the size is the file's own.
        image = QImage()
        if size.isValid():
            probe = QImageReader(str(path))
            probe.setClipRect(QRect(0, 0, min(8, size.width()), min(8, size.height())))
            image = probe.read()
        if image.isNull():
            image = QImage(str(path))
            if image.isNull():
                return None
            size = image.size()
        dpm = image.dotsPerMeterX()
        dpi = dpm * 0.0254 if dpm else None
    if not dpi:
        dpi = DEFAULT_DPI
    # Qt reports 96 dpi (3780 dots/m) when a file has no resolution: treat that as unknown.
    if abs(dpi - 96) < 0.5:
        dpi = DEFAULT_DPI
    return size.width(), size.height(), dpi


CROP_DPI = 100   # resolution at which spectra are measured for auto-crop


def render_page(path: Path, page: int | None, dpi: float = CROP_DPI) -> QImage:
    """A PDF page (from 1) or a picture, drawn on white at about `dpi` (a null QImage if
    it can't be read). A PDF is read from memory, so the file is never left open."""
    from PySide6.QtCore import QBuffer, QIODevice
    if path.suffix.lower() == ".svg":   # (drawn at the resolution asked for: it is vector)
        image = render_svg_file(path, dpi, 4000)
    elif page is None or path.suffix.lower() != ".pdf":
        image = read_image(path, max_side=2000)
    else:
        from PySide6.QtPdf import QPdfDocument
        data = QByteArray(path.read_bytes())
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        doc = QPdfDocument()
        doc.load(buffer)
        if doc.status() != QPdfDocument.Status.Ready or not 1 <= page <= doc.pageCount():
            return QImage()
        size = doc.pagePointSize(page - 1)
        px = QSize(max(1, round(size.width() * dpi / 72)), max(1, round(size.height() * dpi / 72)))
        image = doc.render(page - 1, px)
        doc.close()
    if image.isNull():
        return image
    # Transparent parts count as white paper.
    out = QImage(image.size(), QImage.Format.Format_RGB32)
    out.fill(QColor("white"))
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def content_box(path: Path, page: int | None) -> tuple[float, float, float, float] | None:
    """Where a spectrum page's content is, as fractions (left, top, right, bottom) of the
    page, with a small margin: everything that is not (nearly) white (SPEC §13k.12.8).
    None if the page is blank, can't be read, or has nothing to cut."""
    import numpy as np
    from ..core.spectra import content_box_of
    image = render_page(Path(path), page)
    if image.isNull():
        return None
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h, stride = gray.width(), gray.height(), gray.bytesPerLine()
    arr = np.frombuffer(gray.constBits(), np.uint8, count=stride * h).reshape(h, stride)[:, :w]
    return content_box_of(arr)


# ---------------------------------------------------------------------- SVG (SPEC §13l.1)
# Spectra pasted from Mnova are kept as SVG files (core/emf_svg.py). Typst prints them with
# every SVG feature they use; Qt's SVG renderer, which draws them on screen, measures them
# for auto-crop and checks each conversion, lacks three of them. qt_friendly_svg() rewrites
# those parts for Qt only (the file itself is never changed):
#   * clip paths (<clipPath>, which Qt ignores) become masks, which it supports;
#   * a list of fonts ("'Arial', Helvetica, sans-serif") becomes its first font (Qt reads
#     the whole list as one name and falls back to its default font);
#   * a text whose characters each have their own x position becomes one text per
#     character (Qt uses only the first position).

_FONT_LIST = re.compile(r'font-family="([^"]*)"')
_CLIP_PATH = re.compile(r'<clipPath id="([^"]+)"(?: clip-path="url\(#([^")]+)\)")?>(.*?)</clipPath>',
                        re.S)
_TEXT_X = re.compile(r'<text([^>]*?) x="([^"]*\s[^"]*)"([^>]*)>([^<]*)</text>')


def _first_font(match: re.Match) -> str:
    names = [n.strip().strip("'\"") for n in html.unescape(match.group(1)).split(",")]
    first = next((n for n in names if n), "")
    return f'font-family="{html.escape(first, quote=True)}"'


def _clip_to_mask(match: re.Match) -> str:
    # (A clip path that is itself clipped — the intersection of several clip shapes,
    # core/emf_svg.py — becomes a mask whose content is masked by the other one.)
    body = match.group(3).replace("clip-rule=", "fill-rule=")
    inner = f' mask="url(#{match.group(2)})"' if match.group(2) else ""
    return (f'<mask id="{match.group(1)}"><g fill="#ffffff" stroke="none"{inner}>{body}</g>'
            "</mask>")


def _split_text(match: re.Match) -> str:
    before, xs, after, content = match.groups()
    chars = html.unescape(content)
    positions = xs.split()
    if len(positions) < 2:
        return match.group(0)
    out = []
    for i, c in enumerate(chars):
        if not c.strip():
            continue
        x = positions[min(i, len(positions) - 1)]
        out.append(f'<text{before} x="{x}"{after}>{html.escape(c, quote=False)}</text>')
    return "".join(out)


def qt_friendly_svg(text: str) -> str:
    """The SVG rewritten for Qt's renderer (see above); unchanged if none of it applies."""
    if "font-family=" in text:
        text = _FONT_LIST.sub(_first_font, text)
    if "<clipPath" in text:
        text = _CLIP_PATH.sub(_clip_to_mask, text)
        text = text.replace('clip-path="url(#', 'mask="url(#')
    if "<text" in text:
        text = _TEXT_X.sub(_split_text, text)
    return text


def svg_file_size_in(path: Path) -> tuple[float, float] | None:
    """The printed size of an SVG file in inches (None if it can't be read)."""
    from .document import svg_size_in
    try:
        with open(path, "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None
    return svg_size_in(head)


def render_svg_bytes(data: bytes, width: int, height: int) -> QImage:
    """An SVG drawn on white at width × height pixels (a null QImage if it can't be read)."""
    from PySide6.QtSvg import QSvgRenderer
    text = data.decode("utf-8", "replace")
    renderer = QSvgRenderer(QByteArray(qt_friendly_svg(text).encode("utf-8")))
    if not renderer.isValid() or width <= 0 or height <= 0:
        return QImage()
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    return image


def render_svg_file(path: Path, dpi: float = SVG_DPI, max_side: int = 0) -> QImage:
    """An SVG file drawn at `dpi` (or smaller, to fit `max_side` pixels), recording that
    resolution; a null QImage if it can't be read."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return QImage()
    from .document import svg_size_in
    size = svg_size_in(data[:8192].decode("utf-8", "replace"))
    if size is None or size[0] <= 0 or size[1] <= 0:
        return QImage()
    w, h = size[0] * dpi, size[1] * dpi
    if max_side and max(w, h) > max_side:
        k = max_side / max(w, h)
        w, h, dpi = w * k, h * k, dpi * k
    image = render_svg_bytes(data, max(1, round(w)), max(1, round(h)))
    if not image.isNull():
        dpm = round(dpi / 0.0254)
        image.setDotsPerMeterX(dpm)
        image.setDotsPerMeterY(dpm)
    return image


def pdf_size_in(path: Path) -> tuple[float, float] | None:
    """The size of a PDF's first page in inches (None if it can't be read)."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtPdf import QPdfDocument
    try:
        data = QByteArray(Path(path).read_bytes())
    except OSError:
        return None
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument()
    doc.load(buffer)
    try:
        if doc.status() != QPdfDocument.Status.Ready or doc.pageCount() < 1:
            return None
        size = doc.pagePointSize(0)
    finally:
        doc.close()
    w, h = size.width() / 72, size.height() / 72
    return (w, h) if w > 0 and h > 0 else None


def render_pdf_file(path: Path, dpi: float = SVG_DPI, max_side: int = 0) -> QImage:
    """The first page of a PDF drawn on white at `dpi` (or smaller, to fit `max_side`
    pixels), recording that resolution; a null QImage if it can't be read."""
    size = pdf_size_in(Path(path))
    if size is None:
        return QImage()
    longest = max(size) * dpi
    if max_side and longest > max_side:
        dpi *= max_side / longest
    image = render_page(Path(path), 1, dpi)
    if not image.isNull():
        dpm = round(dpi / 0.0254)
        image.setDotsPerMeterX(dpm)
        image.setDotsPerMeterY(dpm)
    return image


def convert_to_png(source: Path, dest: Path) -> bool:
    # Turned as the file says (as render_page reads it), so an auto-crop box measured on
    # the original fits the converted copy.
    image = read_image(Path(source))
    if image.isNull():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    return image.save(str(dest), "PNG")
