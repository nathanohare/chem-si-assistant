"""The scale of a pasted structure drawing: its typical bond length (SPEC §13n.2a).

A structure pasted from ChemDraw is kept as a vector drawing (an SVG converted from
ChemDraw's metafile on Windows, ChemDraw's PDF on macOS). Drawings copied at different
zoom levels, or from documents with different styles, have different bond lengths. To
print every structure at the same scale, the program measures the typical bond length in
each drawing and scales the printed picture so that bonds print at the length set in
Project settings ▸ Structures (ACS 1996: 0.508 cm = 14.4 pt).

How a bond length is measured (pure Python with NumPy, no Qt, so it runs everywhere):

* Every straight stroked segment of the drawing is collected, in points as printed: SVG
  <line>, <polyline>, <polygon> and the straight parts of <path> (curves, text, pictures,
  rectangles and anything inside <defs>/<clipPath>/<mask> are not bonds); in a PDF the
  stroked straight parts of its paths. A long thin filled rectangle (a bond drawn as a
  filled shape) counts as a segment along its length. White lines (bond-crossing gaps),
  axis-aligned closed rectangles (frames) and segments shorter than MIN_SEGMENT_PT are
  left out.
* Candidate bond lengths are the lengths that many segments share (within ±PEAK_TOL).
  For each candidate B, the "bond-like" segments are those between SHORT_FRACTION·B and
  B (bonds to atom labels are drawn shorter, and so is the inner line of a double bond).
  Segments of a molecule meet at its atoms: a bond-like segment counts fully when one of
  its ends lies within CONNECT_FRACTION·B of an end of another, otherwise a quarter (the
  separate lines of arrows, plus signs and hashed wedges hardly count). The candidate
  with the highest count wins (ties: the longer one), and its length is the median of
  the segments within ±PEAK_TOL of it. At least two joined segments are needed.
* When the structure copied with the picture is known (ChemDraw puts a MOL block on the
  clipboard), it is a cross-check: its median bond length relative to the size of the
  molecule, times the size of the drawing's bonds, must agree with the measurement
  within MOL_TOLERANCE (else the best candidate that does is taken, or none), and the
  drawing must have at least MOL_MIN_SHARE bond-like segments per bond of the molecule.

No measurement (a raster picture, a drawing without bonds, nonsense) means the picture
prints at its own size, as before.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from typing import Any

import numpy as np

ACS_BOND_PT = 0.508 / 2.54 * 72     # 14.4 pt: ACS 1996 document settings
MIN_SEGMENT_PT = 0.5                # shorter strokes are dots and hatches, not bonds
MIN_BOND_PT = 1.0                   # a measured bond outside these limits is nonsense
MAX_BOND_PT = 200.0
PEAK_TOL = 0.04                     # segments within ±4 % share a length
SHORT_FRACTION = 0.6                # a bond to a label is drawn at least this long
CONNECT_FRACTION = 0.2              # ends this close (× bond length) meet at an atom
MIN_CONNECTED = 2                   # joined bond-like segments needed for a measurement
MOL_TOLERANCE = 1.5                 # agreement needed with the copied structure (bonds to
                                    # labels at the edges make the drawing's bonds span less)
MOL_MIN_SHARE = 0.4                 # bond-like segments per bond of the copied structure
MAX_PEAKS = 12
FACTOR_RANGE = (0.2, 5.0)           # a factor from a MEASURED length outside this is not
                                    # used (a bond length typed by the user always is)
TYPED_RANGE = (0.5, 200.0)          # a bond length typed by the user (pt)
MAX_DEPTH = 200                     # SVG groups nested deeper than this are not read

# Project settings key: print all pasted structure pictures at the same bond length.
SETTING_KEY = "same_bond_length"
# compound.json, "structure": the measured bond length of the picture (pt; null: none
# found) and the user's own value (pt; 0: print at the picture's own size).
MEASURED_KEY = "image_bond_pt"
OVERRIDE_KEY = "image_bond_override"
VECTOR_PICTURES = (".svg", ".pdf")


@dataclass(frozen=True)
class Reference:
    """What the copied structure says about the drawing: its number of bonds and its
    median bond length divided by its size (the larger of width and height)."""
    bonds: int
    bond_over_extent: float


# ---------------------------------------------------------------------- the copied structure

def reference_from_mol(mol) -> Reference | None:
    """A Reference from an RDKit molecule with 2D coordinates (None if it has fewer than
    two bonds or no usable coordinates)."""
    try:
        if mol is None or mol.GetNumBonds() < 2 or not mol.GetNumConformers():
            return None
        pos = mol.GetConformer().GetPositions()[:, :2]
        lengths = [float(np.hypot(*(pos[b.GetBeginAtomIdx()] - pos[b.GetEndAtomIdx()])))
                   for b in mol.GetBonds()]
    except Exception:  # noqa: BLE001 — an odd molecule just gives no cross-check
        return None
    extent = float(max(np.ptp(pos[:, 0]), np.ptp(pos[:, 1])))
    median = float(np.median(lengths))
    if not (extent > 0 and median > 0):
        return None
    return Reference(len(lengths), median / extent)


def reference_from_molblock(text: str) -> Reference | None:
    """A Reference from a MOL block's own coordinates (as ChemDraw copies them)."""
    try:
        from rdkit import Chem
        mol = Chem.MolFromMolBlock(text, sanitize=False, removeHs=False)
    except Exception:  # noqa: BLE001
        return None
    return reference_from_mol(mol)


# ---------------------------------------------------------------------- geometry helpers

def _matrix(a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0) -> np.ndarray:
    return np.array([[a, c, e], [b, d, f], [0.0, 0.0, 1.0]])


_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(v) for v in _NUMBER.findall(text or "")]


def parse_transform(text: str | None) -> np.ndarray:
    """An SVG transform attribute as a 3 × 3 matrix (unknown parts are left out)."""
    m = _matrix()
    for name, args in _TRANSFORM.findall(text or ""):
        v = _numbers(args)
        t = _matrix()
        if name == "matrix" and len(v) == 6:
            t = _matrix(*v)
        elif name == "translate" and v:
            t = _matrix(e=v[0], f=v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = _matrix(a=v[0], d=v[1] if len(v) > 1 else v[0])
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = _matrix(math.cos(r), math.sin(r), -math.sin(r), math.cos(r))
            if len(v) == 3:
                t = _matrix(e=v[1], f=v[2]) @ t @ _matrix(e=-v[1], f=-v[2])
        elif name == "skewX" and v:
            t = _matrix(c=math.tan(math.radians(v[0])))
        elif name == "skewY" and v:
            t = _matrix(b=math.tan(math.radians(v[0])))
        m = m @ t
    return m


def _apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ m[:2, :2].T + m[:2, 2]


class _Collector:
    """Straight segments (x0, y0, x1, y1) in points, from the drawing's shapes."""

    def __init__(self):
        self.segments: list[tuple[float, float, float, float]] = []

    def stroked(self, subpaths: list[tuple[list, bool]], m: np.ndarray) -> None:
        """`subpaths`: (pieces, closed), each piece (p0, p1, straight)."""
        for pieces, closed in subpaths:
            if not pieces:
                continue
            pts = np.array([[p0[0], p0[1], p1[0], p1[1]] for p0, p1, _s in pieces], float)
            a, b = _apply(m, pts[:, :2]), _apply(m, pts[:, 2:])
            if closed and _is_frame(a, b, [s for _p0, _p1, s in pieces]):
                continue   # an axis-aligned rectangle: a frame, not bonds
            for (x0, y0), (x1, y1), (_p0, _p1, straight) in zip(a, b, pieces):
                if straight:
                    self.segments.append((x0, y0, x1, y1))

    def filled(self, subpaths: list[tuple[list, bool]], m: np.ndarray) -> None:
        """A long thin filled four-sided shape is a bond drawn as a filled shape."""
        for pieces, _closed in subpaths:
            if not pieces or not all(s for _p0, _p1, s in pieces):
                continue
            pts = [p0 for p0, _p1, _s in pieces]
            if not np.allclose(pieces[-1][1], pieces[0][0]):
                pts.append(pieces[-1][1])   # (not closed explicitly: the fill closes it)
            pts = [p for k, p in enumerate(pts) if k == 0 or not np.allclose(p, pts[k - 1])]
            if len(pts) > 1 and np.allclose(pts[-1], pts[0]):
                pts.pop()
            if len(pts) != 4:
                continue
            q = _apply(m, np.array(pts, float))
            seg = _thin_rectangle(q)
            if seg is not None:
                self.segments.append(seg)


def _is_frame(a: np.ndarray, b: np.ndarray, straight: list[bool]) -> bool:
    if len(a) != 4 or not all(straight):
        return False
    d = b - a
    lengths = np.hypot(d[:, 0], d[:, 1])
    if (lengths <= 0).any():
        return False
    flat = np.minimum(np.abs(d[:, 0]), np.abs(d[:, 1])) / lengths
    return bool((flat < 0.01).all())


def _thin_rectangle(q: np.ndarray) -> tuple[float, float, float, float] | None:
    sides = np.roll(q, -1, axis=0) - q
    lengths = np.hypot(sides[:, 0], sides[:, 1])
    if (lengths <= 0).any():
        return None
    long_i = 0 if lengths[0] + lengths[2] >= lengths[1] + lengths[3] else 1
    long_len = (lengths[long_i] + lengths[long_i + 2]) / 2
    short_len = (lengths[1 - long_i] + lengths[3 - long_i]) / 2
    if not (short_len <= 0.25 * long_len and short_len <= 4.0):
        return None
    if abs(lengths[long_i] - lengths[long_i + 2]) > 0.1 * long_len:
        return None
    cos = abs(float(np.dot(sides[0], sides[1]))) / (lengths[0] * lengths[1])
    if cos > 0.1:
        return None
    # The middles of the two short sides.
    s = 1 - long_i
    m0 = (q[s] + q[(s + 1) % 4]) / 2
    m1 = (q[s + 2] + q[(s + 3) % 4]) / 2
    return float(m0[0]), float(m0[1]), float(m1[0]), float(m1[1])


# ---------------------------------------------------------------------- SVG

_SVG_UNITS_PT = {"": 1.0, "px": 1.0, "pt": 1.0, "pc": 12.0, "mm": 72 / 25.4,
                 "cm": 72 / 2.54, "in": 72.0}   # (as compile/document.svg_size_in)
_SVG_UNITS_PX = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4,
                 "cm": 96 / 2.54, "in": 96.0}   # (as the SVG renderer reads them)
# A width or height as compile/document.svg_size_in reads it for the printed size (the
# two must agree, so it can build its pattern from this one): no sign, spaces anywhere
# around the unit.
PRINT_LENGTH = r"\s*((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*"
_PRINT_LENGTH = re.compile(PRINT_LENGTH)
# … and as the SVG renderer (usvg, in Typst) reads it: spaces before it, a sign, the unit
# straight after the number and nothing after that. Anything else (such as 'auto') it
# ignores, as if there were no width or height.
_RENDER_LENGTH = re.compile(r"[ \t\n\r]*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)"
                            r"(?:[eE][-+]?[0-9]+)?)(%|em|ex|px|in|cm|mm|pt|pc)?")
_SKIP = {"defs", "clipPath", "mask", "pattern", "symbol", "marker", "text", "image",
         "style", "title", "desc", "metadata", "linearGradient", "radialGradient", "use",
         "foreignObject", "script", "rect", "circle", "ellipse"}
_WHITE = {"#fff", "#ffffff", "white", "rgb(255,255,255)", "rgb(100%,100%,100%)"}
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _length_pt(value: str | None) -> float | None:
    """A width or height in pt, as compile/document.svg_size_in prints it (None: it gives
    no size, so the picture prints at its viewBox's size)."""
    m = _PRINT_LENGTH.fullmatch(value or "")
    if m is None or m.group(2) not in _SVG_UNITS_PT:
        return None
    n = float(m.group(1)) * _SVG_UNITS_PT[m.group(2)]
    return n if math.isfinite(n) else None


def _length_px(value: str | None, whole: float | None = None) -> float | None:
    """A length in CSS px, as the SVG renderer (usvg, in Typst) reads it: 96 px per inch;
    a percentage of `whole`, the viewBox's size, and so is no length or one it can't read
    (such as 'auto'). None: a unit it sizes by the font (em, ex), not measured here."""
    m = _RENDER_LENGTH.fullmatch(value or "")
    n = float(m.group(1)) if m else math.nan
    if not math.isfinite(n):
        return whole
    unit = m.group(2) or ""
    if unit == "%":
        return n / 100 * whole if whole else None
    if unit not in _SVG_UNITS_PX:
        return None
    return n * _SVG_UNITS_PX[unit]


def _svg_scale(root) -> tuple[float, float]:
    """Points per user unit of the drawing, each way, as it prints. The rules are those of
    printing: compile/document.svg_size_in gives the printed width (width AND height,
    px or no unit = 1 pt; otherwise the viewBox at 1 unit = 1 pt) and Typst scales the
    drawing's own size (in CSS px, 96 per inch; a missing width or height: the viewBox's)
    to it, with the viewBox fitted into that size as SVG does (preserveAspectRatio:
    'meet' unless it says 'slice' or 'none')."""
    box = _numbers(root.get("viewBox", ""))
    w, h = _length_pt(root.get("width")), _length_pt(root.get("height"))
    sized = bool(w and h and w > 0 and h > 0)
    if len(box) == 4 and box[2] > 0 and box[3] > 0:
        wpx = _length_px(root.get("width"), box[2])
        hpx = _length_px(root.get("height"), box[3])
        if not (wpx and hpx and wpx > 0 and hpx > 0):
            return 1.0, 1.0
        pt_per_px = (w if sized else box[2]) / wpx
        sx, sy = wpx / box[2], hpx / box[3]
        fit = (root.get("preserveAspectRatio") or "").split()
        if "none" in fit:
            return pt_per_px * sx, pt_per_px * sy
        s = max(sx, sy) if "slice" in fit else min(sx, sy)
        return pt_per_px * s, pt_per_px * s
    wpx = _length_px(root.get("width"))
    if not (sized and wpx):
        return 1.0, 1.0                # no printed size known: fitted to its box
    # No viewBox: 1 user unit = 1 CSS px. A px (or unitless) width prints as 1 pt per px,
    # an absolute one (pt, mm, …) at its real size, so a px is then 0.75 pt.
    return w / wpx, w / wpx


def _style(elem, inherited: dict) -> dict:
    out = dict(inherited)
    for key in ("stroke", "fill", "display", "visibility", "stroke-width"):
        if elem.get(key) is not None:
            out[key] = elem.get(key).strip()
    for part in (elem.get("style") or "").split(";"):
        key, _, value = part.partition(":")
        key = key.strip()
        if key in ("stroke", "fill", "display", "visibility", "stroke-width"):
            out[key] = value.strip()
    return out


def _paints(value: str | None) -> bool:
    """Whether a stroke/fill draws something that shows on white paper."""
    if value is None:
        return False
    v = value.replace(" ", "").lower()
    return bool(v) and v not in ("none", "transparent") and v not in _WHITE


def path_subpaths(d: str) -> list[tuple[list, bool]]:
    """SVG path data as subpaths: ([(p0, p1, straight), …], closed). Curves and arcs are
    pieces that are not straight (they only move the pen, as far as bonds go)."""
    tokens = _PATH_TOKEN.findall(d or "")
    out: list[tuple[list, bool]] = []
    pieces: list = []
    cur = start = (0.0, 0.0)
    cmd = ""
    i = 0

    def finish(closed: bool) -> None:
        nonlocal pieces
        if pieces:
            out.append((pieces, closed))
        pieces = []

    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if pieces and cur != start:
                    pieces.append((cur, start, True))
                finish(True)
                cur = start
                continue
        elif not cmd:
            i += 1
            continue
        n = _ARGS[cmd.upper()]
        if n == 0 or i + n > len(tokens) or any(tokens[j].isalpha() for j in range(i, i + n)):
            i += 1
            continue
        v = [float(x) for x in tokens[i:i + n]]
        i += n
        rel = cmd.islower()
        up = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if up == "M":
            finish(False)
            cur = start = (v[0] + ox, v[1] + oy)
            cmd = "l" if rel else "L"   # (further pairs are lines)
            continue
        if up == "L":
            new = (v[0] + ox, v[1] + oy)
            pieces.append((cur, new, True))
        elif up == "H":
            new = (v[0] + (cur[0] if rel else 0.0), cur[1])
            pieces.append((cur, new, True))
        elif up == "V":
            new = (cur[0], v[0] + (cur[1] if rel else 0.0))
            pieces.append((cur, new, True))
        else:   # curves and arcs: their end point
            new = (v[-2] + ox, v[-1] + oy)
            pieces.append((cur, new, False))
        cur = new
    finish(False)
    return out


def _points_attr(text: str) -> list[tuple[float, float]]:
    v = _numbers(text)
    return [(v[k], v[k + 1]) for k in range(0, len(v) - 1, 2)]


def svg_segments(svg_text: str) -> np.ndarray | None:
    """The drawing's straight segments in points, as an (n, 4) array (None if the SVG
    can't be read). Never raises: a damaged or strange file (unknown text encoding,
    groups nested thousands deep, empty shapes…) gives None or fewer segments."""
    try:
        return _svg_segments(svg_text)
    except Exception:  # noqa: BLE001 — an unreadable picture just gives no measurement
        return None


def _svg_segments(svg_text: str) -> np.ndarray | None:
    try:
        root = ET.fromstring(svg_text.encode("utf-8") if isinstance(svg_text, str)
                             else svg_text)
    except (ET.ParseError, ValueError, LookupError):
        return None
    if _local(root.tag) != "svg":
        return None
    sx, sy = _svg_scale(root)
    top = _matrix(a=sx, d=sy)
    box = _numbers(root.get("viewBox", ""))
    if len(box) == 4:
        top = top @ _matrix(e=-box[0], f=-box[1])
    collector = _Collector()

    def shape(child, name: str, cm: np.ndarray, st: dict) -> None:
        if name == "line":
            v = [float(child.get(k, "0") or 0) for k in ("x1", "y1", "x2", "y2")]
            subpaths = [([((v[0], v[1]), (v[2], v[3]), True)], False)]
        elif name in ("polyline", "polygon"):
            pts = _points_attr(child.get("points", ""))
            pieces = [(pts[k], pts[k + 1], True) for k in range(len(pts) - 1)]
            closed = name == "polygon"
            if closed and len(pts) > 2:
                pieces.append((pts[-1], pts[0], True))
            subpaths = [(pieces, closed)]
        elif name == "path":
            subpaths = path_subpaths(child.get("d", ""))
        else:
            return
        stroke = _paints(st.get("stroke"))
        fill = _paints(st.get("fill", "#000000")) and name != "line"
        if stroke:
            collector.stroked(subpaths, cm)
        elif fill:
            collector.filled(subpaths, cm)

    # (walked with a list, not recursion: groups nested very deep can't overflow the stack)
    todo: list[tuple[Any, np.ndarray, dict, int]] = [(root, top, {}, 0)]
    try:
        while todo:
            elem, m, style, depth = todo.pop()
            groups = []
            for child in elem:
                name = _local(child.tag)
                if name in _SKIP:
                    continue
                st = _style(child, style)
                if st.get("display") == "none" or st.get("visibility") == "hidden":
                    continue
                cm = m @ parse_transform(child.get("transform")) if child.get("transform") \
                    else m
                if name in ("g", "a", "svg", "switch"):
                    if depth < MAX_DEPTH:
                        groups.append((child, cm, st, depth + 1))
                    continue
                shape(child, name, cm, st)
            todo.extend(reversed(groups))
    except (ValueError, OverflowError, IndexError, TypeError):
        return None
    return _as_array(collector.segments)


def _as_array(segments) -> np.ndarray:
    arr = np.array(segments, float).reshape(-1, 4)
    return arr[np.isfinite(arr).all(axis=1)]


# ---------------------------------------------------------------------- PDF

_PAINT_STROKE = {"S", "s", "B", "B*", "b", "b*"}
_PAINT_FILL = {"f", "F", "f*"}
# Colour spaces whose values are plain gray, RGB or CMYK (so white can be read from them);
# in any other (a spot colour, a palette, Lab, a pattern) '1' does not mean white.
_DEVICE_SPACES = {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/G", "/RGB", "/CMYK"}
_PLAIN_SPACES = {"/CalGray", "/CalRGB", "/ICCBased"}


def _plain_space(name, resources) -> bool:
    """Whether the colour space `name` (set with CS/cs) is plain gray, RGB or CMYK."""
    name = str(name)
    if name in _DEVICE_SPACES:
        return True
    try:
        spaces = resources.get_object().get("/ColorSpace") if resources is not None else None
        space = spaces.get_object()[name].get_object() if spaces else None
        family = space[0] if isinstance(space, list) else space
        return str(family) in _PLAIN_SPACES or str(family) in _DEVICE_SPACES
    except Exception:  # noqa: BLE001 — an unknown space: its colours are not read as white
        return False


def pdf_segments(data: bytes, page_index: int = 0) -> np.ndarray | None:
    """The straight segments of a page of a PDF (the first: ChemDraw's copy on macOS), in
    points, as an (n, 4) array (None if it can't be read)."""
    try:
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
        reader = PdfReader(io.BytesIO(data))
        page = reader.pages[page_index]
        contents = page.get_contents()
        if contents is None:
            return _as_array([])
        collector = _Collector()
        _pdf_walk(ContentStream(contents, reader).operations, page.get("/Resources"),
                  _matrix(), collector, reader, 0)
        return _as_array(collector.segments)
    except Exception:  # noqa: BLE001 — an unreadable PDF just gives no measurement
        return None


def _pdf_walk(operations, resources, ctm: np.ndarray, collector: _Collector, reader,
              depth: int, colours: tuple = (False, False, True, True)) -> None:
    """`colours`: (stroke white, fill white, stroke space plain, fill space plain) at the
    start — a form drawn with 'Do' takes on the colours (and colour spaces) set before."""
    from pypdf.generic import ContentStream
    stack: list[tuple] = []
    # (plain: the colour space is plain gray, RGB or CMYK, so its colours can be white)
    stroke_white, fill_white, stroke_plain, fill_plain = colours
    subpaths: list[tuple[list, bool]] = []
    pieces: list = []
    cur = start = (0.0, 0.0)

    def finish(closed: bool) -> None:
        nonlocal pieces
        if pieces:
            subpaths.append((pieces, closed))
        pieces = []

    def white(values) -> bool:
        try:
            return all(float(v) >= 0.97 for v in values) if len(values) in (1, 3) else \
                len(values) == 4 and all(float(v) <= 0.03 for v in values)   # (CMYK)
        except (TypeError, ValueError):
            return False

    for operands, op in operations:
        op = op.decode("latin-1") if isinstance(op, bytes) else str(op)
        try:
            v = [float(x) for x in operands] if op in (
                "cm", "m", "l", "c", "v", "y", "re") else []
        except (TypeError, ValueError):
            continue
        if op == "q":
            stack.append((ctm, stroke_white, fill_white, stroke_plain, fill_plain))
        elif op == "Q":
            if stack:
                ctm, stroke_white, fill_white, stroke_plain, fill_plain = stack.pop()
        elif op == "cm" and len(v) == 6:
            ctm = ctm @ _matrix(*v)
        elif op in ("G", "RG", "K"):
            stroke_white, stroke_plain = white(operands), True
        elif op in ("g", "rg", "k"):
            fill_white, fill_plain = white(operands), True
        elif op == "CS":   # (a new colour space starts at black)
            stroke_white = False
            stroke_plain = bool(operands) and _plain_space(operands[0], resources)
        elif op == "cs":
            fill_white = False
            fill_plain = bool(operands) and _plain_space(operands[0], resources)
        elif op in ("SC", "SCN"):   # (a pattern name is not white)
            stroke_white = stroke_plain and white(operands)
        elif op in ("sc", "scn"):
            fill_white = fill_plain and white(operands)
        elif op == "m" and len(v) == 2:
            finish(False)
            cur = start = (v[0], v[1])
        elif op == "l" and len(v) == 2:
            pieces.append((cur, (v[0], v[1]), True))
            cur = (v[0], v[1])
        elif op in ("c", "v", "y") and len(v) >= 4:
            pieces.append((cur, (v[-2], v[-1]), False))
            cur = (v[-2], v[-1])
        elif op == "h":
            if pieces and cur != start:
                pieces.append((cur, start, True))
            finish(True)
            cur = start
        elif op == "re" and len(v) == 4:
            finish(False)
            x, y, w, h = v
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            subpaths.append(([(corners[k], corners[(k + 1) % 4], True) for k in range(4)],
                             True))
            cur = start = (x, y)
        elif op in _PAINT_STROKE or op in _PAINT_FILL or op == "n":
            closes = op in ("s", "b", "b*")
            if closes and pieces and cur != start:
                pieces.append((cur, start, True))   # (as 'h': the closing side)
            finish(closes)
            if closes:
                cur = start
            if op in _PAINT_STROKE and not stroke_white:
                collector.stroked(subpaths, ctm)
            elif op in _PAINT_FILL and not fill_white:
                collector.filled(subpaths, ctm)
            subpaths = []
        elif op == "Do" and depth < 4 and operands and resources is not None:
            try:
                xobjects = resources.get_object().get("/XObject")
                form = xobjects.get_object()[operands[0]].get_object() if xobjects else None
                if form is None or form.get("/Subtype") != "/Form":
                    continue
                matrix = [float(x) for x in form.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                inner = ContentStream(form, reader).operations
                _pdf_walk(inner, form.get("/Resources", resources),
                          ctm @ _matrix(*matrix), collector, reader, depth + 1,
                          (stroke_white, fill_white, stroke_plain, fill_plain))
            except Exception:  # noqa: BLE001
                continue


# ---------------------------------------------------------------------- the measurement

def _connected(seg: np.ndarray, tol: float) -> np.ndarray:
    """For each segment: does one of its ends lie within `tol` of an end of another?"""
    n = len(seg)
    ends = np.vstack((seg[:, :2], seg[:, 2:]))
    owner = np.concatenate((np.arange(n), np.arange(n)))
    cells: dict[tuple[int, int], list[int]] = {}
    keys = np.floor(ends / tol).astype(np.int64)
    for k, (cx, cy) in enumerate(keys.tolist()):
        cells.setdefault((cx, cy), []).append(k)
    out = np.zeros(n, bool)
    for k, (cx, cy) in enumerate(keys.tolist()):
        if out[owner[k]]:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((cx + dx, cy + dy), ()):
                    if owner[j] != owner[k] and np.hypot(*(ends[j] - ends[k])) <= tol:
                        out[owner[k]] = True
                        break
    return out


@dataclass
class _Candidate:
    bond: float          # the median length of the segments sharing it
    core: int            # segments within ±PEAK_TOL
    members: int         # bond-like segments (SHORT_FRACTION·bond … bond)
    connected: int       # of which joined to another
    extent: float        # size of the bond-like segments' area (pt)

    @property
    def score(self) -> float:
        return self.connected + 0.25 * (self.members - self.connected)


def _candidates(seg: np.ndarray) -> list[_Candidate]:
    lengths = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    keep = lengths >= MIN_SEGMENT_PT
    seg, lengths = seg[keep], lengths[keep]
    if len(seg) < 2:
        return []
    order = np.argsort(lengths)
    ls = lengths[order]
    lo = np.searchsorted(ls, ls * (1 - PEAK_TOL), "left")
    hi = np.searchsorted(ls, ls * (1 + PEAK_TOL), "right")
    support = hi - lo
    centres: list[float] = []
    for k in sorted(range(len(ls)), key=lambda k: (-support[k], -ls[k])):
        if support[k] < 2 or len(centres) >= MAX_PEAKS:
            break
        if any(abs(ls[k] / c - 1) < 2 * PEAK_TOL for c in centres):
            continue
        centres.append(float(ls[k]))
    out = []
    for c in centres:
        core = np.abs(lengths / c - 1) <= PEAK_TOL
        bond = float(np.median(lengths[core]))
        if not MIN_BOND_PT <= bond <= MAX_BOND_PT:
            continue
        member = (lengths >= SHORT_FRACTION * bond) & (lengths <= bond * (1 + PEAK_TOL))
        joined = _connected(seg[member], CONNECT_FRACTION * bond)
        pts = np.vstack((seg[member][:, :2], seg[member][:, 2:]))
        extent = float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))) if len(pts) else 0.0
        out.append(_Candidate(bond, int(core.sum()), int(member.sum()), int(joined.sum()),
                              extent))
    return out


def bond_length_from_segments(seg: np.ndarray | None,
                              reference: Reference | None = None) -> float | None:
    """The typical bond length (pt) of a drawing's segments, or None (see the module's
    description)."""
    if seg is None or not len(seg):
        return None
    candidates = [c for c in _candidates(seg) if c.connected >= MIN_CONNECTED]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c.score, c.core, c.bond), reverse=True)
    if reference is None:
        return candidates[0].bond

    def agrees(c: _Candidate) -> bool:
        expected = reference.bond_over_extent * c.extent
        return (expected > 0 and 1 / MOL_TOLERANCE <= c.bond / expected <= MOL_TOLERANCE
                and c.members >= MOL_MIN_SHARE * reference.bonds)

    for c in candidates:   # the best one that agrees with the copied structure
        if agrees(c):
            return c.bond
    return None


def bond_length_pt(svg_text: str, reference: Reference | None = None) -> float | None:
    """The typical bond length of an SVG drawing in points as it prints (at the size its
    width and height give), or None if no bonds can be measured. `reference`: the
    structure copied with it, as a cross-check (reference_from_mol). Never raises."""
    try:
        return bond_length_from_segments(svg_segments(svg_text), reference)
    except Exception:  # noqa: BLE001
        return None


def pdf_bond_length_pt(data: bytes, reference: Reference | None = None) -> float | None:
    """As bond_length_pt, for the first page of a PDF (ChemDraw's copy on macOS)."""
    try:
        return bond_length_from_segments(pdf_segments(data), reference)
    except Exception:  # noqa: BLE001
        return None


def measure_file(path: Path, reference: Reference | None = None) -> float | None:
    """The typical bond length (pt) of an SVG or PDF picture file, or None (another kind
    of picture, unreadable, or no bonds found)."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".svg":
            return bond_length_pt(path.read_text(encoding="utf-8", errors="replace"),
                                  reference)
        if ext == ".pdf":
            return pdf_bond_length_pt(path.read_bytes(), reference)
    except Exception:  # noqa: BLE001 — (unreadable file): no measurement
        return None
    return None


# ---------------------------------------------------------------------- printing

def same_scale(settings: dict) -> bool:
    """Project settings ▸ Structures: "Print all pasted structures at the same bond
    length" (on unless turned off)."""
    return settings.get(SETTING_KEY, True) is not False


def target_bond_pt(settings: dict) -> float:
    """The bond length every structure prints at: Project settings ▸ Structures."""
    from .chem import DrawingSettings
    return DrawingSettings.from_settings(settings).bond_pt


def _number(value) -> float | None:
    if isinstance(value, bool):   # (a hand-edited true/false is no length)
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


_measured: dict[tuple, float | None] = {}


def picture_bond(structure: dict, path: Path | None) -> tuple[float | None, bool]:
    """(bond length in pt, typed by the user?) of a compound's structure picture, as used
    for printing: the user's own value (0: none, print at its own size), else the stored
    measurement, else (a picture stored before measurements, or a hand-edited file)
    measured now. Never raises."""
    structure = structure if isinstance(structure, dict) else {}
    if OVERRIDE_KEY in structure:
        own = _number(structure.get(OVERRIDE_KEY))
        if own is not None:   # (kept within the Printed size dialog's limits)
            return (min(max(own, TYPED_RANGE[0]), TYPED_RANGE[1]) if own > 0 else None), True
    if MEASURED_KEY in structure:
        value = _number(structure.get(MEASURED_KEY))
        return (value if value is not None and value > 0 else None), False
    if path is None or Path(path).suffix.lower() not in VECTOR_PICTURES:
        return None, False
    try:
        st = Path(path).stat()
    except (OSError, ValueError):
        return None, False
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _measured:
        if len(_measured) > 500:
            _measured.clear()
        _measured[key] = measure_file(Path(path))
    return _measured[key], False


def picture_bond_pt(structure: dict, path: Path | None) -> float | None:
    """The bond length (pt) of a compound's structure picture, as used for printing (see
    picture_bond)."""
    return picture_bond(structure, path)[0]


def print_factor(settings: dict, bond_pt: float | None, typed: bool = False) -> float:
    """How much a picture is enlarged (or shrunk) so its bonds print at the project's bond
    length: 1.0 when the setting is off or nothing was measured. A MEASURED length giving
    an absurd factor (outside FACTOR_RANGE: probably not bonds) is not used; a length the
    user typed (`typed`) always is (§2.7)."""
    if bond_pt is None or bond_pt <= 0 or not same_scale(settings):
        return 1.0
    factor = target_bond_pt(settings) / bond_pt
    if typed:
        return factor if math.isfinite(factor) else 1.0
    return factor if FACTOR_RANGE[0] <= factor <= FACTOR_RANGE[1] else 1.0


def structure_print_factor(settings: dict, structure: dict, path: Path | None) -> float:
    """print_factor for a compound's structure picture (its "structure" value)."""
    if not same_scale(settings):
        return 1.0
    bond, typed = picture_bond(structure, path)
    return print_factor(settings, bond, typed)
