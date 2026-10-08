"""The Manuscript check page and its place in the tree and the Warnings panel (SPEC §13o).
All files are made up (tests/manuscript_samples.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from si_assistant.core import manuscript as M

sys.path.insert(0, str(Path(__file__).parent))
import manuscript_samples as S  # noqa: E402


@pytest.fixture
def si(tmp_path):
    p, compounds = S.build_si(tmp_path / "si")
    S.make_pdf(tmp_path / "ms.pdf")
    S.make_cdxml(tmp_path / "scheme.cdxml")
    yield p, compounds, tmp_path
    if p.lock is not None:
        p.close()


def _texts(tree):
    out = []
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        out.append(it)
        stack.extend(it.child(j) for j in range(it.childCount()))
    return out


def test_page_adds_checks_filters_dismisses_and_removes(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, c, tmp = si
    p.enable_undo()
    page = U.ManuscriptPage(p, auto=False)
    assert page.summary.text() == "No manuscript added yet."
    monkeypatch.setattr(U.QMessageBox, "information", lambda *a, **k: None)
    page.add_files("manuscript", [str(tmp / "ms.pdf")])
    page.add_files("chemdraw", [str(tmp / "scheme.cdxml")])
    assert [f.name for f in M.files(p)] == ["ms.pdf", "scheme.cdxml"]
    assert p.can_undo
    assert page.summary.text().startswith("6 compounds checked:")
    problems = page.table.rowCount()
    assert problems == len(page.result.problems) == 7
    labels = {(page.table.item(r, 0).text(), page.table.item(r, 1).text())
              for r in range(problems)}
    assert ("3b", "Yield") in labels and ("3i", "ID") in labels
    # All results: the agreements too.
    page.filter.setCurrentIndex(2)
    assert page.table.rowCount() > problems
    assert any(page.table.item(r, 5).text() == "✓ Agrees" for r in range(page.table.rowCount()))
    page.filter.setCurrentIndex(0)
    # Dismiss the yield difference (with a reason), then restore it.
    row = next(r for r in range(page.table.rowCount())
               if (page.table.item(r, 0).text(), page.table.item(r, 1).text()) == ("3b", "Yield"))
    page.table.selectRow(row)
    assert page.dismiss_btn.isEnabled() and page.open_btn.isEnabled()
    changed = []
    page.changed.connect(lambda: changed.append(1))
    page.dismiss_selected("known: re-weighed")
    assert page.table.rowCount() == problems - 1 and changed
    assert M.dismissed(p)[next(iter(M.dismissed(p)))]["reason"] == "known: re-weighed"
    assert page.summary.text().endswith("(1 dismissed)")
    # Dismissed results are hidden in every filter until "Show dismissed" is ticked.
    for k in range(3):
        page.filter.setCurrentIndex(k)
        assert not any(page.table.item(r, 5).text() == "Dismissed"
                       for r in range(page.table.rowCount()))
    page.filter.setCurrentIndex(0)
    page.show_dismissed.setChecked(True)
    row = next(r for r in range(page.table.rowCount())
               if page.table.item(r, 5).text() == "Dismissed")
    page.table.selectRow(row)
    assert page.restore_btn.isEnabled()
    page.restore_selected()
    assert M.dismissed(p) == {}
    page.show_dismissed.setChecked(False)
    # Open compound: emits the compound and its field.
    page.filter.setCurrentIndex(0)
    opened = []
    page.open_compound.connect(lambda cid, field: opened.append((cid, field)))
    row = next(r for r in range(page.table.rowCount())
               if (page.table.item(r, 0).text(), page.table.item(r, 1).text()) == ("3b", "Yield"))
    page.table.selectRow(row)
    page._open_selected()
    qapp.processEvents()
    assert opened == [(c["3b"].id, "yield")]
    # Remove a file: to the Trash.
    monkeypatch.setattr(U.QMessageBox, "question",
                        lambda *a, **k: U.QMessageBox.StandardButton.Yes)
    page.files.setCurrentRow(1)
    page.remove_selected()
    assert [f.name for f in M.files(p)] == ["ms.pdf"]
    assert any(e.kind == "manuscript_file" for e in p.list_trash())
    assert not any(f.kind == "structure" for f in page.result.findings)


def test_cdx_file_gets_a_note(qapp, si, monkeypatch):
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    told = []
    monkeypatch.setattr(U.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    (tmp / "old.cdx").write_bytes(b"VjCD0100")
    page = U.ManuscriptPage(p, auto=False)
    page.add_files("chemdraw", [str(tmp / "old.cdx")])
    assert any("CDXML" in t for t in told)
    assert any(".cdx" in n and "Save As" in n for n in page.result.notes)
    assert page.files.item(0).text().startswith("⚠")


def test_read_only_page(qapp, si):
    from si_assistant.core.project import Project
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    M.add_file(p, tmp / "ms.pdf")
    p.close()
    ro = Project.open(p.folder, read_only=True)
    page = U.ManuscriptPage(ro, auto=False)
    page.run_check()
    assert page.table.rowCount() > 0
    assert not page.add_ms_btn.isEnabled() and not page.dismiss_btn.isEnabled()
    assert not (M.folder(ro) / M.CACHE_FILE).exists()    # nothing written


@pytest.fixture
def window(qapp, si, monkeypatch):
    from si_assistant.ui import main_window as mw
    p, c, tmp = si
    monkeypatch.setattr(mw, "QSettings",
                        lambda *a: QSettings(str(tmp / "prefs.ini"), QSettings.Format.IniFormat))
    M.add_file(p, tmp / "ms.pdf")
    M.add_file(p, tmp / "scheme.cdxml")
    p.close()
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    assert win.open_project(p.folder)
    yield win, c
    win.close_project()
    win.close()


def test_tree_item_page_and_warnings_panel(qapp, window, monkeypatch):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui import warnings_panel
    from si_assistant.ui.manuscript_ui import ManuscriptPage
    win, c = window
    tree = win.tree
    n = tree.topLevelItemCount()
    assert tree.topLevelItem(n - 2).text(0) == "Manuscript check"
    assert tree.topLevelItem(n - 1).text(0) == "References"
    assert mw.MANUSCRIPT_ITEM in mw.FIXED_KINDS
    win.select(mw.MANUSCRIPT_ITEM, mw.MANUSCRIPT_ITEM)
    page = win.panel
    assert isinstance(page, ManuscriptPage)
    page.run_check()
    assert win.preview_pane.target is None
    assert not win.act_delete.isEnabled() and not win.act_rename.isEnabled()
    # A section dropped on it goes last, before it.
    from PySide6.QtWidgets import QAbstractItemView
    sec = win._find_item("section", win.project.data_sections()[0].id)
    assert tree._section_drop(sec, tree.topLevelItem(n - 2),
                              QAbstractItemView.DropIndicatorPosition.OnItem) == \
        ("section", "", tree.top_section_count())
    # The Warnings panel's "Manuscript" group (from what was read; no file is read there).
    panel = win.warnings_panel
    panel.set_project(win.project)
    items = _texts(panel.tree)
    group = next(i for i in items if i.text(0).startswith("Manuscript"))
    assert group.text(0) == "Manuscript    ⚠ 7"
    yield_item = next(i for i in items if i.text(0).startswith("⚠ Yield: 3b"))
    # Double-click: the compound at its Yield field.
    opened = []
    panel.open_requested.connect(lambda cid, field: opened.append((cid, field)))
    panel._open(yield_item)
    assert opened == [(c["3b"].id, "yield")]
    # Dismiss from the panel (one undo step), then show and restore it.
    panel.tree.setCurrentItem(yield_item)
    assert panel.dismiss_btn.isEnabled()
    monkeypatch.setattr(warnings_panel, "ask_reason", lambda *a: "typo")
    panel._dismiss_selected()
    assert next(i for i in _texts(panel.tree) if i.text(0).startswith("Manuscript")).text(0) \
        == "Manuscript    ⚠ 6"
    assert "manuscript warning" in win.act_undo.text()
    panel.show_dismissed.setChecked(True)
    gone = next(i for i in _texts(panel.tree) if i.text(0).startswith("✓ dismissed · Yield: 3b"))
    panel.tree.setCurrentItem(gone)
    assert panel.restore_btn.isEnabled()
    panel._restore_selected()
    assert M.dismissed(win.project) == {}
    # Changed files: the panel says to check again instead of reading them.
    (M.folder(win.project) / "ms.pdf").write_bytes(
        (M.folder(win.project) / "ms.pdf").read_bytes() + b"\n")
    panel.refresh()
    assert any("not checked since the files changed" in i.text(0) for i in _texts(panel.tree))


def test_open_compound_from_page(qapp, window):
    from si_assistant.ui import main_window as mw
    from si_assistant.ui.compound_panel import CompoundPanel
    win, c = window
    win.select(mw.MANUSCRIPT_ITEM, mw.MANUSCRIPT_ITEM)
    page = win.panel
    page.run_check()
    row = next(r for r in range(page.table.rowCount())
               if (page.table.item(r, 0).text(), page.table.item(r, 1).text()) == ("3b", "Yield"))
    page.table.selectRow(row)
    page._open_selected()
    for _ in range(5):
        qapp.processEvents()
    assert isinstance(win.panel, CompoundPanel) and win.panel.compound.id == c["3b"].id


def test_page_layout_keeps_room_for_the_results(qapp, si):
    """The files list grows with its files (up to a cap) instead of squeezing the results;
    the results table has a minimum height and the page scrolls when the window is small."""
    from si_assistant.ui import manuscript_ui as U
    p, _c, tmp = si
    page = U.ManuscriptPage(p, auto=False)
    page.resize(900, 420)
    page.show()
    qapp.processEvents()
    assert page.table.minimumHeight() >= U.TABLE_MIN_HEIGHT
    assert page.table.height() >= U.TABLE_MIN_HEIGHT        # (the page scrolls instead)
    assert page.scroll.verticalScrollBar().maximum() > 0
    empty_cap = page.files.maximumHeight()
    for k in range(5):
        (tmp / f"scheme {k}.cdxml").write_text("<CDXML/>", encoding="utf-8")
        M.add_file(p, tmp / f"scheme {k}.cdxml")
    page._fill_files()
    qapp.processEvents()
    assert page.files.maximumHeight() > empty_cap
    assert page.files.height() >= page._list_height(5) - 4    # all five files in view
    page.close()
