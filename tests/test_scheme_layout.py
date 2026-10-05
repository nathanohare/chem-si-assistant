import numpy as np
import pytest

from si_assistant.core import chem
from si_assistant.core.scheme_layout import MIN_SCORE, fit, label_positions


def pieces_side_by_side(*smiles, gap=6.0):
    """Molecules laid out left to right with `gap` MOL units between them."""
    out, x = [], 0.0
    for smi in smiles:
        pos = chem.parse_structure(smi).GetConformer().GetPositions()[:, :2].copy()
        pos[:, 0] += x - pos[:, 0].min()
        pos[:, 1] -= pos[:, 1].mean()
        out.append(pos)
        x = pos[:, 0].max() + gap
    return out


def draw(pieces, smiles, scale, dx, dy, size=(500, 1400)):
    """Render bonds as lines plus an arrow and a reagent text block, like a ChemDraw scheme."""
    ink = np.zeros(size, bool)

    def line(x0, y0, x1, y1):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for t in np.linspace(0, 1, n):
            x, y = int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))
            ink[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2] = True

    for pos, smi in zip(pieces, smiles):
        mol = chem.parse_structure(smi)
        px = pos[:, 0] * scale + dx
        py = -pos[:, 1] * scale + dy
        for b in mol.GetBonds():
            i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
            line(px[i], py[i], px[j], py[j])
    # arrow between the molecules, with "conditions" text above it
    left_end = pieces[0][:, 0].max() * scale + dx + 20
    right_start = pieces[1][:, 0].min() * scale + dx - 20
    line(left_end, dy, right_start, dy)
    ink[int(dy) - 60:int(dy) - 30, int(left_end) + 20:int(right_start) - 20] = True
    return ink


SMILES = ("OC(=O)c1ccccc1", "CCOC(=O)c1ccc(F)cc1")


def test_fit_recovers_scale_and_offset():
    pieces = pieces_side_by_side(*SMILES)
    ink = draw(pieces, SMILES, scale=40, dx=120, dy=260)
    f = fit(ink, pieces)
    assert f.score > 0.9
    # The mask is downsampled 3x (1400 px -> 467), so scale 40 -> ~13.3 per mask pixel.
    assert f.scale * 3 == pytest.approx(40, rel=0.05)


def test_labels_land_under_each_structure():
    pieces = pieces_side_by_side(*SMILES)
    ink = draw(pieces, SMILES, scale=40, dx=120, dy=260)
    positions, score = label_positions(ink, pieces, bond_length=1.0)
    assert score > MIN_SCORE
    h, w = ink.shape
    for pos, (fx, fy) in zip(pieces, positions):
        centre_x = ((pos[:, 0].min() + pos[:, 0].max()) / 2 * 40 + 120) / w
        bottom_y = (-pos[:, 1].min() * 40 + 260) / h
        assert fx == pytest.approx(centre_x, abs=0.03)
        assert bottom_y < fy < bottom_y + 0.15  # just below the structure


def test_poor_fit_falls_back_to_a_row():
    pieces = pieces_side_by_side(*SMILES)
    rng = np.random.default_rng(0)
    noise = rng.random((300, 900)) > 0.995  # nothing like the molecules
    positions, score = label_positions(noise, pieces, bond_length=1.0)
    assert score < MIN_SCORE
    assert positions == [(1 / 3, 0.93), (2 / 3, 0.93)]
