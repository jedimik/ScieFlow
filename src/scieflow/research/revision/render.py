"""The response to reviewers as a LaTeX letter (M-G 5.12): the vendored template's head, then the comments.

The letter is written only when coverage is ok. Its preamble is the vendored template up to and including the
\\newpage after \\tableofcontents, filled by letters.response_text (the title). Then one section per block that
has comments (Editor or Reviewer n), one subsection per comment: the comment quote, the response and the change
(or the reason for no change), every value through render.escape. The vendored template is read, never modified.
manuscript/letters/ is outside the source-tree hash, so the letter does not stale a build.
"""

from __future__ import annotations

from pathlib import Path

from scieflow.research.journal import state
from scieflow.research.manuscript import letters as letters_mod
from scieflow.research.manuscript import metadata, tex
from scieflow.research.manuscript import render as manuscript_render
from scieflow.research.revision import coverage as coverage_mod
from scieflow.research.revision import letter as letter_mod
from scieflow.research.revision import respond as respond_mod
from scieflow.research.revision.letter import LetterError

CUT = "\\tableofcontents\n\\newpage\n"


def _metadata(root: Path, folder: Path) -> dict:
    if folder.is_symlink() or not folder.is_dir():
        raise LetterError("manuscript-missing", f"no manuscript folder in {folder.parent.name}")
    try:
        meta, problems = metadata.load(folder, root)
    except (tex.TexError, ValueError) as exc:
        raise LetterError("metadata", str(exc)) from exc
    if meta is None:
        raise LetterError("metadata-missing", "manuscript/metadata.yml is required")
    errors = [p for p in problems if p.level == "error"]
    if errors:
        raise LetterError("metadata", f"metadata.yml: {errors[0].rule} at {errors[0].path}")
    return meta


def _paragraphs(text: str) -> str:
    """The text as LaTeX paragraphs: each blank-line-separated part escaped; the parts joined by a medskip.

    A blank line inside a macro argument would be a TeX paragraph end that the argument cannot hold, so the
    paragraph break is written as \\medskip\\noindent instead. Single-paragraph text is unchanged.
    """
    esc = manuscript_render.escape
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    return "\n\\medskip\\noindent ".join(esc(part) for part in parts)


def _section_title(block_id: str) -> str:
    return "Editor" if block_id.startswith("E") else f"Reviewer {block_id[1:]}"


def letter_text(root, run, round_: int) -> str:
    root, run = Path(root), Path(run)
    document = coverage_mod.evaluate(run, round_)
    if not document["ok"]:
        count = sum(1 for row in document["problems"] if row["level"] == "error")
        raise LetterError("coverage-not-ok", f"round {round_} coverage is not ok ({count} problems): run "
                                             "revision coverage and answer every comment first")
    head = letters_mod.response_text(_metadata(root, run / "manuscript"))
    cut = head.find(CUT)
    if cut < 0:
        raise LetterError("template", "the vendored response template has no table of contents break")
    head = head[: cut + len(CUT)]

    record = letter_mod.load(run, round_)
    adopted = respond_mod.load_adopted(run, round_)
    blocks = [block["id"] for block in record["blocks"]]
    reviews = respond_mod.load_reviews(run, round_, blocks)
    lines_by_comment: dict = {}
    for comment, lines in coverage_mod.sections(respond_mod.read_response(run, round_)):
        lines_by_comment.setdefault(comment, lines)

    pieces: list = []
    for block_id in blocks:
        comments = [cid for cid in adopted["comments"] if cid.split(".", 1)[0] == block_id]
        if not comments:
            continue
        pieces.append(f"\\section{{{_section_title(block_id)}}}\n")
        for comment in comments:
            kind, entry = respond_mod.comment_entry(reviews, comment)
            quote = entry["problem"] if kind == "major" else entry["comment"]
            found = coverage_mod.fields(lines_by_comment[comment])
            if coverage_mod.filled(found["change"]):
                label, text = "Change", found["change"]
            else:
                label, text = "No change", found["no_change"]
            esc = manuscript_render.escape
            pieces.append(
                f"\\subsection{{Comment {esc(comment)}}}\n"
                f"\\reviewercomment{{{_paragraphs(quote)}}}\n\n"
                f"\\response{{{_paragraphs(found['response'])}}}\n\n"
                f"\\noindent\\textbf{{{label}:}} {_paragraphs(text)}\n"
            )
    return head + "\n" + "\n".join(pieces) + "\n\\end{document}\n"


def write(root, run, round_: int, *, force: bool = False) -> Path:
    """Write manuscript/letters/response-to-reviewers-r<N>.tex; an existing letter is kept unless forced."""
    run = Path(run)
    letter_mod.refuse_links(run, round_, "adopted.json", "response.md", "reviews")
    folder = run / "manuscript"
    target_dir = folder / "letters"
    if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
        raise LetterError("symlink", "manuscript/letters is a symbolic link or not a folder; refused")
    target = target_dir / f"response-to-reviewers-r{round_}.tex"
    if target.is_symlink():
        raise LetterError("symlink", f"{target.name} is a symbolic link; refused")
    if target.exists() and not force:
        raise LetterError("exists", f"{target.name} exists; use --force to replace it")
    text = letter_text(root, run, round_)
    state.atomic_write(target, text.encode("utf-8"))
    return target
