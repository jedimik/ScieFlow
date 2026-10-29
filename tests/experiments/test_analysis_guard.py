"""M-D2 t4 (plan 5.5): the SAP guard in `scieflow experiment run|sweep` and `stats`.

A campaign whose analysis block contradicts a locked SAP is refused before any run or spend
(non-computational) or warned about and run under the SAP (computational). Locks are made the way a
human makes them (M-D1 lock.lock + service.answer_gate). Synthetic data only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.experiments.cli import experiment

_SUPPORT = Path(__file__).with_name("sap_support.py")
_spec = importlib.util.spec_from_file_location("sap_support", _SUPPORT)
sup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup)

_STATS = Path(__file__).with_name("stats_support.py")
_sspec = importlib.util.spec_from_file_location("stats_support", _STATS)
ssup = importlib.util.module_from_spec(_sspec)
_sspec.loader.exec_module(ssup)


def _campaign_path(droot):
    return droot / "pipelines" / "demo" / "campaigns" / "gain-sweep.yaml"


def with_analysis(droot, analysis):
    path = _campaign_path(droot)
    doc = yaml.safe_load(path.read_text())
    doc["analysis"] = analysis
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def invoke(droot, ws, verb="sweep"):
    args = [verb, "-c", str(_campaign_path(droot)), "--direct",
            "--experiments-dir", str(ws / "experiments"),
            "--pipelines-dir", str(droot / "pipelines")]
    return CliRunner().invoke(experiment, args)


def of_type(ws, type_):
    return [e for e in events.read(ws) if e["type"] == type_]


def refusals(ws):
    return of_type(ws, "job.refused")


def warnings(ws):
    return of_type(ws, "note.design_warning")


def _droot(project):
    ws = sup.make_root(project)
    return project, ws


# --- non-computational: a conflicting campaign is refused before anything runs --------------------

def test_conflicting_test_is_refused_before_any_run(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "welch-t-test"}})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "sap-test-mismatch" in result.output and "ssim" in result.output
    assert not (ws / "experiments").exists()
    assert "budget.recorded" not in [e["type"] for e in events.read(ws)]
    assert len(refusals(ws)) == 1
    assert refusals(ws)[0]["data"]["reason"] == "design"
    assert refusals(ws)[0]["data"]["rule"] == "sap-test-mismatch"


def test_single_run_obeys_the_same_conflict_rule(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "welch-t-test"}})
    result = invoke(droot, ws, "run")
    assert result.exit_code == 1, result.output
    assert "sap-test-mismatch" in result.output
    assert not (ws / "experiments").exists()


def test_a_string_test_names_only_the_metric_the_sap_disagrees_with(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    # welch-t-test agrees with the exploratory mse outcome and disagrees with the primary ssim.
    with_analysis(droot, {"test": "welch-t-test"})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "sap-test-mismatch" in result.output and "ssim" in result.output
    assert "mse" not in result.output.split("sap-test-mismatch", 1)[1].split("\n", 1)[0]


def test_conflicting_correction_is_refused(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct(method="holm", alpha=0.04))
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "mann-whitney", "mse": "welch-t-test"},
                          "correction": "bh"})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "sap-correction-mismatch" in result.output
    assert refusals(ws)[0]["data"]["rule"] == "sap-correction-mismatch"
    assert not (ws / "experiments").exists()


def test_conflicting_alpha_is_refused(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct(method="holm", alpha=0.04))
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "mann-whitney", "mse": "welch-t-test"},
                          "alpha": 0.05})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "sap-alpha-mismatch" in result.output
    assert refusals(ws)[0]["data"]["rule"] == "sap-alpha-mismatch"


def test_campaign_agreeing_with_the_sap_runs(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct(method="holm", alpha=0.04))
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "mann-whitney", "mse": "welch-t-test"},
                          "correction": "holm", "alpha": 0.04})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert (ws / "experiments").exists()
    assert refusals(ws) == []


def test_no_design_leaves_the_guard_out(project):
    droot, ws = _droot(project)
    with_analysis(droot, {"test": {"ssim": "welch-t-test"}, "alpha": 0.05})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert refusals(ws) == []


# --- computational: warn, and run under the SAP ---------------------------------------------------

def test_computational_conflict_warns_and_runs(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "computational.yml", sup.sap_computational())
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "welch-t-test"}})
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert "warning: design:" in result.output and "sap-test-mismatch" in result.output
    assert [w["data"]["rule"] for w in warnings(ws) if w["data"]["rule"] == "sap-test-mismatch"] \
        == ["sap-test-mismatch"]
    assert refusals(ws) == []


def test_single_run_computational_conflict_warns_and_runs(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "computational.yml", sup.sap_computational())
    sup.lock_as_human(ws)
    with_analysis(droot, {"test": {"ssim": "welch-t-test"}})
    result = invoke(droot, ws, "run")
    assert result.exit_code == 0, result.output
    assert "sap-test-mismatch" in result.output
    assert refusals(ws) == []


# --- stats refuses the same conflict on a campaign run before the lock ----------------------------

def test_stats_refuses_a_conflicting_earlier_campaign(project):
    droot, ws = _droot(project)
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    values = {c: {"ssim": ssup.SSIM[c], "mse": sup.MSE[c]} for c in ssup.CONFIGS}
    cdir = ssup.write_campaign(ws / "experiments", values=values, metrics=("ssim", "mse"),
                               analysis={"baseline": ssup.BASELINE,
                                         "test": {"ssim": "welch-t-test"}})
    result = CliRunner().invoke(experiment, ["stats", str(cdir)])
    assert result.exit_code == 1, result.output
    assert "sap-test-mismatch" in result.output
    assert not (cdir / "stats.json").exists()
