from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class CampaignError(ValueError):
    pass


@dataclass
class RunSpec:
    run_id: str
    params: dict


@dataclass
class Campaign:
    name: str
    pipeline: str
    stage: str
    input: Path
    reference: Path | None
    metrics: list[str]
    parameters: dict = field(default_factory=dict)
    scenarios: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    rank_by: str = ""
    notes: str = ""
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Campaign":
        path = Path(path).resolve()
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            raise CampaignError(f"{path}: campaign file must be a YAML mapping")
        for key in ("name", "pipeline", "stage", "input", "metrics"):
            if key not in data:
                raise CampaignError(f"{path}: missing required field '{key}'")
        if bool(data.get("parameters")) == bool(data.get("scenarios")):
            raise CampaignError(
                f"{path}: define exactly one of 'parameters' or 'scenarios'"
            )
        base = path.parent
        ref = data.get("reference")
        metrics = list(data["metrics"])
        return cls(
            name=data["name"],
            pipeline=data["pipeline"],
            stage=data["stage"],
            input=(base / data["input"]).resolve(),
            reference=(base / ref).resolve() if ref else None,
            metrics=metrics,
            parameters=data.get("parameters") or {},
            scenarios=data.get("scenarios") or [],
            validation=data.get("validation") or {},
            rank_by=data.get("rank_by") or metrics[0],
            notes=data.get("notes", ""),
            path=path,
        )

    def expand(self) -> list[RunSpec]:
        if self.scenarios:
            specs = []
            for i, scenario in enumerate(self.scenarios):
                params = dict(scenario)
                name = params.pop("name", f"scenario{i}")
                specs.append(RunSpec(run_id=f"{i:03d}_{name}", params=params))
            return specs
        fixed, grids = {}, {}
        for pname, pspec in self.parameters.items():
            if not isinstance(pspec, dict) or not ({"grid", "value"} & set(pspec)):
                raise CampaignError(f"parameter '{pname}' needs 'grid' or 'value'")
            if "grid" in pspec:
                grid = list(pspec["grid"])
                if not grid:
                    raise CampaignError(f"parameter '{pname}' has an empty grid")
                grids[pname] = grid
            else:
                fixed[pname] = pspec["value"]
        keys = sorted(grids)
        combos = list(itertools.product(*(grids[k] for k in keys))) or [()]
        specs = []
        for i, combo in enumerate(combos):
            params = dict(fixed)
            params.update(dict(zip(keys, combo)))
            slug = "_".join(f"{k}-{params[k]}" for k in keys) or "single"
            specs.append(RunSpec(run_id=f"{i:03d}_{slug}", params=params))
        return specs

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pipeline": self.pipeline,
            "stage": self.stage,
            "input": str(self.input),
            "reference": str(self.reference) if self.reference else None,
            "metrics": self.metrics,
            "parameters": self.parameters,
            "scenarios": self.scenarios,
            "validation": self.validation,
            "rank_by": self.rank_by,
            "notes": self.notes,
        }
