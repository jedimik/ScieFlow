from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .campaign import Campaign, RunSpec
from .metrics import compute_metrics, load_local_metrics
from .stage import Stage, sif_path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_command(
    stage: Stage, spec: RunSpec, input_path: Path, artifacts_dir: Path, mode: str
) -> list[str]:
    inner = [
        "python",
        str(stage.dir / stage.script),
        "--input",
        str(input_path),
        "--output",
        str(artifacts_dir),
    ]
    for key in sorted(spec.params):
        inner += [f"--{key}", str(spec.params[key])]
    if mode == "direct":
        return [sys.executable] + inner[1:]
    if mode == "apptainer":
        sif = sif_path(stage)
        if not sif.exists():
            raise FileNotFoundError(
                f"{sif} not built — run: scieflow experiment env build {stage.dir}"
            )
        binds = {str(stage.dir), str(input_path.parent), str(artifacts_dir.parent)}
        cmd = ["apptainer", "exec"]
        for b in sorted(binds):
            cmd += ["--bind", b]
        return cmd + [str(sif)] + inner
    raise ValueError(f"unknown mode '{mode}'")


def _environment_info(mode: str, command: list[str], stage: Stage) -> dict:
    info = {"mode": mode, "command": command, "recorded": _utcnow()}
    if mode == "direct":
        info["python"] = sys.version
    else:
        sif = sif_path(stage)
        info["sif"] = str(sif)
        info["sif_sha256"] = hashlib.sha256(sif.read_bytes()).hexdigest()
        version = subprocess.run(
            ["apptainer", "--version"], capture_output=True, text=True
        )
        info["apptainer_version"] = version.stdout.strip()
    return info


def execute_run(
    campaign: Campaign,
    stage: Stage,
    spec: RunSpec,
    experiments_dir: str | Path,
    mode: str = "apptainer",
) -> Path:
    run_dir = Path(experiments_dir).resolve() / campaign.name / "runs" / spec.run_id
    artifacts = run_dir / "artifacts"
    logs = run_dir / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    pipeline_dir = stage.dir.parent.parent
    command = build_command(stage, spec, campaign.input, artifacts, mode)
    config = {
        "campaign": campaign.name,
        "run_id": spec.run_id,
        "params": spec.params,
        "input": str(campaign.input),
        "reference": str(campaign.reference) if campaign.reference else None,
        "output_file": stage.output_file,
        "metrics": campaign.metrics,
        "pipeline_dir": str(pipeline_dir),
        "status": "running",
        "started": _utcnow(),
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    proc = subprocess.run(command, capture_output=True, text=True)
    (logs / "stdout.log").write_text(proc.stdout)
    (logs / "stderr.log").write_text(proc.stderr)

    output_path = artifacts / stage.output_file
    status = "completed" if proc.returncode == 0 and output_path.exists() else "failed"
    config.update(status=status, returncode=proc.returncode, finished=_utcnow())
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    (run_dir / "environment.json").write_text(
        json.dumps(_environment_info(mode, command, stage), indent=2)
    )

    if status == "completed" and campaign.reference:
        load_local_metrics(pipeline_dir / "metrics")
        metrics = compute_metrics(campaign.metrics, output_path, campaign.reference)
        (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return run_dir


def recompute_metrics(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    if not config.get("reference"):
        raise ValueError(f"{run_dir}: run has no reference; cannot compute metrics")
    load_local_metrics(Path(config["pipeline_dir"]) / "metrics")
    output_path = run_dir / "artifacts" / config["output_file"]
    metrics = compute_metrics(config["metrics"], output_path, config["reference"])
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics
