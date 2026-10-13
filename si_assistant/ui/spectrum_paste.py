"""Pasting spectra, e.g. from Mnova (SPEC §13l.1).

Mnova copies a spectrum as an Enhanced Metafile on Windows (a PDF on macOS). The metafile
is turned into a vector drawing (SVG, core/emf_svg.py) and every conversion is checked
against Windows' own rendering of the same metafile (`self_check`): both are drawn at
CHECK_DPI, and the "ink" (everything not white) of each must lie on, or within
CHECK_RADIUS pixels of, the ink of the other. Only a conversion that passes is kept as
SVG. Otherwise the spectrum is kept as a picture:

* Mnova's default copy, a "metafile containing a bitmap" (one picture filling the frame),
  is kept as that picture, at its own resolution (not enlarged), recording the resolution
  that makes it print at the copied size;
* anything else (a check that fails, drawing the converter can't do) is drawn by Windows
  at FALLBACK_DPI.

Mnova's original (the .emf, or the PDF itself) is always kept next to the stored file, so
it can be converted again later ("Re-render pasted pictures" in Project settings).

This module also holds the paste target on a compound's Spectra field, the one-click
spectrum type chooser, and the paste command shared with the compound's spectra page.
"""

from __future__ import annotations

import html
import struct
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF, QGuiApplication, QImage, QRawFont
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..compile.qt_images import render_svg_bytes
from ..core import emf_svg
from ..core import fields as F
from . import clipboard_image as ci
from .field_widgets import AttachmentsEditor
from .structure_widgets import HIGHLIGHT, PasteFrame, PasteTargetMixin, paste_key

# The self-check (tuned on test metafiles and on deliberately broken conversions: text
# moved by 0.5–1 mm or made 25 % larger, a curve, label or picture left out, the drawing
# 0.4 % or 1 % too large, colours swapped, clipping lost — good conversions score 1.000).
CHECK_DPI = 150          # both renderings are compared at this resolution
CHECK_RADIUS = 2         # pixels (0.34 mm at 150 dpi) of leeway each way
CHECK_COVERAGE = 0.99    # share of each rendering's ink that must lie on the other's…
TILE = 64                # …and in every tile of TILE × TILE pixels (about 11 mm)…
TILE_MIN_INK = 20        # …that has at least this much ink,
TILE_COVERAGE = 0.97     # this share
CHECK_COLOUR = 0.05      # at most this share of the inside of shapes may differ in colour
INK_LEVEL = 200          # a pixel is ink when its darkest channel is below this (0–255)
FALLBACK_DPI = 600       # a metafile that can't be kept as SVG is drawn at this resolution
LOW_DPI = 300            # a bitmap below this is mentioned as low resolution

VECTOR_MESSAGE = "Kept as a vector drawing (SVG)."
TIP = ("For sharper spectra, choose “Enhanced Windows Metafile” in Mnova's copy options "
       "and copy again.")


@dataclass
class PastedSpectrum:
    """A spectrum taken from the clipboard (or converted again from its original)."""
    data: bytes
    ext: str                       # ".svg", ".png" or ".pdf"
    original: bytes | None = None  # Mnova's copy (EMF bytes), kept next to the file
    original_ext: str = ""
    kind: str = ""                 # "vector", "bitmap", "picture", "pdf", "image"
    message: str = ""              # one plain line for the user
    details: str = ""              # technical details (the conversion's summary)


@dataclass
class Comparison:
    """How two renderings of one drawing agree (see compare)."""
    coverage: tuple[float, float]     # (the reference's ink near the candidate's, reverse)
    worst_tile: tuple[float, float]   # the same in the worst tile
    colour: float                     # share of the inside of shapes whose colour differs
    ink: tuple[int, int]              # ink pixels of each

    def shapes_agree(self) -> bool:
        return min(self.coverage) >= CHECK_COVERAGE and min(self.worst_tile) >= TILE_COVERAGE

    def passed(self) -> bool:
        return self.shapes_agree() and self.colour <= CHECK_COLOUR


@dataclass
class CheckResult:
    passed: bool
    comparison: Comparison | None = None
    reason: str = ""
    seconds: float = 0.0
    size: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------- fonts for the converter

class QtTextHelper:
    """What the converter needs to know about fonts, from Qt: their ascent and descent,
    and the characters (or outlines) of text written as glyph numbers."""

    _CANDIDATES = "".join(chr(c) for r in ((0x20, 0x7F), (0xA0, 0x180), (0x370, 0x400),
                                             (0x2010, 0x2070), (0x2070, 0x20A0),
                                             (0x2100, 0x2150), (0x2190, 0x2200),
                                             (0x2200, 0x2300)) for c in range(*r))

    def __init__(self):
        self._metrics: dict[tuple, tuple[float, float] | None] = {}
        self._reverse: dict[tuple, dict[int, str]] = {}
        self._raw: dict[tuple, QRawFont] = {}

    @staticmethod
    def _qfont(font: emf_svg.Font, pixels: int = 1000) -> QFont:
        f = QFont(font.face or "Arial")
        f.setPixelSize(pixels)
        f.setBold(font.weight >= 600)
        f.setItalic(font.italic)
        return f

    @staticmethod
    def _found(font: emf_svg.Font, family: str) -> bool:
        """Whether Qt has the font itself (not a stand-in it chose)."""
        return family.strip().lower() == (font.face or "").strip().lower()

    def metrics(self, font: emf_svg.Font) -> tuple[float, float] | None:
        """The font's ascent and descent, if Qt has the font (else the converter's own
        typical values are used)."""
        key = (font.face, font.weight >= 600, font.italic)
        if key not in self._metrics:
            qfont = self._qfont(font)
            found = None
            if self._found(font, QFontInfo(qfont).family()):
                fm = QFontMetricsF(qfont)
                asc, desc = fm.ascent() / 1000, fm.descent() / 1000
                found = (asc, desc) if asc > 0.3 and desc >= 0 else None
            self._metrics[key] = found
        return self._metrics[key]

    def _raw_font(self, font: emf_svg.Font) -> QRawFont:
        """The font's glyphs — only the font itself: glyph numbers mean nothing in a
        stand-in (an invalid QRawFont then)."""
        key = (font.face, font.weight >= 600, font.italic)
        raw = self._raw.get(key)
        if raw is None:
            qfont = self._qfont(font)
            # (QFontInfo asks safely; a QRawFont of a font Qt lacks can crash when asked)
            raw = QRawFont.fromFont(qfont) if self._found(font, QFontInfo(qfont).family()) \
                else QRawFont()
            self._raw[key] = raw
        return raw

    def glyph_text(self, font: emf_svg.Font, glyphs: list[int]) -> str | None:
        key = (font.face, font.weight >= 600, font.italic)
        reverse = self._reverse.get(key)
        if reverse is None:
            raw = self._raw_font(font)
            reverse = {}
            if raw.isValid():
                indexes = raw.glyphIndexesForString(self._CANDIDATES)
                for ch, g in zip(self._CANDIDATES, indexes):
                    if g and g not in reverse:
                        reverse[g] = ch
            self._reverse[key] = reverse
        try:
            return "".join(reverse[g] for g in glyphs)
        except KeyError:
            return None

    def glyph_outline(self, font: emf_svg.Font, glyphs: list[int], xs: list[float],
                      em: float) -> str | None:
        raw = self._raw_font(font)
        if not raw.isValid():
            return None
        k = em / 1000
        nd = 2 if em >= 10 else 3
        parts = []
        for g, x0 in zip(glyphs, xs):
            path = raw.pathForGlyph(g)
            i, count = 0, path.elementCount()
            while i < count:
                e = path.elementAt(i)
                if e.type == e.type.MoveToElement:
                    parts.append(f"M{emf_svg._n(x0 + e.x * k, nd)} {emf_svg._n(e.y * k, nd)}")
                    i += 1
                elif e.type == e.type.LineToElement:
                    parts.append(f"L{emf_svg._n(x0 + e.x * k, nd)} {emf_svg._n(e.y * k, nd)}")
                    i += 1
                elif e.type == e.type.CurveToElement and i + 2 < count:
                    c2, end = path.elementAt(i + 1), path.elementAt(i + 2)
                    parts.append("C" + " ".join(
                        f"{emf_svg._n(x0 + p.x * k, nd)} {emf_svg._n(p.y * k, nd)}"
                        for p in (e, c2, end)))
                    i += 3
                else:
                    i += 1
        return "".join(parts)


# ---------------------------------------------------------------------- the self-check

def has_emf_plus(emf: bytes) -> bool:
    """Whether the metafile also holds EMF+ records: its first record after the header is
    a comment holding the EMF+ header (where the EMF+ format requires it)."""
    if len(emf) < 8:
        return False
    at = struct.unpack_from("<I", emf, 4)[0]
    return len(emf) >= at + 16 and struct.unpack_from("<I", emf, at)[0] == 70 \
        and emf[at + 12:at + 16] == b"EMF+"


def ink(image: QImage) -> tuple[np.ndarray, np.ndarray]:
    """(ink mask, RGB pixels) of a picture: ink = darkest channel below INK_LEVEL."""
    rgb = image.convertToFormat(QImage.Format.Format_RGB32)
    w, h = rgb.width(), rgb.height()
    arr = np.frombuffer(rgb.constBits(), np.uint8, count=rgb.sizeInBytes())
    arr = arr.reshape(h, rgb.bytesPerLine())[:, : w * 4].reshape(h, w, 4)[:, :, 2::-1]
    return arr.min(axis=2) < INK_LEVEL, arr.copy()


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """The mask grown by `radius` pixels each way (a square)."""
    out = mask.copy()
    for s in range(1, radius + 1):
        out[:, s:] |= mask[:, :-s]
        out[:, :-s] |= mask[:, s:]
    grown = out.copy()
    for s in range(1, radius + 1):
        grown[s:, :] |= out[:-s, :]
        grown[:-s, :] |= out[s:, :]
    return grown


def erode(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """The pixels of the mask whose neighbours (within `radius`) are all in it too."""
    return ~dilate(~mask, radius)


def smooth(pixels: np.ndarray) -> np.ndarray:
    """Each pixel averaged with its 8 neighbours (evens out ClearType's coloured edges)."""
    p = pixels.astype(np.float32)
    acc = p.copy()
    acc[:, 1:] += p[:, :-1]
    acc[:, :-1] += p[:, 1:]
    out = acc.copy()
    out[1:, :] += acc[:-1, :]
    out[:-1, :] += acc[1:, :]
    return out / 9


COLOUR_STEP = 60   # a channel differing by more than this is a different colour


def _tile_sums(mask: np.ndarray) -> np.ndarray:
    """The number of set pixels in each TILE × TILE tile."""
    h, w = mask.shape
    ph, pw = -(-h // TILE) * TILE, -(-w // TILE) * TILE
    padded = np.zeros((ph, pw), np.int32)
    padded[:h, :w] = mask
    return padded.reshape(ph // TILE, TILE, pw // TILE, TILE).sum(axis=(1, 3))


def _coverage(x: np.ndarray, y: np.ndarray, radius: int) -> tuple[float, float]:
    """(share of x's ink within `radius` pixels of y's ink, the same in x's worst tile)."""
    n = int(x.sum())
    if not n:
        return (1.0 if not y.any() else 0.0), 1.0
    covered = x & dilate(y, radius)
    total, near = _tile_sums(x), _tile_sums(covered)
    counted = total >= TILE_MIN_INK
    worst = float((near[counted] / total[counted]).min()) if counted.any() else 1.0
    return float(covered.sum()) / n, worst


def compare(reference: QImage, candidate: QImage, radius: int = CHECK_RADIUS) -> Comparison:
    """How well two renderings of the same drawing agree: how much of each one's ink lies
    within `radius` pixels of the other's, over the whole picture and in its worst tile,
    and how much of the inside of shapes drawn by both differs in colour. Colours are
    compared only inside shapes (ink with ink all round it in both) and after averaging
    each pixel with its neighbours, so the edges of lines and letters, which every
    renderer smooths differently (Windows also tints letters' edges for ClearType), don't
    count."""
    a, pa = ink(reference)
    b, pb = ink(candidate)
    na, nb = int(a.sum()), int(b.sum())
    if a.shape != b.shape:
        return Comparison((0.0, 0.0), (0.0, 0.0), 1.0, (na, nb))
    cov_a, tile_a = _coverage(a, b, radius)
    cov_b, tile_b = _coverage(b, a, radius)
    inside = erode(a) & erode(b)
    shared = int(inside.sum())
    mismatch = 0.0
    if shared:
        diff = np.abs(smooth(pa)[inside] - smooth(pb)[inside]).max(axis=1)
        mismatch = float((diff > COLOUR_STEP).sum()) / shared
    return Comparison((cov_a, cov_b), (tile_a, tile_b), mismatch, (na, nb))


def check_svg(emf: bytes, svg: str, dpi: float = CHECK_DPI) -> str:
    """The SVG as the self-check draws it. Windows draws hatched fills (lines 8 pixels
    apart) and the dashes of one-pixel pens in the pixels of the picture it draws on
    (render_emf_bytes draws at `dpi`, or lower for a very large picture, supersampled),
    while the stored SVG uses fixed sizes (emf_svg.HATCH_PIXEL, COSMETIC_PIXEL). So that
    the check compares like with like, its copy of the SVG has those at Windows' size for
    that rendering; the rest is unchanged, so they are still checked for place, colour
    and style."""
    if 'class="hatch' not in svg and 'class="cosmetic-dash"' not in svg:
        return svg
    header = emf_svg.read_header(emf)
    if header is None:
        return svg
    l, t, r, b = header.frame
    used, supersample = ci.fit_dpi((r - l) / 2540, (b - t) / 2540, dpi)
    return emf_svg.rescale_output_pixels(svg, 2540 / (used * supersample))


def self_check(emf: bytes, svg: str, dpi: float = CHECK_DPI) -> CheckResult:
    """Compare Windows' rendering of the metafile with Qt's rendering of the SVG made
    from it (Windows only: elsewhere there is nothing to compare with). Hatched fills are
    compared at Windows' spacing (check_svg)."""
    import time
    start = time.perf_counter()
    reference = ci.render_emf_bytes(emf, dpi)
    if reference is None or reference.isNull():
        return CheckResult(False, reason="Windows could not draw the metafile")
    size = (reference.width(), reference.height())
    candidate = render_svg_bytes(_svg_bytes(check_svg(emf, svg, dpi)), *size)
    if candidate.isNull():
        return CheckResult(False, reason="the SVG could not be drawn", size=size)
    result = compare(reference, candidate)
    if not result.ink[0]:
        return CheckResult(False, result, "the picture is empty",
                           time.perf_counter() - start, size)
    if not result.passed() and result.shapes_agree() and has_emf_plus(emf):
        # A metafile that also holds EMF+ records (Mnova's) is shown by Word and Mnova
        # with GDI+, which draws those. Plain GDI can draw it lighter: a pen thinner than
        # a pixel under a scaled transform is drawn one output pixel wide, grey once
        # averaged. The shapes were compared with GDI's drawing; the colours are compared
        # with GDI+'s instead.
        plus = ci.render_emf_plus_bytes(emf, dpi)
        if plus is not None and not plus.isNull() and (plus.width(), plus.height()) == size:
            result = replace(result, colour=compare(plus, candidate).colour)
    seconds = time.perf_counter() - start
    passed = result.passed()
    reason = "" if passed else (
        "the drawings differ (coverage {:.3f} / {:.3f}, worst tile {:.3f} / {:.3f}, "
        "colour {:.3f})".format(*result.coverage, *result.worst_tile, result.colour))
    return CheckResult(passed, result, reason, seconds, size)


# ---------------------------------------------------------------------- converting a metafile

def _svg_bytes(svg: str) -> bytes:
    """The SVG as UTF-8. (The converter leaves out characters XML can't hold; anything it
    missed is replaced rather than stopping the paste.)"""
    return svg.encode("utf-8", "replace")


def _bitmap_spectrum(conv: emf_svg.Conversion, emf: bytes) -> PastedSpectrum | None:
    """Mnova's "metafile containing a bitmap": that bitmap as a PNG at its own resolution,
    recording the resolution that prints it at the copied size. None if it isn't one."""
    placed = conv.single_bitmap()
    if placed is None or placed.bitmap.pixels is None:
        return None
    x, y, w, h = placed.rect()
    bm = placed.bitmap
    dpi_x = bm.width / (w * emf_svg.UNIT_MM / 25.4)
    dpi_y = bm.height / (h * emf_svg.UNIT_MM / 25.4)
    if not dpi_x or abs(dpi_x - dpi_y) > 0.02 * dpi_x:
        return None   # stretched unevenly: drawn by Windows instead
    pixels = bm.pixels
    flip_x, flip_y = placed.mirrored
    if flip_x:
        pixels = pixels[:, ::-1]
    if flip_y:
        pixels = pixels[::-1]
    if pixels.shape[2] == 4:
        pixels = pixels[:, :, :3]
    dpi = (dpi_x + dpi_y) / 2
    dpm = round(dpi / 0.0254)
    png = emf_svg.png_bytes(np.ascontiguousarray(pixels), (dpm, dpm))
    low = f", only about {dpi:.0f} dpi" if dpi < LOW_DPI else f", about {dpi:.0f} dpi"
    return PastedSpectrum(png, ".png", emf, ".emf", "bitmap",
                          f"Mnova copied a picture ({bm.width} × {bm.height} pixels{low}); "
                          f"it was kept at its own resolution. {TIP}", conv.summary())


def _rendered_spectrum(emf: bytes, conv: emf_svg.Conversion | None, why: str
                       ) -> PastedSpectrum | None:
    """The metafile drawn by Windows at FALLBACK_DPI (by GDI+ when it is EMF+ only)."""
    image = ci.render_emf_bytes(emf, FALLBACK_DPI)
    if (image is None or _blank(image)) and conv is not None and conv.emf_plus:
        image = ci.render_emf_plus_bytes(emf, FALLBACK_DPI)
    if image is None or image.isNull() or _blank(image):
        return None   # (nothing to show: the paste says the copy could not be read)
    dpi = image.dotsPerMeterX() * 0.0254
    return PastedSpectrum(ci.image_to_png(image), ".png", emf, ".emf", "picture",
                          f"Kept as a picture ({dpi:.0f} dpi): {why}.",
                          conv.summary() if conv is not None else "")


def _blank(image: QImage) -> bool:
    """True when no pixel has a channel below INK_LEVEL (the same rule as ink(), without
    building its mask and copy of the pixels: a 600-dpi page is large)."""
    if image.isNull():
        return True
    rgb = image.convertToFormat(QImage.Format.Format_RGB32)   # (kept while px is used)
    w, h = rgb.width(), rgb.height()
    px = np.frombuffer(rgb.constBits(), np.uint8, count=rgb.sizeInBytes())
    px = px.reshape(h, rgb.bytesPerLine())[:, : w * 4].reshape(h, w, 4)[:, :, :3]
    return int(px.min()) >= INK_LEVEL


@dataclass
class CheckedSvg:
    """A metafile converted to SVG and compared with Windows' drawing of it (checked_svg):
    `svg` is set only when the conversion is complete and passed the self-check."""
    svg: str | None
    conversion: emf_svg.Conversion
    reason: str = ""


def checked_svg(emf: bytes) -> CheckedSvg:
    """The metafile as an SVG that has passed the self-check (Windows only: elsewhere a
    conversion is never kept unchecked), or the reason why not. Shared by pasted spectra
    and pasted pictures (ChemDraw structures, schemes, figures; SPEC §13n.1)."""
    conv = emf_svg.convert(emf, QtTextHelper())
    if conv.error:
        return CheckedSvg(None, conv, "the metafile could not be read")
    if not conv.drawn:
        return CheckedSvg(None, conv, "the copy draws nothing that can be converted")
    if not conv.ok:
        return CheckedSvg(None, conv, "the copy uses drawing commands that can't be converted")
    if sys.platform != "win32":
        return CheckedSvg(None, conv, "a metafile can only be checked on Windows")
    result = self_check(emf, conv.svg)
    if not result.passed:
        return CheckedSvg(None, conv, "the vector copy did not match Windows' drawing of it "
                                      "closely enough: " + result.reason)
    return CheckedSvg(conv.svg, conv)


def convert_emf(emf: bytes, check: bool = True) -> PastedSpectrum | None:
    """A copied metafile as the file to store: SVG when the conversion is complete and
    passes the self-check, else a picture (see the module's description). `check=False`
    keeps a complete conversion without comparing (only for testing off Windows). None if
    nothing usable can be made (e.g. a damaged metafile off Windows)."""
    conv = emf_svg.convert(emf, QtTextHelper())
    on_windows = sys.platform == "win32"
    if conv.error and not conv.drawn:
        return _rendered_spectrum(emf, conv, "the metafile could not be read") \
            if on_windows else None
    bitmap = _bitmap_spectrum(conv, emf)
    if bitmap is not None:
        return bitmap
    if conv.ok and conv.drawn:
        if not check:
            return PastedSpectrum(_svg_bytes(conv.svg), ".svg", emf, ".emf", "vector",
                                  VECTOR_MESSAGE, conv.summary())
        if on_windows:
            result = self_check(emf, conv.svg)
            if result.passed:
                return PastedSpectrum(_svg_bytes(conv.svg), ".svg", emf, ".emf",
                                      "vector", VECTOR_MESSAGE, conv.summary())
            why = ("the vector copy did not match Windows' drawing of it closely enough"
                   if result.reason != "the picture is empty" else "the copy looks empty")
            return _rendered_spectrum(emf, conv, why)
        return None
    if not on_windows:
        return None
    if conv.emf_plus and not conv.drawn:
        why = "the copy is in a newer metafile format (EMF+) that can't be converted"
    elif conv.unsupported:
        why = "the copy uses drawing commands that can't be converted"
    else:
        why = "the copy could not be converted"
    return _rendered_spectrum(emf, conv, why)


def reconvert_file(original: Path, check: bool = True) -> PastedSpectrum | None:
    """Convert a stored original (.emf) again; None if it can't be done here (an EMF can
    only be checked, or drawn, on Windows)."""
    if original.suffix.lower() != ".emf":
        return None
    try:
        data = original.read_bytes()
    except OSError:
        return None
    if sys.platform != "win32" and check:
        return None
    return convert_emf(data, check=check)


# ---------------------------------------------------------------------- the clipboard

def clipboard_files() -> list[Path]:
    """Files copied in File Explorer or Finder (their paths)."""
    data = QGuiApplication.clipboard().mimeData()
    if data is None or not data.hasUrls():
        return []
    return [Path(u.toLocalFile()) for u in data.urls() if u.isLocalFile()]


def clipboard_kind() -> str:
    """What a paste would use: "emf" (Mnova on Windows), "pdf" (Mnova on macOS),
    "image", or "" (nothing that can be a spectrum). Files are asked for separately."""
    if ci.clipboard_metafile_available():
        return "emf"
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return ""
    if any("pdf" in f.lower() for f in data.formats()):
        return "pdf"
    if data.hasImage() or any(k in f.lower() for f in data.formats()
                              for k in ("png", "jpeg", "jpg", "tiff", "bmp")):
        return "image"
    return ""


def clipboard_spectrum(kind: str | None = None) -> PastedSpectrum | None:
    """The clipboard's spectrum, ready to store (`kind` as clipboard_kind() says)."""
    kind = clipboard_kind() if kind is None else kind
    if kind == "emf":
        emf = ci.clipboard_metafile_bytes()
        return convert_emf(emf) if emf else None
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return None
    if kind == "pdf":
        fmt = next((f for f in data.formats() if "pdf" in f.lower()), None)
        raw = bytes(data.data(fmt)) if fmt else b""
        if raw.startswith(b"%PDF"):
            return PastedSpectrum(raw, ".pdf", None, "", "pdf",
                                  "Kept Mnova's PDF (a vector drawing).")
        return None
    if kind == "image":
        image = ci._qt_image()
        if image is None or image.isNull():
            return None
        return PastedSpectrum(ci.image_to_png(image), ".png", None, "", "image",
                              "Kept the copied picture as it is.")
    return None


def reconvert_pasted_spectra(project, progress=None) -> tuple[int, int, int]:
    """Convert every pasted spectrum again from the original kept next to it ("Re-render
    pasted pictures" in Project settings): useful after an update of the converter, e.g.
    a spectrum that had to be kept as a picture may now become SVG. Returns (converted
    again, of which kept as SVG, originals that can't be converted here — an EMF needs
    Windows). `progress(done_so_far, total)` as for rerender_pasted_pictures."""
    spectra = project.pasted_spectra()
    done = vector = failed = 0
    for i, (compound, key, item, _path, original) in enumerate(spectra):
        if progress is not None and progress(i, len(spectra)) is False:
            break
        try:
            result = reconvert_file(original)
        except Exception:  # noqa: BLE001 — one unusual original must not stop the others
            result = None
        if result is None:
            failed += 1
            continue
        project.replace_pasted_spectrum(compound, key, item, result.data, result.ext)
        done += 1
        vector += result.kind == "vector"
    return done, vector, failed


# ---------------------------------------------------------------------- the type chooser

TYPE_LABELS = {kind: F.SPECTRUM_TYPE_LABELS.get(kind, kind) for kind in F.SPECTRUM_TYPES}


class SpectrumTypeDialog(QDialog):
    """One click picks the type of a pasted spectrum; the guess is highlighted, so Enter
    takes it. Cancel (or Esc) pastes nothing."""

    def __init__(self, parent: QWidget | None, shown_id: str, guess: str, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Paste spectrum into {shown_id}")
        self.choice: str | None = None
        layout = QVBoxLayout(self)
        question = QLabel(f"What kind of spectrum is it? (for <b>{html.escape(shown_id)}</b>)")
        layout.addWidget(question)
        grid = QGridLayout()
        self.buttons: dict[str, QPushButton] = {}
        for i, kind in enumerate(F.SPECTRUM_TYPES):
            button = QPushButton(TYPE_LABELS.get(kind, kind))
            button.setAutoDefault(True)
            button.setMinimumWidth(90)
            button.clicked.connect(lambda _=False, k=kind: self.choose(kind=k))
            grid.addWidget(button, i // 5, i % 5)
            self.buttons[kind] = button
        layout.addLayout(grid)
        self.guess = guess if guess in self.buttons else F.SPECTRUM_TYPES[0]
        guessed = self.buttons[self.guess]
        guessed.setDefault(True)
        font = guessed.font()
        font.setBold(True)
        guessed.setFont(font)
        guessed.setStyleSheet(f"QPushButton {{ border: 2px solid {HIGHLIGHT}; padding: 4px; }}")
        text = QLabel((note + " " if note else "") +
                      "Press Enter for the highlighted type, or click another.")
        text.setWordWrap(True)
        text.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(text)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        layout.addLayout(row)
        guessed.setFocus()

    def choose(self, kind: str) -> None:
        self.choice = kind
        self.accept()


def choose_spectrum_type(parent: QWidget | None, shown_id: str, guess: str,
                         note: str = "") -> str | None:
    """Ask which type the pasted spectrum is (None: cancelled)."""
    dialog = SpectrumTypeDialog(parent, shown_id, guess, note)
    dialog.buttons[dialog.guess].setFocus()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.choice


# ---------------------------------------------------------------------- pasting

@dataclass
class PasteOutcome:
    items: list[dict]
    message: str


NOTHING_TO_PASTE = ("The clipboard has no spectrum. Copy the spectrum in Mnova (or a PDF "
                    "or picture file in File Explorer or Finder) first, then paste.")


def paste_spectrum(parent: QWidget, project, compound,
                   before_write=None) -> PasteOutcome | None:
    """Paste what was copied into a compound's spectra (the Spectra field and the
    compound's spectra page share this): a spectrum from Mnova (or any picture), or
    copied PDF/picture files. One spectrum asks its type first (one click; the guess is
    highlighted); several copied files get their types from their names. One Edit ▸ Undo
    step. None if nothing was pasted (the user was told why)."""
    if project.read_only:
        return None
    from ..core import spectra as S
    from ..core.project import ATTACHMENT_EXTENSIONS, ProjectError
    from .field_widgets import guess_spectrum_type
    # Typing not saved yet (e.g. a peak report typed a moment ago) is saved first: the
    # type guess reads the saved reports, and it stays an undo step of its own. If it
    # could not be saved (the user was told), nothing is pasted.
    if before_write is not None and before_write() is False:
        return None
    copied = clipboard_files()
    files = [f for f in copied if f.suffix.lower() in ATTACHMENT_EXTENSIONS and f.is_file()]
    kind = "" if files else clipboard_kind()
    if not files and not kind:
        if copied:
            QMessageBox.information(
                parent, "Cannot paste this file",
                f"{copied[0].name} is not a PDF or a picture. Spectra can be PDFs or pictures "
                f"({', '.join(ATTACHMENT_EXTENSIONS)}).")
        else:
            QMessageBox.information(parent, "Nothing to paste", NOTHING_TO_PASTE)
        return None
    shown = project.id_report().display_id(compound.id)
    if len(files) > 1:
        chosen = None
    else:
        if files:
            guess, note = guess_spectrum_type(files[0].name, shown), f"{files[0].name}:"
        else:
            guess = S.guess_pasted_type(compound)
            note = "Guessed from the NMR reports that have no spectrum yet."
        chosen = choose_spectrum_type(parent, shown, guess, note)
        if chosen is None:
            return None
    spectrum = None
    if not files:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            spectrum = clipboard_spectrum(kind)
        except Exception as e:  # noqa: BLE001 — an unexpected problem must not lose the paste silently
            spectrum = None
            problem = f" (details: {type(e).__name__}: {e})"
        else:
            problem = ""
        finally:
            QApplication.restoreOverrideCursor()
        if spectrum is None:
            QMessageBox.warning(parent, "Could not paste",
                                "The copied spectrum could not be read. Copy it again in "
                                "Mnova, or export it as a PDF and add that file instead."
                                + problem)
            return None
    label = (f"Paste spectrum into {shown}" if len(files) <= 1 else
             f"Paste {len(files)} files into {shown} ▸ Spectra")
    try:
        with project.undo_step(label):
            # A compound whose Spectra field was removed (it may still have a spectra page,
            # for an Additional file set to "Spectra section") gets it back, so the pasted
            # spectrum is shown and printed. Part of the same undo step.
            added_field = "spectra" not in compound.field_keys
            if added_field:
                project.add_field(compound, {"key": "spectra"})
            if spectrum is not None:
                items = [project.attach_pasted_spectrum(
                    compound, spectrum.data, spectrum.ext, chosen, spectrum.original,
                    spectrum.original_ext)]
                message = spectrum.message
            else:
                items = [project.attach_compound_file(
                    compound, "spectra", f,
                    {"type": chosen or guess_spectrum_type(f.name, shown)}) for f in files]
                message = ("Pasted " + (files[0].name if len(files) == 1 else
                                        f"{len(files)} files") +
                           " (a copy is stored in the project folder).")
                skipped = [f.name for f in copied if f not in files]
                if skipped:
                    names = ", ".join(skipped[:5]) + (
                        f" and {len(skipped) - 5} more" if len(skipped) > 5 else "")
                    message += f" Not pasted: {names} (spectra can be PDFs or pictures)."
            if added_field:
                message += " The Spectra field was added to this compound's page again."
    except (ProjectError, OSError) as e:
        QMessageBox.warning(parent, "Could not paste", str(e))
        return None
    except Exception as e:  # noqa: BLE001 — say so rather than failing silently
        QMessageBox.warning(parent, "Could not paste",
                            f"The spectrum could not be stored ({type(e).__name__}: {e}).")
        return None
    return PasteOutcome(items, message)


class SpectraEditor(PasteTargetMixin, AttachmentsEditor):
    """A compound's Spectra field, which is also a paste target (SPEC §13l.1): click it,
    then Ctrl+V — or right-click ▸ Paste spectrum, or the Paste spectrum button."""

    pasted = Signal(str)   # a spectrum was pasted: the line to show (status bar)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows_layout.setContentsMargins(4, 4, 4, 4)
        self.paste_btn = QPushButton("Paste spectrum")
        self.paste_btn.setToolTip(
            "Paste a spectrum copied in Mnova (kept as a vector drawing where possible), a "
            f"picture, or copied PDF/picture files. Or click this field and press "
            f"{paste_key()} (right-click offers the same).")
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        self.paste_btn.setEnabled(not self.project.read_only)
        self.bottom_row.insertWidget(1, self.paste_btn)
        # (shorter than the other file fields' hint: it shares its line with three buttons)
        self.drop_hint.setText("…or drop PDFs/pictures here.")
        self.drop_hint.setToolTip("A copy of each file is stored in the project.")
        hint = self._init_paste_target(self.rows_frame, [
            ("Paste spectrum", self.paste_from_clipboard), ("Add files…", self._choose)])
        self.main_layout.insertWidget(self.main_layout.indexOf(self.rows_frame), hint)
        self.paste_note = QLabel()
        self.paste_note.setWordWrap(True)
        self.paste_note.setStyleSheet("color: palette(placeholder-text);")
        self.paste_note.hide()
        self.main_layout.addWidget(self.paste_note)
        self._update_hints()

    def _rows_frame(self) -> QFrame:
        return PasteFrame()

    def _empty(self) -> bool:
        return not self.compound.values.get(self.defn.key)

    def _build_rows(self) -> None:
        super()._build_rows()
        if self._empty():
            # One short line says how (the click-here hint and the drop hint, which say
            # the same, are hidden meanwhile: see _update_hints).
            empty = QLabel("No spectra yet." if self.project.read_only else
                           f"No spectra yet: copy one in Mnova, click here, {paste_key()} "
                           "— or drop files here.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: palette(placeholder-text); padding: 6px;")
            self.rows_layout.addWidget(empty, 0, 0)
        self._update_hints()

    def _update_hints(self) -> None:
        """While the field is empty its own line says how to paste or drop files, so the
        "Click this box…" and "…or drag PDFs/images here" hints are hidden (the blue
        "▶ Ctrl+V pastes here" still shows once the field is clicked)."""
        empty = self._empty()
        if getattr(self, "drop_hint", None) is not None:
            self.drop_hint.setVisible(not empty)
        hint = getattr(self, "paste_hint", None)
        if hint is not None:
            hint.setVisible(not self._paste_locked and bool(hint.text())
                            and (self._paste_active or not empty))

    def _set_paste_active(self, active: bool) -> None:
        super()._set_paste_active(active)
        self._update_hints()

    def paste_from_clipboard(self) -> None:
        outcome = paste_spectrum(self, self.project, self.compound, self.before_write)
        if outcome is None:
            return
        self._build_rows()
        self.files_changed.emit()
        self.paste_note.setText(outcome.message)
        self.paste_note.show()
        self.pasted.emit(outcome.message)
