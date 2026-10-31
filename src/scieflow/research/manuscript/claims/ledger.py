"""The claim ledger (M-G 5.3): build `report/claims/claims.jsonl` and `ledger.json`, and read them back for show.

A build reads the manuscript and the run's evidence (M-G t2: pins and reference findings), makes no network call
and writes deterministic bytes: sorted keys, no timestamps, one record per claim sorted by (file order, line, id).
A claim is a unit with at least one checkable number, one reference or one cite key. More than 5,000 claims is
refused (`claims-limit`) and nothing is written. Pins are carried from the previous ledger while a claim's id and
references are unchanged; `repin` / `repin_claims` take them from the current evidence and emit one
`note.claims_repinned` event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from scieflow.core import events
from scieflow.core.review.claims import parser as claim_parser
from scieflow.research.journal import state
from scieflow.research.manuscript import tex
from scieflow.research.manuscript.claims import evidence as ev
from scieflow.research.manuscript.claims import numbers as numbers_mod
from scieflow.research.manuscript.claims import refs as refs_mod
from scieflow.research.manuscript.claims import resolve as resolve_mod
from scieflow.research.manuscript.claims import units as units_mod

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
    problems: list = field(default_factory=list)
    repinned: int = 0


def ledger_folder(run_folder) -> Path:
    return Path(run_folder) / "report" / "claims"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bib_text(run_folder: Path) -> str:
    bib = run_folder / "manuscript" / "references.bib"
    if not bib.exists():
        return ""
    try:
        return tex.read_regular_text(run_folder / "manuscript", "references.bib")
    except tex.TexError as exc:
        raise ClaimsError(exc.rule, exc.message) from exc


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


def _problem(unit, item: dict, ref: dict | None = None) -> dict:
    out = {"claim": unit.id, "file": unit.file, "line": unit.line, "rule": item["rule"],
           "severity": item["severity"], "message": item["message"]}
    if ref is not None:
        out["ref"] = f"{ref['type']}:{ref['id']}"
    return out


def _previous(run_folder: Path) -> dict:
    """The records of the previous ledger by id; empty when there is none or it cannot be read."""
    try:
        return {rec["id"]: rec for rec in read(run_folder)}
    except ClaimsError:
        return {}


def _pins(run_folder: Path, outcomes: list) -> dict:
    pins = {}
    for outcome in outcomes:
        if outcome.pinned and outcome.files:
            ref = outcome.ref
            try:
                pins[f"{ref['type']}:{ref['id']}"] = ev.pins_for(run_folder, outcome.files)
            except (ev.EvidenceError, OSError) as exc:
                raise ClaimsError("claims-path", "a pinned evidence file cannot be read") from exc
    return pins


def build(run_folder, main: str = "main.tex", *, repin: bool = False, repin_claims=()) -> BuildResult:
    """Write the claim ledger of the run's manuscript (M-G 5.3). Raises ClaimsError (no ledger file written)."""
    run_folder = Path(run_folder)
    _refuse_links(run_folder)
    manuscript = run_folder / "manuscript"
    try:
        tree = tex.read_tree(manuscript, main, keep_comments=True)
    except tex.TexError as exc:
        raise ClaimsError(exc.rule, exc.message) from exc
    text = _bib_text(run_folder)
    parser = claim_parser()
    bib = parser.parse_bib(text)
    bib_keys = {match.group(2) for match in parser.ENTRY_RE.finditer(text)}
    order = {name: index for index, name in enumerate(tree.files)}
    problems: list = []
    claims = []
    for unit in units_mod.extract_units(tree):
        found, parse_problems = refs_mod.parse_refs("\n".join(unit.comments), unit.text, bib)
        problems += [_problem(unit, resolve_mod.finding(p["rule"], p["message"])) for p in parse_problems]
        keys = refs_mod.cite_keys(unit.text)
        nums = numbers_mod.extract(unit.text)
        if not (found or keys or nums):
            continue
        claims.append((unit, found, keys, nums))
    if len(claims) > MAX_CLAIMS:
        raise ClaimsError("claims-limit",
                          f"{len(claims)} claims: the ledger holds at most {MAX_CLAIMS}")
    numbered = units_mod.number([unit for unit, _found, _keys, _nums in claims])
    if set(repin_claims) - {unit.id for unit in numbered}:
        raise ClaimsError("claim-unknown", "no claim with that id in the manuscript")
    try:
        ctx = resolve_mod.open_context(run_folder, bib_keys, bib)
    except ev.EvidenceError as exc:
        raise ClaimsError(exc.rule, exc.message) from exc
    previous = _previous(run_folder)
    records = []
    requested = 0
    for unit, (_u, found, keys, nums) in zip(numbered, claims):
        outcomes = [resolve_mod.resolve_ref(ctx, ref) for ref in found]
        for outcome in outcomes:
            problems += [_problem(unit, item, outcome.ref) for item in outcome.findings]
        for key in keys:
            problems += [_problem(unit, item) for item in resolve_mod.resolve_cite(ctx, key)]
        record = _record(unit, found, keys, nums)
        asked = repin or unit.id in repin_claims
        old = previous.get(unit.id)
        if not asked and old is not None and old.get("refs") == found:
            record["pins"] = old.get("pins", {})
        else:
            record["pins"] = _pins(run_folder, outcomes)
            requested += int(asked)
        records.append(record)
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
    if repin or repin_claims:
        events.emit(run_folder, "note.claims_repinned", "system", count=requested)
    return BuildResult(count=len(records), claims_path=claims_path, ledger_path=ledger_path,
                       problems=problems, repinned=requested)


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
