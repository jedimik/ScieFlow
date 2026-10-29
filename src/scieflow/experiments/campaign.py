from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .stage import Stage


class CampaignError(ValueError):
    pass


# Keys this loader understands (M-A-D5). `backend: local` loads; remote
# campaigns are refused until M-D3, so their keys fail loudly, not silently.
KNOWN_KEYS = frozenset({
    "name", "pipeline", "stage", "input", "reference", "metrics", "parameters",
    "scenarios", "validation", "rank_by", "notes", "replicates", "analysis",
    "backend", "pipeline_dir",  # pipeline_dir: written by `sweep` into campaign.yaml
})
REMOTE_ONLY_KEYS = ("remote", "resources")
REMOTE_REFUSAL = (
    "remote campaigns are not run by `scieflow experiment` yet; drive them "
    "through skills/remote-exec/SKILL.md (M-D3 makes them first-class)"
)
MAX_REPLICATES = 1000
MAX_SEED = 2**31 - 1


def _check_keys(path, data: dict) -> None:
    backend = data.get("backend", "local")
    if backend == "remote" or any(k in data for k in REMOTE_ONLY_KEYS):
        raise CampaignError(f"{path}: {REMOTE_REFUSAL}")
    if backend != "local":
        raise CampaignError(
            f"{path}: backend {backend!r} is not supported (supported: local)"
        )
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        raise CampaignError(
            f"{path}: unknown campaign key(s): {', '.join(unknown)}"
        )


@dataclass(frozen=True)
class Replicates:
    """Repeat every configuration `n` times; replicate k uses seed `seed + k`."""

    n: int
    seed: int


def parse_replicates(value) -> Replicates:
    """`replicates: 5` (n=5, seed 0) or `replicates: {n: 5, seed: 1000}` (plan 5.1)."""

    def refuse(why: str) -> CampaignError:
        return CampaignError(
            f"replicates: {why} (use an integer 1..{MAX_REPLICATES} "
            "or {n: INT, seed: INT})"
        )

    if isinstance(value, bool):
        raise refuse("a boolean is not a replicate count")
    if isinstance(value, int):
        n, seed = value, 0
    elif isinstance(value, dict):
        unknown = sorted(str(k) for k in set(value) - {"n", "seed"})
        if unknown:
            raise refuse(f"unknown key(s) {', '.join(unknown)}")
        if "n" not in value:
            raise refuse("'n' is required")
        n, seed = value["n"], value.get("seed", 0)
    else:
        raise refuse(f"unsupported value {value!r}")
    for name, v in (("n", n), ("seed", seed)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise refuse(f"{name} must be an integer")
    if not 1 <= n <= MAX_REPLICATES:
        raise refuse(f"n must be 1..{MAX_REPLICATES}")
    if not 0 <= seed <= MAX_SEED:
        raise refuse(f"seed must be 0..{MAX_SEED}")
    if seed + n - 1 > MAX_SEED:
        raise refuse("seed + n - 1 exceeds 2**31-1")
    return Replicates(n=n, seed=seed)


@dataclass
class RunSpec:
    run_id: str
    params: dict
    config_id: str | None = None  # defaults to run_id (a configuration is its own id)
    replicate: int | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.config_id is None:
            self.config_id = self.run_id


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
    replicates: Replicates | None = None
    analysis: dict | None = None  # validated by analysis.parse_analysis (M-D2 plan 5.4)

    @classmethod
    def load(cls, path: str | Path) -> "Campaign":
        path = Path(path).resolve()
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            raise CampaignError(f"{path}: campaign file must be a YAML mapping")
        _check_keys(path, data)
        for key in ("name", "pipeline", "stage", "input", "metrics"):
            if key not in data:
                raise CampaignError(f"{path}: missing required field '{key}'")
        if bool(data.get("parameters")) == bool(data.get("scenarios")):
            raise CampaignError(
                f"{path}: define exactly one of 'parameters' or 'scenarios'"
            )
        replicates = None
        if "replicates" in data:
            try:
                replicates = parse_replicates(data["replicates"])
            except CampaignError as e:
                raise CampaignError(f"{path}: {e}") from e
        if replicates is not None:
            parameters = data.get("parameters") or {}
            scenarios = data.get("scenarios") or []
            if not isinstance(parameters, dict):
                raise CampaignError(f"{path}: parameters must be a mapping when replicates is set")
            if not isinstance(scenarios, list) or not all(
                    isinstance(scenario, dict) for scenario in scenarios):
                raise CampaignError(
                    f"{path}: scenarios must be a list of mappings when replicates is set")
            if "seed" in parameters or any("seed" in scenario for scenario in scenarios):
                raise CampaignError(
                    f"{path}: 'seed' is set by replicates; remove it from parameters/scenarios"
                )
        base = path.parent
        ref = data.get("reference")
        metrics = list(data["metrics"])
        analysis_block = data.get("analysis")
        if analysis_block is not None:
            from . import analysis as analysis_mod  # lazy: analysis imports manifest
            try:
                analysis_mod.parse_analysis(analysis_block, metrics)
            except analysis_mod.AnalysisError as e:
                raise CampaignError(f"{path}: {e}") from e
        campaign = cls(
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
            replicates=replicates,
            analysis=analysis_block,
        )
        if analysis_block is not None:
            try:
                analysis_mod.resolve_baseline(
                    analysis_mod.parse_analysis(analysis_block, metrics),
                    [(spec.run_id, spec.params) for spec in campaign.configurations()])
            except analysis_mod.AnalysisError as e:
                raise CampaignError(f"{path}: {e}") from e
        return campaign

    def configurations(self) -> list[RunSpec]:
        """One spec per configuration (parameter grid or scenario), as before replicates."""
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

    def expand(self) -> list[RunSpec]:
        """Configurations, each repeated per replicate: configurations outer, replicates inner."""
        specs = self.configurations()
        if self.replicates is None:
            return specs
        out = []
        for spec in specs:
            for k in range(self.replicates.n):
                out.append(RunSpec(
                    run_id=f"{spec.run_id}_r{k}",
                    params=dict(spec.params),
                    config_id=spec.run_id,
                    replicate=k,
                    seed=self.replicates.seed + k,
                ))
        return out

    def to_dict(self) -> dict:
        data = {
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
        if self.replicates is not None:
            data["replicates"] = {"n": self.replicates.n, "seed": self.replicates.seed}
        if self.analysis is not None:
            data["analysis"] = self.analysis
        return data


def check_replicates(campaign: Campaign, stage: Stage) -> None:
    """Refuse replicates of a stage that is not seeded, before any run or spend (plan 5.1)."""
    if campaign.replicates is not None and not stage.accepts_seed:
        raise CampaignError(
            f"stage {stage.name} does not declare accepts_seed: true; "
            "replicates of a deterministic stage would be identical"
        )
