"""M-A T4 (A-5): a dispatch for a prompt inside a run must name its role with --role."""
import subprocess
import sys
from pathlib import Path

import pytest

from scieflow.core import agent_run, service
from scieflow.core.agent_run import DispatchError, prepare
from scieflow.core.project import Project
from scieflow.core.service import ServiceError

STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
AGENT_RUN = [sys.executable, "-m", "scieflow.core.agent_run"]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f"agents:\n"
        f"  stub: {{cmd: \"{STUB}\", enabled: true, timeout_min: 1, family: claude, "
        f"session_cmd: \"{STUB}\", resume_cmd: \"{STUB} {{session}}\"}}\n"
        f"  other: {{cmd: \"{STUB}\", enabled: true, timeout_min: 1, family: codex}}\n"
        f"  idle: {{cmd: \"{STUB}\", enabled: true, timeout_min: 1, family: claude}}\n"
    )
    (tmp_path / "config" / "defaults.yml").write_text(
        "assignments:\n"
        "  loop.experiment: stub\n"
        "  research.reviewer: other\n"
    )
    ws = tmp_path / "workspace" / "run-1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: run-1\napproval: autonomous\n")
    (ws / "status.yml").write_text("slug: run-1\n")
    return tmp_path


def test_run_prompt_without_role_is_refused(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "p.md"
    prompt.write_text("hello")
    with pytest.raises(DispatchError) as exc:
        prepare(Project(root), "stub", prompt, sandbox_enabled=False)
    msg = str(exc.value)
    assert "--role" in msg
    assert "run-1" in msg
    assert "loop.experiment" in msg          # the role that assigns stub in this run


def test_refusal_says_no_role_assigns_an_unassigned_agent(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "p.md"
    prompt.write_text("hello")
    with pytest.raises(DispatchError) as exc:
        prepare(Project(root), "idle", prompt, sandbox_enabled=False)
    assert "--role" in str(exc.value)
    assert "none" in str(exc.value)


def test_run_prompt_with_assigned_role_dispatches(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "p.md"
    prompt.write_text("hello")
    d = prepare(Project(root), "stub", prompt, role="loop.experiment", sandbox_enabled=False)
    assert d.agent == "stub"
    assert d.run_dir == root / "workspace" / "run-1"


def test_run_prompt_with_wrong_role_is_refused_as_before(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "p.md"
    prompt.write_text("hello")
    with pytest.raises(DispatchError) as exc:
        prepare(Project(root), "stub", prompt, role="research.reviewer", sandbox_enabled=False)
    assert "not assigned to research.reviewer" in str(exc.value)


def test_prompt_outside_a_run_needs_no_role(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "p.md"
    prompt.write_text("hello")
    d = prepare(Project(root), "stub", prompt, sandbox_enabled=False)
    assert d.run_dir is None


def test_conversational_turn_needs_no_role(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "turn.md"
    prompt.write_text("hello")
    d = prepare(Project(root), "stub", prompt, sandbox_enabled=False, conversational=True)
    assert d.agent == "stub"


def test_service_dispatch_without_role_is_refused_before_launch(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "run-1" / "logs" / "p.md"
    prompt.write_text("hello")
    transcript = root / "workspace" / "run-1" / "logs" / "t.md"
    with pytest.raises(ServiceError) as exc:
        service.dispatch_agent(Project(root), "stub", prompt, transcript)
    assert "--role" in str(exc.value)
    assert not transcript.exists()
    assert not (root / "workspace" / "run-1" / "jobs").exists()


def test_cli_refuses_a_run_prompt_without_role_before_any_launch(tmp_path):
    root = _repo(tmp_path)
    ws = root / "workspace" / "run-1"
    prompt = ws / "logs" / "p.md"
    prompt.write_text("output: x.md\nkind: hypothesis\n")
    transcript = ws / "logs" / "t.md"
    proc = subprocess.run(
        [*AGENT_RUN, "--no-sandbox", "stub", str(prompt), str(transcript)],
        capture_output=True, text=True, cwd=root,
    )
    assert proc.returncode == 1, proc.stderr
    assert "--role" in proc.stderr
    assert not (ws / "x.md").exists()
    assert not transcript.exists()
    assert not (ws / "jobs").exists()


def test_cli_accepts_role_for_a_run_prompt(tmp_path):
    root = _repo(tmp_path)
    ws = root / "workspace" / "run-1"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'h.md'}\nkind: hypothesis\n")
    transcript = ws / "logs" / "t.md"
    proc = subprocess.run(
        [*AGENT_RUN, "--no-sandbox", "--role", "loop.experiment", "stub", str(prompt), str(transcript)],
        capture_output=True, text=True, cwd=root,
    )
    assert proc.returncode == 0, proc.stderr
    assert agent_run.owning_run_workspace(prompt) == ws
