"""General procedures, the References field and the References dialog in the UI."""

import pytest

from si_assistant.core import references as R
from si_assistant.core.project import Project
from si_assistant.ui import rich_text


@pytest.fixture
def setup(tmp_path):
    p = Project.create(tmp_path, "P7")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["1a", "3a"])
    sm = p.add_compound(data.id, name="Amide")
    c = p.add_compound(data.id, name="Enamide")
    rich_text.set_link_context(rich_text.LinkContext(p))
    yield p, data, sm, c
    rich_text.set_link_context(None)
    p.close()


def test_general_procedures_tab(qapp, setup):
    from si_assistant.ui.data_panel import DataSectionPanel
    p, data, sm, c = setup
    panel = DataSectionPanel(p, data)
    gps = panel.procedures
    assert not gps.right.isEnabled()          # nothing to edit yet
    gps._add()
    assert gps.list.count() == 1 and gps.name.text() == "General Procedure A"
    gps.title.setText("Synthesis of Enamides")
    gps._edited()
    gps.text.set_markup(f"To a vial was added @{{{sm.id}}} (0.20 mmol).")
    gps._edited()
    panel.flush()
    reopened = Project.open(p.folder)
    gp = reopened.general_procedures(reopened.section(data.id))[0]
    assert gp["title"] == "Synthesis of Enamides"
    assert reopened.read_gp_text(gp["id"]) == f"To a vial was added @{{{sm.id}}} (0.20 mmol)."


def test_procedure_field_uses_a_gp(qapp, setup):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data, sm, c = setup
    gp = p.add_general_procedure(data.id)
    c.values["scale"] = {"value": "0.20", "unit": "mmol"}
    p.save_compound(c)
    panel = CompoundPanel(p, c, lambda: "Tester")
    box = next(b for b in panel.boxes if b.defn.key == "procedure")
    chooser = box.editor.gp
    chooser.combo.setCurrentIndex(chooser.combo.findData(gp["id"]))
    assert list(chooser.value_widgets) == ["GP", "starting material", "scale"]
    assert chooser.value_widgets["scale"].placeholderText() == "automatic: 0.20 mmol"
    chooser.value_widgets["starting material"].set_markup(f"@{{{sm.id}}}")
    chooser._value_edited()
    assert "Prepared according to General Procedure A from <b>1a</b> (0.20 mmol)." in \
        chooser.preview.text()
    panel.flush()
    saved = Project.open(p.folder).compound(c.id)
    assert saved.values["procedure"] == {"gp": gp["id"],
                                         "values": {"starting material": f"@{{{sm.id}}}"}}
    # Typing in the {scale} box: the filled Reaction scale field is still what is printed
    # (SPEC §13n.9), and a warning says the two differ.
    panel = CompoundPanel(p, c, lambda: "Tester")
    chooser = next(b for b in panel.boxes if b.defn.key == "procedure").editor.gp
    chooser.value_widgets["scale"].setText("0.5 mmol of 1a")
    chooser._value_edited()
    assert "(0.20 mmol)" in chooser.preview.text()
    panel.flush()
    box = next(b for b in panel.boxes if b.defn.key == "procedure")
    assert any("Reaction scale field says 0.20 mmol" in w.message for w in box.warnings)
    # Without a Reaction scale field, what is typed is printed (§2.7).
    p.remove_field(c, "scale")
    panel = CompoundPanel(p, c, lambda: "Tester")
    chooser = next(b for b in panel.boxes if b.defn.key == "procedure").editor.gp
    chooser._value_edited()
    assert "(0.5 mmol of 1a)" in chooser.preview.text()
    panel.flush()


def test_scale_typed_in_the_gp_fills_the_scale_field(qapp, setup):
    """SPEC §13n.9: "0.20 mmol" typed in the {scale} box fills an empty Reaction scale
    field, follows later changes while the field still holds what was filled in, and
    never overwrites a value typed in the field (a warning shows instead)."""
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data, sm, c = setup
    gp = p.add_general_procedure(data.id)
    p.save_compound(c)
    panel = CompoundPanel(p, c, lambda: "Tester")
    chooser = next(b for b in panel.boxes if b.defn.key == "procedure").editor.gp
    chooser.combo.setCurrentIndex(chooser.combo.findData(gp["id"]))
    scale = next(b for b in panel.boxes if b.defn.key == "scale").editor.widgets

    def type_scale(text):
        w = chooser.value_widgets["scale"]
        w.setText(text)
        w.textEdited.emit(text)

    type_scale("0.2")                          # no unit yet: nothing filled
    assert scale["value"].text() == ""
    type_scale("0.20mmol")
    assert scale["value"].text() == "0.20" and scale["unit"].currentData() == "mmol"
    type_scale("20 umol")                      # still ours: follows
    assert scale["value"].text() == "20" and scale["unit"].currentData() == "µmol"
    assert "(20 µmol)" in chooser.preview.text()
    panel.flush()
    saved = Project.open(p.folder).compound(c.id)
    assert saved.values["scale"] == {"value": "20", "unit": "µmol"}
    assert saved.data["scale_from_gp"] == {"value": "20", "unit": "µmol"}
    # The user types their own value in the field: it is never overwritten again…
    scale["value"].setText("0.25")
    scale["unit"].setCurrentIndex(scale["unit"].findData("mmol"))
    panel.save()
    type_scale("0.30 mmol")
    assert scale["value"].text() == "0.25"
    panel.flush()
    box = next(b for b in panel.boxes if b.defn.key == "procedure")
    assert any("says “0.30 mmol”" in w.message and "0.25 mmol" in w.message
               for w in box.warnings)
    # …and the warning goes once they agree (0.250 mmol = 0.25 mmol).
    type_scale("0.250 mmol")
    panel.flush()
    assert not any("Reaction scale" in w.message for w in box.warnings)


def test_references_field_and_dialog(qapp, setup, monkeypatch):
    from si_assistant.ui import references_ui
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data, sm, c = setup

    # Add by DOI (Crossref answer faked), checked in the edit dialog.
    found = R.clean({"authors": ["Fulmer, Gregory R."], "title": "Impurities",
                     "journal": "Organometallics", "year": "2010", "doi": "10.1021/om100106e"})
    monkeypatch.setattr(references_ui.refs, "fetch_doi", lambda doi: found)
    monkeypatch.setattr(references_ui.QInputDialog, "getText",
                        lambda *a, **k: ("https://doi.org/10.1021/om100106e", True))
    monkeypatch.setattr(references_ui.ReferenceEditDialog, "exec", lambda self: True)
    dlg = references_ui.ReferencesDialog(p)
    dlg.widget._add_doi()
    assert len(p.references) == 1 and dlg.table.item(0, 0).text() == "Fulmer 2010"
    monkeypatch.setattr(references_ui.QMessageBox, "information", lambda *a, **k: None)
    dlg.widget._add_doi()                            # same DOI again: not added twice
    assert len(p.references) == 1

    # The References field on a compound.
    p.add_field(c, {"key": "references"})
    panel = CompoundPanel(p, c, lambda: "Tester")
    editor = next(b for b in panel.boxes if b.defn.key == "references").editor
    monkeypatch.setattr(rich_text, "choose_references", lambda parent: [p.references[0]["id"]])
    editor._choose()
    assert editor.list.count() == 1
    panel.flush()
    saved = Project.open(p.folder).compound(c.id).values["references"]
    assert saved == {"text": "Spectral data matched those reported in the literature.",
                     "refs": [p.references[0]["id"]]}
    assert references_ui.ReferencesDialog(p).table.item(0, 3).text() == "1 place"
