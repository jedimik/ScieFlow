"""Optional DVC transport for chat bundles.

Deliberately narrow: this moves **bundle files only**, one at a time, and
never a directory, a workspace or a zip of one — that is what
`scripts/dvc_sync.py` is for. It is also opt-in: `remote.enabled` in
`config/chats.yml` is false until you turn it on, because a bundle is your
whole conversation history and pushing it puts that on a network.

A pushed bundle is tracked the same way workspace archives are: `dvc add
--to-remote` uploads the file and leaves a small `.dvc` pointer in the repo.
The pointer is what you commit, so another machine can `dvc pull` it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from scieflow.core import config as config_mod

from . import crypto
from .config import ChatsConfig

POINTER_SUFFIX = ".dvc"


class TransportError(Exception):
    """A bundle could not be pushed or pulled."""


def _require(config: ChatsConfig) -> None:
    if not config.remote.enabled:
        raise TransportError(
            "remote sync is off. Set remote.enabled: true in config/chats.yml "
            "to push bundles to DVC storage — it is opt-in because a bundle is "
            "your whole conversation history."
        )


def staging_dir(config: ChatsConfig) -> Path:
    """Where pointers live inside the repo (gitignored except the `.dvc` files)."""
    return config_mod.repo_root() / config.remote.dir


def _remote_args(config: ChatsConfig) -> list[str]:
    return ["-r", config.remote.dvc_remote] if config.remote.dvc_remote else []


def _run(args: list[str], cwd: Path) -> None:
    if shutil.which(args[0]) is None:
        raise TransportError(f"{args[0]} is not on PATH; install it or move bundles by hand")
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        raise TransportError(f"{args[0]} failed to start: {e}") from e
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        raise TransportError(f"{' '.join(args[:3])} failed: {tail[-1] if tail else ''}")


def push(config: ChatsConfig, bundle: Path) -> tuple[Path, str]:
    """Upload one bundle. Returns (pointer path, `git add` hint)."""
    _require(config)
    if not bundle.exists():
        raise TransportError(f"no such bundle: {bundle}")
    if not crypto.is_encrypted(bundle):
        # Not overridable: a plaintext bundle on shared storage is readable by
        # anyone with bucket access.
        raise TransportError(
            f"{bundle.name} is not encrypted — refusing to push it. "
            "Re-run backup without --no-encrypt."
        )
    root = config_mod.repo_root()
    stage = staging_dir(config)
    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / bundle.name
    if staged.resolve() != bundle.resolve():
        shutil.copy2(bundle, staged)
    rel = str(staged.relative_to(root))
    try:
        _run(["dvc", "add", "--to-remote", *_remote_args(config), rel], cwd=root)
    except TransportError:
        staged.unlink(missing_ok=True)
        raise
    # `--to-remote` uploads without caching locally; the staged copy is spent.
    staged.unlink(missing_ok=True)
    pointer = staged.with_name(staged.name + POINTER_SUFFIX)
    hint = f"git add {pointer.relative_to(root)} && git commit -m 'chore(chats): track {bundle.name}'"
    return pointer, hint


def pull(config: ChatsConfig, name: str, dest_dir: Path | None = None) -> Path:
    """Fetch one bundle by file name into `bundle_dir` (or `dest_dir`)."""
    _require(config)
    root = config_mod.repo_root()
    stage = staging_dir(config)
    pointer = stage / (name if name.endswith(POINTER_SUFFIX) else name + POINTER_SUFFIX)
    if not pointer.exists():
        raise TransportError(
            f"no pointer {pointer.name} in {stage}. "
            "Pull the branch that tracks it, or list what is available."
        )
    _run(["dvc", "pull", *_remote_args(config), str(pointer.relative_to(root))], cwd=root)
    fetched = pointer.with_suffix("")
    if not fetched.exists():
        raise TransportError(f"dvc pull left no file at {fetched}")
    target_dir = dest_dir or config.bundle_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / fetched.name
    if target.exists():
        raise TransportError(f"{target} already exists; move it aside first")
    shutil.move(str(fetched), target)
    target.chmod(0o600)
    return target


def available(config: ChatsConfig) -> list[Path]:
    """Bundles this checkout knows about, newest pointer first."""
    stage = staging_dir(config)
    if not stage.is_dir():
        return []
    return sorted(
        stage.glob(f"*{POINTER_SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True
    )
