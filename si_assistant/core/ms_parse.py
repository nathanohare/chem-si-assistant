"""Reading compound labels and values out of manuscript text (SPEC §13o).

Papers print compound IDs with their yields, ee/er and dr under the structures of a
scope scheme ("3a" and, on the line below, "92%, 95% ee"; or "3a, 92%, 95% ee" on one
line), in scope tables (Entry / Product / Yield / ee columns) and in the body text. This
module turns text with positions (from a PDF, a Word figure or a ChemDraw file, see
core/ms_read.py) into labelled values.

Ported from the chem-vision skill (scripts/annotations.py), by Nathan O'Hare:
    Copyright (c) 2026 nathanohare — MIT License (see THIRD_PARTY_NOTICES.md).
Adapted here: spans come from pypdf, the program's EMF reader or CDXML instead of
PyMuPDF; footnote markers set as superscripts are kept as "^b"; values are tied to an ID
printed *above* them (labels sit under the structures); and the extraction of reaction
conditions (times, temperatures, equivalents…) used to check footnotes against the SI.

Coordinates everywhere: points (or any unit), x to the right, y DOWN (top of the page = 0).
Nothing here needs Qt, so it is tested directly.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

# Unicode variants papers use for minus, colon, primes, spaces.
_NORM = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-", "：": ":",
                       "∶": ":", " ": " ", " ": " ", " ": " ", " ": " ",
                       " ": " ", "′": "'", "’": "'", "‘": "'", "º": "°", "˚": "°"})

NUM = r"\d{1,3}(?:\.\d+)?"
RATIO = rf"(?:[<>≥≤]\s*)?{NUM}\s*:\s*{NUM}"

# Order matters: specific patterns first, then the generic bare percentage. Units that
# collide with compound labels (3h, 8g, 1d) need a space before the unit: "24 h" is a
# time, "3h" is a compound.
PATTERNS = [
    ("ee", rf"(?P<v>[<>≥≤]?\s*{NUM})\s*%\s*ee\b"),
    ("ee", rf"\bee\s*(?:[=:]|of)?\s*(?P<v>[<>≥≤]?\s*{NUM})\s*%"),
    ("ee", rf"enantiomeric\s+excess(?:\s+of)?\s*(?P<v>[<>≥≤]?\s*{NUM})\s*%"),
    ("de", rf"(?P<v>[<>≥≤]?\s*{NUM})\s*%\s*de\b"),
    ("er", rf"(?P<v>{RATIO})\s*(?:e\.?r\.?|er)\b"),
    ("er", rf"\b(?:e\.?r\.?|er)\s*[=:]?\s*(?P<v>{RATIO})"),
    ("dr", rf"(?P<v>{RATIO})\s*(?:d\.?r\.?|dr)\b"),
    ("dr", rf"\b(?:d\.?r\.?|dr)\s*[=:]?\s*(?P<v>{RATIO})"),
    ("rr", rf"(?P<v>{RATIO})\s*(?:r\.?r\.?|rr)\b"),
    ("rr", rf"\b(?:r\.?r\.?|rr)\s*[=:]?\s*(?P<v>{RATIO})"),
    ("ez", rf"(?P<v>{RATIO})\s*(?:E\s*/\s*Z|Z\s*/\s*E)\b"),
    ("ez", rf"\b(?:E\s*/\s*Z|Z\s*/\s*E)\s*[=:]?\s*(?P<v>{RATIO})"),
    ("conversion", rf"(?P<v>{NUM})\s*%\s*conv(?:ersion|\.)?"),
    ("conversion", rf"\bconv(?:ersion|\.)?\s*(?:\(%\))?\s*[=:]?\s*(?P<v>{NUM})\s*%"),
    ("yield_nmr", rf"(?P<v>{NUM})\s*%\s*(?:\(\s*)?(?:NMR|1H NMR|GC|HPLC)\s*(?:yield)?\)?"),
    ("yield", rf"(?P<v>[<>≥≤]?\s*{NUM})\s*%\s*(?:isolated\s+)?yield"),
    ("yield", rf"\byield\s*(?:[=:]|of)?\s*(?P<v>{NUM})\s*%"),
    ("loading", r"(?P<v>\d+(?:\.\d+)?)\s*mol\s*%"),
    ("temperature", r"(?P<v>-?\d{1,3})\s*°\s*C\b"),
    ("temperature", r"\b(?P<v>rt|r\.t\.|room temperature|reflux)\b"),
    ("time", r"(?<![\w.-])(?P<v>\d+(?:\.\d+)?)\s*(?P<u>min|hr|hrs)\b"),
    ("time", r"(?<![\w.-])(?P<v>\d+(?:\.\d+)?)\s+(?P<u>h|days?)\b"),
    ("scale", r"(?<![\w.-])(?P<v>\d+(?:\.\d+)?)\s*(?P<u>mmol|mg)\b"),
    ("scale", r"(?<![\w.-])(?P<v>\d+(?:\.\d+)?)\s+(?P<u>mol|g)\b(?!\s*%)"),
]
_CASE_SENSITIVE = ("ez", "time", "scale", "temperature")
_COMPILED = [(k, re.compile(p, 0 if k in _CASE_SENSITIVE else re.I)) for k, p in PATTERNS]
_PCT = re.compile(rf"(?P<v>[<>≥≤]?\s*{NUM})\s*%")

# Footnote markers: "90%a", "8k[c]", "8k^c", "[a]" -> captured, then removed. ("^c" is
# how this program writes a marker that was set as a superscript.)
# (A ring-fusion letter is not a marker: "benzo[b]thiophene" has a letter right after it.)
_FOOT = re.compile(r"(?<=%)(?P<f>[a-h])\b|\[(?P<g>[a-h])\](?![A-Za-z])|\^(?P<h>[a-h])(?![a-z])")

# Compound labels: 3, 3a, 12b', S1, 4aa, (±)-5, ent-7, (R)-3b, (S,S)-3b, (1R,2S)-3b,
# cis-5a, trans-5h
_DESCRIPTOR = r"\d*[RSEZ]\*?"
ID_PREFIX = (rf"(?:\((?:[±+-]|{_DESCRIPTOR}(?:,\s*{_DESCRIPTOR})*|rac|rel)\)-|ent-|rac-|rel-|"
             r"cis-|trans-|meso-|syn-|anti-)")
ID_RE = re.compile(rf"^{ID_PREFIX}?(?:S|SI-)?\d{{1,3}}[a-z]{{0,3}}'*$")
_NOT_ID_BEFORE = re.compile(r"(?i)(table|scheme|figure|fig\.?|entry|entries|ref\.?|refs\.?|"
                            r"eq\.?|equiv\.?|step|chart|page|ca\.?|n\s*=|section|sections)$")


def normalize(text: str) -> str:
    return str(text or "").translate(_NORM)


def id_key(label: str) -> str:
    """How compound IDs are compared: capitals, spaces and the kind of prime don't count
    ("3A" = "3a", "12b’" = "12b'"). A stereo prefix does ("cis-5a" and "trans-5a" are two
    compounds); id_base() gives the ID without it."""
    return re.sub(r"\s+", "", normalize(label)).casefold()


def id_base(label: str) -> str:
    """The ID without a stereo prefix ("(±)-5" → "5", "(S,S)-3b" → "3b", "cis-5a" → "5a"):
    used only when no SI compound has the full ID."""
    return re.sub(rf"^{ID_PREFIX}", "", id_key(label), flags=re.I)


def id_without_rac(label: str) -> str:
    """The ID key without a prefix that only says racemic or "the enantiomer": "(±)-5",
    "rac-5", "ent-5" → "5"."""
    return re.sub(r"^(?:\((?:±|\+/-|rac)\)-|rac-|ent-)", "", id_key(label))


def split_footnotes(text: str) -> tuple[str, list[str]]:
    """Remove footnote markers, returning (clean text, [markers])."""
    notes: list[str] = []

    def grab(m):
        notes.append(m.group("f") or m.group("g") or m.group("h"))
        return ""

    return _FOOT.sub(grab, text), notes


def parse_values(text: str) -> dict:
    """Labelled values from a snippet like '3a, 92%, 95:5 er'. A percentage with no
    qualifier is taken as the yield (the usual convention in scope schemes) and flagged,
    but only in short label-like snippets: in running text a bare % is too ambiguous."""
    t = normalize(text)
    found: dict = {}
    spans: list[tuple[int, int]] = []

    def taken(a, b):
        return any(a < y and b > x for x, y in spans)

    # A parenthesised second result, e.g. "97% (58%: Pd = 3 mol %)", is kept whole as an
    # alternative instead of leaking its values into this entry.
    for m in re.finditer(r"\((?=[^()]*%)[^()]*\)", t):
        inner = m.group(0)[1:-1]
        if re.fullmatch(rf"\s*{NUM}\s*%\s*", inner) and not _PCT.search(t[:m.start()]):
            continue  # "(77%)" alone is the value itself
        if _only_selectivity(inner):
            continue  # "92% (95% ee)": the ee of this same result, not another result
        if _PCT.search(t[:m.start()]):
            found.setdefault("alternative_results", []).append(inner.strip())
            spans.append(m.span())

    for key, rx in _COMPILED:
        for m in rx.finditer(t):
            if taken(*m.span()):
                continue
            v = re.sub(r"\s+", "", m.group("v"))
            if "u" in rx.groupindex and m.group("u"):
                v = f"{v} {m.group('u')}"
            elif key == "temperature" and v[-1].isdigit():
                v = f"{v} °C"
            elif key == "loading":
                v = f"{v} mol%"
            found.setdefault(key, v)
            spans.append(m.span())
    labelish = len(t.split()) <= 8
    for m in _PCT.finditer(t):
        if taken(*m.span()):
            continue
        v = re.sub(r"\s+", "", m.group("v"))
        if "yield" not in found and labelish:
            found["yield"] = v
            found["yield_assumed"] = True  # a bare % taken as the yield
        else:
            found.setdefault("other_percent", []).append(v)
        spans.append(m.span())
    if "er" in found and "ee" not in found:
        ee = er_to_ee(found["er"])
        if ee is not None:
            found["ee_from_er"] = ee
    return found


_SELECTIVITY = [rx for k, rx in _COMPILED if k in ("ee", "de", "er", "dr", "rr", "ez")]


def _only_selectivity(inner: str) -> bool:
    """Does a bracket hold only an ee/er/dr ("95% ee", "90% ee, 10:1 dr")?"""
    rest = inner
    for rx in _SELECTIVITY:
        rest = rx.sub(" ", rest)
    return rest != inner and not re.search(r"[\d%]", rest)


def er_to_ee(ratio: str) -> float | None:
    """"97:3" → 94.0 (the ee of the major enantiomer); None if it isn't a ratio."""
    try:
        a, b = (float(x) for x in re.sub(r"[<>≥≤\s]", "", normalize(ratio)).split(":"))
    except ValueError:
        return None
    if a + b <= 0:
        return None
    return round(abs(a - b) / (a + b) * 100, 1)


def find_ids(text: str) -> list[str]:
    """Candidate compound labels in free text (tokens shaped like 3a, 12, S4)."""
    out = []
    # (Commas inside a stereo prefix such as "(S,S)-3b" or "(1R, 2S)-3b" don't split it.)
    text = re.sub(rf"\({_DESCRIPTOR}(?:\s*,\s*{_DESCRIPTOR})+\)-",
                  lambda m: re.sub(r"\s*,\s*", "\x00", m.group(0)), normalize(text))
    toks = [t.replace("\x00", ",") for t in re.split(r"[\s,;/]+", text) if t]
    for n, raw in enumerate(toks):
        if n and _NOT_ID_BEFORE.search(toks[n - 1]):
            continue
        if re.fullmatch(r"\(\d+\)", raw):  # "(4)": an equation number
            continue
        if re.fullmatch(r"\d+\.", raw):  # "2. H2O": a numbered step
            continue
        tok = raw.rstrip(".,;:")
        tok = tok.strip("()[]") if not (tok.startswith("(") and ")-" in tok) else tok
        if tok and ID_RE.match(tok) and not re.fullmatch(r"\d{4}", tok):
            out.append(tok)
    return out


def rank_ids(ids: list[str], bold=()) -> list[str]:
    """Most label-like first: bold, then letter-suffixed (3c), then bare."""
    ids = list(dict.fromkeys(ids))

    def score(i):
        return (i not in bold, not re.search(r"\d[a-z]", i), ids.index(i))
    return sorted(ids, key=score)


def annotate_text(text: str) -> dict:
    """{"text", "ids": [...], values…, "footnotes": [...]} for one snippet."""
    clean, notes = split_footnotes(normalize(text))
    vals = parse_values(clean)
    id_text = clean
    if "alternative_results" in vals:   # IDs inside "(40% recovered 8c)" aren't the label
        id_text = re.sub(r"\((?=[^()]*%)[^()]*\)", " ", clean)
    ids = find_ids(id_text)
    # Drop tokens that are really numbers inside values ("95" of "95:5 er").
    value_nums = set(re.findall(r"\d+(?:\.\d+)?", " ".join(
        str(v) for k, v in vals.items() if k != "yield_assumed")))
    ids = [i for i in ids if i not in value_nums]
    if len(clean.split()) > 8:  # running text: bare numbers are rarely labels
        ids = [i for i in ids if not i.isdigit()]
    rec = {"text": text, "ids": rank_ids(ids), **vals}
    if notes:
        rec["footnotes"] = notes
    return rec


def single_label(text: str, ids: list[str]) -> str:
    """The one compound label of a label line that also holds other numbers: "3a, n = 2",
    "3a (from 1b)", "3a, 92%, 95% ee (2 steps)". IDs inside brackets are remarks; of the
    rest, the only one with a letter suffix is the label. "" if it can't be told."""
    clean = split_footnotes(normalize(text))[0]
    outside = [i for i in find_ids(re.sub(r"\([^()]*\)", " ", clean)) if i in ids]
    for pool in (outside, ids):
        lettered = [i for i in dict.fromkeys(pool) if re.search(r"\d[a-z]", i)]
        if len(lettered) == 1:
            return lettered[0]
    return ""


VALUE_KEYS = ("yield", "yield_nmr", "ee", "er", "dr", "conversion", "de", "rr", "ez")


def has_values(rec: dict) -> bool:
    return any(k in rec for k in VALUE_KEYS)


# ---------------------------------------------------------------------- spans and lines

@dataclass
class Span:
    """A piece of text with its box (x0, y0, x1, y1; y down)."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool = False
    script: bool = False     # a superscript/subscript (smaller or raised)

    @property
    def bbox(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


_MARKER_TEXT = re.compile(r"^\[?([a-h](?:\s*,\s*[a-h])*)\]?$")


def marker_letters(text: str) -> list[str]:
    """"b" → ["b"], "a,b" → ["a", "b"], "[c]" → ["c"]; [] for anything else."""
    m = _MARKER_TEXT.match(text.strip())
    return re.findall(r"[a-h]", m.group(1)) if m else []


def mark_scripts(spans: list[Span]) -> list[Span]:
    """Flag spans set smaller than the text beside them as scripts (superscript footnote
    letters, subscript digits): the PDF has no other way of saying so."""
    buckets: dict[int, list[Span]] = {}
    for s in spans:
        buckets.setdefault(int(((s.y0 + s.y1) / 2) // 4), []).append(s)
    for s in spans:
        cy = (s.y0 + s.y1) / 2
        b = int(cy // 4)
        big = s.size
        for k in range(b - 2, b + 3):
            for o in buckets.get(k, ()):
                if o is s or abs((o.y0 + o.y1) / 2 - cy) >= 0.7 * max(o.size, s.size):
                    continue
                if min(abs(o.x0 - s.x1), abs(s.x0 - o.x1)) < 1.5 * max(o.size, s.size):
                    big = max(big, o.size)
        if s.size < 0.8 * big:
            s.script = True
    return spans


def group_lines(spans: list[Span], ygap: float = 2.5) -> list[dict]:
    """Merge spans sitting on the same baseline into lines (ChemDraw text often arrives
    as one span per run: '3a', ', ', '92%', ...). Sub/superscripts join the text they
    belong to without a space (BF3K, 90%^a); a superscript footnote letter becomes "^a".
    A gap wider than about one character height starts a new line, so table columns and
    labels side by side stay apart. Lines: {"text", "bbox", "size", "bold_parts",
    "starts_with_marker"}."""
    spans = [s for s in spans if s.text.strip()]
    if not spans:
        return []
    groups: list[dict] = []     # {"spans": [...], "bbox": [...], "size"}
    for s in sorted((s for s in spans if not s.script), key=lambda s: (round(s.y1, 1), s.x0)):
        home = None
        for g in reversed(groups):
            b = g["bbox"]
            last = g["spans"][-1]
            # (Text widths in a PDF are sometimes only estimated: a piece that seems to
            # overlap the one before it a little still follows it on the line.)
            if abs(b[3] - s.y1) < ygap and s.x0 - b[2] < 1.0 * max(s.size, g["size"]) and                     (s.x0 - b[2] >= -1.5 or s.x0 >= last.x0 + 0.6 * (last.x1 - last.x0)):
                home = g
                break
            if b[3] < s.y1 - 3 * max(s.size, 1):
                break   # (sorted by height: no line further up can take it)
        if home is None:
            groups.append({"spans": [s], "bbox": [s.x0, s.y0, s.x1, s.y1], "size": s.size})
        else:
            b = home["bbox"]
            home["spans"].append(s)
            b[2] = max(b[2], s.x1)
            b[1], b[3] = min(b[1], s.y0), max(b[3], s.y1)
    for s in (s for s in spans if s.script):
        mid = (s.y0 + s.y1) / 2
        best = None
        for g in groups:
            b = g["bbox"]
            if b[1] - 1.5 <= mid <= b[3] + 1.5 and b[0] - g["size"] <= s.x0 <= b[2] + 0.6 * g["size"]:
                if best is None or b[0] > best["bbox"][0]:
                    best = g   # (the line that starts nearest before it)
        if best is None:
            groups.append({"spans": [s], "bbox": [s.x0, s.y0, s.x1, s.y1], "size": s.size})
        else:
            best["spans"].append(s)
            best["bbox"][0] = min(best["bbox"][0], s.x0)
            best["bbox"][2] = max(best["bbox"][2], s.x1)
    lines = []
    for g in groups:
        text, bold, first = "", [], True
        prev = None
        starts_marker = False
        for s in sorted(g["spans"], key=lambda s: s.x0):
            piece = s.text.strip()
            letters = marker_letters(piece) if s.script else []
            if letters:
                piece = "".join(f"^{x}" for x in letters)
            if first:
                starts_marker = bool(letters)
                text = piece
            else:
                gap = s.x0 - prev.x1
                glue = "" if (s.script or text.endswith(("(", "-", "/", "^"))
                              or (gap < 0.12 * s.size and not s.text[:1].isspace())
                              or piece[:1] in ",.;:)%") else " "
                if prev.script and not s.script and piece[:1].isalnum() and \
                        not (prev.text.strip() and marker_letters(prev.text)):
                    glue = ""   # (after a subscript: K3PO4 set as K, 3, PO, 4)
                text += glue + piece
            if s.bold and not s.script:
                bold.append(s.text.strip())
            first = False
            prev = s
        lines.append({"text": re.sub(r"\s+", " ", text).strip(), "bbox": list(g["bbox"]),
                      "size": g["size"], "bold_parts": bold, "starts_with_marker": starts_marker})
    lines.sort(key=lambda L: (L["bbox"][1], L["bbox"][0]))
    return lines


# ---------------------------------------------------------------------- tables

_HEADER_KW = re.compile(r"(?i)^(entry|run|yield|ee|er|dr|d\.r\.|e\.r\.|conv|time|t\s*\(|temp|"
                        r"solvent|catalyst|cat\.|ligand|base|additive|substrate|product|"
                        r"selectivity|ratio|aryl|alkyne|alkene|amine|ketone|aldehyde|"
                        r".*\(%\)|.*mol\s*%|[A-Z]?\d?\s*/\s*\d)")
_FIELD = [("entry", r"(?i)^(entry|run)"), ("product_id", r"(?i)^product"),
          ("substrate_id", r"(?i)^(aryl|substrate|starting|alkyne|alkene|amine|ketone|"
                           r"aldehyde|reactant|sm\b)"),
          ("yield", r"(?i)yield"), ("ee", r"(?i)^ee[a-h]?\b|\bee[a-h]?\s*\(|e\.e\."),
          ("er", r"(?i)^er[a-h]?\b|\ber[a-h]?\s*\(|e\.r\."),
          ("dr", r"(?i)^dr[a-h]?\b|\bdr[a-h]?\s*\(|d\.r\."),
          ("conversion", r"(?i)conv"), ("time", r"(?i)^time|^t\s*\((h|min)"),
          ("temperature", r"(?i)^temp|^T\s*\(°"), ("loading", r"(?i)mol\s*%")]


def header_field(h: str) -> str:
    h = split_footnotes(h.strip())[0]
    h = re.sub(r"(?<=\))[a-h]$|(?<=%)[a-h]$", "", h.strip())  # footnote marks
    for name, rx in _FIELD:
        if re.search(rx, h):
            return name
    return h


def _rows_by_y(lines, tol=3.0):
    rows = []
    for L in sorted(lines, key=lambda L: L["bbox"][1]):
        cy = (L["bbox"][1] + L["bbox"][3]) / 2
        if rows and abs(cy - rows[-1]["cy"]) < tol:
            rows[-1]["lines"].append(L)
        else:
            rows.append({"cy": cy, "lines": [L]})
    return rows


def find_tables(lines: list[dict]) -> list[dict]:
    """Text-layer tables: a header row with at least three column titles, at least two
    of them results/condition keywords, then body rows anchored on the first column
    (usually Entry). Returns {"columns", "rows": [{"cells", "fields", "bbox"}], "bbox"}
    and marks the lines used (line["in_table"] = True)."""
    tables = []
    ys = _rows_by_y(lines)
    used_rows: set[int] = set()
    for i, hdr in enumerate(ys):
        if i in used_rows:
            continue
        cells = sorted((L for L in hdr["lines"] if not marker_letters(L["text"])),
                       key=lambda L: L["bbox"][0])  # lone footnote letters are not columns
        if len(cells) < 3 or sum(bool(_HEADER_KW.match(split_footnotes(c["text"])[0]))
                                 for c in cells) < 2:
            continue
        if not any(header_field(c["text"]) in ("yield", "ee", "er", "dr", "conversion", "entry")
                   for c in cells):
            continue
        x_starts = [c["bbox"][0] for c in cells]
        x_end = max(c["bbox"][2] for c in cells) + 40
        names = [c["text"] for c in cells]

        def col_of(L, x_starts=x_starts):
            cx = L["bbox"][0]
            return max([k for k, x in enumerate(x_starts) if x <= cx + 4] or [0])

        body, last_y = [], hdr["cy"]
        for j, row in enumerate(ys[i + 1:], start=i + 1):
            if row["cy"] - last_y > 45:
                break
            wide = [L for L in row["lines"]
                    if L["bbox"][2] - L["bbox"][0] > 0.6 * (x_end - x_starts[0])]
            if wide or any(re.match(r"(?i)^(table|scheme|figure)\b", L["text"])
                           for L in row["lines"]):
                break
            if sum(bool(_HEADER_KW.match(c["text"])) for c in row["lines"]) >= 3:
                break  # the next table's header
            body.append((j, row))
            last_y = row["cy"]
        entry_col = header_field(names[0]) == "entry"
        kept = []
        for j, row in body:
            first = [L for L in row["lines"] if col_of(L) == 0 and x_starts[0] - 10 <= L["bbox"][0]]
            if entry_col and first and not re.fullmatch(
                    r"\d{1,3}[a-h]?", split_footnotes(first[0]["text"].strip())[0].strip()):
                break   # a footnote or body text ends the table
            kept.append((j, row))
        cells_in = [L for _j, row in kept for L in row["lines"]
                    if x_starts[0] - 10 <= L["bbox"][0] <= x_end]
        anchors = sorted((L for L in cells_in if col_of(L) == 0), key=lambda L: L["bbox"][1])
        out_rows = [{"cells": {names[0]: L["text"]}, "bbox": list(L["bbox"]),
                     "cy": (L["bbox"][1] + L["bbox"][3]) / 2} for L in anchors]
        for L in sorted(cells_in, key=lambda L: (L["bbox"][1], L["bbox"][0])):
            j = col_of(L)
            if j == 0 or not out_rows:
                continue
            cy = (L["bbox"][1] + L["bbox"][3]) / 2
            r = min(out_rows, key=lambda r: abs(r["cy"] - cy))
            key = names[j]
            r["cells"][key] = (r["cells"][key] + " " + L["text"]) if key in r["cells"] else L["text"]
            r["bbox"] = [min(r["bbox"][0], L["bbox"][0]), min(r["bbox"][1], L["bbox"][1]),
                         max(r["bbox"][2], L["bbox"][2]), max(r["bbox"][3], L["bbox"][3])]
        for r in out_rows:
            r.pop("cy")
            r["fields"] = row_fields(r["cells"])
        if out_rows:
            for c in hdr["lines"]:
                c["in_table"] = True
            for L in cells_in:
                L["in_table"] = True
            used_rows.update(j for j, _row in kept)
            bottom = max(r["bbox"][3] for r in out_rows)
            tables.append({"columns": names, "rows": out_rows,
                           "bbox": [x_starts[0], min(c["bbox"][1] for c in cells), x_end,
                                    bottom]})
    return tables


def row_fields(cells: dict[str, str]) -> dict:
    """A table row's cells by meaning: {"entry", "product_id", "yield", "ee", …,
    "footnotes": [...]} (the column titles' footnote marks are not the row's)."""
    parsed: dict = {}
    for k, v in cells.items():
        f = header_field(k)
        clean, notes = split_footnotes(normalize(v))
        vv = clean.strip()
        if f in ("yield", "ee", "conversion"):
            m = re.search(r"[<>≥≤]?\s*\d+(?:\.\d+)?", vv)
            vv = re.sub(r"\s+", "", m.group(0)) if m else vv
        if f in ("er", "dr"):
            m = re.search(RATIO, vv)
            vv = re.sub(r"\s+", "", m.group(0)) if m else vv
        if f in ("product_id", "substrate_id"):
            ids = rank_ids(find_ids(vv))
            vv = ids[0] if ids else vv
        parsed[f] = vv
        if notes:
            parsed.setdefault("footnotes", []).extend(notes)
    return parsed


QUANT = "quant."      # a quantitative yield, as the tables write it
RACEMIC = "rac"       # racemic: 0% ee
_QUANT_RE = re.compile(r"(?i)^quant(?:\.|itative)?$")
_RACEMIC_RE = re.compile(r"(?i)^rac(?:\.|emic)?$")


def is_quant(text) -> bool:
    """Is it a quantitative yield written as a word ("quant.", "quant", "quantitative")?"""
    return bool(_QUANT_RE.match(normalize(str(text or "")).strip()))


def is_racemic(text) -> bool:
    """Is it "rac", "rac." or "racemic" (0% ee)?"""
    return bool(_RACEMIC_RE.match(normalize(str(text or "")).strip()))


def _cell_value(kind: str, text) -> str:
    """A yield/ee/er/dr/conversion cell as a value: a number or ratio as it is, a
    quantitative yield as "quant.", racemic as "rac"; "" for a cell with no value
    ("–", "n.d.", "trace", "nr")."""
    text = str(text or "").strip()
    if re.search(r"\d", text):
        return text
    if kind in ("yield", "conversion") and is_quant(text):
        return QUANT
    if kind in ("ee", "er") and is_racemic(text):
        return RACEMIC
    return ""


def table_records(table: dict) -> list[dict]:
    """One record per row with a product ID: {"ids": [id], "yield", "ee", "er", "dr",
    "footnotes", "entry"}. Rows whose product ID is repeated in the same table (an
    optimisation table) only count as mentions: their yields are not this compound's."""
    rows = [r["fields"] for r in table["rows"]]
    ids = [f.get("product_id", "") for f in rows]
    out = []
    for f in rows:
        pid = f.get("product_id", "")
        if not pid or not ID_RE.match(pid):
            continue
        rec = {"ids": [pid], "text": " | ".join(f"{k}: {v}" for k, v in f.items()
                                                if k != "footnotes"),
               "entry": f.get("entry", "")}
        if ids.count(pid) == 1:
            for k in ("yield", "ee", "er", "dr", "conversion"):
                v = _cell_value(k, f.get(k))
                if v == RACEMIC and k == "er":
                    rec.setdefault("ee", v)     # (shown and compared as an ee: 0%)
                elif v:
                    rec[k] = v
            if rec.get("er") and not rec.get("ee"):
                ee = er_to_ee(rec["er"])
                if ee is not None:
                    rec["ee_from_er"] = ee
        else:
            rec["repeated"] = True
        if f.get("footnotes"):
            rec["footnotes"] = list(dict.fromkeys(f["footnotes"]))
        out.append(rec)
    return out


# ---------------------------------------------------------------------- labels under structures

def _overlap_x(a, b) -> float:
    """How much two boxes overlap left to right (negative: the gap between them)."""
    return min(a[2], b[2]) - max(a[0], b[0])


def _centre_inside(inner, outer, slack: float = 0.0) -> bool:
    cx = (inner[0] + inner[2]) / 2
    return outer[0] - slack <= cx <= outer[2] + slack


_CAPTION_START = re.compile(r"(?i)^(scheme|table|figure|fig\.|chart)\s*S?\d")


def mark_body(lines: list[dict]) -> None:
    """Mark the lines of body-text paragraphs (line["body"] = True) so that they are not
    taken for figure labels. Justified text with wide word spaces arrives in pieces
    ("3c the selectivity was only", "80%,"), which could look like a label and a value.
    A line of 9 or more words is body text, and so are three or more lines of 4+ words
    one under the other (a narrow column); then also a piece right beside body text on
    the same row, and every line just above or below one (at the spacing of lines in a
    paragraph) that overlaps it left to right. Footnotes, tables and captions are left
    alone."""
    def cy(L):
        return (L["bbox"][1] + L["bbox"][3]) / 2

    def skip(L):
        return L.get("footnote") or L.get("in_table") or _CAPTION_START.match(L["text"])

    def next_row(L, B):
        size = max(B.get("size", 10.0), 1.0)
        return 0.5 * size <= abs(cy(L) - cy(B)) <= 2.0 * size and \
            _overlap_x(L["bbox"], B["bbox"]) > 0

    def same_row(L, B):
        size = max(B.get("size", 10.0), 1.0)
        return abs(cy(L) - cy(B)) < 0.5 * size and \
            min(abs(L["bbox"][0] - B["bbox"][2]), abs(B["bbox"][0] - L["bbox"][2])) < 2 * size

    free = [L for L in lines if not skip(L)]
    todo = [L for L in free if len(L["text"].split()) >= 9]
    # Narrow columns: three or more lines of 4+ words, one under the other.
    wordy = [L for L in free if len(L["text"].split()) >= 4]
    seen: set[int] = set()
    for L in wordy:
        if id(L) in seen:
            continue
        group, stack = [], [L]
        seen.add(id(L))
        while stack:
            A = stack.pop()
            group.append(A)
            for B in wordy:
                if id(B) not in seen and next_row(B, A):
                    seen.add(id(B))
                    stack.append(B)
        if len(group) >= 3:
            todo += group
    for L in todo:
        L["body"] = True
    while todo:
        B = todo.pop()
        for L in free:
            if not L.get("body") and (same_row(L, B) or next_row(L, B)):
                L["body"] = True
                todo.append(L)


def label_records(lines: list[dict]) -> list[dict]:
    """Figure labels without structure boxes (a PDF page, a Word figure): an ID line
    ("3a", or "3a, 92%, 95% ee" on one line) and the value lines straight under it
    ("92%, 95% ee"), which overlap it left to right and follow within about two line
    heights. A value line goes to the nearest such line above it, never past another
    label: in a grid of structures, the text under row 1 is never given to row 2.
    Returns records {"ids", values…, "footnotes", "bbox", "text"}."""
    cands = []
    for L in lines:
        if L.get("in_table") or L.get("footnote") or L.get("body") or \
                len(L["text"].split()) > 10:
            continue
        a = annotate_text(L["text"])
        bold_ids = [i for p in L.get("bold_parts", []) for i in find_ids(split_footnotes(p)[0])]
        if bold_ids:
            a["ids"] = rank_ids(a["ids"] + bold_ids, bold_ids)
        elif len(a["ids"]) > 1:
            one = single_label(L["text"], a["ids"])
            if one:
                a["ids"] = [one]
        if not a["ids"] and not has_values(a):
            continue
        a["bbox"] = list(L["bbox"])
        a["size"] = L.get("size", 10.0)
        a["bold_ids"] = bold_ids
        cands.append(a)
    cands.sort(key=lambda a: (a["bbox"][1], a["bbox"][0]))
    anchors: list[dict] = []        # records (an ID line, with values joined in)
    attached: list[tuple[list, dict]] = []   # (bbox of an attached value line, its anchor)
    for a in cands:
        if a["ids"] and (len(a["ids"]) == 1 or a["bold_ids"]) and \
                (len(a["text"].split()) <= 8 or has_values(a)):
            rec = {k: v for k, v in a.items() if k not in ("bbox", "size", "bold_ids")}
            rec["ids"] = rec["ids"][:1]
            rec["bbox"] = list(a["bbox"])
            rec["lines"] = [a["text"]]
            anchors.append(rec)
            attached.append((a["bbox"], rec))
            continue
        if a["ids"] or not has_values(a):
            continue
        # A value line: the nearest label (or value line already tied to one) above it.
        best, best_gap = None, None
        for box, rec in attached:
            gap = a["bbox"][1] - box[3]
            if gap < -0.5 * a["size"] or gap > 1.6 * a["size"]:
                continue
            if _overlap_x(box, a["bbox"]) <= 0 and not _centre_inside(box, a["bbox"]):
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = rec, gap
        if best is None:
            continue
        for k, v in a.items():
            if k in ("text", "ids", "bbox", "size", "bold_ids"):
                continue
            if k == "footnotes":
                best.setdefault("footnotes", []).extend(v)
            else:
                best.setdefault(k, v)
        best["lines"].append(a["text"])
        best["bbox"][3] = max(best["bbox"][3], a["bbox"][3])
        attached.append((a["bbox"], best))
    for rec in anchors:
        rec["text"] = " / ".join(rec.pop("lines"))
    return anchors


def _along_arrow(bb, arrow) -> bool:
    """Is a text box written along a reaction arrow (above or below it, within its
    length)? `arrow`: [x0, y0, x1, y1] of the arrow's line."""
    ax0, ay0, ax1, ay1 = min(arrow[0], arrow[2]), min(arrow[1], arrow[3]), \
        max(arrow[0], arrow[2]), max(arrow[1], arrow[3])
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    if ax1 - ax0 >= ay1 - ay0:      # a horizontal arrow: text above or below its line
        return ax0 - 10 <= cx <= ax1 + 10 and ay0 - 45 <= cy <= ay1 + 45
    return ay0 - 10 <= cy <= ay1 + 10 and ax0 - 60 <= cx <= ax1 + 60


def assign_to_structures(boxes: list[list[float]], blocks: list[dict],
                         arrows: list[list[float]] = ()) -> list[list[dict]]:
    """Tie text blocks ({"text", "bbox"}) to structures (boxes), as ChemDraw scope schemes
    are drawn: the label and values sit UNDER a structure, centred on it or overlapping
    it, between its bottom edge and the next structure below. A block that isn't under
    any structure goes to the nearest one (chem-vision's rule) if it is close and looks
    like a label (an ID or a yield/ee/dr), but not if it is written along a reaction
    arrow or only gives conditions; such blocks are marked blk["fallback"] = True, and
    the text under a structure gives its ID first (see merge_blocks). Returns, per
    structure, its blocks top to bottom."""
    out: list[list[dict]] = [[] for _ in boxes]
    if not boxes:
        return out
    for blk in blocks:
        bb = blk["bbox"]
        best, best_gap = None, None
        for j, b in enumerate(boxes):
            if bb[1] < b[3] - 0.25 * (b[3] - b[1]):     # not below this structure
                continue
            width = bb[2] - bb[0]
            ov = _overlap_x(b, bb)
            if ov < 0.3 * min(width, b[2] - b[0]) and not _centre_inside(bb, b, 4):
                continue
            gap = bb[1] - b[3]
            # Another structure in between (the next row) blocks it.
            blocked = any(k != j and _overlap_x(o, bb) > 0 and b[3] <= o[1] <= bb[1]
                          for k, o in enumerate(boxes))
            if blocked:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = j, gap
        if best is not None and best_gap is not None and \
                best_gap <= max(60.0, 1.2 * (boxes[best][3] - boxes[best][1])):
            out[best].append(blk)
            continue
        a = annotate_text(blk["text"])
        if not (a["ids"] or has_values(a)):
            continue
        # Reaction conditions are not a label: text along an arrow ("2a (1.5 equiv) /
        # Pd (5 mol%), 24 h"), or text giving conditions but no yield/ee/dr.
        if any(_along_arrow(bb, arrow) for arrow in arrows):
            continue
        if not has_values(a) and quantities(blk["text"], reagents_too=False):
            continue
        blk["fallback"] = True
        dists = [_dist(b, bb) for b in boxes]
        j = min(range(len(boxes)), key=dists.__getitem__)
        h = boxes[j][3] - boxes[j][1]
        if dists[j] <= 0.6 * h + 12:
            out[j].append(blk)
    for blocks_j in out:
        blocks_j.sort(key=lambda b: (bool(b.get("fallback")), b["bbox"][1], b["bbox"][0]))
    return out


def _dist(box, bb) -> float:
    """Distance from a structure box to a text box, favouring text below or beside the
    structure (where scope tables put labels)."""
    x0, y0, x1, y1 = box
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    dx = 0 if x0 <= cx <= x1 else min(abs(cx - x0), abs(cx - x1))
    dy = 0 if y0 <= cy <= y1 else min(abs(cy - y0), abs(cy - y1))
    if cy < y0:
        dy *= 1.6  # text above a structure is less often its label
    return (dx * dx + dy * dy) ** 0.5


def merge_blocks(texts: list[str], bold_ids: list[str] = ()) -> dict:
    """One structure's label from its text lines, top to bottom: the ID from the first
    line that has one (bold IDs first), values from all of them (the first of each
    kind)."""
    rec: dict = {"ids": [], "text": " / ".join(texts)}
    for text in texts:
        a = annotate_text(text)
        if a["ids"] and not rec["ids"]:
            if bold_ids:
                rec["ids"] = rank_ids(a["ids"] + list(bold_ids), bold_ids)[:1]
            else:
                rec["ids"] = [single_label(text, a["ids"]) or a["ids"][0]]
        for k, v in a.items():
            if k in ("text", "ids"):
                continue
            if k == "footnotes":
                rec.setdefault("footnotes", []).extend(v)
            else:
                rec.setdefault(k, v)
    return rec


# ---------------------------------------------------------------------- footnotes

_FOOTNOTE_START = re.compile(r"^(?:\^([a-h])|\[([a-h])\]|\(([a-h])\)|([a-h])(?=\s*[A-Z0-9]))\s*")


def footnote_entries(lines: list[dict], plain: bool | None = None) -> list[list]:
    """Footnotes printed under a table or scheme: lines starting with a marker ("^a Isolated
    yield.", "[b] 48 h.", "(b) …" or "b Reaction run at 60 °C."). Several footnotes on one
    line ("^aIsolated yield. ^bAt 60 °C.") are split; a footnote may continue on the next
    lines. A plain letter ("b Reaction…", not raised or in brackets) only starts a
    footnote when `plain` is True, or (`plain` None: a page) when the line sits right
    under a scheme's or table's text or another footnote — body text can start "a Lewis
    acid…". Returns [[marker, text, y of its line], …] top to bottom; marks the lines
    used (line["footnote"] = True)."""
    entries: list[list] = []
    ordered = sorted(lines, key=lambda L: (L["bbox"][1], L["bbox"][0]))
    current = None
    last = None
    for n, L in enumerate(ordered):
        text = L["text"]
        m = _FOOTNOTE_START.match(text)
        starts = m is not None and (L.get("starts_with_marker") or m.group(1) or m.group(2)
                                    or m.group(3))
        if m is not None and not starts and _plain_footnote(text):
            starts = plain if plain is not None else _under_figure(L, ordered[:n])
        if starts:
            for marker, body in _split_inline(text):
                current = [marker, body, L["bbox"][1]]
                entries.append(current)
            L["footnote"] = True
            last = L
            continue
        if current is not None and last is not None:
            gap = L["bbox"][1] - last["bbox"][3]
            if 0 <= gap < 0.8 * L.get("size", 10) and abs(L["bbox"][0] - last["bbox"][0]) < 3                     and len(text.split()) > 2:
                current[1] = _join_wrapped(current[1], text)
                L["footnote"] = True
                last = L
                continue
        current = None
    return entries


def footnote_lines(lines: list[dict], plain: bool | None = None) -> dict[str, str]:
    """{marker: text} of the footnotes among `lines` (the first of each marker)."""
    out: dict[str, str] = {}
    for marker, text, _y in footnote_entries(lines, plain):
        out.setdefault(marker, text)
    return out


def footnotes_for(entries: list[list], y: float) -> dict[str, str]:
    """The footnotes that apply to a label at height y: for each marker the nearest one
    BELOW it (a scheme's or table's footnotes follow it), else the nearest above."""
    out: dict[str, str] = {}
    for marker, text, fy in sorted(entries, key=lambda e: (e[2] < y, abs(e[2] - y))):
        out.setdefault(marker, text)
    return out


def _under_figure(L: dict, above: list[dict]) -> bool:
    """Is the nearest line above L (overlapping it left to right, within three line
    heights) a footnote, a table cell, a caption or a figure label (an ID or a value)?"""
    size = max(L.get("size", 10.0), 1.0)
    near = [o for o in above if o is not L and _overlap_x(o["bbox"], L["bbox"]) > 0
            and -0.3 * size <= L["bbox"][1] - o["bbox"][3] <= 3 * size]
    if not near:
        return False
    o = max(near, key=lambda o: o["bbox"][3])
    if o.get("footnote") or o.get("in_table") or _CAPTION_START.match(o["text"]):
        return True
    if len(o["text"].split()) > 10:
        return False
    a = annotate_text(o["text"])
    return bool(a["ids"] or has_values(a))


def _plain_footnote(text: str) -> bool:
    """"a Reaction conditions: …" (a lower-case letter, a space, then a capital): a
    footnote whose marker wasn't set as a superscript. Prose never starts so."""
    return bool(re.match(r"^[a-h]\s+[A-Z][a-z]", text)) or bool(re.match(r"^[a-h][A-Z][a-z]", text))


def _split_inline(text: str) -> list[tuple[str, str]]:
    """"^aIsolated yield. ^bAt 60 °C." → [("a", "Isolated yield."), ("b", "At 60 °C.")]."""
    marks = list(re.finditer(r"(?:^|(?<=[\s.;)]))(?:\^([a-h])|\[([a-h])\])\s*", text))
    if marks and marks[0].start() == 0:
        out = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out.append((m.group(1) or m.group(2), text[m.end():end].strip()))
        return out
    m = _FOOTNOTE_START.match(text)
    if m:
        marker = next(g for g in m.groups() if g)
        return [(marker, text[m.end():].strip())]
    return []


def _join_wrapped(a: str, b: str) -> str:
    if a.endswith("-") and not a.endswith(" -"):
        return a + b
    return f"{a} {b}"


# ---------------------------------------------------------------------- body text

_NAME_WORDS = {"methyl", "ethyl", "propyl", "isopropyl", "butyl", "tert-butyl", "t-butyl",
               "benzyl", "phenyl", "allyl", "vinyl", "cyclohexyl", "pentyl", "hexyl",
               "octyl", "dimethyl", "diethyl", "dibenzyl", "di-tert-butyl", "trimethylsilyl"}
_NAME_TAILS = {"acid", "ester", "oxide", "chloride", "bromide", "iodide", "fluoride",
               "anhydride", "amide", "hydrochloride", "salt", "acetate", "triflate",
               "carbonate", "sulfate", "ether"}
_CHEM_SUFFIX = re.compile(r"(?i)(ane|ene|yne|ol|one|al|ate|ide|ine|amine|oate|ile|ium|ole|"
                          r"ane|ose|yl|ate|ite|one|oic|ic)$")
_NOT_NAME = {"compound", "compounds", "product", "products", "substrate", "substrates",
             "adduct", "derivative", "ester", "alcohol", "amine", "ketone", "aldehyde",
             "alkene", "alkyne", "table", "scheme", "figure", "entry", "the", "and", "of",
             "to", "in", "from", "with", "intermediate", "analogue", "analog"}


# Words that describe a compound rather than name it: a substituent ("2-naphthyl"), an
# adjective ("benzoic", "electron-poor", "para-substituted").
_NOT_A_NAME = re.compile(
    r"(?i)(?:yl|ic)$|\belectron-|-(?:poor|rich|substituted|based|derived|containing|"
    r"bearing|type|like|protected|free|deficient|donating|withdrawing|functionali[sz]ed)$")


def names_with_ids(text: str) -> list[tuple[str, str]]:
    """(name, ID) for every "name (ID)" in running text, e.g. "methyl
    4-bromobenzoate (3a)" → ("methyl 4-bromobenzoate", "3a"). Only names that look
    like chemical names (a hyphen, digit or comma inside, or a chemical ending)."""
    t = normalize(text)
    out = []
    for m in re.finditer(r"\(\s*([^()\s,;]{1,14})\s*\)", t):
        label = m.group(1)
        if not ID_RE.match(label) or label.isdigit() and len(label) > 2:
            continue
        before = t[:m.start()].rstrip()
        words = before.split(" ")
        if not words or not words[-1]:
            continue
        core = words[-1]
        if core.lower().strip(",.;:") in _NOT_NAME or not re.search(r"[A-Za-z]{3}", core):
            continue
        name_words = [core]
        i = len(words) - 2
        if core.lower() in _NAME_TAILS and i >= 0:
            name_words.insert(0, words[i])
            i -= 1
        while i >= 0 and words[i].lower() in _NAME_WORDS:
            name_words.insert(0, words[i])
            i -= 1
        name = " ".join(name_words).strip(",.;: ")
        name = re.sub(r"^[(\[]+(?![^()]*\))", "", name)
        if _NOT_A_NAME.search(name):
            continue   # "the 2-naphthyl (3a)", "electron-poor (3b)": a description
        chem_like = bool(re.search(r"[-,\d\[\]]", name)) or bool(_CHEM_SUFFIX.search(name)) \
            and len(name) >= 8
        if chem_like and len(name) >= 5:
            out.append((name, label))
    return out


_NOT_SCOPE_SENTENCE = re.compile(
    r"(?i)\b(?:without|controls?|scale[ds]?|scale-up|mmol|grams?|gram-scale|entry|entries)\b"
    r"|\d\s*g\s+scale")


def sentence_records(text: str) -> list[dict]:
    """Yields, ee and dr stated in running text: a sentence with exactly one
    letter-suffixed compound ID and an explicit "92% yield", "95% ee", "10:1 dr"…
    ("3a was obtained in 92% yield and 95% ee"). Anything less clear is left alone, and so
    are sentences about control experiments, scale-ups and table entries ("without",
    "control", "scale", "mmol", "gram", "entry"): their yields are not the scope result.
    (The check reports what these sentences say as notes only, SPEC §13o.6.)"""
    out = []
    t = normalize(text)
    for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z(])", t):
        if _NOT_SCOPE_SENTENCE.search(sentence):
            continue
        clean, notes = split_footnotes(sentence)
        ids = {i for i in find_ids(clean) if re.search(r"\d[a-z]", i)}
        if len(ids) != 1:
            continue
        vals = parse_values(clean)
        vals.pop("yield_assumed", None)
        if not any(k in vals for k in ("yield", "ee", "er", "dr")):
            continue
        rec = {"ids": [ids.pop()], "text": sentence.strip()[:300],
               **{k: v for k, v in vals.items() if k in ("yield", "ee", "er", "dr",
                                                         "ee_from_er")}}
        if notes:
            rec["footnotes"] = notes
        out.append(rec)
    return out


_BODY_ID = re.compile(r"(?:\b[Cc]ompounds?\s+|\b[Pp]roducts?\s+|\()((?:S|SI-)?\d{1,3}[a-z]{1,3}'*)"
                      r"(?=[\s),;.:]|$)")


def mentioned_ids(text: str) -> set[str]:
    """IDs mentioned in running text in a way that is clearly a compound: "(3a)",
    "compound 3h", "products 3a–3f" (the first)."""
    return {m.group(1) for m in _BODY_ID.finditer(normalize(text))}


def token_present(text_keys: set[str], label: str) -> bool:
    return id_key(label) in text_keys


def all_tokens(text: str) -> set[str]:
    """Every word of a text as an ID key (for "is this ID anywhere in the manuscript?")."""
    return {id_key(w.strip("()[].,;:")) for w in re.split(r"[\s,;/–]+", normalize(text)) if w}


# ---------------------------------------------------------------------- conditions

@dataclass
class Quantity:
    kind: str        # "temperature", "time", "equiv", "loading", "scale", "conc", "reagent"
    value: float | str   # number (°C, minutes, equiv, mol%, mmol, M) or a word ("rt", "reflux", a name)
    text: str        # as written

    def same(self, other: "Quantity") -> bool:
        """Equal, counting wordings that mean the same: "rt" / "room temperature" /
        "ambient temperature" = 20–25 °C, "overnight" = 12–18 h (synonyms such as N2 =
        nitrogen are one name already)."""
        if self.kind != other.kind:
            return False
        a, b = self.value, other.value
        if isinstance(a, float) and isinstance(b, float):
            return abs(a - b) <= 1e-6 * max(1.0, abs(a))
        if isinstance(a, float) or isinstance(b, float):
            word, number = (b, a) if isinstance(a, float) else (a, b)
            low, high = _RANGES.get((self.kind, str(word)), (None, None))
            return low is not None and low <= number <= high
        return str(a) == str(b)


# What a word stands for, as a range (°C; minutes).
_RANGES = {("temperature", "rt"): (20.0, 25.0), ("time", "overnight"): (720.0, 1080.0)}


# Reaction solvents and common reagents, with the other ways they are written (all lower
# case; compared case-insensitively except formulas, see reagents()).
REAGENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "THF": ("thf", "tetrahydrofuran"), "2-MeTHF": ("2-methf", "2-methyltetrahydrofuran"),
    "DCM": ("dcm", "ch2cl2", "dichloromethane", "methylene chloride"),
    "DCE": ("dce", "1,2-dichloroethane", "dichloroethane", "(ch2cl)2"),
    "MeCN": ("mecn", "ch3cn", "acetonitrile"), "toluene": ("toluene", "phme", "phch3"),
    "benzene": ("benzene", "c6h6"), "DMF": ("dmf", "n,n-dimethylformamide", "dimethylformamide"),
    "DMA": ("dma", "dmac", "n,n-dimethylacetamide", "dimethylacetamide"),
    "DMSO": ("dmso", "dimethyl sulfoxide", "dimethylsulfoxide"), "NMP": ("nmp",),
    "dioxane": ("dioxane", "1,4-dioxane"), "Et2O": ("et2o", "diethyl ether", "ether"),
    "MTBE": ("mtbe", "tbme"), "CPME": ("cpme",), "MeOH": ("meoh", "methanol"),
    "EtOH": ("etoh", "ethanol"), "iPrOH": ("iproh", "i-proh", "ipa", "2-propanol",
                                           "isopropanol", "isopropyl alcohol"),
    "tBuOH": ("tbuoh", "t-buoh", "tert-butanol", "tert-butyl alcohol"),
    "HFIP": ("hfip", "hexafluoroisopropanol", "1,1,1,3,3,3-hexafluoro-2-propanol"),
    "TFE": ("tfe", "2,2,2-trifluoroethanol", "trifluoroethanol"), "AcOH": ("acoh", "acetic acid"),
    "TFA": ("tfa", "trifluoroacetic acid"), "EtOAc": ("etoac", "ethyl acetate"),
    "hexane": ("hexane", "hexanes", "n-hexane"), "pentane": ("pentane", "n-pentane"),
    "heptane": ("heptane", "n-heptane"), "xylene": ("xylene", "xylenes", "o-xylene", "p-xylene",
                                                    "m-xylene"),
    "mesitylene": ("mesitylene",), "PhCl": ("phcl", "chlorobenzene"),
    "PhCF3": ("phcf3", "trifluorotoluene", "benzotrifluoride"), "water": ("water", "h2o"),
    "acetone": ("acetone",), "CHCl3": ("chcl3", "chloroform"), "CCl4": ("ccl4",),
    "MeNO2": ("meno2", "ch3no2", "nitromethane"), "pyridine": ("pyridine",),
    "DBU": ("dbu",), "DIPEA": ("dipea", "hünig's base", "iPr2NEt".lower(), "i-pr2net"),
    "Et3N": ("et3n", "triethylamine", "nEt3".lower()), "DMAP": ("dmap",), "NBS": ("nbs",),
    "NIS": ("nis",), "NCS": ("ncs",), "TBAF": ("tbaf",), "LDA": ("lda",),
    "LiHMDS": ("lihmds",), "NaHMDS": ("nahmds",), "KHMDS": ("khmds",),
    "DIBAL-H": ("dibal-h", "dibal"), "mCPBA": ("mcpba", "m-cpba"), "TEMPO": ("tempo",),
    "DDQ": ("ddq",), "AIBN": ("aibn",), "Xantphos": ("xantphos",), "BINAP": ("binap",),
    "XPhos": ("xphos",), "SPhos": ("sphos",), "RuPhos": ("ruphos",), "dppf": ("dppf",),
    "Pd/C": ("pd/c",), "TMSCl": ("tmscl",), "TMSCN": ("tmscn",), "HBpin": ("hbpin",),
    "B2pin2": ("b2pin2",), "TsOH": ("tsoh", "p-tsoh"), "CSA": ("csa",),
    # Atmospheres ("Ar" alone is too often the aryl group: see _ATMOSPHERE).
    "N2": ("n2", "nitrogen"), "Ar": ("argon",), "O2": ("o2", "oxygen", "air"),
}
# The solvents among them (for the check of SI procedures against their general procedure).
SOLVENTS = {"THF", "2-MeTHF", "DCM", "DCE", "MeCN", "toluene", "benzene", "DMF", "DMA", "DMSO",
            "NMP", "dioxane", "Et2O", "MTBE", "CPME", "MeOH", "EtOH", "iPrOH", "tBuOH", "HFIP",
            "TFE", "AcOH", "EtOAc", "hexane", "pentane", "heptane", "xylene", "mesitylene",
            "PhCl", "PhCF3", "water", "acetone", "CHCl3", "CCl4", "MeNO2", "pyridine"}
_ATMOSPHERE = re.compile(r"\b(?:under|an?|of|with|in)\s+(?:an?\s+|dry\s+)?(Ar)\b"
                         r"|\b(Ar)\s+(?:atmosphere|balloon|gas|\(1\s*atm\))")
_REAGENT_ALIASES = {alias: name for name, aliases in REAGENT_SYNONYMS.items()
                    for alias in aliases}
_REAGENT_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(a) for a in sorted(_REAGENT_ALIASES, key=len,
                                                            reverse=True)) + r")(?![\w-])",
    re.IGNORECASE)

_TEMP = re.compile(r"(?<![\w.])(-?\d{1,3}(?:\.\d+)?)\s*°\s*C\b|(?<![\w.])(-?\d{1,3})\s*(?:o|º)C\b")
_TEMP_WORD = re.compile(r"(?i)\b(rt|r\.\s?t\.|room temperature|ambient temperature|reflux(?:ing)?)\b")
_TIME = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*(min|mins|minutes?|hrs?|hours?|days?|d|h)\b"
                   r"(?!\s*%)")
_EQUIV = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*(?:equiv\.?|eq\.?|equivalents?)(?![\w])", re.I)
_LOADING = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*mol\s*%", re.I)
_SCALE = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*(mmol|µmol|μmol|umol)\b")
_CONC = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*M\b(?!\s*%)")
_TIME_TO_MIN = {"min": 1, "mins": 1, "minute": 1, "minutes": 1, "h": 60, "hr": 60, "hrs": 60,
                "hour": 60, "hours": 60, "d": 1440, "day": 1440, "days": 1440}


def quantities(text: str, reagents_too: bool = True) -> list[Quantity]:
    """The reaction conditions written in a text: temperatures (°C, rt, reflux), times
    (min, h, days; "3 h" but not "3h", which is a compound), equivalents, mol%, scale
    (mmol), concentrations (M) and named solvents/reagents."""
    t = normalize(text).replace("�C", "°C")
    out: list[Quantity] = []
    for m in _TEMP.finditer(t):
        v = m.group(1) or m.group(2)
        out.append(Quantity("temperature", float(v), m.group(0).strip()))
    for m in _TEMP_WORD.finditer(t):
        word = m.group(1).lower().replace(" ", "")
        value = "reflux" if word.startswith("reflux") else "rt"
        out.append(Quantity("temperature", value, m.group(0)))
    for m in _TIME.finditer(t):
        unit = m.group(2).lower()
        if unit in ("h", "d") and not re.match(r"\d+(?:\.\d+)?\s+[hd]\b", m.group(0)):
            continue    # "3h", "4d": compound IDs, not times
        out.append(Quantity("time", float(m.group(1)) * _TIME_TO_MIN[unit], m.group(0)))
    for m in re.finditer(r"(?i)\bovernight\b", t):
        out.append(Quantity("time", "overnight", m.group(0)))
    for m in _EQUIV.finditer(t):
        out.append(Quantity("equiv", float(m.group(1)), m.group(0)))
    for m in _LOADING.finditer(t):
        out.append(Quantity("loading", float(m.group(1)), m.group(0)))
    for m in _SCALE.finditer(t):
        factor = 1.0 if m.group(2) == "mmol" else 0.001
        out.append(Quantity("scale", float(m.group(1)) * factor, m.group(0)))
    for m in _CONC.finditer(t):
        out.append(Quantity("conc", float(m.group(1)), m.group(0)))
    if reagents_too:
        out += reagents(t)
    return out


def reagents(text: str) -> list[Quantity]:
    """Named solvents and reagents in a text (THF = tetrahydrofuran, CH2Cl2 = DCM…) and
    any other chemical formula (K3PO4, Pd(OAc)2), as Quantity("reagent", name, text)."""
    from .chemtext import formula_matches
    t = normalize(text)
    out: list[Quantity] = []
    seen = set()
    for m in _REAGENT_RE.finditer(t):
        name = _REAGENT_ALIASES[m.group(1).lower()]
        if name not in seen:
            seen.add(name)
            out.append(Quantity("reagent", name, m.group(1)))
    for m in _ATMOSPHERE.finditer(t):
        if "Ar" not in seen:
            seen.add("Ar")
            out.append(Quantity("reagent", "Ar", m.group(1) or m.group(2)))
    for m in formula_matches(t):
        word = m.group(0)
        name = _REAGENT_ALIASES.get(word.lower(), word)
        if name not in seen:
            seen.add(name)
            out.append(Quantity("reagent", name, word))
    return out


_WORKUP = re.compile(
    r"(?i)\b(?:cool(?:ed|ing)?|quench(?:ed|ing)?|extract(?:ed|ion|ions)?|wash(?:ed|ing)?|"
    r"dried|drying|concentrat(?:ed|ion)|evaporat(?:ed|ion)|purif(?:ied|ication|y)|"
    r"chromatograph\w*|column|filter(?:ed|ing)?|filtration|dilut(?:ed|ion)|work-?up|"
    r"poured|recrystalli[sz]\w*|separated|brine|TLC|Rf|eluent|eluting|silica)\b")


def reaction_part(text: str) -> str:
    """The sentences of a procedure that describe the reaction itself, without the work-up
    (cooling, quenching, extraction, washing, drying, concentration, purification…): a
    time or temperature there ("stirred for 10 min", "cooled to room temperature") is not
    the reaction's."""
    sentences = re.split(r"(?<=[.;])\s+|\n+", normalize(text))
    return " ".join(s for s in sentences if s.strip() and not _WORKUP.search(s))


def describe(q: Quantity) -> str:
    return q.text
