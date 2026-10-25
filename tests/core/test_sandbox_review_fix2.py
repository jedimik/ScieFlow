"""S2-S5 code/security review, fix round 2 (coordinator t_363a39ff, re-review 1 findings N1, N2).

N1: no launcher directory inside a writable grant joins the sandbox PATH; the check is shared by
    the CLI path (agent_run.prepare) and the turn path (agent_run.resolve_turn) through
    dispatch_environment.
N2: the host marker is placed in EVERY qualifying home candidate, and verify refuses if any of
    them is visible inside the sandbox.

Every path here is fake and lives in a tmp dir; no real credential, CLI or home file is read.
"""

import os
import shutil
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, events, sandbox, service
from scieflow.core.project import Project
from scieflow.core.run import budget, status

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
REAL_WRAP = sandbox.wrap


# --- helpers -----------------------------------------------------------------

ROLE = "loop.experiment"   # M-A A-5: a dispatch inside a run names its role


def _repo(tmp_path: Path, agent: str = "tracer", cmd: str = "/bin/sh -c {prompt}") -> Project:
    """A project with one agent, run r1, a fake env credential, and a budget."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump({"agents": {
        agent: {"cmd": cmd, "enabled": True, "timeout_min": 1}}}))
    token = sandbox.credentials_dir() / "fixture.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("fake-token-fixture\n")
    token.chmod(0o600)
    (tmp_path / "config" / "sandbox.yml").write_text(yaml.safe_dump({"credentials": {agent: {
        "mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN", "file": "fixture.token",
        "reason": "fake fixture token"}}}))
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "status.yml").write_text(
        (Path(sandbox.__file__).resolve().parents[3] / "schemas" / "status.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nassignments:\n  loop.experiment: [stub, alpha, beta, envopt, stub2, sleepy, sleepy_turn, stub_disabled, tracer, envcli, claude, codex, probe, outcome, writer]\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    return Project(tmp_path)


def _fake_home(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(home))
    return home


def _event_types(run: Path) -> list[str]:
    return [e["type"] for e in events.read(run)]


def _leaky_at_front(extra):
    """A wrap() that exposes `extra` as an early bind; later grants still apply on top."""
    def wrapped(argv, **kw):
        out = REAL_WRAP(argv, **kw)
        return out[:1] + extra + out[1:]
    return wrapped


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake-uv\n")
    path.chmod(0o755)
    return path


# --- N1: launcher directories inside a writable grant (pure) -----------------

def test_n1_a_launcher_dir_inside_the_dispatch_run_is_refused(tmp_path):
    run = tmp_path / "run"
    layout = sandbox.Layout(launcher_dirs=(run / "bin",))
    with pytest.raises(sandbox.SandboxError, match="writable grant") as exc:
        sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
    assert str(run / "bin") in str(exc.value)
    assert str(run) in str(exc.value)


def test_n1_a_launcher_dir_inside_any_writable_grant_is_refused(tmp_path):
    run = tmp_path / "run"
    grant = tmp_path / "allowed"
    layout = sandbox.Layout(launcher_dirs=(grant / "bin",))
    with pytest.raises(sandbox.SandboxError, match="writable grant") as exc:
        sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run, grant])
    assert str(grant / "bin") in str(exc.value)
    assert str(grant) in str(exc.value)


def test_n1_a_launcher_dir_reached_through_a_symlink_into_a_grant_is_refused(tmp_path):
    run = tmp_path / "run"
    (run / "bin").mkdir(parents=True)
    link = tmp_path / "local-bin"
    link.symlink_to(run / "bin")
    layout = sandbox.Layout(launcher_dirs=(link,))
    with pytest.raises(sandbox.SandboxError, match="writable grant"):
        sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])


def test_n1_a_launcher_outside_every_grant_still_joins_path(tmp_path):
    run = tmp_path / "run"
    home_bin = tmp_path / "home" / ".local" / "bin"
    home_bin.mkdir(parents=True)
    layout = sandbox.Layout(launcher_dirs=(home_bin,))
    env = sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
    assert env["PATH"] == f"{home_bin}:/usr/local/bin:/usr/bin:/bin"


def test_n1_a_system_launcher_dir_adds_nothing_even_with_grants_given(tmp_path):
    run = tmp_path / "run"
    layout = sandbox.Layout(launcher_dirs=(Path("/usr/bin"),))
    env = sandbox.dispatch_environment(run, layout=layout, env={}, writable=[run])
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"


# --- N1: the CLI and turn paths refuse a uv launcher inside the run ----------

def test_n1_cli_uv_resolving_into_the_run_is_refused_and_recorded(
        tmp_path, tmp_path_factory, monkeypatch):
    _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    run = project.run_dir("r1")
    uv = _executable(run / "bin" / "uv")
    monkeypatch.setattr(sandbox, "_launcher",
                        lambda name: (uv.parent, [], uv) if name == "uv" else (None, [], None))
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in _event_types(run)
    transcript = (run / "logs" / "t.md").read_text()
    assert "writable grant" in transcript
    assert str(uv.parent) in transcript


def test_n1_turn_uv_resolving_into_the_run_is_refused_and_recorded(
        k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    uv = _executable(ws / "bin" / "uv")
    monkeypatch.setattr(sandbox, "_launcher",
                        lambda name: (uv.parent, [], uv) if name == "uv" else (None, [], None))
    try:
        service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    except service.ServiceError:
        pass
    else:
        with pytest.raises(service.ServiceError, match="writable grant"):
            service.say(project, "r1", "hello")
    assert "job.refused" in _event_types(ws)


# --- N2: one host marker per qualifying home (pure) -------------------------

def test_n2_marker_location_everywhere_returns_every_qualifying_candidate(tmp_path):
    base = tmp_path / "base"
    pw = base / "pw"
    grant = pw / "grant"
    grant.mkdir(parents=True)
    got = sandbox.marker_location(outer=[grant], binds=[grant], candidates=[base, pw],
                                  everywhere=True)
    assert got == [base, pw]


def test_n2_marker_location_everywhere_skips_every_candidate_under_a_bind(tmp_path):
    base = tmp_path / "base"
    pw = base / "pw"
    grant = pw / "grant"
    grant.mkdir(parents=True)
    # base is a bind and an ancestor of pw, so neither qualifies (D-S5-2: outside every bind)
    got = sandbox.marker_location(outer=[grant], binds=[grant, base], candidates=[base, pw],
                                  everywhere=True)
    assert got == []


def test_n2_marker_location_everywhere_is_empty_when_nothing_qualifies(tmp_path):
    grant = tmp_path / "grant"
    grant.mkdir()
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    assert sandbox.marker_location(outer=[grant], binds=[grant], candidates=[unrelated],
                                   everywhere=True) == []


# --- N2: a leak of the passwd home is detected when HOME differs (real bwrap) ----

@needs_bwrap
def test_n2_a_leak_of_only_the_passwd_home_is_refused_when_home_env_differs(
        tmp_path, monkeypatch):
    base = tmp_path / "base"          # the HOME env candidate
    pw = base / "pw"                  # the passwd candidate, which the wrap leaks
    grant = pw / "grant"
    grant.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "_homes", lambda: [base, pw])
    monkeypatch.setattr(sandbox, "wrap", _leaky_at_front(["--ro-bind", str(pw), str(pw)]))
    with pytest.raises(sandbox.SandboxUnavailable, match="visible inside the sandbox") as exc:
        sandbox.verify([grant], cwd=grant)
    assert "marker:1" in str(exc.value)
    assert not list(base.glob(".scieflow-verify-marker-*"))
    assert not list(pw.glob(".scieflow-verify-marker-*"))


@needs_bwrap
def test_n2_without_a_leak_every_home_marker_is_absent_and_removed(tmp_path, monkeypatch):
    base = tmp_path / "base"
    pw = base / "pw"
    grant = pw / "grant"
    grant.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "_homes", lambda: [base, pw])
    sandbox.verify([grant], cwd=grant)
    assert not list(base.glob(".scieflow-verify-marker-*"))
    assert not list(pw.glob(".scieflow-verify-marker-*"))
