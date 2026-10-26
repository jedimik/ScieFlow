"""M-B t5 audit and the no-writer test (Part A 5.10, t1 item 11 extended to every t5 command)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import gates
from scieflow.core.data import ingest, release
from scieflow.core.data.cli import data
from scieflow.core.project import Project
from scieflow.core.run import status

REPO = Path(__file__).resolve().parents[3]
CLEAN = "group,n\nA,12\nB,7\n"


@pytest.fixture(autouse=True)
def gate_schemas(project):
    (project / "schemas").mkdir()
    for name in ("gates", "status"):
        (project / "schemas" / f"{name}.yml").write_text((REPO / "schemas" / f"{name}.yml").read_text())


@pytest.fixture
def proj(project):
    return Project(project)


def released_run(proj, ws, rid="rel-1"):
    (ws / "outputs").mkdir(parents=True)
    (ws / "outputs" / "agg.csv").write_text(CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid=rid)
    gates.answer(proj, ws, g["id"], "release", actor="human")
    return rid


def test_audit_lists_declarations_releases_and_imports(proj, ws, project):
    rid = released_run(proj, ws)
    r = CliRunner().invoke(data, ["audit", "r1"])
    assert r.exit_code == 0, r.output
    assert rid in r.output and "released/rel-1/agg.csv" in r.output


def test_audit_names_a_modified_release_and_exits_1(proj, ws):
    released_run(proj, ws)
    (ws / "released" / "rel-1" / "agg.csv").write_text("group,n\nA,99\n")
    r = CliRunner().invoke(data, ["audit", "r1"])
    assert r.exit_code == 1
    assert "released/rel-1/agg.csv" in r.output


def test_audit_is_allowed_inside_a_dispatch(proj, ws, monkeypatch):
    released_run(proj, ws)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    assert CliRunner().invoke(data, ["audit", "r1"]).exit_code == 0


def test_no_t5_command_writes_the_policy_file(proj, ws, project, monkeypatch, tmp_path):
    (project / "config" / "data-policy.yml").write_text("version: 1\nenforcement: warn\n")
    before = (project / "config" / "data-policy.yml").read_bytes()
    monkeypatch.setattr(ingest, "SALTS_OVERRIDE", tmp_path / "salts")
    rid = released_run(proj, ws)
    dest = project / "workspace" / "r2"
    dest.mkdir(parents=True)
    status.write_status(dest, status.new_status("r2", "autonomous"))
    imported = CliRunner().invoke(data, ["import", "r2", "--from", "r1", "--release", rid])
    assert imported.exit_code == 0, imported.output
    (ws / "tab.csv").write_text("record_id,visit,n\n1,2020-03-05,12\n")
    ingested = CliRunner().invoke(data, ["ingest", "r1", str(ws / "tab.csv"), "--out",
                                         "inputs/t.csv", "--date-anchor", "visit"])
    assert ingested.exit_code == 0, ingested.output
    (ws / "outputs" / "second.csv").write_text(CLEAN)
    released = CliRunner().invoke(data, ["release", "r1", "outputs/second.csv", "--reason", "x"])
    assert released.exit_code == 0, released.output
    audited = CliRunner().invoke(data, ["audit", "r1", "--json"])
    assert audited.exit_code == 0, audited.output
    assert (project / "config" / "data-policy.yml").read_bytes() == before
