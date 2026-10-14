"""Citing and novelty sentences from LaTeX drafts, and the sanitiser that guards them.

Parsing reuses the claim-check module `scripts/nblm/claims.py` (comment stripping,
structural blanking, sentence splitting, `\\cite` and `.bib` parsing, markup
cleaning) — no fork. This module adds what the review needs on top:

- one claim per citing sentence (all cited DOIs together, ids `C1…`), the union
  over every draft, de-duplicated by sanitised text;
- novelty sentences ("the first", "novel", "to our knowledge", …), cited or not;
- the section each sentence sits in (`\\section{…}` heading, else the file name),
  so list wildcards can put Results and Discussion first;
- the **sanitiser**. Only literature claims may leave the machine (spec Q5):
  numbers with units, percentages, statistics (p, n, CI, OR …), math, `[data:…]`,
  `% source:` comments, LaTeX pointers (`\\ref`, `\\input` …), URLs, e-mail
  addresses, DOIs written into prose and anything path-shaped are removed. The
  result must then pass `check_text`, a whitelist check; a value that still looks
  like a leak is refused (`LeakError`), never sent. Refusals never echo the value.
- `safe_doi`: a DOI is only ever sent when it is DOI-shaped *and* not path-shaped
  (no `..`/`.`-segments, no `//`, no local file extension, no path roots).

Limitation, documented: numbers written as words ("forty-three patients") are not
recognised as numbers. Draft `\\input` children are read only inside the draft's
own directory (`drafts/<agent>/` or `manuscript/`).
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from scieflow.core import config as config_mod
from scieflow.research.lib import doi as doi_lib

DRAFTS_DIR = Path("manuscript") / "drafts"
MANUSCRIPT_DIR = Path("manuscript")
BIB_NAME = "references.bib"
STATUS_NAME = "status.yml"
PAPER_REVIEW = "paper-review"


def reviews_manuscript(ws: Path) -> bool:
    """True for a `paper-review` run (its status.yml says `workflow: paper-review`):
    the reviewed text is the submitter's manuscript under `manuscript/`, not a
    draft under `manuscript/drafts/<agent>/`."""
    import yaml

    path = Path(ws) / STATUS_NAME
    if path.is_symlink() or not path.is_file():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and data.get("workflow") == PAPER_REVIEW


def parser():
    """The claim-check parser module (`scripts/nblm/claims.py`), which is part of
    the repo but not of the installed package."""
    try:
        from nblm import claims as nblm_claims
    except ImportError:
        scripts = str(config_mod.repo_root() / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from nblm import claims as nblm_claims
    return nblm_claims


class LeakError(ValueError):
    """A value still looks like data after sanitising; it is refused, never sent.
    The message names the kind of leak only, never the value."""


# -- the leak check (whitelist) -------------------------------------------------
# Letters, digits (only inside identifiers, see identifier_ok), whitespace and plain
# sentence punctuation. Everything else — %, /, \, {, }, <, >, =, ~, $, @, |, [, ],
# #, ^, _, `, * … — is a leak indicator.
_ALLOWED_PUNCT = set(".,;:!?'\"()-\u2013\u2014\u2018\u2019\u201c\u201d&+")
_LABELS = (r"data|datasets?|source|files?|filename|path|dir|directory|folder|cohort|table|tab|"
           r"vars?|variables?|columns?|col|sheet|field|record|id")
_LABEL_COLON = re.compile(rf"\b(?:{_LABELS})\s*:", re.I)
# A word with digits, possibly dotted ("v2.3.1", "0.05", "LRRK2").
_DIGIT_WORD = re.compile(r"[\w-]+(?:\.[\w-]+)*")
_MUTATION = re.compile(r"^[A-Z]\d{1,5}[A-Z]$")


def identifier_ok(token: str) -> bool:
    """Whether a word that contains digits may stay: a gene / protein / compound /
    variant style identifier (LRRK2, G2019S, IL-6, COVID-19, SARS-CoV-2, HbA1c, H2O).
    Needs two capitals, digit runs of at most two (variants: one capital, digits, one
    capital) and no dotted or digit-led part. Subject, visit and record codes
    (sub-01, P-12, Day-28, n43, RBD043, v2.3.1) fail. Residual, documented: a code
    with two capitals and one or two digits (\"PD12\") looks like an identifier."""
    if not any(ch.isdigit() for ch in token):
        return True
    if "." in token or "," in token:
        return False
    if _MUTATION.match(token):
        return True
    parts = token.split("-")
    if any(not p for p in parts) or sum(ch.isupper() for ch in token) < 2:
        return False
    for i, part in enumerate(parts):
        if not any(ch.isdigit() for ch in part):
            continue
        if re.search(r"\d{3,}", part) or "_" in part:
            return False
        if part[0].isdigit():
            # IL-6, COVID-19, IL-1b: a short number after a letters-only part
            if i == 0 or not re.fullmatch(r"\d{1,2}[^\W\d_]?", part):
                return False
    return True


def _normalise(text: str) -> str:
    """NFKC (full-width digits, superscripts → ASCII); format characters such as
    zero-width spaces become spaces so they cannot glue a number to a word."""
    text = unicodedata.normalize("NFKC", str(text))
    return "".join(" " if unicodedata.category(ch) == "Cf" else ch for ch in text)


def leaks(value) -> list[str]:
    """The kinds of leak still present in `value` (empty list: clean)."""
    text = _normalise(value)
    found = []
    if any(not (ch.isalpha() or ch.isdigit() or ch.isspace() or ch in _ALLOWED_PUNCT)
           for ch in text):
        found.append("character")
    if any(ch.isspace() and ch not in " " for ch in text):
        found.append("control")
    if any(not identifier_ok(w) for w in _DIGIT_WORD.findall(text)):
        found.append("number")
    if _LABEL_COLON.search(text):
        found.append("data-label")
    if ".." in text:
        found.append("path")
    return found


def check_text(value) -> str:
    """`value` unchanged if it may leave the machine; `LeakError` otherwise."""
    if not isinstance(value, str) or not re.search(r"[^\W\d_]{2,}", value):
        raise LeakError("refused: nothing left that can be sent")
    kinds = leaks(value)
    if kinds:
        raise LeakError(f"refused: still looks like data ({', '.join(kinds)})")
    return value


# -- the sanitiser --------------------------------------------------------------
_COMMENT = re.compile(r"(?<!\\)%\s*[A-Za-z_-]+\s*:.*$", re.M)
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/]\S*")
_URL = re.compile(r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)\S+", re.I)
_EMAIL = re.compile(r"\S+@\S+")
_DATA_REF = re.compile(r"\[[^\]]*:[^\]]*\]")
_LABELLED = re.compile(rf"\b(?:{_LABELS})\s*:\s*\S*", re.I)
_INPUT_CMD = re.compile(r"\\(?:input|include|includeonly|InputIfFileExists|subfile|import|"
                        r"subimport|lstinputlisting|verbatiminput)\*?\s*"
                        r"(?:(?:\[[^\]]*\]\s*)*(?:\{[^{}]*\}\s*){1,2}|\S+)")
_WILDCARD = re.compile(r"\{\{.*?\}\}", re.S)
_POINTER_CMD = re.compile(
    r"\\(?:ref|eqref|cref|Cref|autoref|pageref|nameref|label|input|include|includegraphics|"
    r"includeonly|url|href|path|verb|[a-zA-Z]*cite[a-zA-Z]*|bibliography\w*|footnote|"
    r"todo|data|source)\*?\s*(?:\[[^\]]*\]\s*)*(?:\{[^{}]*\}\s*){0,2}")
_ESCAPE = re.compile(r"\\([%&_#$])")
_COMMAND = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?|\\.")
_DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/\S*")
_FILE_EXT = (r"csv|tsv|xlsx?|json|jsonl|ya?ml|tex|bib|txt|md|py|ipynb|r|rds|rda|rdata|sav|dta|"
             r"sas7bdat|sas|xpt|por|parquet|feather|arrow|h5|hdf5|npy|npz|mat|pkl|pickle|nii|dcm|"
             r"gz|zip|tar|edf|bdf|db|sqlite3?|env|pdf|docx?|pptx?|cfg|ini|toml|log|bak|tmp|"
             r"png|jpe?g|tiff?|svg|sh|key|pem")
_FILE_TOKEN = re.compile(rf"\S*\.(?:{_FILE_EXT})\b\S*", re.I)
_STAT = re.compile(
    r"(?<![\w-])(?:p|n|N|r|R2|t|F|z|d|g|OR|HR|RR|AUC|SD|SE|SEM|IQR|CI|df|U|W|H|M|Mdn|k|"
    r"\u03b2|\u03c72|chi2|\u03c1|\u03c4|\u03b7p?2|\u03c92|\u03ba)\s*(?:\([^)]*\)\s*)?"
    r"(?:[<>=\u2264\u2265\u2248]|&lt;|&gt;)+")
_STAT_LABEL_BEFORE_NUMBER = re.compile(
    r"\b(?:OR|HR|RR|AUC|CI|SD|SE|SEM|IQR|Mdn)\s*:?\s*(?=[-\u2212\u2013+\u00b1]?[.,]?\d)")
_STAT_LABEL = re.compile(r"\b(?:CI|SD|SE|SEM|IQR)\b")
_NUM = r"[-\u2212\u2013+\u00b1]?(?:\d+(?:[.,]\d+)*|[.,]\d+)"
_UNITS = (r"mg|g|kg|\u03bcg|ug|ng|ml|mL|l|L|mm|cm|m|km|s|ms|sec|min|h|hr|hrs|hours?|days?|"
          r"weeks?|months?|years?|yrs?|y|Hz|kHz|\u03bcV|uV|mV|bpm|IU|mmHg|C|K|x|fold|"
          r"points?|pts?|participants?|patients?|subjects?|cases?|controls?")
_NUMBER = re.compile(
    rf"(?<![\w])(?<![^\W\d_]-){_NUM}\w*(?:-[^\W\d]\w*)*"
    rf"(?:\s*(?:[-\u2212\u2013\u2014]|to)\s*{_NUM}\w*)?"
    rf"(?:\s*(?:%|\u2030|\u00b0\s*C?|\u00d7)|\s*(?:percent|per\s+cent|(?:{_UNITS}))\b)?")
_LOOSE_NUMBER_UNIT_WORDS = {"patients", "participants", "subjects", "cases", "controls",
                            "patient", "participant", "subject", "case", "control"}
_SYMBOLS = re.compile(r"[<>=\u2264\u2265\u2248\u00b1%\u2030{}\[\]~$@|^`*#_\\/]")
_EMPTY_BRACKETS = re.compile(r"[(\[]\s*[^\w()\[\]]*\s*[)\]]")


def _path_token(match: re.Match) -> str:
    token = match.group(0)
    if re.fullmatch(r"[^\W\d_]+/[^\W\d_]+[.,;:!?)]*", token):     # and/or, PD/RBD
        return token.replace("/", " ")
    tail = re.search(r"[.,;:!?)]*$", token).group(0)  # type: ignore[union-attr]
    return " " + (tail[:1] if tail[:1] in ",;" else "")


def _numbers(text: str) -> str:
    def drop(match: re.Match) -> str:
        unit = match.group(0).split()[-1].lower() if " " in match.group(0) else ""
        # "43 patients": keep the noun, drop the count.
        if unit in _LOOSE_NUMBER_UNIT_WORDS:
            return " " + unit
        return " "
    return _NUMBER.sub(drop, text)


def _tidy(text: str) -> str:
    for _ in range(4):
        before = text
        text = _EMPTY_BRACKETS.sub(" ", text)
        text = re.sub(r"\s+([,.;:!?)\]])", r"\1", text)
        text = re.sub(r"([(\[])\s+", r"\1", text)
        text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
        text = re.sub(r"(?<![\w])[-\u2013\u2014](?![\w])", " ", text)
        text = re.sub(r"(?<![\w])-+(?=\w)", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        if text == before:
            break
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\s,;:.)\]-]+", "", text)
    return text.rstrip(" ,;:-(.").strip()


def sanitise(text) -> str:
    """`text` with every data-shaped part removed; `LeakError` if the result still
    looks like data or nothing meaningful is left. Deterministic."""
    out = _normalise(text)
    out = _COMMENT.sub(" ", out)
    out = _MATH.sub(" ", out)
    out = _WINDOWS_PATH.sub(" ", out)
    out = _URL.sub(" ", out)
    out = _EMAIL.sub(" ", out)
    out = _DATA_REF.sub(" ", out)
    out = _WILDCARD.sub(" ", out)
    out = _INPUT_CMD.sub(" ", out)
    out = _POINTER_CMD.sub(" ", out)
    out = _LABELLED.sub(" ", out)
    out = _ESCAPE.sub(r"\1", out)
    out = _DOI_IN_TEXT.sub(" ", out)
    out = re.sub(r"\S*[/\\]\S*", _path_token, out)
    out = _COMMAND.sub(" ", out)
    out = _FILE_TOKEN.sub(" ", out)
    out = out.replace("~", " ")
    out = _STAT.sub(" ", out)
    out = _STAT_LABEL_BEFORE_NUMBER.sub(" ", out)
    out = _numbers(out)
    out = _STAT_LABEL.sub(" ", out)
    out = _DIGIT_WORD.sub(lambda m: m.group(0) if identifier_ok(m.group(0)) else " ", out)
    out = _SYMBOLS.sub(" ", out)
    out = _tidy(out)
    return check_text(out)


# -- DOIs ------------------------------------------------------------------------
_PATH_ROOTS = {"home", "etc", "usr", "var", "tmp", "root", "mnt", "media", "opt", "srv",
               "users", "data", "config", "workspace", "proc", "sys", "dev", "private"}
_DOI_FILE_EXT = re.compile(rf"\.(?:{_FILE_EXT})(?![A-Za-z0-9])", re.I)


def safe_doi(value) -> str | None:
    """The normalised DOI if it may be sent, else None (not DOI-shaped, or path-shaped)."""
    if not isinstance(value, str) or not value.strip():
        return None
    if any(unicodedata.category(ch)[0] == "C" for ch in value) or any(c in value for c in "{}\\%"):
        return None                     # %: percent-encoded traversal (%2F, %2E)
    doi = doi_lib.normalise(value)
    if not doi_lib.is_valid(doi) or "//" in doi or "~" in doi:
        return None
    segments = doi.split("/")[1:]
    if any(not s or s.startswith(".") for s in segments):
        return None
    if any(s in _PATH_ROOTS for s in segments) or _DOI_FILE_EXT.search(doi):
        return None
    if doi[-1] in ".,;:?!" or len(segments) > 5:
        return None
    return doi


# -- novelty and key terms -------------------------------------------------------
_NOVELTY = re.compile(
    r"\b(?:the|a|our)\s+first\b(?!\s+(?:author|step|stage|visit|night|time\s+point|"
    r"session|round|part|phase))"
    r"|\bfor\s+the\s+first\s+time\b|\bnovel\b|\bunprecedented\b|\bfirst[- ]ever\b"
    r"|\bto\s+(?:the\s+best\s+of\s+)?our\s+knowledge\b"
    r"|\b(?:has|have|had)\s+(?:not|never)\s+(?:yet\s+|previously\s+|before\s+)?been\s+"
    r"(?:\w+\s+)?(?:reported|described|studied|investigated|shown|examined|explored|tested|"
    r"demonstrated|addressed)\b",
    re.I)


def is_novelty(text: str) -> bool:
    return bool(_NOVELTY.search(str(text)))


STOPWORDS = frozenset("""
a about above across after again against all almost also although always among an and
another any are around as at be because been before being below between both but by can
could did do does done during each either else et al etc even ever every for from further
had has have having here how however i if in into is it its itself just less like may
might more most much must near neither no nor not of off often on once one only or other
our ours out over own per rather same several shall she should since so some such than
that the their them then there these they this those though through thus to too toward
towards under until up upon us very via was we were what when where whether which while
who whom whose why will with within without would yet you your
study studies studied result results show shows showed shown found find finding findings
suggest suggests suggested report reports reported using used use based may well also
furthermore moreover therefore including include includes included respectively whereas
among compared versus vs significant significantly associated association
""".split())


def key_terms(text: str, limit: int = 12) -> list[str]:
    """Content words of `text` in first-seen order, lower-cased, de-duplicated."""
    terms: list[str] = []
    for token in re.findall(r"[^\W\d_][\w-]*", _normalise(text)):
        word = token.strip("-_").lower()
        if word in STOPWORDS or "_" in word:
            continue
        if len(word) < 3 and not any(ch.isdigit() for ch in word):
            continue
        if word not in terms:
            terms.append(word)
        if len(terms) >= limit:
            break
    return terms


def or_query(texts, limit: int = 12) -> str:
    """One OR-combined query over the key terms of several sentences."""
    terms: list[str] = []
    for text in texts:
        for term in key_terms(text, limit=limit):
            if term not in terms and len(terms) < limit:
                terms.append(term)
    return " OR ".join(terms)


# -- sections and priority -------------------------------------------------------
_CANONICAL = (("result", "results"), ("discussion", "discussion"), ("method", "methods"),
              ("introduction", "introduction"), ("background", "introduction"),
              ("conclusion", "conclusion"), ("abstract", "abstract"))
PRIORITY = {"results": 0, "discussion": 1}


def section_name(heading: str) -> str:
    low = _normalise(heading).lower()
    for needle, name in _CANONICAL:
        if needle in low:
            return name
    slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")
    return slug or "section"


def prioritise(items):
    """Results first, then Discussion, then everything else; stable otherwise."""
    return sorted(items, key=lambda c: PRIORITY.get(c.section, 2))


# -- extraction ------------------------------------------------------------------
@dataclass(frozen=True)
class Claim:
    id: str
    section: str
    text: str                    # sanitised; may leave the machine (as key terms)
    dois: tuple[str, ...]        # safe DOIs it cites, citation order
    keys: tuple[str, ...]        # bib keys it cites (local only)
    file: str                    # manuscript-relative path (local only)
    line: int
    novelty: bool


@dataclass
class Extraction:
    claims: list[Claim] = field(default_factory=list)
    dois: list[str] = field(default_factory=list)                 # every safe bib DOI
    doi_usage: dict[str, list[str]] = field(default_factory=dict)  # local only
    unresolved: list[dict] = field(default_factory=list)          # {key, reason}
    invalid_dois: list[dict] = field(default_factory=list)        # {key, reason}
    refused: list[dict] = field(default_factory=list)             # {file, line, reason}

    @property
    def citing(self) -> list[Claim]:
        return [c for c in self.claims if c.dois]

    @property
    def novelty(self) -> list[Claim]:
        return [c for c in self.claims if c.novelty]


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):         # RuntimeError: symlink loop
        return False


def _confined_tex(entry: Path, root: Path) -> list[Path]:
    """`entry` and its `\\input` children, depth-first, once each — the traversal of
    `nblm.claims.tex_files`, but a child is checked to be a regular file inside
    `root` *before* it is read (no reads outside the draft, no FIFOs)."""
    nb = parser()
    seen: list[Path] = []
    pending = [entry]
    while pending:
        current = pending.pop(0)
        if current in seen or not _inside(current, root):
            continue
        seen.append(current)
        body = nb.strip_comments(current.read_text(encoding="utf-8", errors="replace"))
        for name in nb.INPUT_RE.findall(body):
            child = (current.parent / name.strip()).with_suffix(".tex")
            if child not in seen:
                pending.append(child)
    return seen


def sources(ws: Path) -> list[tuple[Path, Path]]:
    """(file, label root) for every manuscript file to read, in a fixed order:
    each draft under `manuscript/drafts/<agent>/` (agents sorted), else the merged
    manuscript (`main.tex` and its inputs, or the top-level `.tex` files). A
    paper-review run always reads the merged manuscript."""
    man = Path(ws) / MANUSCRIPT_DIR
    drafts = Path(ws) / DRAFTS_DIR
    roots: list[Path] = []
    if drafts.is_dir() and not reviews_manuscript(ws):
        roots = sorted(p for p in drafts.iterdir() if p.is_dir() and not p.is_symlink())
    if not roots and man.is_dir():
        roots = [man]
    out: list[tuple[Path, Path]] = []
    for root in roots:
        main = root / "main.tex"
        entries = [main] if main.is_file() else sorted(root.glob("*.tex"))
        seen: list[Path] = []
        for entry in entries:
            for path in _confined_tex(entry, root):
                if path not in seen:
                    seen.append(path)
        out.extend((path, root) for path in seen)
    return out


_HEADING = re.compile(r"\\section\*?\s*(?:\[[^\]]*\]\s*)?\{([^}]*)\}|\\begin\s*\{(abstract)\}"
                      r"|\\end\s*\{(abstract)\}")


def _sections_by_line(body: str, default: str) -> list[str]:
    current, out = default, []
    for line in body.split("\n"):
        match = _HEADING.search(line)
        if match:
            current = (default if match.group(3)
                       else section_name(match.group(1) or match.group(2)))
        out.append(current)
    return out


def extract(files: list[tuple[Path, Path]], bib: Path | None, label_root: Path) -> Extraction:
    nb = parser()
    bib_text = bib.read_text(encoding="utf-8", errors="replace") if bib and bib.is_file() else ""
    raw_dois = nb.parse_bib(bib_text)
    known_keys = {m.group(2) for m in nb.ENTRY_RE.finditer(bib_text)}
    ex = Extraction()
    key_doi: dict[str, str] = {}
    for key, raw in raw_dois.items():
        doi = safe_doi(raw)
        if doi is None:
            ex.invalid_dois.append({"key": key, "reason": "not a safe DOI"})
            continue
        key_doi[key] = doi
        if doi not in ex.dois:
            ex.dois.append(doi)

    unresolved: dict[str, str] = {}
    merged: dict[str, dict] = {}
    for path, _root in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        no_comments = nb.strip_comments(raw)
        try:
            label = path.relative_to(label_root).as_posix()
        except ValueError:
            label = path.name
        stem = path.stem
        sections = _sections_by_line(no_comments, section_name(stem) if stem != "main" else "body")
        body = nb.blank_structural(no_comments)
        offsets = [i for i, ch in enumerate(body) if ch == "\n"]
        for offset, sentence in nb.split_sentences(body):
            keys = [k.strip() for group in nb.CITE_RE.findall(sentence)
                    for k in group.split(",") if k.strip()]
            cleaned = nb.clean_text(sentence)
            novelty = is_novelty(cleaned)
            if not keys and not novelty:
                continue
            line = nb._line_of(offsets, offset) + 1
            try:
                text = sanitise(cleaned)
            except LeakError as exc:
                ex.refused.append({"file": label, "line": line, "reason": str(exc)})
                continue
            dois: list[str] = []
            for key in dict.fromkeys(keys):
                if key in key_doi:
                    dois.append(key_doi[key])
                elif key not in raw_dois:
                    unresolved[key] = ("bib entry has no doi field" if key in known_keys
                                       else "key not in bib")
            entry = merged.get(text)
            if entry is None:
                merged[text] = {"section": sections[min(line - 1, len(sections) - 1)],
                                "dois": list(dict.fromkeys(dois)), "keys": list(dict.fromkeys(keys)),
                                "file": label, "line": line, "novelty": novelty}
            else:
                entry["dois"] += [d for d in dois if d not in entry["dois"]]
                entry["keys"] += [k for k in keys if k not in entry["keys"]]
                entry["novelty"] = entry["novelty"] or novelty

    for n, (text, e) in enumerate(merged.items(), 1):
        claim = Claim(id=f"C{n}", section=e["section"], text=text, dois=tuple(e["dois"]),
                      keys=tuple(e["keys"]), file=e["file"], line=e["line"], novelty=e["novelty"])
        ex.claims.append(claim)
        for doi in claim.dois:
            usage = ex.doi_usage.setdefault(doi, [])
            if text not in usage:
                usage.append(text)
    ex.doi_usage = {d: ex.doi_usage[d] for d in sorted(ex.doi_usage)}
    ex.unresolved = [{"key": k, "reason": v} for k, v in sorted(unresolved.items())]
    return ex


def extract_run(ws: Path) -> Extraction:
    """Claims of every draft of the run at `ws` (union), against `manuscript/references.bib`."""
    man = Path(ws) / MANUSCRIPT_DIR
    return extract(sources(ws), man / BIB_NAME, man)
