from scieflow.experiments.campaign import Campaign
from scieflow.experiments.report import generate_report
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep


def test_generate_report(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")

    report_path = generate_report(project / "experiments/gain-sweep")
    text = report_path.read_text()
    assert "# Campaign report: gain-sweep" in text
    assert "gain-1.0" in text          # best run named
    assert "| run" in text             # results table
    assert "plots/ssim.png" in text    # plot embedded
    assert (project / "experiments/gain-sweep/plots/ssim.png").exists()


def test_generate_report_preserves_agent_evaluation(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")

    report_path = generate_report(project / "experiments/gain-sweep")
    with report_path.open("a") as f:
        f.write("\n## Evaluation\n\nAgent notes here.\n")

    report_path = generate_report(project / "experiments/gain-sweep")
    text = report_path.read_text()
    assert "Agent notes here." in text
    assert text.count("# Campaign report:") == 1
