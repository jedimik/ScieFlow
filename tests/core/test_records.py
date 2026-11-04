"""Unit tests of the event and job record validators (M-I t2, plan section 5.2).

Synthetic data only. The module under test is imported inside each helper so
that before it exists every rule test fails on its own node, not at collection.
"""

import dataclasses
import importlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
EVENT_ID = "0123456789ABCDEFGHJKMNPQRS"
JOB_ID = "0123456789ABCDEFGHJKMNPQRS"
MB = 1024 * 1024


def records():
    return importlib.import_module("scieflow.core.records")


def good_event(**over):
    event = {
        "id": EVENT_ID,
        "ts": "2026-11-03T12:00:00.000+00:00",
        "run": "run-1",
        "slug": "r1",
        "type": "note.idea",
        "actor": "agent",
        "data": {"text": "x"},
    }
    event.update(over)
    return event


def good_job(**over):
    job = {
        "id": JOB_ID,
        "kind": "turn",
        "argv": ["python", "-c", "pass"],
        "cwd": "/tmp/x",
        "label": "",
        "run_dir": None,
        "state": "running",
        "pid": 123,
        "queued": "2026-11-03T12:00:00.000+00:00",
        "started": "2026-11-03T12:00:01.000+00:00",
        "finished": None,
        "exit_code": None,
        "timeout_s": None,
        "log": "/tmp/x/a.log",
        "err": "/tmp/x/a.err",
        "sandboxed": False,
        "turn_audit": None,
    }
    job.update(over)
    return job


# -- check_event -----------------------------------------------------------

def _drop(key):
    def change(e):
        e.pop(key)
    return change


def _set(key, value):
    def change(e):
        e[key] = value
    return change


EVENT_REFUSALS = [
    (lambda e: e.update(extra=1), "envelope-keys"),
    (_drop("slug"), "envelope-keys"),
    (_set("id", "short"), "id"),
    (_set("id", EVENT_ID.lower()), "id"),
    (_set("ts", "2026-11-03T12:00:00"), "ts"),
    (_set("ts", "not a time"), "ts"),
    (_set("run", ""), "run"),
    (_set("slug", 3), "slug"),
    (_set("type", "phase.strated"), "type"),
    (_set("type", "note.<img src=x>"), "note-name"),
    (_set("type", "note."), "note-name"),
    (_set("actor", "robot"), "actor"),
    (_set("data", ["x"]), "data-object"),
    (_set("data", {1: "x"}), "data-object"),
    (_set("data", {"x": {1, 2}}), "data-value"),
    (_set("data", {"x": b"raw"}), "data-value"),
    (_set("data", {"x": object()}), "data-value"),
    (_set("data", {"x": {"y": [1, {2, 3}]}}), "data-value"),
    (_set("data", {"x": float("nan")}), "data-finite"),
    (_set("data", {"x": float("inf")}), "data-finite"),
    (lambda e: e.update(type="job.finished", data={}), "required-data"),
    (lambda e: e.update(type="gate.answered", data={"gate": "g", "kind": "k"}), "required-data"),
]


@pytest.mark.parametrize("mutate, rule", EVENT_REFUSALS)
def test_check_event_refuses_by_rule(mutate, rule):
    event = good_event()
    mutate(event)
    with pytest.raises(records().EventInvalid) as info:
        records().check_event(event)
    assert info.value.rule == rule
    assert isinstance(info.value, ValueError)
    assert rule in str(info.value)


def test_check_event_coerces_paths_tuples_integrals_and_keeps_bool():
    import numpy as np

    event = good_event(data={
        "p": Path("a/b.txt"),
        "t": (1, (2, 3)),
        "n": np.int64(3),
        "b": True,
        "none": None,
        "f": np.float32(0.5),
        "nested": {"refs": [1, "x"]},
    })
    out = records().check_event(event)
    assert out["data"] == {
        "p": "a/b.txt", "t": [1, [2, 3]], "n": 3, "b": True, "none": None,
        "f": 0.5, "nested": {"refs": [1, "x"]},
    }
    assert type(out["data"]["n"]) is int and type(out["data"]["b"]) is bool
    assert type(out["data"]["f"]) is float
    assert out["id"] == EVENT_ID and out["type"] == "note.idea"


def test_check_event_accepts_every_type_with_its_required_keys():
    from scieflow.core import events

    module = records()
    for type_ in sorted(events.TYPES):
        keys = module.REQUIRED_DATA.get(type_, ())
        out = module.check_event(good_event(type=type_, data={k: "v" for k in keys}))
        assert out["type"] == type_


# -- check_job -------------------------------------------------------------

JOB_REFUSALS = [
    (lambda j: j.update(extra=1), "job-keys"),
    (lambda j: j.pop("pid"), "job-keys"),
    (lambda j: j.update(id="../x"), "job-id"),
    (lambda j: j.update(id=""), "job-id"),
    (lambda j: j.update(kind="Agent"), "job-kind"),
    (lambda j: j.update(kind="job.x"), "job-kind"),
    (lambda j: j.update(state="paused"), "job-state"),
    (lambda j: j.update(argv=[1]), "job-argv"),
    (lambda j: j.update(argv="python"), "job-argv"),
    (lambda j: j.update(cwd=3), "job-strings"),
    (lambda j: j.update(run_dir=3), "job-strings"),
    (lambda j: j.update(queued=None), "job-strings"),
    (lambda j: j.update(pid=True), "job-numbers"),
    (lambda j: j.update(pid="1"), "job-numbers"),
    (lambda j: j.update(exit_code=True), "job-numbers"),
    (lambda j: j.update(timeout_s=-1), "job-numbers"),
    (lambda j: j.update(timeout_s=float("nan")), "job-numbers"),
    (lambda j: j.update(sandboxed="yes"), "job-flags"),
    (lambda j: j.update(turn_audit=[]), "job-flags"),
    (lambda j: j.update(state="done", finished=None), "job-final"),
    (lambda j: j.update(state="failed", finished=None), "job-final"),
]


@pytest.mark.parametrize("mutate, rule", JOB_REFUSALS)
def test_check_job_refuses_by_rule(mutate, rule):
    job = good_job()
    mutate(job)
    with pytest.raises(records().JobInvalid) as info:
        records().check_job(job)
    assert info.value.rule == rule
    assert isinstance(info.value, ValueError)


def test_check_job_accepts_valid_records_including_remote_without_pid():
    module = records()
    module.check_job(good_job())
    module.check_job(good_job(state="done", finished="2026-11-03T12:00:02.000+00:00", exit_code=0))
    module.check_job(good_job(kind="remote", pid=None))
    module.check_job(good_job(turn_audit={}, sandboxed=True, timeout_s=0.5))


# -- schema documents ------------------------------------------------------

def test_events_schema_equals_the_module_constants():
    module = records()
    from scieflow.core import events

    schema = yaml.safe_load((ROOT / "schemas" / "events.yml").read_text())
    assert schema["version"] == 1
    assert schema["envelope"]["keys"] == ["id", "ts", "run", "slug", "type", "actor", "data"]
    assert set(schema["envelope"]["keys"]) == module.ENVELOPE_KEYS
    assert schema["envelope"]["types_source"] == "scieflow.core.events.TYPES"
    assert schema["envelope"]["actors_source"] == "scieflow.core.events.ACTORS"
    assert schema["patterns"]["event_id"] == module.EVENT_ID_PATTERN
    assert schema["patterns"]["note_name"] == module.NOTE_NAME_PATTERN
    assert schema["max_read_bytes"] == module.MAX_READ_BYTES
    assert {k: list(v) for k, v in module.REQUIRED_DATA.items()} == schema["required_data"]
    assert set(schema["required_data"]) <= set(events.TYPES)


def test_jobs_schema_equals_the_module_constants():
    from scieflow.core import jobs

    schema = yaml.safe_load((ROOT / "schemas" / "jobs.yml").read_text())
    assert schema["version"] == 1
    assert set(schema["fields"]) == {f.name for f in dataclasses.fields(jobs.Job)}
    assert schema["states_source"] == "scieflow.core.jobs.STATES"
    assert schema["states"] == list(jobs.STATES)
    assert set(schema["final_states"]) == set(jobs.FINAL)
    assert schema["patterns"]["job_id"] == records().JOB_ID_PATTERN
    assert schema["patterns"]["job_kind"] == records().JOB_KIND_PATTERN


# -- read checks -----------------------------------------------------------

def test_check_event_lines_reports_line_numbers_and_rules(tmp_path):
    older = {k: v for k, v in good_event().items() if k != "slug"}
    lines = [
        "not json",
        "",
        json.dumps([1, 2]),
        json.dumps({k: v for k, v in good_event().items() if k != "data"}),
        json.dumps(good_event(type="phase.strated")),
        json.dumps(good_event(actor="robot")),
        json.dumps(good_event(data=3)),
        json.dumps(older),
        json.dumps(good_event()),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines) + "\n")
    assert records().check_event_lines(path) == [
        {"line": 1, "rule": "json"},
        {"line": 3, "rule": "object"},
        {"line": 4, "rule": "keys"},
        {"line": 5, "rule": "type"},
        {"line": 6, "rule": "actor"},
        {"line": 7, "rule": "data"},
    ]


def test_check_event_lines_missing_file_and_too_large_without_reading(tmp_path):
    module = records()
    assert module.check_event_lines(tmp_path / "absent.jsonl") == []
    big = tmp_path / "events.jsonl"
    with big.open("wb") as fh:
        fh.truncate(51 * MB)
    assert module.check_event_lines(big) == [{"line": None, "rule": "too-large"}]


def test_check_job_files_reports_broken_and_invalid_but_not_final_rule(tmp_path):
    directory = tmp_path / "jobs"
    directory.mkdir()
    (directory / "a.json").write_text("{")
    (directory / "b.json").write_text(json.dumps(good_job(state="paused")))
    (directory / "c.json").write_text(json.dumps(good_job(state="done", finished=None)))
    (directory / "d.json").write_text(json.dumps(good_job(id="ZZZZZZZZZZZZZZZZZZZZZZZZZZ")))
    assert records().check_job_files(directory) == [
        {"file": "a.json", "rule": "json"},
        {"file": "b.json", "rule": "job-state"},
    ]


def test_check_job_files_too_large_and_missing_dir(tmp_path):
    module = records()
    assert module.check_job_files(tmp_path / "none") == []
    directory = tmp_path / "jobs"
    directory.mkdir()
    with (directory / "big.json").open("wb") as fh:
        fh.truncate(51 * MB)
    assert module.check_job_files(directory) == [{"file": "big.json", "rule": "too-large"}]
