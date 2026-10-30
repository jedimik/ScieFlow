"""`scieflow manuscript render` (M-F 5.11): write manuscript/main.tex from metadata.yml, the profile and the
section files, using the paper-class registry. Deterministic: the same inputs give the same bytes.

Refusals (RenderError) name a rule and never print manuscript text. Nothing here runs TeX.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from scieflow.research.journal import profile, state
from scieflow.research.manuscript import check as check_mod
from scieflow.research.manuscript import metadata, tex, templates

MARKER_PREFIX = "% scieflow-render: "
MARKER_RE = re.compile(r"^% scieflow-render: class=(\S+) version=1 sha256=([0-9a-f]{64})$")
PLACEHOLDER_RE = re.compile(r"%%([A-Z]+)%%")
VERSION = 1
MAIN = "main.tex"
ABSTRACT = "sections/abstract.tex"
BIB = "references.bib"
IMRAD = ("introduction", "methods", "results", "discussion")
STEM = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STYLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
OPTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class RenderError(Exception):
    """A refusal: `rule` names it, `message` says what to do."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class RenderResult:
    path: Path
    sha256: str
    cls: str
    written: bool
    backup: Path | None

    def to_dict(self) -> dict:
        return {"class": self.cls, "path": str(self.path), "sha256": self.sha256,
                "written": self.written, "backup": None if self.backup is None else str(self.backup)}


def escape(text) -> str:
    """LaTeX-escape a metadata string in one pass, so an escape is never escaped twice."""
    return "".join(_ESCAPES.get(ch, ch) for ch in str(text))


def _body_sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _valid_render(text: str) -> bool:
    first, _, rest = text.partition("\n")
    match = MARKER_RE.match(first)
    return bool(match) and match.group(1) in templates.CLASSES and match.group(2) == _body_sha(rest)


def _exists(folder: Path, rel: str) -> bool:
    path = folder / rel
    if path.is_symlink():
        raise RenderError("symlink", f"{rel} is a symbolic link; refused")
    return path.is_file()


def _profile(root: Path, folder: Path, meta: dict):
    """(profile dict or None) per M-F 5.6: the run's pinned copy, else config/journals/<slug>.yml."""
    pinned = folder.parent / "journal" / "profile.yml"
    slug = meta.get("journal")
    if pinned.is_file() and not pinned.is_symlink():
        path = pinned
    elif isinstance(slug, str) and check_mod.SLUG.match(slug):
        yml = root / "config" / "journals" / f"{slug}.yml"
        md = root / "config" / "journals" / f"{slug}.md"
        if yml.is_file():
            path = yml
        elif md.is_file():
            raise RenderError("profile-legacy", f"{slug} has only a hand-written page; adopt a structured profile")
        else:
            return None
    else:
        return None
    try:
        prof = profile.load_profile(path)
        issues = profile.validate(prof, root=root, run_dir=check_mod._profile_run_dir(root, prof))
    except profile.ProfileError as exc:
        raise RenderError("profile-invalid", str(exc)) from exc
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise RenderError("profile-invalid", f"{errors[0].rule} at {errors[0].path}")
    return prof


def _article_type(prof, meta: dict, article_type: str | None):
    if prof is None:
        return None
    types = prof.get("article_types") or []
    wanted = article_type or meta.get("article_type")
    if wanted is None and len(types) == 1:
        wanted = types[0].get("id")
    return next((t for t in types if t.get("id") == wanted), None)


def _section_stems(folder: Path, meta: dict, atype) -> list:
    if meta.get("sections") is not None:
        stems = meta["sections"]
        if not isinstance(stems, list) or not all(isinstance(s, str) and STEM.match(s) for s in stems):
            raise RenderError("section-stem", "metadata sections must be lowercase file stems")
        for stem in stems:
            if not _exists(folder, f"sections/{stem}.tex"):
                raise RenderError("section-file-missing", f"sections/{stem}.tex is listed in metadata.yml")
        return stems
    if atype is not None:
        stems = []
        for section in atype["structure"]["sections"]:
            stem = state.slugify(section["title"])
            if not STEM.match(stem):
                raise RenderError("section-stem", f"the profile section {stem!r} has no usable file name")
            if _exists(folder, f"sections/{stem}.tex"):
                stems.append(stem)
            elif section.get("required") is True:
                raise RenderError("section-file-missing", f"sections/{stem}.tex is required by the profile")
        return stems
    return [stem for stem in IMRAD if _exists(folder, f"sections/{stem}.tex")]


def _unique_affiliations(authors: list) -> list:
    names: list = []
    for author in authors:
        for affiliation in author.get("affiliations") or []:
            name = affiliation.get("name") if isinstance(affiliation, dict) else None
            if name and name not in names:
                names.append(name)
    return names


def _author_names(author: dict) -> list:
    return [a["name"] for a in author.get("affiliations") or []
            if isinstance(a, dict) and isinstance(a.get("name"), str)]


def _authors(cls: str, authors: list) -> str:
    affiliations = _unique_affiliations(authors)
    if cls == "article":
        parts = []
        for author in authors:
            part = escape(author["name"])
            if author.get("corresponding") is True:
                email = author.get("email")
                note = f"Corresponding author: {email}" if isinstance(email, str) else "Corresponding author"
                part += "\\thanks{" + escape(note) + "}"
            names = _author_names(author)
            if names:
                part += "\\\\{\\small " + escape("; ".join(names)) + "}"
            parts.append(part)
        return "\\author{" + " \\and ".join(parts) + "}"
    if cls == "elsarticle":
        lines = []
        for author in authors:
            indexes = [str(affiliations.index(n) + 1) for n in _author_names(author) if n in affiliations]
            head = f"\\author[{','.join(indexes)}]" if indexes else "\\author"
            lines.append(f"{head}{{{escape(author['name'])}}}")
            if author.get("corresponding") is True:
                lines.append("\\cortext[cor1]{Corresponding author}")
                if isinstance(author.get("email"), str):
                    lines.append("\\ead{" + escape(author["email"]) + "}")
        for index, name in enumerate(affiliations, start=1):
            lines.append(f"\\affiliation[{index}]{{organization={{{escape(name)}}}}}")
        return "\n".join(lines)
    if cls == "IEEEtran":
        parts = []
        for author in authors:
            part = "\\IEEEauthorblockN{" + escape(author["name"]) + "}"
            names = _author_names(author)
            if names:
                part += "\\IEEEauthorblockA{" + "\\\\".join(escape(n) for n in names) + "}"
            parts.append(part)
        return "\\author{" + " \\and ".join(parts) + "}"
    names = " \\and ".join(escape(author["name"]) for author in authors)
    places = " \\and ".join(escape(name) for name in affiliations)
    return f"\\author{{{names}}}\n\\institute{{{places}}}"


def _keywords(cls: str, keywords: list) -> str:
    if not keywords:
        return ""
    items = [escape(k) for k in keywords]
    if cls == "article":
        return "\\noindent\\textbf{Keywords:} " + ", ".join(items)
    if cls == "elsarticle":
        return "\\begin{keyword}\n" + " \\sep ".join(items) + "\n\\end{keyword}"
    if cls == "IEEEtran":
        return "\\begin{IEEEkeywords}\n" + ", ".join(items) + "\n\\end{IEEEkeywords}"
    return "\\keywords{" + " \\and ".join(items) + "}"


def _fill(template: str, values: dict) -> str:
    """Substitute every placeholder in one pass; an empty line-value drops its whole line."""
    if not values["CLASSOPTIONS"]:
        template = template.replace("[%%CLASSOPTIONS%%]", "")
    for name in sorted(templates.PLACEHOLDERS):
        if not values[name]:
            template = re.sub(rf"(?m)^%%{name}%%\n", "", template)

    def replace(match):
        name = match.group(1)
        if name not in values:
            raise RenderError("template-placeholder", f"the template has an unknown placeholder {name}")
        return values[name]

    return PLACEHOLDER_RE.sub(replace, template)


def _check_options(options) -> list:
    if options is None:
        return []
    if not isinstance(options, list) or not all(isinstance(o, str) and OPTION.match(o) for o in options):
        raise RenderError("class-option", "a LaTeX class option in the profile is not a plain word")
    return options


def render_run(root, run_dir, *, cls: str | None = None, article_type: str | None = None,
               force: bool = False) -> RenderResult:
    root = Path(root)
    run = Path(run_dir)
    folder = run / "manuscript"
    if folder.is_symlink() or not folder.is_dir():
        raise RenderError("manuscript-missing", f"no manuscript folder in {run.name}")
    try:
        meta, problems = metadata.load(folder, root)
    except (tex.TexError, ValueError) as exc:
        raise RenderError("metadata", str(exc)) from exc
    if meta is None:
        raise RenderError("metadata-missing", "manuscript/metadata.yml is required by render")
    errors = [p for p in problems if p.level == "error"]
    if errors:
        raise RenderError(errors[0].rule, f"metadata.yml: {errors[0].rule} at {errors[0].path}")

    prof = _profile(root, folder, meta)
    atype = _article_type(prof, meta, article_type)
    if cls is None:
        cls = ((prof or {}).get("templates") or {}).get("latex_class") or "article"
    if cls == "other":
        raise RenderError("class-other", "the profile names another class: render writes article, "
                                         "elsarticle, IEEEtran or svjour3 only")
    if cls not in templates.CLASSES:
        raise RenderError("class-unknown", f"unknown class {cls!r}")
    entry = templates.load_registry()[cls]

    if not _exists(folder, ABSTRACT):
        raise RenderError("abstract-missing", "sections/abstract.tex is required")
    if not _exists(folder, BIB):
        raise RenderError("bib-missing", "references.bib is required")
    stems = _section_stems(folder, meta, atype)

    style = ((prof or {}).get("references") or {}).get("bibtex_style") or entry["bibtex_style"]
    if not isinstance(style, str) or not STYLE.match(style):
        raise RenderError("bib-style", "the bibliography style is not a plain style name")
    options = _check_options(((prof or {}).get("templates") or {}).get("latex_options"))

    authors = meta.get("authors") or []
    values = {
        "CLASSOPTIONS": ",".join(options),
        "TITLE": escape(meta["title"]),
        "AUTHORS": _authors(cls, authors),
        "ABSTRACT": "\\input{sections/abstract}",
        "KEYWORDS": _keywords(cls, meta.get("keywords") or []),
        "SECTIONS": "\n".join(f"\\input{{sections/{stem}}}" for stem in stems),
        "BIBSTYLE": style,
        "NATBIB": "\\usepackage{natbib}" if entry["natbib"] else "",
    }
    body = _fill(templates.read_template(entry["template"]), values)
    text = f"{MARKER_PREFIX}class={cls} version={VERSION} sha256={_body_sha(body)}\n{body}"

    # Every refusal comes before the first write (the preamble included).
    path = folder / MAIN
    if path.is_symlink():
        raise RenderError("symlink", "main.tex is a symbolic link; refused")
    old_bytes = path.read_bytes() if path.is_file() else None
    if old_bytes is not None and old_bytes.decode("utf-8", errors="replace") != text:
        if not _valid_render(old_bytes.decode("utf-8", errors="replace")) and not force:
            raise RenderError("main-edited", "main.tex was not written by render or was edited by hand; "
                                             "use --force to keep a backup and replace it")

    if cls == "article" and not _exists(folder, "preamble.tex"):
        state.atomic_write(folder / "preamble.tex", (templates.TEMPLATE_DIR / "preamble.tex").read_bytes())

    if old_bytes is not None and old_bytes.decode("utf-8", errors="replace") == text:
        return RenderResult(path, _body_sha(body), cls, False, None)
    backup = None
    if old_bytes is not None and force:
        backup = folder / f"main.tex.bak-{hashlib.sha256(old_bytes).hexdigest()[:12]}"
        state.atomic_write(backup, old_bytes)
    state.atomic_write(path, text.encode("utf-8"))
    return RenderResult(path, _body_sha(body), cls, True, backup)
