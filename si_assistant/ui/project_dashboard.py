"""The project dashboard (SPEC §13k.3): shown when the project itself (the top item of
the tree) is selected. Progress overall and per Data section, missing required data by
field, warnings and flags by type, and a grid of every compound in every section.

The charts are drawn here with QPainter (no chart library: Qt Charts is GPL-only and not
needed for a few bars and rings).
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QTableWidgetItem, QToolTip,
                               QVBoxLayout, QWidget)

from ..core import checks
from ..core import dashboard as D
from ..core.project import Project, ProjectError
from .dashboard_view import KEY_ROLE, DashboardView, _item, blend, cell_colour
from .spectra_panel import ColourButton

# Categorical colours for the slices of a donut, in a fixed order (a validated
# colour-blind-safe set, with its own steps for a dark background). Past eight slices the
# smallest are folded into "Other".
CATEGORICAL_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
                     "#4a3aa7", "#e34948")
CATEGORICAL_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300",
                    "#9085e9", "#e66767")
OTHER_GREY = "#898781"
MAX_SLICES = 8
SECTION_NAME_WIDTH = 240   # px: longer section paths are shortened at the front ("…› Amides")


def plain_tip(text: str) -> str:
    """A tooltip that shows `text` exactly as typed: a name such as "ee < 90%" or
    "Ar<sub>F</sub>" is not read as HTML (Qt guesses whether a tooltip is HTML)."""
    return "<p style='white-space:pre'>" + html.escape(text) + "</p>"


def short_path(path: str, fm, width: int) -> str:
    """A section path that fits in `width` pixels: the outer sections are left out first
    ("… › Tertiary Amides › Cyclic Amides"), then the section's own name is cut at its end."""
    if fm.horizontalAdvance(path) <= width:
        return path
    parts = path.split(D.SECTION_SEPARATOR)
    for i in range(1, len(parts)):
        shorter = "…" + D.SECTION_SEPARATOR + D.SECTION_SEPARATOR.join(parts[i:])
        if fm.horizontalAdvance(shorter) <= width:
            return shorter
    last = ("…" + D.SECTION_SEPARATOR if len(parts) > 1 else "") + parts[-1]
    return fm.elidedText(last, Qt.TextElideMode.ElideRight, width)


def is_dark(widget: QWidget) -> bool:
    return widget.palette().window().color().lightness() < 128


def solid(colour: str, fallback: str = "#898781") -> QColor:
    """A status colour as a chart mark (a status with no colour gets grey)."""
    return QColor(colour or fallback)


@dataclass
class ChartItem:
    label: str
    value: int
    colour: QColor | None = None   # the bar's colour (donuts use the categorical colours)
    tip: str = ""


# ---------------------------------------------------------------------- charts

class Chart(QWidget):
    """Horizontal bars (largest first, value at the end of each bar) or a donut with a
    legend. Hovering over a bar, slice or legend row shows its value."""

    ROW = 22
    BAR = 12

    def __init__(self, empty_text: str = "Nothing to show", parent: QWidget | None = None):
        super().__init__(parent)
        self.items: list[ChartItem] = []
        self.mode = "bars"
        self.empty_text = empty_text
        self.centre_text = ""
        self.use_item_colours = False   # donut slices in the items' own colours
        self.keep_zero = False          # bars of 0 are drawn (a donut never has 0 slices)
        # For a chart whose items do not add up to a whole (missing data: one compound can
        # miss several fields), each value is shown against this count ("27 of 28
        # compounds") instead of as a share of all the items.
        self.denominator = 0
        self.unit = "compounds"
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._hits: list[tuple[QRectF, int]] = []   # (row, item index) for tooltips
        self._ring = None   # (centre, outer radius, inner radius, slices, total) as drawn

    def set_items(self, items: list[ChartItem], mode: str | None = None) -> None:
        self.items = [i for i in items if i.value > 0 or (self.keep_zero and i.value == 0)]
        if not any(i.value for i in self.items):
            self.items = []
        if mode is not None:
            self.mode = mode
        self.updateGeometry()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.updateGeometry()
        self.update()

    # The items as drawn: bars largest first; donut slices in the given order, folded.
    def _bars(self) -> list[ChartItem]:
        return sorted(self.items, key=lambda i: -i.value)

    def _slices(self) -> list[ChartItem]:
        items = [i for i in self.items if i.value > 0]
        if len(items) <= MAX_SLICES:
            return items
        keep = sorted(items, key=lambda i: -i.value)[:MAX_SLICES - 1]
        rest = [i for i in items if i not in keep]
        kept = [i for i in items if i in keep]
        return kept + [ChartItem("Other", sum(i.value for i in rest), QColor(OTHER_GREY),
                                 "\n".join(f"{i.label}: {self.share_text(i)}" for i in rest))]

    def _slice_colours(self, slices: list[ChartItem]) -> list[QColor]:
        palette = CATEGORICAL_DARK if is_dark(self) else CATEGORICAL_LIGHT
        out = []
        for n, item in enumerate(slices):
            if self.use_item_colours and item.colour is not None:
                out.append(QColor(item.colour))
            elif item.label == "Other" and n == MAX_SLICES - 1:
                out.append(QColor(OTHER_GREY))
            else:
                out.append(QColor(palette[n % len(palette)]))
        return out

    # ---- the words shown for a value (legend) and on hover

    def _total(self) -> int:
        return sum(i.value for i in self.items)

    def _folded(self, item: ChartItem) -> bool:
        """The "Other" slice, standing for several items (not a count of compounds)."""
        return not any(item is i for i in self.items)

    def share_text(self, item: ChartItem) -> str:
        """The value as shown in the donut's legend: "27 of 28", or "3  (25%)"."""
        if self.denominator:
            return str(item.value) if self._folded(item) else f"{item.value} of {self.denominator}"
        return f"{item.value}  ({D.share_percent(item.value, self._total())}%)"

    def tip_text(self, item: ChartItem) -> str:
        """The hover text (plain text)."""
        if self.denominator and self._folded(item):
            tip = f"{item.label}: {item.value} in all"
        elif self.denominator:
            tip = (f"{item.label}: {item.value} of {self.denominator} {self.unit} "
                   f"({D.share_percent(item.value, self.denominator)}%)")
        else:
            tip = (f"{item.label}: {item.value} "
                   f"({D.share_percent(item.value, self._total())}% of the total)")
        if item.tip:
            tip += f"\n{item.tip}"
        return tip

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self.items:
            return QSize(240, 60)
        if self.mode == "pie":
            return QSize(320, max(150, len(self._slices()) * self.ROW + 16))
        return QSize(320, len(self.items) * self.ROW + 12)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.sizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        self._ring = None
        text = self.palette().text().color()
        muted = self.palette().placeholderText().color()
        if not self.items:
            p.setPen(muted)
            p.drawText(self.rect().adjusted(4, 4, -4, -4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       self.empty_text)
            return
        if self.mode == "pie":
            self._paint_donut(p, text, muted)
        else:
            self._paint_bars(p, text, muted)

    def _paint_bars(self, p: QPainter, text: QColor, muted: QColor) -> None:
        fm = self.fontMetrics()
        items = self._bars()
        top = 6
        w = self.width()
        label_w = min(max(fm.horizontalAdvance(i.label) for i in items) + 8, int(w * 0.48))
        value_w = max(fm.horizontalAdvance(str(i.value)) for i in items) + 10
        x0 = label_w + 6
        span = max(10, w - x0 - value_w - 4)
        biggest = max(i.value for i in items)
        axis = QColor(muted)
        axis.setAlpha(120)
        p.setPen(QPen(axis, 1))
        p.drawLine(QPointF(x0, top - 2), QPointF(x0, top + len(items) * self.ROW - 4))
        for n, item in enumerate(items):
            y = top + n * self.ROW
            row = QRectF(0, y, w, self.ROW)
            self._hits.append((row, self.items.index(item)))
            p.setPen(text)
            label = fm.elidedText(item.label, Qt.TextElideMode.ElideRight, label_w - 4)
            p.drawText(QRectF(0, y, label_w, self.ROW - 4),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            length = max(3.0 if item.value else 0.0, span * item.value / biggest)
            bar = QRectF(x0 + 1, y + (self.ROW - 4 - self.BAR) / 2, length, self.BAR)
            colour = QColor(item.colour or QColor("#2a78d6"))
            path = QPainterPath()
            # Square at the baseline, rounded at the data end.
            r = min(4.0, length / 2)
            path.moveTo(bar.left(), bar.top())
            path.lineTo(bar.right() - r, bar.top())
            path.quadTo(bar.right(), bar.top(), bar.right(), bar.top() + r)
            path.lineTo(bar.right(), bar.bottom() - r)
            path.quadTo(bar.right(), bar.bottom(), bar.right() - r, bar.bottom())
            path.lineTo(bar.left(), bar.bottom())
            path.closeSubpath()
            p.setPen(QPen(colour.darker(135), 1))   # an edge, so pale colours still show
            p.setBrush(colour)
            p.drawPath(path)
            p.setPen(text)
            p.drawText(QRectF(bar.right() + 6, y, value_w, self.ROW - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(item.value))

    def _paint_donut(self, p: QPainter, text: QColor, muted: QColor) -> None:
        fm = self.fontMetrics()
        slices = self._slices()
        colours = self._slice_colours(slices)
        total = sum(i.value for i in slices)
        # The ring gives way to the legend (labels and "28 (100%)" in full where possible).
        shares = [self.share_text(i) for i in slices]
        legend_w = max(fm.horizontalAdvance(i.label) for i in slices) + \
            max(fm.horizontalAdvance(s) for s in shares) + 38
        size = min(self.height() - 12, 150, int(self.width() * 0.45),
                   self.width() - legend_w - 22)
        size = max(size, 60)
        ring = QRectF(6, (self.height() - size) / 2, size, size)
        hole = size * 0.58
        background = self.palette().window().color()
        angle = 90 * 16
        for item, colour in zip(slices, colours):
            span = -round(360 * 16 * item.value / total)
            p.setPen(QPen(background, 2))   # a gap between the slices
            p.setBrush(colour)
            p.drawPie(ring, angle, span)
            angle += span
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(background)
        p.drawEllipse(ring.center(), hole / 2, hole / 2)
        self._ring = (ring.center(), size / 2, hole / 2, slices, total)
        if self.centre_text:
            f = QFont(self.font())
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.25)
            p.setFont(f)
            p.setPen(text)
            p.drawText(QRectF(ring.center().x() - hole / 2, ring.center().y() - hole / 2,
                              hole, hole), Qt.AlignmentFlag.AlignCenter, self.centre_text)
            p.setFont(self.font())
        # The legend: swatch, label, value and share.
        x = ring.right() + 16
        y = max(4.0, (self.height() - len(slices) * self.ROW) / 2)
        for n, (item, colour, share) in enumerate(zip(slices, colours, shares)):
            row = QRectF(x, y + n * self.ROW, self.width() - x, self.ROW)
            self._hits.append((row, -1 if self._folded(item) else
                               next(k for k, i in enumerate(self.items) if i is item)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(x, row.center().y() - 5, 10, 10), 2, 2)
            share_w = fm.horizontalAdvance(share) + 4
            p.setPen(text)
            label = fm.elidedText(item.label, Qt.TextElideMode.ElideRight,
                                  int(row.width() - 18 - share_w - 8))
            p.drawText(QRectF(x + 16, row.top(), row.width() - 16, row.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(muted)
            p.drawText(QRectF(x + 16, row.top(), row.width() - 20, row.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, share)

    # ------------------------------------------------------------------ hover

    def item_at(self, pos: QPointF) -> ChartItem | None:
        for shape, index in self._hits:
            if shape.contains(pos):
                return self.items[index] if 0 <= index < len(self.items) else \
                    self._slices()[-1]
        if self.mode == "pie" and self._ring is not None:
            centre, outer, inner, slices, total = self._ring
            dx, dy = pos.x() - centre.x(), centre.y() - pos.y()
            if inner <= math.hypot(dx, dy) <= outer and total:
                # Clockwise from 12 o'clock, as drawn.
                turn = (90 - math.degrees(math.atan2(dy, dx))) % 360
                acc = 0.0
                for item in slices:
                    acc += 360 * item.value / total
                    if turn <= acc:
                        return item
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        item = self.item_at(event.position())
        if item is None:
            QToolTip.hideText()
            return
        QToolTip.showText(event.globalPosition().toPoint(), plain_tip(self.tip_text(item)), self)


class ProgressBar(QWidget):
    """A slim bar: the share done in the "filled" colour on a faint track, with an
    optional second, larger share (`partly`: required fields filled) in a light tint."""

    def __init__(self, fraction: float = 0.0, colour: QColor | None = None,
                 partly: float = 0.0, parent: QWidget | None = None):
        super().__init__(parent)
        self.fraction = max(0.0, min(1.0, fraction))
        self.partly = max(0.0, min(1.0, partly))
        self.colour = colour or QColor("#27ae60")
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 14)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, (self.height() - 10) / 2, self.width() - 1, 10)
        track = QColor(self.palette().placeholderText().color())
        track.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, 4, 4)
        if self.partly > self.fraction:
            light = QColor(self.colour)
            light.setAlpha(110)
            p.setBrush(light)
            p.drawRoundedRect(QRectF(rect.left(), rect.top(),
                                     max(8.0, rect.width() * self.partly), rect.height()), 4, 4)
        if self.fraction > 0:
            done = QRectF(rect.left(), rect.top(), max(8.0, rect.width() * self.fraction),
                          rect.height())
            p.setBrush(self.colour)
            p.setPen(QPen(self.colour.darker(130), 1))
            p.drawRoundedRect(done, 4, 4)


# ---------------------------------------------------------------------- status colours

SAMPLE_TEXT = {D.FILLED: "92% · 28.4 mg", D.EMPTY: "empty", D.MISSING: "missing",
               D.FLAGGED: "flagged", D.WARNING: "a warning", D.NOT_USED: "not used"}


class StatusColoursDialog(QDialog):
    """Choose the dashboards' status colours (SPEC §13k.3), with a sample cell for each.
    The symbols stay, so colour is never the only cue."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Dashboard colours")
        self.project = project
        colours = D.status_colours(project.settings)
        intro = QLabel("The colours of the cells in the dashboards (this project's and every "
                       "Data section's). Cells show a light tint of the colour so their text "
                       "stays readable; the charts use the colour itself. The symbols "
                       "(✔ ○ ✖ ⚑ ⚠ —) are always shown as well.")
        intro.setWordWrap(True)
        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Status</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Colour</b>"), 0, 1)
        grid.addWidget(QLabel("<b>A cell looks like</b>"), 0, 3)
        self.buttons: dict[str, ColourButton] = {}
        self.none_boxes: dict[str, QCheckBox] = {}
        self.samples: dict[str, QLabel] = {}
        for r, key in enumerate(D.STATUS_KEYS, start=1):
            grid.addWidget(QLabel(f"{D.STATUS_ICONS[key]}  {D.STATUS_NAMES[key]}"), r, 0)
            button = ColourButton(colours[key] or "#ffffff", D.STATUS_NAMES[key])
            button.changed.connect(lambda _c, k=key: self._update(k))
            none = QCheckBox("No colour")
            none.setChecked(not colours[key])
            none.toggled.connect(lambda _on, k=key: self._update(k))
            sample = QLabel(f"  {D.STATUS_ICONS[key]}  {SAMPLE_TEXT[key]}  ")
            sample.setMinimumWidth(160)
            self.buttons[key], self.none_boxes[key], self.samples[key] = button, none, sample
            grid.addWidget(button, r, 1)
            grid.addWidget(none, r, 2)
            grid.addWidget(sample, r, 3)
        grid.setColumnStretch(3, 1)
        reset = QPushButton("Use the standard colours")
        reset.clicked.connect(self.reset)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch()
        bottom.addWidget(box)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(grid)
        layout.addLayout(bottom)
        for key in D.STATUS_KEYS:
            self._update(key)
        self.resize(560, self.sizeHint().height())

    def colours(self) -> dict[str, str]:
        return {k: "" if self.none_boxes[k].isChecked() else self.buttons[k].colour()
                for k in D.STATUS_KEYS}

    def reset(self) -> None:
        for key, colour in D.DEFAULT_STATUS_COLOURS.items():
            self.buttons[key].set_colour(colour or "#ffffff")
            self.none_boxes[key].setChecked(not colour)
            self._update(key)

    def _update(self, key: str) -> None:
        none = self.none_boxes[key].isChecked()
        button = self.buttons[key]
        button.setEnabled(not none)
        if none:   # a plain, greyed button (its colour comes back when unticked)
            button.setStyleSheet("")
            button.setText("none")
        else:
            button.set_colour(button.colour())
        colour = "" if none else self.buttons[key].colour()
        shade = blend(cell_colour(colour, key), self.palette().base().color())
        self.samples[key].setStyleSheet(
            f"QLabel {{ background: {shade.name()}; color: palette(text); "
            "border: 1px solid palette(mid); padding: 3px; }")


# ---------------------------------------------------------------------- the grid

class ProjectGridView(DashboardView):
    """The dashboard grid with every compound of every Data section, and a Section
    column. Its view choices are stored in project.json ("project_dashboard")."""

    EXTRA = ["Section"]
    NEW_BUTTON = False

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(project, D.ProjectScope(project), parent)
        self._cached: list[D.Row] | None = None
        self._paths: dict[str, str] = {}

    def set_rows(self, section_rows) -> None:
        """Rows already worked out by the dashboard (so they are built only once)."""
        self._cached = [r for _s, rows in section_rows for r in rows]
        self._paths = {r.compound.id: D.section_path(self.project, s)
                       for s, rows in section_rows for r in rows}
        self.section = D.ProjectScope(self.project)
        self.refresh()

    def _rows(self) -> list[D.Row]:
        if self._cached is None:
            self.set_rows(D.project_rows(self.project))
        return self._cached

    def _extra_items(self, row: D.Row) -> list[QTableWidgetItem]:
        path = self._paths.get(row.compound.id, "")
        item = _item(path, plain_tip(f"In the Data section “{path}”"))
        item.setData(KEY_ROLE, "")
        return [item]

    def _id_note_text(self) -> str:
        return ""

    def _empty_text(self) -> str:
        return "No compounds yet. Add a Data section, then compounds to it."

    def _remember(self, **values) -> None:
        view = D.project_view(self.project.settings)
        view.update(values)
        self.project.settings[D.PROJECT_SETTING] = view
        if not self.project.read_only:
            try:
                self.project.save_settings()
            except (ProjectError, OSError) as e:
                self.saved.emit(f"Could not save the dashboard setting: {e}")


# ---------------------------------------------------------------------- the page

def _group(title: str, inner: QWidget | None = None) -> tuple[QGroupBox, QVBoxLayout]:
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    if inner is not None:
        layout.addWidget(inner)
    return box, layout


class ProjectDashboard(QWidget):
    """The page shown for the project item at the top of the tree."""

    saved = Signal(str)
    open_compound = Signal(str, str)   # compound id, field key
    open_section = Signal(str)         # a Data section's id

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.summary: D.ProjectSummary | None = None

        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)   # a title is never read as HTML
        self.headline = QLabel()
        self.headline.setWordWrap(True)
        self.headline.setTextFormat(Qt.TextFormat.PlainText)
        f = self.headline.font()
        f.setPointSizeF(f.pointSizeF() * 1.1)
        self.headline.setFont(f)

        self.chart_box = QComboBox()
        for key, text in D.CHART_TYPES.items():
            self.chart_box.addItem(text, key)
        self.chart_box.setToolTip("Show the charts as bars or as donuts")
        self.chart_box.setCurrentIndex(max(0, self.chart_box.findData(
            D.chart_type(project.settings))))
        self.chart_box.currentIndexChanged.connect(self._chart_changed)
        self.colours_btn = QPushButton("Colours…")
        self.colours_btn.setToolTip("Change the status colours of the dashboards (stored with "
                                    "the project)" if not project.read_only else
                                    "The project is open read-only")
        self.colours_btn.setEnabled(not project.read_only)
        self.colours_btn.clicked.connect(self.choose_colours)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Count everything again")
        self.refresh_btn.clicked.connect(self.refresh)

        heading = QVBoxLayout()
        heading.addWidget(self.title)
        heading.addWidget(self.headline)
        controls = QHBoxLayout()
        for w in (QLabel("Charts:"), self.chart_box, self.colours_btn, self.refresh_btn):
            controls.addWidget(w)
        top = QHBoxLayout()
        top.addLayout(heading, 1)
        top.addLayout(controls)
        top.setAlignment(controls, Qt.AlignmentFlag.AlignTop)

        # Compounds complete, and each Data section's progress.
        self.overall = Chart("No compounds yet.")
        self.overall.use_item_colours = True
        self.overall.keep_zero = True
        overall_box, _ = _group("Compounds complete", self.overall)
        overall_box.setMinimumWidth(250)
        # (its rows are rebuilt in a fresh widget each time: see _fill_sections)
        self.sections_widget = QWidget()
        sections_box, sections_layout = _group("Data sections")
        sections_layout.addWidget(self.sections_widget)
        note = QLabel("Each bar: compounds complete (solid) and required fields filled "
                      "(light). Click a section's name to open it.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        sections_layout.addWidget(note)
        sections_layout.addStretch()
        self.sections_layout = sections_layout
        row1 = QHBoxLayout()
        row1.addWidget(overall_box, 2)
        row1.addWidget(sections_box, 5)

        self.missing = Chart("✔ No required data is missing.")
        self.missing.unit = "compounds"
        missing_box, missing_layout = _group("Missing required data, by field", self.missing)
        self.missing_note = QLabel()
        self.missing_note.setWordWrap(True)
        self.missing_note.setStyleSheet("color: palette(placeholder-text);")
        missing_layout.addWidget(self.missing_note)
        missing_layout.addStretch()
        self.problems = Chart("✔ No warnings or flags.")
        problems_box, problems_layout = _group("Warnings and flags, by type", self.problems)
        hint = QLabel("Open View ▸ Warnings to see and dismiss them one by one.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        problems_layout.addWidget(hint)
        problems_layout.addStretch()
        row2 = QHBoxLayout()
        row2.addWidget(missing_box, 1)
        row2.addWidget(problems_box, 1)
        # Side by side when there is room, one above the other in a narrow window (so the
        # page never needs scrolling sideways): see _arrange.
        self._rows = [(row1, overall_box, sections_box), (row2, missing_box, problems_box)]

        overview = QWidget()
        o = QVBoxLayout(overview)
        o.addLayout(top)
        o.addLayout(row1)
        o.addLayout(row2)
        o.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(overview)
        scroll.viewport().installEventFilter(self)
        self.scroll = scroll
        self.overview = overview

        self.grid = ProjectGridView(project)
        self.grid.open_compound.connect(self.open_compound)
        self.grid.saved.connect(self.saved)
        grid_box, _ = _group("Every compound", self.grid)
        grid_box.setMinimumHeight(260)   # a few rows, however small the window
        scroll.setMinimumHeight(160)

        # The overview (it scrolls) above the grid; drag the divider to give either more room.
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(scroll)
        self.splitter.addWidget(grid_box)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)
        self._sized = False
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._sized:
            # Once laid out at its real height: the overview gets what it needs, up to
            # about half, the grid the rest.
            self._sized = True
            QTimer.singleShot(0, self._size_splitter)

    def _size_splitter(self) -> None:
        self._arrange(self.scroll.viewport().width())
        overview = self.overview.sizeHint().height() + 4
        total = sum(self.splitter.sizes()) or self.height()
        # In a short window the grid gets the larger share (the overview scrolls).
        share = 0.55 if total >= 700 else 0.4
        top = min(overview, max(160, int(total * share)))
        self.splitter.setSizes([top, max(260, total - top)])

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._arrange(event.size().width())
        return super().eventFilter(obj, event)

    def _arrange(self, width: int) -> None:
        """Put each pair of boxes side by side if they fit in `width`, else one above the
        other."""
        for row, left, right in self._rows:
            need = (left.minimumSizeHint().width() + right.minimumSizeHint().width()
                    + row.spacing() + 24)
            direction = (QBoxLayout.Direction.LeftToRight if width >= need
                         else QBoxLayout.Direction.TopToBottom)
            if row.direction() != direction:
                row.setDirection(direction)

    def flush(self) -> None:
        """Nothing is typed on this page (part of the page interface)."""

    # ------------------------------------------------------------------ filling

    def refresh(self) -> None:
        project = self.project
        id_report = project.id_report()
        section_rows = D.project_rows(project, id_report)
        summary = D.summarize(project, section_rows, checks.project_warnings(project))
        self.summary = summary
        self.title.setText(str(project.settings.get("title") or project.folder.name))
        self.headline.setText(summary.text() if summary.sections else
                              "No Data sections yet: add one with “New section” to start "
                              "collecting compounds.")
        colours = D.status_colours(project.settings)
        mode = D.chart_type(project.settings)

        done, todo = summary.complete, summary.compounds - summary.complete
        self.overall.centre_text = (f"{D.percent(done, summary.compounds)}%"
                                    if summary.compounds else "")
        self.overall.set_items([
            ChartItem("Complete", done, solid(colours[D.FILLED]),
                      "Every required field filled"),
            ChartItem("Not complete", todo, solid(colours[D.MISSING]),
                      "At least one required field empty")], mode)
        # Each field against the number of compounds (one compound can miss several).
        self.missing.denominator = summary.compounds
        self.missing.set_items([ChartItem(label, n, solid(colours[D.MISSING]))
                                for label, n in summary.missing], mode)
        n = summary.compounds
        # (With no compounds there is nothing to explain: the chart says so instead.)
        self.missing.empty_text = ("✔ No required data is missing." if n else
                                   "No compounds yet. Add a Data section with “New section”, "
                                   "then compounds to it.")
        self.missing_note.setVisible(bool(n))
        self.missing_note.setText(
            f"How many of the {n} compound{'s' if n != 1 else ''} miss each required field "
            "(one compound can miss several). NMR and spectra are counted per nucleus (an "
            "empty ¹³C report, a ¹³C report with no spectrum)." if n else "")
        problems = [ChartItem(label, n, solid(colours[D.WARNING])) for label, n in summary.warnings]
        if summary.flags:
            problems.append(ChartItem(D.FLAGS_LABEL, summary.flags, solid(colours[D.FLAGGED]),
                                      "Fields flagged by a person (⚑)"))
        self.problems.set_items(problems, mode)
        self._fill_sections(summary, colours)
        self._arrange(self.scroll.viewport().width())
        self.grid.set_rows(section_rows)

    def _fill_sections(self, summary: D.ProjectSummary, colours: dict) -> None:
        # A new widget for the rows each time (a grid layout keeps the height of rows that
        # were taken out).
        old = self.sections_widget
        self.sections_widget = QWidget()
        self.sections_layout.replaceWidget(old, self.sections_widget)
        old.hide()
        old.deleteLater()
        grid = QGridLayout(self.sections_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        if not summary.sections:
            grid.addWidget(QLabel("No Data sections yet."), 0, 0)
            return
        bar_colour = solid(colours[D.FILLED], "#27ae60")
        fm = self.fontMetrics()
        for r, sp in enumerate(summary.sections):
            # A long path ("A › B › C") keeps its end (the section itself); the tooltip has
            # it in full. The name is escaped, so "ee < 90%" shows as typed.
            shown = short_path(sp.path, fm, SECTION_NAME_WIDTH)
            name = QLabel(f"<a href='{html.escape(sp.section.id, quote=True)}'>"
                          f"{html.escape(shown, quote=False)}</a>")
            name.setTextFormat(Qt.TextFormat.RichText)
            name.setToolTip(plain_tip(f"Open “{sp.path}”"))
            name.linkActivated.connect(self.open_section)
            name.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
            bar = ProgressBar(sp.fraction, bar_colour, sp.field_fraction if sp.required else 0.0)
            bar.setToolTip(f"{sp.complete} of {sp.compounds} compounds complete (solid)\n"
                           f"{sp.filled} of {sp.required} required fields filled (light)")
            fields = (f"{D.percent(sp.filled, sp.required)}% of fields"
                      if sp.required else "no required fields")
            counts = QLabel(f"{sp.complete} / {sp.compounds} complete · {fields}")
            counts.setToolTip(f"{sp.complete} of {sp.compounds} compounds complete; "
                              f"{sp.filled} of {sp.required} required fields filled")
            marks = QLabel(f"⚠ {sp.warnings}   ⚑ {sp.flags}")
            marks.setToolTip(f"{sp.warnings} warning(s) and {sp.flags} flag(s) in this section")
            grid.addWidget(name, r, 0)
            grid.addWidget(bar, r, 1)
            grid.addWidget(counts, r, 2)
            grid.addWidget(marks, r, 3)

    # ------------------------------------------------------------------ choices

    def _chart_changed(self) -> None:
        mode = self.chart_box.currentData()
        view = D.project_view(self.project.settings)
        view["chart"] = mode
        self.project.settings[D.PROJECT_SETTING] = view
        if not self.project.read_only:
            try:
                self.project.save_settings()
            except (ProjectError, OSError) as e:
                self.saved.emit(f"Could not save the chart choice: {e}")
        for chart in (self.overall, self.missing, self.problems):
            chart.set_mode(mode)

    def choose_colours(self) -> None:
        dlg = StatusColoursDialog(self.project, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        D.store_status_colours(self.project.settings, dlg.colours())
        try:
            self.project.save_settings()
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.refresh()
        self.saved.emit("Dashboard colours saved")
