"""Assembling the whole SI as one Typst document (SPEC §9.1, §13k.4–5).

    cover page (its own page), table of contents (new page), General Information
    every section and sub-section in tree order (text, files, compounds and scheme
        pages); a section at depth 1/2/3 gets a level 1/2/3 heading
    the Spectra section where it sits in the tree (program-added headers, structures and
        IDs, laid out as its defaults and each spectrum's own settings say, SPEC §13k.12);
        a project without one (opened read-only) gets the spectra at the end, as before
    page numbers S1, S2, … throughout
    anything unticked in the Compile window (export_excluded) is left out

Everything is written to build/: SI.typ (readable, for advanced users), style.typ and
assets/ (structure drawings). Typst's root is the project folder, so every path in
the document is relative to it.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from .. import resources
from ..core import chem
from ..core import drawing_scale
from ..core import fields as F
from ..core import front
from ..core import labels as label_rules
from ..core import ms_reports
from ..core import page as page_setup
from ..core import scheme_editor
from ..core import spectra as spectra_mod
from ..core.markup import Paragraph, Run, mention_kind, parse
from ..core.project import (BUILD_DIR, GENERAL_OWNER, SPECTRA_SECTION, Compound, Project,
                            SchemePage, Section, TextOwner, figure_width_cm)
from ..core.tables import Table
from ..core.references import to_hayagriva
from .blocks import CompoundBlock, compound_block, gp_sentence
from .reaction_scheme import editor_scheme
from .text import (aligned_object, format_chemistry, format_nmr, leave_alone,
                   paragraphs_to_typst, resolve_links, runs_to_typst, typst_label, typst_string)

STYLES_DIR = resources.resource("styles")
REFERENCES_YML = "references.yml"

TEXT_WIDTH_IN = 6.5           # US letter with 1 in margins
STRUCTURE_MAX = (3.25, 2.6)   # inches (width, height) for structures under compound titles
ATTACHMENT_MAX_HEIGHT_IN = 8.0   # an Attachment section's PDF page or picture, at most
CM_PER_IN = 2.54
TYPST_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}
CORNERS = {"top-left": "top + left", "top-right": "top + right",
           "bottom-left": "bottom + left", "bottom-right": "bottom + right"}
SPECTRA_HEADING = "Spectra"   # the heading of the spectra of a preview (or of a project
                              # without a Spectra section)
HRMS_HEADING = "HRMS reports"   # the Spectra section's sub-section of HRMS reports (§13n.3)
HRMS_PAGE_IN = 7.4              # a report's PDF page or picture: at most this high (inches)
HRMS_KEEP_LINES = 70            # a listing this short is kept on one page
PAGE_BREAK = "#pagebreak(weak: true)"

# Links in the PDF (SPEC §13l.7). `si-link` makes a link only when its target is in the
# document exactly once (a spectrum not printed, or a preview without it, leaves the text
# as it is), so a missing target can never stop Typst. How a link looks: Spectra section
# ▸ Link appearance.
LINK_COLOUR = "#0563c1"
LINK_LOOKS = {"plain": "body",
              "blue": f'text(fill: rgb("{LINK_COLOUR}"), body)',
              "blue_underline": f'text(fill: rgb("{LINK_COLOUR}"), underline(body))'}


def link_definition(style: str) -> str:
    look = LINK_LOOKS.get(style, LINK_LOOKS["plain"])
    return ("#let si-link(target, body) = context {\n"
            f"  if query(target).len() == 1 {{ link(target, {look}) }} else {{ body }}\n}}")


@dataclass
class CompileOptions:
    structures: str = "pictures"       # "pictures": the pasted ChemDraw (or own) picture
                                       # where a compound has one, the program's drawing
                                       # otherwise (SPEC §13n.2); "acs": program drawings
                                       # (ACS 1996) for every compound
    format_text: bool = True
    # Preview one page (PREVIEW_KINDS): ("front", "cover"|"toc"|"general"), ("section", id)
    # (a section with its sub-sections), ("compound", id), ("scheme", id) or ("spectra",
    # compound id) (a compound's spectra pages), ("hrms", "") (the Spectra section's HRMS
    # reports). Exclusions are then ignored.
    only: tuple[str, str] | None = None
    # A preview of a compound, scheme or section also shows its compounds' spectra (the
    # toolbar Preview); the live preview pane leaves them out (SPEC §13k.8).
    preview_spectra: bool = True
    # A preview of the Spectra section prints at most this many spectra (0 = all): the
    # live preview pane only needs a sample to show the layout.
    spectra_limit: int = 0
    # Left out of the full SI (SPEC §13k.10): front pages, "spectra" (older key for the
    # Spectra section), section IDs.
    excluded: frozenset[str] = frozenset()

    @classmethod
    def from_settings(cls, settings: dict) -> "CompileOptions":
        return cls(structures=settings.get("compile_structures", "pictures"),
                   format_text=bool(settings.get("format_text", True)),
                   excluded=frozenset(front.excluded(settings)))


# CompileOptions.only kinds; ("spectra", compound id) = that compound's spectra pages.
PREVIEW_KINDS = ("front", "section", "compound", "scheme", "spectra", "hrms")
TOC_END_LABEL = "si-toc-end"   # marks the last page of the table of contents (its preview)


@dataclass
class BuildResult:
    typ_path: Path
    warnings: list[str] = field(default_factory=list)
    empty: bool = False   # a preview with nothing that would be printed
    # A preview to be shown only up to the page holding this label ("" = every page):
    # the table of contents, which is laid out with the whole SI after it.
    last_page_label: str = ""


# Inches per unit of an SVG's width/height. A plain number or "px" counts as 1 pt, as the
# program's own structure drawings have always been sized (SPEC §13a); spectra pasted from
# Mnova give theirs in pt (core/emf_svg.py).
_SVG_UNITS_IN = {"": 1 / 72, "px": 1 / 72, "pt": 1 / 72, "pc": 1 / 6, "mm": 1 / 25.4,
                 "cm": 1 / 2.54, "in": 1.0}
_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)
_SVG_LENGTH = r"""\s{name}\s*=\s*['"]\s*([\d.]+(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*['"]"""


def svg_size_in(text: str) -> tuple[float, float] | None:
    """An SVG's natural size in inches, from the width and height of its <svg> element
    (pt, px, mm, cm, in; a percentage or none: from its viewBox, 1 unit = 1 pt)."""
    tag = _SVG_TAG.search(text)
    if tag is None:
        return None
    root = tag.group(0)
    size = []
    for name in ("width", "height"):
        m = re.search(_SVG_LENGTH.format(name=name), root)
        if m is None or m.group(2) not in _SVG_UNITS_IN:
            size = []
            break
        size.append(float(m.group(1)) * _SVG_UNITS_IN[m.group(2)])
    if len(size) == 2 and size[0] > 0 and size[1] > 0:
        return size[0], size[1]
    box = re.search(r"""\sviewBox\s*=\s*['"]\s*([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+"""
                    r"""([\d.eE+]+)[\s,]+([\d.eE+]+)\s*['"]""", root)
    if box is not None:
        w, h = float(box.group(3)), float(box.group(4))
        if w > 0 and h > 0:
            return w / 72, h / 72
    return None


def pdf_size_in(path: Path) -> tuple[float, float] | None:
    """The size of a PDF's first page in inches, as Typst prints it (its CropBox, turned
    by /Rotate); None if it can't be read."""
    try:
        from pypdf import PdfReader
        page = PdfReader(str(path)).pages[0]
        box = page.cropbox
        w, h = abs(float(box.width)), abs(float(box.height))
        if (page.rotation or 0) % 180:
            w, h = h, w
    except Exception:  # noqa: BLE001 — unknown size: the caller falls back
        return None
    return (w / 72, h / 72) if w > 0 and h > 0 else None


# Structure drawings kept between builds, so the live preview (which builds the whole SI
# for the table of contents) doesn't redraw every structure each time. Keyed by the
# molecule object, which the project keeps (and replaces) until structure.mol changes; the
# entry holds the molecule, so its id is never reused while it is in here.
_DRAWN: dict[tuple[int, chem.DrawingSettings], tuple[object, str, tuple | None]] = {}
_DRAWN_MAX = 3000


def drawn_structure(mol, drawing: chem.DrawingSettings) -> tuple[str, tuple | None]:
    """(SVG text, natural size in inches) of `mol` drawn with `drawing`."""
    key = (id(mol), drawing)
    hit = _DRAWN.get(key)
    if hit is not None and hit[0] is mol:
        return hit[1], hit[2]
    text = chem.structure_svg(mol, settings=drawing)
    if len(_DRAWN) >= _DRAWN_MAX:
        _DRAWN.clear()
    _DRAWN[key] = (mol, text, svg_size_in(text))
    return text, _DRAWN[key][2]


class Builder:
    def __init__(self, project: Project, options: CompileOptions, image_info,
                 folder: Path | None = None, root: Path | None = None):
        """`image_info(path) -> (width_px, height_px, dpi) | None` reads raster images
        (supplied by the UI layer, which has Qt); `convert(path, dest)` is optional.

        The source, style and drawings go to `folder` (default: the project's build/).
        `root` is Typst's root folder (default: the project folder); it must contain both
        the project and `folder` (the live preview builds in a temporary folder)."""
        self.project = project
        self.options = options
        self.image_info = image_info
        self.build = Path(folder) if folder is not None else project.folder / BUILD_DIR
        self.root = Path(root) if root is not None else project.folder
        self.assets = self.build / "assets"
        self.empty = False
        self.last_page_label = ""   # see BuildResult
        self.svg_sizes: dict[Path, tuple[float, float] | None] = {}   # drawn structures
        self.warnings: list[str] = []
        self.report = project.id_report()
        self.converter = None
        # Auto-crop (SPEC §13k.12.8): where each spectrum page's content is, measured by
        # the UI layer (it needs Qt) and remembered in build/crop_cache.json.
        self.crops = spectra_mod.CropCache(project)
        self.spectra_compounds: list[Compound] = []   # compounds the Spectra section prints
        self.ref_ids = {r["id"] for r in project.references}
        self.cited = False   # any citation printed → add the References section
        self.drawing = chem.DrawingSettings.from_settings(project.settings)
        self.today = date.today()   # for {date} in the header/footer
        self.toc = front.toc_settings(project.settings)
        self.toc_rank = front.toc_rank(self.toc["depth"])   # 1 sections … 4 compounds
        self.kinds: Counter[str] = Counter()   # figures printed, by Typst figure kind
        # Figure, table and scheme labels, worked out once (SPEC §13k.5a).
        self.labels = label_rules.compute(project)
        # Links from the peak reports to the spectra, and back (SPEC §13l.7): the Spectra
        # section's settings (a project without one has none).
        links = project.spectra_defaults() if project.spectra_section is not None else {}
        self.nmr_links = bool(links.get("nmr_links"))
        self.link_back = self.nmr_links and bool(links.get("link_back"))
        self.link_style = str(links.get("link_style", "plain"))
        self.placed_labels: set[str] = set()   # Typst labels put in (each only once)

    # ------------------------------------------------------------------ links

    def mention_text(self, item_id: str) -> str | None:
        """A compound's current ID, or a figure's, table's or scheme's label."""
        if mention_kind(item_id) != "compound":
            return self.labels.label(item_id)
        try:
            self.project.compound(item_id)
        except KeyError:
            return None
        return self.report.display_id(item_id)

    def links(self, runs: list[Run], where: str) -> list[Run]:
        """Mentions → current IDs, citations checked (SPEC §7.5, §9.5)."""
        problems: list[str] = []
        out = resolve_links(runs, self.mention_text, self.ref_ids, problems)
        for p in dict.fromkeys(problems):
            self.warnings.append(f"{where}: {p}")
        if any(r.ref for r in out):
            self.cited = True
        return out

    def link_paragraphs(self, paragraphs: list[Paragraph], where: str) -> list[Paragraph]:
        return [replace(p, runs=self.links(p.runs, where)) for p in paragraphs]

    # ------------------------------------------------------------------ links in the PDF

    @staticmethod
    def compound_label(compound: Compound) -> str:
        """The Typst label on a compound's block (its title)."""
        return "si-c-" + typst_label(compound.id)

    @staticmethod
    def spectrum_label(compound: Compound, nucleus: str) -> str:
        """The Typst label on the compound's first printed spectrum of `nucleus`."""
        return f"si-s-{typst_label(compound.id)}-{typst_label(nucleus)}"

    def place_label(self, label: str) -> bool:
        """True (and remembered) the first time `label` is put in the document: a label
        must be there only once, or Typst can't link to it."""
        if label in self.placed_labels:
            return False
        self.placed_labels.add(label)
        return True

    def nmr_link(self, compound: Compound):
        """For compound_block: nucleus -> the label of the compound's spectrum of that
        nucleus ("" when it has none). Whether that spectrum is printed in this document
        is left to si-link."""
        if not self.nmr_links:
            return None
        nuclei = {spectra_mod.nmr_nucleus(item, compound)
                  for _key, item in spectra_mod.spectrum_items(self.project, compound)}
        return lambda nucleus: (self.spectrum_label(compound, nucleus)
                                if nucleus in nuclei else "")

    # ------------------------------------------------------------------ paths and images

    def rel(self, path: Path) -> str:
        """Path as Typst sees it: from Typst's root folder, with forward slashes."""
        return "/" + Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def usable_image(self, path: Path) -> Path | None:
        if not path.exists():
            self.warnings.append(f"Missing file (left out): {path.relative_to(self.project.folder)}")
            return None
        if (path.suffix.lower() == ".svg" and path not in self.svg_sizes   # (not our drawings)
                and not spectra_mod.svg_readable(path)):
            # (a damaged SVG would stop Typst from making the PDF at all)
            self.warnings.append(f"{path.name} could not be read as an SVG drawing (it may be "
                                 "damaged); left out.")
            return None
        if path.suffix.lower() in TYPST_IMAGE_TYPES:
            return path
        if self.converter is not None:  # e.g. TIFF -> PNG
            dest = self.assets / (path.stem + "_" + str(abs(hash(str(path))) % 10**8) + ".png")
            if self.converter(path, dest):
                return dest
        self.warnings.append(f"Cannot place {path.name} (unsupported image type); left out.")
        return None

    def natural_size_in(self, path: Path) -> tuple[float, float] | None:
        if path in self.svg_sizes:   # a structure drawn by this build
            return self.svg_sizes[path]
        if path.suffix.lower() == ".svg":
            return svg_size_in(path.read_text(encoding="utf-8", errors="replace"))
        if path.suffix.lower() == ".pdf":   # (a pasted picture: its first page)
            return pdf_size_in(path)
        info = self.image_info(path)
        if info is None:
            return None
        w, h, dpi = info
        return w / dpi, h / dpi

    def image(self, path: Path, max_in: tuple[float, float], natural: bool = True,
              pdf_page: int | None = None, sizing: str | None = None, scale: float = 1.0,
              natural_factor: float = 1.0, pdf_natural: bool = False
              ) -> tuple[str, float, float] | None:
        """Typst image(...) sized to its natural size times `natural_factor` (a pasted
        structure drawn at another bond length, SPEC §13n.2a) but no bigger than max_in,
        then multiplied by `scale` (so 2.0 prints it twice that size, 0.5 half), or with
        explicit Typst `sizing` (e.g. 'width: 100%, height: 100%, fit: "contain"'). A PDF
        has a natural size only with `pdf_natural` (a pasted picture; other PDFs fill
        `max_in`'s width). Returns (code, width_in, height_in)."""
        usable = self.usable_image(path)
        if usable is None:
            return None
        page = f", page: {pdf_page}" if pdf_page else ""
        if sizing is not None:
            return f"image({typst_string(self.rel(usable))}{page}, {sizing})", max_in[0], max_in[1]
        size = (self.natural_size_in(usable)
                if natural and (pdf_natural or usable.suffix.lower() != ".pdf") else None)
        if size is None:
            w, h = max_in[0] * scale, max_in[1] * scale
            width = f"{max_in[0]}" if scale == 1 else f"{w:.3f}"
            return (f"image({typst_string(self.rel(usable))}, width: {width}in{page})", w, h)
        w, h = size[0] * natural_factor, size[1] * natural_factor
        fit = min(1.0, max_in[0] / w, max_in[1] / h) * scale
        w, h = w * fit, h * fit
        return f"image({typst_string(self.rel(usable))}, width: {w:.3f}in{page})", w, h

    def picture_factor(self, compound: Compound, picture: Path) -> float:
        """How much the compound's pasted picture is enlarged or shrunk so its bonds print
        at the project's bond length (SPEC §13n.2a); 1.0 for a raster picture, a drawing
        without measurable bonds, or with the setting off."""
        return drawing_scale.structure_print_factor(
            self.project.settings, compound.values.get("structure") or {}, picture)

    def structure_image(self, compound: Compound, max_in, scale: float = 1.0) -> str | None:
        """The compound's structure: its picture (pictures mode) or an ACS drawing, at its
        natural size (a pasted vector picture: at the project's bond length) but no bigger
        than `max_in`, then times `scale`."""
        picture = self.project.structure_image_path(compound)
        if picture is not None and picture.exists():
            sized = {"scale": scale, "pdf_natural": True,
                     "natural_factor": self.picture_factor(compound, picture)}
        if self.options.structures == "pictures" and picture is not None and picture.exists():
            img = self.image(picture, max_in, **sized)
            if img:
                return img[0]
        mol = self.project.read_structure(compound)
        if mol is None:
            if picture is not None and picture.exists():  # a picture but no structure
                img = self.image(picture, max_in, **sized)
                return img[0] if img else None
            return None
        svg = self.assets / f"{compound.id}.svg"
        text, size = drawn_structure(mol, self.drawing)
        svg.write_text(text, encoding="utf-8")
        self.svg_sizes[svg] = size
        img = self.image(svg, max_in, scale=scale)
        return img[0] if img else None

    # ------------------------------------------------------------------ pieces

    def cover(self) -> str:
        """The cover page: heading, title, authors, affiliations, corresponding emails and
        the optional extra text (Front matter ▸ Cover page)."""
        s = self.project.settings
        heading = (f"[{runs_to_typst([Run(front.cover_heading(s))])}]"
                   if front.cover_heading_is_custom(s) else "auto")   # auto: the style's own
        extra_text = self.project.read_front_text("cover")
        extra = (f"[{self.rich_text(parse(extra_text), 'Cover page', self.project.front_folder, outlined_levels=0)}]"
                 if extra_text.strip() else "none")
        title = runs_to_typst([Run(str(s.get("title", "") or ""))])
        authors = []
        for i, a in enumerate(front.authors(s)):
            marks = ",".join(a["affiliations"]) + ("*" if a.get("corresponding") else "")
            sup = f"#super[{runs_to_typst([Run(marks)])}]" if marks else ""
            authors.append(runs_to_typst([Run(str(a.get("name", "") or ""))]) + sup)
        if len(authors) > 1:
            names = ", ".join(authors[:-1]) + (", and " if len(authors) > 2 else " and ") + authors[-1]
        else:
            names = "".join(authors)
        affs = " \\ ".join(f"#super[{runs_to_typst([Run(str(a.get('marker', '') or ''))])}]"
                           + runs_to_typst([Run(str(a.get("text", "") or ""))])
                           for a in front.affiliations(s))
        emails = [str(a.get("email", "")) for a in front.authors(s) if a.get("corresponding")
                  and a.get("email")]
        contact = runs_to_typst([Run("*Email: " + "; ".join(emails))]) if emails else ""
        return (f"#si-cover(heading: {heading}, title: [{title}], authors: [{names}], "
                f"affiliations: [{affs}], contact: [{contact}], extra: {extra})")

    def table_of_contents(self) -> str:
        """The TOC page (Front matter ▸ Table of contents). Which headings it lists was
        decided when each heading was written (`outlined`); lists of figures, tables and
        schemes follow if chosen. Call after the rest of the document is built."""
        t = self.toc
        lines = []
        if not t["dots"]:
            lines.append("#set outline.entry(fill: none)")
        if t["font_size"] > 0:
            lines.append(f"#show outline.entry: set text(size: {t['font_size']:g}pt)")
        title = f"[{runs_to_typst([Run(t['title'])])}]" if t["title"].strip() else "none"
        lines.append(f"#outline(title: {title}, depth: none, indent: auto)")
        for key, (list_title, kind) in front.TOC_LISTS.items():
            if not t[key]:
                continue
            if not self.kinds[kind]:
                self.warnings.append(f"The table of contents has no {list_title.lower()}: "
                                     f"the SI has no {key} yet.")
                continue
            # Tables are Typst figures of kind `table` (the element), the others named kinds.
            target = "table" if kind == "table" else typst_string(kind)
            lines.append(f"#outline(title: [{runs_to_typst([Run(list_title)])}], "
                         f"target: figure.where(kind: {target}))")
        return "#[\n" + "\n\n".join(lines) + "\n]"

    def general_information(self, level: int = 1) -> str:
        """The General Information page (front/general_information.txt); "" when empty."""
        text = self.project.read_front_text("general")
        if not text.strip():
            return ""
        title = front.general_heading(self.project.settings).strip()
        numbered = front.general_numbered(self.project.settings)   # (unnumbered by default)
        out = [self.heading(level, [Run(title)], numbered=numbered)] if title else []
        out.append(self.rich_text(parse(text), title or "General Information",
                                  self.project.front_folder, heading_offset=level,
                                  outlined_levels=level + 1 if self.toc_rank >= 2 else 0,
                                  owner=self.project.text_owner(GENERAL_OWNER)))
        return "\n\n".join(x for x in out if x)

    def heading(self, level: int, runs: list[Run], outlined: bool = True,
                numbered: bool = True) -> str:
        """A heading. Only real sections are numbered (1, 1.1, 1.1.1 in numbered styles);
        general procedures and headings typed in a text pass numbered=False."""
        numbering = "" if numbered else ", numbering: none"
        return (f"#heading(level: {level}, outlined: {'true' if outlined else 'false'}"
                f"{numbering})[{runs_to_typst(runs)}]")

    def section_heading(self, level: int, runs: list[Run]) -> str:
        """A section's heading: top-level sections are always in the table of contents,
        sub-sections from the "sub-sections" depth on."""
        return self.heading(level, runs, outlined=level == 1 or self.toc_rank >= 2)

    def rich_text(self, paragraphs: list[Paragraph], where: str, folder: Path | None = None,
                  heading_offset: int = 1, outlined_levels: int = 2,
                  owner: TextOwner | None = None) -> str:
        """Stored text → Typst: links resolved, formatting helpers, figures and tables
        (with the labels of `owner`'s items, SPEC §13k.5)."""
        paragraphs = self.link_paragraphs(paragraphs, where)
        if self.options.format_text:
            paragraphs = [replace(p, runs=format_chemistry(p.runs)) for p in paragraphs]
        out, run_of_text = [], []
        index = 0   # the item's place among the owner's figures and tables (for its label)
        for p in paragraphs + [None]:
            if p is None or p.is_object:
                if run_of_text:
                    out.append(paragraphs_to_typst(run_of_text, heading_offset, outlined_levels))
                    run_of_text = []
                if p is None:
                    continue
                item = self.labels.at(owner.key, index) if owner is not None else None
                label = item.label if item is not None else ""
                index += 1
                if p.table:
                    out.append(aligned_object(self.table(owner, p.table, label, p.runs, where),
                                              p.stored_align))
                    continue
                path, width_cm = None, None
                if p.figure and owner is not None:
                    path = self.project.figure_path(owner, p.figure)
                    width_cm = figure_width_cm(self.project.figures(owner).get(p.figure, {}))
                    if path is None:
                        self.warnings.append(f"{where}: a figure's picture is no longer in the "
                                             "project; left out.")
                elif p.image and folder is not None:
                    path = folder / p.image
                if path is None:
                    img = None
                elif width_cm:   # a printed width typed for this figure (never wider than the text)
                    img = self.image(path, (round(min(TEXT_WIDTH_IN, width_cm / CM_PER_IN), 3),
                                            9.0), natural=False)
                else:
                    img = self.image(path, (TEXT_WIDTH_IN, 7.0))
                if img:
                    out.append(aligned_object(self.inline_figure(img[0], p.runs, label),
                                              p.stored_align))
            else:
                run_of_text.append(p)
        return "\n\n".join(x for x in out if x)

    @staticmethod
    def caption_code(caption: list[Run]) -> str:
        body = runs_to_typst(caption)
        return f"[{body}]" if body.strip() else "none"

    def inline_figure(self, code: str, caption: list[Run], label: str = "") -> str:
        """A figure: a picture in a text, or a file shown inline with a compound."""
        self.kinds["si-figure"] += 1
        return (f"#si-figure({code}, [{runs_to_typst([Run(label or 'Figure')])}], "
                f"{self.caption_code(caption)})")

    def cell_text(self, text: str, where: str) -> str:
        """A table cell or footnote: its paragraphs on separate lines."""
        lines = []
        for p in self.link_paragraphs(parse(text), where):
            runs = format_chemistry(p.runs) if self.options.format_text else p.runs
            lines.append(runs_to_typst(runs))
        return "#linebreak()".join(x for x in lines if x)

    def table(self, owner: TextOwner | None, table_id: str, label: str, caption: list[Run],
              where: str) -> str:
        """A table (core/tables.py) as a Typst figure: label and caption above, merged
        cells, header rows ruled off (and repeated after a page break), footnotes below."""
        table = self.project.read_table(owner, table_id) if owner is not None else None
        if table is None or not table.n_rows or not table.n_cols:
            self.warnings.append(f"{where}: a table could not be found or is empty "
                                 f"(tables/{table_id}.json); left out.")
            return ""
        self.kinds["table"] += 1
        # The style keeps a table whole (moving it to the next page) unless it is taller than
        # a page or the table editor's "may run over a page break" is ticked (`breakable`); then
        # it continues on the next page with its header rows repeated.
        split = ", split: true" if table.breakable else ""
        return (f"#si-table({self.table_code(table, where)}, {self.notes_code(table, where)}, "
                f"[{runs_to_typst([Run(label or 'Table')])}], {self.caption_code(caption)}"
                f"{split})")

    def notes_code(self, table: Table, where: str) -> str:
        notes = [self.cell_text(n, where) for n in table.footnotes]
        notes = [n for n in notes if n]
        return "[" + "#linebreak()".join(notes) + "]" if notes else "none"

    def table_code(self, table: Table, where: str) -> str:
        t = table.copy()
        header = t.header_rows
        for r, _c, cell in t.cells():   # a header cell cannot reach below the header
            if r < header:
                cell.rowspan = min(cell.rowspan, header - r)
        t.normalize()

        def cell_code(cell) -> str:
            args = []
            if cell.colspan > 1:
                args.append(f"colspan: {cell.colspan}")
            if cell.rowspan > 1:
                args.append(f"rowspan: {cell.rowspan}")
            if cell.align:
                args.append(f"align: {cell.align} + horizon")
            body = f"[{self.cell_text(cell.text, where)}]"
            return f"table.cell({', '.join(args)}){body}" if args else body

        head = [cell_code(cell) for r, _c, cell in t.cells() if r < header]
        body = [cell_code(cell) for r, _c, cell in t.cells() if r >= header]
        aligns = ", ".join(f"{a} + horizon" for a in t.columns)
        parts = [f"columns: {t.n_cols}", f"align: ({aligns},)", "stroke: none",
                 "table.hline(stroke: 0.8pt)"]
        if head:
            parts += ["table.header(" + ", ".join(head) + ")", "table.hline(stroke: 0.5pt)"]
        parts += body + ["table.hline(stroke: 0.8pt)"]
        return "table(" + ", ".join(parts) + ")"

    def text_section(self, section: Section, level: int = 1) -> str:
        """A custom section: its heading at `level` (its depth in the tree), then its text,
        whose own headings sit below it, unnumbered."""
        return (self.section_heading(level, [Run(section.name)]) + "\n\n"
                + self.text_section_body(section, level))

    def text_section_body(self, section: Section, level: int = 1) -> str:
        """A custom section's text (without its heading)."""
        return self.rich_text(parse(self.project.read_section_text(section)), section.name,
                              section.folder, heading_offset=level,
                              outlined_levels=level + 1 if self.toc_rank >= 2 else 0,
                              owner=self.project.text_owner(section.id))

    def file_section(self, section: Section, level: int = 1) -> str:
        out = []
        heading = str(section.data.get("heading", "")).strip()
        if heading:
            out.append(self.section_heading(level, [Run(heading)]))
        path = self.project.section_file_path(section)
        if path is None:
            self.warnings.append(f"Attachment section “{section.name}” has no file attached.")
            return "\n\n".join(out)
        caption = str(section.data.get("caption", "")).strip()
        is_pdf = path.suffix.lower() == ".pdf"
        pages = self.pdf_pages(path) if is_pdf else [None]
        for i, page in enumerate(pages):
            if is_pdf:   # each page fills the text width (at most the page's height)
                img = self.image(path, (TEXT_WIDTH_IN, ATTACHMENT_MAX_HEIGHT_IN), pdf_page=page,
                                 sizing=f'width: 100%, height: {ATTACHMENT_MAX_HEIGHT_IN:g}in, '
                                        'fit: "contain"')
            else:   # a picture at its own size (its dpi, or an SVG's size), shrunk to fit
                img = self.image(path, (TEXT_WIDTH_IN, ATTACHMENT_MAX_HEIGHT_IN))
            if img is None:
                continue
            code = img[0]
            if i:
                out.append("#pagebreak(weak: true)")
            out.append(f"#align(center, {code})")
        if caption:
            out.append(f"#align(center, text(size: 10pt)[{runs_to_typst([Run(caption)])}])")
        return "\n\n".join(out)

    def page_aspect(self, path: Path, page: int | None) -> float | None:
        """Height / width of a picture or of one PDF page (None if unknown). For a PDF this
        is the page's visible area (its CropBox, e.g. after cropping it in Acrobat; the
        whole MediaBox when it has none), turned by its /Rotate — what Typst and QtPdf
        draw."""
        try:
            if page is not None:
                from pypdf import PdfReader
                pg = PdfReader(str(path)).pages[page - 1]
                box = pg.cropbox   # (pypdf falls back to the MediaBox)
                w, h = abs(float(box.width)), abs(float(box.height))
                if (pg.rotation or 0) % 180:
                    w, h = h, w
                return h / w if w else None
            size = self.natural_size_in(path)
            return size[1] / size[0] if size and size[0] else None
        except Exception:  # noqa: BLE001 — unknown shape: fall back to filling the frame
            return None

    def pdf_pages(self, path: Path) -> list[int]:
        try:
            from pypdf import PdfReader
            return list(range(1, len(PdfReader(str(path)).pages) + 1))
        except Exception as e:  # noqa: BLE001 — report and keep going
            self.warnings.append(f"Could not read the PDF {path.name}: {e}")
            return []

    def compound(self, compound: Compound, show_structure: bool = True, level: int = 2) -> str:
        """A compound block. Its title is a heading at `level` (one below its section's),
        drawn by the style's si-compound-title: never numbered, in the table of contents
        only when the TOC goes down to compounds."""
        display_id = self.report.display_id(compound.id)
        block: CompoundBlock = compound_block(self.project, compound, display_id,
                                              self.options.format_text,
                                              self.nmr_link(compound))
        outlined = "true" if self.toc_rank >= 4 else "false"
        title = f"#si-compound-title({level}, {outlined})[{runs_to_typst(block.title)}]"
        label = self.compound_label(compound)
        if self.link_back and self.place_label(label):   # (the spectra link back here)
            title += f" <{label}>"
        out = [title]
        if show_structure:
            img = self.structure_image(compound, STRUCTURE_MAX)
            if img:
                out.append(f"#si-structure({img})")
        where = f"{display_id} ({compound.name})" if compound.name else display_id
        if not block.custom_text:
            problems: list[str] = []
            gp_sentence(self.project, compound, problems)
            for p in problems:
                self.warnings.append(f"{where}: general procedure sentence has {p}; printed "
                                     "in [brackets].")
            choice = self.project.gp_choice(compound)
            if (choice.get("gp") and "procedure" in compound.field_keys
                    and self.project.compound_gp(compound) is None):
                self.warnings.append(f"{where}: its general procedure no longer exists; the "
                                     "“Prepared according to…” sentence was left out.")
        out.append(paragraphs_to_typst(self.link_paragraphs(block.paragraphs, where),
                                       heading_offset=level, outlined_levels=0))
        for index, fig in enumerate(block.figures):
            img = self.image(fig.path, (TEXT_WIDTH_IN, 4.0))
            item = self.labels.at(compound.id, index)
            if img:
                caption = [Run(fig.caption)]
                if self.options.format_text:
                    caption = format_chemistry(caption)
                out.append(self.inline_figure(img[0], caption, item.label if item else ""))
        if display_id == "ID?":
            self.warnings.append(f"“{compound.name or 'A compound'}” has no ID (printed as “ID?”).")
        return "\n\n".join(x for x in out if x)

    def scheme(self, scheme: SchemePage, level: int = 2) -> str:
        # Made with the scheme editor (SPEC §13n.4): laid out by the style.
        out = [editor_scheme(self, scheme)]
        # The scheme shows the structures drawn in a box; a compound not linked to any
        # box prints its own, like any compound.
        drawn = set(scheme_editor.linked_compound_ids(scheme))
        for child in scheme.children:
            out.append(self.compound(child, show_structure=child.id not in drawn,
                                     level=level))
        return "\n\n".join(x for x in out if x)

    def general_procedures(self, section: Section, level: int = 2) -> list[str]:
        """Each GP once, under its own (unnumbered) heading at `level`, at the start of its
        section (SPEC §6.3.3)."""
        out = []
        for gp in self.project.general_procedures(section):
            name = str(gp.get("name", "")).strip() or "General Procedure"
            title = str(gp.get("title", "")).strip()
            runs = [Run(name + (f": {title}" if title else ""))]
            if self.options.format_text:
                runs = format_chemistry(runs)
            out.append(self.heading(level, runs, outlined=self.toc_rank >= 3, numbered=False))
            text = self.project.read_gp_text(gp["id"])
            if not text.strip():
                self.warnings.append(f"“{name}” in “{section.name}” has no text yet.")
            # (follows the journal style, like compound procedures: no alignment)
            out.append(self.rich_text([replace(p, align="") for p in parse(text)],
                                      f"{section.name} ▸ {name}",
                                      heading_offset=level, outlined_levels=0))
        return out

    def data_section(self, section: Section, level: int = 1) -> str:
        """A Data section at any depth: its heading, its general procedures and compound
        blocks one level below it."""
        out = [self.section_heading(level, [Run(section.name)])]
        out += self.general_procedures(section, level + 1)
        for item in section.items:
            out.append(self.scheme(item, level + 1) if isinstance(item, SchemePage)
                       else self.compound(item, level=level + 1))
        return "\n\n".join(out)

    def section_block(self, section: Section, level: int, compounds: list[Compound],
                      skip: frozenset[str] = frozenset(), lead: str = "") -> str:
        """A section with all its sub-sections (one level further down). Sections in `skip`
        are left out with everything inside them. Compounds printed are added to
        `compounds` (for the spectra of a preview).

        `lead`: headings of the sections around this one that have nothing of their own to
        print, printed first. A section that is only its heading hands it on to its first
        sub-section in the same way, so when that is the Spectra section (which always
        starts a page of its own) the headings go to the top of its first page instead of
        being left alone at the bottom of the page before."""
        if section.id in skip:
            return lead
        if section.type == SPECTRA_SECTION:
            if front.SPECTRA_KEY in skip:   # (how older projects left the spectra out)
                return lead
            out = [self.spectra_section(section, level, lead)]
            lead = ""
        else:
            if section.type == "text":
                heading = self.section_heading(level, [Run(section.name)])
                body = self.text_section_body(section, level)
                own = heading + "\n\n" + body
            elif section.type == "file":
                heading, own = "", self.file_section(section, level)
            else:
                heading = self.section_heading(level, [Run(section.name)])
                own = self.data_section(section, level)
                compounds += section.compounds
            children = [c for c in section.children if c.id not in skip]
            if heading and own.strip() == heading and children:
                # Only a heading: hand it (and anything handed to it) to the first child.
                lead = "\n\n".join(x for x in (lead, heading) if x)
                out = []
            else:
                out = [lead, own]
                lead = ""
        for child in section.children:
            if child.id in skip:
                continue
            out.append(self.section_block(child, level + 1, compounds, skip, lead))
            lead = ""
        out.append(lead)   # (only when there was no sub-section to take it)
        return "\n\n".join(x for x in out if x)

    # ------------------------------------------------------------------ spectra (SPEC §13k.12)

    def spectrum_header(self, compound: Compound, item: dict, page_no: int) -> list[Run]:
        """The line above a spectrum: "2 — ¹H NMR (400 MHz, CDCl₃)", or the header typed
        for it; later pages of a PDF add "(continued)"."""
        runs = [Run(t) for t in spectra_mod.header_parts(
            compound, item, self.report.display_id(compound.id), page_no)]
        return format_nmr(runs) if self.options.format_text else runs

    def included_compounds(self, sections: list[Section], skip: frozenset[str]) -> list[Compound]:
        """Every compound of the Data sections in `sections` (and below), in tree order,
        minus sections in `skip` and everything inside them."""
        out: list[Compound] = []
        for s in sections:
            if s.id in skip:
                continue
            if s.type == "data":
                out += s.compounds
            out += self.included_compounds(s.children, skip)
        return out

    def spectra_section(self, section: Section, level: int = 1, lead: str = "") -> str:
        """The Spectra section: the spectra of every compound printed in the SI (a preview
        shows them all, the live preview of the section itself a sample), under the
        section's heading (after `lead`, the headings of sections around it that have
        nothing else to print; see section_block)."""
        if section is not self.project.spectra_section:
            self.warnings.append(f"“{section.name}”: a project has only one Spectra section; "
                                 "this one was left out.")
            return lead
        preview = self.options.only is not None
        skip = frozenset() if preview else frozenset(self.options.excluded)
        compounds = self.included_compounds(self.project.top_sections, skip)
        # Only a preview of this section (or of a section holding it) shows a sample: the
        # table of contents preview needs every page to number what comes after it.
        sample = preview and self.options.only[0] == "section"
        heading = "\n\n".join(x for x in (lead, self.section_heading(level, [Run(section.name)]))
                              if x)
        pages = self.spectra_pages(compounds, heading,
                                   limit=self.options.spectra_limit if sample else 0)
        # SPEC §13n.3: the HRMS reports, in a sub-section after every compound's spectra.
        # Without spectra, the section's heading goes on the reports' first page.
        hrms = "" if front.HRMS_KEY in skip else self.hrms_reports_block(
            compounds, level + 1, new_page=bool(pages))
        if not hrms:
            return pages or lead
        if not pages:
            return "\n\n".join(["#pagebreak(weak: true)", heading, hrms])
        return pages + "\n\n" + hrms

    # ------------------------------------------------------------------ HRMS reports (§13n.3)

    def hrms_reports_block(self, compounds: list[Compound], level: int,
                           heading: bool = True, new_page: bool = True) -> str:
        """The sub-section "HRMS reports" of the Spectra section: the report files of the
        compounds whose reports are printed (the section's "Print HRMS reports", or the
        compound's own choice), compound by compound in tree order. "" if none."""
        parts = []
        for compound in compounds:
            if not ms_reports.prints_reports(self.project, compound):
                continue
            for item in ms_reports.report_items(compound):
                code = self.hrms_report(compound, str(item.get("file", "")))
                if code:
                    parts.append(code)
        if not parts:
            return ""
        # (a first PDF page goes under the heading, not on the next page)
        parts[0] = parts[0].removeprefix(PAGE_BREAK + "\n\n")
        top = [PAGE_BREAK] if new_page else []
        if heading:
            top.append(self.section_heading(level, [Run(HRMS_HEADING)]))
        return "\n\n".join(top + parts)

    def hrms_report_title(self, compound: Compound, name: str, more: str = "") -> str:
        """The line above a printed report: "3a — HRMS report: 3a_HRMS.pdf"."""
        runs = [Run(self.report.display_id(compound.id), bold=True),
                Run(f" — HRMS report: {name}{more}")]
        return f"#block(above: 1.2em, below: 0.6em, sticky: true)[{runs_to_typst(runs)}]"

    def hrms_report(self, compound: Compound, name: str) -> str:
        """One report file: a PDF page by page, a picture at its own size (no larger than
        the page), a text or table file as a monospaced listing, a data file (mzML…) as a
        table of its largest peaks near the expected m/z. Raw data is left out (with a
        note in the compile warnings)."""
        path = ms_reports.report_path(compound, name)
        shown = self.report.display_id(compound.id)
        if not path.exists():
            self.warnings.append(f"{shown}: the HRMS report {name} is missing; left out.")
            return ""
        kind = ms_reports.print_kind(path)
        if kind == "none":
            self.warnings.append(f"{shown}: the HRMS report {name} can't be printed (raw data "
                                 "or a kind of file that can't be shown); left out.")
            return ""
        if kind == "pdf":
            out = []
            pages = self.pdf_pages(path)
            for i, page in enumerate(pages):
                img = self.image(path, (TEXT_WIDTH_IN, HRMS_PAGE_IN), pdf_page=page,
                                 sizing=f'width: 100%, height: {HRMS_PAGE_IN:g}in, '
                                        'fit: "contain"')
                if img is None:
                    continue
                more = f" (page {i + 1} of {len(pages)})" if len(pages) > 1 else ""
                out += [PAGE_BREAK, self.hrms_report_title(compound, name, more),
                        f"#align(center, {img[0]})"]
            return "\n\n".join(out)
        if kind == "picture":
            img = self.image(path, (TEXT_WIDTH_IN, HRMS_PAGE_IN))
            if img is None:
                return ""
            return "\n\n".join([self.hrms_report_title(compound, name),
                                 f"#align(center, {img[0]})"])
        if kind == "peaks":
            return self.hrms_peaks_table(compound, name, path)
        lines, left = ms_reports.listing_lines(path)   # (never raises)
        if ms_reports.listing_failed(lines):
            self.warnings.append(f"{shown}: the HRMS report {name} could not be read (it may "
                                 "be damaged); only a note is printed for it.")
        listing = typst_string("\n".join(lines))
        code = [self.hrms_report_title(compound, name),
                "#block(width: 100%, inset: 6pt, stroke: 0.5pt + luma(170), breakable: "
                f"{'false' if len(lines) <= HRMS_KEEP_LINES else 'true'})[#set text(size: "
                f"7pt)\n#raw(block: true, {listing})]"]
        if left:
            note = (f"… {left} more line{'s' if left != 1 else ''} not printed (the whole file "
                    "is kept in the project folder).")
            code.append(f'#text(size: 8pt, style: "italic")[{runs_to_typst([Run(note)])}]')
        return "\n\n".join(code)

    def hrms_peaks_table(self, compound: Compound, name: str, path: Path) -> str:
        """A data file (mzML, mzXML, mzData, netCDF): its largest peaks near the expected
        m/z (the monoisotopic m/z of the chosen ion), with the one used as found marked."""
        data = ms_reports.read_report(path)
        peaks = ms_reports.expected_peaks(self.project, compound)
        centre = None
        if peaks:
            centre = next((p.mz for p in peaks if p.monoisotopic), peaks[0].mz)
        rows = ms_reports.peaks_near(data, centre)
        title = self.hrms_report_title(compound, name)
        if not rows:
            text = (data.message if not data.readable else
                    "No peaks were found near the expected m/z in this file.")
            return "\n\n".join([title, f"#text(size: 9pt)[{runs_to_typst([Run(text)])}]"])
        value = compound.values.get("hrms") or {}
        found = str(value.get("found", "") or "").strip()
        cells = []
        for mz, rel in rows:
            mark = "found m/z" if ms_reports.format_mz(mz) == found else ""
            cells += [f"[{mz:.4f}]", f"[{rel:.1f}]", f"[{mark}]"]
        near = (f" The largest peaks near m/z {centre:.4f}:" if centre is not None
                else " The largest peaks:")
        what = runs_to_typst([Run(ms_reports.summary(data) + near)])
        table = ("#table(columns: 3, align: (right, right, left), stroke: none, "
                 "inset: (x: 8pt, y: 2pt), table.hline(), "
                 "table.header([_m/z_], [Intensity (%)], []), table.hline(), "
                 + ", ".join(cells) + ", table.hline())")
        return "\n\n".join([title, f"#text(size: 9pt)[{what}]",
                             f"#text(size: 9pt)[{table}]"])

    def crop_box(self, path: Path, page: int | None):
        # The table of contents preview only needs the page count, which cropping doesn't
        # change: it uses what was measured before and measures nothing new.
        counting = self.options.only is not None and self.options.only[0] == "front"
        return self.crops.box(path, page, measure=not counting)

    def spectrum_slot(self, sp: spectra_mod.Spectrum, eff: dict) -> str | None:
        """One spectrum (one page of a file) as a call of the style's si-spectrum, set up
        as `eff` (its effective settings) says. None if the file can't be placed."""
        usable = self.usable_image(sp.path)
        if usable is None:
            return None
        compound = sp.compound
        aspect = self.page_aspect(usable, sp.page)
        # Measured on the original file (not a converted copy in build/assets, which is
        # made again at every build), so the result is remembered between builds.
        crop = self.crop_box(sp.path, sp.page) if eff["auto_crop"] else None
        page_arg = f", page: {sp.page}" if sp.page else ""
        img = (f"(w, h) => image({typst_string(self.rel(usable))}{page_arg}, width: w, "
               'height: h, fit: "contain")')
        header = "none"
        if eff["header_line"]:
            header = runs_to_typst(self.spectrum_header(compound, sp.item, sp.page_no))
            if self.link_back:   # back to the compound's data (SPEC §13l.7)
                header = f"#si-link(<{self.compound_label(compound)}>)[{header}]"
            header = f"[{header}]"
        args = [header, img, f"{aspect:.5f}" if aspect else "none"]
        if crop:
            args.append("crop: (" + ", ".join(f"{v:.4f}" for v in crop) + ")")
        if eff["struct_box"]:   # (it can be left off a spectrum: "Structure + ID")
            args += self.structure_box_args(compound, sp.item, eff)
        if eff["spectrum_frame"]:
            args.append(f"frame: {eff['spectrum_frame_width']:g}pt + "
                        f"rgb({typst_string(eff['spectrum_frame_color'])})")
        code = "si-spectrum(" + ", ".join(args) + ")"
        # The peak reports link to the first printed page of the compound's first spectrum
        # of each nucleus (SPEC §13l.7): a label at the top of its slot.
        nucleus = spectra_mod.nmr_nucleus(sp.item, compound) if self.nmr_links else None
        if nucleus and self.place_label(self.spectrum_label(compound, nucleus)):
            code = f"[#metadata(none) <{self.spectrum_label(compound, nucleus)}>#{code}]"
        return code

    def structure_box_args(self, compound: Compound, item: dict, eff: dict) -> list[str]:
        """The si-spectrum arguments for the structure + ID box on a spectrum."""
        # 100 % = its natural size, no bigger than STRUCTURE_BOX_IN; other sizes multiply that.
        structure = self.structure_image(compound, spectra_mod.STRUCTURE_BOX_IN,
                                         scale=eff["struct_scale"]) or "none"
        display_id = runs_to_typst([Run(self.report.display_id(compound.id))])
        caption = "none"
        text = spectra_mod.caption_text(compound, item) if eff["caption"] else ""
        if text:   # NMR spectra, or a caption typed for this spectrum (12.9)
            runs = format_nmr([Run(text)]) if self.options.format_text else [Run(text)]
            caption = f"[{runs_to_typst(runs)}]"
        args = [f"structure: {structure}", f"id: [{display_id}]", f"caption: {caption}",
                f"corner: {CORNERS[eff['corner']]}"]
        if "x" in eff and "y" in eff:   # dragged to a place of its own
            args.append(f"pos: ({eff['x']:.4f}, {eff['y']:.4f})")
        if eff["struct_frame"]:
            args.append(f"box-stroke: {eff['struct_frame_width']:g}pt + "
                        f"rgb({typst_string(eff['struct_frame_color'])})")
        args.append(f"box-fill: rgb({typst_string(eff['struct_bg'])})")
        if eff["id_size"] > 0:
            args.append(f"id-size: {eff['id_size']:g}pt")
        if not eff["id_bold"]:
            args.append("id-bold: false")
        if caption != "none" and eff["caption_size"] != spectra_mod.CAPTION_PT:
            args.append(f"caption-size: {eff['caption_size']:g}pt")
        return args

    def spectra_pages(self, compounds: list[Compound], heading: str = "",
                      limit: int = 0) -> str:
        """The spectra of `compounds`, laid out page by page: each spectrum's effective
        settings (the Spectra section's defaults, then its own) give how many share a page
        and the page's orientation; a spectrum that needs another layout starts a new
        page. `heading` (Typst) goes at the top of the first page. "" if there are none."""
        defaults = self.project.spectra_defaults()
        entries: list[spectra_mod.Spectrum] = []
        problems: list[str] = []
        for compound in compounds:
            entries += spectra_mod.spectra_of(self.project, compound, problems=problems)
        self.warnings += problems
        if limit and len(entries) > limit:
            self.warnings.append(f"The live preview shows the first {limit} of {len(entries)} "
                                 "spectra; the full SI has them all.")
            entries = entries[:limit]
        pages: list[tuple[str, int, list[str]]] = []   # (orientation, per page, slots)
        for sp in entries:
            eff = spectra_mod.effective(defaults, sp.item, sp.key)
            code = self.spectrum_slot(sp, eff)
            if code is None:
                continue
            orientation, per_page = eff["orientation"], eff["per_page"]
            if (not pages or pages[-1][0] != orientation or pages[-1][1] != per_page
                    or len(pages[-1][2]) >= per_page):
                pages.append((orientation, per_page, []))
            pages[-1][2].append(code)
        out = []
        for i, (orientation, per_page, slots) in enumerate(pages):
            landscape = "true" if orientation == "landscape" else "false"
            top = heading + "\n" if heading and i == 0 else ""
            out.append(f"#page(flipped: {landscape})[\n{top}"
                       f"#si-spectra-page({per_page}, {landscape}, ({', '.join(slots)},))\n]")
        return "\n\n".join(out)

    def spectra_of_preview(self, compounds: list[Compound]) -> str:
        """The spectra shown after a compound, scheme or section in the toolbar Preview."""
        return self.spectra_pages(compounds, self.heading(1, [Run(SPECTRA_HEADING)]))

    # ------------------------------------------------------------------ header and footer

    def page_line(self, texts: dict[str, str]) -> str:
        """A header/footer as a Typst function (page, pages) => (left, centre, right).
        {page}/{pages} become the formatted numbers (filled in by the style); {title},
        {authors} and {date} are filled in here."""
        s = self.project.settings
        known = {"title": str(s.get("title", "")), "authors": page_setup.author_names(s),
                 "date": page_setup.date_text(self.today)}
        cells = []
        for pos in page_setup.POSITIONS:
            out, runs = [], []
            for kind, value in page_setup.split_fill_ins(texts.get(pos, "")) + [("end", "")]:
                if kind in ("text", "title", "authors", "date"):
                    runs.append(Run(value if kind == "text" else known[kind]))
                    continue
                if runs:
                    out.append(runs_to_typst(format_chemistry(runs) if self.options.format_text
                                             else runs))
                    runs = []
                if kind in ("page", "pages"):
                    out.append("#(p)" if kind == "page" else "#(t)")
            cells.append("[" + "".join(out) + "]")
        return f"(p, t) => ({', '.join(cells)})"

    def page_args(self) -> str:
        """si-doc's header/footer and page-number options (Project settings ▸ Page)."""
        v = page_setup.page_settings(self.project.settings)
        header = (self.page_line(v["header"]) if any(t.strip() for t in v["header"].values())
                  else "none")
        footer = "none" if v["footer_is_style"] else self.page_line(v["footer"])
        size = f"{v['number_size']:g}pt" if v["number_size"] > 0 else "auto"
        # A preview (or an SI exported without its cover) has no cover page, so its first
        # page is numbered like any other.
        on_cover = (v["number_on_cover"] or self.options.only is not None
                    or not front.front_page_included("cover", set(self.options.excluded)))
        return (f"header: {header}, footer: {footer}, "
                f"number-prefix: {typst_string(v['number_prefix'])}, "
                f"number-style: {typst_string(v['number_style'])}, number-size: {size}, "
                f"number-bold: {'true' if v['number_bold'] else 'false'}, "
                f"number-on-cover: {'true' if on_cover else 'false'}")

    # ------------------------------------------------------------------ whole document

    def document(self) -> str:
        p = self.project
        parts = ['#import "style.typ": *',
                 f"#show: si-doc.with(title: {typst_string(str(p.settings.get('title') or ''))}, "
                 f"{self.page_args()})"]
        if self.nmr_links:
            parts.append(link_definition(self.link_style))
        if self.options.only is None:
            parts += self.whole_document()
        else:
            body = self.preview_parts(*self.options.only)
            self.empty = self.empty or not any(body)
            parts += body
        if self.cited:
            # Only cited references are printed, numbered in order of first citation.
            yml = self.build / REFERENCES_YML
            yml.write_text(to_hayagriva(p.references), encoding="utf-8")
            parts.append(f"#si-references({typst_string(self.rel(yml))})")
        return "\n\n".join(x for x in parts if x) + "\n"

    def preview_parts(self, kind: str, item_id: str) -> list[str]:
        """One page on its own (CompileOptions.only): the toolbar Preview and the live
        preview pane. Spectra are added when options.preview_spectra is on."""
        p = self.project
        spectra = self.options.preview_spectra
        if kind == "compound":
            c = p.compound(item_id)
            return [self.compound(c, show_structure=not c.scheme_id),
                    self.spectra_of_preview([c]) if spectra else ""]
        if kind == "scheme":
            k = p.scheme(item_id)
            return [self.scheme(k), self.spectra_of_preview(k.children) if spectra else ""]
        if kind == "front":
            return [self.front_preview(item_id)]
        if kind == "spectra":
            # One compound's spectra pages (its page under the Spectra section), laid out
            # as they will be printed; always shown, whatever preview_spectra says. Its
            # HRMS reports follow when they are printed (SPEC §13n.3).
            c = p.compound(item_id)
            return [self.spectra_pages([c]), self.hrms_reports_block([c], 2)]
        if kind == "hrms":
            # The Spectra section's "HRMS reports" (its page in the tree).
            return [self.hrms_reports_block(self.included_compounds(p.top_sections,
                                                                    frozenset()), 1)]
        if kind == "section":
            # A section with its sub-sections, shown as if it were at the top level. When
            # the Spectra section is among them it prints the spectra itself.
            compounds: list[Compound] = []
            section = p.section(item_id)
            body = self.section_block(section, 1, compounds)
            has_spectra = any(s.type == SPECTRA_SECTION for s in p.subtree(section))
            return [body, self.spectra_of_preview(compounds)
                    if spectra and not has_spectra else ""]
        raise ValueError(f"Unknown preview kind {kind!r}")

    def whole_document(self) -> list[str]:
        """Front matter, every section in tree order, spectra; minus what was unticked in
        the Compile window. The cover and the TOC each end their page."""
        p = self.project
        excluded = set(self.options.excluded)
        compounds: list[Compound] = []
        body = [self.section_block(s, 1, compounds, frozenset(excluded))
                for s in p.top_sections]
        # The Spectra section prints the spectra where it sits (in section_block). A project
        # without one (an older project opened read-only) has them at the end, as before.
        spectra = (self.spectra_of_preview(compounds) if p.spectra_section is None
                   and front.SPECTRA_KEY not in excluded else "")
        general = (self.general_information() if front.front_page_included("general", excluded)
                   else "")
        parts = []
        if front.front_page_included("cover", excluded):
            parts += [self.cover(), "#pagebreak(weak: true)"]
        if front.front_page_included("toc", excluded):
            # Built last, so it knows which figures, tables and schemes were printed.
            parts += [self.table_of_contents(), "#pagebreak(weak: true)"]
        parts.append(general)
        parts += body + [spectra]
        if not any(x for x in parts if x and not x.startswith("#pagebreak")):
            self.warnings.append("Nothing was chosen to be printed in the Compile window, so "
                                 "the SI is empty. Tick what to include and compile again.")
        return parts

    def front_preview(self, key: str) -> str:
        """One front matter page on its own (the Preview button)."""
        if key == "cover":
            return self.cover()
        if key == "general":
            text = self.general_information()
            if not text:
                self.empty = True   # (the live preview says "nothing to show yet")
            return text or "#emph[The General Information page is empty.]"
        # The table of contents needs the whole document to list anything (and to know its
        # page numbers). The live preview then shows only the pages up to the marker.
        parts = self.whole_document_for_toc()
        self.last_page_label = TOC_END_LABEL
        return "\n\n".join(x for x in parts if x)

    def whole_document_for_toc(self) -> list[str]:
        """The table of contents laid out with what follows it in the full SI (as chosen
        in the Compile window), so its page numbers are the SI's own. The cover is not laid
        out; when the SI has one, the page count simply starts at 2 (the TOC is page S2)."""
        p = self.project
        excluded = set(self.options.excluded)
        compounds: list[Compound] = []
        body = [self.section_block(s, 1, compounds, frozenset(excluded))
                for s in p.top_sections]
        spectra = (self.spectra_of_preview(compounds) if p.spectra_section is None
                   and front.SPECTRA_KEY not in excluded else "")
        general = (self.general_information() if front.front_page_included("general", excluded)
                   else "")
        start = ("#counter(page).update(2)" if front.front_page_included("cover", excluded)
                 else "")
        return [start, self.table_of_contents(), page_marker(TOC_END_LABEL),
                "#pagebreak(weak: true)", general] + body + [spectra]


def page_marker(label: str) -> str:
    """Typst that records the page it lands on under `label` (read back with
    compile.run.last_page_of); it prints nothing."""
    return f"#context [#metadata(here().page()) <{label}>]"



def build(project: Project, options: CompileOptions, image_info, converter=None,
          name: str = "SI", folder: Path | None = None, root: Path | None = None,
          crop=None) -> BuildResult:
    """Write build/<name>.typ (+ style.typ, assets/), or into `folder` with Typst root
    `root` (see Builder). Returns the path, any warnings and whether it is empty.
    `crop(path, page) -> (left, top, right, bottom) | None` finds a spectrum page's
    content for auto-crop (supplied by the UI layer, which has Qt; without it spectra are
    not cropped)."""
    b = Builder(project, options, image_info, folder, root)
    b.converter = converter
    b.crops.detect = crop
    b.build.mkdir(parents=True, exist_ok=True)
    if b.assets.exists():
        shutil.rmtree(b.assets, ignore_errors=True)
    b.assets.mkdir(parents=True, exist_ok=True)
    style = project.settings.get("journal_style", "ACS").lower()
    style_file = STYLES_DIR / f"{style}.typ"
    if not style_file.exists():
        b.warnings.append(f"The {project.settings.get('journal_style')} style is not available "
                          "yet; the ACS style was used.")
        style_file = STYLES_DIR / "acs.typ"
    shutil.copy2(style_file, b.build / "style.typ")
    with leave_alone(project.dictionary_words()):   # words to print as written (§13k.1ai)
        source = b.document()
    b.crops.save()
    typ = b.build / f"{name}.typ"
    typ.write_text(source, encoding="utf-8")
    return BuildResult(typ, b.warnings, b.empty, b.last_page_label)
