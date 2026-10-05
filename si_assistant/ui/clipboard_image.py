"""Getting a picture from the clipboard, the way PowerPoint's "Paste as Picture" does.

ChemDraw puts drawings on the clipboard as a *vector* picture rather than an
ordinary image: an Enhanced Metafile (EMF) on Windows, a PDF on macOS. Qt
cannot read those directly, so they are rendered here into a high-resolution
PNG (1200 dpi by default; 600 dpi can be chosen in Project settings). The vector
original is handed back too, so it can be kept next to the PNG and re-rendered
later (SPEC §13k.6). Ordinary images (screenshots, PNG/JPEG copies) are used as
they are. White margins are trimmed in every case.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

PASTE_DPIS = (600, 1200)  # the choices in Project settings
DEFAULT_DPI = 1200
SUPERSAMPLE = 2           # below 1200 dpi: draw at 2x, then shrink smoothly (anti-aliasing for
                          # GDI drawings). At 1200 dpi it is not needed and would use too much memory.
MAX_PIXELS = 100_000_000  # largest bitmap drawn (~400 MB); bigger pictures get a lower dpi
MAX_SIDE = 30_000         # pixels; guards against malformed pictures
MARGIN = 12               # pixels of white kept around trimmed pictures (at 600 dpi; scaled)


def paste_dpi(settings: dict) -> int:
    """The project's "Pasted picture resolution" (600 or 1200 dpi)."""
    try:
        dpi = int(settings.get("paste_dpi", DEFAULT_DPI))
    except (TypeError, ValueError):
        return DEFAULT_DPI
    return dpi if dpi in PASTE_DPIS else DEFAULT_DPI


@dataclass
class PastedPicture:
    """A picture taken from the clipboard: the PNG, plus ChemDraw's vector original
    (EMF or PDF bytes) when the picture was rendered from one."""
    png: bytes
    source: bytes | None = None
    source_ext: str = ""      # ".emf" or ".pdf"


# ---------------------------------------------------------------------- public

def clipboard_picture(dpi: int = DEFAULT_DPI) -> PastedPicture | None:
    """The best picture on the clipboard (with its vector original if it had one), or None."""
    for getter in (_windows_metafile, _pdf_picture):
        try:
            found = getter(dpi)
        except Exception:  # noqa: BLE001 — one failed format must not stop the others
            found = None
        if found is not None and not found[0].isNull():
            return PastedPicture(image_to_png(trim_white(found[0])), found[1], found[2])
    try:
        image = _qt_image()
    except Exception:  # noqa: BLE001
        image = None
    if image is not None and not image.isNull():
        return PastedPicture(image_to_png(trim_white(image)))
    return None


def clipboard_has_vector() -> bool:
    """Is there a vector picture (ChemDraw's EMF on Windows, a PDF on macOS) on the system
    clipboard? Qt does not report these as images, so text editors ask here before
    deciding that a paste has nothing in it."""
    try:
        if _windows_vector_available():
            return True
        data = QGuiApplication.clipboard().mimeData()
        return data is not None and any("pdf" in f.lower() for f in data.formats())
    except Exception:  # noqa: BLE001 — asking must never break a paste
        return False


def clipboard_image(dpi: int = DEFAULT_DPI) -> QImage | None:
    """The best picture on the clipboard as a QImage (no vector original), or None."""
    picture = clipboard_picture(dpi)
    return QImage.fromData(picture.png, "PNG") if picture is not None else None


def image_to_png(image: QImage) -> bytes:
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(array)


def clipboard_png(dpi: int = DEFAULT_DPI) -> bytes | None:
    picture = clipboard_picture(dpi)
    return picture.png if picture is not None else None


def render_vector(data: bytes, ext: str, dpi: int = DEFAULT_DPI) -> QImage | None:
    """Render a stored vector original (".emf" or ".pdf") to a trimmed picture, or None
    if it can't be read here (EMF pictures can only be rendered on Windows)."""
    ext = ext.lower()
    if ext == ".pdf":
        image = render_pdf(data, dpi)
    elif ext == ".emf":
        image = render_emf_bytes(data, dpi)
    else:
        return None
    return trim_white(image) if image is not None and not image.isNull() else None


def rerender_file(source: Path, dpi: int = DEFAULT_DPI) -> bytes | None:
    """PNG bytes of a stored vector original at `dpi`, or None if it can't be rendered."""
    try:
        data = Path(source).read_bytes()
    except OSError:
        return None
    image = render_vector(data, Path(source).suffix, dpi)
    return image_to_png(image) if image is not None else None


def rerender_pasted_pictures(project, dpi: int,
                             progress=None) -> tuple[int, int, int]:
    """Draw every pasted picture that has a saved vector original again at `dpi`
    ("Re-render pasted pictures" in Project settings). Returns (re-rendered, pictures
    without an original, originals that could not be drawn here).

    `progress(done_so_far, total)` is called before each picture; if it returns False
    the rest are left as they are (each picture is replaced whole, so stopping part-way
    leaves every picture correct, just some still at the old resolution)."""
    done = without = failed = 0
    pictures = project.pasted_pictures()
    for i, (picture, source) in enumerate(pictures):
        if progress is not None and progress(i, len(pictures)) is False:
            break
        if source is None:
            without += 1
            continue
        png = rerender_file(source, dpi)
        if png is None:
            failed += 1
            continue
        project.replace_picture_png(picture, png)
        done += 1
    return done, without, failed


def fit_dpi(width_in: float, height_in: float, dpi: float) -> tuple[float, int]:
    """(dpi actually used, supersampling factor) for a picture of this size in inches.

    Below 1200 dpi the picture is drawn at 2x and shrunk (smoother lines), unless that
    would be too big. If even the plain bitmap would exceed MAX_PIXELS (or MAX_SIDE),
    the dpi is lowered just enough; the true resolution is recorded in the PNG, so the
    picture still prints at its ChemDraw size."""
    supersample = SUPERSAMPLE if dpi < 1200 else 1
    if width_in * height_in * (dpi * supersample) ** 2 > MAX_PIXELS:
        supersample = 1
    pixels = width_in * height_in * dpi ** 2
    if pixels > MAX_PIXELS:
        dpi *= math.sqrt(MAX_PIXELS / pixels)
    longest = max(width_in, height_in)
    if longest * dpi * supersample > MAX_SIDE:
        dpi = MAX_SIDE / (longest * supersample)
    return dpi, supersample


def trim_white(image: QImage, margin: int | None = None) -> QImage:
    """Crop away white (or transparent) borders, keeping a small margin."""
    if margin is None:  # the same physical margin at any resolution
        dpi = image.dotsPerMeterX() * 0.0254
        margin = max(MARGIN, round(MARGIN * dpi / 600)) if dpi > 100 else MARGIN
    rgb = image.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = rgb.width(), rgb.height()
    if w == 0 or h == 0:
        return image
    arr = np.frombuffer(rgb.constBits(), np.uint8, count=rgb.sizeInBytes())
    arr = arr.reshape(h, rgb.bytesPerLine())[:, : w * 4].reshape(h, w, 4)
    # Content = not near-white and not transparent.
    ink = (arr[:, :, :3] < 240).any(axis=2) & (arr[:, :, 3] > 16)
    rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
    del ink, arr
    if rows.size == 0:
        return image
    top, bottom = max(rows[0] - margin, 0), min(rows[-1] + margin, h - 1)
    left, right = max(cols[0] - margin, 0), min(cols[-1] + margin, w - 1)
    cropped = rgb.copy(int(left), int(top), int(right - left + 1), int(bottom - top + 1))
    del rgb
    # Flatten onto white so transparent pictures don't turn black in the PDF.
    flat = QImage(cropped.size(), QImage.Format.Format_RGB32)
    flat.setDotsPerMeterX(image.dotsPerMeterX())  # keep the resolution (print size)
    flat.setDotsPerMeterY(image.dotsPerMeterY())
    flat.fill(QColor("white"))
    painter = QPainter(flat)
    painter.drawImage(0, 0, cropped)
    painter.end()
    return flat


# ---------------------------------------------------------------------- ordinary images

def _qt_image() -> QImage | None:
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return None
    if data.hasImage():
        image = QGuiApplication.clipboard().image()
        if not image.isNull():
            return image
    for fmt in data.formats():  # e.g. a registered "PNG" format Qt did not map
        if "png" in fmt.lower() or "jpeg" in fmt.lower() or "jpg" in fmt.lower():
            image = QImage.fromData(data.data(fmt))
            if not image.isNull():
                return image
    return None


# ---------------------------------------------------------------------- macOS: PDF

def _pdf_picture(dpi: int) -> tuple[QImage, bytes, str] | None:
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return None
    fmt = next((f for f in data.formats() if "pdf" in f.lower()), None)
    if fmt is None:
        return None
    raw = bytes(data.data(fmt))
    image = render_pdf(raw, dpi)
    return (image, raw, ".pdf") if image is not None else None


def render_pdf(raw: bytes, dpi: float = DEFAULT_DPI) -> QImage | None:
    """Draw the first page of a PDF at `dpi` (lowered if the bitmap would be too big)."""
    from PySide6.QtPdf import QPdfDocument
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument()
    doc.load(buffer)
    try:
        if doc.pageCount() < 1:
            return None
        points = doc.pagePointSize(0)  # 1 point = 1/72 inch
        w_in, h_in = points.width() / 72, points.height() / 72
        if w_in <= 0 or h_in <= 0:
            return None
        dpi, _ = fit_dpi(w_in, h_in, dpi)
        size = QSize(max(1, round(w_in * dpi)), max(1, round(h_in * dpi)))
        image = doc.render(0, size)
    finally:
        doc.close()
    return _at_dpi(image, dpi) if not image.isNull() else None


def _at_dpi(image: QImage, dpi: float = DEFAULT_DPI) -> QImage:
    """Record the resolution so the picture prints at its real (ChemDraw) size."""
    dpm = round(dpi / 0.0254)
    image.setDotsPerMeterX(dpm)
    image.setDotsPerMeterY(dpm)
    return image


# ---------------------------------------------------------------------- Windows: EMF

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _HANDLE = ctypes.c_void_p

    class _RECTL(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _SIZEL(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _ENHMETAHEADER(ctypes.Structure):
        _fields_ = [("iType", wintypes.DWORD), ("nSize", wintypes.DWORD),
                    ("rclBounds", _RECTL), ("rclFrame", _RECTL),
                    ("dSignature", wintypes.DWORD), ("nVersion", wintypes.DWORD),
                    ("nBytes", wintypes.DWORD), ("nRecords", wintypes.DWORD),
                    ("nHandles", wintypes.WORD), ("sReserved", wintypes.WORD),
                    ("nDescription", wintypes.DWORD), ("offDescription", wintypes.DWORD),
                    ("nPalEntries", wintypes.DWORD), ("szlDevice", _SIZEL),
                    ("szlMillimeters", _SIZEL), ("cbPixelFormat", wintypes.DWORD),
                    ("offPixelFormat", wintypes.DWORD), ("bOpenGL", wintypes.DWORD),
                    ("szlMicrometers", _SIZEL)]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    def _sig(fn, restype, *argtypes):
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    _OpenClipboard = _sig(_user32.OpenClipboard, wintypes.BOOL, wintypes.HWND)
    _CloseClipboard = _sig(_user32.CloseClipboard, wintypes.BOOL)
    _IsClipboardFormatAvailable = _sig(_user32.IsClipboardFormatAvailable, wintypes.BOOL,
                                       wintypes.UINT)
    _GetClipboardData = _sig(_user32.GetClipboardData, _HANDLE, wintypes.UINT)
    _GetDC = _sig(_user32.GetDC, _HANDLE, wintypes.HWND)
    _ReleaseDC = _sig(_user32.ReleaseDC, ctypes.c_int, wintypes.HWND, _HANDLE)
    _CopyEnhMetaFileW = _sig(_gdi32.CopyEnhMetaFileW, _HANDLE, _HANDLE, wintypes.LPCWSTR)
    _DeleteEnhMetaFile = _sig(_gdi32.DeleteEnhMetaFile, wintypes.BOOL, _HANDLE)
    _GetEnhMetaFileBits = _sig(_gdi32.GetEnhMetaFileBits, wintypes.UINT, _HANDLE,
                               wintypes.UINT, ctypes.c_void_p)
    _SetEnhMetaFileBits = _sig(_gdi32.SetEnhMetaFileBits, _HANDLE, wintypes.UINT,
                               ctypes.c_char_p)
    _GetEnhMetaFileHeader = _sig(_gdi32.GetEnhMetaFileHeader, wintypes.UINT, _HANDLE,
                                 wintypes.UINT, ctypes.POINTER(_ENHMETAHEADER))
    _CreateCompatibleDC = _sig(_gdi32.CreateCompatibleDC, _HANDLE, _HANDLE)
    _DeleteDC = _sig(_gdi32.DeleteDC, wintypes.BOOL, _HANDLE)
    _CreateDIBSection = _sig(_gdi32.CreateDIBSection, _HANDLE, _HANDLE,
                             ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
                             ctypes.POINTER(ctypes.c_void_p), _HANDLE, wintypes.DWORD)
    _SelectObject = _sig(_gdi32.SelectObject, _HANDLE, _HANDLE, _HANDLE)
    _DeleteObject = _sig(_gdi32.DeleteObject, wintypes.BOOL, _HANDLE)
    _PatBlt = _sig(_gdi32.PatBlt, wintypes.BOOL, _HANDLE, ctypes.c_int, ctypes.c_int,
                   ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    _PlayEnhMetaFile = _sig(_gdi32.PlayEnhMetaFile, wintypes.BOOL, _HANDLE, _HANDLE,
                            ctypes.POINTER(_RECT))

    _CF_ENHMETAFILE = 14
    _WHITENESS = 0x00FF0062

    def render_emf(hemf: int, dpi: float = DEFAULT_DPI) -> QImage | None:
        """Draw an EMF handle onto a white bitmap at `dpi` (anti-aliased below 1200 dpi).
        The dpi is lowered for very large pictures (see fit_dpi); the PNG records it."""
        header = _ENHMETAHEADER()
        if not _GetEnhMetaFileHeader(hemf, ctypes.sizeof(header), ctypes.byref(header)):
            return None
        frame = header.rclFrame  # in 0.01 mm
        width_mm = (frame.right - frame.left) / 100
        height_mm = (frame.bottom - frame.top) / 100
        if width_mm <= 0 or height_mm <= 0:
            return None
        dpi, supersample = fit_dpi(width_mm / 25.4, height_mm / 25.4, dpi)
        # Whole pixels at the final size, so the recorded dpi matches the bitmap exactly.
        out_w = max(1, round(width_mm / 25.4 * dpi))
        out_h = max(1, round(height_mm / 25.4 * dpi))
        w, h = out_w * supersample, out_h * supersample

        screen = _GetDC(None)
        dc = _CreateCompatibleDC(screen)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        bitmap = _CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap or not bits.value:
                return None
            old = _SelectObject(dc, bitmap)
            _PatBlt(dc, 0, 0, w, h, _WHITENESS)
            rect = _RECT(0, 0, w, h)
            _PlayEnhMetaFile(dc, hemf, ctypes.byref(rect))
            _SelectObject(dc, old)
            # Read the bitmap in place (no extra copy): only one copy is made, by Qt.
            raw = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
            image = QImage(raw, w, h, w * 4, QImage.Format.Format_RGB32).copy()
            del raw
        finally:
            if bitmap:
                _DeleteObject(bitmap)
            _DeleteDC(dc)
            _ReleaseDC(None, screen)
        if supersample > 1:
            image = image.scaled(out_w, out_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        return _at_dpi(image, dpi)

    def emf_bytes(hemf: int) -> bytes | None:
        """The EMF file contents of a metafile handle (what is saved as picture.emf)."""
        size = _GetEnhMetaFileBits(hemf, 0, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if _GetEnhMetaFileBits(hemf, size, buffer) != size:
            return None
        return buffer.raw

    def render_emf_bytes(data: bytes, dpi: float = DEFAULT_DPI) -> QImage | None:
        hemf = _SetEnhMetaFileBits(len(data), data)
        if not hemf:
            return None
        try:
            return render_emf(hemf, dpi)
        finally:
            _DeleteEnhMetaFile(hemf)

    # GDI+ draws EMF+ records, which plain GDI skips. Only needed for a metafile that is
    # EMF+ alone (no GDI records to fall back on), see ui/spectrum_paste.py.
    class _GdiplusStartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", wintypes.BOOL),
                    ("SuppressExternalCodecs", wintypes.BOOL)]

    def render_emf_plus_bytes(data: bytes, dpi: float = DEFAULT_DPI) -> QImage | None:
        """Draw a metafile with GDI+ (EMF+ records too), on white at `dpi`; None if it
        can't be done."""
        try:
            gdiplus = ctypes.WinDLL("gdiplus")
        except OSError:
            return None
        hemf = _SetEnhMetaFileBits(len(data), data)
        if not hemf:
            return None
        header = _ENHMETAHEADER()
        token = ctypes.c_size_t()
        started = False
        dc = bitmap = metafile = graphics = None
        screen = _GetDC(None)
        try:
            if not _GetEnhMetaFileHeader(hemf, ctypes.sizeof(header), ctypes.byref(header)):
                return None
            frame = header.rclFrame
            width_mm = (frame.right - frame.left) / 100
            height_mm = (frame.bottom - frame.top) / 100
            if width_mm <= 0 or height_mm <= 0:
                return None
            dpi, _supersample = fit_dpi(width_mm / 25.4, height_mm / 25.4, dpi)
            w = max(1, round(width_mm / 25.4 * dpi))
            h = max(1, round(height_mm / 25.4 * dpi))
            start = _GdiplusStartupInput(1, None, False, False)
            if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(start), None) != 0:
                return None
            started = True
            dc = _CreateCompatibleDC(screen)
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = w
            info.bmiHeader.biHeight = -h
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            bitmap = _CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                return None
            old = _SelectObject(dc, bitmap)
            _PatBlt(dc, 0, 0, w, h, _WHITENESS)
            metafile = ctypes.c_void_p()
            gdiplus.GdipCreateMetafileFromEmf.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                                          ctypes.POINTER(ctypes.c_void_p)]
            gdiplus.GdipCreateFromHDC.argtypes = [ctypes.c_void_p,
                                                  ctypes.POINTER(ctypes.c_void_p)]
            gdiplus.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + \
                [ctypes.c_int] * 4
            gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
            gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]
            if gdiplus.GdipCreateMetafileFromEmf(hemf, False, ctypes.byref(metafile)) != 0:
                return None
            graphics = ctypes.c_void_p()
            if gdiplus.GdipCreateFromHDC(dc, ctypes.byref(graphics)) != 0:
                return None
            gdiplus.GdipDrawImageRectI(graphics, metafile, 0, 0, w, h)
            gdiplus.GdipDeleteGraphics(graphics)
            graphics = None
            _SelectObject(dc, old)
            raw = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
            image = QImage(raw, w, h, w * 4, QImage.Format.Format_RGB32).copy()
            del raw
            return _at_dpi(image, dpi)
        except (OSError, AttributeError):
            return None
        finally:
            if graphics:
                gdiplus.GdipDeleteGraphics(graphics)
            if metafile is not None and metafile.value:
                gdiplus.GdipDisposeImage(metafile)   # (made with deleteEmf=False: hemf is ours)
            _DeleteEnhMetaFile(hemf)
            if bitmap:
                _DeleteObject(bitmap)
            if dc:
                _DeleteDC(dc)
            _ReleaseDC(None, screen)
            if started:
                gdiplus.GdiplusShutdown(token)

    def clipboard_metafile_available() -> bool:
        """Is there an Enhanced Metafile (e.g. Mnova's or ChemDraw's copy) on the
        clipboard? (Never in tests: the "offscreen" platform has its own clipboard.)"""
        return _windows_vector_available()

    def clipboard_metafile_bytes() -> bytes | None:
        """The clipboard's Enhanced Metafile as EMF file contents, or None."""
        if not _windows_vector_available() or not _OpenClipboard(None):
            return None
        try:
            handle = _GetClipboardData(_CF_ENHMETAFILE)
            copy = _CopyEnhMetaFileW(handle, None) if handle else None
        finally:
            _CloseClipboard()
        if not copy:
            return None
        try:
            return emf_bytes(copy)
        finally:
            _DeleteEnhMetaFile(copy)

    def _windows_vector_available() -> bool:
        if QGuiApplication.platformName() == "offscreen":
            return False
        return bool(_IsClipboardFormatAvailable(_CF_ENHMETAFILE))

    def _windows_metafile(dpi: int) -> tuple[QImage, bytes | None, str] | None:
        if QGuiApplication.platformName() == "offscreen":
            return None  # tests / no real desktop: Qt's own clipboard is the only one
        if not _IsClipboardFormatAvailable(_CF_ENHMETAFILE):
            return None
        if not _OpenClipboard(None):
            return None
        try:
            handle = _GetClipboardData(_CF_ENHMETAFILE)
            # The clipboard owns `handle`; work on our own copy.
            copy = _CopyEnhMetaFileW(handle, None) if handle else None
        finally:
            _CloseClipboard()
        if not copy:
            return None
        try:
            image = render_emf(copy, dpi)
            source = emf_bytes(copy)
        finally:
            _DeleteEnhMetaFile(copy)
        if image is None:
            return None
        return image, source, ".emf" if source else ""

else:
    def _windows_vector_available() -> bool:
        return False

    def _windows_metafile(dpi: int) -> None:
        return None

    def render_emf_bytes(data: bytes, dpi: float = DEFAULT_DPI) -> None:
        return None  # Enhanced Metafiles can only be drawn by Windows

    def render_emf_plus_bytes(data: bytes, dpi: float = DEFAULT_DPI) -> None:
        return None

    def clipboard_metafile_available() -> bool:
        return False

    def clipboard_metafile_bytes() -> None:
        return None
