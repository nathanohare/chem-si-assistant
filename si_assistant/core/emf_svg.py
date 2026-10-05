"""Enhanced Metafile (EMF) → SVG, in pure Python (SPEC §13l.1).

Mnova (like ChemDraw) puts a copied spectrum on the Windows clipboard as an Enhanced
Metafile: a list of Windows drawing (GDI) records — lines, curves, filled shapes, text and
pictures. This module reads that list and writes the same drawing as an SVG file, so a
pasted spectrum stays a sharp vector drawing in the printed SI. It makes no Windows calls,
so it works (and is tested) on every system; ui/spectrum_paste.py checks every conversion
against Windows' own rendering of the same metafile before the SVG is used.

The SVG:

* its user unit is 0.01 mm (the unit of the metafile's frame), and its width and height
  are given in pt, so it prints at the size it was copied at;
* thin lines: Windows draws "cosmetic" pens (and any line thinner than a pixel) one device
  pixel wide, whatever the size; they are drawn HAIRLINE_PT wide here (0.25 pt);
* text stays text (searchable in the PDF): a <text> with the font named in the metafile
  plus common fall-backs, each character placed where Windows put it (an `x` list);
  text written as glyph numbers (ETO_GLYPH_INDEX) needs a TextHelper (ui/) to be turned
  back into characters or outlines;
* pictures inside the metafile (a "metafile containing a bitmap") are embedded as PNG;
* hatched fills are <pattern>s with the spacing of a standard 96-dpi screen (GDI's own
  spacing depends on the resolution it draws at, see HATCH_PIXEL);
* clipping uses <clipPath>.

Anything the converter cannot draw is counted in `Conversion.unsupported` instead of
stopping it, so the caller can fall back to a picture (a damaged or cut-off metafile gives
`Conversion.error`).

Only numpy (already used by the program) is needed besides the standard library.
"""

from __future__ import annotations

import base64
import math
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Protocol

import numpy as np

UNIT_MM = 0.01                         # one SVG user unit
UNITS_PER_PT = 25.4 / 72 / UNIT_MM     # 35.28 units = 1 pt
HAIRLINE_PT = 0.25                     # cosmetic pens and lines thinner than this
HAIRLINE = HAIRLINE_PT * UNITS_PER_PT
EMF_SIGNATURE = 0x464D4520             # " EMF"
# Guards against malformed data (a damaged or hostile metafile must never hang the paste or
# make a huge SVG; what goes past them counts as damaged or not converted, so the paste
# falls back to a picture):
MAX_RECORDS = 5_000_000                # records read
MAX_COORD = 1e9                        # |coordinate| in SVG units (10 km) — beyond: damaged
MAX_SAVED_STATES = 10_000              # SAVEDC depth
MAX_CLIP_SHAPES = 16                   # clip shapes intersected at one time (SVG nesting)

RECORD_NAMES = {
    1: "HEADER", 2: "POLYBEZIER", 3: "POLYGON", 4: "POLYLINE", 5: "POLYBEZIERTO",
    6: "POLYLINETO", 7: "POLYPOLYLINE", 8: "POLYPOLYGON", 9: "SETWINDOWEXTEX",
    10: "SETWINDOWORGEX", 11: "SETVIEWPORTEXTEX", 12: "SETVIEWPORTORGEX", 13: "SETBRUSHORGEX",
    14: "EOF", 15: "SETPIXELV", 16: "SETMAPPERFLAGS", 17: "SETMAPMODE", 18: "SETBKMODE",
    19: "SETPOLYFILLMODE", 20: "SETROP2", 21: "SETSTRETCHBLTMODE", 22: "SETTEXTALIGN",
    23: "SETCOLORADJUSTMENT", 24: "SETTEXTCOLOR", 25: "SETBKCOLOR", 26: "OFFSETCLIPRGN",
    27: "MOVETOEX", 28: "SETMETARGN", 29: "EXCLUDECLIPRECT", 30: "INTERSECTCLIPRECT",
    31: "SCALEVIEWPORTEXTEX", 32: "SCALEWINDOWEXTEX", 33: "SAVEDC", 34: "RESTOREDC",
    35: "SETWORLDTRANSFORM", 36: "MODIFYWORLDTRANSFORM", 37: "SELECTOBJECT", 38: "CREATEPEN",
    39: "CREATEBRUSHINDIRECT", 40: "DELETEOBJECT", 41: "ANGLEARC", 42: "ELLIPSE",
    43: "RECTANGLE", 44: "ROUNDRECT", 45: "ARC", 46: "CHORD", 47: "PIE", 48: "SELECTPALETTE",
    49: "CREATEPALETTE", 50: "SETPALETTEENTRIES", 51: "RESIZEPALETTE", 52: "REALIZEPALETTE",
    53: "EXTFLOODFILL", 54: "LINETO", 55: "ARCTO", 56: "POLYDRAW", 57: "SETARCDIRECTION",
    58: "SETMITERLIMIT", 59: "BEGINPATH", 60: "ENDPATH", 61: "CLOSEFIGURE", 62: "FILLPATH",
    63: "STROKEANDFILLPATH", 64: "STROKEPATH", 65: "FLATTENPATH", 66: "WIDENPATH",
    67: "SELECTCLIPPATH", 68: "ABORTPATH", 70: "GDICOMMENT", 71: "FILLRGN", 72: "FRAMERGN",
    73: "INVERTRGN", 74: "PAINTRGN", 75: "EXTSELECTCLIPRGN", 76: "BITBLT", 77: "STRETCHBLT",
    78: "MASKBLT", 79: "PLGBLT", 80: "SETDIBITSTODEVICE", 81: "STRETCHDIBITS",
    82: "EXTCREATEFONTINDIRECTW", 83: "EXTTEXTOUTA", 84: "EXTTEXTOUTW", 85: "POLYBEZIER16",
    86: "POLYGON16", 87: "POLYLINE16", 88: "POLYBEZIERTO16", 89: "POLYLINETO16",
    90: "POLYPOLYLINE16", 91: "POLYPOLYGON16", 92: "POLYDRAW16", 93: "CREATEMONOBRUSH",
    94: "CREATEDIBPATTERNBRUSHPT", 95: "EXTCREATEPEN", 96: "POLYTEXTOUTA", 97: "POLYTEXTOUTW",
    98: "SETICMMODE", 99: "CREATECOLORSPACE", 100: "SETCOLORSPACE", 101: "DELETECOLORSPACE",
    102: "GLSRECORD", 103: "GLSBOUNDEDRECORD", 104: "PIXELFORMAT", 105: "DRAWESCAPE",
    106: "EXTESCAPE", 108: "SMALLTEXTOUT", 109: "FORCEUFIMAPPING", 110: "NAMEDESCAPE",
    111: "COLORCORRECTPALETTE", 112: "SETICMPROFILEA", 113: "SETICMPROFILEW",
    114: "ALPHABLEND", 115: "SETLAYOUT", 116: "TRANSPARENTBLT", 118: "GRADIENTFILL",
    119: "SETLINKEDUFIS", 120: "SETTEXTJUSTIFICATION", 121: "COLORMATCHTOTARGETW",
    122: "CREATECOLORSPACEW",
}
# Records with nothing to draw and no effect on how the rest looks (colour management,
# palettes, printer escapes…): skipped on purpose.
IGNORED = {13, 16, 23, 48, 49, 50, 51, 52, 65, 98, 99, 100, 101, 102, 104, 105, 106, 109,
           110, 111, 112, 113, 119, 120, 121, 122}

# Mapping modes
MM_TEXT, MM_LOMETRIC, MM_HIMETRIC, MM_LOENGLISH, MM_HIENGLISH, MM_TWIPS, MM_ISOTROPIC, \
    MM_ANISOTROPIC = range(1, 9)
TRANSPARENT, OPAQUE = 1, 2
ALTERNATE, WINDING = 1, 2
TA_UPDATECP, TA_RIGHT, TA_CENTER, TA_BOTTOM, TA_BASELINE = 1, 2, 6, 8, 24
ETO_OPAQUE, ETO_CLIPPED, ETO_GLYPH_INDEX, ETO_PDY = 0x2, 0x4, 0x10, 0x2000
GM_ADVANCED = 2
AD_COUNTERCLOCKWISE, AD_CLOCKWISE = 1, 2
RGN_AND, RGN_OR, RGN_XOR, RGN_DIFF, RGN_COPY = 1, 2, 3, 4, 5
PS_SOLID, PS_DASH, PS_DOT, PS_DASHDOT, PS_DASHDOTDOT, PS_NULL, PS_INSIDEFRAME, \
    PS_USERSTYLE, PS_ALTERNATE = range(9)
PS_GEOMETRIC = 0x10000
BS_SOLID, BS_NULL, BS_HATCHED, BS_PATTERN = 0, 1, 2, 3
SRCCOPY, NOTSRCCOPY = 0x00CC0020, 0x00330008
PATCOPY, BLACKNESS, WHITENESS, DSTCOPY = 0x00F00021, 0x00000042, 0x00FF0062, 0x00AA0029
R2_BLACK, R2_NOTCOPYPEN, R2_NOP, R2_MASKPEN, R2_COPYPEN, R2_WHITE = 1, 4, 11, 9, 13, 16
SYMBOL_CHARSET = 2
STOCK_BIT = 0x80000000

# GDI's dash patterns (dash, gap, …): cosmetic pens in device pixels, geometric pens in
# multiples of the pen's width.
COSMETIC_DASHES = {PS_DASH: (18, 6), PS_DOT: (3, 3), PS_DASHDOT: (9, 6, 3, 6),
                   PS_DASHDOTDOT: (9, 3, 3, 3, 3, 3), PS_ALTERNATE: (1, 1)}
GEOMETRIC_DASHES = {PS_DASH: (3, 1), PS_DOT: (1, 1), PS_DASHDOT: (3, 1, 1, 1),
                    PS_DASHDOTDOT: (3, 1, 1, 1, 1, 1)}
KAPPA = 0.5522847498   # cubic Bézier circle constant

# Drawing measured in pixels of the *output*. GDI draws two things in the pixels of
# whatever it is drawing on at the time (screen, printer, picture), not in the metafile's
# units, so on paper their size depends on that resolution and there is no single "right"
# size for a resolution-independent SVG:
#  * hatched brushes: 1-pixel lines 8 pixels apart. The SVG uses the look of a standard
#    screen at 100 % (96 dpi: lines 2.12 mm apart, 0.26 mm wide);
#  * the dashes of cosmetic (one-pixel) pens, e.g. 18 pixels on, 6 off. The line itself
#    is drawn HAIRLINE wide, so its dashes are counted in HAIRLINE-sized pixels (a dash
#    1.6 mm long).
# Both are the same whichever screen the metafile was made on (its reference device). The
# self-check draws them at the size Windows used for its own drawing instead
# (`rescale_output_pixels`), so it still compares like with like.
HATCH_CELL = 8                 # hatch pixels from one line to the next
HATCH_PIXEL = 2540 / 96        # SVG units (0.01 mm) per hatch pixel in the stored SVG
COSMETIC_PIXEL = HAIRLINE      # SVG units per pixel of a cosmetic pen's dashes

# Code pages of the A (8-bit) text records, by the font's character set.
CHARSET_CODEPAGES = {161: "cp1253", 162: "cp1254", 177: "cp1255", 178: "cp1256",
                     186: "cp1257", 204: "cp1251", 238: "cp1250", 163: "cp1258", 222: "cp874",
                     128: "cp932", 129: "cp949", 134: "gbk", 136: "cp950"}

# The Symbol font's letters as Unicode (Greek and the usual signs), for text written in it.
_SYMBOL_GREEK = "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"
_SYMBOL_GREEK_LOWER = "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"
SYMBOL_MAP = {**{0x41 + i: c for i, c in enumerate(_SYMBOL_GREEK)},
              **{0x61 + i: c for i, c in enumerate(_SYMBOL_GREEK_LOWER)},
              0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅", 0x5C: "∴",
              0x5E: "⊥", 0x60: "‾", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA5: "∞", 0xA6: "ƒ",
              0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±",
              0xB2: "″", 0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷",
              0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩",
              0xC8: "∪", 0xD1: "∇", 0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xE5: "∑", 0xF2: "∫"}


# ---------------------------------------------------------------------- small helpers

def _n(v: float, nd: int = 2) -> str:
    """A number with at most `nd` decimals and no trailing zeros ("12.5", "3", "-0.25")."""
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def hatch_pattern(pid: str, style: int, colour: str, back: str | None, pixel: float) -> str:
    """The <pattern> of a GDI hatch brush (`style` HS_HORIZONTAL … HS_DIAGCROSS) with lines
    in `colour` on `back` (None: transparent background), `pixel` SVG units per hatch pixel
    (lines `pixel` wide, HATCH_CELL pixels apart). Like GDI's, the pattern starts at the
    drawing's top-left corner. Marked class="hatch hatch-<style>" (see rescale_output_pixels)."""
    size = HATCH_CELL * pixel
    s, h = _n(size), _n(size / 2)
    lines = {0: f"M0 {h}H{s}", 1: f"M{h} 0V{s}", 2: f"M0 0L{s} {s}", 3: f"M0 {s}L{s} 0",
             4: f"M0 {h}H{s}M{h} 0V{s}",
             5: f"M0 0L{s} {s}M0 {s}L{s} 0"}.get(style, f"M0 0L{s} {s}")
    rect = f'<rect width="{s}" height="{s}" fill="{back}"/>' if back else ""
    return (f'<pattern id="{pid}" class="hatch hatch-{style}" patternUnits="userSpaceOnUse" '
            f'width="{s}" height="{s}">{rect}<path d="{lines}" stroke="{colour}" '
            f'stroke-width="{_n(pixel)}" fill="none"/></pattern>')


_HATCH_DEF = re.compile(r'<pattern id="([^"]+)" class="hatch hatch-(\d+)".*?</pattern>', re.S)
_COSMETIC_DASH = re.compile(r'class="cosmetic-dash" stroke-dasharray="([^"]+)"')


def rescale_output_pixels(svg: str, pixel: float) -> str:
    """`svg` with the drawing GDI measures in output pixels (hatched fills, the dashes of
    cosmetic pens) drawn at `pixel` SVG units per pixel, e.g. the pixels of a picture
    Windows drew (for the self-check). Everything else is unchanged."""
    def hatch(m: re.Match) -> str:
        body = m.group(0)
        stroke = re.search(r'<path [^>]*stroke="([^"]+)"', body)
        back = re.search(r'<rect [^>]*fill="([^"]+)"', body)
        return hatch_pattern(m.group(1), int(m.group(2)),
                             stroke.group(1) if stroke else "#000000",
                             back.group(1) if back else None, pixel)

    def dashes(m: re.Match) -> str:
        def pixels(v: str) -> float:   # (whole pixels, before the SVG's rounding)
            n = float(v) / COSMETIC_PIXEL
            return round(n) if abs(n - round(n)) < 0.01 else n
        values = " ".join(_n(pixels(v) * pixel, 3) for v in m.group(1).split())
        return f'class="cosmetic-dash" stroke-dasharray="{values}"'

    return _COSMETIC_DASH.sub(dashes, _HATCH_DEF.sub(hatch, svg))


def _ints(values) -> str:
    """Whole numbers for SVG path data, as short as SVG allows ("12 -3 4" → "12-3 4")."""
    return " ".join(map(str, values)).replace(" -", "-")


def _colour(colorref: int) -> str:
    """A Windows COLORREF (0x00BBGGRR) as "#rrggbb"."""
    r, g, b = colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _xml_char(c: str) -> bool:
    """Whether XML (and so SVG) can hold this character: not a control character, a lone
    half of a UTF-16 pair (0xD800–0xDFFF) or U+FFFE/U+FFFF."""
    o = ord(c)
    return (o in (0x9, 0xA, 0xD) or 0x20 <= o <= 0xD7FF or 0xE000 <= o <= 0xFFFD
            or 0x10000 <= o <= 0x10FFFF)


def xml_text(text: str) -> str:
    """`text` without the characters XML can't hold (see _xml_char)."""
    return text if all(_xml_char(c) for c in text) else "".join(
        c for c in text if _xml_char(c))


def _esc(text: str) -> str:
    return (xml_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _compose(m1, m2):
    """The affine map "m2, then m1"; each is (a, b, c, d, e, f): x' = a·x + c·y + e,
    y' = b·x + d·y + f (as an EMF XFORM: eM11, eM12, eM21, eM22, eDx, eDy)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(m, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _matrix_attr(m, nd: int = 4) -> str:
    if not all(math.isfinite(v) and abs(v) <= MAX_COORD for v in m):
        raise ValueError("transform out of range")   # (damaged data)
    return "matrix(" + " ".join(_n(v, nd) for v in m) + ")"


# ---------------------------------------------------------------------- PNG and DIBs

def png_bytes(pixels: np.ndarray, dpm: tuple[int, int] | None = None) -> bytes:
    """A PNG file of an (h, w, 3) RGB or (h, w, 4) RGBA uint8 array, optionally with its
    resolution (dots per metre, x and y), so it prints at a known size."""
    h, w, channels = pixels.shape
    colour_type = 6 if channels == 4 else 2

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = np.empty((h, w * channels + 1), np.uint8)
    rows[:, 0] = 0   # filter "none" on every row
    rows[:, 1:] = np.ascontiguousarray(pixels, dtype=np.uint8).reshape(h, w * channels)
    out = [b"\x89PNG\r\n\x1a\n",
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colour_type, 0, 0, 0))]
    if dpm:
        out.append(chunk(b"pHYs", struct.pack(">IIB", int(dpm[0]), int(dpm[1]), 1)))
    out.append(chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)))
    out.append(chunk(b"IEND", b""))
    return b"".join(out)


@dataclass
class Bitmap:
    """A picture stored inside the metafile (a DIB), decoded: `pixels` is (h, w, 3) RGB or
    (h, w, 4) RGBA, top row first. JPEG/PNG pictures are kept as they are (`encoded`)."""
    width: int
    height: int
    pixels: np.ndarray | None = None
    encoded: bytes = b""
    mime: str = ""

    def png(self, dpm: tuple[int, int] | None = None) -> bytes:
        if self.pixels is None:
            return self.encoded
        return png_bytes(self.pixels, dpm)

    def data_uri(self) -> str:
        if self.pixels is None:
            return f"data:{self.mime};base64," + base64.b64encode(self.encoded).decode("ascii")
        return "data:image/png;base64," + base64.b64encode(self.png()).decode("ascii")


def decode_dib(bmi: bytes, bits: bytes, alpha: bool = False) -> Bitmap | None:
    """Decode a device-independent bitmap (its BITMAPINFO and pixel bits): 1, 4, 8, 16, 24
    and 32 bits per pixel, uncompressed or with bit fields; JPEG/PNG are kept as they are.
    `alpha`: keep the fourth byte of 32-bit pixels as transparency. None if the bitmap
    can't be read (RLE compression, damaged data)."""
    if len(bmi) < 12:
        return None
    header = struct.unpack_from("<I", bmi, 0)[0]
    if header == 12:   # BITMAPCOREHEADER
        width, height, _planes, depth = struct.unpack_from("<HHHH", bmi, 4)
        compression, used, entry = 0, 0, 3
    else:
        if len(bmi) < 40:
            return None
        width, height, _planes, depth, compression = struct.unpack_from("<iiHHI", bmi, 4)
        used = struct.unpack_from("<I", bmi, 32)[0]
        entry = 4
    top_down = height < 0
    height = abs(height)
    if width <= 0 or height <= 0 or width * height > 200_000_000:
        return None
    if compression in (4, 5):   # BI_JPEG, BI_PNG: the bits are a whole file
        return Bitmap(width, height, encoded=bytes(bits),
                      mime="image/jpeg" if compression == 4 else "image/png")
    if compression not in (0, 3):   # BI_RLE8 / BI_RLE4 and others
        return None
    stride = ((width * depth + 31) // 32) * 4
    if len(bits) < stride * height:
        return None
    rows = np.frombuffer(bits, np.uint8, count=stride * height).reshape(height, stride)
    if depth <= 8:
        n = used or (1 << depth)
        start = header
        table = np.frombuffer(bmi, np.uint8, count=min(n * entry, max(0, len(bmi) - start)),
                              offset=start)
        n = len(table) // entry
        palette = table[: n * entry].reshape(n, entry)[:, 2::-1] if n else \
            np.array([[0, 0, 0], [255, 255, 255]], np.uint8)
        if depth == 1:
            index = np.unpackbits(rows, axis=1)[:, :width]
        elif depth == 4:
            index = np.empty((height, stride * 2), np.uint8)
            index[:, 0::2] = rows >> 4
            index[:, 1::2] = rows & 0x0F
            index = index[:, :width]
        elif depth == 8:
            index = rows[:, :width]
        else:
            return None
        pixels = palette[np.minimum(index, len(palette) - 1)]
    elif depth in (16, 32):
        values = rows[:, : width * depth // 8].copy().view("<u2" if depth == 16 else "<u4")
        if compression == 3:   # BI_BITFIELDS: the masks at byte 40 (after or in the header)
            if len(bmi) < 52:
                return None
            masks = struct.unpack_from("<III", bmi, 40)
            amask = struct.unpack_from("<I", bmi, 52)[0] if header >= 56 and len(bmi) >= 56 else 0
        elif depth == 16:
            masks, amask = (0x7C00, 0x03E0, 0x001F), 0
        else:
            masks, amask = (0xFF0000, 0x00FF00, 0x0000FF), 0xFF000000
        channels = [_bitfield(values, m) for m in masks]
        if alpha and depth == 32 and amask:
            channels.append(_bitfield(values, amask))
        pixels = np.stack(channels, axis=2)
    elif depth == 24:
        pixels = rows[:, : width * 3].reshape(height, width, 3)[:, :, ::-1]
    else:
        return None
    if not top_down:
        pixels = pixels[::-1]
    return Bitmap(width, height, np.ascontiguousarray(pixels, dtype=np.uint8))


def _bitfield(values: np.ndarray, mask: int) -> np.ndarray:
    """One colour channel (0–255) of packed pixels."""
    if not mask:
        return np.zeros(values.shape, np.uint8)
    shift = (mask & -mask).bit_length() - 1
    top = mask >> shift
    channel = (values >> shift) & top
    return (channel.astype(np.uint32) * 255 // max(1, top)).astype(np.uint8)


# ---------------------------------------------------------------------- GDI objects

@dataclass(frozen=True)
class Pen:
    style: int = PS_SOLID       # PS_SOLID, PS_DASH… (the low 4 bits of the pen style)
    width: float = 0.0          # logical units; 0 = cosmetic (one device pixel)
    color: int = 0
    cosmetic: bool = True
    cap: str = "round"
    join: str = "round"
    dashes: tuple = ()          # PS_USERSTYLE entries
    extended: bool = False      # made by EXTCREATEPEN (dashes allowed at any width)

    @property
    def null(self) -> bool:
        return self.style == PS_NULL


@dataclass(frozen=True)
class Brush:
    style: int = BS_SOLID
    color: int = 0xFFFFFF
    hatch: int = 0
    pattern: Bitmap | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Font:
    height: int = -16           # LOGFONT lfHeight (negative: the em size; positive: cell)
    width: int = 0
    escapement: int = 0         # tenths of a degree, counter-clockwise
    orientation: int = 0
    weight: int = 400
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    charset: int = 0
    pitch_family: int = 0
    face: str = "Arial"

    def kind(self) -> str:
        """"sans", "serif" or "mono": which fall-back fonts suit it."""
        lower = self.face.lower()
        if self.charset == SYMBOL_CHARSET or lower == "symbol":
            return "serif"
        if (any(k in lower for k in ("courier", "mono", "consolas", "fixedsys", "terminal",
                                     "lucida console")) or self.pitch_family & 0xF0 == 0x30):
            return "mono"
        if "sans" in lower:
            return "sans"
        if (any(k in lower for k in ("times", "roman", "serif", "georgia", "cambria",
                                     "garamond", "palatino", "book antiqua", "century"))
                or self.pitch_family & 0xF0 == 0x10):
            return "serif"
        return "sans"

    def families(self) -> list[str]:
        """The font to use, then fall-backs that look alike on Windows, macOS and Linux."""
        fallbacks = {"sans": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans",
                              "sans-serif"],
                     "serif": ["Times New Roman", "Times", "Liberation Serif", "DejaVu Serif",
                               "serif"],
                     "mono": ["Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono",
                              "monospace"]}[self.kind()]
        face = self.face.replace("'", "").replace('"', "").strip()
        if self.charset == SYMBOL_CHARSET or face.lower() == "symbol":
            face = ""   # its letters are turned into Unicode Greek (SYMBOL_MAP)
        out: list[str] = []
        for name in ([face] if face else []) + fallbacks:
            if name.lower() not in (o.lower() for o in out):
                out.append(name)
        return out

    def family_attr(self) -> str:
        generic = ("sans-serif", "serif", "monospace")
        return ", ".join(n if n in generic else f"'{n}'" for n in self.families())


STOCK_OBJECTS = {
    0: Brush(BS_SOLID, 0xFFFFFF), 1: Brush(BS_SOLID, 0xC0C0C0), 2: Brush(BS_SOLID, 0x808080),
    3: Brush(BS_SOLID, 0x404040), 4: Brush(BS_SOLID, 0x000000), 5: Brush(BS_NULL, 0),
    6: Pen(color=0xFFFFFF), 7: Pen(color=0), 8: Pen(style=PS_NULL),
    10: Font(height=12, face="Terminal", pitch_family=0x31),
    11: Font(height=12, face="Courier New", pitch_family=0x31),
    12: Font(height=13, face="MS Sans Serif"), 13: Font(height=16, weight=700, face="System"),
    14: Font(height=16, weight=700, face="System"), 16: Font(height=15, face="Fixedsys"),
    17: Font(height=-11, face="MS Shell Dlg"),
    18: Brush(BS_SOLID, 0xFFFFFF), 19: Pen(color=0),
}


class TextHelper(Protocol):
    """Font knowledge the pure converter doesn't have (supplied by ui/, using Qt)."""

    def metrics(self, font: Font) -> tuple[float, float] | None:
        """(ascent, descent) of the font as fractions of its em size."""

    def glyph_text(self, font: Font, glyphs: list[int]) -> str | None:
        """The characters of glyph numbers (text written with ETO_GLYPH_INDEX), or None."""

    def glyph_outline(self, font: Font, glyphs: list[int], xs: list[float],
                      em: float) -> str | None:
        """SVG path data of the glyphs' outlines, glyph i at (xs[i], 0) on the baseline,
        y pointing down, at em size `em`; None if the font can't be found."""


DEFAULT_METRICS = {"sans": (0.905, 0.212), "serif": (0.891, 0.216), "mono": (0.833, 0.300)}


# ---------------------------------------------------------------------- results

@dataclass
class Header:
    bounds: tuple[int, int, int, int]
    frame: tuple[int, int, int, int]     # 0.01 mm
    records: int
    device_px: tuple[int, int]           # the reference device, in pixels…
    device_mm: tuple[int, int]           # …and in millimetres
    device_um: tuple[int, int] | None    # …and in micrometres (newer metafiles)

    @property
    def width_mm(self) -> float:
        return (self.frame[2] - self.frame[0]) / 100

    @property
    def height_mm(self) -> float:
        return (self.frame[3] - self.frame[1]) / 100


def read_header(data: bytes) -> Header | None:
    """The metafile's header (its size and reference device), or None if `data` isn't an
    Enhanced Metafile."""
    if len(data) < 88:
        return None
    kind, size = struct.unpack_from("<II", data, 0)
    if kind != 1 or size < 88 or size > len(data):
        return None
    bounds = struct.unpack_from("<4i", data, 8)
    frame = struct.unpack_from("<4i", data, 24)
    signature, _version, _nbytes, records = struct.unpack_from("<4I", data, 40)
    if signature != EMF_SIGNATURE:
        return None
    device_px = struct.unpack_from("<2i", data, 72)
    device_mm = struct.unpack_from("<2i", data, 80)
    device_um = struct.unpack_from("<2i", data, 100) if size >= 108 else None
    if device_um is not None and (device_um[0] <= 0 or device_um[1] <= 0):
        device_um = None
    if min(device_px) <= 0 or min(device_mm) <= 0:
        return None
    return Header(bounds, frame, records, device_px, device_mm, device_um)


@dataclass
class PlacedBitmap:
    """A picture drawn by the metafile: where its corners (0, 0), (w, 0), (0, h) land, in
    SVG units (0.01 mm from the frame's top-left corner)."""
    bitmap: Bitmap
    p0: tuple[float, float]
    p1: tuple[float, float]
    p3: tuple[float, float]
    opacity: float = 1.0

    def rect(self) -> tuple[float, float, float, float] | None:
        """(x, y, width, height) when drawn upright (not turned or slanted), else None."""
        (x0, y0), (x1, y1), (x3, y3) = self.p0, self.p1, self.p3
        if abs(y1 - y0) > 1 or abs(x3 - x0) > 1:
            return None
        return min(x0, x1), min(y0, y3), abs(x1 - x0), abs(y3 - y0)

    @property
    def mirrored(self) -> tuple[bool, bool]:
        return self.p1[0] < self.p0[0], self.p3[1] < self.p0[1]


@dataclass
class Conversion:
    """What `convert` made of a metafile."""
    svg: str = ""
    width_mm: float = 0.0
    height_mm: float = 0.0
    records: int = 0
    unsupported: Counter = field(default_factory=Counter)   # drawing that was left out
    approximated: Counter = field(default_factory=Counter)  # drawn, but not exactly
    ignored: Counter = field(default_factory=Counter)       # nothing to draw (on purpose)
    images: list[PlacedBitmap] = field(default_factory=list)
    strokes: int = 0
    fills: int = 0
    dark_fills: int = 0          # fills that are not (nearly) white
    texts: int = 0
    emf_plus: bool = False       # it has EMF+ records (in comments)
    error: str = ""              # the data is damaged or cut off

    @property
    def ok(self) -> bool:
        """Everything in the metafile was converted (perhaps approximately)."""
        return not self.error and not self.unsupported

    @property
    def drawn(self) -> int:
        return self.strokes + self.fills + self.texts + len(self.images)

    def single_bitmap(self, cover: float = 0.9) -> PlacedBitmap | None:
        """The one picture that is the whole drawing (Mnova's default "metafile containing
        a bitmap"): one upright picture covering at least `cover` of the frame each way,
        with nothing else drawn but white or near-white backgrounds. None otherwise."""
        if len(self.images) != 1 or self.strokes or self.texts or self.dark_fills:
            return None
        placed = self.images[0]
        rect = placed.rect()
        if rect is None or placed.opacity < 1.0:
            return None
        width, height = self.width_mm * 100, self.height_mm * 100
        if width <= 0 or height <= 0:
            return None
        if rect[2] < cover * width or rect[3] < cover * height:
            return None
        return placed

    def summary(self) -> str:
        """A short technical description (for notes and troubleshooting)."""
        parts = [f"{self.records} records", f"{self.strokes} lines", f"{self.fills} fills",
                 f"{self.texts} texts", f"{len(self.images)} pictures"]
        if self.unsupported:
            parts.append("not converted: " + ", ".join(
                f"{k} ×{v}" for k, v in sorted(self.unsupported.items())))
        if self.approximated:
            parts.append("approximated: " + ", ".join(
                f"{k} ×{v}" for k, v in sorted(self.approximated.items())))
        if self.error:
            parts.append(self.error)
        return "; ".join(parts)


# ---------------------------------------------------------------------- the converter

@dataclass
class _State:
    map_mode: int = MM_TEXT
    win_org: tuple = (0.0, 0.0)
    win_ext: tuple = (1.0, 1.0)
    vp_org: tuple = (0.0, 0.0)
    vp_ext: tuple = (1.0, 1.0)
    world: tuple = IDENTITY
    pen: Pen = STOCK_OBJECTS[7]
    brush: Brush = STOCK_OBJECTS[0]
    font: Font = STOCK_OBJECTS[13]
    text_color: int = 0x000000
    bk_color: int = 0xFFFFFF
    bk_mode: int = OPAQUE
    text_align: int = 0
    fill_mode: int = ALTERNATE
    rop2: int = R2_COPYPEN
    arc_dir: int = AD_COUNTERCLOCKWISE
    miter: float = 10.0
    cur: tuple = (0.0, 0.0)          # the current position (logical units)
    clip_rect: tuple | None = None   # (x0, y0, x1, y1) in SVG units, or None (no limit)
    clip_shapes: tuple = ()          # further clip shapes (ids), all intersected
    meta_rect: tuple | None = None   # the "meta region" (SETMETARGN): the floor of clipping
    meta_shapes: tuple = ()


# Qt's SVG renderer (the screen, auto-crop and the self-check) silently leaves out a
# <path> with more than about 32,000 segments; Typst has no such limit. Long lines are
# therefore written as several <path> elements of at most PIECE_SEGMENTS segments, which
# looks the same (round joins).
CHUNK_SEGMENTS = 4000
PIECE_SEGMENTS = 16000


class _PathData:
    """SVG path data in whole SVG units: absolute moves, relative lines and curves. Kept
    in parts, so a long line can be split into several elements (pieces())."""

    def __init__(self):
        # (text, segments, absolute point before it, start of its figure)
        self.parts: list[tuple[str, int, tuple, tuple]] = []
        self.cur: tuple[int, int] | None = None
        self.start: tuple[int, int] | None = None
        self.segments = 0

    def __bool__(self) -> bool:
        return bool(self.parts)

    def _add(self, text: str, segments: int, before) -> None:
        self.parts.append((text, segments, before, self.start))
        self.segments += segments

    @staticmethod
    def _check(pts) -> None:
        """Damaged data (a transform of 1e30, an infinite or undefined number) gives
        coordinates no drawing has: the record counts as damaged instead."""
        arr = np.asarray(pts, np.float64)
        if arr.size and not (np.isfinite(arr).all() and np.abs(arr).max() <= MAX_COORD):
            raise ValueError("coordinates out of range")

    def move(self, x: float, y: float) -> None:
        self._check((x, y))
        ix, iy = int(round(x)), int(round(y))
        self.start = (ix, iy)
        self._add(f"M{ix} {iy}", 0, (ix, iy))
        self.cur = (ix, iy)

    def lines(self, pts: np.ndarray) -> None:
        """Lines from the current point through `pts` ((n, 2) SVG units)."""
        if not len(pts):
            return
        if self.cur is None:
            self.move(*pts[0])
            pts = pts[1:]
            if not len(pts):
                return
        self._check(pts)
        r = np.rint(pts).astype(np.int64)
        prev = np.vstack((np.array([self.cur], np.int64), r[:-1]))
        keep = (r != prev).any(axis=1)
        deltas, ends = (r - prev)[keep], r[keep]
        last = self.parts[-1] if self.parts else None
        if (last is not None and last[0].startswith("l") and len(deltas)
                and last[1] + len(deltas) <= CHUNK_SEGMENTS):   # (one "l" for LINETO runs)
            more = _ints(deltas.ravel().tolist())
            self.parts[-1] = (last[0] + ("" if more.startswith("-") else " ") + more,
                              last[1] + len(deltas), last[2], last[3])
            self.segments += len(deltas)
            self.cur = (int(ends[-1, 0]), int(ends[-1, 1]))
            return
        for at in range(0, len(deltas), CHUNK_SEGMENTS):
            chunk = deltas[at: at + CHUNK_SEGMENTS]
            before = self.cur
            self.cur = (int(ends[at + len(chunk) - 1, 0]), int(ends[at + len(chunk) - 1, 1]))
            self._add("l" + _ints(chunk.ravel().tolist()), len(chunk), before)

    def curves(self, pts: np.ndarray) -> None:
        """Cubic Béziers from the current point: `pts` holds 3 points per curve."""
        n = len(pts) // 3
        if not n:
            return
        if self.cur is None:
            self.move(*pts[0])
        self._check(pts[: 3 * n])
        r = np.rint(pts[: 3 * n]).astype(np.int64).reshape(n, 3, 2)
        starts = np.vstack((np.array([self.cur], np.int64), r[:-1, 2]))
        rel = r - starts[:, None, :]
        before = self.cur
        self.cur = (int(r[-1, 2, 0]), int(r[-1, 2, 1]))
        self._add("c" + _ints(rel.ravel().tolist()), 3 * n, before)

    def close(self) -> None:
        if self.parts and self.parts[-1][0] != "z":
            self._add("z", 1, self.cur)
            self.cur = self.start

    def text(self) -> str:
        return "".join(p[0] for p in self.parts)

    def pieces(self, limit: int = PIECE_SEGMENTS) -> list[tuple[str, int]]:
        """The path data split into pieces of at most `limit` segments, each starting with
        a move to where the last one ended (for lines only: a fill can't be split):
        [(path data, segments)]."""
        if self.segments <= limit:
            return [(self.text(), self.segments)]
        out: list[tuple[str, int]] = []
        texts: list[str] = []
        count = 0
        continued = False   # the piece began inside a figure started in an earlier one
        for text, segments, before, figure in self.parts:
            if texts and count + segments > limit:
                out.append(("".join(texts), count))
                texts, count = [], 0
            if not texts and not text.startswith("M"):
                texts.append(f"M{before[0]} {before[1]}")
                continued = True
            if text.startswith("M"):
                continued = False
            elif text == "z" and continued and figure is not None:
                text = f"L{figure[0]} {figure[1]}"   # (closing a figure of an earlier piece)
            texts.append(text)
            count += segments
        if texts:
            out.append(("".join(texts), count))
        return out


class _Converter:
    def __init__(self, data: bytes, helper: TextHelper | None):
        self.data = data
        self.helper = helper
        self.result = Conversion()
        self.st = _State()
        self.stack: list[_State] = []
        self.objects: dict[int, object] = {}
        self.out: list[tuple[tuple, str]] = []     # (clip key, element)
        self._pending: tuple | None = None         # (clip key, attributes, [path data])
        self.clip_defs: dict[tuple, str] = {}      # clip shape → id
        self.clip_shapes: dict[str, str] = {}      # id → <clipPath> body
        self.patterns: dict[tuple, str] = {}       # pattern key → id
        self.pattern_defs: list[str] = []
        self.path: _PathData | None = None         # between BEGINPATH and ENDPATH…
        self.path_open = False
        self.path_figures = False                  # …and whether it holds anything
        self._m = None

    # ------------------------------------------------------------------ transforms

    def _page(self):
        st = self.st
        wx, wy = st.win_ext
        vx, vy = st.vp_ext
        sx = vx / wx if wx else 1.0
        sy = vy / wy if wy else 1.0
        return (sx, 0.0, 0.0, sy, st.vp_org[0] - st.win_org[0] * sx,
                st.vp_org[1] - st.win_org[1] * sy)

    @property
    def m(self):
        """Logical units → SVG units (world transform, then page, then the frame)."""
        if self._m is None:
            self._m = _compose(self.base, _compose(self._page(), self.st.world))
        return self._m

    def _changed(self) -> None:
        self._m = None

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return _apply(self.m, x, y)

    def pts(self, arr: np.ndarray) -> np.ndarray:
        a, b, c, d, e, f = self.m
        x, y = arr[:, 0].astype(np.float64), arr[:, 1].astype(np.float64)
        return np.column_stack((a * x + c * y + e, b * x + d * y + f))

    def scale(self) -> float:
        """How much a length in logical units grows in SVG units (for pen widths)."""
        a, b, c, d, _e, _f = self.m
        return math.sqrt(abs(a * d - b * c))

    def dev_pts(self, arr: np.ndarray) -> np.ndarray:
        """Reference-device pixels → SVG units (regions are in device units)."""
        a, b, c, d, e, f = self.base
        return np.column_stack((a * arr[:, 0] + e, d * arr[:, 1] + f))

    def _fixed_extents(self, mode: int) -> tuple[tuple, tuple] | None:
        """Window and viewport extents of the fixed mapping modes (from the reference
        device's size), as GDI sets them."""
        (px, py), (mx, my) = self.header.device_px, self.header.device_mm
        per_mm = {MM_LOMETRIC: 10.0, MM_ISOTROPIC: 10.0, MM_HIMETRIC: 100.0,
                  MM_LOENGLISH: 100 / 25.4, MM_HIENGLISH: 1000 / 25.4, MM_TWIPS: 1440 / 25.4}
        if mode == MM_TEXT:
            return (1.0, 1.0), (1.0, 1.0)
        if mode in per_mm:
            k = per_mm[mode]
            return (mx * k, my * k), (float(px), float(-py))
        return None

    def _fix_isotropic(self) -> None:
        st = self.st
        (px, py), (mx, my) = self.header.device_px, self.header.device_mm
        wx, wy = st.win_ext
        vx, vy = st.vp_ext
        if not (wx and wy and vx and vy):
            return
        xdim = abs(vx * mx / (px * wx))
        ydim = abs(vy * my / (py * wy))
        if xdim > ydim:
            new = math.floor(vx * ydim / xdim + 0.5) or (1 if vx >= 0 else -1)
            st.vp_ext = (float(new), vy)
        elif ydim > xdim:
            new = math.floor(vy * xdim / ydim + 0.5) or (1 if vy >= 0 else -1)
            st.vp_ext = (vx, float(new))

    # ------------------------------------------------------------------ running

    def run(self) -> Conversion:
        r = self.result
        header = read_header(self.data)
        if header is None:
            r.error = "This is not an Enhanced Metafile."
            return r
        self.header = header
        l, t, rr, b = header.frame
        r.width_mm, r.height_mm = (rr - l) / 100, (b - t) / 100
        if r.width_mm <= 0 or r.height_mm <= 0:
            r.error = "The metafile has no size."
            return r
        (px, py), (mx, my) = header.device_px, header.device_mm
        # SVG unit (0.01 mm) per reference-device pixel, as Windows maps the frame onto
        # the output when it plays the metafile.
        sx, sy = mx * 100 / px, my * 100 / py
        self.base = (sx, 0.0, 0.0, sy, -float(l), -float(t))
        self.px_units = (sx, sy)
        handlers = self._handlers()
        data = self.data
        off, size_total = 0, len(data)
        count = 0
        ended = False
        while off + 8 <= size_total:
            kind, size = struct.unpack_from("<II", data, off)
            if size < 8 or off + size > size_total:
                r.error = "The metafile is incomplete (cut off or damaged)."
                break
            count += 1
            if count > MAX_RECORDS:
                r.error = "The metafile has too many records."
                break
            if kind == 14:   # EOF
                ended = True
                break
            if kind != 1:
                rec = data[off: off + size]
                handler = handlers.get(kind)
                name = RECORD_NAMES.get(kind, f"record {kind}")
                if handler is not None:
                    try:
                        handler(rec)
                    except (struct.error, IndexError, ValueError, ZeroDivisionError,
                            OverflowError, TypeError):
                        r.unsupported[f"damaged {name}"] += 1
                elif kind in IGNORED:
                    r.ignored[name] += 1
                else:
                    r.unsupported[name] += 1
            off += size
        if not ended and not r.error:
            r.error = "The metafile is incomplete (cut off or damaged)."
        r.records = count
        self._flush()
        r.svg = self._svg()
        return r

    def _handlers(self) -> dict:
        h = {
            2: lambda rec: self._poly(rec, 4, "bezier"), 3: lambda rec: self._poly(rec, 4, "polygon"),
            4: lambda rec: self._poly(rec, 4, "polyline"), 5: lambda rec: self._poly(rec, 4, "bezierto"),
            6: lambda rec: self._poly(rec, 4, "polylineto"),
            85: lambda rec: self._poly(rec, 2, "bezier"), 86: lambda rec: self._poly(rec, 2, "polygon"),
            87: lambda rec: self._poly(rec, 2, "polyline"), 88: lambda rec: self._poly(rec, 2, "bezierto"),
            89: lambda rec: self._poly(rec, 2, "polylineto"),
            7: lambda rec: self._polypoly(rec, 4, False), 8: lambda rec: self._polypoly(rec, 4, True),
            90: lambda rec: self._polypoly(rec, 2, False), 91: lambda rec: self._polypoly(rec, 2, True),
            56: lambda rec: self._polydraw(rec, 4), 92: lambda rec: self._polydraw(rec, 2),
            9: self._set_window_ext, 10: self._set_window_org, 11: self._set_viewport_ext,
            12: self._set_viewport_org, 31: lambda rec: self._scale_ext(rec, "vp"),
            32: lambda rec: self._scale_ext(rec, "win"), 17: self._set_map_mode,
            18: lambda rec: self._set_attr(rec, "bk_mode"),
            19: lambda rec: self._set_attr(rec, "fill_mode"),
            20: lambda rec: self._set_attr(rec, "rop2"),
            21: lambda rec: None,   # SETSTRETCHBLTMODE: pictures are drawn smoothly anyway
            22: lambda rec: self._set_attr(rec, "text_align"),
            24: lambda rec: self._set_attr(rec, "text_color"),
            25: lambda rec: self._set_attr(rec, "bk_color"),
            57: lambda rec: self._set_attr(rec, "arc_dir"),
            58: self._set_miter, 26: self._offset_clip, 27: self._move_to, 28: self._set_meta_rgn,
            29: lambda rec: self._clip_rect(rec, exclude=True),
            30: lambda rec: self._clip_rect(rec, exclude=False),
            33: self._save_dc, 34: self._restore_dc, 35: self._set_world, 36: self._modify_world,
            37: self._select_object, 38: self._create_pen, 39: self._create_brush,
            40: self._delete_object, 41: self._angle_arc, 42: self._ellipse, 43: self._rectangle,
            44: self._round_rect, 45: lambda rec: self._arc(rec, "arc"),
            46: lambda rec: self._arc(rec, "chord"), 47: lambda rec: self._arc(rec, "pie"),
            55: lambda rec: self._arc(rec, "arcto"), 54: self._line_to,
            59: self._begin_path, 60: self._end_path, 61: self._close_figure,
            62: lambda rec: self._draw_path(fill=True, stroke=False),
            63: lambda rec: self._draw_path(fill=True, stroke=True),
            64: lambda rec: self._draw_path(fill=False, stroke=True),
            66: self._widen_path, 67: self._select_clip_path, 68: self._abort_path,
            70: self._comment, 71: self._fill_rgn, 74: self._paint_rgn,
            75: self._ext_select_clip_rgn, 76: lambda rec: self._blt(rec, stretch=False),
            77: lambda rec: self._blt(rec, stretch=True),
            114: lambda rec: self._blt(rec, stretch=True, blend=True),
            80: self._set_dibits, 81: self._stretch_dibits, 82: self._create_font,
            83: lambda rec: self._text(rec, wide=False), 84: lambda rec: self._text(rec, wide=True),
            93: self._create_pattern_brush, 94: self._create_pattern_brush,
            95: self._ext_create_pen, 15: self._set_pixel, 115: self._set_layout,
        }
        return h

    # ------------------------------------------------------------------ state records

    def _set_attr(self, rec: bytes, name: str) -> None:
        setattr(self.st, name, struct.unpack_from("<I", rec, 8)[0])

    def _set_miter(self, rec: bytes) -> None:
        value = struct.unpack_from("<f", rec, 8)[0]
        if not (1.0 <= value <= 1000.0):   # (some writers store a whole number)
            value = float(struct.unpack_from("<I", rec, 8)[0])
        self.st.miter = min(max(value, 1.0), 100.0)

    def _set_map_mode(self, rec: bytes) -> None:
        mode = struct.unpack_from("<I", rec, 8)[0]
        if not MM_TEXT <= mode <= MM_ANISOTROPIC:
            return
        self.st.map_mode = mode
        fixed = self._fixed_extents(mode)
        if fixed is not None:
            self.st.win_ext, self.st.vp_ext = fixed
        self._changed()

    def _set_window_ext(self, rec: bytes) -> None:
        x, y = struct.unpack_from("<ii", rec, 8)
        if self.st.map_mode in (MM_ISOTROPIC, MM_ANISOTROPIC) and x and y:
            self.st.win_ext = (float(x), float(y))
            if self.st.map_mode == MM_ISOTROPIC:
                self._fix_isotropic()
            self._changed()

    def _set_viewport_ext(self, rec: bytes) -> None:
        x, y = struct.unpack_from("<ii", rec, 8)
        if self.st.map_mode in (MM_ISOTROPIC, MM_ANISOTROPIC) and x and y:
            self.st.vp_ext = (float(x), float(y))
            if self.st.map_mode == MM_ISOTROPIC:
                self._fix_isotropic()
            self._changed()

    def _set_window_org(self, rec: bytes) -> None:
        self.st.win_org = tuple(float(v) for v in struct.unpack_from("<ii", rec, 8))
        self._changed()

    def _set_viewport_org(self, rec: bytes) -> None:
        self.st.vp_org = tuple(float(v) for v in struct.unpack_from("<ii", rec, 8))
        self._changed()

    def _scale_ext(self, rec: bytes, which: str) -> None:
        xn, xd, yn, yd = struct.unpack_from("<4i", rec, 8)
        if self.st.map_mode not in (MM_ISOTROPIC, MM_ANISOTROPIC) or not (xd and yd):
            return
        attr = "vp_ext" if which == "vp" else "win_ext"
        x, y = getattr(self.st, attr)
        new = (x * xn / xd, y * yn / yd)
        if new[0] and new[1]:
            setattr(self.st, attr, new)
            if self.st.map_mode == MM_ISOTROPIC:
                self._fix_isotropic()
            self._changed()

    def _set_world(self, rec: bytes) -> None:
        self.st.world = tuple(float(v) for v in struct.unpack_from("<6f", rec, 8))
        self._changed()

    def _modify_world(self, rec: bytes) -> None:
        xform = tuple(float(v) for v in struct.unpack_from("<6f", rec, 8))
        mode = struct.unpack_from("<I", rec, 32)[0]
        if mode == 1:
            self.st.world = IDENTITY
        elif mode == 2:     # MWT_LEFTMULTIPLY: the new transform first, then the old one
            self.st.world = _compose(self.st.world, xform)
        elif mode == 3:     # MWT_RIGHTMULTIPLY
            self.st.world = _compose(xform, self.st.world)
        elif mode == 4:
            self.st.world = xform
        self._changed()

    def _move_to(self, rec: bytes) -> None:
        x, y = struct.unpack_from("<ii", rec, 8)
        self.st.cur = (float(x), float(y))
        if self.path_open:
            self.path.move(*self.pt(x, y))

    def _save_dc(self, rec: bytes) -> None:
        if len(self.stack) >= MAX_SAVED_STATES:   # (damaged data: memory must stay bounded)
            self.result.unsupported["SAVEDC (too many saved states)"] += 1
            return
        self.stack.append(replace(self.st))

    def _restore_dc(self, rec: bytes) -> None:
        n = struct.unpack_from("<i", rec, 8)[0]
        if n < 0:
            depth = -n
            if depth > len(self.stack):
                return
            state = self.stack[-depth]
            del self.stack[-depth:]
        elif 0 < n <= len(self.stack):
            state = self.stack[n - 1]
            del self.stack[n - 1:]
        else:
            return
        self.st = state
        self._changed()

    def _set_layout(self, rec: bytes) -> None:
        if struct.unpack_from("<I", rec, 8)[0] & 1:   # right-to-left (mirrored) drawing
            self.result.unsupported["SETLAYOUT (mirrored)"] += 1

    # ------------------------------------------------------------------ objects

    def _select_object(self, rec: bytes) -> None:
        handle = struct.unpack_from("<I", rec, 8)[0]
        obj = STOCK_OBJECTS.get(handle & ~STOCK_BIT) if handle & STOCK_BIT else \
            self.objects.get(handle)
        if isinstance(obj, Pen):
            self.st.pen = obj
        elif isinstance(obj, Brush):
            self.st.brush = obj
        elif isinstance(obj, Font):
            self.st.font = obj

    def _delete_object(self, rec: bytes) -> None:
        self.objects.pop(struct.unpack_from("<I", rec, 8)[0], None)

    def _create_pen(self, rec: bytes) -> None:
        handle, style, width, _wy, color = struct.unpack_from("<IIiiI", rec, 8)
        kind = style & 0xF
        self.objects[handle] = Pen(style=kind, width=float(abs(width)), color=color,
                                   cosmetic=width == 0, cap=_cap(style), join=_join(style))

    def _ext_create_pen(self, rec: bytes) -> None:
        handle = struct.unpack_from("<I", rec, 8)[0]
        style, width, brush_style, color, _hatch, entries = struct.unpack_from("<6I", rec, 28)
        dashes = struct.unpack_from(f"<{min(entries, 32)}I", rec, 52) if entries else ()
        kind = style & 0xF
        if brush_style == BS_NULL:
            kind = PS_NULL
        elif brush_style not in (BS_SOLID, BS_HATCHED):
            self.result.approximated["pattern pen"] += 1
        geometric = bool(style & PS_GEOMETRIC)
        self.objects[handle] = Pen(style=kind, width=float(width) if geometric else 0.0,
                                   color=color, cosmetic=not geometric, cap=_cap(style),
                                   join=_join(style), dashes=tuple(dashes), extended=True)

    def _create_brush(self, rec: bytes) -> None:
        handle, style, color, hatch = struct.unpack_from("<4I", rec, 8)
        self.objects[handle] = Brush(style=style, color=color, hatch=hatch)

    def _create_pattern_brush(self, rec: bytes) -> None:
        handle, _usage, off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<6I", rec, 8)
        bitmap = decode_dib(rec[off_bmi: off_bmi + cb_bmi], rec[off_bits: off_bits + cb_bits])
        self.objects[handle] = Brush(style=BS_PATTERN, color=0x808080, pattern=bitmap)

    def _create_font(self, rec: bytes) -> None:
        handle = struct.unpack_from("<I", rec, 8)[0]
        height, width, esc, orient, weight = struct.unpack_from("<5i", rec, 12)
        italic, underline, strike, charset, _out, _clip, _quality, pitch = \
            struct.unpack_from("<8B", rec, 32)
        face = xml_text(rec[40:104].decode("utf-16-le", "replace").split("\x00", 1)[0])
        self.objects[handle] = Font(height=height, width=width, escapement=esc,
                                    orientation=orient, weight=weight or 400,
                                    italic=bool(italic), underline=bool(underline),
                                    strikeout=bool(strike), charset=charset,
                                    pitch_family=pitch, face=face.strip() or "Arial")

    # ------------------------------------------------------------------ styles

    def _rop_colour(self, color: int) -> str | None:
        """The colour a pen or brush paints with the current ROP2 mode (None: nothing is
        drawn). Modes that combine with what is underneath are counted as unsupported."""
        rop = self.st.rop2
        if rop == R2_COPYPEN:
            return _colour(color)
        if rop == R2_NOP:
            return None
        if rop == R2_BLACK:
            return "#000000"
        if rop == R2_WHITE:
            return "#ffffff"
        if rop == R2_NOTCOPYPEN:
            return _colour(~color & 0xFFFFFF)
        if rop == R2_MASKPEN:   # pen AND paper: the pen's colour on white paper
            self.result.approximated["SETROP2 (mask pen)"] += 1
            return _colour(color)
        self.result.unsupported[f"SETROP2 mode {rop}"] += 1
        return None

    def _stroke_attrs(self) -> str | None:
        pen = self.st.pen
        if pen.null:
            return None
        colour = self._rop_colour(pen.color)
        if colour is None:
            return None
        width = HAIRLINE if pen.cosmetic else max(HAIRLINE, pen.width * self.scale())
        attrs = f'stroke="{colour}" stroke-width="{_n(width)}"'
        if pen.cap != "round":
            attrs += f' stroke-linecap="{pen.cap}"'
        if pen.join != "round":
            attrs += f' stroke-linejoin="{pen.join}"'
            if pen.join == "miter":
                attrs += f' stroke-miterlimit="{_n(self.st.miter)}"'
        dashes = self._dashes(pen, width)
        if dashes:
            if pen.cosmetic:   # (marked for rescale_output_pixels: the self-check)
                attrs += ' class="cosmetic-dash"'
            attrs += f' stroke-dasharray="{" ".join(_n(d) for d in dashes)}"'
        return attrs

    def _dashes(self, pen: Pen, width: float) -> tuple:
        kind = pen.style
        if kind in (PS_SOLID, PS_INSIDEFRAME, PS_NULL):
            return ()
        if not pen.extended and not pen.cosmetic and pen.width * self.scale() > \
                1.5 * self.px_units[0]:
            return ()   # (CreatePen: patterns only for pens up to one pixel wide)
        px = COSMETIC_PIXEL   # (cosmetic dashes: pixels of the output, see COSMETIC_PIXEL)
        if kind == PS_USERSTYLE:
            if not pen.dashes:
                return ()
            k = px if pen.cosmetic else self.scale()
            return tuple(max(0.5, d * k) for d in pen.dashes)
        if pen.cosmetic:
            pattern = COSMETIC_DASHES.get(kind)
            return tuple(d * px for d in pattern) if pattern else ()
        pattern = GEOMETRIC_DASHES.get(kind)
        return tuple(d * width for d in pattern) if pattern else ()

    def _fill_attrs(self, brush: Brush | None = None, rule: bool = True) -> str | None:
        brush = brush or self.st.brush
        if brush.style == BS_NULL:
            return None
        if brush.style == BS_SOLID:
            colour = self._rop_colour(brush.color)
            if colour is None:
                return None
            fill = colour
        elif brush.style == BS_HATCHED:
            fill = f"url(#{self._hatch_pattern(brush)})"
        elif brush.pattern is not None:
            fill = f"url(#{self._image_pattern(brush)})"
            self.result.approximated["pattern brush"] += 1
        else:
            self.result.unsupported["pattern brush"] += 1
            return None
        attrs = f'fill="{fill}"'
        if rule and self.st.fill_mode == ALTERNATE:
            attrs += ' fill-rule="evenodd"'
        return attrs

    def _hatch_pattern(self, brush: Brush) -> str:
        opaque = self.st.bk_mode == OPAQUE
        key = ("hatch", brush.hatch, brush.color, self.st.bk_color if opaque else None)
        if key in self.patterns:
            return self.patterns[key]
        pid = f"p{len(self.patterns) + 1}"
        self.patterns[key] = pid
        self.pattern_defs.append(hatch_pattern(
            pid, brush.hatch, _colour(brush.color),
            _colour(self.st.bk_color) if opaque else None, HATCH_PIXEL))
        return pid

    def _image_pattern(self, brush: Brush) -> str:
        key = ("image", id(brush.pattern))
        if key in self.patterns:
            return self.patterns[key]
        pid = f"p{len(self.patterns) + 1}"
        self.patterns[key] = pid
        bm = brush.pattern
        w, h = bm.width * self.px_units[0], bm.height * self.px_units[1]
        self.pattern_defs.append(
            f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="{_n(w)}" '
            f'height="{_n(h)}"><image width="{_n(w)}" height="{_n(h)}" '
            f'preserveAspectRatio="none" xlink:href="{bm.data_uri()}"/></pattern>')
        return pid

    # ------------------------------------------------------------------ clipping

    def _clip_key(self) -> tuple:
        return (self.st.clip_rect, self.st.clip_shapes)

    def _shape_id(self, body: str) -> str:
        cid = self.clip_defs.get(body)
        if cid is None:
            cid = f"c{len(self.clip_defs) + 1}"
            self.clip_defs[body] = cid
            self.clip_shapes[cid] = body
        return cid

    def _add_clip_shape(self, body: str) -> None:
        """Intersect the clip with a shape (a <clipPath> body). The same shape again
        changes nothing; more than MAX_CLIP_SHAPES at once (damaged or very unusual data,
        each one more level of SVG clipping) counts as not converted."""
        cid = self._shape_id(body)
        shapes = self.st.clip_shapes
        if cid in shapes:
            return
        if len(shapes) >= MAX_CLIP_SHAPES:
            self.result.unsupported["clipping (too many regions at once)"] += 1
            return
        self.st.clip_shapes = shapes + (cid,)

    def _intersect_rect(self, rect: tuple) -> None:
        x0, y0, x1, y1 = rect
        cur = self.st.clip_rect
        if cur is not None:
            x0, y0 = max(x0, cur[0]), max(y0, cur[1])
            x1, y1 = min(x1, cur[2]), min(y1, cur[3])
        self.st.clip_rect = (x0, y0, max(x0, x1), max(y0, y1))

    def _poly_or_rect(self, corners: np.ndarray) -> tuple | None:
        """An upright rectangle (x0, y0, x1, y1) if the 4 corners make one, else None."""
        xs, ys = corners[:, 0], corners[:, 1]
        if (abs(corners[0, 1] - corners[1, 1]) < 0.5 and abs(corners[1, 0] - corners[2, 0]) < 0.5
                and abs(corners[2, 1] - corners[3, 1]) < 0.5 and
                abs(corners[3, 0] - corners[0, 0]) < 0.5):
            return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
        if (abs(corners[0, 0] - corners[1, 0]) < 0.5 and abs(corners[1, 1] - corners[2, 1]) < 0.5
                and abs(corners[2, 0] - corners[3, 0]) < 0.5 and
                abs(corners[3, 1] - corners[0, 1]) < 0.5):
            return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
        return None

    def _logical_rect_corners(self, l, t, r, b) -> np.ndarray:
        return self.pts(np.array([[l, t], [r, t], [r, b], [l, b]], np.float64))

    def _clip_rect(self, rec: bytes, exclude: bool) -> None:
        l, t, r, b = struct.unpack_from("<4i", rec, 8)
        corners = self._logical_rect_corners(l, t, r, b)
        rect = self._poly_or_rect(corners)
        if exclude:
            self._exclude(self._polygon_d(corners))
        elif rect is not None:
            self._intersect_rect(rect)
        else:
            self._add_clip_shape(f'<path d="{self._polygon_d(corners)}"/>')

    def _exclude(self, d: str) -> None:
        big = 10 ** 7
        outer = f"M{-big} {-big}H{big}V{big}H{-big}z"
        self._add_clip_shape(f'<path d="{outer}{d}" clip-rule="evenodd"/>')

    def _polygon_d(self, pts: np.ndarray) -> str:
        p = _PathData()
        p.move(*pts[0])
        p.lines(pts[1:])
        p.close()
        return p.text()

    def _set_clip(self, rect, shapes) -> None:
        self.st.clip_rect, self.st.clip_shapes = rect, shapes

    def _reset_clip(self) -> None:
        self._set_clip(self.st.meta_rect, self.st.meta_shapes)

    def _set_meta_rgn(self, rec: bytes) -> None:
        self.st.meta_rect, self.st.meta_shapes = self.st.clip_rect, self.st.clip_shapes

    def _offset_clip(self, rec: bytes) -> None:
        x, y = struct.unpack_from("<ii", rec, 8)
        if not (x or y):
            return
        a, b, c, d, _e, _f = self.m
        dx, dy = a * x + c * y, b * x + d * y
        if self.st.clip_rect is not None and not self.st.clip_shapes:
            x0, y0, x1, y1 = self.st.clip_rect
            self.st.clip_rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        elif self.st.clip_rect is not None or self.st.clip_shapes:
            self.result.unsupported["OFFSETCLIPRGN"] += 1

    def _region_rects(self, rec: bytes, at: int, size: int) -> list[tuple] | None:
        """The rectangles of a region (in SVG units), or None if there are none."""
        if size < 32:
            return None
        _hsize, _kind, count, _rsize = struct.unpack_from("<4I", rec, at)
        count = min(count, (size - 32) // 16)
        if not count:
            return []
        raw = np.frombuffer(rec, "<i4", count=count * 4, offset=at + 32).reshape(count, 4)
        tl = self.dev_pts(raw[:, 0:2].astype(np.float64))
        br = self.dev_pts(raw[:, 2:4].astype(np.float64))
        return [(float(a[0]), float(a[1]), float(b[0]), float(b[1])) for a, b in zip(tl, br)]

    @staticmethod
    def _rects_d(rects: list[tuple]) -> str:
        parts = []
        for x0, y0, x1, y1 in rects:
            parts.append(f"M{round(x0)} {round(y0)}H{round(x1)}V{round(y1)}H{round(x0)}z")
        return "".join(parts)

    def _ext_select_clip_rgn(self, rec: bytes) -> None:
        size, mode = struct.unpack_from("<II", rec, 8)
        rects = self._region_rects(rec, 16, size) if size else None
        if rects is None:
            if mode == RGN_COPY:
                self._reset_clip()
            return
        self._apply_region(rects, mode)

    def _apply_region(self, rects: list[tuple], mode: int, d: str | None = None) -> None:
        """Combine the clip with a region (its rectangles, or path data `d`)."""
        single = rects[0] if rects is not None and len(rects) == 1 else None
        if d is None:
            d = self._rects_d(rects or [])
        shape = f'<path d="{d}"/>'
        if mode == RGN_COPY:
            self._reset_clip()
            mode = RGN_AND
        if mode == RGN_AND:
            if single is not None:
                self._intersect_rect(single)
            elif rects is not None and not rects:
                self._intersect_rect((0, 0, 0, 0))
            else:
                self._add_clip_shape(shape)
        elif mode == RGN_DIFF:
            self._exclude(d)
        elif mode in (RGN_OR, RGN_XOR):
            if mode == RGN_XOR:
                self.result.approximated["clip region XOR"] += 1
            rect, shapes = self.st.clip_rect, self.st.clip_shapes
            if rect is None and not shapes:
                return    # everything is already visible
            if shapes:
                self.result.approximated["clip region union"] += 1
                self._set_clip(None, ())
                return
            x0, y0, x1, y1 = rect
            union = f"M{round(x0)} {round(y0)}H{round(x1)}V{round(y1)}H{round(x0)}z" + d
            self._set_clip(None, (self._shape_id(f'<path d="{union}"/>'),))

    # ------------------------------------------------------------------ output

    def _emit(self, element: str, clip: tuple | None = None) -> None:
        self._flush()
        self.out.append((clip if clip is not None else self._clip_key(), element))

    def _emit_stroke(self, d: str, segments: int, attrs: str) -> None:
        """A line with no fill: joined to the ones before when they look the same (fewer,
        larger paths), up to PIECE_SEGMENTS segments per path."""
        key = self._clip_key()
        pend = self._pending
        if (pend is not None and pend[0] == key and pend[1] == attrs
                and pend[3] + segments <= PIECE_SEGMENTS):
            pend[2].append(d)
            self._pending = (key, attrs, pend[2], pend[3] + segments)
            return
        self._flush()
        self._pending = (key, attrs, [d], segments)

    def _flush(self) -> None:
        if self._pending is not None:
            key, attrs, ds, _count = self._pending
            self._pending = None
            self.out.append((key, f'<path d="{"".join(ds)}" fill="none" {attrs}/>'))

    def _shape(self, path: _PathData | None, fill: bool, stroke: bool,
               closed: bool = True) -> None:
        """Draw a path with the current brush and/or pen."""
        if not path:
            return
        fill_attrs = self._fill_attrs() if fill and closed else None
        stroke_attrs = self._stroke_attrs() if stroke else None
        if fill_attrs is None and stroke_attrs is None:
            return
        r = self.result
        if fill_attrs is not None:
            r.fills += 1
            brush = self.st.brush
            if not (brush.style == BS_SOLID and _light(brush.color)):
                r.dark_fills += 1
        if stroke_attrs is not None:
            r.strokes += 1
        if fill_attrs is None:
            for d, segments in path.pieces():
                self._emit_stroke(d, segments, stroke_attrs)
            return
        attrs = fill_attrs + (" " + stroke_attrs if stroke_attrs else "")
        self._emit(f'<path d="{path.text()}" {attrs}/>')

    def _fill_d(self, d: str, colour: int) -> None:
        """Fill path data with a plain colour (text backgrounds, BLACKNESS…)."""
        self.result.fills += 1
        if not _light(colour):
            self.result.dark_fills += 1
        self._emit(f'<path d="{d}" fill="{_colour(colour)}"/>')

    # ------------------------------------------------------------------ lines and shapes

    def _points(self, rec: bytes, at: int, count: int, size: int) -> np.ndarray:
        """`count` points (int32 or int16 pairs) at `at`, in logical units."""
        if count < 0 or at + count * 2 * size > len(rec):
            raise ValueError("points beyond the record")
        dtype = "<i4" if size == 4 else "<i2"
        return np.frombuffer(rec, dtype, count=count * 2, offset=at).reshape(count, 2)

    def _poly(self, rec: bytes, size: int, kind: str) -> None:
        count = struct.unpack_from("<I", rec, 24)[0]
        logical = self._points(rec, 28, count, size)
        if not count:
            return
        pts = self.pts(logical)
        if kind in ("polylineto", "bezierto"):
            start = self.pt(*self.st.cur)
            self.st.cur = (float(logical[-1, 0]), float(logical[-1, 1]))
            if self.path_open:
                if self.path.cur is None:
                    self.path.move(*start)
                (self.path.lines if kind == "polylineto" else self.path.curves)(pts)
                self.path_figures = True
                return
            p = _PathData()
            p.move(*start)
            (p.lines if kind == "polylineto" else p.curves)(pts)
            self._shape(p, fill=False, stroke=True, closed=False)
            return
        target = self.path if self.path_open else _PathData()
        target.move(*pts[0])
        if kind == "bezier":
            target.curves(pts[1:])
        else:
            target.lines(pts[1:])
        if kind == "polygon":
            target.close()
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=kind == "polygon", stroke=True,
                    closed=kind == "polygon")

    def _polypoly(self, rec: bytes, size: int, polygon: bool) -> None:
        n, total = struct.unpack_from("<II", rec, 24)
        if n > total or 32 + 4 * n > len(rec):
            raise ValueError("bad counts")
        counts = struct.unpack_from(f"<{n}I", rec, 32)
        if sum(counts) > total:
            raise ValueError("bad counts")
        logical = self._points(rec, 32 + 4 * n, total, size)
        pts = self.pts(logical)
        target = self.path if self.path_open else _PathData()
        at = 0
        for c in counts:
            if c:
                target.move(*pts[at])
                target.lines(pts[at + 1: at + c])
                if polygon:
                    target.close()
            at += c
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=polygon, stroke=True, closed=polygon)

    def _polydraw(self, rec: bytes, size: int) -> None:
        count = struct.unpack_from("<I", rec, 24)[0]
        logical = self._points(rec, 28, count, size)
        kinds = rec[28 + count * 2 * size: 28 + count * 2 * size + count]
        if len(kinds) < count:
            raise ValueError("types beyond the record")
        pts = self.pts(logical)
        target = self.path if self.path_open else _PathData()
        if target.cur is None:
            target.move(*self.pt(*self.st.cur))
        i = 0
        while i < count:
            t = kinds[i]
            base = t & 0x06
            if base == 0x06:     # PT_MOVETO
                target.move(*pts[i])
                i += 1
            elif base == 0x02:   # PT_LINETO
                target.lines(pts[i:i + 1])
                i += 1
            elif base == 0x04 and i + 2 < count:   # PT_BEZIERTO (three points)
                target.curves(pts[i:i + 3])
                t = kinds[i + 2]
                i += 3
            else:
                i += 1
                continue
            if t & 0x01:         # PT_CLOSEFIGURE
                target.close()
        if count:
            self.st.cur = (float(logical[-1, 0]), float(logical[-1, 1]))
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=False, stroke=True, closed=False)

    def _line_to(self, rec: bytes) -> None:
        x, y = struct.unpack_from("<ii", rec, 8)
        start = self.pt(*self.st.cur)
        end = np.array([self.pt(x, y)])
        self.st.cur = (float(x), float(y))
        if self.path_open:
            if self.path.cur is None:
                self.path.move(*start)
            self.path.lines(end)
            self.path_figures = True
            return
        p = _PathData()
        p.move(*start)
        p.lines(end)
        if len(p.parts) == 1:   # (a line of no length)
            return
        self._shape(p, fill=False, stroke=True, closed=False)

    def _set_pixel(self, rec: bytes) -> None:
        x, y, color = struct.unpack_from("<iiI", rec, 8)
        px, py = self.pt(x, y)
        w, h = self.px_units
        self._fill_d(f"M{round(px)} {round(py)}h{round(w) or 1}v{round(h) or 1}h{-(round(w) or 1)}z",
                     color)

    def _box(self, rec: bytes, at: int = 8) -> tuple[float, float, float, float]:
        l, t, r, b = struct.unpack_from("<4i", rec, at)
        return float(l), float(t), float(r), float(b)

    def _rectangle(self, rec: bytes) -> None:
        l, t, r, b = self._box(rec)
        corners = self._logical_rect_corners(l, t, r, b)
        target = self.path if self.path_open else _PathData()
        target.move(*corners[0])
        target.lines(corners[1:])
        target.close()
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=True, stroke=True)

    def _ellipse_curves(self, l, t, r, b) -> np.ndarray:
        """Four Béziers for the ellipse in the box (logical), starting at the right."""
        cx, cy, rx, ry = (l + r) / 2, (t + b) / 2, (r - l) / 2, (b - t) / 2
        k = KAPPA
        pts = [(cx + rx, cy),
               (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry),
               (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy),
               (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry),
               (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy)]
        return self.pts(np.array(pts, np.float64))

    def _ellipse(self, rec: bytes) -> None:
        pts = self._ellipse_curves(*self._box(rec))
        target = self.path if self.path_open else _PathData()
        target.move(*pts[0])
        target.curves(pts[1:])
        target.close()
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=True, stroke=True)

    def _round_rect(self, rec: bytes) -> None:
        l, t, r, b = self._box(rec)
        cw, ch = struct.unpack_from("<ii", rec, 24)
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        rx, ry = min(abs(cw) / 2, (r - l) / 2), min(abs(ch) / 2, (b - t) / 2)
        k = KAPPA
        pts = [(l + rx, t), (r - rx, t),
               (r - rx + k * rx, t), (r, t + ry - k * ry), (r, t + ry),
               (r, b - ry),
               (r, b - ry + k * ry), (r - rx + k * rx, b), (r - rx, b),
               (l + rx, b),
               (l + rx - k * rx, b), (l, b - ry + k * ry), (l, b - ry),
               (l, t + ry),
               (l, t + ry - k * ry), (l + rx - k * rx, t), (l + rx, t)]
        p = self.pts(np.array(pts, np.float64))
        target = self.path if self.path_open else _PathData()
        target.move(*p[0])
        target.lines(p[1:2])
        target.curves(p[2:5])
        target.lines(p[5:6])
        target.curves(p[6:9])
        target.lines(p[9:10])
        target.curves(p[10:13])
        target.lines(p[13:14])
        target.curves(p[14:17])
        target.close()
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=True, stroke=True)

    def _arc_points(self, cx, cy, rx, ry, a0, sweep) -> np.ndarray:
        """Béziers (in logical units) along an ellipse from angle a0 (radians) through
        `sweep`; angles grow anti-clockwise as seen with the y axis pointing down. A sweep of
        more than one turn goes over the same ellipse again, so it is drawn as one turn
        plus the rest (ending at the same angle); at most 8 curves, whatever the record
        says (damaged data could ask for 10³⁰ turns)."""
        if not all(math.isfinite(v) for v in (cx, cy, rx, ry, a0, sweep)):
            raise ValueError("arc with an unusable size or angle")
        turn = 2 * math.pi
        if abs(sweep) > turn:
            sweep = math.copysign(turn + math.fmod(abs(sweep), turn), sweep)
        n = max(1, math.ceil(abs(sweep) / (math.pi / 2) - 1e-9))
        step = sweep / n
        k = 4 / 3 * math.tan(step / 4)
        out = [(cx + rx * math.cos(a0), cy - ry * math.sin(a0))]
        a = a0
        for _ in range(n):
            b = a + step
            ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
            out += [(cx + rx * (ca - k * sa), cy - ry * (sa + k * ca)),
                    (cx + rx * (cb + k * sb), cy - ry * (sb - k * cb)),
                    (cx + rx * cb, cy - ry * sb)]
            a = b
        return np.array(out, np.float64)

    def _visual_sign(self) -> float:
        """+1 when the logical y axis points down on paper (as in SVG), −1 when up."""
        a, b, c, d, _e, _f = self.m
        return 1.0 if a * d - b * c > 0 else -1.0

    def _arc(self, rec: bytes, kind: str) -> None:
        l, t, r, b = self._box(rec)
        sx_, sy_, ex_, ey_ = (float(v) for v in struct.unpack_from("<4i", rec, 24))
        cx, cy, rx, ry = (l + r) / 2, (t + b) / 2, abs(r - l) / 2, abs(b - t) / 2
        if not rx or not ry:
            return
        flip = self._visual_sign()
        a0 = math.atan2(-(sy_ - cy) / ry, (sx_ - cx) / rx)
        a1 = math.atan2(-(ey_ - cy) / ry, (ex_ - cx) / rx)
        ccw = self.st.arc_dir == AD_COUNTERCLOCKWISE
        if flip < 0:
            ccw = not ccw    # (angles are measured with y pointing down)
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        if sweep == 0:
            sweep = 2 * math.pi if ccw else -2 * math.pi
        pts = self.pts(self._arc_points(cx, cy, rx, ry, a0, sweep))
        target = self.path if self.path_open else _PathData()
        if kind == "arcto":
            start = self.pt(*self.st.cur)
            if target.cur is None:
                target.move(*start)
            target.lines(pts[:1])
            last = pts[-1]
            a, bb, c, d, e, f = self.m
            det = a * d - bb * c
            if det:
                x, y = last[0] - e, last[1] - f
                self.st.cur = ((d * x - c * y) / det, (-bb * x + a * y) / det)
        else:
            target.move(*pts[0])
        target.curves(pts[1:])
        if kind == "chord":
            target.close()
        elif kind == "pie":
            target.lines(self.pts(np.array([[cx, cy]], np.float64)))
            target.close()
        if self.path_open:
            self.path_figures = True
            return
        closed = kind in ("chord", "pie")
        self._shape(target, fill=closed, stroke=True, closed=closed)

    def _angle_arc(self, rec: bytes) -> None:
        x, y, radius = struct.unpack_from("<iiI", rec, 8)
        start, sweep = struct.unpack_from("<ff", rec, 20)
        flip = self._visual_sign()
        a0 = math.radians(start) * flip
        pts = self.pts(self._arc_points(x, y, radius, radius, a0, math.radians(sweep) * flip))
        target = self.path if self.path_open else _PathData()
        if target.cur is None or not self.path_open:
            target.move(*self.pt(*self.st.cur))
        target.lines(pts[:1])
        target.curves(pts[1:])
        a, b, c, d, e, f = self.m
        det = a * d - b * c
        if det:
            px, py = pts[-1][0] - e, pts[-1][1] - f
            self.st.cur = ((d * px - c * py) / det, (-b * px + a * py) / det)
        if self.path_open:
            self.path_figures = True
            return
        self._shape(target, fill=False, stroke=True, closed=False)

    # ------------------------------------------------------------------ paths

    def _begin_path(self, rec: bytes) -> None:
        self.path = _PathData()
        self.path_open = True
        self.path_figures = False

    def _end_path(self, rec: bytes) -> None:
        self.path_open = False

    def _close_figure(self, rec: bytes) -> None:
        if self.path is not None and self.path_open:
            self.path.close()

    def _abort_path(self, rec: bytes) -> None:
        self.path, self.path_open, self.path_figures = None, False, False

    def _take_path(self) -> _PathData | None:
        path = self.path
        self.path, self.path_open, self.path_figures = None, False, False
        return path

    def _draw_path(self, fill: bool, stroke: bool) -> None:
        self._shape(self._take_path(), fill=fill, stroke=stroke, closed=True)

    def _widen_path(self, rec: bytes) -> None:
        # The path becomes the outline of the pen along it, so filling it later draws the
        # line. Not converted: the drawing falls back to a picture.
        self.result.unsupported["WIDENPATH"] += 1

    def _select_clip_path(self, rec: bytes) -> None:
        mode = struct.unpack_from("<I", rec, 8)[0]
        path = self._take_path()
        if not path:
            return
        d = path.text()
        rule = ' clip-rule="evenodd"' if self.st.fill_mode == ALTERNATE else ""
        if mode == RGN_COPY:
            self._reset_clip()
            mode = RGN_AND
        if mode == RGN_AND:
            self._add_clip_shape(f'<path d="{d}"{rule}/>')
        elif mode == RGN_DIFF:
            self._exclude(d)
        else:
            self._apply_region(None, mode, d)

    # ------------------------------------------------------------------ regions

    def _fill_rgn(self, rec: bytes) -> None:
        size, handle = struct.unpack_from("<II", rec, 24)
        rects = self._region_rects(rec, 32, size) or []
        obj = STOCK_OBJECTS.get(handle & ~STOCK_BIT) if handle & STOCK_BIT else \
            self.objects.get(handle)
        brush = obj if isinstance(obj, Brush) else self.st.brush
        self._region_fill(rects, brush)

    def _paint_rgn(self, rec: bytes) -> None:
        size = struct.unpack_from("<I", rec, 24)[0]
        self._region_fill(self._region_rects(rec, 28, size) or [], self.st.brush)

    def _region_fill(self, rects: list[tuple], brush: Brush) -> None:
        if not rects:
            return
        fill = self._fill_attrs(brush, rule=False)
        if fill is None:
            return
        self.result.fills += 1
        if not (brush.style == BS_SOLID and _light(brush.color)):
            self.result.dark_fills += 1
        self._emit(f'<path d="{self._rects_d(rects)}" {fill}/>')

    # ------------------------------------------------------------------ comments

    def _comment(self, rec: bytes) -> None:
        size = struct.unpack_from("<I", rec, 8)[0]
        if size >= 4 and rec[12:16] == b"EMF+":
            self.result.emf_plus = True
        self.result.ignored["GDICOMMENT"] += 1

    # ------------------------------------------------------------------ pictures

    def _bitmap(self, rec: bytes, off_bmi: int, cb_bmi: int, off_bits: int, cb_bits: int,
                alpha: bool = False) -> Bitmap | None:
        if not cb_bmi or off_bmi + cb_bmi > len(rec) or off_bits + cb_bits > len(rec):
            return None
        return decode_dib(rec[off_bmi: off_bmi + cb_bmi], rec[off_bits: off_bits + cb_bits],
                          alpha)

    @staticmethod
    def _crop(bm: Bitmap, x: int, y: int, w: int, h: int, from_bottom: bool) -> Bitmap:
        """The part of a picture that is drawn (x, y from the top-left corner; for
        StretchDIBits of a bottom-up DIB, y from the bottom). Negative sizes mirror it."""
        if bm.pixels is None:
            return bm
        flip_x, flip_y = w < 0, h < 0
        w, h = abs(w), abs(h)
        if from_bottom:
            y = bm.height - y - h
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(bm.width, x + w), min(bm.height, y + h)
        if x1 <= x0 or y1 <= y0:
            return bm
        pixels = bm.pixels
        if (x0, y0, x1, y1) != (0, 0, bm.width, bm.height):
            pixels = pixels[y0:y1, x0:x1]
        if flip_x:
            pixels = pixels[:, ::-1]
        if flip_y:
            pixels = pixels[::-1]
        return Bitmap(pixels.shape[1], pixels.shape[0], np.ascontiguousarray(pixels))

    def _place(self, bm: Bitmap, x: float, y: float, cx: float, cy: float,
               opacity: float = 1.0, device_size: bool = False) -> None:
        """Draw a picture into the logical rectangle (x, y, cx, cy) (`device_size`: cx, cy
        are reference-device pixels)."""
        p0 = self.pt(x, y)
        if device_size:
            p1 = (p0[0] + cx * self.px_units[0], p0[1])
            p3 = (p0[0], p0[1] + cy * self.px_units[1])
        else:
            p1, p3 = self.pt(x + cx, y), self.pt(x, y + cy)
        w, h = bm.width, bm.height
        m = ((p1[0] - p0[0]) / w, (p1[1] - p0[1]) / w, (p3[0] - p0[0]) / h,
             (p3[1] - p0[1]) / h, p0[0], p0[1])
        transform = _matrix_attr(m, 6)   # (raises for damaged data: nothing is placed)
        self.result.images.append(PlacedBitmap(bm, p0, p1, p3, opacity))
        op = f' opacity="{_n(opacity, 3)}"' if opacity < 1 else ""
        self._emit(f'<image width="{w}" height="{h}" preserveAspectRatio="none" '
                   f'transform="{transform}"{op} xlink:href="{bm.data_uri()}"/>')

    def _blt(self, rec: bytes, stretch: bool, blend: bool = False) -> None:
        x, y, cx, cy, rop, xs, ys = struct.unpack_from("<4iIii", rec, 24)
        _bk, _usage, off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<6I", rec, 76)
        cxs, cys = struct.unpack_from("<ii", rec, 100) if stretch else (cx, cy)
        name = "ALPHABLEND" if blend else "STRETCHBLT" if stretch else "BITBLT"
        if rop == DSTCOPY and not blend:   # leaves everything as it is (EMF+ writes these)
            self.result.ignored[f"{name} (no change)"] += 1
            return
        if not cb_bmi:   # no picture: a fill with the brush, or black or white
            corners = self._logical_rect_corners(x, y, x + cx, y + cy)
            d = self._polygon_d(corners)
            if rop == PATCOPY:
                fill = self._fill_attrs(rule=False)
                if fill is not None:
                    self.result.fills += 1
                    brush = self.st.brush
                    if not (brush.style == BS_SOLID and _light(brush.color)):
                        self.result.dark_fills += 1
                    self._emit(f'<path d="{d}" {fill}/>')
            elif rop in (BLACKNESS, WHITENESS):
                self._fill_d(d, 0 if rop == BLACKNESS else 0xFFFFFF)
            else:
                self.result.unsupported[f"{name} without a picture"] += 1
            return
        opacity = 1.0
        alpha = False
        if blend:
            _op, _flags, constant, fmt = struct.unpack_from("<4B", rec, 40)
            opacity = constant / 255
            alpha = bool(fmt & 1)
        elif rop not in (SRCCOPY, NOTSRCCOPY):
            self.result.unsupported[f"{name} (raster operation {rop:#x})"] += 1
            return
        bm = self._bitmap(rec, off_bmi, cb_bmi, off_bits, cb_bits, alpha)
        if bm is None:
            self.result.unsupported[f"{name} (unreadable picture)"] += 1
            return
        bm = self._crop(bm, xs, ys, cxs, cys, from_bottom=False)
        if alpha and bm.pixels is not None and bm.pixels.shape[2] == 4:
            bm = _unpremultiply(bm)
        if rop == NOTSRCCOPY and not blend and bm.pixels is not None:
            bm = Bitmap(bm.width, bm.height, 255 - bm.pixels)
        self._place(bm, x, y, cx, cy, opacity)

    def _stretch_dibits(self, rec: bytes) -> None:
        x, y, xs, ys, cxs, cys = struct.unpack_from("<6i", rec, 24)
        off_bmi, cb_bmi, off_bits, cb_bits, _usage, rop = struct.unpack_from("<6I", rec, 48)
        cx, cy = struct.unpack_from("<ii", rec, 72)
        if rop not in (SRCCOPY, NOTSRCCOPY):
            self.result.unsupported[f"STRETCHDIBITS (raster operation {rop:#x})"] += 1
            return
        bm = self._bitmap(rec, off_bmi, cb_bmi, off_bits, cb_bits)
        if bm is None:
            self.result.unsupported["STRETCHDIBITS (unreadable picture)"] += 1
            return
        bottom_up = struct.unpack_from("<i", rec, off_bmi + 8)[0] > 0 if cb_bmi >= 12 else True
        bm = self._crop(bm, xs, ys, cxs, cys, from_bottom=bottom_up)
        if rop == NOTSRCCOPY and bm.pixels is not None:
            bm = Bitmap(bm.width, bm.height, 255 - bm.pixels)
        self._place(bm, x, y, cx, cy)

    def _set_dibits(self, rec: bytes) -> None:
        x, y, xs, ys, cxs, cys = struct.unpack_from("<6i", rec, 24)
        off_bmi, cb_bmi, off_bits, cb_bits, _usage, start, scans = \
            struct.unpack_from("<7I", rec, 48)
        bm = self._bitmap(rec, off_bmi, cb_bmi, off_bits, cb_bits)
        if bm is None or start != 0 or scans < bm.height:
            self.result.unsupported["SETDIBITSTODEVICE"] += 1
            return
        bottom_up = struct.unpack_from("<i", rec, off_bmi + 8)[0] > 0 if cb_bmi >= 12 else True
        bm = self._crop(bm, xs, ys, cxs, cys, from_bottom=bottom_up)
        self._place(bm, x, y, cxs, cys, device_size=True)

    # ------------------------------------------------------------------ text

    def _metrics(self, font: Font) -> tuple[float, float]:
        found = None
        if self.helper is not None:
            try:
                found = self.helper.metrics(font)
            except Exception:  # noqa: BLE001 — a helper problem must not stop the conversion
                found = None
        return found or DEFAULT_METRICS[font.kind()]

    def _text(self, rec: bytes, wide: bool) -> None:
        st = self.st
        mode = struct.unpack_from("<I", rec, 24)[0]
        rx, ry, n, off_string, options = struct.unpack_from("<iiIII", rec, 36)
        rcl = struct.unpack_from("<4i", rec, 56)
        off_dx = struct.unpack_from("<I", rec, 72)[0]
        if options & ETO_OPAQUE and rcl[2] > rcl[0] and rcl[3] > rcl[1]:
            corners = self._logical_rect_corners(*rcl)
            self._fill_d(self._polygon_d(corners), st.bk_color)
        if not n:
            return
        unit = 2 if wide else 1
        if off_string + n * unit > len(rec):
            raise ValueError("text beyond the record")
        raw = rec[off_string: off_string + n * unit]
        dx: list[float] = []
        if off_dx:
            per = 2 if options & ETO_PDY else 1
            if off_dx + n * per * 4 <= len(rec):
                dx = [float(v) for v in struct.unpack_from(f"<{n * per}i", rec, off_dx)[::per]]
        font = st.font
        ascent, descent = self._metrics(font)
        if font.height < 0:
            em = float(-font.height)
        elif font.height > 0:
            em = font.height / max(0.5, ascent + descent)
        else:
            em = 12.0 * self.header.device_px[1] / self.header.device_mm[1] * 25.4 / 72
        glyphs: list[int] | None = None
        if wide and options & ETO_GLYPH_INDEX:
            glyphs = list(struct.unpack_from(f"<{n}H", raw, 0))
            chars = None
            if self.helper is not None:
                try:
                    chars = self.helper.glyph_text(font, glyphs)
                except Exception:  # noqa: BLE001
                    chars = None
            if chars is not None and len(chars) == n:
                units = list(chars)
                glyphs = None
            else:
                units = ["�"] * n
        elif wide:
            units = [chr(v) for v in struct.unpack_from(f"<{n}H", raw, 0)]
        else:
            codec = CHARSET_CODEPAGES.get(font.charset, "cp1252")
            try:
                units = list(bytes(raw).decode(codec, "replace"))
            except LookupError:
                units = list(bytes(raw).decode("cp1252", "replace"))
            if len(units) != n:   # (a double-byte code page: one advance per character)
                units = units[:n] + [""] * (n - len(units))
        if not dx:
            dx = [0.55 * em] * n
        dx = (dx + [0.0] * n)[:n]
        # Characters with their advances (two UTF-16 halves make one character).
        chars_out: list[str] = []
        advances: list[float] = []
        i = 0
        while i < n:
            c = units[i]
            adv = dx[i]
            if (wide and glyphs is None and "\ud800" <= c <= "\udbff" and i + 1 < n
                    and "\udc00" <= units[i + 1] <= "\udfff"):
                c = (c + units[i + 1]).encode("utf-16-le", "surrogatepass").decode("utf-16-le")
                adv += dx[i + 1]
                i += 1
            chars_out.append(c)
            advances.append(adv)
            i += 1
        if font.charset == SYMBOL_CHARSET or font.face.lower() == "symbol":
            chars_out = [SYMBOL_MAP.get(ord(c) - 0xF000 if len(c) == 1 and ord(c) >= 0xF000
                                        else ord(c) if len(c) == 1 else -1, c)
                         if c else c for c in chars_out]
        total = sum(advances)
        # Where the text starts along its baseline, and where the baseline is.
        align = st.text_align
        if align & TA_UPDATECP:
            rx, ry = st.cur
            st.cur = (st.cur[0] + total, st.cur[1])
        horiz = align & TA_CENTER
        x0 = -total if horiz == TA_RIGHT else -total / 2 if horiz == TA_CENTER else 0.0
        vert = align & TA_BASELINE
        baseline = 0.0 if vert == TA_BASELINE else -descent * em if vert == TA_BOTTOM else \
            ascent * em
        transform = self._text_transform(rx, ry, font, mode)
        matrix = _matrix_attr(transform)   # (raises for damaged data before anything is drawn)
        if st.bk_mode == OPAQUE and not options & ETO_OPAQUE and total:
            box = np.array([[x0, baseline - ascent * em], [x0 + total, baseline - ascent * em],
                            [x0 + total, baseline + descent * em], [x0, baseline + descent * em]])
            a, b, c, d, e, f = transform
            corners = np.column_stack((a * box[:, 0] + c * box[:, 1] + e,
                                       b * box[:, 0] + d * box[:, 1] + f))
            self._fill_d(self._polygon_d(corners), st.bk_color)
        xs: list[float] = []
        x = x0
        for adv in advances:
            xs.append(x)
            x += adv
        clip = None
        if options & ETO_CLIPPED:
            saved = (st.clip_rect, st.clip_shapes)
            corners = self._logical_rect_corners(*rcl)
            rect = self._poly_or_rect(corners)
            if rect is not None:
                self._intersect_rect(rect)
            else:
                self._add_clip_shape(f'<path d="{self._polygon_d(corners)}"/>')
            clip = self._clip_key()
            st.clip_rect, st.clip_shapes = saved
        colour = _colour(st.text_color)
        nd = 2 if em >= 10 else 3 if em >= 1 else 4
        if glyphs is not None:   # glyph numbers that could not be read back as text
            outline = None
            if self.helper is not None:
                try:
                    outline = self.helper.glyph_outline(font, glyphs, xs, em)
                except Exception:  # noqa: BLE001
                    outline = None
            if outline is None:
                self.result.unsupported["EXTTEXTOUTW (glyph numbers)"] += 1
                return
            self.result.texts += 1
            if outline:
                self.result.approximated["text as outlines"] += 1
                self._emit(f'<path transform="{matrix}" d="{outline}" '
                           f'fill="{colour}"/>', clip)
            return
        # (Characters XML can't hold — control characters, a lone half of a UTF-16 pair,
        # U+FFFE/U+FFFF, as damaged data may have — are left out; the self-check then
        # decides whether what is left is close enough.)
        keep = [(c, xv) for c, xv in zip(chars_out, xs)
                if c and c >= " " and c != "�" and all(_xml_char(ch) for ch in c)]
        if not any(c.strip() for c, _x in keep):
            return
        self.result.texts += 1
        text = "".join(c for c, _x in keep)
        x_list = " ".join(_n(xv, nd) for _c, xv in keep)
        attrs = [f'transform="{matrix}"', f'x="{x_list}"',
                 f'y="{_n(baseline, nd)}"', f'font-family="{_esc(font.family_attr())}"',
                 f'font-size="{_n(em, nd)}"']
        if font.weight >= 600:
            attrs.append('font-weight="bold"')
        elif font.weight and font.weight not in (400, 500) and font.weight < 400:
            attrs.append(f'font-weight="{font.weight}"')
        if font.italic:
            attrs.append('font-style="italic"')
        decoration = " ".join(d for d, on in (("underline", font.underline),
                                              ("line-through", font.strikeout)) if on)
        if decoration:
            attrs.append(f'text-decoration="{decoration}"')
        attrs.append(f'fill="{colour}"')
        self._emit(f'<text xml:space="preserve" {" ".join(attrs)}>{_esc(text)}</text>', clip)

    def _text_transform(self, rx: float, ry: float, font: Font, mode: int):
        """Text's own coordinates (logical units along the baseline, y down) → SVG units."""
        px, py = self.pt(rx, ry)
        theta = math.radians(font.escapement / 10)
        a, b, c, d, _e, _f = self.m
        if mode == GM_ADVANCED:
            rot = (math.cos(-theta), math.sin(-theta), -math.sin(-theta), math.cos(-theta), 0, 0)
            lin = _compose((a, b, c, d, 0.0, 0.0), rot)
            return (lin[0], lin[1], lin[2], lin[3], px, py)
        sx, sy = math.hypot(a, b), math.hypot(c, d)
        phi = -theta
        return (math.cos(phi) * sx, math.sin(phi) * sx, -math.sin(phi) * sy,
                math.cos(phi) * sy, px, py)

    # ------------------------------------------------------------------ the SVG file

    def _svg(self) -> str:
        r = self.result
        width_units = round(r.width_mm * 100)
        height_units = round(r.height_mm * 100)
        w_pt, h_pt = r.width_mm / 25.4 * 72, r.height_mm / 25.4 * 72
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" '
                 f'width="{_n(w_pt, 3)}pt" height="{_n(h_pt, 3)}pt" '
                 f'viewBox="0 0 {width_units} {height_units}">',
                 "<!-- Converted from an Enhanced Metafile by Chem SI Assistant "
                 "(1 unit = 0.01 mm) -->"]
        # Each clip state (a rectangle and further shapes, all intersected) is one
        # <clipPath>, so every element sits in at most one clipping group. Several shapes
        # are intersected by chaining: <clipPath id="k2" clip-path="url(#k1)"> holds the
        # last shape and refers to the clip of the others, each chain defined once (states
        # that share their first shapes share those links). The chain is at most
        # MAX_CLIP_SHAPES + 1 long.
        clip_of: dict[tuple, str | None] = {}
        rect_ids: dict[tuple, str] = {}
        chains: dict[tuple, str] = {}
        chain_defs: list[str] = []

        def chain(ids: tuple) -> str:
            if len(ids) == 1:
                return ids[0]
            found = chains.get(ids)
            if found is None:
                outer = chain(ids[:-1])
                found = chains[ids] = f"k{len(chains) + 1}"
                chain_defs.append(f'<clipPath id="{found}" clip-path="url(#{outer})">'
                                  f'{self.clip_shapes[ids[-1]]}</clipPath>')
            return found

        for key, _element in self.out:
            if key in clip_of:
                continue
            rect, shapes = key
            ids = []
            if rect is not None:
                if rect not in rect_ids:
                    x0, y0, x1, y1 = rect
                    rect_ids[rect] = self._shape_id(
                        f'<rect x="{_n(x0, 1)}" y="{_n(y0, 1)}" width="{_n(x1 - x0, 1)}" '
                        f'height="{_n(y1 - y0, 1)}"/>')
                ids.append(rect_ids[rect])
            ids += list(shapes)
            clip_of[key] = chain(tuple(ids)) if ids else None
        used = {cid for cid in clip_of.values() if cid} | {
            ids[0] for ids in chains}
        defs = [f'<clipPath id="{cid}">{body}</clipPath>'
                for cid, body in self.clip_shapes.items() if cid in used]
        defs += chain_defs
        defs += self.pattern_defs
        if defs:
            lines.append("<defs>" + "".join(defs) + "</defs>")
        lines.append('<g stroke-linecap="round" stroke-linejoin="round">')
        open_id: str | None = None
        for key, element in self.out:
            cid = clip_of[key]
            if cid != open_id:
                if open_id is not None:
                    lines.append("</g>")
                if cid is not None:
                    lines.append(f'<g clip-path="url(#{cid})">')
                open_id = cid
            lines.append(element)
        if open_id is not None:
            lines.append("</g>")
        lines.append("</g>")
        lines.append("</svg>")
        return "\n".join(lines) + "\n"


def _light(colorref: int) -> bool:
    """Whether a colour is white or nearly so (a background rather than a drawing)."""
    r, g, b = colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF
    return min(r, g, b) >= 0xF0


def _cap(style: int) -> str:
    return {0x100: "square", 0x200: "butt"}.get(style & 0xF00, "round")


def _join(style: int) -> str:
    return {0x1000: "bevel", 0x2000: "miter"}.get(style & 0xF000, "round")


def _unpremultiply(bm: Bitmap) -> Bitmap:
    """AlphaBlend pictures store colours multiplied by their transparency."""
    px = bm.pixels.astype(np.float32)
    alpha = px[:, :, 3:4]
    with np.errstate(divide="ignore", invalid="ignore"):
        rgb = np.where(alpha > 0, np.minimum(255, px[:, :, :3] * 255 / np.maximum(alpha, 1)), 0)
    out = np.concatenate((rgb, alpha), axis=2).astype(np.uint8)
    return Bitmap(bm.width, bm.height, out)


def convert(data: bytes, helper: TextHelper | None = None) -> Conversion:
    """Convert an Enhanced Metafile to SVG. Never raises for bad data: a metafile that
    can't be read gives a Conversion with `error` set; records that could not be converted
    are counted in `unsupported`."""
    try:
        return _Converter(bytes(data), helper).run()
    except Exception as e:  # noqa: BLE001 — anything unexpected: report, don't crash
        return Conversion(error=f"The metafile could not be converted ({type(e).__name__}).")
