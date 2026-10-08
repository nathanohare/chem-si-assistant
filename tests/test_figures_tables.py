"""Figures and tables in custom sections and General Information, and their labels
(SPEC §13k.5, §13k.5a): stored format, table grid operations, label numbering, overrides,
duplicates, mentions and compiling."""

import json

import pytest

from si_assistant.compile.document import Builder, CompileOptions
from si_assistant.core import scheme_editor as se
from si_assistant.core import checks, labels, links
from si_assistant.core.markup import Paragraph, Run, mention_kind, objects, parse, serialize
from si_assistant.core.tables import Cell, Table, block_from_text

PNG = None


def png() -> bytes:
    """A small real PNG (some code reads its size)."""
    global PNG
    if PNG is None:
        import struct
        import zlib
        raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(3))
        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
        PNG = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return PNG


# ---------------------------------------------------------------------- stored text format

def test_figure_and_table_paragraphs_round_trip():
    text = ("Intro @{t-aaaaaa} and @{f-bbbbbb}.\n!figure{f-bbbbbb} A *scheme* of @{c-123456}\n"
            "!table{t-aaaaaa} Optimization\n!image{images/old.png} Old picture")
    paragraphs = parse(text)
    assert paragraphs[1].figure == "f-bbbbbb" and paragraphs[1].plain_text == "A scheme of "
    assert paragraphs[2].table == "t-aaaaaa" and paragraphs[2].is_object
    assert paragraphs[3].image == "images/old.png"
    assert objects(paragraphs) == [("figure", "f-bbbbbb"), ("table", "t-aaaaaa"),
                                   ("image", "images/old.png")]
    assert serialize(paragraphs) == text


def test_text_that_looks_like_an_object_is_escaped():
    paragraphs = [Paragraph([Run("!table{t-x} is just text")]),
                  Paragraph([Run("!figure{f-y}")])]
    stored = serialize(paragraphs)
    assert stored.startswith("\\!table")
    assert parse(stored) == paragraphs


def test_mention_kinds():
    assert [mention_kind(i) for i in ("c-1a2b3c", "f-1a2b3c", "t-1a2b3c", "k-1a2b3c", "odd")] \
        == ["compound", "figure", "table", "scheme", "compound"]


# ---------------------------------------------------------------------- tables

def grid(table):
    """Each row as text, '-' for a covered position, spans as e.g. A(2x1)."""
    out = []
    for row in table.rows:
        out.append(" ".join("-" if c is None else
                            (c.text or ".") + (f"({c.colspan}x{c.rowspan})"
                                               if c.colspan > 1 or c.rowspan > 1 else "")
                            for c in row))
    return out


def test_table_json_round_trip():
    t = Table.new(3, 2, "t-000001")
    t.rows[0] = [Cell("Entry"), Cell("Yield^{a}", align="right")]
    t.rows[1][0] = Cell("1", rowspan=2)
    t.rows[2][0] = None
    t.footnotes = ["^{a} Isolated.", "  "]
    t.label = "Table S9"
    data = t.to_json()
    assert data["rows"][2][0] is None and data["rows"][1][0] == {"text": "1", "rowspan": 2}
    assert data["footnotes"] == ["^{a} Isolated."]
    back = Table.from_json(json.loads(json.dumps(data)))
    assert back.to_json() == data and back.label == "Table S9" and back.header_rows == 1


def test_damaged_table_is_repaired():
    t = Table.from_json({"rows": [[{"text": "a", "colspan": 9}], [None, "b", {"rowspan": 5}]],
                         "columns": ["left"], "header_rows": 7})
    assert t.n_cols == 3 and t.n_rows == 2
    assert grid(t) == ["a(3x1) - -", ". b ."]
    assert t.header_rows == 2


def test_insert_and_delete_rows_and_columns_keep_merged_cells():
    t = Table.new(3, 3)
    t.rows[0][0].text = "A"
    assert t.merge(0, 0, 1, 1)                   # a 2 x 2 block
    assert grid(t) == ["A(2x2) - .", "- - .", ". . ."]
    t.insert_row(1)                              # crosses the block: it grows
    assert grid(t) == ["A(2x3) - .", "- - .", "- - .", ". . ."]
    t.insert_column(1)
    assert grid(t) == ["A(3x3) - - .", "- - - .", "- - - .", ". . . ."]
    t.delete_rows([0])                           # the block's top row: it moves down
    assert grid(t) == ["A(3x2) - - .", "- - - .", ". . . ."]
    t.delete_columns([1, 2])
    assert grid(t) == ["A - .", "- - .", ". . ."] or grid(t) == ["A(1x2) .", "- .", ". ."]
    assert t.n_cols == 2 and t.cell(1, 0).text == "A"


def test_header_rows_follow_inserts_and_deletes():
    t = Table.new(3, 2)
    t.insert_row(0)
    assert t.header_rows == 2
    t.delete_rows([0, 1])
    assert t.header_rows == 0


def test_merge_takes_in_overlapping_cells_and_split_undoes_it():
    t = Table.new(3, 3)
    for r in range(3):
        for c in range(3):
            t.rows[r][c].text = f"{r}{c}"
    t.merge(1, 1, 2, 2)
    t.merge(0, 0, 1, 1)          # cuts the first merge: grows to take it in
    assert grid(t)[0] == "00 01 02 10 11 12 21 22 20(3x3) - -"
    assert t.split(2, 2)
    assert t.cell(1, 1).colspan == 1 and t.rows[1][1] is not None
    assert not t.split(1, 1)


def test_paste_grows_the_table():
    t = Table.new(2, 2)
    t.paste(1, 1, block_from_text("a\tb\tc\n1\t2*\t3\n"))
    assert t.n_rows == 3 and t.n_cols == 4
    assert [t.rows[1][c].text for c in range(1, 4)] == ["a", "b", "c"]
    assert parse(t.rows[2][2].text)[0].plain_text == "2*"   # escaped, not italic


# ---------------------------------------------------------------------- labels

def test_label_styles_and_rules():
    rules = labels.default_rules()
    assert labels.auto_label("figure", 3, rules) == "Figure S3"
    rules["table"].update(prefix="", style="I")
    assert labels.auto_label("table", 4, rules) == "Table IV"
    rules["scheme"].update(style="a", word="")
    assert labels.auto_label("scheme", 28, rules) == "Sab"
    assert labels.alpha(26) == "z" and labels.alpha(27) == "aa"
    assert labels.format_number(9, "i") == "ix"
    assert labels.default_rules("RSC")["figure"]["word"] == "Fig."
    settings = {"journal_style": "ACS"}
    labels.store_label_rules(settings, labels.default_rules())
    assert "labels" not in settings
    changed = labels.default_rules()
    changed["table"]["style"] = "1"
    changed["table"]["prefix"] = ""
    labels.store_label_rules(settings, changed)
    assert settings["labels"] == {"table": {"prefix": ""}}
    settings["journal_style"] = "RSC"   # the figure word follows the style
    assert labels.label_rules(settings)["figure"]["word"] == "Fig."


@pytest.fixture
def doc(project, tmp_path):
    """General Information with a figure; a custom section with a sub-section; a Data
    section with a scheme page and an inline file."""
    p = project
    general = p.text_owner("general")
    g_fig = p.add_figure(general, data=png())
    p.write_front_text("general", f"Methods.\n!figure{{{g_fig}}} Set-up")
    notes = p.add_section("text", "Optimization")
    sub = p.add_section("text", "Controls", parent_id=notes.id)
    t1 = p.add_table(p.text_owner(notes.id))
    f1 = p.add_figure(p.text_owner(notes.id), data=png())
    p.write_section_text(notes, f"!table{{{t1}}} Screening\nText.\n!figure{{{f1}}} Trace")
    t2 = p.add_table(p.text_owner(sub.id))
    p.write_section_text(sub, f"!table{{{t2}}} Controls")
    data = p.add_section("data", "Products")
    p.set_id_table(data.id, ["1", "2"])
    scheme = p.add_scheme(data.id, name="Synthesis")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme), "show_label": True})   # (numbered: SPEC §13p.1)
    c = p.add_compound(data.id, name="Product", scheme_id=scheme.id)
    p.add_field(c, {"key": "files"})
    src = tmp_path / "hplc.png"
    src.write_bytes(png())
    item = p.attach_compound_file(c, "files", src, {"caption": "HPLC trace"})
    return p, {"g_fig": g_fig, "notes": notes, "sub": sub, "t1": t1, "f1": f1, "t2": t2,
               "scheme": scheme, "c": c, "item": item, "data": data}


def test_labels_in_document_order(doc):
    p, d = doc
    found = labels.compute(p)
    assert [(i.kind, i.label) for i in found.items] == [
        ("figure", "Figure S1"), ("table", "Table S1"), ("figure", "Figure S2"),
        ("table", "Table S2"), ("scheme", "Scheme S1"), ("figure", "Figure S3")]
    assert found.label(d["g_fig"]) == "Figure S1"
    assert found.label(d["t2"]) == "Table S2"
    assert found.label(d["item"]["id"]) == "Figure S3"
    assert found.at(d["c"].id, 0).caption == "HPLC trace"
    assert found.by_id[d["scheme"].id].place == ("scheme", d["scheme"].id)
    # Moving the sub-section to the top level (after the Data section) renumbers.
    p.move_section(d["sub"].id, 5, None)
    assert labels.compute(p).label(d["t2"]) == "Table S2"
    p.move_section(d["sub"].id, 0, None)
    assert labels.compute(p).label(d["t2"]) == "Table S1"


def test_typed_labels_keep_numbers_and_duplicates_are_reported(doc):
    p, d = doc
    p.set_figure_label(p.text_owner(d["notes"].id), d["f1"], "Figure S3")   # clash
    table = p.read_table(p.text_owner(d["notes"].id), d["t1"])
    table.label = "Table S2a"
    p.write_table(p.text_owner(d["notes"].id), table)
    p.set_scheme_label(d["scheme"].id, "Scheme 1")
    found = labels.compute(p)
    assert found.label(d["t1"]) == "Table S2a" and found.label(d["t2"]) == "Table S2"
    assert found.label(d["scheme"].id) == "Scheme 1"
    assert found.label(d["item"]["id"]) == "Figure S3"          # still its own number
    assert list(found.duplicates) == ["Figure S3"]
    assert found.is_duplicate("figure  s3")
    warnings = checks.project_warnings(p)
    assert len([w for w in warnings if w.check == "label"]) == 2
    places = {w.place for w in warnings if w.check == "label"}
    assert places == {("section", d["notes"].id), ("compound", d["c"].id)}
    # The inline file's own label override.
    d["item"]["label"] = "Figure X"
    p.save_compound(d["c"])
    assert labels.compute(p).duplicates == {}


def test_live_text_changes_labels(doc):
    p, d = doc
    live = {"general": f"!figure{{{d['g_fig']}}} later\n"}
    assert labels.compute(p, live).label(d["f1"]) == "Figure S2"
    assert labels.compute(p, {"general": "No figure now."}).label(d["f1"]) == "Figure S1"


def test_mentions_of_figures_tables_and_schemes(doc):
    p, d = doc
    p.write_front_text("cover", f"See @{{{d['t2']}}}, @{{{d['scheme'].id}}} and @{{t-gone00}}.")
    assert links.plain_text(p, p.read_front_text("cover")) == "See Table S2, Scheme S1 and ??."
    gone, _missing = links.broken_links(p, parse(p.read_front_text("cover")))
    assert gone == ["t-gone00"]
    warnings = [w for w in checks.project_warnings(p) if w.check == "mention"]
    assert len(warnings) == 1 and "a table that has been deleted" in warnings[0].message
    assert warnings[0].place == ("front", "cover")


def test_table_cells_are_searched_for_links(doc):
    p, d = doc
    owner = p.text_owner(d["notes"].id)
    table = p.read_table(owner, d["t1"])
    table.rows[1][1].text = f"@{{{d['c'].id}}} @cite{{r-none00}}"
    p.write_table(owner, table)
    assert links.mention_uses(p, d["c"].id) == ["Optimization ▸ table"]
    assert any("cites a reference" in w.message for w in checks.project_warnings(p))


# ---------------------------------------------------------------------- storage and Trash

def test_figure_files_trash_and_restore(doc):
    p, d = doc
    owner = p.text_owner(d["notes"].id)
    path = p.figure_path(owner, d["f1"])
    assert path.parent.name == "images" and path.exists()
    assert d["f1"] in json.loads((d["notes"].folder / "section.json").read_text("utf-8"))[
        "figures"]
    entry = p.remove_figure(owner, d["f1"], "Trace of *x*")
    assert not path.exists() and d["f1"] not in p.figures(owner)
    # The editor takes the paragraph out of the text; restoring puts it at the end.
    p.write_section_text(d["notes"], p.read_section_text(d["notes"]).split("\n!figure")[0])
    message = p.restore_from_trash(entry.id)
    assert "end of “Optimization”" in message
    assert p.read_section_text(d["notes"]).endswith(f"!figure{{{d['f1']}}} Trace of *x*")
    assert p.figure_path(owner, d["f1"]).exists()


def test_table_trash_restore_fills_a_paragraph_left_without_its_file(doc):
    p, d = doc
    owner = p.text_owner(d["sub"].id)
    before = p.read_section_text(d["sub"])
    entry = p.remove_table(owner, d["t2"], "Controls")
    assert not p.table_path(owner, d["t2"]).exists()
    from si_assistant.core.checks import project_warnings
    assert [w.check for w in project_warnings(p)] == ["object"]   # its file is missing
    # The text still has !table{t2} with nothing behind it: the table goes back there.
    p.restore_from_trash(entry.id)
    assert p.read_section_text(d["sub"]) == before
    assert p.read_table(owner, d["t2"]) is not None and not project_warnings(p)


def test_table_trash_restore_gets_a_new_id_if_still_in_use(doc):
    p, d = doc
    owner = p.text_owner(d["sub"].id)
    entry = p.remove_table(owner, d["t2"], "Controls")
    p.add_table(owner, Table.new(table_id=d["t2"]))   # (the ID is in use again)
    p.restore_from_trash(entry.id)
    text = p.read_section_text(d["sub"])
    new_id = text.split("\n")[-1][len("!table{"):].split("}")[0]
    assert new_id != d["t2"] and p.read_table(owner, new_id) is not None


def test_replace_figure_picture_keeps_id_and_label(doc):
    p, d = doc
    owner = p.text_owner("general")
    p.set_figure_label(owner, d["g_fig"], "Figure A")
    old = p.figure_path(owner, d["g_fig"])
    p.replace_figure_picture(owner, d["g_fig"], data=png(), ext=".png",
                             vector=b"%PDF-1.4 fake", vector_ext=".pdf")
    new = p.figure_path(owner, d["g_fig"])
    assert new.exists() and new.with_suffix(".pdf").exists()
    assert p.figures(owner)[d["g_fig"]]["label"] == "Figure A"
    assert [e.kind for e in p.list_trash()] == ["figure"]
    assert (new, new.with_suffix(".pdf")) in p.pasted_pictures()


def test_older_pictures_become_figures(project):
    p = project
    s = p.add_section("text", "Notes")
    rel = p.add_section_image(s, data=png())
    p.write_section_text(s, f"A\n!image{{{rel}}} Old caption")
    owner = p.text_owner(s.id)
    assert labels.compute(p).items[0].label == "Figure S1"   # labelled even before
    assert p.upgrade_pictures(owner) == 1
    paragraph = parse(p.read_section_text(s))[1]
    assert paragraph.figure and paragraph.plain_text == "Old caption"
    assert p.figure_path(owner, paragraph.figure) == s.folder / rel
    assert p.upgrade_pictures(owner) == 0
    assert p.object_owners() == {paragraph.figure: s.id}


# ---------------------------------------------------------------------- compiling

def builder(p):
    return Builder(p, CompileOptions.from_settings(p.settings), lambda path: (4, 3, 300))


def test_table_typst_has_merged_cells_header_and_footnotes(doc):
    p, d = doc
    owner = p.text_owner(d["notes"].id)
    t = Table.new(3, 3, d["t1"])
    t.rows[0] = [Cell("Entry"), Cell("Result", colspan=2), None]
    t.rows[1] = [Cell("1", rowspan=2), Cell("CH2Cl2", align="left"), Cell("90%^{a}")]
    t.rows[2] = [None, Cell("THF"), Cell("10%")]
    t.footnotes = ["^{a} NMR yield."]
    p.write_table(owner, t)
    b = builder(p)
    code = b.text_section(d["notes"])
    assert "table.header([#\"Entry\"], table.cell(colspan: 2)[#\"Result\"])" in code
    assert "table.cell(rowspan: 2)[#\"1\"]" in code
    assert "table.cell(align: left + horizon)" in code
    assert '#"CH"#sub[#"2"]#"Cl"#sub[#"2"]' in code       # formatting helpers in cells
    assert '#super[#"a"]#" NMR yield."' in code
    assert '[#"Table S1"], [#"Screening"]' in code
    assert '#si-figure(image(' in code and '[#"Figure S2"], [#"Trace"]' in code
    assert b.kinds["table"] == 1 and b.kinds["si-figure"] == 1


def test_header_cells_cannot_reach_into_the_body(doc):
    p, d = doc
    t = Table.new(3, 2)
    t.rows[0][0] = Cell("tall", rowspan=3)
    t.rows[1][0] = t.rows[2][0] = None
    code = builder(p).table_code(t, "here")
    assert "rowspan" not in code


def test_missing_table_and_picture_warn(doc):
    p, d = doc
    owner = p.text_owner(d["notes"].id)
    p.table_path(owner, d["t1"]).unlink()
    p.figure_path(owner, d["f1"]).unlink()
    b = builder(p)
    b.text_section(d["notes"])
    assert any("table could not be found" in w for w in b.warnings)
    assert any("Missing file" in w for w in b.warnings)


# ---------------------------------------------------------------------- review fixes (S3)

def test_png_and_pdf_figures_with_the_same_name_stay_apart(project, tmp_path):
    """A scheme.png and a scheme.pdf added as two figures are never taken for a picture
    and its ChemDraw original (removing one must not trash the other)."""
    p = project
    s = p.add_section("text", "Pictures")
    owner = p.text_owner(s.id)
    (tmp_path / "scheme.png").write_bytes(png())
    (tmp_path / "scheme.pdf").write_bytes(b"%PDF-1.4 fake")
    a = p.add_figure(owner, source=tmp_path / "scheme.png")
    b = p.add_figure(owner, source=tmp_path / "scheme.pdf")
    pa, pb = p.figure_path(owner, a), p.figure_path(owner, b)
    assert pa.stem != pb.stem
    assert dict(p.pasted_pictures())[pa] is None
    p.remove_figure(owner, a)
    assert pb.exists()
    # An older project where two figures did end up with one name: still never a pair.
    folder = owner.folder / "images"
    (folder / "old.png").write_bytes(png())
    (folder / "old.pdf").write_bytes(b"%PDF-1.4 fake")
    p._set_figure_entry(owner, "f-old1", {"file": "images/old.png", "label": ""})
    p._set_figure_entry(owner, "f-old2", {"file": "images/old.pdf", "label": ""})
    assert p.figure_source(owner, "f-old1") is None
    p.remove_figure(owner, "f-old1")
    assert (folder / "old.pdf").exists()
    # A real ChemDraw original is still found.
    c = p.add_figure(owner, data=png(), vector=b"%PDF-1.4 fake", vector_ext=".pdf")
    assert p.figure_source(owner, c) == p.figure_path(owner, c).with_suffix(".pdf")


def test_figure_printed_width_can_be_typed(doc):
    p, d = doc
    owner = p.text_owner(d["notes"].id)
    assert p.figure_width(owner, d["f1"]) is None
    p.set_figure_width(owner, d["f1"], 8)
    assert p.figures(owner)[d["f1"]]["width"] == 8
    assert "width: 3.15in" in builder(p).text_section(d["notes"])
    p.set_figure_width(owner, d["f1"], 40)        # never wider than the text
    assert "width: 6.5in" in builder(p).text_section(d["notes"])
    p.replace_figure_picture(owner, d["f1"], data=png())   # kept with a new picture
    assert p.figure_width(owner, d["f1"]) == 40
    p.set_figure_width(owner, d["f1"], None)
    assert "width" not in p.figures(owner)[d["f1"]]
    # Restored from the Trash with its width.
    p.set_figure_width(owner, d["f1"], 5)
    entry = p.remove_figure(owner, d["f1"], "Trace")
    p.write_section_text(d["notes"], "Text only.")
    p.restore_from_trash(entry.id)
    assert p.figure_width(owner, d["f1"]) == 5


def test_table_page_break_choice(doc):
    p, d = doc
    assert "breakable" not in Table.new().to_json()
    owner = p.text_owner(d["notes"].id)
    t = p.read_table(owner, d["t1"])
    assert not t.breakable
    assert "split: true" not in builder(p).text_section(d["notes"])
    t.breakable = True
    p.write_table(owner, t)
    assert p.read_table(owner, d["t1"]).breakable
    assert "split: true)" in builder(p).text_section(d["notes"])


def test_a_scheme_without_its_label_is_not_numbered_and_mentions_say_why(doc):
    """Schemes print without a label or caption by default (SPEC §13p.1): not numbered,
    and a mention of one prints “??” with a warning saying how to turn the label on."""
    p, d = doc
    scheme = d["scheme"]
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme),
                                     "show_label": False})
    found = labels.compute(p)
    assert scheme.id not in found.by_id
    assert found.label(d["item"]["id"]) == "Figure S3"
    p.write_front_text("cover", f"See @{{{scheme.id}}}.")
    assert links.plain_text(p, p.read_front_text("cover")) == "See ??."
    warnings = [w for w in checks.project_warnings(p) if w.check == "mention"]
    assert len(warnings) == 1 and "printed without a label" in warnings[0].message
    assert "Label and caption" in warnings[0].message
