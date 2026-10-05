# Changelog

What changed in each version of Chem SI Assistant. The newest version is at the top.
(The release workflow copies a version's section into its GitHub Release notes, which the
program's update check shows.)

## [0.1.0] - first release

The first public test version. Everything below is new.

**Projects**
- One folder per SI, kept as ordinary, readable files, so it can live in OneDrive or
  Dropbox, be backed up, and be shared with co-authors. A lock stops two people editing at
  once; File ▸ Snapshot keeps a dated copy; deleted items go to the project's trash first.
- Undo and redo for every change, autosaving as you type.

**Building the SI**
- Sections in any order and depth: text sections, data sections with compounds, schemes,
  attachments (a PDF or picture made elsewhere) and a Spectra section; front matter with the
  cover page, table of contents and General Information.
- A form for each compound: name, structure, yield, physical state, Rf, ee/er, optical
  rotation, melting point, IR, NMR reports (¹H, ¹³C, ¹⁹F, optional ³¹P/¹¹B), HRMS and
  more; fields can be added, removed and reordered, and every value the program fills in
  can be changed by hand.
- Structures from ChemDraw (copy and paste), SMILES, or the built-in Ketcher editor; the
  formula and masses are calculated for you, including the HRMS "calcd" value for the
  chosen ion.
- Compound numbers (IDs) given automatically from an ID table, also in schemes, with
  labels placed under the structures.
- General procedures written once and referred to by each compound.
- Spectra: PDFs or pictures, or pasted straight from Mnova (on Windows kept as a sharp
  vector drawing when possible), arranged on spectra pages with captions and links from
  the NMR text to the spectrum.
- References: add by DOI (filled in from Crossref), by hand, or from BibTeX/RIS; cited in
  the text and listed at the end.
- Journal styles for ACS, Wiley and RSC.
- Compile SI makes the PDF with Typst; a live preview shows the page you are editing.

**Checks (warnings only, never blocking)**
- ¹H integrations against the structure's hydrogens, ¹³C signal counts against its
  symmetry, solvent and impurity peaks, HRMS errors in ppm, missing fields, compound
  numbers, chemistry formatting (subscripts, en dashes, italic *J*), and spelling with a
  chemistry dictionary.

**Program**
- Installers for Windows, macOS (Apple silicon) and Linux (AppImage).
- Checks for new versions on GitHub (can be turned off) and offers to install them.
- Help ▸ User guide, Help ▸ Send feedback… (a pre-filled GitHub page) and "Report this
  problem" after an unexpected error.
