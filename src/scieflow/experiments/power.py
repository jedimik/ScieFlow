"""Sample size and power for the experiments module (M-D1 t1).

Formulas follow the statsmodels.stats.power semantics (TTestIndPower, TTestPower,
FTestAnovaPower, NormalIndPower), implemented over scipy, which the experiments
extra already provides. statsmodels is not a dependency (M-D1-D1). Correlation
uses Fisher's z; survival uses the Schoenfeld (1983) events formula.

scipy is imported lazily inside this module. Every calculation is fail-closed:
a NaN is never returned as a power. One documented exception: a NaN lower-tail
term of the noncentral t at noncentrality >= 6 is taken as 0.0, because that
term is bounded by Phi(-6) < 1e-9 (M-D1 plan 5.4, review r3 clarification C1).
"""

from __future__ import annotations

import json
import math
from typing import Any

VERSION = 1
TOOL = "scieflow experiment power"
FAMILIES = ("t-ind", "t-paired", "t-one", "anova", "proportions", "correlation", "survival")
MAX_SIZE = 10_000_000
MAX_EFFECT = 100.0
NC_LOWER_TAIL_SAFE = 6.0


class PowerError(Exception):
    """A refused or numerically unsafe calculation (CLI exit 1)."""


class PowerUsageError(ValueError):
    """The wrong set of unknowns or options (CLI exit 2)."""


_APPLIES = {
    "t-ind": {"n", "effect_size", "ratio"},
    "t-paired": {"n", "effect_size"},
    "t-one": {"n", "effect_size"},
    "anova": {"n", "effect_size", "groups"},
    "proportions": {"n", "effect_size", "p1", "p2", "ratio"},
    "correlation": {"n", "r"},
    "survival": {"n", "events", "hazard_ratio", "allocation", "event_probability"},
}

_FLAGS = {
    "n": "--n",
    "events": "--events",
    "effect_size": "--effect-size",
    "p1": "--p1",
    "p2": "--p2",
    "r": "--r",
    "hazard_ratio": "--hazard-ratio",
    "groups": "--groups",
    "ratio": "--ratio",
    "allocation": "--allocation",
    "event_probability": "--event-probability",
}

_METHOD = {
    "t-ind": "noncentral t (statsmodels TTestIndPower semantics)",
    "t-paired": "noncentral t (statsmodels TTestPower semantics)",
    "t-one": "noncentral t (statsmodels TTestPower semantics)",
    "anova": "noncentral F (statsmodels FTestAnovaPower semantics)",
    "proportions": "normal approximation on Cohen's arcsine h (statsmodels NormalIndPower semantics)",
    "correlation": "Fisher z normal approximation",
    "survival": "Schoenfeld (1983) events formula, normal approximation",
}

_ASSUMPTIONS = {
    "t-ind": [
        "normally distributed outcome in each group",
        "equal variances",
        "independent observations",
    ],
    "t-paired": [
        "paired differences are normally distributed",
        "independent pairs",
    ],
    "t-one": [
        "outcome is normally distributed",
        "independent observations",
    ],
    "anova": [
        "normally distributed outcome in each group",
        "equal variances across groups",
        "independent observations",
    ],
    "proportions": [
        "independent groups",
        "large samples (normal approximation to the arcsine-transformed proportions)",
    ],
    "correlation": [
        "bivariate normal distribution",
        "independent pairs",
        "large samples (Fisher z approximation)",
    ],
    "survival": [
        "proportional hazards between the two groups",
        "independent censoring",
        "large samples (Schoenfeld approximation)",
    ],
}


def _stats():
    from scipy import stats

    return stats


def _nct_sf(x: float, df: float, nc: float) -> float:
    return float(_stats().nct.sf(x, df, nc))


def _nct_cdf(x: float, df: float, nc: float) -> float:
    return float(_stats().nct.cdf(x, df, nc))


def _fail(what: str = "numerical failure for these inputs") -> None:
    raise PowerError(what)


def _clip(p: float) -> float:
    if math.isnan(p):
        _fail()
    return min(max(p, 0.0), 1.0)


def ttest_power(d: float, nobs: float, df: float, alpha: float, alternative: str = "two-sided") -> float:
    """Power of a t test with standardized effect d and effective sample nobs."""
    st = _stats()
    two = alternative == "two-sided"
    a = alpha / 2 if two else alpha
    nc = d * math.sqrt(nobs)
    upper = _nct_sf(float(st.t.isf(a, df)), df, nc)
    if math.isnan(upper):
        _fail()
    if not two:
        return _clip(upper)
    lower = _nct_cdf(float(st.t.ppf(a, df)), df, nc)
    if math.isnan(lower):
        if nc < NC_LOWER_TAIL_SAFE:
            _fail()
        lower = 0.0
    return _clip(upper + lower)


def _normal_power(d: float, nobs: float, alpha: float, two: bool) -> float:
    st = _stats()
    a = alpha / 2 if two else alpha
    nc = d * math.sqrt(nobs)
    upper = float(st.norm.sf(float(st.norm.isf(a)) - nc))
    if not two:
        return _clip(upper)
    lower = float(st.norm.cdf(float(st.norm.ppf(a)) - nc))
    return _clip(upper + lower)


def _anova_power(f: float, n: float, k: int, alpha: float) -> float:
    st = _stats()
    d1, d2 = k - 1, n - k
    crit = float(st.f.isf(alpha, d1, d2))
    value = float(st.ncf.sf(crit, d1, d2, f * f * n))
    if math.isnan(value):
        _fail()
    return _clip(value)


def _survival_power(events: float, hr: float, p: float, alpha: float) -> float:
    st = _stats()
    shift = math.sqrt(events * p * (1 - p)) * abs(math.log(hr))
    z = float(st.norm.isf(alpha / 2))
    return _clip(float(st.norm.cdf(shift - z) + st.norm.cdf(-shift - z)))


def _schoenfeld_events(hr: float, p: float, alpha: float, target: float) -> float:
    st = _stats()
    z_a = float(st.norm.isf(alpha / 2))
    z_b = float(st.norm.ppf(target))
    return (z_a + z_b) ** 2 / (p * (1 - p) * math.log(hr) ** 2)


def _power_at(family: str, size: float, o: dict[str, Any]) -> float:
    """Power for a size (n1, n, n total, or events) and an effect in o['effect']."""
    two = o["alternative"] == "two-sided"
    alpha = o["alpha"]
    if family == "t-ind":
        n2 = o["ratio"] * size
        nobs = 1.0 / (1.0 / size + 1.0 / n2)
        return ttest_power(o["effect"], nobs, size + n2 - 2, alpha, o["alternative"])
    if family in ("t-paired", "t-one"):
        return ttest_power(o["effect"], size, size - 1, alpha, o["alternative"])
    if family == "anova":
        return _anova_power(o["effect"], size, o["groups"], alpha)
    if family == "proportions":
        n2 = o["ratio"] * size
        nobs = 1.0 / (1.0 / size + 1.0 / n2)
        return _normal_power(o["effect"], nobs, alpha, two)
    if family == "correlation":
        return _normal_power(o["effect"], size - 3, alpha, two)
    if family == "survival":
        return _survival_power(size, o["effect"], o["allocation"], alpha)
    raise PowerError(f"unknown family {family!r}")


def _solve_increasing(f, target: float, lo: float, start: float, cap: float, unit: str) -> float:
    """Smallest x >= lo with f(x) >= target for an increasing f (bracket, then brentq)."""
    from scipy.optimize import brentq

    if f(lo) >= target:
        return lo
    hi = start
    while f(hi) < target:
        hi *= 2
        if hi > cap:
            raise PowerError(f"needs more than {cap:g} {unit} for these inputs")
    return float(brentq(lambda x: f(x) - target, lo, hi, xtol=1e-12, maxiter=500))


def _finite(name: str, value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise PowerError(f"{name} must be a number") from None
    if not math.isfinite(v):
        raise PowerError(f"{name} must be finite")
    return v


def _check_alpha_power(alpha: float, target: float | None) -> None:
    if not 0 < alpha <= 0.5:
        raise PowerError("alpha must satisfy 0 < alpha <= 0.5")
    if target is not None and not (alpha < target < 1):
        raise PowerError(f"power must satisfy alpha < power < 1 (alpha is {alpha:g})")


def solve(
    family: str,
    *,
    alpha: float = 0.05,
    power: float | None = None,
    n: float | None = None,
    events: float | None = None,
    effect_size: float | None = None,
    p1: float | None = None,
    p2: float | None = None,
    r: float | None = None,
    hazard_ratio: float | None = None,
    groups: int | None = None,
    ratio: float | None = None,
    allocation: float | None = None,
    event_probability: float | None = None,
    alternative: str | None = None,
) -> dict[str, Any]:
    """Solve for exactly one of sample size, power or effect and return the document."""
    if family not in FAMILIES:
        raise PowerError(f"unknown family {family!r}; choose one of {', '.join(FAMILIES)}")
    alt = "two-sided" if alternative is None else alternative
    if alt == "smaller":
        raise PowerError("state the effect as a positive size and use larger")
    if alt not in ("two-sided", "larger"):
        raise PowerError(f"unknown alternative {alt!r}")
    if family in ("anova", "survival") and alt != "two-sided":
        raise PowerError(f"{family} is two-sided only")

    alpha = _finite("alpha", alpha)
    target = _finite("power", power)
    n = _finite("n", n)
    events = _finite("events", events)
    effect_size = _finite("effect_size", effect_size)
    p1 = _finite("p1", p1)
    p2 = _finite("p2", p2)
    r = _finite("r", r)
    hazard_ratio = _finite("hazard_ratio", hazard_ratio)
    ratio = _finite("ratio", ratio)
    allocation = _finite("allocation", allocation)
    event_probability = _finite("event_probability", event_probability)
    _check_alpha_power(alpha, target)

    given = {
        "n": n, "events": events, "effect_size": effect_size, "p1": p1, "p2": p2,
        "r": r, "hazard_ratio": hazard_ratio, "groups": groups, "ratio": ratio,
        "allocation": allocation, "event_probability": event_probability,
    }
    for name, value in given.items():
        if value is not None and name not in _APPLIES[family]:
            raise PowerError(f"{_FLAGS[name]} does not apply to {family}")
    if ratio is None and family in ("t-ind", "proportions"):
        ratio = 1.0
    if ratio is not None and ratio <= 0:
        raise PowerError("ratio must be greater than 0")
    if allocation is None and family == "survival":
        allocation = 0.5
    if allocation is not None and not 0 < allocation < 1:
        raise PowerError("allocation must satisfy 0 < allocation < 1")
    if groups is not None and (not isinstance(groups, int) or groups < 2):
        raise PowerError("groups must be an integer of at least 2")
    if family == "anova" and groups is None:
        raise PowerError("anova needs --groups (the number of groups, at least 2)")

    # Effect inputs and their unknowns
    if family == "proportions":
        pair = p1 is not None and p2 is not None
        if pair and effect_size is not None:
            raise PowerError("give --effect-size or both --p1 and --p2, not both")
        if (p1 is None) != (p2 is None) and effect_size is None:
            raise PowerError("proportions need both --p1 and --p2 (or --effect-size)")
        if pair:
            for name, p in (("p1", p1), ("p2", p2)):
                if not 0 < p < 1:
                    raise PowerError(f"{name} must satisfy 0 < {name} < 1")
            if p1 == p2:
                raise PowerError("p1 equals p2: there is no effect to size")
            effect_size = abs(2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2)))
        effect_known = effect_size is not None
    elif family == "correlation":
        if r is None:
            raise PowerError("correlation needs --r; solving for the correlation is not supported")
        if not 0 < abs(r) < 1:
            raise PowerError("r must satisfy 0 < |r| < 1")
        effect_known = True
    elif family == "survival":
        if hazard_ratio is None:
            raise PowerError("survival needs --hazard-ratio; solving for it is not supported")
        if hazard_ratio <= 0 or hazard_ratio == 1:
            raise PowerError("hazard_ratio must be greater than 0 and not equal to 1")
        if event_probability is not None and not 0 < event_probability <= 1:
            raise PowerError("event_probability must satisfy 0 < event_probability <= 1")
        effect_known = True
    else:
        if effect_size is not None and effect_size <= 0:
            raise PowerError("state the effect as a positive size")
        effect_known = effect_size is not None

    if family == "survival":
        if events is not None and n is not None:
            raise PowerError("give --events or --n, not both")
        if n is not None and event_probability is None:
            raise PowerError("survival --n needs --event-probability (or give --events)")
        size_unknown = events is None and n is None
        size_name = "n" if event_probability is not None else "events"
    else:
        size_unknown = n is None
        size_name = "n"

    unknown = []
    if size_unknown:
        unknown.append(size_name)
    if target is None:
        unknown.append("power")
    if not effect_known:
        unknown.append("effect")
    if len(unknown) != 1:
        got = ", ".join(unknown) if unknown else "none"
        raise PowerUsageError(
            "exactly one of the sample size, power and effect must be unknown "
            f"(leave it out); got unknown: {got}"
        )
    mode = unknown[0]

    # Minimum sizes for known sizes
    if family == "t-ind" and n is not None and (n < 2 or ratio * n < 2):
        raise PowerError("n (the first group) and ratio × n must each be at least 2")
    if family in ("t-paired", "t-one") and n is not None and n < 2:
        raise PowerError(f"n must be at least 2 for {family}")
    if family == "anova" and n is not None and n < groups + 1:
        raise PowerError(f"n must be at least groups + 1 ({groups + 1}) for anova")
    if family == "correlation" and n is not None and n < 4:
        raise PowerError("n must be at least 4 for correlation")
    if family == "proportions" and n is not None and n < 1:
        raise PowerError("n must be at least 1 for proportions")
    if family == "survival" and events is not None and events < 1:
        raise PowerError("events must be at least 1")
    if family == "survival" and n is not None and n * event_probability < 1:
        raise PowerError("n × event_probability must be at least 1 event")

    inputs: dict[str, Any] = {"alpha": alpha, "alternative": alt}
    if target is not None:
        inputs["power"] = target
    for name, value in (("n", n), ("events", events), ("groups", groups),
                        ("event_probability", event_probability)):
        if value is not None:
            inputs[name] = value
    if family in ("t-ind", "proportions"):
        inputs["ratio"] = ratio
    if family == "anova":
        inputs["groups"] = groups
    if family == "proportions":
        if p1 is not None:
            inputs["p1"] = p1
            inputs["p2"] = p2
        if effect_size is not None:
            inputs["effect_size"] = effect_size
    elif family == "correlation":
        inputs["r"] = r
    elif family == "survival":
        inputs["hazard_ratio"] = hazard_ratio
        inputs["allocation"] = allocation
    elif effect_size is not None:
        inputs["effect_size"] = effect_size

    opts = {"alpha": alpha, "alternative": alt, "ratio": ratio or 1.0, "groups": groups,
            "allocation": allocation}
    result = _solve_result(family, mode, opts, target, n, events, effect_size,
                           r, hazard_ratio, event_probability)
    import scipy

    return {
        "version": VERSION,
        "tool": TOOL,
        "family": family,
        "solve": mode,
        "inputs": inputs,
        "result": result,
        "method": f"{_METHOD[family]}, scipy {scipy.__version__}",
        "assumptions": list(_ASSUMPTIONS[family]),
    }


def _solve_result(family, mode, o, target, n, events, effect_size, r, hazard_ratio,
                  event_probability) -> dict[str, Any]:
    alpha = o["alpha"]
    if family == "survival":
        p = o["allocation"]
        if mode == "power":
            d = events if events is not None else n * event_probability
            return {"power": _survival_power(d, hazard_ratio, p, alpha)}
        d = _schoenfeld_events(hazard_ratio, p, alpha, target)
        if mode == "events":
            return {"events": d, "events_required": math.ceil(d)}
        n_solved = d / event_probability
        return {"events": d, "events_required": math.ceil(d),
                "n": n_solved, "n_required": math.ceil(n_solved)}

    if family == "correlation":
        effect = math.atanh(abs(r))
    else:
        effect = effect_size

    if mode == "power":
        return {"power": _power_at(family, n, {**o, "effect": effect})}

    if mode == "effect":
        if family == "correlation" or family == "survival":
            raise PowerError(f"solving for the effect is not supported for {family}")

        def by_effect(e):
            return _power_at(family, n, {**o, "effect": e})

        value = _solve_increasing(by_effect, target, 1e-9, 0.05, MAX_EFFECT, "effect size")
        return {"effect_size": value}

    # mode == "n"
    def by_size(size):
        return _power_at(family, size, {**o, "effect": effect})

    if family in ("t-ind", "proportions"):
        ratio = o["ratio"]
        lo = max(2.0, 2.0 / ratio) if family == "t-ind" else 1.0
        n1 = _solve_increasing(by_size, target, lo, lo * 2, MAX_SIZE, "per group")
        n1_req = math.ceil(n1)
        n2_req = math.ceil(ratio * n1)
        return {"n1": n1, "n1_required": n1_req, "n2_required": n2_req,
                "n_total_required": n1_req + n2_req}
    if family in ("t-paired", "t-one", "correlation"):
        lo = 4.0 if family == "correlation" else 2.0
        unit = "pairs" if family == "correlation" else "subjects"
        nn = _solve_increasing(by_size, target, lo, lo * 2, MAX_SIZE, unit)
        return {"n": nn, "n_required": math.ceil(nn)}
    if family == "anova":
        k = o["groups"]
        total = _solve_increasing(by_size, target, k + 1.0, (k + 1.0) * 2, MAX_SIZE, "in total")
        per_group = math.ceil(total / k)
        return {"n_total": total, "n_per_group_required": per_group,
                "n_total_required": k * per_group}
    raise PowerError(f"unknown family {family!r}")


def recompute(doc: dict[str, Any]) -> dict[str, Any]:
    """Re-solve a stored document from its inputs (used by design check, M-D1 t3)."""
    family = doc["family"]
    inp = dict(doc["inputs"])
    kwargs: dict[str, Any] = {
        "alpha": inp.get("alpha", 0.05),
        "alternative": inp.get("alternative", "two-sided"),
    }
    for key in ("power", "n", "events", "effect_size", "p1", "p2", "r", "hazard_ratio",
                "groups", "ratio", "allocation", "event_probability"):
        if key in inp:
            kwargs[key] = inp[key]
    if "p1" in inp and "p2" in inp:
        kwargs.pop("effect_size", None)  # derived from p1 and p2
    return solve(family, **kwargs)


def render_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def render_text(doc: dict[str, Any]) -> str:
    lines = [f"{doc['tool']}: {doc['family']} (solved: {doc['solve']})"]
    lines.append("inputs:")
    lines += [f"  {k} = {v}" for k, v in sorted(doc["inputs"].items())]
    lines.append("result:")
    lines += [f"  {k} = {v}" for k, v in sorted(doc["result"].items())]
    lines.append(f"method: {doc['method']}")
    lines.append("assumptions:")
    lines += [f"  - {a}" for a in doc["assumptions"]]
    return "\n".join(lines) + "\n"
