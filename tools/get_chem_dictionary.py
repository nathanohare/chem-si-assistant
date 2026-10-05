"""Download the chemistry spelling dictionary used by the spelling check (SPEC §13l.6).

    python tools/get_chem_dictionary.py                 (downloads it)
    python tools/get_chem_dictionary.py chem-dic.zip    (uses a zip or .dic already saved)

The dictionary is Adam M. Azman's Open Source Chemistry Dictionary (version 3.0, 104,658
words; "A Chemistry Spell-Check Dictionary for Word Processors", J. Chem. Educ. 2012, 89,
412-413, doi:10.1021/ed2002994), licensed under the
Creative Commons Attribution 3.0 United States licence. It is distributed by
sciencebase.com as chem-dic.zip, which holds the word list `chemistry.dic` (UTF-16, one
word per line) and `install.txt` (the credits and licence).

This script writes two files into si_assistant/data/:

- azman_chemistry_dictionary.txt: the words, converted to UTF-8, one per line, sorted
  (ignoring capitals) with repeated lines removed. The words themselves are unchanged
  (original capitals kept).
- azman_chemistry_dictionary_LICENSE.txt: the attribution the licence asks for.

Only the Python standard library is used.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "si_assistant" / "data"
OUT = DATA / "azman_chemistry_dictionary.txt"
LICENSE_OUT = DATA / "azman_chemistry_dictionary_LICENSE.txt"
URL = "https://www.sciencebase.com/chem-dic.zip"
USER_AGENT = "Chem-SI-Assistant"
DIC_NAME = "chemistry.dic"

LICENSE_TEXT = """\
Open Source Chemistry Dictionary (a chemistry spell-check dictionary for word processors)
Version 3.0
Created by Adam M. Azman

Bundled with Chem SI Assistant as azman_chemistry_dictionary.txt and used by its
spelling check.

Source:   {url} (file chemistry.dic)
Article:  A. M. Azman, "A Chemistry Spell-Check Dictionary for Word Processors",
          J. Chem. Educ. 2012, 89, 412-413. https://doi.org/10.1021/ed2002994
Licence:  Creative Commons Attribution 3.0 United States (CC BY 3.0 US)
          https://creativecommons.org/licenses/by/3.0/us/

Changes:  none to the words. The file was converted from UTF-16 to UTF-8 text, and its
          lines were sorted alphabetically (ignoring capitals) with repeated lines removed
          ({words} words). Made by tools/get_chem_dictionary.py.
{credits}"""


def download(url: str = URL) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def decode(data: bytes) -> str:
    """The text of a .dic file: UTF-16 (with a byte-order mark) as distributed, or UTF-8,
    or Windows-1252 as a last resort."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def words_of(text: str) -> list[str]:
    """One word per line, sorted (ignoring capitals, then exactly), each once."""
    words = {line.strip() for line in text.splitlines() if line.strip()}
    return sorted(words, key=lambda w: (w.casefold(), w))


def unpack(data: bytes) -> tuple[str, str]:
    """(the word list's text, the credits text or "") from chem-dic.zip's bytes, or from
    a .dic file's bytes."""
    if data[:2] != b"PK":
        return decode(data), ""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {Path(n).name.lower(): n for n in zf.namelist()}
        if DIC_NAME not in names:
            raise SystemExit(f"{DIC_NAME} is not in the zip (it has: {', '.join(names)})")
        credits = ""
        if "install.txt" in names:
            credits = decode(zf.read(names["install.txt"])).strip()
        return decode(zf.read(names[DIC_NAME])), credits


def main(argv: list[str]) -> None:
    if argv:
        source = Path(argv[0])
        data = source.read_bytes()
        print(f"Using {source}")
    else:
        print(f"Downloading {URL} …")
        data = download()
    text, credits = unpack(data)
    words = words_of(text)
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(words) + "\n", encoding="utf-8", newline="\n")
    note = ""
    if credits:
        note = ("\nThe credits and licence as distributed (install.txt):\n\n"
                + credits.replace("\r\n", "\n") + "\n")
    LICENSE_OUT.write_text(LICENSE_TEXT.format(url=URL, words=f"{len(words):,}",
                                               credits=note),
                           encoding="utf-8", newline="\n")
    print(f"{len(words):,} words -> {OUT.relative_to(ROOT)}")
    print(f"attribution -> {LICENSE_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
