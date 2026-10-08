"""Made-up structure drawings like ChemDraw's copies, for the tests of pasted structure
pictures (SPEC §13n.1, §13n.2a). There is no ChemDraw here, so they are drawn the way
ChemDraw draws (ACS Document 1996 style): bonds as straight lines 14.4 pt long and 0.6 pt
wide with round caps, bonds to atom labels stopping short of the label, the inner line of
a double bond in a ring shorter and inside the ring, a C=O double bond as two lines, a
wedge as a filled triangle, a hashed wedge as short lines, and atom labels as Arial text.

* structure_svg(scale): the drawing as an SVG written the way core/emf_svg.py writes one
  (1 unit = 0.01 mm, width and height in pt), on any system;
* structure_molblock(): the same molecule as a MOL block with ChemDraw-like coordinates;
* structure_emf(scale): the drawing as an Enhanced Metafile made with real Windows (GDI)
  calls (Windows only), as ChemDraw puts it on the clipboard.

`scale` is the zoom it was copied at: 1.0 gives 14.4 pt bonds, 0.5 gives 7.2 pt, 2.0 gives
28.8 pt.
"""

from __future__ import annotations

import math

BOND_PT = 14.4
UNITS_PER_PT = 2540 / 72          # 0.01 mm per pt
LABEL_GAP = 0.32                  # a bond stops this far (× bond) from a labelled atom
INNER_OFFSET = 0.18               # a ring's inner double-bond line: offset and
INNER_TRIM = 0.12                 # shortening at each end (× bond)
CO_OFFSET = 0.09                  # each line of a C=O bond from the bond's axis
WEDGE_WIDTH = 0.16                # the wide end of a wedge (× bond)
FONT_PT = 10.0


def _molecule():
    """(atoms [(x, y) in bond lengths, y down], labels {atom: text}, bonds [(i, j, kind)]).
    Methyl 4-(1-methoxyethyl)benzoate-like: a benzene ring, an ester, a stereocentre with a
    wedge and a hashed wedge."""
    ring = [(math.cos(math.radians(a)), math.sin(math.radians(a)))
            for a in (-90, -30, 30, 90, 150, 210)]
    atoms = [(x + 2.5, y + 2.0) for x, y in ring]            # 0-5
    c6 = (atoms[1][0] + math.cos(math.radians(-30)), atoms[1][1] + math.sin(math.radians(-30)))
    atoms.append(c6)                                          # 6 carbonyl C
    atoms.append((c6[0], c6[1] - 1))                          # 7 =O
    atoms.append((c6[0] + math.cos(math.radians(30)), c6[1] + 0.5))   # 8 O
    o8 = atoms[8]
    atoms.append((o8[0] + math.cos(math.radians(-30)), o8[1] - 0.5))  # 9 CH3
    c10 = (atoms[4][0] - math.cos(math.radians(30)), atoms[4][1] + 0.5)
    atoms.append(c10)                                         # 10 stereocentre
    atoms.append((c10[0], c10[1] + 1))                        # 11 CH3 (wedge)
    atoms.append((c10[0] - math.cos(math.radians(30)), c10[1] - 0.5))  # 12 O (hashed)
    atoms.append((atoms[12][0] - math.cos(math.radians(30)), atoms[12][1] + 0.5))  # 13 CH3
    labels = {7: "O", 8: "O", 12: "O"}
    bonds = [(0, 1, "single"), (1, 2, "ring"), (2, 3, "single"), (3, 4, "ring"),
             (4, 5, "single"), (5, 0, "ring"), (1, 6, "single"), (6, 7, "co"),
             (6, 8, "single"), (8, 9, "single"), (4, 10, "single"), (10, 11, "wedge"),
             (10, 12, "hash"), (12, 13, "single")]
    return atoms, labels, bonds


def _ring_centre(atoms):
    xs, ys = zip(*atoms[:6])
    return sum(xs) / 6, sum(ys) / 6


def drawing(scale: float = 1.0):
    """The drawing in pt (y down), as lists: lines [(x0, y0, x1, y1)], filled polygons
    [[(x, y)…]], texts [(x, y, text)] (x, y: the label's centre), and its size (w, h)."""
    atoms, labels, bonds = _molecule()
    b = BOND_PT * scale
    margin = 0.6
    x_min, y_min = min(x for x, _y in atoms), min(y for _x, y in atoms)
    pts = [((x - x_min + margin) * b, (y - y_min + margin) * b) for x, y in atoms]
    lines, polygons, texts = [], [], []
    cx, cy = _ring_centre(pts)

    def trimmed(i, j, a=0.0, c=0.0):
        (x0, y0), (x1, y1) = pts[i], pts[j]
        L = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        s = (LABEL_GAP if i in labels else 0.0) + a
        e = (LABEL_GAP if j in labels else 0.0) + c
        return (x0 + ux * s * b, y0 + uy * s * b, x1 - ux * e * b, y1 - uy * e * b), (ux, uy)

    for i, j, kind in bonds:
        (x0, y0, x1, y1), (ux, uy) = trimmed(i, j)
        nx, ny = -uy, ux
        if kind in ("single", "ring"):
            lines.append((x0, y0, x1, y1))
        if kind == "ring":   # the inner line, towards the ring's centre
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if (cx - mx) * nx + (cy - my) * ny < 0:
                nx, ny = -nx, -ny
            (a0, b0, a1, b1), _u = trimmed(i, j, INNER_TRIM, INNER_TRIM)
            o = INNER_OFFSET * b
            lines.append((a0 + nx * o, b0 + ny * o, a1 + nx * o, b1 + ny * o))
        elif kind == "co":
            o = CO_OFFSET * b
            lines.append((x0 + nx * o, y0 + ny * o, x1 + nx * o, y1 + ny * o))
            lines.append((x0 - nx * o, y0 - ny * o, x1 - nx * o, y1 - ny * o))
        elif kind == "wedge":
            w = WEDGE_WIDTH * b / 2
            polygons.append([(x0, y0), (x1 + nx * w, y1 + ny * w), (x1 - nx * w, y1 - ny * w)])
        elif kind == "hash":
            for k in range(1, 7):
                t = k / 7
                w = WEDGE_WIDTH * b / 2 * t
                px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                lines.append((px + nx * w, py + ny * w, px - nx * w, py - ny * w))
    for i, text in labels.items():
        texts.append((pts[i][0], pts[i][1], text))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return lines, polygons, texts, (max(xs) + margin * b, max(ys) + margin * b)


def structure_svg(scale: float = 1.0, noise: bool = False) -> str:
    """The drawing as an SVG like core/emf_svg.py writes (1 unit = 0.01 mm). `noise` adds
    what a picture may have besides the molecule: a frame round it, a long reaction arrow
    with a filled head, a plus sign and a caption."""
    lines, polygons, texts, (w, h) = drawing(scale)
    k = UNITS_PER_PT
    extra_h = 40 * scale if noise else 0.0
    W, H = w + (120 * scale if noise else 0.0), h + extra_h

    def u(v):
        return round(v * k)

    d = "".join(f"M{u(x0)} {u(y0)}L{u(x1)} {u(y1)}" for x0, y0, x1, y1 in lines)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{W:.3f}pt" '
           f'height="{H:.3f}pt" viewBox="0 0 {u(W)} {u(H)}">',
           '<g stroke-linecap="round" stroke-linejoin="round">',
           f'<path d="{d}" fill="none" stroke="#000000" stroke-width="{u(0.6 * scale)}"/>']
    for poly in polygons:
        pd = "M" + "L".join(f"{u(x)} {u(y)}" for x, y in poly) + "Z"
        out.append(f'<path d="{pd}" fill="#000000"/>')
    for x, y, text in texts:
        fs = u(FONT_PT * scale)
        out.append(f'<rect x="{u(x - 4 * scale)}" y="{u(y - 5 * scale)}" '
                   f'width="{u(8 * scale)}" height="{u(10 * scale)}" fill="#ffffff"/>')
        out.append(f'<text x="{u(x - 3.6 * scale)}" y="{u(y + 3.6 * scale)}" '
                   f'font-family="\'Arial\', sans-serif" font-size="{fs}" '
                   f'fill="#000000">{text}</text>')
    if noise:
        s = scale
        frame = (f"M{u(2 * s)} {u(2 * s)}L{u(W - 2 * s)} {u(2 * s)}L{u(W - 2 * s)} "
                 f"{u(H - 2 * s)}L{u(2 * s)} {u(H - 2 * s)}Z")
        out.append(f'<path d="{frame}" fill="none" stroke="#000000" stroke-width="{u(0.5 * s)}"/>')
        ay = h / 2
        out.append(f'<path d="M{u(w + 10 * s)} {u(ay)}L{u(w + 90 * s)} {u(ay)}" fill="none" '
                   f'stroke="#000000" stroke-width="{u(0.6 * s)}"/>')
        out.append(f'<path d="M{u(w + 96 * s)} {u(ay)}L{u(w + 88 * s)} {u(ay - 3 * s)}'
                   f'L{u(w + 88 * s)} {u(ay + 3 * s)}Z" fill="#000000"/>')
        px, py = w + 105 * s, ay
        out.append(f'<path d="M{u(px - 4 * s)} {u(py)}L{u(px + 4 * s)} {u(py)}M{u(px)} '
                   f'{u(py - 4 * s)}L{u(px)} {u(py + 4 * s)}" fill="none" stroke="#000000" '
                   f'stroke-width="{u(0.6 * s)}"/>')
        out.append(f'<text x="{u(10 * s)}" y="{u(h + 25 * s)}" font-size="{u(10 * s)}">'
                   'Scheme 1. Made-up caption, 92% yield</text>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def structure_molblock() -> str:
    """The molecule as a MOL block with ChemDraw-like coordinates (bond 0.825, y up)."""
    atoms, labels, bonds = _molecule()
    elements = {i: "O" for i in labels}
    order = {"single": 1, "ring": 2, "co": 2, "wedge": 1, "hash": 1}
    stereo = {"wedge": 1}   # (the hashed bond's flag is left out: RDKit reads one per centre)
    lines = ["", "  ChemDraw10072612002D", "",
             f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999 V2000"]
    for i, (x, y) in enumerate(atoms):
        lines.append(f"{x * 0.825:10.4f}{-y * 0.825:10.4f}{0:10.4f} "
                     f"{elements.get(i, 'C'):<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    for i, j, kind in bonds:
        lines.append(f"{i + 1:3d}{j + 1:3d}{order[kind]:3d}{stereo.get(kind, 0):3d}")
    lines.append("M  END")
    return "\n".join(lines) + "\n"


def structure_emf(scale: float = 1.0, labels: bool = True) -> bytes:
    """The drawing as an Enhanced Metafile made with real Windows calls (Windows only), in
    0.01 mm like the other samples: bonds as MoveTo/LineTo with a 0.6 pt round pen, the
    wedge a filled polygon, labels Arial text on white boxes. `labels=False` leaves the
    text out (Qt "offscreen", which the tests use, has no fonts to compare text with)."""
    from tests import emf_samples as es
    lines, polygons, texts, (w, h) = drawing(scale)
    k = UNITS_PER_PT
    with es.Metafile(w / 72 * 25.4, h / 72 * 25.4) as m:
        dc = m.dc
        brush = es.LOGBRUSH(0, 0, 0)
        pen = m.keep(es.ExtCreatePen(es.PS_GEOMETRIC, round(0.6 * scale * k),
                                     ctypes_byref(brush), 0, None))
        m.select(pen)
        for x0, y0, x1, y1 in lines:
            es.MoveToEx(dc, round(x0 * k), round(y0 * k), None)
            es.LineTo(dc, round(x1 * k), round(y1 * k))
        black = m.keep(es.CreateSolidBrush(0))
        m.select(black)
        m.select(es.GetStockObject(es.NULL_PEN))
        for poly in polygons:
            es.Polygon(dc, es.points([(x * k, y * k) for x, y in poly]), len(poly))
        if labels:
            m.select(es.GetStockObject(es.WHITE_BRUSH))
            font = m.keep(es.font(-round(FONT_PT * scale * k)))
            m.select(font)
            es.SetBkMode(dc, es.TRANSPARENT)
            es.SetTextAlign(dc, es.TA_CENTER | es.TA_BASELINE)
            for x, y, text in texts:
                es.Rectangle(dc, round((x - 4 * scale) * k), round((y - 5 * scale) * k),
                             round((x + 4 * scale) * k), round((y + 5 * scale) * k))
                es.text(dc, round(x * k), round((y + 3.6 * scale) * k), text)
    return m.data


def ctypes_byref(obj):
    import ctypes
    return ctypes.byref(obj)
