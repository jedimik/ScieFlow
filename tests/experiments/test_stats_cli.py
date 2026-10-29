"""M-D2 t3 case 7 and the stats/recompute freshness path of case 6, through the CLI."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest
import scipy
from click.testing import CliRunner
from scipy import stats as sps

from scieflow.core import events
from scieflow.core.run import status
from scieflow.experiments import analysis, manifest
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


@pytest.mark.parametrize("label, edit", support.MALFORMED, ids=[m[0] for m in support.MALFORMED])
def test_malformed_results_json_exits_1_without_a_traceback(tmp_path, label, edit):
    cdir = support.write_campaign(tmp_path, mode="direct")
    support.corrupt_results(cdir, edit)
    result = _stats(cdir, "--include-direct")
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)  # a one-line ClickException, not a traceback
    assert "Error: results.json" in result.output
    assert not (cdir / "stats.json").exists()


def test_stats_inside_a_run_emits_a_value_free_note(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    cdir = support.write_campaign(ws / "experiments", mode="direct", analysis={
        "baseline": support.BASELINE, "test": "welch-t-test"})
    result = _stats(cdir, "--include-direct")
    assert result.exit_code == 0, result.output
    notes = [e for e in events.read(ws) if e["type"] == "note.experiment_analysis"]
    assert len(notes) == 1
    data = notes[0]["data"]
    assert set(data) == {"kind", "campaign", "tests", "sha256"}
    assert data["kind"] == "stats" and data["campaign"] == "stat-camp" and data["tests"] == 2
    assert data["sha256"] == manifest.file_sha256(cdir / "stats.json")


def test_three_by_six_direct_sweep_stats_document(project):
    rep.add_seeded_stage(project)
    campaign = Campaign.load(rep.write_campaign(
        project, rep.campaign_dict(
            name="rep-3x6", replicates=6, metrics=["ssim"],
            parameters={"gain": {"grid": [0.5, 1.0, 1.5]}},
            analysis={"baseline": "000_gain-0.5", "test": "welch-t-test"})))
    stage = find_stage(project / "pipelines", "demo", "seeded")
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    cdir = project / "experiments" / "rep-3x6"

    result = _stats(cdir, "--include-direct", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert [c["config_id"] for c in doc["configs"]] == [
        "000_gain-0.5", "001_gain-1.0", "002_gain-1.5"]
    for config in doc["configs"]:
        assert config["n_runs"] == 6 and config["metrics"]["ssim"]["n"] == 6

    assert [t["id"] for t in doc["tests"]] == ["ssim:001_gain-1.0", "ssim:002_gain-1.5"]
    recorded = json.loads((cdir / "results.json").read_text())["runs"]

    def ssim_values(config_id):
        return [r["metrics"]["ssim"] for r in recorded if r["config_id"] == config_id]

    base = ssim_values("000_gain-0.5")
    raw = [float(sps.ttest_ind(ssim_values(c), base, equal_var=False).pvalue)
           for c in ("001_gain-1.0", "002_gain-1.5")]
    for test, p in zip(doc["tests"], raw):
        assert test["computed"] is True and test["baseline"] == "000_gain-0.5"
        assert test["p"] == pytest.approx(p, rel=1e-12)
    m = len(raw)
    order = sorted(range(m), key=lambda i: raw[i])
    holm, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * raw[i]))
        holm[i] = running
    assert [t["p_adjusted"] for t in doc["tests"]] == pytest.approx(holm, rel=1e-12)
    assert [t["reject"] for t in doc["tests"]] == [adj <= 0.05 for adj in holm]
    assert doc["families"] == [{"name": "campaign", "correction": "holm", "alpha": 0.05, "size": 2}]
    assert doc["design"]["status"] == "none"


def test_campaign_without_reference_exits_0_and_csv_rows_keep_empty_values(tmp_path):
    values = {c: {"ssim": [0.7, 0.7, 0.7]} for c in support.CONFIGS[:2]}
    cdir = support.write_campaign(tmp_path, mode="direct", reference=False, values=values,
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    result = _stats(cdir, "--include-direct")
    assert result.exit_code == 0, result.output
    with (cdir / "results.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2 * 3 * 1
    assert {row["metric"] for row in rows} == {"ssim"}
    assert all(row["value"] == "" for row in rows)
