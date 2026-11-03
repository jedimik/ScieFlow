"""M-H t8 (plan 5.11, 5.12, Part B t8): Wilson intervals, binary confusion metrics, the kappa tables and
the offline developer harness scripts/evals/run_axes.py. Every expected value is hand-typed from the plan
(synthetic gold, no patient data); no network, no agent."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from scieflow.research import evals
from scieflow.research.evals import Confusion, format_interval, wilson

REPO = Path(__file__).resolve().parents[2]
SR_FIX = REPO / "tests" / "fixtures" / "sr"
HARNESS = REPO / "scripts" / "evals" / "run_axes.py"

# plan 5.11: k, n, low, high (6 decimals, abs tol 1e-6); point is k/n.
PINNED_WILSON = [
    (8, 10, 0.490162, 0.943318),
    (9, 10, 0.595850, 0.982124),
    (10, 10, 0.722467, 1.0),
    (0, 50, 0.0, 0.071348),
    (0, 100, 0.0, 0.036993),
    (27, 30, 0.743789, 0.965400),
    (24, 30, 0.626943, 0.904949),
    (36, 40, 0.769482, 0.960420),
    (34, 40, 0.709277, 0.929388),
    (110, 110, 0.966256, 1.0),
]


@pytest.mark.parametrize("k,n,low,high", PINNED_WILSON)
def test_wilson_matches_the_pinned_values(k, n, low, high):
    interval = wilson(k, n)
    assert interval.k == k and interval.n == n
    assert interval.point == pytest.approx(k / n, abs=1e-12)
    assert interval.low == pytest.approx(low, abs=1e-6)
    assert interval.high == pytest.approx(high, abs=1e-6)


def test_z95_is_the_two_sided_95_percent_quantile():
    assert evals.Z95 == pytest.approx(1.9599639845400536, abs=1e-12)


@pytest.mark.parametrize("k,n", [(0, 0), (1, 0)])
def test_wilson_refuses_n_zero(k, n):
    with pytest.raises(ValueError):
        wilson(k, n)


@pytest.mark.parametrize("k,n", [(-1, 5), (6, 5)])
def test_wilson_refuses_k_outside_zero_to_n(k, n):
    with pytest.raises(ValueError):
        wilson(k, n)


@pytest.mark.parametrize("n", [1, 2, 3, 10, 40, 110])
def test_bounds_stay_in_the_unit_interval(n):
    for k in range(n + 1):
        interval = wilson(k, n)
        assert 0.0 <= interval.low <= interval.point <= interval.high <= 1.0


def test_format_interval_prints_the_plan_string():
    assert format_interval(wilson(9, 10)) == "9/10 = 0.900000 [0.595850, 0.982124]"


def test_confusion_metrics_are_wilson_intervals_over_the_right_denominators():
    c = Confusion(tp=9, fn=1, fp=3, tn=27)
    assert c.sensitivity == wilson(9, 10)
    assert c.specificity == wilson(27, 30)
    assert c.accuracy == wilson(36, 40)
    assert c.ppv == wilson(9, 12)


def test_confusion_with_an_empty_denominator_is_undefined_not_an_error():
    c = Confusion(tp=0, fn=0, fp=2, tn=5)
    assert c.sensitivity is None
    assert c.ppv == wilson(0, 2)


def test_from_pairs_counts_the_four_cells():
    pairs = [("include", "include"), ("include", "exclude"), ("exclude", "include"), ("exclude", "exclude")]
    assert Confusion.from_pairs(pairs) == Confusion(tp=1, fn=1, fp=1, tn=1)


def _screening_fixture():
    return yaml.safe_load((SR_FIX / "screening-calibration.yml").read_text(encoding="utf-8"))


def test_calibration_fixture_reproduces_the_pinned_confusions():
    doc = _screening_fixture()
    assert doc["format"] == 1 and len(doc["records"]) == 40
    assert sum(1 for r in doc["records"] if r["gold"] == "include") == 10
    for name, column in (("A", "codex"), ("B", "claude")):
        pairs = [(r["gold"], r[column]) for r in doc["records"]]
        confusion = Confusion.from_pairs(pairs)
        exp = doc["expected"][name]
        assert (confusion.tp, confusion.fn, confusion.fp, confusion.tn) == (
            exp["tp"], exp["fn"], exp["fp"], exp["tn"]), name


def test_calibration_fixture_gives_the_pinned_sensitivity_and_accuracy():
    doc = _screening_fixture()
    a = Confusion.from_pairs([(r["gold"], r["codex"]) for r in doc["records"]])
    b = Confusion.from_pairs([(r["gold"], r["claude"]) for r in doc["records"]])
    assert a.sensitivity == wilson(9, 10) and a.specificity == wilson(27, 30) and a.accuracy == wilson(36, 40)
    assert b.sensitivity == wilson(10, 10) and b.specificity == wilson(24, 30) and b.accuracy == wilson(34, 40)


def _kappa_rows():
    return yaml.safe_load((SR_FIX / "kappa.yml").read_text(encoding="utf-8"))["rows"]


@pytest.mark.parametrize("row", _kappa_rows(), ids=lambda r: f"row{r['id']}")
def test_kappa_tables_reproduce_the_plan_values_exactly(row):
    value = evals.cohens_kappa_2x2(row["a"], row["b"], row["c"], row["d"])
    if row["kappa"] is None:
        assert value is None
    else:
        assert value == Fraction(row["kappa"])


def test_kappa_formula_matches_the_plan_fractions_by_hand():
    assert evals.cohens_kappa_2x2(20, 5, 10, 15) == Fraction(2, 5)
    assert evals.cohens_kappa_2x2(40, 10, 5, 45) == Fraction(7, 10)
    assert evals.cohens_kappa_2x2(10, 5, 3, 30) == Fraction(95, 159)
    assert evals.cohens_kappa_2x2(5, 1, 1, 6) == Fraction(58, 84)


def _harness(*args, fixtures: Path | None = None):
    cmd = [sys.executable, str(HARNESS), *args]
    if fixtures is not None:
        cmd += ["--fixtures", str(fixtures)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, timeout=300)


def test_harness_all_axes_exits_0_and_prints_each_pinned_interval():
    result = _harness("--axis", "all")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    for axis in ("dedup", "kappa", "prisma", "screening"):
        assert any(line.startswith(axis) for line in out.splitlines()), axis
    assert "110/110 = 1.000000 [0.966256, 1.000000]" in out
    assert "0/100 = 0.000000 [0.000000, 0.036993]" in out
    assert "9/10 = 0.900000 [0.595850, 0.982124]" in out
    assert "10/10 = 1.000000 [0.722467, 1.000000]" in out
    assert "27/30 = 0.900000 [0.743789, 0.965400]" in out
    assert "24/30 = 0.800000 [0.626943, 0.904949]" in out
    assert "36/40 = 0.900000 [0.769482, 0.960420]" in out
    assert "34/40 = 0.850000 [0.709277, 0.929388]" in out


def test_harness_json_has_one_entry_per_axis_and_all_pass():
    result = _harness("--axis", "all", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(result.stdout)
    assert [a["axis"] for a in doc["axes"]] == ["dedup", "kappa", "prisma", "screening"]
    assert all(a["pass"] for a in doc["axes"])


@pytest.mark.parametrize("axis", ["dedup", "kappa", "prisma", "screening"])
def test_harness_single_axis_exits_0(axis):
    result = _harness("--axis", axis)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith(axis)


def test_harness_corrupted_kappa_table_fails_only_the_kappa_axis(tmp_path):
    fixtures = tmp_path / "fixtures"
    shutil.copytree(REPO / "tests" / "fixtures" / "sr", fixtures / "sr")
    shutil.copytree(REPO / "tests" / "fixtures" / "kernel", fixtures / "kernel",
                    ignore=shutil.ignore_patterns("injection"))
    kappa = fixtures / "sr" / "kappa.yml"
    kappa.write_text(kappa.read_text(encoding="utf-8").replace('kappa: "2/5"', 'kappa: "3/5"'), encoding="utf-8")
    result = _harness("--axis", "all", fixtures=fixtures)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = {line.split(":")[0].split(" ")[0]: line for line in result.stdout.splitlines() if line.strip()}
    assert "FAIL" in lines["kappa"]
    assert "PASS" in lines["dedup"] and "PASS" in lines["prisma"] and "PASS" in lines["screening"]


def _load_harness():
    spec = importlib.util.spec_from_file_location("run_axes", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prisma_replay_reproduces_both_rounds_of_the_scenario():
    module = _load_harness()
    gold = yaml.safe_load((SR_FIX / "prisma-scenario.yml").read_text(encoding="utf-8"))
    flows = module.prisma_flows()
    keys = ("identified", "duplicates_removed", "records_after_dedup", "records_screened", "records_excluded",
            "reports_sought_for_retrieval", "reports_not_retrieved", "reports_assessed", "reports_excluded",
            "studies_included")
    for round_key, flow in (("round1", flows[0]), ("round2", flows[1])):
        for key in keys:
            assert flow[key] == gold[round_key][key], (round_key, key)
    assert flows[0]["identified_by_source"] == gold["round1"]["identified_by_source"]
    assert flows[0]["reports_excluded_by_reason"] == gold["round1"]["reports_excluded_by_reason"]


def test_kappa_axis_runs_the_vendored_kappa_that_sr_kappa_uses(monkeypatch):
    """Plan 5.11: the kappa axis gates on the function `sr kappa` runs, not on a private copy of the formula."""
    from scieflow._vendor.researcher_core import screen as vscreen

    module = _load_harness()
    real = vscreen.cohens_kappa
    seen = []

    def wrong(ledger, run_id, stage):
        seen.append(stage)
        return dataclasses.replace(real(ledger, run_id, stage), kappa=0.123)

    monkeypatch.setattr(vscreen, "cohens_kappa", wrong)
    axis = module.kappa_axis(REPO / "tests" / "fixtures")
    assert seen, "the kappa axis must call the vendored cohens_kappa"
    assert axis["pass"] is False
