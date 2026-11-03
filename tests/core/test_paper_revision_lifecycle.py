"""M-G2 t1: the paper-revision native lifecycle (vocabulary, init, adopt, checkpoint).

Synthetic metadata only: every goal, manuscript name and status here is invented.
No gate logic is exercised in this file (that is M-G2 t2).
"""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.core.project import Project
from scieflow.core.run import checkpoint, status, validate
from scieflow.core.run.init import adopt_workspace
from scieflow.core.service import ServiceError, create_run

ROOT = Path(__file__).resolve().parents[2]
REVISION_PHASES = ["intake", "structure", "revise", "respond", "verify", "resubmit"]
PAPER_DRAFT_PHASES = ["intake", "outline", "draft", "cross-review", "merge", "verify", "handoff"]


@pytest.fixture
def paper_project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: autonomous\nmax_iterations: 5\nmax_experiment_runs: 40\n"
        "max_wall_minutes: 120\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "status-research", "gates"):
        (tmp_path / "schemas" / f"{name}.yml").write_bytes(
            (ROOT / "schemas" / f"{name}.yml").read_bytes())
    (tmp_path / "workspace").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "_VOCAB", None)
    return Project(tmp_path)


def invoke(*args):
    return CliRunner().invoke(main, list(args))


def synthetic_goal(project: Project, name: str = "synthetic-goal.md") -> Path:
    goal = project.root / name
    goal.write_text("Synthetic paper-revision lifecycle exercise only.\n")
    return goal


def init_revision(project: Project, slug: str = "rev", *extra: str):
    goal = synthetic_goal(project)
    return invoke("run", "init", slug, "--workflow", "paper-revision", "--goal", str(goal),
                  "--approval", "per-campaign", *extra)


# --- vocabulary -------------------------------------------------------------

def test_phase_vocabulary_is_shared_by_schema_and_status_module(paper_project):
    assert status.phases_for("paper-revision") == REVISION_PHASES
    assert status.PAPER_WORKFLOWS == ("paper-draft", "paper-revision")


def test_schemas_declare_paper_revision_rows(paper_project):
    loop_free = yaml.safe_load((ROOT / "schemas" / "status.yml").read_text())["workflows"]
    assert loop_free["paper-revision"] == REVISION_PHASES
    assert tuple(loop_free) == status.PAPER_WORKFLOWS
    lenient = yaml.safe_load((ROOT / "schemas" / "status-research.yml").read_text())["workflows"]
    assert lenient["paper-revision"] == status.phases_for("paper-revision")


def test_service_still_refuses_paper_revision_creation(paper_project):
    # M-G2-D9: paper-revision is not on the menu or the Start page; creation is CLI-native only.
    with pytest.raises(ServiceError, match="unknown workflow: paper-revision"):
        create_run(paper_project, "rev-svc", "Synthetic goal.", workflow="paper-revision",
                   approval="per-campaign")


# --- init -------------------------------------------------------------------

def test_init_paper_revision_creates_native_status(paper_project):
    goal = synthetic_goal(paper_project)
    result = init_revision(paper_project)
    assert result.exit_code == 0, result.output
    ws = paper_project.run_dir("rev")
    doc = json.loads(invoke("run", "show", "rev", "--json").output)
    st = doc["status"]
    assert st["workflow"] == "paper-revision"
    assert st["slug"] == "rev" and "run" not in st
    assert st["approval"] == "per-campaign"
    assert list(st["phases"]) == REVISION_PHASES
    assert set(st["phases"].values()) == {"pending"}
    assert validate.validate_status(st) == []
    assert validate.validate_status_research(st) == []
    assert doc["budget"] is None and not (ws / "budget.yml").exists()
    assert not (ws / "iterations").exists() and not (ws / "notebook.md").exists()
    assert (ws / "goal.md").read_bytes() == goal.read_bytes()
    assert yaml.safe_load((ws / "config.yml").read_text()) == {
        "workflow": "paper-revision", "approval": "per-campaign"}
    assert [e["type"] for e in events.read(ws)] == ["run.created"]


def test_init_paper_revision_requires_explicit_approval(paper_project):
    goal = synthetic_goal(paper_project)
    result = invoke("run", "init", "rev", "--workflow", "paper-revision", "--goal", str(goal))
    assert result.exit_code != 0
    assert "paper-revision initialization requires explicit --approval" in result.output
    assert not paper_project.run_dir("rev").exists()


def test_init_paper_revision_refuses_partial_budget(paper_project):
    result = init_revision(paper_project, "rev", "--max-iterations", "3")
    assert result.exit_code != 0
    assert "supply all three budget limits, or none, for paper-revision" in result.output
    assert not paper_project.run_dir("rev").exists()


def test_init_paper_revision_with_all_budget_limits_writes_budget(paper_project):
    result = init_revision(paper_project, "rev", "--max-iterations", "3",
                           "--max-experiment-runs", "4", "--max-wall-minutes", "60")
    assert result.exit_code == 0, result.output
    assert (paper_project.run_dir("rev") / "budget.yml").exists()


def test_init_paper_draft_messages_are_unchanged(paper_project):
    goal = synthetic_goal(paper_project)
    no_approval = invoke("run", "init", "pd", "--workflow", "paper-draft", "--goal", str(goal))
    assert no_approval.exit_code != 0
    assert "paper-draft initialization requires explicit --approval" in no_approval.output
    partial = invoke("run", "init", "pd", "--workflow", "paper-draft", "--goal", str(goal),
                     "--approval", "per-campaign", "--max-iterations", "3")
    assert partial.exit_code != 0
    assert "supply all three budget limits, or none, for paper-draft" in partial.output


# --- adopt ------------------------------------------------------------------

def prepared_workspace(project: Project, slug: str) -> dict[str, bytes]:
    ws = project.run_dir(slug)
    ws.mkdir(parents=True)
    (ws / "goal.md").write_text("Prepared synthetic goal.\n")
    (ws / "notes.txt").write_text("Prepared synthetic notes; never rewritten by adoption.\n")
    return {name: (ws / name).read_bytes() for name in ("goal.md", "notes.txt")}


def test_adopt_paper_revision_keeps_prepared_files(paper_project):
    before = prepared_workspace(paper_project, "prep")
    result = invoke("run", "adopt", "prep", "--workflow", "paper-revision",
                    "--approval", "per-campaign", "--as-agent")
    assert result.exit_code == 0, result.output
    ws = paper_project.run_dir("prep")
    for name, data in before.items():
        assert (ws / name).read_bytes() == data
    st = status.read_status(ws)
    assert st["workflow"] == "paper-revision" and st["slug"] == "prep"
    assert list(st["phases"]) == REVISION_PHASES
    assert validate.validate_status(st) == []
    adopted = [e for e in events.read(ws) if e["type"] == "note.run-adopted"]
    assert len(adopted) == 1
    assert adopted[0]["data"] == {"workflow": "paper-revision", "approval": "per-campaign"}
    assert adopted[0]["actor"] == "agent"


def test_adopt_cli_rejects_research_loop_workflow(paper_project):
    prepared_workspace(paper_project, "prep")
    result = invoke("run", "adopt", "prep", "--workflow", "research-loop",
                    "--approval", "per-campaign", "--as-agent")
    assert result.exit_code == 2


def test_adopt_refuses_an_already_initialised_run(paper_project):
    prepared_workspace(paper_project, "prep")
    assert invoke("run", "adopt", "prep", "--workflow", "paper-revision",
                  "--approval", "per-campaign", "--as-agent").exit_code == 0
    again = invoke("run", "adopt", "prep", "--workflow", "paper-revision",
                   "--approval", "per-campaign", "--as-agent")
    assert again.exit_code != 0


def test_adopt_workspace_names_both_paper_workflows(paper_project):
    prepared_workspace(paper_project, "prep")
    with pytest.raises(ValueError,
                       match="^adoption supports only paper-draft and paper-revision$"):
        adopt_workspace("prep", paper_project.workspace_root, workflow="lit-review",
                        approval="per-campaign")


# --- phases, advance, checkpoint and resume texts ---------------------------

def test_paper_revision_phases_mark_and_refuse_foreign_names(paper_project):
    assert init_revision(paper_project).exit_code == 0
    ws = paper_project.run_dir("rev")
    for phase in ("intake", "structure", "revise", "resubmit"):
        assert invoke("run", "mark", "rev", phase, "done", "--as-agent").exit_code == 0
    assert invoke("run", "mark", "rev", "respond", "running", "--as-agent").exit_code == 0
    assert invoke("run", "mark", "rev", "verify", "failed", "--as-agent").exit_code == 0
    before = (ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes()
    for phase in ("outline", "draft", "hypothesize", "experiment", "unknown"):
        assert invoke("run", "mark", "rev", phase, "done", "--as-agent").exit_code != 0
        assert before == ((ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes())
    # A paper-revision run is not a loop: no iteration advance (require_loop).
    assert invoke("run", "advance", "rev", "--as-agent").exit_code != 0
    assert before == ((ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes())


def test_paper_revision_checkpoint_and_resume_texts(paper_project):
    assert init_revision(paper_project).exit_code == 0
    ws = paper_project.run_dir("rev")
    assert invoke("run", "mark", "rev", "intake", "done", "--as-agent").exit_code == 0
    result = invoke("run", "checkpoint", "rev", "--reason", "user", "--as-agent")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "Resume: read status.yml; paper-revision phase 'structure'.")
    assert "iterations/" not in result.output and "budget.yml" not in result.output
    assert invoke("run", "resume", "rev", "--as-agent").exit_code == 0
    assert checkpoint.resume_info(ws) == (
        "Run active: paper-revision, next phase 'structure'.")


def test_paper_revision_checkpoint_mentions_budget_only_when_present(paper_project):
    assert init_revision(paper_project, "bud", "--max-iterations", "3",
                         "--max-experiment-runs", "4", "--max-wall-minutes", "60").exit_code == 0
    result = invoke("run", "checkpoint", "bud", "--reason", "user", "--as-agent")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "Resume: read status.yml; paper-revision phase 'intake'. Budget ledger in budget.yml.")


def test_paper_draft_checkpoint_text_is_unchanged(paper_project):
    goal = synthetic_goal(paper_project)
    assert invoke("run", "init", "pd", "--workflow", "paper-draft", "--goal", str(goal),
                  "--approval", "per-campaign").exit_code == 0
    result = invoke("run", "checkpoint", "pd", "--reason", "user", "--as-agent")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Resume: read status.yml; paper-draft phase 'intake'."
