"""M-H t9 (plan 5.15, 6 t9): the systematic-review workflow is registered, creating one through the
existing create-run path records it with its six phases, and a documented end-to-end pass works.

Synthetic data only; no network, no agent. The run is created through `service.create_run` exactly as
the menu and `scieflow run init` do.
"""

import shutil
import sys
from pathlib import Path

import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import menu, service
from scieflow.core.project import Project

ROOT = Path(__file__).resolve().parents[3]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
PHASES = ["protocol", "search", "screen-title-abstract", "screen-full-text", "appraise", "report"]
SKILL_REL = "src/scieflow/research/skills/systematic-review/SKILL.md"


def test_status_research_registers_the_six_phase_workflow():
    schema = yaml.safe_load((ROOT / "schemas" / "status-research.yml").read_text(encoding="utf-8"))
    assert schema["workflows"]["systematic-review"] == PHASES


def test_the_menu_entry_names_the_skill_the_question_and_the_screener_role():
    spec = menu.WORKFLOWS["systematic-review"]
    assert spec == {
        "skill": SKILL_REL,
        "ask": "Review question for the systematic review",
        "roles": ["research.sr-screeners"],
    }
    assert (ROOT / spec["skill"]).is_file()


def test_the_menu_workflow_prompt_points_at_the_skill():
    text = menu.prompt_for("systematic-review", "vitamin D and falls", "vd-falls")
    assert SKILL_REL in text and "vitamin D and falls" in text


def test_the_screener_role_is_known_to_the_agent_config():
    from scieflow.core import agent_config as ac

    assert "research.sr-screeners" in ac.ROLES


def _project(tmp_path, grant_credentials):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: per-campaign\nmax_iterations: 3\nmax_experiment_runs: 10\nmax_wall_minutes: 60\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research", "design"):
        shutil.copy(ROOT / "schemas" / f"{name}.yml", tmp_path / "schemas" / f"{name}.yml")
    (tmp_path / "workspace").mkdir()
    grant_credentials(tmp_path, "stub")
    return Project(tmp_path)


def test_create_run_records_the_systematic_review_workflow(tmp_path, grant_credentials):
    """The create-run path records the chosen workflow in config.yml, as for every workflow. Its
    status.yml is the research-loop status, which init.py (an INFLIGHT file) does not change."""
    project = _project(tmp_path, grant_credentials)
    service.create_run(project, "sr1", "Does exercise reduce falls?", workflow="systematic-review")
    ws = project.run_dir("sr1")
    cfg = yaml.safe_load((ws / "config.yml").read_text(encoding="utf-8"))
    assert cfg["workflow"] == "systematic-review"
    assert (ws / "status.yml").is_file()


def test_a_systematic_review_status_is_checked_against_the_six_phases():
    """A coordinator's workflow status declares `workflow:` and exactly the registered phases."""
    from scieflow.core.run import validate

    doc = {"workflow": "systematic-review", "slug": "sr1", "phase": "protocol",
           "phases": {p: "pending" for p in PHASES}}
    assert validate.validate_status_research(doc) == []
    extra = dict(doc, phases={**doc["phases"], "publish": "pending"})
    assert any("unknown phase" in e for e in validate.validate_status_research(extra))


def test_the_workflow_appears_in_the_menu_registry_listing(tmp_path, grant_credentials):
    project = _project(tmp_path, grant_credentials)
    names = [w["name"] for w in service.workflows()]
    assert "systematic-review" in names


def test_documented_commands_read_a_fresh_review_run(srroot, monkeypatch):
    """The skill's first coordinator steps, through the CLI only, on the synthetic review run r1
    (its design.yml is the t2 fixture; nothing is locked, searched, dispatched or adjudicated)."""
    from scieflow.core.design import cli as design_cli
    from scieflow.research.sr import cli as sr_cli

    monkeypatch.setattr(sr_cli, "_root", lambda: srroot)
    monkeypatch.setattr(design_cli, "_root", lambda: srroot)
    runner = CliRunner()
    check = runner.invoke(main, ["design", "check", "r1"])
    assert check.exit_code == 0, check.output
    before_lock = runner.invoke(main, ["sr", "protocol", "show", "r1"])
    assert before_lock.exit_code == 1  # refused: no locked protocol yet
    status_out = runner.invoke(main, ["sr", "status", "r1", "--json"])
    assert status_out.exit_code == 0, status_out.output
    assert '"protocol"' in status_out.output
