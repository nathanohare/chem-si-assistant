"""Enhanced Metafiles drawn with real Windows (GDI) calls, for the tests of pasting spectra
(SPEC §13l.1, core/emf_svg.py). Windows only: import it inside tests that are skipped on
other systems."""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes

gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")
msimg32 = ctypes.WinDLL("msimg32")
H = ctypes.c_void_p

# The program is DPI-aware (Qt's Windows platform makes it so). Tests run Qt "offscreen",
# which doesn't, and then Windows tells drawing code a scaled-down screen size while the
# metafile header records the real one: the samples would not match their own header.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor (v2)
except (AttributeError, OSError):
    pass


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class XFORM(ctypes.Structure):
    _fields_ = [(n, ctypes.c_float) for n in ("eM11", "eM12", "eM21", "eM22", "eDx", "eDy")]


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long),
                ("lfEscapement", ctypes.c_long), ("lfOrientation", ctypes.c_long),
                ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
                ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
                ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
                ("lfPitchAndFamily", ctypes.c_byte), ("lfFaceName", ctypes.c_wchar * 32)]


class LOGBRUSH(ctypes.Structure):
    _fields_ = [("lbStyle", wintypes.UINT), ("lbColor", wintypes.DWORD),
                ("lbHatch", ctypes.c_size_t)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 256)]


class TRIVERTEX(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long), ("Red", ctypes.c_ushort),
                ("Green", ctypes.c_ushort), ("Blue", ctypes.c_ushort),
                ("Alpha", ctypes.c_ushort)]


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


I = ctypes.c_int
CreateEnhMetaFileW = _sig(gdi32.CreateEnhMetaFileW, H, H, wintypes.LPCWSTR,
                          ctypes.POINTER(RECT), wintypes.LPCWSTR)
CloseEnhMetaFile = _sig(gdi32.CloseEnhMetaFile, H, H)
GetEnhMetaFileBits = _sig(gdi32.GetEnhMetaFileBits, wintypes.UINT, H, wintypes.UINT,
                          ctypes.c_void_p)
DeleteEnhMetaFile = _sig(gdi32.DeleteEnhMetaFile, wintypes.BOOL, H)
GetDeviceCaps = _sig(gdi32.GetDeviceCaps, I, H, I)
SetMapMode = _sig(gdi32.SetMapMode, I, H, I)
SetWindowExtEx = _sig(gdi32.SetWindowExtEx, wintypes.BOOL, H, I, I, ctypes.c_void_p)
SetViewportExtEx = _sig(gdi32.SetViewportExtEx, wintypes.BOOL, H, I, I, ctypes.c_void_p)
SetWindowOrgEx = _sig(gdi32.SetWindowOrgEx, wintypes.BOOL, H, I, I, ctypes.c_void_p)
SetGraphicsMode = _sig(gdi32.SetGraphicsMode, I, H, I)
SetWorldTransform = _sig(gdi32.SetWorldTransform, wintypes.BOOL, H, ctypes.POINTER(XFORM))
SaveDC = _sig(gdi32.SaveDC, I, H)
RestoreDC = _sig(gdi32.RestoreDC, wintypes.BOOL, H, I)
IntersectClipRect = _sig(gdi32.IntersectClipRect, I, H, I, I, I, I)
ExcludeClipRect = _sig(gdi32.ExcludeClipRect, I, H, I, I, I, I)
CreatePen = _sig(gdi32.CreatePen, H, I, I, wintypes.DWORD)
ExtCreatePen = _sig(gdi32.ExtCreatePen, H, wintypes.DWORD, wintypes.DWORD,
                    ctypes.POINTER(LOGBRUSH), wintypes.DWORD, ctypes.c_void_p)
CreateSolidBrush = _sig(gdi32.CreateSolidBrush, H, wintypes.DWORD)
CreateHatchBrush = _sig(gdi32.CreateHatchBrush, H, I, wintypes.DWORD)
GetStockObject = _sig(gdi32.GetStockObject, H, I)
SelectObject = _sig(gdi32.SelectObject, H, H, H)
DeleteObject = _sig(gdi32.DeleteObject, wintypes.BOOL, H)
MoveToEx = _sig(gdi32.MoveToEx, wintypes.BOOL, H, I, I, ctypes.c_void_p)
LineTo = _sig(gdi32.LineTo, wintypes.BOOL, H, I, I)
Polyline = _sig(gdi32.Polyline, wintypes.BOOL, H, ctypes.POINTER(POINT), I)
PolyBezier = _sig(gdi32.PolyBezier, wintypes.BOOL, H, ctypes.POINTER(POINT), wintypes.DWORD)
Polygon = _sig(gdi32.Polygon, wintypes.BOOL, H, ctypes.POINTER(POINT), I)
PolyPolygon = _sig(gdi32.PolyPolygon, wintypes.BOOL, H, ctypes.POINTER(POINT),
                   ctypes.POINTER(I), I)
Rectangle = _sig(gdi32.Rectangle, wintypes.BOOL, H, I, I, I, I)
Ellipse = _sig(gdi32.Ellipse, wintypes.BOOL, H, I, I, I, I)
RoundRect = _sig(gdi32.RoundRect, wintypes.BOOL, H, I, I, I, I, I, I)
Arc = _sig(gdi32.Arc, wintypes.BOOL, H, I, I, I, I, I, I, I, I)
Pie = _sig(gdi32.Pie, wintypes.BOOL, H, I, I, I, I, I, I, I, I)
CreateFontIndirectW = _sig(gdi32.CreateFontIndirectW, H, ctypes.POINTER(LOGFONTW))
SetTextColor = _sig(gdi32.SetTextColor, wintypes.DWORD, H, wintypes.DWORD)
SetBkColor = _sig(gdi32.SetBkColor, wintypes.DWORD, H, wintypes.DWORD)
SetBkMode = _sig(gdi32.SetBkMode, I, H, I)
SetTextAlign = _sig(gdi32.SetTextAlign, wintypes.UINT, H, wintypes.UINT)
TextOutW = _sig(gdi32.TextOutW, wintypes.BOOL, H, I, I, wintypes.LPCWSTR, I)
ExtTextOutW = _sig(gdi32.ExtTextOutW, wintypes.BOOL, H, I, I, wintypes.UINT,
                   ctypes.POINTER(RECT), wintypes.LPCWSTR, wintypes.UINT, ctypes.POINTER(I))
StretchDIBits = _sig(gdi32.StretchDIBits, I, H, I, I, I, I, I, I, I, I, ctypes.c_void_p,
                     ctypes.POINTER(BITMAPINFO), wintypes.UINT, wintypes.DWORD)
BeginPath = _sig(gdi32.BeginPath, wintypes.BOOL, H)
EndPath = _sig(gdi32.EndPath, wintypes.BOOL, H)
StrokePath = _sig(gdi32.StrokePath, wintypes.BOOL, H)
FillPath = _sig(gdi32.FillPath, wintypes.BOOL, H)
CloseFigure = _sig(gdi32.CloseFigure, wintypes.BOOL, H)
SelectClipPath = _sig(gdi32.SelectClipPath, wintypes.BOOL, H, I)
SetPolyFillMode = _sig(gdi32.SetPolyFillMode, I, H, I)
SetROP2 = _sig(gdi32.SetROP2, I, H, I)
ExtFloodFill = _sig(gdi32.ExtFloodFill, wintypes.BOOL, H, I, I, wintypes.DWORD, wintypes.UINT)
GradientFill = _sig(msimg32.GradientFill, wintypes.BOOL, H, ctypes.POINTER(TRIVERTEX),
                    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG)

user32.GetDC.restype = H
user32.GetDC.argtypes = [H]
user32.ReleaseDC.argtypes = [H, H]

MM_ANISOTROPIC, MM_HIMETRIC = 8, 3
GM_ADVANCED = 2
TRANSPARENT = 1
TA_LEFT, TA_RIGHT, TA_CENTER, TA_TOP, TA_BOTTOM, TA_BASELINE = 0, 2, 6, 0, 8, 24
WHITE_BRUSH, NULL_BRUSH, NULL_PEN, BLACK_PEN = 0, 5, 8, 7
PS_DASH, PS_GEOMETRIC, PS_ENDCAP_FLAT, PS_JOIN_MITER = 1, 0x10000, 0x200, 0x2000


def rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


def points(seq) -> ctypes.Array:
    arr = (POINT * len(seq))()
    for i, (x, y) in enumerate(seq):
        arr[i].x, arr[i].y = int(round(x)), int(round(y))
    return arr


def font(height: int, face: str = "Arial", weight: int = 400, italic: bool = False,
         escapement: int = 0) -> H:
    lf = LOGFONTW()
    lf.lfHeight = height
    lf.lfEscapement = lf.lfOrientation = escapement
    lf.lfWeight = weight
    lf.lfItalic = 1 if italic else 0
    lf.lfQuality = 5   # CLEARTYPE (as Mnova on a normal desktop)
    lf.lfFaceName = face
    return CreateFontIndirectW(ctypes.byref(lf))


class Metafile:
    """A metafile being drawn: `with Metafile(180, 120) as m: …`; then m.data holds the
    EMF bytes. Drawing is in 0.01 mm (MM_ANISOTROPIC, y down) unless `units` is "himetric"
    (y up) or "pixels" (MM_TEXT)."""

    def __init__(self, width_mm: float, height_mm: float, units: str = "0.01mm"):
        self.width_mm, self.height_mm = width_mm, height_mm
        self.units = units
        self.data = b""
        self._objects: list = []

    def __enter__(self) -> "Metafile":
        frame = RECT(0, 0, round(self.width_mm * 100), round(self.height_mm * 100))
        self.dc = CreateEnhMetaFileW(None, None, ctypes.byref(frame), "Test\0Spectrum\0\0")
        dc = self.dc
        self.px_per_mm = (GetDeviceCaps(dc, 8) / GetDeviceCaps(dc, 4),
                          GetDeviceCaps(dc, 10) / GetDeviceCaps(dc, 6))
        if self.units == "0.01mm":
            SetMapMode(dc, MM_ANISOTROPIC)
            SetWindowExtEx(dc, round(self.width_mm * 100), round(self.height_mm * 100), None)
            SetViewportExtEx(dc, round(self.width_mm * self.px_per_mm[0]),
                             round(self.height_mm * self.px_per_mm[1]), None)
        elif self.units == "himetric":
            SetMapMode(dc, MM_HIMETRIC)
        return self

    def keep(self, handle):
        self._objects.append(handle)
        return handle

    def select(self, handle):
        return SelectObject(self.dc, handle)

    def __exit__(self, *exc) -> None:
        hemf = CloseEnhMetaFile(self.dc)
        size = GetEnhMetaFileBits(hemf, 0, None)
        buf = ctypes.create_string_buffer(size)
        GetEnhMetaFileBits(hemf, size, buf)
        self.data = buf.raw
        DeleteEnhMetaFile(hemf)
        for h in self._objects:
            DeleteObject(h)


def text(dc, x: int, y: int, s: str, options: int = 0) -> None:
    ExtTextOutW(dc, x, y, options, None, s, len(s), None)


def gradient_dib(width: int, height: int):
    """A 24-bit bottom-up DIB with a colour gradient (and its BITMAPINFO)."""
    info = BITMAPINFO()
    h = info.bmiHeader
    h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    h.biWidth, h.biHeight, h.biPlanes, h.biBitCount = width, height, 1, 24
    stride = (width * 3 + 3) // 4 * 4
    bits = bytearray(stride * height)
    for row in range(height):        # (row 0 is the bottom one)
        for col in range(width):
            i = row * stride + col * 3
            bits[i:i + 3] = bytes((col * 255 // max(1, width - 1), 80,
                                   row * 255 // max(1, height - 1)))   # B, G, R
    return info, (ctypes.c_ubyte * len(bits)).from_buffer(bits), bits


def spectrum_trace(n: int, x0: float, x1: float, base: float, height: float):
    """n points of an NMR-like trace from x0 to x1 (peaks going up from `base`)."""
    peaks = [(0.12, 1.0, 0.002), (0.18, 0.35, 0.003), (0.4, 0.6, 0.002), (0.42, 0.55, 0.002),
             (0.7, 0.8, 0.004), (0.86, 0.25, 0.006)]
    out = []
    for i in range(n):
        t = i / (n - 1)
        v = sum(a * w * w / ((t - c) ** 2 + w * w) for c, a, w in peaks)
        v += 0.004 * math.sin(i * 0.37) + 0.003 * math.sin(i * 1.91)
        out.append((x0 + (x1 - x0) * t, base - height * v))
    return out


def spectrum_emf(n_points: int = 65536, n_labels: int = 300, bitmap: bool = True,
                 world: bool = True) -> bytes:
    """A metafile like a spectrum copied from Mnova as vectors (180 × 120 mm): a white
    background, axes with ticks and numbers, a long trace (a polyline clipped to the plot),
    integral curves (Béziers), a filled peak, many small upright peak labels and a
    rotated axis title, a little picture, and a part drawn through a world transform."""
    with Metafile(180, 120) as m:
        dc = m.dc
        m.select(GetStockObject(WHITE_BRUSH))
        m.select(GetStockObject(NULL_PEN))
        Rectangle(dc, 0, 0, 18000, 12000)
        SetBkMode(dc, TRANSPARENT)
        black = m.keep(CreatePen(0, 20, 0))
        m.select(black)
        MoveToEx(dc, 1500, 10500, None)
        LineTo(dc, 17000, 10500)
        small = m.keep(font(-280))
        m.select(small)
        SetTextAlign(dc, TA_CENTER | TA_TOP)
        for i in range(11):
            x = 17000 - i * 1550
            MoveToEx(dc, x, 10500, None)
            LineTo(dc, x, 10700)
            text(dc, x, 10750, f"{i:.1f}")
        SetTextAlign(dc, TA_CENTER | TA_TOP)
        title = m.keep(font(-340, weight=700))
        m.select(title)
        text(dc, 9250, 11300, "f1 (ppm)")
        # The axis title on the left, turned 90°.
        upright = m.keep(font(-320, escapement=900))
        m.select(upright)
        SetTextAlign(dc, TA_CENTER | TA_BASELINE)
        text(dc, 700, 6000, "Normalized Intensity")
        # The trace, clipped to the plot area.
        SaveDC(dc)
        IntersectClipRect(dc, 1500, 800, 17000, 10450)
        trace = m.keep(CreatePen(0, 0, rgb(0, 0, 160)))   # cosmetic (one pixel)
        m.select(trace)
        pts = spectrum_trace(n_points, 1500, 17000, 10300, 9000)
        Polyline(dc, points(pts), len(pts))
        RestoreDC(dc, -1)
        # Integral curves.
        green = m.keep(CreatePen(0, 15, rgb(0, 140, 0)))
        m.select(green)
        for k in range(4):
            x = 2500 + k * 3500
            curve = [(x, 9800), (x + 300, 9800), (x + 600, 8200), (x + 1000, 8000)]
            PolyBezier(dc, points(curve), 4)
        # A filled peak area.
        fill = m.keep(CreateSolidBrush(rgb(255, 200, 200)))
        edge = m.keep(CreatePen(0, 10, rgb(180, 0, 0)))
        m.select(fill)
        m.select(edge)
        Polygon(dc, points([(12200, 10300), (12400, 7000), (12600, 10300)]), 3)
        # Peak labels: small upright numbers, as Mnova prints picked peaks.
        labels = m.keep(font(-200, escapement=900))
        m.select(labels)
        SetTextAlign(dc, TA_LEFT | TA_BASELINE)
        SetTextColor(dc, rgb(60, 60, 60))
        for i in range(n_labels):
            x = 1600 + (i * 15300) // max(1, n_labels)
            text(dc, x + 60, 1300, f"{7.9 - i * 0.021:.2f}")
        SetTextColor(dc, 0)
        if bitmap:
            info, bits, _keep = gradient_dib(48, 32)
            StretchDIBits(dc, 15500, 900, 1200, 800, 0, 0, 48, 32, ctypes.addressof(bits),
                          ctypes.byref(info), 0, 0x00CC0020)
        if world:
            SetGraphicsMode(dc, GM_ADVANCED)
            SaveDC(dc)
            angle = math.radians(20)
            xf = XFORM(math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle),
                       5000, 2000)
            SetWorldTransform(dc, ctypes.byref(xf))
            box = m.keep(CreateSolidBrush(rgb(220, 230, 255)))
            m.select(box)
            m.select(black)
            Rectangle(dc, 0, 0, 2500, 900)
            m.select(small)
            SetTextAlign(dc, TA_LEFT | TA_BASELINE)
            text(dc, 150, 600, "CDCl3, 400 MHz")
            RestoreDC(dc, -1)
    return m.data


def lines_emf() -> bytes:
    """A drawing without text (so it needs no fonts): a frame, a trace and a filled peak."""
    with Metafile(80, 50) as m:
        dc = m.dc
        pen = m.keep(CreatePen(0, 15, 0))
        m.select(pen)
        m.select(GetStockObject(NULL_BRUSH))
        Rectangle(dc, 500, 500, 7500, 4500)
        trace = m.keep(CreatePen(0, 0, rgb(0, 0, 160)))
        m.select(trace)
        pts = spectrum_trace(3000, 600, 7400, 4300, 3500)
        Polyline(dc, points(pts), len(pts))
        fill = m.keep(CreateSolidBrush(rgb(255, 180, 180)))
        m.select(fill)
        m.select(pen)
        Polygon(dc, points([(3000, 4300), (3300, 2000), (3600, 4300)]), 3)
    return m.data


def bitmap_emf(width_px: int = 640, height_px: int = 480, width_mm: float = 160,
               height_mm: float = 120) -> bytes:
    """Mnova's default copy: a "metafile containing a bitmap" filling the frame."""
    with Metafile(width_mm, height_mm) as m:
        info, bits, _keep = gradient_dib(width_px, height_px)
        StretchDIBits(m.dc, 0, 0, round(width_mm * 100), round(height_mm * 100), 0, 0,
                      width_px, height_px, ctypes.addressof(bits), ctypes.byref(info), 0,
                      0x00CC0020)
    return m.data


def shapes_emf() -> bytes:
    """Other drawing commands: ellipses, rounded boxes, a pie, an arc, dashed and wide
    lines, a hatched fill, holes (PolyPolygon), a stroked path, a path used as clip,
    an excluded rectangle and text of several kinds."""
    with Metafile(120, 80) as m:
        dc = m.dc
        SetBkMode(dc, TRANSPARENT)
        pen = m.keep(CreatePen(0, 30, rgb(0, 0, 0)))
        m.select(pen)
        brush = m.keep(CreateSolidBrush(rgb(255, 230, 120)))
        m.select(brush)
        Ellipse(dc, 500, 500, 3500, 2500)
        RoundRect(dc, 4000, 500, 7500, 2500, 800, 800)
        Pie(dc, 8000, 500, 11000, 3500, 11000, 2000, 9500, 500)
        m.select(GetStockObject(NULL_BRUSH))
        Arc(dc, 500, 3000, 3500, 6000, 3500, 4500, 500, 4500)
        lb = LOGBRUSH(0, rgb(200, 0, 0), 0)
        dashed = m.keep(ExtCreatePen(PS_GEOMETRIC | PS_DASH | PS_ENDCAP_FLAT | PS_JOIN_MITER,
                                     40, ctypes.byref(lb), 0, None))
        m.select(dashed)
        Polyline(dc, points([(4000, 3200), (7500, 3200), (7500, 5800)]), 3)
        thin_dash = m.keep(CreatePen(PS_DASH, 0, rgb(0, 0, 200)))
        m.select(thin_dash)
        MoveToEx(dc, 4000, 4000, None)
        LineTo(dc, 7000, 5500)
        hatch = m.keep(CreateHatchBrush(5, rgb(0, 120, 0)))   # HS_DIAGCROSS
        m.select(hatch)
        m.select(pen)
        Rectangle(dc, 8000, 4000, 11500, 5800)
        # A square with a square hole.
        solid = m.keep(CreateSolidBrush(rgb(80, 80, 200)))
        m.select(solid)
        ring = points([(500, 6300), (2500, 6300), (2500, 7800), (500, 7800),
                       (1000, 6700), (2000, 6700), (2000, 7400), (1000, 7400)])
        counts = (I * 2)(4, 4)
        PolyPolygon(dc, ring, counts, 2)
        # A stroked path with a closed figure.
        BeginPath(dc)
        MoveToEx(dc, 3000, 6300, None)
        LineTo(dc, 4500, 7800)
        LineTo(dc, 6000, 6300)
        CloseFigure(dc)
        EndPath(dc)
        StrokePath(dc)
        # A path used as the clip: only the part of the box inside the triangle shows.
        SaveDC(dc)
        BeginPath(dc)
        MoveToEx(dc, 6500, 7900, None)
        LineTo(dc, 8000, 6200)
        LineTo(dc, 9500, 7900)
        CloseFigure(dc)
        EndPath(dc)
        SelectClipPath(dc, 5)   # RGN_COPY
        red = m.keep(CreateSolidBrush(rgb(220, 0, 0)))
        m.select(red)
        m.select(GetStockObject(NULL_PEN))
        Rectangle(dc, 6000, 6000, 10000, 8000)
        RestoreDC(dc, -1)
        # A box with a rectangle cut out of it (ExcludeClipRect).
        SaveDC(dc)
        ExcludeClipRect(dc, 10300, 6500, 11200, 7400)
        m.select(solid)
        m.select(GetStockObject(NULL_PEN))
        Rectangle(dc, 9900, 6100, 11700, 7800)
        RestoreDC(dc, -1)
        f = m.keep(font(-400, face="Times New Roman", italic=True))
        m.select(f)
        SetTextAlign(dc, TA_RIGHT | TA_BOTTOM)
        SetTextColor(dc, rgb(128, 0, 128))
        text(dc, 11800, 3900, "Right & bottom <δ>")
    return m.data


def himetric_emf() -> bytes:
    """Drawn in MM_HIMETRIC (y pointing up), with text, a filled box and a polyline."""
    with Metafile(60, 40, units="himetric") as m:
        dc = m.dc
        m.select(GetStockObject(4))   # BLACK_BRUSH
        Rectangle(dc, 500, -500, 2500, -1500)
        pen = m.keep(CreatePen(0, 25, rgb(0, 100, 200)))
        m.select(pen)
        Polyline(dc, points([(3000, -500), (4000, -3500), (5500, -1000)]), 3)
        SetBkMode(dc, TRANSPARENT)
        f = m.keep(font(-350))
        m.select(f)
        SetTextAlign(dc, TA_LEFT | TA_BASELINE)
        text(dc, 500, -3200, "HIMETRIC 1.25")
    return m.data


class _RectF(ctypes.Structure):
    _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float), ("Width", ctypes.c_float),
                ("Height", ctypes.c_float)]


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


def emf_plus_only() -> bytes:
    """A metafile recorded by GDI+ as "EMF+ only": plain GDI draws nothing of it (as a
    program that copies with GDI+ might produce)."""
    gp = ctypes.WinDLL("gdiplus")
    token = ctypes.c_size_t()
    gp.GdiplusStartup(ctypes.byref(token), ctypes.byref(_GdiplusStartupInput(1, None, 0, 0)),
                      None)
    screen = user32.GetDC(None)
    try:
        meta = ctypes.c_void_p()
        frame = _RectF(0, 0, 6000, 4000)
        gp.GdipRecordMetafile.argtypes = [H, I, ctypes.POINTER(_RectF), I, wintypes.LPCWSTR,
                                          ctypes.POINTER(ctypes.c_void_p)]
        assert gp.GdipRecordMetafile(screen, 4, ctypes.byref(frame), 7, None,
                                     ctypes.byref(meta)) == 0
        graphics = ctypes.c_void_p()
        gp.GdipGetImageGraphicsContext.argtypes = [H, ctypes.POINTER(ctypes.c_void_p)]
        gp.GdipGetImageGraphicsContext(meta, ctypes.byref(graphics))
        pen = ctypes.c_void_p()
        gp.GdipCreatePen1.argtypes = [wintypes.DWORD, ctypes.c_float, I,
                                      ctypes.POINTER(ctypes.c_void_p)]
        gp.GdipCreatePen1(0xFF000080, 3.0, 2, ctypes.byref(pen))
        gp.GdipDrawLineI.argtypes = [H, H, I, I, I, I]
        brush = ctypes.c_void_p()
        gp.GdipCreateSolidFill.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        gp.GdipCreateSolidFill(0xFFC00000, ctypes.byref(brush))
        gp.GdipFillRectangleI.argtypes = [H, H, I, I, I, I]
        gp.GdipFillRectangleI(graphics, brush, 20, 20, 80, 40)
        for k in range(10):
            gp.GdipDrawLineI(graphics, pen, 10, 10 + 8 * k, 200, 30 + 8 * k)
        gp.GdipDeletePen.argtypes = [H]
        gp.GdipDeletePen(pen)
        gp.GdipDeleteBrush.argtypes = [H]
        gp.GdipDeleteBrush(brush)
        gp.GdipDeleteGraphics.argtypes = [H]
        gp.GdipDeleteGraphics(graphics)
        hemf = ctypes.c_void_p()
        gp.GdipGetHemfFromMetafile.argtypes = [H, ctypes.POINTER(ctypes.c_void_p)]
        gp.GdipGetHemfFromMetafile(meta, ctypes.byref(hemf))
        gp.GdipDisposeImage.argtypes = [H]
        gp.GdipDisposeImage(meta)
        size = GetEnhMetaFileBits(hemf, 0, None)
        buf = ctypes.create_string_buffer(size)
        GetEnhMetaFileBits(hemf, size, buf)
        DeleteEnhMetaFile(hemf)
        return buf.raw
    finally:
        user32.ReleaseDC(None, screen)
        gp.GdiplusShutdown(token)


def unsupported_emf() -> bytes:
    """A drawing that uses commands the converter does not do (a gradient fill, a flood
    fill and XOR drawing), so the paste must fall back to a picture."""
    with Metafile(60, 40) as m:
        dc = m.dc
        vert = (TRIVERTEX * 2)(TRIVERTEX(500, 500, 0xFF00, 0, 0, 0),
                               TRIVERTEX(5500, 3500, 0, 0, 0xFF00, 0))
        rect = (wintypes.ULONG * 2)(0, 1)
        GradientFill(dc, vert, 2, ctypes.byref(rect), 1, 0)   # GRADIENT_FILL_RECT_H
        SetROP2(dc, 7)   # R2_XORPEN
        pen = m.keep(CreatePen(0, 50, rgb(255, 255, 255)))
        m.select(pen)
        MoveToEx(dc, 500, 2000, None)
        LineTo(dc, 5500, 2000)
    return m.data
