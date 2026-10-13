"""Structure pictures cut out of a PDF SI (SPEC §13u.7): a PDF holds no structure data,
only the drawing, so each compound gets the drawing of its structure as a vector picture
(a one-page PDF, trimmed to its ink), which the user can keep or leave out.

Two layouts are recognised:
* the structure on its own between the compound's heading and its text (the gap between
  them, across the text width);
* the structure in a scheme, with the compound's ID written under it ("5b"): the drawing
  above the label, between the labels beside it, up to the body text above.

Needs Qt (QtPdf) to find the ink; without it no pictures are made.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

MIN_GAP = 24.0          # pt between a heading and its text that can hold a drawing
MAX_TEXT_CHARS = 40     # more text than this in a cut-out: it is not a drawing


def _spans(page) -> list:
    from . import ms_read
    frames, _plain, _slanted = ms_read.pdf_spans(page)
    return frames[0] if frames else []


def _margins(spans) -> tuple[float, float]:
    xs0 = sorted(s.x0 for s in spans)
    xs1 = sorted(s.x1 for s in spans)
    if not xs0:
        return 72.0, 540.0
    return xs0[int(0.05 * (len(xs0) - 1))], xs1[int(0.95 * (len(xs1) - 1))]


def _text_inside(spans, box, skip: str = "") -> int:
    x0, y0, x1, y1 = box
    return sum(len(s.text.strip()) for s in spans
               if s.x0 >= x0 - 1 and s.x1 <= x1 + 1 and s.y0 >= y0 - 1 and s.y1 <= y1 + 1
               and s.text.strip().rstrip(".") != skip)


def _crop(reader, page_no: int, box) -> bytes:
    """One page cut down to `box` (left, top, right, bottom; pt, y down) as PDF bytes."""
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject
    page = reader.pages[page_no - 1]
    mb = page.mediabox
    top = float(mb.top)
    rect = RectangleObject((box[0], top - box[3], box[2], top - box[1]))
    writer = PdfWriter()
    writer.add_page(page)
    out = writer.pages[0]
    out.mediabox = rect
    out.cropbox = rect
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _ink_fraction_box(pdf: bytes):
    """Where the ink is in a one-page PDF, as fractions (left, top, right, bottom); None
    if it is blank (or Qt can't draw it)."""
    import numpy as np
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtPdf import QPdfDocument
    from .spectra import content_box_of
    data = QByteArray(pdf)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument()
    doc.load(buffer)
    try:
        if doc.pageCount() < 1:
            return None
        size = doc.pagePointSize(0)
        scale = 150 / 72.0
        image = doc.render(0, QSize(max(1, int(size.width() * scale)),
                                    max(1, int(size.height() * scale))))
    finally:
        doc.close()
    if image.isNull():
        return None
    from PySide6.QtGui import QPainter
    white = QImage(image.size(), QImage.Format.Format_RGB32)   # (Qt draws on transparent)
    white.fill(0xFFFFFF)
    painter = QPainter(white)
    painter.drawImage(0, 0, image)
    painter.end()
    gray = white.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h, stride = gray.width(), gray.height(), gray.bytesPerLine()
    arr = np.frombuffer(gray.constBits(), np.uint8, count=stride * h).reshape(h, stride)[:, :w]
    arr = arr.copy()
    # (a cut drawn by Qt can show a faint line along its edges: not ink)
    edge = 3
    arr[:edge, :] = arr[-edge:, :] = 255
    arr[:, :edge] = arr[:, -edge:] = 255
    if not (arr < 200).any():
        return None
    return content_box_of(arr, white=200) or (0.0, 0.0, 1.0, 1.0)


def _trimmed(reader, page_no: int, box) -> bytes | None:
    """The drawing in `box` trimmed to its ink, as PDF bytes; None if `box` is blank."""
    first = _crop(reader, page_no, box)
    frac = _ink_fraction_box(first)
    if frac is None:
        return None
    w, h = box[2] - box[0], box[3] - box[1]
    tight = (box[0] + frac[0] * w, box[1] + frac[1] * h, box[0] + frac[2] * w, box[1] + frac[3] * h)
    if tight[2] - tight[0] < 8 or tight[3] - tight[1] < 8:
        return None
    return _crop(reader, page_no, tight)


def _gap_region(spans, heading_bottom: float, text_top: float):
    left, right = _margins(spans)
    if text_top - heading_bottom < MIN_GAP:
        return None
    return (left - 2, heading_bottom + 1, right + 2, text_top - 1)


def _label_region(spans, cid: str, id_pattern: str):
    """The drawing above the compound's label in a scheme (see the module's text)."""
    left, right = _margins(spans)
    width = right - left
    labels = [s for s in spans if re.fullmatch(id_pattern, s.text.strip().rstrip("."))]
    mine = [s for s in labels if s.text.strip().rstrip(".") == cid]
    for lab in mine:
        # a label, not the ID in running text: nothing else on its line but labels
        line = [s for s in spans if abs((s.y0 + s.y1) / 2 - (lab.y0 + lab.y1) / 2) < 3]
        if any(s not in labels and len(s.text.strip()) > 2 for s in line):
            continue
        row = sorted((s for s in labels if abs(s.y0 - lab.y0) < 4), key=lambda s: s.x0)
        i = row.index(lab)
        cx = (lab.x0 + lab.x1) / 2
        x0 = (((row[i - 1].x0 + row[i - 1].x1) / 2 + cx) / 2) if i > 0 else left - 2
        x1 = ((cx + (row[i + 1].x0 + row[i + 1].x1) / 2) / 2) if i + 1 < len(row) else right + 2
        # up to the nearest line of body text above (a line across most of the width)
        above = [s for s in spans if s.y1 < lab.y0 - 1]
        lines: dict[int, list] = {}
        for s in above:
            lines.setdefault(round(s.y1), []).append(s)
        body = [y for y, ss in lines.items()
                if max(s.x1 for s in ss) - min(s.x0 for s in ss) > 0.6 * width]
        top = (max(body) + 2) if body else min((s.y0 for s in spans), default=36.0) - 2
        bottom = lab.y0 - 1
        if bottom - top < MIN_GAP:
            continue
        # Never cut through a text: one across an edge (a scheme's conditions) is left out.
        for s in spans:
            if s.y1 < top or s.y0 > bottom or not s.text.strip():
                continue
            if s.x0 < x0 < s.x1:
                x0 = s.x1 + 1
            if s.x0 < x1 < s.x1:
                x1 = s.x0 - 1
        if x1 - x0 < MIN_GAP:
            continue
        return (x0, top, x1, bottom)
    return None


def add_pictures(path: Path, plan, id_pattern: str, progress=None) -> int:
    """Cut each PDF compound's structure picture out of `path` (the SI read for `plan`),
    into its `picture_pdf`; a compound with a structure already is left alone. Returns
    how many were found."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")
    except Exception:  # noqa: BLE001 — no pictures then; the rest of the import stands
        return 0
    cache: dict[int, list] = {}

    def spans_of(n: int):
        if n not in cache:
            try:
                cache[n] = _spans(reader.pages[n - 1])
            except Exception:  # noqa: BLE001
                cache[n] = []
        return cache[n]
    found = 0
    for k, c in enumerate(plan.compounds):
        if progress is not None and progress(k, len(plan.compounds)) is False:
            break
        if c.structure or not getattr(c, "geometry", None):
            continue
        page, head_bottom, text_page, text_top = c.geometry
        if not page or page > len(reader.pages):
            continue
        try:
            picture, where = None, ""
            if text_page == page and text_top:
                box = _gap_region(spans_of(page), head_bottom, text_top)
                if box is not None and _text_inside(spans_of(page), box) <= MAX_TEXT_CHARS:
                    picture, where = _trimmed(reader, page, box), "between its heading and its text"
            if picture is None:
                for n in (page, page - 1):
                    if n < 1:
                        continue
                    box = _label_region(spans_of(n), c.cid, id_pattern)
                    if box is not None and _text_inside(spans_of(n), box, c.cid) <= MAX_TEXT_CHARS:
                        picture = _trimmed(reader, n, box)
                        where = f"above its label “{c.cid}” in a scheme"
                        page = n
                        if picture is not None:
                            break
        except Exception:  # noqa: BLE001 — one odd page must not stop the others
            picture = None
        if picture is not None:
            c.picture_pdf = picture
            c.structure_note = f"a picture cut out of the PDF (page {page}, {where})"
            found += 1
    return found


def _prose_inside(spans, box) -> bool:
    """Whether `box` holds a line of running text (60 characters or more): a figure's
    labels are shorter."""
    from .si_import_read import _lines
    x0, y0, x1, y1 = box
    inside = [s for s in spans if s.x0 >= x0 - 1 and s.x1 <= x1 + 1 and s.y0 >= y0 - 1
              and s.y1 <= y1 + 1]
    return any(len(L.plain()) >= 60 for L in _lines(inside))


def add_figures(path: Path, plan) -> int:
    """Cut the figures and tables of a PDF SI's sections (SPEC §13u.16) out of their pages:
    a figure from the text above it to its caption, a table from its caption to its last
    row, across the text width, trimmed to the ink, as a one-page PDF (`data`). Returns
    how many were cut out; a figure that can't be (nothing there) keeps no picture, and
    its caption is imported as text."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")
    except Exception:  # noqa: BLE001 — no figures then; the rest of the import stands
        return 0
    found = 0
    for s in getattr(plan, "sections", []) or []:
        for fig in s.figures.values():
            if fig.kind != "cut" or not fig.page or fig.page > len(reader.pages):
                continue
            try:
                page = reader.pages[fig.page - 1]
                height = float(page.mediabox.height)
                spans = _spans(page)
                left, right = _margins(spans)
                boxes = []
                if fig.is_table:
                    boxes.append((fig.top + 1, fig.bottom + 2))
                else:
                    boxes.append((fig.top if fig.top else 0.1 * height, fig.bottom - 1))
                    if fig.below and fig.below[1] >= 0:      # (or below its caption)
                        boxes.append((fig.below[0] + 1,
                                      fig.below[1] - 1 if fig.below[1] else 0.92 * height))
                data = None
                for k, (top, bottom) in enumerate(boxes):
                    box = (left - 4, top, right + 4, bottom)
                    if bottom - top < 20 or (k == 1 and _prose_inside(spans, box)):
                        continue        # (under a caption, lines of text are no figure)
                    data = _trimmed(reader, fig.page, box)
                    if data is not None:
                        fig.cut_below = k == 1
                        break
            except Exception:  # noqa: BLE001 — one odd page must not stop the others
                data = None
            if data is not None:
                fig.data, fig.ext = data, ".pdf"
                try:          # (printed at the width it had on the page)
                    fig.width_cm = round(float(PdfReader(io.BytesIO(data)).pages[0]
                                               .mediabox.width) / 72 * 2.54, 2)
                except Exception:  # noqa: BLE001
                    fig.width_cm = None
                found += 1
    return found
