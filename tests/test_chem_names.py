"""Italic prefixes in compound names (core/chem_names.py)."""

import pytest

from si_assistant.core.chem_names import italic_spans, name_runs


def marked(name: str) -> str:
    out, at = "", 0
    for start, end in italic_spans(name):
        out += name[at:start] + "<" + name[start:end] + ">"
        at = end
    return out + name[at:]


@pytest.mark.parametrize("name, expected", [
    ("N-Benzyl-2-phenylpyridinium bromide", "<N>-Benzyl-2-phenylpyridinium bromide"),
    ("N,N-dimethylformamide", "<N>,<N>-dimethylformamide"),
    ("N,N′-dimethylurea", "<N>,<N>′-dimethylurea"),
    ("O-acetyl-S-methyl", "<O>-acetyl-<S>-methyl"),
    ("4-(N-Boc-amino)pyridine", "4-(<N>-Boc-amino)pyridine"),
    ("Indolizidine 201 N-H Piperidine int.", "Indolizidine 201 <N>-H Piperidine int."),
    ("tert-butyl sec-butyl", "<tert>-butyl <sec>-butyl"),
    ("trans-2-methylcyclohexanol", "<trans>-2-methylcyclohexanol"),
    ("1-Benzyl-cis-(6-ethyl-2-phenyl)piperidine", "1-Benzyl-<cis>-(6-ethyl-2-phenyl)piperidine"),
    ("(2E,4Z)-hexa-2,4-dienoate", "(2<E>,4<Z>)-hexa-2,4-dienoate"),
    ("(1R,2S)-2-phenylcyclopropan-1-ol", "(1<R>,2<S>)-2-phenylcyclopropan-1-ol"),
    ("1-Benzyl-2-((E)-styryl)-1,2-dihydropyridine", "1-Benzyl-2-((<E>)-styryl)-1,2-dihydropyridine"),
    ("[(R)-BINAP]", "[(<R>)-BINAP]"),
    ("1-benzyl-2-(p-tolyl)pyridinium", "1-benzyl-2-(<p>-tolyl)pyridinium"),
    ("o-xylene and m-CPBA", "<o>-xylene and <m>-CPBA"),
])
def test_conventional_prefixes_are_italic(name, expected):
    assert marked(name) == expected


@pytest.mark.parametrize("name", [
    "Transfer", "SNAr product", "1H-indole", "N1-benzyl", "Boc-protected", "4N-x",
    "transition state", "2-methylpropane", "Compound (R1)", "Nb-catalysed", "BnN-x",
])
def test_ordinary_words_are_left_upright(name):
    assert italic_spans(name) == []


def test_name_runs_are_bold_with_italic_parts():
    runs = name_runs("N-Benzyl-(S)-x", bold=True)
    assert [(r.text, r.italic) for r in runs] == [
        ("N", True), ("-Benzyl-(", False), ("S", True), (")-x", False)]
    assert all(r.bold for r in runs)
    assert [(r.text, r.italic) for r in name_runs("x")] == [("x", False)]


def test_a_name_with_asterisks_is_printed_as_typed():
    runs = name_runs("*N*-Benzyl 1H-*x* trans-y")   # typed over: nothing automatic
    assert [(r.text, r.italic) for r in runs] == [
        ("N", True), ("-Benzyl 1H-", False), ("x", True), (" trans-y", False)]


def test_relative_configuration_asterisks_are_characters():
    runs = name_runs("(1R*,2S*)-2-methyl")
    assert "".join(r.text for r in runs) == "(1R*,2S*)-2-methyl"
    assert [r.text for r in runs if r.italic] == ["R", "S"]
    # …also next to an italic typed by hand
    runs = name_runs("(R*)-x *cis*")
    assert "".join(r.text for r in runs) == "(R*)-x cis"
    assert [r.text for r in runs if r.italic] == ["cis"]


def test_compound_title_prints_the_italics():
    from types import SimpleNamespace
    from si_assistant.compile.blocks import compound_title
    from si_assistant.compile.text import runs_to_typst
    title = compound_title(None, SimpleNamespace(name="N-Benzyl-2-(p-tolyl)pyridinium"), "1b")
    code = runs_to_typst(title)
    assert "emph" in code or "_N_" in code
    assert all(r.bold for r in title)
    assert "".join(r.text for r in title) == "N-Benzyl-2-(p-tolyl)pyridinium (1b)."
