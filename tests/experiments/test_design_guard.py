"""M-D1 t5 (plan 5.6, 6 t5): the study-design guard in `scieflow experiment run|sweep`.

A run that holds a design is bound by it: campaigns whose metrics are not outcomes of the locked
SAP are refused (non-computational) or warned about (computational), and every campaign is refused
while the design file differs from its lock or the ledger does not verify. Locks are made the way a
human makes them (lock.lock + service.answer_gate), never by writing the ledger. Synthetic data only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, service
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.core.run import status
from scieflow.experiments.cli import experiment

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "design"


@pytest.fixture
def droot(project):
    """The experiments fixture root plus the schemas and checklists the design code reads."""
    shutil.copytree(REPO / "schemas", project / "schemas")
    shutil.copytree(REPO / "config" / "reporting", project / "config" / "reporting")
    run = project / "workspace" / "r1"
    run.mkdir(parents=True)
    status.write_status(run, status.new_status("r1", "autonomous"))
    return project


@pytest.fixture
def ws(droot):
    return droot / "workspace" / "r1"


def rct_with_outcomes(*metrics):
    """The valid RCT fixture, its SAP outcomes naming exactly these campaign metrics."""
    def mutate(doc):
        base = doc["sap"]["outcomes"][0]
        doc["sap"]["outcomes"] = [
            dict(base, id=base["id"] if i == 0 else f"out_{i}", metric=m,
                 priority="primary" if i == 0 else "secondary")
            for i, m in enumerate(metrics)
        ]
    return "rct.yml", mutate


def write_design(ws, source, mutate=None):
    (ws / "design" / "power").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "power-rct-ttest.json", ws / "design" / "power" / "rct-ttest.json")
    doc = yaml.safe_load((FIX / source).read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(doc)
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def set_design(ws, source_and_mutate):
    source, mutate = source_and_mutate
    write_design(ws, source, mutate)


def lock_as_human(droot, ws):
    proj = Project(droot)
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, "r1", gate["id"], "approve")


def invoke(droot, ws, verb="sweep"):
    args = [verb, "-c", str(droot / "pipelines" / "demo" / "campaigns" / "gain-sweep.yaml"),
            "--direct", "--experiments-dir", str(ws / "experiments"),
            "--pipelines-dir", str(droot / "pipelines")]
    return CliRunner().invoke(experiment, args)


def of_type(ws, type_):
    return [e for e in events.read(ws) if e["type"] == type_]


def refusals(ws):
    return of_type(ws, "job.refused")


def warnings(ws):
    return of_type(ws, "note.design_warning")


# --- non-computational: refusal rules --------------------------------------------------------------

def test_locked_design_refuses_a_campaign_metric_outside_the_sap(droot, ws):
    set_design(ws, rct_with_outcomes("ssim"))
    lock_as_human(droot, ws)
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "out-of-sap" in result.output and "mse" in result.output
    assert not (ws / "experiments").exists()
    assert len(refusals(ws)) == 1
    assert refusals(ws)[0]["data"]["reason"] == "design"
    assert refusals(ws)[0]["data"]["rule"] == "out-of-sap"
    assert "budget.recorded" not in [e["type"] for e in events.read(ws)]


def test_locked_design_whose_sap_names_every_metric_runs_as_today(droot, ws):
    set_design(ws, rct_with_outcomes("ssim", "mse"))
    lock_as_human(droot, ws)
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert (ws / "experiments").exists()
    assert refusals(ws) == []


def test_single_run_obeys_the_same_verdict(droot, ws):
    set_design(ws, rct_with_outcomes("ssim"))
    lock_as_human(droot, ws)
    refused = invoke(droot, ws, "run")
    assert refused.exit_code == 1, refused.output
    assert "out-of-sap" in refused.output
    assert not (ws / "experiments").exists()


def test_single_run_under_a_covering_sap_runs(droot, ws):
    set_design(ws, rct_with_outcomes("ssim", "mse"))
    lock_as_human(droot, ws)
    assert invoke(droot, ws, "run").exit_code == 0


def test_unlocked_non_computational_design_refuses_with_design_unlocked(droot, ws):
    write_design(ws, "rct.yml")
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "design-unlocked" in result.output and "scieflow design lock" in result.output
    assert not (ws / "experiments").exists()
    assert refusals(ws)[0]["data"]["rule"] == "design-unlocked"


def test_locked_then_edited_design_refuses_with_design_modified(droot, ws):
    write_design(ws, "rct.yml")
    lock_as_human(droot, ws)
    doc = yaml.safe_load((ws / "design" / "design.yml").read_text(encoding="utf-8"))
    doc["question"] = "A different question written after the lock."
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "design-modified" in result.output and "differs from its lock" in result.output
    assert not (ws / "experiments").exists()
    assert refusals(ws)[0]["data"]["rule"] == "design-modified"


def test_broken_ledger_refuses_with_design_ledger_invalid(droot, ws):
    write_design(ws, "rct.yml")
    lock_as_human(droot, ws)
    (ws / "design" / "lock.jsonl").write_text("not json\n")
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "design-ledger-invalid" in result.output
    assert not (ws / "experiments").exists()


def test_unreadable_design_refuses_with_design_invalid(droot, ws):
    (ws / "design").mkdir(parents=True)
    (ws / "design" / "design.yml").write_text("title: [unclosed\n")
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "design-invalid" in result.output
    assert not (ws / "experiments").exists()


# --- computational: warnings only, except integrity -----------------------------------------------

def test_unlocked_computational_design_runs_with_one_warning(droot, ws):
    write_design(ws, "computational.yml")
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert "warning: design:" in result.output and "design-unlocked" in result.output
    assert [w["data"]["rule"] for w in warnings(ws)] == ["design-unlocked"]
    assert warnings(ws)[0]["data"]["campaign"] == "gain-sweep"
    assert refusals(ws) == []


def test_locked_computational_design_warns_about_metrics_outside_the_sap(droot, ws):
    write_design(ws, "computational.yml")
    lock_as_human(droot, ws)
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert [w["data"]["rule"] for w in warnings(ws)] == ["out-of-sap"]
    assert "mse" in result.output
    assert refusals(ws) == []


def test_locked_then_edited_computational_design_is_still_refused(droot, ws):
    write_design(ws, "computational.yml")
    lock_as_human(droot, ws)
    doc = yaml.safe_load((ws / "design" / "design.yml").read_text(encoding="utf-8"))
    doc["question"] = "Edited after the lock."
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 1, result.output
    assert "design-modified" in result.output
    assert refusals(ws)[0]["data"]["rule"] == "design-modified"


# --- no design: behaviour and events unchanged -----------------------------------------------------

def test_run_without_a_design_behaves_as_today(droot, ws):
    result = invoke(droot, ws, "sweep")
    assert result.exit_code == 0, result.output
    assert (ws / "experiments").exists()
    assert warnings(ws) == [] and refusals(ws) == []
    assert "warning: design" not in result.output


def test_campaign_outside_any_run_is_unaffected(droot):
    result = CliRunner().invoke(experiment, [
        "sweep", "-c", str(droot / "pipelines" / "demo" / "campaigns" / "gain-sweep.yaml"),
        "--direct", "--experiments-dir", str(droot / "exp"),
        "--pipelines-dir", str(droot / "pipelines")])
    assert result.exit_code == 0, result.output
    assert (droot / "exp").exists()
