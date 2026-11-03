"""M-D3 t1: shared writers extracted from the local sweep path (plan 5.2). No behaviour change."""

import json
import sys

import yaml

from scieflow.experiments.campaign import Campaign, RunSpec
from scieflow.experiments.runner import build_command
from scieflow.experiments.stage import find_stage


def load(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    return campaign, stage


def test_stage_argv_is_the_inner_argv_of_build_command(project):
    from scieflow.experiments.runner import stage_argv

    campaign, stage = load(project)
    spec = RunSpec(run_id="000_gain-1.0", params={"gain": 1.0})
    artifacts = project / "experiments/x/runs/000/artifacts"
    argv = stage_argv(str(stage.dir), stage.script, spec, str(campaign.input), str(artifacts))
    assert argv == [
        "python", str(stage.dir / stage.script),
        "--input", str(campaign.input), "--output", str(artifacts), "--gain", "1.0",
    ]
    direct = build_command(stage, spec, campaign.input, artifacts, "direct")
    assert direct == [sys.executable] + argv[1:]


def test_stage_argv_adds_seed_last_and_sorts_params(project):
    from scieflow.experiments.runner import stage_argv

    spec = RunSpec(run_id="r", params={"sigma": 2, "gain": 0.5}, seed=7)
    argv = stage_argv("/s", "run.py", spec, "/in.png", "/out")
    assert argv == [
        "python", "/s/run.py", "--input", "/in.png", "--output", "/out",
        "--gain", "0.5", "--sigma", "2", "--seed", "7",
    ]


def test_sweep_results_json_bytes_match_the_literal_document(project):
    from scieflow.experiments import manifest
    from scieflow.experiments.sweep import run_sweep

    campaign, stage = load(project)
    results_path = run_sweep(campaign, stage, project / "experiments", mode="direct")
    runs = []
    for spec in campaign.expand():
        run_dir = project / "experiments/gain-sweep/runs" / spec.run_id
        config = yaml.safe_load((run_dir / "config.yaml").read_text())
        metrics_file = run_dir / "metrics.json"
        runs.append({
            "run_id": spec.run_id,
            "config_id": spec.config_id,
            "replicate": spec.replicate,
            "seed": spec.seed,
            "params": spec.params,
            "mode": "direct",
            "status": config["status"],
            "metrics": json.loads(metrics_file.read_text()) if metrics_file.exists() else {},
            "manifest_sha256": manifest.file_sha256(run_dir / manifest.MANIFEST_FILE),
        })
    expected = json.dumps({
        "campaign": campaign.name,
        "rank_by": campaign.rank_by,
        "mode": "direct",
        "runs": runs,
    }, indent=2).encode()
    assert results_path.read_bytes() == expected


def test_sweep_results_csv_bytes_match_the_literal_csv(project):
    """Plan 5.2 / t1 case 5: results.csv bytes, not only results.json (review r1 finding 3).

    The expected text is written out here from the run folders' metrics and params; it does
    not call tidy.write_results_csv.
    """
    from scieflow.experiments.sweep import run_sweep

    campaign, stage = load(project)
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    lines = ["campaign,config_id,run_id,replicate,seed,mode,status,metric,value,param.gain"]
    for spec in campaign.expand():
        run_dir = project / "experiments/gain-sweep/runs" / spec.run_id
        status = yaml.safe_load((run_dir / "config.yaml").read_text())["status"]
        metrics = json.loads((run_dir / "metrics.json").read_text())
        for metric in campaign.metrics:
            value = metrics.get(metric)
            cell_value = "" if value is None else repr(value)
            lines.append(
                f"gain-sweep,{spec.config_id},{spec.run_id},,,direct,{status},{metric},"
                f"{cell_value},{spec.params['gain']!r}"
            )
    expected = ("\n".join(lines) + "\n").encode()
    assert (project / "experiments/gain-sweep/results.csv").read_bytes() == expected


def test_write_results_adds_replicates_only_when_set(project, tmp_path):
    from scieflow.experiments.campaign import Replicates
    from scieflow.experiments.sweep import write_results

    campaign, _ = load(project)
    out = write_results(tmp_path, campaign, [], "direct")
    assert "replicates" not in json.loads(out.read_text())
    campaign.replicates = Replicates(n=2, seed=10)
    out = write_results(tmp_path, campaign, [], "direct")
    doc = json.loads(out.read_text())
    assert doc["replicates"] == {"n": 2, "seed": 10}
    assert doc["mode"] == "direct" and doc["runs"] == []


def test_run_entry_reads_status_metrics_and_manifest_hash(project):
    from scieflow.experiments import manifest
    from scieflow.experiments.sweep import run_entry

    campaign, stage = load(project)
    run_dir = project / "experiments/gain-sweep/runs/000_gain-1.0"
    run_dir.mkdir(parents=True)
    (run_dir / "config.yaml").write_text("status: completed\n")
    (run_dir / "metrics.json").write_text('{"ssim": 1.0}')
    (run_dir / manifest.MANIFEST_FILE).write_text("{}")
    spec = RunSpec(run_id="000_gain-1.0", params={"gain": 1.0})
    entry = run_entry(spec, run_dir, "direct")
    assert entry == {
        "run_id": "000_gain-1.0",
        "config_id": "000_gain-1.0",
        "replicate": None,
        "seed": None,
        "params": {"gain": 1.0},
        "mode": "direct",
        "status": "completed",
        "metrics": {"ssim": 1.0},
        "manifest_sha256": manifest.file_sha256(run_dir / manifest.MANIFEST_FILE),
    }
