import shutil
import subprocess
from pathlib import Path

import pytest

from scieflow.core import sandbox

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")


def test_available_reflects_the_binary(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.available() is False
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    assert sandbox.available() is True


def test_wrap_without_bubblewrap_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.SandboxUnavailable, match="apt-get install"):
        sandbox.wrap(["echo", "hi"], writable=[tmp_path], cwd=tmp_path)


def test_wrap_builds_a_read_only_root_with_granted_binds(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    run = tmp_path / "run"
    run.mkdir()
    argv = sandbox.wrap(["agent", "--flag"], writable=[run], cwd=run)
    assert argv[0] == sandbox.BWRAP
    joined = " ".join(argv)
    assert "--ro-bind / /" in joined              # everything read-only first
    assert f"--bind {run} {run}" in joined        # then the grant on top
    assert "--tmpfs /tmp" in joined
    assert "--die-with-parent" in joined          # must not outlive a cancelled job
    assert argv[-2:] == ["agent", "--flag"]       # the real command last, after --
    assert argv[argv.index("--chdir") + 1] == str(run)


def test_wrap_creates_missing_writable_directories(monkeypatch, tmp_path):
    """bubblewrap refuses to bind a source that does not exist — a fresh machine
    has no uv cache, and the failure would otherwise be a cryptic bwrap error."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    absent = tmp_path / "not" / "created" / "yet"
    sandbox.wrap(["echo"], writable=[absent], cwd=tmp_path)
    assert absent.is_dir()


@needs_bwrap
def test_verify_passes_with_a_real_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    sandbox.verify([tmp_path / "run"], cwd=tmp_path / "run")      # does not raise


@needs_bwrap
def test_a_sandboxed_process_cannot_write_outside_its_grant(tmp_path):
    run, outside = tmp_path / "run", tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    argv = sandbox.wrap(
        ["/bin/sh", "-c", f"touch {run}/inside; touch {outside}/escaped"],
        writable=[run], cwd=run)
    subprocess.run(argv, capture_output=True, timeout=60, check=False)
    assert (run / "inside").exists()              # the grant works
    assert not (outside / "escaped").exists()     # and nothing else does


def test_verify_refuses_a_wrapper_that_does_not_confine(monkeypatch, tmp_path):
    """The defeat test: hand verify() a wrapper that runs the command unchanged.
    If verify() can be fooled, every other guarantee here is decoration."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/bin/sh", "-c", argv[-1]])
    with pytest.raises(sandbox.SandboxUnavailable, match="did not block"):
        sandbox.verify([tmp_path], cwd=tmp_path)


def test_verify_reports_a_probe_that_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/nonexistent/binary"])
    with pytest.raises(sandbox.SandboxUnavailable, match="could not run"):
        sandbox.verify([tmp_path], cwd=tmp_path)


def test_verify_raises_when_sandboxed_command_never_runs(monkeypatch, tmp_path):
    """If the wrapper returns a command that fails (e.g., /bin/false), the
    sentinel never gets written, and verify() must detect this and refuse."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    writable = tmp_path / "writable"
    writable.mkdir()
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/bin/false"])
    with pytest.raises(sandbox.SandboxUnavailable, match="did not run the probe"):
        sandbox.verify([writable], cwd=writable)


def test_verify_raises_when_probe_location_is_not_writable(monkeypatch, tmp_path):
    """If $HOME is not writable (mode 0500, full disk, read-only mount), we cannot
    verify confinement. The absence of the probe file is ambiguous and must not be
    treated as proof. This test catches the false pass: writable but inaccessible home."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    writable = tmp_path / "writable"
    writable.mkdir()

    # Make home unwritable
    home.chmod(0o500)
    try:
        with pytest.raises(sandbox.SandboxUnavailable, match="not writable"):
            sandbox.verify([writable], cwd=writable)
    finally:
        # Restore permissions so cleanup can happen
        home.chmod(0o755)


def test_verify_raises_sandboxerror_for_file_writable(monkeypatch, tmp_path):
    """verify() must raise SandboxError (not NotADirectoryError) when a writable
    entry is a file. Callers catch SandboxError, not NotADirectoryError."""
    writable_file = tmp_path / "writable.txt"
    writable_file.write_text("test")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    with pytest.raises(sandbox.SandboxError):
        sandbox.verify([writable_file], cwd=tmp_path)


def test_wrap_raises_sandboxerror_for_file_writable(monkeypatch, tmp_path):
    """wrap() must raise SandboxError (not NotADirectoryError) when a writable
    entry is a file. Callers catch SandboxError, not NotADirectoryError."""
    writable_file = tmp_path / "writable.txt"
    writable_file.write_text("test")

    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(["echo", "hi"], writable=[writable_file], cwd=tmp_path)


def test_writable_file_error_message_names_path(monkeypatch, tmp_path):
    """The error message from a file writable grant must name the offending path."""
    writable_file = tmp_path / "writable.txt"
    writable_file.write_text("test")

    with pytest.raises(sandbox.SandboxError, match=str(writable_file)):
        sandbox.wrap(["echo", "hi"], writable=[writable_file], cwd=tmp_path)


from scieflow.core.project import Project


def make_project(tmp_path) -> Project:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "workspace" / "r1").mkdir(parents=True)
    return Project(tmp_path)


def test_tool_cache_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "custom"))
    assert sandbox.tool_cache() == tmp_path / "custom"
    monkeypatch.delenv("UV_CACHE_DIR")
    assert sandbox.tool_cache() == Path.home() / ".cache" / "uv"


def test_sub_agent_gets_its_own_run_and_nothing_wider(tmp_path):
    project = make_project(tmp_path)
    run = project.run_dir("r1")
    writable = sandbox.writable_for(project, run_dir=run, coordinator=False)
    assert run in writable
    assert sandbox.tool_cache() in writable
    assert project.workspace_root not in writable      # not the whole tree
    assert project.root not in writable                # and certainly not the repo


def test_coordinator_gets_the_workspace_tree(tmp_path):
    project = make_project(tmp_path)
    writable = sandbox.writable_for(project, run_dir=None, coordinator=True)
    assert project.workspace_root in writable
    assert project.root not in writable


def test_a_sub_agent_without_a_run_is_refused_not_promoted(tmp_path):
    project = make_project(tmp_path)
    with pytest.raises(sandbox.SandboxError, match="must name the run"):
        sandbox.writable_for(project, run_dir=None, coordinator=False)


def test_a_slug_with_spaces_still_works(tmp_path):
    """Project.SLUG_RE permits spaces, so `workspace/my run/` is a legal run."""
    project = make_project(tmp_path)
    run = project.run_dir("my run")
    run.mkdir(parents=True)
    writable = sandbox.writable_for(project, run_dir=run, coordinator=False)
    assert run in writable
    argv = sandbox.wrap(["echo"], writable=writable, cwd=run)
    assert str(run) in argv            # one argv element, never word-split
    assert argv[argv.index("--chdir") + 1] == str(run)


def test_allowlist_absent_grants_nothing_extra(tmp_path):
    project = make_project(tmp_path)
    assert sandbox.allowlist_paths(project) == []


def test_allowlist_grants_a_listed_path(tmp_path):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - path: config/journals\n    reason: journal cache\n")
    assert sandbox.allowlist_paths(project) == [(tmp_path / "config" / "journals").resolve()]


@pytest.mark.parametrize("entry, match", [
    ("../outside", "escapes the repository"),
    ("/etc", "escapes the repository"),
    (".", "the whole repository"),
    ("config", "the whole repository"),          # would include sandbox.yml itself
    ("config/sandbox.yml", "cannot grant write access to itself"),
])
def test_allowlist_rejects_dangerous_entries(tmp_path, entry, match):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        f"writable:\n  - path: {entry}\n    reason: nope\n")
    with pytest.raises(sandbox.SandboxError, match=match):
        sandbox.allowlist_paths(project)
