"""M-D2 t6 (plan 5.8, Part A 6.t6 item 1): `scieflow experiment compare-campaigns`.

Synthetic campaign folders only (no patient data). Every campaign is analysed with
`scieflow experiment stats --include-direct` first, so each DIR carries a fresh stats.json.
"""

import importlib.util
import json
import os
from pathlib import Path

from click.testing import CliRunner

from scieflow.experiments.cli import experiment
from scieflow.experiments.crosscompare import CrossCompareError, compare_campaigns

_SUPPORT = Path(__file__).with_name("stats_support.py")
_spec = importlib.util.spec_from_file_location("stats_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

# Means: ssim 000 0.7595 (highest), 001 0.7195, 002 0.7210; mse 000 0.115, 001 0.055 (lowest), 002 0.085.
MSE = {
    "000_sigma-0.5": [0.11, 0.12, 0.10, 0.13, 0.11, 0.12],
    "001_sigma-1.0": [0.05, 0.06, 0.04, 0.07, 0.05, 0.06],
    "002_sigma-2.0": [0.08, 0.09, 0.07, 0.10, 0.08, 0.09],
}
BOTH = {c: {"ssim": support.SSIM[c], "mse": MSE[c]} for c in support.CONFIGS}


def _campaign(root, name, values=None, metrics=("ssim",)):
    cdir = support.write_campaign(root, name=name, mode="direct", metrics=metrics,
                                  values=values)
    result = CliRunner().invoke(experiment, ["stats", "--include-direct", str(cdir)])
    assert result.exit_code == 0, result.output
    return cdir


def _cli(*args):
    return CliRunner().invoke(experiment, ["compare-campaigns", *map(str, args)])


def _entry(doc, cdir):
    return next(c for c in doc["campaigns"] if c["dir"] == str(cdir))


def test_best_configuration_per_campaign_in_the_metric_direction(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    doc = compare_campaigns([a, b])
    assert doc["metrics"] == ["mse", "ssim"]
    for cdir in (a, b):
        ssim = _entry(doc, cdir)["metrics"]["ssim"]
        assert ssim["higher_is_better"] is True
        assert ssim["best"]["config_id"] == "000_sigma-0.5"
        assert ssim["best"]["n"] == 6 and ssim["reason"] is None
        assert ssim["best"]["params"] == {"sigma": 0.5}
        assert ssim["best"]["ci"]["low"] <= ssim["best"]["mean"] <= ssim["best"]["ci"]["high"]
        mse = _entry(doc, cdir)["metrics"]["mse"]
        assert mse["higher_is_better"] is False
        assert mse["best"]["config_id"] == "001_sigma-1.0"


def test_stats_sha256_and_design_status_are_recorded(tmp_path):
    import hashlib

    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    entry = _entry(compare_campaigns([a, b]), a)
    stats_bytes = (a / "stats.json").read_bytes()
    assert entry["stats_sha256"] == hashlib.sha256(stats_bytes).hexdigest()
    assert entry["design_status"] == "none"
    assert entry["campaign"] == "camp-a"


def test_metric_flag_limits_the_comparison_and_mse_picks_the_lowest_mean(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    result = _cli(a, b, "--metric", "mse", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["metrics"] == ["mse"]
    assert _entry(doc, a)["metrics"]["mse"]["best"]["config_id"] == "001_sigma-1.0"
    assert list(_entry(doc, a)["metrics"]) == ["mse"]


def test_a_campaign_without_values_is_reported_not_refused(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    empty = {c: {"ssim": [None] * 6, "mse": MSE[c]} for c in support.CONFIGS}
    b = _campaign(tmp_path, "camp-b", values=empty, metrics=("ssim", "mse"))
    result = _cli(a, b, "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    blank = _entry(doc, b)["metrics"]["ssim"]
    assert blank["best"] is None and blank["reason"] == "no-values"
    assert _entry(doc, b)["metrics"]["mse"]["best"]["config_id"] == "001_sigma-1.0"
    assert _entry(doc, a)["metrics"]["ssim"]["best"]["config_id"] == "000_sigma-0.5"


def test_ties_are_broken_by_configuration_id(tmp_path):
    flat = {c: {"ssim": [0.7] * 6} for c in support.CONFIGS}
    a = _campaign(tmp_path, "camp-a", values=flat)
    b = _campaign(tmp_path, "camp-b", values=flat)
    doc = compare_campaigns([a, b])
    assert _entry(doc, a)["metrics"]["ssim"]["best"]["config_id"] == "000_sigma-0.5"


def test_the_document_states_that_no_test_crosses_campaigns(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    doc = compare_campaigns([a, b])
    assert doc["version"] == 1
    assert doc["note"] == "no test across campaigns: inputs and stages differ"


def test_json_is_deterministic_and_nothing_is_written_without_out(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    first = _cli(a, b, "--json")
    second = _cli(a, b, "--json")
    assert first.exit_code == 0 and first.output == second.output
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_human_output_names_each_campaign_and_metric(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    result = _cli(a, b)
    assert result.exit_code == 0, result.output
    assert "camp-a" in result.output and "camp-b" in result.output
    assert "000_sigma-0.5" in result.output and "001_sigma-1.0" in result.output


def test_out_writes_atomically_and_refuses_an_existing_file_without_force(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    out = tmp_path / "compare.json"
    result = _cli(a, b, "--out", out)
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["metrics"] == ["mse", "ssim"]
    assert not (tmp_path / "compare.json.tmp").exists()
    again = _cli(a, b, "--out", out)
    assert again.exit_code == 1 and "--force" in again.output
    forced = _cli(a, b, "--out", out, "--force")
    assert forced.exit_code == 0, forced.output


def test_a_symlinked_out_is_refused_even_with_force(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    target = tmp_path / "target.json"
    target.write_text("keep")
    link = tmp_path / "link.json"
    os.symlink(target, link)
    result = _cli(a, b, "--out", link, "--force")
    assert result.exit_code == 1
    assert target.read_text() == "keep"


def test_fewer_than_two_campaigns_is_refused(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    result = _cli(a)
    assert result.exit_code == 1
    assert "two" in result.output
    try:
        compare_campaigns([a])
    except CrossCompareError:
        pass
    else:
        raise AssertionError("one directory must be refused")


def test_a_campaign_without_stats_json_is_refused_naming_it(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    bare = support.write_campaign(tmp_path, name="bare", mode="direct",
                                  metrics=("ssim", "mse"), values=BOTH)
    result = _cli(a, bare)
    assert result.exit_code == 1
    assert "stats.json" in result.output and "bare" in result.output


def test_a_stale_stats_json_is_refused(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    results = json.loads((b / "results.json").read_text())
    results["runs"][0]["metrics"]["ssim"] = 0.1
    (b / "results.json").write_text(json.dumps(results, indent=2))
    result = _cli(a, b)
    assert result.exit_code == 1
    assert "stale" in result.output and "stats" in result.output


def test_a_symlinked_stats_json_is_refused(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    b = _campaign(tmp_path, "camp-b", values=BOTH, metrics=("ssim", "mse"))
    real = b / "stats-real.json"
    (b / "stats.json").rename(real)
    os.symlink(real, b / "stats.json")
    result = _cli(a, b)
    assert result.exit_code == 1
    assert "symlink" in result.output


def test_no_shared_metric_is_refused(tmp_path):
    a = _campaign(tmp_path, "camp-a", values=BOTH, metrics=("ssim", "mse"))
    only_psnr = {c: {"psnr": [30.0 + k / 10 for k in range(6)]} for c in support.CONFIGS}
    b = _campaign(tmp_path, "camp-b", values=only_psnr, metrics=("psnr",))
    result = _cli(a, b)
    assert result.exit_code == 1
    assert "no shared metric" in result.output
    unshared = _cli(a, b, "--metric", "ssim")
    assert unshared.exit_code == 1
    assert "ssim" in unshared.output and "shared" in unshared.output
