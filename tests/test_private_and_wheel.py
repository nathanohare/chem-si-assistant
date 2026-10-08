from si_assistant.core.project import Project


def test_private_files_and_folders(project, tmp_path):
    section = project.add_section("data", "Products")
    c = project.add_compound(section.id)
    project.add_field(c, {"key": "private_files"})
    raw = tmp_path / "3a_1H.fid"
    (raw / "pdata" / "1").mkdir(parents=True)
    (raw / "fid").write_bytes(b"raw nmr")
    (raw / "pdata" / "1" / "1r").write_bytes(b"processed")
    mnova = tmp_path / "3a.mnova"
    mnova.write_bytes(b"mnova")

    folder_item = project.add_private_item(c, raw)
    file_item = project.add_private_item(c, mnova, note="processed")
    assert folder_item["kind"] == "folder" and file_item["kind"] == "file"
    assert (c.folder / "private" / "3a_1H.fid" / "pdata" / "1" / "1r").read_bytes() == b"processed"
    assert raw.exists()  # the original is never touched

    project.remove_private_item(c, "3a_1H.fid")
    assert not (c.folder / "private" / "3a_1H.fid").exists()
    project.restore_from_trash(project.list_trash()[0].id)
    reopened = Project.open(project.folder).compound(c.id)
    assert {i["file"] for i in reopened.values["private_files"]} == {"3a_1H.fid", "3a.mnova"}
    assert (c.folder / "private" / "3a_1H.fid" / "fid").read_bytes() == b"raw nmr"


def _wheel(target, ticks: int = -1):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120 * ticks),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, ev)


def test_wheel_never_changes_a_dropdown(qapp):
    """SPEC §13n.6: dropdowns and number boxes ignore the wheel even when clicked into
    (the page scrolls instead)."""
    from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QScrollArea,
                                   QSpinBox, QVBoxLayout, QWidget)
    from si_assistant.ui.wheel_guard import WheelGuard
    guard = WheelGuard()
    QApplication.instance().installEventFilter(guard)
    try:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QComboBox()
        box.addItems(["mmol", "µmol", "mol"])
        editable = QComboBox()
        editable.setEditable(True)
        editable.addItems(["ESI", "APCI", "EI"])
        spin = QSpinBox()
        spin.setRange(0, 10)
        double = QDoubleSpinBox()
        double.setRange(0, 10)
        for w in (box, editable, spin, double):
            layout.addWidget(w)
        layout.addSpacing(3000)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.resize(300, 200)
        scroll.show()
        qapp.processEvents()
        bar = scroll.verticalScrollBar()

        _wheel(box)
        assert box.currentIndex() == 0          # not focused: unchanged…
        assert bar.value() > 0                  # …and the page scrolled instead
        for combo in (box, editable):
            combo.setFocus()
            qapp.processEvents()
            _wheel(combo)
            if combo.isEditable():
                _wheel(combo.lineEdit())
            assert combo.currentIndex() == 0    # focused (selected) too: unchanged
        for number in (spin, double):
            _wheel(number, ticks=1)
            assert number.value() == 0          # not clicked into: unchanged
            number.setFocus()
            qapp.processEvents()
            _wheel(number, ticks=1)
            assert number.value() == 0          # clicked into: still unchanged
        before = bar.value()
        _wheel(spin)
        assert bar.value() > before             # the page scrolls instead
        scroll.close()
    finally:
        QApplication.instance().removeEventFilter(guard)
