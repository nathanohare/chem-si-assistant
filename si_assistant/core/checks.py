"""Automatic checks (SPEC §8.2). Every result is a warning; nothing blocks the user.

Every check returns `Warning`s. A warning can be dismissed per compound (with an optional
reason); the dismissal is remembered with a fingerprint of the data the warning was about,
so the warning comes back if that data changes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from . import chem, impurities, ms_reports, nmr_parse
from . import fields as F
from .compound_ids import IdReport
from .markup import parse as parse_markup

ID_KEY = "id"  # pseudo field key for ID warnings


@dataclass
class HrmsResult:
    adduct_label: str
    peaks: list[chem.Peak]
    found: float | None = None
    matched: chem.Peak | None = None   # calculated peak nearest to the found value
    ppm: float | None = None
    tolerance: float = 5.0

    @property
    def out_of_tolerance(self) -> bool:
        return self.ppm is not None and abs(self.ppm) > self.tolerance

    @property
    def monoisotopic(self) -> chem.Peak:
        return next((p for p in self.peaks if p.monoisotopic), self.peaks[0])


def parse_number(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".").replace("−", "-")
    try:
        return float(text)
    except ValueError:
        return None


def hrms_calc(info: chem.MolInfo, adduct: str, found_text: str,
              tolerance: float) -> HrmsResult:
    """Calculated isotope peaks, and the ppm error against the nearest one.

    Raises chem.ChemError for an adduct that cannot be used.
    """
    adduct = adduct or "[M+H]+"
    # min 1% so a found value on e.g. a ¹³C or ⁸¹Br peak is still matched to it.
    peaks = chem.isotope_pattern(info, adduct, min_percent=1.0, max_peaks=12)
    result = HrmsResult(adduct, peaks, tolerance=tolerance)
    found = parse_number(found_text)
    if found is not None and found > 0:
        result.found = found
        result.matched = chem.nearest_peak(peaks, found)
        result.ppm = chem.ppm_error(found, result.matched.mz)
        if abs(result.ppm) > tolerance:
            # No isotope peak fits: report the error against the expected (monoisotopic) peak.
            result.matched = result.monoisotopic
            result.ppm = chem.ppm_error(found, result.matched.mz)
    return result


@dataclass
class HrmsSpecies:
    """What "M" is for the HRMS calculation."""
    info: chem.MolInfo
    manual: bool = False                       # True: the user typed the M formula
    choices: list[tuple[str, str, chem.MolInfo]] | None = None  # salt pieces: (key, label, info)
    key: str = ""                              # chosen piece ("" = automatic)


def species_label(info: chem.MolInfo) -> str:
    return info.formula + chem.charge_text(info.charge)


# (pieces, whole-structure info) per molecule, kept between calls: compiling the SI (and the
# live preview of its table of contents) works this out for every compound each time. The
# project keeps one molecule object per structure.mol until the file changes; the entry
# holds the molecule, so its id is never reused while it is in here.
_SPECIES: dict[int, tuple] = {}
_SPECIES_MAX = 3000


def _species_of(mol) -> tuple[list[tuple[str, chem.MolInfo]], chem.MolInfo]:
    hit = _SPECIES.get(id(mol))
    if hit is not None and hit[0] is mol:
        return hit[1], hit[2]
    pieces = chem.species(mol)
    whole = chem.mol_info(mol)
    if len(_SPECIES) >= _SPECIES_MAX:
        _SPECIES.clear()
    _SPECIES[id(mol)] = (mol, pieces, whole)
    return pieces, whole


def hrms_species(mol, value: dict) -> HrmsSpecies | None:
    """M for the HRMS field: the typed formula if any (raises chem.ChemError if it can't be
    read), else the chosen piece of a salt, else the whole structure. None = no structure."""
    formula = str(value.get("formula", "")).strip()
    if formula:
        return HrmsSpecies(chem.info_from_counts(chem.parse_m_formula(formula)), manual=True)
    if mol is None:
        return None
    pieces, whole = _species_of(mol)
    if len(pieces) == 1:
        return HrmsSpecies(whole)
    choices = [("whole", f"Whole structure ({species_label(whole)})", whole)]
    choices += [(f"frag:{smi}", species_label(info), info) for smi, info in pieces]
    key = str(value.get("species", ""))
    chosen = next((c for c in choices if c[0] == key), None)
    if chosen is None:  # automatic: the largest piece (e.g. the organic cation of a salt)
        key, chosen = "", choices[1]
    return HrmsSpecies(chosen[2], choices=choices, key=key)


def suggested_adduct(info: chem.MolInfo) -> str | None:
    """For an ion that is already charged as drawn: [M]+, [M]2+, [M]−…"""
    if not info.charge:
        return None
    n = abs(info.charge)
    return f"[M]{n if n > 1 else ''}{'+' if info.charge > 0 else '-'}"


def charged_adduct_hint(info: chem.MolInfo, adduct: str) -> str:
    """A gentle warning when M is already an ion but the adduct adds atoms or charge."""
    if not info.charge:
        return ""
    try:
        a = chem.parse_adduct(adduct or "[M+H]+")
    except chem.ChemError:
        return ""
    if not a.delta and a.charge == info.charge and a.multimer == 1:
        return ""
    usual = suggested_adduct(info)
    return (f"M is already charged ({species_label(info)}); it is usually reported as "
            f"{chem.adduct_display(usual)}.")


def compound_hrms(project, compound) -> tuple[HrmsResult | None, str]:
    """(result, problem) for a compound's saved HRMS field. problem is "" if none."""
    if "hrms" not in compound.field_keys:
        return None, ""
    value = compound.values.get("hrms") or {}
    try:
        sp = hrms_species(project.read_structure(compound), value)
    except chem.ChemError as e:
        return None, f"HRMS formula: {e}"
    if sp is None:
        return None, ""
    info = sp.info
    try:
        result = hrms_calc(info, value.get("adduct", ""), value.get("found", ""),
                           float(project.settings.get("hrms_tolerance_ppm", 5.0)))
    except chem.ChemError as e:
        return None, str(e)
    if result.out_of_tolerance:
        return result, (f"HRMS error {result.ppm:+.1f} ppm is outside the "
                        f"±{result.tolerance:g} ppm tolerance.")
    return result, ""


# ---------------------------------------------------------------------- all checks (§8.2)

@dataclass
class Warning:
    """One automatic warning about one compound.

    `key` identifies the warning within the compound (used to remember a dismissal);
    `fingerprint` changes whenever the data the warning is about changes, so a dismissed
    warning comes back if, say, the found m/z is edited.
    """
    check: str
    field: str      # the field it belongs to ("id" for ID problems)
    message: str
    key: str
    fingerprint: str


def _fp(*parts) -> str:
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:12]


def _report_text(report: dict) -> str:
    return " ".join(p.plain_text for p in parse_markup(str(report.get("text", ""))))


NMR_RATIOS = {"13C": 0.2515, "19F": 0.9408, "31P": 0.4048, "11B": 0.3209, "15N": 0.1014,
              "29Si": 0.1987, "2H": 0.1535}
MASS_TO_MG = {"mg": 1.0, "g": 1000.0, "µg": 0.001, "ug": 0.001}
AMOUNT_TO_MMOL = {"mmol": 1.0, "mol": 1000.0, "µmol": 0.001, "umol": 0.001}


def all_warnings(project, compound, id_report: IdReport) -> list[Warning]:
    """Every warning for a compound, dismissed or not."""
    out: list[Warning] = []
    keys = set(compound.field_keys)
    mol = project.read_structure(compound)
    smiles = chem.mol_info(mol).smiles if mol is not None else ""

    # 9. IDs
    for msg in id_report.compound_warnings.get(compound.id, []):
        out.append(Warning("id", ID_KEY, msg, f"id:{_fp(msg)}", _fp(msg)))

    # 1. HRMS
    if "hrms" in keys:
        value = compound.values.get("hrms") or {}
        fp = _fp(smiles, value, project.settings.get("hrms_tolerance_ppm"))
        try:
            sp = hrms_species(mol, value)
        except chem.ChemError as e:
            out.append(Warning("hrms", "hrms", f"HRMS formula: {e}", "hrms:formula-unreadable", fp))
            sp = None
        result, problem = compound_hrms(project, compound)
        if problem and not problem.startswith("HRMS formula"):
            out.append(Warning("hrms", "hrms", problem, "hrms:ppm", fp))
        if sp is not None:
            hint = charged_adduct_hint(sp.info, value.get("adduct", ""))
            if hint:
                out.append(Warning("hrms", "hrms", hint, "hrms:charged", fp))
            if sp.manual and mol is not None:
                auto = hrms_species(mol, {**value, "formula": ""})
                if auto is not None and auto.info.formula != sp.info.formula:
                    out.append(Warning(
                        "hrms", "hrms",
                        f"The typed M formula ({sp.info.formula}) differs from the structure's "
                        f"({auto.info.formula}).", "hrms:formula-differs", fp))
        if ms_reports.report_items(compound):
            # A found m/z typed by hand that the report files don't back up (SPEC §13n.3a).
            message, match = ms_reports.differs_message(project, compound)
            if message and match is not None:
                out.append(Warning("hrms", "hrms", message, "hrms:report-differs",
                                   _fp(fp, match.file, round(match.mz, 6))))

    reports = compound.values.get("nmr") or [] if "nmr" in keys else []
    texts = {r.get("id", str(i)): _report_text(r) for i, r in enumerate(reports)}

    # 2./3. ¹³C signal count and ¹H integration (approximate by design)
    if mol is not None:
        carbons = chem.unique_carbons(mol)
        h_total, h_exch = chem.hydrogen_counts(mol)
        for i, r in enumerate(reports):
            rid, text = r.get("id", str(i)), texts.get(r.get("id", str(i)), "")
            if not text.strip():
                continue
            label = f" ({r['label']})" if r.get("label") else ""
            if r.get("nucleus") == "13C":
                n = nmr_parse.carbon_signal_count(text)
                if n and n != carbons:
                    out.append(Warning(
                        "c13", "nmr",
                        f"¹³C NMR{label} lists {n} signals; the structure has {carbons} "
                        "symmetry-distinct carbons. (Approximate: C–F coupling, rotamers and "
                        "overlapping signals can make these differ.)",
                        f"c13:{rid}", _fp(text, smiles)))
            if r.get("nucleus") == "1H":
                total = nmr_parse.proton_count(text)
                if total and abs(total - h_total) > 0.05:
                    note = ""
                    if h_exch and abs((h_total - total) - h_exch) <= 0.05:
                        note = (f" The difference matches the {h_exch} NH/OH hydrogen"
                                f"{'s' if h_exch != 1 else ''}, which often exchange and are "
                                "not seen.")
                    out.append(Warning(
                        "h1", "nmr",
                        f"¹H NMR{label} integrations add up to {total:g} H; the structure has "
                        f"{h_total} H.{note}", f"h1:{rid}", _fp(text, smiles)))

    # 4. Solvent / impurity peaks (Fulmer et al., Organometallics 2010, 29, 2176;
    #    editable per project in Project settings → NMR impurities)
    imp_table = impurities.project_table(project.settings)
    tol_h, tol_c = impurities.tolerances(project.settings)
    for i, r in enumerate(reports):
        rid, text = r.get("id", str(i)), texts.get(r.get("id", str(i)), "")
        solvent = nmr_parse.solvent(text)
        nucleus = r.get("nucleus", "")
        if not solvent or nucleus not in ("1H", "13C"):
            continue
        for peak in nmr_parse.peaks(text):
            if peak.end is not None:
                continue
            singlet = peak.multiplicity in ("s", "br s", "brs", "bs", "br. s")
            what = impurities.matches(solvent, nucleus, peak.shift, singlet, imp_table,
                                      tol_h, tol_c)
            if what:
                out.append(Warning(
                    "impurity", "nmr",
                    f"{chem_label(nucleus)} NMR: δ {peak.shift:g}"
                    f"{' (s)' if nucleus == '1H' else ''} is where {what} appears in "
                    f"{pretty_solvent(solvent)} — check it isn't a solvent or impurity peak.",
                    f"impurity:{rid}:{peak.shift:g}", _fp(text)))

    # 4b. Large coupling constants (SPEC §13l.4): H–H couplings are rarely over 20 Hz, so
    #     a ¹H J above that is usually a typo; H–F (and H–P) couplings can be that large.
    fp_atoms = sorted({"F", "P"} & set(chem.mol_info(mol).elements)) if mol is not None else []
    for i, r in enumerate(reports):
        if r.get("nucleus") != "1H":
            continue
        rid, text = r.get("id", str(i)), texts.get(r.get("id", str(i)), "")
        large = [c for c in nmr_parse.couplings(text) if c.value > LARGE_J_HZ]
        if large:
            out.append(Warning("coupling", "nmr", large_j_message(r, large, fp_atoms),
                               f"coupling:{rid}", _fp([c.written for c in large])))

    # 5. Yield consistency
    if "yield" in keys and mol is not None:
        y = compound.values.get("yield") or {}
        s = compound.values.get("scale") or {} if "scale" in keys else {}
        pct, mass, scale = (parse_number(str(y.get("percent", ""))),
                            parse_number(str(y.get("mass", ""))), parse_number(str(s.get("value", ""))))
        mass_f = MASS_TO_MG.get(str(y.get("mass_unit", "mg")), 1.0)
        scale_f = AMOUNT_TO_MMOL.get(str(s.get("unit", "mmol")), 1.0)
        if pct is not None and mass and scale:
            mw = chem.mol_info(mol).mw
            expected = mass * mass_f / (scale * scale_f * mw) * 100
            if abs(expected - pct) > 1.0:
                out.append(Warning(
                    "yield", "yield",
                    f"{y.get('mass')} {y.get('mass_unit', 'mg')} of this compound (MW {mw:.2f}) "
                    f"from {s.get('value')} {s.get('unit', 'mmol')} is {expected:.1f}%, not "
                    f"{pct:g}%. (If the yield is based on a different reagent or stoichiometry, "
                    "dismiss this warning.)", "yield", _fp(y, s, smiles)))

    # 6. Spectra present
    if "nmr" in keys and "spectra" in keys:
        reported = {r.get("nucleus") for r in reports
                    if texts.get(r.get("id", ""), _report_text(r)).strip()}
        attached = {i.get("type") for i in compound.values.get("spectra") or []}
        fp = _fp(sorted(reported), sorted(attached))
        for n in sorted(reported & set(F.SPECTRUM_TYPES)):
            if n not in attached:
                out.append(Warning("spectra", "spectra",
                                   f"There is a {chem_label(n)} NMR report but no {chem_label(n)} "
                                   "spectrum attached.", f"spectra:missing:{n}", fp))
        for n in sorted(attached & set(F.NUCLEI)):
            if n not in reported:
                out.append(Warning("spectra", "nmr",
                                   f"A {chem_label(n)} spectrum is attached but there is no "
                                   f"{chem_label(n)} NMR report.", f"spectra:extra:{n}", fp))

    # 7. NMR frequency consistency (e.g. ¹³C ≈ ¹H × 0.2515: 400 → 101 MHz)
    h_freqs = [nmr_parse.frequency(texts.get(r.get("id", str(i)), ""))
               for i, r in enumerate(reports) if r.get("nucleus") == "1H"]
    h_freqs = [f for f in h_freqs if f]
    if h_freqs:
        for i, r in enumerate(reports):
            n = r.get("nucleus", "")
            text = texts.get(r.get("id", str(i)), "")
            f = nmr_parse.frequency(text)
            if n not in NMR_RATIOS or not f:
                continue
            expected = [h * NMR_RATIOS[n] for h in h_freqs]
            if all(abs(f - e) / e > 0.03 for e in expected):
                out.append(Warning(
                    "frequency", "nmr",
                    f"{chem_label(n)} NMR at {f:g} MHz doesn't match ¹H at "
                    f"{', '.join(f'{h:g}' for h in sorted(set(h_freqs)))} MHz "
                    f"(expected ≈ {', '.join(f'{e:.0f}' for e in sorted(set(expected)))} MHz).",
                    f"frequency:{r.get('id', i)}", _fp(text, h_freqs)))

    # 8. Missing heteroatom NMR
    if mol is not None and "nmr" in keys:
        have = {r.get("nucleus") for r in reports if texts.get(r.get("id", ""), _report_text(r)).strip()}
        for n in project.heteroatom_nuclei(chem.mol_info(mol)):
            if n not in have:
                element = n.lstrip("0123456789")
                out.append(Warning("heteroatom", "nmr",
                                   f"The structure contains {element} but there is no "
                                   f"{chem_label(n)} NMR data.", f"heteroatom:{n}",
                                   _fp(smiles, sorted(have))))

    # 9. Broken compound mentions and citations (SPEC §7.5, §9.5)
    from . import links, procedures
    seen: set[str] = set()
    for place in links.compound_texts(project, compound):
        gone, missing = links.broken_links(project, parse_markup(place.text))
        for cid in gone:
            w = Warning("mention", place.field, "This text " + _gone_text(project, cid),
                        f"mention:{place.field}:{cid}", _fp(cid))
            if w.key not in seen:
                seen.add(w.key)
                out.append(w)
        for rid in missing:
            w = Warning("citation", place.field,
                        "This cites a reference that is no longer in the References list; it "
                        "will be left out of the SI.", f"citation:{place.field}:{rid}", _fp(rid))
            if w.key not in seen:
                seen.add(w.key)
                out.append(w)

    # General procedure chosen on the Procedure field
    choice = project.gp_choice(compound)
    if "procedure" in keys and choice.get("gp"):
        gp = project.compound_gp(compound)
        if gp is None:
            out.append(Warning("gp", "procedure",
                               "The general procedure chosen for this compound no longer exists "
                               "(it may have been deleted). Choose another, or restore it from "
                               "the Trash.", "gp:missing", _fp(choice.get("gp"))))
        else:
            typed = choice.get("values") or {}
            auto = procedures.auto_values(project, compound, gp)
            for name in procedures.missing_values(gp.get("sentence", ""), typed, auto):
                out.append(Warning("gp", "procedure",
                                   f"“{gp.get('name', 'The general procedure')}” needs a value "
                                   f"for {{{name}}} (it would be printed as [{name}]).",
                                   f"gp:value:{procedures.key(name)}",
                                   _fp(gp.get("sentence", ""), typed)))
            # The {scale} box and the Reaction scale field disagree (SPEC §13n.9).
            conflict = procedures.scale_conflict(project, compound)
            if conflict:
                out.append(Warning("gp", "procedure", conflict, "gp:scale-differs",
                                   _fp(typed, compound.values.get("scale"))))
    return out


def _a_kind(item_id: str) -> str:
    from .markup import mention_kind
    return {"compound": "a compound", "figure": "a figure", "table": "a table",
            "scheme": "a scheme"}[mention_kind(item_id)]


def _gone_text(project, item_id: str) -> str:
    """"mentions … (printed as “??”) …" for a mention whose target has no label: deleted,
    or a scheme printed without its label and caption (the default, SPEC §13p.1)."""
    from .markup import mention_kind
    if mention_kind(item_id) == "scheme":
        try:
            scheme = project.scheme(item_id)
        except KeyError:
            scheme = None
        if scheme is not None:
            return (f"mentions scheme “{scheme.name}”, which is printed without a label "
                    "(printed as “??”). Tick “Label and caption” in the scheme's Layout…, or "
                    "remove the mention.")
    return (f"mentions {_a_kind(item_id)} that has been deleted (printed as “??”). Restore "
            "it from the Trash, or remove the mention.")


# ---------------------------------------------------------------------- project-wide checks

@dataclass
class ProjectWarning:
    """A warning that is not about one compound: two items with the same label, a
    mention in a section's text whose target was deleted (SPEC §13k.5a), or a figure or
    table in a text whose picture or table file is missing (SPEC §13k.5)."""
    check: str                  # "label", "mention", "citation", "object" or "gp_name"
    message: str
    place: tuple[str, str]      # the tree item to open: ("front", "general"), ("section",
                                # id), ("scheme", id) or ("compound", id)
    field: str = ""             # for a compound: the field to scroll to


def project_warnings(project, labels=None) -> list[ProjectWarning]:
    """Warnings about the project as a whole (shown in their own group in the Warnings
    panel). Compound texts' broken mentions are compound warnings (check 9) instead."""
    from . import links
    from .labels import compute
    labels = labels or compute(project)
    out: list[ProjectWarning] = []
    for label, items in labels.duplicates.items():
        for item in items:
            others = "; ".join(o.where for o in items if o is not item)
            out.append(ProjectWarning(
                "label", f"“{item.label}” ({item.where}) has the same label as {others}. Give "
                         "one of them its own label, or let both be numbered automatically.",
                item.place, "files" if item.place[0] == "compound" else ""))
    for place in links.all_texts(project, compounds=False):
        if place.kind not in ("front", "section", "gp", "gp_sentence", "table", "scheme"):
            continue
        gone, missing = links.broken_links(project, parse_markup(place.text), labels)
        target = _text_place(project, place)
        for item_id in gone:
            out.append(ProjectWarning(
                "mention", f"{place.where}: " + _gone_text(project, item_id), target))
        for _rid in missing:   # (the same check as a compound text's missing reference)
            out.append(ProjectWarning(
                "citation", f"{place.where}: cites a reference that is no longer in the "
                           "References list; it will be left out of the SI.", target))
    out += missing_objects(project)
    out += duplicate_gp_names(project)
    return out


def duplicate_gp_names(project) -> list[ProjectWarning]:
    """Two general procedures with the same name (ignoring capitals and spaces) anywhere
    in the project (SPEC §13n.8): the SI would print two procedures with the same
    heading, and compounds would choose between two identical names."""
    from . import procedures
    out: list[ProjectWarning] = []
    for group in procedures.duplicate_names(project).values():
        for section, gp in group:
            others = "; ".join(f"“{o.get('name', '')}” in “{s.name}”"
                               for s, o in group if o is not gp)
            out.append(ProjectWarning(
                "gp_name", f"General procedure “{gp.get('name', '')}” (in “{section.name}”) has "
                           f"the same name as {others}. Rename one of them (Data section ▸ "
                           "General procedures).", ("section", section.id)))
    return out


def missing_objects(project) -> list[ProjectWarning]:
    """Figures and tables in a custom section's (or General Information's) text whose
    picture or table file is no longer in the project: they are left out of the SI."""
    from .markup import objects as markup_objects
    out: list[ProjectWarning] = []
    for owner in project.text_owners():
        try:
            text = project.read_owner_text(owner)
        except (OSError, ValueError):
            continue
        place = ("front", "general") if owner.key == "general" else ("section", owner.key)
        for kind, item_id in markup_objects(parse_markup(text)):
            if kind == "figure":
                path = project.figure_path(owner, item_id)
                if path is None or not path.exists():
                    out.append(ProjectWarning(
                        "object", f"{owner.name}: a figure's picture is missing, so the figure "
                                  "is left out of the SI. Restore it from the Trash if it was "
                                  "removed, or replace the picture (right-click it).", place))
            elif kind == "table" and not project.table_path(owner, item_id).exists():
                out.append(ProjectWarning(
                    "object", f"{owner.name}: a table's file is missing, so the table is left "
                              "out of the SI. Restore it from the Trash if it was removed.",
                    place))
    return out


def _text_place(project, place) -> tuple[str, str]:
    if place.kind == "front":
        return ("front", place.owner_id)
    if place.kind == "scheme":   # (the scheme editor's conditions and labels)
        return ("scheme", place.owner_id)
    if place.kind in ("section", "table"):
        return ("front", "general") if place.owner_id == "general" else ("section",
                                                                          place.owner_id)
    found = project.general_procedure(place.owner_id)   # gp, gp_sentence
    return ("section", found[0].id) if found else ("", "")


LARGE_J_HZ = 20.0   # ¹H couplings above this are flagged (SPEC §13l.4)


def hetero_coupling(which: str) -> str:
    """"F" or "P" when a coupling constant is labelled as an H–F or H–P coupling ("H–F",
    "HF", "(H,F)", "H-P", "FH"…, as nmr_parse.Coupling.which), else ""."""
    letters = re.sub(r"[^A-Za-z]", "", which).upper()
    for atom in ("F", "P"):
        if letters in ("H" + atom, atom + "H"):
            return atom
    return ""


def large_j_message(report: dict, large: list, fp_atoms: list[str]) -> str:
    """The warning for coupling constants over LARGE_J_HZ in one ¹H NMR report. H–F and
    H–P couplings can be this large: values labelled as one (JH–F, J(H,F), JHP…) "may be
    correct" whatever the structure; when the structure contains F or P (`fp_atoms`), all
    of them may be, which the message says first. Otherwise: check for a typo."""
    label = f" ({report['label']})" if report.get("label") else ""

    def listed(values: list) -> str:   # "J = 26.5 Hz, 21,4 Hz, JH–F = 47.6 Hz"
        plain_ = [f"{c.written} Hz" for c in values if not hetero_coupling(c.which)]
        out = [f"J = {', '.join(plain_)}"] if plain_ else []
        out += [f"J{c.which} = {c.written} Hz" for c in values if hetero_coupling(c.which)]
        return ", ".join(out)

    def be(values: list) -> str:
        return "is" if len(values) == 1 else "are"

    def it(values: list) -> str:
        return "it" if len(values) == 1 else "they"

    head = f"¹H NMR{label}: "
    limit = f"larger than {LARGE_J_HZ:g} Hz"
    hetero = [c for c in large if hetero_coupling(c.which)]
    plain = [c for c in large if not hetero_coupling(c.which)]
    if fp_atoms:
        atoms = list(dict.fromkeys([*fp_atoms, *(hetero_coupling(c.which) for c in hetero)]))
        couplings_ = " or ".join(f"H–{a}" for a in atoms)
        text = (f"{head}{listed(large)} {be(large)} {limit}, which may be correct: the "
                f"structure contains {' and '.join(fp_atoms)}, and {couplings_} couplings "
                "can be this large.")
        if plain:   # (not labelled as H–F/H–P: it could still be a mistyped H–H coupling)
            one = len(plain) == 1
            who = (listed(plain) if hetero else "it" if one else "they")
            text += (f" If {who} {'is an H–H coupling' if one else 'are H–H couplings'}, "
                     "check for a typo.")
        return text
    parts = []
    if plain:
        parts.append(f"{listed(plain)} {be(plain)} {limit}. H–H coupling constants are rarely "
                     f"this large: check {'it' if len(plain) == 1 else 'them'} for a typo.")
    if hetero:
        kinds = " and ".join(f"H–{a}" for a in dict.fromkeys(hetero_coupling(c.which)
                                                             for c in hetero))
        one = len(hetero) == 1
        parts.append(f"{listed(hetero)} {be(hetero)} {limit}, which may be correct: "
                     f"{'it is labelled as an' if one else 'they are labelled as'} {kinds} "
                     f"coupling{'' if one else 's'}, and these can be this large.")
    return head + " ".join(parts)


def pretty_solvent(name: str) -> str:
    """"CDCl3" -> "CDCl₃", "DMSO-d6" -> "DMSO-d₆" for messages."""
    sub = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    return re.sub(r"(?<=[A-Za-z])(\d+)", lambda m: m.group(1).translate(sub), name)


def chem_label(nucleus: str) -> str:
    return F.nucleus_label(nucleus)


def is_dismissed(compound, w: Warning) -> bool:
    d = (compound.data.get("dismissed") or {}).get(w.key)
    return bool(d) and d.get("fingerprint") == w.fingerprint


def active_warnings(project, compound, id_report: IdReport) -> list[Warning]:
    return [w for w in all_warnings(project, compound, id_report) if not is_dismissed(compound, w)]


def dismissed_warnings(project, compound, id_report: IdReport) -> list[tuple[Warning, dict]]:
    out = []
    for w in all_warnings(project, compound, id_report):
        if is_dismissed(compound, w):
            out.append((w, compound.data["dismissed"][w.key]))
    return out


def compound_warnings(project, compound, id_report: IdReport) -> dict[str, list[str]]:
    """Active (not dismissed) warning messages for one compound, by field key."""
    out: dict[str, list[str]] = {}
    for w in active_warnings(project, compound, id_report):
        out.setdefault(w.field, []).append(w.message)
    return out