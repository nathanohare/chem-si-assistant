"""A compound's spectra page (SPEC §13k.12): the page under the Spectra section where the
structure + ID box is dragged and resized and single spectra get settings of their own;
the tree's pages per compound, "View spectra page" and the ("spectra", id) preview."""

import pytest
from PySide6.QtCore import QPointF, QSettings

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import image_info
from si_assistant.core import spectra as S
from si_assistant.core.project import Project
from tests.sample_project import FACTS, build_sample, fake_spectrum_pdf


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def _spectra_item(c, file_name):
    return next(i for i in c.values["spectra"] if i["file"] == file_name)


# ---------------------------------------------------------------------- core

def test_printed_geometry():
    w, h = S.slot_size_in("ACS", 1, "portrait")
    assert (round(w, 3), round(h, 3)) == (6.5, 9.0)
    w2, h2 = S.slot_size_in("ACS", 2, "portrait")          # stacked, with the gap
    assert w2 == pytest.approx(6.5) and h2 == pytest.approx((9.0 - 0.8 / 2.54) / 2)
    w4, h4 = S.slot_size_in("Wiley", 4, "landscape")       # A4 turned, 2 × 2
    assert w4 > h4
    # A landscape spectrum (8.5 × 11 in) fills the slot's width; cropping enlarges it.
    full = S.shown_size_in((6.5, 9.0), 8.5 / 11)
    assert full == pytest.approx((6.5, 6.5 * 8.5 / 11))
    cropped = S.shown_size_in((6.5, 9.0), 8.5 / 11, (0.1, 0.1, 0.9, 0.9), header=True)
    assert cropped[0] == pytest.approx(6.5)
    assert S.shown_size_in((6.5, 2.0), 1.0, header=True) == pytest.approx(
        (2.0 - S.HEADER_IN, 2.0 - S.HEADER_IN))           # too tall: the height decides
    assert S.structure_size_in((3.0, 1.0), 1.0) == pytest.approx((1.5, 0.5))
    assert S.structure_size_in((3.0, 1.0), 2.0) == pytest.approx((3.0, 1.0))
    assert S.structure_size_in(None, 0.5) == pytest.approx((0.75, 0.55))


def test_header_parts_and_names(sample):
    p, parts = sample
    c2 = parts["c2"]
    item = _spectra_item(c2, "2_1H.pdf")
    assert S.header_parts(c2, item, "2", 1) == ["2 — 1H NMR", " (400 MHz, CDCl3)"]
    assert S.header_parts(c2, item, "2", 2)[-1] == " (continued)"
    assert S.header_parts(c2, {**item, "title": "Crude 1H NMR"}, "2", 1) == ["Crude 1H NMR"]
    assert S.header_parts(c2, {"type": "HPLC/SFC", "file": "x.pdf"}, "2", 1) == ["2 — HPLC/SFC"]
    assert S.spectrum_name(item, c2) == "1H NMR"
    assert S.spectrum_name({"type": "IR"}) == "IR"
    assert S.spectrum_name({"caption": "HPLC trace", "placement": "appendix"}) == "HPLC trace"


def test_compounds_with_spectra_follow_the_spectra(sample, tmp_path):
    p, parts = sample
    c2, c4 = parts["c2"], parts["c4"]
    assert [c.id for c in S.compounds_with_spectra(p)] == [c2.id]
    p.add_field(c4, {"key": "files"})
    pdf = fake_spectrum_pdf(tmp_path / "4_hplc.pdf", "HPLC of 4")
    p.attach_compound_file(c4, "files", pdf, {"placement": "inline"})
    assert [c.id for c in S.compounds_with_spectra(p)] == [c2.id]   # inline: not a spectrum
    c4.values["files"][0]["placement"] = "appendix"
    assert [c.id for c in S.compounds_with_spectra(p)] == [c2.id, c4.id]


def test_typed_header_and_caption_are_stored(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.set_spectrum_text(c2, "spectra", "2_1H.pdf", S.CAPTION_KEY, "  1H NMR (crude)  ")
    p.set_spectrum_text(c2, "spectra", "2_1H.pdf", S.HEADER_KEY, "My header")
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert (item[S.CAPTION_KEY], item[S.HEADER_KEY]) == ("1H NMR (crude)", "My header")
    p.set_spectrum_text(c2, "spectra", "2_1H.pdf", S.HEADER_KEY, "")
    assert _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")[S.HEADER_KEY] == ""
    with pytest.raises(ValueError):
        p.set_spectrum_text(c2, "spectra", "2_1H.pdf", "layout", "x")


def test_the_spectra_preview_kind_prints_one_compounds_spectra(sample):
    p, parts = sample
    c2, c4 = parts["c2"], parts["c4"]
    options = CompileOptions(only=("spectra", c2.id), preview_spectra=False)
    result = build(p, options, image_info, name="spectra_preview")
    source = result.typ_path.read_text(encoding="utf-8")
    assert source.count("si-spectrum(") == 3          # 1H, and both pages of the 13C
    assert not result.empty
    empty = build(p, CompileOptions(only=("spectra", c4.id)), image_info, name="sp4")
    assert empty.empty                                  # no spectra: "Nothing to show yet"


# ---------------------------------------------------------------------- the page

def _panel(p, compound):
    from si_assistant.ui.spectra_page import SpectraPagePanel
    return SpectraPagePanel(p, compound)


def test_the_page_lists_every_spectrum_page(sample):
    p, parts = sample
    panel = _panel(p, parts["c2"])
    texts = [panel.list.item(i).text() for i in range(panel.list.count())]
    assert texts == ["¹H NMR", "¹³C NMR · page 1 of 2", "¹³C NMR · page 2 of 2"]
    assert panel.current.item["file"] == "2_1H.pdf"
    assert not panel.scope.isVisibleTo(panel)             # one page: no choice
    panel.list.setCurrentRow(2)
    assert panel.current.page == 2 and panel.scope.isVisibleTo(panel)
    assert not panel.canvas.image.isNull()
    assert panel.canvas.header.startswith("2 — ¹³C NMR (100 MHz, CDCl₃)")
    assert panel.canvas.header.endswith("(continued)")
    panel.canvas.grab()                                   # draws without error
    panel.deleteLater()


def test_dragging_the_box_saves_its_page_position(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    canvas = panel.canvas
    canvas.resize(600, 450)
    box = canvas.box_rect()
    assert not box.isEmpty()
    spec, _k = canvas.spectrum_rect()
    assert box.left() > spec.left() and box.top() > spec.top()    # top-left corner
    start = box.center()
    canvas.start_move(start)
    canvas.drag_to(QPointF(spec.x() + 0.7 * spec.width(), spec.y() + 0.4 * spec.height()))
    canvas.finish()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert item["pages"]["1"] == {"x": pytest.approx(0.7, abs=1e-3),
                                  "y": pytest.approx(0.4, abs=1e-3)}
    assert panel.corner.currentData() == "dragged"
    assert panel.row_state("position") == "changed"
    assert panel.list.item(0).text().startswith("● ")      # marked as having its own
    # Choosing a corner again puts the box back in it (the dragged place is dropped).
    panel.corner.setCurrentIndex(panel.corner.findData("bottom-right"))
    panel.flush()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert item["pages"]["1"] == {"corner": "bottom-right"}
    panel.deleteLater()


def test_resizing_with_a_handle_changes_the_structure_size(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    canvas = panel.canvas
    canvas.resize(600, 450)
    box = canvas.box_rect()
    w0 = box.width()
    # Top-left corner placement: the box's top-left stays put; pull the far corner out.
    canvas.start_resize("bottom-right", box.bottomRight())
    far = box.topLeft() + (box.bottomRight() - box.topLeft()) * 1.5
    canvas.drag_to(far)
    canvas.finish()
    assert panel.scale.value() == 150
    after = canvas.box_rect()
    assert after.topLeft() == box.topLeft() and after.width() > w0
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert item["pages"]["1"] == {"struct_scale": 1.5}
    # Never beyond the allowed sizes.
    canvas.start_resize("bottom-right", after.bottomRight())
    canvas.drag_to(after.topLeft() + QPointF(1, 1))
    canvas.finish()
    assert panel.scale.value() == round(S.SCALE_RANGE[0] * 100)
    panel.deleteLater()


def test_overrides_round_trip_and_reset(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    from si_assistant.ui.spectra_page import ROWS
    assert all(panel.row_state(r) == "default" for r, _l, _k in ROWS)
    panel.per_page.setCurrentIndex(panel.per_page.findData(2))
    panel.orientation.setCurrentIndex(panel.orientation.findData("landscape"))
    panel.struct_frame.setChecked(True)
    panel.background.set_colour("#ffeecc")
    panel.background.changed.emit("#ffeecc")
    panel.same_size.setChecked(False)
    panel.id_size.setValue(9.0)
    panel.caption.setChecked(True)
    panel.flush()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert item["pages"]["1"] == {"per_page": 2, "orientation": "landscape",
                                  "struct_frame": True, "struct_bg": "#ffeecc",
                                  "id_size": 9.0, "caption": True}
    eff = S.effective(p.spectra_defaults(), item, "1")
    assert eff["per_page"] == 2 and eff["header_line"] is True     # the rest: defaults
    assert panel.row_state("per_page") == "changed" and panel.row_state("auto_crop") == "default"
    assert panel.canvas.caption.startswith("¹H NMR (CDCl₃")
    # Put one back by hand: it follows the default again (not stored).
    panel.caption.setChecked(False)
    panel.flush()
    assert "caption" not in _spectra_item(c2, "2_1H.pdf")["pages"]["1"]
    panel.reset_row("per_page")
    assert "per_page" not in _spectra_item(c2, "2_1H.pdf")["pages"]["1"]
    assert panel.per_page.currentData() == 1
    panel.reset_spectrum()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert "pages" not in item and "layout" not in item
    assert panel.row_state("struct_frame") == "default"
    # The page opens again on the spectrum shown last.
    panel.list.setCurrentRow(1)
    again = _panel(p, c2)
    assert again.list.currentRow() == 1
    panel.deleteLater()
    again.deleteLater()


def test_every_page_of_a_file(sample, monkeypatch):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    panel.list.setCurrentRow(1)                            # 13C, page 1 of 2
    panel.auto_crop.setChecked(True)                       # this page only
    panel.flush()
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    assert panel.row_state("auto_crop") == "this page"     # the page's own wins
    panel.header_line.setChecked(False)                    # every page
    panel.flush()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["layout"] == {"header_line": False}
    assert item["pages"] == {"1": {"auto_crop": True}}
    panel.list.setCurrentRow(2)                            # page 2 follows the file
    assert panel.scope.currentData() == "page"
    assert panel.row_state("header_line") == "this file"
    assert not panel.header_line.isChecked()
    # A dragged box always belongs to its own page.
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    panel.canvas.box_moved.emit(0.5, 0.5)
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["pages"]["2"] == {"x": 0.5, "y": 0.5} and "x" not in item["layout"]
    from si_assistant.ui import spectra_page
    monkeypatch.setattr(spectra_page.QMessageBox, "question",
                        lambda *a, **k: spectra_page.QMessageBox.StandardButton.Yes)
    panel.reset_spectrum()                                 # every page of the file
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert "layout" not in item and "pages" not in item
    panel.deleteLater()


def test_typed_text_on_the_page(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    assert panel.header_edit.placeholderText().startswith("automatic: 2 — ¹H NMR")
    assert "¹H NMR (CDCl₃, 400 MHz)" in panel.caption_edit.placeholderText()
    panel.caption_edit.setText("crude, after 2 h")
    panel.flush()
    assert _spectra_item(Project.open(p.folder).compound(c2.id),
                         "2_1H.pdf")[S.CAPTION_KEY] == "crude, after 2 h"
    panel.deleteLater()


def test_a_read_only_page_shows_but_cannot_change(sample):
    p, parts = sample
    ro = Project.open(p.folder, read_only=True)
    panel = _panel(ro, ro.compound(parts["c2"].id))
    assert not panel.canvas.editable and not panel.per_page.isEnabled()
    assert panel.header_edit.isReadOnly() and not panel.reset_btn.isEnabled()
    panel.canvas.box_moved.emit(0.5, 0.5)                  # ignored
    panel.flush()
    assert "pages" not in _spectra_item(Project.open(p.folder).compound(parts["c2"].id),
                                        "2_1H.pdf")
    panel.deleteLater()


def test_a_missing_file_is_said_in_plain_words(sample):
    p, parts = sample
    c2 = parts["c2"]
    p.attachment_path(c2, "spectra", "2_1H.pdf").unlink()
    panel = _panel(p, c2)
    assert panel.canvas.image.isNull() and "missing" in panel.canvas.message
    assert "(missing)" in panel.list.item(0).toolTip()
    panel.canvas.grab()
    panel.deleteLater()


# ---------------------------------------------------------------------- the window

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p, parts = build_sample(tmp_path)
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield win, parts
    win.close_project()
    win.close()


def _pages(win):
    item = win._find_item("section", win.project.spectra_section.id)
    return [(item.child(i).data(0, win_kind_role()), item.child(i).data(0, win_id_role()))
            for i in range(item.childCount())]


def win_kind_role():
    from si_assistant.ui.main_window import KIND_ROLE
    return KIND_ROLE


def win_id_role():
    from si_assistant.ui.main_window import ID_ROLE
    return ID_ROLE


def test_the_tree_has_a_page_per_compound_with_spectra(window, tmp_path, monkeypatch):
    from si_assistant.ui.spectra_page import SpectraPagePanel
    win, parts = window
    c2_id, c4_id = parts["c2"].id, parts["c4"].id
    assert _pages(win) == [("spectra_page", c2_id)]
    item = win._find_item("spectra_page", c2_id)
    assert item.text(0).startswith("2 — " + FACTS["c2_name"])
    win.select("spectra_page", c2_id)
    assert isinstance(win.panel, SpectraPagePanel)
    assert win.preview_pane.target == ("spectra", c2_id)
    assert win.preview_pane.compiles >= 0
    # Fixed: cannot be renamed, deleted or moved.
    assert not win.act_delete.isEnabled() and not win.act_rename.isEnabled()
    assert not win.act_up.isEnabled()
    win.delete_item()
    assert _pages(win) == [("spectra_page", c2_id)]
    # A spectrum added to compound 4 (on its page) gives it a page too, in compound order.
    win.select("compound", c4_id)                         # (a scheme page's compound tab)
    editor = next(b.editor for b in win.panel.compound_panel.boxes if b.defn.key == "spectra")
    assert not editor.view_btn.isEnabled()                # nothing attached yet
    editor.add_files([fake_spectrum_pdf(tmp_path / "4_1H.pdf", "1H of 4")])
    assert editor.view_btn.isEnabled()
    assert _pages(win) == [("spectra_page", c2_id), ("spectra_page", c4_id)]
    # Removing it takes the page away again.
    from si_assistant.ui import field_widgets
    monkeypatch.setattr(field_widgets.QMessageBox, "question",
                        lambda *a, **k: field_widgets.QMessageBox.StandardButton.Yes)
    editor._remove("4_1H.pdf")
    assert _pages(win) == [("spectra_page", c2_id)]


def test_a_shown_page_whose_spectra_are_gone_goes_back_to_the_section(window):
    win, parts = window
    c2 = win.project.compound(parts["c2"].id)
    win.select("spectra_page", c2.id)
    for name in ("2_1H.pdf", "2_13C.pdf"):
        win.project.remove_compound_file(c2, "spectra", name)
    win._refresh_labels()
    assert win._current() == ("section", win.project.spectra_section.id)
    assert _pages(win) == []


def test_view_spectra_page_selects_the_compounds_page(window, qapp, monkeypatch):
    from PySide6.QtTest import QTest
    from si_assistant.ui.spectra_page import SpectraPagePanel
    win, parts = window
    c2_id = parts["c2"].id
    spectra_item = win._find_item("section", win.project.spectra_section.id)
    spectra_item.setExpanded(False)
    win.select("compound", c2_id)
    editor = next(b.editor for b in win.panel.boxes if b.defn.key == "spectra")
    assert editor.view_btn.isEnabled()
    editor.view_btn.click()
    QTest.qWait(20)
    assert win._current() == ("spectra_page", c2_id)
    assert isinstance(win.panel, SpectraPagePanel) and spectra_item.isExpanded()
    # And back: "Compound page".
    win.panel.open_compound.emit(c2_id)
    QTest.qWait(20)
    assert win._current() == ("compound", c2_id)
    # From a scheme page's compound tab too.
    c4_id = parts["c4"].id
    win.select("compound", c4_id)
    told = []
    import si_assistant.ui.main_window as mw
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    win.panel.view_spectra.emit(c4_id)                     # (no spectra: explained)
    QTest.qWait(20)
    assert told and "no spectra page yet" in told[0]


def test_the_live_preview_compiles_a_spectra_page(window):
    win, parts = window
    c2_id = parts["c2"].id
    win.preview_pane.set_collapsed(False)
    win.select("spectra_page", c2_id)
    pane = win.preview_pane
    pane._start()
    assert pane.doc.pageCount() == 3                       # one page per spectrum
    assert pane.status.text().startswith("Up to date")
    before = pane.compiles
    win.panel.canvas.box_moved.emit(0.6, 0.6)              # saved: compiled again
    pane._start()
    assert pane.compiles == before + 1


# ---------------------------------------------------------------------- after review

def test_the_drawing_leaves_room_for_the_header_as_printed(sample):
    """When the slot's height limits the spectrum (2 per page, portrait), the header line
    takes its share first, as in the styles' si-spectrum."""
    p, parts = sample
    panel = _panel(p, parts["c2"])
    panel.per_page.setCurrentIndex(panel.per_page.findData(2))
    panel.flush()
    canvas = panel.canvas
    canvas.resize(700, 520)
    spec, k = canvas.spectrum_rect()
    slot = S.slot_size_in("ACS", 2, "portrait")
    printed = S.shown_size_in(slot, canvas.aspect(), None, header=True)
    assert spec.width() / k == pytest.approx(printed[0], rel=1e-3)
    assert spec.height() / k == pytest.approx(printed[1], rel=1e-3)
    assert printed[0] < S.shown_size_in(slot, canvas.aspect(), None)[0]   # (height-limited)
    panel.header_line.setChecked(False)                     # no header: the whole slot
    assert canvas.shown_in() == pytest.approx(S.shown_size_in(slot, canvas.aspect()))
    panel.deleteLater()


def test_a_click_on_the_box_without_moving_it_changes_nothing(sample):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    canvas = panel.canvas
    canvas.resize(600, 450)
    saved = []
    panel.saved.connect(saved.append)
    canvas.start_move(canvas.box_rect().center())           # pressed and let go at once
    canvas.finish()
    canvas.start_resize("bottom-right", canvas.box_rect().bottomRight())
    canvas.finish()
    point = canvas.box_rect().center().toPoint()            # and with the real mouse events
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert saved == []
    assert "pages" not in _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    panel.deleteLater()


def test_switching_scope_saves_a_waiting_change_where_it_was_made(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    panel.list.setCurrentRow(1)                             # 13C, page 1 of 2
    panel.auto_crop.setChecked(True)                        # not saved yet (timer running)
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["pages"] == {"1": {"auto_crop": True}} and "layout" not in item
    assert panel.scope_key() == "file" and panel.row_state("auto_crop") == "this page"
    panel.header_line.setChecked(False)                     # now for every page
    panel.scope.setCurrentIndex(panel.scope.findData("page"))
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["layout"] == {"header_line": False}
    assert item["pages"] == {"1": {"auto_crop": True}}
    panel.deleteLater()


def test_resizing_in_file_scope_keeps_a_pages_own_size(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    panel.list.setCurrentRow(2)                             # 13C, page 2 of 2
    panel.scale.setValue(150)                               # this page's own size
    panel.flush()
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    panel.canvas.box_resized.emit(2.0)                      # pulled a handle
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["pages"]["2"] == {"struct_scale": 2.0} and "layout" not in item
    assert panel.canvas.values["struct_scale"] == 2.0       # the box keeps its new size
    # Without a size of its own, the handle changes every page (the chosen scope).
    panel.scope.setCurrentIndex(panel.scope.findData("page"))
    panel.reset_row("struct_scale")
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    panel.canvas.box_resized.emit(0.8)
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_13C.pdf")
    assert item["layout"] == {"struct_scale": 0.8} and "pages" not in item
    assert panel.canvas.values["struct_scale"] == 0.8
    panel.deleteLater()


def test_the_structure_and_id_can_be_left_off(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    panel.canvas.resize(600, 450)
    assert not panel.canvas.box_rect().isEmpty()
    panel.struct_box.setChecked(False)
    assert panel.canvas.box_rect().isEmpty()               # nothing to drag either
    assert not panel.corner.isEnabled() and not panel.scale.isEnabled()
    panel.flush()
    item = _spectra_item(Project.open(p.folder).compound(c2.id), "2_1H.pdf")
    assert item["pages"]["1"] == {"struct_box": False}
    assert panel.row_state("struct_box") == "changed"
    source = build(p, CompileOptions(only=("spectra", c2.id)), image_info,
                   name="nobox").typ_path.read_text(encoding="utf-8")
    slots = source.split("si-spectrum(")[1:]
    assert len(slots) == 3
    assert "id: [" not in slots[0]                          # the 1H: no box
    assert sum("id: [" in s for s in slots) == 2            # the other two keep theirs
    # As a default for every spectrum (the Spectra section's page).
    p.set_spectra_defaults({**p.spectra_defaults(), "struct_box": False})
    source = build(p, CompileOptions(only=("spectra", c2.id)), image_info,
                   name="nobox2").typ_path.read_text(encoding="utf-8")
    assert "id: [" not in source
    panel.deleteLater()


def test_the_spectra_section_page_has_the_structure_and_id_switch(sample):
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, parts = sample
    panel = SpectraSectionPanel(p, p.spectra_section)
    assert panel.struct_box.isChecked() and panel.corner.isEnabled()
    panel.struct_box.setChecked(False)
    assert not panel.corner.isEnabled() and not panel.caption.isEnabled()
    panel.flush()
    assert Project.open(p.folder).spectra_defaults()["struct_box"] is False
    panel.sample.grab()
    panel.deleteLater()


def test_the_order_of_a_compounds_spectra_can_be_changed(sample, tmp_path):
    p, parts = sample
    c2 = parts["c2"]
    names = lambda: [i.get("file") for _k, i in S.spectrum_items(p, c2)]  # noqa: E731
    assert names() == ["2_1H.pdf", "2_13C.pdf"]
    order = S.moved(p, c2, 1, -1)
    assert order == [("spectra", "2_13C.pdf"), ("spectra", "2_1H.pdf")]
    assert S.moved(p, c2, 0, -1) is None and S.moved(p, c2, 1, 1) is None
    p.set_spectra_order(c2, order)
    reopened = Project.open(p.folder).compound(c2.id)
    assert S.manual_order(reopened) == order
    assert [i.get("file") for _k, i in S.spectrum_items(p, reopened)] == ["2_13C.pdf", "2_1H.pdf"]
    assert [i.get("file") for _k, i in S.spectrum_items(p, reopened, automatic=True)] == \
        ["2_1H.pdf", "2_13C.pdf"]
    # A file added later follows the ones in the order.
    p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(tmp_path / "2_19F.pdf", "19F"),
                           {"type": "19F"})
    assert names() == ["2_13C.pdf", "2_1H.pdf", "2_19F.pdf"]
    # The compiled SI follows it.
    source = build(p, CompileOptions(only=("spectra", c2.id)), image_info,
                   name="order").typ_path.read_text(encoding="utf-8")
    assert source.index("2_13C") < source.index("2_1H") < source.index("2_19F")
    p.set_spectra_order(c2, None)
    assert names() == ["2_1H.pdf", "2_13C.pdf", "2_19F.pdf"]


def test_moving_spectra_on_the_page(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    assert panel.up_btn.isVisible() or panel.up_btn.isVisibleTo(panel)
    assert not panel.up_btn.isEnabled() and panel.down_btn.isEnabled()
    assert not panel.auto_order_btn.isEnabled()             # already automatic
    panel.down_btn.click()                                  # 1H after the 13C
    texts = [panel.list.item(i).text() for i in range(panel.list.count())]
    assert texts == ["¹³C NMR · page 1 of 2", "¹³C NMR · page 2 of 2", "¹H NMR"]
    assert panel.current.item["file"] == "2_1H.pdf"         # still shown
    assert panel.up_btn.isEnabled() and not panel.down_btn.isEnabled()
    assert panel.auto_order_btn.isEnabled()
    assert S.manual_order(Project.open(p.folder).compound(c2.id))[0] == ("spectra", "2_13C.pdf")
    panel.auto_order_btn.click()
    assert panel.list.item(0).text() == "¹H NMR"
    assert not S.manual_order(Project.open(p.folder).compound(c2.id))
    panel.deleteLater()


def test_an_unreadable_pdf_is_listed_and_explained(sample, tmp_path):
    p, parts = sample
    c4 = parts["c4"]
    bad = tmp_path / "4_1H.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    p.attach_compound_file(c4, "spectra", bad, {"type": "1H"})
    panel = _panel(p, c4)
    assert panel.list.count() == 1
    assert panel.list.item(0).text() == "¹H NMR (could not be read)"
    assert "4_1H.pdf could not be opened" in panel.canvas.message
    assert "Stream" not in panel.canvas.message
    assert panel.header_line.isChecked()                    # the real settings are shown
    panel.canvas.grab()
    # The compiled SI warns in the same plain words.
    result = build(p, CompileOptions(only=("spectra", c4.id)), image_info, name="bad")
    assert any("4_1H.pdf could not be opened" in w for w in result.warnings)
    panel.deleteLater()


def test_a_page_without_spectra_shows_the_defaults(sample):
    p, parts = sample
    panel = _panel(p, parts["c4"])
    assert "no spectra yet" in panel.canvas.message
    assert not panel.scope.isVisibleTo(panel)
    assert panel.header_line.isChecked() == p.spectra_defaults()["header_line"]
    assert panel.spectrum_width.value() == p.spectra_defaults()["spectrum_frame_width"]
    panel.deleteLater()


def test_an_additional_file_without_a_caption_is_named_by_its_file():
    assert S.spectrum_name({"file": "4_hplc.pdf", "placement": "appendix"}) == "4_hplc.pdf"
    assert S.spectrum_name({}) == "File"


def test_file_settings_are_explained_where_the_reset_is(sample):
    p, parts = sample
    c2 = parts["c2"]
    panel = _panel(p, c2)
    panel.list.setCurrentRow(1)
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    panel.struct_frame.setChecked(True)
    panel.scope.setCurrentIndex(panel.scope.findData("page"))
    assert panel.row_state("struct_frame") == "this file"
    assert not panel.reset_btn.isEnabled() and panel.reset_hint.isVisibleTo(panel)
    panel.scope.setCurrentIndex(panel.scope.findData("file"))
    assert panel.reset_btn.isEnabled() and not panel.reset_hint.isVisibleTo(panel)
    panel.deleteLater()


def test_view_spectra_page_for_a_compound_with_only_an_hplc_trace(window, tmp_path):
    win, parts = window
    c4_id = parts["c4"].id
    win.select("compound", c4_id)
    cp = win.panel.compound_panel
    c4 = cp.compound
    win.project.add_field(c4, {"key": "files"})
    win.project.attach_compound_file(c4, "files", fake_spectrum_pdf(tmp_path / "4_hplc.pdf",
                                                                     "HPLC of 4"),
                                     {"placement": "inline"})
    win.select("compound", parts["c2"].id)
    win.select("compound", c4_id)                          # (the page again, with the field)
    cp = win.panel.compound_panel
    editors = {b.defn.key: b.editor for b in cp.boxes if b.defn.key in ("spectra", "files")}
    assert not editors["spectra"].view_btn.isEnabled()
    assert not editors["files"].view_btn.isVisibleTo(cp)
    # Set it to "Spectra section" on the page: the buttons follow once it is saved.
    combo = editors["files"].row_widgets[0][1]["placement"]
    combo.setCurrentIndex(combo.findData("appendix"))
    cp.flush()
    assert c4.values["files"][0]["placement"] == "appendix"
    assert editors["spectra"].view_btn.isEnabled()
    assert editors["files"].view_btn.isVisibleTo(cp) and editors["files"].view_btn.isEnabled()
    assert ("spectra_page", c4_id) in _pages(win)
