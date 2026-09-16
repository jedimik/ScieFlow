from datetime import date

import pytest

from scieflow.news.db import Database


def make_db(tmp_path):
    return Database(tmp_path / "sub" / "news.json")


def test_last_checked_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_last_checked("Snakemake") is None
    db.set_last_checked("Snakemake", date(2026, 7, 17))
    assert db.get_last_checked("Snakemake") == date(2026, 7, 17)
    db.set_last_checked("Snakemake", date(2026, 7, 18))
    assert db.get_last_checked("Snakemake") == date(2026, 7, 18)


def test_create_and_get_run(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    run = db.get_run(run_id)
    assert run["id"] == run_id
    assert run["agent"] == "claude"
    assert run["failures"] == []
    assert "T" in run["timestamp"]
    assert db.get_run(999) is None


def test_latest_run_id(tmp_path):
    db = make_db(tmp_path)
    assert db.latest_run_id() is None
    db.create_run("claude")
    second = db.create_run("agy")
    assert db.latest_run_id() == second


def test_results_in_insertion_order(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    db.add_result(run_id, "B", date(2026, 7, 1), date(2026, 7, 17), "## B\n")
    db.add_result(run_id, "A", date(2026, 7, 1), date(2026, 7, 17), "## A\n")
    results = db.results_for_run(run_id)
    assert [r["name"] for r in results] == ["B", "A"]
    assert results[0]["window_start"] == "2026-07-01"
    assert results[0]["edited_markdown"] is None
    assert db.results_for_run(run_id + 1) == []


def test_failures_accumulate(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    db.add_failure(run_id, "X", "timeout")
    db.add_failure(run_id, "Y", "bad output")
    assert db.get_run(run_id)["failures"] == [
        {"name": "X", "reason": "timeout", "raw_output": None},
        {"name": "Y", "reason": "bad output", "raw_output": None},
    ]


def test_corrupt_db_recovers_with_backup(tmp_path):
    path = tmp_path / "news.json"
    path.write_text("{ this is not json")
    db = Database(path)
    db.set_last_checked("X", date(2026, 7, 17))
    assert db.get_last_checked("X") == date(2026, 7, 17)
    assert (tmp_path / "news.json.bak").read_text() == "{ this is not json"


def test_lock_file_created(tmp_path):
    db = Database(tmp_path / "news.json")
    db.set_last_checked("X", date(2026, 7, 17))
    assert (tmp_path / "news.json.lock").exists()


def test_lock_held_elsewhere_times_out(tmp_path):
    from filelock import FileLock, Timeout

    path = tmp_path / "news.json"
    db = Database(path, lock_timeout=0.1)
    other = FileLock(str(path) + ".lock")
    other.acquire()
    try:
        with pytest.raises(Timeout):
            db.set_last_checked("X", date(2026, 7, 17))
    finally:
        other.release()


def test_list_runs_newest_first(tmp_path):
    db = make_db(tmp_path)
    first = db.create_run("claude")
    second = db.create_run("agy")
    runs = db.list_runs()
    assert [r["id"] for r in runs] == [second, first]
    assert runs[0]["agent"] == "agy"
    assert runs[0]["failures"] == []


def test_results_include_id_and_edit_roundtrip(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    db.add_result(run_id, "X", date(2026, 7, 1), date(2026, 7, 17), "## X\noriginal\n")
    result = db.results_for_run(run_id)[0]
    assert isinstance(result["id"], int)
    db.set_edited_markdown(result["id"], "## X\nedited\n")
    updated = db.results_for_run(run_id)[0]
    assert updated["edited_markdown"] == "## X\nedited\n"
    assert updated["markdown"] == "## X\noriginal\n"  # original untouched
    db.set_edited_markdown(result["id"], None)
    assert db.results_for_run(run_id)[0]["edited_markdown"] is None


def test_second_instance_sees_writes(tmp_path):
    path = tmp_path / "news.json"
    a = Database(path)
    run_id = a.create_run("claude")
    assert a.results_for_run(run_id) == []  # prime any cache
    b = Database(path)
    b.add_result(run_id, "X", date(2026, 7, 1), date(2026, 7, 18), "## X\n")
    assert [r["name"] for r in a.results_for_run(run_id)] == ["X"]


def test_models_cache_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_models("agy") is None
    db.set_models("agy", ["A (High)", "B (Low)"])
    cached = db.get_models("agy")
    assert cached["models"] == ["A (High)", "B (Low)"]
    assert "T" in cached["checked_at"]
    db.set_models("agy", ["C"])
    assert db.get_models("agy")["models"] == ["C"]


def test_failure_raw_output_truncated(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("agy")
    db.add_failure(run_id, "X", "no header", raw_output="y" * 5000)
    failure = db.get_run(run_id)["failures"][0]
    assert failure["reason"] == "no header"
    assert len(failure["raw_output"]) == 4000
    db.add_failure(run_id, "Y", "boom")
    assert db.get_run(run_id)["failures"][1]["raw_output"] is None


def test_search_results(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    db.add_result(run_id, "Snakemake", date(2026, 7, 1), date(2026, 7, 19), "## Snakemake\nremote storage fix\n")
    db.add_result(run_id, "DuckDB", date(2026, 7, 1), date(2026, 7, 19), "## DuckDB\nvector search\n")
    hits = db.search_results("REMOTE storage")
    assert [h["name"] for h in hits] == ["Snakemake"]
    assert isinstance(hits[0]["id"], int)
    assert db.search_results("   ") == []
    # edited content is searched instead of the original
    result_id = db.results_for_run(run_id)[1]["id"]
    db.set_edited_markdown(result_id, "## DuckDB\nedited banana\n")
    assert [h["name"] for h in db.search_results("banana")] == ["DuckDB"]
    assert db.search_results("vector search") == []  # original superseded by edit


def test_search_results_newest_first_and_limit(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run("claude")
    for i in range(5):
        db.add_result(run_id, f"I{i}", date(2026, 7, 1), date(2026, 7, 19), f"## I{i}\ncommon token\n")
    hits = db.search_results("common token", limit=3)
    assert [h["name"] for h in hits] == ["I4", "I3", "I2"]
