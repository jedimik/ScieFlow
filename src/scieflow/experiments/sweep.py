from __future__ import annotations

import json
from pathlib import Path

import yaml

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
    runs = []
    for spec in campaign.expand():
        run_dir = execute_run(campaign, stage, spec, experiments_dir, mode=mode)
        config = yaml.safe_load((run_dir / "config.yaml").read_text())
        metrics_file = run_dir / "metrics.json"
        runs.append(
            {
                "run_id": spec.run_id,
                "params": spec.params,
                "status": config["status"],
                "metrics": (
                    json.loads(metrics_file.read_text())
                    if metrics_file.exists()
                    else {}
                ),
            }
        )
    results_path = campaign_dir / "results.json"
    results_path.write_text(
        json.dumps(
            {"campaign": campaign.name, "rank_by": campaign.rank_by, "runs": runs},
            indent=2,
        )
    )
    return results_path
