"""M-D2 t2: `scieflow experiment verify` (plan 5.3) — each hash rule, legacy notes, exit codes."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.experiments import manifest
from scieflow.experiments.cli import experiment
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("manifest_support.py")
_spec = importlib.util.spec_from_file_location("manifest_support", _SUPPORT)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

RUN = "001_gain-1.0"  # grid [0.5, 1.0]: 000 is gain 0.5, 001 is gain 1.0


def sweep(project) -> Path:
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    return run_sweep(campaign, stage, project / "experiments", mode="direct").parent


def cli_verify(camp: Path, *extra):
    return CliRunner().invoke(experiment, ["verify", *extra, str(camp)])


def rules(report) -> set:
    return {(p["rule"], p["run_id"]) for p in report.to_dict()["problems"]}


def test_clean_campaign_verifies_and_output_is_deterministic(project):
    camp = sweep(project)
    first = cli_verify(camp, "--json")
    assert first.exit_code == 0, first.output
    doc = json.loads(first.output)
    assert doc["ok"] is True and doc["problems"] == []
    assert cli_verify(camp, "--json").output == first.output


def test_tampered_artifact_is_artifact_hash(project):
    camp = sweep(project)
    (camp / "runs" / RUN / "artifacts" / "result.png").write_bytes(b"SECRET-OUTPUT")
    report = manifest.verify_campaign(camp)
    assert not report.ok
    assert ("artifact-hash", RUN) in rules(report)
    result = cli_verify(camp)
    assert result.exit_code == 1
    assert "SECRET" not in result.output  # messages never carry file contents


def test_deleted_artifact_is_artifact_missing(project):
    camp = sweep(project)
    (camp / "runs" / RUN / "artifacts" / "result.png").unlink()
    assert ("artifact-missing", RUN) in rules(manifest.verify_campaign(camp))


def test_added_artifact_is_artifact_extra(project):
    camp = sweep(project)
    (camp / "runs" / RUN / "artifacts" / "extra.txt").write_text("not in the manifest")
    assert ("artifact-extra", RUN) in rules(manifest.verify_campaign(camp))


def test_edited_manifest_is_manifest_hash(project):
    camp = sweep(project)
    path = camp / "runs" / RUN / "manifest.json"
    doc = json.loads(path.read_text())
    doc["params"]["gain"] = 0.5
    path.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n")
    report = manifest.verify_campaign(camp)
    assert ("manifest-hash", RUN) in rules(report)
    assert cli_verify(camp).exit_code == 1


def test_missing_manifest_is_manifest_missing(project):
    camp = sweep(project)
    (camp / "runs" / RUN / "manifest.json").unlink()
    assert ("manifest-missing", RUN) in rules(manifest.verify_campaign(camp))


def test_hand_edited_metrics_is_metrics_hash(project):
    camp = sweep(project)
    path = camp / "runs" / RUN / "metrics.json"
    doc = json.loads(path.read_text())
    doc["ssim"] = 0.5
    path.write_text(json.dumps(doc, indent=2))
    report = manifest.verify_campaign(camp)
    assert ("metrics-hash", RUN) in rules(report)
    assert cli_verify(camp).exit_code == 1


def test_pre_m_d2_campaign_is_a_legacy_note_not_a_failure(project):
    camp = sweep(project)
    results = json.loads((camp / "results.json").read_text())
    for entry in results["runs"]:
        entry.pop("manifest_sha256", None)
        (camp / "runs" / entry["run_id"] / "manifest.json").unlink()
    (camp / "results.json").write_text(json.dumps(results, indent=2))
    report = manifest.verify_campaign(camp)
    assert report.ok
    assert ("legacy-run", RUN) in {(n["rule"], n["run_id"]) for n in report.to_dict()["notes"]}
    assert cli_verify(camp).exit_code == 0


def rewrite_manifest(camp: Path, mutate) -> None:
    """Edit RUN's manifest the way the tool would: rewrite it and re-record its hash in results.json."""
    run_dir = camp / "runs" / RUN
    doc = json.loads((run_dir / "manifest.json").read_text())
    mutate(doc)
    sha = manifest.write_manifest(run_dir, doc)
    results = json.loads((camp / "results.json").read_text())
    next(e for e in results["runs"] if e["run_id"] == RUN)["manifest_sha256"] = sha
    (camp / "results.json").write_text(json.dumps(results, indent=2))


def set_results(camp: Path, edit) -> None:
    results = json.loads((camp / "results.json").read_text())
    edit(results)
    (camp / "results.json").write_text(json.dumps(results, indent=2))


RESULTS_INVALID = [{"rule": "results-invalid", "run_id": None, "path": "results.json"}]


@pytest.mark.parametrize("runs", [
    ["001_gain-1.0"],                     # strings instead of entries
    {"001_gain-1.0": {"metrics": {}}},   # an object instead of a list
    "001_gain-1.0",                       # a string
    [{"run_id": 5}],                      # a non-string run id
    [{"params": {}}],                     # an entry without a run id
])
def test_malformed_results_runs_are_results_invalid_not_a_traceback(project, runs):
    camp = sweep(project)
    set_results(camp, lambda doc: doc.update(runs=runs))
    assert manifest.verify_campaign(camp).to_dict()["problems"] == RESULTS_INVALID
    result = cli_verify(camp)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a handled exit, not an AttributeError


@pytest.mark.parametrize("run_id", [
    "../../escape", "/tmp/elsewhere", "a/../b", "a//b", "./a", "a/.", "a\\b", "a\0b", "..", "",
])
def test_run_id_that_escapes_or_is_not_a_plain_relative_path_is_results_invalid(project, run_id):
    camp = sweep(project)
    set_results(camp, lambda doc: doc["runs"][0].update(run_id=run_id))
    assert manifest.verify_campaign(camp).to_dict()["problems"] == RESULTS_INVALID


def test_run_id_from_a_path_like_scenario_name_verifies(project):
    """A scenario name with '/' gives a nested run id (campaign.py:177); verify must accept it."""
    campaigns = project / "pipelines" / "demo" / "campaigns"
    doc = yaml.safe_load((campaigns / "gain-sweep.yaml").read_text())
    doc.pop("parameters")
    doc["scenarios"] = [{"name": "models/a", "gain": 0.5}, {"name": "models/b", "gain": 1.0}]
    (campaigns / "slash-sweep.yaml").write_text(yaml.safe_dump(doc))
    campaign = Campaign.load(campaigns / "slash-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    camp = run_sweep(campaign, stage, project / "experiments", mode="direct").parent
    run_ids = {e["run_id"] for e in json.loads((camp / "results.json").read_text())["runs"]}
    assert run_ids == {"000_models/a", "001_models/b"}
    assert (camp / "runs" / "000_models" / "a" / "manifest.json").is_file()
    report = manifest.verify_campaign(camp)
    assert report.ok, report.to_dict()["problems"]
    assert cli_verify(camp).exit_code == 0


def test_run_id_cannot_point_verify_at_a_manifest_outside_the_campaign(project):
    camp = sweep(project)
    decoy = camp.parent / "escape"  # what camp/runs/../../escape resolves to
    decoy.mkdir()
    shutil.copy(camp / "runs" / RUN / "manifest.json", decoy / "manifest.json")
    set_results(camp, lambda doc: doc["runs"][0].update(
        run_id="../../escape", manifest_sha256=manifest.file_sha256(decoy / "manifest.json")))
    report = manifest.verify_campaign(camp)
    assert report.to_dict()["problems"] == RESULTS_INVALID
    assert report.to_dict()["notes"] == []


@pytest.mark.parametrize("path", ["/etc/hostname", "../../../outside.txt", 5])
def test_artifact_path_outside_artifacts_is_manifest_invalid(project, path):
    camp = sweep(project)
    entry = {"path": path, "sha256": "0" * 64, "size": 1}
    rewrite_manifest(camp, lambda doc: doc["artifacts"].append(entry))
    problems = manifest.verify_campaign(camp).to_dict()["problems"]
    assert problems == [{"rule": "manifest-invalid", "run_id": RUN, "path": f"runs/{RUN}/manifest.json"}]


def test_artifacts_that_are_not_a_list_are_manifest_invalid(project):
    camp = sweep(project)
    rewrite_manifest(camp, lambda doc: doc.update(artifacts=5))
    problems = manifest.verify_campaign(camp).to_dict()["problems"]
    assert problems == [{"rule": "manifest-invalid", "run_id": RUN, "path": f"runs/{RUN}/manifest.json"}]
