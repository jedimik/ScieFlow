"""M-D2 t2: `scieflow experiment verify` (plan 5.3) — each hash rule, legacy notes, exit codes."""

import importlib.util
import json
from pathlib import Path

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
