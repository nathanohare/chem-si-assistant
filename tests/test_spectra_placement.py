"""Issue #1: where the structure + ID box goes on a spectrum: over it in a corner, over it
in blank space (auto, optionally made smaller), above it or beside it."""

import numpy as np
import pytest
from pypdf import PdfReader

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import content_box, convert_to_png, image_info, ink_map
from si_assistant.compile.run import compile_pdf
from si_assistant.core import spectra as S
from tests.sample_project import build_sample


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def source(p, ink=None):
    result = build(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png,
                   crop=content_box, ink=ink)
    return result.typ_path.read_text(encoding="utf-8"), result.warnings


def calls(src):
    return src.split("si-spectrum(")[1:]


# ---------------------------------------------------------------------- the settings

def test_settings_are_checked():
    assert S.DEFAULTS["struct_place"] == "on" and S.DEFAULTS["auto_shrink"] is False
    assert S.clean({"struct_place": "beside", "auto_min_scale": 0.01, "auto_shrink": True}) == \
        {"struct_place": "beside", "auto_min_scale": S.SCALE_RANGE[0], "auto_shrink": True}
    assert S.clean({"struct_place": "inside-out", "auto_min_scale": "x"}) == {}


# ---------------------------------------------------------------------- finding blank space

def page(h=300, w=400):
    return np.full((h, w), 255, np.uint8)


def test_ink_map_keeps_thin_lines_and_round_trips():
    g = page()
    g[150, :] = 0                      # a one-pixel line
    ink = S.ink_map_of(g, cells=100)
    assert ink.shape == (75, 100) and ink.any(axis=1).sum() == 1
    assert (S.unpack_ink(S.pack_ink(ink)) == ink).all()
    assert S.unpack_ink({"shape": [5, 5], "bits": ""}) is None
    assert S.unpack_ink({}) is None


def test_blank_spot_prefers_the_corner_and_avoids_ink():
    g = page()
    g[:, 190:200] = 0                  # a tall peak in the middle
    ink = S.ink_map_of(g)
    assert S.blank_spot(ink, (0.3, 0.3), "top-left") == (0.15, 0.15)
    assert S.blank_spot(ink, (0.3, 0.3), "bottom-right") == (0.85, 0.85)
    assert S.blank_spot(ink, (0.6, 0.3)) is None          # wider than either half
    g[:30, :] = 0                       # ink along the top: the box moves down
    x, y = S.blank_spot(S.ink_map_of(g), (0.3, 0.3), "top-left")
    assert x == 0.15 and y >= 0.25


def test_auto_place_shrinks_down_to_the_minimum():
    g = page()
    g[:, 150:160] = 0                   # blank strips: 150, 140 and 90 of 400 px wide
    g[:, 300:310] = 0
    ink = S.ink_map_of(g)
    box_at = lambda scale: (2.0 * scale, 1.0 * scale)    # noqa: E731 (inches)
    shown = (4.0, 3.0)
    assert S.auto_place(ink, shown, box_at, 1.0, "top-left") is None
    x, y, scale = S.auto_place(ink, shown, box_at, 1.0, "top-left", shrink=True, min_scale=0.5)
    assert scale < 1.0 and 2.0 * scale / 4.0 < 150 / 400 and x < 0.4
    assert S.auto_place(ink, shown, box_at, 1.0, "top-left", shrink=True, min_scale=0.9) is None
    # Already small enough: the size is kept.
    assert S.auto_place(ink, shown, box_at, 0.5, "top-left", shrink=True)[2] == 0.5


def test_shown_size_leaves_room_above_or_beside():
    slot = (6.5, 4.0)
    on = S.shown_size_in(slot, 0.75)
    above = S.shown_size_in(slot, 0.75, place="above", box_in=(1.5, 1.2))
    beside = S.shown_size_in(slot, 0.75, place="beside", box_in=(1.5, 1.2))
    assert above[1] <= 4.0 - 1.2 and above[0] < on[0]
    assert beside[0] <= 6.5 - 1.5
    assert S.shown_size_in(slot, 0.75, place="auto", box_in=(1.5, 1.2)) == on


# ---------------------------------------------------------------------- compiling

def test_above_and_beside_in_the_source_and_pdf(sample):
    p, _parts = sample
    for place in ("above", "beside"):
        p.set_spectra_defaults({**p.spectra_defaults(), "struct_place": place})
        src, warnings = source(p)
        assert warnings == []
        assert all(f'spot: "{place}"' in c and "pos:" not in c for c in calls(src))
        result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                             convert_to_png, crop=content_box, ink=ink_map)
        assert result.pdf and len(PdfReader(str(result.pdf)).pages) > 2


def test_auto_finds_blank_space_on_the_spectra(sample):
    p, _parts = sample
    p.set_spectra_defaults({**p.spectra_defaults(), "struct_place": "auto"})
    src, warnings = source(p, ink=ink_map)
    assert warnings == []
    for c in calls(src):
        assert "pos: (" in c and "spot:" not in c
    assert (p.folder / "build" / S.InkCache.FILE).exists()      # remembered for next time
    # The spectra (sample_project.fake_spectrum_pdf) have peaks below the middle: the box
    # stays in the top part, nearest the top-left corner.
    x, y = (float(v) for v in calls(src)[0].split("pos: (")[1].split(")")[0].split(","))
    assert x < 0.5 and y < 0.5


def test_auto_without_room_goes_above_and_says_so(sample, monkeypatch):
    p, _parts = sample
    p.set_spectra_defaults({**p.spectra_defaults(), "struct_place": "auto"})
    full = lambda path, page: np.ones((50, 60), bool)    # noqa: E731 (ink everywhere)
    src, warnings = source(p, ink=full)
    assert all('spot: "above"' in c for c in calls(src))
    assert len(warnings) == 1 and "no blank space" in warnings[0]
    # Without anything to measure with (no Qt): above as well.
    src, warnings = source(p)
    assert all('spot: "above"' in c for c in calls(src))


def test_a_dragged_box_stays_where_it_was_put(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.set_spectra_defaults({**p.spectra_defaults(), "struct_place": "auto"})
    item = c2.values["spectra"][0]
    p.set_spectrum_overrides(c2, "spectra", item["file"], {"x": 0.7, "y": 0.6}, page="1")
    src, _warnings = source(p, ink=ink_map)
    assert "pos: (0.7000, 0.6000)" in src


def test_auto_placement_does_not_cover_ink(sample, tmp_path):
    """The compiled page: the area under the box was blank on the spectrum itself."""
    p, parts = sample
    p.set_spectra_defaults({**p.spectra_defaults(), "struct_place": "auto", "per_page": 1})
    src, _ = source(p, ink=ink_map)
    first = calls(src)[0]
    x, y = (float(v) for v in first.split("pos: (")[1].split(")")[0].split(","))
    sp = S.spectra_of(p, parts["c2"])[0]
    ink = ink_map(sp.path, sp.page)
    rows, cols = ink.shape
    r, c = int(y * rows), int(x * cols)
    assert not ink[max(0, r - 2):r + 3, max(0, c - 2):c + 3].any()
