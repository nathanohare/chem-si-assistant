"""Structure field and HRMS field editors (SPEC §7.1 fields 1 and 8, §8.1)."""

from __future__ import annotations

import re
from collections import OrderedDict
from html import escape as html_escape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton,
                               QToolButton, QVBoxLayout, QWidget)

from ..compile.qt_images import read_image
from ..core import chem, checks
from ..core import drawing_scale
from ..core import ms_reports
from ..core import fields as F
from ..core.project import PICTURE_FILE_EXTENSIONS, ProjectError
from .clipboard_image import VECTOR_EVERYWHERE, PastedPicture, clipboard_picture, paste_dpi
from .field_widgets import FieldEditor, PartsEditor, get_part_value, set_part_value

STRUCTURE_FILE_EXTENSIONS = (".mol", ".sdf", ".sd", ".mdl")


def formula_html(formula: str) -> str:
    """"C8H11O3" -> C<sub>8</sub>H<sub>11</sub>O<sub>3</sub>; "[13C]" -> <sup>13</sup>C."""
    text = re.sub(r"\[(\d+)([A-Z][a-z]?)\]", r"<sup>\1</sup>\2", formula)
    return re.sub(r"(?<![\d>])(\d+)(?!</sup>)", r"<sub>\1</sub>", text)


_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def formula_plain(text: str) -> str:
    """"C12H12N+" -> "C₁₂H₁₂N⁺" for places that can't show rich text (dropdowns, hints)."""
    import re
    text = re.sub(r"\[(\d+)([A-Z][a-z]?)\]",
                  lambda m: m.group(1).translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
                  + m.group(2), text)
    body = re.sub(r"(?<=[A-Za-z)])(\d+)", lambda m: m.group(1).translate(_SUB), text)
    return body.replace("+", "⁺").replace("−", "⁻")


def device_pixels(size: QSize, dpr: float) -> QSize:
    """`size` (in the interface's units) in screen pixels at device pixel ratio `dpr`."""
    return QSize(max(1, round(size.width() * dpr)), max(1, round(size.height() * dpr)))


def mol_pixmap(mol, size: QSize, settings: "chem.DrawingSettings | None" = None,
               dpr: float = 1.0) -> QPixmap:
    """The program's drawing of `mol`, filling `size`, in the project's drawing style
    (`settings`, from Project settings ▸ Structures). With `dpr` (the screen's device
    pixel ratio, e.g. 1.5 or 2 on a high-resolution screen) it is drawn with that many
    pixels per unit, so it stays sharp (SPEC §13n.7b)."""
    pixels = device_pixels(size, dpr)
    pixmap = QPixmap()
    pixmap.loadFromData(chem.structure_png(mol, pixels.width(), pixels.height(), settings),
                        "PNG")
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def generic_structure(project, compound) -> tuple:
    """(molecule, generic atoms) of a structure.mol the project reads as no structure
    because it has a generic atom (R, X, *: kept, e.g. for a general scheme), so the page
    can still draw it and give its SMILES, without formula or masses. (None, []) otherwise
    (also when the file can't be read just now). Both come from the project's one cached
    read of the file, so they always agree."""
    atoms = project.generic_structure_atoms(compound)
    if not atoms:
        return None, []
    return project.structure_for_drawing(compound), atoms


def generic_note(atoms: list[str]) -> str:
    """What the page says for a structure with a generic atom (the warning's own words)."""
    return checks.generic_structure_message(atoms)


def fit_svg_image(path: Path, pixels: QSize) -> QImage:
    """An SVG picture (or a PDF's first page) drawn straight at the largest size that fits
    `pixels` (a vector drawing is sharp at any size); a null QImage if it can't be read."""
    from ..compile.qt_images import pdf_size_in, render_page, render_svg_bytes, svg_file_size_in
    is_pdf = Path(path).suffix.lower() == ".pdf"
    size = pdf_size_in(Path(path)) if is_pdf else svg_file_size_in(Path(path))
    if size is None or size[0] <= 0 or size[1] <= 0:
        return QImage()
    fitted = QSize(round(size[0] * 1000), round(size[1] * 1000)).scaled(
        pixels, Qt.AspectRatioMode.KeepAspectRatio)
    if is_pdf:
        image = render_page(Path(path), 1, max(1.0, fitted.width() / size[0]))
        return image if image.isNull() else image.scaled(
            fitted, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    try:
        data = Path(path).read_bytes()
    except OSError:
        return QImage()
    return render_svg_bytes(data, max(1, fitted.width()), max(1, fitted.height()))


def vector_picture(path: Path) -> Path | None:
    """The vector drawing of a structure picture: the picture itself (an SVG, or ChemDraw's
    PDF on macOS), or an SVG kept beside a PNG (same name), else None."""
    path = Path(path)
    if path.suffix.lower() in (".svg", ".pdf"):
        return path
    svg = path.with_suffix(".svg")
    return svg if svg.is_file() else None


def drawing_settings(project) -> "chem.DrawingSettings":
    return chem.DrawingSettings.from_settings(project.settings)


_picture_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
PICTURE_CACHE_SIZE = 300  # small pictures (thumbnails, previews), a few MB in all


def picture_pixmap(path: Path, size: QSize, dpr: float = 1.0) -> QPixmap:
    """A picture file shrunk to fit `size`, for showing on screen (a null QPixmap if it
    can't be read). Pasted pictures are large (1200 dpi), so the shrunk copy is kept
    until the file changes, instead of reading the whole picture every time.

    With `dpr` (the screen's device pixel ratio) it has that many pixels per unit, so it
    is sharp on a high-resolution screen (SPEC §13n.7b): an SVG (the picture, or one kept
    beside it) is drawn straight at that size; any other picture is shrunk smoothly from
    the full-resolution original."""
    svg = vector_picture(path)
    try:
        st = Path(svg or path).stat()
    except OSError:
        return QPixmap()
    key = (str(svg or path), st.st_mtime_ns, st.st_size, size.width(), size.height(),
           round(dpr, 3))
    pixmap = _picture_cache.get(key)
    if pixmap is not None:
        _picture_cache.move_to_end(key)
        return pixmap
    pixels = device_pixels(size, dpr)
    image = fit_svg_image(svg, pixels) if svg is not None else QImage()
    if image.isNull() and Path(path).exists():
        image = read_image(Path(path))
        if not image.isNull():
            image = image.scaled(pixels, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
    if image.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    del image
    _picture_cache[key] = pixmap
    while len(_picture_cache) > PICTURE_CACHE_SIZE:
        _picture_cache.popitem(last=False)
    return pixmap


def clipboard_structure_text() -> str:
    """Structure text from the clipboard: ChemDraw's MDLCT format, a MOL file, or plain text."""
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return ""
    for fmt in data.formats():
        if "MDLCT" in fmt:
            raw = bytes(data.data(fmt))
            if raw:
                return chem.mdlct_to_molblock(raw)
        if "mdl-molfile" in fmt or "x-mdl" in fmt:
            raw = bytes(data.data(fmt))
            if raw:
                return raw.decode("latin-1")
    return data.text() if data.hasText() else ""


def clipboard_picture_for(project) -> PastedPicture | None:
    """The clipboard's picture, or None if there is none. ChemDraw's metafile becomes a
    vector drawing (SVG) when the conversion passes the self-check, its PDF (macOS) is
    kept as it is (SPEC §13n.1); otherwise it is rendered at the project's "Pasted picture
    resolution", like PowerPoint's "Paste as Picture". The vector original comes with it."""
    return clipboard_picture(paste_dpi(project.settings), vector=VECTOR_EVERYWHERE)

# ---------------------------------------------------------------------- paste targets

HIGHLIGHT = "#2f80ed"


def paste_key() -> str:
    """"Ctrl+V" (or "⌘V" on a Mac). Only call this once the application is running:
    asking Qt for key names before then crashes on Windows."""
    return QKeySequence(QKeySequence.StandardKey.Paste).toString(
        QKeySequence.SequenceFormat.NativeText) or "Ctrl+V"


def copy_key() -> str:
    """"Ctrl+C" (or "⌘C" on a Mac). Like paste_key(), only call this once the application
    is running."""
    return QKeySequence(QKeySequence.StandardKey.Copy).toString(
        QKeySequence.SequenceFormat.NativeText) or "Ctrl+C"


class PasteFrame(QFrame):
    """A frame around a paste target's content (an Attachment section's file, a compound's
    spectra): outlined in blue while it is selected for pasting."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PasteFrame")
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        border = f"3px solid {HIGHLIGHT}" if active else "1px solid transparent"
        self.setStyleSheet(f"QFrame#PasteFrame {{ border: {border}; }}")


class PasteTargetMixin:
    """Makes a field box a click-to-select paste target.

    Clicking anywhere in the box (picture, buttons, empty space) selects it: its
    picture gets a blue outline and the hint says "Ctrl+V pastes here". Ctrl+V
    then pastes into this box only. Right-click offers the same paste commands.
    Typing in a text box inside (e.g. SMILES) keeps normal text pasting.
    """

    def _init_paste_target(self, area: QWidget, menu: list[tuple[str, object]],
                           scope: QWidget | None = None) -> QLabel:
        """`scope` is the widget whose clicks select this paste target (default: self).
        Paste targets must not be nested, or Ctrl+V would be ambiguous."""
        self._paste_area = area
        self._paste_menu = menu
        self._paste_active = False
        self._paste_locked = False  # "saved": no Ctrl+V, no hint; right-click = Edit menu
        self._paste_scope = scope = scope or self
        scope.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        area.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # (a parent straight away: shown without one, it would flash up as a window)
        self.paste_hint = QLabel(scope)
        shortcut = QShortcut(QKeySequence.StandardKey.Paste, scope)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._shortcut_paste)
        scope.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        scope.customContextMenuRequested.connect(self._show_paste_menu)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self._set_paste_active(False)
        return self.paste_hint

    def _on_focus_changed(self, _old, new) -> None:
        try:
            scope = self._paste_scope
            active = new is not None and (new is scope or scope.isAncestorOf(new))
            if active != self._paste_active:
                self._set_paste_active(active)
        except RuntimeError:  # this box has already been closed
            pass

    def _set_paste_active(self, active: bool) -> None:
        self._paste_active = active
        if hasattr(self._paste_area, "set_active"):
            self._paste_area.set_active(active)
        else:
            border = f"3px solid {HIGHLIGHT}" if active else "1px solid #999"
            self._paste_area.setStyleSheet(
                f"QLabel {{ background: white; color: #666; border: {border}; }}")
        self.paste_hint.setVisible(not self._paste_locked)
        if self.project.read_only:
            self.paste_hint.setText("")
        elif active:
            self.paste_hint.setText(f"▶ {paste_key()} pastes here")
            self.paste_hint.setStyleSheet(f"color: {HIGHLIGHT}; font-weight: bold;")
        else:
            self.paste_hint.setText(f"Click this box, then {paste_key()} to paste — or right-click")
            self.paste_hint.setStyleSheet("color: palette(placeholder-text);")

    def _shortcut_paste(self) -> None:
        if not self.project.read_only and not self._paste_locked:
            self.paste_from_clipboard()

    def set_paste_locked(self, locked: bool) -> None:
        self._paste_locked = locked
        self._set_paste_active(self._paste_active)

    def locked_menu(self, menu: QMenu) -> None:
        """Fill the right-click menu shown while locked (subclasses override)."""

    def _show_paste_menu(self, pos) -> None:
        if self.project.read_only:
            return
        self._paste_scope.setFocus()  # right-clicking also selects the box
        menu = QMenu(self)
        if self._paste_locked:
            self.locked_menu(menu)
        else:
            for text, slot in self._paste_menu:
                menu.addAction(text, slot)
        if not menu.isEmpty():
            menu.exec(self._paste_scope.mapToGlobal(pos))

    def paste_from_clipboard(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------- structure field

class StructureEditor(PasteTargetMixin, FieldEditor):
    # NMR nuclei added automatically (e.g. ["19F"]); the page rebuilds and recalculates.
    structure_changed = Signal(list)

    IMAGE_SIZE = QSize(300, 220)   # (so a scheme's compound tab fits beside the live preview)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(not self.project.read_only)

        self.image = QLabel()
        self.image.setFixedSize(self.IMAGE_SIZE)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = self._init_paste_target(self.image, [
            ("Paste structure (MOL / SMILES)", self._paste),
            ("Paste image (use a picture in the SI)", self._paste_image),
            ("Open MOL/SDF file…", self._open_file)])
        self.image_note = QLabel()
        self.image_note.setStyleSheet("color: palette(placeholder-text);")

        self.smiles = QLineEdit()
        self.smiles.setPlaceholderText("Type or paste SMILES and press Enter")
        self.smiles.returnPressed.connect(self._apply_smiles)

        buttons = QGridLayout()
        specs = [("Paste structure", "Paste SMILES or a MOL block from the clipboard "
                  "(in ChemDraw: select the structure and Copy, or Edit ▸ Copy As ▸ SMILES)",
                  self._paste),
                 ("Open MOL/SDF file…", "Read a .mol or .sdf file", self._open_file),
                 ("Copy SMILES", "Copy this structure as SMILES", self._copy_smiles),
                 ("Clear", "Remove the structure (it goes to the project Trash)", self._clear),
                 ("Paste image", "Paste a picture from the clipboard (e.g. copied from ChemDraw) "
                  "to use in the SI instead of the program's drawing. Formula and masses still "
                  "come from the structure.", self._paste_image),
                 ("Use own picture…", "Choose an image file as this structure's picture. "
                  "Formula and masses still come from the structure.", self._choose_image),
                 ("Remove picture", "Remove the picture (it goes to the project Trash) and "
                  "show the program's drawing", self._remove_image)]
        self.buttons: dict[str, QPushButton] = {}
        for i, (text, tip, slot) in enumerate(specs):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            # One column: two side by side made the page wider than the space beside the
            # live preview (1200x800).
            buttons.addWidget(b, i, 0)
            self.buttons[text] = b
        self.buttons["Copy SMILES"].setProperty("keep_enabled", True)
        self.controls = QWidget()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.controls.setLayout(buttons)
        self.done_btn = QPushButton("Done editing")
        self.done_btn.setToolTip("Hide the editing buttons again")
        self.done_btn.clicked.connect(lambda: self.set_editing(False))

        # Once there is a structure, the buttons are tucked into this menu so the structure
        # isn't changed by accident (and the page is shorter).
        self.edit_btn = QToolButton()
        self.edit_btn.setText("Edit or Remove")
        self.edit_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.edit_menu = QMenu(self.edit_btn)
        self.edit_menu.aboutToShow.connect(lambda: self._fill_edit_menu(self.edit_menu))
        self.edit_btn.setMenu(self.edit_menu)
        self._editing = False

        self.info = QLabel()
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info.setWordWrap(True)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d35400;")
        self.error.hide()

        right = QVBoxLayout()
        right.addWidget(self.smiles)
        right.addWidget(self.controls)
        # Formula and masses with the Edit or Remove menu: once a structure is in, this one
        # line is all the field shows (SPEC §13n.7a; the page header shows the structure).
        info_row = QHBoxLayout()
        info_row.addWidget(self.info, 1)
        info_row.addWidget(self.edit_btn, 0, Qt.AlignmentFlag.AlignTop)
        info_row.addWidget(self.done_btn, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(info_row)
        right.addWidget(self.error)
        right.addStretch()
        self.picture_box = QWidget()   # the picture with its paste hint and note
        left = QVBoxLayout(self.picture_box)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(hint)
        left.addWidget(self.image)
        left.addWidget(self.image_note)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.picture_box, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(right, 1)
        self.refresh()

    # ---- display

    def refresh(self) -> None:
        mol = self.project.read_structure(self.compound)
        try:
            info = chem.mol_info(mol) if mol is not None else None
            problem = ""
        except chem.ChemError as e:   # (no formula or masses)
            info, problem = None, str(e)
        # A structure with a generic atom (R) is read as none: still drawn, with its SMILES
        # and a note, and Clear stays on, so it can be replaced or removed.
        generic_mol, generic = ((None, []) if mol is not None
                                 else generic_structure(self.project, self.compound))
        if generic:
            problem = generic_note(generic)
        shown = mol if mol is not None else generic_mol
        override = self.project.structure_image_path(self.compound)
        dpr = self.image.devicePixelRatioF()
        if override is not None and override.exists():
            pixmap = picture_pixmap(override, self.IMAGE_SIZE - QSize(10, 10), dpr)
            if not pixmap.isNull():
                self.image.setPixmap(pixmap)
            else:
                self.image.setText(f"(cannot preview {override.name})")
            source = self.project.structure_picture_source(self.compound)
            who = "ChemDraw's picture" if source == "chemdraw" else "your own picture"
            if override.suffix.lower() in drawing_scale.VECTOR_PICTURES:
                who += " (a vector drawing)"
            self.image_note.setText(
                f"Showing {who}. {picture_scale_text(self.project, self.compound)}")
            self.image_note.setWordWrap(True)
        elif shown is not None:
            self.image.setPixmap(mol_pixmap(shown, self.IMAGE_SIZE,
                                            drawing_settings(self.project), dpr))
            self.image_note.setText("Taken from the reaction scheme."
                                    if self.project.structure_from_scheme(self.compound) else "")
        elif generic:
            self.image.setPixmap(QPixmap())
            self.image.setText("(cannot draw this structure)")
            self.image_note.setText("")
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText("No structure yet.\n\nCopy it in ChemDraw, click here\n"
                               f"and press {paste_key()} — or type SMILES,\nopen a .mol/.sdf "
                               "file, or drop one here.")
            self.image_note.setText("")
        self.smiles.setText(chem.canonical_smiles(shown) if shown is not None else "")
        if info:
            charge = f" · net charge {info.charge:+d}" if info.charge else ""
            self.info.setText(
                f"<b>Formula</b> {formula_html(info.formula)} &nbsp; "
                f"<b>MW</b> {info.mw:.2f} &nbsp; <b>Exact mass</b> {info.exact_mass:.4f}{charge}")
        elif problem:
            self.info.setText(f"<i>{html_escape(problem)}</i>")
        elif override is not None:
            self.info.setText("<i>A picture only, no structure: paste or type the structure "
                              "to get the formula and masses.</i>")
        else:
            self.info.setText("")
        has_override = override is not None
        if not self.project.read_only:
            self.buttons["Clear"].setEnabled(mol is not None or bool(generic))
            self.buttons["Copy SMILES"].setEnabled(shown is not None)
            self.buttons["Remove picture"].setEnabled(has_override)
        self._apply_mode()

    # ---- compact ("saved") mode

    def has_content(self) -> bool:
        # (a structure with a generic atom (R) counts: it is stored and drawn)
        return (self.project.structure_for_drawing(self.compound) is not None
                or self.project.structure_image_path(self.compound) is not None)

    def set_editing(self, editing: bool) -> None:
        self._editing = editing
        self._apply_mode()

    def is_compact(self) -> bool:
        """One line (formula, masses, Edit or Remove menu) instead of the whole editor."""
        return self.has_content() and not self._editing and not self.project.read_only

    def _apply_mode(self) -> None:
        """With a structure (or picture): compact, one line — formula, MW and the Edit or
        Remove menu holding the buttons; the structure itself shows in the page header
        (SPEC §13n.7a). Without one, or while editing ("Show all editing controls"): the
        picture, the SMILES box and all buttons."""
        compact = self.is_compact()
        self.picture_box.setVisible(not compact)
        self.smiles.setVisible(not compact)
        self.controls.setVisible(not compact and not self.project.read_only)
        self.edit_btn.setVisible(compact)
        self.done_btn.setVisible(self._editing and self.has_content())
        self.smiles.setReadOnly(compact or self.project.read_only)
        self.set_paste_locked(compact)

    def _expand_field(self) -> None:
        """The structure is gone: show the whole editor again, even if the field was
        collapsed (▸), so the next structure can be pasted straight away (a picture kept
        without a structure stays, with all the editing controls shown)."""
        self._editing = self.has_content()
        box = self.parentWidget()
        if getattr(box, "collapsed", False) and hasattr(box, "set_collapsed"):
            box.set_collapsed(False)
        self._apply_mode()

    def _fill_edit_menu(self, menu: QMenu) -> None:
        menu.clear()
        self.locked_menu(menu)

    def locked_menu(self, menu: QMenu) -> None:
        menu.addAction("Replace with the copied structure (MOL / SMILES)", self._paste)
        menu.addAction("Use the copied picture (image)", self._paste_image)
        menu.addAction("Open MOL/SDF file…", self._open_file)
        menu.addAction("Use own picture file…", self._choose_image)
        menu.addSeparator()
        menu.addAction("Copy SMILES", self._copy_smiles)
        menu.addSeparator()
        size = menu.addAction("Printed size of the picture…", self._picture_size)
        size.setEnabled(self.project.structure_image_path(self.compound) is not None)
        remove_pic = menu.addAction("Remove picture", self._remove_image)
        remove_pic.setEnabled(self.project.structure_image_path(self.compound) is not None)
        menu.addAction("Clear structure…", self._clear)
        menu.addSeparator()
        menu.addAction("Show all editing controls (type SMILES…)",
                       lambda: self.set_editing(True))

    def _show_error(self, message: str) -> None:
        self.error.setText(f"⚠ {message}")
        self.error.setVisible(bool(message))

    # ---- setting the structure

    def _undo_step(self, what: str):
        """One undo step for an action on the structure, e.g. "Change the structure of 2"."""
        shown = self.project.id_report().display_id(self.compound.id)
        return self.project.undo_step(f"{what} {shown}")

    def set_structure_text(self, text: str, picture: PastedPicture | None = None) -> None:
        if not self.saved_first():   # (on-screen edits saved before set_structure)
            return
        try:
            with self._undo_step("Change the structure of"):
                if picture is not None:
                    added = self.project.set_structure(self.compound, text, picture=picture.data,
                                                       vector=picture.source,
                                                       vector_ext=picture.source_ext,
                                                       picture_ext=picture.ext)
                else:
                    added = self.project.set_structure(self.compound, text)
        except chem.ChemError as e:
            self._show_error(f"{e} The structure was not changed.")
            return
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save structure", str(e))
            return
        self._show_error("")
        self._editing = False  # back to compact once a structure is in
        self.refresh()
        self.structure_changed.emit(added)

    def _apply_smiles(self) -> None:
        # Compared with the structure's SMILES (not its formula's: a structure with a generic
        # atom has none), so Enter on the unchanged box stores nothing again (which would
        # move a ChemDraw picture to the Trash).
        # A structure.mol that can't be read just now (an online-only OneDrive file, or one
        # open in another program) must not count as "no structure": nothing is stored then.
        text = self.smiles.text().strip()
        try:
            # (the molecule, or the one with a generic atom that read_structure gives as None)
            mol = self.project.structure_for_drawing(self.compound, strict=True)
        except OSError as e:
            self._show_error(f"The structure file can't be read just now ({e}); try again "
                             "in a moment. The structure was not changed.")
            return
        current = chem.canonical_smiles(mol) if mol is not None else ""
        if text and text != current:
            self.set_structure_text(text)

    def _paste(self) -> None:
        text = clipboard_structure_text()
        if not text.strip():
            self._show_error("The clipboard has no SMILES or MOL text.")
            return
        # ChemDraw copies a picture along with the structure: keep it for previews (and,
        # if chosen when compiling, for the SI).
        self.set_structure_text(text, picture=clipboard_picture_for(self.project))

    def paste_from_clipboard(self) -> None:
        """Ctrl+V: the copied structure if there is one (ChemDraw copies both the structure
        and a picture), otherwise a picture (e.g. a screenshot) as the image to use."""
        if clipboard_structure_text().strip():
            self._paste()
        else:
            self._paste_image()

    def _open_file(self) -> None:
        patterns = " ".join(f"*{e}" for e in STRUCTURE_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Open a structure file", "",
                                              f"Structure files ({patterns})")
        if path:
            self._read_file(Path(path))

    def _read_file(self, path: Path) -> None:
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as e:
            self._show_error(f"Could not read {path.name}: {e}")
            return
        self.set_structure_text(text)

    def _copy_smiles(self) -> None:
        if self.smiles.text():
            QGuiApplication.clipboard().setText(self.smiles.text())

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear structure",
                                "Remove this compound's structure? It will be moved to the "
                                "project Trash.") != QMessageBox.StandardButton.Yes:
            return
        if not self.saved_first():
            return
        try:
            with self._undo_step("Clear the structure of"):
                self.project.clear_structure(self.compound)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not clear structure", str(e))
        self.refresh()
        if self.project.read_structure(self.compound) is None:
            self._expand_field()
        self.structure_changed.emit([])

    def _paste_image(self) -> None:
        picture = clipboard_picture_for(self.project)
        if picture is None:
            self._show_error("The clipboard has no picture. Copy the structure as a picture "
                             f"first (in ChemDraw, select it and press {copy_key()}).")
            return
        if not self.saved_first():
            return
        try:
            with self._undo_step("Paste a picture of"):
                self.project.set_structure_image(self.compound, data=picture.data,
                                                 ext=picture.ext,
                                                 vector=picture.source,
                                                 vector_ext=picture.source_ext)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not use image", str(e))
        self._show_error("")
        self.refresh()
        self.structure_changed.emit([])

    def _picture_size(self) -> None:
        """The picture's bond length: measured, typed, or "its own size" (SPEC §13n.2a)."""
        if self.project.structure_image_path(self.compound) is None:
            return
        dialog = PictureScaleDialog(self, self.project, self.compound)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.set_picture_bond(dialog.choice())

    def set_picture_bond(self, own: float | None) -> None:
        """Store the choice of the Printed size dialog (one Edit ▸ Undo step)."""
        if own == self.project.structure_picture_bond(self.compound)[1]:
            return
        if not self.saved_first():
            return
        try:
            with self._undo_step("Change the printed size of the picture of"):
                self.project.set_structure_picture_bond(self.compound, own)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not change the printed size", str(e))
        self.refresh()
        self.structure_changed.emit([])

    def _choose_image(self) -> None:
        patterns = " ".join(f"*{e}" for e in PICTURE_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a structure image", "",
                                              f"Images ({patterns})")
        if not path:
            return
        if not self.saved_first():
            return
        try:
            with self._undo_step("Use a picture file for"):
                self.project.set_structure_image(self.compound, Path(path))
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not use image", str(e))
        self.refresh()
        self.structure_changed.emit([])

    def _remove_image(self) -> None:
        if not self.saved_first():
            return
        try:
            with self._undo_step("Remove the picture of"):
                self.project.remove_structure_image(self.compound)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove image", str(e))
        self.refresh()
        if not self.has_content():
            self._expand_field()
        self.structure_changed.emit([])

    def store(self) -> None:
        pass  # the structure is saved the moment it changes

    # ---- drag and drop of .mol/.sdf files

    def _dropped(self, event) -> Path | None:
        if self.project.read_only or not event.mimeData().hasUrls():
            return None
        urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
        if len(urls) == 1 and Path(urls[0].toLocalFile()).suffix.lower() in STRUCTURE_FILE_EXTENSIONS:
            return Path(urls[0].toLocalFile())
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._dropped(event)
        if path:
            event.acceptProposedAction()
            self._read_file(path)


def picture_scale_text(project, compound) -> str:
    """One line on how the structure picture prints (SPEC §13n.2a)."""
    if project.structure_image_path(compound) is None:
        return ""
    settings = project.settings
    measured, own = project.structure_picture_bond(compound)
    target = drawing_scale.target_bond_pt(settings)
    if not drawing_scale.same_scale(settings):
        return "It prints at its own size (same bond length is off in Project settings)."
    if own == 0:
        return "It prints at its own size (Edit or Remove ▸ Printed size of the picture)."
    bond = own if own else measured
    if bond is None:
        return "No bonds could be measured in it, so it prints at its own size."
    factor = drawing_scale.print_factor(settings, bond, typed=bool(own))
    if not own and factor == 1.0 and abs(bond - target) > 0.01 * target:
        return (f"Its bonds measure {bond:.1f} pt, too far from {target:.1f} pt to rescale: "
                "it prints at its own size.")
    typed = " (typed)" if own else ""
    return (f"Its bonds are {bond:.1f} pt{typed}: printed at {factor:.0%} so they are "
            f"{target:.1f} pt, like every structure.")


class PictureScaleDialog(QDialog):
    """Edit or Remove ▸ Printed size of the picture: use the measured bond length (the
    default), type the picture's bond length, or print it at its own size (§2.7)."""

    def __init__(self, parent: QWidget | None, project, compound, bond=None):
        """`bond` = (measured, own) for a picture that is not a compound's own (a scheme
        editor's box, SPEC §13n.4); else the compound's are used."""
        super().__init__(parent)
        self.setWindowTitle("Printed size of the picture")
        measured, own = bond if bond is not None else project.structure_picture_bond(compound)
        target = drawing_scale.target_bond_pt(project.settings)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Pasted structures are printed so that their bonds are {target:.1f} pt "
            f"({target / 72 * 2.54:.3f} cm) long, the bond length in Project settings ▸ "
            "Structures, so every structure in the SI has the same scale.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.auto = QRadioButton(
            f"Automatic: bonds measured at {measured:.1f} pt" if measured else
            "Automatic: no bonds could be measured, so it prints at its own size")
        self.typed = QRadioButton("The bonds in this picture are")
        self.bond = QDoubleSpinBox()
        self.bond.setRange(0.5, 200.0)
        self.bond.setDecimals(2)
        self.bond.setSuffix(" pt")
        self.bond.setValue(own if own else (measured or target))
        self.natural = QRadioButton("Print it at its own size (as copied)")
        group = QButtonGroup(self)
        for button in (self.auto, self.typed, self.natural):
            group.addButton(button)
        (self.natural if own == 0 else self.typed if own else self.auto).setChecked(True)
        layout.addWidget(self.auto)
        row = QHBoxLayout()
        row.addWidget(self.typed)
        row.addWidget(self.bond)
        row.addWidget(QLabel("long"))
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.natural)
        # A typed length is always used (§2.7), but one far from the drawing's bonds is
        # probably a typing slip: say how big the picture will print.
        self.extreme = QLabel()
        self.extreme.setWordWrap(True)
        self.extreme.setStyleSheet("color: #b36b00;")
        layout.addWidget(self.extreme)
        self._target = target
        self.bond.valueChanged.connect(lambda _v: self.typed.setChecked(True))
        self.bond.valueChanged.connect(lambda _v: self._check_extreme())
        self.typed.toggled.connect(lambda _on: self._check_extreme())
        self._check_extreme()
        if not drawing_scale.same_scale(project.settings):
            off = QLabel("“Print all pasted structures at the same bond length” is off in "
                         "Project settings ▸ Structures: every picture prints at its own size "
                         "until it is turned on again.")
            off.setWordWrap(True)
            off.setStyleSheet("color: #b36b00;")
            layout.addWidget(off)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check_extreme(self) -> None:
        factor = self._target / self.bond.value() if self.bond.value() > 0 else 1.0
        lo, hi = drawing_scale.FACTOR_RANGE
        extreme = self.typed.isChecked() and not lo <= factor <= hi
        self.extreme.setText(
            f"⚠ With bonds of {self.bond.value():.2f} pt the picture prints at {factor:.0%} "
            "of its own size. Check the length you typed (it is used as typed)."
            if extreme else "")
        self.extreme.setVisible(extreme)

    def choice(self) -> float | None:
        """None: the measured length; 0: its own size; else the typed length (pt)."""
        if self.natural.isChecked():
            return 0.0
        if self.typed.isChecked():
            return round(self.bond.value(), 3)
        return None


# ---------------------------------------------------------------------- HRMS field

class HrmsEditor(PartsEditor):
    """HRMS inputs plus the calculated m/z and ppm error (SPEC §7.1 field 8, §8.1).

    M is normally the structure. For a salt, the user picks which piece (e.g. the
    cation) is M. Typing an M formula overrides both; the adduct is still applied.

    The instrument's report files are listed under it (SPEC §13n.3, ui/hrms_reports_ui).
    The found m/z is filled from them when the Found box is empty or still holds the value
    filled from a report before; a value typed by hand is never changed. This is worked
    out again when the page opens, the structure, adduct, M or the files change.
    """

    files_changed = Signal()   # a report file was added or removed (saved already)
    status = Signal(str)       # a line for the status bar

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stored = self.compound.values.get(self.defn.key)
        stored = stored if isinstance(stored, dict) else {}   # (a hand-edited compound.json)
        source = stored.get(ms_reports.FOUND_FROM_KEY)
        self._found_from = dict(source) if isinstance(source, dict) and \
            ms_reports.found_is_from_report(stored) else None
        self._last_found = get_part_value(self.widgets["found"])
        adduct_box = self.widgets["adduct"]
        for i in range(adduct_box.count()):
            adduct_box.setItemText(i, chem.adduct_display(adduct_box.itemData(i)))
        value = stored
        set_part_value(adduct_box, F.Part("adduct"), F.part_text(value.get("adduct", "[M+H]+")))
        adduct_box.setToolTip("Choose an adduct, or type your own, e.g. [M+Li]+, [2M+Na]+, "
                              "[M+H−H2O]+, [M+2H]2+")
        grid = self.layout()

        # Salt: which piece is M (hidden for ordinary structures).
        self.species_label = QLabel("M is:")
        self.species_box = QComboBox()
        self.species_box.setToolTip("This structure has more than one piece (e.g. a salt). "
                                    "Choose which one the m/z is calculated for.")
        self._species_key = F.part_text(value.get("species", ""))
        self.species_box.activated.connect(self._species_chosen)

        # Manual M formula.
        self.formula_edit = QLineEdit(F.part_text(value.get("formula", "")))
        self.formula_edit.setMaximumWidth(220)
        self.formula_edit.textEdited.connect(lambda *_: self.changed.emit())
        self.formula_preview = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.species_label)
        row.addWidget(self.species_box)
        row.addSpacing(12)
        row.addWidget(QLabel("M formula:"))
        row.addWidget(self.formula_edit)
        row.addWidget(self.formula_preview, 1)
        grid.addLayout(row, grid.rowCount(), 0, 1, -1)

        self.calc = QLabel()
        self.calc.setWordWrap(True)
        self.calc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.calc, grid.rowCount(), 0, 1, -1)
        # "from the report …" under the Found box (SPEC §13n.3a)
        self.found_note = QLabel()
        self.found_note.setWordWrap(True)
        self.found_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.found_note, grid.rowCount(), 0, 1, -1)
        from .hrms_reports_ui import HrmsReportsBox
        self.reports = HrmsReportsBox(self.project, self.compound, self.before_write,
                                      self.shown_id)
        self.reports.files_changed.connect(self._files_changed)
        self.reports.saved.connect(self.status)
        grid.addWidget(self.reports, grid.rowCount(), 0, 1, -1)
        self.changed.connect(self.refresh_calculation)
        self.refresh_calculation(opening=True)

    def store(self) -> None:
        old = self.compound.values.get(self.defn.key)
        old = old if isinstance(old, dict) else {}
        super().store()
        value = self.compound.values[self.defn.key]
        value["species"] = self._species_key
        value["formula"] = self.formula_edit.text().strip()
        # Kept from what is stored: the report files and the printing choice (saved by
        # their own actions), and where a found m/z filled from a report came from.
        for key in (ms_reports.REPORTS_KEY, ms_reports.PRINT_KEY):
            if key in old:
                value[key] = old[key]
        if self._found_from is not None:
            value[ms_reports.FOUND_FROM_KEY] = dict(self._found_from)
            if not ms_reports.found_is_from_report(value):
                value.pop(ms_reports.FOUND_FROM_KEY, None)

    # ---- report files (SPEC §13n.3)

    def _files_changed(self) -> None:
        """A report file was added or removed: the project has saved the field (and filled
        or cleared the found m/z); show what it holds now."""
        self._show_stored_found()
        self.refresh_calculation()
        self.files_changed.emit()

    def structure_changed(self) -> None:
        """The structure was changed (or cleared) and saved, with the found m/z filled or
        cleared from the reports in the same step: show what is stored, so the page does
        not fill it again as an edit (and an undo step) of its own. (Typing on the page
        was saved before the structure was changed.)"""
        self._show_stored_found()
        self.refresh_calculation()

    def _show_stored_found(self) -> None:
        value = self.compound.values.get(self.defn.key)
        value = value if isinstance(value, dict) else {}
        found = F.part_text(value.get("found", ""))
        set_part_value(self.widgets["found"], F.Part("found"), found)
        self._last_found = get_part_value(self.widgets["found"])
        source = value.get(ms_reports.FOUND_FROM_KEY)
        self._found_from = dict(source) if isinstance(source, dict) and \
            ms_reports.found_is_from_report(value) else None

    def _value_on_screen(self) -> dict:
        stored = self.compound.values.get(self.defn.key)
        value = dict(stored) if isinstance(stored, dict) else {}
        value.update({k: get_part_value(w) for k, w in self.widgets.items()})
        value["species"] = self._species_key
        value["formula"] = self.formula_edit.text().strip()
        value.pop(ms_reports.FOUND_FROM_KEY, None)
        if self._found_from is not None:
            value[ms_reports.FOUND_FROM_KEY] = dict(self._found_from)
        return value

    def _auto_fill(self) -> bool:
        """Fill the Found box from the reports (see the class). True if it changed."""
        if self.project.read_only or not ms_reports.report_items(self.compound):
            return False
        value = self._value_on_screen()
        if not ms_reports.update_found(self.project, self.compound, value):
            return False
        set_part_value(self.widgets["found"], F.Part("found"), str(value.get("found", "")))
        self._last_found = get_part_value(self.widgets["found"])
        source = value.get(ms_reports.FOUND_FROM_KEY)
        self._found_from = dict(source) if isinstance(source, dict) else None
        return True

    def _show_found_note(self) -> None:
        if not ms_reports.report_items(self.compound):
            self.found_note.setText("")
            self.found_note.setVisible(False)
            return
        text = ""
        if self._found_from is not None:
            text = (f"<span style='color:#888'>↳ Found m/z from the report "
                    f"{html_escape(str(self._found_from.get('file', '')))} — type over it to "
                    f"use your own value.</span>")
        else:
            value = self._value_on_screen()
            match = ms_reports.compound_match(self.project, self.compound, value)
            found = checks.parse_number(str(value.get("found", "")))
            tol = ms_reports.tolerance(self.project)
            if match is None:
                text = ("<span style='color:#888'>No m/z within the tolerance (±"
                        f"{tol:g} ppm) of the expected ion was found in the report files."
                        "</span>")
            elif found is None:
                text = (f"<span style='color:#888'>The reports give {match.mz:.4f} "
                        f"({html_escape(match.file)}).</span>")
            else:
                ppm = (found - match.mz) / match.mz * 1e6
                if abs(ppm) > tol:
                    # (how far apart they are is said once: in the field's warning)
                    text = (f"<span style='color:#888'>Your value is kept; the report "
                            f"{html_escape(match.file)} gives {match.mz:.4f}.</span>")
                else:
                    text = (f"<span style='color:#888'>Your value; the report "
                            f"{html_escape(match.file)} gives {match.mz:.4f}.</span>")
        self.found_note.setText(text)
        self.found_note.setVisible(bool(text))

    def _species_chosen(self, index: int) -> None:
        self._species_key = self.species_box.itemData(index) or ""
        # A piece that is already charged (e.g. a pyridinium cation) is normally
        # reported as [M]+: fill that in (the adduct can still be changed afterwards).
        mol = self.project.read_structure(self.compound)
        sp = checks.hrms_species(mol, {"species": self._species_key})
        suggestion = checks.suggested_adduct(sp.info) if sp else None
        if suggestion:
            set_part_value(self.widgets["adduct"], F.Part("adduct"), suggestion)
        self.changed.emit()

    def _current_value(self) -> dict:
        return {"species": self._species_key, "formula": self.formula_edit.text()}

    def _fill_species(self, sp) -> None:
        choices = sp.choices if sp and sp.choices else None
        self.species_label.setVisible(bool(choices))
        self.species_box.setVisible(bool(choices))
        if not choices:
            return
        self.species_box.blockSignals(True)
        self.species_box.clear()
        auto = sp.automatic or choices[1]
        self.species_box.addItem(f"Automatic ({formula_plain(auto[1])})", "")
        self.species_box.setItemData(
            0, "The largest piece that is not a common counter-ion (halide, BF4⁻, PF6⁻, "
               "OTf⁻, NTf2⁻, TFA⁻, BArF⁻, Na⁺, K⁺, NBu4⁺…); the largest piece if all are.",
            Qt.ItemDataRole.ToolTipRole)
        for key, label, _info in choices:
            self.species_box.addItem(formula_plain(label), key)
        index = self.species_box.findData(self._species_key)
        self.species_box.setCurrentIndex(max(index, 0))
        self.species_box.blockSignals(False)

    def refresh_calculation(self, opening: bool = False) -> None:
        # The found m/z: typed by hand (it is the user's own from now on), or filled from
        # the reports when something else changed (SPEC §13n.3a).
        found_now = get_part_value(self.widgets["found"])
        if found_now != self._last_found:
            self._last_found = found_now
            if self._found_from is not None and \
                    found_now != ms_reports.format_mz(self._found_from.get("mz", 0)):
                self._found_from = None
        elif self._auto_fill() and not self.project.read_only:
            if opening:   # (saved once the page is ready, as an edit of its own)
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self.changed.emit)
            else:
                self.changed.emit()
                return   # (refresh_calculation runs again for the new value)
        self._show_found_note()
        mol = self.project.read_structure(self.compound)
        auto = None
        try:
            auto = checks.hrms_species(mol, {"species": self._species_key})
        except chem.ChemError:
            pass
        self._fill_species(auto)
        manual = bool(self.formula_edit.text().strip())
        auto_formula = species_text = ""
        auto_counts = None
        if auto is not None:
            auto_formula = auto.info.formula
            auto_counts = auto.info.counts
            species_text = checks.species_label(auto.info)
        self.formula_edit.setPlaceholderText(f"auto: {formula_plain(species_text)}"
                                             if species_text else "e.g. C12H13NO")
        self.formula_edit.setToolTip(
            "Type M's formula to override the one from the structure (the adduct is still "
            "applied). Leave empty to use the structure." +
            (f"\nFrom the structure: {species_text}" if species_text else ""))
        self.formula_edit.setStyleSheet(self.MANUAL_STYLE if manual else "")

        try:
            sp = checks.hrms_species(mol, self._current_value())
        except chem.ChemError as e:
            self.formula_preview.setText(f"<span style='color:#d35400'>⚠ {e}</span>")
            self.calc.setText("")
            return
        if manual:
            # (Only for a different composition: a typed C8H5[2H]3O2 for a CD3 structure
            # is the same M, written another way.)
            differs = (f" (structure gives {formula_html(auto_formula)})"
                       if auto_counts is not None
                       and not chem.same_formula(auto_counts, sp.info.counts) else "")
            self.formula_preview.setText(f"✎ <b>manual</b>: {formula_html(sp.info.formula)}"
                                         f"{differs}")
        else:
            self.formula_preview.setText("")
        adduct = get_part_value(self.widgets["adduct"]) or "[M+H]+"
        if sp is None:
            generic = self.project.generic_structure_atoms(self.compound)
            self.calc.setText(
                f"<i>The structure has a generic atom ({html_escape(', '.join(generic))}): "
                "type M's formula above to calculate the expected m/z.</i>" if generic else
                "<i>Add a structure (or type an M formula) to calculate the expected "
                "m/z.</i>")
            return
        try:
            r = checks.hrms_calc(sp.info, adduct, get_part_value(self.widgets["found"]),
                                 ms_reports.tolerance(self.project))
        except chem.ChemError as e:
            self.calc.setText(f"<span style='color:#d35400'>⚠ {e}</span>")
            return
        display = chem.adduct_display(adduct)
        shown = [p for p in r.peaks if p.abundance >= 5.0][:6]
        pattern = ", ".join(f"{p.mz:.4f} ({p.abundance:.1f}%)" for p in shown)
        mono = r.monoisotopic
        star = " ✎" if manual else ""
        text = (f"<b>Calcd</b> for {formula_html(mono.formula)}{star} {display}: "
                f"<b>{mono.mz:.4f}</b>"
                f"<br><span style='color:#888'>Isotope pattern (m/z): {pattern}</span>")
        hint = checks.charged_adduct_hint(sp.info, adduct)
        if hint:
            text += f"<br><span style='color:#d35400'>⚠ {hint}</span>"
        if r.found is not None:
            ppm_text = f"Δ = {r.ppm:+.1f} ppm"
            if r.out_of_tolerance:
                ppm_text = (f"<span style='color:#d35400'>⚠ {ppm_text} — outside your "
                            f"±{r.tolerance:g} ppm tolerance (Org. Lett. allows ±10 ppm)</span>")
            else:
                ppm_text = f"✔ {ppm_text}"
            peak = ""
            if r.matched is not mono:
                peak = (f" (compared with the {r.matched.mz:.4f} peak, "
                        f"{formula_html(r.matched.formula)})")
            text += f"<br>Found {r.found:.4f}{peak} &nbsp;·&nbsp; {ppm_text}"
        self.calc.setText(text)
