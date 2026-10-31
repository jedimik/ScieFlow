"""Checkable numbers of a claim (M-G 5.5): table-driven, synthetic sentences only."""

import pytest

from scieflow.research.manuscript.claims import numbers


def n(raw, value, decimals, percent=False, relation="="):
    return (raw, value, decimals, percent, relation)


def found(text):
    return [(d["raw"], d["value"], d["decimals"], d["percent"], d["relation"])
            for d in numbers.extract(text)]


CASES = [
    ("The mean was 0.91.", [n("0.91", "0.91", 2)]),
    ("Value 12 here", [n("12", "12", 0)]),
    ("Count 1,234 cases", [n("1,234", "1234", 0)]),
    ("Count 1 234 cases", [n("1 234", "1234", 0)]),
    ("Big 1,234,567 items", [n("1,234,567", "1234567", 0)]),
    ("A minus of \u22120.5 here", [n("\u22120.5", "-0.5", 1)]),
    ("A minus of -0.5 here", [n("-0.5", "-0.5", 1)]),
    ("A gain of +3 units", [n("+3", "3", 0)]),
    ("Rate 5% now", [n("5", "5", 0, True)]),
    ("Rate 5 % now", [n("5", "5", 0, True)]),
    ("Rate 5\\% now", [n("5", "5", 0, True)]),
    ("p < 0.001 here", [n("0.001", "0.001", 3, False, "<")]),
    ("p<0.001 here", [n("0.001", "0.001", 3, False, "<")]),
    ("p \u2264 0.05 here", [n("0.05", "0.05", 2, False, "<=")]),
    ("p \\le 0.05 here", [n("0.05", "0.05", 2, False, "<=")]),
    ("p \\leq 0.05 here", [n("0.05", "0.05", 2, False, "<=")]),
    ("p \\lt 0.05 here", [n("0.05", "0.05", 2, False, "<")]),
    ("x > 2.5", [n("2.5", "2.5", 1, False, ">")]),
    ("x \u2265 2.5", [n("2.5", "2.5", 1, False, ">=")]),
    ("x \\ge 2.5", [n("2.5", "2.5", 1, False, ">=")]),
    ("x \\geq 2.5", [n("2.5", "2.5", 1, False, ">=")]),
    ("x $\\gt$ 2.5", [n("2.5", "2.5", 1, False, ">")]),
    ("x $< 2.5$", [n("2.5", "2.5", 1, False, "<")]),
    ("in 2019 cohorts", []),
    ("in 1899 cohorts", [n("1899", "1899", 0)]),
    ("in 2100 cohorts", [n("2100", "2100", 0)]),
    ("in 2019% cases", [n("2019", "2019", 0, True)]),
    ("the 1st trial", []),
    ("the 2nd trial", []),
    ("Figure 3 shows", []),
    ("Fig. 3 shows", []),
    ("Figs. 3 and 4", [n("4", "4", 0)]),
    ("Table 2 gives 3.4", [n("3.4", "3.4", 1)]),
    ("Tab. 5 shows", []),
    ("Section 4.2 describes", []),
    ("Eq. 7 gives", []),
    ("Equation 7 gives", []),
    ("version 2 of", []),
    ("v2 of", []),
    ("H2O at 2 mM", [n("2", "2", 0)]),
    ("SF-36 score", []),
    ("1.2e-05 units", []),
    ("1.2e5 units", []),
    ("(n = 14)", [n("14", "14", 0)]),
    ("$x=3$ units", [n("3", "3", 0)]),
    ("\\cite{lee2010} and 3 more", [n("3", "3", 0)]),
    ("see \\ref{tab:2} and 5 cases", [n("5", "5", 0)]),
    ("a link \\url{https://x.org/10} and 6 cases", [n("6", "6", 0)]),
    ("\\href{https://x.org/7}{text 8}", [n("8", "8", 0)]),
    ("\\includegraphics[width=0.5\\textwidth]{fig1.pdf} 9 cells", [n("9", "9", 0)]),
    ("\\begin{equation} x = 12.5 \\end{equation} and 3 cases", [n("3", "3", 0)]),
    ("$$ 99.9 $$ then 4", [n("4", "4", 0)]),
    ("\\[ 77 \\] then 4", [n("4", "4", 0)]),
    ("Dr 1.5 and [data:tbl-7] 2.5", [n("1.5", "1.5", 1), n("2.5", "2.5", 1)]),
    ("1\u20132 range", [n("1", "1", 0), n("2", "2", 0)]),
    ("", []),
    ("99.9%", [n("99.9", "99.9", 1, True)]),
    ("0.5 and 0.50", [n("0.5", "0.5", 1), n("0.50", "0.50", 2)]),
    ("1 000 000 items", [n("1 000 000", "1000000", 0)]),
    ("1\u00a0234 units", [n("1\u00a0234", "1234", 0)]),
    ("gait 0.91 and gait 0.92", [n("0.91", "0.91", 2), n("0.92", "0.92", 2)]),
    ("12 cm", [n("12", "12", 0)]),
]


@pytest.mark.parametrize("text, expected", CASES, ids=[c[0][:40] or "empty" for c in CASES])
def test_number_rule(text, expected):
    assert found(text) == expected


def test_values_are_decimal_strings_not_floats():
    values = numbers.extract("0.1 and 0.50")
    assert [d["value"] for d in values] == ["0.1", "0.50"]
    assert all(isinstance(d["value"], str) for d in values)
    assert all(isinstance(d["decimals"], int) for d in values)
