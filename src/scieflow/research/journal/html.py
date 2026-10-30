"""Saved author-guideline pages as plain text (M-F 5.4, Text).

Deterministic and standard-library only. Scripts, styles, templates, SVG, frames and the
document head are dropped (the title is kept as the first line); block tags end a line; list
items start with "- "; table cells are separated by " | "; whitespace is collapsed per line.
A body with no <html> or <body> root is plain text and is kept as it is. The result passes
`sanitize.fence_text`: the text is data, never instructions.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from scieflow.core.sanitize import fence_text

_DROP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head"})
_BLOCK = frozenset({
    "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article",
    "table", "ul", "ol", "dl", "dt", "dd", "header", "footer", "nav", "aside", "main",
    "blockquote", "pre",
})
_ROOT = re.compile(r"<(?:html|body)[\s>]", re.IGNORECASE)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.cur: list[str] = []
        self.skip = 0
        self.in_title = False
        self.title: list[str] = []
        self.row_cells = 0

    def _break(self) -> None:
        text = " ".join("".join(self.cur).split())
        self.cur = []
        if text and text != "-":          # an empty list item is not a line
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
            return
        if tag in _DROP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in _BLOCK:
            self._break()
        if tag == "li":
            self.cur.append("- ")
        elif tag == "tr":
            self.row_cells = 0
        elif tag in ("td", "th"):
            if self.row_cells:
                self.cur.append(" | ")
            self.row_cells += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag in _DROP:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in _BLOCK:
            self._break()

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
            return
        if self.skip:
            return
        if data.strip():
            self.cur.append(data)
        elif self.cur:
            self.cur.append(" ")

    def close_text(self) -> list[str]:
        self._break()
        head = " ".join("".join(self.title).split())
        return ([head] if head else []) + self.lines


def _collapse_blank(lines: list[str]) -> list[str]:
    """One empty line at most in a row; none at the start or the end."""
    out: list[str] = []
    for line in (ln.rstrip() for ln in lines):
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out


def to_text(body: str) -> str:
    """The plain text of one saved page. Deterministic; the caller stores it as data."""
    if not _ROOT.search(body):
        lines = _collapse_blank(body.splitlines())
    else:
        parser = _Text()
        parser.feed(body)
        parser.close()
        lines = _collapse_blank(parser.close_text())
    return fence_text("\n".join(lines))
