"""M-G t3: number matching table (M-G 5.5, 5.6 C002). Pure function tests, synthetic values only."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scieflow.research.manuscript.claims import compile as compile_mod

# (manuscript value, decimals, percent, relation, evidence value, expected)
CASES = [
    ("0.91", 2, False, "=", "0.9134", True),          # 0.9134 rounds to 0.91
    ("0.93", 2, False, "=", "0.9134", False),         # the planted wrong number
    ("0.91", 2, False, "=", "0.91", True),            # exact
    ("0.91", 2, False, "=", "0.9049", False),         # rounds to 0.90
    ("0.92", 2, False, "=", "0.915", True),           # half-up: 0.915 -> 0.92
    ("0.92", 2, False, "=", "0.925", True),           # half-even: 0.925 -> 0.92 (either rule)
    ("0.93", 2, False, "=", "0.925", True),           # half-up: 0.925 -> 0.93
    ("0.915", 3, False, "=", "0.9145", True),         # half-up at three decimals
    ("3.14", 2, False, "=", "3.1416", True),
    ("3.14", 2, False, "=", "3.1499", False),
    ("42", 0, False, "=", "42", True),                # integers exact
    ("42", 0, False, "=", "42.0", True),              # integral evidence equal to the integer
    ("42", 0, False, "=", "42.4", False),             # a non-integral evidence value never matches d = 0
    ("42", 0, False, "=", "41.6", False),
    ("1234", 0, False, "=", "1234", True),            # thousands separators are removed before matching
    ("1234", 0, False, "=", "1233.5", False),
    ("5", 0, True, "=", "0.05", True),                # percent: 0.05 x 100 = 5
    ("5.5", 1, True, "=", "0.055", True),
    ("5", 0, True, "=", "0.0512", False),             # 5.12 is not integral
    ("0.5", 1, False, "=", "50", False),              # no percent: 50 is not 0.5
    ("-0.5", 1, False, "=", "-0.46", True),           # negatives
    ("-0.5", 1, False, "=", "0.5", False),
    ("-1", 0, False, "<", "-2", True),
    ("-1", 0, False, "<", "0", False),
    ("0.001", 3, False, "<", "0.0004", True),         # p < 0.001 against 0.0004 (match)
    ("0.001", 3, False, "<", "0.002", False),         # ... and against 0.002 (no match)
    ("0.05", 2, False, "<=", "0.05", True),
    ("0.05", 2, False, "<=", "0.06", False),
    ("0.8", 1, False, ">=", "0.8", True),
    ("0.8", 1, False, ">=", "0.79", False),
    ("10", 0, False, ">", "11", True),
    ("10", 0, False, ">", "10", False),
    ("0.000012", 6, False, "=", "1.2e-05", True),     # scientific notation in evidence
    ("0.00001", 5, False, "=", "1.2e-05", True),
    ("2.5", 1, True, ">", "0.03", True),              # percent with a relation: 3 > 2.5
    ("2.5", 1, False, ">", "0.03", False),
]


@pytest.mark.parametrize("t, d, percent, relation, a, expected", CASES)
def test_value_matches(t, d, percent, relation, a, expected):
    got = compile_mod.value_matches(Decimal(t), d, percent, relation, Decimal(a))
    assert got is expected, (t, d, percent, relation, a)


def test_the_case_table_has_at_least_thirty_cases():
    assert len(CASES) >= 30


def test_evidence_values_read_signs_and_scientific_notation_without_a_word_sign():
    text = "run sigma-1.5, offset -0.25, rate 1.2e-05, count 7\n"
    values = compile_mod.evidence_values(text)
    assert Decimal("1.5") in values, "a hyphen glued to a word is a separator, not a sign"
    assert Decimal("-0.25") in values
    assert Decimal("0.000012") in values
    assert Decimal("7") in values


def test_evidence_values_of_plain_text_hold_no_numbers():
    assert compile_mod.evidence_values("no numbers here\n") == []
