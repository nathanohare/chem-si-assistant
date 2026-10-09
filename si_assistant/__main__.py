"""Start the program:  python -m si_assistant  [optional project folder]"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import APP_NAME, resources, selftest
from .compile.qt_images import allow_large_images
from .ui import restart
from .ui.app_icon import app_icon, use_own_taskbar_icon
from .ui.carry_over import carry_over_from_old_name
from .ui.feedback_ui import install_error_handler
from .ui.main_window import MainWindow

# Tests only: a folder for this computer's settings, so a test run never reads or changes
# the real ones (see use_settings_folder). Normal start-ups don't set it.
SETTINGS_DIR_ENV = "SI_ASSISTANT_SETTINGS_DIR"

# The program's own per-computer folders ("My dictionary", ...) that use_settings_folder
# moves into the test folder. Others (e.g. Downloads) stay where they are.
_MOVED_LOCATIONS = ("AppDataLocation", "AppLocalDataLocation", "AppConfigLocation",
                    "ConfigLocation", "GenericDataLocation", "GenericConfigLocation",
                    "CacheLocation", "GenericCacheLocation", "StateLocation",
                    "GenericStateLocation")


def use_settings_folder(folder) -> None:
    """For tests ($SI_ASSISTANT_SETTINGS_DIR): keep every per-computer setting (user name,
    recent projects, Interface size, ...) and the program's own files ("My dictionary")
    in `folder` instead of the real ones (on Windows the registry), also the old name's
    settings that carry_over_from_old_name reads. Call before anything makes a QSettings.

    The program makes its settings with QSettings(name, name), which Qt always keeps in
    the registry / plist / .config file (QSettings.setDefaultFormat does not apply to
    that form), so QSettings and QStandardPaths are swapped, in Qt and in every module
    of the program already loaded, for versions that use `folder`."""
    from PySide6 import QtCore
    from PySide6.QtCore import QSettings, QStandardPaths

    folder = Path(folder)
    ini = QSettings.Format.IniFormat
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(ini, scope, str(folder / "settings" / scope.name))
    QSettings.setDefaultFormat(ini)

    class FolderSettings(QSettings):
        """QSettings that keeps everything as .ini files in the test folder."""

        def __init__(self, *args, **kwargs):
            named = len(args) == 1 or (len(args) > 1 and isinstance(args[1], str))
            if args and isinstance(args[0], str) and named:   # (organisation, program)
                args = (ini, QSettings.Scope.UserScope, *args)
            elif args and isinstance(args[0], QSettings.Scope) and len(args) > 1 \
                    and isinstance(args[1], str):              # (scope, organisation, ...)
                args = (ini, *args)
            elif args and args[0] == QSettings.Format.NativeFormat:
                args = (ini, *args[1:])
            super().__init__(*args, **kwargs)

    StandardLocation = QStandardPaths.StandardLocation
    moved = {getattr(StandardLocation, n) for n in _MOVED_LOCATIONS
             if hasattr(StandardLocation, n)}

    def own_folder(location) -> str:
        app = QtCore.QCoreApplication
        return str(folder / "paths" / location.name / app.organizationName()
                   / app.applicationName())

    class FolderPaths(QStandardPaths):
        """QStandardPaths whose per-program folders are in the test folder."""

        @staticmethod
        def writableLocation(location):
            if location in moved:
                return own_folder(location)
            return QStandardPaths.writableLocation(location)

        @staticmethod
        def standardLocations(location):
            if location in moved:
                return [own_folder(location)]
            return QStandardPaths.standardLocations(location)

    QtCore.QSettings, QtCore.QStandardPaths = FolderSettings, FolderPaths
    package = __package__ or "si_assistant"
    for name, module in list(sys.modules.items()):
        if name != package and not name.startswith(package + "."):
            continue
        if getattr(module, "QSettings", None) is QSettings:
            module.QSettings = FolderSettings
        if getattr(module, "QStandardPaths", None) is QStandardPaths:
            module.QStandardPaths = FolderPaths


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
    if os.environ.get(SETTINGS_DIR_ENV):   # tests only: throw-away settings
        use_settings_folder(os.environ[SETTINGS_DIR_ENV])
    # View ▸ Interface size (SPEC §13l.2): Qt scales the interface only if told before the
    # application object exists (QT_SCALE_FACTOR; one the user set themselves wins).
    restart.apply_saved_size()
    use_own_taskbar_icon()  # Windows: show our icon on the taskbar, not Python's
    allow_large_images()  # pictures pasted at 1200 dpi can be bigger than Qt's default limit
    app = QApplication(sys.argv)
    restart.forget_scale_variable()   # (read by now; programs started from here don't get it)
    restart.forget_bundle_library_path()   # (Linux build: opened PDFs/links use the system's)
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
