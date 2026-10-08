"""ChemDraw structure pictures as vector drawings (SPEC §13n.1) printed at one bond length
(§13n.2a): the paste (metafile → checked SVG, else the 1200-dpi picture; macOS PDF kept),
storage with the original, Trash, undo and re-rendering of SVG pictures, the other places
drawings are pasted (schemes, figures, Attachment sections), compiling at the same bond
length, the setting, the user's own value, and the spectra pages.

There is no ChemDraw here: the drawings are made-up look-alikes (tests/structure_samples.py),
metafiles drawn with real Windows calls. Parts that need Windows are skipped elsewhere."""

import os
import re
import sys

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QSize
from PySide6.QtGui import QGuiApplication

from si_assistant.compile.document import Builder, CompileOptions, build, svg_size_in
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.core import drawing_scale as ds
from si_assistant.core import spectra as S
from si_assistant.core.project import Project
from tests import structure_samples as ss

windows = pytest.mark.skipif(sys.platform != "win32",
                             reason="Enhanced Metafiles are drawn by Windows")
# Comparisons with Windows' own drawing depend on the computer's screen and fonts: left
# out on GitHub's test computers (as for spectra, tests/test_spectrum_paste.py).
windows_desktop = pytest.mark.skipif(sys.platform != "win32" or bool(os.environ.get("CI")),
                                     reason="needs Windows' drawing on a real desktop")

MOL = ss.structure_molblock()


def new_project(tmp_path, ids=("3a", "3b", "3c")):
    p = Project.create(tmp_path, "N")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, list(ids))
    return p, data


def svg_data(scale: float, noise: bool = False) -> bytes:
    return ss.structure_svg(scale, noise).encode("utf-8")


def structure_value(c) -> dict:
    return c.values.get("structure") or {}


# ---------------------------------------------------------------------- storage

def test_svg_picture_is_kept_with_its_original_and_bond_length(qapp, tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="ester")
    with p.undo_step("Change the structure of 3a"):
        p.set_structure(c, MOL, picture=svg_data(0.5), vector=b"EMF original",
                        vector_ext=".emf", picture_ext=".svg")
    picture = p.structure_image_path(c)
    assert picture.name == "structure_image.svg"
    assert picture.with_suffix(".emf").read_bytes() == b"EMF original"
    value = structure_value(c)
    assert value["image_source"] == "chemdraw"
    assert value[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.005)
    # In compound.json, so compiling needn't measure again.
    again = Project.open(p.folder)
    assert structure_value(again.compound(c.id))[ds.MEASURED_KEY] == value[ds.MEASURED_KEY]
    assert (picture, picture.with_suffix(".emf")) in p.pasted_pictures()

    # Remove: both go to one Trash item; restore: both come back, measured again.
    with p.undo_step("Remove the picture of 3a"):
        p.remove_structure_image(c)
    assert not picture.exists() and not picture.with_suffix(".emf").exists()
    assert ds.MEASURED_KEY not in structure_value(c)
    entry = p.list_trash()[0]
    assert {f.name for f in entry.folder.iterdir()} >= {"structure_image.svg",
                                                         "structure_image.emf"}
    p.restore_from_trash(entry.id)
    assert p.structure_image_path(c).read_bytes() == svg_data(0.5)
    assert p.structure_image_path(c).with_suffix(".emf").exists()
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.005)

    # Undo of a paste takes the picture, its original and the measurement away.
    d = p.add_compound(data.id, name="other")
    with p.undo_step("Change the structure of 3b"):
        p.set_structure(d, MOL, picture=svg_data(2.0), vector=b"EMF", vector_ext=".emf",
                        picture_ext=".svg")
    pasted = p.structure_image_path(d)
    assert pasted.exists() and pasted.with_suffix(".emf").exists()
    assert p.undo().label == "Change the structure of 3b"
    d = p.compound(d.id)
    assert not pasted.exists() and not pasted.with_suffix(".emf").exists()
    assert ds.MEASURED_KEY not in structure_value(d)
    p.redo()
    d = p.compound(d.id)
    assert pasted.exists() and structure_value(d)[ds.MEASURED_KEY] == \
        pytest.approx(28.8, rel=0.005)
    p.close()


def test_a_picture_without_bonds_or_a_raster_picture_has_no_measurement(qapp, tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    p.set_structure_image(c, data=b'<svg xmlns="http://www.w3.org/2000/svg" width="40pt" '
                                  b'height="20pt"><text x="2" y="12">3a</text></svg>',
                          ext=".svg")
    assert structure_value(c)[ds.MEASURED_KEY] is None
    p.set_structure_image(c, data=b"\x89PNG fake", ext=".png")
    assert ds.MEASURED_KEY not in structure_value(c)
    p.close()


def test_the_copied_structure_cross_checks_the_measurement(qapp, tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    # A picture that doesn't show the copied molecule (four bonds of a chain for a
    # 14-bond structure) is not trusted: it prints at its own size.
    zigzag = "M10 50" + "".join(f"L{10 + 12.47 * (i + 1)} {50 - 7.2 * (i % 2 == 0)}"
                                for i in range(4))
    wrong = (f'<svg xmlns="http://www.w3.org/2000/svg" width="80pt" height="60pt" '
             f'viewBox="0 0 80 60"><path d="{zigzag}" stroke="#000"/></svg>').encode()
    p.set_structure(c, MOL, picture=wrong, picture_ext=".svg")
    assert structure_value(c)[ds.MEASURED_KEY] is None
    # SMILES has no drawing to compare with: the picture alone decides.
    p.set_structure(c, "CCO", picture=wrong, picture_ext=".svg")
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(14.4, rel=0.01)
    p.close()


def test_replace_pasted_picture_follows_a_new_extension(qapp, tmp_path):
    """Re-rendering may turn a PNG into an SVG: compound.json, the figure
    list and the Attachment section follow; the old PNG goes; the original stays."""
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    p.set_structure(c, MOL, picture=b"\x89PNG old", vector=b"EMF", vector_ext=".emf")
    text = p.add_section("text", "Methods")
    owner = p.text_owner(text.id)
    fid = p.add_figure(owner, data=b"\x89PNG old", ext=".png", vector=b"EMF", vector_ext=".emf")
    attachment = p.add_section("file", "Drawing")
    p.attach_pasted_picture(attachment, b"\x89PNG old", b"EMF", ".emf")
    olds = [path for path, _source in p.pasted_pictures()]
    assert [o.suffix for o in olds] == [".png"] * 3
    for old in olds:
        new = p.replace_pasted_picture(old, svg_data(2.0), ".svg")
        assert new == old.with_suffix(".svg") and new.exists() and not old.exists()
        assert new.with_suffix(".emf").read_bytes() == b"EMF"
    again = Project.open(p.folder)
    c2 = again.compound(c.id)
    assert again.structure_image_path(c2).name == "structure_image.svg"
    assert structure_value(c2)[ds.MEASURED_KEY] == pytest.approx(28.8, rel=0.005)
    assert again.figure_path(again.text_owner(text.id), fid).suffix == ".svg"
    assert again.figure_source(again.text_owner(text.id), fid).suffix == ".emf"
    section = again.section(attachment.id)
    assert again.section_file_path(section).name == "pasted picture.svg"
    assert again.section_file_source(section).name == "pasted picture.emf"
    # The same kind again: in place.
    path = p.structure_image_path(c)
    assert p.replace_pasted_picture(path, svg_data(0.5), ".svg") == path
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.005)
    p.close()


# ---------------------------------------------------------------------- Windows: metafiles

@windows
def test_a_chemdraw_metafile_becomes_a_checked_svg(qapp):
    from si_assistant.ui import clipboard_image as ci
    for scale in (0.5, 2.0):
        picture = ci.picture_from_emf(ss.structure_emf(scale, labels=False), 1200)
        assert picture.ext == ".svg" and picture.source_ext == ".emf"
        assert picture.message == ci.SVG_MESSAGE
        text = picture.data.decode()
        assert ds.bond_length_pt(text) == pytest.approx(ss.BOND_PT * scale, rel=0.01)
        # Trimmed like pasted PNGs: about 0.5 mm of white round the drawing.
        size = svg_size_in(text)
        lines, polygons, _t, _size = ss.drawing(scale)
        xs = [v for x0, _y0, x1, _y1 in lines for v in (x0, x1)] +             [x for poly in polygons for x, _y in poly]
        ys = [v for _x0, y0, _x1, y1 in lines for v in (y0, y1)] +             [y for poly in polygons for _x, y in poly]
        ink = 0.6 * scale                                # (the pen's width)
        white = 2 * ci.SVG_MARGIN_MM / 25.4 * 72         # (both sides)
        assert size[0] * 72 == pytest.approx(max(xs) - min(xs) + ink + white, abs=1.5)
        assert size[1] * 72 == pytest.approx(max(ys) - min(ys) + ink + white, abs=1.5)


@windows
def test_a_metafile_that_fails_falls_back_to_the_1200_dpi_picture(qapp):
    from tests import emf_samples as es
    from si_assistant.ui import clipboard_image as ci
    picture = ci.picture_from_emf(es.unsupported_emf(), 1200)
    assert picture.ext == ".png" and picture.data.startswith(b"\x89PNG")
    assert picture.source_ext == ".emf"
    assert picture.message.startswith("Kept as a picture (1200 dpi): ")
    assert ds.bond_length_pt("") is None


@windows_desktop
def test_atom_labels_pass_the_check_with_the_computers_fonts():
    """In a process on Qt's Windows platform (fonts), as the program runs."""
    import json
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    proc = subprocess.run([sys.executable, "-m", "tests.native_structure_checks"], cwd=root,
                          env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-4000:]
    native = json.loads(proc.stdout.strip().splitlines()[-1])
    assert native["platform"] == "windows"
    for scale in ("0.5", "1.0", "2.0"):
        r = native[scale]
        assert r["passed"], r["reason"]
        assert r["ext"] == ".svg" and r["texts"] == 3      # the O labels stay text
        assert r["bond"] == pytest.approx(ss.BOND_PT * float(scale), rel=0.01)


def fake_chemdraw_clipboard(monkeypatch, emf: bytes, mol: str | None = MOL):
    """ChemDraw's copy: a metafile (the Windows clipboard, read through
    clipboard_metafile_bytes) and the structure as text."""
    from si_assistant.ui import clipboard_image as ci
    monkeypatch.setattr(ci, "_windows_vector_available", lambda: True)
    monkeypatch.setattr(ci, "clipboard_metafile_bytes", lambda: emf)
    data = QMimeData()
    if mol:
        data.setText(mol)
    QGuiApplication.clipboard().setMimeData(data)


@windows_desktop
def test_paste_from_chemdraw_into_every_place(qapp, tmp_path, monkeypatch):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.panels import FileSectionPanel
    from si_assistant.ui.structure_widgets import StructureEditor
    emf = ss.structure_emf(0.5, labels=False)
    fake_chemdraw_clipboard(monkeypatch, emf)
    p, data = new_project(tmp_path)

    # A compound's Structure field (Ctrl+V): the structure and its picture as SVG.
    c = p.add_compound(data.id, name="ester")
    panel = CompoundPanel(p, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if isinstance(b.editor, StructureEditor))
    editor.paste_from_clipboard()
    qapp.processEvents()
    picture = p.structure_image_path(c)
    assert picture.name == "structure_image.svg"
    assert picture.with_suffix(".emf").read_bytes() == emf
    assert p.structure_info(c).formula == "C11H14O3"
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.01)
    assert not panel.thumb.pixmap().isNull()          # the header draws the SVG
    editor.set_editing(True)
    assert "vector drawing" in editor.image_note.text()
    assert "printed at 200%" in editor.image_note.text()
    panel.flush()

    # An Attachment section.
    section = p.add_section("file", "Drawing")
    fpanel = FileSectionPanel(p, section)
    fpanel.paste_from_clipboard()
    path = p.section_file_path(p.section(section.id))
    assert path.name == "pasted picture.svg" and path.with_suffix(".emf").exists()
    assert fpanel.preview.currentWidget() is fpanel.preview.image_scroll
    assert fpanel.preview._pixmap is not None and not fpanel.preview._pixmap.isNull()

    # A figure in a custom section (the shared paste function).
    from si_assistant.ui.clipboard_image import clipboard_picture
    text = p.add_section("text", "Methods")
    owner = p.text_owner(text.id)
    picture = clipboard_picture(1200)
    fid = p.add_figure(owner, data=picture.data, ext=picture.ext, vector=picture.source,
                       vector_ext=picture.source_ext)
    assert p.figure_path(owner, fid).suffix == ".svg"
    assert p.figure_source(owner, fid).suffix == ".emf"
    p.close()


@windows_desktop
def test_rerender_turns_metafile_originals_into_svg(qapp, tmp_path):
    from si_assistant.ui import clipboard_image as ci
    emf = ss.structure_emf(2.0, labels=False)
    png = ci.picture_from_emf(emf, 600, svg=False)
    assert png.ext == ".png"
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    p.set_structure(c, MOL, picture=png.data, vector=emf, vector_ext=".emf")
    plain = p.add_compound(data.id)
    p.set_structure_image(plain, data=png.data)        # (a screenshot: no original)
    vector_only = p.add_compound(data.id)
    p.set_structure_image(vector_only, data=svg_data(1.0), ext=".svg")
    assert ci.rerender_pasted_pictures(p, 1200) == (1, 1, 0)
    assert p.structure_image_path(c).name == "structure_image.svg"
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(28.8, rel=0.01)
    assert not (c.folder / "structure_image.png").exists()
    # Again: converted again in place.
    assert ci.rerender_pasted_pictures(p, 1200) == (1, 1, 0)
    assert p.structure_image_path(c).name == "structure_image.svg"
    p.close()


# ---------------------------------------------------------------------- macOS: PDF

def chemdraw_pdf(tmp_path, scale: float = 1.0) -> bytes:
    """A PDF like ChemDraw's copy on macOS: the drawing's lines, made with Typst."""
    import typst
    lines, _polygons, texts, (w, h) = ss.drawing(scale)
    body = "\n".join(f"#place(line(start: ({x0:.3f}pt, {y0:.3f}pt), end: ({x1:.3f}pt, "
                     f"{y1:.3f}pt), stroke: {0.6 * scale:.2f}pt))" for x0, y0, x1, y1 in lines)
    body += "\n" + "\n".join(f"#place(dx: {x - 3:.2f}pt, dy: {y - 5:.2f}pt)[#text(size: "
                             f"{10 * scale:.1f}pt)[{t}]]" for x, y, t in texts)
    src = tmp_path / f"chemdraw{scale}.typ"
    src.write_text(f"#set page(width: {w:.2f}pt, height: {h:.2f}pt, margin: 0pt)\n" + body,
                   encoding="utf-8")
    out = src.with_suffix(".pdf")
    typst.compile(str(src), output=str(out))
    return out.read_bytes()


def test_a_pdf_from_chemdraw_on_macos_is_kept_as_pdf(qapp, tmp_path):
    from si_assistant.ui import clipboard_image as ci
    from si_assistant.ui.structure_widgets import picture_pixmap
    pdf = chemdraw_pdf(tmp_path, 0.5)
    data = QMimeData()
    data.setData("application/pdf", QByteArray(pdf))
    QGuiApplication.clipboard().setMimeData(data)
    kept = ci.clipboard_picture(1200, vector=ci.VECTOR_EVERYWHERE)
    assert kept.ext == ".pdf" and kept.data == pdf and kept.source is None
    rendered = ci.clipboard_picture(1200)                 # (figures: rendered as before)
    assert rendered.ext == ".png" and rendered.source_ext == ".pdf"

    p, d = new_project(tmp_path / "p")
    c = p.add_compound(d.id)
    p.set_structure_image(c, data=kept.data, ext=kept.ext)
    picture = p.structure_image_path(c)
    assert picture.name == "structure_image.pdf"
    assert structure_value(c)[ds.MEASURED_KEY] == pytest.approx(7.2, rel=0.01)
    assert not picture_pixmap(picture, QSize(100, 80)).isNull()     # on screen (QtPdf)
    info = image_info(picture)
    assert info[0] / info[2] == pytest.approx(ss.drawing(0.5)[3][0] / 72, rel=0.01)
    # Printed at the project's bond length: twice its own size.
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    width = float(re.search(r'structure_image\.pdf", width: ([\d.]+)in', source).group(1))
    assert width == pytest.approx(ss.drawing(0.5)[3][0] / 72 * 2, rel=0.01)
    # Nothing to re-render: it is the vector drawing itself.
    assert ci.rerender_pasted_pictures(p, 1200) == (0, 0, 0)
    p.close()


# ---------------------------------------------------------------------- compiling

def printed_widths(source: str) -> list[float]:
    return [float(w) for w in re.findall(r'structure_image\.svg", width: ([\d.]+)in', source)]


def two_scales(tmp_path):
    p, data = new_project(tmp_path)
    for name, scale in (("small", 0.5), ("large", 2.0)):
        c = p.add_compound(data.id, name=name)
        p.set_structure(c, MOL, picture=svg_data(scale), vector=b"EMF", vector_ext=".emf",
                        picture_ext=".svg")
    return p


def test_compile_prints_pictures_at_the_same_bond_length(qapp, tmp_path):
    p = two_scales(tmp_path)
    natural = svg_size_in(ss.structure_svg(1.0))[0]   # its size at 14.4 pt bonds
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    assert printed_widths(source) == [pytest.approx(natural, abs=0.002)] * 2
    # Off: each at its own size (half and twice), the large one shrunk to 3.25 in at most.
    p.settings[ds.SETTING_KEY] = False
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    small, large = printed_widths(source)
    assert small == pytest.approx(natural / 2, abs=0.002)
    assert large == pytest.approx(min(natural * 2, 3.25), abs=0.002)
    # The program's drawings for every compound: no pictures at all.
    p.settings[ds.SETTING_KEY] = True
    source = build(p, CompileOptions(structures="acs"), image_info,
                   convert_to_png).typ_path.read_text("utf-8")
    assert printed_widths(source) == []
    p.close()


def test_the_compiled_pdf_has_one_bond_length(qapp, tmp_path):
    """In the PDF itself: every structure's bonds are 14.4 pt (ACS 1996)."""
    import numpy as np
    from pypdf import PdfReader
    from si_assistant.compile.run import compile_pdf
    p = two_scales(tmp_path)
    p.settings["export_excluded"] = ["cover", "toc"]

    def lengths(pdf_path):
        data = pdf_path.read_bytes()
        out = []
        for i in range(len(PdfReader(str(pdf_path)).pages)):
            seg = ds.pdf_segments(data, i)
            out += np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]).tolist()
        return np.array(out)

    def near(values, target):
        return int((np.abs(values / target - 1) < 0.02).sum())

    pdf = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                      convert_to_png).pdf
    found = lengths(pdf)
    assert near(found, 14.4) >= 2 * 6               # full bonds of both molecules
    assert near(found, 7.2) == near(found, 28.8) == 0
    p.settings[ds.SETTING_KEY] = False
    pdf = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                      convert_to_png, name="SI off").pdf
    found = lengths(pdf)
    assert near(found, 7.2) >= 6 and near(found, 28.8 * 3.25 / (2 * svg_size_in(
        ss.structure_svg(1.0))[0])) >= 6           # (the large one shrunk to 3.25 in)
    p.close()


def test_users_own_value_wins(qapp, tmp_path):
    p = two_scales(tmp_path)
    small = p.section(p.sections[0].id).compounds[0]
    natural = svg_size_in(ss.structure_svg(1.0))[0]
    p.set_structure_picture_bond(small, 0)            # its own size
    assert p.structure_picture_bond(small) == (pytest.approx(7.2, rel=0.005), 0.0)
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    assert printed_widths(source)[0] == pytest.approx(natural / 2, abs=0.002)
    p.set_structure_picture_bond(small, 3.6)          # typed: its bonds are 3.6 pt
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    assert printed_widths(source)[0] == pytest.approx(natural * 2, abs=0.002)
    p.set_structure_picture_bond(small, None)         # back to the measurement
    source = build(p, CompileOptions(), image_info, convert_to_png).typ_path.read_text("utf-8")
    assert printed_widths(source)[0] == pytest.approx(natural, abs=0.002)
    p.close()


def test_spectra_structure_box_follows_the_same_scale(qapp, tmp_path):
    """The default size on a spectrum is the normalised one; a size set by hand multiplies
    it, so what the spectra page shows is what prints (SPEC §13n.2a)."""
    from si_assistant.ui.spectra_page import structure_source
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="small")
    p.set_structure(c, MOL, picture=svg_data(0.25), picture_ext=".svg")
    natural = svg_size_in(ss.structure_svg(0.25))
    source = structure_source(p, c)
    assert source[0] == "picture"
    assert source[2] == (pytest.approx(natural[0] * 4, rel=0.002),
                         pytest.approx(natural[1] * 4, rel=0.002))
    builder = Builder(p, CompileOptions(), image_info)
    for scale in (1.0, 0.6, 1.4):                    # default, and resized by hand
        eff = dict(S.DEFAULTS, struct_scale=scale)
        args = builder.structure_box_args(c, {}, eff)
        width = float(re.search(r"width: ([\d.]+)in", args[0]).group(1))
        assert width == pytest.approx(S.structure_size_in(source[2], scale)[0], abs=0.002)
    # A raster picture (nothing measured) is exactly as before.
    plain = p.add_compound(data.id, name="png")
    from tests.test_large_pictures import save_png
    png = save_png(tmp_path / "pic.png", (300, 150))
    p.set_structure_image(plain, png)
    w_in = image_info(p.structure_image_path(plain))
    assert structure_source(p, plain)[2] == (pytest.approx(w_in[0] / w_in[2]),
                                             pytest.approx(w_in[1] / w_in[2]))
    p.close()


# ---------------------------------------------------------------------- settings and UI

def test_project_settings_and_compile_wording(qapp, tmp_path):
    from si_assistant.ui.compile_ui import CompileDialog
    from si_assistant.ui.dialogs import COMPILE_ACS, COMPILE_PICTURES, StructureDrawingTab
    settings = {}
    tab = StructureDrawingTab(settings)
    assert tab.same_bond.isChecked()
    assert tab.compile_choice.itemText(0) == COMPILE_PICTURES == \
        "ChemDraw pictures where available; program drawings otherwise"
    assert tab.compile_choice.itemText(1) == COMPILE_ACS == "Program drawings for every compound"
    assert tab.compile_choice.currentData() == "pictures"          # the default
    tab.same_bond.setChecked(False)
    tab.apply(settings)
    assert settings[ds.SETTING_KEY] is False and not ds.same_scale(settings)
    tab = StructureDrawingTab(settings)
    assert not tab.same_bond.isChecked()
    tab.same_bond.setChecked(True)
    tab.apply(settings)
    assert ds.SETTING_KEY not in settings                         # default: not saved
    p, _data = new_project(tmp_path)
    dialog = CompileDialog(p)
    assert dialog.pictures.text() == COMPILE_PICTURES and dialog.pictures.isChecked()
    assert dialog.acs.text() == COMPILE_ACS
    p.close()


def test_printed_size_choice_is_one_undo_step(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.structure_widgets import (PictureScaleDialog, StructureEditor,
                                                   picture_scale_text)
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    p.set_structure(c, MOL, picture=svg_data(0.5), picture_ext=".svg")
    assert "printed at 200%" in picture_scale_text(p, c)
    dialog = PictureScaleDialog(None, p, c)
    assert dialog.auto.isChecked() and dialog.choice() is None
    assert "7.2 pt" in dialog.auto.text()
    dialog.natural.setChecked(True)
    assert dialog.choice() == 0.0
    panel = CompoundPanel(p, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if isinstance(b.editor, StructureEditor))
    editor.set_picture_bond(0.0)
    assert p.structure_picture_bond(c)[1] == 0.0
    assert "own size" in picture_scale_text(p, c)
    assert p.undo().label.endswith("Change the printed size of the picture of 3a")
    c = p.compound(c.id)
    assert p.structure_picture_bond(c)[1] is None
    p.settings[ds.SETTING_KEY] = False
    assert "off in Project settings" in picture_scale_text(p, c)
    panel.flush()
    p.close()
