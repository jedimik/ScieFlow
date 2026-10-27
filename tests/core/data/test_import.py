"""M-B t5 import (Part A 5.8): a released file enters another run as L1.5, never as L2."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.data import levels, release
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


def make_run(project, slug):
    ws = project / "workspace" / slug
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status(slug, "autonomous"))
    return ws


def released_run(proj, ws, rid="rel-1"):
    (ws / "outputs").mkdir(parents=True)
    (ws / "outputs" / "agg.csv").write_text(CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid=rid)
    gates.answer(proj, ws, g["id"], "release", actor="human")
    return rid


def test_an_import_copies_the_file_and_records_it(proj, ws, project):
    rid = released_run(proj, ws)
    dest = make_run(project, "r2")
    release.import_release(proj, dest, ws, rid)
    copied = dest / "inputs" / "released" / "r1-rel-1" / "agg.csv"
    assert copied.read_bytes() == (ws / "released" / rid / "agg.csv").read_bytes()
    [imp] = levels.read(dest).imports
    assert imp["from"] == "r1" and imp["release"] == rid
    assert imp["file"] == "inputs/released/r1-rel-1/agg.csv"
    assert imp["sha256"] == levels.read(ws).released[0]["sha256"]
    assert levels.level_info(dest).level == "L1.5"
    assert len(events.read(dest, types=("data.imported",))) == 1


def test_an_imported_run_is_l15_never_l2(proj, ws, project):
    rid = released_run(proj, ws)
    dest = make_run(project, "r2")
    release.import_release(proj, dest, ws, rid)
    assert levels.run_level(dest) == "L1.5"
    assert levels.run_level(dest) != "L2"


def test_a_tampered_release_copies_nothing(proj, ws, project):
    rid = released_run(proj, ws)
    (ws / "released" / rid / "agg.csv").write_text("group,n\nA,99\n")
    dest = make_run(project, "r2")
    with pytest.raises(release.ReleaseError):
        release.import_release(proj, dest, ws, rid)
    assert not (dest / "inputs").exists()
    assert levels.read(dest).imports == []


def test_an_unknown_release_id_is_refused(proj, ws, project):
    released_run(proj, ws)
    dest = make_run(project, "r2")
    with pytest.raises(release.ReleaseError):
        release.import_release(proj, dest, ws, "nope")


def test_the_same_release_cannot_be_imported_twice(proj, ws, project):
    rid = released_run(proj, ws)
    dest = make_run(project, "r2")
    release.import_release(proj, dest, ws, rid)
    with pytest.raises(release.ReleaseError):
        release.import_release(proj, dest, ws, rid)
    assert len(levels.read(dest).imports) == 1


def test_a_run_cannot_import_its_own_release(proj, ws):
    rid = released_run(proj, ws)
    with pytest.raises(release.ReleaseError):
        release.import_release(proj, ws, ws, rid)


def test_the_import_command_exit_codes(proj, ws, project):
    rid = released_run(proj, ws)
    make_run(project, "r2")
    ok = CliRunner().invoke(data, ["import", "r2", "--from", "r1", "--release", rid])
    assert ok.exit_code == 0, ok.output
    (ws / "released" / rid / "agg.csv").write_text("group,n\nA,99\n")
    tampered = CliRunner().invoke(data, ["import", "r2", "--from", "r1", "--release", "nope"])
    assert tampered.exit_code == 1
    assert CliRunner().invoke(data, ["import", "r2", "--from", "r1", "--release", rid]).exit_code == 1


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_released_record_naming_a_file_elsewhere_copies_nothing(proj, ws, project):  # F3
    rid = released_run(proj, ws)
    (ws / "secret.csv").write_text("group,n\nA,99\n")
    df = yaml.safe_load((ws / "data.yml").read_text())
    df["released"][0]["file"] = "secret.csv"
    df["released"][0]["sha256"] = sha(ws / "secret.csv")
    (ws / "data.yml").write_text(yaml.safe_dump(df, sort_keys=False))
    dest = make_run(project, "r2")
    with pytest.raises(release.ReleaseError):
        release.import_release(proj, dest, ws, rid)
    assert not (dest / "inputs").exists()
    assert levels.read(dest).imports == []


def test_an_audit_names_a_released_record_that_points_elsewhere(proj, ws, project):  # F3
    rid = released_run(proj, ws)
    (ws / "secret.csv").write_text("group,n\nA,99\n")
    df = yaml.safe_load((ws / "data.yml").read_text())
    df["released"][0]["file"] = "secret.csv"
    df["released"][0]["sha256"] = sha(ws / "secret.csv")
    (ws / "data.yml").write_text(yaml.safe_dump(df, sort_keys=False))
    assert release.audit(ws)["mismatches"] == 1
    r = CliRunner().invoke(data, ["audit", "r1"])
    assert r.exit_code == 1 and "secret.csv" in r.output


def test_an_import_record_naming_a_file_elsewhere_is_a_mismatch(proj, ws, project):  # F3
    rid = released_run(proj, ws)
    dest = make_run(project, "r2")
    release.import_release(proj, dest, ws, rid)
    (dest / "other.csv").write_text(CLEAN)
    df = yaml.safe_load((dest / "data.yml").read_text())
    df["imports"][0]["file"] = "other.csv"
    df["imports"][0]["sha256"] = sha(dest / "other.csv")
    (dest / "data.yml").write_text(yaml.safe_dump(df, sort_keys=False))
    report = release.audit(dest)
    assert report["mismatches"] == 1 and report["imports"][0]["ok"] is False

