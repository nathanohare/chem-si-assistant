# PyInstaller recipe for Chem SI Assistant (SPEC §13m.2, Phase 8).
#
# Run through   python packaging/build_app.py build --dist <folder> --work <folder>
# (it first checks the files it needs are there and makes the icons). Builds ONE FOLDER (not one
# file: it starts faster and an installer can update it in place) with a windowed
# program (no console window):
#   Windows: <dist>/Chem SI Assistant/Chem SI Assistant.exe   (+ _internal/)
#   macOS:   <dist>/Chem SI Assistant.app
#   Linux:   <dist>/chem-si-assistant/chem-si-assistant       (+ _internal/)
# The program finds its own files through si_assistant/resources.py.

import os
import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)          # (SPECPATH: this folder, set by PyInstaller)
import build_app as B                 # noqa: E402
from PyInstaller.utils.hooks import collect_data_files  # noqa: E402

version = B.read_version()
name = B.app_name()

datas = B.data_files()
# pyspellchecker's English word list (the other languages are not used).
datas += collect_data_files("spellchecker", includes=["resources/en.json.gz"])
# RDKit's data folder (fonts for structure drawings, functional groups, salts…).
datas += collect_data_files("rdkit", includes=["Data/**/*"])

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(B.ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Imported inside functions or by Qt itself, so PyInstaller may not see them.
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvg", "rdkit.Chem.rdChemReactions",
        "pypdf", "spellchecker",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "PyQt5", "PyQt6", "matplotlib", "pandas", "IPython",
              "jupyter", "notebook", "pytest", "tests", "PIL", "scipy"] + B.QT_EXCLUDES,
    noarchive=False,
)
# Leave out what the program never uses (Qt WebEngine, Qt's translations, QML plugins…;
# see build_app.keep_in_bundle).
a.datas = [d for d in a.datas if B.keep_in_bundle(d[0])]
a.binaries = [b for b in a.binaries if B.keep_in_bundle(b[0])]
pyz = PYZ(a.pure)

windows = sys.platform.startswith("win")
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=name,
    console=False,                    # a windowed program
    disable_windowed_traceback=False,
    upx=False,
    icon=str(B.PACKAGE / "icons" / "app_icon.ico") if windows else None,
    version=os.environ.get("CSA_VERSION_FILE") if windows else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{B.APP_NAME}.app",
        icon=os.environ.get("CSA_ICNS"),
        bundle_identifier=B.BUNDLE_ID,
        version=version,
        info_plist=B.info_plist(version),
    )
