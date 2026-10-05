"""The small text format used to store formatted text in plain .txt files.

Formatted text (bold, italic, superscript, subscript, headings) is stored
human-readably so the Project Folder stays useful without the program:

    # Heading 1             (line starting with "# ")
    ## Heading 2            (line starting with "## ")
    **bold**   *italic*   ^{superscript}   _{subscript}

Example:  "**¹H NMR** (400 MHz, CDCl_{3}) δ 7.25 (d, *J* = 2.0 Hz, 1H)"

Characters that would otherwise be read as markers are escaped with a
backslash: \\*  \\^  \\_  \\}  \\#  \\\\

Links (Phase 7) are stored by permanent internal ID, so they survive renumbering:

    @{c-7f3a2c}                 a mention, printed as the item's current ID or label:
                                a compound (c-…: **3g**), figure (f-…: Figure S2),
                                table (t-…: Table S1) or scheme page (k-…: Scheme S3)
    @cite{r-1a2b3c,r-4d5e6f}    a citation of entries in references.json
    !figure{f-1a2b3c} Caption text         a figure paragraph (custom sections, General
                                           Information); its picture is listed in the
                                           owner's JSON ("figures": {id: {"file", "label"}})
    !table{t-1a2b3c} Caption text          a table paragraph; the table is in tables/<id>.json
    !image{images/x.png} Caption text      an older picture paragraph (read; becomes a
                                           figure when the text is next opened for editing)

Alignment (SPEC §13l.10) is a short prefix at the very start of the line, before
everything else (a heading's "# ", a figure's "!figure{…}"):

    !align{center} A centred paragraph
    !align{right} # A heading on the right
    !align{left} !figure{f-1a2b3c} Caption       a figure (or table) on the left

Paragraphs can be left, center, right or justify; figures and tables left, center or
right. Only a choice that differs from the default is written: text is justified and
figures and tables are centred unless the line says otherwise, so texts that use the
defaults look exactly as before. The alignment of a figure or table is its paragraph's
(it is not in the figure's or table's own JSON): it travels with the paragraph when
text is copied, pasted, moved or undone. Texts that follow the journal style (compound
procedures and SI texts, general procedures in the editor) never get the prefix.

An "@" is only special before "{" or "cite{", and "!" only at the start of a line
before "image{", "figure{", "table{" or "align{"; there they are escaped as \\@ and \\!.

This module only converts between text and a list of `Paragraph`s; the
editor (ui/rich_text.py) and the compiler (Phase 5) both work from that list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NORMAL, SUPER, SUB = "normal", "super", "sub"
MENTION, CITE = "c:", "cite:"


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    valign: str = NORMAL  # NORMAL, SUPER or SUB
    # A link instead of text: "c:<compound id>" (mention) or "cite:<id>,<id>" (citation).
    # `text` is then only what an editor shows; the stored form is the link.
    ref: str = ""

    def same_format(self, other: "Run") -> bool:
        return (not self.ref and not other.ref and
                (self.bold, self.italic, self.valign) == (other.bold, other.italic, other.valign))

    @property
    def mention(self) -> str:
        """The mentioned item's internal ID (compound, figure, table or scheme), or ""."""
        return self.ref[len(MENTION):] if self.ref.startswith(MENTION) else ""

    @property
    def citations(self) -> list[str]:
        """The cited reference IDs, or []."""
        if not self.ref.startswith(CITE):
            return []
        return [x.strip() for x in self.ref[len(CITE):].split(",") if x.strip()]


LEFT, CENTER, RIGHT, JUSTIFY = "left", "center", "right", "justify"
TEXT_ALIGNS = (LEFT, CENTER, RIGHT, JUSTIFY)
OBJECT_ALIGNS = (LEFT, CENTER, RIGHT)
TEXT_DEFAULT_ALIGN, OBJECT_DEFAULT_ALIGN = JUSTIFY, CENTER


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    heading: int = 0  # 0 = normal text, 1 or 2 = heading level
    image: str = ""   # an older picture paragraph: path relative to the owner's folder
    figure: str = ""  # a figure paragraph: the figure's ID (runs = its caption)
    table: str = ""   # a table paragraph: the table's ID (runs = its caption)
    align: str = ""   # "" = the default (text justified, figures and tables centred), or
                      # "left", "center", "right", "justify" (text only)

    @property
    def plain_text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def is_object(self) -> bool:
        """A figure, table or (older) picture paragraph: its runs are a caption."""
        return bool(self.image or self.figure or self.table)

    @property
    def alignment(self) -> str:
        """The alignment it has: its own, or the default for its kind."""
        return effective_align(self.align, self.is_object)

    @property
    def stored_align(self) -> str:
        """What is written for it: "" when it is the default for its kind."""
        return stored_align(self.align, self.is_object)


def effective_align(align: str, is_object: bool) -> str:
    """"left", "center", "right" or "justify": `align` if it is possible for a text
    (`is_object` False) or a figure/table, otherwise the default for it."""
    if is_object:
        return align if align in OBJECT_ALIGNS else OBJECT_DEFAULT_ALIGN
    return align if align in TEXT_ALIGNS else TEXT_DEFAULT_ALIGN


def stored_align(align: str, is_object: bool) -> str:
    """The alignment to store: "" for the default (and for anything not possible)."""
    value = effective_align(align, is_object)
    return "" if value == (OBJECT_DEFAULT_ALIGN if is_object else TEXT_DEFAULT_ALIGN) else value


# What a mention points to, from the internal ID's prefix (core/ids.new_id).
MENTION_KINDS = {"f": "figure", "t": "table", "k": "scheme"}


def mention_kind(item_id: str) -> str:
    """"compound", "figure", "table" or "scheme" for an internal ID."""
    return MENTION_KINDS.get(item_id.split("-", 1)[0], "compound") if "-" in item_id \
        else "compound"


def mentions(paragraphs: list[Paragraph]) -> list[str]:
    """Every mentioned ID (compounds, figures, tables, schemes), in order (repeats included)."""
    return [r.mention for p in paragraphs for r in p.runs if r.mention]


def objects(paragraphs: list[Paragraph]) -> list[tuple[str, str]]:
    """(kind, ID) of every figure and table paragraph, in order. Older picture paragraphs
    are ("image", path)."""
    out = []
    for p in paragraphs:
        if p.figure:
            out.append(("figure", p.figure))
        elif p.table:
            out.append(("table", p.table))
        elif p.image:
            out.append(("image", p.image))
    return out


def citations(paragraphs: list[Paragraph]) -> list[str]:
    return [c for p in paragraphs for r in p.runs for c in r.citations]


_LINK = re.compile(r"@(cite)?\{([^{}]*)\}")
_OBJECT = re.compile(r"^!(image|figure|table)\{([^{}]*)\}\s?")
# (written as "center"; "centre" and "justified" are read too, e.g. typed by hand)
_ALIGN = re.compile(r"^!align\{\s*(left|center|centre|right|justify|justified)\s*\}\s?",
                    re.IGNORECASE)
_ALIGN_NAMES = {"centre": CENTER, "justified": JUSTIFY}
_OBJECT_STARTS = ("!image{", "!figure{", "!table{", "!align{")


MAX_HEADING = 2


# ---------------------------------------------------------------------- parsing

def parse(text: str) -> list[Paragraph]:
    """Read stored text into paragraphs (one per line)."""
    return [_parse_line(line) for line in text.replace("\r\n", "\n").split("\n")]


def _parse_line(line: str) -> Paragraph:
    heading = 0
    obj = {}
    m = _ALIGN.match(line)
    if m:
        name = m.group(1).lower()
        obj["align"] = _ALIGN_NAMES.get(name, name)
        line = line[m.end():]
    m = _OBJECT.match(line)
    if m:
        obj[m.group(1)] = m.group(2).strip()
        line = line[m.end():]
    else:
        for level in range(MAX_HEADING, 0, -1):
            prefix = "#" * level + " "
            if line.startswith(prefix):
                heading, line = level, line[len(prefix):]
                break

    runs: list[Run] = []
    buf: list[str] = []
    bold = italic = False
    valign = NORMAL

    def flush() -> None:
        if buf:
            _append(runs, Run("".join(buf), bold, italic, valign))
            buf.clear()

    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if ch == "\\" and nxt:
            buf.append(nxt)
            i += 2
        elif ch == "@" and (link := _LINK.match(line, i)):
            flush()
            if link.group(1):
                ids = [x.strip() for x in link.group(2).split(",") if x.strip()]
                runs.append(Run("", bold, italic, valign, CITE + ",".join(ids)))
            else:
                runs.append(Run("", bold, italic, valign, MENTION + link.group(2).strip()))
            i = link.end()
        elif ch == "*" and nxt == "*":
            flush(); bold = not bold; i += 2
        elif ch == "*":
            flush(); italic = not italic; i += 1
        elif ch in "^_" and nxt == "{" and valign == NORMAL:
            flush(); valign = SUPER if ch == "^" else SUB; i += 2
        elif ch == "}" and valign != NORMAL:
            flush(); valign = NORMAL; i += 1
        else:
            buf.append(ch)
            i += 1
    flush()
    return Paragraph(runs, heading, **obj)


def _append(runs: list[Run], run: Run) -> None:
    """Add a run, merging it into the previous one if the format matches."""
    if not run.text and not run.ref:
        return
    if runs and runs[-1].same_format(run):
        runs[-1] = Run(runs[-1].text + run.text, run.bold, run.italic, run.valign)
    else:
        runs.append(run)


# ---------------------------------------------------------------------- writing

def serialize(paragraphs: list[Paragraph]) -> str:
    return "\n".join(_serialize_paragraph(p) for p in paragraphs)


def _escape(text: str) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch in "\\*^_}" or (ch == "@" and (text.startswith("{", i + 1)
                                            or text.startswith("cite{", i + 1))):
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _link_text(run: Run) -> str:
    if run.mention:
        return "@{" + run.mention + "}"
    return "@cite{" + ",".join(run.citations) + "}"


def _serialize_paragraph(p: Paragraph) -> str:
    merged: list[Run] = []
    for run in p.runs:
        _append(merged, run)

    out: list[str] = []
    align = p.stored_align
    if align:   # (a space after it only when something follows)
        out.append(f"!align{{{align}}}"
                   + (" " if merged or p.is_object or p.heading else ""))
    for kind in ("figure", "table", "image"):
        value = getattr(p, kind)
        if value:
            out.append(f"!{kind}{{" + value.replace("{", "").replace("}", "")
                       + ("} " if merged else "}"))
            break
    else:
        if p.heading:
            out.append("#" * min(p.heading, MAX_HEADING) + " ")
    bold = italic = False
    valign = NORMAL
    for k, run in enumerate(merged):
        text = _link_text(run) if run.ref else _escape(run.text)
        if k == 0 and not p.heading and not p.is_object and not run.ref and (
                text.startswith("#") or text.startswith(_OBJECT_STARTS)):
            text = "\\" + text  # don't let normal text be mistaken for a heading or picture
        # Close a super/subscript first (braces must nest), then toggle bold/italic.
        if valign != run.valign and valign != NORMAL:
            out.append("}")
            valign = NORMAL
        if bold != run.bold:
            out.append("**")
            bold = run.bold
        if italic != run.italic:
            out.append("*")
            italic = run.italic
        if valign != run.valign:
            out.append("^{" if run.valign == SUPER else "_{")
            valign = run.valign
        out.append(text)
    if valign != NORMAL:
        out.append("}")
    if italic:
        out.append("*")
    if bold:
        out.append("**")
    return "".join(out)
