"""The ScieFlow-authored ledger interface the vendored screening and PRISMA code runs on (M-H t1).

``_ledger.py`` replaces researcher's SQLite provenance ledger for the vendored modules: a frozen
event record, a run context, the abstract ledger base (which refuses to write or read, naming the
ScieFlow replacement) and a list-backed ``MemoryLedger`` for tests and the harness. These tests pin
the interface, the determinism of ids and order, the import hygiene of the package, and the
hand-checked Cohen's kappa rows of plan section 5.12 computed through the vendored ``cohens_kappa``.
Offline; every ts is a caller-supplied string (D15).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core._ledger import (
    MemoryLedger,
    ProvenanceEvent,
    ProvenanceLedger,
    RunContext,
)
from scieflow._vendor.researcher_core.prisma_counts import PrismaCounts, derive_prisma
from scieflow._vendor.researcher_core.screen import (
    EXCLUDE,
    INCLUDE,
    cohens_kappa,
    record_screening_decision,
)

PKG = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "_vendor" / "researcher_core"
RUN_ID = "run-interface"


def ts(step: int) -> str:
    return f"2026-07-16T12:00:{step:02d}Z"


def test_run_context_merges_extra_into_every_payload() -> None:
    run = RunContext(run_id=RUN_ID, extra={"batch": "b1", "gate": "g1"})
    event = run.event("screening_decision", {"record_id": "R00001", "decision": "include"}, ts(0))
    assert event.payload == {
        "record_id": "R00001",
        "decision": "include",
        "batch": "b1",
        "gate": "g1",
    }
    assert event.run_id == RUN_ID and event.type == "screening_decision"
    assert event.ts == ts(0)
    assert event.source_response_hashes == ()


def test_run_context_retrieval_and_dedup_payloads() -> None:
    run = RunContext(run_id=RUN_ID)
    hit = run.retrieval(ts(1), source="openalex", query="q", record_ids=["a", "b"])
    assert hit.type == "retrieval"
    assert hit.payload == {"source": "openalex", "query": "q", "record_count": 2, "record_ids": ["a", "b"]}
    dup = run.dedup_decision(ts(2), winner="a", losers=["b"], reason="doi_exact")
    assert dup.type == "dedup_decision"
    assert dup.payload == {"winner": "a", "losers": ["b"], "reason": "doi_exact"}


def test_base_ledger_refuses_to_append_or_read_and_names_the_replacement() -> None:
    base = ProvenanceLedger()
    event = RunContext(run_id=RUN_ID).event("retrieval", {"source": "s"}, ts(0))
    with pytest.raises(NotImplementedError, match="scieflow.research.sr.ledger"):
        base.append(event)
    with pytest.raises(NotImplementedError, match="scieflow.research.sr.ledger"):
        list(base.iter_events(run_id=RUN_ID))


def test_memory_ledger_keeps_append_order_and_filters_by_run_and_type() -> None:
    ledger = MemoryLedger()
    run = RunContext(run_id=RUN_ID)
    other = RunContext(run_id="run-other")
    stored = [
        ledger.append(run.event("retrieval", {"source": "a"}, ts(0))),
        ledger.append(other.event("retrieval", {"source": "b"}, ts(1))),
        ledger.append(run.event("screening_decision", {"record_id": "R1"}, ts(2))),
    ]
    assert [e.type for e in ledger.events()] == ["retrieval", "retrieval", "screening_decision"]
    assert [e.payload["source"] for e in ledger.events(run_id=RUN_ID, type="retrieval")] == ["a"]
    assert len(ledger.events(run_id="run-other")) == 1
    assert all(isinstance(e, ProvenanceEvent) for e in stored)


def test_memory_ledger_ids_are_deterministic_and_a_given_id_is_kept() -> None:
    def build() -> list[str]:
        ledger = MemoryLedger()
        run = RunContext(run_id=RUN_ID)
        ledger.append(run.event("retrieval", {"source": "a"}, ts(0)))
        ledger.append(run.event("retrieval", {"source": "b"}, ts(1)))
        return [e.event_id for e in ledger.events()]

    first, second = build(), build()
    assert first == second
    assert all(len(event_id) == 64 for event_id in first)
    assert len(set(first)) == 2
    ledger = MemoryLedger()
    kept = ledger.append(
        RunContext(run_id=RUN_ID).event("retrieval", {"source": "a"}, ts(0), event_id="given-1")
    )
    assert kept.event_id == "given-1"


def test_memory_ledger_prisma_counts_are_derived_from_its_events() -> None:
    ledger = MemoryLedger()
    run = RunContext(run_id=RUN_ID)
    ledger.append(run.retrieval(ts(0), source="openalex", query="q", record_ids=["a", "b", "c"]))
    ledger.append(run.dedup_decision(ts(1), winner="a", losers=["b"], reason="doi_exact"))
    counts = ledger.prisma(run_id=RUN_ID)
    assert isinstance(counts, PrismaCounts)
    assert counts == derive_prisma(ledger.events(run_id=RUN_ID), run_id=RUN_ID)
    assert (counts.identified, counts.duplicates_removed, counts.deduplicated) == (3, 1, 2)


def test_package_modules_import_only_the_standard_library_and_their_own_package() -> None:
    allowed_roots = {"__future__", "collections", "dataclasses", "datetime", "hashlib", "json", "typing"}
    tree = ast.parse((PKG / "_ledger.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_roots, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                assert (node.module or "").split(".")[0] in allowed_roots, node.module
            else:
                assert node.level == 1 and node.module == "prisma_counts", node.module


def test_vendored_package_never_imports_scieflow_core() -> None:
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "scieflow" in node.module:
                assert not node.module.startswith("scieflow.core"), path.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("scieflow.core"), path.name


# Plan section 5.12, rows 1-5. Cells: a = both include, b = A include / B exclude,
# c = A exclude / B include, d = both exclude. Expected kappa is exact (fractions).
KAPPA_ROWS = [
    ((20, 5, 10, 15), 0.4),
    ((40, 10, 5, 45), 0.7),
    ((10, 5, 3, 30), 95 / 159),
    ((5, 1, 1, 6), 58 / 84),
]


def _seed_table(ledger: MemoryLedger, table: tuple[int, int, int, int]) -> None:
    run = RunContext(run_id=RUN_ID)
    a, b, c, d = table
    cells = (
        ([INCLUDE, INCLUDE], a),
        ([INCLUDE, EXCLUDE], b),
        ([EXCLUDE, INCLUDE], c),
        ([EXCLUDE, EXCLUDE], d),
    )
    step = 0
    for decisions, count in cells:
        for _ in range(count):
            record = f"r{step}"
            for screener, decision in zip(("alice", "bob"), decisions):
                record_screening_decision(
                    ledger,
                    run,
                    screener_id=screener,
                    record_id=record,
                    stage="title-abstract",
                    decision=decision,
                    ts=ts(step % 60),
                    reason="",
                )
            step += 1


@pytest.mark.parametrize("table, expected", KAPPA_ROWS)
def test_kappa_rows_match_the_hand_computation(table, expected) -> None:
    ledger = MemoryLedger()
    _seed_table(ledger, table)
    result = cohens_kappa(ledger, RUN_ID, "title-abstract")
    assert result.n == sum(table)
    assert result.kappa is not None
    assert abs(result.kappa - expected) < 1e-12


def test_kappa_row_five_is_undefined_and_says_so() -> None:
    ledger = MemoryLedger()
    _seed_table(ledger, (10, 0, 0, 0))
    result = cohens_kappa(ledger, RUN_ID, "title-abstract")
    assert result.kappa is None
    assert result.observed_agreement == 1.0
    assert result.note
