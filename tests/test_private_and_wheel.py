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


def test_wheel_does_not_change_unfocused_dropdown(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QComboBox
    from si_assistant.ui.wheel_guard import WheelGuard
    guard = WheelGuard()
    QApplication.instance().installEventFilter(guard)
    try:
        box = QComboBox()
        box.addItems(["mmol", "µmol", "mol"])

        def wheel():
            ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                             Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(box, ev)

        wheel()
        assert box.currentIndex() == 0          # not focused: unchanged
        box.show()
        box.setFocus()
        qapp.processEvents()
        if box.hasFocus():                      # offscreen focus is best-effort
            wheel()
            assert box.currentIndex() == 1
    finally:
        QApplication.instance().removeEventFilter(guard)
