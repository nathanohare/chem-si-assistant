"""Italic parts of a compound's printed name (SPEC §13x).

By chemistry convention (IUPAC), some prefixes of a systematic name are printed in italics.
When the SI is compiled, these are italicised automatically in the compound's name:

  - heteroatom locants before a hyphen: *N*-benzyl, *N*,*N*-dimethyl, *N*′-, *O*-acetyl,
    *S*-methyl, *P*-phenyl;
  - *tert*- and *sec*-: *tert*-butyl, *sec*-butyl;
  - *cis*/*trans*: *trans*-2-methylcyclohexanol;
  - stereodescriptors in brackets: (*E*)-, (*Z*)-, (*R*)-, (*S*)-, (2*E*,4*Z*)-,
    (1*R*,2*S*)-, (*R**)- (only the letters are italic: "(", "2", "," and ")" are not);
  - *o*-, *m*-, *p*- before a hyphen: *p*-tolyl, *o*-xylene, *m*-CPBA.

Every automatic value can be typed over (SPEC §2.7): a name that contains an asterisk (other
than the * of a relative configuration, (R*,S*)) is printed exactly as typed instead, with *…* as italics (the same marks as in other texts:
"1-Benzyl-2-*p*-tolyl…"), and nothing else in it is italicised automatically. "\\*" is a
literal asterisk.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .markup import Run, parse

_PRIME = "'′″‴"
# A heteroatom locant list just before a hyphen: N-, N,N-, N,N′-, N,O-, N1-? (no: a number
# after the letter is a different kind of locant and is left upright). Not after a letter
# or digit, so the end of a word ("…SN-", "4N-") is not taken.
_HETERO = re.compile(rf"(?<![A-Za-z0-9])([NOSP][{_PRIME}]*(?:,[NOSP][{_PRIME}]*)*)(?=-)")
_WORDS = re.compile(r"(?<![A-Za-z])(tert|sec|cis|trans)(?=-)")
_RING = re.compile(r"(?<![A-Za-z0-9])([omp])(?=-[A-Za-z0-9(\[])")
# Stereodescriptors in brackets: (E), (Z), (R), (S), (2E,4Z), (1R,2S), (R*), (1′S), (R,R)…
_ITEM = rf"\d*[{_PRIME}]*[RSEZ]\*?"
_STEREO = re.compile(rf"(?<=[(\[])\s*{_ITEM}(?:\s*,\s*{_ITEM})*\s*(?=[)\]])")
_STEREO_LETTER = re.compile(r"[RSEZ]")
_RELATIVE = re.compile(r"(?<=[RS])\*")


def italic_spans(name: str) -> list[tuple[int, int]]:
    """The (start, end) character ranges of `name` that are printed in italics, in order
    and not overlapping."""
    marks = [False] * len(name)
    for m in _HETERO.finditer(name):
        for i in range(m.start(1), m.end(1)):
            if name[i].isalpha():
                marks[i] = True
    for pattern in (_WORDS, _RING):
        for m in pattern.finditer(name):
            for i in range(m.start(1), m.end(1)):
                marks[i] = True
    for m in _STEREO.finditer(name):
        for letter in _STEREO_LETTER.finditer(m.group(0)):
            marks[m.start() + letter.start()] = True
    spans: list[tuple[int, int]] = []
    for i, italic in enumerate(marks):
        if not italic:
            continue
        if spans and spans[-1][1] == i:
            spans[-1] = (spans[-1][0], i + 1)
        else:
            spans.append((i, i + 1))
    return spans


def name_runs(name: str, bold: bool = False) -> list[Run]:
    """The compound's name as runs of text, italicised as described above (or as typed, if
    the name contains an asterisk)."""
    # (the * of a relative configuration, (R*,S*), is a character, not an italic mark)
    literal = _RELATIVE.sub(r"\\*", name)
    if "*" in _RELATIVE.sub("", name):   # typed over by hand: *…* is italic
        return [replace(r, bold=r.bold or bold)
                for p in parse(literal) for r in p.runs]
    runs: list[Run] = []
    at = 0
    for start, end in italic_spans(name):
        if start > at:
            runs.append(Run(name[at:start], bold))
        runs.append(Run(name[start:end], bold, True))
        at = end
    if at < len(name):
        runs.append(Run(name[at:], bold))
    return runs
