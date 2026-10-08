"""Made-up manuscript files for the manuscript check's tests (SPEC §13o).

Everything here is INVENTED (no real paper): a short "manuscript" about a made-up
coupling, as a PDF made with Typst, a Word .docx written by hand (with a ChemDraw-like
figure as an Enhanced Metafile drawn with Windows' own calls — Windows only), and a
ChemDraw .cdxml written by hand from RDKit 2D coordinates. Labels are drawn the usual
way: the ID under each structure, the values on the line(s) under it, in a grid of
two rows.

`build_si(parent)` makes the matching SI project, with deliberate differences:

    ID   manuscript                        SI                         expected finding
    3a   92%, 95% ee; methyl 4-bromo…      92%, 95% ee, same name     agrees
    3b   85%^b, 97:3 er (b: 48 h, 60 °C)   82%, 94% ee; 24 h, 60 °C   yield; footnote b (48 h)
    3c   70%, >20:1 dr; other structure;   70%, >20:1 dr, "Ethyl     structure; name
         "Ethyl 2-phenylpropanoate (3c)"   2-phenylacetate"
    3d   Table 1: 88, 91 ee (no footnote)  88%, 91% ee; GP A + 36 h   SI deviation (36 h vs 24 h)
    3e   Table 1: 2^c (c: 10 mol%, tol.)   76%; no 10 mol% in text    footnote c
    3h   64%^c                             64%; 10 mol% in toluene    footnote agrees
    3i   77%, 90% ee                       (no compound 3i)           missing in the SI
    3f   —                                 in the SI                  not in the manuscript
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import typst

SMILES = {
    "3a": "COC(=O)c1ccc(Br)cc1",
    "3b": "COC(=O)c1ccc(Cl)cc1",
    "3c": "CCOC(=O)Cc1ccccc1",
    "3c_other": "CCOC(=O)Cc1ccc(C)cc1",
    "3d": "CC(O)c1ccccc1",
    "3e": "OCc1ccccc1",
    "3f": "CCCCO",
    "3h": "CC(N)c1ccccc1",
    "3i": "CC(=O)c1ccccc1",
}

FOOTNOTE_B = "Reaction run for 48 h at 60 °C."
FOOTNOTE_C = "Using 10 mol% catalyst in toluene."


# ---------------------------------------------------------------------- PDF (Typst)

def manuscript_typ(with_figure_text: bool = True) -> str:
    sup = "#let sup(x) = super(typographic: false)[#x]\n"
    cell = ("align(center)[#box(width: 3cm, height: 1.6cm, stroke: 0.5pt)[] \\ {label}]")
    labels = ["*3a* \\ 92%, 95% ee", "*3b* \\ 85%#sup[b], 97:3 er", "*3c*, 70%, >20:1 dr",
              "*3h*#sup[c] \\ 64%", "*3i* \\ 77%, 90% ee"]
    if with_figure_text:
        grid = ("#grid(columns: 3, column-gutter: 1cm, row-gutter: 0.5cm,\n  "
                + ",\n  ".join(cell.format(label=l) for l in labels) + ")\n")
    else:   # the figure's text "outlined": only boxes, no text
        grid = "#grid(columns: 3, column-gutter: 1cm, row-gutter: 0.5cm, " + ", ".join(
            "box(width: 3cm, height: 2cm, stroke: 0.5pt)[]" for _ in labels) + ")\n"
    return (
        "#set page(width: 17cm, height: 24cm, margin: 1.5cm)\n#set text(size: 9pt)\n" + sup
        + "= A Made-Up Coupling for Testing\n\n"
        "The ester methyl 4-bromobenzoate (3a) was made on a large scale. Ethyl "
        "2-phenylpropanoate (3c) is a made-up example.\n\n"
        "*Scheme 2.* Scope of the made-up coupling#sup[a]\n\n" + grid
        + f"#sup[a]Isolated yields. #sup[b]{FOOTNOTE_B} #sup[c]{FOOTNOTE_C}\n\n"
        "*Table 1.* Further products\n\n"
        "#table(columns: 4, stroke: none, column-gutter: 1.5em, [Entry], [Product], "
        "[Yield (%)#sup[a]], [ee (%)], [1], [3d], [88], [91], [2#sup[c]], [3e], [76], [93])\n\n"
        "#sup[a]Isolated yield. #sup[c]" + FOOTNOTE_C + "\n\n"
        "After 3 h the mixture was filtered. Compound 3e was also made.\n")


def make_pdf(path: Path, with_figure_text: bool = True) -> Path:
    src = path.with_suffix(".typ")
    src.write_text(manuscript_typ(with_figure_text), encoding="utf-8")
    typst.compile(str(src), output=str(path))
    src.unlink()
    return path


# ---------------------------------------------------------------------- ChemDraw .cdxml

def _fragment(fid: int, smiles: str, x: float, y: float) -> tuple[str, list[float]]:
    """A CDXML fragment drawn from RDKit's 2D coordinates, top-left at (x, y)."""
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol = Chem.MolFromSmiles(smiles)
    Chem.Kekulize(mol, clearAromaticFlags=True)
    AllChem.Compute2DCoords(mol)
    conf = mol.GetConformer()
    pts = [(conf.GetAtomPosition(i).x * 14.4, -conf.GetAtomPosition(i).y * 14.4)
           for i in range(mol.GetNumAtoms())]
    minx, miny = min(p[0] for p in pts), min(p[1] for p in pts)
    pts = [(px - minx + x, py - miny + y) for px, py in pts]
    nodes = []
    for atom, (px, py) in zip(mol.GetAtoms(), pts):
        attrs = f'id="{fid}{atom.GetIdx() + 1:03d}" p="{px:.2f} {py:.2f}"'
        if atom.GetAtomicNum() != 6:
            attrs += f' Element="{atom.GetAtomicNum()}"'
            sym = atom.GetSymbol()
            nodes.append(f'<n {attrs}><t p="{px - 3:.2f} {py + 3:.2f}" BoundingBox="{px - 4:.2f} '
                         f'{py - 4:.2f} {px + 4:.2f} {py + 4:.2f}"><s>{sym}</s></t></n>')
        else:
            nodes.append(f"<n {attrs}/>")
    bonds = []
    for b in mol.GetBonds():
        order = int(b.GetBondTypeAsDouble())
        o = f' Order="{order}"' if order != 1 else ""
        bonds.append(f'<b id="{fid}9{b.GetIdx():03d}" B="{fid}{b.GetBeginAtomIdx() + 1:03d}" '
                     f'E="{fid}{b.GetEndAtomIdx() + 1:03d}"{o}/>')
    box = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts),
           max(p[1] for p in pts)]
    xml = (f'<fragment id="{fid}" BoundingBox="{box[0]:.2f} {box[1]:.2f} {box[2]:.2f} '
           f'{box[3]:.2f}">' + "".join(nodes) + "".join(bonds) + "</fragment>")
    return xml, box


def _text(x: float, y: float, runs: list[tuple[str, int]], width: float = 60) -> str:
    """A ChemDraw text object (runs: (text, face): 1 bold, 64 superscript)."""
    lines = sum(t.count("\n") for t, _f in runs) + 1
    body = "".join(f'<s font="3" size="10" face="{face}">{t}</s>' for t, face in runs)
    return (f'<t p="{x:.2f} {y + 9:.2f}" BoundingBox="{x:.2f} {y:.2f} {x + width:.2f} '
            f'{y + 11 * lines:.2f}" Justification="Center">{body}</t>')


def write_cdxml(path: Path, parts: list[str]) -> Path:
    """A .cdxml file from fragments and text objects (see _fragment and _text)."""
    path.write_text('<?xml version="1.0" encoding="UTF-8" ?>\n<CDXML><page id="1">'
                    + "\n".join(parts) + "</page></CDXML>\n", encoding="utf-8")
    return path


def make_cdxml(path: Path) -> Path:
    """Two rows of structures with their labels underneath: row 1 3a (ID and values on
    two lines), 3b (on one line, with a footnote marker), 3c (drawn as another
    structure than the SI's); row 2 3h (footnote c) and 3i."""
    parts = []
    cells = [("3a", SMILES["3a"], [("3a", 1), ("\n92%, 95% ee", 0)]),
             ("3b", SMILES["3b"], [("3b", 1), (", 85%", 0), ("b", 64), (", 97:3 er", 0)]),
             ("3c", SMILES["3c_other"], [("3c", 1), ("\n70%, >20:1 dr", 0)]),
             ("3h", SMILES["3h"], [("3h", 1), ("c", 64), ("\n64%", 0)]),
             ("3i", SMILES["3i"], [("3i", 1), ("\n77%, 90% ee", 0)])]
    for k, (_label, smiles, runs) in enumerate(cells):
        row, col = divmod(k, 3)
        x, y = 40 + col * 150, 40 + row * 170
        frag, box = _fragment(100 + k, smiles, x, y)
        parts.append(frag)
        cx = (box[0] + box[2]) / 2
        parts.append(_text(cx - 30, box[3] + 12, runs))
    parts.append(_text(40, 400, [("a", 64), ("Isolated yields. ", 0), ("b", 64),
                                 (FOOTNOTE_B + " ", 0), ("c", 64), (FOOTNOTE_C, 0)], 400))
    doc = ('<?xml version="1.0" encoding="UTF-8" ?>\n'
           '<!DOCTYPE CDXML SYSTEM "http://www.cambridgesoft.com/xml/cdxml.dtd" >\n'
           '<CDXML><page id="1">' + "\n".join(parts) + "</page></CDXML>\n")
    path.write_text(doc, encoding="utf-8")
    return path


# ---------------------------------------------------------------------- Word .docx

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _p(*runs) -> str:
    """A paragraph: runs are text or (text, "b"/"sup")."""
    out = []
    for r in runs:
        text, fmt = (r, "") if isinstance(r, str) else r
        props = {"b": "<w:rPr><w:b/></w:rPr>",
                 "sup": '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'}.get(fmt, "")
        out.append(f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>')
    return "<w:p>" + "".join(out) + "</w:p>"


def _tc(*runs) -> str:
    return "<w:tc>" + _p(*runs) + "</w:tc>"


def write_docx(path: Path, body: str, rels: str = "", media: dict | None = None) -> Path:
    """A minimal .docx: word/document.xml with this body XML (and relationships)."""
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>' + body
           + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        if rels:
            z.writestr("word/_rels/document.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://'
                       'schemas.openxmlformats.org/package/2006/relationships">' + rels
                       + "</Relationships>")
        for name, data in (media or {}).items():
            z.writestr(name, data)
    return path


def tc_props(text: str, props: str = "") -> str:
    """A table cell with cell properties (gridSpan, vMerge…)."""
    return f"<w:tc><w:tcPr>{props}</w:tcPr>" + _p(text) + "</w:tc>"


def figure_emf() -> bytes | None:
    """A ChemDraw-like figure (Windows only; None elsewhere): two structures drawn as lines
    with "3a" / "92%, 95% ee" under the first and "3b, 85%^b, 97:3 er" under the second,
    and a second row with "3h^c" / "64%"."""
    if sys.platform != "win32":
        return None
    sys.path.insert(0, str(Path(__file__).parent))
    import emf_samples as E
    with E.Metafile(120, 90) as m:
        dc = m.dc
        E.SetBkMode(dc, E.TRANSPARENT)
        pen = m.keep(E.CreatePen(0, 20, 0))
        m.select(pen)
        for cx, top in ((2500, 500), (7500, 500), (2500, 4800)):
            pts = [(cx - 1000, top + 1500), (cx - 500, top), (cx + 500, top),
                   (cx + 1000, top + 1500)]
            E.Polyline(dc, E.points(pts), len(pts))
        big = m.keep(E.font(-350))
        bold = m.keep(E.font(-350, weight=700))
        small = m.keep(E.font(-220))
        E.SetTextAlign(dc, E.TA_LEFT | E.TA_TOP)

        def put(x, y, pieces):
            for piece, kind in pieces:
                m.select({"b": bold, "sup": small}.get(kind, big))
                dy = -60 if kind == "sup" else 0
                E.text(dc, x, y + dy, piece)
                x += int(len(piece) * (110 if kind == "sup" else 200))
        put(2200, 2300, [("3a", "b")])
        put(1500, 2800, [("92%, 95% ee", "")])
        put(6200, 2300, [("3b", "b"), (", 85%", ""), ("b", "sup"), (", 97:3 er", "")])
        put(2200, 6600, [("3h", "b"), ("c", "sup")])
        put(2200, 7100, [("64%", "")])
    return m.data


def make_docx(path: Path, with_figure: bool = True) -> Path:
    emf = figure_emf() if with_figure else None
    body = [
        _p(("A Made-Up Coupling for Testing", "b")),
        _p("The ester methyl 4-bromobenzoate (3a) was made on a large scale; ethyl "
           "2-phenylpropanoate (3c) too."),
        _p(("Scheme 2.", "b"), " Scope of the made-up coupling."),
    ]
    if emf is not None:
        body.append('<w:p><w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
                    f'<v:imagedata xmlns:r="{R_NS}" r:id="rId9"/></v:shape></w:pict></w:r></w:p>')
    body.append(_p(("a", "sup"), "Isolated yields. ", ("b", "sup"), FOOTNOTE_B + " ",
                   ("c", "sup"), FOOTNOTE_C))
    body.append(_p(("Table 1.", "b"), " Further products"))
    rows = [("Entry", "Product", "Yield (%)", "ee (%)"), ("1", "3d", "88", "91"),
            (("2", ("c", "sup")), "3e", "76", "93")]
    trs = []
    for row in rows:
        cells = []
        for cell in row:
            cells.append(_tc(*cell) if isinstance(cell, tuple) else _tc(cell))
        trs.append("<w:tr>" + "".join(cells) + "</w:tr>")
    body.append("<w:tbl>" + "".join(trs) + "</w:tbl>")
    body.append(_p(("c", "sup"), FOOTNOTE_C))
    body.append(_p("After 3 h the mixture was filtered."))
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>' + "".join(body)
           + "</w:body></w:document>")
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">'
            + ('<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/'
               '2006/relationships/image" Target="media/image1.emf"/>' if emf else "")
            + "</Relationships>")
    types = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.'
             'org/package/2006/content-types"><Default Extension="xml" ContentType="application/'
             'xml"/><Default Extension="emf" ContentType="image/x-emf"/><Override PartName="/'
             'word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
             'wordprocessingml.document.main+xml"/></Types>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
        if emf:
            z.writestr("word/media/image1.emf", emf)
    return path


# ---------------------------------------------------------------------- the SI project

def build_si(parent: Path):
    """The matching (made-up) SI: see the module's table for what differs."""
    from si_assistant.core.project import Project
    p = Project.create(parent, "Made-up SI")
    p.acquire_lock("Tester")
    s = p.add_section("data", "Scope of the coupling")
    ids = ["3a", "3b", "3c", "3d", "3e", "3h", "3f"]
    p.set_id_table(s.id, ids)
    gp = p.add_general_procedure(s.id, "General Procedure A")
    p.write_gp_text(gp["id"], "The aryl halide (0.20 mmol), the catalyst (5 mol%) and "
                              "K3PO4 (2.0 equiv) in THF were stirred at 60 °C for 24 h.")
    p.update_general_procedure(gp["id"], sentence="Prepared according to {GP} from "
                                                  "{starting material} ({scale}).")
    data = {
        "3a": ("Methyl 4‑bromobenzoate", "92", "95% ee", "", "", ""),
        "3b": ("Methyl 4-chlorobenzoate", "82", "94% ee", "",
               "The mixture was stirred at 60 °C for 24 h.", ""),
        "3c": ("Ethyl 2-phenylacetate", "70", "", ">20:1", "", ""),
        "3d": ("1-Phenylethan-1-ol", "88", "91% ee", "", "", "gp36"),
        "3e": ("Phenylmethanol", "76", "93% ee", "", "Stirred in THF for 24 h.", ""),
        "3h": ("1-Phenylethan-1-amine", "64", "", "",
               "Using 10 mol% of the catalyst in toluene at 60 °C.", ""),
        "3f": ("Butan-1-ol", "50", "", "", "", ""),
    }
    compounds = {}
    for label in ids:
        name, y, ee, dr, text, how = data[label]
        c = p.add_compound(s.id, name=name)
        compounds[label] = c
        p.set_structure(c, SMILES[label])
        c.values["yield"] = {"percent": y, "mass": "", "mass_unit": "mg"}
        if ee:
            p.add_field(c, {"key": "ee"})
            c.values["ee"] = {"value": ee, "method": "HPLC"}
        if dr:
            p.add_field(c, {"key": "dr"})
            c.values["dr"] = {"value": dr, "method": ""}
        if how == "gp36":
            c.values["procedure"] = {"gp": gp["id"], "values": {
                "starting material": "1a (35 mg)", "scale": "0.20 mmol"}}
            p.save_compound(c)
            p.write_compound_text(c, "procedure", "The mixture was stirred at 60 °C for 36 h.")
        else:
            p.save_compound(c)
            if text:
                p.write_compound_text(c, "procedure", text)
    return p, compounds
