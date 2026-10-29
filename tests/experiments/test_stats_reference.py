"""M-D2 t3 case 1: every reference value reproduces through the stats API (plan 5.4).

The reference is the byte copy of reviews/m-d2-stats-reference.json (synthetic data only).
"""

import hashlib
import json
import math
from pathlib import Path

import pytest

from scieflow.experiments import stats

REF = Path(__file__).resolve().parents[1] / "fixtures" / "stats" / "reference.json"
REF_SHA = "1724e82e9a29b798b3499c5d149e8ca3ceed3934113ea405fa5b95447cbc0a8b"
DATA = json.loads(REF.read_text())
TOL = DATA["tolerance"]


def _close(got, want):
    assert got is not None, "value is None where the reference has a number"
    assert math.isclose(got, want, rel_tol=TOL["rel"], abs_tol=TOL["abs"]), (got, want)


def _case(case_id):
    return next(c for c in DATA["cases"] if c["id"] == case_id)


def test_fixture_is_the_byte_copy_of_the_approved_reference():
    assert hashlib.sha256(REF.read_bytes()).hexdigest() == REF_SHA


@pytest.mark.parametrize("case_id", ["describe-A", "describe-B", "describe-C"])
def test_descriptives(case_id):
    case = _case(case_id)
    got = stats.describe(case["inputs"]["values"])
    assert got["n"] == case["expected"]["n"]
    for key in ("mean", "sd", "median", "min", "max"):
        _close(got[key], case["expected"][key])


def test_welch_student_paired_and_effects_for_b_vs_a():
    b = _case("welch-B-vs-A")["inputs"]
    welch = stats.welch_t(b["config"], b["baseline"])
    _close(welch["statistic"], _case("welch-B-vs-A")["expected"]["statistic"])
    _close(welch["df"], _case("welch-B-vs-A")["expected"]["df"])
    _close(welch["p"], _case("welch-B-vs-A")["expected"]["p"])

    student = _case("student-B-vs-A")
    got = stats.student_t(student["inputs"]["config"], student["inputs"]["baseline"])
    _close(got["statistic"], student["expected"]["statistic"])
    _close(got["df"], student["expected"]["df"])
    _close(got["p"], student["expected"]["p"])

    paired = _case("paired-t-B-vs-A")
    got = stats.paired_t(paired["inputs"]["config"], paired["inputs"]["baseline"])
    _close(got["statistic"], paired["expected"]["statistic"])
    _close(got["df"], paired["expected"]["df"])
    _close(got["p"], paired["expected"]["p"])


@pytest.mark.parametrize("case_id, fn", [
    ("mwu-B-vs-A", stats.mann_whitney),
    ("mwu-C-vs-A", stats.mann_whitney),
    ("wilcoxon-C-vs-A", stats.wilcoxon),
])
def test_rank_tests(case_id, fn):
    case = _case(case_id)
    got = fn(case["inputs"]["config"], case["inputs"]["baseline"])
    _close(got["statistic"], case["expected"]["statistic"])
    _close(got["p"], case["expected"]["p"])


def test_anova_and_kruskal_over_three_configurations():
    anova = _case("anova-ABC")
    got = stats.anova_oneway(anova["inputs"]["groups"])
    _close(got["statistic"], anova["expected"]["statistic"])
    assert got["df"] == anova["expected"]["df"]
    _close(got["p"], anova["expected"]["p"])

    kruskal = _case("kruskal-ABC")
    got = stats.kruskal_wallis(kruskal["inputs"]["groups"])
    _close(got["statistic"], kruskal["expected"]["statistic"])
    assert got["df"] == kruskal["expected"]["df"]
    _close(got["p"], kruskal["expected"]["p"])


@pytest.mark.parametrize("case_id, fn", [
    ("pearson-XY", stats.pearson),
    ("spearman-XY", stats.spearman),
])
def test_correlations(case_id, fn):
    case = _case(case_id)
    got = fn(case["inputs"]["x"], case["inputs"]["y"])
    _close(got["statistic"], case["expected"]["statistic"])
    _close(got["p"], case["expected"]["p"])


@pytest.mark.parametrize("case_id", ["effect-B-vs-A", "effect-C-vs-A"])
def test_effect_sizes(case_id):
    case = _case(case_id)
    b, a = case["inputs"]["config"], case["inputs"]["baseline"]
    got = stats.effect_sizes(b, a, pairs=(b, a))
    for key, want in case["expected"].items():
        _close(got[key], want)


def test_corrections_on_one_p_vector_and_bh_matches_scipy():
    case = _case("correction")
    p = case["inputs"]["p"]
    for method in ("none", "bonferroni", "holm", "hochberg", "bh"):
        got = stats.adjust(p, method)
        for g, want in zip(got, case["expected"][method]):
            _close(g, want)

    from scipy import stats as sps

    assert stats.adjust(p, "bh") == pytest.approx(
        list(sps.false_discovery_control(p, method="bh")), rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("case_id", ["bootstrap-A", "bootstrap-B"])
def test_bootstrap_intervals_are_the_fixed_scipy_call(case_id):
    case = _case(case_id)
    ci, note = stats.bootstrap_ci(
        case["inputs"]["values"],
        level=case["inputs"]["confidence_level"],
        resamples=case["inputs"]["n_resamples"],
        seed=case["inputs"]["seed"],
    )
    assert note is None
    _close(ci["low"], case["expected"]["low"])
    _close(ci["high"], case["expected"]["high"])
    assert ci["method"] == "BCa" and ci["resamples"] == 9999 and ci["seed"] == 0


def test_degenerate_bootstrap_is_null_not_nan():
    ci, note = stats.bootstrap_ci(DATA["degenerate"]["values"], level=0.95,
                                  resamples=9999, seed=0)
    assert ci is None and note == "degenerate"


def test_integer_cross_checks_hold():
    # Plan 5.4: U(B vs A) = 36, U(C vs A) = 19, W(C vs A) = 9, delta(B vs A) = 1.0.
    a = _case("mwu-B-vs-A")["inputs"]["baseline"]
    b = _case("mwu-B-vs-A")["inputs"]["config"]
    assert stats.mann_whitney(b, a)["statistic"] == 36.0
    c = _case("mwu-C-vs-A")["inputs"]["config"]
    assert stats.mann_whitney(c, a)["statistic"] == 19.0
    assert stats.wilcoxon(c, a)["statistic"] == 9.0
    assert stats.effect_sizes(b, a, pairs=(b, a))["cliffs_delta"] == 1.0
