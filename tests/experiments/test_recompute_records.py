"""M-D2 t2: a metrics recompute keeps metrics.json, manifest, results.json and results.csv coherent (plan 5.3, F1)."""

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import status
from scieflow.experiments import runner
from scieflow.experiments.campaign import Campaign, RunSpec
from scieflow.experiments.cli import experiment
from scieflow.experiments.runner import execute_run
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("manifest_support.py")
_spec = importlib.util.spec_from_file_location("manifest_support", _SUPPORT)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

NEW = {"ssim": 0.123, "mse": 4.5}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sweep(project, experiments_dir=None) -> Path:
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    exp = experiments_dir or project / "experiments"
    return run_sweep(campaign, stage, exp, mode="direct").parent


def fake_metrics(monkeypatch):
    monkeypatch.setattr(runner, "compute_metrics", lambda names, out, ref: dict(NEW))


def metrics_cli(run_dir: Path):
    return CliRunner().invoke(experiment, ["metrics", str(run_dir)])


def verify_cli(camp: Path):
    return CliRunner().invoke(experiment, ["verify", "--json", str(camp)])


def test_recompute_updates_metrics_manifest_results_and_csv_together(project, monkeypatch):
    camp = sweep(project)
    run = "001_gain-1.0"  # grid [0.5, 1.0]: 000 is gain 0.5, 001 is gain 1.0
    run_dir = camp / "runs" / run
    other = "000_gain-0.5"
    results_before = json.loads((camp / "results.json").read_text())
    other_entry_before = next(e for e in results_before["runs"] if e["run_id"] == other)
    old_metrics_sha = sha((run_dir / "metrics.json").read_bytes())
    old_manifest = json.loads((run_dir / "manifest.json").read_text())
    other_manifest_before = (camp / "runs" / other / "manifest.json").read_bytes()

    fake_metrics(monkeypatch)
    result = metrics_cli(run_dir)
    assert result.exit_code == 0, result.output

    new_bytes = (run_dir / "metrics.json").read_bytes()
    assert json.loads(new_bytes) == NEW
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    assert m["metrics_sha256"] == sha(new_bytes)
    assert m["metrics_history"] == [{
        "previous_sha256": old_metrics_sha,
        "sha256": sha(new_bytes),
        "recomputed": m["metrics_history"][0]["recomputed"],
        "tool": "scieflow experiment metrics",
    }]
    assert m["code"] == old_manifest["code"] and m["inputs"] == old_manifest["inputs"]

    after = json.loads((camp / "results.json").read_text())
    entry = next(e for e in after["runs"] if e["run_id"] == run)
    assert entry["metrics"] == NEW
    assert entry["manifest_sha256"] == sha(manifest_bytes)
    assert next(e for e in after["runs"] if e["run_id"] == other) == other_entry_before
    assert {k: v for k, v in after.items() if k != "runs"} == \
        {k: v for k, v in results_before.items() if k != "runs"}
    assert (camp / "runs" / other / "manifest.json").read_bytes() == other_manifest_before

    rows = list(csv.DictReader((camp / "results.csv").open(encoding="utf-8")))
    ssim_row = next(r for r in rows if r["run_id"] == run and r["metric"] == "ssim")
    assert ssim_row["value"] == "0.123"

    verify = verify_cli(camp)
    assert verify.exit_code == 0, verify.output
    notes = json.loads(verify.output)["notes"]
    assert {"rule": "metrics-recomputed", "run_id": run,
            "path": f"runs/{run}/metrics.json"} in notes


def test_hand_edit_of_metrics_still_fails_metrics_hash(project):
    camp = sweep(project)
    path = camp / "runs" / "001_gain-1.0" / "metrics.json"
    path.write_text(json.dumps({"ssim": 0.5, "mse": 1.0}, indent=2))
    result = verify_cli(camp)
    assert result.exit_code == 1
    problems = json.loads(result.output)["problems"]
    assert {"rule": "metrics-hash", "run_id": "001_gain-1.0",
            "path": "runs/001_gain-1.0/metrics.json"} in problems


def test_run_outside_the_results_file_changes_only_its_own_files(project, monkeypatch):
    camp_stage_dir = project / "solo"
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_dir = execute_run(campaign, stage, RunSpec(run_id="manual_gain-1.0", params={"gain": 1.0}),
                          camp_stage_dir, mode="direct")
    campaign_dir = run_dir.parent.parent
    assert not (campaign_dir / "results.json").exists()
    fake_metrics(monkeypatch)
    assert metrics_cli(run_dir).exit_code == 0
    assert not (campaign_dir / "results.json").exists()
    assert not (campaign_dir / "results.csv").exists()
    m = json.loads((run_dir / "manifest.json").read_text())
    assert m["metrics_sha256"] == sha((run_dir / "metrics.json").read_bytes())


def test_legacy_run_folder_gets_no_manifest_on_recompute(project, monkeypatch):
    camp = sweep(project)
    run = "001_gain-1.0"
    run_dir = camp / "runs" / run
    (run_dir / "manifest.json").unlink()
    results = json.loads((camp / "results.json").read_text())
    for entry in results["runs"]:
        entry.pop("manifest_sha256", None)
    (camp / "results.json").write_text(json.dumps(results, indent=2))
    fake_metrics(monkeypatch)
    assert metrics_cli(run_dir).exit_code == 0
    assert not (run_dir / "manifest.json").exists()
    assert json.loads((run_dir / "metrics.json").read_text()) == NEW
    entry = next(e for e in json.loads((camp / "results.json").read_text())["runs"] if e["run_id"] == run)
    assert entry["metrics"] == NEW
    assert "manifest_sha256" not in entry


def test_recompute_inside_a_run_emits_a_value_free_note(project, monkeypatch):
    ws = project / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    camp = sweep(project, experiments_dir=ws / "experiments")
    fake_metrics(monkeypatch)
    run_dir = camp / "runs" / "001_gain-1.0"
    assert metrics_cli(run_dir).exit_code == 0
    notes = [e for e in events.read(ws) if e["type"] == "note.experiment_analysis"]
    assert len(notes) == 1
    data = notes[0]["data"]
    assert data["kind"] == "metrics-recompute"
    assert data["campaign"] == "gain-sweep" and data["run_id"] == "001_gain-1.0"
    assert data["sha256"] == sha((run_dir / "metrics.json").read_bytes())
    assert "ssim" not in data and 0.123 not in data.values()
