"""Checks of pasted ChemDraw-like structure pictures that need the computer's fonts in Qt
(SPEC §13n.1): the self-check compares Windows' drawing of the metafile, atom labels
included, with Qt's drawing of the SVG. The tests run Qt "offscreen", which has no fonts,
so test_chemdraw_structures.py runs this module in a separate process on Qt's own Windows
platform, as the program itself runs (no window is shown). It prints its results as JSON.

    python -m tests.native_structure_checks
"""

import json


def main() -> dict:
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from si_assistant.core import drawing_scale as ds
    from si_assistant.ui import clipboard_image as ci
    from si_assistant.ui import spectrum_paste as sp
    from tests import structure_samples as ss
    out: dict = {"platform": app.platformName()}
    reference = ds.reference_from_molblock(ss.structure_molblock())
    for scale in (0.5, 1.0, 2.0):
        emf = ss.structure_emf(scale, labels=True)
        checked = sp.checked_svg(emf)
        picture = ci.picture_from_emf(emf, 1200)
        text = picture.data.decode("utf-8", "replace") if picture.ext == ".svg" else ""
        out[str(scale)] = {
            "passed": checked.svg is not None, "reason": checked.reason,
            "ext": picture.ext, "message": picture.message, "texts": text.count("<text"),
            "bond": ds.bond_length_pt(text, reference) if text else None}
    return out


if __name__ == "__main__":
    print(json.dumps(main()))
