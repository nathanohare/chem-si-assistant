"""HRMS report files (SPEC §13n.3): reading m/z values from every supported format,
centroiding, picking the found m/z, filling the Found box, storage/Trash/undo and printing.
All files are made up (tests/ms_samples.py)."""

import json

import numpy as np
import pytest
from pypdf import PdfReader

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.core import checks, chem, front
from si_assistant.core import ms_reports as R
from si_assistant.core.project import Project
from tests import ms_samples as M

TOL = 5.0


@pytest.fixture(scope="module")
def peaks():
    return M.ion_peaks()


def match(data, peaks, tol=TOL):
    return R.choose_match([data], peaks, tol)


def ppm(a, b):
    return (a - b) / b * 1e6


# ---------------------------------------------------------------------- formats

def test_mzml_centroid_zlib_64bit(peaks):
    sp = np.array(M.sample_peaks(1.2))
    d = R.read_bytes(M.mzml([(sp[:, 0], sp[:, 1])]), "3a.mzML")
    assert d.readable and d.kind == "mzML" and d.scans == 1 and not d.profile
    m = match(d, peaks)
    assert m.peak.monoisotopic and abs(m.ppm - 1.2) < 0.01


def test_mzml_indexed_uncompressed_32bit_profile(peaks):
    x, y = M.profile(M.sample_peaks(0.8))
    d = R.read_bytes(M.mzml([(x, y)], compress=False, precision=32, profile_data=True,
                            indexed=True), "3a.mzML")
    assert d.profile and d.count == 6   # one centroid per peak
    assert abs(match(d, peaks).ppm - 0.8) < 0.2   # (32-bit floats: ~0.1 ppm)


def test_mzml_without_ms1_and_numpress_say_why():
    sp = np.array(M.sample_peaks())
    with pytest.raises(R.ReportReadError, match="MS1"):
        R.read_mzml(M.mzml([(sp[:, 0], sp[:, 1])], ms_level=2))
    with pytest.raises(R.ReportReadError, match="Numpress"):
        R.read_mzml(M.mzml([(sp[:, 0], sp[:, 1])], numpress=True))


def test_mzml_scans_are_merged(peaks):
    sp = np.array(M.sample_peaks(1.0))
    scans = [(sp[:, 0] * (1 + d * 1e-6), sp[:, 1]) for d in (-0.5, 0.0, 0.5)]
    d = R.read_mzml(M.mzml(scans))
    assert d.scans == 3 and d.count == len(sp)   # the same peaks of three scans: one each
    assert abs(match(d, peaks).ppm - 1.0) < 0.05


@pytest.mark.parametrize("kw", [dict(), dict(precision=64, compress=True)])
def test_mzxml_centroid_and_ms2_skipped(peaks, kw):
    sp = np.array(M.sample_peaks(1.5))
    d = R.read_bytes(M.mzxml([(sp[:, 0], sp[:, 1])], ms2=True, **kw), "3a.mzXML")
    assert d.kind == "mzXML" and d.scans == 1
    assert not np.any(np.isclose(d.mz, 99.0001))   # (the MS/MS scan inside is left out)
    assert abs(match(d, peaks).ppm - 1.5) < 0.2


def test_mzxml_profile(peaks):
    x, y = M.profile(M.sample_peaks(-1.0))
    d = R.read_mzxml(M.mzxml([(x, y)], precision=64, compress=True, centroided=False))
    assert d.profile and abs(match(d, peaks).ppm + 1.0) < 0.05


def test_mzdata(peaks):
    sp = np.array(M.sample_peaks(2.0))
    d = R.read_bytes(M.mzdata([(sp[:, 0], sp[:, 1])]), "3a.mzData")
    assert d.kind == "mzData" and abs(match(d, peaks).ppm - 2.0) < 0.01


def test_mgf_pepmass_counts(peaks):
    mono = peaks[0].mz
    d = R.read_bytes(M.mgf([], pepmass=mono * (1 + 0.7e-6)).encode(), "3a.mgf")
    assert abs(match(d, peaks).ppm - 0.7) < 0.05
    d = R.read_bytes(M.mgf(M.sample_peaks(1.1)).encode(), "3a.mgf")
    assert abs(match(d, peaks).ppm - 1.1) < 0.05


def test_jcamp_peak_table(peaks):
    d = R.read_bytes(M.jcamp_peak_table(M.sample_peaks(-2.2)).encode(), "3a.jdx")
    assert d.kind == "JCAMP-DX" and abs(match(d, peaks).ppm + 2.2) < 0.05


@pytest.mark.parametrize("asdf", [False, True])
def test_jcamp_xydata_affn_and_asdf(peaks, asdf):
    mono = peaks[0].mz
    grid = np.arange(mono - 0.05, mono + 0.05, 0.0005)
    true = mono * (1 + 1.5e-6)
    y = 50000 * np.exp(-0.5 * ((grid - true) / (mono / 40000 / 2.3548)) ** 2)
    text = M.jcamp_xydata(grid, y, asdf=asdf)
    if asdf:   # really compressed: letters, not numbers
        data_lines = text.split("##XYDATA=(X++(Y..Y))\n")[1].splitlines()[:-1]
        assert any(c in "JKLMNOPQRjklmnopqr" for c in "".join(data_lines))
    d = R.read_jcamp(text)
    assert d.profile and d.count == 1
    assert abs(match(d, peaks).ppm - 1.5) < 0.05


def test_asdf_decoding():
    # SQZ then DIF, and DUP repeating the difference
    values, ends_dif = R.decode_asdf_line("5A0J0T")   # 10, +10 then DUP 2 → 10, 20, 30
    assert values == [5.0, 10.0, 20.0, 30.0] and ends_dif
    values, ends_dif = R.decode_asdf_line("5 10 20 30")
    assert values == [5.0, 10.0, 20.0, 30.0] and not ends_dif
    values, _ = R.decode_asdf_line("1a2b3")   # SQZ negatives: -12, -23
    assert values == [1.0, -12.0, -23.0]


def test_asdf_writer_and_reader_agree():
    ys = [0, 5, 10, 15, 20, 20, 20, -3, 7, 1000, 999, 0, 0, 0, 0, 0, 12]
    lines = M.asdf_lines(ys, 100.0, 0.5, per_line=6)
    out, check = [], False
    for line in lines:
        values, ends = R.decode_asdf_line(line)
        y = values[1:]
        out += y[1:] if check else y
        check = ends
    assert out == ys


@pytest.mark.parametrize("scale", [None, 1e-5])
def test_netcdf_andi(peaks, scale):
    sp = np.array(M.sample_peaks(0.6))
    d = R.read_bytes(M.netcdf([(sp[:, 0], sp[:, 1]), (sp[:, 0], sp[:, 1] * 0.5)],
                              scale=scale), "3a.cdf")
    assert d.kind.startswith("netCDF") and d.scans == 2
    assert abs(match(d, peaks).ppm - 0.6) < (0.05 if scale is None else 0.1)


def test_netcdf_profile(peaks):
    x, y = M.profile(M.sample_peaks(1.3))
    d = R.read_netcdf(M.netcdf([(x, y)], centroided=False))
    assert d.profile and abs(match(d, peaks).ppm - 1.3) < 0.05


def test_netcdf4_is_refused_plainly():
    with pytest.raises(R.ReportReadError, match="netCDF"):
        R.read_netcdf(b"\x89HDF\r\n\x1a\n" + b"\0" * 100)


@pytest.mark.parametrize("delimiter, header, comma", [(",", True, False), (";", True, True),
                                                      ("\t", False, False),
                                                      ("\t", True, True)])
def test_csv_and_tsv_peak_lists(peaks, delimiter, header, comma):
    text = M.csv_text(M.sample_peaks(-1.4), delimiter, header, comma)
    d = R.read_bytes(text.encode(), "3a.csv")
    assert d.count == 6 and abs(match(d, peaks).ppm + 1.4) < 0.05
    if header:
        assert np.isfinite(d.intensity).all()   # intensities read from their column


def test_space_separated_peak_list(peaks):
    text = "\n".join(f"{mz:.5f}   {i:.0f}" for mz, i in M.sample_peaks(0.4))
    d = R.read_bytes(text.encode(), "peaks.txt")
    assert d.count == 6 and abs(match(d, peaks).ppm - 0.4) < 0.05


def test_profile_peak_list_is_centroided(peaks):
    x, y = M.profile(M.sample_peaks(0.9))
    text = "m/z,Intensity\n" + "\n".join(f"{a:.6f},{b:.2f}" for a, b in zip(x, y))
    d = R.read_bytes(text.encode(), "profile.csv")
    assert d.profile and d.count == 6 and abs(match(d, peaks).ppm - 0.9) < 0.05


def test_table_with_measured_and_calculated_columns(peaks):
    p = peaks[0]
    text = ("Formula,Calc m/z,Meas m/z,Diff (ppm)\n"
            f"C12H16NO3,{p.mz:.4f},{p.mz * (1 + 2.5e-6):.4f},2.5\n")
    d = R.read_bytes(text.encode(), "formula.csv")
    assert d.calculated == (round(p.mz, 4),)
    m = match(d, peaks)
    assert m.measured and abs(m.ppm - 2.5) < 0.3   # never the calculated value


@pytest.mark.parametrize("shared", [True, False])
def test_xlsx(peaks, shared):
    rows = [["Made-up peak list"], ["m/z", "Intensity"]] + [[a, b] for a, b in
                                                              M.sample_peaks(1.7)]
    d = R.read_bytes(M.xlsx(rows, shared=shared), "3a.xlsx")
    assert d.kind == "Excel file" and d.count == 6 and abs(match(d, peaks).ppm - 1.7) < 0.05


def test_xlsx_numbers_as_written():
    sheets = R.xlsx_rows(M.xlsx([["m/z"], [188.1073], [12345]]))
    assert sheets[0] == [["m/z"], ["188.1073"], ["12345"]]


# ---------------------------------------------------------------------- PDF reports

def test_agilent_like_pdf(tmp_path, peaks):
    d = R.read_report(M.agilent_like_pdf(tmp_path / "a.pdf", 1.2))
    assert d.kind == "PDF report"
    assert round(peaks[0].mz, 4) in d.calculated
    m = match(d, peaks)
    assert m.measured and m.peak.monoisotopic and abs(m.ppm - 1.2) < 0.3


def test_shimadzu_like_pdf_calculated_first(tmp_path, peaks):
    d = R.read_report(M.shimadzu_like_pdf(tmp_path / "s.pdf", -0.8))
    m = match(d, peaks)
    assert m.measured and abs(m.ppm + 0.8) < 0.3


def test_bruker_like_pdf_plain_mz_is_calculated(tmp_path, peaks):
    d = R.read_report(M.bruker_like_pdf(tmp_path / "b.pdf", 2.1))
    assert d.calculated == (round(peaks[0].mz, 4),)
    m = match(d, peaks)
    assert m.measured and abs(m.ppm - 2.1) < 0.3


def test_running_text_calcd_and_found(tmp_path, peaks):
    mono = peaks[0].mz
    d = R.read_report(M.text_report_pdf(tmp_path / "t.pdf", mono * (1 + 0.9e-6), mono))
    assert d.calculated == (round(mono, 4),)
    m = match(d, peaks)
    assert m.measured and abs(m.ppm - 0.9) < 0.3


def test_text_keywords():
    d = R.parse_report_text("HRMS (ESI) m/z: [M+H]+ calcd for C12H16NO3 222.1125; "
                            "found 222.1129.\nTheophylline 181.0720 (not a keyword)\n"
                            "Date 2026.10.07 12:30:45.123, 98.765% purity")
    assert d.calculated == (222.1125,)
    assert d.is_measured(222.1129) and 181.072 in d.mz and not d.is_measured(181.072)
    assert not np.any(np.isclose(d.mz, 45.123)) and not np.any(np.isclose(d.mz, 98.765))


def test_header_columns():
    assert R.header_columns("m/z  Abund  Formula  Calc m/z  Diff (ppm)") == ["meas", "calc"]
    assert R.header_columns("Formula  Calc. m/z  Meas. m/z  Diff (ppm)") == ["calc", "meas"]
    assert R.header_columns("Meas. m/z  #  Ion Formula  m/z  err [ppm]") == ["meas", "calc"]
    assert R.header_columns("m/z  Intensity  Relative  Theo. Mass  Delta (ppm)") == \
        ["meas", "calc"]
    assert R.header_columns("Mass  Calc. Mass  mDa  PPM  DBE  i-FIT  Formula") == \
        ["meas", "calc"]
    assert R.header_columns("High Resolution Mass Spectrum") is None
    assert R.header_columns("Mass error 1.2 ppm at 222.1127") is None


def test_scanned_pdf_and_pictures_say_why(tmp_path):
    d = R.read_report(M.scanned_pdf(tmp_path / "scan.pdf"))
    assert not d.readable and "scanned" in d.message
    pic = tmp_path / "report.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n")
    d = R.read_report(pic)
    assert not d.readable and "picture" in d.message


# ---------------------------------------------------------------------- vendor raw data

@pytest.mark.parametrize("name, folder, inside, kind", [
    ("3a.raw", False, None, "Thermo"),
    ("3a.raw", True, "_FUNC001.DAT", "Waters"),
    ("3a.d", True, "AcqData/MSScan.bin", "Agilent"),
    ("3a.d", True, "analysis.baf", "Bruker"),
    ("3a.wiff", False, None, "SCIEX"),
    ("3a.wiff.scan", False, None, "SCIEX"),
    ("3a.lcd", False, None, "Shimadzu"),
    ("MSScan.bin", False, None, "raw data"),
])
def test_vendor_raw_data_is_kept_but_not_read(tmp_path, name, folder, inside, kind):
    path = tmp_path / name
    if folder:
        (path / inside).parent.mkdir(parents=True, exist_ok=True)
        (path / inside).write_bytes(b"\0\1\2")
    else:
        path.write_bytes(b"\0\1\2made-up")
    d = R.read_report(path)
    assert not d.readable and kind in d.kind
    assert "MSConvert" in d.message and "mzML" in d.message and "PDF or CSV" in d.message
    assert R.print_kind(path) == "none"


def test_damaged_files_never_raise(tmp_path):
    for name, data in (("x.mzML", b"<mzML><spectrum"), ("x.cdf", b"CDF\x01junk"),
                       ("x.xlsx", b"PK\x03\x04junk"), ("x.pdf", b"%PDF-1.4 junk")):
        path = tmp_path / name
        path.write_bytes(data)
        d = R.read_report(path)
        assert not d.readable and d.message


# ---------------------------------------------------------------------- centroiding

@pytest.mark.parametrize("offset", [0.0, 0.25, 0.5, 0.77])
@pytest.mark.parametrize("points", [4, 10])
def test_centroid_finds_the_peak_however_it_is_sampled(offset, points):
    true = 500.12345
    x, y = M.profile([(true, 1000.0), (true + 1.00335, 300.0)], points_per_fwhm=points,
                     offset=offset)
    cx, cy = R.centroid(x, y)
    assert len(cx) == 2
    assert abs(ppm(cx[0], true)) < 0.05 and cy[0] > cy[1]


def test_profile_detection():
    x, y = M.profile(M.sample_peaks())
    assert R.looks_profile(x, y)
    sp = np.array(M.sample_peaks() * 10)
    assert not R.looks_profile(np.sort(sp[:, 0] + np.arange(len(sp))), sp[:, 1])


def test_merge_peaks():
    a = (np.array([100.0, 200.0]), np.array([1.0, 3.0]))
    b = (np.array([100.0001, 300.0]), np.array([3.0, 1.0]))
    mz, inten = R.merge_peaks([a, b])
    assert len(mz) == 3 and abs(mz[0] - 100.000075) < 1e-6 and inten[0] == 4.0


# ---------------------------------------------------------------------- the choice

def data_of(values, measured=()):
    return R.ReportData("r.pdf", "test", np.array(values, float), np.full(len(values), np.nan),
                        measured=frozenset(round(v, 6) for v in measured))


def test_nearest_within_tolerance(peaks):
    mono = peaks[0].mz
    d = data_of([mono * (1 + 4e-6), mono * (1 + 1e-6), mono * (1 - 3e-6)])
    assert abs(match(d, peaks).ppm - 1.0) < 0.01
    assert match(data_of([mono * (1 + 6e-6)]), peaks) is None          # outside ±5 ppm
    assert match(data_of([mono * (1 + 6e-6)]), peaks, tol=10) is not None


def test_isotope_peak_used_only_without_monoisotopic(peaks):
    m1 = peaks[1]
    d = data_of([m1.mz * (1 + 0.1e-6)])   # only the ¹³C peak is in the report
    m = match(d, peaks)
    assert m is not None and not m.peak.monoisotopic
    d = data_of([m1.mz * (1 + 0.1e-6), peaks[0].mz * (1 + 3e-6)])   # both: mono wins
    assert match(d, peaks).peak.monoisotopic


def test_measured_column_beats_other_values(peaks):
    mono = peaks[0].mz
    near, marked = mono * (1 + 0.2e-6), mono * (1 + 2.0e-6)
    m = match(data_of([near, marked], measured=[marked]), peaks)
    assert m.measured and abs(m.ppm - 2.0) < 0.01


def test_several_reports(peaks):
    mono = peaks[0].mz
    a = data_of([mono * (1 + 3e-6)])
    a.name = "a.csv"
    b = data_of([mono * (1 + 1e-6)])
    b.name = "b.pdf"
    m = R.choose_match([a, b, R.ReportData("c.raw", "raw", readable=False)], peaks, TOL)
    assert m.file == "b.pdf"


def test_salt_and_adducts(tmp_path):
    """The chosen ion decides: [M+Na]+ and a salt's cation."""
    p = Project.create(tmp_path, "S")
    p.acquire_lock("T")
    data = p.add_section("data", "D")
    c = p.add_compound(data.id)
    p.set_structure(c, M.SMILES)
    info = chem.mol_info(chem.parse_structure(M.SMILES))
    na = checks.hrms_calc(info, "[M+Na]+", "", 5).peaks[0].mz
    report = tmp_path / "na.csv"
    report.write_text(M.csv_text([(na * (1 + 1e-6), 100.0)]), encoding="utf-8")
    c.values["hrms"]["adduct"] = "[M+Na]+"
    p.add_hrms_report(c, report)
    assert c.values["hrms"]["found"] == R.format_mz(na * (1 + 1e-6))
    p.close()


# ---------------------------------------------------------------------- filling the Found box

@pytest.fixture
def setup(tmp_path):
    p = Project.create(tmp_path, "H")
    p.acquire_lock("Tester")
    data = p.add_section("data", "Products")
    p.ensure_spectra_section()
    p.set_id_table(data.id, ["3a"])
    c = p.add_compound(data.id, name="made-up ester")
    p.set_structure(c, M.SMILES)
    yield p, c, tmp_path
    p.close()


def write_csv(folder, name, offset_ppm):
    path = folder / name
    path.write_text(M.csv_text(M.sample_peaks(offset_ppm)), encoding="utf-8")
    return path


def test_found_is_filled_from_the_report_with_provenance(setup, peaks):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp, "3a peaks.csv", 1.2))
    value = c.values["hrms"]
    expected = R.format_mz(peaks[0].mz * (1 + 1.2e-6))
    assert value["found"] == expected
    assert value["found_from"]["file"] == "3a peaks.csv"
    stored = json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))
    assert stored["values"]["hrms"]["found_from"]["file"] == "3a peaks.csv"
    assert stored["values"]["hrms"]["reports"] == [{"file": "3a peaks.csv", "kind": "file"}]
    assert (c.folder / "hrms" / "3a peaks.csv").is_file()
    assert R.found_is_from_report(value)


def test_typed_value_is_never_overwritten_and_warns(setup, peaks):
    p, c, tmp = setup
    c.values["hrms"]["found"] = "222.1200"           # typed by hand, ~34 ppm off
    p.add_hrms_report(c, write_csv(tmp, "r.csv", 1.2))
    assert c.values["hrms"]["found"] == "222.1200" and "found_from" not in c.values["hrms"]
    warnings = checks.all_warnings(p, c, p.id_report())
    w = next(w for w in warnings if w.key == "hrms:report-differs")
    assert "r.csv" in w.message and "222.1200" in w.message
    p.dismiss_warning(c, w, "checked")
    assert not any(x.key == "hrms:report-differs"
                   for x in checks.active_warnings(p, c, p.id_report()))
    # Within the tolerance: no warning.
    c.values["hrms"]["found"] = R.format_mz(peaks[0].mz * (1 + 2e-6))
    assert not any(x.key == "hrms:report-differs"
                   for x in checks.all_warnings(p, c, p.id_report()))


def test_report_value_follows_changes(setup, peaks):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp, "r.csv", 1.2))
    value = c.values["hrms"]
    # Another adduct with no peak in the report: the value filled from it goes.
    value["adduct"] = "[M+K]+"
    assert R.update_found(p, c) == "cleared" and value["found"] == ""
    value["adduct"] = "[M+H]+"
    assert R.update_found(p, c) == "filled"
    # A tighter tolerance than the error: cleared again; the user's own value stays.
    p.settings["hrms_tolerance_ppm"] = 1.0
    assert p.refresh_hrms_found() == 1 and value["found"] == ""
    p.settings["hrms_tolerance_ppm"] = 5.0
    p.refresh_hrms_found()
    assert value["found"]
    value["found"] = "222.1130"                       # the user types over it
    assert R.update_found(p, c) == "" and "found_from" not in value
    assert value["found"] == "222.1130"


def test_an_emptied_box_is_filled_again(setup):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp, "r.csv", 1.2))
    c.values["hrms"]["found"] = ""
    assert R.update_found(p, c) == "filled"


def test_removing_the_report_takes_its_value_and_trash_restores(setup, peaks):
    p, c, tmp = setup
    p.add_hrms_report(c, write_csv(tmp, "a.csv", 1.2))
    p.add_hrms_report(c, write_csv(tmp, "b.csv", 0.3))
    assert c.values["hrms"]["found_from"]["file"] == "b.csv"   # (nearer)
    p.remove_hrms_report(c, "b.csv")
    assert not (c.folder / "hrms" / "b.csv").exists()
    assert c.values["hrms"]["found_from"]["file"] == "a.csv"   # the other report now
    p.remove_hrms_report(c, "a.csv")
    assert c.values["hrms"]["found"] == "" and "reports" not in c.values["hrms"]
    entries = [e for e in p.list_trash() if e.info.get("field_key") == R.TRASH_FIELD]
    assert len(entries) == 2
    p.restore_from_trash(next(e.id for e in entries if e.name == "b.csv"))
    assert [i["file"] for i in R.report_items(c)] == ["b.csv"]
    assert c.values["hrms"]["found_from"]["file"] == "b.csv"


def test_folders_and_any_file_type_are_kept(setup):
    p, c, tmp = setup
    raw = tmp / "run 3a.d"
    (raw / "AcqData").mkdir(parents=True)
    (raw / "AcqData" / "MSScan.bin").write_bytes(b"\0" * 10)
    odd = tmp / "notes.xyz"
    odd.write_bytes(b"\x01\x02\x00binary")
    p.add_hrms_report(c, raw)
    p.add_hrms_report(c, odd)
    items = R.report_items(c)
    assert items == [{"file": "run 3a.d", "kind": "folder"}, {"file": "notes.xyz",
                                                             "kind": "file"}]
    assert (c.folder / "hrms" / "run 3a.d" / "AcqData" / "MSScan.bin").is_file()
    p.remove_hrms_report(c, "run 3a.d")
    entry = next(e for e in p.list_trash() if e.name == "run 3a.d")
    p.restore_from_trash(entry.id)
    assert (c.folder / "hrms" / "run 3a.d" / "AcqData" / "MSScan.bin").is_file()


def test_undo_and_redo(setup):
    p, c, tmp = setup
    p.enable_undo()
    with p.undo_step("Add r.csv"):
        p.add_hrms_report(c, write_csv(tmp, "r.csv", 1.2))
    assert c.values["hrms"]["found"]
    p.undo()
    c = p.compound(c.id)
    assert not R.report_items(c) and not (c.folder / "hrms" / "r.csv").exists()
    assert not c.values["hrms"].get("found")
    p.redo()
    c = p.compound(c.id)
    assert R.report_items(c) and (c.folder / "hrms" / "r.csv").exists()
    with p.undo_step("Remove r.csv"):
        p.remove_hrms_report(c, "r.csv")
    p.undo()
    c = p.compound(c.id)
    assert (c.folder / "hrms" / "r.csv").exists() and R.report_items(c)


# ---------------------------------------------------------------------- printing

def source(p, **kw):
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    result = build(p, options, image_info, convert_to_png)
    return result.typ_path.read_text(encoding="utf-8"), result.warnings


@pytest.fixture
def printing(setup, qapp):
    p, c, tmp = setup
    p.add_hrms_report(c, M.agilent_like_pdf(tmp / "3a_HRMS.pdf"))
    p.add_hrms_report(c, write_csv(tmp, "3a peaks.csv", 1.2))
    sp = np.array(M.sample_peaks(1.2))
    mz = tmp / "3a.mzML"
    mz.write_bytes(M.mzml([(sp[:, 0], sp[:, 1])]))
    p.add_hrms_report(c, mz)
    raw = tmp / "3a.raw"
    raw.write_bytes(b"\0made-up raw")
    p.add_hrms_report(c, raw)
    return p, c, tmp


def test_not_printed_by_default(printing):
    p, c, _tmp = printing
    assert not R.prints_reports(p, c)
    src, _w = source(p)
    assert "HRMS reports" not in src and "3a_HRMS.pdf" not in src


def test_printed_when_on(printing):
    p, c, _tmp = printing
    p.set_spectra_defaults({**p.spectra_defaults(), "hrms_reports": True})
    assert R.prints_reports(p, c)
    src, warnings = source(p)
    assert "#heading(level: 2" in src and '[#"HRMS reports"]' in src
    assert src.count('3a_HRMS.pdf", page: 1') == 1           # the PDF page by page
    assert "raw(block: true" in src and "Intensity" in src    # the CSV as a listing
    assert "table.header([_m/z_]" in src and "found m/z" in src   # the mzML's peaks
    assert any("3a.raw" in w and "can't be printed" in w for w in warnings)
    # After the spectra: the sub-section comes after the Spectra section's heading.
    assert src.index('[#"Spectra"]') < src.index('[#"HRMS reports"]')


def test_compound_choice_overrides_the_section(printing):
    p, c, _tmp = printing
    p.set_hrms_print(c, True)
    assert R.prints_reports(p, c) and '[#"HRMS reports"]' in source(p)[0]
    p.set_spectra_defaults({**p.spectra_defaults(), "hrms_reports": True})
    p.set_hrms_print(c, False)
    assert not R.prints_reports(p, c) and '[#"HRMS reports"]' not in source(p)[0]
    p.set_hrms_print(c, None)
    assert R.prints_reports(p, c)


def test_exclusions_are_respected(printing):
    p, c, _tmp = printing
    p.set_hrms_print(c, True)
    assert '[#"HRMS reports"]' in source(p)[0]
    assert '[#"HRMS reports"]' not in source(p, excluded=frozenset({front.HRMS_KEY}))[0]
    assert '[#"HRMS reports"]' not in source(p, excluded=frozenset({c.section_id}))[0]
    assert '[#"HRMS reports"]' not in source(
        p, excluded=frozenset({p.spectra_section.id}))[0]


def test_previews(printing):
    p, c, _tmp = printing
    p.set_hrms_print(c, True)
    src, _w = source(p, only=("hrms", "hrms_reports"))
    assert '[#"HRMS reports"]' in src
    src, _w = source(p, only=("spectra", c.id))   # the compound's spectra page
    assert "3a_HRMS.pdf" in src


def test_listing_is_cut_with_a_note(setup):
    p, c, tmp = setup
    path = tmp / "long.txt"
    path.write_text("\n".join(f"line {i} " + "x" * 200 for i in range(400)), encoding="utf-8")
    lines, left = R.listing_lines(path)
    assert len(lines) == R.LISTING_LINES and left == 400 - R.LISTING_LINES
    assert all(len(line) <= R.LISTING_WIDTH for line in lines)
    p.add_hrms_report(c, path)
    p.set_hrms_print(c, True)
    src, _w = source(p)
    assert "250 more lines not printed" in src


def test_printed_pdf(printing):
    p, c, _tmp = printing
    p.set_hrms_print(c, True)
    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info,
                         convert_to_png)
    text = "\n".join(page.extract_text() for page in PdfReader(str(result.pdf)).pages)
    assert "HRMS reports" in text and "HRMS report: 3a_HRMS.pdf" in text
    assert "HRMS report: 3a peaks.csv" in text and "found m/z" in text
