"""The manuscript check (SPEC §13o): does the manuscript agree with the SI?

The user adds the manuscript (PDF and/or Word .docx) and its ChemDraw files (.cdxml) to
the project's `manuscript/` folder (never printed). They are read (core/ms_read.py) into
compound records — ID, yield, ee/er, dr, footnote markers with the footnotes' text, a
structure from ChemDraw, names written as "name (ID)" — which are compared, by compound
ID, with the SI:

* manuscript IDs with data that no SI compound has (only this direction: a compound only
  in the SI is not reported; Nathan, SPEC §13o.6);
* the structure (InChIKey of the ChemDraw structure vs the SI's; a different
  stereochemistry or charge is reported as such; a salt is compared by its largest piece);
* yield (the printed numbers must be identical), ee/er (er is converted to ee to compare;
  both are shown as printed) and dr; values in running-text sentences only give notes;
* names (ignoring spacing, capitals, italics, kinds of hyphens and quotes);
* conditions: the times, temperatures, equivalents, mol%, scale, concentrations and named
  reagents/solvents of a compound's footnote must appear in its SI procedure (its own
  text, its general procedure's sentence with the placeholders filled in, and the general
  procedure's text); and a compound whose SI procedure uses another time, temperature,
  loading, equivalents or solvent than its general procedure, but has no footnote in the
  manuscript, is flagged.

Every result is a `Finding` with a plain-language message, a stable key and a fingerprint
of the values it is about: it can be dismissed (manuscript/dismissed.json, readable) and
comes back if the values change. All checks are local: nothing is sent anywhere.

What was read is kept in manuscript/check.json (readable), file by file: a file is read
again when it is new or changed, or when "Check again" is clicked (a file that could not
be opened just now — offline, or locked by another program — is not kept, and is read
again next time); the comparison itself is quick and is made afresh whenever the results
are shown, so they follow the SI as it is edited.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import chem
from . import fileio
from . import ms_parse as P
from . import ms_read
from . import procedures
from .ids import safe_filename
from .markup import parse as parse_markup

MANUSCRIPT_DIR = "manuscript"
CACHE_FILE = "check.json"
DISMISSED_FILE = "dismissed.json"
DAMAGED_FILE = "dismissed.damaged.json"   # (kept by earlier versions: see _damaged_copy)
OWN_FILES = (CACHE_FILE, DISMISSED_FILE, DAMAGED_FILE)
MANUSCRIPT_EXTENSIONS = (".pdf", ".docx", ".doc")
CHEMDRAW_EXTENSIONS = (".cdxml", ".cdx")
CACHE_VERSION = 1

PROBLEM, NOTE, OK, DISMISSED = "problem", "note", "ok", "dismissed"

# What each kind of finding is called in the summary line ("2 yield differences").
KIND_SUMMARY = {
    "missing_si": ("missing in the SI", "missing in the SI"),
    "structure": ("structure difference", "structure differences"),
    "yield": ("yield difference", "yield differences"),
    "ee": ("ee/er difference", "ee/er differences"),
    "dr": ("dr difference", "dr differences"),
    "name": ("name difference", "name differences"),
    "conditions": ("footnote not matched in the SI", "footnotes not matched in the SI"),
    "deviation": ("SI deviation without a footnote", "SI deviations without a footnote"),
    "internal": ("disagreement within the manuscript", "disagreements within the manuscript"),
}
WHAT = {"missing_si": "ID", "structure": "Structure", "yield": "Yield",
        "ee": "ee / er", "dr": "dr", "name": "Name", "conditions": "Conditions",
        "deviation": "Conditions", "internal": "Manuscript", "mention": "ID"}
FIELD_OF = {"structure": "structure", "yield": "yield", "ee": "ee", "dr": "dr",
            "name": "name", "conditions": "procedure", "deviation": "procedure"}


@dataclass
class Finding:
    kind: str            # see KIND_SUMMARY ("mention" and "ok" rows too)
    label: str           # the compound ID as shown
    message: str
    status: str = PROBLEM   # PROBLEM, NOTE, OK (DISMISSED is set when listing)
    compound_id: str = ""   # the SI compound's internal ID ("" if none)
    ms_value: str = ""
    si_value: str = ""
    where: str = ""
    key: str = ""
    fingerprint: str = ""
    reason: str = ""        # why it was dismissed (when it was)

    @property
    def what(self) -> str:
        return WHAT.get(self.kind, self.kind.capitalize())

    @property
    def field(self) -> str:
        """The compound field to open for it ("" = the page's top)."""
        return FIELD_OF.get(self.kind, "")


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0               # SI compounds found in the manuscript
    notes: list[str] = field(default_factory=list)
    files: int = 0

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.status == PROBLEM]

    def summary(self) -> str:
        if not self.files:
            return "No manuscript added yet."
        counts: dict[str, int] = {}
        for f in self.problems:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        what = f"{self.checked} compound{'s' if self.checked != 1 else ''} checked"
        gone = sum(f.status == DISMISSED for f in self.findings)
        tail = f" ({gone} dismissed)" if gone else ""
        if not counts:
            return what + ": no differences found." + tail
        parts = []
        for kind in KIND_SUMMARY:
            n = counts.get(kind, 0)
            if n:
                one, many = KIND_SUMMARY[kind]
                parts.append(f"{n} {one if n == 1 else many}")
        return what + ": " + ", ".join(parts) + "." + tail


def _fp(*parts) -> str:
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------- the folder

def folder(project) -> Path:
    return project.folder / MANUSCRIPT_DIR


def files(project) -> list[Path]:
    """The manuscript files (not the program's own check.json and dismissed.json)."""
    base = folder(project)
    if not base.is_dir():
        return []
    try:
        return sorted((p for p in base.iterdir() if p.is_file() and not _hidden(p.name)),
                      key=lambda p: p.name.lower())
    except OSError:
        return []


# Files the system puts in folders by itself (Finder, Explorer), never the user's.
_SYSTEM_FILES = {"desktop.ini", "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "icon\r"}


def _hidden(name: str) -> bool:
    """The program's own files (and OneDrive's conflict copies of them, such as
    dismissed-OFFICE-PC.json), temporary files and hidden or system files (.DS_Store, ._x,
    desktop.ini, Thumbs.db, Word's ~$ lock files)."""
    return name in OWN_FILES or name.startswith((".", "~$")) or \
        name.lower() in _SYSTEM_FILES or _conflict_copy(name) or _damaged_copy(name)


def _damaged_copy(name: str) -> bool:
    """A damaged dismissed.json kept by dismiss(): dismissed.damaged-<date>.json (or the
    single dismissed.damaged.json kept by earlier versions)."""
    return re.fullmatch(r"dismissed\.damaged(-.+)?\.json", name, re.IGNORECASE) is not None


def _conflict_copy(name: str) -> bool:
    # (only OneDrive's "-<computer>" form: add_file's own "dismissed (2).json", for a
    # user's file of that name, stays in the list)
    return re.fullmatch(r"(check|dismissed)-.+\.json", name, re.IGNORECASE) is not None


def add_file(project, source: Path) -> Path:
    """Copy a file into manuscript/ (a second file of the same name gets " (2)")."""
    project._check_writable()
    base = folder(project)
    fileio.make_dirs(base)
    name = safe_filename(Path(source).name)
    if _damaged_copy(name):   # (named like a kept damaged dismissed.json: it would be hidden)
        name = re.sub(r"^dismissed\.", "dismissed_", name, flags=re.IGNORECASE)
    dest = base / name
    n = 2
    while dest.exists() or dest.name in OWN_FILES:
        dest = base / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    fileio.copy_file(Path(source), dest)
    return dest


def remove_file(project, name: str):
    """Move a manuscript file to the project Trash (restorable)."""
    project._check_writable()
    path = folder(project) / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return project._move_to_trash(path, kind="manuscript_file", name=name, extra={})


def restore_file(project, entry, item: Path) -> str:
    """Put a manuscript file back from the Trash (the Trash calls this)."""
    base = folder(project)
    fileio.make_dirs(base)
    dest = base / item.name
    n = 2
    while dest.exists():
        dest = base / f"{item.stem} ({n}){item.suffix}"
        n += 1
    fileio.copy_file(item, dest)
    return f"Restored {dest.name} to the manuscript check."


# ---------------------------------------------------------------------- reading (cached)

_memory: dict[str, tuple[dict, list[ms_read.Source]]] = {}
_lock = threading.Lock()


def stamps(project) -> dict[str, list[int]]:
    out = {}
    for p in files(project):
        try:
            st = p.stat()
        except OSError:
            continue
        out[p.name] = [st.st_size, st.st_mtime_ns]
    return out


def cached_sources(project) -> list[ms_read.Source] | None:
    """What was read last time, if the files haven't changed since (else None)."""
    now = stamps(project)
    key = str(project.folder)
    with _lock:
        hit = _memory.get(key)
    if hit is not None and hit[0] == now:
        return hit[1]
    stored, sources = _stored(project)
    # (Read again when the files changed, or when this program reads files differently.)
    if stored != now:
        return None
    with _lock:
        _memory[key] = (now, sources)
    return sources


def _stored(project) -> tuple[dict | None, list[ms_read.Source]]:
    """(the files' stamps, what they gave) from check.json; (None, []) if there is none,
    it was made by another version of the reader, or it doesn't hold what is expected
    (edited by hand): then the files are simply read again."""
    try:
        data = json.loads((folder(project) / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or \
            data.get("reader") != ms_read.READER_VERSION or \
            not isinstance(data.get("files"), dict):
        return None, []
    try:
        sources = [ms_read.Source.from_json(s) for s in data.get("sources") or []]
    except (TypeError, ValueError, AttributeError):
        return None, []
    if not all(_well_formed(s) for s in sources):
        return None, []
    return data["files"], sources


def _well_formed(src: ms_read.Source) -> bool:
    """Does a Source read back from check.json hold what the comparison expects?"""
    def strings(items):
        return isinstance(items, list) and all(isinstance(x, str) for x in items)
    return (all(isinstance(x, str) for x in (src.file, src.kind, src.error))
            and isinstance(src.records, list) and isinstance(src.names, list)
            and all(isinstance(r, dict) for r in src.records)
            and all(isinstance(n, dict) and isinstance(n.get("name"), str)
                    for n in src.names)
            and strings(src.mentioned) and strings(src.tokens) and strings(src.notes))


def _previous(project) -> tuple[dict, dict[str, ms_read.Source]]:
    """What was read last time, file by file: (stamps, {file name: Source}). A file that
    could not be opened just now is left out (it is read again)."""
    with _lock:
        hit = _memory.get(str(project.folder))
    if hit is not None:
        then, sources = hit
    else:
        then, sources = _stored(project)
    return then or {}, {s.file: s for s in sources if not s.transient}


def read_all(project, progress=None, force: bool = False) -> list[ms_read.Source]:
    """Read the manuscript files: a file that hasn't changed since it was last read (the
    same name, size and modification time, the same reader) is not read again, unless
    `force` ("Check again"). Raises ms_read.Stopped if the progress callback says stop."""
    if not force:
        hit = cached_sources(project)
        if hit is not None and not any(s.transient for s in hit):
            return hit
    then, before = ({}, {}) if force else _previous(project)
    now, sources = {}, []
    for p in files(project):          # (one listing for both the stamps and the reading)
        try:
            st = p.stat()
            stamp = [st.st_size, st.st_mtime_ns]
            now[p.name] = stamp
        except OSError:
            stamp = None
        old = before.get(p.name)
        if stamp is not None and old is not None and then.get(p.name) == stamp:
            sources.append(old)       # unchanged since it was read
        else:
            sources.append(ms_read.read_file(p, progress))
    with _lock:
        _memory[str(project.folder)] = (now, sources)
    # A file that could not be opened just now is not written down: read again next time.
    later = {s.file for s in sources if s.transient}
    kept = {name: stamp for name, stamp in now.items() if name not in later}
    if not project.read_only and kept:
        _write_cache(folder(project) / CACHE_FILE,
                     {"version": CACHE_VERSION, "reader": ms_read.READER_VERSION,
                      "files": kept,
                      "checked_at": datetime.now().isoformat(timespec="seconds"),
                      "note": "What the Manuscript check read from the files beside this "
                              "one. Made by the program; safe to delete.",
                      "sources": [s.to_json() for s in sources if not s.transient]})
    return sources


def _write_cache(path: Path, data: dict) -> None:
    """check.json is only a cache: written directly, not as an undoable change (the
    files it describes are; it is read again whenever they differ)."""
    tmp = path.with_name(f".tmp-{path.name}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def forget(project) -> None:
    with _lock:
        _memory.pop(str(project.folder), None)


# ---------------------------------------------------------------------- dismissals

def _load_dismissed(project, strict: bool = False) -> dict | None:
    """dismissed.json: {} if there is none, None if it is there but doesn't hold JSON
    (cut off, or edited by hand). A file that can't be opened just now (offline, locked)
    gives {}, or raises the OSError if `strict` (so that it is not written over)."""
    try:
        raw = (folder(project) / DISMISSED_FILE).read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        if strict:
            raise
        return {}
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def dismissed(project) -> dict[str, dict]:
    return _load_dismissed(project) or {}


def dismiss(project, finding: Finding, reason: str = "") -> None:
    project._check_writable()
    data = _load_dismissed(project, strict=True)
    if data is None:
        # Don't lose what it held: keep a dated copy, never written over (as for a damaged
        # references.json or front.json; Undo also brings the file back). If it can't be
        # copied, the OSError stops the dismissal before the file is written over (the
        # caller says the dismissal could not be saved).
        from .project import _unique_path
        source = folder(project) / DISMISSED_FILE
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        fileio.copy_file(source, _unique_path(
            source.with_name(f"dismissed.damaged-{stamp}.json")))
        data = {}
    data[finding.key] = {"fingerprint": finding.fingerprint, "message": finding.message,
                         "reason": reason.strip(),
                         "by": project.lock.user if project.lock else "",
                         "at": datetime.now().isoformat(timespec="seconds")}
    fileio.make_dirs(folder(project))
    fileio.atomic_write_json(folder(project) / DISMISSED_FILE, data)


def restore(project, key: str) -> None:
    """Raises the OSError if dismissed.json can't be opened just now (offline, locked):
    the Restore did not happen, and the user is told so."""
    project._check_writable()
    data = _load_dismissed(project, strict=True) or {}
    if data.pop(key, None) is not None:
        fileio.atomic_write_json(folder(project) / DISMISSED_FILE, data)


# ---------------------------------------------------------------------- the SI side

def _plain(text: str) -> str:
    return "\n".join(p.plain_text for p in parse_markup(text or ""))


@dataclass
class SiCompound:
    compound: object
    label: str
    section: str


def si_compounds(project) -> tuple[dict[str, SiCompound], dict[str, str]]:
    """({ID key: the SI compound}, {ID key: label} of labels on scheme-editor boxes not
    linked to a compound — starting materials, known IDs without data). Keys are full IDs
    ("cis-5a" and "trans-5a" are two compounds); see Resolver for the matching."""
    report = project.id_report()
    out: dict[str, SiCompound] = {}
    for section in project.data_sections():
        for c in section.compounds:
            label = report.ids.get(c.id)
            if label and P.id_key(label) not in out:
                out[P.id_key(label)] = SiCompound(c, label, section.name)
    extra: dict[str, str] = {}
    try:
        from . import scheme_editor as SE
        for scheme in project.all_schemes():
            for box, _where in SE.all_boxes(scheme):
                if box.get("compound"):
                    continue
                label = _plain(str(box.get("label") or "")).strip()
                if label and P.id_key(label) not in out:
                    extra[P.id_key(label)] = label
    except Exception:  # noqa: BLE001 — scheme pages are an extra; never stop the check
        pass
    return out, extra


class Resolver:
    """Which SI ID a manuscript ID stands for: the same full ID if the SI has it
    ("cis-5a" → cis-5a, even when trans-5a exists too); else the same ID without a stereo
    prefix that only says racemic or "the enantiomer" ("(±)-5", "rac-5", "ent-5" → 5);
    else the ID without any stereo prefix, if exactly one SI ID is so ("(S,S)-3b" → 3b,
    "5a" → cis-5a when that is the only 5a). Otherwise the manuscript ID itself (not in
    the SI)."""

    def __init__(self, keys):
        self.keys = set(keys)
        self.by_base: dict[str, set[str]] = {}
        for k in self.keys:
            self.by_base.setdefault(P.id_base(k), set()).add(k)

    def __call__(self, label: str) -> str:
        key = P.id_key(label)
        if key in self.keys:
            return key
        if P.id_without_rac(label) in self.keys:
            return P.id_without_rac(label)
        found = self.by_base.get(P.id_base(label)) or set()
        return next(iter(found)) if len(found) == 1 else key


def si_procedure_text(project, compound, gp_texts: dict | None = None) -> tuple[str, str, str]:
    """(the compound's own procedure text and typed placeholder values, its general
    procedure's sentence filled in, the general procedure's text) as plain text.
    `gp_texts` ({GP ID: text}, for one check only) saves reading a GP once per compound."""
    own = []
    for key in ("procedure", "si_text"):
        if key in compound.field_keys:
            try:
                own.append(_plain(project.read_compound_text(compound, key)))
            except (OSError, ValueError):
                pass
    gp = project.compound_gp(compound)
    sentence = gp_text = ""
    if gp is not None:
        choice = project.gp_choice(compound)
        typed = choice.get("values") or {}
        own += [_plain(str(v)) for v in typed.values() if str(v).strip()]
        try:
            runs = procedures.fill(gp.get("sentence", "") or "", typed,
                                   procedures.auto_values(project, compound, gp))
            sentence = "".join(r.text for r in runs)
        except Exception:  # noqa: BLE001
            sentence = gp.get("sentence", "")
        gp_text = None if gp_texts is None else gp_texts.get(gp.get("id"))
        if gp_text is None:
            try:
                gp_text = _plain(project.read_gp_text(gp["id"]))
            except (OSError, ValueError, KeyError):
                gp_text = ""
            if gp_texts is not None and gp.get("id") is not None:
                gp_texts[gp["id"]] = gp_text
    scale = compound.values.get("scale") or {}
    if "scale" in compound.field_keys and str(scale.get("value", "")).strip():
        own.append(f"{str(scale['value']).strip()} {scale.get('unit') or 'mmol'}")
    return "\n".join(t for t in own if t), sentence, gp_text


def _si_yield(c) -> str:
    if "yield" not in c.field_keys:
        return ""
    return str((c.values.get("yield") or {}).get("percent", "")).strip()


def _si_part(c, key: str) -> str:
    if key not in c.field_keys:
        return ""
    return str((c.values.get(key) or {}).get("value", "")).strip()


# ---------------------------------------------------------------------- values

_NUM = re.compile(r"([<>≥≤]?)\s*(\d+(?:[.,]\d+)?)")


def _number(text: str) -> tuple[str, float] | None:
    m = _NUM.search(P.normalize(str(text or "")))
    if not m:
        return None
    return m.group(1), float(m.group(2).replace(",", "."))


def _ratio(text: str) -> tuple[str, float, float] | None:
    m = re.search(r"([<>≥≤]?)\s*(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)", P.normalize(str(text or "")))
    if not m:
        return None
    return m.group(1), float(m.group(2).replace(",", ".")), float(m.group(3).replace(",", "."))


def same_yield(a: str, b: str) -> bool:
    """Two yields agree when their numbers do; "quant." agrees with "quant." and with
    99, >99 or 100%."""
    if P.is_quant(a) or P.is_quant(b):
        other = b if P.is_quant(a) else a
        n = _number(other)
        return P.is_quant(other) or (n is not None and n[0] in ("", ">", "≥") and n[1] >= 99)
    return _number(a) == _number(b)


def _pct(value: str) -> str:
    """A yield as it is shown: "92%", but "quant." without a % sign."""
    value = str(value)
    return f"{value}%" if re.search(r"\d", value) else value


def ee_value(text: str) -> tuple[str, float, bool] | None:
    """(sign, ee, converted) from "94% ee", "94", ">99% ee" or "97:3 er" (converted);
    "rac" or "racemic" is 0% ee."""
    if P.is_racemic(text):
        return "", 0.0, False
    r = _ratio(text)
    if r is not None:
        sign, a, b = r
        if a + b <= 0:
            return None
        return sign, round(abs(a - b) / (a + b) * 100, 1), True
    n = _number(text)
    return (n[0], n[1], False) if n else None


def same_ee(a: tuple[str, float, bool], b: tuple[str, float, bool]) -> bool:
    if a[2] or b[2]:            # an er converted: equal to the precision printed
        return abs(a[1] - b[1]) <= 0.5
    return a[0] == b[0] and a[1] == b[1]


def same_dr(a: str, b: str) -> bool:
    """Two dr values agree when written the same; written two ways ("10:1" and "91:9")
    they agree within 2 %, or when they agree at the precision printed ("5:1" stands for
    4.5–5.5:1, "83:17" for 82.5–83.5 : 17.5–16.5, so 83:17 ≈ 4.9:1 agrees with 5:1)."""
    ra, rb = _ratio(a), _ratio(b)
    if ra is None or rb is None:
        return P.normalize(a).replace(" ", "") == P.normalize(b).replace(" ", "")
    if ra[0] != rb[0] or ra[2] == 0 or rb[2] == 0:
        return ra == rb
    if (ra[1], ra[2]) == (rb[1], rb[2]):
        return True
    if (ra[2] == 1) == (rb[2] == 1):
        return False     # the same format: must be the same numbers
    x, y = ra[1] / ra[2], rb[1] / rb[2]
    if abs(x - y) <= 0.02 * max(x, y):
        return True
    (lo_a, hi_a), (lo_b, hi_b) = _dr_range(a), _dr_range(b)
    return lo_a <= hi_b and lo_b <= hi_a


def _dr_range(text: str) -> tuple[float, float]:
    """The range of major:minor ratios a printed dr stands for, at the precision
    printed: "5:1" → 4.5–5.5; "83:17" → 82.5/17.5 – 83.5/16.5."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)", P.normalize(text))
    a_s, b_s = m.group(1).replace(",", "."), m.group(2).replace(",", ".")

    def half(s):     # half a unit in the last printed digit
        return 0.5 * 10 ** -(len(s.split(".")[1]) if "." in s else 0)
    a, b = float(a_s), float(b_s)
    ha, hb = half(a_s), half(b_s)
    if b == 1:
        return a - ha, a + ha
    return (a - ha) / (b + hb), (a + ha) / max(b - hb, 1e-9)


def name_key(name: str) -> str:
    """Names are compared ignoring spacing, capitals, kinds of hyphens and quotes and a
    final full stop (italics are not in the plain text anyway)."""
    text = P.normalize(name)
    text = text.replace("`", "'").replace("″", "''").replace('"', "''")
    text = re.sub(r"\s+", "", text).casefold().rstrip(".")
    return text


def same_name(a: str, b: str) -> bool:
    """Names agree as name_key() says, or once hyphens are left out too (a hyphen the
    typesetter put at a line break, "4-bromo-benzoate", is not a different name)."""
    ka, kb = name_key(a), name_key(b)
    return ka == kb or ka.replace("-", "") == kb.replace("-", "")


def _ms_ee_text(rec: dict) -> str:
    if P.is_racemic(rec.get("ee")):
        return P.RACEMIC
    if rec.get("ee"):
        return f"{rec['ee']}% ee"
    if rec.get("er"):
        return f"{rec['er']} er"
    return ""


def _mol_keys(mol) -> tuple[str, str, bool, str]:
    """(InChIKey, InChIKey of the largest piece, is it more than one piece, formula of the
    largest piece)."""
    from rdkit import Chem, RDLogger
    from rdkit.Chem.rdMolDescriptors import CalcMolFormula
    RDLogger.DisableLog("rdApp.*")
    try:
        whole = Chem.MolToInchiKey(mol) or ""
        pieces = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
        # (the compound without its counter-ions, by the rule the HRMS "M" follows)
        largest = chem.main_piece(mol)
        main = Chem.MolToInchiKey(largest) or ""
        formula = CalcMolFormula(largest)
    finally:
        RDLogger.EnableLog("rdApp.*")
    return whole, main, len(pieces) > 1, formula


@functools.lru_cache(maxsize=4096)
def _smiles_keys(smiles: str) -> tuple[str, str, bool, str] | None:
    """_mol_keys of a manuscript structure (None if it can't be read): the same SMILES
    always gives the same keys, so they are worked out once."""
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(smiles)
    finally:
        RDLogger.EnableLog("rdApp.*")
    if mol is None:
        return None
    try:
        return _mol_keys(mol)
    except Exception:  # noqa: BLE001
        return None


# The SI side's keys, kept with the molecule object itself: Project.read_structure gives
# a new object whenever structure.mol changes (an edit, Undo/Redo, a sync, a reload), so
# these can never be older than the molecule they describe.
_si_mol_keys: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _si_keys(mol) -> tuple[str, str, bool, str] | None:
    with _lock:
        try:
            return _si_mol_keys[mol]
        except (KeyError, TypeError):
            pass
    try:
        keys = _mol_keys(mol)
    except Exception:  # noqa: BLE001
        keys = None
    with _lock:
        try:
            _si_mol_keys[mol] = keys
        except TypeError:
            pass
    return keys


def compare_structures(ms_smiles: str, si_mol) -> tuple[str, str, str, str]:
    """(status, fingerprint, manuscript formula, SI formula): status "same", "salt"
    (same compound once the counter-ions are left out), "stereo", "charge", "different"
    or "" (not comparable). The fingerprint is made from both InChIKeys, so a dismissed
    difference comes back when either structure changes (even to an isomer)."""
    if si_mol is None:
        return "", "", "", ""
    a, b = _smiles_keys(ms_smiles), _si_keys(si_mol)
    if a is None or b is None:
        return "", "", "", ""
    a_whole, a_main, a_salt, a_formula = a
    b_whole, b_main, b_salt, b_formula = b
    if not a_whole or not b_whole:
        return "", "", "", ""
    fp = _fp(a_whole, b_whole)
    if a_whole == b_whole:
        return "same", fp, a_formula, b_formula
    if a_main == b_main and (a_salt or b_salt):
        return "salt", fp, a_formula, b_formula
    if a_main[:14] == b_main[:14]:
        if a_main[15:23] == b_main[15:23]:
            return "charge", fp, a_formula, b_formula
        return "stereo", fp, a_formula, b_formula
    return "different", fp, a_formula, b_formula


# ---------------------------------------------------------------------- merging

FILE_TYPE = {"pdf": "PDF", "docx": "Word"}


def place(where: str, kind: str) -> str:
    """A place in the manuscript with its file type: "Table 1, p. 1 (PDF)", "Table 1
    (Word)", "Word file, paragraph 4"; a ChemDraw place is its file name."""
    if not where:
        return ""
    if kind == "docx" and where.startswith("paragraph"):
        return f"Word file, {where}"
    if kind in FILE_TYPE:
        return f"{where} ({FILE_TYPE[kind]})"
    return where


@dataclass
class MsCompound:
    """Everything the manuscript says about one compound ID."""
    label: str
    records: list[dict] = field(default_factory=list)
    names: list[dict] = field(default_factory=list)
    mentioned: bool = False

    @staticmethod
    def own(r: dict) -> bool:
        """A record that gives this compound's own values: not a repeated row of an
        optimisation table, not a sentence of running text (those only give notes)."""
        return not r.get("repeated") and r.get("source") != "text"

    @property
    def where(self) -> str:
        """Every place it is in, each with its file type."""
        places = [r.get("place", "") for r in self.records] + \
            [n.get("place", "") for n in self.names]
        return "; ".join(p for p in dict.fromkeys(places) if p)

    def values(self, key: str) -> list[tuple[str, str]]:
        """(value, place) of one kind, from records that give this compound's own values."""
        out = []
        for r in self.records:
            v = r.get(key)
            if self.own(r) and v not in (None, "", []):
                out.append((str(v), r.get("place", "")))
        return out

    @property
    def has_data(self) -> bool:
        """Does the manuscript give values for it (a yield, ee/er or dr in a scheme or
        table)? A structure alone does not count: a labelled starting material or
        reagent in a ChemDraw file has one."""
        return any(self.own(r) and any(r.get(k) for k in ("yield", "ee", "er", "dr"))
                   for r in self.records)

    @property
    def text_records(self) -> list[dict]:
        return [r for r in self.records if r.get("source") == "text" and
                any(r.get(k) for k in ("yield", "ee", "er", "dr"))]

    @property
    def markers(self) -> list[tuple[str, str, str]]:
        """(marker, footnote text, place) of the footnotes on this compound (not those of
        the repeated rows of an optimisation table: they are about other conditions)."""
        out, seen = [], set()
        for r in self.records:
            if r.get("repeated"):
                continue
            for m in r.get("footnotes") or []:
                text = (r.get("conditions") or {}).get(m, "")
                if (m, text) not in seen:
                    seen.add((m, text))
                    out.append((m, text, r.get("place", "")))
        return out


def merge(sources: list[ms_read.Source], resolve=None) -> dict[str, MsCompound]:
    """The manuscript's compounds by ID key (as `resolve` gives it: the SI ID it stands
    for), every source's records together. Records are copied with their place."""
    resolve = resolve or P.id_key
    out: dict[str, MsCompound] = {}
    for src in sources:
        for rec in src.records:
            label = str(rec.get("id") or "").strip()
            if not label:
                continue
            rec = {**rec, "place": place(str(rec.get("where") or ""), src.kind)}
            out.setdefault(resolve(label), MsCompound(label)).records.append(rec)
        for n in src.names:
            label = str(n.get("id") or "")
            if label:
                n = {**n, "place": place(str(n.get("where") or ""), src.kind)}
                out.setdefault(resolve(label), MsCompound(label)).names.append(n)
        for label in src.mentioned:
            out.setdefault(resolve(label), MsCompound(label)).mentioned = True
    return out


# ---------------------------------------------------------------------- the comparison

def check(project, sources: list[ms_read.Source] | None) -> Result:
    """Compare what the manuscript says with the SI (quick: nothing is read from the
    manuscript files here). Only the manuscript → SI direction: an SI compound that the
    manuscript doesn't show is not reported (SPEC §13o.6)."""
    result = Result(files=len(files(project)))
    if not sources:
        return result
    for src in sources:
        if src.error:
            result.notes.append(src.error)
        result.notes.extend(src.notes)
    si, extra = si_compounds(project)
    ms = merge(sources, Resolver(list(si) + list(extra)))
    out: list[Finding] = []
    gp_texts: dict = {}      # each general procedure's text, read once for this check

    for key, mc in ms.items():
        sc = si.get(key)
        if sc is None:
            if key in extra:
                continue    # a starting material labelled on a scheme: known, no data
            if mc.has_data:
                out.append(Finding("missing_si", mc.label,
                                   f"{mc.label}: the manuscript gives data for it "
                                   f"({_data_text(mc)}) but no compound in the SI has this ID.",
                                   PROBLEM, ms_value=_data_text(mc),
                                   si_value="no compound with this ID", where=mc.where))
            else:
                what = ("a labelled structure" if any(r.get("smiles") for r in mc.records)
                        else "a value in the text" if mc.text_records else "mentioned")
                out.append(Finding("mention", mc.label,
                                   f"{mc.label} is in the manuscript ({what}) but no compound "
                                   "in the SI has this ID. That is fine for a known compound, "
                                   "a starting material or a reagent.", NOTE, ms_value=what,
                                   si_value="no compound with this ID", where=mc.where))
            continue
        result.checked += 1
        out += _compare_one(project, mc, sc, gp_texts)

    for f in out:
        if not f.key:
            f.key = f"{f.kind}:{P.id_key(f.label)}"
        if not f.fingerprint:
            f.fingerprint = _fp(f.ms_value, f.si_value, f.message if f.kind in
                                ("conditions", "deviation") else "")
    # Two findings can share a key (one footnote letter read with two texts, two drawings
    # with one label in one file): those get their fingerprint in the key, so that each
    # can be dismissed on its own.
    prints: dict[str, set[str]] = {}
    for f in out:
        prints.setdefault(f.key, set()).add(f.fingerprint)
    gone = dismissed(project)
    for f in out:
        base, own = f.key, f"{f.key}:{f.fingerprint}"
        if len(prints[base]) > 1:
            f.key = own
        if f.status != PROBLEM:
            continue
        for k in dict.fromkeys((f.key, base, own)):   # (also one stored the other way)
            d = gone.get(k)
            if isinstance(d, dict) and d.get("fingerprint") == f.fingerprint:
                f.key = k        # so that Restore removes this very entry
                f.status = DISMISSED
                f.reason = str(d.get("reason") or "")
                break
    order = {PROBLEM: 0, NOTE: 1, DISMISSED: 2, OK: 3}
    out.sort(key=lambda f: (order.get(f.status, 9), _id_sort(f.label), f.kind))
    result.findings = out
    return result


def _id_sort(label: str):
    m = re.match(r"\D*(\d+)(.*)", label)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 6, label)


def _data_text(mc: MsCompound) -> str:
    parts = []
    y = mc.values("yield")
    if y:
        parts.append(f"{_pct(y[0][0])} yield")
    ee = [r for r in mc.records if _ms_ee_text(r) and mc.own(r)]
    if ee:
        parts.append(_ms_ee_text(ee[0]))
    d = mc.values("dr")
    if d:
        parts.append(f"{d[0][0]} dr")
    if any(r.get("smiles") for r in mc.records):
        parts.append("a structure")
    return ", ".join(parts)


def _compare_one(project, mc: MsCompound, sc: SiCompound,
                 gp_texts: dict | None = None) -> list[Finding]:
    c = sc.compound
    label = sc.label
    out: list[Finding] = []

    def add(kind, message, status=PROBLEM, ms_value="", si_value="", where="", key="",
            fingerprint=""):
        out.append(Finding(kind, label, message, status, c.id, ms_value, si_value,
                           where or mc.where, key, fingerprint))

    # --- structure
    structures = [(r["smiles"], r.get("place", "")) for r in mc.records if r.get("smiles")]
    if structures:
        # (strict: a structure.mol that can't be read just now, e.g. offline, is not "no
        # structure"; it is said so, and compared at the next check)
        unreadable = False
        try:
            si_mol = project.read_structure(c, strict=True)
        except OSError:
            si_mol, unreadable = None, True
        except Exception:  # noqa: BLE001
            si_mol = None
        if unreadable:
            add("structure", f"{label}: the SI structure file could not be read just now (it "
                             "may be an online-only OneDrive file while you are offline, or "
                             "open in another program), so the structure was not compared. "
                             "Check again later.", NOTE, "structure", "could not be read",
                structures[0][1])
        elif si_mol is None and project.generic_structure_atoms(c):
            add("structure", f"{label}: the SI structure has a generic atom "
                             f"({', '.join(project.generic_structure_atoms(c))}), so it was not "
                             "compared with the ChemDraw file.", NOTE, "structure",
                "generic structure", structures[0][1])
        elif si_mol is None:
            add("structure", f"{label}: the ChemDraw file has a structure but the SI compound "
                             "has none yet.", NOTE, "structure", "—", structures[0][1])
        else:
            for smiles, where in structures[:3]:
                status, fp, a_f, b_f = compare_structures(smiles, si_mol)
                key = f"structure:{P.id_key(label)}:{where}"
                if status == "same":
                    add("structure", f"{label}: the structure agrees ({b_f}).", OK, a_f, b_f, where)
                elif status == "salt":
                    add("structure", f"{label}: the structure agrees once the counter-ion is "
                                     f"left out (compared by the largest piece, {b_f}).", OK,
                        a_f, b_f, where)
                elif status == "stereo":
                    add("structure", f"{label}: {where} and the SI have the same compound with "
                                     "different stereochemistry (wedges, E/Z or R/S).", PROBLEM,
                        f"{a_f}, other stereo", b_f, where, key, fp)
                elif status == "charge":
                    add("structure", f"{label}: {where} and the SI have the same compound with a "
                                     "different charge or protonation.", PROBLEM, a_f, b_f,
                        where, key, fp)
                elif status == "different":
                    add("structure", f"{label}: the structure in {where} ({a_f}) is not the SI "
                                     f"structure ({b_f}).", PROBLEM, a_f, b_f, where, key, fp)

    # --- yield
    yields = mc.values("yield")
    si_y = _si_yield(c)
    if yields:
        out += _internal(label, c.id, "yield", yields, _pct, same_yield)
        printed = ", ".join(_pct(v) for v in dict.fromkeys(v for v, _w in yields))
        if not si_y:
            add("yield", f"{label}: the manuscript gives {printed} yield; the SI's Yield field "
                         "is empty.", PROBLEM, printed, "—", yields[0][1])
        elif any(same_yield(v, si_y) for v, _w in yields):
            add("yield", f"{label}: the yield agrees ({_pct(si_y)}).", OK, printed, _pct(si_y))
        else:
            add("yield", f"{label}: the manuscript gives {printed} yield, the SI {_pct(si_y)}.",
                PROBLEM, printed, _pct(si_y), yields[0][1])
    nmr = mc.values("yield_nmr")
    if nmr and not yields:
        add("yield", f"{label}: the manuscript gives only an NMR/GC yield ({nmr[0][0]}%); it is "
                     "not compared with the SI's isolated yield.", NOTE, f"{nmr[0][0]}% (NMR)",
            f"{si_y}%" if si_y else "—", nmr[0][1])

    # --- ee / er
    ee_recs = [(r, _ms_ee_text(r)) for r in mc.records if _ms_ee_text(r) and mc.own(r)]
    si_ee_text = _si_part(c, "ee")
    if ee_recs:
        vals = [(text, r.get("place", "")) for r, text in ee_recs]
        out += _internal(label, c.id, "ee", vals, lambda v: v,
                         lambda a, b: _same_ee_text(a, b))
        printed = ", ".join(dict.fromkeys(t for t, _w in vals))
        if not si_ee_text:
            # (a racemic product with no ee in the SI is as expected)
            if not all(t == P.RACEMIC for t, _w in vals):
                add("ee", f"{label}: the manuscript gives {printed}; the SI has no ee/er.",
                    PROBLEM, printed, "—", vals[0][1])
        else:
            si_ee = ee_value(si_ee_text)
            match = si_ee is not None and any(
                (m := ee_value(t)) is not None and same_ee(m, si_ee) for t, _w in vals)
            if match:
                add("ee", f"{label}: the ee/er agrees ({si_ee_text}).", OK, printed, si_ee_text)
            else:
                conv = []
                for t, _w in vals:
                    m = ee_value(t)
                    if m and m[2]:
                        conv.append(f"{t} = {m[1]:g}% ee")
                if si_ee and si_ee[2]:
                    conv.append(f"SI {si_ee_text} = {si_ee[1]:g}% ee")
                extra = f"; {'; '.join(conv)}" if conv else ""
                add("ee", f"{label}: the manuscript gives {printed}, the SI "
                          f"{F_ee(si_ee_text)}{extra}.", PROBLEM, printed, si_ee_text, vals[0][1])

    # --- dr
    drs = mc.values("dr")
    si_dr = _si_part(c, "dr")
    if drs:
        out += _internal(label, c.id, "dr", drs, lambda v: f"{v} dr", same_dr)
        printed = ", ".join(dict.fromkeys(f"{v}" for v, _w in drs))
        if not si_dr:
            add("dr", f"{label}: the manuscript gives {printed} dr; the SI has no dr.", PROBLEM,
                printed, "—", drs[0][1])
        elif any(same_dr(v, si_dr) for v, _w in drs):
            add("dr", f"{label}: the dr agrees ({si_dr}).", OK, printed, si_dr)
        else:
            add("dr", f"{label}: the manuscript gives {printed} dr, the SI {si_dr}.", PROBLEM,
                printed, si_dr, drs[0][1])

    # --- values in running text: notes only (SPEC §13o.6)
    out += _text_notes(label, c, mc)

    # --- names
    if mc.names:
        si_name = c.name
        ms_names = list(dict.fromkeys(n["name"] for n in mc.names))
        distinct: list[str] = []
        for n in ms_names:
            if not any(same_name(n, d) for d in distinct):
                distinct.append(n)
        if len(distinct) > 1:
            add("internal", f"{label}: the manuscript names it in different ways: "
                            + "; ".join(f"“{n}”" for n in distinct) + ".", PROBLEM,
                " / ".join(distinct), "", mc.names[0].get("place", ""),
                f"internal:name:{P.id_key(label)}")
        if si_name:
            if any(same_name(n, si_name) for n in ms_names):
                add("name", f"{label}: the name agrees.", OK, ms_names[0], si_name)
            else:
                add("name", f"{label}: the manuscript calls it “{ms_names[0]}”; the SI name is "
                            f"“{si_name}”.", PROBLEM, ms_names[0], si_name,
                    mc.names[0].get("place", ""))

    # --- conditions
    own, sentence, gp_text = si_procedure_text(project, c, gp_texts)
    si_all = "\n".join((own, sentence, gp_text))
    si_q = P.quantities(si_all)
    markers = mc.markers
    for marker, text, where in markers:
        if not text:
            add("conditions", f"{label}: footnote {marker} was not found in the manuscript, so "
                              "its conditions could not be checked.", NOTE, f"footnote {marker}",
                "", where, f"conditions:{P.id_key(label)}:{marker}")
            continue
        wanted = P.quantities(text)
        if not wanted:
            continue
        missing = [q for q in wanted if not any(q.same(s) for s in si_q)]
        key = f"conditions:{P.id_key(label)}:{marker}"
        if not missing:
            add("conditions", f"{label}: footnote {marker} (“{text}”) agrees with its SI "
                              "procedure.", OK, text, "found", where, key)
        else:
            listed = ", ".join(dict.fromkeys(q.text for q in missing))
            add("conditions", f"{label}: footnote {marker} of the manuscript (“{text}”) mentions "
                              f"{listed}, which {'is' if len(missing) == 1 else 'are'} not in its "
                              "SI procedure: its own text and its general procedure.", PROBLEM,
                text, "not in the SI: " + listed, where, key)
    gp = project.compound_gp(c)
    if not markers and gp is not None:
        out += _deviations(label, c, mc, own, gp_text + "\n" + (gp.get("sentence") or ""),
                           gp.get("name") or "its general procedure")
    return out


# What the check of an SI procedure against its general procedure compares (Nathan,
# SPEC §13o.6): not the scale, which is expected to differ.
DEVIATION_KINDS = (("time", "time"), ("temperature", "temperature"),
                   ("loading", "catalyst loading"), ("equiv", "equivalents"),
                   ("solvent", "solvent"))


def _reaction_quantities(text: str) -> list[P.Quantity]:
    """The time, temperature, loading, equivalents and solvent of the reaction itself
    (the work-up's sentences left out: "cooled to room temperature", "stirred for 10 min
    with water" are not the reaction's conditions)."""
    part = P.reaction_part(text)
    out = [q for q in P.quantities(part, reagents_too=False)
           if q.kind in ("time", "temperature", "loading", "equiv")]
    out += [P.Quantity("solvent", q.value, q.text) for q in P.reagents(part)
            if q.value in P.SOLVENTS]
    return out


def _deviations(label, c, mc: MsCompound, own: str, gp_text: str, gp_name: str):
    """An SI procedure whose reaction uses another time, temperature, loading,
    equivalents or solvent than its general procedure, while the manuscript has no
    footnote on the compound saying so."""
    out = []
    gp_q = _reaction_quantities(gp_text)
    own_q = _reaction_quantities(own)
    for kind, word in DEVIATION_KINDS:
        mine = [q for q in own_q if q.kind == kind]
        theirs = [q for q in gp_q if q.kind == kind]
        if not mine or not theirs:
            continue
        new = [q for q in mine if not any(q.same(t) for t in theirs)]
        if new:
            own_text = ", ".join(dict.fromkeys(q.text for q in new))
            gp_vals = ", ".join(dict.fromkeys(q.text for q in theirs))
            out.append(Finding(
                "deviation", label, f"{label}: its SI procedure uses {own_text}, where "
                f"{gp_name} has {gp_vals}, but the manuscript has no footnote on {label} saying "
                "so.", PROBLEM, c.id, "no footnote", f"{word}: {own_text}", mc.where,
                f"deviation:{P.id_key(label)}:{kind}"))
    return out


def _text_notes(label: str, c, mc: MsCompound) -> list[Finding]:
    """Yields, ee and dr stated in running-text sentences: compared with the SI but only
    ever notes (the sentence may be about another experiment), quoting the sentence."""
    out = []
    si = {"yield": _si_yield(c), "ee": _si_part(c, "ee"), "dr": _si_part(c, "dr")}
    for n, r in enumerate(mc.text_records):
        quote = str(r.get("text") or "").strip()
        if len(quote) > 220:
            quote = quote[:217].rstrip() + "…"
        for kind in ("yield", "ee", "dr"):
            if kind == "ee":
                ms_text = _ms_ee_text(r)
            else:
                ms_text = str(r.get(kind) or "")
                ms_text = _pct(ms_text) if kind == "yield" and ms_text else \
                    (f"{ms_text} dr" if ms_text else "")
            if not ms_text:
                continue
            si_text = si[kind]
            if kind == "yield":
                agrees = bool(si_text) and same_yield(r["yield"], si_text)
                si_shown = _pct(si_text) if si_text else ""
            elif kind == "ee":
                a, b = ee_value(ms_text), ee_value(si_text) if si_text else None
                agrees = a is not None and b is not None and same_ee(a, b)
                si_shown = si_text
            else:
                agrees = bool(si_text) and same_dr(r["dr"], si_text)
                si_shown = si_text
            what = {"yield": "yield", "ee": "ee/er", "dr": "dr"}[kind]
            if agrees:
                msg = f"{label}: the {what} in the text agrees ({ms_text}): “{quote}”"
            elif si_shown:
                msg = (f"{label}: the text says “{quote}”, giving {ms_text}; the SI has "
                       f"{si_shown}. Running text is only noted: check whether this sentence "
                       "is about the same experiment.")
            else:
                msg = (f"{label}: the text says “{quote}”, giving {ms_text}; the SI has no "
                       f"{what} for it. Running text is only noted.")
            out.append(Finding(kind, label, msg, OK if agrees else NOTE, c.id,
                               f"{ms_text} (in the text)", si_shown or "—",
                               r.get("place", ""), f"text:{kind}:{P.id_key(label)}:{n}"))
    return out


def F_ee(text: str) -> str:
    from .fields import ee_with_unit
    return ee_with_unit(text)


def _same_ee_text(a: str, b: str) -> bool:
    x, y = ee_value(a), ee_value(b)
    return x is not None and y is not None and same_ee(x, y)


def _internal(label: str, compound_id: str, kind: str, vals: list[tuple[str, str]], show,
              same) -> list[Finding]:
    """A finding if the manuscript itself gives different values of one kind."""
    distinct: list[tuple[str, str]] = []
    for v, w in vals:
        if not any(same(v, d) for d, _w in distinct):
            distinct.append((v, w))
    if len(distinct) < 2:
        return []
    listed = " and ".join(f"{show(v)} in {w}" if w else show(v) for v, w in distinct)
    word = {"yield": "yields", "ee": "ee/er values", "dr": "dr values"}[kind]
    return [Finding("internal", label, f"{label}: the manuscript gives different {word}: "
                                       f"{listed}.", PROBLEM, compound_id,
                    " / ".join(show(v) for v, _w in distinct), "", distinct[0][1],
                    f"internal:{kind}:{P.id_key(label)}")]


def cached_result(project) -> Result | None:
    """The comparison made with what was read last time (no file is read): for the
    Warnings panel. None if nothing was read yet or the files changed since."""
    if not files(project):
        return None
    try:
        sources = cached_sources(project)
        return None if sources is None else check(project, sources)
    except Exception:  # noqa: BLE001 — never break the Warnings panel
        return None
