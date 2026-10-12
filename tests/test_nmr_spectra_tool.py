"""The simulated-spectrum tool for the example SI (tools/nmr_spectra.py): multiplets,
integrals, peak labels, the PDF and its thinned-out spectrum line."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nmr_spectra_tool", ROOT / "tools" / "nmr_spectra.py")
nmr = importlib.util.module_from_spec(_spec)
sys.modules["nmr_spectra_tool"] = nmr
_spec.loader.exec_module(nmr)

PHENYLETHANOL_1H = [{"shift": [7.38, 7.25], "mult": "m", "nH": 5},
                    {"shift": 4.89, "mult": "q", "J": [6.5], "nH": 1},
                    {"shift": 1.85, "mult": "br s", "nH": 1},
                    {"shift": 1.49, "mult": "d", "J": [6.5], "nH": 3}]
PHENYLETHANOL_13C = [{"shift": 145.9, "type": "C"}, {"shift": 128.5, "count": 2},
                     {"shift": 127.5}, {"shift": 125.4, "count": 2}, {"shift": 70.4},
                     {"shift": 25.2, "type": "CH3"}]


@pytest.mark.parametrize("mult, counts, broad, multiplet", [
    ("s", [], False, False), ("d", [1], False, False), ("t", [2], False, False),
    ("q", [3], False, False), ("quint", [4], False, False), ("sext", [5], False, False),
    ("sept", [6], False, False), ("dd", [1, 1], False, False), ("dt", [1, 2], False, False),
    ("td", [2, 1], False, False), ("tt", [2, 2], False, False), ("dq", [1, 3], False, False),
    ("qd", [3, 1], False, False), ("ddd", [1, 1, 1], False, False),
    ("br s", [], True, False), ("m", [], False, True), ("br d", [1], True, False)])
def test_parse_multiplicity(mult, counts, broad, multiplet):
    assert nmr.parse_multiplicity(mult) == (counts, broad, multiplet)


def _rounded(pattern):
    return [(round(o, 4), round(w, 4)) for o, w in pattern]


def test_split_patterns():
    assert _rounded(nmr.split_pattern([(1, 6.5, 0.5)])) == [(-3.25, 0.5), (3.25, 0.5)]
    assert _rounded(nmr.split_pattern([(3, 6.5, 0.5)])) == [
        (-9.75, 0.125), (-3.25, 0.375), (3.25, 0.375), (9.75, 0.125)]
    dd = nmr.split_pattern([(1, 10.0, 0.5), (1, 2.0, 0.5)])
    assert _rounded(dd) == [(-6.0, 0.25), (-4.0, 0.25), (4.0, 0.25), (6.0, 0.25)]
    tt = nmr.split_pattern([(2, 8.6, 0.5), (2, 5.4, 0.5)])
    assert len(tt) == 9 and dict(_rounded(tt))[0.0] == 0.25    # centre line 4/16
    # Deuterium (spin 1): CDCl3 in ¹³C 1:1:1, CHD2 in ¹H 1:2:3:2:1.
    assert _rounded(nmr.split_pattern([(1, 32.0, 1.0)])) == [
        (-32.0, 0.3333), (0.0, 0.3333), (32.0, 0.3333)]
    assert [round(w * 9, 3) for _o, w in nmr.split_pattern([(2, 1.9, 1.0)])] == [1, 2, 3, 2, 1]


def test_roofing_leans_towards_the_partner():
    pattern = nmr.split_pattern([(1, 8.0, 0.5)], tilts=[0.2])
    (o_low, w_low), (o_high, w_high) = pattern
    assert o_high > o_low and w_high > w_low       # leans to higher ppm
    assert abs(w_low + w_high - 1) < 1e-12


def test_quartet_lines_in_ppm():
    lines = nmr.compound_lines("1H", [{"shift": 4.89, "mult": "q", "J": [6.5], "nH": 1}],
                               400, seed=1)
    ppm = sorted(line.ppm for line in lines)
    expected = [4.89 + k / 400 for k in (-9.75, -3.25, 3.25, 9.75)]
    assert ppm == pytest.approx(expected, abs=1e-9)
    areas = [line.area for line in sorted(lines, key=lambda x: x.ppm)]
    assert areas == pytest.approx([0.125, 0.375, 0.375, 0.125], rel=1e-9)


def test_multiplet_cluster_spans_its_range_with_its_area():
    lines = nmr.compound_lines("1H", [{"shift": [7.38, 7.25], "mult": "m", "nH": 5}], 400, 1)
    ppm = [line.ppm for line in lines]
    assert max(ppm) == pytest.approx(7.38) and min(ppm) == pytest.approx(7.25)
    assert sum(line.area for line in lines) == pytest.approx(5.0)
    assert len(lines) >= 6


def test_compound_areas_are_exactly_nH():
    sim = nmr.simulate_1h(PHENYLETHANOL_1H, mhz=400, seed=3)
    for i, peak in enumerate(PHENYLETHANOL_1H):
        own = [line.area for line in sim.lines if line.signal == i]
        assert sum(own) == pytest.approx(peak["nH"])


def test_integrals_proportional_to_nH():
    sim = nmr.simulate_1h(PHENYLETHANOL_1H, mhz=400, seed=3, impurities=False)
    values = sorted((left, value) for left, _right, value in sim.integrals)
    # High ppm first: aromatic 5, CH 1 (the reference, exactly), OH 1, CH3 3.
    by_shift = [v for _l, v in sorted(values, reverse=True)]
    assert by_shift[1] == pytest.approx(1.0)
    assert by_shift == pytest.approx([5, 1, 1, 3], rel=0.05)   # (broad OH: tails cut off)
    assert by_shift[3] == pytest.approx(3, rel=0.01)


def test_integral_can_be_typed():
    peaks = [dict(p) for p in PHENYLETHANOL_1H]
    peaks[0]["integral"] = 5.0
    sim = nmr.simulate_1h(peaks, seed=3)
    assert max(sim.integrals)[2] == 5.0


def test_peak_labels():
    sim = nmr.simulate_1h(PHENYLETHANOL_1H, mhz=400, seed=3)
    texts = [nmr.ppm_text(v) for v in sim.labels]
    for t in ("4.91", "4.90", "4.88", "4.87", "1.85", "1.50", "1.48", "7.38", "7.25"):
        assert t in texts
    assert "1.56" not in texts and "0.00" not in texts    # water and TMS are not labelled
    c = nmr.simulate_13c(PHENYLETHANOL_13C, mhz=101, seed=3)
    texts = [nmr.ppm_text(v) for v in c.labels]
    assert {"77.48", "77.16", "76.84"} <= set(texts)         # CDCl3 (exactly where it is)
    compound = [v for v in c.labels if not 76 < v < 78]
    assert len(compound) == len(PHENYLETHANOL_13C)
    # A shift reported with one decimal: the label (two decimals) rounds to it, but is not
    # the made-up-looking x.x0.
    for v, peak in zip(sorted(compound, reverse=True), PHENYLETHANOL_13C):
        assert f"{v:.1f}" == f"{peak['shift']:.1f}"
        assert not nmr.ppm_text(v).endswith("0")
    assert nmr.ppm_text(-0.001) == "0.00"


def test_reported_decimals_and_offset():
    assert nmr.reported_decimals({"shift": 128.6}) == 1
    assert nmr.reported_decimals({"shift": 146.0}) == 1
    assert nmr.reported_decimals({"shift": 123.94}) == 2
    assert nmr.reported_decimals({"shift": "128.60"}) == 2       # as written
    assert nmr.reported_decimals({"shift": 128.6, "decimals": 2}) == 2
    rng = np.random.default_rng(5)
    offsets = [nmr.reported_offset({"shift": 128.6}, rng) for _ in range(200)]
    assert all(0.006 <= abs(o) <= 0.04 for o in offsets)
    assert min(offsets) < 0 < max(offsets)                       # both ways
    assert all(f"{128.6 + o:.1f}" == "128.6" for o in offsets)
    assert nmr.reported_offset({"shift": 123.94}, rng) == 0.0    # two decimals: exact
    assert nmr.reported_offset({"shift": 128.6, "exact": True}, rng) == 0.0


def test_two_decimal_13c_shifts_stay_exact():
    sim = nmr.simulate_13c([{"shift": 123.94}, {"shift": 123.93}, {"shift": 70.6}],
                           mhz=151, seed=4)
    texts = [nmr.ppm_text(v) for v in sim.labels]
    assert "123.94" in texts and "123.93" in texts
    own = sorted(line.ppm for line in sim.lines if line.kind == "compound")
    assert own[1:] == pytest.approx([123.93, 123.94], abs=1e-12)
    assert own[0] != 70.6 and round(own[0], 1) == 70.6


def test_13c_solvent_and_quaternary_heights():
    sim = nmr.simulate_13c(PHENYLETHANOL_13C, mhz=101, seed=3)
    solvent = sorted((line.ppm, line.area) for line in sim.lines if line.kind == "solvent")
    assert [p for p, _a in solvent] == pytest.approx([77.16 - 32 / 101, 77.16, 77.16 + 32 / 101])
    assert solvent[0][1] == pytest.approx(solvent[1][1]) == pytest.approx(solvent[2][1])

    def height(shift):
        return max(sim.intensity[np.abs(sim.ppm - shift) < 0.05])
    assert height(145.9) < 0.5 * height(128.5)      # quaternary carbons are weaker


def test_cf_coupling_in_13c():
    sim = nmr.simulate_13c([{"shift": 162.4, "type": "C", "mult": "d", "J": [247.0]}], seed=2)
    low, high = sorted(line.ppm for line in sim.lines if line.kind == "compound")
    assert (high - low) * 101 == pytest.approx(247.0)
    assert round((high + low) / 2, 1) == 162.4 and abs((high + low) / 2 - 162.4) >= 0.006
    exact = nmr.simulate_13c([{"shift": 162.4, "type": "C", "mult": "d", "J": [247.0],
                               "exact": True}], seed=2)
    ppm = sorted(line.ppm for line in exact.lines if line.kind == "compound")
    assert ppm == pytest.approx([162.4 - 123.5 / 101, 162.4 + 123.5 / 101])


def test_oh_singlet_is_a_little_broader():
    peaks = [{"shift": 4.90, "mult": "q", "J": [6.5], "nH": 1},
             {"shift": 3.81, "mult": "s", "nH": 3},                # OMe: sharp
             {"shift": 1.96, "mult": "s", "nH": 1},                # OH: 2.5-4 Hz
             {"shift": 1.50, "mult": "d", "J": [6.5], "nH": 3}]
    assert [nmr.is_exchangeable(p) for p in peaks] == [False, False, True, False]
    assert not nmr.is_exchangeable({"shift": 9.95, "mult": "s", "nH": 1})     # CHO
    assert not nmr.is_exchangeable({"shift": 1.85, "mult": "br s", "nH": 1})  # (already br)
    assert not nmr.is_exchangeable({"shift": 1.96, "mult": "s", "nH": 1, "exchangeable": False})
    assert nmr.is_exchangeable({"shift": 6.10, "mult": "s", "nH": 1, "exchangeable": True})
    lines = nmr.compound_lines("1H", peaks, 500, seed=1)
    width = {line.signal: line.width for line in lines}
    assert 2.5 <= width[2] <= 4.0
    assert width[1] < 1.0 and width[3] < 1.0
    peaks[2]["width"] = 1.2                                       # typed: wins
    assert {x.signal: x.width for x in nmr.compound_lines("1H", peaks, 500, 1)}[2] == 1.2


def test_window_widened_to_clear_the_structure_corner():
    reserve = (180.0, 175.0)

    def x_of(v, hi, lo):            # as draw_pdf: the page width less two margins
        return nmr.MARGIN + (hi - v) / (hi - lo) * (792 - 2 * nmr.MARGIN)
    # Naphthalene ¹H (7.86): under the corner at 9.5 ppm, clear at the widened window.
    hi, lo = nmr.clear_of_reserve("1H", (9.5, -0.5), [7.86, 7.26, 1.6], reserve)
    assert lo == -0.5 and hi > 9.5 and (hi * 2) == int(hi * 2)       # ½ ppm steps
    assert x_of(7.86, 9.5, -0.5) < reserve[0]
    assert x_of(7.86, hi, lo) >= reserve[0] + nmr.RESERVE_GAP
    assert x_of(7.86, hi - 0.5, lo) < reserve[0] + nmr.RESERVE_GAP   # (no wider than needed)
    # Nothing under the corner, no corner, or an impossible case: unchanged.
    assert nmr.clear_of_reserve("1H", (9.5, -0.5), [7.0, 1.5], reserve) == (9.5, -0.5)
    assert nmr.clear_of_reserve("1H", (9.5, -0.5), [7.86], (0, 0)) == (9.5, -0.5)
    assert nmr.clear_of_reserve("1H", (9.5, -0.5), [9.4], (600, 175)) == (9.5, -0.5)
    # From a description: widened unless "xrange" is given.
    peaks = [{"shift": [7.86, 7.80], "mult": "m", "nH": 4}, {"shift": 1.59, "mult": "d",
                                                             "J": [6.4], "nH": 3}]
    sim = nmr.from_spec({"nucleus": "1H", "mhz": 600, "peaks": peaks})
    hi, lo = sim.xrange
    assert x_of(max(sim.labels), hi, lo) >= reserve[0] + nmr.RESERVE_GAP
    fixed = nmr.from_spec({"nucleus": "1H", "mhz": 600, "peaks": peaks, "xrange": [9.5, -0.5]})
    assert tuple(fixed.xrange) == (9.5, -0.5)


def test_19f_window_and_inset():
    peaks = [{"shift": -115.4, "mult": "tt", "J": [8.6, 5.4]}]
    sim = nmr.from_spec({"nucleus": "19F", "mhz": 376, "peaks": peaks})
    hi, lo = sim.xrange
    assert hi > -115.4 > lo and hi - lo <= 30
    assert len(sim.insets) == 1
    left, right = sim.insets[0]["range"]
    assert left > -115.4 + 14 / 376 and right < -115.4 - 14 / 376


def test_decimation_keeps_peak_maxima():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 100, 50_000)
    y = rng.normal(0, 0.01, x.size)
    spikes = [123, 9_876, 31_415, 49_999]
    y[spikes] = [5.0, 7.0, 3.0, 2.0]
    y[20_000] = -4.0
    xs, ys = nmr.decimate(x, y, 400)
    assert len(xs) <= 800
    assert ys.max() == 7.0 and ys.min() == -4.0
    for i in spikes:
        assert y[i] in ys and x[i] in xs
    assert np.all(np.diff(xs) >= 0)


def test_spread_labels_do_not_overlap():
    wanted = [100.0, 101.0, 101.5, 102.0, 300.0, 302.0, 5.0]
    placed = nmr.spread_labels(wanted, 9.0, 20.0, 700.0)
    order = np.argsort(wanted)
    xs = np.array(placed)[order]
    assert np.all(np.diff(xs) >= 9.0 - 1e-9)
    assert xs.min() >= 20.0 and xs.max() <= 700.0
    assert abs(placed[4] - 300.0) < 10              # a small group stays near its peaks


def test_pdf_has_one_page(qapp, tmp_path):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtPdf import QPdfDocument
    for nucleus, peaks in (("1H", PHENYLETHANOL_1H), ("13C", PHENYLETHANOL_13C)):
        sim = nmr.simulate(nucleus, peaks, 400 if nucleus == "1H" else 101, seed=3)
        out = nmr.draw_pdf(tmp_path / f"{nucleus}.pdf", sim)
        assert out.exists() and out.stat().st_size < 400_000
        data = QByteArray(out.read_bytes())
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        doc = QPdfDocument()
        doc.load(buffer)
        assert doc.pageCount() == 1
        size = doc.pagePointSize(0)
        assert size.width() > size.height()           # landscape
        doc.close()


def test_cli_reads_json(qapp, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(nmr, "require_font", lambda: "Arial")   # (the test platform may
                                                                # have no fonts)
    spec = {"out": "x.pdf", "nucleus": "19F", "mhz": 376,
            "peaks": [{"shift": -115.4, "mult": "tt", "J": [8.6, 5.4]}]}
    (tmp_path / "s.json").write_text(json.dumps({"spectra": [spec]}), encoding="utf-8")
    assert nmr.main([str(tmp_path / "s.json")]) == 0
    assert (tmp_path / "x.pdf").stat().st_size > 1000



def test_every_spectrum_says_it_is_simulated(qapp, tmp_path, monkeypatch):
    # A page cut out of the example SI must still say it is not real data. (The text
    # drawn is recorded: the test platform may have no fonts to put it in the PDF with.)
    drawn = []
    real = nmr._Pdf.text

    def text(self, x, y, s, *args, **kwargs):
        drawn.append(s)
        return real(self, x, y, s, *args, **kwargs)
    monkeypatch.setattr(nmr._Pdf, "text", text)
    spec = {"nucleus": "1H", "mhz": 400, "peaks": PHENYLETHANOL_1H}
    nmr.render_spec(spec, tmp_path / "a.pdf")
    assert nmr.WATERMARK in drawn and "Simulated" in nmr.WATERMARK
    drawn.clear()
    nmr.render_spec({**spec, "watermark": ""}, tmp_path / "b.pdf")
    assert drawn and nmr.WATERMARK not in drawn


def test_without_a_font_the_spectra_are_refused(qapp, monkeypatch):
    # Without a usable font Qt draws every digit as a solid box (Qt's "offscreen" platform
    # on Windows): the tool says so instead of making such spectra.
    from PySide6.QtGui import QFontDatabase
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a: ["Wingdings"]))
    assert nmr.usable_font() is None
    with pytest.raises(RuntimeError, match="solid box"):
        nmr.require_font()
    monkeypatch.setattr(QFontDatabase, "families",
                        staticmethod(lambda *a: ["Wingdings", "DejaVu Sans"]))
    assert nmr.require_font() == "DejaVu Sans"
