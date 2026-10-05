"""NMR solvents: the ways a peak report may write them (SPEC §13l.14).

One table for the whole program: the impurity-peak check (core/checks.py, via
core/nmr_parse.py and core/impurities.py) maps a solvent to its usual name to look it up
in its table, and the spectra captions and header lines (core/spectra.py) print it as the
report writes it ("MeOD", "CDCl3", "methanol-d4"), subscripted when printed.

Spellings are compared after `plain()`: lower case, subscript digits made plain, any dash
made "-", spaces removed. So "CD₃OD", "cd3od" and "CD3 OD" are all CD3OD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each solvent's usual (canonical) name, with the ways a report may write it.
SOLVENT_NAMES: dict[str, tuple[str, ...]] = {
    "CDCl3": ("cdcl3", "chloroform-d", "chloroform-d1", "deuterochloroform", "d-chloroform",
              "cdcl3-d"),
    "DMSO-d6": ("dmso-d6", "d6-dmso", "(cd3)2so", "cd3socd3", "dimethylsulfoxide-d6",
                "dimethylsulphoxide-d6", "dmso"),
    "CD3OD": ("cd3od", "methanol-d4", "methanol-d", "d4-methanol", "meod", "meod-d4",
              "d4-meoh", "meoh-d4", "d4-meod", "cd3od-d4"),
    "acetone-d6": ("acetone-d6", "d6-acetone", "(cd3)2co", "cd3cocd3"),
    "C6D6": ("c6d6", "benzene-d6", "d6-benzene"),
    "CD2Cl2": ("cd2cl2", "dichloromethane-d2", "d2-dichloromethane", "methylenechloride-d2",
               "dcm-d2", "d2-dcm"),
    "CD3CN": ("cd3cn", "acetonitrile-d3", "d3-acetonitrile", "mecn-d3", "d3-mecn"),
    "D2O": ("d2o", "deuteriumoxide"),
    "THF-d8": ("thf-d8", "d8-thf", "tetrahydrofuran-d8", "c4d8o"),
    "toluene-d8": ("toluene-d8", "d8-toluene", "c7d8", "c6d5cd3"),
    "CD3NO2": ("cd3no2", "nitromethane-d3"),
    "pyridine-d5": ("pyridine-d5", "d5-pyridine", "c5d5n"),
}
SOLVENTS = tuple(SOLVENT_NAMES)

# Every spelling -> its solvent's usual name ("meod" -> "CD3OD").
ALIASES: dict[str, str] = {alias: name for name, aliases in SOLVENT_NAMES.items()
                           for alias in aliases}

# Subscript digits made plain and any dash made "-": for comparing, and for the written
# form (so "CD₃OD" prints like "CD3OD", subscripted by the printing, and "DMSO‑d₆" with a
# non-breaking hyphen like "DMSO-d6").
_PLAIN = str.maketrans({**{chr(0x2080 + i): str(i) for i in range(10)},
                        **{c: "-" for c in "‐‑‒–—−"}})


def plain(text: str) -> str:
    """How spellings are compared: "CD₃ OD" -> "cd3od"."""
    return re.sub(r"\s+", "", text.translate(_PLAIN).lower())


@dataclass(frozen=True)
class Found:
    name: str      # the usual name: "CD3OD"
    written: str   # as the report writes it: "MeOD" (plain digits and dashes, one space)
    start: int     # where it is in the text
    end: int


def find(text: str) -> Found | None:
    """The first NMR solvent named in `text` (the longest spelling where two start at
    the same place: "DMSO-d6" rather than "DMSO"), or None."""
    # The text without spaces, with where each of its characters came from. (Lowered one
    # character at a time, so every position still matches the original text.)
    low = "".join(c.lower()[:1] or c for c in text.translate(_PLAIN))
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(low):
        if not ch.isspace():
            chars.append(ch)
            where.append(i)
    squeezed = "".join(chars)
    best: tuple[int, int, str] | None = None   # (start, -length, alias)
    for alias in ALIASES:
        i = squeezed.find(alias)
        while i != -1:
            begin, end = where[i], where[i + len(alias) - 1] + 1
            if _whole(low, begin, end):
                key = (i, -len(alias), alias)
                if best is None or key < best:
                    best = key
                break
            i = squeezed.find(alias, i + 1)
    if best is None:
        return None
    start, length, alias = best[0], -best[1], best[2]
    begin, end = where[start], where[start + length - 1] + 1
    written = re.sub(r"\s+", " ", text[begin:end].translate(_PLAIN)).strip()
    return Found(ALIASES[alias], written, begin, end)


def _whole(text: str, start: int, end: int) -> bool:
    """Not part of a longer word or formula (so "d2o" is not found inside "cd2od", nor
    "meod" inside a longer word)."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (before.isalnum() or after.isalnum())


def canonical(text: str) -> str:
    """The usual name of a solvent typed on its own ("cdcl3", "Chloroform-d", "MeOD" ->
    "CDCl3", "CD3OD"); the text itself (trimmed) if it isn't one this program knows."""
    return ALIASES.get(plain(text), text.strip())
