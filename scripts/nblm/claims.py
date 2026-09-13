"""Turn a LaTeX manuscript plus its .bib into claims.yml — offline, no network.

A claim is one sentence that carries a citation, paired with one cited DOI.
A sentence citing two sources yields two claims: verification is per-source.

The .bib DOI scan here is deliberately entry-scoped and unanchored. The older
vendors/ResearchX/scripts/check_citations.py anchors its DOI regex with
re.M + '^\\s*doi', so a 'doi=' field on a single-line BibTeX entry is silently
missed — a documented live bug this module must not inherit.
"""

import bisect
import re
from pathlib import Path

# \cite, \citep, \citet, \citealp, \parencite, \autocite, \textcite ...
# with any number of optional [..] arguments before the key group.
CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")
INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]*)\}")
ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s}]+)\s*,", re.S)
# Unanchored on purpose: matches 'doi = {...}' anywhere in the entry body,
# including single-line entries. The lookbehind keeps it from matching
# 'eprintdoi' or similar composite field names.
BIB_DOI_RE = re.compile(r"(?<![A-Za-z])doi\s*=\s*[{\"]?\s*([^,}\"\n]+)", re.I)

# A '.' after one of these is an abbreviation, not a sentence end.
ABBREVIATIONS = {
    "e.g", "i.e", "cf", "vs", "al", "approx", "etc", "resp", "viz",
    "Fig", "Figs", "Eq", "Eqs", "Tab", "Sec", "Ref", "Refs", "No",
    "Dr", "Prof", "St", "cf", "ca", "min", "max", "std",
}
# Lines that are pure document structure: they never carry a verifiable
# claim, and blanking them keeps prose from running together across them.
STRUCTURAL_RE = re.compile(
    r"^[ \t]*\\(?:documentclass|usepackage|begin|end|(?:sub)*section|"
    r"paragraph|label|ref|maketitle|title|author|date|address|"
    r"bibliography\w*|printbibliography|input|include|newcommand|"
    r"renewcommand|def|caption|includegraphics)\b.*$",
    re.M,
)
# A sentence ends at .!? followed by a capital or a command, or at a
# paragraph break.
SENTENCE_BOUNDARY_RE = re.compile(
    r"(?<=[.!?])[ \t]*\n?[ \t]*(?=[A-Z\\(])|\n[ \t]*\n\s*"
)


def strip_comments(text: str) -> str:
    """Drop LaTeX comments while preserving every newline (line numbers matter)."""
    out = []
    for line in text.split("\n"):
        cleaned = re.sub(r"(?<!\\)%.*$", "", line)
        out.append(cleaned)
    return "\n".join(out)


def blank_structural(text: str) -> str:
    """Blank structural lines in place, preserving every newline."""
    return STRUCTURAL_RE.sub("", text)


def parse_bib(text: str) -> dict[str, str]:
    """Citation key -> DOI, for every entry that declares one."""
    dois = {}
    matches = list(ENTRY_RE.finditer(text))
    for i, match in enumerate(matches):
        key = match.group(2)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end]
        found = BIB_DOI_RE.search(body)
        if not found:
            continue
        doi = found.group(1).strip().rstrip("},").strip()
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
        if doi:
            dois[key] = doi
    return dois


def tex_files(main: Path) -> list[Path]:
    """The manuscript plus every \\input/\\include child, depth-first, once each."""
    seen = []
    pending = [main]
    while pending:
        current = pending.pop(0)
        if not current.exists() or current in seen:
            continue
        seen.append(current)
        body = strip_comments(current.read_text(encoding="utf-8", errors="replace"))
        for name in INPUT_RE.findall(body):
            child = (current.parent / name.strip()).with_suffix(".tex")
            if child not in seen:
                pending.append(child)
    return seen


def _line_of(offsets: list[int], position: int) -> int:
    return bisect.bisect_right(offsets, position)


def _is_abbreviation(text: str) -> bool:
    tail = re.search(r"([A-Za-z.]+)\.$", text.rstrip())
    if not tail:
        return False
    return tail.group(1).rstrip(".").split(".")[-1] in ABBREVIATIONS \
        or tail.group(1) in ABBREVIATIONS


def split_sentences(text: str) -> list[tuple[int, str]]:
    """(offset, sentence) pairs. Offsets index into `text`."""
    sentences = []
    start = 0
    for boundary in SENTENCE_BOUNDARY_RE.finditer(text):
        if boundary.start() < start:
            continue
        candidate = text[start:boundary.start()]
        if not candidate.strip():
            start = boundary.end()      # nothing there: skip the gap entirely
            continue
        if _is_abbreviation(candidate):
            continue                    # not a real end: keep accumulating
        sentences.append((start, candidate))
        start = boundary.end()
    if text[start:].strip():
        sentences.append((start, text[start:]))
    return sentences


# Formatting macros carry no meaning for the check; keep their argument only.
# Left in, they reach NotebookLM as literal "\\textbf{...}" noise.
FORMATTING_RE = re.compile(
    r"\\(?:textbf|textit|texttt|emph|textsc|textrm|mathrm|text|underline)"
    r"\{([^{}]*)\}")


def clean_text(sentence: str) -> str:
    """Readable prose for the question: citations removed, markup unwrapped."""
    without_cites = CITE_RE.sub("", sentence)
    for _ in range(3):                       # unwrap simple nesting
        unwrapped = FORMATTING_RE.sub(r"\1", without_cites)
        if unwrapped == without_cites:
            break
        without_cites = unwrapped
    without_cites = without_cites.replace("---", "\u2014").replace("--", "\u2013")
    without_cites = without_cites.replace("~", " ")
    # LaTeX escapes: "\ " (hard space), "\%", "\&", "\_", "\#", "\$".
    without_cites = re.sub(r"\\([ %&_#$])", r"\1", without_cites)
    collapsed = re.sub(r"\s+", " ", without_cites)
    # Removing a citation can strand a space before its punctuation.
    collapsed = re.sub(r"\s+([,.;:)\]])", r"\1", collapsed)
    return collapsed.strip(" ,;")


def extract(manuscript: Path, bib: Path) -> dict:
    """claims.yml payload: one claim per (sentence, cited DOI)."""
    bib_text = bib.read_text(encoding="utf-8", errors="replace")
    dois = parse_bib(bib_text)
    known_keys = {m.group(2) for m in ENTRY_RE.finditer(bib_text)}
    root = manuscript.parent
    claims: list[dict] = []
    unresolved: dict[str, str] = {}
    counter = 0

    for path in tex_files(manuscript):
        body = blank_structural(
            strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        )
        offsets = [i for i, ch in enumerate(body) if ch == "\n"]
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = str(path)
        for offset, sentence in split_sentences(body):
            keys = []
            for group in CITE_RE.findall(sentence):
                keys.extend(k.strip() for k in group.split(",") if k.strip())
            if not keys:
                continue
            text = clean_text(sentence)
            if not text:
                continue
            line = _line_of(offsets, offset) + 1
            for key in dict.fromkeys(keys):
                if key not in dois:
                    reason = ("bib entry has no doi field" if key in known_keys
                              else "key not in bib")
                    unresolved[key] = reason
                    continue
                counter += 1
                claims.append({
                    "id": f"c{counter:03d}",
                    "text": text,
                    "file": label,
                    "line": line,
                    "cites": list(dict.fromkeys(keys)),
                    "doi": dois[key],
                })
    return {
        "manuscript": str(manuscript),
        "claims": claims,
        "unresolved": [{"key": k, "reason": v} for k, v in sorted(unresolved.items())],
    }
