"""The project's bibliography (SPEC §9.5): references.json, adding references by DOI
(Crossref), importing BibTeX/RIS, and writing them out for Typst.

Each reference is a plain dict, so references.json stays readable:

    {"id": "r-1a2b3c", "type": "article",
     "authors": ["Fulmer, Gregory R.", "Miller, Alexander J. M."],
     "title": "NMR Chemical Shifts of Trace Impurities…", "journal": "Organometallics",
     "year": "2010", "volume": "29", "issue": "9", "pages": "2176–2179",
     "doi": "10.1021/om100106e", "url": "", "publisher": "", "note": ""}

Citations in text are stored as `@cite{r-1a2b3c}` (core/markup.py) and numbered in
order of first appearance when compiling; the journal style sets the format.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .ids import new_id

TYPES = {"article": "Journal article", "book": "Book", "chapter": "Book chapter",
         "thesis": "Thesis", "web": "Web page", "misc": "Other"}
FIELDS = ("authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "url",
          "publisher", "note")
CROSSREF_URL = "https://api.crossref.org/works/"
USER_AGENT = "Chem-SI-Assistant (open-source desktop program for Supporting Information)"


class ReferenceError(Exception):
    """A problem the user should be told about in plain language."""


def blank(type_: str = "article") -> dict[str, Any]:
    ref: dict[str, Any] = {"id": new_id("r"), "type": type_}
    for f in FIELDS:
        ref[f] = [] if f == "authors" else ""
    return ref


def clean(ref: dict[str, Any]) -> dict[str, Any]:
    """Fill in missing keys and tidy values (always returns a new dict)."""
    out = blank(ref.get("type") if ref.get("type") in TYPES else "article")
    out["id"] = ref.get("id") or out["id"]
    for f in FIELDS:
        value = ref.get(f, out[f])
        if f == "authors":
            if isinstance(value, str):
                value = [a.strip() for a in re.split(r"\s*;\s*|\s+and\s+", value) if a.strip()]
            out[f] = [str(a).strip() for a in value if str(a).strip()]
        else:
            out[f] = str(value).strip() if value is not None else ""
    out["doi"] = normalise_doi(out["doi"])
    out["pages"] = re.sub(r"(?<=\d)\s*-+\s*(?=\d)", "–", out["pages"])
    return out


def normalise_doi(text: str) -> str:
    """"https://doi.org/10.1021/OM100106E " → "10.1021/OM100106E"."""
    text = (text or "").strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    return text.strip()


def surname(author: str) -> str:
    author = author.strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = author.split()
    return parts[-1] if parts else ""


def short_label(ref: dict[str, Any]) -> str:
    """"Fulmer 2010", "Fulmer & Miller 2010" or "Smith et al. 2020" — how a citation
    looks in the editor (the SI uses the journal's numbering)."""
    names = [surname(a) for a in ref.get("authors") or [] if surname(a)]
    if not names:
        who = (ref.get("title") or "Untitled")[:30]
    elif len(names) == 1:
        who = names[0]
    elif len(names) == 2:
        who = f"{names[0]} & {names[1]}"
    else:
        who = f"{names[0]} et al."
    year = str(ref.get("year") or "").strip()
    return f"{who} {year}".strip()


def describe(ref: dict[str, Any]) -> str:
    """One line for lists: authors, journal year, volume, pages."""
    bits = [short_label(ref)]
    where = " ".join(x for x in (ref.get("journal", ""), ref.get("volume", "")) if x)
    if where:
        bits.append(where)
    if ref.get("pages"):
        bits.append(ref["pages"])
    title = ref.get("title", "")
    return ", ".join(bits) + (f" — {title}" if title else "")


def find_duplicate(refs: list[dict[str, Any]], ref: dict[str, Any]) -> dict[str, Any] | None:
    """An existing reference with the same DOI (or same title and year)."""
    doi = normalise_doi(ref.get("doi", "")).lower()
    for r in refs:
        if doi and normalise_doi(r.get("doi", "")).lower() == doi:
            return r
        if (not doi and ref.get("title") and r.get("title", "").strip().lower()
                == ref["title"].strip().lower() and r.get("year") == ref.get("year")):
            return r
    return None


# ---------------------------------------------------------------------- Crossref

def from_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """A reference from a Crossref /works/<doi> "message"."""
    ctype = message.get("type", "")
    type_ = {"journal-article": "article", "book": "book", "monograph": "book",
             "book-chapter": "chapter", "dissertation": "thesis",
             "posted-content": "misc"}.get(ctype, "misc")
    authors = []
    for a in message.get("author", []) or []:
        if a.get("family"):
            authors.append(f"{a['family']}, {a.get('given', '')}".strip().rstrip(","))
        elif a.get("name"):
            authors.append(a["name"])
    date = (message.get("published-print") or message.get("published-online")
            or message.get("issued") or {}).get("date-parts") or [[None]]
    year = str(date[0][0]) if date and date[0] and date[0][0] else ""
    journal = ""
    short = message.get("short-container-title") or []
    full = message.get("container-title") or []
    if short:
        journal = short[0]
    elif full:
        journal = full[0]
    title = (message.get("title") or [""])[0]
    return clean({"type": type_, "authors": authors, "title": _strip_tags(title),
                  "journal": journal, "year": year, "volume": message.get("volume", ""),
                  "issue": message.get("issue", ""), "pages": message.get("page", ""),
                  "doi": message.get("DOI", ""), "publisher": message.get("publisher", "")
                  if type_ in ("book", "chapter") else ""})


def fetch_doi(doi: str, timeout: float = 15.0) -> dict[str, Any]:
    """Look a DOI up on Crossref (needs an internet connection)."""
    doi = normalise_doi(doi)
    if not re.match(r"^10\.\d{4,9}/\S+$", doi):
        raise ReferenceError(f"“{doi}” doesn't look like a DOI (they start with 10.…/).")
    url = CROSSREF_URL + urllib.parse.quote(doi, safe="/:;()")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ReferenceError(f"Crossref has no record of the DOI {doi}. Check it, or "
                                 "enter the reference by hand.") from e
        raise ReferenceError(f"Crossref could not be reached (error {e.code}). Try again "
                             "later, or enter the reference by hand.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ReferenceError("Could not connect to Crossref. Are you online? You can also "
                             "enter the reference by hand.") from e
    except ValueError as e:
        raise ReferenceError("Crossref sent an answer that could not be read.") from e
    return from_crossref(data.get("message", {}))


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ---------------------------------------------------------------------- BibTeX / RIS

_LATEX_ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
                  "c": "\u0327", "v": "\u030c", "=": "\u0304", ".": "\u0307", "u": "\u0306",
                  "H": "\u030b", "k": "\u0328", "r": "\u030a"}
_LATEX_LETTERS = {"o": "ø", "O": "Ø", "ss": "ß", "ae": "æ", "AE": "Æ", "l": "ł", "L": "Ł",
                  "aa": "å", "AA": "Å", "i": "ı"}


def _delatex(text: str) -> str:
    import unicodedata
    text = re.sub(r"\\textendash\b\s?|--", "–", text)
    text = re.sub(r"\\textemdash\b\s?", "—", text)

    def accent(m: re.Match) -> str:
        mark, letter = m.group(1), m.group(2) or m.group(3)
        if letter == "\\i":
            letter = "i"
        return unicodedata.normalize("NFC", letter + _LATEX_ACCENTS.get(mark, ""))

    text = re.sub(r"\\([\'`^\"~=.uvcHkr])\s*(?:\{\s*(\\i|[A-Za-z])\s*\}|(\\i|[A-Za-z]))", accent, text)
    text = re.sub(r"\{?\\(ss|ae|AE|aa|AA|o|O|l|L)\}?(?![A-Za-z])",
                  lambda m: _LATEX_LETTERS[m.group(1)], text)
    text = re.sub(r"\\&", "&", text)
    text = re.sub(r"\$_\{?(\d+)\}?\$", r"\1", text)   # $_2$ → 2 in titles
    text = re.sub(r"\\[a-zA-Z]+\s*", "", text)        # any other command
    return re.sub(r"\s+", " ", text.replace("{", "").replace("}", "")).strip()


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    """References from a BibTeX file (e.g. exported from Zotero or EndNote)."""
    refs = []
    for m in re.finditer(r"@(\w+)\s*\{", text):
        kind = m.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        body, i, depth = [], m.end(), 1
        while i < len(text) and depth:
            ch = text[i]
            depth += (ch == "{") - (ch == "}")
            if depth:
                body.append(ch)
            i += 1
        entry = _bibtex_fields("".join(body))
        type_ = {"article": "article", "book": "book", "inbook": "chapter",
                 "incollection": "chapter", "phdthesis": "thesis", "mastersthesis": "thesis",
                 "online": "web", "misc": "misc"}.get(kind, "misc")
        authors = [_flip(_delatex(a)) for a in re.split(r"\s+and\s+", entry.get("author", ""))
                   if a.strip()]
        refs.append(clean({
            "type": type_, "authors": authors, "title": _delatex(entry.get("title", "")),
            "journal": _delatex(entry.get("journal", "") or entry.get("journaltitle", "")
                                or entry.get("booktitle", "")),
            "year": entry.get("year", "") or entry.get("date", "")[:4],
            "volume": entry.get("volume", ""), "issue": entry.get("number", ""),
            "pages": _delatex(entry.get("pages", "")), "doi": entry.get("doi", ""),
            "url": entry.get("url", ""), "publisher": _delatex(entry.get("publisher", "")),
            "note": _delatex(entry.get("note", ""))}))
    return refs


def _bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i = body.find(",")  # skip the citation key
    i = 0 if i < 0 else i + 1
    n = len(body)
    while i < n:
        m = re.compile(r"\s*([A-Za-z][\w-]*)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += (body[j] == "{") - (body[j] == "}")
                j += 1
            value, i = body[i + 1:j - 1], j
        elif i < n and body[i] == '"':
            j = i + 1
            while j < n and not (body[j] == '"' and body[j - 1] != "\\"):
                j += 1
            value, i = body[i + 1:j], j + 1
        else:
            j = i
            while j < n and body[j] not in ",\n":
                j += 1
            value, i = body[i:j].strip(), j
        fields[name] = re.sub(r"\s+", " ", value).strip()
        comma = body.find(",", i)
        if comma < 0:
            break
        i = comma + 1
    return fields


def _flip(name: str) -> str:
    """"Gregory R. Fulmer" → "Fulmer, Gregory R." (names with a comma are kept)."""
    name = name.strip()
    if "," in name or " " not in name:
        return name
    first, _, last = name.rpartition(" ")
    return f"{last}, {first}"


def parse_ris(text: str) -> list[dict[str, Any]]:
    """References from a RIS file (EndNote, Zotero, publishers' "export citation")."""
    refs, current = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line.rstrip())
        if not m:
            continue
        tag, value = m.group(1), m.group(2).strip()
        if tag == "TY":
            current = {"TY": value, "AU": []}
        elif current is None:
            continue
        elif tag == "ER":
            refs.append(_ris_ref(current))
            current = None
        elif tag in ("AU", "A1"):
            current["AU"].append(value)
        else:
            current.setdefault(tag, value)
    if current is not None:
        refs.append(_ris_ref(current))
    return refs


def _ris_ref(e: dict[str, Any]) -> dict[str, Any]:
    type_ = {"JOUR": "article", "JFULL": "article", "BOOK": "book", "CHAP": "chapter",
             "THES": "thesis", "ELEC": "web"}.get(e.get("TY", ""), "misc")
    pages = e.get("SP", "")
    if e.get("EP"):
        pages = f"{pages}–{e['EP']}"
    year = (e.get("PY") or e.get("Y1") or e.get("DA") or "")[:4]
    return clean({"type": type_, "authors": e.get("AU", []),
                  "title": e.get("TI") or e.get("T1", ""),
                  "journal": e.get("J2") or e.get("JO") or e.get("JA") or e.get("T2")
                  or e.get("JF", ""),
                  "year": year, "volume": e.get("VL", ""), "issue": e.get("IS", ""),
                  "pages": pages, "doi": e.get("DO", ""), "url": e.get("UR", ""),
                  "publisher": e.get("PB", ""), "note": e.get("N1", "")})


def parse_file_text(text: str) -> list[dict[str, Any]]:
    """BibTeX or RIS, whichever the text is."""
    if re.search(r"^TY  -", text, flags=re.M):
        return parse_ris(text)
    return parse_bibtex(text)


# ---------------------------------------------------------------------- Typst

def to_hayagriva(refs: list[dict[str, Any]]) -> str:
    """The references as a Hayagriva bibliography (Typst's own format). Written as JSON,
    which is valid YAML, so no character in a title can break the file."""
    out: dict[str, Any] = {}
    for ref in refs:
        r = clean(ref)
        entry: dict[str, Any] = {
            "type": {"article": "article", "book": "book", "chapter": "chapter",
                     "thesis": "thesis", "web": "web"}.get(r["type"], "misc")}
        if r["title"]:
            entry["title"] = r["title"]
        if r["authors"]:
            entry["author"] = r["authors"]
        if r["year"]:
            entry["date"] = r["year"]
        if r["pages"]:
            entry["page-range"] = r["pages"]
        if r["doi"]:
            entry["serial-number"] = {"doi": r["doi"]}
        if r["url"]:
            entry["url"] = r["url"]
        if r["publisher"]:
            entry["publisher"] = r["publisher"]
        if r["note"]:
            entry["note"] = r["note"]
        if r["journal"] or r["volume"] or r["issue"]:
            parent: dict[str, Any] = {"type": "periodical" if r["type"] == "article" else "book"}
            if r["journal"]:
                parent["title"] = r["journal"]
            if r["volume"]:
                parent["volume"] = r["volume"]
            if r["issue"]:
                parent["issue"] = r["issue"]
            entry["parent"] = parent
        out[r["id"]] = entry
    return json.dumps(out, ensure_ascii=False, indent=1)
