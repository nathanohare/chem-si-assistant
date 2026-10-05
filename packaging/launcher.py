"""The installed program's starting point (PyInstaller runs this file; see
chem_si_assistant.spec). It does what `python -m si_assistant` does."""

import multiprocessing
import sys

from si_assistant.__main__ import main

if __name__ == "__main__":
    # (Nothing uses multiprocessing today; this keeps a packaged program safe if it ever
    # does: a child process then runs the job instead of opening another window.)
    multiprocessing.freeze_support()
    sys.exit(main())
