"""General procedures (SPEC §6.3.3, §13j).

A general procedure (GP) belongs to a Data section: a name ("General Procedure A"), an
optional title ("Synthesis of enamides"), its text (printed once, at the start of the
section) and a *compound sentence*: the template printed in the block of every compound
that uses it, e.g.

    Prepared according to {GP} from {starting material} ({scale}).

Words in {braces} are placeholders, filled in per compound. Some fill themselves in
(GP, scale, yield, product, purification) but every one can be typed by hand on the
compound page.
"""

from __future__ import annotations

import re

from .markup import MENTION, Run, parse

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
# Placeholders the program fills in (the compound page can still override them).
AUTO = {"gp": "the general procedure's name",
        "scale": "from the Reaction scale field, e.g. 0.20 mmol",
        "yield": "from the Yield field, e.g. 92%",
        "product": "this compound's ID",
        "purification": "from the Purification field, e.g. flash column chromatography "
                        "(SiO₂, 10% EtOAc/hexanes)"}
PURIFICATION_START = "The crude product was purified by "   # the automatic sentence


def key(name: str) -> str:
    """Placeholders match whatever their case and spacing: {Starting Material} = {starting material}."""
    return " ".join(name.lower().split())


def placeholders(sentence: str) -> list[str]:
    """Placeholder names in order of appearance, without repeats (as written)."""
    seen, out = set(), []
    for p in parse(sentence):
        for r in p.runs:
            if r.ref:
                continue
            for m in PLACEHOLDER.finditer(r.text):
                k = key(m.group(1))
                if k not in seen:
                    seen.add(k)
                    out.append(m.group(1).strip())
    return out


def auto_values(project, compound, gp: dict) -> dict[str, list[Run]]:
    """What the automatic placeholders become for this compound."""
    out: dict[str, list[Run]] = {"gp": [Run(gp.get("name", "") or "the general procedure")],
                                 "product": [Run("", ref=MENTION + compound.id)]}
    scale = compound.values.get("scale") or {}
    if "scale" in compound.field_keys and str(scale.get("value", "")).strip():
        out["scale"] = [Run(f"{str(scale['value']).strip()} {scale.get('unit', 'mmol')}".strip())]
    y = compound.values.get("yield") or {}
    if "yield" in compound.field_keys and str(y.get("percent", "")).strip():
        out["yield"] = [Run(f"{str(y['percent']).strip()}%")]
    purification = purification_text(compound)
    if purification:
        out["purification"] = [Run(purification)]
    return out


# ---------------------------------------------------------------------- Purification (§13q.2)

def _purification_value(compound) -> dict[str, str]:
    if "purification" not in compound.field_keys:
        return {}
    value = compound.values.get("purification")
    return {k: str(v).strip() for k, v in value.items()} if isinstance(value, dict) else {}


def purification_text(compound) -> str:
    """"flash column chromatography (SiO₂, 10% EtOAc/hexanes)" from the Purification field
    ("" without a method): what {purification} becomes."""
    return purification_text_of(_purification_value(compound))


def purification_text_of(value: dict) -> str:
    """purification_text for the field's value {"method", "phase", "eluent", …}."""
    v = {k: str(x).strip() for k, x in value.items()}
    if not v.get("method"):
        return ""
    inside = ", ".join(x for x in (v.get("phase", ""), v.get("eluent", "")) if x)
    return v["method"] + (f" ({inside})" if inside else "")


def automatic_purification_sentence(compound) -> str:
    text = purification_text(compound)
    return f"{PURIFICATION_START}{text}." if text else ""


def purification_sentence(compound) -> str:
    """The sentence printed at the end of the experimental procedure: the one typed in
    "Printed as", else the automatic one ("" when there is nothing to print)."""
    typed = _purification_value(compound).get("sentence", "")
    return typed or automatic_purification_sentence(compound)


def uses_purification(gp: dict | None) -> bool:
    """Does this general procedure's compound sentence place {purification} itself?"""
    return gp is not None and "purification" in [
        key(p) for p in placeholders(gp.get("sentence", "") or "")]


AUTO_WINS = ("scale",)   # the field's value is printed even if something is typed in the box


# ---------------------------------------------------------------------- duplicate names (§13n.8)

def name_key(name: str) -> str:
    """GP names are compared ignoring capitals and spaces: "General Procedure A" =
    "general procedure  a" = "GeneralProcedureA"."""
    return "".join(str(name or "").casefold().split())


def duplicate_names(project) -> dict[str, list[tuple]]:
    """{name key: [(section, gp), …]} for every general-procedure name used more than once
    anywhere in the project (empty names are not counted)."""
    groups: dict[str, list[tuple]] = {}
    for section, gp in project.all_general_procedures():
        k = name_key(gp.get("name", ""))
        if k:
            groups.setdefault(k, []).append((section, gp))
    return {k: v for k, v in groups.items() if len(v) > 1}


def duplicate_of(project, gp_id: str) -> list[tuple]:
    """The other (section, gp) pairs with the same name as GP `gp_id` (empty if none)."""
    for group in duplicate_names(project).values():
        if any(gp.get("id") == gp_id for _s, gp in group):
            return [(s, gp) for s, gp in group if gp.get("id") != gp_id]
    return []


# ---------------------------------------------------------------------- {scale} → Reaction scale (§13n.9)

_SCALE = re.compile(r"^\s*(\d*[.,]?\d+)\s*(mmol|[µμu]mol|mol)\s*\.?\s*$", re.IGNORECASE)
_UNITS = {"mmol": "mmol", "µmol": "µmol", "μmol": "µmol", "umol": "µmol", "mol": "mol"}
_TO_MMOL = {"mmol": 1.0, "µmol": 0.001, "mol": 1000.0}


def parse_scale(text: str) -> tuple[str, str] | None:
    """"0.20 mmol" → ("0.20", "mmol"); "0.2mmol", "20 µmol", "20 umol", "1.5 mol" too.
    The number is kept as typed (its significant figures; a decimal comma becomes a
    point). None for anything else, e.g. "0.20 mmol of 1a" or "1.0 g"."""
    m = _SCALE.match(str(text or ""))
    if not m:
        return None
    unit = _UNITS.get(m.group(2).lower().replace("μ", "µ"))
    if unit is None:
        return None
    number = m.group(1).replace(",", ".")
    if number.startswith("."):
        number = "0" + number
    return number, unit


def scale_mmol(value: str, unit: str) -> float | None:
    try:
        return float(str(value).replace(",", ".")) * _TO_MMOL.get(unit or "mmol", 1.0)
    except ValueError:
        return None


def same_scale(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """The same amount, whatever its unit or zeros: 0.20 mmol = 0.2 mmol = 200 µmol."""
    x, y = scale_mmol(*a), scale_mmol(*b)
    return x is not None and y is not None and abs(x - y) <= 1e-9 * max(abs(x), abs(y), 1e-12)


def scale_to_fill(typed: str, field: tuple[str, str],
                  filled_before: tuple[str, str] | None) -> tuple[str, str] | None:
    """What the Reaction scale field should become when "typed" is typed in a GP's
    {scale} box: (value, unit), or None to leave it alone. `field` is the field's (value,
    unit) now; `filled_before` what this filled in last time (None if never). The field
    is filled when it is empty, or still holds what was filled in before; a value the
    user typed in the field is never changed (a warning says they differ instead)."""
    parsed = parse_scale(typed)
    if parsed is None:
        return None
    value, unit = (str(field[0] or "").strip(), str(field[1] or "mmol").strip() or "mmol")
    empty = not value
    ours = filled_before is not None and (value, unit) == tuple(filled_before)
    if not (empty or ours) or (value, unit) == parsed:
        return None
    return parsed


def scale_conflict(project, compound) -> str:
    """A warning when a GP's {scale} box and the Reaction scale field say different
    things (the field is what is printed); "" if they agree or either is empty."""
    gp = project.compound_gp(compound)
    if gp is None or "scale" not in compound.field_keys:
        return ""
    typed = next((str(v).strip() for n, v in (project.gp_choice(compound).get("values")
                                              or {}).items() if key(n) == "scale"), "")
    if not typed or "scale" not in [key(p) for p in placeholders(gp.get("sentence", ""))]:
        return ""
    field = compound.values.get("scale") or {}
    value, unit = str(field.get("value", "")).strip(), str(field.get("unit", "mmol") or "mmol")
    if not value:
        return ""
    plain = " ".join(r.text for r in value_runs(typed)).strip()
    parsed = parse_scale(plain)
    if parsed is not None and same_scale(parsed, (value, unit)):
        return ""
    return (f"The {{scale}} box of “{gp.get('name', 'the general procedure')}” says "
            f"“{plain}”, but the Reaction scale field says {value} {unit}. The Reaction scale "
            "field is printed: change whichever is wrong.")


def value_runs(text: str) -> list[Run]:
    """A typed placeholder value (stored markup, possibly with mentions) as runs."""
    runs: list[Run] = []
    for i, p in enumerate(parse(text)):
        if i and p.runs:
            runs.append(Run(" "))
        runs.extend(p.runs)
    return runs


def fill(sentence: str, typed: dict[str, str], auto: dict[str, list[Run]],
         problems: list[str] | None = None) -> list[Run]:
    """The compound sentence with its placeholders filled in. Typed values win over
    automatic ones, except {scale}: once the compound's Reaction scale field is filled,
    that is printed (SPEC §13n.9; a typed {scale} fills the field, and a warning says if
    the two differ). A placeholder with no value is printed as [name] and reported."""
    typed = {key(k): v for k, v in typed.items() if str(v).strip()}
    out: list[Run] = []
    for p in parse(sentence):
        if out and p.runs:
            out.append(Run(" "))
        for r in p.runs:
            if r.ref:
                out.append(r)
                continue
            pos = 0
            for m in PLACEHOLDER.finditer(r.text):
                if m.start() > pos:
                    out.append(r.but(text=r.text[pos:m.start()]))
                k = key(m.group(1))
                if k in AUTO_WINS and k in auto:
                    value = auto[k]
                elif k in typed:
                    value = value_runs(typed[k])
                elif k in auto:
                    value = auto[k]
                else:
                    value = [Run(f"[{m.group(1).strip()}]")]
                    if problems is not None:
                        problems.append(f"no value for {{{m.group(1).strip()}}}")
                for v in value:
                    out.append(Run(v.text, v.bold or r.bold, v.italic or r.italic,
                                   v.valign if v.valign != "normal" else r.valign, v.ref,
                                   underline=v.underline or r.underline))
                pos = m.end()
            if pos < len(r.text):
                out.append(r.but(text=r.text[pos:]))
    return out


def missing_values(sentence: str, typed: dict[str, str], auto: dict[str, list[Run]]) -> list[str]:
    problems: list[str] = []
    fill(sentence, typed, auto, problems)
    return [p.split("{", 1)[1].rstrip("}") for p in problems]
