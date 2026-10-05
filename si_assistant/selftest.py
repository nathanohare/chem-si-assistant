"""The installed program's self-test (SPEC §13m, Phase 8).

    Chem-SI-Assistant --self-test REPORT.json [PROJECT FOLDER]

Checks that everything the program needs came with it — journal styles, word lists and
dictionaries, icons, the user guide and licences, Ketcher (and that it really starts in Qt
WebEngine and hands a structure back), RDKit (structures and drawings), Typst, Qt's SVG,
PDF and web parts — and, given a project folder, compiles
that project's SI (to its build/ folder, as Compile SI does). The results are written as
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
    """Python's own libraries the program relies on: https (update check, Crossref, the
    Ketcher download), XML, ctypes (Windows clipboard and metafiles), compression."""
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


def _ketcher() -> str:
    from . import ketcher_setup
    if not ketcher_setup.installed():
        raise RuntimeError(f"Ketcher is missing ({ketcher_setup.index_url_path()})")
    return str(ketcher_setup.index_url_path())


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


def _qt() -> str:
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    import PySide6.QtPdf  # noqa: F401
    import PySide6.QtWebEngineWidgets  # noqa: F401
    svg = QSvgRenderer(QByteArray(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" '
                                  b'height="10"><rect width="10" height="10"/></svg>'))
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    svg.render(painter)
    painter.end()
    if image.pixelColor(5, 5).alpha() == 0:
        raise RuntimeError("Qt SVG drew nothing")
    return "QtSvg, QtPdf, QtWebEngine"


def _wait(condition: Callable[[], bool], seconds: float) -> bool:
    """Let Qt work until `condition()` is true (True) or `seconds` have passed (False)."""
    from PySide6.QtCore import QCoreApplication, QEventLoop
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _ketcher_runs() -> str:
    """Ketcher really starts in Qt WebEngine (its helper program, resources and language
    files all came with the program): load a structure, read it back as a MOL block."""
    from .core import chem
    from .ui.ketcher_dialog import KetcherDialog
    smiles = "CC(=O)N(C)Cc1ccccc1"
    dialog = KetcherDialog(smiles)
    try:
        dialog.show()
        if not _wait(lambda: dialog._ready, 90):
            raise RuntimeError("Ketcher did not start within 90 s")
        molfile, end = "", time.monotonic() + 60
        while time.monotonic() < end:   # (setting the structure takes Ketcher a moment)
            dialog._result = ""
            dialog.status.setText("")
            dialog._fetch()
            _wait(lambda: bool(dialog._result) or "⚠" in dialog.status.text(), 30)
            molfile = dialog._result
            if "V2000" in molfile or "V3000" in molfile:
                try:
                    mol = chem.parse_structure(molfile)
                except chem.ChemError:   # (still empty: try again)
                    mol = None
                if mol is not None and mol.GetNumAtoms() == 12:
                    return f"{smiles} drawn and read back as a MOL block"
            _wait(lambda: False, 0.5)
        raise RuntimeError(f"the structure did not come back ({dialog.status.text()!r})")
    finally:
        dialog.close()
        dialog.deleteLater()


def _compile(folder: Path) -> str:
    from .compile.document import CompileOptions
    from .compile.qt_images import content_box, convert_to_png, image_info
    from .compile.run import compile_pdf
    from .core.project import Project
    project = Project.open(folder)
    try:
        project.acquire_lock("self-test")
        options = CompileOptions.from_settings(project.settings)
        result = compile_pdf(project, options, image_info, convert_to_png, crop=content_box)
    finally:
        project.close()
    from pypdf import PdfReader
    pages = len(PdfReader(str(result.pdf)).pages)
    if pages < 1:
        raise RuntimeError("the PDF has no pages")
    return f"{result.pdf} ({pages} pages)"


def run(report: Path, project: Path | None = None) -> int:
    """Run every check, write `report` (JSON) and return the exit code (0 = all passed)."""
    checks: list[tuple[str, Callable[[], str]]] = [
        ("files", _files), ("python", _python), ("ketcher", _ketcher), ("spelling", _spelling),
        ("rdkit", _rdkit), ("typst", _typst), ("qt", _qt), ("ketcher-runs", _ketcher_runs)]
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
