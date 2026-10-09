"""Audit fixes in the EMF → SVG converter (core/emf_svg.py) and the bond-length
measurement of pasted drawings (core/drawing_scale.py). Hand-made metafiles, SVGs and
PDFs, so these run on every system."""

import io
import math
import re
import struct

import numpy as np
import pytest

from si_assistant.core import drawing_scale as ds
from si_assistant.core import emf_svg as E
from tests.test_emf_svg import BOUNDS, dib24, emf, paths, rec, select


# ---------------------------------------------------------------------- SVG print scale

# A zigzag of six 20-unit bonds, drawn in user units starting at (10, 10).
_ZIGZAG = " ".join(f"{10 + 20 * math.cos(math.radians(30)) * i:.4f},"
                   f"{10 + 10 * (i % 2):.4f}" for i in range(7))


def _svg(attrs: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>'
            f'<polyline points="{_ZIGZAG}" stroke="black" stroke-width="1" fill="none"/>'
            f'</svg>')


# (attributes of <svg>, bond length as printed in pt)
PRINT_CASES = [
    ('width="120pt" height="40pt"', 15.0),                      # no viewBox, pt: 1 px = 0.75 pt
    ('width="40mm" height="15mm"', 15.0),                       # no viewBox, mm
    ('width="120" height="40"', 20.0),                          # no viewBox, no unit
    ('width="120px" height="40px"', 20.0),                      # no viewBox, px
    ('width="60pt" viewBox="0 0 120 40"', 20.0),                # only a width: its viewBox
    ('height="20pt" viewBox="0 0 120 40"', 40 / 3),             # only a height: fitted 2/3
    ('width="100%" height="100%" viewBox="0 0 120 40"', 20.0),  # percentages: its viewBox
    ('width="90pt" height="40" viewBox="0 0 120 40"', 15.0),    # mixed units
    ('width="60pt" height="20pt" viewBox="0 0 120 40"', 10.0),  # both: as before
    ('width="120pt" height="20pt" viewBox="0 0 120 40"', 10.0),  # other shape: fitted
    ('width="200px" height="auto" viewBox="0 0 120 40"', 12.0),  # 'auto': its viewBox
    ('width="+60pt" height="+20pt" viewBox="0 0 120 40"', 20.0),  # a sign: not a printed size
    ('width=" 60pt " height=" 20pt" viewBox="0 0 120 40"', 20 / 3),  # a space after: ignored
]


@pytest.mark.parametrize("attrs, printed", PRINT_CASES)
def test_svg_bond_is_measured_as_it_prints(attrs, printed):
    assert ds.bond_length_pt(_svg(attrs)) == pytest.approx(printed, rel=0.01)


def test_svg_stretched_without_keeping_its_shape():
    """preserveAspectRatio="none": each way its own scale (the bonds are no longer equal,
    but none of them is measured at the fitted scale)."""
    plain = ds.bond_length_pt(_svg('width="120pt" height="20pt" viewBox="0 0 120 40"'))
    stretched = ds.bond_length_pt(_svg('width="120pt" height="20pt" viewBox="0 0 120 40" '
                                       'preserveAspectRatio="none"'))
    assert plain == pytest.approx(10.0, rel=0.01) and stretched > plain * 1.5


@pytest.mark.parametrize("attrs, printed", PRINT_CASES)
def test_svg_bond_matches_the_printed_pdf(tmp_path, attrs, printed):
    """Printed with Typst at the size the compiled document gives the picture, the bond
    measured in the PDF is the bond measured in the SVG."""
    typst = pytest.importorskip("typst")
    from si_assistant.compile.document import svg_size_in
    text = _svg(attrs)
    w, _h = svg_size_in(text)
    (tmp_path / "a.svg").write_text(text, encoding="utf-8")
    src = tmp_path / "a.typ"
    src.write_text("#set page(width: 400pt, height: 400pt, margin: 0pt)\n"
                   f'#image("a.svg", width: {w:.4f}in)', encoding="utf-8")
    pdf = tmp_path / "a.pdf"
    typst.compile(str(src), output=str(pdf))
    assert ds.pdf_bond_length_pt(pdf.read_bytes()) == pytest.approx(
        ds.bond_length_pt(text), rel=0.02)


# ---------------------------------------------------------------------- PDF paths and colours

def _pdf(content: str, spaces: dict | None = None) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    if spaces:
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/ColorSpace"):
            DictionaryObject({NameObject(k): v for k, v in spaces.items()})})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _count(content: str, spaces: dict | None = None) -> int:
    return len(ds.pdf_segments(_pdf(content, spaces)))


def test_pdf_close_and_stroke_draws_the_closing_side():
    assert _count("10 10 m 60 10 l 35 50 l s") == 3              # as 'h S'
    assert _count("10 10 m 60 10 l 35 50 l h S") == 3
    assert _count("10 10 m 110 10 l 110 60 l 10 60 l s") == 0    # a frame: left out
    assert _count("10 10 m 110 10 l 110 60 l 10 60 l b") == 0


def _spaces():
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject
    cal = ArrayObject([NameObject("/CalRGB"), DictionaryObject(
        {NameObject("/WhitePoint"): ArrayObject([FloatObject(1)] * 3)})])
    spot = ArrayObject([NameObject("/Separation"), NameObject("/Spot"),
                        NameObject("/DeviceCMYK"), DictionaryObject()])
    lab = ArrayObject([NameObject("/Lab"), DictionaryObject()])
    return {"/Cs1": cal, "/Cs2": spot, "/Cs3": lab}


LINE = "10 10 m 60 10 l S"


@pytest.mark.parametrize("colour, counted", [
    ("1 1 1 RG", 0),                              # white: a bond-crossing gap
    ("/DeviceRGB CS 1 1 1 SC", 0),
    ("/DeviceGray CS 1 SCN", 0),
    ("/Cs1 CS 1 1 1 SCN", 0),                     # CalRGB (as Quartz writes it)
    ("/Cs1 CS 0 0 0 SC", 1),                      # black
    ("/Cs2 CS 1 SCN", 1),                         # a spot colour at full ink: not white
    ("/Cs3 CS 53 80 67 SC", 1),                   # Lab red
    ("/DeviceRGB CS 1 1 1 SC /DeviceRGB CS", 1),  # a new colour space starts at black
    ("0 0 SC", 1),                                # two values: not a colour we know
    ("q 1 1 1 RG Q", 1),                          # white only inside q … Q
    ("q /DeviceRGB CS 1 1 1 SC Q", 1),
])
def test_pdf_white_strokes_in_any_plain_colour_space(colour, counted):
    assert _count(f"{colour} {LINE}", _spaces()) == counted


def _pdf_with_form(page: str, form: str, spaces: dict) -> bytes:
    """A page that draws a Form XObject (/Fm0) with `page`; the form's own content
    `form`, with no resources of its own."""
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                               NameObject, NumberObject)
    writer = PdfWriter()
    pg = writer.add_blank_page(200, 200)
    fm = DecodedStreamObject()
    fm.set_data(form.encode("latin-1"))
    fm.update({NameObject("/Type"): NameObject("/XObject"),
               NameObject("/Subtype"): NameObject("/Form"),
               NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, 0, 200, 200)])})
    stream = DecodedStreamObject()
    stream.set_data(page.encode("latin-1"))
    pg[NameObject("/Contents")] = writer._add_object(stream)
    pg[NameObject("/Resources")] = DictionaryObject({
        NameObject("/ColorSpace"): DictionaryObject(
            {NameObject(k): v for k, v in spaces.items()}),
        NameObject("/XObject"): DictionaryObject({NameObject("/Fm0"): writer._add_object(fm)})})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize("page, form, counted", [
    ("/Cs2 CS /Fm0 Do", "1 SC " + LINE, 1),           # a full-ink spot colour: not white
    ("/Cs3 CS /Fm0 Do", "100 0 0 SC " + LINE, 1),      # Lab
    ("/DeviceRGB CS /Fm0 Do", "1 1 1 SC " + LINE, 0),  # white in plain RGB
    ("1 1 1 RG /Fm0 Do", LINE, 0),                     # white set before the form
    ("1 1 1 rg /Fm0 Do", "10 10 m 60 10 l 60 11 l 10 11 l h f", 0),
    ("/Fm0 Do", LINE, 1),
    ("/Cs2 CS /Fm0 Do", "q /DeviceRGB CS 1 1 1 SC Q 1 SC " + LINE, 1),
])
def test_pdf_form_takes_on_the_colour_set_before_it(page, form, counted):
    """A Form XObject drawn with 'Do' starts with the colour and colour space of the page
    at that point (spot colours, palettes and Lab are never read as white)."""
    assert len(ds.pdf_segments(_pdf_with_form(page, form, _spaces()))) == counted


def test_pdf_white_fills_set_with_sc():
    bar = "10 10 m 60 10 l 60 11 l 10 11 l h f"   # a bond drawn as a thin filled shape
    assert _count(bar) == 1
    assert _count("/DeviceRGB cs 1 1 1 sc " + bar) == 0
    assert _count("/Cs2 cs 1 scn " + bar, _spaces()) == 1


# ---------------------------------------------------------------------- long runs of curves

def _absolute(d: str) -> list[tuple[int, int]]:
    """The absolute points of path data written by _PathData (M, relative l and c)."""
    out, cur = [], None
    for cmd, args in re.findall(r"([MlcLz])([^MlcLz]*)", d):
        v = [int(x) for x in re.findall(r"-?\d+", args)]
        if cmd in "ML":
            cur = (v[0], v[1])
            out.append(("M" if cmd == "M" else "L", cur))
        elif cmd == "c":
            for k in range(0, len(v), 6):
                pts = [(cur[0] + v[k + j], cur[1] + v[k + j + 1]) for j in (0, 2, 4)]
                out.extend(("c", p) for p in pts)
                cur = pts[2]
    return out


def test_a_long_run_of_curves_is_split_into_pieces():
    n = 12000
    rng = np.random.default_rng(1)
    pts = np.cumsum(rng.integers(-50, 50, size=(3 * n, 2)), axis=0).astype(float) + 1e5
    p = E._PathData()
    p.move(1e5, 1e5)
    p.curves(pts)
    pieces = p.pieces()
    assert len(pieces) >= 3 and all(s <= E.PIECE_SEGMENTS for _d, s in pieces)
    # Each piece starts where the one before ended, and together they are the curve.
    joined = []
    for k, (d, _s) in enumerate(pieces):
        points = _absolute(d)
        assert points[0][0] == "M"
        if k:
            assert points[0][1] == joined[-1]
        joined.extend(q for kind, q in points if kind == "c")
    assert joined == [tuple(q) for q in np.rint(pts).astype(int).tolist()]
    # A short run is still one part, as before.
    short = E._PathData()
    short.move(0, 0)
    short.curves(pts[:30] - 1e5)
    assert len(short.parts) == 2 and short.text().count("c") == 1


def test_a_metafile_with_a_long_polybezier_keeps_every_path_small():
    n = 12000
    pts = [(0, 0)] + [((i * 3 + j) % 2000, (i * 7 + j * 5) % 800)
                      for i in range(n) for j in range(3)]
    record = rec(85, BOUNDS + struct.pack("<I", len(pts))
                 + b"".join(struct.pack("<hh", *q) for q in pts))   # POLYBEZIER16
    c = E.convert(emf(record))
    assert c.ok
    ds_ = paths(c.svg)
    assert len(ds_) >= 3
    for d in ds_:
        assert sum(1 for kind, _q in _absolute(d) if kind == "c") <= E.PIECE_SEGMENTS


# ---------------------------------------------------------------------- pattern brushes

def _pattern_brush(handle: int, colour: tuple) -> bytes:
    info, bits = dib24([[colour] * 8] * 8)
    off_bmi = 32
    return rec(94, struct.pack("<6I", handle, 0, off_bmi, len(info), off_bmi + len(info),
                               len(bits)) + info + bits)


def test_each_pattern_brush_keeps_its_own_picture():
    """Brushes created, used and deleted one after another: each fill shows its own
    picture (a deleted brush's picture must not be taken for the next one's)."""
    records = []
    for k in range(60):
        records += [_pattern_brush(1, (k * 4, 255 - k * 4, 100)), select(1),
                    rec(43, struct.pack("<4i", k * 10, 0, k * 10 + 8, 8)),   # RECTANGLE
                    select(0x80000000), rec(40, struct.pack("<I", 1))]       # DELETEOBJECT
    # One brush used for two fills: one picture.
    records += [_pattern_brush(2, (1, 2, 3)), select(2),
                rec(43, struct.pack("<4i", 0, 20, 8, 28)),
                rec(43, struct.pack("<4i", 10, 20, 18, 28))]
    c = E.convert(emf(*records))
    assert c.ok
    defs = re.findall(r'<pattern id="(p\d+)"[^>]*>.*?xlink:href="([^"]+)"', c.svg)
    assert len(defs) == 61 and len({uri for _pid, uri in defs}) == 61
    used = re.findall(r'fill="url\(#(p\d+)\)"', c.svg)
    assert len(used) == 62 and len(set(used)) == 61 and used[-1] == used[-2]


# ---------------------------------------------------------------------- LINETO records

def _parts(p: E._PathData):
    return p.parts, p.cur, p.start, p.segments


@pytest.mark.parametrize("points", [
    [(2.5, -2.5), (3.5, 0.5), (-1.5, 4.49)],     # rounding halves (to even, as NumPy)
    [(10, 10), (10.2, 9.8), (11, 10)],            # a line of no length is left out
    [(-5, 7), (8, -3), (8, -3), (0, 0)],
])
def test_one_point_line_is_the_same_as_lines(points):
    for start in (None, (1.0, 1.0)):
        a, b = E._PathData(), E._PathData()
        if start:
            a.move(*start)
            b.move(*start)
        for x, y in points:
            a.lines(np.array([[x, y]]))
            b.line(x, y)
        assert _parts(a) == _parts(b)


def test_one_point_lines_split_at_the_chunk_size():
    a, b = E._PathData(), E._PathData()
    for p in (a, b):
        p.move(0, 0)
    for k in range(1, E.CHUNK_SEGMENTS + 3):
        a.lines(np.array([[k, k % 2]], float))
        b.line(float(k), float(k % 2))
    assert _parts(a) == _parts(b) and len(b.parts) == 3


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 2 * E.MAX_COORD])
def test_one_point_line_rejects_damaged_coordinates(bad):
    p = E._PathData()
    p.move(0, 0)
    with pytest.raises(ValueError):
        p.line(bad, 0)
    with pytest.raises(ValueError):
        E._PathData().move(0, bad)


def _move_to(x, y):
    return rec(27, struct.pack("<ii", x, y))


def _line_to(x, y):
    return rec(54, struct.pack("<ii", x, y))


def test_lineto_records_give_the_same_drawing():
    PX = 25
    alone = E.convert(emf(_move_to(0, 0), _line_to(10, 0), _line_to(10, 0),
                          _line_to(10, 10)))
    assert alone.ok and alone.strokes == 2
    assert paths(alone.svg) == [f"M0 0l{10 * PX} 0M{10 * PX} 0l0 {10 * PX}"]
    inside = E.convert(emf(rec(59), _move_to(0, 0), _line_to(10, 0), _line_to(10, 0),
                           _line_to(10, 10), rec(60), rec(64, BOUNDS)))
    assert inside.ok and paths(inside.svg) == [f"M0 0l{10 * PX} 0 0 {10 * PX}"]


def test_many_lineto_records_convert_quickly():
    import time
    records = [_move_to(0, 400)] + [_line_to(k % 2000, 400 + (k * 37) % 300)
                                   for k in range(1, 20001)]
    t = time.perf_counter()
    c = E.convert(emf(*records))
    assert c.ok and c.strokes == 20000
    assert time.perf_counter() - t < 2.0
