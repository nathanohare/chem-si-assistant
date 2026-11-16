"""Issue #3: a figure in a custom section printed without its number and caption."""

import pytest
from PySide6.QtGui import QImage

from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.core.labels import compute
from si_assistant.ui import rich_text, text_objects
from si_assistant.ui.panels import TextSectionPanel


def picture(path):
    img = QImage(300, 120, QImage.Format.Format_RGB32)
    img.fill(0x3366FF)
    img.save(str(path))
    return path


@pytest.fixture
def setup(qapp, project, tmp_path):
    p = project
    notes = p.add_section("text", "Optimization")
    owner = p.text_owner(notes.id)
    f1 = p.add_figure(owner, source=picture(tmp_path / "a.png"))
    f2 = p.add_figure(owner, source=picture(tmp_path / "b.png"))
    f3 = p.add_figure(owner, source=picture(tmp_path / "c.png"))
    p.write_section_text(notes, f"See @{{{f3}}}.\n!figure{{{f1}}} First\n"
                                f"!figure{{{f2}}} Second\n!figure{{{f3}}} Third")
    yield p, notes, owner, (f1, f2, f3)


def source(p):
    result = build(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png)
    return result.typ_path.read_text(encoding="utf-8"), result.warnings


def test_numbering_skips_the_uncaptioned_figure(setup):
    p, _notes, owner, (f1, f2, f3) = setup
    p.set_figure_captioned(owner, f2, False)
    assert p.figures(owner)[f2]["uncaptioned"] is True
    labels = compute(p)
    assert labels.label(f1) == "Figure S1" and labels.label(f3) == "Figure S2"
    assert labels.label(f2) is None and f2 in labels.unnumbered
    p.set_figure_captioned(owner, f2, True)
    assert "uncaptioned" not in p.figures(owner)[f2]
    assert compute(p).label(f3) == "Figure S3"


def test_compiled_without_number_or_caption(setup):
    p, _notes, owner, (f1, f2, f3) = setup
    p.set_figure_captioned(owner, f2, False)
    src, warnings = source(p)
    assert src.count("#si-figure(") == 2
    assert "Second" not in src and "First" in src and "Third" in src
    assert "#block(width: 100%, breakable: false, align(center, image(" in src
    assert "Figure S2" in src and "Figure S3" not in src   # (the mention follows)
    assert warnings == []
    # Mentioning it: a warning of its own, printed as ??.
    p.set_figure_captioned(owner, f3, False)
    src, warnings = source(p)
    assert any("without a number or caption" in w for w in warnings)
    assert not any("no longer exists" in w for w in warnings)


def test_the_menu_switches_it_and_copies_keep_it(setup):
    p, notes, owner, (f1, f2, f3) = setup
    rich_text.set_link_context(rich_text.LinkContext(p))
    try:
        panel = TextSectionPanel(p, notes)
        edit = panel.editor.edit
        menu = text_objects.object_menu(edit, "figure", f1)
        act = next(a for a in menu.actions() if a.text() == "Print without number and caption")
        assert act.isCheckable() and not act.isChecked()
        act.trigger()
        assert edit.objects.figure_uncaptioned(f1)
        assert compute(p).label(f2) == "Figure S1"
        info = edit.objects.export("figure", f1)
        assert info["uncaptioned"] is True
        copy = edit.objects.adopt("figure", f1, info, {f1})
        assert copy != f1 and edit.objects.figure_uncaptioned(copy)
        menu = text_objects.object_menu(edit, "figure", f1)
        act = next(a for a in menu.actions() if a.text() == "Print without number and caption")
        assert act.isChecked()
        act.trigger()
        assert not edit.objects.figure_uncaptioned(f1)
        panel.deleteLater()
    finally:
        rich_text.set_link_context(None)


def test_a_new_picture_keeps_the_setting(setup, tmp_path):
    p, _notes, owner, (f1, _f2, _f3) = setup
    p.set_figure_captioned(owner, f1, False)
    p.replace_figure_picture(owner, f1, source=picture(tmp_path / "new.png"))
    assert p.figures(owner)[f1]["uncaptioned"] is True
