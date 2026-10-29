"""Verbatim-quote, number and name rules of journal profiles (M-F plan 5.5, 5.17).

Pure functions, no I/O. A quote is evidence only when it is a substring of the stored page text
(whitespace collapsed, NFC) and is at least 20 characters or a whole line of that text. Numbers and
guideline or class names must appear in the quote that covers them.
"""

from __future__ import annotations

import re
import unicodedata

MIN_QUOTE = 20
EXCERPT = 60

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
# 3000, 3,000, "3 000" (space or no-break space as the group separator); never inside a longer number.
_DIGITS = re.compile(r"(?<!\d)(\d{1,3}(?:[, \u00a0]\d{3})+|\d+)(?!\d)")
# 3.5 and 1,000.5 are decimals: they give no integer (review r1, finding 7).
_DECIMAL = re.compile(r"\d+(?:[, \u00a0]\d{3})*\.\d+")
_SPACE = re.compile(r"[ \u00a0]")
_WORD = re.compile(r"[A-Za-z]+")


def norm(text) -> str:
    """NFC, every run of whitespace collapsed to one space, ends trimmed."""
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


def excerpt(text: str) -> str:
    """At most 60 characters of quoted text, for messages (M-F 5.15)."""
    return text if len(text) <= EXCERPT else text[: EXCERPT - 3] + "..."


def quote_problem(quote, source_text, min_chars: int = MIN_QUOTE) -> str | None:
    """Why `quote` is not verbatim evidence in `source_text` (None when it is).

    `min_chars` comes from schemas/journal-profile.yml (quote.min_chars) when the profile is checked.
    """
    q = norm(quote)
    if not q:
        return "the quote is empty"
    full = norm(source_text)
    lines = {norm(line) for line in str(source_text).splitlines() if norm(line)}
    if q not in full:
        return f"quote {excerpt(q)!r} is not verbatim in the stored source"
    if len(q) < min_chars and q not in lines:
        return (f"quote {excerpt(q)!r} is shorter than {min_chars} characters "
                "and is not a whole line of the stored source")
    return None


def number_tokens(quote) -> set:
    """The integer values written in a quote: digits with separators, the parts of a space-separated
    group (`5 100` gives 5100, 5 and 100) and words zero to twenty. A decimal gives no integer."""
    text = _DECIMAL.sub(" ", norm(quote))
    values = set()
    for match in _DIGITS.finditer(text):
        token = match.group(1)
        values.add(int(re.sub(r"[, \u00a0]", "", token)))
        if _SPACE.search(token):
            values.update(int(part) for part in _SPACE.split(token))
    for word in _WORD.findall(text):
        if word.lower() in _NUMBER_WORDS:
            values.add(_NUMBER_WORDS[word.lower()])
    return values


def number_in_quote(value: int, quote) -> bool:
    return int(value) in number_tokens(quote)


def name_first_word(name: str) -> str:
    """The first word of a guideline name, lower-case: `TRIPOD+AI` -> `tripod`."""
    return re.split(r"[\s+]+", str(name).strip())[0].lower()


def name_in_quote(word: str, quote) -> bool:
    """Whole-word, case-insensitive match of `word` in `quote`."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", norm(quote), re.IGNORECASE) is not None


def class_in_quote(name: str, quote) -> bool:
    """A LaTeX class name must be named in its quote (case-insensitive, whole word)."""
    return name_in_quote(name, quote)
