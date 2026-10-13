"""Recognising an SI's general procedures and compounds for the import (SPEC §13u.3).

`analyse(paras)` takes the paragraphs read by core/si_import_read.py and returns an
`ImportPlan`: the general procedures, the compounds with their values sorted into the
program's fields, the other text, and warnings. Nothing is written here; the review
window shows the plan and core/si_import_apply.py writes what the user keeps.

Everything is best effort, and nothing is dropped: a compound's text that is neither its
procedure nor a recognised value is kept as `unrecognised` (stored in its private Notes),
and text outside the compounds and procedures is kept as `other`.

Values are kept as printed (significant figures, dashes) so that the SI prints them as
before; only spacing is evened out.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import chem
from .ids import new_id
from .markup import Paragraph, Run, parse, serialize
from .si_import_read import Para, clean_space, merge_runs

# ---------------------------------------------------------------------- results


@dataclass
class ImportedGP:
    key: str                    # an internal key ("gp1", …)
    name: str                   # "General Procedure A"
    title: str                  # "Reduction of aryl methyl ketones"
    text: str                   # its text (markup)
    label: str = ""             # "A", "1", … ("" when it has none)
    sentence: str = ""          # the compound sentence worked out from its compounds
    original: str = ""          # as printed (plain), for the review window
    keep: bool = True
    end: tuple = (0, 0.0)       # PDF: (page, bottom in pt) where its text ends


@dataclass
class ImportedCompound:
    cid: str                    # the compound's ID as printed ("2a")
    name: str                   # its name (plain)
    procedure: str = ""         # its experimental procedure (markup)
    values: dict = field(default_factory=dict)   # field key -> value, as compound.json
    unrecognised: list[str] = field(default_factory=list)
    original: str = ""          # its whole text as printed (markup), heading excluded
    page: int = 0
    gp_key: str = ""            # the general procedure it cites ("" if none)
    gp_values: dict = field(default_factory=dict)  # its values for the GP's placeholders
    gp_sentence: str = ""       # the sentence citing the GP, as printed (markup)
    cites: list[str] = field(default_factory=list)  # citation numbers (phase I4)
    hrms_calcd: str = ""        # the calculated m/z as printed (checked, not stored)
    warnings: list[str] = field(default_factory=list)
    keep: bool = True
    # Its structure (SPEC §13u.7): a MOL block, how it was found, and the ChemDraw
    # picture (EMF) when the drawing shows just this structure; `use_structure` = the
    # review window's tick box.
    structure: str = ""
    structure_note: str = ""
    structure_picture: bytes | None = None
    use_structure: bool = True
    # From a PDF: where its heading and text are (page, heading's bottom, text's page,
    # text's top; pt), and the picture of its structure cut out of the page (a PDF).
    geometry: tuple | None = None
    picture_pdf: bytes | None = None
    end: tuple = (0, 0.0)       # PDF: (page, bottom in pt) where its text ends


@dataclass
class ImportPlan:
    gps: list[ImportedGP] = field(default_factory=list)
    compounds: list[ImportedCompound] = field(default_factory=list)
    other: list[Para] = field(default_factory=list)
    section_name: str = ""      # the heading above the compounds ("Synthesis and …")
    warnings: list[str] = field(default_factory=list)
    spectra: list = field(default_factory=list)   # si_import_spectra.ImportedSpectrum
    # Phase I4 (core/si_import_text.py): everything in reading order — ("para", Para),
    # ("gp", ImportedGP), ("compound", ImportedCompound) — and what it makes of it.
    sequence: list = field(default_factory=list)
    sections: list = field(default_factory=list)   # si_import_text.ImportedSection
    references: list = field(default_factory=list)  # si_import_refs.ImportedReference
    cover: object = None                            # si_import_text.ImportedCover
    body_size: float = 0.0                          # PDF: the body text's size (pt)
    headings: list = field(default_factory=list)    # the paragraphs taken for headings
    levels: dict = field(default_factory=dict)      # id(paragraph) -> its level (1–3)
    level_overrides: dict = field(default_factory=dict)   # …as changed in the review window


# ---------------------------------------------------------------------- headings

# A compound ID as printed: 2a, 3b′, 10aa, S12, SI-3 (with an optional "rac-"/"(S)-"…
# before it, which is not part of the ID).
ID = (r"(?:[A-Z]{1,4}-?|[a-z]{2,4}-)?\d{1,3}[a-z]{0,3}[′'″‴]*"
      r"(?:-[A-Z][A-Za-z]{0,3})?")          # (also "3a-DA", "SI-02", "TS-2", "int-4b")
STEREO = r"(?:\((?:[RSEZ](?:,\s*[RSEZ])*|rac|ent|±|[+−-])\)-|rac-|ent-|\(±\)-)?"
_HEAD = re.compile(rf"^(?P<name>.*?\S)\s*\(\s*(?P<stereo>{STEREO})(?P<id>{ID})\s*\)\s*[.:]?\s*$")
_COMPOUND_WORD = re.compile(rf"^(?:Compound|Product)\s+(?P<id>{ID})\s*[.:]?\s*$", re.I)
_RUN_IN = re.compile(rf"^(?P<name>[^.]{{3,250}}?)\s*\(\s*(?P<stereo>{STEREO})(?P<id>{ID})\s*\)"
                     rf"\s*[.:]\s+(?P<rest>\S.*)$", re.S)
# Words that make a heading a sentence rather than a compound's name.
_NOT_NAME = re.compile(r"\b(?:was|were|is|are|the|to|from|with|by|using|according|following|"
                       r"prepared|obtained|afforded|gave|yield|synthesis|general|procedure|"
                       r"see|table|scheme|figure)\b", re.I)

_GP = re.compile(r"^(?:(?:\d+(?:\.\d+)*|S\d+|[IVX]+)\.?\s+)?(?P<head>"
                 r"(?i:general|typical|representative)\s+(?i:experimental\s+)?"
                 r"(?i:procedures?)|GP)(?P<rest>.*)$", re.S)
_GP_LABEL = re.compile(r"^\s*[-–—]?\s*\(?(?P<label>[A-Z]{1,2}|\d{1,2}|[IVX]{1,4})\)?"
                       r"(?=[\s:.,)–—-]|$)")
_GP_LABEL_INSIDE = re.compile(r"\((?:GP|General\s+Procedure)\s*[-–]?\s*(?P<label>[A-Z]{1,2}|"
                              r"\d{1,2}|[IVX]{1,4})\)", re.I)
_LEADERS = re.compile(r"(?:\.\s?){5,}|…{2,}")   # a table of contents' dot leaders
_CAPTION = re.compile(r"^(?:Scheme|Figure|Fig\.|Table|Chart)\s+S?\d+[A-Za-z]?\b")


def looks_like_name(text: str) -> bool:
    """A compound's name rather than a sentence or a heading."""
    return (2 < len(text) <= 300 and any(ch.isalpha() for ch in text)
            and not _NOT_NAME.search(text) and ". " not in text)


def compound_heading(text: str) -> tuple[str, str] | None:
    """(name, ID) of a paragraph that is a compound's heading, e.g.
    "1-Phenylethan-1-ol (2a)." → ("1-Phenylethan-1-ol", "2a"); "Compound 3b" → ("", "3b")."""
    text = clean_space(text)
    m = _COMPOUND_WORD.match(text)
    if m:
        return "", m.group("id")
    m = _HEAD.match(text)
    if m and looks_like_name(m.group("name")):
        return m.group("name").rstrip(" ,:"), m.group("id")
    return None


def gp_heading(text: str) -> tuple[str, str] | None:
    """(label, title) of a general procedure's heading ("" when it has no label/title),
    e.g. "General Procedure A: Reduction of ketones" → ("A", "Reduction of ketones")."""
    text = clean_space(text)
    m = _GP.match(text)
    if not m:
        return None
    rest = m.group("rest")
    label = ""
    lm = _GP_LABEL.match(rest)
    if lm and not re.match(r"\s*(?:for|of)\b", rest, re.I):
        label = lm.group("label")
        rest = rest[lm.end():]
    inside = _GP_LABEL_INSIDE.search(rest)
    if inside:
        label = label or inside.group("label").upper()
        rest = (rest[:inside.start()] + rest[inside.end():])
    title = rest.strip(" :.,–—-")
    title = re.sub(r"^(?:for|of)\s+(?:the\s+)?", "", title, flags=re.I)
    if title:
        title = title[0].upper() + title[1:]
    if re.search(r"(?i)\b(?:was|were|is|are|followed|used)\b", title):   # a sentence about a GP
        return None
    return label, title


def is_section_heading(p: Para, body_size: float) -> bool:
    if p.heading:
        return True
    text = p.text
    if not text or len(text) > 120:
        return False
    if not re.search(r"[A-Za-z]{3}", text):      # (e.g. a scheme's labels "2e 3")
        return False
    if p.size and p.size > body_size + 0.9 and len(text) < 120:
        return True
    if p.bold_share() < 0.9 or text.endswith((",", ";")):
        return False
    # a value's label standing alone ("Enantiomeric Excess:", "HRMS (ESI):"): its value
    # follows on the next line; it belongs to the compound
    own = _item_spans(text)[:1]
    return not (own and own[0][1] == 0 and text.rstrip().endswith(":"))


def _bold_prefix(runs: list[Run]) -> tuple[list[Run], list[Run]]:
    """(the bold runs at the start, the rest)."""
    head: list[Run] = []
    i = 0
    while i < len(runs) and (runs[i].bold or not runs[i].text.strip()):
        head.append(runs[i])
        i += 1
    while head and not head[-1].text.strip():   # (spaces after the bold part: the rest's)
        i -= 1
        head.pop()
    return head, runs[i:]


def _plain(runs: list[Run]) -> str:
    return clean_space("".join(r.text for r in runs))


def _markup(runs: list[Run]) -> str:
    return serialize([Paragraph(merge_runs(runs))]).strip()


def _strip_leading(runs: list[Run], chars: str) -> list[Run]:
    out = list(runs)
    while out:
        t = out[0].text.lstrip(chars)
        if t:
            out[0] = Run(t, out[0].bold, out[0].italic, out[0].valign)
            break
        out.pop(0)
    return out


# ---------------------------------------------------------------------- data values

NMR_HEAD = re.compile(r"(?<![\w{])(?:(?P<mass>\d{1,3})\s*(?P<el>[A-Z][a-z]?)|(?P<el2>[A-Z][a-z]?)-"
                      r"(?P<mass2>\d{1,3}))(?:\s*\{\s*\d{1,3}\s*[A-Z][a-z]?\s*\})?\s*-?\s*NMR\b")
_KEYS = {
    "nmr": NMR_HEAD,
    "hrms": re.compile(r"\b(?:HRMS|HR-?MS|HR\s?-?(?:ESI|EI|APCI|CI|FAB|MALDI)\s?-?(?:TOF\s?-?)?MS|"
                       r"(?:ESI|EI|APCI)-?HRMS)\b"),
    "ir": re.compile(r"\b(?:FT-?)?IR\b(?=\s*[(:ν=]|\s*\(|\s+\d|\s*ν)"),
    "rf": re.compile(r"\bR\s?f\b(?=\s*[=:(]|\s+\d|\s*\()"),
    "mp": re.compile(r"(?<![\w.])(?:m\.\s?p\.|mp|Mp|M\.p\.|[Mm]elting\s+point)(?=\s*[=:(]|\s+\d|\s*[–-])"),
    "rotation": re.compile(r"\[\s*[αa]\s*\]"),
    "ea": re.compile(r"\b(?:Anal\.|Elemental\s+analysis|Anal\s+[Cc]alcd)"),
    "ee": re.compile(r"\b[Ee]nantiomeric\s+(?:[Ee]xcess|[Rr]atio)\b|"
                     r"\b(?:HPLC|SFC|chiral\s+GC|GC)\b(?=\s*[(:]|\s+analysis)|"
                     r"(?<![\w.])\d+(?:\.\d+)?\s*%\s*ee\b|\b(?:ee|er|e\.r\.)\s*[=:]"),
    "yield": re.compile(r"\b(?:[Ii]solated\s+)?[Yy]ield\s*:"),
    "dr": re.compile(r"(?<![\w])(?:d\.\s?r\.|dr)(?=\s*[=:>]|\s+\d|\s*\()"),
    "xray": re.compile(r"\bCCDC\b"),
    "references": re.compile(r"(?:[Ss]pectr(?:al|oscopic)\s+data|[Tt]he\s+(?:NMR\s+)?data|"
                             r"[Aa]nalytical\s+data|[Dd]ata)\s+(?:\w+\s+){0,4}?(?:matched|"
                             r"match|were\s+(?:consistent|in\s+(?:good\s+)?agreement)|"
                             r"(?:are|is)\s+(?:consistent|in\s+agreement)|agree|corresponded|"
                             r"correspond)"),
}
_STATE_WORDS = (r"oil|solid|liquid|powder|crystals?|foam|gum|wax|syrup|semi-?solid|needles|"
                r"plates|prisms|glass|film|amorphous\s+solid|crystalline\s+solid|resin")
_STATE = re.compile(rf"^(?P<state>(?:[A-Za-z][\w-]*\s+){{0,4}}?(?:{_STATE_WORDS}))\b\s*"
                    r"(?:\((?P<inside>[^()]*(?:\([^()]*\)[^()]*)*)\))?\s*[.;,]?\s*", re.I)
# "… as a white to off-white crystalline solid", "… as a pale yellow oil/crystal"
_STATE_IN_TEXT = re.compile(rf"\bas\s+(?:an?\s+)?(?P<state>(?:[A-Za-z][\w-]*\s+){{0,5}}?"
                            rf"(?:{_STATE_WORDS})(?:/(?:{_STATE_WORDS}|crystal))?)\b\s*"
                            r"(?:\((?P<inside>[^()]*)\))?", re.I)
_YIELD_WORD = re.compile(r"^(?:Yield|Isolated\s+yield)\b\s*[:=]?\s*(?P<inside>[\d(].*?)[.;]?\s*$",
                         re.I)
_MASS = re.compile(r"(?P<v>\d+(?:\.\d+)?)\s*(?P<u>mg|g|µg|μg|kg)\b")
_PERCENT = re.compile(r"(?P<sign>[>≥]\s*)?(?P<v>\d+(?:\.\d+)?)\s*%(?!\s*(?:ee|de|e\.e\.))")
_LABEL_BEFORE = re.compile(r"(?:^|[.;]\s+)(?P<label>[A-Za-z][A-Za-z ,'′-]{1,40}):\s*$")


def nucleus_of(report: str) -> str:
    m = NMR_HEAD.search(report)
    if not m:
        return ""
    if m.group("mass"):
        return f"{m.group('mass')}{m.group('el')}"
    return f"{m.group('mass2')}{m.group('el2')}"


def _clean_state(text: str) -> str:
    text = clean_space(text)
    # "Colorless oil" → "colorless oil" (the program capitalises it where it starts a
    # sentence); a word in capitals (e.g. "IR") stays.
    if text and text[0].isupper() and (len(text) < 2 or not text[1].isupper()):
        text = text[0].lower() + text[1:]
    return text


def _inside_values(inside: str) -> dict:
    """Mass and yield (and d.r.) from what a state's parentheses hold:
    "57.4 mg, 94%", "94% yield", "57 mg, 0.47 mmol, 94%"."""
    out: dict = {}
    m = _MASS.search(inside)
    if m:
        out["mass"], out["mass_unit"] = m.group("v"), {"μg": "µg"}.get(m.group("u"), m.group("u"))
    p = _PERCENT.search(inside)
    if p:
        out["percent"] = (p.group("sign") or "").strip() + p.group("v")
        note = re.sub(r"(?i)^\s*(?:yield\b)?[\s,;]*", "", inside[p.end():]).strip(" ,;")
        if note and not re.search(r"(?i)\b(?:ee|er|dr|d\.r\.)\b|\d+\s*:\s*\d+", note):
            out["note"] = note       # "(57.4 mg, 94% over two steps)"
    elif re.search(r"\bquant(?:\.|itative)", inside, re.I):
        out["percent"] = "quant."
    return out


def _paren(text: str, start: int) -> tuple[str, int]:
    """The text inside the parentheses that open at `start` (nested ones kept), and the
    index after the closing one; ("", start) if there are none."""
    if start >= len(text) or text[start] != "(":
        return "", start
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return text[start + 1:], len(text)


def _after(text: str, pattern: str) -> int:
    m = re.match(pattern, text)
    return m.end() if m else 0


def _number_after(text: str) -> str:
    m = re.search(r"[-−–+]?\d+(?:\.\d+)?", text)
    return m.group(0).replace("−", "-").replace("–", "-") if m else ""


ADDUCT = re.compile(r"\[\s*\d*\s*M\s*(?:[+\-−–]\s*\d*\s*[A-Z][A-Za-z0-9]*\s*)*\]\s*(?:\d?\s*[+\-−–•·]\s*)+"
                    r"|\bM\s*[+\-−–]\s*(?:H|Na|K|NH4|Li)\b|\bM\s*[+•·]")


def adduct_text(raw: str) -> str:
    """"[M + H]+" → "[M+H]+", "[M−H]−" kept, "M+Na" → "[M+Na]+"."""
    t = re.sub(r"\s+", "", raw).replace("–", "−").replace("·", "•")
    if not t.startswith("["):
        if re.fullmatch(r"M[+•]", t):
            return "[M]+" if t == "M+" else "[M]+•"
        t = f"[{t}]+"
    t = re.sub(r"\](.*)$", lambda m: "]" + m.group(1).replace("-", "−"), t)
    inner = t[1:t.index("]")].replace("−", "-")
    out = "[" + inner.replace("-", "−") + t[t.index("]"):]
    return out


def parse_hrms(text: str) -> tuple[dict, str, str]:
    """({"ionization", "adduct", "found", "formula"}, the calculated m/z as printed, the
    ion's formula as printed) from an HRMS sentence such as
    "HRMS (ESI) m/z: [M+Na]+ calcd for C10H11BrNaO2 264.9835; found 264.9837"."""
    value: dict = {}
    head = _KEYS["hrms"].search(text)
    rest = text[head.end():] if head else text
    m = re.match(r"\s*\((?P<ion>[^()]{1,30})\)", rest)
    if m:
        ion = m.group("ion").strip()
        value["ionization"] = ion
    elif head:
        inline = re.search(r"(ESI|EI|APCI|CI|FAB|MALDI|DART|APPI)", head.group(0))
        if inline:
            value["ionization"] = inline.group(1)
    a = ADDUCT.search(text)
    if a:
        value["adduct"] = adduct_text(a.group(0))
    found = re.search(r"(?i)\b(?:found|obsd\.?|observed|measured)\b\s*(?:m/z)?\s*[:=]?\s*"
                      r"(?P<v>\d+\.\d+)", text)
    if found:
        value["found"] = found.group("v")
    calcd = ""
    formula = ""
    # "calcd for C10H11BrNaO2", "calculated for [C12H12N]+:", "calcd for (C9H12NO)+"
    c = re.search(r"(?i)\bcalc(?:ulate)?d\.?\s*(?:mass\s*)?(?:for\s*)?"
                  r"(?P<f>[\[(]?[A-Z][A-Za-z0-9]*[\])]?\s*[+\-−]?)?", text)
    if c:
        f = re.sub(r"[\[\]()\s]", "", c.group("f") or "")
        if re.fullmatch(r"(?:[A-Z][a-z]?\d*)+[+\-−]?", f) and re.search(r"\d", f):
            formula = f
        num = re.search(r"\d+\.\d{3,}", text[c.end():])
        if num:
            calcd = num.group(0)
    return value, calcd, formula


def m_formula(ion_formula: str, adduct: str, calcd: str) -> str:
    """The neutral M formula from the ion formula printed with "calcd for": the adduct
    taken off ("C12H14NO" with [M+H]+ → "C12H13NO"). The printed calculated m/z decides
    whether the printed formula is the ion's or already M's. "" when it can't be told."""
    if not ion_formula or not adduct:
        return ""
    try:
        counts = chem.parse_formula(ion_formula.rstrip("+-−"))
        add = chem.parse_adduct(adduct)
    except chem.ChemError:
        return ""
    target = None
    try:
        target = float(calcd) if calcd else None
    except ValueError:
        target = None
    candidates = []
    stripped = counts.copy()
    ok = True
    for el, n in add.delta.items():
        stripped[el] -= n * 1
        if stripped[el] < 0:
            ok = False
    if ok:
        candidates.append(+stripped)
    candidates.append(+counts)     # the printed formula is M's already
    for cand in candidates:
        try:
            info = chem.info_from_counts(cand)
            # (the printed value may be for an isotope peak, e.g. the ⁸¹Br one)
            mzs = [chem.ion_mz(info, adduct).mz] + [
                pk.mz for pk in chem.isotope_pattern(info, adduct, min_percent=20.0)]
        except (chem.ChemError, ZeroDivisionError, AttributeError):
            continue
        if target is None or any(abs(mz - target) < 0.003 for mz in mzs):
            return chem.hill_formula(cand)
    return ""


def parse_ir(text: str) -> dict:
    head = _KEYS["ir"].search(text)
    rest = text[head.end():] if head else text
    value = {}
    inside, end = _paren(rest.lstrip(), 0)
    if inside and len(inside) < 25 and not re.search(r"\d{3}", inside):
        value["method"] = inside.strip()
        rest = rest.lstrip()[end:]
    rest = re.sub(r"^\s*[:=]?\s*(?:ν|v|nu)?\s*(?:_?\{?max\}?)?\s*[:=]?\s*", "", rest)
    rest = re.sub(r"\s*\(?\s*cm\s*[-−–]?\s*[¹1]\s*\)?\s*[.;,]?\s*$", "", rest)
    rest = re.sub(r"\s*cm\s*[-−–]\s*1", "", rest).strip(" .;:")
    if re.search(r"\d{3}", rest):
        value["bands"] = rest
    return value


_STAINS = re.compile(r"(?i)^(?:UV.*|KMnO.*|CAM|PMA|p-?anisaldehyde|anisaldehyde|vanillin|"
                     r"ninhydrin|I2|iodine|phosphomolybdic.*|ceric.*|Dragendorff.*|.*\bstain)$")


def parse_rf(text: str) -> dict:
    m = re.search(r"R\s?f\s*(?:value)?\s*[=:]?\s*(?P<v>\d?\.\d+|\d(?:\.\d+)?)", text)
    if not m:
        return {}
    value = {"value": m.group("v")}
    after = text[m.end():].lstrip()
    inside, end = _paren(after, 0)
    vis: list[str] = []
    if inside:
        parts = [p.strip() for p in re.split(r",\s*", inside)]
        vis = [p for p in parts if _STAINS.match(p)]
        eluent = [p for p in parts if p not in vis]
        if eluent:
            value["eluent"] = ", ".join(eluent)
    # the stains may follow the parentheses: "(100% Et2O), UV – 254 nm, KMnO4 stain"
    tail = after[end:].strip(" ,;.") if inside else ""
    if tail:
        more = [p.strip() for p in re.split(r",\s*", tail)]
        if all(_STAINS.match(p) for p in more):
            vis += more
    if vis:
        value["visualization"] = ", ".join(vis)
    return value


_STEPS = re.compile(r"(?i)^\s*(?:over|in|for)\s+(?:\w+|\d+)\s+steps?\b")


def _yield_note(text: str, percent: re.Match) -> tuple[str, int, int]:
    """What qualifies a yield (the Yield field's note, SPEC §13u.13): the rest of the
    parentheses it is in ("57% over two steps", "76% from 2a", "64% brsm"), or "over two
    steps" right after them. (note, start, end) in `text`; ("", 0, 0) if none."""
    open_ = text.rfind("(", 0, percent.start())
    close = text.find(")", percent.end())
    if open_ >= 0 and close >= 0 and ")" not in text[open_:percent.start()]:
        inside = text[percent.end():close]
        note = re.sub(r"(?i)^\s*(?:yield\b)?[\s,;]*", "", inside).strip(" ,;")
        if note and not re.search(r"(?i)\b(?:ee|er|dr|d\.r\.)\b|\d+\s*:\s*\d+", note):
            return note, percent.end(), close
        after = text[close + 1:]
        m = _STEPS.match(after)
        if m:
            return m.group(0).strip(), close + 1, close + 1 + m.end()
    m = _STEPS.match(text[percent.end():])
    if m:
        return m.group(0).strip(), percent.end(), percent.end() + m.end()
    return "", 0, 0


def parse_yield(text: str) -> tuple[dict, str, bool]:
    """From "Isolated Yield: 1.25 g (64%) as a pale-yellow crystalline solid."
    the Yield field's value, the physical state ("" if none) and whether there is more
    than these (e.g. "over two steps", "(Hygroscopic!)"), to keep as text."""
    body = re.sub(r"^\s*(?:[Ii]solated\s+)?[Yy]ield\s*:\s*", "", text)
    value: dict = {}
    rest = body
    m = _MASS.search(body)
    if m:
        value["mass"], value["mass_unit"] = m.group("v"), {"μg": "µg"}.get(m.group("u"), m.group("u"))
        rest = rest.replace(m.group(0), " ", 1)
    p = _PERCENT.search(rest)
    if p:
        value["percent"] = (p.group("sign") or "").strip() + p.group("v")
        note, start, end = _yield_note(rest, p)
        if note:
            value["note"] = note
            rest = rest[:start] + " " + rest[end:]
        rest = rest.replace(p.group(0), " ", 1)
    elif re.search(r"\bquant(?:\.|itative)", rest, re.I):
        value["percent"] = "quant."
    state = ""
    s = _STATE_IN_TEXT.search(rest)
    if s:
        state = s.group("state")
        rest = rest[:s.start()] + " " + rest[s.start() + len("as") + 1:].replace(state, " ", 1)
    rest = re.sub(r"\b(?:an?|as|yield)\b", " ", rest)
    more = bool(re.search(r"[A-Za-z]{3}", rest))
    return value, state, more


def parse_mp(text: str) -> dict:
    m = re.search(r"(?P<v>\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+(?:\.\d+)?)?)\s*°?\s*C?", text)
    if not m:
        return {}
    value = {"value": re.sub(r"\s*[-—]\s*", "–", m.group("v")).replace(" – ", "–")}
    inside, _ = _paren(text[m.end():].lstrip(" .,;"), 0)
    if inside and not re.search(r"\d", inside):
        value["solvent"] = inside.strip()
    return value


def parse_rotation(text: str) -> dict:
    m = re.search(r"\[\s*[αa]\s*\]\s*(?P<a>[^=:\s(]*)\s*(?P<b>[^=:\s(]*)?\s*[=:]?\s*"
                  r"(?P<v>[+\-−–]?\s*\d+(?:\.\d+)?)", text)
    if not m:
        return {}
    value = {"value": m.group("v").replace(" ", "").replace("−", "-").replace("–", "-")}
    temp = re.search(r"\d+", (m.group("a") or "") + (m.group("b") or ""))
    if temp:
        value["temperature"] = temp.group(0)
    inside, _ = _paren(text[m.end():].lstrip(" ,"), 0)
    if inside:
        cm = re.search(r"\bc\s*[=:]?\s*(?P<c>\d+(?:\.\d+)?)", inside)
        if cm:
            value["c"] = cm.group("c")
        parts = [p.strip() for p in inside.split(",")]
        solvent = [p for p in parts if p and not re.match(r"c\s*[=:]?\s*\d", p)
                   and not re.search(r"g/(?:100\s*)?mL", p)]
        if solvent:
            value["solvent"] = solvent[-1]
    if value["value"][0].isdigit():
        value["value"] = "+" + value["value"] if False else value["value"]
    return value


def parse_ea(text: str) -> dict:
    value = {}
    f = re.search(r"(?i)calc(?:ulate)?d\.?\s*(?:for\s*)?(?P<f>[A-Z][A-Za-z0-9·•.]*)\s*[:;,]?", text)
    if f and re.search(r"\d", f.group("f")):
        value["formula"] = f.group("f").rstrip(":;,.")
    calcd = re.search(r"(?i)calc(?:ulate)?d[^:]*?:?\s*(?P<v>(?:[A-Z][a-z]?\s*,?\s*\d+\.\d+\s*[;,]?\s*)+)", text)
    found = re.search(r"(?i)found\s*[:]?\s*(?P<v>(?:[A-Z][a-z]?\s*,?\s*\d+\.\d+\s*[;,]?\s*)+)", text)
    if calcd:
        value["calcd"] = calcd.group("v").strip(" ;,.")
    if found:
        value["found"] = found.group("v").strip(" ;,.")
    return value if ("calcd" in value or "found" in value) else {}


def parse_dr(text: str) -> dict:
    m = re.search(r"(?:d\.\s?r\.|dr)\s*[=:]?\s*(?P<v>[>≥]?\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?)", text)
    if not m:
        return {}
    value = {"value": re.sub(r"\s+", "", m.group("v")).replace(">", "> ").replace("≥", "≥ ")
             .replace("> ", ">").replace("≥ ", "≥")}
    inside, _ = _paren(text[m.end():].lstrip(" ,"), 0)
    if inside and not re.search(r"\d+\s*:\s*\d+", inside):
        value["method"] = inside.strip()
    return value


_COLUMN = re.compile(r"(?i)\b(?:chiral(?:pak|cel)|lux|whelk|cyclobond|daicel|regis|phenomenex)"
                     r"[^,;.]*?(?:\([^()]*\))?(?=\s*[,;.]\s|\s*$|\s*,)")
_ELUENT = re.compile(r"\d+(?:\.\d+)?\s*%\s*[A-Za-z][\w-]*(?:\s*(?:,|/|in)\s*(?:\d+(?:\.\d+)?\s*%"
                     r"\s*)?[nA-Za-z][\w-]*)*")


def parse_ee(text: str) -> dict:
    """ee or er with how it was measured, from "HPLC (Chiralpak IA, 5% iPrOH/hexanes, 1.0
    mL/min, 220 nm): tR (major) = 9.8 min, tR (minor) = 11.2 min; 92% ee" or from
    "Enantiomeric Excess: Determined by HPLC analysis to be 98%. CHIRALPAK® - ID (4.6 x
    250mm). 10% Isopropanol, 90% n-Hexane. 1.0 mL/min, λ = 220nm. Retention times: 12.0
    min (minor) 13.3 min (major)."."""
    value: dict = {}
    m = re.search(r"(?P<v>[>≥]?\s*\d+(?:\.\d+)?)\s*%\s*ee\b", text)
    if m:
        value["value"] = re.sub(r"\s+", "", m.group("v")) + "% ee"
    else:
        m = (re.search(r"\b(?:ee|er|e\.r\.)\s*[=:]\s*(?P<v>\d+(?:\.\d+)?\s*(?:%|:\s*\d+(?:\.\d+)?))", text)
             or re.search(r"(?i)enantiomeric\s+(?:excess|ratio)\b[^.;]*?(?:to\s+be|=|:|of|was)\s*"
                          r"(?P<v>[>≥]?\s*\d+(?:\.\d+)?\s*(?:%|:\s*\d+(?:\.\d+)?))", text))
        if m:
            v = re.sub(r"\s+", "", m.group("v"))
            value["value"] = f"{v} ee" if v.endswith("%") else f"{v} er"
    method = re.search(r"\b(HPLC|SFC|GC)\b", text)
    if method:
        value["method"] = method.group(1)
    col = _COLUMN.search(text)
    if col:
        value["column"] = clean_space(col.group(0)).rstrip(" ,;.")
    elu = _ELUENT.search(text[col.end():] if col else text)
    if elu and not re.fullmatch(r"\d+(?:\.\d+)?\s*%\s*ee", elu.group(0)):
        value["eluent"] = elu.group(0).strip()
    flow = re.search(r"(\d+(?:\.\d+)?)\s*mL\s*/\s*min", text)
    if flow:
        value["flow"] = flow.group(1)
    nm = re.search(r"(?:λ\s*=?\s*)?(\d{3})\s*nm\b", text)
    if nm:
        value["wavelength"] = nm.group(1)
    tmaj = re.search(r"(?i)t\s?_?\{?R\}?\s*\(?\s*maj(?:or)?\s*\)?\s*[=:]?\s*(?P<v>\d+(?:\.\d+)?)", text) or \
        re.search(r"(?i)(?P<v>\d+(?:\.\d+)?)\s*min\s*\(\s*maj", text)
    tmin = re.search(r"(?i)t\s?_?\{?R\}?\s*\(?\s*min(?:or)?\s*\)?\s*[=:]?\s*(?P<v>\d+(?:\.\d+)?)", text) or \
        re.search(r"(?i)(?P<v>\d+(?:\.\d+)?)\s*min\s*\(\s*min", text)
    if tmaj:
        value["t_major"] = tmaj.group("v")
    if tmin:
        value["t_minor"] = tmin.group("v")
    return value if value.get("value") or value.get("t_major") else {}


def parse_ccdc(text: str) -> dict:
    m = re.search(r"CCDC\s*(?:No\.?|number)?\s*[:#]?\s*(?P<v>\d{6,8})", text)
    return {"ccdc": m.group("v")} if m else {}


def _citations(text: str) -> tuple[str, list[str]]:
    """(the sentence without its citation numbers, the numbers) — "…literature.3,4" →
    ("…literature.", ["3", "4"]); "[3]", "(3–5)", "ref 3" too."""
    cites: list[str] = []

    def numbers(spec: str) -> list[str]:
        out = []
        for part in re.split(r"\s*,\s*", spec):
            r = re.match(r"(\d+)\s*[-–]\s*(\d+)$", part)
            if r and int(r.group(2)) - int(r.group(1)) < 50:
                out += [str(i) for i in range(int(r.group(1)), int(r.group(2)) + 1)]
            elif part.isdigit():
                out.append(part)
        return out
    m = re.search(r"(?:\s*(?:\[|\()\s*(?P<a>\d+(?:\s*[-–,]\s*\d+)*)\s*(?:\]|\))|"
                  r"(?<=[a-z.)])\s?(?P<b>\d+(?:\s*[-–,]\s*\d+)*))\s*\.?\s*$", text)
    if m:
        cites = numbers(m.group("a") or m.group("b"))
        text = text[:m.start()].rstrip()
    text = text.rstrip(" ;,")
    if text and not text.endswith("."):
        text += "."
    return text, cites


# ---------------------------------------------------------------------- one compound


def _item_spans(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) of every value in a data paragraph, in order."""
    starts: list[tuple[int, str]] = []
    for kind, pattern in _KEYS.items():
        for m in pattern.finditer(text):
            starts.append((m.start(), kind))
    starts.sort()
    # How deep in parentheses each character is: a value inside parentheses belongs to
    # what they follow ("Colorless oil (57.4 mg, 74%, 92% ee)").
    depth, depths = 0, []
    for ch in text:
        depth += ch == "("
        depths.append(depth)
        depth -= ch == ")" and depth > 0
    # one start per place; an "ee" start inside an HPLC description belongs to it
    merged: list[tuple[int, str]] = []
    for pos, kind in starts:
        if depths[pos] > 0:
            continue
        if merged and pos - merged[-1][0] < 3:
            continue
        if merged and kind == "ee" and merged[-1][1] == "ee":
            continue
        merged.append((pos, kind))
    out = []
    for i, (pos, kind) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        out.append((kind, pos, end))
    return out


def _is_data(text: str) -> bool:
    """Whether a paragraph is (mostly) characterisation data."""
    own = _item_spans(text)[:1]
    if own and own[0][1] == 0:       # it starts with a value ("Isolated Yield: …", "HRMS …")
        return True
    body = re.sub(r"^[A-Z][A-Za-z ,'′-]{1,40}:\s*", "", text)   # "Major isomer: …"
    if _STATE.match(body) and len(body) < 400 or _YIELD_WORD.match(body):
        return True
    spans = _item_spans(body)
    if not spans:
        return False
    if spans[0][1] <= 3:
        return True
    covered = sum(e - s for _k, s, e in spans)
    return covered > 0.6 * len(body)


def parse_data(text: str, into: ImportedCompound) -> None:
    """Sort one data paragraph's values into the compound's fields (as printed);
    whatever is not recognised goes into `into.unrecognised`."""
    values = into.values
    # (spaces a PDF puts inside parentheses: "7.47 ( d, J = 8.5 Hz, 2H )")
    text = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", clean_space(text)))
    spans = _item_spans(text)
    prefix = text[:spans[0][1]] if spans else text
    label_next = ""
    # The text before the first value: a physical state with mass and yield, a label.
    rest = prefix.strip()
    if rest:
        lm = re.match(r"^(?P<label>[A-Z][A-Za-z ,'′-]{1,40}):\s*(?P<rest>.*)$", rest)
        if lm and not _STATE.match(rest):
            label_next, rest = lm.group("label"), lm.group("rest").strip()
        sm = _STATE.match(rest)
        ym = _YIELD_WORD.match(rest)
        if sm:
            _set_state(values, sm.group("state"), sm.group("inside") or "")
            rest = rest[sm.end():].strip()
        elif ym:
            got = _inside_values(ym.group("inside"))
            if got:
                y = values.setdefault("yield", {})
                for k, v in got.items():
                    y.setdefault(k, v)
                rest = ""
        if rest:
            lm = _LABEL_BEFORE.search(rest)
            if lm and spans:
                label_next = lm.group("label")
                rest = rest[:lm.start()].strip()
        if rest.strip(" .;,"):
            into.unrecognised.append(rest.strip())
    for kind, start, end in spans:
        piece = text[start:end].strip()
        # A label for the next value written at the end of this one ("…; minor isomer:")
        next_label = ""
        lm = _LABEL_BEFORE.search(piece)
        if lm and kind != "references":
            next_label = lm.group("label")
            piece = piece[:lm.start()].rstrip()
        piece = piece.rstrip(" ;,")
        ok = _parse_item(kind, piece, into, label_next)
        if not ok:
            into.unrecognised.append(piece)
        label_next = next_label


def _set_state(values: dict, state: str, inside: str) -> None:
    values.setdefault("physical_state", {"text": _clean_state(state)})
    got = _inside_values(inside)
    if got:
        y = values.setdefault("yield", {})
        for k, v in got.items():
            y.setdefault(k, v)
    ee = re.search(r"(\d+(?:\.\d+)?)\s*%\s*ee", inside)
    if ee:
        values.setdefault("ee", {"value": f"{ee.group(1)}% ee"})
    dr = re.search(r"(?:d\.\s?r\.|dr)\s*[=:]?\s*([>≥]?\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?)", inside)
    if dr:
        values.setdefault("dr", {"value": re.sub(r"\s+", "", dr.group(1))})


def _parse_item(kind: str, piece: str, into: ImportedCompound, label: str) -> bool:
    values = into.values
    if kind == "yield":
        got, state, more = parse_yield(piece)
        if not got and not state:
            return False
        if got:
            y = values.setdefault("yield", {})
            for k, v in got.items():
                y.setdefault(k, v)
        if state:
            values.setdefault("physical_state", {"text": _clean_state(state)})
        if more:          # ("over two steps", "(Hygroscopic!)": kept, to print by hand)
            into.unrecognised.append(piece if piece.endswith(".") else piece + ".")
        return True
    if kind == "nmr":
        # "1H NMR: (500 MHz, …)" → "1H NMR (500 MHz, …)"
        piece = re.sub(r"^(.{0,25}?NMR)\s*:\s*", r"\1 ", piece)
        report = piece if piece.endswith(".") else piece + "."
        if "." == report[-2:-1]:
            report = report[:-1]
        label = label.strip()
        # A label is printed in the report's conditions ("…, CDCl3, major rotamer"): a
        # short description only; a sentence ("Both rotamers are described together")
        # is kept as text instead.
        if label and (len(label) > 30 or re.search(r"\b(?:is|are|was|were|be|been)\b", label)):
            into.unrecognised.append(label + ":")
            label = ""
        elif label and label[0].isupper() and label[1:2].islower():
            label = label[0].lower() + label[1:]
        values.setdefault("nmr", []).append({
            "id": new_id("r"), "nucleus": nucleus_of(piece), "label": label,
            "text": report})
        return True
    if kind == "hrms":
        value, calcd, formula = parse_hrms(piece)
        if not value.get("found") and not calcd:
            return False
        if "hrms" in values:
            return False          # (a second HRMS: kept as text)
        if not value.get("adduct") and formula[-1:] in "+-−" and formula:
            # Only the ion's formula, no adduct ("calculated for [C12H12N]+"): a salt's
            # cation (by its name: "…pyridinium bromide") is [M]+ with M charged; anything
            # else was protonated (or took Na if the ion holds Na).
            sign = "−" if formula[-1] in "-−" else "+"
            if re.search(r"(?i)ium\b|\bsalt\b|\bate\b", into.name):
                value["adduct"] = f"[M]{sign}"
                f = m_formula(formula, value["adduct"], calcd)
                if f:
                    value["formula"] = f + ("+" if sign == "+" else "-")
            else:
                tries = (("[M+H]+", "[M+Na]+", "[M+K]+", "[M]+") if sign == "+" else
                         ("[M−H]−", "[M+Cl]−", "[M+HCOO]−", "[M]−"))
                bare = formula.rstrip("+-−")
                for adduct in tries:
                    f = m_formula(bare, adduct, calcd)
                    if f and (adduct.startswith("[M]") or
                              chem.hill_formula(chem.parse_formula(bare)) != f):
                        value["adduct"], value["formula"] = adduct, f
                        break
            f = value.get("formula", "")
        else:
            f = m_formula(formula, value.get("adduct", ""), calcd)
        if f:
            value["formula"] = f
        elif formula and calcd and value.get("adduct"):
            into.warnings.append(
                f"HRMS: the formula printed ({formula}) does not give the calculated m/z "
                f"printed ({calcd}) for {value['adduct']}; check which is wrong. No formula "
                "was taken from it.")
        into.hrms_calcd = calcd
        values["hrms"] = value
        return True
    parsers = {"ir": parse_ir, "rf": parse_rf, "mp": parse_mp, "rotation": parse_rotation,
               "ea": parse_ea, "ee": parse_ee, "dr": parse_dr, "xray": parse_ccdc}
    if kind in parsers:
        value = parsers[kind](piece)
        if not value or kind in values:
            return False
        values[kind] = value
        return True
    if kind == "references":
        sentence, cites = _citations(piece)
        if "references" in values:
            return False
        values["references"] = {"text": sentence, "refs": []}
        into.cites = cites
        return True
    return False


_SENTENCE_START = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9(\[])")


def data_start(text: str) -> int:
    """Where the data begin in a paragraph that starts with the procedure ("Prepared
    according to … . Colorless oil (57.4 mg, 94%). Rf = …"): the start of the first
    sentence from which on the rest is data; 0 if the paragraph starts with data or
    has none."""
    def starts_with_data(t: str) -> bool:
        own = _item_spans(t)[:1]
        if own and own[0][1] == 0:            # "Isolated Yield: …", "HRMS (ESI) …"
            return True
        # (a label may come first: "Both rotamers are described together: ¹H NMR …")
        unlabelled = re.sub(r"^[A-Z][A-Za-z ,'′-]{1,40}:\s*", "", t)
        first = _item_spans(unlabelled)[:1]
        return bool(_STATE.match(unlabelled) or (first and first[0][1] <= 3))

    if starts_with_data(text):
        return 0        # (the whole paragraph is data)
    for m in _SENTENCE_START.finditer(text):
        rest = text[m.end():]
        if (starts_with_data(rest) or _YIELD_WORD.match(rest.split(". ")[0])) \
                and _is_data(rest):
            return m.end()
    return 0


def split_runs(runs: list[Run], index: int) -> tuple[list[Run], list[Run]]:
    """The runs cut at character `index` of their plain (spaces evened) text."""
    raw = "".join(r.text for r in runs)
    # the raw index where the cleaned text's character `index` is
    cleaned, raw_cut, prev_space = 0, len(raw), True
    for i, ch in enumerate(raw.lstrip()):
        if cleaned >= index and not ch.isspace():
            raw_cut = i + (len(raw) - len(raw.lstrip()))
            break
        if ch.isspace() or ch in "   ":
            if not prev_space:
                cleaned += 1
            prev_space = True
        else:
            cleaned += 1
            prev_space = False
    before: list[Run] = []
    after: list[Run] = []
    pos = 0
    for r in runs:
        end = pos + len(r.text)
        if end <= raw_cut:
            before.append(r)
        elif pos >= raw_cut:
            after.append(r)
        else:
            k = raw_cut - pos
            before.append(Run(r.text[:k], r.bold, r.italic, r.valign))
            after.append(Run(r.text[k:], r.bold, r.italic, r.valign))
        pos = end
    return merge_runs(before), merge_runs(after)


def parse_compound(cid: str, name: str, paras: list[Para]) -> ImportedCompound:
    """One compound's block (its paragraphs after the heading) into an ImportedCompound."""
    c = ImportedCompound(cid=cid, name=name, page=paras[0].page if paras else 0)
    c.original = "\n".join(p.markup for p in paras)
    data_seen = False
    procedure: list[str] = []
    for p in paras:
        text = p.text
        if not text:
            continue
        if not data_seen:
            cut = data_start(text)
            if cut:     # the procedure, then the data, in one paragraph
                before, _after = split_runs(p.runs, cut)
                procedure.append(_markup(before))
                data_seen = True
                parse_data(text[cut:], c)
                continue
        if _is_data(text):
            data_seen = True
            parse_data(text, c)
        elif not data_seen:
            procedure.append(p.markup)
        else:
            c.unrecognised.append(text)
    c.procedure = "\n".join(procedure)
    # The state, mass and yield written in the procedure ("…to give 2a as a colorless oil
    # (57.4 mg, 94%)") when the data don't give them.
    if "physical_state" not in c.values or "yield" not in c.values:
        plain = clean_space(" ".join(_plain(parse(line)[0].runs) for line in procedure
                                     if line.strip()))
        m = _STATE_IN_TEXT.search(plain)
        if m:
            before = dict(c.values)
            _set_state(c.values, m.group("state"), m.group("inside") or "")
            if c.values != before:
                c.warnings.append(
                    "The procedure text gives the physical state or yield, which were put in "
                    "their fields. Printed from the fields, they would appear twice: remove "
                    "them from the procedure text, or print this compound exactly as imported.")
    if c.values.get("hrms", {}).get("found") is None and "hrms" in c.values:
        c.warnings.append("HRMS: no found m/z was recognised.")
    return c


# ---------------------------------------------------------------------- the whole SI


@dataclass
class _Block:
    kind: str                   # "gp", "compound" or "other"
    head: tuple                 # gp: (label, title); compound: (name, id); other: ()
    heading_para: Para | None
    paras: list[Para] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)    # drawings anchored in it


def _split_run_in(p: Para) -> tuple[str, tuple, list[Run], Para | None]:
    """A paragraph that starts a block: ("compound"|"gp", head, heading runs, the rest as
    a paragraph or None); ("", (), [], None) if it starts none."""
    text = p.text
    whole_gp = gp_heading(text) if len(text) <= 200 and ". " not in text.rstrip(".") else None
    head_runs, rest_runs = _bold_prefix(p.runs)
    head_text = _plain(head_runs)
    rest_para = None
    if head_runs and rest_runs and _plain(rest_runs):
        rest_para = Para(merge_runs(_strip_leading(rest_runs, " .:–—-")), page=p.page,
                         size=p.size)
    # a compound heading on its own, or run in (bold, or "Name (2a). Text…")
    h = compound_heading(text)
    if h:
        return "compound", h, p.runs, None
    if head_runs and rest_para is not None:
        h = compound_heading(head_text)
        if h:
            return "compound", h, head_runs, rest_para
        # only the ID, in bold, starting the text: "**SI-01.** Acetone (56 mL, …"
        only_id = re.fullmatch(rf"{STEREO}(?P<id>{ID})\s*[.:]", head_text)
        if only_id:
            return "compound", ("", only_id.group("id")), head_runs, rest_para
    m = _RUN_IN.match(text)
    if m and looks_like_name(m.group("name")) and len(m.group("name")) < 200:
        name, cid = m.group("name"), m.group("id")
        cut = text.index(m.group("rest"))
        runs = _cut_runs(p.runs, cut)
        return "compound", (name, cid), [], Para(runs, page=p.page, size=p.size)
    # a general procedure's heading
    if whole_gp is not None:
        return "gp", whole_gp, p.runs, None
    if head_runs and rest_para is not None:
        g = gp_heading(head_text)
        if g is not None:
            return "gp", g, head_runs, rest_para
    m = _GP.match(text)
    if m:
        colon = re.match(r"^(.{0,80}?):\s+(?P<rest>\S.*)$", text, re.S)
        if colon:
            g = gp_heading(colon.group(1))
            if g is not None:
                cut = text.index(colon.group("rest"), colon.end(1))
                return "gp", g, [], Para(_cut_runs(p.runs, cut), page=p.page, size=p.size)
    return "", (), [], None


def _cut_runs(runs: list[Run], start: int) -> list[Run]:
    """The runs from character `start` of their plain (spaces evened) text on."""
    return split_runs(runs, start)[1]


def analyse(paras: list[Para]) -> ImportPlan:
    """The SI's general procedures, compounds and other text (see the module's text)."""
    plan = ImportPlan()
    sizes = [p.size for p in paras if p.size]
    body_size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    blocks: list[_Block] = [_Block("other", (), None)]
    last_heading = ""
    section_for_compounds = ""
    compound_seen = False
    all_objects: list[dict] = []
    for p in paras:
        for obj in p.objects:
            all_objects.append(obj)
        if p.table or (p.text and _LEADERS.search(p.text)):   # (a table, a contents line)
            if blocks[-1].kind == "other":
                blocks[-1].paras.append(p)
            else:
                blocks.append(_Block("other", (), None, [p]))
            blocks[-1].objects.extend(p.objects)
            continue
        if not p.text:
            blocks[-1].objects.extend(p.objects)    # (a paragraph with only a drawing)
            if blocks[-1].kind == "other" and p.objects:
                blocks[-1].paras.append(p)          # (in the text: a figure, §13u.16)
            continue
        if _CAPTION.match(p.text):
            # A figure's caption ends a compound's block; the short pieces of text just
            # before it are the figure's own (labels in a scheme), not the compound's.
            blk = blocks[-1]
            moved = []
            while blk.kind != "other" and blk.paras and len(blk.paras[-1].text) < 90 \
                    and not _is_data(blk.paras[-1].text) and blk.paras[-1].page == p.page \
                    and len(blk.paras) > 1:
                moved.insert(0, blk.paras.pop())
            blocks.append(_Block("other", (), None, moved + [p]))
            continue
        kind, head, head_runs, rest = _split_run_in(p)
        if kind:
            # The Data section's name: the heading above the first compound (or above
            # the first general procedure, if the compounds have none of their own).
            if kind == "compound" and not compound_seen:
                compound_seen = True
                section_for_compounds = last_heading or section_for_compounds
            elif not section_for_compounds:
                section_for_compounds = last_heading
            if kind == "compound":
                last_heading = ""
            blk = _Block(kind, head, p, objects=list(p.objects))
            if rest is not None:
                blk.paras.append(rest)
            blocks.append(blk)
            continue
        if is_section_heading(p, body_size):
            last_heading = p.text
            blocks.append(_Block("other", (), p, [], list(p.objects)))
            continue
        blocks[-1].paras.append(p)
        blocks[-1].objects.extend(p.objects)
    plan.section_name = section_for_compounds
    own_objects: list[list[dict]] = []          # per compound, in plan order

    seen_ids: dict[str, int] = {}
    gp_count = 0
    for blk in blocks:
        if blk.kind == "other":
            own = ([blk.heading_para] if blk.heading_para else []) + blk.paras
            plan.other.extend(p for p in own if p.text)
            plan.sequence.extend(("para", p) for p in own)
        elif blk.kind == "gp":
            gp_count += 1
            label, title = blk.head
            name = f"General Procedure {label}" if label else "General Procedure"
            text = "\n".join(p.markup for p in blk.paras)
            plan.gps.append(ImportedGP(f"gp{gp_count}", name, title, text, label,
                                       original="\n".join(p.text for p in
                                                          [blk.heading_para, *blk.paras] if p),
                                       end=_end_of(blk)))
            plan.sequence.append(("gp", plan.gps[-1]))
        else:
            name, cid = blk.head
            c = parse_compound(cid, name, blk.paras)
            if not blk.paras:
                c.warnings.append("No text was found for this compound.")
            if cid in seen_ids:
                c.warnings.append(f"The ID {cid} is used by another compound of this SI too.")
                c.keep = False
            seen_ids[cid] = len(plan.compounds)
            if blk.heading_para is not None and not c.page:
                c.page = blk.heading_para.page
            head = blk.heading_para
            if head is not None and head.page:
                first = blk.paras[0] if blk.paras else None
                c.geometry = (head.page, head.bottom, first.page if first else 0,
                              first.top if first is not None else 0.0)
            c.end = _end_of(blk)
            plan.compounds.append(c)
            plan.sequence.append(("compound", c))
            own_objects.append(blk.objects)
    _name_unlabelled_gps(plan)
    link_general_procedures(plan)
    for c in plan.compounds:
        purification_from_text(c)
    assign_structures(plan, all_objects, own_objects)
    if any(o.get("kind") == "mnova" for o in all_objects):
        from . import si_import_spectra
        plan.spectra = si_import_spectra.word_spectra(plan, all_objects, own_objects,
                                                      STEREO + ID)
    from . import si_import_text
    plan.body_size = body_size
    si_import_text.outline(plan, body_size)
    if not plan.compounds:
        plan.warnings.append("No compounds were recognised. Compound headings are expected "
                             "as a name followed by the ID in parentheses, e.g. "
                             "“1-Phenylethan-1-ol (2a).”")
    return plan


def _end_of(blk: "_Block") -> tuple:
    """(page, bottom) of a PDF block's last paragraph; (0, 0.0) from Word."""
    last = blk.paras[-1] if blk.paras else blk.heading_para
    return (last.page, last.bottom) if last is not None and last.page else (0, 0.0)


def _name_unlabelled_gps(plan: ImportPlan) -> None:
    unlabelled = [g for g in plan.gps if not g.label]
    if len(plan.gps) > 1 and unlabelled:
        used = {g.label for g in plan.gps if g.label}
        letters = (ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if ch not in used)
        for g in unlabelled:
            g.label = next(letters)
            g.name = f"General Procedure {g.label}"


# ---------------------------------------------------------------------- general procedures

_SENTENCE_END = re.compile(r"\.\s+(?=[A-Z0-9(*])")
# A full stop after these is not a sentence's end ("ca. 5 mL", "e.g. THF", "Fig. 2").
_ABBREVIATIONS = {"ca", "vs", "eq", "equiv", "approx", "ref", "refs", "e.g", "i.e", "cf",
                  "fig", "no", "vol", "al", "resp", "conc", "sat", "aq", "anhyd", "temp"}


def sentences(markup: str) -> list[str]:
    """A procedure's text (markup) cut into sentences (not inside parentheses)."""
    out, depth, start = [], 0, 0
    i = 0
    while i < len(markup):
        ch = markup[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "\n" and depth == 0:
            out.append(markup[start:i + 1])
            start = i + 1
        elif ch == "." and depth == 0:
            m = _SENTENCE_END.match(markup, i)
            word = re.search(r"([\w.]+)$", markup[start:i])
            if m and not (word and word.group(1).lower().strip(".") in _ABBREVIATIONS):
                out.append(markup[start:m.end()])
                start = m.end()
                i = m.end()
                continue
        i += 1
    if start < len(markup):
        out.append(markup[start:])
    return [s for s in out if s.strip()]


def cites_gp(sentence: str, gp: ImportedGP, only_one: bool) -> bool:
    plain = re.sub(r"[*^_{}\\]", "", sentence)
    for m in re.finditer(r"(?i)\b(?:general\s+(?:experimental\s+)?procedure|GP)\s*[-–]?\s*"
                         r"\(?(?P<label>[A-Z]{1,2}\b|\d{1,2}\b|[IVX]{1,4}\b)?", plain):
        label = m.group("label") or ""
        if gp.label and label.upper() == gp.label.upper():
            return True
        if only_one and (not label or label.lower() in ("a", "an", "the")):
            return True
    return False


# Words (with their digits and hyphens: "4-methylphenol", "4a", "CH2Cl2" are one each),
# numbers ("54.1", "88%"), markup marks, spaces, single characters.
_TOKEN = re.compile(r"\*\*|\^\{|_\{|\}|[\w′'-]*[^\W\d_][\w′'-]*|\d+(?:\.\d+)?%?|\s+|.")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _lcs_align(common: list[str], seq: list[str]) -> list[int] | None:
    """Positions in `seq` of the tokens of `common`, in order (a longest-common-
    subsequence alignment); None if `common` is not a subsequence of `seq`."""
    n, m = len(common), len(seq)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if common[i] == seq[j] else max(dp[i + 1][j],
                                                                          dp[i][j + 1])
    if dp[0][0] != n:
        return None
    out, i, j = [], 0, 0
    while i < n:
        if common[i] == seq[j] and dp[i][j] == dp[i + 1][j + 1] + 1:
            out.append(j)
            i += 1
        j += 1
    return out


def induce_template(texts: list[str]) -> tuple[list[str], list[list[str]]] | None:
    """What a general procedure's sentences in several compounds share: (the template's
    pieces, literal text and None for a blank alternately as a list where blanks are "",
    the blanks' values per sentence). None if they share too little to be one sentence
    with blanks."""
    seqs = [tokens(t) for t in texts]
    common = seqs[0]
    for s in seqs[1:]:
        sm = difflib.SequenceMatcher(None, common, s, autojunk=False)
        common = [tok for a, _b, size in sm.get_matching_blocks() for tok in common[a:a + size]]
    # whitespace and punctuation alone are no anchor between two blanks
    alignments = []
    for s in seqs:
        al = _lcs_align(common, s)
        if al is None:
            return None
        alignments.append(al)
    # gaps: before common[0], between common[k] and common[k+1], after the last
    n = len(common)
    gap_values = []
    for s, al in zip(seqs, alignments):
        bounds = [-1] + al + [len(s)]
        gap_values.append(["".join(s[bounds[k] + 1:bounds[k + 1]]) for k in range(n + 1)])
    pieces: list[str] = []          # alternately literal, blank marker (None → "")
    values: list[list[str]] = [[] for _ in seqs]
    literal = ""
    for k in range(n + 1):
        if any(g[k] for g in gap_values):
            if pieces or literal:
                pieces.append(literal)
            else:
                pieces.append("")
            pieces.append(None)   # type: ignore[arg-type]
            for i, g in enumerate(gap_values):
                values[i].append(g[k])
            literal = ""
        if k < n:
            literal += common[k]
    pieces.append(literal)
    # Merge blanks separated only by a space, a hyphen or a slash (one value, cut up).
    merged_pieces: list = [pieces[0]]
    merged_values = [[] for _ in seqs]
    blank_index = 0
    i = 1
    while i < len(pieces):
        if pieces[i] is None:
            vals = [v[blank_index] for v in values]
            blank_index += 1
            while i + 2 < len(pieces) and pieces[i + 2] is None and \
                    re.fullmatch(r"\s*[-–/]?\s*", pieces[i + 1]):
                vals = [a + pieces[i + 1] + v[blank_index] for a, v in zip(vals, values)]
                blank_index += 1
                i += 2
            merged_pieces.append(None)
            for j, v in enumerate(vals):
                merged_values[j].append(v)
        else:
            merged_pieces.append(pieces[i])
        i += 1
    # What every value of a blank starts or ends with is part of the sentence ("…88% yield)
    # by flash…" – the ")" and " by " are not the blank's).
    blank = 0
    for k, piece in enumerate(merged_pieces):
        if piece is not None:
            continue
        vals = [v[blank] for v in merged_values]
        prefix = _common_prefix(vals)
        suffix = _common_prefix([v[len(prefix):][::-1] for v in vals])[::-1]
        if prefix or suffix:
            merged_pieces[k - 1] = (merged_pieces[k - 1] or "") + prefix
            if k + 1 < len(merged_pieces):
                merged_pieces[k + 1] = suffix + (merged_pieces[k + 1] or "")
            for v in merged_values:
                v[blank] = v[blank][len(prefix):len(v[blank]) - len(suffix)]
        blank += 1
    literal_len = sum(len(p) for p in merged_pieces if p)
    avg = sum(len(t) for t in texts) / len(texts)
    blanks = sum(1 for p in merged_pieces if p is None)
    if blanks > 8 or literal_len < 0.4 * avg:
        return None
    return merged_pieces, merged_values


def _common_prefix(values: list[str]) -> str:
    """The longest start all of them share, ended at a whole word (so that "88%" and
    "89%" share nothing, and "(SiO2, 10%…" and "(SiO2, 5%…" share "(SiO2, ")."""
    if not values:
        return ""
    first = values[0]
    n = 0
    while all(len(v) > n and v[n] == first[n] for v in values):
        n += 1
    if n == min(len(v) for v in values):
        return first[:n]      # (a whole value: it may end anywhere)
    # never inside a word ("4-methylphenol", "4-bromophenol" share no "4-")
    while n and (first[n - 1].isalnum() or first[n - 1] in "-′'"):
        n -= 1
    return first[:n]


def _blank_name(before: str, after: str, vals: list[str], comps: list[ImportedCompound],
                used: set[str]) -> tuple[str, bool]:
    """A name for a blank of the template, and whether it is one the program fills."""
    stripped = [re.sub(r"\*\*", "", v).strip() for v in vals]
    if all(s == c.cid for s, c in zip(stripped, comps)):
        return "product", True
    yields = [c.values.get("yield", {}).get("percent", "") for c in comps]
    if all(y and s in (f"{y}%", y) for s, y in zip(stripped, yields)) and \
            all(s.endswith("%") for s in stripped) or (
                all(y and s == y for s, y in zip(stripped, yields)) and after.startswith("%")):
        return "yield", True
    if all(re.fullmatch(r"\d+(?:\.\d+)?\s*(?:mmol|µmol|μmol|mol)", s) for s in stripped):
        return "scale", True
    base = "value"
    if all(re.fullmatch(ID, s) for s in stripped) and (
            any(re.search(r"[a-z]", s) for s in stripped) or re.search(r"(?:\(|\*\*)\s*$", before)):
        base = "starting material ID" if "starting material" in used else "ID"
    elif re.search(r"\bfrom\s*\(?\s*$", before):
        base = "starting material"
    elif re.match(r"\s*(?:mg|g)\b", after):
        base = "mass"
    name, n = base, 2
    while name in used:
        name = f"{base} {n}"
        n += 1
    return name, False


def link_general_procedures(plan: ImportPlan) -> None:
    """Find each compound's sentence citing a general procedure, work out the GP's
    compound sentence from them, and link the compounds whose sentence fits it."""
    only_one = len(plan.gps) == 1
    for gp in plan.gps:
        users: list[tuple[ImportedCompound, str]] = []
        for c in plan.compounds:
            if c.gp_key:
                continue
            for s in sentences(c.procedure):
                if cites_gp(s, gp, only_one):
                    users.append((c, s))
                    break
        if not users:
            continue
        texts = [s.strip() for _c, s in users]
        comps = [c for c, _s in users]
        if len(users) == 1:
            sentence, vals = texts[0], {}
            cid = comps[0].cid
            for form in (f"**{cid}**", cid):
                pat = re.escape(form) if form.startswith("*") else rf"(?<![\w.]){re.escape(form)}(?![\w′'])"
                if re.search(pat, sentence):
                    sentence = re.sub(pat, "{product}", sentence, count=1)
                    break
            gp.sentence = _gp_placeholder(sentence, gp)
            _link(comps[0], gp, texts[0], vals)
            continue
        induced = induce_template(texts)
        if induced is None:
            plan.warnings.append(
                f"The compounds made by {gp.name} cite it in sentences too different to "
                "share one compound sentence: they keep their own text and are not linked "
                "to it.")
            continue
        pieces, values = induced
        names: list[str] = []
        used: set[str] = set()
        out = ""
        blank = 0
        pieces = list(pieces)
        for k, piece in enumerate(pieces):
            if piece is None:
                before = pieces[k - 1] or "" if k else ""
                after = pieces[k + 1] or "" if k + 1 < len(pieces) else ""
                vals = [v[blank] for v in values]
                name, _auto = _blank_name(before, after, vals, comps, used)
                if name == "yield" and after.startswith("%"):   # ({yield} gives "88%")
                    pieces[k + 1] = after[1:]
                used.add(name)
                names.append(name)
                out += "{" + name + "}"
                blank += 1
            else:
                out += piece
        # Blanks the program can't name ("{value 2}% → {value 3} in {value 4}") make a
        # sentence nobody can read: with more than three, no link.
        if sum(1 for n in names if n.startswith("value")) > 3:
            plan.warnings.append(
                f"The compounds made by {gp.name} cite it in sentences too different to "
                "share one compound sentence: they keep their own text and are not linked "
                "to it.")
            continue
        # The same amount in every compound's sentence ("…, 0.50 mmol).") is the reaction
        # scale: {scale}, with each compound's Reaction scale field filled.
        common_scale = None
        if "scale" not in names:
            amounts = re.findall(r"(?<![\w.{])(\d+(?:\.\d+)?)\s*(mmol|µmol|μmol)\b", out)
            if len(amounts) == 1:
                common_scale = amounts[0]
                out = re.sub(r"(?<![\w.{])\d+(?:\.\d+)?\s*(?:mmol|µmol|μmol)\b", "{scale}", out,
                             count=1)
        gp.sentence = _gp_placeholder(out, gp)
        for c in comps:
            if common_scale:
                c.values.setdefault("scale", {"value": common_scale[0],
                                              "unit": common_scale[1].replace("μ", "µ")})
        for c, text, vals in zip(comps, texts, values):
            own = {}
            for name, v in zip(names, vals):
                if name in ("product", "yield"):
                    continue
                if name == "scale":
                    sm = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(\S+)", v.strip())
                    if sm:
                        c.values.setdefault("scale", {"value": sm.group(1),
                                                      "unit": sm.group(2).replace("μ", "µ")})
                    continue
                own[name] = v.strip()
            _link(c, gp, text, own)


_PURIFIED = re.compile(r"^The crude product was purified by (?P<method>[^()]+?)"
                       r"(?:\s*\((?P<inside>[^()]*(?:\([^()]*\)[^()]*)*)\))?\.$")
_PHASE = re.compile(r"(?i)^(?:SiO2|SiO₂|silica(?:\s+gel)?|Al2O3|Al₂O₃|(?:neutral|basic|acidic)?\s*"
                    r"alumina|C18|florisil|Celite)$")


def purification_from_text(c: ImportedCompound) -> None:
    """The procedure's last sentence, when it is "The crude product was purified by flash
    column chromatography (SiO2, 10% EtOAc/hexanes)." (the sentence the Purification
    field prints, word for word), becomes the Purification field (SPEC §13q.2): it
    prints the same, at the same place."""
    from . import procedures
    if "purification" in c.values:
        return
    parts = sentences(c.procedure)
    if not parts:
        return
    last = parts[-1].strip()
    plain = clean_space(_plain(parse(last)[0].runs)) if "\n" not in last else ""
    m = _PURIFIED.match(plain)
    if not m:
        return
    value = {"method": m.group("method").strip(), "phase": "", "eluent": "", "sentence": ""}
    inside = (m.group("inside") or "").strip()
    if inside:
        first, _, rest = inside.partition(", ")
        if _PHASE.match(first.strip()):
            value["phase"], value["eluent"] = first.strip(), rest.strip()
        else:
            value["eluent"] = inside
    if procedures.automatic_purification_sentence(_Values(value)) != plain:
        return
    c.values["purification"] = value
    c.procedure = c.procedure[:c.procedure.rfind(parts[-1])].rstrip()


class _Values:
    """Just enough of a compound for procedures.automatic_purification_sentence."""

    def __init__(self, purification: dict):
        self.values = {"purification": purification}
        self.field_keys = {"purification"}


def _gp_placeholder(sentence: str, gp: ImportedGP) -> str:
    """The GP's own name in its compound sentence becomes {GP} (filled in by the program,
    so it follows a renamed GP)."""
    if gp.label:
        # (the label may be bold: "general procedure **A**")
        pattern = (r"(?i)\b(?:general\s+(?:experimental\s+)?procedure|GP)\s*[-–]?\s*"
                   r"(?:\*\*)?\(?" + re.escape(gp.label) + r"\)?(?:\*\*)?(?![\w])")
        return re.sub(pattern, "{GP}", sentence, count=1)
    return sentence


def _link(c: ImportedCompound, gp: ImportedGP, sentence: str, values: dict) -> None:
    c.gp_key = gp.key
    c.gp_values = values
    c.gp_sentence = sentence
    rest = c.procedure
    idx = rest.find(sentence)
    if idx >= 0:
        rest = (rest[:idx] + rest[idx + len(sentence):])
    c.procedure = re.sub(r"[ \t]+\n", "\n", rest).strip()


# ---------------------------------------------------------------------- structures (§13u.7)

@dataclass
class _Drawn:
    """A structure found in one of the SI's drawings."""
    mol: object                 # RDKit Mol (the whole structure, a salt's pieces joined)
    label: str
    drawing: int                # which drawing (index into the SI's objects)
    alone: bool                 # the drawing shows only this structure (and counter-ions)
    picture: bytes | None       # the drawing's picture (EMF), for `alone` ones


def is_id_label(text: str) -> bool:
    return re.fullmatch(STEREO + ID, text.strip()) is not None


def _drawn_structures(objects: list[dict]) -> list[_Drawn]:
    """Every structure in the ChemDraw drawings, with its label (a salt's counter-ion,
    drawn near its cation and not labelled itself, is joined to it)."""
    from rdkit import Chem
    from . import cdx
    out: list[_Drawn] = []
    for index, obj in enumerate(objects):
        if obj.get("kind") != "chemdraw":
            continue
        try:
            structures, _labels = cdx.drawing(obj["cdx"], is_id_label)
        except (cdx.CdxError, KeyError, ValueError):
            continue
        ions = [s for s in structures if s.charge and not s.label]
        used: set[int] = set()
        mains = []
        for s in structures:
            if s in ions or len(s.molecule.atoms) <= 1 and not s.charge:
                continue      # (a counter-ion, or a lone atom: a label read as carbon)
            molecule = s.molecule
            if s.charge:      # join the nearest unlabelled ions of the opposite charge
                need = s.charge
                for ion in sorted(ions, key=lambda i: _box_gap(i.box, s.box)):
                    if not need or id(ion) in used or ion.charge * need > 0 \
                            or _box_gap(ion.box, s.box) > 80:
                        continue
                    molecule = cdx.combine(molecule, ion.molecule)
                    used.add(id(ion))
                    need += ion.charge
            mol = cdx.to_rdkit(molecule)
            if mol is not None:
                mains.append((mol, s.label))
        # Its picture only for a drawing of just this one structure, with no text or
        # arrows (a label or a scheme in the picture would be printed with it).
        plain = len(mains) == 1 and cdx.is_plain(obj["cdx"])
        for mol, label in mains:
            out.append(_Drawn(mol, label, index, len(mains) == 1,
                              obj.get("picture") if plain else None))
    return out


def _box_gap(a: tuple, b: tuple) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _formula_of(mol) -> object:
    """The structure's formula counts as the HRMS field reads it (a salt: its main ion)."""
    try:
        return chem.element_counts(chem.main_piece(mol))
    except Exception:  # noqa: BLE001
        return None


def _hrms_counts(c: ImportedCompound):
    f = str(c.values.get("hrms", {}).get("formula", "") or "")
    if not f:
        return None
    try:
        return chem.parse_m_formula_and_charge(f)[0]
    except chem.ChemError:
        return None


def assign_structures(plan: ImportPlan, objects: list[dict],
                      own_objects: list[list[dict]]) -> None:
    """Give each compound its structure from the SI's ChemDraw drawings (SPEC §13u.7):
    the structure labelled with its ID (several: the one fitting its HRMS formula, else
    one in its own text); else, when its own text holds one drawing of one structure,
    that one. A structure that doesn't fit the HRMS formula gets a warning."""
    from rdkit import Chem
    drawn = _drawn_structures(objects)
    if not drawn:
        return
    by_label: dict[str, list[_Drawn]] = {}
    for d in drawn:
        if d.label:
            by_label.setdefault(d.label.strip(), []).append(d)
    index_of = {id(o): i for i, o in enumerate(objects)}
    for c, own in zip(plan.compounds, own_objects):
        own_ids = {index_of[id(o)] for o in own if id(o) in index_of}
        want = _hrms_counts(c)
        cands = by_label.get(c.cid, [])
        chosen, note = None, ""
        if cands:
            def rank(d: _Drawn):
                fits = want is not None and chem.same_formula(_formula_of(d.mol) or {}, want)
                return (not fits, d.drawing not in own_ids, d.drawing)
            chosen = sorted(cands, key=rank)[0]
            note = f"from the ChemDraw drawing labelled “{c.cid}”"
            others = {Chem.MolToSmiles(d.mol) for d in cands} - {Chem.MolToSmiles(chosen.mol)}
            if others:
                c.warnings.append(
                    f"“{c.cid}” labels {len(others) + 1} different structures in the SI's "
                    "drawings; the one that fits its HRMS formula (or is next to its text) "
                    "was taken. Check it.")
        else:
            mine = [d for d in drawn if d.drawing in own_ids and (not d.label or d.label == c.cid)]
            drawings = {d.drawing for d in mine}
            if len(mine) == 1 and len(drawings) == 1 and mine[0].alone:
                chosen = mine[0]
                note = "from the ChemDraw drawing next to its text"
        if chosen is None:
            continue
        got = _formula_of(chosen.mol)
        if want is not None and got is not None and not chem.same_formula(got, want):
            # The labelled structure doesn't fit the HRMS: if just one structure in the
            # whole SI does (by its formula), it is that one, labelled otherwise.
            fitting = {Chem.MolToSmiles(d.mol): d for d in drawn
                       if chem.same_formula(_formula_of(d.mol) or {}, want)}
            if len(fitting) == 1:
                other = next(iter(fitting.values()))
                c.warnings.append(
                    f"The drawing labelled “{c.cid}” shows a structure ({chem.hill_formula(got)}) "
                    f"that does not fit its HRMS; the only structure in the SI that does "
                    f"(drawn {('as “' + other.label + '”') if other.label else 'unlabelled'}) "
                    "was taken instead. Check it.")
                chosen, got = other, _formula_of(other.mol)
                note = "the one structure in the SI's drawings that fits its HRMS formula"
        c.structure = chem.mol_to_block(chosen.mol)
        c.structure_note = note
        c.structure_picture = chosen.picture if chosen.alone else None
        if want is not None and got is not None and not chem.same_formula(got, want):
            c.warnings.append(
                f"The structure found ({chem.hill_formula(got)}) does not fit the formula of "
                f"its HRMS ({c.values['hrms'].get('formula')}): check the structure or the "
                "HRMS.")
