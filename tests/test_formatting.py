"""Text and table formatting (core/formatting.py, Project settings ▸ Text and tables):
table text sizes (selected cells, header rows, document defaults), column widths,
shrink-to-fit, caption sizes (document default and per caption), line and paragraph
spacing. Nothing set = printed exactly as before."""

import json
import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.document import Builder, CompileOptions
from si_assistant.core import formatting
from si_assistant.core import scheme_editor as se
from si_assistant.core.tables import Cell, Table
from tests.test_compile_pdf import compile_sample, sample  # noqa: F401  (fixture)


# ---------------------------------------------------------------------- storing

def test_nothing_set_stores_and_prints_nothing():
    s = {}
    assert all(v is None for k, v in formatting.formatting(s).items()
               if k != "table_header_bold")
    assert formatting.typst_rules(s) == ""
    formatting.store_formatting(s, formatting.formatting(s))
    assert s == {}
    # Line spacing 1 and "header not bold" are the style's own: not stored.
    formatting.store_formatting(s, {"line_spacing": 1.0, "table_header_bold": False})
    assert s == {}


def test_values_are_cleaned_and_kept():
    s = {}
    formatting.store_formatting(s, {"caption_size": 9, "table_size": "8.5",
                                    "table_header_bold": True, "line_spacing": 1.5,
                                    "space_after": 6, "table_notes_size": 999})
    assert s["formatting"] == {"caption_size": 9.0, "table_size": 8.5,
                               "table_header_bold": True, "line_spacing": 1.5,
                               "space_after": 6.0, "table_notes_size": 30.0}
    # Hand-edited nonsense is ignored rather than breaking the document.
    v = formatting.formatting({"formatting": {"caption_size": "big", "line_spacing": None,
                                              "table_header_bold": "yes",
                                              "space_before": float("nan")}})
    assert v["caption_size"] is None and v["line_spacing"] is None
    assert v["table_header_bold"] is None and v["space_before"] is None
    assert formatting.formatting({"formatting": "junk"})["table_size"] is None


def test_spacing_rules():
    assert formatting.leading_em(1.0) == 0.6          # the styles' own
    assert formatting.leading_em(2.0) == 1.9          # twice the baseline distance
    s = {"formatting": {"line_spacing": 2, "space_before": 3}}
    rules = formatting.typst_rules(s)
    assert "#set par(leading: 1.9em, spacing: 3pt + 0.5em)" in rules
    s = {"formatting": {"space_before": 0, "space_after": 0, "caption_size": 8}}
    rules = formatting.typst_rules(s)
    assert "spacing: 0pt + 0pt" in rules and "leading" not in rules
    assert "#show figure.caption: set text(size: 8pt)" in rules


def test_caption_and_text_size_code():
    code = "#si-figure(image(\"a.png\"), [Figure], none)"
    assert formatting.caption_size_code(code, None) == code
    assert formatting.caption_size_code(code, 8) == (
        "#{ show figure.caption: set text(size: 8pt); si-figure(image(\"a.png\"), "
        "[Figure], none) }")
    assert formatting.text_size_code("x", None) == "x"
    assert formatting.text_size_code("x", 9, True) == '#set text(size: 9pt, weight: "bold"); x'
    assert formatting.text_size_code("", 9) == ""


def test_table_formatting_round_trips_and_follows_the_grid():
    t = Table.new(2, 3, "t-1")
    assert set(t.to_json()) == {"id", "label", "header_rows", "columns", "rows", "footnotes"}
    t.rows[0][1].size = 8
    t.widths = [None, 2.5, None]
    t.fit_width, t.caption_size, t.notes_size = True, 9, 7.5
    data = json.loads(json.dumps(t.to_json()))
    assert data["rows"][0][1]["size"] == 8 and data["widths"] == [None, 2.5, None]
    back = Table.from_json(data)
    assert back.to_json() == data
    back.insert_column(1)
    assert back.widths == [None, None, 2.5, None]
    back.delete_columns([2])
    assert back.widths == [None, None, None]
    # Merging keeps the first cell's size; splitting gives it to the new cells.
    back.rows[1][0].size = 9
    back.merge(1, 0, 1, 1)
    assert back.rows[1][0].size == 9
    back.split(1, 0)
    assert back.rows[1][1].size == 9
    # Damaged values are dropped.
    bad = Table.from_json({"rows": [[{"text": "a", "size": "huge"}]], "widths": ["x"],
                           "caption_size": -3})
    assert bad.rows[0][0].size is None and bad.widths == [None]
    assert bad.caption_size == 4.0   # (brought into range)


# ---------------------------------------------------------------------- Typst code

@pytest.fixture
def owner_table(project):
    p = project
    s = p.add_section("text", "Optimization")
    owner = p.text_owner(s.id)
    t = Table.new(2, 2)
    t.rows[0] = [Cell("Entry"), Cell("Solvent")]
    t.rows[1] = [Cell("1"), Cell("THF")]
    t.footnotes = ["^{a} note"]
    return p, s, owner, t


def builder(p):
    return Builder(p, CompileOptions.from_settings(p.settings), lambda path: (4, 3, 300))


def test_unformatted_table_code_is_unchanged(owner_table):
    p, _s, _owner, t = owner_table
    code = builder(p).table_code(t, "here")
    assert code.startswith("table(columns: 2, ") and "set text" not in code
    assert "#set text" not in builder(p).notes_code(t, "here")


def test_table_sizes_widths_and_fit(owner_table):
    p, _s, _owner, t = owner_table
    formatting.store_formatting(p.settings, {"table_size": 9, "table_header_size": 8,
                                             "table_header_bold": True,
                                             "table_notes_size": 7})
    t.rows[1][1].size = 6.5
    t.widths = [1.5, None]
    t.fit_width = True
    b = builder(p)
    code = b.table_code(t, "here")
    assert "columns: (1.5cm, auto,)" in code
    assert code.startswith("layout(room => { let t = { set text(size: 9pt); table(")
    assert '[#set text(size: 8pt, weight: "bold"); #"Entry"]' in code
    assert '[#set text(size: 6.5pt); #"THF"]' in code
    assert "#set text(size: 7pt)" in b.notes_code(t, "here")
    t.notes_size = 8
    assert "#set text(size: 8pt)" in b.notes_code(t, "here")


def test_caption_sizes_per_table_and_figure(owner_table, tmp_path):
    from tests.test_figures_tables import png
    p, s, owner, t = owner_table
    t.caption_size = 8
    table_id = p.add_table(owner, t)
    fig = p.add_figure(owner, data=png())
    p.set_figure_caption_size(owner, fig, 7.5)
    p.write_section_text(s, f"!table{{{table_id}}} Screening\n!figure{{{fig}}} Trace")
    code = builder(p).text_section(s)
    assert "#{ show figure.caption: set text(size: 8pt); si-table(" in code
    assert "#{ show figure.caption: set text(size: 7.5pt); si-figure(" in code
    p.set_figure_caption_size(owner, fig, None)
    assert "caption_size" not in p.figures(owner)[fig]


def test_apply_to_whole_document_clears_single_sizes(owner_table):
    from tests.test_figures_tables import png
    p, s, owner, t = owner_table
    t.caption_size = 8
    t.rows[0][0].size = 7
    t.notes_size = 6
    table_id = p.add_table(owner, t)
    fig = p.add_figure(owner, data=png())
    p.set_figure_caption_size(owner, fig, 7.5)
    data = p.add_section("data", "Products")
    scheme = p.add_scheme(data.id, name="Synthesis")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme),
                                     "caption_size_pt": 12})
    assert se.scheme_layout(p.settings, scheme)["caption_size_pt"] == 12
    assert p.clear_caption_sizes() == 3
    assert p.read_table(owner, table_id).caption_size is None
    assert "caption_size" not in p.figures(owner)[fig]
    assert se.scheme_layout(p.settings, scheme)["caption_size_pt"] == 0
    assert p.clear_table_sizes() == 1
    back = p.read_table(owner, table_id)
    assert back.rows[0][0].size is None and back.notes_size is None


# ---------------------------------------------------------------------- the PDF

def _sizes(pdf, word: str) -> set[float]:
    """The printed font sizes of the text pieces starting with `word`."""
    found = set()
    for page in PdfReader(str(pdf)).pages:
        def visit(text, cm, tm, _font, size):
            if text.strip().startswith(word):
                found.add(round(size * tm[0] * cm[0], 1))
        page.extract_text(visitor_text=visit)
    return found


def test_formatting_reaches_the_pdf(sample):
    p, parts = sample
    owner = p.text_owner(parts["notes"].id)
    t = Table.new(2, 3)
    t.rows[0] = [Cell("HEADA"), Cell("HEADB"), Cell("HEADC")]
    t.rows[1] = [Cell("CELLA"), Cell("CELLB", size=7), Cell("CELLC")]
    t.widths = [None, 3.0, None]
    t.caption_size = 8
    t.footnotes = ["NOTEA explained."]
    table_id = p.add_table(owner, t)
    p.write_section_text(parts["notes"], p.read_section_text(parts["notes"])
                         + f"\n!table{{{table_id}}} CAPTIONX of a table")
    formatting.store_formatting(p.settings, {"table_size": 9, "table_header_size": 8.5,
                                             "table_header_bold": True,
                                             "table_notes_size": 6, "caption_size": 9.5,
                                             "line_spacing": 1.5, "space_after": 4})
    result = compile_sample(p)
    assert result.warnings == []
    assert _sizes(result.pdf, "CELLA") == {9.0}
    assert _sizes(result.pdf, "CELLB") == {7.0}
    assert _sizes(result.pdf, "HEADA") == {8.5}
    assert _sizes(result.pdf, "NOTEA") == {6.0}
    assert 8.0 in _sizes(result.pdf, "CAPTIONX")


def test_shrink_to_fit_keeps_a_wide_table_on_one_line(sample):
    """The problem in Nathan's SI: a table of many columns whose cells wrap. With
    "Shrink to fit the page width" every cell stays on one line."""
    p, parts = sample
    owner = p.text_owner(parts["notes"].id)
    words = [f"Dioxane/MeOH{i}" for i in range(12)]
    t = Table.new(2, len(words))
    t.rows[0] = [Cell(f"Column{i}") for i in range(len(words))]
    t.rows[1] = [Cell(w) for w in words]
    t.fit_width = True
    table_id = p.add_table(owner, t)
    p.write_section_text(parts["notes"], p.read_section_text(parts["notes"])
                         + f"\n!table{{{table_id}}} Wide")
    result = compile_sample(p)
    assert result.warnings == []
    text = "".join(page.extract_text() or "" for page in PdfReader(str(result.pdf)).pages)
    for w in words:   # none broken over two lines
        assert re.search(re.escape(w) + r"(?!\d)", text), w
    assert max(_sizes(result.pdf, "Dioxane")) < 10


# ---------------------------------------------------------------------- the windows

def test_table_editor_sizes_widths_and_fit(qapp):
    from si_assistant.ui.table_editor import TableEditorDialog
    t = Table.new(3, 3)
    dlg = TableEditorDialog(t, "Cap", "Table S1")
    assert dlg.result_table().to_json() == t.to_json()   # nothing set: nothing added
    # Two cells selected: both get 8 pt.
    from PySide6.QtWidgets import QTableWidgetSelectionRange
    dlg.grid.setCurrentCell(1, 0)
    dlg.grid.setRangeSelected(QTableWidgetSelectionRange(1, 0, 1, 1), True)
    dlg.size_box.setValue(8)
    # Column B 2.5 cm wide.
    dlg.grid.clearSelection()
    dlg.grid.setCurrentCell(0, 1)
    dlg.width_box.setValue(2.5)
    dlg.fit_box.setChecked(True)
    dlg.caption_size.setValue(9)
    dlg.notes_size.setValue(7)
    out = dlg.result_table()
    assert out.rows[1][0].size == 8 and out.rows[1][1].size == 8 and out.rows[0][0].size is None
    assert out.widths == [None, 2.5, None]
    assert out.fit_width and out.caption_size == 9 and out.notes_size == 7
    assert "2.5 cm" in dlg.grid.horizontalHeaderItem(1).text()
    dlg.size_box.setValue(dlg.size_box.minimum())   # back to "Default"
    assert dlg.result_table().rows[0][1].size is None
    dlg._autofit()
    assert dlg.result_table().widths == [None, None, None]
    assert dlg.has_changes()


def test_settings_tab_stores_and_applies_to_everything(qapp, owner_table):
    from si_assistant.ui.dialogs import ProjectSettingsDialog
    p, _s, owner, t = owner_table
    t.rows[0][0].size = 7
    t.caption_size = 12
    table_id = p.add_table(owner, t)
    dlg = ProjectSettingsDialog(p, "Tester")
    tab = dlg.text_tab
    assert all(v in (None, False) for v in tab.values().values())
    tab.table_size.setValue(9)
    tab.header_bold.setChecked(True)
    tab.caption_size.setValue(9.5)
    tab.line_spacing.setValue(1.15)
    tab.space_after.setValue(0)   # (0 pt is a value, not "As the style")
    tab.all_captions.setChecked(True)
    dlg.apply()
    assert p.settings["formatting"] == {"table_size": 9.0, "table_header_bold": True,
                                        "caption_size": 9.5, "line_spacing": 1.15,
                                        "space_after": 0.0}
    back = p.read_table(owner, table_id)
    assert back.caption_size is None and back.rows[0][0].size == 7   # tables not ticked
    # Opening it again shows the stored values; Reset goes back to the style.
    dlg = ProjectSettingsDialog(p, "Tester")
    assert dlg.text_tab.values()["caption_size"] == 9.5
    dlg.text_tab.reset()
    dlg.apply()
    assert "formatting" not in p.settings


def test_formatting_marks_toggle_is_shared(qapp):
    from PySide6.QtGui import QTextOption
    from si_assistant.ui import text_checks
    from si_assistant.ui.rich_text import RichTextEditor
    hub = text_checks.checks()
    hub.set_marks_on(False)
    a, b = RichTextEditor(), RichTextEditor()
    flag = QTextOption.Flag.ShowLineAndParagraphSeparators

    def shown(editor):
        return bool(editor.edit.document().defaultTextOption().flags() & flag)

    assert not shown(a) and not a.marks_act.isChecked()
    a.marks_act.setChecked(True)
    assert hub.marks_on and shown(a) and shown(b) and b.marks_act.isChecked()
    a.edit.document().setPlainText("text stays the same")
    assert a.edit.toPlainText() == "text stays the same" and shown(a)
    hub.set_marks_on(False)
    assert not shown(a) and not shown(b) and not a.marks_act.isChecked()


def test_stepping_up_from_default_starts_at_a_usual_value(qapp):
    from si_assistant.ui.format_widgets import size_box, value_of
    box = size_box()
    assert value_of(box) is None
    box.stepBy(1)
    assert value_of(box) == 10.0
    box.stepBy(1)
    assert value_of(box) == 10.5
