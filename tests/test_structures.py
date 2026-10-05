import pytest

from si_assistant.core import chem
from si_assistant.core.project import Project, ProjectError


@pytest.fixture
def compound(project):
    section = project.add_section("data", "Products")
    return project.add_compound(section.id, name="test")


def nuclei(c):
    return [r["nucleus"] for r in c.values["nmr"]]


def test_set_and_read_structure(project, compound):
    assert project.structure_info(compound) is None
    assert project.set_structure(compound, "CCOC(=O)c1ccoc1C") == []
    assert (compound.folder / "structure.mol").is_file()
    reopened = Project.open(project.folder)
    info = reopened.structure_info(reopened.compound(compound.id))
    assert info.formula == "C8H10O3"
    assert project.completion(compound)[2].count("Structure") == 0


def test_bad_structure_leaves_old_one(project, compound):
    project.set_structure(compound, "CCO")
    with pytest.raises(chem.ChemError):
        project.set_structure(compound, "C1CC")
    assert project.structure_info(compound).formula == "C2H6O"


def test_structure_cache_notices_changes(project, compound):
    project.set_structure(compound, "CCO")
    assert project.structure_info(compound).formula == "C2H6O"
    project.set_structure(compound, "CCCO")
    assert project.structure_info(compound).formula == "C3H8O"


def test_fluorine_adds_19f_report_once(project, compound):
    assert project.set_structure(compound, "FC(F)(F)c1ccccc1") == ["19F"]
    assert nuclei(compound) == ["1H", "13C", "19F"]
    assert project.set_structure(compound, "Fc1ccccc1") == []  # already there
    assert nuclei(Project.open(project.folder).compound(compound.id)) == ["1H", "13C", "19F"]


def test_phosphorus_and_boron_follow_settings(project, compound):
    assert project.set_structure(compound, "OB(O)c1ccc(P(C)(C)=O)cc1") == []
    project.settings["auto_nmr_31P"] = True
    project.settings["auto_nmr_11B"] = True
    assert project.set_structure(compound, "OB(O)c1ccc(P(C)(C)=O)cc1") == ["31P", "11B"]


def test_no_nmr_field_no_auto_reports(project, compound):
    project.remove_field(compound, "nmr")
    assert project.set_structure(compound, "Fc1ccccc1") == []


def test_clear_and_restore_structure(project, compound):
    project.set_structure(compound, "CCO")
    project.clear_structure(compound)
    assert project.structure_info(compound) is None
    entry = project.list_trash()[0]
    project.restore_from_trash(entry.id)
    assert project.structure_info(compound).formula == "C2H6O"


def scheme_molblock(*smiles_left_to_right):
    """A MOL block holding several structures side by side, like a copied ChemDraw scheme."""
    from rdkit import Chem
    from rdkit.Geometry import Point3D
    combined = None
    for i, smi in enumerate(smiles_left_to_right):
        mol = chem.parse_structure(smi)
        conf = mol.GetConformer()
        for a in range(mol.GetNumAtoms()):
            p = conf.GetAtomPosition(a)
            conf.SetAtomPosition(a, Point3D(p.x + 20 * i, p.y, 0))
        combined = mol if combined is None else Chem.CombineMols(combined, mol)
    return chem.mol_to_block(combined)


def test_scheme_pieces_left_to_right():
    pieces = chem.scheme_pieces(scheme_molblock("OC(=O)c1ccccc1", "[Na+]", "CCOC(=O)c1ccccc1"))
    assert [chem.mol_info(m).formula for m in pieces] == ["C7H6O2", "C9H10O2"]  # Na+ left out
    mol, n = chem.rightmost_structure(scheme_molblock("CCO", "CC=O", "[Cl-]"))
    assert n == 2 and chem.mol_info(mol).formula == "C2H4O"


def test_rightmost_structure_from_reaction():
    from rdkit.Chem import rdChemReactions
    rxn = rdChemReactions.ReactionFromSmarts("[CH3:1][OH:2]>>[CH2:1]=[O:2]", useSmiles=True)
    mol, n = chem.rightmost_structure(rdChemReactions.ReactionToRxnBlock(rxn))
    assert chem.mol_info(mol).formula == "CH2O"


def test_same_structure_key():
    a = chem.parse_structure("OC(=O)c1ccccc1")
    b = chem.parse_structure("c1ccccc1C(O)=O")
    assert chem.same_structure_key(a) == chem.same_structure_key(b)
    salt = chem.parse_structure("C(c1ccccc1)[n+]1ccccc1.[Br-]")
    cation = chem.parse_structure("C(c1ccccc1)[n+]1ccccc1")
    assert chem.same_structure_key(salt) == chem.same_structure_key(cation)
    r = chem.parse_structure("C[C@H](N)C(=O)O")
    s = chem.parse_structure("C[C@@H](N)C(=O)O")
    assert chem.same_structure_key(r) != chem.same_structure_key(s)
    assert chem.same_structure_key(r, stereo=False) == chem.same_structure_key(s, stereo=False)

def test_chemdraw_picture_kept_with_pasted_structure(project, compound):
    project.set_structure(compound, "CCO", picture=b"\x89PNG chemdraw")
    assert project.structure_picture_source(compound) == "chemdraw"
    assert project.structure_image_path(compound).read_bytes() == b"\x89PNG chemdraw"
    # Changing the structure another way makes ChemDraw's picture outdated: it goes to Trash.
    project.set_structure(compound, "CCCO")
    assert project.structure_image_path(compound) is None
    assert project.list_trash()[0].name.startswith("structure_image")


def test_own_picture_survives_structure_change(project, compound):
    project.set_structure(compound, "CCO")
    project.set_structure_image(compound, data=b"\x89PNG mine")
    assert project.structure_picture_source(compound) == "user"
    project.set_structure(compound, "CCCO")
    assert project.structure_image_path(compound).read_bytes() == b"\x89PNG mine"


def test_pasted_structure_image_bytes(project, compound):
    project.set_structure_image(compound, data=b"\x89PNG pasted")
    assert project.structure_image_path(compound).read_bytes() == b"\x89PNG pasted"


def test_structure_image_override(project, compound, tmp_path):
    img = tmp_path / "drawn.png"
    img.write_bytes(b"\x89PNG fake")
    project.set_structure_image(compound, img)
    assert project.structure_image_path(compound).name == "structure_image.png"
    project.remove_structure_image(compound)
    assert project.structure_image_path(compound) is None
    project.restore_from_trash(project.list_trash()[0].id)
    assert project.structure_image_path(compound).read_bytes() == b"\x89PNG fake"
    with pytest.raises(ProjectError):
        bad = tmp_path / "x.pdf"
        bad.write_bytes(b"x")
        project.set_structure_image(compound, bad)
