"""M-D1 t1: scieflow experiment power, module API.

Reference values: tests/fixtures/power/reference.json (copied byte for byte from
the M-D1 planning artifact; sha256 9eace567...). Synthetic inputs only.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("scipy")

from scieflow.experiments import power as P  # noqa: E402

REF_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "power" / "reference.json"
REF = json.loads(REF_PATH.read_text(encoding="utf-8"))
CASES = REF["cases"]
TOL = REF["tolerance"]

INPUT_KEYS = (
    "alpha", "power", "n", "events", "effect_size", "p1", "p2", "r",
    "hazard_ratio", "groups", "ratio", "allocation", "event_probability", "alternative",
)

RESULT_KEY = {
    ("t-ind", "n"): "n1",
    ("t-paired", "n"): "n",
    ("t-one", "n"): "n",
    ("anova", "n"): "n_total",
    ("proportions", "n"): "n1",
    ("correlation", "n"): "n",
    ("survival", "n"): "n",
    ("survival", "events"): "events",
}


def _kwargs(case):
    return {k: case[k] for k in INPUT_KEYS if k in case}


def _result_key(case):
    if case["solve"] == "power":
        return "power"
    if case["solve"] == "effect":
        return "effect_size"
    return RESULT_KEY[(case["family"], case["solve"])]


def _upper_tail(d, nobs, df, alpha):
    """Upper-tail power term straight from scipy (independent of the module)."""
    from scipy import stats

    nc = d * math.sqrt(nobs)
    return float(stats.nct.sf(stats.t.isf(alpha / 2, df), df, nc))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_case_reproduces(case):
    doc = P.solve(case["family"], **_kwargs(case))
    actual = doc["result"][_result_key(case)]
    expected = case["expected"]
    if case["solve"] == "power":
        assert abs(actual - expected) <= TOL["power"]["absolute"], (actual, expected)
    else:
        assert math.isclose(actual, expected, rel_tol=TOL["n"]["relative"]), (actual, expected)


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["solve"] in ("n", "events")],
    ids=[c["id"] for c in CASES if c["solve"] in ("n", "events")],
)
def test_required_counts_are_ceilings(case):
    result = P.solve(case["family"], **_kwargs(case))["result"]
    if "n1_required" in result:
        assert result["n1_required"] == math.ceil(result["n1"])
        assert result["n2_required"] == math.ceil(_kwargs(case).get("ratio", 1.0) * result["n1"])
        assert result["n_total_required"] == result["n1_required"] + result["n2_required"]
    if "n_per_group_required" in result:
        assert result["n_per_group_required"] == math.ceil(result["n_total"] / case["groups"])
        assert result["n_total_required"] == case["groups"] * result["n_per_group_required"]
    if "n_required" in result:
        assert result["n_required"] == math.ceil(result["n"])
    if "events_required" in result:
        assert result["events_required"] == math.ceil(result["events"])


def test_cross_check_integers_hold():
    # Cohen (1988) Table 2.4.1, two-sided alpha 0.05, power 0.80, per group
    assert round(P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.2)["result"]["n1"]) == 393
    assert P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5)["result"]["n1_required"] == 64
    assert round(P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.8)["result"]["n1"]) == 26
    # ANOVA f = 0.25, k = 3: G*Power reports 3 x 53 = 159
    assert P.solve("anova", alpha=0.05, power=0.8, effect_size=0.25, groups=3)["result"][
        "n_total_required"
    ] == 159
    # correlation r = 0.30 (Cohen: 85 pairs)
    assert round(P.solve("correlation", alpha=0.05, power=0.8, r=0.3)["result"]["n"]) == 85
    # Schoenfeld: HR 0.5 needs about 66 events
    assert P.solve("survival", alpha=0.05, power=0.8, hazard_ratio=0.5)["result"][
        "events_required"
    ] == 66


def test_exactly_one_unknown_none_is_usage_error():
    with pytest.raises(P.PowerUsageError):
        P.solve("t-ind", alpha=0.05, power=0.8, n=64, effect_size=0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(alpha=0.05, effect_size=0.5),  # n and power unknown
        dict(alpha=0.05, power=0.8),  # n and effect unknown
    ],
)
def test_exactly_one_unknown_two_is_usage_error(kwargs):
    with pytest.raises(P.PowerUsageError):
        P.solve("t-ind", **kwargs)


@pytest.mark.parametrize(
    "family, kwargs",
    [
        ("proportions", dict(alpha=0.05, power=0.8, p1=0.5, p2=0.5)),
        ("survival", dict(alpha=0.05, power=0.8, hazard_ratio=1.0)),
        ("t-ind", dict(alpha=0.05, power=0.8, effect_size=0.5, ratio=0)),
        ("t-ind", dict(alpha=0.05, n=1, effect_size=0.5)),
        ("t-ind", dict(alpha=0.6, power=0.8, effect_size=0.5)),
        ("t-ind", dict(alpha=0.05, power=0.05, effect_size=0.5)),
        ("t-ind", dict(alpha=0.05, power=0.8, effect_size=-0.5)),
        ("t-ind", dict(alpha=0.05, n=64, effect_size=0.5, alternative="smaller")),
        ("anova", dict(alpha=0.05, power=0.8, effect_size=0.25, groups=1)),
        ("anova", dict(alpha=0.05, power=0.8, effect_size=0.25, groups=3, alternative="larger")),
        ("correlation", dict(alpha=0.05, power=0.8, r=0)),
        ("correlation", dict(alpha=0.05, power=0.8, n=50)),  # effect unknown: not supported
        ("survival", dict(alpha=0.05, power=0.8, hazard_ratio=0.7, allocation=0)),
    ],
)
def test_domain_errors_are_refused(family, kwargs):
    with pytest.raises(P.PowerError):
        P.solve(family, **kwargs)


def test_nan_lower_tail_below_noncentrality_six_is_refused(monkeypatch):
    monkeypatch.setattr(P, "_nct_cdf", lambda *a, **k: float("nan"))
    nobs = (5.9 / 0.5) ** 2  # noncentrality 5.9
    with pytest.raises(P.PowerError):
        P.ttest_power(0.5, nobs, 100, 0.05, "two-sided")


def test_nan_lower_tail_at_noncentrality_six_counts_as_zero(monkeypatch):
    monkeypatch.setattr(P, "_nct_cdf", lambda *a, **k: float("nan"))
    nobs = (6.0 / 0.5) ** 2  # noncentrality 6.0
    expected = _upper_tail(0.5, nobs, 143, 0.05)
    assert P.ttest_power(0.5, nobs, 143, 0.05, "two-sided") == pytest.approx(expected, abs=1e-12)


def test_real_scipy_paired_n300_power_is_about_one():
    # scipy 1.18.1 returns NaN for the lower tail here (noncentrality 8.66); the
    # term is bounded by 1e-9 and must not turn into an error.
    power = P.solve("t-paired", alpha=0.05, n=300, effect_size=0.5)["result"]["power"]
    assert power == pytest.approx(1.0, abs=1e-6)


def test_over_powered_study_solving_for_power_reports_one():
    power = P.solve("t-ind", alpha=0.05, n=5000, effect_size=0.5)["result"]["power"]
    assert power == pytest.approx(1.0, abs=1e-6)


def test_nan_power_is_never_a_result(monkeypatch):
    monkeypatch.setattr(P, "_nct_sf", lambda *a, **k: float("nan"))
    with pytest.raises(P.PowerError):
        P.solve("t-ind", alpha=0.05, n=64, effect_size=0.5)


def test_effect_needing_more_than_ten_million_is_refused():
    with pytest.raises(P.PowerError, match="needs more than"):
        P.solve("t-ind", alpha=0.05, power=0.8, effect_size=1e-4)


def test_small_effect_solves_from_below():
    doc = P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.05)
    n1 = doc["result"]["n1_required"]
    assert 6000 <= n1 <= 6600
    assert P.solve("t-ind", alpha=0.05, n=n1, effect_size=0.05)["result"]["power"] >= 0.8


def test_proportions_records_derived_effect_size():
    doc = P.solve("proportions", alpha=0.05, power=0.8, p1=0.65, p2=0.45)
    assert doc["inputs"]["effect_size"] == pytest.approx(0.40486007517695755, rel=1e-9)


def test_document_states_method_and_assumptions():
    doc = P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5)
    assert doc["version"] == 1
    assert doc["tool"] == "scieflow experiment power"
    assert doc["family"] == "t-ind" and doc["solve"] == "n"
    assert doc["inputs"]["effect_size"] == 0.5
    assert "scipy" in doc["method"]
    assert any("equal variances" in a for a in doc["assumptions"])


def test_render_json_is_deterministic():
    a = P.render_json(P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5))
    b = P.render_json(P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5))
    assert a == b
    assert json.loads(a)["result"]["n1_required"] == 64


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_recompute_matches_every_reference_case(case):
    doc = P.solve(case["family"], **_kwargs(case))
    assert P.recompute(doc)["result"] == doc["result"]


def test_recompute_detects_a_changed_required_count():
    doc = P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5)
    stored = json.loads(json.dumps(doc))
    stored["result"]["n1_required"] += 1
    fresh = P.recompute(stored)
    assert fresh["result"]["n1_required"] == doc["result"]["n1_required"]
    assert fresh["result"] != stored["result"]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(alpha=0.05, power=0.8, p1=0.5, effect_size=0.4),
        dict(alpha=0.05, power=0.8, p2=0.6, effect_size=0.4),
        dict(alpha=0.05, power=0.8, p1=0.5),
        dict(alpha=0.05, power=0.8, p2=0.6),
    ],
)
def test_proportions_one_of_p1_p2_is_refused_even_with_effect_size(kwargs):
    # A lone p1 or p2 must never be stored (it would make recompute fail).
    with pytest.raises(P.PowerError, match="both --p1 and --p2"):
        P.solve("proportions", **kwargs)


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {},
        {"family": "nope", "inputs": {}},
        {"family": "t-ind"},
        {"family": "t-ind", "inputs": []},
        {"family": "t-ind", "inputs": {"alpha": 0.05, "power": 0.8, "effect_size": 0.5},
         "result": []},
        {"family": "t-ind", "inputs": {"alpha": 0.05, "effect_size": 0.5}},  # two unknown
        {"family": "t-ind", "inputs": {"alpha": [0.05], "power": 0.8, "effect_size": 0.5}},
    ],
)
def test_recompute_refuses_a_malformed_document_with_power_error(doc):
    with pytest.raises(P.PowerError):
        P.recompute(doc)


def test_recompute_refuses_a_stored_solve_the_inputs_do_not_imply():
    doc = P.solve("t-ind", alpha=0.05, power=0.8, effect_size=0.5)
    stored = json.loads(json.dumps(doc))
    stored["solve"] = "power"
    with pytest.raises(P.PowerError, match="solve"):
        P.recompute(stored)


def test_power_module_imports_scipy_lazily():
    code = "import sys, scieflow.experiments.power; print('scipy' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
