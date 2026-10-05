"""The EMF → SVG converter (core/emf_svg.py, SPEC §13l.1), fed hand-made metafile records,
so these run on every system (tests with real Windows drawing are in
test_spectrum_paste.py)."""

import re
import struct
import zlib

import numpy as np
import pytest

from si_assistant.core import emf_svg as E

# The reference device of these metafiles: 1000 × 800 pixels on 250 × 200 mm, so one
# device pixel is 0.25 mm = 25 SVG units (0.01 mm).
PX = 25


def header(frame=(0, 0, 5000, 2000), device_px=(1000, 800), device_mm=(250, 200)) -> bytes:
    return (struct.pack("<II", 1, 108) + struct.pack("<4i", 0, 0, 0, 0)
            + struct.pack("<4i", *frame)
            + struct.pack("<IIII", E.EMF_SIGNATURE, 0x10000, 0, 0) + struct.pack("<HH", 0, 0)
            + struct.pack("<III", 0, 0, 0) + struct.pack("<2i", *device_px)
            + struct.pack("<2i", *device_mm) + struct.pack("<III", 0, 0, 0)
            + struct.pack("<2i", device_mm[0] * 1000, device_mm[1] * 1000))


def rec(kind: int, payload: bytes = b"") -> bytes:
    payload += b"\0" * (-len(payload) % 4)
    return struct.pack("<II", kind, 8 + len(payload)) + payload


EOF = rec(14, struct.pack("<III", 0, 0, 20))
BOUNDS = struct.pack("<4i", 0, 0, 0, 0)


def emf(*records: bytes, **kw) -> bytes:
    return header(**kw) + b"".join(records) + EOF


def polyline16(points, kind=87) -> bytes:
    return rec(kind, BOUNDS + struct.pack("<I", len(points)) +
               b"".join(struct.pack("<hh", *p) for p in points))


def polyline(points, kind=4) -> bytes:
    return rec(kind, BOUNDS + struct.pack("<I", len(points)) +
               b"".join(struct.pack("<ii", *p) for p in points))


def create_pen(handle, width=0, color=0, style=0) -> bytes:
    return rec(38, struct.pack("<IIiiI", handle, style, width, 0, color))


def create_brush(handle, color, style=0) -> bytes:
    return rec(39, struct.pack("<4I", handle, style, color, 0))


def select(handle) -> bytes:
    return rec(37, struct.pack("<I", handle))


def font(handle, height=-20, face="Arial", escapement=0, weight=400, italic=0,
         charset=0) -> bytes:
    name = face.encode("utf-16-le")[:64].ljust(64, b"\0")
    return rec(82, struct.pack("<I5i8B", handle, height, 0, escapement, escapement, weight,
                               italic, 0, 0, charset, 0, 0, 0, 0) + name)


def text_out(x, y, s: str, dx=None, options=0, rcl=(0, 0, 0, 0), mode=1, glyphs=None) -> bytes:
    n = len(glyphs) if glyphs is not None else len(s)
    off_string = 76
    raw = (struct.pack(f"<{n}H", *glyphs) if glyphs is not None else s.encode("utf-16-le"))
    raw += b"\0" * (-len(raw) % 4)
    off_dx = off_string + len(raw)
    dx = dx if dx is not None else [10] * n
    body = (BOUNDS + struct.pack("<Iff", mode, 1.0, 1.0) + struct.pack("<iiIII", x, y, n,
                                                                         off_string, options)
            + struct.pack("<4i", *rcl) + struct.pack("<I", off_dx))
    return rec(84, body + raw + struct.pack(f"<{n}i", *dx))


def set_attr(kind, value) -> bytes:
    return rec(kind, struct.pack("<I", value))


def dib24(pixels) -> tuple[bytes, bytes]:
    """BITMAPINFOHEADER and bottom-up 24-bit bits of rows of (r, g, b), top row first."""
    h, w = len(pixels), len(pixels[0])
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, 0, 0, 0, 0, 0)
    stride = (w * 3 + 3) // 4 * 4
    bits = b""
    for row in reversed(pixels):
        line = b"".join(bytes((b, g, r)) for r, g, b in row)
        bits += line + b"\0" * (stride - len(line))
    return info, bits


def stretch_dibits(x, y, cx, cy, info, bits, w, h, rop=E.SRCCOPY) -> bytes:
    off_bmi = 80
    off_bits = off_bmi + len(info)
    body = (BOUNDS + struct.pack("<6i", x, y, 0, 0, w, h)
            + struct.pack("<6I", off_bmi, len(info), off_bits, len(bits), 0, rop)
            + struct.pack("<ii", cx, cy))
    return rec(81, body + info + bits)


def paths(svg: str) -> list[str]:
    return re.findall(r'<path d="([^"]*)"', svg)


# ---------------------------------------------------------------------- size and coordinates

def test_size_viewbox_and_print_size():
    from si_assistant.compile.document import svg_size_in
    c = E.convert(emf(polyline16([(10, 10), (20, 10)])))
    assert c.ok and (c.width_mm, c.height_mm) == (50, 20)
    assert 'width="141.732pt" height="56.693pt" viewBox="0 0 5000 2000"' in c.svg
    assert svg_size_in(c.svg) == pytest.approx((50 / 25.4, 20 / 25.4), abs=1e-4)


def test_device_units_become_hundredths_of_a_mm():
    c = E.convert(emf(polyline16([(10, 10), (20, 10), (20, 30)])))
    assert paths(c.svg) == [f"M{10 * PX} {10 * PX}l{10 * PX} 0 0 {20 * PX}"]
    assert c.strokes == 1 and not c.unsupported


def test_frame_origin_is_the_top_left_corner():
    c = E.convert(emf(polyline16([(10, 10), (20, 10)]), frame=(100, 200, 5100, 2200)))
    assert paths(c.svg)[0].startswith(f"M{10 * PX - 100} {10 * PX - 200}")


@pytest.mark.parametrize("records, expected", [
    # MM_HIMETRIC: logical units are 0.01 mm with y pointing up.
    ([set_attr(17, E.MM_HIMETRIC), polyline([(1000, -500), (2000, -500)])], "M1000 500"),
    # MM_ANISOTROPIC: window 100 × 100 → viewport 200 × 400 device pixels.
    ([set_attr(17, E.MM_ANISOTROPIC), rec(9, struct.pack("<ii", 100, 100)),
      rec(11, struct.pack("<ii", 200, 400)), polyline([(10, 10), (20, 10)])],
     f"M{20 * PX} {40 * PX}"),
    # MM_ISOTROPIC keeps x and y alike: the larger scale is cut down to the smaller.
    ([set_attr(17, E.MM_ISOTROPIC), rec(9, struct.pack("<ii", 100, 100)),
      rec(11, struct.pack("<ii", 200, 400)), polyline([(10, 10), (20, 10)])],
     f"M{20 * PX} {20 * PX}"),
    # Window and viewport origins.
    ([set_attr(17, E.MM_ANISOTROPIC), rec(10, struct.pack("<ii", 5, 5)),
      rec(12, struct.pack("<ii", 100, 0)), polyline([(10, 10), (20, 10)])],
     f"M{105 * PX} {5 * PX}"),
    # SCALEWINDOWEXTEX halves the window: everything twice as large.
    ([set_attr(17, E.MM_ANISOTROPIC), rec(32, struct.pack("<4i", 1, 2, 1, 2)),
      polyline([(10, 10), (20, 10)])], f"M{20 * PX} {20 * PX}"),
])
def test_mapping_modes(records, expected):
    c = E.convert(emf(*records))
    assert paths(c.svg)[0].startswith(expected), paths(c.svg)


def test_world_transform_save_and_restore():
    scale2 = rec(35, struct.pack("<6f", 2, 0, 0, 2, 0, 0))
    identity = rec(36, struct.pack("<6fI", 1, 0, 0, 1, 0, 0, 1))
    c = E.convert(emf(scale2, rec(33), identity, polyline16([(10, 10), (20, 10)]),
                      rec(34, struct.pack("<i", -1)), polyline16([(10, 10), (20, 10)])))
    first, second = paths(c.svg)[0].split("M")[1:]
    assert first.startswith(f"{10 * PX} {10 * PX}")    # (inside SaveDC: identity)
    assert second.startswith(f"{20 * PX} {20 * PX}")   # (restored: twice the size)


def test_modify_world_transform_multiplies():
    move = rec(36, struct.pack("<6fI", 1, 0, 0, 1, 100, 0, 4))      # MWT_SET
    twice = rec(36, struct.pack("<6fI", 2, 0, 0, 2, 0, 0, 2))       # left-multiply
    c = E.convert(emf(move, twice, polyline16([(10, 10), (20, 10)])))
    assert paths(c.svg)[0].startswith(f"M{120 * PX} {20 * PX}")


# ---------------------------------------------------------------------- pens and brushes

def test_pens_cosmetic_wide_dashed_and_null():
    c = E.convert(emf(create_pen(1, 0, 0x0000FF), select(1), polyline16([(0, 0), (10, 0)]),
                      create_pen(2, 4, 0x00FF00), select(2), polyline16([(0, 5), (10, 5)]),
                      create_pen(3, 0, 0, style=E.PS_DASH), select(3),
                      polyline16([(0, 9), (10, 9)]),
                      select(E.STOCK_BIT | 8), polyline16([(0, 12), (10, 12)])))
    assert f'stroke="#ff0000" stroke-width="{E.HAIRLINE:.2f}"' in c.svg     # red, hairline
    assert f'stroke="#00ff00" stroke-width="{4 * PX}"' in c.svg             # 4 px wide
    # GDI's dashes, in pixels of the output: here hairline-sized (see COSMETIC_PIXEL)
    px = E.COSMETIC_PIXEL
    assert (f'class="cosmetic-dash" stroke-dasharray="{E._n(18 * px)} {E._n(6 * px)}"'
            in c.svg)
    assert c.svg.count("<path") == 3                                         # (null pen)


def test_lines_with_the_same_pen_are_joined_into_one_path():
    c = E.convert(emf(*[polyline16([(0, y), (10, y)]) for y in range(5)]))
    assert len(paths(c.svg)) == 1 and paths(c.svg)[0].count("M") == 5


def test_filled_rectangle_and_polygon_fill_rule():
    c = E.convert(emf(create_brush(1, 0x0000FF), select(1),
                      rec(43, struct.pack("<4i", 10, 10, 20, 20)),
                      set_attr(19, E.WINDING), polyline16([(0, 0), (10, 0), (5, 5)], kind=86)))
    rect, triangle = re.findall(r"<path [^>]*/>", c.svg)
    assert 'fill="#ff0000"' in rect and 'stroke="#000000"' in rect   # default black pen
    assert rect.count("z") == 1 and 'fill-rule="evenodd"' in rect    # ALTERNATE (default)
    assert 'fill-rule' not in triangle                                # WINDING = nonzero
    assert c.fills == 2 and c.dark_fills == 2


def test_hatched_brush_becomes_a_pattern():
    c = E.convert(emf(create_brush(1, 0x00FF00, style=E.BS_HATCHED), select(1),
                      rec(43, struct.pack("<4i", 10, 10, 20, 20))))
    assert "<pattern" in c.svg and 'fill="url(#p1)"' in c.svg


def _hatch_and_dash(**screen) -> tuple[list[str], list[str]]:
    c = E.convert(emf(create_brush(1, 0x00FF00, style=E.BS_HATCHED), select(1),
                      rec(43, struct.pack("<4i", 10, 10, 20, 20)),
                      create_pen(2, 0, 0, style=E.PS_DASH), select(2),
                      polyline16([(0, 30), (100, 30)]), **screen))
    return (re.findall(r"<pattern .*?</pattern>", c.svg),
            re.findall(r'class="cosmetic-dash" stroke-dasharray="[^"]+"', c.svg))


@pytest.mark.parametrize("device_px, device_mm", [
    ((1366, 768), (309, 174)),      # a 96-dpi-ish laptop
    ((1920, 1080), (344, 194)),     # 142 dpi
    ((3840, 2160), (597, 336)),     # 4K, 163 dpi
    ((2560, 1600), (286, 179)),     # 227 dpi
])
def test_hatches_and_thin_dashes_do_not_depend_on_the_screen(device_px, device_mm):
    """Windows draws hatches (8 pixels apart) and the dashes of one-pixel pens in the
    pixels of whatever it draws on, not in those of the screen the metafile was made on
    (its header's reference device). The SVG gives them one fixed size: hatches as on a
    96-dpi screen, dashes in hairline-sized pixels."""
    patterns, dashes = _hatch_and_dash(device_px=device_px, device_mm=device_mm)
    assert (patterns, dashes) == _hatch_and_dash()
    size = E._n(E.HATCH_CELL * E.HATCH_PIXEL)
    assert patterns == [f'<pattern id="p1" class="hatch hatch-0" patternUnits="userSpaceOnUse" '
                        f'width="{size}" height="{size}"><rect width="{size}" height="{size}" '
                        f'fill="#ffffff"/><path d="M0 {E._n(E.HATCH_CELL * E.HATCH_PIXEL / 2)}'
                        f'H{size}" stroke="#00ff00" stroke-width="{E._n(E.HATCH_PIXEL)}" '
                        f'fill="none"/></pattern>']
    assert abs(E.HATCH_CELL * E.HATCH_PIXEL - 211.67) < 0.01        # 2.12 mm apart
    assert len(dashes) == 1


def test_output_pixel_drawing_can_be_redrawn_at_another_resolution():
    """For the self-check: hatches and cosmetic dashes at Windows' own pixel size (here
    1/300 inch = 8.47 units), all else unchanged."""
    c = E.convert(emf(create_brush(1, 0x00FF00, style=E.BS_HATCHED), select(1),
                      set_attr(18, E.TRANSPARENT), rec(43, struct.pack("<4i", 10, 10, 20, 20)),
                      create_pen(2, 0, 0, style=E.PS_DOT), select(2),
                      polyline16([(0, 30), (100, 30)])))
    pixel = 2540 / 300
    again = E.rescale_output_pixels(c.svg, pixel)
    size = E._n(8 * pixel)
    assert (f'class="hatch hatch-0" patternUnits="userSpaceOnUse" width="{size}" '
            f'height="{size}"><path d="M0 {E._n(4 * pixel)}H{size}" stroke="#00ff00" '
            f'stroke-width="{E._n(pixel)}" fill="none"/></pattern>') in again     # no background
    assert f'stroke-dasharray="{E._n(3 * pixel, 3)} {E._n(3 * pixel, 3)}"' in again
    strip = re.compile(r'<pattern .*?</pattern>|stroke-dasharray="[^"]*"')
    assert strip.sub("", again) == strip.sub("", c.svg)
    assert E.rescale_output_pixels(again, E.HATCH_PIXEL).count("<pattern") == 1


def test_ellipse_arc_and_round_rect_are_curves():
    c = E.convert(emf(rec(42, struct.pack("<4i", 0, 0, 40, 20)),
                      rec(44, struct.pack("<6i", 50, 0, 90, 20, 10, 10)),
                      rec(45, struct.pack("<8i", 0, 30, 40, 60, 40, 45, 0, 45))))
    assert c.ok and c.svg.count("c") >= 3 and c.fills == 2 and c.strokes == 3


def test_path_bracket_filled_and_stroked():
    c = E.convert(emf(rec(59), rec(27, struct.pack("<ii", 0, 0)),
                      rec(54, struct.pack("<ii", 10, 0)), rec(54, struct.pack("<ii", 10, 10)),
                      rec(61), rec(60), rec(63, BOUNDS)))
    (d,) = paths(c.svg)
    assert d == f"M0 0l{10 * PX} 0 0 {10 * PX}z" and c.fills == 1 and c.strokes == 1


def test_rop2_modes():
    xor = E.convert(emf(set_attr(20, 7), polyline16([(0, 0), (10, 0)])))
    assert not xor.ok and xor.unsupported["SETROP2 mode 7"] == 1
    nop = E.convert(emf(set_attr(20, E.R2_NOP), polyline16([(0, 0), (10, 0)])))
    assert nop.ok and "<path" not in nop.svg


# ---------------------------------------------------------------------- clipping

def test_clip_rect_applies_until_restore():
    c = E.convert(emf(rec(33), rec(30, struct.pack("<4i", 0, 0, 100, 50)),
                      polyline16([(0, 0), (200, 60)]), rec(34, struct.pack("<i", -1)),
                      polyline16([(0, 70), (200, 70)])))
    assert f'<clipPath id="c1"><rect x="0" y="0" width="{100 * PX}" height="{50 * PX}"/>' \
           in c.svg
    clipped, free = c.svg.split('<g clip-path="url(#c1)">')[1].split("</g>", 1)
    assert "<path" in clipped and "<path" in free


def test_clip_rects_intersect_and_exclude():
    c = E.convert(emf(rec(30, struct.pack("<4i", 0, 0, 100, 50)),
                      rec(30, struct.pack("<4i", 20, 10, 200, 200)),
                      rec(29, struct.pack("<4i", 30, 20, 40, 30)),
                      polyline16([(0, 0), (200, 60)])))
    assert f'<rect x="{20 * PX}" y="{10 * PX}" width="{80 * PX}" height="{40 * PX}"/>' in c.svg
    assert 'clip-rule="evenodd"' in c.svg and c.svg.count("clip-path=") == 2


def test_clip_region_device_units_and_reset():
    region = (struct.pack("<4I", 32, 1, 1, 16) + struct.pack("<4i", 0, 0, 40, 40)
              + struct.pack("<4i", 0, 0, 40, 40))
    c = E.convert(emf(rec(75, struct.pack("<II", len(region), E.RGN_COPY) + region),
                      polyline16([(0, 0), (100, 0)]),
                      rec(75, struct.pack("<II", 0, E.RGN_COPY)),
                      polyline16([(0, 5), (100, 5)])))
    assert f'width="{40 * PX}" height="{40 * PX}"' in c.svg
    assert c.svg.count("<path") == 2   # (the second line is outside the clip)


def test_clip_path():
    c = E.convert(emf(rec(59), rec(27, struct.pack("<ii", 0, 0)),
                      rec(54, struct.pack("<ii", 10, 0)), rec(54, struct.pack("<ii", 5, 10)),
                      rec(61), rec(60), set_attr(67, E.RGN_COPY),
                      rec(43, struct.pack("<4i", 0, 0, 10, 10))))
    assert '<clipPath id="c1"><path d="M0 0l250 0-125 250z" clip-rule="evenodd"/>' in c.svg
    assert '<g clip-path="url(#c1)">\n<path d="M0 0l250 0 0 250-250 0z"' in c.svg


# ---------------------------------------------------------------------- text

def test_text_kept_as_text_at_windows_positions():
    c = E.convert(emf(font(1, -20, "Arial"), select(1), set_attr(18, E.TRANSPARENT),
                      set_attr(22, E.TA_BASELINE), set_attr(24, 0x00FF0000),
                      text_out(100, 50, "7.26", dx=[11, 5, 11, 11])))
    (t,) = re.findall(r"<text [^>]*>[^<]*</text>", c.svg)
    assert ">7.26</text>" in t and 'x="0 11 16 27"' in t and 'y="0"' in t
    assert 'font-size="20"' in t and 'fill="#0000ff"' in t
    assert "font-family=\"'Arial', 'Helvetica', 'Liberation Sans'" in t and "sans-serif" in t
    assert f'transform="matrix({PX} 0 0 {PX} {100 * PX} {50 * PX})"' in t
    assert c.texts == 1 and c.ok


def test_text_alignment_right_centre_top_bottom():
    def text_svg(align):
        c = E.convert(emf(font(1, -20), select(1), set_attr(18, E.TRANSPARENT),
                          set_attr(22, align), text_out(0, 0, "ab", dx=[10, 10])))
        return re.search(r"<text [^>]*>", c.svg).group(0)
    assert 'x="-20 -10"' in text_svg(E.TA_RIGHT | E.TA_BASELINE)
    assert 'x="-10 0"' in text_svg(E.TA_CENTER | E.TA_BASELINE)
    ascent, descent = E.DEFAULT_METRICS["sans"]
    assert f'y="{E._n(20 * ascent)}"' in text_svg(0)                     # TA_TOP
    assert f'y="{E._n(-20 * descent)}"' in text_svg(E.TA_BOTTOM)


def test_rotated_text_from_escapement():
    c = E.convert(emf(font(1, -20, escapement=900), select(1), set_attr(18, E.TRANSPARENT),
                      text_out(10, 10, "y")))
    assert f'transform="matrix(0 -{PX} {PX} 0 {10 * PX} {10 * PX})"' in c.svg


def test_opaque_background_and_eto_opaque_fill():
    c = E.convert(emf(font(1, -20), select(1), set_attr(25, 0x00FFFF),
                      text_out(0, 50, "a", options=E.ETO_OPAQUE, rcl=(0, 0, 40, 40)),
                      text_out(0, 90, "b")))
    assert c.svg.count('fill="#ffff00"') == 2    # the ETO_OPAQUE box, then "b"'s background


def test_text_with_special_characters_is_escaped():
    c = E.convert(emf(font(1, -20), select(1), set_attr(18, E.TRANSPARENT),
                      text_out(0, 0, "a<b&c δ")))
    assert ">a&lt;b&amp;c δ</text>" in c.svg


def test_symbol_font_letters_become_greek():
    c = E.convert(emf(font(1, -20, "Symbol", charset=2), select(1),
                      set_attr(18, E.TRANSPARENT), text_out(0, 0, "d")))
    assert ">δα</text>" in c.svg and "'Symbol'" not in c.svg


def test_glyph_numbers_need_help_to_become_text():
    record = text_out(0, 0, "", options=E.ETO_GLYPH_INDEX, glyphs=[36, 37])
    plain = E.convert(emf(font(1, -20), select(1), set_attr(18, E.TRANSPARENT), record))
    assert not plain.ok and plain.unsupported["EXTTEXTOUTW (glyph numbers)"] == 1

    class Helper:
        def metrics(self, f):
            return None

        def glyph_text(self, f, glyphs):
            return "".join(chr(g + 29) for g in glyphs)   # 36 → "A"

        def glyph_outline(self, f, glyphs, xs, em):
            return None

    helped = E.convert(emf(font(1, -20), select(1), set_attr(18, E.TRANSPARENT), record),
                       Helper())
    assert helped.ok and ">AB</text>" in helped.svg


# ---------------------------------------------------------------------- pictures

def test_stretch_dibits_embeds_a_png_and_counts_as_the_whole_drawing():
    info, bits = dib24([[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]])
    c = E.convert(emf(stretch_dibits(0, 0, 200, 80, info, bits, 2, 2)))
    assert c.ok and len(c.images) == 1 and "data:image/png;base64," in c.svg
    placed = c.single_bitmap()
    assert placed is not None and placed.rect() == (0, 0, 200 * PX, 80 * PX)
    px = placed.bitmap.pixels
    assert tuple(px[0, 0]) == (255, 0, 0) and tuple(px[1, 0]) == (0, 0, 255)   # top row first


def test_a_picture_with_drawing_on_it_is_not_a_bitmap_only_copy():
    info, bits = dib24([[(255, 0, 0)]])
    c = E.convert(emf(stretch_dibits(0, 0, 200, 80, info, bits, 1, 1),
                      polyline16([(0, 0), (10, 10)])))
    assert c.single_bitmap() is None
    small = E.convert(emf(stretch_dibits(0, 0, 50, 20, info, bits, 1, 1)))
    assert small.single_bitmap() is None   # (covers only part of the frame)


def test_unsupported_raster_operation_is_counted():
    info, bits = dib24([[(255, 0, 0)]])
    c = E.convert(emf(stretch_dibits(0, 0, 10, 10, info, bits, 1, 1, rop=0x008800C6)))
    assert not c.ok and "STRETCHDIBITS (raster operation 0x8800c6)" in c.unsupported


@pytest.mark.parametrize("depth", [1, 4, 8])
def test_palette_dibs(depth):
    palette = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)][: min(4, 1 << depth)]
    info = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, depth, 0, 0, 0, 0, len(palette), 0)
    info += b"".join(bytes((b, g, r, 0)) for r, g, b in palette)
    if depth == 1:
        bits = bytes([0b01000000, 0, 0, 0])
    elif depth == 4:
        bits = bytes([0x01, 0, 0, 0])
    else:
        bits = bytes([0, 1, 0, 0])
    bm = E.decode_dib(info, bits)
    assert bm.pixels.shape == (1, 2, 3)
    assert tuple(bm.pixels[0, 0]) == (0, 0, 0) and tuple(bm.pixels[0, 1]) == (255, 0, 0)


def test_16_and_32_bit_dibs_with_bit_fields():
    info16 = struct.pack("<IiiHHIIiiII", 40, 1, -1, 1, 16, 0, 0, 0, 0, 0, 0)
    bm = E.decode_dib(info16, struct.pack("<HH", 0x7C00, 0))
    assert tuple(bm.pixels[0, 0]) == (255, 0, 0)
    info32 = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 32, 3, 0, 0, 0, 0, 0) + \
        struct.pack("<III", 0x0000FF, 0x00FF00, 0xFF0000)
    bm = E.decode_dib(info32, struct.pack("<I", 0x00336699))
    assert tuple(bm.pixels[0, 0]) == (0x99, 0x66, 0x33)
    assert E.decode_dib(struct.pack("<IiiHHI", 40, 1, 1, 1, 8, 1) + b"\0" * 20, b"\0" * 4) \
        is None   # (RLE: not decoded, the paste falls back to a picture)


def test_png_writer_records_resolution(tmp_path):
    from si_assistant.compile.qt_images import png_dpi
    pixels = np.zeros((3, 4, 3), np.uint8)
    pixels[1, 2] = (10, 20, 30)
    data = E.png_bytes(pixels, (11811, 11811))
    (tmp_path / "a.png").write_bytes(data)
    assert png_dpi(tmp_path / "a.png") == pytest.approx(300, abs=0.1)
    idat = data[data.index(b"IDAT") + 4:]
    raw = zlib.decompressobj().decompress(idat)
    assert raw[1 * 13 + 1 + 2 * 3: 1 * 13 + 1 + 3 * 3] == bytes((10, 20, 30))


def test_bitblt_without_a_picture_fills_with_the_brush():
    body = (BOUNDS + struct.pack("<4iIii", 0, 0, 10, 10, E.PATCOPY, 0, 0)
            + struct.pack("<6f", 1, 0, 0, 1, 0, 0) + struct.pack("<6I", 0, 0, 0, 0, 0, 0))
    c = E.convert(emf(create_brush(1, 0x00FF00), select(1), rec(76, body)))
    assert c.ok and 'fill="#00ff00"' in c.svg


# ---------------------------------------------------------------------- robustness

def test_unknown_and_unsupported_records_are_counted_not_fatal():
    c = E.convert(emf(rec(118, b"\0" * 40), rec(250, b"\0" * 8), polyline16([(0, 0), (9, 9)])))
    assert c.unsupported["GRADIENTFILL"] == 1 and c.unsupported["record 250"] == 1
    assert not c.ok and c.strokes == 1 and "<path" in c.svg


def test_damaged_record_is_counted():
    bad = rec(87, BOUNDS + struct.pack("<I", 1000) + b"\0" * 8)   # 1000 points, 2 present
    c = E.convert(emf(bad, polyline16([(0, 0), (9, 9)])))
    assert c.unsupported["damaged POLYLINE16"] == 1 and c.strokes == 1


@pytest.mark.parametrize("cut", [0, 5, 60, 107, 120, 150, -3])
def test_cut_off_data_never_crashes(cut):
    data = emf(font(1, -20), select(1), text_out(0, 0, "abc"), polyline16([(0, 0), (9, 9)]))
    c = E.convert(data[:cut])
    assert c.error


def test_not_a_metafile():
    assert E.convert(b"").error == "This is not an Enhanced Metafile."
    assert E.convert(b"%PDF-1.7" + b"\0" * 200).error
    rng = np.random.default_rng(1)
    for _ in range(50):   # random bytes after a good header: reported, never a crash
        junk = header() + rng.integers(0, 256, 300, dtype=np.uint8).tobytes()
        c = E.convert(junk)
        assert isinstance(c, E.Conversion) and (c.error or c.unsupported or c.records)


def test_emf_plus_comments_are_noted_and_skipped():
    plus = rec(70, struct.pack("<I", 16) + b"EMF+" + struct.pack("<HHII", 0x4001, 1, 28, 16))
    c = E.convert(emf(plus, polyline16([(0, 0), (9, 9)])))
    assert c.emf_plus and c.ok and c.strokes == 1


def test_long_lines_are_split_for_qt():
    n = 50_000
    points = [(i % 30000, (i * 7) % 700) for i in range(n)]
    c = E.convert(emf(polyline(points)))
    ds = paths(c.svg)
    assert len(ds) >= 4
    for d in ds:
        assert len(re.findall(r"-?\d+", d)) <= 2 * E.PIECE_SEGMENTS + 4
    # Each piece starts where the one before ended.
    ends = []
    for d in ds:
        nums = [int(v) for v in re.findall(r"-?\d+", d)]
        x, y = nums[0], nums[1]
        if ends:
            assert (x, y) == ends[-1]
        rest = nums[2:]
        for dx, dy in zip(rest[0::2], rest[1::2]):
            x, y = x + dx, y + dy
        ends.append((x, y))
    assert ends[-1] == (points[-1][0] * PX, points[-1][1] * PX)


def test_conversion_is_fast_for_a_spectrum_sized_polyline():
    import time
    points = [(i // 4, 300 + int(200 * np.sin(i / 50))) for i in range(65536)]
    data = emf(polyline(points))
    start = time.perf_counter()
    c = E.convert(data)
    assert c.ok and time.perf_counter() - start < 1.5
    assert len(c.svg) < 65536 * 12   # compact path data
