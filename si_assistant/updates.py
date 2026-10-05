"""Checking GitHub for a newer version, and the links for feedback (SPEC §13m.3, §13m.4).

No user interface here (that is in ui/update_ui.py), so everything can be tested without
the network: the functions that go online take the function that fetches as an argument.

Only GitHub is contacted, and nothing about the user or their projects is sent: the
update check asks for the latest release; a download fetches one file of it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import APP_NAME, __version__

GITHUB_REPO = "nathanohare/chem-si-assistant"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
NEW_ISSUE_URL = f"{REPO_URL}/issues/new"
TIMEOUT_S = 8

# The names of the installers attached to each GitHub Release. The release workflow
# (packaging) must name its files to match:
#   Chem-SI-Assistant-1.2.0-Windows-Setup.exe
#   Chem-SI-Assistant-1.2.0-macOS.dmg
#   Chem-SI-Assistant-1.2.0-x86_64.AppImage
ASSET_PATTERNS = {
    "windows": "Chem-SI-Assistant-*-Windows-Setup.exe",
    "macos": "Chem-SI-Assistant-*-macOS.dmg",
    "linux": "Chem-SI-Assistant-*-x86_64.AppImage",
}


# ---------------------------------------------------------------------- versions

_VERSION = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?"
                      r"(?:\+[0-9A-Za-z.-]+)?\s*$")


@dataclass(frozen=True)
class Version:
    """A version number like 1.2.3 or 1.2.3-beta.1 (from a tag such as "v1.2.3"),
    ordered as in Semantic Versioning: 1.0.0-alpha < 1.0.0-beta.1 < 1.0.0-beta.2 <
    1.0.0-rc.1 < 1.0.0 < 1.0.1."""
    numbers: tuple[int, int, int]
    pre: tuple = ()          # ("beta", 1) for -beta.1; () for a release
    text: str = ""

    def key(self) -> tuple:
        # A release sorts after its pre-releases; numeric parts before text parts.
        pre = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.pre)
        return (self.numbers, 0 if self.pre else 1, pre)

    def __lt__(self, other: "Version") -> bool:
        return self.key() < other.key()

    def __le__(self, other: "Version") -> bool:
        return self.key() <= other.key()

    def __gt__(self, other: "Version") -> bool:
        return self.key() > other.key()

    def __ge__(self, other: "Version") -> bool:
        return self.key() >= other.key()

    def __eq__(self, other) -> bool:
        return isinstance(other, Version) and self.key() == other.key()

    def __hash__(self) -> int:
        return hash(self.key())

    def __str__(self) -> str:
        return self.text


def parse_version(text: str) -> Version | None:
    """"v1.2.3", "1.2.3-beta.1", "1.2" → a Version; anything else → None."""
    m = _VERSION.match(str(text or ""))
    if not m:
        return None
    numbers = tuple(int(g or 0) for g in m.group(1, 2, 3))
    pre = tuple(int(p) if p.isdigit() else p.lower()
                for p in (m.group(4) or "").split(".") if p)
    plain = ".".join(str(n) for n in numbers) + (f"-{m.group(4)}" if m.group(4) else "")
    return Version(numbers, pre, plain)   # type: ignore[arg-type]


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


# ---------------------------------------------------------------------- the check

@dataclass
class Asset:
    name: str
    url: str          # browser_download_url
    size: int = 0


@dataclass
class UpdateInfo:
    """What the check found. `status` is one of:
    "newer" (a newer release exists), "up_to_date", "not_public" (GitHub answered 404:
    the repository is private or has no release yet), "unreachable" (no answer: offline,
    timeout, GitHub down) or "error" (an answer that could not be understood)."""
    status: str
    current: str = __version__
    latest: str = ""
    name: str = ""
    notes: str = ""
    page_url: str = RELEASES_URL
    assets: list[Asset] = field(default_factory=list)
    detail: str = ""


Fetcher = Callable[[str, int], dict]


def user_agent() -> str:
    return f"Chem-SI-Assistant/{__version__}"


def fetch_json(url: str, timeout: int = TIMEOUT_S) -> dict:
    """GET a GitHub API address and return its JSON (raises on any problem)."""
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent(), "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def check_latest(current: str = __version__, fetch: Fetcher = fetch_json) -> UpdateInfo:
    """Ask GitHub for the latest release and compare it with `current`. Never raises."""
    try:
        data = fetch(LATEST_RELEASE_API, TIMEOUT_S)
    except urllib.error.HTTPError as e:
        status = "not_public" if e.code == 404 else "unreachable"
        return UpdateInfo(status, current, detail=f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return UpdateInfo("unreachable", current, detail=str(e))
    except Exception as e:  # noqa: BLE001 — e.g. not JSON: never a crash
        return UpdateInfo("error", current, detail=f"{type(e).__name__}: {e}")
    if not isinstance(data, dict):
        return UpdateInfo("error", current, detail="unexpected answer")
    tag = str(data.get("tag_name") or "")
    latest = parse_version(tag)
    if latest is None:
        return UpdateInfo("error", current, detail=f"unreadable version {tag!r}")
    assets = []
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            try:
                size = int(a.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            assets.append(Asset(str(a["name"]), str(a["browser_download_url"]), size))
    mine = parse_version(current)
    newer = mine is None or latest > mine
    return UpdateInfo("newer" if newer else "up_to_date", current, str(latest),
                      str(data.get("name") or tag), str(data.get("body") or ""),
                      str(data.get("html_url") or RELEASES_URL), assets)


def platform_key(system: str | None = None, machine: str | None = None) -> str | None:
    """"windows", "macos" or "linux" (x86-64 only, as the AppImage), else None."""
    system = system if system is not None else sys.platform
    machine = (machine if machine is not None else platform.machine()).lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    if system.startswith("linux"):
        return "linux" if machine in ("x86_64", "amd64") else None
    return None


def pick_asset(assets: list[Asset], key: str | None) -> Asset | None:
    """The installer for this computer among a release's files, if there is one."""
    pattern = ASSET_PATTERNS.get(key or "")
    if not pattern:
        return None
    for a in assets:
        if fnmatch.fnmatchcase(a.name, pattern):
            return a
    return None


def automatic_check_allowed() -> bool:
    """The start-up check never runs in the start-up test or while tests run."""
    return not (os.environ.get("SI_ASSISTANT_SMOKE_TEST") or os.environ.get("PYTEST_CURRENT_TEST")
                or "pytest" in sys.modules)


class DownloadError(Exception):
    pass


def download(url: str, dest: Path, expected_size: int = 0,
             progress: Callable[[int, int], bool] | None = None,
             opener: Callable | None = None, timeout: int = 30) -> Path:
    """Download `url` to `dest` (a ".part" file renamed when complete). `progress(done,
    total)` is called as it goes; returning False cancels (DownloadError). The size is
    checked against `expected_size` (GitHub's size of the file) when given."""
    opener = opener or urllib.request.urlopen
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    done = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
        with opener(request, timeout=timeout) as response, open(part, "wb") as out:
            total = expected_size or int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None and progress(done, total) is False:
                    raise DownloadError("The download was cancelled.")
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001
        part.unlink(missing_ok=True)
        raise DownloadError(f"The download failed: {e}") from e
    if expected_size and done != expected_size:
        part.unlink(missing_ok=True)
        raise DownloadError(f"The download is incomplete ({done:,} of {expected_size:,} bytes).")
    os.replace(part, dest)
    return dest


# ---------------------------------------------------------------------- feedback (§13m.4)

ISSUE_TEMPLATES = {"bug": "bug_report.yml", "feature": "feature_request.yml",
                   "feedback": "feedback.yml"}
MAX_URL_LENGTH = 7000   # GitHub refuses much longer addresses (about 8 KB)


def os_description() -> str:
    """The operating system, e.g. "Windows 11 (10.0.26200, AMD64)"."""
    machine = platform.machine()
    if sys.platform.startswith("win"):
        version = platform.version()
        release = platform.release()
        try:   # Windows 11 still says "10" in older Pythons; build 22000+ is 11
            if release == "10" and int(version.split(".")[2]) >= 22000:
                release = "11"
        except (IndexError, ValueError):
            pass
        return f"Windows {release} ({version}, {machine})"
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({machine})"
    try:
        name = platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        name = ""
    return f"Linux{' ' + name if name else ''} ({platform.release()}, {machine})"


def scrub_paths(text: str, extra: dict[str, str] | None = None) -> str:
    """Remove personal folder names from a traceback: the home folder becomes "~" and each
    path in `extra` (e.g. the open project's folder) becomes its label."""
    replacements = dict(extra or {})
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        replacements.setdefault(home, "~")
    for path, label in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        if not path:
            continue
        variants = {path, path.replace("\\", "/"), path.replace("/", "\\"),
                    path.replace("\\", "\\\\")}
        for v in sorted(variants, key=len, reverse=True):
            flags = re.IGNORECASE if sys.platform.startswith("win") or ":" in v[:3] else 0
            text = re.sub(re.escape(v), lambda _m, label=label: label, text, flags=flags)
    return text


def shorten_middle(text: str, keep: int) -> str:
    """`text` cut to about `keep` characters, keeping its start and (mostly) its end,
    where a traceback says what went wrong."""
    if len(text) <= keep:
        return text
    marker = "\n… (shortened) …\n"
    keep = max(keep - len(marker), 0)
    head = keep // 4
    return text[:head] + marker + text[len(text) - (keep - head):]


def issue_url(kind: str, title: str = "", version: str = __version__, os_text: str | None = None,
              details: str = "", limit: int = MAX_URL_LENGTH) -> str:
    """The address of GitHub's "New issue" page with the issue form for `kind` ("bug",
    "feature" or "feedback") and its fields filled in. Long `details` are shortened so the
    address stays under `limit` characters."""
    os_text = os_description() if os_text is None else os_text
    params = {"template": ISSUE_TEMPLATES.get(kind, ISSUE_TEMPLATES["feedback"])}
    if title.strip():
        params["title"] = title.strip()[:200]
    params["version"] = version
    params["os"] = os_text

    def build(d: str) -> str:
        p = dict(params)
        if d:
            p["details"] = d
        return f"{NEW_ISSUE_URL}?{urllib.parse.urlencode(p, quote_via=urllib.parse.quote)}"

    url = build(details)
    keep = len(details)
    while len(url) > limit and keep > 0:
        keep = int(keep * 0.85) - 1
        url = build(shorten_middle(details, keep) if keep > 0 else "")
    return url


def error_report(exc_type, exc, tb, extra: dict[str, str] | None = None) -> tuple[str, str]:
    """(one-line summary, scrubbed traceback) of an unexpected error."""
    import traceback
    summary = traceback.format_exception_only(exc_type, exc)[-1].strip()
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    details += f"\n{APP_NAME} {__version__}, Python {platform.python_version()}"
    return scrub_paths(summary, extra), scrub_paths(details, extra)
