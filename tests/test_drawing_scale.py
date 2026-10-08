"""Measuring the bond length of a pasted structure drawing (SPEC §13n.2a,
core/drawing_scale.py): synthetic ChemDraw-like drawings at different zooms, drawings with
other things in them, drawings without bonds, the cross-check with the copied MOL block,
PDFs, and the printing factor. Runs on every system (no Qt, no Windows)."""

import math

import pytest

from si_assistant.core import drawing_scale as ds
from tests import structure_samples as ss

SCALES = (0.5, 1.0, 2.0)


def factor(bond):
    return ds.print_factor({}, bond)


@pytest.mark.parametrize("scale", SCALES)
def test_bond_length_of_a_scaled_drawing(scale):
    bond = ds.bond_length_pt(ss.structure_svg(scale))
    assert bond == pytest.approx(ss.BOND_PT * scale, rel=0.005)
    # …so it prints at 14.4 pt: twice as large at 0.5×, half as large at 2×.
    assert factor(bond) == pytest.approx(1 / scale, rel=0.005)


@pytest.mark.parametrize("scale", SCALES)
def test_arrows_frames_signs_and_text_do_not_count(scale):
    bond = ds.bond_length_pt(ss.structure_svg(scale, noise=True))
    assert bond == pytest.approx(ss.BOND_PT * scale, rel=0.005)


@pytest.mark.parametrize("scale", SCALES)
def test_cross_check_with_the_copied_structure(scale):
    ref = ds.reference_from_molblock(ss.structure_molblock())
    assert ref.bonds == 14
    assert ds.bond_length_pt(ss.structure_svg(scale, noise=True), ref) == \
        pytest.approx(ss.BOND_PT * scale, rel=0.005)


def svg(body: str, w="200pt", h="100pt", box="0 0 200 100") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="{box}">{body}</svg>')


def test_no_bonds_no_measurement():
    assert ds.bond_length_pt(svg('<text x="10" y="20">Hello 3a</text>')) is None
    assert ds.bond_length_pt(svg("")) is None
    assert ds.bond_length_pt("not an SVG at all") is None
    assert ds.bond_length_pt("<html><body/></html>") is None
    # One line, a frame and an arrow are not a molecule.
    assert ds.bond_length_pt(svg('<path d="M10 50L190 50" stroke="#000"/>')) is None
    assert ds.bond_length_pt(svg('<path d="M5 5L195 5L195 95L5 95Z" fill="none" '
                                 'stroke="#000"/><path d="M20 50L120 50M130 50L180 50" '
                                 'stroke="#000"/>')) is None
    # Parallel lines that never meet (a hatched area) are not bonds either.
    hatch = "".join(f"M{10 + 6 * i} 20L{10 + 6 * i} 40" for i in range(20))
    assert ds.bond_length_pt(svg(f'<path d="{hatch}" stroke="#000"/>')) is None
    # Things inside <defs>, clip paths and white lines are left out.
    zigzag = "M10 50" + "".join(f"L{10 + 12.47 * (i + 1)} {50 - 7.2 * (i % 2 == 0)}"
                                for i in range(6))
    assert ds.bond_length_pt(svg(f'<defs><path d="{zigzag}" stroke="#000"/></defs>')) is None
    assert ds.bond_length_pt(svg(f'<path d="{zigzag}" stroke="#ffffff"/>')) is None
    assert ds.bond_length_pt(svg(f'<path d="{zigzag}" stroke="#000"/>')) == \
        pytest.approx(14.4, rel=0.01)


def test_a_drawing_that_disagrees_with_its_structure_is_not_measured():
    zigzag = "M10 50" + "".join(f"L{10 + 12.47 * (i + 1)} {50 - 7.2 * (i % 2 == 0)}"
                                for i in range(4))
    drawing = svg(f'<path d="{zigzag}" stroke="#000"/>')
    assert ds.bond_length_pt(drawing) == pytest.approx(14.4, rel=0.01)
    # The copied structure has 14 bonds: four lines can't be its drawing.
    ref = ds.reference_from_molblock(ss.structure_molblock())
    assert ds.bond_length_pt(drawing, ref) is None
    # A structure whose bonds are a quarter of its size, but here they are half of it.
    assert ds.bond_length_pt(drawing, ds.Reference(4, 0.12)) is None
    assert ds.bond_length_pt(drawing, ds.Reference(4, 14.4 / 52)) == pytest.approx(14.4, rel=0.01)


def test_svg_units_transforms_styles_and_filled_bonds():
    # The zigzag in mm (1 unit = 1 mm), doubled by a transform, styled like RDKit's drawings.
    k = 25.4 / 72                     # mm per pt
    pts = [(10 + 12.47 * i, 50 - 7.2 * (i % 2)) for i in range(6)]
    d = "M" + " L".join(f"{x * k:.4f},{y * k:.4f}" for x, y in pts)
    drawing = (f'<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" '
               f'viewBox="0 0 200 100"><g transform="scale(2)"><path class="bond-0" d="{d}" '
               "style='fill:none;stroke:#000000;stroke-width:0.2'/></g></svg>")
    assert ds.bond_length_pt(drawing) == pytest.approx(14.4 * 2, rel=0.01)   # (scaled ×2)
    # Lines and polylines, and a matrix that turns the drawing.
    poly = " ".join(f"{x},{y}" for x, y in pts)
    turned = svg(f'<g transform="rotate(30 100 50)"><polyline points="{poly}" fill="none" '
                 'stroke="black"/></g>')
    assert ds.bond_length_pt(turned) == pytest.approx(14.4, rel=0.01)
    # Bonds drawn as long thin filled shapes (0.6 pt wide).
    shapes = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / L * 0.3, (x1 - x0) / L * 0.3
        corners = [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]
        shapes.append("<polygon points='" + " ".join(f"{x:.3f},{y:.3f}" for x, y in corners)
                      + "' fill='#000'/>")
    assert ds.bond_length_pt(svg("".join(shapes))) == pytest.approx(14.4, rel=0.01)


def test_path_data_reading():
    sub = ds.path_subpaths("M0 0l10 0h5v5c1 1 2 2 3 3L0 0zm20 20 10 0")
    (first, closed), (second, open_) = sub
    assert closed and not open_
    assert [s for _a, _b, s in first] == [True, True, True, False, True]
    assert first[1] == ((10.0, 0.0), (15.0, 0.0), True)
    assert second == [((20.0, 20.0), (30.0, 20.0), True)]   # (pairs after m are lines)


def test_bond_length_of_a_pdf(tmp_path):
    """ChemDraw's copy on macOS is a PDF: its stroked lines are measured too."""
    import typst
    for scale in (0.5, 2.0):
        b = 14.4 * scale
        pts = [(10 + b * math.cos(math.radians(30)) * i, 40 - b / 2 * (i % 2)) for i in range(7)]
        lines = "\n".join(
            f"#place(line(start: ({x0:.3f}pt, {y0:.3f}pt), end: ({x1:.3f}pt, {y1:.3f}pt), "
            f"stroke: {0.6 * scale:.2f}pt))" for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
        src = tmp_path / f"s{scale}.typ"
        src.write_text("#set page(width: 160pt, height: 70pt, margin: 0pt)\n" + lines
                       + "\n#place(dx: 5pt, dy: 55pt)[OMe 3a]", encoding="utf-8")
        pdf = tmp_path / f"s{scale}.pdf"
        typst.compile(str(src), output=str(pdf))
        assert ds.pdf_bond_length_pt(pdf.read_bytes()) == pytest.approx(b, rel=0.01)
        assert ds.measure_file(pdf) == pytest.approx(b, rel=0.01)
    assert ds.pdf_bond_length_pt(b"%PDF-1.4 damaged") is None


def test_print_factor_and_the_setting():
    target = ds.ACS_BOND_PT
    assert factor(7.2) == pytest.approx(2.0)
    assert factor(None) == 1.0                             # nothing measured: own size
    assert factor(target * 20) == 1.0                      # absurd: own size
    assert ds.print_factor({ds.SETTING_KEY: False}, 7.2) == 1.0   # setting off
    # Another bond length in Project settings ▸ Structures (0.6 cm = 17.0 pt).
    settings = {"structure_drawing": {"bond_length_cm": 0.6}}
    assert ds.print_factor(settings, 17.0079) == pytest.approx(1.0, rel=1e-3)
    # A picture's own values: stored measurement, a typed length, "its own size".
    assert ds.structure_print_factor({}, {ds.MEASURED_KEY: 7.2}, None) == pytest.approx(2.0)
    assert ds.structure_print_factor({}, {ds.MEASURED_KEY: None}, None) == 1.0
    assert ds.structure_print_factor({}, {ds.MEASURED_KEY: 7.2, ds.OVERRIDE_KEY: 28.8},
                                     None) == pytest.approx(0.5)
    assert ds.structure_print_factor({}, {ds.MEASURED_KEY: 7.2, ds.OVERRIDE_KEY: 0},
                                     None) == 1.0
    assert ds.structure_print_factor({}, {ds.MEASURED_KEY: "rubbish"}, None) == 1.0


def test_a_picture_stored_before_measuring_is_measured_when_needed(tmp_path):
    path = tmp_path / "structure_image.svg"
    path.write_text(ss.structure_svg(0.5), encoding="utf-8")
    assert ds.picture_bond_pt({}, path) == pytest.approx(7.2, rel=0.005)
    assert ds.picture_bond_pt({}, tmp_path / "structure_image.png") is None
