"""Tests for living-review monitoring: saved searches and diff-on-rerun (M4.11).

The acceptance properties the milestone rests on are asserted directly here:

* a second rerun after seeding reports ONLY unseen records;
* the seen-id list grows monotonically and never shrinks or reorders;
* ``ts`` is caller-supplied (the module reads no clock), verified both by asserting the
  stored timestamp equals what was passed and by scanning the source for a clock call;
* the state round-trips byte-for-byte through save/load.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_monitor.py. Imports are rewritten to
# scieflow._vendor.researcher_core. The two retrieval_events tests are not ported (they need
# upstream RunContext and ProvenanceEvent; M-C2 t1 plan list). See VENDORED.md.

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core.model import CSLRecord
from scieflow._vendor.researcher_core.monitor import (
    MonitorError,
    MonitorState,
    SearchStrategy,
    default_monitor_path,
    diff_new,
    extract_ids,
    load_monitor_state,
    record_id,
    run_monitor,
    save_monitor_state,
    update_state,
)
from scieflow._vendor.researcher_core.provenance_ts import ProvenanceError

TS1 = "2026-07-14T12:00:00Z"
TS2 = "2026-07-15T12:00:00Z"
TS3 = "2026-07-16T12:00:00Z"

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "scieflow" / "_vendor" / "researcher_core" / "monitor.py"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_state() -> MonitorState:
    return MonitorState(
        monitor_id="ecg-ssl",
        strategies=(
            SearchStrategy(source="openalex", query="self-supervised ECG"),
            SearchStrategy(source="arxiv", query="cat:eess.SP self-supervised ECG"),
        ),
    )


def executor_from(mapping: dict[str, list]):
    """A strategy executor that returns canned records keyed by source. Offline."""

    def run(strategy: SearchStrategy):
        return mapping.get(strategy.source, [])

    return run


# ---------------------------------------------------------------------------
# diff_new
# ---------------------------------------------------------------------------


def test_diff_new_reports_only_unseen_order_stable() -> None:
    assert diff_new(["a", "b", "c", "d"], ["b", "d"]) == ["a", "c"]


def test_diff_new_deduplicates_current() -> None:
    assert diff_new(["a", "a", "b", "a"], []) == ["a", "b"]


def test_diff_new_ignores_blank_ids() -> None:
    assert diff_new(["a", "", "  ", "b"], []) == ["a", "b"]


def test_diff_new_empty_when_all_seen() -> None:
    assert diff_new(["a", "b"], ["a", "b", "c"]) == []


# ---------------------------------------------------------------------------
# id extraction
# ---------------------------------------------------------------------------


def test_record_id_accepts_str_mapping_and_object() -> None:
    assert record_id("W123") == "W123"
    assert record_id({"id": "W456"}) == "W456"
    assert record_id({"DOI": "10.1/x"}) == "10.1/x"
    assert record_id(CSLRecord(id="W789")) == "W789"


def test_extract_ids_order_stable_and_deduplicated() -> None:
    records = ["W1", {"id": "W2"}, "W1", CSLRecord(id="W3")]
    assert extract_ids(records) == ["W1", "W2", "W3"]


# ---------------------------------------------------------------------------
# The rerun: only unseen records after seeding
# ---------------------------------------------------------------------------


def test_second_run_reports_only_unseen() -> None:
    state = make_state()

    first = run_monitor(
        state,
        executor_from({"openalex": ["a", "b", "c"], "arxiv": ["c", "d"]}),
        ts=TS1,
    )
    assert set(first.new_ids) == {"a", "b", "c", "d"}
    assert first.current_ids == ("a", "b", "c", "d")

    second = run_monitor(
        first.state,
        executor_from({"openalex": ["a", "b", "c", "e"], "arxiv": ["d", "f"]}),
        ts=TS2,
    )
    # Only the records unseen at the start of the second run are reported.
    assert list(second.new_ids) == ["e", "f"]
    # And they are reported in the order the strategies surfaced them.
    assert second.current_ids == ("a", "b", "c", "e", "d", "f")


def test_third_run_with_no_new_records_reports_empty() -> None:
    state = make_state()
    first = run_monitor(state, executor_from({"openalex": ["a", "b"]}), ts=TS1)
    second = run_monitor(first.state, executor_from({"openalex": ["a", "b"]}), ts=TS2)
    assert second.new_ids == ()
    assert second.state is not None
    assert second.state.seen_ids == ("a", "b")


# ---------------------------------------------------------------------------
# Monotonic growth of the seen list
# ---------------------------------------------------------------------------


def test_seen_list_grows_monotonically() -> None:
    state = make_state()
    assert state.seen_ids == ()

    runs = [
        {"openalex": ["a", "b"]},
        {"openalex": ["a", "b", "c"]},
        {"openalex": ["a", "b", "c", "d"]},
    ]
    tss = [TS1, TS2, TS3]
    sizes: list[int] = []
    for mapping, ts in zip(runs, tss, strict=True):
        rerun = run_monitor(state, executor_from(mapping), ts=ts)
        assert rerun.state is not None
        # The new state's seen list is a prefix-extension of the previous one.
        assert rerun.state.seen_ids[: len(state.seen_ids)] == state.seen_ids
        state = rerun.state
        sizes.append(len(state.seen_ids))

    assert sizes == [2, 3, 4]
    assert state.seen_ids == ("a", "b", "c", "d")
    assert state.run_count == 3


def test_update_state_never_shrinks_or_reorders() -> None:
    state = MonitorState(monitor_id="m", seen_ids=("a", "b"))
    grown = update_state(state, ["b", "c", "a", "d"], TS1)
    # Existing ids keep their order; only genuinely new ids append, in order.
    assert grown.seen_ids == ("a", "b", "c", "d")
    # The input state is untouched (immutable transition).
    assert state.seen_ids == ("a", "b")


# ---------------------------------------------------------------------------
# ts is caller-supplied, never from the clock
# ---------------------------------------------------------------------------


def test_ts_is_caller_supplied_verbatim() -> None:
    state = make_state()
    rerun = run_monitor(state, executor_from({"openalex": ["a"]}), ts=TS1)
    assert rerun.ts == TS1
    assert rerun.state is not None
    assert rerun.state.last_run == TS1


def test_update_state_rejects_a_non_timestamp() -> None:
    state = make_state()
    with pytest.raises(ProvenanceError):
        update_state(state, ["a"], "not-a-timestamp")


def test_module_makes_no_clock_call() -> None:
    """No time.time / datetime.now anywhere in the module (D15 replay determinism)."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    banned = {"time", "monotonic", "now", "today", "utcnow"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            raise AssertionError(f"monitor.py reads the clock via .{node.attr}()")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"time", "datetime"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"time", "datetime"}, node.module


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


def test_state_round_trips_through_save_load(tmp_path: Path) -> None:
    state = make_state()
    rerun = run_monitor(
        state,
        executor_from({"openalex": ["a", "b"], "arxiv": ["c"]}),
        ts=TS1,
    )
    assert rerun.state is not None
    path = save_monitor_state(tmp_path / "monitoring.json", rerun.state)

    loaded = load_monitor_state(path)
    assert loaded.to_json_dict() == rerun.state.to_json_dict()
    assert loaded.seen_ids == ("a", "b", "c")
    assert loaded.last_run == TS1
    assert loaded.strategies == rerun.state.strategies


def test_serialize_is_stable_across_two_saves(tmp_path: Path) -> None:
    state = make_state()
    grown = update_state(state, ["a", "b", "c"], TS1)
    first = save_monitor_state(tmp_path / "one.json", grown).read_text(encoding="utf-8")
    second = save_monitor_state(tmp_path / "two.json", grown).read_text(encoding="utf-8")
    assert first == second
    # seen_ids order is preserved on disk (not sorted), because the diff is order-stable.
    on_disk = json.loads(first)
    assert on_disk["seen_ids"] == ["a", "b", "c"]


def test_load_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(MonitorError):
        load_monitor_state(path)


def test_default_monitor_path_is_json_under_manuscript() -> None:
    assert default_monitor_path("manuscript") == Path("manuscript") / "monitoring.json"


# ---------------------------------------------------------------------------
# State validation
# ---------------------------------------------------------------------------


def test_state_requires_monitor_id() -> None:
    with pytest.raises(MonitorError):
        MonitorState(monitor_id="")


def test_state_deduplicates_hand_edited_seen_list() -> None:
    state = MonitorState(monitor_id="m", seen_ids=("a", "b", "a", "c", "b"))
    assert state.seen_ids == ("a", "b", "c")


def test_sources_are_distinct_in_order() -> None:
    state = MonitorState(
        monitor_id="m",
        strategies=(
            SearchStrategy(source="openalex", query="q1"),
            SearchStrategy(source="arxiv", query="q2"),
            SearchStrategy(source="openalex", query="q3"),
        ),
    )
    assert state.sources == ["openalex", "arxiv"]


# ---------------------------------------------------------------------------
# Provenance bridge
# ---------------------------------------------------------------------------


