"""Claim units of a manuscript (M-G 5.2): the sentences, abstract sentences, figures and tables of the body.

`extract_units(tree)` takes the tree read with `tex.read_tree(..., keep_comments=True)` and returns every unit
of the document body in document order. A unit is only a structure here (its text, lines, section and the
comments of its lines); whether it is a claim, and its references, are decided by the ledger.

The body is the text from `\\begin{document}` to `\\end{document}`, or to the first bibliography command. A
file without `\\begin{document}` is all body, as M-F treats it. Sentences come from the paragraphs between
blank lines, section headings, and figure, table and abstract environments; the abstract's sentences are
abstract units; display maths belongs to its sentence.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace

from scieflow.core.review.claims import parser as claim_parser
from scieflow.research.manuscript import checklist, tex

BODY_BEGIN = "\\begin{document}"
BODY_END_RE = re.compile(r"\\end\{document\}|\\printbibliography|\\bibliography\s*\{|\\begin\{thebibliography\}")
META_RE = re.compile(
    r"\\(?:title|author|date|affiliation|thanks|maketitle|printbibliography|bibliographystyle|bibliography)\b"
    r"(?:\s*\[[^\]]*\])?(?:\s*\{(?:[^{}]|\{[^{}]*\})*\})?")
SECTION_RE = re.compile(r"\\section\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
HEADING_RE = re.compile(r"\\(?:sub)*(?:section|paragraph)\*?\s*\{(?:[^{}]|\{[^{}]*\})*\}")
ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.S)
ENV_RE = re.compile(r"\\begin\{(?P<env>figure|table)(?P<star>\*?)\}.*?\\end\{(?P=env)(?P=star)\}", re.S)
BLANK_RE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str                # sentence | abstract | figure | table
    file: str
    line: int
    end_line: int
    section: str             # the title of the enclosing \section ("" for the front and the abstract)
    section_kinds: tuple     # sorted part kinds of the section (checklist.section_kinds)
    text: str                # NFC, comments removed, whitespace collapsed; the full text
    comments: tuple          # the comments of the lines the unit spans, in line order
    offset: int              # position in the body text (document order)


@dataclass(frozen=True)
class _Row:
    file: str
    lineno: int
    code: str                # the line without its comment
    comment: str             # the comment ("" when none), starting at the first unescaped '%'


def normalise(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def _body_rows(tree: tex.Tree) -> list[_Row]:
    has_begin = any(BODY_BEGIN in tex.strip_line(line.text) for line in tree.lines)
    started = not has_begin
    rows: list[_Row] = []
    for line in tree.lines:
        raw = line.text
        code = tex.strip_line(raw)
        if not started:
            if BODY_BEGIN not in code:
                continue
            cut = code.index(BODY_BEGIN) + len(BODY_BEGIN)
            raw, code = raw[cut:], code[cut:]
            started = True
        end = BODY_END_RE.search(code)
        if end:
            raw, code = raw[:end.start()], code[:end.start()]
            rows.append(_Row(line.file, line.lineno, code, raw[len(code):]))
            break
        rows.append(_Row(line.file, line.lineno, code, raw[len(code):]))
    return rows


def _line_index(starts: list, offset: int) -> int:
    return max(0, bisect.bisect_right(starts, offset) - 1)


def _sentences(text: str, base: int) -> list[tuple[int, int]]:
    """(start, end) offsets in the body of each sentence of `text`, which starts at body offset `base`."""
    spans = []
    for offset, sentence in claim_parser().split_sentences(text):
        if not sentence.strip():
            continue
        start = base + offset + (len(sentence) - len(sentence.lstrip()))
        end = base + offset + len(sentence.rstrip())
        spans.append((start, end))
    return spans


def extract_units(tree: tex.Tree) -> list[Unit]:
    """Every claim unit of the body of `tree`, in document order, with ids (M-G 5.2)."""
    rows = _body_rows(tree)
    starts: list[int] = []
    parts: list[str] = []
    position = 0
    for row in rows:
        starts.append(position)
        parts.append(row.code)
        position += len(row.code) + 1
    doc = "\n".join(parts)
    prose = META_RE.sub(lambda m: " " * len(m.group(0)), doc)

    headings = [(m.start(), " ".join(m.group(1).split())) for m in SECTION_RE.finditer(doc)]
    heading_starts = [start for start, _title in headings]

    def section_of(offset: int) -> tuple:
        index = bisect.bisect_right(heading_starts, offset) - 1
        if index < 0:
            return "", ("front",)
        title = headings[index][1]
        return title, tuple(sorted(checklist.section_kinds(title)))

    def unit(kind, start, end, section, kinds, offset):
        first = _line_index(starts, start)
        last = _line_index(starts, max(start, end - 1))
        if rows[last].file != rows[first].file:
            last = first
        comments = tuple(rows[k].comment for k in range(first, last + 1) if rows[k].comment)
        text = normalise(doc[start:end] if kind != "sentence" else prose[start:end])
        return Unit(id="", kind=kind, file=rows[first].file, line=rows[first].lineno,
                    end_line=rows[last].lineno, section=section, section_kinds=kinds, text=text,
                    comments=comments, offset=offset)

    found: list[Unit] = []

    for match in ABSTRACT_RE.finditer(doc):
        for start, end in _sentences(doc[match.start(1):match.end(1)], match.start(1)):
            found.append(unit("abstract", start, end, "", ("abstract",), start))

    for match in ENV_RE.finditer(doc):
        section, kinds = section_of(match.start())
        found.append(unit(match.group("env"), match.start(), match.end(), section, kinds, match.start()))

    barriers = [(m.start(), m.end()) for m in ABSTRACT_RE.finditer(doc)]
    barriers += [(m.start(), m.end()) for m in ENV_RE.finditer(doc)]
    barriers += [(m.start(), m.end()) for m in HEADING_RE.finditer(doc)]
    barriers += [(m.start(), m.end()) for m in BLANK_RE.finditer(prose)]
    barriers.sort()
    gaps, cursor = [], 0
    for start, end in barriers:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(doc):
        gaps.append((cursor, len(doc)))
    for gap_start, gap_end in gaps:
        chunk = prose[gap_start:gap_end]
        if not chunk.strip():
            continue
        for start, end in _sentences(chunk, gap_start):
            section, kinds = section_of(start)
            found.append(unit("sentence", start, end, section, kinds, start))

    found.sort(key=lambda u: u.offset)
    ids: dict[str, int] = {}
    numbered: list[Unit] = []
    for item in found:
        digest = hashlib.sha256((item.file + "\n" + item.text).encode("utf-8")).hexdigest()[:12]
        base = "c-" + digest
        ids[base] = ids.get(base, 0) + 1
        numbered.append(replace(item, id=base if ids[base] == 1 else f"{base}-{ids[base]}"))
    return numbered
