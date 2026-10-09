"""The text of one compound's block in the SI (SPEC §9.2), ACS style.

    **Ethyl 2-methylfuran-3-carboxylate (3g).**  [structure]
    <procedure>
    Colorless oil (28.4 mg, 92%). *R*f = 0.35 (30% EtOAc/hexanes; UV). **¹H NMR** (400 MHz,
    CDCl₃) δ 7.25 (d, *J* = 2.0 Hz, 1H), … **HRMS** (ESI) *m/z*: [M+H]⁺ calcd for C₈H₁₁O₃
    155.0703; found 155.0701.

The block is built as paragraphs of runs (the same format as stored text), so the
user can start from the generated text and edit it: the "SI text" field replaces
the generated text exactly as typed (SPEC §2.7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core import chem, checks, ms_reports, procedures
from ..core import fields as F
from ..core.labels import is_inline_figure
from ..core.markup import CITE, NORMAL, SUB, SUPER, Paragraph, Run, parse
from ..core.project import DEFAULT_GP_SENTENCE
from .text import charge_runs, format_chemistry, format_nmr, formula_runs, linked

SI_TEXT_KEY = "si_text"
SKIP = {"structure", "name", "notes", "private_files", "spectra", "files", "scale", SI_TEXT_KEY}


@dataclass
class Figure:
    path: Path
    caption: str = ""


@dataclass
class CompoundBlock:
    title: list[Run]
    paragraphs: list[Paragraph]
    figures: list[Figure] = field(default_factory=list)
    custom_text: bool = False   # the user's own SI text replaced the generated text


# ---------------------------------------------------------------------- small helpers

def R(text: str, bold=False, italic=False, valign=NORMAL) -> Run:  # noqa: N802
    return Run(text, bold, italic, valign)


def _plain(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


def _end_sentence(runs: list[Run]) -> list[Run]:
    text = _plain(runs).rstrip()
    if text and text[-1] not in ".;:!?":
        runs = runs + [R(".")]
    return runs


def _dash_ranges(text: str) -> str:
    return re.sub(r"(?<=\d)\s*-\s*(?=\d)", "–", text)


def _value(compound, key: str) -> dict:
    """A field's stored value ({} when it is empty, or not of the right kind: e.g.
    compound.json edited by hand)."""
    v = compound.values.get(key)
    return v if isinstance(v, dict) else {}


def _items(compound, key: str) -> list[dict]:
    """A list field's items (peak reports, attached files), leaving out anything that is
    not one."""
    v = compound.values.get(key)
    return [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []


def _bold_prefix(runs: list[Run], n: int) -> list[Run]:
    """Make the first n characters bold."""
    out, left = [], n
    for r in runs:
        if left <= 0:
            out.append(r)
        elif len(r.text) <= left:
            out.append(r.but(bold=True))
            left -= len(r.text)
        else:
            out.append(r.but(text=r.text[:left], bold=True))
            out.append(r.but(text=r.text[left:]))
            left = 0
    return out


def _insert(runs: list[Run], index: int, text: str) -> list[Run]:
    out, pos, done = [], 0, False
    for r in runs:
        if not done and pos <= index <= pos + len(r.text):
            cut = index - pos
            out += [r.but(text=r.text[:cut]), R(text), r.but(text=r.text[cut:])]
            done = True
        else:
            out.append(r)
        pos += len(r.text)
    if not done:
        out.append(R(text))
    return [r for r in out if r.text]


# ---------------------------------------------------------------------- sentences

_NMR_HEADER = re.compile(r"^\s*\d{1,3}\s*[A-Z][a-z]?(?:\s*\{\s*\d{1,3}\s*[A-Z][a-z]?\s*\})?\s*NMR")


def nmr_sentence(report: dict, use_helpers: bool, link=None) -> list[Run]:
    """One peak report as a sentence, starting with its bold "¹H NMR". `link(nucleus)`
    gives the Typst label of the compound's spectrum of that nucleus, or "" (SPEC
    §13l.7): the bold "¹H NMR" then links to it."""
    runs = [r for p in parse(F.part_text(report.get("text"))) for r in p.runs + [R(" ")]][:-1]
    if not _plain(runs).strip():
        return []
    if use_helpers:
        runs = format_nmr(runs)
    text = _plain(runs)
    m = _NMR_HEADER.match(text)
    label = F.part_text(report.get("label")).strip()
    nucleus = F.part_text(report.get("nucleus"))
    if m:
        header_length = m.end()
        runs = _bold_prefix(runs, m.end())
        # The nucleus the reader sees: "13C{1H} NMR" -> "13C".
        written = re.match(r"\s*(\d{1,3})\s*([A-Z][a-z]?)", text)
        nucleus = written.group(1) + written.group(2)
        if label:
            after = text[m.end():]
            par = re.match(r"\s*\(([^)]*)\)", after)
            if par:  # "(400 MHz, CDCl3)" -> "(400 MHz, CDCl3, major rotamer)"
                runs = _insert(runs, m.end() + par.end() - 1, f", {label}")
            else:
                runs = _insert(runs, m.end(), f" ({label})")
    else:
        n = nucleus or "1H"
        nucleus = n
        digits = n[:len(n) - len(n.lstrip("0123456789"))]
        header = ([R(digits, bold=True, valign=SUPER)] if digits else []) + \
                 [R(n[len(digits):] + " NMR", bold=True)]
        header_length = len(_plain(header))
        runs = header + ([R(f" ({label})")] if label else []) + [R(" ")] + runs
    target = link(nucleus) if link is not None else ""
    if target:
        lead = len(text) - len(text.lstrip()) if m else 0   # (not the spaces before it)
        runs = runs if not lead else _split_at(runs, lead)
        head = []
        while lead > 0 and runs:
            lead -= len(runs[0].text)
            head.append(runs.pop(0))
        runs = head + linked(runs, header_length - len(_plain(head)), target)
    return _end_sentence(runs)


def _split_at(runs: list[Run], index: int) -> list[Run]:
    """`runs` with a run cut in two at character `index` (if one spans it)."""
    out, pos = [], 0
    for r in runs:
        if pos < index < pos + len(r.text) and not r.ref:
            cut = index - pos
            out += [r.but(text=r.text[:cut]), r.but(text=r.text[cut:])]
        else:
            out.append(r)
        pos += len(r.text)
    return out


def hrms_sentence(project, compound) -> list[Run]:
    value = _value(compound, "hrms")
    found_text = F.part_text(value.get("found"))
    found = checks.parse_number(found_text)
    ion = F.part_text(value.get("ionization")).strip()
    adduct = F.part_text(value.get("adduct")).strip() or "[M+H]+"
    runs = [R("HRMS", bold=True)] + ([R(f" ({ion})")] if ion else []) + \
           [R(" "), R("m/z", italic=True), R(": ")] + charge_runs(adduct)
    try:
        sp = checks.hrms_species(project.read_structure(compound, strict=True), value)
        result = checks.hrms_calc(sp.info, adduct, found_text,
                                  ms_reports.tolerance(project)) if sp else None
    except (chem.ChemError, OSError):
        # (OSError: structure.mol can't be read just now; the compile's notes say so)
        result = None
    if result is not None:
        peak = result.matched if (result.matched is not None and not result.out_of_tolerance) \
            else result.monoisotopic
        runs += [R(" calcd for ")] + formula_runs(peak.formula) + [R(f" {peak.mz:.4f}")]
        if found is not None:
            runs += [R(f"; found {found:.4f}")]
    elif found is not None:
        runs += [R(f" found {found:.4f}")]
    else:
        return []
    return _end_sentence(runs)


def _parts_sentence(project, compound, defn: F.FieldDef, use_helpers: bool) -> list[Run]:
    v = {k: F.part_text(x).strip() for k, x in _value(compound, defn.key).items()}
    key = defn.key
    fmt = format_chemistry if use_helpers else (lambda runs: runs)
    if key == "rf":
        if not v.get("value"):
            return []
        extra = "; ".join(x for x in (v.get("eluent", ""), v.get("visualization", "")) if x)
        return _end_sentence([R("R", italic=True), R("f", italic=True, valign=SUB),
                              R(f" = {v['value']}")] + fmt([R(f" ({extra})")] if extra else []))
    if key == "mp":
        if not v.get("value"):
            return []
        solvent = f" ({v['solvent']})" if v.get("solvent") else ""
        return _end_sentence([R("mp", bold=False), R(f" {_dash_ranges(v['value'])} °C")]
                             + fmt([R(solvent)] if solvent else []))
    if key == "ir":
        if not v.get("bands"):
            return []
        method = f" ({v['method']})" if v.get("method") else ""
        return _end_sentence([R("IR", bold=True), R(method + " "), R("ν", italic=True),
                              R("max", valign=SUB), R(f" {v['bands']} cm"), R("−1", valign=SUPER)])
    if key == "rotation":
        if not v.get("value"):
            return []
        runs = [R("[α]"), R("D", valign=SUB)]
        if v.get("temperature"):
            runs.append(R(v["temperature"], valign=SUPER))
        runs.append(R(f" {v['value']}"))
        inside = [R("c", italic=True), R(f" {v.get('c', '')}")] if v.get("c") else []
        if v.get("solvent"):
            inside += ([R(", ")] if inside else []) + fmt([R(v["solvent"])])
        if inside:
            runs += [R(" (")] + inside + [R(")")]
        return _end_sentence(runs)
    if key == "ee":
        return ee_sentence(v, fmt)
    if key == "dr":
        if not v.get("value"):
            return []
        runs = [R(f"d.r. = {v['value']}")]
        if v.get("method"):
            runs += [R(" (determined by ")] + (format_nmr([R(v["method"])]) if use_helpers
                                              else [R(v["method"])]) + [R(")")]
        return _end_sentence(runs)
    if key == "ea":
        if not v.get("calcd") and not v.get("found"):
            return []
        runs = [R("Anal.")]
        if v.get("calcd"):   # (without calculated values: only what was found)
            runs.append(R(" Calcd"))
            if v.get("formula"):   # typed: e.g. a solvate the values were calculated for
                runs += [R(" for ")] + typed_formula_runs(v["formula"])
            else:
                try:
                    info = project.structure_info(compound, strict=True)
                except (chem.ChemError, OSError):
                    # (e.g. a generic atom (R), or structure.mol can't be read just now:
                    # the compile's notes say so) no formula to give
                    info = None
                if info is not None:
                    runs += [R(" for ")] + formula_runs(info.formula)
            runs.append(R(f": {v['calcd']}" + ("." if v.get("found") else "")))
        if v.get("found"):
            runs.append(R(f" Found: {v['found']}"))
        return _end_sentence(runs)
    if key == "xray":
        return _end_sentence([R(f"CCDC {v['ccdc']}")]) if v.get("ccdc") else []
    if defn.custom:  # custom short text / number with unit
        text = " ".join(x for x in (v.get("text", ""), v.get("value", ""), v.get("unit", "")) if x)
        return _end_sentence([R(f"{defn.label}: ")] + fmt([R(text)])) if text else []
    return []


_FORMULA_PIECE = re.compile(r"(\d+(?:\.\d+)?)?((?:\[\d+[A-Z][a-z]?\]|[A-Z][a-z]?|\d+)+)")


def typed_formula_runs(text: str) -> list[Run]:
    """A formula typed by the user, which may be a solvate or salt: "C12H15NO·0.25H2O"
    prints with only the atom counts as subscripts (the 0.25 stays as it is). Anything
    that isn't a plain formula is printed exactly as typed."""
    pieces = re.split(r"\s*([·•∙])\s*", text.strip())
    out: list[Run] = []
    for i, piece in enumerate(pieces):
        if i % 2:   # the dot between the parts
            out.append(R(piece))
            continue
        m = _FORMULA_PIECE.fullmatch(piece)
        if not m:
            return [R(text.strip())]
        out += ([R(m.group(1))] if m.group(1) else []) + formula_runs(m.group(2))
    return out


def ee_sentence(v: dict[str, str], fmt=lambda runs: runs) -> list[Run]:
    """The ee field (SPEC §13l.12b), value first:
    "99% ee. HPLC (Chiralpak ID, 10% iPrOH/hexanes, 1.0 mL/min, 254 nm): tR (major) =
    12.3 min, tR (minor) = 14.8 min." Missing parts are left out; with nothing but the
    method: "99% ee (HPLC)." A bare "99%" or "97:3" gets its ee/er here too."""
    value = F.ee_with_unit(v.get("value", ""))
    if not value:
        return []
    conditions = ", ".join(x for x in (v.get("column", ""), v.get("eluent", ""),
                                        f"{v['flow']} mL/min" if v.get("flow") else "",
                                        f"{v['wavelength']} nm" if v.get("wavelength") else "")
                           if x)
    times: list[list[Run]] = []
    for which, k in (("major", "t_major"), ("minor", "t_minor")):
        if v.get(k):
            times.append([R("t", italic=True), R("R", valign=SUB), R(f" ({which}) = {v[k]} min")])
    method = v.get("method", "")
    if not conditions and not times:
        return _end_sentence([R(value + (f" ({method})" if method else ""))])
    runs = _end_sentence([R(value)]) + [R(" " + (method or "HPLC"))]
    if conditions:
        runs += [R(" (")] + fmt([R(conditions)]) + [R(")")]
    if times:
        runs.append(R(": "))
        for i, t in enumerate(times):
            runs += ([R(", ")] if i else []) + t
    return _end_sentence(runs)


def state_and_yield(compound) -> list[Run]:
    state = F.part_text(_value(compound, "physical_state").get("text")).strip() \
        if "physical_state" in compound.field_keys else ""
    y = _value(compound, "yield") if "yield" in compound.field_keys else {}
    mass, unit, pct = (F.part_text(y.get(k)).strip() for k in ("mass", "mass_unit", "percent"))
    amounts = ", ".join(x for x in (f"{mass} {unit}".strip() if mass else "",
                                    f"{pct}%" if pct else "") if x)
    if state:
        text = state[0].upper() + state[1:] + (f" ({amounts})" if amounts else "")
    elif amounts:
        text = f"Yield: {amounts}"
    else:
        return []
    return _end_sentence([R(text)])


def gp_sentence(project, compound, problems: list[str] | None = None) -> list[Run]:
    """The chosen general procedure's compound sentence, filled in (SPEC §13j)."""
    gp = project.compound_gp(compound)
    if gp is None:
        return []
    choice = project.gp_choice(compound)
    runs = procedures.fill(gp.get("sentence", "") or DEFAULT_GP_SENTENCE,
                           choice.get("values") or {},
                           procedures.auto_values(project, compound, gp), problems)
    return _end_sentence(runs) if runs else []


def citation_sentence(project, compound, key: str = "references") -> list[Run]:
    """"Spectral data matched those reported in the literature.¹²" """
    value = _value(compound, key)
    refs = value.get("refs")
    ids = [i for i in refs if isinstance(i, str) and i] if isinstance(refs, list) else []
    text = F.part_text(value.get("text")).strip()
    if not ids and not text:
        return []
    runs = [r for p in parse(text) for r in p.runs] if text else []
    runs = _end_sentence(runs) if runs else []
    if ids:
        runs.append(Run("", ref=CITE + ",".join(ids)))
    return runs


# ---------------------------------------------------------------------- the block

def compound_title(project, compound, display_id: str) -> list[Run]:
    name = compound.name
    if name:
        return [R(f"{name} (", bold=True), R(display_id, bold=True), R(").", bold=True)]
    return [R(f"Compound {display_id}.", bold=True)]


def generated_paragraphs(project, compound, use_helpers: bool = True,
                         nmr_link=None) -> list[Paragraph]:
    """The text the program writes for this compound (procedure, then the data).
    `nmr_link(nucleus)`: the label of the compound's spectrum to link each peak report's
    "¹H NMR" to, or "" (see nmr_sentence)."""
    paragraphs: list[Paragraph] = []
    data: list[Run] = []
    did_state = False
    # The Purification field's sentence (SPEC §13q.2): at the end of the experimental
    # procedure, unless its general procedure's sentence places {purification} itself;
    # without a procedure field, where the Purification field is.
    purification = procedures.purification_sentence(compound)
    if purification and procedures.uses_purification(project.compound_gp(compound)):
        purification = ""
    with_procedure = "procedure" in compound.field_keys
    for defn in project.field_defs(compound):
        if defn.key in SKIP:
            continue
        if defn.key == "purification":
            if purification and not with_procedure:
                runs = [R(purification)]
                paragraphs.append(Paragraph(format_chemistry(runs) if use_helpers else runs))
            continue
        if defn.kind == "richtext":
            text = project.read_compound_text(compound, defn.key)
            own = [p for p in parse(text) if p.runs]
            if defn.key == "procedure":
                sentence = gp_sentence(project, compound)
                if sentence:  # "Prepared according to General Procedure A from 1a (0.20 mmol)."
                    first = own.pop(0).runs if own else []
                    own.insert(0, Paragraph(sentence + ([R(" ")] + first if first else [])))
                if purification:   # "… The crude product was purified by …"
                    last = own.pop().runs if own else []
                    own.append(Paragraph((last + [R(" ")] if last else []) + [R(purification)]))
            for p in own:
                paragraphs.append(Paragraph(format_chemistry(p.runs) if use_helpers else p.runs))
            continue
        if defn.kind == "citations":
            data += _with_space(citation_sentence(project, compound, defn.key))
            continue
        if defn.key in ("physical_state", "yield"):
            if not did_state:
                data += _with_space(state_and_yield(compound))
                did_state = True
            continue
        if defn.kind == "nmr":
            for report in _items(compound, "nmr"):
                data += _with_space(nmr_sentence(report, use_helpers, nmr_link))
            continue
        if defn.key == "hrms":
            data += _with_space(hrms_sentence(project, compound))
            continue
        if defn.kind == "parts":
            data += _with_space(_parts_sentence(project, compound, defn, use_helpers))
    if data:
        paragraphs.append(Paragraph(data[:-1] if data[-1].text == " " else data))
    return paragraphs


def _with_space(runs: list[Run]) -> list[Run]:
    return runs + [R(" ")] if runs else []


def inline_figures(project, compound) -> list[Figure]:
    figures = []
    for defn in project.field_defs(compound):
        if defn.kind != "attachments" or defn.key == "spectra":
            continue
        # (the same items as core/labels counts: is_inline_figure, so each gets its own
        # figure label)
        for item in _items(compound, defn.key):
            if is_inline_figure(item):
                figures.append(Figure(project.attachment_path(compound, defn.key,
                                                              F.part_text(item["file"])),
                                      F.part_text(item.get("caption"))))
    return figures


def compound_block(project, compound, display_id: str, use_helpers: bool = True,
                   nmr_link=None) -> CompoundBlock:
    """`nmr_link`: see generated_paragraphs (the user's own SI text gets no links)."""
    title = compound_title(project, compound, display_id)
    custom = project.read_compound_text(compound, SI_TEXT_KEY).strip() \
        if SI_TEXT_KEY in compound.field_keys else ""
    if custom:
        # (follows the journal style: an alignment typed into the file is ignored)
        paragraphs = [Paragraph(p.runs, p.heading) for p in parse(custom) if p.runs]
    else:
        paragraphs = generated_paragraphs(project, compound, use_helpers, nmr_link)
    return CompoundBlock(title, paragraphs, inline_figures(project, compound), bool(custom))
