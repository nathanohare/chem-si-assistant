# Changelog

What changed in each version of Chem SI Assistant. The newest version is at the top.
(The release workflow copies a version's section into its GitHub Release notes, which the
program's update check shows.)

## [Unreleased]

**Compound pages**
- **Duplicate…** (right-click a compound or scheme page in the tree): choose how many
  copies, where they go (at the end of the section, taking the next unused IDs, or right
  after the original) and which fields are copied. Spectra, other attached files, NMR peak
  reports and HRMS report files are never copied. A scheme page is copied with its drawing
  and all its compounds.
- **Purification** field (+ Add field): method, stationary phase and eluent drop-downs that
  learn. It prints "The crude product was purified by …" at the end of the experimental
  procedure (shown greyed in its "Printed as" box, which you can type over), or where a
  general procedure puts **{purification}**.
- The ID table's **Make pages for unused IDs…** makes a blank compound page for every ID no
  compound has yet.
- A more compact header on every compound page: two lines beside the structure, with
  "◐ 1 / 8 required" (hover for what is missing, click to go to it).
- Sharper, slightly larger structure pictures in the Data section's dashboard on screens
  with display scaling.
- The caption under the structure on spectra (e.g. "¹H NMR (CDCl₃, 500 MHz)") has a size
  setting (9 pt unless changed).

**Scheme editor**
- One row at the top of a scheme page (▾ Scheme and the editor's buttons); the name and
  label boxes only show when the scheme prints its label and caption. "+ Product" is under
  the step's last product.
- New scheme pages are drawn with the **scheme editor**: starting materials, then steps (an
  arrow with conditions above and under it, then the products), left to right like the
  printed scheme. Paste a structure into a box from ChemDraw (Ctrl+V), type SMILES or open
  a MOL file; **+ Starting material**, **+ Step** and **+ Product** add boxes and steps.
- Tick **Compound** under a product to give it an ID and its own data page (a tab on the
  scheme page); the IDs follow the boxes, left to right. Starting materials (and other
  boxes) can have an ID label, typed or mentioning a compound elsewhere, with no data page.
  IDs can be dragged under their structures.
- The conditions boxes have the usual text formatting, mentions and citations; structures
  pasted into the box above an arrow are printed small, in the text.
- Arrows are as long as their conditions need. A scheme too wide for the page continues on
  a new row (starting with the arrow) or, if chosen, shrinks to one line. Yields print under
  the products, under the step's conditions, or not at all.
- **Project settings ▸ Schemes** sets the defaults (text size, alignment, arrow lengths,
  scale, yields…); each scheme's **Layout…** can change them for that scheme.
- The scheme editor is now the only way to make a scheme: "One pasted picture" (a whole
  ChemDraw scheme pasted as one picture, with draggable ID labels) was removed. Scheme pages
  made that way open in the scheme editor with their compounds as the products of one step;
  the old picture file is left in the scheme's folder.
- Schemes print without a label or caption by default. Tick **Label and caption** in a
  scheme's Layout… (or Project settings ▸ Schemes) to print "Scheme S1. *name*" and number
  it.
- While one of a scheme's compounds is edited, the live preview shows the whole scheme page.
- No more empty space between the scheme editor and the compound page under it, and no
  more small windows flashing up when boxes or steps are added or removed.
- **Underline** (U, Ctrl+U) in every text box, kept when pasting from Word and printed in
  the SI. It is stored as `__text__` (literal underscores stay escaped, as before).
- The IDs of a row are lined up under its lowest structure (Layout ▸ "Line up compound IDs
  in each row", on by default). Compile SI warns when shrinking a scheme makes its
  conditions smaller than 6 pt.
- A structure in the conditions has its own **Printed size of the picture…** (right-click
  it), and conditions copied into another scheme take their structures with them.
- Fixes found in testing: a new scheme page no longer adds an unnamed compound; Enter in a
  box's ID label or Yield no longer crashes; the editor keeps its full height after an edit
  and a divider shares the page with the compound tabs; the Layout example no longer draws
  the product as a black box; a compound of the page that is not drawn in the scheme prints
  its own structure; a
  compound restored from the Trash goes back in the boxes' order; hand-edited scheme files
  with odd values no longer stop the compile.

**Structures**
- The built-in Ketcher drawing editor is gone (the **Draw…** button and "Edit the
  drawing"). Structures are pasted from ChemDraw or another editor, typed as SMILES, or
  read from a MOL/SDF file. The installers no longer carry the web browser engine Ketcher
  needed, so they are much smaller.
- Structures pasted from ChemDraw are kept as **vector drawings** (SVG on Windows, checked
  against Windows' own drawing of the copy; ChemDraw's PDF on a Mac), so they are sharp at
  any size and their labels stay text in the PDF. If the check fails, the 1200-dpi picture
  is used as before. ChemDraw's original copy is kept beside it. The same applies to whole
  schemes, figures and Attachment sections pasted from ChemDraw. "Re-render pasted
  pictures" also turns older pictures into vector drawings.
- **Same scale everywhere:** the program measures the bond length in each pasted structure
  and prints it so that bonds have the length set in Project settings ▸ Structures (ACS
  1996: 0.508 cm), whatever zoom it was copied at. A tick box there turns it off, and Edit or
  Remove ▸ **Printed size of the picture…** lets you type a picture's bond length or print
  it at its own size.
- Compiling uses the pasted ChemDraw picture where a compound has one and the program's
  drawing otherwise ("Program drawings for every compound" is still a choice).

**Compound page**
- Once a compound has a structure (or a picture), the Structure field shrinks to one line:
  formula, molecular weight, exact mass and the **Edit or Remove** menu. The structure shows
  at the top of the page. **Show all editing controls** opens the field again, and clearing
  the structure opens it by itself.
- The structure at the top of the page is sharp on high-resolution screens.
- While a compound has no structure, clicking the picture at the top of the page and
  pressing Ctrl+V pastes one.
- Less space between the fields, so more of a compound fits on the screen.
- Dropdowns and number boxes no longer change when the mouse wheel turns over them, even
  after clicking them: the wheel always scrolls the page.

**HRMS**
- The HRMS field keeps the instrument's **report files** (any file, also raw-data folders):
  add them, drop them, or paste files copied in File Explorer/Finder. Removing one moves it
  to the project Trash.
- The **found m/z is read from the reports** — PDF reports, CSV/TXT peak lists, Excel
  files, mzML, mzXML, mzData, MGF, JCAMP-DX and netCDF (ANDI-MS) — and fills the Found box
  when it is empty, marked "from the report …". A value you type is never changed; a
  warning shows if it differs from the reports ("is +2.3 ppm from the report's value"). Raw data (Thermo, Agilent, Bruker, Waters,
  SCIEX, Shimadzu) is kept, with a note on how to export or convert it (MSConvert).
- **Print HRMS reports** (Spectra section, off by default; each compound can choose): the
  reports print together in a sub-section "HRMS reports" at the end of the Spectra section.
- Damaged or unusual report files (a broken Excel file, a netCDF file still being written,
  odd JCAMP repeat counts) can no longer stop the compile or use up memory: they get a
  one-line note and a warning. A typed bond length (Printed size of the picture…) is now
  always used as typed, with a note in the window when it gives a very large or small
  picture.

**Names**
- **Look up name (PubChem)** on the Name field fills in the IUPAC name of a compound that
  is in PubChem. Only the structure's InChIKey is sent, only when you click; the name stays
  editable.

**General procedures**
- Two general procedures with the same name (anywhere in the project, ignoring capitals and
  spaces) are marked ⚠ in the General procedures tab and listed in the Warnings panel.
- A scale typed in a procedure's {scale} box, e.g. "0.20 mmol", fills the compound's
  Reaction scale field when that is empty. Once the Reaction scale field has a value, that
  value is printed, and a warning shows if the two differ.

**References**
- The reference list is now the **References** item at the bottom of the tree (always
  there, can't be moved or deleted) instead of a toolbar button. Ctrl+Shift+R and File ▸
  References open it. Adding, editing and deleting references can be undone.

**Manuscript check**
- A new **Manuscript check** item (just above References) compares the manuscript with the
  SI. Add the manuscript as a PDF and/or Word .docx and, best, the ChemDraw files (.cdxml)
  of its schemes. The files are kept in the project's `manuscript/` folder and never
  printed; everything is checked on your computer (nothing is sent anywhere).
- Compound by compound it reports: manuscript IDs with data that no SI compound has
  (compounds only in the SI are not reported; IDs only mentioned, or labelled structures
  without values, are notes); a different structure, stereochemistry or charge (ChemDraw
  files); different yields, ee/er (er is converted to compare) and dr (two ways of writing
  a dr agree within 2 % or at the precision printed: "5:1" = 83:17); a different name
  where the manuscript writes "name (ID)"; and footnotes whose times, temperatures,
  equivalents, mol%, scale or reagents are not in the compound's SI procedure ("23 °C" =
  rt, N2 = nitrogen, overnight = 12–18 h) — or an SI procedure whose reaction uses another
  time, temperature, loading, equivalents or solvent than its general procedure (work-up
  steps aside) with no footnote in the manuscript. It also notices when the manuscript
  disagrees with itself (two yields for one compound).
- Yields, ee and dr in running-text sentences are only notes, quoting the sentence;
  sentences about controls, scale-ups and table entries are skipped.
- IDs keep their stereo prefix (cis-5a and trans-5a are two compounds); "(±)-5", "rac-5",
  "(S,S)-3b" find 5 and 3b in the SI.
- Labels are read from the text under each structure ("3a" with "92%, 95% ee" on the line
  below, or "3a, 92%, 95% ee"), from scope tables (Entry / Product / Yield / ee, also Word
  tables with merged cells and footnotes in their last row; sideways tables in a PDF) and
  from figures pasted from ChemDraw into Word. Text written along a reaction arrow is not
  a label; body text is never taken for a figure label. A PDF whose figures are outlined
  says so.
- Each result says where it was found, with the file type ("Table 1 (Word); Table 1, p. 1
  (PDF)", "Word file, paragraph 4", "Scheme 2.cdxml").
- Double-click a result to open the compound at that field; dismiss results that don't
  apply (with a reason). Dismissed results are hidden until **Show dismissed** is ticked,
  and the summary line counts them. The results also appear in the Warnings panel
  ("Manuscript"). The page keeps room for the results table (the files list is as tall as
  its files, up to eight; the page scrolls in a small window).
- The label and value reading is adapted from Nathan O'Hare's chem-vision (MIT licence).

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
