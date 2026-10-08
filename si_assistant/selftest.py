"""The installed program's self-test (SPEC §13m, Phase 8).

    Chem-SI-Assistant --self-test REPORT.json [PROJECT FOLDER]

Checks that everything the program needs came with it — journal styles, word lists and
dictionaries, icons, the user guide and licences, RDKit (structures and drawings), Typst,
Qt's SVG and PDF parts (and that Qt WebEngine, no longer used, was left out) — and, given
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
        ("files", _files), ("python", _python), ("spelling", _spelling),
        ("rdkit", _rdkit), ("typst", _typst), ("qt", _qt)]
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
