"""M-D2 t3 case 2: guards decide before scipy is called; NaN/inf is never written (plan 5.4)."""

import json
import math

import pytest
from scipy import stats as sps

from scieflow.experiments import stats


def _not_computed(result, reason):
    assert result["computed"] is False
    assert result["reason"] == reason
    assert result["statistic"] is None and result["p"] is None


def test_two_constant_samples_are_not_computed():
    _not_computed(stats.welch_t([0.5] * 4, [0.5] * 4), "constant")
    _not_computed(stats.mann_whitney([0.5] * 4, [0.5] * 4), "constant")
    _not_computed(stats.student_t([0.5] * 4, [0.5] * 4), "constant")


def test_one_value_per_group_is_not_computed():
    _not_computed(stats.welch_t([0.7], [0.1, 0.2, 0.3]), "n<2")
    _not_computed(stats.student_t([0.7, 0.8], [0.1]), "n<2")


def test_identical_paired_samples_are_all_differences_zero():
    same = [0.7, 0.8, 0.9]
    _not_computed(stats.paired_t(same, same), "all-differences-zero")
    _not_computed(stats.wilcoxon(same, same), "all-differences-zero")


def test_paired_tests_without_shared_seeds_have_no_pairs():
    _not_computed(stats.paired_t([], []), "no-pairs")
    _not_computed(stats.wilcoxon([], []), "no-pairs")


def test_paired_t_with_constant_nonzero_differences_is_not_computed():
    _not_computed(stats.paired_t([1.0, 2.0, 3.0], [0.5, 1.5, 2.5]), "constant")


def test_correlation_needs_three_points_and_nonconstant_inputs():
    _not_computed(stats.pearson([1.0, 2.0], [1.0, 2.0]), "n<3")
    _not_computed(stats.spearman([1.0, 2.0], [1.0, 2.0]), "n<3")
    _not_computed(stats.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]), "constant")


def test_omnibus_guards():
    _not_computed(stats.anova_oneway([[0.5, 0.5], [0.5, 0.5]]), "constant")
    _not_computed(stats.anova_oneway([[0.5, 0.6], [0.7]]), "n<2")
    _not_computed(stats.kruskal_wallis([[0.5, 0.5], [0.5]]), "constant")


def test_scipy_nan_p_value_is_numerical_not_a_number(monkeypatch):
    class Fake:
        statistic = float("nan")
        pvalue = float("nan")
        df = 4.0

    monkeypatch.setattr(sps, "ttest_ind", lambda *a, **k: Fake())
    result = stats.welch_t([0.1, 0.2, 0.4], [0.3, 0.5, 0.9])
    _not_computed(result, "numerical")


def test_scipy_infinite_statistic_is_numerical(monkeypatch):
    class Fake:
        statistic = float("inf")
        pvalue = 0.5
        df = 4.0

    monkeypatch.setattr(sps, "ttest_rel", lambda *a, **k: Fake())
    _not_computed(stats.paired_t([0.1, 0.2, 0.4], [0.3, 0.5, 0.9]), "numerical")


def test_warnings_from_scipy_are_listed_not_hidden(monkeypatch):
    import warnings

    class Fake:
        statistic = 1.0
        pvalue = 0.5
        df = 3.0

    def noisy(*a, **k):
        warnings.warn("synthetic scipy warning", RuntimeWarning)
        return Fake()

    monkeypatch.setattr(sps, "ttest_ind", noisy)
    result = stats.welch_t([0.1, 0.2, 0.4], [0.3, 0.5, 0.9])
    assert result["computed"] is True
    assert any("synthetic scipy warning" in w["message"] for w in result["warnings"])


def test_bootstrap_of_constant_data_is_degenerate_and_one_value_is_n_lt_2():
    ci, note = stats.bootstrap_ci([0.5] * 5, level=0.95, resamples=1000, seed=0)
    assert ci is None and note == "degenerate"
    ci, note = stats.bootstrap_ci([0.5], level=0.95, resamples=1000, seed=0)
    assert ci is None and note == "n<2"


def test_describe_of_no_values_is_all_null_and_one_value_has_no_sd():
    empty = stats.describe([])
    assert empty["n"] == 0
    assert all(empty[k] is None for k in ("mean", "sd", "median", "min", "max"))
    one = stats.describe([0.25])
    assert one["n"] == 1 and one["mean"] == 0.25 and one["sd"] is None


def test_no_nan_or_inf_survives_a_strict_dump():
    results = [
        stats.welch_t([0.5] * 3, [0.5] * 3),
        stats.paired_t([0.7, 0.8], [0.7, 0.8]),
        stats.describe([0.1, 0.2, 0.3]),
    ]
    text = json.dumps(results, allow_nan=False, sort_keys=True)
    parsed = json.loads(text)
    assert all(
        not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
        for r in parsed for v in r.values() if isinstance(v, (int, float))
    )


@pytest.mark.parametrize("version", ["1.14.1", "1.13.0", "0.19.2"])
def test_scipy_older_than_1_15_is_refused(monkeypatch, version):
    import scipy

    monkeypatch.setattr(scipy, "__version__", version)
    with pytest.raises(stats.StatsUnavailable) as err:
        stats.require_scipy()
    assert str(err.value) == (
        f"needs the experiments extra (scipy >= 1.15); found {version}")


@pytest.mark.parametrize("version", ["1.15.0", "1.18.1"])
def test_scipy_1_15_or_newer_is_accepted(monkeypatch, version):
    import scipy

    monkeypatch.setattr(scipy, "__version__", version)
    stats.require_scipy()  # no exception
