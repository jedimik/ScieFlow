"""S1 of the allow-list sandbox: the allow-list root and the per-run agent home.

Design 2026-10-17 (D1, D2, D4) and plan r2 section 3, S1 (with section 5 amendments).
Pure argv tests monkeypatch bwrap as available. Real-bwrap tests carry needs_bwrap.

Subprocess tests set PYTHONPATH to this worktree's src: the venv's editable .pth
points at the main checkout, and a bare subprocess would import that tree instead.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, jobs, sandbox, service
from scieflow.core.project import Project
from scieflow.core.run import budget, status

W = Path(sandbox.__file__).resolve().parents[3]
HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
FAKE_BWRAP = "/usr/bin/bwrap"
_real_which = shutil.which


@pytest.fixture
def fake_bwrap(monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(
        sandbox.shutil, "which",
        lambda name, *a, **k: FAKE_BWRAP if name == "bwrap" else _real_which(name, *a, **k))


def _at(argv, *items):
    """Index of the first contiguous occurrence of `items` in argv."""
    n = len(items)
    for i in range(len(argv) - n + 1):
        if tuple(argv[i:i + n]) == items:
            return i
    raise AssertionError(f"{items!r} not in argv")


def _repo(tmp_path: Path, agent: str = "tracer", cmd: str = "/bin/sh -c {prompt}") -> Path:
    """A ScieFlow repo with one agent and one run (workspace/r1), for CLI dispatch.

    S4 (A9): the agent gets a real env credential backed by a fake 0600 token file.
    """
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
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    return tmp_path


def _home_of(run: Path, agent: str) -> Path:
    return run.resolve() / "scratch" / "agent-home" / agent


# --- argv shape (pure) -------------------------------------------------------

def test_wrap_has_an_allowlist_root_not_a_root_bind(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run)
    assert argv[0] == FAKE_BWRAP                      # the absolute bwrap path (A13)
    assert not any(argv[i:i + 3] == ["--ro-bind", "/", "/"] for i in range(len(argv)))
    for item in (("--ro-bind", "/usr", "/usr"), ("--ro-bind", "/etc", "/etc"),
                 ("--dev", "/dev"), ("--proc", "/proc"), ("--tmpfs", "/tmp"), ("--tmpfs", "/run")):
        _at(argv, *item)
    for banned in ("/home", "/mnt", "/srv", "/root", "/var", "/opt"):
        assert banned not in argv
    assert argv[_at(argv, "--chdir") + 1] == str(run.resolve())
    assert argv[-1] == "agent" and argv[-2] == "--"


def test_resolv_conf_target_is_bound_after_tmpfs_run(fake_bwrap, tmp_path, monkeypatch):
    target = tmp_path / "resolve" / "stub-resolv.conf"
    target.parent.mkdir()
    target.write_text("nameserver 192.0.2.1\n")
    link = tmp_path / "resolv.conf"
    link.symlink_to(target)
    monkeypatch.setattr(sandbox, "RESOLV_CONF", link)
    run = tmp_path / "run"
    run.mkdir()
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run)
    assert _at(argv, "--ro-bind", str(target.resolve()), str(target.resolve())) > _at(argv, "--tmpfs", "/run")


def test_bind_order_readable_links_writable_masks_chdir(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    ro = tmp_path / "ro"
    ro.mkdir()
    layout = sandbox.Layout(readable=(ro,), links=((tmp_path / "lnk", "ro"),))
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run, mask=[run / "masked"], layout=layout)
    order = [_at(argv, "--ro-bind", str(ro), str(ro)),
             _at(argv, "--symlink", "ro", str(tmp_path / "lnk")),
             _at(argv, "--bind", str(run.resolve()), str(run.resolve())),
             _at(argv, "--tmpfs", str(run.resolve() / "masked")),
             _at(argv, "--chdir")]
    assert order == sorted(order)
    assert argv[order[-1]:order[-1] + 6] == ["--chdir", str(run.resolve()), "--unshare-pid",
                                             "--die-with-parent", "--", "agent"]


def test_a_cwd_outside_every_bind_is_refused(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(sandbox.SandboxError, match="cwd"):
        sandbox.wrap(["agent"], writable=[run], cwd=tmp_path)


def test_layout_rejects_relative_paths_and_lists(tmp_path):
    with pytest.raises(TypeError):
        sandbox.Layout(readable=(Path("relative"),))
    with pytest.raises(TypeError):
        sandbox.Layout(readable=[tmp_path])
    with pytest.raises(TypeError):
        sandbox.Layout(links=((Path("rel"), "t"),))


# --- per-run agent home (pure, host filesystem) ------------------------------

def test_agent_home_is_a_private_0700_dir_that_persists(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    home = sandbox.agent_home(run, "claude")
    assert home == _home_of(run, "claude")
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    (home / "marker").write_text("kept")
    assert sandbox.agent_home(run, "claude") == home
    assert (home / "marker").read_text() == "kept"


@pytest.mark.parametrize("bad", ["../x", "a/b", ".hidden", "", "x y", "-x"])
def test_agent_home_refuses_bad_agent_names(tmp_path, bad):
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox.agent_home(run, bad)


def test_agent_home_never_follows_a_symlinked_component(tmp_path):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    decoy.chmod(0o755)
    run_a = tmp_path / "run-a"
    (run_a / "scratch").mkdir(parents=True)
    (run_a / "scratch" / "agent-home").symlink_to(decoy, target_is_directory=True)
    run_b = tmp_path / "run-b"
    run_b.mkdir()
    (run_b / "scratch").symlink_to(decoy, target_is_directory=True)
    for run in (run_a, run_b):
        with pytest.raises(sandbox.SandboxError):
            sandbox.agent_home(run, "claude")
    assert stat.S_IMODE(decoy.stat().st_mode) == 0o755
    assert not (decoy / "claude").exists()


def test_masks_for_a_run_grant_hide_provenance_and_every_agent_home(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    writable = [run]
    assert sandbox.masks_for(writable, run_dir=run) == [
        run.resolve() / "provenance.git", run.resolve() / "scratch" / "agent-home"]


def test_dispatch_creates_no_run_directories(tmp_path):
    """resolve/prepare-style dispatch construction is read-only (A12)."""
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("x")
    before = sorted(str(p) for p in run.rglob("*"))
    agent_run.prepare(Project(root), "tracer", prompt)
    assert sorted(str(p) for p in run.rglob("*")) == before
    assert not (run / "scratch").exists()


def test_dispatch_env_and_layout_name_the_agent_home(tmp_path):
    root = _repo(tmp_path)
    prompt = root / "workspace" / "r1" / "logs" / "p.md"
    prompt.write_text("x")
    d = agent_run.prepare(Project(root), "tracer", prompt)
    home = _home_of(root / "workspace" / "r1", "tracer")
    assert d.env["HOME"] == str(home)
    assert d.layout is not None and d.layout.home == home
    assert d.env["PATH"].endswith("/usr/local/bin:/usr/bin:/bin")
    assert d.env["UV_CACHE_DIR"] == str((root / "workspace" / "r1" / ".uv-cache").resolve())


def test_dispatch_layout_has_the_project_and_no_nested_entries(tmp_path):
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer")
    assert root.resolve() in layout.readable
    for p in layout.readable:
        assert not any(q != p and q in p.parents for q in layout.readable), p
        assert not str(p).startswith("/usr/")
    assert layout.home == _home_of(run, "tracer")


def test_a_dispatch_layout_refuses_a_bad_agent_name(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(sandbox.SandboxError):
        sandbox.dispatch_layout(Project(root), run_dir=root / "workspace" / "r1", agent="../x")


# --- hard deny table (pure) --------------------------------------------------

@pytest.mark.parametrize("rel", [".", ".ssh", ".ssh/id_ed25519", ".gnupg", ".hermes/.env",
                                 ".config/scieflow/credentials", ".config/scieflow/credentials/x.token",
                                 ".codex", ".codex/auth.json", ".claude", ".claude.json", ".gemini",
                                 ".config", ".."])
def test_the_deny_table_refuses_secret_paths_under_the_home(monkeypatch, tmp_path, rel):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    with pytest.raises(sandbox.SandboxError):
        sandbox.check_not_denied(fake / rel, kind="readable")


def test_the_deny_table_refuses_ancestors_of_the_home(monkeypatch, tmp_path):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    for path in (Path("/"), Path("/home"), fake.parent):
        with pytest.raises(sandbox.SandboxError):
            sandbox.check_not_denied(path, kind="readable")


def test_the_deny_table_allows_the_project_inside_home(monkeypatch, tmp_path):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    for path in (fake / "project", fake / "project" / "src", fake / ".local" / "bin"):
        sandbox.check_not_denied(path, kind="readable")       # does not raise


def test_the_deny_table_also_covers_the_passwd_home(monkeypatch, tmp_path):
    import pwd
    monkeypatch.setenv("HOME", str(tmp_path))
    passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    for rel in (".ssh", ".hermes", ".codex/auth.json"):
        with pytest.raises(sandbox.SandboxError):
            sandbox.check_not_denied(passwd_home / rel, kind="readable")


@pytest.mark.parametrize("path", ["/run", "/run/user/1000/bus", "/var/run", "/proc", "/sys", "/dev"])
def test_runtime_sockets_are_never_readable(path):
    with pytest.raises(sandbox.SandboxError):
        sandbox.check_not_denied(Path(path), kind="readable")


# --- env fail-closed and launch refusals (pure) -------------------------------

def test_a_sandboxed_job_without_an_explicit_env_is_refused(tmp_path):
    """A4: a sandboxed launch must not silently inherit the host environment."""
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    with pytest.raises(sandbox.SandboxError):
        jobs.start(Project(root), ["/bin/true"], kind="agent", cwd=run, run_dir=run,
                   sandbox_writable=[run])


def test_a_cli_launch_refusal_exits_77_and_records_job_refused(tmp_path, monkeypatch):
    """A11: a SandboxError at launch (verify passed) is exit 77 plus job.refused."""
    from scieflow.core import events
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sandbox, "verify", lambda *a, **k: None)

    def refuse(*a, **k):
        raise sandbox.SandboxError("launch refused by test")
    monkeypatch.setattr(sandbox, "wrap", refuse)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_a_service_launch_refusal_is_service_error_and_job_refused(tmp_path, monkeypatch):
    from scieflow.core import events
    root = _repo(tmp_path, agent="tracer")
    project = Project(root)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    monkeypatch.setattr(sandbox, "verify", lambda *a, **k: None)

    def refuse(*a, **k):
        raise sandbox.SandboxError("launch refused by test")
    monkeypatch.setattr(sandbox, "wrap", refuse)
    with pytest.raises(service.ServiceError, match="launch refused"):
        service.dispatch_agent(project, "tracer", prompt, run / "logs" / "t.md")
    assert "job.refused" in [e["type"] for e in events.read(run)]


# --- real bwrap tracers ------------------------------------------------------

def _tracer_script(run: Path, repo: Path, fake_home: Path) -> str:
    passwd_home = "$(getent passwd \"$(id -u)\" | cut -d: -f6)"
    checks = [str(fake_home / ".ssh"), str(fake_home / ".hermes"), passwd_home + "/.ssh",
              passwd_home + "/.hermes", "/run/user/$(id -u)/bus", "/run/dbus/system_bus_socket"]
    lines = [f"printf 'HOME=%s\\n' \"$HOME\" > {run}/result.txt",
             f"cat {repo}/src/untouched.py >/dev/null 2>&1 && echo read-repo-ok >> {run}/result.txt"
             f" || echo read-repo-FAIL >> {run}/result.txt",
             f"echo inside > {run}/inside.txt 2>/dev/null && echo write-run-ok >> {run}/result.txt"
             f" || echo write-run-FAIL >> {run}/result.txt",
             f"echo pwned > {repo}/src/untouched.py 2>/dev/null && echo write-repo-LEAK >> {run}/result.txt"
             f" || echo write-repo-denied >> {run}/result.txt"]
    for path in checks:
        lines.append(f"if test -e {path}; then echo PRESENT {path} >> {run}/result.txt; "
                     f"else echo absent >> {run}/result.txt; fi")
    return "\n".join(lines) + "\n"


@needs_bwrap
def test_bwrap_cli_tracer_reads_repo_writes_run_and_hides_secrets(tmp_path, tmp_path_factory):
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    (root / "src").mkdir()
    (root / "src" / "untouched.py").write_text("original\n")
    fake_home = tmp_path_factory.mktemp("home")
    # S4 (A9): the CLI child resolves its credentials dir from this HOME, so the fake token lives here.
    creds_dir = fake_home / ".config" / "scieflow" / "credentials"
    creds_dir.mkdir(parents=True)
    (creds_dir / "fixture.token").write_text("fake-token-fixture\n")
    (creds_dir / "fixture.token").chmod(0o600)
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "id").write_text("secret")
    (fake_home / ".hermes").mkdir()
    (fake_home / ".hermes" / ".env").write_text("token")
    prompt = run / "logs" / "p.md"
    prompt.write_text(_tracer_script(run, root, fake_home))
    env = {**os.environ, "PYTHONPATH": str(W / "src"), "HOME": str(fake_home)}
    proc = subprocess.run([sys.executable, "-m", "scieflow.core.agent_run", "tracer",
                           str(prompt), str(run / "logs" / "t.md")],
                          capture_output=True, text=True, cwd=root, env=env, timeout=120)
    result = (run / "result.txt").read_text() if (run / "result.txt").exists() else ""
    assert proc.returncode == 0, proc.stdout + proc.stderr + result
    assert f"HOME={_home_of(run, 'tracer')}" in result
    assert "read-repo-ok" in result and "write-run-ok" in result
    assert "write-repo-denied" in result and "LEAK" not in result
    assert "PRESENT" not in result, result
    assert (root / "src" / "untouched.py").read_text() == "original\n"


@needs_bwrap
def test_bwrap_home_persists_within_a_run_and_not_across_runs(tmp_path):
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    other = project.run_dir("r2")
    (other / "logs").mkdir(parents=True)
    (other / "config.yml").write_text("slug: r2\n")
    ws_layout = sandbox.dispatch_layout(project, run_dir=run, agent="tracer")
    other_layout = sandbox.dispatch_layout(project, run_dir=other, agent="tracer")
    # F6: another run's dispatch gets its own HOME, not the first run's directory.
    assert other_layout.home != ws_layout.home
    assert other_layout.home == _home_of(other, "tracer")

    def run_once(writable_run, layout, script):
        env = sandbox.dispatch_environment(writable_run, layout=layout, env=dict(os.environ), writable=[writable_run])
        job = jobs.run_blocking(project, ["/bin/sh", "-c", script], kind="agent", cwd=writable_run,
                                run_dir=writable_run, sandbox_writable=[writable_run], env=env,
                                sandbox_layout=layout)
        return job.state

    assert run_once(run, ws_layout, f"echo first > $HOME/marker") == "done"
    assert run_once(run, ws_layout, f"cat $HOME/marker > {run}/read.txt") == "done"
    assert (run / "read.txt").read_text() == "first\n"
    assert run_once(other, other_layout, f"test -e $HOME/marker && echo LEAK > {other}/leak.txt; true") == "done"
    assert not (other / "leak.txt").exists()


# --- S1 fix round 1 (review F1-F3, F5, F6, F10-F13) ---------------------------

def test_wrap_self_binds_scratch_between_writable_and_masks(fake_bwrap, tmp_path):
    """F1: scratch becomes a mount point inside, so it cannot be renamed or removed."""
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    run_r = run.resolve()
    scratch = run_r / "scratch"
    layout = sandbox.dispatch_layout(project, run_dir=run, agent="tracer")
    masks = sandbox.masks_for([run], run_dir=run)
    assert not scratch.exists()
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run, mask=masks, layout=layout)
    assert scratch.is_dir()                       # created host-side, before the bind
    order = [_at(argv, "--bind", str(run_r), str(run_r)),
             _at(argv, "--bind", str(scratch), str(scratch)),
             _at(argv, "--tmpfs", str(run_r / "scratch" / "agent-home")),
             _at(argv, "--bind", str(layout.home), str(layout.home))]
    assert order == sorted(order)


@needs_bwrap
def test_bwrap_agent_cannot_relocate_scratch_or_plant_in_a_sibling_home(tmp_path):
    """F1 (review probe p10): alpha must not move scratch and plant into beta's home."""
    root = _repo(tmp_path, agent="alpha")
    project = Project(root)
    run = project.run_dir("r1")
    run_r = run.resolve()
    beta_home = sandbox.agent_home(run_r, "beta")
    (beta_home / ".claude").mkdir()
    (beta_home / ".claude" / "session.json").write_text("BETA-SESSION\n")
    alpha = sandbox.dispatch_layout(project, run_dir=run, agent="alpha")
    beta = sandbox.dispatch_layout(project, run_dir=run, agent="beta")
    env_a = sandbox.dispatch_environment(run_r, layout=alpha, env=dict(os.environ), writable=[run_r])
    env_b = sandbox.dispatch_environment(run_r, layout=beta, env=dict(os.environ), writable=[run_r])
    attack = (
        f"if mv {run_r}/scratch {run_r}/scratch.old 2>/dev/null; then echo mv-scratch-LEAK; "
        f"else echo mv-scratch-blocked; fi > {run_r}/alpha-out.txt\n"
        f"if mv {run_r}/scratch/agent-home {run_r}/scratch/home.old 2>/dev/null; then echo mv-home-LEAK; "
        f"else echo mv-home-blocked; fi >> {run_r}/alpha-out.txt\n"
        f"mkdir -p {beta_home}/.claude 2>/dev/null && echo PLANTED > {beta_home}/.claude/settings.json "
        f"2>/dev/null; true\n")
    jobs.run_blocking(project, ["/bin/sh", "-c", attack], kind="agent", cwd=run_r, run_dir=run_r,
                      sandbox_writable=[run_r], env=env_a, sandbox_layout=alpha)
    alpha_out = (run_r / "alpha-out.txt").read_text()
    assert "mv-scratch-blocked" in alpha_out and "mv-home-blocked" in alpha_out, alpha_out
    assert "LEAK" not in alpha_out
    check = (f"if test -e $HOME/.claude/session.json; then echo session-present; fi > {run_r}/beta-out.txt; "
             f"if test -e $HOME/.claude/settings.json; then echo PLANTED-SEEN; "
             f"else echo settings-absent; fi >> {run_r}/beta-out.txt")
    jobs.run_blocking(project, ["/bin/sh", "-c", check], kind="agent", cwd=run_r, run_dir=run_r,
                      sandbox_writable=[run_r], env=env_b, sandbox_layout=beta)
    beta_out = (run_r / "beta-out.txt").read_text()
    assert "session-present" in beta_out and "settings-absent" in beta_out, beta_out
    assert (beta_home / ".claude" / "session.json").read_text() == "BETA-SESSION\n"
    assert not (beta_home / ".claude" / "settings.json").exists()


def test_a_link_already_inside_a_bound_root_is_dropped(tmp_path, monkeypatch):
    """F2a / A10 guard: an interpreter link under the bound project root is not recreated."""
    root = _repo(tmp_path).resolve()
    (root / "real_venv" / "bin").mkdir(parents=True)
    (root / ".venv").symlink_to(root / "real_venv", target_is_directory=True)
    monkeypatch.setattr(sys, "executable", str(root / ".venv" / "bin" / "python"))
    layout = sandbox.dispatch_layout(Project(root), run_dir=root / "workspace" / "r1", agent="tracer")
    assert (root / ".venv").is_symlink() and root in layout.readable   # positive controls
    assert all(link != root / ".venv" for link, _ in layout.links)


def test_merged_usr_links_follow_the_host_in_all_three_branches(tmp_path, monkeypatch):
    """F2b / D1: symlink -> --symlink, real dir -> --ro-bind, absent -> omitted."""
    host = tmp_path / "host"
    host.mkdir()
    (host / "bin").symlink_to("usr/bin")
    (host / "lib").mkdir()
    monkeypatch.setattr(sandbox, "MERGED_USR_ROOT", host)
    monkeypatch.setattr(sandbox, "RESOLV_CONF", tmp_path / "no-resolv.conf")
    args = sandbox._system_args(sandbox.RESOLV_CONF)
    assert _at(args, "--symlink", "usr/bin", "/bin") >= 0
    assert _at(args, "--ro-bind", str(host / "lib"), "/lib") >= 0
    assert not any(item in ("/lib64", "/sbin") for item in args)


@needs_bwrap
def test_one_layout_object_reaches_verify_and_launch_on_the_service_path(tmp_path, monkeypatch):
    """F2c / S1.11 (service): every wrap (verify probe and launch) gets the one captured Layout."""
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo ok > " + str(run / "ok.txt"))
    built, seen = [], []
    real_layout, real_wrap = sandbox.dispatch_layout, sandbox.wrap

    def spy_layout(*a, **k):
        built.append(real_layout(*a, **k))
        return built[-1]

    def spy_wrap(argv, **kw):
        seen.append(kw.get("layout"))
        return real_wrap(argv, **kw)
    monkeypatch.setattr(sandbox, "dispatch_layout", spy_layout)
    monkeypatch.setattr(sandbox, "wrap", spy_wrap)
    service.dispatch_agent(project, "tracer", prompt, run / "logs" / "t.md")
    assert (run / "ok.txt").read_text() == "ok\n"
    assert len(built) == 1 and len(seen) >= 2
    assert all(layout is built[0] for layout in seen)


def test_one_layout_object_reaches_verify_and_launch_on_the_turn_path(k_conversational_project, monkeypatch):
    """F2c / S1.11 (turn): verify and launch get the layout resolve_turn captured."""
    project, ws, _ = k_conversational_project
    built, seen_verify, seen_wrap = [], [], []
    real_layout = sandbox.dispatch_layout

    def spy_layout(*a, **k):
        built.append(real_layout(*a, **k))
        return built[-1]

    def spy_verify(writable, cwd, **kw):
        seen_verify.append(kw.get("layout"))

    def refuse_launch(argv, **kw):
        seen_wrap.append(kw.get("layout"))
        raise sandbox.SandboxError("turn launch refused by test")
    monkeypatch.setattr(sandbox, "dispatch_layout", spy_layout)
    monkeypatch.setattr(sandbox, "verify", spy_verify)
    monkeypatch.setattr(sandbox, "wrap", refuse_launch)
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert len(built) == 1 and len(seen_verify) == 1 and len(seen_wrap) == 1
    assert seen_verify[0] is built[0] and seen_wrap[0] is built[0]


@pytest.mark.parametrize("target", [".ssh", ".codex", ".hermes"])
def test_a_symlink_resolving_into_a_denied_path_is_refused(monkeypatch, tmp_path, target):
    """F2d guard: the lexical path lies inside the home; only its resolved target is denied."""
    fake = tmp_path / "home"
    (fake / target).mkdir(parents=True)
    (fake / "project").mkdir()
    link = fake / "project" / "keys"
    link.symlink_to(fake / target, target_is_directory=True)
    monkeypatch.setenv("HOME", str(fake))
    sandbox.check_not_denied(fake / "project", kind="readable")       # positive control
    with pytest.raises(sandbox.SandboxError):
        sandbox.check_not_denied(link, kind="readable")


def test_agent_home_mode_is_set_without_following_a_link_swapped_in_late(tmp_path, monkeypatch):
    """F5: a link planted after the lstat checks must not get the victim's mode changed."""
    victim = tmp_path / "victim"
    victim.mkdir()
    victim.chmod(0o755)
    run = tmp_path / "run"
    run.mkdir()
    real_restrict = sandbox._restrict_home

    def swap_then_restrict(path):
        moved = path.rename(tmp_path / "moved-aside")
        path.symlink_to(victim, target_is_directory=True)
        return real_restrict(path)
    monkeypatch.setattr(sandbox, "_restrict_home", swap_then_restrict)
    with pytest.raises(sandbox.SandboxError):
        sandbox.agent_home(run, "claude")
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755


def test_a_turn_in_an_unsandboxed_run_creates_no_agent_home(k_conversational_project):
    """F3: an unsandboxed turn creates nothing on the host (A12)."""
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")   # chosen while sandboxed
    (project.root / sandbox.ALLOWLIST_FILE).write_text(
        "unsandboxed_runs:\n  - slug: r1\n    reason: test\n")
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert not (ws / "scratch" / "agent-home").exists()


def test_a_turn_with_a_symlinked_scratch_is_refused_and_recorded(k_conversational_project, tmp_path_factory):
    """F3 / F13 (turn): the refusal is a ServiceError plus job.refused, and the decoy is untouched."""
    from scieflow.core import events
    project, ws, _ = k_conversational_project
    decoy = tmp_path_factory.mktemp("decoy")
    decoy.chmod(0o755)
    (ws / "scratch").symlink_to(decoy, target_is_directory=True)
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert "job.refused" in [e["type"] for e in events.read(ws)]
    assert stat.S_IMODE(decoy.stat().st_mode) == 0o755
    assert not (decoy / "agent-home").exists()


def _linked_scratch_repo(tmp_path, tmp_path_factory):
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    decoy = tmp_path_factory.mktemp("decoy")
    (run / "scratch").symlink_to(decoy, target_is_directory=True)
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    return root, run, prompt


def test_a_layout_refusal_on_the_cli_is_exit_77_and_records_job_refused(tmp_path, tmp_path_factory, monkeypatch):
    """F13 (CLI): a SandboxError from dispatch_layout exits 77 and records job.refused."""
    from scieflow.core import events
    root, run, prompt = _linked_scratch_repo(tmp_path, tmp_path_factory)
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_a_layout_refusal_on_the_service_path_is_service_error_and_job_refused(tmp_path, tmp_path_factory):
    """F13 (service): a SandboxError from dispatch_layout is a ServiceError plus job.refused."""
    from scieflow.core import events
    root, run, prompt = _linked_scratch_repo(tmp_path, tmp_path_factory)
    with pytest.raises(service.ServiceError):
        service.dispatch_agent(Project(root), "tracer", prompt, run / "logs" / "t.md")
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_wrap_emits_an_absolute_bwrap_for_a_relative_path_entry(tmp_path, monkeypatch):
    """F10 / A13: a relative PATH entry must not leak a relative bwrap into argv."""
    rel = tmp_path / "relbin"
    rel.mkdir()
    fake = rel / "bwrap"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "relbin:/usr/bin:/bin")
    monkeypatch.setattr(sandbox, "available", lambda: True)
    run = tmp_path / "run"
    run.mkdir()
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run)
    assert os.path.isabs(argv[0]) and Path(argv[0]).name == "bwrap"
    assert Path(argv[0]) == fake.absolute()


def test_a_resolv_conf_target_inside_a_denied_home_is_not_bound(monkeypatch, tmp_path):
    """F11: the resolved resolv.conf target is deny-checked; a refused target is skipped."""
    fake = tmp_path / "home"
    (fake / ".ssh").mkdir(parents=True)
    secret = fake / ".ssh" / "resolv.conf"
    secret.write_text("nameserver 192.0.2.9\n")
    link = tmp_path / "resolv.conf"
    link.symlink_to(secret)
    monkeypatch.setenv("HOME", str(fake))
    args = sandbox._system_args(link)
    assert str(secret.resolve()) not in args
    assert _at(args, "--tmpfs", "/run") >= 0          # the rest of the system set still builds


def test_the_resolv_check_does_not_refuse_the_runtime_area_dns_needs(monkeypatch, tmp_path):
    """F11 guard: the stub resolver lives under /run; the resolv check must still allow it."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    sandbox.check_not_denied(Path("/run/systemd/resolve/stub-resolv.conf"), kind="resolv")


def test_a_launcher_dir_reaches_path_unless_it_is_a_system_dir(tmp_path):
    """F12 / A17, narrowed by S25fix1 (review finding 5): a launcher dir under a bound SYSTEM path adds
    nothing to PATH. A dir that is only covered by a readable bind (a project-local install) is
    added, because a covered-but-off-PATH launcher made the dispatch refuse (see the review's probe)."""
    root = tmp_path / "project"
    (root / ".venv" / "bin").mkdir(parents=True)
    covered = sandbox.Layout(readable=(root,), launcher_dirs=(root / ".venv" / "bin",))
    env = sandbox.dispatch_environment(tmp_path / "run", layout=covered, env={}, writable=[tmp_path / "run"])
    assert env["PATH"] == f"{root / '.venv' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    system = sandbox.Layout(readable=(root,), launcher_dirs=(Path("/usr/bin"),))
    assert sandbox.dispatch_environment(tmp_path / "run", layout=system, env={}, writable=[tmp_path / "run"])["PATH"] == \
        "/usr/local/bin:/usr/bin:/bin"
    outside = tmp_path / "bin2"
    outside.mkdir()
    free = sandbox.Layout(readable=(root,), launcher_dirs=(outside,))
    assert sandbox.dispatch_environment(tmp_path / "run", layout=free, env={}, writable=[tmp_path / "run"])["PATH"].startswith(str(outside))


@needs_bwrap
def test_bwrap_agent_cannot_see_or_write_a_sibling_agents_home(tmp_path):
    root = _repo(tmp_path, agent="alpha")
    run = Project(root).run_dir("r1")
    project = Project(root)
    alpha = sandbox.dispatch_layout(project, run_dir=run, agent="alpha")
    beta = sandbox.dispatch_layout(project, run_dir=run, agent="beta")
    alpha_home = _home_of(run, "alpha")
    beta_home = _home_of(run, "beta")
    env_a = sandbox.dispatch_environment(run, layout=alpha, env=dict(os.environ), writable=[run])
    env_b = sandbox.dispatch_environment(run, layout=beta, env=dict(os.environ), writable=[run])
    jobs.run_blocking(project, ["/bin/sh", "-c", "echo alpha-secret > $HOME/token"], kind="agent",
                      cwd=run, run_dir=run, sandbox_writable=[run], env=env_a, sandbox_layout=alpha)
    jobs.run_blocking(project, ["/bin/sh", "-c",
                                f"test -e {alpha_home}/token && echo SEEN > {run}/seen.txt; "
                                f"echo planted > {alpha_home}/token 2>/dev/null; true"],
                      kind="agent", cwd=run, run_dir=run, sandbox_writable=[run], env=env_b,
                      sandbox_layout=beta)
    assert not (run / "seen.txt").exists()
    assert (alpha_home / "token").read_text() == "alpha-secret\n"
    assert beta_home.is_dir()


@needs_bwrap
def test_bwrap_resolv_conf_is_readable_inside(tmp_path):
    if not Path("/etc/resolv.conf").exists():
        pytest.skip("no /etc/resolv.conf on this host")
    run = tmp_path / "run"
    run.mkdir()
    proc = subprocess.run(sandbox.wrap(["/bin/sh", "-c", "cat /etc/resolv.conf >/dev/null && echo ok"],
                                       writable=[run], cwd=run),
                          capture_output=True, text=True, timeout=60)
    assert proc.stdout.strip() == "ok", proc.stderr


@needs_bwrap
def test_bwrap_uv_run_executes_python_inside(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    layout = sandbox.dispatch_layout(project, run_dir=run, agent="tracer")
    env = sandbox.dispatch_environment(run, layout=layout, env=dict(os.environ), writable=[run])
    script = f"uv run --offline --no-project python3 -c 'print(42)' > {run}/uv.txt 2>&1"
    job = jobs.run_blocking(project, ["/bin/sh", "-c", script], kind="agent", cwd=run,
                            run_dir=run, sandbox_writable=[run], env=env, sandbox_layout=layout)
    assert job.state == "done", (run / "uv.txt").read_text()
    assert (run / "uv.txt").read_text().strip() == "42"


@needs_bwrap
def test_bwrap_service_path_tracer_uses_the_same_layout(tmp_path, tmp_path_factory):
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    fake_home = tmp_path_factory.mktemp("home")
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "id").write_text("secret")
    prompt = run / "logs" / "p.md"
    prompt.write_text(_tracer_script(run, root, fake_home))
    (root / "src").mkdir()
    (root / "src" / "untouched.py").write_text("original\n")
    service.dispatch_agent(project, "tracer", prompt, run / "logs" / "t.md")
    result = (run / "result.txt").read_text()
    assert f"HOME={_home_of(run, 'tracer')}" in result
    assert "write-repo-denied" in result and "PRESENT" not in result


@needs_bwrap
def test_one_layout_object_reaches_verify_and_launch(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo ok > " + str(run / "ok.txt"))
    monkeypatch.chdir(root)
    seen = []
    real_wrap = sandbox.wrap

    def spy(argv, **kw):
        seen.append(kw.get("layout"))
        return real_wrap(argv, **kw)
    monkeypatch.setattr(sandbox, "wrap", spy)
    agent_run.main(["tracer", str(prompt), str(run / "logs" / "t.md")])
    assert len(seen) >= 2 and seen[0] is not None
    assert all(layout is seen[0] for layout in seen)


# --- turn path (pure, k fixture) ---------------------------------------------

def test_a_resolved_turn_carries_the_agent_home_and_writes_nothing(k_conversational_project):
    from scieflow.core.turn_input import HumanSubmission
    project, ws, _ = k_conversational_project
    before = sorted(str(p) for p in ws.rglob("*"))
    resolved = agent_run.resolve_turn(project, "k-protocol-fixture", run_dir=ws, session=None)
    assert dict(resolved.env_items)["HOME"] == str(_home_of(ws, "k-protocol-fixture"))
    assert resolved.layout is not None and resolved.layout.home == _home_of(ws, "k-protocol-fixture")
    prepared = agent_run.prepare_turn(resolved, HumanSubmission("hello", "chat", None))
    assert prepared.layout is resolved.layout
    assert sorted(str(p) for p in ws.rglob("*")) == before
    assert not (ws / "scratch").exists()


def test_the_turn_layout_must_be_the_exact_type(k_conversational_project):
    from dataclasses import fields, replace
    project, ws, _ = k_conversational_project
    resolved = agent_run.resolve_turn(project, "k-protocol-fixture", run_dir=ws, session=None)
    assert "layout" in {f.name for f in fields(resolved)}          # the field must exist (RED)
    with pytest.raises(TypeError):
        replace(resolved, layout={"readable": ()})


def test_a_turn_launch_refusal_is_service_error_and_job_refused(k_conversational_project, monkeypatch):
    """A11 on the turn path: verify passed, the launch is refused, the turn is not replayed."""
    from scieflow.core import events
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    monkeypatch.setattr(sandbox, "verify", lambda *a, **k: None)

    def refuse(*a, **k):
        raise sandbox.SandboxError("turn launch refused by test")
    monkeypatch.setattr(sandbox, "wrap", refuse)
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert "job.refused" in [e["type"] for e in events.read(ws)]


# --- S2: readable: grammar, hard deny list, refusals, C14 masks, other runs' homes ---
# Plan r2 section 3 S2, design D5, C8, C9, C14, A1, A2 and the S1 re-review NITs N1-N4.

def _policy(root: Path, text: str) -> Project:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "sandbox.yml").write_text(text)
    return Project(root)


def _readable_yaml(*entries) -> str:
    """A config/sandbox.yml whose readable: list holds one (path, reason) entry per pair.

    S4 (A9): also one fake env credential per agent these tests dispatch, so a sandboxed
    dispatch is not refused for want of a login. Their refusals stay the readable ones.
    """
    creds = "".join(
        f"  {name}:\n    mode: env\n    var: SCIEFLOW_FIXTURE_TOKEN\n    file: fixture.token\n"
        "    reason: fake fixture token\n"
        for name in ("tracer", "k-protocol-fixture", "claude-turn", "envcli"))
    return ("readable:\n" + "".join(f"  - path: {p}\n    reason: {r}\n" for p, r in entries)
            + "credentials:\n" + creds)


def _two_homes(monkeypatch, tmp_path_factory):
    """Both homes the deny table consults: a fake HOME-env home and a fake passwd home."""
    env_home = tmp_path_factory.mktemp("home").resolve()
    passwd_home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(env_home))
    monkeypatch.setattr(sandbox, "_homes", lambda: sorted({env_home, passwd_home}, key=str))
    return env_home, passwd_home


def _make_secret_tree(home: Path) -> None:
    for d in (".ssh", ".gnupg", ".hermes", ".config/scieflow/credentials", ".claude", ".codex", ".gemini"):
        (home / d).mkdir(parents=True, exist_ok=True)
    for f in (".ssh/id_ed25519", ".hermes/.env", ".config/scieflow/credentials/a.token",
              ".claude.json", ".codex/auth.json"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text("FAKE")


_DENIED_UNDER_HOME = [".", ".ssh", ".ssh/id_ed25519", ".gnupg", ".hermes", ".hermes/.env",
                      ".config/scieflow/credentials", ".config/scieflow/credentials/a.token",
                      ".claude", ".claude.json", ".codex", ".codex/auth.json", ".gemini"]
_RUNTIME_SOCKETS = ["/run", "/run/user/1000/bus", "/var/run", "/proc", "/sys", "/dev"]


def test_s2_readable_is_empty_when_the_key_is_absent(tmp_path):
    assert sandbox.readable_paths(_policy(tmp_path / "a", "writable: []\n")) == ()
    assert sandbox.readable_paths(_policy(tmp_path / "b", "")) == ()


def test_s2_readable_accepts_absolute_home_and_project_relative_entries(tmp_path, tmp_path_factory, monkeypatch):
    env_home, _ = _two_homes(monkeypatch, tmp_path_factory)
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    extra = tmp_path / "extra"
    extra.mkdir()
    (env_home / "shared").mkdir()
    project = _policy(root, _readable_yaml((str(extra), "absolute"), ("~/shared", "home"), ("data", "relative")))
    assert sandbox.readable_paths(project) == (
        extra.resolve(), (env_home / "shared").resolve(), (root / "data").resolve())


@pytest.mark.parametrize("text", [
    "readable: data\n",                                    # not a list
    "readable:\n  - data\n",                               # entry not a mapping
    "readable:\n  - reason: why\n",                        # no path
    "readable:\n  - path: ''\n    reason: why\n",          # empty path
    "readable:\n  - path: 5\n    reason: why\n",            # path not text
    "readable:\n  - path: data\n",                         # no reason
    "readable:\n  - path: data\n    reason: ''\n",         # empty reason
    "readable:\n  - path: no-such-dir\n    reason: why\n",  # path does not exist
])
def test_s2_malformed_readable_entries_are_refused(tmp_path, text):
    (tmp_path / "repo" / "data").mkdir(parents=True)
    with pytest.raises(sandbox.SandboxError):
        sandbox.readable_paths(_policy(tmp_path / "repo", text))


@pytest.mark.parametrize("which", ["env", "passwd"])
@pytest.mark.parametrize("rel", _DENIED_UNDER_HOME)
def test_s2_readable_refuses_every_hard_denied_path_under_either_home(
        tmp_path, tmp_path_factory, monkeypatch, which, rel):
    env_home, passwd_home = _two_homes(monkeypatch, tmp_path_factory)
    for home in (env_home, passwd_home):
        _make_secret_tree(home)
    home = env_home if which == "env" else passwd_home
    root = tmp_path / "repo"
    root.mkdir()
    target = home / rel
    with pytest.raises(sandbox.SandboxError):
        sandbox.readable_paths(_policy(root, _readable_yaml((str(target), "test"))))


@pytest.mark.parametrize("path", ["/", "/home", "PARENT", *_RUNTIME_SOCKETS])
def test_s2_readable_refuses_home_ancestors_and_runtime_sockets(tmp_path, tmp_path_factory, monkeypatch, path):
    env_home, _ = _two_homes(monkeypatch, tmp_path_factory)
    root = tmp_path / "repo"
    root.mkdir()
    target = str(env_home.parent) if path == "PARENT" else path
    with pytest.raises(sandbox.SandboxError):
        sandbox.readable_paths(_policy(root, _readable_yaml((target, "test"))))


def test_s2_readable_refuses_a_symlink_that_resolves_into_a_denied_path(tmp_path, tmp_path_factory, monkeypatch):
    env_home, _ = _two_homes(monkeypatch, tmp_path_factory)
    _make_secret_tree(env_home)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "alias").symlink_to(env_home / ".ssh", target_is_directory=True)
    with pytest.raises(sandbox.SandboxError):
        sandbox.readable_paths(_policy(root, _readable_yaml(("alias", "link"))))


def test_s2_a_denied_readable_entry_is_exit_77_and_recorded_on_the_cli(tmp_path, monkeypatch):
    from scieflow.core import events
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    (root / "config" / "sandbox.yml").write_text(_readable_yaml(("/run", "denied by test")))
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["tracer", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_s2_a_denied_readable_entry_is_a_service_error_and_recorded(tmp_path):
    from scieflow.core import events
    root = _repo(tmp_path)
    project = Project(root)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    (root / "config" / "sandbox.yml").write_text(_readable_yaml(("/run", "denied by test")))
    with pytest.raises(service.ServiceError, match="runtime socket"):
        service.dispatch_agent(project, "tracer", prompt, run / "logs" / "t.md")
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_s2_a_denied_readable_entry_refuses_a_turn_before_launch(k_conversational_project):
    from scieflow.core import events
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    (project.root / "config" / "sandbox.yml").write_text(_readable_yaml(("/run", "denied by test")))
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "hello")
    assert "job.refused" in [e["type"] for e in events.read(ws)]


def test_s2_a_denied_readable_entry_refuses_setting_the_turn_agent(k_conversational_project):
    from scieflow.core import events
    project, ws, _ = k_conversational_project
    (project.root / "config" / "sandbox.yml").write_text(_readable_yaml(("/run", "denied by test")))
    with pytest.raises(service.ServiceError, match="runtime socket"):
        service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    assert "job.refused" in [e["type"] for e in events.read(ws)]


def test_s2_the_cli_reads_the_policy_once_and_carries_readable_into_the_layout(tmp_path, tmp_path_factory, monkeypatch):
    root = _repo(tmp_path)
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("echo hi")
    extra = tmp_path_factory.mktemp("extra")           # outside the repo: inside it, it is already readable
    (root / "config" / "sandbox.yml").write_text(_readable_yaml((str(extra), "cli test")))
    reads = []
    real_load = sandbox._load_allowlist

    def counting(project):
        reads.append(project.root)
        return real_load(project)
    monkeypatch.setattr(sandbox, "_load_allowlist", counting)
    d = agent_run.prepare(Project(root), "tracer", prompt)
    assert len(reads) == 1                                        # A2: one captured policy read
    assert d.writable is not None and extra.resolve() in d.layout.readable


def test_s2_the_turn_captures_readable_from_the_one_policy_read(k_conversational_project, tmp_path_factory):
    project, ws, _ = k_conversational_project
    extra = tmp_path_factory.mktemp("extra").resolve()
    (project.root / "config" / "sandbox.yml").write_text(_readable_yaml((str(extra), "turn test")))
    protection = sandbox.resolve_turn_protection(project, run_dir=ws)
    assert protection.readable == (extra,)
    resolved = agent_run.resolve_turn(project, "k-protocol-fixture", run_dir=ws, session=None)
    assert extra in resolved.layout.readable


def test_s2_a_readable_policy_edit_after_resolve_fails_admission(k_conversational_project, tmp_path_factory):
    from scieflow.core import turn_admission
    project, ws, _ = k_conversational_project
    extra = tmp_path_factory.mktemp("extra").resolve()
    policy = project.root / "config" / "sandbox.yml"
    policy.write_text(_readable_yaml((str(extra), "turn test")))
    with turn_admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.validate_protection(protection)
        policy.write_text(_readable_yaml((str(extra), "turn test"), ("/usr/share/doc", "edited")))
        with pytest.raises(turn_admission.AdmissionUnsafe):
            held.validate_protection(protection)


def test_s2_validate_protection_refuses_a_tampered_readable(k_conversational_project, tmp_path_factory):
    import dataclasses
    from scieflow.core import turn_admission
    project, ws, _ = k_conversational_project
    extra = tmp_path_factory.mktemp("extra").resolve()
    other = tmp_path_factory.mktemp("other").resolve()
    (project.root / "config" / "sandbox.yml").write_text(_readable_yaml((str(extra), "turn test")))
    with turn_admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.validate_protection(protection)
        with pytest.raises(turn_admission.AdmissionUnsafe):
            held.validate_protection(dataclasses.replace(protection, readable=(other,)))


def test_s2_layout_hidden_must_be_a_tuple_of_absolute_paths():
    assert sandbox.Layout(hidden=(Path("/x"),)).hidden == (Path("/x"),)
    with pytest.raises(TypeError):
        sandbox.Layout(hidden=("relative",))
    with pytest.raises(TypeError):
        sandbox.Layout(hidden=[Path("/x")])


def test_s2_readable_binds_after_the_system_set_and_before_the_writable_run(fake_bwrap, tmp_path):
    extra = (tmp_path / "extra").resolve()
    extra.mkdir()
    run = (tmp_path / "repo" / "workspace" / "r1").resolve()
    run.mkdir(parents=True)
    argv = sandbox.wrap(["/bin/true"], writable=[run], cwd=run,
                        layout=sandbox.Layout(readable=(extra,)))
    at = _at(argv, "--ro-bind", str(extra), str(extra))
    assert _at(argv, "--tmpfs", "/run") < at < _at(argv, "--bind", str(run), str(run))


def test_s2_a_readable_grant_inside_a_writable_grant_is_refused_not_shadowed(fake_bwrap, tmp_path):
    """A readable entry under a writable grant would silently be writable: refused, not bound."""
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    (run / "data").mkdir(parents=True)
    with pytest.raises(sandbox.SandboxError, match="writable grant"):
        sandbox.wrap(["/bin/true"], writable=[run], cwd=run,
                     layout=sandbox.Layout(readable=((run / "data").resolve(),)))


def test_s2_project_secret_files_are_discovered_and_only_those(tmp_path):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    (root / ".env").write_text("A=1")
    (root / ".env.local").write_text("B=2")
    (root / ".env.example").write_text("C=3")
    (root / ".env_template").write_text("D=4")
    (root / ".env.d").mkdir()
    (root / ".dvc").mkdir()
    (root / ".dvc" / "config.local").write_text("E=5")
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer")
    assert set(layout.hidden) == {root / ".env", root / ".env.local", root / ".env.example",
                                  root / ".env.d", root / ".dvc" / "config.local"}


def test_s2_hidden_secrets_overlay_after_readable_and_before_the_writable_run(fake_bwrap, tmp_path):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    (root / ".env").write_text("A=1")
    (root / ".env.d").mkdir()
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer")
    argv = sandbox.wrap(["/bin/true"], writable=[run], cwd=run, layout=layout)
    null_bind = _at(argv, "--ro-bind", "/dev/null", str(root / ".env"))
    assert _at(argv, "--ro-bind", str(root), str(root)) < null_bind
    assert _at(argv, "--tmpfs", str(root / ".env.d")) < _at(argv, "--bind", str(run), str(run))
    assert null_bind < _at(argv, "--bind", str(run), str(run))


def test_s2_a_symlinked_project_secret_is_refused(tmp_path):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    (tmp_path / "elsewhere").write_text("x")
    (root / ".env").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(sandbox.SandboxError):
        sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer")


def test_s2_a_secret_inside_a_writable_grant_is_refused_not_reexposed(fake_bwrap, tmp_path):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    (root / ".env").write_text("A=1")
    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(["/bin/true"], writable=[root], cwd=run,
                     layout=sandbox.Layout(hidden=(root / ".env",)))


def test_s2_other_runs_agent_homes_are_hidden_and_symlinks_are_never_followed(tmp_path, tmp_path_factory):
    root = (tmp_path / "repo").resolve()
    ws = root / "workspace"
    r1, r2 = ws / "r1", ws / "r2"
    (r1 / "scratch" / "agent-home" / "alpha").mkdir(parents=True)
    (r1 / "scratch" / "agent-home" / "alpha" / "session.json").write_text("FAKE")
    r2.mkdir(parents=True)
    decoy = tmp_path_factory.mktemp("decoy").resolve()
    (decoy / "agent-home" / "gamma").mkdir(parents=True)
    r3 = ws / "r3"                                   # symlinked scratch: skipped, target untouched
    r3.mkdir()
    (r3 / "scratch").symlink_to(decoy, target_is_directory=True)
    r4 = ws / "r4"                                   # symlinked agent-home: skipped
    (r4 / "scratch").mkdir(parents=True)
    (r4 / "scratch" / "agent-home").symlink_to(decoy / "agent-home", target_is_directory=True)
    layout = sandbox.dispatch_layout(Project(root), run_dir=r2, agent="beta")
    assert set(layout.hidden) == {r1 / "scratch" / "agent-home"}
    assert (decoy / "agent-home" / "gamma").is_dir()


@pytest.mark.parametrize("name", ["resolv-dir", "resolv-proc"])
def test_n1_the_resolv_target_must_be_a_regular_file_outside_proc_sys_dev(tmp_path, name):
    target = {"resolv-dir": tmp_path / "dir", "resolv-proc": Path("/proc/self/environ")}[name]
    if name == "resolv-dir":
        target.mkdir()
    link = tmp_path / name
    link.symlink_to(target)
    assert sandbox._resolv_target(link) is None
    good = tmp_path / "resolv.conf"
    good.write_text("nameserver 192.0.2.1\n")
    ok = tmp_path / "resolv-ok"
    ok.symlink_to(good)
    assert sandbox._resolv_target(ok) == good.resolve()


def test_n2_a_mask_only_scratch_symlink_is_refused_like_the_home_case(fake_bwrap, tmp_path, tmp_path_factory):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    decoy = tmp_path_factory.mktemp("decoy").resolve()
    (decoy / "agent-home" / "beta").mkdir(parents=True)
    (run / "scratch").symlink_to(decoy, target_is_directory=True)
    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(["/bin/true"], writable=[run], cwd=run, mask=[run / "scratch" / "agent-home"])
    assert (decoy / "agent-home" / "beta").is_dir()


def test_n3_check_agent_name_is_public_and_the_service_uses_it():
    import inspect
    assert sandbox.check_agent_name("alpha") is None
    with pytest.raises(sandbox.SandboxError):
        sandbox.check_agent_name("../x")
    assert "_check_agent_name" not in inspect.getsource(service._say_prepared)


def test_n4_a_scratch_pin_that_cannot_be_created_is_a_sandbox_error(fake_bwrap, tmp_path, monkeypatch):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    real_mkdir = os.mkdir

    def deny_scratch(path, *args, **kwargs):
        if Path(path).name == "scratch":
            raise PermissionError(13, "denied by test")
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(sandbox.os, "mkdir", deny_scratch)
    with pytest.raises(sandbox.SandboxError, match="scratch"):
        sandbox.wrap(["/bin/true"], writable=[run], cwd=run, mask=[run / "scratch" / "agent-home"])


@needs_bwrap
def test_bwrap_a_readable_dir_is_readable_not_writable_and_a_sibling_is_absent(tmp_path):
    extra = (tmp_path / "extra").resolve()
    extra.mkdir()
    (extra / "note.txt").write_text("NOTE-OK")
    sibling = (tmp_path / "sibling").resolve()
    sibling.mkdir()
    run = (tmp_path / "repo" / "workspace" / "r1").resolve()
    run.mkdir(parents=True)
    script = (f"cat {extra}/note.txt; echo; "
              f"if (echo x > {extra}/w.txt) 2>/dev/null; then echo WRITE-LEAK; else echo ro-denied; fi; "
              f"if test -e {sibling}; then echo SIBLING-LEAK; else echo sibling-absent; fi")
    argv = sandbox.wrap(["/bin/sh", "-c", script], writable=[run], cwd=run,
                        layout=sandbox.Layout(readable=(extra,)))
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "NOTE-OK" in proc.stdout and "ro-denied" in proc.stdout and "sibling-absent" in proc.stdout
    assert "LEAK" not in proc.stdout and not (extra / "w.txt").exists()


@needs_bwrap
def test_bwrap_a_dispatch_cannot_see_another_runs_agent_home(tmp_path):
    root = (tmp_path / "repo").resolve()
    (root / "config").mkdir(parents=True)
    r1, r2 = root / "workspace" / "r1", root / "workspace" / "r2"
    r1.mkdir(parents=True)
    r2.mkdir(parents=True)
    alpha_home = sandbox.agent_home(r1, "alpha")
    (alpha_home / "session.json").write_text("FAKE-SESSION")
    layout = sandbox.dispatch_layout(Project(root), run_dir=r2, agent="beta")
    script = (f"if test -e {alpha_home}/session.json; then echo SEEN-SESSION; fi; "
              f"echo LISTING:$(ls -A {r1}/scratch/agent-home); "
              f"mkdir -p {alpha_home}/beta 2>/dev/null; touch {alpha_home}/planted 2>/dev/null; echo done")
    argv = sandbox.wrap(["/bin/sh", "-c", script], writable=[r2], cwd=r2,
                        mask=sandbox.masks_for([r2], run_dir=r2), layout=layout)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "SEEN-SESSION" not in proc.stdout and "alpha" not in proc.stdout and "done" in proc.stdout
    assert not (alpha_home / "planted").exists() and not (alpha_home / "beta").exists()
    assert (alpha_home / "session.json").read_text() == "FAKE-SESSION"
    own = sandbox.dispatch_layout(Project(root), run_dir=r1, agent="alpha")
    argv_own = sandbox.wrap(["/bin/sh", "-c", f"cat {alpha_home}/session.json"], writable=[r1], cwd=r1,
                            mask=sandbox.masks_for([r1], run_dir=r1), layout=own)
    own_proc = subprocess.run(argv_own, capture_output=True, text=True, timeout=60, check=False)
    assert own_proc.stdout == "FAKE-SESSION"


@needs_bwrap
def test_bwrap_project_secret_files_are_masked_and_the_template_stays_readable(tmp_path):
    root = (tmp_path / "repo").resolve()
    run = root / "workspace" / "r1"
    run.mkdir(parents=True)
    (root / ".dvc").mkdir()
    secrets = {root / ".env": "FAKE_SECRET_A\n", root / ".env.local": "FAKE_SECRET_B\n",
               root / ".dvc" / "config.local": "FAKE_SECRET_C\n"}
    for path, text in secrets.items():
        path.write_text(text)
    (root / ".env_template").write_text("TEMPLATE_OK\n")
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer")
    script = (f"cat {root}/.env; cat {root}/.env.local; cat {root}/.dvc/config.local; "
              f"cat {root}/.env_template")
    argv = sandbox.wrap(["/bin/sh", "-c", script], writable=[run], cwd=run,
                        mask=sandbox.masks_for([run], run_dir=run), layout=layout)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "FAKE_SECRET" not in proc.stdout and "TEMPLATE_OK" in proc.stdout
    assert {p: p.read_text() for p in secrets} == secrets


def test_s2_the_shipped_policy_documents_readable():
    text = (W / "config" / "sandbox.yml").read_text()
    assert "# readable:" in text and "never overridable" in text


def test_s2_docs_gain_a_readable_section():
    import re
    text = (W / "docs" / "sandbox.md").read_text()
    assert re.search(r"^#+ .*readable", text, re.M)


# --- S3: agent and tool install roots, env prefix, K16 compatibility -----------
# Fake installs live under a fake HOME from tmp_path_factory (A6). Labels s3-*.

CODEX_BODY = ("#!/bin/sh\n"
              'here=$(dirname "$(readlink -f "$0")")\n'
              'if test -f "$here/../codex-resources/marker"; then echo resources-ok; '
              'else echo resources-missing; fi\n')


def _s3_home(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(home))
    (home / ".local" / "bin").mkdir(parents=True)
    return home


def _exe(path: Path, body: str = "#!/bin/sh\necho ok\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _claude_install(home: Path, body: str = "#!/bin/sh\necho claude-ran\n", version: str = "1.0") -> Path:
    """claude: ~/.local/bin/claude -> ~/.local/share/claude/versions/<v> (a file)."""
    target = _exe(home / ".local" / "share" / "claude" / "versions" / version, body)
    (home / ".local" / "bin" / "claude").symlink_to(target)
    return target


def _codex_install(home: Path, body: str = CODEX_BODY, version: str = "0.1.0") -> Path:
    """codex: ~/.local/bin/codex -> .codex/.../standalone/current/bin/codex; current -> releases/<v>."""
    release = home / ".codex" / "packages" / "standalone" / "releases" / version
    _exe(release / "bin" / "codex", body)
    (release / "codex-resources").mkdir()
    (release / "codex-resources" / "marker").write_text("res\n")
    current = home / ".codex" / "packages" / "standalone" / "current"
    current.symlink_to(f"releases/{version}")
    (home / ".local" / "bin" / "codex").symlink_to(current / "bin" / "codex")
    return release


def _layout(project, launcher, agent="tracer"):
    return sandbox.dispatch_layout(project, run_dir=project.run_dir("r1"), agent=agent,
                                   launcher=launcher)


def _append_agent(project, name: str, cfg: dict) -> None:
    path = project.root / "config" / "agents.yml"
    block = ''.join('    ' + line + '\n' for line in yaml.safe_dump(cfg).splitlines())
    path.write_text(path.read_text() + f'  {name}:\n{block}')


def _turn_cfg(**over) -> dict:
    cfg = {'cmd': 'claude first {prompt}', 'session_cmd': 'claude first {prompt}',
           'resume_cmd': 'claude --resume {session} resume {prompt}', 'family': 'claude',
           'enabled': True, 'timeout_min': 1, 'model': 'fixture', 'reasoning': 'high'}
    cfg.update(over)
    return cfg


@pytest.mark.parametrize("argv, index", [
    (["claude", "-p"], 0),
    (["env", "MAX_THINKING_TOKENS=32000", "claude", "-p"], 2),
    (["/usr/bin/env", "A=1", "B=2", "codex", "exec"], 3),
])
def test_s3_cli_index_is_argv0_or_the_command_after_env_operands(argv, index):
    assert sandbox.agent_cli_index(argv) == index


@pytest.mark.parametrize("argv", [
    ["env", "-i", "claude"], ["env", "-S", "claude -p"], ["env", "-u", "X", "claude"],
    ["env", "--", "claude"], ["env", "A=1", "-i", "claude"], ["env"], ["env", "A=1"], [],
])
def test_s3_an_env_prefix_with_options_or_no_command_is_refused(argv):
    with pytest.raises(sandbox.SandboxError):
        sandbox.agent_cli_index(argv)


def test_s3_claude_install_root_is_the_version_file_and_the_hop_is_a_link(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    target = _claude_install(home)
    layout = _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "claude")
    assert layout.installs == (target,)
    assert (home / ".local" / "bin" / "claude", str(target)) in layout.links
    assert home / ".local" / "bin" in layout.launcher_dirs


def test_s3_codex_install_root_is_the_releases_dir_with_both_hops_as_links(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    release = _codex_install(home)
    layout = _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "codex")
    current = home / ".codex" / "packages" / "standalone" / "current"
    assert layout.installs == (release,)
    assert (home / ".local" / "bin" / "codex", str(current / "bin" / "codex")) in layout.links
    assert (current, "releases/0.1.0") in layout.links


def test_s3_a_plain_file_launcher_is_its_own_root(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    tool = _exe(home / ".local" / "bin" / "agy", "#!/bin/sh\n")
    layout = _layout(Project(_repo(tmp_path)), tool)
    assert layout.installs == (tool,)
    assert not [link for link, _ in layout.links if link.name == "agy"]


def test_s3_a_venv_link_to_a_uv_python_roots_at_the_python_file(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    uv_python = _exe(home / ".local" / "share" / "uv" / "python" / "cpython-3.12.9" / "bin" / "python3.12")
    root = _repo(tmp_path)
    venv_python = root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(uv_python)
    layout = _layout(Project(root), venv_python)
    assert layout.installs == (uv_python,)
    # the venv link lies inside the project root, which is already bound: it is not recreated
    assert all(link != venv_python for link, _ in layout.links)


def test_s3_the_climb_is_bounded_to_two_components_above_the_file(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    deep = _exe(home / ".local" / "share" / "tool" / "versions" / "1.0" / "sub" / "deeper" / "tool")
    (home / ".local" / "bin" / "tool").symlink_to(deep)
    layout = _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "tool")
    assert layout.installs == (deep,)      # versions/1.0 is three components up: not the root


def test_s3_a_store_root_outside_packages_is_refused(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    release = home / ".codex" / "releases" / "0.1.0"
    _exe(release / "bin" / "codex", CODEX_BODY)
    (home / ".local" / "bin" / "codex").symlink_to(release / "bin" / "codex")
    with pytest.raises(sandbox.SandboxError, match="credential store"):
        _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "codex")


def test_s3_a_store_root_holding_a_credential_file_is_refused(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    release = _codex_install(home)
    (release / "auth.json").write_text("fake-token\n")
    with pytest.raises(sandbox.SandboxError, match="credential"):
        _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "codex")


def test_s3_the_store_walk_is_bounded(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    _codex_install(home)
    monkeypatch.setattr(sandbox, "INSTALL_WALK_MAX", 3)
    with pytest.raises(sandbox.SandboxError, match="entries"):
        _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "codex")


@pytest.mark.parametrize("target", [".codex", ".claude", ""])
def test_s3_a_root_equal_to_or_containing_a_store_or_the_home_is_refused(tmp_path, tmp_path_factory,
                                                                     monkeypatch, target):
    home = _s3_home(tmp_path_factory, monkeypatch)
    where = home / target if target else home
    where.mkdir(exist_ok=True)
    (home / ".local" / "bin" / "tool").symlink_to(where)
    with pytest.raises(sandbox.SandboxError):
        _layout(Project(_repo(tmp_path)), home / ".local" / "bin" / "tool")


def test_s3_an_install_root_inside_a_bound_root_is_dropped(tmp_path):
    root = _repo(tmp_path)
    real = _exe(root / "tools" / "real", "#!/bin/sh\n")
    launcher = root / "tools" / "alias"
    launcher.symlink_to(real)
    assert _layout(Project(root), launcher).installs == ()


def test_s3_the_launcher_dir_joins_path_only_when_no_bound_path_covers_it(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    _claude_install(home)
    monkeypatch.setattr(sandbox, "_launcher", lambda name: (None, [], None))
    project = Project(_repo(tmp_path))
    layout = _layout(project, home / ".local" / "bin" / "claude")
    env = sandbox.dispatch_environment(project.run_dir("r1"), layout=layout, env={"PATH": "/x"}, writable=[project.run_dir("r1")])
    assert env["PATH"] == f"{home / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    system = _layout(project, Path("/bin/sh"))
    assert sandbox.dispatch_environment(project.run_dir("r1"), layout=system,
                                        env={"PATH": "/x"},
                                        writable=[project.run_dir("r1")])["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_s3_merged_usr_hops_are_never_recreated(tmp_path):
    layout = _layout(Project(_repo(tmp_path)), Path("/bin/sh"))
    assert not [link for link, _ in layout.links if link.parts[1:2] and link.parts[1] in
                ("bin", "lib", "lib64", "sbin")]


def test_s3_layout_installs_must_be_an_immutable_tuple_of_absolute_paths(tmp_path):
    assert sandbox.Layout(installs=(tmp_path,)).installs == (tmp_path,)   # RED: the field must exist
    with pytest.raises(TypeError):
        sandbox.Layout(installs=[tmp_path])
    with pytest.raises(TypeError):
        sandbox.Layout(installs=(Path("relative"),))


def test_s3_installs_are_read_only_and_an_install_inside_a_grant_is_refused(fake_bwrap, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    inst = tmp_path / "inst"
    inst.mkdir()
    argv = sandbox.wrap(["agent"], writable=[run], cwd=run, layout=sandbox.Layout(installs=(inst,)))
    _at(argv, "--ro-bind", str(inst), str(inst))
    (run / "tool").write_text("x")
    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(["agent"], writable=[run], cwd=run,
                     layout=sandbox.Layout(installs=(run / "tool",)))


def test_s3_cli_prepare_resolves_claude_behind_an_env_prefix(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    target = _claude_install(home)
    monkeypatch.setenv("PATH", f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    root = _repo(tmp_path, agent="envcli", cmd="env MAX_THINKING_TOKENS=32000 claude -p {prompt}")
    prompt = root / "workspace" / "r1" / "logs" / "p.md"
    prompt.write_text("hi")
    d = agent_run.prepare(Project(root), "envcli", prompt)
    assert d.argv[:4] == ["env", "MAX_THINKING_TOKENS=32000", "claude", "-p"]
    assert d.layout.installs == (target,)
    assert str(home / ".local" / "bin") in d.env["PATH"].split(":")


def test_s3_cli_env_with_options_is_refused_exit_77_and_recorded(tmp_path, monkeypatch):
    from scieflow.core import events
    root = _repo(tmp_path, agent="envopt", cmd="env -i claude -p {prompt}")
    run = root / "workspace" / "r1"
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exc:
        agent_run.main(["envopt", str(prompt), str(run / "logs" / "t.md")])
    assert exc.value.code == 77
    assert "job.refused" in [e["type"] for e in events.read(run)]


def test_s3_host_and_capture_divergence_is_refused(tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    _claude_install(home)
    monkeypatch.setenv("PATH", f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    root = _repo(tmp_path, agent="envcli", cmd="claude -p {prompt}")
    prompt = root / "workspace" / "r1" / "logs" / "p.md"
    prompt.write_text("hi")
    monkeypatch.setattr(agent_run, "_capture_execution",
                        lambda token, cwd, env: (Path("/usr/bin/true"), (), "/usr/bin/true"))
    with pytest.raises(sandbox.SandboxError, match="launcher"):
        agent_run.prepare(Project(root), "envcli", prompt)


@pytest.mark.parametrize("key", ["session_cmd", "resume_cmd"])
@pytest.mark.parametrize("prefix", ["env", "/usr/bin/env"])
def test_s3_a_turn_env_prefix_is_refused_on_both_templates(k_conversational_project, key, prefix):
    project, ws, _ = k_conversational_project
    _append_agent(project, "envturn", _turn_cfg(**{key: prefix + " MAX_THINKING_TOKENS=32000 " +
                                                  _turn_cfg()[key]}))
    with pytest.raises(sandbox.SandboxError, match="env"):
        agent_run.resolve_turn(project, "envturn", run_dir=ws, session=None)


def test_s3_a_turn_env_prefix_refusal_is_recorded_as_job_refused(k_conversational_project):
    from scieflow.core import events
    project, ws, _ = k_conversational_project
    _append_agent(project, "envturn", _turn_cfg(session_cmd="env X=1 " + _turn_cfg()["session_cmd"]))
    try:
        service.set_conversation_agent(project, "r1", "envturn")
    except service.ServiceError:
        pass
    else:
        with pytest.raises(service.ServiceError):
            service.say(project, "r1", "hello")
    assert "job.refused" in [e["type"] for e in events.read(ws)]


@needs_bwrap
def test_s3_bwrap_a_fake_claude_runs_by_name_and_by_path_with_its_own_path_and_no_neighbours(
        tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    body = ("#!/bin/sh\necho claude-ran\n"
            f"if test -e {home}/.local/bin/other; then echo NEIGHBOUR-LEAK; else echo neighbour-absent; fi\n"
            "echo path=$PATH\n")
    _claude_install(home, body=body)
    _exe(home / ".local" / "bin" / "other", "#!/bin/sh\n")
    monkeypatch.setenv("PATH", f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    root = _repo(tmp_path, agent="envcli", cmd="claude -p {prompt}")
    project = Project(root)
    run = project.run_dir("r1")
    prompt = run / "logs" / "p.md"
    prompt.write_text("hi")
    d = agent_run.prepare(project, "envcli", prompt)
    kw = dict(kind="agent", cwd=d.cwd, run_dir=d.run_dir, label=d.agent, timeout_s=60,
              sandbox_writable=d.writable, sandbox_layout=d.layout, env=d.env)
    job = jobs.run_blocking(project, d.argv, **kw)
    out = Path(job.log).read_text()
    assert job.exit_code == 0, out
    assert "claude-ran" in out and "neighbour-absent" in out and "LEAK" not in out, out
    assert f"path={d.env['PATH']}" in out, out
    by_path = jobs.run_blocking(project, [str(home / ".local" / "bin" / "claude"), "-p", "hi"],
                                **{**kw, "label": "by-path"})
    assert by_path.exit_code == 0 and "claude-ran" in Path(by_path.log).read_text()


@needs_bwrap
def test_s3_bwrap_a_fake_codex_finds_its_resources_and_never_sees_the_store_auth(
        tmp_path, tmp_path_factory, monkeypatch):
    home = _s3_home(tmp_path_factory, monkeypatch)
    body = CODEX_BODY + f"if test -e {home}/.codex/auth.json; then echo AUTH-LEAK; else echo auth-absent; fi\n"
    _codex_install(home, body)
    (home / ".codex" / "auth.json").write_text("fake-auth\n")
    project = Project(_repo(tmp_path))
    run = project.run_dir("r1")
    layout = sandbox.dispatch_layout(project, run_dir=run, agent="tracer",
                                     launcher=home / ".local" / "bin" / "codex")
    env = sandbox.dispatch_environment(run, layout=layout, env=dict(os.environ), writable=[run])
    job = jobs.run_blocking(project, [str(home / ".local" / "bin" / "codex")], kind="agent", cwd=run,
                            run_dir=run, label="codex", timeout_s=60, sandbox_writable=[run],
                            sandbox_layout=layout, env=env)
    out = Path(job.log).read_text()
    assert job.exit_code == 0, out
    assert "resources-ok" in out and "auth-absent" in out and "LEAK" not in out, out


@needs_bwrap
def test_s3_bwrap_k16_turn_through_a_fake_claude_resolves_its_launcher_and_records_its_session(
        k_conversational_project, tmp_path_factory, monkeypatch):
    import shlex
    project, ws, _ = k_conversational_project
    home = _s3_home(tmp_path_factory, monkeypatch)
    protocol = W / "tests" / "fixtures" / "k_conversation_protocol.py"
    fake = _exe(home / ".local" / "bin" / "claude",
                f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -B {shlex.quote(str(protocol))} \"$@\"\n")
    _append_agent(project, "claude-turn", _turn_cfg())
    (project.root / "config" / "sandbox.yml").write_text(_readable_yaml())   # S4 (A9): claude-turn's login
    monkeypatch.setenv("PATH", f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    resolved = agent_run.resolve_turn(project, "claude-turn", run_dir=ws, session=None)
    assert resolved.execution_path == home / ".local" / "bin" / "claude"
    assert resolved.layout.installs == (fake,)
    service.set_conversation_agent(project, "r1", "claude-turn")
    result = service.say(project, "r1", "hello")
    assert result["job"]["state"] == "done" and result["job"]["exit_code"] == 0
    assert result["turn"]["text"] == "K protocol reply"
