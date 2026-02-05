import pytest

from avrae_ls.runtime import argparser
from avrae_ls.runtime.argparser import Argument, EphemeralArgument


def test_argsplit_matches_avrae_style_quotes():
    assert argparser.argsplit('foo bar "two words"') == ["foo", "bar", "two words"]
    assert argparser.argsplit("-title \u201cHello World\u201d") == ["-title", "Hello World"]
    assert argparser.argsplit("-desc \u00abFlavor\u00bb") == ["-desc", "Flavor"]


def test_argsplit_unclosed_quote_raises():
    with pytest.raises(argparser.ExpectedClosingQuoteError):
        argparser.argsplit("-title \u201cHello")


def test_argparse_arg_respects_ephemeral_flag():
    assert argparser._argparse_arg("d", None, True, 0, parse_ephem=True) == Argument("d", True, 0)
    assert argparser._argparse_arg("d", "1", True, 0, parse_ephem=True) == EphemeralArgument("d", True, 0, 1)
    assert argparser._argparse_arg("d", "1", True, 0, parse_ephem=False) == Argument("d1", True, 0)


def test_argparse_iterator_matches_avrae_dfa():
    args = ["d", "d1", "adv1", "adv", "-i"]
    parsed = list(argparser._argparse_iterator(args, parse_ephem=True))
    assert parsed == [
        Argument("d", True, 0),
        EphemeralArgument("d", True, 1, 1),
        EphemeralArgument("adv", True, 2, 1),
        Argument("adv", True, 3),
        Argument("i", True, 4),
    ]


@pytest.mark.parametrize(
    ("args", "parse_ephem", "expected"),
    [
        (["12345", "this is junk", "!*&^#&(*#"], True, []),
        (["-d"], True, [Argument("d", True, 0)]),
        (["-d1"], True, [EphemeralArgument("d", True, 0, 1)]),
        (["-d", "5"], True, [Argument("d", "5", 0)]),
        (["-d1", "5"], False, [Argument("d1", "5", 0)]),
        (["-d", "-1d6"], True, [Argument("d", "-1d6", 0)]),
        (["-d1", "-1d6"], False, [Argument("d1", "-1d6", 0)]),
        (["-t", "d5"], True, [Argument("t", "d5", 0)]),
        (["-t", "-i"], True, [Argument("t", True, 0), Argument("i", True, 1)]),
        (["-t", "-t"], True, [Argument("t", True, 0), Argument("t", True, 1)]),
    ],
)
def test_argparse_iterator_edge_cases(args, parse_ephem, expected):
    parsed = list(argparser._argparse_iterator(args, parse_ephem=parse_ephem))
    assert parsed == expected
