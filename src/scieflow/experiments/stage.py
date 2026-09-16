from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class StageError(ValueError):
    pass


@dataclass
class EnvSpec:
    conda_file: Path | None = None
    base_image: str = "python:3.12-slim"
    pip: list[str] = field(default_factory=list)
    apt: list[str] = field(default_factory=list)


@dataclass
class Stage:
    name: str
    description: str
    script: str
    output_file: str
    environment: EnvSpec
    dir: Path

    @classmethod
    def load(cls, stage_dir: str | Path) -> "Stage":
        stage_dir = Path(stage_dir).resolve()
        yaml_path = stage_dir / "stage.yaml"
        if not yaml_path.exists():
            raise StageError(f"no stage.yaml in {stage_dir}")
        data = yaml.safe_load(yaml_path.read_text())
        if not isinstance(data, dict):
            raise StageError(f"{yaml_path}: stage.yaml must be a YAML mapping")
        for key in ("name", "script", "output_file"):
            if key not in data:
                raise StageError(f"{yaml_path}: missing required field '{key}'")
        env = data.get("environment") or {}
        conda = env.get("conda")
        spec = EnvSpec(
            conda_file=(stage_dir / conda).resolve() if conda else None,
            base_image=env.get("base_image", "python:3.12-slim"),
            pip=list(env.get("pip") or []),
            apt=list(env.get("apt") or []),
        )
        if spec.conda_file and not spec.conda_file.exists():
            raise StageError(f"conda file not found: {spec.conda_file}")
        script_path = stage_dir / data["script"]
        if not script_path.exists():
            raise StageError(f"script not found: {script_path}")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            script=data["script"],
            output_file=data["output_file"],
            environment=spec,
            dir=stage_dir,
        )


def find_stage(pipelines_dir: str | Path, pipeline: str, stage: str) -> Stage:
    return Stage.load(Path(pipelines_dir) / pipeline / "stages" / stage)


def sif_path(stage: Stage) -> Path:
    return stage.dir / "env" / f"{stage.name}.sif"
