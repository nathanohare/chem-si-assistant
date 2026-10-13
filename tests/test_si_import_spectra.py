"""Importing an SI, phase I3 (SPEC §13u.8): spectra. Word: MestReNova pictures matched to
compounds by their peaks, by the header picture next to them, or by the ChemDraw label
in their group; PDF: spectrum pages cut at their headers. Made-up spectra only
(tests/chemdraw_files.py builds the pictures); the PDF tests use the shipped example."""

from pathlib import Path

import pytest

from si_assistant import resources
from si_assistant.core import si_import as I
from si_assistant.core import si_import_apply as A
from si_assistant.core import si_import_read as R
from si_assistant.core import si_import_spectra as S
from si_assistant.core.project import Project
from tests import chemdraw_files as CF

C13_AXIS = [str(v) for v in range(0, 220, 20)]
H1_AXIS = [f"{v:.1f}" for v in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]


# ---------------------------------------------------------------------- reading pictures

def test_peak_labels_and_axis_are_read_from_a_spectrum_picture():
    data = CF.spectrum_emf(C13_AXIS, ["128.50", "77.16", "21.30"])
    assert S.emf_peaks(data) == [128.5, 77.16, 21.3]
    assert S.type_of_emf(data)[0] == "13C"
    assert S.type_of_emf(CF.spectrum_emf(H1_AXIS, ["7.26", "2.35"]))[0] == "1H"
    assert S.type_of_emf(CF.spectrum_emf(["-200", "-150", "-100", "-50", "0"], []))[0] == "19F"
    titled = CF.spectrum_emf(C13_AXIS, [], title="19F{1H} NMR (376 MHz, CDCl3)")
    assert S.type_of_emf(titled) == ("19F", "19F{1H} NMR (376 MHz, CDCl3)")
    assert S.is_spectrum_picture(data)
    assert not S.is_spectrum_picture(CF.header_emf("5a", "1H NMR (CDCl3, 400 MHz)"))


def test_types_named_in_headers():
    assert S.type_from_text("2a — 13C{1H} NMR (126 MHz, CDCl3)") == "13C"
    assert S.type_from_text("31P NMR of 5") == "31P"
    assert S.type_from_text("29Si NMR (99 MHz)") == "Other NMR"
    assert S.type_from_text("HSQC of 3b") == "2D NMR"
    assert S.type_from_text("HPLC trace of rac-4") == "HPLC/SFC"
    assert S.type_from_text("General remarks") == ""


# ---------------------------------------------------------------------- matching by peaks

def compound(cid: str, c13: str, h1: str = "") -> I.ImportedCompound:
    c = I.ImportedCompound(cid, cid)
    c.values["nmr"] = [{"nucleus": "13C", "text": f"13C NMR (101 MHz, CDCl3) δ {c13}."}]
    if h1:
        c.values["nmr"].append({"nucleus": "1H", "text": f"1H NMR (400 MHz, CDCl3) δ {h1}."})
    return c


def test_spectra_are_matched_to_the_report_their_peaks_fit():
    a = compound("4a", "170.1, 133.2, 128.5, 21.3", "7.40 – 7.31 (m, 5H), 2.35 (s, 3H)")
    b = compound("4b", "165.0, 140.2, 116.4, 55.6")
    peaks = [170.12, 133.21, 128.48, 77.16, 21.30]
    assert S.match_score(peaks, a.values["nmr"][0]["text"], "13C") > 0.9
    assert S.match_score(peaks, b.values["nmr"][0]["text"], "13C") < 0.3
    assert S.best_by_peaks(peaks, "13C", [a, b])[0] == "4a"
    # ¹H: a range counts when a peak lies in it
    assert S.best_by_peaks([7.38, 7.35, 7.33, 7.26, 2.35], "1H", [a, b])[0] == "4a"
    # two compounds fitting about as well: no match (the user chooses)
    twin = compound("4c", "170.1, 133.2, 128.5, 21.3")
    assert S.best_by_peaks(peaks, "13C", [a, twin]) == ("", 0.0)


# ---------------------------------------------------------------------- Word files

def spectra_docx(tmp_path) -> Path:
    """4a and 4b, then a spectra section: 4a's ¹³C spectrum (its peaks fit 4a's report);
    a header picture "4b 1H NMR" grouped with a spectrum with no peak labels; a group of
    a ChemDraw drawing labelled 4b with a ¹³C spectrum (no peaks), followed by a stray
    header naming 4a (the ID in the group wins); and an Mnova page that is a molecule
    only (no axis: not a spectrum)."""
    atoms, bonds = CF.phenol()
    drawing = CF.Cdx().fragment(atoms, bonds, (-15, -15, 30, 25)).label("4b", (0, 35, 10, 42)).bytes()
    objects = [
        ("mnova", CF.spectrum_emf(C13_AXIS, ["170.12", "133.21", "128.48", "77.16", "21.30"])),
        ("mnova", CF.header_emf("4b", "1H NMR (CDCl3, 400 MHz)")),
        ("mnova", CF.spectrum_emf(H1_AXIS, [])),
        drawing,
        ("mnova", CF.spectrum_emf(C13_AXIS, [])),
        ("mnova", CF.header_emf("4a", "")),
        ("mnova", CF.emf([(10, 10, 0, "OH"), (40, 30, 0, "N")])),
    ]
    bold = "<w:r><w:rPr><w:b/></w:rPr><w:t>{}</w:t></w:r>"
    text = "<w:r><w:t>{}</w:t></w:r>"
    path = tmp_path / "si.docx"
    path.write_bytes(CF.docx_with_objects([
        (bold.format("Toluate (4a)"), None),
        (text.format("Colorless oil (10 mg, 50%). 13C NMR (101 MHz, CDCl3) δ 170.1, 133.2, "
                     "128.5, 21.3."), None),
        (bold.format("Anisate (4b)"), None),
        (text.format("White solid (5 mg, 20%). 13C NMR (101 MHz, CDCl3) δ 165.0, 140.2, "
                     "116.4, 55.6."), None),
        (bold.format("NMR Spectra"), None),
        ("", 0),
        ("", [1, 2]),
        ("", [3, 4]),
        ("", 5),
        ("", 6),
    ], objects))
    return path


def test_spectra_from_mnova_objects_in_word(tmp_path):
    read = R.read_file(spectra_docx(tmp_path))
    assert sum(1 for p in read.paras for o in p.objects if o["kind"] == "mnova") == 6
    plan = I.analyse(read.paras)
    found = [(s.cid, s.type) for s in plan.spectra]
    assert found == [("4a", "13C"), ("4b", "1H"), ("4b", "13C")]   # (the molecule: no)
    hows = [s.how for s in plan.spectra]
    assert hows[0].startswith("its peaks fit the 13C NMR report of 4a")
    assert hows[1] == "by the header next to it (4b)"
    assert plan.spectra[1].title == "1H NMR (CDCl3, 400 MHz)"
    assert hows[2] == "by the ID under the structure next to it"


def test_a_drawing_label_does_not_override_the_peaks(tmp_path):
    # A spectrum grouped with a drawing labelled 4b whose peaks fit 4a: it is 4a's.
    atoms, bonds = CF.phenol()
    drawing = CF.Cdx().fragment(atoms, bonds, (-15, -15, 30, 25)).label("4b", (0, 35, 10, 42)).bytes()
    spectrum = ("mnova", CF.spectrum_emf(C13_AXIS, ["170.12", "133.21", "128.48", "21.30"]))
    path = tmp_path / "si.docx"
    bold = "<w:r><w:rPr><w:b/></w:rPr><w:t>{}</w:t></w:r>"
    text = "<w:r><w:t>{}</w:t></w:r>"
    path.write_bytes(CF.docx_with_objects([
        (bold.format("Toluate (4a)"), None),
        (text.format("13C NMR (101 MHz, CDCl3) δ 170.1, 133.2, 128.5, 21.3."), None),
        (bold.format("Anisate (4b)"), None),
        (text.format("13C NMR (101 MHz, CDCl3) δ 165.0, 140.2, 116.4, 55.6."), None),
        ("", [0, 1]),
    ], [drawing, spectrum]))
    plan = I.analyse(R.read_file(path).paras)
    assert [(s.cid, s.type) for s in plan.spectra] == [("4a", "13C")]


def test_the_review_window_can_move_a_spectrum(qapp, tmp_path):
    """Every match can be changed by hand (SPEC §2.7): a matched spectrum's compound and
    type, and the compound of one not matched; a compound's ID changed in the window takes
    its spectra along."""
    from PySide6.QtCore import Qt
    from si_assistant.ui import si_import_ui as U
    read = R.read_file(spectra_docx(tmp_path))
    plan = I.analyse(read.paras)
    first, stray = plan.spectra[0], S.ImportedSpectrum("emf", b"", "1H")
    plan.spectra.append(stray)
    d = U.ImportDialog(plan, read, None)
    compounds = d.tree.topLevelItem(0)
    page_4a = d.pages.widget(d._page_of[id(compounds.child(0))])
    page_4b = d.pages.widget(d._page_of[id(compounds.child(1))])
    row = page_4a.findChildren(U._SpectrumRow)[0]
    assert row.which.currentData() is plan.compounds[0]
    row.which.setCurrentIndex(row.which.findText("4b", flags=Qt.MatchFlag.MatchStartsWith))
    assert first.cid == "4b"
    row.kind.setCurrentIndex(row.kind.findData("1H"))
    assert first.type == "1H"
    page_4b.cid.setText("4c")                  # (4b renamed: its spectra follow)
    assert first.cid == "4c" and plan.spectra[1].cid == "4c"
    unmatched = d.tree.topLevelItem(d.tree.topLevelItemCount() - 1)
    assert "not matched (1)" in unmatched.text(0)
    other = d.pages.widget(d._page_of[id(unmatched)]).findChildren(U._SpectrumRow)[0]
    assert other.which.currentData() is None
    other.which.setCurrentIndex(1)
    assert stray.cid == "4a"
    d.close()


def test_word_spectra_are_stored(tmp_path):
    plan = I.analyse(R.read_file(spectra_docx(tmp_path)).paras)
    plan.spectra[2].keep = False
    p = Project.create(tmp_path / "p", "P")
    p.acquire_lock("Tester")
    converted = []

    def spectrum_for(emf: bytes):
        converted.append(emf)
        return (b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>", ".svg",
                emf, ".emf")
    result = A.apply_plan(p, plan, "si.docx", spectrum_for=spectrum_for)
    assert result.spectra == 2 and len(converted) == 2
    by = {p.id_report().display_id(i): p.compound(i) for i in result.created}
    items = by["4a"].values["spectra"]
    assert [(i["type"], Path(i["file"]).suffix) for i in items] == [("13C", ".svg")]
    assert items[0].get("original", "").endswith(".emf")          # (Mnova's copy kept)
    assert [i["type"] for i in by["4b"].values["spectra"]] == ["1H"]
    p.close()


# ---------------------------------------------------------------------- PDF files

@pytest.fixture(scope="module")
def example():
    read = R.read_file(resources.example_pdf())
    return read, I.analyse(read.paras)


def test_spectrum_pages_of_a_pdf(qapp, example):
    read, plan = example
    spectra = S.pdf_spectra(resources.example_pdf(), plan, read.spectrum_pages)
    assert len(spectra) == 15 and all(s.cid for s in spectra)
    assert [(s.cid, s.type) for s in spectra[:3]] == [("2a", "1H"), ("2a", "13C"), ("2b", "1H")]
    assert spectra[0].title == "2a — 1H NMR (500 MHz, CDCl3)"
    assert ("2c", "19F") in [(s.cid, s.type) for s in spectra]
    assert all(s.shows_structure for s in spectra)


def test_pdf_spectra_are_stored_without_a_second_structure(qapp, example, tmp_path):
    read, plan = example
    plan = I.analyse(read.paras)
    plan.spectra = S.pdf_spectra(resources.example_pdf(), plan, read.spectrum_pages)
    p = Project.create(tmp_path / "p", "P")
    p.acquire_lock("Tester")
    result = A.apply_plan(p, plan, "Example SI.pdf")
    assert result.spectra == 15
    c = p.compound(result.created[0])
    items = c.values["spectra"]
    assert [i["type"] for i in items] == ["1H", "13C"]
    assert all(Path(i["file"]).suffix == ".pdf" for i in items)
    assert all(i.get("layout", {}).get("struct_box") is False for i in items)
    p.close()


def test_a_text_page_mentioning_nmr_is_no_spectrum_page():
    from si_assistant.core.si_import_read import _is_spectrum_page

    class Line:
        def __init__(self, text):
            self.text = text

        def plain(self):
            return self.text
    long = "Spectra were recorded on a 400 MHz NMR spectrometer; chemical shifts are given " \
           "relative to the residual solvent signal of CDCl3 in all cases."
    assert not _is_spectrum_page([Line("General information"), Line(long)])
    assert _is_spectrum_page([Line("2a — 1H NMR (500 MHz, CDCl3)"), Line("f1 (ppm)")])
