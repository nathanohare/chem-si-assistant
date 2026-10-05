"""Project settings: Structures, Page and pasted-picture resolution (SPEC §13k.2a, 6, 7)."""

from si_assistant.core import chem
from si_assistant.ui import dialogs
from si_assistant.ui.dialogs import ProjectSettingsDialog


def test_structures_tab_saves_drawing_settings(qapp, project):
    dlg = ProjectSettingsDialog(project, "Me")
    tab = dlg.structures
    assert tab.values() == chem.DrawingSettings()            # ACS defaults
    assert tab.sample.pixmap() is not None and not tab.sample.pixmap().isNull()
    dlg.apply()
    assert "structure_drawing" not in project.settings       # unchanged: not stored
    tab.bond.setValue(0.6)
    tab.colour.setChecked(True)
    tab.compile_choice.setCurrentIndex(tab.compile_choice.findData("acs"))
    dlg.apply()
    saved = chem.DrawingSettings.from_settings(project.settings)
    assert saved.bond_length_cm == 0.6 and saved.colour_atoms
    assert project.settings["compile_structures"] == "acs"
    # Reopening shows the saved values.
    again = ProjectSettingsDialog(project, "Me").structures.values()
    assert again == saved


def test_page_tab_saves_header_footer_and_numbers(qapp, project):
    project.settings["title"] = "My SI"     # (set on Front matter ▸ Cover page)
    dlg = ProjectSettingsDialog(project, "Me")
    tab = dlg.page_tab
    assert tab.footer["center"].text() == "{page}"           # ACS: number centred
    tab.header["left"].setText("{title}")
    tab.number_style.setCurrentIndex(tab.number_style.findData("i"))
    tab.bold.setChecked(True)
    assert "My SI" in tab.example.text() and "Sii" in tab.example.text()
    dlg.apply()
    page = project.settings["page"]
    assert page["header"]["left"] == "{title}" and page["number_style"] == "i"
    assert page["number_bold"] and "footer" not in page      # style footer not stored


def test_footer_follows_journal_style_until_changed(qapp, project):
    dlg = ProjectSettingsDialog(project, "Me")
    tab = dlg.page_tab
    dlg.style_box.setCurrentText("Wiley")
    assert tab.footer["right"].text() == "{page}" and tab.footer["center"].text() == ""
    tab.footer["left"].setText("Page {page} of {pages}")
    dlg.style_box.setCurrentText("RSC")                       # edited: left alone
    assert tab.footer["left"].text() == "Page {page} of {pages}"
    dlg.apply()
    assert project.settings["page"]["footer"]["left"] == "Page {page} of {pages}"


def test_paste_resolution_and_rerender_button(qapp, project, monkeypatch):
    shown = []
    monkeypatch.setattr(dialogs.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id)
    project.set_structure_image(c, data=b"\x89PNG fake")       # no saved original
    dlg = ProjectSettingsDialog(project, "Me")
    assert dlg.paste_dpi.currentData() == 1200
    dlg.paste_dpi.setCurrentIndex(dlg.paste_dpi.findData(600))
    dlg.rerender_btn.click()
    assert "Re-rendered 0" in shown[0] and "1 picture(s) have no saved" in shown[0]
    assert project.settings["paste_dpi"] == 600
    dlg.apply()
    assert project.settings["paste_dpi"] == 600


def test_page_number_size_is_same_as_text_or_readable(qapp, project):
    dlg = ProjectSettingsDialog(project, "Me")
    tab = dlg.page_tab
    assert tab.same_size.isChecked() and not tab.size.isEnabled()
    assert tab.values()["number_size"] == 0                   # "Same as the text"
    assert tab.size.minimum() >= 5 and tab.size.value() == 11  # ACS body text size
    tab.same_size.setChecked(False)
    assert tab.size.isEnabled()
    tab.size.stepBy(1)                                        # one click up: 11.5 pt, not 0.5 pt
    assert tab.values()["number_size"] == 11.5
    dlg.apply()
    assert project.settings["page"]["number_size"] == 11.5
    again = ProjectSettingsDialog(project, "Me").page_tab
    assert not again.same_size.isChecked() and again.size.value() == 11.5


def test_rerender_button_shows_progress_and_can_stop(qapp, project, monkeypatch):
    from si_assistant.ui import clipboard_image as ci
    shown = []
    monkeypatch.setattr(dialogs.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(dialogs.QProgressDialog, "wasCanceled", lambda self: True)
    calls = []
    monkeypatch.setattr(ci, "rerender_pasted_pictures",
                        lambda p, dpi, progress=None: calls.append(progress(0, 5)) or (0, 0, 0))
    dlg = ProjectSettingsDialog(project, "Me")
    dlg.rerender_btn.click()
    assert calls == [False]                                   # Stop was pressed
    assert "Stopped before the end" in shown[0]