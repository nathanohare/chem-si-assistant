"""The rich-text editor's mention/citation chips and pictures (SPEC §6.1, §7.5)."""

import pytest
from PySide6.QtGui import QImage, QTextCursor

from si_assistant.ui import rich_text


@pytest.fixture
def ctx(project):
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["3a", "3b"])
    a = project.add_compound(data.id, name="Amide")
    b = project.add_compound(data.id, name="Enamide")
    ref = project.add_reference({"authors": ["Fulmer, G. R."], "year": "2010",
                                 "title": "Impurities", "journal": "Organometallics"})
    rich_text.set_link_context(rich_text.LinkContext(project))
    yield project, a, b, ref
    rich_text.set_link_context(None)


def chip_labels(edit):
    out = []
    block = edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.objectType() == rich_text.LINK_OBJECT:
                out.append(edit.chip_label(fmt))   # (the label shown)
            it += 1
        block = block.next()
    return out


def test_chips_round_trip_and_show_current_ids(qapp, ctx):
    project, a, b, ref = ctx
    ed = rich_text.RichTextEditor()
    text = f"From @{{{a.id}}} to **@{{{b.id}}}**.@cite{{{ref['id']}}}"
    ed.set_markup(text)
    assert chip_labels(ed.edit) == ["3a", "3b", "[Fulmer 2010]"]
    assert ed.markup() == f"From @{{{a.id}}} to @{{{b.id}}}.@cite{{{ref['id']}}}"
    # Renumbering: move b before a, labels follow.
    project.move_compound(b.id, b.section_id, 0)
    ed.edit.refresh_links()
    assert chip_labels(ed.edit) == ["3b", "3a", "[Fulmer 2010]"]


def test_insert_mention_and_type_after_it(qapp, ctx):
    project, a, _b, _ref = ctx
    ed = rich_text.RichTextEditor()
    ed.set_markup("Made from ")
    ed.edit.moveCursor(QTextCursor.MoveOperation.End)
    ed.edit.insert_mention(a.id)
    ed.edit.insertPlainText(" (0.2 mmol)")
    assert ed.markup() == f"Made from @{{{a.id}}} (0.2 mmol)"


def test_copy_paste_keeps_links(qapp, ctx):
    project, a, _b, ref = ctx
    src = rich_text.RichTextEditor()
    src.set_markup(f"*See* @{{{a.id}}}@cite{{{ref['id']}}}")
    src.edit.selectAll()
    mime = src.edit.createMimeDataFromSelection()
    assert mime.text() == "See 3a[Fulmer 2010]"
    dest = rich_text.RichTextEditor()
    dest.edit.insertFromMimeData(mime)
    assert dest.markup() == f"*See* @{{{a.id}}}@cite{{{ref['id']}}}"
    no_links = rich_text.RichTextEditor(links=False)
    no_links.edit.insertFromMimeData(mime)
    assert no_links.markup() == "*See* "


def test_pictures_in_text(qapp, project, tmp_path):
    """Older picture paragraphs still show (read-only editors); in an editor with
    figures they become figures (SPEC §13k.5)."""
    from si_assistant.ui.text_objects import ObjectStore
    img = QImage(800, 200, QImage.Format.Format_RGB32)
    img.fill(0xFF3366)
    section = project.add_section("text", "Notes")
    (section.folder / "images").mkdir()
    img.save(str(section.folder / "images" / "pic.png"))
    ed = rich_text.RichTextEditor()
    ed.edit.image_folder = section.folder
    ed.set_markup("Intro\n!image{images/pic.png} Optimisation of *conditions*\nAfter")
    assert ed.markup() == "Intro\n!image{images/pic.png} Optimisation of *conditions*\nAfter"
    store = ObjectStore(project, section.id)
    ed2 = rich_text.RichTextEditor()
    ed2.enable_objects(store)
    figure_id = store.add_file(section.folder / "images" / "pic.png")
    ed2.set_markup("After")
    ed2.edit.moveCursor(QTextCursor.MoveOperation.End)
    ed2.edit.insert_object("figure", figure_id)
    ed2.edit.insertPlainText("Caption 2")
    assert ed2.markup() == f"After\n!figure{{{figure_id}}} Caption 2"
