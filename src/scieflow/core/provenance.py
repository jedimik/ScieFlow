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
#: no writable identity configured (confirmed: with these four omitted and
#: `HOME` also absent, `commit-tree` fails with `empty ident name`, since
#: git cannot fall back to reading `~/.gitconfig`). Fixing it here means
#: every commit this feature ever makes is attributable to the same identity
#: regardless of host configuration.
_AUTHOR_NAME = "ScieFlow"
_AUTHOR_EMAIL = "provenance@scieflow.local"

#: The date every root commit — and only the root commit — is stamped with,
#: rather than "now". `_root` records no work; it is a synthetic anchor that
#: exists solely so `git diff` between any two branches has a common
#: ancestor (see the module docstring). Giving it the moment `ensure_repo`
#: happened to run would imply an event took place then, which is false,
#: and it would also make two runs' provenance repos gratuitously
#: incomparable — two `_root` commits built from the empty tree, by the
#: same identity, with the same message, differ only in timestamp, so
#: pinning it makes `_root` the same SHA in every repo this module ever
#: creates. Every other commit keeps a real date; only `ensure_repo`'s
#: `_commit` call for `_root` passes `when=_ROOT_COMMIT_DATE`.
_ROOT_COMMIT_DATE = "@0 +0000"  # 1970-01-01T00:00:00Z, git's raw-epoch date syntax

#: The only variables a git subprocess inherits from the serve process.
#: `PATH` is needed to find `git` itself; `LANG` only decides how git's own
#: messages are encoded. Everything else — notably the serve process's model
#: API keys, and `HOME` — is simply absent, because this dict is built
#: explicitly rather than being a copy of `os.environ`. See
#: `preview.compile_env`, which applies the identical discipline for
#: `latexmk`.
#:
#: `HOME` is deliberately never set (`compile_env` sets it; this module does
#: not): a git subprocess with no `HOME` cannot read a developer's
#: `~/.gitconfig`, which is what keeps the fixed `_AUTHOR_*`/`_AUTHOR_EMAIL`
#: identity below from being silently overridden by whatever `user.name`/
#: `user.email` happens to be configured on the host running ScieFlow. If a
#: future change adds `HOME` back — e.g. because some other tool needs a
#: writable one — the loss of this guarantee needs to be deliberate, not a
#: side effect: a maintainer "fixing" an unrelated `HOME` complaint here
#: would reopen the channel with no test to catch it, since a bare `git
#: config user.email you@host` on the CI/dev machine would then silently
#: win.
_INHERITED = ("PATH", "LANG")


class ProvenanceError(ValueError):
    """A git operation failed, or the repo could not be made usable."""


def available() -> bool:
    return shutil.which(GIT) is not None


def repo_path(ws: Path) -> Path:
    return Path(ws) / REPO_DIR


def _env(parent: dict[str, str] | None = None) -> dict[str, str]:
    """The whole environment a git subprocess runs with, built once here so
    no other call site in this module (or a later one) can hand a git
    subprocess the serve process's own environment by accident. See the
    module docstring and `_AUTHOR_NAME`/`_AUTHOR_EMAIL` for why.

    `parent` is the environment to inherit `_INHERITED` from, defaulting to
    this process's — a parameter only so a test can hand in a hostile one
    (carrying, say, a model API key) without mutating the real `os.environ`,
    exactly as `preview.compile_env`'s own `parent` argument exists for.
    """
    src = os.environ if parent is None else parent
    env = {name: src[name] for name in _INHERITED if name in src}
    env["GIT_AUTHOR_NAME"] = _AUTHOR_NAME
    env["GIT_COMMITTER_NAME"] = _AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = _AUTHOR_EMAIL
    env["GIT_COMMITTER_EMAIL"] = _AUTHOR_EMAIL
    # The environment above is sealed, but `/etc/gitconfig` (system config)
    # is a second channel into the same subprocess, with none of this
    # module's own settings able to stop it. Verified on this host (git
    # 2.43.0): `core.hooksPath` set system-wide makes `update-ref` run an
    # arbitrary `reference-transaction` hook script on every ref update this
    # module makes — i.e. every `_update_ref` call. (`commit.gpgsign = true`
    # alone was also tried and does *not* affect `commit-tree`, which only
    # signs when `-S`/`--gpg-sign` is passed explicitly and this module
    # never passes it — but `core.hooksPath` is a real, reproduced instance
    # of the same class of hole, and there may be others.) `-norc` closes
    # the equivalent hole for `latexmk` in `preview.compile_argv`; this
    # closes it for git.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(repo: Path, *args: str, stdin: str | None = None,
         extra_env: dict[str, str] | None = None) -> str:
    """Run `git --git-dir <repo> <args>`, with `stdin` (if given) piped in as
    text, and return stdout with trailing whitespace stripped.

    Every argument is its own argv element — never a shell string — so a
    value that looks like a flag (a commit message, a filename) is always
    taken literally rather than parsed. A non-zero exit, a missing `git`
    binary, or a hung process past `_TIMEOUT_S` all become `ProvenanceError`
    rather than propagating a `CalledProcessError`/`OSError`/
    `TimeoutExpired`, so every caller in this module has exactly one
    exception to handle.

    `extra_env`, when given, is merged over `_env()`'s result — used only by
    `_commit` to pin `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` for the root
    commit, so that one caller does not need its own environment-building
    logic.

    The exception message names only the git subcommand (`args[0]`), never
    the rest of `args`: a failing `commit-tree` call carries an
    agent-influenced commit message as one of those arguments, and that
    message ends up in logs or an HTTP error body if the whole argument
    list were interpolated here.
    """
    env = _env()
    if extra_env:
        env.update(extra_env)
    argv = [GIT, "--git-dir", str(repo), *args]
    subcommand = args[0] if args else "?"
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT_S,
            input=stdin, env=env,
        )
    except FileNotFoundError as exc:
        raise ProvenanceError(f"git not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProvenanceError(
            f"git {subcommand} timed out after {_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1:] or [""]
        raise ProvenanceError(f"git {subcommand} failed: {tail[0]}")
    return result.stdout.strip()


def _blob(repo: Path, data: str) -> str:
    return _git(repo, "hash-object", "-w", "--stdin", stdin=data)


def _tree(repo: Path, entries: list[tuple[str, str, str, str]]) -> str:
    """Build a tree from `(mode, kind, sha, name)` entries via `mktree -z`.

    Records are NUL-delimited (`mktree -z`), not newline-delimited, and this
    is load-bearing, not cosmetic: `name` is every later task's primitive
    for writing agent-chosen filenames into a tree — a section name an
    agent picked, a review filename built from an agent name — and is
    therefore untrusted. With plain newline-delimited `mktree`, a name
    containing an embedded `<mode> <kind> <sha>\\t<other-name>\\n` payload is
    indistinguishable from two real entries: `mktree` parses it as such and
    the caller's single intended write silently gains a second file it
    never asked for, pointing at content it never chose. A tab is the same
    class of hole, since a tab that isn't the mode/name separator can shift
    where the parser thinks the name starts.
    `-z` closes both: verified on this host that the identical payload,
    NUL-delimited, comes back out of `ls-tree -z` as one entry whose name
    contains a literal embedded newline (or tab), never as two entries.

    A NUL byte in `name` itself cannot be represented in a NUL-delimited
    record at all — it would truncate that record — so it is refused
    outright rather than silently corrupting the tree.

    An empty `entries` list still goes through `mktree -z` with an empty
    stdin rather than being special-cased, because `mktree` on empty input
    is itself how the empty tree's well-known SHA is produced — there is
    nothing more "correct" this function could return by skipping the call.
    """
    records = []
    for mode, kind, sha, name in entries:
        if "\x00" in name:
            raise ProvenanceError(f"tree entry name contains a NUL byte: {name!r}")
        records.append(f"{mode} {kind} {sha}\t{name}")
    stdin = "".join(record + "\x00" for record in records)
    return _git(repo, "mktree", "-z", stdin=stdin)


def _commit(repo: Path, tree: str, parents: list[str], message: str, *,
            when: str | None = None) -> str:
    """`commit-tree`, with the dates left to git — except when `when` is
    given (a git date string), which pins both `GIT_AUTHOR_DATE` and
    `GIT_COMMITTER_DATE` for this call only. `ensure_repo` is the only
    caller that ever passes it, for the root commit; see
    `_ROOT_COMMIT_DATE`.
    """
    parent_args = list(chain.from_iterable(("-p", p) for p in parents))
    extra_env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None
    return _git(repo, "commit-tree", tree, *parent_args, "-m", message, extra_env=extra_env)


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

    if repo.is_symlink():
        # Checked before `.exists()`/`.is_dir()`, both of which follow the
        # link: a symlink whose target is corrupt would otherwise reach the
        # `is_dir()` branch below and `shutil.rmtree` would refuse to remove
        # a symlink, raising an uncaught `OSError` instead of
        # `ProvenanceError` — breaking this module's "one exception to
        # handle" contract. A *broken* symlink also fails `.exists()`
        # entirely (it returns `False`), which is why this check comes
        # first rather than inside the `repo.exists()` branch below.
        repo.unlink()
    elif repo.exists():
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
        root_commit = _commit(repo, empty_tree, [], "empty root", when=_ROOT_COMMIT_DATE)
        _update_ref(repo, EMPTY_ROOT_REF, root_commit)

    return repo
