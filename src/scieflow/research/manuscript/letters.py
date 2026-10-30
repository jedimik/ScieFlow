"""Starter letters (M-F 5.13): the cover letter and the response to reviewers, from the vendored templates.

`write_letter` writes manuscript/letters/ (never part of the source tree, so a letter does not stale a build).
`cover_letter_text` is also the fallback of the package when the run has no letter of its own. Every fill is
LaTeX-escaped; a value that is not known (no corresponding author, no profile name) keeps its placeholder, and
`placeholders` lists what is still left so the submission checklist can say so.
"""

from __future__ import annotations

import re
from pathlib import Path

from scieflow._vendor import researcher_core as _vendored
from scieflow.research.journal import state
from scieflow.research.manuscript import metadata, render, tex

TEMPLATE_DIR = Path(_vendored.__file__).resolve().parent / "templates"
LETTERS = "letters"
COVER = "cover-letter.tex"
RESPONSE = "response-to-reviewers.tex"
KINDS = {"cover": COVER, "response": RESPONSE}
MARKERS = ("JOURNAL NAME", "CORRESPONDING AUTHOR NAME", "TITLE", "EDITOR NAME",
           "Department, University", "email@example.com", "Name, Affiliation")
BRACKET = re.compile(r"\[[^\]\n]*\]")


class LetterError(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def corresponding(meta: dict):
    """The one corresponding author (a mapping), or None when there is none or more than one."""
    chosen = [a for a in (meta.get("authors") or []) if isinstance(a, dict) and a.get("corresponding") is True]
    return chosen[0] if len(chosen) == 1 else None


def _text(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _first_affiliation(author: dict) -> str | None:
    for item in author.get("affiliations") or []:
        if isinstance(item, dict) and _text(item.get("name")):
            return item["name"]
    return None


def cover_letter_text(meta: dict, journal_name: str | None = None) -> str:
    """The vendored cover letter with the known values filled in and escaped."""
    text = (TEMPLATE_DIR / COVER).read_text(encoding="utf-8")
    author = corresponding(meta)
    if _text(journal_name):
        text = text.replace("JOURNAL NAME", render.escape(journal_name))
    if _text(meta.get("title")):
        text = text.replace("``TITLE''", f"``{render.escape(meta['title'])}''")
    if author is not None:
        if _text(author.get("name")):
            text = text.replace("CORRESPONDING AUTHOR NAME", render.escape(author["name"]))
        if _text(author.get("email")):
            text = text.replace("email@example.com", render.escape(author["email"]))
        affiliation = _first_affiliation(author)
        if affiliation:
            text = text.replace("Department, University", render.escape(affiliation))
    letters = meta.get("letters") or {}
    if isinstance(letters, dict) and _text(letters.get("editor_name")):
        text = text.replace("EDITOR NAME", render.escape(letters["editor_name"]))
    return text


def response_text(meta: dict) -> str:
    """The vendored response to reviewers with the title filled in and escaped."""
    text = (TEMPLATE_DIR / RESPONSE).read_text(encoding="utf-8")
    if _text(meta.get("title")):
        text = text.replace("TITLE", render.escape(meta["title"]))
    return text


def placeholders(text: str) -> list:
    """Placeholder tokens still present in a letter: the fixed markers and bracketed prompts in the body."""
    live = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("%"))
    found = [marker for marker in MARKERS if marker in live]
    body = live.split("\\begin{document}", 1)[-1]
    found += [tex.excerpt(match.group(0)) for match in BRACKET.finditer(body)]
    return list(dict.fromkeys(found))


def _journal_name(root: Path, folder: Path, meta: dict):
    try:
        prof = render._profile(root, folder, meta)
    except render.RenderError:
        return None
    if prof is None:
        return None
    return _text((prof.get("identity") or {}).get("name"))


def write_letter(root, run_dir, kind: str, *, force: bool = False) -> Path:
    if kind not in KINDS:
        raise LetterError("kind", f"unknown letter kind {kind!r}: cover or response")
    root = Path(root)
    run = Path(run_dir)
    folder = run / "manuscript"
    if folder.is_symlink() or not folder.is_dir():
        raise LetterError("manuscript-missing", f"no manuscript folder in {run.name}")
    try:
        meta, problems = metadata.load(folder, root)
    except (tex.TexError, ValueError) as exc:
        raise LetterError("metadata", str(exc)) from exc
    if meta is None:
        raise LetterError("metadata-missing", "manuscript/metadata.yml is required")
    errors = [p for p in problems if p.level == "error"]
    if errors:
        raise LetterError("metadata", f"metadata.yml: {errors[0].rule} at {errors[0].path}")

    target_dir = folder / LETTERS
    if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
        raise LetterError("symlink", "manuscript/letters is a symbolic link or not a folder; refused")
    target = target_dir / KINDS[kind]
    if target.is_symlink():
        raise LetterError("symlink", f"{KINDS[kind]} is a symbolic link; refused")
    if target.exists() and not force:
        raise LetterError("exists", f"{KINDS[kind]} exists; use --force to replace it")

    if kind == "cover":
        text = cover_letter_text(meta, _journal_name(root, folder, meta))
    else:
        text = response_text(meta)
    state.atomic_write(target, text.encode("utf-8"))
    return target
