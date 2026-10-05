"""Help ▸ User guide (SPEC §13m, Phase 8).

The guide is docs/user-guide.md, shipped with the program (resources.user_guide()). It is
shown in a window of its own: the list of its headings on the left, the text on the right
(Qt shows Markdown directly, pictures included). Links to its own headings
("#getting-started", written as on GitHub) jump there; web links open in the browser. If
the guide is missing (e.g. a partial copy of the source code), its GitHub page opens
instead.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from .. import APP_NAME, resources, updates

GUIDE_URL = f"{updates.REPO_URL}/blob/main/docs/user-guide.md"
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def plain(text: str) -> str:
    """A heading's text without Markdown marks (**bold**, `code`, [link](…))."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[*_`]", "", text).strip()


def slug(text: str) -> str:
    """The anchor GitHub gives a heading: "Spectra & the Spectra section" →
    "spectra--the-spectra-section" (lower case; letters, digits, spaces → "-", "-", "_"
    kept; everything else dropped)."""
    text = plain(text).lower()
    kept = "".join(c for c in text if c in " -_" or unicodedata.category(c)[0] in "LN")
    return kept.replace(" ", "-")


def headings(markdown: str, deepest: int = 3) -> list[tuple[int, str]]:
    """(level, text) of the guide's headings down to `deepest`, in order (not those
    inside code blocks)."""
    found, fenced = [], False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if m and len(m.group(1)) <= deepest:
            found.append((len(m.group(1)), plain(m.group(2))))
    return found


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class GuideBrowser(QTextBrowser):
    """The guide's text. Pictures are shrunk to the width of the pane (never enlarged):
    Qt's Markdown view would show them at full size. When the pane is resized, the text is
    laid out again with the pictures at the new size (the reading position is kept)."""

    def __init__(self):
        super().__init__()
        self.markdown = ""
        self._laid_out_for = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._refit)

    def room(self) -> int:
        """The width a picture may have (logical pixels)."""
        return max(120, self.viewport().width() - 2 * int(self.document().documentMargin()) - 8)

    def show_markdown(self, markdown: str) -> None:
        self.markdown = markdown
        self._laid_out_for = self.room()
        self.setMarkdown(markdown)

    def loadResource(self, kind: int, url: QUrl):   # noqa: N802 (Qt's name)
        if kind == QTextDocument.ResourceType.ImageResource.value or \
                kind == QTextDocument.ResourceType.ImageResource:
            files = [Path(url.toLocalFile())] if url.isLocalFile() else \
                [Path(folder) / url.toString() for folder in self.searchPaths()]
            image = QImage()
            for file in files:
                image = QImage(str(file))
                if not image.isNull():
                    break
            if not image.isNull():
                room = self.room()
                if image.width() > room:   # (sharp on high-resolution screens too)
                    ratio = max(1.0, self.devicePixelRatioF())
                    image = image.scaledToWidth(round(room * ratio),
                                                Qt.TransformationMode.SmoothTransformation)
                    image.setDevicePixelRatio(ratio)
                return image
        return super().loadResource(kind, url)

    def resizeEvent(self, event) -> None:   # noqa: N802 (Qt's name)
        super().resizeEvent(event)
        if self.markdown and abs(self.room() - self._laid_out_for) > 30:
            self._timer.start()

    def _refit(self) -> None:
        bar = self.verticalScrollBar()
        where = bar.value() / bar.maximum() if bar.maximum() else 0.0
        self.show_markdown(self.markdown)
        bar.setValue(round(where * bar.maximum()))

class UserGuideWindow(QWidget):
    """The user guide: headings on the left, the text on the right, and a search box."""

    def __init__(self, path: Path, parent: QWidget | None = None,
                 opener: Callable[[str], None] = open_url):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} — User guide")
        self.path = path
        self.opener = opener
        markdown = path.read_text(encoding="utf-8")
        self.contents = headings(markdown)

        self.browser = GuideBrowser()
        self.browser.setOpenLinks(False)          # (handled in follow_link)
        self.browser.anchorClicked.connect(self.follow_link)
        self.browser.setSearchPaths([str(path.parent)])
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(path)))
        self.browser.show_markdown(markdown)

        self.toc = QListWidget()
        for level, text in self.contents:
            item = QListWidgetItem(("    " * (level - 2) if level > 1 else "") + text)
            item.setData(Qt.ItemDataRole.UserRole, text)
            if level == 1:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.toc.addItem(item)
        self.toc.itemClicked.connect(
            lambda item: self.go_to(item.data(Qt.ItemDataRole.UserRole)))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the guide…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.find_next)
        find = QPushButton("Find")
        find.clicked.connect(self.find_next)
        self.not_found = QLabel("")
        online = QLabel(f'<a href="{GUIDE_URL}">Open on GitHub</a>')
        online.setOpenExternalLinks(True)

        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(find)
        bar.addWidget(self.not_found)
        bar.addStretch(0)
        bar.addWidget(online)
        split = QSplitter()
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 720])
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(split, 1)
        self.resize(980, 720)

    def heading_block(self, text: str):
        """The first heading of the text whose words are `text` (None if there is none)."""
        block = self.browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and block.text().strip() == text:
                return block
            block = block.next()
        return None

    def go_to(self, text: str) -> bool:
        """Scroll so the heading `text` is at the top. True if it was found."""
        block = self.heading_block(text)
        if block is None:
            return False
        cursor = QTextCursor(block)
        bar = self.browser.verticalScrollBar()
        bar.setValue(bar.maximum())             # (so the heading can come to the top)
        self.browser.setTextCursor(cursor)
        self.browser.ensureCursorVisible()
        return True

    def follow_link(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("#") or (url.hasFragment() and url.isLocalFile()
                                    and Path(url.toLocalFile()) == self.path):
            wanted = url.fragment()
            for _level, heading in self.contents:
                if slug(heading) == wanted:
                    self.go_to(heading)
                    return
            return
        self.opener(text)

    def find_next(self) -> bool:
        words = self.search.text().strip()
        if not words:
            return False
        found = self.browser.find(words)
        if not found:   # from the top again
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
            found = self.browser.find(words)
        self.not_found.setText("" if found else "Not found")
        return found


def open_user_guide(parent: QWidget | None, opener: Callable[[str], None] = open_url,
                    path: Path | None = None) -> UserGuideWindow | None:
    """Show the guide shipped with the program; without it, open its GitHub page."""
    path = resources.user_guide() if path is None else path
    if not path.is_file():
        opener(GUIDE_URL)
        return None
    window = UserGuideWindow(path, parent, opener)
    window.show()
    return window
