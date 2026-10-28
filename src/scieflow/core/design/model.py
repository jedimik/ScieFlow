"""Study design model (M-D1 plan section 5.2): read, check and hash a run's design.yml.

A run's design lives in workspace/<slug>/design/design.yml. This module reads and checks it. The
lock ledger (lock.py, t4) and the campaign rule (t5) build on it.

Loading is fail closed, like core/data/levels.py: a symlink, an unreadable file, a YAML error, an
empty file or a non-mapping is Invalid. Only a plain absent name is Absent.

Messages name a rule and a key path, never a free-text value from the design file. Rules are the
plan's table (5.2) plus `shape`, for a value of the wrong structure (not named in that table).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

import yaml

from scieflow.core.data import levels
from scieflow.core.design import reporting
from scieflow.core.design import schema as design_schema

DESIGN_DIR = "design"
DESIGN_FILE = "design.yml"
ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,40}")
OUTCOME_ROLES = ("outcome", "index-test", "reference-standard")
POWER_TOLERANCE_ABS = 1e-8
POWER_TOLERANCE_REL = 1e-6


@dataclass(frozen=True)
class Absent:
    """No design/design.yml (a plain absent name)."""


@dataclass(frozen=True)
class Invalid:
    """design/design.yml exists but cannot be read as a design; `reason` names the cause."""

    reason: str


@dataclass(frozen=True)
class Problem:
    rule: str
    where: str
    message: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "where": self.where, "message": self.message}

    def __str__(self) -> str:
        return f"[{self.rule}] {self.where}: {self.message}"


@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"errors": [p.to_dict() for p in self.errors],
                "warnings": [p.to_dict() for p in self.warnings]}


# --- reading -------------------------------------------------------------------------------------

def design_path(ws) -> Path:
    return Path(ws) / DESIGN_DIR / DESIGN_FILE


def load(ws):
    """The design mapping, Absent, or Invalid(reason). A symlinked directory or file is Invalid."""
    directory = Path(ws) / DESIGN_DIR
    if directory.is_symlink():
        return Invalid("design/: is a symlink; use a regular directory")
    path = directory / DESIGN_FILE
    try:
        if path.is_symlink():
            return Invalid("design/design.yml: is a symlink; use a regular file")
        if not path.exists():
            return Absent()
        if not path.is_file():
            return Invalid("design/design.yml: is not a regular file")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Invalid("design/design.yml: cannot be read")
    try:
        raw = yaml.safe_load(text)
    except Exception:  # YAMLError, ValueError, RecursionError: all fail closed
        return Invalid("design/design.yml: not readable YAML")
    if raw is None:
        return Invalid("design/design.yml: the file is empty")
    if not isinstance(raw, dict):
        return Invalid("design/design.yml: the top level must be a mapping")
    return raw


def content_hash(obj) -> str:
    """SHA-256 of canonical JSON (keys sorted, no spaces, no NaN). Plan 5.2, M-D1-D2."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sap_metrics(design) -> list:
    """The campaign metric names that SAP outcomes name, sorted and unique."""
    sap = design.get("sap") if isinstance(design, dict) else None
    outcomes = sap.get("outcomes") if isinstance(sap, dict) else None
    if not isinstance(outcomes, list):
        return []
    names = {o["metric"] for o in outcomes
             if isinstance(o, dict) and isinstance(o.get("metric"), str)
             and not _is_todo(o["metric"])}
    return sorted(names)


def power_document(ws, design):
    """The locked power file's mapping when the design names one, else None."""
    sample = design.get("sample_size") if isinstance(design, dict) else None
    if not isinstance(sample, dict) or sample.get("method") != "power":
        return None
    rel = sample.get("power_file")
    if _power_path_problem(Path(ws), rel) is not None:
        return None
    return _read_json(Path(ws), rel)


# --- helpers ---------------------------------------------------------------------------------------

def _is_todo(value) -> bool:
    return isinstance(value, str) and (value == "TODO" or value.startswith("TODO:"))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _at(where: str, key) -> str:
    return f"{where}.{key}" if where else str(key)


def _idx(where: str, index: int) -> str:
    return f"{where}[{index}]"


def _json_problems(value, where, out: list) -> None:
    """Every value must be plain JSON (plan 5.2 `json-value`): no YAML date, NaN or inf."""
    here = where or "top level"
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            out.append(Problem("json-value", here, "must be a finite number"))
        return
    if isinstance(value, date):
        out.append(Problem("json-value", here, "is a YAML date or time; quote it as text"))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_problems(item, _idx(where, index), out)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                out.append(Problem("json-value", here, "has a key that is not text"))
                continue
            _json_problems(item, _at(where, key), out)
        return
    out.append(Problem("json-value", here, "has a value that is not plain JSON"))


def _todo_problems(value, where, out: list) -> None:
    if isinstance(value, str):
        if _is_todo(value):
            out.append(Problem("todo", where or "top level", "is still a TODO placeholder"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _todo_problems(item, _idx(where, index), out)
    elif isinstance(value, dict):
        for key, item in value.items():
            _todo_problems(item, _at(where, key), out)


def _enum(value, allowed, where: str, errors: list, rule: str = "enum") -> None:
    if value is None or _is_todo(value):
        return
    if value not in allowed:
        errors.append(Problem(rule, where, "is not one of the values in schemas/design.yml"))


def _text(container: dict, where: str, keys, errors: list) -> None:
    for key in keys:
        if key in container and not isinstance(container[key], str):
            errors.append(Problem("shape", _at(where, key), "must be text"))


def _items(container: dict, where: str, key: str, errors: list, *, required: bool = False):
    """(path, mapping) for each item of a list, or shape problems for anything else."""
    path = _at(where, key)
    value = container.get(key)
    if value is None:
        if required:
            errors.append(Problem("shape", path, f"{key} must list at least one item"))
        return []
    if not isinstance(value, list):
        errors.append(Problem("shape", path, f"{key} must be a list"))
        return []
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(Problem("shape", _idx(path, index), "must be a mapping"))
            continue
        items.append((_idx(path, index), item))
    if required and not items:
        errors.append(Problem("shape", path, f"{key} must list at least one item"))
    return items


def _mapping(container: dict, where: str, key: str, errors: list):
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(Problem("shape", _at(where, key), "must be a mapping"))
        return None
    return value


def _keys(mapping: dict, where: str, spec: dict, errors: list, *, rule: str = "keys",
          skip=()) -> None:
    """Unknown keys (named by position, never by text) and missing required keys."""
    known = set(spec["required"]) | set(spec["optional"])
    for position, key in enumerate(mapping, start=1):
        if key not in known:
            errors.append(Problem("keys", where or "top level",
                                  f"unknown key at position {position}"))
    for key in spec["required"]:
        if key not in mapping and key not in skip:
            errors.append(Problem(rule, _at(where, key), f"{key} is required"))


def _ids(items, errors: list) -> set:
    """Valid, unique ids of one list (plan 5.2 `id`). Returns the valid ids."""
    seen: set = set()
    for where, item in items:
        value = item.get("id")
        if value is None or _is_todo(value):
            continue
        if not (isinstance(value, str) and ID_PATTERN.fullmatch(value)):
            errors.append(Problem("id", _at(where, "id"), "must match ^[a-z][a-z0-9_-]{0,40}$"))
            continue
        if value in seen:
            errors.append(Problem("id", _at(where, "id"), "repeats an id in this list"))
            continue
        seen.add(value)
    return seen


def _ref(value, pool: set, where: str, errors: list, *, many: bool = False) -> None:
    """Plan 5.2 `ref`: a reference must name an id of the list it points into."""
    if value is None:
        return
    if many:
        if not isinstance(value, list):
            errors.append(Problem("shape", where, "must be a list"))
            return
        candidates = value
    else:
        candidates = [value]
    for item in candidates:
        if _is_todo(item):
            continue
        if not (isinstance(item, str) and item in pool):
            errors.append(Problem("ref", where, "names no id in the list it refers to"))
            return


def _split_ok(split, var_ids: set) -> bool:
    if not isinstance(split, dict):
        return False
    fraction, seed, stratify = split.get("fraction"), split.get("seed"), split.get("stratify_by")
    if not (_number(fraction) and 0 < fraction < 1):
        return False
    if not (isinstance(seed, int) and not isinstance(seed, bool)):
        return False
    return isinstance(stratify, list) and all(
        isinstance(s, str) and s in var_ids for s in stratify)


def _power_path_problem(ws: Path, rel) -> str | None:
    """Why a power file reference is unacceptable (plan 5.2 `sample-size`), or None."""
    if not isinstance(rel, str):
        return "must be text"
    if levels.path_problem(rel):
        return "must be run-relative and free of '..'"
    parts = PurePosixPath(rel).parts
    if parts[0] != DESIGN_DIR or len(parts) < 2:
        return "must lie under design/"
    current = ws
    for part in parts:
        current = current / part
        if current.is_symlink():
            return "must not be a symlink"
    if not current.is_file():
        return "is missing"
    return None


def _read_json(ws: Path, rel: str):
    try:
        doc = json.loads((ws / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _same_result(stored, fresh: dict) -> bool:
    """Plan 5.4 tolerances: integers exact, power absolute 1e-8, other numbers relative 1e-6."""
    if not isinstance(stored, dict) or set(stored) != set(fresh):
        return False
    for key, value in fresh.items():
        other = stored[key]
        if isinstance(value, int) and isinstance(other, int) and not (
                isinstance(value, bool) or isinstance(other, bool)):
            if value != other:
                return False
            continue
        if not (_number(value) and _number(other)):
            return False
        if key == "power":
            if abs(float(other) - float(value)) > POWER_TOLERANCE_ABS:
                return False
        elif not math.isclose(float(other), float(value), rel_tol=POWER_TOLERANCE_REL, abs_tol=0.0):
            return False
    return True


# --- check (plan 5.2) ----------------------------------------------------------------------------

def check(project, ws, design: dict) -> Report:
    """Every error and warning of a design (plan 5.2). Raises SchemaError only if the schema is bad."""
    schema = design_schema.load(project.root)
    enums, keys = schema["enums"], schema["item_keys"]
    report = Report()
    errors, warnings = report.errors, report.warnings
    ws = Path(ws)

    _json_problems(design, "", errors)
    _todo_problems(design, "", errors)
    _top_level(design, schema, errors)
    _text(design, "", ("title", "question", "notes"), errors)

    study = design.get("study_type")
    _enum(study, enums["study_types"], "study_type", errors)
    computational = study == "computational"
    rct = study == "rct"

    pico = _mapping(design, "", "pico", errors)
    if pico is not None:
        _keys(pico, "pico", keys["pico"], errors)
        _text(pico, "pico", tuple(pico), errors)

    variables = _items(design, "", "variables", errors, required=True)
    for where, var in variables:
        _keys(var, where, keys["variable"], errors)
        _enum(var.get("role"), enums["variable_roles"], _at(where, "role"), errors)
        _enum(var.get("type"), enums["variable_types"], _at(where, "type"), errors)
    var_ids = _ids(variables, errors)
    var_roles = {v.get("id"): v.get("role") for _, v in variables if isinstance(v.get("id"), str)}

    groups = _items(design, "", "groups", errors)
    for where, group in groups:
        _keys(group, where, keys["group"], errors)
    _ids(groups, errors)
    if rct and len(groups) < 2:
        errors.append(Problem("rct", "groups", "a randomised trial needs at least two groups"))

    timeline = _items(design, "", "timeline", errors)
    for where, row in timeline:
        _keys(row, where, keys["timeline"], errors)
    _ids(timeline, errors)

    sampling = _mapping(design, "", "sampling", errors)
    if sampling is not None:
        _keys(sampling, "sampling", keys["sampling"], errors)
        _enum(sampling.get("method"), enums["sampling"], "sampling.method", errors)

    randomisation = _mapping(design, "", "randomisation", errors)
    if rct and randomisation is None:
        errors.append(Problem("rct", "randomisation", "a randomised trial needs a randomisation"))
    if randomisation is not None:
        _keys(randomisation, "randomisation", keys["randomisation"], errors)
        _ref(randomisation.get("stratified_by"), var_ids, "randomisation.stratified_by", errors,
             many=True)

    blinding = _mapping(design, "", "blinding", errors)
    if blinding is not None:
        _keys(blinding, "blinding", keys["blinding"], errors)
        for name in ("participants", "assessors", "analysts"):
            if name in blinding and not isinstance(blinding[name], bool):
                errors.append(Problem("shape", _at("blinding", name), "must be true or false"))

    _sample_size(design, ws, keys, computational, errors, warnings)
    _sap(design, enums, keys, schema["sap_keys"], computational, var_ids, var_roles, errors,
         warnings)
    _guidelines(design, project, study, computational, errors, warnings)
    _data(design, ws, enums, keys, errors, warnings)
    return report


def _top_level(design: dict, schema: dict, errors: list) -> None:
    spec = schema["top_keys"]
    known = set(spec["required"]) | set(spec["optional"])
    for position, key in enumerate(design, start=1):
        if key not in known:
            errors.append(Problem("keys", "top level", f"unknown key at position {position}"))
    for key in spec["required"]:
        if key not in design:
            errors.append(Problem("keys", key, f"{key} is required"))
    if "version" in design:
        version = design["version"]
        if type(version) is not int or version != schema["format_version"]:
            errors.append(Problem("keys", "version", f"version must be {schema['format_version']}"))


def _sample_size(design: dict, ws: Path, keys: dict, computational: bool, errors: list,
                 warnings: list) -> None:
    if design.get("sample_size") is None:
        if not computational:
            warnings.append(Problem("no-sample-size", "sample_size",
                                    "no sample size is recorded; use scieflow experiment power"))
        return
    sample = design["sample_size"]
    if not isinstance(sample, dict):
        errors.append(Problem("sample-size", "sample_size", "must be a mapping"))
        return
    method = sample.get("method")
    if method is None or _is_todo(method):
        if method is None:
            errors.append(Problem("sample-size", "sample_size.method", "is required"))
        return
    if method not in ("power", "fixed", "not-applicable"):
        errors.append(Problem("sample-size", "sample_size.method",
                              "must be power, fixed or not-applicable"))
        return
    spec = {"power": "sample_size_power", "fixed": "sample_size_fixed",
            "not-applicable": "sample_size_not_applicable"}[method]
    _keys(sample, "sample_size", keys[spec], errors, rule="sample-size")
    if method == "fixed":
        n = sample.get("n")
        if not (isinstance(n, int) and not isinstance(n, bool) and n > 0) and not _is_todo(n):
            errors.append(Problem("sample-size", "sample_size.n", "must be a whole number above zero"))
    if method == "power":
        _power_file(sample.get("power_file"), ws, errors, warnings)


def _power_file(rel, ws: Path, errors: list, warnings: list) -> None:
    where = "sample_size.power_file"
    if _is_todo(rel):
        return
    problem = _power_path_problem(ws, rel)
    if problem is not None:
        errors.append(Problem("sample-size", where, problem))
        return
    doc = _read_json(ws, rel)
    if doc is None:
        errors.append(Problem("sample-size", where, "is not a readable JSON object"))
        return
    try:
        power = importlib.import_module("scieflow.experiments.power")
    except ImportError:
        warnings.append(Problem("power-unverified", where,
                                "the experiments extra is missing, so the power file was not recomputed"))
        return
    try:
        fresh = power.recompute(doc)
    except Exception:  # any failure to solve the stored inputs is a refusal, not a pass
        errors.append(Problem("power-mismatch", where,
                              "the inputs do not solve or the stored solve is unknown"))
        return
    if not _same_result(doc.get("result"), fresh["result"]):
        errors.append(Problem("power-mismatch", where,
                              "the stored result differs from a recomputation of its inputs"))


def _sap(design: dict, enums: dict, keys: dict, sap_keys: dict, computational: bool,
         var_ids: set, var_roles: dict, errors: list, warnings: list) -> None:
    sap = _mapping(design, "", "sap", errors) or {}
    _keys(sap, "sap", sap_keys, errors)
    estimands = _items(sap, "sap", "estimands", errors)
    outcomes = _items(sap, "sap", "outcomes", errors)
    sensitivity = _items(sap, "sap", "sensitivity", errors)
    for where, est in estimands:
        _keys(est, where, keys["estimand"], errors)
    for where, out in outcomes:
        _keys(out, where, keys["outcome"], errors, skip=("test",))
    for where, sens in sensitivity:
        _keys(sens, where, keys["sensitivity"], errors)
    est_ids = _ids(estimands, errors)
    out_ids = _ids(outcomes, errors)
    _ids(sensitivity, errors)
    for where, est in estimands:
        _ref(est.get("outcome"), out_ids, _at(where, "outcome"), errors)

    metrics: dict = {}
    for where, out in outcomes:
        _enum(out.get("priority"), enums["outcome_priorities"], _at(where, "priority"), errors)
        if "test" not in out:
            errors.append(Problem("test", _at(where, "test"), "is required: name the analysis"))
        else:
            _enum(out["test"], enums["tests"], _at(where, "test"), errors)
        _ref(out.get("variable"), var_ids, _at(where, "variable"), errors)
        _ref(out.get("covariates"), var_ids, _at(where, "covariates"), errors, many=True)
        _ref(out.get("estimand"), est_ids, _at(where, "estimand"), errors)
        metric = out.get("metric")
        if isinstance(metric, str) and not _is_todo(metric):
            if metric in metrics:
                errors.append(Problem("metric", _at(where, "metric"),
                                      f"repeats the metric named at {metrics[metric]}"))
            else:
                metrics[metric] = _at(where, "metric")
        variable = out.get("variable")
        role = var_roles.get(variable) if isinstance(variable, str) else None
        if role is not None and role not in OUTCOME_ROLES:
            warnings.append(Problem("outcome-role", _at(where, "variable"),
                                    "the variable's role is not an outcome, index-test or reference-standard"))

    primary = [o for _, o in outcomes if o.get("priority") == "primary"]
    if not computational and not primary:
        errors.append(Problem("primary", "sap.outcomes",
                              "needs at least one outcome with priority primary"))

    mult = _mapping(sap, "sap", "multiplicity", errors)
    if mult is not None:
        _keys(mult, "sap.multiplicity", keys["multiplicity"], errors, skip=("alpha",))
        _enum(mult.get("method"), enums["multiplicity"], "sap.multiplicity.method", errors)
        alpha = mult.get("alpha")
        if not _is_todo(alpha) and not (_number(alpha) and 0 < alpha < 1):
            errors.append(Problem("alpha", "sap.multiplicity.alpha", "must be a number between 0 and 1"))
    method = mult.get("method") if mult else None
    if len(primary) > 1 and method in (None, "none"):
        warnings.append(Problem("multiplicity", "sap.multiplicity",
                                "more than one primary outcome and no multiplicity method"))

    exploratory = _mapping(sap, "sap", "exploratory", errors)
    split_ok = False
    fdr_ok = False
    if exploratory is not None:
        _keys(exploratory, "sap.exploratory", keys["exploratory"], errors, rule="exploratory")
        split = exploratory.get("split")
        if split is not None:
            if isinstance(split, dict):
                _keys(split, "sap.exploratory.split", keys["split"], errors, rule="exploratory")
            split_ok = _split_ok(split, var_ids)
            if not split_ok:
                errors.append(Problem("exploratory", "sap.exploratory.split",
                                      "needs a fraction between 0 and 1, an integer seed and stratify_by variable ids"))
        fdr_ok = exploratory.get("fdr") == "bh"
        if "fdr" in exploratory and not fdr_ok and not _is_todo(exploratory.get("fdr")):
            errors.append(Problem("exploratory", "sap.exploratory.fdr", "must be bh"))
    has_exploratory = any(o.get("priority") == "exploratory" for _, o in outcomes)
    if not computational and has_exploratory and not (split_ok and fdr_ok):
        errors.append(Problem("exploratory", "sap.outcomes",
                              "an exploratory outcome needs sap.exploratory with a split and fdr: bh"))

    missing = _mapping(sap, "sap", "missing_data", errors)
    if missing is not None:
        _keys(missing, "sap.missing_data", keys["missing_data"], errors)
        _enum(missing.get("strategy"), enums["missing_data"], "sap.missing_data.strategy", errors)
    elif not computational:
        warnings.append(Problem("missing-data", "sap.missing_data",
                                "no missing-data strategy is recorded"))

    interim = _mapping(sap, "sap", "interim", errors)
    if interim is not None:
        _keys(interim, "sap.interim", keys["interim"], errors)
        if "planned" in interim and not isinstance(interim["planned"], bool):
            errors.append(Problem("shape", "sap.interim.planned", "must be true or false"))


def _guidelines(design: dict, project, study, computational: bool, errors: list,
                warnings: list) -> None:
    items = design.get("reporting_guidelines")
    if items is None:
        ids = []
    elif not isinstance(items, list):
        errors.append(Problem("shape", "reporting_guidelines", "must be a list"))
        return
    else:
        ids = items
    if not computational and not any(not _is_todo(g) for g in ids if g is not None):
        warnings.append(Problem("no-guideline", "reporting_guidelines",
                                "no reporting guideline is named; M-F will name one for journal work"))
    try:
        known = {c.id: c for c in reporting.registry(project.root)}
    except reporting.ReportingError:
        known = None
        if ids:
            errors.append(Problem("guideline", "reporting_guidelines",
                                  "the reporting checklists under config/reporting cannot be loaded"))
    for index, gid in enumerate(ids):
        where = _idx("reporting_guidelines", index)
        if _is_todo(gid) or known is None:
            continue
        if not (isinstance(gid, str) and gid in known):
            errors.append(Problem("guideline", where, "names no checklist in config/reporting/index.yml"))
            continue
        checklist = known[gid]
        if study not in checklist.applies_to:
            warnings.append(Problem("guideline-fit", where,
                                    "the checklist does not list this study type in applies_to"))
        if checklist.verification.get("status") == "unverified":
            warnings.append(Problem("guideline-unverified", where,
                                    "the checklist is unverified; check it against the official source"))


def _data(design: dict, ws: Path, enums: dict, keys: dict, errors: list, warnings: list) -> None:
    data = _mapping(design, "", "data", errors)
    if data is None:
        return
    _keys(data, "data", keys["data"], errors)
    needed = data.get("levels_needed")
    if needed is not None and not isinstance(needed, list):
        errors.append(Problem("shape", "data.levels_needed", "must be a list"))
        needed = None
    for index, level in enumerate(needed or []):
        _enum(level, enums["data_levels"], _idx("data.levels_needed", index), errors, rule="levels")
    for where, source in _items(data, "data", "sources", errors):
        _keys(source, where, keys["source"], errors)
        _enum(source.get("level"), enums["data_levels"], _at(where, "level"), errors, rule="levels")
        path = source.get("path")
        if path is not None and not _is_todo(path) and levels.path_problem(path):
            errors.append(Problem("levels", _at(where, "path"), "must be run-relative and free of '..'"))
    if isinstance(needed, list) and "L2" in needed and levels.rank(levels.run_level(ws)) < levels.rank("L2"):
        warnings.append(Problem("level-undeclared", "data.levels_needed",
                                "declare the L2 level with scieflow data declare; ScieFlow never declares it"))
