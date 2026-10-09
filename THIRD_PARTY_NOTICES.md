# Third-party components

Chem SI Assistant is licensed under the GNU General Public License v3 or later (see
`LICENSE`). It is built on the free and open-source components below, which keep their
own licences. The installers include these components unchanged; their full licence
texts are included with each package (in the installed program's library folder) or at
the addresses given.

| Component | What it does in the program | Licence | Where to get it |
|---|---|---|---|
| Qt 6 via PySide6 / Shiboken6 (The Qt Company) | Windows, buttons, PDF preview | LGPL-3.0 (Qt and PySide6 are used unmodified, as separate libraries) | https://pypi.org/project/PySide6/ · https://www.qt.io/licensing/open-source-lgpl-obligations |
| RDKit | Structures, formulas, masses, structure drawings | BSD-3-Clause | https://github.com/rdkit/rdkit |
| Typst (the `typst` Python package, which includes the Typst compiler) | Typesetting the SI PDF | Apache-2.0 | https://github.com/messense/typst-py · https://github.com/typst/typst |
| pypdf | Reading spectra PDFs (page counts), the text of HRMS report PDFs and the text of manuscript PDFs (Manuscript check) | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| cryptography (Python Cryptographic Authority; its compiled part includes OpenSSL, Apache-2.0), with the compiled part of cffi (`_cffi_backend`) | Lets pypdf open PDFs that their software saved with edit restrictions (see below). Nothing else. | cryptography: Apache-2.0 or BSD-3-Clause (dual-licensed, either may be chosen); cffi: MIT-0 (MIT No Attribution; MIT in older versions); pycparser (installed with cffi when running from the source code, not in the installers): BSD-3-Clause | https://github.com/pyca/cryptography · https://github.com/python-cffi/cffi · https://github.com/eliben/pycparser |
| chem-vision (Nathan O'Hare) — code adapted, not bundled | The Manuscript check's reading of compound labels, yields, ee/er, dr, footnote markers, scope tables and ChemDraw (.cdxml) text (`si_assistant/core/ms_parse.py`, `si_assistant/core/ms_read.py`) | MIT (notice below) | https://github.com/nathanohare/Claude-chem-vision |
| NumPy | Picture trimming, scheme label placement, spectra handling | BSD-3-Clause | https://numpy.org |
| pyspellchecker (with its English word list) | Spelling check | MIT | https://github.com/barrust/pyspellchecker |
| Open Source Chemistry Dictionary v3.0, Adam M. Azman (*J. Chem. Educ.* 2012, 89, 412, doi:10.1021/ed2002994) | Chemistry words for the spelling check (`si_assistant/data/azman_chemistry_dictionary.txt`, converted to UTF-8) | CC BY 3.0 US (attribution in `si_assistant/data/azman_chemistry_dictionary_LICENSE.txt`) | https://creativecommons.org/licenses/by/3.0/us/ |
| Python | Runs the program (bundled by the installers) | PSF License | https://www.python.org |
| PyInstaller bootloader | Starts the installed program (part of every installer) | GPL-2.0-or-later with the bootloader exception (allows it to start programs under any licence) | https://pyinstaller.org |
| Inno Setup | The Windows installer and uninstaller | Inno Setup License (free) | https://jrsoftware.org/isinfo.php |
| AppImage runtime (appimagetool) | Starts the Linux AppImage | MIT | https://github.com/AppImage/appimagetool |

Web services (contacted only when you use the feature; nothing about you or your projects
is sent):

- **Crossref REST API** — filling in a reference from its DOI (free public API, no
  account). https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- **PubChem PUG REST** (US National Institutes of Health) — "Look up name (PubChem)" on the
  Name field: the structure's InChIKey (nothing else) is sent, only when the button is
  clicked. https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
- **GitHub** — checking for a new version and downloading it (Help ▸ Check for updates),
  and the "New issue" page for feedback. https://github.com/nathanohare/chem-si-assistant

### Why a "cryptography" package is included

Many NMR and mass-spectrometry programs save their PDF reports with *edit restrictions*:
anyone can open and read the file without a password, but the file is stored encrypted
(usually AES) so that other programs know not to change it. pypdf, which the program uses to count
the pages of spectra PDFs and to read the text of HRMS report and manuscript PDFs, needs
the free `cryptography` package to read such files; without it they would be reported as
"password-protected" and left out of the SI.

That is the only thing `cryptography` is used for: **reading** these PDFs, on your
computer, the same way any PDF viewer opens them. Chem SI Assistant does not encrypt your
files and keeps no passwords or keys, and reading these PDFs sends nothing anywhere. A PDF
that really needs a password to open is still reported as password-protected and left out
(save an unprotected copy, e.g. Print ▸ Save as PDF). `cffi` is a small helper that
`cryptography` needs to run; `pycparser` is installed along with `cffi` when running from
the source code (the installers leave it out).

HRMS report files are read by the program's own code (`si_assistant/core/ms_reports.py`),
written from the published open format descriptions — mzML and mzData (HUPO-PSI), mzXML
(Institute for Systems Biology), MGF, JCAMP-DX (IUPAC), netCDF classic/ANDI-MS (Unidata,
ASTM E1947), Office Open XML for `.xlsx` — with no code copied from other programs and no
vendor libraries. ProteoWizard MSConvert, which the program suggests for converting raw data,
is a separate free program (Apache-2.0) and is not included.

### chem-vision (MIT License)

Parts of `si_assistant/core/ms_parse.py` and `si_assistant/core/ms_read.py` are adapted
from chem-vision (`skills/chem-vision/scripts/annotations.py` and `chemvision.py`):

> MIT License
>
> Copyright (c) 2026 nathanohare
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The manuscript check reads Word figures with the program's own EMF reader
(`si_assistant/core/emf_svg.py`) and ChemDraw structures with RDKit; PyMuPDF (AGPL) is not
used.

The impurity shifts used by the NMR check are facts taken from G. R. Fulmer et al.,
*Organometallics* 2010, 29, 2176–2179 (cited in `si_assistant/core/impurities.py`).
