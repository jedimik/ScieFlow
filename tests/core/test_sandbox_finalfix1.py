"""Sandbox allow-list final fix round 1 (coordinator t_363a39ff, brief finalfix1).

Code findings closed here, test-first:
- finding 2 (MINOR): `_launcher` does not collapse `link/..` lexically; the grant check sees the
  unnormalised launcher directory and its realpath.
- finding 3 (MINOR): malformed policy values (a 5000-char readable path, a NUL writable path, a
  NUL credential file) raise SandboxError, never a raw OSError/ValueError; the CLI exits 77 and
  records job.refused.
- finding 5 (NIT): dispatch_environment's `writable` is a required keyword argument.

Every path here is fake and lives in a tmp dir; no real CLI, credential or home file is read.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, events, sandbox
from scieflow.core.project import Project
from scieflow.core.run import budget, status


# --- helpers -----------------------------------------------------------------

ROLE = "loop.experiment"   # M-A A-5: a dispatch inside a run names its role


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake-uv\n")
    path.chmod(0o755)
    return path


def _repo(tmp_path: Path, policy: dict) -> Project:
    """A project with one agent, run r1, a fake home-free policy and a budget."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump({"agents": {
        "tracer": {"cmd": "/bin/sh -c {prompt}", "enabled": True, "timeout_min": 1}}}))
    (tmp_path / "config" / "sandbox.yml").write_text(yaml.safe_dump(policy))
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "status.yml").write_text(
        (Path(sandbox.__file__).resolve().parents[3] / "schemas" / "status.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nassignments:\n  loop.experiment: [stub, alpha, beta, envopt, stub2, sleepy, sleepy_turn, stub_disabled, tracer, envcli, claude, codex, probe, outcome, writer]\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    return Project(tmp_path)


def _event_types(run: Path) -> list[str]:
    return [e["type"] for e in events.read(run)]


# --- finding 2: link/.. launcher directory -----------------------------------

def test_finalfix1_launcher_dir_keeps_dotdot_and_link_into_run_is_refused(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    run = root / "run"
    (run / "sub" / "deep").mkdir(parents=True)
    _executable(run / "sub" / "bin" / "uv")
    (root / "link").symlink_to(run / "sub" / "deep")
    # T2d: the PATH entry link/../bin reaches run/sub/bin/uv, which lies inside the run.
    monkeypatch.setenv("PATH", str(root / "link" / ".." / "bin"))

    launcher_dir, _hops, _final = sandbox._launcher("uv")

    assert launcher_dir == root / "link" / ".." / "bin"
    layout = sandbox.Layout(launcher_dirs=(launcher_dir,))
    with pytest.raises(sandbox.SandboxError, match="writable grant") as exc:
        sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
    assert str(run) in str(exc.value)


# --- finding 3: malformed policy values are SandboxError ---------------------

def test_finalfix1_long_readable_path_is_a_sandbox_error(tmp_path):
    policy = {"readable": [{"path": "a" * 5000, "reason": "fuzz: over-long path"}]}
    with pytest.raises(sandbox.SandboxError, match="readable"):
        sandbox._readable_of(policy, tmp_path, tmp_path / "config" / "sandbox.yml")


def test_finalfix1_nul_writable_path_is_a_sandbox_error(tmp_path):
    policy = {"writable": [{"path": "a\0b", "reason": "fuzz: NUL in path"}]}
    with pytest.raises(sandbox.SandboxError, match="writable"):
        sandbox._allowlist_of(policy, tmp_path, tmp_path / "config" / "sandbox.yml")


def test_finalfix1_nul_credential_file_is_a_sandbox_error(tmp_path):
    policy = {"credentials": {"tracer": {
        "mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN", "file": "a\0b",
        "reason": "fuzz: NUL in token path"}}}
    cred = sandbox.credentials_of(Project(tmp_path), policy=policy, check_sources=False)[0]
    with pytest.raises(sandbox.SandboxError, match="file"):
        sandbox.read_token(cred)


def test_finalfix1_cli_nul_writable_path_exits_77_and_records_refusal(
        tmp_path, tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(home))
    project = _repo(tmp_path, {"writable": [{"path": "a\0b", "reason": "fuzz: NUL in path"}]})
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in _event_types(run)
    assert "writable" in (run / "logs" / "t.md").read_text()


# --- finding 5: writable is a required keyword -------------------------------

def test_finalfix1_dispatch_environment_requires_writable(tmp_path):
    run = tmp_path / "run"
    with pytest.raises(TypeError):
        sandbox.dispatch_environment(run, layout=sandbox.Layout(), env={})


# --- finalfix2 finding 1: doctor_lines on a NUL credential file ---------------

def test_finalfix2_doctor_lines_nul_credential_file_is_unreadable_not_raw(tmp_path):
    project = _repo(tmp_path, {"credentials": {"tracer": {
        "mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN", "file": "a\0b",
        "reason": "fuzz: NUL in token path"}}})
    lines = sandbox.doctor_lines(project)
    assert len(lines) == 1
    assert lines[0].startswith("tracer: env unreadable")


# --- finalfix2 finding 3: a `..` spelling is not a system dir ------------------

def test_finalfix2_dotdot_launcher_spelling_outside_grants_joins_path(tmp_path):
    root = tmp_path.resolve()
    run = root / "run"
    run.mkdir()
    outside = root / "bin"
    outside.mkdir()
    spelled = "/usr/bin/../.." + str(outside)
    layout = sandbox.Layout(launcher_dirs=(Path(spelled),))
    env = sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
    assert env["PATH"].split(":")[0] == spelled


def test_finalfix2_dotdot_launcher_spelling_into_run_is_still_refused(tmp_path):
    root = tmp_path.resolve()
    run = root / "run"
    (run / "bin").mkdir(parents=True)
    spelled = "/usr/bin/../.." + str(run / "bin")
    layout = sandbox.Layout(launcher_dirs=(Path(spelled),))
    with pytest.raises(sandbox.SandboxError, match="writable grant"):
        sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
