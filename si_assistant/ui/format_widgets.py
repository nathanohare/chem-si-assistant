"""Number boxes that may be left at "Default" (formatting sizes and spacing): used by the
table editor, Project settings ▸ Text and tables, figure and scheme caption sizes.

A box's lowest value stands for "not set" and shows a word (e.g. "Default"); `value_of`
gives None for it, so nothing is stored and the default applies."""

from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox

from ..core.formatting import SIZE_RANGE


class OptionalSpinBox(QDoubleSpinBox):
    """Stepping up from "not set" goes to `first` (e.g. the style's own 10 pt), not to the
    lowest number."""

    first: float | None = None

    def stepBy(self, steps: int) -> None:  # noqa: N802
        if steps > 0 and self.value() <= self.minimum() and self.first is not None:
            self.setValue(self.first)
            return
        super().stepBy(steps)


def optional_box(lo: float, hi: float, step: float, decimals: int, suffix: str,
                 unset_text: str, tip: str = "", first: float | None = None
                 ) -> QDoubleSpinBox:
    """A box for `lo`…`hi`, with one step below `lo` meaning "not set" (`unset_text`).
    Stepping up from "not set" goes to `first` (if given)."""
    box = OptionalSpinBox()
    box.first = first
    box.setRange(round(lo - step, decimals), hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setSpecialValueText(unset_text)
    box.setAccelerated(True)
    if tip:
        box.setToolTip(tip)
    box.setValue(box.minimum())
    return box


def size_box(unset_text: str = "Default", tip: str = "") -> QDoubleSpinBox:
    """A text size in pt (4–30), or `unset_text`; stepping up from it starts at 10 pt."""
    return optional_box(SIZE_RANGE[0], SIZE_RANGE[1], 0.5, 1, " pt", unset_text, tip, 10.0)


def value_of(box: QDoubleSpinBox) -> float | None:
    """The number in the box, or None when it shows its "not set" word."""
    value = box.value()
    return None if value <= box.minimum() else round(value, box.decimals())


def set_value(box: QDoubleSpinBox, value: float | None) -> None:
    """Show `value` (None: the "not set" word) without telling anyone it changed."""
    box.blockSignals(True)
    box.setValue(box.minimum() if value is None else value)
    box.blockSignals(False)
