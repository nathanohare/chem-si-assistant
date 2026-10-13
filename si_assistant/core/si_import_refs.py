"""The reference list of an imported SI and the citations in its text (SPEC §13u.9,
phase I4).

* `split_entries(texts)`: the reference list's paragraphs into numbered entries. A PDF
  can run several entries into one paragraph ("… 2925. (2) Fulmer, …") or cut one in
  two; entries numbered "(1)", "[1]", "1." or "1 " are found by their numbers counting
  up. Without numbers (Word's own numbered list), each paragraph is one entry,
  numbered 1, 2, … in order. An entry with parts "a) …; b) …" gives one reference per
  part ("3a", "3b"); citing "3" cites them all.
* `parse_reference(text)`: an entry's authors, title, journal, year, volume, issue,
  pages, DOI and web address, for the common styles (ACS "Surname, I. I.; …", RSC and
  Wiley "I. I. Surname, …", Nature "… (2010)."). What can't be read stays whole as the
  title of a reference of type "Other", so nothing is lost; with a DOI, Crossref can
  fill it in (on click, in the review window).
* `link_citations(markup, numbers)`: numbered citations in imported text — superscript
  "…literature.^{3,4}", "[3]", "[3–5]" — become the program's citations
  (`@cite{r-…}`) of those references; numbers with no reference are left as they are.

Pure Python, no Qt: everything here is unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import references as refs

# ---------------------------------------------------------------------- entries


@dataclass
class ImportedReference:
    number: str                 # "1", "3a" (as numbered in the SI; "" = none)
    text: str                   # the entry as printed (plain text)
    ref: dict = field(default_factory=dict)   # the parsed reference (references.py form)
    keep: bool = True
    looked_up: str = ""         # "" / "done" / why Crossref could not fill it


_MARKERS = {          # (the marker of entry {n}, how a list in that style starts)
    "paren": (r"\(\s*{n}\s*\)", re.compile(r"^\(\s*(\d{1,3})\s*\)\s*\S")),
    "bracket": (r"\[\s*{n}\s*\]", re.compile(r"^\[\s*(\d{1,3})\s*\]\s*\S")),
    "dot": (r"{n}\.", re.compile(r"^(\d{1,3})\.\s+\S")),
    "plain": (r"{n}", re.compile(r"^(\d{1,3})\s+[A-ZÀ-ɏ]")),
}


def _style(first: str) -> tuple[str, int]:
    """(the numbering style of a list starting with `first`, its first number)."""
    for name, (_pat, start) in _MARKERS.items():
        m = start.match(first)
        if m:
            return name, int(m.group(1))
    return "", 0


def split_entries(texts: list[str]) -> list[tuple[str, str]]:
    """[(number, text)] of a reference list's paragraphs (see the module's text)."""
    texts = [re.sub(r"\s+", " ", t).strip() for t in texts if t and t.strip()]
    if not texts:
        return []
    style, first = _style(texts[0])
    if not style:
        out = []
        for i, t in enumerate(texts, 1):
            out.extend(_parts(str(i), t))
        return out
    joined = " " + " ".join(texts)
    pattern, _start = _MARKERS[style]
    starts: list[tuple[int, int, int]] = []    # (number, where the marker starts, ends)
    pos, n = 0, first
    while True:
        # the next number, after a space (the end of the entry before: ". ", ") ", a URL…)
        m = re.compile(r"(?<=\s)" + pattern.format(n=n) + r"\s*(?=\S)").search(joined, pos)
        if not m:
            break
        if n > first and style in ("plain", "dot") and not re.search(
                r"[.)\]\d/a-z]\s*$", joined[:m.start()]):
            pos = m.end()          # ("… 100 159–185": a number inside an entry)
            continue
        starts.append((n, m.start(), m.end()))
        pos, n = m.end(), n + 1
    if not starts:
        return [(str(i), t) for i, t in enumerate(texts, 1)]
    out = []
    for k, (num, _s, e) in enumerate(starts):
        end = starts[k + 1][1] if k + 1 < len(starts) else len(joined)
        out.extend(_parts(str(num), joined[e:end].strip()))
    return out


def _parts(number: str, text: str) -> list[tuple[str, str]]:
    """An entry with parts "(a) …; (b) …" or "a) …; b) …" as one entry per part."""
    marks = list(re.finditer(r"(?:^|(?<=[;.]\s))\(?([a-z])\)\s+(?=\S)", text))
    letters = [m.group(1) for m in marks]
    if len(marks) < 2 or letters != [chr(ord("a") + i) for i in range(len(letters))]:
        return [(number, text.strip())]
    out = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        part = text[m.end():end].strip().rstrip(";").strip()
        out.append((number + m.group(1), part))
    return out


# ---------------------------------------------------------------------- one reference

# Common journal abbreviations (CASSI), longest first when matched, so the title and the
# journal can be told apart ("… Redux. J. Org. Chem. 2022").
_JOURNALS = [
    "J. Am. Chem. Soc.", "J. Org. Chem.", "Org. Lett.", "Angew. Chem. Int. Ed.", "Angew. Chem.",
    "Chem. Sci.", "Nat. Chem.", "Nature Chemistry", "Science", "Nature", "Organometallics",
    "Inorg. Chem.", "ACS Catal.", "Chem. Eur. J.", "Chem. - Eur. J.", "Org. Synth.",
    "Eur. J. Org. Chem.", "Tetrahedron Lett.", "Tetrahedron: Asymmetry", "Tetrahedron",
    "Synthesis", "Synlett", "Chem. Commun.", "Phys. Chem. Chem. Phys.", "J. Chem. Phys.",
    "J. Phys. Chem. A", "J. Phys. Chem. B", "J. Phys. Chem. C", "J. Phys. Chem. Lett.",
    "J. Comput. Chem.", "J. Chem. Theory Comput.", "Chem. Rev.", "Acc. Chem. Res.",
    "Org. Process Res. Dev.", "Adv. Synth. Catal.", "Beilstein J. Org. Chem.",
    "J. Med. Chem.", "ACS Med. Chem. Lett.", "Org. Biomol. Chem.", "Green Chem.",
    "Dalton Trans.", "JACS Au", "ACS Cent. Sci.", "Nat. Commun.", "Nat. Catal.",
    "Nat. Synth.", "Proc. Natl. Acad. Sci. U.S.A.", "Proc. Natl. Acad. Sci. USA",
    "Bioorg. Med. Chem. Lett.", "Bioorg. Med. Chem.", "J. Nat. Prod.", "Org. Chem. Front.",
    "Chin. J. Chem.", "CCS Chem.", "Chem. Soc. Rev.", "J. Fluorine Chem.",
    "Synth. Commun.", "Helv. Chim. Acta", "Bull. Chem. Soc. Jpn.", "Chem. Lett.",
    "J. Organomet. Chem.", "Polyhedron", "ChemCatChem", "ChemistrySelect",
    "Asian J. Org. Chem.", "Chem. Asian J.", "Chem. - Asian J.", "Eur. J. Inorg. Chem.",
    "RSC Adv.", "New J. Chem.", "Theor. Chem. Acc.", "J. Mol. Struct.", "Chem",
    "Cell Rep. Phys. Sci.", "iScience", "ACS Omega", "Molecules", "Org. React.",
    "Chem. Rec.", "Synth. Met.", "Macromolecules", "ACS Macro Lett.", "Polym. Chem.",
    "J. Chem. Inf. Model.", "J. Chem. Educ.", "Anal. Chem.", "ChemBioChem",
    "ChemMedChem", "ChemPhysChem", "ChemSusChem", "ChemPlusChem", "Biochemistry",
    "J. Biol. Chem.", "Nat. Chem. Biol.", "ACS Chem. Biol.", "Cell Chem. Biol.",
    "Chem. Biol.", "J. Antibiot.", "Appl. Environ. Microbiol.", "Nucleic Acids Res.",
    "Mol. Microbiol.", "J. Bacteriol.", "Phys. Rev. Lett.", "Phys. Rev. B",
    "Theoret. chim. Acta", "Theor. Chim. Acta", "Coord. Chem. Rev.", "Chem. Mater.",
    "J. Mater. Chem. A", "J. Mater. Chem.", "Adv. Mater.", "Adv. Funct. Mater.",
    "J. Catal.", "Catal. Sci. Technol.", "Faraday Discuss.", "Chem. Phys. Lett.",
    "Mol. Phys.", "J. Chem. Soc., Perkin Trans. 1", "J. Chem. Soc., Chem. Commun.",
    "J. Chem. Soc.", "Liebigs Ann. Chem.", "Chem. Ber.", "Synth. Met.", "Heterocycles",
    "Carbohydr. Res.", "Steroids", "Phytochemistry", "Org. Prep. Proced. Int.",
    "Can. J. Chem.", "Aust. J. Chem.", "Collect. Czech. Chem. Commun.",
    "Russ. Chem. Rev.", "Mendeleev Commun.", "Chem. Pharm. Bull.", "J. Heterocycl. Chem.",
]
_JOURNALS.sort(key=len, reverse=True)
_JOURNAL_RE = "|".join(re.escape(j).replace(r"\ ", r"\s?") for j in _JOURNALS)

_DOI = re.compile(r"(?:https?://\s?(?:dx\.)?doi\.\s?org/\s?|doi:\s*|DOI:?\s*)?"
                  r"(?P<doi>10\.\s?\d{4,9}/\s?[^\s]+?)(?=[.,;]?(?:\s|$))", re.I)
_URL = re.compile(r"https?://\S+")
_UP = r"A-ZÀ-ÞĀ-ſ"         # capitals (with accents)
_LOW = r"a-zß-ÿĀ-ſ'’\-"
_SURNAME = (rf"(?:(?:van|von|der|de|del|da|di|le|la|du|ten|ter)\s)*[{_UP}][{_LOW}]*"
            rf"(?:['’]?[{_UP}][{_LOW}]+)*(?:[\s\-][{_UP}][{_LOW}]+(?:['’]?[{_UP}][{_LOW}]+)*)*")
# ("O'Connor", "McDonald", "Head-Gordon", "Jimenez Santiago", "van der Berg")
_INITIALS = rf"(?:[{_UP}][a-z]?\s?\.\s?-?\s?)+"
# "Still, W. C." (ACS, surname first) and "W. C. Still" (initials first)
_AUTHOR_SF = rf"{_SURNAME},\s?{_INITIALS}(?:,?\s?(?:Jr|Sr|II|III)\.?)?"
_AUTHOR_IF = rf"{_INITIALS}\s?{_SURNAME}(?![.\w])"      # (not "J. Org." of a journal)
_SEP = r"(?:\s?[;,]\s?(?:and\s|&\s)?|\s(?:and|&)\s)"     # between two authors
_YEAR = r"(?:1[89]|20)\d\d"
_PAGES = r"[A-Za-z]?\d+[A-Za-z]?(?:\s?[-–—]\s?[A-Za-z]?\d+[A-Za-z]?)?"


def _tidy(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"(?<=[A-Za-z.])(?=" + _YEAR + r"\s?,)", " ", t)    # "Chem.1978," (PDF)
    t = re.sub(r"\s+([,;:.])(?=\s|$)", r"\1", t)                    # "10 , 6615"
    t = re.sub(r"([A-Z])\s\.", r"\1.", t)                            # "V . R." (PDF)
    return t


def parse_reference(text: str) -> dict:
    """A reference (core/references.py form) from one entry as printed (see the module's
    text). Never fails: what can't be read is kept as the title of an "Other"."""
    t = _tidy(text)
    ref = refs.blank("article")
    m = _DOI.search(t)
    if m:
        ref["doi"] = re.sub(r"\s", "", m.group("doi")).rstrip(".")
        t = (t[:m.start()] + t[m.end():]).strip()
    m = _URL.search(t)
    if m:
        ref["url"] = m.group(0).rstrip(".,;")
        t = (t[:m.start()] + t[m.end():]).strip()
    t = re.sub(r"\s*(?:Available\s+online|Retrieved\s+from|Accessed|DOI:?)\s*[.:]?\s*$", "",
               t, flags=re.I).strip().rstrip(".,; ").strip()
    for reader in (_acs, _initials_first, _nature, _authors_and_title):
        found = reader(t)
        if found:
            ref.update({k: v for k, v in found.items() if v})
            return refs.clean(ref)
    ref["type"] = "misc"
    ref["title"] = t
    return refs.clean(ref)


def _authors_and_title(t: str) -> dict | None:
    """Authors (surname first) and then anything else: kept as the title of an "Other"."""
    m = re.match(rf"(?P<authors>{_AUTHOR_SF}(?:{_SEP}{_AUTHOR_SF})*)"
                 rf"(?:\s?(?:et\s+al\.))?[,.]?\s+(?P<rest>\S.*)$", t)
    if not m:
        return None
    return {"authors": [a.strip(" ,;") for a in re.findall(_AUTHOR_SF, m.group("authors"))],
            "title": m.group("rest").strip(" ."), "type": "misc"}


def _tail(rest: str) -> dict | None:
    """Journal, year, volume (issue), pages at the end: "J. Org. Chem. 1978, 43 (14),
    2923–2925" / "J. Org. Chem., 1978, 43, 2923" / "Science 2013, 341, 1374"."""
    m = re.search(rf"(?P<journal>.*?)[\s,]*(?P<year>{_YEAR})\s?,\s?(?P<volume>\d+[A-Z]?)"
                  rf"(?:\s?\((?P<issue>[^)]{{1,12}})\))?\s?,\s?(?P<pages>{_PAGES})\s*$", rest)
    if not m:
        m = re.search(rf"(?P<journal>.*?)[\s,]*(?P<year>{_YEAR})\s?,\s?(?P<pages>{_PAGES})\s*$",
                      rest)       # (no volume: "Org. Synth. 2023, 159–185" is rare but seen)
        if not m:
            return None
    out = {k: (v or "").strip() for k, v in m.groupdict().items()}
    out["journal"] = out["journal"].strip(" ,")
    return out


def _split_title_journal(before: str) -> tuple[str, str]:
    """"Rapid Technique … Resolution. J. Org. Chem." → (title, journal); a title alone
    (no journal found) → (title, "")."""
    before = before.strip(" ,")
    m = re.search(rf"(?:^|(?<=[.?!,]\s)|(?<=[.?!,]))(?P<j>{_JOURNAL_RE})\.?$", before)
    if m:
        return before[:m.start()].strip(" ,."), m.group("j").strip()
    # Not a known journal: the capitalised words after the title's last full stop (an
    # abbreviated journal's words end with "." themselves: "Theor. Chem. Acc.").
    words = before.split(" ")
    abbreviation = re.compile(rf"[{_UP}][a-z]{{0,5}}\.")
    k = len(words)                  # the journal is words[k:]
    while k > 0:
        w = words[k - 1]
        if not (abbreviation.fullmatch(w) or re.fullmatch(rf"[{_UP}][{_LOW}A-Za-z]*\.?", w)):
            break
        k -= 1
        if k and words[k - 1].endswith((".", "?", "!")) and \
                not abbreviation.fullmatch(words[k - 1]):
            break                   # (the title's last word)
    if k == len(words) or (k and not words[k - 1].endswith((".", "?", "!"))):
        return before.strip(" ,."), ""          # (no full stop before it: all title)
    return " ".join(words[:k]).strip(" ,."), " ".join(words[k:]).strip(" ,")


def _acs(t: str) -> dict | None:
    """"Still, W. C.; Kahn, M.; Mitra, A. Title. J. Org. Chem. 1978, 43 (14), 2923–2925"."""
    m = re.match(rf"(?P<authors>{_AUTHOR_SF}(?:{_SEP}{_AUTHOR_SF})*)"
                 rf"(?:\s?(?:et\s+al\.))?[,.]?\s+(?P<rest>\S.*)$", t)
    if not m:
        return None
    authors = [a.strip(" ,;") for a in re.findall(_AUTHOR_SF, m.group("authors"))]
    rest = m.group("rest")
    year_first = re.match(rf"\((?P<year>{_YEAR})[a-z]?\)[.,]?\s*(?P<rest>.*)$|"
                          rf"(?P<year2>{_YEAR})[a-z]?[.,]\s*(?P<rest2>.*)$", rest)
    if year_first:      # "Surname, I. (2004), Title. Journal 7: 1-9" (Wiley Online, Cell…)
        year = year_first.group("year") or year_first.group("year2")
        after = year_first.group("rest") if year_first.group("year") else year_first.group("rest2")
        tail = re.search(r"(?P<before>.+?),?\s(?P<volume>\d+)(?:\s?\((?P<issue>[^)]+)\))?"
                         rf"\s?[:,]\s?(?P<pages>{_PAGES})$", after)
        if tail:
            title, journal = _split_title_journal(tail.group("before"))
            return {"authors": authors, "title": title, "journal": journal, "year": year,
                    "volume": tail.group("volume"), "issue": tail.group("issue") or "",
                    "pages": tail.group("pages")}
        return {"authors": authors, "year": year, "title": after.strip(" ."), "type": "misc"}
    tail = _tail(rest)
    if tail is None:
        return None                      # (perhaps Nature's style: tried next)
    title, journal = _split_title_journal(tail.pop("journal"))
    return {"authors": authors, "title": title, "journal": journal, **tail}


def _initials_first(t: str) -> dict | None:
    """RSC / Wiley / Elsevier: "W. C. Still, M. Kahn and A. Mitra, J. Org. Chem., 1978,
    43, 2923." (usually no title)."""
    m = re.match(rf"(?P<authors>{_AUTHOR_IF}(?:{_SEP}{_AUTHOR_IF})*)"
                 rf"(?:\s?(?:et\s+al\.))?\s?[,.:]\s*(?P<rest>\S.*)$", t)
    if not m:
        return None
    authors = []
    for a in re.findall(_AUTHOR_IF, m.group("authors")):   # "W. C. Still" → "Still, W. C."
        name = re.match(rf"(?P<given>{_INITIALS})\s?(?P<family>.+)$", a.strip())
        authors.append(f"{name.group('family').strip()}, {name.group('given').strip()}"
                       if name else a.strip())
    tail = _tail(m.group("rest"))
    if tail is None:
        return None
    title, journal = _split_title_journal(tail.pop("journal"))
    if not journal:                      # (no title in these styles: it is the journal)
        journal, title = title, ""
    return {"authors": authors, "title": title, "journal": journal, **tail}


def _nature(t: str) -> dict | None:
    """Nature: "Still, W. C., Kahn, M. & Mitra, A. Title. J. Org. Chem. 43, 2923–2925
    (1978)"."""
    m = re.search(rf"(?P<before>.*?)\s(?P<volume>\d+),\s?(?P<pages>{_PAGES})\s?\((?P<year>{_YEAR})\)$", t)
    if not m:
        return None
    head = re.match(rf"(?P<authors>{_AUTHOR_SF}(?:{_SEP}{_AUTHOR_SF})*)\s+(?P<rest>\S.*)$",
                    m.group("before"))
    if not head:
        return None
    authors = [a.strip() for a in re.findall(_AUTHOR_SF, head.group("authors"))]
    title, journal = _split_title_journal(head.group("rest"))
    return {"authors": authors, "title": title, "journal": journal, "year": m.group("year"),
            "volume": m.group("volume"), "pages": m.group("pages")}


def read_list(texts: list[str]) -> list[ImportedReference]:
    """The reference list's paragraphs as imported references, each parsed, with a new ID."""
    return [ImportedReference(number, text, parse_reference(text))
            for number, text in split_entries(texts)]


# ---------------------------------------------------------------------- citations

def citation_map(found: list[ImportedReference], ids: dict[int, str]) -> dict[str, list[str]]:
    """{number as cited: [reference IDs]} — "3" cites all of 3a, 3b…; `ids` gives each
    kept imported reference's ID in the project (by its index in `found`)."""
    out: dict[str, list[str]] = {}
    for k, r in enumerate(found):
        rid = ids.get(k)
        if not rid or not r.number:
            continue
        out.setdefault(r.number, []).append(rid)
        m = re.fullmatch(r"(\d+)[a-z]", r.number)
        if m:
            out.setdefault(m.group(1), []).append(rid)
    return out


def _numbers(spec: str) -> list[str] | None:
    """"3,4" → ["3", "4"]; "3–5" → 3, 4, 5; "3a" → ["3a"]; None if it is not that."""
    out: list[str] = []
    for part in re.split(r"\s*,\s*", spec.strip()):
        r = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if r:
            a, b = int(r.group(1)), int(r.group(2))
            if not 0 < b - a < 50:
                return None
            out += [str(i) for i in range(a, b + 1)]
        elif re.fullmatch(r"\d+[a-z]?", part):
            out.append(part)
        else:
            return None
    return out or None


# Words a superscript number after which is not a citation: units (m^{2}, Hz^{2}),
# substituent labels and groups (R^{1}, Ar^{2}), single letters; isotopes are told by
# what follows (^{13}C).
_NOT_BEFORE = re.compile(
    r"(?:(?<![A-Za-z])(?:[A-Za-z]|[a-zµμ]?m|[kM]?Hz|[kmµμ]?[gL]|[mµμ]?mol|[mµ]?M|min|cm|mm|nm|"
    r"Å|eV|kJ|kcal|cal|bar|Pa|ppm|Ar|Het|Nu|PG|Ln|Me|Et|Pr|Bu|Ph|Bn|Ts|Ms|Tf|Ac|Bz|Boc|Cbz|"
    r"Si|Sn|Ge|Pd|Pt|Rh|Ir|Ru|Cu|Ni|Co|Fe|Zn|Mg|Li|Na|Cs|Al|Ti|Zr)\s?)$")


def link_citations(markup: str, numbers: dict[str, list[str]]) -> str:
    """Numbered citations in imported text (stored text format) as the program's
    citations of those references (see the module's text)."""
    if not numbers or not markup:
        return markup

    def cite(spec: str) -> str | None:
        found = _numbers(spec)
        if not found or any(n not in numbers for n in found):
            return None
        ids: list[str] = []
        for n in found:
            for rid in numbers[n]:
                if rid not in ids:
                    ids.append(rid)
        return "@cite{" + ",".join(ids) + "}"

    def sup(m: re.Match) -> str:
        before = markup[:m.start()]
        after = markup[m.end():m.end() + 1]
        if not before or before[-1].isspace() or _NOT_BEFORE.search(before) or \
                (after and (after.isupper() or after.isdigit())):
            return m.group(0)              # (an isotope "^{13}C", a unit, R^{1}, a charge…)
        return cite(m.group(1)) or m.group(0)

    def bracket(m: re.Match) -> str:
        return cite(m.group(1)) or m.group(0)

    out = re.sub(r"\^\{(\d+[a-z]?(?:\s*[,–—-]\s*\d+[a-z]?)*)\}", sup, markup)
    out = re.sub(r"\s?\[(\d+[a-z]?(?:\s*[,–—-]\s*\d+[a-z]?)*)\]", bracket, out)
    return out
