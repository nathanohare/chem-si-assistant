"""The text of an imported SI around its compounds (SPEC §13u.9, §13u.16; phase I4): the
cover page, the table of contents, the sections with their headings, text, figures and
tables, and the reference list.

`outline(plan, body_size)` reads `plan.sequence` (core/si_import.py: the SI's paragraphs,
general procedures and compounds in reading order) and fills in `plan.cover`,
`plan.sections` and `plan.references`:

* **Cover page** (everything before the table of contents, or before the first heading
  followed by body text when the SI starts "Supporting Information"): its title, authors
  (with their affiliation marks, * = corresponding) and affiliations, for the project's
  own cover page.
* **Table of contents**: from its heading to the first line that is not an entry (a
  line with dot leaders or a page number at its end, or a heading repeated further on).
  Not imported (the program makes its own); listed so it can be.
* **Heading levels**: Word's heading styles where the SI uses them (a bold paragraph
  without a style sits one level below the styled heading before it); else numbering
  ("1.", "I.", "S1." → 1; "1.1" → 2; "1.1.1" → 3; unnumbered headings one level below
  the numbered one before them); else font sizes (largest = 1); else all level 1. (The
  review window can change any heading's level.)
* **Sections**: each level-1 heading starts one. A section without compounds is a
  custom section (its lower headings are headings in its text); the first one named
  like "General Information" / "Materials and Methods" goes to the front matter's
  General Information page; "References" is the reference list; a section of spectra
  ("NMR Spectra", "Copies of spectra") is not imported (the program prints its own
  Spectra section). A section with compounds or general procedures is a Data section,
  or, when it also has text, a custom section holding the text, with its compounds in a
  Data sub-section ("Compounds"); its level-2 headings each start a sub-section.
* **Figures and tables**: a Word picture or embedded drawing in the text becomes a
  figure; a Word table a table (cells, merged cells, footnotes under it). The caption
  next to it ("Figure S2. …", "Scheme 1:", "Table S1.") becomes its caption, and its
  label as printed ("Figure S2") its label, so the SI prints as before. A PDF holds no
  pictures to take: a figure is cut out of its page above its caption (a table below
  its caption) later (core/si_import_pdf.add_figures, with Qt).

Pure Python (no Qt): unit-tested with made-up SIs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .markup import Paragraph, Run, serialize

# ---------------------------------------------------------------------- results


@dataclass
class ImportedFigure:
    key: str                    # "fig1", "tab2", … (the placeholder in its section's text)
    kind: str                   # "picture" (a Word picture), "object" (an embedded
                                # drawing's picture, EMF), "table" (a Word table), "cut"
                                # (to cut out of a PDF page)
    data: bytes | None = None
    ext: str = ""               # the picture's type: ".png", ".emf", ".pdf"…
    caption: str = ""           # markup, without its label
    label: str = ""             # as printed: "Figure S2", "Scheme 1", "Table S1"
    table: dict | None = None   # a table (core/tables.py JSON form)
    page: int = 0               # PDF: the page, and the part of it to cut out (pt)
    top: float = 0.0
    bottom: float = 0.0
    is_table: bool = False      # (a PDF table: cut out below its caption)
    keep: bool = True
    width_cm: float | None = None   # its printed width in the SI (None: unknown)
    below: tuple | None = None      # PDF: (top, bottom) under its caption, tried second,
    below_labels: list = field(default_factory=list)  # …the short lines there (its labels
    cut_below: bool = False         # if it was cut from there: then left out of the text)


@dataclass
class ImportedSection:
    key: str                    # "x1", …
    kind: str                   # "general", "text", "data", "references", "spectra", "toc"
    name: str                   # its heading without numbering
    heading: str = ""           # as printed
    parent: str = ""            # the key of the section it sits in ("" = top level)
    text: str = ""              # markup; figures as "!figure{key} caption" lines
    figures: dict[str, ImportedFigure] = field(default_factory=dict)
    compounds: list = field(default_factory=list)   # si_import.ImportedCompound
    gps: list = field(default_factory=list)         # si_import.ImportedGP
    keep: bool = True
    note: str = ""              # what the review window says about it


@dataclass
class ImportedCover:
    title: str = ""
    authors: list[dict] = field(default_factory=list)       # project.json "authors" rows
    affiliations: list[dict] = field(default_factory=list)  # {"marker", "text"}
    lines: list[str] = field(default_factory=list)          # the cover as printed
    keep: bool = True


# ---------------------------------------------------------------------- patterns

_TOC = re.compile(r"^(?:table\s+of\s+)?contents\.?:?$", re.I)
_SI_LINE = re.compile(r"^(?:electronic\s+)?(?:supporting|supplementary|supplemental)\s+"
                      r"(?:information|materials?|data|info)\b.{0,40}$", re.I)
_LEADERS = re.compile(r"(?:\.\s?){5,}|…{2,}|_{5,}")
_PAGE_AT_END = re.compile(r"(?:\s|\.)(?:S|SI-?)?\d{1,3}$")
_NUM = re.compile(r"^(?P<num>(?:S|SI-?)?\d{1,2}(?:\.\d{1,2}){0,2}|[IVX]{1,5}|[A-H])"
                  r"(?P<dot>[.)]?)\s+(?=\S)")
_SUPPLEMENTARY = re.compile(r"^(?:supplementary|supporting)\s+(?:note|discussion|methods?|"
                            r"figures?|tables?|text)\b", re.I)
_GENERAL = re.compile(r"^(?:general(?:\s+(?:information|remarks?|methods?|considerations|"
                      r"notes?|experimental(?:\s+\w+)?|procedures?\s+and\s+methods|"
                      r"materials\s+and\s+methods|aspects|details))?|"
                      r"(?:supplementary\s+)?materials\s+and\s+methods|"
                      r"experimental\s+(?:section|details|methods|procedures)?|methods)\b",
                      re.I)
_REFERENCES = re.compile(r"^(?:(?:supplementary|supplemental|supporting|SI)\s+)?"
                         r"(?:references?(?:\s+and\s+notes)?|notes\s+and\s+references|"
                         r"bibliography|literature(?:\s+cited)?|cited\s+literature)\s*:?$", re.I)
_SPECTRA = re.compile(r"\b(?:spectra|traces|chromatograms|copies\s+of)\b|"
                      r"\b\d{1,3}\s?[A-Z][a-z]?\s?(?:\{[^}]*\})?\s?NMR\s*\(", re.I)
# (a spectrum's own header: "6a ¹H NMR (600 MHz, CDCl3)")
CAPTION = re.compile(r"^(?P<label>(?:(?:Supplementary|Supporting)\s+)?(?:Figure|Fig\.|Scheme|"
                     r"Chart|Table|Graph)\s+S?\d+[A-Za-z]?)\s*(?:[.:|—–-]\s*)?(?P<rest>.*)$",
                     re.S)


def strip_number(text: str) -> str:
    """"1.2. Synthesis" → "Synthesis"; "IV. Experimental" → "Experimental" (the program
    numbers its sections itself)."""
    m = _NUM.match(text)
    if m and (m.group("dot") or re.match(r"(?:S|SI-?)?\d", m.group("num"))):
        return text[m.end():].strip(" .:")
    return text.strip(" :")


def _num_level(text: str) -> int:
    m = _NUM.match(text)
    if not m:
        return 0
    num = m.group("num")
    if re.fullmatch(r"[A-H]", num) and (not m.group("dot") or not text[m.end():][:1].isupper()):
        return 0                        # ("A General Method", "E. coli": not numbered)
    if re.fullmatch(r"[IVX]{1,5}", num) and not m.group("dot"):
        return 0
    return num.count(".") + 1


def _norm(text: str) -> str:
    text = _LEADERS.sub(" ", text)
    text = _PAGE_AT_END.sub("", text.strip())
    return re.sub(r"\W+", " ", strip_number(text)).strip().lower()


# ---------------------------------------------------------------------- headings


def heading_levels(paras: list, body_size: float) -> dict[int, int]:
    """{id(para): level 1–3} for the paragraphs that are headings (see the module's
    text). Captions and table cells are never headings."""
    from .si_import import is_section_heading
    heads = [p for p in paras if not p.table and not CAPTION.match(p.text)
             and is_section_heading(p, body_size)]
    out: dict[int, int] = {}
    if any(p.heading for p in heads):
        last = 0
        for p in heads:
            if p.heading:
                last = min(3, p.heading)
                out[id(p)] = last
            else:
                out[id(p)] = min(3, last + 1) if last else 1
        return out
    if sum(1 for p in heads if _num_level(p.text) == 1) >= 2:
        last = 0
        for p in heads:
            n = _num_level(p.text)
            if n:
                last = n
                out[id(p)] = n
            elif _SUPPLEMENTARY.match(p.text):
                out[id(p)] = last = 1
            else:
                out[id(p)] = min(3, last + 1) if last else 1
        return out
    sizes: list[float] = []
    for s in sorted({round(p.size) for p in heads if p.size}, reverse=True):
        sizes.append(s)
    rank = {s: min(3, i + 1) for i, s in enumerate(sizes)} if len(sizes) >= 2 else {}
    for p in heads:
        out[id(p)] = rank.get(round(p.size), 1) if rank else 1
    return out


# ---------------------------------------------------------------------- cover and contents


def _front(seq: list, levels: dict[int, int]) -> tuple[int, int]:
    """(where the cover ends, where the table of contents ends) in `seq`."""
    first_block = next((i for i, (k, _o) in enumerate(seq) if k != "para"), len(seq))
    paras = [(i, o) for i, (k, o) in enumerate(seq[:first_block]) if k == "para"]
    toc = next((i for i, p in paras[:80] if _TOC.match(p.text.strip())), None)
    if toc is not None:
        return toc, _toc_end(seq, toc, first_block, levels)
    if not any(_SI_LINE.match(p.text.strip()) for _i, p in paras[:4]):
        return 0, 0
    for k, (i, p) in enumerate(paras):
        if p.page > 1:
            return i, i
        if id(p) in levels:
            nxt = next((q for _j, q in paras[k + 1:] if id(q) not in levels and q.text), None)
            if nxt is not None and len(nxt.text) >= 150:
                return i, i
    return first_block, first_block


def _toc_end(seq: list, toc: int, limit: int, levels: dict[int, int]) -> int:
    heads_after = [(j, _norm(o.text)) for j, (k, o) in enumerate(seq)
                   if j > toc and k == "para" and id(o) in levels]
    # Where the first entry comes again as a heading, the SI's text starts.
    first = next((o for k, o in seq[toc + 1:limit] if k == "para" and o.text.strip()), None)
    target = _norm(first.text) if first is not None else ""
    if target:
        again = next((j for j, n in heads_after if n == target and seq[j][1] is not first
                      and j < limit), None)
        if again is not None:
            return again
    i = toc + 1
    while i < limit:
        kind, p = seq[i]
        if kind != "para":
            break
        t = p.text.strip()
        if not t or _LEADERS.search(t) or (_PAGE_AT_END.search(t) and len(t) < 160):
            i += 1
            continue
        norm = _norm(t)
        if norm and any(j > i and n == norm for j, n in heads_after):
            i += 1             # (an entry without a page number: the heading comes again)
            continue
        break
    return i


def read_cover(paras: list, levels: dict[int, int]) -> ImportedCover:
    """The cover page's title, authors and affiliations (see the module's text)."""
    cover = ImportedCover(lines=[p.text for p in paras if p.text])
    rest = [p for p in paras if p.text and not _SI_LINE.match(p.text.strip())]
    if not rest:
        return cover
    # the title: the first heading-like paragraph(s) — bold, larger, or styled
    k = 0
    while k < len(rest) and not _title_like(rest[k], levels) and k < 2:
        k += 1
    if k >= len(rest) or not _title_like(rest[k], levels):
        k = 0
    title = [rest[k].text]
    k += 1
    while k < len(rest) and _title_like(rest[k], levels) and not _author_line(rest[k]) \
            and abs((rest[k].size or 0) - (rest[k - 1].size or 0)) < 0.6:
        title.append(rest[k].text)
        k += 1
    cover.title = re.sub(r"\s+", " ", " ".join(title)).strip()
    if k < len(rest) and _author_line(rest[k]):
        runs = list(rest[k].runs)
        k += 1
        while k < len(rest) and not _affiliation_like(rest[k]) and \
                not re.search(r"@|e-?mail", rest[k].text, re.I) and len(rest[k].text) < 200 and \
                all(re.fullmatch(r"(?:[A-ZÀ-Þ][\w.'’\-]*|and|&)[*†‡§#+,]*", w)
                    for w in rest[k].text.split()):
            runs += [Run(" ")] + list(rest[k].runs)     # (a list running over two lines)
            k += 1
        cover.authors = _authors(runs)
    emails: list[str] = []
    for p in rest[k:]:
        found = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", p.text)
        if found or re.match(r"^\W*(?:e-?mail|corresponding|\*)", p.text, re.I):
            emails += found
            continue
        if _affiliation_like(p):
            marker, text = _marker_and_text(p.runs)
            if not marker and cover.affiliations and not cover.affiliations[-1]["marker"]:
                cover.affiliations[-1]["text"] += ", " + text      # (a second line)
            else:
                cover.affiliations.append({"marker": marker, "text": text})
    # Each e-mail address to the corresponding author whose name is in it ("xiacf@…" →
    # Chengfeng Xia); the others in order.
    corresponding = [a for a in cover.authors if a["corresponding"]]
    left = []
    for mail in emails:
        local = re.sub(r"[^a-z]", "", mail.split("@")[0].lower())
        match = [a for a in corresponding if not a["email"] and any(
            len(w) >= 3 and w in local for w in re.findall(r"[a-z]+", _ascii(a["name"]).lower()))]
        if len(match) == 1:
            match[0]["email"] = mail
        else:
            left.append(mail)
    for a, mail in zip([a for a in corresponding if not a["email"]], left):
        a["email"] = mail
    return cover


def _ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _title_like(p, levels: dict[int, int]) -> bool:
    return id(p) in levels or p.bold_share() >= 0.6 or bool(p.heading)


def _author_line(p) -> bool:
    text = p.text
    if len(text) > 400 or re.search(r"@|\bUniversity\b|\bDepartment\b|\bInstitute\b", text):
        return False
    names = [n for n in re.split(r",|\band\b|&|;", re.sub(r"[*†‡§#+]", "", text)) if n.strip()]
    capital = sum(1 for n in names if re.fullmatch(r"\s*(?:[A-Z][\w.'’\-]*\s+){0,4}[A-Z][\w'’\-]+"
                                                  r"\s*[\w,*†‡]{0,4}\s*", n))
    sup = any(r.valign == "super" for r in p.runs)
    return len(names) >= 1 and capital >= max(1, 0.7 * len(names)) and (len(names) >= 2 or sup)


def _affiliation_like(p) -> bool:
    first = next((r for r in p.runs if r.text.strip()), None)
    return bool(first is not None and first.valign == "super") or bool(re.search(
        r"\b(?:Universit\w*|Department|Dept\.|Institut\w*|Laborator\w*|School|College|"
        r"Cent(?:er|re)|Faculty|Hospital|Academy|Inc\.|Ltd|GmbH|Street|Road|Avenue|"
        r"Building|\d{5})\b", p.text))


def _marker_and_text(runs: list[Run]) -> tuple[str, str]:
    runs = [r for r in runs if r.text]
    if runs and runs[0].valign == "super":
        marker = runs[0].text.strip(" ,")
        return marker, "".join(r.text for r in runs[1:]).strip(" ,;")
    m = re.match(r"^\s*(?:([a-z]|\d{1,2})\s+|([*†‡§]{1,2})\s*)(?=[A-Z])",
                 "".join(r.text for r in runs))
    text = "".join(r.text for r in runs)
    if m:
        return (m.group(1) or m.group(2)), text[m.end():].strip(" ,;")
    return "", text.strip(" ,;")


def _authors(runs: list[Run]) -> list[dict]:
    """"Alex Example^{a} and Blair Sample^{a,*}" → author rows (project.json form)."""
    out: list[dict] = []
    name, marks, star = "", [], False

    def close():
        nonlocal name, marks, star
        n = re.sub(r"\s+", " ", name).strip(" ,;")
        if n and re.search(r"[A-Za-z]", n):
            out.append({"name": n, "affiliations": [m for m in marks if m],
                        "corresponding": star, "email": ""})
        name, marks, star = "", [], False

    for r in runs:
        if r.valign == "super":
            for m in re.split(r"[,\s]+", r.text):
                if not m:
                    continue
                if "*" in m:
                    star = True
                m = re.sub(r"[*†‡§#+]", "", m)
                if m:
                    marks.append(m)
            continue
        for piece in re.split(r"(,|\band\b|&|;)", r.text):
            if piece in (",", "and", "&", ";"):
                close()
            else:
                if "*" in piece:
                    star = True
                name += re.sub(r"[*†‡§#+]", "", piece)
    close()
    return out


# ---------------------------------------------------------------------- the sections


def outline(plan, body_size: float) -> None:
    """Fill in plan.cover, plan.sections and plan.references (see the module's text)."""
    from . import si_import_refs
    seq = plan.sequence
    paras = [o for k, o in seq if k == "para"]
    first = heading_levels(paras, body_size)
    cover_end, toc_end = _front(seq, first)
    plan.cover = None
    if cover_end:
        cover = read_cover([o for k, o in seq[:cover_end] if k == "para"], first)
        if cover.title or cover.authors:
            plan.cover = cover
    # (the levels again without the cover and contents: their large type would count)
    levels = heading_levels([o for k, o in seq[toc_end:] if k == "para"], body_size)
    for k, o in seq[toc_end:]:            # (the reference list is always a section)
        if k == "para" and id(o) in levels and _REFERENCES.match(strip_number(o.text)):
            levels[id(o)] = 1
    overrides = getattr(plan, "level_overrides", {}) or {}
    plan.headings = [o for k, o in seq[toc_end:] if k == "para" and
                     (id(o) in levels or id(o) in overrides)]
    for key, level in overrides.items():   # (levels changed in the review window)
        if level:
            levels[key] = level
        else:
            levels.pop(key, None)
    plan.levels = levels
    old_sections = {_identity(s, plan.sections): s for s in getattr(plan, "sections", []) or []}
    old_refs = {(r.number, r.text): r for r in getattr(plan, "references", []) or []}
    sections: list[ImportedSection] = []
    counter = [0]

    def new(kind: str, name: str, **kw) -> ImportedSection:
        counter[0] += 1
        s = ImportedSection(f"x{counter[0]}", kind, name, **kw)
        sections.append(s)
        return s

    if toc_end > cover_end:
        toc = [o for k, o in seq[cover_end:toc_end] if k == "para"]
        new("toc", strip_number(toc[0].text) or "Table of Contents", heading=toc[0].text,
            text="\n".join(p.markup for p in toc[1:]), keep=False,
            note="The program makes its own table of contents, so this one is not imported.")
    chapters: list[tuple[object, list]] = []
    head, items = None, []
    for kind, obj in seq[toc_end:]:
        if kind == "para" and levels.get(id(obj)) == 1:
            if head is not None or items:
                chapters.append((head, items))
            head, items = obj, []
        else:
            items.append((kind, obj))
    if head is not None or items:
        chapters.append((head, items))
    builder = _Builder(levels, new, plan)
    general_done = False
    for n, (head, items) in enumerate(chapters):
        name = strip_number(head.text) if head is not None else ""
        has_data = any(k in ("compound", "gp") for k, _o in items)
        texts = [o for k, o in items if k == "para"]
        if not has_data and head is not None and _REFERENCES.match(name):
            plan.references = [old_refs.get((r.number, r.text), r) for r in
                               si_import_refs.read_list([p.text for p in texts
                                                         if p.text and id(p) not in levels])]
            new("references", name, heading=head.text,
                note=f"{len(plan.references)} reference(s), listed under References.")
            continue
        if not has_data and head is not None and _SPECTRA.search(name):
            s = new("spectra", name, heading=head.text, keep=False,
                    note="The program prints the spectra in its own Spectra section, so this "
                         "section is not imported (tick it to import its text anyway).")
            s.text, s.figures = builder.text(items, 1, spectra=True)
            continue
        if not has_data:
            if head is None and not any(p.text or p.objects for p in texts):
                continue
            general = (not general_done and head is not None and n <= 3
                       and bool(_GENERAL.match(name)))
            general_done = general_done or general
            s = new("general" if general else "text", name or "Other text",
                    heading=head.text if head is not None else "",
                    note="Goes to the front matter's General Information page." if general
                    else "")
            s.text, s.figures = builder.text(items, 1)
            continue
        builder.data_chapter(head, name, items)
    for s in sections:        # (names and ticks changed in the review window are kept)
        old = old_sections.get(_identity(s, sections))
        if old is not None:
            s.name, s.keep = old.name, old.keep
    plan.sections = sections


def _identity(s: ImportedSection, sections: list) -> tuple:
    """What a section is, whatever its key: its kind, heading and the heading above it."""
    parent = next((p for p in sections if p.key == s.parent), None)
    return (s.kind, s.heading, parent.heading if parent is not None else "",
            s.name if not s.heading else "")


class _Builder:
    """Text, figures and Data sections from a run of the SI's items."""

    def __init__(self, levels: dict[int, int], new, plan):
        self.levels, self.new, self.plan = levels, new, plan
        self.n_fig = 0

    # ---- text

    def text(self, items: list, base: int, spectra: bool = False
             ) -> tuple[str, dict[str, ImportedFigure]]:
        """(markup, figures) of the paragraphs among `items`: headings below level
        `base` as "# "/"## " lines, pictures and tables as figure/table lines."""
        lines: list[str] = []
        figures: dict[str, ImportedFigure] = {}
        paras = [o for k, o in items if k == "para"]
        where = {id(o): k for k, (_kind, o) in enumerate(items)}
        pending: tuple[str, str] | None = None        # (label, caption) waiting for a figure
        i = 0
        while i < len(paras):
            p = paras[i]
            if p.cell is not None:                    # a Word table
                cells = []
                while i < len(paras) and paras[i].cell is not None and \
                        paras[i].cell[0] == p.cell[0]:
                    cells.append(paras[i])
                    i += 1
                fig = self._figure("table", figures, table=_table(cells))
                if pending and _is_table_label(pending[0]):   # (its caption, above it)
                    fig.label, fig.caption = pending
                    marker = _caption_marker(*pending)
                    if marker in lines:
                        lines.remove(marker)
                    pending = None
                caption = fig.caption
                notes = []
                while i < len(paras) and _footnote(paras[i]):
                    notes.append(paras[i].markup)
                    i += 1
                if fig.table is not None:
                    fig.table["footnotes"] = notes
                lines.append(f"!table{{{fig.key}}} {caption}".rstrip())
                continue
            i += 1
            m = CAPTION.match(p.text) if p.text else None
            if m:
                label = m.group("label")
                caption = _rest_markup(p, m)
                if p.page:                            # a PDF: cut out of the page later
                    fig = self._figure("cut", figures, page=p.page, label=label,
                                       caption=caption, is_table=_is_table_label(label))
                    if fig.is_table:
                        fig.top = p.bottom
                        fig.bottom = p.bottom
                        while i < len(paras) and _table_row(paras[i], self.levels) and \
                                paras[i].page == p.page:
                            fig.bottom = paras[i].bottom
                            i += 1
                    else:
                        fig.bottom = p.top
                        fig.top, labels = _top_above(items, where[id(p)], self.levels)
                        while lines and lines[-1] in labels:  # (drawn in the figure)
                            lines.pop()
                        # (if there is nothing above it, the figure may be below it)
                        below, fig.below_labels = _top_below(items, where[id(p)], self.levels)
                        fig.below = (p.bottom, below)
                    lines.append(f"!figure{{{fig.key}}} {caption}".rstrip())
                    continue
                last = lines[-1] if lines else ""
                target = re.match(r"!(figure|table)\{(\w+)\}$", last.strip())
                if target and not _is_table_label(label) and target.group(1) == "figure":
                    fig = figures[target.group(2)]
                    fig.label, fig.caption = label, caption
                    lines[-1] = f"!figure{{{fig.key}}} {caption}".rstrip()
                    continue
                pending = (label, caption)
                lines.append(_caption_marker(label, caption))
                continue
            level = self.levels.get(id(p))
            if level:
                runs = p.runs
                number = len(p.text) - len(strip_number(p.text).lstrip())
                if number and strip_number(p.text) != p.text.strip(" :"):
                    from .si_import import split_runs   # ("1.1 Solvents" → "Solvents")
                    runs = split_runs(runs, number)[1] or runs
                plain = [Run(r.text, False, r.italic, r.valign, r.ref, r.underline)
                         for r in runs if r.text]   # (a heading prints in its own style)
                if plain:
                    last = plain[-1]
                    plain[-1] = Run(last.text.rstrip().rstrip(":").rstrip(), False, last.italic,
                                    last.valign, last.ref, last.underline)
                lines.append("#" * max(1, min(2, level - base)) + " "
                             + serialize([Paragraph([r for r in plain if r.text])]).strip())
            elif p.text:
                lines.append(p.markup)
            for o in p.objects:
                kind = o.get("kind")
                if kind == "mnova" or not o.get("picture"):
                    continue                          # (spectra are imported on their own)
                if spectra and kind != "picture":
                    continue
                ext = _picture_ext(o.get("picture_name", ""), o["picture"])
                fig = self._figure("picture" if kind == "picture" and ext not in
                                   (".emf", ".wmf") else "object", figures,
                                   data=o["picture"], ext=ext)
                if o.get("width_in"):
                    fig.width_cm = round(o["width_in"] * 2.54, 2)
                if pending and not _is_table_label(pending[0]):
                    fig.label, fig.caption = pending
                    pending = None
                    # (the caption came first: its line becomes the figure's)
                    for k in range(len(lines) - 1, -1, -1):
                        if lines[k] == _caption_marker(fig.label, fig.caption):
                            del lines[k]
                            break
                lines.append(f"!figure{{{fig.key}}} {fig.caption}".rstrip())
        # captions that found no figure stay as text
        out = [_caption_text(line) for line in lines]
        return "\n".join(out).strip("\n"), figures

    def _figure(self, kind: str, figures: dict, **kw) -> ImportedFigure:
        self.n_fig += 1
        key = f"{'tab' if kind == 'table' else 'fig'}{self.n_fig}"
        fig = ImportedFigure(key, kind, **kw)
        figures[key] = fig
        return fig

    # ---- compounds

    def data_chapter(self, head, name: str, items: list) -> None:
        """A level-1 section with compounds (see the module's text)."""
        parts: list[tuple[object, list]] = [(None, [])]
        for kind, obj in items:
            if kind == "para" and self.levels.get(id(obj)) == 2:
                parts.append((obj, []))
            else:
                parts[-1][1].append((kind, obj))
        if not parts[0][1]:
            parts.pop(0)
        texty = any(_has_text(part, self.levels) for _h, part in parts) or len(parts) > 1
        name = name or self.plan.section_name or "Synthesis and Characterization"
        heading = head.text if head is not None else ""
        if not texty:
            self._data(name, items, "", heading)
            return
        parent = self.new("text", name, heading=heading)
        # General procedures in a part without compounds go with the next part's (or, at
        # the end, the last part's) compounds, so no Data section holds only procedures.
        gps_waiting: list = []
        for k, (h, part) in enumerate(parts):
            if not any(kind == "compound" for kind, _o in part):
                gps_waiting += [x for x in part if x[0] == "gp"]
                parts[k] = (h, [x for x in part if x[0] != "gp"])
            elif gps_waiting:
                parts[k] = (h, gps_waiting + part)
                gps_waiting = []
        only_gps: list = []
        if gps_waiting:
            for k in range(len(parts) - 1, -1, -1):
                if any(kind == "compound" for kind, _o in parts[k][1]):
                    parts[k] = (parts[k][0], parts[k][1] + gps_waiting)
                    break
            else:
                only_gps = gps_waiting       # (procedures, no compounds: a section of them)
        for h, part in parts:
            data = any(k in ("compound", "gp") for k, _o in part)
            if h is None:
                parent.text, parent.figures = self.text(part, 1)
                if data:
                    self._data("Compounds", part, parent.key)
                continue
            sub_name = strip_number(h.text)
            if not data:
                s = self.new("text", sub_name, heading=h.text, parent=parent.key)
                s.text, s.figures = self.text(part, 2)
            elif _has_text(part, self.levels):
                s = self.new("text", sub_name, heading=h.text, parent=parent.key)
                s.text, s.figures = self.text(part, 2)
                self._data("Compounds", part, s.key)
            else:
                self._data(sub_name, part, parent.key, h.text)
        if only_gps:
            self._data("General Procedures", only_gps, parent.key)

    def _data(self, name: str, items: list, parent: str, heading: str = "") -> ImportedSection:
        s = self.new("data", name, heading=heading, parent=parent)
        s.compounds = [o for k, o in items if k == "compound"]
        s.gps = [o for k, o in items if k == "gp"]
        return s


def _top_above(items: list, k: int, levels: dict[int, int]) -> tuple[float, set[str]]:
    """(where a PDF figure above its caption items[k] starts, the figure's own labels):
    below the last line of body text, heading, caption, general procedure or compound
    above it on its page; the short lines between are drawn in the figure (left out of
    the text)."""
    cap = items[k][1]
    labels: set[str] = set()
    j = k - 1
    while j >= 0:
        kind, q = items[j]
        if kind != "para":
            page, bottom = getattr(q, "end", (0, 0.0))
            if page == cap.page and bottom:
                return bottom + 4, labels
            if page and page != cap.page:
                break
            j -= 1
            continue
        if q.page != cap.page:
            break
        if levels.get(id(q)) or len(q.text) >= 90 or CAPTION.match(q.text):
            return q.bottom + 4, labels
        labels.add(q.markup)
        j -= 1
    return 0.0, labels                    # (from the top of the page's text)


def _top_below(items: list, k: int, levels: dict[int, int]) -> tuple[float, list[str]]:
    """(where the next text under a PDF caption items[k] starts on its page — 0.0: none,
    to the foot of the page; -1: unknown, not to be tried —, the short lines between,
    which would be the figure's own labels if it is below its caption)."""
    cap = items[k][1]
    labels: list[str] = []
    for kind, q in items[k + 1:]:
        if kind == "para":
            if q.page != cap.page:
                return 0.0, labels
            if levels.get(id(q)) or len(q.text) >= 90 or CAPTION.match(q.text):
                return q.top, labels
            labels.append(q.markup)
            continue
        geometry = getattr(q, "geometry", None)       # (a compound: above its heading)
        if geometry and geometry[0] == cap.page:
            return max(cap.bottom, geometry[1] - 16), labels
        return (-1.0 if getattr(q, "end", (0, 0))[0] == cap.page else 0.0), labels
    return 0.0, labels


def _has_text(items: list, levels: dict[int, int]) -> bool:
    return any(k == "para" and (o.text or any(x.get("kind") != "mnova" and x.get("picture")
                                              for x in o.objects) or o.cell is not None)
               for k, o in items)


def _rest_markup(p, m: re.Match) -> str:
    from .si_import import split_runs
    cut = len(p.text) - len(m.group("rest"))
    _before, after = split_runs(p.runs, cut)
    return serialize([Paragraph(after)]).strip() if after else ""


def _is_table_label(label: str) -> bool:
    return bool(re.search(r"\bTable\b", label))


def _caption_marker(label: str, caption: str) -> str:
    return f"\x00{label}\x00{caption}"


def _caption_line(line: str) -> tuple[str, str] | None:
    if line.startswith("\x00"):
        _e, label, caption = line.split("\x00", 2)
        return label, caption
    return None


def _caption_text(line: str) -> str:
    """A caption that found no figure: its text as printed."""
    found = _caption_line(line)
    if found is None:
        return line
    label, caption = found
    return f"**{label}.** {caption}".strip()


def _footnote(p) -> bool:
    first = next((r for r in p.runs if r.text.strip()), None)
    return (first is not None and first.valign == "super" and len(p.text) < 400) or \
        bool(re.match(r"^[a-h]\s[A-Z]", p.text) and len(p.text) < 300)


def _table_row(p, levels: dict[int, int]) -> bool:
    """A PDF paragraph that is part of a table under its caption: not a heading, not a
    caption, not prose (a long run of words ending with a full stop)."""
    if levels.get(id(p)) or CAPTION.match(p.text):
        return False
    if _footnote(p) or getattr(p, "columnar", False):
        return True
    words = p.text.split()
    letters = sum(1 for w in words if re.fullmatch(r"[A-Za-z][a-z]+[,.;:]?", w))
    prose = len(p.text) >= 150 and p.text.rstrip().endswith(".") and \
        letters >= 0.6 * max(1, len(words))
    return not prose


def _picture_ext(name: str, data: bytes) -> str:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in (".jpeg",):
        return ".jpg"
    if ext:
        return ext
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[40:44] == b" EMF":
        return ".emf"
    return ".png"


def _table(cells: list) -> dict:
    """A Word table's cells (paragraphs with .cell) as a table (core/tables.py JSON)."""
    by_pos: dict[tuple[int, int], dict] = {}
    for p in cells:
        _t, r, c, span, merge = p.cell
        cell = by_pos.setdefault((r, c), {"texts": [], "span": span, "merge": merge,
                                          "bold": True})
        if p.text:
            cell["texts"].append(p.markup)
            cell["bold"] = cell["bold"] and p.bold_share() > 0.9
    if not by_pos:
        return {}
    n_rows = max(r for r, _c in by_pos) + 1
    n_cols = max(c + v["span"] for (_r, c), v in by_pos.items())
    rows: list[list] = [[{"text": ""} for _ in range(n_cols)] for _ in range(n_rows)]
    for (r, c), v in sorted(by_pos.items()):
        if v["merge"] == "continue":
            rows[r][c] = None
            k = r - 1                          # (the cell this one is merged into)
            while k >= 0 and rows[k][c] is None:
                k -= 1
            if k >= 0 and rows[k][c] is not None:
                rows[k][c]["rowspan"] = rows[k][c].get("rowspan", 1) + 1
        else:
            text = " ".join(v["texts"])
            rows[r][c] = {"text": text}
            if v["span"] > 1:
                rows[r][c]["colspan"] = v["span"]
        for extra in range(1, v["span"]):
            if c + extra < n_cols:
                rows[r][c + extra] = None
    header = 1 if n_rows > 1 and all(by_pos[(0, c)]["bold"] and by_pos[(0, c)]["texts"]
                                     for (r, c) in by_pos if r == 0) else 0
    return {"header_rows": header, "columns": ["center"] * n_cols, "rows": rows,
            "footnotes": []}
