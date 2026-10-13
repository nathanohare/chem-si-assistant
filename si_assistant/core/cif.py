"""Reading CIF files (Crystallographic Information File, version 1.1) for X-ray
structures (SPEC §13v).

`read_cif(text)` returns the file's data blocks; each holds its single items (tag →
value as written) and its loops (tables of values). Values are kept as text; `number()`
reads "12.345(6)" as 12.345 (and its standard uncertainty), and "?" / "." (unknown /
not applicable) as None. `cif_markup()` turns CIF's own text codes ("Mo K\\a",
"\\%A^3^", "R~int~") into the program's stored text format.

The syntax handled is CIF 1.1's: comments (#), data_ blocks, loop_ tables, tags
(_name), values unquoted, 'quoted', "quoted" (a quote ends a value only when white
space follows it) or as text fields between lines starting with ";". Save frames and
global_ blocks (dictionaries, not structures) are skipped. Never raises for a damaged
file: what can be read is returned.

Pure Python: unit-tested with made-up files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CifBlock:
    name: str                                            # "data_xyz" without "data_"
    items: dict[str, str] = field(default_factory=dict)  # tag (lower case) -> value
    loops: list[dict[str, list[str]]] = field(default_factory=list)

    def get(self, *tags: str) -> str | None:
        """The value of the first of `tags` the block has (a single item, or a loop's
        only row); None if none (or only "?" / ".")."""
        for tag in tags:
            tag = tag.lower()
            value = self.items.get(tag)
            if value is None:
                loop = self.loop_with(tag)
                if loop is not None and len(loop[tag]) == 1:
                    value = loop[tag][0]
            if value is not None and value not in ("?", "."):
                return value
        return None

    def loop_with(self, tag: str) -> dict[str, list[str]] | None:
        tag = tag.lower()
        return next((loop for loop in self.loops if tag in loop), None)

    def rows(self, tag: str) -> list[dict[str, str]]:
        """The rows of the loop holding `tag`, each {tag: value}."""
        loop = self.loop_with(tag)
        if loop is None:
            return []
        n = min(len(v) for v in loop.values()) if loop else 0
        return [{t: values[k] for t, values in loop.items()} for k in range(n)]


# ---------------------------------------------------------------------- reading


def _tokens(text: str):
    """(kind, value) pairs: "tag", "value", "loop", "data", "save", "global", "stop"."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(";"):                  # a text field, to the next ";" line
            body = [line[1:]]
            i += 1
            while i < len(lines) and not lines[i].startswith(";"):
                body.append(lines[i])
                i += 1
            i += 1                                # (the closing ";" line)
            value = "\n".join(body).strip("\n")
            yield "value", value.strip()
            continue
        pos, n = 0, len(line)
        while pos < n:
            ch = line[pos]
            if ch in " \t":
                pos += 1
                continue
            if ch == "#":
                break
            if ch in "'\"":                       # quoted: ends at quote + white space
                end = pos + 1
                while end < n and not (line[end] == ch and (end + 1 == n or line[end + 1] in " \t")):
                    end += 1
                yield "value", line[pos + 1:end]
                pos = end + 1
                continue
            end = pos
            while end < n and line[end] not in " \t":
                end += 1
            word = line[pos:end]
            pos = end
            low = word.lower()
            if word.startswith("_"):
                yield "tag", low
            elif low == "loop_":
                yield "loop", ""
            elif low.startswith("data_"):
                yield "data", word[5:]
            elif low.startswith("save_"):
                yield "save", word[5:]
            elif low == "global_":
                yield "global", ""
            elif low == "stop_":
                yield "stop", ""
            else:
                yield "value", word
        i += 1


def read_cif(text: str) -> list[CifBlock]:
    """The data blocks of a CIF file's text (see the module's text)."""
    blocks: list[CifBlock] = []
    block: CifBlock | None = None
    tokens = list(_tokens(text))
    k = 0
    in_save = False
    while k < len(tokens):
        kind, value = tokens[k]
        if kind == "data":
            block = CifBlock(value)
            blocks.append(block)
            in_save = False
            k += 1
        elif kind in ("save", "global"):
            in_save = kind == "save" and bool(value)  # (a save frame: skipped)
            if kind == "global":
                block = None
            k += 1
        elif kind == "loop":
            tags = []
            k += 1
            while k < len(tokens) and tokens[k][0] == "tag":
                tags.append(tokens[k][1])
                k += 1
            values = []
            while k < len(tokens) and tokens[k][0] == "value":
                values.append(tokens[k][1])
                k += 1
            if block is not None and not in_save and tags:
                loop = {t: values[j::len(tags)] for j, t in enumerate(tags)}
                block.loops.append(loop)
        elif kind == "tag":
            if k + 1 < len(tokens) and tokens[k + 1][0] == "value":
                if block is not None and not in_save:
                    block.items[value] = tokens[k + 1][1]
                k += 2
            else:
                k += 1
        else:
            k += 1
    return blocks


def read_cif_file(path) -> list[CifBlock]:
    from pathlib import Path
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return read_cif(text)


# ---------------------------------------------------------------------- values

_NUMBER = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?:\((\d+)\))?\s*$")


def number(value: str | None) -> float | None:
    """"12.345(6)" → 12.345; "?", ".", None or text → None."""
    if value is None:
        return None
    m = _NUMBER.match(value)
    return float(m.group(1)) if m else None


def uncertainty(value: str | None) -> float | None:
    """"12.345(6)" → 0.006 (the standard uncertainty in the last digits); None if none."""
    if value is None:
        return None
    m = _NUMBER.match(value)
    if not m or not m.group(2):
        return None
    digits = m.group(1).split("e")[0].split("E")[0]
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return int(m.group(2)) * 10.0 ** -decimals


# CIF's text codes (International Tables, CIF 1.1 "markup"): \a α, \b β, \q θ, \l λ, \m μ,
# \s σ, \r ρ, \%A Å, \% °, ^…^ superscript, ~…~ subscript.
_GREEK = {"a": "α", "b": "β", "g": "γ", "d": "δ", "e": "ε", "z": "ζ", "h": "η", "q": "θ",
          "i": "ι", "k": "κ", "l": "λ", "m": "μ", "n": "ν", "x": "ξ", "p": "π", "r": "ρ",
          "s": "σ", "t": "τ", "f": "φ", "c": "χ", "y": "ψ", "w": "ω",
          "A": "Α", "B": "Β", "G": "Γ", "D": "Δ", "Q": "Θ", "L": "Λ", "P": "Π", "S": "Σ",
          "F": "Φ", "Y": "Ψ", "W": "Ω"}


def cif_plain(value: str) -> str:
    """CIF text codes as plain characters (no super/subscripts)."""
    out = re.sub(r"\\%A", "Å", value)
    out = out.replace("\\%", "°")
    out = re.sub(r"\\([A-Za-z])", lambda m: _GREEK.get(m.group(1), m.group(1)), out)
    return out


def cif_markup(value: str) -> str:
    """CIF text codes in the program's stored text format: "R~int~" → "R_{int}",
    "\\%A^3^" → "Å^{3}"."""
    from .markup import Paragraph, Run, serialize
    text = cif_plain(value)
    runs: list[Run] = []
    for piece in re.split(r"(\^[^^]*\^|~[^~]*~)", text):
        if not piece:
            continue
        if piece.startswith("^") and piece.endswith("^") and len(piece) > 1:
            runs.append(Run(piece[1:-1], valign="super"))
        elif piece.startswith("~") and piece.endswith("~") and len(piece) > 1:
            runs.append(Run(piece[1:-1], valign="sub"))
        else:
            runs.append(Run(piece))
    return serialize([Paragraph(runs)]).strip() if runs else ""
