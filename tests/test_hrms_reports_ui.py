"""HRMS report files on screen (SPEC §13n.3): the list in the HRMS field (add, paste, drop,
remove to the Trash, undo), the Found box filled "from the report", the printing choices
(Spectra section, compound, spectra page), the "HRMS reports" tree page and the Compile
window. All files are made up (tests/ms_samples.py)."""

import json

import pytest
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox

from si_assistant.core import checks
from si_assistant.core import ms_reports as R
from si_assistant.core.project import Project
from tests import ms_samples as M


def new_project(tmp_path):
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.ensure_spectra_section()
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="made-up ester")
    p.set_structure(c, M.SMILES)
    return p, c


def csv_file(folder, name="3a peaks.csv", offset=1.2):
    path = folder / name
    path.write_text(M.csv_text(M.sample_peaks(offset)), encoding="utf-8")
    return path


@pytest.fixture
def page(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, c = new_project(tmp_path / "proj")
    p.enable_undo()
    panel = CompoundPanel(p, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "hrms")
    yield p, c, panel, editor, tmp_path
    panel.deleteLater()
    p.close()


def stored(c) -> dict:
    return json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))["values"]["hrms"]


def test_adding_a_report_fills_found(page):
    p, c, panel, editor, tmp = page
    peaks = M.ion_peaks()
    editor.reports.add_paths([csv_file(tmp)])
    expected = R.format_mz(peaks[0].mz * (1 + 1.2e-6))
    assert editor.widgets["found"].text() == expected
    assert "from the report 3a peaks.csv" in editor.found_note.text()
    assert editor.reports.row_names == ["3a peaks.csv"]
    assert "peak list: 6 m/z values" in editor.reports.rows.itemAtPosition(0, 1).widget().text()
    panel.save()   # (the page's own save keeps the reports and the provenance)
    value = stored(c)
    assert value["found"] == expected and value["found_from"]["file"] == "3a peaks.csv"
    assert value["reports"] == [{"file": "3a peaks.csv", "kind": "file"}]


def test_typed_value_is_kept_and_warned(page):
    p, c, panel, editor, tmp = page
    editor.reports.add_paths([csv_file(tmp)])
    found = editor.widgets["found"]
    found.setText("222.1300")
    found.textEdited.emit("222.1300")
    assert "Your value is kept" in editor.found_note.text()
    panel.save()
    assert stored(c)["found"] == "222.1300" and "found_from" not in stored(c)
    keys = [w.key for w in checks.active_warnings(p, c, p.id_report())]
    assert "hrms:report-differs" in keys
    # Changing the adduct never changes a value typed by hand.
    adduct = editor.widgets["adduct"]
    adduct.setCurrentIndex(adduct.findData("[M+Na]+"))
    assert found.text() == "222.1300"


def test_report_value_follows_the_adduct(page):
    p, c, panel, editor, tmp = page
    editor.reports.add_paths([csv_file(tmp)])
    adduct = editor.widgets["adduct"]
    adduct.setCurrentIndex(adduct.findData("[M+K]+"))       # no such peak in the report
    assert editor.widgets["found"].text() == ""
    adduct.setCurrentIndex(adduct.findData("[M+H]+"))
    assert editor.widgets["found"].text() != ""
    adduct.setCurrentIndex(adduct.findData("[M+Na]+"))       # the report has the Na peak
    assert "from the report" in editor.found_note.text()


def test_remove_to_trash_and_undo(page):
    p, c, panel, editor, tmp = page
    editor.reports.add_paths([csv_file(tmp)])
    editor.reports.remove_now("3a peaks.csv")
    assert editor.widgets["found"].text() == ""
    assert not (c.folder / "hrms" / "3a peaks.csv").exists()
    assert any(e.name == "3a peaks.csv" for e in p.list_trash())
    assert editor.reports.row_names == []
    p.undo()
    c2 = p.compound(c.id)
    assert (c2.folder / "hrms" / "3a peaks.csv").exists() and R.report_items(c2)


def test_paste_and_drop_copied_files(page, monkeypatch):
    p, c, panel, editor, tmp = page
    a, b = csv_file(tmp, "a.csv"), M.agilent_like_pdf(tmp / "b.pdf")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(a))])
    QGuiApplication.clipboard().setMimeData(mime)
    editor.reports.paste()
    assert editor.reports.row_names == ["a.csv"]

    class Drop:
        def __init__(self, paths):
            self.data = QMimeData()
            self.data.setUrls([QUrl.fromLocalFile(str(x)) for x in paths])
            self.accepted = False

        def mimeData(self):  # noqa: N802
            return self.data

        def acceptProposedAction(self):  # noqa: N802
            self.accepted = True

    event = Drop([b])
    editor.reports.dropEvent(event)
    assert event.accepted and editor.reports.row_names == ["a.csv", "b.pdf"]
    # The PDF's measured column now wins over the CSV's unmarked value.
    assert stored(c)["found_from"]["file"] == "b.pdf"


def test_print_choice_in_the_field(page):
    p, c, panel, editor, tmp = page
    editor.reports.add_paths([csv_file(tmp)])
    box = editor.reports.print_box
    assert box.isVisibleTo(editor) and box.currentIndex() == 0
    assert "now: don’t print" in box.itemText(0)
    box.setCurrentIndex(1)
    box.activated.emit(1)
    assert stored(c)["print_reports"] is True and R.prints_reports(p, c)
    panel.save()
    assert stored(c)["print_reports"] is True   # (kept by the page's own save)
    box.setCurrentIndex(0)
    box.activated.emit(0)
    assert "print_reports" not in stored(c)


def test_read_only_project_shows_but_does_not_fill(qapp, tmp_path):
    from si_assistant.ui.compound_panel import CompoundPanel
    p, c = new_project(tmp_path)
    p.add_hrms_report(c, csv_file(tmp_path))
    c.values["hrms"]["found"] = ""
    c.values["hrms"].pop("found_from")
    p.save_compound(c)
    p.close()
    ro = Project.open(p.folder, read_only=True)
    c = ro.all_compounds()[0]
    panel = CompoundPanel(ro, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "hrms")
    assert editor.widgets["found"].text() == ""
    assert not editor.reports.add_btn.isEnabled()
    panel.deleteLater()


def test_spectra_section_and_page_options(qapp, tmp_path):
    from si_assistant.ui.spectra_page import SpectraPagePanel
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, c = new_project(tmp_path)
    p.add_hrms_report(c, csv_file(tmp_path))
    panel = SpectraSectionPanel(p, p.spectra_section)
    assert panel.values()["hrms_reports"] is False
    panel.hrms_reports.setChecked(True)
    panel.flush()
    assert p.spectra_defaults()["hrms_reports"] is True and R.prints_reports(p, c)
    page = SpectraPagePanel(p, c)
    assert page.hrms_row.isVisibleTo(page) and "now: print" in page.hrms_print.itemText(0)
    page.hrms_print.setCurrentIndex(2)
    page.hrms_print.activated.emit(2)
    assert c.values["hrms"]["print_reports"] is False and not R.prints_reports(p, c)
    panel.deleteLater()
    page.deleteLater()
    p.close()


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    p, c = new_project(tmp_path / "proj")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield win, mw
    win.close_project()
    win.close()


def test_tree_item_page_and_preview(window, tmp_path):
    from si_assistant.ui.hrms_reports_ui import HrmsReportsPage
    win, mw = window
    assert win._find_item(mw.HRMS_ITEM, mw.HRMS_ITEM) is None   # no reports yet
    c = win.project.all_compounds()[0]
    win.select("compound", c.id)
    editor = next(b.editor for b in win.panel.boxes if b.defn.key == "hrms")
    editor.reports.add_paths([csv_file(tmp_path)])
    item = win._find_item(mw.HRMS_ITEM, mw.HRMS_ITEM)
    assert item is not None and item.parent().data(0, mw.KIND_ROLE) == "section"
    win.select(mw.HRMS_ITEM, mw.HRMS_ITEM)
    assert isinstance(win.panel, HrmsReportsPage)
    assert win._preview_target() == ("hrms", "")
    top = win.panel.tree.topLevelItem(0)
    assert top.text(1).startswith("no") and top.child(0).text(0) == "3a peaks.csv"
    assert not win.act_delete.isEnabled() and not win.act_rename.isEnabled()


def test_compile_window_lists_the_reports(qapp, tmp_path):
    from si_assistant.core import front
    from si_assistant.ui.compile_ui import ExportTree
    p, c = new_project(tmp_path)
    p.add_hrms_report(c, csv_file(tmp_path))
    assert front.HRMS_KEY not in ExportTree(p, set())._items   # not printed: not listed
    p.set_hrms_print(c, True)
    tree = ExportTree(p, {front.HRMS_KEY})
    assert front.HRMS_KEY in tree._items and front.HRMS_KEY in tree.excluded()
    p.close()
