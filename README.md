# Chem SI Assistant

A desktop program for building the Supporting Information of a synthetic chemistry paper.
See `SPEC.md` for the full design.

The program was first called "SI Assistant". The folder, the package (`si_assistant`), the
Anaconda environment (`si-assistant`) and `run_si_assistant.bat` keep that name. Your
settings on each computer (your name, recent projects, window layout, preferences and "My
dictionary") are copied over from the old name automatically the first time Chem SI
Assistant starts; the old copies are left untouched. **Current status: Phase 8 (release: installers, update check, feedback).**

## Download and install

Ready-to-install versions for Windows, macOS and Linux are on the
[Releases page](https://github.com/nathanohare/chem-si-assistant/releases). Nothing else
needs installing (Python, Qt, RDKit and Typst are included).

| System | File | How to install |
|---|---|---|
| **Windows** 10 or 11 (64-bit) | `Chem-SI-Assistant-<version>-Windows-Setup.exe` | Run it. It installs for you only (no administrator rights needed), adds a Start-menu entry and an uninstaller. |
| **macOS** 11 or later, Apple-silicon Macs (M1 and newer) | `Chem-SI-Assistant-<version>-macOS.dmg` | Open it and drag **Chem SI Assistant** into **Applications**. |
| **Linux** (64-bit Intel/AMD; Ubuntu 22.04 or similar and newer) | `Chem-SI-Assistant-<version>-x86_64.AppImage` | Make it executable (`chmod +x Chem-SI-Assistant-*.AppImage`, or Properties ▸ Allow executing), then double-click it. |

**Intel Macs are not supported yet:** the chemistry library (RDKit) used by the program is
not available as a ready-made package for Intel Macs with the Python version the installer
is built with. You can still run the program from the source code (below).

**First start — the installers are not signed yet**, so the system warns once:
- **Windows** ("Windows protected your PC", SmartScreen): click **More info**, then **Run
  anyway**.
- **macOS** ("cannot be opened because Apple cannot check it for malicious software"):
  right-click (or Control-click) the app in Applications and choose **Open**, then
  **Open** again. On newer macOS versions, if there is no Open button: **System Settings ▸
  Privacy & Security**, scroll down to the message about Chem SI Assistant and click
  **Open Anyway**.

The [user guide](docs/user-guide.md) (also in the program: **Help ▸ User guide**, F1)
explains all of this step by step.

The program checks for new versions when it starts (it can be turned off in the View
menu) and from **Help ▸ Check for updates…**. On Windows it downloads the new installer and
runs it; on macOS and Linux it downloads the new version for you to open. Only GitHub is
contacted, and nothing about you or your projects is sent.

**Problems and ideas:** use **Help ▸ Send feedback…** (it opens a pre-filled GitHub page;
a free GitHub account is needed), or open an
[issue](https://github.com/nathanohare/chem-si-assistant/issues/new/choose).

## Known limitations

- **Pasting from ChemDraw and Mnova on a Mac has not been tested yet.** The program was
  developed and tested with ChemDraw and Mnova on Windows. On macOS it may not see
  ChemDraw's structure data or the sharp (vector) copies ChemDraw and Mnova make, so a
  pasted structure, picture or spectrum may come in as an ordinary lower-resolution picture
  instead. Nothing is lost or broken: you can still paste the structure as SMILES or a MOL
  file, or attach the spectrum as a PDF file. The check to run on a Mac is described in
  [TODO.md](TODO.md); if you have a Mac with ChemDraw or Mnova, the results are very
  welcome as an issue.
- **Intel Macs are not supported**, and the installers are **not signed** (see above).
- **Three journal styles:** ACS, Wiley and RSC. Other journals' layouts can be approximated
  with these.
- **No structure editor of its own:** structures are drawn in ChemDraw (or another
  editor) and pasted, or typed as SMILES.
- **English only:** the program, its spelling check and the SI text it writes.

## Running it from the source code (Windows, current setup)

The program uses its own Anaconda environment called `si-assistant`, kept separate
from your other Python work.

- **Easiest:** double-click `run_si_assistant.bat`.
- **From a terminal** (Anaconda Prompt or PowerShell), in this folder:

      %USERPROFILE%\anaconda3\envs\si-assistant\python.exe -m si_assistant

  You can also pass a project folder to open it straight away.

### Setting up the environment on another computer

    conda create -n si-assistant python=3.12 -y
    conda activate si-assistant
    pip install -r requirements.txt
    python -m si_assistant

## Building the installers yourself

The installers are normally made by GitHub (below), but the program can be packaged on
any of the three systems by hand. Use the same environment as above, then:

    pip install "pyinstaller>=6.10,<7"           # packaging tool (GPL with an exception that allows this use)
    python packaging/build_app.py build --dist <output folder> --work <scratch folder>
    python packaging/build_app.py verify --app "<output folder>/Chem SI Assistant"
    python packaging/build_app.py smoke --exe "<output folder>/Chem SI Assistant/Chem SI Assistant.exe"

This makes one folder holding the whole program (about 250 MB; on macOS a `.app`). On
Windows, keep the output and scratch folders on a short path such as `C:\Temp\b` (long
paths are a problem for Windows). `build_app.py self-test --exe … --report r.json
--project <a project folder>` runs the built program's own self-test: every bundled
file, the spelling dictionaries, RDKit, Typst, reading a PDF saved with edit restrictions
(the `cryptography` package), Qt (and that Qt WebEngine, no longer used, was left out), and
compiling that project's SI.

The installers themselves: on Windows, compile `packaging/windows/installer.iss` with
[Inno Setup 6](https://jrsoftware.org/isinfo.php) (see the comment at its top); on macOS
the workflow packs the `.app` into a `.dmg` with `hdiutil`; on Linux it makes an AppImage
with `appimagetool` (`build_app.py appdir` prepares its folder).

**Releases on GitHub** (`.github/workflows/release.yml`): set the new version in
`si_assistant/__init__.py`, add its section to `CHANGELOG.md` (it becomes the release
notes the update check shows), commit, then push a tag with the same version:

    git tag v0.1.0
    git push origin v0.1.0

The workflow checks that the tag and the version agree, builds and tests the program on
Windows, macOS and Linux, makes the three installers and publishes the release (a tag such
as `v0.2.0-beta.1` makes a pre-release). It can also be started by hand from the Actions
tab, to build without publishing.

## Running the tests

    %USERPROFILE%\anaconda3\envs\si-assistant\python.exe -m pytest

The tests also run automatically on GitHub (`.github/workflows/tests.yml`) on Windows,
macOS and Linux. The sample project they compile (`tests/sample_project.py`) is made up:
invented authors, title and data. To test with another dataset, replace that one module
(its `FACTS` list what the tests look for). Real data kept for local testing goes in
`tests/local/`, which is never uploaded.

## Code layout

| Folder | What's in it |
|---|---|
| `si_assistant/core/` | Project data, files, lock, text format — no user interface, fully unit-tested |
| `si_assistant/ui/` | The windows and panels (PySide6 / Qt) |
| `si_assistant/compile/` | Turning the project into the SI PDF with Typst |
| `si_assistant/styles/` | Journal styles (plain Typst templates: `acs.typ`, `wiley.typ`, `rsc.typ`) |
| `si_assistant/data/` | Built-in data for the spelling check: `azman_chemistry_dictionary.txt` (the chemistry dictionary, with its licence beside it), `chem_words.txt` (more chemistry words), `name_reactions.txt` (names of named reactions) and `lab_terms.txt` (hand-made: NMR multiplicities, reagents such as HOBt, supplier and chromatography brand names) |
| `si_assistant/updates.py` | The update check and feedback links (GitHub); `ui/update_ui.py` and `ui/feedback_ui.py` are their windows |
| `si_assistant/resources.py`, `selftest.py` | Where the program's own files are (from the source code or in an installed program); the installed program's self-test |
| `docs/` | The user guide (`user-guide.md`, shown by Help ▸ User guide) and its pictures |
| `packaging/` | Building the installers: `build_app.py` (the steps), `chem_si_assistant.spec` (PyInstaller), `launcher.py`, `windows/installer.iss` (Inno Setup) |
| `tools/` | Developer scripts: `get_chem_dictionary.py` (downloads the chemistry dictionary again), `build_chem_words.py` (rebuilds `chem_words.txt` from a local `Example SIs/` folder, leaving out people's names) |
| `tests/` | Unit tests (`pytest`) |
| `.github/` | Issue forms (bug report, feature request, feedback), the automatic test run and the release workflow |
| `CHANGELOG.md` | What changed in each version |

## Licence

Chem SI Assistant is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version
(GPL-3.0-or-later). It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY. The full licence text is in [`LICENSE`](LICENSE).

## Third-party components

The program is built on the free components below, which keep their own licences; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for details and links.

## Dependencies and licences

| Package | Used for | Licence |
|---|---|---|
| PySide6 (Qt 6) | User interface, PDF preview | LGPL-3.0 |
| RDKit | Structures, formulas, masses, structure drawings | BSD-3-Clause |
| Typst (`typst` Python package) | Typesetting the SI PDF | Apache-2.0 |
| pypdf | Reading page counts of spectra PDFs; the text of HRMS report PDFs and of manuscript PDFs (Manuscript check) | BSD-3-Clause |
| cryptography (with its helper cffi; pip also installs pycparser, which the installers leave out) | Used **only** so that pypdf can open PDF files that their software saved with edit restrictions (many NMR and mass-spectrometry programs do this; anyone can open such a file, but without this package pypdf cannot read it, and those spectra and HRMS reports would be left out). Chem SI Assistant does not encrypt your files and keeps no passwords or keys; reading these PDFs happens on your computer and sends nothing anywhere. A truly password-protected PDF is still reported and left out. | Apache-2.0 or BSD-3-Clause (either may be chosen); cffi MIT-0; pycparser BSD-3-Clause |
| chem-vision (Nathan O'Hare; code adapted) | The Manuscript check's reading of labels, yields, ee/er, dr, footnotes, tables and ChemDraw files | MIT |
| NumPy | Scheme label placement, picture trimming, spectra auto-crop, converting pasted Mnova spectra (metafile pictures, the self-check) | BSD-3-Clause |
| pyspellchecker | Spelling check in the text boxes (English word list included; works offline) | MIT |
| Open Source Chemistry Dictionary v3.0 (Adam M. Azman; *J. Chem. Educ.* 2012, 89, 412, doi:10.1021/ed2002994) | Chemistry words for the spelling check (bundled as `si_assistant/data/azman_chemistry_dictionary.txt`, converted to UTF-8; attribution in `azman_chemistry_dictionary_LICENSE.txt`) | CC BY 3.0 US |
| Crossref REST API (web service) | Filling in references from a DOI | free public API; no account needed |
| PubChem PUG REST (web service, NIH) | "Look up name (PubChem)": only the structure's InChIKey is sent, only when clicked | free public service; no account needed |
| GitHub (web service) | Checking for and downloading new versions; feedback pages | free; feedback needs a free GitHub account |
| Python | Runs the program (bundled in the installers) | PSF License |
| pytest | Tests (development only) | MIT |
| PyInstaller | Packaging the installed program (building only; not part of the program) | GPL-2.0-or-later with the bootloader exception, which allows packaging any program |
| Inno Setup | The Windows installer (building only) | Inno Setup licence (free, also for commercial use) |
| appimagetool | The Linux AppImage (building only) | MIT |
