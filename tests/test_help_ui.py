"""Help ▸ User guide (ui/help_ui.py): the guide shipped with the program, its headings,
links between them, search, and the GitHub page when the guide is missing."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl

from si_assistant import resources
from si_assistant.ui import help_ui

GUIDE = """# The guide

Intro with a [link to the spectra part](#spectra--the-spectra-section) and
[a web page](https://example.com/page).

## Getting started

Some text.

```
# not a heading (code)
```

### First project

More text about **pasting** structures.

## Spectra & the Spectra section

![A picture](images/pic.png)

Spectra text.
"""


@pytest.fixture
def guide(tmp_path):
    (tmp_path / "images").mkdir()
    from PySide6.QtGui import QColor, QImage
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    image.save(str(tmp_path / "images" / "pic.png"))
    path = tmp_path / "user-guide.md"
    path.write_text(GUIDE, encoding="utf-8")
    return path


def test_headings_and_github_anchors():
    assert help_ui.headings(GUIDE) == [(1, "The guide"), (2, "Getting started"),
                                       (3, "First project"),
                                       (2, "Spectra & the Spectra section")]
    assert help_ui.slug("Spectra & the Spectra section") == "spectra--the-spectra-section"
    assert help_ui.slug("Compile, preview and **check**") == "compile-preview-and-check"
    assert help_ui.slug("Where your data lives (backups)") == "where-your-data-lives-backups"


def test_the_shipped_guide_has_working_headings_links_and_pictures():
    path = resources.user_guide()
    text = path.read_text(encoding="utf-8")
    anchors = {help_ui.slug(h) for _level, h in help_ui.headings(text, deepest=6)}
    import re
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, f"the link #{target} goes nowhere"
    for picture in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        file = path.parent / picture
        assert file.is_file(), picture
        assert file.suffix == ".png" and file.stat().st_size <= 160_000, picture


def test_guide_window(qapp, guide):
    opened = []
    window = help_ui.UserGuideWindow(guide, opener=opened.append)
    assert window.toc.count() == 4
    assert "pasting" in window.browser.toPlainText()
    # A heading in the list scrolls there.
    assert window.go_to("First project")
    assert window.browser.textCursor().block().text() == "First project"
    assert not window.go_to("No such heading")
    # A link to a heading jumps there; a web link opens in the browser.
    window.follow_link(QUrl("#spectra--the-spectra-section"))
    assert window.browser.textCursor().block().text() == "Spectra & the Spectra section"
    window.follow_link(QUrl("https://example.com/page"))
    assert opened == ["https://example.com/page"]
    # The picture is found next to the guide.
    doc = window.browser.document()
    image = doc.resource(doc.ResourceType.ImageResource, QUrl("images/pic.png"))
    assert image is not None
    kind = doc.ResourceType.ImageResource
    small = window.browser.loadResource(kind, QUrl("images/pic.png"))
    assert (small.width(), small.height()) == (20, 10)            # never enlarged
    from PySide6.QtGui import QImage
    QImage(3000, 1000, QImage.Format.Format_RGB32).save(str(guide.parent / "images" / "big.png"))
    big = window.browser.loadResource(kind, QUrl("images/big.png"))
    shown = big.width() / big.devicePixelRatio()
    assert abs(shown - window.browser.room()) <= 1 and shown < 3000    # shrunk to the pane
    absolute = window.browser.loadResource(kind, QUrl.fromLocalFile(str(guide.parent / "images" / "pic.png")))
    assert absolute.width() == 20
    # Search, wrapping round to the top.
    window.search.setText("spectra text")
    assert window.find_next() and window.find_next()
    window.search.setText("nothing like this")
    assert not window.find_next() and window.not_found.text() == "Not found"
    window.close()


def test_without_the_guide_the_github_page_opens(qapp, tmp_path):
    opened = []
    assert help_ui.open_user_guide(None, opened.append, tmp_path / "missing.md") is None
    assert opened == [help_ui.GUIDE_URL]
    assert help_ui.GUIDE_URL.endswith("/blob/main/docs/user-guide.md")


def test_help_menu_has_the_user_guide(qapp, tmp_path, monkeypatch, guide):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(resources, "user_guide", lambda: guide)
    win = mw.MainWindow()
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    assert help_menu.actions()[0] is win.act_user_guide
    assert win.act_user_guide.text() == "User guide"
    assert win.act_user_guide.shortcut().toString() == "F1"
    win.act_user_guide.trigger()
    first = win.guide_window
    assert first is not None and first.isVisible() and first.path == guide
    win.act_user_guide.trigger()             # already open: the same window
    assert win.guide_window is first
    first.close()
    win.close()


def test_guide_path_is_next_to_the_package():
    assert resources.user_guide() == Path(resources.bundle_root()) / "docs" / "user-guide.md"
