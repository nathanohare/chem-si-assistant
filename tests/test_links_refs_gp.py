"""Phase 7 core: mentions, citations, references, general procedures (SPEC §6.3.3, §7.5,
§9.5, §13j)."""

import json
import re

import pytest
from pypdf import PdfReader

from si_assistant.compile.blocks import compound_block
from si_assistant.compile.document import CompileOptions
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.core import checks, links, procedures
from si_assistant.core import references as R
from si_assistant.core.markup import Paragraph, Run, parse, serialize
from si_assistant.core.project import Project


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------------- markup

def test_links_round_trip():
    text = ("Prepared from @{c-abc123} and **@{c-def456}**^{@cite{r-1,r-2}} (email a@b.c).\n"
            "!image{images/pic.png} The *crude* mixture\n"
            "Literal \\@{not a link} and \\!image{not a picture}")
    paras = parse(text)
    assert [r.mention for r in paras[0].runs if r.mention] == ["c-abc123", "c-def456"]
    assert [r.citations for r in paras[0].runs if r.citations] == [["r-1", "r-2"]]
    assert paras[0].runs[3].bold and paras[0].runs[4].valign == "super"
    assert paras[1].image == "images/pic.png" and paras[1].plain_text == "The crude mixture"
    assert paras[2].image == "" and paras[2].plain_text == "Literal @{not a link} and !image{not a picture}"
    out = serialize(paras)
    assert out.split("\n")[:2] == text.split("\n")[:2]
    assert out.split("\n")[2] == "Literal \\@{not a link\\} and !image{not a picture\\}"
    assert [p.plain_text for p in parse(out)] == [p.plain_text for p in paras]
    assert parse("\\!image{x} y")[0].image == "" and serialize(parse("\\!image{x\\} y")) == \
        "\\!image{x\\} y"
    # Two mentions side by side stay two mentions.
    two = parse("@{c-1}@{c-1}")
    assert len(two[0].runs) == 2 and serialize(two) == "@{c-1}@{c-1}"


# ---------------------------------------------------------------------- references

BIBTEX = r"""
@article{fulmer2010,
  title = {{NMR} Chemical Shifts of Trace Impurities: Common Laboratory Solvents},
  author = {Fulmer, Gregory R. and Miller, Alexander J. M. and Sherden, Nathaniel H.},
  journal = {Organometallics},
  volume = {29}, number = {9}, pages = {2176--2179}, year = {2010},
  doi = {10.1021/om100106e}
}
@book{clayden, title="Organic Chemistry", author="Jonathan Clayden and Nick Greeves",
  publisher = {Oxford University Press}, year = 2012}
"""

RIS = """TY  - JOUR
AU  - Kim, Hyun
AU  - Lee, Sun
TI  - A dehydrogenation
JO  - Org. Lett.
PY  - 2021/05/03
VL  - 23
IS  - 10
SP  - 3911
EP  - 3915
DO  - https://doi.org/10.1021/acs.orglett.1c01234
ER  -
"""


def test_bibtex_and_ris_import():
    bib = R.parse_file_text(BIBTEX)
    assert len(bib) == 2
    f = bib[0]
    assert f["authors"][0] == "Fulmer, Gregory R." and f["pages"] == "2176–2179"
    assert f["title"].startswith("NMR Chemical Shifts") and f["doi"] == "10.1021/om100106e"
    assert R.short_label(f) == "Fulmer et al. 2010"
    assert bib[1]["type"] == "book" and bib[1]["authors"] == ["Clayden, Jonathan", "Greeves, Nick"]
    ris = R.parse_file_text(RIS)[0]
    assert ris["journal"] == "Org. Lett." and ris["year"] == "2021" and ris["pages"] == "3911–3915"
    assert ris["doi"] == "10.1021/acs.orglett.1c01234"
    assert R.short_label(ris) == "Kim & Lee 2021"
    assert R._delatex(r"Schr\"{o}dinger and M\"uller \& Co. CO$_2$") == "Schrödinger and Müller & Co. CO2"


def test_crossref_record():
    msg = {"type": "journal-article", "title": ["NMR Chemical Shifts of <i>Trace</i> Impurities"],
           "author": [{"given": "Gregory R.", "family": "Fulmer"}],
           "container-title": ["Organometallics"], "short-container-title": ["Organometallics"],
           "volume": "29", "issue": "9", "page": "2176-2179", "DOI": "10.1021/om100106e",
           "published-print": {"date-parts": [[2010, 5, 10]]}}
    ref = R.from_crossref(msg)
    assert ref["title"] == "NMR Chemical Shifts of Trace Impurities"
    assert ref["year"] == "2010" and ref["pages"] == "2176–2179"
    assert R.normalise_doi("https://doi.org/10.1021/OM100106E ") == "10.1021/OM100106E"
    with pytest.raises(R.ReferenceError):
        R.fetch_doi("not a doi")


def test_project_references_and_duplicates(project):
    a = project.add_reference(R.parse_bibtex(BIBTEX)[0])
    again = project.add_reference({"doi": "https://doi.org/10.1021/OM100106E", "title": "x"})
    assert again["id"] == a["id"] and len(project.references) == 1
    reopened = Project.open(project.folder)
    assert reopened.references[0]["authors"][1] == "Miller, Alexander J. M."
    json.loads(R.to_hayagriva(reopened.references))  # valid JSON (= valid YAML)


# ---------------------------------------------------------------------- general procedures

def test_gp_sentence_placeholders(project):
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["1a", "3a"])
    sm = project.add_compound(data.id, name="Amide")
    c = project.add_compound(data.id, name="Enamide")
    gp = project.add_general_procedure(data.id)
    assert gp["name"] == "General Procedure A"
    assert project.add_general_procedure(data.id)["name"] == "General Procedure B"
    assert procedures.placeholders(gp["sentence"]) == ["GP", "starting material", "scale"]

    c.values["procedure"] = {"gp": gp["id"], "values": {"Starting Material": f"@{{{sm.id}}}"}}
    c.values["scale"] = {"value": "0.20", "unit": "mmol"}
    project.save_compound(c)
    project.write_compound_text(c, "procedure", "Purification by flash chromatography afforded "
                                f"@{{{c.id}}} as a colorless oil.")
    block = compound_block(project, c, "3a")
    first = block.paragraphs[0]
    shown = links.display_runs(project, first.runs)
    assert "".join(r.text for r in shown) == (
        "Prepared according to General Procedure A from 1a (0.20 mmol). Purification by "
        "flash chromatography afforded 3a as a colorless oil.")
    assert project.field_filled(c, next(d for d in project.field_defs(c) if d.key == "procedure"))

    # A missing value is a warning. A typed {scale} differing from the filled Reaction
    # scale field is a warning too, and the field is printed (SPEC §13n.9).
    project.update_general_procedure(gp["id"], sentence="Following {GP}, {starting material} "
                                                        "({scale}) was heated to {temperature}.")
    c.values["procedure"]["values"]["scale"] = "0.5 mmol of amide"
    ws = [w for w in checks.all_warnings(project, c, project.id_report()) if w.check == "gp"]
    assert len(ws) == 2 and "{temperature}" in ws[0].message
    assert ws[1].key == "gp:scale-differs" and "0.20 mmol" in ws[1].message
    text = "".join(r.text for r in links.display_runs(
        project, compound_block(project, c, "3a").paragraphs[0].runs))
    assert text.startswith("Following General Procedure A, 1a (0.20 mmol) was heated "
                           "to [temperature].")
    # Typed values override the other automatic ones (§2.7), and {scale} when the Reaction
    # scale field is empty.
    c.values["procedure"]["values"]["GP"] = "GP-A"
    c.values["scale"] = {"value": "", "unit": "mmol"}
    assert not [w for w in checks.all_warnings(project, c, project.id_report())
                if w.key == "gp:scale-differs"]
    text = "".join(r.text for r in links.display_runs(
        project, compound_block(project, c, "3a").paragraphs[0].runs))
    assert text.startswith("Following GP-A, 1a (0.5 mmol of amide) was heated")
    del c.values["procedure"]["values"]["GP"]

    # Deleting the GP: the compound keeps its choice and gets a warning; restore brings it back.
    entry = project.delete_general_procedure(gp["id"])
    assert any(w.key == "gp:missing" for w in checks.all_warnings(project, c, project.id_report()))
    project.restore_from_trash(entry.id)
    assert project.compound_gp(c)["name"] == "General Procedure A"


def test_broken_mentions_and_citations_warn(project):
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["1", "2"])
    a = project.add_compound(data.id)
    b = project.add_compound(data.id)
    project.write_compound_text(b, "procedure", f"From @{{{a.id}}} @cite{{r-gone}}.")
    assert links.mention_uses(project, a.id) == ["2 ▸ Experimental procedure"]
    project.delete_compound(a.id)
    ws = checks.all_warnings(project, b, project.id_report())
    assert {w.check for w in ws} >= {"mention", "citation"}
    assert links.plain_text(project, project.read_compound_text(b, "procedure")) == \
        "From ?? [missing reference]."


# ---------------------------------------------------------------------- compiling

@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_compile_with_gp_mentions_and_references(qapp, tmp_path, style):
    from tests.sample_project import build_sample
    p, parts = build_sample(tmp_path)
    p.settings["journal_style"] = style
    data, c2, c4 = parts["data"], parts["c2"], parts["c4"]
    ref = p.add_reference(R.parse_bibtex(BIBTEX)[0])
    gp = p.add_general_procedure(data.id)
    p.update_general_procedure(gp["id"], title="Dehydrogenation of Amides")
    p.write_gp_text(gp["id"], "A vial was charged with the amide (0.20 mmol) in CDCl3."
                              f"@cite{{{ref['id']}}}")
    c4.values["procedure"] = {"gp": gp["id"], "values": {"starting material": f"@{{{c2.id}}}"}}
    c4.values["scale"] = {"value": "0.20", "unit": "mmol"}
    p.add_field(c4, {"key": "references"})
    c4.values["references"]["refs"] = [ref["id"]]
    p.save_compound(c4)
    general = p.sections[0]
    p.write_section_text(general, p.read_section_text(general) + f"\nSee compound @{{{c4.id}}}.")

    result = compile_pdf(p, CompileOptions.from_settings(p.settings), image_info, convert_to_png)
    pages = [pg.extract_text() or "" for pg in PdfReader(str(result.pdf)).pages]
    text = squash("".join(pages))
    assert "GeneralProcedureA:DehydrogenationofAmides" in text
    assert "PreparedaccordingtoGeneralProcedureAfrom2(0.20mmol)." in text
    assert "Seecompound4." in text
    assert "Spectraldatamatchedthosereportedintheliterature." in text
    assert "Fulmer" in squash(pages[-1]) and "Organometallics" in squash(pages[-1])
    # The same reference cited twice gets one entry.
    assert squash(pages[-1]).count("Fulmer") == 1
    assert result.warnings == []
    p.close()
