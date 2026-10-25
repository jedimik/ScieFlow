"""S4 of the allow-list sandbox: env allow-list, per-agent credentials, never packed.

Design 2026-10-17 (D6, D7) and plan r2 section 3 S4 with the section 5 amendments.
Pure tests monkeypatch bwrap as available; real-bwrap tests carry needs_bwrap.
Every token here is a fake value in a tmp dir. No real CLI, credential or home file is read.

Deviation D-S4-1: S4 tests live in this file so the reviewed S1-S3 file stays as it was.
"""

import hashlib
import os
import pwd
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from scieflow.core import agent_run, events, jobs, sandbox, service
from scieflow.core.project import Project
from scieflow.core.run import budget, status

ROLE = "loop.experiment"   # M-A A-5: a dispatch inside a run names its role


W = Path(sandbox.__file__).resolve().parents[3]
HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
FAKE_BWRAP = "/usr/bin/bwrap"
_real_which = shutil.which

# C12, exact: the kept names the env allow-list passes through (PATH and HOME are reset below).
C12_KEPT = {"PATH", "HOME", "LANG", "TZ", "TERM", "UV_CACHE_DIR", "SCIEFLOW_STATE_DIR",
            "SCIEFLOW_PROMPT_ARGV_LIMIT", "SCIEFLOW_LEGACY_QUIET", "SCIEFLOW_MAILTO"}
SHELL_ADDED = {"PWD", "OLDPWD", "SHLVL", "_"}      # set by /bin/sh itself, not by the allow-list
HOST_SECRETS = {                                   # fake host values that must never reach an agent
    "SCITE_TOKEN": "fake-scite-7f3a9c",
    "DISCORD_BOT_TOKEN": "fake-discord-7f3a9c",
    "HERMES_API_KEY": "fake-hermes-7f3a9c",
    "SSH_AUTH_SOCK": "/tmp/fake-ssh-agent-7f3a9c.sock",
    "ANTHROPIC_API_KEY": "fake-anthropic-7f3a9c",
    "OPENAI_API_KEY": "fake-openai-7f3a9c",
    "CLAUDE_CODE_OAUTH_TOKEN": "fake-host-claude-7f3a9c",
}


@pytest.fixture
def fake_bwrap(monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(
        sandbox.shutil, "which",
        lambda name, *a, **k: FAKE_BWRAP if name == "bwrap" else _real_which(name, *a, **k))


def _at(argv, *items):
    n = len(items)
    for i in range(len(argv) - n + 1):
        if tuple(argv[i:i + n]) == items:
            return i
    raise AssertionError(f"{items!r} not in argv")


def _fixture_token(name: str = "fixture.token") -> str:
    """A fake 0600 token in the per-test credentials dir (A9). Returns its value."""
    value = f"fake-token-{uuid4().hex}"
    path = sandbox.credentials_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    os.chmod(path, 0o600)
    return value


def _fixture_value(name: str = "fixture.token") -> str:
    return (sandbox.credentials_dir() / name).read_text().strip()


def _project(tmp_path: Path, agents: dict, credentials: dict | None) -> Project:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump({"agents": agents}))
    (tmp_path / "schemas").mkdir(exist_ok=True)
    (tmp_path / "schemas" / "status.yml").write_text((W / "schemas" / "status.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nassignments:\n  loop.experiment: [stub, alpha, beta, stub2, sleepy, sleepy_turn, stub_disabled, tracer, envcli, claude, codex, probe, outcome, writer]\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    if credentials is not None:
        (tmp_path / "config" / "sandbox.yml").write_text(yaml.safe_dump({"credentials": credentials}))
    return Project(tmp_path)


def _repo(tmp_path: Path, agent: str = "tracer", cmd: str = "/bin/sh -c {prompt}", *,
          credentials: bool = True) -> Project:
    """A project with one agent, run r1, and (by default) a fake env credential for it."""
    creds = None
    if credentials:
        _fixture_token()
        creds = {agent: {"mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN",
                         "file": "fixture.token", "reason": "fake fixture token"}}
    return _project(tmp_path, {agent: {"cmd": cmd, "enabled": True, "timeout_min": 1}}, creds)


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def _fake_home(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(home))
    return home


def _names(path: Path) -> set[str]:
    return {item.split(b"=", 1)[0].decode() for item in path.read_bytes().split(b"\0") if item}


def _tree_bytes(*roots: Path) -> dict[Path, bytes]:
    out: dict[Path, bytes] = {}
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


# --- suite-wide credential isolation (A9) ------------------------------------

def test_s4_suite_points_credentials_dir_at_a_tmp_dir():
    real = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / "scieflow" / "credentials"
    got = sandbox.credentials_dir().resolve()
    assert got.is_dir()
    assert real.resolve() not in [got, *got.parents], f"credentials_dir() resolves under {real}"


# --- credentials: grammar (C10, A8) ------------------------------------------

def test_s4_absent_credentials_key_is_empty(tmp_path):
    project = _project(tmp_path, {}, None)
    (tmp_path / "config" / "sandbox.yml").write_text("writable: []\n")
    assert sandbox.credentials_of(project) == ()


def test_s4_env_and_bind_entries_parse_with_defaults(tmp_path, tmp_path_factory, monkeypatch):
    home = _fake_home(tmp_path_factory, monkeypatch)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("fake-login")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sandbox.yml").write_text(
        "credentials:\n"
        "  claude:\n"
        "    mode: env\n"
        "    var: CLAUDE_CODE_OAUTH_TOKEN\n"
        "    reason: the claude login\n"
        "  codex:\n"
        f"    mode: bind\n    source: {home}/.codex/auth.json\n    target: .codex/auth.json\n"
        "    writable: true\n    reason: the codex login file\n")
    project = Project(tmp_path)
    creds = {c.agent: c for c in sandbox.credentials_of(project)}
    assert creds["claude"].mode == "env"
    assert creds["claude"].var == "CLAUDE_CODE_OAUTH_TOKEN"
    assert creds["claude"].file == sandbox.credentials_dir() / "claude.token"
    assert creds["codex"].mode == "bind"
    assert creds["codex"].source == (home / ".codex" / "auth.json").resolve()
    assert creds["codex"].target == ".codex/auth.json"
    assert creds["codex"].writable is True
    assert "fake-login" not in repr(creds)


S4_BAD_ENTRIES = [
    "mode: none, reason: stub agents need nothing",            # HD1: there is no mode none
    "mode: env, var: X_TOKEN",                                  # no reason
    "mode: env, reason: no var",                                # env without var
    "mode: env, var: lower_case, reason: r",                    # var grammar
    "mode: env, var: PATH, reason: r",                          # reserved
    "mode: env, var: HOME, reason: r",
    "mode: env, var: UV_CACHE_DIR, reason: r",
    "mode: env, var: LD_PRELOAD, reason: r",
    "mode: env, var: LANG, reason: r",                          # a C12 kept name
    "mode: env, var: X_TOKEN, reason: r, extra: 1",             # unknown key
    "mode: bind, reason: no source",
    "mode: bind, source: {home}/does-not-exist, target: a, reason: r",
    "mode: bind, source: {home}/.ssh, target: a, reason: r",   # secret directory
    "mode: bind, source: {home}/.codex, target: a, reason: r",  # the store directory itself
    "mode: bind, source: {home}, target: a, reason: r",         # the home
    "mode: bind, source: {src}, target: ../escape, reason: r",
    "mode: bind, source: {src}, target: /absolute, reason: r",
]


@pytest.mark.parametrize("entry", S4_BAD_ENTRIES)
def test_s4_malformed_or_denied_credentials_are_refused(tmp_path, tmp_path_factory, monkeypatch, entry):
    home = _fake_home(tmp_path_factory, monkeypatch)
    (home / ".ssh").mkdir()
    (home / ".codex").mkdir()
    src = tmp_path / "src.json"
    src.write_text("x")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sandbox.yml").write_text(
        "credentials:\n  claude: {" + entry.format(home=home, src=src) + "}\n")
    with pytest.raises(sandbox.SandboxError):
        sandbox.credentials_of(Project(tmp_path))


def test_s4_a_credentials_mapping_must_be_a_mapping(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sandbox.yml").write_text("credentials: [claude]\n")
    with pytest.raises(sandbox.SandboxError):
        sandbox.credentials_of(Project(tmp_path))


# --- the token file (C10, A8, the 77 checks) ---------------------------------

def _token_cred(tmp_path: Path, path: Path) -> "sandbox.Credential":
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "sandbox.yml").write_text(yaml.safe_dump({"credentials": {"claude": {
        "mode": "env", "var": "X_TOKEN", "file": str(path), "reason": "fake"}}}))
    (cred,) = sandbox.credentials_of(Project(tmp_path))
    return cred


def _write_token(path: Path, data: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def test_s4_a_0600_token_file_is_read(tmp_path):
    path = _write_token(tmp_path / "t" / "claude.token", b"fake-token-ok\n")
    assert sandbox.read_token(_token_cred(tmp_path, path), forbidden=()) == "fake-token-ok"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660, 0o666])
def test_s4_a_group_or_world_readable_token_file_is_refused(tmp_path, mode):
    path = _write_token(tmp_path / "t" / "claude.token", b"fake-token-ok\n", mode)
    with pytest.raises(sandbox.SandboxError, match="mode|permission|readable"):
        sandbox.read_token(_token_cred(tmp_path, path), forbidden=())


@pytest.mark.parametrize("data", [b"", b"x" * 4097, b"one\ntwo\n", b"\xff\xfe"])
def test_s4_empty_oversized_multiline_or_undecodable_token_is_refused(tmp_path, data):
    path = _write_token(tmp_path / "t" / "claude.token", data)
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_token(_token_cred(tmp_path, path), forbidden=())


def test_s4_a_symlinked_token_file_is_refused(tmp_path):
    real = _write_token(tmp_path / "t" / "real.token", b"fake-token-ok\n")
    link = tmp_path / "t" / "claude.token"
    link.symlink_to(real)
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_token(_token_cred(tmp_path, link), forbidden=())


def test_s4_a_token_file_inside_a_grant_is_refused(tmp_path):
    path = _write_token(tmp_path / "repo" / "claude.token", b"fake-token-ok\n")
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_token(_token_cred(tmp_path, path), forbidden=[tmp_path / "repo"])


def test_s4_a_refusal_never_echoes_the_token(tmp_path):
    path = _write_token(tmp_path / "t" / "claude.token", b"fake-token-SECRETVALUE\n", 0o644)
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.read_token(_token_cred(tmp_path, path), forbidden=())
    assert "SECRETVALUE" not in str(exc.value)


def test_s4_the_credential_record_is_token_free_and_resolves_no_token(tmp_path):
    path = _write_token(tmp_path / "t" / "claude.token", b"fake-token-SECRETVALUE\n")
    cred = _token_cred(tmp_path, path)
    assert "SECRETVALUE" not in repr(cred)


# --- env allow-list (C12, D7), real bwrap ------------------------------------

def _probe_script(tmp_path: Path) -> str:
    return _write_script(tmp_path, "s4-probe.sh",
        "for p in /proc/[0-9]*; do\n"
        "  cat \"$p/environ\" >> proc-environ.bin 2>/dev/null; printf '\\0' >> proc-environ.bin\n"
        "  cat \"$p/cmdline\" >> proc-cmdline.bin 2>/dev/null; printf '\\0' >> proc-cmdline.bin\n"
        "done\n"
        "env -0 > env.bin\n")


@needs_bwrap
def test_s4_bwrap_environment_is_the_allow_list_and_no_host_secret_is_visible(tmp_path, monkeypatch):
    for key, value in HOST_SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LANG", "C.UTF-8")
    project = _repo(tmp_path, "probe", f"/bin/sh {_probe_script(tmp_path)} {{prompt}}")
    token = _fixture_value()
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    d = agent_run.prepare(project, "probe", prompt, run, role=ROLE)
    job = jobs.run_blocking(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                            label="probe", timeout_s=60, sandbox_writable=d.writable,
                            sandbox_layout=d.layout, env=d.env)
    assert job.exit_code == 0, Path(job.err).read_text()
    names = _names(run / "env.bin")
    assert names <= C12_KEPT | {"UV_CACHE_DIR", "SCIEFLOW_FIXTURE_TOKEN"} | SHELL_ADDED, names
    assert {"PATH", "HOME", "UV_CACHE_DIR", "LANG", "SCIEFLOW_FIXTURE_TOKEN"} <= names
    dumps = b"".join((run / f).read_bytes() for f in ("env.bin", "proc-environ.bin", "proc-cmdline.bin"))
    for secret in HOST_SECRETS.values():
        assert secret.encode() not in dumps, "a host secret reached the sandbox"
    assert token.encode() in (run / "env.bin").read_bytes()
    assert token.encode() not in (run / "proc-cmdline.bin").read_bytes()


@needs_bwrap
def test_s4_a_token_reaches_only_its_own_agent(tmp_path):
    alpha_value = f"fake-alpha-{uuid4().hex}"
    beta_value = f"fake-beta-{uuid4().hex}"
    for name, value in (("alpha.token", alpha_value), ("beta.token", beta_value)):
        _write_token(sandbox.credentials_dir() / name, (value + "\n").encode())

    def probe(name: str) -> str:
        return _write_script(tmp_path, f"s4-{name}.sh",
            "a=absent; b=absent\n"
            "if [ \"${SCIEFLOW_ALPHA_TOKEN+set}\" = set ]; then"
            f" [ \"$SCIEFLOW_ALPHA_TOKEN\" = {alpha_value} ] && a=match || a=mismatch; fi\n"
            "if [ \"${SCIEFLOW_BETA_TOKEN+set}\" = set ]; then"
            f" [ \"$SCIEFLOW_BETA_TOKEN\" = {beta_value} ] && b=match || b=mismatch; fi\n"
            f"echo \"alpha=$a beta=$b\" > result-{name}.txt\n")

    creds = {
        "alpha": {"mode": "env", "var": "SCIEFLOW_ALPHA_TOKEN", "file": "alpha.token", "reason": "fake"},
        "beta": {"mode": "env", "var": "SCIEFLOW_BETA_TOKEN", "file": "beta.token", "reason": "fake"},
    }
    agents = {"alpha": {"cmd": f"/bin/sh {probe('alpha')} {{prompt}}", "enabled": True, "timeout_min": 1},
              "beta": {"cmd": f"/bin/sh {probe('beta')} {{prompt}}", "enabled": True, "timeout_min": 1}}
    project = _project(tmp_path, agents, creds)
    run = project.run_dir("r1")
    for agent in ("alpha", "beta"):
        prompt = run / "logs" / f"{agent}.md"
        prompt.write_text(agent)
        d = agent_run.prepare(project, agent, prompt, run, role=ROLE)
        job = jobs.run_blocking(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                                label=agent, timeout_s=60, sandbox_writable=d.writable,
                                sandbox_layout=d.layout, env=d.env)
        assert job.exit_code == 0, Path(job.err).read_text()
    assert (run / "result-alpha.txt").read_text() == "alpha=match beta=absent\n"
    assert (run / "result-beta.txt").read_text() == "alpha=absent beta=match\n"


@needs_bwrap
@pytest.mark.parametrize("outcome", ["success", "failure", "refusal"])
def test_s4_the_token_is_never_written_to_the_run_state_or_refusals(
        tmp_path, monkeypatch, capsys, outcome):
    if outcome == "success":
        body = "echo ok > out.txt\n"
    elif outcome == "failure":
        body = "echo partial > out.txt\nexit 3\n"
    else:
        body = "echo ok > out.txt\n"
    script = _write_script(tmp_path, "s4-outcome.sh", body)
    project = _repo(tmp_path, "outcome", f"/bin/sh {script} {{prompt}}")
    token = _fixture_value()
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    transcript = run / "logs" / "t.md"
    monkeypatch.chdir(tmp_path)
    if outcome == "refusal":
        os.chmod(sandbox.credentials_dir() / "fixture.token", 0o644)
    if outcome != "refusal":
        d = agent_run.prepare(project, "outcome", prompt, run, role=ROLE)
        assert token not in repr(d)
    code = 0
    try:
        agent_run.main(["--role", ROLE, "outcome", str(prompt), str(transcript), "--cwd", str(run)])
    except SystemExit as exc:
        code = exc.code
    assert code == {"success": 0, "failure": 3, "refusal": 77}[outcome], code
    captured = capsys.readouterr()
    state = Path(os.environ["SCIEFLOW_STATE_DIR"])
    for path, data in _tree_bytes(run, state).items():
        assert token.encode() not in data, f"the token was written to {path}"
    assert token not in captured.out and token not in captured.err


@needs_bwrap
def test_s4_bwrap_turn_path_env_is_the_allow_list_plus_its_own_token(
        k_conversational_project, fake_bwrap, monkeypatch):
    for key, value in HOST_SECRETS.items():
        monkeypatch.setenv(key, value)
    project, ws, _ = k_conversational_project
    resolved = agent_run.resolve_turn(project, "k-protocol-fixture", run_dir=ws, session=None,
                                      create_home=True)
    items = dict(resolved.env_items)
    value = _fixture_value()
    assert items["SCIEFLOW_FIXTURE_TOKEN"] == value
    assert set(items) <= C12_KEPT | {"UV_CACHE_DIR", "SCIEFLOW_FIXTURE_TOKEN"}, set(items)
    assert all(secret not in items.values() for secret in HOST_SECRETS.values())
    assert value not in repr(resolved)


# --- C11 hand-off guard and the env-prefix rule (A7) -------------------------

def test_s4_a_credential_is_refused_for_a_cli_inside_a_writable_grant(tmp_path):
    run = tmp_path / "workspace" / "r1"
    planted = run / "bin" / "claude"
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/bin/sh\n")
    planted.chmod(0o755)
    project = _repo(tmp_path, "claude", f"{planted} -p {{prompt}}")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    with pytest.raises(sandbox.SandboxError, match="inside"):
        agent_run.prepare(project, "claude", prompt, run, role=ROLE)


@pytest.mark.parametrize("prefix, ok", [
    ("env MAX_THINKING_TOKENS=32000", True),
    ("env FOO=1", False),
    ("env PATH=/x", False),
    ("env HOME=/x", False),
    ("env LD_PRELOAD=/x", False),
    ("env LD_LIBRARY_PATH=/x", False),
])
def test_s4_env_prefix_names_are_limited_when_a_credential_is_handed_over(tmp_path, prefix, ok):
    project = _repo(tmp_path, "claude", f"{prefix} /bin/true {{prompt}}")
    prompt = project.run_dir("r1") / "logs" / "p.md"
    prompt.write_text("hi")
    if ok:
        d = agent_run.prepare(project, "claude", prompt, role=ROLE)
        assert d.writable is not None and d.env is not None
    else:
        with pytest.raises(sandbox.SandboxError):
            agent_run.prepare(project, "claude", prompt, role=ROLE)


# --- missing credentials: exit 77 with the exact instruction (C10) -----------

def test_s4_a_missing_claude_entry_names_the_setup_steps(tmp_path):
    project = _repo(tmp_path, "claude", "/bin/true {prompt}", credentials=False)
    prompt = project.run_dir("r1") / "logs" / "p.md"
    prompt.write_text("hi")
    with pytest.raises(sandbox.SandboxError) as exc:
        agent_run.prepare(project, "claude", prompt, role=ROLE)
    text = str(exc.value)
    assert "claude setup-token" in text and "chmod 600" in text and "credentials:" in text
    assert str(sandbox.credentials_dir() / "claude.token") in text


def test_s4_a_missing_entry_for_another_agent_names_the_entry_to_add(tmp_path):
    project = _repo(tmp_path, "codex", "/bin/true {prompt}", credentials=False)
    prompt = project.run_dir("r1") / "logs" / "p.md"
    prompt.write_text("hi")
    with pytest.raises(sandbox.SandboxError, match="codex"):
        agent_run.prepare(project, "codex", prompt, role=ROLE)


def test_s4_a_missing_entry_is_exit_77_and_job_refused_on_the_cli(tmp_path, monkeypatch):
    project = _repo(tmp_path, "claude", "/bin/true {prompt}", credentials=False)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["--role", ROLE, "claude", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_s4_a_missing_entry_is_a_service_error_and_job_refused(tmp_path):
    project = _repo(tmp_path, "claude", "/bin/true {prompt}", credentials=False)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    with pytest.raises(service.ServiceError, match="setup-token"):
        service.dispatch_agent(project, "claude", prompt, run / "logs" / "t.md", role=ROLE)
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_s4_an_unsandboxed_dispatch_needs_no_credential_and_no_env(tmp_path):
    project = _repo(tmp_path, "claude", "/bin/true {prompt}", credentials=False)
    prompt = project.run_dir("r1") / "logs" / "p.md"
    prompt.write_text("hi")
    d = agent_run.prepare(project, "claude", prompt, sandbox_enabled=False, role=ROLE)
    assert d.writable is None and d.env is None


# --- bind mode (C10, C13 layout) ---------------------------------------------

def test_s4_layout_binds_must_be_immutable_triples_of_absolute_paths():
    with pytest.raises(TypeError, match="binds must be"):
        sandbox.Layout(binds=[(Path("/a"), Path("/b"), False)])
    with pytest.raises(TypeError, match="binds must be"):
        sandbox.Layout(binds=((Path("rel"), Path("/b"), False),))
    with pytest.raises(TypeError, match="binds must be"):
        sandbox.Layout(binds=((Path("/a"), Path("/b"), "no"),))


def test_s4_a_bind_is_applied_after_the_own_home_bind(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    src = tmp_path / "auth.json"
    src.write_text("x")
    home = sandbox.agent_home(run, "bind-agent")
    layout = sandbox.Layout(home=home, binds=((src, home / ".codex" / "auth.json", False),))
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run, layout=layout)
    assert (_at(argv, "--bind", str(home), str(home))
            < _at(argv, "--ro-bind", str(src), str(home / ".codex" / "auth.json")))


def test_s4_a_bind_target_through_a_symlink_in_the_home_is_refused(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    src = tmp_path / "auth.json"
    src.write_text("x")
    home = sandbox.agent_home(run, "bind-agent")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / "link").symlink_to(elsewhere)
    layout = sandbox.Layout(home=home, binds=((src, home / "link" / "auth.json", False),))
    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(["agent"], writable=[run], cwd=run, layout=layout)


def test_s4_a_bind_credential_is_carried_into_the_layout_and_adds_no_env_var(
        tmp_path, tmp_path_factory, monkeypatch):
    home = _fake_home(tmp_path_factory, monkeypatch)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("fake-login")
    project = _project(tmp_path, {"codex": {"cmd": "/bin/true {prompt}", "enabled": True, "timeout_min": 1}},
                       {"codex": {"mode": "bind", "source": f"{home}/.codex/auth.json",
                                  "target": ".codex/auth.json", "reason": "codex login"}})
    prompt = project.run_dir("r1") / "logs" / "p.md"
    prompt.write_text("hi")
    d = agent_run.prepare(project, "codex", prompt, role=ROLE)
    home_dir = sandbox.agent_home(project.run_dir("r1"), "codex")
    assert d.layout.binds == (((home / ".codex" / "auth.json").resolve(),
                               home_dir / ".codex" / "auth.json", False),)
    assert "SCIEFLOW_FIXTURE_TOKEN" not in d.env
    assert "fake-login" not in repr(d)


@needs_bwrap
def test_s4_bwrap_bind_is_visible_read_only_and_leaves_no_source_bytes_on_the_host(
        tmp_path, tmp_path_factory, monkeypatch):
    home = _fake_home(tmp_path_factory, monkeypatch)
    (home / ".codex").mkdir()
    source = home / ".codex" / "auth.json"
    source.write_text("SECRET-LOGIN-7f3a9c")
    project = Project(tmp_path)
    run = tmp_path / "workspace" / "r1"
    (run / "logs").mkdir(parents=True)
    agent_home = sandbox.agent_home(run, "bind-agent")
    layout = sandbox.Layout(home=agent_home, binds=((source.resolve(),
                                                    agent_home / ".codex" / "auth.json", False),))
    script = ("cat \"$HOME/.codex/auth.json\" > \"$PWD/seen.txt\"\n"
              "echo x >> \"$HOME/.codex/auth.json\" 2>/dev/null && echo WRITE-LEAK > \"$PWD/w.txt\"\n"
              "true\n")
    job = jobs.run_blocking(project, ["/bin/sh", "-c", script], kind="agent", cwd=run, run_dir=run,
                            label="bind-agent", timeout_s=60, sandbox_writable=[run],
                            sandbox_layout=layout,
                            env={"HOME": str(agent_home), "PATH": "/usr/bin:/bin"})
    assert job.exit_code == 0, Path(job.err).read_text()
    assert (run / "seen.txt").read_text() == "SECRET-LOGIN-7f3a9c"
    assert not (run / "w.txt").exists()
    assert source.read_text() == "SECRET-LOGIN-7f3a9c"
    assert (agent_home / ".codex" / "auth.json").read_bytes() == b""
    # The agent may read its own login (residual R1) and writes what it saw into its run. What the
    # host must never hold is the source's bytes: no other file in the run may carry the secret.
    for path, data in _tree_bytes(run).items():
        if path.name != "seen.txt":
            assert b"SECRET-LOGIN" not in data, path


def test_s4_shipped_policy_is_a_pure_grammar_match(tmp_path):
    """Document-level check: the claude entries and the commented examples."""
    project = Project(W)
    creds = {c.agent: c for c in sandbox.credentials_of(project, check_sources=False)}
    for agent in ("claude", "claude-paper"):
        assert creds[agent].mode == "env"
        assert creds[agent].var == "CLAUDE_CODE_OAUTH_TOKEN"
        assert creds[agent].file == sandbox.credentials_dir() / "claude.token"
    assert not {"codex", "codex-paper", "codex-review", "agy"} & set(creds)
    text = (W / "config" / "sandbox.yml").read_text()
    for name in ("codex", "codex-paper", "codex-review", "agy"):
        assert f"#   {name}:" in text, name
    assert "credentials:" in text and "setup-token" in text


# --- never packed (A16) ------------------------------------------------------

def _archive():
    from sflib import archive
    return archive


def _run_tree(tmp_path: Path) -> Path:
    src = tmp_path / "run"
    (src / "logs").mkdir(parents=True)
    (src / "logs" / "a.md").write_text("keep")
    home = src / "scratch" / "agent-home" / "claude"
    home.mkdir(parents=True)
    (home / "session.json").write_text("cli state")
    (src / "scratch" / "other").mkdir()
    (src / "scratch" / "other" / "x.txt").write_text("scratch")
    (src / "agent-home").mkdir()
    (src / "agent-home" / "bare.txt").write_text("bare name, packed")
    return src


@pytest.mark.parametrize("skip_rebuildable", [True, False])
def test_s4_agent_home_is_never_packed_with_or_without_skip_rebuildable(tmp_path, skip_rebuildable):
    archive = _archive()
    src = _run_tree(tmp_path)
    out = tmp_path / "out.zip"
    archive.build_zip(src, out, skip_rebuildable=skip_rebuildable)
    names = set(zipfile.ZipFile(out).namelist())
    assert not any("scratch/agent-home" in n for n in names), names
    assert "logs/a.md" in names and "agent-home/bare.txt" in names
    if not skip_rebuildable:
        assert "scratch/other/x.txt" in names


@pytest.mark.parametrize("skip_rebuildable", [True, False])
def test_s4_agent_home_is_not_counted_in_workspace_size_or_ensure_space(tmp_path, skip_rebuildable):
    archive = _archive()
    src = _run_tree(tmp_path)
    home = src / "scratch" / "agent-home"
    counted = [p for p in src.rglob("*") if p.is_file() and home not in p.parents]
    if skip_rebuildable:   # the rebuildable rules also drop scratch/ itself
        counted = [p for p in counted if (src / "scratch") not in p.parents]
    expected = sum(p.stat().st_size for p in counted)
    assert archive.workspace_size(src, skip_rebuildable) == expected
    archive.ensure_space(src, tmp_path, skip_rebuildable=skip_rebuildable)


def test_s4_chats_bundle_leaves_agent_home_out(tmp_path):
    from scieflow.chats import bundle
    stage = tmp_path / "stage"
    home = stage / "scratch" / "agent-home" / "claude"
    home.mkdir(parents=True)
    (home / "session.json").write_text("cli state")
    (stage / "keep.txt").write_text("keep")
    out = bundle.write(stage, tmp_path / "bundle.zip")
    names = set(zipfile.ZipFile(out).namelist())
    assert "keep.txt" in names
    assert not any("agent-home" in n for n in names), names


def test_s4_dvcignore_excludes_agent_homes():
    lines = (W / ".dvcignore").read_text().splitlines()
    assert "**/scratch/agent-home/" in lines


# --- doctor (A11): stat only, never content or a hash ------------------------

def test_s4_doctor_reports_each_credential_from_stat_only_and_never_content(tmp_path, tmp_path_factory,
                                                                             monkeypatch):
    value = f"fake-doctor-{uuid4().hex}"
    _write_token(sandbox.credentials_dir() / "claude.token", (value + "\n").encode())
    _write_token(sandbox.credentials_dir() / "wide.token", (value + "\n").encode(), 0o644)
    home = _fake_home(tmp_path_factory, monkeypatch)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("fake-login")
    project = _project(tmp_path, {}, {
        "claude": {"mode": "env", "var": "CLAUDE_CODE_OAUTH_TOKEN", "file": "claude.token", "reason": "r"},
        "agy": {"mode": "env", "var": "AGY_TOKEN", "file": "agy-missing.token", "reason": "r"},
        "codex": {"mode": "env", "var": "CODEX_TOKEN", "file": "wide.token", "reason": "r"},
        "codex-paper": {"mode": "bind", "source": f"{home}/.codex/auth.json",
                        "target": ".codex/auth.json", "reason": "r"},
    })
    lines = sandbox.doctor_lines(project)
    text = "\n".join(lines)
    assert value not in text
    assert hashlib.sha256(value.encode()).hexdigest() not in text
    by_agent = {line.split()[0]: line for line in lines}
    assert "present" in by_agent["claude:"]
    assert "missing" in by_agent["agy:"]
    assert "bad-mode" in by_agent["codex:"]
    assert "present" in by_agent["codex-paper:"]


@needs_bwrap
def test_s4_doctor_module_entry_runs_and_prints_no_token(tmp_path, tmp_path_factory, monkeypatch):
    """S5 (residual c): the child's HOME is a fake one, so its credentials dir is the fake one.

    The doctor now also runs the confinement proof, so this needs bwrap (skips without it).
    """
    value = f"fake-entry-{uuid4().hex}"
    home = _fake_home(tmp_path_factory, monkeypatch)
    _write_token(home / ".config" / "scieflow" / "credentials" / "fixture.token",
                 (value + "\n").encode())
    _project(tmp_path, {}, {"claude": {"mode": "env", "var": "CLAUDE_CODE_OAUTH_TOKEN",
                                       "file": "fixture.token", "reason": "r"}})
    env = {**os.environ, "PYTHONPATH": str(W / "src"), "HOME": str(home),
           "SCIEFLOW_STATE_DIR": str(tmp_path_factory.mktemp("state"))}
    proc = subprocess.run([sys.executable, "-m", "scieflow.core.sandbox", "doctor"],
                          capture_output=True, text=True, cwd=tmp_path, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claude" in proc.stdout and value not in proc.stdout + proc.stderr


# --- turn path (A2, C10): the token-free record in TurnProtection ------------

def test_s4_turn_protection_carries_a_token_free_credential_record(k_conversational_project,
                                                                   monkeypatch):
    project, ws, _ = k_conversational_project
    protection = sandbox.resolve_turn_protection(project, run_dir=ws)
    creds = {c.agent: c for c in protection.credentials}
    assert creds["k-protocol-fixture"].var == "SCIEFLOW_FIXTURE_TOKEN"
    assert _fixture_value() not in repr(protection)


def test_s4_a_missing_turn_credential_is_a_service_error_and_job_refused(k_conversational_project):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    (project.root / "config" / "sandbox.yml").write_text("writable: []\n")   # the login entry is gone
    with pytest.raises(service.ServiceError, match="credentials"):
        service.say(project, "r1", "hello")
    assert "job.refused" in [e["type"] for e in events.read(ws)]
