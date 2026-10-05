"""The live preview (SPEC §13k.8): one page compiled on its own, in the pane on the right
of the main window, about a second after an edit is saved."""

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import CompileError, run_typst
from tests.sample_project import build_sample


def preview_source(p, only, **kw) -> tuple[str, object]:
    options = CompileOptions.from_settings(p.settings)
    options.only = only
    for k, v in kw.items():
        setattr(options, k, v)
    result = build(p, options, image_info, convert_to_png, name="t")
    return result.typ_path.read_text(encoding="utf-8"), result


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    if p.lock is not None:
        p.close()


# ---------------------------------------------------------------------- one page alone

def test_each_kind_of_page_compiles_on_its_own(sample):
    p, parts = sample
    c2, data, notes, scheme = parts["c2"], parts["data"], parts["notes"], parts["scheme"]
    sub = p.add_section("text", "Solvent screen", parent_id=notes.id)
    p.write_section_text(sub, "Toluene was best.")
    files = p.add_section("file", "Crystal structure")
    files.data["heading"] = "X-ray data"
    p.save_section(files)

    src, _ = preview_source(p, ("front", "cover"))
    assert "#si-cover(" in src and "#outline(" not in src
    src, _ = preview_source(p, ("front", "toc"))
    assert "#outline(" in src and "#si-cover(" not in src
    p.write_front_text("general", "All reactions were run under argon.")
    src, _ = preview_source(p, ("front", "general"))
    assert "argon" in src and "#si-cover(" not in src
    # A custom section with its sub-section, printed as if at the top level.
    src, _ = preview_source(p, ("section", notes.id))
    assert "#heading(level: 1" in src and "Reaction Optimization" in src
    assert "#heading(level: 2" in src and "Solvent screen" in src and "#si-table(" in src
    src, _ = preview_source(p, ("section", files.id))
    assert "X-ray data" in src and "Reaction Optimization" not in src
    src, _ = preview_source(p, ("compound", c2.id))
    assert "#si-compound-title(" in src and "#si-scheme(" not in src
    src, _ = preview_source(p, ("scheme", scheme.id))
    assert "#si-scheme(" in src and "#si-compound-title(" in src
    src, _ = preview_source(p, ("section", data.id))
    assert src.count("#si-compound-title(") == 2
    with pytest.raises(ValueError):
        preview_source(p, ("nonsense", "x"))


def test_live_preview_leaves_out_the_spectra(sample):
    p, parts = sample
    c2 = parts["c2"]
    src, _ = preview_source(p, ("compound", c2.id))
    assert "si-spectrum(" in src                       # the toolbar Preview shows them
    src, _ = preview_source(p, ("compound", c2.id), preview_spectra=False)
    assert "si-spectrum(" not in src and "#si-compound-title(" in src
    src, _ = preview_source(p, ("section", parts["data"].id), preview_spectra=False)
    assert "si-spectrum(" not in src


def test_a_page_with_nothing_to_print_is_empty(sample):
    p, _parts = sample
    files = p.add_section("file", "Nothing yet")
    _, result = preview_source(p, ("section", files.id))
    assert result.empty
    _, result = preview_source(p, ("front", "general"))
    assert result.empty                                 # no General Information text
    p.write_front_text("general", "Argon.")
    _, result = preview_source(p, ("front", "general"))
    assert not result.empty


def test_building_outside_the_project_folder(sample, tmp_path):
    """The live preview builds in a temporary folder: paths are written from a Typst root
    that holds both that folder and the project, and nothing is added to build/."""
    p, parts = sample
    outside = tmp_path / "elsewhere"
    options = CompileOptions.from_settings(p.settings)
    options.only = ("section", parts["notes"].id)
    options.preview_spectra = False
    before = set((p.folder / "build").glob("*")) if (p.folder / "build").exists() else set()
    result = build(p, options, image_info, convert_to_png, name="live", folder=outside,
                   root=tmp_path)
    assert result.typ_path.parent == outside and (outside / "style.typ").exists()
    src = result.typ_path.read_text(encoding="utf-8")
    project_rel = "/" + p.folder.relative_to(tmp_path).as_posix() + "/"
    assert project_rel in src                           # pictures: from the common root
    after = set((p.folder / "build").glob("*")) if (p.folder / "build").exists() else set()
    assert after == before
    run_typst(result.typ_path, outside / "live_a.pdf", tmp_path)
    assert (outside / "live_a.pdf").stat().st_size > 1000


def test_citations_are_found_from_any_build_folder(sample, tmp_path):
    p, parts = sample
    ref = p.add_reference({"authors": ["Doe, J."], "title": "A paper", "journal": "J. Test",
                           "year": "2020"})
    p.write_section_text(parts["notes"], "As reported.@cite{" + ref["id"] + "}")
    outside = tmp_path / "elsewhere"
    options = CompileOptions.from_settings(p.settings)
    options.only = ("section", parts["notes"].id)
    result = build(p, options, image_info, convert_to_png, name="live", folder=outside,
                   root=tmp_path)
    src = result.typ_path.read_text(encoding="utf-8")
    assert '#si-references("/elsewhere/references.yml")' in src
    run_typst(result.typ_path, outside / "x.pdf", tmp_path)


def test_typst_errors_become_compile_errors(tmp_path):
    bad = tmp_path / "bad.typ"
    bad.write_text("#unknown-function()", encoding="utf-8")
    with pytest.raises(CompileError):
        run_typst(bad, tmp_path / "bad.pdf", tmp_path)


def test_the_toc_preview_shows_only_the_toc_pages(sample, tmp_path):
    """The TOC is laid out with the whole SI after it (so its page numbers are right), but
    its preview keeps only the pages up to the end of the TOC."""
    from pypdf import PdfReader
    from si_assistant.compile.document import TOC_END_LABEL
    from si_assistant.compile.run import keep_pages_until, last_page_of
    p, _parts = sample
    src, result = preview_source(p, ("front", "toc"), preview_spectra=False)
    assert result.last_page_label == TOC_END_LABEL and f"<{TOC_END_LABEL}>" in src
    assert "Reaction Optimization" in src                     # the body is still laid out
    pdf = tmp_path / "toc.pdf"
    run_typst(result.typ_path, pdf, p.folder)
    assert len(PdfReader(str(pdf)).pages) > 1
    assert last_page_of(result.typ_path, p.folder, TOC_END_LABEL) == 1
    keep_pages_until(result.typ_path, pdf, p.folder, TOC_END_LABEL)
    reader = PdfReader(str(pdf))
    assert len(reader.pages) == 1
    assert "Table of Contents" in reader.pages[0].extract_text()
    # Other pages show every page; a missing label leaves the PDF as it is.
    _, other = preview_source(p, ("section", _parts["notes"].id))
    assert other.last_page_label == ""
    assert last_page_of(result.typ_path, p.folder, "no-such-label") is None
    before = pdf.read_bytes()
    keep_pages_until(result.typ_path, pdf, p.folder, "no-such-label")
    assert pdf.read_bytes() == before


def test_the_pane_shows_only_the_toc_pages(sample, pane):
    p, _parts = sample
    pane.set_project(p)
    pane.set_target(("front", "toc"))
    pane._start()
    assert pane.stack.currentWidget() is pane.view and pane.doc.pageCount() == 1


def test_structure_drawings_are_kept_between_builds(sample, monkeypatch):
    """The TOC preview builds the whole SI on the main thread: structures already drawn
    (same molecule, same drawing settings) are not drawn again."""
    from si_assistant.compile import document
    from si_assistant.core import chem
    p, parts = sample
    c2 = parts["c2"]
    drawn = []
    real = chem.structure_svg
    monkeypatch.setattr(chem, "structure_svg",
                        lambda mol, *a, **k: drawn.append(1) or real(mol, *a, **k))
    document._DRAWN.clear()
    mol = p.read_structure(c2)
    settings = chem.DrawingSettings()
    first = document.drawn_structure(mol, settings)
    assert document.drawn_structure(mol, settings) == first and len(drawn) == 1
    assert first[1] is not None and first[1][0] > 0           # its size in inches
    document.drawn_structure(mol, chem.DrawingSettings(colour_atoms=True))
    assert len(drawn) == 2                                    # other settings: drawn again
    p.set_structure(c2, "CCO")                                # a new structure: drawn again
    document.drawn_structure(p.read_structure(c2), settings)
    assert len(drawn) == 3
    # Whole builds: the second one draws nothing, and prints the same.
    document._DRAWN.clear()
    drawn.clear()
    src1, _ = preview_source(p, ("front", "toc"))
    n = len(drawn)
    src2, _ = preview_source(p, ("front", "toc"))
    assert n > 0 and len(drawn) == n and src1 == src2


def test_isotope_patterns_and_hrms_species_are_reused(sample):
    from si_assistant.core import chem, checks
    p, parts = sample
    mol = p.read_structure(parts["c2"])
    sp1 = checks.hrms_species(mol, {})
    sp2 = checks.hrms_species(mol, {})
    assert sp1.info is sp2.info                               # worked out once
    peaks1 = chem.isotope_pattern(sp1.info, "[M+H]+", 1.0, 12)
    peaks2 = chem.isotope_pattern(sp1.info, "[M+H]+", 1.0, 12)
    assert peaks1 == peaks2 and peaks1[0] is not peaks2[0]    # equal copies
    peaks1[0].mz = 0.0                                        # changing a copy changes nothing
    assert chem.isotope_pattern(sp1.info, "[M+H]+", 1.0, 12)[0].mz == peaks2[0].mz
    assert chem.isotope_pattern(sp1.info, "[M+Na]+", 1.0, 12)[0].mz != peaks2[0].mz
    with pytest.raises(chem.ChemError):                       # errors are not remembered
        chem.isotope_pattern(sp1.info, "[M-C99]+")
    with pytest.raises(chem.ChemError):
        chem.isotope_pattern(sp1.info, "[M-C99]+")


def test_where_the_live_preview_is_built(project, tmp_path, monkeypatch):
    from si_assistant.ui import live_preview
    temp = tmp_path / "tmp"
    temp.mkdir()
    folder, root = live_preview.live_folders(project, temp)
    assert folder == temp and project.folder.resolve().is_relative_to(root)
    # On another drive there is no common folder.
    def no_common(_paths):
        raise ValueError("different drives")
    monkeypatch.setattr(live_preview.os.path, "commonpath", no_common)
    folder, root = live_preview.live_folders(project, temp)
    assert folder == project.folder / "build" / "live" and root == project.folder
    project.read_only = True
    with pytest.raises(live_preview.PreviewUnavailable):
        live_preview.live_folders(project, temp)
    project.read_only = False


# ---------------------------------------------------------------------- the pane

def pump(qapp, until=lambda: False, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


@pytest.fixture
def pane(qapp):
    from si_assistant.ui.live_preview import LivePreviewPane
    pane = LivePreviewPane()
    pane.resize(500, 700)
    yield pane
    pane.shutdown()
    pane.close()
    pane.deleteLater()


def test_pane_compiles_the_page_and_alternates_files(sample, pane, qapp):
    p, parts = sample
    pane.set_project(p)
    pane.set_target(("compound", parts["c2"].id))
    assert not pane.isHidden() and pane._timer.isActive()
    pane._start()
    assert pane.doc.pageCount() >= 1 and pane.stack.currentWidget() is pane.view
    assert pane.status.text().startswith("Up to date")
    first = pane._shown_file
    assert first.name == "live_a.pdf"
    assert not (p.folder / "build" / "live.typ").exists()   # built outside the project
    pane._start()
    assert pane._shown_file.name == "live_b.pdf" and pane._shown_file.parent == first.parent
    pane._start()
    assert pane._shown_file.name == "live_a.pdf"
    temp = pane._temp
    pane.set_project(None)
    assert pane.isHidden() and not temp.exists()             # tidied up


def test_pane_waits_for_a_second_after_an_edit(sample, pane):
    p, parts = sample
    pane.set_project(p)
    pane.set_target(("compound", parts["c2"].id))
    pane._start()
    pane.schedule()
    assert pane._timer.isActive() and pane._timer.interval() == 1000
    pane.schedule()                                           # bursts are combined
    assert pane.compiles == 1


def test_problems_are_shown_in_the_pane(sample, pane, monkeypatch):
    from si_assistant.ui import live_preview
    p, parts = sample
    pane.set_project(p)
    pane.set_target(("compound", parts["c2"].id))

    def broken(*_a):
        raise CompileError("x") from RuntimeError("error: something odd")
    monkeypatch.setattr(live_preview, "run_typst", broken)
    pane._start()
    assert pane.stack.currentWidget() is pane.message
    assert "something odd" in pane.message.text() and "Not updated" in pane.status.text()
    # With a preview already on show, it stays and the problem is shown above it.
    monkeypatch.setattr(live_preview, "run_typst", run_typst)
    pane._start()
    assert pane.stack.currentWidget() is pane.view
    monkeypatch.setattr(live_preview, "run_typst", broken)
    pane._start()
    assert pane.stack.currentWidget() is pane.view and not pane.error_label.isHidden()
    assert "something odd" in pane.error_label.text()
    # A page that was deleted meanwhile: a plain message, never an exception, a pop-up or
    # a raw internal ID.
    pane.set_target(("compound", "c-gone"))
    pane._start()
    assert pane.message.text() == "This page no longer exists."
    assert pane.stack.currentWidget() is pane.message and pane.error_label.isHidden()
    # Anything else that goes wrong while preparing the page is reported in the pane.
    monkeypatch.setattr(live_preview, "build", lambda *a, **k: 1 / 0)
    pane.set_target(("compound", parts["c2"].id))
    pane._start()
    assert "could not be prepared" in pane.status.toolTip()


def test_nothing_to_show_yet(sample, pane):
    p, _parts = sample
    files = p.add_section("file", "Nothing yet")
    pane.set_project(p)
    pane.set_target(("section", files.id))
    pane._start()
    assert "Nothing to show yet" in pane.message.text() and pane.compiles == 0


def test_collapsed_pane_compiles_when_shown_again(sample, pane):
    p, parts = sample
    pane.set_project(p)
    pane.set_collapsed(True)
    pane.set_target(("compound", parts["c2"].id))
    pane.schedule()
    assert not pane._timer.isActive() and pane._dirty
    assert pane.strip.isVisibleTo(pane) and not pane.body.isVisibleTo(pane)
    pane.set_collapsed(False)
    assert pane._timer.isActive()


def test_compiling_in_a_worker_thread(sample, pane, qapp, monkeypatch):
    """Typst runs in a thread; an edit saved meanwhile is compiled once it has finished,
    never twice at once."""
    from si_assistant.ui import live_preview
    monkeypatch.setattr(live_preview.LivePreviewPane, "use_thread", True)
    p, parts = sample
    release = threading.Event()
    running = []

    def slow(typ, output, root):
        running.append(threading.current_thread() is not threading.main_thread())
        release.wait(10)
        run_typst(typ, output, root)
    monkeypatch.setattr(live_preview, "run_typst", slow)
    pane.set_project(p)
    pane.set_target(("compound", parts["c2"].id))
    pane._start()
    assert pane.busy() and pane.status.text() == "Updating…"
    pane._start()                                             # an edit arrives meanwhile
    assert pane._pending and pane.compiles == 1
    release.set()
    assert pump(qapp, lambda: pane.compiles == 2 and not pane.busy())
    assert running == [True, True]
    assert pane.doc.pageCount() >= 1


# ---------------------------------------------------------------------- in the main window

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    wins = []

    def make(folder: Path, user="Tester"):
        win = mw.MainWindow()
        win.prefs.user_name = user
        assert win.open_project(folder)
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def test_selecting_pages_sets_what_the_pane_shows(sample, window):
    p, parts = sample
    p.close()
    win = window(p.folder)
    pane = win.preview_pane
    win.select("compound", parts["c2"].id)
    assert pane.target == ("compound", parts["c2"].id) and not pane.isHidden()
    assert pane._timer.isActive()                             # a preview is on its way
    win.select("section", parts["data"].id)                   # a Data section's overview
    assert pane.target is None and pane.isHidden()
    win.select("front_root", "front")
    assert pane.isHidden()
    win.select("front", "cover")
    assert pane.target == ("front", "cover") and not pane.isHidden()
    win.select("section", parts["notes"].id)
    assert pane.target == ("section", parts["notes"].id)
    win.select("scheme", parts["scheme"].id)
    assert pane.target == ("scheme", parts["scheme"].id)
    win.select("compound", parts["c4"].id)                    # a compound of that scheme
    assert pane.target == ("compound", parts["c4"].id)
    win.close_project()
    assert pane.isHidden() and pane.project is None


def test_saving_an_edit_schedules_an_update(sample, window):
    p, parts = sample
    p.close()
    win = window(p.folder)
    win.select("compound", parts["c2"].id)
    pane = win.preview_pane
    pane._start()
    assert pane.compiles == 1 and not pane._timer.isActive()
    win.panel.saved.emit("Saved")
    assert pane._timer.isActive() and pane._timer.interval() == 1000
    pane._start()
    assert pane.compiles == 2 and pane.doc.pageCount() >= 1


def test_hiding_the_pane_is_remembered(sample, window):
    p, parts = sample
    p.close()
    win = window(p.folder)
    assert win.act_live.isChecked() and not win.preview_pane.collapsed
    win.act_live.trigger()
    assert win.preview_pane.collapsed and not win.prefs.live_preview
    win.close_project()
    win2 = window(p.folder)
    assert win2.preview_pane.collapsed and not win2.act_live.isChecked()
    win2.preview_pane.strip.click()                           # the strip shows it again
    assert not win2.preview_pane.collapsed and win2.prefs.live_preview
    assert win2.act_live.isChecked()


def test_read_only_projects_preview_without_writing(sample, window, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, parts = sample                                         # still locked by "Tester"
    monkeypatch.setattr(mw, "ask_about_lock", lambda *a: "read_only")
    win = window(p.folder, user="Someone else")
    assert win.project.read_only
    win.select("compound", parts["c2"].id)
    pane = win.preview_pane
    pane._start()
    assert pane.doc.pageCount() >= 1
    assert not (p.folder / "build" / "live").exists() and not (p.folder / "build" / "live.typ").exists()


def test_deleting_the_previewed_scheme_compound(sample, window, monkeypatch):
    """Deleting the compound on show (its tab on a scheme page) moves the preview to what
    the tree now shows, never on to the deleted compound."""
    from PySide6.QtWidgets import QMessageBox
    from si_assistant.ui import scheme_panel
    p, parts = sample
    p.close()
    win = window(p.folder)
    c4, scheme = parts["c4"], parts["scheme"]
    win.select("scheme", scheme.id)
    name = win.panel.name_edit                                # (the start of the name shows)
    assert name.cursorPosition() == 0 and name.toolTip() == scheme.name
    win.select("compound", c4.id)
    pane = win.preview_pane
    assert pane.target == ("compound", c4.id)
    monkeypatch.setattr(scheme_panel.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.panel._close_tab(win.panel.tabs.currentIndex())
    assert pane.target == win._preview_target() == ("scheme", scheme.id)
    pane._start()
    assert pane.stack.currentWidget() is pane.view and pane.status.text().startswith("Up to")


def _show(win, qapp):
    win.resize(1500, 1000)
    win.show()
    pump(qapp, seconds=0.2)


def test_a_hidden_preview_leaves_no_gap(sample, window, qapp):
    """Hidden, the pane is a thin strip and the page in the middle takes the rest of the
    width: after starting with it hidden, and after hiding or showing it on a page that has
    no preview (where the whole pane is out of sight)."""
    from si_assistant.ui.live_preview import MIN_WIDTH
    p, parts = sample
    p.close()
    win = window(p.folder)
    win.act_live.trigger()                                    # hidden (remembered)
    win.close_project()
    win2 = window(p.folder)                                   # starts with it hidden
    _show(win2, qapp)
    pane, splitter = win2.preview_pane, win2.splitter
    strip = pane.strip.sizeHint().width()
    c2, data = parts["c2"].id, parts["data"].id

    def check(collapsed: bool):
        pump(qapp, seconds=0.1)
        sizes = splitter.sizes()
        assert pane.collapsed == collapsed and sizes[2] == pane.width()
        if collapsed:
            assert sizes[2] == strip and sizes[1] > 1100
        else:
            assert sizes[2] >= MIN_WIDTH and sizes[1] >= 400

    win2.select("compound", c2)
    check(True)
    pane.strip.click()                                        # shown again: its width back
    check(False)
    assert splitter.sizes()[2] == win2.prefs.live_preview_width
    # Hidden while on a Data section's overview (no preview there), then a compound.
    win2.select("section", data)
    win2.act_live.trigger()
    win2.select("compound", c2)
    check(True)
    # Shown again while on the overview, then a compound.
    win2.select("section", data)
    win2.act_live.trigger()
    win2.select("compound", c2)
    check(False)
    win2.act_live.trigger()                                   # and hidden on the page itself
    check(True)
