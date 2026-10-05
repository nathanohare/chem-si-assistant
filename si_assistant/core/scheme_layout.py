"""Placing compound ID labels on a scheme picture (SPEC §6.3.5, decision §13e).

When a scheme is copied from ChemDraw we get a picture *and* the structures
(atom coordinates). We find the scale and offset that line the atoms up with the
dark pixels ("ink") of the picture, then put each label just below its structure.

This is a best guess: labels can always be dragged by hand, and if the fit is
poor we say so and fall back to a simple row of labels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_WIDTH = 480          # work on a downsampled ink mask this wide (speed)
MIN_SCORE = 0.8          # real schemes score ~1.0; random speckle up to ~0.6
MAX_ATOMS = 80           # atoms used for fitting (evenly sampled)


@dataclass
class Fit:
    scale: float          # picture pixels per MOL unit (in the downsampled mask)
    dx: float
    dy: float
    score: float          # 0-1: how much better than chance the atoms land on ink
    width: int            # mask size the fit refers to
    height: int


def downsample(ink: np.ndarray, max_width: int = MAX_WIDTH) -> tuple[np.ndarray, int]:
    """Shrink a boolean mask by an integer factor, keeping any ink in each block."""
    h, w = ink.shape
    f = max(1, int(np.ceil(w / max_width)))
    if f == 1:
        return ink.copy(), 1
    H, W = -(-h // f), -(-w // f)
    padded = np.zeros((H * f, W * f), bool)
    padded[:h, :w] = ink
    return padded.reshape(H, f, W, f).any(axis=(1, 3)), f


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def _atoms(pieces: list[np.ndarray]) -> np.ndarray:
    pts = np.vstack([p[:, :2] for p in pieces]).astype(float)
    pts[:, 1] = -pts[:, 1]  # MOL y points up; picture rows point down
    if len(pts) > MAX_ATOMS:
        pts = pts[np.linspace(0, len(pts) - 1, MAX_ATOMS).astype(int)]
    return pts


def fit(ink: np.ndarray, pieces: list[np.ndarray]) -> Fit | None:
    """Best scale/offset mapping the pieces' atoms onto the ink of `ink` (downsampled)."""
    if not pieces or not ink.any():
        return None
    small, _f = downsample(ink)
    h, w = small.shape
    coarse_target = dilate(small, 2)   # forgiving, for the coarse 3-pixel search
    fine_target = dilate(small, 1)     # strict, for the final 1-pixel search and the score
    pts = _atoms(pieces)
    mx0, my0 = pts[:, 0].min(), pts[:, 1].min()
    span_x = pts[:, 0].max() - mx0
    span_y = pts[:, 1].max() - my0
    if span_x <= 0:
        return None
    cols = np.where(small.any(axis=0))[0]
    s0 = (cols[-1] - cols[0] + 1) / span_x
    rel = pts - [mx0, my0]

    def search(target, scales, step, dx_range=None, dy_range=None):
        best = (-1.0, 0.0, 0, 0)
        for s in scales:
            wpx, hpx = span_x * s, span_y * s
            if wpx >= w or hpx >= h:
                continue
            xs = np.round(rel[:, 0] * s).astype(int)
            ys = np.round(rel[:, 1] * s).astype(int)
            dxs = np.arange(*(dx_range or (0, int(w - wpx))), step)
            dys = np.arange(*(dy_range or (0, int(h - hpx))), step)
            dxs = dxs[(dxs >= 0) & (dxs + xs.max() < w)]
            dys = dys[(dys >= 0) & (dys + ys.max() < h)]
            if not len(dxs) or not len(dys):
                continue
            hits = target[ys[:, None, None] + dys[None, None, :],
                          xs[:, None, None] + dxs[None, :, None]]
            score = hits.mean(axis=0)
            i, j = np.unravel_index(np.argmax(score), score.shape)
            if score[i, j] > best[0]:
                best = (float(score[i, j]), float(s), int(dxs[i]), int(dys[j]))
        return best

    score, s, dx, dy = search(coarse_target, s0 * np.linspace(0.7, 1.15, 28), 3)
    if score < 0:
        return None
    hits, s, dx, dy = search(fine_target, s * np.linspace(0.97, 1.03, 13), 1,
                             (dx - 4, dx + 5), (dy - 4, dy + 5))
    # How much better than chance: random points would land on ink `density` of the time.
    density = float(fine_target.mean())
    score = max(0.0, (hits - density) / (1 - density)) if density < 1 else 0.0
    return Fit(s, dx - mx0 * s, dy - my0 * s, score, w, h)


def label_positions(ink: np.ndarray, pieces: list[np.ndarray],
                    bond_length: float) -> tuple[list[tuple[float, float]], float]:
    """(label centre (x, y) as fractions of the picture for each piece, fit score).

    Labels go just below each structure. With a poor fit (score below MIN_SCORE) the
    labels are spread in a row along the bottom instead, for the user to drag.
    """
    f = fit(ink, pieces)
    if f is None or f.score < MIN_SCORE:
        n = len(pieces)
        return [((i + 1) / (n + 1), 0.93) for i in range(n)], (f.score if f else 0.0)
    out = []
    gap = 0.9 * bond_length * f.scale
    for p in pieces:
        xs = p[:, 0] * f.scale + f.dx
        ys = -p[:, 1] * f.scale + f.dy
        cx = (xs.min() + xs.max()) / 2
        below = ys.max() + gap
        out.append((float(np.clip(cx / f.width, 0.02, 0.98)),
                    float(np.clip(below / f.height, 0.03, 0.97))))
    return out, f.score
