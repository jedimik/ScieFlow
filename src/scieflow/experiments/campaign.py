from __future__ import annotations

import dataclasses
import itertools
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .stage import Stage


class CampaignError(ValueError):
    pass


# Keys this loader understands (M-A-D5). `backend: remote` (M-D3) needs `remote`
# and may carry `resources`; both are refused for a local campaign by name.
KNOWN_KEYS = frozenset({
    "name", "pipeline", "stage", "input", "reference", "metrics", "parameters",
    "scenarios", "validation", "rank_by", "notes", "replicates", "analysis",
    "backend", "pipeline_dir",  # pipeline_dir: written by `sweep` into campaign.yaml
    "remote", "resources",
})
SUPPORTED_BACKENDS = ("local", "remote")
REMOTE_ONLY_KEYS = ("remote", "resources")
REMOTE_JOBS = ("per-run", "array")
TOKEN_RE = re.compile(r"[\w.\-]+")
DIR_RE = re.compile(r"[\w./\-]+")
WALLTIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}")
MAX_REPLICATES = 1000
MAX_SEED = 2**31 - 1


def _check_keys(path, data: dict) -> None:
    backend = data.get("backend", "local")
    if backend not in SUPPORTED_BACKENDS:
        raise CampaignError(
            f"{path}: backend {backend!r} is not supported (supported: local, remote)"
        )
    if backend != "remote" and any(k in data for k in REMOTE_ONLY_KEYS):
        raise CampaignError(f"{path}: remote and resources need backend: remote")
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        raise CampaignError(
            f"{path}: unknown campaign key(s): {', '.join(unknown)}"
        )


@dataclass(frozen=True)
class RemoteTarget:
    """The `remote:` block of a `backend: remote` campaign (M-D3 plan 5.1)."""

    name: str  # an entry of config/remotes.yml
    dir: str  # absolute remote folder, inside the policy's allowed_dirs
    jobs: str = "per-run"  # per-run | array


@dataclass(frozen=True)
class Resources:
    """The `resources:` block; defaults equal remote.py submit's defaults (M-D3 plan 5.1)."""

    walltime: str = "01:00:00"
    cpus: int = 1
    mem_gb: int = 4
    gpus: int = 0
    queue: str = "default"
    scratch_type: str = "none"
    scratch_gb: int = 0


def _parse_remote(path, value) -> RemoteTarget:
    if not isinstance(value, dict):
        raise CampaignError(
            f"{path}: backend: remote needs a 'remote' mapping (name, dir, optional jobs)"
        )
    unknown = sorted(str(k) for k in set(value) - {"name", "dir", "jobs"})
    if unknown:
        raise CampaignError(f"{path}: remote: unknown key(s) {', '.join(unknown)}")
    name = value.get("name")
    if not isinstance(name, str) or not TOKEN_RE.fullmatch(name):
        raise CampaignError(
            f"{path}: remote.name must be a token of letters, digits, '.', '_' or '-'"
        )
    folder = value.get("dir")
    if (not isinstance(folder, str) or not DIR_RE.fullmatch(folder)
            or not folder.startswith("/") or folder == "/" or folder.startswith("//")
            or posixpath.normpath(folder) != folder or ".." in folder.split("/")):
        raise CampaignError(
            f"{path}: remote.dir must be an absolute, normalized folder with no '..' "
            "(letters, digits, '.', '_', '-', '/')"
        )
    jobs = value.get("jobs", "per-run")
    if jobs not in REMOTE_JOBS:
        raise CampaignError(f"{path}: remote.jobs must be one of per-run, array")
    return RemoteTarget(name=name, dir=folder, jobs=jobs)


def _parse_resources(path, value) -> Resources:
    if not isinstance(value, dict):
        raise CampaignError(f"{path}: resources must be a mapping")
    fields_ = {f.name for f in dataclasses.fields(Resources)}
    unknown = sorted(str(k) for k in set(value) - fields_)
    if unknown:
        raise CampaignError(f"{path}: resources: unknown key(s) {', '.join(unknown)}")
    out = {}
    for key, v in value.items():
        if key == "walltime":
            if not (isinstance(v, str) and WALLTIME_RE.fullmatch(v)):
                raise CampaignError(
                    f"{path}: resources.walltime must be quoted HH:MM:SS, e.g. \"01:00:00\""
                )
        elif key in ("queue", "scratch_type"):
            if not (isinstance(v, str) and TOKEN_RE.fullmatch(v)):
                raise CampaignError(
                    f"{path}: resources.{key} must be a token of letters, digits, '.', '_' or '-'"
                )
        else:
            minimum = 1 if key in ("cpus", "mem_gb") else 0
            if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
                raise CampaignError(f"{path}: resources.{key} must be an integer >= {minimum}")
        out[key] = v
    return Resources(**out)


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
    backend: str = "local"  # local | remote (M-D3)
    remote: RemoteTarget | None = None  # set for backend: remote only
    resources: Resources | None = None  # set for backend: remote only (defaults filled)

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
        backend = data.get("backend", "local")
        remote = resources = None
        if backend == "remote":
            remote = _parse_remote(path, data.get("remote"))
            resources = _parse_resources(path, data["resources"]) if "resources" in data else Resources()
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
            backend=backend,
            remote=remote,
            resources=resources,
        )
        if backend == "remote":
            # The campaign name becomes a remote folder and a task name (plan 5.1, review r1).
            # str-only: TOKEN_RE would raise on a non-string. '.' and '..' match the token
            # pattern but name the parent folder itself.
            name = campaign.name
            if (not isinstance(name, str) or not TOKEN_RE.fullmatch(name)
                    or set(name) <= {"."}):
                raise CampaignError(
                    f"{path}: campaign name {name!r} is not allowed in a remote campaign "
                    "(letters, digits, '.', '_', '-' only; not '.' or '..')"
                )
            # Run ids become remote path components and ledger task names (plan 5.1).
            for spec in campaign.expand():
                if not TOKEN_RE.fullmatch(spec.run_id):
                    raise CampaignError(
                        f"{path}: run id {spec.run_id!r} is not allowed in a remote campaign "
                        "(letters, digits, '.', '_', '-' only; rename the scenario or values)"
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
        if self.backend == "remote":  # local campaign.yaml stays byte-identical (plan 5.1)
            data["backend"] = "remote"
            data["remote"] = {"name": self.remote.name, "dir": self.remote.dir,
                              "jobs": self.remote.jobs}
            data["resources"] = dataclasses.asdict(self.resources)
        return data


def check_replicates(campaign: Campaign, stage: Stage) -> None:
    """Refuse replicates of a stage that is not seeded, before any run or spend (plan 5.1)."""
    if campaign.replicates is not None and not stage.accepts_seed:
        raise CampaignError(
            f"stage {stage.name} does not declare accepts_seed: true; "
            "replicates of a deterministic stage would be identical"
        )
