import pytest

from si_assistant.core.markup import SUB, SUPER, Paragraph, Run, parse, serialize


def runs(text):
    (p,) = parse(text)
    return p.runs


def test_plain_text():
    assert runs("hello") == [Run("hello")]


def test_bold_italic_super_sub():
    assert runs("**¹H NMR** (400 MHz, CDCl_{3}) *J* = 2.0 Hz, ^{13}C") == [
        Run("¹H NMR", bold=True),
        Run(" (400 MHz, CDCl"),
        Run("3", valign=SUB),
        Run(") "),
        Run("J", italic=True),
        Run(" = 2.0 Hz, "),
        Run("13", valign=SUPER),
        Run("C"),
    ]


def test_bold_and_italic_together():
    assert runs("***both***") == [Run("both", bold=True, italic=True)]


def test_headings():
    paras = parse("# Top\n## Sub\nnormal\n### three hashes")
    assert [p.heading for p in paras] == [1, 2, 0, 0]
    assert paras[0].plain_text == "Top"
    assert paras[3].plain_text == "### three hashes"


def test_escapes():
    assert runs(r"a\*b \^{x} \_{y} 2\\3") == [Run(r"a*b ^{x} _{y} 2\3")]


def test_unclosed_markers_do_not_leak_to_next_line():
    paras = parse("**bold\nplain")
    assert paras[1].runs == [Run("plain")]


@pytest.mark.parametrize("paragraphs", [
    [Paragraph([Run("a*b_c^d}e\\f{g")])],
    [Paragraph([Run("# not a heading")])],
    [Paragraph([Run("Title", italic=True)], heading=1), Paragraph([]), Paragraph([Run("x")])],
    [Paragraph([Run("x", bold=True, valign=SUPER), Run("y", valign=SUPER), Run("z", bold=True)])],
    [Paragraph([Run("a", bold=True, italic=True), Run("b", italic=True), Run("c", bold=True),
                Run("d")])],
    [Paragraph([Run("C"), Run("6", valign=SUB), Run("H"), Run("5", valign=SUB),
                Run("+", valign=SUPER)])],
    [Paragraph([Run("^"), Run("{x", valign=SUB)])],
    [Paragraph([Run("_"), Run("x", bold=True), Run("{")])],
])
def test_roundtrip(paragraphs):
    assert parse(serialize(paragraphs)) == paragraphs


def test_serialize_is_readable():
    text = serialize([Paragraph([Run("CDCl"), Run("3", valign=SUB), Run(" "),
                                 Run("δ", italic=True)])])
    assert text == "CDCl_{3} *δ*"
