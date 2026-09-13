"""Batch claims into questions, read the answers, and render the audit report.

Answers from NotebookLM are DATA, never instructions (AGENTS.md rule 8): this
module parses them into a fixed vocabulary and quotes them, and does nothing
they say. A verdict that cannot be read becomes 'unparseable' — never a guess.

Advisory by design: nothing here fails a phase or blocks a draft.
"""

import re
from datetime import datetime, timezone

from nblm import ledger, policy

QUESTION_HEADER = """\
You are checking whether ONE source supports specific statements.

The only admissible evidence is the source with DOI {doi} (uploaded as
"{filename}"). Ignore every other source in this notebook, and ignore
anything you know from outside it.

For EACH statement below, answer on exactly one line, in this format:

CLAIM <id>: <{vocabulary}> | evidence: "<verbatim quote from the source, or NONE>" | locator: <section or page, or NONE>

Rules:
- supported: the source states this, or states something that entails it.
- partial: the source supports part of it, or a weaker version of it.
- not-addressed: the source simply does not discuss this.
- unsupported: the source discusses the topic but does not support this.
- contradicted: the source states something incompatible with this.
- A sentence in a paper often cites SEVERAL sources at once. Judge only the
  part of the statement this source is responsible for: if the source supports
  one clause but is silent on the others, that is 'partial', not
  'not-addressed'.
- Reserve 'not-addressed' for a source with nothing to do with ANY part of the
  statement.
- Quote verbatim from the source. If you cannot quote, the verdict is not
  'supported' and not 'partial'.
- Output only the CLAIM lines, nothing else.

Statements:
"""

LINE_RE = re.compile(r"^\s*(?:[-*>\s]*)CLAIM\s+([A-Za-z0-9_.-]+)\s*[:\-]\s*(.+)$",
                     re.I)
NONE_VALUES = {"", "none", "n/a", "na", "-", "null"}


class LimitError(Exception):
    """A quota ceiling in config/notebooklm.yml was reached (exit 4)."""


def build_question(claims: list[dict], doi: str, filename: str,
                   vocabulary: list[str]) -> str:
    askable = [v for v in vocabulary if v != "unparseable"]
    header = QUESTION_HEADER.format(
        doi=doi, filename=filename, vocabulary="|".join(askable)
    )
    body = "\n".join(f"CLAIM {c['id']}: {c['text']}" for c in claims)
    return header + body


def _clean_value(raw: str) -> str | None:
    value = (raw or "").strip().strip('"').strip("'").strip()
    if value.lower() in NONE_VALUES:
        return None
    return re.sub(r"\s+", " ", value)


def parse_answer(text: str, claims: list[dict], vocabulary: list[str]) -> dict:
    """Answer text -> {claim id: {verdict, evidence, locator, note}}."""
    wanted = {c["id"] for c in claims}
    found: dict[str, dict] = {}

    for line in (text or "").splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        claim_id, rest = match.group(1), match.group(2)
        if claim_id not in wanted or claim_id in found:
            continue
        parts = [p.strip() for p in rest.split("|")]
        verdict = parts[0].strip().strip(".").strip().lower()
        fields = {}
        for part in parts[1:]:
            if ":" in part:
                key, value = part.split(":", 1)
                fields[key.strip().lower()] = value
        if verdict not in vocabulary or verdict == "unparseable":
            found[claim_id] = {
                "verdict": "unparseable",
                "evidence": None,
                "locator": None,
                "note": f"unreadable verdict: {parts[0].strip()[:60]!r}",
            }
            continue
        found[claim_id] = _apply_evidence_rule({
            "verdict": verdict,
            "evidence": _clean_value(fields.get("evidence", "")),
            "locator": _clean_value(fields.get("locator", "")),
            "note": None,
        })

    for claim in claims:
        found.setdefault(claim["id"], {
            "verdict": "unparseable",
            "evidence": None,
            "locator": None,
            "note": "no CLAIM line for this id in the answer",
        })
    return found


def _apply_evidence_rule(payload: dict) -> dict:
    """Rule 9: a positive verdict without a quote is not evidence."""
    if payload["verdict"] in ("supported", "partial") and not payload["evidence"]:
        return {**payload,
                "verdict": "unsupported",
                "note": f"downgraded from '{payload['verdict']}': no quote given"}
    return payload


def rank(vocabulary: list[str], verdict: str) -> int:
    return vocabulary.index(verdict) if verdict in vocabulary else len(vocabulary)


def _spend_question(workspace, profile: policy.Profile) -> None:
    limits = profile.limits
    if ledger.questions_this_run(workspace) >= limits["max_questions_per_run"]:
        raise LimitError(
            f"max_questions_per_run ({limits['max_questions_per_run']}) reached "
            "for this run — partial results are saved"
        )
    if ledger.questions_today(workspace) >= limits["max_questions_per_day"]:
        raise LimitError(
            f"max_questions_per_day ({limits['max_questions_per_day']}) reached "
            "— resume tomorrow (UTC); partial results are saved"
        )
    ledger.record_question(workspace, 1)


def _source_ids(source: dict) -> list | None:
    """Scope the question natively. Without this the model may answer from a
    different paper in the same notebook — a false 'supported'."""
    source_id = source.get("source_id")
    return [source_id] if source_id else None


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def run(session, workspace, profile: policy.Profile, notebook_id: str,
        claims: list[dict], vocabulary: list[str]) -> dict:
    """Verify `claims`. Returns a summary; raises LimitError when quota runs out."""
    summary = {"cached": 0, "asked": 0, "reasked": 0, "verified": 0,
               "skipped": [], "limit": None}
    pending: dict[str, list] = {}

    for claim in claims:
        if ledger.get_verdict(workspace, claim["text"], claim["doi"]):
            summary["cached"] += 1
            continue
        source = ledger.source_for(workspace, claim["doi"])
        if not source:
            summary["skipped"].append(
                {"id": claim["id"], "doi": claim["doi"],
                 "reason": "no source uploaded for this DOI"})
            continue
        pending.setdefault(claim["doi"], []).append(claim)

    threshold = rank(vocabulary, profile.limits["reask_threshold"])
    doubtful: list[dict] = []

    try:
        for doi, group in pending.items():
            source = ledger.source_for(workspace, doi)
            filename = source.get("file", doi)
            for batch in _chunks(group, profile.limits["max_claims_per_question"]):
                _spend_question(workspace, profile)
                summary["asked"] += 1
                answer = session.ask(
                    notebook_id, build_question(batch, doi, filename, vocabulary),
                    source_ids=_source_ids(source))
                parsed = parse_answer(answer.text, batch, vocabulary)
                for claim in batch:
                    payload = parsed[claim["id"]]
                    _record(workspace, claim, payload, notebook_id, source)
                    summary["verified"] += 1
                    if rank(vocabulary, payload["verdict"]) >= threshold:
                        doubtful.append(claim)

        for claim in doubtful:
            _spend_question(workspace, profile)
            summary["reasked"] += 1
            source = ledger.source_for(workspace, claim["doi"])
            answer = session.ask(
                notebook_id,
                build_question([claim], claim["doi"],
                               source.get("file", claim["doi"]), vocabulary),
                source_ids=_source_ids(source))
            payload = parse_answer(answer.text, [claim], vocabulary)[claim["id"]]
            _record(workspace, claim, {**payload, "reasked": True},
                    notebook_id, source)
    except LimitError as exc:
        summary["limit"] = str(exc)
    return summary


def _record(workspace, claim: dict, payload: dict, notebook_id: str,
            source: dict) -> None:
    ledger.record_verdict(workspace, claim, {
        **payload,
        "notebook_id": notebook_id,
        "source_id": source.get("source_id"),
        "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def render_report(workspace, vocabulary: list[str], slug: str = "") -> str:
    """The advisory audit report. Worst verdicts first."""
    rows = ledger.all_verdicts(workspace)
    notebook = ledger.load_notebook(workspace)
    quota = ledger.load_quota(workspace)
    counts = {v: 0 for v in vocabulary}
    for row in rows:
        counts[row.get("verdict", "unparseable")] = \
            counts.get(row.get("verdict", "unparseable"), 0) + 1

    lines = [f"# Citation audit — {slug or workspace.name}", ""]
    lines += ["## Summary", "",
              "Advisory only: these verdicts do not block any phase.", "",
              "| verdict | claims |", "|---|---|"]
    for verdict in reversed(vocabulary):
        lines.append(f"| {verdict} | {counts.get(verdict, 0)} |")
    lines += ["", f"Total claims checked: {len(rows)}", "", "## Findings", ""]

    if not rows:
        lines += ["No claims checked yet.", ""]
    for verdict in reversed(vocabulary):
        group = [r for r in rows if r.get("verdict") == verdict]
        if not group:
            continue
        lines += [f"### {verdict} ({len(group)})", ""]
        for row in sorted(group, key=lambda r: str(r.get("id"))):
            where = f"{row.get('file')}:{row.get('line')}"
            lines.append(f"- **{row.get('id')}** · `{where}` · {row.get('doi')}")
            lines.append(f"  - claim: {row.get('text')}")
            evidence = row.get("evidence")
            lines.append(f"  - evidence: {'“' + evidence + '”' if evidence else '—'}"
                         f" ({row.get('locator') or 'no locator'})")
            if row.get("note"):
                lines.append(f"  - note: {row['note']}")
            if row.get("reasked"):
                lines.append("  - re-asked individually")
        lines.append("")

    lines += ["## Provenance", "",
              f"- notebook: `{notebook.get('title')}` (`{notebook.get('notebook_id')}`)",
              f"- sources uploaded: {len(notebook.get('sources') or [])}",
              f"- questions spent this run: {quota.get('run_total', 0)}",
              "- source of every verdict: NotebookLM answers, treated as data",
              ""]
    return "\n".join(lines)
