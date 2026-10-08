"""The scheme editor on a scheme page (SPEC §13n.4): pasting into boxes, the Compound
tick, ID labels and dragging them, conditions with structures, Layout and Project settings
▸ Schemes, and saving/locking. Made-up structures only."""

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QGuiApplication

from si_assistant.core import scheme_editor as se
from si_assistant.core.project import Project
from tests.test_scheme_editor import chemdraw_picture


@pytest.fixture
def page(qapp, tmp_path):
    from si_assistant.ui.scheme_panel import SchemePanel
    p = Project.create(tmp_path, "UI")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["2a", "2b", "2c"])
    k = p.add_scheme(data.id, name="Route")
    panel = SchemePanel(p, k, lambda: "Tester")
    panel.resize(1100, 800)
    yield p, k, panel
    panel.flush()
    panel.close()
    panel.deleteLater()
    qapp.processEvents()
    p.close()


def test_new_scheme_page_shows_the_editor(page):
    p, k, panel = page
    assert panel.editor is not None and not hasattr(panel, "mode_picture")   # (§13p.5)
    ed = panel.editor
    assert len(ed.boxes) == 2 and len(ed.steps) == 1      # a starting material → a product
    ed.add_box(se.START)
    ed.add_step()
    assert len(ed.boxes) == 4 and len(ed.steps) == 2
    assert p.can_undo
    p.undo()                                                # (the window reloads the page)


def test_paste_into_a_box_and_mark_it_as_a_compound(page, monkeypatch, qapp):
    from si_assistant.ui import scheme_editor_ui as ui
    p, k, panel = page
    ed = panel.editor
    product = next(b for b in ed.boxes if b.where != se.START)
    # ChemDraw's copy: its picture (made-up SVG) and the structure as MOL text.
    monkeypatch.setattr(ui, "clipboard_picture_for", lambda project: chemdraw_picture())
    from tests import structure_samples as ss
    QGuiApplication.clipboard().setText(ss.structure_molblock())
    product.paste_from_clipboard()
    box = se.find_box(k, product.box_id)[0]
    assert se.path_of(k, box["picture"]).suffix == ".svg" and box["structure"]
    product = next(b for b in ed.boxes if b.box_id == box["id"])
    assert not product.canvas.pixmap.isNull()
    # Ctrl+V on the selected box (the paste target).
    sm = next(b for b in ed.boxes if b.where == se.START)
    monkeypatch.setattr(ui, "clipboard_picture_for", lambda project: None)
    QGuiApplication.clipboard().setText("CC(=O)c1ccccc1")
    sm.paste_from_clipboard()
    assert se.box_view(p, k, se.starting(k)[0]).mol is not None
    # Tick "Compound": a sub-compound page with the next ID, shown as a tab.
    product = next(b for b in ed.boxes if b.box_id == box["id"])
    product.compound_box.setChecked(True)
    assert len(k.children) == 1 and panel.tabs.count() == 1
    c = k.children[0]
    product = next(b for b in ed.boxes if b.box_id == box["id"])
    assert product.canvas.label == "2a" and product.label_row.isHidden()
    assert p.structure_info(c).formula == "C11H14O3"
    # The compound's yield is shown under it, as printed.
    c.values["yield"] = {"percent": "77"}
    p.save_compound(c)
    ed.refresh_boxes(redraw=False)
    product = next(b for b in ed.boxes if b.box_id == box["id"])
    assert product.canvas.yield_text == "77%"
    assert "77%" in product.yield_edit.placeholderText()
    # Untick, keeping the page.
    monkeypatch.setattr(ui.SchemeEditorWidget, "_ask_compound", lambda *a: "keep")
    product.compound_box.setChecked(False)
    assert "compound" not in se.find_box(k, box["id"])[0] and len(k.children) == 1
    # Remove the box and delete the page.
    monkeypatch.setattr(ui.SchemeEditorWidget, "_ask_compound", lambda *a: "delete")
    ed.link_existing(box["id"], c.id)
    ed.remove_box(box["id"])
    assert k.children == [] and panel.tabs.count() == 0


def test_label_typed_mentioned_and_dragged(page, monkeypatch):
    from si_assistant.ui import scheme_editor_ui as ui
    p, k, panel = page
    other = p.add_compound(k.section_id, name="elsewhere", index=0)
    ed = panel.editor
    sm = next(b for b in ed.boxes if b.where == se.START)
    se.set_box_structure(p, k, sm.box_id, "CCO")
    ed.rebuild()
    sm = next(b for b in ed.boxes if b.where == se.START)
    sm.label_edit.setText("S1")
    sm._label_typed()
    assert se.starting(k)[0]["label"] == "S1"
    sm = next(b for b in ed.boxes if b.where == se.START)
    assert sm.canvas.label == "S1"
    monkeypatch.setattr(ui, "choose_compound", lambda parent, project: other.id)
    sm._mention()
    assert se.starting(k)[0]["label"] == "@{" + other.id + "}"
    sm = next(b for b in ed.boxes if b.where == se.START)
    assert sm.canvas.label == "2a" and sm.label_edit.isReadOnly()
    # Dragging the ID saves where it went (pt); "Reset" puts it back.
    canvas = sm.canvas
    start = canvas.label_rect().center()
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    def mouse(kind, pos, button=Qt.MouseButton.LeftButton):
        return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, button,
                           Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, start))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, start + QPointF(25, 10)))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, start + QPointF(25, 10)))
    dx, dy = se.label_offset(se.starting(k)[0])
    assert dx == pytest.approx(25 / canvas.px_per_pt, abs=0.1) and dy > 0
    sm = next(b for b in ed.boxes if b.where == se.START)
    sm.set_offset(0, 0)
    assert se.label_offset(se.starting(k)[0]) == (0.0, 0.0)


def test_conditions_are_saved_and_take_pasted_structures(page, qapp):
    p, k, panel = page
    ed = panel.editor
    step = ed.steps[0]
    step.top.text.edit.setFocus()
    step.top.text.edit.insertPlainText("NaBH4 (1.5 equiv)")
    QGuiApplication.clipboard().setText("ClCCl")      # SMILES text is just text …
    step.flush()
    assert se.steps(k)[0]["top"] == "NaBH4 (1.5 equiv)"
    # … but a copied MOL block (or ChemDraw's copy) becomes a small structure.
    from tests import structure_samples as ss
    mime = QMimeData()
    mime.setText(ss.structure_molblock())
    step.top.text.edit.insertFromMimeData(mime)
    step.flush()
    text = se.steps(k)[0]["top"]
    assert "@struct{s-" in text
    sid = se.inline_ids(text)[0]
    assert se.read_mol(se.path_of(k, se.inline_entry(k, sid)["structure"])) is not None
    assert ed.inline.size(sid) is not None
    # The bottom box does not take structures; pasting the conditions text elsewhere
    # (a compound's procedure) drops the structure.
    assert step.bottom.text.edit.inline_structures is None
    from si_assistant.ui.rich_text import MARKUP_MIME, RichTextEdit
    other = RichTextEdit()
    data = QMimeData()
    data.setData(MARKUP_MIME, text.encode("utf-8"))
    other.insertFromMimeData(data)
    assert "@struct" not in other.markup() and "NaBH4" in other.markup()
    # Alignment of one box.
    step.bottom.set_align("right")
    assert se.steps(k)[0]["bottom_align"] == "right"


def test_layout_dialog_and_project_settings_tab(page, monkeypatch, qapp):
    from si_assistant.ui import scheme_editor_ui as ui
    p, k, panel = page
    dialog = ui.SchemeLayoutDialog(panel, p, k)
    dialog.form.fields["fit"].setCurrentIndex(dialog.form.fields["fit"].findData("shrink"))
    dialog.form.fields["scale_pct"].setValue(75)
    chosen = dialog.values()
    monkeypatch.setattr(ui.SchemeLayoutDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ui.SchemeLayoutDialog, "values", lambda self: chosen)
    panel.editor.edit_layout()
    assert k.data["editor"]["layout"] == {"fit": "shrink", "scale_pct": 75.0}
    # Project settings ▸ Schemes.
    tab = ui.SchemesTab(p.settings)
    tab.form.fields["cond_size_pt"].setValue(8)
    tab.form.fields["yields"].setCurrentIndex(tab.form.fields["yields"].findData("none"))
    settings = {}
    tab.apply(settings)
    assert settings["schemes"] == {"cond_size_pt": 8.0, "yields": "none"}
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    dlg = ProjectSettingsDialog(p, "Tester")
    dlg.schemes_tab.form.fields["arrow_min_mm"].setValue(20)
    dlg.apply()
    assert Project.open(p.folder).settings["schemes"] == {"arrow_min_mm": 20.0}


def test_save_locks_the_editor(page, monkeypatch):
    p, k, panel = page
    ed = panel.editor
    ed.set_locked(True)
    assert se.is_locked(k) and "saved" not in k.data   # (the editor's own lock, §13n.4)
    assert ed.save_btn.isHidden() and not ed.edit_btn.isHidden()
    assert all(b._paste_locked for b in ed.boxes)
    assert all(s.top.text.edit.isReadOnly() for s in ed.steps)
    ed.set_locked(False)
    assert not ed.boxes[0]._paste_locked


def test_live_preview_of_an_editor_scheme(page):
    from si_assistant.compile.document import CompileOptions, build
    from si_assistant.compile.qt_images import convert_to_png, image_info
    p, k, panel = page
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CCO")
    se.set_box_structure(p, k, se.steps(k)[0]["products"][0]["id"], "CC=O")
    options = CompileOptions.from_settings(p.settings)
    options.only = ("scheme", k.id)
    options.preview_spectra = False
    result = build(p, options, image_info, convert_to_png, name="live")
    assert "#si-reaction-scheme(" in result.typ_path.read_text(encoding="utf-8")
    assert not result.empty
