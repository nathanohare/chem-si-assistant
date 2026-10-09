"""Audit fixes for the checks, links, compound IDs, impurities and dashboard code."""

import json

import pytest

from si_assistant.core import chem, checks, compound_ids, dashboard, impurities, labels, links
from si_assistant.core.compound_ids import IdReport, SectionIds
from si_assistant.core.project import SECTION_FILE, Project


@pytest.fixture
def section(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["3a", "3b", "3c", "3d"])
    return s


@pytest.fixture
def make(project, section):
    def make_compound(smiles, **values):
        c = project.add_compound(section.id)
        project.set_structure(c, smiles)
        for k, v in values.items():
            c.values[k] = v
        project.save_compound(c)
        return c
    return make_compound


def active(project, c, check=None):
    return [w for w in checks.active_warnings(project, c, IdReport())
            if check is None or w.check == check]


def keys(project, c, check=None):
    return [w.key for w in active(project, c, check)]


# ---------------------------------------------------------------------- checks-dashboard-3

def test_id_table_with_numbers_or_nulls_is_read_as_text():
    report = compound_ids.check_ids([SectionIds("s1", "Products", [1, 2, None, 3.5],
                                                ["c1", "c2", "c3", "c4"], [None, None, None, 7])])
    assert report.ids == {"c1": "1", "c2": "2", "c3": None, "c4": "7"}
    assert compound_ids.assign_ids([None, ""], [1, "  "]) == ["1", None]
    # Not a list at all (a hand-edited "3a, 3b"): counts as an empty table, not letters.
    report = compound_ids.check_ids([SectionIds("s1", "Products", "3a, 3b", ["c1"], [None])])
    assert report.ids == {"c1": None} and not report.table_warnings


def test_hand_edited_numeric_id_table_still_opens(project, section):
    a = project.add_compound(section.id)
    b = project.add_compound(section.id)
    path = section.folder / SECTION_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id_table"] = [1, 2]
    path.write_text(json.dumps(data), encoding="utf-8")
    project.close()
    reopened = Project.open(project.folder)
    try:
        report = reopened.id_report()
        assert report.display_id(a.id) == "1" and report.display_id(b.id) == "2"
    finally:
        reopened.close()


# ---------------------------------------------------------------------- checks-dashboard-8

@pytest.mark.parametrize("bad", ["5 ppm", None, [5]])
def test_unreadable_hrms_tolerance_uses_5_ppm(project, section, make, bad):
    c = make("CCO", hrms={"ionization": "ESI", "adduct": "[M+H]+", "found": "47.0600"})
    project.settings["hrms_tolerance_ppm"] = bad
    result, problem = checks.compound_hrms(project, c)
    assert result.tolerance == 5.0 and "±5 ppm" in problem
    assert "hrms:ppm" in [w.key for w in checks.all_warnings(project, c, project.id_report())]
    (row,) = dashboard.build_rows(project, section)
    assert row.cells["hrms"].warnings


# ---------------------------------------------------------------------- qt-lifetime-2

def test_impurity_matches_follow_the_edited_table():
    table = [{"solvent": "chloroform-d", "nucleus": "1H", "shift": 2.17, "what": "acetone",
              "on": True},
             {"solvent": "MeOD", "nucleus": "1H", "shift": 4.87, "what": "water", "on": True},
             {"solvent": "CDCl3", "nucleus": "1H", "shift": 1.56, "what": "water", "on": False}]
    for _ in range(2):   # (the second time from the remembered names)
        assert impurities.matches("CDCl3", "1H", 2.17, True, table) == "acetone"
        assert impurities.matches("CD3OD", "1H", 4.87, True, table) == "water"
        assert impurities.matches("CDCl3", "1H", 1.56, True, table) == ""   # switched off
    table[0]["solvent"] = "weird"   # an edit to the table is used at once
    assert impurities.matches("CDCl3", "1H", 2.17, True, table) == ""
    assert impurities.normalise_solvent(" my solvent ") == "my solvent"
    assert impurities.normalise_solvent("cdcl3") == impurities.normalise_solvent("cdcl3") == "CDCl3"


# ---------------------------------------------------------------------- profile-core-2 / -3

@pytest.fixture
def mentions(project, section):
    """Three compounds whose procedures mention the one before and a table."""
    notes = project.add_section("text", "Optimization")
    table = project.add_table(project.text_owner(notes.id))
    project.write_section_text(notes, f"!table{{{table}}} Screening")
    cs = [project.add_compound(section.id, name=f"C{i}") for i in range(3)]
    for prev, c in zip(cs, cs[1:]):
        project.write_compound_text(c, "procedure",
                                    f"From @{{{prev.id}}} (see @{{{table}}}).")
    return notes, table, cs


def summaries(project, section):
    return {r.compound.id: (r.cells["procedure"].summary, r.checks)
            for r in dashboard.build_rows(project, section)}


def test_plain_text_with_shared_report_and_labels(project, section, mentions):
    _notes, table, cs = mentions
    text = f"From @{{{cs[0].id}}} and @{{c-gone}} (see @{{{table}}} and @{{t-gone}})."
    plain = links.plain_text(project, text)
    assert plain == "From 3a and ?? (see Table S1 and ??)."
    assert links.plain_text(project, text, project.id_report(), labels.compute(project)) == plain


def test_dashboard_works_out_labels_once_and_stays_current(project, section, mentions,
                                                           monkeypatch):
    notes, table, cs = mentions
    calls = []
    real = labels.compute
    monkeypatch.setattr(labels, "compute", lambda *a, **k: calls.append(1) or real(*a, **k))
    rows = summaries(project, section)
    assert len(calls) == 1
    assert rows[cs[2].id][0] == "From 3b (see Table S1)."
    dashboard.project_rows(project)
    assert len(calls) == 2   # one for the whole project
    # After changes, the next refresh shows them: a new ID and a deleted table.
    project.set_id_table(section.id, ["5a", "5b", "5c"])
    project.write_section_text(notes, "No table any more.")
    rows = summaries(project, section)
    assert rows[cs[2].id][0] == "From 5b (see ??)."
    assert rows[cs[2].id][1].get("mention") == 1


def test_active_warnings_same_with_shared_labels(project, section, mentions):
    notes, _table, cs = mentions
    project.write_section_text(notes, "No table any more.")
    report = project.id_report()
    shared = labels.compute(project)
    for c in cs:
        alone = [(w.key, w.message, w.fingerprint)
                 for w in checks.active_warnings(project, c, report)]
        assert alone == [(w.key, w.message, w.fingerprint)
                         for w in checks.active_warnings(project, c, report, shared)]
    assert any(w.check == "mention" for w in checks.active_warnings(project, cs[1], report, shared))


# ---------------------------------------------------------------------- profile-core-5

def test_structure_info_worked_out_once_and_follows_the_structure(project, make, monkeypatch):
    c = make("OCc1ccc(F)cc1",
             nmr=[{"nucleus": "1H", "label": "", "text": "δ 7.3 (m, 4H), 4.6 (s, 2H)"},
                  {"id": "r1", "nucleus": "13C", "label": "", "text": ""}],
             spectra=[], scale={"value": "1.0", "unit": "mmol"})
    c.values["yield"] = {"percent": "60", "mass": "63.1", "mass_unit": "mg"}
    project.save_compound(c)
    first = [(w.key, w.message) for w in checks.all_warnings(project, c, IdReport())]
    calls = []
    real = chem.mol_info
    monkeypatch.setattr(chem, "mol_info", lambda mol: calls.append(1) or real(mol))
    assert first == [(w.key, w.message) for w in checks.all_warnings(project, c, IdReport())]
    assert len(calls) == 1   # (the HRMS pieces are remembered from the first pass)
    found = dict(first)
    assert "heteroatom:19F" in found and "yield" in found and "MW 126.13" in found["yield"]
    # The ¹H report has no "id": it still counts as reported (no spectrum warning for 13C).
    assert "spectra:missing:1H" in found and "spectra:missing:13C" not in found
    assert first == [(w.key, w.message) for w in checks.all_warnings(project, c, IdReport())]
    project.set_structure(c, "OCc1ccccc1")   # no F any more, other MW
    after = dict((w.key, w.message) for w in checks.all_warnings(project, c, IdReport()))
    assert "heteroatom:19F" not in after and "MW 108.14" in after["yield"]


# ---------------------------------------------------------------------- hunt-chemistry-numbers-verified-1

def info(formula):
    return chem.info_from_counts(chem.parse_m_formula(formula))


def test_monoisotopic_peak_always_in_the_hrms_peaks():
    mono = chem.ion_mz(info("C42H24Br6"), "[M]+").mz       # ⁷⁹Br₆ (14th most intense)
    found = mono * (1 + 1e-6)
    r = checks.hrms_calc(info("C42H24Br6"), "[M]+", f"{found:.4f}", 5.0)
    assert r.monoisotopic.formula == "C42H24Br6" and r.monoisotopic.mz == pytest.approx(mono)
    assert r.matched.monoisotopic and r.ppm == pytest.approx(1.0, abs=0.1)
    assert not r.out_of_tolerance and len(r.peaks) == 13
    # A value on the most intense peak (⁷⁹Br₃⁸¹Br₃) is still matched to it.
    top = r.peaks[0]
    r2 = checks.hrms_calc(info("C42H24Br6"), "[M]+", f"{top.mz:.4f}", 5.0)
    assert r2.matched.formula == top.formula and abs(r2.ppm) < 1
    # Multi-tin: the monoisotopic peak is matched, not a near-isobaric one.
    mono = chem.ion_mz(info("C36H81Cl3Sn3"), "[M+H]+").mz
    r3 = checks.hrms_calc(info("C36H81Cl3Sn3"), "[M+H]+", f"{mono * (1 + 1e-6):.4f}", 5.0)
    assert r3.matched.formula == "C36H82Cl3Sn3" and r3.ppm == pytest.approx(1.0, abs=0.1)
    # Ordinary compounds are unchanged (12 peaks at most, the monoisotopic one among them).
    r4 = checks.hrms_calc(info("C6H5Br"), "[M+H]+", "156.9648", 5.0)
    assert r4.matched.monoisotopic and all(p.abundance >= 1 for p in r4.peaks)


def test_monoisotopic_peak_below_one_percent():
    r = checks.hrms_calc(info("C10Br20"), "[M]+", "", 5.0)
    mono = chem.ion_mz(info("C10Br20"), "[M]+")
    assert r.monoisotopic.monoisotopic and r.monoisotopic.formula == mono.formula
    assert r.monoisotopic.mz == pytest.approx(mono.mz) and r.monoisotopic.abundance < 1


def test_monoisotopic_abundance_worked_out_directly():
    """review-5: the monoisotopic peak added for e.g. several Sn/Br atoms is worked out
    directly (not from the whole isotope pattern), with the same abundance."""
    import time
    for formula, adduct in [("C10H10Sn6Br6", "[M+H]+"), ("C2Hg4Br8Cl8", "[M+H]+"),
                            ("C12Br10", "[M]+"), ("C36H81Cl3Sn3", "[M+H]+")]:
        full = next(p for p in chem.isotope_pattern(info(formula), adduct, min_percent=0.0,
                                                    max_peaks=10 ** 6) if p.monoisotopic)
        start = time.perf_counter()
        peak = checks._monoisotopic_peak(info(formula), adduct)
        assert time.perf_counter() - start < 0.05
        assert peak.monoisotopic and peak.formula == full.formula
        assert peak.mz == pytest.approx(full.mz, abs=1e-9)
        assert peak.abundance == pytest.approx(full.abundance, rel=1e-9)
    # Nothing that big is kept: hrms_calc asks only for the 12 main peaks.
    chem._PATTERNS.clear()
    r = checks.hrms_calc(info("C10H10Sn6Br6"), "[M+H]+", "", 5.0)
    assert len(r.peaks) == 13 and r.peaks[-1].monoisotopic
    assert all(len(v) <= 12 for v in chem._PATTERNS.values())


# ---------------------------------------------------------------------- checks-dashboard-4

def test_dismissed_hrms_warning_survives_unrelated_changes(project, make):
    c = make("CCOC(=O)c1ccoc1C", hrms={"ionization": "ESI", "adduct": "[M+H]+",
                                       "found": "155.0803"})
    (w,) = active(project, c, "hrms")
    assert w.key == "hrms:ppm"
    project.dismiss_warning(c, w)
    project.set_hrms_print(c, False)
    assert active(project, c, "hrms") == []
    c.values["hrms"]["ionization"] = "APCI"
    c.values["hrms"] = dict(reversed(list(c.values["hrms"].items())))   # another key order
    c.values["hrms"].update(species="", formula="")
    project.save_compound(c)
    assert active(project, c, "hrms") == []
    c.values["hrms"]["found"] = "155.0900"   # its own data: back
    project.save_compound(c)
    assert keys(project, c, "hrms") == ["hrms:ppm"]


# ---------------------------------------------------------------------- checks-dashboard-5

def test_dismissed_heteroatom_and_spectra_warnings_stay_when_other_nuclei_are_filled(project,
                                                                                    make):
    c = make("FC(F)(F)c1ccccc1",
             nmr=[{"id": "h", "nucleus": "1H", "label": "", "text": ""},
                  {"id": "c", "nucleus": "13C", "label": "", "text": ""}])
    (w,) = active(project, c, "heteroatom")
    project.dismiss_warning(c, w, "not measured")
    c.values["nmr"][0]["text"] = "δ 7.5 (m, 5H)"
    project.save_compound(c)
    assert active(project, c, "heteroatom") == []
    (s,) = active(project, c, "spectra")
    assert s.key == "spectra:missing:1H"
    project.dismiss_warning(c, s)
    c.values["nmr"][1]["text"] = "δ 131.0, 128.9, 125.7"
    c.values["spectra"] = [{"type": "13C", "file": "13c.pdf"}]
    project.save_compound(c)
    assert active(project, c, "spectra") == [] and active(project, c, "heteroatom") == []
    project.set_structure(c, "Fc1ccccc1")   # another structure: the 19F warning is back
    assert keys(project, c, "heteroatom") == ["heteroatom:19F"]


def test_dismissed_gp_value_warning_stays_when_another_value_is_typed(project, section):
    c = project.add_compound(section.id)
    gp = project.add_general_procedure(section.id)
    project.update_general_procedure(gp["id"], sentence="Stirred at {temp} for {time}.")
    c.values["procedure"] = {"gp": gp["id"], "values": {}}
    project.save_compound(c)
    ws = {w.key: w for w in active(project, c, "gp")}
    assert set(ws) == {"gp:value:temp", "gp:value:time"}
    project.dismiss_warning(c, ws["gp:value:temp"])
    c.values["procedure"]["values"]["time"] = "2 h"
    project.save_compound(c)
    assert keys(project, c, "gp") == []
    project.update_general_procedure(gp["id"], sentence="Heated at {temp} for {time}.")
    assert keys(project, c, "gp") == ["gp:value:temp"]


def test_checks_run_when_the_structure_has_no_formula(project, make, monkeypatch):
    """A structure whose formula can't be worked out (e.g. one with a generic atom R) still
    gets the other checks; the ones that need a formula are left out."""
    c = make("OCc1ccc(F)cc1", nmr=[{"id": "h", "nucleus": "1H", "label": "", "text": ""}],
             scale={"value": "1.0", "unit": "mmol"})
    c.values["yield"] = {"percent": "60", "mass": "63.1", "mass_unit": "mg"}
    project.save_compound(c)

    def no_formula(mol):
        raise chem.ChemError("This structure has a generic atom (R).")
    monkeypatch.setattr(chem, "mol_info", no_formula)
    checks._SPECIES.clear()   # (nothing remembered from before)
    found = [w.key for w in checks.all_warnings(project, c, IdReport())]
    assert "yield" not in found and "heteroatom:19F" not in found
    # The dashboard still shows the compound (with no formula).
    (row,) = dashboard.build_rows(project, project.sections[0])
    assert row.cells["structure"].summary == ""
    # A typed M formula for HRMS: nothing to compare it with, but no crash either.
    assert "hrms" in c.field_keys
    c.values["hrms"] = {"formula": "C7H7FO", "adduct": "[M+H]+", "found": ""}
    project.save_compound(c)
    found = [w.key for w in checks.all_warnings(project, c, IdReport())]
    assert "hrms:formula-differs" not in found and "hrms:formula-unreadable" not in found
    (row,) = dashboard.build_rows(project, project.sections[0])


def _dismiss_as_before(project, c, key, fingerprint):
    """A dismissal saved by the version before the fingerprints changed."""
    c.data.setdefault("dismissed", {})[key] = {"fingerprint": fingerprint, "message": "",
                                               "reason": "checked", "by": "", "at": ""}
    project.save_compound(c)


def test_warnings_dismissed_before_the_update_stay_dismissed(project, section, make):
    """review-2: HRMS, spectra, heteroatom and GP-value warnings dismissed with the old
    fingerprints stay dismissed until their data changes."""
    value = {"ionization": "ESI", "adduct": "[M+H]+", "found": "180.0500"}
    c = make("FC(F)(F)c1ccccc1", hrms=dict(value),
             nmr=[{"id": "h", "nucleus": "1H", "label": "", "text": "δ 7.5 (m, 5H)"}],
             spectra=[])
    smiles = chem.mol_info(project.read_structure(c)).smiles
    assert set(keys(project, c)) >= {"hrms:ppm", "spectra:missing:1H", "heteroatom:19F"}
    _dismiss_as_before(project, c, "hrms:ppm", checks._fp(smiles, value, project.settings.get("hrms_tolerance_ppm")))
    _dismiss_as_before(project, c, "spectra:missing:1H", checks._fp(["1H"], []))
    _dismiss_as_before(project, c, "heteroatom:19F", checks._fp(smiles, ["1H"]))
    assert {"hrms:ppm", "spectra:missing:1H", "heteroatom:19F"}.isdisjoint(keys(project, c))
    assert {w.key for w, _d in checks.dismissed_warnings(project, c, IdReport())} >= {
        "hrms:ppm", "spectra:missing:1H", "heteroatom:19F"}
    c.values["hrms"]["found"] = "180.0600"   # its data changed: back
    c.values["nmr"].append({"id": "c", "nucleus": "13C", "label": "", "text": "δ 131.0"})
    project.save_compound(c)
    assert {"hrms:ppm", "spectra:missing:1H", "heteroatom:19F"} <= set(keys(project, c))

    d = project.add_compound(section.id)
    gp = project.add_general_procedure(section.id)
    sentence = "Stirred at {temp} for {time}."
    project.update_general_procedure(gp["id"], sentence=sentence)
    d.values["procedure"] = {"gp": gp["id"], "values": {"time": "2 h"}}
    project.save_compound(d)
    _dismiss_as_before(project, d, "gp:value:temp", checks._fp(sentence, {"time": "2 h"}))
    assert keys(project, d, "gp") == []
    d.values["procedure"]["values"]["time"] = "3 h"
    project.save_compound(d)
    assert keys(project, d, "gp") == ["gp:value:temp"]


# ---------------------------------------------------------------------- handoffs from other groups

def test_figure_labels_count_only_the_files_the_si_prints(project, section):
    """compile-4: labels.compute counts exactly the inline files blocks.inline_figures
    prints, and a hand-edited "files": ["a.png"] no longer crashes it."""
    from si_assistant.compile import blocks
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "files"})
    c.values["files"] = ["a.png", {"file": ""}, {"file": "b.png", "id": "f1"},
                         {"file": "c.png", "placement": "end"}, None]
    project.save_compound(c)
    figures = [i for i in labels.compute(project).items if i.kind == "figure"]
    assert [i.id for i in figures] == ["f1"]
    assert len(blocks.inline_figures(project, c)) == len(figures)
    c.values["files"] = "a.png"   # not a list at all
    project.save_compound(c)
    assert [i for i in labels.compute(project).items if i.kind == "figure"] == []


@pytest.mark.parametrize("odd", [True, 0, ["x.png"], {"a": 1}, None, 2.5])
def test_figure_labels_follow_the_printed_files_for_odd_file_values(project, section, odd):
    """review-4: each figure the SI prints with a compound gets the label labels.compute
    gives at its index, whatever a hand-edited "file" holds."""
    from si_assistant.compile import blocks
    from si_assistant.core import fields as F
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "files"})
    c.values["files"] = [{"id": "fa", "file": odd}, {"id": "fb", "file": "b.png"}]
    project.save_compound(c)
    computed = labels.compute(project)
    printed = blocks.inline_figures(project, c)
    figures = [i for i in computed.items if i.kind == "figure"]
    assert len(figures) == len(printed)
    expected = [i["id"] for i in c.values["files"] if F.part_text(i.get("file"))]
    assert [computed.at(c.id, n).id for n in range(len(printed))] == expected
    assert computed.at(c.id, len(printed)) is None


def test_typed_purification_sentence_hidden_by_the_gp_is_warned(project, section):
    """hunt-ui-vs-compiled-agreement-2: a "Printed as" sentence that the general procedure
    replaces with {purification} gets a (dismissable) warning."""
    gp = project.add_general_procedure(section.id, "General Procedure A")
    project.update_general_procedure(gp["id"], sentence="Purified by {purification}.")
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "purification"})
    c.values["purification"] = {"method": "distillation", "sentence": "Distilled (bp 80 °C)."}
    c.values["procedure"] = {"gp": gp["id"], "values": {}}
    project.save_compound(c)
    (w,) = active(project, c, "gp")
    assert w.key == "gp:purification-sentence" and w.field == "purification"
    assert w.message.startswith("“General Procedure A” places {purification} itself")
    project.dismiss_warning(c, w)
    assert keys(project, c, "gp") == []
    c.values["purification"]["sentence"] = "Distilled (bp 82 °C)."   # a new sentence: back
    project.save_compound(c)
    assert keys(project, c, "gp") == ["gp:purification-sentence"]
    # No typed sentence, or a GP that doesn't place {purification}: no warning.
    c.values["purification"]["sentence"] = ""
    project.save_compound(c)
    assert keys(project, c, "gp") == []
    c.values["purification"]["sentence"] = "Distilled."
    project.save_compound(c)
    project.update_general_procedure(gp["id"], sentence="Prepared according to {GP}.")
    assert keys(project, c, "gp") == []


def test_typed_deuterium_formula_matches_a_drawn_cd3(project, make):
    """hunt-chemistry-numbers-verified-5: [2H] typed in the HRMS formula is the same M as
    a structure drawn with D."""
    c = make("[2H]C([2H])([2H])OC(=O)c1ccccc1",
             hrms={"formula": "C8H5[2H]3O2", "adduct": "[M+H]+", "found": ""})
    assert "hrms:formula-differs" not in keys(project, c, "hrms")
    c.values["hrms"]["formula"] = "C8H6[2H]2O2"
    project.save_compound(c)
    assert "hrms:formula-differs" in keys(project, c, "hrms")


def test_reference_that_cannot_be_printed_as_typed_is_warned(project):
    """undo-files-2: a year or web address the journal style leaves out is a project
    warning that opens the References page."""
    project.add_reference({"authors": ["Fulmer, G. R."], "title": "NMR shifts",
                           "year": "in press", "url": "https://example.org/a"})
    (w,) = [w for w in checks.project_warnings(project) if w.check == "reference"]
    assert "Fulmer" in w.message and "“in press”" in w.message
    assert w.place == ("references", "references")
    # review-1: the project dashboard's warnings chart names it in plain words
    assert dashboard.summarize(project).warnings == [
        ("References that can't be printed as typed", 1)]
    project.references[0]["year"] = "2010"
    assert [w for w in checks.project_warnings(project) if w.check == "reference"] == []


def test_impurity_table_rows_that_are_not_rows_are_skipped(project, make):
    """app-shell-2: a hand-edited impurity_table with stray entries doesn't crash the NMR
    impurity check."""
    project.settings["impurity_table"] = [
        "junk", None, {"solvent": "CDCl3", "nucleus": "1H", "shift": 7.26,
                       "what": "residual CHCl₃", "on": True}]
    assert impurities.project_table(project.settings) == [project.settings["impurity_table"][2]]
    c = make("CCO", nmr=[{"id": "h", "nucleus": "1H", "label": "",
                          "text": "¹H NMR (400 MHz, CDCl3) δ 7.26 (s, 1H)"}])
    assert keys(project, c, "impurity") == ["impurity:h:7.26"]


def _write_generic_structure(project, c, smiles):
    """A structure.mol with a generic atom, as set_structure, an older version or a hand
    edit leaves it."""
    from rdkit import Chem
    project.structure_path(c).write_text(Chem.MolToMolBlock(Chem.MolFromSmiles(smiles)),
                                         encoding="utf-8")


def test_structure_with_a_generic_atom_is_warned(project, section):
    """review-2: a structure.mol with an R/* is read as no structure; a warning says why."""
    c = project.add_compound(section.id)
    assert keys(project, c, "structure") == []
    _write_generic_structure(project, c, "*CC(*)c1ccccc1")
    assert project.read_structure(c) is None
    (w,) = active(project, c, "structure")
    assert w.field == "structure" and w.key == "structure:generic"
    assert "generic atom (R)" in w.message and "type the M formula in the HRMS" in w.message
    assert "structure" in dashboard.CHECK_LABELS
    project.set_structure(c, "CCc1ccccc1")   # drawn properly: the warning goes
    assert keys(project, c, "structure") == []


def test_generic_atom_warning_only_with_a_structure_field(project, section):
    """review-2: with the Structure field taken off the page, no box could show the
    warning, so it is not given (it would count with nothing to dismiss)."""
    c = project.add_compound(section.id)
    _write_generic_structure(project, c, "*CC(*)c1ccccc1")
    assert keys(project, c, "structure") == ["structure:generic"]
    c.data["fields"] = [e for e in c.data["fields"] if e["key"] != "structure"]
    project.save_compound(c)
    assert "structure" not in c.field_keys
    assert keys(project, c, "structure") == []


def test_generic_atom_warning_comes_from_the_same_read_as_the_structure(project, section,
                                                                        monkeypatch):
    """review-3/6: the generic atoms come from the project's own cached read of
    structure.mol (the one read_structure uses), not a second read of the file."""
    from si_assistant.core import fileio, project as project_module
    c = project.add_compound(section.id)
    _write_generic_structure(project, c, "*CCF")
    assert keys(project, c, "structure") == ["structure:generic"]
    reads = []
    real = project_module.read_text
    monkeypatch.setattr(project_module, "read_text", lambda p, *a, **k: reads.append(p)
                        or real(p, *a, **k))
    monkeypatch.setattr(fileio, "read_text", lambda p, *a, **k: reads.append(p)
                        or real(p, *a, **k))
    assert keys(project, c, "structure") == ["structure:generic"]
    assert project.structure_path(c) not in reads   # (unchanged file: nothing read again)


def test_generic_atom_structure_with_a_typed_hrms_formula_does_not_crash(project, section):
    """profile-core-5, end to end: a typed M formula next to a structure with an R doesn't
    stop the checks (the project reads such a structure as none, so there is nothing to
    compare the formula with). The guard around working out the structure's own formula
    is tested in test_typed_formula_with_a_structure_whose_formula_fails."""
    c = project.add_compound(section.id)
    _write_generic_structure(project, c, "*CCF")
    c.values["hrms"] = {"formula": "C3H7F", "adduct": "[M+H]+", "found": "63.0605"}
    project.save_compound(c)
    found = checks.all_warnings(project, c, IdReport())
    assert "hrms:formula-differs" not in [w.key for w in found]
    assert "structure:generic" in [w.key for w in found]


def test_typed_formula_with_a_structure_whose_formula_fails(project, make, monkeypatch):
    """review-4: a typed M formula next to a structure whose own formula can't be worked out
    gives no "differs" warning and no crash (the guarded comparison is reached)."""
    c = make("OCc1ccc(F)cc1", hrms={"formula": "C7H6FO", "adduct": "[M+H]+", "found": ""})
    assert "hrms:formula-differs" in keys(project, c, "hrms")   # (the comparison runs)
    calls = []

    def fails(mol):
        calls.append(mol)
        raise chem.ChemError("This structure has no formula.")
    monkeypatch.setattr(checks, "_species_of", fails)
    found = [w.key for w in checks.all_warnings(project, c, IdReport())]
    assert calls   # (the structure's own formula was asked for)
    assert "hrms:formula-differs" not in found and "hrms:formula-unreadable" not in found


# ---------------------------------------------------------------------- review (round 2)

def test_generic_atom_warning_gives_the_structure_boxs_own_words(project, section):
    """review-1: the warning and the Structure box's note give the same advice."""
    pytest.importorskip("PySide6")
    from si_assistant.ui.structure_widgets import generic_note
    c = project.add_compound(section.id)
    _write_generic_structure(project, c, "*CC(*)c1ccccc1")
    (w,) = active(project, c, "structure")
    assert w.key == checks.GENERIC_STRUCTURE_KEY
    assert w.message == generic_note(project.generic_structure_atoms(c))


def test_compact_dashboard_shows_structure_warnings_on_the_id(project, section):
    """review-2: the compact view hides the structure column, so its warnings (a generic
    atom) are listed with the ID's; they are still counted once."""
    c = project.add_compound(section.id)
    _write_generic_structure(project, c, "*CC(*)c1ccccc1")
    (row,) = dashboard.build_rows(project, section)
    (msg,) = row.cells["structure"].warnings
    assert row.id_warnings == [] and row.compact_id_warnings == [msg]
    assert row.warning_count == 1 and dashboard.filter_rows([row], "warnings") == [row]


def test_dismissed_missing_spectrum_comes_back_for_a_new_report(project, make):
    """review-3: a dismissed "no ¹H spectrum" stays dismissed while the ¹H report is edited
    or another nucleus is filled in, and comes back when the ¹H report is deleted and a new
    one written."""
    c = make("CCO", nmr=[{"id": "h", "nucleus": "1H", "label": "", "text": "δ 1.2 (t, 3H)"}],
             spectra=[])
    (s,) = active(project, c, "spectra")
    project.dismiss_warning(c, s)
    c.values["nmr"][0]["text"] = "δ 1.22 (t, 3H)"   # a typo fixed
    c.values["nmr"].append({"id": "c", "nucleus": "13C", "label": "", "text": "δ 58.0"})
    c.values["spectra"] = [{"type": "13C", "file": "13c.pdf"}]
    project.save_compound(c)
    assert active(project, c, "spectra") == []
    c.values["nmr"][0] = {"id": "h2", "nucleus": "1H", "label": "", "text": "δ 1.2 (t, 3H)"}
    project.save_compound(c)
    assert keys(project, c, "spectra") == ["spectra:missing:1H"]


def test_dismissed_extra_spectrum_comes_back_for_another_file(project, make):
    """review-3: a dismissed "¹⁹F spectrum but no ¹⁹F report" comes back when another ¹⁹F
    file is attached, not when a spectrum of another nucleus is."""
    c = make("CCO", nmr=[], spectra=[{"type": "19F", "file": "f.pdf"}])
    (s,) = active(project, c, "spectra")
    assert s.key == "spectra:extra:19F"
    project.dismiss_warning(c, s)
    c.values["spectra"].append({"type": "31P", "file": "p.pdf"})
    project.save_compound(c)
    assert keys(project, c, "spectra") == ["spectra:extra:31P"]
    c.values["spectra"].append({"type": "19F", "file": "f2.pdf"})
    project.save_compound(c)
    assert keys(project, c, "spectra") == ["spectra:extra:19F", "spectra:extra:31P"]


# ---------------------------------------------------------------------- late2 (compound)

@pytest.mark.parametrize("nmr", [5, "δ 1.2", {"nucleus": "1H"}, [5, None, "x"]])
def test_hand_edited_nmr_of_the_wrong_form_does_not_stop_the_checks(project, make, nmr):
    """late2-1: a hand-edited "nmr" that is not a list of reports is read as no reports,
    so the compound page and the dashboard still open."""
    c = make("CCO")
    c.values["nmr"] = nmr
    project.save_compound(c)
    found = {w.key for w in checks.active_warnings(project, c, IdReport())}
    assert not any(k.startswith(("c13:", "h1:", "frequency:")) for k in found)
    dashboard.build_rows(project, project.sections[0])


def test_wrong_form_reports_keep_the_positions_of_the_others(project, make):
    """late2-1: a stray non-report entry does not shift the position-based IDs of the
    reports after it."""
    c = make("CCO", nmr=[5, {"nucleus": "13C", "label": "", "text": "δ 58.0"}])
    assert "c13:1" in keys(project, c, "c13")


@pytest.mark.parametrize("value", [92, "92", [92], None])
def test_hand_edited_yield_or_scale_of_the_wrong_form_does_not_stop_the_checks(project, make,
                                                                               value):
    """late2-1: "yield": 92 (or a scale that is not a dict) is read as empty: no crash and
    no yield warning."""
    c = make("CCOC(=O)c1ccoc1C", scale=value, **{"yield": value})
    assert active(project, c, "yield") == []
    c.values["yield"] = {"percent": "82", "mass": "28.4", "mass_unit": "mg"}
    c.values["scale"] = value
    project.save_compound(c)
    assert active(project, c, "yield") == []
    dashboard.build_rows(project, project.sections[0])


def test_yield_message_shows_no_none_for_null_parts(project, make):
    """late2-1: null units in a hand-edited compound.json fall back to mg / mmol in the
    message (and the sum), never "None"."""
    c = make("CCOC(=O)c1ccoc1C", scale={"value": 0.2, "unit": None},
             **{"yield": {"percent": 82, "mass": "28.4", "mass_unit": None}})
    (w,) = active(project, c, "yield")
    assert "None" not in w.message
    assert "28.4 mg" in w.message and "from 0.2 mmol is 92.1%" in w.message


def test_dashboard_summary_of_hand_edited_values(project, make):
    """late2-1: the dashboard reads null parts as empty (not "None") and values of the
    wrong form as empty."""
    make("CCO", nmr=[5, {"nucleus": "1H", "label": "", "text": "δ 1.2"}], spectra="x",
             **{"yield": {"percent": 82, "mass": "28.4", "mass_unit": None}})
    (row,) = dashboard.build_rows(project, project.sections[0])
    assert "None" not in row.cells["yield"].summary and "28.4" in row.cells["yield"].summary
    assert row.cells["nmr"].summary == "¹H"


# ---------------------------------------------------------------------- late3 (checks)

@pytest.mark.parametrize("report", [
    {"id": [1], "nucleus": "13C", "label": "", "text": "δ 58.0"},
    {"id": {"a": 1}, "nucleus": ["1H"], "label": ["x"], "text": "δ 1.2"},
    {"id": "a", "nucleus": {"n": "1H"}, "label": 5, "text": "δ 1.2"}])
def test_nmr_report_with_a_list_id_or_nucleus_does_not_stop_the_checks(project, make, report):
    """review-1/4: an id, nucleus or label that is a list, dict or number (a hand edit)
    is read as text: the compound page and the dashboard still open."""
    c = make("CCO", nmr=[report, {"id": [2], "nucleus": "13C", "label": "", "text": "δ 18.0"}],
             spectra=[{"type": ["1H"], "file": "a.pdf"}])
    checks.active_warnings(project, c, IdReport())
    dashboard.build_rows(project, project.sections[0])
    dashboard.nmr_nuclei(project.sections[0])
    # Two reports with a list as their id are still told apart (by their position).
    if report["id"] == [1]:
        assert set(keys(project, c, "c13")) == {"c13:0", "c13:1"}


@pytest.mark.parametrize("text", [None, 5, ["δ 1.2"]])
def test_nmr_report_with_null_text_counts_as_empty(project, make, text):
    """review-2/4: a hand-edited null text is an empty report, not the word "None": no
    "report but no spectrum" warning, and the dashboard lists it as empty."""
    c = make("CCO", nmr=[{"id": "a", "nucleus": "13C", "label": "", "text": text}])
    if text is None or isinstance(text, list):
        assert keys(project, c, "spectra") == []
        (row,) = dashboard.build_rows(project, project.sections[0])
        assert row.cells["nmr"].summary == "(empty: ¹³C)"
    else:   # (a number is read as written, like any other part)
        dashboard.build_rows(project, project.sections[0])


def test_numeric_hrms_found_and_adduct_after_reopening(project, section, make):
    """review-3: "found": 47.0491 (a number) and "adduct": 5 typed into compound.json
    are read as text: the checks and the dashboard still work after reopening."""
    c = make("CCO", hrms={"found": 47.0491})
    project.close()
    reopened = Project.open(project.folder)
    try:
        (c,) = reopened.sections[0].compounds
        assert c.values["hrms"] == {"found": 47.0491}
        result, problem = checks.compound_hrms(reopened, c)
        assert result.found == 47.0491 and problem == ""
        checks.active_warnings(reopened, c, IdReport())
        (row,) = dashboard.build_rows(reopened, reopened.sections[0])
        assert row.cells["hrms"].summary.startswith("47.0491 (Δ")
        c.values["hrms"] = {"found": "47.0491", "adduct": 5, "formula": None}
        checks.active_warnings(reopened, c, IdReport())
        dashboard.build_rows(reopened, reopened.sections[0])
    finally:
        reopened.close()
    assert checks.parse_number(47.0491) == 47.0491 and checks.parse_number(None) is None
