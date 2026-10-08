"""The sample project used as the regression test for compiling (SPEC §12).

Everything here is MADE UP (SPEC §13m.1): the authors, emails, title, affiliation and the
NMR/HRMS numbers are invented for testing and describe no real paper. The data are kept
internally consistent so the error checks give no warnings: ¹H integrations add up to
the structure's H count, ¹³C lists one signal per symmetry-distinct carbon, the HRMS
"found" values are within 2 ppm of what the program calculates, and no singlet sits on a
known solvent/impurity shift.

To use another dataset, replace this one module: keep `build_sample(parent)` returning
`(project, parts)` with the same keys, and update `FACTS`, which holds every value the
tests look for in the compiled SI.
"""

from __future__ import annotations

from pathlib import Path

import typst

from si_assistant.core import chem
from si_assistant.core.project import Project
from si_assistant.core.tables import Cell, Table

# What the tests expect to find (in the project and in the compiled PDF).
FACTS = {
    "project_name": "Demo SI",
    "title": "A Made-Up Study of Simple Amides and Alcohols for Testing Chem SI Assistant",
    "title_word": "Made-Up",          # a word of the title that survives PDF text extraction
    "authors": ["A. Researcher", "B. Chemist"],
    "email": "b.chemist@example.com",
    "data_section": "Synthesis of Example Compounds",
    "c2_name": "N-Benzyl-N-methylacetamide",
    "c2_yield": "Colorless oil (29.4 mg, 90%)",
    "c2_hrms": ("164.1070", "164.1072"),     # (calculated, found), [M+H]+ C10H14NO
    "c4_name": "1-(4-Methoxyphenyl)ethan-1-ol",
    "c4_hrms": ("175.0730", "175.0728"),     # [M+Na]+ C9H12NaO2
    "c2_mention_in_table": "2: 90%",
    "scheme_caption": "Reduction of ketone to alcohol 4",
    "table_caption": "Optimization of the acetylation",   # "*acetylation*" in the source
    "figure_caption": "Amide 2 (see Scheme S1).",
}


def fake_spectrum_pdf(path: Path, title: str, pages: int = 1) -> Path:
    """A PDF that looks like an exported NMR spectrum (one or more pages)."""
    body = "\n#pagebreak()\n".join(
        f"#align(center)[{title} (page {i + 1})]\n"
        "#line(start: (5%, 70%), end: (95%, 70%))\n"
        "#for x in range(30) { place(dx: 10% + x * 2.6%, dy: 70% - calc.rem(x * 37, 50) * 1%,"
        " line(length: calc.rem(x * 37, 50) * 1%, angle: -90deg)) }"
        for i in range(pages))
    src = path.with_suffix(".typ")
    src.write_text("#set page(width: 11in, height: 8.5in)\n" + body, encoding="utf-8")
    typst.compile(str(src), output=str(path))
    src.unlink()
    return path


def build_sample(parent: Path) -> tuple[Project, dict]:
    p = Project.create(parent, FACTS["project_name"])
    p.acquire_lock("Tester")
    first, second = FACTS["authors"]
    p.settings.update({
        "title": FACTS["title"],
        "authors": [{"name": first, "affiliations": ["a"], "corresponding": False,
                     "email": ""},
                    {"name": second, "affiliations": ["a"], "corresponding": True,
                     "email": FACTS["email"]}],
        "affiliations": [{"marker": "a", "text": "Department of Chemistry, Example University, "
                                                 "Exampletown 12345, Country"}],
    })
    p.save_settings()

    general = p.add_section("text", "General Information")
    p.write_section_text(general, "Unless otherwise noted, materials were purchased from "
                         "commercial suppliers. ^{1}H NMR spectra were recorded at 400 MHz in "
                         "CDCl3.\n## Abbreviations\nDMAP = 4-(dimethylamino)pyridine.")

    data = p.add_section("data", FACTS["data_section"])
    p.set_id_table(data.id, ["2", "4"])

    # Compound 2: a tertiary amide seen as two rotamers (two labelled ¹H reports).
    c2 = p.add_compound(data.id, name=FACTS["c2_name"])
    p.set_structure(c2, "CC(=O)N(C)Cc1ccccc1")                       # C10H13NO
    p.write_compound_text(c2, "procedure",
                          "Following the general procedure A, **2** was purified by flash column "
                          "chromatography (ethyl acetate/hexanes = 1/4). The amide was "
                          "obtained as two rotamers in a ratio of around 3:2.")
    c2.values["yield"] = {"percent": "90", "mass": "29.4", "mass_unit": "mg"}
    c2.values["physical_state"] = {"text": "colorless oil"}
    c2.values["nmr"] = [
        {"id": "r1", "nucleus": "1H", "label": "major rotamer",
         "text": "1H NMR (400 MHz, CDCl3) δ 7.38 – 7.27 (m, 3H), 7.20 – 7.14 (m, 2H), "
                 "4.60 (s, 2H), 2.93 (s, 3H), 2.15 (s, 3H)"},
        {"id": "r2", "nucleus": "1H", "label": "minor rotamer",
         "text": "1H NMR (400 MHz, CDCl3) δ 7.38 – 7.27 (m, 3H), 7.25 – 7.21 (m, 2H), "
                 "4.53 (s, 2H), 2.91 (s, 3H), 2.13 (s, 3H)"},
        {"id": "r3", "nucleus": "13C", "label": "",
         "text": "13C NMR (100 MHz, CDCl3) δ 170.9, 137.3, 128.9, 128.0, 127.4, 53.9, "
                 "34.5, 21.6"}]
    c2.values["hrms"] = {"ionization": "ESI", "adduct": "[M+H]+", "found": FACTS["c2_hrms"][1],
                         "species": "", "formula": ""}
    p.save_compound(c2)
    folder = parent / "spectra_src"
    folder.mkdir(exist_ok=True)
    p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(folder / "2_1H.pdf", "1H NMR of 2"),
                           {"type": "1H"})
    p.attach_compound_file(c2, "spectra", fake_spectrum_pdf(folder / "2_13C.pdf", "13C NMR of 2",
                                                            pages=2), {"type": "13C"})

    # Compound 4: a secondary alcohol, made in a scheme (ketone -> alcohol).
    scheme = p.add_scheme(data.id, name=FACTS["scheme_caption"])
    c4 = p.add_compound(data.id, name=FACTS["c4_name"], scheme_id=scheme.id)
    p.set_structure(c4, "COc1ccc(C(C)O)cc1")                         # C9H12O2
    c4.values["yield"] = {"percent": "85", "mass": "12.9", "mass_unit": "mg"}
    c4.values["physical_state"] = {"text": "colorless oil"}
    c4.values["nmr"][0]["text"] = ("1H NMR (400 MHz, CDCl3) δ 7.30 (d, J = 8.6 Hz, 2H), 6.89 "
                                   "(d, J = 8.6 Hz, 2H), 4.86 (q, J = 6.4 Hz, 1H), 3.81 (s, 3H), "
                                   "1.78 (br s, 1H), 1.48 (d, J = 6.4 Hz, 3H)")
    c4.values["nmr"][1]["text"] = ("13C NMR (100 MHz, CDCl3) δ 159.0, 138.1, 126.7, 113.9, "
                                   "70.0, 55.3, 25.1")
    c4.values["hrms"] = {"ionization": "ESI", "adduct": "[M+Na]+", "found": FACTS["c4_hrms"][1],
                         "species": "", "formula": ""}
    p.save_compound(c4)
    # The scheme, drawn with the scheme editor: the starting ketone -> alcohol 4, printed
    # with its label and caption (mentioned as Scheme S1; off by default, SPEC §13p.1).
    from si_assistant.core import scheme_editor as se
    se.set_box_structure(p, scheme, se.starting(scheme)[0]["id"], "CC(=O)c1ccc(OC)cc1")
    se.link_compound(p, scheme, se.steps(scheme)[0]["products"][0]["id"], c4.id)
    se.set_conditions(p, scheme, se.steps(scheme)[0]["id"], "top", "NaBH4")
    se.set_conditions(p, scheme, se.steps(scheme)[0]["id"], "bottom", "MeOH, 0 °C")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme), "show_label": True})

    # A custom section with a table (merged cells, footnotes, a compound mention) and a
    # figure (SPEC §13k.5); General Information mentions both.
    notes = p.add_section("text", "Reaction Optimization")
    owner = p.text_owner(notes.id)
    table = Table.new(4, 3)
    table.rows[0] = [Cell("Entry"), Cell("Conditions^{a}", colspan=2), None]
    table.rows[1] = [Cell("1"), Cell("Ac2O, DMAP, CH2Cl2"), Cell(f"**@{{{c2.id}}}**: 90%^{{b}}")]
    table.rows[2] = [Cell("2", rowspan=2), Cell("no DMAP"), Cell("12%")]
    table.rows[3] = [None, Cell("no base"), Cell("<5%")]
    table.footnotes = ["^{a} 0.2 mmol scale.", "^{b} Isolated yield."]
    table_id = p.add_table(owner, table)
    figure_id = p.add_figure(owner, data=chem.structure_png(chem.parse_structure(
        "CC(=O)N(C)Cc1ccccc1"), 600, 300), ext=".png")
    p.write_section_text(notes, f"Conditions were screened as shown in @{{{table_id}}}.\n"
                                f"!table{{{table_id}}} Optimization of the *acetylation*\n"
                                f"!figure{{{figure_id}}} Amide 2 (see @{{{scheme.id}}}).")
    p.write_section_text(general, p.read_section_text(general)
                         + f"\nThe best conditions are in @{{{table_id}}} and @{{{figure_id}}}.")
    # The Spectra section, added at the end as when the project is opened for editing
    # (SPEC §13k.12).
    spectra = p.ensure_spectra_section()
    return p, {"c2": c2, "c4": c4, "scheme": scheme, "data": data, "notes": notes,
               "table": table_id, "figure": figure_id, "spectra": spectra}
