"""Phase 6 error checks (SPEC §8.2) and dismissing warnings."""

import pytest

from si_assistant.core import chem, checks, nmr_parse
from si_assistant.core.compound_ids import IdReport
from si_assistant.core.project import Project


@pytest.fixture
def make(project):
    section = project.add_section("data", "Products")
    project.set_id_table(section.id, ["3a", "3b", "3c"])

    def make_compound(smiles, **values):
        c = project.add_compound(section.id)
        project.set_structure(c, smiles)
        for k, v in values.items():
            c.values[k] = v
        project.save_compound(c)
        return c
    return make_compound


def warnings(project, c, check=None):
    ws = checks.active_warnings(project, c, IdReport())
    return [w for w in ws if check is None or w.check == check]


def nmr(*reports):
    return [{"id": f"r{i}", "nucleus": n, "label": "", "text": t} for i, (n, t) in enumerate(reports)]


# ---------------------------------------------------------------------- parsing

def test_parse_peaks():
    text = ("1H NMR (400 MHz, CDCl3) δ 7.36 – 7.29 (m, 5H), 6.52 (d, J = 7.9 Hz, 1H), "
            "2.08 (s, 3H)")
    peaks = nmr_parse.peaks(text)
    assert [(p.shift, p.end, p.multiplicity, p.integration) for p in peaks] == [
        (7.36, 7.29, "m", 5.0), (6.52, None, "d", 1.0), (2.08, None, "s", 3.0)]
    assert nmr_parse.frequency(text) == 400 and nmr_parse.solvent(text) == "CDCl3"
    assert nmr_parse.proton_count(text) == 9
    c13 = "13C NMR (101 MHz, CDCl3) δ 171.2, 138.6, 128.9 (2C), 162.3 (d, J = 247.1 Hz), 20.8"
    assert nmr_parse.carbon_signal_count(c13) == 5
    assert nmr_parse.solvent("in DMSO-d6") == "DMSO-d6"


def test_unique_carbons_and_hydrogens():
    assert chem.unique_carbons(chem.parse_structure("Cc1ccccc1")) == 5          # toluene
    assert chem.unique_carbons(chem.parse_structure("CC(=O)N(C)Cc1ccccc1")) == 8
    assert chem.hydrogen_counts(chem.parse_structure("OCC(N)=O")) == (5, 3)


# ---------------------------------------------------------------------- the checks

def test_c13_count(project, make):
    c = make("Cc1ccccc1", nmr=nmr(("13C", "13C NMR (101 MHz, CDCl3) δ 137.9, 129.1, 128.3, 125.3, 21.5")))
    assert warnings(project, c, "c13") == []
    c.values["nmr"][0]["text"] = "δ 137.9, 129.1, 128.3, 21.5"
    project.save_compound(c)
    (w,) = warnings(project, c, "c13")
    assert "lists 4 signals" in w.message and "5 symmetry-distinct" in w.message


def test_h1_integration_and_exchangeable_note(project, make):
    c = make("OCc1ccccc1", nmr=nmr(("1H", "δ 7.35 (m, 5H), 4.68 (s, 2H)")))  # benzyl alcohol, 8 H
    (w,) = warnings(project, c, "h1")
    assert "add up to 7 H" in w.message and "8 H" in w.message and "NH/OH" in w.message
    c.values["nmr"][0]["text"] = "δ 7.35 (m, 5H), 4.68 (s, 2H), 1.80 (br s, 1H)"
    project.save_compound(c)
    assert warnings(project, c, "h1") == []


def test_impurity_peaks(project, make):
    c = make("CCO", nmr=nmr(("1H", "1H NMR (400 MHz, CDCl3) δ 3.70 (q, 2H), 2.17 (s, 1H), 1.22 (t, 3H)"),
                            ("13C", "13C NMR (101 MHz, CDCl3) δ 77.16, 58.3, 18.4")))
    msgs = [w.message for w in warnings(project, c, "impurity")]
    assert any("2.17" in m and "acetone" in m for m in msgs)
    assert any("77.16" in m and "solvent" in m for m in msgs)
    assert not any("1.22" in m for m in msgs)  # not a singlet


def test_impurity_table_is_editable_per_project(project, make):
    from si_assistant.core import impurities
    c = make("CCO", nmr=nmr(("1H", "1H NMR (400 MHz, CDCl3) δ 3.70 (q, 2H), 2.17 (s, 1H), "
                                   "1.11 (s, 3H)"),))
    table = impurities.default_table()
    for row in table:                       # switch off acetone in CDCl3
        if row["solvent"] == "CDCl3" and row["what"] == "acetone":
            row["on"] = False
    table.append({"solvent": "cdcl3", "nucleus": "1H", "shift": 1.11, "what": "my grease",
                  "on": True})              # typed solvent names are normalised
    project.settings["impurity_table"] = table
    msgs = [w.message for w in warnings(project, c, "impurity")]
    assert not any("2.17" in m for m in msgs)
    assert any("1.11" in m and "my grease" in m for m in msgs)
    project.settings["impurity_tolerance_1H"] = 0.1   # wider: 2.17 (s) now near MeCN's 2.10
    assert any("acetonitrile" in w.message for w in warnings(project, c, "impurity"))


def test_impurity_editor_round_trip(qapp):
    from si_assistant.core import impurities
    from si_assistant.ui.dialogs import ImpurityTableEditor
    settings = {}
    ed = ImpurityTableEditor(settings)
    ed.apply(settings)
    assert "impurity_table" not in settings          # unchanged → keeps using the built-in table
    ed.table.add_row([True, "chloroform-d", "¹H", "−0.05", "TMS-ish"])
    ed.table.add_row([True, "CDCl3", "1H", "not a number", "ignored"])
    ed.filter.setCurrentIndex(ed.filter.findData("D2O"))
    hidden = sum(ed.table.table.isRowHidden(r) for r in range(ed.table.table.rowCount()))
    assert hidden > 100
    ed.apply(settings)
    rows = settings["impurity_table"]
    assert len(rows) == len(impurities.default_table()) + 1
    assert rows[-1] == {"solvent": "CDCl3", "nucleus": "1H", "shift": -0.05, "what": "TMS-ish",
                        "on": True}


def test_yield_consistency(project, make):
    # Ethyl 2-methylfuran-3-carboxylate, MW 154.16: 28.4 mg from 0.20 mmol = 92.1%
    ok = make("CCOC(=O)c1ccoc1C", scale={"value": "0.20", "unit": "mmol"},
              **{"yield": {"percent": "92", "mass": "28.4", "mass_unit": "mg"}})
    assert warnings(project, ok, "yield") == []
    bad = make("CCOC(=O)c1ccoc1C", scale={"value": "0.20", "unit": "mmol"},
               **{"yield": {"percent": "82", "mass": "28.4", "mass_unit": "mg"}})
    (w,) = warnings(project, bad, "yield")
    assert "92.1%" in w.message and "not 82%" in w.message


def test_spectra_present(project, make):
    c = make("CCO", nmr=nmr(("1H", "δ 3.7 (q, 2H)"), ("13C", "δ 58.3, 18.4")),
             spectra=[{"file": "a.pdf", "type": "1H", "title": ""},
                      {"file": "b.pdf", "type": "19F", "title": ""}])
    msgs = sorted(w.message for w in warnings(project, c, "spectra"))
    assert msgs == ["A ¹⁹F spectrum is attached but there is no ¹⁹F NMR report.",
                    "There is a ¹³C NMR report but no ¹³C spectrum attached."]


def test_frequency_consistency(project, make):
    c = make("CCO", nmr=nmr(("1H", "1H NMR (400 MHz, CDCl3) δ 3.7 (q, 2H)"),
                            ("13C", "13C NMR (101 MHz, CDCl3) δ 58.3")))
    assert warnings(project, c, "frequency") == []
    c.values["nmr"][1]["text"] = "13C NMR (126 MHz, CDCl3) δ 58.3"
    project.save_compound(c)
    (w,) = warnings(project, c, "frequency")
    assert "126 MHz" in w.message and "≈ 101 MHz" in w.message


def test_missing_heteroatom_report(project, make):
    c = make("FC(F)(F)c1ccccc1")  # adds an empty 19F report automatically
    (w,) = warnings(project, c, "heteroatom")
    assert "contains F" in w.message
    c.values["nmr"][2]["text"] = "δ −62.8"
    project.save_compound(c)
    assert warnings(project, c, "heteroatom") == []


def test_hrms_manual_formula_differs(project, make):
    c = make("CCO", hrms={"ionization": "ESI", "adduct": "[M+H]+", "found": "",
                          "species": "", "formula": "C3H8O"})
    assert any("differs from the structure" in w.message for w in warnings(project, c, "hrms"))


# ---------------------------------------------------------------------- dismissing

def test_dismiss_comes_back_when_data_changes(project, make):
    c = make("Cc1ccccc1", nmr=nmr(("13C", "δ 137.9, 129.1, 21.5")))
    (w,) = warnings(project, c, "c13")
    project.dismiss_warning(c, w, "C-F coupling")
    assert warnings(project, c, "c13") == []
    reopened = Project.open(project.folder).compound(c.id)
    assert reopened.data["dismissed"][w.key]["reason"] == "C-F coupling"
    assert checks.dismissed_warnings(project, c, IdReport())[0][1]["by"] == "Tester"
    # Editing the report changes the fingerprint: the warning is back.
    c.values["nmr"][0]["text"] = "δ 137.9, 129.1, 128.3, 21.5"
    project.save_compound(c)
    assert len(warnings(project, c, "c13")) == 1
    project.restore_warning(c, w.key)
    assert "dismissed" not in c.data or w.key not in c.data["dismissed"]
