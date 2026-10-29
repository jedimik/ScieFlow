"""M-D2 t4 (plan 5.5): binding to the locked SAP, read by `scieflow experiment stats`.

Synthetic data only. Designs are locked through M-D1's API (see sap_support.py). The binding
module is imported inside the helper so each case fails at its own assertion until sap.py exists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from scieflow.core.data import levels
from scieflow.core.design import lock as design_lock
from scieflow.experiments import stats as st
from scieflow.experiments.analysis import AnalysisError, analyse_campaign

_SUPPORT = Path(__file__).with_name("sap_support.py")
_spec = importlib.util.spec_from_file_location("sap_support", _SUPPORT)
sup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup)

_STATS = Path(__file__).with_name("stats_support.py")
_sspec = importlib.util.spec_from_file_location("stats_support", _STATS)
ssup = importlib.util.module_from_spec(_sspec)
_sspec.loader.exec_module(ssup)

CONFIGS = ssup.CONFIGS
BASELINE = ssup.BASELINE


def _sap():
    from scieflow.experiments import sap
    return sap


@pytest.fixture
def ws(tmp_path):
    return sup.make_root(tmp_path / "proj")


def campaign(ws, analysis=None):
    values = {c: {"ssim": ssup.SSIM[c], "mse": sup.MSE[c]} for c in CONFIGS}
    return ssup.write_campaign(ws / "experiments", values=values, analysis=analysis,
                               metrics=("ssim", "mse"))


def rows(doc, metric, *, kind="tests"):
    return [t for t in doc[kind] if t["metric"] == metric]


# --- the binding itself ---------------------------------------------------------------------------

def test_campaign_outside_any_run_has_no_binding(tmp_path):
    cdir = ssup.write_campaign(tmp_path / "exp", values=None)
    assert _sap().binding_for_campaign(cdir).status == "none"


def test_run_without_a_design_has_no_binding(ws):
    cdir = campaign(ws)
    assert _sap().binding_for_campaign(cdir).status == "none"


def test_locked_design_binds_outcomes_and_multiplicity(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    cdir = campaign(ws)
    b = _sap().binding_for_campaign(cdir)
    assert b.status == "locked"
    assert b.study_type == "rct"
    assert b.computational is False
    assert set(b.outcomes) == {"ssim", "mse"}
    assert b.outcomes["ssim"]["test"] == "mann-whitney"
    assert b.outcomes["ssim"]["priority"] == "primary"
    assert b.outcomes["mse"]["priority"] == "exploratory"
    assert b.multiplicity == {"method": "holm", "alpha": 0.04}


def test_draft_and_modified_designs_are_reported_as_their_status(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    cdir = campaign(ws)
    assert _sap().binding_for_campaign(cdir).status == "draft"
    sup.lock_as_human(ws)
    import yaml
    doc = yaml.safe_load((ws / "design" / "design.yml").read_text(encoding="utf-8"))
    doc["question"] = "Edited after the lock."
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    assert _sap().binding_for_campaign(cdir).status == "modified"


# --- stats on a locked run ------------------------------------------------------------------------

def test_locked_stats_use_the_sap_tests_families_and_design_block(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE}))
    assert doc["design"]["status"] == "locked"
    assert doc["design"]["study_type"] == "rct"
    assert doc["design"]["in_force_hash"]
    assert doc["data_level"] == levels.run_level(ws)

    ssim = [t for t in rows(doc, "ssim") if t["computed"]]
    assert ssim, "the SAP primary outcome must produce computed tests"
    for t in ssim:
        assert t["test"] == "mann-whitney"
        assert t["source"] == "sap"
        assert t["family"] == "confirmatory"
        assert t["confirmatory"] is True

    mse = [t for t in rows(doc, "mse") if t["computed"]]
    assert mse
    for t in mse:
        assert t["test"] == "welch-t-test"
        assert t["source"] == "sap"
        assert t["family"] == "exploratory"
        assert t["confirmatory"] is False

    families = {f["name"]: f for f in doc["families"]}
    assert families["confirmatory"]["correction"] == "holm"
    assert families["confirmatory"]["alpha"] == 0.04
    assert families["exploratory"]["correction"] == "bh"
    assert families["exploratory"]["alpha"] == 0.04


def test_sap_holm_correction_is_applied_within_the_confirmatory_family(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE}))
    ssim = [t for t in rows(doc, "ssim") if t["computed"]]
    expected = st.adjust([t["p"] for t in ssim], "holm")
    for t, adjusted in zip(ssim, expected):
        assert t["p_adjusted"] == pytest.approx(adjusted)
        assert t["reject"] == (adjusted <= 0.04)


def test_sap_gatekeeping_leaves_p_values_unadjusted_and_warns(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct(method="gatekeeping"))
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE}))
    ssim = [t for t in rows(doc, "ssim") if t["computed"]]
    assert ssim
    assert all(t["p_adjusted"] is None and t["reject"] is None for t in ssim)
    families = {f["name"]: f for f in doc["families"]}
    assert families["confirmatory"]["correction"] == "not-applied"
    assert "sap-correction-unsupported" in [w["rule"] for w in doc["warnings"]]


def test_sap_test_outside_the_supported_set_is_not_computed(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct(ssim_test="cox-regression"))
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE}))
    ssim = rows(doc, "ssim")
    assert ssim
    assert all(t["computed"] is False and t["reason"] == "test-unsupported" for t in ssim)


def test_sap_descriptive_outcome_gets_no_test(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct(ssim_test="descriptive"))
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE}))
    assert rows(doc, "ssim") == []
    assert [t for t in rows(doc, "mse") if t["computed"]]


def test_unlocked_draft_is_never_confirmatory(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE, "test": "welch-t-test"}))
    assert doc["design"]["status"] == "draft"
    assert doc["tests"]
    assert all(t["confirmatory"] is False and t["family"] == "campaign" for t in doc["tests"])
    assert [f["name"] for f in doc["families"]] == ["campaign"]
    assert "design-draft" in [w["rule"] for w in doc["warnings"]]


def test_an_exception_in_the_design_read_falls_back_to_campaign_settings(ws, monkeypatch):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)

    def boom(project, run):
        raise RuntimeError("simulated design failure")

    monkeypatch.setattr(design_lock, "state", boom)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE, "test": "welch-t-test"}))
    assert doc["design"]["status"] == "error"
    assert all(t["confirmatory"] is False and t["family"] == "campaign" for t in doc["tests"])
    assert "design-error" in [w["rule"] for w in doc["warnings"]]


# --- computational designs: warnings, unplanned metrics --------------------------------------------

def test_computational_unplanned_metric_is_exploratory_and_flagged(ws):
    sup.write_design(ws, "computational.yml", sup.sap_computational())
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {
        "baseline": BASELINE, "test": {"ssim": "mann-whitney", "mse": "welch-t-test"}}))
    mse = [t for t in rows(doc, "mse") if t["computed"]]
    assert mse
    for t in mse:
        assert t["family"] == "exploratory"
        assert t["unplanned"] is True
        assert t["source"] == "campaign"
    assert "out-of-sap" in [w["rule"] for w in doc["warnings"]]


def test_computational_conflict_is_warned_and_the_sap_test_is_used(ws):
    sup.write_design(ws, "computational.yml", sup.sap_computational())
    sup.lock_as_human(ws)
    doc = analyse_campaign(campaign(ws, {"baseline": BASELINE, "test": "welch-t-test"}))
    ssim = [t for t in rows(doc, "ssim") if t["computed"]]
    assert ssim and all(t["test"] == "mann-whitney" and t["source"] == "sap" for t in ssim)
    assert "sap-test-mismatch" in [w["rule"] for w in doc["warnings"]]


def test_non_computational_conflict_refuses_stats(ws):
    sup.write_design(ws, "rct.yml", sup.sap_rct())
    sup.lock_as_human(ws)
    with pytest.raises(AnalysisError, match="sap-test-mismatch"):
        analyse_campaign(campaign(ws, {"baseline": BASELINE, "test": {"ssim": "welch-t-test"}}))
