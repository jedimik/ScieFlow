"""Pure statistics for M-D2 (plan 5.4): descriptives, bootstrap CI, tests, effect sizes, corrections.

numpy and scipy are imported lazily inside the functions (no new dependency). Every
precondition is checked before a scipy call; a NaN or infinite statistic or p-value is
never returned as a number (reason "numerical"). Each test returns a dict with
computed, reason, statistic, df, p and warnings. Nothing here reads or writes files.
"""

from __future__ import annotations

import math
import re
import warnings

MIN_SCIPY = (1, 15)  # first release whose scipy.stats.bootstrap takes rng=
CORRECTIONS = ("none", "bonferroni", "holm", "hochberg", "bh")


class StatsUnavailable(RuntimeError):
    """numpy/scipy missing, or scipy older than 1.15 (plan 5.4)."""


def _major_minor(version) -> tuple[int, int]:
    match = re.match(r"\s*(\d+)\.(\d+)", str(version))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def require_scipy():
    """Import numpy and scipy.stats; refuse scipy < 1.15 before any computation."""
    try:
        import numpy as np
        import scipy
        from scipy import stats as sps
    except ImportError as e:
        raise StatsUnavailable(
            "needs the experiments extra (scipy >= 1.15); numpy or scipy is not importable"
        ) from e
    if _major_minor(scipy.__version__) < MIN_SCIPY:
        raise StatsUnavailable(
            f"needs the experiments extra (scipy >= 1.15); found {scipy.__version__}")
    return np, sps


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _constant(values) -> bool:
    return len(set(values)) <= 1


def _not(reason, warns=None) -> dict:
    return {"computed": False, "reason": reason, "statistic": None, "df": None, "p": None,
            "warnings": list(warns or [])}


def _ok(statistic, p, df=None, warns=None) -> dict:
    if not (_finite(statistic) and _finite(p)) or (df is not None and not _finite(df)):
        return _not("numerical", warns)
    return {"computed": True, "reason": None, "statistic": float(statistic),
            "df": None if df is None else float(df), "p": float(p),
            "warnings": list(warns or [])}


def _call(fn, *args, **kwargs):
    """Run one scipy call, recording the warnings it emits."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = fn(*args, **kwargs)
    listed = [{"rule": "scipy-warning", "message": f"{w.category.__name__}: {w.message}"}
              for w in caught]
    return out, listed


def describe(values) -> dict:
    """n, mean, sd (ddof 1), median, min, max; nulls for n = 0 and sd for n < 2."""
    np, _ = require_scipy()
    arr = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("describe takes finite values only (non-finite values are excluded earlier)")
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "median": None, "min": None, "max": None}
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "sd": float(np.std(arr, ddof=1)) if n >= 2 else None,
        "median": float(np.median(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def bootstrap_ci(values, *, level: float, resamples: int, seed: int):
    """BCa interval of the mean. Returns (ci or None, note): note is "n<2" or "degenerate"."""
    np, sps = require_scipy()
    if len(values) < 2:
        return None, "n<2"
    if _constant(values):
        return None, "degenerate"
    arr = np.asarray(values, dtype=float)
    res, _ = _call(
        sps.bootstrap, (arr,), np.mean, n_resamples=resamples, confidence_level=level,
        method="BCa", rng=np.random.default_rng(seed))
    low = float(res.confidence_interval.low)
    high = float(res.confidence_interval.high)
    if not (_finite(low) and _finite(high)):
        return None, "degenerate"
    return ({"low": low, "high": high, "level": level, "method": "BCa",
             "resamples": resamples, "seed": seed}, None)


def welch_t(config, baseline) -> dict:
    _, sps = require_scipy()
    if len(config) < 2 or len(baseline) < 2:
        return _not("n<2")
    if _constant(config) and _constant(baseline):
        return _not("constant")
    res, warns = _call(sps.ttest_ind, list(config), list(baseline), equal_var=False)
    return _ok(res.statistic, res.pvalue, res.df, warns)


def student_t(config, baseline) -> dict:
    _, sps = require_scipy()
    if len(config) < 2 or len(baseline) < 2:
        return _not("n<2")
    if _constant(config) and _constant(baseline):
        return _not("constant")
    res, warns = _call(sps.ttest_ind, list(config), list(baseline), equal_var=True)
    return _ok(res.statistic, res.pvalue, res.df, warns)


def mann_whitney(config, baseline) -> dict:
    _, sps = require_scipy()
    if len(config) < 2 or len(baseline) < 2:
        return _not("n<2")
    if _constant(list(config) + list(baseline)):
        return _not("constant")
    res, warns = _call(sps.mannwhitneyu, list(config), list(baseline), use_continuity=True,
                       alternative="two-sided", method="auto")
    return _ok(res.statistic, res.pvalue, None, warns)


def paired_t(config_pairs, baseline_pairs) -> dict:
    _, sps = require_scipy()
    if len(config_pairs) == 0:
        return _not("no-pairs")
    if len(config_pairs) < 2:
        return _not("n<2")
    diffs = [c - b for c, b in zip(config_pairs, baseline_pairs)]
    if all(d == 0 for d in diffs):
        return _not("all-differences-zero")
    if _constant(diffs):
        return _not("constant")
    res, warns = _call(sps.ttest_rel, list(config_pairs), list(baseline_pairs))
    return _ok(res.statistic, res.pvalue, res.df, warns)


def wilcoxon(config_pairs, baseline_pairs) -> dict:
    _, sps = require_scipy()
    if len(config_pairs) == 0:
        return _not("no-pairs")
    diffs = [c - b for c, b in zip(config_pairs, baseline_pairs)]
    if all(d == 0 for d in diffs):
        return _not("all-differences-zero")
    res, warns = _call(sps.wilcoxon, list(config_pairs), list(baseline_pairs),
                       zero_method="wilcox", correction=False, alternative="two-sided",
                       method="auto")
    return _ok(res.statistic, res.pvalue, None, warns)


def anova_oneway(groups) -> dict:
    _, sps = require_scipy()
    kept = [list(g) for g in groups if len(g) >= 2]
    excluded = len(groups) - len(kept)
    warns = []
    if excluded:
        warns.append({"rule": "group-excluded",
                      "message": f"{excluded} configuration(s) with fewer than 2 values left out"})
    if len(kept) < 2:
        return _not("n<2", warns)
    flat = [v for g in kept for v in g]
    if _constant(flat):
        return _not("constant", warns)
    res, scipy_warns = _call(sps.f_oneway, *kept)
    out = _ok(res.statistic, res.pvalue, None, warns + scipy_warns)
    if out["computed"]:
        out["df"] = [len(kept) - 1, len(flat) - len(kept)]
    return out


def kruskal_wallis(groups) -> dict:
    _, sps = require_scipy()
    kept = [list(g) for g in groups if len(g) >= 1]
    if len(kept) < 2:
        return _not("n<2")
    flat = [v for g in kept for v in g]
    if _constant(flat):
        return _not("constant")
    res, warns = _call(sps.kruskal, *kept)
    out = _ok(res.statistic, res.pvalue, len(kept) - 1, warns)
    return out


def _correlation(fn, x, y) -> dict:
    _, sps = require_scipy()
    if len(x) < 3:
        return _not("n<3")
    if _constant(x) or _constant(y):
        return _not("constant")
    res, warns = _call(getattr(sps, fn), list(x), list(y))
    return _ok(res.statistic, res.pvalue, None, warns)


def pearson(x, y) -> dict:
    return _correlation("pearsonr", x, y)


def spearman(x, y) -> dict:
    return _correlation("spearmanr", x, y)


def effect_sizes(config, baseline, *, higher_is_better: bool = True, pairs=None) -> dict:
    """Mean difference (config minus baseline), Hedges g, Cliff's delta, d_z (paired), better."""
    nc, nb = len(config), len(baseline)
    mc = math.fsum(config) / nc if nc else None
    mb = math.fsum(baseline) / nb if nb else None
    md = mc - mb if nc and nb else None

    hedges = None
    if nc >= 2 and nb >= 2:
        vc = math.fsum((v - mc) ** 2 for v in config) / (nc - 1)
        vb = math.fsum((v - mb) ** 2 for v in baseline) / (nb - 1)
        pooled = math.sqrt(((nc - 1) * vc + (nb - 1) * vb) / (nc + nb - 2))
        if pooled > 0:
            j = 1 - 3 / (4 * (nc + nb) - 9)
            hedges = j * md / pooled

    cliffs = None
    if nc and nb:
        greater = sum(1 for c in config for b in baseline if c > b)
        less = sum(1 for c in config for b in baseline if c < b)
        cliffs = (greater - less) / (nc * nb)

    d_z = None
    if pairs is not None and len(pairs[0]) >= 2:
        diffs = [c - b for c, b in zip(pairs[0], pairs[1])]
        md_pairs = math.fsum(diffs) / len(diffs)
        sd = math.sqrt(math.fsum((d - md_pairs) ** 2 for d in diffs) / (len(diffs) - 1))
        if sd > 0:
            d_z = md_pairs / sd

    better = None
    if md is not None and md != 0:
        better = (md > 0) == bool(higher_is_better)
    return {"mean_difference": md, "hedges_g": hedges, "cliffs_delta": cliffs,
            "d_z": d_z, "better": better}


def adjust(pvalues, method: str) -> list[float]:
    """Corrected p-values of one family (plan 5.4). "none" returns the inputs unchanged."""
    p = [float(x) for x in pvalues]
    m = len(p)
    if method == "none":
        return list(p)
    if method == "bonferroni":
        return [min(1.0, m * x) for x in p]
    if method not in ("holm", "hochberg", "bh"):
        raise ValueError(f"unknown correction {method!r}")
    order = sorted(range(m), key=lambda i: p[i])
    out = [0.0] * m
    if method == "holm":
        running = 0.0
        for rank, i in enumerate(order):
            running = max(running, min(1.0, (m - rank) * p[i]))
            out[i] = running
    elif method == "hochberg":
        running = 1.0
        for rank in reversed(range(m)):
            i = order[rank]
            running = min(running, min(1.0, (m - rank) * p[i]))
            out[i] = running
    else:  # bh
        running = 1.0
        for rank in reversed(range(m)):
            i = order[rank]
            running = min(running, min(1.0, m / (rank + 1) * p[i]))
            out[i] = running
    return out
