"""Filesystem confinement for agent dispatches — the only bubblewrap-aware module.

A sandboxed process may write inside the run it was given, plus a private /tmp,
the tool cache and whatever the allowlist grants. It may read the repository,
because agents read their own protocols, skills and schemas.

What this deliberately does NOT do: restrict the network, or hide credentials.
Agents need both to reach their model API, so a hostile agent can still send
what it can read. See docs/sandbox.md; that limit is stated, not implied.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

BWRAP = "bwrap"
VERIFY_TIMEOUT_S = 30
INSTALL_HINT = "install it with: sudo apt-get install -y bubblewrap"


class SandboxError(RuntimeError):
    """The sandbox could not be built, or its configuration is not allowed."""


class SandboxUnavailable(SandboxError):
    """bubblewrap is missing, or it did not actually confine a test write."""


def available() -> bool:
    return shutil.which(BWRAP) is not None


def wrap(argv: list[str], *, writable: list[Path], cwd: Path) -> list[str]:
    """`argv` rewritten to run under bubblewrap, writable only where granted.

    Missing writable directories are created first: bubblewrap refuses to bind a
    source that does not exist, and on a fresh machine the tool cache has not
    been created yet — which would otherwise surface as a cryptic bwrap error.
    """
    if not available():
        raise SandboxUnavailable(f"bubblewrap is not installed; {INSTALL_HINT}")
    out = [BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--tmpfs", "/tmp"]
    for path in writable:
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        out += ["--bind", str(resolved), str(resolved)]
    out += ["--chdir", str(Path(cwd).resolve()),
            "--unshare-pid", "--die-with-parent", "--"]
    return out + list(argv)


def verify(writable: list[Path], cwd: Path) -> None:
    """Prove the sandbox confines before trusting it; raise if it does not.

    Runs a throwaway process inside the sandbox that tries to write into $HOME,
    which is never in any writable set. If the file appears on the host, the
    sandbox is not in effect and the caller must refuse to dispatch. This is
    what separates "we passed the right flags" from "we watched it block a
    write", and it catches the mechanism changing underneath us.
    """
    probe = Path.home() / f".scieflow-sandbox-probe-{os.getpid()}"
    probe.unlink(missing_ok=True)
    script = f"touch {shlex.quote(str(probe))} 2>/dev/null; true"
    try:
        subprocess.run(wrap(["/bin/sh", "-c", script], writable=writable, cwd=cwd),
                       capture_output=True, timeout=VERIFY_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailable(f"the sandbox probe could not run: {exc}") from exc
    if probe.exists():
        probe.unlink(missing_ok=True)
        raise SandboxUnavailable(
            "the sandbox did not block a write to $HOME — refusing to dispatch")
