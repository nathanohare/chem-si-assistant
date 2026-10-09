"""Audit fixes for the scheme editor, the scheme page and duplicating (made-up data only):
removing a box marked Compound, typing on a compound tab before a scheme change, ID label
escapes, a plain click on an ID, a rejected picture file, a compound that can't go to the
Trash, mentions in a duplicated scheme's conditions, hand-edited file names, heteroatom
NMR boxes on copies, the compound page built once, undo labels of the tabs, and the
yields shown under the conditions."""

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from si_assistant.core import duplicate as dup
from si_assistant.core import fileio
from si_assistant.core import scheme_editor as se
from si_assistant.core.project import Project, ProjectError
from si_assistant.ui.clipboard_image import PastedPicture
from tests.test_scheme_editor import chemdraw_picture


def new_project(tmp_path, ids=("2a", "2b", "2c", "2d", "2e", "2f", "2g", "2h")):
    p = Project.create(tmp_path, "Audit")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, list(ids))
    return p, data


def saved_editor(k) -> dict:
    return json.loads((k.folder / "scheme.json").read_text(encoding="utf-8"))["editor"]


def box_ids(k) -> list[str]:
    return [b["id"] for b, _w in se.all_boxes(k)]


def linked_scheme(p, data):
    """A scheme: a starting material → a product marked Compound."""
    k = p.add_scheme(data.id, name="Route")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    product = se.steps(k)[0]["products"][0]
    se.set_box_structure(p, k, product["id"], "CC(O)c1ccccc1")
    c = se.link_new_compound(p, k, product["id"], name="alcohol")
    return k, product["id"], c


# ---------------------------------------------------------------------- scheme-1

@pytest.mark.parametrize("delete", [False, True])
def test_removing_a_box_marked_compound_removes_the_box(tmp_path, delete):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    with p.undo_step("Remove"):
        se.remove_box(p, k, product, delete_compound=delete)
    assert product not in box_ids(k)
    saved = saved_editor(k)
    assert all(b["id"] != product for s in saved["steps"] for b in s["products"])
    if delete:
        assert k.children == [] and any(e.kind == "compound" for e in p.list_trash())
    else:
        assert k.children == [c] and se.unlinked_children(k) == [c]
    p.undo()
    k = p.scheme(k.id)
    assert product in box_ids(k) and se.find_box(k, product)[0].get("compound") == c.id


def test_editor_data_keeps_the_lists_callers_hold(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    held_start = se.starting(k)
    held_products = se.steps(k)[0]["products"]
    held_steps = se.steps(k)
    se.editor_data(k)
    assert se.starting(k) is held_start and se.steps(k) is held_steps
    assert se.steps(k)[0]["products"] is held_products
    # A hand-edited file is still tidied (only boxes kept, IDs given).
    k.data["editor"]["starting"].append("not a box")
    k.data["editor"]["steps"][0]["products"] = "nonsense"
    se.editor_data(k)
    assert all(isinstance(b, dict) for b in se.starting(k))
    assert se.steps(k)[0]["products"] == []


# ---------------------------------------------------------------------- scheme-5

def test_a_rejected_picture_file_leaves_the_old_picture(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    box = se.starting(k)[0]
    se.set_box_structure(p, k, box["id"], "CCO", chemdraw_picture())
    picture = se.path_of(k, box["picture"])
    original = picture.with_suffix(".emf")
    assert picture.exists() and original.exists()
    bmp = tmp_path / "photo.bmp"
    bmp.write_bytes(b"BM not supported")
    for bad in (bmp, tmp_path / "gone.png"):
        with pytest.raises(ProjectError):
            se.set_box_picture(p, k, box["id"], source=bad)
        assert picture.exists() and original.exists()
        assert se.find_box(k, box["id"])[0]["picture"] == f"editor/{picture.name}"
    # A pasted picture of a kind that can't be used: the old structure and picture stay.
    with pytest.raises(ProjectError):
        se.set_box_structure(p, k, box["id"], "CCN", PastedPicture(b"x", None, "", ".bmp"))
    assert picture.exists() and original.exists()
    assert se.box_view(p, k, se.find_box(k, box["id"])[0]).mol.GetNumAtoms() == 3


# ---------------------------------------------------------------------- scheme-6

def _block_rename(monkeypatch, folder):
    real = fileio.rename

    def rename(source, target, *a, **kw):
        if str(source) == str(folder):
            raise PermissionError(13, "The file is open in another program", str(source))
        return real(source, target, *a, **kw)
    monkeypatch.setattr(fileio, "rename", rename)


def test_unmark_with_delete_changes_nothing_when_the_page_cant_go_to_the_trash(
        tmp_path, monkeypatch):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    editor_files = sorted(f.name for f in (k.folder / se.FILES_DIR).iterdir()) \
        if (k.folder / se.FILES_DIR).is_dir() else []
    _block_rename(monkeypatch, c.folder)
    for action in (lambda: se.unlink_box(p, k, product, delete_compound=True),
                   lambda: se.remove_box(p, k, product, delete_compound=True)):
        with pytest.raises(ProjectError):
            action()
        assert se.find_box(k, product)[0].get("compound") == c.id   # still linked
        assert k.children == [c] and c.folder.is_dir()
        saved = saved_editor(k)
        assert saved["steps"][0]["products"][0].get("compound") == c.id
        now = sorted(f.name for f in (k.folder / se.FILES_DIR).iterdir()) \
            if (k.folder / se.FILES_DIR).is_dir() else []
        assert now == editor_files                                    # no copies left


def test_unmark_with_delete_keeps_a_copy_of_the_structure(tmp_path):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    with p.undo_step("Unmark"):
        se.unlink_box(p, k, product, delete_compound=True)
    box = se.find_box(k, product)[0]
    assert "compound" not in box and k.children == []
    assert se.box_view(p, k, box).mol is not None        # (copied from the Trash entry)
    assert any(e.kind == "compound" for e in p.list_trash())
    assert c.folder.name not in [f.name for f in c.folder.parent.iterdir()]
    p.undo()
    k = p.scheme(k.id)
    assert se.find_box(k, product)[0].get("compound") == c.id and len(k.children) == 1


# ---------------------------------------------------------------------- scheme-8, 11, 12

def test_duplicated_scheme_conditions_mention_the_copies(tmp_path):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    step = se.steps(k)[0]
    se.set_conditions(p, k, step["id"], "bottom", "then @{" + c.id + "}, NaOH")
    se.set_conditions(p, k, step["id"], "top", "@{" + c.id + "} (5 mol%)")
    with p.undo_step("Duplicate"):
        made = dup.duplicate_scheme(p, k, {"name", "structure"}, copies=2)
    for new in made:
        new = p.scheme(new.id)
        copied = new.children[0].id
        assert copied != c.id
        assert se.steps(new)[0]["bottom"] == "then @{" + copied + "}, NaOH"
        assert se.steps(new)[0]["top"] == "@{" + copied + "} (5 mol%)"
    assert se.steps(k)[0]["bottom"] == "then @{" + c.id + "}, NaOH"   # (original unchanged)


def nuclei(c) -> list[str]:
    return [r["nucleus"] for r in c.values.get("nmr") or []]


def test_copies_of_a_fluorinated_compound_get_the_19F_box(tmp_path):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="fluoro")
    p.set_structure(c, "FC(F)(F)c1ccccc1")
    assert "19F" in nuclei(c)
    made = dup.duplicate_compound(p, c, {"name", "structure"}, copies=2)
    for m in made:
        assert nuclei(m) == ["1H", "13C", "19F"]
        assert all(not r["text"] for r in m.values["nmr"])
    # Without its structure: only the usual boxes.
    plain = dup.duplicate_compound(p, c, {"name"})[0]
    assert nuclei(plain) == ["1H", "13C"]
    # A scheme's compound, copied with the scheme.
    k, product, sc = linked_scheme(p, data)
    p.set_structure(sc, "FC(F)(F)c1ccccc1")
    new = dup.duplicate_scheme(p, k, {"name", "structure"})[0]
    assert nuclei(new.children[0]) == ["1H", "13C", "19F"]
    reopened = Project.open(p.folder, read_only=True)
    assert nuclei(reopened.compound(made[0].id)) == ["1H", "13C", "19F"]


def test_duplicated_schemes_match_on_disk(tmp_path, monkeypatch):
    p, data = new_project(tmp_path, ids=tuple(f"2{x}" for x in "abcdefghijklmnop"))
    k, product, c = linked_scheme(p, data)
    box = se.add_box(p, k, se.steps(k)[0]["id"])
    se.set_box_structure(p, k, box["id"], "CCO")
    se.link_new_compound(p, k, box["id"], name="ethanol")
    made = dup.duplicate_scheme(p, k, {"name", "structure"}, copies=3)
    reopened = Project.open(p.folder, read_only=True)
    for new in made:
        again = reopened.scheme(new.id)
        assert [x.id for x in again.children] == [x.id for x in new.children]
        assert len(again.children) == 2
        assert [b.get("compound") for b in se.steps(again)[0]["products"]] == \
            [x.id for x in again.children]
    assert all(x.scheme_id for x in reopened.section(data.id).compounds)   # none loose
    # A copy failing half-way: what it made is still listed by the copy's scheme.json.
    real, calls = dup._fill_copy, []

    def failing(project, original, new, keys):
        calls.append(new.id)
        if len(calls) == 2:
            raise OSError("disk full")
        real(project, original, new, keys)
    monkeypatch.setattr(dup, "_fill_copy", failing)
    with pytest.raises(OSError):
        dup.duplicate_scheme(p, k, {"name"})
    copy = next(x for x in p.section(data.id).schemes if x.id not in
                {k.id, *(m.id for m in made)})
    reopened = Project.open(p.folder, read_only=True)
    assert [x.id for x in reopened.scheme(copy.id).children] == [x.id for x in copy.children]
    assert [x.id for x in copy.children] == calls


# ---------------------------------------------------------------------- scheme-10

def test_hand_edited_file_names_that_are_not_text(tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    sm = se.starting(k)[0]
    se.set_box_structure(p, k, sm["id"], "CCO")
    good = se.add_box(p, k, se.START)
    se.set_box_structure(p, k, good["id"], "CCN")
    product = se.steps(k)[0]["products"][0]
    raw = json.loads((k.folder / "scheme.json").read_text(encoding="utf-8"))
    ed = raw["editor"]
    ed["starting"][0]["structure"] = ["editor/b.mol"]
    ed["starting"][1]["picture"] = 5
    ed["starting"][1]["compound"] = ["c-x"]
    ed["inline"]["s-hand"] = {"structure": [1], "picture": {}}
    ed["steps"][0]["top"] = "@struct{s-hand} THF"
    (k.folder / "scheme.json").write_text(json.dumps(raw), encoding="utf-8")
    p.reload()
    k = p.scheme(k.id)
    warnings = se.warnings(p, k)
    assert any("has no structure yet" in w for w in warnings)
    assert any("structure in the conditions" in w for w in warnings)
    assert se.box_view(p, k, se.starting(k)[0]).empty
    assert se.box_view(p, k, se.starting(k)[1]).mol is not None
    assert se.path_of(k, [1]) is None and se.path_of(k, 5) is None
    se.set_box_structure(p, k, product["id"], "CC=O")
    se.link_new_compound(p, k, product["id"])
    dup.duplicate_scheme(p, k, {"name"})
    entry = se.remove_box(p, k, se.starting(k)[1]["id"])
    assert entry is not None and set(entry.info["files"]) == {"structure"}


# ---------------------------------------------------------------------- yields under the conditions

def test_conditions_yield_parts_match_what_prints(tmp_path):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    step = se.steps(k)[0]
    other = se.add_box(p, k, step["id"])
    se.set_box_structure(p, k, other["id"], "CCO")
    c2 = se.link_new_compound(p, k, other["id"])
    c.values["yield"] = {"percent": "92"}
    p.save_compound(c)
    # Two products, only one with a yield: just "92%".
    assert se.conditions_yield_parts(p, k, se.steps(k)[0]) == [("92%", "")]
    c2.values["yield"] = {"percent": "5"}
    p.save_compound(c2)
    assert se.conditions_yield_parts(p, k, se.steps(k)[0]) == \
        [("92%", "@{" + c.id + "}"), ("5%", "@{" + c2.id + "}")]
    # Products that are not compounds: their typed ID labels.
    k2 = p.add_scheme(data.id, name="Side")
    s2 = se.steps(k2)[0]
    a = s2["products"][0]
    b = se.add_box(p, k2, s2["id"])
    for box, label, y in ((a, "S1", "60%"), (b, "S2", "30%")):
        se.set_box_label(p, k2, box["id"], label)
        se.set_box_yield(p, k2, box["id"], y)
    assert se.conditions_yield_parts(p, k2, se.steps(k2)[0]) == [("60%", "S1"), ("30%", "S2")]
    assert se.conditions_yield_parts(p, k2, {"products": []}) == []


# ---------------------------------------------------------------------- the scheme page (Qt)

@pytest.fixture
def page(qapp, tmp_path):
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    panels = []

    def make(select=None):
        panel = SchemePanel(p, k, lambda: "Tester", select_compound=select)
        panel.resize(1100, 800)
        panels.append(panel)
        return panel
    yield p, data, k, make
    for panel in panels:
        panel.flush()
        panel.close()
        panel.deleteLater()
    qapp.processEvents()
    p.close()


def type_name(panel, text):
    """Type a new name on the compound tab (its autosave then waits)."""
    box = next(b for b in panel.compound_panel.boxes if b.defn.key == "name")
    widget = box.editor.widgets["text"]
    widget.setFocus()
    widget.selectAll()
    QTest.keyClicks(widget, text)


def test_typing_on_a_compound_tab_is_saved_before_a_scheme_change(page, qapp):
    p, data, k, make = page
    step2 = se.add_step(p, k)
    se.set_box_structure(p, k, step2["products"][0]["id"], "CCO")
    later = se.link_new_compound(p, k, step2["products"][0]["id"], name="first")
    first_product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, first_product, "CC(O)c1ccccc1")
    panel = make()
    assert panel.current_compound_id() == later.id
    type_name(panel, "first TYPED")
    assert panel.compound_panel._timer.isActive()
    # Marking the earlier product puts the new compound on the first tab, at once.
    panel.editor.mark_compound(first_product)
    qapp.processEvents()
    assert Project.open(p.folder, read_only=True).compound(later.id).name == "first TYPED"
    assert len(k.children) == 2
    p.undo()                                           # only the marking is undone
    again = Project.open(p.folder, read_only=True)
    assert again.compound(later.id).name == "first TYPED"
    assert len(again.scheme(k.id).children) == 1


def test_the_compound_page_is_built_once(page, monkeypatch, qapp):
    from si_assistant.ui import scheme_panel
    p, data, k, make = page
    made = []
    for smiles in ("CCO", "CCN", "CCC"):
        box = se.add_box(p, k, se.steps(k)[0]["id"])
        se.set_box_structure(p, k, box["id"], smiles)
        made.append(se.link_new_compound(p, k, box["id"]))
    built, real = [], scheme_panel.CompoundPanel

    def counting(*a, **kw):
        panel = real(*a, **kw)
        built.append(panel.compound.id)
        return panel
    monkeypatch.setattr(scheme_panel, "CompoundPanel", counting)
    panel = make(select=made[2].id)
    assert built == [made[2].id] and panel.current_compound_id() == made[2].id
    selected = []
    panel.compound_selected.connect(selected.append)
    built.clear()
    panel._editor_compounds_changed("")                # (the editor changed the compounds)
    assert built == [made[2].id] and selected == [made[2].id]
    built.clear()
    panel.select_compound(made[0].id)                  # a click on another tab
    assert built == [made[0].id]
    built.clear()
    panel.add_compound()
    assert len(built) == 1 and panel.current_compound_id() == built[0]


def test_typing_kept_but_a_deleted_compound_not_saved_again(page, qapp):
    p, data, k, make = page
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    c = se.link_new_compound(p, k, product, name="gone")
    panel = make()
    type_name(panel, "gone TYPED")
    assert panel.compound_panel._timer.isActive()
    p.delete_compound(c.id)                            # (removed while typing waits)
    panel._fill_tabs()
    qapp.processEvents()
    assert not c.folder.exists()                       # not re-created by a late save


def held_compound_file(monkeypatch, p, cid):
    """compound.json of one compound can't be written (held, e.g. by OneDrive)."""
    real = p.save_compound

    def save(compound, *a, **kw):
        if compound.id == cid:
            raise PermissionError(13, "held by another program")
        return real(compound, *a, **kw)
    monkeypatch.setattr(p, "save_compound", save)


def test_a_scheme_change_waits_while_the_compound_tab_cant_be_saved(page, monkeypatch,
                                                                     qapp):
    p, data, k, make = page
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    c = se.link_new_compound(p, k, product, name="held")
    panel = make()
    type_name(panel, "held TYPED")
    held_compound_file(monkeypatch, p, c.id)
    before = box_ids(k)
    panel.editor.add_box(se.START)                     # no crash; nothing changed
    assert box_ids(k) == before
    assert "could not be saved" in panel.editor.status.text()
    assert panel.compound_panel._dirty                 # the typing still waits
    monkeypatch.undo()                                 # the file is free again
    panel.editor.add_box(se.START)
    assert len(box_ids(k)) == len(before) + 1
    assert Project.open(p.folder, read_only=True).compound(c.id).name == "held TYPED"


def test_compound_tabs_rebuilt_keep_typing_that_cant_be_saved(page, monkeypatch, qapp):
    p, data, k, make = page
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    c = se.link_new_compound(p, k, product, name="held")
    other = se.add_box(p, k, se.steps(k)[0]["id"])
    se.set_box_structure(p, k, other["id"], "CCN")
    panel = make(select=c.id)
    type_name(panel, "held TYPED")
    held_compound_file(monkeypatch, p, c.id)
    shown = panel.compound_panel
    messages = []
    panel.saved.connect(messages.append)
    se.link_new_compound(p, k, other["id"], name="new")
    panel._editor_compounds_changed("")                # no crash; the page is kept
    assert panel.compound_panel is shown and panel.current_compound_id() == c.id
    assert panel.tabs.count() == 2
    assert any(m.startswith("Could not save") for m in messages)
    monkeypatch.undo()
    panel.flush()
    assert Project.open(p.folder, read_only=True).compound(c.id).name == "held TYPED"


def test_add_and_delete_compound_tabs_are_named_undo_steps(page, monkeypatch, qapp):
    p, data, k, make = page
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    first = se.link_new_compound(p, k, product, name="first")
    panel = make()
    panel.add_compound()
    assert p.journal.undo_label == "New compound"
    new = panel.current_compound_id()
    assert p.journal.undo_stack[-1].where[0] == ("compound", new)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    shown = p.id_report().display_id(first.id)
    panel._close_tab(next(i for i in range(panel.tabs.count())
                          if panel.tabs.tabData(i) == first.id))
    assert p.journal.undo_label == f"Delete compound {shown}"
    assert p.journal.undo_stack[-1].where[0] == ("compound", first.id)
    assert len(p.list_trash()) == 1
    p.undo()
    assert len(p.list_trash()) == 0 and p.compound(first.id).name == "first"


# ---------------------------------------------------------------------- the scheme editor (Qt)

def sm_widget(panel):
    return next(b for b in panel.editor.boxes if b.where == se.START)


def test_id_label_shows_and_saves_what_was_typed(page, qapp):
    p, data, k, make = page
    panel = make()
    sm = sm_widget(panel)
    sm.label_edit.setText("1_a*")
    sm._label_typed()
    stored = se.starting(k)[0]["label"]
    assert stored == "1\\_a\\*"
    sm = sm_widget(panel)
    assert sm.label_edit.text() == "1_a*" and sm.canvas.label == "1_a*"
    sm.label_edit.setText(sm.label_edit.text() + "b")
    sm._label_typed()
    assert se.starting(k)[0]["label"] == "1\\_a\\*b"
    sm = sm_widget(panel)
    assert sm.label_edit.text() == "1_a*b" and sm.canvas.label == "1_a*b"
    # A label with formatting (hand-edited): leaving the field unchanged keeps it.
    se.set_box_label(p, k, se.starting(k)[0]["id"], "S_{3}**x**")
    panel.editor.rebuild()
    sm = sm_widget(panel)
    steps = len(p.journal.undo_stack)
    sm._label_typed()
    assert se.starting(k)[0]["label"] == "S_{3}**x**"
    assert len(p.journal.undo_stack) == steps


def test_a_plain_click_on_an_id_only_selects_the_box(page, qapp):
    p, data, k, make = page
    se.set_box_label(p, k, se.starting(k)[0]["id"], "S1")
    panel = make()
    panel.show()
    qapp.processEvents()
    sm = sm_widget(panel)
    path = k.folder / "scheme.json"
    before = path.stat().st_mtime_ns
    moved = []
    sm.canvas.label_moved.connect(lambda *a: moved.append(a))
    QTest.mouseClick(sm.canvas, Qt.MouseButton.LeftButton,
                     pos=sm.canvas.label_rect().center().toPoint())
    qapp.processEvents()
    assert moved == []
    assert sm_widget(panel) is sm                      # (not rebuilt)
    assert path.stat().st_mtime_ns == before


def test_yields_note_under_the_conditions_is_what_prints(page, qapp):
    p, data, k, make = page
    step = se.steps(k)[0]
    a = step["products"][0]
    se.set_box_structure(p, k, a["id"], "CCO")
    ca = se.link_new_compound(p, k, a["id"])
    b = se.add_box(p, k, step["id"])
    se.set_box_structure(p, k, b["id"], "CCN")
    se.link_new_compound(p, k, b["id"])
    se.set_scheme_layout(p, k, {"yields": "conditions"})
    ca.values["yield"] = {"percent": "92"}
    p.save_compound(ca)
    panel = make()
    note = panel.editor.steps[0].yields_note
    assert note.text() == "92%"                         # (only one product has a yield)
    # Products that are not compounds, with typed ID labels and yields.
    k2 = p.add_scheme(data.id, name="Side")
    s2 = se.steps(k2)[0]
    x, y = s2["products"][0], se.add_box(p, k2, s2["id"])
    for box, label, value in ((x, "S1", "60%"), (y, "S2", "30%")):
        se.set_box_structure(p, k2, box["id"], "CCO")
        se.set_box_label(p, k2, box["id"], label)
        se.set_box_yield(p, k2, box["id"], value)
    se.set_scheme_layout(p, k2, {"yields": "conditions"})
    from si_assistant.ui.scheme_editor_ui import SchemeEditorWidget
    editor = SchemeEditorWidget(p, k2)
    try:
        assert editor.steps[0].yields_note.text() == "60% (S1), 30% (S2)"
    finally:
        editor.deleteLater()


# ---------------------------------------------------------------------- handoffs from other groups

def test_a_generic_box_structure_can_become_a_compound_page(tmp_path):
    # (Nathan's decision: a generic structure is accepted for a compound, with a warning)
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="General")
    box = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, box, "*c1ccccc1C(=O)C")      # (R groups: fine in a scheme)
    compound = se.link_new_compound(p, k, box)
    assert [c.id for c in p.scheme(k.id).children] == [compound.id]
    found = se.find_box(k, box)[0]
    assert found.get("compound") == compound.id
    assert p.generic_structure_atoms(compound) == ["R"]
    assert se.box_view(p, k, found).mol is not None          # (still drawn in the box)


def test_an_unreadable_compound_structure_is_not_written_over(tmp_path, monkeypatch):
    from si_assistant.core import project as project_module
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    box = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, box, "CCN")
    c = p.add_compound(data.id, 0, name="amine", scheme_id=k.id)
    p.set_structure(c, "CCO")
    before = p.structure_path(c).read_bytes()
    real = project_module.read_text

    def offline(path, *a, **kw):            # (an online-only OneDrive file, offline)
        if path.name == "structure.mol":
            raise OSError("The cloud file provider is not running.")
        return real(path, *a, **kw)
    monkeypatch.setattr(project_module, "read_text", offline)
    p._structure_cache.clear()
    with pytest.raises(OSError):
        se.link_compound(p, k, box, c.id)
    monkeypatch.undo()
    assert p.structure_path(c).read_bytes() == before
    assert not se.find_box(k, box)[0].get("compound")


@pytest.mark.parametrize("value, kept", [("abc", None), (True, None), (3.5, 3.5),
                                         ("4", 4.0), (0, 0.0)])
def test_a_hand_edited_box_bond_length_is_read_as_printing_reads_it(tmp_path, value, kept):
    from si_assistant.core import drawing_scale as ds
    from tests import structure_samples as ss
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    box = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, box, ss.structure_molblock(), chemdraw_picture())
    se.find_box(k, box)[0][ds.OVERRIDE_KEY] = value       # (scheme.json edited by hand)
    c = se.link_new_compound(p, k, box)
    assert p.structure_picture_bond(c)[1] == kept


@pytest.fixture
def scale_dialog(monkeypatch):
    """The Printed size dialog, replaced: the values it is shown, and Cancel."""
    from si_assistant.ui import scheme_editor_ui as ui
    shown = []

    class FakeDialog:
        def __init__(self, *a, bond=None, **kw):
            shown.append(bond)

        def exec(self):
            return ui.QDialog.DialogCode.Rejected
    monkeypatch.setattr(ui, "PictureScaleDialog", FakeDialog)
    return shown


@pytest.mark.parametrize("value, own", [("abc", None), (True, None), ("4", 4.0),
                                        (0, 0.0), (1e6, "most")])
def test_printed_size_dialog_shows_the_typed_value_as_printing_reads_it(
        page, qapp, scale_dialog, value, own):
    from si_assistant.core import drawing_scale as ds
    from tests import structure_samples as ss
    p, data, k, make = page
    sm = se.starting(k)[0]
    se.set_box_structure(p, k, sm["id"], ss.structure_molblock(), chemdraw_picture())
    sid = se.add_inline_structure(p, k, ss.structure_molblock(), chemdraw_picture())
    se.set_conditions(p, k, se.steps(k)[0]["id"], "top", f"NaH @struct{{{sid}}}")
    se.starting(k)[0][ds.OVERRIDE_KEY] = value            # (scheme.json edited by hand)
    se.inline_entry(k, sid)[ds.OVERRIDE_KEY] = value
    panel = make()
    sm_widget(panel).picture_size()
    panel.editor.inline.picture_size(sid)
    own = ds.TYPED_RANGE[1] if own == "most" else own   # (kept within the dialog's limits)
    assert [bond[1] for bond in scale_dialog] == [own, own]


def test_ampersands_in_compound_names_show_in_tabs_and_menus(page, qapp):
    from PySide6.QtWidgets import QMenu
    p, data, k, make = page
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    linked = se.link_new_compound(p, k, product, name="R&D")
    free = p.add_compound(data.id, 1, name="A&B", scheme_id=k.id)
    other = se.add_box(p, k, se.steps(k)[0]["id"])
    panel = make()
    report = p.id_report()
    texts = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    assert f"{report.display_id(linked.id)}  R&&D" in texts
    linked.values["name"] = {"text": "R&D 2"}
    p.save_compound(linked)
    panel._refresh_tab_texts()
    texts = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    assert f"{report.display_id(linked.id)}  R&&D 2" in texts
    assert [panel.tabs.tabData(i) for i in range(panel.tabs.count())] == [linked.id, free.id]
    widget = next(b for b in panel.editor.boxes if b.box_id == other["id"])
    menu = QMenu()
    widget.fill_menu(menu)
    sub = next(a.menu() for a in menu.actions()
               if a.text() == "Show a compound of this page")
    assert [a.text() for a in sub.actions()] == [f"{report.display_id(free.id)}  A&&B"]
    sub.actions()[0].trigger()                            # (the raw ID is linked)
    assert se.find_box(p.scheme(k.id), other["id"])[0]["compound"] == free.id
    menu.deleteLater()


def test_duplicate_dialog_shows_an_ampersand_in_a_field_label(qapp, tmp_path):
    from si_assistant.ui.duplicate_ui import DuplicateDialog
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="ester")
    p.add_field(c, {"key": "custom-ye", "custom": {"label": "Yield & ee",
                                                   "type": "short_text"}})
    dialog = DuplicateDialog(p, c)
    assert dialog.boxes["custom-ye"].text() == "Yield && ee"
    dialog.deleteLater()


def test_pasted_conditions_with_structures_are_one_undo_step(page, qapp):
    from si_assistant.core.undo import UndoContext
    from si_assistant.ui.scheme_editor_ui import SchemeEditorWidget
    from tests import structure_samples as ss
    p, data, a, make = page
    se.set_box_structure(p, a, se.steps(a)[0]["products"][0]["id"], "CC=O")
    s1 = se.add_inline_structure(p, a, ss.structure_molblock(), chemdraw_picture())
    s2 = se.add_inline_structure(p, a, ss.structure_molblock(), chemdraw_picture(2.0))
    se.set_conditions(p, a, se.steps(a)[0]["id"], "top",
                      f"NaH @struct{{{s1}}} then @struct{{{s2}}}")
    b = p.add_scheme(data.id, name="Other")
    se.set_conditions(p, b, se.steps(b)[0]["id"], "top", "THF")
    # (the page on show, as the main window says it: the text's autosave can join a step)
    p.journal.context = lambda: UndoContext(("scheme", b.id), "Typing", [("scheme", b.id)])
    wa, wb = SchemeEditorWidget(p, a), SchemeEditorWidget(p, b)
    try:
        ea = wa.steps[0].top.text.edit
        ea.selectAll()
        copied = ea.createMimeDataFromSelection()
        steps = len(p.journal.undo_stack)
        eb = wb.steps[0].top.text.edit
        eb.selectAll()
        eb.insertFromMimeData(copied)
        wb.flush()
        assert len(p.journal.undo_stack) == steps + 1
        assert p.journal.undo_label == "Paste conditions with structures"
        b = p.scheme(b.id)
        assert len(se.editor_data(b)["inline"]) == 2
        p.undo()
        b = p.scheme(b.id)
        assert se.steps(b)[0]["top"] == "THF" and se.editor_data(b)["inline"] == {}
        folder = b.folder / se.FILES_DIR
        assert not folder.exists() or not list(folder.glob("s-*"))
    finally:
        wa.deleteLater()
        wb.deleteLater()


def test_a_locked_scheme_saves_no_conditions(page, qapp):
    p, data, k, make = page
    se.set_conditions(p, k, se.steps(k)[0]["id"], "top", "NaH")
    panel = make()
    step = panel.editor.steps[0]
    panel.editor.locked = True                            # (e.g. locked in the meantime)
    step.top.text.set_markup("changed")
    step.save(step.top)
    assert se.steps(p.scheme(k.id))[0]["top"] == "NaH"


# ---------------------------------------------------------------------- late handoffs

def test_duplicated_compounds_save_the_section_once_and_match_on_disk(tmp_path,
                                                                      monkeypatch):
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="alcohol")
    real, saves = p.save_section, []

    def save_section(section, *a, **kw):
        saves.append(section.id)
        return real(section, *a, **kw)
    monkeypatch.setattr(p, "save_section", save_section)
    made = dup.duplicate_compound(p, c, {"name"}, copies=3)
    assert len(made) == 3 and saves == [data.id]          # section.json written once
    ids = [x.id for x in p.section(data.id).items]
    assert [x.id for x in Project.open(p.folder, read_only=True).section(data.id).items] \
        == ids
    # A copy failing half-way: the copies made are still listed by section.json.
    real_fill, calls = dup._fill_copy, []

    def failing(project, original, new, keys):
        calls.append(new.id)
        if len(calls) == 2:
            raise OSError("disk full")
        real_fill(project, original, new, keys)
    monkeypatch.setattr(dup, "_fill_copy", failing)
    with pytest.raises(OSError):
        dup.duplicate_compound(p, c, {"name"}, copies=3)
    reopened = Project.open(p.folder, read_only=True)
    on_disk = [x.id for x in reopened.section(data.id).items]
    assert on_disk == [x.id for x in p.section(data.id).items]
    assert set(calls) <= set(on_disk) and len(on_disk) == len(ids) + 2


def two_linked_compounds(p, k):
    product = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, product, "CCO")
    c = se.link_new_compound(p, k, product, name="held")
    other = se.add_box(p, k, se.steps(k)[0]["id"])
    se.set_box_structure(p, k, other["id"], "CCN")
    d = se.link_new_compound(p, k, other["id"], name="other")
    return c, d


def tab_of(panel, cid) -> int:
    return next(i for i in range(panel.tabs.count()) if panel.tabs.tabData(i) == cid)


def test_a_tab_change_keeps_a_compound_tab_that_cant_be_saved(page, monkeypatch, qapp):
    import sys
    p, data, k, make = page
    c, d = two_linked_compounds(p, k)
    panel = make(select=c.id)
    type_name(panel, "held TYPED")
    held_compound_file(monkeypatch, p, c.id)
    panel.compound_panel._timer.timeout.emit()            # the autosave fails
    errors, messages = [], []
    panel.saved.connect(messages.append)
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    panel.tabs.setCurrentIndex(tab_of(panel, d.id))
    qapp.processEvents()
    assert not errors
    assert panel.current_compound_id() == panel.compound_panel.compound.id == c.id
    assert panel.compound_panel._dirty                    # the typing still waits
    assert any(m.startswith("Could not save") for m in messages)
    monkeypatch.undo()                                    # the file is free again
    panel.flush()
    assert Project.open(p.folder, read_only=True).compound(c.id).name == "held TYPED"
    panel.tabs.setCurrentIndex(tab_of(panel, d.id))       # now the tab changes
    assert panel.compound_panel.compound.id == d.id


def test_add_and_delete_compound_tabs_while_the_page_cant_be_saved(page, monkeypatch, qapp):
    p, data, k, make = page
    c, d = two_linked_compounds(p, k)
    panel = make(select=c.id)
    type_name(panel, "held TYPED")
    held_compound_file(monkeypatch, p, c.id)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: warnings.append(a[1]))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    # Adding a compound goes on; the page with the typing stays on show.
    panel.add_compound()
    assert warnings == ["Could not save"] and len(k.children) == 3
    assert panel.compound_panel.compound.id == c.id and panel.compound_panel._dirty
    # Deleting another compound: nothing deleted (closing the page would lose the typing).
    panel._close_tab(tab_of(panel, d.id))
    assert len(warnings) == 2 and len(p.list_trash()) == 0
    assert panel.compound_panel.compound.id == c.id and panel.compound_panel._dirty
    # Deleting the compound on show itself goes on (its typing goes with it).
    panel._close_tab(tab_of(panel, c.id))
    assert len(p.list_trash()) == 1 and c.id not in [x.id for x in k.children]
    monkeypatch.undo()
    p.undo()
    assert p.compound(c.id).name == "held"


# ---------------------------------------------------------------------- review 2

def counted_held_compound_file(monkeypatch, p, cid) -> list:
    """As held_compound_file; the list counts the tries to save it."""
    real, tries = p.save_compound, []

    def save(compound, *a, **kw):
        if compound.id == cid:
            tries.append(compound.id)
            raise PermissionError(13, "held by another program")
        return real(compound, *a, **kw)
    monkeypatch.setattr(p, "save_compound", save)
    return tries


def box_widget(panel, box_id):
    return next(b for b in panel.editor.boxes if b.box_id == box_id)


def test_a_blocked_scheme_change_shows_the_scheme_as_it_is(page, monkeypatch, qapp):
    p, data, k, make = page
    c, d = two_linked_compounds(p, k)
    third = se.add_box(p, k, se.steps(k)[0]["id"])
    se.set_box_structure(p, k, third["id"], "CCC")
    panel = make(select=c.id)
    type_name(panel, "held TYPED")
    tries = counted_held_compound_file(monkeypatch, p, c.id)
    linked = next(b["id"] for b, _w in se.all_boxes(k) if b.get("compound") == d.id)
    # Ticking "Compound": the tick goes back, no compound is made, tried once.
    box_widget(panel, third["id"]).compound_box.setChecked(True)
    qapp.processEvents()
    assert tries == [c.id]
    assert not box_widget(panel, third["id"]).compound_box.isChecked()
    assert se.find_box(k, third["id"])[0].get("compound") is None and len(k.children) == 2
    assert "could not be saved" in panel.editor.status.text()
    assert panel.compound_panel.compound.id == c.id and panel.compound_panel._dirty
    # Unticking a linked box: the tick comes back, it stays linked.
    monkeypatch.setattr(panel.editor, "_ask_compound", lambda *a: "keep")
    box_widget(panel, linked).compound_box.setChecked(False)
    qapp.processEvents()
    assert box_widget(panel, linked).compound_box.isChecked()
    assert se.find_box(k, linked)[0].get("compound") == d.id
    # A dragged ID goes back to where it is saved.
    box_widget(panel, third["id"]).set_offset(12.0, 7.0)
    qapp.processEvents()
    assert se.label_offset(se.find_box(k, third["id"])[0]) == (0.0, 0.0)
    assert box_widget(panel, third["id"]).canvas.offset.x() == 0.0
    monkeypatch.undo()                                     # the file is free again
    box_widget(panel, third["id"]).compound_box.setChecked(True)
    qapp.processEvents()
    assert len(k.children) == 3
    assert Project.open(p.folder, read_only=True).compound(c.id).name == "held TYPED"


def held_scheme_file(monkeypatch, p, kid):
    """scheme.json of one scheme can't be written (held, e.g. by OneDrive)."""
    real = p.save_scheme

    def save(scheme, *a, **kw):
        if scheme.id == kid:
            raise PermissionError(13, "scheme.json held")
        return real(scheme, *a, **kw)
    monkeypatch.setattr(p, "save_scheme", save)


def test_a_held_scheme_file_stops_add_and_delete_and_says_so(page, monkeypatch, qapp):
    import sys
    p, data, k, make = page
    c, d = two_linked_compounds(p, k)
    panel = make(select=c.id)
    warnings, messages, errors = [], [], []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: warnings.append((a[1], a[2])))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    panel.saved.connect(messages.append)
    held_scheme_file(monkeypatch, p, k.id)
    panel.name_edit.setText("Renamed")                     # waiting to be saved
    folders = sorted(x.name for x in k.folder.iterdir())
    # Deleting the compound on show: nothing deleted, the scheme page is named as the cause.
    panel._close_tab(tab_of(panel, c.id))
    assert len(warnings) == 1 and "scheme page" in warnings[0][1]
    assert "compound page" not in warnings[0][1]
    assert c.folder.exists() and len(p.list_trash()) == 0
    assert [x.id for x in k.children] == [c.id, d.id]
    # Adding one: nothing added, in memory or on disk.
    panel.add_compound()
    assert len(warnings) == 2 and "scheme page" in warnings[1][1]
    assert [x.id for x in k.children] == [c.id, d.id] and panel.tabs.count() == 2
    assert sorted(x.name for x in k.folder.iterdir()) == folders
    # A tab change still happens (the compound page saved by itself), with a message
    # about the scheme page.
    panel.tabs.setCurrentIndex(tab_of(panel, d.id))
    qapp.processEvents()
    assert panel.compound_panel.compound.id == d.id and not errors
    assert any("scheme page" in m for m in messages)
    # The name box left: a warning, not an error.
    panel._commit_name()
    assert not errors and warnings[-1][0] == "Could not save the name"
    monkeypatch.undo()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: None)
    panel.flush()
    assert Project.open(p.folder, read_only=True).scheme(k.id).name == "Renamed"


def test_add_and_close_try_a_held_compound_page_once(page, monkeypatch, qapp):
    p, data, k, make = page
    c, d = two_linked_compounds(p, k)
    panel = make(select=c.id)
    type_name(panel, "held TYPED")
    tries = counted_held_compound_file(monkeypatch, p, c.id)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: None)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    panel.add_compound()
    assert tries == [c.id] and len(k.children) == 3
    assert panel.compound_panel.compound.id == c.id and panel.compound_panel._dirty
    tries.clear()
    real = p.delete_compound

    def held_delete(cid):
        raise PermissionError(13, "held by another program")
    monkeypatch.setattr(p, "delete_compound", held_delete)
    panel._close_tab(tab_of(panel, c.id))                  # the delete fails
    assert tries == [c.id] and panel.compound_panel.compound.id == c.id
    assert panel.compound_panel._dirty
    monkeypatch.setattr(p, "delete_compound", real)
    monkeypatch.undo()                                     # the file is free again
    panel.flush()
    assert Project.open(p.folder, read_only=True).compound(c.id).name == "held TYPED"


def test_a_duplicate_whose_files_cant_be_copied_links_nothing_of_the_original(
        tmp_path, monkeypatch):
    p, data = new_project(tmp_path)
    k, product, c = linked_scheme(p, data)
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    assert any((k.folder / se.FILES_DIR).iterdir())

    def held(*a, **kw):
        raise PermissionError(13, "held by another program")
    monkeypatch.setattr(fileio, "copy_file", held)
    with pytest.raises(PermissionError):
        dup.duplicate_scheme(p, k, {"name"})
    monkeypatch.undo()
    copy = next(x for x in p.section(data.id).schemes if x.id != k.id)
    in_memory = [b.get("compound") for b, _w in se.all_boxes(copy)]
    assert c.id not in in_memory
    p.rename_scheme(copy.id, "Route copy")                 # saved again for another reason
    reopened = Project.open(p.folder, read_only=True).scheme(copy.id)
    assert c.id not in [b.get("compound") for b, _w in se.all_boxes(reopened)]
    assert saved_editor(copy) == se.editor_data(copy)


# ---------------------------------------------------------------------- late handoffs, round 2

def test_a_linked_compound_with_an_r_group_is_drawn_in_its_box(tmp_path):
    from rdkit import Chem
    from si_assistant.core import chem
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    box = se.steps(k)[0]["products"][0]["id"]
    c = p.add_compound(data.id, 0, name="aryl R", scheme_id=k.id)
    se.link_compound(p, k, box, c.id)
    # (an R group: no formula or mass, but still a drawing, e.g. from an older version)
    p.structure_path(c).write_text(chem.mol_to_block(Chem.MolFromSmiles("*c1ccccc1")),
                                   encoding="utf-8")
    assert p.read_structure(c) is None
    view = se.box_view(p, k, se.find_box(k, box)[0])
    assert view.compound is not None and view.compound.id == c.id
    assert not view.empty and view.mol.GetNumAtoms() == 7


def test_linking_keeps_a_compound_structure_with_an_r_group(tmp_path):
    from rdkit import Chem
    from si_assistant.core import chem
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    box = se.steps(k)[0]["products"][0]["id"]
    se.set_box_structure(p, k, box, "CC(=O)c1ccccc1")
    c = p.add_compound(data.id, 0, name="aryl R", scheme_id=k.id)
    old = chem.mol_to_block(Chem.MolFromSmiles("*c1ccccc1"))   # (from an older version)
    p.structure_path(c).write_text(old, encoding="utf-8")
    assert p.read_structure(c) is None
    se.link_compound(p, k, box, c.id)
    # The compound keeps its own structure (the box shows it); nothing goes to the Trash.
    assert p.structure_path(c).read_text(encoding="utf-8") == old
    assert p.list_trash() == []
    view = se.box_view(p, k, se.find_box(k, box)[0])
    assert view.compound.id == c.id and view.mol.GetNumAtoms() == 7
