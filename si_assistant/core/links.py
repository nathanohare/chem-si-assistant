"""Mentions and citations across the project (SPEC §7.5, §9.5, §13k.5a).

Mentions (@{c-…}) and citations (@cite{r-…}) are stored by permanent internal ID inside
the .txt files. A mention can point to a compound (shown as its current ID, "3a") or to a
figure, table or scheme page (shown as its label, "Table S2"); the ID's prefix says which
(markup.mention_kind). This module finds every stored text, shows links the way the
editor does, and reports broken ones (check 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from . import references as refs
from .markup import Paragraph, Run, citations, mention_kind, mentions, parse

BROKEN = "??"


@dataclass
class TextPlace:
    """One stored text: where it is and its markup."""
    kind: str            # "compound" | "section" | "gp" | "gp_sentence" | "gp_value" |
                         # "citations" | "front" (owner_id "general" or "cover")
    owner_id: str        # compound, section or GP id
    field: str           # compound field key ("" otherwise)
    where: str           # for messages, e.g. "3a ▸ Experimental procedure"
    text: str


def compound_texts(project, compound, label: str = "") -> Iterator[TextPlace]:
    label = label or compound.name or "compound"
    for defn in project.field_defs(compound):
        if defn.kind == "richtext":
            yield TextPlace("compound", compound.id, defn.key, f"{label} ▸ {defn.label}",
                            project.read_compound_text(compound, defn.key))
        elif defn.kind == "citations":
            ids = ((compound.values.get(defn.key) or {}).get("refs") or [])
            if ids:
                yield TextPlace("citations", compound.id, defn.key, f"{label} ▸ {defn.label}",
                                "@cite{" + ",".join(ids) + "}")
    gp = project.compound_gp(compound)
    if gp is not None:
        from .procedures import key, placeholders
        used = {key(n) for n in placeholders(gp.get("sentence", ""))}
        for name, value in (project.gp_choice(compound).get("values") or {}).items():
            if key(name) in used:  # values kept for another procedure are not printed
                yield TextPlace("gp_value", compound.id, "procedure",
                                f"{label} ▸ Experimental procedure ▸ {{{name}}}", str(value))


def table_texts(project, owner, text: str, where: str) -> Iterator[TextPlace]:
    """The cells and footnotes of each table in an owner's text (kind "table": owner_id is
    the owner's key, field the table's ID)."""
    for p in parse(text):
        if p.table:
            table = project.read_table(owner, p.table)
            if table is None:
                continue
            cells = [cell.text for _r, _c, cell in table.cells() if cell.text.strip()]
            body = "\n".join(cells + table.footnotes)
            if body:
                yield TextPlace("table", owner.key, p.table, f"{where} ▸ table", body)


def all_texts(project, compounds: bool = True) -> Iterator[TextPlace]:
    """Every stored text; with compounds=False, not the compounds' own texts."""
    report = project.id_report() if compounds else None
    for key, where in (("cover", "Front matter ▸ Cover page"),
                       ("general", "Front matter ▸ General Information")):
        text = project.read_front_text(key)
        if text:
            yield TextPlace("front", key, "", where, text)
            if key == "general":
                yield from table_texts(project, project.text_owner(key), text, where)
    for section in project.sections:
        if section.type == "text":
            text = project.read_section_text(section)
            yield TextPlace("section", section.id, "", section.name, text)
            yield from table_texts(project, project.text_owner(section.id), text, section.name)
        if section.type != "data":
            continue
        for gp in project.general_procedures(section):
            yield TextPlace("gp", gp["id"], "", f"{section.name} ▸ {gp.get('name', '')}",
                            project.read_gp_text(gp["id"]))
            yield TextPlace("gp_sentence", gp["id"], "",
                            f"{section.name} ▸ {gp.get('name', '')} (compound sentence)",
                            gp.get("sentence", ""))
        for c in section.compounds if compounds else []:
            yield from compound_texts(project, c, report.display_id(c.id))


def display_runs(project, runs: list[Run], report=None, labels=None) -> list[Run]:
    """Links as the editor shows them: compound mentions → current ID (bold), figure/table/
    scheme mentions → their label, citations → [Author year]."""
    out = []
    for r in runs:
        if r.mention:
            compound = mention_kind(r.mention) == "compound"
            if compound and report is None:
                report = project.id_report()   # (only worked out when needed)
            out.append(Run(mention_label(project, r.mention, report, labels),
                           compound or r.bold, r.italic, r.valign, r.ref))
        elif r.ref:
            out.append(Run(citation_label(project, r.citations), r.bold, r.italic, r.valign,
                           r.ref))
        else:
            out.append(r)
    return out


def mention_label(project, item_id: str, report=None, labels=None) -> str:
    """What a mention shows: a compound's current ID, or a figure/table/scheme label;
    "??" if the item no longer exists."""
    if mention_kind(item_id) != "compound":
        if labels is None:
            from .labels import compute
            labels = compute(project)
        return labels.label(item_id) or BROKEN
    try:
        project.compound(item_id)
    except KeyError:
        return BROKEN
    return (report or project.id_report()).display_id(item_id)


def target_exists(project, item_id: str, labels=None) -> bool:
    """Does a mentioned compound, figure, table or scheme still exist?"""
    if mention_kind(item_id) == "compound":
        return compound_exists(project, item_id)
    if labels is None:
        from .labels import compute
        labels = compute(project)
    return item_id in labels.by_id


def citation_label(project, ids: list[str]) -> str:
    labels = []
    for i in ids:
        ref = project.reference(i)
        labels.append(refs.short_label(ref) if ref else "missing reference")
    return "[" + "; ".join(labels) + "]"


def plain_text(project, text: str) -> str:
    paragraphs = parse(text)
    report = project.id_report() if any(mention_kind(m) == "compound"
                                        for m in mentions(paragraphs)) else None
    labels = None
    if any(mention_kind(m) != "compound" for m in mentions(paragraphs)):
        from .labels import compute
        labels = compute(project)
    return " ".join("".join(r.text for r in display_runs(project, p.runs, report, labels))
                    for p in paragraphs).strip()


def compound_exists(project, compound_id: str) -> bool:
    try:
        project.compound(compound_id)
        return True
    except KeyError:
        return False


def broken_links(project, paragraphs: list[Paragraph],
                 labels=None) -> tuple[list[str], list[str]]:
    """(mentioned IDs that no longer exist, cited reference IDs not in the list)."""
    found = list(dict.fromkeys(mentions(paragraphs)))
    if labels is None and any(mention_kind(m) != "compound" for m in found):
        from .labels import compute
        labels = compute(project)
    gone = [m for m in found if not target_exists(project, m, labels)]
    missing = [c for c in dict.fromkeys(citations(paragraphs)) if project.reference(c) is None]
    return gone, missing


def citation_uses(project) -> dict[str, list[str]]:
    """{reference id: [where it is cited]}."""
    uses: dict[str, list[str]] = {}
    for place in all_texts(project):
        for c in dict.fromkeys(citations(parse(place.text))):
            uses.setdefault(c, []).append(place.where)
    return uses


def mention_uses(project, compound_id: str) -> list[str]:
    """Where a compound is mentioned (e.g. to warn before deleting it)."""
    return [place.where for place in all_texts(project)
            if compound_id in mentions(parse(place.text))]
