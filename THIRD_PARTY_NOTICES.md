# Third-party components

Chem SI Assistant is licensed under the GNU General Public License v3 or later (see
`LICENSE`). It is built on the free and open-source components below, which keep their
own licences. The installers include these components unchanged; their full licence
texts are included with each package (in the installed program's library folder) or at
the addresses given.

| Component | What it does in the program | Licence | Where to get it |
|---|---|---|---|
| Qt 6 via PySide6 / Shiboken6 (The Qt Company) | Windows, buttons, PDF preview; Qt WebEngine shows Ketcher | LGPL-3.0 (Qt and PySide6 are used unmodified, as separate libraries) | https://pypi.org/project/PySide6/ · https://www.qt.io/licensing/open-source-lgpl-obligations |
| RDKit | Structures, formulas, masses, structure drawings | BSD-3-Clause | https://github.com/rdkit/rdkit |
| Typst (the `typst` Python package, which includes the Typst compiler) | Typesetting the SI PDF | Apache-2.0 | https://github.com/messense/typst-py · https://github.com/typst/typst |
| pypdf | Reading spectra PDFs (page counts) | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| NumPy | Picture trimming, scheme label placement, spectra handling | BSD-3-Clause | https://numpy.org |
| pyspellchecker (with its English word list) | Spelling check | MIT | https://github.com/barrust/pyspellchecker |
| Ketcher 3.18 (EPAM Systems) | Drawing structures | Apache-2.0 (its `LICENSE` is kept in `si_assistant/vendor/ketcher/`) | https://github.com/epam/ketcher |
| Open Source Chemistry Dictionary v3.0, Adam M. Azman (*J. Chem. Educ.* 2012, 89, 412, doi:10.1021/ed2002994) | Chemistry words for the spelling check (`si_assistant/data/azman_chemistry_dictionary.txt`, converted to UTF-8) | CC BY 3.0 US (attribution in `si_assistant/data/azman_chemistry_dictionary_LICENSE.txt`) | https://creativecommons.org/licenses/by/3.0/us/ |
| Python | Runs the program (bundled by the installers) | PSF License | https://www.python.org |
| PyInstaller bootloader | Starts the installed program (part of every installer) | GPL-2.0-or-later with the bootloader exception (allows it to start programs under any licence) | https://pyinstaller.org |
| Inno Setup | The Windows installer and uninstaller | Inno Setup License (free) | https://jrsoftware.org/isinfo.php |
| AppImage runtime (appimagetool) | Starts the Linux AppImage | MIT | https://github.com/AppImage/appimagetool |

Web services (contacted only when you use the feature; nothing about you or your projects
is sent):

- **Crossref REST API** — filling in a reference from its DOI (free public API, no
  account). https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- **GitHub** — checking for a new version and downloading it (Help ▸ Check for updates),
  and the "New issue" page for feedback. https://github.com/nathanohare/chem-si-assistant

The impurity shifts used by the NMR check are facts taken from G. R. Fulmer et al.,
*Organometallics* 2010, 29, 2176–2179 (cited in `si_assistant/core/impurities.py`).
