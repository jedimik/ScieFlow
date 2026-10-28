"""M-C1 t3 (Part A 5.4, 5.11): `scieflow research network` and the network.yml writer.

Synthetic values only. No network: the CLI runs in-process on a tmp project.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, network
from scieflow.research.cli import research


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "r"
    ws.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return root, ws


def cli(*args):
    return CliRunner().invoke(research, ["network", *args])


def network_events(ws):
    return events.read(ws, types=("network.set",))


# 1. a human sets a mode with a reason: file and event say so
def test_a_human_sets_replay_and_the_file_and_event_say_so(project):
    _, ws = project
    res = cli("r", "replay", "--reason", "reproduce the round-1 searches")
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((ws / "network.yml").read_text(encoding="utf-8"))
    assert data["mode"] == "replay" and data["set_by"] == "human"
    assert data["reason"] == "reproduce the round-1 searches"
    assert isinstance(data["set_at"], str) and data["set_at"].endswith("Z")
    [evt] = network_events(ws)
    assert evt["actor"] == "human"
    assert evt["data"]["mode"] == "replay" and evt["data"]["previous"] == "live"


def test_the_written_file_is_read_back_as_a_valid_file(project):
    _, ws = project
    assert cli("r", "replay", "--reason", "x").exit_code == 0
    assert network.base(ws) == ("replay", "file")


# 1b. the show command reports the state (--json)
def test_show_json_reports_mode_origin_set_by_set_at_reason_and_snapshot_count(project):
    _, ws = project
    cli("r", "replay", "--reason", "x")
    res = cli("r", "--json")
    assert res.exit_code == 0, res.output
    import json
    doc = json.loads(res.output)
    assert doc["mode"] == "replay" and doc["origin"] == "file"
    assert doc["set_by"] == "human" and doc["reason"] == "x"
    assert doc["set_at"].endswith("Z") and doc["snapshots"] == 0


def test_show_reports_an_invalid_file_as_offline(project):
    _, ws = project
    (ws / "network.yml").write_text("mode: [not, a, mode\n", encoding="utf-8")
    res = cli("r", "--json")
    import json
    doc = json.loads(res.output)
    assert doc["mode"] == "offline" and doc["origin"] == "invalid"


# 2. loosening and clearing need a reason; a refusal leaves the file unchanged
def test_loosening_without_a_reason_fails_and_leaves_the_file_unchanged(project):
    _, ws = project
    assert cli("r", "replay", "--reason", "x").exit_code == 0
    before = (ws / "network.yml").read_bytes()
    res = cli("r", "live")
    assert res.exit_code == 1
    assert (ws / "network.yml").read_bytes() == before
    assert network_events(ws)[-1]["data"]["mode"] == "replay"   # no second event


def test_loosening_with_a_reason_is_written(project):
    _, ws = project
    cli("r", "replay", "--reason", "x")
    assert cli("r", "live", "--reason", "re-fetch the index").exit_code == 0
    assert network.base(ws) == ("live", "file")


def test_tightening_needs_no_reason(project):
    _, ws = project
    assert cli("r", "live").exit_code == 0
    assert cli("r", "offline").exit_code == 0
    assert network.base(ws) == ("offline", "file")


def test_clear_without_a_reason_fails(project):
    _, ws = project
    cli("r", "replay", "--reason", "x")
    res = cli("r", "--clear")
    assert res.exit_code == 1
    assert (ws / "network.yml").is_file()


def test_clear_with_a_reason_deletes_the_file(project):
    _, ws = project
    cli("r", "replay", "--reason", "x")
    res = cli("r", "--clear", "--reason", "the review is finished")
    assert res.exit_code == 0, res.output
    assert not (ws / "network.yml").exists()
    assert network.base(ws) == ("live", "default")


# 3. an agent may only tighten to replay or offline
def test_an_agent_may_tighten_to_replay_or_offline(project):
    _, ws = project
    res = cli("r", "offline", "--as-agent")
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((ws / "network.yml").read_text(encoding="utf-8"))
    assert data["set_by"] == "agent" and data["mode"] == "offline"
    assert network_events(ws)[-1]["actor"] == "agent"


@pytest.mark.parametrize("args", [
    ("live",), ("record",), ("--clear", "--reason", "x"),
])
def test_an_agent_cannot_loosen_clear_or_record(project, args):
    _, ws = project
    cli("r", "replay", "--reason", "x")
    before = (ws / "network.yml").read_bytes()
    res = cli("r", *args, "--as-agent")
    assert res.exit_code == 1, res.output
    assert (ws / "network.yml").read_bytes() == before


# 4. every write refuses inside a dispatch
@pytest.mark.parametrize("args", [
    ("replay", "--reason", "x"), ("offline", "--as-agent"),
])
def test_every_write_refuses_inside_a_dispatch(project, monkeypatch, args):
    _, ws = project
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    res = cli("r", *args)
    assert res.exit_code == 1
    assert not (ws / "network.yml").exists()


def test_a_dispatch_detected_by_its_cache_dir_also_refuses(project, monkeypatch, tmp_path):
    _, ws = project
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / ".uv-cache"))
    assert cli("r", "offline", "--as-agent").exit_code == 1
    assert not (ws / "network.yml").exists()


# 5. a symbolic link is never written through
def test_a_write_refuses_a_symbolic_link_and_leaves_its_target_alone(project, tmp_path):
    _, ws = project
    target = tmp_path / "elsewhere.yml"
    target.write_text("keep\n", encoding="utf-8")
    os.symlink(target, ws / "network.yml")
    res = cli("r", "replay", "--reason", "x")
    assert res.exit_code == 1
    assert target.read_text(encoding="utf-8") == "keep\n"


# 6. a replaying run gets an empty snapshot folder (plan-review N1)
def test_replay_creates_the_snapshot_folder_so_it_can_be_protected(project):
    _, ws = project
    assert cli("r", "replay", "--reason", "x").exit_code == 0
    assert (ws / "snapshots" / "literature").is_dir()
    assert not (ws / "snapshots" / "literature").is_symlink()


# 7. a linked snapshot folder is reported as corrupt, not a traceback (plan-review F3)
def test_show_with_a_linked_snapshot_folder_reports_it_and_exits_1(project, tmp_path):
    _, ws = project
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, ws / "snapshots")
    res = cli("r")
    assert res.exit_code == 1
    assert isinstance(res.exception, SystemExit), repr(res.exception)
    assert "symbolic link" in res.output


def test_show_json_with_a_linked_snapshot_folder_keeps_the_state(project, tmp_path):
    import json

    _, ws = project
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, ws / "snapshots")
    res = cli("r", "--json")
    assert res.exit_code == 1
    assert isinstance(res.exception, SystemExit), repr(res.exception)
    doc = json.loads(res.stdout)
    assert doc["mode"] == "live" and doc["origin"] == "default" and doc["snapshots"] is None


# 8. a snapshot path that is a file refuses before network.yml is written (plan-review F3)
@pytest.mark.parametrize("args", [("replay", "--reason", "x"), ("offline", "--as-agent")])
def test_a_no_egress_mode_refuses_a_snapshot_file_and_writes_nothing(project, args):
    _, ws = project
    (ws / "snapshots").write_text("not a folder\n", encoding="utf-8")
    res = cli("r", *args)
    assert res.exit_code == 1, res.output
    assert "snapshots is not a folder" in res.output
    assert not (ws / "network.yml").exists()
    assert network_events(ws) == []
    assert (ws / "snapshots").read_text(encoding="utf-8") == "not a folder\n"
