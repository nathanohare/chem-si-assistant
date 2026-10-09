"""Draw realistic SIMULATED NMR spectra as PDF files that look like MestReNova printouts,
for the example SI shipped with the program. A developer tool: not part of the program.

    python tools/nmr_spectra.py spectra.json            (writes the PDFs named in the file)
    python tools/nmr_spectra.py spectra.json --png DIR  (also saves a PNG preview of each)

The spectra are made up from a peak list (first-order simulation), so they must only ever
be used where it is clear they are examples. Uses Qt's own platform (for the computer's
fonts); only NumPy (BSD-3-Clause) and PySide6 (LGPL-3.0), which the program already uses.

The JSON file holds one spectrum, or {"spectra": [...]} with several. A spectrum:

    {"out": "2_1H.pdf",            (relative to the JSON file's folder)
     "nucleus": "1H",              ("1H", "13C" or "19F")
     "mhz": 400,                   (spectrometer frequency for this nucleus)
     "solvent": "CDCl3",           (CDCl3, DMSO-d6, CD3OD, C6D6, acetone-d6, CD2Cl2)
     "seed": 1,                    (the same seed always gives the same spectrum)
     "peaks": [
        {"shift": [7.38, 7.25], "mult": "m", "nH": 5},
        {"shift": 4.89, "mult": "q", "J": [6.5], "nH": 1},
        {"shift": 1.85, "mult": "br s", "nH": 1},
        {"shift": 1.49, "mult": "d", "J": [6.5], "nH": 3}]}

¹H signals: "shift" (a range [from, to] for "m"), "mult" (s, d, t, q, quint, sext, sept,
dd, dt, td, tt, dq, qd, ddd, br s, m, ...; "br" widens any of them), "J" in Hz in the
order of the letters, "nH". Optional: "width" (line width at half height, Hz),
"integral" (the printed value), "labels": false (no peak labels for this signal),
"exchangeable" (an OH/NH: drawn 2.5–4 Hz wide unless "br"; by default a 1 H singlet
between 0.5 and 5.0 ppm is taken to be one; false turns that off).
¹³C signals: "shift", optional "type" ("C" for a quaternary carbon: a weaker line; "CH",
"CH2", "CH3"), "count" (equivalent carbons, e.g. 2), "mult"/"J" for C–F couplings,
"height" (relative height, overrides the automatic one).
¹⁹F signals: "shift", "mult", "J" (or "s" for a ¹H-decoupled spectrum), optional "nF".
¹³C and ¹⁹F: a shift reported with one decimal (128.6) is simulated a little off it
(0.006–0.040 ppm), so its two-decimal label looks like real peak picking (128.63) and still
rounds to the reported value; "exact": true keeps it on the value, "decimals" says how many
the reported value has (otherwise read from how "shift" is written).

Spectrum options (all optional): "xrange" [left, right] in ppm (without it, the window is
widened to the left when a labelled peak would sit under the reserved corner); "reference"
(the index of the ¹H signal whose integral is set to its nH; default the first sharp,
non-"m" one);
"impurities" (false: no residual solvent, water or TMS); "labels" (a list of ppm values to
label instead of the automatic peak picking); "label_solvent" (true); "integrals" (true for
¹H); "insets" (a list of {"range": [left, right], "box": [x0, y0, x1, y1] in points or
"auto"}; ¹⁹F multiplets get one automatically); "reserve" ([width, height] in points of
the top-left corner kept free for the structure and compound ID the program puts there;
[0, 0] for none); "page" ("letter" or "a4", landscape); "color" (the spectrum line);
"watermark" (the small grey note under the right end of the axis, by default WATERMARK
"Simulated spectrum (not a measurement)"; "" for none — only where the page around it
already says the spectrum is simulated).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------------------

NUCLEI = ("1H", "13C", "19F")
DEFAULT_RANGE = {"1H": (9.5, -0.5), "13C": (220.0, -10.0)}
GRID_HZ = {"1H": 0.1, "13C": 0.25, "19F": 0.2}          # data point spacing
LINE_WIDTH_HZ = {"1H": (0.6, 0.9), "13C": (1.0, 1.5), "19F": (0.8, 1.2)}
BROAD_HZ = (5.0, 15.0)                                   # "br" signals (¹H)
EXCHANGE_HZ = (2.5, 4.0)          # an OH/NH singlet not called "br": a little broader
EXCHANGE_PPM = (0.5, 5.0)         # where a lone 1 H singlet is taken to be OH/NH
# A ¹³C/¹⁹F shift reported with one decimal is simulated this far (ppm, either way) from
# it: real peak picking never lands exactly on x.x0, and the two-decimal label must still
# round to the reported value (|offset| < 0.05 minus a grid step).
REPORTED_OFFSET = (0.006, 0.040)
NOISE = {"1H": 0.0004, "13C": 0.0025, "19F": 0.0006}      # σ / tallest compound peak
LABEL_THRESHOLD = {"1H": 0.02, "13C": 0.04, "19F": 0.03}  # of the tallest compound peak

# Multiplicity words -> number of equivalent spin-½ neighbours.
MULT_WORDS = [("quint", 4), ("quin", 4), ("pent", 4), ("sext", 5), ("sept", 6), ("hept", 6),
              ("hex", 5), ("d", 1), ("t", 2), ("q", 3), ("p", 4)]

# Residual solvent signals: (ppm, number of deuterium neighbours, J(H,D) or J(C,D) in Hz,
# relative size). Water: (ppm, line width in Hz).
SOLVENTS = {
    "CDCl3": {"1H": [(7.26, 0, 0.0, 1.0)], "13C": [(77.16, 1, 32.0, 1.0)],
              "water": (1.56, 3.0), "tms": True},
    "DMSO-d6": {"1H": [(2.50, 2, 1.9, 1.0)], "13C": [(39.52, 3, 21.0, 1.0)],
                "water": (3.33, 6.0), "tms": False},
    "CD3OD": {"1H": [(3.31, 2, 1.7, 1.0)], "13C": [(49.00, 3, 21.4, 1.0)],
              "water": (4.87, 4.0), "tms": False},
    "C6D6": {"1H": [(7.16, 0, 0.0, 1.0)], "13C": [(128.06, 1, 24.3, 1.0)],
             "water": (0.40, 3.0), "tms": False},
    "acetone-d6": {"1H": [(2.05, 2, 2.2, 1.0)],
                   "13C": [(29.84, 3, 19.4, 1.0), (206.26, 0, 0.0, 0.25)],
                   "water": (2.84, 5.0), "tms": False},
    "CD2Cl2": {"1H": [(5.32, 1, 0.9, 1.0)], "13C": [(53.84, 2, 27.2, 1.0)],
               "water": (1.52, 3.0), "tms": False},
}
SOLVENT_1H_AREA = 0.10      # residual CHCl3 etc., in units of one compound H
WATER_1H_AREA = 0.12
TMS_1H_AREA = 0.06
SATELLITE = 0.0055          # each ¹³C satellite of a ¹H singlet (half of 1.1 %)
SOLVENT_13C_HEIGHT = 0.85   # tallest solvent line / tallest compound line


@dataclass
class Line:
    ppm: float
    area: float          # intensity × Hz
    width: float         # full width at half height, Hz
    kind: str            # "compound", "solvent", "impurity"
    signal: int = -1     # the compound signal it belongs to


@dataclass
class Simulation:
    nucleus: str
    mhz: float
    solvent: str
    ppm: np.ndarray                  # high to low
    intensity: np.ndarray            # what is drawn (noise included)
    tallest: float                   # tallest compound peak (no noise)
    labels: list[float]              # peak labels (ppm)
    integrals: list[tuple[float, float, float]]   # (left ppm, right ppm, printed value)
    xrange: tuple[float, float]
    lines: list[Line] = field(default_factory=list)
    insets: list[dict] = field(default_factory=list)


def parse_multiplicity(mult: str) -> tuple[list[int], bool, bool]:
    """'dt' -> ([1, 2], broad False, multiplet False); 'br s' -> ([], True, False);
    'm' -> ([], False, True). Unknown letters are ignored."""
    text = (mult or "s").lower().replace(".", " ").replace(",", " ")
    broad = "br" in text
    words = [w for w in text.split() if w not in ("br", "app", "apparent")]
    rest = "".join(words).replace("br", "")
    if rest in ("m", "mult", "multiplet"):
        return [], broad, True
    counts: list[int] = []
    i = 0
    while i < len(rest):
        for word, n in MULT_WORDS:
            if rest.startswith(word, i):
                counts.append(n)
                i += len(word)
                break
        else:
            i += 1        # "s", or something unknown
    return counts, broad, False


def split_pattern(couplings: list[tuple[int, float, float]],
                  tilts: list[float] | None = None) -> list[tuple[float, float]]:
    """Lines (offset Hz, weight; weights add up to 1) of a first-order multiplet.
    couplings: (number of equivalent neighbours, J in Hz, their spin: 0.5 or 1).
    tilts: for each coupling, the 'roof' (+ leans the pattern to higher ppm)."""
    lines = [(0.0, 1.0)]
    for c, (n, j, spin) in enumerate(couplings):
        if n <= 0:
            continue
        if spin >= 1:      # deuterium: (1 + x + x²)^n, e.g. 1:1:1, 1:2:3:2:1
            coeffs = np.array([1.0])
            for _ in range(n):
                coeffs = np.convolve(coeffs, [1.0, 1.0, 1.0])
            offsets = [(k - n) * j for k in range(2 * n + 1)]
        else:
            coeffs = np.array([float(math.comb(n, k)) for k in range(n + 1)])
            offsets = [(k - n / 2) * j for k in range(n + 1)]
        tilt = tilts[c] if tilts and c < len(tilts) else 0.0
        if tilt and j:
            coeffs = coeffs * np.array([1 + tilt * 2 * o / j / max(1, n) for o in offsets])
            coeffs = np.clip(coeffs, 0.0, None)
        coeffs = coeffs / coeffs.sum()
        lines = [(o0 + o, w0 * w) for o0, w0 in lines for o, w in zip(offsets, coeffs)]
    merged: dict[float, float] = {}
    for o, w in lines:
        key = round(o, 6)
        merged[key] = merged.get(key, 0.0) + w
    total = sum(merged.values())
    return sorted((o, w / total) for o, w in merged.items())


def _rng(seed: int, *salt: float) -> np.random.Generator:
    return np.random.default_rng([abs(int(seed))] + [abs(int(round(s * 1000))) for s in salt])


def _shifts(peak: dict) -> tuple[float, float]:
    """(high, low) ppm of a signal ("shift" a number or a range)."""
    s = peak["shift"]
    if isinstance(s, (list, tuple)):
        return max(float(s[0]), float(s[-1])), min(float(s[0]), float(s[-1]))
    return float(s), float(s)


def _couplings(peak: dict) -> tuple[list[tuple[int, float, float]], bool, bool]:
    counts, broad, multiplet = parse_multiplicity(str(peak.get("mult", "s")))
    js = [float(j) for j in (peak.get("J") or [])]
    if counts and not js:
        js = [7.0]
    couplings = []
    for i, n in enumerate(counts):
        couplings.append((n, js[min(i, len(js) - 1)], 0.5))
    return couplings, broad, multiplet


def reported_decimals(peak: dict) -> int:
    """How many decimals the reported shift has: "decimals" if given, else as written
    ("128.60" as text: 2; the number 128.6: 1; 123.94: 2)."""
    if peak.get("decimals") is not None:
        return int(peak["decimals"])
    s = peak["shift"]
    s = s[0] if isinstance(s, (list, tuple)) else s
    text = str(s).strip() if isinstance(s, str) else repr(float(s))
    if "e" in text.lower():
        return 2
    return len(text.split(".", 1)[1]) if "." in text else 0


def reported_offset(peak: dict, rng: np.random.Generator) -> float:
    """Where a ¹³C/¹⁹F signal is simulated, in ppm from its reported shift: a small random
    amount for a one-decimal value (so its two-decimal label reads like real peak picking,
    e.g. 128.63 for 128.6, and still rounds to it); 0 for two or more decimals, or with
    "exact": true."""
    if peak.get("exact") or reported_decimals(peak) >= 2:
        return 0.0
    lo, hi = REPORTED_OFFSET
    return float(rng.uniform(lo, hi) * rng.choice([-1.0, 1.0]))


def is_exchangeable(peak: dict) -> bool:
    """An OH/NH singlet (drawn a little broader than a C–H singlet): "exchangeable" if
    given, else a 1 H singlet not called "br" between 0.5 and 5.0 ppm."""
    if peak.get("exchangeable") is not None:
        return bool(peak["exchangeable"])
    counts, broad, multiplet = parse_multiplicity(str(peak.get("mult", "s")))
    high, low = _shifts(peak)
    return (not counts and not broad and not multiplet and high == low
            and abs(float(peak.get("nH", 0)) - 1) < 1e-9
            and EXCHANGE_PPM[0] <= high <= EXCHANGE_PPM[1])


def multiplet_cluster(high: float, low: float, area: float, mhz: float,
                      rng: np.random.Generator) -> list[tuple[float, float, float]]:
    """A plausible "m": (ppm, area, width Hz) lines spread from `high` to `low` ppm (the
    outermost lines exactly there) whose areas add up to `area`."""
    span = (high - low) * mhz
    aromatic = low > 5.5
    choices = ([[(1, 7.8)], [(2, 7.6)], [(1, 8.0), (1, 1.3)], [(2, 7.5), (1, 1.2)],
                [(2, 7.4), (2, 1.3)], [(1, 7.9), (1, 7.3), (1, 1.4)]] if aromatic else
               [[(2, 7.2)], [(1, 12.5), (2, 6.8)], [(2, 6.9), (2, 2.0)], [(3, 7.0)],
                [(4, 6.9)], [(1, 13.0), (1, 7.0), (1, 4.5)]])
    n_sub = int(np.clip(round(span / 16), 1, max(1, math.ceil(area))))
    if area >= n_sub and abs(area - round(area)) < 1e-9:
        parts = [area // n_sub] * n_sub
        for i in rng.permutation(n_sub)[: int(area) % n_sub]:
            parts[i] += 1
    else:
        parts = [area / n_sub] * n_sub
    subs = []
    for part in parts:
        pattern = choices[int(rng.integers(len(choices)))]
        couplings = [(n, j * (1 + rng.uniform(-0.04, 0.04)), 0.5) for n, j in pattern]
        subs.append((part, split_pattern(couplings)))
    # Centres from high to low; then stretch the whole cluster to [high, low].
    centres = np.linspace(0.0, -span, n_sub) if n_sub > 1 else np.array([-span / 2])
    centres = centres + rng.uniform(-2.0, 2.0, n_sub) * (n_sub > 1)
    raw = []
    for (part, lines), c in zip(subs, centres):
        roof = 0.08 * (1 if c < -span / 2 else -1)        # lean towards the cluster centre
        for o, w in lines:
            w = w * (1 + roof * np.sign(o)) * (1 + rng.uniform(-0.12, 0.12))
            raw.append([c + o, max(w, 0.02) * part])
    raw_arr = np.array(raw)
    hz = raw_arr[:, 0]
    lo_hz, hi_hz = hz.min(), hz.max()
    if n_sub > 1 or hi_hz - lo_hz > span:
        hz = (hz - lo_hz) / max(hi_hz - lo_hz, 1e-9) * span - span     # [-span, 0]
    else:
        hz = hz - (hi_hz + lo_hz) / 2 - span / 2
    areas = raw_arr[:, 1] * area / raw_arr[:, 1].sum()
    widths = rng.uniform(0.9, 1.3, len(hz))
    return [(high + h / mhz, a, w) for h, a, w in zip(hz, areas, widths)]


def _roof_tilts(i: int, signals: list[dict], couplings, mhz: float) -> list[float]:
    """Roofing of signal i: each coupling leans towards the signal (sharing that J) it
    couples to, more so the closer they are (AB-like: inner/outer ≈ (1 + J/Δν)/(1 − J/Δν))."""
    tilts = []
    me = signals[i]
    for n, j, _spin in couplings:
        best = None
        for k, other in enumerate(signals):
            if k == i or other["multiplet"] or not other["couplings"]:
                continue
            if any(abs(j - j2) <= 0.3 for _n, j2, _s in other["couplings"]):
                d = (other["center"] - me["center"]) * mhz
                if d and (best is None or abs(d) < abs(best)):
                    best = d
        tilts.append(float(np.clip(j / best, -0.35, 0.35)) if best else 0.0)
    return tilts


def compound_lines(nucleus: str, peaks: list[dict], mhz: float, seed: int) -> list[Line]:
    """The lines of the compound's signals. Areas: ¹H in units of one H (exactly nH per
    signal), ¹⁹F nF; ¹³C areas give the wanted peak heights."""
    signals = []
    for p in peaks:
        high, low = _shifts(p)
        couplings, broad, multiplet = _couplings(p)
        signals.append({"peak": p, "high": high, "low": low, "center": (high + low) / 2,
                        "couplings": couplings, "broad": broad, "multiplet": multiplet})
    lines: list[Line] = []
    lw_lo, lw_hi = LINE_WIDTH_HZ[nucleus]
    for i, s in enumerate(signals):
        p = s["peak"]
        rng = _rng(seed, s["center"], i)
        if p.get("width"):
            width = float(p["width"])
        elif s["broad"]:
            width = rng.uniform(*BROAD_HZ)
        elif nucleus == "1H" and is_exchangeable(p):
            width = rng.uniform(*EXCHANGE_HZ)
        else:
            width = rng.uniform(lw_lo, lw_hi)
        if nucleus != "1H" and not s["multiplet"]:
            s["center"] += reported_offset(p, _rng(seed, s["center"], i, 3.0))
        if nucleus == "13C":
            count = float(p.get("count", 1))
            kind = str(p.get("type", "")).upper()
            if p.get("height") is not None:
                height = float(p["height"])
            elif kind == "C" or (not kind and s["center"] > 165):
                height = rng.uniform(0.18, 0.35)
            elif kind == "CH3":
                height = rng.uniform(0.55, 0.85)
            else:
                height = rng.uniform(0.6, 0.95)
            height *= count
            # A Lorentzian of this width and area A is A·2/(π·w) high.
            area = height * math.pi * width / 2
            # (C–F couplings split this area, so each line of a doublet is half as high.)
        else:
            area = float(p.get("nH", p.get("nF", 1)))
        if s["multiplet"]:
            for ppm, a, w in multiplet_cluster(s["high"], s["low"], area, mhz, rng):
                lines.append(Line(ppm, a, w * (width / 0.75 if nucleus == "1H" else 1),
                                  "compound", i))
            continue
        tilts = _roof_tilts(i, signals, s["couplings"], mhz) if nucleus == "1H" else None
        for o, w in split_pattern(s["couplings"], tilts):
            lines.append(Line(s["center"] + o / mhz, area * w, width, "compound", i))
    return lines


def solvent_lines(nucleus: str, solvent: str, mhz: float, compound_height: float,
                  seed: int, with_impurities: bool = True) -> list[Line]:
    """Residual solvent (labelled like the compound), water, TMS and ¹³C satellites."""
    info = SOLVENTS.get(solvent)
    out: list[Line] = []
    if info is None or nucleus == "19F" or not with_impurities and nucleus == "1H":
        return out
    rng = _rng(seed, 99.0)
    for ppm, n, j, size in info.get(nucleus, []):
        pattern = split_pattern([(n, j, 1.0)]) if n else [(0.0, 1.0)]
        if nucleus == "1H":
            width = rng.uniform(0.6, 0.8)
            for o, w in pattern:
                out.append(Line(ppm + o / mhz, SOLVENT_1H_AREA * size * w, width, "solvent"))
            if not n:     # ¹³C satellites of CHCl3 (J = 209 Hz): very small
                for side in (-1, 1):
                    out.append(Line(ppm + side * 104.5 / mhz,
                                    SOLVENT_1H_AREA * size * SATELLITE, width, "impurity"))
        else:
            width = rng.uniform(1.1, 1.4)
            top = max(w for _o, w in pattern)
            height = compound_height * SOLVENT_13C_HEIGHT * size / top
            for o, w in pattern:
                out.append(Line(ppm + o / mhz, height * w * math.pi * width / 2, width,
                                "solvent"))
    if nucleus == "1H" and with_impurities:
        water_ppm, water_w = info["water"]
        out.append(Line(water_ppm, WATER_1H_AREA, water_w, "impurity"))
        if info.get("tms"):
            out.append(Line(0.0, TMS_1H_AREA, 0.7, "impurity"))
    return out


def satellite_lines(lines: list[Line], peaks: list[dict], mhz: float) -> list[Line]:
    """¹³C satellites (0.55 % each side, ±J(C,H)/2) of the compound's sharp ¹H singlets."""
    out = []
    for line in lines:
        if line.kind != "compound" or line.signal < 0:
            continue
        p = peaks[line.signal]
        counts, broad, multiplet = parse_multiplicity(str(p.get("mult", "s")))
        if counts or broad or multiplet or float(p.get("nH", 1)) < 2:
            continue
        jch = 145.0 if line.ppm > 3.0 else 128.0
        for side in (-1, 1):
            out.append(Line(line.ppm + side * jch / 2 / mhz, line.area * SATELLITE,
                            line.width + 0.3, "impurity"))
    return out


def lorentzians(hz: np.ndarray, lines: list[Line], mhz: float) -> np.ndarray:
    y = np.zeros_like(hz)
    for line in lines:
        g = line.width / 2
        y += line.area * g / math.pi / ((hz - line.ppm * mhz) ** 2 + g * g)
    return y


def pick_peaks(ppm: np.ndarray, y: np.ndarray, threshold: float) -> list[float]:
    """The ppm of each local maximum of `y` higher than `threshold` (high to low ppm)."""
    if len(y) < 3:
        return []
    mid = y[1:-1]
    idx = np.nonzero((mid > y[:-2]) & (mid >= y[2:]) & (mid > threshold))[0] + 1
    return [float(ppm[i]) for i in idx]


def auto_range(nucleus: str, lines: list[Line]) -> tuple[float, float]:
    """The ppm window drawn: ¹H 9.5 to −0.5, ¹³C 220 to −10 (widened if a signal is
    outside), ¹⁹F a window around the signals."""
    shifts = [line.ppm for line in lines if line.kind == "compound"] or [0.0]
    if nucleus in DEFAULT_RANGE:
        hi, lo = DEFAULT_RANGE[nucleus]
        step = 0.5 if nucleus == "1H" else 10.0
        hi = max(hi, math.ceil((max(shifts) + step) / step) * step)
        lo = min(lo, math.floor((min(shifts) - step) / step) * step)
        return hi, lo
    hi = math.ceil((max(shifts) + 5) / 5) * 5
    lo = math.floor((min(shifts) - 5) / 5) * 5
    return float(hi), float(lo)


def _integral_regions(lines: list[Line], peaks: list[dict], mhz: float
                      ) -> list[tuple[int, float, float]]:
    """(signal, left ppm, right ppm) of each signal's integral, neighbours not overlapping."""
    regions = []
    for i, p in enumerate(peaks):
        own = [line for line in lines if line.signal == i]
        if not own:
            continue
        pad = max(8.0, 6 * max(line.width for line in own)) / mhz
        regions.append([i, max(line.ppm for line in own) + pad,
                        min(line.ppm for line in own) - pad,
                        max(line.ppm for line in own), min(line.ppm for line in own)])
    regions.sort(key=lambda r: -r[1])
    for a, b in zip(regions, regions[1:]):
        if a[2] < b[1]:       # overlapping: meet half way between the outermost lines
            mid = (a[4] + b[3]) / 2
            a[2], b[1] = mid, mid
    return [(r[0], r[1], r[2]) for r in regions]


def simulate(nucleus: str, peaks: list[dict], mhz: float, solvent: str = "CDCl3",
             seed: int = 1, xrange: tuple[float, float] | None = None,
             impurities: bool = True, reference: int | None = None,
             labels: list[float] | None = None, label_solvent: bool = True,
             integrals: bool | None = None) -> Simulation:
    """A simulated spectrum: data points, peak labels and (¹H) integrals."""
    if nucleus not in NUCLEI:
        raise ValueError(f"nucleus must be one of {', '.join(NUCLEI)}")
    mhz = float(mhz)
    lines = compound_lines(nucleus, peaks, mhz, seed)
    hz_step = GRID_HZ[nucleus]
    # The tallest compound peak (for the size of the solvent's ¹³C lines).
    probe_hi, probe_lo = auto_range(nucleus, lines)
    if lines:
        local = np.arange(-200.0, 200.0, hz_step / 2)
        tallest_guess = max(
            float(lorentzians(local + line.ppm * mhz, lines, mhz).max()) for line in lines)
    else:
        tallest_guess = 1.0
    lines += solvent_lines(nucleus, solvent, mhz, tallest_guess, seed, impurities)
    if nucleus == "1H" and impurities:
        lines += satellite_lines(lines, peaks, mhz)
    hi, lo = (float(xrange[0]), float(xrange[1])) if xrange else (probe_hi, probe_lo)
    n = int(math.ceil((hi - lo) * mhz / hz_step)) + 1
    ppm = np.linspace(hi, lo, n)
    hz = ppm * mhz
    compound = lorentzians(hz, [x for x in lines if x.kind == "compound"], mhz)
    solvent_y = lorentzians(hz, [x for x in lines if x.kind == "solvent"], mhz)
    other = lorentzians(hz, [x for x in lines if x.kind == "impurity"], mhz)
    tallest = float(compound.max()) if compound.any() else float(max(solvent_y.max(), 1e-9))
    # Noise: white noise lightly smoothed (as after the usual line broadening).
    rng = _rng(seed, 7.0, len(peaks))
    noise = rng.normal(0.0, 1.0, n + 8)
    kernel = np.exp(-np.arange(5) / 1.5)
    noise = np.convolve(noise, kernel / kernel.sum(), mode="same")[4:-4]
    noise *= NOISE[nucleus] * tallest / max(noise.std(), 1e-12)
    intensity = compound + solvent_y + other + noise
    if labels is None:
        source = compound + (solvent_y if label_solvent else 0)
        picked = pick_peaks(ppm, source, LABEL_THRESHOLD[nucleus] * tallest)
        hidden = {i for i, p in enumerate(peaks) if p.get("labels") is False}
        if hidden:
            spans = [(_shifts(peaks[i])[0] + 0.02, _shifts(peaks[i])[1] - 0.02) for i in hidden]
            picked = [v for v in picked if not any(lo2 <= v <= hi2 for hi2, lo2 in spans)]
        labels = picked
    ints: list[tuple[float, float, float]] = []
    if integrals is None:
        integrals = nucleus == "1H"
    if integrals and peaks:
        regions = _integral_regions(lines, peaks, mhz)
        raw = {}
        for i, left, right in regions:
            mask = (ppm <= left) & (ppm >= right)
            raw[i] = float(intensity[mask].sum() * hz_step)
        if reference is None:
            reference = next((i for i, p in enumerate(peaks)
                              if not any(parse_multiplicity(str(p.get("mult", "s")))[1:])),
                             0)
        ref_n = float(peaks[reference].get("nH", peaks[reference].get("nF", 1)))
        unit = raw.get(reference, 1.0) / ref_n
        for i, left, right in regions:
            value = peaks[i].get("integral")
            value = float(value) if value is not None else raw[i] / unit
            ints.append((left, right, value))
    return Simulation(nucleus, mhz, solvent, ppm, intensity, tallest, list(labels), ints,
                      (hi, lo), lines)


def simulate_1h(peaks: list[dict], mhz: float = 400, solvent: str = "CDCl3", seed: int = 1,
                **kw) -> Simulation:
    return simulate("1H", peaks, mhz, solvent, seed, **kw)


def simulate_13c(peaks: list[dict], mhz: float = 101, solvent: str = "CDCl3", seed: int = 1,
                 **kw) -> Simulation:
    return simulate("13C", peaks, mhz, solvent, seed, **kw)


def simulate_19f(peaks: list[dict], mhz: float = 376, solvent: str = "CDCl3", seed: int = 1,
                 **kw) -> Simulation:
    return simulate("19F", peaks, mhz, solvent, seed, **kw)


# ---------------------------------------------------------------------------------------
# Drawing helpers (no Qt)
# ---------------------------------------------------------------------------------------

def decimate(x: np.ndarray, y: np.ndarray, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """At most two points (the lowest and the highest, in their own order) for each of
    `columns` equal slices of x (x increasing), so no peak or dip is lost when drawn."""
    n = len(x)
    if n <= 2 * columns:
        return x.copy(), y.copy()
    edges = np.linspace(x[0], x[-1], columns + 1)
    starts = np.searchsorted(x, edges[:-1], side="left")
    starts[0] = 0
    starts = np.unique(starts)
    ends = np.append(starts[1:], n)
    xs, ys = [], []
    for a, b in zip(starts, ends):
        if b <= a:
            continue
        seg = y[a:b]
        i_min = a + int(seg.argmin())
        i_max = a + int(seg.argmax())
        for i in sorted({i_min, i_max}):
            xs.append(x[i])
            ys.append(y[i])
    return np.array(xs), np.array(ys)


def spread_labels(wanted: list[float], size: float, low: float, high: float) -> list[float]:
    """Places for labels `size` wide, as close as possible to `wanted` (increasing), in
    the same order, never overlapping, kept between `low` and `high` (label centres)."""
    if not wanted:
        return []
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    xs = [wanted[i] for i in order]
    # Groups of touching labels, each centred on the mean of what its labels want.
    groups: list[list] = []        # [start index, count, centre of the first label]
    for i, x in enumerate(xs):
        groups.append([i, 1, x])
        while len(groups) > 1:
            a, b = groups[-2], groups[-1]
            if a[2] + a[1] * size <= b[2] + 1e-9:
                break
            count = a[1] + b[1]
            mean = sum(xs[a[0]:a[0] + count]) / count
            groups[-2:] = [[a[0], count, mean - (count - 1) * size / 2]]
    out = []
    for start, count, first in groups:
        out += [first + k * size for k in range(count)]
    # Keep inside [low, high]: push from the left, then from the right.
    span = (len(out) - 1) * size
    if span > high - low:      # too many to fit: squeeze them (better than leaving the page)
        size = (high - low) / max(1, len(out) - 1)
        out = [low + k * size for k in range(len(out))]
    else:
        for k in range(len(out)):
            out[k] = max(out[k], low + k * size)
        for k in range(len(out) - 1, -1, -1):
            out[k] = min(out[k], high - (len(out) - 1 - k) * size)
    result = [0.0] * len(out)
    for i, v in zip(order, out):
        result[i] = v
    return result


def nice_steps(span: float) -> tuple[float, float]:
    """(major, minor) tick steps for a ppm axis `span` wide (about 20 labelled ticks)."""
    for major in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100):
        if span / major <= 24:
            break
    lead = float(f"{major:e}"[0])
    return float(major), float(major) / (5 if lead in (1.0, 5.0) else 4)


def ppm_text(v: float, decimals: int = 2) -> str:
    text = f"{v:.{decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


# ---------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------

PAGES = {"letter": (792.0, 612.0), "a4": (842.0, 595.0)}
RESOLUTION = 720                       # PDF device units per inch (10 per point)
FONTS = ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"]
LABEL_PT = 8.5
AXIS_PT = 9.5
INTEGRAL_PT = 8.5
LABEL_COLOR = "#00007F"
INTEGRAL_COLOR = "#007F00"
SPECTRUM_COLOR = "#1E1E1E"
MARGIN = 36.0
LABEL_BOTTOM = 66.0       # where the peak labels end (they are written upwards)
LEADER = (3.0, 6.0, 24.0, 30.0)   # the leader: vertical, sloping, vertical (from LABEL_BOTTOM)
# The top-left area kept free for the structure + compound ID + caption box that the
# program puts over the spectrum (white, so whatever is under it is hidden). Measured on
# the compiled example SI (ACS, one landscape spectrum per page): the box reaches about
# 168 × 160 pt of this page; this leaves room for a structure up to the program's largest
# size (spectra.STRUCTURE_BOX_IN).
RESERVE = (180.0, 175.0)
RESERVE_GAP = 6.0         # labelled peaks (and their leaders) at least this far right of it
# Written on every spectrum (small, grey, under the right end of the axis), so a page cut
# out of the example SI, or a screenshot of it, still says it is not real data.
WATERMARK = "Simulated spectrum (not a measurement)"
WATERMARK_PT = 9.0
WATERMARK_COLOR = "#8C8C8C"


class _Pdf:
    """A QPainter on a landscape PDF page, used in points (fonts kept exact)."""

    def __init__(self, path: Path, page: str):
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
        self.width, self.height = PAGES.get(page, PAGES["letter"])
        self.writer = QPdfWriter(str(path))
        self.writer.setResolution(RESOLUTION)
        size_id = QPageSize.PageSizeId.A4 if page == "a4" else QPageSize.PageSizeId.Letter
        self.writer.setPageSize(QPageSize(size_id))
        self.writer.setPageOrientation(QPageLayout.Orientation.Landscape)
        self.writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
        self.writer.setCreator("Chem SI Assistant tools/nmr_spectra.py (simulated spectrum)")
        self.u = RESOLUTION / 72.0
        self.painter = QPainter(self.writer)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.painter.scale(self.u, self.u)
        self._fonts = {}

    def font(self, size: float):
        from PySide6.QtGui import QFont, QFontMetricsF
        if size not in self._fonts:
            f = QFont()
            f.setFamilies(FONTS)
            f.setPointSizeF(size)
            self._fonts[size] = (f, QFontMetricsF(f, self.writer))
        return self._fonts[size]

    def text_size(self, text: str, size: float) -> tuple[float, float]:
        """(width, digit height) in points; estimated if no font can be measured."""
        _f, fm = self.font(size)
        w, cap = fm.horizontalAdvance(text) / self.u, fm.capHeight() / self.u
        if w <= 0:
            w = 0.556 * size * len(text)
        if cap <= 0:
            cap = 0.716 * size
        return w, cap

    def pen(self, color: str, width: float):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPen
        p = QPen(QColor(color), width)
        p.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.painter.setPen(p)

    def polyline(self, xs, ys) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        self.painter.drawPolyline(QPolygonF([QPointF(float(x), float(y))
                                             for x, y in zip(xs, ys)]))

    def line(self, x0, y0, x1, y1) -> None:
        from PySide6.QtCore import QPointF
        self.painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def text(self, x: float, y: float, text: str, size: float, color: str,
             rotate: bool = False) -> None:
        """Text with its baseline starting at (x, y) points; rotate: written upwards."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor
        f, _fm = self.font(size)
        self.painter.save()
        self.painter.resetTransform()
        self.painter.translate(x * self.u, y * self.u)
        if rotate:
            self.painter.rotate(-90)
        self.painter.setFont(f)
        self.painter.setPen(QColor(color))
        self.painter.drawText(QPointF(0, 0), text)
        self.painter.restore()

    def clip(self, x0, y0, x1, y1) -> None:
        from PySide6.QtCore import QRectF
        self.painter.setClipRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def unclip(self) -> None:
        self.painter.setClipping(False)

    def end(self) -> None:
        self.painter.end()


def _draw_axis(pdf: _Pdf, left: float, right: float, y: float, hi: float, lo: float,
               size: float = AXIS_PT, title: bool = True, max_ticks: int = 24) -> None:
    span = hi - lo
    major, minor = nice_steps(span * 24 / max_ticks)
    decimals = max(0, -int(math.floor(math.log10(major) + 1e-9)))
    to_x = lambda v: left + (hi - v) / span * (right - left)  # noqa: E731
    pdf.pen("#000000", 0.6)
    pdf.line(left, y, right, y)
    k = math.ceil(lo / minor - 1e-9)
    while k * minor <= hi + 1e-9:
        v = k * minor
        is_major = abs(v / major - round(v / major)) < 1e-6
        x = to_x(v)
        pdf.pen("#000000", 0.6 if is_major else 0.45)
        pdf.line(x, y, x, y + (4.5 if is_major else 2.5) * size / AXIS_PT)
        if is_major:
            label = ppm_text(v, decimals)
            w, cap = pdf.text_size(label, size)
            if left - 2 <= x - w / 2 and x + w / 2 <= right + 2:
                pdf.text(x - w / 2, y + 6 * size / AXIS_PT + cap, label, size, "#000000")
        k += 1
    if title:
        label = "f1 (ppm)"
        w, cap = pdf.text_size(label, size)
        pdf.text((left + right) / 2 - w / 2, y + 9 + 2.4 * cap, label, size, "#000000")


def _auto_inset_box(sim: Simulation, to_x, y_of, top: float, bottom: float,
                    left: float, right: float, rng: tuple[float, float],
                    reserve: tuple[float, float]) -> tuple[float, float, float, float]:
    """A free place for an inset: above the lowest part of the spectrum, not over the
    expanded signal or the reserved corner."""
    w, h = 190.0, 150.0
    xs = to_x(sim.ppm)
    page_y = y_of(sim.intensity)
    best, best_score = None, -1e9
    for x0 in np.linspace(left + 10, right - w - 10, 40):
        if x0 < reserve[0] and top < reserve[1]:
            continue
        mask = (xs >= x0 - 8) & (xs <= x0 + w + 8)
        highest = float(page_y[mask].min()) if mask.any() else bottom
        sig = (to_x(rng[0]) <= x0 + w + 8) and (to_x(rng[1]) >= x0 - 8)
        score = highest - (1000 if sig else 0) + 0.05 * x0    # low spectrum; to the right
        if score > best_score:
            best, best_score = (float(x0), top, float(x0) + w, top + h), score
    return best


def _draw_inset(pdf: _Pdf, sim: Simulation, box, rng, color: str) -> None:
    x0, y0, x1, y1 = box
    hi, lo = max(rng), min(rng)
    mask = (sim.ppm <= hi) & (sim.ppm >= lo)
    if mask.sum() < 3:
        return
    ppm, y = sim.ppm[mask], sim.intensity[mask]
    axis_y = y1 - 18
    base = axis_y - 6
    scale = 0.85 * (base - y0) / max(float(y.max()), 1e-12)
    xs = x0 + (hi - ppm) / (hi - lo) * (x1 - x0)
    dx, dy = decimate(xs, base - y * scale, int((x1 - x0) * 4))
    pdf.clip(x0, y0, x1, axis_y - 1)
    pdf.pen(color, 0.5)
    pdf.polyline(dx, dy)
    pdf.unclip()
    _draw_axis(pdf, x0, x1, axis_y, hi, lo, size=7.0, title=False, max_ticks=5)


def _labels_pass_reserve(reserve) -> bool:
    """Whether the peak labels' row (and their leaders) runs through the reserved corner."""
    return reserve[0] > 0 and LABEL_BOTTOM - 30 < reserve[1]


def clear_of_reserve(nucleus: str, xrange: tuple[float, float], labels: list[float],
                     reserve: tuple[float, float] = RESERVE, page: str = "letter"
                     ) -> tuple[float, float]:
    """The ppm window, widened to the left (in ½ ppm steps for ¹H, 10 for ¹³C, 5 for ¹⁹F)
    until every labelled peak is right of the reserved corner: there the program puts a
    white box (the structure), which would hide the peaks' leaders and tick ends. The
    window is kept as it is if nothing is under the corner, or if even a window twice as
    wide would not clear it."""
    hi, lo = float(xrange[0]), float(xrange[1])
    reserve = tuple(reserve or (0.0, 0.0))
    if not labels or not _labels_pass_reserve(reserve):
        return hi, lo
    width = PAGES.get(page, PAGES["letter"])[0]
    left, right = MARGIN, width - MARGIN
    need = reserve[0] + RESERVE_GAP
    step = {"1H": 0.5, "13C": 10.0}.get(nucleus, 5.0)
    top = max(labels)
    new_hi = hi
    while left + (new_hi - top) / (new_hi - lo) * (right - left) < need:
        new_hi += step
        if new_hi - lo > 2 * (hi - lo):
            return hi, lo
    return new_hi, lo


def draw_pdf(path: str | Path, sim: Simulation, *, page: str = "letter",
             reserve: tuple[float, float] = RESERVE, insets: list[dict] | None = None,
             color: str = SPECTRUM_COLOR, watermark: str = WATERMARK) -> Path:
    """Draw `sim` on one landscape page as a vector PDF, Mnova style: peak labels with
    leader lines at the top, the spectrum, integrals under it (¹H), the ppm axis, and
    `watermark` in small grey letters under the right end of the axis ("" for none)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = _Pdf(path, page)
    width, height = pdf.width, pdf.height
    left, right = MARGIN, width - MARGIN
    hi, lo = sim.xrange
    to_x = lambda v: left + (hi - np.asarray(v)) / (hi - lo) * (right - left)  # noqa: E731
    axis_y = height - 62.0
    has_int = bool(sim.integrals)
    base = axis_y - (50.0 if has_int else 22.0)
    plot_top = LABEL_BOTTOM + LEADER[3] + 8
    scale = 0.8 * (base - plot_top) / max(sim.tallest, 1e-12)
    y_of = lambda v: base - np.asarray(v) * scale  # noqa: E731
    reserve = tuple(reserve or (0.0, 0.0))

    # The spectrum.
    xs, ys = decimate(to_x(sim.ppm)[::1], y_of(sim.intensity), int((right - left) * 4))
    pdf.clip(left, 20, right, axis_y - 2)
    pdf.pen(color, 0.55)
    pdf.polyline(xs, ys)
    pdf.unclip()

    # Peak labels: written upwards, ending at LABEL_BOTTOM, joined to their peaks.
    if sim.labels:
        texts = [ppm_text(v) for v in sorted(sim.labels, reverse=True)]
        wanted = [float(to_x(v)) for v in sorted(sim.labels, reverse=True)]
        _w, cap = pdf.text_size("0", LABEL_PT)
        pitch = LABEL_PT * 1.08
        low = left + pitch / 2
        if _labels_pass_reserve(reserve):        # the label row passes the reserved corner
            low = max(low, reserve[0] + RESERVE_GAP + pitch / 2)
        placed = spread_labels(wanted, pitch, low, right - pitch / 2)
        pdf.pen(LABEL_COLOR, 0.4)
        a, b, c, d = (LABEL_BOTTOM + v for v in LEADER)
        for text, x_peak, x_lab in zip(texts, wanted, placed):
            pdf.polyline([x_lab, x_lab, x_peak, x_peak], [a, b, c, d])
        for text, x_lab in zip(texts, placed):
            pdf.text(x_lab + cap / 2, LABEL_BOTTOM, text, LABEL_PT, LABEL_COLOR, rotate=True)

    # Integrals: a curve over each signal, a bracket and the value under the spectrum.
    if has_int:
        totals = []
        for left_ppm, right_ppm, _v in sim.integrals:
            m = (sim.ppm <= left_ppm) & (sim.ppm >= right_ppm)
            totals.append(float(sim.intensity[m].sum()))
        k_int = 0.2 * (base - plot_top) / max(max(totals), 1e-12)
        pdf.pen(INTEGRAL_COLOR, 0.5)
        for (left_ppm, right_ppm, _v), total in zip(sim.integrals, totals):
            m = (sim.ppm <= left_ppm) & (sim.ppm >= right_ppm)
            if m.sum() < 2:
                continue
            cum = np.cumsum(sim.intensity[m])
            px = to_x(sim.ppm[m])
            start = float(y_of(sim.intensity[m].max())) - 10
            start = min(start, base - 12)
            start = max(start, plot_top + total * k_int + 4)
            step = max(1, len(cum) // 300)
            pdf.polyline(px[::step], start - cum[::step] * k_int)
        bracket_y = base + 8
        _w, cap = pdf.text_size("0", INTEGRAL_PT)
        centres = [float(to_x((l + r) / 2)) for l, r, _v in sim.integrals]
        placed = spread_labels(centres, INTEGRAL_PT * 1.08, left, right)
        for (left_ppm, right_ppm, value), xc in zip(sim.integrals, placed):
            x0, x1 = float(to_x(left_ppm)), float(to_x(right_ppm))
            pdf.pen(INTEGRAL_COLOR, 0.5)
            pdf.line(x0, bracket_y, x1, bracket_y)
            pdf.line(x0, bracket_y - 2.5, x0, bracket_y + 2.5)
            pdf.line(x1, bracket_y - 2.5, x1, bracket_y + 2.5)
            text = f"{value:.2f}"
            tw, _cap = pdf.text_size(text, INTEGRAL_PT)
            pdf.text(xc + cap / 2, bracket_y + 5 + tw, text, INTEGRAL_PT, LABEL_COLOR,
                     rotate=True)

    # Insets (expanded multiplets).
    for inset in (sim.insets if insets is None else insets):
        rng = inset["range"]
        box = inset.get("box", "auto")
        if box == "auto" or not box:
            box = _auto_inset_box(sim, to_x, y_of, plot_top + 4, base, left, right,
                                  (max(rng), min(rng)), reserve)
        _draw_inset(pdf, sim, box, rng, color)

    _draw_axis(pdf, left, right, axis_y, hi, lo)
    if watermark:
        # On the line of the axis title ("f1 (ppm)", in the middle), ending at the right.
        w, _cap = pdf.text_size(watermark, WATERMARK_PT)
        _w, axis_cap = pdf.text_size("0", AXIS_PT)
        pdf.text(right - w, axis_y + 9 + 2.4 * axis_cap, watermark, WATERMARK_PT,
                 WATERMARK_COLOR)
    pdf.end()
    return path


def auto_insets(nucleus: str, peaks: list[dict], mhz: float) -> list[dict]:
    """¹⁹F: an expansion of each split signal (they are too narrow to see otherwise)."""
    if nucleus != "19F":
        return []
    out = []
    for p in peaks:
        couplings, _broad, multiplet = _couplings(p)
        if not couplings and not multiplet:
            continue
        high, low = _shifts(p)
        width = sum(n * j for n, j, _s in couplings) / mhz
        pad = max(0.04, 0.6 * width) + REPORTED_OFFSET[1]   # (the line may sit off the shift)
        out.append({"range": [high + width / 2 + pad, low - width / 2 - pad], "box": "auto"})
    return out[:2]


# ---------------------------------------------------------------------------------------
# From a JSON description
# ---------------------------------------------------------------------------------------

def from_spec(spec: dict) -> Simulation:
    """The simulation a spectrum description asks for. Without an "xrange", the window
    is widened if a labelled peak would sit under the reserved corner (clear_of_reserve)."""
    nucleus = spec.get("nucleus", "1H")
    mhz = spec.get("mhz") or {"1H": 400, "13C": 101, "19F": 376}[nucleus]

    def run(xrange):
        return simulate(nucleus, spec.get("peaks", []), mhz, spec.get("solvent", "CDCl3"),
                        int(spec.get("seed", 1)), xrange=xrange,
                        impurities=spec.get("impurities", True),
                        reference=spec.get("reference"), labels=spec.get("labels"),
                        label_solvent=spec.get("label_solvent", True),
                        integrals=spec.get("integrals"))
    sim = run(spec.get("xrange"))
    if not spec.get("xrange"):
        wider = clear_of_reserve(nucleus, sim.xrange, sim.labels,
                                 tuple(spec.get("reserve", RESERVE)), spec.get("page", "letter"))
        if wider != tuple(sim.xrange):
            sim = run(wider)
    sim.insets = spec["insets"] if "insets" in spec else auto_insets(
        nucleus, spec.get("peaks", []), float(mhz))
    return sim


def render_spec(spec: dict, out: Path) -> Path:
    sim = from_spec(spec)
    return draw_pdf(out, sim, page=spec.get("page", "letter"),
                    reserve=tuple(spec.get("reserve", RESERVE)),
                    color=spec.get("color", SPECTRUM_COLOR),
                    watermark=spec.get("watermark", WATERMARK))


def save_png(pdf_path: Path, png_path: Path, dpi: float = 110) -> None:
    sys.path.insert(0, str(ROOT))
    from si_assistant.compile.qt_images import render_page
    image = render_page(pdf_path, 1, dpi=dpi)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(png_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw simulated NMR spectra (example SI).")
    parser.add_argument("json", type=Path, help="spectrum description (see the top of this file)")
    parser.add_argument("--png", type=Path, default=None, help="also save PNG previews here")
    parser.add_argument("--dpi", type=float, default=110, help="PNG resolution")
    args = parser.parse_args(argv)
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    data = json.loads(args.json.read_text(encoding="utf-8"))
    specs = data["spectra"] if isinstance(data, dict) and "spectra" in data else (
        data if isinstance(data, list) else [data])
    for n, spec in enumerate(specs, 1):
        out = args.json.parent / spec.get("out", f"{args.json.stem}_{n}.pdf")
        path = render_spec(spec, out)
        print(f"{path} ({path.stat().st_size / 1000:.0f} KB)")
        if args.png:
            save_png(path, args.png / (path.stem + ".png"), args.dpi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
