from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import manifest, tidy
from .campaign import Campaign
from .runner import execute_run
from .stage import Stage


def run_sweep(
    campaign: Campaign,
    stage: Stage,
    experiments_dir: str | Path,
    mode: str = "apptainer",
) -> Path:
    campaign_dir = Path(experiments_dir) / campaign.name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.yaml").write_text(
        yaml.safe_dump(
            campaign.to_dict() | {"pipeline_dir": str(stage.dir.parent.parent)},
            sort_keys=False,
        )
    )
    context = manifest.capture_context(campaign, stage, mode, campaign_dir)
    runs = []
    for spec in campaign.expand():
        run_dir = execute_run(campaign, stage, spec, experiments_dir, mode=mode, context=context)
        config = yaml.safe_load((run_dir / "config.yaml").read_text())
        metrics_file = run_dir / "metrics.json"
        runs.append(
            {
                "run_id": spec.run_id,
                "config_id": spec.config_id,
                "replicate": spec.replicate,
                "seed": spec.seed,
                "params": spec.params,
                "mode": mode,
                "status": config["status"],
                "metrics": (
                    json.loads(metrics_file.read_text())
                    if metrics_file.exists()
                    else {}
                ),
                "manifest_sha256": manifest.file_sha256(run_dir / manifest.MANIFEST_FILE),
            }
        )
    results_path = campaign_dir / "results.json"
    doc = {
        "campaign": campaign.name,
        "rank_by": campaign.rank_by,
        "mode": mode,
        "runs": runs,
    }
    if campaign.replicates is not None:
        doc["replicates"] = {"n": campaign.replicates.n, "seed": campaign.replicates.seed}
    manifest.atomic_write(results_path, json.dumps(doc, indent=2).encode())
    tidy.write_results_csv(campaign_dir, campaign.metrics)
    return results_path


def update_run_entry(campaign_dir: str | Path, run_id: str, *, metrics: dict,
                     manifest_sha256: str | None = None) -> bool:
    """Update one run's entry in the campaign's results.json, then re-write results.csv.

    Every other field, run and top-level key is kept. Returns False (and writes nothing)
    when there is no results.json or the run is not listed in it.
    """
    campaign_dir = Path(campaign_dir)
    path = campaign_dir / "results.json"
    if not path.is_file():
        return False
    doc = manifest.load_results(campaign_dir)  # ValueError when malformed; nothing is written
    for entry in doc["runs"]:
        if entry["run_id"] == run_id:
            entry["metrics"] = metrics
            if manifest_sha256 is not None:
                entry["manifest_sha256"] = manifest_sha256
            manifest.atomic_write(path, json.dumps(doc, indent=2).encode())
            tidy.write_results_csv(campaign_dir)
            return True
    return False
