"""Small test files for the SI import's structures (SPEC §13u.7): ChemDraw CDX drawings,
the OLE compound file Word keeps a ChemDraw object in, and a Word document holding one.
Made-up structures only."""

from __future__ import annotations

import io
import struct
import zipfile

# ---------------------------------------------------------------------- CDX


def _prop(tag: int, data: bytes) -> bytes:
    return struct.pack("<HH", tag, len(data)) + data


def _obj(tag: int, oid: int, body: bytes) -> bytes:
    return struct.pack("<HI", tag, oid) + body + b"\x00\x00"


def _point(x: float, y: float) -> bytes:
    return struct.pack("<ii", int(y * 65536), int(x * 65536))


def _rect(left: float, top: float, right: float, bottom: float) -> bytes:
    return struct.pack("<iiii", int(top * 65536), int(left * 65536), int(bottom * 65536),
                       int(right * 65536))


class Cdx:
    """A CDX drawing built piece by piece: fragments (atoms, bonds), labels."""

    def __init__(self):
        self.parts: list[bytes] = []
        self.next_id = 100

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def fragment(self, atoms, bonds, box, nickname=None) -> "Cdx":
        """atoms: [(element, x, y, charge)], bonds: [(i, j, order, display)] (0-based),
        box: (left, top, right, bottom) pt. `nickname`: (atom index, inner atoms, inner
        bonds, attach index) — that atom is an abbreviation holding a fragment."""
        ids = [self._id() for _ in atoms]
        body = _prop(0x0204, _rect(*box))
        for k, ((el, x, y, charge), oid) in enumerate(zip(atoms, ids)):
            props = _prop(0x0200, _point(x, y))
            if nickname and nickname[0] == k:
                inner_atoms, inner_bonds, attach = nickname[1:]
                props += _prop(0x0400, struct.pack("<h", 4))
                props += self._inner(inner_atoms, inner_bonds, attach)
                body += _obj(0x8004, oid, props)
                continue
            if el != 6:
                props += _prop(0x0402, struct.pack("<h", el))
            if charge:
                props += _prop(0x0421, struct.pack("<b", charge))
            body += _obj(0x8004, oid, props)
        for i, j, order, display in bonds:
            props = (_prop(0x0604, struct.pack("<I", ids[i])) +
                     _prop(0x0605, struct.pack("<I", ids[j])) +
                     _prop(0x0600, struct.pack("<H", {1: 1, 2: 2, 3: 4}[order])))
            if display:
                props += _prop(0x0601, struct.pack("<h", display))
            body += _obj(0x8005, self._id(), props)
        self.parts.append(_obj(0x8003, self._id(), body))
        return self

    def _inner(self, atoms, bonds, attach) -> bytes:
        ids = [self._id() for _ in atoms]
        ext = self._id()
        body = b""
        for (el, x, y, charge), oid in zip(atoms, ids):
            props = _prop(0x0200, _point(x, y))
            if el != 6:
                props += _prop(0x0402, struct.pack("<h", el))
            body += _obj(0x8004, oid, props)
        body += _obj(0x8004, ext, _prop(0x0400, struct.pack("<h", 12)))
        for i, j, order, _d in bonds + [(attach, -1, 1, 0)]:
            b = ids[j] if j >= 0 else ext
            props = (_prop(0x0604, struct.pack("<I", ids[i])) +
                     _prop(0x0605, struct.pack("<I", b)) +
                     _prop(0x0600, struct.pack("<H", {1: 1, 2: 2, 3: 4}[order])))
            body += _obj(0x8005, self._id(), props)
        return _obj(0x8003, self._id(), body)

    def label(self, text: str, box) -> "Cdx":
        data = struct.pack("<H", 0) + text.encode("cp1252")
        self.parts.append(_obj(0x8006, self._id(), _prop(0x0204, _rect(*box)) + _prop(0x0700, data)))
        return self

    def arrow(self) -> "Cdx":
        self.parts.append(_obj(0x8021, self._id(), b""))
        return self

    def bytes(self) -> bytes:
        page = _obj(0x8001, 2, b"".join(self.parts))
        return b"VjCD0100" + b"\x04\x03\x02\x01" + b"\x00" * 10 + _obj(0x8000, 1, page)


def phenol(x: float = 0.0, y: float = 0.0) -> tuple[list, list]:
    """Atoms and bonds of phenol (a ring of 6 C, an O) placed at (x, y), 14.4 pt bonds."""
    import math
    atoms = [(6, x + 14.4 * math.cos(math.radians(30 + 60 * k)),
              y + 14.4 * math.sin(math.radians(30 + 60 * k)), 0) for k in range(6)]
    atoms.append((8, x + 28.8 * math.cos(math.radians(30)), y + 28.8 * math.sin(math.radians(30)), 0))
    bonds = [(k, (k + 1) % 6, 2 if k % 2 == 0 else 1, 0) for k in range(6)] + [(0, 6, 1, 0)]
    return atoms, bonds


# ---------------------------------------------------------------------- OLE compound file

def ole_file(streams: dict[str, bytes], mini_cutoff: int = 4096) -> bytes:
    """A small OLE compound file ([MS-CFB], 512-byte sectors) holding `streams` in its
    root storage: streams under `mini_cutoff` bytes in the mini stream, as Word writes."""
    SECT = 512
    FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    names = list(streams)
    mini_data = b""
    mini_starts: dict[str, int] = {}
    for n in names:
        if len(streams[n]) < mini_cutoff:
            mini_starts[n] = len(mini_data) // 64
            d = streams[n]
            mini_data += d + b"\x00" * ((-len(d)) % 64)
    big = [n for n in names if len(streams[n]) >= mini_cutoff]
    # sector plan: 0 FAT, 1.. directory, then miniFAT, ministream, big streams
    n_dir = (len(names) + 1 + 3) // 4
    sectors: list[bytes] = []
    fat: list[int] = []

    def add_chain(data: bytes) -> int:
        start = 1 + len(sectors)       # (sector 0 is the FAT)
        chunks = [data[i:i + SECT] for i in range(0, max(len(data), 1), SECT)] or [b""]
        for k, ch in enumerate(chunks):
            sectors.append(ch + b"\x00" * (SECT - len(ch)))
            fat_index = start + k
            while len(fat) <= fat_index:
                fat.append(FREE)
            fat[fat_index] = fat_index + 1 if k + 1 < len(chunks) else END
        return start

    # directory (filled in once the starts are known)
    dir_start = 1
    for _ in range(n_dir):
        sectors.append(b"")
    for k in range(n_dir):
        while len(fat) <= 1 + k:
            fat.append(FREE)
        fat[1 + k] = 2 + k if k + 1 < n_dir else END
    minifat_entries = []
    for n in names:
        if n in mini_starts:
            count = (len(streams[n]) + 63) // 64 or 1
            s = mini_starts[n]
            for k in range(count):
                while len(minifat_entries) <= s + k:
                    minifat_entries.append(FREE)
                minifat_entries[s + k] = s + k + 1 if k + 1 < count else END
    minifat_start = add_chain(struct.pack(f"<{len(minifat_entries)}I", *minifat_entries)) \
        if minifat_entries else END
    ministream_start = add_chain(mini_data) if mini_data else END
    big_starts = {n: add_chain(streams[n]) for n in big}
    while len(fat) < 1:
        fat.append(FREE)
    fat[0] = FATSECT

    def entry(name: str, kind: int, left: int, right: int, child: int, start: int, size: int) -> bytes:
        raw = name.encode("utf-16-le") + b"\x00\x00"
        return (raw + b"\x00" * (64 - len(raw)) + struct.pack("<HBB", len(raw), kind, 1) +
                struct.pack("<III", left, right, child) + b"\x00" * 16 + b"\x00" * 4 +
                b"\x00" * 16 + struct.pack("<I", start) + struct.pack("<Q", size))
    NONE = 0xFFFFFFFF
    entries = [entry("Root Entry", 5, NONE, NONE, 1 if names else NONE, ministream_start,
                     len(mini_data))]
    for k, n in enumerate(names):
        start = mini_starts[n] if n in mini_starts else big_starts[n]
        entries.append(entry(n, 2, NONE, 2 + k if k + 1 < len(names) else NONE, NONE, start,
                             len(streams[n])))
    raw_dir = b"".join(entries)
    raw_dir += b"\x00" * (n_dir * SECT - len(raw_dir))
    for k in range(n_dir):
        sectors[k] = raw_dir[k * SECT:(k + 1) * SECT]
    fat += [FREE] * (128 - len(fat))
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 0x20, 6)
    struct.pack_into("<IIIIIIII", header, 0x2C, 1, dir_start, 0, mini_cutoff,
                     minifat_start, len(minifat_entries) and 1, END, 0)
    difat = [0] + [FREE] * 108
    struct.pack_into("<109I", header, 0x4C, *difat)
    return bytes(header) + struct.pack("<128I", *fat[:128]) + b"".join(sectors)


def chemdraw_ole(cdx: bytes) -> bytes:
    return ole_file({"\x01CompObj": b"\x01\x00\xfe\xff ChemDraw.Document.6.0\x00", "CONTENTS": cdx})


# ---------------------------------------------------------------------- EMF (a spectrum)

def emf(texts: list[tuple[float, float, int, str]]) -> bytes:
    """An EMF that writes `texts` — (x, y, escapement in tenths of a degree, text) — as
    EMR_EXTTEXTOUTW records, each with a font of that escapement (as Mnova's spectrum
    pictures do: axis numbers flat, peak labels turned by 90° a character at a time)."""
    recs = [struct.pack("<II", 1, 88) + b"\x00" * 32 + b" EMF" + b"\x00" * 44]
    fonts: dict[int, int] = {}
    for x, y, esc, text in texts:
        if esc not in fonts:
            handle = len(fonts) + 1
            fonts[esc] = handle
            logfont = struct.pack("<iiiii", -12, 0, esc, esc, 400) + b"\x00" * 8 + \
                "Arial".encode("utf-16-le").ljust(64, b"\x00")
            recs.append(struct.pack("<III", 82, 12 + len(logfont), handle) + logfont)
        recs.append(struct.pack("<III", 37, 12, fonts[esc]))
        raw = text.encode("utf-16-le")
        raw += b"\x00" * ((-len(raw)) % 4)
        body = (b"\x00" * 16 + struct.pack("<iff", 1, 1.0, 1.0) +
                struct.pack("<iiIIi", int(x), int(y), len(text), 76, 0) + b"\x00" * 16 +
                struct.pack("<I", 0))
        recs.append(struct.pack("<II", 84, 8 + len(body) + len(raw)) + body + raw)
    recs.append(struct.pack("<IIIII", 14, 20, 0, 0, 20))
    return b"".join(recs)


def spectrum_emf(axis: list[str], peaks: list[str], title: str = "") -> bytes:
    """A spectrum's picture: an axis (numbers, then "ppm") and peak labels turned by 90°
    (one character at a time, read upwards), and an optional title."""
    texts = [(100 + 60 * k, 900, 0, v) for k, v in enumerate(axis)]
    texts.append((100 + 60 * len(axis), 900, 0, "ppm"))
    for k, label in enumerate(peaks):
        for i, ch in enumerate(label):
            texts.append((120 + 40 * k, 200 - 10 * i, 900, ch))
    if title:
        texts.append((300, 50, 0, title))
    return emf(texts)


def header_emf(cid: str, title: str) -> bytes:
    """A spectrum page's header picture: some atom labels, the compound's ID, a title."""
    return emf([(10, 10, 0, "O"), (30, 40, 0, "Me"), (20, 80, 0, cid), (100, 20, 0, title)])


# ---------------------------------------------------------------------- Word

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
OTHER_NS = ('xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:o="urn:schemas-microsoft-com:office:office" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def docx_with_objects(paragraphs: list[tuple[str, int | list | None]], objects: list) -> bytes:
    """A Word document: paragraphs of (WordprocessingML runs, the object anchored in it:
    an index into `objects`, a list of indexes for a group of objects, or None).
    `objects` are ChemDraw drawings (CDX bytes) or ("mnova", picture EMF bytes)."""
    rels = []
    body = []
    media = {}
    for text, obj in paragraphs:
        extra = ""
        if obj is not None:
            members = obj if isinstance(obj, list) else [obj]
            shapes, oles = [], []
            for k in members:
                is_mnova = isinstance(objects[k], tuple)
                rid = f"rIdO{k}"
                img = ""
                if is_mnova:
                    media[k] = objects[k][1]
                    img = f'<v:imagedata r:id="rIdI{k}"/>'
                    rels.append(f'<Relationship Id="rIdI{k}" Type="http://schemas.openxmlformats.'
                                f'org/officeDocument/2006/relationships/image" '
                                f'Target="media/image{k + 1}.emf"/>')
                shapes.append(f'<v:shape id="s{k}" style="position:absolute">{img}</v:shape>')
                prog = "MestReNova.Document.1" if is_mnova else "ChemDraw.Document.6.0"
                oles.append(f'<o:OLEObject Type="Embed" ProgID="{prog}" ShapeID="s{k}" '
                            f'r:id="{rid}"/>')
                rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                            f'officeDocument/2006/relationships/oleObject" '
                            f'Target="embeddings/oleObject{k + 1}.bin"/>')
            inner = "".join(shapes)
            if len(members) > 1:
                inner = f"<v:group>{inner}</v:group>"
            extra = f'<w:r><w:object>{inner}{"".join(oles)}</w:object></w:r>'
        body.append(f"<w:p>{extra}{text}</w:p>")
    doc = f'<w:document {W_NS} {OTHER_NS}><w:body>{"".join(body)}</w:body></w:document>'
    rels_xml = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                f'relationships">{"".join(rels)}</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels_xml)
        for k, obj in enumerate(objects):
            if isinstance(obj, tuple):            # (an Mnova object: its data isn't read)
                z.writestr(f"word/embeddings/oleObject{k + 1}.bin",
                           ole_file({"MNOVA-CONTENTS": b"mnova"}))
            else:
                z.writestr(f"word/embeddings/oleObject{k + 1}.bin", chemdraw_ole(obj))
        for k, picture in media.items():
            z.writestr(f"word/media/image{k + 1}.emf", picture)
    return buf.getvalue()
