"""Checks of pasted spectra that need the computer's fonts in Qt (SPEC §13l.1).

The self-check compares Windows' drawing of a metafile, text included, with Qt's drawing of
the SVG made from it. The tests run Qt "offscreen", which has no fonts, so
test_spectrum_paste.py runs this module in a separate process on Qt's own Windows platform,
as the program itself runs (no window is shown). It prints its results as JSON.

    python -m tests.native_spectrum_checks
"""

import json
import re
import time


def main() -> dict:
    from PySide6.QtGui import QFont, QRawFont
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from si_assistant.core import emf_svg
    from si_assistant.ui import spectrum_paste as sp
    from tests import emf_samples as es
    from tests import test_emf_svg as T
    out: dict = {"platform": app.platformName()}

    # A spectrum like Mnova's vector copy: 65,536-point trace, 300 labels.
    data = es.spectrum_emf()
    start = time.perf_counter()
    conv = emf_svg.convert(data, sp.QtTextHelper())
    converted = time.perf_counter() - start
    check = sp.self_check(data, conv.svg)
    start = time.perf_counter()
    result = sp.convert_emf(data)
    total = time.perf_counter() - start
    out["spectrum"] = {"kind": result.kind, "message": result.message, "bytes": len(result.data),
                       "convert_s": converted, "check_s": check.seconds, "total_s": total,
                       "passed": check.passed, "coverage": check.comparison.coverage,
                       "worst_tile": check.comparison.worst_tile,
                       "texts": conv.texts, "label": "Normalized Intensity" in conv.svg}

    for name in ("shapes_emf", "himetric_emf", "lines_emf"):
        sample = getattr(es, name)()
        c = emf_svg.convert(sample, sp.QtTextHelper())
        chk = sp.self_check(sample, c.svg)
        out[name] = {"ok": c.ok, "passed": chk.passed, "coverage": chk.comparison.coverage,
                     "worst_tile": chk.comparison.worst_tile, "reason": chk.reason}

    # The same drawing as if made on other screens (the metafile's reference device:
    # pixels and millimetres in its header). Windows draws hatches and the dashes of
    # one-pixel pens in its own output pixels whatever the screen, so the check must pass
    # for every one, and the stored SVG's hatches and dashes must not change.
    import struct
    shapes = es.shapes_emf()
    out["other screens"] = {}
    for name, px, mm in (("1366x768 laptop", (1366, 768), (309, 174)),
                         ("1920x1080 24in", (1920, 1080), (531, 299)),
                         ("2560x1600 13in", (2560, 1600), (286, 179)),
                         ("3840x2160 15.6in", (3840, 2160), (344, 194))):
        data = bytearray(shapes)
        struct.pack_into("<2i", data, 72, *px)
        struct.pack_into("<2i", data, 80, *mm)
        struct.pack_into("<2i", data, 100, mm[0] * 1000, mm[1] * 1000)
        c = emf_svg.convert(bytes(data), sp.QtTextHelper())
        chk = sp.self_check(bytes(data), c.svg)
        out["other screens"][name] = {
            "passed": chk.passed, "reason": chk.reason,
            "patterns": re.findall(r"<pattern .*?</pattern>", c.svg),
            "dashes": re.findall(r'class="cosmetic-dash" stroke-dasharray="[^"]+"', c.svg)}

    # Deliberately broken conversions must fail.
    small = es.spectrum_emf(4000, 30)
    svg = emf_svg.convert(small, sp.QtTextHelper()).svg
    shift = re.sub(r'(<text[^>]*transform="matrix\(\S+ \S+ \S+ \S+ )([-\d.]+)',
                   lambda m: m.group(1) + str(float(m.group(2)) + 100), svg)   # 1 mm
    variants = {
        "good": svg,
        "a label left out": "\n".join(l for l in svg.splitlines() if "Normalized" not in l),
        "text moved by 1 mm": shift,
        "1 % too large": svg.replace("<g stroke-linecap",
                                     '<g transform="scale(1.01)" stroke-linecap', 1),
        "picture left out": "\n".join(l for l in svg.splitlines() if "<image" not in l),
        "curves left out": "\n".join(l for l in svg.splitlines() if "#008c00" not in l),
    }
    out["variants"] = {k: sp.self_check(small, v).passed for k, v in variants.items()}
    shapes = es.shapes_emf()
    swapped = re.sub(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", r"#\3\2\1",
                     emf_svg.convert(shapes, sp.QtTextHelper()).svg)
    out["colours swapped"] = sp.self_check(shapes, swapped).passed

    # Text written as glyph numbers is read back as text (and as outlines if asked).
    font = QFont("Arial")
    font.setPixelSize(1000)
    glyphs = list(QRawFont.fromFont(font).glyphIndexesForString("7.26 ppm"))
    record = T.text_out(0, 0, "", options=emf_svg.ETO_GLYPH_INDEX, glyphs=glyphs)
    c = emf_svg.convert(T.emf(T.font(1, -20, "Arial"), T.select(1),
                              T.set_attr(18, emf_svg.TRANSPARENT), record), sp.QtTextHelper())
    outline = sp.QtTextHelper().glyph_outline(emf_svg.Font(face="Arial"), glyphs[:1], [0.0], 20)
    out["glyphs"] = {"ok": c.ok, "text": ">7.26 ppm</text>" in c.svg,
                     "outline": bool(outline) and outline.startswith("M")}
    return out


if __name__ == "__main__":
    print(json.dumps(main()))
