"""The project dashboard (SPEC §13k.3): summary numbers, status colours (also used by the
section dashboards), the grid of every compound, and the page in the main window."""

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QColor

from si_assistant.core import checks
from si_assistant.core import dashboard as D
from si_assistant.core.project import Project


@pytest.fixture
def two_sections(project):
    """A top-level Data section with 2 compounds and one inside a custom section with 3."""
    a = project.add_section("data", "Products")
    holder = project.add_section("text", "Substrates")
    b = project.add_section("data", "Amides", parent_id=holder.id)
    project.set_id_table(a.id, ["3a", "3b"])
    project.set_id_table(b.id, ["1a", "1b", "1c"])
    # Only Structure and Yield are required in "Products"; the defaults in "Amides".
    project.set_default_fields(a.id, [{"key": "structure", "required": True},
                                      {"key": "yield", "required": True},
                                      {"key": "rf", "required": False}])
    done = project.add_compound(a.id, name="Done")
    project.set_structure(done, "CCO")
    done.values["yield"] = {"percent": "90", "mass": "10", "mass_unit": "mg"}
    project.save_compound(done)
    half = project.add_compound(a.id, name="Half")
    project.set_structure(half, "CCN")
    for i in range(3):
        c = project.add_compound(b.id, name=f"Amide {i}")
        if i == 0:
            project.set_flag(c, "yield", True, "check")
            project.set_flag(c, "nmr", True)
    return project, a, b


def test_summary_numbers(two_sections):
    project, a, b = two_sections
    summary = D.summarize(project)
    first, second = summary.sections
    assert (first.section.id, first.path) == (a.id, "Products")
    assert second.path == "Substrates › Amides"
    assert (first.compounds, first.complete, first.filled, first.required) == (2, 1, 3, 4)
    assert first.fraction == 0.5 and first.field_fraction == 0.75
    assert (second.compounds, second.complete, second.flags) == (3, 0, 2)
    assert (summary.compounds, summary.complete, summary.flags) == (5, 1, 2)
    assert summary.text().startswith("1 / 5 compounds complete · ")
    # Missing required data by field: "Half" lacks its yield; each amide lacks everything.
    missing = dict(summary.missing)
    assert missing["Yield"] == 4 and missing["Structure"] == 3
    assert missing["Experimental procedure"] == 3
    # NMR and spectra are counted per nucleus (empty ¹H and ¹³C reports, no spectra).
    assert missing["¹H NMR"] == 3 and missing["¹³C NMR"] == 3
    assert missing["¹H spectrum"] == 3
    assert "NMR" not in missing
    # In the order of the fields, not by count.
    labels = [label for label, _n in summary.missing]
    assert labels.index("Structure") < labels.index("Yield") < labels.index("¹H NMR")


def test_warnings_by_type_match_the_checks(two_sections):
    project, a, _b = two_sections
    half = a.compounds[1]
    project.add_field(half, {"key": "hrms"})
    half.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "999.0"}
    project.save_compound(half)
    b2 = project.add_compound(a.id)
    b2.data["id_override"] = "3a"     # the same ID as "Done": both get an ID warning
    project.save_compound(b2)
    summary = D.summarize(project)
    found = dict(summary.warnings)
    report = project.id_report()
    expected = {}
    for c in project.all_compounds():
        for w in checks.active_warnings(project, c, report):
            label = D.CHECK_LABELS[w.check]
            expected[label] = expected.get(label, 0) + 1
    assert found == expected
    assert found["HRMS"] >= 1 and found["Compound IDs"] == 2
    assert summary.warning_count == sum(expected.values())
    # Project-wide warnings (e.g. two figures with the same label) are counted too.
    pw = [checks.ProjectWarning("label", "same label", ("section", a.id))]
    assert dict(D.summarize(project, project_warnings=pw).warnings)["Duplicate labels"] == 1


def test_missing_references_are_one_type_wherever_they_are(two_sections):
    """A reference missing from the References list is "Missing references" whether it is
    cited in a compound's text or in a custom section's (a project-wide warning)."""
    project, a, _b = two_sections
    project.write_front_text("general", "As reported @cite{r-none00}.")
    pw = checks.project_warnings(project)
    assert [w.check for w in pw if "cites a reference" in w.message] == ["citation"]
    found = dict(D.summarize(project).warnings)
    assert found["Missing references"] == 1 and "Deleted mentions" not in found


def test_percentages_never_round_up_to_done():
    assert D.percent(1199, 1200) == 99 and D.percent(1200, 1200) == 100
    assert D.percent(0, 5) == 0 and D.percent(1, 3) == 33 and D.percent(0, 0) == 100
    assert D.share_percent(199, 200) == 99 and D.share_percent(1, 200) == 1
    assert D.share_percent(5, 5) == 100 and D.share_percent(0, 5) == 0
    assert D.share_percent(1, 4) == 25 and D.share_percent(3, 0) == 0
    s = D.ProjectSummary([D.SectionProgress(None, "A", 150, 149, 1199, 1200, 0, 0)], [], [], 0)
    assert "99% of required fields filled" in s.text()


def test_hand_edited_view_settings_do_not_break_the_grid(project):
    a = project.add_section("data", "Products")
    project.add_compound(a.id, name="One")
    every = D.available_columns(a)
    for bad in (5, "yield", None, {"x": 1}):
        a.data["dashboard"] = {"columns": bad}
        assert D.chosen_columns(a) == every
    a.data["dashboard"] = {"columns": ["yield", 7, None, "nonsense"]}
    assert D.chosen_columns(a) == ["yield"]
    a.data["dashboard"] = ["not", "a", "dict"]
    assert D.chosen_columns(a) == every and not D.is_split(a)
    project.settings[D.PROJECT_SETTING] = {"columns": 5, "filter": None, "chart": 7}
    assert D.chosen_columns(D.ProjectScope(project)) == D.available_columns(D.ProjectScope(project))
    assert D.chart_type(project.settings) == "bars"


def test_an_empty_project(project):
    summary = D.summarize(project)
    assert summary.sections == [] and summary.missing == [] and summary.compounds == 0
    assert summary.text() == "0 / 0 compounds complete · 0 warnings · 0 flags"


def test_status_colours_are_stored_only_when_changed(project):
    settings = project.settings
    assert D.status_colours(settings) == D.DEFAULT_STATUS_COLOURS
    D.store_status_colours(settings, {**D.DEFAULT_STATUS_COLOURS, "filled": "#2A78D6",
                                      "missing": "", "flagged": "not a colour"})
    assert settings[D.STATUS_SETTING] == {"filled": "#2a78d6", "missing": ""}
    project.save_settings()
    again = Project.open(project.folder).settings
    colours = D.status_colours(again)
    assert colours["filled"] == "#2a78d6" and colours["missing"] == ""
    assert colours["flagged"] == D.DEFAULT_STATUS_COLOURS["flagged"]
    # Hand-edited nonsense in project.json is ignored, colour by colour.
    again[D.STATUS_SETTING] = {"warning": "yellow", "not_used": "#123456"}
    assert D.status_colours(again)["warning"] == D.DEFAULT_STATUS_COLOURS["warning"]
    assert D.status_colours(again)["not_used"] == "#123456"
    D.store_status_colours(settings, D.DEFAULT_STATUS_COLOURS)
    assert D.STATUS_SETTING not in settings


def test_cell_status_order():
    assert D.cell_status(D.Cell(D.FILLED)) == D.FILLED
    assert D.cell_status(D.Cell(D.MISSING, flag={"comment": ""})) == D.FLAGGED
    assert D.cell_status(D.Cell(D.MISSING, flag={}, warnings=["x"])) == D.WARNING


def test_section_dashboards_use_the_status_colours(qapp, two_sections):
    from si_assistant.ui.dashboard_view import DashboardView, cell_colour
    project, a, _b = two_sections
    project.settings[D.STATUS_SETTING] = {"filled": "#0000ff", "missing": ""}
    view = DashboardView(project, a)
    view.refresh()
    headers = [view.table.horizontalHeaderItem(i).text() for i in range(view.table.columnCount())]
    col = headers.index("Yield")
    filled = view.table.item(0, col)        # "Done": yield filled
    missing = view.table.item(1, col)       # "Half": yield required and empty
    assert filled.text().startswith("✔")
    assert filled.background().color() == cell_colour("#0000ff", D.FILLED)
    assert filled.background().color().alpha() == D.CELL_ALPHA[D.FILLED]
    assert missing.text().startswith("✖")
    assert missing.background().style() == Qt.BrushStyle.NoBrush   # "no colour", icon kept
    assert "#" in view.legend.text() and "✖" in view.legend.text()


def test_project_grid_lists_every_compound_with_its_section(qapp, two_sections):
    from si_assistant.ui.dashboard_view import CID_ROLE
    from si_assistant.ui.project_dashboard import ProjectGridView
    project, a, b = two_sections
    project.add_field(b.compounds[0], {"key": "mp"})   # a field only one section uses
    grid = ProjectGridView(project)
    grid.refresh()
    assert grid.table.rowCount() == 5
    headers = [grid.table.horizontalHeaderItem(i).text() for i in range(grid.table.columnCount())]
    assert headers[:5] == ["ID", "Structure", "Name", "Section", "Required"]
    assert grid.table.item(0, 3).text() == "Products"
    assert grid.table.item(2, 3).text() == "Substrates › Amides"
    assert grid.table.item(2, 0).data(CID_ROLE) == b.compounds[0].id
    mp = headers.index("Melting point")
    assert grid.table.item(0, mp).text() == "—"           # Products' compounds don't use it
    assert not grid.new_btn.isVisibleTo(grid)
    # The column choice and filter are remembered in project.json.
    grid._toggle_column("rf", False)
    grid.filter_box.setCurrentIndex(grid.filter_box.findData("flagged"))
    assert grid.table.rowCount() == 1
    saved = Project.open(project.folder).settings[D.PROJECT_SETTING]
    assert "rf" not in saved["columns"] and saved["filter"] == "flagged"
    assert "dashboard" not in Project.open(project.folder).section(a.id).data


def test_chart_bars_donut_and_hover(qapp):
    from si_assistant.ui.project_dashboard import MAX_SLICES, Chart, ChartItem
    chart = Chart()
    chart.resize(400, 200)
    items = [ChartItem(f"Field {i}", i + 1, QColor("#e74c3c")) for i in range(10)]
    chart.set_items(items + [ChartItem("Nothing", 0)], "bars")
    assert len(chart.items) == 10                       # zeros are left out
    chart.grab()                                        # paints, recording what is where
    assert chart.item_at(QPointF(5, 10)).label == "Field 9"     # largest bar first
    chart.set_mode("pie")
    slices = chart._slices()
    assert len(slices) == MAX_SLICES and slices[-1].label == "Other"
    assert sum(s.value for s in slices) == sum(i.value for i in items)
    chart.grab()
    centre, outer, inner, *_ = chart._ring
    # Just right of 12 o'clock, in the ring: the first slice.
    hit = chart.item_at(QPointF(centre.x() + 2, centre.y() - (outer + inner) / 2))
    assert hit is slices[0]
    assert chart.item_at(centre) is None                 # the hole
    chart.keep_zero = True
    chart.set_items([ChartItem("Complete", 0), ChartItem("Not complete", 3)], "bars")
    assert [i.label for i in chart.items] == ["Complete", "Not complete"]
    chart.set_items([ChartItem("Complete", 0)])
    assert chart.items == []                            # all zero: "nothing to show"


def test_missing_data_is_shown_against_the_compounds(qapp):
    """Missing data: "27 of 28 compounds", not a share of all the missing items (a compound
    can miss several fields, so the items do not add up to a whole)."""
    from si_assistant.ui.project_dashboard import Chart, ChartItem
    chart = Chart()
    chart.resize(500, 220)
    proc, scale = ChartItem("Experimental procedure", 27), ChartItem("Reaction scale", 2)
    chart.denominator = 28
    chart.set_items([proc, scale], "pie")
    assert chart.share_text(proc) == "27 of 28"
    assert chart.tip_text(scale) == "Reaction scale: 2 of 28 compounds (7%)"
    chart.grab()
    # Without a count of compounds (e.g. warnings by type): the share of the total.
    chart.denominator = 0
    assert chart.share_text(proc) == "27  (93%)"
    assert chart.tip_text(proc).startswith("Experimental procedure: 27 (93% of the total)")
    # The folded "Other" slice is never "n of 28" (it can add up to more than 28).
    chart.denominator = 28
    chart.set_items([ChartItem(f"Field {i}", 20) for i in range(10)], "pie")
    other = chart._slices()[-1]
    assert other.label == "Other" and chart.share_text(other) == "60"
    assert "Field" in chart.tip_text(other) and "60 in all" in chart.tip_text(other)


def test_names_are_shown_as_typed(qapp, project):
    """Section names and the title are never read as HTML ("ee < 90%", "Ar<sub>F</sub>")."""
    from PySide6.QtGui import QTextDocument
    from si_assistant.ui.project_dashboard import ProjectDashboard
    project.settings["title"] = "Ar<sub>F</sub> & amides"
    holder = project.add_section("text", "Low ee <90% & side products")
    a = project.add_section("data", "ee<90 & <b>x", parent_id=holder.id)
    project.add_compound(a.id, name="One")
    dash = ProjectDashboard(project)
    assert dash.title.textFormat() == Qt.TextFormat.PlainText
    assert dash.title.text() == "Ar<sub>F</sub> & amides"
    from PySide6.QtWidgets import QLabel
    links = [w for w in dash.sections_widget.findChildren(QLabel) if "<a " in w.text()]
    doc = QTextDocument()
    doc.setHtml(links[0].text())
    assert doc.toPlainText().endswith("ee<90 & <b>x")       # (the outer name may be left out)
    tip = QTextDocument()
    tip.setHtml(links[0].toolTip())
    assert tip.toPlainText() == "Open “Low ee <90% & side products › ee<90 & <b>x”"
    # The link still opens the section.
    opened = []
    dash.open_section.connect(opened.append)
    links[0].linkActivated.emit(a.id)
    assert opened == [a.id]


def test_short_path():
    from si_assistant.ui.project_dashboard import short_path

    class Metrics:   # one pixel per character
        def horizontalAdvance(self, text):  # noqa: N802
            return len(text)

        def elidedText(self, text, _mode, width):  # noqa: N802
            return text if len(text) <= width else text[:width - 1] + "…"
    fm = Metrics()
    path = "Outer › Middle › Amides"
    assert short_path(path, fm, 40) == path
    assert short_path(path, fm, 20) == "… › Middle › Amides"
    assert short_path(path, fm, 12) == "… › Amides"
    assert short_path(path, fm, 8) == "… › Ami…"
    assert short_path("Amides of acids", fm, 8) == "Amides …"


def test_long_section_paths_and_narrow_windows(qapp, project):
    """A long nested path is shortened at the front, and the boxes stack in a narrow
    window, so the page never needs scrolling sideways."""
    from PySide6.QtWidgets import QBoxLayout, QLabel
    from si_assistant.ui.project_dashboard import SECTION_NAME_WIDTH, ProjectDashboard
    outer = project.add_section("text", "Synthesis of the Amide Substrates")
    middle = project.add_section("text", "Tertiary Amides from Acid Chlorides",
                                 parent_id=outer.id)
    data = project.add_section("data", "Cyclic Amine Derived Amides", parent_id=middle.id)
    project.add_compound(data.id, name="One")
    dash = ProjectDashboard(project)
    link = next(w for w in dash.sections_widget.findChildren(QLabel) if "<a " in w.text())
    assert "Synthesis" not in link.text() and "…" in link.text()
    assert link.sizeHint().width() <= SECTION_NAME_WIDTH + 20
    assert "Synthesis of the Amide Substrates" in link.toolTip()
    dash._arrange(500)
    assert all(row.direction() == QBoxLayout.Direction.TopToBottom for row, *_ in dash._rows)
    assert dash.overview.minimumSizeHint().width() <= 500
    dash._arrange(1400)
    assert all(row.direction() == QBoxLayout.Direction.LeftToRight for row, *_ in dash._rows)


def test_status_colours_dialog(qapp, project):
    from si_assistant.ui.project_dashboard import StatusColoursDialog
    dlg = StatusColoursDialog(project)
    assert dlg.colours() == D.DEFAULT_STATUS_COLOURS
    dlg.buttons["filled"].set_colour("#2a78d6")
    dlg.none_boxes["not_used"].setChecked(True)
    assert not dlg.buttons["not_used"].isEnabled()
    assert dlg.colours()["filled"] == "#2a78d6" and dlg.colours()["not_used"] == ""
    dlg.reset()
    assert dlg.colours() == D.DEFAULT_STATUS_COLOURS


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    wins = []

    def make(p):
        p.close()
        win = mw.MainWindow()
        win.prefs.user_name = "Tester"
        assert win.open_project(p.folder)
        wins.append(win)
        return win
    yield make
    for win in wins:
        win.close_project()
        win.close()


def test_project_item_shows_the_dashboard(window, two_sections, qapp):
    from si_assistant.ui.compound_panel import CompoundPanel
    from si_assistant.ui.data_panel import DataSectionPanel
    from si_assistant.ui.dashboard_view import KEY_ROLE
    from si_assistant.ui.project_dashboard import ProjectDashboard
    project, a, b = two_sections
    project.settings["title"] = "My SI"
    project.save_settings()
    win = window(project)
    top = win.tree.topLevelItem(0)
    assert top.text(0) == "My SI" and win.tree.topLevelItem(1).text(0) == "Front matter"
    assert not top.flags() & Qt.ItemFlag.ItemIsDragEnabled
    win.select("project", "project")
    dash = win.panel
    assert isinstance(dash, ProjectDashboard)
    assert win.preview_pane.isHidden()                     # no preview for this page
    for act in (win.act_delete, win.act_rename, win.act_up, win.act_down, win.act_preview):
        assert not act.isEnabled()
    win.delete_item()
    assert win.tree.topLevelItem(0).text(0) == "My SI"
    assert dash.headline.text().startswith("1 / 5 compounds complete")
    assert len(dash.overall.items) == 2 and dash.missing.items and dash.problems.items
    assert dash.problems.items[-1].label == D.FLAGS_LABEL and dash.problems.items[-1].value == 2

    # Bars or donuts, remembered in the project.
    dash.chart_box.setCurrentIndex(dash.chart_box.findData("pie"))
    assert dash.missing.mode == "pie"
    assert Project.open(win.project.folder).settings[D.PROJECT_SETTING]["chart"] == "pie"

    # A section's name opens it; a grid cell opens that compound at that field.
    dash.open_section.emit(b.id)
    qapp.processEvents()
    assert isinstance(win.panel, DataSectionPanel) and win.panel.section.id == b.id
    win.select("project", "project")
    grid = win.panel.grid
    headers = [grid.table.horizontalHeaderItem(i).text() for i in range(grid.table.columnCount())]
    col = headers.index("Yield")
    assert grid.table.item(1, col).data(KEY_ROLE) == "yield"
    grid._cell_clicked(1, col)
    qapp.processEvents()
    assert isinstance(win.panel, CompoundPanel) and win.panel.compound.name == "Half"


def test_a_new_title_renames_the_project_item(window, project):
    win = window(project)
    assert win.tree.topLevelItem(0).text(0) == "Test SI"
    win.select("front", "cover")
    win.panel.title_edit.setText("Enamides")
    win.panel.title_edit.textEdited.emit("Enamides")
    win.select("front", "toc")                 # switching away saves
    assert win.tree.topLevelItem(0).text(0) == "Enamides"


def test_sections_dropped_on_the_project_item_go_first(window, project):
    from si_assistant.ui.main_window import PROJECT_ITEM
    project.add_section("text", "One")
    win = window(project)
    project_item = win._find_item(PROJECT_ITEM, PROJECT_ITEM)
    one = win._find_item("section", win.project.top_sections[0].id)
    spectra = win._find_item("section", win.project.spectra_section.id)
    assert win.tree.section_index(one) == 0 and win.tree.section_index(spectra) == 1
    from PySide6.QtWidgets import QAbstractItemView
    spot = win.tree._section_drop(spectra, project_item,
                                  QAbstractItemView.DropIndicatorPosition.OnItem)
    assert spot == ("section", "", 0)


def test_colours_button_changes_the_section_dashboards(window, two_sections, monkeypatch):
    from si_assistant.ui import project_dashboard as PD
    from si_assistant.ui.dashboard_view import cell_colour
    project, a, _b = two_sections
    win = window(project)
    win.select("project", "project")

    class FakeDialog:
        def __init__(self, *args):
            pass

        def exec(self):
            return PD.QDialog.DialogCode.Accepted

        def colours(self):
            return {**D.DEFAULT_STATUS_COLOURS, "filled": "#00ff00"}
    monkeypatch.setattr(PD, "StatusColoursDialog", FakeDialog)
    win.panel.choose_colours()
    assert Project.open(win.project.folder).settings[D.STATUS_SETTING] == {"filled": "#00ff00"}
    win.select("section", a.id)
    dash = win.panel.dashboard
    headers = [dash.table.horizontalHeaderItem(i).text() for i in range(dash.table.columnCount())]
    item = dash.table.item(0, headers.index("Yield"))
    assert item.background().color() == cell_colour("#00ff00", D.FILLED)
