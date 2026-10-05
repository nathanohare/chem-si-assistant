"""General procedures (SPEC §6.3.3, §13j).

A general procedure (GP) belongs to a Data section: a name ("General Procedure A"), an
optional title ("Synthesis of enamides"), its text (printed once, at the start of the
section) and a *compound sentence*: the template printed in the block of every compound
that uses it, e.g.

    Prepared according to {GP} from {starting material} ({scale}).

Words in {braces} are placeholders, filled in per compound. Some fill themselves in
(GP, scale, yield, product) but every one can be typed by hand on the compound page.
"""

from __future__ import annotations

import re

from .markup import MENTION, Run, parse

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
# Placeholders the program fills in (the compound page can still override them).
AUTO = {"gp": "the general procedure's name",
        "scale": "from the Reaction scale field, e.g. 0.20 mmol",
        "yield": "from the Yield field, e.g. 92%",
        "product": "this compound's ID"}


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
    return out


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
    automatic ones; a placeholder with no value is printed as [name] and reported."""
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
                    out.append(Run(r.text[pos:m.start()], r.bold, r.italic, r.valign))
                k = key(m.group(1))
                if k in typed:
                    value = value_runs(typed[k])
                elif k in auto:
                    value = auto[k]
                else:
                    value = [Run(f"[{m.group(1).strip()}]")]
                    if problems is not None:
                        problems.append(f"no value for {{{m.group(1).strip()}}}")
                for v in value:
                    out.append(Run(v.text, v.bold or r.bold, v.italic or r.italic,
                                   v.valign if v.valign != "normal" else r.valign, v.ref))
                pos = m.end()
            if pos < len(r.text):
                out.append(Run(r.text[pos:], r.bold, r.italic, r.valign))
    return out


def missing_values(sentence: str, typed: dict[str, str], auto: dict[str, list[Run]]) -> list[str]:
    problems: list[str] = []
    fill(sentence, typed, auto, problems)
    return [p.split("{", 1)[1].rstrip("}") for p in problems]
