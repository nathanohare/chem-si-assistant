"""Review list of SPEC §13p: schemes without a label by default, no gap under the scheme
editor, a scheme compound previewed with its scheme, no flashing windows in the scheme
editor, and the "One pasted picture" way of making a scheme removed. Made-up data only."""

import json

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.core import scheme_editor as se
from si_assistant.core.labels import compute
from si_assistant.core.project import Project, ProjectError


def new_project(tmp_path):
    p = Project.create(tmp_path, "P")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["2a", "2b", "2c"])
    return p, data


def drawn_scheme(p, data, name="Route"):
    k = p.add_scheme(data.id, name=name)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    box = se.steps(k)[0]["products"][0]
    se.set_box_structure(p, k, box["id"], "CC(O)c1ccccc1")
    se.link_new_compound(p, k, box["id"], name="alcohol")
    return k


def source(p, k):
    options = CompileOptions.from_settings(p.settings)
    options.only = ("scheme", k.id)
    options.preview_spectra = False
    result = build(p, options, image_info, convert_to_png, name="live")
    return result.typ_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- 1. no label

def test_schemes_print_without_label_or_caption_by_default(tmp_path):
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    assert se.DEFAULT_LAYOUT["show_label"] is False
    src = source(p, k)
    assert "label: none, caption: none" in src
    assert "caption: [" not in src.split("#si-reaction-scheme")[1]
    assert k.id not in compute(p).by_id
    # Turned on for this scheme (its Layout…): numbered, with its name as the caption.
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    assert se.layout_overrides(k) == {"show_label": True}
    assert compute(p).label(k.id) == "Scheme S1"
    assert 'label: [#"Scheme S1"], caption: [#"Route"]' in source(p, k)
    # … or for every scheme (Project settings ▸ Schemes); the scheme's own "off" wins.
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": False})
    se.set_project_layout(p.settings, {**se.DEFAULT_LAYOUT, "show_label": True})
    assert p.settings["schemes"] == {"show_label": True}
    other = drawn_scheme(p, data, name="Other")
    assert compute(p).label(other.id) == "Scheme S2"   # (both follow the project's default)
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": False})
    assert se.layout_overrides(k) == {"show_label": False}
    assert k.id not in compute(p).by_id and compute(p).label(other.id) == "Scheme S1"
    assert se.clean_value("show_label", "yes") is None   # (a hand-edited file: ignored)


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_a_scheme_without_label_compiles_in_every_style(qapp, tmp_path, style):
    from pypdf import PdfReader
    from si_assistant.compile.run import compile_pdf
    p, data = new_project(tmp_path)
    p.settings["journal_style"] = style
    p.save_settings()
    drawn_scheme(p, data, name="Reduction route")
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                         convert_to_png)
    text = "".join(page.extract_text() for page in PdfReader(str(result.pdf)).pages)
    assert "2a" in text and "Scheme S1" not in text and "Reduction route" not in text
    assert "List of Schemes" not in text
    assert not [w for w in result.warnings if "cheme" in w]


def test_scheme_page_label_box_follows_the_layout(qapp, tmp_path):
    from si_assistant.ui.scheme_editor_ui import LayoutForm
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    panel = SchemePanel(p, k, lambda: "Tester")
    assert not panel.label_edit.isEnabled()
    assert panel.label_edit.placeholderText() == "not printed"
    assert "Label and caption" in panel.label_edit.toolTip()
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    panel.editor.layout_changed.emit()          # (as after OK in its Layout… window)
    assert panel.label_edit.isEnabled()
    assert panel.label_edit.placeholderText() == "Scheme S1 (automatic)"
    form = LayoutForm(se.DEFAULT_LAYOUT)
    assert not form.fields["show_label"].isChecked()
    form.fields["show_label"].setChecked(True)
    assert form.values()["show_label"] is True
    panel.flush()
    panel.deleteLater()


# ---------------------------------------------------------------------- 2. no gap

@pytest.mark.parametrize("height", [900, 1600])
def test_no_gap_between_the_scheme_editor_and_the_compound_page(qapp, tmp_path, height):
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    panel = SchemePanel(p, k, lambda: "Tester")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.resize(1600, height)
    panel.show()
    for _ in range(10):
        qapp.processEvents()
    need = panel.scheme_need()
    assert panel.splitter.sizes()[0] == need          # exactly what it needs …
    assert panel.scheme_area.maximumHeight() == need  # … and the divider can't add more
    ed = panel.editor
    assert ed.status.isHidden()                       # (no empty line under it)
    # The boxes fit across: no room kept for a sideways scroll bar.
    assert ed.scroll.need == (ed.strip.sizeHint().height() + 2 * ed.scroll.frameWidth() + 2)
    ed.say("⚠ Something to say")
    assert not ed.status.isHidden()
    ed.say("")
    assert ed.status.isHidden()
    # Narrow: the boxes scroll sideways, and the scroll bar has its room.
    panel.resize(420, height)
    for _ in range(10):
        qapp.processEvents()
    bar = ed.scroll.horizontalScrollBar().sizeHint().height()
    assert ed.scroll.need == (ed.strip.sizeHint().height() + bar
                              + 2 * ed.scroll.frameWidth() + 2)
    panel.close()
    panel.deleteLater()


# ---------------------------------------------------------------------- 3. preview

def test_a_scheme_compound_is_previewed_with_its_scheme(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from si_assistant.ui import main_window as mw
    p, data = new_project(tmp_path / "p")
    k = drawn_scheme(p, data)
    c = k.children[0]
    folder = p.folder
    p.close()
    prefs = str(tmp_path / "prefs.ini")   # (never the real settings)
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(prefs, QSettings.Format.IniFormat))
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    try:
        assert win.open_project(folder)
        win.select("compound", c.id)
        assert win.panel.current_compound_id() == c.id
        assert win.preview_pane.target == ("scheme", k.id)
        win.select("scheme", k.id)
        assert win.preview_pane.target == ("scheme", k.id)
    finally:
        win.close_project()
        win.close()


# ---------------------------------------------------------------------- 4. no flashing

class ShownWindows(QObject):
    """Every window (other than `own`) that is shown while it listens."""

    def __init__(self, own):
        super().__init__()
        self.own = own
        self.shown = []

    def eventFilter(self, obj, event):  # noqa: N802
        if (event.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow()
                and obj is not self.own):
            self.shown.append(type(obj).__name__)
        return False


def test_editing_the_scheme_flashes_no_windows(qapp, tmp_path):
    """Adding or removing a starting material, step or product made little windows
    (the boxes' "Click, then Ctrl+V" hints, a step's ✕ and "+ Product") flash up in front
    of the page, because they were shown before being put in place."""
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    panel = SchemePanel(p, k, lambda: "Tester")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.resize(1100, 900)
    panel.show()
    qapp.processEvents()
    spy = ShownWindows(panel)
    QApplication.instance().installEventFilter(spy)
    try:
        ed = panel.editor
        ed.add_box(se.START)
        ed.add_step()
        ed.add_box(se.steps(k)[1]["id"])
        ed.remove_box(se.starting(k)[1]["id"])
        ed.remove_step(se.steps(k)[1]["id"])
        panel.select_compound(k.children[0].id)      # a compound tab (its paste boxes)
        qapp.processEvents()
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert spy.shown == []
    panel.close()
    panel.deleteLater()


# ---------------------------------------------------------------------- 5. no pictures

def test_the_scheme_page_has_no_pasted_picture_choice(qapp, tmp_path):
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    panel = SchemePanel(p, k, lambda: "Tester")
    from PySide6.QtWidgets import QAbstractButton
    texts = [b.text() for b in panel.scheme_area.findChildren(QAbstractButton)]
    texts += [b.text() for b in panel.findChildren(QAbstractButton)
              if not panel.lower.isAncestorOf(b)]
    assert "Layout…" in texts
    assert not any("picture" in t.lower() or t == "Paste scheme" for t in texts)
    for name in ("mode_picture", "mode_editor", "picture_block", "canvas", "auto_place"):
        assert not hasattr(panel, name)
    panel.add_menu.aboutToShow.emit()
    assert [a.text() for a in panel.add_menu.actions()] == [
        "Blank compound (not drawn in the scheme)"]
    panel.deleteLater()
    with pytest.raises(TypeError):
        p.add_scheme(data.id, mode="picture")
    assert not hasattr(p, "set_scheme_picture")


def test_old_picture_scheme_in_a_compiled_si(tmp_path):
    """A project from before: a picture scheme page whose compound has a structure. The
    SI prints the scheme editor's drawing (the compound as the product) and no warning
    about a missing picture."""
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Old route")
    c = p.add_compound(data.id, name="alcohol", scheme_id=k.id)
    p.set_structure(c, "CC(O)c1ccccc1")
    (k.folder / "scheme.json").write_text(json.dumps(
        {"id": k.id, "name": "Old route", "children": [c.id], "image": "scheme.png",
         "labels": {c.id: {"x": 0.8, "y": 0.95, "manual": False}}}), encoding="utf-8")
    p.reload()
    k = p.scheme(k.id)
    src = source(p, k)
    assert src.count('(kind: "mol"') == 1             # the product (the starting box is empty)
    assert "si-compound-title" in src
    assert se.linked_compound_ids(k) == [c.id]


def test_old_scheme_picture_in_the_trash_is_explained(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Old route")
    picture = k.folder / "scheme.png"
    picture.write_bytes(b"PNG")
    p._move_to_trash(picture, kind="scheme_file", name=picture.name,
                     extra={"scheme_id": k.id, "item": picture.name, "scheme_name": k.name})
    entry = p.list_trash()[0]
    with pytest.raises(ProjectError, match="scheme editor"):
        p.restore_from_trash(entry.id)


def test_converting_an_old_compound_with_a_scheme_picture(tmp_path):
    """Very old projects kept a scheme picture on the compound: "Change to reaction
    scheme…" now draws it with the editor and puts the old picture in the Trash."""
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="B")
    p.set_structure(c, "CCOC(C)=O")
    p._set_image(c, "scheme_image", data=b"PNG old scheme", ext=".png")
    k = p.convert_to_scheme(c.id)
    assert k.data["mode"] == "editor" and "image" not in k.data
    assert se.box_compound(p, k, se.steps(k)[0]["products"][0]) is c
    assert [e.name for e in p.list_trash()]
