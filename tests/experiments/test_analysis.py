"""M-D2 t3: the campaign `analysis` block, the analysis document and stats.json (plan 5.4, 5.5)."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from scipy import stats as sps

from scieflow.experiments import analysis
from scieflow.experiments.campaign import Campaign, CampaignError

_SUPPORT = Path(__file__).with_name("stats_support.py")
_spec = importlib.util.spec_from_file_location("stats_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "design.yml"


def _holm(p):
    m = len(p)
    order = sorted(range(m), key=lambda i: p[i])
    out, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[i]))
        out[i] = running
    return out


def _test(doc, test_id):
    return next(t for t in doc["tests"] if t["id"] == test_id)


def _config(doc, config_id):
    return next(c for c in doc["configs"] if c["config_id"] == config_id)


def _assert_n_arithmetic(doc):
    """Plan 5.4: n = n_runs - n_failed - n_direct_excluded - n_missing - n_nonfinite, per cell."""
    for row in doc["configs"]:
        for cell in row["metrics"].values():
            assert cell["n"] == (row["n_runs"] - row["n_failed"] - row["n_direct_excluded"]
                                 - cell["n_missing"] - cell["n_nonfinite"])


# --- validation -------------------------------------------------------------------------------


def test_test_names_are_pinned_to_the_design_schema_enumeration():
    schema = yaml.safe_load(SCHEMA.read_text())
    enumerated = schema["enums"]["tests"]
    assert analysis.TESTS == frozenset(enumerated)


@pytest.mark.parametrize("block, key_path", [
    ({"nope": 1}, "analysis.nope"),
    ({"test": "bogus-test"}, "analysis.test"),
    ({"test": {"ssim": "bogus-test"}}, "analysis.test"),
    ({"test": {"not-a-metric": "welch-t-test"}}, "analysis.test.not-a-metric"),
    ({"correction": "bonferonni"}, "analysis.correction"),
    ({"alpha": 0}, "analysis.alpha"),
    ({"alpha": 1.0}, "analysis.alpha"),
    ({"alpha": True}, "analysis.alpha"),
    ({"ci": 0.4}, "analysis.ci"),
    ({"ci": 1.0}, "analysis.ci"),
    ({"bootstrap": {"resamples": 999}}, "analysis.bootstrap.resamples"),
    ({"bootstrap": {"resamples": 100001}}, "analysis.bootstrap.resamples"),
    ({"bootstrap": {"seed": -1}}, "analysis.bootstrap.seed"),
    ({"bootstrap": {"method": "bca"}}, "analysis.bootstrap.method"),
    ({"baseline": 7}, "analysis.baseline"),
])
def test_bad_analysis_keys_are_refused_naming_the_key_path(block, key_path):
    with pytest.raises(analysis.AnalysisError) as err:
        analysis.parse_analysis(block, ["ssim"])
    assert key_path in str(err.value)


def test_campaign_load_refuses_a_baseline_matching_zero_or_several_configurations(tmp_path):
    def write(baseline):
        path = tmp_path / "c.yaml"
        path.write_text(yaml.safe_dump({
            "name": "g", "pipeline": "demo", "stage": "scale", "input": "x.png",
            "metrics": ["ssim"], "parameters": {"gain": {"grid": [0.5, 1.0]}},
            "analysis": {"baseline": baseline, "test": "welch-t-test"},
        }))
        return path

    with pytest.raises(CampaignError, match="analysis.baseline"):
        Campaign.load(write({"gain": 2.0}))  # no such configuration
    with pytest.raises(CampaignError, match="analysis.baseline"):
        Campaign.load(write({"gain": 0.5, "extra": 1}))  # matches none exactly
    loaded = Campaign.load(write({"gain": 0.5}))
    assert loaded.analysis == {"baseline": {"gain": 0.5}, "test": "welch-t-test"}
    assert loaded.to_dict()["analysis"]["test"] == "welch-t-test"


def test_campaign_without_analysis_block_has_no_analysis_key_in_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({
        "name": "g", "pipeline": "demo", "stage": "scale", "input": "x.png",
        "metrics": ["ssim"], "parameters": {"gain": {"value": 1.0}},
    }))
    assert "analysis" not in Campaign.load(path).to_dict()


# --- analysis document ------------------------------------------------------------------------


def test_three_by_six_pairwise_welch_with_holm_across_two_tests(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)

    assert doc["version"] == 1 and doc["design"] == {
        "status": "none", "version": None, "in_force_hash": None, "study_type": None}
    assert [t["id"] for t in doc["tests"]] == [
        "ssim:000_sigma-0.5", "ssim:002_sigma-2.0"]  # the baseline is never compared with itself
    for config in support.CONFIGS:
        assert _config(doc, config)["metrics"]["ssim"]["n"] == 6

    raw = {}
    for config in ("000_sigma-0.5", "002_sigma-2.0"):
        t = sps.ttest_ind(support.SSIM[config], support.SSIM[support.BASELINE], equal_var=False)
        raw[config] = float(t.pvalue)
        assert _test(doc, f"ssim:{config}")["p"] == pytest.approx(float(t.pvalue), rel=1e-12)
    adjusted = _holm([raw["000_sigma-0.5"], raw["002_sigma-2.0"]])
    got = [_test(doc, f"ssim:{c}")["p_adjusted"] for c in ("000_sigma-0.5", "002_sigma-2.0")]
    assert got == pytest.approx(adjusted, rel=1e-12)
    for c, adj in zip(("000_sigma-0.5", "002_sigma-2.0"), adjusted):
        assert _test(doc, f"ssim:{c}")["reject"] is (adj <= 0.05)
    assert doc["families"] == [{"name": "campaign", "correction": "holm", "alpha": 0.05, "size": 2}]
    assert doc["settings"]["baseline"] == support.BASELINE
    assert doc["metrics"] == {"ssim": {"higher_is_better": True}}


def test_failed_replicate_is_counted_per_configuration_not_dropped(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")

    def fail_one(doc):
        run = support.run_of(doc, "000_sigma-0.5_r0")
        run.update(status="failed", metrics={})

    support.edit_results(cdir, fail_one)
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    cell = _config(doc, "000_sigma-0.5")["metrics"]["ssim"]
    assert cell["n"] == 5
    assert _config(doc, "000_sigma-0.5")["n_failed"] == 1
    assert _config(doc, "000_sigma-0.5")["n_runs"] == 6
    _assert_n_arithmetic(doc)


def test_direct_runs_are_excluded_unless_included(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    doc = analysis.analyse_campaign(cdir)
    for config in support.CONFIGS:
        entry = _config(doc, config)
        assert entry["n_runs"] == 6
        assert entry["metrics"]["ssim"]["n"] == 0
        assert entry["n_direct_excluded"] == 6
    _assert_n_arithmetic(doc)
    assert all(t["computed"] is False and t["reason"] == "n<2" for t in doc["tests"])
    assert doc["settings"]["include_direct"] is False

    included = analysis.analyse_campaign(cdir, include_direct=True)
    for config in support.CONFIGS:
        entry = _config(included, config)
        assert entry["n_runs"] == 6 and entry["n_direct_excluded"] == 0
    assert _config(included, "000_sigma-0.5")["metrics"]["ssim"]["n"] == 6
    _assert_n_arithmetic(included)


def test_campaign_without_reference_has_missing_values_not_a_crash(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", reference=False,
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    for config in support.CONFIGS:
        cell = _config(doc, config)["metrics"]["ssim"]
        assert cell["n"] == 0 and cell["n_missing"] == 6
        assert cell["mean"] is None and cell["ci"] is None and cell["ci_note"] == "n<2"
        assert [e["reason"] for e in cell["excluded"]] == ["missing"] * 6
    assert all(t["computed"] is False and t["reason"] == "n<2" for t in doc["tests"])
    rules = [w["rule"] for w in doc["warnings"] if w.get("metric") == "ssim"]
    assert rules.count("values-missing") == 1


def test_missing_and_non_finite_values_are_excluded_and_counted(tmp_path):
    values = {c: {"ssim": list(support.SSIM[c]), "psnr": [30.0] * 6} for c in support.CONFIGS}
    values["000_sigma-0.5"]["ssim"][2] = None  # one completed run without ssim
    values["000_sigma-0.5"]["psnr"][2] = float("inf")  # identical images: PSNR inf
    cdir = support.write_campaign(tmp_path, mode="direct", metrics=("ssim", "psnr"), values=values,
                                  analysis={"baseline": support.BASELINE,
                                            "test": {"ssim": "t-test-paired"}})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    config = _config(doc, "000_sigma-0.5")
    assert config["metrics"]["ssim"]["n"] == 5 and config["metrics"]["ssim"]["n_missing"] == 1
    assert config["metrics"]["psnr"]["n"] == 5 and config["metrics"]["psnr"]["n_nonfinite"] == 1
    assert config["metrics"]["ssim"]["excluded"][0]["reason"] == "missing"
    assert config["metrics"]["psnr"]["excluded"][0]["reason"] == "non-finite"
    paired = _test(doc, "ssim:000_sigma-0.5")
    assert paired["n"] == 5 and paired["n_unpaired"] == 1  # seed 2 is unpaired on the baseline side
    rules = {w["rule"] for w in doc["warnings"]}
    assert {"values-missing", "values-nonfinite"} <= rules
    _assert_n_arithmetic(doc)
    text = json.dumps(doc, allow_nan=False, sort_keys=True)  # strict dump succeeds
    assert "Infinity" not in text and "NaN" not in text


def test_correction_none_keeps_p_and_decides_reject_at_alpha(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test",
                                            "correction": "none"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    computed = [t for t in doc["tests"] if t["computed"]]
    assert len(computed) == 2
    for t in computed:
        assert t["p_adjusted"] == t["p"]
        assert t["reject"] is (t["p"] <= 0.05)
    assert doc["families"] == [{"name": "campaign", "correction": "none", "alpha": 0.05, "size": 2}]


def test_unsupported_test_is_not_computed_with_its_reason(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"test": "ancova"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    [entry] = doc["tests"]
    assert entry["computed"] is False and entry["reason"] == "test-unsupported"


def test_anova_and_kruskal_are_omnibus_over_all_configurations(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "anova-oneway"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    entry = _test(doc, "ssim:omnibus")
    assert entry["kind"] == "omnibus" and entry["computed"] is True
    assert entry["df"] == [2, 15]
    groups = [support.SSIM[c] for c in support.CONFIGS]
    want = sps.f_oneway(*groups)
    assert entry["statistic"] == pytest.approx(float(want.statistic), rel=1e-12)
    assert entry["p"] == pytest.approx(float(want.pvalue), rel=1e-12)

    cdir2 = support.write_campaign(tmp_path, name="kw", mode="direct",
                                   analysis={"test": "kruskal-wallis"})
    kw = _test(analysis.analyse_campaign(cdir2, include_direct=True), "ssim:omnibus")
    assert kw["df"] == 2 and kw["computed"] is True


def test_pearson_over_the_single_varying_numeric_parameter(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "pearson"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    entry = _test(doc, "ssim:correlation")
    xs = [0.5] * 6 + [1.0] * 6 + [2.0] * 6
    ys = [v for c in support.CONFIGS for v in support.SSIM[c]]
    want = sps.pearsonr(xs, ys)
    assert entry["computed"] is True and entry["n"] == 18
    assert entry["statistic"] == pytest.approx(float(want.statistic), rel=1e-12)


def test_two_varying_parameters_make_a_correlation_not_computed(tmp_path):
    params = {
        "000_sigma-0.5": {"sigma": 0.5, "gain": 1.0},
        "001_sigma-1.0": {"sigma": 1.0, "gain": 2.0},
        "002_sigma-2.0": {"sigma": 2.0, "gain": 1.0},
    }
    cdir = support.write_campaign(tmp_path, mode="direct", params=params,
                                  analysis={"test": "spearman"})
    entry = _test(analysis.analyse_campaign(cdir, include_direct=True), "ssim:correlation")
    assert entry["computed"] is False and entry["reason"] == "no-numeric-parameter"


def test_boolean_parameter_is_not_a_numeric_parameter(tmp_path):
    params = {c: {"flag": bool(i % 2)} for i, c in enumerate(support.CONFIGS)}
    cdir = support.write_campaign(tmp_path, mode="direct", params=params,
                                  analysis={"test": "pearson"})
    entry = _test(analysis.analyse_campaign(cdir, include_direct=True), "ssim:correlation")
    assert entry["reason"] == "no-numeric-parameter"


def test_paired_test_on_a_non_replicate_campaign_is_not_replicates(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", replicated=False,
                                  values={c: {"ssim": [support.SSIM[c][0]]} for c in support.CONFIGS},
                                  analysis={"baseline": support.BASELINE, "test": "t-test-paired"})
    entry = _test(analysis.analyse_campaign(cdir, include_direct=True), "ssim:000_sigma-0.5")
    assert entry["computed"] is False and entry["reason"] == "not-replicates"


def test_pairwise_test_without_a_baseline_is_refused_naming_no_baseline(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "welch-t-test"})
    with pytest.raises(analysis.AnalysisError, match="no-baseline"):
        analysis.analyse_campaign(cdir, include_direct=True)


def test_descriptive_only_block_needs_no_baseline_and_gives_no_tests(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={"test": "descriptive"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    assert doc["tests"] == [] and _config(doc, "000_sigma-0.5")["metrics"]["ssim"]["n"] == 6


def test_no_analysis_block_gives_descriptives_and_empty_tests(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    assert doc["tests"] == [] and doc["families"] == []
    assert doc["settings"]["baseline"] is None


def test_baseline_given_as_a_parameter_mapping_resolves_to_its_configuration(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct", analysis={
        "baseline": {"sigma": 1.0}, "test": "welch-t-test"})
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    assert doc["settings"]["baseline"] == support.BASELINE
    assert {t["baseline"] for t in doc["tests"]} == {support.BASELINE}


def test_missing_results_json_names_the_file(tmp_path):
    cdir = tmp_path / "empty"
    cdir.mkdir()
    (cdir / "campaign.yaml").write_text("name: empty\n")
    with pytest.raises(analysis.AnalysisError, match="results.json"):
        analysis.analyse_campaign(cdir)


@pytest.mark.parametrize("label, edit", support.MALFORMED, ids=[m[0] for m in support.MALFORMED])
def test_malformed_results_json_is_refused_naming_the_file(tmp_path, label, edit):
    cdir = support.write_campaign(tmp_path, mode="direct")
    support.corrupt_results(cdir, edit)
    with pytest.raises(analysis.AnalysisError, match="results.json"):
        analysis.analyse_campaign(cdir, include_direct=True)


def test_metric_too_large_for_a_float_is_non_finite_not_a_crash(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")
    support.edit_results(cdir, lambda doc: support.run_of(doc, "000_sigma-0.5_r0")["metrics"]
                         .update(ssim=10**400))
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    cell = _config(doc, "000_sigma-0.5")["metrics"]["ssim"]
    assert cell["n"] == 5 and cell["n_nonfinite"] == 1
    _assert_n_arithmetic(doc)
    json.dumps(doc, allow_nan=False)


def test_campaign_metrics_must_be_a_list_naming_the_key(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")
    campaign = yaml.safe_load((cdir / "campaign.yaml").read_text())
    campaign["metrics"] = "ssim"
    (cdir / "campaign.yaml").write_text(yaml.safe_dump(campaign))
    with pytest.raises(analysis.AnalysisError, match="campaign.yaml: 'metrics' must be a list"):
        analysis.analyse_campaign(cdir, include_direct=True)


def test_stats_json_and_results_csv_are_byte_identical_on_rerun(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    first = analysis.write_stats(cdir, analysis.analyse_campaign(cdir, include_direct=True))
    stats_a = first.read_bytes()
    csv_a = (cdir / "results.csv").read_bytes()
    second = analysis.write_stats(cdir, analysis.analyse_campaign(cdir, include_direct=True))
    assert second.read_bytes() == stats_a
    assert (cdir / "results.csv").read_bytes() == csv_a


def test_stats_goes_stale_when_results_change(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct",
                                  analysis={"baseline": support.BASELINE, "test": "welch-t-test"})
    assert analysis.stats_fresh(cdir) is False  # nothing written yet
    analysis.write_stats(cdir, analysis.analyse_campaign(cdir, include_direct=True))
    assert analysis.stats_fresh(cdir) is True
    support.edit_results(cdir, lambda doc: support.run_of(doc, "000_sigma-0.5_r0")["metrics"]
                         .update(ssim=0.5))
    assert analysis.stats_fresh(cdir) is False


def test_stats_json_refuses_a_symlinked_target(tmp_path):
    cdir = support.write_campaign(tmp_path, mode="direct")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("{}")
    (cdir / "stats.json").symlink_to(elsewhere)
    with pytest.raises(analysis.AnalysisError, match="symlink"):
        analysis.write_stats(cdir, analysis.analyse_campaign(cdir, include_direct=True))
    assert elsewhere.read_text() == "{}"
