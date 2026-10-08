"""The ee field (SPEC §13l.12) and the drop-downs that learn (§13l.13)."""

import pytest
from PySide6.QtWidgets import QComboBox, QLineEdit

from si_assistant.compile.blocks import ee_sentence, generated_paragraphs
from si_assistant.core import fields as F
from si_assistant.core.markup import SUB


# ---------------------------------------------------------------------- 12a: ee / er

@pytest.mark.parametrize("typed, shown", [
    ("99%", "99% ee"), (" 99 % ", "99 % ee"), (">99%", ">99% ee"), ("94.5%", "94.5% ee"),
    ("97:3", "97:3 er"), ("97.5:2.5", "97.5:2.5 er"), ("97 : 3", "97 : 3 er"),
    ("99% ee", "99% ee"), ("97:3 er", "97:3 er"), ("99% EE", "99% EE"), ("97:3 e.r.", "97:3 e.r."),
    ("99", "99"), ("", ""), ("racemic", "racemic"), ("10:1 dr", "10:1 dr"),
])
def test_ee_or_er_is_added(typed, shown):
    assert F.ee_with_unit(typed) == shown


# ---------------------------------------------------------------------- 12b: printed order

def text_of(runs):
    return "".join(r.text for r in runs)


FULL = {"value": "99%", "method": "HPLC", "column": "Chiralpak ID",
        "eluent": "10% iPrOH/hexanes", "flow": "1.0", "wavelength": "254", "t_major": "12.3",
        "t_minor": "14.8"}


def test_ee_sentence_value_first():
    runs = ee_sentence(FULL)
    assert text_of(runs) == ("99% ee. HPLC (Chiralpak ID, 10% iPrOH/hexanes, 1.0 mL/min, "
                             "254 nm): tR (major) = 12.3 min, tR (minor) = 14.8 min.")
    t = next(i for i, r in enumerate(runs) if r.text == "t")
    assert runs[t].italic and runs[t + 1].text == "R" and runs[t + 1].valign == SUB


@pytest.mark.parametrize("parts, printed", [
    ({"value": "97:3 er"}, "97:3 er."),
    ({"value": "99%", "method": "HPLC"}, "99% ee (HPLC)."),
    ({"value": "99%", "method": "SFC", "column": "Chiralpak AD-H"},
     "99% ee. SFC (Chiralpak AD-H)."),
    ({"value": "99% ee", "method": "", "t_major": "12.3"}, "99% ee. HPLC: tR (major) = 12.3 min."),
    ({"value": "", "column": "Chiralpak AD-H"}, ""),
])
def test_ee_sentence_leaves_out_missing_parts(parts, printed):
    assert text_of(ee_sentence(parts)) == printed


def test_compound_block_prints_ee_first(project):
    section = project.add_section("data", "Products")
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "ee"})
    c.values["ee"] = dict(FULL)
    project.save_compound(c)
    text = " ".join(text_of(p.runs) for p in generated_paragraphs(project, c))
    assert "99% ee. HPLC (Chiralpak ID" in text


# ---------------------------------------------------------------------- 13: learning lists

def test_learned_choices_merge_and_order():
    part = F.Part("text", kind="combo", choices=("colorless oil", "white solid"), learn=True)
    used = ["white solid", "Amber gum", "", "  ", "amber gum", "White Solid", "brown foam"]
    assert F.learned_choices(part, used) == ["colorless oil", "white solid", "Amber gum",
                                             "brown foam"]
    assert F.distinct(["A", "a", " b ", None]) == ["A", "b"]


def test_which_parts_learn():
    learning = {(d.key, p.key) for d in F.ALL_FIELDS.values() for p in d.parts if p.learn}
    assert learning == {("physical_state", "text"), ("rf", "visualization"),
                        ("ee", "column"), ("ee", "eluent"),
                        # (the Purification field, SPEC §13q.2)
                        ("purification", "method"), ("purification", "phase"),
                        ("purification", "eluent")}
    ee = {p.key: p for p in F.ALL_FIELDS["ee"].parts}
    assert ee["column"].kind == "combo" and ee["column"].choices == ()     # none built in
    assert ee["eluent"].kind == "combo"
    rf = {p.key: p for p in F.ALL_FIELDS["rf"].parts}
    assert rf["eluent"].kind == "text"                                    # stays a text box
    # Column and eluent are still needed for the field to count as filled in.
    assert {"column", "eluent"} <= set(F.ALL_FIELDS["ee"].required_parts())


@pytest.fixture
def section_with_compounds(project):
    section = project.add_section("data", "Products")
    a, b, c = (project.add_compound(section.id) for _ in range(3))
    for compound in (a, b, c):
        project.add_field(compound, {"key": "ee"})
    a.values["ee"] = {"value": "99% ee", "column": "Chiralpak AD-H", "eluent": "5% iPrOH/hexanes"}
    b.values["ee"] = {"value": "90% ee", "column": "chiralpak ad-h", "eluent": ""}
    a.values["physical_state"] = {"text": "amber gum"}
    b.values["physical_state"] = {"text": "white solid"}
    for compound in (a, b, c):
        project.save_compound(compound)
    return project, a, b, c


def test_used_part_values(section_with_compounds):
    p, a, b, c = section_with_compounds
    assert p.used_part_values("ee", "column") == ["Chiralpak AD-H"]   # first spelling
    assert p.used_part_values("ee", "eluent") == ["5% iPrOH/hexanes"]
    assert p.used_part_values("physical_state", "text") == ["amber gum", "white solid"]
    # A field taken off a compound keeps its value, but it no longer counts as used.
    p.remove_field(a, "ee")
    assert a.values["ee"]["column"] == "Chiralpak AD-H"
    assert p.used_part_values("ee", "column") == ["chiralpak ad-h"]


def combo(panel, field_key, part_key):
    box = next(b for b in panel.boxes if b.defn.key == field_key)
    return box.editor.widgets[part_key]


def items(box: QComboBox) -> list[str]:
    return [box.itemText(i) for i in range(box.count())]


def test_drop_downs_offer_what_the_project_uses(qapp, section_with_compounds):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, a, b, c = section_with_compounds
    panel = CompoundPanel(p, c, lambda: "Tester")
    column = combo(panel, "ee", "column")
    assert isinstance(column, QComboBox) and column.isEditable()
    assert items(column) == ["Chiralpak AD-H"]
    assert column.currentText() == ""                 # nothing chosen for this compound
    assert items(combo(panel, "ee", "eluent")) == ["5% iPrOH/hexanes"]
    state = items(combo(panel, "physical_state", "text"))
    assert state[:len(F.PHYSICAL_STATES)] == list(F.PHYSICAL_STATES)
    assert state[len(F.PHYSICAL_STATES):] == ["amber gum"]   # "white solid" is built in
    # A value typed here is stored as typed, and offered on other pages.
    column.setEditText("Chiralcel OD-H")
    panel.flush()
    assert p.compound(c.id).values["ee"]["column"] == "Chiralcel OD-H"
    panel_a = CompoundPanel(p, a, lambda: "Tester")
    assert items(combo(panel_a, "ee", "column")) == ["Chiralcel OD-H", "Chiralpak AD-H"]
    assert combo(panel_a, "ee", "column").currentText() == "Chiralpak AD-H"
    # Once nothing uses a value, it is no longer offered.
    combo(panel_a, "physical_state", "text").setEditText("colorless oil")
    panel_a.flush()
    panel_c = CompoundPanel(p, c, lambda: "Tester")
    assert "amber gum" not in items(combo(panel_c, "physical_state", "text"))
    for pl in (panel, panel_a, panel_c):
        pl.flush()


def test_ee_box_adds_ee_when_left(qapp, section_with_compounds):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, a, b, c = section_with_compounds
    panel = CompoundPanel(p, c, lambda: "Tester")
    value = next(bx for bx in panel.boxes if bx.defn.key == "ee").editor.widgets["value"]
    assert isinstance(value, QLineEdit)
    value.setText("99%")
    value.editingFinished.emit()
    assert value.text() == "99% ee"
    value.setText("97:3")
    value.editingFinished.emit()
    assert value.text() == "97:3 er"
    value.setText("97:3 (by SFC)")                    # anything else stays as typed
    value.editingFinished.emit()
    assert value.text() == "97:3 (by SFC)"
    value.setText("99")
    value.editingFinished.emit()
    assert value.text() == "99"
    panel.flush()
    assert p.compound(c.id).values["ee"]["value"] == "99"
