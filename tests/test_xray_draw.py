"""X-ray structures (SPEC §13v.6): the drawing — ORTEP ellipsoids seen from any
direction, ball-and-stick, bonds, labels, the settings and the SVG. Made-up structures."""

import math
import re
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from si_assistant.core import cif, crystal
from si_assistant.core import xray_draw as X
from tests.test_crystal import cif_text, ethane_half


def grown(atoms=None, aniso=(), ops=("x, y, z", "-x, -y, -z")):
    s = crystal.structure_from_cif(cif.read_cif(cif_text(atoms or ethane_half(), ops=ops,
                                                         aniso=aniso))[0])
    return crystal.grow(s)


TILTED = [("C1", 0.04, 0.015, 0.025, 0.008, -0.006, 0.004)]


def test_the_outline_holds_the_principal_ellipses():
    """Seen from any direction, the ellipsoid's principal ellipses lie inside (or on) the
    ellipse drawn as its outline."""
    g = grown(aniso=TILTED)
    for seed in range(6):
        rng = np.random.default_rng(seed)
        q, _r = np.linalg.qr(rng.normal(size=(3, 3)))
        view = X.ViewSettings(rotation=(q * np.sign(np.linalg.det(q))).tolist(),
                              labels=False, hydrogens="hide")
        items = X.shapes(g, view)
        outline = next(sh for sh in items if sh.kind == "ellipse")
        a, b = outline.axes
        t = math.radians(outline.angle)
        lines = [sh for sh in items if sh.kind == "polyline" and sh.width < 0.5
                 and abs(sh.depth - outline.depth) < 1e-3]
        assert lines
        for sh in lines:
            for x, y in sh.points:
                dx, dy = x - outline.centre[0], y - outline.centre[1]
                # into the ellipse's own axes (angle: clockwise on the page, y down)
                u = dx * math.cos(t) + dy * math.sin(t)
                v = -dx * math.sin(t) + dy * math.cos(t)
                assert (u / a) ** 2 + (v / b) ** 2 <= 1.02


def test_the_probability_sets_the_size():
    g = grown(aniso=TILTED)
    sizes = {}
    for p in (30, 50, 90):
        items = X.shapes(g, X.ViewSettings(probability=p, labels=False, hydrogens="hide"))
        sizes[p] = max(next(sh for sh in items if sh.kind == "ellipse").axes)
    assert sizes[30] < sizes[50] < sizes[90]
    assert sizes[90] / sizes[50] == pytest.approx(2.5003 / 1.5382, rel=1e-3)


def test_bonds_stop_at_the_atoms_and_are_drawn_back_to_front():
    g = grown(aniso=TILTED)
    items = X.shapes(g, X.ViewSettings(labels=False))
    depths = [sh.depth for sh in items]
    assert depths == sorted(depths)
    cc = [sh for sh in items if sh.kind == "polyline" and sh.width > 2]   # (the C–C bond)
    assert len(cc) == 1
    (x0, y0), (x1, y1) = cc[0].points
    carbons = [sh for sh in items if sh.kind == "ellipse"]
    for sh in carbons:                       # (neither end inside an atom's outline)
        for x, y in cc[0].points:
            d = math.hypot(x - sh.centre[0], y - sh.centre[1])
            assert d >= min(sh.axes) * 0.98


def test_hydrogen_atoms_and_labels():
    g = grown()
    items = X.shapes(g, X.ViewSettings())
    texts = [sh.text for sh in items if sh.kind == "text"]
    assert texts == ["C1"]                    # (one label for both halves; none for H)
    hidden = X.shapes(g, X.ViewSettings(hydrogens="hide", labels=False))
    assert len([sh for sh in hidden if sh.kind in ("ellipse", "ball")]) == 2
    # a label dragged: its place is kept relative to its atom
    def label_at(offset):
        view = X.ViewSettings(label_offsets={"C1": offset})
        return next(sh for sh in X.shapes(g, view) if sh.kind == "text").centre
    (x0, y0), (x1, y1) = label_at((0.0, 0.0)), label_at((20.0, 5.0))
    assert (x1 - x0, y1 - y0) == pytest.approx((20.0, 5.0))


def test_a_label_goes_on_the_asymmetric_units_own_atom():
    """Of the two carbons of ethane (one a copy made by symmetry), the label is on the
    file's own C1 whichever is nearer — also when they are equally near (a flat view)."""
    g = grown()
    own = next(k for k, a in enumerate(g.atoms) if a.label == "C1" and a.own)
    copy = next(k for k, a in enumerate(g.atoms) if a.label == "C1" and not a.own)
    for rotation in (np.eye(3), X.turn(np.eye(3), 0, 90), X.turn(np.eye(3), 0, -90)):
        view = X.ViewSettings(rotation=rotation.tolist(), label_offsets={"C1": (0.0, 0.0)})
        label = next(sh for sh in X.shapes(g, view) if sh.kind == "text")
        r = np.asarray(rotation)
        xyz = np.array([g.atoms[k].xyz for k in range(len(g.atoms))])
        mean = xyz.mean(axis=0)
        at = (r @ (g.atoms[own].xyz - mean)) * X.PT_PER_A
        assert label.centre == pytest.approx((at[0], -at[1]), abs=0.01)
        assert own != copy


def test_ball_and_stick():
    g = grown()
    items = X.shapes(g, X.ViewSettings(style="ball", labels=False))
    balls = [sh for sh in items if sh.kind == "ball"]
    sticks = [sh for sh in items if sh.kind == "polygon"]
    assert len(balls) == 8 and len(sticks) == 2 * 7           # (two halves per bond)
    carbon = max(balls, key=lambda sh: sh.axes[0])
    assert carbon.fill == X.COLOURS["C"]


def test_views():
    xyz = np.array([[0, 0, 0], [4, 0, 0], [8, 0.1, 0], [4, 1.5, 0.05]], float)
    r = X.best_rotation(xyz)
    assert abs(np.linalg.det(r) - 1) < 1e-9
    seen = (xyz - xyz.mean(axis=0)) @ r.T
    assert np.ptp(seen[:, 0]) > np.ptp(seen[:, 1]) > np.ptp(seen[:, 2])  # (flat on the page)
    r = X.axis_rotation([0, 0, 1])
    assert np.allclose(r @ [0, 0, 1], [0, 0, 1])
    turned = X.turn(np.eye(3), 0, 90)
    assert np.allclose(turned @ [1, 0, 0], [0, 0, -1], atol=1e-9)


def test_settings_are_stored_and_damaged_ones_ignored():
    v = X.ViewSettings(rotation=X.turn(np.eye(3), 10, 20).tolist(), zoom=1.5, style="ball",
                       probability=30, hydrogens="hide", colour=False,
                       label_offsets={"C1": (3.0, -2.0)}, hidden_molecules=[1])
    back = X.ViewSettings.from_json(v.to_json())
    assert back.to_json() == v.to_json()
    bad = X.ViewSettings.from_json({"rotation": "nonsense", "zoom": -3, "style": "cartoon",
                                    "probability": 42, "label_offsets": {"C1": "x"}})
    assert bad.to_json() == X.ViewSettings().to_json()
    assert X.ViewSettings.from_json(None).style == "ortep"


def test_the_svg():
    g = grown(aniso=TILTED)
    svg = X.to_svg(X.shapes(g, X.ViewSettings()))
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert re.match(r"[\d.]+pt", root.get("width"))
    ellipses = root.findall("{http://www.w3.org/2000/svg}ellipse")
    assert len(ellipses) == 2
    assert "C1" in svg
    # the outline is turned as the shape says (clockwise on the page, as SVG turns)
    shape = next(sh for sh in X.shapes(g, X.ViewSettings()) if sh.kind == "ellipse")
    turned = float(re.match(r"rotate\(([-\d.]+)", ellipses[0].get("transform")).group(1))
    assert turned == pytest.approx(shape.angle, abs=0.01)
    ball = X.to_svg(X.shapes(g, X.ViewSettings(style="ball")))
    assert "radialGradient" in ball
    ET.fromstring(ball)
