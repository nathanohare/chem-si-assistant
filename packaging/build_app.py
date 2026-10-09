"""Building the installed program (SPEC §13m.2, Phase 8).

Used by the release workflow (.github/workflows/release.yml) and by hand:

    python packaging/build_app.py check-version --tag v0.1.0
    python packaging/build_app.py build --dist <folder> --work <folder>
    python packaging/build_app.py verify --app <built program folder>
    python packaging/build_app.py smoke --exe <built program>
    python packaging/build_app.py self-test --exe <built program> --report r.json [--project P]
    python packaging/build_app.py notes --version 0.1.0 --out notes.md
    python packaging/build_app.py asset-name --platform windows
    python packaging/build_app.py appdir --app <built folder> --out <AppDir>

`build` runs PyInstaller with packaging/chem_si_assistant.spec: one folder holding the
program and everything it needs (Python, Qt, RDKit, Typst, the journal styles, word lists,
the user guide, the licences). The installers are then made from that folder:
Inno Setup on Windows (packaging/windows/installer.iss), a .dmg on macOS, an AppImage on
Linux (the workflow does those steps).

The functions here that don't build anything are unit-tested (tests/test_packaging.py).
Nothing here needs Qt except `make_icons`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "si_assistant"
SPEC = Path(__file__).resolve().parent / "chem_si_assistant.spec"
APP_NAME = "Chem SI Assistant"
BUNDLE_ID = "io.github.nathanohare.chemsiassistant"
LINUX_NAME = "chem-si-assistant"
# The Windows taskbar groups windows by this ID (ui/app_icon.py sets the same one).
WINDOWS_APP_ID = "ChemSIAssistant.App"
# Tags: v1.2.3, or a pre-release such as v1.2.0-beta.1.
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


# ---------------------------------------------------------------------- names and versions

def read_version(root: Path = ROOT) -> str:
    """si_assistant.__version__, read from the file (without importing the program)."""
    text = (root / "si_assistant" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise ValueError("no __version__ in si_assistant/__init__.py")
    return m.group(1)


def check_tag(tag: str, version: str) -> str | None:
    """None if the release tag fits the program's version, else what is wrong."""
    if not TAG.match(tag or ""):
        return f"The tag {tag!r} is not a version tag like v1.2.3 or v1.2.0-beta.1."
    if tag[1:] != version:
        return (f"The tag {tag} does not match the program's version {version} "
                "(si_assistant/__init__.py): change one so they agree.")
    return None


def is_prerelease(tag: str) -> bool:
    """v1.2.0-beta.1 is a pre-release; v1.2.0 is not."""
    m = TAG.match(tag or "")
    return bool(m and m.group(4))


def numeric_version(version: str) -> tuple[int, int, int, int]:
    """1.2.3-beta.1 → (1, 2, 3, 0): the four numbers Windows keeps in a program's details."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        raise ValueError(f"not a version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0


def asset_name(key: str, version: str) -> str:
    """The installer's file name on the GitHub Release, as the update check expects it
    (si_assistant/updates.py: ASSET_PATTERNS)."""
    sys.path.insert(0, str(ROOT))
    try:
        from si_assistant.updates import ASSET_PATTERNS
    finally:
        sys.path.pop(0)
    return ASSET_PATTERNS[key].replace("*", version)


def app_name(platform: str = sys.platform) -> str:
    """The built program's name: "Chem SI Assistant" (.exe / .app), "chem-si-assistant"
    on Linux (no spaces in a command)."""
    return LINUX_NAME if platform.startswith("linux") else APP_NAME


def executable(app_dir: Path, platform: str = sys.platform) -> Path:
    """The program file inside a built folder (`dist/<name>`, or the .app on macOS)."""
    app_dir = Path(app_dir)
    if platform.startswith("win"):
        return app_dir / f"{APP_NAME}.exe"
    if platform == "darwin" and app_dir.suffix == ".app":
        return app_dir / "Contents" / "MacOS" / APP_NAME
    return app_dir / app_name(platform)


def release_notes(changelog: str, version: str) -> str:
    """The section of CHANGELOG.md for `version` (its text under "## [x.y.z] …" or
    "## x.y.z …", up to the next "## " heading). Raises ValueError if there is none."""
    lines = changelog.splitlines()
    head = re.compile(rf"^##\s+\[?v?{re.escape(version)}\]?(\s|$)")
    for i, line in enumerate(lines):
        if head.match(line):
            body = []
            for other in lines[i + 1:]:
                if other.startswith("## "):
                    break
                body.append(other)
            text = "\n".join(body).strip()
            if text:
                return text + "\n"
    raise ValueError(f"CHANGELOG.md has no section for version {version}")


# ---------------------------------------------------------------------- what goes in

def data_files(root: Path = ROOT) -> list[tuple[str, str]]:
    """(source, folder in the program) for every file the program needs besides code, in
    PyInstaller's `datas` form. The same layout as the source code (see
    si_assistant/resources.py)."""
    pkg = root / "si_assistant"
    pairs = [(str(pkg / "styles" / "*.typ"), "si_assistant/styles"),
             (str(pkg / "data" / "*.txt"), "si_assistant/data"),
             (str(pkg / "icons" / "*"), "si_assistant/icons"),
             # The example project (Help ▸ Open example project…): a whole folder, copied
             # as it is (tools/make_example_project.py makes it).
             (str(pkg / "example"), "si_assistant/example"),
             (str(root / "LICENSE"), "."),
             (str(root / "THIRD_PARTY_NOTICES.md"), "."),
             (str(root / "docs" / "user-guide.md"), "docs")]
    if any((root / "docs" / "images").glob("*.png")):
        pairs.append((str(root / "docs" / "images" / "*.png"), "docs/images"))
    return pairs


# Qt parts PyInstaller would bring along that the program never uses. The program uses
# only QtCore, QtGui, QtWidgets, QtSvg, QtPdf and QtPdfWidgets. Qt WebEngine (a whole
# web browser, ~200 MB) is left out since the structure editor that needed it was removed
# (SPEC §13n.2). Python modules not to collect…
QT_EXCLUDES = ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
               "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.Qt3DCore",
               "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtGraphs",
               "PySide6.QtMultimedia", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest",
               "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
               "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
               "PySide6.QtWebView", "PySide6.QtPositioning"]
# …and files not to ship (paths inside the program, with "/"), in case another part
# brings them along anyway:
#  * everything of Qt WebEngine: its libraries, helper program, data and languages;
#  * Qt's translations of its own dialogs (the program's interface is English);
#  * QML plugins (the program has no QML).
# (Qt's folders are PySide6/… on Windows, PySide6/Qt/… on macOS and Linux; on macOS Qt
# WebEngine is QtWebEngineCore.framework.)
EXAMPLE_LEFTOVERS = re.compile(
    r"(^|/)si_assistant/example/(.*/)?((build|trash|snapshots)/|[^/]*\.lock$|\.tmp-[^/]*$)")
_DROP = (re.compile(r"(^|/)(lib)?Qt6?(WebEngine|WebChannel|WebSockets|Positioning)[^/]*$"),
         re.compile(r"(^|/)Qt(WebEngine|WebChannel|Positioning)[^/]*\.framework/"),
         re.compile(r"(^|/)QtWebEngineProcess[^/]*$"),
         re.compile(r"(^|/)qtwebengine_[^/]*$"),
         re.compile(r"(^|/)qtwebengine_locales/"),
         re.compile(r"(^|/)PySide6/(Qt/)?resources/(icudtl\.dat|v8_context_snapshot[^/]*)$"),
         re.compile(r"(^|/)PySide6/(Qt/)?translations/[^/]+\.qm$"),
         re.compile(r"(^|/)PySide6/(Qt/)?qml/"),
         re.compile(r"(^|/)PySide6/(Qt/)?plugins/qmltooling/"),
         # What a program that had the example project open leaves in it (the build/
         # folder with the undo history, snapshots, Trash, the lock, half-written files):
         # never shipped (resources.NOT_COPIED; the user's copy leaves them out too).
         EXAMPLE_LEFTOVERS)


def keep_in_bundle(dest: str) -> bool:
    """Whether a file PyInstaller collected (its path inside the program) is shipped."""
    dest = dest.replace("\\", "/")
    return not any(p.search(dest) for p in _DROP)


def missing_sources(root: Path = ROOT) -> list[str]:
    """What must exist before building (all of it is kept with the code)."""
    needed = [root / "LICENSE", root / "THIRD_PARTY_NOTICES.md",
              root / "docs" / "user-guide.md", root / "si_assistant" / "icons" / "app_icon.ico",
              root / "si_assistant" / "icons" / "app_icon.svg"]
    return [str(p.relative_to(root)) for p in needed if not p.exists()]


def windows_version_file(version: str) -> str:
    """The version details of the Windows .exe (PyInstaller's version-file format)."""
    nums = numeric_version(version)
    fields = {"CompanyName": "Chem SI Assistant contributors",
              "FileDescription": APP_NAME, "FileVersion": version,
              "InternalName": APP_NAME, "LegalCopyright": "GPL-3.0-or-later",
              "OriginalFilename": f"{APP_NAME}.exe", "ProductName": APP_NAME,
              "ProductVersion": version}
    strings = ",\n            ".join(f"StringStruct('{k}', '{v}')" for k, v in fields.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
            {strings}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def info_plist(version: str) -> dict:
    """Extra Info.plist entries of the macOS app."""
    return {"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": ".".join(str(n) for n in numeric_version(version)[:3]),
            "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "GPL-3.0-or-later",
            "LSApplicationCategoryType": "public.app-category.education"}


# ---------------------------------------------------------------------- icons

# The PNG sizes of a macOS .icns file, by the type code each is stored under.
ICNS_TYPES = (("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256),
              ("ic09", 512), ("ic10", 1024))


def icns_bytes(pngs: dict[int, bytes]) -> bytes:
    """A macOS .icns file holding PNG pictures (`pngs`: size → PNG data); sizes without a
    picture are left out."""
    chunks = b""
    for code, size in ICNS_TYPES:
        data = pngs.get(size)
        if data:
            chunks += code.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
    return b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks


def make_icons(out: Path) -> dict[str, Path]:
    """Render si_assistant/icons/app_icon.svg (the program's own icon) to the files the
    installers need: app_icon.icns (macOS) and app_icon_512.png (Linux). The Windows .ico
    is kept with the code (si_assistant/icons/app_icon.ico). Needs Qt (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(PACKAGE / "icons" / "app_icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("app_icon.svg could not be read")
    pngs = {}
    for _code, size in ICNS_TYPES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        pngs[size] = bytes(data)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {"icns": out / "app_icon.icns", "png": out / "app_icon_512.png",
             "ico": PACKAGE / "icons" / "app_icon.ico"}
    files["icns"].write_bytes(icns_bytes(pngs))
    files["png"].write_bytes(pngs[512])
    return files


# ---------------------------------------------------------------------- building

def build_path(path: str, prefix: str = sys.prefix, platform: str = sys.platform) -> str:
    """PATH for PyInstaller. In an Anaconda environment on Windows, Python's own DLLs
    (OpenSSL, expat, libffi, sqlite, lzma, bz2) are in <env>\\Library\\bin, which
    PyInstaller only searches when it is on PATH; without them the program could not use
    https, XML or ctypes. (The release workflow uses python.org's Python: no change.)"""
    library = Path(prefix) / "Library" / "bin"
    if platform.startswith("win") and (Path(prefix) / "conda-meta").is_dir() \
            and library.is_dir():
        return str(library) + os.pathsep + path
    return path


def build(dist: Path, work: Path) -> Path:
    """Run PyInstaller; returns the built program's folder (or the .app on macOS)."""
    missing = missing_sources()
    if missing:
        raise SystemExit("Can't build yet, missing: " + ", ".join(missing))
    # Absolute: PyInstaller reads relative paths in the spec from the spec's own folder.
    work, dist = Path(work).resolve(), Path(dist).resolve()
    icons = make_icons(work / "icons")
    version = read_version()
    version_file = work / "version_info.txt"
    work.mkdir(parents=True, exist_ok=True)
    version_file.write_text(windows_version_file(version), encoding="utf-8")
    env = dict(os.environ, CSA_ICNS=str(icons["icns"]), CSA_VERSION_FILE=str(version_file))
    env["PATH"] = build_path(env.get("PATH", ""))
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean",
           "--distpath", str(dist), "--workpath", str(work / "pyinstaller")]
    subprocess.run(cmd, check=True, env=env, cwd=str(ROOT))
    built = Path(dist) / (f"{APP_NAME}.app" if sys.platform == "darwin" else app_name())
    print(f"Built {built}")
    return built


def bundle_data_dir(app_dir: Path, platform: str = sys.platform) -> Path:
    """Where PyInstaller put the data files inside a built program (its `sys._MEIPASS`)."""
    app_dir = Path(app_dir)
    if platform == "darwin" and app_dir.suffix == ".app":
        return app_dir / "Contents" / "Resources"
    return app_dir / "_internal"


# The compiled parts of 'cryptography' and its helper cffi in a built program (".pyd" on
# Windows, ".so" elsewhere), and their licence texts: Apache-2.0 asks for the licence to
# be passed on with the program (the spec copies their package information folders).
CRYPTOGRAPHY_FILES = ("cryptography/hazmat/bindings/_rust*", "_cffi_backend*",
                      "cryptography-*.dist-info/**/LICENSE.APACHE",
                      "cryptography-*.dist-info/**/LICENSE.BSD",
                      "cffi-*.dist-info/**/LICENSE")


def verify(app_dir: Path, platform: str = sys.platform) -> list[str]:
    """What is missing from a built program, or should not be in it (empty when all is
    well)."""
    data = bundle_data_dir(app_dir, platform)
    needed = [data / "si_assistant" / "styles" / "acs.typ",
              data / "si_assistant" / "data" / "azman_chemistry_dictionary.txt",
              data / "si_assistant" / "icons" / "app_icon_256.png",
              data / "si_assistant" / "example" / "Example project" / "project.json",
              data / "spellchecker" / "resources" / "en.json.gz",
              data / "rdkit" / "Data" / "Fonts",
              data / "docs" / "user-guide.md", data / "LICENSE",
              data / "THIRD_PARTY_NOTICES.md", executable(app_dir, platform)]
    missing = [str(p) for p in needed if not p.exists()]
    # The 'cryptography' package (compiled part and licence), which pypdf needs to read
    # PDFs saved with edit restrictions; pypdf does without it silently, so check it came
    # along.
    places = [data, Path(app_dir) / "Contents" / "Frameworks"] \
        if platform == "darwin" else [data]
    for pattern in CRYPTOGRAPHY_FILES:
        if not any(any(place.glob(pattern)) for place in places if place.is_dir()):
            missing.append(str(data / pattern))
    # The example project as tools/make_example_project.py makes it, nothing more.
    example = data / "si_assistant" / "example"
    if example.is_dir():
        for found in sorted(example.rglob("*")):
            rel = "si_assistant/example/" + found.relative_to(example).as_posix()
            if EXAMPLE_LEFTOVERS.search(rel + ("/" if found.is_dir() else "")):
                missing.append(f"should not be there: {found}")
                break
    # Qt WebEngine is no longer used (SPEC §13n.2): it must not have come along.
    for found in Path(app_dir).rglob("*WebEngine*"):
        missing.append(f"should not be there: {found}")
        break
    return missing


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def smoke(exe: Path, timeout: int = 180) -> int:
    """Start the built program in start-up test mode (it opens its window and quits after
    1.5 s); returns its exit code (0 = it started)."""
    env = dict(os.environ, SI_ASSISTANT_SMOKE_TEST="1")
    return subprocess.run([str(exe)], env=env, timeout=timeout).returncode


def self_test(exe: Path, report: Path, project: Path | None = None, timeout: int = 600) -> int:
    """Run the built program's self-test (si_assistant/selftest.py) and print its report."""
    cmd = [str(exe), "--self-test", str(report)] + ([str(project)] if project else [])
    code = subprocess.run(cmd, timeout=timeout).returncode
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
        for name, r in data["checks"].items():
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {name}: {r['detail']}")
    except (OSError, ValueError, KeyError) as e:
        print(f"No self-test report ({e})")
        return code or 1
    return code


# ---------------------------------------------------------------------- Linux AppImage

DESKTOP_FILE = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Build the Supporting Information of a chemistry paper
Exec={LINUX_NAME} %F
Icon={LINUX_NAME}
Categories=Science;Chemistry;Education;
Terminal=false
StartupWMClass={APP_NAME}
"""

APP_RUN = f"""#!/bin/sh
# Starts {APP_NAME} from inside the AppImage.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/{LINUX_NAME}/{LINUX_NAME}" "$@"
"""


def write_appdir(app_dir: Path, out: Path, icon_png: Path) -> Path:
    """The AppDir that appimagetool packs into the AppImage: the built program in
    usr/lib/chem-si-assistant/, AppRun, the .desktop file and the icon."""
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    lib = out / "usr" / "lib" / LINUX_NAME
    shutil.copytree(app_dir, lib, symlinks=True)
    (out / "AppRun").write_text(APP_RUN, encoding="utf-8", newline="\n")
    (out / "AppRun").chmod(0o755)
    (out / f"{LINUX_NAME}.desktop").write_text(DESKTOP_FILE, encoding="utf-8", newline="\n")
    icons = out / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_png, icons / f"{LINUX_NAME}.png")
    shutil.copy2(icon_png, out / f"{LINUX_NAME}.png")
    shutil.copy2(icon_png, out / ".DirIcon")
    return out


# ---------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Chem SI Assistant's installers.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-version", help="the release tag must match __version__")
    p.add_argument("--tag", required=True)
    p = sub.add_parser("version", help="print the program's version")
    p = sub.add_parser("notes", help="the CHANGELOG.md section of a version")
    p.add_argument("--version", default=None)
    p.add_argument("--out", required=True)
    p = sub.add_parser("asset-name", help="an installer's file name on the Release")
    p.add_argument("--platform", required=True, choices=["windows", "macos", "linux"])
    p.add_argument("--version", default=None)
    p = sub.add_parser("icons", help="make the .icns and the Linux PNG")
    p.add_argument("--out", required=True)
    p = sub.add_parser("build", help="run PyInstaller")
    p.add_argument("--dist", required=True)
    p.add_argument("--work", required=True)
    p = sub.add_parser("verify", help="check a built program is complete")
    p.add_argument("--app", required=True)
    p = sub.add_parser("smoke", help="start the built program and check it starts")
    p.add_argument("--exe", required=True)
    p = sub.add_parser("self-test", help="the built program's self-test")
    p.add_argument("--exe", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--project", default=None)
    p = sub.add_parser("appdir", help="make the AppDir for appimagetool (Linux)")
    p.add_argument("--app", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--icon", required=True)
    args = parser.parse_args(argv)

    if args.command == "check-version":
        problem = check_tag(args.tag, read_version())
        if problem:
            print(problem, file=sys.stderr)
            return 1
        print(f"{args.tag} matches the program's version"
              + (" (pre-release)" if is_prerelease(args.tag) else ""))
        return 0
    if args.command == "version":
        print(read_version())
        return 0
    if args.command == "notes":
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        Path(args.out).write_text(release_notes(text, args.version or read_version()),
                                  encoding="utf-8")
        return 0
    if args.command == "asset-name":
        print(asset_name(args.platform, args.version or read_version()))
        return 0
    if args.command == "icons":
        for kind, path in make_icons(Path(args.out)).items():
            print(f"{kind}: {path}")
        return 0
    if args.command == "build":
        built = build(Path(args.dist), Path(args.work))
        print(f"Size: {folder_size(built) / 1e6:.0f} MB")
        return 0
    if args.command == "verify":
        missing = verify(Path(args.app))
        for m in missing:
            print(m if m.startswith("should not") else f"missing: {m}", file=sys.stderr)
        print("complete" if not missing else f"{len(missing)} problem(s)")
        return 1 if missing else 0
    if args.command == "smoke":
        code = smoke(Path(args.exe))
        print(f"start-up test: exit code {code}")
        return code
    if args.command == "self-test":
        return self_test(Path(args.exe), Path(args.report),
                         Path(args.project) if args.project else None)
    if args.command == "appdir":
        print(write_appdir(Path(args.app), Path(args.out), Path(args.icon)))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
