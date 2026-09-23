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
def test_verify_passes_with_a_real_sandbox(tmp_path):
    sandbox.verify([tmp_path], cwd=tmp_path)      # does not raise


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
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/bin/sh", "-c", argv[-1]])
    with pytest.raises(sandbox.SandboxUnavailable, match="did not block"):
        sandbox.verify([tmp_path], cwd=tmp_path)


def test_verify_reports_a_probe_that_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/nonexistent/binary"])
    with pytest.raises(sandbox.SandboxUnavailable, match="could not run"):
        sandbox.verify([tmp_path], cwd=tmp_path)
