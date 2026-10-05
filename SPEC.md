# Chem SI Assistant — Product Specification

*Version 1.0 · Author: Nathan O'Hare · Drafted 2026-09-30*

An open-source desktop program for building the **Supporting Information (SI)** document of a synthetic/organic chemistry paper: collecting compound data in one place, tracking what is missing, catching common mistakes, and compiling everything into a single, journal-formatted PDF.

This document is the source of truth for what to build. It is written for a developer (Claude Code) working with a chemist who is not a programmer. Decisions on the original open questions are recorded in §13.

---

## 1. The problem

An SI for a methodology paper is typically 50–150+ pages, most of it per-compound characterization data and spectra. Today it is assembled by hand in Word, which causes four problems:

1. **Scattered data.** A single compound's data is a mix of text (yield, HRMS, NMR peak lists), structures, images, PDFs and spectra, stored in different places.
2. **No overview.** It is hard to see what is done and what is missing, especially with several people and a long document.
3. **Renumbering is error-prone.** Removing or reordering a compound (e.g. deleting **3g**) means relabelling **3h→3g, 3i→3h…** everywhere: headings, procedures, spectra labels. Mistakes are easy and common.
4. **Formatting is repetitive.** Every compound block and every spectrum page is formatted by hand.

Real errors of this kind are visible in published SIs. For example, one of the reference SIs in `Example SIs/` gives an HRMS "calcd" value with a typo in the formula (of the kind `calcd for C10H41NO [M + H]+: 164.1070` for C10H14NO: the mass is right, the formula is not), and another labels a carbon spectrum "12C NMR". Both would be caught automatically by this program.

## 2. Guiding principles

1. **The Project Folder is the data.** Everything about a project lives in one ordinary folder of human-readable files (JSON, plain text, and copies of uploaded files). A user can browse, back up, move, or version-control it without the program. The program never stores data anywhere else.
2. **Warnings never block.** Error checks draw attention; the user can always dismiss them and proceed.
3. **Labels are added by the program, never baked into uploads.** Users upload unlabelled structures and spectra. Compound IDs and headers are applied at compile time so they always match the current numbering.
4. **Cross-platform.** Must run on Windows, macOS and Linux.
5. **One editor at a time.** Multiple people may share a project (e.g. via OneDrive/Dropbox), but only one may have it open for editing at once (see §4.3). Real-time collaboration is a future feature.
6. **Free and open-source dependencies only.**
7. **Everything automatic can be overridden by hand.** Formulaic parts of the SI are automated, but anything the program fills in (IDs, formulas, masses, auto-added NMR reports, structures taken from schemes, label positions, generated text, drawings…) must have a way for the user to change or replace it. Edge cases too unusual to automate are handled by manual input rather than by blocking the user. When adding a feature, always ask: how does the user override this?

## 3. Recommended technology

The developer may revise these choices, but should explain why if so.

| Concern | Recommendation | Why |
|---|---|---|
| Language | **Python 3.11+** | Best chemistry libraries; the author has some Python experience. |
| Desktop UI | **PySide6 (Qt 6)** | Mature, cross-platform, native look; LGPL. Includes QtWebEngine for embedding the structure editor. |
| Chemistry | **RDKit** | SMILES/MOL parsing, formula, MW, exact mass, symmetry classes, structure images (supports ACS 1996 drawing style). |
| Structure editor | **Ketcher** (EPAM, Apache-2.0), standalone build bundled and shown in a QtWebEngine view | Free, ChemDraw-like web editor; reads/writes MOL and SMILES. |
| Document compilation | **Typst** via the `typst` Python package (bundles the compiler — no separate install) | Replaces LaTeX: no multi-GB TeX install, handles Unicode (δ, °, ¹H, CDCl₃) natively, fast, supports templates, TOC, page numbering, bibliographies. |
| PDF handling | **pypdf** (BSD) for reading page counts/merging if needed; Typst for placing pages | Uploaded PDFs (spectra, Attachment sections) must be placed into the compiled document. |
| Packaging | PyInstaller or Briefcase → installers for Win/macOS/Linux | So end users do not need Python. |

Everything is compiled through **one Typst document** so that the table of contents, page numbers (S1, S2…), cross-references and bibliography are consistent across all sections, including uploaded PDFs.

## 4. Project Folder

### 4.1 Opening and creating

On launch the user can **Open Project** (choose a folder) or **New Project** (choose a location and name; the program creates the folder). A recent-projects list is shown.

### 4.2 Layout (illustrative — exact names up to developer)

```
My Dearomatization SI/
├── project.json            # title, authors, affiliations, journal style, section tree
│                           #   (nesting of sections and sub-sections), settings
├── references.json         # bibliography entries (see §9.5)
├── dictionary.txt          # the project's spelling words, one per line (§13k.1a)
├── .lock                   # present while someone has the project open (see §4.3)
├── front/                  # Front matter pages (§13k.4)
│   ├── general_information.txt   # the General Information text
│   ├── cover_text.txt      # optional extra text under the affiliations on the cover
│   ├── front.json          # General Information's figures: {id: {file, label}}
│   ├── images/             # pictures of General Information's figures
│   └── tables/             # its tables, one t-….json each (§13k.5)
├── trash/                  # deleted items, restorable (one folder per deletion)
├── sections/               # flat: sub-sections are folders here too; nesting is in project.json
│   ├── s-1a2b_materials-and-methods/
│   │   ├── section.json    # type = text (a "custom section"), name, settings, "figures"
│   │   ├── content.txt     # the text, human-readable (!figure{…} / !table{…} paragraphs)
│   │   ├── images/         # figure pictures (+ ChemDraw originals)
│   │   └── tables/         # one t-….json per table
│   ├── s-3c4d_optimization-table/
│   │   ├── section.json    # type = file
│   │   └── optimization.pdf
│   ├── s-9a8b_spectra/
│   │   └── section.json    # type = spectra (one per project): its layout "defaults" (§13k.12)
│   └── s-5e6f_products/
│       ├── section.json    # type = data: ID table, default fields, general procedures, dashboard config
│       ├── general_procedures/
│       │   └── gp-a.txt
│       └── compounds/
│           └── c-7f3a2c_ethyl-ester/
│               ├── compound.json       # all field values, flags, notes, dismissed warnings
│               ├── structure.mol       # canonical structure
│               ├── procedure.txt       # long text fields stored as plain text for readability
│               ├── spectra/            # copies of uploaded spectra
│               └── files/              # copies of other attachments
└── build/                  # generated output only (Typst sources, SI.pdf, crop_cache.json,
                            #   undo/ = the undo history while the project is open, §13k.11);
                            #   safe to delete when the project is closed
```

Rules:

- **Order is stored in JSON, not in folder names.** Reordering or renumbering never renames folders (avoids sync conflicts and broken paths).
- **Folders are named by a permanent internal ID** plus a friendly slug. The *displayed* compound ID (e.g. "3g") is never used in folder names because it changes.
- **Uploads are copied** into the project; the original file on the user's computer is never modified or referenced afterwards.
- **Autosave** on every change. Writes are atomic (write to temp file, then rename) so a crash or sync never leaves a half-written file.
- A **"Save snapshot"** command zips the project (excluding `build/`) with a timestamp, for milestones such as "submitted version".

### 4.3 One-person-at-a-time lock

When a project is opened, the program writes `.lock` containing the user's name, computer name and time. If another user opens the project while a lock exists, they see "Opened by Nathan on npo-laptop since 3:12 PM" and may open **read-only** or **take over** (with a warning). The lock is removed on close. Stale locks (e.g. after a crash) can be cleared by the user.

## 5. Main window

- **Left sidebar — Tree View.** The first item is the **project** itself (its title), which opens the project dashboard (§13k.3). Next is the fixed **Front matter** (Cover page, Table of contents, General Information; §13k.4), which cannot be moved, renamed or deleted. Below it are the **Sections**, including the **Spectra section** (§13k.12), which every project has and which can be moved but not deleted; it shows one page per compound with spectra, automatically. Custom sections can hold **sub-sections** of any type, up to three levels (1, 1.1, 1.1.1; §13k.5). Data sections expand to show their **Compound Pages**. Users can create, delete, rename and drag-to-reorder sections, sub-sections and compound pages (compound pages can also be moved between Data sections). The final SI follows the tree order exactly.
- **Main panel** shows whatever is selected in the tree.
- **Toolbar:** New section, Compile SI, Preview, Warnings panel, Project settings.
- Deleting anything asks for confirmation and moves it to a project-level **Trash** (inside the Project Folder) so it can be restored.

### 5.1 Project settings

Journal style (§9.6), HRMS tolerance (§8), and the user's name (for the lock and flag comments). Title, authors and affiliations are set on Front matter ▸ Cover page (§13k.4) and printed on the SI's first page (an Organic Letters requirement). The spectra layout options (§9.3) are on the Spectra section's page (§13k.12).

## 6. Section types

### 6.1 Custom section (formerly "Text section")

For non-compound text: materials and methods, supplementary notes. (General Information has its own page in the Front matter, §13k.4.) Stored as type `text` in section.json. A custom section can hold sub-sections (§13k.5).

- A text editor supporting **bold, italic, superscript, subscript**, Greek letters and simple headings.
- **Pasting preserves formatting** from Word/MestReNova (read the clipboard's rich-text/HTML form, keep sub/superscript/italic).
- Can insert **images** (copied into the section folder) with captions.
- Can insert **compound mentions** and **citations** (see §7.5, §9.5).

### 6.2 Attachment section

(Called "File section" until §13l.8; stored as section type `"file"`.) For content prepared elsewhere (optimization tables, X-ray reports, computational data).

- Drag-and-drop or browse to upload a **PDF or image** (PNG/JPG/TIFF/SVG), or paste one (Ctrl+V, §13l.8).
- The panel previews the attached file and has **Replace** and **Remove** buttons.
- An optional **heading** (used in the table of contents) and **caption**.
- `.docx` is not supported (§13.2); the user exports a PDF from Word.

### 6.3 Data section

A container for Compound Pages (e.g. "Starting materials", "Products", "Derivatizations"). Its main panel has four parts:

1. **Dashboard** (top) — §6.3.4.
2. **ID table** — §6.3.1.
3. **Default fields** — §6.3.2.
4. **General procedures** — §6.3.3.

A Data section's heading appears in the compiled SI and the table of contents.

#### 6.3.1 Compound ID rules (ID table)

Each Data section has its own numbering so every ID in the project is unique.

- The **ID table** has two columns: **Index** (1, 2, 3…) and **ID** (e.g. 3a, 3b, 3c…).
- A compound's ID is the ID in the row matching its **position** in the section. Reordering compounds therefore re-assigns IDs automatically.
- A **"Fill from pattern"** helper generates the table so the user does not type it by hand: prefix (e.g. `3`) + suffix sequence (a…z then aa, ab… or 1, 2, 3…), with optional starting value. Any cell can then be edited by hand (e.g. to skip a letter).
- **Manual override:** on any compound page the user can type a fixed ID, overriding the table. Overridden compounds show a lock icon. An overridden compound still occupies its position, so the compounds after it keep their table IDs.
- **Duplicate IDs** anywhere in the project (from overrides or table edits) produce a warning.
- If there are more compounds than table rows, extra compounds show "ID?" and a warning. The warning says how to fix it: the section's ID table tab ("Fill from pattern…" or typing the IDs), or a Manual ID on the compound page (a new Data section starts with an empty table, so its first compound shows it at once).

#### 6.3.2 Default fields

The user chooses which fields new compound pages in this section start with (from the field library, §7.2), their order, and which are **required** (required fields drive the dashboard's "complete" status). Changing defaults offers to apply the change to existing compounds. Individual compounds can still add or remove fields.

#### 6.3.3 General procedures

- Each Data section can define **General Procedures** (GP A, GP B…): a name and text, with optional variable placeholders (e.g. `{scale}`, `{starting material}`).
- A compound page can select a General Procedure from a dropdown instead of (or in addition to) writing its own procedure. The compiled block then reads e.g. *"Prepared according to General Procedure A from **1a** (0.20 mmol)…"*.
- General procedures are printed once, at the start of their Data section, under their own headings (included in the TOC).

#### 6.3.4 Dashboard

A customizable overview of every compound in the section.

- **Grid:** one row per compound (ID, structure thumbnail, name), one column per field. The user chooses which columns to show.
- **Cell states** (with colour *and* icon, so colour is not the only cue): filled · empty · empty-but-required · flagged · has warning · field not used for this compound.
- **Summary bar:** e.g. "18 / 24 compounds complete · 5 flags · 7 warnings".
- Clicking a cell opens that compound at that field.
- Filters: show only incomplete / flagged / with warnings.

## 7. Compound Pages

A form for one compound. Every field has a **flag toggle** (§7.4). The header shows the structure, current ID, formula, MW and completion status.

### 7.1 Core fields

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | **Structure** | structure | Paste **SMILES** or **MOL/MOL block**, open a MOL/SDF file, or draw in the built-in **Ketcher** editor. Stored as `structure.mol`. |
| 1a | Calculated values | auto | **Molecular formula, MW, exact (monoisotopic) mass, and expected HRMS m/z** for the adduct chosen from a dropdown (§8.1). Recalculated whenever the structure changes. IUPAC name is **not** generated. |
| 1b | Name | text (optional) | Free text; the user may paste the name from ChemDraw. |
| 1c | Structure image override | file (optional) | Lets the user upload a ChemDraw-drawn image to use in the SI instead of the RDKit/Ketcher drawing. |
| 2 | **ID** | auto / override | From the Data section's ID table (§6.3.1), or manually overridden. |
| 3 | **Experimental procedure** | rich text | Written or pasted; may select a General Procedure (§6.3.3); supports compound mentions (§7.5). |
| 4 | **Reaction scale** | number + unit | mmol (default). Used by the yield check. |
| 5 | **Yield** | % and mass (with unit) | Both fields; consistency checked (§8). |
| 6 | **Physical state** | text | e.g. "colorless oil", "white solid". |
| 7 | **NMR peak reports** | rich text, repeatable | ¹H and ¹³C by default. A **¹⁹F** field is added automatically if the structure contains F (³¹P for P and ¹¹B for B are each optional, set in Project Settings — §13.3). Each report may be repeated with a label (e.g. "major rotamer", "minor rotamer"). Pasted text from MestReNova is parsed for nucleus, frequency, solvent, shifts and integrations. |
| 8 | **HRMS** | structured | Ionization (ESI, APCI…), adduct (dropdown), **found m/z**. Calculated m/z and ppm error are shown automatically. |
| 9 | **Spectra** | file list | Upload PDFs/images of spectra. Each item is tagged with a type (¹H, ¹³C, ¹⁹F, 2D, HPLC…) and goes to the Spectra section (§9.3, §13k.12). Each also has an optional header (empty = automatic) and an optional caption under the structure (empty = automatic). **View spectra page** opens the compound's page under the Spectra section (§13k.12). |
| 10 | **Additional files** | file list | Other attachments (HPLC traces, photos, screenshots). Each has a caption and a placement setting: *inline with compound*, *Spectra section* (stored as `appendix`; §13k.9), or *don't include in SI*. Files that should only be stored (raw data etc.) go in the optional **Store additional files and folders** field (§7.2, never printed). |
| 11 | **References** | citation list | For known compounds (§9.5). |
| 12 | **Notes** | plain text | Private notes/comments; **never** printed in the SI. |

### 7.2 Field library (optional fields)

Users can add any of these to a compound or to a Data section's defaults:

- Melting point · Rf (with eluent) · IR · Optical rotation [α]D (value, c, solvent, temperature) · ee/er with chiral HPLC/SFC conditions (column, eluent, flow rate, wavelength, retention times of both enantiomers — required by ACS for non-racemic compounds) · d.r. · Elemental analysis (calcd/found) · Other NMR nuclei · X-ray/CCDC number.
- **Custom field:** the user defines a label and type (short text, long text, number with unit, file).

### 7.3 Text formatting helpers

Applied when compiling (the stored text is not altered): `1H`→¹H, `13C`→¹³C, `CDCl3`→CDCl₃, italic *J*, *δ*, en-dashes in ranges, "Hz" spacing. Rules live in the journal style (§9.6).

### 7.4 Flags

Every field has a **flag** toggle with an optional comment (e.g. "re-run 13C, poor S/N"). Flags show on the compound page, in the dashboard, and in the project-wide Warnings panel. Flags are never printed in the SI.

### 7.5 Compound mentions

In any text (procedures, text sections, captions) the user can insert a link to another compound (e.g. type `@` and pick from a list). It is stored as a reference to the compound's internal ID and **printed as that compound's current ID**, so renumbering updates every mention automatically. Deleting a mentioned compound produces a warning at each mention.

## 8. Calculations and error checks

All checks produce **warnings** that are dismissible per compound (dismissal is saved, with optional reason). A dismissed warning re-appears if the underlying data changes.

### 8.1 Mass calculation

- Monoisotopic mass from RDKit; ion mass = M ± adduct ∓ electron mass (0.000549 Da).
- Adduct dropdown: [M+H]⁺, [M+Na]⁺, [M+K]⁺, [M+NH₄]⁺, [M]⁺, [M]⁺•, [M−H]⁻, [M+Cl]⁻, [M+HCOO]⁻, with room for custom adducts.
- Printed to 4 decimal places as e.g. *HRMS (ESI) m/z: [M+H]⁺ calcd for C₁₂H₁₄NO 188.1070; found 188.1073.*

### 8.2 Checks

| # | Check | Detail |
|---|---|---|
| 1 | **HRMS error** | ppm error between found and calculated m/z exceeds the user-set tolerance (default **5 ppm**; Org. Lett. limit is ±10 ppm). Also warn if the ion formula would not match the structure. |
| 2 | **¹³C signal count** | Number of reported ¹³C shifts vs. expected unique carbons (RDKit topological symmetry classes). Approximate by design — C–F coupling, rotamers and diastereotopicity can make it differ; message says so. |
| 3 | **¹H integration** | Sum of integrations vs. H count. Message notes possible exchangeable NH/OH. |
| 4 | **Solvent/impurity peaks** | Reported shifts that match common solvent and impurity residuals for the solvent parsed from the peak report (reference table: Fulmer et al., *Organometallics* 2010, 29, 2176), within a tolerance. |
| 5 | **Yield consistency** | % yield vs. mass yield, using scale and MW (allow ±1%). |
| 6 | **Spectra present** | A peak report exists but no matching spectrum is attached (and vice versa). |
| 7 | **NMR frequency consistency** | ¹³C frequency ≈ ¹H frequency × 0.2515 (e.g. 400 → 101 MHz); ¹⁹F ≈ × 0.941. |
| 8 | **Missing heteroatom NMR** | Structure contains F (P, B…) but no corresponding report. |
| 9 | **IDs** | Duplicate IDs, missing IDs, or broken compound mentions. |
| 10 | **Required fields** | Empty required fields (shown on the dashboard rather than as individual warnings). |

A project-wide **Warnings panel** lists all warnings and flags, grouped by section and compound, each clickable.

## 9. Compiling the SI

**Compile SI** turns the project into `build/SI.pdf` (and keeps the generated Typst source so advanced users can inspect it). A **Preview** compiles a single compound or section quickly.

### 9.1 Document order

1. **Cover page:** "Supporting Information", manuscript title, authors, affiliations, corresponding-author email.
2. **Table of contents** (automatic, with page numbers).
3. **All sections in tree order.** Text and Attachment sections print their content. Each Data section prints its heading, then its General Procedures, then one **compound block** per compound in order.
4. **Spectra** — all spectra (and any files set to "Spectra section") for all compounds, in the same order the compounds appear in the tree. Since §13k.12 they are printed where the Spectra section sits in the tree (at the end by default).
5. **References** (automatic bibliography).

Page numbers use the journal's SI style (e.g. **S1, S2, …**) on every page, including pages taken from uploaded PDFs.

### 9.2 Compound block

A compact, consistent block per compound, following the selected journal style. Default (ACS-like) layout:

> **Ethyl 2-methylfuran-3-carboxylate (3g).** *[structure image]*
> Prepared according to General Procedure A from **1g** (0.20 mmol). Purification by flash chromatography (10% EtOAc/hexanes) afforded **3g** as a colorless oil (28.4 mg, 92%). **R*f*** = 0.35 (30% EtOAc/hexanes). **¹H NMR** (400 MHz, CDCl₃) δ 7.25 (d, *J* = 2.0 Hz, 1H), … **¹³C NMR** (101 MHz, CDCl₃) δ 164.2, … **HRMS** (ESI) *m/z*: [M+H]⁺ calcd for C₈H₁₁O₃ 155.0703; found 155.0701.

*(Illustrative layout only; the shift values are placeholders.)*

Fields that are empty or removed are simply omitted. Inline attachments are placed after the text with their captions. The block is kept together on one page where possible.

### 9.3 Spectra appendix and labelling

- Each spectrum gets a **header added by the program**: compound ID + experiment + conditions, e.g. **"3g — ¹H NMR (400 MHz, CDCl₃)"**. Conditions are taken from the matching peak report.
- The **compound's structure image and ID** are placed on the spectrum (a corner chosen on the Spectra section's page, default top-left inside the plot area; or dragged anywhere on the compound's spectra page), satisfying the Org. Lett. requirement that spectra be labelled with structure and compound number.
- Layout option: **one, two or four spectra per page**, portrait or landscape (Org. Lett. requires each spectrum to be at least half a page). All layout options are on the Spectra section (§13k.12).
- Spectra appear in the appendix in this order: compounds in tree order; within a compound, ¹H, ¹³C, ¹⁹F, other nuclei, 2D, then other files.
- Uploaded multi-page PDFs are placed page by page.

### 9.4 Table of contents

Generated automatically from section headings, General Procedure headings, the spectra appendix and references. Optionally lists each compound (setting).

### 9.5 References

- References live in `references.json`. Users add a reference by **pasting a DOI** (metadata fetched from Crossref when online) or by entering it manually; import from BibTeX/RIS (e.g. exported from Zotero) is supported.
- References can be cited in a compound's References field (printed as *"Spectral data matched those reported in the literature.¹²"* or similar, per style) and in any text section.
- Citations are numbered by order of first appearance and collected into the References section using the journal's citation style (Typst supports CSL styles).

### 9.6 Journal styles

- The user picks a style in Project Settings. Ship at least: **ACS** (Org. Lett., J. Org. Chem., JACS), **Wiley** (Angew. Chem., Chem. Eur. J.), **RSC**. A style controls: page size and margins, fonts, page-number format, compound block layout and field order, heading style, citation style, spectra layout, and text formatting helpers.
- Styles are plain template files in a `styles/` folder so new journals can be added (or a lab's own style made) without changing the program.

## 10. Org. Lett. checklist coverage

The *Organic Letters Data Preparation Checklist* (in `Example Author Guidelines/`) maps onto the features above:

| Checklist item | Covered by |
|---|---|
| Compound numbers/structures consistent with manuscript; SI order matches manuscript | Tree order + auto-IDs (§6.3.1), mentions (§7.5) |
| Title and authors on first page; pages numbered; TOC; single combined file | Compile (§9.1) |
| Intro section: techniques, instruments, suppliers, citations for known compounds, hazards | Front matter ▸ General Information (§13k.4) |
| Reactant quantities, purification, product quantities, isolated yields, physical state | Compound fields (§7.1) + yield check |
| HRMS within ±10 ppm; formula matches structure | Checks 1 (§8.2) |
| ¹H and ¹³C atoms accounted for | Checks 2–3 |
| Spectra labelled with structure and compound number; ≥ half page | Spectra appendix (§9.3) |

## 11. Out of scope for version 1 (future ideas)

- Real-time multi-user editing.
- Automatic IUPAC names.
- Reading raw NMR data (FIDs) or generating spectra images.
- Two-way sync with the manuscript Word file (e.g. updating compound numbers in the manuscript).
- Exporting compound blocks to Word for pasting into the manuscript.
- Importing compounds in bulk from a spreadsheet/SDF (a strong candidate for v1.1).

## 12. Build plan

Build and test in phases; each phase should produce something the author can open and use.

| Phase | Deliverable |
|---|---|
| **1. Skeleton** | Create/open Project Folder, lock file, Tree View with add/rename/delete/reorder, Text and File sections, autosave, project settings. |
| **2. Data sections & compounds** | Compound pages with the field system (core fields, field library, add/remove), default fields, ID table with "fill from pattern", auto-ID on reorder, manual override, file attachments, notes, flags. |
| **3. Chemistry** | SMILES/MOL input, RDKit calculations, adduct dropdown, structure images, heteroatom-NMR auto-fields. |
| **4. Dashboard** | Customizable grid, cell states, filters, summary bar. |
| **5. Compile v1** | Typst pipeline: cover page, TOC, sections in order, compound blocks, spectra appendix with program-added labels and structures, S-page numbering, one ACS style. |
| **6. Error checks** | All §8.2 checks, Warnings panel, dismissal. |
| **7. Rich features** | Ketcher editor, General Procedures, compound mentions, references/bibliography, additional journal styles, paste-with-formatting. |
| **8. Release** | Installers for Windows/macOS/Linux, user guide, example project, open-source licence. |

**Testing:** build a sample project from one of the example SIs (several compounds, a General Procedure, spectra) and use it as a regression test for compilation. Unit-test all calculations (masses vs. known values, ID assignment on reorder, each error check).

## 13. Decisions on the former open questions (answered by Nathan, 2026-10-01)

1. **Overridden IDs** still use up their position in the ID table. **Duplicate IDs are always flagged**, both in the ID table itself (e.g. after hand-editing it) and between compounds anywhere in the project. Comparison ignores upper/lower case and surrounding spaces.
2. **Word files** are not supported. Attachment sections and attachments accept PDFs and images only.
3. **¹⁹F** is always added automatically when the structure contains F. Automatic **³¹P** and **¹¹B** fields are each an **option in Project Settings** (off by default).
4. **Licence:** GPL (GPL-3.0-or-later).
5. **Name:** "SI Assistant" for now. Keep the name in one place in the code (`APP_NAME`) so it is easy to change. (Renamed "Chem SI Assistant" in §13l.5.)

## 13a. Implementation decisions (approved by Nathan 2026-10-01)

1. **Stored text format.** Formatted text is saved in `.txt` files with a minimal markup so it stays readable outside the program: `**bold**`, `*italic*`, `^{superscript}`, `_{subscript}`, and lines starting `# ` / `## ` for headings (e.g. `**¹H NMR** (400 MHz, CDCl_{3})`). Backslash escapes literal `* ^ _ } # \`.
2. **Your name is a per-computer setting**, not saved in `project.json`, so each person sharing a project keeps their own name (it is shown in Project Settings ▸ You). The recent-projects list is also per-computer. Neither is project data.
3. **Snapshots** are saved inside the project at `snapshots/<date>_<label>.zip` (excluding `build/`, `.lock` and earlier snapshots).
4. **Trash** lives at `trash/` inside the project; each deleted item has its own folder with an `entry.json` describing where it came from. Replacing or removing an attached file also sends the old file to Trash.
5. **Renaming a section or compound does not rename its folder** (the folder keeps its original slug), to avoid sync conflicts.
6. **Paste** into rich-text fields keeps only bold/italic/superscript/subscript/headings (fonts, colours and sizes are dropped). This brings part of the Phase 7 "paste-with-formatting" feature forward.
7. **Deferred:** images and compound mentions/citations inside Text sections move to Phase 7 (they need the compound and reference systems).

## 13b. Phase 3 conventions (approved by Nathan 2026-10-01, item 1 extended)

1. **Isotope pattern (ChemDraw-style).** The HRMS field lists the ion's main isotope peaks with relative abundances, e.g. bromobenzene [M+H]⁺: 156.9647 (100.0%), 158.9627 (97.3%), 157.9681 (6.5%), 159.9660 (6.3%). This agrees with ChemDraw to within 0.0001 Da; ChemDraw's values are uniformly ~0.3 ppm higher, most likely from slightly different atomic-mass tables. The found m/z is compared with the **nearest** peak, so a value reported on e.g. the ⁸¹Br peak passes, and the formula is labelled (C9H10⁸¹BrO2). If no peak is within tolerance, the error is reported against the monoisotopic peak. The **monoisotopic mass** uses the most abundant isotope of each element (¹²C, ¹H, ³⁵Cl, ⁷⁹Br, ¹¹B…), or the labelled isotope if the structure has one (D is written as "D" in formulas, e.g. C8H5D3O2). **Ion m/z** = (sum of atom masses − z × 0.000549) / |z|.
2. **Adducts are calculated from their label**, not just looked up, so any custom adduct in the form `[M+Li]+`, `[2M+Na]+`, `[M+H−H2O]+` or `[M+2H]2+` works. `[M]+` and `[M]+•` give the same mass (the structure as drawn, minus one electron).
3. **The HRMS field's adduct** is the one used for the calculated m/z. The ppm error is shown next to it and marked ⚠ if it is outside the Project Settings tolerance. This is a preview of check 1 in §8.2; the full Warnings panel comes in Phase 6.
4. **Auto-added NMR reports are only ever added, never removed.** For example, if F is drawn by mistake and then removed from the structure, the empty ¹⁹F report stays until deleted. Phase 6 will warn about reports that don't match the structure.
5. **Replacing a structure overwrites it.** Only **Clear** sends the old structure to the Trash, so that typing several SMILES in a row doesn't fill the Trash.
6. **Structure drawings** use RDKit's ACS 1996 style. On screen they are scaled up to fill the box; the compiled SI (Phase 5) will use true ACS bond lengths.
## 13c. Reaction schemes and pasted images (agreed with Nathan 2026-10-01)

1. **Paste image** on the Structure field uses a picture from the clipboard (e.g. copied from ChemDraw) in the SI instead of the program's drawing. Formula and masses still come from the structure.
2. **Reaction scheme** is an optional field (add it per compound, or tick it in a section's Default fields). **Paste scheme** takes a scheme copied from ChemDraw. ChemDraw puts both the picture and the structures on the clipboard, so the program stores the picture and makes the **right-most structure** the compound's structure. This is done by reading the copied structures, not by recognising them from the image. It happens automatically only if the compound has no structure yet (or its structure came from a scheme); otherwise **Use right-most structure** applies it. The structure can always be changed by hand.
3. A scheme from an image file or screenshot carries no structures, so the structure is set by hand. Recognising structures from pictures (e.g. DECIMER/MolScribe) is a possible future feature.
4. **When compiling (Phase 5), a compound with a scheme shows the scheme picture instead of the structure drawing.**
5. A **Reaction scheme** checkbox in the compound page header turns the scheme on or off (turning it off keeps the picture). The scheme always sits at the very top of the page.
6. **Pasting works like PowerPoint's "Paste as Picture".** ChemDraw puts a *vector* picture on the clipboard (an Enhanced Metafile on Windows, a PDF on macOS). The program renders it to a high-resolution PNG (600 dpi; 1200 dpi by default since §13k.6) and trims the white margins. Ordinary images and screenshots are pasted as they are. This applies to both **Paste scheme** (Ctrl+V also works on the scheme box) and **Paste image**.
7. **Click-to-select paste targets.** Clicking anywhere in the Structure or Scheme box selects it: its picture gets a blue outline and the hint reads “▶ Ctrl+V pastes here”. Ctrl+V then pastes only into the selected box. On the Structure box it pastes the copied structure, or a picture if the clipboard has no structure (e.g. a screenshot). Right-clicking either box gives the same paste commands. Typing in the SMILES box keeps normal text pasting.

## 13d. Dashboard (Phase 4)

1. The dashboard replaces the Phase 2 "Overview" tab. Fixed columns are ID, structure thumbnail, name and required-field progress; then one column per field, chosen with **Columns** and remembered in `section.json` along with the **Show** filter.
2. Each cell shows an icon *and* a colour: ✔ filled (green), ○ empty, ✖ empty-but-required (red), — not used (grey), ⚑ flagged (orange), ⚠ warning (yellow). The colours can be changed per project since §13k.3. It also shows a short summary of the value (e.g. "92% · 28.4 mg", "¹H ¹³C (empty: ¹⁹F)", "155.0701 (Δ −1.3 ppm)").
3. Warnings so far are ID problems and HRMS out of tolerance; Phase 6 adds the remaining §8.2 checks.
4. **Split NMR & spectra by nucleus** (a checkbox, remembered per section) replaces the NMR and Spectra columns with one column per nucleus (¹H NMR, ¹³C NMR, ¹⁹F NMR…) and per spectrum type (¹H spectrum, ¹³C spectrum, 2D NMR…). A spectrum column is ✖ when the compound has an NMR report for that nucleus but no spectrum, and — when it doesn't use that nucleus at all.

## 13e. Scheme pages (agreed with Nathan 2026-10-01; replaces the per-compound scheme field of §13c)

1. A **scheme page** lives inside a Data section, among its compounds. It has a picture of the reaction scheme and its compounds as **sub-pages** (tabs on the scheme page, children in the tree, rows in the dashboard). It has **no ID and no data fields** of its own.
2. Its compounds take the next IDs from the section's ID table **in tree order** (e.g. 3a, then a scheme with 3b and 3c, then 3d). Renumbering works as everywhere else.
3. A sub-compound has a Structure field like any compound (used for formula, masses, checks), but **its structure is not printed**: the scheme is. In the SI, the scheme is printed as "Scheme S*n*. <name>", followed by its compounds' blocks.
4. **Change to reaction scheme…** on a compound page makes a new scheme page at that position, with the compound as its first sub-page. New Scheme Page (toolbar) adds an empty one.
5. **Paste scheme** (or Ctrl+V on the scheme box) stores the picture and the structures ChemDraw copies with it. **+ Add compound** can start a sub-compound from any structure in the pasted scheme. If the scheme has one sub-compound without a structure, it gets the right-most structure.
6. **ID labels on the scheme**: each sub-compound's structure is matched to a structure in the pasted scheme. The atoms are then lined up with the picture (best-fit scale and offset), and the ID is placed under its structure. Any label can be **dragged**; dragged labels are never moved automatically. Labels that can't be placed (no match, picture-only scheme, poor fit) go along the bottom, highlighted, for the user to drag.
7. Folders stay flat: a scheme's compounds sit next to the other compounds in the section's `compounds/` folder, and `scheme.json` lists which belong to it. This keeps paths well inside Windows' 260-character limit; the program warns (once) when a project is in a very deep folder and long-path support is off.

## 13f. HRMS: salts and manual formulas (agreed 2026-10-01)

1. If the structure has several pieces (e.g. a salt), the HRMS field offers **"M is:"** the whole structure or any piece. The default is the largest piece (e.g. the pyridinium cation). Choosing a charged piece fills in [M]⁺/[M]⁻ as the adduct (still editable). A warning appears if an already-charged M is given an adduct that adds charge.
2. **M formula** can be typed by hand (the neutral M formula; the adduct is still applied). It overrides the structure and the piece choice, and is shown highlighted with ✎ and the formula subscripted.

## 13g. Compiling (Phase 5)

1. **Compile SI** makes `build/SI.pdf` (plus `SI.typ`, `style.typ` and `assets/`), via Typst. Pages are numbered S1, S2…; S1 is the cover (title, authors, affiliations, corresponding email), on its own page since §13k.4, and the table of contents starts on S2.
2. Each compound block: **Name (ID).**, the structure (unless it belongs to a scheme), the procedure, then one data paragraph: physical state and yield, R<sub>f</sub>, mp, IR, [α]<sub>D</sub>, ee/er, d.r., NMR reports (labels such as "major rotamer" go into the conditions), HRMS (calcd for the matched isotope peak), elemental analysis, CCDC, custom fields. Inline attachments follow as figures.
3. **Override:** the optional **SI text** field replaces a compound's generated text exactly as typed. **Fill with generated text** starts it from the program's version (SPEC §2.7).
4. **Structures:** at compile time the user chooses ChemDraw/own pictures where available, or the program's ACS 1996 drawings for every compound (true ACS bond length, 0.508 cm). Pasted pictures keep their resolution (§13k.6), so they print at ChemDraw size.
5. **Text formatting** (on by default, can be turned off when compiling): formula subscripts (CDCl₃, Na₂SO₄, Et₃N, DMSO-*d*₆) everywhere; in NMR text also ¹H/¹³C superscripts (only before "NMR" or a decoupling brace, so "2H" integrations are untouched), italic *J*, en-dashes in ranges, "400 MHz" spacing.
6. **Spectra appendix:** compounds in tree order; within a compound ¹H, ¹³C, ¹⁹F… then other files set to "appendix". Each spectrum gets a header such as "2 — ¹H NMR (400 MHz, CDCl₃)", with conditions taken from the matching NMR report. The compound's structure and ID sit in the chosen corner of the spectrum itself. Multi-page PDFs are placed page by page, with later pages marked "(continued)". One or two spectra per page.
7. **Preview** compiles just the selected compound, scheme page or section into `build/preview.pdf` and shows it in a window.

## 13h. Layout conveniences (agreed with Nathan 2026-10-01)

1. **Scheme pages** have a **▾ Scheme** button to collapse the picture area. A scheme page opened by selecting one of its compounds in the tree starts collapsed; opened from the scheme itself, it starts expanded.
2. **Save scheme** marks a scheme as done (stored in scheme.json). This hides the paste hint and buttons, disables Ctrl+V and label dragging, and puts Edit / Replace / Remove into an **Edit or Remove** menu.
3. **Structure boxes** do the same automatically once a structure (or picture) is in. The buttons go into **Edit or Remove** (also on right-click), the SMILES box becomes read-only, and Ctrl+V is off until "Show all editing controls" is chosen.
4. **Field boxes collapse** (▾/▸ or click the title) to one line with a summary such as "✔ 92% · 28.4 mg". **Collapse all / Expand all** are on every compound page. Collapsed fields stay collapsed when moving to the next compound, for the rest of the session.
5. **Compact dashboard** (checkbox, remembered per section): colours and symbols only, no thumbnails or summaries. Hovering over a compound's ID or name shows its structure.

## 13i. Error checks (Phase 6)

All are warnings: never blocking, dismissible per compound with an optional reason. A dismissal stores a fingerprint of the data the warning was about, so **the warning comes back if that data changes**.

| Check | Rule |
|---|---|
| IDs | Duplicate or missing IDs (§13.1). |
| HRMS | ppm error outside the tolerance (vs. the nearest isotope peak); unreadable adduct or formula; an adduct that adds charge to an already-charged M; a typed M formula that differs from the structure's. |
| ¹³C count | Listed signals (a "(2C)" note still counts as one; J values in brackets are ignored) vs. symmetry-distinct carbons (RDKit canonical ranks). Message says it is approximate. |
| ¹H integration | Sum of "(…, nH)" integrations vs. the structure's H count. If the difference equals the number of NH/OH/SH hydrogens, the message says so. |
| Solvent/impurity | A reported ¹H **singlet** at exactly (±0.005 ppm) a known impurity singlet, or a ¹³C peak within ±0.03 ppm of a distinctive impurity/solvent peak, for the solvent named in the report (Fulmer et al., *Organometallics* 2010, 29, 2176; checked by Nathan). The table and both tolerances can be viewed and edited per project in **Project settings ▸ NMR impurities** (untick, edit, add or remove rows; "Reset to built-in table"). An unedited table is not stored, so the project follows the built-in one. |
| Yield | mass ÷ (scale × MW) vs. the reported % (±1%). Units converted (mg/g/µg, mmol/µmol/mol). |
| Spectra present | An NMR report without a spectrum of that nucleus, or a spectrum without a report. |
| NMR frequency | Other nuclei vs. the ¹H frequency × ratio (¹³C 0.2515, ¹⁹F 0.9408, ³¹P 0.4048, ¹¹B 0.3209…), ±3%. |
| Heteroatom NMR | The structure has F (and P/B if those settings are on) but no report with data for it. |

Warnings appear under the field they are about on the compound page (each with **Dismiss…**), in the dashboard, in the tree, and in the **Warnings panel** (toolbar, Ctrl+Shift+W). The panel lists every warning and flag by section and compound; double-click opens the compound at that field, and dismissed warnings can be shown and restored. Required-field gaps stay on the dashboard (check 10). Broken compound mentions and citations, and general-procedure problems, are covered in §13j.

## 13j. Phase 7: drawing, general procedures, mentions, references, styles

1. **Stored text** (extends §13a.1): a compound mention is `@{c-…}` (the compound's permanent internal ID), a citation is `@cite{r-…,r-…}`, and a picture paragraph in a Text section is `!image{images/x.png} Caption`. In the editor, mentions and citations are coloured "chips" that behave like one character: a mention shows the compound's current ID, and a citation shows e.g. [Fulmer et al. 2010]. Type **@** or use **@ Compound** to mention, and use **Cite** to cite. Copying and pasting between the program's text boxes keeps links. NMR report boxes have no links.
2. **Mentions** print as the compound's current ID in bold. If the compound is deleted, the mention shows "??" and gives a warning (under the field, and in the Warnings panel) until the compound is restored. Deleting a compound that is mentioned elsewhere lists where it is mentioned.
3. **General procedures** live in a Data section's "General procedures" tab. Each has a name ("General Procedure A"), an optional title, its text (printed once at the start of the section under the heading "General Procedure A: Title", in the TOC), and a **compound sentence**. The default sentence is "Prepared according to {GP} from {starting material} ({scale})."
   - On a compound's Experimental procedure field, choose the GP. Each {placeholder} in its sentence gets a box:
     - {GP}, {scale}, {yield} and {product} fill themselves in, and anything typed in their box replaces the automatic value (§2.7).
     - Other placeholders, such as {starting material} or {temperature}, are typed in, with mentions allowed.
     - A placeholder left empty prints as [name] and gives a warning.
   - The field's own text (e.g. purification) follows the sentence in the same paragraph.
   - A compound may use a GP from any section.
   - A deleted GP goes to the Trash; compounds using it keep the choice and get a warning.
4. **References** are kept in `references.json` and managed in the **References** window (toolbar).
   - Add one by DOI (filled in from Crossref, needs internet), by hand, or by importing BibTeX/RIS. Duplicate DOIs are not added twice.
   - Cite references in any text, or with the **References (known compound)** field, which prints "Spectral data matched those reported in the literature." followed by the citations; the sentence is editable.
   - Only cited references are printed, numbered by first citation, in a References section at the end. Typst's built-in citation styles are used: ACS, Angewandte (Wiley) and RSC.
   - A citation of a deleted reference gives a warning and is left out.
5. **Ketcher drawing:** **Draw…** on the Structure field (also "Edit the drawing" in its Edit or Remove menu) opens Ketcher 3.18 (EPAM, Apache-2.0) with the current structure. The drawing is saved as the compound's MOL file.
   - Ketcher is downloaded once (~35 MB) into `si_assistant/vendor/ketcher`, either by `python -m si_assistant.ketcher_setup` or when Draw… is first used. The Phase 8 installers will include it.
   - If the compound has its own picture, the program asks whether to remove it so the SI uses the new drawing.
6. **Journal styles:** `acs.typ` (US letter, ACS citations), `wiley.typ` (A4, numbered headings, Angewandte citations) and `rsc.typ` (A4, numbered sans-serif headings, "Electronic Supplementary Information", RSC citations). All have the same 6.5-inch text width, so pictures and spectra are sized identically.
7. **Pictures in Text sections:** use **Picture…**, or paste one. It is copied into the section's `images/` folder, and its caption is typed on the line under it. Pictures print as numbered figures (Figure S1…). *(Now figures with labels, plus tables: see §13k.5.)*

## 13k. Phase 7b: Nathan's review list (agreed 2026-10-03, from `Comments.md`)

Build order: quick items (6, 9, 10, 7, 2a) → front matter and custom sections (4, 5, 5a) → live preview (8) → spectra section (12) → spelling and formatting (1) → project dashboard (3) → undo (11, last, so it covers everything). Phase 8 (installers) follows.

**1. Text checks (all rich-text boxes).**
- **1a. Spelling.** English dictionary (pyspellchecker, MIT) plus a built-in chemistry word list (solvents, reagents, abbreviations, NMR/MS/TLC terms, name fragments).
  - Misspellings get a red wavy underline. Right-click offers suggestions, **Add to dictionary** (saved in the project's `dictionary.txt`, so co-authors share it), **Add to my dictionary** (this computer, all projects) and **Ignore**.
  - Skipped so compound names don't light up: words with digits, brackets or locants; short all-capital abbreviations; links; the Name field; NMR report text.
- **1ai. Formatting check.** Chemistry formatting problems get a blue wavy underline with a right-click **Fix**:
  - a formula without subscripts (PdCl2);
  - a number glued to its unit (1.2mmol, 8.0Hz);
  - a hyphen in a number range (7.36-7.29 → en dash);
  - an un-superscripted nucleus before "NMR" (1H NMR);
  - un-subscripted deuterated solvents (CDCl3).
- **1b. Chemical formula toggle.** A toolbar toggle in every text box, on by default and remembered on this computer. While on, numbers straight after letters in formula-like words are subscripted as you type (H2SO4 → H₂SO₄).
  - Not affected: S1, C18, SN2, E2, 3a, figure/table labels.
  - Ctrl+Z undoes an unwanted subscript.
  - Compiling still applies its own formatting helpers to anything left over.
- *As built:*
  - **Where.** Every rich-text box gets both checks, including table cells, captions and the cover text. NMR report boxes get the formatting check only (no spelling). The private Notes box gets spelling only (it is never printed). The Name field is a one-line box and is not checked.
  - **Underlines only change the look.** They are drawn on screen and never change the saved text. Only choosing a suggestion or a Fix edits the text, as one Ctrl+Z step.
  - **Word list.** `si_assistant/data/chem_words.txt` is made by `tools/build_chem_words.py`. It collects words the English dictionary does not know that appear at least twice in the example SIs, plus the program's own field and solvent words, plus two short hand lists (lab and reaction words; instrument makers, suppliers, software and name reactions). Words the PDF reader glued together ("areaction") or cut at an apostrophe ("couldn") are left out. Re-run the tool after adding example SIs. Systematic names built from name fragments (di·chloro·meth·ane, tetra·hydro·furan, thiophen·e, prop·anol, butyl·lithium, diisobutyl·aluminum) are also accepted, so common reagent names need no list.
  - **Skipped words.** Words with digits, brackets, commas or Greek letters; hyphenated words with a one-letter part (N-, n-, t-); all-capital words up to 6 letters; words with a capital inside (EtOAc, iPrOH; since §13l.6 only abbreviations made of element symbols and groups); links; web addresses, DOIs and e-mail addresses; single letters. Other hyphenated words are checked part by part.
  - **Dictionaries.** The project dictionary is `dictionary.txt` in the Project Folder, one word per line, sorted. "My dictionary" is `my_dictionary.txt` in this computer's application-data folder. Both are read without regard to capitals. Lines starting with "#" are comments and are kept when a word is added. A file saved in another encoding (Notepad "ANSI", Excel "Text") is read as Windows-1252; a dictionary file can never stop a project from opening. The right-click entries read "Add “word” to this project's dictionary", "Add “word” to my dictionary (all projects)" and "Ignore “word” until the program is closed", with tooltips. Plurals of dictionary words (+s, +es) are accepted. "Ignore" lasts until the program is closed. Adding a word updates every open text box. A read-only project cannot take new words, but "my dictionary" still can.
  - **English is US English** (pyspellchecker's dictionary), so British spellings such as "colour" are underlined until added to a dictionary.
  - **Right-click** also offers "Fix all n formatting problems in this text".
  - **Number + unit.** Long units (mmol, mL, mg, Hz, MHz, min, °C, equiv, ppm…) are flagged after any number. Single-letter units are flagged only for g, h and s after a decimal number (1.5g) and for L, M and K after any number, so compound IDs such as 3g or 12h are not flagged. "5mol%" is fixed to "5 mol %" (ACS style). A "92%" yield is not flagged.
  - **Formulas.** One rule decides what counts as a formula, both for the check, for the toggle and for compiling. It needs two units with a digit. Units are elements, groups (Me, Et, Ph, Boc, Cp…), alkyl groups with an i/n/s/t prefix (iPr2NEt, nBu4NI, tBu3P), and lower-case ligands inside brackets (Pd2(dba)3, Ni(cod)2, Ru(bpy)3Cl2, [Ir(cod)Cl]2; "pin" also after B: B2pin2). Not formulas: SN1/SN2/SE2, TS1, IC50/CC50/ID50/LD50, PS1/PS2, OD600, SI1, common protein, cell-line and virus names (CDK2, PI3K, CYP2D6, HSP90, HCT116, NIH3T3, COVID19…), and anything with one unit (S1, C18, E2).
  - **One-element molecules** (H2, D2, N2, O2, F2, Cl2, Br2, I2, O3) are also atom labels ("1H, H2", "N2-alkylation", "Pd1–Cl2", "D2 receptor"), so they count only in context. Cl2, Br2, I2 and O3 count unless written next to a dash or prime. H2, D2, N2, O2 and F2 count only when the words around them show the gas or reagent: "under N2", "purged with N2", "under an atmosphere of N2", "H2 (1 atm)", "H2 (balloon)", "an O2 atmosphere", "N2-filled", "O2-free". So NMR assignments such as "(d, 1H, H2)" are printed as typed, as before this stage. When the next word decides it ("an N2 atmosphere"), the toggle subscripts N2 when that word is finished.
  - **Keeping a word as written** (SPEC §2.7). Right-click on a blue underline offers "Leave “CDK2” as written in this project" (for formulas and solvents) and "Ignore “…” until the program is closed" (for every kind). "Leave as written" adds the word to the project's `dictionary.txt`: it is then not underlined, not subscripted by the toggle and not formatted when compiling (compiling reads only the project's dictionary, not "my dictionary", so every co-author's copy prints the same). "Ignore" only hides the underline and stops the toggle for this session; compiling still formats it.
  - **Number ranges.** Not flagged: chains with more than one hyphen (CAS numbers 7647-01-0, dates 2024-10-04, ISBNs, notebook codes NPO-2-117), numbers stuck to a word before them (C2-3) and numbers followed by letters that are not a unit (6-31G, 6-311+G). Compiling NMR text uses the same rule.
  - **Deuterated solvents** written as names (DMSO-d6, acetone-d6, THF-d8, DMF-d7, MeOD-d4…; since the review of §13l in any capitals, e.g. Methanol-d4, Acetone-d6, dmso-d6, kept as written; chloroform-d gets an italic *d*) are fixed to an italic *d* with a subscript number; CDCl3, CD3OD, C6D6 and similar are fixed like formulas.
  - The underlines of a text box are forgotten when its page is closed, so a dictionary change only re-underlines the text boxes on show (it once also called every closed one, an error each, growing through the session).
  - **Chemical formula toggle.** It is the "H₂" button on every text-box toolbar. One setting is shared by all boxes and stored on this computer. A word is subscripted when it is finished by a space, punctuation, Enter or Tab. Pasted text is left alone (compiling still formats it). Digits that are already subscripts stay as they are.

**2a. Header and footer** (Project settings ▸ Page).
- Left, centre and right text for both header and footer, with fill-ins {page}, {pages}, {title}, {authors}, {date}.
- Page-number options:
  - prefix (default "S");
  - style (1 / i / I);
  - font size and bold;
  - whether the cover page shows a number.
- "Page S1 of S20" is written as `Page {page} of {pages}`.
- The default is the current footer: page number centred.
- *As built:*
  - An unchanged footer follows the journal style: ACS and RSC centre the number, Wiley puts it on the right. Only a changed footer is saved.
  - Size and bold apply to the {page}/{pages} numbers. The size is "Same as the text" (a tick box, the default) or its own size of 5–24 pt.
  - The cover page shows no header, footer or number unless "Show the page number (and header and footer) on the cover page" is ticked. It still counts as page S1.
  - {date} is the compile date, e.g. "3 October 2026". {authors} is the author names separated by commas. Header/footer text gets the same formula formatting as other text (CDCl3 → CDCl₃).
  - The tab shows a live example of page 2 of 20.

**3. Project dashboard.** Clicking the project (the top item of the tree) shows:
- overall and per-section progress;
- a chart of missing required fields by field;
- warnings and flags by type;
- a grid of every compound in every section, with a column chooser.

You can switch between chart types (bars, pie/donut) and change the status colours (filled, empty, missing, flagged, warning, not used). The colours are stored with the project and also used by the section dashboards.
- *As built:*
  - **The project item** is the first item in the tree, above Front matter, named by the manuscript title (it follows the Cover page title). It cannot be moved, renamed or deleted, and nothing can be dropped into it; a section dropped on it goes first, after the front matter. It has no live preview. Opening a project still selects the first section, as before.
  - **The page** has an overview at the top (it scrolls) and the grid below; the divider between them can be dragged. The overview shows:
    - a headline: "12 / 20 compounds complete · 86% of required fields filled · 7 warnings · 2 flags";
    - **Compounds complete**: complete vs not complete;
    - **Data sections**: every Data section, nested ones with their path ("Substrate Synthesis › Amide Substrates"), each with a bar (solid: compounds complete; light: required fields filled), the counts, and its warnings and flags. Clicking a name opens the section. A long path is shortened from the front ("… › Amide Substrates"); the tooltip and the grid's Section column show it in full. Names are always shown exactly as typed (a name such as "ee < 90%" is never read as formatting);
    - **Missing required data, by field**: how many compounds miss each required field, shown against the number of compounds ("27 of 28", and "27 of 28 compounds (96%)" on hover), because one compound can miss several fields, so the fields do not add up to a whole. NMR and spectra are counted per nucleus ("¹³C NMR" for an empty ¹³C report, "¹³C spectrum" for a ¹³C report with no spectrum), so a compound whose NMR field counts as filled can still be listed here;
    - **Warnings and flags, by type**: active warnings per check (HRMS, ¹H integration, solvent peaks…, plus project-wide ones such as duplicate labels), and one "Flagged by a person" entry. These are the same warnings as the Warnings panel. A reference cited but no longer in the References list is counted under "Missing references" wherever it is cited (a compound's text, a custom section, a general procedure or General Information).
  - **Percentages** of progress (the headline, the donut centre, each section's "% of fields") are rounded down, so 100% means nothing is missing (1199 of 1200 is 99%). A share in a donut legend is never shown as 0% for something that is there or 100% for something that is not everything.
  - **Narrow windows.** The boxes sit side by side when there is room and one above the other when there is not, so the overview never needs scrolling sideways; in a short window the grid gets the larger share when the page first opens.
  - **Charts** are bars (largest first, value at the end) or donuts (a legend with each value and its share of the total — or, for missing data, "n of N compounds"; past eight slices the smallest are grouped as "Other"). Hovering shows the value. The choice is stored in `project.json` (`project_dashboard.chart`). They are drawn by the program itself, so there is no new dependency. Bars use the status colours (missing: red, warnings: yellow, flags: orange); donut slices use a fixed colour-blind-safe set with its own dark-mode steps.
  - **The grid** is the section dashboard's grid with every compound of every Data section and a **Section** column. Columns, Show filter, Compact and Split by nucleus work as on a section's dashboard and are remembered in `project.json` (`project_dashboard`: `columns`, `filter`, `compact`, `split_nuclei`). Clicking a cell opens that compound at that field. A hand-edited `columns` value that is not a list is ignored (all columns are shown).
  - **Status colours** (**Colours…**): one colour per status, or "No colour", with a sample cell and "Use the standard colours". Stored in `project.json` as `status_colors: {status: "#rrggbb" or ""}`, only those that differ from the standard ones. Cells show a see-through tint of the colour (the same strength as before), so text stays readable in light and dark mode; charts use the colour itself. The symbols (✔ ○ ✖ ⚑ ⚠ —) always stay. The section dashboards use the same colours, and their legend now shows each symbol on its colour. A read-only project cannot change them.
  - **A new project** (no compounds yet) shows "No compounds yet. Add a Data section with “New section”, then compounds to it." in the missing-data box, without the note about counting.
  - **Refreshing.** The page is counted afresh whenever it is shown (and by **Refresh**). With about 150 compounds it opens in about 0.5 s; the structure pictures in the ID tooltips are now drawn only when first hovered (this also makes the section dashboards open faster).

**4. Front matter.** A fixed item at the top of the tree; it cannot be moved or deleted. It has three pages:
- **Cover page:** title, authors, affiliations, corresponding emails and an optional free text, moved here from Project settings. It is on its own page.
- **Table of contents:**
  - starts on a new page after the cover;
  - title, depth (sections / sub-sections / general procedures / compounds);
  - lists of figures, tables and schemes, each on/off;
  - dot leaders, font size.
- **General Information:** a large text editor. It starts on a new page after the TOC and is stored in `front/general_information.txt`. Pictures, tables, mentions and citations are allowed.
- *As built:*
  - Selecting "Front matter" itself shows a short overview with a button for each page. Nothing can be dragged into it, and it cannot be renamed, moved or deleted.
  - Title, authors and affiliations moved from Project settings (the Manuscript tab is gone). They keep their old keys in project.json, so older projects open unchanged.
  - **Cover heading:** if you leave it as it is, it follows the journal style: "Supporting Information", or "Electronic Supplementary Information" for RSC. Only a changed heading is saved. The optional extra text is saved in `front/cover_text.txt`.
  - **TOC depth:** the options are sections only / + sub-sections / + general procedures (the default, as before) / + every compound. The old "List each compound" setting is used until the TOC page is saved.
  - Headings typed inside a text are listed with the sub-sections.
  - A list of figures, tables or schemes with nothing in it is left out, with a note. Font size is "Same as the text" or 6–16 pt; while "Same as the text" is ticked, the size box shows the journal style's text size (ACS and Wiley 11 pt, RSC 10.5 pt), so unticking it starts there.
  - The cover and the TOC each end their page. Sections follow General Information directly, without a page break.
  - **General Information:** its printed heading can be changed, or left empty for no heading. An empty page is not printed. Its heading is not numbered, so in Wiley/RSC the first real section is "1."; tick "Number this heading like a section" on the page to print "1. General Information" instead (stored as `"general_information": {"numbered": true}`).
  - Older projects keep their "General Information" custom section. **Use as General Information…** (right-click the section, or Edit menu) moves its text and pictures there, then sends the section to the Trash. Text already on the page is kept, and the section's text is added after it. Sections with sub-sections are refused. While the front matter page is empty and such a section exists, the page shows a note pointing to this command.
  - On the Cover page, a table cell still being typed in is saved when you switch page, compile, preview or close. Its long notes wrap, so the page fits beside the live preview at 1200×800.
  - **New Project** asks for a name that becomes the folder name and, until changed on the Cover page, the manuscript title; the question says both.
  - A hand-edited `project.json` with a wrong kind of value for `cover`, `general_information`, `authors`, `affiliations` or `title` (e.g. text where a list belongs) never stops the program: the value is ignored (rows that are not author entries are skipped).
  - Preview works on each front matter page. The TOC preview shows the whole document after it, so the page numbers can be filled in (the live preview pane, §13k.8, lays it out the same way but shows only the TOC pages). It is laid out as the SI is, with the Compile window's choices; the cover is not laid out, but when the SI has one the page count starts at 2, so the numbers (and the TOC's own page, S2) match the compiled SI.

**5. Sections and sub-sections.**
- "Text" sections become **custom sections**. They can hold sub-sections (any type, including Data sections) up to three levels deep: 1, 1.1, 1.1.1.
- Everything can be rearranged by drag-and-drop. Folders stay flat on disk; the nesting is stored in project.json.
- A custom section is one flowing document: text, **Figures** (a picture or a pasted ChemDraw scheme, with caption) and **Tables**.
- Tables appear in the text as a block; double-click opens the table editor. The editor has:
  - rich-text cells (bold, italic, sub/superscript, compound mentions);
  - add/remove rows and columns;
  - merged cells and header rows;
  - footnotes under the table;
  - pasting a table from Word or Excel.
- Structures inside tables are left for a later dedicated table editor.
- *As built (sections):*
  - project.json stores `"section_tree": [{"id", "children": [...]}]`. Projects with the old flat `"section_order"` open unchanged and are converted when next saved.
  - If a sub-section was saved somewhere it isn't allowed (inside a File/Data section, or a 4th level), it is moved up to sit after its parent, so nothing is lost.
  - Only custom sections hold sub-sections. The program explains why when a move or new sub-section isn't allowed.
  - **Adding and moving:** *New sub-section ▸* (toolbar "New section" menu, Edit menu and right-click) adds one inside the selected custom section. Drag a section onto a custom section to put it inside. Right-click also has *Move into section ▸* and *Move out to the top level*. Move Up/Down (Ctrl+↑/↓) works among sections at the same level.
  - Deleting a section sends it and everything inside it to the Trash as one item. Restoring puts it all back in place, or at the top level if its parent has gone.
  - Deleting is all or nothing: folders are renamed into the Trash (never copied then deleted). If one can't be moved (e.g. a PDF open in Acrobat or being synced), the ones already moved are put back, nothing is deleted, and a message says why. The Trash also lists a deletion that stopped part-way (e.g. a crash) so it can still be restored.
  - In the tree, hovering over a section shows its full name, type and level.
  - **Printed headings:** each section's heading level is its depth (1, 1.1, 1.1.1). Wiley and RSC number only these section headings, plus the Spectra section's heading, numbered like any section at its place in the tree (item 12). The General Information heading is unnumbered unless chosen (item 4).
  - General procedure headings, compound titles and headings typed in a text sit one level below their section and are never numbered.
  - Compound titles use the style's `si-compound-title`, so they look the same at any depth.
  - A Preview of a section includes its sub-sections, printed as if it were at the top level.
- *As built (figures and tables, in custom sections and General Information):*
  - **Stored as:** a paragraph `!figure{f-…} Caption` or `!table{t-…} Caption` in the text. A figure's picture is in the owner's `images/` folder and listed in its JSON (`section.json`, or `front/front.json` for General Information) as `"figures": {id: {"file", "label", "width"?}}` ("width" = a printed width typed in cm). A table is `tables/t-….json` (columns' alignment, rows of cells `{text, colspan, rowspan, align}` with `null` for covered cells, header-row count, footnotes, label, optional `"breakable": true`).
  - A new picture never gets the same file name (whatever its extension) as another picture in the folder, and a file that is itself a figure's picture is never taken for another figure's ChemDraw original. Only pasted pictures (and copies of figures that have one) keep a vector original; a picture file chosen with Figure… or "Replace picture with a file…" never picks up a same-named .pdf/.emf next to it.
  - Older `!image{…}` pictures are still read; they become figures (same file, new ID) when the project is opened for editing (before its undo history starts, §13k.11) and when a section deleted by an older version comes back from the Trash (as part of that step). Not when a page is shown: Undo shows the page again, which would make a new step every time, so Undo could never get past it. "Use as General Information" copies figures and tables with their IDs, so mentions keep working.
  - **In the editor:** toolbar **Figure…** (from a file) and **Table…**, straight after the text formatting buttons (on a narrow page the toolbar's » menu takes the symbols, @ Mention and Cite first; the paragraph-style box is never cut short); pasting a picture also works: a screenshot, or a ChemDraw scheme (its vector picture, which Qt does not see as an image, is read from the system clipboard, drawn at the project's resolution and its original kept). Pasting HTML with tables (Word, web pages) keeps the text and turns each table (of 2+ cells) into a table of the text in its place; a table on its own (e.g. cells from Excel) opens in the table editor first; a single cell pastes as text. A figure shows its picture at about printed size with its label under it; a table shows its label, a preview of the grid (cells and footnotes with their sub- and superscripts and italics, so "CH₂Cl₂" and a footnote mark "ᵃ" look as printed) and "▼ Caption of Table S1, on the next line (printed above the table)". The caption is typed on the line under the object.
  - A figure or table always keeps a paragraph of its own: Backspace in front of one (or Delete at the end of the line above it) does not join it to the text above, typing in front of one starts a new line above it, and pasting one into a line splits the line. If a paragraph does end up holding text and objects (e.g. a selection deleted across them), it is saved as separate paragraphs, so nothing is lost.
  - Figures and tables can only be pasted into custom sections and General Information. Elsewhere (compound texts, procedures, the cover text) they are left out with a message, and stay on the clipboard to be pasted in the right place.
  - **Right-click:** Edit label…, Printed size… (figures: width in cm, empty = the picture's own size; never wider than the text), Replace picture (file or copied picture), Remove (to the Trash, with its caption; restoring puts it back at the end of the text). Tables also have Edit table… (or double-click).
  - Remove is undone with Edit ▸ Undo (Ctrl+Z once the text box has no typing left to undo; the text box's own typing history ends at the removal, because the file is already in the Trash): the picture or table and its paragraph come back together, in their place (§13k.11). After the project has been closed, the Trash restores it. Restoring puts it back at the end of the text, or, if its paragraph is still in the text with nothing behind it, back in that paragraph under the same ID. A figure or table whose picture or table file is missing is listed in the Warnings panel ("Figures, tables, schemes and texts").
  - **Table editor:** a grid, with the selected cell's text edited in a box under it (bold, italic, sub/superscript, symbols, @ mentions, citations); typing on a cell replaces it, Enter/Tab move on, Delete clears. Buttons add/delete rows and columns, merge/split cells, align cells (or whole columns), paste a table (HTML from Word/Excel, keeping merged cells and formatting, or tab-separated text; the table grows as needed). Below: caption, footnotes (one per line), number of header rows, label, and "May run over a page break". Cancel changes nothing; if anything was changed (or the table is new and already has text, e.g. pasted from Word), Cancel/Escape first asks "discard your changes?".
  - **Copy and paste:** cutting and pasting a figure or table moves it (same ID, so mentions stay right); copying makes a new copy with a new ID, also into another section.
  - **Printed:** figures as Typst figures (kind `si-figure`), tables as booktabs-style tables (rules above and below the header and at the bottom, no vertical lines) with the caption above and footnotes under the table, aligned with its left edge. A table is kept whole, moving to the next page if it does not fit where it is, unless it is taller than a page (measured by Typst, not counted in rows) or "May run over a page break" is ticked; then it continues on the next page with its header rows repeated. Formatting helpers apply to cells, captions and footnotes; mentions and citations in them work.
  - The TOC's "List of tables" lists these tables (fixed: it now targets Typst's table figures).

**5a. Figure, table and scheme labels.**
- Numbered automatically in document order: "Figure S1", "Table S1", "Scheme S1". Any label can be typed over.
- Project settings set the default rule for each kind: label word, prefix ("S" or none) and number style (1 / I / i / a).
- Identical labels are flagged.
- Text can mention a figure, table or scheme like a compound, so "see Table S2" stays right after reordering.
- *As built:*
  - **What is counted, in order:** General Information, then every section and sub-section in tree order. Figures = pictures in custom sections and General Information, and files a compound shows "Inline with compound"; tables = tables in those texts; schemes = scheme pages. Leaving something out in the Compile window does not renumber the rest (like compound IDs).
  - **Project settings ▸ Labels:** word, prefix and numbers (1, 2, 3 / I, II / i, ii / a, b) for each kind, with an example and "Reset to the journal style's labels". The figure word follows the journal style unless changed ("Fig." for RSC). Only changes are saved (`"labels"` in project.json).
  - **Typing over a label:** right-click a figure or table ▸ Edit label…, the Label box on a scheme page, or the Label box of an inline file. A typed-over item still uses its number, so typing one label never renumbers the others.
  - The program prints its own labels (Typst numbering is off), so the PDF always matches the editor, and the TOC lists of figures/tables/schemes show them. Captions read "**Figure S1.** Caption" (ACS), "**Figure S1:**" (Wiley), "**Fig. S1**" (RSC).
  - **Same label twice** (compared ignoring case and spaces, across kinds): the labels and mention chips show in orange, and the Warnings panel has a new group "Figures, tables, schemes and texts" (double-click opens the page). That group also lists mentions or citations in section/General Information/procedure texts and table cells whose target was deleted ("??"); in compound texts these stay compound warnings.
  - **Mentions:** "@" (and the **@ Mention** button, formerly "@ Compound") lists compounds, figures, tables and schemes in groups. Stored as `@{f-…}`, `@{t-…}`, `@{k-…}`; shown and printed as the label (not bold; compound IDs stay bold). Labels in the editor follow unsaved edits; chips in other texts update when the text is saved.
  - Inline files get an ID (`"id"` in the file's entry) so they can be mentioned too; older ones get one when the compound is next saved.

**6. Pasted pictures at 1200 dpi** (Project settings, 600 or 1200; default 1200).
- No extra smoothing pass at 1200 dpi (it would use too much memory).
- ChemDraw's original vector picture (EMF on Windows, PDF on macOS) is kept next to the PNG, so it can be re-rendered later ("Re-render pasted pictures" in Project settings).
- Pictures pasted before this change stay at 600 dpi unless pasted again.
- *As built:*
  - The original is saved with the same name as the picture, e.g. `structure_image.png` + `structure_image.emf`, or `scheme.png` + `scheme.pdf`. Removing a picture sends both to the same Trash item, and restoring brings both back.
  - Very large pictures (over about 100 megapixels) get a slightly lower resolution so memory isn't exhausted. The PNG records its true resolution, so it still prints at ChemDraw size.
  - Qt normally refuses to open pictures over 256 MB (about 67 megapixels, e.g. a full-page scheme at 1200 dpi); the program raises this limit to 1 GB. On screen, large pictures are shown from a reduced copy (schemes at most 3000 pixels across; thumbnails are kept in memory until the file changes), so pages stay quick. Compiling reads the resolution straight from the PNG file.
  - "Re-render pasted pictures" is on Project settings ▸ Output, next to the resolution. It shows a progress bar with a Stop button (stopping part-way is safe: each picture is replaced whole) and reports how many pictures were redrawn, and how many have no saved original and were left alone.
  - EMF originals can only be redrawn on Windows; PDF originals can be redrawn on any system.
  - Pictures pasted into custom sections and General Information also use the chosen resolution, and (since §13k.5 was built) keep their originals too, so they are re-rendered with the rest.

**7. Structure drawing settings** (Project settings ▸ Structures, with a live sample). Applies to the program's ACS-style drawings (PDF and screen):
- bond length (default ACS 0.508 cm) and line width;
- font and size;
- black or coloured atoms;
- terminal CH₃ labels;
- stereo labels;
- explicit H on heteroatoms.
- *As built:*
  - The defaults reproduce the earlier ACS 1996 drawings exactly: black atoms, built-in Roboto font at 10 pt, no CH₃ labels, no stereo labels, H shown on heteroatoms.
  - Fonts offered are the two built-in ones plus Arial, Helvetica, Liberation Sans, DejaVu Sans, Calibri and Times New Roman, where installed. A font missing on another computer falls back to the built-in one.
  - On screen, drawings are enlarged to fill their box, keeping the same proportions of font and line width to bond length.
  - The sample is drawn at its printed size.
  - The tab also has the "When compiling, use: pictures / the program's drawings" choice (the same setting as in the Compile SI window).
  - Settings left at the defaults are not saved, so the project follows the ACS defaults.

**8. Live preview.** A collapsible pane on the right of every page except a Data section's overview: front matter pages, custom/attachment sections, compound pages, scheme pages, spectra pages.
- It shows only that page, compiled with Typst about 1 second after an edit is saved.
- Page numbers are placeholders (the real ones appear in the full compile).
- The Preview button still compiles a whole Data section.
- *As built:*
  - **Shown for:** Cover page, Table of contents, General Information, custom sections (with their sub-sections, printed as if at the top level), Attachment sections, compound pages and scheme pages. Hidden for a Data section's overview and the "Front matter" item itself. On a scheme page it follows the tree: the scheme item shows the whole scheme page; picking one of its compounds (tab or tree) shows that compound.
  - **Spectra are left out** of the live preview of compounds, schemes and sections (the toolbar Preview still shows them). The Spectra section's own page previews its first 8 spectra, with a note; each compound's spectra page previews all of that compound's spectra pages (item 12).
  - **When it updates:** about 1 s after a page saves an edit (its autosave), with bursts combined into one compile; shortly (0.15 s) after another page is selected; and when the page is rebuilt (e.g. after Project settings or References change). Only one compile runs at a time; an edit saved meanwhile is compiled as soon as it finishes.
  - **Never freezes the window:** the Typst source is written on the main thread (quick), then Typst runs in a worker thread (the typst package releases Python's lock — checked).
  - **Nothing is added to the Project Folder:** previews are built in a temporary folder (deleted when the project closes), which also works for read-only projects. Only if the project is on another drive from that folder does a writable project build in `build/live/` instead (a read-only one then shows a note). The PDF alternates between `live_a.pdf` and `live_b.pdf`, so the one on show is never overwritten while open.
  - **In the pane:** "Live preview" with its state (Updating… / Up to date · n notes, hover for the notes / ⚠ Not updated), zoom −/+ and Fit (width), and ▸ to hide it. The scroll position and zoom are kept when the same page is updated. Typst problems are shown in the pane (above the last good preview, or in its place), never as pop-ups; a page with nothing to print says "Nothing to show yet". A note under it says page numbers are placeholders (the normal S-numbering, starting at S1).
  - **Hiding it:** ▸ in the pane, the "Live preview" toolbar button, View ▸ Live preview or Ctrl+Shift+L collapse it to a thin "Live preview" strip on the right (click it to show it again). Whether it is shown, and its width, are remembered on this computer. The page in the middle always takes the room the strip leaves, also when the program starts with the preview hidden or it is hidden/shown on a page without a preview.
  - **Table of contents:** laid out with the whole SI after it (so its page numbers are filled in), but the pane shows only the TOC's own pages (a marker at the end of the TOC tells which; the rest is cut off). The program's structure drawings and HRMS calculations are kept between builds, so updating this page stays quick for large projects (about 0.15 s of preparation for 150 compounds once they have been drawn).
  - **The toolbar Preview** lets go of `preview.pdf` before writing the next one (and when its window is closed), so a second Preview no longer makes "preview (2).pdf" with an "open in another program" message.
  - **Fits at 1200×800:** with the pane open, the page in the middle gets about 490 px; the cover page, scheme pages (their buttons wrap onto a second line) and compound pages (see item 9; the Structure field's buttons are in one column and its picture box is 300 × 220 px) fit in it, so the tree keeps its width from page to page.
  - **Deleted pages:** if the page on show is deleted (e.g. a scheme's compound tab is closed), the preview follows what the tree now shows; a page that no longer exists says so in plain words.

**9. File fields.**
- The Additional Files field explains that "Inline" and "Spectra section" files are printed, and "Don't include" files are only stored. For storage, use the renamed **Store additional files and folders** field (formerly "Private files & folders"); it is never printed.
- *As built:*
  - The storage field is optional (added with "+ Add field"); when a compound doesn't have it, the note in Additional files says so and has an "add it now" link.
  - Each attached file takes two lines: its name (cut short when narrow, in full in the tooltip), its choices and ✕, then its text boxes (header and caption, or caption and label) underneath. (On one line the Spectra field needed about 700 px, so compound pages scrolled sideways beside the live preview.)
  - The placement "Spectra appendix" is now shown as "Spectra section". The values saved in compound.json (`appendix`, `private_files`, the `private/` folder) are unchanged, so older projects still open as before.

**10. Choosing what to export.**
- The Compile dialog has a checkbox for every section and sub-section, the front matter pages and the spectra section.
- Unticking a section also leaves out its sub-sections and its compounds' spectra.
- The choice is remembered in the project. Individual compounds can't be excluded here.
- *As built:*
  - It is a tick-box tree: Front matter (Cover page, Table of contents, General Information), every section and sub-section, and "Spectra".
  - Unticking an item greys out everything inside it. Each item keeps its own tick for when its parent is ticked again.
  - Only the unticked items are saved, in `"export_excluded"`.
  - "Tick everything" resets the choice. A note appears when nothing that prints is ticked ("Front matter" alone prints nothing), and compiling then gives a warning (never blocks).
  - Preview ignores the choice.
  - Without the cover, the first page shows its page number.

**11. Undo / redo.**
- Ctrl+Z / Ctrl+Y (also Edit ▸ Undo/Redo, naming the action, e.g. "Undo Delete compound 3a") undo any saved change step by step: field edits, structures, pastes, adding/deleting/moving items, settings, references, general procedures.
- About 50 steps; the history lasts until the project is closed.
- It works by remembering the previous version of every file a change touched (kept in `build/undo/`).
- Inside a text box that has its own typing history, Ctrl+Z first undoes typing.
- *As built:*
  - **How it works:** every change the program makes to the project's files (writes, copies, moves, new and deleted folders) goes through `core/fileio.py`, which first tells the project's undo journal (`core/undo.py`). For each step the journal keeps, once per file: its earlier contents (a copy in `build/undo/<step>/`, never in memory), or "did not exist", or where a folder was moved (e.g. into `trash/`). Folders that are deleted (emptying the Trash, removing a Trash entry) are moved into `build/undo/` instead, so they can come back. `build/`, `snapshots/` and `.lock` are not part of the history.
  - **Undo** puts everything back, newest first, and reloads the project from its files; what it replaces becomes the redo step. **Redo** works the same way. A new change clears the redo list. 50 steps are kept; the history (and `build/undo/`) is removed when the project is closed or opened. Read-only projects have no undo.
  - **Safe:** if something cannot be put back part-way (a file open in another program, being synced), everything already done is reversed again and a plain message says nothing was changed and why (naming the place in the project). The step stays in the list to try again. If the copy kept for undoing is gone (the project was opened elsewhere, which starts a new history) or an item was moved or deleted outside the program, the message says so instead.
  - **Steps and names:** each command of the window is one step, named in Edit ▸ Undo/Redo ("Undo Delete compound 2", "Redo Move section “Methods”"): new/rename/delete/move of sections, sub-sections, compounds and scheme pages, Change to reaction scheme, Use as General Information, Project settings (the whole dialog, including Re-render pasted pictures), References, the Compile window's choices, dismissing/restoring warnings, adding a word to the project dictionary, Restore from Trash and Empty Trash (whose question now says Undo can still bring the items back until the project is closed), and Remove figure/table (picture and its paragraph together; its question now says Edit ▸ Undo brings it back).
  - **Actions on a page are steps of their own**, named where it helps: "Change the structure of 2", "Paste a picture of 2", "Add 2_13C.pdf to 2 ▸ Spectra", "Remove 2_1H.pdf from 2 ▸ Spectra", "Add field “Rf” to 2", "Remove field “Yield” from 2", "Add a general procedure", "Remove general procedure “B”", "Move general procedure “B”", "Edit the ID table of “Products”", "Change the default fields of “Products”", "Attach opt.pdf to “Optimization Table”", "Paste the scheme of “…”", "Paste a figure", "Add a table", "Edit Table S1", "Change the label of Figure S1", "Rename section “…”". Any other save that is not typing (a tick box, a list choice, a number, a dragged label, a dashboard choice) is a step named after the page, one per click or change ("Edit 2", "Edit Table of contents", "Change the spectra defaults", "Edit the spectra of 2"); all the files one click changes are one step.
  - **Typing** is saved by the page's autosave every 800 ms; consecutive autosaves of the same page (on a compound page, the same field: "Edit 2 ▸ Yield") merge into one step while nothing else happens. Any action, opening another page, a command or an undo starts a new step. Typing not yet saved is saved first, as its own step, before an action on the page (so undoing the action keeps the typing), and before an undo. A figure or table added or removed takes only the text's next autosave (its paragraph) into its step; typing after it is a step of its own. Typing saved after clicking another tree item is named after (and undone on) the page it was typed on.
  - **Nothing-steps are dropped:** saving something unchanged is not a step; a command that failed and put everything back (e.g. "nothing was deleted") adds no step and keeps what can be redone; typing undone in its own box and autosaved again removes its step, so Edit ▸ Undo never seems to do nothing.
  - **Open files:** an Attachment section's PDF on show is let go before an undo, redo, delete or move (Windows cannot move a file that is open).
  - **Changes made when a project opens** (adding the Spectra section, turning older pictures into figures) happen before the history starts, so they are not steps.
  - **Another person taking over** the project ends this window's history (it becomes read-only) and leaves `build/undo/`, now theirs, alone, also when this window is closed. Undo first checks the lock.
  - **After an undo/redo** the tree is rebuilt and the page the step was about is shown (the deleted compound after undoing its deletion, the item after it after redoing it); if it no longer exists, the nearest item that does. Edits typed but not yet saved are saved first (so they are undone too). The status bar says "Undone: …".
  - **Keys:** Ctrl+Z / Ctrl+Y (also Ctrl+Shift+Z, and ⌘ on macOS). In a text box (also number and name boxes) that still has typing to undo, the keys undo typing there; once it has none, they go to Edit ▸ Undo/Redo. In dialogs the keys stay with the dialog's boxes. Tips and messages show the keys as this computer names them (⌘Z on macOS).

**12. Spectra section.** A special section that replaces the automatic appendix. One is added to every project; it can be moved but not deleted.
- It gets one sub-page per compound with spectra, automatically, in compound order. Each compound's Spectra field has a **View spectra page** button.
- It covers NMR, HPLC/SFC, IR and other files placed in the spectra section. Every type can show the structure + ID (on by default; it can be left off a spectrum, or off by default).
- **Defaults** on the section page:
  - 12.1 spectra per page (1, 2 or 4) and orientation (portrait/landscape);
  - 12.2 optional frame around the structure + ID;
  - 12.3 background colour behind them (white by default);
  - 12.4 ID text size/bold and structure size;
  - 12.5 optional frame around each spectrum;
  - 12.8 auto-crop of white margins, scaling each spectrum to fill its slot (axes kept; only blank white is cut);
  - 12.9 optional caption under the structure, e.g. "¹H NMR (CDCl₃, 500 MHz)", taken from the peak report (NMR only);
  - the existing header line above each spectrum ("2 — ¹H NMR (400 MHz, CDCl₃)"), on/off.
- **Apply defaults to all spectra** asks first, because it also resets dragged positions.
- **Per spectrum:** on its compound's sub-page, every default can be overridden (12.7), including orientation of that page. The structure + ID box can be dragged anywhere on the spectrum and resized with handles, as with scheme labels.
- Each page of a multi-page PDF is a separate spectrum with its own position.
- *As built (data, defaults page, auto-crop and printing):*
  - **The section:** stored as a section of type `spectra` (shown as "Spectra"). It is added at the end of the tree when a project is opened for editing (so also right after a new project is made). An older project's "Spectra per page" and "Structure position on spectra" settings become its defaults and are removed from project.json. A project opened read-only that has none yet prints its spectra at the end, as before. Delete is greyed out for it (its tooltip, and a message if Delete is pressed anyway, say why), and a custom section holding it can't be deleted until it is moved out. It can be renamed (its name is its printed heading) and moved, also into custom sections.
  - **Defaults** are saved in its `section.json` as `"defaults": {per_page, orientation, header_line, struct_box, corner, struct_frame, struct_frame_color, struct_frame_width, struct_bg, id_size, id_bold, struct_scale, spectrum_frame, spectrum_frame_color, spectrum_frame_width, auto_crop, caption}`. The defaults reproduce the earlier appendix: 1 per page, portrait, header line on, top-left, white box, ID bold at the text size (`id_size` 0), structure at its natural size but at most 1.5 × 1.1 in (`struct_scale` 1.0), structure + ID shown (`struct_box` true), no frames, no crop, no caption. Frames have their own colour and line width. Unusable values in the file are ignored. **Structure size** (25–400 %) is a true scale: 100 % is the size above, 200 % prints the structure twice as large, 50 % half (it multiplies the 100 % size, so small and large structures change alike).
  - **Per spectrum** (for the next stage's sub-pages): `"layout": {…}` on the file's entry in compound.json overrides defaults for all its pages; `"pages": {"1": {…, "x", "y"}}` overrides one page and holds the dragged centre of the structure + ID box as fractions of the shown spectrum. Effective settings = defaults, then the file's, then the page's.
  - **Apply defaults to all spectra…** (on the section's page) says how many spectrum files have settings of their own, asks first, then removes every `layout` and `pages` entry.
  - **The page** has the defaults in two groups (Pages; Structure and compound ID), a small sketch of one page (not to scale) that follows the choices at once, and the live preview on the right shows the first 8 spectra (with a note; only the preview of this section, or of a section holding it, is cut short: the table of contents preview lays out every spectrum so the page numbers after it are right, and measures nothing new for auto-crop there). Changes are saved straight away. In a narrow window the page scrolls sideways rather than hiding controls. Spectra per page / structure position were removed from Project settings and the Compile SI window (both now point to this page).
  - **Which spectra:** the Spectra field (¹H, ¹³C, ¹⁹F, other nuclei, other NMR, 2D, HPLC/SFC, IR, other) then Additional files set to "Spectra section", compound by compound in tree order (within a compound, the order can be set by hand on its spectra page, see below). Every one gets the structure + ID (HPLC/SFC traces too) unless "Structure + ID" is unticked for it; the automatic caption is for NMR spectra with a nucleus (¹H, ¹³C, ³¹P…): "¹H NMR (CDCl₃, 500 MHz)", nucleus then solvent then frequency from the matching peak report ("¹³C NMR" alone when the report doesn't say). For "Other NMR" the nucleus is the one in the file name (e.g. `3a_29Si.pdf`), or else the compound's only peak report for a nucleus without a type of its own (²⁹Si, ¹⁵N…); the header line uses it too. The solvent is recognised however the report writes it (CDCl₃ or CDCl3, chloroform-d, (CD₃)₂SO, DMSO-d₆, methanol-d4, benzene-d6, MeOD…) and printed as the report writes it, subscripted automatically (§13l.14; before that, in its usual form); the first one named wins.
  - **Typed caption (manual override, §2.7):** each file in the Spectra field has a box "Caption under the structure (empty = automatic)" (stored as `struct_caption` on the item). When it is filled in, that text is printed instead of the automatic caption, for any kind of spectrum (so an HPLC trace can have one too). Captions print only when the section's "Caption under them" is ticked.
  - **Printing:** each spectra page is its own Typst page (landscape pages are turned), its slots filling the page body below the section heading; 2 per page are stacked on portrait and side by side on landscape pages, 4 make a 2 × 2 grid; a page that isn't full keeps the same slot size. A spectrum whose own settings need a different number per page or orientation starts a new page. Each spectrum is scaled to fit its slot keeping its shape (with 2 per page a little larger than the old fixed 3.85 in, as the slot is now measured). The section heading prints only when there are spectra. When the Spectra section is the first sub-section of a custom section that has no text of its own (or of a Data section with nothing in it), that section's heading is printed at the top of the first spectra page with the Spectra heading, rather than alone at the bottom of the page before (and so the table of contents points to the right page). A Preview of a section holding the Spectra section prints the spectra once, where the Spectra section sits.
  - **Auto-crop:** each spectrum page is drawn at 100 dpi; everything darker than near-white (245 of 255) counts as content, plus a small margin (1 % of the page, at least 4 pixels). Only that part is shown (the rest is clipped), scaled to fill the slot. Results are remembered in `build/crop_cache.json` by file, size, date and page, so each page is measured once. The original file is measured (also for pictures that are converted for printing, e.g. TIFF), so results carry over between builds; results for files that are gone or have changed are dropped when the cache is saved. A blank or unreadable page is not cropped. A PDF's shape is taken from its CropBox (e.g. a page cropped in Acrobat), as Typst and the preview draw it, turned by its /Rotate.
  - **Compile SI window:** the Spectra section is ticked like any section; the older `"spectra"` exclusion key unticks it and is replaced by the section's ID when saved. Its entry reads "(the spectra of the compounds in the ticked Data sections)": excluded Data sections' compounds are left out of it (there is no per-compound choice there).
- *As built (each compound's spectra page):*
  - **In the tree:** under the Spectra section, one page per compound that has something in the Spectra section ("2 — N-Benzyl-N-methylacet…"), in compound order, with the spectrum icon. The pages follow the compounds by themselves: adding or removing a spectrum (or setting an Additional file to "Spectra section") adds or removes the page at once. They can't be renamed, moved or deleted; right-click offers "Open the compound page". If the page on show loses its last spectrum, the Spectra section is shown instead.
  - **View spectra page:** a button in the compound page's Spectra field (also on scheme pages' compound tabs), enabled once anything of the compound is printed in the Spectra section (a spectrum, or an Additional file set to "Spectra section"). The Additional files field shows the same button while one of its files is set to "Spectra section" (e.g. a compound whose only trace is an HPLC file). It selects the compound's page (expanding the tree to it). The spectra page has a "Compound page" button back.
  - **The page:** a list of the compound's spectra (each page of a PDF on its own, e.g. "¹³C NMR · page 2 of 2"; spectra with settings of their own are bold with a dot), the chosen spectrum drawn in its printed proportions (the slot size of its journal style, number per page and orientation, auto-cropped if on, header line above it) with the structure + ID box on it (the same structure picture or drawing, ID size/bold, caption, frame and background as printed), and below, its settings. A note under the drawing says how it is set out; the live preview pane shows the real pages of this compound (preview kind `("spectra", compound id)`).
  - **Dragging and resizing:** drag the box anywhere on the spectrum (saved as that page's `"x"`/`"y"`, the box's centre as fractions of the shown spectrum); drag a corner handle to resize it, keeping its shape (this is the same as typing in "Structure size", 25–400 %). A dragged box resizes about its centre; one in a corner keeps that corner in place. A click on the box that does not move it changes nothing. Choosing a corner in "Structure position" puts a dragged box back in that corner. While changing every page of a file, a handle on a page that has its own structure size changes that page's size (which wins), so the box keeps the size it was pulled to; otherwise it changes every page.
  - **Drawn as printed:** the spectrum is fitted into its slot below the header line, as the styles do, so the box is in the right proportion to it also when the slot's height is the limit (e.g. 2 per page, portrait).
  - **Settings for this spectrum:** every default of the section page can be changed: spectra per page, orientation, header line, spectrum frame (colour, width), auto-crop, **Structure + ID shown** (untick to print this spectrum without the structure, ID and caption, e.g. a crowded HPLC trace or a 2D spectrum; also a default on the section page; the box's other settings are greyed while it is off), structure position, structure size, ID size and bold, frame around the structure + ID (colour, width), background colour, caption. Each row has ↺ (back to the default) and its label shows where the value comes from: plain = the default, blue and bold = changed here, italic = changed for every page of the file (or, when changing every page, orange "this page" = the page has its own, which wins). A setting put back to the default value is not stored, so it follows the default again. A legend under the settings explains the colours. "Reset this spectrum to the defaults" removes everything changed for it, including the dragged place; when a page of a several-page PDF has nothing of its own but the file has settings, a note beside the (greyed) button says to choose "Every page of this file" to change or reset them. Changes are saved straight away (spin boxes after a short pause).
  - **PDFs with several pages:** "Changes apply to: This page only / Every page of this file" (stored as the page's own settings, or the file's `"layout"`). A change still waiting to be saved when the choice is switched is saved where it was made. A dragged position always belongs to its own page. "Reset every page of this file" asks first.
  - **Text for this file:** the header line and the caption under the structure can be typed here too (the same `title` and `struct_caption` as the compound page's Spectra field; empty = automatic, which the boxes show greyed). This also gives Additional files placed in the Spectra section a typed header and caption.
  - **Order of the spectra (manual override, §2.7):** "▲ Up" / "▼ Down" under the list move the chosen file (all its pages) among the compound's spectra; the order is then kept in compound.json as `"spectra_order": [[field key, file name], …]` and used by the spectra page, the preview and the compiled SI. Files not in it (e.g. added later) follow, in the automatic order. "Automatic order" removes it (back to ¹H, ¹³C, … then Additional files). "Apply defaults to all spectra" does not change the order.
  - Read-only projects show the page (and drawing) without editing. A missing or unreadable file is said in plain words: a PDF that can't be opened stays in the list as e.g. "¹H NMR (could not be read)" with "… could not be opened as a PDF (it may be damaged …). Export it again and replace it on the compound page." on the drawing (the compile warning uses the same words). A page without spectra shows the section's defaults, greyed. An Additional file without a caption is listed by its file name (as its header).

## 13l. Nathan's second review list (agreed 2026-10-04, from `Comments.md`)

1. **Pasting spectra from Mnova.**
   - Where Ctrl+V works:
     - on the compound's Spectra field: click it to select it, as with the structure box;
     - on the compound's spectra page;
     - right-click ▸ Paste spectrum.
   - The spectrum type is guessed from which NMR reports have no spectrum yet, and confirmed in a one-click chooser.
   - The spectrum is kept as a **vector drawing (SVG)** where possible:
     - Mnova's Enhanced Metafile (Windows) is converted to SVG. Each conversion is checked against Windows' own rendering of the same metafile.
     - If the check fails, or Mnova copied its default "metafile containing a bitmap", the spectrum is kept as a picture instead: the bitmap at its own resolution, or rendered at 600 dpi.
     - On macOS, Mnova's PDF copy is kept as a PDF, which is already vector.
   - Mnova's original copy is always stored next to the result, so it can be converted again later.
   - Mnova's link back to the FID (double-click to reprocess) can't be kept; store the .mnova file in "Store additional files and folders".
   - Tip for the user: in Mnova choose the Enhanced Windows Metafile copy option for the sharpest spectra.
   - *As built:*
     - **Where.** The Spectra field (`ui/spectrum_paste.SpectraEditor`) is a paste target like the structure box: click it (blue outline round its files, "▶ Ctrl+V pastes here"), then Ctrl+V; right-click ▸ Paste spectrum / Add files…; a **Paste spectrum** button next to "Add files…" (its tooltip names Ctrl+V too). While it is empty one short line says how ("No spectra yet: copy one in Mnova, click here, Ctrl+V — or drop files here."; the "Click this box…" and drop hints, which said the same, are hidden meanwhile, so it no longer wraps to four lines on a scheme's compound tab at 1200 px; the blue "▶ Ctrl+V pastes here" still shows once it is clicked). The drop hint of this field is shorter ("…or drop PDFs/pictures here."). The per-file Type drop-down shows the chooser's labels ("¹H NMR", "¹³C NMR"…; stored values unchanged). Text boxes inside keep their own paste. The compound's spectra page has a **Paste spectrum** button, Ctrl+V anywhere on the page and right-click ▸ Paste spectrum on the drawing or the list; the new spectrum is then shown.
     - **What is pasted:** an Enhanced Metafile (Mnova, Windows) as below; a PDF (Mnova on macOS) is kept as the PDF itself (it is its own original); any other picture is kept as a PNG as it is; copied PDF/picture files are added (one file: its type asked, guessed from its name; several: added like dropped files, types from their names, no question). Anything else: "The clipboard has no spectrum. Copy the spectrum in Mnova (or a PDF or picture file…) first, then paste." / "notes.docx is not a PDF or a picture…".
     - **Type chooser:** ten buttons (¹H, ¹³C, ¹⁹F, ³¹P, ¹¹B NMR, Other NMR, 2D NMR, HPLC/SFC, IR, Other); one click chooses; the guess is outlined, bold and the default, so Enter takes it; Cancel/Esc pastes nothing. Guess (`core/spectra.guess_pasted_type`): the first of ¹H, ¹³C, ¹⁹F, ³¹P, ¹¹B that has a peak report with text but no spectrum of that type; else ¹H when the compound has no spectra; else Other. Typing not saved yet (e.g. a report typed a moment ago) is saved before the guess.
     - **Stored** in the compound's `spectra/` folder as `pasted 1H.svg` (or `.png`/`.pdf`; `pasted 13C`, `pasted other NMR`, `pasted 2D NMR`, `pasted HPLC-SFC`, `pasted IR`, `pasted spectrum`; " (2)" when taken; never the compound's ID) with Mnova's copy next to it under the same name (`pasted 1H.emf`), named on the item in compound.json as `"original": "pasted 1H.emf"`. Only pasted spectra are paired this way, so an attached `3a_1H.png` never takes `3a_1H.pdf` for its original. Removing sends both to one Trash item; restoring brings both back. One Edit ▸ Undo step "Paste spectrum into 3a" (several files: "Paste 2 files into 3a ▸ Spectra"). A compound whose Spectra field was removed (it can still have a spectra page, for an Additional file set to "Spectra section") gets the field back in the same step, so the pasted spectrum is shown and printed (the line says so). An unexpected problem while converting or storing is said in a message ("…could not be read… (details: …)"), never a silent failure; likewise "Re-render pasted pictures" counts an original that fails and goes on with the others, and reports any other problem in plain words. One plain line says what happened, under the field's buttons and in the status bar: "Kept as a vector drawing (SVG)." / "Mnova copied a picture (1600 × 1200 pixels, only about 254 dpi); it was kept at its own resolution. For sharper spectra, choose “Enhanced Windows Metafile” in Mnova's copy options and copy again." / "Kept as a picture (600 dpi): …why…" / "Kept Mnova's PDF (a vector drawing)." / "Kept the copied picture as it is." The page's warnings follow at once (e.g. "a ¹H report but no ¹H spectrum" goes; this also now happens after "Add files…").
     - **The converter** (`core/emf_svg.py`, pure Python with NumPy, no Windows calls, so it runs and is tested everywhere) reads the record list: header; mapping modes (MM_TEXT, LO/HIMETRIC, LO/HIENGLISH, TWIPS, ISOTROPIC with GDI's aspect fix, ANISOTROPIC), window/viewport extents and origins, SCALE*EXTEX; SET/MODIFYWORLDTRANSFORM; SAVEDC/RESTOREDC (the whole state, clipping too); pens (CREATEPEN, EXTCREATEPEN: width, colour, caps, joins, dashes incl. user styles), brushes (solid, hatched → SVG pattern, DIB pattern brushes → image pattern), fonts (EXTCREATEFONTINDIRECTW), stock objects, DELETEOBJECT; text colour, background mode and colour, text alignment, fill mode, ROP2 (copy, no-op, black, white, inverted, mask; others count as not converted), miter limit; MOVETOEX, LINETO, POLYLINE/POLYGON/POLYBEZIER(+TO) in 32 and 16 bit, POLYPOLYLINE/POLYGON, POLYDRAW, RECTANGLE, ROUNDRECT, ELLIPSE, ARC/CHORD/PIE/ARCTO, ANGLEARC, SETPIXELV; paths (BEGINPATH … STROKE/FILL/STROKEANDFILLPATH, CLOSEFIGURE, ABORTPATH, SELECTCLIPPATH); clipping (INTERSECTCLIPRECT, EXCLUDECLIPRECT, EXTSELECTCLIPRGN, SETMETARGN, OFFSETCLIPRGN for rectangles) as SVG `<clipPath>`; FILLRGN/PAINTRGN; EXTTEXTOUTW/A with the dx spacing, alignment (left/centre/right, top/baseline/bottom, update-CP), opaque backgrounds and ETO_OPAQUE/ETO_CLIPPED, escapement (turned labels) and the advanced graphics mode, A records in the font's code page, the Symbol font's letters turned into Unicode Greek; pictures (STRETCHDIBITS, BITBLT, STRETCHBLT, SETDIBITSTODEVICE, ALPHABLEND; 1/4/8/16/24/32-bit DIBs with palettes and bit fields, bottom-up rows, source rectangles, mirroring; JPEG/PNG DIBs as they are) embedded as PNG; BITBLT without a picture (PATCOPY, BLACKNESS, WHITENESS); comments (EMF+ dual records are skipped, the GDI records after them are used). Colour management, palettes and escapes are skipped (no visible effect). Anything else that draws (gradient fills, flood fills, mask/transparent blits, XOR drawing, RLE pictures, WIDENPATH…) is counted as "not converted" instead of stopping it, and a damaged or cut-off metafile is reported; either way the paste falls back to a picture.
     - **Damaged data** (review of §13l): nothing a record says can make the converter hang or build a huge SVG. An ANGLEARC sweep of more than one turn is drawn as one turn plus the rest (ending at the same angle; at most 8 curves), and an infinite or undefined angle or size counts the record as damaged; coordinates or transforms beyond ±10⁹ units (10 km) or not finite count the record as damaged (nothing of it is drawn, never "nan" in the SVG); SAVEDC keeps at most 10,000 states; at most 16 clip shapes are intersected at once (the same shape again changes nothing; more counts as "not converted"). Characters XML can't hold (control characters, a lone half of a UTF-16 pair, U+FFFE/U+FFFF) are left out of texts and font names, and the SVG is encoded with any left-over ones replaced, so a paste never fails on them. Point, polygon and text counts were already checked against the record's length, and pictures against their data. The reviewers' fuzz tests (12,000 metafiles of random records, 770 cut-off or mutated real ones) pass with no error, no invalid XML and none slower than 1 s.
     - **The SVG:** user unit 0.01 mm, viewBox = the metafile's frame, width and height in pt, so it prints at the copied size (`compile/document.svg_size_in` now reads pt, px, mm, cm, in or the viewBox). Coordinates are whole 0.01 mm, written as relative path data (65,536 points ≈ 400 KB). Cosmetic pens and anything thinner are drawn 0.25 pt wide (`HAIRLINE_PT`). **Drawing Windows measures in output pixels** (Phase 8 fix): GDI draws hatched brushes (1-pixel lines 8 pixels apart) and the dashes of cosmetic pens in the pixels of whatever it draws on at the time, not in the metafile's reference device (the screen it was made on), so no one size is right for an SVG; the SVG uses fixed sizes (hatches as on a 96-dpi screen: lines 2.12 mm apart, 0.26 mm wide, `HATCH_PIXEL`; cosmetic dashes in hairline-sized pixels, e.g. a PS_DASH dash 1.6 mm, `COSMETIC_PIXEL`), the same whichever screen the metafile came from. Both are marked in the SVG (`class="hatch hatch-N"`, `class="cosmetic-dash"`), and the self-check redraws them at the pixel size Windows used for its own rendering (`spectrum_paste.check_svg`, `emf_svg.rescale_output_pixels`), so it still compares them strictly. (Before, they were sized in reference-device pixels, so the check failed on screens of another resolution.) Long lines are written as several `<path>` elements of at most 16,000 segments (Qt's SVG renderer silently drops longer ones; Typst doesn't care). **Clipping:** each clip state is one `<clipPath>`, so every element sits in at most one clipping group; a state of several shapes is a chain of clip paths, each clipped by the one before (`<clipPath id="k2" clip-path="url(#k1)">`), each link written once (before, every element re-opened one nested group per shape: a 226 KB metafile made a 60 MB SVG). Typst and Qt (as masks within masks) both draw the chain right.
     - **Text stays text.** Measured: Typst draws SVG text with the computer's fonts and keeps it as real, searchable text in the PDF (Arial and Times New Roman embedded; every letter at the metafile's position via an `x` list), so text is not turned into outlines. The font list is the metafile's font, then look-alikes on every system ("'Arial', 'Helvetica', 'Liberation Sans', 'DejaVu Sans', sans-serif"; serif and monospace likewise), because Typst's own fall-back is a serif font. Only text written as glyph numbers (ETO_GLYPH_INDEX) is read back into characters with Qt (`QtTextHelper`, only when Qt has that very font); if that fails it is drawn as outlines; without either, it counts as not converted. Qt's metrics (ascent/descent) are used for top- and bottom-aligned text when Qt has the font, else typical values.
     - **Qt's view of the SVG.** Qt's SVG renderer (the screen, auto-crop, the self-check) ignores `clip-path`, reads a font list as one name and uses only the first `x`; `compile/qt_images.qt_friendly_svg` rewrites those for Qt only (clip paths → masks, a chained clip path → a mask whose content is masked by the one before, the first font, one `<text>` per character). The file itself is never changed.
     - **The self-check** (Windows; `ui/spectrum_paste.self_check`): Windows draws the metafile (GDI, as for ChemDraw pictures) and Qt draws the SVG, both at 150 dpi. Ink = pixels whose darkest channel is below 200. Each drawing's ink must lie within 2 pixels (0.34 mm) of the other's: at least 99 % overall and at least 97 % in every 64 × 64-pixel tile (≈ 11 mm) holding 20 or more ink pixels, both ways; and inside shapes (ink with ink all round in both, each pixel averaged with its neighbours, so ClearType's coloured letter edges don't count) at most 5 % may differ in colour by more than 60 of 255. Tuned on test metafiles drawn with real Windows calls: good conversions score 1.000 everywhere; text moved by 1 mm, a label, curves or picture left out, the drawing 0.4 % or 1 % too large, lost clipping and swapped colours all fail (a single overall score missed a lost axis title, hence the tiles). Typst's own rendering of the SVG was also compared with Windows' and matches by the same measure.
     - **Fall-backs:** a metafile that is one picture filling at least 90 % of the frame each way, with nothing else but white backgrounds (Mnova's default), is kept as that picture, as PNG, at its own resolution (never enlarged), its resolution recorded so it prints at the copied size (pictures stretched unevenly are drawn by Windows instead); anything that fails the check or uses drawing the converter can't do is drawn by Windows at 600 dpi (lower only for huge pictures, as for ChemDraw pictures); a metafile that is EMF+ only (no GDI records) is drawn by GDI+ instead. A copy that draws nothing: "The copied spectrum could not be read…". Off Windows an EMF is never converted unchecked (a re-render there leaves it as it is).
     - **Speed** (65,536-point trace, 314 texts, a picture and a clip): conversion 0.04 s, self-check 0.4 s.
     - **SVG spectra everywhere:** the spectra page, auto-crop, thumbnails and "image size" draw SVGs with Qt at the resolution asked for; Typst prints them in all three styles. A damaged SVG would stop Typst from making the PDF, so it is checked first: the spectra page lists it as "… (could not be read)" with "pasted 1H.svg could not be read as an SVG drawing (it may be damaged). Paste or export it again…", and compiling leaves it out with that warning (any other damaged SVG picture likewise).
     - **Re-render pasted pictures** (Project settings ▸ Output) also converts every pasted spectrum again from its kept original (e.g. one kept as a picture may become SVG after an update): same name, perhaps another extension; its settings, place in the order and original stay. The progress bar counts both; the report says how many spectra were converted and how many became SVG. EMF originals can only be converted on Windows.
     - **Tests:** `tests/test_emf_svg.py` (hand-made records, every system: mapping modes, transforms, pens, clipping, text, pictures, damaged and cut-off data, speed), `tests/test_review13l_fixes.py` (one or more regression tests for each finding of the review of §13l), `tests/test_spectrum_paste.py` (metafiles drawn with real Windows calls in `tests/emf_samples.py`, the fall-backs, storage, Trash, the chooser, pasting in the window, compiling). The checks that compare Windows' text with Qt's need the computer's fonts, which the tests' "offscreen" Qt has none of, so they run in a separate process on Qt's Windows platform (`tests/native_spectrum_checks.py`).
     - **Not testable here:** a real copy from Mnova. If one is kept as a picture although it was copied as "Enhanced Windows Metafile", the reason is in the conversion's summary (`emf_svg.convert(data).summary()` on the kept `.emf`).
2. **Interface size.** View ▸ Interface size (75–200 %), saved on this computer.
   - It applies on restart; "Restart now?" restarts the program.
   - The restarted program reopens the same project at the same page (tree item and tab).
   - *As built:* sizes 75, 90, 100 (normal), 110, 125, 150, 175 and 200 %, a checkable list in View ▸ Interface size, saved as QSettings `interface_size` (a whole percentage; anything else counts as 100). It scales everything (text, buttons, pictures) on top of the computer's own display scaling. `ui/restart.py` applies it at start-up, before the application object exists, as the environment variable `QT_SCALE_FACTOR` (read with the program's name given explicitly), and takes the variable away again as soon as the application exists (Qt has read it), so programs started from this one (a .mnova file opened in Mnova, a PDF viewer) are not scaled too; Ketcher was checked at 150 %: it is laid out at the window's size either way. A `QT_SCALE_FACTOR` the user set themselves wins (choosing a size then just says so) and is left alone. **Sizes too large for the screen** are greyed in the menu (worked out when it opens: the screen's free area, scaled to that size, smaller than the window's smallest size), with a tooltip saying why ("Too large for this screen…"); the size in use and the saved one stay available. The View menu shows its items' tooltips. Choosing the size already in use does nothing; another one asks "Restart now?" (Yes / No; No = from the next start). **Restart:** pending edits are saved, then the *restore info* is taken: the project folder (and whether it was open read-only), the selected tree item with the items it sits in (nearest first), the Data section's tab or whether a scheme page's picture is folded away (its compound tab is the tree item), and whether the live preview is folded and the Warnings pane open. The project is closed (lock released), the window's size and place saved, a new copy started (`python -m si_assistant --restore {json}` from the folder holding the package; a PyInstaller program is started directly), and this one quits. The restore info travels only on that command line, so it is used once and nothing is left on disk. The new copy reopens the project (read-only again without asking if it was read-only and someone still has it), selects the same item or, if it has gone, the nearest one it sat in, and puts back the tab and panes. A folder that has gone gives the start page, quietly; unusable restore info (e.g. a place list that is not a list) is ignored, and any error while applying it never stops the program starting (`__main__.restore_safely`). If the new copy cannot be started, the project is reopened where it was and the user is told to restart by hand. The start-up test mode (`SI_ASSISTANT_SMOKE_TEST`) only reads the saved size and ignores restore info; it saves nothing on this computer either (since the review of §13l not even the window's size and place when it closes).
3. **Most recent project.**
   - The start page has **Open "<name>"** for the last project.
   - Option (View menu and start page): **Open the last project when the program starts**, off by default.
   - *As built:* the button is named after the project's folder (`Open “Enamides”`, the full path in its tooltip) and is hidden when there is no recent project or its folder is gone (it does not fall back to an older one). The option is a tick box under the buttons and a checkable View-menu item showing the same setting (QSettings `open_last_at_start`). At start-up it opens the most recent project only if its folder is still there (otherwise the start page shows, with no message); a project folder given on the command line wins, and the start-up test mode (`SI_ASSISTANT_SMOKE_TEST`) never opens one.
4. **Large J check.** A coupling constant over 20 Hz in a ¹H NMR report gives a warning. If the structure contains F or P, the message adds that H–F or H–P couplings can be this large, so it may be correct.
   - *As built:* check `"coupling"` ("Large J values" on the dashboard), on the NMR field, one warning per ¹H report listing every value over 20 Hz as written ("J = 26.5 Hz, 21,4 Hz are larger than 20 Hz…"); exactly 20 Hz is fine. Read forms: `J = 8.3 Hz`, `J = 8.3, 2.0 Hz`, `2J = …`, `J1 = …, J2 = …`, `JH–F = …`, `J(H,F) = …`, values joined by "and", decimal commas (`8,3`); a number followed by "H" is an integration, not a J. Explicitly labelled H–F couplings are flagged too. **Wording** (review of §13l): a value labelled as an H–F or H–P coupling (JH–F, J(H,F), JHF, JH-P…) "may be correct: it is labelled as an H–F coupling, and these can be this large", with or without a structure; unlabelled values (no F or P in the structure, or no structure) "…H–H coupling constants are rarely this large: check it for a typo"; when the structure contains F or P, the message leads with "…which may be correct: the structure contains F, and H–F couplings can be this large" and only then "If it is an H–H coupling, check for a typo" (left out when every value is labelled). Dismissal key `coupling:<report id>`, fingerprint = the large values, so editing other peaks keeps it dismissed but changing a large value brings it back.
5. **Name:** "Chem SI Assistant" (window title, About, cover-page producer text). The per-computer settings (your name, recent projects, personal dictionary) are carried over from "SI Assistant" automatically. Internal folder and package names stay as they are.
   - *As built:* `APP_NAME` is "Chem SI Assistant" (window title, About, start page, README, style file headers, the web requests' user agent). At start-up (`ui/carry_over.py`) every setting under the old name that the new place doesn't have yet is copied once (a marker `carried_over_from_old_name` remembers it was done), and "My dictionary" (`my_dictionary.txt` in the app-data folder) is copied if there is none yet under the new name; the old copies are never changed or deleted. The package, folders, `run_si_assistant.bat` and the conda environment keep their names. **Producer text:** the program prints no producer/creator text on the cover page. Typst writes "Typst 0.15.0" as the PDF's Creator; since the review of §13l (decision of Nathan's lead developer) Compile SI then names this program: Creator "Chem SI Assistant 0.1.0", Producer "Chem SI Assistant 0.1.0 (Typst 0.15.0)", in the document information and the XMP metadata (`compile/run.mark_producer`). It is an *incremental update* (the new objects, a cross-reference section and trailer appended to Typst's file), so every page, link, bookmark, named destination and the tagged structure stay byte for byte as Typst wrote them; measured 0.02 s on the sample project (7 pages) and 0.05 s for 142 pages (rewriting the file with pypdf's `PdfWriter(clone_from=…)` took 0.7 s there). A PDF whose cross-references are a stream (not what Typst 0.15 writes) is updated by pypdf instead; if anything fails, the PDF is kept as Typst wrote it. The live preview's PDFs are not changed.
6. **Chemistry dictionary.**
   - The spellchecker adds Adam M. Azman's *Chemistry Dictionary for Word Processing* v3.0 (104,658 words; *J. Chem. Educ.*, doi:10.1021/ed2002994; CC BY 3.0 US, bundled with its attribution). This is on top of English and the word list taken from the example SIs.
   - Abbreviations made entirely of element symbols and group abbreviations, with at least two capitals (MeCN, EtOAc, nBuLi, iPrOH, tBuOK, PhMe), are accepted as written, like the all-capital ones (THF, DMSO).
   - A short list of name reactions is added (Suzuki, Buchwald, Heck…).
   - The IUPAC Gold Book is not used: its site blocks automated access.
   - *As built:* **Dictionary.** `tools/get_chem_dictionary.py` downloads `chem-dic.zip` from sciencebase.com (or takes a saved zip/.dic) and writes `si_assistant/data/azman_chemistry_dictionary.txt` (UTF-8, one word per line, sorted ignoring capitals, repeated lines removed, words and capitals unchanged; 104,658 words, 1.8 MB) and `azman_chemistry_dictionary_LICENSE.txt` (title, author, version, source, the article "A Chemistry Spell-Check Dictionary for Word Processors", *J. Chem. Educ.* 2012, 89, 412–413, doi:10.1021/ed2002994, the CC BY 3.0 US link, what was changed, and the zip's own credits). The licence is in README's table and the About box. `core/spelling.py` reads it the first time a word is checked, not at start-up (about 0.03 s and 10 MB; English plus the dictionary together about 0.1 s on the first underline). It is used like the other word lists, capitals ignored, plurals (+s/+es) too, and its words are also offered as suggestions (after the English ones). **Name reactions.** `si_assistant/data/name_reactions.txt` (hand-made, 143 surnames: Suzuki, Miyaura, Buchwald, Hartwig, McMurry, Fétizon…, since the review also Lemieux, Enders, Hajos, Eder, Parrish, Sauer, Wiechert; those the chemistry dictionary already has, such as Swern or Wittig, are not repeated). **Lab terms** (review of §13l, fewer false alarms): `si_assistant/data/lab_terms.txt` (hand-made): NMR multiplicities (dd, dt, td, tt, dq, qd, ddd, ddt, dtd, tdd, dddd, quint, sext, sept, app, br, ABq…; single letters are never checked), reagents with a capital inside (HOBt, HOAt, CuTC, DiPEA, HOSu, OxymaPure, cataCXium, QuinoxP, Acr) and supplier, instrument and chromatography names (AcroSeal, AmBeed, MilliporeSigma, SiliaPlate, SiliaFlash, ChiralCel, ChiralPak, CombiFlash, RediSep, Biotage, Isolera, Teledyne, Phenomenex, Daicel, Strem, Oakwood, Enamine, Combi(-Blocks), Aesar, Exactive, Infinity…), plus concd, preprint, rotovap. **Phosphine ligands** are accepted by a rule (`spelling.is_phos_ligand`): a known name + "Phos" in any capitals (XantPhos, Xantphos, JosiPhos, SegPhos, SEGPHOS, DuPhos, DPEphos, DavePhos, BippyPhos…), perhaps after groups (CyJohnPhos, tBuBrettPhos, AdBrettPhos), or a capital letter, element symbol or group + Phos (XPhos, tBuXPhos, RuPhos, MePhos). Measured with the reviewers' scripts (inner capitals still checked): a General Information paragraph full of reagents and brands 19 → 0 underlined words; their list of 300 lab words 14 → 0; the three example SIs 268 → 261 distinct underlined words, 432 → 277 underlined places (the rest are authors' names, DNA and peptide sequences and words cut at line ends by the PDF reader). **chem_words.txt** is still built by `tools/build_chem_words.py`, which now leaves out words the English dictionary, the chemistry dictionary or the name list already know (333 words instead of 506; every word dropped is still known), takes the solvents from `core/solvents.py` (spellings without digits, not the ones written without their spaces), and adds a few lab words (phos, pKa, DMAc, GmbH, KGaA, SiliCycle, RediSep). **Words with capitals inside** were all skipped until now (§13k.1a); they are now checked, except: abbreviations made only of element symbols (H to Am, and D) and groups (Me, Et, Pr, Bu, Pent, Hex, Ph, Bn, Tol, Xyl, Mes, Cy, Cp, Ad, Ar, Het, Vin, Py, R, X, Nu, Ac, Bz, Piv, Ts, Ms, Tf, Ns, Bs, Nf, Tr, Trt, Boc, Cbz, Fmoc, Alloc, Troc, Teoc, PMB, PMP, DMB, MOM, MEM, SEM, BOM, THP, TMS, TES, TBS, TBDMS, TIPS, TBDPS, HMDS, TMP, Bpin), a group optionally with a locant in front (n, i, s, t, c, m, o, p, sec, tert, iso, neo), with at least two capitals (MeCN, EtOAc, nBuLi, iPrOH, tBuOK, PhMe, PhMgBr, LiAlH, NaOtBu, BnBr, TBSCl, pTsOH, MeOD); a locant and one group (iPr, nBu, tBu, cHex); a locant and a capitalised abbreviation (mCPBA, pTSA); a capitalised abbreviation with a plural s (NMRs). A word with one capital ("Bu", a mistyped first word of a sentence) is checked as usual. Other words with capitals inside are accepted when they are in a dictionary, or joined from known words, element symbols, groups and capitalised abbreviations (ChemDraw, TopSpin, ReactIR, XPhos, RuPhos, tBuXPhos, JohnPhos); the rest are underlined (AbcDef, a mistyped EtOAcc), and "Add to dictionary" works as for any word. `CHECK_INNER_CAPITALS = False` in `core/spelling.py` would skip them all again.
7. **Links from peak reports to spectra** (Spectra section page, off by default).
   - In the PDF, the "¹H NMR" / "¹³C NMR" label at the start of each report links to that compound's spectrum of that nucleus. There is no link when there is no such spectrum, or when the spectra section isn't printed.
   - When it's on, two more options appear: link appearance (looks like normal text, the default / blue / blue and underlined), and a **link back** from each spectrum's header line to the compound's data.
   - *As built:* a "Links in the PDF" box on the Spectra section's page: "Link each NMR report to its spectrum (in the PDF)", "Link appearance:" (Like normal text / Blue / Blue and underlined, enabled only with links) and "Link each spectrum's header back to the compound's data" (likewise), with a short explanation. Stored with the other defaults in the section's `section.json` as `nmr_links` (false), `link_style` ("plain", "blue", "blue_underline") and `link_back` (false); they are for the whole SI, never a single spectrum's setting (`SECTION_ONLY_KEYS`). **Compiling:** the compound title gets the label `<si-c-ID>` (only with the link back); the first printed page of a compound's first spectrum of each nucleus gets `<si-s-ID-1H>` (a metadata label at the top of its slot; the nucleus of "Other NMR" files as for captions); each label is put in only once per document. The bold "¹H NMR" / "¹³C NMR"… of every generated peak report (the nucleus as written, e.g. "13C{1H} NMR" → ¹³C; a report without that text uses its nucleus) becomes `#si-link(<si-s-…>)[…]` when the compound has a spectrum of that nucleus; reports labelled "major/minor rotamer" all go to the same spectrum. Every header line of its spectra (also "(continued)" pages) becomes `#si-link(<si-c-…>)[…]`. `si-link`, written at the top of SI.typ when links are on, makes a link only when its target is in the document exactly once, so a spectrum that is not printed (Spectra section or the compound's section left out, a missing file, the live preview of a compound without spectra, a compound's spectra pages alone, the live preview's sample of the Spectra section) leaves the text as it is and Typst never stops. Appearance: plain text, or the text in blue (#0563c1), or blue and underlined; it applies to both kinds of link and to nothing else (the table of contents and citations keep theirs). It works the same in the ACS, Wiley and RSC styles (nothing in the style files). The user's own "SI text" (which replaces the generated text) gets no links.
   - *Also fixed (found while building this list):* Ctrl+B, Ctrl+I, Ctrl+= (subscript) and Ctrl+Shift+= (superscript) now work while typing in a text box: the actions were only on its toolbar, so their shortcuts only worked with the toolbar focused; they are now also on the editor itself (shortcut context "widget with children", like the alignment keys), so the text box that has the cursor gets them and the others don't.
8. **Attachment sections.** "File section" is renamed **Attachment section**. Ctrl+V (or right-click ▸ Paste) puts the clipboard into it:
   - a copied PDF or picture file;
   - an image;
   - a ChemDraw drawing (the same rendering as structures: the project's paste resolution, original kept).
   Pasting replaces the current file, which goes to the Trash as before.
   - *As built:* the stored type stays `"file"`; menus ("New Attachment section", "Attachment sub-section"), the tree tooltip, the page label, the Compile SI window and the compile warning say "Attachment". The page's buttons and preview are a paste target (click it: blue outline and "▶ Ctrl+V pastes here"; right-click offers Paste and Choose file…, or Replace… once a file is attached, as the button says), and a **Paste** button sits beside "Choose file…" (its tooltip names Ctrl+V). A copied file is taken from the clipboard's file list (the first PDF or picture; another kind of file gets a plain message); otherwise the clipboard's picture, exactly as structures and schemes get it (`clipboard_picture` at the project's paste resolution). A pasted picture is stored as `pasted picture.png` with ChemDraw's original next to it (`pasted picture.emf`/`.pdf`); it goes to the Trash with its original, comes back with it, and is included in "Re-render pasted pictures". When a file is already attached, a short Yes/No question comes first. Each paste is one Edit ▸ Undo step ("Paste into “…”"). The section's own file (e.g. copied from the project folder in File Explorer and pasted or dropped back) changes nothing ("“x.pdf” is already the file of this section; nothing was changed."); any other file is copied in before the old one goes to the Trash (under a passing name, then given its own name), so a copy that fails leaves the section as it was. **Printed size** (review of §13l, decision of Nathan's lead developer): a picture (PNG, JPG, SVG…) prints at its natural size — its recorded resolution (300 dpi when it records none, or Qt's 96 dpi default), or an SVG's own size — shrunk to fit the text width (6.5 in) and 8 in of height; before, it was always stretched to the full text width. A PDF's pages still fill the width.
9. **Impurity tolerances.** New defaults are ¹H 0.010 ppm and ¹³C 0.050 ppm. Projects still at the old defaults (0.005 / 0.03) switch to the new ones; values someone changed are kept.
   - *As built:* the tolerances are now stored as `impurity_tolerance_1H_ppm` / `impurity_tolerance_13C_ppm`, and only when they differ from the defaults (so the project follows the defaults). When a project is opened, the older keys `impurity_tolerance_1H` / `_13C` (which the settings window used to save even unchanged) are dropped if they hold the old defaults, otherwise moved to the new keys; this is saved with the next settings change. Because the keys changed, a value deliberately set to 0.005 or 0.03 later is kept.
10. **Alignment.**
   - Paragraphs can be left, centre, right or justified; figures and tables can be left, centre or right.
   - Available in custom sections, General Information, the cover page's free text and table cells. Compound procedures follow the journal style.
   - Defaults are as before: text justified, figures and tables centred.
   - *As built:* **Stored** as a short prefix at the start of the paragraph's line, before a heading's `# ` or a figure's `!figure{…}`: `!align{center} Text`, `!align{right} # Heading`, `!align{left} !figure{f-…} Caption` (also `!table{…}`). Only a choice that differs from the default is written, so texts using the defaults are unchanged; `centre` and `justified` are read too; a line of ordinary text starting with `!align{` is escaped (`\!align{`). The alignment of a figure or table is its paragraph's prefix, not in its JSON, so it travels with the paragraph through copy, paste, cut, Use as General Information and undo (documented in `core/markup.py`). **Editor:** a drop-down button beside the paragraph-style box (Left, Centre, Right, Justified; Ctrl+L / Ctrl+E / Ctrl+R / Ctrl+J, which its tooltip lists), for the paragraph at the cursor or every paragraph in a selection; on a figure's or table's paragraph it sets the object's alignment ("Justified" is greyed). The editor shows it: text justified, figures and tables centred (their label under/over them follows), or as chosen. A new line made under a figure's caption (Enter, or text with line breaks pasted into it) is ordinary justified text, not centred like the figure; Enter at the end of an aligned text paragraph keeps its alignment, as in Word. A figure or table put on an empty line keeps that line's left/right choice, otherwise it is centred. Copying whole paragraphs keeps their alignment when pasted on an empty line or as further paragraphs; part of a line pastes into the line it goes in. Changes are saved and undone like other edits. A figure or table removed to the Trash keeps its alignment there (`"align"` in its Trash entry, read from the saved text) and gets it back when restored (review of §13l). **Where:** custom sections, General Information and the cover page's extra text. Compound texts (procedures, SI text, NMR…), general procedures, captions and table cells have no button, and pasted alignment is dropped there; an alignment typed into a compound's SI text or a general procedure file by hand is ignored when compiling. **Table cells** keep their own Left / Centre / Right in the table editor (per column or cell, in the table's JSON, as before); those buttons now show the selected cell's alignment. **Compiled:** a left/centred/right paragraph or heading is `#align(…)[#set par(justify: false) …]` (ragged lines); a left/right figure or table is wrapped in `#[#show figure: set align(…) …]`, which moves the picture or table with its caption and footnotes; defaults print exactly as before.
11. **Bug fix.** Selecting text by dragging across a table or figure must not hide it.
   - *As built (cause and fix):* the editor makes text typed straight after a mention, figure or table ordinary text by resetting the "current format" whenever the cursor's format is an object's. With a selection, Qt applies that reset to the selected text itself, so as soon as the moving end of a selection passed a figure, table or mention (dragging up from the text below, Shift+Up, or a selection ending just after a mention, e.g. Select All in a text ending with one), every figure, table and mention in the selection became a plain invisible character, the selected text lost its italic and sub/superscripts, and the autosave then wrote the text without them (their files stayed in the project). The reset is now done only when there is no selection (text typed over a selection is ordinary text anyway); leftover properties of an object on typed text are cleaned too. Selected figures and tables are drawn under the selection's tint, with their labels in the selection's text colour. Regression tests: `tests/test_selection_objects.py` (mouse drag, Shift+Up, a selection ending after a mention or figure, Select All, copying a selection with a table and a figure).
12. **ee field.**
   - **12a.** On leaving the box, a value that is just a percentage gets " ee" ("99%" → "99% ee") and a ratio gets " er" ("97:3" → "97:3 er"). It is visible and editable; a value already ending in ee/er is left alone.
   - **12b.** Printed as: "99% ee. HPLC (Chiralpak ID, 10% iPrOH/hexanes, 1.0 mL/min, 254 nm): *t*R (major) = 12.3 min, *t*R (minor) = 14.8 min."
   - **12c.** The Column box is a drop-down listing every column used anywhere in the project (none built in). A column disappears from the list once nothing uses it.
13. **Drop-downs that learn.** These list their built-in choices plus every other value used in the project; a learned choice disappears once nothing uses it:
   - Physical state;
   - Rf ▸ Visualization;
   - ee ▸ Column;
   - ee ▸ Eluent.
   The Rf eluent box stays a plain text box.
   - *As built (12 and 13):* 12a happens in the box when it is left (`fields.ee_with_unit`): only a bare percentage (also ">99%", "94.5%") or a bare ratio ("97:3", "97.5:2.5") gets " ee"/" er"; "99" alone, "racemic", "99% ee" are left as typed. The compiled text applies the same rule, so values typed before this print with ee/er too. 12b: the value sentence comes first, then the method with the conditions in brackets and the retention times after a colon; with nothing but the method it prints "99% ee (HPLC)."; a missing method with conditions or times prints as HPLC (as before). 13: a part flag `learn=True` (`core/fields.py`) makes a combo part list its built-in choices (in their order) followed by every other value of that part on the project's compounds that show the field (`Project.used_part_values`), A to Z, each once ignoring capitals (first spelling kept). The list is worked out when the page opens. ee ▸ Column and ee ▸ Eluent became editable drop-downs with no built-in choices; they still count as required parts of the ee field. Typing any value is still possible (the mouse wheel only changes a drop-down after it is clicked, as elsewhere). **Typed text is kept exactly as typed** (review of §13l): Qt's own completion filled in the rest of a matching choice by itself ("Chiralpak AD" + Tab → "Chiralpak AD-H", "UV" → "UV (254 nm)", "pale" → "pale yellow oil"); now every editable drop-down (`field_widgets.SuggestingCompleter`) only suggests the choices that contain what is typed, whatever its capitals, in a list under the box, and one is used only when it is clicked or picked with the arrow keys and Enter. Capitals are kept too ("chiralpak ad-h" is not changed into an existing "Chiralpak AD-H", so a choice's capitals can be corrected). **Width:** the ee field has two parts per row (value and method, column and eluent, flow and λ, the two retention times), and the learning drop-downs are at least six characters wide and grow with the page, so long learned values no longer make the compound page wider than the window (at 1200 × 800 the ee field needed 633 px beside the live preview, 460 px available; now 331 px; every other field fits too, also on a scheme's compound tab).
14. **Solvent in captions and checks.**
   - Common spellings are recognised: MeOD, CD3OD, methanol-d4, MeOD-d4, d4-MeOH, DMSO-d6, (CD3)2SO, acetone-d6, (CD3)2CO, benzene-d6, C6D6, CDCl3, chloroform-d, CD2Cl2, CD3CN, MeCN-d3, D2O, THF-d8, toluene-d8, pyridine-d5.
   - In the spectra caption the solvent is printed as written in the peak report, with automatic subscripts (e.g. MeOD), and the impurity check understands it.
   - *As built:* one table for the whole program, `core/solvents.py` (each solvent's usual name with its spellings, also d6-DMSO, d6-acetone, d8-THF, C7D8, C5D5N, CD3NO2…), compared ignoring capitals, spaces, subscript digits and the kind of dash; a spelling must not be part of a longer word. The impurity check (`nmr_parse.solvent`), the NMR-impurities table's typed solvent names and the spectra captions and header lines all use it. Captions and header lines print the solvent as the report writes it, with subscript digits and dashes made plain so the printing subscripts them like any formula ("CD3OD" → CD₃OD, "methanol-d4" → methanol-*d*₄, and now also "d4-MeOH" → *d*₄-MeOH); "MeOD" prints as MeOD. Since the review of §13l the names are recognised in any capitals and printed as written ("Methanol-d4" → Methanol-*d*₄, "Acetone-d6", "Benzene-d6", "Acetonitrile-d3", "Toluene-d8", "Pyridine-d5", "Dichloromethane-d2", "dmso-d6"), "chloroform-d"/"Chloroform-d" gets its italic *d*, and the formatting check (§13k.1ai) flags them alike (`chemtext.DEUTERATED`; the small *d* itself stays as the convention writes it).

## 13m. Phase 8: distribution, updates and feedback (agreed with Nathan)

1. **GitHub.** The code lives in a new public repository, `nathanohare/chem-si-assistant`, under GPL-3.0-or-later (with a `LICENSE` file).
   - Not published (agreed 2026-10-05):
     - `Example SIs/`, `Example Author Guidelines/` (both ACS PDFs) and `Example output/`;
     - Nathan's notes `Comments.md` and `Overview of Functions.md`;
     - build output and stray scratch files (e.g. `p.out`).
     These files stay in Nathan's local folder; they are only left out of the repository (`.gitignore`).
   - **Test data:** the current sample project (`tests/sample_project.py`, modelled on Example SI 1 with real authors' names and email) is **not uploaded**. Nathan will provide a sample dataset; the tests are switched to it before the first upload.
   - **Commit email:** commits use Nathan's private GitHub "noreply" address (`195338517+nathanohare@users.noreply.github.com`), not his university email. It is set for this repository only when the repository is created. GitHub sign-in was verified on 2026-10-05: account nathanohare, with permission to create repositories and run automated builds.
   - **Status:** started. The repository is private at first and made public later. The made-up sample project replaces the real-data one until Nathan provides his dataset (item 5).
2. **Releases.** Installers are built by GitHub Actions whenever a version is tagged (e.g. `v1.0.0`) and attached to the GitHub Release:
   - a Windows installer;
   - a macOS app (`.dmg`);
   - a Linux AppImage.
   - They are **unsigned** for now. The user guide explains the first-launch warnings on Windows (SmartScreen) and macOS (Gatekeeper). Signing can be added later.
3. **Update check.** On startup, in the background, the program asks GitHub for the latest release and compares versions.
   - If a newer one exists, it shows the version, the release notes, and **Download and install / Remind me later / Skip this version**.
   - On Windows it downloads and runs the installer after saving and closing; on macOS and Linux it downloads the new version and shows where it is.
   - Also: Help ▸ Check for updates, and a setting to turn the startup check off. Only GitHub is contacted, and nothing about the user or projects is sent.
   - A project made by a newer program version warns before being opened by an older one.
4. **Feedback.** Help ▸ Send feedback… asks for the kind (bug report / feature request / feedback). It then opens a pre-filled GitHub "New issue" page in the browser, using the repository's issue templates, with the program version and operating system filled in.
   - Users need a free GitHub account.
   - After an unexpected error, "Report this problem" pre-fills the technical details.
   - Project files are never attached automatically.
5. *As built (Phase 8, first part; the repository is private at first and made public later):*
   - **Repository files.** `.gitignore` leaves out the items of §13m.1 plus `tests/local/` (real-data tests), `si_assistant/vendor/ketcher/` (downloaded by `python -m si_assistant.ketcher_setup`; the installers fetch it when they are built), build output, caches and OS clutter. `LICENSE` is the unmodified GNU GPL v3 text (identical to GNU's own copy), `THIRD_PARTY_NOTICES.md` lists the components and their licences, the README has Download, Licence and Third-party sections. `requirements.txt` has upper limits on the versions known to work (PySide6 < 7, typst < 0.16, pypdf < 7).
   - **Test data.** `tests/sample_project.py` is now a made-up project (authors "A. Researcher" and "B. Chemist", example.com email, an invented title; compound 2 = *N*-benzyl-*N*-methylacetamide with two rotamer ¹H reports, compound 4 = 1-(4-methoxyphenyl)ethan-1-ol in a ketone → alcohol scheme), with invented but consistent data (no check warnings). Its `FACTS` hold every value the tests look for, so another dataset replaces this one module. The real-data version is kept, unused, in `tests/local/real_sample.py` (not uploaded).
   - **Spelling list.** `tools/build_chem_words.py` now leaves out people's names (words always written with a capital that stand where names stand in citations, sit on reference lines, or occur only on the first page of the example SIs): `chem_words.txt` went from 333 to 249 words.
   - **Update check** (`si_assistant/updates.py`, `ui/update_ui.py`). Asks `api.github.com/repos/nathanohare/chem-si-assistant/releases/latest` (8 s timeout, header `User-Agent: Chem-SI-Assistant/<version>`) in a background thread 3 s after the window shows; never in the start-up test or in tests. Versions follow Semantic Versioning (tag `v1.2.3`, pre-releases such as `v1.2.0-beta.1` sort before `v1.2.0`). At start-up it only speaks when a newer version exists that was not skipped (and waits while another question is open); Help ▸ Check for updates… always answers (up to date / newer / "can't check until the GitHub project is public" for a 404 / couldn't reach GitHub). The dialog shows the release notes (Markdown) with Download and install / Remind me later / Skip this version. **Release file names** (the release workflow must use them): `Chem-SI-Assistant-<version>-Windows-Setup.exe`, `Chem-SI-Assistant-<version>-macOS.dmg`, `Chem-SI-Assistant-<version>-x86_64.AppImage`. Windows: downloaded to a temporary folder with a progress window, size checked, the project saved and closed, the installer started and the program quits. macOS/Linux: downloaded to Downloads (the AppImage made executable) and shown. No file for this computer (e.g. ARM Linux): the release page opens. View ▸ "Check for updates when the program starts" (on by default, per computer).
   - **Newer projects.** `project.json` keeps `format_version` (still 1) and now `saved_by_version` (the program version that saved it). A project whose format number is higher than the program knows is opened only after a warning, read-only by default (Open read-only / Cancel). Projects without the number open as before.
   - **Feedback** (`ui/feedback_ui.py`, `.github/ISSUE_TEMPLATE/`). Issue forms `bug_report.yml` (what went wrong, steps, expected, actual, version, operating system, technical details), `feature_request.yml`, `feedback.yml`; blank issues are off. Help ▸ Send feedback… opens `…/issues/new?template=…&title=…&version=…&os=…`. Unexpected errors (also in other threads) show "Something went wrong" with Show details / Copy details / Report this problem; the report fills the bug form's details with the traceback, with the home folder written as `~` and the open project's folder as `<project folder>`, shortened in the middle to keep the address under 7,000 characters. Help ▸ About shows the version, licence and GitHub address.
   - **Automatic tests on GitHub** (`.github/workflows/tests.yml`): Python 3.12, `pip install -r requirements.txt`, Qt "offscreen". Pushes to `main`, version tags and manual runs test on Windows, macOS and Linux; other branches and pull requests on Windows only (saves minutes while the repository is private). Windows-only tests (metafile pictures) skip elsewhere.
6. *As built (Phase 8, second part: installers, user guide):*
   - **Fixes first.** (a) Pasted-spectrum self-check: hatched fills and the dashes of one-pixel pens are drawn by Windows in the pixels of whatever it draws on, so their size depended on the screen the metafile was made on, and the self-check failed on other screens. They now have fixed sizes in the SVG and are compared at Windows' own size in the check (details in §13l.1, "Drawing Windows measures in output pixels"); a test simulates metafiles from four different screens. (b) Test strings copied from the published example SI (NMR shift lists, HRMS found values, a published formula typo) were replaced by invented values with the same meaning in tests, docstrings, placeholders and this SPEC (29 places; calculated masses such as 188.1070 for C₁₂H₁₄NO stay, being properties of the formula). (c) An empty MOL block (e.g. an empty Ketcher drawing) now gives "The structure has no atoms." instead of an error.
   - **Program files from the source or installed** (`si_assistant/resources.py`): every built-in file (styles, word lists, icons, Ketcher, LICENSE, THIRD_PARTY_NOTICES.md, the user guide) is found through `bundle_root()` (the source folder, or PyInstaller's `sys._MEIPASS` with the same layout). Ketcher is bundled; if an installed program ever lacks it, "Download" puts it in this computer's program-data folder (`%LOCALAPPDATA%\Chem SI Assistant`, `~/Library/Application Support/Chem SI Assistant`, `~/.local/share/chem-si-assistant`), because a macOS app or an AppImage can't be written to. Restart (Interface size) starts the installed program directly (`sys.executable`). On Windows the running program holds a mutex `ChemSIAssistant.Running`, so an update's installer can wait for it to close. On Linux an installed program turns Qt WebEngine's sandbox off (`QTWEBENGINE_DISABLE_SANDBOX=1`) **only** when the system forbids the user namespaces the sandbox needs (e.g. Ubuntu 24.04's AppArmor rule; an AppImage can't hold the set-uid helper either) — otherwise Ketcher's window stays blank; Ketcher is a local page shipped with the program, never a web site, and a user's own setting of the variable is respected.
   - **Self-test of the installed program** (`si_assistant/selftest.py`): `<program> --self-test REPORT.json [PROJECT]` checks the bundled files, the spelling dictionaries, RDKit drawing, Typst, Qt SVG/PDF/WebEngine, that Ketcher really starts and hands a structure back, and (with a project) compiles its SI; JSON report, exit code 0 when all passed. The start-up test (`SI_ASSISTANT_SMOKE_TEST=1`) is unchanged.
   - **Packaging** (`packaging/`): `build_app.py` (version/tag check, release notes from CHANGELOG.md, asset names from `updates.ASSET_PATTERNS`, icons, build, verify, smoke, self-test, AppDir), `chem_si_assistant.spec` (PyInstaller, one folder, windowed; leaves out Qt WebEngine's debug and developer-tools data, its languages other than en-US, Qt's own translations and QML plugins: 731 → 498 MB on Windows), `launcher.py` (entry point, `multiprocessing.freeze_support`), `windows/installer.iss` (Inno Setup 6: per-user install by default with an "all users" option, Start-menu entry, optional desktop shortcut, uninstaller, LICENSE page, fixed AppId `{DF97C8E6-8D0B-435D-8BDE-43AD337399D5}` so updates replace the old install, old `_internal` folder removed first, waits up to 15 s for the running program to close, then asks). macOS: `Chem SI Assistant.app` (bundle id `io.github.nathanohare.chemsiassistant`, version in Info.plist, ad-hoc signed) in a `.dmg` made with `hdiutil`; **Apple silicon only** (Intel Macs are not supported for now: no RDKit wheel for them with this Python). Linux: AppImage made with `appimagetool` (AppRun, `.desktop`, icon) on Ubuntu 22.04 for older glibc. App icon: the existing `si_assistant/icons/app_icon.svg` (`.ico` for Windows; `.icns` and a 512 px PNG are made from the SVG at build time); it is also the window icon.
   - **Release workflow** (`.github/workflows/release.yml`): on tags `v*.*.*` and manual runs only. A first job checks the tag equals `si_assistant.__version__` and CHANGELOG.md has its section (fails early). Then Windows, macOS (`macos-latest`) and Linux (`ubuntu-22.04`) build with Python 3.12, fetch Ketcher, verify the bundle, run the start-up test and the self-test (compiling the made-up sample project), make the installer (Windows: Inno Setup, then a silent install, start and uninstall; Linux: also starts the AppImage) and upload it. Finally the GitHub Release is created with the three files and the CHANGELOG section as notes (pre-release when the tag has a "-suffix"). `CHANGELOG.md` starts with 0.1.0.
   - **User guide** (`docs/user-guide.md`, pictures in `docs/images/` made from the made-up sample project by `tools/make_guide_screenshots.py`): installing (incl. SmartScreen "More info ▸ Run anyway", Gatekeeper "right-click ▸ Open" / "Open Anyway", AppImage chmod), first project, the main window, sections and front matter, compounds and fields, structures, schemes, GPs, spectra, references, checks, compile and preview, undo, updates, feedback, where data lives and sharing. **Help ▸ User guide** (F1, first item of the Help menu) shows the bundled copy in a window (headings on the left, search, links between headings, pictures fitted to the width) and opens the GitHub page if the file is missing.
   - **Example project:** Nathan will provide a dataset later for an example project shipped with the program; not built yet.

## 14. Glossary

- **SI** — Supporting/Supplemental Information.
- **Project Folder** — the folder holding all data for one SI.
- **Section** — an item in the tree (custom/Text, Attachment (formerly "File"), Data, or the Spectra section); custom sections can contain sub-sections (§13k.5).
- **Attachment section** — a section holding one PDF or picture made elsewhere (stored type `"file"`, §6.2, §13l.8).
- **Front matter** — the cover page, table of contents and General Information at the start of the SI (§13k.4).
- **Compound Page** — the form for one compound inside a Data section.
- **ID** — the compound label used in the manuscript (e.g. 3g); assigned automatically from the ID table.
- **General Procedure (GP)** — a shared procedure referenced by many compounds.
- **Flag** — a user-set marker on a field meaning "needs attention".
- **Warning** — an automatic, dismissible message from an error check.
- **Typst** — the typesetting engine used to produce the final PDF (a modern alternative to LaTeX).
