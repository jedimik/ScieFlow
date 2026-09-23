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

import yaml

from scieflow.core.project import Project

BWRAP = "bwrap"
VERIFY_TIMEOUT_S = 30
INSTALL_HINT = "install it with: sudo apt-get install -y bubblewrap"


class SandboxError(RuntimeError):
    """The sandbox could not be built, or its configuration is not allowed."""


class SandboxUnavailable(SandboxError):
    """bubblewrap is missing, or it did not actually confine a test write."""


def available() -> bool:
    return shutil.which(BWRAP) is not None


def _validate_writable(writable: list[Path]) -> None:
    """Validate that each writable entry is a directory (or does not exist yet).

    Raises SandboxError if any entry exists and is not a directory, which would
    cause cryptic errors when trying to create sentinel files or bind mounts.
    """
    for entry in writable:
        path = Path(entry).resolve()
        if path.exists() and not path.is_dir():
            raise SandboxError(
                f"writable grant must be a directory, not a file: {path}")


def wrap(argv: list[str], *, writable: list[Path], cwd: Path) -> list[str]:
    """`argv` rewritten to run under bubblewrap, writable only where granted.

    As a side effect, creates any missing writable directories on the host:
    bubblewrap refuses to bind a source that does not exist, and on a fresh
    machine the tool cache has not been created yet — which would otherwise
    surface as a cryptic bwrap error. This is part of the contract and every
    caller depends on it.
    """
    _validate_writable(writable)
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
    which is never in any writable set. Proves three things: the probe location
    is actually writable on the host (so absence means confinement, not just
    permission denied); the sandboxed command ran (with a sentinel file in a
    granted path); and the forbidden write was blocked. Raises if any check fails.

    Requires at least one writable path to host a control file.
    """
    if not writable:
        raise SandboxError("verify() requires at least one writable path for the control")

    _validate_writable(writable)

    probe = Path.home() / f".scieflow-sandbox-probe-{os.getpid()}"
    sentinel = Path(writable[0]).resolve() / f".scieflow-verify-sentinel-{os.getpid()}"

    try:
        # Positive control 1: prove the probe location is writable on the host.
        # If this fails, we cannot draw any conclusion from its later absence.
        probe.unlink(missing_ok=True)
        try:
            probe.touch()
            probe.unlink()
        except OSError as exc:
            raise SandboxUnavailable(
                f"confinement could not be verified because the probe location "
                f"is not writable: {exc}") from exc

        # Positive control 2: run a command that writes a sentinel into the granted path,
        # then tries to write to the forbidden path. If the sentinel doesn't appear,
        # the sandboxed command never ran.
        sentinel.unlink(missing_ok=True)
        script = (f"touch {shlex.quote(str(sentinel))} && "
                  f"touch {shlex.quote(str(probe))} 2>/dev/null; true")
        try:
            subprocess.run(wrap(["/bin/sh", "-c", script], writable=writable, cwd=cwd),
                           capture_output=True, timeout=VERIFY_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxUnavailable(f"the sandbox probe could not run: {exc}") from exc

        # Check positive control 2: did the sandboxed command actually run?
        if not sentinel.exists():
            raise SandboxUnavailable(
                "the sandbox did not run the probe at all — refusing to dispatch")

        # Positive control 3: was the forbidden write blocked?
        if probe.exists():
            raise SandboxUnavailable(
                "the sandbox did not block a write to $HOME — refusing to dispatch")
    finally:
        # Clean up on every path through this function, defensively.
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            sentinel.unlink(missing_ok=True)
        except Exception:
            pass


ALLOWLIST_FILE = "config/sandbox.yml"


def tool_cache() -> Path:
    """Where uv keeps its cache. Writable in every sandbox: `uv run` fails
    outright without it, so every agent that runs a scieflow command needs it."""
    override = os.environ.get("UV_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "uv"


def allowlist_paths(project: Project) -> list[Path]:
    """Extra writable paths from config/sandbox.yml, deny-by-default.

    The file lives in config/, which is never writable inside a sandbox, so an
    agent cannot extend its own permissions — adding an entry is a human edit.
    """
    path = project.root / ALLOWLIST_FILE
    if not path.exists():
        return []

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SandboxError(
            f"allowlist file is malformed YAML: {path}\n{exc}") from exc

    if data is None:
        return []

    if not isinstance(data, dict):
        raise SandboxError(
            f"allowlist file must be a mapping, not {type(data).__name__}: {path}")

    writable = data.get("writable")
    if writable is None:
        return []

    if not isinstance(writable, list):
        raise SandboxError(
            f"allowlist 'writable:' must be a list, not {type(writable).__name__}: {path}")

    root = project.root.resolve()
    forbidden = {root, (root / "config").resolve()}
    out: list[Path] = []

    for i, entry in enumerate(writable):
        if not isinstance(entry, dict):
            raise SandboxError(
                f"allowlist entry {i} must be a mapping with a 'path:' key, not {type(entry).__name__}: {path}")

        raw = entry.get("path")
        if not raw:
            raise SandboxError(
                f"allowlist entry {i} has no 'path:' or an empty path: {path}")

        candidate = (root / str(raw)).resolve()
        if candidate in forbidden:
            raise SandboxError(
                f"allowlist entry {raw!r} would grant the whole repository or all of "
                "config/; grant the specific directory instead")
        if root not in candidate.parents:
            raise SandboxError(f"allowlist entry {raw!r} escapes the repository")
        if candidate == (root / ALLOWLIST_FILE).resolve():
            raise SandboxError("the allowlist cannot grant write access to itself")
        out.append(candidate)

    return out


def writable_for(project: Project, *, run_dir: Path | None,
                 coordinator: bool) -> list[Path]:
    """What this dispatch may write. The caller declares which kind it is —
    the sandbox never guesses, because guessing wrong widens the boundary."""
    if coordinator:
        base = [project.workspace_root]
    elif run_dir is None:
        raise SandboxError(
            "a sandboxed agent dispatch must name the run it belongs to; "
            "use --no-sandbox for a one-off dispatch outside a run")
    else:
        base = [Path(run_dir)]
    return [*base, tool_cache(), *allowlist_paths(project)]
