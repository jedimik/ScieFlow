"""The per-run git repo that gives a manuscript's history a readable form.

Bare, no working tree: every write is git plumbing — `hash-object -w
--stdin` for a blob, `mktree` for a tree, `commit-tree` for a commit,
`update-ref` to move a branch — and every read is `rev-parse`, `ls-tree` or
`show`. Nothing here ever runs `git add`, `git checkout` or `--work-tree`;
there is no working tree to check anything out into.

The repo is *derived*, never authoritative: a run's workspace files are the
truth, and this repo is a history built by replaying writes to them. That is
why `ensure_repo` discards and re-inits a repo it finds corrupt rather than
trying to repair it — there is nothing here worth repairing that isn't
already in the workspace.

Every branch this feature will ever create — `main`, and a `draft/<agent>`
per drafting agent — needs a common ancestor, or `git diff` between two of
them compares two unrelated histories instead of the changes between them.
`EMPTY_ROOT_REF` is that ancestor: one commit with an empty tree, created
once by `ensure_repo` and never moved again. Recreating it would reparent
every branch already descended from it.

`shutil` and `subprocess` are imported at module level under those exact
names — a test monkeypatches `provenance.shutil.which` to simulate a host
with no `git`, and that patch only works because this module's own
attribute is the same module object everything else imports.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from itertools import chain
from pathlib import Path

REPO_DIR = "provenance.git"
EMPTY_ROOT_REF = "refs/heads/_root"
GIT = "git"
_TIMEOUT_S = 30

#: The commit identity every write in this repo is attributed to. A run's
#: writes are made by ScieFlow orchestrating agents, not by whatever human
#: happens to be running the serve process, and a fresh machine may have no
#: global `user.email` at all — which makes `commit-tree` fail outright with
#: no writable identity configured. Fixing it here means every commit this
#: feature ever makes is attributable to the same identity regardless of
#: host configuration.
_AUTHOR_NAME = "ScieFlow"
_AUTHOR_EMAIL = "provenance@scieflow.local"

#: The only variables a git subprocess inherits from the serve process.
#: `PATH` is needed to find `git` itself; `LANG` only decides how git's own
#: messages are encoded. Everything else — notably the serve process's model
#: API keys — is simply absent, because this dict is built explicitly rather
#: than being a copy of `os.environ`. See `preview.compile_env`, which
#: applies the identical discipline for `latexmk`.
_INHERITED = ("PATH", "LANG")


class ProvenanceError(ValueError):
    """A git operation failed, or the repo could not be made usable."""


def available() -> bool:
    return shutil.which(GIT) is not None


def repo_path(ws: Path) -> Path:
    return Path(ws) / REPO_DIR


def _env() -> dict[str, str]:
    """The whole environment a git subprocess runs with, built once here so
    no other call site in this module (or a later one) can hand a git
    subprocess the serve process's own environment by accident. See the
    module docstring and `_AUTHOR_NAME`/`_AUTHOR_EMAIL` for why."""
    env = {name: os.environ[name] for name in _INHERITED if name in os.environ}
    env["GIT_AUTHOR_NAME"] = _AUTHOR_NAME
    env["GIT_COMMITTER_NAME"] = _AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = _AUTHOR_EMAIL
    env["GIT_COMMITTER_EMAIL"] = _AUTHOR_EMAIL
    return env


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    """Run `git --git-dir <repo> <args>`, with `stdin` (if given) piped in as
    text, and return stdout with trailing whitespace stripped.

    Every argument is its own argv element — never a shell string — so a
    value that looks like a flag (a commit message, a filename) is always
    taken literally rather than parsed. A non-zero exit, a missing `git`
    binary, or a hung process past `_TIMEOUT_S` all become `ProvenanceError`
    rather than propagating a `CalledProcessError`/`OSError`/
    `TimeoutExpired`, so every caller in this module has exactly one
    exception to handle.
    """
    argv = [GIT, "--git-dir", str(repo), *args]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT_S,
            input=stdin, env=_env(),
        )
    except FileNotFoundError as exc:
        raise ProvenanceError(f"git not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProvenanceError(
            f"git timed out after {_TIMEOUT_S}s: {' '.join(args)}") from exc
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1:] or [""]
        raise ProvenanceError(f"git {' '.join(args)} failed: {tail[0]}")
    return result.stdout.strip()


def _blob(repo: Path, data: str) -> str:
    return _git(repo, "hash-object", "-w", "--stdin", stdin=data)


def _tree(repo: Path, entries: list[tuple[str, str, str, str]]) -> str:
    """Build a tree from `(mode, kind, sha, name)` entries via `mktree`.

    An empty `entries` list still goes through `mktree` with an empty stdin
    rather than being special-cased, because `mktree` on empty input is
    itself how the empty tree's well-known SHA is produced — there is
    nothing more "correct" this function could return by skipping the call.
    """
    stdin = "\n".join(f"{mode} {kind} {sha}\t{name}" for mode, kind, sha, name in entries)
    return _git(repo, "mktree", stdin=stdin)


def _commit(repo: Path, tree: str, parents: list[str], message: str) -> str:
    parent_args = list(chain.from_iterable(("-p", p) for p in parents))
    return _git(repo, "commit-tree", tree, *parent_args, "-m", message)


def _update_ref(repo: Path, ref: str, sha: str) -> None:
    _git(repo, "update-ref", ref, sha)


def _ref_sha(repo: Path, ref: str) -> str | None:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", ref) or None
    except ProvenanceError:
        return None


def _is_bare_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-bare-repository") == "true"
    except ProvenanceError:
        return False


def ensure_repo(ws: Path) -> Path:
    """Make `repo_path(ws)` a usable bare repo with `EMPTY_ROOT_REF` set,
    creating or rebuilding it as needed, and return its path.

    Idempotent: calling this again on an already-good repo touches nothing
    — in particular it never recreates `EMPTY_ROOT_REF` once it exists,
    since every branch will eventually descend from that one commit and
    moving it would reparent all of them.

    A repo that exists but is not a usable bare repo (corrupted, or
    replaced by a plain file) is discarded and reinitialised rather than
    repaired: the repo is derived from the workspace, never the other way
    round, so there is nothing here worth preserving that a fresh `init`
    plus this module's own writes cannot reproduce.
    """
    ws = Path(ws)
    repo = repo_path(ws)

    if repo.exists():
        if repo.is_dir() and _is_bare_repo(repo):
            pass
        elif repo.is_dir():
            shutil.rmtree(repo)
        else:
            repo.unlink()

    if not repo.exists():
        # `--git-dir` tells `init` itself where to create the repo, so this
        # goes through `_git` like every other call — one environment
        # builder, with no second call site that could drift from it.
        _git(repo, "init", "--bare", "--quiet")

    if _ref_sha(repo, EMPTY_ROOT_REF) is None:
        empty_tree = _tree(repo, [])
        root_commit = _commit(repo, empty_tree, [], "empty root")
        _update_ref(repo, EMPTY_ROOT_REF, root_commit)

    return repo
