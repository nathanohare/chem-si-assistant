import pytest

from si_assistant.compile.text import (charge_runs, format_chemistry, format_nmr, formula_runs,
                                       paragraphs_to_typst, runs_to_typst, typst_string)
from si_assistant.core.markup import SUB, SUPER, Paragraph, Run, parse, serialize


def show(runs):
    """Runs back in the readable stored format, to compare easily."""
    return serialize([Paragraph(runs)])


def test_typst_strings_escape_everything():
    assert typst_string('a "quote" \\ #*_$@<>[]') == '"a \\"quote\\" \\\\ #*_$@<>[]"'
    assert runs_to_typst([Run("x", bold=True, italic=True, valign=SUPER)]) == \
        '#strong[#emph[#super[#"x"]]]'


@pytest.mark.parametrize("text,expected", [
    ("in CDCl3 and CH2Cl2", "in CDCl_{3} and CH_{2}Cl_{2}"),
    ("dried over Na2SO4", "dried over Na_{2}SO_{4}"),
    ("Et3N (2.0 equiv)", "Et_{3}N (2.0 equiv)"),
    ("in DMSO-d6", "in DMSO-*d*_{6}"),
    ("product 3a in THF at 25 °C", "product 3a in THF at 25 °C"),  # untouched
    ("1H NMR", "1H NMR"),  # nuclei are only formatted in NMR text
])
def test_chemistry_formatting(text, expected):
    assert show(format_chemistry([Run(text)])) == expected


def test_nmr_formatting():
    text = "1H NMR (400MHz, CDCl3) δ 7.25-7.20 (m, 2H), 4.28 (q, J = 7.1 Hz, 2H)"
    assert show(format_nmr([Run(text)])) == (
        "^{1}H NMR (400 MHz, CDCl_{3}) δ 7.25–7.20 (m, 2H), 4.28 (q, *J* = 7.1 Hz, 2H)")
    assert show(format_nmr([Run("13C NMR (101 MHz, CDCl3) δ 164.2")])) == \
        "^{13}C NMR (101 MHz, CDCl_{3}) δ 164.2"
    assert show(format_nmr([Run("19F{1H} NMR")])) == r"^{19}F{^{1}H\} NMR"  # "}" is escaped when stored


def test_formatting_keeps_existing_formatting():
    runs = [Run("1H NMR", bold=True), Run(" (CDCl3)")]
    assert show(format_nmr(runs)) == "**^{1}H NMR** (CDCl_{3})"


def test_formula_and_charge_runs():
    assert show(formula_runs("C12H14NO")) == "C_{12}H_{14}NO"
    assert show(formula_runs("C9H10[81Br]O2")) == "C_{9}H_{10}^{81}BrO_{2}"
    assert show(charge_runs("[M+H]+")) == "[M+H]^{+}"
    assert show(charge_runs("[M-H]-")) == "[M−H]^{−}"


def test_paragraphs_with_headings():
    out = paragraphs_to_typst(parse("# Materials\nAll **dry**.\n\n## Detail\nx"))
    # Typed headings are never numbered (only real sections are: SPEC §13k.5).
    assert out.splitlines()[0] == '#heading(level: 2, outlined: true, numbering: none)[#"Materials"]'
    assert '#heading(level: 3, outlined: false, numbering: none)[#"Detail"]' in out
    assert '#"All "#strong[#"dry"]#"."' in out
