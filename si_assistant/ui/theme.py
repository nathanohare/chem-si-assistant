"""The program's look (SPEC §13w), matched to Chem Illustrator's: a light grey-blue window
with white panels and thin borders, one blue accent (a pale blue for hover and selection),
rounded buttons and fields, thin line icons. Light or dark as the computer is set, and it
changes with it.

`apply(app)` sets it up once, at start-up: Qt's "Fusion" style (the same on Windows,
macOS and Linux), a palette from the colours below, and a style sheet for the shapes.
Widgets that colour themselves use `palette(…)` in their own style sheets, or `colour()`.
`icon(name)` draws one of the line icons below in the text colour.

The colours are Chem Illustrator's (src/app.css there): keep them in step.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, \
    QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QStyleFactory

LIGHT = {
    "bg": "#eef0f3",           # the window around the panels
    "panel": "#ffffff",        # panels, fields, lists
    "text": "#1d2330",
    "muted": "#677084",        # hints, labels of secondary things
    "line": "#d6dae1",         # borders
    "accent": "#2f6fdf",
    "accent_soft": "#e3ecfc",  # hover and selection
    "highlight": "#2f6fdf",    # selected text (with white text)
    "ok": "#18794e",
    "warn": "#9a5b00",
    "error": "#b4232a",
    "thumb": "rgba(90, 100, 120, 0.38)",
    "thumb_hover": "rgba(70, 80, 100, 0.6)",
}
DARK = {
    "bg": "#1b1e24",
    "panel": "#252a32",
    "text": "#e6e9ef",
    "muted": "#9aa3b5",
    "line": "#3a414d",
    "accent": "#7aa7ff",
    "accent_soft": "#2c3b57",
    "highlight": "#3a64b0",
    "ok": "#5fd39a",
    "warn": "#f0b85a",
    "error": "#ff7b81",
    "thumb": "rgba(170, 180, 200, 0.32)",
    "thumb_hover": "rgba(190, 200, 220, 0.55)",
}
UI_FONTS = ("IBM Plex Sans",)   # Chem Illustrator's; else the computer's own UI font

_state = {"dark": False, "folder": None, "filter": None}


def is_dark() -> bool:
    return bool(_state["dark"])


def colours() -> dict[str, str]:
    return DARK if is_dark() else LIGHT


def colour(name: str) -> str:
    """One of the theme's colours ("accent", "muted", "warn"…), as now shown."""
    return colours()[name]


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        value = scheme()
        if value == Qt.ColorScheme.Dark:
            return True
        if value == Qt.ColorScheme.Light:
            return False
    # (unknown: as the computer's own palette)
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


# ---------------------------------------------------------------------- icons
# 24 × 24, drawn with the text colour, 1.6 wide, round ends: as Chem Illustrator's.

def _svg(body: str, colour_: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" '
            f'height="24" fill="none" stroke="{colour_}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


ICONS = {
    # the toolbar
    "new_section": '<path d="M4 6.5h6l2 2h8v10H4z"/><path d="M12 11v6M9 14h6"/>',
    "new_compound": '<polygon points="12,3.5 18.5,7.25 18.5,14.75 12,18.5 5.5,14.75 5.5,7.25"/>'
                    '<path d="M18 16v6M15 19h6"/>',
    "new_scheme": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/>'
                  '<path d="M7 12h9"/><path d="M13.5 9.5L16 12l-2.5 2.5"/>',
    "compile": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path d="M9 13h6M9 16.5h4"/>',
    "preview": '<path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
               '<circle cx="12" cy="12" r="2.8"/>',
    "live": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M14 4.5v15"/>'
            '<path d="M16.3 9h2M16.3 12h2M16.3 15h2"/>',
    "warning": '<path d="M12 4L21 19.5H3z"/><path d="M12 10v4.5"/><path d="M12 17.2v.1"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6'
                'M18.6 12h2.6M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M5.5 18.5l1.8-1.8M16.7 7.3l1.8-1.8"/>',
    "trash": '<path d="M4.5 7h15"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7l1 13h9l1-13"/>'
             '<path d="M10.5 10.5v6M13.5 10.5v6"/>',
    "references": '<path d="M5 4.5h9.5a2 2 0 0 1 2 2v13H7a2 2 0 0 1-2-2z"/><path d="M5 17.5a2 2 0 0 1 2-2h9.5"/>'
                  '<path d="M8.5 8.5h5M8.5 11.5h5"/>',
    # the tree
    "project": '<path d="M3.5 10.5L12 4l8.5 6.5"/><path d="M6 9v10.5h12V9"/><path d="M10 19.5v-5h4v5"/>',
    "front": '<rect x="4.5" y="3.5" width="15" height="17" rx="1.5"/><path d="M8 8h8M8 11.5h8M8 15h5"/>',
    "page": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/>',
    "text": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path d="M9 12h6M9 15.5h6"/>',
    "file": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/>'
            '<path d="M9 17l2.2-3 1.6 2 1.2-1.4L16 17z"/>',
    "data": '<path d="M3.5 6.5h6l2 2h9v11h-17z"/>',
    "scheme": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M7 12h9"/>'
              '<path d="M13.5 9.5L16 12l-2.5 2.5"/>',
    "manuscript": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path d="M9 14l2 2 4-4.5"/>',
    "spectrum": '<path d="M3 19.5h18"/><path d="M5 19.5v-3M8 19.5v-9M10.5 19.5V13M13 19.5V6'
                'M15.5 19.5v-8M18.5 19.5v-5"/>',
}


def icon(name: str, colour_: str | None = None) -> QIcon:
    """One of the line icons, in the text colour (or `colour_`); greyed when disabled."""
    from PySide6.QtSvg import QSvgRenderer
    out = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, colour_ or colour("text")),
                      (QIcon.Mode.Active, colour_ or colour("text")),
                      (QIcon.Mode.Selected, colour_ or colour("text")),
                      (QIcon.Mode.Disabled, colour("muted"))):
        renderer = QSvgRenderer(QByteArray(_svg(ICONS[name], ink).encode()))
        for size in (16, 24, 32, 48):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            out.addPixmap(pixmap, mode)
    return out


# ---------------------------------------------------------------------- the style sheet

def _arrow_files(c: dict[str, str]) -> dict[str, str]:
    """Small arrow pictures for the style sheet (combo boxes, spin boxes), written once to
    a folder of their own (style sheets take pictures only from files)."""
    if _state["folder"] is None:
        _state["folder"] = Path(tempfile.mkdtemp(prefix="csa-theme-"))
    folder: Path = _state["folder"]
    out = {}
    for name, body in (("down", '<path d="M7 10l5 5 5-5"/>'), ("up", '<path d="M7 14l5-5 5 5"/>')):
        for key, ink in (("", c["text"]), ("_off", c["muted"])):
            path = folder / f"{name}{key}_{'dark' if c is DARK else 'light'}.svg"
            if not path.exists():
                path.write_text(_svg(body, ink).replace('stroke-width="1.6"',
                                                        'stroke-width="2"'), encoding="utf-8")
            out[name + key] = path.as_posix()
    tick = folder / "tick.svg"   # (white on the accent: the same in light and dark)
    if not tick.exists():
        tick.write_text(_svg('<path d="M6 12.5l4 4 8-9"/>', "#ffffff").replace(
            'stroke-width="1.6"', 'stroke-width="2.6"'), encoding="utf-8")
    out["tick"] = tick.as_posix()
    return out


def style_sheet(c: dict[str, str]) -> str:
    a = _arrow_files(c)
    return f"""
QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['line']};
           padding: 4px 6px; }}

QPushButton {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['line']};
              border-radius: 6px; padding: 4px 10px; min-height: 18px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed, QPushButton:checked {{ background: {c['accent_soft']};
                                            border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['bg']}; border-color: {c['line']}; }}
QPushButton:focus {{ outline: none; }}
QPushButton::menu-indicator {{ image: url({a['down']}); subcontrol-position: right center;
                               subcontrol-origin: padding; width: 12px; right: 4px; }}

QToolButton {{ color: {c['text']}; background: transparent; border: 1px solid transparent;
              border-radius: 6px; padding: 3px 6px; }}
QToolButton:hover {{ background: {c['accent_soft']}; }}
QToolButton:checked, QToolButton:pressed {{ background: {c['accent_soft']};
                                            border-color: {c['accent']}; color: {c['accent']}; }}
QToolButton:disabled {{ color: {c['muted']}; }}
QToolButton[popupMode="2"] {{ padding-right: 18px; }}
QToolButton::menu-indicator {{ image: url({a['down']}); width: 10px;
                               subcontrol-position: right center; subcontrol-origin: padding;
                               right: 4px; }}
QToolButton::menu-indicator:disabled {{ image: url({a['down_off']}); }}

QToolBar {{ background: transparent; border: none; spacing: 2px; }}
QToolBar#main_toolbar {{ background: {c['panel']}; border: none;
                         border-bottom: 1px solid {c['line']}; padding: 4px 6px; spacing: 3px; }}
QToolBar::separator {{ background: {c['line']}; width: 1px; margin: 4px 6px; }}

QMenuBar {{ background: {c['panel']}; color: {c['text']}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 5px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {c['accent_soft']}; }}
QMenu {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['line']};
        border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 10px; border-radius: 5px; background: transparent; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {c['panel']}; color: {c['text']}; border: 1px solid {c['line']};
    border-radius: 5px; selection-background-color: {c['highlight']};
    selection-color: #ffffff; }}
QLineEdit, QAbstractSpinBox, QComboBox {{ padding: 3px 6px; min-height: 18px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QAbstractSpinBox:focus,
QComboBox:focus {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QAbstractSpinBox:disabled,
QComboBox:disabled {{ color: {c['muted']}; background: {c['bg']}; }}
QLineEdit:read-only {{ background: {c['bg']}; }}
QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {c['accent']}; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
                        width: 20px; border: none; }}
QComboBox::down-arrow {{ image: url({a['down']}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({a['down_off']}); }}
QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']};
                               border: 1px solid {c['line']}; outline: none;
                               selection-background-color: {c['accent_soft']};
                               selection-color: {c['text']}; padding: 2px; }}
QAbstractSpinBox {{ padding-right: 20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: none; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background: {c['accent_soft']}; border-radius: 4px; }}
QAbstractSpinBox::up-arrow {{ image: url({a['up']}); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({a['down']}); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url({a['up_off']}); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{
    image: url({a['down_off']}); }}

QTreeView, QListView, QTableView {{ background: {c['panel']}; color: {c['text']};
    alternate-background-color: {c['bg']}; border: 1px solid {c['line']};
    border-radius: 6px; outline: none; selection-background-color: {c['accent_soft']};
    selection-color: {c['text']}; gridline-color: {c['line']}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border-radius: 4px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {c['bg']}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {c['accent_soft']}; color: {c['text']}; }}
QTreeView::branch:selected {{ background: transparent; }}
QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['line']}; border-right: 1px solid {c['line']};
    padding: 4px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['panel']}; border: none; }}

QTabWidget::pane {{ border: 1px solid {c['line']}; border-radius: 6px; top: -1px;
                    background: {c['panel']}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; border: 1px solid transparent;
               border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px;
               padding: 6px 14px; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {c['text']}; background: {c['accent_soft']}; }}
QTabBar::tab:selected {{ background: {c['panel']}; color: {c['text']};
                        border-color: {c['line']}; }}

QGroupBox {{ border: 1px solid {c['line']}; border-radius: 8px; margin-top: 16px;
            padding: 10px 8px 8px 8px; background: {c['panel']}; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 8px;
                   padding: 0 4px; color: {c['muted']}; font-weight: 600; }}

QStatusBar {{ background: {c['panel']}; color: {c['muted']};
             border-top: 1px solid {c['line']}; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {c['bg']}; }}
QSplitter::handle:hover {{ background: {c['accent_soft']}; }}
QMainWindow::separator {{ background: {c['bg']}; width: 5px; height: 5px; }}
QDockWidget {{ color: {c['text']}; }}
QDockWidget::title {{ background: {c['panel']}; padding: 6px 8px;
                     border-bottom: 1px solid {c['line']}; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {c['thumb']}; border-radius: 4px; }}
QScrollBar::handle:vertical {{ min-height: 28px; margin: 2px 2px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; margin: 2px 2px; }}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {c['thumb_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 6px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {c['muted']}; }}
QCheckBox::indicator, QRadioButton::indicator, QTreeView::indicator, QListView::indicator,
QTableView::indicator {{ width: 14px; height: 14px; border: 1px solid {c['muted']};
    background: {c['panel']}; }}
QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator {{
    border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked,
QTableView::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url({a['tick']}); }}
QRadioButton::indicator:checked {{ border: 4px solid {c['accent']}; background: #ffffff;
    width: 8px; height: 8px; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {c['line']};
    background: {c['bg']}; }}
QCheckBox::indicator:checked:disabled {{ background: {c['line']}; border-color: {c['line']}; }}

QProgressBar {{ border: 1px solid {c['line']}; border-radius: 5px; background: {c['panel']};
               text-align: center; color: {c['text']}; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['line']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; margin: -6px 0;
                             border-radius: 7px; }}
"""


def palette(c: dict[str, str]) -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    roles = {R.Window: c["bg"], R.WindowText: c["text"], R.Base: c["panel"],
             R.AlternateBase: c["bg"], R.Text: c["text"], R.Button: c["panel"],
             R.ButtonText: c["text"], R.BrightText: "#ffffff", R.Highlight: c["highlight"],
             R.HighlightedText: "#ffffff", R.ToolTipBase: c["panel"], R.ToolTipText: c["text"],
             R.PlaceholderText: c["muted"], R.Link: c["accent"], R.LinkVisited: c["accent"],
             R.Light: c["panel"], R.Midlight: c["line"], R.Mid: c["line"], R.Dark: c["muted"],
             R.Shadow: "#000000"}
    for role, value in roles.items():
        p.setColor(role, QColor(value))
    G = QPalette.ColorGroup.Disabled
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G, role, QColor(c["muted"]))
    p.setColor(G, R.Base, QColor(c["bg"]))
    p.setColor(G, R.Button, QColor(c["bg"]))
    return p


class _RoundMenus(QObject):
    """Menus with rounded corners (their corners are see-through, not square)."""

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Polish and isinstance(obj, QMenu):
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            obj.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            obj.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        return False


def apply(app: QApplication, dark: bool | None = None) -> None:
    """Give the program its look (light or dark as the computer is set, unless `dark` is
    given), and follow the computer when it switches."""
    first = _state["filter"] is None
    if first:
        app.setStyle(QStyleFactory.create("Fusion"))
        families = set(QFontDatabase.families())
        family = next((f for f in UI_FONTS if f in families), None)
        if family is not None:
            font = QFont(app.font())
            font.setFamily(family)
            app.setFont(font)
        _state["filter"] = _RoundMenus(app)
        app.installEventFilter(_state["filter"])
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _s: apply(app))
    _state["dark"] = system_is_dark() if dark is None else dark
    c = colours()
    app.setPalette(palette(c))
    app.setStyleSheet(style_sheet(c))


def icon_size() -> QSize:
    return QSize(20, 20)
