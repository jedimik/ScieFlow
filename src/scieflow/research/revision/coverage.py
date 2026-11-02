"""Coverage of the response starter (M-G 5.12): every adopted comment answered, with a change or a reason.

`evaluate` checks response.md against adopted.json without writing anything; `write` stores the document as
coverage.json and emits one event with counts. Errors: missing, duplicate-section, no-response,
no-change-line, both-change-lines, no-change-reason, change-path, change-not-found. Warning: unknown-comment.
Messages name ids and rules, never response or letter text (M-G 5.16).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scieflow.core import events
from scieflow.research.journal import state
from scieflow.research.manuscript import points as points_mod
from scieflow.research.manuscript import tex
from scieflow.research.revision import letter as letter_mod
from scieflow.research.revision import respond as respond_mod
from scieflow.research.revision.letter import LetterError

VERSION = 1
MIN_REASON = 10
HEADING_TOKEN_RE = re.compile(r"^#{1,6}\s*(\S+)")
COMMENT_RE = respond_mod.COMMENT_RE
LABELS = (
    ("response", re.compile(r"^Response:(.*)$")),
    ("change", re.compile(r"^Change:(.*)$")),
    ("no_change", re.compile(r"^No change:(.*)$")),
)
OTHER_LABELS = ("Agent's reading:",)
PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]")
PATH_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:tex|bib))(?=$|[\s:;,])")
MESSAGES = {
    "missing": "no section in response.md for this adopted comment",
    "duplicate-section": "more than one section in response.md for this comment",
    "no-response": "the Response line is empty or still a placeholder",
    "no-change-line": "neither a Change line nor a No change line is filled in",
    "both-change-lines": "both a Change line and a No change line are filled in; keep one",
    "no-change-reason": f"the No change reason is shorter than {MIN_REASON} characters",
    "change-path": "the Change line must begin with a manuscript file path ending in .tex or .bib",
    "change-not-found": "the named file is unchanged from the submitted point or absent from both",
    "point-unknown": "the submitted point for this round is not in the run; a change cannot be verified",
    "point-tampered": "the submitted point does not match its point.json; a change cannot be verified",
    "unknown-comment": "a section names a comment that was not adopted",
}


def _document_json(record: dict) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sections(text: str) -> list:
    """(heading token, lines) for each heading whose first token has the shape of a comment id, in order.

    The token is compared exactly with the adopted ids by `evaluate` (M-G 5.12, clarification C2): `E.M1xyz`
    and `R1.m1:` are not a comment id and their text is ordinary text.
    """
    found: list = []
    current = None
    for line in text.split("\n"):
        if line.startswith("#"):
            match = HEADING_TOKEN_RE.match(line)
            if match and COMMENT_RE.fullmatch(match.group(1)):
                current = (match.group(1), [])
                found.append(current)
            else:
                current = None
            continue
        if current is not None:
            current[1].append(line)
    return found


def fields(lines: list) -> dict:
    """The labelled fields of one section: response, change, no_change (text, or None when no line).

    A field runs until the next label, a quoted line, an `Agent's reading:` line or a heading. A blank line inside
    a field separates paragraphs ("\\n\\n" in the text); the paragraphs are kept, never dropped.
    """
    out: dict = {"response": None, "change": None, "no_change": None}
    current = None
    paragraph_break = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if current is not None:
                paragraph_break = True
            continue
        if line.startswith(">") or line.startswith(OTHER_LABELS):
            current = None
            paragraph_break = False
            continue
        for key, pattern in LABELS:
            match = pattern.match(line)
            if match:
                out[key] = match.group(1).strip()
                current = key
                paragraph_break = False
                break
        else:
            if current is not None:
                out[current] = _join(out[current], line.strip(), paragraph_break)
                paragraph_break = False
    return out


def _join(text, line: str, paragraph: bool) -> str:
    if not text:
        return line
    return f"{text}\n\n{line}" if paragraph else f"{text} {line}"


def filled(text) -> bool:
    if text is None or not text.strip():
        return False
    return PLACEHOLDER_RE.fullmatch(text.strip()) is None


def _current_bytes(path: Path):
    if not path.is_file():
        return None
    if path.stat().st_size > tex.MAX_BYTES:
        return None
    return path.read_bytes()


def _verified_point(run: Path, label: str) -> tuple:
    """(rule, folder): rule is None when point `label` is intact, else point-unknown or point-tampered.

    The pinned files must match point.json exactly, as diff.py checks them (M-G 5.9, 5.16): a change cannot be
    verified against a point that is missing or altered, so it is refused, never accepted.
    """
    try:
        record = points_mod.read_point(run, label)
        folder = points_mod.point_dir(run, label) / "files"
        if not folder.is_dir():
            return "point-tampered", None
        digests = {rel: hashlib.sha256(path.read_bytes()).hexdigest()
                   for rel, path in points_mod.source_files(folder).items()}
    except points_mod.PointError as exc:
        if exc.rule == "symlink":
            raise LetterError("symlink", exc.message) from exc
        return ("point-unknown" if exc.rule == "point-unknown" else "point-tampered"), None
    if not isinstance(record.get("files"), dict) or digests != record["files"]:
        return "point-tampered", None
    return None, folder


def _point_bytes(run: Path, label: str, rel: str) -> tuple:
    """(rule, bytes): the verified pinned copy of `rel` in point `label`, None when it has no such file."""
    rule, folder = _verified_point(run, label)
    if rule:
        return rule, None
    cursor = folder
    for part in Path(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise LetterError("symlink", f"{rel} crosses a symbolic link; refused")
    return None, (cursor.read_bytes() if cursor.is_file() else None)


def change_rule(run: Path, round_: int, text: str):
    """None when the Change line names a manuscript file that differs from the submitted point, else a rule."""
    match = PATH_RE.match(text.strip())
    if not match:
        return "change-path"
    rel = match.group(1)
    if any(part in ("", "..") for part in rel.split("/")):
        return "change-path"
    try:
        path = tex._inside(run / "manuscript", rel)
    except tex.TexError:
        return "change-path"
    current = _current_bytes(path)
    if current is None and path.is_file():
        return "change-path"
    rule, point = _point_bytes(run, f"submitted-r{round_}", rel)
    if rule:
        return rule
    if current is None and point is None:
        return "change-not-found"
    if current is not None and current == point:
        return "change-not-found"
    return None


def _problem(comment: str, rule: str, level: str = "error") -> dict:
    return {"comment": comment, "level": level, "rule": rule, "message": MESSAGES[rule]}


def _check(run: Path, round_: int, comment: str, lines: list) -> list:
    found = fields(lines)
    rules = []
    if not filled(found["response"]):
        rules.append("no-response")
    has_change = filled(found["change"])
    has_no_change = filled(found["no_change"])
    if has_change and has_no_change:
        rules.append("both-change-lines")
    elif not has_change and not has_no_change:
        rules.append("no-change-line")
    elif has_change:
        rule = change_rule(run, round_, found["change"])
        if rule:
            rules.append(rule)
    elif len(found["no_change"].strip()) < MIN_REASON:
        rules.append("no-change-reason")
    return [_problem(comment, rule) for rule in rules]


def evaluate(run, round_: int) -> dict:
    """The coverage document of a round, computed from disk and not written."""
    run = Path(run)
    adopted = respond_mod.load_adopted(run, round_)
    text = respond_mod.read_response(run, round_)
    comments = list(adopted["comments"])
    found: dict = {}
    duplicates = set()
    for comment, lines in sections(text):
        if comment in found:
            duplicates.add(comment)
        else:
            found[comment] = lines
    problems: list = []
    for comment in comments:
        if comment not in found:
            problems.append(_problem(comment, "missing"))
            continue
        if comment in duplicates:
            problems.append(_problem(comment, "duplicate-section"))
        problems.extend(_check(run, round_, comment, found[comment]))
    for comment in sorted(set(found) - set(comments)):
        problems.append(_problem(comment, "unknown-comment", level="warning"))
    errors = [row for row in problems if row["level"] == "error"]
    return {"version": VERSION, "round": round_, "ok": not errors, "comments": comments, "problems": problems}


def write(run, round_: int) -> dict:
    """evaluate, then store coverage.json and emit note.revision_coverage (counts only)."""
    run = Path(run)
    doc = evaluate(run, round_)
    letter_mod.refuse_links(run, round_, "coverage.json")
    state.atomic_write(letter_mod.round_dir(run, round_) / "coverage.json", _document_json(doc))
    errors = sum(1 for row in doc["problems"] if row["level"] == "error")
    events.emit(run, "note.revision_coverage", actor="agent", round=round_, comments=len(doc["comments"]),
                errors=errors, warnings=len(doc["problems"]) - errors, ok=doc["ok"])
    return doc
