"""The dashboard's read cost, counted.

The run list once looped `service.run_detail` per run to render two columns,
reading every event log, job record and gate to do it. Nothing counted, so
nothing noticed. These tests count filesystem reads and listings and pin the
per-run cost, so the cost growing makes them fail.
"""
import builtins
import io
import os
from collections import Counter
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.run import budget, status

SLUGS = ("r2", "r3")


@pytest.fixture
def many_runs(project):
    """Three runs in all (r1 from the fixture), each with state and a budget."""
    for slug in SLUGS:
        ws = project.run_dir(slug)
        (ws / "logs").mkdir(parents=True)
        (ws / "config.yml").write_text(f"slug: {slug}\napproval: autonomous\n")
        status.write_status(ws, status.new_status(slug, "autonomous"))
        budget.write_budget(ws, budget.new_budget(3, 10, 60))
    return project


@pytest.fixture
def counted(monkeypatch):
    """Counts, per (run slug, file name), every file opened for reading, and
    every directory listing, under the workspace.

    Hooks `io.open` (what `Path.open`/`read_text` call) and `builtins.open`,
    plus `os.scandir`/`os.listdir` (what `iterdir`/`glob` call). Counting at
    the filesystem boundary means it cannot be dodged by a new helper that
    bypasses a particular reader.
    """
    reads: Counter = Counter()
    listings: Counter = Counter()

    def key(path):
        try:
            parts = Path(os.fspath(path)).parts
        except TypeError:
            return None
        if "workspace" not in parts:
            return None
        rest = parts[parts.index("workspace") + 1:]
        return rest if rest else None

    def wrap_open(real):
        def counting(file, mode="r", *args, **kwargs):
            k = key(file) if isinstance(file, (str, os.PathLike)) else None
            if k and not any(c in mode for c in "wax+"):
                reads[(k[0], "/".join(k[1:]))] += 1
            return real(file, mode, *args, **kwargs)
        return counting

    def wrap_list(real):
        def counting(path=".", *args, **kwargs):
            k = key(path)
            if k:
                listings[(k[0], "/".join(k[1:]))] += 1
            return real(path, *args, **kwargs)
        return counting

    monkeypatch.setattr(io, "open", wrap_open(io.open))
    monkeypatch.setattr(builtins, "open", wrap_open(builtins.open))
    monkeypatch.setattr(os, "scandir", wrap_list(os.scandir))
    monkeypatch.setattr(os, "listdir", wrap_list(os.listdir))
    return reads, listings


def _forbidden(reads, listings, slugs):
    """Reads of anything beyond the three small files, and any listing."""
    allowed = {"status.yml", "config.yml", "budget.yml"}
    extra = {k: n for k, n in reads.items() if k[0] in slugs and k[1] not in allowed}
    inside = {k: n for k, n in listings.items() if k[0] in slugs}
    return extra, inside


def test_the_run_list_costs_three_small_reads_per_run(many_runs, counted):
    reads, listings = counted
    runs = service.list_runs_with_budget(many_runs)
    slugs = {"r1", *SLUGS}
    assert {r["slug"] for r in runs} == slugs
    per_run = {s: {k[1]: n for k, n in reads.items() if k[0] == s} for s in slugs}
    for slug in slugs:
        assert per_run[slug] == {"status.yml": 1, "config.yml": 1, "budget.yml": 1}, slug
    # No event log, gate, proposal preview or job listing: nothing else read
    # or listed inside any run.
    assert _forbidden(reads, listings, slugs) == ({}, {})


def test_the_dashboard_page_never_reads_events_or_jobs(many_runs, counted, client):
    reads, listings = counted
    body = client.get("/").text
    assert "r1" in body and "r2" in body and "r3" in body
    for slug in ("r1", *SLUGS):
        assert reads[(slug, "events.jsonl")] == 0, slug
        assert reads[(slug, "budget.yml")] == 1, slug
        assert not [k for k in listings if k[0] == slug and "jobs" in k[1]], slug
        assert not [k for k in reads if k[0] == slug and "jobs" in k[1]], slug


def test_the_dashboard_still_shows_budget_left(many_runs, client):
    body = client.get("/").text
    assert "iterations 100%" in body
    assert body.count("iterations 100%") == 3


def test_a_run_without_budget_lists_with_no_budget(project, client):
    (project.run_dir("r1") / "budget.yml").unlink()
    runs = service.list_runs_with_budget(project)
    assert runs[0]["slug"] == "r1" and runs[0]["remaining"] is None
    body = client.get("/").text
    assert "r1" in body and "iterations" not in body


def test_a_run_with_no_status_or_budget_still_lists(project, client):
    ws = project.run_dir("bare")
    ws.mkdir(parents=True)
    runs = {r["slug"]: r for r in service.list_runs_with_budget(project)}
    assert runs["bare"]["kind"] == "none" and runs["bare"]["remaining"] is None
    assert "bare" in client.get("/").text


def test_a_stopped_run_is_reported_from_describe(project):
    ws = project.run_dir("r1")
    st = status.read_status(ws)
    st["stopped"] = {"reason": "budget"}
    status.write_status(ws, st)
    assert service.list_runs_with_budget(project)[0]["stopped_reason"] == "budget"


def test_a_damaged_budget_lists_as_no_budget(project):
    (project.run_dir("r1") / "budget.yml").write_text("a: [unclosed\n")
    assert service.list_runs_with_budget(project)[0]["remaining"] is None
