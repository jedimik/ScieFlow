"""M-C1 t3 (Part A 5.5, 5.11): `scieflow research snapshots list|verify|copy`.

Synthetic values only; the snapshots are written by the store's own writer, never by a network call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.research.cli import research
from scieflow.research.lib import snapshots

QUERY_KEY = "a" * 64
OTHER_KEY = "b" * 64
URL = "https://api.openalex.org/works"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    src = root / "workspace" / "src"
    dst = root / "workspace" / "dst"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return root, src, dst


def snap(ws, key=QUERY_KEY, provider="search-openalex", body=None):
    return snapshots.write(ws, provider=provider, op="search", key=key, url=URL,
                           request_params={"search": "synthetic"}, body_kind="json",
                           response_body=body or {"results": [{"title": "Synthetic"}]},
                           results=1)


def cli(*args):
    return CliRunner().invoke(research, ["snapshots", *args])


def snapshot_events(ws):
    return events.read(ws, types=("literature.snapshot",))


def test_list_counts_and_sizes_each_provider(project):
    _, src, _ = project
    snap(src)
    snap(src, key=OTHER_KEY)
    res = cli("list", "src", "--json")
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["search-openalex"]["count"] == 2 and doc["search-openalex"]["bytes"] > 0


def test_verify_passes_a_clean_run_and_names_a_tampered_file(project):
    _, src, _ = project
    rec = snap(src)
    assert cli("verify", "src").exit_code == 0
    path = snapshots.path(src, "search-openalex", QUERY_KEY)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["response_body"]["results"][0]["title"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    res = cli("verify", "src")
    assert res.exit_code == 1
    assert f"snapshots/literature/search-openalex/{QUERY_KEY}.json" in res.output
    assert "tampered" not in res.output
    assert rec["response_hash"] not in res.output


def test_copy_moves_clean_snapshots_and_records_one_event_in_the_target(project):
    _, src, dst = project
    snap(src)
    snap(src, key=OTHER_KEY)
    res = cli("copy", "src", "dst")
    assert res.exit_code == 0, res.output
    assert snapshots.path(dst, "search-openalex", QUERY_KEY).is_file()
    assert snapshots.path(dst, "search-openalex", OTHER_KEY).is_file()
    [evt] = snapshot_events(dst)
    assert evt["data"]["action"] == "copied"
    assert evt["data"]["from"] == "src" and evt["data"]["count"] == 2


def test_copy_skips_identical_targets(project):
    _, src, dst = project
    snap(src)
    snap(dst)
    res = cli("copy", "src", "dst")
    assert res.exit_code == 0, res.output
    [evt] = snapshot_events(dst)
    assert evt["data"]["count"] == 0


def test_copy_from_an_l2_run_is_refused_and_copies_nothing(project):
    _, src, dst = project
    snap(src)
    (src / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    res = cli("copy", "src", "dst")
    assert res.exit_code == 3
    assert "data protection: run src is L2" in res.output
    assert not snapshots.path(dst, "search-openalex", QUERY_KEY).exists()


def test_copy_refuses_a_corrupt_source_and_copies_nothing(project):
    _, src, dst = project
    snap(src)
    snap(src, key=OTHER_KEY)
    path = snapshots.path(src, "search-openalex", OTHER_KEY)
    path.write_text("{not json", encoding="utf-8")
    res = cli("copy", "src", "dst")
    assert res.exit_code == 1
    assert not snapshots.path(dst, "search-openalex", QUERY_KEY).exists()


def test_copy_refuses_a_differing_target_and_copies_nothing(project):
    _, src, dst = project
    snap(src)
    snap(src, key=OTHER_KEY)
    snap(dst, key=OTHER_KEY, body={"results": [{"title": "Different"}]})
    res = cli("copy", "src", "dst")
    assert res.exit_code == 1
    assert not snapshots.path(dst, "search-openalex", QUERY_KEY).exists()


def test_copy_refuses_inside_a_dispatch(project, monkeypatch):
    _, src, dst = project
    snap(src)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    assert cli("copy", "src", "dst").exit_code == 1
    assert not snapshots.path(dst, "search-openalex", QUERY_KEY).exists()


def test_a_symbolic_link_snapshot_is_reported_as_corrupt(project, tmp_path):
    _, src, _ = project
    snap(src)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = snapshots.path(src, "search-openalex", OTHER_KEY)
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)
    res = cli("verify", "src")
    assert res.exit_code == 1
    assert "symbolic link" in res.output
