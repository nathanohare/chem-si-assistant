"""Turning stored text into Typst, and the automatic text formatting (SPEC §7.3).

Text is handled as paragraphs of runs (core/markup.py): each run is plain text
with bold / italic / superscript / subscript. Formatting helpers split plain
runs further (e.g. "CDCl3" -> "CDCl" + subscript "3"); then every run is written
to Typst as a quoted string, so no character in the user's text can ever be
mistaken for Typst syntax.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from ..core import chemtext
from ..core.markup import NORMAL, SUB, SUPER, Paragraph, Run, mention_kind

# ---------------------------------------------------------------------- Typst output


@dataclass(frozen=True)
class LinkedRun(Run):
    """A run printed as a link to a place in the PDF (SPEC §13l.7): `target` is a Typst
    label without its <>, e.g. the "¹H NMR" of a peak report linking to the spectrum.
    Only made while compiling; it is never stored."""
    target: str = ""


def link_target(run: Run) -> str:
    return getattr(run, "target", "")


def linked(runs: list[Run], n: int, target: str) -> list[Run]:
    """`runs` with their first `n` characters made a link to `target` (a run is split
    where the link ends)."""
    out, left = [], n
    for r in runs:
        if left <= 0 or r.ref:
            out.append(r)
            continue
        head, tail = r.text[:left], r.text[left:]
        out.append(LinkedRun(head, r.bold, r.italic, r.valign, target=target))
        if tail:
            out.append(Run(tail, r.bold, r.italic, r.valign))
        left -= len(head)
    return out


def typst_label(text: str) -> str:
    """`text` made usable as a Typst label: letters, digits, "-" and "_" only."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)


def typst_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_to_typst(run: Run) -> str:
    if run.ref:
        # Mentions are resolved to text before this (resolve_links); citations become
        # Typst citations, numbered and formatted by the journal's citation style.
        return "".join(f"#cite(label({typst_string(c)}))" for c in run.citations)
    out = "#" + typst_string(run.text)
    if run.valign == SUPER:
        out = f"#super[{out}]"
    elif run.valign == SUB:
        out = f"#sub[{out}]"
    if run.italic:
        out = f"#emph[{out}]"
    if run.bold:
        out = f"#strong[{out}]"
    return out


def runs_to_typst(runs: list[Run]) -> str:
    """Runs → Typst. Runs next to each other that link to the same place become one
    link, `#si-link(<target>)[…]` (defined by compile/document.py: a link only when the
    target is in the document)."""
    out: list[str] = []
    target, group = "", []
    for r in [r for r in runs if r.text or r.ref] + [None]:
        t = link_target(r) if r is not None else ""
        if group and (r is None or t != target):
            out.append(f"#si-link(<{target}>)[{''.join(group)}]")
            group = []
        if r is None:
            break
        if t:
            target = t
            group.append(run_to_typst(r))
        else:
            out.append(run_to_typst(r))
    return "".join(out)


BROKEN_MENTION = "??"


def resolve_links(runs: list[Run], mention_text, known_refs: set[str] | None,
                  problems: list[str] | None = None) -> list[Run]:
    """Mentions become their compound's current ID in bold (SPEC §7.5), or a figure's,
    table's or scheme's label ("Table S2", not bold, §13k.5a); citations keep only
    references that exist. `mention_text(item_id) -> str | None` (None = the item no
    longer exists, printed as "??"). Problems are appended to `problems`."""
    out: list[Run] = []
    for r in runs:
        if r.mention:
            kind = mention_kind(r.mention)
            text = mention_text(r.mention)
            if text is None:
                text = BROKEN_MENTION
                if problems is not None:
                    problems.append(f"A mention points to a {kind} that no longer exists "
                                    "(printed as “??”).")
            out.append(Run(text, kind == "compound" or r.bold, r.italic, r.valign))
        elif r.ref:
            ids = r.citations
            if known_refs is not None:
                missing = [c for c in ids if c not in known_refs]
                ids = [c for c in ids if c in known_refs]
                if missing and problems is not None:
                    problems.append("A citation points to a reference that is no longer in "
                                    "the References list; it was left out.")
            if ids:
                out.append(Run("", r.bold, r.italic, r.valign, "cite:" + ",".join(ids)))
        else:
            out.append(r)
    return out


def paragraphs_to_typst(paragraphs: list[Paragraph], heading_offset: int = 1,
                        outlined_levels: int = 2) -> str:
    """Paragraphs become Typst paragraphs; headings shift down by `heading_offset`
    (inside a section titled at level 1, "# Heading" becomes level 2). Headings typed in
    a text are never numbered: only real sections are (1, 1.1, 1.1.1 in numbered styles).
    Those at level `outlined_levels` or above are listed in the table of contents."""
    out = []
    for p in paragraphs:
        body = runs_to_typst(p.runs)
        if not body:
            continue
        if p.heading:
            level = p.heading + heading_offset
            outlined = "true" if level <= outlined_levels else "false"
            body = f"#heading(level: {level}, outlined: {outlined}, numbering: none)[{body}]"
        out.append(aligned_paragraph(body, p.stored_align))
    return "\n\n".join(out)


def aligned_paragraph(body: str, align: str) -> str:
    """A paragraph (or heading) left, centred or right (SPEC §13l.10): its lines are then
    not justified. "" (or "justify") = as the journal style sets text: justified."""
    if align not in ("left", "center", "right"):
        return body
    return f"#align({align})[#set par(justify: false)\n{body}]"


def aligned_object(code: str, align: str) -> str:
    """A figure or table (a Typst figure: picture or table with its caption) on the left
    or right; "" (or "center") = centred, as the journal style places figures."""
    if align not in ("left", "right"):
        return code
    return f"#[#show figure: set align({align})\n{code}]"


# ---------------------------------------------------------------------- formatting helpers

def _split(runs: list[Run], pattern: re.Pattern, make) -> list[Run]:
    """Apply `make(match) -> list[Run]` to every match of `pattern` in plain runs."""
    out: list[Run] = []
    for run in runs:
        if run.valign != NORMAL or run.ref:
            out.append(run)
            continue
        pos = 0
        for m in pattern.finditer(run.text):
            if m.start() > pos:
                out.append(Run(run.text[pos:m.start()], run.bold, run.italic))
            for piece in make(m):
                out.append(Run(piece.text, run.bold or piece.bold, run.italic or piece.italic,
                               piece.valign))
            pos = m.end()
        if pos < len(run.text):
            out.append(Run(run.text[pos:], run.bold, run.italic))
    return [r for r in out if r.text or r.ref]


# Formulas (CDCl3, Na2SO4, CH2Cl2, Et3N, "under N2"; not SN2, S1, 3a, THF, the H2 of
# "1H, H2"), deuterated solvents, nuclei and ranges are recognised as by the editors'
# formatting check (core/chemtext.py).
_DEUTERATED = chemtext.DEUTERATED
_NUCLEUS = chemtext.NUCLEUS   # only before "NMR" or a brace, so "(m, 2H)" is left alone
_J = re.compile(r"(?<![A-Za-z])J(?=\s*(?:=|\d|[A-Z]?[A-Z]?\s*=))")
_RANGE = chemtext.Finder(chemtext.range_matches)   # not CAS numbers, dates, 6-31G
_HZ = re.compile(r"(?<=\d)(Hz|MHz)\b")

# Words the user wants printed as written (the project's dictionary.txt, SPEC §13k.1ai):
# formulas among them are not subscripted. Set for one build with `leave_alone`.
_LEAVE: ContextVar[frozenset[str]] = ContextVar("leave_alone", default=frozenset())


@contextmanager
def leave_alone(words):
    """While building, leave these words (any capitals) as the user wrote them."""
    token = _LEAVE.set(frozenset(w.lower() for w in words))
    try:
        yield
    finally:
        _LEAVE.reset(token)


def _formulas() -> chemtext.Finder:
    leave = _LEAVE.get()
    return chemtext.Finder(lambda text: chemtext.formula_matches(text, leave))


def _formula_runs(m: re.Match) -> list[Run]:
    out = []
    for part in re.findall(r"\d+|[^\d]+", m.group(0)):
        out.append(Run(part, valign=SUB if part.isdigit() else NORMAL))
    return out


def format_chemistry(runs: list[Run]) -> list[Run]:
    """Subscripts in formulas (CDCl3 -> CDCl₃, Na2SO4, Et3N) and deuterated solvents
    (DMSO-d6 -> DMSO-d₆)."""
    leave = _LEAVE.get()
    solvents = chemtext.Finder(lambda text: [m for m in _DEUTERATED.finditer(text)
                                             if m.group(0).lower() not in leave])
    runs = _split(runs, solvents, lambda m: [Run(f"{m.group(1)}-"), Run("d", italic=True),
                                             Run(m.group(2), valign=SUB)])
    runs = _split(runs, chemtext.DEUTERATED_PREFIX,   # "d4-MeOH" -> d₄-MeOH
                  lambda m: [Run("d", italic=True), Run(m.group(1), valign=SUB), Run("-")])
    return _split(runs, _formulas(), _formula_runs)


def format_nmr(runs: list[Run]) -> list[Run]:
    """For NMR text: ¹H/¹³C superscripts, italic J, en-dashes in ranges, "400MHz" spacing,
    plus the chemistry formatting."""
    runs = _split(runs, _NUCLEUS, lambda m: [Run(m.group(1), valign=SUPER), Run(m.group(2))])
    runs = _split(runs, _J, lambda m: [Run("J", italic=True)])
    runs = _split(runs, _RANGE, lambda m: [Run("–")])
    runs = _split(runs, _HZ, lambda m: [Run(" " + m.group(1))])
    return format_chemistry(runs)


def formula_runs(formula: str) -> list[Run]:
    """A Hill formula as runs: digits subscripted, isotope labels ([13C]) superscripted."""
    out: list[Run] = []
    for iso, sym, word in _formula_tokens(formula):
        if iso:
            out += [Run(iso, valign=SUPER), Run(sym)]
        elif word.isdigit():
            out.append(Run(word, valign=SUB))
        else:
            out.append(Run(word))
    return _merge(out)


def _formula_tokens(formula: str) -> list[tuple[str, str, str]]:
    tokens = []
    for m in re.finditer(r"\[(\d+)([A-Z][a-z]?)\]|([A-Z][a-z]?)|(\d+)", formula):
        if m.group(1):
            tokens.append((m.group(1), m.group(2), ""))
        else:
            tokens.append(("", "", m.group(3) or m.group(4)))
    return tokens


def _merge(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if out and out[-1].same_format(r):
            out[-1] = Run(out[-1].text + r.text, r.bold, r.italic, r.valign)
        else:
            out.append(r)
    return out


def charge_runs(display: str) -> list[Run]:
    """An adduct like "[M+H]+" or "[M−H]−" with the charge as superscript."""
    m = re.match(r"^(\[.*\])(\d*[+\-−][•·]?)$", display.strip())
    if not m:
        return [Run(display)]
    charge = m.group(2).replace("-", "−")
    return [Run(m.group(1).replace("-", "−")), Run(charge, valign=SUPER)]
