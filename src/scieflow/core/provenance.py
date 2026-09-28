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
import re
import shutil
import subprocess
from itertools import chain
from pathlib import Path

from scieflow.core import drafts, events

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
         extra_env: dict[str, str] | None = None, errors: str = "strict") -> str:
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

    `errors` is the text-decoding error handler `subprocess.run` applies to
    this process's stdout/stderr (it is `text=True` throughout, so this is
    the same `errors=` a plain `bytes.decode` would take). Every caller but
    `diff` leaves it at the default `"strict"`: a malformed byte from `git`
    itself would be a bug worth surfacing. `diff` passes `"replace"`,
    because its output can legitimately embed a round's own artifact bytes
    — including a non-UTF-8 `.tex` file's changed lines (see
    `test_a_non_utf8_source_is_committed_byte_exact`) — and a page rendering
    a diff must not 500 over a byte it didn't write; `service._log_tail`
    makes the identical choice for a compile log.

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
            input=stdin, env=env, errors=errors,
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


def _blob_file(repo: Path, path: Path) -> str:
    """Hash `path`'s on-disk bytes into a blob, exactly as they are.

    `--no-filters` and reading straight from the path (rather than piping
    `path.read_bytes()` through `_blob`'s `--stdin`, which requires a `str`
    because `_git`'s subprocess runs in text mode) makes this
    encoding-agnostic by construction: a `.tex` file in latin-1 or UTF-16 is
    ordinary in LaTeX work, and its content must land in history byte for
    byte, not get silently dropped for failing a UTF-8 decode or mangled by
    a round trip through `str`. `_blob` is kept alongside this for in-memory
    content with no path of its own (Task 1's round-trip test exercises
    it); `sync`'s projection uses this one for every on-disk artifact.
    """
    return _git(repo, "hash-object", "-w", "--no-filters", "--", str(path))


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


def ref_safe(name: str) -> bool:
    """Whether `name` can be a branch component and a git argument.

    An agent chooses these — they are directory names it writes under
    `manuscript/drafts/` — and they become `refs/heads/draft/<name>`. Three
    separate hazards, so three checks:

    * `drafts.check_name` first, for the path-shaped and control-character
      cases it already owns (a newline in a ref would be its own problem).
    * a leading `-`, because `git diff -x main` reads `-x` as a flag, not a
      ref. `check-ref-format` accepts it.
    * `git check-ref-format` last, for git's own rules — `a..b`, `a.lock`,
      `x y`, `a~1`, `a^b`, `a:b`, `HEAD@{0}`. Asking git is the only way to
      stay right when git's rules change.

    Returns `False` for any of the three rather than raising: an unsafe
    agent name costs that agent, never the whole sync (see `sync`).
    `check-ref-format` is run directly through `subprocess.run` with
    `check=False`, not through `_git` — it needs no repo, and must not turn
    a merely-unsafe name into a `ProvenanceError`. It still runs with
    `_env()`'s environment, not the process's own: it is the one git
    subprocess in this module that would otherwise inherit `os.environ`
    wholesale (a model API key included) for no reason — `check-ref-format`
    reads no repo config and runs no hooks, so this is defence in depth,
    not a fix for a found exploit, and it keeps "every git subprocess in
    this module gets its environment from `_env()`" true without exception.
    """
    try:
        drafts.check_name(name)
    except drafts.DraftError:
        return False
    if name.startswith("-"):
        return False
    try:
        result = subprocess.run(
            [GIT, "check-ref-format", f"refs/heads/draft/{name}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S, check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _raw_agent_names(ws: Path) -> set[str]:
    """Every agent name a run's own artifacts imply, unfiltered by
    `ref_safe`.

    `agents()` filters this through `ref_safe` and sorts it; `sync()` also
    needs the unfiltered set, so a name that fails `ref_safe` can be
    reported in `skipped` instead of silently vanishing.

    Sources, unioned: subdirectories of `manuscript/drafts/`; stems of
    `findings/*.json` and `gaps/*.json`; and the `<R>`/`<A>` components of
    `<R>-on-<A>.json` filenames directly under `reviews/` and under each
    `review/draft-round-<N>/`. Never `config/agents.yml` — an agent's
    presence here is evidence it acted, not configuration.

    The `manuscript/drafts/` subdirectories go through `drafts.agents(ws)`
    rather than a bare `iterdir()`/`is_dir()` — `drafts.agents` (via its own
    `_subdirs`) already resolves each entry and refuses one that resolves
    outside `manuscript/drafts/`, which a bare `is_dir()` does not: it
    follows a symlink exactly like a real directory. Without this, an
    agent-created `manuscript/drafts/evil -> /etc` would surface `"evil"` as
    a discovered agent and — since every artifact read in `sync()` still
    has to be individually contained (see `put`) — the *name* leaking here
    was a smaller half of a two-part hole; this file uses the same
    already-hardened primitive the rest of the codebase uses for exactly
    this directory, instead of re-deriving the same containment check.
    """
    ws = Path(ws)
    names: set[str] = set(drafts.agents(ws))

    for sub in ("findings", "gaps"):
        directory = ws / sub
        if directory.is_dir():
            names.update(p.stem for p in directory.glob("*.json") if p.is_file())

    def add_on_pairs(directory: Path) -> None:
        if not directory.is_dir():
            return
        for p in directory.glob("*-on-*.json"):
            if not p.is_file():
                continue
            reviewer, sep, author = p.stem.partition("-on-")
            if sep:
                names.add(reviewer)
                names.add(author)

    add_on_pairs(ws / "reviews")
    review_dir = ws / "review"
    if review_dir.is_dir():
        for round_dir in review_dir.glob("draft-round-*"):
            add_on_pairs(round_dir)

    return names


def agents(ws: Path) -> list[str]:
    """Agent names discovered from a run's own artifacts, sorted, filtered
    to those safe to use as a branch name.

    See `_raw_agent_names` for discovery sources and `ref_safe` for the
    filter. A name `ref_safe` refuses is simply absent — never fatal."""
    return sorted(name for name in _raw_agent_names(Path(ws)) if ref_safe(name))


def _round_dir_number(name: str) -> tuple[bool, int | None]:
    """`(looks_like_a_round, number)` for a round-directory name (already
    stripped of any `round-`/`draft-round-` prefix).

    `looks_like_a_round` is `False` for anything that isn't ASCII digits at
    all — that directory simply isn't a round of anything, silently, the
    same as before this function existed. When it *is* digit-shaped,
    `number` is `None` unless `name` is the canonical decimal spelling of
    it (`str(int(name)) == name`): a bare `.isdigit()` test would let
    `"01"` and `"1"` both parse as round 1 and collapse onto the same
    target path, with the winner decided by directory-scan order — not
    guaranteed stable, so which content wins could flip between syncs and
    produce a spurious commit purely from scan-order noise. A digit-shaped
    but non-canonical name is therefore reported by the caller, not
    silently picked or dropped. `drafts.rounds()` gets away with a bare
    `int()` because it only ever reads; this module writes, so a
    non-canonical name has to cost that round, not create nondeterminism.
    """
    if not (name.isascii() and name.isdigit()):
        return False, None
    return True, (int(name) if str(int(name)) == name else None)


def _tree_from_paths(repo: Path, files: dict[str, Path]) -> str:
    """Build a tree object from a flat map of `{target/path: source path}`.

    `files` keys are full paths relative to the branch root (e.g.
    `"merge_2/sections/results.tex"`); `mktree` only knows how to build one
    flat level of entries from `(mode, kind, sha, name)` tuples, so a nested
    target path has to become a nested tree first. This groups the flat map
    by its first path component — everything with no remaining `/` is a
    leaf (blobbed via `_blob_file`) at this level, everything else is
    grouped by that first component and recursed on the remainder — and
    folds each recursive result in as a `tree` entry alongside the leaves.
    Recursing before building this level's own entry list means every
    subtree is built (and hashed, via `mktree`) before the tree that
    references it, i.e. deepest first, simply because a child's `mktree`
    call has to return before its parent's entry tuple can be constructed.
    An empty `files` produces the well-known empty tree, the same way
    `_tree(repo, [])` does, since that's just `entries == []` here too.

    A name used as both a leaf and a group at the same level — `files ==
    {"a": ..., "a/b": ...}` — would `mktree` into two entries both named
    `"a"`, which `fsck --strict` flags as `duplicateEntries`; nothing in
    today's path table can produce this, but a future table row silently
    could, so it is refused here rather than left for `fsck` to someday
    notice.
    """
    leaves: dict[str, Path] = {}
    groups: dict[str, dict[str, Path]] = {}
    for path, source in files.items():
        head, sep, rest = path.partition("/")
        if not sep:
            leaves[head] = source
        else:
            groups.setdefault(head, {})[rest] = source
    collision = set(leaves) & set(groups)
    if collision:
        raise ProvenanceError(
            f"path used as both a file and a directory: {sorted(collision)!r}")
    entries: list[tuple[str, str, str, str]] = []
    for name, source in leaves.items():
        blob = _blob_file(repo, source)
        entries.append(("100644", "blob", blob, name))
    for name, sub_files in groups.items():
        subtree = _tree_from_paths(repo, sub_files)
        entries.append(("040000", "tree", subtree, name))
    return _tree(repo, entries)


def sync(ws: Path) -> dict:
    """Project `ws`'s artifacts onto `main` and a `draft/<agent>` branch per
    discovered agent, committing only what changed since the last sync.

    `ensure_repo` first, so a missing or corrupt repo is made usable before
    anything is written. Each branch's full target tree is built from
    scratch every time (see `_tree_from_paths`) and compared against that
    branch's current tip — or, for a branch that does not exist yet,
    against the root's empty tree — so a branch is committed only when it
    would actually change, and is parented on its existing tip, or on
    `EMPTY_ROOT_REF` when it is new. `main` is the one exception to "skip an
    empty new branch": it is always given a first commit, even an honestly
    empty one, the moment a repo exists, because every later reader
    compares branches by ref and `main` simply not existing yet is a harder
    special case to handle in each of those readers than committing one
    empty `main` here.

    Every artifact this function reads is required, by the nested `put`
    helper, to resolve to a real path inside `ws` before it is opened —
    this is what stops an agent-created symlink (a drafting directory such
    as `manuscript/drafts/<agent>`, or any single artifact file the path
    table names) from smuggling host content outside the run into a
    committed, exportable repo. A source that does not exist at all is an
    optional artifact the run simply hasn't written yet and is silently
    skipped, not reported; a source that *does* exist in some irregular
    shape — a directory where a file is expected, a symlink that is
    dangling or resolves outside `ws`, or a file this process cannot open —
    is a genuine anomaly and lands in `skipped["artifacts"]` as a
    run-relative path, never the absolute host path (which would otherwise
    leak into `events.jsonl` forever, exactly the class of leak `_git`'s own
    docstring avoids for argument lists). An agent name `ref_safe` refuses
    is recorded by name in `skipped["agents"]`. A round directory whose name
    is digit-shaped but not the canonical decimal spelling of its number
    (`"01"` alongside `"1"`) is refused and reported the same way, so which
    one wins never depends on directory-scan order.

    Both `emit` calls are independently guarded: a run directory without
    `status.yml` (most of this module's callers in tests, and plausibly in
    production before a run has written one) must not turn a successful
    sync into a failure just because the event log couldn't be annotated,
    and a failure emitting one event must not suppress the other.

    Returns `{"commits": {ref: sha, ...}, "agents": [...], "skipped":
    {"agents": [...], "artifacts": [...]}}`.
    """
    ws = Path(ws)
    try:
        ws_resolved = ws.resolve()
    except (OSError, RuntimeError) as exc:
        # A symlink cycle in an *ancestor* of `ws` (not `ws` itself, and not
        # any artifact under it — those are `put`'s problem) makes
        # `.resolve()` raise a bare `RuntimeError`, and a sufficiently deep
        # or looping chain can also raise `OSError`. Left unguarded, this
        # would be the one place in the module where a caller sees
        # something other than `ProvenanceError` — every other resolve in
        # this codebase either runs inside `_git` (which already turns a
        # failure into `ProvenanceError`) or inside `drafts._subdirs`
        # (which already catches exactly these two). `sync` needs the same
        # discipline, since Task 4's `manuscript_history` is specified to
        # never raise for an ordinary state and catches `ProvenanceError`
        # specifically — an uncaught `RuntimeError` here would 500 that
        # page instead of degrading.
        raise ProvenanceError(f"cannot resolve workspace {str(ws)!r}: {exc}") from exc
    if not available():
        raise ProvenanceError("git is not available on this host")
    repo = ensure_repo(ws)

    raw = _raw_agent_names(ws)
    safe_agents = sorted(name for name in raw if ref_safe(name))
    skipped: dict[str, list[str]] = {
        "agents": sorted(name for name in raw if name not in safe_agents),
        "artifacts": [],
    }

    branches: dict[str, dict[str, Path]] = {"refs/heads/main": {}}
    for name in safe_agents:
        branches[f"refs/heads/draft/{name}"] = {}

    def put(ref: str, target: str, source: Path) -> None:
        """Add `source` at `target` on `ref`'s tree, or record why not.

        `source.is_symlink() or source.exists()` is "is there anything at
        all here" — `.exists()` alone follows a symlink and reads `False`
        for a dangling one, which would make a dangling symlink
        indistinguishable from a genuinely absent optional artifact. Once
        something is confirmed present, everything else that can go wrong
        (wrong shape, escapes `ws`, unreadable) is a reportable skip.
        """
        if not (source.is_symlink() or source.exists()):
            return
        if not source.is_file():
            skipped["artifacts"].append(str(source.relative_to(ws)))
            return
        try:
            resolved = source.resolve()
        except (OSError, RuntimeError):
            skipped["artifacts"].append(str(source.relative_to(ws)))
            return
        if not resolved.is_relative_to(ws_resolved):
            skipped["artifacts"].append(str(source.relative_to(ws)))
            return
        try:
            with resolved.open("rb"):
                pass
        except OSError:
            skipped["artifacts"].append(str(source.relative_to(ws)))
            return
        branches[ref][target] = resolved

    # outline/outline.md -> main: outline.md
    put("refs/heads/main", "outline.md", ws / "outline" / "outline.md")

    # manuscript/curation/rounds/<n>/<section>.tex -> main: merge_<n>/sections/<section>.tex
    round_numbers: list[int] = []
    rounds_dir = ws / "manuscript" / "curation" / "rounds"
    if rounds_dir.is_dir():
        for round_dir in rounds_dir.iterdir():
            if not round_dir.is_dir():
                continue
            looks_like_round, n = _round_dir_number(round_dir.name)
            if not looks_like_round:
                continue
            if n is None:
                skipped["artifacts"].append(str(round_dir.relative_to(ws)))
                continue
            round_numbers.append(n)
            for tex in round_dir.glob("*.tex"):
                put("refs/heads/main", f"merge_{n}/sections/{tex.stem}.tex", tex)

    # manuscript/curation/document.yml -> main: merge_<n>/curation.yml, highest n present
    if round_numbers:
        highest = max(round_numbers)
        put("refs/heads/main", f"merge_{highest}/curation.yml",
            ws / "manuscript" / "curation" / "document.yml")

    # review/round-<N>/{review,response}.md -> main: review_<N>/{review,response}.md
    review_dir = ws / "review"
    if review_dir.is_dir():
        for round_dir in review_dir.glob("round-*"):
            if not round_dir.is_dir():
                continue
            looks_like_round, n = _round_dir_number(round_dir.name[len("round-"):])
            if not looks_like_round:
                continue
            if n is None:
                skipped["artifacts"].append(str(round_dir.relative_to(ws)))
                continue
            for fname in ("review.md", "response.md"):
                put("refs/heads/main", f"review_{n}/{fname}", round_dir / fname)

    # findings/<agent>.json, gaps/<agent>.json, manuscript/drafts/<agent>/<section>.tex
    # -> draft/<agent>: findings.json, gaps.json, sections/<section>.tex
    for name in safe_agents:
        ref = f"refs/heads/draft/{name}"
        put(ref, "findings.json", ws / "findings" / f"{name}.json")
        put(ref, "gaps.json", ws / "gaps" / f"{name}.json")
        agent_dir = ws / "manuscript" / "drafts" / name
        if agent_dir.is_dir():
            for tex in agent_dir.glob("*.tex"):
                put(ref, f"sections/{tex.stem}.tex", tex)

    # reviews/<R>-on-<A>.json -> draft/<R>: reviews/<R>-on-<A>.json
    reviews_dir = ws / "reviews"
    if reviews_dir.is_dir():
        for f in reviews_dir.glob("*-on-*.json"):
            reviewer, sep, _author = f.stem.partition("-on-")
            if sep and reviewer in safe_agents:
                put(f"refs/heads/draft/{reviewer}", f"reviews/{f.name}", f)

    # review/draft-round-<N>/<R>-on-<A>.json -> draft/<R>: review_<N>/<R>-on-<A>.json
    # review/draft-round-<N>/response-<A>.md -> draft/<A>: review_<N>/response-<A>.md
    if review_dir.is_dir():
        for round_dir in review_dir.glob("draft-round-*"):
            if not round_dir.is_dir():
                continue
            looks_like_round, n = _round_dir_number(round_dir.name[len("draft-round-"):])
            if not looks_like_round:
                continue
            if n is None:
                skipped["artifacts"].append(str(round_dir.relative_to(ws)))
                continue
            for f in round_dir.iterdir():
                fname = f.name
                if fname.endswith(".json") and "-on-" in fname:
                    reviewer, sep, _author = f.stem.partition("-on-")
                    if sep and reviewer in safe_agents:
                        put(f"refs/heads/draft/{reviewer}", f"review_{n}/{fname}", f)
                elif fname.startswith("response-") and fname.endswith(".md"):
                    author = f.stem[len("response-"):]
                    if author in safe_agents:
                        put(f"refs/heads/draft/{author}", f"review_{n}/{fname}", f)

    skipped["artifacts"].sort()

    root = _ref_sha(repo, EMPTY_ROOT_REF)
    root_tree = _git(repo, "rev-parse", f"{EMPTY_ROOT_REF}^{{tree}}")

    commits: dict[str, str] = {}
    for ref, files in branches.items():
        tree = _tree_from_paths(repo, files)
        tip = _ref_sha(repo, ref)
        if tip is not None:
            baseline = _git(repo, "rev-parse", f"{ref}^{{tree}}")
            parent = tip
            if tree == baseline:
                continue
        else:
            parent = root
            # Ruling 2: `main` always gets committed on its first sync, even
            # from an empty tree, so it exists as soon as the repo does. A
            # brand-new agent branch stays exempt: an agent is only ever
            # discovered because *some* artifact of theirs exists, so an
            # empty `draft/<agent>` here would only mean every one of their
            # artifacts failed containment — already visible in
            # `skipped["artifacts"]` — and doesn't need an empty commit too.
            if ref != "refs/heads/main" and tree == root_tree:
                continue
        message = f"sync {ref.removeprefix('refs/heads/')}"
        commit = _commit(repo, tree, [parent], message)
        _update_ref(repo, ref, commit)
        commits[ref] = commit

    try:
        if commits:
            events.emit(ws, "provenance.synced", refs=list(commits), agents=safe_agents)
    except Exception:
        pass
    try:
        if skipped["agents"] or skipped["artifacts"]:
            events.emit(ws, "provenance.skipped",
                        agents=skipped["agents"], artifacts=skipped["artifacts"])
    except Exception:
        pass

    return {"commits": commits, "agents": safe_agents, "skipped": skipped}


#: The maximum length, in characters, of `diff()`'s returned `text` — a big
#: diff (a giant generated `.tex` file, or many rounds at once) must not be
#: read whole into a page. Truncation is stated in the text itself, never
#: silent (see `diff`).
DIFF_LIMIT = 200_000

_MERGE_DIR_RE = re.compile(r"^merge_(\d+)$")
_REVIEW_DIR_RE = re.compile(r"^review_(\d+)$")


def _branch_tip(repo: Path, ref: str) -> tuple[str, str] | None:
    """`(sha, committer-date-iso)` for `ref`'s current tip, or `None` if
    `ref` does not exist.

    Read once per branch and shared by every point that branch contributes
    (see `points`) — `%cI` is the committer date in strict ISO 8601, and a
    NUL separates it from the sha so neither field can bleed into the
    other regardless of what `%H` or `%cI` ever produce.
    """
    if _ref_sha(repo, ref) is None:
        return None
    out = _git(repo, "log", "-1", "--format=%H%x00%cI", ref)
    sha, _, at = out.partition("\x00")
    return sha, at


def _ls_tree_names(repo: Path, treeish: str) -> list[str]:
    """Top-level entry names of `treeish` (non-recursive), or `[]` if it
    doesn't resolve to a tree at all — e.g. a round that turns out to have
    no `sections` subdirectory, or a branch with an empty tree."""
    try:
        out = _git(repo, "ls-tree", "--name-only", treeish)
    except ProvenanceError:
        return []
    return out.splitlines() if out else []


def _numbered_dirs(names: list[str], pattern: re.Pattern[str]) -> list[int]:
    """Every `pattern`-matching name in `names`, as its captured number,
    sorted newest (highest) first."""
    numbers = (int(match.group(1)) for name in names if (match := pattern.match(name)))
    return sorted(numbers, reverse=True)


def _draft_branches(repo: Path) -> list[str]:
    """Agent names with a `draft/<agent>` branch actually present in the
    repo, sorted.

    Read from the repo's own refs via `for-each-ref`, not from
    `agents(ws)` — `points` reflects what is *committed*, per its own
    contract, and a workspace can drift from what was last synced (an
    agent's directory removed after the fact, say). A repo with no
    `draft/*` branches at all yields `[]`, not an error.
    """
    try:
        out = _git(repo, "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/draft/*")
    except ProvenanceError:
        return []
    return sorted(line.removeprefix("draft/") for line in out.splitlines() if line)


def _main_points(repo: Path) -> list[dict]:
    """Points contributed by `refs/heads/main`: one per `merge_<n>`
    directory (plus a finer `merge_<n>/sections` point when that round has
    a `sections` subdirectory — the same depth as a draft's `sections`
    point, so the two are comparable in one `diff` call), one per
    `review_<N>` directory, and one for `outline.md` when present.
    Newest round first; `outline.md` last, since it predates every round.
    """
    ref = "refs/heads/main"
    tip = _branch_tip(repo, ref)
    if tip is None:
        return []
    sha, at = tip
    names = _ls_tree_names(repo, ref)

    points_: list[dict] = []
    for n in _numbered_dirs(names, _MERGE_DIR_RE):
        points_.append({"ref": f"main:merge_{n}", "label": f"Merge round {n}",
                        "kind": "merge", "commit": sha, "at": at})
        sub = _ls_tree_names(repo, f"{ref}:merge_{n}")
        if "sections" in sub:
            points_.append({"ref": f"main:merge_{n}/sections",
                            "label": f"Merge round {n} sections",
                            "kind": "merge", "commit": sha, "at": at})
    for n in _numbered_dirs(names, _REVIEW_DIR_RE):
        points_.append({"ref": f"main:review_{n}", "label": f"Review round {n}",
                        "kind": "review", "commit": sha, "at": at})
    if "outline.md" in names:
        points_.append({"ref": "main:outline.md", "label": "Outline",
                        "kind": "outline", "commit": sha, "at": at})
    return points_


def _draft_points(repo: Path, agent: str) -> list[dict]:
    """Points contributed by `refs/heads/draft/<agent>`: one for
    `sections` (that agent's current draft) when present, and one per
    `review_<N>` directory. `sections` first — it is the current state,
    the most recent thing to look at — then reviews newest round first.
    """
    short = f"draft/{agent}"
    ref = f"refs/heads/{short}"
    tip = _branch_tip(repo, ref)
    if tip is None:
        return []
    sha, at = tip
    names = _ls_tree_names(repo, ref)

    points_: list[dict] = []
    if "sections" in names:
        points_.append({"ref": f"{short}:sections", "label": f"{agent}'s draft",
                        "kind": "draft", "commit": sha, "at": at})
    for n in _numbered_dirs(names, _REVIEW_DIR_RE):
        points_.append({"ref": f"{short}:review_{n}", "label": f"Review round {n} ({agent})",
                        "kind": "review", "commit": sha, "at": at})
    return points_


def points(ws: Path) -> list[dict]:
    """Every point a run's provenance repo currently holds — a round's
    merge or review on `main`, an agent's current draft `sections`, or a
    per-round review on that agent's branch — as
    `{"ref": "<branch>:<tree path>", "label": str, "kind": "merge"|"review"
    |"draft"|"outline", "commit": "<sha>", "at": "<iso>"}`.

    A point is exactly the `<branch>:<tree path>` string `diff` accepts,
    and the whole whitelist `diff`'s `a`/`b` are checked against (see
    `diff`) — so this is the *only* place that vocabulary is produced.

    Reads the repo, never the workspace: nothing here calls `ensure_repo`
    or `sync`, so asking for points is not itself a write, and a ref this
    function doesn't find (because it was never synced, or a rebuild
    hasn't happened yet) simply contributes nothing rather than raising.
    A workspace whose repo doesn't exist at all — never synced — is the
    same ordinary case as one synced with no branches: `[]`, not an error.

    Sorted `main` before the draft branches (agents sorted by name), and
    newest-round-first within each branch, so a panel listing these
    top-down reads as the most recent work first.
    """
    ws = Path(ws)
    repo = repo_path(ws)
    if not repo.is_dir():
        return []

    result = _main_points(repo)
    for agent in _draft_branches(repo):
        result.extend(_draft_points(repo, agent))
    return result


def _point_treeish(point: str) -> str:
    """`<branch>:<tree path>` -> `refs/heads/<branch>:<tree path>`, the
    tree-ish `git diff` actually takes. Only ever called on a `point`
    already proven to equal one `points()` returned — see `diff`."""
    branch, _, path = point.partition(":")
    return f"refs/heads/{branch}:{path}"


def diff(ws: Path, a: str, b: str) -> dict:
    """A textual `git diff --no-color` between two points, as
    `{"text": str, "truncated": bool, "a": str, "b": str}`.

    `a` and `b` reach a `git` subprocess, so each is checked for whole-
    string equality against `points(ws)` *before* that subprocess ever
    runs — not prefix-matched, not sanitised, not merely checked for
    `..`. An unvalidated ref could be argument-shaped
    (`--output=/tmp/x`, `-x`) rather than merely path-shaped, which no
    containment or traversal check would catch; only a whitelist of
    exactly the strings this module itself produced closes that. A
    syntactically real ref that simply isn't a point — `refs/heads/main`,
    as opposed to the point `main:merge_2` — is refused identically, since
    the check is equality against a known-good set, not a general
    ref-format or existence check.

    Both points are resolved to a `refs/heads/<branch>:<tree path>`
    tree-ish (see `_point_treeish`) and diffed directly — `git diff`
    accepts two tree-ish arguments and compares them recursively, which is
    exactly what lets a draft's `sections` be compared against a round's
    `merge_<n>/sections` despite living on different branches.

    The result is truncated to `DIFF_LIMIT` characters with the
    truncation stated in the text itself, never silent. Decoding uses
    `errors="replace"` (see `_git`) so a diff embedding a non-UTF-8
    artifact's changed lines can't turn into a crash instead of a page —
    git itself already keeps raw bytes out of a *binary* file's diff,
    reporting `Binary files ... differ` instead of emitting them.
    """
    ws = Path(ws)
    allowed = {point["ref"] for point in points(ws)}
    if a not in allowed:
        raise ProvenanceError(f"not a point: {a!r}")
    if b not in allowed:
        raise ProvenanceError(f"not a point: {b!r}")

    repo = repo_path(ws)
    text = _git(repo, "diff", "--no-color", _point_treeish(a), _point_treeish(b),
               errors="replace")

    truncated = False
    if len(text) > DIFF_LIMIT:
        text = text[:DIFF_LIMIT] + f"\n… (diff truncated at {DIFF_LIMIT} characters)\n"
        truncated = True

    return {"text": text, "truncated": truncated, "a": a, "b": b}
