"""Spectra in an imported SI (SPEC §13u.8, phase I3): which compound each belongs to and
what it is (¹H, ¹³C, …), from a Word file's embedded MestReNova spectra or a PDF's
spectrum pages.

* Word: MestReNova objects are stored with the picture Word shows of them (an EMF, which
  the program converts like a pasted Mnova spectrum). A spectrum's compound is, in this
  order: the one whose peak report its printed peak labels clearly fit; the one named by
  a header picture next to it (Mnova's structure + ID + title); the one whose ID labels
  a ChemDraw drawing grouped with it, else whose imported structure is drawn there, else
  the compound whose text the group is in (not when its peaks contradict that one).
* PDF: each spectrum page is cut at its headers ("2a — ¹H NMR (500 MHz, CDCl₃)", "¹H NMR
  of 2"), one picture per spectrum; the compound is the ID in the header, else the only
  compound label on the page.
* The nucleus comes from a header or title naming it, else from the ppm axis (its range).

What can't be matched is listed (cid "") for the user to assign in the review window.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

# a nucleus or another spectrum type named in a header or title
_NUCLEUS = re.compile(r"(?<![\w{])(?P<mass>\d{1,3})\s*(?P<el>[A-Z][a-z]?)(?:\s*\{\s*\d{1,3}\s*"
                      r"[A-Z][a-z]?\s*\})?\s*-?\s*NMR\b")
_TWO_D = re.compile(r"\b(?:2D\s+NMR|COSY|HSQC|HMBC|HMQC|NOESY|ROESY|TOCSY)\b")
_HPLC = re.compile(r"\b(?:HPLC|SFC|chromatogram)\b", re.I)
_IR = re.compile(r"\bIR\s+spectrum\b|\bFT-?IR\b")
HEADER = re.compile(_NUCLEUS.pattern + "|" + _TWO_D.pattern + "|" + _HPLC.pattern + "|"
                    + _IR.pattern, re.I)
OWN_TYPES = ("1H", "13C", "19F", "31P", "11B")


@dataclass
class ImportedSpectrum:
    kind: str                 # "emf" (a Word MestReNova picture) or "pdf" (cut from a page)
    data: bytes
    type: str                 # "1H", "13C", …, "Other NMR", "2D NMR", "HPLC/SFC", "IR", "Other"
    cid: str = ""             # the compound it belongs to ("": not matched)
    where: str = ""           # where it was found, in words
    title: str = ""           # its header or title, if it had one
    how: str = ""             # how its compound was found, in words
    keep: bool = True
    shows_structure: bool = False   # its picture already shows the structure


def type_from_text(text: str) -> str:
    """The spectrum type a header or title names ("" if none)."""
    m = _NUCLEUS.search(text)
    if m:
        nucleus = f"{m.group('mass')}{m.group('el')}"
        return nucleus if nucleus in OWN_TYPES else "Other NMR"
    if _TWO_D.search(text):
        return "2D NMR"
    if _HPLC.search(text):
        return "HPLC/SFC"
    if _IR.search(text):
        return "IR"
    return ""


def type_from_axis(numbers: list[float]) -> str:
    """¹H or ¹³C (or ¹⁹F) from the numbers on a ppm axis: their range."""
    if len(numbers) < 3:
        return ""
    lo, hi = min(numbers), max(numbers)
    if lo >= -3 and hi <= 16:
        return "1H"
    if lo >= -20 and 90 <= hi <= 260:
        return "13C"
    if lo <= -40 and hi <= 20:
        return "19F"
    return ""


def emf_texts(data: bytes) -> list[str]:
    """The texts an EMF draws (its EMR_EXTTEXTOUTW records), in order."""
    out: list[str] = []
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        kind, size = struct.unpack_from("<II", data, pos)
        if size < 8 or pos + size > n:
            break
        if kind == 84 and size >= 76:      # EMR_EXTTEXTOUTW
            chars, off = struct.unpack_from("<II", data, pos + 44)
            if 0 < chars < 4096 and off + 2 * chars <= size:
                out.append(data[pos + off:pos + off + 2 * chars].decode("utf-16-le", "replace"))
        pos += size
    return out


def emf_peaks(data: bytes) -> list[float]:
    """The peak positions printed on a spectrum picture (EMF): Mnova and most NMR programs
    write them above the peaks, turned by 90°, a character at a time. Characters standing
    one above the other form one label ("39.52"), read upwards (or downwards for text
    turned the other way)."""
    fonts: dict[int, tuple[int, int]] = {}
    current = (0, 10)
    chars: list[tuple[float, float, int, str]] = []
    pos, n = 0, len(data)
    while pos + 8 <= n:
        kind, size = struct.unpack_from("<II", data, pos)
        if size < 8 or pos + size > n:
            break
        if kind == 82 and size >= 32:                # EMR_EXTCREATEFONTINDIRECTW
            handle, = struct.unpack_from("<I", data, pos + 8)
            height, _width, escapement = struct.unpack_from("<iii", data, pos + 12)
            fonts[handle] = (escapement, abs(height) or 10)
        elif kind == 37 and size >= 12:              # EMR_SELECTOBJECT
            handle, = struct.unpack_from("<I", data, pos + 8)
            if handle in fonts:
                current = fonts[handle]
        elif kind == 84 and size >= 76:              # EMR_EXTTEXTOUTW
            x, y = struct.unpack_from("<ii", data, pos + 36)
            count, off = struct.unpack_from("<II", data, pos + 44)
            if 0 < count < 4096 and off + 2 * count <= size and current[0] in (900, 2700):
                text = data[pos + off:pos + off + 2 * count].decode("utf-16-le", "replace")
                chars.append((x, y, current[0], text))
        pos += size
    if not chars:
        return []
    height = max(f[1] for f in fonts.values()) if fonts else 10
    chars.sort(key=lambda c: c[0])
    columns: list[list] = []
    for c in chars:
        if columns and abs(c[0] - columns[-1][-1][0]) <= height * 0.4:
            columns[-1].append(c)
        else:
            columns.append([c])
    out = []
    for col in columns:
        upward = col[0][2] == 900
        text = "".join(t for _x, _y, _e, t in sorted(col, key=lambda c: c[1], reverse=upward))
        m = re.match(r"\s*(-?\d{1,3}\.\d{1,3})", text.replace("−", "-"))
        if m:
            out.append(float(m.group(1)))
    return out


def match_score(peaks: list[float], report: str, kind: str) -> float:
    """How well a spectrum's peaks fit a peak report (0–1): mostly the share of the
    report's peaks found in the spectrum (a range "7.38 – 7.29" counts if any peak is in
    it), a little the share of the spectrum's peaks the report explains."""
    from . import nmr_parse
    reported = nmr_parse.peaks(report)
    if len(peaks) < 3 or not reported:
        return 0.0
    tol = {"13C": 0.06, "19F": 0.15}.get(kind, 0.02)
    spans = []
    for p in reported:
        lo, hi = (p.shift, p.end) if p.end is not None else (p.shift, p.shift)
        spans.append((min(lo, hi) - tol, max(lo, hi) + tol))
    found = sum(1 for lo, hi in spans if any(lo <= x <= hi for x in peaks))
    explained = sum(1 for x in peaks if any(lo <= x <= hi for lo, hi in spans))
    return 0.75 * found / len(spans) + 0.25 * explained / len(peaks)


def best_by_peaks(peaks: list[float], kind: str, compounds) -> tuple[str, float]:
    """(the compound whose report of this nucleus the peaks fit clearly best, its score);
    ("", 0) when none fits well or two fit about as well."""
    scored = []
    for c in compounds:
        reports = [r.get("text", "") for r in c.values.get("nmr", [])
                   if r.get("nucleus") == kind]
        if reports:
            scored.append((max(match_score(peaks, t, kind) for t in reports), c.cid))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.6:
        return "", 0.0
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.1:
        return "", 0.0
    return scored[0][1], scored[0][0]


def _axis_numbers(texts: list[str]) -> list[float]:
    out = []
    for t in texts:
        t = t.strip().replace("−", "-")
        if re.fullmatch(r"-?\d{1,3}(?:\.\d{1,2})?", t):
            out.append(float(t))
    return out


def is_spectrum_picture(data: bytes) -> bool:
    """Whether a picture (EMF) is a spectrum: it has an axis (in ppm, or several numbers
    in a row along it, as a chromatogram's minutes)."""
    texts = emf_texts(data)
    if any(t.strip().lower() in ("ppm", "f1 (ppm)", "δ (ppm)", "min", "time (min)")
           for t in texts):
        return True
    return len(_axis_numbers(texts)) >= 5


def type_of_emf(data: bytes) -> tuple[str, str]:
    """(type, title) of a MestReNova picture: a title naming the nucleus, else its axis."""
    texts = emf_texts(data)
    joined = " ".join(texts)
    for t in texts:
        found = type_from_text(t)
        if found:
            return found, t.strip()
    found = type_from_text(joined)
    if found:
        return found, ""
    # The axis: the tick labels are the numbers printed next to "ppm"
    numbers = _axis_numbers(texts[:texts.index("ppm")] if "ppm" in texts else texts)
    return type_from_axis(numbers) or "Other NMR", ""


# ---------------------------------------------------------------------- Word

def word_spectra(plan, objects: list[dict], own_objects: list[list[dict]], id_pattern: str):
    """The MestReNova spectra of a Word SI, each with its compound if it could be told."""
    from rdkit import Chem
    from . import cdx
    known = {c.cid for c in plan.compounds}
    owner = {}
    for c, own in zip(plan.compounds, own_objects):
        for o in own:
            owner[id(o)] = c.cid
    by_structure: dict[str, set[str]] = {}
    for c in plan.compounds:
        if c.structure:
            mol = Chem.MolFromMolBlock(c.structure)
            if mol is not None:
                for iso in (True, False):
                    by_structure.setdefault(Chem.MolToSmiles(mol, isomericSmiles=iso),
                                            set()).add(c.cid)
    headers = _paired_headers(objects, known)
    groups: dict[int, list[dict]] = {}
    for o in objects:
        groups.setdefault(o.get("group", id(o)), []).append(o)
    out: list[ImportedSpectrum] = []
    for number, (key, members) in enumerate(groups.items(), 1):
        # (an Mnova page can hold just a molecule: without a ppm axis it is no spectrum)
        spectra = [o for o in members if o.get("kind") == "mnova" and o.get("picture")
                   and is_spectrum_picture(o["picture"])]
        if not spectra:
            continue
        cid, how = "", ""
        drawings = [o for o in members if o.get("kind") == "chemdraw"]
        labels: set[str] = set()
        smiles: set[str] = set()
        for d in drawings:
            try:
                structures, found = cdx.drawing(d["cdx"],
                                                lambda t: re.fullmatch(id_pattern, t.strip()))
            except (cdx.CdxError, KeyError, ValueError):
                continue
            labels |= {lab.text.strip() for lab in found if lab.text.strip() in known}
            for s in structures:
                mol = cdx.to_rdkit(s.molecule)
                if mol is not None and mol.GetNumAtoms() > 1:
                    smiles.add(Chem.MolToSmiles(mol))
                    smiles.add(Chem.MolToSmiles(mol, isomericSmiles=False))
        types = [type_of_emf(o["picture"])[0] for o in spectra]
        unambiguous = len(types) == len(set(types))     # (at most one spectrum per nucleus)
        labelled = len(labels) == 1 and unambiguous
        if labelled:
            cid, how = labels.pop(), "by the ID under the structure next to it"
        else:
            matches = set().union(*(by_structure.get(s, set()) for s in smiles)) if smiles else set()
            if len(matches) == 1:
                cid, how = matches.pop(), "by the structure drawn next to it"
            else:
                owners = {owner[id(o)] for o in members if id(o) in owner}
                if len(owners) == 1:
                    cid, how = owners.pop(), "it is in the compound's text"
        for o in spectra:
            kind, title = type_of_emf(o["picture"])
            # The peaks decide when they clearly fit one compound's report; the drawing
            # next to it only when they can't tell (no peaks printed, no such report).
            peaks = emf_peaks(o["picture"])
            by_peaks, score = best_by_peaks(peaks, kind, plan.compounds)
            head = headers.get(id(o))
            if head is not None and not head[3] and cid and labelled and head[0] != cid:
                head = None         # (the ID in its own group beats a neighbour's header)
            if head is not None and not title:
                title = head[2]
            if by_peaks:
                own_cid, own_how = by_peaks, (f"its peaks fit the {kind} NMR report of "
                                               f"{by_peaks} ({round(score * 100)}%)")
                if head is not None and head[0] != by_peaks:
                    own_how += f"; the header next to it says {head[0]}: check it"
            elif head is not None:
                own_cid, own_how = head[0], f"by the header next to it ({head[0]})"
            else:
                own_cid, own_how = cid, how
                # A drawing next to it is weaker evidence than peaks: not when its
                # compound has a report of this nucleus that the peaks don't fit.
                target = next((c for c in plan.compounds if c.cid == cid), None)
                reports = [r.get("text", "") for r in (target.values.get("nmr", [])
                                                        if target else [])
                           if r.get("nucleus") == kind]
                if cid and len(peaks) >= 3 and reports and \
                        max(match_score(peaks, t, kind) for t in reports) < 0.5:
                    own_cid, own_how = "", ""
            out.append(ImportedSpectrum("emf", o["picture"], kind, own_cid,
                                        f"spectrum group {number}", title, own_how))
    _settle(out)
    return out


def _paired_headers(objects: list[dict],
                    known: set[str]) -> dict[int, tuple[str, str, str, bool]]:
    """{id(spectrum object): (compound ID, type, title, whether in the same group)} from
    the header pictures next to the spectra: a picture that is no spectrum but writes a
    compound's ID (and usually a title, "¹H NMR (CDCl3, 500 MHz)") — e.g. an Mnova page
    holding the structure, the ID and the title above the spectrum. Each header goes to
    the nearest spectrum of its type within three objects of it (in the same group
    first), each spectrum to one header."""
    spectra_at, heads = [], []
    for i, o in enumerate(objects):
        picture = o.get("picture")
        if o.get("kind") not in ("mnova", "other") or not picture:
            continue
        if is_spectrum_picture(picture):
            spectra_at.append(i)
            continue
        raw = emf_texts(picture)
        texts = [t.strip() for t in raw]
        ids = [t for t in texts if t in known]
        if len(set(ids)) != 1:
            continue
        joined = " ".join(texts)
        heads.append((i, ids[0], type_from_text(re.sub(r"(\d)\s+([A-Z])", r"\1\2", joined)),
                      _title_of(raw)))
    pairs = []
    for hi, cid, kind, title in heads:
        for si in spectra_at:
            if abs(si - hi) > 3:
                continue
            skind = type_of_emf(objects[si]["picture"])[0]
            if kind and skind and kind != skind:
                continue
            same_group = objects[si].get("group") == objects[hi].get("group")
            pairs.append((not same_group, abs(si - hi), hi, si, cid, kind, title))
    out: dict[int, tuple[str, str, str, bool]] = {}
    used_h, used_s = set(), set()
    for other_group, _d, hi, si, cid, kind, title in sorted(pairs):
        if hi in used_h or si in used_s:
            continue
        used_h.add(hi)
        used_s.add(si)
        out[id(objects[si])] = (cid, kind, title, not other_group)
    return out


def _title_of(texts: list[str]) -> str:
    """"1H NMR (CDCl3, 500 MHz)" from a header's pieces of text (as drawn: their own
    spaces kept)."""
    joined = "".join(texts)
    m = re.search(r"\d{1,3}\s*[A-Z][a-z]?(?:\{[^}]*\})?\s*NMR\s*\([^)]*\)", joined)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _settle(spectra: list[ImportedSpectrum]) -> None:
    """A compound that has a spectrum matched by its peaks for a nucleus doesn't also get
    one of that nucleus by a drawing next to it only (that one is someone else's). Two
    matched by their peaks are both kept, with a note (the SI may hold it twice)."""
    by_peaks = {(s.cid, s.type) for s in spectra if s.cid and s.how.startswith("its peaks")}
    for s in spectra:
        if s.cid and not s.how.startswith("its peaks") and (s.cid, s.type) in by_peaks:
            s.cid, s.how = "", ""
    count: dict[tuple, int] = {}
    for s in spectra:
        if s.cid:
            count[(s.cid, s.type)] = count.get((s.cid, s.type), 0) + 1
    for s in spectra:
        if s.cid and count[(s.cid, s.type)] > 1:
            s.how += (f" — {s.cid} has {count[(s.cid, s.type)]} {s.type} spectra: untick the "
                      "ones you don't want")


# ---------------------------------------------------------------------- PDF

def _find_id(text: str, ids: list[str]) -> str:
    """The first compound ID in a header ("2a — 1H NMR…", "1H NMR of 2", "9a13C NMR")."""
    best = None
    for cid in ids:
        for m in re.finditer(rf"(?<![\w-]){re.escape(cid)}(?=$|[^\w-]|\d{{1,3}}[A-Z])", text):
            # not the nucleus itself ("1H", "13C": an ID "1" or "13" must not match there)
            if re.match(r"[A-Z][a-z]?\b", text[m.end():]) and cid.isdigit():
                continue
            if best is None or m.start() < best[0] or (m.start() == best[0] and len(cid) > len(best[1])):
                best = (m.start(), cid)
    return best[1] if best else ""


def pdf_spectra(path, plan, pages: list[int]) -> list[ImportedSpectrum]:
    """The spectra on a PDF SI's spectrum pages: each page cut at its headers (one picture
    per spectrum, trimmed to its ink), with the compound the header names."""
    from pypdf import PdfReader
    from . import si_import_pdf as P
    from .si_import_read import _lines
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")
    except Exception:  # noqa: BLE001
        return []
    ids = sorted((c.cid for c in plan.compounds), key=len, reverse=True)
    out: list[ImportedSpectrum] = []
    for n in pages:
        try:
            page = reader.pages[n - 1]
            spans = P._spans(page)
            height = float(page.mediabox.height)
        except Exception:  # noqa: BLE001
            continue
        lines = _lines(spans)
        headers = [L for L in lines if HEADER.search(L.plain()) and len(L.plain()) < 160]
        page_ids = {_find_id(L.plain(), ids) for L in lines
                    if re.fullmatch(r"[\w′'\-\s]{1,40}", L.plain())} - {""}
        left = min((s.x0 for s in spans), default=36.0)
        right = max((s.x1 for s in spans), default=576.0)
        bottom_text = [L for L in lines if L.base > 0.9 * height]   # (page numbers, footers)
        page_bottom = min((L.base - L.size for L in bottom_text), default=height - 20)
        if not headers:
            headers_y = [None]
        else:
            headers_y = headers
        for k, head in enumerate(headers_y):
            title = head.plain() if head is not None else ""
            top = (head.base + 0.3 * head.size) if head is not None else 20
            end = (headers_y[k + 1].base - headers_y[k + 1].size - 2) if k + 1 < len(headers_y) \
                else page_bottom - 2
            box = (max(0, left - 6), top, min(float(page.mediabox.width), right + 6), end)
            if end - top < 60:
                continue
            try:
                data = P._trimmed(reader, n, box)
            except Exception:  # noqa: BLE001
                data = None
            if data is None:
                continue
            cid = _find_id(title, ids) if title else ""
            how = "by the ID in its header" if cid else ""
            if not cid and len(page_ids) == 1:
                cid, how = next(iter(page_ids)), "by the compound label on its page"
            kind = type_from_text(title) or "Other NMR"
            out.append(ImportedSpectrum("pdf", data, kind, cid, f"page {n}", title, how,
                                        shows_structure=True))
    return out
