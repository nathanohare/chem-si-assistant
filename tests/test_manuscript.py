"""The manuscript check (SPEC §13o): parsing, reading PDF/Word/ChemDraw files, comparing
with the SI, dismissing, and damaged files. All test files are made up
(tests/manuscript_samples.py)."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

from si_assistant.core import manuscript as M
from si_assistant.core import ms_parse as P
from si_assistant.core import ms_read

sys.path.insert(0, str(Path(__file__).parent))
import manuscript_samples as S  # noqa: E402

WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------- label and value parsing

# (From chem-vision's tests, MIT, plus this program's own cases.)
CASES = [
    ("3a, 92%, 95% ee", {"ids": ["3a"], "yield": "92", "ee": "95"}),
    ("4b (85% yield, 97:3 er)", {"ids": ["4b"], "yield": "85", "er": "97:3", "ee_from_er": 94.0}),
    ("12c: 71%, >20:1 dr, 90% ee", {"ids": ["12c"], "yield": "71", "dr": ">20:1", "ee": "90"}),
    ("(±)-5, 64% (NMR yield)", {"ids": ["(±)-5"], "yield_nmr": "64"}),
    ("S7, 88%, E/Z = 10:1", {"ids": ["S7"], "yield": "88", "ez": "10:1"}),
    ("6aa 40% conv., 12 h, −20 °C", {"ids": ["6aa"], "conversion": "40", "time": "12 h",
                                     "temperature": "-20 °C"}),
    ("Table 1, entry 3: 3a, 92%", {"ids": ["3a"], "yield": "92"}),
    ("3d", {"ids": ["3d"]}),
    ("3h, 70%", {"ids": ["3h"], "yield": "70"}),
]


@pytest.mark.parametrize("text,want", CASES)
def test_annotate_text(text, want):
    got = P.annotate_text(text)
    for k, v in want.items():
        assert got.get(k) == v, (text, k, got)


def test_3h_is_a_compound_but_3_h_is_a_time():
    assert P.find_ids("3h was isolated") == ["3h"]
    assert "time" not in P.annotate_text("3h, 70%")
    times = [q for q in P.quantities("stirred for 3 h, then 3h was filtered") if q.kind == "time"]
    assert [q.value for q in times] == [180.0]
    assert not [q for q in P.quantities("compound 3h and 4d") if q.kind == "time"]


def test_footnote_markers():
    assert P.annotate_text("85%^b, 97:3 er")["footnotes"] == ["b"]
    assert P.annotate_text("3a, 90%a")["footnotes"] == ["a"]
    assert P.annotate_text("8k[c]")["footnotes"] == ["c"]
    rec = P.annotate_text("3h^c")
    assert rec["ids"] == ["3h"] and rec["footnotes"] == ["c"]


def test_er_to_ee_and_alternative_results():
    assert P.er_to_ee("97:3") == 94.0 and P.er_to_ee("97.5:2.5") == 95.0
    assert P.er_to_ee("abc") is None
    rec = P.annotate_text("3b, 97% (58%: Pd = 3 mol %)")
    assert rec["yield"] == "97" and rec["alternative_results"] == ["58%: Pd = 3 mol %"]


def test_id_key():
    assert P.id_key("3A") == P.id_key(" 3a ") == "3a" and P.id_key("12b’") == "12b'"
    # A stereo prefix is part of the ID; id_base() leaves it out (used only when no SI
    # compound has the full ID, see test_ids_with_stereo_prefixes).
    assert P.id_key("cis-5a") != P.id_key("trans-5a")
    assert P.id_base("(±)-5") == "5" and P.id_base("trans-5a") == "5a"
    assert P.id_without_rac("rac-5") == P.id_without_rac("ent-5") == "5"


def _span(text, x, y, size=9.0, **kw):
    return P.Span(text, x, y - 0.75 * size, x + 0.5 * size * len(text), y + 0.2 * size, size, **kw)


def test_lines_scripts_and_footnotes():
    spans = [_span("85%", 10, 50), _span("b", 23.6, 47, size=5.4), _span(", 97:3 er", 26.5, 50),
             _span("a", 10, 97, size=5.4), _span("Isolated yields.", 12.8, 100),
             _span("b", 86, 97, size=5.4), _span("Run at 60 °C for 48 h.", 88.8, 100)]
    P.mark_scripts(spans)
    lines = P.group_lines(spans)
    texts = [L["text"] for L in lines]
    assert "85%^b, 97:3 er" in texts
    entries = P.footnote_entries(lines)
    assert [(m, t) for m, t, _y in entries] == [("a", "Isolated yields."),
                                                 ("b", "Run at 60 °C for 48 h.")]


def test_footnotes_for_picks_the_one_below():
    entries = [["c", "first", 100.0], ["c", "second", 300.0]]
    assert P.footnotes_for(entries, 50) == {"c": "first"}
    assert P.footnotes_for(entries, 200) == {"c": "second"}
    assert P.footnotes_for(entries, 400) == {"c": "second"}


def _line(text, x0, y0, x1=None, size=9.0, bold=()):
    return {"text": text, "bbox": [x0, y0, x1 or x0 + 0.5 * size * len(text), y0 + size],
            "size": size, "bold_parts": list(bold)}


def test_label_records_two_rows_never_mix():
    """IDs with their values on the line under them, in two rows: row 1's values belong
    to row 1, even though the row-2 labels are the next text below them."""
    lines = [_line("3a", 50, 100, bold=["3a"]), _line("92%, 95% ee", 40, 111),
             _line("3b, 85%, 97:3 er", 200, 100),
             _line("3h", 50, 220, bold=["3h"]), _line("64%", 48, 231),
             _line("3i", 200, 220), _line("77%, 90% ee", 190, 231)]
    recs = {r["ids"][0]: r for r in P.label_records(lines)}
    assert recs["3a"]["yield"] == "92" and recs["3a"]["ee"] == "95"
    assert recs["3b"]["yield"] == "85" and recs["3b"]["er"] == "97:3"
    assert recs["3h"]["yield"] == "64" and "ee" not in recs["3h"]
    assert recs["3i"]["yield"] == "77" and recs["3i"]["ee"] == "90"


def test_assign_to_structures_below_first():
    boxes = [[40, 40, 100, 90], [180, 40, 240, 90], [40, 200, 100, 250]]
    blocks = [{"text": "3a / 92%", "bbox": [45, 100, 95, 120]},     # under 1
              {"text": "3b, 80%", "bbox": [185, 100, 235, 110]},    # under 2
              {"text": "3h / 64%", "bbox": [45, 260, 95, 280]},     # under 3
              {"text": "12 h", "bbox": [120, 60, 150, 70]}]         # an arrow's conditions
    out = P.assign_to_structures(boxes, blocks)
    assert [b["text"] for b in out[0]] == ["3a / 92%"]
    assert [b["text"] for b in out[1]] == ["3b, 80%"]
    assert [b["text"] for b in out[2]] == ["3h / 64%"]


def test_find_tables_and_repeated_products():
    lines = [_line("Entry", 30, 100), _line("Product", 80, 100), _line("Yield (%)^a", 130, 100),
             _line("ee (%)", 190, 100)]
    for i, (e, pid, y, ee) in enumerate([("1", "3d", "88", "91"), ("2^c", "3e", "76", "93")]):
        yy = 115 + 14 * i
        lines += [_line(e, 30, yy), _line(pid, 80, yy), _line(y, 130, yy), _line(ee, 190, yy)]
    lines.append(_line("^aIsolated yield. ^cAt 60 °C for a long time indeed.", 30, 150, 300))
    P.footnote_entries(lines)
    tables = P.find_tables(lines)
    assert len(tables) == 1
    recs = P.table_records(tables[0])
    assert [(r["ids"][0], r["yield"], r["ee"]) for r in recs] == [("3d", "88", "91"),
                                                                 ("3e", "76", "93")]
    assert recs[1]["footnotes"] == ["c"] and "footnotes" not in recs[0]
    # An optimisation table: the same product in every row -> mentions only, no values.
    opt = {"rows": [{"fields": {"entry": "1", "product_id": "3a", "yield": "40"}},
                    {"fields": {"entry": "2", "product_id": "3a", "yield": "92"}}]}
    recs = P.table_records(opt)
    assert all(r.get("repeated") and "yield" not in r for r in recs)


def test_names_sentences_and_mentions():
    text = ("The ester methyl 4-bromobenzoate (3a) was obtained in 92% yield. Then "
            "2-phenylpropanoic acid (3c) and the product (3d) were made. Ketone 3e was formed "
            "in 76% yield and 90% ee.")
    names = dict((i, n) for n, i in P.names_with_ids(text))
    assert names == {"3a": "methyl 4-bromobenzoate", "3c": "2-phenylpropanoic acid"}
    recs = P.sentence_records(text)
    assert {r["ids"][0]: r.get("yield") for r in recs} == {"3a": "92", "3e": "76"}
    assert P.mentioned_ids(text) >= {"3a", "3c", "3d"}


def test_quantities():
    q = {(x.kind, x.value) for x in P.quantities(
        "K3PO4 (2.0 equiv), Pd(OAc)2 (5 mol%) in CH2Cl2 (0.1 M) at reflux, 0.20 mmol, rt, "
        "-78 °C, 30 min, 2 days")}
    assert {("equiv", 2.0), ("loading", 5.0), ("conc", 0.1), ("temperature", "reflux"),
            ("scale", 0.2), ("temperature", "rt"), ("temperature", -78.0), ("time", 30.0),
            ("time", 2880.0), ("reagent", "DCM"), ("reagent", "K3PO4"),
            ("reagent", "Pd(OAc)2")} <= q
    a = [x for x in P.quantities("in dichloromethane") if x.kind == "reagent"]
    assert a[0].value == "DCM"
    assert P.Quantity("time", 2880.0, "48 h").same(P.Quantity("time", 2880.0, "2 days"))


# ---------------------------------------------------------------------- reading the files

def test_read_pdf(tmp_path):
    src = ms_read.read_file(S.make_pdf(tmp_path / "ms.pdf"))
    assert not src.error and src.figure_text
    recs = {}
    for r in src.records:
        recs.setdefault(r["id"], []).append(r)
    fig = {r["id"]: r for r in src.records if r["source"] == "figure"}
    assert (fig["3a"]["yield"], fig["3a"]["ee"]) == ("92", "95")
    assert (fig["3b"]["yield"], fig["3b"]["er"], fig["3b"]["footnotes"]) == ("85", "97:3", ["b"])
    assert fig["3b"]["conditions"]["b"] == S.FOOTNOTE_B
    assert fig["3c"]["dr"] == ">20:1"
    assert fig["3h"]["yield"] == "64" and fig["3h"]["conditions"]["c"] == S.FOOTNOTE_C
    assert fig["3i"]["ee"] == "90" and fig["3a"]["where"] == "Scheme 2, p. 1"
    table = {r["id"]: r for r in src.records if r["source"] == "table"}
    assert (table["3d"]["yield"], table["3d"]["ee"]) == ("88", "91")
    assert table["3e"]["conditions"] == {"c": S.FOOTNOTE_C}
    assert table["3e"]["where"] == "Table 1, p. 1"
    assert {(n["name"], n["id"]) for n in src.names} == {
        ("methyl 4-bromobenzoate", "3a"), ("Ethyl 2-phenylpropanoate", "3c")}
    assert "3e" in src.mentioned and "3a" in src.tokens


def test_read_pdf_without_figure_text(tmp_path):
    src = ms_read.read_file(S.make_pdf(tmp_path / "ms.pdf", with_figure_text=False))
    assert not src.error and not src.figure_text
    assert any("outlined" in n for n in src.notes)
    assert {r["id"] for r in src.records if r["source"] == "table"} == {"3d", "3e"}


def test_read_cdxml(tmp_path):
    src = ms_read.read_file(S.make_cdxml(tmp_path / "s.cdxml"))
    assert not src.error
    recs = {r["id"]: r for r in src.records}
    assert set(recs) == {"3a", "3b", "3c", "3h", "3i"}
    assert recs["3a"]["smiles"] == "COC(=O)c1ccc(Br)cc1"
    assert (recs["3a"]["yield"], recs["3a"]["ee"]) == ("92", "95")
    assert (recs["3b"]["yield"], recs["3b"]["er"], recs["3b"]["footnotes"]) == ("85", "97:3", ["b"])
    assert recs["3b"]["conditions"]["b"] == S.FOOTNOTE_B
    # Row 2 gets its own text, not the row-1 text above it.
    assert recs["3h"]["yield"] == "64" and recs["3h"]["footnotes"] == ["c"]
    assert recs["3i"]["yield"] == "77" and recs["3i"]["smiles"] == "CC(=O)c1ccccc1"
    assert recs["3c"]["dr"] == ">20:1" and recs["3a"]["confidence"] == "exact"
    assert "o" not in src.tokens and "br" not in src.tokens   # atom labels are not text


def test_read_docx(tmp_path):
    src = ms_read.read_file(S.make_docx(tmp_path / "ms.docx", with_figure=WINDOWS))
    assert not src.error
    table = {r["id"]: r for r in src.records if r["source"] == "table"}
    assert table["3e"]["conditions"] == {"c": S.FOOTNOTE_C} and table["3d"]["yield"] == "88"
    assert ("methyl 4-bromobenzoate", "3a") in {(n["name"], n["id"]) for n in src.names}
    if WINDOWS:   # the pasted ChemDraw figure (an EMF) keeps its text
        fig = {r["id"]: r for r in src.records if r["source"] == "figure"}
        assert (fig["3a"]["yield"], fig["3a"]["ee"]) == ("92", "95")
        assert fig["3b"]["footnotes"] == ["b"] and fig["3b"]["conditions"]["b"] == S.FOOTNOTE_B
        assert fig["3h"]["yield"] == "64" and fig["3h"]["footnotes"] == ["c"]
        assert src.figure_text
    else:   # (no figure in the file: nothing from figures, and no note about them)
        assert not [r for r in src.records if r["source"] == "figure"]
        assert src.figure_text and not any("figures" in n for n in src.notes)


@pytest.mark.skipif(not WINDOWS, reason="EMF samples are drawn with Windows GDI")
def test_emf_spans():
    spans = ms_read.emf_spans(S.figure_emf())
    texts = [s.text for s in spans]
    assert "3a" in texts and "92%, 95% ee" in texts
    a = next(s for s in spans if s.text == "3a")
    v = next(s for s in spans if s.text == "92%, 95% ee")
    assert v.y0 > a.y0 and abs(a.size - 9.9) < 1.5   # below it; 3.5 mm ≈ 9.9 pt
    assert next(s for s in spans if s.text == "3a").bold


@pytest.mark.parametrize("name,data", [
    ("bad.pdf", b"%PDF-1.4 nonsense \x00\x01"),
    ("empty.pdf", b""),
    ("bad.docx", b"not a zip"),
    ("bad.cdxml", b"<CDXML><page><fragment"),
    ("odd.cdxml", b"<CDXML><t BoundingBox='a b c d'><s>3a</s></t><fragment><n p='x'/></fragment>"
                  b"</CDXML>"),
])
def test_damaged_files_give_a_message(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    src = ms_read.read_file(path)     # never raises
    assert isinstance(src, ms_read.Source)
    if name != "odd.cdxml":
        assert src.error and path.name in src.error


def test_docx_without_document_and_other_types(tmp_path):
    path = tmp_path / "x.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "hi")
    assert "Word" in ms_read.read_file(path).error
    (tmp_path / "s.cdx").write_bytes(b"VjCD0100")
    assert "Save As" in ms_read.read_file(tmp_path / "s.cdx").error
    (tmp_path / "m.doc").write_bytes(b"x")
    assert ".docx" in ms_read.read_file(tmp_path / "m.doc").error


def test_pdf_without_text(tmp_path):
    src = tmp_path / "pic.typ"
    src.write_text("#rect(width: 5cm, height: 3cm)", encoding="utf-8")
    import typst
    typst.compile(str(src), output=str(tmp_path / "pic.pdf"))
    out = ms_read.read_file(tmp_path / "pic.pdf")
    assert "no text" in out.error


# ---------------------------------------------------------------------- comparing

@pytest.fixture
def checked(tmp_path):
    p, compounds = S.build_si(tmp_path / "si")
    for name, make in (("ms.pdf", S.make_pdf), ("scheme.cdxml", S.make_cdxml)):
        M.add_file(p, make(tmp_path / name))
    yield p, compounds
    p.close()


def _problems(result):
    return {(f.kind, f.label) for f in result.problems}


def test_end_to_end(checked):
    p, _c = checked
    result = M.check(p, M.read_all(p))
    assert _problems(result) == {
        ("yield", "3b"), ("conditions", "3b"), ("structure", "3c"), ("name", "3c"),
        ("deviation", "3d"), ("conditions", "3e"), ("missing_si", "3i")}
    # 3f is only in the SI: not reported at all (SPEC §13o.6).
    assert not any(f.label == "3f" for f in result.findings)
    msg = {(f.kind, f.label): f.message for f in result.problems}
    assert msg[("yield", "3b")] == "3b: the manuscript gives 85% yield, the SI 82%."
    assert "mentions 48 h, which is not" in msg[("conditions", "3b")]   # (60 °C is there)
    assert "10 mol%" in msg[("conditions", "3e")] and "toluene" in msg[("conditions", "3e")]
    assert "36 h" in msg[("deviation", "3d")] and "24 h" in msg[("deviation", "3d")]
    ok = {(f.kind, f.label) for f in result.findings if f.status == M.OK}
    assert {("yield", "3a"), ("ee", "3a"), ("ee", "3b"), ("structure", "3a"), ("name", "3a"),
            ("conditions", "3h"), ("dr", "3c")} <= ok
    assert result.checked == 6
    assert result.summary().startswith("6 compounds checked: 1 missing in the SI, 1 structure")
    where = {(f.kind, f.label): f.where for f in result.problems}
    assert where[("structure", "3c")] == "scheme.cdxml"           # (no "(ChemDraw)" after it)
    assert where[("missing_si", "3i")] == "Scheme 2, p. 1 (PDF); scheme.cdxml"
    assert msg[("structure", "3c")] == ("3c: the structure in scheme.cdxml (C11H14O2) is not "
                                       "the SI structure (C10H12O2).")
    # The yield field opens from the finding.
    f = next(f for f in result.problems if f.kind == "yield")
    assert f.field == "yield" and f.compound_id == checked[1]["3b"].id


def test_fixing_the_si_clears_findings(checked):
    p, c = checked
    c["3b"].values["yield"]["percent"] = "85"
    p.save_compound(c["3b"])
    p.write_compound_text(c["3b"], "procedure", "Stirred at 60 °C for 48 h.")
    result = M.check(p, M.read_all(p))
    assert ("yield", "3b") not in _problems(result) and ("conditions", "3b") not in _problems(result)


def test_internal_disagreement_and_er(tmp_path):
    p, c = S.build_si(tmp_path)
    try:
        sources = [ms_read.Source("a.pdf", "pdf", records=[
            {"id": "3a", "yield": "92", "ee": "95", "where": "Scheme 2"},
            {"id": "3a", "yield": "90", "where": "Table 1"},
            {"id": "3b", "er": "97:3", "where": "Scheme 2"},
            {"id": "3d", "er": "96:4", "where": "Scheme 2"}])]
        result = M.check(p, sources)
        internal = [f for f in result.problems if f.kind == "internal"]
        assert len(internal) == 1 and             "92% in Scheme 2 (PDF) and 90% in Table 1 (PDF)" in internal[0].message
        assert ("yield", "3a") not in _problems(result)     # the SI matches one of them
        assert ("ee", "3b") not in _problems(result)        # 97:3 er = 94% ee
        bad = next(f for f in result.problems if (f.kind, f.label) == ("ee", "3d"))
        assert "96:4 er = 92% ee" in bad.message and "91% ee" in bad.message
    finally:
        p.close()


def test_value_rules():
    assert M.same_ee(M.ee_value("94% ee"), M.ee_value("97:3 er"))
    assert not M.same_ee(M.ee_value("95% ee"), M.ee_value("97:3"))
    assert not M.same_ee(M.ee_value(">99% ee"), M.ee_value("99"))
    assert M.same_dr(">20:1", ">20:1") and not M.same_dr(">20:1", "20:1")
    assert M.same_dr("10:1", "91:9") and not M.same_dr("10:1", "5:1")
    assert M._number("92") == M._number("92.0") and M._number("92") != M._number("91")
    assert M.name_key("Methyl 4‑bromobenzoate.") == M.name_key("methyl  4-bromobenzoate")
    assert M.name_key("N’-methyl") == M.name_key("N'-Methyl")


def test_structure_comparisons():
    from rdkit import Chem
    mol = Chem.MolFromSmiles
    assert M.compare_structures("C[C@H](O)c1ccccc1", mol("C[C@H](O)c1ccccc1"))[0] == "same"
    assert M.compare_structures("C[C@H](O)c1ccccc1", mol("C[C@@H](O)c1ccccc1"))[0] == "stereo"
    assert M.compare_structures("CCN", mol("CCN.Cl"))[0] == "salt"
    assert M.compare_structures("CCN", mol("CC[NH3+]"))[0] == "charge"
    assert M.compare_structures("CCO", mol("CCN"))[0] == "different"
    assert M.compare_structures("not smiles", mol("CCN"))[0] == ""


def test_starting_material_labels_count_as_known(tmp_path):
    from si_assistant.core import scheme_editor as se
    p, c = S.build_si(tmp_path)
    try:
        k = p.add_scheme(p.data_sections()[0].id)
        se.set_box_label(p, k, se.starting(k)[0]["id"], "1a")
        sources = [ms_read.Source("a.pdf", "pdf", records=[
            {"id": "1a", "where": "Scheme 2"}, {"id": "9z", "where": "Scheme 2"}])]
        result = M.check(p, sources)
        labels = {f.label for f in result.findings if f.kind in ("mention", "missing_si")}
        assert labels == {"9z"}
    finally:
        p.close()


def test_dismissing(checked):
    p, c = checked
    result = M.check(p, M.read_all(p))
    f = next(f for f in result.problems if f.kind == "yield")
    with p.undo_step("Dismiss"):
        M.dismiss(p, f, "typo in the SI, fixed later")
    data = M.dismissed(p)
    assert data[f.key]["reason"] == "typo in the SI, fixed later"
    assert (M.folder(p) / M.DISMISSED_FILE).is_file()
    again = M.check(p, M.read_all(p))
    g = next(x for x in again.findings if x.key == f.key)
    assert g.status == M.DISMISSED and ("yield", "3b") not in _problems(again)
    # The values change: it comes back.
    c["3b"].values["yield"]["percent"] = "80"
    p.save_compound(c["3b"])
    assert ("yield", "3b") in _problems(M.check(p, M.read_all(p)))
    M.restore(p, f.key)
    assert f.key not in M.dismissed(p)


def test_files_cache_and_trash(checked):
    p, _c = checked
    p.enable_undo()
    names = [f.name for f in M.files(p)]
    assert names == ["ms.pdf", "scheme.cdxml"]
    M.read_all(p)
    cache = M.folder(p) / M.CACHE_FILE
    assert cache.is_file() and "ms.pdf" in cache.read_text(encoding="utf-8")
    assert [f.name for f in M.files(p)] == names      # (its own files are not listed)
    M.forget(p)
    assert M.cached_sources(p) is not None            # read back from check.json
    undo_before = p.can_undo
    M.read_all(p, force=True)
    assert p.can_undo == undo_before                # the cache is not an undo step
    # A changed file: the cache no longer counts.
    (M.folder(p) / "ms.pdf").write_bytes((M.folder(p) / "ms.pdf").read_bytes() + b"\n")
    assert M.cached_sources(p) is None
    # Remove to the Trash and restore.
    entry = M.remove_file(p, "scheme.cdxml")
    assert [f.name for f in M.files(p)] == ["ms.pdf"]
    assert "Restored" in p.restore_from_trash(entry.id)
    assert [f.name for f in M.files(p)] == names
    # The same name added again gets " (2)".
    assert M.add_file(p, M.folder(p) / "ms.pdf").name == "ms (2).pdf"


def test_cached_result_and_no_files(tmp_path):
    p, _c = S.build_si(tmp_path)
    try:
        assert M.cached_result(p) is None
        assert M.check(p, []).summary() == "No manuscript added yet."
    finally:
        p.close()


# ---------------------------------------------------------------------- review fixes (§13o)
# One regression test per finding of the review; all inputs are made up.

def _typst_pdf(path: Path, body: str) -> Path:
    import typst
    src = path.with_suffix(".typ")
    src.write_text(body, encoding="utf-8")
    typst.compile(str(src), output=str(path))
    return path


def _si(tmp_path, labels_values):
    """A small SI: [(ID, SMILES, yield, extra values)]."""
    from si_assistant.core.project import Project
    p = Project.create(tmp_path, "Made-up")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Made-up")
    p.set_id_table(s.id, [x[0] for x in labels_values])
    out = {}
    for label, smiles, y, extra in labels_values:
        c = p.add_compound(s.id, name="made-up compound")
        if smiles:
            p.set_structure(c, smiles)
        c.values["yield"] = {"percent": y, "mass": "", "mass_unit": "mg"}
        for key, value in (extra or {}).items():
            p.add_field(c, {"key": key})
            c.values[key] = {"value": value, "method": ""}
        p.save_compound(c)
        out[label] = c
    return p, out


def test_a1_ids_with_stereo_prefixes(tmp_path):
    """cis-5a and trans-5a are two compounds; (±)-5 and (S,S)-3b find 5 and 3b."""
    p, _c = _si(tmp_path, [("cis-5a", "C[C@H]1CC[C@@H](O)CC1", "45", None),
                           ("trans-5a", "C[C@H]1CC[C@H](O)CC1", "30", None),
                           ("5", "CCO", "70", None), ("3b", "CCN", "64", None)])
    try:
        src = ms_read.Source("ms.pdf", "pdf", records=[
            {"id": "cis-5a", "yield": "45", "where": "Scheme 3"},
            {"id": "trans-5a", "yield": "30", "where": "Scheme 3"},
            {"id": "(±)-5", "yield": "70", "where": "Scheme 3"},
            {"id": "(S,S)-3b", "yield": "64", "where": "Scheme 3"}])
        result = M.check(p, [src])
        assert not result.problems, [f.message for f in result.problems]
        ok = {(f.kind, f.label) for f in result.findings if f.status == M.OK}
        assert {("yield", "cis-5a"), ("yield", "trans-5a"), ("yield", "5"),
                ("yield", "3b")} <= ok
        assert result.checked == 4
        r = M.Resolver(["cis-5a", "trans-5a", "7"])
        assert r("5a") == "5a"            # two SI compounds could be meant: none chosen
        assert r("ent-7") == "7" and r("trans-5a") == "trans-5a"
    finally:
        p.close()


def test_a2_ee_in_brackets_is_part_of_the_result():
    assert P.annotate_text("3a, 92% (95% ee)")["ee"] == "95"
    rec = P.annotate_text("3a, 81% yield (90% ee)")
    assert (rec["yield"], rec["ee"]) == ("81", "90") and "alternative_results" not in rec
    assert P.annotate_text("3c, 70% (10:1 dr)")["dr"] == "10:1"
    # A second result in brackets is still kept apart.
    assert P.annotate_text("3b, 97% (58%: Pd = 3 mol %)")["alternative_results"]


def test_a3_arrow_conditions_do_not_label_a_structure(tmp_path):
    f1, b1 = S._fragment(100, "COC(=O)c1ccc(Br)cc1", 40, 40)
    f2, b2 = S._fragment(101, "COC(=O)c1ccc(-c2ccccc2)cc1", b1[2] + 70, 40)
    mid = (b1[2] + b2[0]) / 2
    cy = (b1[1] + b1[3]) / 2
    parts = [f1, f2, S._text(b1[0] + 10, b1[3] + 8, [("1a", 0)]),
             S._text(b2[0] + 20, b2[3] + 8, [("3a", 0), ("\n92%, 95% ee", 0)]),
             S._text(mid - 30, cy + 4, [("2a (1.5 equiv)\nPd (5 mol%), 24 h", 0)], 60)]
    recs = {r["id"]: r for r in ms_read.read_file(S.write_cdxml(tmp_path / "r.cdxml",
                                                                 parts)).records}
    assert set(recs) == {"1a", "3a"} and recs["1a"]["smiles"] == "COC(=O)c1ccc(Br)cc1"
    # Text along a drawn arrow is not a label even when it is only an ID ("2a").
    f3, b3 = S._fragment(102, "CCO", 40, 40)
    arrow = (f'<arrow id="9" Head3D="{b3[2] + 90} {cy} 0" Tail3D="{b3[2] + 10} {cy} 0" '
             f'BoundingBox="{b3[2] + 10} {cy - 3} {b3[2] + 90} {cy + 3}"/>')
    parts = [f3, arrow, S._text(b3[2] + 20, cy - 16, [("2a", 0)], 50)]
    src = ms_read.read_file(S.write_cdxml(tmp_path / "a.cdxml", parts))
    assert not src.records


def test_a4_labelled_structure_not_in_the_si_is_a_note(tmp_path):
    p, _c = S.build_si(tmp_path / "si")
    try:
        src = ms_read.Source("s.cdxml", "cdxml", records=[
            {"id": "1a", "smiles": "Brc1ccccc1", "where": "s.cdxml", "source": "cdxml"}])
        result = M.check(p, [src])
        f = next(f for f in result.findings if f.label == "1a")
        assert f.kind == "mention" and f.status == M.NOTE and not result.problems
        assert "a labelled structure" in f.message
    finally:
        p.close()


def test_a5_optimisation_table_footnotes_are_not_checked(tmp_path):
    p, _c = S.build_si(tmp_path / "si")
    try:
        recs = P.table_records({"rows": [
            {"fields": {"entry": "1", "product_id": "3a", "yield": "40"}},
            {"fields": {"entry": "2", "product_id": "3a", "yield": "92", "footnotes": ["b"]}}]})
        for r in recs:
            ms_read._resolve(r, {"b": "At 80 °C in DMF."})
        records = [ms_read._clean_record(r, "Table 1", "ms.pdf", "table", "high") for r in recs]
        result = M.check(p, [ms_read.Source("ms.pdf", "pdf", records=records)])
        assert not [f for f in result.findings if f.kind == "conditions"]
    finally:
        p.close()


def test_a6_word_table_with_merged_cells(tmp_path):
    tc = S.tc_props
    rows = ("<w:tr>" + tc("Entry", '<w:vMerge w:val="restart"/>')
            + tc("Product", '<w:vMerge w:val="restart"/>')
            + tc("Results", '<w:gridSpan w:val="2"/>') + "</w:tr>"
            + "<w:tr>" + tc("", "<w:vMerge/>") + tc("", "<w:vMerge/>") + tc("Yield (%)")
            + tc("ee (%)") + "</w:tr>"
            + "<w:tr>" + tc("1") + tc("3d") + tc("88") + tc("91") + "</w:tr>"
            + "<w:tr>" + tc("2") + tc("3e") + tc("76") + tc("93") + "</w:tr>")
    path = S.write_docx(tmp_path / "m.docx", S._p(("Table 1.", "b"), " Scope")
                        + "<w:tbl>" + rows + "</w:tbl>")
    recs = {r["id"]: r for r in ms_read.read_file(path).records}
    assert (recs["3d"]["yield"], recs["3d"]["ee"]) == ("88", "91")
    assert (recs["3e"]["yield"], recs["3e"]["ee"]) == ("76", "93")
    # A cell spanning two columns gives its text to the first of them only.
    rows = ("<w:tr>" + tc("Entry") + tc("Substrate") + tc("Product") + tc("Yield (%)")
            + "</w:tr><w:tr>" + tc("1") + tc("1d") + tc("3d") + tc("88") + "</w:tr><w:tr>"
            + tc("2") + tc("1e and 2e", '<w:gridSpan w:val="2"/>') + tc("76") + "</w:tr>")
    path = S.write_docx(tmp_path / "s.docx", "<w:tbl>" + rows + "</w:tbl>")
    assert [r["id"] for r in ms_read.read_file(path).records] == ["3d"]


def test_a7_label_lines_with_a_second_number():
    for text in ("3a, n = 2", "3a (from 1b)", "3a, 92%, 95% ee (2 steps)"):
        recs = P.label_records([_line(text, 50, 100)])
        assert [r["ids"] for r in recs] == [["3a"]], text
    recs = P.label_records([_line("3a (from 1b)", 50, 100), _line("92%, 95% ee", 45, 111)])
    assert recs[0]["yield"] == "92"


def test_a8_ring_fusion_letters_are_not_footnotes():
    rec = P.annotate_text("benzo[b]thiophene 3a, 70%")
    assert "footnotes" not in rec and rec["ids"] == ["3a"]
    assert P.annotate_text("8k[c]")["footnotes"] == ["c"]


def test_a9_body_text_starting_with_a_is_not_a_footnote(tmp_path):
    body = ("We made up this paragraph to stand here and it goes on for a while in the "
            "made-up text")
    lines = [_line(body, 30, 100, 400), _line("with more words here", 30, 113, 200),
             _line("a Lewis acid catalysts at 80 °C in DMF for 3 days were tried later",
                   30, 126, 400)]
    assert P.footnote_entries(lines) == []
    # The same plain letter straight under a scheme's labels is a footnote.
    lines = [_line("3a", 60, 100), _line("92%, 95% ee", 50, 111),
             _line("a Reaction run at 23 °C for 12 h.", 30, 124, 300)]
    assert [(m, t) for m, t, _y in P.footnote_entries(lines)] == [
        ("a", "Reaction run at 23 °C for 12 h.")]
    # Word: a plain "a …" paragraph only counts after a table, a figure or a footnote.
    doc = S.write_docx(tmp_path / "f.docx",
                       S._p("Ketone 3a", ("a", "sup"), " was obtained in 90% yield.")
                       + S._p("a Lewis acid at 80 °C was tried."))
    rec = ms_read.read_file(doc).records[0]
    assert rec["footnotes"] == ["a"] and not rec.get("conditions")
    doc = S.write_docx(tmp_path / "g.docx",
                       S._p("Ketone 3a", ("a", "sup"), " was obtained in 90% yield.")
                       + S._p(("Table 1.", "b"), " Made-up") + S._p("a Run at 80 °C."))
    assert ms_read.read_file(doc).records[0]["conditions"] == {"a": "Run at 80 °C."}


def test_a10_names_need_a_full_name_and_line_breaks_are_joined():
    text = ("The 2-naphthyl (3a) and electron-poor (3b) substrates reacted, as did the "
            "benzoic (3d) one. Methyl 4-bromo-benzoate (3c) too.")
    assert P.names_with_ids(text) == [("Methyl 4-bromo-benzoate", "3c")]
    assert M.same_name("Methyl 4-bromo-benzoate", "methyl 4-bromobenzoate")
    assert not M.same_name("methyl 3-bromobenzoate", "methyl 4-bromobenzoate")
    assert ms_read._dehyphenate("methyl 4-bromo-\nbenzoate (3c)") == "methyl 4-bromobenzoate (3c)"
    assert ms_read._dehyphenate("tert-\nbutyl and 2-\nmethyl and N-\nmethyl") == \
        "tert-butyl and 2-methyl and N-methyl"
    assert ms_read._dehyphenate("electro\xad\nchemical") == "electrochemical"


def test_a11_a12_deviations_ignore_work_up_and_equivalent_wordings(tmp_path):
    p, c = S.build_si(tmp_path / "si")
    try:
        src = ms_read.Source("ms.pdf", "pdf", records=[
            {"id": "3d", "yield": "88", "ee": "91", "where": "Table 1"}])
        # Work-up times and temperatures are not the reaction's.
        p.write_compound_text(c["3d"], "procedure", "After cooling to room temperature, the "
                              "mixture was diluted with water and stirred for 10 min.")
        assert not [f for f in M.check(p, [src]).problems if f.kind == "deviation"]
        # The reaction's own 36 h still counts, and so do equivalents and the solvent.
        p.write_compound_text(c["3d"], "procedure", "K3PO4 (3.0 equiv) in toluene was stirred "
                              "at 60 °C for 36 h. The mixture was cooled and filtered.")
        dev = {f.key: f for f in M.check(p, [src]).problems if f.kind == "deviation"}
        assert set(dev) == {"deviation:3d:time", "deviation:3d:equiv", "deviation:3d:solvent"}
        assert "toluene" in dev["deviation:3d:solvent"].message
        # A footnote saying "23 °C" agrees with "room temperature"; N2 with nitrogen;
        # overnight with 16 h.
        p.write_compound_text(c["3b"], "procedure", "Stirred at room temperature under "
                              "nitrogen for 16 h.")
        src = ms_read.Source("ms.pdf", "pdf", records=[
            {"id": "3b", "yield": "82", "footnotes": ["b"], "where": "Scheme 2",
             "conditions": {"b": "Reaction run at 23 °C under N2 overnight."}}])
        assert not [f for f in M.check(p, [src]).problems if f.kind == "conditions"]
    finally:
        p.close()
    q = P.Quantity
    assert q("temperature", "rt", "rt").same(q("temperature", 23.0, "23 °C"))
    assert not q("temperature", "rt", "rt").same(q("temperature", 40.0, "40 °C"))
    assert q("time", "overnight", "overnight").same(q("time", 960.0, "16 h"))
    assert not q("time", "overnight", "overnight").same(q("time", 2880.0, "48 h"))
    assert {x.value for x in P.reagents("under argon")} == {"Ar"}
    assert {x.value for x in P.reagents("under an Ar atmosphere")} == {"Ar"}
    assert "Ar" not in {x.value for x in P.reagents("Ar = 4-MeOC6H4")}
    assert {x.value for x in P.reagents("in air")} == {"O2"}


def test_a13_dr_agrees_at_the_printed_precision():
    assert M.same_dr("5:1", "83:17")      # 83:17 = 4.9:1; "5:1" stands for 4.5–5.5:1
    assert M.same_dr("10:1", "91:9") and not M.same_dr("10:1", "5:1")
    assert not M.same_dr("5:1", "90:10")  # 9:1
    assert not M.same_dr("83:17", "80:20")   # the same format: exactly


def test_a14_ids_with_two_stereo_descriptors():
    assert P.annotate_text("(S,S)-3b, 64%")["ids"] == ["(S,S)-3b"]
    assert P.find_ids("(1R,2S)-3b and (1R, 2S)-4b") == ["(1R,2S)-3b", "(1R,2S)-4b"]
    assert P.id_base("(1R,2S)-3b") == "3b"


@pytest.mark.parametrize("width", ["12cm", "14cm"])
def test_a15_justified_body_text_is_not_a_figure_label(tmp_path, width):
    body = (f"#set page(width: {width}, height: 25cm, margin: 1.5cm, columns: 2)\n"
            "#set text(size: 10pt)\n#set par(justify: true)\n"
            "We next examined the scope. Under these conditions the made-up substrate gave "
            "the expected product in good yield after a short time. When the made-up ligand "
            "was changed, the made-up product 3c was obtained but the selectivity dropped to "
            "80% in that case, and the product was isolated cleanly. The tert-butyl variant "
            "behaved similarly and gave a modest result overall in all made-up cases.\n\n"
            "Further experiments with the made-up catalyst showed that the reaction of 4b "
            "required longer times; extending the reaction to 48 h raised it to 70% in that "
            "case and also others which we do not discuss further here.\n")
    src = ms_read.read_file(_typst_pdf(tmp_path / "t.pdf", body))
    assert not [r for r in src.records if r["source"] == "figure"]


def test_a16_rotated_table_is_read(tmp_path):
    body = ("#set page(width: 17cm, height: 24cm, margin: 1.5cm)\n#set text(size: 9pt)\n"
            "Some made-up text.\n\n#rotate(-90deg, reflow: true)[*Table 1.* Made-up "
            "products\n\n#table(columns: 4, stroke: none, column-gutter: 1.5em, [Entry], "
            "[Product], [Yield (%)], [ee (%)], [1], [3d], [88], [91], [2], [3e], [76], [93])]\n")
    src = ms_read.read_file(_typst_pdf(tmp_path / "r.pdf", body))
    table = {r["id"]: r for r in src.records if r["source"] == "table"}
    assert (table["3d"]["yield"], table["3e"]["ee"]) == ("88", "93")
    assert table["3d"]["where"] == "Table 1, p. 1"
    body = body.replace("#rotate(-90deg, reflow: true)", "#rotate(-30deg)")
    src = ms_read.read_file(_typst_pdf(tmp_path / "s.pdf", body))
    assert any("slant" in n for n in src.notes)


def test_a17_cdxml_line_starts_count_the_stored_text():
    import xml.etree.ElementTree as ET
    raw = "aIsolated yields. bReaction run for 48 h at 60 °C."
    cut = raw.index("at 60")
    t = ET.fromstring(f'<t BoundingBox="0 0 200 30" LineStarts="{cut} {len(raw)}">'
                      '<s face="64">a</s><s face="0">Isolated yields. </s><s face="64">b</s>'
                      '<s face="0">Reaction run for 48 h at 60 °C.</s></t>')
    text, _bold = ms_read._cdxml_text(t)
    assert text == "^aIsolated yields. ^bReaction run for 48 h \nat 60 °C."


def test_a18_deeply_nested_cdxml_does_not_crash(tmp_path):
    path = tmp_path / "deep.cdxml"
    path.write_bytes(b"<CDXML>" + b"<group>" * 30000 + b"</group>" * 30000 + b"</CDXML>")
    src = ms_read.read_file(path)        # RDKit would overflow its stack on this
    assert not src.records and any("nested" in n for n in src.notes)


def test_a19_absolute_image_targets_in_word():
    assert ms_read._part_name("/word/media/image1.emf") == "word/media/image1.emf"
    assert ms_read._part_name("media/image1.emf") == "word/media/image1.emf"
    assert ms_read._part_name("../media/x.emf") == "media/x.emf"


def test_a20_system_files_are_not_manuscript_files(tmp_path):
    p, _c = S.build_si(tmp_path / "si")
    try:
        M.folder(p).mkdir(exist_ok=True)
        for name in (".DS_Store", "desktop.ini", "Thumbs.db", "._ms.pdf", "~$ms.docx"):
            (M.folder(p) / name).write_bytes(b"x")
        M.add_file(p, S.make_pdf(tmp_path / "ms.pdf"))
        assert [f.name for f in M.files(p)] == ["ms.pdf"]
    finally:
        p.close()


def test_a21_structure_fingerprint_follows_the_structure():
    from rdkit import Chem
    si = Chem.MolFromSmiles("CCCO")
    a = M.compare_structures("CC(C)O", si)        # an isomer: the same formula
    b = M.compare_structures("COCC", si)
    assert a[0] == b[0] == "different" and a[2] == b[2] and a[1] != b[1]


def test_a22_cache_is_read_again_by_a_new_reader(checked, monkeypatch):
    p, _c = checked
    M.read_all(p)
    M.forget(p)
    assert M.cached_sources(p) is not None
    M.forget(p)
    monkeypatch.setattr(ms_read, "READER_VERSION", ms_read.READER_VERSION + 1)
    assert M.cached_sources(p) is None


def test_a23_footnotes_in_a_word_tables_last_row(tmp_path):
    tc = S.tc_props
    rows = ("<w:tr>" + tc("Entry") + tc("Product") + tc("Yield (%)") + "</w:tr>"
            + "<w:tr>" + S._tc("1") + S._tc("3d") + S._tc("88") + "</w:tr>"
            + "<w:tr>" + S._tc("2", ("c", "sup")) + S._tc("3e") + S._tc("76") + "</w:tr>"
            + "<w:tr>" + tc("a Isolated yield. ", '<w:gridSpan w:val="3"/>')
            .replace("</w:p></w:tc>", "</w:p>" + S._p(("c", "sup"), S.FOOTNOTE_C) + "</w:tc>")
            + "</w:tr>")
    src = ms_read.read_file(S.write_docx(tmp_path / "t.docx", "<w:tbl>" + rows + "</w:tbl>"))
    recs = {r["id"]: r for r in src.records}
    assert recs["3e"]["conditions"] == {"c": S.FOOTNOTE_C}


def test_a24_cdxml_footnotes_belong_to_their_own_scheme(tmp_path):
    f1, b1 = S._fragment(100, "COC(=O)c1ccc(Br)cc1", 40, 40)
    f2, b2 = S._fragment(101, "CC(=O)c1ccccc1", 40, 300)
    parts = [f1, S._text(b1[0], b1[3] + 8, [("3a", 1), ("b", 64), ("\n92%", 0)]),
             S._text(40, b1[3] + 60, [("b", 64), ("Reaction run at 40 °C.", 0)], 300),
             f2, S._text(b2[0], b2[3] + 8, [("4a", 1), ("b", 64), ("\n70%", 0)]),
             S._text(40, b2[3] + 60, [("b", 64), ("Reaction run for 48 h.", 0)], 300)]
    recs = {r["id"]: r for r in ms_read.read_file(S.write_cdxml(tmp_path / "two.cdxml",
                                                                 parts)).records}
    assert recs["3a"]["conditions"] == {"b": "Reaction run at 40 °C."}
    assert recs["4a"]["conditions"] == {"b": "Reaction run for 48 h."}


def test_b1_running_text_values_are_notes_quoting_the_sentence(tmp_path):
    p, _c = S.build_si(tmp_path / "si")
    try:
        text = ("Using Pd(OAc)2 instead, compound 3b formed in 85% yield. Without the ligand, "
                "3e was formed in only 12% yield. On a 5 mmol scale, 3a was obtained in 80% "
                "yield. The amine 3h was isolated in 64% yield.")
        recs = P.sentence_records(text)
        assert [r["ids"][0] for r in recs] == ["3b", "3h"]     # control and scale-up skipped
        records = [ms_read._clean_record(r, "paragraph 3", "ms.docx", "text", "medium")
                   for r in recs]
        result = M.check(p, [ms_read.Source("ms.docx", "docx", records=records)])
        assert not result.problems
        note = next(f for f in result.findings if f.label == "3b" and f.kind == "yield")
        assert note.status == M.NOTE and "“Using Pd(OAc)2 instead, compound 3b formed in 85% " \
            "yield.”" in note.message and note.where == "Word file, paragraph 3"
        ok = next(f for f in result.findings if f.label == "3h" and f.kind == "yield")
        assert ok.status == M.OK
        # Only in the text and not in the SI: a note too, never a problem.
        rec = ms_read._clean_record(P.sentence_records("Ketone 9z formed in 50% yield.")[0],
                                    "p. 2", "ms.pdf", "text", "medium")
        result = M.check(p, [ms_read.Source("ms.pdf", "pdf", records=[rec])])
        assert [(f.kind, f.status) for f in result.findings] == [("mention", M.NOTE)]
    finally:
        p.close()


def test_b5_where_lists_every_source_with_its_file_type(tmp_path):
    p, _c = S.build_si(tmp_path / "si")
    try:
        sources = [ms_read.Source("ms.docx", "docx", records=[
                       {"id": "3d", "yield": "88", "where": "Table 1", "source": "table"}]),
                   ms_read.Source("ms.pdf", "pdf", records=[
                       {"id": "3d", "yield": "88", "where": "Table 1, p. 1",
                        "source": "table"}]),
                   ms_read.Source("s.cdxml", "cdxml", records=[
                       {"id": "3d", "smiles": "CC(O)c1ccccc1", "where": "s.cdxml",
                        "source": "cdxml"}])]
        result = M.check(p, sources)
        f = next(f for f in result.findings if f.kind == "yield" and f.label == "3d")
        assert f.where == "Table 1 (Word); Table 1, p. 1 (PDF); s.cdxml"
        assert not any("((" in x.message or "))" in x.message for x in result.findings)
    finally:
        p.close()
