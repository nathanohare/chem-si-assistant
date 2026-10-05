"""Links in the PDF from the peak reports to the spectra, and back (SPEC §13l.7)."""

import json
import re

import pytest
from pypdf import PdfReader
from pypdf.generic import ArrayObject

from si_assistant.compile.blocks import nmr_sentence
from si_assistant.compile.document import CompileOptions, build
from si_assistant.compile.qt_images import convert_to_png, image_info
from si_assistant.compile.run import compile_pdf
from si_assistant.compile.text import LinkedRun, runs_to_typst
from si_assistant.core import spectra as S
from si_assistant.core.markup import Run
from si_assistant.core.project import Project
from tests.sample_project import FACTS, build_sample


@pytest.fixture
def sample(qapp, tmp_path):
    p, parts = build_sample(tmp_path)
    yield p, parts
    p.close()


def links_on(p, style="plain", back=True, on=True):
    p.set_spectra_defaults({**p.spectra_defaults(), "nmr_links": on, "link_style": style,
                            "link_back": back})


def compiled(p, **kw) -> PdfReader:
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    return PdfReader(str(compile_pdf(p, options, image_info, convert_to_png).pdf))


def source(p, **kw) -> str:
    options = CompileOptions.from_settings(p.settings)
    for k, v in kw.items():
        setattr(options, k, v)
    return build(p, options, image_info, convert_to_png).typ_path.read_text(encoding="utf-8")


def links(reader: PdfReader) -> set[tuple[int, int]]:
    """(page, page it jumps to) of every link inside the PDF (from 0)."""
    pages = {page.indirect_reference.idnum: i for i, page in enumerate(reader.pages)}
    out = set()
    for i, page in enumerate(reader.pages):
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            dest = annot.get("/Dest")
            if dest is None and annot.get("/A") is not None:
                dest = annot["/A"].get_object().get("/D")
            dest = dest.get_object() if hasattr(dest, "get_object") else dest
            if isinstance(dest, ArrayObject):
                out.add((i, pages[dest[0].idnum]))
    return out


def page_with(reader: PdfReader, pattern: str) -> int:
    for i, page in enumerate(reader.pages):
        if re.search(pattern, page.extract_text() or ""):
            return i
    raise AssertionError(f"no page with {pattern!r}")


def spectrum_pages(reader: PdfReader) -> dict[str, int]:
    """The page of compound 2's first ¹H and ¹³C spectrum, and the ¹³C file's 2nd page."""
    return {"1H": page_with(reader, r"1H NMR of 2 \(page 1\)"),
            "13C": page_with(reader, r"13C NMR of 2 \(page 1\)"),
            "13C-2": page_with(reader, r"13C NMR of 2 \(page 2\)")}


# ---------------------------------------------------------------------- the setting

def test_the_links_are_off_by_default_and_kept_for_the_whole_section(sample):
    p, parts = sample
    d = p.spectra_defaults()
    assert (d["nmr_links"], d["link_style"], d["link_back"]) == (False, "plain", False)
    links_on(p, "blue_underline")
    stored = json.loads((parts["spectra"].folder / "section.json").read_text(encoding="utf-8"))
    assert stored["defaults"]["nmr_links"] is True
    assert stored["defaults"]["link_style"] == "blue_underline"
    again = Project.open(p.folder)
    assert again.spectra_defaults()["link_back"] is True
    p.set_spectra_defaults({**p.spectra_defaults(), "link_style": "purple"})   # not a choice
    assert p.spectra_defaults()["link_style"] == "plain"
    # Never a setting of one spectrum.
    assert S.clean({"nmr_links": True, "per_page": 2}, spectrum=True) == {"per_page": 2}
    assert "nmr_links" not in S.overrides({"layout": {"nmr_links": True}}, "1")


def test_the_page_shows_the_link_options(sample):
    from si_assistant.ui.spectra_panel import SpectraSectionPanel
    p, parts = sample
    panel = SpectraSectionPanel(p, parts["spectra"])
    assert not panel.nmr_links.isChecked()
    assert not panel.link_style.isEnabled() and not panel.link_back.isEnabled()
    panel.nmr_links.setChecked(True)
    assert panel.link_style.isEnabled() and panel.link_back.isEnabled()
    panel.link_style.setCurrentIndex(panel.link_style.findData("blue"))
    panel.link_back.setChecked(True)
    panel.flush()
    d = Project.open(p.folder).spectra_defaults()
    assert (d["nmr_links"], d["link_style"], d["link_back"]) == (True, "blue", True)
    panel.deleteLater()


# ---------------------------------------------------------------------- the text

def test_the_nmr_label_becomes_the_link():
    report = {"nucleus": "13C", "label": "",
              "text": "13C NMR (100 MHz, CDCl3) δ 171.2, 138.6"}
    runs = nmr_sentence(report, True, lambda n: f"si-s-c-1-{n}")
    linked = [r for r in runs if isinstance(r, LinkedRun)]
    assert "".join(r.text for r in linked) == "13C NMR"
    assert {r.target for r in linked} == {"si-s-c-1-13C"}
    code = runs_to_typst(runs)
    assert code.startswith('#si-link(<si-s-c-1-13C>)[#strong[#super[#"13"]]')
    assert '#strong[#" NMR"]]#" (100 MHz, "' in code          # the link ends there
    assert code.count("si-link") == 1
    # A report with a label ("major rotamer") links to the same spectrum; a report written
    # without "1H NMR" gets the label from its nucleus, linked too.
    report = {"nucleus": "1H", "label": "minor rotamer", "text": "δ 7.12 (br s, 1H)"}
    runs = nmr_sentence(report, True, lambda n: f"s-{n}")
    assert "".join(r.text for r in runs if isinstance(r, LinkedRun)) == "1H NMR"
    # No spectrum of that nucleus: no link.
    assert not any(isinstance(r, LinkedRun) for r in nmr_sentence(report, True, lambda n: ""))
    # The nucleus as the reader sees it; spaces in front are not part of the link.
    report = {"nucleus": "13C", "label": "", "text": "  13C{1H} NMR (101 MHz) δ 128.0"}
    runs = nmr_sentence(report, True, lambda n: f"s-{n}")
    assert {r.target for r in runs if isinstance(r, LinkedRun)} == {"s-13C"}
    assert "".join(r.text for r in runs if isinstance(r, LinkedRun)) == "13C{1H} NMR"
    assert runs_to_typst([Run("a"), LinkedRun("b", target="x"), LinkedRun("c", target="y")]) \
        == '#"a"#si-link(<x>)[#"b"]#si-link(<y>)[#"c"]'


def test_the_source_has_each_label_once(sample):
    p, parts = sample
    assert "si-link" not in source(p)                        # off: nothing changes
    links_on(p)
    src = source(p)
    c2 = parts["c2"].id
    assert src.count(f"#si-link(<si-s-{c2}-1H>)") == 2       # both rotamer reports
    assert src.count(f"#si-link(<si-s-{c2}-13C>)") == 1
    for label in (f"si-s-{c2}-1H", f"si-s-{c2}-13C", f"si-c-{c2}"):
        assert len(re.findall(rf"(?<!\()<{label}>", src)) == 1, label   # placed once
    assert f"#si-link(<si-c-{c2}>)" in src                   # the headers link back
    # Compound 4 has no spectra: its reports are not links.
    assert f"si-s-{parts['c4'].id}" not in src


# ---------------------------------------------------------------------- the PDF

def test_the_reports_link_to_their_spectra_and_back(sample):
    p, parts = sample
    links_on(p, "blue")
    reader = compiled(p)
    pages = spectrum_pages(reader)
    data = page_with(reader, re.escape(FACTS["c2_hrms"][1]))                     # compound 2's data
    found = links(reader)
    assert (data, pages["1H"]) in found and (data, pages["13C"]) in found
    assert (data, pages["13C-2"]) not in found               # the first page of the file
    for page in pages.values():                              # every header links back
        assert (page, data) in found
    # Nothing else links to or from the spectra (but the table of contents, as before).
    spectra = set(pages.values())
    toc = page_with(reader, "Table of Contents")
    assert {(a, b) for a, b in found if a != toc and (a in spectra or b in spectra)} == \
        {(data, pages["1H"]), (data, pages["13C"])} | {(s, data) for s in spectra}


def test_without_the_link_back(sample):
    p, parts = sample
    links_on(p, back=False)
    reader = compiled(p)
    pages = spectrum_pages(reader)
    found = links(reader)
    assert not any(a in pages.values() for a, _ in found)
    assert any(b == pages["1H"] for _, b in found)


def test_no_links_when_off(sample):
    p, parts = sample
    links_on(p, on=False)
    reader = compiled(p)
    spectra = set(spectrum_pages(reader).values())
    toc = page_with(reader, "Table of Contents")
    assert not any(a != toc and (a in spectra or b in spectra) for a, b in links(reader))
    assert "si-link" not in source(p)


def test_no_links_and_no_error_when_the_spectra_are_left_out(sample):
    p, parts = sample
    links_on(p, "blue_underline")
    reader = compiled(p, excluded=frozenset({parts["spectra"].id}))
    assert "1H NMR of 2" not in "".join(page.extract_text() or "" for page in reader.pages)
    data = page_with(reader, re.escape(FACTS["c2_hrms"][1]))
    assert not any(a == data for a, _ in links(reader))      # the reports are plain text
    # The same when the compound's section is left out: its spectra are not printed.
    reader = compiled(p, excluded=frozenset({parts["data"].id}))
    assert not any(re.search(r"NMR of 2", page.extract_text() or "") for page in reader.pages)


def test_previews_without_the_other_end_compile(sample):
    p, parts = sample
    links_on(p, "blue")
    c2 = parts["c2"].id
    # The live preview of a compound (no spectra), a compound's spectra pages alone, and a
    # section with the Spectra section: Typst never meets a link to a missing label.
    reader = compiled(p, only=("compound", c2), preview_spectra=False)
    assert links(reader) == set()
    reader = compiled(p, only=("spectra", c2))
    assert links(reader) == set()
    reader = compiled(p, only=("compound", c2))               # the toolbar Preview
    pages = spectrum_pages(reader)
    assert (0, pages["1H"]) in links(reader) and (pages["1H"], 0) in links(reader)


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_every_journal_style_links(sample, style):
    p, parts = sample
    p.settings["journal_style"] = style
    p.save_settings()
    links_on(p, "blue_underline")
    reader = compiled(p)
    pages = spectrum_pages(reader)
    data = page_with(reader, re.escape(FACTS["c2_hrms"][1]))
    assert {(data, pages["1H"]), (data, pages["13C"]), (pages["1H"], data)} <= links(reader)
