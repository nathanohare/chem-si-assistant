"""Update check, feedback links, the unexpected-error message and the project format guard
(SPEC §13m.3, §13m.4). Nothing here goes online: the fetchers are fakes."""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from si_assistant import __version__, updates
from si_assistant.updates import Asset, UpdateInfo, parse_version


# ---------------------------------------------------------------------- versions

@pytest.mark.parametrize("text,numbers,pre", [
    ("v1.2.3", (1, 2, 3), ()), ("1.2.3", (1, 2, 3), ()), ("1.2", (1, 2, 0), ()),
    ("v2", (2, 0, 0), ()), ("v1.0.0-beta.1", (1, 0, 0), ("beta", 1)),
    ("1.0.0-RC.2+build.5", (1, 0, 0), ("rc", 2)),
])
def test_parse_version(text, numbers, pre):
    v = parse_version(text)
    assert v is not None and v.numbers == numbers and v.pre == pre


@pytest.mark.parametrize("text", ["", "latest", "v1.x", "1.2.3.4.5", "release-1"])
def test_unreadable_versions(text):
    assert parse_version(text) is None


def test_version_order():
    order = ["0.1.0", "0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta",
             "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1",
             "1.1.0", "1.10.0", "2.0.0"]
    versions = [parse_version(v) for v in order]
    assert versions == sorted(versions)
    for a, b in zip(versions, versions[1:]):
        assert a < b and b > a and a != b
    assert parse_version("v1.0") == parse_version("1.0.0")
    assert updates.is_newer("v1.0.1", "1.0.0") and not updates.is_newer("v1.0.0", "1.0.0")
    assert not updates.is_newer("v0.9.0", "1.0.0") and updates.is_newer("1.0.0", "1.0.0-rc.1")
    assert not updates.is_newer("nonsense", "1.0.0")


# ---------------------------------------------------------------------- the check

def release(tag="v9.0.0", assets=None, body="## What's new\n\n- **Faster** compiling"):
    if assets is None:
        assets = [{"name": f"Chem-SI-Assistant-{tag[1:]}-Windows-Setup.exe", "size": 1234,
                   "browser_download_url": "https://example.invalid/win.exe"},
                  {"name": f"Chem-SI-Assistant-{tag[1:]}-macOS.dmg", "size": 2000,
                   "browser_download_url": "https://example.invalid/mac.dmg"},
                  {"name": f"Chem-SI-Assistant-{tag[1:]}-x86_64.AppImage", "size": 3000,
                   "browser_download_url": "https://example.invalid/linux.AppImage"}]
    return {"tag_name": tag, "name": f"Chem SI Assistant {tag}", "body": body,
            "html_url": f"https://github.com/{updates.GITHUB_REPO}/releases/tag/{tag}",
            "assets": assets}


def fetcher(answer):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        if isinstance(answer, Exception):
            raise answer
        return answer
    fetch.calls = calls
    return fetch


def test_newer_release_found():
    fetch = fetcher(release("v9.0.0"))
    info = updates.check_latest("1.0.0", fetch)
    assert fetch.calls == [(updates.LATEST_RELEASE_API, updates.TIMEOUT_S)]
    assert updates.LATEST_RELEASE_API == ("https://api.github.com/repos/nathanohare/"
                                          "chem-si-assistant/releases/latest")
    assert info.status == "newer" and info.latest == "9.0.0" and info.current == "1.0.0"
    assert "Faster" in info.notes and info.page_url.endswith("/tag/v9.0.0")
    assert [a.size for a in info.assets] == [1234, 2000, 3000]


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.9.0", "v1.0.0-rc.1"])
def test_same_or_older_release(tag):
    assert updates.check_latest("1.0.0", fetcher(release(tag))).status == "up_to_date"


def test_prerelease_user_sees_the_release():
    assert updates.check_latest("1.0.0-beta.2", fetcher(release("v1.0.0"))).status == "newer"


def http_error(code):
    return urllib.error.HTTPError(updates.LATEST_RELEASE_API, code, "x", {}, io.BytesIO(b""))


@pytest.mark.parametrize("error,status", [
    (http_error(404), "not_public"),          # private repository or no release yet
    (http_error(403), "unreachable"),         # e.g. rate limit
    (urllib.error.URLError("offline"), "unreachable"),
    (TimeoutError("timed out"), "unreachable"),
    (OSError("network is down"), "unreachable"),
    (json.JSONDecodeError("bad", "x", 0), "error"),
])
def test_failures_never_raise(error, status):
    assert updates.check_latest("1.0.0", fetcher(error)).status == status


@pytest.mark.parametrize("answer", [[], {"tag_name": "latest"}, {"name": "no tag"}])
def test_strange_answers(answer):
    assert updates.check_latest("1.0.0", fetcher(answer)).status == "error"


def test_real_fetcher_sends_the_agreed_headers(monkeypatch):
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        seen["url"], seen["timeout"] = request.full_url, timeout
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return Response(json.dumps(release()).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    assert updates.fetch_json(updates.LATEST_RELEASE_API)["tag_name"] == "v9.0.0"
    assert seen["timeout"] == updates.TIMEOUT_S == 8
    assert seen["headers"]["user-agent"] == f"Chem-SI-Assistant/{__version__}"
    assert seen["headers"]["accept"] == "application/vnd.github+json"


@pytest.mark.parametrize("system,machine,key,name", [
    ("win32", "AMD64", "windows", "Chem-SI-Assistant-9.0.0-Windows-Setup.exe"),
    ("darwin", "arm64", "macos", "Chem-SI-Assistant-9.0.0-macOS.dmg"),
    ("linux", "x86_64", "linux", "Chem-SI-Assistant-9.0.0-x86_64.AppImage"),
    ("linux", "aarch64", None, None),     # no AppImage for ARM Linux: release page instead
    ("freebsd13", "amd64", None, None),
])
def test_asset_for_each_system(system, machine, key, name):
    info = updates.check_latest("1.0.0", fetcher(release()))
    assert updates.platform_key(system, machine) == key
    asset = updates.pick_asset(info.assets, key)
    assert (asset.name if asset else None) == name


def test_no_matching_asset():
    info = updates.check_latest("1.0.0", fetcher(release(assets=[
        {"name": "Source code.zip", "browser_download_url": "https://example.invalid/s"}])))
    assert updates.pick_asset(info.assets, "windows") is None


def test_automatic_check_is_off_in_tests(monkeypatch):
    assert not updates.automatic_check_allowed()


# ---------------------------------------------------------------------- download

class FakeResponse(io.BytesIO):
    headers = {"Content-Length": "0"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download(tmp_path):
    data = b"x" * 600_000
    seen = []
    path = updates.download("https://example.invalid/f", tmp_path / "setup.exe", len(data),
                            lambda d, t: seen.append((d, t)) or True,
                            opener=lambda req, timeout: FakeResponse(data))
    assert path.read_bytes() == data and seen[-1] == (len(data), len(data))
    assert not list(tmp_path.glob("*.part"))


def test_download_wrong_size_and_cancel(tmp_path):
    with pytest.raises(updates.DownloadError, match="incomplete"):
        updates.download("https://example.invalid/f", tmp_path / "a.exe", 999, opener=lambda r, timeout: FakeResponse(b"x"))
    with pytest.raises(updates.DownloadError, match="cancelled"):
        updates.download("https://example.invalid/f", tmp_path / "b.exe", 0, lambda d, t: False,
                         opener=lambda r, timeout: FakeResponse(b"x" * 10))

    def broken(request, timeout):
        raise urllib.error.URLError("offline")
    with pytest.raises(updates.DownloadError, match="failed"):
        updates.download("https://example.invalid/f", tmp_path / "c.exe", 0, opener=broken)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------- the flow (UI)

class Prefs:
    def __init__(self, **values):
        self.values = dict(values)

    def value(self, key, default=None):
        return self.values.get(key, default)

    def set_value(self, key, value):
        self.values[key] = value


@pytest.fixture
def flow(qapp):
    from PySide6.QtWidgets import QMainWindow
    from si_assistant.ui import update_ui
    window = QMainWindow()
    f = update_ui.UpdateFlow(window, Prefs(), fetch=fetcher(release("v9.0.0")))
    f.asked, f.told = [], []
    f.ask = lambda dialog: f.asked.append(dialog) or update_ui.UpdateDialog.LATER
    f.inform = lambda kind, title, text: f.told.append((kind, title, text))
    yield f
    window.deleteLater()


def info_for(status, latest="9.0.0", assets=()):
    return UpdateInfo(status, "1.0.0", latest, notes="- one\n- two", assets=list(assets))


def test_flow_newer_shows_the_dialog(flow):
    from si_assistant.ui import update_ui
    assert flow.show_result(info_for("newer"), manual=False) == "dialog:later"
    dialog = flow.asked[0]
    assert any("9.0.0" in label.text() for label in dialog.findChildren(update_ui.QLabel))
    assert "one" in dialog.notes.toPlainText()          # markdown shown as text
    assert [b.text() for b in (dialog.install_btn, dialog.later_btn, dialog.skip_btn)] == \
        ["Open the download page", "Remind me later", "Skip this version"]


def test_flow_skip_this_version(flow):
    from si_assistant.ui import update_ui
    flow.ask = lambda dialog: update_ui.UpdateDialog.SKIP
    assert flow.show_result(info_for("newer"), manual=False) == "dialog:skip"
    assert update_ui.skipped_version(flow.prefs) == "9.0.0"
    # Not offered again at start-up, but Help ▸ Check for updates still shows it.
    assert flow.show_result(info_for("newer"), manual=False) == "silent"
    assert flow.show_result(info_for("newer"), manual=True) == "dialog:skip"
    # A later version is offered again.
    assert flow.show_result(info_for("newer", "9.1.0"), manual=False) == "dialog:skip"


@pytest.mark.parametrize("status,said", [("up_to_date", "latest version"),
                                         ("not_public", "until its GitHub project is public"),
                                         ("unreachable", "Couldn't reach GitHub"),
                                         ("error", "Couldn't reach GitHub")])
def test_flow_manual_always_answers_automatic_is_silent(flow, status, said):
    assert flow.show_result(info_for(status), manual=False) == "silent"
    assert flow.told == []
    flow.show_result(info_for(status), manual=True)
    assert said in flow.told[-1][2]


def test_flow_runs_the_check_in_a_thread(flow, qapp):
    flow.check(manual=True)
    deadline = time.monotonic() + 5
    while flow.last is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert flow.last is not None and flow.last.latest == "9.0.0" and flow.asked


def test_install_downloads_and_hands_over_on_windows(flow, tmp_path, monkeypatch, qapp):
    from si_assistant.ui import update_ui
    data = b"installer" * 1000
    monkeypatch.setattr(update_ui, "download_folder", lambda key: tmp_path)
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(data))
    events = []

    class Window(update_ui.QWidget):
        def close_project(self):
            events.append("closed project")
            return True

    info = info_for("newer", assets=[Asset("Chem-SI-Assistant-9.0.0-Windows-Setup.exe",
                                           "https://example.invalid/w.exe", len(data))])
    win = Window()
    up = update_ui.Updater(win, info, key="windows")
    up.launch = lambda path: events.append(("launch", path.name, path.read_bytes() == data))
    up.quit_app = lambda: events.append("quit")
    up.start()
    deadline = time.monotonic() + 5
    while up.result is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert events == ["closed project",
                      ("launch", "Chem-SI-Assistant-9.0.0-Windows-Setup.exe", True), "quit"]
    win.deleteLater()


def test_install_without_a_file_for_this_computer_opens_the_page(qapp):
    from si_assistant.ui import update_ui
    opened = []
    up = update_ui.Updater(None, info_for("newer"), key="linux")
    up.open_url = opened.append
    up.start()
    assert opened == [updates.RELEASES_URL]


def test_linux_download_is_made_executable(qapp, tmp_path, monkeypatch):
    from si_assistant.ui import update_ui
    path = tmp_path / "Chem-SI-Assistant-9.0.0-x86_64.AppImage"
    path.write_bytes(b"x")
    monkeypatch.setattr(update_ui.QMessageBox, "exec", lambda self: 0)
    update_ui.Updater(None, info_for("newer"), key="linux").hand_over(path)
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o111


def test_start_up_check_setting(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from si_assistant.ui import main_window as mw, update_ui
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    win = mw.MainWindow()
    assert win.act_check_updates_at_start.isChecked()          # on by default
    win.act_check_updates_at_start.trigger()
    assert not update_ui.check_at_start(win.prefs)
    assert win.act_check_updates.text() == "Check for updates…"
    assert win.act_feedback.text() == "Send feedback…"
    # Never runs while testing, whatever the setting.
    win.act_check_updates_at_start.trigger()
    assert update_ui.check_at_start(win.prefs) and not win.check_for_updates_at_start()
    win.close()


# ---------------------------------------------------------------------- feedback links

def query(url: str) -> dict:
    parts = urllib.parse.urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == updates.NEW_ISSUE_URL
    return dict(urllib.parse.parse_qsl(parts.query))


@pytest.mark.parametrize("kind,template", [("bug", "bug_report.yml"),
                                           ("feature", "feature_request.yml"),
                                           ("feedback", "feedback.yml")])
def test_issue_url(kind, template):
    url = updates.issue_url(kind, "δ & ¹H: 50% wrong?", version="1.2.3",
                            os_text="Windows 11 (10.0.26200, AMD64)")
    assert " " not in url and "&title=" in url
    q = query(url)
    assert q == {"template": template, "title": "δ & ¹H: 50% wrong?", "version": "1.2.3",
                 "os": "Windows 11 (10.0.26200, AMD64)"}


def test_issue_templates_exist_with_the_prefilled_fields():
    root = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"
    for kind, name in updates.ISSUE_TEMPLATES.items():
        text = (root / name).read_text(encoding="utf-8")
        assert "id: version" in text and "id: os" in text
    assert "id: details" in (root / "bug_report.yml").read_text(encoding="utf-8")
    assert "blank_issues_enabled: false" in (root / "config.yml").read_text(encoding="utf-8")


def test_issue_url_long_details_are_shortened():
    details = "Traceback (most recent call last):\n" + "  File \"x.py\", line 1\n" * 2000 \
        + "ValueError: the real problem"
    url = updates.issue_url("bug", "Error", version="1", os_text="Linux", details=details)
    assert len(url) <= updates.MAX_URL_LENGTH
    got = query(url)["details"]
    assert got.startswith("Traceback") and got.endswith("ValueError: the real problem")
    assert "(shortened)" in got
    short = updates.issue_url("bug", "x", version="1", os_text="Linux", details="tiny")
    assert query(short)["details"] == "tiny"


def test_home_folder_and_project_folder_are_removed(monkeypatch):
    home = r"C:\Users\Jane Doe" if sys.platform == "win32" else "/home/jane"
    monkeypatch.setattr(updates.Path, "home", classmethod(lambda cls: Path(home)))
    sep = "\\" if sys.platform == "win32" else "/"
    project = sep.join([home, "Research", "Secret SI"])
    text = (f'File "{home}{sep}app{sep}x.py", line 3\n'
            f"FileNotFoundError: '{project}{sep}compound.json'\n"
            f"repr: {project.replace(chr(92), chr(92) * 2)}")
    out = updates.scrub_paths(text, {project: "<project folder>"})
    assert "Jane" not in out and "jane" not in out and "Secret" not in out
    assert f'"~{sep}app{sep}x.py"' in out and "<project folder>" in out


def test_error_report_summary_and_details(monkeypatch):
    try:
        raise ValueError("bad value")
    except ValueError as e:
        summary, details = updates.error_report(type(e), e, e.__traceback__)
    assert summary == "ValueError: bad value"
    assert "Traceback" in details and __version__ in details
    assert str(Path.home()) not in details


def test_os_description_is_plain():
    text = updates.os_description()
    assert text.split()[0] in ("Windows", "macOS", "Linux")


# ---------------------------------------------------------------------- dialogs

def test_feedback_dialog_opens_the_prefilled_page(qapp):
    from si_assistant.ui.feedback_ui import FeedbackDialog
    opened = []
    dialog = FeedbackDialog(opener=opened.append)
    dialog.radios["feature"].setChecked(True)
    dialog.title.setText("Export to Word")
    dialog.open_btn.click()
    q = query(opened[0])
    assert q["template"] == "feature_request.yml" and q["title"] == "Export to Word"
    assert q["version"] == __version__ and q["os"]


def test_error_handler_shows_the_message(qapp, monkeypatch):
    import threading
    from si_assistant.ui import feedback_ui
    shown = []
    monkeypatch.setattr(feedback_ui.ErrorDialog, "exec",
                        lambda self: shown.append(self) or 0)
    old = (sys.excepthook, threading.excepthook)
    try:
        reporter = feedback_ui.install_error_handler(lambda: None)
        assert sys.excepthook == reporter.handle
        reporter.previous = (lambda *a: None, reporter.previous[1])   # (quiet test output)
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        qapp.processEvents()
        assert len(shown) == 1 and shown[0].summary == "RuntimeError: boom"
        dialog = shown[0]
        opened = []
        dialog.opener = opened.append
        dialog.report_btn.click()
        q = query(opened[0])
        assert q["template"] == "bug_report.yml" and q["title"] == "Error: RuntimeError: boom"
        assert "Traceback" in q["details"] and "RuntimeError: boom" in q["details"]
        dialog.copy_btn.click()
        assert "RuntimeError: boom" in qapp.clipboard().text()
        # An error in another thread arrives on the main thread.
        t = threading.Thread(target=lambda: 1 / 0)
        t.start()
        t.join()
        deadline = time.monotonic() + 3
        while len(shown) < 2 and time.monotonic() < deadline:
            qapp.processEvents()
        assert shown[1].summary.startswith("ZeroDivisionError")
    finally:
        sys.excepthook, threading.excepthook = old


def test_about_mentions_licence_and_github():
    from si_assistant.ui.feedback_ui import about_text
    text = about_text()
    assert __version__ in text and "GNU General Public License" in text
    assert updates.REPO_URL in text
    # (why the cryptography package is bundled, said plainly: SPEC §13s)
    assert "cryptography" in text and "nothing is encrypted" in text


# ---------------------------------------------------------------------- project format guard

def write_format(project, number):
    from si_assistant.core.project import PROJECT_FILE
    path = project.folder / PROJECT_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = number
    data["saved_by_version"] = "99.0.0"
    path.write_text(json.dumps(data), encoding="utf-8")


def test_format_version_and_program_version_are_saved(project):
    from si_assistant.core.project import FORMAT_VERSION, PROJECT_FILE, Project
    data = json.loads((project.folder / PROJECT_FILE).read_text(encoding="utf-8"))
    assert data["format_version"] == FORMAT_VERSION and data["saved_by_version"] == __version__
    again = Project.open(project.folder)
    assert not again.newer_format and "saved_by_version" not in again.settings
    assert "format_version" not in again.settings


def test_old_project_without_a_format_number_opens_normally(project):
    from si_assistant.core.project import PROJECT_FILE, Project
    path = project.folder / PROJECT_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["format_version"], data["saved_by_version"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not Project.open(project.folder).newer_format


def test_newer_project_is_flagged(project):
    from si_assistant.core.project import FORMAT_VERSION, Project
    write_format(project, FORMAT_VERSION + 1)
    p = Project.open(project.folder)
    assert p.newer_format and p.saved_by == "99.0.0"


@pytest.mark.parametrize("answer", ["read_only", "cancel"])
def test_main_window_warns_before_opening_a_newer_project(qapp, tmp_path, monkeypatch, answer):
    from PySide6.QtCore import QSettings
    from si_assistant.core.project import FORMAT_VERSION, Project
    from si_assistant.ui import main_window as mw, update_ui
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    p = Project.create(tmp_path, "Newer")
    write_format(p, FORMAT_VERSION + 1)
    asked = []
    monkeypatch.setattr(update_ui, "ask_about_newer_format",
                        lambda parent, project: asked.append(project.saved_by) or answer)
    assert "99.0.0" in update_ui.newer_format_message(Project.open(p.folder))
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    opened = win.open_project(p.folder)
    assert asked == ["99.0.0"]
    if answer == "cancel":
        assert not opened and win.project is None
    else:
        assert opened and win.project.read_only
        assert not (p.folder / ".lock").exists()
        win.close_project()
    win.close()
