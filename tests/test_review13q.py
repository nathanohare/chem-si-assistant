"""Review list of SPEC §13q: spectra caption size, the Purification field, Duplicate…,
pages for the ID table's unused IDs, sharp dashboard thumbnails, and a more compact
scheme page and compound header. Made-up data only."""

import json

import pytest
from PySide6.QtCore import Qt

from si_assistant.compile import blocks
from si_assistant.core import duplicate as dup
from si_assistant.core import procedures
from si_assistant.core import scheme_editor as se
from si_assistant.core import spectra as S
from si_assistant.core.markup import serialize
from si_assistant.core.project import Project, ProjectError


def new_project(tmp_path):
    p = Project.create(tmp_path, "P")
    p.acquire_lock("Tester")
    p.enable_undo()
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["3a", "3b", "3c", "3d", "3e", "3f", "3g", "3h"])
    return p, data


def text_of(paragraphs) -> str:
    return " | ".join("".join(r.text for r in p.runs) for p in paragraphs)


# ---------------------------------------------------------------------- 1. caption size

def test_spectra_caption_size_is_a_setting(tmp_path):
    assert S.DEFAULTS["caption_size"] == S.CAPTION_PT == 9.0
    assert S.clean({"caption_size": 11})["caption_size"] == 11.0
    assert S.clean({"caption_size": 100})["caption_size"] == S.CAPTION_SIZE_RANGE[1]
    assert S.clean({"caption_size": 1})["caption_size"] == S.CAPTION_SIZE_RANGE[0]
    assert "caption_size" not in S.clean({"caption_size": "big"})


def test_caption_size_is_compiled(tmp_path):
    from si_assistant.compile.document import CompileOptions, build
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from tests.sample_project import build_sample
    p, parts = build_sample(tmp_path)
    spectra = parts["spectra"]
    defaults = {**p.spectra_defaults(), "caption": True}
    spectra.data["defaults"] = defaults
    p.save_section(spectra)

    def source():
        options = CompileOptions.from_settings(p.settings)
        options.only = ("spectra", parts["c2"].id)
        return build(p, options, image_info, convert_to_png, name="live").typ_path.read_text(
            encoding="utf-8")
    assert "caption: [" in source() and "caption-size" not in source()   # (9 pt: the style's)
    spectra.data["defaults"] = {**defaults, "caption_size": 7.5}
    p.save_section(spectra)
    assert "caption-size: 7.5pt" in source()


def test_caption_size_on_the_section_and_spectrum_pages(qapp, tmp_path):
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    from tests.sample_project import build_sample
    p, parts = build_sample(tmp_path)
    panel = SpectraSectionPanel(p, parts["spectra"])
    assert panel.caption_size.value() == 9.0
    assert not panel.caption_size.isEnabled()          # (only with the caption shown)
    panel.caption.setChecked(True)
    assert panel.caption_size.isEnabled()
    panel.caption_size.setValue(10.5)
    assert panel.values()["caption_size"] == 10.5
    panel._save()
    assert p.spectra_defaults()["caption_size"] == 10.5
    panel.deleteLater()


# ---------------------------------------------------------------------- 2. purification

def purified(p, data, **parts):
    c = p.add_compound(data.id, name="alcohol")
    p.add_field(c, {"key": "purification"})
    c.values["purification"] = {"method": "flash column chromatography", "phase": "SiO₂",
                                "eluent": "10% EtOAc/hexanes", **parts}
    p.save_compound(c)
    return c


def test_purification_sentence_ends_the_procedure(tmp_path):
    p, data = new_project(tmp_path)
    c = purified(p, data)
    assert procedures.purification_text(c) == \
        "flash column chromatography (SiO₂, 10% EtOAc/hexanes)"
    p.write_compound_text(c, "procedure", "The ketone was reduced with NaBH4 in MeOH.")
    text = text_of(blocks.generated_paragraphs(p, c, use_helpers=False))
    assert text.startswith("The ketone was reduced with NaBH4 in MeOH. The crude product was "
                           "purified by flash column chromatography (SiO₂, 10% EtOAc/hexanes).")
    # Only a method: no brackets. No method: nothing printed (and not "filled").
    c.values["purification"] = {"method": "recrystallization", "phase": "", "eluent": ""}
    assert "purified by recrystallization." in text_of(blocks.generated_paragraphs(p, c, False))
    c.values["purification"] = {"method": "", "phase": "SiO₂", "eluent": "x"}
    assert "purified" not in text_of(blocks.generated_paragraphs(p, c, False))
    defn = next(d for d in p.field_defs(c) if d.key == "purification")
    assert not p.field_filled(c, defn)
    # Typed "Printed as" wins (SPEC §2.7).
    c.values["purification"] = {"method": "distillation", "sentence": "Distilled (bp 80 °C)."}
    assert text_of(blocks.generated_paragraphs(p, c, False)).endswith(
        "MeOH. Distilled (bp 80 °C).")


def test_purification_without_a_procedure_field_and_with_si_text(tmp_path):
    p, data = new_project(tmp_path)
    c = purified(p, data)
    p.remove_field(c, "procedure")
    paragraphs = blocks.generated_paragraphs(p, c, use_helpers=False)
    assert "".join(r.text for r in paragraphs[0].runs).startswith(
        "The crude product was purified by flash column chromatography")


def test_gp_purification_placeholder(tmp_path):
    p, data = new_project(tmp_path)
    gp = p.add_general_procedure(data.id, "General Procedure A")
    p.update_general_procedure(gp["id"],
                               sentence="Prepared according to {GP}; purified by {purification}.")
    c = purified(p, data)
    c.values["procedure"] = {"gp": gp["id"], "values": {}}
    p.save_compound(c)
    assert procedures.uses_purification(p.compound_gp(c))
    text = text_of(blocks.generated_paragraphs(p, c, use_helpers=False))
    assert ("Prepared according to General Procedure A; purified by flash column "
            "chromatography (SiO₂, 10% EtOAc/hexanes).") in text
    assert "The crude product" not in text                # (placed by the GP: not twice)
    # Typed in its box on the compound page: that is printed instead.
    c.values["procedure"]["values"] = {"purification": "distillation"}
    assert "purified by distillation." in text_of(blocks.generated_paragraphs(p, c, False))


def test_purification_field_shows_what_it_prints(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    other = p.add_compound(data.id)
    p.add_field(other, {"key": "rf"})
    other.values["rf"] = {"value": "0.3", "eluent": "20% Et2O/pentane"}
    p.save_compound(other)
    c = purified(p, data)
    panel = CompoundPanel(p, c, lambda: "Tester")
    box = next(b for b in panel.boxes if b.defn.key == "purification")
    editor = box.editor
    sentence = editor.widgets["sentence"]
    assert sentence.placeholderText() == ("The crude product was purified by flash column "
                                          "chromatography (SiO₂, 10% EtOAc/hexanes).")
    assert "end of the experimental procedure" in sentence.toolTip()
    eluents = [editor.widgets["eluent"].itemText(i)
               for i in range(editor.widgets["eluent"].count())]
    assert "20% Et2O/pentane" in eluents                  # (learned from the Rf field)
    methods = [editor.widgets["method"].itemText(i)
               for i in range(editor.widgets["method"].count())]
    assert "flash column chromatography" in methods and "preparative TLC" in methods
    editor.widgets["phase"].setCurrentText("Al₂O₃")
    assert "(Al₂O₃, 10% EtOAc/hexanes)" in sentence.placeholderText()
    panel.deleteLater()


# ---------------------------------------------------------------------- 3. duplicate

def rich_compound(p, data, tmp_path):
    c = p.add_compound(data.id, name="Ethyl benzoate")
    p.set_structure(c, "CCOC(=O)c1ccccc1", picture=b"PNG picture", picture_ext=".png")
    p.write_compound_text(c, "procedure", "Stirred overnight.")
    c.values["yield"] = {"percent": "88", "mass": "12", "mass_unit": "mg"}
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "151.0754",
                        "reports": [{"file": "report.mzML"}], "found_from": {"file": "x"}}
    c.values["nmr"][0]["text"] = "1H NMR (400 MHz, CDCl3) δ 8.05 (d, 2H)"
    c.data["id_override"] = "S9"
    c.data["flags"] = {"yield": {"note": "check"}}
    p.save_compound(c)
    spectrum = tmp_path / "3a_1H.pdf"
    spectrum.write_bytes(b"%PDF-1.4 made up")
    p.attach_compound_file(c, "spectra", spectrum, {"type": "1H"})
    p.write_compound_text(c, "notes", "private note")
    return p.compound(c.id)


def test_duplicate_a_compound(tmp_path):
    p, data = new_project(tmp_path)
    first = p.add_compound(data.id, name="first")
    c = rich_compound(p, data, tmp_path)
    keys = {d.key for d in dup.copyable_fields(p, c)}
    assert {"name", "structure", "procedure", "yield", "hrms"} <= keys
    assert not keys & {"spectra", "files", "nmr", "private_files"}
    with p.undo_step("Duplicate"):
        made = dup.duplicate_compound(p, c, keys - {"yield"}, copies=2)
    assert len(made) == 2
    report = p.id_report()
    assert [report.display_id(x.id) for x in made] == ["3c", "3d"]   # (next unused IDs)
    assert report.display_id(first.id) == "3a"                        # (nothing renumbered)
    new = p.compound(made[0].id)
    assert new.name == "Ethyl benzoate" and new.id_override == "" and not new.flags
    assert p.structure_info(new).formula == "C9H10O2"
    assert p.structure_image_path(new).read_bytes() == b"PNG picture"
    assert p.read_compound_text(new, "procedure") == "Stirred overnight."
    assert p.read_compound_text(new, "notes") == "private note"
    assert "yield" not in new.values                                   # (unticked)
    assert new.values["hrms"]["found"] == "151.0754"
    assert "reports" not in new.values["hrms"] and "found_from" not in new.values["hrms"]
    assert not new.values.get("spectra") and not any((new.folder / "spectra").glob("*"))
    assert all(not r.get("text") for r in new.values["nmr"])
    assert [f["key"] for f in new.fields] == [f["key"] for f in c.fields]
    p.undo()
    assert len(p.section(data.id).compounds) == 2


def test_duplicate_right_after_renumbers_the_rest(tmp_path):
    p, data = new_project(tmp_path)
    a = p.add_compound(data.id, name="a")
    b = p.add_compound(data.id, name="b")
    made = dup.duplicate_compound(p, a, {"name"}, copies=1, place=dup.AFTER)
    report = p.id_report()
    assert report.display_id(made[0].id) == "3b" and report.display_id(b.id) == "3c"
    with pytest.raises(ProjectError):
        dup.check_copies(0)
    assert dup.check_copies("3") == 3


def drawn_scheme(p, data):
    k = p.add_scheme(data.id, name="Route")
    se.set_box_structure(p, k, se.starting(k)[0]["id"], "CC(=O)c1ccccc1")
    sid = se.add_inline_structure(p, k, "ClCCl", None)
    step = se.steps(k)[0]
    se.set_conditions(p, k, step["id"], "top", f"NaBH_{{4}} @struct{{{sid}}}")
    box = step["products"][0]
    se.set_box_structure(p, k, box["id"], "CC(O)c1ccccc1")
    c = se.link_new_compound(p, k, box["id"], name="alcohol")
    c.values["yield"] = {"percent": "91"}
    p.save_compound(c)
    # The starting material's ID label names the product (made up, to test the link).
    se.set_box_label(p, k, se.starting(k)[0]["id"], "@{" + c.id + "}")
    se.set_locked(p, k, True)
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    p.set_scheme_label(k.id, "Scheme 9")
    return p.scheme(k.id)


def test_duplicate_a_scheme_page(tmp_path):
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    with p.undo_step("Duplicate"):
        made = dup.duplicate_scheme(p, k, {"name", "structure", "yield"}, copies=2)
    assert [m.name for m in made] == ["Route (copy 1)", "Route (copy 2)"]
    new = p.scheme(made[0].id)
    assert len(new.children) == 1 and new.children[0].id != k.children[0].id
    product = se.steps(new)[0]["products"][0]
    assert product["compound"] == new.children[0].id                 # (linked to its copy)
    assert se.starting(new)[0]["label"] == "@{" + new.children[0].id + "}"
    assert new.children[0].values["yield"] == {"percent": "91"}
    assert p.id_report().display_id(new.children[0].id) == "3b"
    assert not se.is_locked(new) and "label" not in new.data          # (lock, typed label)
    assert se.layout_overrides(new) == {"show_label": True}
    assert se.box_view(p, new, se.starting(new)[0]).mol is not None   # (its files copied)
    assert not se.warnings(p, new)
    # The original is unchanged.
    assert se.steps(k)[0]["products"][0]["compound"] == k.children[0].id
    p.undo()
    assert [x.id for x in p.section(data.id).schemes] == [k.id]


def test_duplicate_a_compound_of_a_scheme(tmp_path):
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    made = dup.duplicate_compound(p, k.children[0], {"name"}, place=dup.AFTER)
    section = p.section(data.id)
    assert made[0].scheme_id is None and section.items.index(made[0]) == \
        section.items.index(k) + 1


def test_duplicate_dialog(qapp, tmp_path):
    from si_assistant.ui.duplicate_ui import DuplicateDialog
    p, data = new_project(tmp_path)
    c = rich_compound(p, data, tmp_path)
    dialog = DuplicateDialog(p, c)
    assert set(dialog.boxes) == {d.key for d in dup.copyable_fields(p, c)}
    assert "3b" in dialog.ids_note.text()
    dialog.copies.setValue(3)
    assert "3b, 3c, 3d" in dialog.ids_note.text()
    dialog.boxes["yield"].setChecked(False)
    assert "yield" not in dialog.keys() and dialog.place() == dup.END
    dialog.after.setChecked(True)
    assert dialog.place() == dup.AFTER and "renumbered" in dialog.ids_note.text()
    dialog.deleteLater()
    k = drawn_scheme(p, data)
    sd = DuplicateDialog(p, k)
    assert "scheme" in sd.windowTitle() and "yield" in sd.boxes
    sd.deleteLater()


# ---------------------------------------------------------------------- 4. pages for IDs

def test_pages_for_unused_ids(tmp_path):
    p, data = new_project(tmp_path)
    p.add_compound(data.id)
    p.set_id_table(data.id, ["3a", "3b", "", "3d", "", ""])
    section = p.section(data.id)
    assert p.ids_without_pages(section) == ["3b", "", "3d"]
    with p.undo_step("Pages"):
        made = p.add_pages_for_ids(data.id)
    report = p.id_report()
    assert [report.display_id(c.id) for c in made][::2] == ["3b", "3d"]
    assert p.ids_without_pages(p.section(data.id)) == []
    p.undo()
    assert len(p.section(data.id).compounds) == 1


def test_pages_button_on_the_id_table(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    from si_assistant.ui import main_window as mw
    p, data = new_project(tmp_path / "p")
    folder = p.folder
    p.close()
    prefs = str(tmp_path / "prefs.ini")
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(prefs, QSettings.Format.IniFormat))
    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    try:
        assert win.open_project(folder)
        win.select("section", data.id)
        panel = win.panel
        panel.tabs.setCurrentIndex(1)                     # the ID table tab
        assert panel.pages_btn.isEnabled()
        panel.pages_btn.click()
        assert len(win.project.section(data.id).compounds) == 8
        assert win.panel.tabs.currentIndex() == 1 and not win.panel.pages_btn.isEnabled()
        assert win._find_item("compound", win.project.section(data.id).compounds[7].id)
    finally:
        win.close_project()
        win.close()


# ---------------------------------------------------------------------- 5. thumbnails

def test_dashboard_thumbnails_are_drawn_for_the_screen(qapp, tmp_path):
    from si_assistant.ui import dashboard_view as dv
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    p.set_structure(c, "CCOC(=O)c1ccccc1")
    one = dv.thumbnail(p, c, 1.0)
    sharp = dv.thumbnail(p, c, 2.0)
    assert sharp.devicePixelRatio() == 2.0
    assert sharp.width() == 2 * one.width() == 2 * dv.THUMB.width()


# ---------------------------------------------------------------------- 6. compact pages

def editor_page(qapp, p, k):
    from si_assistant.ui.scheme_panel import SchemePanel
    panel = SchemePanel(p, k, lambda: "Tester")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.resize(1300, 900)
    panel.show()
    qapp.processEvents()
    return panel


def test_plus_product_is_under_the_last_product(qapp, tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id)
    se.add_box(p, k, se.steps(k)[0]["id"])            # two products in step 1
    panel = editor_page(qapp, p, k)
    ed = panel.editor
    products = [b for b in ed.boxes if b.where != se.START]
    assert products[0].add_product_btn is None
    assert products[1].add_product_btn is not None and products[1].add_product_btn.isVisible()
    assert ed.steps[0].add_btn.isHidden()             # (the step has products)
    products[1].add_product_btn.click()
    assert len(se.steps(k)[0]["products"]) == 3
    # A step without products keeps its own "+ Product".
    for box in list(se.steps(k)[0]["products"]):
        se.remove_box(p, k, box["id"])
    ed.rebuild()
    assert not ed.steps[0].add_btn.isHidden()
    panel.close()


def test_scheme_page_header_is_one_row(qapp, tmp_path):
    p, data = new_project(tmp_path)
    k = p.add_scheme(data.id, name="Route")
    panel = editor_page(qapp, p, k)
    assert panel.name_row.isHidden()                   # (label and caption off: renamed in
    assert panel.collapse_btn.y() == panel.editor.add_sm_btn.y() or \
        abs(panel.collapse_btn.y() - panel.editor.add_sm_btn.y()) < 12   # the tree)
    se.set_scheme_layout(p, k, {**se.scheme_layout(p.settings, k), "show_label": True})
    panel.editor.layout_changed.emit()
    assert not panel.name_row.isHidden()
    panel.set_collapsed(True)
    assert panel.editor.add_sm_btn.isHidden() and panel.editor.layout_btn.isHidden()
    assert not panel.collapse_btn.isHidden()
    panel.set_collapsed(False)
    assert not panel.editor.add_sm_btn.isHidden()
    panel.close()


def test_compact_compound_header(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, data = new_project(tmp_path)
    c = p.add_compound(data.id)
    panel = CompoundPanel(p, c, lambda: "Tester")
    label = panel.completion_label
    total = len([d for d in p.field_defs(c) if d.key in p.section(data.id).required_keys()])
    assert f"/ {total} required" in label.text() and 'href="missing"' in label.text()
    assert label.toolTip().startswith("Missing: ")
    first_missing = next(b for b in panel.boxes if b.defn.label in panel.missing_labels)
    first_missing.set_collapsed(True)
    panel.focus_first_missing()
    assert not first_missing.collapsed
    panel.deleteLater()
    k = p.add_scheme(data.id)
    child = se.link_new_compound(p, k, se.steps(k)[0]["products"][0]["id"])
    panel = CompoundPanel(p, child, lambda: "Tester")
    assert "not printed" in panel.scheme_info.toolTip()
    panel.deleteLater()


def test_no_windows_flash_on_the_new_pages(qapp, tmp_path):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    class Spy(QObject):
        shown = []

        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow():
                self.shown.append(type(obj).__name__)
            return False
    p, data = new_project(tmp_path)
    k = drawn_scheme(p, data)
    se.set_locked(p, k, False)
    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        panel = editor_page(qapp, p, k)
        spy.shown.clear()
        panel.editor.add_box(se.steps(k)[0]["id"])
        panel.editor.add_step()
        qapp.processEvents()
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert spy.shown == []
    panel.close()
