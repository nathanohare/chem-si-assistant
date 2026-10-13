"""Importing an SI, phase I2 (SPEC §13u.7): structures from the ChemDraw objects in a Word
file (CDX inside an OLE compound file) and structure pictures cut out of a PDF. Made-up
structures only (tests/chemdraw_files.py builds the files); the PDF tests use the example
SI shipped with the program."""

import io
import sys
import tempfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from si_assistant import resources
from si_assistant.core import chem
from si_assistant.core import si_import as I
from si_assistant.core import si_import_apply as A
from si_assistant.core import si_import_read as R
from si_assistant.core.project import Project
from tests import chemdraw_files as CF

ROOT = Path(__file__).resolve().parent.parent


def smiles(molblock_or_mol) -> str:
    from rdkit import Chem
    mol = Chem.MolFromMolBlock(molblock_or_mol) if isinstance(molblock_or_mol, str) \
        else molblock_or_mol
    return Chem.MolToSmiles(mol)


def calcd(formula_m: str, adduct: str) -> str:
    info = chem.info_from_counts(chem.parse_formula(formula_m))
    return f"{chem.ion_mz(info, adduct).mz:.4f}"


def new_project(tmp_path):
    p = Project.create(tmp_path, "P")
    p.acquire_lock("Tester")
    p.enable_undo()
    return p


# ---------------------------------------------------------------------- ChemDraw (CDX)

def test_reading_a_chemdraw_cdx_drawing():
    from si_assistant.core import cdx
    atoms, bonds = CF.phenol()
    data = CF.Cdx().fragment(atoms, bonds, (0, 0, 40, 30)).bytes()
    assert [smiles(m) for m in cdx.rdkit_mols(data)] == ["Oc1ccccc1"]
    with pytest.raises(cdx.CdxError):
        cdx.molecules(b"not chemdraw")
    with pytest.raises(cdx.CdxError):
        cdx.molecules(data[:40])                     # (cut short)


def test_cdx_wedges_and_abbreviations():
    from rdkit import Chem
    from si_assistant.core import cdx
    # 1-phenylethanol drawn with a wedge to the O, the phenyl as an abbreviation ("Ph")
    ph = [(6, 0, 0, 0), (6, 12, -7, 0), (6, 24, 0, 0), (6, 24, 14, 0), (6, 12, 21, 0),
          (6, 0, 14, 0)]
    ph_bonds = [(0, 1, 2, 0), (1, 2, 1, 0), (2, 3, 2, 0), (3, 4, 1, 0), (4, 5, 2, 0),
                (5, 0, 1, 0)]
    atoms = [(6, 0, 0, 0), (6, 14.4, 0, 0), (8, 0, -14.4, 0), (6, -12.5, 7.2, 0)]
    bonds = [(0, 1, 1, 0), (0, 2, 1, 6), (0, 3, 1, 0)]
    data = CF.Cdx().fragment(atoms, bonds, (0, 0, 40, 40),
                             nickname=(1, ph, ph_bonds, 0)).bytes()
    mol = cdx.rdkit_mols(data)[0]
    assert Chem.MolToSmiles(mol, isomericSmiles=False) == "CC(O)c1ccccc1"
    assert "@" in Chem.MolToSmiles(mol)                  # stereo from the wedge
    # the abbreviation's atoms were laid out anew: no two atoms on top of each other
    conf = mol.GetConformer()
    pts = [conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())]
    assert min((a - b).Length() for i, a in enumerate(pts) for b in pts[i + 1:]) > 0.3


def test_cdx_charges():
    from si_assistant.core import cdx
    data = (CF.Cdx().fragment([(7, 0, 0, 1), (6, 14, 0, 0), (6, 0, 14, 0), (6, -14, 0, 0),
                               (6, 0, -14, 0)], [(0, k, 1, 0) for k in (1, 2, 3, 4)],
                              (-20, -20, 20, 20))
            .fragment([(35, 40, 0, -1)], [], (35, -5, 45, 5)).bytes())
    assert sorted(smiles(m) for m in cdx.rdkit_mols(data)) == ["C[N+](C)(C)C", "[Br-]"]


def test_labels_name_the_structures_under_them():
    from si_assistant.core import cdx
    a1, b1 = CF.phenol(0, 0)
    a2, b2 = [(6, 100, 0, 0), (8, 114, 0, 0)], [(0, 1, 1, 0)]
    data = (CF.Cdx().fragment(a1, b1, (-15, -15, 30, 25)).label("4a", (0, 35, 10, 42))
            .fragment(a2, b2, (98, -3, 116, 3)).label("4b", (100, 35, 112, 42))
            .label("THF, 20 °C", (40, -10, 80, -4)).arrow().bytes())
    structures, _labels = cdx.drawing(data, I.is_id_label)
    assert sorted((s.label, smiles(cdx.to_rdkit(s.molecule))) for s in structures) == [
        ("4a", "Oc1ccccc1"), ("4b", "CO")]
    assert not cdx.is_plain(data)
    assert cdx.is_plain(CF.Cdx().fragment(a1, b1, (-15, -15, 30, 25)).bytes())


def test_ole_compound_files():
    from si_assistant.core import ole
    small, big = b"x" * 300, bytes(range(256)) * 40      # (mini stream / regular sectors)
    data = CF.ole_file({"CONTENTS": small, "Big": big})
    f = ole.OleFile(data)
    assert sorted(f.stream_names()) == ["Big", "CONTENTS"]
    assert f.read("contents") == small and f.read("Big") == big
    assert ole.read_stream(data, "nope") is None
    assert ole.read_stream(b"not an ole file", "CONTENTS") is None
    assert ole.read_stream(data[:600], "Big") is None       # (cut short)


# ---------------------------------------------------------------------- Word files

def structure_docx(tmp_path, hrms: str) -> Path:
    """A scheme labelling 5a (phenol) and 5b (methanol), then 5a, 5b and 5c, with 5c's own
    unlabelled drawing (phenol again) in its heading."""
    atoms, bonds = CF.phenol()
    scheme = (CF.Cdx().fragment(atoms, bonds, (-15, -15, 30, 25)).label("5a", (0, 35, 10, 42))
              .fragment([(6, 100, 0, 0), (8, 114, 0, 0)], [(0, 1, 1, 0)], (98, -3, 116, 3))
              .label("5b", (100, 35, 112, 42)).arrow().bytes())
    plain = CF.Cdx().fragment(atoms, bonds, (-15, -15, 30, 25)).bytes()
    path = tmp_path / "si.docx"
    bold = "<w:r><w:rPr><w:b/></w:rPr><w:t>{}</w:t></w:r>"
    text = "<w:r><w:t>{}</w:t></w:r>"
    path.write_bytes(CF.docx_with_objects([
        (text.format("Scheme of the work"), 0),
        (bold.format("Phenol (5a)"), None),
        (text.format(f"White solid (10 mg, 50%). {hrms}"), None),
        (bold.format("Methanol (5b)"), None),
        (text.format("Colorless liquid (5 mg, 20%)."), None),
        (bold.format("Benzenol (5c)"), 1),
        (text.format("White solid (1 mg, 5%)."), None),
    ], [scheme, plain]))
    return path


def hrms_line(formula_m: str, ion: str) -> str:
    mz = calcd(formula_m, "[M+H]+")
    return f"HRMS (ESI) m/z: [M+H]+ calcd for {ion} {mz}; found {mz}."


def test_structures_from_chemdraw_objects_in_word(tmp_path):
    read = R.read_file(structure_docx(tmp_path, hrms_line("C6H6O", "C6H7O")))
    assert [len(p.objects) for p in read.paras if p.objects] == [1, 1]
    plan = I.analyse(read.paras)
    by = {c.cid: c for c in plan.compounds}
    # by its label in the scheme (no picture: the drawing is a scheme)
    assert smiles(by["5a"].structure) == "Oc1ccccc1" and by["5a"].structure_picture is None
    assert "labelled “5a”" in by["5a"].structure_note
    assert smiles(by["5b"].structure) == "CO"
    # unlabelled, next to its own text, alone in its drawing
    assert smiles(by["5c"].structure) == "Oc1ccccc1"
    assert "next to its text" in by["5c"].structure_note
    assert not [w for c in plan.compounds for w in c.warnings if "structure" in w]


def test_a_structure_that_does_not_fit_the_hrms_is_reported(tmp_path):
    read = R.read_file(structure_docx(tmp_path, hrms_line("C7H8O", "C7H9O")))
    a = next(c for c in I.analyse(read.paras).compounds if c.cid == "5a")
    assert smiles(a.structure) == "Oc1ccccc1"
    assert any("does not fit the formula of its HRMS" in w for w in a.warnings)


def test_a_mislabelled_structure_is_found_by_its_formula(tmp_path):
    # 5a's HRMS fits only the structure labelled 5b: that one is taken, with a warning
    read = R.read_file(structure_docx(tmp_path, hrms_line("CH4O", "CH5O")))
    a = next(c for c in I.analyse(read.paras).compounds if c.cid == "5a")
    assert smiles(a.structure) == "CO"
    assert any("was taken instead" in w for w in a.warnings)


def test_imported_structures_are_stored(tmp_path):
    read = R.read_file(structure_docx(tmp_path, hrms_line("C6H6O", "C6H7O")))
    plan = I.analyse(read.paras)
    plan.compounds[1].use_structure = False                  # (unticked: not stored)
    p = new_project(tmp_path / "p")
    result = A.apply_plan(p, plan, "si.docx")
    comps = [p.compound(i) for i in result.created]
    assert p.read_structure(comps[0]) is not None
    assert p.read_structure(comps[1]) is None
    assert comps[0].values["hrms"]["formula"] == ""          # (the structure gives it now)
    p.close()


# ---------------------------------------------------------------------- PDF files

@pytest.fixture(scope="module")
def example_read():
    return R.read_file(resources.example_pdf())


def test_structure_pictures_cut_out_of_a_pdf(qapp, example_read):
    from si_assistant.core import si_import_pdf as P
    plan = I.analyse(example_read.paras)
    assert P.add_pictures(resources.example_pdf(), plan, I.STEREO + I.ID) == 7
    page = PdfReader(io.BytesIO(plan.compounds[0].picture_pdf)).pages[0]
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    assert 20 < w < 150 and 20 < h < 120                 # trimmed to the drawing
    assert "between its heading and its text" in plan.compounds[0].structure_note


def test_pdf_pictures_are_stored_and_compile(qapp, example_read):
    from si_assistant.core import si_import_pdf as P
    sys.path.insert(0, str(ROOT / "tools"))
    import make_example_project as M
    plan = I.analyse(example_read.paras)
    P.add_pictures(resources.example_pdf(), plan, I.STEREO + I.ID)
    short = Path(tempfile.mkdtemp(prefix="csa-imp-"))
    p = Project.create(short, "Imported")
    try:
        result = A.apply_plan(p, plan, "Example SI.pdf")
        c = p.compound(result.created[0])
        assert p.structure_image_path(c).suffix == ".pdf"
        compiled = M.compile_si(p, "ACS")
    finally:
        p.close()
    assert compiled.warnings == []
