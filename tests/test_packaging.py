"""Packaging (SPEC §13m.2, Phase 8): the parts of packaging/build_app.py that don't build
anything, the resource paths of an installed program (si_assistant/resources.py), the
self-test's command line, Help ▸ User guide, and the release workflow's file names.
Nothing here runs PyInstaller or makes an installer."""

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import pytest

from si_assistant import __version__, resources, selftest, updates

ROOT = Path(__file__).resolve().parent.parent


def load_build_app():
    spec = importlib.util.spec_from_file_location("build_app", ROOT / "packaging" / "build_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load_build_app()


# ---------------------------------------------------------------------- versions and names

def test_version_is_read_without_importing_the_program():
    assert B.read_version() == __version__


@pytest.mark.parametrize("tag, ok", [
    (f"v{__version__}", True), (__version__, False), ("v9.9.9", False), ("v1.2", False),
    ("release-1", False), ("", False)])
def test_the_tag_must_match_the_version(tag, ok):
    assert (B.check_tag(tag, __version__) is None) == ok


def test_pre_releases_and_windows_numbers():
    assert B.check_tag("v1.2.0-beta.1", "1.2.0-beta.1") is None
    assert B.is_prerelease("v1.2.0-beta.1") and not B.is_prerelease("v1.2.0")
    assert B.numeric_version("1.2.3-beta.1") == (1, 2, 3, 0)


def test_asset_names_are_what_the_update_check_looks_for():
    import fnmatch
    for key, pattern in updates.ASSET_PATTERNS.items():
        name = B.asset_name(key, "1.2.3")
        assert fnmatch.fnmatchcase(name, pattern) and "1.2.3" in name
    assert B.asset_name("windows", "0.1.0") == "Chem-SI-Assistant-0.1.0-Windows-Setup.exe"
    assert B.asset_name("macos", "0.1.0") == "Chem-SI-Assistant-0.1.0-macOS.dmg"
    assert B.asset_name("linux", "0.1.0") == "Chem-SI-Assistant-0.1.0-x86_64.AppImage"


def test_the_release_workflow_and_installer_use_the_same_names():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*.*.*"]' in workflow and "workflow_dispatch" in workflow
    assert "check-version" in workflow and "ubuntu-22.04" in workflow
    assert "asset-name" in workflow                     # names come from updates.py
    iss = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "OutputBaseFilename=Chem-SI-Assistant-{#AppVersion}-Windows-Setup" in iss
    assert "PrivilegesRequired=lowest" in iss and "LicenseFile=..\\..\\LICENSE" in iss
    assert resources.RUNNING_MUTEX in iss                 # waits for the running program
    assert all(ord(c) < 128 for c in iss)                 # (Inno reads it as ANSI)
    app_id = re.search(r"AppId=\{\{([0-9A-F-]{36})\}", iss)
    assert app_id   # fixed, so an update replaces the old installation


def test_app_names_and_executables():
    assert B.app_name("win32") == B.app_name("darwin") == "Chem SI Assistant"
    assert B.app_name("linux") == "chem-si-assistant"
    assert B.executable(Path("d/Chem SI Assistant"), "win32").name == "Chem SI Assistant.exe"
    mac = B.executable(Path("d/Chem SI Assistant.app"), "darwin")
    assert mac.parts[-3:] == ("Contents", "MacOS", "Chem SI Assistant")
    assert B.executable(Path("d/chem-si-assistant"), "linux").name == "chem-si-assistant"
    assert B.bundle_data_dir(Path("x.app"), "darwin").parts[-2:] == ("Contents", "Resources")
    assert B.bundle_data_dir(Path("x"), "win32").name == "_internal"


CHANGELOG = """# Changelog

## [1.1.0] - next
- New: something.

## [1.0.0] - 2026-11-01
- First.
- Second.

## 0.9.0
- Old.
"""


def test_release_notes_come_from_the_changelog():
    assert B.release_notes(CHANGELOG, "1.0.0") == "- First.\n- Second.\n"
    assert B.release_notes(CHANGELOG, "1.1.0") == "- New: something.\n"
    assert B.release_notes(CHANGELOG, "0.9.0") == "- Old.\n"
    with pytest.raises(ValueError):
        B.release_notes(CHANGELOG, "2.0.0")


def test_the_changelog_has_this_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert len(B.release_notes(text, __version__)) > 200


# ---------------------------------------------------------------------- what goes in

def test_data_files_cover_what_the_program_reads():
    pairs = B.data_files()
    dests = {d for _s, d in pairs}
    assert {"si_assistant/styles", "si_assistant/data", "si_assistant/icons",
            "si_assistant/vendor/ketcher", ".", "docs"} <= dests
    sources = [Path(s) for s, _d in pairs]
    assert ROOT / "LICENSE" in sources and ROOT / "THIRD_PARTY_NOTICES.md" in sources
    assert ROOT / "docs" / "user-guide.md" in sources
    # The layout matches what resources.py looks for.
    assert resources.resource("styles").relative_to(resources.bundle_root()).as_posix() \
        == "si_assistant/styles"


@pytest.mark.parametrize("dest, kept", [
    ("PySide6/resources/qtwebengine_resources.pak", True),
    ("PySide6/resources/qtwebengine_devtools_resources.pak", False),
    ("PySide6/resources/qtwebengine_resources.debug.pak", False),
    ("PySide6/resources/v8_context_snapshot.debug.bin", False),
    ("PySide6/resources/v8_context_snapshot.bin", True),
    ("PySide6\\translations\\qtwebengine_locales\\en-US.pak", True),
    ("PySide6/translations/qtwebengine_locales/de.pak", False),
    ("PySide6/Qt/lib/QtWebEngineCore.framework/Resources/qtwebengine_locales/fr.pak", False),
    ("PySide6/translations/qtbase_de.qm", False),
    ("PySide6/Qt/qml/QtQuick/Controls/qmldir", False),
    ("PySide6/plugins/platforms/qwindows.dll", True),
    ("PySide6/Qt6WebEngineCore.dll", True),
    ("si_assistant/vendor/ketcher/index.html", True),
    ("spellchecker/resources/en.json.gz", True),
])
def test_unused_qt_files_are_left_out(dest, kept):
    assert B.keep_in_bundle(dest) == kept


def test_conda_dlls_are_found_when_building(tmp_path):
    (tmp_path / "conda-meta").mkdir()
    (tmp_path / "Library" / "bin").mkdir(parents=True)
    path = B.build_path("C:\\Windows", str(tmp_path), "win32")
    assert path.split(";" if sys.platform == "win32" else ":")[0] == str(tmp_path / "Library" / "bin")
    assert B.build_path("C:\\Windows", str(tmp_path), "darwin") == "C:\\Windows"
    assert B.build_path("x", str(tmp_path / "plain"), "win32") == "x"


def test_windows_version_details_and_mac_info():
    text = B.windows_version_file("1.2.3-beta.1")
    assert "filevers=(1, 2, 3, 0)" in text and "StringStruct('ProductVersion', '1.2.3-beta.1')" in text
    plist = B.info_plist("1.2.3")
    assert plist["CFBundleShortVersionString"] == "1.2.3" and plist["CFBundleVersion"] == "1.2.3"


def test_icns_file_holds_the_pngs():
    data = B.icns_bytes({16: b"\x89PNG16", 512: b"\x89PNG512", 99: b"ignored"})
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    assert data[8:12] == b"icp4" and struct.unpack(">I", data[12:16])[0] == 8 + 6
    assert b"ic09" in data and b"ignored" not in data


def test_icons_are_made_from_the_programs_svg(qapp, tmp_path):
    files = B.make_icons(tmp_path)
    icns = files["icns"].read_bytes()
    assert icns[:4] == b"icns" and icns.count(b"\x89PNG") == len(B.ICNS_TYPES)
    assert files["png"].read_bytes().startswith(b"\x89PNG") and files["ico"].exists()


def test_appdir_for_the_appimage(tmp_path):
    app = tmp_path / "chem-si-assistant"
    (app / "_internal").mkdir(parents=True)
    (app / "chem-si-assistant").write_text("binary")
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"\x89PNG")
    out = B.write_appdir(app, tmp_path / "AppDir", icon)
    run = (out / "AppRun").read_text(encoding="utf-8")
    assert run.startswith("#!/bin/sh") and "usr/lib/chem-si-assistant/chem-si-assistant" in run
    desktop = (out / "chem-si-assistant.desktop").read_text(encoding="utf-8")
    assert "Icon=chem-si-assistant" in desktop and "Exec=chem-si-assistant" in desktop
    assert (out / "chem-si-assistant.png").exists() and (out / ".DirIcon").exists()
    assert (out / "usr" / "lib" / "chem-si-assistant" / "chem-si-assistant").exists()


def test_verify_lists_what_is_missing(tmp_path):
    missing = B.verify(tmp_path / "nothing", "win32")
    assert any("ketcher" in m for m in missing) and "QtWebEngineProcess.exe" in missing


def test_the_build_needs_ketcher_and_the_guide(tmp_path):
    # Everything but Ketcher (downloaded before building) is kept with the code.
    assert all("ketcher" in m for m in B.missing_sources(ROOT))
    assert len(B.missing_sources(tmp_path)) == 6


def test_command_line_without_building(tmp_path, capsys):
    assert B.main(["check-version", "--tag", f"v{__version__}"]) == 0
    assert B.main(["check-version", "--tag", "v99.0.0"]) == 1
    assert B.main(["notes", "--out", str(tmp_path / "n.md")]) == 0
    assert (tmp_path / "n.md").read_text(encoding="utf-8").strip()
    capsys.readouterr()
    assert B.main(["asset-name", "--platform", "linux", "--version", "2.0.0"]) == 0
    assert capsys.readouterr().out.strip() == "Chem-SI-Assistant-2.0.0-x86_64.AppImage"


# ---------------------------------------------------------------------- resources.py

def test_resources_from_the_source_code():
    assert not resources.is_frozen()
    assert resources.bundle_root() == ROOT
    assert resources.resource("styles", "acs.typ").is_file()
    assert resources.top_level("LICENSE").is_file()
    assert resources.user_guide() == ROOT / "docs" / "user-guide.md"


def test_resources_in_an_installed_program(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.is_frozen() and resources.bundle_root() == tmp_path
    assert resources.resource("data", "x.txt") == tmp_path / "si_assistant" / "data" / "x.txt"
    assert resources.user_guide() == tmp_path / "docs" / "user-guide.md"


def test_ketcher_downloads_elsewhere_when_the_installed_copy_is_missing(monkeypatch, tmp_path):
    from si_assistant import ketcher_setup
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(ketcher_setup, "KETCHER_DIR", tmp_path / "si_assistant" / "vendor" / "ketcher")
    monkeypatch.setattr(resources, "user_data_dir", lambda: tmp_path / "user")
    assert ketcher_setup.ketcher_dir() == tmp_path / "user" / "ketcher"   # (not bundled)
    bundled = tmp_path / "si_assistant" / "vendor" / "ketcher"
    bundled.mkdir(parents=True)
    (bundled / "index.html").write_text("<html>")
    assert ketcher_setup.ketcher_dir() == bundled and ketcher_setup.installed()


def test_user_data_folder_per_system(tmp_path):
    home = tmp_path
    assert resources.user_data_dir({"LOCALAPPDATA": "L"}, "win32", home) == Path("L") / "Chem SI Assistant"
    assert resources.user_data_dir({}, "darwin", home) == \
        home / "Library" / "Application Support" / "Chem SI Assistant"
    assert resources.user_data_dir({}, "linux", home) == home / ".local" / "share" / "chem-si-assistant"
    assert resources.user_data_dir({"XDG_DATA_HOME": "X"}, "linux", home) == Path("X") / "chem-si-assistant"


@pytest.mark.parametrize("files, needed", [
    ({"/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1"}, True),   # Ubuntu 24.04
    ({"/proc/sys/kernel/unprivileged_userns_clone": "0"}, True),
    ({"/proc/sys/user/max_user_namespaces": "0"}, True),
    ({"/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "0",
      "/proc/sys/user/max_user_namespaces": "63000"}, False),
    ({}, False),
])
def test_webengine_sandbox_off_only_where_linux_forbids_it(files, needed):
    read = lambda p: files.get(p, "")   # noqa: E731
    assert resources.webengine_needs_no_sandbox("linux", read) == needed
    assert resources.webengine_needs_no_sandbox("win32", read) is False
    env = {}
    assert resources.prepare_environment(env, "linux", read, frozen=True) == (
        ["QTWEBENGINE_DISABLE_SANDBOX"] if needed else [])
    assert ("QTWEBENGINE_DISABLE_SANDBOX" in env) == needed
    # Never from the source code, and never against the user's own setting.
    env = {}
    assert resources.prepare_environment(env, "linux", read, frozen=False) == []
    env = {"QTWEBENGINE_DISABLE_SANDBOX": "0"}
    resources.prepare_environment(env, "linux", read, frozen=True)
    assert env == {"QTWEBENGINE_DISABLE_SANDBOX": "0"}


def test_running_mutex(monkeypatch):
    monkeypatch.setattr(resources, "_mutex_handle", None)
    assert resources.hold_running_mutex("linux") is False
    if sys.platform == "win32":
        assert resources.hold_running_mutex() is True
        assert resources.hold_running_mutex() is True   # (held once)


def test_restart_starts_the_installed_program_directly(monkeypatch):
    from si_assistant.ui import restart
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args, cwd = restart.restart_command({"folder": "x"})
    assert args[0] == sys.executable and args[1] == restart.RESTORE_ARG and cwd is None


# ---------------------------------------------------------------------- the self-test

@pytest.mark.parametrize("argv, expected", [
    (["proj"], (None, None, ["proj"])),
    (["--self-test", "r.json"], (Path("r.json"), None, [])),
    (["--self-test", "r.json", "P"], (Path("r.json"), Path("P"), [])),
    (["a", "--self-test", "r.json", "--restore", "{}"], (Path("r.json"), None,
                                                         ["a", "--restore", "{}"])),
])
def test_self_test_command_line(argv, expected):
    assert selftest.split_args(argv) == expected


def test_self_test_writes_a_report(monkeypatch, tmp_path):
    monkeypatch.setattr(selftest, "_ketcher_runs", lambda: "skipped in the tests")
    monkeypatch.setattr(selftest, "_qt", lambda: "skipped in the tests")
    report = tmp_path / "r.json"
    code = selftest.run(report)
    data = json.loads(report.read_text(encoding="utf-8"))
    assert set(data["checks"]) == {"files", "python", "ketcher", "spelling", "rdkit", "typst", "qt",
                                   "ketcher-runs"}
    for name in ("files", "python", "spelling", "rdkit", "typst"):
        assert data["checks"][name]["ok"], data["checks"][name]
    assert code == (0 if data["ok"] else 1)
    assert data["frozen"] is False and __version__ in data["program"]


def test_self_test_reports_a_failure(monkeypatch, tmp_path):
    def broken():
        raise RuntimeError("no dictionary")
    for name in ("_files", "_python", "_ketcher", "_rdkit", "_typst", "_qt", "_ketcher_runs"):
        monkeypatch.setattr(selftest, name, lambda: "fine")
    monkeypatch.setattr(selftest, "_spelling", broken)
    report = tmp_path / "r.json"
    assert selftest.run(report) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is False and "no dictionary" in data["checks"]["spelling"]["detail"]
