"""Reading NMR peak reports (as pasted from MestReNova) for the error checks (SPEC §8.2).

    "1H NMR (400 MHz, CDCl3) δ 7.36 – 7.29 (m, 5H), 6.52 (d, J = 7.9 Hz, 1H), 2.08 (s, 3H)"
    "13C NMR (101 MHz, CDCl3) δ 171.2, 138.6, 128.9 (2C), 162.3 (d, J = 247 Hz)"

These are best-effort readings: anything unexpected is simply not counted, and the
checks that use them say they are approximate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import solvents as _solvents

# How solvents are written -> their usual name ("meod" -> "CD3OD"). The table itself is
# shared with the spectra captions: core/solvents.py.
SOLVENTS = _solvents.ALIASES

_NUMBER = r"[−-]?\d+(?:\.\d+)?"


@dataclass
class Peak:
    shift: float            # δ, or the start of a range
    end: float | None       # end of a range ("7.36 – 7.29"), else None
    details: str            # what was in the brackets: "m, 5H" / "d, J = 8.3 Hz, 1H" / "2C"

    @property
    def multiplicity(self) -> str:
        return self.details.split(",")[0].strip().lower() if self.details else ""

    @property
    def integration(self) -> float | None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*H\b", self.details)
        return float(m.group(1)) if m else None


def _num(text: str) -> float:
    return float(text.replace("−", "-"))


def frequency(text: str) -> float | None:
    m = re.search(r"(\d{2,4}(?:\.\d+)?)\s*MHz", text)
    return float(m.group(1)) if m else None


def solvent(text: str) -> str:
    """The usual name of the first NMR solvent the report names ("CD3OD" for "MeOD"), or
    "" if it names none."""
    found = _solvents.find(text)
    return found.name if found is not None else ""


def data_part(text: str) -> str:
    """The peak list: everything after "δ" (or after the "(400 MHz, CDCl3)" header)."""
    if "δ" in text:
        return text.split("δ", 1)[1]
    m = re.match(r"^[^()]*NMR\s*\([^)]*\)\s*:?", text)
    return text[m.end():] if m else text


def peaks(text: str) -> list[Peak]:
    """Every peak in a report, with the bracketed details that follow it (the details
    may hold brackets of their own, e.g. "(s, 9H, C(CH3)3)" or "(d, J(C,F) = 247 Hz)")."""
    data = data_part(text)
    out = []
    pattern = re.compile(rf"({_NUMBER})(?:\s*[–—-]\s*({_NUMBER}))?\s*")
    pos = 0
    while (m := pattern.search(data, pos)) is not None:
        pos = m.end()
        # Skip numbers that are inside brackets (J values, "2C") — they are part of details.
        before = data[:m.start()]
        if before.count("(") > before.count(")"):
            continue
        details = ""
        if data.startswith("(", m.end()):
            depth = 0
            for i in range(m.end(), len(data)):
                if data[i] == "(":
                    depth += 1
                elif data[i] == ")":
                    depth -= 1
                    if depth == 0:
                        details = data[m.end() + 1:i].strip()
                        pos = i + 1     # carry on after the whole bracket
                        break
            # (no matching ")": no details, and the numbers inside are skipped above)
        out.append(Peak(_num(m.group(1)), _num(m.group(2)) if m.group(2) else None,
                        details))
    return out


# A coupling constant: "J = 8.3 Hz", "J = 8.3, 2.0 Hz", "2J = 14.0 Hz", "J1 = 8.0",
# "JH–F = 47.6 Hz", "J(H,F) = 47.6 Hz", "3JHP = 22", decimal commas ("J = 8,3 Hz"). After
# "J": up to a few characters naming the coupling (no "=", ";" or line break), then "=",
# then one or more numbers (each may be followed by "Hz") separated by commas, "and" or
# "&". A number followed by "H" is an integration ("2H"), not a coupling constant.
_J_VALUE = r"\d+(?:[.,]\d+)?(?!\d|\s*H(?![a-z]))"
_COUPLING = re.compile(
    rf"(?<![A-Za-z])J(?P<which>[^=;\n]{{0,15}}?)\s*=\s*"
    rf"(?P<values>{_J_VALUE}(?:\s*Hz)?(?:\s*(?:,|and|&)\s*{_J_VALUE}(?:\s*Hz)?)*)")


@dataclass
class Coupling:
    value: float      # Hz
    written: str      # as in the report: "8.3" or "8,3"
    which: str        # what names the coupling, e.g. "" / "H–F" / "(H,F)" / "1"


def couplings(text: str) -> list[Coupling]:
    """Every coupling constant in a report, in order (best-effort: anything unexpected is
    left out)."""
    out = []
    for m in _COUPLING.finditer(text):
        which = m.group("which").strip()
        if len(which) > 12 or re.search(r"[A-Za-z]{4,}", which):
            continue   # (a word between "J" and "=": not a coupling constant)
        for value in re.findall(_J_VALUE, m.group("values")):
            try:
                out.append(Coupling(float(value.replace(",", ".")), value, which))
            except ValueError:
                continue
    return out


def carbon_signal_count(text: str) -> int:
    """Number of listed ¹³C signals (a "(2C)" note still counts as one listed signal)."""
    return len(peaks(text))


def proton_count(text: str) -> float:
    """Sum of the integrations, e.g. "(m, 5H)" + "(d, 1H)" = 6."""
    return sum(p.integration or 0.0 for p in peaks(text))
