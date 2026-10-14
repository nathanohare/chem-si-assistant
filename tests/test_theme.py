"""The program's look (SPEC §13w): Chem Illustrator's colours, light and dark, the style
sheet, and the line icons."""

from PySide6.QtGui import QPalette

from si_assistant.ui import theme


def test_the_colours_are_chem_illustrators():
    assert theme.LIGHT["bg"] == "#eef0f3" and theme.LIGHT["accent"] == "#2f6fdf"
    assert theme.DARK["bg"] == "#1b1e24" and theme.DARK["accent"] == "#7aa7ff"
    assert set(theme.LIGHT) == set(theme.DARK)


def test_palette_and_style_sheet(qapp):
    for c in (theme.LIGHT, theme.DARK):
        p = theme.palette(c)
        assert p.color(QPalette.ColorRole.Window).name() == c["bg"]
        assert p.color(QPalette.ColorRole.Base).name() == c["panel"]
        sheet = theme.style_sheet(c)
        assert c["accent_soft"] in sheet and "{" in sheet and "{{" not in sheet
        assert sheet.count("{") == sheet.count("}")


def test_every_icon_draws(qapp):
    for name in theme.ICONS:
        icon = theme.icon(name)
        assert not icon.isNull() and not icon.pixmap(24, 24).toImage().isNull(), name
