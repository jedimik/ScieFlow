"""M-D3 t4 acceptance on the real `local` adapter (plan section 6 t4 items 8-10).

Real bash, real setsid sessions and real rsync on this host; no remote host is reached. Every session the
test starts is killed in teardown, and each test is bounded (<= 120 s).
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

_SUPPORT = Path(__file__).with_name("remote_support.py")
_spec = importlib.util.spec_from_file_location("remote_support", _SUPPORT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

from scieflow.core.run import budget  # noqa: E402
from scieflow.experiments import remote_campaign  # noqa: E402
from scieflow.experiments.cli import experiment  # noqa: E402

BOUND_S = 120

# A seeded, deterministic pure-Python stage: the same inputs give the same bytes in any run.
ACCEPTS_SEED_STAGE = """\
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--output", required=True)
p.add_argument("--gain", type=float, default=1.0)
p.add_argument("--seed", type=int, default=0)
a = p.parse_args()
Path(a.output, "result.txt").write_text(Path(a.input).read_text() + f"gain={a.gain} seed={a.seed}\\n")
"""
STAGE_YAML = ("name: scale\ndescription: seeded test stage\nscript: run.py\noutput_file: result.txt\n"
              "accepts_seed: true\nenvironment:\n  pip: []\n")


def campaign_doc(name: str, *, backend: str | None, remote_name: str, remote_dir: str,
                 replicates, jobs: str | None = None, configs: int = 2) -> dict:
    doc = {
        "name": name, "pipeline": "demo", "stage": "scale", "input": "../data/in.txt",
        "parameters": {"gain": {"grid": [0.5, 1.0, 1.5][:configs]}}, "metrics": ["score"],
        "replicates": replicates,
    }
    if backend is not None:
        block = {"name": remote_name, "dir": remote_dir}
        if jobs is not None:
            block["jobs"] = jobs
        doc = {"backend": backend, "remote": block} | doc
    return doc


def make(tmp_path, *, replicates, jobs=None, limits=None):
    allowed = tmp_path / "remote_root"
    allowed.mkdir()
    limits = limits or {}
    remote = rs.local_remote(str(allowed))
    remote["limits"] = dict(remote["limits"], **limits)
    p = rs.make_project(tmp_path, remote=remote, remote_name="local",
                        campaign_remote={"name": "local", "dir": str(allowed)})
    (p.root / "config" / "remotes.yml").write_text(yaml.safe_dump({"remotes": {"local": remote}}))
    stage_dir = p.root / "pipelines" / "demo" / "stages" / "scale"
    (stage_dir / "stage.yaml").write_text(STAGE_YAML)
    (stage_dir / "run.py").write_text(ACCEPTS_SEED_STAGE)
    p.campaign.write_text(yaml.safe_dump(campaign_doc(
        "gain-sweep", backend="remote", remote_name="local", remote_dir=str(allowed),
        replicates=replicates, jobs=jobs)))
    local = p.campaign.with_name("gain-local.yaml")
    local.write_text(yaml.safe_dump(campaign_doc(
        "gain-local", backend=None, remote_name="", remote_dir="", replicates=replicates)))
    return p, allowed, local


def sweep(p, campaign_file, *extra):
    args = ["sweep", "-c", str(campaign_file), "--experiments-dir", str(p.experiments),
            "--pipelines-dir", str(p.root / "pipelines"), "--direct", *extra]
    return CliRunner().invoke(experiment, args)


def ledger_jobs(p) -> list[str]:
    path = p.ws / "remote" / "jobs.yml"
    if not path.exists():
        return []
    return [entry["job_id"] for entry in (yaml.safe_load(path.read_text()) or [])]


@pytest.fixture
def sessions(tmp_path):
    """Teardown: kill every local session this test started, even when the test failed."""
    holder: dict = {}
    yield holder
    rs.kill_local_sessions(holder.get("p") and ledger_jobs(holder["p"]) or [])


def watch_until_done(p, sessions) -> tuple[int, int]:
    """Repeated `watch --once` passes with 0.5 s pauses, bounded; returns (exit code, passes)."""
    sessions["p"] = p
    campaign_dir = p.experiments / "gain-sweep"
    deadline = time.monotonic() + BOUND_S
    passes = 0
    while True:
        result = CliRunner().invoke(experiment, ["watch", str(campaign_dir), "--once"])
        passes += 1
        if result.exit_code != 4 or time.monotonic() > deadline:
            return result.exit_code, passes
        time.sleep(0.5)


def run_entries(path: Path) -> list[dict]:
    return json.loads((path / "results.json").read_text())["runs"]


def test_a_remote_local_adapter_campaign_has_the_same_results_shape_as_a_local_one(tmp_path, sessions):
    p, _allowed, local = make(tmp_path, replicates={"n": 2, "seed": 3})
    sessions["p"] = p
    assert sweep(p, p.campaign).exit_code == 0, "remote sweep"
    code, _ = watch_until_done(p, sessions)
    assert code == 0
    remote_dir = p.experiments / "gain-sweep"
    assert sweep(p, local).exit_code == 0, "local sweep"
    local_dir = p.experiments / "gain-local"

    remote_doc = json.loads((remote_dir / "results.json").read_text())
    local_doc = json.loads((local_dir / "results.json").read_text())
    assert set(remote_doc) == set(local_doc)
    remote_runs, local_runs = remote_doc["runs"], local_doc["runs"]
    assert [r["run_id"] for r in remote_runs] == [r["run_id"] for r in local_runs]
    assert len(remote_runs) == 4
    for a, b in zip(remote_runs, local_runs):
        assert set(a) == set(b)
        for key in ("config_id", "replicate", "seed", "params", "status", "metrics", "mode"):
            assert a[key] == b[key], key
        assert a["status"] == "completed"
    for a in remote_runs:
        for name in ("artifacts/result.txt",):
            assert (remote_dir / "runs" / a["run_id"] / name).read_bytes() == \
                   (local_dir / "runs" / a["run_id"] / name).read_bytes()
    remote_csv = (remote_dir / "results.csv").read_text().splitlines()[0]
    local_csv = (local_dir / "results.csv").read_text().splitlines()[0]
    assert remote_csv == local_csv
    assert CliRunner().invoke(experiment, ["verify", str(remote_dir)]).exit_code == 0
    assert CliRunner().invoke(experiment, ["verify", str(local_dir)]).exit_code == 0


def test_the_budget_counts_n_for_an_array_job_and_the_ledger_has_one_entry(tmp_path, sessions):
    p, _allowed, _local = make(tmp_path, replicates={"n": 3, "seed": 0}, jobs="array")
    sessions["p"] = p
    assert sweep(p, p.campaign).exit_code == 0
    assert budget.read_budget(p.ws)["spent"]["experiment_runs"] == 6
    assert len(ledger_jobs(p)) == 1
    events = [e for e in rs_events(p) if e.get("type") == "budget.recorded"]
    assert len(events) == 1

    code, _ = watch_until_done(p, sessions)
    assert code == 0
    runs = run_entries(p.experiments / "gain-sweep")
    assert len(runs) == 6 and all(r["status"] == "completed" for r in runs)
    assert budget.read_budget(p.ws)["spent"]["experiment_runs"] == 6


def test_a_per_run_campaign_with_one_slot_completes_through_repeated_passes(tmp_path, sessions):
    p, _allowed, _local = make(tmp_path, replicates={"n": 3, "seed": 0},
                               limits={"max_concurrent_jobs": 1})
    sessions["p"] = p
    first = sweep(p, p.campaign)
    assert first.exit_code == 0, first.output
    assert "submitted 1 of 6 runs" in first.output
    code, passes = watch_until_done(p, sessions)
    assert code == 0 and passes >= 2
    runs = run_entries(p.experiments / "gain-sweep")
    assert len(runs) == 6 and all(r["status"] == "completed" for r in runs)


def rs_events(p):
    return rs.read_events(p.ws)
