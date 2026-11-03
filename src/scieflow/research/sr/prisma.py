"""`sr prisma` (M-H plan 5.9, 5.16): the PRISMA 2020 flow and checklist, derived from the ledger each time.

`sr verify` runs first and refuses on any error. The flow and checklist are derived by the vendored
researcher functions over the committed events; the counts beside them are derived from the committed search
rounds and the screening streams. `--write` stamps the outputs with `derived_from` and writes them to
sr/prisma/. Nothing in sr/prisma/ is read back as a source of counts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow._vendor.researcher_core.sr_prisma import derive_prisma_checklist, derive_prisma_flow
from scieflow.core import events
from scieflow.core.design import lock
from scieflow.research.sr import ledger, records, screening, verify
from scieflow.research.sr.errors import SRError

OUT_DIR = Path("sr") / "prisma"
CHECKLIST = "config/reporting/prisma-2020.yml"
COMMITTED = "sr.committed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _checklist_items(project) -> list[dict]:
    path = Path(project.root) / CHECKLIST
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise SRError(f"{CHECKLIST} cannot be read") from None
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise SRError(f"{CHECKLIST} holds no item list")
    return items


def _committed_rows(ws: Path) -> list[dict]:
    """The committed sr events: every sr.committed and every detail event of a committed operation."""
    rows = events.read(ws)
    committed = {str((r.get("data") or {}).get("op")) for r in rows if r.get("type") == COMMITTED}
    return [r for r in rows if str(r.get("type", "")).startswith("sr.")
            and (r.get("type") == COMMITTED or str((r.get("data") or {}).get("op")) in committed)]


def _derived_from(ws: Path, count: int) -> dict:
    """The stamp: the event count, the hash of the committed sr events and the design ledger records."""
    payload = {"events": _committed_rows(ws), "design": [dict(r) for r in lock.read_ledger(ws).records]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {"events": count, "sha256": digest, "at": _now()}


def _round_summary(ws: Path, number: int) -> dict:
    path = ws / "sr" / "rounds" / str(number) / "round.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _rounds(ws: Path) -> list[dict]:
    out = []
    for data in records.committed_search_rounds(ws):
        number = int(data["round"])
        summary = _round_summary(ws, number)
        capped = sorted(f"{strategy}/{source}" for strategy, per in (summary.get("strategies") or {}).items()
                        for source, row in per.items() if isinstance(row, dict) and row.get("capped"))
        out.append({"round": number, "identified": data.get("identified"), "duplicates": data.get("duplicates"),
                    "new_records": data.get("new_records"), "capped": capped})
    return out


def _kappa(ws: Path) -> dict:
    out = {}
    for stage in screening.STAGES:
        try:
            out[stage] = screening.kappa(ws, stage)
        except SRError as exc:
            out[stage] = {"problem": str(exc)}
    return out


def _statement(project, ws: Path) -> str | None:
    try:
        screeners, _warnings = screening._screeners(project, ws)
    except SRError:
        return None
    return (f"two independent agent screeners: {screeners[0]}, {screeners[1]}; conflicts adjudicated by a "
            "human; screener independence is procedural (docs)")


def build(project, ws) -> dict:
    """The PRISMA flow, checklist and the derived counts of the run (refused on an invalid sr ledger)."""
    ws = Path(ws)
    verify.preflight(project, ws)
    evs = list(ledger.view(ws).iter_events())
    flow = derive_prisma_flow(evs, run_id=ws.name)
    checklist = derive_prisma_checklist(evs, run_id=ws.name, flow=flow)
    state = lock.state(project, ws)
    return {
        "run": ws.name,
        "flow": flow.to_json_dict(),
        "checklist": checklist.to_json_dict(),
        "rounds": _rounds(ws),
        "kappa": _kappa(ws),
        "screening_statement": _statement(project, ws),
        "protocol_trail": state.trail,
        "derived_from": _derived_from(ws, len(evs)),
    }


def _box(lines: list[str], width: int = 78) -> list[str]:
    return [f"| {line:<{width - 4}} |" for line in lines]


def _text(doc: dict) -> str:
    flow = doc["flow"]
    sources = ", ".join(f"{k} {v}" for k, v in flow["identified_by_source"].items()) or "none"
    reasons = ", ".join(f"{k} {v}" for k, v in flow["reports_excluded_by_reason"].items()) or "none"
    boxes = [
        ["Identification", f"Records identified (n={flow['identified']}): {sources}",
         f"Duplicates removed (n={flow['duplicates_removed']})",
         f"Records after duplicates removed (n={flow['records_after_dedup']})"],
        ["Screening", f"Records screened (n={flow['records_screened']})",
         f"Records excluded (n={flow['records_excluded']})"],
        ["Eligibility", f"Reports sought for retrieval (n={flow['reports_sought_for_retrieval']})",
         f"Reports not retrieved (n={flow['reports_not_retrieved']})",
         f"Reports assessed for eligibility (n={flow['reports_assessed']})",
         f"Reports excluded (n={flow['reports_excluded']}): {reasons}"],
        ["Included", f"Studies included (n={flow['studies_included']})"],
    ]
    lines: list[str] = []
    for title, *rows in boxes:
        lines.append(f"{title}:")
        lines.extend(_box(rows))
    return "\n".join(lines) + "\n"


def _mermaid(doc: dict) -> str:
    flow = doc["flow"]
    sources = ", ".join(f"{k} {v}" for k, v in flow["identified_by_source"].items()) or "none"
    reasons = ", ".join(f"{k} {v}" for k, v in flow["reports_excluded_by_reason"].items()) or "none"
    nodes = [
        ("A", f"Records identified (n={flow['identified']}): {sources}"),
        ("B", f"Duplicates removed (n={flow['duplicates_removed']})"),
        ("C", f"Records after duplicates removed (n={flow['records_after_dedup']})"),
        ("D", f"Records screened (n={flow['records_screened']})"),
        ("E", f"Records excluded (n={flow['records_excluded']})"),
        ("F", f"Reports sought for retrieval (n={flow['reports_sought_for_retrieval']})"),
        ("G", f"Reports not retrieved (n={flow['reports_not_retrieved']})"),
        ("H", f"Reports assessed for eligibility (n={flow['reports_assessed']})"),
        ("I", f"Reports excluded (n={flow['reports_excluded']}): {reasons}"),
        ("J", f"Studies included (n={flow['studies_included']})"),
    ]
    edges = [("A", "B"), ("A", "C"), ("C", "D"), ("D", "E"), ("D", "F"), ("F", "G"), ("F", "H"), ("H", "I"),
             ("H", "J")]
    lines = ["flowchart TD"] + [f'    {key}["{label}"]' for key, label in nodes]
    lines += [f"    {a} --> {b}" for a, b in edges]
    return "\n".join(lines)


def _checklist_md(project, doc: dict) -> str:
    derived = {str(item["item"]): item for item in doc["checklist"]["items"]}
    rows = []
    for item in _checklist_items(project):
        key = str(item.get("id"))
        got = derived.get(key)
        status = got["status"] if got else "author-supplied"
        evidence = (got["evidence"] if got else "not covered by the vendored checklist; author-supplied")
        topic = str(item.get("topic", "")).replace("|", "/")
        rows.append(f"| {key} | {topic} | {status} | {str(evidence).replace('|', '/')} |")
    stamp = doc["derived_from"]
    head = [f"# PRISMA 2020 checklist: run {doc['run']}", "",
            f"Derived from {stamp['events']} committed sr events (sha256 {stamp['sha256'][:12]}, at {stamp['at']}). "
            "Nothing here is read back as a source of counts.", "",
            "| Item | Topic | Status | Evidence |", "|---|---|---|---|"]
    return "\n".join(head + rows) + "\n"


def write(project, ws, doc: dict) -> list[str]:
    """Write sr/prisma/flow.json, flow.md and checklist.md, each stamped with derived_from."""
    ws = Path(ws)
    out = ws / OUT_DIR
    screening._write_text(out / "flow.json", json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n")
    flow_md = (f"# PRISMA 2020 flow: run {doc['run']}\n\n"
               f"Derived from {doc['derived_from']['events']} committed sr events "
               f"(sha256 {doc['derived_from']['sha256'][:12]}, at {doc['derived_from']['at']}).\n\n"
               "```mermaid\n" + _mermaid(doc) + "\n```\n\n```text\n" + _text(doc) + "```\n")
    screening._write_text(out / "flow.md", flow_md)
    screening._write_text(out / "checklist.md", _checklist_md(project, doc))
    return [f"{OUT_DIR.as_posix()}/{name}" for name in ("flow.json", "flow.md", "checklist.md")]
