"""The LaTeX manuscript reader (M-F 5.8): tree, comments, includes, word count, structure.

`read_tree` resolves `\\input` / `\\include` inside manuscript/ (no escape, no symlink, at most 200
files and depth 20, cycles refused) and flattens them into lines that keep their file and line.
`analyse` splits the body into sections, counts words by the rule of M-F 5.8 and reads the citation
keys and the bibliography years. Nothing here writes a file or touches the network.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from scieflow.research.citations import BIBKEY_RE, CITE_RE

MAX_FILES = 200
MAX_DEPTH = 20
MAX_BYTES = 5_000_000
INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
BIB_YEAR_RE = re.compile(r"\b(?:year|date)\s*=\s*[{\"]?\s*(\d{4})")

# Environments whose content is not main text (captions are counted apart).
MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "math",
             "flalign", "alignat", "split")
DROP_ENVS = MATH_ENVS + ("figure", "table", "tabular", "verbatim", "lstlisting", "minted",
                         "thebibliography", "comment")
DROP_RE = re.compile(r"\\begin\{(?P<env>" + "|".join(DROP_ENVS) + r")(?P<star>\*?)\}"
                     r".*?\\end\{(?P=env)(?P=star)\}", re.S)
DISPLAY_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "flalign",
                "alignat")
DISPLAY_RE = re.compile(r"\\begin\{(?:" + "|".join(DISPLAY_ENVS) + r")\*?\}")
HREF_RE = re.compile(r"\\href\{[^{}]*\}\{([^{}]*)\}")
NOTEXT_RE = re.compile(
    r"\\(?:cite[a-zA-Z]*|[cC]ref|ref|eqref|autoref|label|url|includegraphics|bibliography|"
    r"bibliographystyle|input|include|usepackage|documentclass|newcommand|renewcommand|vspace|"
    r"hspace)\*?(?:\[[^\]]*\])*\{[^{}]*\}")
ARG_CMD_RE = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])*\{([^{}]*)\}")
WORD_RE = re.compile(r"[^\W_](?:[\w'’.\-]*[^\W_])?")

EXCLUDED_TITLES = frozenset({
    "references", "bibliography", "acknowledgements", "acknowledgments", "funding", "declarations",
    "competing interests", "conflict of interest", "conflicts of interest", "author contributions",
    "credit authorship contribution statement", "data availability", "code availability", "ethics",
    "ethics statement", "supplementary material", "appendix",
})
TEX_BYPRODUCTS = (".aux", ".log", ".out", ".bbl", ".blg", ".fls", ".fdb_latexmk", ".synctex.gz",
                  ".toc", ".bcf", ".run.xml", ".lof", ".lot")
TREE_EXCLUDED_DIRS = frozenset({"build", "drafts", "letters"})


class TexError(ValueError):
    """A manuscript file cannot be read as a tree: `rule` names the refusal, `path` the file."""

    def __init__(self, rule: str, path: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Line:
    file: str
    lineno: int
    text: str


@dataclass
class Tree:
    folder: Path
    main: str
    files: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)

    def flat_text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass
class Section:
    title: str
    norm: str
    file: str
    lineno: int
    words: int
    excluded: bool

    def to_dict(self) -> dict:
        return {"title": self.title, "norm": self.norm, "file": self.file, "lineno": self.lineno,
                "words": self.words, "excluded": self.excluded}


@dataclass
class Analysis:
    documentclass: str | None
    bibstyle: str | None
    title: str | None
    title_words: int
    abstract: str | None
    abstract_words: int | None
    tex_keywords: list | None
    sections: list
    main_words: int
    caption_words: int
    figures: int
    tables: int
    display_items: int
    cites: list
    bib: dict | None

    def to_dict(self) -> dict:
        return {
            "documentclass": self.documentclass, "bibstyle": self.bibstyle,
            "title_words": self.title_words, "abstract_words": self.abstract_words,
            "sections": [s.to_dict() for s in self.sections], "main_words": self.main_words,
            "caption_words": self.caption_words, "figures": self.figures, "tables": self.tables,
            "display_items": self.display_items, "cites": list(self.cites),
            "bib": None if self.bib is None else dict(sorted(self.bib.items())),
        }


# --- files ---------------------------------------------------------------------------------------

def _inside(folder: Path, name: str) -> Path:
    """The path of `name` under `folder`; refused when it leaves the folder or is a symlink."""
    path = folder / name
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise TexError("tex-path", name, f"{name}: cannot be resolved") from exc
    base = folder.resolve()
    if resolved != base and base not in resolved.parents:
        raise TexError("tex-path", name, f"{name}: lies outside the manuscript folder")
    if path.is_symlink():
        raise TexError("tex-path", name, f"{name}: is a symbolic link")
    return path


def read_regular_text(folder: Path, name: str, limit: int = MAX_BYTES) -> str:
    """A regular, non-symlink file of the manuscript folder, read as UTF-8 (refused otherwise)."""
    path = _inside(folder, name)
    if not path.is_file():
        raise TexError("tex-missing-input", name, f"{name}: no such file")
    if path.stat().st_size > limit:
        raise TexError("tex-limit", name, f"{name}: larger than {limit} bytes")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TexError("tex-encoding", name, f"{name}: not UTF-8") from exc


def _tex_name(raw: str) -> str:
    name = raw.strip()
    if not Path(name).suffix:
        name += ".tex"
    return name


def strip_line(line: str) -> str:
    """A line without its comment: from the first `%` not escaped by an odd number of backslashes."""
    start = 0
    while True:
        at = line.find("%", start)
        if at < 0:
            return line
        backslashes = 0
        index = at - 1
        while index >= 0 and line[index] == "\\":
            backslashes += 1
            index -= 1
        if backslashes % 2 == 0:
            return line[:at]
        start = at + 1


def strip_comments(text: str) -> str:
    return "\n".join(strip_line(line) for line in text.split("\n"))


def read_tree(folder, main: str = "main.tex") -> Tree:
    """The flattened manuscript: `main` and everything it inputs, within the limits of M-F 5.8."""
    folder = Path(folder)
    tree = Tree(folder=folder, main=_tex_name(main))
    _expand(tree, tree.main, depth=0, stack=[])
    return tree


def _expand(tree: Tree, name: str, depth: int, stack: list) -> None:
    if depth > MAX_DEPTH:
        raise TexError("tex-limit", name, f"{name}: nested deeper than {MAX_DEPTH} levels")
    if name in stack:
        raise TexError("tex-cycle", name, f"{name}: inputs itself")
    if name not in tree.files:
        if len(tree.files) >= MAX_FILES:
            raise TexError("tex-limit", name, f"more than {MAX_FILES} files")
        tree.files[name] = strip_comments(read_regular_text(tree.folder, name))
    stack.append(name)
    for lineno, raw in enumerate(tree.files[name].split("\n"), start=1):
        position = 0
        for match in INCLUDE_RE.finditer(raw):
            tree.lines.append(Line(name, lineno, raw[position:match.start()]))
            _expand(tree, _tex_name(match.group(1)), depth + 1, stack)
            position = match.end()
        tree.lines.append(Line(name, lineno, raw[position:]))
    stack.pop()


def source_tree_hash(folder) -> str:
    """sha256 over the sorted lines `<sha256>  <relpath>` of the manuscript's source files (M-F 5.12)."""
    folder = Path(folder)
    rows = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel.split("/")[0] in TREE_EXCLUDED_DIRS or ".bak-" in path.name:
            continue
        if path.name.endswith(TEX_BYPRODUCTS):
            continue
        rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}")
    text = "\n".join(sorted(rows))
    return hashlib.sha256((text + "\n" if rows else "").encode("utf-8")).hexdigest()


# --- words ---------------------------------------------------------------------------------------

def count_words(text: str) -> int:
    """Main-text words of a LaTeX fragment (M-F 5.8): math, figures and tables out; commands out."""
    return len(WORD_RE.findall(_plain(strip_comments(text))))


def caption_words(text: str) -> int:
    """Words of the \\caption arguments of a fragment (counted apart from the main text)."""
    return sum(count_words(body) for body in _braced_args(strip_comments(text), "caption"))


def _plain(text: str) -> str:
    text = DROP_RE.sub(" ", text)
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"\\\[.*?\\\]", " ", text, flags=re.S)
    text = re.sub(r"\\\(.*?\\\)", " ", text, flags=re.S)
    text = re.sub(r"(?<!\\)\$[^$]*?(?<!\\)\$", " ", text, flags=re.S)
    text = HREF_RE.sub(r" \1 ", text)
    text = NOTEXT_RE.sub(" ", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]*\}", " ", text)
    for _ in range(20):
        shrunk = ARG_CMD_RE.sub(r" \1 ", text)
        if shrunk == text:
            break
        text = shrunk
    text = re.sub(r"\\\\", " ", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = re.sub(r"\\[%&$#_{}~,;:! ]", " ", text)
    return text.replace("~", " ").replace("{", " ").replace("}", " ")


def normalise_title(text: str) -> str:
    """Title for matching: commands keep their arguments, `&` is `and`, punctuation out, lower case."""
    for _ in range(20):
        shrunk = ARG_CMD_RE.sub(r" \1 ", text)
        if shrunk == text:
            break
        text = shrunk
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text).replace("\\&", "&").replace("&", " and ")
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.lower().split())


def _braced(text: str, start: int) -> tuple:
    """The content of the brace group opening at text[start] and the index after its close."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return text[start + 1:], len(text)


def _braced_args(text: str, command: str) -> list:
    return [_braced(text, m.end() - 1)[0]
            for m in re.finditer(r"\\" + command + r"\*?\s*\{", text)]


# --- structure -----------------------------------------------------------------------------------

def _document_body(flat: str) -> str:
    start = flat.find("\\begin{document}")
    body = flat[start + len("\\begin{document}"):] if start >= 0 else flat
    end = body.find("\\end{document}")
    return body[:end] if end >= 0 else body


def _section_locations(tree: Tree) -> list:
    """(file, lineno) of each \\section header of the document body, in order."""
    started = "\\begin{document}" not in tree.flat_text()
    found = []
    for line in tree.lines:
        if not started and "\\begin{document}" in line.text:
            started = True
            continue
        if started:
            found.extend([(line.file, line.lineno)] * len(re.findall(r"\\section\*?\s*\{", line.text)))
    return found


def read_bib(folder: Path) -> dict | None:
    """Bibliography keys and their years from references.bib (None when the file is absent)."""
    if not (folder / "references.bib").exists():
        return None
    text = read_regular_text(folder, "references.bib")
    keys = list(BIBKEY_RE.finditer(text))
    entries = {}
    for index, match in enumerate(keys):
        end = keys[index + 1].start() if index + 1 < len(keys) else len(text)
        year = BIB_YEAR_RE.search(text[match.end():end])
        entries[match.group(1)] = year.group(1) if year else None
    return entries


def _keyword_lists(body: str) -> tuple:
    """(keywords found in the TeX, body without keyword markup)."""
    found = None
    for env, splitter in (("keyword", r"\\sep\b"), ("IEEEkeywords", ",")):
        match = re.search(r"\\begin\{" + env + r"\}(.*?)\\end\{" + env + r"\}", body, re.S)
        if match:
            body = body[:match.start()] + body[match.end():]
            if found is None:
                found = re.split(splitter, match.group(1))
    for arg in _braced_args(body, "keywords"):
        if found is None:
            found = re.split(r"[,;]", arg)
    body = re.sub(r"\\keywords\s*\{(?:[^{}]|\{[^{}]*\})*\}", " ", body)
    if found is not None:
        found = [item.strip() for item in found if item.strip()]
    return found, body


def analyse(tree: Tree) -> Analysis:
    flat = tree.flat_text()
    preamble = _document_body(flat)
    doc_class = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^{}]+)\}", flat)
    bib_style = re.search(r"\\bibliographystyle\{([^{}]+)\}", flat)

    title_match = re.search(r"\\title\s*\{", preamble)
    title = _braced(preamble, title_match.end() - 1)[0] if title_match else None
    body = re.sub(r"\\title\s*\{(?:[^{}]|\{[^{}]*\})*\}", " ", preamble)
    body = body.replace("\\maketitle", " ")

    abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S)
    abstract = abstract_match.group(1) if abstract_match else None
    if abstract_match:
        body = body[:abstract_match.start()] + " " + body[abstract_match.end():]

    tex_keywords, body = _keyword_lists(body)

    heads = []
    for match in re.finditer(r"\\section\*?\s*\{", body):
        content, end = _braced(body, match.end() - 1)
        heads.append((match.start(), end, content))
    locations = _section_locations(tree)
    sections = []
    pre = body[:heads[0][0]] if heads else body
    if count_words(pre):
        sections.append(Section("", "", tree.main, 0, count_words(pre), False))
    for index, (_start, end, title_text) in enumerate(heads):
        stop = heads[index + 1][0] if index + 1 < len(heads) else len(body)
        segment = body[end:stop]
        norm = normalise_title(title_text)
        file, lineno = locations[index] if index < len(locations) else (tree.main, 0)
        sections.append(Section(
            title=" ".join(title_text.split()), norm=norm, file=file, lineno=lineno,
            words=count_words(segment), excluded=norm in EXCLUDED_TITLES))

    main_words = sum(s.words for s in sections if not s.excluded)
    cites = []
    for match in CITE_RE.finditer(flat):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key not in cites:
                cites.append(key)
    display = len(DISPLAY_RE.findall(flat)) + flat.count("$$") // 2 + flat.count("\\[")

    return Analysis(
        documentclass=doc_class.group(1).strip() if doc_class else None,
        bibstyle=bib_style.group(1).strip() if bib_style else None,
        title=title, title_words=count_words(title) if title else 0,
        abstract=abstract, abstract_words=count_words(abstract) if abstract is not None else None,
        tex_keywords=tex_keywords, sections=sections, main_words=main_words,
        caption_words=sum(count_words(c) for c in _braced_args(flat, "caption")),
        figures=len(re.findall(r"\\begin\{figure\*?\}", flat)),
        tables=len(re.findall(r"\\begin\{table\*?\}", flat)),
        display_items=display, cites=cites, bib=read_bib(tree.folder),
    )
