"""A-6: --direct (development) runs are recorded but never rank by default."""

import json

from click.testing import CliRunner

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.cli import experiment as main
from scieflow.experiments.compare import compare_campaign
from scieflow.experiments.report import generate_report
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep


def _sweep_direct(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    return run_sweep(campaign, stage, project / "experiments", mode="direct")


def _results_path(project):
    return project / "experiments/gain-sweep/results.json"


def _edit_results(project, fn):
    path = _results_path(project)
    results = json.loads(path.read_text())
    fn(results)
    path.write_text(json.dumps(results, indent=2))


def _run_id_for_gain(project, gain):
    results = json.loads(_results_path(project).read_text())
    return next(r["run_id"] for r in results["runs"] if r["params"]["gain"] == gain)


def test_sweep_records_mode_per_run_and_top_level(project):
    _sweep_direct(project)
    results = json.loads(_results_path(project).read_text())
    assert results["mode"] == "direct"
    assert all(r["mode"] == "direct" for r in results["runs"])


def test_direct_runs_are_not_ranked_but_listed(project):
    _sweep_direct(project)
    container_id = _run_id_for_gain(project, 1.0)
    direct_id = _run_id_for_gain(project, 0.5)
    _edit_results(project, lambda r: [
        run.update(mode="apptainer") for run in r["runs"] if run["run_id"] == container_id
    ])

    comparison = compare_campaign(project / "experiments/gain-sweep")

    assert [r["run_id"] for r in comparison["ranked"]] == [container_id]
    assert comparison["best"] == container_id
    assert [d["run_id"] for d in comparison["direct"]] == [direct_id]
    assert direct_id not in comparison["failed"]
    assert comparison["failed"] == []


def test_include_direct_restores_todays_ranking(project):
    _sweep_direct(project)
    comparison = compare_campaign(project / "experiments/gain-sweep", include_direct=True)

    assert len(comparison["ranked"]) == 2
    assert comparison["direct"] == []
    assert comparison["best"].endswith("gain-1.0")
    assert comparison["failed"] == []


def _strip_modes(project):
    def strip(results):
        results.pop("mode", None)
        for run in results["runs"]:
            run.pop("mode", None)

    _edit_results(project, strip)


def test_legacy_results_resolve_mode_through_environment_json(project):
    _sweep_direct(project)
    _strip_modes(project)

    comparison = compare_campaign(project / "experiments/gain-sweep")

    assert comparison["ranked"] == []
    assert len(comparison["direct"]) == 2
    assert comparison["mode_unknown"] == []


def test_legacy_run_without_environment_is_mode_unknown_and_ranked(project):
    _sweep_direct(project)
    unknown_id = _run_id_for_gain(project, 1.0)
    _strip_modes(project)
    (project / "experiments/gain-sweep/runs" / unknown_id / "environment.json").unlink()

    comparison = compare_campaign(project / "experiments/gain-sweep")

    assert comparison["mode_unknown"] == [unknown_id]
    assert [r["run_id"] for r in comparison["ranked"]] == [unknown_id]
    assert comparison["best"] == unknown_id


def test_report_marks_direct_runs_and_counts_them(project):
    _sweep_direct(project)
    text = generate_report(project / "experiments/gain-sweep").read_text()

    assert "- runs: 2 (0 failed)" in text
    assert "direct runs (dev-only, not ranked): 2" in text
    assert "DIRECT, NOT RANKED" in text
    assert "n/a (no runs ranked)" in text


def test_report_include_direct_ranks_and_marks_them(project):
    _sweep_direct(project)
    text = generate_report(
        project / "experiments/gain-sweep", include_direct=True
    ).read_text()

    assert "gain-1.0" in text
    assert "(direct)" in text
    assert "DIRECT, NOT RANKED" not in text


def test_cli_sweep_direct_prints_notice(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    result = CliRunner().invoke(main, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(project / "experiments"),
        "--pipelines-dir", str(project / "pipelines"),
    ])
    assert result.exit_code == 0, result.output
    assert "not ranked" in result.output
    assert "--include-direct" in result.output


def test_cli_compare_and_report_accept_include_direct(project):
    _sweep_direct(project)
    runner = CliRunner()
    campaign_dir = str(project / "experiments/gain-sweep")

    plain = runner.invoke(main, ["compare", campaign_dir])
    assert plain.exit_code == 0, plain.output
    assert "best: None" in plain.output
    assert "[DIRECT, NOT RANKED]" in plain.output

    ranked = runner.invoke(main, ["compare", "--include-direct", campaign_dir])
    assert ranked.exit_code == 0, ranked.output
    assert "gain-1.0" in ranked.output
    assert "best: None" not in ranked.output

    report = runner.invoke(main, ["report", "--include-direct", campaign_dir])
    assert report.exit_code == 0, report.output
