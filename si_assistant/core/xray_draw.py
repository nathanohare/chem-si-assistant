"""Drawing an X-ray structure (SPEC §13v.6): ORTEP (thermal ellipsoids) or
ball-and-stick, seen from any direction, as a list of shapes in drawing order and as an
SVG (vector: sharp in the SI).

The view is flat (orthographic), as crystal structures are drawn. Each atom's ellipsoid
(the surface its displacement parameters U enclose with the chosen probability, 50% by
default) shows as the ellipse of its outline from that direction, with its three
principal ellipses (where the surface is seen); hydrogen atoms as small spheres. Bonds and
atoms are drawn back to front, so nearer ones hide farther ones.

`ViewSettings` holds everything the user chooses (stored with the structure as JSON:
rotation, zoom, style…); `shapes(grown, view)` gives the shapes in points (y downwards,
as in SVG and Qt), `to_svg(...)` the SVG. The viewer (ui/) draws the same shapes with Qt,
so what is seen is what is exported.

Pure Python + numpy: unit-tested.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field

import numpy as np

from .crystal import COVALENT, Grown

# The ellipsoid's size for a probability: c with P(χ²₃ ≤ c²) = p.
PROBABILITY_SCALE = {30: 1.1932, 50: 1.5382, 70: 1.9144, 90: 2.5003}
PT_PER_A = 28.0                   # points per ångström at zoom 1
H_RADIUS = 0.14                   # Å: a hydrogen atom drawn as a small sphere
BOND_WIDTH = 0.085                # Å: a bond's line (ORTEP)
STICK_RADIUS = 0.10               # Å: a bond's stick (ball-and-stick)

# Element colours (as in Jmol/CPK, a little darker for print).
COLOURS = {"H": "#ffffff", "C": "#8c8c8c", "N": "#3050f8", "O": "#e60d0d", "F": "#7fd34d",
           "Cl": "#1fbf1f", "Br": "#a62929", "I": "#940094", "S": "#e6c21f", "P": "#ff8000",
           "B": "#ffb5b5", "Si": "#d0b080", "Se": "#e09000", "Na": "#ab5cf2", "K": "#8f40d4",
           "Li": "#cc80ff", "Mg": "#56c400", "Al": "#bfa6a6", "Fe": "#e06633", "Co": "#f090a0",
           "Ni": "#50d050", "Cu": "#c88033", "Zn": "#7d80b0", "Pd": "#006985", "Pt": "#d0d0e0",
           "Rh": "#0a7d8c", "Ru": "#248f8f", "Ir": "#175487", "Au": "#ffd123", "Ag": "#c0c0c0",
           "Sn": "#668080", "Ti": "#bfc2c7", "Zr": "#94e0e0", "Mn": "#9c7ac7", "Cr": "#8a99c7",
           "Mo": "#54b5b5", "W": "#2194d6", "D": "#ffffc0"}


@dataclass
class ViewSettings:
    """What the user chose (stored as JSON with the structure, §13v.8)."""
    rotation: list = field(default_factory=lambda: np.eye(3).tolist())   # Å → screen axes
    zoom: float = 1.0
    style: str = "ortep"            # "ortep" or "ball"
    probability: int = 50           # ellipsoids at this % probability
    hydrogens: str = "spheres"      # "spheres", "hide" (ORTEP: drawn as small spheres)
    colour: bool = True             # element colours, or black and white
    principal: bool = True          # ORTEP: the principal ellipses
    labels: bool = True             # atom labels (not hydrogen)
    label_size: float = 9.0         # pt
    label_offsets: dict = field(default_factory=dict)   # label → (dx, dy) pt, dragged
    hidden_molecules: list = field(default_factory=list)   # molecule numbers not shown
    hidden_parts: list = field(default_factory=list)       # disorder parts not shown

    def to_json(self) -> dict:
        return {"rotation": [[round(float(v), 6) for v in row] for row in self.rotation],
                "zoom": self.zoom, "style": self.style, "probability": self.probability,
                "hydrogens": self.hydrogens, "colour": self.colour, "principal": self.principal,
                "labels": self.labels, "label_size": self.label_size,
                "label_offsets": {k: [round(v[0], 1), round(v[1], 1)]
                                  for k, v in self.label_offsets.items()},
                "hidden_molecules": list(self.hidden_molecules),
                "hidden_parts": list(self.hidden_parts)}

    @classmethod
    def from_json(cls, data) -> "ViewSettings":
        """Settings from a (possibly hand-edited) JSON value: anything wrong falls back to
        the default, so a damaged file never stops the drawing."""
        v = cls()
        if not isinstance(data, dict):
            return v
        try:
            r = np.array(data.get("rotation"), float)
            if r.shape == (3, 3) and np.all(np.isfinite(r)):
                u, _s, wt = np.linalg.svd(r)          # (back to a true rotation)
                v.rotation = (u @ wt).tolist()
        except (TypeError, ValueError):
            pass
        for key, kind in (("zoom", float), ("label_size", float)):
            try:
                value = kind(data.get(key, getattr(v, key)))
                if math.isfinite(value) and value > 0:
                    setattr(v, key, min(value, 50.0))
            except (TypeError, ValueError):
                pass
        if data.get("style") in ("ortep", "ball"):
            v.style = data["style"]
        if data.get("probability") in PROBABILITY_SCALE:
            v.probability = data["probability"]
        if data.get("hydrogens") in ("spheres", "hide"):
            v.hydrogens = data["hydrogens"]
        for key in ("colour", "principal", "labels"):
            if isinstance(data.get(key), bool):
                setattr(v, key, data[key])
        offsets = data.get("label_offsets")
        if isinstance(offsets, dict):
            for k, val in offsets.items():
                try:
                    v.label_offsets[str(k)] = (float(val[0]), float(val[1]))
                except (TypeError, ValueError, IndexError):
                    pass
        for key in ("hidden_molecules", "hidden_parts"):
            value = data.get(key)
            if isinstance(value, list):
                setattr(v, key, [x for x in value if isinstance(x, (int, str))])
        return v


# ---------------------------------------------------------------------- views


def best_rotation(xyz: np.ndarray) -> np.ndarray:
    """A rotation showing the molecule spread out: its longest extent across the page,
    looking along its thinnest direction (as one usually draws a molecule)."""
    if len(xyz) < 3:
        return np.eye(3)
    centred = xyz - xyz.mean(axis=0)
    _w, vecs = np.linalg.eigh(centred.T @ centred)
    r = vecs[:, ::-1].T                  # rows: largest, middle, smallest spread
    if np.linalg.det(r) < 0:
        r[2] = -r[2]
    return r


def axis_rotation(direction: np.ndarray, up: np.ndarray | None = None) -> np.ndarray:
    """A rotation looking along `direction` (it points at the viewer)."""
    z = np.asarray(direction, float)
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0]) if up is None else np.asarray(up, float)
    if abs(float(np.dot(up, z))) > 0.95:
        up = np.array([1.0, 0.0, 0.0])
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.array([x, y, z])


DEFAULT_WIDTH_PT = 340.0          # a new drawing's width (about 12 cm: fits the SI's text)


def fitting_zoom(grown: Grown, view: "ViewSettings", width_pt: float = DEFAULT_WIDTH_PT) -> float:
    """The zoom at which the drawing (as `view` shows it) is `width_pt` wide, so that its
    labels and lines print at their own sizes in the SI."""
    trial = ViewSettings.from_json(view.to_json())
    trial.zoom, trial.labels = 1.0, False
    x0, _y0, x1, _y1 = bounds(shapes(grown, trial))
    return float(min(4.0, max(0.2, width_pt / max(1.0, x1 - x0))))


def turn(rotation, dx_deg: float, dy_deg: float, dz_deg: float = 0.0) -> np.ndarray:
    """The view turned about the screen's axes: dy about the vertical axis (drag left /
    right), dx about the horizontal one (drag up / down), dz in the page's plane."""
    def about(axis: int, deg: float) -> np.ndarray:
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(3)
        i, j = [(1, 2), (2, 0), (0, 1)][axis]
        m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
        return m
    return about(1, dy_deg) @ about(0, dx_deg) @ about(2, dz_deg) @ np.asarray(rotation, float)


# ---------------------------------------------------------------------- shapes


@dataclass
class Shape:
    kind: str                     # "ellipse", "polyline", "polygon", "ball", "text"
    depth: float                  # larger = nearer the viewer (drawn later)
    points: list = field(default_factory=list)   # polyline/polygon: [(x, y)…]
    centre: tuple = (0.0, 0.0)    # ellipse/ball/text
    axes: tuple = (0.0, 0.0)      # ellipse: semi-axes (pt); ball: (radius, radius)
    angle: float = 0.0            # ellipse: its first axis, degrees clockwise on the page
                                  # (as SVG's and Qt's rotate, y pointing down)
    fill: str = "none"
    stroke: str = "#000000"
    width: float = 0.6            # pt
    text: str = ""                # a label
    size: float = 9.0
    key: str = ""                 # a label's atom ("C1"): for dragging


def _visible_atoms(grown: Grown, view: ViewSettings) -> list[int]:
    out = []
    for i, a in enumerate(grown.atoms):
        if a.molecule in view.hidden_molecules or (a.atom.part and a.atom.part in
                                                   [str(p) for p in view.hidden_parts]):
            continue
        if a.element in ("H", "D") and view.hydrogens == "hide":
            continue
        out.append(i)
    return out


def shapes(grown: Grown, view: ViewSettings) -> list[Shape]:
    """The shapes of the drawing, back to front, in points (see the module's text)."""
    r = np.asarray(view.rotation, float)
    keep = _visible_atoms(grown, view)
    if not keep:
        return []
    xyz = np.array([grown.atoms[i].xyz for i in keep])
    centre = xyz.mean(axis=0)
    scale = PT_PER_A * view.zoom
    screen = {i: (r @ (grown.atoms[i].xyz - centre)) for i in keep}     # Å, y up

    def pt(v) -> tuple[float, float]:
        return float(v[0] * scale), float(-v[1] * scale)

    out: list[Shape] = []
    radius: dict[int, float] = {}          # Å, the atom's size (labels)
    outline: dict[int, object] = {}        # how far the atom's outline is in a direction
    c = PROBABILITY_SCALE.get(view.probability, 1.5382)
    for i in keep:
        a = grown.atoms[i]
        s = screen[i]
        colour = COLOURS.get(a.element, "#b0b0b0") if view.colour else "#ffffff"
        hydrogen = a.element in ("H", "D")
        if view.style == "ball" or hydrogen:
            rad = H_RADIUS if hydrogen and view.style != "ball" else \
                max(0.15, 0.38 * COVALENT.get(a.element, 1.0) + 0.05)
            if hydrogen and view.style == "ball":
                rad = 0.17
            radius[i] = rad
            outline[i] = rad
            out.append(Shape("ball" if view.style == "ball" or view.colour else "ellipse",
                             float(s[2]), centre=pt(s), axes=(rad * scale, rad * scale),
                             fill=colour, stroke="#000000", width=0.5 if hydrogen else 0.6))
            continue
        # ORTEP: the outline of the ellipsoid, and its principal ellipses
        u = r @ a.u @ r.T
        u = (u + u.T) / 2
        w, vecs = np.linalg.eigh(u[:2, :2])
        w = np.clip(w, 1e-5, None)
        major = vecs[:, 1]
        out.append(Shape("ellipse", float(s[2]), centre=pt(s),
                         axes=(c * math.sqrt(w[1]) * scale, c * math.sqrt(w[0]) * scale),
                         angle=math.degrees(math.atan2(-major[1], major[0])),
                         fill=_tint(colour) if view.colour else "#ffffff",
                         stroke="#000000", width=0.7))
        radius[i] = c * math.sqrt(float(np.mean(w)))
        outline[i] = (c, np.linalg.inv(u[:2, :2]))
        if view.principal and a.atom.anisotropic:
            for line in _principal_ellipses(u, c):
                pts = [pt(s + p) for p in line]
                out.append(Shape("polyline", float(s[2]) + 1e-6, points=pts,
                                 stroke="#000000", width=0.45))
    # bonds: from the edge of one atom's outline to the other's (so they never run over
    # an atom), back to front with the atoms
    keep_set = set(keep)

    def reach(k: int, direction: np.ndarray) -> float:
        """How far atom k's outline is from its centre in a screen direction (Å)."""
        o = outline[k]
        if not isinstance(o, tuple):
            return float(o)
        cc, inv = o
        q = float(direction @ inv @ direction)
        return cc / math.sqrt(q) if q > 1e-12 else 0.0

    for i, j in grown.bonds:
        if i not in keep_set or j not in keep_set:
            continue
        a, b = screen[i], screen[j]
        d = b - a
        length = float(np.linalg.norm(d[:2]))
        if length < 1e-6:
            continue
        unit = d[:2] / length
        ra, rb = reach(i, unit), reach(j, -unit)
        if ra + rb >= length:
            continue                      # (one atom hides the bond: seen end on)
        a = a + d * (ra / length)
        b = b - d * (rb / length)
        hydrogen = "H" in (grown.atoms[i].element, grown.atoms[j].element)
        if view.style == "ball":
            out += _stick(a, b, scale, grown, i, j, view, pt)
            continue
        width = (BOND_WIDTH * (0.6 if hydrogen else 1.0)) * scale
        depth = float((a[2] + b[2]) / 2) - 1e-3
        out.append(Shape("polyline", depth, points=[pt(a), pt(b)], stroke="#000000",
                         width=width))
    out.sort(key=lambda sh: sh.depth)
    if view.labels:
        out += _labels(grown, keep, screen, radius, scale, view, pt)
    return out


def _tint(colour: str) -> str:
    """An element's colour, lighter (an ellipsoid's fill: its outline and principal
    ellipses stay readable)."""
    rgb = [int(colour[k:k + 2], 16) for k in (1, 3, 5)]
    return "#" + "".join(f"{int(v + (255 - v) * 0.35):02x}" for v in rgb)


def _principal_ellipses(u: np.ndarray, c: float, n: int = 72) -> list[list[np.ndarray]]:
    """The parts of the ellipsoid's three principal ellipses that face the viewer, as
    lists of points (Å, relative to its centre)."""
    w, vecs = np.linalg.eigh(u)
    w = np.clip(w, 1e-6, None)
    axes = [vecs[:, k] * c * math.sqrt(w[k]) for k in range(3)]
    inv = np.linalg.inv(u)
    out = []
    for p, q in ((0, 1), (1, 2), (0, 2)):
        run: list[np.ndarray] = []
        for t in np.linspace(0, 2 * math.pi, n + 1):
            point = axes[p] * math.cos(t) + axes[q] * math.sin(t)
            normal = inv @ point
            if normal[2] > 0:                 # (on the side facing the viewer)
                run.append(point)
            elif run:
                if len(run) > 1:
                    out.append(run)
                run = []
        if len(run) > 1:
            out.append(run)
    return out


def _stick(a, b, scale, grown, i, j, view, pt) -> list[Shape]:
    """A ball-and-stick bond: two halves in the colours of their atoms."""
    mid = (a + b) / 2
    out = []
    for start, end, k in ((a, mid, i), (mid, b, j)):
        colour = COLOURS.get(grown.atoms[k].element, "#b0b0b0") if view.colour else "#ffffff"
        d = np.array(pt(end)) - np.array(pt(start))
        n = np.array([-d[1], d[0]])
        if np.linalg.norm(n) < 1e-9:
            continue
        n = n / np.linalg.norm(n) * STICK_RADIUS * scale
        p0, p1 = np.array(pt(start)), np.array(pt(end))
        quad = [tuple(p0 + n), tuple(p1 + n), tuple(p1 - n), tuple(p0 - n)]
        out.append(Shape("polygon", float((start[2] + end[2]) / 2) - 1e-3,
                         points=[(float(x), float(y)) for x, y in quad], fill=colour,
                         stroke="#000000", width=0.4))
    return out


def _labels(grown, keep, screen, radius, scale, view, pt) -> list[Shape]:
    """Labels of the non-hydrogen atoms, on the open side of each (away from its bonds),
    or where dragged. An atom shown twice (a molecule completed by symmetry) is labelled
    once: on the asymmetric unit's own atom (as crystallographers label), else the
    nearest copy."""
    keep_set = set(keep)
    neighbours: dict[int, list[int]] = {}
    for i, j in grown.bonds:
        if i in keep_set and j in keep_set:
            neighbours.setdefault(i, []).append(j)
            neighbours.setdefault(j, []).append(i)
    out = []
    seen = set()
    for i in sorted(keep, key=lambda k: (not grown.atoms[k].own, -screen[k][2])):
        a = grown.atoms[i]
        if a.element in ("H", "D") or a.label in seen:
            continue
        seen.add(a.label)
        x, y = pt(screen[i])
        offset = view.label_offsets.get(a.label)
        if offset is None:
            offset = _open_side(i, neighbours.get(i, []), screen, radius.get(i, 0.3) * scale,
                                a.label, view.label_size)
        out.append(Shape("text", 1e9, centre=(x + offset[0], y + offset[1]), text=a.label,
                         size=view.label_size, key=a.label))
    return out


def _open_side(i: int, bonded: list[int], screen: dict, radius_pt: float, text: str,
               size: float) -> tuple[float, float]:
    """Where a label goes from its atom (pt, y down; the text's start on its baseline):
    in the middle of the widest open angle between the atom's bonds as seen (opposite a
    single bond; up and to the right with none), just outside the atom."""
    angles = []
    for j in bonded:
        d = screen[j][:2] - screen[i][:2]
        if float(np.linalg.norm(d)) > 1e-6:
            angles.append(math.atan2(float(d[1]), float(d[0])))
    if not angles:
        angle = math.radians(45)
    else:
        angles.sort()
        gaps = [(angles[(k + 1) % len(angles)] - angles[k]) % (2 * math.pi) or 2 * math.pi
                for k in range(len(angles))]
        k = max(range(len(gaps)), key=lambda m: (round(gaps[m], 3), -abs(math.sin(
            angles[m] + gaps[m] / 2 - math.radians(45)))))
        angle = angles[k] + gaps[k] / 2
    dx, dy = math.cos(angle), -math.sin(angle)                  # (y down on the page)
    w, h = 0.58 * size * len(text), 0.72 * size
    # the gap from the atom's edge to the label's box edge, in this direction
    reach = abs(dx) * w / 2 + abs(dy) * h / 2
    dist = radius_pt + 2.5 + reach
    cx, cy = dx * dist, dy * dist
    return cx - w / 2, cy + h / 2


def bounds(items: list[Shape]) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) the shapes cover (pt), labels included."""
    xs, ys = [], []
    for sh in items:
        if sh.kind in ("ellipse", "ball"):
            rad = max(sh.axes)
            xs += [sh.centre[0] - rad, sh.centre[0] + rad]
            ys += [sh.centre[1] - rad, sh.centre[1] + rad]
        elif sh.kind == "text":
            w = 0.6 * sh.size * len(sh.text)
            xs += [sh.centre[0], sh.centre[0] + w]
            ys += [sh.centre[1] - sh.size, sh.centre[1] + 0.3 * sh.size]
        else:
            for x, y in sh.points:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------- SVG


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if v else "0"


def to_svg(items: list[Shape], margin: float = 6.0, font: str = "Arial") -> str:
    """The shapes as an SVG (width and height in pt, as the program's other drawings)."""
    x0, y0, x1, y1 = bounds(items)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(w)}pt" height="{_num(h)}pt" '
           f'viewBox="{_num(x0)} {_num(y0)} {_num(w)} {_num(h)}">',
           '<!-- X-ray structure drawn by Chem SI Assistant -->']
    gradients: dict[str, str] = {}
    body = []
    for sh in items:
        if sh.kind == "ellipse":
            body.append(f'<ellipse cx="{_num(sh.centre[0])}" cy="{_num(sh.centre[1])}" '
                        f'rx="{_num(sh.axes[0])}" ry="{_num(sh.axes[1])}" '
                        f'transform="rotate({_num(sh.angle)} {_num(sh.centre[0])} '
                        f'{_num(sh.centre[1])})" fill="{sh.fill}" stroke="{sh.stroke}" '
                        f'stroke-width="{_num(sh.width)}"/>')
        elif sh.kind == "ball":
            gid = gradients.setdefault(sh.fill, f"g{len(gradients)}")
            body.append(f'<circle cx="{_num(sh.centre[0])}" cy="{_num(sh.centre[1])}" '
                        f'r="{_num(sh.axes[0])}" fill="url(#{gid})" stroke="{sh.stroke}" '
                        f'stroke-width="{_num(sh.width)}"/>')
        elif sh.kind in ("polyline", "polygon"):
            pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in sh.points)
            tag = "polygon" if sh.kind == "polygon" else "polyline"
            body.append(f'<{tag} points="{pts}" fill="{sh.fill}" stroke="{sh.stroke}" '
                        f'stroke-width="{_num(sh.width)}" stroke-linecap="round" '
                        f'stroke-linejoin="round"/>')
        elif sh.kind == "text":
            # a white halo first (a copy with a white outline), so a label stays legible
            # where it crosses a bond; then the label
            where = (f'x="{_num(sh.centre[0])}" y="{_num(sh.centre[1])}" '
                     f'font-family="{font}" font-size="{_num(sh.size)}"')
            body.append(f'<text {where} fill="#ffffff" stroke="#ffffff" '
                        f'stroke-width="{_num(sh.size * 0.25)}" stroke-linejoin="round">'
                        f'{html.escape(sh.text)}</text>')
            body.append(f'<text {where}>{html.escape(sh.text)}</text>')
    if gradients:
        out.append("<defs>")
        for colour, gid in gradients.items():
            out.append(f'<radialGradient id="{gid}" cx="35%" cy="35%" r="65%">'
                       f'<stop offset="0" stop-color="#ffffff"/>'
                       f'<stop offset="1" stop-color="{colour}"/></radialGradient>')
        out.append("</defs>")
    return "\n".join(out + body + ["</svg>"]) + "\n"
