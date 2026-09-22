import subprocess
import sys
import time

import yaml

from scieflow.core import store


def test_ids_are_unique_sortable_and_26_chars():
    ids = [store.new_id() for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(len(i) == 26 for i in ids)
    first = store.new_id()
    time.sleep(0.005)
    assert store.new_id() > first


def test_write_yaml_is_atomic_and_readable(tmp_path):
    path = tmp_path / "s" / "status.yml"
    store.write_yaml(path, {"a": 1, "b": [1, 2]})
    assert store.read_yaml(path) == {"a": 1, "b": [1, 2]}
    assert not list(path.parent.glob(".status.yml.*.tmp"))


def test_read_yaml_default_when_missing(tmp_path):
    assert store.read_yaml(tmp_path / "nope.yml", {"x": 0}) == {"x": 0}


BUMP = """
import sys
from pathlib import Path
from scieflow.core import store
for _ in range(int(sys.argv[2])):
    store.update_yaml(Path(sys.argv[1]), lambda d: {**d, "n": d.get("n", 0) + 1})
"""


def test_concurrent_updates_never_lose_or_corrupt(tmp_path):
    """Six separate interpreters hammer one file; every increment must land."""
    path = tmp_path / "counter.yml"
    store.write_yaml(path, {"n": 0})
    procs = [subprocess.Popen([sys.executable, "-c", BUMP, str(path), "25"])
             for _ in range(6)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    assert yaml.safe_load(path.read_text()) == {"n": 150}


def test_jsonl_append_and_read_skip_garbage(tmp_path):
    path = tmp_path / "events.jsonl"
    store.append_jsonl(path, {"a": 1})
    with path.open("a") as fh:
        fh.write("not json\n\n")
    store.append_jsonl(path, {"a": 2})
    assert store.read_jsonl(path) == [{"a": 1}, {"a": 2}]
    assert store.read_jsonl(tmp_path / "missing.jsonl") == []
