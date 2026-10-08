"""Problems found by the final review of SPEC §13k (and fixed): each test is one of them."""

import re
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import run_typst
from si_assistant.core import chem
from tests.sample_project import build_sample, fake_spectrum_pdf


@pytest.fixture
def sample(tmp_path):
    return build_sample(tmp_path)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    wins = []

    def make(p):
        p.close()
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        assert win.open_project(p.folder)
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def _pump(app):
    for _ in range(3):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ---------------------------------------------------------------------- spelling underlines

def test_closed_text_boxes_are_not_underlined_again(qapp):
    """A text box's underliner must be forgotten when the box is deleted: a dictionary
    change used to call every deleted one (an error each) and their number kept growing."""
    from si_assistant.ui import text_checks
    from si_assistant.ui.rich_text import RichTextEditor
    hub = text_checks.checks()
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a[1])
    try:
        editors = [RichTextEditor() for _ in range(5)]
        for e in editors:
            e.set_markup("Some text with a tyypo.")
        assert len(hub.highlighters()) == 5
        for e in editors:
            e.deleteLater()
        _pump(qapp)
        hub.ignore("tyypo")   # (emits changed)
        _pump(qapp)
        assert errors == []
        assert hub.highlighters() == []
        assert hub.receivers("2changed()") == 1   # only the hub's own
        # A text box that still exists is underlined again.
        live = RichTextEditor()
        live.set_markup("Another wrod.")
        calls = []
        live.edit.checks.rehighlight = lambda: calls.append(1)
        hub.changed.emit()
        assert calls == [1]
    finally:
        sys.excepthook = old_hook


# ---------------------------------------------------------------------- toolbar Preview

def test_the_preview_window_lets_go_of_its_pdf(qapp, tmp_path):
    """The toolbar Preview frees preview.pdf before the next preview writes it (Windows
    keeps a file open by QPdfDocument.close(), so the next one was 'preview (2).pdf')."""
    from si_assistant.ui.compile_ui import PreviewWindow
    pdf = fake_spectrum_pdf(tmp_path / "preview.pdf", "1H NMR")
    win = PreviewWindow()
    win.show_pdf(pdf, "2")
    _pump(qapp)
    win.release()
    _pump(qapp)
    pdf.unlink()            # (fails on Windows while the file is still open)
    fake_spectrum_pdf(pdf, "1H NMR again")
    win.show_pdf(pdf, "2")
    win.done(0)             # Escape: lets go of the file too
    pdf.unlink()
    win.deleteLater()


def test_two_toolbar_previews_give_no_second_file(window, sample):
    p, s = sample
    win = window(p)
    shown = []
    from si_assistant.ui import main_window as mw
    original = mw.show_warnings
    mw.show_warnings = lambda parent, result, title: shown.append(list(result.warnings))
    try:
        win.select("compound", s["c2"].id)
        win.preview_current()
        win.preview_current()
    finally:
        mw.show_warnings = original
    assert shown == [[], []]
    assert sorted(x.name for x in (win.project.folder / "build").glob("preview*.pdf")) == \
        ["preview.pdf"]
    win._preview.close()


# ---------------------------------------------------------------------- older pictures

def _old_section(p):
    old = p.add_section("text", "Old Notes")
    (old.folder / "images").mkdir()
    (old.folder / "images" / "pic.png").write_bytes(
        chem.structure_png(chem.parse_structure("c1ccccc1"), 300, 200))
    (old.folder / "content.txt").write_text("Some text.\n!image{images/pic.png} A ring\n",
                                            encoding="utf-8")
    return old


def test_older_pictures_are_upgraded_when_the_project_opens(window, sample):
    """!image lines become figures when the project is opened, before its undo history
    starts. Done when the page was shown, Undo re-showed the page, which made a new step
    each time, so Undo could never get past it."""
    p, s = sample
    old = _old_section(p)
    win = window(p)
    assert "!figure{f-" in (old.folder / "content.txt").read_text(encoding="utf-8")
    journal = win.project.journal
    assert journal.undo_stack == []           # opening made no step
    win.select("section", s["notes"].id)
    with win._step("Rename notes"):
        win.project.rename_section(s["notes"].id, "Renamed notes")
    win.select("section", old.id)
    assert not any("Old Notes" in st.label for st in journal.undo_stack)   # showing: no step
    while journal.undo_stack:   # each Undo goes one step back, the page being shown again
        n = len(journal.undo_stack)
        win.undo()
        assert len(journal.undo_stack) == n - 1
    assert win.project.section(s["notes"].id).name == "Reaction Optimization"
    assert len(journal.redo_stack) >= 1


def test_a_section_restored_from_the_trash_gets_its_pictures_upgraded(sample):
    p, s = sample
    old = _old_section(p)
    p.delete_section(old.id)
    entry = p.list_trash()[0]
    # (a section deleted by an older version: its text still has !image lines)
    item = entry.folder / entry.info["item"]
    assert "!image{" in (item / "content.txt").read_text(encoding="utf-8")
    p.restore_from_trash(entry.id)
    restored = next(x for x in p.sections if x.name == "Old Notes")
    text = (restored.folder / "content.txt").read_text(encoding="utf-8")
    assert "!figure{f-" in text and "!image{" not in text


# ---------------------------------------------------------------------- TOC preview numbers

def _toc_numbers(pdf, page_index):
    from pypdf import PdfReader
    text = PdfReader(str(pdf)).pages[page_index].extract_text()
    return re.findall(r"S\d+", text)


def test_the_toc_preview_has_the_page_numbers_of_the_si(sample, tmp_path):
    p, _s = sample
    full = build(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png,
                 name="full")
    run_typst(full.typ_path, tmp_path / "full.pdf", p.folder)
    options = CompileOptions.from_settings(p.settings)
    options.only = ("front", "toc")
    preview = build(p, options, image_info, convert_to_png, name="tocp")
    assert "#counter(page).update(2)" in preview.typ_path.read_text(encoding="utf-8")
    run_typst(preview.typ_path, tmp_path / "toc.pdf", p.folder)
    in_si = _toc_numbers(tmp_path / "full.pdf", 1)        # the TOC is page S2 of the SI
    assert in_si and in_si[-1] == "S2"
    assert _toc_numbers(tmp_path / "toc.pdf", 0) == in_si
    # Without a cover, the TOC is the first page.
    from si_assistant.core import front
    front.store_excluded(p.settings, {"cover"})
    options = CompileOptions.from_settings(p.settings)
    options.only = ("front", "toc")
    preview = build(p, options, image_info, convert_to_png, name="tocp2")
    assert "#counter(page).update" not in preview.typ_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- hand-edited settings

@pytest.mark.parametrize("key, value", [("cover", "x"), ("general_information", 5),
                                        ("authors", "x"), ("authors", [1, {"name": 3}]),
                                        ("affiliations", {"a": 1}), ("title", 7)])
def test_hand_edited_front_matter_settings_never_crash(qapp, sample, key, value):
    from si_assistant.ui.front_matter import CoverPagePanel, GeneralInfoPanel, TocPanel
    p, _s = sample
    p.settings[key] = value
    result = build(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png,
                   name="bad")
    assert result.typ_path.exists()
    for cls in (CoverPagePanel, GeneralInfoPanel, TocPanel):
        cls(p).deleteLater()


def test_the_toc_font_size_starts_at_the_text_size(qapp, project):
    from si_assistant.ui.front_matter import TocPanel
    for style, size in (("ACS", 11.0), ("Wiley", 11.0), ("RSC", 10.5)):
        project.settings["journal_style"] = style
        panel = TocPanel(project)
        assert panel.same_size.isChecked() and panel.size.value() == size
        assert panel.values()["font_size"] == 0.0
        panel.deleteLater()


# ---------------------------------------------------------------------- page widths

def test_the_spectra_field_fits_beside_the_live_preview(qapp, sample):
    """Each attachment takes two lines (name and choices, then its text boxes), so the
    Spectra field no longer needs ~700 px (the compound page scrolled sideways at
    1200x800 with the live preview open)."""
    from si_assistant.ui.compound_panel import CompoundPanel
    p, s = sample
    c = s["c2"]
    long = fake_spectrum_pdf(p.folder / "3a_1H_NMR_CDCl3_400MHz_purified_second_batch.pdf",
                             "1H")
    p.attach_compound_file(c, "spectra", long, {"type": "1H NMR"})
    panel = CompoundPanel(p, c, "Tester")
    box = next(b for b in panel.boxes if b.defn.key == "spectra")
    editor = box.editor
    grid = editor.rows_layout
    for name, widgets in editor.row_widgets:
        for key, w in widgets.items():
            part = next(x for x in box.defn.item_parts if x.key == key)
            # Text boxes are on the line under the file name, not in the grid's columns.
            assert (grid.indexOf(w) == -1) == (part.kind == "text"), key
    link = grid.itemAtPosition(0, 0).widget()
    assert link.minimumWidth() <= 80          # a long file name doesn't widen the page
    # (Sizes in pixels are checked in the screenshots: tests have no real fonts.)
    # Rebuilding the rows (after adding or removing a file) leaves nothing behind.
    editor._build_rows()
    _pump(qapp)
    assert len(editor.row_widgets) == len(c.values["spectra"])
    texts = [w for w in editor.findChildren(QLineEdit) if not w.isHidden()]
    assert len(texts) == 2 * len(c.values["spectra"])   # header + caption, per file
    panel.deleteLater()


def test_cover_and_scheme_pages_fit_the_middle_column(window, sample):
    """At 1200x800 with the live preview the tree shrank on these pages: the cover's long
    notes now wrap and the scheme editor's buttons wrap onto a second line when narrow."""
    from si_assistant.ui.flow_layout import FlowLayout
    p, s = sample
    win = window(p)
    win.select("front", "cover")
    for label in win.panel.findChildren(QLabel):
        if len(label.text()) > 60:
            assert label.wordWrap(), label.text()
    win.select("scheme", s["scheme"].id)
    editor = win.panel.editor               # (its buttons: in the page's top row, §13q.6b)
    flow = editor.bar
    assert isinstance(flow, FlowLayout) and flow.indexOf(editor.layout_btn) >= 0
    one_line = flow.sizeHint().height()
    assert flow.heightForWidth(150) > one_line   # narrow: two lines or more


def test_figure_and_table_buttons_come_before_symbols_and_mentions(qapp):
    """On a narrow page the toolbar's » menu takes the last buttons: Figure… and Table…
    come straight after the text formatting, so they stay on show."""
    from si_assistant.ui.rich_text import RichTextEditor

    class Store:
        editable = True
        folder = None
        live_text = None

        def changed(self):
            pass
    e = RichTextEditor()
    e.enable_objects(Store())
    texts = [a.text() for a in e.toolbar.actions() if a.text()]
    assert texts.index("Table…") == texts.index("Figure…") + 1
    assert texts.index("H₂") < texts.index("Figure…") < texts.index("@ Mention")
    assert e.heading_box.minimumWidth() >= e.heading_box.sizeHint().width()

# ---------------------------------------------------------------------- small wording fixes

def test_the_no_id_warning_says_where_to_fix_it(project):
    section = project.add_section("data", "Products")
    c = project.add_compound(section.id, name="x")
    warnings = project.id_report().compound_warnings[c.id]
    assert "ID table" in warnings[0] and "Manual ID" in warnings[0]


def test_an_empty_project_dashboard_has_no_count_note(qapp, project):
    from si_assistant.ui.project_dashboard import ProjectDashboard
    dash = ProjectDashboard(project)
    dash.refresh()
    assert dash.missing_note.isHidden()
    assert "No compounds yet" in dash.missing.empty_text
    dash.deleteLater()
