"""Start the program:  python -m si_assistant  [optional project folder]"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtWidgets import QApplication

from . import APP_NAME, resources, selftest
from .compile.qt_images import allow_large_images
from .ui import restart
from .ui.app_icon import app_icon, use_own_taskbar_icon
from .ui.carry_over import carry_over_from_old_name
from .ui.feedback_ui import install_error_handler
from .ui.main_window import MainWindow


def restore_safely(window, info) -> bool:
    """Go back to where the user was before a restart (Interface size). Whatever the
    restore info holds, a problem with it never stops the program starting: it then just
    shows the start page (or the project as far as it got). True if it was applied."""
    try:
        return bool(window.apply_restore_info(info))
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    # A packaged program (the installers) may need settings before Qt starts (resources.py).
    resources.prepare_environment()
    # View ▸ Interface size (SPEC §13l.2): Qt scales the interface only if told before the
    # application object exists (QT_SCALE_FACTOR; one the user set themselves wins).
    restart.apply_saved_size()
    # The Ketcher drawing editor is a web page shown with Qt WebEngine, which must be
    # loaded before the application object is created.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        pass  # drawing is then unavailable; everything else works
    use_own_taskbar_icon()  # Windows: show our icon on the taskbar, not Python's
    allow_large_images()  # pictures pasted at 1200 dpi can be bigger than Qt's default limit
    app = QApplication(sys.argv)
    restart.forget_scale_variable()   # (read by now; programs started from here don't get it)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(app_icon())  # every window of the program uses this icon
    # The installed program's self-test (selftest.py): checks and quits, no window.
    report, self_test_project, argv = selftest.split_args(sys.argv[1:])
    if report is not None:
        return selftest.run(report, self_test_project)
    carry_over_from_old_name()   # settings saved under the old name "SI Assistant"
    resources.hold_running_mutex()   # (Windows: lets an update's installer wait for us)
    smoke_test = bool(os.environ.get("SI_ASSISTANT_SMOKE_TEST"))
    # After "Restart now?" (Interface size): where the user was, for this start only.
    restore, args = restart.split_restore_arg(argv)
    window = MainWindow()
    # Unexpected errors: a plain "Something went wrong" with "Report this problem"
    # (SPEC §13m.4). Kept in a variable so it lives as long as the program.
    error_handler = install_error_handler(lambda: window, window.error_report_paths)  # noqa: F841
    window.show()
    if restore is not None and not smoke_test:
        restore_safely(window, restore)
    elif args:
        window.open_project(Path(args[0]))
    elif not smoke_test:
        # "Open the last project when the program starts" (View menu / start page).
        window.open_last_project_at_start()
    if smoke_test:
        # Used by the tests: start up completely, then quit straight away.
        QTimer.singleShot(1500, app.quit)
    else:
        # Is there a newer version? (In the background; silent unless there is: §13m.3.)
        window.check_for_updates_at_start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
