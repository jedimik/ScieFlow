"""M-D2 t3 case 7 and the stats/recompute freshness path of case 6, through the CLI."""

import importlib.util
import json
from pathlib import Path

import scipy
from click.testing import CliRunner

from scieflow.experiments import analysis
from scieflow.experiments.cli import experiment
from scieflow.experiments.sweep import run_sweep
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.stage import find_stage

_SUPPORT = Path(__file__).with_name("stats_support.py")
_spec = importlib.util.spec_from_file_location("stats_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

_REP = Path(__file__).with_name("replicate_support.py")
_rspec = importlib.util.spec_from_file_location("replicate_support", _REP)
rep = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(rep)


def _stats(cdir, *args):
    return CliRunner().invoke(experiment, ["stats", *args, str(cdir)])


def test_missing_results_json_exits_1_naming_the_file(tmp_path):
    cdir = tmp_path / "bare"
    cdir.mkdir()
    (cdir / "campaign.yaml").write_text("name: bare\n")
    result = _stats(cdir)
    assert result.exit_code == 1
    assert "results.json" in result.output


def test_pairwise_test_without_baseline_exits_1_no_baseline(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "welch-t-test"})
    result = _stats(cdir, "--include-direct")
    assert result.exit_code == 1
    assert "no-baseline" in result.output
    assert not (cdir / "stats.json").exists()


def test_json_prints_the_document_and_writes_stats_json(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={
        "baseline": support.BASELINE, "test": "welch-t-test"})
    result = _stats(cdir, "--include-direct", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["version"] == 1 and len(doc["tests"]) == 2
    assert json.loads((cdir / "stats.json").read_text()) == doc
    assert analysis.stats_fresh(cdir)


def test_no_analysis_block_prints_descriptives_and_no_tests(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")
    result = _stats(cdir, "--include-direct", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["tests"] == []


def test_scipy_older_than_1_15_refuses_and_writes_nothing(tmp_path, monkeypatch):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "descriptive"})
    monkeypatch.setattr(scipy, "__version__", "1.14.1")
    result = _stats(cdir, "--include-direct")
    assert result.exit_code == 1
    assert "needs the experiments extra (scipy >= 1.15); found 1.14.1" in result.output
    assert not (cdir / "stats.json").exists()


def test_end_to_end_sweep_then_metrics_recompute_stales_stats(project):
    rep.add_seeded_stage(project)
    campaign = Campaign.load(rep.write_campaign(
        project, rep.campaign_dict(
            replicates=3,
            analysis={"baseline": "000_gain-0.5", "test": "welch-t-test"})))
    stage = find_stage(project / "pipelines", "demo", "seeded")
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    cdir = project / "experiments" / "rep-sweep"

    first = _stats(cdir, "--include-direct")
    assert first.exit_code == 0, first.output
    assert analysis.stats_fresh(cdir)

    run_dir = cdir / "runs" / "001_gain-1.0_r0"
    recomputed = CliRunner().invoke(experiment, ["metrics", str(run_dir)])
    assert recomputed.exit_code == 0, recomputed.output
    assert analysis.stats_fresh(cdir) is False  # the recompute rewrote results.json

    again = _stats(cdir, "--include-direct", "--json")
    assert again.exit_code == 0, again.output
    assert analysis.stats_fresh(cdir)
    doc = json.loads(again.output)
    recorded = json.loads((cdir / "results.json").read_text())
    run = next(r for r in recorded["runs"] if r["run_id"] == "001_gain-1.0_r0")
    value = doc["configs"][1]["metrics"]["ssim"]["values"][0]
    assert value["run_id"] == "001_gain-1.0_r0"
    assert value["value"] == run["metrics"]["ssim"]
