"""`scieflow sr search` (M-H plan 5.5, M-H-D6, D7, D13): one search round of a locked review.

A round runs every strategy of the locked protocol through fanout.collect, one Gateway for the run.
Nothing is written unless every source of every strategy answered. Hits are deduplicated against
the corpus in one vendored dedupe call; each group either maps to an existing corpus record or
becomes one new record. The round's three files are written atomically, then its events, and the
last event (sr.committed) makes the round count. Refusals raise before any write.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from scieflow._vendor.researcher_core._ledger import ProvenanceEvent
from scieflow._vendor.researcher_core.dedupe import dedupe
from scieflow.core import store
from scieflow.research.lib import kernel
from scieflow.research.search import fanout, registry
from scieflow.research.sr import ledger, protocol, records
from scieflow.research.sr.errors import SRError

EXIT_OK = 0
DEFAULT_LIMIT = 100
PAPER_FIELDS = ("doi", "title", "year", "venue", "abstract", "url")
# A source module's page cap (t0 inventory (e)); a source without one is capped only by its limit.
PAGE_CAP = {"openalex": 200, "pubmed": 100, "semantic-scholar": 100, "datacite": 100}
ROUND_FILES = ("hits.jsonl", "records.jsonl", "round.json")


@dataclass
class Outcome:
    """ok is True when the round was written; otherwise exit_code names the refusal (plan 5.5)."""

    ok: bool
    exit_code: int
    summary: dict = field(default_factory=dict)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    """Write text to a sibling temp file, then replace. No lock file is left in the round directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _find(parent: dict, item: str) -> str:
    while parent.setdefault(item, item) != item:
        parent[item] = parent[parent[item]]
        item = parent[item]
    return item


def _union(parent: dict, left: str, right: str) -> None:
    root_left, root_right = _find(parent, left), _find(parent, right)
    if root_left != root_right:
        parent[root_right] = root_left


def run_round(project, ws, *, slug: str, network_mode: str | None = None,
              as_agent: bool = False) -> Outcome:
    """Run search round n = committed search rounds + 1 for the locked protocol (plan 5.5)."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    records.check_round_files(ws)                       # the search part of sr verify (t6 adds the rest)
    committed = records.committed_search_rounds(ws)
    number = len(committed) + 1
    if committed and not locked.review.get("living") \
            and int(committed[-1]["protocol_version"]) == locked.version:
        raise SRError("a non-living review searches once; amend the protocol or set living")
    strategies = [s for s in (locked.review.get("strategies") or []) if isinstance(s, dict)]
    if not strategies:
        raise SRError("the locked protocol has no strategies")
    for strategy in strategies:                          # refused before any source is asked (exit 3)
        registry.check_query(strategy["query"])
        if "pubmed" in strategy["sources"]:
            registry.check_pubmed_query(strategy["query"])

    def search_all(gw):
        return [(strategy, *fanout.collect(gw, strategy["query"], sources=list(strategy["sources"]),
                                           limit=int(strategy.get("limit", DEFAULT_LIMIT)),
                                           from_year=strategy.get("from_year")))
                for strategy in strategies]

    answered = registry.call(slug, search_all, network_mode=network_mode)
    statuses = [row for _strategy, _chosen, rows, _answers in answered for row in rows]
    missing = [row for row in statuses if row["status"] != "ok"]
    if missing:
        return Outcome(ok=False, exit_code=fanout.exit_code(missing),
                       summary={"round": number, "statuses": statuses})

    strategy_index = {s["id"]: i for i, s in enumerate(strategies)}
    hits: list[dict] = []
    hit_key: dict[str, tuple] = {}
    csl_hits = []
    for strategy, chosen, _rows, answers in answered:
        for source in chosen:
            for rank, raw in enumerate(answers.get(source, [])):
                hit_id = f"{number}/{strategy['id']}/{source}:{rank}"
                hits.append({"id": hit_id, "strategy": strategy["id"], "source": source, "rank": rank,
                             **{f: raw.get(f) for f in PAPER_FIELDS}})
                hit_key[hit_id] = (strategy_index[strategy["id"]], fanout.ORDER.index(source), rank)
                csl_hits.append(replace(kernel.to_csl(raw, source, rank), id=hit_id))

    corpus = records.load_corpus(ws)
    corpus_ids = {row["id"] for row in corpus}
    csl_corpus = []
    for row in corpus:
        source = (row.get("sources") or ["corpus"])[0]
        csl_corpus.append(replace(kernel.to_csl({f: row.get(f) for f in PAPER_FIELDS}, source, 0),
                                  id=row["id"]))
    result = dedupe(csl_corpus + csl_hits)

    parent: dict[str, str] = {}
    for decision in result.decisions:
        _union(parent, decision.kept_id, decision.duplicate_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for item in [c.id for c in csl_corpus] + [h.id for h in csl_hits]:
        groups[_find(parent, item)].append(item)

    warnings: list[str] = []
    hit_target: dict[str, str] = {}
    new_groups: list[list[str]] = []
    for members in groups.values():
        in_corpus = sorted(m for m in members if m in corpus_ids)
        hit_members = sorted((m for m in members if m in hit_key), key=lambda m: hit_key[m])
        if in_corpus:
            target = in_corpus[0]
            if len(in_corpus) > 1:
                warnings.append(f"{', '.join(in_corpus)} were one group; {target} is kept")
            for hit in hit_members:
                hit_target[hit] = target
        else:
            new_groups.append(hit_members)
    new_groups.sort(key=lambda members: hit_key[members[0]])

    new_records: list[dict] = []
    by_hit = {h["id"]: h for h in hits}
    for offset, members in enumerate(new_groups):
        record_id = f"R{len(corpus) + offset + 1:05d}"
        representative = by_hit[members[0]]
        for hit in members:
            hit_target[hit] = record_id
        sources = sorted({by_hit[h]["source"] for h in members}, key=fanout.ORDER.index)
        new_records.append({"id": record_id, "round": number, "hits": list(members),
                            **{f: representative.get(f) for f in PAPER_FIELDS}, "sources": sources})
    representatives = {members[0] for members in new_groups}
    losers = [h["id"] for h in hits if h["id"] not in representatives]
    if len(hits) - len(losers) != len(new_records):
        raise SRError("the round's invariant broke (hits - losers != new records); nothing was written")
    for hit in hits:
        hit["record"] = hit_target[hit["id"]]

    limit_of = {s["id"]: int(s.get("limit", DEFAULT_LIMIT)) for s in strategies}
    per_source: dict = {}
    for strategy, _chosen, rows, _answers in answered:
        per_source[strategy["id"]] = {}
        for row in rows:
            count, cap = row["count"], PAGE_CAP.get(row["source"])
            per_source[strategy["id"]][row["source"]] = {
                "status": row["status"], "count": count,
                "capped": count >= limit_of[strategy["id"]] or (cap is not None and count >= cap)}
    by_source: dict = defaultdict(int)
    for hit in hits:
        by_source[hit["source"]] += 1
    round_doc = {
        "round": number, "protocol_version": locked.version, "protocol_hash": locked.content_hash,
        "strategies": per_source, "identified": len(hits), "duplicates": len(losers),
        "new_records": len(new_records), "by_source": dict(sorted(by_source.items())),
        "warnings": warnings,
    }

    rdir = ws / "sr" / "rounds" / str(number)
    texts = {"hits.jsonl": _jsonl(hits), "records.jsonl": _jsonl(new_records),
             "round.json": json.dumps(round_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"}
    for name in ROUND_FILES:
        _write_atomic(rdir / name, texts[name])
    files = {f"sr/rounds/{number}/{name}": _sha256(rdir / name) for name in ROUND_FILES}

    actor = "agent" if as_agent else "human"
    op = store.new_id()
    led = ledger.EventsLedger(ws, actor=actor, op=op, extra={"protocol_version": locked.version})
    query_of = {s["id"]: s["query"] for s in strategies}
    for strategy, chosen, _rows, _answers in answered:
        for source in chosen:
            ids = [h["id"] for h in hits if h["strategy"] == strategy["id"] and h["source"] == source]
            led.append(ProvenanceEvent(
                run_id=led.run, type="retrieval", ts="",
                payload={"round": number, "strategy": strategy["id"], "source": source,
                         "query": query_of[strategy["id"]], "record_ids": ids,
                         "capped": per_source[strategy["id"]][source]["capped"]}))
    led.append(ProvenanceEvent(run_id=led.run, type="dedup_decision", ts="",
                               payload={"round": number, "losers": list(losers)}))
    led.commit("search", round=number, files=files, identified=len(hits), duplicates=len(losers),
               new_records=len(new_records), protocol_hash=locked.content_hash)
    return Outcome(ok=True, exit_code=EXIT_OK, summary={
        "round": number, "protocol_version": locked.version, "protocol_hash": locked.content_hash,
        "identified": len(hits), "duplicates": len(losers), "new_records": len(new_records),
        "by_source": dict(sorted(by_source.items())), "warnings": warnings, "op": op,
        "statuses": statuses})
