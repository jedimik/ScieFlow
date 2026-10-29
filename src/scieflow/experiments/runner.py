from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import manifest, tidy
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
    if spec.seed is not None:
        inner += ["--seed", str(spec.seed)]
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
    context: dict | None = None,
) -> Path:
    run_dir = Path(experiments_dir).resolve() / campaign.name / "runs" / spec.run_id
    artifacts = run_dir / "artifacts"
    logs = run_dir / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    pipeline_dir = stage.dir.parent.parent
    if context is None:  # a sweep passes one context for all its runs (plan 5.3)
        context = manifest.capture_context(campaign, stage, mode, run_dir.parent.parent)
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
    if spec.replicate is not None or spec.seed is not None:  # replicate or `run --seed`
        config.update(config_id=spec.config_id, replicate=spec.replicate, seed=spec.seed)
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

    doc = manifest.run_manifest(
        run_dir, campaign_name=campaign.name, spec=spec, mode=mode, status=status,
        returncode=proc.returncode, started=config["started"], finished=config["finished"],
        context=context,
    )
    manifest.write_manifest(run_dir, doc)
    return run_dir


def recompute_metrics(run_dir: str | Path) -> dict:
    """Recompute a run's metrics as a recorded, coherent update (M-D2 plan 5.3).

    Rewrites metrics.json, then (when present) the run's manifest (metrics_sha256 and one
    metrics_history entry), then the campaign's results.json entry and results.csv for this run.
    A run that is not listed in results.json changes only its own files.
    """
    from .sweep import update_run_entry  # sweep imports this module; import lazily

    run_dir = Path(run_dir)
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    if not config.get("reference"):
        raise ValueError(f"{run_dir}: run has no reference; cannot compute metrics")
    load_local_metrics(Path(config["pipeline_dir"]) / "metrics")
    output_path = run_dir / "artifacts" / config["output_file"]
    metrics = compute_metrics(config["metrics"], output_path, config["reference"])
    data = json.dumps(metrics, indent=2).encode()

    # Read-only checks first: a corrupt record must leave every file exactly as it was (review r1 F4).
    campaign_dir = run_dir.parent.parent
    if (campaign_dir / "results.json").is_file():
        manifest.load_results(campaign_dir)  # ValueError when malformed
    manifest_path = run_dir / manifest.MANIFEST_FILE
    doc = None
    if manifest_path.is_file():
        try:
            doc = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as e:
            raise ValueError(f"{manifest_path.name} is not readable JSON ({type(e).__name__})") from e
        if not isinstance(doc, dict) or not isinstance(doc.get("metrics_history", []), list):
            raise ValueError(f"{manifest_path.name} is not a valid run manifest")

    manifest.atomic_write(run_dir / "metrics.json", data)
    manifest_sha = None
    if doc is not None:
        new_sha = manifest.sha256_bytes(data)
        doc.setdefault("metrics_history", []).append({
            "previous_sha256": doc.get("metrics_sha256"),
            "sha256": new_sha,
            "recomputed": manifest.utc_now(),
            "tool": "scieflow experiment metrics",
        })
        doc["metrics_sha256"] = new_sha
        manifest_sha = manifest.write_manifest(run_dir, doc)

    update_run_entry(campaign_dir, config.get("run_id", run_dir.name),
                     metrics=metrics, manifest_sha256=manifest_sha)
    return metrics
