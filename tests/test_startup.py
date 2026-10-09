"""Start the real program in a separate process, exactly as the launcher does.

The other UI tests create the Qt application before importing anything, which
hides crashes that happen while the program's modules are first loaded.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_program_starts_and_quits_cleanly(tmp_path):
    # SI_ASSISTANT_SETTINGS_DIR: the program keeps its per-computer settings in tmp_path,
    # so this test never reads or changes the real ones (the registry on Windows).
    env = dict(os.environ, SI_ASSISTANT_SMOKE_TEST="1", QT_QPA_PLATFORM="offscreen",
               PYTHONFAULTHANDLER="1", SI_ASSISTANT_SETTINGS_DIR=str(tmp_path))
    result = subprocess.run([sys.executable, "-m", "si_assistant"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        f"The program crashed on start-up (exit code {result.returncode}):\n{result.stderr}")
    # Start-up wrote its settings (e.g. "carried over from the old name") to tmp_path.
    assert list((tmp_path / "settings").rglob("*.ini")), result.stderr


def test_modules_import_without_a_running_app():
    """Importing the UI must not touch Qt features that need the app to exist."""
    code = ("import faulthandler; faulthandler.enable(); "
            "import si_assistant.ui.main_window, si_assistant.ui.clipboard_image; print('ok')")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr
