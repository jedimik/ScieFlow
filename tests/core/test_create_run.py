"""Creating a run through the service layer.

The wizard is the first thing to hand `run.init` a slug that came from an
HTTP form, and `init_workspace` joins that slug to the workspace root with no
validation of its own — so the refusals below are the load-bearing part of
this module, not edge cases.
"""

import sys
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: per-campaign\nmax_iterations: 3\n"
        "max_experiment_runs: 10\nmax_wall_minutes: 60\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    (tmp_path / "workspace").mkdir()
    return Project(tmp_path)


def test_create_run_makes_a_workspace_with_its_goal(project):
    detail = service.create_run(project, "r1", "Find a better catalyst.")
    ws = project.run_dir("r1")
    assert (ws / "goal.md").read_text() == "Find a better catalyst."
    assert (ws / "status.yml").exists() and (ws / "budget.yml").exists()
    assert detail["status"]["run"] == "r1"


def test_create_run_applies_the_defaults(project):
    from scieflow.core.run import budget

    service.create_run(project, "r1", "a goal")
    limits = budget.read_budget(project.run_dir("r1"))["budgets"]
    assert limits["max_iterations"] == 3 and limits["max_wall_minutes"] == 60


def test_create_run_applies_overrides(project):
    from scieflow.core.run import budget, status

    service.create_run(project, "r1", "a goal", approval="autonomous",
                       max_iterations=7, max_wall_minutes=120)
    ws = project.run_dir("r1")
    assert status.read_status(ws)["approval"] == "autonomous"
    limits = budget.read_budget(ws)["budgets"]
    assert limits["max_iterations"] == 7 and limits["max_wall_minutes"] == 120


@pytest.mark.parametrize("bad", ["../escape", "a/b", "/abs", "", "   ", "..",
                                 "workspace/../escape"])
def test_a_slug_that_escapes_the_workspace_is_refused(project, bad):
    """`init_workspace` joins the slug to the workspace root with no checks of
    its own, and this is the first caller whose slug comes from a form."""
    with pytest.raises(service.ServiceError):
        service.create_run(project, bad, "a goal")
    made = [p.name for p in (project.root / "workspace").iterdir()]
    assert made == [], f"something was created outside the intended run: {made}"
    assert not (project.root.parent / "escape").exists()


def test_a_duplicate_slug_is_refused_and_leaves_the_first_run_alone(project):
    service.create_run(project, "r1", "the original goal")
    with pytest.raises(service.ServiceError, match="exists"):
        service.create_run(project, "r1", "a different goal")
    assert (project.run_dir("r1") / "goal.md").read_text() == "the original goal"


def test_an_empty_goal_is_refused(project):
    with pytest.raises(service.ServiceError, match="goal"):
        service.create_run(project, "r1", "   ")
    assert not project.run_dir("r1").exists()


def test_a_failure_partway_leaves_no_half_made_run(project, monkeypatch):
    """`init_workspace` cleans up after itself, but the service does more than
    call it. A run that exists with no status, or a conversation record
    pointing at a workspace that was removed, would both be worse than a
    clean refusal."""
    from scieflow.core.run import init as init_mod

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(init_mod, "init_workspace", explode)
    with pytest.raises(service.ServiceError):
        service.create_run(project, "r1", "a goal")
    assert not project.run_dir("r1").exists()


def test_an_unknown_workflow_is_refused_before_anything_is_written(project):
    with pytest.raises(service.ServiceError, match="workflow"):
        service.create_run(project, "r1", "a goal", workflow="nonesuch")
    assert not project.run_dir("r1").exists()


def test_workflows_lists_the_registry(project):
    names = [w["name"] for w in service.workflows()]
    assert "research-loop" in names and "lit-review" in names
    assert all(w["ask"] for w in service.workflows())
