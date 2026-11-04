"""M-H t9 (plan 5.15, 6 t9): the systematic-review workflow is registered, creating one through the
existing create-run path records it with its six phases, and a documented end-to-end pass works.

Synthetic data only; no network, no agent. The run is created through `service.create_run` exactly as
the menu and `scieflow run init` do.
"""

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import menu, service
from scieflow.core.project import Project
from tests.research.sr import t6_scenario as scn

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


def test_the_workflow_appears_in_the_menu_registry_listing():
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


GOLD = yaml.safe_load((scn.REPO / "tests" / "fixtures" / "sr" / "prisma-scenario.yml").read_text(encoding="utf-8"))
FLOW_KEYS = ("identified", "duplicates_removed", "records_after_dedup", "records_screened", "records_excluded",
             "reports_sought_for_retrieval", "reports_not_retrieved", "reports_assessed", "reports_excluded",
             "studies_included")
ROUND2_KEYS = ("identified", "duplicates_removed", "records_after_dedup", "records_screened", "records_excluded",
               "reports_sought_for_retrieval", "studies_included")


@pytest.fixture
def review_root(tmp_path, monkeypatch):
    """The t6 synthetic review project. The working directory is its root, so every command resolves it."""
    root = scn.make_root(tmp_path)
    monkeypatch.chdir(root)
    return root, scn.install_sources(monkeypatch)


def _invoke(*args):
    return CliRunner().invoke(main, list(args))


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _stub_screen(root, ws, *, stage, size, decide):
    """Prepare, import and status run through the CLI. The two screener outputs and their dispatch evidence
    (job records) are written directly, as in the t6 scenario: no agent is launched."""
    args = ["sr", "screen", "prepare", scn.SLUG, "--stage", stage, "--as-agent", "--json"]
    if size is not None:
        args += ["--size", str(size)]
    batch = _json(_invoke(*args))["batch"]
    doc = scn.batch_doc(ws, stage, batch)
    stamp = datetime.fromisoformat(doc["prepared_at"]) + timedelta(seconds=1)
    proj = Project(root)
    for agent in scn.AGENTS:
        rows = []
        for rid in doc["record_ids"]:
            decision, reason = decide(agent, rid)
            rows.append({"record_id": rid, "decision": decision, "reason": reason})
        out = ws / "sr" / "screening" / stage / batch / f"{agent}.json"
        out.write_text(json.dumps({"format": 1, "screener": agent, "batch": batch, "decisions": rows},
                                  ensure_ascii=False), encoding="utf-8")
        scn.fake_job(proj, ws, agent, stamp.isoformat())
    _json(_invoke("sr", "screen", "import", scn.SLUG, "--stage", stage, "--batch", batch, "--as-agent", "--json"))


def _adjudicate(stage, answers):
    """`sr conflicts` writes the blinded list and opens the gate. The human decides each record through
    `sr adjudicate`, then answers the gate. Both are human acts: the test stands in for the user."""
    gid = _json(_invoke("sr", "conflicts", scn.SLUG, "--stage", stage, "--as-agent", "--json"))["gate"]
    for rid, (decision, reason) in answers.items():
        args = ["sr", "adjudicate", scn.SLUG, gid, rid, "--decision", decision,
                "--rationale", f"synthetic rationale for {rid}", "--json"]
        if reason:
            args += ["--reason", reason]
        _json(_invoke(*args))
    answered = _invoke("gate", "answer", scn.SLUG, gid, "resolved")
    assert answered.exit_code == 0, answered.output


def test_the_documented_loop_runs_end_to_end_on_the_stub_flow(review_root):
    """The skill's coordinator steps in its order, through the CLI only, on the t6 stub flow. The plan 5.12
    numbers come from the commands. Stubbed: the screeners' outputs and dispatch evidence, and the reports the
    user places under sr/reports/. Synthetic data only; no network, no agent."""
    root, box = review_root
    ws = root / "workspace" / scn.SLUG
    assert _invoke("design", "check", scn.SLUG).exit_code == 0
    assert _invoke("sr", "protocol", "show", scn.SLUG).exit_code == 1  # refused: not locked yet
    gate = _json(_invoke("design", "lock", scn.SLUG, "--json"))
    answered = _invoke("gate", "answer", scn.SLUG, gate["id"], "approve")
    assert answered.exit_code == 0, answered.output
    assert _invoke("sr", "protocol", "show", scn.SLUG).exit_code == 0

    box.answers = scn.ROUND_1
    first = _json(_invoke("sr", "search", scn.SLUG, "--json"))
    assert (first["identified"], first["duplicates"], first["new_records"]) == (60, 12, 48)
    assert _invoke("sr", "records", scn.SLUG).exit_code == 0

    _stub_screen(root, ws, stage=scn.TA, size=48, decide=scn.decide_title_abstract)
    assert _invoke("sr", "screen", "status", scn.SLUG).exit_code == 0
    _adjudicate(scn.TA, scn.TA_ANSWERS)

    reports = ws / "sr" / "reports"  # the user's step: 13 of the 14 title-abstract includes have a report
    reports.mkdir(parents=True)
    for n in range(2, 11):
        (reports / f"R{n:05d}.pdf").write_bytes(b"synthetic report")
    for rid in ("R00041", "R00043", "R00045", "R00047"):
        (reports / f"{rid}.pdf").write_bytes(b"synthetic report")
    _stub_screen(root, ws, stage=scn.FT, size=None, decide=scn.decide_full_text)
    _adjudicate(scn.FT, scn.FT_ANSWERS)

    assert _invoke("sr", "verify", scn.SLUG).exit_code == 0
    flow = _json(_invoke("sr", "prisma", scn.SLUG, "--write", "--json"))["flow"]
    assert (ws / "sr" / "prisma" / "flow.json").is_file()
    for key in FLOW_KEYS:
        assert flow[key] == GOLD["round1"][key], key
    assert flow["identified_by_source"] == GOLD["round1"]["identified_by_source"]

    box.answers = scn.ROUND_2  # the living update: a second search round, then the cumulative flow
    second = _json(_invoke("sr", "search", scn.SLUG, "--json"))
    assert second["new_records"] == 3
    flow2 = _json(_invoke("sr", "prisma", scn.SLUG, "--json"))["flow"]
    for key in ROUND2_KEYS:
        assert flow2[key] == GOLD["round2"][key], key
    assert _invoke("sr", "status", scn.SLUG).exit_code == 0
