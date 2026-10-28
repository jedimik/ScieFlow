"""Dedup gold set through the vendored dedupe (M-C1 acceptance; plan t1 item 5).

The gold file is a byte copy of researcher's evals/gold/dedup.yaml (210 labelled pairs: 110 same,
100 different, D15). Required: every 'same' pair merges, no 'different' pair merges. Synthetic
bibliographic records only; no patient data; offline.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scieflow._vendor.researcher_core.dedupe import dedupe
from scieflow._vendor.researcher_core.model import CSLRecord

GOLD = Path(__file__).resolve().parents[1] / "fixtures" / "kernel" / "dedup.yaml"


def _items() -> list[dict]:
    return yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]


def _pair(item: dict, side: str, index: int) -> CSLRecord:
    raw = item[side]
    return CSLRecord(
        id=f"{side}{index}",
        DOI=raw.get("doi") or "",
        title=raw.get("title") or "",
        source=raw.get("source") or "",
    )


def _confusion() -> dict[str, int]:
    counts = {"same_merged": 0, "same_kept": 0, "different_merged": 0, "different_kept": 0}
    for index, item in enumerate(_items()):
        merged = len(dedupe([_pair(item, "a", index), _pair(item, "b", index)]).records) == 1
        kind = "same" if item["label"] == "same" else "different"
        counts[f"{kind}_{'merged' if merged else 'kept'}"] += 1
    return counts


def test_gold_set_has_the_recorded_size_and_balance() -> None:
    items = _items()
    assert len(items) == 210
    assert sum(1 for item in items if item["label"] == "same") == 110
    assert sum(1 for item in items if item["label"] == "different") == 100


def test_every_same_pair_merges_and_no_different_pair_merges() -> None:
    counts = _confusion()
    assert counts == {
        "same_merged": 110,
        "same_kept": 0,
        "different_merged": 0,
        "different_kept": 100,
    }, counts
