import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scieflow.core import events, gates, jobs
from scieflow.core.project import Project
from scieflow.core.run import budget, status
from scieflow.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token"
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    """A project with one run that has state, budget, events, a job and a gate."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "assignments:\n  research.outline: stub\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())

    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    project = Project(tmp_path)

    events.emit(ws, "run.created", "human", slug="r1")
    events.emit(ws, "phase.started", "agent", phase="hypothesize")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    jobs.run_blocking(project, [sys.executable, "-c", "print('hello from the job')"],
                      kind="agent", cwd=tmp_path, run_dir=ws, label="stub")
    gates.open_gate(project, ws, "question", "Which dataset?", options=["A", "B"])
    return project


@pytest.fixture
def client(project):
    with TestClient(create_app(project, TOKEN)) as signed_in:
        signed_in.get(f"/healthz?token={TOKEN}")     # exchange token for cookies
        yield signed_in
