"""The corpus of a systematic review (M-H plan 5.5): the records of the committed search rounds.

A search round is committed when a later `sr.committed` event of kind `search` names its op. Only
committed rounds form the corpus, in round order; a round directory without its commit (a crashed
operation) is ignored. check_round_files re-reads each committed round's files and refuses a
missing, linked or altered one (the search part of `sr verify`, which t6 completes).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scieflow.core import events
from scieflow.research.sr.errors import SRError

COMMITTED = "sr.committed"


def committed_search_rounds(ws) -> list[dict]:
    """The committed search operations of the run, in file order (their sr.committed data)."""
    out = []
    for row in events.read(Path(ws), types=(COMMITTED,)):
        data = dict(row.get("data") or {})
        if data.get("kind") == "search":
            out.append(data)
    return out


def round_numbers(ws) -> list[int]:
    return sorted(int(data["round"]) for data in committed_search_rounds(ws))


def _read_jsonl(path: Path) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise SRError(f"sr/rounds/{path.parent.name}/{path.name} is missing or not a plain file")
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SRError(f"sr/rounds/{path.parent.name}/{path.name} is not valid JSON lines") from None


def _round_records(ws: Path, number: int) -> list[dict]:
    return _read_jsonl(ws / "sr" / "rounds" / str(number) / "records.jsonl")


def load_corpus(ws) -> list[dict]:
    """Every record of every committed search round, in round order."""
    ws = Path(ws)
    rows: list[dict] = []
    for number in round_numbers(ws):
        rows.extend(_round_records(ws, number))
    return rows


def read_records(ws, number: int | None = None) -> list[dict]:
    """The corpus, or one committed round's records (SRError when that round is not committed)."""
    ws = Path(ws)
    if number is None:
        return load_corpus(ws)
    if number not in round_numbers(ws):
        raise SRError(f"round {number} is not a committed search round")
    return _round_records(ws, number)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_round_files(ws) -> None:
    """Refuse when a committed round's file is missing, linked or differs from its committed sha256."""
    ws = Path(ws)
    for data in committed_search_rounds(ws):
        for rel, digest in sorted((data.get("files") or {}).items()):
            path = ws / rel
            if path.is_symlink() or not path.is_file():
                raise SRError(f"{rel} is missing or not a plain file")
            if _sha256(path) != digest:
                raise SRError(f"{rel} differs from its committed sha256")
