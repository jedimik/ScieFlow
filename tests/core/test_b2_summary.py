"""T-9 t2: zip reading, part 3 — summary, control members and data level (B2:5.6, T9-D6, plan t2 items 5-6)."""

from __future__ import annotations

import dataclasses

import pytest
import yaml

from scieflow.core import archives as a
from scieflow.core import workspace
from scieflow.core.run import budget as budget_mod
from tests.fixtures.b2_archives import make_project
from tests.fixtures.b2_zips import info, make_tree, put_zip, reset_index, zip_tree

SLUG = "r1"
STAMP = (2026, 11, 5, 10, 0, 0)


@pytest.fixture(autouse=True)
def fresh_cache():
    reset_index()
    yield
    reset_index()


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


def _loop_files():
    status = {
        "run": "loop", "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "iteration": 2,
        "phases": {"hypothesize": "done", "experiment": "running"},
    }
    return {
        "status.yml": yaml.safe_dump(status).encode(),
        "config.yml": yaml.safe_dump({"lineage": "abc"}).encode(),
        "budget.yml": yaml.safe_dump({
            "budgets": {"max_iterations": 4, "max_experiment_runs": 10, "max_wall_minutes": 100},
            "spent": {"iterations": 1, "experiment_runs": 3, "wall_minutes": 25},
            "started": "2026-11-01T00:00:00+00:00",
        }).encode(),
        "data.yml": yaml.safe_dump({"version": 1, "default_level": "L1"}).encode(),
        "log.md": b"log\n",
        "notebook.md": b"notebook\n",
    }


def _zip_of_files(project, files: dict[str, bytes], stamp=STAMP):
    entries = [(info(name, date_time=stamp), data) for name, data in files.items()]
    return put_zip(project, SLUG, entries)


def test_complete_loop_run_matches_describe_on_the_extracted_tree(project, tmp_path):
    files = _loop_files()
    src = make_tree(tmp_path / "src" / SLUG, files)
    zip_tree(src, zip_path_for(project))
    idx = a.index(project, SLUG)
    got = a.summary(idx)
    expected = dataclasses.asdict(workspace.describe(src))
    for key in ("updated", "updated_at"):
        expected.pop(key)
    run = dict(got["run"])
    run.pop("updated", None)
    run.pop("updated_at", None)
    assert run == expected
    assert got["run"]["kind"] == "loop"
    assert got["run"]["iteration"] == 2
    assert got["run"]["lineage"] == "abc"
    assert got["updated_label"] == a.ZIP_LABEL


def zip_path_for(project):
    from tests.fixtures.b2_archives import zip_path
    return zip_path(project, SLUG)


def test_updated_comes_from_the_member_dates_without_reading(project):
    _zip_of_files(project, {"status.yml": b"run: loop\n", "log.md": b"x\n"})
    got = a.summary(a.index(project, SLUG))
    assert got["run"]["updated"] == "2026-11-05"
    assert got["run"]["updated_at"].startswith("2026-11-05T10:00:00")


def test_missing_status_is_kind_none(project):
    _zip_of_files(project, {"log.md": b"x\n"})
    got = a.summary(a.index(project, SLUG))
    assert got["controls"]["status.yml"] == "not in this archive"
    assert got["run"]["kind"] == "none"
    assert got["run"]["state"] == "no status.yml"


@pytest.mark.parametrize("name, payload, word", [
    pytest.param("status.yml", b"a: [unclosed\n", "unreadable", id="status-bad-yaml"),
    pytest.param("status.yml", b"- just\n- a list\n", "unreadable", id="status-not-mapping"),
    pytest.param("status.yml", b"run: loop\n" + b"#" * a.CONTROL_MAX_BYTES, "too large to read here",
                 id="status-oversized"),
    pytest.param("config.yml", b"a: [unclosed\n", "unreadable", id="config-bad-yaml"),
])
def test_control_words(project, name, payload, word):
    files = _loop_files()
    files[name] = payload
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    assert got["controls"][name] == word


def test_unreadable_status_gives_no_run_description(project):
    files = _loop_files()
    files["status.yml"] = b"a: [unclosed\n"
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    assert got["run"] is None
    assert got["run_reason"] == "unreadable"


def test_budget_remaining_matches_budget_module(project):
    files = _loop_files()
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    expected = budget_mod.remaining_fraction(yaml.safe_load(files["budget.yml"]))
    assert got["budget"]["remaining"] == expected
    assert got["budget"]["status"] == "ok"


def test_budget_absent_is_not_in_this_archive(project):
    files = _loop_files()
    del files["budget.yml"]
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    assert got["budget"] == {"status": "not in this archive", "remaining": None}


def test_malformed_budget_is_unreadable(project):
    files = _loop_files()
    files["budget.yml"] = yaml.safe_dump({"budgets": {}, "spent": {}}).encode()
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    assert got["budget"] == {"status": "unreadable", "remaining": None}


@pytest.mark.parametrize("files, level, reason", [
    ({}, "L0", "undeclared"),
    ({"data.yml": yaml.safe_dump({"version": 1, "default_level": "L1"}).encode()}, "L1", None),
    ({"data.yml": b"version: [oops\n"}, "L2", "data.yml: not readable YAML"),
    ({"data.yml": b"\xff\xfe bad"}, "L2", "data.yml: cannot be read"),
])
def test_data_level_from_bytes(project, files, level, reason):
    base = {"status.yml": b"run: loop\n"}
    base.update(files)
    _zip_of_files(project, base)
    got = a.summary(a.index(project, SLUG))
    assert got["data_level"] == {"level": level, "reason": reason}


def test_oversized_data_file_is_l2_too_large(project):
    files = {"status.yml": b"run: loop\n",
             "data.yml": b"version: 1\n" + b"#" * a.CONTROL_MAX_BYTES}
    _zip_of_files(project, files)
    got = a.summary(a.index(project, SLUG))
    assert got["controls"]["data.yml"] == "too large to read here"
    assert got["data_level"] == {"level": "L2", "reason": "too large to read here"}


def test_totals_count_each_kind(project):
    from tests.fixtures.b2_zips import set_flag_bit, write_zip
    from tests.fixtures.b2_archives import zip_path
    entries = [(info("status.yml"), b"run: loop\n"), (info("a.txt"), b"aa"), (info("../x"), b"x")]
    path = write_zip(zip_path(project, SLUG), entries)
    set_flag_bit(path, "a.txt")
    got = a.summary(a.index(project, SLUG))
    assert got["totals"]["members"] == 3
    assert got["totals"]["unsafe"] == 1
    assert got["totals"]["encrypted"] == 1
    assert got["totals"]["uncompressed_bytes"] == len(b"run: loop\n") + 2 + 1


def test_summary_writes_nothing(project):
    from tests.fixtures.b2_archives import tree_snapshot
    _zip_of_files(project, _loop_files())
    idx = a.index(project, SLUG)
    before = tree_snapshot(project.root)
    a.summary(idx)
    assert tree_snapshot(project.root) == before
