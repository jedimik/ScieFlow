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


def test_a_goal_file_write_failure_is_a_service_error_with_no_leaked_temp_dir(
        project, monkeypatch, tmp_path):
    """The temp dir holding `goal.md` is created and written to *before*
    `init_workspace` is ever called. A failure right there — disk full, no
    permission on the temp dir — must still come out as a `ServiceError`,
    and must not leave that temp directory behind, the same as a failure one
    step later inside `init_workspace` itself."""
    from scieflow.core import service as service_mod

    fake_tmp = tmp_path / "fake-goal-tmp"

    def fake_mkdtemp(*a, **kw):
        fake_tmp.mkdir()
        return str(fake_tmp)

    monkeypatch.setattr(service_mod.tempfile, "mkdtemp", fake_mkdtemp)

    real_write_text = Path.write_text

    def explode(self, *a, **kw):
        if self == fake_tmp / "goal.md":
            raise OSError("disk full")
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", explode)

    with pytest.raises(service.ServiceError):
        service.create_run(project, "r1", "a goal")
    assert not fake_tmp.exists(), "the temp directory was left behind"
    assert not project.run_dir("r1").exists()


def test_an_unknown_workflow_is_refused_before_anything_is_written(project):
    with pytest.raises(service.ServiceError, match="workflow"):
        service.create_run(project, "r1", "a goal", workflow="nonesuch")
    assert not project.run_dir("r1").exists()


def test_workflows_lists_the_registry(project):
    names = [w["name"] for w in service.workflows()]
    assert "research-loop" in names and "lit-review" in names
    assert all(w["ask"] for w in service.workflows())


@pytest.fixture
def project_with_agent(tmp_path):
    """The same shape as `project`, with `stub` additionally configured to
    hold a conversation (`family`, `session_cmd`, `resume_cmd`) — copied from
    the conversation milestone's fixture in `tests/core/test_service.py`.
    `project` itself keeps `stub` non-conversational, which is what the
    refusal test needs."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: per-campaign\nmax_iterations: 3\n"
        "max_experiment_runs: 10\nmax_wall_minutes: 60\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    (tmp_path / "workspace").mkdir()
    return Project(tmp_path)


def test_start_run_creates_the_run_and_takes_the_first_turn(project_with_agent):
    from scieflow.core.run import conversation

    project = project_with_agent
    result = service.start_run(project, "r1", "Find a catalyst.", "stub",
                               workflow="research-loop")
    ws = project.run_dir("r1")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub"
    assert [t["role"] for t in doc["turns"]] == ["human", "agent"]
    assert result["turn"]["role"] == "agent"


def test_the_first_turn_names_the_workflow_skill(project_with_agent):
    """The prompt must follow the convention `menu` already uses to hand a run
    to a coordinator, so the TUI and the browser say the same thing."""
    from scieflow.core.run import conversation

    project = project_with_agent
    service.start_run(project, "r1", "Find a catalyst.", "stub",
                      workflow="research-loop")
    first = conversation.read(project.run_dir("r1"))["turns"][0]["text"]
    assert "skills/research-loop/SKILL.md" in first
    assert "Find a catalyst." in first


def test_start_run_without_an_agent_still_creates_the_run(project):
    """A registry with nothing conversational must still let someone make a
    run — they can choose an agent later on its page."""
    result = service.start_run(project, "r1", "a goal", "")
    assert project.run_dir("r1").exists()
    assert "turn" not in result or result["turn"] is None


def test_start_run_refuses_an_agent_that_cannot_converse_before_creating(project):
    """The run must not exist afterwards: a half-started run with no way to
    talk to it is worse than a refusal."""
    with pytest.raises(service.ServiceError, match="conversation"):
        service.start_run(project, "r1", "a goal", "stub")   # no session_cmd
    assert not project.run_dir("r1").exists()


def test_a_very_large_goal_survives_storage_and_the_first_turn(project_with_agent):
    """The goal is stored verbatim and pinned into every prompt, so it can push
    the first turn's prompt past the argv limit."""
    project = project_with_agent
    goal = "G" * 150_000
    from scieflow.core.run import conversation

    service.start_run(project, "r1", goal, "stub", workflow="research-loop")
    ws = project.run_dir("r1")
    assert (ws / "goal.md").read_text() == goal
    agent_turn = next(t for t in conversation.read(ws)["turns"] if t["role"] == "agent")
    assert agent_turn["job_id"], "the first turn was never dispatched"


def test_goal_text_is_stored_literally(project_with_agent):
    """Braces and delimiter-looking text are data, not templating."""
    project = project_with_agent
    goal = "Use {model} and --- and ## headings; mind `backticks`."
    service.start_run(project, "r1", goal, "stub", workflow="research-loop")
    assert (project.run_dir("r1") / "goal.md").read_text() == goal
