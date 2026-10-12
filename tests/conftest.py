import os

import pytest

# Run Qt without opening windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Delete the windows tests left open while Qt is still running: with PySide6 6.12 on
    # Linux, Python freeing them after Qt has shut down crashes the test run on exit
    # (though every test passed).
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


@pytest.fixture(autouse=True)
def _clear_clipboard():
    """Tests that put a QMimeData on the clipboard must not leave it there: PySide6 would
    free it twice when the test run ends (a crash on exit, though every test passed)."""
    yield
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is not None:
        QGuiApplication.clipboard().clear()


@pytest.fixture(autouse=True)
def _example_copy_in_temp(monkeypatch, tmp_path_factory):
    """The user's copy of the example project lives in this computer's folder for the
    program's own files: tests keep it in a temporary folder instead."""
    from si_assistant import resources
    folder = tmp_path_factory.mktemp("appdata", numbered=True)
    monkeypatch.setattr(resources, "example_copy_folder",
                        lambda: folder / resources.EXAMPLE_COPY_DIR)
    yield


@pytest.fixture(autouse=True)
def _expand_fields():
    """Collapsed fields are remembered for the session; start each test fully expanded."""
    from si_assistant.ui import field_widgets
    field_widgets.COLLAPSED.clear()
    yield


@pytest.fixture(autouse=True)
def _live_preview_on_main_thread(monkeypatch):
    """The live preview pane normally runs Typst in a worker thread. In tests it runs on
    the main thread, so no thread can outlive a test (tests of the thread turn it back on
    and wait for it)."""
    from si_assistant.ui import live_preview
    monkeypatch.setattr(live_preview.LivePreviewPane, "use_thread", False)
    yield


@pytest.fixture(autouse=True)
def _text_checks_in_temp(monkeypatch, tmp_path_factory):
    """"My dictionary" and the Chemical formula toggle are per-computer settings: tests
    keep them in a temporary folder, fresh for each test."""
    from PySide6.QtCore import QSettings
    from si_assistant.ui import text_checks
    folder = tmp_path_factory.mktemp("appdata")
    monkeypatch.setattr(text_checks, "personal_dictionary_path",
                        lambda: folder / text_checks.PERSONAL_FILE)
    monkeypatch.setattr(text_checks, "_settings",
                        lambda: QSettings(str(folder / "prefs.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(text_checks, "_hub", None)
    yield


@pytest.fixture(autouse=True)
def _pubchem_offline(monkeypatch, tmp_path_factory):
    """The PubChem name lookup (SPEC §13n.5) never uses the network in tests: a fetcher
    that fails unless a test puts its own fake one in. Its "Don't show again" setting is
    kept in a temporary file, and the lookup runs on the main thread."""
    from PySide6.QtCore import QSettings
    from si_assistant.core import pubchem
    from si_assistant.ui import name_lookup

    def no_network(url, timeout):
        raise pubchem.LookupFailed("No network in tests.")

    folder = tmp_path_factory.mktemp("pubchem")
    monkeypatch.setattr(name_lookup, "fetcher", no_network)
    monkeypatch.setattr(name_lookup, "USE_THREAD", False)
    monkeypatch.setattr(name_lookup, "_note_shown", False)
    monkeypatch.setattr(name_lookup, "_settings",
                        lambda: QSettings(str(folder / "prefs.ini"), QSettings.Format.IniFormat))
    yield


@pytest.fixture
def project(tmp_path):
    from si_assistant.core.project import Project
    p = Project.create(tmp_path, "Test SI")
    p.acquire_lock("Tester")
    yield p
    p.close()
