"""Campaign analysis (M-D2 plan 5.4): the `analysis` block, stats.json, freshness.

Reads a campaign folder (campaign.yaml, results.json, runs' metrics) and builds the
deterministic analysis document. The statistics are in stats.py; this module decides which
values feed which test, applies the guards' reasons, corrects the family and writes files.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import manifest, sap, stats, tidy
from .compare import _run_mode
from .metrics import get_metric, load_local_metrics

# Families in the order they appear in stats.json (plan 5.5).
FAMILY_ORDER = ("confirmatory", "exploratory", "campaign")
# Design status -> the message of its design-<status> warning (plan 5.5).
STATUS_MESSAGES = {
    "draft": "the design is not locked yet; the analysis is not confirmatory",
    "pending": "the design lock is awaiting its human gate; the analysis is not confirmatory",
    "modified": "the design differs from its lock; the analysis is not confirmatory",
    "invalid": "the design cannot be read; the analysis is not confirmatory",
    "ledger-invalid": "the design ledger does not verify; the analysis is not confirmatory",
    "error": "the design could not be read; campaign settings are used and no SAP is bound",
}

# Every name of the M-D1 design enumeration (schemas/design.yml enums.tests); pinned by a test.
TESTS = frozenset({
    "t-test-ind", "welch-t-test", "t-test-paired", "t-test-one", "anova-oneway", "ancova",
    "chi-square", "fisher-exact", "mann-whitney", "wilcoxon-signed-rank", "kruskal-wallis",
    "linear-regression", "logistic-regression", "mixed-model", "cox-regression", "log-rank",
    "pearson", "spearman", "roc-auc", "sensitivity-specificity", "descriptive", "other",
})
# Test -> kind (plan 5.4 table). Any other name in TESTS is "unsupported" (M-D2-D1).
KIND = {
    "welch-t-test": "pairwise", "t-test-ind": "pairwise", "mann-whitney": "pairwise",
    "t-test-paired": "paired", "wilcoxon-signed-rank": "paired",
    "anova-oneway": "omnibus", "kruskal-wallis": "omnibus",
    "pearson": "correlation", "spearman": "correlation",
    "descriptive": "none",
}
ASSUMPTIONS = {
    "welch-t-test": ["independent runs", "approximately normal metric per configuration"],
    "t-test-ind": ["independent runs", "approximately normal metric per configuration",
                   "equal variances"],
    "mann-whitney": ["independent runs", "same-shaped distributions under the null"],
    "t-test-paired": ["paired seeds", "approximately normal paired differences"],
    "wilcoxon-signed-rank": ["paired seeds", "symmetric paired differences"],
    "anova-oneway": ["independent runs", "approximately normal metric per configuration",
                     "equal variances"],
    "kruskal-wallis": ["independent runs", "same-shaped distributions under the null"],
    "pearson": ["bivariate normal"],
    "spearman": ["monotone association"],
}
ALLOWED_KEYS = ("baseline", "test", "correction", "alpha", "ci", "bootstrap")
BOOTSTRAP_KEYS = ("resamples", "seed")
MIN_RESAMPLES, MAX_RESAMPLES = 1000, 100000
MAX_SEED = 2**31 - 1


class AnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class Analysis:
    baseline: object = None  # a configuration id (str) or a parameter mapping
    tests: dict = field(default_factory=dict)  # metric -> test name
    correction: str = "holm"
    alpha: float = 0.05
    ci: float = 0.95
    resamples: int = 9999
    seed: int = 0
    given: frozenset = frozenset()  # the keys the block states explicitly (correction, alpha)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_test(path: str, name) -> None:
    if not isinstance(name, str) or name not in TESTS:
        raise AnalysisError(f"{path}: {name!r} is not a design test (see schemas/design.yml)")


def parse_analysis(block, metrics) -> Analysis | None:
    """Validate a campaign's `analysis` block (plan 5.4). None when there is no block."""
    if block is None:
        return None
    if not isinstance(block, dict):
        raise AnalysisError("analysis: must be a mapping")
    for key in block:
        if key not in ALLOWED_KEYS:
            raise AnalysisError(
                f"analysis.{key}: unknown key (allowed: {', '.join(ALLOWED_KEYS)})")

    baseline = block.get("baseline")
    if baseline is not None and (isinstance(baseline, bool) or not isinstance(baseline, (str, dict))):
        raise AnalysisError("analysis.baseline: a configuration id or a parameter mapping")

    tests: dict = {}
    spec = block.get("test")
    if isinstance(spec, str):
        _check_test("analysis.test", spec)
        tests = {m: spec for m in metrics}
    elif isinstance(spec, dict):
        for metric, name in spec.items():
            if metric not in metrics:
                raise AnalysisError(
                    f"analysis.test.{metric}: not a campaign metric (metrics: {', '.join(metrics)})")
            _check_test(f"analysis.test.{metric}", name)
            tests[metric] = name
    elif spec is not None:
        raise AnalysisError("analysis.test: a test name or a {metric: test} mapping")

    correction = block.get("correction", "holm")
    if correction not in stats.CORRECTIONS:
        raise AnalysisError(
            f"analysis.correction: {correction!r} (one of {', '.join(stats.CORRECTIONS)})")
    alpha = block.get("alpha", 0.05)
    if not _is_number(alpha) or not 0 < alpha < 1:
        raise AnalysisError("analysis.alpha: a number with 0 < alpha < 1")
    ci = block.get("ci", 0.95)
    if not _is_number(ci) or not 0.5 <= ci < 1:
        raise AnalysisError("analysis.ci: a number with 0.5 <= ci < 1")

    resamples, seed = 9999, 0
    boot = block.get("bootstrap")
    if boot is not None:
        if not isinstance(boot, dict):
            raise AnalysisError("analysis.bootstrap: a mapping with resamples and seed")
        for key in boot:
            if key not in BOOTSTRAP_KEYS:
                raise AnalysisError(f"analysis.bootstrap.{key}: unknown key")
        resamples = boot.get("resamples", 9999)
        if (isinstance(resamples, bool) or not isinstance(resamples, int)
                or not MIN_RESAMPLES <= resamples <= MAX_RESAMPLES):
            raise AnalysisError(
                f"analysis.bootstrap.resamples: an integer {MIN_RESAMPLES}..{MAX_RESAMPLES}")
        seed = boot.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
            raise AnalysisError(f"analysis.bootstrap.seed: an integer 0..{MAX_SEED}")

    return Analysis(baseline=baseline, tests=tests, correction=correction, alpha=float(alpha),
                    ci=float(ci), resamples=resamples, seed=seed,
                    given=frozenset(k for k in ("correction", "alpha") if k in block))


def resolve_baseline(spec: Analysis | None, configs) -> str | None:
    """The one configuration id the analysis compares with. configs: [(config_id, params)]."""
    if spec is None or spec.baseline is None:
        return None
    if isinstance(spec.baseline, str):
        matches = [c for c, _ in configs if c == spec.baseline]
    else:
        matches = [c for c, params in configs if params == spec.baseline]
    if len(matches) != 1:
        raise AnalysisError(
            f"analysis.baseline: {spec.baseline!r} matches {len(matches)} configurations "
            "(exactly one is required)")
    return matches[0]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as e:
        raise AnalysisError(f"{path.name}: missing (run scieflow experiment sweep first)") from e
    except (OSError, ValueError) as e:
        raise AnalysisError(f"{path.name}: unreadable JSON") from e


def _read_yaml(path: Path):
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as e:
        raise AnalysisError(f"{path.name}: missing") from e
    except (OSError, yaml.YAMLError) as e:
        raise AnalysisError(f"{path.name}: unreadable YAML") from e
    if not isinstance(data, dict):
        raise AnalysisError(f"{path.name}: must be a mapping")
    return data


def _finite(value) -> bool:
    """A finite number. An int too large for a float is not finite (review F1, plan 5.12)."""
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _check_results(results) -> None:
    """Refuse a results.json whose shape the analysis cannot read, naming the rule (plan 5.12)."""
    if not isinstance(results, dict):
        raise AnalysisError("results.json: must be a JSON object")
    runs = results.get("runs")
    if not isinstance(runs, list):
        raise AnalysisError("results.json: 'runs' must be a list")
    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            raise AnalysisError(f"results.json: runs[{i}] must be a mapping")
        for key in ("run_id", "config_id", "status"):
            if not isinstance(run.get(key), str):
                raise AnalysisError(f"results.json: runs[{i}].{key} must be a string")
        for key in ("params", "metrics"):
            if run.get(key) is not None and not isinstance(run[key], dict):
                raise AnalysisError(f"results.json: runs[{i}].{key} must be a mapping")
        seed = run.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise AnalysisError(f"results.json: runs[{i}].seed must be an integer or null")


def _cell(runs, metric, include_direct, modes):
    """Descriptives of one configuration × metric with the excluded runs listed (plan 5.4)."""
    values, excluded = [], []
    for r in runs:
        run_id, seed = r["run_id"], r.get("seed")
        if modes[run_id] == "direct" and not include_direct:
            continue
        if r["status"] != "completed":
            continue
        raw = (r.get("metrics") or {}).get(metric)
        if raw is None or isinstance(raw, bool) or not _is_number(raw):
            excluded.append({"run_id": run_id, "seed": seed, "reason": "missing"})
        elif not _finite(raw):
            excluded.append({"run_id": run_id, "seed": seed, "reason": "non-finite"})
        else:
            values.append({"run_id": run_id, "seed": seed, "value": float(raw)})
    cell = stats.describe([v["value"] for v in values])
    cell["n_missing"] = sum(1 for e in excluded if e["reason"] == "missing")
    cell["n_nonfinite"] = sum(1 for e in excluded if e["reason"] == "non-finite")
    cell["values"] = values
    cell["excluded"] = excluded
    cell["ci"] = None
    cell["ci_note"] = None
    return cell


def _usable(cell) -> list[float]:
    return [v["value"] for v in cell["values"]]


def _entry(metric, test, kind, *, config_id=None, baseline=None, n=None, n_baseline=None,
           n_unpaired=0, outcome=None, effect=None):
    outcome = outcome or stats._not("test-unsupported")
    if config_id is not None:
        ident = f"{metric}:{config_id}"
    else:
        ident = f"{metric}:{kind}"
    return {
        "id": ident, "metric": metric, "kind": kind, "test": test, "source": "campaign",
        "config_id": config_id, "baseline": baseline, "n": n, "n_baseline": n_baseline,
        "n_unpaired": n_unpaired, "computed": outcome["computed"], "reason": outcome["reason"],
        "statistic": outcome["statistic"], "df": outcome["df"], "p": outcome["p"],
        "p_adjusted": None, "reject": None, "family": "campaign", "confirmatory": False,
        "effect": effect if outcome["computed"] else None, "warnings": outcome["warnings"],
    }


def _pairwise(metric, test, config_cell, base_cell, config_id, baseline_id, higher):
    cfg, base = _usable(config_cell), _usable(base_cell)
    if len(cfg) < 2:
        outcome = stats._not("n<2")
    elif not base:
        outcome = stats._not("no-baseline-run")
    else:
        fn = stats.welch_t if test == "welch-t-test" else (
            stats.student_t if test == "t-test-ind" else stats.mann_whitney)
        outcome = fn(cfg, base)
    effect = None
    if outcome["computed"]:
        effect = stats.effect_sizes(cfg, base, higher_is_better=higher)
    return _entry(metric, test, "pairwise", config_id=config_id, baseline=baseline_id,
                  n=len(cfg), n_baseline=len(base), outcome=outcome, effect=effect)


def _paired(metric, test, config_cell, base_cell, config_id, baseline_id, higher, replicated):
    if not replicated:
        outcome = stats._not("not-replicates")
        return _entry(metric, test, "paired", config_id=config_id, baseline=baseline_id,
                      n=0, n_baseline=0, outcome=outcome)
    by_seed_b = {v["seed"]: v["value"] for v in base_cell["values"] if v["seed"] is not None}
    pairs = [(v["seed"], v["value"], by_seed_b[v["seed"]]) for v in config_cell["values"]
             if v["seed"] in by_seed_b]
    cfg_pairs = [c for _, c, _ in pairs]
    base_pairs = [b for _, _, b in pairs]
    unpaired = (len(_usable(config_cell)) - len(pairs)) + (len(_usable(base_cell)) - len(pairs))
    fn = stats.paired_t if test == "t-test-paired" else stats.wilcoxon
    outcome = fn(cfg_pairs, base_pairs)
    effect = None
    if outcome["computed"]:
        effect = stats.effect_sizes(cfg_pairs, base_pairs, higher_is_better=higher,
                                    pairs=(cfg_pairs, base_pairs))
    return _entry(metric, test, "paired", config_id=config_id, baseline=baseline_id,
                  n=len(pairs), n_baseline=len(pairs), n_unpaired=unpaired,
                  outcome=outcome, effect=effect)


def _correlation(metric, test, configs, cells, params, higher):
    """One varying numeric parameter (x) against the metric (y), over every usable run."""
    keys = sorted({k for _, p in configs for k in p})
    varying = [k for k in keys if len({repr(p.get(k)) for _, p in configs}) > 1]
    xs, ys = [], []
    valid = len(varying) == 1
    if valid:
        key = varying[0]
        for config_id, p in configs:
            value = p.get(key)
            if isinstance(value, bool) or not _is_number(value) or not _finite(value):
                valid = False
                break
            for v in cells[config_id][metric]["values"]:
                xs.append(float(value))
                ys.append(v["value"])
    if not valid:
        outcome = stats._not("no-numeric-parameter")
        return _entry(metric, test, "correlation", n=0, outcome=outcome)
    fn = stats.pearson if test == "pearson" else stats.spearman
    outcome = fn(xs, ys)
    return _entry(metric, test, "correlation", n=len(xs), outcome=outcome)


def analyse_campaign(campaign_dir, include_direct: bool = False) -> dict:
    """The analysis document of one campaign folder (plan 5.4). Writes nothing."""
    campaign_dir = Path(campaign_dir)
    np, _ = stats.require_scipy()  # refuse before any computation
    import scipy

    campaign_path = campaign_dir / "campaign.yaml"
    results_path = campaign_dir / "results.json"
    campaign = _read_yaml(campaign_path)
    results = _read_json(results_path)
    _check_results(results)
    metrics_raw = campaign.get("metrics") or []
    if not isinstance(metrics_raw, list) or not all(isinstance(m, str) for m in metrics_raw):
        raise AnalysisError("campaign.yaml: 'metrics' must be a list of metric names")
    metrics = list(metrics_raw)
    if not metrics:
        raise AnalysisError("campaign.yaml: 'metrics' is required")
    if campaign.get("pipeline_dir"):
        load_local_metrics(Path(campaign["pipeline_dir"]) / "metrics")
    try:
        higher = {m: bool(get_metric(m).higher_is_better) for m in metrics}
    except KeyError as e:
        raise AnalysisError(f"campaign.yaml: {e.args[0]}") from e
    spec = parse_analysis(campaign.get("analysis"), metrics) or Analysis()
    bound = sap.binding_for_campaign(campaign_dir)
    found = sap.conflicts(bound, spec, metrics)
    if found and not bound.computational:
        first = found[0]
        raise AnalysisError(f"[{first['rule']}] {first['message']}")

    runs = results["runs"]
    modes = {r["run_id"]: _run_mode(r, campaign_dir) for r in runs}
    config_ids, params = [], {}
    for r in runs:
        if r["config_id"] not in params:
            config_ids.append(r["config_id"])
            params[r["config_id"]] = r.get("params") or {}
    configs = [(c, params[c]) for c in config_ids]
    baseline_id = resolve_baseline(spec, configs)
    replicated = isinstance(results.get("replicates"), dict)

    resolved = _resolve_tests(metrics, spec, bound)
    for metric, r in resolved.items():
        if KIND.get(r["test"]) in ("pairwise", "paired") and baseline_id is None:
            raise AnalysisError(
                f"no-baseline: analysis.test {r['test']} on {metric} needs analysis.baseline")

    cells: dict = {}
    config_rows = []
    for config_id in config_ids:
        runs_of = [r for r in runs if r["config_id"] == config_id]
        direct_excluded = sum(1 for r in runs_of
                              if modes[r["run_id"]] == "direct" and not include_direct)
        included = [r for r in runs_of
                    if not (modes[r["run_id"]] == "direct" and not include_direct)]
        cells[config_id] = {m: _cell(runs_of, m, include_direct, modes) for m in metrics}
        config_rows.append({
            "config_id": config_id, "params": params[config_id], "n_runs": len(runs_of),
            "n_failed": sum(1 for r in included if r["status"] != "completed"),
            "n_direct_excluded": direct_excluded, "metrics": None,
        })
    for row in config_rows:
        row["metrics"] = {}
        for m in metrics:
            cell = cells[row["config_id"]][m]
            ci, note = stats.bootstrap_ci(_usable(cell), level=spec.ci,
                                          resamples=spec.resamples, seed=spec.seed)
            cell["ci"], cell["ci_note"] = ci, note
            row["metrics"][m] = cell

    tests = []
    used = set()
    for metric in metrics:
        if metric not in resolved:
            continue
        r = resolved[metric]
        test = r["test"]
        kind = KIND.get(test, "unsupported")
        used.add(test)
        first_new = len(tests)
        if kind == "unsupported":
            tests.append(_entry(metric, test, "unsupported"))
        elif kind in ("pairwise", "paired"):
            for config_id in config_ids:
                if config_id == baseline_id:
                    continue
                cell_c = cells[config_id][metric]
                cell_b = cells[baseline_id][metric]
                if kind == "pairwise":
                    tests.append(_pairwise(metric, test, cell_c, cell_b, config_id,
                                           baseline_id, higher[metric]))
                else:
                    tests.append(_paired(metric, test, cell_c, cell_b, config_id, baseline_id,
                                         higher[metric], replicated))
        elif test in ("anova-oneway", "kruskal-wallis"):
            groups = [_usable(cells[c][metric]) for c in config_ids]
            fn = stats.anova_oneway if test == "anova-oneway" else stats.kruskal_wallis
            tests.append(_entry(metric, test, "omnibus", n=sum(len(g) for g in groups),
                                outcome=fn(groups)))
        else:  # correlation
            tests.append(_correlation(metric, test, configs, cells, params, higher[metric]))
        for t in tests[first_new:]:
            t["source"] = r["source"]
            t["family"] = r["family"]
            t["confirmatory"] = r["family"] == "confirmatory"
            if r["unplanned"]:
                t["unplanned"] = True

    warnings_out = []
    families = []
    for name in FAMILY_ORDER:
        group = [t for t in tests if t["family"] == name]
        if not group:
            continue
        correction, alpha, unsupported = _family_setting(name, bound, spec)
        family = [t for t in group if t["computed"]]
        if unsupported:
            for t in family:
                t["p_adjusted"] = None
                t["reject"] = None
            warnings_out.append({"rule": "sap-correction-unsupported", "metric": None,
                                 "message": (f"the locked SAP's multiplicity method "
                                             f"{bound.multiplicity['method']} is not applied; "
                                             "p-values are not adjusted")})
        else:
            for t, adjusted in zip(family, stats.adjust([t["p"] for t in family], correction)):
                t["p_adjusted"] = adjusted
                t["reject"] = adjusted <= alpha
        families.append({"name": name, "correction": correction, "alpha": alpha,
                         "size": len(family)})

    if bound.status not in ("none", "locked"):
        warnings_out.append({"rule": f"design-{bound.status}", "metric": None,
                             "message": STATUS_MESSAGES.get(
                                 bound.status, "the design is not usable; the analysis is not "
                                 "confirmatory")})
    for item in found:  # only computational designs reach here: a non-computational one refused
        warnings_out.append({"rule": item["rule"], "metric": item["metric"],
                             "message": item["message"]})
    for m in metrics:
        missing = sum(cells[c][m]["n_missing"] for c in config_ids)
        nonfinite = sum(cells[c][m]["n_nonfinite"] for c in config_ids)
        if missing:
            warnings_out.append({"rule": "values-missing", "metric": m,
                                 "message": f"{missing} completed run(s) without a usable {m} value"})
        if nonfinite:
            warnings_out.append({"rule": "values-nonfinite", "metric": m,
                                 "message": f"{nonfinite} non-finite {m} value(s) excluded"})

    return {
        "version": 1,
        "tool": "scieflow experiment stats",
        "campaign": campaign.get("name") or results.get("campaign"),
        "source": {
            "results_sha256": manifest.file_sha256(results_path),
            "campaign_sha256": manifest.file_sha256(campaign_path),
        },
        "settings": {
            "include_direct": include_direct,
            "alpha": spec.alpha,
            "ci": spec.ci,
            "correction": spec.correction,
            "bootstrap": {"resamples": spec.resamples, "seed": spec.seed},
            "baseline": baseline_id,
            "tests": {m: spec.tests[m] for m in metrics if m in spec.tests},
        },
        "design": {"status": bound.status, "version": bound.version,
                   "in_force_hash": bound.in_force_hash, "study_type": bound.study_type},
        "data_level": manifest._data_level(campaign_dir),
        "metrics": {m: {"higher_is_better": higher[m]} for m in metrics},
        "configs": config_rows,
        "tests": tests,
        "families": families,
        "method": {
            "scipy": scipy.__version__,
            "numpy": np.__version__,
            "assumptions": {t: ASSUMPTIONS[t] for t in sorted(used) if t in ASSUMPTIONS},
        },
        "warnings": warnings_out,
    }


def _family_setting(name: str, bound: sap.Binding, spec: Analysis):
    """(correction, alpha, unsupported) of one family (plan 5.5). Campaign settings when unbound."""
    if name == "campaign" or not bound.locked:
        return spec.correction, spec.alpha, False
    alpha = sap.sap_alpha(bound, spec.alpha)
    if name == "exploratory":
        return "bh", alpha, False
    expected = sap.expected_correction(bound)
    if expected is None:
        return "not-applied", alpha, True
    return expected, alpha, False


def _resolve_tests(metrics, spec: Analysis, bound: sap.Binding) -> dict:
    """metric -> {test, source, family, unplanned} for every metric that gets a test (plan 5.5)."""
    out = {}
    for metric in metrics:
        if bound.locked and metric in bound.outcomes:
            named = bound.outcomes[metric]
            test = named["test"]
            if test is None or test == "descriptive":
                continue
            family = ("confirmatory" if named["priority"] in sap.CONFIRMATORY_PRIORITIES
                      else "exploratory")
            out[metric] = {"test": test, "source": "sap", "family": family, "unplanned": False}
            continue
        test = spec.tests.get(metric)
        if test is None or test == "descriptive":
            continue
        if bound.locked:  # computational only: the metric is not an SAP outcome (plan 5.5)
            out[metric] = {"test": test, "source": "campaign", "family": "exploratory",
                           "unplanned": True}
        else:
            out[metric] = {"test": test, "source": "campaign", "family": "campaign",
                           "unplanned": False}
    return out


def write_stats(campaign_dir, doc: dict) -> Path:
    """Write stats.json (deterministic, strict JSON) and re-write results.csv.

    Both outputs and their .tmp siblings are checked before either is written (M-D2 final r1).
    """
    campaign_dir = Path(campaign_dir)
    path = campaign_dir / "stats.json"
    csv_path = campaign_dir / "results.csv"
    try:
        for target in (path, csv_path):
            manifest.refuse_unsafe_output(target)
            manifest.refuse_unsafe_output(manifest.tmp_of(target))
    except manifest.UnsafeOutputError as e:
        raise AnalysisError(str(e)) from e
    try:
        data = json.dumps(doc, sort_keys=True, indent=1, allow_nan=False).encode("utf-8")
    except ValueError as e:
        raise AnalysisError("a non-finite number reached stats.json; nothing written") from e
    try:
        manifest.atomic_write(path, data)
        tidy.write_results_csv(campaign_dir, list(doc["metrics"]))
    except manifest.UnsafeOutputError as e:
        raise AnalysisError(str(e)) from e
    return path


def stats_fresh(campaign_dir) -> bool:
    """True when stats.json records the current results.json and campaign.yaml (plan 5.4)."""
    campaign_dir = Path(campaign_dir)
    try:
        doc = json.loads((campaign_dir / "stats.json").read_text())
        source = doc.get("source") or {}
        return (
            source.get("results_sha256") == manifest.file_sha256(campaign_dir / "results.json")
            and source.get("campaign_sha256") == manifest.file_sha256(campaign_dir / "campaign.yaml")
        )
    except (OSError, ValueError, AttributeError):
        return False
