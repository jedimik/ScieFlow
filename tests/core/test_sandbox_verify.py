"""S5 of the allow-list sandbox: the confinement proof in verify() and doctor.

Design 2026-10-17 (D8) and plan r2 section 3 S5 with the section 5 amendments (A11, A15).
Real-bwrap tests carry needs_bwrap. Leaky wraps are built by inserting raw bwrap arguments
before the `--` separator of the real wrap() output. Every token and home here is fake and
lives in a tmp dir; no real credential, CLI or home file is read.
Also holds the S4 residual tests (coordinator item 6): a foreign-owner token, the turn-path
token scan, and the credential resolution of a child process.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from scieflow.core import agent_run, events, sandbox, service
from scieflow.core.project import Project
from scieflow.core.run import budget, status

ROLE = "loop.experiment"   # M-A A-5: a dispatch inside a run names its role


W = Path(sandbox.__file__).resolve().parents[3]
HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
REAL_WRAP = sandbox.wrap
ROOT = Path(__file__).resolve().parents[2]


# --- helpers -----------------------------------------------------------------

def _fake_home(tmp_path_factory, monkeypatch, *, secrets: bool = True) -> Path:
    """A fake HOME, a sibling of any project (A6), with the secret dirs the probe must not see."""
    home = tmp_path_factory.mktemp("home").resolve()
    if secrets:
        (home / ".ssh").mkdir()
        (home / ".ssh" / "id").write_text("fake-ssh-key")
        (home / ".hermes").mkdir()
        (home / ".hermes" / ".env").write_text("fake-hermes")
    monkeypatch.setenv("HOME", str(home))
    return home


def _leaky(extra_for):
    """A wrap() that inserts raw bwrap arguments before the `--` separator (a leaky layout)."""
    def wrapped(argv, **kw):
        out = REAL_WRAP(argv, **kw)
        cut = out.index("--")
        return out[:cut] + extra_for() + out[cut:]
    return wrapped


LEAKS = {
    "root": lambda home: ["--ro-bind", "/", "/"],
    "fake-ssh": lambda home: ["--ro-bind", str(home / ".ssh"), str(home / ".ssh")],
    "run-bus": lambda home: ["--bind", "/run", "/run"],
}

# S25fix1 (finding 3b): the run-bus leak can only be detected where a D-Bus socket exists on the
# host, so that one case is skipped (not weakened) on a host with neither socket.
RUN_BUS_SOCKETS = (Path(f"/run/user/{os.getuid()}/bus"), Path("/run/dbus/system_bus_socket"))
LEAK_PARAMS = [
    pytest.param(name, marks=pytest.mark.skipif(
        not any(s.exists() for s in RUN_BUS_SOCKETS),
        reason="the run-bus leak needs /run/user/UID/bus or /run/dbus/system_bus_socket on the host"))
    if name == "run-bus" else name
    for name in sorted(LEAKS)
]


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
    (tmp_path / "schemas" / "status.yml").write_text((W / "schemas" / "status.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nassignments:\n  loop.experiment: [stub, stub2, sleepy, sleepy_turn, stub_disabled, tracer, envcli, claude, codex, probe, outcome, writer]\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    return Project(tmp_path)


def _event_types(run: Path) -> list[str]:
    return [e["type"] for e in events.read(run)]


# --- A. the probe result format (pure) ---------------------------------------

EXPECTED = ("ran", "ssh:0", "hermes:0", "run-user-bus", "system-bus", "marker")


def _good_results(ids=EXPECTED) -> str:
    lines = [f"{name}\tyes" if name == "ran" else f"{name}\tabsent" for name in ids]
    return "\n".join(lines) + "\n"


def test_s5_probe_results_with_every_expected_id_absent_are_accepted():
    sandbox.read_probe_results(_good_results(), EXPECTED)


@pytest.mark.parametrize("text, why", [
    ("", "empty"),
    (_good_results().replace("ran\tyes\n", ""), "no ran line"),
    (_good_results().replace("ssh:0\tabsent", "ssh:0\tpresent"), "a present secret"),
    (_good_results().replace("system-bus\tabsent", "system-bus\tmaybe"), "unknown state"),
    (_good_results() + "marker\tabsent\n", "duplicate id"),
    (_good_results().replace("marker\tabsent\n", ""), "missing id"),
    (_good_results() + "extra-id\tabsent\n", "unknown id"),
    (_good_results().replace("\t", " ", 1), "no tab separator"),
    (_good_results().replace("ran\tyes", "ran\tno"), "ran is not yes"),
])
def test_s5_unparseable_or_present_probe_results_are_refused(text, why):
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.read_probe_results(text, EXPECTED)


def test_s5_probe_results_refuse_an_absent_sentinel_text_of_none():
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.read_probe_results(None, EXPECTED)


# --- B. the marker location (pure, A15) --------------------------------------

def test_s5_marker_is_an_ancestor_of_the_outermost_bind_and_outside_every_bind(tmp_path):
    home = tmp_path / "home"
    project = home / "proj"
    project.mkdir(parents=True)
    got = sandbox.marker_location(outer=[project], binds=[project], candidates=[home])
    assert got == home


def test_s5_marker_never_lies_inside_a_readable_bind(tmp_path):
    parent = tmp_path / "parent"
    grant = parent / "grant"
    grant.mkdir(parents=True)
    readable = parent                     # a readable bind that contains the candidate
    got = sandbox.marker_location(outer=[grant], binds=[grant, readable], candidates=[parent])
    assert got is None


def test_s5_marker_refuses_a_candidate_that_is_not_an_ancestor(tmp_path):
    grant = tmp_path / "grant"
    grant.mkdir()
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    assert sandbox.marker_location(outer=[grant], binds=[grant], candidates=[unrelated]) is None


def test_s5_marker_refuses_a_candidate_that_is_not_writable(tmp_path):
    grant = tmp_path / "grant"
    grant.mkdir()
    assert sandbox.marker_location(outer=[grant], binds=[grant],
                                   candidates=[tmp_path / "missing"]) is None


def test_s5_verify_refuses_when_no_marker_location_qualifies(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(sandbox, "marker_location", lambda **kw: None)
    with pytest.raises(sandbox.SandboxUnavailable, match="marker"):
        sandbox.verify([tmp_path / "w"], cwd=tmp_path)


def test_s5_verify_refuses_when_the_marker_cannot_be_created(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    def broken(*args, **kwargs):
        raise OSError("read-only host")
    monkeypatch.setattr(sandbox, "create_marker", broken)
    with pytest.raises(sandbox.SandboxUnavailable, match="marker"):
        sandbox.verify([tmp_path / "w"], cwd=tmp_path)


def test_s5_verify_removes_its_marker_on_a_refused_path(monkeypatch, tmp_path):
    """Every exit path cleans up: a refused verify leaves no marker behind."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    writable = tmp_path / "w"
    writable.mkdir()
    monkeypatch.setattr(sandbox, "wrap", lambda argv, *, writable, cwd: ["/bin/false"])
    with pytest.raises(sandbox.SandboxUnavailable, match="did not run"):
        sandbox.verify([writable], cwd=writable)
    assert not list(tmp_path.glob(".scieflow-verify-marker-*"))
    assert not list((tmp_path / "home").glob(".scieflow-verify-marker-*"))


# --- C. the mask is passed as the launch passes it (F8, D-S5-1) -----------------

def test_s5_verify_passes_only_the_masks_that_exist_to_the_probe(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    grant = tmp_path / "run"
    grant.mkdir()
    present = grant / "scratch-existing"
    present.mkdir()
    absent = grant / "provenance.git"
    seen = {}

    def spy(argv, *, writable, cwd, mask=None, layout=None):
        seen["mask"] = mask
        return ["/bin/false"]
    monkeypatch.setattr(sandbox, "wrap", spy)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.verify([grant], cwd=grant, mask=[absent, present])
    assert seen["mask"] == [present]


@needs_bwrap
def test_s5_probe_never_creates_an_absent_mask_on_the_host(monkeypatch, tmp_path_factory, tmp_path):
    _fake_home(tmp_path_factory, monkeypatch)
    grant = tmp_path / "run"
    grant.mkdir()
    absent = grant / "provenance.git"
    sandbox.verify([grant], cwd=grant, mask=[absent])
    assert not absent.exists(), "the probe left a mountpoint on the host"


# --- D. the real layout passes; leaky layouts are refused (real bwrap) -------------

@needs_bwrap
def test_s5_verify_passes_on_the_real_allow_list_layout(monkeypatch, tmp_path_factory, tmp_path):
    home = _fake_home(tmp_path_factory, monkeypatch)
    run = tmp_path / "run"
    run.mkdir()
    agent_home = sandbox.agent_home(run, "probe")
    layout = sandbox.Layout(home=agent_home)
    sandbox.verify([run], cwd=run, mask=sandbox.masks_for([run], run_dir=run), layout=layout)
    assert home.is_dir()


@needs_bwrap
@pytest.mark.parametrize("leak", LEAK_PARAMS)
def test_s5_verify_refuses_a_leaky_wrap(monkeypatch, tmp_path_factory, tmp_path, leak):
    home = _fake_home(tmp_path_factory, monkeypatch)
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(sandbox, "wrap", _leaky(lambda: LEAKS[leak](home)))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.verify([run], cwd=run)


@needs_bwrap
@pytest.mark.parametrize("leak", LEAK_PARAMS)
def test_s5_a_leaky_wrap_on_the_cli_is_exit_77_and_job_refused(monkeypatch, tmp_path_factory,
                                                               tmp_path, leak):
    home = _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sandbox, "wrap", _leaky(lambda: LEAKS[leak](home)))
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in _event_types(run)


@needs_bwrap
@pytest.mark.parametrize("leak", LEAK_PARAMS)
def test_s5_a_leaky_wrap_on_the_service_path_is_service_error_and_job_refused(
        monkeypatch, tmp_path_factory, tmp_path, leak):
    home = _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.setattr(sandbox, "wrap", _leaky(lambda: LEAKS[leak](home)))
    with pytest.raises(service.ServiceError):
        service.dispatch_agent(project, "tracer", prompt, run / "logs" / "t.md", role=ROLE)
    assert "job.refused" in _event_types(run)


@needs_bwrap
@pytest.mark.parametrize("leak", LEAK_PARAMS)
def test_s5_a_leaky_wrap_on_the_turn_path_is_service_error_and_job_refused(
        monkeypatch, tmp_path_factory, k_conversational_project, leak):
    home = _fake_home(tmp_path_factory, monkeypatch)
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    monkeypatch.setattr(sandbox, "wrap", _leaky(lambda: LEAKS[leak](home)))
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert "job.refused" in _event_types(ws)


@needs_bwrap
def test_s5_the_turn_verify_receives_the_mask_the_launch_receives(
        monkeypatch, tmp_path_factory, k_conversational_project):
    _fake_home(tmp_path_factory, monkeypatch)
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    seen = []
    real_verify = sandbox.verify

    def spy(writable, cwd, *, mask=None, layout=None):
        seen.append(mask)
        return real_verify(writable, cwd, mask=mask, layout=layout)
    monkeypatch.setattr(sandbox, "verify", spy)
    service.say(project, "r1", "hello")
    protection = sandbox.resolve_turn_protection(project, run_dir=ws)
    assert seen and seen[0] is not None
    assert set(seen[0]) == set(protection.masks)


# --- E. doctor: the Python entry proves the real wrap() ----------------------------

@needs_bwrap
def test_s5_doctor_entry_proves_confinement_and_prints_ok_lines(tmp_path, tmp_path_factory,
                                                               monkeypatch):
    home = _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(W / "src"), "HOME": str(home),
           "SCIEFLOW_STATE_DIR": str(tmp_path_factory.mktemp("state"))}
    proc = subprocess.run([sys.executable, "-m", "scieflow.core.sandbox", "doctor"],
                          capture_output=True, text=True, cwd=project.root, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(line.startswith("ok") and "confine" in line for line in proc.stdout.splitlines())
    assert "fake-token-fixture" not in proc.stdout + proc.stderr


def test_s5_doctor_reports_a_failed_proof_and_exits_nonzero(tmp_path, monkeypatch, tmp_path_factory):
    _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(sandbox, "wrap", lambda argv, *, writable, cwd, **kw: ["/bin/false"])
    assert sandbox.main(["doctor"]) == 1


def test_s5_doctor_sh_no_longer_hand_builds_a_root_bind():
    text = (W / "setup" / "doctor.sh").read_text()
    assert "--ro-bind / /" not in text
    assert "-m scieflow.core.sandbox doctor" in text


def test_s5_doctor_sh_sandbox_section_calls_the_python_entry_only():
    text = (W / "setup" / "doctor.sh").read_text()
    section = text.split("== sandbox ==", 1)[1].split("Python deps:", 1)[0]
    assert "scieflow.core.sandbox doctor" in section
    assert "bwrap --ro-bind" not in section


# --- F. S4 residual (a): a foreign-owner token is refused without content ----------

def test_s4_residual_a_foreign_owner_token_is_refused_and_never_echoes_content(
        tmp_path, monkeypatch):
    value = f"fake-foreign-{uuid4().hex}"
    token = sandbox.credentials_dir() / "foreign.token"
    token.write_text(value + "\n")
    token.chmod(0o600)
    cred = sandbox.Credential(agent="claude", mode="env", var="CLAUDE_CODE_OAUTH_TOKEN",
                              file=token, source=None, reason="r")
    real_getuid = os.getuid
    monkeypatch.setattr(sandbox.os, "getuid", lambda: real_getuid() + 1)
    with pytest.raises(sandbox.SandboxError, match="not owned by this user") as exc:
        sandbox.read_token(cred)
    assert value not in str(exc.value)


def test_s4_residual_a_foreign_owner_is_exit_77_on_the_cli_and_bad_mode_in_doctor(
        tmp_path, monkeypatch, tmp_path_factory):
    _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    monkeypatch.chdir(tmp_path)
    real_getuid = os.getuid
    monkeypatch.setattr(sandbox.os, "getuid", lambda: real_getuid() + 1)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    lines = sandbox.doctor_lines(project)
    assert any("tracer:" in line and "bad-mode" in line for line in lines), lines


# --- G. S4 residual (b): the turn path writes no token, on every outcome -------------

def _fixture_turn_agent_fails(project: Project) -> None:
    """Make the k-protocol-fixture's first-turn argv invalid, so the agent process fails."""
    path = project.root / "config" / "agents.yml"
    text = path.read_text()
    assert text.count("first {prompt}") >= 2
    path.write_text(text.replace("first {prompt}", "bogus {prompt}"))


@needs_bwrap
@pytest.mark.parametrize("outcome", ["success", "failure", "refusal"])
def test_s4_residual_b_the_turn_path_never_writes_the_token(
        monkeypatch, tmp_path_factory, k_conversational_project, outcome):
    _fake_home(tmp_path_factory, monkeypatch)
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    token_file = sandbox.credentials_dir() / "fixture.token"
    value = f"fake-turn-{uuid4().hex}"
    token_file.write_text(value + "\n")
    token_file.chmod(0o600)
    if outcome == "failure":
        _fixture_turn_agent_fails(project)
    if outcome == "refusal":
        token_file.chmod(0o644)
    raised = None
    result = None
    try:
        result = service.say(project, "r1", "hello")
    except service.ServiceError as exc:
        raised = exc
    if outcome == "refusal":
        assert isinstance(raised, service.ServiceError), "a group-readable token must be refused"
    state = Path(os.environ["SCIEFLOW_STATE_DIR"])
    for path, data in _tree_bytes(ws, state).items():
        assert value.encode() not in data, f"the token was written to {path}"
    assert value not in repr(result) and value not in str(raised)


def _tree_bytes(*roots: Path) -> dict:
    out = {}
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = Path(dirpath) / name
                try:
                    out[path] = path.read_bytes()
                except OSError:
                    pass
    return out


# --- H. S4 residual (c): a credentialed child resolves its credentials under a fake HOME --

def test_s4_residual_c_a_child_resolves_credentials_only_under_its_fake_home(
        tmp_path, tmp_path_factory, monkeypatch):
    home = _fake_home(tmp_path_factory, monkeypatch, secrets=False)
    project = _repo(tmp_path)
    import pwd
    real_dir = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / "scieflow" / "credentials"
    env = {**os.environ, "PYTHONPATH": str(W / "src"), "HOME": str(home),
           "SCIEFLOW_STATE_DIR": str(tmp_path_factory.mktemp("state"))}
    proc = subprocess.run([sys.executable, "-m", "scieflow.core.sandbox", "doctor"],
                          capture_output=True, text=True, cwd=project.root, env=env, timeout=300)
    out = proc.stdout + proc.stderr
    assert str(home / ".config" / "scieflow" / "credentials") in out, out
    assert str(real_dir) not in out, "the child resolved the real credentials directory"


# --- S25fix1 closing tests (review t_d91ce2af), the verify/doctor half ---------------------------

def _leaky_at_front(extra):
    """A wrap() that exposes `extra` as an EARLY bind: later grants and masks still apply on top,
    so the exposure is exactly "this directory is visible read-only", as a real leak would be."""
    def wrapped(argv, **kw):
        out = REAL_WRAP(argv, **kw)
        return out[:1] + extra + out[1:]
    return wrapped


def test_f3b_the_doctor_proof_grant_is_never_under_an_overlaid_tmp(tmp_path_factory, monkeypatch):
    """Finding 3a's consequence: with TMPDIR=/tmp the doctor's throwaway grant must not sit in /tmp,
    or the marker would have to go there and be invisible by construction."""
    home = _fake_home(tmp_path_factory, monkeypatch)
    monkeypatch.setattr(tempfile, "tempdir", "/tmp")
    seen = {}

    def spy(writable, cwd=None, **kw):
        seen["grant"] = Path(writable[0]).resolve()

    monkeypatch.setattr(sandbox, "verify", spy)
    ok, line = sandbox.doctor_verify()
    assert ok is True, line
    assert "grant" in seen
    assert not str(seen["grant"]).startswith("/tmp/"), seen["grant"]
    assert str(seen["grant"]).startswith(str(home)), seen["grant"]


@needs_bwrap
def test_f3c_a_doctor_proof_with_a_home_leak_is_refused_when_the_marker_is_in_tmp_by_default(
        tmp_path_factory, monkeypatch):
    """The review's scenario (test_review_adv2): TMPDIR=/tmp and a wrap that exposes the whole fake
    home. The leak must be detected (the doctor refuses), not passed by a marker in /tmp."""
    home = _fake_home(tmp_path_factory, monkeypatch, secrets=False)   # only the marker can leak
    monkeypatch.setattr(tempfile, "tempdir", "/tmp")
    monkeypatch.setattr(sandbox, "_homes", lambda: [home])     # the marker's home candidate is this one
    monkeypatch.setattr(sandbox, "wrap", _leaky_at_front(["--ro-bind", str(home), str(home)]))
    ok, line = sandbox.doctor_verify()
    assert ok is False, line


def test_f11_a_hard_linked_token_is_refused_on_the_cli_with_a_named_reason(
        tmp_path, tmp_path_factory, monkeypatch):
    _fake_home(tmp_path_factory, monkeypatch)
    project = _repo(tmp_path)
    token = sandbox.credentials_dir() / "fixture.token"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.link(token, elsewhere / "alias.token")           # the same inode, another name
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in _event_types(run)
    assert "hard link" in (run / "logs" / "t.md").read_text()
