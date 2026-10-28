"""Query-repair helpers: a verbatim excerpt of researcher v1.0.0 connectors/base.py,
lines 86-203 (MIT; see VENDORED.md). Nothing else from base.py is vendored."""

import re


#: Every character Lucene's query-string grammar reserves AND accepts a backslash escape
#: for. ``&`` and ``|`` are listed singly, which covers the two-character ``&&`` and ``||``
#: operators as a side effect.
#:
#: ``/`` is deliberately NOT here, though Lucene does reserve it (as the regex delimiter in
#: ``/pattern/``). Elasticsearch's query_string lexer REJECTS the escaped form: sending
#: ``CRISPR\/Cas9`` to DataCite is HTTP 400 (``token_mgr_error``), while the raw
#: ``CRISPR/Cas9`` is a perfectly good 200. Escaping it would therefore break the ordinary
#: titles ("CRISPR/Cas9", "AND/OR gates", "km/h") that it was meant to protect. The regex
#: hazard a bare ``/`` still carries is dealt with where it belongs, in the DataCite
#: connector, by replacing the slash with a space rather than by escaping it.
LUCENE_SPECIALS: str = '+-=&|><!(){}[]^"~*?:\\'

#: Bracket pairs balanced by :func:`sanitize_query`. Angle brackets are deliberately absent:
#: "<10 mg" is ordinary title text, not a grouping construct, in every grammar we speak.
_BRACKETS: dict[str, str] = {"(": ")", "[": "]", "{": "}"}
_CLOSERS: dict[str, str] = {close: open_ for open_, close in _BRACKETS.items()}

#: Uppercase-only, and only as a whole word. "Android" and "Nothing" keep their letters;
#: a lowercase "and" is already a plain term to every parser and is left alone.
_BOOLEAN_RE = re.compile(r"\b(?:AND|OR|NOT)\b")


def sanitize_query(query: str) -> str:
    """Make a free-text query safe for a query parser without changing what it asks for.

    The input is a title lifted out of a ``.bib`` file, so it can be truncated, quoted,
    escaped, or otherwise mangled by whatever citation manager wrote it. This repairs the
    four things that are lexical errors rather than words:

    * **Backslashes** are dropped. A backslash is the escape character in every grammar
      here, so a trailing one ("Attention Is All You Need \\") is an unterminated escape.
      No title means anything by a literal backslash.
    * **Unbalanced brackets** are dropped, and only the unbalanced ones. A truncated title
      ending "(The LOOP Stu" loses its orphan "(" and keeps every other character; a title
      that legitimately contains "(2nd edition)" keeps both parentheses.
    * **An unpaired double quote** is dropped. Quotes come in pairs, so an odd count means
      exactly one is stray, and it is the last one that has no partner.
    * **Bare boolean operators** are lowercased. Uppercase ``AND`` / ``OR`` / ``NOT`` is
      what makes them operators in Lucene, in PubMed's Entrez grammar, and in the arXiv
      API; a dangling one ("Attention Is All You Need AND") is a parse error. Lowercasing
      neutralizes the operator while keeping the token, which is the point: every index
      lowercases at analysis time anyway, so "NOT gates in DNA computing" still searches
      for the same words it always did.

    What this deliberately does NOT do is just as load-bearing. A colon, a slash, a hyphen,
    an apostrophe, a balanced quote, or a balanced bracket is left exactly as written,
    because those are ordinary punctuation in ordinary titles and this function runs on
    every query, not just the broken ones. A clean title comes out byte-for-byte identical.
    Where such a character really is reserved for one source and not the others (a colon is
    a field separator to Lucene, which is why DataCite quietly returns zero hits for
    "Attention: Is All You Need"), that source escapes it itself with :func:`escape_lucene`.

    Returns the cleaned query, which may be the empty string when the input was nothing but
    junk. An empty query is a clean negative: callers return ``[]`` and send no request.
    """
    text = str(query or "")
    # C0/C1 control characters, including the newline a wrapped .bib field can smuggle in.
    text = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in text)
    text = text.replace("\\", " ")
    text = _drop_unbalanced_brackets(text)
    text = _drop_unpaired_quote(text)
    text = _BOOLEAN_RE.sub(lambda match: match.group(0).lower(), text)
    text = " ".join(text.split())
    if not any(char.isalnum() for char in text):
        # Punctuation with no word in it ("()", '""', "-") is not a search: there is
        # nothing in it to match on. Balancing alone would let it through, since "()" is
        # perfectly balanced, and it would go out as a real request that can only earn a
        # 400 or a meaningless zero. It asks nothing, so it gets the empty query, and the
        # caller turns that into a clean negative without troubling the source at all.
        return ""
    return text


def escape_lucene(text: str) -> str:
    """Backslash-escape every reserved character in :data:`LUCENE_SPECIALS`.

    Opt-in, per source, and applied on top of :func:`sanitize_query` rather than instead of
    it: only the sources whose ``query`` parameter really is a Lucene expression (DataCite)
    want this. It is the difference between DataCite answering HTTP 400 for a title with a
    stray brace, or zero hits for a title with a colon in it, and DataCite answering the
    question that was asked.

    Note what is NOT escaped, and why it is not a shortcut: ``/`` is reserved by Lucene but
    its escaped form is rejected outright by Elasticsearch. See :data:`LUCENE_SPECIALS`.

    It must never be applied to a query the caller composed, only to the user's free text.
    A source that wraps the text in its own operators (``(<text>) AND publicationYear:[...]``)
    escapes the text first and adds the operators after, or it would escape its own syntax.
    """
    return "".join(f"\\{ch}" if ch in LUCENE_SPECIALS else ch for ch in text)


def _drop_unbalanced_brackets(text: str) -> str:
    """Remove only the brackets with no partner. Balanced ones survive untouched."""
    stack: list[int] = []
    orphans: set[int] = set()
    for index, char in enumerate(text):
        if char in _BRACKETS:
            stack.append(index)
        elif char in _CLOSERS and not (stack and text[stack[-1]] == _CLOSERS[char]):
            # A closer with no opener of its kind waiting for it, e.g. the "]" in "(a]".
            orphans.add(index)
        elif char in _CLOSERS:
            stack.pop()
    orphans.update(stack)  # Openers still waiting when the string ran out.
    if not orphans:
        return text
    return "".join(char for index, char in enumerate(text) if index not in orphans)


def _drop_unpaired_quote(text: str) -> str:
    """An odd number of double quotes means the last one is stray. Drop that one only."""
    positions = [index for index, char in enumerate(text) if char == '"']
    if len(positions) % 2 == 0:
        return text
    stray = positions[-1]
    return text[:stray] + text[stray + 1 :]
