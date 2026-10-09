"""Audit fixes on the Compound page (group "compound"): the HRMS Found box after a
structure change, a failed autosave, the spectrum-type guess, the automatic figure labels
of inline files, warnings after a rebuild, the general procedure's preview, hand-edited
values, the copy/paste key names and the storage folder of a read-only project.
All data is made up."""

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from si_assistant.core import checks
from si_assistant.core import fields as F
from si_assistant.core import ms_reports as R
from si_assistant.core.labels import compute
from si_assistant.core.project import Project
from si_assistant.ui.field_widgets import AttachmentsEditor, guess_spectrum_type
from tests import ms_samples as M


def new_project(folder, structure: bool = True):
    p = Project.create(folder, "C")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.ensure_spectra_section()
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="made-up ester")
    if structure:
        p.set_structure(c, M.SMILES)
    return p, c


def csv_file(folder, name="3a peaks.csv", offset=1.2):
    path = folder / name
    path.write_text(M.csv_text(M.sample_peaks(offset)), encoding="utf-8")
    return path


def panel_for(p, c):
    from si_assistant.ui.compound_panel import CompoundPanel
    return CompoundPanel(p, c, lambda: "Tester")


def box(panel, key):
    return next(b for b in panel.boxes if b.defn.key == key)


def stored(c) -> dict:
    return json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))["values"]


def picture(path):
    img = QImage(60, 40, QImage.Format.Format_RGB32)
    img.fill(0x3366FF)
    img.save(str(path))
    return path


# ---------------------------------------------------------------------- HRMS Found box

def _project_syncs_found(folder) -> bool:
    """Whether Project.set_structure fills the found m/z from the reports in the same
    step (the core half of the fix, in core/project.py)."""
    p, c = new_project(folder, structure=False)
    try:
        p.add_hrms_report(c, csv_file(folder))
        p.set_structure(c, M.SMILES)
        return bool((c.values.get("hrms") or {}).get("found"))
    finally:
        p.close()


def test_structure_change_shows_the_stored_found_without_an_edit_of_its_own(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    p.add_hrms_report(c, csv_file(tmp_path))
    assert c.values["hrms"]["found"]            # filled from the report
    panel = panel_for(p, c)
    editor = box(panel, "hrms").editor
    qapp.processEvents()
    panel.flush()
    # The structure changes to one the report does not fit; the project clears the Found
    # m/z filled from the report in the same save (as Project.set_structure does).
    p.set_structure(c, "CCCCCCCCO")
    R.update_found(p, c)
    p.save_compound(c)
    assert c.values["hrms"]["found"] == ""
    panel._on_structure_changed([])
    assert editor.widgets["found"].text() == ""
    assert editor._found_from is None
    # Nothing is left to save: the page made no edit (and no undo step) of its own.
    assert not panel._timer.isActive() and not panel._dirty
    panel.deleteLater()
    p.close()


def test_undo_of_a_structure_change_with_a_report_keeps_redo(qapp, tmp_path):
    if not _project_syncs_found(tmp_path / "probe"):
        pytest.skip("needs Project.set_structure to fill the found m/z (core/project.py)")
    p, c = new_project(tmp_path / "proj", structure=False)
    p.enable_undo()
    panel = panel_for(p, c)
    editor = box(panel, "hrms").editor
    editor.reports.add_paths([csv_file(tmp_path)])
    qapp.processEvents()
    panel.flush()
    structure = next(b.editor for b in panel.boxes if b.defn.key == "structure")
    structure.set_structure_text(M.SMILES)
    for _ in range(3):
        qapp.processEvents()
    panel.flush()
    found = stored(c)["hrms"]["found"]
    assert found
    panel.deleteLater()
    p.undo()
    c = p.compound(c.id)
    panel = panel_for(p, c)        # the page is built again after Undo
    for _ in range(3):
        qapp.processEvents()
    panel.flush()
    assert p.read_structure(c) is None
    assert p.can_redo                 # opening the page made no new step
    panel.deleteLater()
    p.redo()
    c = p.compound(c.id)
    assert p.read_structure(c) is not None and stored(c)["hrms"]["found"] == found
    p.close()


# ---------------------------------------------------------------------- failed autosave

def test_a_failed_autosave_is_said_and_tried_again(qapp, tmp_path, monkeypatch):
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    messages = []
    panel.saved.connect(messages.append)
    percent = box(panel, "yield").editor.widgets["percent"]
    percent.setText("42")
    percent.textEdited.emit("42")
    real = p.save_compound
    calls = []

    def locked_once(compound):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        return real(compound)
    monkeypatch.setattr(p, "save_compound", locked_once)
    panel._timer.timeout.emit()          # the autosave: no exception escapes
    assert any(m.startswith("Could not save") for m in messages)
    assert stored(c)["yield"].get("percent") != "42"
    panel.flush()                         # leaving the page tries again
    assert stored(c)["yield"]["percent"] == "42"
    assert not panel._dirty
    panel.deleteLater()
    p.close()


def test_flush_raises_when_the_retry_fails_too(qapp, tmp_path, monkeypatch):
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    percent = box(panel, "yield").editor.widgets["percent"]
    percent.setText("42")
    percent.textEdited.emit("42")

    def locked(compound):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(p, "save_compound", locked)
    panel.save()
    with pytest.raises(PermissionError):   # (the main window shows "Could not save")
        panel.flush()
    panel.deleteLater()
    p.close()


def test_typing_whose_autosave_failed_counts_as_unsaved_when_the_lock_is_lost(
        qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from si_assistant.ui.main_window import MainWindow
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    percent = box(panel, "yield").editor.widgets["percent"]
    percent.setText("42")
    percent.textEdited.emit("42")

    def locked(compound):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(p, "save_compound", locked)
    panel._timer.timeout.emit()          # the autosave fails; the timer is stopped
    assert not panel._timer.isActive() and panel._dirty
    # So the main window keeps the page with the typing on it, instead of rebuilding it.
    assert MainWindow._panel_has_unsaved_typing(SimpleNamespace(
        panel=panel, _panel_waits_to_save=MainWindow._panel_waits_to_save))
    panel.deleteLater()
    p.close()


def test_general_procedure_retries_a_failed_save(qapp, tmp_path, monkeypatch):
    from si_assistant.ui.procedure_widgets import GeneralProceduresEditor
    p, c = new_project(tmp_path / "proj")
    section = p.section(c.section_id)
    gp = p.add_general_procedure(section.id)
    editor = GeneralProceduresEditor(p, section)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    editor.title.setText("Synthesis of esters")
    editor._edited()
    real = p.update_general_procedure
    calls = []

    def locked_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        return real(*a, **k)
    monkeypatch.setattr(p, "update_general_procedure", locked_once)
    editor.flush()                        # fails (a warning is shown)
    assert p.general_procedure(gp["id"])[1].get("title", "") == ""
    editor.flush()                        # tried again
    assert p.general_procedure(gp["id"])[1]["title"] == "Synthesis of esters"
    editor.deleteLater()
    p.close()


# ---------------------------------------------------------------------- spectrum type guess

@pytest.mark.parametrize("name, shown, expected", [
    ("13c_1H.pdf", "", "1H"),
    ("11b_1H NMR.pdf", "", "1H"),
    ("31p_1H.pdf", "", "1H"),
    ("13c_HPLC.pdf", "", "HPLC/SFC"),
    ("13c_ir.pdf", "", "IR"),
    ("19f-13C.pdf", "", "13C"),
    ("3a_1H.pdf", "", "1H"),
    ("3a_13C.pdf", "", "13C"),
    ("3a_HSQC.pdf", "", "2D NMR"),
    ("3a_HMBC_1H-13C.pdf", "", "2D NMR"),
    ("3a_1H-13C HSQC.pdf", "", "2D NMR"),
    ("3a IR.pdf", "", "IR"),
    ("3a_13CNMR.pdf", "", "13C"),
    ("3a13C.pdf", "", "13C"),
    ("3a_13C{1H}.pdf", "3a", "13C"),
    ("3a 13C{1H} NMR.pdf", "3a", "13C"),
    ("3a_19F{1H}.pdf", "3a", "19F"),
    ("13c_13C.pdf", "13c", "13C"),
    ("13c_13C{1H}.pdf", "13c", "13C"),
    ("13c_1h.pdf", "13c", "1H"),
    ("1H NMR 13c.pdf", "13c", "1H"),
    ("3a.pdf", "3a", "1H"),
    # A decoupled heteronucleus spectrum: the decoupled nucleus is not the type.
    ("3a_13C(1H).pdf", "3a", "13C"),
    ("3a_13C [1H].pdf", "3a", "13C"),
    ("3a_31P[1H].pdf", "3a", "31P"),
    ("3a_19F(1H).pdf", "3a", "19F"),
    ("3a 19F (1H decoupled).pdf", "3a", "19F"),
    ("3a_13C_1H-decoupled.pdf", "3a", "13C"),
    ("S12_13C_1H-decoupled.pdf", "S12", "13C"),
    ("3a 13C proton decoupled.pdf", "3a", "13C"),
    ("3a_carbon_1H-dec.pdf", "3a", "13C"),
    ("3a_31P_1H-dec.pdf", "3a", "31P"),
    ("3a_13C-1H.pdf", "3a", "13C"),
    ("3a_19F_1H.pdf", "3a", "19F"),
    ("3a_13C(1H).pdf", "", "13C"),
    ("3a_13C_1H-decoupled.pdf", "", "13C"),
    ("13c_13C(1H).pdf", "13c", "13C"),
    ("13c_1H.pdf", "13c", "1H"),
    ("3a (13C).pdf", "3a", "13C"),
    ("3a13C(1H).pdf", "3a", "13C"),
    ("3a_1H (CDCl3).pdf", "3a", "1H"),
])
def test_spectrum_type_is_not_taken_from_the_compound_id(name, shown, expected):
    assert guess_spectrum_type(name, shown) == expected


# ---------------------------------------------------------------------- inline file labels

def _two_file_fields(p, c, tmp_path):
    """Additional files (a inline, z not printed) and a custom File(s) field (b, d inline)."""
    if "files" not in c.field_keys:
        p.add_field(c, {"key": "files"})
    p.add_field(c, {"key": "custom-f1", "custom": {"label": "More files", "type": "file"}})
    for key, name in (("files", "a.png"), ("files", "z.png"), ("custom-f1", "b.png"),
                      ("custom-f1", "d.png")):
        p.attach_compound_file(c, key, picture(tmp_path / name))
    for item in c.values["files"]:
        if item["file"] == "z.png":
            item["placement"] = "exclude"
    p.save_compound(c)


def _placeholders(editor):
    return [w["label"].placeholderText() for _n, w in editor.row_widgets]


def _attachments(panel, key) -> AttachmentsEditor:
    return box(panel, key).editor


def test_second_file_field_shows_the_printed_label(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    _two_file_fields(p, c, tmp_path)
    panel = panel_for(p, c)
    files, more = _attachments(panel, "files"), _attachments(panel, "custom-f1")
    assert _placeholders(files) == ["Figure S1 (automatic)", "(label: only for inline files)"]
    assert _placeholders(more) == ["Figure S2 (automatic)", "Figure S3 (automatic)"]
    # The same as what is printed (labels.compute numbers them in one run).
    printed = [i.auto for i in compute(p).items if i.owner == c.id and i.kind == "figure"]
    assert printed == ["Figure S1", "Figure S2", "Figure S3"]
    panel.deleteLater()
    p.close()


def test_label_hints_follow_a_placement_change(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    _two_file_fields(p, c, tmp_path)
    panel = panel_for(p, c)
    files, more = _attachments(panel, "files"), _attachments(panel, "custom-f1")
    placement = files.row_widgets[0][1]["placement"]
    placement.setCurrentIndex(placement.findData("appendix"))   # a.png: Spectra section
    panel.flush()
    assert _placeholders(files) == ["(label: only for inline files)",
                                    "(label: only for inline files)"]
    assert _placeholders(more) == ["Figure S1 (automatic)", "Figure S2 (automatic)"]
    placement.setCurrentIndex(placement.findData("inline"))      # and back
    panel.flush()
    assert _placeholders(more) == ["Figure S2 (automatic)", "Figure S3 (automatic)"]
    # A typed label that another file prints with: that file's hint turns orange, and
    # back when the label is cleared.
    label = files.row_widgets[0][1]["label"]
    label.setText("Figure S3")
    label.textEdited.emit("Figure S3")
    panel.flush()
    d_box = more.row_widgets[1][1]["label"]
    assert "#d35400" in d_box.styleSheet()
    label.setText("")
    label.textEdited.emit("")
    panel.flush()
    assert d_box.styleSheet() == ""
    panel.deleteLater()
    p.close()


def test_an_entry_without_a_file_takes_no_figure_number(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    _two_file_fields(p, c, tmp_path)
    # A hand-edited entry with no file name, first in Additional files: nothing is printed
    # for it (labels.compute and the compiler leave it out), so it takes no number.
    c.values["files"].insert(0, {"placement": "inline", "caption": "x"})
    p.save_compound(c)
    panel = panel_for(p, c)
    files, more = _attachments(panel, "files"), _attachments(panel, "custom-f1")
    assert _placeholders(files) == ["(label: only for inline files)", "Figure S1 (automatic)",
                                    "(label: only for inline files)"]
    assert _placeholders(more) == ["Figure S2 (automatic)", "Figure S3 (automatic)"]
    printed = [i.auto for i in compute(p).items if i.owner == c.id and i.kind == "figure"]
    assert printed == ["Figure S1", "Figure S2", "Figure S3"]
    # The same after a save (the hints are refreshed without rebuilding the rows).
    caption = more.row_widgets[0][1]["caption"]
    caption.setText("A picture")
    caption.textEdited.emit("A picture")
    panel.flush()
    assert _placeholders(files)[1:] == ["Figure S1 (automatic)", "(label: only for inline files)"]
    assert _placeholders(more) == ["Figure S2 (automatic)", "Figure S3 (automatic)"]
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- warnings after rebuild

def test_warnings_stay_on_the_fields_after_a_new_nmr_report(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    structure = box(panel, "structure").editor
    structure.set_structure_text("CC(=O)N(C)Cc1ccc(F)cc1")    # adds a ¹⁹F report
    for _ in range(3):
        qapp.processEvents()
    active = {w.field for w in checks.active_warnings(p, c, p.id_report())} - {checks.ID_KEY}
    assert active
    shown = {b.defn.key for b in panel.boxes if b.warnings}
    assert shown == active & {b.defn.key for b in panel.boxes}
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- general procedure

def test_gp_preview_follows_yield_and_manual_id(qapp, tmp_path):
    from si_assistant.ui import rich_text
    p, c = new_project(tmp_path / "proj")
    rich_text.set_link_context(rich_text.LinkContext(p))
    try:
        gp = p.add_general_procedure(c.section_id)
        p.update_general_procedure(gp["id"], sentence="Made by {GP} in {yield} yield, "
                                                      "{product}.")
        c.values["yield"] = {"percent": "90", "mass": "", "mass_unit": "mg"}
        c.values["procedure"] = {"gp": gp["id"], "values": {}}
        p.save_compound(c)
        panel = panel_for(p, c)
        chooser = box(panel, "procedure").editor.gp
        assert "in 90% yield" in chooser.preview.text()
        percent = box(panel, "yield").editor.widgets["percent"]
        percent.setText("55")
        percent.textEdited.emit("55")
        panel.override_edit.setText("9z")
        panel.override_edit.textEdited.emit("9z")
        panel.flush()
        assert "in 55% yield" in chooser.preview.text()
        assert "<b>9z</b>" in chooser.preview.text()
        assert chooser.value_widgets["yield"].placeholderText() == "automatic: 55%"
        assert chooser.value_widgets["product"].placeholderText() == "automatic: 9z"
        panel.deleteLater()
    finally:
        rich_text.set_link_context(None)
        p.close()


# ---------------------------------------------------------------------- hand-edited values

def test_part_text_and_is_filled_with_odd_values():
    assert F.part_text(None) == "" and F.part_text("ab") == "ab" and F.part_text(92) == "92"
    assert F.part_text({"x": 1}) == "" and F.part_text(True) == ""
    yld = F.ALL_FIELDS["yield"]
    assert not F.is_filled(yld, "92")
    assert not F.is_filled(yld, {"percent": None})
    assert F.is_filled(yld, {"percent": 92, "mass": 30.5, "mass_unit": "mg"})
    nmr = F.ALL_FIELDS["nmr"]
    assert not F.is_filled(nmr, ["x", {"text": None}])
    assert F.is_filled(nmr, ["x", {"text": "δ 7.26"}])


def test_null_and_wrong_type_values_open_and_never_save_none(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    c.values["rf"] = {"value": "0.3", "eluent": "EtOAc", "visualization": "UV"}
    p.save_compound(c)
    folder = p.folder
    p.close()
    path = c.folder / "compound.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["values"]["yield"] = {"percent": "92", "mass": None, "mass_unit": "mg"}
    data["values"]["rf"]["value"] = None
    data["values"]["hrms"] = "x"
    data["values"]["nmr"] = ["x", {"nucleus": "1H", "label": None, "text": "δ 7.26"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    p = Project.open(folder)
    p.acquire_lock("Tester")
    c = p.compound(c.id)
    panel = panel_for(p, c)                       # opens (no crash)
    assert box(panel, "yield").editor.widgets["mass"].text() == ""
    assert box(panel, "rf").editor.widgets["value"].text() == ""
    assert len(box(panel, "nmr").editor.rows) == 1
    panel.save()
    values = stored(c)
    assert values["yield"]["mass"] == "" and values["rf"]["value"] == ""
    assert "None" not in json.dumps(values)
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- key names

def test_structure_hints_use_the_platform_keys(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import structure_widgets as sw
    monkeypatch.setattr(sw, "paste_key", lambda: "⌘V")
    monkeypatch.setattr(sw, "copy_key", lambda: "⌘C")
    monkeypatch.setattr(sw, "clipboard_picture_for", lambda project: None)
    p, c = new_project(tmp_path / "proj", structure=False)
    panel = panel_for(p, c)
    editor = box(panel, "structure").editor
    editor.refresh()
    assert "press ⌘V" in editor.image.text() and "Ctrl" not in editor.image.text()
    editor._paste_image()
    assert "press ⌘C" in editor.error.text()
    assert sw.copy_key()                          # (a real key name, e.g. Ctrl+C)
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- storage folder

def _private_editor(panel):
    return box(panel, "private_files").editor


def test_open_storage_folder_writes_nothing_in_a_read_only_project(qapp, tmp_path,
                                                                   monkeypatch):
    from si_assistant.ui import field_widgets as fw
    opened, told = [], []
    monkeypatch.setattr(fw.QDesktopServices, "openUrl", lambda url: opened.append(url))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "private_files"})
    folder = p.folder
    p.close()
    ro = Project.open(folder, read_only=True)
    c = ro.compound(c.id)
    panel = panel_for(ro, c)
    _private_editor(panel)._open_dir()
    assert not ro.private_dir(c).exists()
    assert told and not opened
    panel.deleteLater()
    ro.close()


def test_open_storage_folder_keeps_redo(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import field_widgets as fw
    opened = []
    monkeypatch.setattr(fw.QDesktopServices, "openUrl", lambda url: opened.append(url))
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "private_files"})
    p.enable_undo()
    with p.undo_step("Edit"):
        c.values["yield"] = {"percent": "80", "mass": "", "mass_unit": "mg"}
        p.save_compound(c)
    p.undo()
    c = p.compound(c.id)
    assert p.can_redo
    panel = panel_for(p, c)
    _private_editor(panel)._open_dir()
    assert p.private_dir(c).is_dir() and opened
    assert p.can_redo
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- handoffs from other groups

def _link_texts(layout) -> list[str]:
    from PySide6.QtWidgets import QPushButton
    return [layout.itemAt(i).widget().text() for i in range(layout.count())
            if isinstance(layout.itemAt(i).widget(), QPushButton)]


def test_file_buttons_show_an_ampersand_without_a_shortcut(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "private_files"})
    p.attach_compound_file(c, "files", picture(tmp_path / "A&B.png"))
    zipped = tmp_path / "A&B.zip"
    zipped.write_bytes(b"made up")
    p.add_private_item(c, zipped)
    panel = panel_for(p, c)
    files = _attachments(panel, "files")
    assert "A&&B.png" in _link_texts(files.rows_layout)
    assert files.row_widgets[0][0] == "A&B.png"            # (the file itself: as named)
    assert any(t.endswith("A&&B.zip") for t in _link_texts(_private_editor(panel).rows_layout))
    (c.folder / "files" / "A&B.png").unlink()
    files._build_rows()
    assert "⚠ A&&B.png (missing)" in _link_texts(files.rows_layout)
    panel.deleteLater()
    p.close()


def test_add_field_menu_shows_an_ampersand_in_a_custom_name(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    p.section(c.section_id).default_fields.append(
        {"key": "c-ab", "custom": {"label": "R&D code", "type": "short_text"}})
    panel = panel_for(p, c)
    panel._fill_add_menu()
    assert "R&&D code" in [a.text() for a in panel.add_menu.actions()]
    panel.deleteLater()
    p.close()


def _purification_project(tmp_path, places: bool):
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "purification"})
    gp = p.add_general_procedure(c.section_id, "General Procedure A")
    p.update_general_procedure(gp["id"], sentence="Made by {GP}, then purified by "
                               "{purification}." if places else "Made by {GP}.")
    c.values["purification"] = {"method": "flash column chromatography", "phase": "SiO₂",
                                "eluent": "", "sentence": "Typed."}
    p.save_compound(c)
    return p, c, gp


def test_printed_as_says_when_the_general_procedure_places_purification(qapp, tmp_path):
    p, c, gp = _purification_project(tmp_path, places=True)
    panel = panel_for(p, c)
    sentence = box(panel, "purification").editor.widgets["sentence"]
    assert sentence.placeholderText().startswith("The crude product was purified by ")
    chooser = box(panel, "procedure").editor.gp
    chooser.combo.setCurrentIndex(chooser.combo.findData(gp["id"]))   # chosen on this page
    panel.flush()
    assert sentence.placeholderText() == ("not used: “General Procedure A” places “flash "
                                          "column chromatography (SiO₂)” itself")
    assert "not reach the SI" in sentence.toolTip()
    assert sentence.isEnabled() and not sentence.isReadOnly()     # (still editable)
    chooser.combo.setCurrentIndex(0)                                # no general procedure
    panel.flush()
    assert sentence.placeholderText().startswith("The crude product was purified by ")
    assert sentence.toolTip().startswith("Printed at the end")
    panel.deleteLater()
    p.close()


def test_printed_as_is_used_when_the_general_procedure_does_not_place_it(qapp, tmp_path):
    p, c, gp = _purification_project(tmp_path, places=False)
    c.values["procedure"] = {"gp": gp["id"], "values": {}}
    p.save_compound(c)
    panel = panel_for(p, c)
    sentence = box(panel, "purification").editor.widgets["sentence"]
    assert sentence.placeholderText() == ("The crude product was purified by flash column "
                                          "chromatography (SiO₂).")
    panel.deleteLater()
    p.close()


def test_elemental_analysis_has_an_optional_formula():
    ea = F.ALL_FIELDS["ea"]
    assert [part.key for part in ea.parts] == ["calcd", "found", "formula"]
    assert ea.required_parts() == ("calcd", "found")
    assert F.is_filled(ea, {"calcd": "C, 65.1", "found": "C, 65.0", "formula": ""})
    assert not F.is_filled(ea, {"calcd": "", "found": "", "formula": "C12H15NO3"})


def test_elemental_analysis_formula_box_shows_the_structure(qapp, tmp_path):
    from si_assistant.ui.structure_widgets import formula_plain
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "ea"})
    panel = panel_for(p, c)
    editor = box(panel, "ea").editor
    formula = editor.widgets["formula"]
    assert formula.placeholderText() == f"auto: {formula_plain('C12H15NO3')}"
    assert formula.styleSheet() == ""
    formula.setText("C12H15NO3·0.25H2O")
    formula.textEdited.emit("C12H15NO3·0.25H2O")
    assert formula.styleSheet() == editor.MANUAL_STYLE           # typed: highlighted
    box(panel, "structure").editor.set_structure_text("CCO")
    for _ in range(3):
        qapp.processEvents()
    assert formula.placeholderText() == f"auto: {formula_plain('C2H6O')}"
    panel.deleteLater()
    p.close()


def test_elemental_analysis_formula_box_without_a_structure(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj", structure=False)
    p.add_field(c, {"key": "ea"})
    panel = panel_for(p, c)
    assert box(panel, "ea").editor.widgets["formula"].placeholderText() == \
        "e.g. C12H15NO·0.25H2O"
    panel.deleteLater()
    p.close()


def _type_m_formula(editor, text):
    editor.formula_edit.setText(text)
    editor.formula_edit.textEdited.emit(text)
    return editor.formula_preview.text()


def test_hrms_manual_formula_note_only_for_a_different_composition(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj", structure=False)
    p.set_structure(c, "[2H]C([2H])([2H])OC(=O)c1ccccc1")
    panel = panel_for(p, c)
    editor = box(panel, "hrms").editor
    same = _type_m_formula(editor, "C8H5[2H]3O2")             # the CD3 group written as [2H]
    assert "manual" in same and "structure gives" not in same
    other = _type_m_formula(editor, "C8H8O2")
    assert "structure gives" in other
    panel.deleteLater()
    p.close()


def test_hrms_editor_with_a_hand_edited_tolerance(qapp, tmp_path):
    from si_assistant.core import chem
    p, c = new_project(tmp_path / "proj")
    p.settings["hrms_tolerance_ppm"] = "abc"                  # (not a number: 5 ppm)
    mono = checks.hrms_calc(chem.mol_info(chem.parse_structure(M.SMILES)), "[M+H]+",
                            "", 5.0).monoisotopic.mz
    c.values["hrms"] = {"adduct": "[M+H]+", "found": f"{mono + 0.01:.4f}"}
    p.save_compound(c)
    panel = panel_for(p, c)                                   # opens (no crash)
    assert "±5 ppm tolerance" in box(panel, "hrms").editor.calc.text()
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- late handoffs

def test_has_unsaved_on_the_compound_page_and_general_procedures(qapp, tmp_path, monkeypatch):
    from si_assistant.ui.procedure_widgets import GeneralProceduresEditor
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    assert not panel.has_unsaved()
    percent = box(panel, "yield").editor.widgets["percent"]
    percent.setText("42")
    percent.textEdited.emit("42")
    assert panel.has_unsaved()
    panel.flush()
    assert not panel.has_unsaved()
    section = p.section(c.section_id)
    p.add_general_procedure(section.id)
    editor = GeneralProceduresEditor(p, section)
    assert not editor.has_unsaved()
    editor.title.setText("Synthesis of esters")
    editor._edited()
    assert editor.has_unsaved()
    editor.flush()
    assert not editor.has_unsaved()
    editor.deleteLater()
    panel.deleteLater()
    p.close()


def _write_generic_structure(p, c, smiles="*c1ccc(C(=O)OC)cc1"):
    """A structure.mol with a generic atom, as set_structure, an older version or a hand
    edit leaves it."""
    from rdkit import Chem
    p.structure_path(c).write_text(Chem.MolToMolBlock(Chem.MolFromSmiles(smiles)),
                                   encoding="utf-8")


def test_a_structure_with_a_generic_atom_is_shown_with_a_note(qapp, tmp_path, monkeypatch):
    from si_assistant.core import chem
    p, c = new_project(tmp_path / "proj", structure=False)
    _write_generic_structure(p, c)
    panel = panel_for(p, c)                                   # opens (no crash)
    assert "generic atom" in panel.formula_label.text()
    assert not panel.thumb.pixmap().isNull()                  # still drawn
    editor = box(panel, "structure").editor
    assert editor.smiles.text() == chem.canonical_smiles(chem.parse_structure(
        "*c1ccc(C(=O)OC)cc1"))
    assert "generic atom (R)" in editor.info.text() and "No structure" not in editor.image.text()
    assert not editor.image.pixmap().isNull()
    assert editor.buttons["Clear"].isEnabled()                # can be removed or replaced
    # Enter in the unchanged SMILES box stores nothing again.
    calls = []
    monkeypatch.setattr(p, "set_structure", lambda *a, **k: calls.append(a) or [])
    editor._apply_smiles()
    assert calls == []
    editor.smiles.setText("CCO")
    editor._apply_smiles()
    assert len(calls) == 1
    panel.deleteLater()
    p.close()


def test_generic_atom_warning_is_not_shown_twice_in_the_structure_box(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj", structure=False)
    _write_generic_structure(p, c)
    # The warning itself is still made (Warnings panel, dashboard)...
    assert any(w.key == checks.GENERIC_STRUCTURE_KEY
               for w in checks.active_warnings(p, c, p.id_report()))
    panel = panel_for(p, c)
    # ...but the Structure box only shows its own note, not the same words again.
    structure_box = box(panel, "structure")
    assert not any(w.key == checks.GENERIC_STRUCTURE_KEY for w in structure_box.warnings)
    assert "generic atom (R)" in structure_box.editor.info.text()
    # Folded up (▸), the box's one-line summary still has its ⚠ (the note is hidden then).
    structure_box.set_collapsed(True)
    try:
        assert "⚠" in structure_box.summary.text()
    finally:
        structure_box.set_collapsed(False)
    panel.deleteLater()
    p.close()


def test_generic_structure_is_drawn_from_the_projects_cached_read(qapp, tmp_path,
                                                                  monkeypatch):
    """The page header and the Structure box use the project's one cached read of
    structure.mol: no second read that could fail (or differ) on its own."""
    from si_assistant.core import project as project_mod
    p, c = new_project(tmp_path / "proj", structure=False)
    _write_generic_structure(p, c)
    panel = panel_for(p, c)
    editor = box(panel, "structure").editor
    smiles = editor.smiles.text()
    assert smiles

    real_read_text = project_mod.read_text

    def unreadable(path, *a, **k):   # structure.mol can't be read just now
        if Path(path).suffix == ".mol":
            raise OSError("file is not available just now")
        return real_read_text(path, *a, **k)
    monkeypatch.setattr(project_mod, "read_text", unreadable)
    panel._refresh_header()
    editor.refresh()
    assert editor.smiles.text() == smiles
    assert not panel.thumb.pixmap().isNull()
    panel.deleteLater()
    p.close()


def test_page_header_when_the_formula_cannot_be_worked_out(qapp, tmp_path, monkeypatch):
    from si_assistant.core import chem
    p, c = new_project(tmp_path / "proj")

    def no_formula(mol):
        raise chem.ChemError("This structure has a generic atom (R).")
    monkeypatch.setattr(chem, "mol_info", no_formula)
    panel = panel_for(p, c)                                   # opens (no crash)
    assert panel.formula_label.text() == "no formula (generic atom)"
    assert "generic atom (R)" in box(panel, "structure").editor.info.text()
    panel.deleteLater()
    p.close()


# ---------------------------------------------------------------------- review 2

def test_a_file_named_by_a_number_keeps_its_typed_caption(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    _two_file_fields(p, c, tmp_path)
    # A hand-edited entry whose file name is a number: shown as "7", given a figure number,
    # so what is typed in its boxes must be stored too.
    c.values["files"].append({"file": 7, "caption": "seven", "placement": "inline"})
    p.save_compound(c)
    panel = panel_for(p, c)
    files = _attachments(panel, "files")
    names = [n for n, _w in files.row_widgets]
    assert names == ["a.png", "z.png", "7"]
    caption = files.row_widgets[2][1]["caption"]
    caption.setText("changed caption")
    caption.textEdited.emit("changed caption")
    panel.flush()
    item = next(i for i in stored(c)["files"] if i["file"] == 7)
    assert item["caption"] == "changed caption"
    panel.deleteLater()
    p.close()


def test_a_private_file_named_by_a_number_keeps_its_note(qapp, tmp_path):
    p, c = new_project(tmp_path / "proj")
    p.add_field(c, {"key": "private_files"})
    c.values["private_files"] = [{"file": 7, "note": "old"}]
    p.save_compound(c)
    panel = panel_for(p, c)
    note = _private_editor(panel).notes["7"]
    note.setText("new note")
    note.textEdited.emit("new note")
    panel.flush()
    assert stored(c)["private_files"][0]["note"] == "new note"
    panel.deleteLater()
    p.close()


@pytest.mark.parametrize("value", [5, True, 2.5, "x", {"text": "δ 7.26"}, None])
def test_nmr_of_the_wrong_form_counts_as_empty(value):
    assert F.is_filled(F.ALL_FIELDS["nmr"], value) is False


def test_nmr_box_opens_with_a_value_of_the_wrong_form(qapp, tmp_path):
    from si_assistant.ui.field_widgets import NmrEditor
    p, c = new_project(tmp_path / "proj")
    c.values["nmr"] = 5                            # (a hand-edited compound.json)
    editor = NmrEditor(p, c, F.ALL_FIELDS["nmr"], lambda: None)   # opens (no crash)
    assert editor.rows == []
    editor.deleteLater()
    p.close()


def test_general_procedure_values_with_null_and_wrong_type(qapp, tmp_path):
    from si_assistant.core import procedures
    p, c = new_project(tmp_path / "proj")
    if "purification" not in c.field_keys:
        p.add_field(c, {"key": "purification"})
    c.values["scale"] = {"value": None, "unit": None}
    c.values["yield"] = {"percent": None}
    c.values["purification"] = {"method": "column", "phase": None, "eluent": None}
    gp = p.add_general_procedure(c.section_id)
    auto = procedures.auto_values(p, c, gp)
    assert "scale" not in auto and "yield" not in auto
    assert auto["purification"][0].text == "column"
    assert procedures.purification_sentence(c) == "The crude product was purified by column."
    assert procedures.purification_text_of({"method": "flash", "phase": None,
                                            "eluent": 5}) == "flash (5)"
    assert procedures.purification_text_of("x") == ""
    c.values["scale"] = {"value": 0.2, "unit": None}
    c.values["yield"] = {"percent": 92}
    auto = procedures.auto_values(p, c, gp)
    assert auto["scale"][0].text == "0.2 mmol" and auto["yield"][0].text == "92%"
    # Of the wrong form: no value (and the page still opens).
    c.values["scale"] = "0.2 mmol"
    c.values["yield"] = 92
    auto = procedures.auto_values(p, c, gp)
    assert "scale" not in auto and "yield" not in auto
    filled = procedures.fill("{scale} {yield} {x}", {"x": None, "yield": 5}, auto)
    assert "None" not in "".join(r.text for r in filled)
    c.values["procedure"] = {"gp": gp["id"], "values": {"scale": None}}
    assert procedures.scale_conflict(p, c) == ""
    c.values["procedure"] = {"gp": gp["id"], "values": {}}
    from si_assistant.ui.field_widgets import RichTextFieldEditor
    editor = RichTextFieldEditor(p, c, F.ALL_FIELDS["procedure"], lambda: None)  # (no crash)
    assert editor.gp is not None
    editor.deleteLater()
    p.close()


def _type_procedure(panel, text="Made-up procedure text."):
    editor = box(panel, "procedure").editor
    editor.editor.set_markup(text)
    editor.changed.emit()
    return editor


def test_an_action_stops_when_the_typing_cannot_be_saved(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import compound_panel as cp
    told = []
    monkeypatch.setattr(cp.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: told.append(a)))
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    _type_procedure(panel)
    real_write = p.write_compound_text

    def held(*a, **k):
        raise OSError("held by OneDrive (made up)")
    monkeypatch.setattr(p, "write_compound_text", held)
    new_key = next(k for k, d in F.ALL_FIELDS.items()
                   if k not in c.field_keys and d.kind == "parts")
    panel._add_field({"key": new_key})             # no exception escapes
    assert told and new_key not in c.field_keys
    assert box(panel, "procedure").editor.editor.markup().strip()   # still on screen
    assert panel.has_unsaved() and not panel.unsaved_label.isHidden()
    told.clear()
    panel._remove_field("yield")
    assert told and "yield" in c.field_keys
    panel.set_all_collapsed(True)                  # goes on: folding loses nothing
    assert all(b.collapsed for b in panel.boxes)
    # A field's own action (here: a structure typed) stops too.
    editor = box(panel, "structure").editor
    calls = []
    monkeypatch.setattr(p, "set_structure", lambda *a, **k: calls.append(a) or [])
    told.clear()
    editor.set_structure_text("CCO")
    assert told and calls == []
    # Once the file can be written again, the typing is saved.
    monkeypatch.setattr(p, "write_compound_text", real_write)
    panel.flush()
    assert "Made-up procedure text." in p.read_compound_text(c, "procedure")
    assert panel.unsaved_label.isHidden()
    panel.deleteLater()
    p.close()


def test_dismissing_a_warning_that_cannot_be_saved_is_said(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import compound_panel as cp
    from si_assistant.ui import warnings_panel
    told = []
    monkeypatch.setattr(cp.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: told.append(a)))
    monkeypatch.setattr(warnings_panel, "ask_reason", lambda parent, message: "checked")
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    warning = checks.Warning("yield", "yield", "A made-up warning.", "yield:made-up", "x")

    def held(*a, **k):
        raise OSError("held (made up)")
    monkeypatch.setattr(p, "save_compound", held)
    panel._dismiss_warning(warning)                # no exception escapes
    assert told
    panel.deleteLater()
    p.close()


def test_the_list_keeps_showing_the_procedure_being_edited_after_a_click(
        qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from si_assistant.ui import procedure_widgets as pw
    monkeypatch.setattr(pw.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    p, c = new_project(tmp_path / "proj")
    section = p.section(c.section_id)
    a = p.add_general_procedure(section.id)
    p.update_general_procedure(a["id"], name="GP A")
    b = p.add_general_procedure(section.id)
    p.update_general_procedure(b["id"], name="GP B")
    editor = pw.GeneralProceduresEditor(p, section)
    editor.resize(800, 500)
    editor.show()
    qapp.processEvents()
    assert editor.current == a["id"]
    editor.title.setText("typed")
    editor._edited()

    tried = []

    def held(*args, **kwargs):
        tried.append(1)
        raise OSError("held (made up)")
    monkeypatch.setattr(p, "update_general_procedure", held)
    rect = editor.list.visualItemRect(editor.list.item(1))
    QTest.mouseClick(editor.list.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())
    for _ in range(3):
        qapp.processEvents()
    assert tried                                   # (the click asked to leave procedure A)
    assert editor.current == a["id"] and editor.name.text() == "GP A"
    assert editor.list.currentRow() == 0
    assert [i.row() for i in editor.list.selectedIndexes()] == [0]
    editor.close()
    editor.deleteLater()
    p.close()


def test_enter_on_the_smiles_box_stores_nothing_when_the_file_cannot_be_read(
        qapp, tmp_path, monkeypatch):
    p, c = new_project(tmp_path / "proj")
    panel = panel_for(p, c)
    editor = box(panel, "structure").editor
    calls = []
    monkeypatch.setattr(p, "set_structure", lambda *a, **k: calls.append(a) or [])

    def offline(compound, strict=False):
        if strict:
            raise OSError("online-only file (made up)")
        return None
    monkeypatch.setattr(p, "structure_for_drawing", offline)
    monkeypatch.setattr(p, "read_structure", offline)
    editor.smiles.setText(M.SMILES)
    editor._apply_smiles()
    assert calls == []
    assert "read just now" in editor.error.text()
    panel.deleteLater()
    p.close()
