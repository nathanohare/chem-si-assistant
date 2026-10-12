"""The installed program's self-test (SPEC §13m, Phase 8).

    Chem-SI-Assistant --self-test REPORT.json [PROJECT FOLDER]

Checks that everything the program needs came with it — journal styles, word lists and
dictionaries, icons, the user guide and licences, RDKit (structures and drawings), Typst,
Qt's SVG and PDF parts (and that Qt WebEngine, no longer used, was left out), reading a PDF
saved with edit restrictions (the 'cryptography' package, see `_pdf`), the example project
(it is there and opens, see `_example`) — and, given
a project folder, compiles that project's SI (to its build/ folder, as Compile SI does). The results are written as
JSON to REPORT.json (a packaged Windows program has no console to print to); the exit
code is 0 when every check passed. Used by the release workflow and when building the
installers by hand; it changes nothing on the computer except the project's build/
folder.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable

from . import APP_NAME, __version__, resources

ARG = "--self-test"


def split_args(argv: list[str]) -> tuple[Path | None, Path | None, list[str]]:
    """(report file, project folder or None, the other arguments). (None, None, argv)
    when the self-test was not asked for."""
    if ARG not in argv:
        return None, None, list(argv)
    i = argv.index(ARG)
    rest = argv[:i] + argv[i + 1:]
    if i + 1 >= len(argv):
        return Path("self-test.json"), None, rest
    report = Path(argv[i + 1])
    rest = argv[:i] + argv[i + 2:]
    project = None
    if i + 2 < len(argv) and not argv[i + 2].startswith("--"):
        project = Path(argv[i + 2])
        rest = argv[:i] + argv[i + 3:]
    return report, project, rest


def _files() -> str:
    needed = [resources.resource("styles", s) for s in ("acs.typ", "wiley.typ", "rsc.typ")]
    needed += [resources.resource("data", d) for d in (
        "azman_chemistry_dictionary.txt", "azman_chemistry_dictionary_LICENSE.txt",
        "chem_words.txt", "name_reactions.txt", "lab_terms.txt")]
    needed += [resources.resource("icons", "app_icon_256.png"),
               resources.top_level("LICENSE"), resources.top_level("THIRD_PARTY_NOTICES.md"),
               resources.user_guide()]
    missing = [str(p) for p in needed if not p.is_file()]
    if missing:
        raise RuntimeError("missing: " + ", ".join(missing))
    return f"{len(needed)} files in {resources.bundle_root()}"


def _python() -> str:
    """Python's own libraries the program relies on: https (update check, Crossref
    look-ups), XML, ctypes (Windows clipboard and metafiles), compression."""
    import bz2
    import ctypes
    import lzma
    import ssl
    import xml.etree.ElementTree as ET
    ssl.create_default_context()
    ET.fromstring("<a><b/></a>")
    lzma.compress(b"x")
    bz2.compress(b"x")
    ctypes.c_int(1)
    return ssl.OPENSSL_VERSION


def _spelling() -> str:
    from .core import spelling
    speller = spelling.Speller()
    if not speller.known("toluene") or not speller.known("chromatography"):
        raise RuntimeError("known words are not recognised")
    if speller.known("chromatgraphy"):
        raise RuntimeError("a misspelling was not found")
    return (f"English {len(speller.english.word_frequency.dictionary):,} words, chemistry "
            f"{len(spelling.chem_dictionary()):,} words")


def _rdkit() -> str:
    from .core import chem
    mol = chem.parse_structure("CC(=O)N(C)Cc1ccccc1")
    png = chem.structure_png(mol, 300, 200)
    if not png.startswith(b"\x89PNG"):
        raise RuntimeError("no structure drawing")
    from rdkit import RDConfig
    return f"drawing {len(png):,} bytes; data {Path(RDConfig.RDDataDir).is_dir()}"


def _typst() -> str:
    import typst
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "t.typ"
        src.write_text("Hello, $C_6H_6$ #text(font: \"New Computer Modern\")[NMR]\n",
                       encoding="utf-8")
        pdf = typst.compile(str(src))
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("no PDF")
    return f"PDF {len(pdf):,} bytes"


def edit_restricted_pdf(text: str, user_password: str = "") -> bytes:
    """A small two-page PDF with `text` on its first page, saved the way much NMR and
    mass-spectrometry software saves its reports: AES-256 encrypted with an empty opening
    password (anyone can open and read it; only editing is restricted). A non-empty
    `user_password` makes a truly password-protected PDF instead. Used by the self-test
    and the tests."""
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(300, 100)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    content = DecodedStreamObject()
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(b"BT /F1 10 Tf 10 50 Td (" + safe.encode("latin-1") + b") Tj ET")
    page.replace_contents(content)
    writer.add_blank_page(300, 100)
    # Made-up test sample only: this PDF is built in memory from the text above, never
    # from a user's file, and is thrown away straight after the check. "owner-only" is a
    # fixed dummy value that PDF edit restrictions require, not a real password.
    writer.encrypt(user_password=user_password, owner_password="owner-only",
                   algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _pdf() -> str:
    """Spectra and HRMS report PDFs saved with edit restrictions (AES-encrypted with an
    empty opening password, as much instrument software does) can be read: pypdf needs
    the 'cryptography' package for that. Without it such files are reported as
    password-protected and left out of the SI. (Reading such PDFs is the only use of
    'cryptography'. Apart from the throw-away sample PDF that edit_restricted_pdf makes
    for this check, the program never encrypts anything, and it keeps no passwords or
    keys.)"""
    import cryptography
    from .compile import document
    from .core import ms_reports
    text = "HRMS (ESI) found 165.0912"
    data = edit_restricted_pdf(text)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "edit_restricted.pdf"
        path.write_bytes(data)
        pages = document.pdf_page_count(path)
    if pages != 2:
        raise RuntimeError(f"an edit-restricted PDF gave {pages} pages instead of 2")
    if "165.0912" not in ms_reports.pdf_text(data):
        raise RuntimeError("the text of an edit-restricted PDF could not be read")
    return f"edit-restricted (AES) PDFs open; cryptography {cryptography.__version__}"


def _qt() -> str:
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    import PySide6.QtPdf  # noqa: F401
    svg = QSvgRenderer(QByteArray(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" '
                                  b'height="10"><rect width="10" height="10"/></svg>'))
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    svg.render(painter)
    painter.end()
    if image.pixelColor(5, 5).alpha() == 0:
        raise RuntimeError("Qt SVG drew nothing")
    if resources.is_frozen() and _module_shipped("PySide6.QtWebEngineCore"):
        # (SPEC §13n.2: the structure editor and its web engine were removed; shipping
        # the engine would only make the installers ~200 MB larger.)
        raise RuntimeError("Qt WebEngine is bundled, but the program no longer uses it")
    return "QtSvg, QtPdf (no Qt WebEngine)"


def _example() -> str:
    """The example project (Help ▸ Open example project) came with the program and opens:
    read-only and without taking its lock, so nothing in the program's folder changes."""
    from .core import chem
    from .core.project import Project
    folder = resources.example_project()
    if not folder.is_dir():
        raise RuntimeError(f"missing: {folder}")
    if not resources.example_pdf().is_file():   # (Help ▸ View the example SI (PDF))
        raise RuntimeError(f"missing: {resources.example_pdf()}")
    project = Project.open(folder, read_only=True)
    try:
        compounds = project.all_compounds()
        if not compounds:
            raise RuntimeError("the example project has no compounds")
        drawn = [c for c in compounds if project.read_structure(c) is not None]
        if len(drawn) != len(compounds):
            raise RuntimeError(f"only {len(drawn)} of {len(compounds)} structures could "
                               "be read")
        chem.mol_info(project.read_structure(compounds[0]))
        spectra = sum(len(c.values.get("spectra") or []) for c in compounds)
        problems = project.load_warnings
    finally:
        project.close()
    if problems:
        raise RuntimeError("opened with warnings: " + "; ".join(problems))
    return f"{len(compounds)} compounds, {spectra} spectra in {folder}"


def _module_shipped(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _compile(folder: Path) -> str:
    from .compile.document import CompileOptions
    from .compile.qt_images import content_box, convert_to_png, image_info
    from .compile.run import compile_pdf
    from .core import lock as lockmod
    from .core.project import Project
    # Opened read-only, without taking the lock: compiling only writes build/, and someone
    # who has the project open in the program keeps it (their window is not taken over).
    project = Project.open(folder, read_only=True)
    try:
        options = CompileOptions.from_settings(project.settings)
        result = compile_pdf(project, options, image_info, convert_to_png, crop=content_box)
    finally:
        project.close()
    from pypdf import PdfReader
    pages = len(PdfReader(str(result.pdf)).pages)
    if pages < 1:
        raise RuntimeError("the PDF has no pages")
    held = lockmod.read_lock(folder)
    note = f"; {held.describe()}, so it was compiled without taking it over" if held else ""
    return f"{result.pdf} ({pages} pages){note}"


def run(report: Path, project: Path | None = None) -> int:
    """Run every check, write `report` (JSON) and return the exit code (0 = all passed)."""
    checks: list[tuple[str, Callable[[], str]]] = [
        ("files", _files), ("python", _python), ("spelling", _spelling),
        ("rdkit", _rdkit), ("typst", _typst), ("pdf", _pdf), ("qt", _qt),
        ("example", _example)]
    if project is not None:
        checks.append(("compile", lambda: _compile(project)))
    results = {}
    for name, check in checks:
        start = time.perf_counter()
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as e:  # noqa: BLE001 — every problem is reported, none stops it
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}",
                             "traceback": traceback.format_exc()}
        results[name]["seconds"] = round(time.perf_counter() - start, 2)
    ok = all(r["ok"] for r in results.values())
    data = {"ok": ok, "program": f"{APP_NAME} {__version__}", "frozen": resources.is_frozen(),
            "executable": sys.executable, "checks": results}
    try:
        Path(report).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1
