import sys
import time
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n'
        '  sleepy: {cmd: "sleep 300", enabled: true, timeout_min: 5}\n')
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    # A real run always carries config.yml beside status.yml (run/init.py).
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path)


def test_list_and_detail(project):
    assert [r["slug"] for r in service.list_runs(project)] == ["r1"]
    detail = service.run_detail(project, "r1")
    assert detail["status"]["run"] == "r1"
    assert detail["gates"] == [] and detail["jobs"] == [] and detail["events"] == []


def test_dispatch_blocking_records_a_job(project):
    ws = project.run_dir("r1")
    out = ws / "iterations" / "h.md"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert job["state"] == "done" and out.exists()
    assert (ws / "logs" / "t.md").exists()
    assert service.run_detail(project, "r1")["jobs"][0]["id"] == job["id"]


def test_dispatch_detached_then_cancel(project):
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "t.md", detach=True)
    assert job["state"] == "running"
    cancelled = service.cancel_job(project, job["id"])
    assert cancelled["state"] == "cancelled"


def test_gates_across_runs(project):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    g = gates.open_gate(project, ws, "question", "Which dataset?", options=["A", "B"])
    assert [x["id"] for x in service.open_gates(project)] == [g["id"]]
    assert service.open_gates(project)[0]["slug"] == "r1"
    service.answer_gate(project, "r1", g["id"], "A")
    assert service.open_gates(project) == []


def test_unknown_run_is_a_service_error(project):
    with pytest.raises(service.ServiceError):
        service.run_detail(project, "nope")


def test_dispatch_through_the_service_is_sandboxed(project, monkeypatch):
    """The web app must inherit the boundary without knowing it exists."""
    seen = {}
    real_start = service.jobs.start

    def spy(project_, argv, **kw):
        seen.update(kw)
        return real_start(project_, argv, **kw)

    monkeypatch.setattr(service.jobs, "start", spy)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert seen["sandbox_writable"] is not None
    assert ws in seen["sandbox_writable"]


def test_service_dispatch_refuses_without_a_sandbox(project, monkeypatch):
    from scieflow.core import sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError, match="bubblewrap"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")


def test_service_refusal_leaves_a_job_refused_event_on_the_timeline(project, monkeypatch):
    """agent_run.main() emits job.refused on this path; the service must too,
    or a refusal from the web app leaves nothing on the run's history."""
    from scieflow.core import events, sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    refused = [e for e in events.read(ws) if e["type"] == "job.refused"]
    assert len(refused) == 1
    assert refused[0]["data"]["reason"] == "sandbox"


def test_service_escape_hatch_leaves_a_sandbox_disabled_event(project):
    """agent_run.main() emits sandbox.disabled on this path; the service must
    too, so an unsandboxed dispatch from the web app is visible in the run's
    history and not only via the per-job marker on the page."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    (project.root / "config" / "sandbox.yml").write_text(
        "unsandboxed_runs:\n  - slug: r1\n    reason: a human decided\n")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert job["state"] == "done"
    disabled = [e for e in events.read(ws) if e["type"] == "sandbox.disabled"]
    assert len(disabled) == 1
    assert disabled[0]["data"]["why"] == "config/sandbox.yml unsandboxed_runs"


def test_service_ignores_a_run_config_that_tries_to_disable_the_sandbox(project):
    """The same escape as on the CLI path, through the web app: an agent that
    appends `sandbox: off` to its own run config must not get an unconfined
    dispatch out of the service layer either."""
    ws = project.run_dir("r1")
    config_path = ws / "config.yml"
    config_path.write_text(config_path.read_text() + "sandbox: off\n")
    out = ws / "out.md"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    with pytest.raises(service.ServiceError, match="config/sandbox.yml"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert not out.exists()


def test_normal_sandboxed_dispatch_leaves_neither_event(project):
    """The new emissions must not fire on the happy path."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert job["state"] == "done"
    types = {e["type"] for e in events.read(ws)}
    assert "job.refused" not in types
    assert "sandbox.disabled" not in types
