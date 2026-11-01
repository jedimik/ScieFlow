"""Adopt a structuring draft (M-G 5.11): every comment is checked against the stored letter, then written.

Nothing is written unless every check passes. Checks, in order: dispatch, draft path and size, letter hash
(stored file, record and draft agree), the stored blocks match a re-parse, block set, schema and extra
review rules, verbatim quotes, paragraph coverage. Messages name rules, ids and line ranges, never letter
text beyond 60 characters (M-G 5.16).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import NoReturn

from scieflow.core import events
from scieflow.core.review import decisions
from scieflow.integrations.scite.mcp import in_dispatch
from scieflow.research.journal import state
from scieflow.research.revision import letter as letter_mod
from scieflow.research.revision.letter import LetterError

MAX_DRAFT_BYTES = 1024 * 1024
MIN_QUOTE = 20
MIN_REASON, MAX_REASON = 3, 200
FORBIDDEN_KEYS = ("pass", "evidence", "followup_requests", "scite_status", "changed_from_blind")
LOCATION_RE = re.compile(r"letter:(\d+)-(\d+)")
ID_MAJOR_RE = re.compile(r"M\d+")
ID_MINOR_RE = re.compile(r"m\d+")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(record) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= 60 else text[:57] + "..."


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _refuse(rule: str, message: str) -> NoReturn:
    raise LetterError(rule, message)


def _draft_file(run: Path, draft_path: Path) -> bytes:
    if draft_path.is_symlink():
        _refuse("draft-path", "the draft is a symbolic link; refused")
    if not draft_path.is_file():
        _refuse("draft-path", "the draft file does not exist")
    if not draft_path.resolve().is_relative_to(run.resolve()):
        _refuse("draft-path", "the draft must be a file inside the run folder")
    if draft_path.stat().st_size > MAX_DRAFT_BYTES:
        _refuse("draft-size", f"the draft is over {MAX_DRAFT_BYTES} bytes; refused")
    return draft_path.read_bytes()


def _parse_draft(data: bytes, round_: int) -> dict:
    try:
        draft = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _refuse("draft-invalid", "the draft is not valid UTF-8 JSON")
    if not isinstance(draft, dict) or set(draft) - {"version", "round", "letter_sha256", "reviews",
                                                    "not_comments"}:
        _refuse("draft-invalid", "the draft must be an object with version, round, letter_sha256, reviews, "
                                 "not_comments")
    if draft.get("version") != 1 or draft.get("round") != round_:
        _refuse("draft-invalid", "the draft version or round does not match this run")
    if not isinstance(draft.get("reviews"), list) or not isinstance(draft.get("not_comments"), list):
        _refuse("draft-invalid", "reviews and not_comments must be lists")
    return draft


def _check_review(item, block: dict, lines: list) -> tuple:
    """Check one review entry; return (review, comment ranges [(a, b)], comment ids)."""
    if not isinstance(item, dict) or set(item) != {"block", "review"}:
        _refuse("draft-invalid", f"a reviews entry for {block['id']} must have exactly block and review")
    review = item["review"]
    where = block["id"]
    error = decisions.schema_error("manuscript-review", review)
    if error:
        path, _, message = error.partition(": ")
        _refuse("review-invalid", f"{where} review at {path}: {_short(message)}")
    for key in FORBIDDEN_KEYS:
        if key in review:
            _refuse("review-invalid", f"{where} review must not set {key}")
    if review["agent"] != f"journal-{where.lower()}":
        _refuse("review-invalid", f"{where} review: agent must be journal-{where.lower()}")
    if review["reviewed"] != "manuscript":
        _refuse("review-invalid", f"{where} review: reviewed must be manuscript")
    ids = []
    ranges = []
    for group, id_re, text_key in (("major", ID_MAJOR_RE, "problem"), ("minor", ID_MINOR_RE, "comment")):
        for entry in review[group]:
            label = f"{where}.{entry['id']}"
            if not id_re.fullmatch(entry["id"]):
                _refuse("review-invalid", f"{where} review: id {entry['id']!r} has the wrong form")
            ids.append(entry["id"])
            match = LOCATION_RE.fullmatch(entry["location"])
            if not match:
                _refuse("review-invalid", f"{label}: location must be letter:<a>-<b>")
            a, b = int(match.group(1)), int(match.group(2))
            if not (block["start"] <= a <= b <= block["end"]) or b > len(lines):
                _refuse("review-invalid", f"{label}: location letter:{a}-{b} is outside the block's lines "
                                          f"{block['start']}-{block['end']}")
            quote = _collapse(entry[text_key])
            span = _collapse("\n".join(lines[a - 1:b]))
            if quote not in span:
                _refuse("quote-not-in-letter", f"{label} (letter lines {a}-{b}) is not verbatim in the letter")
            if len(quote) < MIN_QUOTE and quote != span:
                _refuse("quote-too-short", f"{label} (letter lines {a}-{b}): a quote needs {MIN_QUOTE} "
                                           f"characters or the whole range")
            ranges.append((a, b))
    if len(set(ids)) != len(ids):
        _refuse("review-invalid", f"{where} review: an id is used more than once")
    return review, ranges


def _check_not_comments(draft_items, paragraphs: list) -> dict:
    known = {p["id"] for p in paragraphs}
    seen: dict = {}
    for item in draft_items:
        if not isinstance(item, dict) or set(item) != {"paragraph", "reason"}:
            _refuse("not-comment-invalid", "a not_comments entry must have exactly paragraph and reason")
        pid, reason = item["paragraph"], item["reason"]
        if pid not in known:
            _refuse("not-comment-invalid", f"not_comments names an unknown paragraph {pid!r}")
        if pid in seen:
            _refuse("not-comment-invalid", f"paragraph {pid} is listed in not_comments more than once")
        if not isinstance(reason, str) or not (MIN_REASON <= len(reason) <= MAX_REASON):
            _refuse("not-comment-invalid", f"the reason for {pid} must be 3 to 200 characters")
        seen[pid] = reason
    return seen


def adopt(run, round_: int, draft_path: Path, *, replace: bool = False) -> dict:
    run = Path(run)
    if in_dispatch():
        _refuse("dispatch", "refused: the coordinator adopts a draft; sub-agents never do (SCIEFLOW_DISPATCH is set)")
    record = letter_mod.load(run, round_)
    folder = letter_mod.round_dir(run, round_)
    adopted_path = folder / "adopted.json"
    if adopted_path.exists() and not replace:
        _refuse("adopted-exists", f"round {round_} already has adopted.json; use --replace to overwrite it")
    data = _draft_file(run, Path(draft_path))
    draft = _parse_draft(data, round_)

    letter_bytes = (folder / "letter.txt").read_bytes() if (folder / "letter.txt").is_file() else b""
    stored_sha = _sha(letter_bytes)
    if not (stored_sha == record.get("letter_sha256") == draft.get("letter_sha256")):
        _refuse("letter-changed", f"round {round_}: the letter, its record and the draft disagree on the letter hash")
    text = letter_bytes.decode("utf-8")
    lines = text.split("\n")[:-1]
    blocks = letter_mod.parse(text)
    if blocks != record.get("blocks"):
        _refuse("letter-changed", f"round {round_}: the stored block record does not match letter.txt")

    by_id = {b["id"]: b for b in blocks}
    drafted = [item.get("block") if isinstance(item, dict) else None for item in draft["reviews"]]
    if len(drafted) != len(set(drafted)) or set(drafted) != set(by_id):
        _refuse("blocks-differ", f"the draft reviews {sorted(map(str, drafted))} but the letter has "
                                 f"{sorted(by_id)}")

    reviews: dict = {}
    comment_ranges: list = []
    comment_ids: list = []
    for block in blocks:
        item = next(entry for entry in draft["reviews"] if entry["block"] == block["id"])
        review, ranges = _check_review(item, block, lines)
        reviews[block["id"]] = review
        comment_ranges.extend(ranges)
        comment_ids.extend(f"{block['id']}.{entry['id']}" for entry in review["major"] + review["minor"])

    not_map = _check_not_comments(draft["not_comments"], [p for b in blocks for p in b["paragraphs"]])
    for block in blocks:
        for para in block["paragraphs"]:
            covered = any(a <= para["end"] and b >= para["start"] for a, b in comment_ranges)
            if covered and para["id"] in not_map:
                _refuse("not-comment-conflict", f"paragraph {para['id']} has a comment and is also in not_comments")
            if not covered and para["id"] not in not_map:
                _refuse("letter-uncovered", f"paragraph {para['id']} has no comment and no not_comments reason")

    order = [p["id"] for b in blocks for p in b["paragraphs"]]
    adopted = {
        "version": letter_mod.VERSION,
        "round": round_,
        "letter_sha256": stored_sha,
        "draft_sha256": _sha(data),
        "comments": comment_ids,
        "not_comments": [{"paragraph": pid, "reason": not_map[pid]} for pid in order if pid in not_map],
    }
    for block_id, review in reviews.items():
        state.atomic_write(folder / "reviews" / f"{block_id}.json", _json(review))
    state.atomic_write(adopted_path, _json(adopted))
    events.emit(run, "note.revision_adopted", actor="agent", round=round_, blocks=len(blocks),
                comments=len(comment_ids), not_comments=len(not_map))
    return adopted
