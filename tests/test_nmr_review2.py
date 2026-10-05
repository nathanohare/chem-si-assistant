"""NMR items of the second review list: impurity tolerances (SPEC §13l.9), solvent
spellings (§13l.14) and the large-J check (§13l.4)."""

import json

import pytest

from si_assistant.core import checks, impurities, nmr_parse, solvents
from si_assistant.core import spectra as S
from si_assistant.core.compound_ids import IdReport
from si_assistant.core.markup import NORMAL, SUB, SUPER, Run
from si_assistant.core.project import PROJECT_FILE, Project


def nmr(*reports):
    return [{"id": f"r{i}", "nucleus": n, "label": "", "text": t}
            for i, (n, t) in enumerate(reports)]


@pytest.fixture
def make(project):
    section = project.add_section("data", "Products")
    project.set_id_table(section.id, ["3a", "3b", "3c", "3d"])

    def make_compound(smiles, **values):
        c = project.add_compound(section.id)
        project.set_structure(c, smiles)
        c.values.update(values)
        project.save_compound(c)
        return c
    return make_compound


def warnings(project, c, check):
    return [w for w in checks.active_warnings(project, c, IdReport()) if w.check == check]


# ---------------------------------------------------------------------- tolerances (§13l.9)

def test_new_default_tolerances():
    assert impurities.tolerances({}) == (0.010, 0.050)
    assert (impurities.PROTON_TOLERANCE, impurities.CARBON_TOLERANCE) == (0.010, 0.050)


def test_old_defaults_count_as_not_changed_but_changed_values_are_kept():
    old = {"impurity_tolerance_1H": 0.005, "impurity_tolerance_13C": 0.03}
    assert impurities.tolerances(old) == (0.010, 0.050)
    changed = {"impurity_tolerance_1H": 0.02, "impurity_tolerance_13C": 0.03}
    assert impurities.tolerances(changed) == (0.02, 0.050)
    # Under the new keys every value counts as chosen, even one equal to an old default.
    assert impurities.tolerances({"impurity_tolerance_1H_ppm": 0.005}) == (0.005, 0.050)


def test_set_tolerances_stores_only_changes():
    settings = {"impurity_tolerance_1H": 0.005, "impurity_tolerance_13C": 0.1}
    impurities.set_tolerances(settings, 0.010, 0.050)
    assert settings == {}
    impurities.set_tolerances(settings, 0.005, 0.2)
    assert settings == {"impurity_tolerance_1H_ppm": 0.005, "impurity_tolerance_13C_ppm": 0.2}


def test_projects_at_the_old_defaults_switch_on_opening(tmp_path):
    for (h, c), expected in (((0.005, 0.03), (0.010, 0.050)),
                             ((0.02, 0.03), (0.02, 0.050)),
                             ((0.005, 0.1), (0.010, 0.1))):
        p = Project.create(tmp_path / f"{h}-{c}", "T")
        data = json.loads((p.folder / PROJECT_FILE).read_text(encoding="utf-8"))
        data.update({"impurity_tolerance_1H": h, "impurity_tolerance_13C": c})
        (p.folder / PROJECT_FILE).write_text(json.dumps(data), encoding="utf-8")
        p = Project.open(p.folder)
        assert impurities.tolerances(p.settings) == expected
        assert "impurity_tolerance_1H" not in p.settings
        assert "impurity_tolerance_13C" not in p.settings
        # Saved with the next settings change, and still the same when opened again.
        p.acquire_lock("Tester")
        p.save_settings()
        p.close()
        again = Project.open(p.folder)
        assert impurities.tolerances(again.settings) == expected
        # A value set to an old default by hand after the change is kept.
        impurities.set_tolerances(again.settings, 0.005, 0.03)
        again.acquire_lock("Tester")
        again.save_settings()
        again.close()
        assert impurities.tolerances(Project.open(p.folder).settings) == (0.005, 0.03)


def test_settings_editor_uses_the_new_defaults(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from si_assistant.ui.dialogs import ImpurityTableEditor
    settings = {"impurity_tolerance_1H": 0.005, "impurity_tolerance_13C": 0.03}
    ed = ImpurityTableEditor(settings)
    assert (ed.tol_h.value(), ed.tol_c.value()) == (0.010, 0.050)
    ed.apply(settings)
    assert not any(k.startswith("impurity_tolerance") for k in settings)   # follows defaults
    ed.tol_h.setValue(0.02)
    ed.apply(settings)
    assert settings["impurity_tolerance_1H_ppm"] == 0.02
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    ed._reset()
    assert (ed.tol_h.value(), ed.tol_c.value()) == (0.010, 0.050)


def test_wider_default_tolerance_catches_a_near_match(project, make):
    # Acetone in CDCl3 is at 2.17; a singlet at 2.16 is now within 0.010 ppm.
    c = make("CCO", nmr=nmr(("1H", "1H NMR (400 MHz, CDCl3) δ 3.70 (q, 2H), 2.16 (s, 1H), "
                                   "1.22 (t, 3H)"),
                            ("13C", "13C NMR (101 MHz, CDCl3) δ 77.20, 58.3, 18.4")))
    msgs = [w.message for w in warnings(project, c, "impurity")]
    assert any("2.16" in m and "acetone" in m for m in msgs)
    assert any("77.2" in m and "solvent" in m for m in msgs)       # 0.04 from 77.16


# ---------------------------------------------------------------------- solvents (§13l.14)

@pytest.mark.parametrize("written, name", [
    ("MeOD", "CD3OD"), ("CD3OD", "CD3OD"), ("methanol-d4", "CD3OD"), ("MeOD-d4", "CD3OD"),
    ("d4-MeOH", "CD3OD"), ("CD₃OD", "CD3OD"), ("meod", "CD3OD"),
    ("DMSO-d6", "DMSO-d6"), ("(CD3)2SO", "DMSO-d6"), ("DMSO‑d₆", "DMSO-d6"),
    ("acetone-d6", "acetone-d6"), ("(CD3)2CO", "acetone-d6"),
    ("benzene-d6", "C6D6"), ("C6D6", "C6D6"), ("CDCl3", "CDCl3"), ("chloroform-d", "CDCl3"),
    ("CDCl₃", "CDCl3"), ("CD2Cl2", "CD2Cl2"), ("CD3CN", "CD3CN"), ("MeCN-d3", "CD3CN"),
    ("D2O", "D2O"), ("THF-d8", "THF-d8"), ("toluene-d8", "toluene-d8"),
    ("pyridine-d5", "pyridine-d5"), ("CD3 OD", "CD3OD"), ("Methanol-D4", "CD3OD"),
])
def test_solvent_spellings(written, name):
    found = solvents.find(f"1H NMR (500 MHz, {written}) δ 7.26 (s, 1H)")
    assert found is not None and found.name == name
    assert nmr_parse.solvent(f"1H NMR (500 MHz, {written})") == name
    assert impurities.normalise_solvent(written) == name


def test_solvent_as_written_and_word_edges():
    found = solvents.find("1H NMR (500 MHz, MeOD) δ 4.87 (s)")
    assert (found.name, found.written) == ("CD3OD", "MeOD")
    assert solvents.find("(400 MHz, CDCl₃)").written == "CDCl3"      # plain digits
    assert solvents.find("(400 MHz, DMSO‑d₆)").written == "DMSO-d6"  # plain hyphen
    assert solvents.find("(400 MHz, CDCl3/CD3OD 4:1)").name == "CDCl3"   # first one named
    assert solvents.find("homeodomain, CD2OD") is None                  # not inside words
    assert solvents.find("δ 7.26 (s, 1H)") is None
    assert impurities.normalise_solvent("my solvent") == "my solvent"


def test_caption_prints_the_solvent_as_written():
    from si_assistant.compile.text import format_nmr

    class C:
        values = {"nmr": [{"nucleus": "1H", "text": "1H NMR (500 MHz, MeOD) δ 7.26 (s, 1H)"},
                          {"nucleus": "13C", "text": "13C NMR (126 MHz, CD3OD) δ 170.1"}]}
    caption = S.caption_text(C, {"type": "1H"})
    assert caption == "1H NMR (MeOD, 500 MHz)"
    runs = format_nmr([Run(caption)])
    assert "".join(r.text for r in runs) == "1H NMR (MeOD, 500 MHz)"
    assert runs[0].text == "1" and runs[0].valign == SUPER        # ¹H NMR (MeOD, 500 MHz)
    assert S.header_parts(C, {"type": "1H"}, "3x", 1) == ["3x — 1H NMR", " (500 MHz, MeOD)"]
    # Written as a formula: subscripted automatically when printed.
    c13 = format_nmr([Run(S.caption_text(C, {"type": "13C"}))])
    assert [(r.text, r.valign) for r in c13 if r.valign == SUB] == [("3", SUB)]
    assert S.find_solvent("(500 MHz, MeOD)") == "CD3OD"      # its usual name


def test_reversed_deuterated_names_are_formatted():
    from si_assistant.compile.text import format_chemistry
    runs = format_chemistry([Run("in d4-MeOH and d6-DMSO, not d2-labelled")])
    assert [(r.text, r.valign, r.italic) for r in runs[:4]] == [
        ("in ", NORMAL, False), ("d", NORMAL, True), ("4", SUB, False), ("-", NORMAL, False)]
    assert "".join(r.text for r in runs) == "in d4-MeOH and d6-DMSO, not d2-labelled"
    assert sum(1 for r in runs if r.valign == SUB) == 2           # not "d2-labelled"


def test_impurity_check_understands_meod(project, make):
    c = make("CCO", nmr=nmr(("1H", "1H NMR (500 MHz, MeOD) δ 3.61 (q, 2H), 3.31 (s, 1H), "
                                   "1.18 (t, 3H)")))
    msgs = [w.message for w in warnings(project, c, "impurity")]
    assert any("3.31" in m and "CHD₂OD" in m and "CD₃OD" in m for m in msgs)


# ---------------------------------------------------------------------- large J (§13l.4)

@pytest.mark.parametrize("text, values", [
    ("6.52 (d, J = 7.9 Hz, 1H)", [7.9]),
    ("(dd, J = 8.3, 2.0 Hz, 1H)", [8.3, 2.0]),
    ("(dd, J = 17.2, 10.8 Hz, 1H)", [17.2, 10.8]),
    ("(d, 2J = 14.0 Hz, 1H)", [14.0]),
    ("(dd, J1 = 8.0 Hz, J2 = 2.0 Hz, 1H)", [8.0, 2.0]),
    ("(dt, JH–F = 47.6 Hz, J = 6.1 Hz, 2H)", [47.6, 6.1]),
    ("(d, J(H,F) = 47.6 Hz, 2H)", [47.6]),
    ("(d, J = 8,3 Hz, 1H)", [8.3]),
    ("(dd, J = 17,2, 10,8 Hz, 1H)", [17.2, 10.8]),
    ("(t, J = 7.2, 24H)", [7.2]),                    # "24H" is the integration
    ("(dd, J = 47.6 Hz and 21.0 Hz, 1H)", [47.6, 21.0]),
    ("see J. Org. Chem. 2010, 75, 1", []),
])
def test_coupling_constants_are_read(text, values):
    assert [c.value for c in nmr_parse.couplings(text)] == values


def test_large_j_warning(project, make):
    c = make("CCCO", nmr=nmr(
        ("1H", "1H NMR (400 MHz, CDCl3) δ 5.80 (dd, J = 17.2, 10.8 Hz, 1H), "
               "3.60 (t, J = 26.5 Hz, 2H), 1.50 (d, J = 21,4 Hz, 2H)"),
        ("13C", "13C NMR (101 MHz, CDCl3) δ 162.3 (d, J = 247.1 Hz)")))
    (w,) = warnings(project, c, "coupling")
    assert w.field == "nmr" and w.key == "coupling:r0"
    assert "J = 26.5 Hz, 21,4 Hz are larger than 20 Hz" in w.message
    assert "H–F" not in w.message                       # no F or P in the structure
    # Dismissed: stays away while the large values are the same, comes back if they change.
    project.dismiss_warning(c, w, "checked")
    assert warnings(project, c, "coupling") == []
    c.values["nmr"][0]["text"] += ", 1.20 (t, J = 7.1 Hz, 3H)"
    project.save_compound(c)
    assert warnings(project, c, "coupling") == []
    c.values["nmr"][0]["text"] = c.values["nmr"][0]["text"].replace("26.5", "62.5")
    project.save_compound(c)
    assert len(warnings(project, c, "coupling")) == 1


def test_large_j_with_fluorine_or_phosphorus_may_be_correct(project, make):
    f = make("FCCO", nmr=nmr(("1H", "δ 4.55 (dt, JH–F = 47.6 Hz, J = 4.1 Hz, 2H)")))
    (w,) = warnings(project, f, "coupling")
    assert "47.6 Hz is larger than 20 Hz" in w.message
    assert "contains F" in w.message and "H–F couplings can be this large" in w.message
    p = make("CCP(=O)(OCC)OCC", nmr=nmr(("1H", "δ 1.70 (dq, J = 18.0, 7.6 Hz, 2H), "
                                               "1.10 (dt, J = 20.3, 7.6 Hz, 3H)")))
    (w,) = warnings(project, p, "coupling")
    assert "20.3 Hz" in w.message and "H–P" in w.message and "18.0" not in w.message
    assert warnings(project, make("CCO", nmr=nmr(("1H", "δ 1.2 (t, J = 20.0 Hz, 3H)"))),
                    "coupling") == []                 # exactly 20 Hz is fine
