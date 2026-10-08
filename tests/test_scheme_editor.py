"""The scheme editor (SPEC §13n.4): its data in scheme.json, boxes and steps, compounds
marked on the scheme, ID labels, inline structures in the conditions, layout settings,
the Trash and undo, and the Typst it compiles to. Made-up structures only."""

import json
import re

import pytest

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.core import drawing_scale as ds
from si_assistant.core import scheme_editor as se
from si_assistant.core.labels import compute
from si_assistant.core.project import Project
from si_assistant.ui.clipboard_image import PastedPicture
from tests import structure_samples as ss


def new_project(tmp_path, ids=("2a", "2b", "2c", "2d", "2e")):
    p = Project.create(tmp_path, "Editor")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, list(ids))
    return p, data


def chemdraw_picture(scale: float = 1.0) -> PastedPicture:
    """What pasting a ChemDraw structure gives (its SVG, with the original metafile)."""
    return PastedPicture(ss.structure_svg(scale).encode("utf-8"), b"EMF original", ".emf",
                         ".svg")


def ids(p, scheme):
    report = p.id_report()
    return [report.display_id(c.id) for c in scheme.children]


def source(p, scheme, **options):
    opts = CompileOptions.from_settings(p.settings)
    opts.only = ("scheme", scheme.id)
    opts.preview_spectra = False
    for k, v in options.items():
        setattr(opts, k, v)
    result = build(p, opts, image_info, convert_to_png, name="t")
    return result.typ_path.read_text(encoding="utf-8"), result


def scheme_call(src: str) -> str:
    start = src.index("#si-reaction-scheme(")
    return src[start:src.index("\n\n", start) if "\n\n" in src[start:] else None]


def arrows(src: str) -> list[str]:
    return re.findall(r'\(kind: "arrow".*?\)(?=,\n|\n)', scheme_call(src))


# ---------------------------------------------------------------------- the data

def test_new_schemes_use_the_editor_and_old_picture_schemes_open_in_it(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    saved = json.loads((k.folder / "scheme.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "editor"
    assert len(saved["editor"]["starting"]) == 1 and len(saved["editor"]["steps"]) == 1
    assert len(saved["editor"]["steps"][0]["products"]) == 1
    # A scheme page saved as one pasted picture (before the editor; SPEC §13p.5 removed
    # that way): it opens in the editor, its compounds the products of one step, and
    # showing or compiling it writes nothing; its picture file is left alone.
    old = p.add_scheme(data.id, name="Old")
    a = p.add_compound(data.id, name="A", scheme_id=old.id)
    b = p.add_compound(data.id, name="B", scheme_id=old.id)
    p.set_structure(a, "CCO")
    p.set_structure(b, "CC=O")
    (old.folder / "scheme.png").write_bytes(b"PNG old picture")
    (old.folder / "scheme.json").write_text(json.dumps(
        {"id": old.id, "name": "Old", "children": [a.id, b.id], "image": "scheme.png",
         "labels": {a.id: {"x": 0.2, "y": 0.9, "manual": True}}, "saved": True}),
        encoding="utf-8")
    p.reload()
    old = p.scheme(old.id)
    before = (old.folder / "scheme.json").read_bytes()
    assert old.data["mode"] == "editor" and len(se.starting(old)) == 1
    assert [se.box_compound(p, old, x).name for x in se.steps(old)[0]["products"]] == ["A", "B"]
    src, result = source(p, old)
    assert "#si-reaction-scheme(" in src and "#si-scheme(" not in src
    assert (old.folder / "scheme.json").read_bytes() == before
    assert (old.folder / "scheme.png").read_bytes() == b"PNG old picture"
    # Saved with the page's next change.
    se.set_conditions(p, old, se.steps(old)[0]["id"], "top", "NaBH4")
    saved = json.loads((old.folder / "scheme.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "editor" and len(saved["editor"]["steps"][0]["products"]) == 2


def test_boxes_steps_and_round_trip(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    sm = se.starting(k)[0]
    se.set_box_structure(p, k, sm["id"], "CC(=O)c1ccccc1")
    second = se.add_box(p, k, se.START)
    se.set_box_structure(p, k, second["id"], "[BH4-].[Na+]")
    step = se.steps(k)[0]
    se.set_conditions(p, k, step["id"], "top", "NaBH_{4} (1.5 equiv)\n**MeOH**, 0 °C")
    se.set_conditions_align(p, k, step["id"], "bottom", "left")
    se.add_box(p, k, step["id"])
    step2 = se.add_step(p, k)
    assert se.move_box(p, k, second["id"], -1) and not se.move_box(p, k, second["id"], -1)
    reopened = Project.open(p.folder).scheme(k.id)
    assert [b["id"] for b in se.starting(reopened)] == [second["id"], sm["id"]]
    assert [s["id"] for s in se.steps(reopened)] == [step["id"], step2["id"]]
    assert len(se.steps(reopened)[0]["products"]) == 2
    assert se.steps(reopened)[0]["top"] == "NaBH_{4} (1.5 equiv)\n**MeOH**, 0 °C"
    assert se.conditions_align(p.settings, reopened, se.steps(reopened)[0], "bottom") == "left"
    assert se.conditions_align(p.settings, reopened, se.steps(reopened)[0], "top") == "center"
    # The structure is a readable MOL file in the scheme's folder.
    view = se.box_view(p, reopened, se.find_box(reopened, sm["id"])[0])
    assert view.mol is not None and view.picture is None
    path = se.path_of(reopened, se.find_box(reopened, sm["id"])[0]["structure"])
    assert path.parent.name == "editor" and path.suffix == ".mol"
    # Removing a step with plain boxes; a box with a structure goes to the Trash.
    se.remove_step(p, k, step2["id"])
    assert len(se.steps(k)) == 1
    entry = se.remove_box(p, k, sm["id"])
    assert entry is not None and entry.kind == "scheme_box"
    assert [b["id"] for b in se.starting(k)] == [second["id"]]
    message = p.restore_from_trash(entry.id)
    assert "Restored" in message and len(se.starting(k)) == 2
    restored = se.starting(k)[-1]
    assert se.box_view(p, k, restored).mol is not None


def test_chemdraw_picture_is_kept_with_its_original_and_measured(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    box = se.starting(k)[0]
    se.set_box_structure(p, k, box["id"], ss.structure_molblock(), chemdraw_picture(0.5))
    box = se.find_box(k, box["id"])[0]
    picture = se.path_of(k, box["picture"])
    assert picture.suffix == ".svg" and picture.with_suffix(".emf").read_bytes() == b"EMF original"
    assert box["picture_source"] == "chemdraw"
    assert box[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.02)
    # Printed at the project's bond length (14.4 pt): twice its own size.
    assert se.box_print_factor(p.settings, box, picture) == pytest.approx(2.0, rel=0.02)
    se.set_box_bond(p, k, box["id"], 0.0)          # the user's own choice: its own size
    assert se.box_print_factor(p.settings, box, picture) == 1.0
    # Re-render pasted pictures knows about it (and the original).
    assert (picture, picture.with_suffix(".emf")) in p.pasted_pictures()


def test_compound_toggle_creates_links_and_unlinks(tmp_path):
    p, data = new_project(tmp_path)
    before = p.add_compound(data.id, name="before")
    k = p.add_scheme(data.id, name="Route")
    after = p.add_compound(data.id, name="after")
    step = se.steps(k)[0]
    first = step["products"][0]
    se.set_box_structure(p, k, first["id"], "CC(O)c1ccccc1", chemdraw_picture())
    second = se.add_box(p, k, step["id"])
    step2 = se.add_step(p, k)
    third = step2["products"][0]
    # Marking the last one first: the IDs still follow the boxes, left to right.
    c3 = se.link_new_compound(p, k, third["id"], name="third")
    c1 = se.link_new_compound(p, k, first["id"], name="first")
    c2 = se.link_new_compound(p, k, second["id"], name="second")
    assert [c.name for c in k.children] == ["first", "second", "third"]
    report = p.id_report()
    assert [report.display_id(c.id) for c in data.compounds] == ["2a", "2b", "2c", "2d", "2e"]
    assert [c.name for c in data.compounds] == ["before", "first", "second", "third", "after"]
    # One source of truth: the box's structure and picture became the compound's.
    first = se.find_box(k, first["id"])[0]
    assert "structure" not in first and "picture" not in first
    assert p.structure_info(c1).formula == "C8H10O"
    assert p.structure_image_path(c1).suffix == ".svg"
    assert not list((k.folder / "editor").glob(f"{first['id']}*"))
    view = se.box_view(p, k, first)
    assert view.compound is c1 and view.mol is not None and view.picture is not None
    # Pasting into the box changes the compound.
    se.set_box_structure(p, k, first["id"], "CCO")
    assert p.structure_info(c1).formula == "C2H6O"
    # Moving a box reorders the compounds (and their IDs).
    se.move_box(p, k, second["id"], -1)
    assert [c.name for c in k.children] == ["second", "first", "third"]
    # Unmark, keeping the page: the box keeps a copy; the page stays, unlinked, last.
    se.unlink_box(p, k, first["id"], delete_compound=False)
    assert [c.name for c in k.children] == ["second", "third", "first"]
    box = se.find_box(k, first["id"])[0]
    assert "compound" not in box and se.box_view(p, k, box).mol is not None
    assert se.unlinked_children(k) == [c1]
    # … and link it back to the box.
    se.link_compound(p, k, first["id"], c1.id)
    assert [c.name for c in k.children] == ["second", "first", "third"]
    # Unmark and delete the page: it goes to the Trash (restorable).
    se.unlink_box(p, k, third["id"], delete_compound=True)
    assert [c.name for c in k.children] == ["second", "first"]
    assert any(e.kind == "compound" and e.name == "third" for e in p.list_trash())
    # Removing a box whose compound is kept: the compound stays on the page.
    se.remove_box(p, k, second["id"], delete_compound=False)
    assert [c.name for c in k.children] == ["first", "second"]
    assert se.unlinked_children(k) == [c2]


def test_deleting_or_moving_a_compound_leaves_its_structure_in_the_box(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    box = se.steps(k)[0]["products"][0]
    se.set_box_structure(p, k, box["id"], "c1ccccc1O")
    c = se.link_new_compound(p, k, box["id"])
    p.delete_compound(c.id)
    box = se.find_box(k, box["id"])[0]
    assert "compound" not in box
    assert se.box_view(p, k, box).mol is not None    # (a copy: the scheme still shows it)
    c = se.link_new_compound(p, k, box["id"])
    p.move_compound(c.id, data.id, 0)                # out of the scheme
    assert "compound" not in se.find_box(k, box["id"])[0]
    assert k.children == []


def test_convert_compound_to_scheme_uses_the_editor(tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="B")
    p.set_structure(c, "CCOC(C)=O")
    k = p.convert_to_scheme(c.id)
    assert k.data["mode"] == "editor"
    product = se.steps(k)[0]["products"][0]
    assert se.box_compound(p, k, product) is c
    assert se.box_view(p, k, product).mol is not None


def test_starting_material_labels_and_yields(tmp_path):
    p, data = new_project(tmp_path)
    elsewhere = p.add_compound(data.id, name="elsewhere")     # 2a
    k = p.add_scheme(data.id)
    sm = se.starting(k)[0]
    se.set_box_structure(p, k, sm["id"], "CCO")
    se.set_box_label(p, k, sm["id"], "S1")
    product = se.steps(k)[0]["products"][0]
    se.set_box_structure(p, k, product["id"], "CC=O")
    c = se.link_new_compound(p, k, product["id"])           # 2b
    c.values["yield"] = {"percent": "88", "mass": "12", "mass_unit": "mg"}
    p.save_compound(c)
    assert se.box_yield(p, k, product) == "88%"              # from the Yield field
    se.set_box_yield(p, k, product["id"], "88% (2 steps)")    # typed over
    assert se.box_yield(p, k, se.find_box(k, product["id"])[0]) == "88% (2 steps)"
    src, _ = source(p, k)
    call = scheme_call(src)
    assert 'label: [#"S1"]' in call
    assert 'label: [#strong[#"2b"]]' in call and 'yield: [#"88% (2 steps)"]' in call
    # A mention of a compound elsewhere: printed as its current ID, no data page.
    se.set_box_label(p, k, sm["id"], "@{" + elsewhere.id + "}")
    src, _ = source(p, k)
    assert 'label: [#strong[#"2a"]]' in scheme_call(src)
    assert src.count("#si-compound-title(") == 1               # only 2b's block
    se.set_box_yield(p, k, product["id"], None)                # automatic again
    se.set_label_offset(p, k, product["id"], 12.5, -3)
    src, _ = source(p, k)
    assert "dx: 12.50pt, dy: -3.00pt" in scheme_call(src) and 'yield: [#"88%"]' in src
    # Yields under the step's conditions, or not printed.
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "yields": "conditions"})
    src, _ = source(p, k)
    assert 'yield: none' in scheme_call(src) and '[#"88%"]' in arrows(src)[0]
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "yields": "none"})
    src, _ = source(p, k)
    assert "88%" not in scheme_call(src)


def test_inline_structure_in_the_conditions(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    step = se.steps(k)[0]
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, step["products"][0]["id"], "CC=O")
    with pytest.raises(se.SchemeEditorError):
        se.add_inline_structure(p, k, "", None)
    sid = se.add_inline_structure(p, k, ss.structure_molblock(), chemdraw_picture(0.5))
    sid2 = se.add_inline_structure(p, k, "ClCCl", None)        # SMILES only: drawn
    se.set_conditions(p, k, step["id"], "top", f"@struct{{{sid}}} (1.2 equiv) @struct{{{sid2}}}")
    entry = se.inline_entry(k, sid)
    assert entry["picture"].endswith(".svg") and entry[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.02)
    src, result = source(p, k)
    top = arrows(src)[0]
    assert top.count("#box(baseline: 50% - 0.3em, image(") == 2
    # 60 % (the default) of its size at the project's bond length: 2 × 60 % = 1.2 × natural.
    from si_assistant.compile.document import svg_size_in
    natural = svg_size_in(ss.structure_svg(0.5))[0]
    width = float(re.search(r'image\("[^"]*\.svg", width: ([\d.]+)in\)\)', top).group(1))
    assert width == pytest.approx(natural * 2 * 0.6, rel=0.03)
    assert not [w for w in result.warnings if "structure" in w]
    # A structure missing from the scheme is left out with a warning.
    se.set_conditions(p, k, step["id"], "top", "@struct{s-gone}")
    src, result = source(p, k)
    assert any("missing" in w for w in result.warnings)


def test_layout_settings_project_defaults_and_scheme_overrides(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    assert se.scheme_layout(p.settings, k) == se.DEFAULT_LAYOUT
    se.set_project_layout(p.settings, {**se.DEFAULT_LAYOUT, "cond_size_pt": 8,
                                       "fit": "shrink"})
    assert p.settings["schemes"] == {"cond_size_pt": 8.0, "fit": "shrink"}
    se.set_scheme_layout(p, k, {**se.project_layout(p.settings), "scale_pct": 80,
                                "fit": "shrink"})
    assert k.data["editor"]["layout"] == {"scale_pct": 80.0}   # only what differs
    lay = se.scheme_layout(p.settings, k)
    assert lay["cond_size_pt"] == 8.0 and lay["scale_pct"] == 80.0 and lay["fit"] == "shrink"
    se.set_project_layout(p.settings, se.DEFAULT_LAYOUT)
    assert "schemes" not in p.settings
    # Nonsense values are ignored or kept within limits.
    assert se.clean_value("top_align", "centre") == "center"
    assert se.clean_value("fit", "sideways") is None
    assert se.clean_value("scale_pct", 5000) == se.LIMITS["scale_pct"][1]


def three_step_scheme(p, data, top_texts=("a", "b", "c")):
    k = p.add_scheme(data.id, name="Three steps")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    for i, smiles in enumerate(("CC(O)c1ccccc1", "CC(OC(C)=O)c1ccccc1", "CC(N)c1ccccc1")):
        step = se.steps(k)[0] if i == 0 else se.add_step(p, k)
        se.set_box_structure(p, k, step["products"][0]["id"], smiles)
        se.set_conditions(p, k, step["id"], "top", top_texts[i])
    return k


def test_typst_for_one_and_three_steps(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="One step")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    step = se.steps(k)[0]
    se.set_box_structure(p, k, step["products"][0]["id"], "CC=O")
    se.set_conditions(p, k, step["id"], "top", "PCC, CH2Cl2")
    se.set_conditions(p, k, step["id"], "bottom", "rt, 2 h")
    src, result = source(p, k)
    call = scheme_call(src)
    assert call.count('(kind: "mol"') == 2 and call.count('(kind: "arrow"') == 1
    assert '#"CH"#sub[#"2"]#"Cl"#sub[#"2"]' in call            # formulas subscripted
    assert "label: none, caption: none" in call       # (no label or caption by default)
    assert k.id not in compute(p).by_id                # (not numbered, not listed)
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    call = scheme_call(source(p, k)[0])
    assert 'label: [#"Scheme S1"], caption: [#"One step"]' in call
    assert 'fit: "wrap"' in call and "factor: 100%" in call and "min-arrow: 12mm" in call
    assert not result.warnings
    # Project settings ▸ Schemes: label and caption on for every scheme.
    se.set_project_layout(p.settings, {**se.DEFAULT_LAYOUT, "show_label": True})
    p.save_settings()
    k3 = three_step_scheme(p, data)
    src, result = source(p, k3)
    call = scheme_call(src)
    assert call.count('(kind: "arrow"') == 3 and call.count('(kind: "mol"') == 4
    assert 'label: [#"Scheme S2"]' in call
    assert not result.warnings


def test_empty_boxes_are_left_out_with_a_warning(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CCO")
    se.add_box(p, k, se.START)
    src, result = source(p, k)
    assert scheme_call(src).count('(kind: "mol"') == 1
    assert any("starting material has no structure" in w for w in result.warnings)


def test_compiled_pdf_arrows_grow_with_the_conditions_and_rows_wrap(qapp, tmp_path):
    """Compiled with Typst: the arrow under longer conditions is longer; a scheme too
    wide for the page goes onto a second row, or shrinks to one line."""
    import typst
    from pypdf import PdfReader
    from si_assistant.core.drawing_scale import pdf_segments
    p, data = new_project(tmp_path)
    k = three_step_scheme(p, data, ("H2", "Ac2O, DMAP, CH2Cl2, rt, a much longer line of "
                                    "conditions", "NH3"))

    def compile_(**layout):
        se.set_scheme_layout(p, k, {**se.project_layout(p.settings), **layout})
        _src, result = source(p, k)
        pdf = result.typ_path.with_suffix(".pdf")
        typst.compile(str(result.typ_path), output=str(pdf), root=str(p.folder))
        segments = pdf_segments(pdf.read_bytes())
        horizontal = [s for s in segments if abs(s[1] - s[3]) < 0.01 and abs(s[0] - s[2]) > 20]
        return horizontal, PdfReader(str(pdf))

    lines, _ = compile_(fit="wrap")
    lengths = sorted(abs(s[2] - s[0]) for s in lines)
    rows = sorted({round(s[1]) for s in lines})
    assert len(lengths) == 3
    assert lengths[-1] > 3 * lengths[0]           # the long conditions' arrow
    assert len(rows) >= 2                         # too wide: a second row
    lines, _ = compile_(fit="shrink")
    assert len({round(s[1]) for s in lines}) == 1 # one line
    shrunk = sorted(abs(s[2] - s[0]) for s in lines)
    assert shrunk[-1] < lengths[-1]
    lines, _ = compile_(fit="wrap", scale_pct=50)
    half = sorted(abs(s[2] - s[0]) for s in lines)
    assert half[-1] == pytest.approx(lengths[-1] / 2, rel=0.05)
    lines, _ = compile_(fit="wrap", arrow_min_mm=60)
    assert min(abs(s[2] - s[0]) for s in lines) > 60 / 25.4 * 72 * 0.9


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_compiles_in_every_style_without_warnings(qapp, tmp_path, style):
    from si_assistant.compile.run import compile_pdf
    p, data = new_project(tmp_path)
    p.settings["journal_style"] = style
    p.save_settings()
    k = three_step_scheme(p, data, ("NaBH_{4}", "Ac_{2}O", "NH_{3}"))
    sid = se.add_inline_structure(p, k, "ClCCl", None)
    step = se.steps(k)[0]
    se.set_conditions(p, k, step["id"], "top", f"NaBH_{{4}} @struct{{{sid}}}")
    se.set_conditions(p, k, step["id"], "bottom", "MeOH, 0 °C")
    c = se.link_new_compound(p, k, step["products"][0]["id"], name="alcohol")
    c.values["yield"] = {"percent": "91"}
    p.save_compound(c)
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    opts = CompileOptions.from_settings(p.settings)
    result = compile_pdf(p, opts, image_info, convert_to_png)
    assert result.pdf.exists()
    assert not [w for w in result.warnings if "cheme" in w]
    from pypdf import PdfReader
    text = "".join(page.extract_text() for page in PdfReader(str(result.pdf)).pages)
    assert "Scheme S1" in text and "91%" in text


def test_undo_of_editor_actions(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    box = se.steps(k)[0]["products"][0]
    with p.undo_step("Paste"):
        se.set_box_structure(p, k, box["id"], "CCO", chemdraw_picture())
    with p.undo_step("Mark"):
        se.link_new_compound(p, k, box["id"])
    assert len(p.scheme(k.id).children) == 1
    p.undo()
    k = p.scheme(k.id)
    assert k.children == []
    box = se.find_box(k, box["id"])[0]
    assert se.box_view(p, k, box).picture is not None and "compound" not in box
    p.undo()
    k = p.scheme(k.id)
    assert se.box_view(p, k, se.find_box(k, box["id"])[0]).empty
    p.redo()
    p.redo()
    k = p.scheme(k.id)
    assert len(k.children) == 1 and se.find_box(k, box["id"])[0]["compound"] == k.children[0].id


def test_conditions_mentions_and_citations_are_checked(tmp_path):
    from si_assistant.core import checks, links
    p, data = new_project(tmp_path)
    other = p.add_compound(data.id, name="other")
    k = p.add_scheme(data.id, name="Route")
    step = se.steps(k)[0]
    se.set_conditions(p, k, step["id"], "top", "@{" + other.id + "} (1 equiv) @cite{r-none}")
    places = [t for t in links.all_texts(p) if t.kind == "scheme"]
    assert places and "step 1" in places[0].where
    warnings = checks.project_warnings(p)
    assert any(w.check == "citation" and w.place == ("scheme", k.id) for w in warnings)
    assert any("Route" in place for place in links.mention_uses(p, other.id))


def test_rerender_turns_a_box_picture_into_svg(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    box = se.starting(k)[0]
    png = PastedPicture(b"\x89PNG not really", b"EMF original", ".emf", ".png")
    se.set_box_structure(p, k, box["id"], ss.structure_molblock(), png)
    sid = se.add_inline_structure(p, k, "", png)
    box = se.find_box(k, box["id"])[0]
    old = se.path_of(k, box["picture"])
    assert ds.MEASURED_KEY not in box                      # (a raster picture: no bonds)
    new = p.replace_pasted_picture(old, ss.structure_svg(0.5).encode("utf-8"), ".svg")
    assert new.suffix == ".svg" and not old.exists()
    box = se.find_box(Project.open(p.folder).scheme(k.id), box["id"])[0]
    assert box["picture"].endswith(".svg")
    assert box[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.02)
    inline = se.path_of(k, se.inline_entry(k, sid)["picture"])
    p.replace_pasted_picture(inline, ss.structure_svg(0.5).encode("utf-8"), ".svg")
    assert se.inline_entry(k, sid)["picture"].endswith(".svg")
