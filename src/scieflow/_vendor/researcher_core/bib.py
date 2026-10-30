"""BibTeX parsing and emission (D20).

CSL-JSON is the canonical record format (D4), so this module is a *boundary*: it reads
`.bib` files the world hands us into :class:`CSLRecord`, and it writes :class:`CSLRecord`
back out as `.bib`. BibTeX is never the model.

The parser
----------
A brace-aware tokenizer, never a regex. The regex logic that shipped in the v1
``scripts/bib-validator.py`` parsed ZERO of the citation-audit example's compact entries
(the ones whose closing brace sits on the last field line, ``... doi={10.x/y}}``), which is
why D20 exists. The M1 ``scripts/bib-validator.py`` rewrite is the reference this module
ports from; its behavior and its test cases are preserved here and then extended.

Constructs handled:

* nested braces in values, to arbitrary depth (``{A {Nested {Deep}} Title}``)
* quoted values, in which braces may nest and the closing quote counts only at brace
  depth 0 (``"Commas, {Braces}, and Quotes"``)
* bare tokens: numbers (``year = 2021``) and macro names (``journal = jbhi``)
* concatenation with ``#`` (``"Part one " # "and part two"``)
* compact entries terminated by ``}}`` on the last field line
* a trailing comma before the closing brace
* ``@comment``, ``@preamble``, and ``@string`` blocks
* parenthesis-delimited entries (``@article(key, ...)``), which BibTeX also accepts
* ``%`` line comments outside (and between) entries
* case-insensitive entry types and macro names

The @string decision (deliberate divergence from M1)
----------------------------------------------------
The M1 script SKIPS ``@string`` blocks; it never expands them, so ``journal = jbhi`` parses
to the literal string ``"jbhi"``. That is acceptable for a duplicate-key checker. It is NOT
acceptable here, because core feeds verification: a title or author defined through a macro
would be compared as the macro NAME against real index metadata and produce a spurious
``mismatch`` on axis (a).

**Core therefore EXPANDS ``@string`` macros** (``expand_strings=True``, the default). The
definitions are retained on :attr:`BibDatabase.strings` so emission can round-trip them, and
BibTeX's built-in month macros (:data:`BUILTIN_STRINGS`) are expanded too. A bare token that
resolves to no known macro stays as its literal text (the M1 behavior) and is reported on
:attr:`BibDatabase.unresolved_macros` rather than silently vanishing. Pass
``expand_strings=False`` for the literal M1 reading.

The emitter
-----------
:func:`emit_bib` writes a :class:`BibDatabase`, a list of :class:`BibEntry`, or a list of
:class:`CSLRecord`. Emission is deterministic: a fixed field order, braces for every value,
no line wrapping. **Parse -> emit -> parse is a fixed point**: the entries, macros,
preambles, and comments that come back are equal to the ones that went in.

Lossless BibTeX round-trip through CSL
--------------------------------------
Standard CSL fields carry *cleaned* values: LaTeX accent commands are decoded to Unicode
(``Ant{\\^o}nio`` -> ``Antônio``) and brace groups are removed, because that is what a title
or author has to look like to be compared against OpenAlex or Crossref metadata. Cleaning is
lossy in the BibTeX direction (brace capitalization protection cannot survive it), so
:func:`entry_to_record` also stashes the raw entry under ``custom.bibtex`` (type, key, and
raw fields). :func:`record_to_entry` restores from it when present, so
``bib -> CSL -> bib`` is lossless, and synthesizes an entry from the CSL fields when it is
absent (which is the case for records that came from a connector).

Two BibTeX fields (``issn`` and ``keywords``) are carried ONLY under ``custom.bibtex``, never
in the standard CSL variables; see the comment on :data:`_MAPPED_FIELDS` for why the schema
forces that.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import (
    CSLDate,
    CSLName,
    CSLRecord,
    normalize_doi,
    parse_name,
)

__all__ = [
    "BIBTEX_TO_CSL_TYPE",
    "BUILTIN_STRINGS",
    "CSL_TO_BIBTEX_TYPE",
    "FIELD_ORDER",
    "SKIPPED_BLOCK_TYPES",
    "BibDatabase",
    "BibEntry",
    "BibError",
    "BibParseError",
    "bibtex_name_to_csl",
    "citation_key_for",
    "emit_bib",
    "emit_entry",
    "entry_to_record",
    "latex_to_text",
    "parse_bib",
    "parse_bib_file",
    "record_to_entry",
    "records_from_bib",
    "split_bibtex_names",
]


class BibError(Exception):
    """Base class for BibTeX errors raised by this module."""


class BibParseError(BibError):
    """A malformed construct was found while parsing in ``strict=True`` mode."""


#: Block types that are not bibliographic entries. They are captured, never returned as
#: entries.
SKIPPED_BLOCK_TYPES = frozenset({"comment", "preamble", "string"})

#: BibTeX's built-in month macros. Expanded like any other macro, but overridable by an
#: explicit ``@string`` definition in the file.
BUILTIN_STRINGS: dict[str, str] = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}

_MONTH_NUMBERS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

#: Field emission order. Fields not listed follow, sorted alphabetically, so emission is
#: deterministic for any input.
FIELD_ORDER: tuple[str, ...] = (
    "author",
    "editor",
    "title",
    "booktitle",
    "journal",
    "series",
    "school",
    "institution",
    "organization",
    "publisher",
    "address",
    "edition",
    "chapter",
    "volume",
    "number",
    "issue",
    "pages",
    "year",
    "month",
    "doi",
    "url",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "issn",
    "isbn",
    "language",
    "keywords",
    "abstract",
    "howpublished",
    "note",
)

#: BibTeX entry type -> CSL item type.
BIBTEX_TO_CSL_TYPE: dict[str, str] = {
    "article": "article-journal",
    "book": "book",
    "booklet": "pamphlet",
    "conference": "paper-conference",
    "dataset": "dataset",
    "electronic": "webpage",
    "inbook": "chapter",
    "incollection": "chapter",
    "inproceedings": "paper-conference",
    "manual": "report",
    "mastersthesis": "thesis",
    "misc": "document",
    "online": "webpage",
    "patent": "patent",
    "phdthesis": "thesis",
    "preprint": "article",
    "proceedings": "book",
    "software": "software",
    "standard": "standard",
    "techreport": "report",
    "thesis": "thesis",
    "unpublished": "manuscript",
    "www": "webpage",
}

#: CSL item type -> BibTeX entry type. The inverse of :data:`BIBTEX_TO_CSL_TYPE` where that
#: map is many-to-one (thesis, chapter, webpage), with the conventional choice picked.
CSL_TO_BIBTEX_TYPE: dict[str, str] = {
    "article": "article",
    "article-journal": "article",
    "article-magazine": "article",
    "article-newspaper": "article",
    "book": "book",
    "chapter": "incollection",
    "dataset": "misc",
    "document": "misc",
    "manuscript": "unpublished",
    "pamphlet": "booklet",
    "paper-conference": "inproceedings",
    "patent": "misc",
    "post": "misc",
    "post-weblog": "misc",
    "report": "techreport",
    "review": "article",
    "software": "misc",
    "speech": "misc",
    "standard": "misc",
    "thesis": "phdthesis",
    "webpage": "misc",
}

#: A BibTeX citation key that needs no quoting or rewriting.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:.+/-]*$")

_WHITESPACE_RE = re.compile(r"\s+")

_CUSTOM_BIBTEX_KEY = "bibtex"


# ---------------------------------------------------------------------------
# LaTeX to text
# ---------------------------------------------------------------------------

# Accent commands mapped to their combining codepoint. Applying the combining mark and then
# NFC-normalizing is how \^o becomes a single "ô" rather than "o" plus a stray mark.
_COMBINING = {
    "`": "̀",
    "'": "́",
    "^": "̂",
    '"': "̈",
    "~": "̃",
    "=": "̄",
    ".": "̇",
    "u": "̆",
    "v": "̌",
    "H": "̋",
    "r": "̊",
    "c": "̧",
    "k": "̨",
    "b": "̱",
    "d": "̣",
}

# Short accents:  \'e   \'{e}   {\'e}   \^o   \'{\i}
_ACCENT_SHORT_RE = re.compile(
    r"\\(?P<cmd>[`'^\"~=.])\s*(?:\{\s*(?P<b1>\\i|\\j|[A-Za-z])\s*\}|(?P<b2>\\i|\\j|[A-Za-z]))"
)
# Named accents, in both the braced and the space-separated form:  \c{c}   \H o   \v{s}
_ACCENT_LONG_RE = re.compile(
    r"\\(?P<cmd>[uvHrckbd])\s*"
    r"(?:\{\s*(?P<b1>\\i|\\j|[A-Za-z])\s*\}|(?P<b2>\\i|\\j|[A-Za-z])(?![A-Za-z]))"
)

# Control words that stand for a character. Longest alternatives first so that \aa is not
# matched as \a, and the negative lookahead stops \o from eating the "o" of \oe.
_CONTROL_WORDS: dict[str, str] = {
    "textbackslash": "\\",
    "textendash": "-",
    "textemdash": "-",
    "textquoteleft": "‘",
    "textquoteright": "’",
    "textquotedblleft": "“",
    "textquotedblright": "”",
    "ss": "ß",
    "AA": "Å",
    "aa": "å",
    "AE": "Æ",
    "ae": "æ",
    "OE": "Œ",
    "oe": "œ",
    "DH": "Ð",
    "dh": "ð",
    "TH": "Þ",
    "th": "þ",
    "DJ": "Đ",
    "dj": "đ",
    "NG": "Ŋ",
    "ng": "ŋ",
    "O": "Ø",
    "o": "ø",
    "L": "Ł",
    "l": "ł",
    "i": "i",
    "j": "j",
}

_CONTROL_WORD_RE = re.compile(
    r"\\(" + "|".join(sorted(_CONTROL_WORDS, key=len, reverse=True)) + r")(\{\})?(?![A-Za-z])"
)

_ESCAPED_PUNCT_RE = re.compile(r"\\([&%$#_{}])")

_LATEX_MATH_RE = re.compile(r"\$([^$]*)\$")

# Placeholders for an ESCAPED brace, which is a literal character in the text and must
# survive the removal of the grouping braces around it.
_ESCAPED_OPEN = "\x01"
_ESCAPED_CLOSE = "\x02"


def _accent_repl(match: re.Match[str]) -> str:
    command = match.group("cmd")
    base = match.group("b1") or match.group("b2") or ""
    if base == "\\i":
        base = "i"
    elif base == "\\j":
        base = "j"
    return unicodedata.normalize("NFC", base + _COMBINING[command])


def latex_to_text(value: str) -> str:
    """Decode the LaTeX a `.bib` file carries into plain Unicode text.

    Accent commands become precomposed characters (``Ant{\\^o}nio`` -> ``Antônio``,
    ``Erd{\\H o}s`` -> ``Erdős``), control words become their character (``\\ss`` -> ``ß``),
    escaped punctuation loses its backslash (``\\&`` -> ``&``), trivial math mode is
    unwrapped, and the remaining brace groups (which in BibTeX protect capitalization and
    carry no textual meaning) are removed.

    This is the value that goes into a CSL field, because it is the value that can be
    compared against index metadata. It is intentionally NOT reversible; the raw BibTeX is
    preserved separately under ``custom.bibtex`` (see :func:`entry_to_record`).
    """
    if not value:
        return ""
    text = value
    for _ in range(3):  # nested accents, for example \'{\v{s}}
        new = _ACCENT_LONG_RE.sub(_accent_repl, text)
        new = _ACCENT_SHORT_RE.sub(_accent_repl, new)
        if new == text:
            break
        text = new
    text = _CONTROL_WORD_RE.sub(lambda m: _CONTROL_WORDS[m.group(1)], text)
    text = _LATEX_MATH_RE.sub(lambda m: m.group(1), text)
    text = _ESCAPED_PUNCT_RE.sub(_escaped_punct_repl, text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace(_ESCAPED_OPEN, "{").replace(_ESCAPED_CLOSE, "}")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return unicodedata.normalize("NFC", text)


def _escaped_punct_repl(match: re.Match[str]) -> str:
    char = match.group(1)
    if char == "{":
        return _ESCAPED_OPEN
    if char == "}":
        return _ESCAPED_CLOSE
    return char


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class _Cursor:
    """A position in the source text. Tracks the line number for diagnostics."""

    __slots__ = ("text", "pos", "line")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line = 1

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def advance(self) -> str:
        char = self.text[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
        return char

    def skip_ws(self) -> None:
        while not self.eof() and self.text[self.pos].isspace():
            self.advance()

    def skip_line(self) -> None:
        while not self.eof() and self.peek() != "\n":
            self.advance()

    def skip_ws_and_comments(self) -> None:
        """Skip whitespace and ``%`` line comments (BibTeX ignores both between fields)."""
        while True:
            self.skip_ws()
            if self.peek() == "%":
                self.skip_line()
                continue
            return


_CLOSING = {"{": "}", "(": ")"}


def _read_balanced(cur: _Cursor, opener: str) -> tuple[str, bool]:
    """Read a delimited block. ``cur`` sits ON the opener.

    Returns ``(inner_text, terminated)``; ``cur`` ends past the closing delimiter. Nesting
    is counted on the same delimiter pair, so ``{a {b} c}`` yields ``a {b} c``.
    """
    closer = _CLOSING[opener]
    cur.advance()
    depth = 1
    out: list[str] = []
    while not cur.eof():
        char = cur.advance()
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return "".join(out), True
        out.append(char)
    return "".join(out), False  # unterminated: hand back what we have


def _read_quoted(cur: _Cursor) -> tuple[str, bool]:
    """Read a ``"``-quoted value. Braces may nest inside; the closing quote counts only at
    brace depth 0, so ``"Commas, {Braces}, and Quotes"`` survives whole."""
    cur.advance()
    depth = 0
    out: list[str] = []
    while not cur.eof():
        char = cur.advance()
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == '"' and depth == 0:
            return "".join(out), True
        out.append(char)
    return "".join(out), False


def _read_bare(cur: _Cursor, closer: str) -> str:
    """A bare value (a number or a macro name), up to a top-level separator."""
    stops = {",", "#", closer, '"', "}"}
    out: list[str] = []
    while not cur.eof() and cur.peek() not in stops and not cur.peek().isspace():
        out.append(cur.advance())
    return "".join(out)


def _read_value_parts(cur: _Cursor, closer: str) -> tuple[list[tuple[str, str]], bool]:
    """Read one field value as a list of ``(kind, text)`` parts, honoring ``#``.

    ``kind`` is ``"braced"``, ``"quoted"``, or ``"bare"``. Keeping the kind is what makes
    macro expansion correct: only a BARE part can name a macro. ``{jbhi}`` is the literal
    string "jbhi", and it must never be expanded.
    """
    parts: list[tuple[str, str]] = []
    terminated = True
    while True:
        cur.skip_ws_and_comments()
        char = cur.peek()
        if char == "{":
            text, ok = _read_balanced(cur, "{")
            parts.append(("braced", text))
            terminated = terminated and ok
        elif char == '"':
            text, ok = _read_quoted(cur)
            parts.append(("quoted", text))
            terminated = terminated and ok
        else:
            parts.append(("bare", _read_bare(cur, closer)))
        cur.skip_ws_and_comments()
        if cur.peek() == "#":
            cur.advance()
            continue
        return parts, terminated


def _is_number(text: str) -> bool:
    return bool(text) and text.isdigit()


# ---------------------------------------------------------------------------
# The parsed shapes
# ---------------------------------------------------------------------------


@dataclass
class BibEntry:
    """One BibTeX entry: its type, its citation key, and its fields, in file order.

    Field names are lowercased (BibTeX field names are case-insensitive); values are the
    raw BibTeX text with whitespace runs collapsed, brace groups and LaTeX intact. Use
    :meth:`to_record` for the cleaned, comparable form.
    """

    entry_type: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)
    #: 1-based line of the ``@`` that opened the entry. Diagnostics only, so it does not
    #: participate in equality: a round-tripped entry is the same entry at a new line.
    line: int = field(default=0, compare=False, repr=False)

    def get(self, name: str, default: str = "") -> str:
        return self.fields.get(name.lower(), default)

    @property
    def doi(self) -> str:
        """The DOI, normalized to its bare lowercase form ("" when there is none)."""
        return normalize_doi(latex_to_text(self.get("doi")))

    @property
    def year(self) -> int | None:
        date = CSLDate.parse(self.get("year"))
        return date.year if date else None

    @property
    def authors(self) -> list[CSLName]:
        return [bibtex_name_to_csl(name) for name in split_bibtex_names(self.get("author"))]

    def to_record(self, *, keep_source: bool = True) -> CSLRecord:
        return entry_to_record(self, keep_source=keep_source)

    def to_bibtex(self) -> str:
        return emit_entry(self)

    @classmethod
    def from_record(cls, record: CSLRecord, *, key: str | None = None) -> BibEntry:
        return record_to_entry(record, key=key)


@dataclass
class BibDatabase:
    """Everything a `.bib` file contains: entries plus the blocks that are not entries.

    ``strings``, ``preambles``, and ``comments`` are retained (not discarded, as the M1
    script does) so that emission round-trips the file rather than silently dropping parts
    of it.
    """

    entries: list[BibEntry] = field(default_factory=list)
    #: ``@string`` macros, names lowercased (BibTeX macro names are case-insensitive),
    #: values already expanded against the macros defined before them.
    strings: dict[str, str] = field(default_factory=dict)
    preambles: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    #: Bare tokens that named no known macro and were kept as literal text. Diagnostics
    #: about the SOURCE text, so they do not participate in equality.
    unresolved_macros: list[str] = field(default_factory=list, compare=False)
    #: Malformed constructs found while parsing. Diagnostics; see ``strict=True``.
    problems: list[str] = field(default_factory=list, compare=False)

    def __iter__(self) -> Iterator[BibEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def by_key(self, key: str) -> BibEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def keys(self) -> list[str]:
        return [entry.key for entry in self.entries]

    def records(self, *, keep_source: bool = True) -> list[CSLRecord]:
        """Every entry as a :class:`CSLRecord`. This is what verification consumes."""
        return [entry_to_record(entry, keep_source=keep_source) for entry in self.entries]

    def to_bibtex(self, **kwargs: Any) -> str:
        return emit_bib(self, **kwargs)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_bib(
    source: str | Path,
    *,
    expand_strings: bool = True,
    strict: bool = False,
) -> BibDatabase:
    """Parse BibTeX text (or a `.bib` file) into a :class:`BibDatabase`.

    ``source`` is BibTeX text, a :class:`~pathlib.Path` to a file, or (for compatibility
    with the M1 script's signature) a string that names an existing file.

    ``expand_strings`` expands ``@string`` and built-in month macros in bare values. This is
    the default and the documented divergence from the M1 script; see the module docstring.
    Set it to False to get the literal reading, in which ``journal = jbhi`` stays ``"jbhi"``.

    ``strict`` raises :class:`BibParseError` on any malformed construct instead of recording
    it on :attr:`BibDatabase.problems` and carrying on. The default is tolerant, because a
    single bad entry in a 300-entry library must not cost the other 299.
    """
    text = _read_source(source)
    db = BibDatabase()
    cur = _Cursor(text)

    while not cur.eof():
        cur.skip_ws()
        char = cur.peek()
        if char == "%":  # a line comment between entries
            cur.skip_line()
            continue
        if char != "@":
            if char:
                cur.advance()  # ignorable text outside entries
            continue
        cur.advance()  # consume '@'
        _parse_block(cur, db, expand_strings=expand_strings)

    if strict and db.problems:
        raise BibParseError("; ".join(db.problems))
    return db


def parse_bib_file(path: str | Path, **kwargs: Any) -> BibDatabase:
    """Parse a `.bib` file. Undecodable bytes are replaced, never fatal."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_bib(content, **kwargs)


def records_from_bib(source: str | Path, **kwargs: Any) -> list[CSLRecord]:
    """Parse and hand back :class:`CSLRecord` objects. The verification entry point."""
    return parse_bib(source, **kwargs).records()


def _read_source(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8", errors="replace")
    text = str(source)
    if "\n" not in text and "@" not in text and len(text) < 4096:
        candidate = Path(text)
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:  # a string that is not a usable path is just text
            pass
    return text


def _parse_block(cur: _Cursor, db: BibDatabase, *, expand_strings: bool) -> None:
    """Parse one ``@...`` block. ``cur`` sits just past the ``@``."""
    line = cur.line
    start = cur.pos
    while not cur.eof() and (cur.peek().isalnum() or cur.peek() == "_"):
        cur.advance()
    entry_type = cur.text[start : cur.pos].lower()
    cur.skip_ws_and_comments()

    opener = cur.peek()
    if opener not in _CLOSING or not entry_type:
        db.problems.append(f"line {line}: '@{entry_type}' is not followed by an entry body")
        return

    if entry_type in SKIPPED_BLOCK_TYPES:
        _parse_skipped_block(cur, db, entry_type, opener, line, expand_strings=expand_strings)
        return

    closer = _CLOSING[opener]
    cur.advance()  # consume the opener

    # Citation key: everything up to the first top-level ',' (or the closer, for a keyless
    # entry).
    key_chars: list[str] = []
    while not cur.eof() and cur.peek() not in (",", closer):
        key_chars.append(cur.advance())
    key = "".join(key_chars).strip()

    entry = BibEntry(entry_type=entry_type, key=key, line=line)
    if cur.peek() == ",":
        cur.advance()

    _parse_fields(cur, db, entry, closer, expand_strings=expand_strings)

    if key or entry.fields:
        db.entries.append(entry)
    else:
        db.problems.append(f"line {line}: '@{entry_type}' block has neither a key nor fields")


def _parse_skipped_block(
    cur: _Cursor,
    db: BibDatabase,
    entry_type: str,
    opener: str,
    line: int,
    *,
    expand_strings: bool,
) -> None:
    """``@comment`` / ``@preamble`` / ``@string``: captured, never returned as entries."""
    if entry_type == "string":
        closer = _CLOSING[opener]
        cur.advance()  # consume the opener
        cur.skip_ws_and_comments()
        name_start = cur.pos
        while not cur.eof() and (cur.peek().isalnum() or cur.peek() in "_-:+."):
            cur.advance()
        name = cur.text[name_start : cur.pos].strip().lower()
        cur.skip_ws_and_comments()
        if cur.peek() != "=" or not name:
            db.problems.append(f"line {line}: malformed @string block")
            # Consume to the closer so the scan cannot loop.
            while not cur.eof() and cur.advance() != closer:
                pass
            return
        cur.advance()  # '='
        parts, terminated = _read_value_parts(cur, closer)
        if not terminated:
            db.problems.append(f"line {line}: unterminated value in @string {name}")
        db.strings[name] = _resolve_parts(parts, db, expand_strings=expand_strings)
        cur.skip_ws_and_comments()
        if cur.peek() == closer:
            cur.advance()
        return

    raw, terminated = _read_balanced(cur, opener)
    if not terminated:
        db.problems.append(f"line {line}: unterminated @{entry_type} block")
    if entry_type == "preamble":
        db.preambles.append(raw.strip())
    else:
        db.comments.append(raw.strip())


def _parse_fields(
    cur: _Cursor,
    db: BibDatabase,
    entry: BibEntry,
    closer: str,
    *,
    expand_strings: bool,
) -> None:
    while True:
        cur.skip_ws_and_comments()
        if cur.eof():
            db.problems.append(f"line {entry.line}: unterminated entry '{entry.key}'")
            return
        if cur.peek() == closer:
            cur.advance()
            return
        if cur.peek() == ",":  # a trailing or repeated comma
            cur.advance()
            continue

        name_start = cur.pos
        while not cur.eof() and (cur.peek().isalnum() or cur.peek() in "_-:."):
            cur.advance()
        name = cur.text[name_start : cur.pos].strip().lower()
        cur.skip_ws_and_comments()

        if cur.peek() != "=" or not name:
            db.problems.append(
                f"line {cur.line}: entry '{entry.key}' has a field without a value, skipped"
            )
            # Skip to the next separator so a malformed field cannot loop forever.
            while not cur.eof() and cur.peek() not in (",", closer):
                cur.advance()
            if cur.peek() == ",":
                cur.advance()
            continue

        cur.advance()  # '='
        parts, terminated = _read_value_parts(cur, closer)
        if not terminated:
            db.problems.append(
                f"line {cur.line}: unterminated value for '{name}' in entry '{entry.key}'"
            )
        value = _resolve_parts(parts, db, expand_strings=expand_strings)
        if name:
            entry.fields[name] = value


def _resolve_parts(
    parts: Sequence[tuple[str, str]],
    db: BibDatabase,
    *,
    expand_strings: bool,
) -> str:
    """Concatenate value parts, expanding macros in the BARE parts only."""
    out: list[str] = []
    for kind, text in parts:
        if kind != "bare":
            out.append(text)
            continue
        if not text:
            continue
        if expand_strings:
            lowered = text.lower()
            if lowered in db.strings:
                out.append(db.strings[lowered])
                continue
            if lowered in BUILTIN_STRINGS:
                out.append(BUILTIN_STRINGS[lowered])
                continue
            if not _is_number(text) and text not in db.unresolved_macros:
                db.unresolved_macros.append(text)
        out.append(text)
    return _WHITESPACE_RE.sub(" ", "".join(out)).strip()


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def split_bibtex_names(author_field: str) -> list[str]:
    """Split a BibTeX name list on ``and`` at brace depth 0.

    Depth matters: ``{Barnes and Noble}, Inc.`` is ONE corporate name, and splitting it
    would invent an author. ``Doe, Jane and Roe, Richard`` is two.
    """
    if not author_field:
        return []
    names: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    text = author_field
    length = len(text)
    while index < length:
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if (
            depth == 0
            and char.lower() == "a"
            and text[index : index + 3].lower() == "and"
            and (index == 0 or text[index - 1].isspace())
            and (index + 3 >= length or text[index + 3].isspace())
        ):
            names.append("".join(current).strip())
            current = []
            index += 3
            continue
        current.append(char)
        index += 1
    names.append("".join(current).strip())
    return [name for name in names if name]


def _is_fully_braced(text: str) -> bool:
    """True when the whole string is one brace group: ``{World Health Organization}``."""
    if len(text) < 2 or not text.startswith("{") or not text.endswith("}"):
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def bibtex_name_to_csl(raw: str) -> CSLName:
    """One BibTeX name as a :class:`CSLName`.

    A fully braced name is a LITERAL name (an organization), not a person: BibTeX braces
    exist precisely to say "do not take this apart". Splitting ``{World Health Organization}``
    into given="World Health" / family="Organization" would invent a person, so it stays
    literal. Anything else goes through the ordinary family/given/suffix parse.
    """
    text = raw.strip()
    if _is_fully_braced(text):
        return CSLName(literal=latex_to_text(text))
    return parse_name(latex_to_text(text))


def _name_to_bibtex(name: CSLName) -> str:
    """A CSL name as BibTeX. Braced when it would otherwise be re-split or re-parsed wrong."""
    if name.literal and not name.family:
        # ALWAYS braced. The braces are what make it a literal name on re-reading: without
        # them, "World Health Organization" comes back as given="World Health" /
        # family="Organization", which is an author that does not exist.
        return "{" + name.literal + "}"
    family = " ".join(p for p in (name.non_dropping_particle, name.family) if p).strip()
    family = _protect_name(family)
    parts = [family]
    if name.suffix:
        parts.append(name.suffix)
    given = " ".join(p for p in (name.dropping_particle, name.given) if p).strip()
    if given:
        parts.append(given)
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts)


_NAME_AND_RE = re.compile(r"(^|\s)and(\s|$)", re.IGNORECASE)


def _protect_name(text: str) -> str:
    """Brace a name part that contains the ``and`` separator or a comma, so re-parsing it
    yields the same one name rather than two, or a mangled family/given split."""
    if not text:
        return ""
    if _NAME_AND_RE.search(text) or "," in text:
        return "{" + text + "}"
    return text


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def _balance_braces(value: str) -> str:
    """Drop unmatched braces from a value.

    BibTeX counts braces literally (``\\{`` is still a brace to its counter), so a value
    with unmatched braces cannot be represented at all: it would swallow the rest of the
    entry. Emission therefore drops the unmatched ones. Balanced input, which is everything
    the parser produces from well-formed source, is returned unchanged.
    """
    if "{" not in value and "}" not in value:
        return value
    keep = [True] * len(value)
    stack: list[int] = []
    for index, char in enumerate(value):
        if char == "{":
            stack.append(index)
        elif char == "}":
            if stack:
                stack.pop()
            else:
                keep[index] = False
    for index in stack:
        keep[index] = False
    return "".join(char for index, char in enumerate(value) if keep[index])


def _emit_value(value: str) -> str:
    return "{" + _balance_braces(_WHITESPACE_RE.sub(" ", value).strip()) + "}"


def _ordered_fields(fields: Mapping[str, str]) -> list[tuple[str, str]]:
    known = [(name, fields[name]) for name in FIELD_ORDER if name in fields]
    rest = sorted((name, value) for name, value in fields.items() if name not in FIELD_ORDER)
    return known + rest


def emit_entry(entry: BibEntry, *, indent: str = "  ") -> str:
    """One entry as BibTeX. Deterministic: fixed field order, braced values, no wrapping."""
    ordered = _ordered_fields(entry.fields)
    if not ordered:
        return f"@{entry.entry_type}{{{entry.key},\n}}"
    lines = [f"@{entry.entry_type}{{{entry.key},"]
    body = [f"{indent}{name} = {_emit_value(value)}" for name, value in ordered]
    lines.append(",\n".join(body))
    lines.append("}")
    return "\n".join(lines)


def emit_bib(
    source: BibDatabase | Iterable[BibEntry | CSLRecord],
    *,
    include_preamble: bool = True,
    include_strings: bool = True,
    include_comments: bool = True,
) -> str:
    """Emit BibTeX for a database, a list of entries, or a list of :class:`CSLRecord`.

    Deterministic and a fixed point under re-parsing: ``parse_bib(emit_bib(db))`` equals
    ``db`` (entries, macros, preambles, comments).

    The blocks are emitted in BibTeX's own dependency order: preambles, then ``@string``
    macros (a macro must be defined before the entry that uses it), then ``@comment``
    blocks, then the entries.
    """
    if isinstance(source, BibDatabase):
        db = source
        entries = db.entries
    else:
        db = BibDatabase()
        entries = [item if isinstance(item, BibEntry) else record_to_entry(item) for item in source]

    blocks: list[str] = []
    if include_preamble:
        blocks.extend(f"@preamble{{{text}}}" for text in db.preambles)
    if include_strings:
        blocks.extend(
            f"@string{{{name} = {_emit_value(value)}}}"
            for name, value in sorted(db.strings.items())
        )
    if include_comments:
        blocks.extend(f"@comment{{{text}}}" for text in db.comments)
    blocks.extend(emit_entry(entry) for entry in entries)
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# BibTeX <-> CSL
# ---------------------------------------------------------------------------

# BibTeX fields consumed into standard CSL fields. Everything else survives under
# custom.bibtex, so nothing is lost.
#
# 'issn' and 'keywords' are deliberately NOT in this set, and entry_to_record does not
# populate CSLRecord.ISSN or CSLRecord.keyword. record.schema.json (the contract) types both
# CSL variables as string-or-number, exactly as upstream csl-data.json does, while model.py
# types them as Python lists and serializes them as JSON arrays. A record carrying either one
# from a `.bib` file would therefore fail schema validation. Rather than emit records the
# contract rejects, bib.py leaves both fields alone: the values survive verbatim under
# custom.bibtex (and under bibtex_extra_fields when keep_source is off), and record_to_entry
# still writes them back out if a connector-built record happens to carry them.
_CONTAINER_FIELDS = ("journal", "journaltitle", "booktitle", "series")
_MAPPED_FIELDS = frozenset(
    {
        "abstract",
        "author",
        "booktitle",
        "doi",
        "editor",
        "isbn",
        "issue",
        "journal",
        "journaltitle",
        "language",
        "month",
        "note",
        "number",
        "pages",
        "publisher",
        "school",
        "institution",
        "title",
        "url",
        "volume",
        "year",
    }
)

_IDENTIFIER_FIELDS = frozenset({"eprint", "archiveprefix", "primaryclass"})

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(v\d+)?|[a-z-]+(\.[A-Z]{2})?/\d{7})", re.IGNORECASE)

_MONTH_NAMES_BY_NUMBER = {number: BUILTIN_STRINGS[name] for name, number in _MONTH_NUMBERS.items()}


def _issued(entry: BibEntry) -> CSLDate | None:
    year_text = latex_to_text(entry.get("year"))
    date = CSLDate.parse(year_text) if year_text else None
    if date is None or date.year is None:
        return date
    month_text = latex_to_text(entry.get("month")).strip().lower()
    if month_text:
        month = _MONTH_NUMBERS.get(month_text[:3])
        if month is None and month_text.isdigit():
            value = int(month_text)
            month = value if 1 <= value <= 12 else None
        if month is not None:
            date.month = month
    return date


def _number_field(entry: BibEntry) -> tuple[str, str]:
    """BibTeX ``number`` is the journal issue for articles and a report number otherwise."""
    number = latex_to_text(entry.get("number"))
    issue = latex_to_text(entry.get("issue"))
    if entry.entry_type in {"techreport", "manual", "patent", "standard", "misc"}:
        return issue, number
    return issue or number, ""


def _arxiv_id(entry: BibEntry) -> str:
    eprint = latex_to_text(entry.get("eprint"))
    archive = latex_to_text(entry.get("archiveprefix")).lower()
    if eprint and (archive == "arxiv" or _ARXIV_ID_RE.fullmatch(eprint)):
        return eprint
    url = entry.get("url")
    if "arxiv.org/abs/" in url:
        return url.rsplit("/", 1)[-1]
    return ""


def entry_to_record(entry: BibEntry, *, keep_source: bool = True) -> CSLRecord:
    """Project a :class:`BibEntry` onto the canonical :class:`CSLRecord`.

    Standard CSL fields carry LaTeX-decoded, brace-stripped values, because that is the form
    that can be compared against index metadata on axis (a). The raw entry is stashed under
    ``custom.bibtex`` when ``keep_source`` is set, so :func:`record_to_entry` can restore it
    exactly.
    """
    issue, number = _number_field(entry)
    pages = latex_to_text(entry.get("pages")).replace("--", "-")
    container = ""
    for name in _CONTAINER_FIELDS:
        if entry.get(name):
            container = latex_to_text(entry.get(name))
            break
    publisher = (
        latex_to_text(entry.get("publisher"))
        or latex_to_text(entry.get("school"))
        or latex_to_text(entry.get("institution"))
    )

    extra: dict[str, Any] = {}
    if keep_source:
        # The whole entry, verbatim: record_to_entry restores from this, so bib -> CSL -> bib
        # is lossless. No need for the per-field leftovers below when it is present.
        extra[_CUSTOM_BIBTEX_KEY] = {
            "type": entry.entry_type,
            "key": entry.key,
            "fields": dict(entry.fields),
        }
    else:
        leftovers = {
            name: value
            for name, value in entry.fields.items()
            if name not in _MAPPED_FIELDS and name not in _IDENTIFIER_FIELDS
        }
        if leftovers:
            extra["bibtex_extra_fields"] = leftovers

    return CSLRecord(
        id=entry.key or "",
        type=BIBTEX_TO_CSL_TYPE.get(entry.entry_type, "document"),
        title=latex_to_text(entry.get("title")),
        author=[bibtex_name_to_csl(name) for name in split_bibtex_names(entry.get("author"))],
        editor=[bibtex_name_to_csl(name) for name in split_bibtex_names(entry.get("editor"))],
        issued=_issued(entry),
        container_title=container,
        publisher=publisher,
        volume=latex_to_text(entry.get("volume")),
        issue=issue,
        page=pages,
        number=number,
        abstract=latex_to_text(entry.get("abstract")),
        DOI=normalize_doi(latex_to_text(entry.get("doi"))),
        URL=latex_to_text(entry.get("url")),
        ISBN=latex_to_text(entry.get("isbn")),
        language=latex_to_text(entry.get("language")),
        note=latex_to_text(entry.get("note")),
        source="bibtex",
        arxiv_id=_arxiv_id(entry),
        extra=extra,
    )


def citation_key_for(record: CSLRecord) -> str:
    """A stable, BibTeX-legal citation key for a record that has none.

    ``surname + year + first significant title word``, ASCII-folded and lowercased, for
    example ``mehari2022self``. Falls back to the record's content hash when the record is
    too thin to name.
    """
    surname = _ascii_slug(record.first_author_surname)
    year = str(record.year) if record.year else ""
    title_word = ""
    for word in re.split(r"\s+", record.title):
        slug = _ascii_slug(word)
        if len(slug) > 3:
            title_word = slug
            break
    key = f"{surname}{year}{title_word}"
    if not key or not key[0].isalpha():
        key = f"ref{key}" if key else f"ref{record.content_hash()[:8]}"
    return key


def _ascii_slug(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^A-Za-z0-9]", "", ascii_only).lower()


def record_to_entry(record: CSLRecord, *, key: str | None = None) -> BibEntry:
    """Emit a :class:`CSLRecord` as a :class:`BibEntry`.

    When the record carries ``custom.bibtex`` (that is, it came from :func:`entry_to_record`),
    the original entry is restored verbatim, so ``bib -> CSL -> bib`` loses nothing, not even
    brace capitalization protection. Otherwise the entry is synthesized from the CSL fields.
    """
    source = record.extra.get(_CUSTOM_BIBTEX_KEY)
    if isinstance(source, Mapping) and isinstance(source.get("fields"), Mapping):
        return BibEntry(
            entry_type=str(source.get("type") or "misc"),
            key=str(key or source.get("key") or record.id),
            fields={str(k): str(v) for k, v in source["fields"].items()},
        )

    entry_type = CSL_TO_BIBTEX_TYPE.get(record.type, "misc")
    if key is None:
        key = record.id if _SAFE_KEY_RE.match(record.id or "") else citation_key_for(record)

    fields: dict[str, str] = {}
    if record.author:
        fields["author"] = " and ".join(_name_to_bibtex(name) for name in record.author)
    if record.editor:
        fields["editor"] = " and ".join(_name_to_bibtex(name) for name in record.editor)
    if record.title:
        fields["title"] = record.title
    if record.container_title:
        container = "booktitle" if entry_type in {"inproceedings", "incollection"} else "journal"
        fields[container] = record.container_title
    if record.publisher:
        publisher_field = "publisher"
        if entry_type in {"phdthesis", "mastersthesis"}:
            publisher_field = "school"
        elif entry_type == "techreport":
            publisher_field = "institution"
        fields[publisher_field] = record.publisher
    if record.volume:
        fields["volume"] = record.volume
    if record.issue:
        fields["number"] = record.issue
    elif record.number:
        fields["number"] = record.number
    if record.page:
        fields["pages"] = re.sub(r"(?<=\d)-(?=\d)", "--", record.page)
    if record.year is not None:
        fields["year"] = str(record.year)
    if record.issued is not None and record.issued.month in _MONTH_NAMES_BY_NUMBER:
        fields["month"] = _MONTH_NAMES_BY_NUMBER[record.issued.month]
    if record.DOI:
        fields["doi"] = record.DOI
    if record.URL:
        fields["url"] = record.URL
    if record.arxiv_id:
        fields["eprint"] = record.arxiv_id
        fields["archiveprefix"] = "arXiv"
    if record.ISSN:
        fields["issn"] = ", ".join(record.ISSN)
    if record.ISBN:
        fields["isbn"] = record.ISBN
    if record.language:
        fields["language"] = record.language
    if record.keyword:
        fields["keywords"] = ", ".join(record.keyword)
    if record.abstract:
        fields["abstract"] = record.abstract
    if record.note:
        fields["note"] = record.note

    leftovers = record.extra.get("bibtex_extra_fields")
    if isinstance(leftovers, Mapping):
        for name, value in leftovers.items():
            fields.setdefault(str(name).lower(), str(value))

    return BibEntry(entry_type=entry_type, key=key, fields=fields)
