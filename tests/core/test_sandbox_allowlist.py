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
    """A ScieFlow repo with one agent and one run (workspace/r1), for CLI dispatch."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump({"agents": {
        agent: {"cmd": cmd, "enabled": True, "timeout_min": 1}}}))
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
        env = sandbox.dispatch_environment(writable_run, layout=layout, env=dict(os.environ))
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
    env_a = sandbox.dispatch_environment(run_r, layout=alpha, env=dict(os.environ))
    env_b = sandbox.dispatch_environment(run_r, layout=beta, env=dict(os.environ))
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


def test_a_launcher_inside_a_bound_root_adds_nothing_to_path(tmp_path):
    """F12 / A17: a launcher dir already covered by a bound path is not added to PATH."""
    root = tmp_path / "project"
    (root / ".venv" / "bin").mkdir(parents=True)
    covered = sandbox.Layout(readable=(root,), launcher_dirs=(root / ".venv" / "bin",))
    env = sandbox.dispatch_environment(tmp_path / "run", layout=covered, env={})
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    outside = tmp_path / "bin2"
    outside.mkdir()
    free = sandbox.Layout(readable=(root,), launcher_dirs=(outside,))
    assert sandbox.dispatch_environment(tmp_path / "run", layout=free, env={})["PATH"].startswith(str(outside))


@needs_bwrap
def test_bwrap_agent_cannot_see_or_write_a_sibling_agents_home(tmp_path):
    root = _repo(tmp_path, agent="alpha")
    run = Project(root).run_dir("r1")
    project = Project(root)
    alpha = sandbox.dispatch_layout(project, run_dir=run, agent="alpha")
    beta = sandbox.dispatch_layout(project, run_dir=run, agent="beta")
    alpha_home = _home_of(run, "alpha")
    beta_home = _home_of(run, "beta")
    env_a = sandbox.dispatch_environment(run, layout=alpha, env=dict(os.environ))
    env_b = sandbox.dispatch_environment(run, layout=beta, env=dict(os.environ))
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
    env = sandbox.dispatch_environment(run, layout=layout, env=dict(os.environ))
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
