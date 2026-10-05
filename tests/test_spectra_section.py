"""The Spectra section (SPEC §13k.12): the section itself, its defaults and each spectrum's
own settings, auto-crop, and the Typst/PDF output in every layout and style."""

import json
import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import content_box, convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.core import front
from si_assistant.core import spectra as S
from si_assistant.core.project import Project, ProjectError
from tests.sample_project import FACTS, build_sample, fake_spectrum_pdf


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def source(p, crop=None, **kw) -> tuple[str, list[str]]:
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    result = build(p, options, image_info, convert_to_png, crop=crop)
    return result.typ_path.read_text(encoding="utf-8"), result.warnings


def compiled(p, crop=None):
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                         convert_to_png, crop=crop)
    return PdfReader(str(result.pdf)), result.warnings


def spectrum_calls(src: str) -> list[str]:
    """The si-spectrum(...) calls in the source, one string each (up to the next one)."""
    return src.split("si-spectrum(")[1:]


# ---------------------------------------------------------------------- the section

def test_an_older_project_gets_a_spectra_section_with_its_settings(project):
    data = project.add_section("data", "Products")
    project.settings.update({"spectra_per_page": 2, "structure_corner": "bottom-right"})
    project.save_settings()
    old = Project.open(project.folder)
    assert old.spectra_section is None
    assert old.spectra_defaults()["per_page"] == 2          # (until it is added)
    section = old.ensure_spectra_section()
    assert section.type == "spectra" and section.name == "Spectra"
    assert [s.id for s in old.top_sections] == [data.id, section.id]   # at the end
    saved = json.loads((old.folder / "project.json").read_text(encoding="utf-8"))
    assert "spectra_per_page" not in saved and "structure_corner" not in saved
    stored = json.loads((section.folder / "section.json").read_text(encoding="utf-8"))
    assert stored["defaults"]["per_page"] == 2
    assert stored["defaults"]["corner"] == "bottom-right"
    assert stored["defaults"]["header_line"] is True
    assert old.ensure_spectra_section() is section          # only ever one
    again = Project.open(project.folder)
    assert [s.type for s in again.sections].count("spectra") == 1
    assert again.spectra_defaults()["corner"] == "bottom-right"


def test_a_read_only_project_is_not_changed(project):
    ro = Project.open(project.folder, read_only=True)
    assert ro.ensure_spectra_section() is None
    assert Project.open(project.folder).spectra_section is None


def test_the_spectra_section_can_move_but_not_be_deleted(project):
    spectra = project.ensure_spectra_section()
    methods = project.add_section("text", "Methods")
    with pytest.raises(ProjectError, match="cannot be deleted"):
        project.delete_section(spectra.id)
    with pytest.raises(ProjectError, match="only one"):
        project.add_section("spectra", "More spectra")
    project.move_section(spectra.id, 0)
    assert project.top_sections[0] is spectra
    project.move_section(spectra.id, 0, methods.id)          # into a custom section
    assert Project.open(project.folder).section(spectra.id).parent_id == methods.id
    with pytest.raises(ProjectError, match="holds the Spectra section"):
        project.delete_section(methods.id)
    assert project.list_trash() == []
    assert spectra.type_label == "Spectra"


# ---------------------------------------------------------------------- settings

def test_effective_settings_are_defaults_then_file_then_page():
    defaults = {**S.DEFAULTS, "per_page": 2}
    item = {"file": "a.pdf", "layout": {"orientation": "landscape", "caption": True,
                                        "x": 0.5},              # (no position for a file)
            "pages": {"2": {"per_page": 1, "x": 0.8, "y": 1.7, "corner": "middle"}}}
    one = S.effective(defaults, item, "1")
    assert (one["per_page"], one["orientation"], one["caption"]) == (2, "landscape", True)
    assert "x" not in one
    two = S.effective(defaults, item, "2")
    assert two["per_page"] == 1 and two["orientation"] == "landscape"
    assert (two["x"], two["y"]) == (0.8, 1.0)                 # kept on the spectrum
    assert two["corner"] == "top-left"                        # unknown value ignored
    assert S.effective(defaults, {"file": "b.pdf", "pages": "junk"}, "1") == defaults


def test_defaults_are_checked_when_stored(sample):
    p, parts = sample
    p.set_spectra_defaults({"per_page": 3, "corner": "middle", "struct_bg": "red",
                            "id_size": 2, "caption": True, "struct_scale": 9,
                            "struct_frame_color": "#FF0000", "nonsense": 1})
    stored = json.loads((parts["spectra"].folder / "section.json").read_text(encoding="utf-8"))
    d = stored["defaults"]
    assert set(d) == set(S.DEFAULTS)
    assert d["per_page"] == 1 and d["corner"] == "top-left" and d["struct_bg"] == "#ffffff"
    assert d["id_size"] == 5.0 and d["struct_scale"] == 4.0 and d["caption"] is True
    assert d["struct_frame_color"] == "#ff0000"


def test_overrides_and_apply_defaults_to_all(sample):
    p, parts = sample
    c2 = parts["c2"]
    files = {i["type"]: i["file"] for i in c2.values["spectra"]}
    p.set_spectrum_overrides(c2, "spectra", files["1H"], {"per_page": 2})
    p.set_spectrum_overrides(c2, "spectra", files["13C"], {"x": 0.7, "y": 0.3, "struct_scale": 0.5},
                             page="2")
    assert S.count_overridden(p) == 2
    again = Project.open(p.folder).compound(c2.id)
    item = next(i for i in again.values["spectra"] if i["type"] == "13C")
    assert item["pages"] == {"2": {"x": 0.7, "y": 0.3, "struct_scale": 0.5}}
    p.set_spectrum_overrides(c2, "spectra", files["13C"], {}, page="2")   # empty: removed
    assert "pages" not in next(i for i in c2.values["spectra"] if i["type"] == "13C")
    p.set_spectrum_overrides(c2, "spectra", files["13C"], {"x": 0.1, "y": 0.1}, page="1")
    assert S.apply_defaults_to_all(p) == 2
    assert S.count_overridden(p) == 0
    for item in Project.open(p.folder).compound(c2.id).values["spectra"]:
        assert "layout" not in item and "pages" not in item
    with pytest.raises(ProjectError):
        p.set_spectrum_overrides(c2, "spectra", "gone.pdf", {"per_page": 2})


def test_which_spectra_and_in_which_order(sample, tmp_path):
    p, parts = sample
    c2 = parts["c2"]
    hplc = fake_spectrum_pdf(tmp_path / "hplc.pdf", "HPLC trace")
    p.attach_compound_file(c2, "spectra", hplc, {"type": "HPLC/SFC"})
    p.attach_compound_file(c2, "files", fake_spectrum_pdf(tmp_path / "ir.pdf", "IR"),
                           {"caption": "IR spectrum", "placement": "appendix"})
    p.attach_compound_file(c2, "files", fake_spectrum_pdf(tmp_path / "photo.pdf", "Photo"),
                           {"caption": "Photo", "placement": "inline"})
    p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(tmp_path / "f.pdf", "19F"),
                           {"type": "19F"})
    kinds = [item.get("type") or item.get("caption") for _k, item in S.spectrum_items(p, c2)]
    assert kinds == ["1H", "13C", "19F", "HPLC/SFC", "IR spectrum"]
    pages = S.spectra_of(p, c2)
    assert [(sp.item.get("type"), sp.page, sp.key) for sp in pages[:3]] == \
        [("1H", 1, "1"), ("13C", 1, "1"), ("13C", 2, "2")]
    assert pages[2].page_no == 2


def test_nmr_captions():
    class C:
        values = {"nmr": [{"nucleus": "1H", "text": "1H NMR (500 MHz, DMSO-d6) δ 7.2"},
                          {"nucleus": "13C", "text": "δ 170.1"}]}
    assert S.caption_text(C, {"type": "1H"}) == "1H NMR (DMSO-d6, 500 MHz)"
    assert S.caption_text(C, {"type": "13C"}) == "13C NMR"
    assert S.caption_text(C, {"type": "HPLC/SFC"}) == ""
    assert S.caption_text(C, {"type": "2D NMR"}) == ""
    assert S.nmr_nucleus({"type": "31P"}) == "31P"


# ---------------------------------------------------------------------- auto-crop

def margins_pdf(path, pages=1):
    """A 10 × 8 in page whose only content is a 5 × 3 in box at (2 in, 1.5 in)."""
    import typst
    body = "\n#pagebreak()\n".join(
        "#place(dx: 2in, dy: 1.5in, rect(width: 5in, height: 3in, fill: black))"
        for _ in range(pages))
    src = path.with_suffix(".typ")
    src.write_text("#set page(width: 10in, height: 8in, margin: 0pt)\n" + body
                   + "\n#pagebreak()\n", encoding="utf-8")   # a blank last page
    typst.compile(str(src), output=str(path))
    return path


def test_content_box_of_a_spectrum_with_big_white_margins(qapp, tmp_path):
    pdf = margins_pdf(tmp_path / "wide.pdf")
    l, t, r, b = content_box(pdf, 1)
    assert l == pytest.approx(0.2, abs=0.02) and r == pytest.approx(0.7, abs=0.02)
    assert t == pytest.approx(0.1875, abs=0.02) and b == pytest.approx(0.5625, abs=0.02)
    assert l < 0.2 and r > 0.7 and t < 0.1875 and b > 0.5625     # a small margin is kept
    assert content_box(pdf, 2) is None                            # a blank page: no crop
    assert content_box(pdf, 9) is None                            # no such page
    from PySide6.QtGui import QColor, QImage
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    for x in range(100, 300):
        image.setPixelColor(x, 120, QColor("black"))
    image.save(str(tmp_path / "trace.png"))
    l, t, r, b = content_box(tmp_path / "trace.png", None)
    assert l == pytest.approx(0.25, abs=0.02) and r == pytest.approx(0.75, abs=0.02)
    assert t == pytest.approx(0.6, abs=0.03) and b == pytest.approx(0.605, abs=0.03)


def test_crop_results_are_remembered(project, tmp_path):
    pdf = project.folder / "x.pdf"
    margins_pdf(pdf)
    calls = []

    def detect(path, page):
        calls.append(page)
        return (0.1, 0.2, 0.9, 0.8)

    cache = S.CropCache(project, detect)
    assert cache.box(pdf, 1) == (0.1, 0.2, 0.9, 0.8)
    assert cache.box(pdf, 1) == (0.1, 0.2, 0.9, 0.8)
    assert calls == [1]
    cache.save()
    assert (project.folder / "build" / "crop_cache.json").exists()
    fresh = S.CropCache(project, None)                 # read back, no measuring needed
    assert fresh.box(pdf, 1) == (0.1, 0.2, 0.9, 0.8)
    margins_pdf(pdf, pages=2)                          # the file changed: measured again
    cache = S.CropCache(project, detect)
    cache.box(pdf, 1)
    assert calls == [1, 1]
    assert S.CropCache(project, lambda *_: (0.5, 0.5, 0.2, 0.9)).box(pdf, 3) is None  # nonsense


# ---------------------------------------------------------------------- Typst output

@pytest.mark.parametrize("per_page,orientation", [(1, "portrait"), (2, "portrait"),
                                                  (4, "portrait"), (1, "landscape"),
                                                  (2, "landscape"), (4, "landscape")])
def test_layouts_in_the_source(sample, per_page, orientation):
    p, _parts = sample
    p.set_spectra_defaults({"per_page": per_page, "orientation": orientation})
    src, warnings = source(p)
    assert warnings == []
    pages = -(-3 // per_page)                    # the sample has three spectrum pages
    flipped = "true" if orientation == "landscape" else "false"
    assert src.count(f"#page(flipped: {flipped})[") == pages
    assert src.count(f"#si-spectra-page({per_page}, {flipped}, (") == pages
    assert len(spectrum_calls(src)) == 3
    # The section's heading is on its first page.
    first = src.index("#page(flipped:")
    assert src.index('#heading(level: 1, outlined: true)[#"Spectra"]') > first


@pytest.mark.parametrize("per_page,orientation,pages", [(2, "landscape", 2), (4, "portrait", 1)])
def test_layouts_in_the_pdf(sample, per_page, orientation, pages):
    p, _parts = sample
    reader, _ = compiled(p)
    before = len(reader.pages)
    p.set_spectra_defaults({"per_page": per_page, "orientation": orientation})
    reader, warnings = compiled(p)
    assert warnings == []
    assert len(reader.pages) == before - 3 + pages
    spectra = reader.pages[-pages:]
    for page in spectra:
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        assert (w > h) == (orientation == "landscape")
    text = squash("".join(pg.extract_text() or "" for pg in spectra))
    assert "2—1HNMR(400MHz,CDCl3)" in text and "(continued)" in text


def test_a_spectrum_of_its_own_layout_starts_a_new_page(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.set_spectra_defaults({"per_page": 2})
    c13 = next(i["file"] for i in c2.values["spectra"] if i["type"] == "13C")
    p.set_spectrum_overrides(c2, "spectra", c13, {"orientation": "landscape"}, page="2")
    src, _ = source(p)
    pages = re.findall(r"#si-spectra-page\((\d), (true|false), ", src)
    assert pages == [("2", "false"), ("2", "true")]     # 1H + 13C p1, then 13C p2 alone


def test_hplc_traces_get_the_structure_and_id_but_no_caption(sample, tmp_path):
    p, parts = sample
    c2 = parts["c2"]
    p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(tmp_path / "hplc.pdf", "HPLC"),
                           {"type": "HPLC/SFC"})
    p.set_spectra_defaults({"caption": True})
    src, warnings = source(p)
    assert warnings == []
    calls = spectrum_calls(src)
    hplc = next(c for c in calls if 'HPLC/SFC"' in c.split("(w, h)")[0])
    nmr = next(c for c in calls if "NMR" in c.split("(w, h)")[0])
    for call in (hplc, nmr):
        assert "structure: image(" in call and 'id: [#"2"]' in call
    assert "caption: none" in hplc
    assert "caption: [" in nmr and "caption: none" not in nmr
    caption = nmr.split("caption: ")[1]       # ¹H NMR (CDCl₃, 400 MHz), formatted
    assert caption.startswith('[#super[#"1"]#"H"#" NMR ("#"CDCl"#sub[#"3"]#", 400 MHz)"]')


def test_settings_reach_the_source(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.set_spectra_defaults({"struct_frame": True, "struct_frame_width": 1, "struct_bg": "#ffeedd",
                            "id_size": 9, "id_bold": False, "spectrum_frame": True,
                            "corner": "bottom-right", "header_line": False, "auto_crop": True,
                            "struct_scale": 0.5})
    c1h = next(i["file"] for i in c2.values["spectra"] if i["type"] == "1H")
    p.set_spectrum_overrides(c2, "spectra", c1h, {"x": 0.8, "y": 0.25}, page="1")
    src, warnings = source(p, crop=lambda path, page: (0.1, 0.2, 0.9, 0.7))
    assert warnings == []
    first, *others = spectrum_calls(src)
    assert first.startswith("none, (w, h) => image(")      # no header line
    assert "crop: (0.1000, 0.2000, 0.9000, 0.7000)" in first
    assert "pos: (0.8000, 0.2500)" in first
    assert all("pos:" not in c for c in others)              # only that page was dragged
    for call in (first, *others):
        assert 'box-stroke: 1pt + rgb("#000000")' in call
        assert 'box-fill: rgb("#ffeedd")' in call
        assert "id-size: 9pt" in call and "id-bold: false" in call
        assert 'frame: 0.5pt + rgb("#000000")' in call
        assert "corner: bottom + right" in call
    # Structure size: 50 % of 1.5 × 1.1 in at most.
    width = float(re.search(r"structure: image\([^)]*width: ([\d.]+)in", first).group(1))
    assert width <= 0.75 + 1e-6
    # The boxes are remembered (build/crop_cache.json); with auto-crop off nothing is cut.
    assert "crop: (0.1000" in source(p)[0]
    p.set_spectra_defaults({**p.spectra_defaults(), "auto_crop": False})
    src, _ = source(p, crop=lambda path, page: (0.1, 0.2, 0.9, 0.7))
    assert "crop:" not in src


def test_where_the_section_sits_and_what_it_leaves_out(sample):
    p, parts = sample
    spectra = parts["spectra"]
    p.move_section(spectra.id, 0)
    src, _ = source(p)
    assert src.index("#si-spectra-page(") < src.index(f'"{FACTS["data_section"]}"')
    front.store_excluded(p.settings, [spectra.id])
    src, _ = source(p)
    assert "si-spectrum(" not in src and f'"{FACTS["data_section"]}"' in src
    front.store_excluded(p.settings, ["spectra"])          # how older projects said it
    src, _ = source(p)
    assert "si-spectrum(" not in src
    front.store_excluded(p.settings, [parts["data"].id])    # its compounds' spectra go too
    src, _ = source(p)
    assert "si-spectrum(" not in src
    # Previews ignore the choice; the live preview shows only a sample.
    src, warnings = source(p, only=("section", spectra.id), spectra_limit=2)
    assert len(spectrum_calls(src)) == 2
    assert any("first 2 of 3 spectra" in w for w in warnings)


def test_a_project_without_the_section_prints_them_at_the_end(sample):
    p, parts = sample
    p.top_sections.remove(parts["spectra"])        # as an older project opened read-only
    p.settings["spectra_per_page"] = 2
    src, warnings = source(p)
    assert warnings == []
    assert src.count("#si-spectra-page(2, false,") == 2
    assert src.index('"Reaction Optimization"') < src.index('[#"Spectra"]')
    front.store_excluded(p.settings, ["spectra"])
    assert "si-spectrum(" not in source(p)[0]


def structure_width(call: str) -> float:
    return float(re.search(r"structure: image\([^)]*width: ([\d.]+)in", call).group(1))


def test_structure_size_scales_the_structure(sample):
    """100 % = natural size (no bigger than 1.5 × 1.1 in); 200 % doubles it, 50 % halves."""
    p, _parts = sample
    widths = {}
    for scale in (1.0, 2.0, 0.5, 4.0):
        p.set_spectra_defaults({"struct_scale": scale})
        widths[scale] = structure_width(spectrum_calls(source(p)[0])[0])
    assert 0 < widths[1.0] <= 1.5
    assert widths[2.0] == pytest.approx(2 * widths[1.0], abs=0.002)
    assert widths[0.5] == pytest.approx(0.5 * widths[1.0], abs=0.002)
    assert widths[4.0] == pytest.approx(4 * widths[1.0], abs=0.004)


def test_solvents_however_they_are_written():
    from si_assistant.core.markup import parse
    plain = lambda t: " ".join(x.plain_text for x in parse(t))   # noqa: E731
    assert S.find_solvent("1H NMR (400 MHz, CDCl₃) δ 7.2") == "CDCl3"
    assert S.find_solvent(plain("1H NMR (500 MHz, (CD_{3})_{2}SO)")) == "DMSO-d6"
    assert S.find_solvent("(400 MHz, chloroform-d)") == "CDCl3"
    assert S.find_solvent("(400 MHz, DMSO‑d₆)") == "DMSO-d6"     # non-breaking hyphen
    assert S.find_solvent("(400 MHz, methanol-d4)") == "CD3OD"
    assert S.find_solvent("(400 MHz, CDCl3/CD3OD 4:1)") == "CDCl3"   # the first one named
    assert S.find_solvent("(400 MHz, benzene-d6)") == "C6D6"
    assert S.find_solvent("δ 7.26 (s, 1H)") == ""


def test_other_nmr_nuclei_and_typed_captions():
    class C:
        values = {"nmr": [{"nucleus": "1H", "text": "1H NMR (400 MHz, CDCl₃)"},
                          {"nucleus": "29Si", "text": "29Si NMR (79 MHz, C6D6) δ 12.1"}]}
    assert S.nmr_nucleus({"type": "Other NMR", "file": "3a_15N.pdf"}) == "15N"   # file name
    assert S.nmr_nucleus({"type": "Other NMR", "file": "x.pdf"}, C) == "29Si"  # its only report
    assert S.caption_text(C, {"type": "Other NMR", "file": "x.pdf"}) == "29Si NMR (C6D6, 79 MHz)"
    assert S.caption_text(C, {"type": "1H"}) == "1H NMR (CDCl3, 400 MHz)"

    class Two:
        values = {"nmr": [{"nucleus": "29Si", "text": ""}, {"nucleus": "15N", "text": ""}]}
    assert S.nmr_nucleus({"type": "Other NMR", "file": "x.pdf"}, Two) is None  # can't tell
    assert S.nmr_nucleus({"type": "2D NMR", "file": "3a_29Si.pdf"}, C) is None
    # A caption typed for the file is used as it is, for any kind of spectrum.
    assert S.caption_text(C, {"type": "1H", S.CAPTION_KEY: "1H NMR (CDCl3, 600 MHz, 50 °C)"}) \
        == "1H NMR (CDCl3, 600 MHz, 50 °C)"
    assert S.caption_text(C, {"type": "HPLC/SFC", S.CAPTION_KEY: "Chiralpak AD-H"}) == \
        "Chiralpak AD-H"
    assert S.caption_text(C, {"type": "HPLC/SFC", S.CAPTION_KEY: "  "}) == ""


def test_a_typed_caption_reaches_the_source(sample, tmp_path):
    p, parts = sample
    c2 = parts["c2"]
    item = p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(tmp_path / "hplc.pdf", "HPLC"),
                                  {"type": "HPLC/SFC"})
    assert item[S.CAPTION_KEY] == ""            # every new spectrum has the box, empty
    item[S.CAPTION_KEY] = "Chiralpak AD-H, 90:10 hexane/iPrOH"
    p.save_compound(c2)
    p.set_spectra_defaults({"caption": True})
    src, _ = source(p)
    hplc = next(c for c in spectrum_calls(src) if 'HPLC/SFC"' in c.split("(w, h)")[0])
    assert "Chiralpak AD-H" in hplc.split("caption: ")[1]


def test_the_table_of_contents_preview_lays_out_every_spectrum(sample):
    """Its page numbers for what follows the Spectra section need every spectrum page; the
    sample is only for the preview of the Spectra section itself. Nothing is measured for
    auto-crop there (cropping doesn't change the number of pages)."""
    p, parts = sample
    p.set_spectra_defaults({"auto_crop": True})
    measured = []

    def crop(path, page):
        measured.append(page)
        return (0.1, 0.1, 0.9, 0.9)

    src, warnings = source(p, crop=crop, only=("front", "toc"), spectra_limit=2)
    assert len(spectrum_calls(src)) == 3
    assert not any("first 2" in w for w in warnings)
    assert measured == []
    src, warnings = source(p, crop=crop, only=("section", parts["spectra"].id), spectra_limit=2)
    assert len(spectrum_calls(src)) == 2 and any("first 2 of 3" in w for w in warnings)


def test_a_custom_section_holding_the_spectra_previews_them_once(sample):
    p, parts = sample
    holder = p.add_section("text", "Holder")
    p.move_section(parts["data"].id, 0, holder.id)
    p.move_section(parts["spectra"].id, 1, holder.id)
    src, _ = source(p, only=("section", holder.id))
    assert len(spectrum_calls(src)) == 3
    src, _ = source(p, only=("section", parts["data"].id))     # a Data section: added after
    assert len(spectrum_calls(src)) == 3


def test_a_custom_section_starting_with_the_spectra_heads_their_first_page(sample):
    """A custom section with no text of its own whose first sub-section is the Spectra
    section: its heading goes to the top of the first spectra page, not alone at the
    bottom of the page before."""
    p, parts = sample
    outer = p.add_section("text", "Container & co")
    p.move_section(outer.id, 1)
    inner = p.add_section("text", "Inner", parent_id=outer.id)
    p.move_section(parts["spectra"].id, 0, inner.id)
    src, _ = source(p)
    first = src.index("#page(flipped:")
    for title in ('[#"Container & co"]', '[#"Inner"]', '[#"Spectra"]'):
        assert src.count(title) == 1 and src.index(title) > first
    assert src.index('[#"Container & co"]') < src.index('[#"Inner"]') < src.index('[#"Spectra"]')
    assert re.search(r'#heading\(level: 3, outlined: (true|false)\)\[#"Spectra"\]', src)
    reader, warnings = compiled(p)
    assert warnings == []
    pages = [squash(pg.extract_text() or "") for pg in reader.pages]
    body = [i for i, t in enumerate(pages) if "Container&co" in t and "TableofContents" not in t]
    assert len(body) == 1                     # not also alone on the page before
    assert "Inner" in pages[body[0]] and "Spectra" in pages[body[0]]
    assert "2—1HNMR" in pages[body[0]]
    # With text of its own the custom section keeps its heading with its text, and with no
    # spectra the headings are still printed.
    p.write_section_text(outer, "Spectra were recorded on a 400 MHz instrument.")
    src, _ = source(p)
    assert src.index('[#"Container & co"]') < src.index("#page(flipped:") < src.index('[#"Inner"]')
    p.write_section_text(outer, "")
    front.store_excluded(p.settings, [parts["data"].id])
    src, _ = source(p)
    assert "si-spectrum(" not in src and '[#"Container & co"]' in src and '[#"Inner"]' in src


def test_a_converted_spectrum_is_measured_once(sample):
    """Auto-crop measures the original file (not the copy converted for Typst, which is made
    again at every build), so a TIFF is measured once; results for files that are gone are
    dropped from build/crop_cache.json."""
    from PySide6.QtGui import QColor, QImage
    p, parts = sample
    c2 = parts["c2"]
    image = QImage(600, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    for x in range(150, 450):
        image.setPixelColor(x, 300, QColor("black"))
    tif = p.folder.parent / "trace.tif"
    assert image.save(str(tif), "TIFF")
    p.attach_compound_file(c2, "spectra", tif, {"type": "HPLC/SFC"})
    p.set_spectra_defaults({"auto_crop": True})
    measured = []

    def crop(path, page):
        measured.append(path.name)
        return content_box(path, page)

    for _ in range(3):
        src, warnings = source(p, crop=crop)
        assert warnings == []
    assert measured.count("trace.tif") == 1 and not any(n.endswith(".png") for n in measured)
    call = next(c for c in spectrum_calls(src) if "/assets/" in c.split("width: w")[0])
    assert "crop: (0.2" in call
    cache_file = p.folder / "build" / "crop_cache.json"
    keys = list(json.loads(cache_file.read_text(encoding="utf-8")))
    assert len(keys) == 4 and not any("build/assets" in k for k in keys)
    p.remove_compound_file(c2, "spectra", "trace.tif")
    source(p, crop=crop)
    cache = S.CropCache(p, crop)
    cache._load()
    cache._dirty = True
    cache.save()
    keys = list(json.loads(cache_file.read_text(encoding="utf-8")))
    assert len(keys) == 3 and not any("trace.tif" in k for k in keys)


def test_a_pdf_cropped_in_acrobat_keeps_its_shape(sample, tmp_path, qapp):
    """A PDF page with a CropBox smaller than its MediaBox: the shape used to fit it in its
    slot is the CropBox's, as Typst and QtPdf draw it."""
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject
    from PySide6.QtPdf import QPdfDocument
    from si_assistant.compile.document import Builder
    p, _parts = sample
    src = fake_spectrum_pdf(tmp_path / "full.pdf", "Cropped")       # 792 × 612 pt
    writer = PdfWriter(clone_from=str(src))
    writer.pages[0].cropbox = RectangleObject([0, 0, 396, 612])     # the left half
    cropped = tmp_path / "cropped.pdf"
    with open(cropped, "wb") as f:
        writer.write(f)
    b = Builder(p, CompileOptions.from_settings(p.settings), image_info)
    assert b.page_aspect(cropped, 1) == pytest.approx(612 / 396)
    doc = QPdfDocument()
    doc.load(str(cropped))
    size = doc.pagePointSize(0)
    doc.close()
    assert b.page_aspect(cropped, 1) == pytest.approx(size.height() / size.width(), rel=0.01)
    assert b.page_aspect(src, 1) == pytest.approx(612 / 792)


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_every_style_compiles_every_option(sample, style):
    p, parts = sample
    p.settings["journal_style"] = style
    p.set_spectra_defaults({"per_page": 2, "orientation": "landscape", "caption": True,
                            "auto_crop": True, "struct_frame": True, "spectrum_frame": True,
                            "id_size": 12, "struct_bg": "#f0f0ff"})
    c2 = parts["c2"]
    c13 = next(i["file"] for i in c2.values["spectra"] if i["type"] == "13C")
    p.set_spectrum_overrides(c2, "spectra", c13, {"per_page": 1, "orientation": "portrait"},
                             page="2")
    p.set_spectrum_overrides(c2, "spectra", c13, {"x": 0.9, "y": 0.9}, page="1")
    reader, warnings = compiled(p, crop=content_box)
    assert warnings == []
    text = squash("".join(pg.extract_text() or "" for pg in reader.pages[-2:]))
    assert "1HNMR(CDCl3,400MHz)" in text          # the caption under the structure
    assert "2—13CNMR(100MHz,CDCl3)(continued)" in text
    landscape = [float(pg.mediabox.width) > float(pg.mediabox.height) for pg in reader.pages]
    assert landscape[-2:] == [True, False]
    cache = json.loads((p.folder / "build" / "crop_cache.json").read_text(encoding="utf-8"))
    assert len(cache) == 3 and all(v is not None for v in cache.values())
