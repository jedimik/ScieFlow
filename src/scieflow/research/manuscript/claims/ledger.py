"""The claim ledger (M-G 5.3): build `report/claims/claims.jsonl` and `ledger.json`, and read them back for show.

A build reads only the manuscript (no evidence yet: the pins come with M-G t2), makes no network call and
writes deterministic bytes: sorted keys, no timestamps, one record per claim sorted by (file order, line, id).
A claim is a unit with at least one checkable number, one reference or one cite key. More than 5,000 claims
is refused (`claims-limit`) and nothing is written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from scieflow.research.journal import state
from scieflow.research.manuscript import tex
from scieflow.research.manuscript.claims import numbers as numbers_mod
from scieflow.research.manuscript.claims import refs as refs_mod
from scieflow.research.manuscript.claims import units as units_mod
from scieflow.core.review.claims import parser as claim_parser

LEDGER_VERSION = 1
PARSER_VERSION = 1
MAX_CLAIMS = 5000
TEXT_LIMIT = 2000
MAX_LEDGER_BYTES = 32 * 1024 * 1024                  # `show` reads at most this much (5,000 claims fit)


class ClaimsError(ValueError):
    """The ledger cannot be built or read: `rule` names the refusal, `message` says why (no values)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass(frozen=True)
class BuildResult:
    count: int
    claims_path: Path
    ledger_path: Path


def ledger_folder(run_folder) -> Path:
    return Path(run_folder) / "report" / "claims"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bib_dois(run_folder: Path) -> dict:
    bib = run_folder / "manuscript" / "references.bib"
    if not bib.exists():
        return {}
    try:
        text = tex.read_regular_text(run_folder / "manuscript", "references.bib")
    except tex.TexError as exc:
        raise ClaimsError(exc.rule, exc.message) from exc
    return claim_parser().parse_bib(text)


def _record(unit, found: list, keys: list, nums: list) -> dict:
    text = unit.text
    record = {
        "version": LEDGER_VERSION,
        "id": unit.id,
        "file": unit.file,
        "line": unit.line,
        "end_line": unit.end_line,
        "unit": unit.kind,
        "section": unit.section,
        "section_kinds": sorted(unit.section_kinds),
        "text": text[:TEXT_LIMIT],
        "text_sha256": _sha(text.encode("utf-8")),
        "numbers": nums,
        "refs": found,
        "cites": keys,
        "pins": {},
    }
    if len(text) > TEXT_LIMIT:
        record["text_truncated"] = True
    return record


def build(run_folder, main: str = "main.tex") -> BuildResult:
    """Write the claim ledger of the run's manuscript (M-G 5.3). Raises ClaimsError (no file written)."""
    run_folder = Path(run_folder)
    manuscript = run_folder / "manuscript"
    try:
        tree = tex.read_tree(manuscript, main, keep_comments=True)
    except tex.TexError as exc:
        raise ClaimsError(exc.rule, exc.message) from exc
    bib = _bib_dois(run_folder)
    order = {name: index for index, name in enumerate(tree.files)}
    claims = []
    for unit in units_mod.extract_units(tree):
        found, _problems = refs_mod.parse_refs("\n".join(unit.comments), unit.text, bib)
        keys = refs_mod.cite_keys(unit.text)
        nums = numbers_mod.extract(unit.text)
        if not (found or keys or nums):
            continue
        claims.append((unit, found, keys, nums))
    if len(claims) > MAX_CLAIMS:
        raise ClaimsError("claims-limit",
                          f"{len(claims)} claims: the ledger holds at most {MAX_CLAIMS}")
    numbered = units_mod.number([unit for unit, _found, _keys, _nums in claims])
    records = [_record(unit, found, keys, nums)
               for unit, (_u, found, keys, nums) in zip(numbered, claims)]
    records.sort(key=lambda r: (order.get(r["file"], len(order)), r["line"], r["id"]))

    claim_lines = b"".join(
        (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        for record in records)
    document = {
        "version": LEDGER_VERSION,
        "parser_version": PARSER_VERSION,
        "main": tree.main,
        "manuscript_tree_sha256": tex.source_tree_hash(manuscript),
        "count": len(records),
        "claims_sha256": _sha(claim_lines),
    }
    ledger_bytes = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")

    _refuse_links(run_folder)
    folder = ledger_folder(run_folder)
    claims_path = folder / "claims.jsonl"
    ledger_path = folder / "ledger.json"
    try:
        _confined(run_folder, folder)
        folder.mkdir(parents=True, exist_ok=True)
        state.atomic_write(claims_path, claim_lines)
        state.atomic_write(ledger_path, ledger_bytes)
    except (state.StateError, OSError) as exc:
        raise ClaimsError("claims-path", f"the claim ledger cannot be written: {exc}") from exc
    return BuildResult(count=len(records), claims_path=claims_path, ledger_path=ledger_path)


def read(run_folder, claim_id: str | None = None) -> list[dict]:
    """The ledger records of the run (M-G 5.3 `show`), optionally only the one with `claim_id`.

    Raises ClaimsError: `claims-path` for a symbolic link or a path outside the run, `ledger-missing`
    without a ledger, `ledger-unreadable` for a file that is not a list of claim records (its content is
    never echoed).
    """
    run_folder = Path(run_folder)
    _refuse_links(run_folder)
    path = ledger_folder(run_folder) / "claims.jsonl"
    if not path.is_file():
        raise ClaimsError("ledger-missing",
                          "no claim ledger yet: run `scieflow manuscript claims build <run>` first")
    _confined(run_folder, path)
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            raise ClaimsError("ledger-unreadable", "claims.jsonl is larger than the ledger limit")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ClaimsError("ledger-unreadable", "claims.jsonl cannot be read as UTF-8 text") from None
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            raise ClaimsError("ledger-unreadable", f"claims.jsonl line {number} is not JSON") from None
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ClaimsError("ledger-unreadable", f"claims.jsonl line {number} is not a claim record")
        records.append(record)
    if claim_id is not None:
        records = [record for record in records if record["id"] == claim_id]
        if not records:
            raise ClaimsError("claim-unknown", "no claim with that id in the ledger")
    return records


def _refuse_links(run_folder: Path) -> None:
    """Refuse a symbolic link at report/, report/claims/ or either ledger file (M-G 5.1, 5.16)."""
    folder = ledger_folder(run_folder)
    for part in (Path(run_folder) / "report", folder, folder / "claims.jsonl", folder / "ledger.json"):
        if part.is_symlink():
            raise ClaimsError("claims-path", f"{part.relative_to(run_folder)} is a symbolic link")


def _confined(run_folder: Path, path: Path) -> None:
    """Refuse a path that resolves outside the run folder."""
    if not path.resolve(strict=False).is_relative_to(Path(run_folder).resolve()):
        raise ClaimsError("claims-path", f"{path.name} lies outside the run folder")

