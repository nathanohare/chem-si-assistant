"""Getting the Ketcher structure editor (EPAM, Apache-2.0 licence).

Ketcher is a web app; its "standalone" build works offline (the chemistry engine is
built in). It is ~30 MB, so it is not kept with the program's source: run
`python -m si_assistant.ketcher_setup` once (or click "Download" when the program
offers it). Installers (Phase 8) include it.
"""

from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import resources

VERSION = "3.18.0"
ZIP_URL = (f"https://github.com/epam/ketcher/releases/download/v{VERSION}/"
           f"ketcher-standalone-{VERSION}.zip")
LICENSE_URL = f"https://raw.githubusercontent.com/epam/ketcher/v{VERSION}/LICENSE"
# In the source code, and bundled in the same place in the installed program.
KETCHER_DIR = resources.resource("vendor", "ketcher")
# The release also has three other copies of the app (popup, duo, closable) we don't need.
SKIP = ("popup", "duo", "closable")


def ketcher_dir() -> Path:
    """Where Ketcher is, or is downloaded to: KETCHER_DIR, except in an installed program
    that somehow lacks its bundled copy — its own folder may not be writable (a macOS app,
    an AppImage), so the download then goes to this computer's program-data folder."""
    if not resources.is_frozen() or (KETCHER_DIR / "index.html").is_file():
        return KETCHER_DIR
    return resources.user_data_dir() / "ketcher"


def installed() -> bool:
    return (ketcher_dir() / "index.html").is_file()


def index_url_path() -> Path:
    return ketcher_dir() / "index.html"


def _download(url: str, progress: Callable[[int, int], None] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Chem-SI-Assistant"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk)
            if progress is not None:
                progress(buf.tell(), total)
    return buf.getvalue()


def install(progress: Callable[[int, int], None] | None = None) -> Path:
    """Download Ketcher and unpack the parts we use into si_assistant/vendor/ketcher."""
    data = _download(ZIP_URL, progress)
    target = ketcher_dir()
    tmp = target.with_name("ketcher.partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if name.endswith("/") or any(base.startswith(s) for s in SKIP):
                continue
            out = tmp / name
            if not out.resolve().is_relative_to(tmp.resolve()):
                continue  # never write outside the folder
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(name))
    try:
        (tmp / "LICENSE").write_bytes(_download(LICENSE_URL))
    except OSError:
        (tmp / "LICENSE").write_text(
            "Ketcher is (c) EPAM Systems, licensed under the Apache License 2.0:\n"
            "https://www.apache.org/licenses/LICENSE-2.0\nhttps://github.com/epam/ketcher\n",
            encoding="utf-8")
    (tmp / "VERSION").write_text(VERSION, encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    tmp.replace(target)
    return target


if __name__ == "__main__":
    def show(done: int, total: int) -> None:
        if total:
            sys.stdout.write(f"\rDownloading Ketcher {VERSION}: {done * 100 // total}%")
            sys.stdout.flush()

    print(f"Installed into {install(show)}")
