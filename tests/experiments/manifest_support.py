"""Shared synthetic helpers for the M-D2 t2 manifest, verify and CSV tests (no patient data).

Loaded by the test modules through importlib (the tests run with --import-mode=importlib).
Apptainer is never run: `FakeApptainer` stands in for `subprocess.run` inside the modules
under test only. Its `apptainer exec ... python ...` calls run the inner command on the host
interpreter, so the stage script still produces real artifacts; `pip list` returns fixed lines.
Git calls are delegated to the real `git` binary, always against a throw-away repository.
"""

from __future__ import annotations

import subprocess as _real_subprocess
import sys
import types
from pathlib import Path

_REAL_RUN = _real_subprocess.run


def git(repo, *args, check=True):
    proc = _REAL_RUN(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")
    return proc


def init_repo(root) -> None:
    """Make `root` a throw-away git repository whose current tree is committed."""
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")


def make_sif(project, stage: str = "scale") -> Path:
    """A fake container image file; its bytes are never executed."""
    sif = project / "pipelines" / "demo" / "stages" / stage / "env" / f"{stage}.sif"
    sif.parent.mkdir(parents=True, exist_ok=True)
    sif.write_bytes(b"fake-sif-image\n")
    return sif


class FakeApptainer:
    """Replacement for `subprocess.run` in a module under test."""

    def __init__(self, pip_lines=("imageio==2.37.0", "numpy==2.2.1"), pip_rc=0,
                 pip_exc=None, version="apptainer version 1.4.0"):
        self.pip_lines = list(pip_lines)
        self.pip_rc = pip_rc
        self.pip_exc = pip_exc
        self.version = version
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[0] == "git":
            return _REAL_RUN(cmd, **kwargs)
        if cmd[0] == "apptainer":
            if cmd[1] == "--version":
                return _real_subprocess.CompletedProcess(cmd, 0, stdout=self.version + "\n", stderr="")
            if "pip" in cmd:
                if self.pip_exc is not None:
                    raise self.pip_exc
                return _real_subprocess.CompletedProcess(
                    cmd, self.pip_rc, stdout="\n".join(self.pip_lines) + "\n", stderr="")
            if cmd[1] == "exec":
                sif_index = next(i for i, c in enumerate(cmd) if c.endswith(".sif"))
                assert cmd[sif_index + 1] == "python", cmd
                inner = [sys.executable, *cmd[sif_index + 2:]]
                return _REAL_RUN(inner, **kwargs)
        return _REAL_RUN(cmd, **kwargs)


def patch_subprocess(monkeypatch, module, run_fn) -> None:
    """Replace `subprocess` inside one module only, with a namespace exposing `run`."""
    ns = types.SimpleNamespace(
        run=run_fn,
        TimeoutExpired=_real_subprocess.TimeoutExpired,
        CompletedProcess=_real_subprocess.CompletedProcess,
    )
    monkeypatch.setattr(module, "subprocess", ns)
