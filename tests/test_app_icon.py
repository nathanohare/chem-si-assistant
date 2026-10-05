"""The program's icon files are present and load as a usable icon."""

from si_assistant.ui.app_icon import ICON_SIZES, ICONS_DIR, app_icon, icon_files


def test_every_icon_size_is_present():
    assert [f.name for f in icon_files()] == [f"app_icon_{s}.png" for s in ICON_SIZES]
    assert (ICONS_DIR / "app_icon.ico").exists()


def test_icon_loads(qapp):
    icon = app_icon()
    assert not icon.isNull()
    assert max(s.width() for s in icon.availableSizes()) == 256
