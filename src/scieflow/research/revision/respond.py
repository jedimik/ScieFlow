"""The response starter (M-G 5.12, M-G-D10): one section per adopted comment, for the author to answer.

`write` builds revision/round-<N>/response.md from the stored letter, adopted.json and the adopted reviews. The
starter quotes each comment (a manuscript-side file, never a message or an event, M-G 5.16). Nothing else is
written. Refusals name a rule and never echo letter text beyond the quoted comments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scieflow.research.journal import state
from scieflow.research.revision import letter as letter_mod
from scieflow.research.revision.letter import LetterError

RESPONSE = "response.md"
BLOCK_RE = re.compile(r"E\d*|R\d+")
COMMENT_RE = re.compile(r"(?:E\d*|R\d+)\.[Mm]\d+")
PLACEHOLDER_RESPONSE = "Response: [write your response]"
PLACEHOLDER_CHANGE = "Change: [file \u2014 what changed]"
PLACEHOLDER_NO_CHANGE = "No change: [why]"


def load_adopted(run, round_: int) -> dict:
    """adopted.json of an adopted round (LetterError when absent, linked or unreadable)."""
    letter_mod.refuse_links(run, round_, "adopted.json")
    path = letter_mod.round_dir(run, round_) / "adopted.json"
    if not path.is_file():
        raise LetterError("adopted-missing", f"round {round_} has no adopted comments: run revision adopt first")
    try:
        adopted = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LetterError("adopted-invalid", f"round {round_} has an unreadable adopted.json") from exc
    if not isinstance(adopted, dict) or not isinstance(adopted.get("comments"), list) \
            or not isinstance(adopted.get("not_comments"), list):
        raise LetterError("adopted-invalid", f"round {round_}: adopted.json has no comments list")
    return adopted


def load_reviews(run, round_: int, blocks: list) -> dict:
    """The adopted review of each block id (block ids are checked before any path is built from them)."""
    for block in blocks:
        if not isinstance(block, str) or not BLOCK_RE.fullmatch(block):
            raise LetterError("letter-invalid", f"round {round_}: a block id is not a review id")
    letter_mod.refuse_links(run, round_, "reviews", *[f"reviews/{block}.json" for block in blocks])
    reviews = {}
    for block in blocks:
        path = letter_mod.round_dir(run, round_) / "reviews" / f"{block}.json"
        if not path.is_file():
            raise LetterError("review-missing", f"round {round_} has no adopted review for {block}")
        try:
            reviews[block] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LetterError("review-invalid", f"round {round_}: the review for {block} is unreadable") from exc
    return reviews


def comment_entry(reviews: dict, comment_id: str) -> tuple:
    """(kind, entry) of an adopted comment id such as R1.M1: kind is major or minor."""
    block, _, label = comment_id.partition(".")
    review = reviews.get(block)
    if review is None or label[:1] not in ("M", "m"):
        raise LetterError("adopted-invalid", f"{comment_id} is not an adopted comment")
    kind, group = ("major", "major") if label[:1] == "M" else ("minor", "minor")
    for entry in review.get(group, []):
        if isinstance(entry, dict) and entry.get("id") == label:
            return kind, entry
    raise LetterError("adopted-invalid", f"{comment_id} is not in its review")


def read_response(run, round_: int) -> str:
    """response.md of a round, read as UTF-8 (LetterError when absent, linked or unreadable)."""
    letter_mod.refuse_links(run, round_, RESPONSE)
    path = letter_mod.round_dir(run, round_) / RESPONSE
    if not path.is_file():
        raise LetterError("response-missing", f"round {round_} has no response.md: run revision respond first")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LetterError("response-invalid", f"round {round_}: response.md is not UTF-8") from exc


def build(run, round_: int) -> str:
    record = letter_mod.load(run, round_)
    adopted = load_adopted(run, round_)
    blocks = [block["id"] for block in record["blocks"]]
    reviews = load_reviews(run, round_, blocks)
    lines = [f"# Response to reviewers, round {round_}", "",
             f"Decision: {record['decision']} ({record['decision_source']}).", "",
             "Paragraphs with no comment:"]
    for item in adopted["not_comments"]:
        lines.append(f"- {item['paragraph']}: {item['reason']}")
    for comment_id in adopted["comments"]:
        kind, entry = comment_entry(reviews, comment_id)
        lines += ["", f"## {comment_id} ({kind}) \u2014 {entry['location']}", ""]
        quote = entry["problem"] if kind == "major" else entry["comment"]
        lines += [f"> {part}" for part in quote.split("\n")]
        lines.append("")
        if kind == "major":
            lines += [f"Agent's reading: {entry['why_it_matters']} Suggested resolution: {entry['resolution']}", ""]
        lines += [PLACEHOLDER_RESPONSE, PLACEHOLDER_CHANGE, PLACEHOLDER_NO_CHANGE]
    return "\n".join(lines) + "\n"


def write(run, round_: int, *, force: bool = False) -> Path:
    """Write revision/round-N/response.md; an existing one is kept unless `force` is given."""
    run = Path(run)
    letter_mod.refuse_links(run, round_, RESPONSE, "adopted.json", "reviews")
    target = letter_mod.round_dir(run, round_) / RESPONSE
    if target.exists() and not force:
        raise LetterError("exists", f"round {round_} already has response.md; use --force to replace it")
    text = build(run, round_)
    state.atomic_write(target, text.encode("utf-8"))
    return target
