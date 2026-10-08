"""Made-up HRMS report files for the tests (SPEC §13n.3).

Everything here is invented: the peaks, intensities, instrument names, sample names and
report layouts only imitate the look of common exports (Agilent MassHunter, Thermo
Xcalibur/FreeStyle, Waters MassLynx, Bruker DataAnalysis, Shimadzu LabSolutions); no real
report or spectrum was copied. Each writer makes one file format from a list of peaks
[(m/z, intensity)], or from profile points.
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np

# A made-up compound for the reports: methyl 4-morpholinobenzoate, C12H15NO3.
SMILES = "COC(=O)c1ccc(N2CCOCC2)cc1"


def ion_peaks():
    """The calculated [M+H]+ isotope peaks of SMILES (chem.Peak)."""
    from si_assistant.core import chem, checks
    info = chem.mol_info(chem.parse_structure(SMILES))
    return checks.hrms_calc(info, "[M+H]+", "", 5.0).peaks


def sample_peaks(offset_ppm: float = 1.2, noise: bool = True) -> list[tuple[float, float]]:
    """Centroid peaks of a made-up spectrum of the [M+H]+ ion: the isotope peaks measured
    `offset_ppm` high, plus a few unrelated peaks (a sodium adduct, solvent clusters)."""
    peaks = ion_peaks()
    out = [(p.mz * (1 + offset_ppm * 1e-6), p.abundance * 1000.0) for p in peaks]
    mono = peaks[0].mz
    if noise:
        out += [(mono + 21.98194, 20000.0), (149.02332, 5000.0), (279.15903, 3000.0),
                (mono - 14.0157 + 0.0003, 800.0)]
    return sorted(out)


def profile(peaks: list[tuple[float, float]], resolution: float = 40000.0,
            points_per_fwhm: float = 8.0, offset: float = 0.37
            ) -> tuple[np.ndarray, np.ndarray]:
    """Profile data around each peak: Gaussians of width m/z / resolution sampled every
    FWHM / points_per_fwhm (with the grid `offset` of a step off the peak, so no sample
    sits on the top), with zeros in between left out."""
    xs, ys = [], []
    for mz, height in peaks:
        fwhm = mz / resolution
        sigma = fwhm / 2.3548
        step = fwhm / points_per_fwhm
        grid = mz + (np.arange(-30, 31) + offset) * step
        xs.append(grid)
        ys.append(height * np.exp(-0.5 * ((grid - mz) / sigma) ** 2))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    order = np.argsort(x)
    return x[order], y[order]


def _b64(values, dtype: str, compress: bool) -> str:
    raw = np.asarray(values, dtype=dtype).tobytes()
    if compress:
        raw = zlib.compress(raw)
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------- mzML

def mzml(scans: list[tuple], compress: bool = True, precision: int = 64,
         profile_data: bool = False, indexed: bool = False, ms_level: int = 1,
         numpress: bool = False) -> bytes:
    """An mzML file with one spectrum per (mz array, intensity array)."""
    dtype = "<f8" if precision == 64 else "<f4"
    prec_acc = ("MS:1000523", "64-bit float") if precision == 64 else ("MS:1000521",
                                                                       "32-bit float")
    comp_acc = ("MS:1000574", "zlib compression") if compress else ("MS:1000576",
                                                                    "no compression")
    mode = ("MS:1000128", "profile spectrum") if profile_data else ("MS:1000127",
                                                                    "centroid spectrum")
    spectra = []
    for n, (mz, inten) in enumerate(scans):
        arrays = []
        for values, acc, name in ((mz, "MS:1000514", "m/z array"),
                                  (inten, "MS:1000515", "intensity array")):
            extra = ('<cvParam cvRef="MS" accession="MS:1002312" name="MS-Numpress linear '
                     'prediction compression"/>' if numpress else "")
            arrays.append(
                f'<binaryDataArray encodedLength="0">'
                f'<cvParam cvRef="MS" accession="{prec_acc[0]}" name="{prec_acc[1]}"/>'
                f'<cvParam cvRef="MS" accession="{comp_acc[0]}" name="{comp_acc[1]}"/>{extra}'
                f'<cvParam cvRef="MS" accession="{acc}" name="{name}"/>'
                f'<binary>{_b64(values, dtype, compress)}</binary></binaryDataArray>')
        spectra.append(
            f'<spectrum index="{n}" id="scan={n + 1}" defaultArrayLength="{len(mz)}">'
            f'<cvParam cvRef="MS" accession="MS:1000579" name="MS1 spectrum"/>'
            f'<cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="{ms_level}"/>'
            f'<cvParam cvRef="MS" accession="{mode[0]}" name="{mode[1]}"/>'
            f'<binaryDataArrayList count="2">{"".join(arrays)}</binaryDataArrayList>'
            f'</spectrum>')
    body = (f'<mzML xmlns="http://psi.hupo.org/ms/mzml" version="1.1.0">'
            f'<cvList count="1"><cv id="MS" fullName="PSI-MS"/></cvList>'
            f'<run id="made_up_run"><spectrumList count="{len(scans)}">{"".join(spectra)}'
            f'</spectrumList><chromatogramList count="1"><chromatogram index="0" id="TIC" '
            f'defaultArrayLength="2"><binaryDataArrayList count="2"><binaryDataArray>'
            f'<cvParam cvRef="MS" accession="MS:1000523" name="64-bit float"/>'
            f'<cvParam cvRef="MS" accession="MS:1000576" name="no compression"/>'
            f'<cvParam cvRef="MS" accession="MS:1000595" name="time array"/>'
            f'<binary>{_b64([0.1, 0.2], "<f8", False)}</binary></binaryDataArray>'
            f'<binaryDataArray><cvParam cvRef="MS" accession="MS:1000523" name="64-bit float"/>'
            f'<cvParam cvRef="MS" accession="MS:1000576" name="no compression"/>'
            f'<cvParam cvRef="MS" accession="MS:1000515" name="intensity array"/>'
            f'<binary>{_b64([5.0, 6.0], "<f8", False)}</binary></binaryDataArray>'
            f'</binaryDataArrayList></chromatogram></chromatogramList></run></mzML>')
    if indexed:
        body = (f'<indexedmzML xmlns="http://psi.hupo.org/ms/mzml">{body}'
                f'<indexList count="1"><index name="spectrum"><offset idRef="scan=1">0</offset>'
                f'</index></indexList><indexListOffset>0</indexListOffset></indexedmzML>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + body).encode("utf-8")


# ---------------------------------------------------------------------- mzXML

def mzxml(scans: list[tuple], precision: int = 32, compress: bool = False,
          centroided: bool = True, ms2: bool = False) -> bytes:
    dtype = ">f8" if precision == 64 else ">f4"
    out = []
    for n, (mz, inten) in enumerate(scans):
        pairs = np.empty(2 * len(mz))
        pairs[0::2], pairs[1::2] = mz, inten
        child = ""
        if ms2:   # an MS/MS scan inside the MS1 scan (must be skipped)
            child = (f'<scan num="{100 + n}" msLevel="2" peaksCount="1">'
                     f'<peaks precision="32" byteOrder="network" pairOrder="m/z-int">'
                     f'{_b64([99.0001, 5.0], ">f4", False)}</peaks></scan>')
        out.append(
            f'<scan num="{n + 1}" msLevel="1" peaksCount="{len(mz)}" '
            f'centroided="{1 if centroided else 0}">'
            f'<peaks precision="{precision}" byteOrder="network" pairOrder="m/z-int" '
            f'compressionType="{"zlib" if compress else "none"}">'
            f'{_b64(pairs, dtype, compress)}</peaks>{child}</scan>')
    return ('<?xml version="1.0" encoding="ISO-8859-1"?>\n'
            '<mzXML xmlns="http://sashimi.sourceforge.net/schema_revision/mzXML_3.2">'
            f'<msRun scanCount="{len(scans)}">{"".join(out)}</msRun></mzXML>').encode("latin-1")


# ---------------------------------------------------------------------- mzData

def mzdata(scans: list[tuple]) -> bytes:
    out = []
    for n, (mz, inten) in enumerate(scans):
        out.append(
            f'<spectrum id="{n + 1}"><spectrumDesc><spectrumSettings>'
            f'<spectrumInstrument msLevel="1"/></spectrumSettings></spectrumDesc>'
            f'<mzArrayBinary><data precision="64" endian="little" length="{len(mz)}">'
            f'{_b64(mz, "<f8", False)}</data></mzArrayBinary>'
            f'<intenArrayBinary><data precision="32" endian="little" length="{len(mz)}">'
            f'{_b64(inten, "<f4", False)}</data></intenArrayBinary></spectrum>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<mzData version="1.05" '
            f'accessionNumber="0"><spectrumList count="{len(scans)}">{"".join(out)}'
            '</spectrumList></mzData>').encode("utf-8")


# ---------------------------------------------------------------------- MGF

def mgf(peaks: list[tuple[float, float]], pepmass: float | None = None) -> str:
    lines = ["# made-up MGF export", "BEGIN IONS", "TITLE=made-up sample 3a",
             "CHARGE=1+"]
    if pepmass is not None:
        lines.append(f"PEPMASS={pepmass:.5f} 123456")
    lines += [f"{mz:.5f} {i:.1f}" for mz, i in peaks]
    lines.append("END IONS")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- JCAMP-DX

def jcamp_peak_table(peaks: list[tuple[float, float]]) -> str:
    head = ["##TITLE=made-up mass spectrum of 3a", "##JCAMP-DX=5.01",
            "##DATA TYPE=MASS SPECTRUM", "##DATA CLASS=PEAK TABLE", "##XUNITS=M/Z",
            "##YUNITS=RELATIVE ABUNDANCE", f"##NPOINTS={len(peaks)}",
            "##PEAK TABLE=(XY..XY)"]
    body = []
    for i in range(0, len(peaks), 3):
        body.append(" ".join(f"{mz:.5f},{y:.1f}" for mz, y in peaks[i:i + 3]))
    return "\n".join(head + body + ["##END="]) + "\n"


_SQZ_POS, _SQZ_NEG = "@ABCDEFGHI", "abcdefghi"
_DIF_POS, _DIF_NEG = "%JKLMNOPQR", "jklmnopqr"
_DUP = "STUVWXYZs"


def _squeeze(value: int, pos: str, neg: str) -> str:
    text = str(abs(value))
    first = int(text[0])
    if value < 0:
        return neg[first - 1] + text[1:]
    return pos[first] + text[1:]


def _dup(count: int) -> str:
    text = str(count)
    return _DUP[int(text[0]) - 1] + text[1:]


def asdf_lines(ys: list[int], first_x: float, delta_x: float, per_line: int = 10) -> list[str]:
    """XYDATA lines in DIFDUP form (the standard ASDF compression): each line is the X
    value, the first Y squeezed, then differences (repeats folded into DUP); every line
    after the first starts with the Y check value (the last Y of the line before)."""
    lines = []
    i = 0
    n = len(ys)
    while i < n:
        chunk = ys[i:i + per_line]
        start = i
        tokens = [_squeeze(chunk[0], _SQZ_POS, _SQZ_NEG)]
        diffs = [chunk[k] - chunk[k - 1] for k in range(1, len(chunk))]
        k = 0
        while k < len(diffs):
            run = 1
            while k + run < len(diffs) and diffs[k + run] == diffs[k]:
                run += 1
            tokens.append(_squeeze(diffs[k], _DIF_POS, _DIF_NEG))
            if run > 1:
                tokens.append(_dup(run))
            k += run
        x = first_x + start * delta_x
        lines.append(f"{x:.6f}" + "".join(tokens))
        i += len(chunk) - 1 if len(chunk) > 1 and i + len(chunk) < n else len(chunk)
    return lines


def jcamp_xydata(x: np.ndarray, y: np.ndarray, asdf: bool = False) -> str:
    """Profile data on an even grid as ##XYDATA=(X++(Y..Y)), AFFN or ASDF compressed."""
    ys = [int(round(v)) for v in y]
    first, last = float(x[0]), float(x[-1])
    delta = (last - first) / (len(x) - 1)
    head = ["##TITLE=made-up profile spectrum", "##JCAMP-DX=4.24",
            "##DATA TYPE=MASS SPECTRUM", "##XUNITS=M/Z", "##YUNITS=COUNTS",
            "##XFACTOR=1", "##YFACTOR=1", f"##FIRSTX={first:.6f}", f"##LASTX={last:.6f}",
            f"##NPOINTS={len(ys)}", f"##DELTAX={delta:.8f}", "##XYDATA=(X++(Y..Y))"]
    if asdf:
        body = asdf_lines(ys, first, delta)
    else:
        body = [f"{first + i * delta:.6f} " + " ".join(str(v) for v in ys[i:i + 8])
                for i in range(0, len(ys), 8)]
    return "\n".join(head + body + ["##END="]) + "\n"


# ---------------------------------------------------------------------- netCDF (ANDI-MS)

def _nc_name(name: str) -> bytes:
    raw = name.encode()
    return struct.pack(">I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _nc_values(nc_type: int, values) -> bytes:
    if nc_type == 2:
        raw = values.encode()
        return struct.pack(">I", len(raw)) + raw + b"\0" * (-len(raw) % 4)
    fmt = {3: ">i2", 4: ">i4", 5: ">f4", 6: ">f8"}[nc_type]
    raw = np.asarray(values, dtype=fmt).tobytes()
    return struct.pack(">I", len(values)) + raw + b"\0" * (-len(raw) % 4)


def netcdf(scans: list[tuple], centroided: bool = True, scale: float | None = None) -> bytes:
    """An ANDI-MS (netCDF classic) file: mass_values, intensity_values, scan_index and
    point_count; `scale`: mass_values stored as integers with a scale_factor."""
    mz = np.concatenate([np.asarray(s[0], float) for s in scans])
    inten = np.concatenate([np.asarray(s[1], float) for s in scans])
    index = np.cumsum([0] + [len(s[0]) for s in scans[:-1]])
    counts = [len(s[0]) for s in scans]
    dims = [("point_number", len(mz)), ("scan_number", len(scans))]
    gatts = [("experiment_type", 2,
              "Centroided Mass Spectrum" if centroided else "Continuum Mass Spectrum"),
             ("comments", 2, "made-up test data")]
    if scale:
        mass_type, mass_data, mass_atts = 4, np.round(mz / scale).astype(int), \
            [("scale_factor", 6, [scale])]
    else:
        mass_type, mass_data, mass_atts = 6, mz, []
    variables = [("mass_values", [0], mass_atts, mass_type, mass_data),
                 ("intensity_values", [0], [], 5, inten),
                 ("scan_index", [1], [], 4, index),
                 ("point_count", [1], [], 4, counts)]

    def atts(items) -> bytes:
        if not items:
            return struct.pack(">II", 0, 0)
        out = struct.pack(">II", 12, len(items))
        for name, t, v in items:
            out += _nc_name(name) + struct.pack(">I", t)
            out += _nc_values(t, v)
        return out

    sizes = {4: 4, 5: 4, 6: 8, 3: 2}

    def header(begins: list[int]) -> bytes:
        out = b"CDF\x01" + struct.pack(">I", 0)
        out += struct.pack(">II", 10, len(dims))
        for name, n in dims:
            out += _nc_name(name) + struct.pack(">I", n)
        out += atts(gatts)
        out += struct.pack(">II", 11, len(variables))
        for (name, dimids, vatts, t, data), begin in zip(variables, begins):
            vsize = len(data) * sizes[t]
            vsize += -vsize % 4
            out += _nc_name(name) + struct.pack(">I", len(dimids))
            out += b"".join(struct.pack(">I", d) for d in dimids)
            out += atts(vatts) + struct.pack(">III", t, vsize, begin)
        return out

    size = len(header([0] * len(variables)))
    begins, blobs, pos = [], [], size
    for name, dimids, vatts, t, data in variables:
        fmt = {4: ">i4", 5: ">f4", 6: ">f8", 3: ">i2"}[t]
        raw = np.asarray(data, dtype=fmt).tobytes()
        raw += b"\0" * (-len(raw) % 4)
        begins.append(pos)
        blobs.append(raw)
        pos += len(raw)
    return header(begins) + b"".join(blobs)


# ---------------------------------------------------------------------- tables

def csv_text(peaks: list[tuple[float, float]], delimiter: str = ",", header: bool = True,
             decimal_comma: bool = False) -> str:
    rows = [f"Made-up export of sample 3a{delimiter}{delimiter}"]
    if header:
        rows.append(delimiter.join(["Peak", "m/z", "Intensity"]))
    for n, (mz, i) in enumerate(peaks, 1):
        cells = [str(n), f"{mz:.5f}", f"{i:.1f}"]
        if decimal_comma:
            cells = [c.replace(".", ",") for c in cells]
        rows.append(delimiter.join(cells))
    return "\n".join(rows) + "\n"


def xlsx(rows: list[list], shared: bool = True) -> bytes:
    """A minimal .xlsx workbook (one sheet). Text cells use the shared-strings table (or
    inline strings), numbers are stored as Excel stores them."""
    strings: list[str] = []
    sheet_rows = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{repr(float(value))}</v></c>')
            elif shared:
                strings.append(str(value))
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType='
                   '"application/xml"/></Types>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
                                      '<sheet name="Peaks" sheetId="1" r:id="rId1"/>'
                                      '</sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet xmlns="{ns}"><sheetData>{"".join(sheet_rows)}</sheetData>'
                   '</worksheet>')
        if shared:
            items = "".join(f"<si><t>{s}</t></si>" for s in strings)
            z.writestr("xl/sharedStrings.xml",
                       f'<sst xmlns="{ns}" count="{len(strings)}">{items}</sst>')
    return buf.getvalue()


# ---------------------------------------------------------------------- PDF reports

def _typst_pdf(path: Path, body: str) -> Path:
    import typst
    src = path.with_suffix(".typ")
    src.write_text('#set page(paper: "us-letter", margin: 1.6cm)\n'
                   '#set text(font: "DejaVu Sans Mono", size: 8.5pt)\n' + body,
                   encoding="utf-8")
    typst.compile(str(src), output=str(path))
    src.unlink()
    return path


def _row(cells: list[str]) -> str:
    return ", ".join(f"[{c}]" for c in cells)


def agilent_like_pdf(path: Path, offset_ppm: float = 1.2) -> Path:
    """A made-up report in the style of a peak list with a formula match: columns
    "m/z  Abund  Formula  Calc m/z  Diff (ppm)" (the measured m/z first)."""
    peaks = ion_peaks()
    rows = []
    for p in peaks[:3]:
        meas = p.mz * (1 + offset_ppm * 1e-6)
        rows.append(_row([f"{meas:.4f}", f"{int(p.abundance * 1234)}", "C12H16NO3",
                          f"{p.mz:.4f}", f"{offset_ppm:.2f}"]))
    rows.append(_row(["244.0941", "20412", "", "", ""]))
    body = ("= Qualitative Analysis Report (made up)\n"
            "Data File: made_up_3a.d #h(2em) Sample Name: 3a #h(2em) Ion source: ESI\n\n"
            "== Peak List\n"
            "#table(columns: 5, stroke: none, align: right, "
            f"[m/z], [Abund], [Formula], [Calc m/z], [Diff (ppm)], {', '.join(rows)})\n"
            "Acquired 2026-10-07 10:15:42.123\n")
    return _typst_pdf(path, body)


def shimadzu_like_pdf(path: Path, offset_ppm: float = -0.8) -> Path:
    """A made-up "Formula  Calc. m/z  Meas. m/z  Diff (ppm)" table (calculated first)."""
    p = ion_peaks()[0]
    meas = p.mz * (1 + offset_ppm * 1e-6)
    body = ("= Formula Predictor Report (made up)\n"
            "#table(columns: 5, stroke: 0.4pt, "
            "[Ion], [Formula], [Calc. m/z], [Meas. m/z], [Diff (ppm)], "
            f"[[M+H]+], [C12H16NO3], [{p.mz:.4f}], [{meas:.4f}], [{offset_ppm:.2f}])\n")
    return _typst_pdf(path, body)


def bruker_like_pdf(path: Path, offset_ppm: float = 2.1) -> Path:
    """A made-up "Meas. m/z  #  Ion Formula  m/z  err [ppm]" table: the plain "m/z" column
    after the formula is the calculated one."""
    p = ion_peaks()[0]
    meas = p.mz * (1 + offset_ppm * 1e-6)
    body = ("= Generate Molecular Formula (made up)\n"
            "#table(columns: 6, stroke: none, "
            "[Meas. m/z], [\\#], [Ion Formula], [m/z], [err \\[ppm\\]], [mSigma], "
            f"[{meas:.4f}], [1], [C12H16NO3], [{p.mz:.4f}], [{-offset_ppm:.1f}], [12.3])\n")
    return _typst_pdf(path, body)


def text_report_pdf(path: Path, found: float, calc: float) -> Path:
    """A made-up report written as running text: "calcd … ; found …"."""
    body = ("= HRMS result (made up)\n"
            f"HRMS (ESI) m/z: [M+H]+ calcd for C12H16NO3 {calc:.4f}; found {found:.4f}.\n\n"
            "Instrument: made-up Q-TOF, resolution 30000, lock mass 556.2771 (calibrant).\n")
    return _typst_pdf(path, body)


def scanned_pdf(path: Path) -> Path:
    """A PDF with no text (like a scanned page): just lines."""
    return _typst_pdf(path, "#line(length: 80%)\n#rect(width: 50%, height: 3cm)\n")
