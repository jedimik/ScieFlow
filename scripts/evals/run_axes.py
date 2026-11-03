#!/usr/bin/env python3
"""Developer evaluation harness for the systematic-review vertical (M-H t8, plan 5.11 and 5.12).

Offline and deterministic: it reads only repository fixtures (tests/fixtures/kernel/dedup.yaml and
tests/fixtures/sr/*), imports scieflow from this checkout and never touches the network.

Axes:
  dedup      the 210-pair gold set through the vendored dedupe (110 same must merge, 100 different must not)
  kappa      the 2x2 tables of tests/fixtures/sr/kappa.yml reproduce their exact kappa values
  prisma     the round-1 and round-2 scenario of plan 5.12 replayed on a MemoryLedger through the vendored
             PRISMA derivation, compared with tests/fixtures/sr/prisma-scenario.yml
  screening  the calibration sample: sensitivity, specificity, accuracy of screeners A and B, with Wilson
             intervals, compared with the hand counts in tests/fixtures/sr/screening-calibration.yml

Exit 0 when every requested axis meets its gate, 1 otherwise. The first line of each axis names the axis.

usage: run_axes.py [--axis dedup|kappa|prisma|screening|all] [--json] [--fixtures DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from scieflow._vendor.researcher_core._ledger import MemoryLedger, ProvenanceEvent  # noqa: E402
from scieflow._vendor.researcher_core.dedupe import dedupe  # noqa: E402
from scieflow._vendor.researcher_core.model import CSLRecord  # noqa: E402
from scieflow._vendor.researcher_core.sr_prisma import prisma_flow  # noqa: E402
from scieflow.research.evals import Confusion, cohens_kappa_2x2, format_interval, format_metric, wilson  # noqa: E402

AXES = ("dedup", "kappa", "prisma", "screening")
DEFAULT_FIXTURES = REPO / "tests" / "fixtures"
RUN_ID = "axis-replay"
REPLAY_TS = "1970-01-01T00:00:00+00:00"  # a fixed timestamp text: a replay never reads the clock
FLOW_KEYS = ("identified", "duplicates_removed", "records_after_dedup", "records_screened", "records_excluded",
             "reports_sought_for_retrieval", "reports_not_retrieved", "reports_assessed", "reports_excluded",
             "studies_included")
TA, FT = "title-abstract", "full-text"


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# ------------------------------------------------------------------ dedup

def _csl(raw: dict, side: str, index: int) -> CSLRecord:
    return CSLRecord(id=f"{side}{index}", DOI=raw.get("doi") or "", title=raw.get("title") or "",
                     source=raw.get("source") or "")


def dedup_axis(fixtures: Path) -> dict:
    items = yaml.safe_load((fixtures / "kernel" / "dedup.yaml").read_text(encoding="utf-8"))["items"]
    counts = {"same_merged": 0, "same_kept": 0, "different_merged": 0, "different_kept": 0}
    for index, item in enumerate(items):
        merged = len(dedupe([_csl(item["a"], "a", index), _csl(item["b"], "b", index)]).records) == 1
        kind = "same" if item["label"] == "same" else "different"
        counts[f"{kind}_{'merged' if merged else 'kept'}"] += 1
    same_total = counts["same_merged"] + counts["same_kept"]
    different_total = counts["different_merged"] + counts["different_kept"]
    same_rate = wilson(counts["same_merged"], same_total)
    false_merge = wilson(counts["different_merged"], different_total)
    ok = counts["same_kept"] == 0 and counts["different_merged"] == 0
    line = (f"dedup: same-pair merge rate {format_interval(same_rate)}; "
            f"different-pair false merges {format_interval(false_merge)}: {_status(ok)}")
    return {"axis": "dedup", "pass": ok, "counts": counts, "lines": [line]}


# ------------------------------------------------------------------ kappa

def kappa_axis(fixtures: Path) -> dict:
    rows = yaml.safe_load((fixtures / "sr" / "kappa.yml").read_text(encoding="utf-8"))["rows"]
    details, failing = [], []
    for row in rows:
        value = cohens_kappa_2x2(row["a"], row["b"], row["c"], row["d"])
        expected = None if row["kappa"] is None else Fraction(row["kappa"])
        if value != expected:
            failing.append(row["id"])
        shown = "undefined" if value is None else f"{value} = {float(value):.6f}"
        details.append(f"  kappa row {row['id']} (a={row['a']} b={row['b']} c={row['c']} d={row['d']}): "
                       f"{shown}, expected {row['kappa'] or 'undefined'}: {_status(value == expected)}")
    ok = not failing
    summary = (f"kappa: {len(rows) - len(failing)} of {len(rows)} tables reproduce exactly: {_status(ok)}")
    return {"axis": "kappa", "pass": ok, "lines": [summary, *details]}


# ------------------------------------------------------------------ prisma

def _scenario(ledger: MemoryLedger, round_no: int) -> None:
    """The plan 5.12 scenario, appended as ledger events. Round 1 is the search, the dedup, the title-abstract
    and full-text screening with the human adjudications; round 2 only adds identification."""
    def add(type_: str, payload: dict) -> None:
        ledger.append(ProvenanceEvent(run_id=RUN_ID, type=type_, ts=REPLAY_TS, payload=payload))

    def decide(stage: str, rid: str, verdict: str, *, reason: str = "", screener: str = "") -> None:
        add("screening_decision", {"record_id": rid, "stage": stage, "verdict": verdict, "reason": reason,
                                   "screener_id": screener})

    if round_no == 2:
        add("retrieval", {"source": "openalex", "strategy": "s1", "record_ids": ["d1", "d2", "n1", "n2", "n3"]})
        add("dedup_decision", {"losers": ["d1", "d2"]})
        return

    add("retrieval", {"source": "openalex", "strategy": "s1", "record_ids": [f"o{i}" for i in range(1, 31)]})
    add("retrieval", {"source": "europepmc", "strategy": "s1", "record_ids": [f"e{i}" for i in range(1, 21)]})
    add("retrieval", {"source": "crossref", "strategy": "s2", "record_ids": [f"c{i}" for i in range(1, 11)]})
    add("dedup_decision", {"losers": [f"e{i}" for i in range(1, 9)] + [f"c{i}" for i in range(1, 5)]})

    # Title-abstract: both screeners decide all 48 records (codex = A, claude = B).
    for rid in [f"o{i}" for i in range(1, 11)]:
        decide(TA, rid, "include", screener="codex")
        decide(TA, rid, "include", screener="claude")
    for rid in [f"o{i}" for i in range(11, 31)] + [f"e{i}" for i in range(9, 19)]:
        decide(TA, rid, "exclude", screener="codex")
        decide(TA, rid, "exclude", screener="claude")
    conflicts = {"e19": ("include", "exclude"), "e20": ("include", "exclude"), "c5": ("include", "exclude"),
                 "c6": ("include", "exclude"), "c7": ("include", "exclude"), "c8": ("exclude", "include"),
                 "c9": ("exclude", "include"), "c10": ("exclude", "include")}
    for rid, (a, b) in conflicts.items():
        decide(TA, rid, a, screener="codex")
        decide(TA, rid, b, screener="claude")
    for rid in ("e19", "e20", "c5", "c6"):
        add("adjudication", {"record_id": rid, "stage": TA, "verdict": "include", "reason": ""})
    for rid in ("c7", "c8", "c9", "c10"):
        add("adjudication", {"record_id": rid, "stage": TA, "verdict": "exclude", "reason": ""})

    # Full-text: 14 sought, c6 has no report, so 13 are assessed.
    for rid in [f"o{i}" for i in range(1, 6)]:
        decide(FT, rid, "include", screener="codex")
        decide(FT, rid, "include", screener="claude")
    for rid in ("o6", "o7", "o8", "o9"):
        decide(FT, rid, "exclude", reason="e1", screener="codex")
        decide(FT, rid, "exclude", reason="e1", screener="claude")
    for rid in ("o10", "e19"):
        decide(FT, rid, "exclude", reason="e2", screener="codex")
        decide(FT, rid, "exclude", reason="e2", screener="claude")
    decide(FT, "e20", "include", screener="codex")
    decide(FT, "e20", "exclude", reason="e2", screener="claude")
    decide(FT, "c5", "exclude", reason="e1", screener="codex")
    decide(FT, "c5", "include", screener="claude")
    add("adjudication", {"record_id": "e20", "stage": FT, "verdict": "exclude", "reason": "e2"})
    add("adjudication", {"record_id": "c5", "stage": FT, "verdict": "include", "reason": ""})


def prisma_flows() -> list[dict]:
    """[round 1, round 2] flows as JSON dicts, derived by the vendored PRISMA code from a MemoryLedger."""
    ledger = MemoryLedger()
    _scenario(ledger, 1)
    round1 = prisma_flow(ledger, RUN_ID).to_json_dict()
    _scenario(ledger, 2)
    round2 = prisma_flow(ledger, RUN_ID).to_json_dict()
    return [round1, round2]


def prisma_axis(fixtures: Path) -> dict:
    gold = yaml.safe_load((fixtures / "sr" / "prisma-scenario.yml").read_text(encoding="utf-8"))
    flows = prisma_flows()
    failing = []
    for label, flow, expected in (("round 1", flows[0], gold["round1"]), ("round 2", flows[1], gold["round2"])):
        for key in FLOW_KEYS:
            if flow[key] != expected[key]:
                failing.append(f"{label} {key}")
    if flows[0]["identified_by_source"] != gold["round1"]["identified_by_source"]:
        failing.append("round 1 identified_by_source")
    if flows[0]["reports_excluded_by_reason"] != gold["round1"]["reports_excluded_by_reason"]:
        failing.append("round 1 reports_excluded_by_reason")
    ok = not failing
    r1 = flows[0]
    line = (f"prisma: round 1 identified {r1['identified']}, duplicates {r1['duplicates_removed']}, "
            f"screened {r1['records_screened']}, sought {r1['reports_sought_for_retrieval']}, "
            f"assessed {r1['reports_assessed']}, included {r1['studies_included']}; "
            f"round 2 identified {flows[1]['identified']}, after dedup {flows[1]['records_after_dedup']}: "
            f"{_status(ok)}")
    detail = [f"  prisma mismatch: {item}" for item in failing]
    return {"axis": "prisma", "pass": ok, "lines": [line, *detail]}


# ------------------------------------------------------------------ screening

def screening_axis(fixtures: Path) -> dict:
    doc = yaml.safe_load((fixtures / "sr" / "screening-calibration.yml").read_text(encoding="utf-8"))
    records = doc["records"]
    lines, ok = [], True
    for name, column in (("A", "codex"), ("B", "claude")):
        confusion = Confusion.from_pairs([(row["gold"], row[column]) for row in records])
        exp = doc["expected"][name]
        counts_ok = (confusion.tp, confusion.fn, confusion.fp, confusion.tn) == (
            exp["tp"], exp["fn"], exp["fp"], exp["tn"])
        ok = ok and counts_ok
        metrics = "; ".join(format_metric(m, getattr(confusion, m)) for m in ("sensitivity", "specificity", "accuracy"))
        lines.append(f"screening {name} ({column}): TP {confusion.tp} FN {confusion.fn} FP {confusion.fp} "
                     f"TN {confusion.tn}: {metrics}: {_status(counts_ok)}")
    return {"axis": "screening", "pass": ok, "lines": lines}


RUNNERS = {"dedup": dedup_axis, "kappa": kappa_axis, "prisma": prisma_axis, "screening": screening_axis}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline evaluation harness for the systematic-review vertical.")
    parser.add_argument("--axis", choices=(*AXES, "all"), default="all")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES,
                        help="Fixture root holding kernel/ and sr/ (default: tests/fixtures).")
    args = parser.parse_args(argv)
    names = AXES if args.axis == "all" else (args.axis,)
    results = [RUNNERS[name](args.fixtures) for name in names]
    ok = all(result["pass"] for result in results)
    if args.json:
        print(json.dumps({"pass": ok, "axes": results}, indent=2, sort_keys=True, default=str))
    else:
        for result in results:
            for line in result["lines"]:
                print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
