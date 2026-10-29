"""Quote, number and name rules of the journal profile (M-F 5.5). Synthetic text only."""

import pytest

from scieflow.research.journal import quotes


def test_norm_collapses_whitespace_and_composes_nfc():
    assert quotes.norm("a\u00a0 b\n c\t") == "a b c"
    assert quotes.norm("e\u0301") == "\u00e9"


def test_long_substring_is_verbatim_evidence():
    text = "Research articles may contain up to 3000 words in the main text, excluding the abstract."
    assert quotes.quote_problem("Research articles may contain up to 3000 words in the main text", text) is None


def test_whitespace_differences_do_not_break_a_quote():
    text = "The abstract must not exceed\n250 words."
    assert quotes.quote_problem("The abstract must not exceed 250  words.", text) is None


def test_short_substring_that_is_not_a_whole_line_is_refused():
    text = "Research articles may contain up to 3000 words in the main text."
    problem = quotes.quote_problem("3000 words", text)
    assert problem is not None and "3000 words" in problem


def test_short_whole_line_is_accepted():
    text = "Keywords\nMaximum of six."
    assert quotes.quote_problem("Maximum of six.", text) is None


def test_quote_not_in_text_is_refused():
    assert quotes.quote_problem("Invented sentence that the page never says.", "Other text entirely here.") is not None


@pytest.mark.parametrize("quote, value", [
    ("up to 3000 words", 3000),
    ("up to 3,000 words", 3000),
    ("up to 3 000 words", 3000),
    ("The abstract must not exceed 250 words", 250),
    ("at most six references", 6),
    ("Maximum twenty pages", 20),
    ("zero figures", 0),
])
def test_number_present_in_quote(quote, value):
    assert quotes.number_in_quote(value, quote) is True


@pytest.mark.parametrize("quote, value", [
    ("up to 3000 words", 300),
    ("The abstract must not exceed 250 words", 3000),
    ("at most six references", 7),
    ("up to 3,000 words", 30),
])
def test_number_absent_from_quote(quote, value):
    assert quotes.number_in_quote(value, quote) is False


@pytest.mark.parametrize("name, word", [
    ("CONSORT 2010", "consort"),
    ("TRIPOD+AI", "tripod"),
    ("arrive 2.0", "arrive"),
    ("PRISMA 2020", "prisma"),
])
def test_guideline_name_first_word(name, word):
    assert quotes.name_first_word(name) == word


def test_guideline_name_is_matched_case_insensitively():
    assert quotes.name_in_quote("consort", "Manuscripts must follow the CONSORT 2010 guideline.")
    assert not quotes.name_in_quote("strobe", "Manuscripts must follow the CONSORT 2010 guideline.")


def test_latex_class_named_case_insensitively():
    assert quotes.class_in_quote("elsarticle", "Use the Elsevier elsarticle class for submission.")
    assert not quotes.class_in_quote("IEEEtran", "Use the Elsevier elsarticle class for submission.")
