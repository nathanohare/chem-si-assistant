"""A scheme made with the scheme editor, as Typst (SPEC §13n.4).

The program lists what is in the scheme, left to right; the journal style's
`si-reaction-scheme` lays it out (arrow lengths, rows, shrinking), measuring the text in
Typst, so the result is exactly as wide as Typst sets it:

    #si-reaction-scheme((
        (kind: "mol", body: image(…), label: [3a], dx: 0pt, dy: 0pt, yield: [92%]),
        (kind: "plus"),
        (kind: "arrow", top: ([line], [line]), bottom: ([line],),
         top-align: center, bottom-align: center),
        …), factor: 100%, fit: "wrap", gap: 2mm, before: 2mm, after: 3mm,
        min-arrow: 12mm, size: 9pt, label-size: 10pt, label: [Scheme S1], caption: […])

(`label: none, caption: none` when the scheme's "Label and caption" is off, the default.)

Structures print at their natural size, a pasted ChemDraw picture at the project's bond
length (SPEC §13n.2a), times the scheme's scale. Structures in the conditions are that
size times "structure size in conditions".
"""

from __future__ import annotations

from ..core import scheme_editor as se
from ..core.markup import Run, parse
from .text import format_chemistry, runs_to_typst, typst_string

STRUCTURE_MAX_IN = (6.5, 4.0)    # one structure in a scheme: at most this big
TYPST_ALIGN = {"left": "left", "center": "center", "right": "right"}


def _content(code: str) -> str:
    return f"[{code}]"


def _tuple(items: list[str]) -> str:
    if not items:
        return "()"
    return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"


class SchemeWriter:
    """Writes one editor scheme for a compile/document.Builder."""

    def __init__(self, builder, scheme):
        self.b = builder
        self.project = builder.project
        self.scheme = scheme
        self.layout = se.scheme_layout(self.project.settings, scheme)
        self.where = f"Scheme “{scheme.name}”"

    # ---- structures

    def box_image(self, view: se.BoxView) -> str | None:
        """Typst image(...) of a box's structure, or None (nothing to show)."""
        b = self.b
        if view.compound is not None:
            return b.structure_image(view.compound, STRUCTURE_MAX_IN)
        picture = view.picture
        if picture is not None and (b.options.structures == "pictures" or view.mol is None):
            img = b.image(picture, STRUCTURE_MAX_IN, pdf_natural=True,
                          natural_factor=se.box_print_factor(self.project.settings,
                                                             view.scale_info, picture))
            if img:
                return img[0]
        if view.mol is not None:
            return self.drawn(view.mol, view.box["id"])
        return None

    def drawn(self, mol, key: str, scale: float = 1.0) -> str | None:
        """The program's drawing of `mol` (ACS style), in build/assets."""
        from .document import drawn_structure
        b = self.b
        svg = b.assets / f"{self.scheme.id}_{key}.svg"
        text, size = drawn_structure(mol, b.drawing)
        b.assets.mkdir(parents=True, exist_ok=True)
        svg.write_text(text, encoding="utf-8")
        b.svg_sizes[svg] = size
        img = b.image(svg, STRUCTURE_MAX_IN, scale=scale)
        return img[0] if img else None

    def inline(self, sid: str) -> str | None:
        """A structure in the conditions: a small picture on the text line."""
        b = self.b
        entry = se.inline_entry(self.scheme, sid)
        pct = float(self.layout["cond_structure_pct"]) / 100.0
        picture = se.inline_path(self.scheme, sid)
        mol = se.read_mol(se.path_of(self.scheme, entry.get("structure")))
        code = None
        if picture is not None and (b.options.structures == "pictures" or mol is None):
            factor = se.box_print_factor(self.project.settings, entry, picture)
            img = b.image(picture, STRUCTURE_MAX_IN, pdf_natural=True,
                          natural_factor=factor * pct)
            code = img[0] if img else None
        elif mol is not None:
            code = self.drawn(mol, sid, scale=pct)
        if code is None:
            return None
        # Its middle on the middle of the text line.
        return f"#box(baseline: 50% - 0.3em, {code})"

    def _inline_exists(self, sid: str) -> bool:
        entry = se.inline_entry(self.scheme, sid)
        return (se.inline_path(self.scheme, sid) is not None
                or se.read_mol(se.path_of(self.scheme, entry.get("structure"))) is not None)

    # ---- text

    def runs_code(self, runs: list[Run], where: str) -> str:
        """Runs → Typst: mentions and citations resolved, formulas subscripted, and any
        structures (@struct{…}) drawn in their place."""
        out, plain = [], []

        def flush():
            if plain:
                linked = self.b.links(list(plain), where)
                if self.b.options.format_text:
                    linked = format_chemistry(linked)
                out.append(runs_to_typst(linked))
                plain.clear()

        for run in runs:
            if run.structure:
                flush()
                code = self.inline(run.structure)
                if code is not None:
                    out.append(code)
                elif self._inline_exists(run.structure):
                    # (a missing one is already in scheme_editor.warnings: said once)
                    self.b.warnings.append(f"{where}: a structure in the conditions could "
                                           "not be drawn; left out.")
            else:
                plain.append(run)
        flush()
        return "".join(out)

    def lines(self, text: str, where: str) -> list[str]:
        """Each paragraph of a conditions box is one line (empty ones are left out)."""
        out = []
        for p in parse(text or ""):
            code = self.runs_code(p.runs, where)
            if code.strip():
                out.append(_content(code))
        return out

    def label_code(self, box: dict) -> str:
        markup = se.label_markup(self.project, self.scheme, box)
        if not markup:
            return "none"
        runs = [r for p in parse(markup) for r in p.runs]
        code = self.runs_code(runs, f"{self.where} ▸ ID label")
        return _content(code) if code.strip() else "none"

    def yield_runs(self, box: dict) -> list[Run]:
        text = se.box_yield(self.project, self.scheme, box)
        return [Run(text)] if text else []

    # ---- the scheme

    def items(self) -> list[str]:
        items: list[str] = []
        yields = self.layout["yields"]

        def add_boxes(boxes: list[dict], product: bool) -> list[tuple[dict, str]]:
            placed = []
            for box in boxes:
                view = se.box_view(self.project, self.scheme, box)
                code = self.box_image(view)
                if code is None:
                    continue   # (an empty box: compile warning from scheme_editor.warnings)
                if placed:
                    items.append('(kind: "plus")')
                dx, dy = se.label_offset(box)
                y = "none"
                if product and yields == "product":
                    runs = self.yield_runs(box)
                    if runs:
                        y = _content(runs_to_typst(runs))
                items.append(f'(kind: "mol", body: {code}, label: {self.label_code(box)}, '
                             f"dx: {dx:.2f}pt, dy: {dy:.2f}pt, yield: {y})")
                placed.append((box, code))
            return placed

        add_boxes(se.starting(self.scheme), product=False)
        for i, step in enumerate(se.steps(self.scheme)):
            where = f"{self.where} ▸ step {i + 1}"
            top = self.lines(step.get("top", ""), where)
            bottom = self.lines(step.get("bottom", ""), where)
            if yields == "conditions":
                line = self.conditions_yields(step)
                if line:
                    bottom.append(_content(line))
            ta = TYPST_ALIGN[se.conditions_align(self.project.settings, self.scheme, step,
                                                 "top")]
            ba = TYPST_ALIGN[se.conditions_align(self.project.settings, self.scheme, step,
                                                 "bottom")]
            items.append(f'(kind: "arrow", top: {_tuple(top)}, bottom: {_tuple(bottom)}, '
                         f"top-align: {ta}, bottom-align: {ba})")
            add_boxes(step["products"], product=True)
        return items

    def conditions_yields(self, step: dict) -> str:
        """The step's yields for under its conditions: "92%", or with several products
        "92% (3b), 5% (3c)"."""
        found = []
        for box in step["products"]:
            runs = self.yield_runs(box)
            if not runs:
                continue
            label = se.label_markup(self.project, self.scheme, box)
            found.append((runs, label))
        if not found:
            return ""
        if len(found) == 1:
            return runs_to_typst(found[0][0])
        parts = []
        for runs, label in found:
            code = runs_to_typst(runs)
            if label:
                ref = [r for p in parse(label) for r in p.runs]
                code += " (" + self.runs_code(ref, f"{self.where} ▸ ID label") + ")"
            parts.append(code)
        return ", ".join(parts)

    def code(self) -> str:
        for message in se.warnings(self.project, self.scheme):
            self.b.warnings.append(message)
        items = self.items()
        if not items:
            return ""
        lay = self.layout
        b = self.b
        if lay["show_label"]:
            b.kinds["scheme"] += 1
            label = b.labels.label(self.scheme.id) or "Scheme"
            caption = [Run(self.scheme.name)]
            if b.options.format_text:
                caption = format_chemistry(caption)
            label_args = [f"label: [{runs_to_typst([Run(label)])}]",
                          f"caption: {b.caption_code(caption)}"]
        else:   # (the default: no label or caption, not numbered; SPEC §13p.1)
            label_args = ["label: none", "caption: none"]
        size = float(lay["cond_size_pt"])
        args = [f"factor: {float(lay['scale_pct']):g}%",
                f"fit: {typst_string(lay['fit'])}",
                f"gap: {float(lay['gap_mm']):g}mm",
                f"before: {float(lay['arrow_before_mm']):g}mm",
                f"after: {float(lay['arrow_after_mm']):g}mm",
                f"min-arrow: {float(lay['arrow_min_mm']):g}mm",
                f"size: {size:g}pt", f"label-size: {size + 1:g}pt",
                *label_args,
                f"align-ids: {'true' if lay['align_ids'] else 'false'}",
                # (the conditions' printed size comes back from Typst under this key:
                # compile/run.py warns when shrinking made it too small)
                f"key: {typst_string(self.scheme.name)}"]
        return ("#si-reaction-scheme((\n  " + ",\n  ".join(items) + ",\n), "
                + ", ".join(args) + ")")


def editor_scheme(builder, scheme) -> str:
    """The Typst code of a scheme made with the scheme editor ("" if it has nothing to
    print yet)."""
    return SchemeWriter(builder, scheme).code()

