import pytest

from si_assistant.core import checks
from si_assistant.core import dashboard as D
from si_assistant.core.project import Project


@pytest.fixture
def section(project):
    s = project.add_section("data", "Products")
    project.set_id_table(s.id, ["3a", "3b", "3c"])
    return s


def row_for(rows, compound):
    return next(r for r in rows if r.compound.id == compound.id)


def test_cell_states(project, section):
    c = project.add_compound(section.id, name="A")
    c.values["yield"] = {"percent": "92", "mass": "28.4", "mass_unit": "mg"}
    project.save_compound(c)
    project.remove_field(c, "files")
    rows = D.build_rows(project, section)
    cells = rows[0].cells
    assert cells["yield"].state == D.FILLED and cells["yield"].summary == "92% · 28.4 mg"
    assert cells["procedure"].state == D.MISSING          # required, empty
    assert cells["rf"].state == D.EMPTY                   # optional, empty
    assert cells["files"].state == D.NOT_USED             # removed from this compound
    assert cells["structure"].state == D.MISSING
    assert cells["procedure"].icons == "✖"


def test_flags_and_warnings_in_cells(project, section):
    c = project.add_compound(section.id)
    project.set_flag(c, "nmr", True, "re-run 13C")
    project.set_structure(c, "CCOC(=O)c1ccoc1C")
    c.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": "155.0803"}  # way off
    project.save_compound(c)
    cell_nmr = D.build_rows(project, section)[0].cells["nmr"]
    assert cell_nmr.icons.endswith("⚑") and "re-run 13C" in cell_nmr.tooltip
    hrms = D.build_rows(project, section)[0].cells["hrms"]
    assert hrms.state == D.FILLED and hrms.icons == "✔ ⚠"
    assert "outside" in hrms.warnings[0]
    assert hrms.summary == "155.0803 (Δ +64.7 ppm)"  # (155.0803 − 155.0703) / 155.0703


def test_hrms_matches_isotope_peak_only_when_it_fits():
    from si_assistant.core import chem, checks
    info = chem.mol_info(chem.parse_structure("CCOC(=O)c1ccc(Br)cc1"))   # C9H9BrO2
    r = checks.hrms_calc(info, "[M+H]+", "230.9841", 5.0)               # 81Br peak 230.9838
    assert r.matched.formula == "C9H10[81Br]O2" and abs(r.ppm) < 5
    r = checks.hrms_calc(info, "[M+H]+", "244.9906", 5.0)               # fits nothing
    assert r.matched.monoisotopic and r.out_of_tolerance


def test_id_warnings_and_summary(project, section):
    a = project.add_compound(section.id)
    b = project.add_compound(section.id)
    b.data["id_override"] = "3a"
    project.save_compound(b)
    rows = D.build_rows(project, section)
    assert all(r.id_warnings for r in rows)
    assert D.summary_text(rows) == "0 / 2 compounds complete · 0 flags · 2 warnings"


def test_filters(project, section):
    a = project.add_compound(section.id)
    b = project.add_compound(section.id)
    project.set_flag(b, "yield", True)
    project.set_default_fields(section.id, [{"key": "rf", "required": False}])
    rows = D.build_rows(project, section)
    assert [r.compound.id for r in D.filter_rows(rows, "flagged")] == [b.id]
    assert D.filter_rows(rows, "incomplete") == []  # no required fields any more
    assert len(D.filter_rows(rows, "all")) == 2


def test_columns_default_custom_and_saved(project, section):
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "mp"})
    project.add_field(c, {"key": "custom-1", "custom": {"label": "Lot", "type": "short_text"}})
    cols = D.available_columns(section)
    assert cols[0] == "procedure" and "structure" not in cols and "notes" not in cols
    assert cols[-2:] == ["mp", "custom-1"]
    assert D.field_label(section, "custom-1") == "Lot"
    assert D.shown_columns(section) == cols
    project.set_dashboard(section.id, columns=["yield", "nmr", "gone"], filter_mode="flagged")
    reopened = Project.open(project.folder).section(section.id)
    assert D.shown_columns(reopened) == ["yield", "nmr"]
    assert reopened.data["dashboard"]["filter"] == "flagged"


def test_split_by_nucleus(project, section):
    a = project.add_compound(section.id, name="A")
    b = project.add_compound(section.id, name="B")
    project.set_structure(b, "Fc1ccccc1")                      # adds an empty 19F report
    a.values["nmr"][0]["text"] = "δ 7.25"                        # 1H filled, 13C empty
    a.values["spectra"] = [{"file": "a_1H.pdf", "type": "1H"}, {"file": "h.pdf", "type": "2D NMR"}]
    project.save_compound(a)
    project.set_dashboard(section.id, split_nuclei=True)
    cols = D.shown_columns(section)
    assert "nmr" not in cols and "spectra" not in cols
    nmr_cols = [k for k in cols if k.startswith("nmr:")]
    spec_cols = [k for k in cols if k.startswith("spectra:")]
    assert nmr_cols == ["nmr:1H", "nmr:13C", "nmr:19F"]
    assert spec_cols == ["spectra:1H", "spectra:13C", "spectra:19F", "spectra:2D NMR"]
    assert D.field_label(section, "nmr:19F") == "¹⁹F NMR"
    assert D.field_label(section, "spectra:13C") == "¹³C spectrum"

    rows = {r.compound.id: r.cells for r in D.build_rows(project, section)}
    assert rows[a.id]["nmr:1H"].state == D.FILLED
    assert rows[a.id]["nmr:13C"].state == D.MISSING        # NMR is required, report empty
    assert rows[a.id]["nmr:19F"].state == D.NOT_USED       # no F in compound A
    assert rows[b.id]["nmr:19F"].state == D.MISSING
    assert rows[a.id]["spectra:1H"].state == D.FILLED
    assert rows[a.id]["spectra:13C"].state == D.MISSING    # has a 13C report, no spectrum
    assert rows[a.id]["spectra:19F"].state == D.NOT_USED
    assert rows[a.id]["spectra:2D NMR"].state == D.FILLED
    assert rows[b.id]["spectra:2D NMR"].state == D.NOT_USED

    # Per-nucleus cells must not count their field's warnings again.
    row_b = next(r for r in D.build_rows(project, section) if r.compound.id == b.id)
    expected = checks.compound_warnings(project, b, project.id_report())
    assert row_b.warning_count == sum(len(v) for v in expected.values())
    # Hiding the NMR group hides all its nucleus columns.
    project.set_dashboard(section.id, columns=[k for k in D.available_columns(section) if k != "nmr"])
    assert not any(k.startswith("nmr:") for k in D.shown_columns(section))
    project.set_dashboard(section.id, split_nuclei=False)
    assert "spectra" in D.shown_columns(section)


def test_summaries(project, section):
    c = project.add_compound(section.id)
    project.write_compound_text(c, "procedure", "Prepared according to **General Procedure A** "
                                                "from 1a (0.20 mmol) and lots more text here.")
    c.values["nmr"][0]["text"] = "δ 7.25"
    c.values["scale"] = {"value": "0.20", "unit": "mmol"}
    c.values["rf"] = {"value": "0.35", "eluent": "30% EtOAc/hexanes", "visualization": ""}
    project.save_compound(c)
    cells = D.build_rows(project, section)[0].cells
    assert cells["procedure"].summary == "Prepared according to General Procedure…"
    assert cells["nmr"].summary == "¹H (empty: ¹³C)"
    assert cells["scale"].summary == "0.20 mmol"
    assert cells["rf"].summary == "0.35 · 30% EtOAc/hexanes"
