"""Reading structures from ChemDraw's binary format (CDX), e.g. the drawing inside a
ChemDraw object pasted into Word (SPEC §13u.7). RDKit's Python interface reads only
ChemDraw's XML format, so this reads the binary one into a MOL block that RDKit then
reads as usual (stereochemistry from the wedges and the 2D coordinates).

A CDX file is a tree of tagged objects (document, page, fragment, node = atom, bond,
text…), each with properties. Read here: every fragment's atoms (element, charge,
isotope, explicit hydrogens, position), bonds (order, wedges) and abbreviations such as
Ph, OMe or Boc (a node holding a fragment of its own, joined at its connection point),
which are expanded. Text, arrows, graphics and the rest are skipped.

Each drawing gives a list of `Molecule`s: the separate structures in it (two pieces drawn
next to each other, e.g. a cation and its counter-ion, are one Molecule if they are one
fragment in ChemDraw; ChemDraw keeps a salt as one fragment when its pieces were drawn
together, else as two). Never raises for bad input: CdxError says what is wrong.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"VjCD0100"
HEADER = 22                      # magic, 4 bytes of byte-order mark, 10 reserved

# Objects (tags ≥ 0x8000)
OBJ_DOCUMENT, OBJ_PAGE, OBJ_GROUP, OBJ_FRAGMENT, OBJ_NODE, OBJ_BOND = (
    0x8000, 0x8001, 0x8002, 0x8003, 0x8004, 0x8005)
# Properties
PROP_POSITION = 0x0200           # CDXPoint2D: y, x (INT32 each, 1/65536 pt)
PROP_NODE_TYPE = 0x0400
PROP_ELEMENT = 0x0402
PROP_ISOTOPE = 0x0420
PROP_CHARGE = 0x0421
PROP_RADICAL = 0x0422
PROP_NUM_H = 0x042B
PROP_BOND_ORDER = 0x0600
PROP_BOND_DISPLAY = 0x0601
PROP_BOND_BEGIN = 0x0604
PROP_BOND_END = 0x0605
# Node types
NODE_ELEMENT, NODE_NICKNAME, NODE_FRAGMENT, NODE_EXTERNAL = 1, 4, 5, 12
GENERIC_NODE_TYPES = {2, 3, 7, 8, 9, 10, 11, 13}   # lists, R groups, link nodes…
# Bond orders (bit flags) and displays
ORDERS = {0x0001: 1, 0x0002: 2, 0x0004: 3, 0x0080: 4}   # 4 = aromatic in a MOL block
WEDGE_BEGIN, WEDGE_END, HASH_BEGIN, HASH_END = 6, 7, 3, 4
WAVY = 8


class CdxError(ValueError):
    """The bytes are not a CDX drawing that can be read."""


@dataclass
class _Obj:
    tag: int
    oid: int
    props: dict[int, bytes] = field(default_factory=dict)
    children: list["_Obj"] = field(default_factory=list)


def _parse(data: bytes) -> _Obj:
    if not data.startswith(MAGIC):
        raise CdxError("not a ChemDraw (CDX) file")
    pos = HEADER
    root = _Obj(0, 0)
    stack = [root]
    n = len(data)
    while pos + 2 <= n:
        tag, = struct.unpack_from("<H", data, pos)
        pos += 2
        if tag == 0:                         # end of the current object
            if len(stack) > 1:
                stack.pop()
                continue
            break
        if tag & 0x8000:                     # an object: its ID, then its contents
            if pos + 4 > n:
                raise CdxError("cut short")
            oid, = struct.unpack_from("<I", data, pos)
            pos += 4
            obj = _Obj(tag, oid)
            stack[-1].children.append(obj)
            stack.append(obj)
            if len(stack) > 200:
                raise CdxError("nested too deeply")
            continue
        if pos + 2 > n:                      # a property: its length, then its bytes
            raise CdxError("cut short")
        length, = struct.unpack_from("<H", data, pos)
        pos += 2
        if length == 0xFFFF:
            if pos + 4 > n:
                raise CdxError("cut short")
            length, = struct.unpack_from("<I", data, pos)
            pos += 4
        if pos + length > n:
            raise CdxError("cut short")
        stack[-1].props[tag] = data[pos:pos + length]
        pos += length
    if len(stack) > 1:                       # objects never closed: the file ends early
        raise CdxError("cut short")
    return root


def _int(b: bytes | None, default: int = 0) -> int:
    if not b:
        return default
    if len(b) == 1:
        return struct.unpack("<b", b)[0]
    if len(b) == 2:
        return struct.unpack("<h", b)[0]
    if len(b) >= 4:
        return struct.unpack("<i", b[:4])[0]
    return default


def _uint(b: bytes | None, default: int = 0) -> int:
    if not b:
        return default
    return int.from_bytes(b[:4], "little")


@dataclass
class Atom:
    element: int = 6
    charge: int = 0
    isotope: int = 0
    hydrogens: int | None = None
    radical: int = 0
    x: float = 0.0
    y: float = 0.0
    generic: bool = False          # an R group, a list, a link node… (no element)
    placed: bool = True            # False: an unfolded abbreviation's atom (laid out anew)


@dataclass
class Bond:
    a: int
    b: int
    order: int = 1
    stereo: int = 0                # MOL block: 1 wedge, 6 hash, 4 wavy


@dataclass
class Molecule:
    atoms: list[Atom] = field(default_factory=list)
    bonds: list[Bond] = field(default_factory=list)

    def molblock(self) -> str:
        """The V2000 MOL block (coordinates in Å-like units, y up)."""
        lines = ["", "  Chem SI Assistant (from ChemDraw)", "",
                 f"{len(self.atoms):3d}{len(self.bonds):3d}  0  0  0  0  0  0  0  0999 V2000"]
        from rdkit.Chem import GetPeriodicTable
        table = GetPeriodicTable()
        charges, isotopes, radicals = [], [], []
        for i, a in enumerate(self.atoms, 1):
            symbol = "R" if a.generic else table.GetElementSymbol(a.element)
            lines.append(f"{a.x:10.4f}{a.y:10.4f}{0:10.4f} {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
            if a.charge:
                charges.append((i, a.charge))
            if a.isotope:
                isotopes.append((i, a.isotope))
            if a.radical:
                radicals.append((i, a.radical))
        for b in self.bonds:
            lines.append(f"{b.a + 1:3d}{b.b + 1:3d}{b.order:3d}{b.stereo:3d}")
        for tag, items in (("CHG", charges), ("ISO", isotopes), ("RAD", radicals)):
            for k in range(0, len(items), 8):
                part = items[k:k + 8]
                lines.append(f"M  {tag}{len(part):3d}" + "".join(f" {i:3d} {v:3d}" for i, v in part))
        lines.append("M  END")
        return "\n".join(lines) + "\n"


def _position(obj: _Obj) -> tuple[float, float]:
    p = obj.props.get(PROP_POSITION)
    if p is None or len(p) < 8:
        return 0.0, 0.0
    y, x = struct.unpack_from("<ii", p, 0)
    return x / 65536.0, y / 65536.0


def _fragment(frag: _Obj) -> tuple[list[Atom], list[Bond], dict[int, int], int | None]:
    """A fragment's atoms and bonds ({node id: atom index}), with its abbreviations
    expanded; and the index of the atom joined to its connection point, if it has one
    (for an abbreviation's own fragment)."""
    atoms: list[Atom] = []
    bonds: list[Bond] = []
    index: dict[int, int] = {}
    external: set[int] = set()
    # an abbreviation's node id -> (its atoms' index offset, the atom at its connection)
    nick: dict[int, int] = {}
    for child in frag.children:
        if child.tag != OBJ_NODE:
            continue
        ntype = _int(child.props.get(PROP_NODE_TYPE), NODE_ELEMENT)
        x, y = _position(child)
        inner = next((c for c in child.children if c.tag == OBJ_FRAGMENT), None)
        if ntype == NODE_EXTERNAL:
            external.add(child.oid)
            continue
        if ntype in (NODE_NICKNAME, NODE_FRAGMENT) and inner is not None:
            in_atoms, in_bonds, _in_index, attach = _fragment(inner)
            if not in_atoms:
                continue
            # The abbreviation's atoms, placed around the node where its label is (their
            # own coordinates are relative to its connection atom).
            ref = in_atoms[attach if attach is not None else 0]
            off = len(atoms)
            for k, a in enumerate(in_atoms):
                atoms.append(Atom(a.element, a.charge, a.isotope, a.hydrogens, a.radical,
                                  x + (a.x - ref.x), y + (a.y - ref.y), a.generic,
                                  placed=k == (attach if attach is not None else 0)))
            for b in in_bonds:
                bonds.append(Bond(b.a + off, b.b + off, b.order, b.stereo))
            nick[child.oid] = off + (attach if attach is not None else 0)
            continue
        atom = Atom(x=x, y=y)
        if ntype in GENERIC_NODE_TYPES:
            atom.generic = True
        element = child.props.get(PROP_ELEMENT)
        if element is not None:
            atom.element = _int(element, 6)
        atom.charge = _int(child.props.get(PROP_CHARGE))
        if abs(atom.charge) > 8:            # (some files keep the charge × 2^24)
            atom.charge = atom.charge >> 24
        atom.isotope = _int(child.props.get(PROP_ISOTOPE))
        if PROP_NUM_H in child.props:
            atom.hydrogens = _uint(child.props[PROP_NUM_H])
        rad = _int(child.props.get(PROP_RADICAL))
        atom.radical = {1: 1, 2: 2, 3: 3}.get(rad, 0)
        index[child.oid] = len(atoms)
        atoms.append(atom)
    for oid, at in nick.items():
        index[oid] = at
    attach = None
    for child in frag.children:
        if child.tag != OBJ_BOND:
            continue
        begin = _uint(child.props.get(PROP_BOND_BEGIN))
        end = _uint(child.props.get(PROP_BOND_END))
        if begin in external or end in external:     # the abbreviation's connection
            other = end if begin in external else begin
            if other in index:
                attach = index[other]
            continue
        if begin not in index or end not in index or begin == end:
            continue
        order_flags = _uint(child.props.get(PROP_BOND_ORDER), 1) or 1
        order = next((v for k, v in ORDERS.items() if order_flags & k), 1)
        display = _int(child.props.get(PROP_BOND_DISPLAY))
        a, b, stereo = index[begin], index[end], 0
        if order == 1:
            if display in (WEDGE_BEGIN, WEDGE_END):
                stereo = 1
            elif display in (HASH_BEGIN, HASH_END):
                stereo = 6
            elif display == WAVY:
                stereo = 4
            if display in (WEDGE_END, HASH_END):
                a, b = b, a                     # (the narrow end is where it begins)
        bonds.append(Bond(a, b, order, stereo))
    return atoms, bonds, index, attach


def molecules(data: bytes) -> list[Molecule]:
    """The structures drawn in a CDX file, in drawing order (one per top-level fragment;
    abbreviations expanded). Raises CdxError for a file that can't be read."""
    root = _parse(data)
    out: list[Molecule] = []

    def walk(obj: _Obj):
        for child in obj.children:
            if child.tag == OBJ_FRAGMENT:
                atoms, bonds, _index, _attach = _fragment(child)
                if atoms:
                    # y up in a MOL block; ChemDraw's points are 1/72 inch: ×(1/14.4)
                    # gives the usual 1.5 Å bond for ChemDraw's 0.6 cm default bond.
                    for a in atoms:
                        a.x, a.y = a.x / 14.4, -a.y / 14.4
                    out.append(Molecule(atoms, bonds))
            elif child.tag in (OBJ_DOCUMENT, OBJ_PAGE, OBJ_GROUP):
                walk(child)
    walk(root)
    return out


PROP_BOUNDING_BOX = 0x0204       # CDXRectangle: top, left, bottom, right
PROP_TEXT = 0x0700               # CDXString: style runs, then the characters
OBJ_TEXT = 0x8006


@dataclass
class Structure:
    """One structure of a drawing, where it is, and the compound label written next to
    it ("" if none)."""
    molecule: Molecule
    box: tuple[float, float, float, float]      # left, top, right, bottom (pt, y down)
    label: str = ""
    charge: int = 0


@dataclass
class Label:
    text: str
    box: tuple[float, float, float, float]


def _box(obj: _Obj) -> tuple[float, float, float, float] | None:
    b = obj.props.get(PROP_BOUNDING_BOX)
    if b is None or len(b) < 16:
        return None
    top, left, bottom, right = struct.unpack_from("<iiii", b, 0)
    return left / 65536.0, top / 65536.0, right / 65536.0, bottom / 65536.0


def _text(obj: _Obj) -> str:
    t = obj.props.get(PROP_TEXT)
    if not t or len(t) < 2:
        return ""
    runs, = struct.unpack_from("<H", t, 0)
    return t[2 + 10 * runs:].decode("cp1252", "replace").strip()


def drawing(data: bytes, is_label=None) -> tuple[list[Structure], list[Label]]:
    """The structures of a CDX drawing with their places, and its short texts that
    `is_label` (a function of the text) takes for compound labels ("3a", "3a-DA"); each
    structure gets the label nearest below or beside it. Raises CdxError."""
    root = _parse(data)
    frags: list[tuple[_Obj, tuple]] = []
    labels: list[Label] = []

    def walk(obj: _Obj):
        for child in obj.children:
            if child.tag == OBJ_FRAGMENT:
                box = _box(child)
                if box is not None:
                    frags.append((child, box))
            elif child.tag == OBJ_TEXT:
                text, box = _text(child), _box(child)
                if text and box is not None and (is_label is None or is_label(text)):
                    labels.append(Label(text, box))
            elif child.tag in (OBJ_DOCUMENT, OBJ_PAGE, OBJ_GROUP):
                walk(child)
    walk(root)
    structures: list[Structure] = []
    for frag, box in frags:
        atoms, bonds, _index, _attach = _fragment(frag)
        if not atoms:
            continue
        for a in atoms:
            a.x, a.y = a.x / 14.4, -a.y / 14.4
        structures.append(Structure(Molecule(atoms, bonds), box,
                                    charge=sum(a.charge for a in atoms)))
    # Each label goes to the nearest structure above it (or beside it); a structure
    # takes only one label, the nearest.
    pairs = []
    for li, lab in enumerate(labels):
        for si, s in enumerate(structures):
            pairs.append((_label_distance(lab.box, s.box), li, si))
    taken_l, taken_s = set(), set()
    for dist, li, si in sorted(pairs):
        if li in taken_l or si in taken_s or dist > 160:
            continue
        if len(structures[si].molecule.atoms) <= 1 and structures[si].charge:
            continue          # (a counter-ion is not what a label names)
        structures[si].label = labels[li].text
        taken_l.add(li)
        taken_s.add(si)
    return structures, labels


def _label_distance(label: tuple, box: tuple) -> float:
    """How far a label is from a structure (pt): a label under the structure is nearest;
    one beside it a little further; above it further still."""
    lx = (label[0] + label[2]) / 2
    ly = (label[1] + label[3]) / 2
    dx = max(box[0] - lx, 0.0, lx - box[2])
    if ly >= box[3]:                        # below
        dy = ly - box[3]
        return dy + 2 * dx
    if ly <= box[1]:                        # above
        return 2 * (box[1] - ly) + 2 * dx + 20
    return 1.5 * dx + 10                    # beside


PLAIN_EXTRAS = {OBJ_TEXT, 0x8007, 0x8008, 0x8021, 0x800D, 0x800E}   # text, graphic,
                                                   # curve, arrow, reaction scheme/step


def is_plain(data: bytes) -> bool:
    """Whether a drawing is only structures: no text of its own (labels, conditions),
    no arrows or other graphics. (Its picture can then be a compound's structure
    picture as it is.)"""
    try:
        root = _parse(data)
    except CdxError:
        return False

    def extras(obj: _Obj) -> bool:
        for child in obj.children:
            if child.tag in PLAIN_EXTRAS and not (child.tag == 0x800D and not child.children):
                return True
            if child.tag in (OBJ_DOCUMENT, OBJ_PAGE, OBJ_GROUP) and extras(child):
                return True
        return False
    return not extras(root)


def combine(a: Molecule, b: Molecule) -> Molecule:
    """Two structures as one (a salt's cation and its counter-ion)."""
    off = len(a.atoms)
    return Molecule(a.atoms + b.atoms,
                    a.bonds + [Bond(x.a + off, x.b + off, x.order, x.stereo) for x in b.bonds])


def to_rdkit(m: Molecule):
    """An RDKit molecule (stereo from the wedges and the drawing's coordinates), or None.
    The atoms of unfolded abbreviations (Bn, Ph, Boc…) are laid out anew around the
    atoms as drawn, which keep ChemDraw's places."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Geometry import Point2D
    mol = Chem.MolFromMolBlock(m.molblock(), sanitize=True, removeHs=False)
    if mol is None:
        return None
    if not all(a.placed for a in m.atoms):
        conf = mol.GetConformer()
        fixed = {i: Point2D(conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y)
                 for i, a in enumerate(m.atoms) if a.placed}
        try:
            rdDepictor.Compute2DCoords(mol, coordMap=fixed)
        except Exception:  # noqa: BLE001 — the drawing's own places are kept then
            pass
    try:
        mol = Chem.RemoveHs(mol)
    except Exception:  # noqa: BLE001
        pass
    return mol


def rdkit_mols(data: bytes) -> list:
    """The structures as RDKit molecules (see to_rdkit); those RDKit can't read are left
    out. Raises CdxError for a file that can't be read."""
    return [mol for mol in (to_rdkit(m) for m in molecules(data)) if mol is not None]
