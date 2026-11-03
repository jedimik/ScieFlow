"""Decision letters (M-G 5.10, M-G-D9): normalise, split into blocks and paragraphs, read the decision.

`ingest` stores the normalised letter as revision/round-<N>/letter.txt with letter.json (blocks, paragraph
line ranges, decision) and pins the submitted manuscript as point `submitted-r<N>`. The letter is data: it is
never executed, and nothing here sends it anywhere. Messages name rules and line numbers, never letter text
beyond 60 characters (M-G 5.16).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from scieflow.core import events
from scieflow.integrations.scite.mcp import in_dispatch
from scieflow.research.journal import html as html_mod
from scieflow.research.journal import state
from scieflow.research.manuscript import points as points_mod

VERSION = 1
MAX_BYTES = 1024 * 1024
SUFFIXES = (".txt", ".md", ".html", ".htm")
MAX_HEADER = 120
DECISIONS = ("accept", "minor", "major", "reject")
REVIEWER_RE = re.compile(r"^\s*(?:reviewer|referee)\s*#?\s*(\d{1,2})\b", re.IGNORECASE)
EDITOR_RE = re.compile(r"^\s*(?:(?:associate|academic|handling|section)\s+)?editor(?:-in-chief)?\b",
                       re.IGNORECASE)
DECISION_PATTERNS = (
    ("accept", re.compile(r"\baccept(?:ed)? (?:for publication|as is)\b", re.IGNORECASE)),
    ("minor", re.compile(r"\bminor revision\b", re.IGNORECASE)),
    ("major", re.compile(r"\bmajor revision\b", re.IGNORECASE)),
    ("reject", re.compile(r"\breject(?:ed)?\b", re.IGNORECASE)),
)
EDITOR_ID_RE = re.compile(r"E\d*")


class LetterError(ValueError):
    """A letter is refused: `rule` names the refusal, `message` says why (no letter text)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def _json(record: dict) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise(raw: str) -> str:
    """NFC, LF line ends, trailing spaces and tabs removed, trailing blank lines removed, one final newline."""
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _lines(text: str) -> list:
    body = text[:-1] if text.endswith("\n") else text
    return body.split("\n") if body else []


def parse(text: str) -> list:
    """The blocks of a normalised letter, in order. Each block: id, label_line, start, end, paragraphs.

    Headers: `Reviewer #n` / `Referee n` (R<n>; a repeated header is text of the current block) and
    `Editor`-style lines (E for the first, E2, E3 for later ones). Text before the first header is E.
    Paragraphs are maximal runs of non-blank lines within one block.
    """
    lines = _lines(text)
    owner: dict = {}
    info: dict = {}
    order: list = []
    current = None

    def open_block(block_id: str, label):
        info[block_id] = {"label_line": label, "lines": []}
        order.append(block_id)
        return block_id

    for number, line in enumerate(lines, start=1):
        if len(line) <= MAX_HEADER:
            match = REVIEWER_RE.match(line)
            if match:
                block_id = f"R{int(match.group(1))}"
                if block_id not in info:
                    current = open_block(block_id, number)
                    owner[number] = current
                    info[current]["lines"].append(number)
                    continue
            elif EDITOR_RE.match(line):
                if "E" in info and info["E"]["label_line"] is None and current == "E":
                    info["E"]["label_line"] = number
                elif "E" not in info:
                    current = open_block("E", number)
                else:
                    k = 2
                    while f"E{k}" in info:
                        k += 1
                    current = open_block(f"E{k}", number)
                owner[number] = current
                info[current]["lines"].append(number)
                continue
        if current is None:
            current = open_block("E", None)
        owner[number] = current
        info[current]["lines"].append(number)

    blocks: list = []
    for block_id in order:
        numbers = info[block_id]["lines"]
        runs = _runs(lines, numbers, owner, block_id)
        if not runs:
            continue
        paragraphs = [{"id": f"{block_id}.p{index}", "start": a, "end": b}
                      for index, (a, b) in enumerate(runs, start=1)]
        blocks.append({
            "id": block_id,
            "label_line": info[block_id]["label_line"],
            "start": min(numbers),
            "end": max(b for _a, b in runs),
            "paragraphs": paragraphs,
        })
    return blocks


def _runs(lines: list, numbers: list, owner: dict, block_id: str) -> list:
    """(start, end) line runs of non-blank lines owned by `block_id`, never crossing a block change."""
    runs: list = []
    open_run = None
    for number in numbers:
        if not lines[number - 1].strip():
            if open_run is not None:
                runs.append(open_run)
                open_run = None
            continue
        if open_run is not None and open_run[1] == number - 1 and owner.get(number) == block_id:
            open_run = (open_run[0], number)
            continue
        if open_run is not None:
            runs.append(open_run)
        open_run = (number, number)
    if open_run is not None:
        runs.append(open_run)
    return runs


def decide(text: str, blocks: list, flag: str | None = None) -> tuple:
    """(decision, source): the flag wins; else the one decision phrase found in the editor blocks."""
    if flag is not None:
        return flag, "flag"
    lines = _lines(text)
    found: set = set()
    for block in blocks:
        if not EDITOR_ID_RE.fullmatch(block["id"]):
            continue
        body = "\n".join(" ".join(lines[p["start"] - 1:p["end"]]) for p in block["paragraphs"])
        for value, pattern in DECISION_PATTERNS:
            if pattern.search(body):
                found.add(value)
    if len(found) == 1:
        return found.pop(), "letter"
    return "unknown", "unknown"


def _read_letter(run: Path, path: Path) -> tuple:
    """(normalised text, suffix). A symbolic link, a path outside the run, a bad suffix, a size over 1 MB, or
    text that is not UTF-8 is refused before anything is written."""
    if path.is_symlink():
        raise LetterError("letter-symlink", "the letter is a symbolic link; refused")
    if not path.is_file():
        raise LetterError("letter-missing", "the letter file does not exist")
    if not path.resolve().is_relative_to(run.resolve()):
        raise LetterError("letter-path", "the letter must be a file inside the run folder")
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise LetterError("letter-type", "the letter must be .txt, .md, .html or .htm")
    if path.stat().st_size > MAX_BYTES:
        raise LetterError("letter-size", f"the letter is over {MAX_BYTES} bytes; refused")
    try:
        raw = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LetterError("letter-encoding", "the letter is not valid UTF-8; refused") from exc
    if suffix in (".html", ".htm"):
        raw = html_mod.to_text(raw)
    return normalise(raw), suffix


def round_dir(run, round_: int) -> Path:
    return Path(run) / "revision" / f"round-{round_}"


def refuse_links(run, round_: int, *names: str) -> None:
    """Refuse a symbolic link at revision/, at revision/round-N/ or at one of `names` inside the round folder.

    Links are never followed (M-G 5.16). Call before any read or write under the round folder, so a refusal
    leaves nothing behind in the link's target.
    """
    run = Path(run)
    revision = run / "revision"
    folder = round_dir(run, round_)
    for part in (revision, folder, revision / "points"):
        if part.is_symlink():
            raise LetterError("symlink", f"{part.relative_to(run).as_posix()} is a symbolic link; refused")
    for name in names:
        if (folder / name).is_symlink():
            raise LetterError("symlink", f"{name} is a symbolic link; refused")


def existing_rounds(run) -> list:
    folder = Path(run) / "revision"
    if folder.is_symlink():
        raise LetterError("symlink", "revision is a symbolic link; refused")
    if not folder.is_dir():
        return []
    found = []
    for child in folder.iterdir():
        match = re.fullmatch(r"round-(\d+)", child.name)
        if not match:
            continue
        if child.is_symlink():
            raise LetterError("symlink", f"{child.name} is a symbolic link; refused")
        if child.is_dir():
            found.append(int(match.group(1)))
    return sorted(found)


def load(run, round_: int) -> dict:
    """letter.json of an ingested round (LetterError when absent, linked or unreadable)."""
    refuse_links(run, round_, "letter.json")
    path = round_dir(run, round_) / "letter.json"
    if not path.is_file():
        raise LetterError("round-missing", f"round {round_} has no ingested letter: run revision ingest first")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LetterError("letter-invalid", f"round {round_} has an unreadable letter.json") from exc


def _point_for(run: Path, round_: int) -> dict:
    label = f"submitted-r{round_}"
    if points_mod.point_dir(run, label).exists():
        return {"label": label, "origin": "existing", "note": None}
    package = run / points_mod.SUBMISSION / points_mod.PACKAGE_NAME
    if package.exists():
        record = points_mod.add(run, label, from_package=True)
        return {"label": label, "origin": record["origin"], "note": None}
    record = points_mod.add(run, label)
    return {"label": label, "origin": record["origin"],
            "note": "point taken from the current manuscript, not from a submission package"}


def ingest(run, letter_path: Path, *, round_: int | None = None, decision: str | None = None) -> dict:
    """Store letter `letter_path` (inside the run) as the next (or given) round; returns the record."""
    run = Path(run)
    if in_dispatch():
        raise LetterError("dispatch", "refused: the coordinator ingests letters; sub-agents never do "
                                      "(SCIEFLOW_DISPATCH is set)")
    if decision is not None and decision not in DECISIONS:
        raise LetterError("decision", f"decision must be one of {', '.join(DECISIONS)}")
    text, suffix = _read_letter(run, Path(letter_path))
    blocks = parse(text)
    if not blocks:
        raise LetterError("letter-empty", "the letter has no text to structure")
    number = round_ if round_ is not None else max(existing_rounds(run) + [0]) + 1
    if round_dir(run, number).exists() or round_dir(run, number).is_symlink():
        raise LetterError("round-exists", f"round-{number} already exists; choose another round")
    refuse_links(run, number, "letter.txt", "letter.json")
    value, source = decide(text, blocks, decision)
    point = _point_for(run, number)
    record = {
        "version": VERSION,
        "round": number,
        "letter_sha256": _sha(text.encode("utf-8")),
        "source_suffix": suffix,
        "decision": value,
        "decision_source": source,
        "blocks": blocks,
    }
    folder = round_dir(run, number)
    state.atomic_write(folder / "letter.txt", text.encode("utf-8"))
    state.atomic_write(folder / "letter.json", _json(record))
    paragraphs = sum(len(block["paragraphs"]) for block in blocks)
    events.emit(run, "note.revision_ingested", actor="agent", round=number, blocks=len(blocks),
                paragraphs=paragraphs, decision=value, letter_sha256=record["letter_sha256"])
    return {"record": record, "point": point, "paragraphs": paragraphs}
