"""M-H t8 (plan 5.11, 5.13): `scieflow sr eval` — each screener's confusion and the resolved verdict against a
human-labelled gold sample, with Wilson intervals.

Everything is derived from the committed decisions each time (nothing is stored). The gold file is read only
from inside the run, and no path component may be a symbolic link. Output carries record ids and counts only,
never record text.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scieflow.research.evals import INCLUDE, EXCLUDE, Confusion
from scieflow.research.sr import screening
from scieflow.research.sr.errors import SRError

GOLD_FORMAT = 1
METRICS = ("sensitivity", "specificity", "accuracy", "ppv")
_RECORD_ID = re.compile(r"^R\d{5}$")
_LABELS = (INCLUDE, EXCLUDE)


def gold_path(ws: Path, gold: str) -> Path:
    """The gold file, resolved inside the run. Refuses `..`, a path outside the run, any symbolic link on the
    way and anything that is not a regular file."""
    if not gold:
        raise SRError("--gold names no file")
    raw = Path(gold)
    if ".." in raw.parts:
        raise SRError("--gold must not contain '..'")
    path = raw if raw.is_absolute() else ws / raw
    try:
        rel = path.relative_to(ws)
    except ValueError:
        raise SRError("--gold must resolve inside the run") from None
    current = ws
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise SRError(f"--gold must not pass through a symbolic link ({current.name})")
    if not current.is_file():
        raise SRError("--gold is missing or not a regular file")
    return current


def load_gold(ws: Path, gold: str, stage: str) -> dict[str, str]:
    """{record_id: include|exclude} from a `format: 1` gold file for this stage."""
    path = gold_path(ws, gold)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise SRError("--gold is not readable YAML") from None
    if not isinstance(doc, dict) or doc.get("format") != GOLD_FORMAT:
        raise SRError(f"gold file must be a mapping with format: {GOLD_FORMAT}")
    if doc.get("stage") != stage:
        raise SRError(f"gold file is for stage {doc.get('stage')!r}, not {stage!r}")
    labels = doc.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise SRError("gold file has no labels mapping")
    out: dict[str, str] = {}
    for rid, label in labels.items():
        if not isinstance(rid, str) or not _RECORD_ID.match(rid):
            raise SRError("gold label ids must look like R00001")
        if label not in _LABELS:
            raise SRError(f"gold label for {rid} must be include or exclude")
        out[rid] = label
    return out


def _row(pairs: list[tuple[str, str]], undecided: list[str]) -> dict:
    confusion = Confusion.from_pairs(pairs)
    metrics = {}
    for name in METRICS:
        interval = getattr(confusion, name)
        metrics[name] = interval.to_json_dict() if interval is not None else None
    return {"tp": confusion.tp, "fp": confusion.fp, "fn": confusion.fn, "tn": confusion.tn,
            "decided": confusion.total, "undecided": undecided, "metrics": metrics}


def evaluate(project, ws: Path, *, gold: str, stage: str) -> dict:
    """Confusion of each screener and of the resolved verdict against the gold labels. A labelled record
    that a stream has not decided (or that is still pending) is counted in `undecided` and named."""
    screening._stage(stage)
    labels = load_gold(ws, gold, stage)
    screeners, _warnings = screening._screeners(project, ws)
    streams = screening._stage_streams(ws, stage)
    verdicts = screening.resolved(ws, stage, screeners)
    rows: dict[str, dict] = {}
    for name in screeners:
        pairs, undecided = [], []
        for rid in sorted(labels):
            decision = streams.get(rid, {}).get(name)
            decided = getattr(decision, "decision", None)
            if decided in (INCLUDE, EXCLUDE):
                pairs.append((labels[rid], decided))
            else:
                undecided.append(rid)
        rows[name] = _row(pairs, undecided)
    pairs, undecided = [], []
    for rid in sorted(labels):
        verdict = verdicts.get(rid, screening.PENDING)
        if verdict in (INCLUDE, EXCLUDE):
            pairs.append((labels[rid], verdict))
        else:
            undecided.append(rid)
    return {"run": ws.name, "stage": stage, "gold": gold, "labelled": len(labels),
            "screeners": list(screeners), "streams": rows, "resolved": _row(pairs, undecided)}


def _interval_text(name: str, interval: dict | None) -> str:
    if interval is None:
        return f"{name} undefined (no cases)"
    return (f"{name} {interval['k']}/{interval['n']} = {interval['point']:.6f} "
            f"[{interval['low']:.6f}, {interval['high']:.6f}]")


def render(doc: dict) -> list[str]:
    """Plain-text lines for the CLI (no record text)."""
    lines = [f"gold {doc['gold']}: {doc['labelled']} labelled records, stage {doc['stage']}"]
    rows = [(name, doc["streams"][name]) for name in doc["screeners"]] + [("resolved", doc["resolved"])]
    for name, row in rows:
        lines.append(f"{name}: TP {row['tp']} FN {row['fn']} FP {row['fp']} TN {row['tn']} "
                     f"(decided {row['decided']})")
        for metric in METRICS:
            lines.append("  " + _interval_text(metric, row["metrics"][metric]))
        if row["undecided"]:
            lines.append(f"  undecided ({len(row['undecided'])}): " + ", ".join(row["undecided"]))
    return lines
