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
    """If the probe location turns out not to be writable (full disk, a mode
    change between choosing it and using it), we cannot verify confinement. The
    absence of the probe file is ambiguous and must not be treated as proof.
    This test catches the false pass: chosen but inaccessible probe location."""
    home = tmp_path / "home"
    home.mkdir()
    writable = tmp_path / "writable"
    writable.mkdir()
    monkeypatch.setattr(sandbox, "probe_dir", lambda w: home)

    home.chmod(0o500)
    try:
        with pytest.raises(sandbox.SandboxUnavailable, match="not writable"):
            sandbox.verify([writable], cwd=writable)
    finally:
        # Restore permissions so cleanup can happen
        home.chmod(0o755)


def test_probe_dir_prefers_home_when_it_qualifies(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    run = tmp_path / "workspace" / "r1"
    run.mkdir(parents=True)
    assert sandbox.probe_dir([run]) == home.resolve()


def test_probe_dir_skips_a_home_that_is_not_writable(monkeypatch, tmp_path):
    """A coordinator that is itself sandboxed sees a read-only $HOME. Hardcoding
    $HOME would kill every nested dispatch; a parent of the grant still proves
    confinement, because binding workspace/<slug> never makes workspace/ writable."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    run = tmp_path / "workspace" / "r1"
    run.mkdir(parents=True)
    home.chmod(0o500)
    try:
        assert sandbox.probe_dir([run]) == (tmp_path / "workspace").resolve()
    finally:
        home.chmod(0o755)


def test_probe_dir_never_picks_a_location_inside_a_grant(monkeypatch, tmp_path):
    """$HOME inside the writable set would be writable in the sandbox too, so
    its probe could never prove anything; a location outside is chosen instead."""
    home = tmp_path / "workspace" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    chosen = sandbox.probe_dir([tmp_path / "workspace"])
    assert chosen == tmp_path.resolve()


def test_verify_refuses_when_no_probe_location_exists(monkeypatch, tmp_path):
    """Everything is granted, so no write anywhere could prove confinement.
    Refuse rather than run a control that cannot fail."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(sandbox.SandboxError, match="no writable directory outside"):
        sandbox.verify([Path(tmp_path.anchor)], cwd=tmp_path)


@needs_bwrap
def test_concurrent_verifies_do_not_clobber_each_other(tmp_path, monkeypatch):
    """The control files are named per call, not per process. With a pid-keyed
    name, threads delete each other's probe (a false pass with no confinement)
    and each other's sentinel (a false refusal with a real sandbox)."""
    import threading

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    threads, results = [], {}

    def attempt(i: int) -> None:
        run = tmp_path / f"run{i}"
        run.mkdir()
        try:
            sandbox.verify([run], cwd=run)
            results[i] = "passed"
        except sandbox.SandboxError as exc:
            results[i] = f"refused: {exc}"

    # Half the threads run against a real sandbox; the other half against a
    # wrapper that confines nothing, which must never be reported as a pass.
    real_wrap = sandbox.wrap

    def dispatching_wrap(argv, *, writable, cwd):
        if getattr(threading.current_thread(), "defeat", False):
            return ["/bin/sh", "-c", argv[-1]]
        return real_wrap(argv, writable=writable, cwd=cwd)

    monkeypatch.setattr(sandbox, "wrap", dispatching_wrap)

    for i in range(16):
        t = threading.Thread(target=attempt, args=(i,))
        t.defeat = i % 2 == 1
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i, outcome in sorted(results.items()):
        if i % 2 == 1:
            assert outcome.startswith("refused"), f"thread {i} falsely passed: {outcome}"
            assert "did not block" in outcome, f"thread {i} refused for the wrong reason"
        else:
            assert outcome == "passed", f"thread {i} falsely refused: {outcome}"


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


def test_the_uv_cache_lives_inside_the_dispatch_area(monkeypatch, tmp_path):
    """Never the shared host cache: uv hardlinks cache files into .venv, so a
    writable shared cache is a writable host virtualenv."""
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "shared"))
    run = tmp_path / "workspace" / "r1"
    assert sandbox.cache_dir(run) == run.resolve() / ".uv-cache"
    env = sandbox.environment(run)
    assert env["UV_CACHE_DIR"] == str(run.resolve() / ".uv-cache")
    assert str(Path.home() / ".cache" / "uv") != env["UV_CACHE_DIR"]


def test_sub_agent_gets_its_own_run_and_nothing_wider(monkeypatch, tmp_path):
    project = make_project(tmp_path)
    run = project.run_dir("r1")
    writable = sandbox.writable_for(project, run_dir=run, coordinator=False)
    assert run in writable
    assert (Path.home() / ".cache" / "uv") not in writable   # never the shared cache
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
    ("src", "the whole repository"),             # module code is never an agent's to write
    ("config/sandbox.yml", "cannot grant write access to itself"),
])
def test_allowlist_rejects_dangerous_entries(tmp_path, entry, match):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        f"writable:\n  - path: {entry}\n    reason: nope\n")
    with pytest.raises(sandbox.SandboxError, match=match):
        sandbox.allowlist_paths(project)


def test_malformed_yaml_raises_sandboxerror(tmp_path):
    """Malformed YAML in the allowlist must raise SandboxError, not yaml.YAMLError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text("writable: [unclosed bracket")
    with pytest.raises(sandbox.SandboxError, match="malformed YAML"):
        sandbox.allowlist_paths(project)


def test_top_level_list_raises_sandboxerror(tmp_path):
    """YAML document that parses to a list must raise SandboxError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text("- path: config/journals\n")
    with pytest.raises(sandbox.SandboxError, match="must be a mapping"):
        sandbox.allowlist_paths(project)


def test_top_level_scalar_raises_sandboxerror(tmp_path):
    """YAML document that parses to a bare scalar must raise SandboxError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text("just a string\n")
    with pytest.raises(sandbox.SandboxError, match="must be a mapping"):
        sandbox.allowlist_paths(project)


def test_writable_as_string_raises_sandboxerror_and_grants_nothing(tmp_path):
    """If writable is a string instead of a list, it must raise SandboxError.
    This is the silent-over-grant case: `writable: config` would iterate over
    characters and grant bogus single-letter directories. Prove it raises instead."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text("writable: config\n")
    with pytest.raises(sandbox.SandboxError, match="must be a list"):
        sandbox.allowlist_paths(project)


def test_entry_with_missing_path_raises_sandboxerror(tmp_path):
    """An entry without a 'path:' key must raise SandboxError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - reason: no path here\n")
    with pytest.raises(sandbox.SandboxError, match="no 'path:'"):
        sandbox.allowlist_paths(project)


def test_entry_with_empty_path_raises_sandboxerror(tmp_path):
    """An entry with an empty 'path:' value must raise SandboxError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - path: \"\"\n    reason: empty\n")
    with pytest.raises(sandbox.SandboxError, match="no 'path:'"):
        sandbox.allowlist_paths(project)


def test_bare_string_entry_raises_sandboxerror(tmp_path):
    """A bare string entry (- config/journals without path: key) must raise SandboxError."""
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - config/journals\n")
    with pytest.raises(sandbox.SandboxError, match="must be a mapping"):
        sandbox.allowlist_paths(project)


# -- the per-run escape hatch, which lives outside every run ----------------

def test_no_unsandboxed_runs_by_default(tmp_path):
    project = make_project(tmp_path)
    assert sandbox.unsandboxed_runs(project) == set()
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - path: config/journals\n    reason: journal cache\n")
    assert sandbox.unsandboxed_runs(project) == set()


def test_a_listed_slug_may_dispatch_unsandboxed(tmp_path):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "unsandboxed_runs:\n  - slug: r1\n    reason: needs docker\n")
    assert sandbox.unsandboxed_runs(project) == {"r1"}


@pytest.mark.parametrize("body, match", [
    ("unsandboxed_runs: r1\n", "must be a list"),
    ("unsandboxed_runs:\n  - r1\n", "must be a mapping"),
    ("unsandboxed_runs:\n  - reason: no slug\n", "no 'slug:'"),
    ("unsandboxed_runs:\n  - slug: \"\"\n    reason: empty\n", "no 'slug:'"),
    ("unsandboxed_runs:\n  - slug: r1\n", "needs a 'reason:'"),
    ("unsandboxed_runs:\n  - slug: ../elsewhere\n    reason: nope\n", "not a path"),
])
def test_unsandboxed_runs_rejects_malformed_entries(tmp_path, body, match):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(body)
    with pytest.raises(sandbox.SandboxError, match=match):
        sandbox.unsandboxed_runs(project)


def test_an_unreadable_allowlist_raises_sandboxerror(tmp_path):
    """An unreadable config/sandbox.yml is a refusal, not a raw OSError."""
    project = make_project(tmp_path)
    path = tmp_path / "config" / "sandbox.yml"
    path.write_text("writable: []\n")
    path.chmod(0o000)
    try:
        with pytest.raises(sandbox.SandboxError):
            sandbox.allowlist_paths(project)
    finally:
        path.chmod(0o644)
