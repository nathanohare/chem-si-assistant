"""Running Typst to make the PDF."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import typst

from ..core.project import Project
from .document import CompileOptions, build


class CompileError(Exception):
    """Typst could not make the PDF. The message is shown to the user."""


@dataclass
class CompileResult:
    pdf: Path
    typ: Path
    warnings: list[str] = field(default_factory=list)


def run_typst(typ: Path, output: Path, root: Path) -> list[str]:
    """Typst alone: `typ` → PDF `output`, with `root` as Typst's root folder. Returns
    warnings only Typst can know (laid-out sizes: scheme_notes).

    It touches no Qt objects and no project data, so it can run in a worker thread (the
    live preview, SPEC §13k.8); the typst package releases Python's lock while it works,
    so the window stays responsive. Raises CompileError."""
    try:
        compiler = typst.Compiler(str(typ), root=str(root))
        compiler.compile(output=str(output))
    except Exception as e:  # noqa: BLE001 — Typst reports problems as plain exceptions
        raise CompileError(
            "The SI could not be compiled. This is usually caused by an unusual file or "
            f"character; details for troubleshooting:\n\n{e}") from e
    return scheme_notes(compiler)


SCHEME_TEXT_LABEL = "si-scheme-text"


def scheme_notes(compiler) -> list[str]:
    """Schemes made with the scheme editor whose conditions print smaller than
    MIN_TEXT_PT (shrunk to fit the page, or a small scale): one warning each. The size
    is recorded by the style's si-reaction-scheme after it is laid out (asked of the
    document just compiled: no second layout)."""
    from ..core.scheme_editor import MIN_TEXT_PT
    try:
        found = compiler.query(f"<{SCHEME_TEXT_LABEL}>", field="value")
        found = json.loads(found) if isinstance(found, (str, bytes)) else found
    except Exception:  # noqa: BLE001 — no schemes, or an older style: nothing to say
        return []
    out = []
    for item in found or []:
        try:
            size, name = float(item["size"]), str(item.get("key", ""))
        except (TypeError, KeyError, ValueError):
            continue
        if size < MIN_TEXT_PT - 0.05:
            out.append(f"Scheme “{name}”: its conditions print at only {size:.1f} pt (the "
                       "scheme was shrunk to fit the page). Use Layout… on the scheme page: "
                       "“Continue on a new row”, a smaller scheme size or structures, or "
                       "shorter conditions.")
    return out


def last_page_of(typ: Path, root: Path, label: str) -> int | None:
    """The page number (from 1) recorded at `label` by `page_marker(label)` in the source,
    or None if Typst can't tell. Asks Typst (a second, quick layout of the source)."""
    try:
        value = typst.query(str(typ), f"<{label}>", field="value", one=True, root=str(root))
        page = int(json.loads(value) if isinstance(value, str) else value)
    except Exception:  # noqa: BLE001 — not found or unreadable: keep every page
        return None
    return page if page >= 1 else None


def keep_pages_until(typ: Path, pdf: Path, root: Path, label: str) -> None:
    """Cut `pdf` (compiled from `typ`) down to its pages up to the one holding `label`
    (the live preview of the table of contents, which is laid out with the whole SI so its
    page numbers are right). Nothing changes if the label can't be found. Safe in a worker
    thread (no Qt, no project data)."""
    last = last_page_of(typ, root, label)
    if last is None:
        return
    from pypdf import PdfReader, PdfWriter
    tmp = pdf.with_name(pdf.name + ".part")
    try:
        reader = PdfReader(str(pdf))   # (read into memory: the file is not kept open)
        if len(reader.pages) <= last:
            return
        writer = PdfWriter()
        for page in reader.pages[:last]:
            writer.add_page(page)
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, pdf)
    except Exception:  # noqa: BLE001 — the whole PDF is shown instead
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def mark_producer(pdf: Path) -> bool:
    """Name this program as the PDF's creator (SPEC §13l.5): Creator "Chem SI Assistant
    0.1.0" and Producer "Chem SI Assistant 0.1.0 (Typst 0.15.0)", in the document
    information and in its XMP metadata (Typst itself writes only "Typst 0.15.0" as the
    Creator).

    Written as an *incremental update*: the new document information and XMP objects, a
    small cross-reference section and trailer are added after the end of the file, as PDF
    allows; everything Typst wrote (pages, links, bookmarks, named destinations, the tagged
    structure) stays byte for byte as it was, and it takes a few milliseconds whatever the
    size (rewriting the whole file with pypdf took 0.7 s for 140 pages). A file whose
    cross-references are a stream (not what Typst 0.15 writes) is updated by pypdf instead.
    If anything goes wrong, the PDF is left as Typst wrote it (returns False)."""
    from io import BytesIO
    from pypdf import PdfReader
    from .. import APP_NAME, __version__
    tmp = pdf.with_name(pdf.name + ".meta")
    try:
        data = pdf.read_bytes()
        reader = PdfReader(BytesIO(data))   # (reads the cross-references, not every object)
        engine = str((reader.metadata or {}).get("/Creator") or "")
        if not engine.startswith("Typst"):
            engine = f"Typst {getattr(typst, '__version__', '')}".strip()
        creator = f"{APP_NAME} {__version__}"
        producer = f"{creator} ({engine})"
        at = data.rfind(b"startxref")
        prev = int(data[at + len(b"startxref"):].split()[0])
        if data[prev:prev + 4] == b"xref":
            new = _append_names(data, reader, prev, creator, producer)
        else:
            new = _pypdf_names(reader, creator, producer)
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, pdf)
        return True
    except Exception:  # noqa: BLE001 — the PDF itself is fine without it
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _named_xmp(xmp: bytes, creator: str, producer: str) -> bytes:
    """The XMP metadata with this program as CreatorTool and Producer."""
    import re
    from xml.sax.saxutils import escape
    text = xmp.decode("utf-8")
    tool = f"<xmp:CreatorTool>{escape(creator)}</xmp:CreatorTool>"
    text, n = re.subn(r"<xmp:CreatorTool>.*?</xmp:CreatorTool>", tool, text, count=1, flags=re.S)
    made = f"<pdf:Producer>{escape(producer)}</pdf:Producer>"
    text, m = re.subn(r"<pdf:Producer>.*?</pdf:Producer>", made, text, count=1, flags=re.S)
    if not n or not m:   # (add what is missing to the description)
        text = text.replace("</rdf:Description>", ("" if n else tool) + ("" if m else made)
                            + "</rdf:Description>", 1)
    return text.encode("utf-8")


def _append_names(data: bytes, reader, prev: int, creator: str, producer: str) -> bytes:
    """`data` (a PDF with a classic cross-reference table at `prev`) with an incremental
    update holding the new document information and XMP metadata."""
    from io import BytesIO
    from pypdf.generic import (ArrayObject, DictionaryObject, IndirectObject, NameObject,
                               NumberObject, StreamObject, TextStringObject)
    trailer = reader.trailer
    size = int(trailer["/Size"])
    objects: dict[tuple[int, int], object] = {}
    info = DictionaryObject()
    old_info = trailer.raw_get("/Info") if "/Info" in trailer else None
    for key, value in (trailer["/Info"].items() if old_info is not None else ()):
        info[NameObject(key)] = value
    info[NameObject("/Creator")] = TextStringObject(creator)
    info[NameObject("/Producer")] = TextStringObject(producer)
    if isinstance(old_info, IndirectObject):
        info_ref = (old_info.idnum, old_info.generation)
    else:
        info_ref, size = (size, 0), size + 1
    objects[info_ref] = info
    meta = trailer["/Root"].raw_get("/Metadata") if "/Metadata" in trailer["/Root"] else None
    if isinstance(meta, IndirectObject):
        old = meta.get_object()
        stream = StreamObject()
        for key, value in old.items():
            if key not in ("/Length", "/Filter", "/DecodeParms"):
                stream[NameObject(key)] = value
        stream.set_data(_named_xmp(old.get_data(), creator, producer))
        objects[(meta.idnum, meta.generation)] = stream
    out = BytesIO()
    out.write(data)
    if not data.endswith(b"\n"):
        out.write(b"\n")
    offsets = {}
    for (num, gen), obj in sorted(objects.items()):
        offsets[(num, gen)] = out.tell()
        out.write(f"{num} {gen} obj\n".encode("ascii"))
        obj.write_to_stream(out)
        out.write(b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n")
    for (num, gen), offset in sorted(offsets.items()):   # (each entry is 20 bytes)
        out.write(f"{num} 1\n{offset:010d} {gen:05d} n\r\n".encode("ascii"))
    new_trailer = DictionaryObject({
        NameObject("/Size"): NumberObject(size),
        NameObject("/Root"): trailer.raw_get("/Root"),
        NameObject("/Info"): IndirectObject(info_ref[0], info_ref[1], reader),
        NameObject("/Prev"): NumberObject(prev)})
    if "/ID" in trailer:
        new_trailer[NameObject("/ID")] = ArrayObject(trailer.raw_get("/ID"))
    out.write(b"trailer\n")
    new_trailer.write_to_stream(out)
    out.write(f"\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii"))
    return out.getvalue()


def _pypdf_names(reader, creator: str, producer: str) -> bytes:
    """The same done by pypdf (an incremental update too), for other kinds of PDF."""
    from io import BytesIO
    from pypdf import PdfWriter
    writer = PdfWriter(reader, incremental=True)
    writer.add_metadata({"/Creator": creator, "/Producer": producer})
    ref = writer.root_object.get("/Metadata")
    if ref is not None:
        stream = ref.get_object()
        stream.set_data(_named_xmp(stream.get_data(), creator, producer))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def compile_pdf(project: Project, options: CompileOptions, image_info, converter=None,
                name: str = "SI", crop=None) -> CompileResult:
    """Build build/<name>.typ and compile it to build/<name>.pdf.

    The PDF is written to a temporary file first and then renamed, so a viewer that has
    the old SI.pdf open can't leave a half-written file. If the old file is locked
    (e.g. open in Acrobat on Windows) the new PDF gets a numbered name instead.
    """
    result = build(project, options, image_info, converter, name=name, crop=crop)
    tmp = result.typ_path.with_name(f".{name}.partial.pdf")
    result.warnings += run_typst(result.typ_path, tmp, project.folder)
    mark_producer(tmp)
    final = result.typ_path.with_suffix(".pdf")
    try:
        os.replace(tmp, final)
    except PermissionError:
        n = 2
        while True:
            alt = final.with_name(f"{name} ({n}).pdf")
            try:
                os.replace(tmp, alt)
                final = alt
                result.warnings.append(f"{name}.pdf is open in another program, so the new PDF "
                                       f"was saved as {alt.name}.")
                break
            except PermissionError:
                n += 1
                if n > 50:
                    raise CompileError(f"Could not save {name}.pdf: the file is locked.") from None
    return CompileResult(final, result.typ_path, result.warnings)
