"""`scieflow sr` (M-H t2, plan 5.13): the systematic-review group.

t2 ships `protocol show|check` and `status`. Later tickets add search, records, screening,
kappa, conflicts, adjudication, verify, prisma, rob, grade and eval to this group.

Exit codes: 0 ok, 1 refused or invalid (SRError: the message names the rule and an id or path,
never record text or a query), 2 usage (click). Read commands take --json.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core import events, network, sanitize
from scieflow.core.config import repo_root
from scieflow.core.design import lock
from scieflow.core.project import Project, ProjectError
from scieflow.research.search import registry
from scieflow.research.sr import adjudication, appraisal, ledger, prisma, protocol, records, screening, search, verify
from scieflow.research.sr.errors import SRError

OK, REFUSED, USAGE = 0, 1, 2
REFUSED_QUERY = 3   # a query the leak check refuses (plan 5.5 step 2): nothing was sent


def _fail(code: int, message: str) -> NoReturn:
    click.echo(message, err=True)
    sys.exit(code)


def _root() -> Path:
    return repo_root()


def _project() -> Project:
    try:
        return Project(_root().resolve())
    except FileNotFoundError as exc:
        _fail(USAGE, str(exc))


def _run(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        _fail(USAGE, str(exc))
    if not ws.is_dir():
        _fail(USAGE, f"no run workspace/{slug}")
    return ws


def _emit(as_json: bool, payload: dict, lines: list) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    else:
        for line in lines:
            click.echo(line)


@click.group()
def sr() -> None:
    """Systematic review: protocol, search, dual screening, adjudication, PRISMA, appraisal."""


@sr.group(name="protocol")
def protocol_group() -> None:
    """The locked review protocol (read-only)."""


@protocol_group.command("show")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def protocol_show(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    try:
        doc = protocol.show(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"Protocol version {doc['version']} (sha256 {doc['content_hash'][:12]})."]
    for kind in ("include", "exclude"):
        for crit in doc["review"]["eligibility"].get(kind, []):
            lines.append(f"{kind} {crit.get('id')}: {crit.get('text')}")
    for strat in doc["review"].get("strategies", []):
        lines.append(f"strategy {strat.get('id')}: sources {', '.join(strat.get('sources', []))}")
    _emit(as_json, doc, lines)


@protocol_group.command("check")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def protocol_check(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    try:
        doc = protocol.check(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"strategy {row['id']}: {row['status']}" + (f" ({row['reason']})" if row["reason"] else "")
             for row in doc["strategies"]]
    lines.append("Nothing was sent: this check only reads the locked protocol.")
    _emit(as_json, doc, lines)


@sr.command("status")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def status_cmd(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    state = lock.state(project, ws)
    doc = {
        "run": slug,
        "protocol": {"status": state.status, "version": state.version, "content_hash": state.in_force_hash},
        "committed": {},
        "uncommitted": [],
        "problems": [],
    }
    kinds: Counter = Counter()
    for row in events.read(ws, types=("sr.committed",)):
        kinds[str((row.get("data") or {}).get("kind"))] += 1
    doc["committed"] = dict(sorted(kinds.items()))
    try:
        doc["uncommitted"] = list(ledger.view(ws).uncommitted_ops)
    except SRError as exc:
        doc["problems"].append(str(exc))
    lines = [f"protocol: {state.status}" + (f" (version {state.version})" if state.version else "")]
    lines.append("committed: " + (", ".join(f"{k} {v}" for k, v in doc["committed"].items()) or "none"))
    if doc["uncommitted"]:
        lines.append("uncommitted operations (not read): " + ", ".join(map(str, doc["uncommitted"])))
    lines += [f"problem: {p}" for p in doc["problems"]]
    _emit(as_json, doc, lines)


@sr.command("search")
@click.argument("slug")
@click.option("--network", "network_mode", type=click.Choice(network.MODES), default=None,
              help="This run's network mode for the round; a flag can tighten it, never loosen it.")
@click.option("--as-agent", is_flag=True, help="Record the round as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def search_cmd(slug: str, network_mode: str | None, as_agent: bool, as_json: bool) -> None:
    """Run the next search round of the locked protocol (exit 3 refused query, 1 refused or partial)."""
    project = _project()
    ws = _run(project, slug)
    try:
        outcome = search.run_round(project, ws, slug=slug, network_mode=network_mode, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    except registry.LeakError as exc:
        _fail(REFUSED_QUERY, str(exc))
    if not outcome.ok:
        rows = [f"{row['source']}: {row['status']}" for row in outcome.summary.get("statuses", [])
                if row["status"] != "ok"]
        _fail(outcome.exit_code, "round not written; sources that did not answer: " + ", ".join(rows))
    doc = sanitize.sanitize_json_strings(outcome.summary)
    lines = [f"round {doc['round']}: identified {doc['identified']}, duplicates {doc['duplicates']}, "
             f"new records {doc['new_records']}"]
    lines += [f"warning: {w}" for w in doc["warnings"]]
    _emit(as_json, doc, lines)


@sr.command("records")
@click.argument("slug")
@click.option("--round", "round_no", type=int, default=None, help="Only this committed search round.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def records_cmd(slug: str, round_no: int | None, as_json: bool) -> None:
    """The corpus: records of the committed search rounds, in round order."""
    project = _project()
    ws = _run(project, slug)
    try:
        rows = sanitize.sanitize_json_strings(records.read_records(ws, round_no))
    except SRError as exc:
        _fail(REFUSED, str(exc))
    if as_json:
        click.echo(json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False))
        return
    for row in rows:
        click.echo(f"{row['id']}  {row.get('doi') or '-'}  {row.get('title') or ''}")


@sr.group(name="screen")
def screen_group() -> None:
    """Dual screening: prepare a batch, import the two screeners' outputs, status."""


@screen_group.command("prepare")
@click.argument("slug")
@click.option("--stage", required=True, type=click.Choice(screening.STAGES))
@click.option("--size", type=int, default=None, help="Records in the batch (default review.screening.batch_size).")
@click.option("--prioritise/--no-prioritise", default=None,
              help="Reorder the queue by similarity to included records (order only, nothing dropped).")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def screen_prepare(slug: str, stage: str, size: int | None, prioritise: bool | None,
                   as_agent: bool, as_json: bool) -> None:
    """Write one batch and its two prompts (nothing is dispatched)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = screening.prepare(project, ws, stage=stage, size=size, prioritise=prioritise, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"batch {doc['batch']} ({stage}): {len(doc['record_ids'])} records for {', '.join(doc['screeners'])}"]
    lines += [f"warning: {w}" for w in doc["warnings"]]
    lines += [f"prompt: workspace/{slug}/{p}" for p in doc["prompts"]]
    _emit(as_json, doc, lines)


@screen_group.command("import")
@click.argument("slug")
@click.option("--stage", required=True, type=click.Choice(screening.STAGES))
@click.option("--batch", "batch_id", required=True, help="The prepared batch id.")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def screen_import(slug: str, stage: str, batch_id: str, as_agent: bool, as_json: bool) -> None:
    """Import both screeners' outputs together, after every check passes (exit 1 otherwise)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = screening.import_batch(project, ws, stage=stage, batch=batch_id, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, doc, [f"imported batch {doc['batch']}: {doc['decisions']} decisions committed"])


@screen_group.command("status")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def screen_status_cmd(slug: str, as_json: bool) -> None:
    """Per stage: decided by both, disagreements, open batches, verdict counts and kappa (derived)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = screening.screen_status(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    doc = sanitize.sanitize_json_strings(doc)
    lines = [f"corpus {doc['corpus']} records; screeners: {', '.join(doc['screeners'] or []) or 'none'}"]
    if doc["problem"]:
        lines.append(f"problem: {doc['problem']}")
    for stage, row in doc["stages"].items():
        lines.append(f"{stage}: decided by both {row['decided_by_both']}, disagreements {row['disagreements']}, "
                     f"open batches {len(row['open_batches'])}, verdicts {row['verdicts']}")
        if row["stale_batches"]:
            lines.append(f"  stale batches (protocol changed; they block nothing, import refuses them): "
                         f"{', '.join(row['stale_batches'])}")
    _emit(as_json, doc, lines)


@sr.command("kappa")
@click.argument("slug")
@click.option("--stage", required=True, type=click.Choice(screening.STAGES))
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def kappa_cmd(slug: str, stage: str, as_json: bool) -> None:
    """Cohen's kappa between the two screeners, derived from the committed decisions."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = screening.kappa(ws, stage)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"n = {doc['n']}"]
    if doc["table"]:
        cats = sorted(set(doc["table"]) | {c for row in doc["table"].values() for c in row})
        lines.append(f"rows {doc['screener_ids'][0]}, columns {doc['screener_ids'][-1]}: " + ", ".join(cats))
        for row_cat in cats:
            lines.append(f"  {row_cat}: " + "  ".join(str(doc["table"].get(row_cat, {}).get(c, 0)) for c in cats))
    if doc["kappa"] is not None:
        lines.append(f"observed {doc['observed_agreement']:.6f}, expected {doc['expected_agreement']:.6f}, "
                     f"kappa {doc['kappa']:.6f}")
    else:
        lines.append("kappa undefined")
    if doc["note"]:
        lines.append(f"note: {doc['note']}")
    _emit(as_json, doc, lines)


@sr.command("conflicts")
@click.argument("slug")
@click.option("--stage", required=True, type=click.Choice(screening.STAGES))
@click.option("--as-agent", is_flag=True, help="Record the gate as opened by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def conflicts_cmd(slug: str, stage: str, as_agent: bool, as_json: bool) -> None:
    """Blinded conflicts of a stage: write them and open the human adjudication gate (exit 1 if refused)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = adjudication.open_conflicts(project, ws, stage=stage, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    if doc["gate"] is None:
        lines = ["no conflicts"]
    else:
        lines = [f"{doc['conflicts']} blinded {stage} conflicts; gate {doc['gate']} is open for a human answer"]
        lines += [f"file: workspace/{slug}/{path}" for path in doc["files"]]
    _emit(as_json, doc, lines)


@sr.command("adjudicate")
@click.argument("slug")
@click.argument("gate_id")
@click.argument("record_id")
@click.option("--decision", required=True, type=click.Choice(("include", "exclude")))
@click.option("--reason", default="", help="An exclude criterion id (a title-abstract exclude may leave it blank).")
@click.option("--rationale", required=True, help="Why, in 1 to 2000 characters.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def adjudicate_cmd(slug: str, gate_id: str, record_id: str, decision: str, reason: str, rationale: str,
                   as_json: bool) -> None:
    """Record a human decision on one blinded conflict (human only; refused inside a dispatch)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = adjudication.adjudicate(project, ws, gate_id=gate_id, record_id=record_id, decision=decision,
                                      reason=reason, rationale=rationale)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, doc, [f"{record_id}: {decision} recorded; {doc['remaining']} conflict(s) remain undecided"])


@sr.command("verify")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def verify_cmd(slug: str, as_json: bool) -> None:
    """Read-only integrity check of the committed sr ledger (exit 1 with errors; warnings never fail)."""
    project = _project()
    ws = _run(project, slug)
    doc = verify.verify(project, ws)
    state = "valid" if doc["ok"] else "INVALID"
    lines = [f"sr ledger {state}: {len(doc['errors'])} error(s), {len(doc['warnings'])} warning(s)"]
    lines += [f"error: {e}" for e in doc["errors"]] + [f"warning: {w}" for w in doc["warnings"]]
    _emit(as_json, doc, lines)
    if not doc["ok"]:
        sys.exit(REFUSED)


@sr.command("prisma")
@click.argument("slug")
@click.option("--write", "write_files", is_flag=True, help="Write sr/prisma/flow.json, flow.md and checklist.md.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def prisma_cmd(slug: str, write_files: bool, as_json: bool) -> None:
    """PRISMA 2020 flow and checklist, derived from the committed ledger (sr verify runs first)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = prisma.build(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    try:
        written = prisma.write(project, ws, doc) if write_files else []
    except SRError as exc:
        _fail(REFUSED, str(exc))
    flow = doc["flow"]
    lines = [f"identified {flow['identified']} ({', '.join(f'{k} {v}' for k, v in flow['identified_by_source'].items())}); "
             f"duplicates {flow['duplicates_removed']}; after duplicates {flow['records_after_dedup']}",
             f"screened {flow['records_screened']}; excluded {flow['records_excluded']}; "
             f"reports sought {flow['reports_sought_for_retrieval']}, not retrieved {flow['reports_not_retrieved']}, "
             f"assessed {flow['reports_assessed']}; excluded {flow['reports_excluded']}; "
             f"included {flow['studies_included']}"]
    lines += [f"written: workspace/{slug}/{path}" for path in written]
    lines += [f"warning: {w}" for w in doc.get("warnings") or []]
    _emit(as_json, doc, lines)


@sr.group(name="rob")
def rob_group() -> None:
    """Risk-of-bias worksheets (single assessor): init, check, record (plan 5.10)."""


@rob_group.command("init")
@click.argument("slug")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def rob_init(slug: str, as_agent: bool, as_json: bool) -> None:
    """One unjudged worksheet per included study; existing worksheets are never overwritten."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.init_rob(project, ws, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"created {len(doc['created'])} worksheet(s); {len(doc['existing'])} existing, left unchanged"]
    _emit(as_json, doc, lines)


@rob_group.command("check")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def rob_check(slug: str, as_json: bool) -> None:
    """Deterministic checks of every included study's worksheet (exit 1 with the errors)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.check_rob(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"{row['record_id']}: {row['status']}" for row in doc["studies"]]
    lines += [f"error: {e}" for e in doc["errors"]]
    _emit(as_json, doc, lines)
    if not doc["ok"]:
        sys.exit(REFUSED)


@rob_group.command("record")
@click.argument("slug")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def rob_record(slug: str, as_agent: bool, as_json: bool) -> None:
    """Record every included study's appraisal and commit the worksheets (exit 1 if any check fails)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.record_rob(project, ws, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, doc, [f"recorded {doc['appraisals']} risk-of-bias appraisal(s), operation {doc['op']}"])


@sr.group(name="grade")
def grade_group() -> None:
    """GRADE certainty: init, check, record, table (plan 5.10). Certainty is computed, never stored."""


@grade_group.command("init")
@click.argument("slug")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def grade_init(slug: str, as_agent: bool, as_json: bool) -> None:
    """One entry per SAP outcome of the locked design; never overwrites sr/grade.yml."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.init_grade(project, ws, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, doc, [f"wrote {doc['file']} with {len(doc['outcomes'])} outcome entr(ies)"])


@grade_group.command("check")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def grade_check(slug: str, as_json: bool) -> None:
    """Deterministic checks and the computed certainty of each outcome (exit 1 with the errors)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.check_grade(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"{row['outcome']}: certainty {row['certainty']}" for row in doc["entries"]]
    lines += [f"error: {e}" for e in doc["errors"]]
    _emit(as_json, doc, lines)
    if not doc["ok"]:
        sys.exit(REFUSED)


@grade_group.command("record")
@click.argument("slug")
@click.option("--as-agent", is_flag=True, help="Record the operation as written by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def grade_record(slug: str, as_agent: bool, as_json: bool) -> None:
    """Record each outcome's computed certainty and commit sr/grade.yml (exit 1 if the check fails)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.record_grade(project, ws, as_agent=as_agent)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, doc, [f"recorded {doc['outcomes']} outcome certainty(ies), operation {doc['op']}"])


@grade_group.command("table")
@click.argument("slug")
@click.option("--write", "write_file", is_flag=True, help="Write sr/grade/summary-of-findings.md.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def grade_table(slug: str, write_file: bool, as_json: bool) -> None:
    """The summary-of-findings table, derived from the check (--write renders the file)."""
    project = _project()
    ws = _run(project, slug)
    try:
        doc = appraisal.table(project, ws, write=write_file)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    if as_json:
        _emit(True, doc, [])
    else:
        click.echo(doc["markdown"], nl=False)
        if doc["file"]:
            click.echo(f"written: workspace/{slug}/{doc['file']}")


