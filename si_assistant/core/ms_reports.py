"""HRMS report files: reading their m/z values, and picking the found m/z (SPEC §13n.3).

A compound's HRMS field keeps the instrument's report files in the compound folder
(hrms/). Any kind of file (or folder) is kept; these are read, with no vendor software:

* open data formats: mzML (indexed or not; base64 arrays, zlib or none, 32/64-bit),
  mzXML, mzData, MGF, JCAMP-DX (peak tables, and XYDATA in AFFN or ASDF compression),
  netCDF/ANDI-MS (.cdf, read by the small reader below);
* exports: CSV/TSV/TXT peak lists, Excel .xlsx (read straight from its XML), and the text
  of PDF reports (pypdf), as printed by Agilent MassHunter, Thermo Xcalibur/FreeStyle,
  Waters MassLynx, Bruker DataAnalysis, Shimadzu…
* pictures are kept (and can be printed) but not read; vendor raw data (Thermo .raw,
  Agilent/Bruker .d, Waters .raw folders, SCIEX .wiff, Shimadzu .lcd…) is kept, with a
  note saying how to get a readable copy.

Spectra (data formats): only MS1 spectra where the file says so. Profile (not centroided)
spectra are centroided: every local maximum becomes one peak at the intensity-weighted
centre of its points above half its height. The peaks of all scans of a file are then
merged (peaks within MERGE_PPM of each other become one, at their intensity-weighted
m/z), as averaging the scans would.

Reports as text (PDF, TXT, a table without m/z columns): numbers with at least three
decimals in MZ_RANGE are m/z values. A table header ("m/z  Abund  Formula  Calc m/z  Diff
(ppm)", "Formula  Calc. m/z  Meas. m/z  Diff (ppm)", "Meas. m/z  #  Ion Formula  m/z  err
[ppm]"…) says which column is measured and which calculated; in running text a nearby
"calcd"/"theor." or "found"/"meas." does. Calculated values are never used as the found
m/z.

Picking the found m/z (choose_match): of every m/z value read from every report, those
within the HRMS tolerance (ppm) of a calculated isotope peak of the chosen ion; a value a
report marks as measured beats an unmarked one, then one on the monoisotopic peak beats
the other isotope peaks, then the nearest wins.

The Found box (update_found): filled from the reports when it is empty or still holds the
value filled from a report before (compound.json: hrms.found_from = {"file", "mz"}); a
value typed by the user is never changed (checks.py warns when it differs from the
reports). Pure Python with NumPy; no user-interface code.
"""

from __future__ import annotations

import base64
import io
import math
import re
import struct
import zipfile
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

HRMS_DIR = "hrms"              # <compound>/hrms/: the report files
REPORTS_KEY = "reports"        # compound.json: hrms.reports = [{"file", "kind"}]
FOUND_FROM_KEY = "found_from"  # hrms.found_from = {"file", "mz"}: Found came from a report
PRINT_KEY = "print_reports"    # hrms.print_reports: true/false; absent = the section default
TRASH_FIELD = "hrms_reports"   # the field key of a report file in the Trash

MZ_RANGE = (40.0, 6000.0)      # plausible m/z values in a report's text
MERGE_PPM = 3.0                # peaks of different scans this close are one
MIN_REL_INTENSITY = 0.001      # peaks below 0.1 % of their scan's largest are dropped
MAX_PEAKS_PER_SCAN = 3000
MAX_PEAKS = 50000
MAX_FILE_BYTES = 400 * 1024 * 1024
LISTING_LINES = 150            # printed listing of a text report: at most this many lines
LISTING_WIDTH = 110            # … of at most this many characters

PICTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".svg")
TEXT_EXTENSIONS = (".csv", ".tsv", ".txt", ".tab", ".asc", ".xy", ".dat", ".prn", ".text")
JCAMP_EXTENSIONS = (".jdx", ".dx", ".jcamp", ".jcm")
CDF_EXTENSIONS = (".cdf", ".nc")
XLSX_EXTENSIONS = (".xlsx", ".xlsm")


class ReportReadError(Exception):
    """A report that could not be read; the message is for the user."""


@dataclass
class ReportData:
    """What was read from one report file."""
    name: str
    kind: str                                   # "mzML", "PDF report", "picture", …
    mz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    intensity: np.ndarray = field(default_factory=lambda: np.zeros(0))  # nan: not known
    measured: frozenset = frozenset()           # values (rounded 6) marked measured/found
    calculated: tuple = ()                      # values marked calculated (never "found")
    message: str = ""                           # in plain words, for the HRMS field
    readable: bool = True                       # False: nothing can be read from it
    profile: bool = False                       # profile data, centroided here
    scans: int = 0                              # spectra read (data formats)

    @property
    def count(self) -> int:
        return int(len(self.mz))

    def is_measured(self, mz: float) -> bool:
        return round(float(mz), 6) in self.measured


@dataclass
class Match:
    """The report value picked as the found m/z."""
    mz: float
    file: str
    peak: Any            # chem.Peak: the calculated isotope peak it was matched to
    ppm: float
    measured: bool       # the report marks it as measured/found


# ---------------------------------------------------------------------- what a file is

VENDOR_FILES = {".raw": "Thermo .raw data", ".wiff": "SCIEX .wiff data",
                ".wiff2": "SCIEX .wiff2 data", ".scan": "SCIEX .wiff.scan data",
                ".lcd": "Shimadzu .lcd data", ".qgd": "Shimadzu .qgd data",
                ".baf": "Bruker raw data (analysis.baf)", ".tdf": "Bruker raw data (.tdf)",
                ".tdf_bin": "Bruker raw data (.tdf_bin)", ".yep": "Bruker raw data (.yep)",
                ".fid": "Bruker raw data (fid)", ".ms": "Agilent ChemStation .ms data",
                ".rec": "raw instrument data"}
VENDOR_DIR_FILES = ("msscan.bin", "msprofile.bin", "mspeak.bin", "analysis.baf",
                    "analysis.tdf", "_func001.dat", "_extern.inf")


def vendor_raw_kind(path: Path) -> str | None:
    """What kind of instrument raw data `path` is ("Thermo .raw data", "Agilent .d data
    folder"…), or None if it isn't raw data. A file inside a .d or .raw folder counts."""
    path = Path(path)
    suffix = path.suffix.lower()
    if path.is_dir() or (not path.exists() and suffix in (".d", ".raw")):
        if suffix == ".d":
            names = {p.name.lower() for p in path.rglob("*")} if path.is_dir() else set()
            if names & {"analysis.baf", "analysis.tdf", "analysis.yep"}:
                return "Bruker .d data folder"
            if "acqdata" in names or names & {"msscan.bin", "msprofile.bin"}:
                return "Agilent .d data folder"
            return "Agilent or Bruker .d data folder"
        if suffix == ".raw":
            return "Waters .raw data folder"
        if path.is_dir() and any(p.name.lower() in VENDOR_DIR_FILES for p in path.rglob("*")):
            return "a folder of instrument raw data"
        return None
    if path.parent.suffix.lower() in (".d", ".raw") or \
            path.parent.parent.suffix.lower() in (".d", ".raw"):
        return "a file from an instrument's raw data folder"
    if path.name.lower().endswith(".wiff.scan"):
        return VENDOR_FILES[".scan"]
    if path.name.lower() in VENDOR_DIR_FILES:
        return "a file of instrument raw data"
    return VENDOR_FILES.get(suffix) if suffix != ".scan" else None


def raw_data_message(name: str, kind: str) -> str:
    return (f"{name} is {kind}: the instrument's own format, which this program can't "
            "read. It is kept with the compound. To fill in the found m/z automatically, "
            "also add a report exported from the instrument software (PDF or CSV), or "
            "convert the data to mzML with ProteoWizard MSConvert (free) and add that.")


def picture_message(name: str) -> str:
    return (f"{name} is a picture: it is kept (and can be printed), but numbers can't be "
            "read from pictures. Type the found m/z yourself.")


def print_kind(path: Path) -> str:
    """How a report is printed: "pdf" (page by page), "picture" (as it is), "listing"
    (formatted text), "peaks" (a table of the peaks near the expected m/z) or "none"
    (raw data, folders and files that can't be shown)."""
    path = Path(path)
    if path.is_dir() or vendor_raw_kind(path):
        return "none"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in PICTURE_EXTENSIONS:
        return "picture"
    if suffix in (".mzml", ".mzxml", ".mzdata") or suffix in CDF_EXTENSIONS or \
            path.name.lower().endswith(".mzdata.xml"):
        return "peaks"
    if suffix in TEXT_EXTENSIONS + JCAMP_EXTENSIONS + XLSX_EXTENSIONS + (".mgf",):
        return "listing"
    return "listing" if _is_text_file(path) else "none"


def _is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return all(b >= 9 for b in head)


# ---------------------------------------------------------------------- reading (cached)

_CACHE: "OrderedDict[tuple, ReportData]" = OrderedDict()
_CACHE_MAX = 64


def read_report(path: Path) -> ReportData:
    """What can be read from one report file (or folder). Never raises: a file that can't
    be read gives `readable=False` and a message saying why. Remembered until the file
    changes."""
    path = Path(path)
    try:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns, path.is_dir())
    except OSError:
        return ReportData(path.name, "missing", readable=False,
                          message=f"{path.name} is missing from the compound's folder.")
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    data = _read_uncached(path)
    _CACHE[key] = data
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return data


def _read_uncached(path: Path) -> ReportData:
    name = path.name
    raw = vendor_raw_kind(path)
    if raw:
        return ReportData(name, raw, readable=False, message=raw_data_message(name, raw))
    if path.is_dir():
        return ReportData(name, "folder", readable=False,
                          message=f"{name} is a folder: it is kept, but not read.")
    suffix = path.suffix.lower()
    if suffix in PICTURE_EXTENSIONS:
        return ReportData(name, "picture", readable=False, message=picture_message(name))
    if suffix == ".xls":
        return ReportData(name, "old Excel file", readable=False,
                          message=f"{name} is an old Excel file (.xls), which can't be read. "
                                  "Save it as .xlsx or CSV in Excel and add that.")
    if suffix in (".doc", ".docx", ".rtf", ".odt"):
        return ReportData(name, "document", readable=False,
                          message=f"{name} can't be read. Save it as PDF and add that.")
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return ReportData(name, "large file", readable=False,
                              message=f"{name} is too large to read here "
                                      f"({size / 1e6:.0f} MB). Export a peak list or a PDF "
                                      "report of the spectrum and add that.")
        if suffix == ".pdf":
            data = read_pdf(path)
        else:
            content = path.read_bytes()
            data = read_bytes(content, name)
    except ReportReadError as e:
        return ReportData(name, _kind_name(path), readable=False, message=str(e))
    except Exception as e:  # noqa: BLE001 — a damaged file must never stop the program
        return ReportData(name, _kind_name(path), readable=False,
                          message=f"{name} could not be read (it may be damaged, or a "
                                  f"variant this program doesn't know): {type(e).__name__}.")
    data.name = name
    if not data.message:
        data.message = summary(data)
    return data


def _kind_name(path: Path) -> str:
    suffix = path.suffix.lower()
    return {".pdf": "PDF report", ".mzml": "mzML", ".mzxml": "mzXML", ".mgf": "MGF",
            ".xlsx": "Excel file", ".cdf": "netCDF (ANDI-MS)"}.get(suffix, suffix.lstrip(".")
                                                                  or "file")


def summary(data: ReportData) -> str:
    """One line on what was read: "PDF report: 3 m/z values (1 marked measured)"."""
    if not data.readable:
        return data.message
    n = data.count
    if not n:
        return f"{data.kind}: no m/z values found in it."
    what = f"{n} m/z value{'s' if n != 1 else ''}"
    if data.scans:
        what = (f"{n} peak{'s' if n != 1 else ''} from {data.scans} "
                f"spectr{'a' if data.scans != 1 else 'um'}")
        if data.profile:
            what += " (profile data, centroided)"
    if data.measured:
        what += f" ({len(data.measured)} marked measured)"
    return f"{data.kind}: {what}."


def read_bytes(content: bytes, name: str = "") -> ReportData:
    """Read a report from its bytes, by its name's extension (and its first bytes)."""
    lower = name.lower()
    suffix = Path(lower).suffix
    head = content[:2048].lstrip()
    if content[:3] == b"CDF" or suffix in CDF_EXTENSIONS:
        return read_netcdf(content)
    if content[:2] == b"PK" or suffix in XLSX_EXTENSIONS:
        return read_xlsx(content)
    if content[:5] == b"%PDF-":
        return read_pdf_bytes(content)
    if suffix in (".mzml", ".mzxml", ".mzdata") or lower.endswith(".mzdata.xml") or \
            head.startswith(b"<?xml") or head.startswith(b"<"):
        root = _xml_root_name(content)
        if root in ("mzML", "indexedmzML"):
            return read_mzml(content)
        if root in ("mzXML", "msRun"):
            return read_mzxml(content)
        if root == "mzData":
            return read_mzdata(content)
        if suffix in (".mzml", ".mzxml", ".mzdata"):
            raise ReportReadError(f"{name} is not a readable {suffix.lstrip('.')} file.")
    text = _decode(content)
    if suffix == ".mgf" or re.search(r"^\s*BEGIN IONS", text, re.M):
        return read_mgf(text)
    if suffix in JCAMP_EXTENSIONS or re.match(r"\s*##TITLE=", text):
        return read_jcamp(text)
    return read_table_text(text)


def _decode(content: bytes) -> str:
    for codec in ("utf-8-sig", "utf-16") if content[:2] in (b"\xff\xfe", b"\xfe\xff") \
            else ("utf-8-sig",):
        try:
            return content.decode(codec)
        except UnicodeDecodeError:
            pass
    return content.decode("latin-1")


def _xml_root_name(content: bytes) -> str:
    try:
        for _event, elem in ET.iterparse(io.BytesIO(content), events=("start",)):
            return _local(elem.tag)
    except ET.ParseError:
        return ""
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------- peaks

def looks_profile(mz: np.ndarray, intensity: np.ndarray) -> bool:
    """Whether a spectrum is profile data (many closely spaced points per peak) rather
    than a list of centroids."""
    n = len(mz)
    if n < 30:
        return False
    order = np.argsort(mz)
    x, y = mz[order], intensity[order]
    d = np.diff(x)
    ok = (d > 0) & (x[1:] > 0)
    if not ok.any():
        return False
    spacing_ppm = float(np.median(d[ok] / x[1:][ok])) * 1e6
    maxima = int(np.count_nonzero((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:])))
    return spacing_ppm < 150 and maxima < 0.3 * n


def centroid(mz: np.ndarray, intensity: np.ndarray,
             min_rel: float = MIN_REL_INTENSITY) -> tuple[np.ndarray, np.ndarray]:
    """Profile data → peaks: each local maximum (above `min_rel` of the largest) becomes one
    peak with the maximum's height. Its m/z is the top of a Gaussian through the highest
    point and its two neighbours (exact for a Gaussian peak, however it is sampled); when
    that can't be used (a flat or one-sided top) it is the intensity-weighted centre of
    the points above half its height."""
    order = np.argsort(mz)
    x = np.asarray(mz, dtype=float)[order]
    y = np.asarray(intensity, dtype=float)[order]
    n = len(x)
    if n < 3 or not np.isfinite(y).any() or y.max() <= 0:
        return np.zeros(0), np.zeros(0)
    top = y.max()
    inner = np.flatnonzero((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:])) + 1
    inner = inner[y[inner] >= min_rel * top]
    if len(inner) > MAX_PEAKS_PER_SCAN:
        inner = inner[np.argsort(y[inner])[-MAX_PEAKS_PER_SCAN:]]
        inner.sort()
    spacing = np.diff(x)
    gap_limit = 8 * float(np.median(spacing)) if len(spacing) else 0.0
    out_x, out_y = [], []
    for i in inner:
        half = y[i] / 2
        lo = i
        while lo > 0 and y[lo - 1] >= half and y[lo - 1] <= y[lo] and \
                x[lo] - x[lo - 1] <= gap_limit:
            lo -= 1
        hi = i
        while hi < n - 1 and y[hi + 1] >= half and y[hi + 1] <= y[hi] and \
                x[hi + 1] - x[hi] <= gap_limit:
            hi += 1
        w = y[lo:hi + 1]
        centre = float(np.dot(x[lo:hi + 1], w) / w.sum())
        if 0 < i < n - 1 and y[i - 1] > 0 and y[i + 1] > 0 and \
                x[i] - x[i - 1] <= gap_limit and x[i + 1] - x[i] <= gap_limit:
            # (log-parabola through three points: the top of the Gaussian)
            a, b, c = np.log(y[i - 1]), np.log(y[i]), np.log(y[i + 1])
            h1, h2 = x[i] - x[i - 1], x[i + 1] - x[i]
            denom = h2 * (a - b) + h1 * (c - b)
            if denom < 0:   # (curving down: a top)
                t = (h2 * h2 * (a - b) - h1 * h1 * (c - b)) / (2 * denom)
                if -h1 <= t <= h2:
                    centre = float(x[i] + t)
        out_x.append(centre)
        out_y.append(float(y[i]))
    return np.array(out_x), np.array(out_y)


def _scan_peaks(mz, intensity, profile: bool | None) -> tuple[np.ndarray, np.ndarray, bool]:
    """One spectrum's peaks: centroided if it is profile data (`profile` None: decided by
    looking at it), small ones dropped."""
    mz = np.asarray(mz, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    if len(mz) != len(intensity):
        n = min(len(mz), len(intensity))
        mz, intensity = mz[:n], intensity[:n]
    keep = np.isfinite(mz) & np.isfinite(intensity) & (mz > 0)
    mz, intensity = mz[keep], intensity[keep]
    if not len(mz):
        return mz, intensity, False
    is_profile = looks_profile(mz, intensity) if profile is None else profile
    if is_profile:
        mz, intensity = centroid(mz, intensity)
    elif len(mz) and intensity.max() > 0:
        keep = intensity >= MIN_REL_INTENSITY * intensity.max()
        mz, intensity = mz[keep], intensity[keep]
        if len(mz) > MAX_PEAKS_PER_SCAN:
            top = np.argsort(intensity)[-MAX_PEAKS_PER_SCAN:]
            mz, intensity = mz[top], intensity[top]
    return mz, intensity, bool(is_profile)


def merge_peaks(scans: list[tuple[np.ndarray, np.ndarray]],
                ppm: float = MERGE_PPM) -> tuple[np.ndarray, np.ndarray]:
    """The peaks of several scans as one list: peaks within `ppm` of their neighbour are
    one peak at their intensity-weighted m/z, with the summed intensity."""
    scans = [(np.asarray(m, float), np.asarray(i, float)) for m, i in scans if len(m)]
    if not scans:
        return np.zeros(0), np.zeros(0)
    if len(scans) == 1:
        order = np.argsort(scans[0][0])
        return scans[0][0][order], scans[0][1][order]
    mz = np.concatenate([s[0] for s in scans])
    inten = np.concatenate([s[1] for s in scans])
    order = np.argsort(mz)
    mz, inten = mz[order], inten[order]
    breaks = np.diff(mz) > mz[1:] * ppm * 1e-6
    group = np.concatenate([[0], np.cumsum(breaks)])
    weights = np.where(inten > 0, inten, 1e-12)
    total = np.bincount(group, weights=weights)
    centre = np.bincount(group, weights=mz * weights) / total
    summed = np.bincount(group, weights=inten)
    if len(centre) > MAX_PEAKS:
        top = np.sort(np.argsort(summed)[-MAX_PEAKS:])
        centre, summed = centre[top], summed[top]
    return centre, summed


def _spectra_result(kind: str, scans: list[tuple], profile_flags: list[bool],
                    message: str = "") -> ReportData:
    mz, inten = merge_peaks(scans)
    return ReportData("", kind, mz, inten, scans=len(scans), profile=any(profile_flags),
                      message=message)


def _array(data: bytes, dtype: str) -> np.ndarray:
    return np.frombuffer(data, dtype=dtype).astype(float)


MAX_ARRAY_BYTES = 256 * 1024 * 1024   # one unpacked data array (a damaged or "zip bomb"
MAX_POINTS = 20_000_000               # array is not unpacked); points of one JCAMP spectrum


def _inflate(raw: bytes) -> bytes:
    """zlib-unpacked data, at most MAX_ARRAY_BYTES (ReportReadError beyond that)."""
    d = zlib.decompressobj()
    out = d.decompress(raw, MAX_ARRAY_BYTES)
    if d.unconsumed_tail:
        raise ReportReadError("A data array in this file is too large to read here.")
    return out


# ---------------------------------------------------------------------- mzML

MZML_FLOAT32, MZML_FLOAT64 = "MS:1000521", "MS:1000523"
MZML_ZLIB, MZML_NO_COMPRESSION = "MS:1000574", "MS:1000576"
MZML_MZ_ARRAY, MZML_INTENSITY_ARRAY = "MS:1000514", "MS:1000515"
MZML_MS_LEVEL, MZML_CENTROID, MZML_PROFILE = "MS:1000511", "MS:1000127", "MS:1000128"
MZML_NUMPRESS = {"MS:1002312", "MS:1002313", "MS:1002314", "MS:1002746", "MS:1002747",
                 "MS:1002748"}


def read_mzml(content: bytes) -> ReportData:
    """The MS1 spectra of an mzML file (indexed or not)."""
    scans, flags, levels = [], [], []
    for event, elem in ET.iterparse(io.BytesIO(content), events=("end",)):
        if _local(elem.tag) != "spectrum":
            continue
        level, profile = None, None
        for p in elem.iterfind("{*}cvParam"):
            acc = p.get("accession", "")
            if acc == MZML_MS_LEVEL:
                level = int(float(p.get("value") or 1))
            elif acc == MZML_CENTROID:
                profile = False
            elif acc == MZML_PROFILE:
                profile = True
        levels.append(level)
        if level not in (None, 1):
            elem.clear()
            continue
        arrays = {}
        for bda in elem.iter():
            if _local(bda.tag) != "binaryDataArray":
                continue
            accs = {p.get("accession", "") for p in bda.iter() if _local(p.tag) == "cvParam"}
            if accs & MZML_NUMPRESS:
                raise ReportReadError(
                    "This mzML file is compressed with MS-Numpress, which this program can't "
                    "read. Convert it again with MSConvert with Numpress switched off.")
            binary = next((b for b in bda.iter() if _local(b.tag) == "binary"), None)
            raw = base64.b64decode((binary.text or "").strip()) if binary is not None else b""
            if MZML_ZLIB in accs and raw:
                raw = _inflate(raw)
            dtype = "<f4" if MZML_FLOAT32 in accs else "<f8"
            if MZML_MZ_ARRAY in accs:
                arrays["mz"] = _array(raw, dtype)
            elif MZML_INTENSITY_ARRAY in accs:
                arrays["i"] = _array(raw, dtype)
        if "mz" in arrays and "i" in arrays:
            mz, inten, was_profile = _scan_peaks(arrays["mz"], arrays["i"], profile)
            scans.append((mz, inten))
            flags.append(was_profile)
        elem.clear()
    if not scans and any(lv not in (None, 1) for lv in levels):
        raise ReportReadError("This mzML file has no MS1 spectra (only MS/MS), so no found "
                              "m/z can be read from it.")
    return _spectra_result("mzML", scans, flags)


# ---------------------------------------------------------------------- mzXML

def read_mzxml(content: bytes) -> ReportData:
    """The MS1 scans of an mzXML file (base64 peaks in network byte order, zlib or none)."""
    scans, flags, levels = [], [], []
    stack: list[dict] = []
    for event, elem in ET.iterparse(io.BytesIO(content), events=("start", "end")):
        tag = _local(elem.tag)
        if event == "start":
            if tag == "scan":
                stack.append(dict(elem.attrib))
            continue
        if tag == "scan":
            if stack:
                stack.pop()
            continue
        if tag != "peaks" or not stack:
            continue
        scan = stack[-1]
        level = int(scan.get("msLevel", "1") or 1)
        levels.append(level)
        if level != 1:
            continue
        raw = base64.b64decode((elem.text or "").strip())
        if (elem.get("compressionType") or "none").lower() == "zlib" and raw:
            raw = _inflate(raw)
        precision = elem.get("precision", "32")
        order = ">" if (elem.get("byteOrder") or "network").lower() in ("network", "big") \
            else "<"
        values = _array(raw, f"{order}f{8 if precision == '64' else 4}")
        mz, inten = values[0::2], values[1::2]
        centroided = scan.get("centroided")
        profile = None if centroided is None else centroided not in ("1", "true")
        mz, inten, was_profile = _scan_peaks(mz, inten, profile)
        scans.append((mz, inten))
        flags.append(was_profile)
    if not scans and levels:
        raise ReportReadError("This mzXML file has no MS1 scans, so no found m/z can be read "
                              "from it.")
    return _spectra_result("mzXML", scans, flags)


# ---------------------------------------------------------------------- mzData

def read_mzdata(content: bytes) -> ReportData:
    """The MS1 spectra of an mzData file (mzArrayBinary/intenArrayBinary)."""
    scans, flags = [], []
    for event, elem in ET.iterparse(io.BytesIO(content), events=("end",)):
        if _local(elem.tag) != "spectrum":
            continue
        level = 1
        for inst in elem.iter():
            if _local(inst.tag) == "spectrumInstrument" and inst.get("msLevel"):
                level = int(inst.get("msLevel"))
        arrays = {}
        for holder in elem.iter():
            name = _local(holder.tag)
            if name not in ("mzArrayBinary", "intenArrayBinary"):
                continue
            data = next((d for d in holder.iter() if _local(d.tag) == "data"), None)
            if data is None:
                continue
            raw = base64.b64decode((data.text or "").strip())
            order = "<" if (data.get("endian") or "little").lower() == "little" else ">"
            arrays[name] = _array(raw, f"{order}f{8 if data.get('precision') == '64' else 4}")
        if level == 1 and len(arrays) == 2:
            mz, inten, was_profile = _scan_peaks(arrays["mzArrayBinary"],
                                                 arrays["intenArrayBinary"], None)
            scans.append((mz, inten))
            flags.append(was_profile)
        elem.clear()
    return _spectra_result("mzData", scans, flags)


# ---------------------------------------------------------------------- MGF

_NUMBER = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"


def read_mgf(text: str) -> ReportData:
    """An MGF file: each BEGIN IONS … END IONS block is a spectrum; its PEPMASS (the
    precursor's m/z) counts as a value too."""
    scans, flags, extra = [], [], []
    block: list[tuple[float, float]] | None = None
    for line in text.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper == "BEGIN IONS":
            block = []
        elif upper == "END IONS":
            if block:
                arr = np.array(block)
                mz, inten, was_profile = _scan_peaks(arr[:, 0], arr[:, 1], None)
                scans.append((mz, inten))
                flags.append(was_profile)
            block = None
        elif block is not None and upper.startswith("PEPMASS="):
            nums = re.findall(_NUMBER, s.split("=", 1)[1])
            if nums:
                extra.append(float(nums[0]))
        elif block is not None and s and (s[0].isdigit() or s[0] == "."):
            nums = re.findall(_NUMBER, s)
            if len(nums) >= 2:
                block.append((float(nums[0]), float(nums[1])))
    data = _spectra_result("MGF", scans, flags)
    if extra:
        data.mz = np.concatenate([data.mz, extra])
        data.intensity = np.concatenate([data.intensity, np.full(len(extra), np.nan)])
    return data


# ---------------------------------------------------------------------- JCAMP-DX

SQZ = {**{c: i for i, c in enumerate("@ABCDEFGHI")}, **{c: -(i + 1) for i, c in
                                                          enumerate("abcdefghi")}}
DIF = {**{c: i for i, c in enumerate("%JKLMNOPQR")}, **{c: -(i + 1) for i, c in
                                                          enumerate("jklmnopqr")}}
DUP = {c: i + 1 for i, c in enumerate("STUVWXYZs")}
_ASDF_TOKEN = re.compile(r"[@A-Ia-i%J-Rj-rS-Zs]\d*|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ASDF_CHARS = re.compile(r"[@A-DF-Ia-df-i%J-Rj-rS-Zs]")


def decode_asdf_line(line: str) -> tuple[list[float], bool]:
    """One line of JCAMP-DX XYDATA in AFFN or ASDF form (SQZ, DIF, DUP): its numbers, the
    first being X; and whether it ended in DIF form (then the next line's first Y repeats
    this line's last one, as a check)."""
    values: list[float] = []
    last_kind, last_delta = "", 0.0
    for tok in _ASDF_TOKEN.findall(line):
        c = tok[0]
        if c in SQZ:
            digits = tok[1:]
            first = SQZ[c]
            v = float(f"{abs(first)}{digits}") * (-1 if first < 0 else 1)
            values.append(v)
            last_kind = "sqz"
        elif c in DIF:
            first = DIF[c]
            d = float(f"{abs(first)}{tok[1:]}") * (-1 if first < 0 else 1)
            values.append((values[-1] if values else 0.0) + d)
            last_kind, last_delta = "dif", d
        elif c in DUP:
            count = int(f"{DUP[c]}{tok[1:]}")
            if count > MAX_POINTS or len(values) + count > MAX_POINTS:
                raise ReportReadError("This JCAMP-DX file is damaged (a repeat count is "
                                      "far too large).")
            for _ in range(count - 1):
                if last_kind == "dif":
                    values.append(values[-1] + last_delta)
                elif values:
                    values.append(values[-1])
            # (a DUP of a DIF keeps the line "ending in DIF")
        else:
            values.append(float(tok))
            last_kind = "affn"
    return values, last_kind == "dif"


def _jcamp_number(text: str, default: float | None = None) -> float | None:
    m = re.search(_NUMBER, text or "")
    return float(m.group(0)) if m else default


def read_jcamp(text: str) -> ReportData:
    """A JCAMP-DX mass spectrum: ##PEAK TABLE=(XY..XY) / ##XYPOINTS / NTUPLES data tables
    (peaks), or ##XYDATA=(X++(Y..Y)) (profile, AFFN or ASDF compressed)."""
    scans, flags = [], []
    labels: dict[str, str] = {}
    mode, rows, check_next = "", [], False
    xy: list[float] = []
    xs_first: list[float] = []

    def finish():
        nonlocal mode, rows, xy, xs_first
        xf = _jcamp_number(labels.get("XFACTOR", ""), 1.0) or 1.0
        yf = _jcamp_number(labels.get("YFACTOR", ""), 1.0) or 1.0
        factors = re.findall(_NUMBER, labels.get("FACTOR", ""))
        if mode == "pairs" and rows:
            if len(factors) >= 2 and "XFACTOR" not in labels:
                xf, yf = float(factors[0]), float(factors[1])
            arr = np.array(rows[: len(rows) // 2 * 2], dtype=float).reshape(-1, 2)
            mz, inten, was_profile = _scan_peaks(arr[:, 0] * xf, arr[:, 1] * yf, None)
            scans.append((mz, inten))
            flags.append(was_profile)
        elif mode == "xydata" and xy:
            first = _jcamp_number(labels.get("FIRSTX", ""))
            last = _jcamp_number(labels.get("LASTX", ""))
            npoints = _jcamp_number(labels.get("NPOINTS", ""))
            n = len(xy)
            if first is not None and last is not None and n > 1:
                x = np.linspace(first, last, int(npoints) if npoints and
                                int(npoints) == n else n)
            elif xs_first:
                delta = _jcamp_number(labels.get("DELTAX", ""), 1.0)
                x = xs_first[0] * xf + np.arange(n) * delta
            else:
                x = np.arange(n, dtype=float)
            mz, inten, was_profile = _scan_peaks(x[:n], np.array(xy) * yf, True)
            scans.append((mz, inten))
            flags.append(was_profile)
        mode, rows, xy, xs_first = "", [], [], []

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("$$"):
            continue
        if s.startswith("##"):
            label, _, value = s[2:].partition("=")
            label = label.strip().upper()
            value = value.split("$$")[0].strip()
            if label in ("PEAK TABLE", "XYPOINTS", "DATA TABLE", "XYDATA", "END", "PAGE",
                         "TITLE"):
                finish()
            if label in ("PEAK TABLE", "XYPOINTS") or (
                    label == "DATA TABLE" and "XY..XY" in value.upper().replace(" ", "")):
                mode = "pairs"
            elif label in ("XYDATA", "DATA TABLE") and "Y..Y" in value.upper().replace(" ", ""):
                mode = "xydata"
                check_next = False
            elif label == "TITLE":
                labels = {}
            labels[label] = value
            continue
        if mode == "pairs":
            rows += [float(v) for v in re.findall(_NUMBER, s)]
        elif mode == "xydata" and s:
            values, ends_dif = decode_asdf_line(s) if _ASDF_CHARS.search(s) else \
                ([float(v) for v in re.findall(_NUMBER, s)], False)
            if not values:
                continue
            xs_first.append(values[0])
            ys = values[1:]
            if check_next and ys:
                ys = ys[1:]   # (the Y check value: the last Y of the line before)
            xy += ys
            if len(xy) > MAX_POINTS:
                raise ReportReadError("This JCAMP-DX file has too many points to read here.")
            check_next = ends_dif
    finish()
    if not scans:
        raise ReportReadError("No mass spectrum (peak table or XYDATA) was found in this "
                              "JCAMP-DX file.")
    return _spectra_result("JCAMP-DX", scans, flags)


# ---------------------------------------------------------------------- netCDF (ANDI-MS)

_NC_TYPES = {1: ("b", 1), 2: ("S1", 1), 3: (">i2", 2), 4: (">i4", 4), 5: (">f4", 4),
             6: (">f8", 8)}


class _Reader:
    def __init__(self, data: bytes, offset64: bool):
        self.data, self.pos, self.offset64 = data, 4, offset64

    def u32(self) -> int:
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def offset(self) -> int:
        if self.offset64:
            v = struct.unpack_from(">Q", self.data, self.pos)[0]
            self.pos += 8
            return v
        return self.u32()

    def name(self) -> str:
        n = self.u32()
        text = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
        self.pos += (n + 3) // 4 * 4
        return text

    def values(self, nc_type: int, n: int):
        dtype, size = _NC_TYPES[nc_type]
        raw = self.data[self.pos:self.pos + n * size]
        self.pos += (n * size + 3) // 4 * 4
        if nc_type == 2:
            return raw.decode("utf-8", "replace").rstrip("\x00")
        return np.frombuffer(raw, dtype=dtype)

    def attributes(self) -> dict[str, Any]:
        tag, n = self.u32(), self.u32()
        out = {}
        if tag == 0 and n == 0:
            return out
        for _ in range(n):
            name = self.name()
            nc_type, count = self.u32(), self.u32()
            out[name] = self.values(nc_type, count)
        return out


def read_netcdf_variables(content: bytes) -> tuple[dict[str, Any], dict[str, tuple]]:
    """A classic (or 64-bit offset) netCDF file: (global attributes, {variable name:
    (array, attributes)}). A small reader of just what ANDI-MS files need."""
    if content[:3] != b"CDF" or content[3] not in (1, 2):
        raise ReportReadError("This .cdf file is not a classic netCDF file (netCDF-4/HDF5 "
                              "files can't be read). Export it again as ANDI-MS (netCDF 3).")
    r = _Reader(content, content[3] == 2)
    numrecs = r.u32()
    tag, ndims = r.u32(), r.u32()
    dims = []
    for _ in range(ndims if tag else 0):
        dims.append((r.name(), r.u32()))
    gatts = r.attributes()
    tag, nvars = r.u32(), r.u32()
    specs = []
    for _ in range(nvars if tag else 0):
        name = r.name()
        n = r.u32()
        dimids = [r.u32() for _ in range(n)]
        vatts = r.attributes()
        nc_type, vsize, begin = r.u32(), r.u32(), r.offset()
        specs.append((name, dimids, vatts, nc_type, vsize, begin))
    for _name, dimids, _vatts, nc_type, _vsize, _begin in specs:
        if nc_type not in _NC_TYPES or any(not 0 <= d < len(dims) for d in dimids):
            raise ReportReadError("This netCDF file is damaged (a variable has an unknown "
                                  "type or dimension).")
    record_vars = [s for s in specs if s[1] and dims[s[1][0]][1] == 0]
    recsize = sum(s[4] for s in record_vars)
    if len(record_vars) == 1:   # (a single record variable is not padded)
        dtype, size = _NC_TYPES[record_vars[0][3]]
        recsize = size * _product([dims[d][1] for d in record_vars[0][1][1:]])
    # The number of records: STREAMING (0xFFFFFFFF, "not known yet") or a damaged count
    # can't be more than the records the file holds; no record size means none are read.
    if record_vars:
        first = min(s[5] for s in record_vars)
        fit = (len(content) - first) // recsize if recsize > 0 and first < len(content) else 0
        numrecs = fit if numrecs == STREAMING else min(numrecs, max(0, fit))
    out = {}
    for name, dimids, vatts, nc_type, vsize, begin in specs:
        dtype, size = _NC_TYPES[nc_type]
        if nc_type == 2:
            continue
        shape = [dims[d][1] for d in dimids]
        if shape and shape[0] == 0:   # a record variable
            per = _product(shape[1:])
            if numrecs and (per * size > recsize
                            or begin + (numrecs - 1) * recsize + per * size > len(content)):
                raise ReportReadError("This netCDF file is damaged (its records don't fit).")
            parts = [np.frombuffer(content, dtype=dtype, count=per, offset=begin + k * recsize)
                     for k in range(numrecs)]
            arr = np.concatenate(parts) if parts else np.zeros(0)
        else:
            count = _product(shape)
            if begin + count * size > len(content):
                raise ReportReadError("This netCDF file is damaged (a variable is cut off).")
            arr = np.frombuffer(content, dtype=dtype, count=count, offset=begin)
        out[name] = (arr.astype(float), vatts)
    return gatts, out


STREAMING = 0xFFFFFFFF   # netCDF: "the number of records is not known yet"


def _product(values) -> int:
    n = 1
    for v in values:
        n *= int(v)
    return n


def read_netcdf(content: bytes) -> ReportData:
    """An ANDI-MS (AIA) netCDF file: mass_values, intensity_values, scan_index and
    point_count (scale_factor attributes applied)."""
    gatts, variables = read_netcdf_variables(content)
    for key in ("mass_values", "intensity_values", "scan_index"):
        if key not in variables:
            raise ReportReadError("This netCDF file is not an ANDI-MS mass spectrum (it has "
                                  f"no {key}).")

    def values(key):
        arr, atts = variables[key]
        scale = atts.get("scale_factor")
        return arr * float(scale[0]) if scale is not None and len(scale) else arr

    mz, inten = values("mass_values"), values("intensity_values")
    index = variables["scan_index"][0].astype(int)
    counts = (variables["point_count"][0].astype(int) if "point_count" in variables
              else np.diff(np.append(index, len(mz))))
    kind = str(gatts.get("experiment_type", "")).lower()
    profile = True if "continuum" in kind or "profile" in kind else \
        False if "centroid" in kind else None
    scans, flags = [], []
    for start, count in zip(index, counts):
        m, i, was_profile = _scan_peaks(mz[start:start + count], inten[start:start + count],
                                        profile)
        scans.append((m, i))
        flags.append(was_profile)
    return _spectra_result("netCDF (ANDI-MS)", scans, flags)


# ---------------------------------------------------------------------- text reports

_MZ_TEXT = re.compile(r"(?<![\w.,:/+-])(\d{2,4}\.\d{3,7})(?!\d|\.\d|\s*%)")
# (whole words only: "theo." but not "theophylline", "meas." but not "measurement")
_CALC_WORDS = (r"(?<![A-Za-z])(?:calc(?:ulated|d)?\.?|theor(?:etical|\.)?|theo\.?|expected|"
               r"exact\s+mass|required|requires|target)(?![A-Za-z])")
_FOUND_WORDS = (r"(?<![A-Za-z])(?:found|meas(?:ured|\.)?|observed|obsd\.?|obs\.?|"
                r"experimental|exptl\.?|detected|accurate\s+mass)(?![A-Za-z])")
_KEYWORD = re.compile(rf"(?P<calc>{_CALC_WORDS})|(?P<found>{_FOUND_WORDS})", re.I)
_HEADER_MASS = re.compile(
    rf"(?P<calc>{_CALC_WORDS}\s*(?:ion\s+)?(?:m/z|mass|m\.z\.)?)|"
    rf"(?P<meas>{_FOUND_WORDS}\s*(?:ion\s+)?(?:m/z|mass)?)|"
    r"(?P<plain>(?<![\w/])(?:m/z|mass|m\.z\.)(?![\w/])"
    r"(?!\s*(?:error|accuracy|diff|defect|range|spectr|tolerance|window|resolution|"
    r"shift|delta)))", re.I)
_COLUMN_WORDS = re.compile(
    r"abund|intens|\bint\.?\b|formula|ppm|mda|\bdiff|error|\berr\b|\bdbe\b|\brdb|score|"
    r"composition|\bion\b|height|area|delta|i-fit|relative|\brel\b|%|\bres\b|charge|"
    r"adduct|species|\bz\b", re.I)


def _in_range(v: float) -> bool:
    return MZ_RANGE[0] <= v <= MZ_RANGE[1]


def header_columns(line: str) -> list[str] | None:
    """The mass columns of a table header, in order: "calc" or "meas" (a plain "m/z"/"Mass"
    column is measured, unless the header also names a measured column, as Bruker's
    "Meas. m/z … Ion Formula  m/z" does: then it is the calculated one). None if `line`
    is not a header."""
    if _MZ_TEXT.search(line):
        return None
    found = [m.lastgroup for m in _HEADER_MASS.finditer(line)]
    if not found:
        return None
    rest = _HEADER_MASS.sub(" ", line)
    if not _COLUMN_WORDS.search(rest) and len(found) < 2:
        return None
    has_meas = "meas" in found
    return [("calc" if has_meas else "meas") if k == "plain" else k for k in found]


def parse_report_text(text: str, kind: str = "text report") -> ReportData:
    """The m/z values in a report's text (a PDF's text, a TXT report): table rows mapped
    to their header's columns, labelled numbers in running text, and the other plausible
    m/z values (numbers with at least three decimals in MZ_RANGE)."""
    measured, calculated, plain = [], [], []
    columns: list[str] | None = None
    idle = 0
    for line in text.splitlines():
        head = header_columns(line)
        if head:
            columns, idle = head, 0
            continue
        numbers = [(m.start(), float(m.group(1))) for m in _MZ_TEXT.finditer(line)]
        numbers = [(pos, v) for pos, v in numbers if _in_range(v)]
        if not numbers:
            if line.strip():
                idle += 1
                if idle > 3:
                    columns = None
            continue
        idle = 0
        labelled = _labelled(line, numbers)
        if columns and len(numbers) >= len(columns) and not any(labelled):
            # A table row: its first numbers are the header's mass columns, in order
            # (a further number with three decimals, e.g. a score, is just a value).
            for (_pos, v), col in zip(numbers, columns):
                (calculated if col == "calc" else measured).append(v)
            plain += [v for _pos, v in numbers[len(columns):]]
            continue
        if columns and len(numbers) == 1 and set(columns) == {"meas"} and not labelled[0]:
            measured.append(numbers[0][1])
            continue
        for (_pos, v), label in zip(numbers, labelled):
            if label == "calc":
                calculated.append(v)
            elif label == "found":
                measured.append(v)
            else:
                plain.append(v)
    return _text_result(kind, measured, calculated, plain)


def _labelled(line: str, numbers: list[tuple[int, float]]) -> list[str]:
    """For each number in a line of running text: "calc" or "found" after the nearest
    such word before it (on the same line), else ""."""
    out = []
    for pos, _v in numbers:
        words = list(_KEYWORD.finditer(line, 0, pos))
        out.append(words[-1].lastgroup if words else "")
    return out


def _text_result(kind: str, measured: list[float], calculated: list[float],
                 plain: list[float], intensity: dict[float, float] | None = None
                 ) -> ReportData:
    calc_keys = {round(v, 4) for v in calculated}
    # A plain number equal to a calculated one is that calculated value printed again.
    values = list(dict.fromkeys(measured + [v for v in plain if round(v, 4) not in calc_keys]))
    mz = np.array(values, dtype=float)
    inten = np.array([(intensity or {}).get(v, np.nan) for v in values], dtype=float)
    return ReportData("", kind, mz, inten, measured=frozenset(round(v, 6) for v in measured),
                      calculated=tuple(dict.fromkeys(calculated)))


# ---------------------------------------------------------------------- tables (CSV, XLSX)

_INTENSITY_HEADER = re.compile(r"intens|abund|\bint\b|\bint\.|counts|height|area|\by\b|"
                               r"relative|\brel\b|%", re.I)


def _cell_number(text: str, comma_decimal: bool) -> float | None:
    s = str(text).strip().strip('"').replace("−", "-")
    if not s:
        return None
    if comma_decimal:
        s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 else s
    else:
        s = s.replace(",", "") if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", s) else s
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _decimals(text: str) -> int:
    m = re.search(r"[.,](\d+)\s*$", str(text).strip())
    return len(m.group(1)) if m else 0


def _split_rows(text: str) -> tuple[list[list[str]], bool, bool]:
    """Rows of cells of a delimited text, whether it uses decimal commas, and whether it
    has a delimiter (tab, semicolon, comma) rather than spaces."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = lines[:200]
    best, best_score = None, 0
    for delim in ("\t", ";", ","):
        counts = [ln.count(delim) for ln in sample]
        common = max(set(counts), key=counts.count) if counts else 0
        score = sum(1 for c in counts if c == common and c > 0)
        if common and score > best_score:
            best, best_score = delim, score
    if best is None or best_score < max(2, len(sample) // 3):
        rows = [ln.split() for ln in lines]
        best = None
    else:
        rows = [[c.strip() for c in ln.split(best)] for ln in lines]
    comma = best in ("\t", ";", None) and sum(
        1 for r in rows[:200] for c in r if re.fullmatch(r"\s*[+-]?\d+,\d+\s*", c)) > \
        sum(1 for r in rows[:200] for c in r if re.fullmatch(r"\s*[+-]?\d+\.\d+\s*", c))
    return rows, comma, best is not None


def table_values(rows: list[list[str]], comma_decimal: bool = False,
                 kind: str = "peak list") -> ReportData | None:
    """m/z values from a table (CSV/TSV/XLSX rows): its header says which columns are m/z
    (measured or calculated) and intensity; without one, the first column of numbers with
    three or more decimals in MZ_RANGE is m/z and the next numeric column its intensity.
    None if it doesn't look like a table of m/z values."""
    header_at, mass_cols, inten_col = None, [], None
    for r, row in enumerate(rows[:50]):
        kinds = []
        for c, cell in enumerate(row):
            if _cell_number(cell, comma_decimal) is not None:
                kinds.append(None)
                continue
            found = [m.lastgroup for m in _HEADER_MASS.finditer(str(cell))]
            kinds.append(found[0] if len(found) == 1 else None)
        if any(kinds):
            has_meas = "meas" in kinds
            mass_cols = [(c, ("calc" if has_meas else "meas") if k == "plain" else k)
                         for c, k in enumerate(kinds) if k]
            inten_col = next((c for c, cell in enumerate(row) if kinds[c] is None and
                              _INTENSITY_HEADER.search(str(cell))), None)
            header_at = r
            break
    measured, calculated, plain, intensity = [], [], [], {}
    paired = any(k == "calc" for _c, k in mass_cols)
    if header_at is not None:
        for row in rows[header_at + 1:]:
            inten = (_cell_number(row[inten_col], comma_decimal)
                     if inten_col is not None and inten_col < len(row) else None)
            for c, k in mass_cols:
                if c >= len(row):
                    continue
                v = _cell_number(row[c], comma_decimal)
                if v is None or v <= 0:
                    continue
                if k == "calc":
                    calculated.append(v)
                else:
                    (measured if paired else plain).append(v)
                    if inten is not None:
                        intensity[v] = inten
        if measured or calculated or plain:
            return _table_result(kind, measured, calculated, plain, intensity)
        return None
    # No header: the shape of the numbers.
    numeric = []
    for row in rows:
        nums = [(_cell_number(c, comma_decimal), _decimals(c)) for c in row]
        if len(nums) >= 1 and all(n is not None for n, _d in nums):
            numeric.append(nums)
    if len(numeric) < 3:
        return None
    width = max(set(len(r) for r in numeric), key=[len(r) for r in numeric].count)
    numeric = [r for r in numeric if len(r) == width]
    mz_col = next((c for c in range(width)
                   if sum(1 for r in numeric if _in_range(r[c][0]) and r[c][1] >= 3)
                   >= 0.8 * len(numeric)), None)
    if mz_col is None:
        return None
    inten_c = mz_col + 1 if mz_col + 1 < width else None
    for r in numeric:
        v = r[mz_col][0]
        plain.append(v)
        if inten_c is not None:
            intensity[v] = r[inten_c][0]
    return _table_result(kind, [], [], plain, intensity)


def _table_result(kind, measured, calculated, plain, intensity) -> ReportData:
    values = measured + plain
    inten = np.array([intensity.get(v, np.nan) for v in values], dtype=float)
    if len(values) >= 30 and np.isfinite(inten).all():
        mz, i, was_profile = _scan_peaks(np.array(values), inten, None)
        if was_profile:
            data = _text_result(kind, [], calculated, list(mz),
                                dict(zip(mz.tolist(), i.tolist())))
            data.profile, data.scans = True, 1
            return data
    return _text_result(kind, measured, calculated, plain, intensity)


def read_table_text(text: str) -> ReportData:
    """A CSV/TSV/TXT file: a peak list (a table), or else a text report."""
    rows, comma, delimited = _split_rows(text)
    numeric = sum(1 for r in rows if r and all(_cell_number(c, comma) is not None for c in r))
    if delimited or (numeric >= 3 and numeric >= 0.5 * len(rows)):
        data = table_values(rows, comma, "peak list")
        if data is not None and data.count:
            return data
    return parse_report_text(text, "text report")


# ---------------------------------------------------------------------- Excel .xlsx

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


XLSX_MAX_COLUMN = 16384        # Excel's last column (XFD): cells beyond it are ignored
XLSX_MAX_COLUMNS = 256         # at most this many used columns of a sheet are kept
XLSX_MAX_CELLS = 1_000_000     # … and at most this many cells (rows × columns) in all
XLSX_MAX_PART_BYTES = 200 * 1024 * 1024   # a sheet's XML unpacked (a damaged or "zip bomb"
                                          # file is not unpacked)


def xlsx_rows(content: bytes, max_rows: int = 100000) -> list[list[list[str]]]:
    """Every sheet of an .xlsx workbook as rows of cell texts (shared strings and inline
    strings resolved, numbers as written in the file). Read with zipfile, no Excel.

    Cells are collected sparsely (only those in the file); then each sheet keeps just its
    used columns, in order, so a stray cell far to the right (or a damaged reference such
    as "ZZZZZZZ1", beyond Excel's last column XFD: ignored) costs one column, not millions.
    At most `max_rows` rows, XLSX_MAX_COLUMNS columns and XLSX_MAX_CELLS cells in all.
    Raises ReportReadError (or zipfile/XML errors) for a file that can't be read."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = z.namelist()

        def part(name: str) -> bytes:
            if z.getinfo(name).file_size > XLSX_MAX_PART_BYTES:
                raise ReportReadError("This Excel file is too large to read here. Save the "
                                      "peak list as CSV and add that.")
            return z.read(name)

        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(part("xl/sharedStrings.xml"))
            for si in root.iter(f"{_XLSX_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t")))
        sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        out = []
        budget = XLSX_MAX_CELLS
        for name in sheets:
            sparse: list[dict[int, str]] = []
            cells_read = 0
            for row in ET.fromstring(part(name)).iter(f"{_XLSX_NS}row"):
                cells: dict[int, str] = {}
                for c in row.iter(f"{_XLSX_NS}c"):
                    ref = c.get("r", "")
                    col = _column_index(ref) if ref else (max(cells) + 1 if cells else 0)
                    if not 0 <= col < XLSX_MAX_COLUMN:
                        continue
                    t = c.get("t", "")
                    v = c.find(f"{_XLSX_NS}v")
                    if t == "s" and v is not None:
                        k = _int_or_none(v.text)
                        text = shared[k] if k is not None and 0 <= k < len(shared) else ""
                    elif t == "inlineStr":
                        text = "".join(x.text or "" for x in c.iter(f"{_XLSX_NS}t"))
                    else:
                        text = v.text if v is not None and v.text else ""
                        if t not in ("str", "b", "e") and text:
                            text = _excel_number(text)
                    cells[col] = text
                if cells:
                    sparse.append(cells)
                    cells_read += len(cells)
                if len(sparse) >= max_rows or cells_read >= budget:
                    break
            used = sorted({col for cells in sparse for col in cells})[:XLSX_MAX_COLUMNS]
            if used:
                sparse = sparse[:max(1, budget // len(used))]
            rows = [[cells.get(col, "") for col in used] for cells in sparse]
            budget = max(0, budget - len(rows) * len(used))
            out.append(rows)
        return out


def _int_or_none(text) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _column_index(ref: str) -> int:
    """0 for "A1", 25 for "Z3", 26 for "AA1"… (-1 for no letters; huge for nonsense, which
    the caller ignores beyond Excel's last column)."""
    letters = re.match(r"[A-Z]{1,8}", ref.upper())
    if not letters:
        return -1
    n = 0
    for ch in letters.group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _excel_number(text: str) -> str:
    """A number as Excel stores it ("188.10730000000001") written plainly ("188.1073")."""
    try:
        v = float(text)
    except ValueError:
        return text
    return f"{v:.8f}".rstrip("0").rstrip(".") if v != int(v) or "." in text else str(int(v))


def read_xlsx(content: bytes) -> ReportData:
    try:
        sheets = xlsx_rows(content)
    except ReportReadError:
        raise
    except Exception as e:  # noqa: BLE001 — zip/XML/zlib errors of a damaged file
        raise ReportReadError("This Excel file could not be read (it may be damaged).") from e
    best = None
    for rows in sheets:
        data = table_values(rows, False, "Excel file")
        if data is not None and data.count and (best is None or data.count > best.count):
            best = data
    if best is not None:
        return best
    text = "\n".join("  ".join(r) for rows in sheets for r in rows)
    return parse_report_text(text, "Excel file")


# ---------------------------------------------------------------------- PDF

def pdf_text(path_or_bytes) -> str:
    from pypdf import PdfReader
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else \
        str(path_or_bytes)
    reader = PdfReader(source)
    parts = []
    for page in reader.pages:
        # Layout mode keeps a table's cells on one line, in their columns.
        text = ""
        for mode in ("layout", "plain"):
            try:
                text = page.extract_text(extraction_mode=mode) or ""
            except Exception:  # noqa: BLE001 — a page that can't be read gives no text
                text = ""
            if text.strip():
                break
        parts.append(text)
    return "\n".join(parts)


def _pdf_result(text: str) -> ReportData:
    if not text.strip():
        raise ReportReadError("This PDF has no text in it (probably a scanned picture), so "
                              "no m/z can be read from it. Type the found m/z yourself.")
    return parse_report_text(text, "PDF report")


def read_pdf(path: Path) -> ReportData:
    try:
        text = pdf_text(path)
    except Exception as e:  # noqa: BLE001
        raise ReportReadError(f"{Path(path).name} could not be opened as a PDF (it may be "
                              "damaged).") from e
    return _pdf_result(text)


def read_pdf_bytes(content: bytes) -> ReportData:
    try:
        text = pdf_text(content)
    except Exception as e:  # noqa: BLE001
        raise ReportReadError("This file could not be opened as a PDF.") from e
    return _pdf_result(text)


# ---------------------------------------------------------------------- picking the found m/z

def choose_match(reports: list[ReportData], peaks: list, tolerance: float) -> Match | None:
    """The report value used as the found m/z: of every value within `tolerance` ppm of a
    calculated isotope peak (`peaks`, chem.Peak), one marked measured beats an unmarked one,
    then one on the monoisotopic peak beats the others, then the nearest wins. None if no
    value is close enough."""
    best_key, best = None, None
    for report in reports:
        if not report.readable or not report.count:
            continue
        values = report.mz
        for peak in peaks:
            window = peak.mz * tolerance * 1e-6
            near = np.flatnonzero(np.abs(values - peak.mz) <= window + 1e-12)
            for i in near:
                v = float(values[i])
                ppm = (v - peak.mz) / peak.mz * 1e6
                measured = report.is_measured(v)
                key = (0 if measured else 1, 0 if peak.monoisotopic else 1, abs(ppm))
                if best_key is None or key < best_key:
                    best_key = key
                    best = Match(v, report.name, peak, ppm, measured)
    return best


def format_mz(mz: float) -> str:
    """How a found m/z from a report is written in the Found box (four decimals)."""
    return f"{float(mz):.4f}"


def report_items(compound) -> list[dict]:
    value = compound.values.get("hrms")
    items = value.get(REPORTS_KEY) if isinstance(value, dict) else None
    return [i for i in items or [] if isinstance(i, dict) and i.get("file")]


def report_path(compound, name: str) -> Path:
    return Path(compound.folder) / HRMS_DIR / name


def compound_reports(project, compound) -> list[ReportData]:
    """What was read from each of a compound's report files, in the field's order."""
    return [read_report(report_path(compound, i["file"])) for i in report_items(compound)]


def expected_peaks(project, compound, value: dict | None = None):
    """The calculated isotope peaks of the compound's chosen ion (checks.hrms_calc), or
    None when there is no structure/formula or the adduct can't be used."""
    from . import chem, checks
    value = value if value is not None else (compound.values.get("hrms") or {})
    try:
        sp = checks.hrms_species(project.read_structure(compound), value)
        if sp is None:
            return None
        return checks.hrms_calc(sp.info, str(value.get("adduct", "") or ""), "",
                                tolerance(project)).peaks
    except chem.ChemError:
        return None


def tolerance(project) -> float:
    try:
        return float(project.settings.get("hrms_tolerance_ppm", 5.0))
    except (TypeError, ValueError):
        return 5.0


def compound_match(project, compound, value: dict | None = None) -> Match | None:
    """The report value for the compound's Found box (see choose_match), or None."""
    if not report_items(compound):
        return None
    peaks = expected_peaks(project, compound, value)
    if not peaks:
        return None
    return choose_match(compound_reports(project, compound), peaks, tolerance(project))


def found_is_from_report(value: dict) -> bool:
    """Whether the Found box still holds the value filled from a report."""
    source = value.get(FOUND_FROM_KEY)
    found = str(value.get("found", "") or "").strip()
    if not isinstance(source, dict) or not found:
        return False
    try:
        return found == format_mz(float(source.get("mz")))
    except (TypeError, ValueError):
        return False


def update_found(project, compound, value: dict | None = None) -> str:
    """Fill the Found box from the reports (in `value`, default the compound's HRMS value;
    nothing is saved): when it is empty or still holds the value filled from a report
    before. A value the user typed is never changed (its "found_from" is dropped).
    Returns "filled", "cleared" (the report value no longer fits) or ""."""
    if value is None:
        value = compound.values.get("hrms")
        if not isinstance(value, dict):
            return ""
    from_report = found_is_from_report(value)
    found = str(value.get("found", "") or "").strip()
    if not from_report:
        value.pop(FOUND_FROM_KEY, None)
        if found:
            return ""
    match = compound_match(project, compound, value)
    if match is None:
        if from_report:
            value["found"] = ""
            value.pop(FOUND_FROM_KEY, None)
            return "cleared"
        return ""
    new = format_mz(match.mz)
    source = {"file": match.file, "mz": round(match.mz, 6)}
    if from_report and found == new and value.get(FOUND_FROM_KEY) == source:
        return ""
    value["found"] = new
    value[FOUND_FROM_KEY] = source
    return "filled"


def differs_message(project, compound) -> tuple[str, Match | None]:
    """When the found m/z was typed by the user and differs from the reports' value by
    more than the tolerance: the warning, and the report value. ("", None) otherwise."""
    value = compound.values.get("hrms")
    if not isinstance(value, dict) or found_is_from_report(value):
        return "", None
    try:
        found = float(str(value.get("found", "") or "").strip().replace(",", "."))
    except ValueError:
        return "", None
    match = compound_match(project, compound, value)
    if match is None or found <= 0:
        return "", None
    ppm = (found - match.mz) / match.mz * 1e6
    if abs(ppm) <= tolerance(project):
        return "", match
    # (ppm = (yours − the report's) / the report's: "+2.0 ppm" = yours is higher)
    return (f"The found m/z you typed ({found:.4f}) is {ppm:+.1f} ppm from the report's "
            f"value ({match.mz:.4f} in {match.file}). Check which is right."), match


def prints_reports(project, compound) -> bool:
    """Whether the compound's HRMS reports are printed in the Spectra section: its own
    choice (hrms.print_reports), else the Spectra section's "Print HRMS reports"."""
    if "hrms" not in compound.field_keys or not report_items(compound):
        return False
    value = compound.values.get("hrms") or {}
    own = value.get(PRINT_KEY)
    if isinstance(own, bool):
        return own
    return bool(project.spectra_defaults().get("hrms_reports", False))


def peaks_near(data: ReportData, centre: float | None, below: float = 2.0,
               above: float = 6.0, count: int = 10) -> list[tuple[float, float]]:
    """The largest peaks of a data file near the expected m/z (from `below` under to
    `above` over it; the whole spectrum without one): (m/z, % of the largest), by m/z."""
    if not data.count:
        return []
    mz, inten = data.mz, np.nan_to_num(data.intensity, nan=0.0)
    if centre is not None:
        keep = (mz >= centre - below) & (mz <= centre + above)
        mz, inten = mz[keep], inten[keep]
    if not len(mz):
        return []
    top = np.argsort(inten)[-count:]
    base = inten.max() or 1.0
    return sorted((float(mz[i]), float(inten[i] / base * 100)) for i in top)


# ---------------------------------------------------------------------- printed listings

UNREADABLE_LISTING = "(could not be read)"


def listing_lines(path: Path, max_lines: int = LISTING_LINES,
                  width: int = LISTING_WIDTH) -> tuple[list[str], int]:
    """A text-like report as lines to print in a monospaced listing: delimited tables and
    Excel sheets in aligned columns, other text as it is (tabs expanded). Long lines are
    cut at `width`. Returns (lines, number of lines left out). Never raises: a file that
    can't be read gives one line saying so (`listing_failed` is True for it)."""
    try:
        return _listing_lines(Path(path), max(0, int(max_lines)), max(2, int(width)))
    except Exception:  # noqa: BLE001 — a damaged file must never stop the compile
        what = "Excel file" if Path(path).suffix.lower() in XLSX_EXTENSIONS else "file"
        return [f"{UNREADABLE_LISTING[:-1]}: this {what} may be damaged)"], 0


def listing_failed(lines: list[str]) -> bool:
    """True for the one line listing_lines gives for a file it could not read."""
    return len(lines) == 1 and lines[0].startswith(UNREADABLE_LISTING[:-1])


def _listing_lines(path: Path, max_lines: int, width: int) -> tuple[list[str], int]:
    if path.suffix.lower() in XLSX_EXTENSIONS:
        sheets = xlsx_rows(path.read_bytes(), max_rows=max_lines + 1000)
        rows = [r for s in sheets for r in s + [[]]]
        lines = _aligned(rows)
    else:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ReportReadError("too large")
        text = _decode(path.read_bytes())
        raw = text.splitlines()
        if path.suffix.lower() in (".csv", ".tsv", ".tab"):
            rows, _comma, _delimited = _split_rows(text)
            lines = _aligned(rows)
        else:
            lines = [ln.expandtabs(8).rstrip() for ln in raw]
    while lines and not lines[-1].strip():
        lines.pop()
    left = max(0, len(lines) - max_lines)
    lines = [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines[:max_lines]]
    return lines, left


def _aligned(rows: list[list[str]], cell_max: int = 18) -> list[str]:
    widths: dict[int, int] = {}
    for row in rows[:2000]:
        if sum(1 for cell in row if str(cell).strip()) < 2:
            continue   # (a title line doesn't set the columns' widths)
        for i, cell in enumerate(row):
            widths[i] = min(cell_max, max(widths.get(i, 0), len(str(cell).strip())))
    out = []
    for row in rows:
        filled = [str(cell).strip() for cell in row if str(cell).strip()]
        if len(filled) < 2:   # a title line: as it is
            out.append(filled[0] if filled else "")
            continue
        cells = []
        for i, cell in enumerate(row):
            c = str(cell).strip()
            c = c if len(c) <= cell_max else c[:cell_max - 1] + "…"
            cells.append(c.ljust(widths.get(i, 0)))
        out.append("  ".join(cells).rstrip())
    return out
