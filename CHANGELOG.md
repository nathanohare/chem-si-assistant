# Changelog

What changed in each version of Chem SI Assistant. The newest version is at the top.
(The release workflow copies a version's section into its GitHub Release notes, which the
program's update check shows.)

## [1.0.0] - 2026-10-09

**Example SI**
- The program now comes with a finished example: six 1-arylethanols made by one general
  procedure, a follow-up acetylation, two reaction schemes, an optimisation table, HRMS
  reports, references and the compiled SI. The start page shows it in a highlighted box:
  **Open the Example Project…** opens a copy you can change freely, and **View Its Finished
  SI (PDF)** shows the result. Both are also in the Help menu. Its data come from recent
  papers (cited in it); its spectra are simulated from those data and say so on each page.

**Decisions after the code audit**
- A structure with a generic atom (R, X, *) can now be used, for example in a general
  scheme (R = aryl). It has no formula or mass, so it gets a warning and the checks that need
  a formula skip it.
- For a salt, the automatic HRMS "M" skips common counter-ions (halides, BF₄⁻, PF₆⁻, OTf⁻,
  OTs⁻, BArF⁻, Na⁺, K⁺, NBu₄⁺…), so it picks the organic ion; the "M is:" list shows which
  piece it took and you can still choose another.
- A charge typed in the M formula ("C10H16N+") is used for the adduct hint and the
  charge warning.
- The free **cryptography** package is now included. Many NMR and MS programs save their PDF
  reports with edit restrictions, and it is what lets the program read them. It is used for
  nothing else: nothing is encrypted, no keys or passwords are kept, nothing is sent. The
  README, the licence notices, the About box and the user guide explain this.

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

**Reliability and speed (code audit)**

*Faster with large projects*
- Pausing while typing re-checks only the compound you are on, not the whole project, and
  the Warnings panel checks once per autosave instead of twice. Structures, compound IDs
  and figure/table labels are worked out once per check, so warnings, dashboards and the
  Manuscript check keep up in big projects.
- The Data section's dashboard is rebuilt only while it is on show (switching tabs and
  editing the ID table are much faster), the compact dashboard opens faster, and old
  structure pictures no longer pile up in memory during a long session.
- Projects with more than 64 HRMS report files no longer re-read every report at each
  autosave (this froze the window for seconds). Long CSV peak lists and large mzXML files
  are quicker to show and use less memory.
- PDF spectra and reports are read again only when the file changes, and each structure is
  prepared once per compile, so Compile SI and the table-of-contents and Spectra live
  previews are noticeably quicker. The Spectra live preview shows only the first two HRMS
  reports (the HRMS reports page and the SI have them all).
- Also quicker: the References page with hundreds of references, BibTeX/RIS import, typing
  above a JPEG/TIFF figure or a table (no more stutter), the Delete question for a large
  section, duplicating a scheme many times, Save Snapshot, and pasting metafiles (about 3×)
  or Mnova spectra. The spectra page and figure previews use far less memory, and saving
  no longer rewrites unchanged files, so OneDrive has less to sync.

*Never lose work*
- If a page cannot be saved (for example OneDrive or a virus scanner is holding the file),
  you get a plain "Could not save" message instead of a crash report, your typing stays on
  screen, and the save is tried again at the next edit or when you leave the page. This
  covers compound pages, general procedures, texts, front-matter pages, references and
  spectrum settings.
- While a page's latest changes are not saved, the program asks before you leave it, undo,
  redo or close (answer No to stay and copy your text), and commands that change that
  compound wait with a message. A change that could not be saved (a reference edit, a
  warning dismissal, a scheme name) is also taken back on screen, so it is never written
  later without you knowing.
- Moving, deleting or restoring a compound, scheme page or section, and removing or
  replacing a file, now happens completely or not at all: if a file is open in another
  program (e.g. a spectrum PDF in Acrobat), nothing changes and the message says so. A
  failed copy of an HRMS report or raw-data folder is cleaned up, with a message saying why.
- A damaged references.json or front.json is kept as a dated "….damaged-….json" copy before
  a new one is written (and a damaged manuscript dismissed.json as a dated
  dismissed.damaged-….json, never overwritten).
  Text and dictionary files saved by older editors (Notepad "ANSI" or "Unicode") are read
  correctly and saved as UTF-8 next time.
- Project files edited by hand with odd values (numbers as IDs, null, a list where text
  belongs…) no longer stop the project, a page, the dashboard or Compile SI. The project
  opens with a warning, an unusable value is kept in the file as "*name* (could not be
  read)", and empty values never print as "None" in the SI.
- A compound folder copied into another section in Explorer no longer edits the original;
  the copy is skipped with a warning saying which folder to rename or delete.
- When OneDrive or a virus scanner holds a file, the scheme editor (typing conditions, Save
  scheme, its buttons) and Compile SI give a plain message instead of the crash dialog, and
  every kind of page counts typing it could not save: the scheme name, label and
  conditions, an Attachment section's heading and caption and a spectrum's header and
  caption now ask before you leave. Each failure is reported once.
- A change that could not be saved is also taken back on screen: renaming a compound from
  the tree, a scheme-editor change, renaming or moving a section, and moving a compound or
  scheme page (which no longer renumbers the compounds when it fails).
- A structure file that cannot be read just now no longer stops Compile SI or the Preview:
  that structure, its formula and masses are left out with a plain note. The warning icons
  in the tree notice a procedure or notes file changed by OneDrive.

*Undo and Trash*
- Edit ▸ Undo no longer takes away (and later deletes) files you put into a compound's
  folder by hand; it says which files are in the way and changes nothing. If an undo stops
  part-way, your newer files are kept in the Trash with a list of where each came from.
- Undo is fixed after changing the structure of a compound with HRMS reports, after typing
  an ID in the ID table, and in a text box after a mention's number changed. Adding several
  manuscript files, or pasting scheme conditions holding several structures, is one Undo
  step; adding or deleting a scheme compound is named in the Undo menu.
- Putting a Trash item back is all or nothing, and one damaged entry no longer stops the
  Trash window. Restoring an HRMS or raw-data folder is instant (moved, not copied), a
  scheme whose deletion was interrupted can be restored with its compounds, a restored
  structure keeps its ChemDraw picture, and the Kind column reads "Figure", "Table" or
  "Structure (from scheme …)". The Empty Trash question says how long Undo can bring the
  items back (while it is one of the last 50 changes, until the project is closed).

*Compiling*
- A damaged PNG, JPEG or GIF no longer stops the whole SI: it is left out with a note
  naming the file (a picture saved under the wrong extension, or a camera JPEG with stray
  bytes, prints as normal). A file that cannot be read just now (not yet downloaded by
  OneDrive, or open elsewhere) is reported as such, not as "damaged", and never left out
  without a word.
- A password-protected PDF is called that, with how to fix it. Edit-restricted vendor PDFs
  are read (with the included "cryptography" package, see above).
- Files named like Windows devices ("AUX.png", "con.pdf", "COM1") are stored as "AUX_.png"
  etc., and references with "in press", "2010a", full-width digits, odd characters or an
  unusual web address no longer stop the compile. New warning: a reference whose year or
  web address the journal style cannot print is listed instead of quietly dropped.
- The table-of-contents preview leaves out unticked spectra and HRMS reports, so its page
  numbers match the SI; Windows no longer marks the program "Not responding" during a long
  compile; a toolbar Preview no longer deletes the drawings build/SI.typ uses.
- What you see now matches what prints: '[M]+.' prints as [M]⁺•, the scheme editor's yields
  match the printed ones, and the Preview of a scheme-page compound not drawn in a box
  shows its structure.

*Text boxes and tables*
- Cut, Copy and drag-and-drop take exactly the selected text when a paragraph holds an
  emoji or a math letter such as 𝐽 (7.2 Hz no longer becomes .2 Hz), and the spelling and
  formatting checks mark and fix the right letters after one.
- Tables over 200 rows are no longer cut to 200 when reopened. Tables can have up to 5000
  rows and 200 columns; a paste that is too large says what was left out.
- Shift+Enter in a caption no longer joins two words; formatting keys no longer change
  read-only boxes; Mention and Cite no longer insert an unrelated item when nothing
  matches; an "&" in a section, compound, field or file name (e.g. "Synthesis &
  Characterization") shows correctly in menus, tabs and buttons.

*Schemes*
- Renaming a scheme page or its compounds from the tree (F2) sticks; typing on a compound
  tab is no longer lost when you tick "Compound" straight after; removing a "Compound"
  product removes its box; ID labels with * _ ^ or } no longer gain backslashes; choosing
  an unusable picture no longer deletes the box's picture; mentions in a duplicated
  scheme's conditions point to the copy's own compounds.

*Chemistry checks*
- A structure with a generic atom (R, X, *) no longer crashes the program or counts R as
  0 Da: it is drawn, printed and explained ("no formula (generic atom)"), and can be cleared
  or replaced.
- The ¹³C signal-count check no longer gives false warnings for meso and cis/trans ring
  compounds. The ¹H integration check no longer counts D or T, recognises OH/NH drawn as
  atoms in a MOL file, and reads peaks like (s, 9H, C(CH3)3) correctly.
- Spectrum files named after IDs such as 13c or 19f are no longer guessed as ¹³C/¹⁹F
  spectra; "13C{1H}", "13C(1H)" and "31P[1H]" stay ¹³C/³¹P, and HSQC/HMBC files are 2D NMR.
- Dismissed warnings stay dismissed until their data really changes (not, for example,
  when you tick "print reports"), including ones dismissed in an earlier version. New
  warning: text typed in Purification ▸ "Printed as" is not printed when the general
  procedure places {purification} itself.
- Elemental analysis has an optional **Formula** box for the formula the values were
  calculated for (e.g. a solvate); with only Found values, no empty "Calcd for …: ." prints.

*HRMS and spectra*
- Compounds with several Br, Cl, Sn, Pd or Mo atoms always get the monoisotopic "calcd"
  m/z (no more ⁸¹Br/¹¹⁸Sn peak with an error of thousands of ppm).
- Adducts typed with subscripts, superscripts or en dashes ([M+NH₄]⁺, [M+H–H₂O]⁺) are read
  correctly; an isotope in an adduct ([M+⁷Li]+, [M+¹⁰⁷Ag]+) uses that isotope's mass; an
  adduct that cannot be read, or has charge 0, gives a warning; a formula written with
  [2H] matches a structure drawn with D.
- A Found m/z filled from a report is no longer cleared while a file cannot be opened; long
  Excel reports say exactly how many lines were left out; a zipped raw-data folder is no
  longer called an Excel file. A tolerance that is not a sensible number falls back to
  5 ppm, and Project settings takes 0.01–1000 ppm without rounding.
- A spectrum that cannot be read for a moment is read again later instead of being called
  damaged (Auto-crop too). Pasting a spectrum updates the warnings at once and names any
  files not pasted (e.g. an .mnova file).

*Manuscript check*
- "–", "n.d.", "trace" and "nr" in a yield, ee, er or dr column are no longer read as
  values; two warnings about the same footnote letter can both be dismissed; dismissing
  after the manuscript changed reads it again instead of showing "0 compounds checked".
- Adding or removing one file reads only new or changed files; a file that cannot be opened
  for a moment is not remembered as damaged; OneDrive conflict copies of the program's own
  files are not listed as manuscripts.

*Shared folders and other computers*
- If the project is taken over or closed on another computer, or its folder is renamed or
  moved, this window turns read-only and tells you, instead of saving its old copy over
  the other changes. Unsaved typing stays on screen to copy, and Compile SI and Save
  Snapshot no longer write into the project.
- A lock file held for a moment by OneDrive, or a USB/network drive that drops out briefly,
  no longer makes the project read-only; the "project is in use" time is always in English
  with AM/PM.
- A project with files not yet downloaded by OneDrive now opens, with a warning naming
  them; those pages keep their place and IDs. When OneDrive or Dropbox has kept a second
  version of a file ("compound-LAPTOP.json"), opening the project names it.

*Mac, Linux and updates*
- Mac: hints show ⌘V/⌘C, and ⌫ or ⌘⌫ deletes the selected tree item (after asking). macOS
  and Linux: saved files keep their permissions, so shared group folders keep working.
- Linux: View ▸ Interface size ▸ Restart works in the AppImage; PDFs, folders and links
  open with the computer's own libraries; dashboard tooltip pictures work for every user
  of a shared computer.
- Updates: a failed download shows "Update not downloaded" with the release link; Cancel at
  the last moment no longer starts the installer; on Windows, old installers are removed
  from the temporary folder.

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
