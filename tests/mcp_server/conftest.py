"""Fixture for the M-I t5 run tools: a synthetic project with a stub agent and three runs.

r1      a dispatched stub job, a conversation turn, an open campaign gate, a charter version,
        a curation block, a note event, and an open charter-adoption gate (no data.yml: L0).
secret  declared L2 in data.yml.
l1      declared L1, no external-sharing gate, and an open charter-adoption gate whose proposal
        file holds PROPOSAL_MARKER.

All values are synthetic. No test calls a real service or the network.
"""

import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import events, gates, sandbox, service
from scieflow.core.data import levels
from scieflow.core.project import Project
from scieflow.core.run import curation, status

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
PROPOSAL_MARKER = "SYNTHETIC-PROPOSAL-MARKER-7f3a adopt the catalyst plan"
CHARTER_TEXT = "Goal: characterise the stub catalyst before scaling."


def _credentials(root: Path) -> None:
    token = sandbox.credentials_dir() / "fixture.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("fake-token-fixture\n")
    token.chmod(0o600)
    entry = {"mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN", "file": "fixture.token",
             "reason": "fake fixture token"}
    (root / "config" / "sandbox.yml").write_text(
        yaml.safe_dump({"credentials": {"stub": dict(entry)}}))


def make_run(project: Project, slug: str) -> Path:
    ws = project.workspace_root / slug
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    (ws / "config.yml").write_text(yaml.safe_dump({
        "slug": slug, "approval": "autonomous", "assignments": {"loop.experiment": ["stub"]}}))
    status.write_status(ws, status.new_status(slug, "autonomous"))
    return ws


def declare(ws: Path, level: str, path: str) -> None:
    entry = {"path": path, "level": level, "kind": "tabular", "reason": "synthetic",
             "declared_by": "human", "declared_at": "2026-10-26T10:00:00Z"}
    levels.modify(ws, lambda df: df.levels.append(entry))


@pytest.fixture
def mcp_project(tmp_path):
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n')
    (root / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    _credentials(root)
    (root / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (root / "schemas" / f"{name}.yml").write_text((ROOT / "schemas" / f"{name}.yml").read_text())
    project = Project(root)

    r1 = make_run(project, "r1")
    prompt = r1 / "logs" / "prompt.md"
    out = r1 / "iterations" / "1" / "results-summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(f"output: {out}\nkind: results-summary\n")
    service.dispatch_agent(project, "stub", prompt, r1 / "logs" / "t.md", role="loop.experiment")
    service.set_conversation_agent(project, "r1", "stub")
    service.say(project, "r1", "hello from the fixture")
    service.set_charter(project, "r1", CHARTER_TEXT, note="fixture")
    curation.add_own(r1, "Stub curation block.")
    events.emit(r1, "note.fixture", "system", message="fixture note")
    gates.open_gate(project, r1, "campaign-approval", "Run the six-run sweep?",
                    options=["approve", "reject"])

    make_run(project, "secret")
    declare(project.run_dir("secret"), "L2", "inputs/patients.csv")

    l1 = make_run(project, "l1")
    declare(l1, "L1", "inputs/summary.csv")
    proposal = l1 / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(f"Goal: {PROPOSAL_MARKER}.\n")
    gates.open_gate(project, l1, "charter-adoption", "Adopt this plan as the run's charter?",
                    options=["adopt", "decline"], files=[proposal])
    return project
