"""S2-S5 code/security review, fix round 1 (coordinator t_363a39ff, review t_d91ce2af).

Each test names the review finding it closes. Findings 1 (child-process tests), 3b and 3c (the
doctor marker) and the CLI half of 11 are in tests/core/test_agent_run.py and
tests/core/test_sandbox_verify.py. Real-bwrap tests carry needs_bwrap. Every token and home here
is fake and lives in a tmp dir; no real credential, CLI or home file is read.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, sandbox
from scieflow.core.project import Project

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
not_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
W = Path(sandbox.__file__).resolve().parents[3]
CODEX_BODY = "#!/bin/sh\necho codex-ran\n"


# --- helpers -----------------------------------------------------------------

def _home(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("home").resolve()
    monkeypatch.setenv("HOME", str(home))
    (home / ".local" / "bin").mkdir(parents=True)
    return home


def _exe(path: Path, body: str = "#!/bin/sh\necho ok\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _write_token(path: Path, data: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def _root(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "agents.yml").write_text("agents: {}\n")
    (root / "workspace" / "r1").mkdir(parents=True, exist_ok=True)
    return root


def _cred(path: Path) -> "sandbox.Credential":
    return sandbox.Credential("a", "env", var="SOME_VAR", file=Path(path), reason="r")


def _codex_install(home: Path, version: str = "0.1.0") -> Path:
    """codex, as the store lays it out: ~/.local/bin/codex -> .codex/.../current -> releases/<v>."""
    release = home / ".codex" / "packages" / "standalone" / "releases" / version
    _exe(release / "bin" / "codex", CODEX_BODY)
    (release / "codex-resources").mkdir()
    (release / "codex-resources" / "marker").write_text("res\n")
    current = home / ".codex" / "packages" / "standalone" / "current"
    current.symlink_to(f"releases/{version}")
    (home / ".local" / "bin" / "codex").symlink_to(current / "bin" / "codex")
    return release


def _store_layout(tmp_path: Path, home: Path) -> sandbox.Layout:
    root = _root(tmp_path)
    return sandbox.dispatch_layout(Project(root), run_dir=root / "workspace" / "r1", agent="tracer",
                                   launcher=home / ".local" / "bin" / "codex")


# --- finding 2 (MAJOR-tier MINOR): read_token forbids install roots and system trees ----------

def test_f2_a_token_inside_an_install_root_is_refused_without_echoing_it(tmp_path):
    inst = (tmp_path / "install").resolve()
    tok = _write_token(inst / "a.token", b"FAKE-F2-VALUE\n")
    launcher = _exe(tmp_path / "bin" / "tool")
    layout = sandbox.Layout(installs=(inst,), home=None)
    with pytest.raises(sandbox.SandboxError, match="grant or bind") as exc:
        sandbox.attach_credential(layout, (_cred(tok),), agent="a", argv=["tool"], located=launcher,
                                  writable=[tmp_path / "run"], root=_root(tmp_path))
    assert str(tok) in str(exc.value)
    assert "FAKE-F2-VALUE" not in str(exc.value)


def test_f2_a_token_under_a_system_tree_is_refused_without_echoing_it(tmp_path, monkeypatch):
    sysdir = (tmp_path / "sys").resolve()
    tok = _write_token(sysdir / "a.token", b"FAKE-F2-SYS\n")
    monkeypatch.setattr(sandbox, "SYSTEM_TREES", (sysdir, Path("/usr")))
    launcher = _exe(tmp_path / "bin" / "tool")
    with pytest.raises(sandbox.SandboxError, match="grant or bind") as exc:
        sandbox.attach_credential(sandbox.Layout(), (_cred(tok),), agent="a", argv=["tool"],
                                  located=launcher, writable=[tmp_path / "run"], root=_root(tmp_path))
    assert "FAKE-F2-SYS" not in str(exc.value)


# --- finding 11: read_token refuses a token with more than one hard link ----------------------

def test_f11_a_hard_linked_token_is_refused_wherever_the_other_link_lies(tmp_path):
    tok = _write_token(tmp_path / "t" / "a.token", b"FAKE-F11-VALUE\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.link(tok, elsewhere / "alias.token")
    with pytest.raises(sandbox.SandboxError, match="hard link") as exc:
        sandbox.read_token(_cred(tok), forbidden=())
    assert "FAKE-F11-VALUE" not in str(exc.value)


# --- finding 4: the A1 store walk refuses hard links and unreadable subtrees ------------------

def test_f4_a_hard_linked_file_inside_a_store_install_root_is_refused(tmp_path, tmp_path_factory,
                                                                     monkeypatch):
    home = _home(tmp_path_factory, monkeypatch)
    release = _codex_install(home)
    os.link(release / "codex-resources" / "marker", release / "alias")   # same inode, another name
    with pytest.raises(sandbox.SandboxError, match="hard link"):
        _store_layout(tmp_path, home)


@not_root
def test_f4_an_unreadable_subtree_inside_a_store_install_root_is_refused(tmp_path, tmp_path_factory,
                                                                        monkeypatch):
    home = _home(tmp_path_factory, monkeypatch)
    release = _codex_install(home)
    locked = release / "codex-resources" / "locked"
    locked.mkdir()
    (locked / "inner").write_text("x\n")
    locked.chmod(0)
    try:
        with pytest.raises(sandbox.SandboxError, match="unreadable"):
            _store_layout(tmp_path, home)
    finally:
        locked.chmod(0o755)


# --- finding 5: a launcher under a readable-covered directory reaches the sandbox PATH --------

def test_f5_a_launcher_under_the_bound_project_root_reaches_the_sandbox_path(tmp_path, monkeypatch):
    root = _root(tmp_path)
    exe = _exe(root / "tools" / "bin" / "claude")
    monkeypatch.setenv("PATH", f"{exe.parent}:{os.environ['PATH']}")
    run = root / "workspace" / "r1"
    located = sandbox.host_launcher("claude", run)
    assert located == exe.absolute()
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="tracer", launcher=located)
    env = sandbox.dispatch_environment(run, layout=layout, env={})
    assert str(exe.parent) in env["PATH"].split(":")
    captured = agent_run._capture_execution("claude", run, env)[0]
    assert captured == located, "the dispatch would refuse: the launcher is not on its PATH"


# --- finding 6: R-S2-e paths, which had no test (they already behave correctly) ---------------

def test_f6_a_symlinked_workspace_is_refused(tmp_path, tmp_path_factory, monkeypatch):
    _home(tmp_path_factory, monkeypatch)
    root = (tmp_path / "repo").resolve()
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("agents: {}\n")
    real = tmp_path / "elsewhere"
    (real / "r1").mkdir(parents=True)
    (root / "workspace").symlink_to(real, target_is_directory=True)
    with pytest.raises(sandbox.SandboxError, match="symbolic link"):
        sandbox.dispatch_layout(Project(root), run_dir=real / "r1", agent="a")


def test_f6_a_hidden_path_that_cannot_be_checked_is_refused(tmp_path):
    with pytest.raises(sandbox.SandboxError, match="cannot be checked"):
        sandbox._hidden_args((tmp_path / "nope",), [tmp_path / "grant"])


@needs_bwrap
def test_f6_a_directory_secret_and_a_dotenv_file_read_empty_inside_the_sandbox(tmp_path, tmp_path_factory,
                                                                              monkeypatch):
    _home(tmp_path_factory, monkeypatch)
    root = _root(tmp_path)
    (root / ".env").mkdir()
    (root / ".env" / "inner").write_text("INNER-FAKE\n")
    (root / ".env.prod").write_text("PROD-FAKE\n")
    run = root / "workspace" / "r1"
    layout = sandbox.dispatch_layout(Project(root), run_dir=run, agent="a")
    script = f"ls -A {root}/.env | wc -l; cat {root}/.env.prod | wc -c"
    argv = sandbox.wrap(["/bin/sh", "-c", script], writable=[run], cwd=run, layout=layout)
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[:2] == ["0", "0"], out.stdout


# --- finding 3a: the host marker is never placed in a directory the sandbox overlays ---------

def test_f3a_the_host_marker_is_never_placed_in_tmp(tmp_path):
    """/tmp is replaced by the sandbox's own tmpfs (`_system_args`): a marker there is invisible
    inside by construction, so a leak could never be seen."""
    grant = Path("/tmp/scieflow-f3a-grant")
    assert sandbox.marker_location(outer=[grant], binds=[grant], candidates=[Path("/tmp")]) is None


# --- finding 10: a bind credential may not name a runtime-socket area ------------------------

def test_f10_a_bind_source_under_a_runtime_socket_area_is_refused(tmp_path):
    with pytest.raises(sandbox.SandboxError, match="runtime socket"):
        sandbox.check_not_denied(Path(f"/run/user/{os.getuid()}/bus"), kind="bind")


def test_f10_a_credentials_bind_of_run_is_refused_at_parse(tmp_path):
    root = _root(tmp_path)
    (root / "config" / "sandbox.yml").write_text(yaml.safe_dump({"credentials": {"a": {
        "mode": "bind", "source": "/run", "target": ".codex/auth.json", "reason": "r"}}}))
    with pytest.raises(sandbox.SandboxError, match="runtime socket"):
        sandbox.credentials_of(Project(root))


# --- NIT 8: a scratch/agent-home is never packed, whatever src_dir is -----------------------

def test_n8_build_zip_from_a_scratch_dir_never_packs_its_agent_home(tmp_path):
    from sflib import archive

    run = tmp_path / "run"
    (run / "scratch" / "agent-home" / "claude").mkdir(parents=True)
    (run / "scratch" / "agent-home" / "claude" / "session.json").write_text("cli state")
    (run / "scratch" / "other").mkdir()
    (run / "scratch" / "other" / "x.txt").write_text("scratch")
    out = tmp_path / "out.zip"
    archive.build_zip(run / "scratch", out, skip_rebuildable=False)
    names = set(zipfile.ZipFile(out).namelist())
    assert not any("agent-home" in n for n in names), names
    assert "other/x.txt" in names


# --- NIT 7: the module guard is the last statement, so the module is complete when it runs ---

def test_n7_the_sandbox_main_guard_is_the_last_statement():
    """The doctor entry (`python -m scieflow.core.sandbox doctor`) is exercised end to end by
    tests/core/test_sandbox_verify.py::test_s5_doctor_entry_proves_confinement_and_prints_ok_lines."""
    text = (W / "src" / "scieflow" / "core" / "sandbox.py").read_text()
    assert text.rstrip().endswith('if __name__ == "__main__":\n    raise SystemExit(main())')
