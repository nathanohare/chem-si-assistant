"""Collapsing, 'saved' (compact) structure and scheme boxes, and the compact dashboard."""

from si_assistant.core.project import Project


def new_project(tmp_path):
    p = Project.create(tmp_path, "L")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b"])
    return p, data


def test_structure_box_compacts_once_a_structure_is_in(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.structure_widgets import StructureEditor
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    ed = next(b.editor for b in panel.boxes if isinstance(b.editor, StructureEditor))
    assert not ed.controls.isHidden() and ed.edit_btn.isHidden()  # empty: all buttons shown
    ed.smiles.setText("CCO")
    ed._apply_smiles()
    assert ed.controls.isHidden() and not ed.edit_btn.isHidden()
    assert ed._paste_locked and ed.smiles.isReadOnly() and ed.paste_hint.isHidden()
    ed.set_editing(True)                      # "Show all editing controls"
    assert not ed.controls.isHidden() and not ed.done_btn.isHidden() and not ed._paste_locked
    ed.set_editing(False)
    assert ed.controls.isHidden()
    panel.flush()
    p.close()


def test_collapse_fields_and_collapse_all(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    c.values["yield"] = {"percent": "92", "mass": "28.4", "mass_unit": "mg"}
    p.save_compound(c)
    panel = CompoundPanel(p, c, lambda: "Tester")
    box = next(b for b in panel.boxes if b.defn.key == "yield")
    box.set_collapsed(True)
    assert box.editor.isHidden() and box.summary.text() == "✔ 92% · 28.4 mg"
    # The next compound opens with the same field collapsed.
    other = CompoundPanel(p, p.add_compound(data.id), lambda: "Tester")
    assert next(b for b in other.boxes if b.defn.key == "yield").collapsed
    panel.set_all_collapsed(True)
    assert all(b.collapsed for b in panel.boxes)
    panel.set_all_collapsed(False)
    assert not any(b.collapsed for b in panel.boxes)
    panel.focus_field("yield")
    assert not box.collapsed
    panel.flush()
    other.flush()
    p.close()


def test_scheme_collapse_and_save(qapp, tmp_path):
    from si_assistant.ui.scheme_panel import SchemePanel
    p, data = new_project(tmp_path)
    scheme = p.add_scheme(data.id)
    p.add_compound(data.id, scheme_id=scheme.id)
    p.set_scheme_picture(scheme, data=b"\x89PNG")
    panel = SchemePanel(p, scheme, lambda: "Tester", collapsed=True)
    assert panel.picture_block.isHidden() and panel.collapse_btn.text() == "▸ Scheme"
    panel.set_collapsed(False)
    assert not panel.picture_block.isHidden()
    assert not panel.save_btn.isHidden() and panel.edit_btn.isHidden()
    panel.set_scheme_saved(True)
    assert panel.save_btn.isHidden() and not panel.edit_btn.isHidden()
    assert all(b.isHidden() for b in panel.buttons.values())
    assert not panel.canvas.editable and panel._paste_locked
    assert Project.open(p.folder).scheme(scheme.id).data["saved"] is True
    panel.set_scheme_saved(False)
    assert panel.canvas.editable and not panel._paste_locked
    panel.flush()
    p.close()


def test_compact_dashboard(qapp, tmp_path):
    from si_assistant.ui.dashboard_view import DashboardView
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id, name="Ethanol")
    p.set_structure(c, "CCO")
    c.values["yield"] = {"percent": "92", "mass": "28.4", "mass_unit": "mg"}
    p.save_compound(c)
    view = DashboardView(p, data)
    view.refresh()
    headers = [view.table.horizontalHeaderItem(i).text() for i in range(view.table.columnCount())]
    yield_col = headers.index("Yield")
    assert view.table.item(0, yield_col).text() == "✔  92% · 28.4 mg"
    view.compact_box.setChecked(True)
    assert view.table.isColumnHidden(1)                     # no thumbnails
    assert view.table.item(0, yield_col).text() == "✔"      # symbol only
    assert "<img" in view.tooltip_for(view.table.item(0, 0))  # structure on hover
    assert "<img" in view.tooltip_for(view.table.item(0, 2))  # (drawn when first hovered)
    assert Project.open(p.folder).section(data.id).data["dashboard"]["compact"] is True
    p.close()
