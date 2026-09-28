# Task 1 report: the repo and the plumbing primitives

## What was implemented

`src/scieflow/core/provenance.py` — a bare, no-working-tree git repo per
run (`<ws>/provenance.git`), built and read entirely through git plumbing.

Public surface, exactly as specified:
- `REPO_DIR = "provenance.git"`
- `EMPTY_ROOT_REF = "refs/heads/_root"`
- `ProvenanceError(ValueError)`
- `available() -> bool`
- `repo_path(ws) -> Path`
- `ensure_repo(ws) -> Path`

Private plumbing primitives:
- `_git(repo, *args, stdin=None) -> str`
- `_blob(repo, data) -> str`
- `_tree(repo, entries) -> str`
- `_commit(repo, tree, parents, message) -> str`
- `_update_ref(repo, ref, sha) -> None`
- `_ref_sha(repo, ref) -> str | None`

One private helper not in the brief's interface list: `_is_bare_repo(repo)
-> bool`, used only by `ensure_repo` to decide whether an existing
`provenance.git` directory is a usable bare repo or needs discarding. It
wraps `_git(repo, "rev-parse", "--is-bare-repository")` and turns any
`ProvenanceError` (a corrupt repo) into `False`, so `ensure_repo`'s
corrupt-repo branch stays a plain `if`/`elif`/`else` rather than a
try/except. Not part of the module's public contract, so I did not treat
adding it as deviating from the interface list.

`tests/core/test_provenance.py` is verbatim from the brief — no test text
changed.

## Decisions and why

1. **`_git` owns the environment for *every* call, including `git init
   --bare`.** The brief's `ensure_repo` pseudocode calls `subprocess.run([GIT,
   "init", "--bare", "--quiet", str(repo)])` directly, bypassing `_git`. I
   initially wrote it that way (with its own explicit `env=_env()` and
   its own error handling), then reconsidered: your context note #2 says
   "`_git` owns the environment ... Build the env once inside `_git`, for
   every call" and "constructing the env in exactly one place means no
   later call site can accidentally hand the serve process's environment
   ... to a subprocess." A second call site building `env=_env()` by hand
   is exactly the kind of second place that note warns against, even
   though it happens to call the same helper. I confirmed
   `git --git-dir <path> init --bare` behaves identically to
   `git init --bare <path>` (verified manually — see Self-review below),
   so I routed the init call through `_git` itself:
   `_git(repo, "init", "--bare", "--quiet")`. This also means the init
   call gets `_git`'s existing error handling (non-zero exit ->
   `ProvenanceError`) for free instead of a duplicated check.

2. **`_ref_sha` and `_is_bare_repo` catch `ProvenanceError` broadly**, per
   the brief: "returns `None` on the non-zero exit rather than raising."
   Since `_git` turns every non-zero exit into `ProvenanceError`, catching
   that exception is the only way to distinguish "ref doesn't exist" /
   "not a bare repo" from success. This does mean a `_git` failure for an
   unrelated reason (e.g. a transient I/O error) would also read as
   "missing" here rather than propagating — the brief's own wording pins
   this behavior, so I did not add a narrower error path.

3. **Commit identity is fixed, not read from any config** — `ScieFlow`
   `<provenance@scieflow.local>`, both author and committer, values taken
   verbatim from your context note #2.

## Ambiguities / brief vs. codebase, and how resolved

- The brief's own `ensure_repo` pseudocode initializes with a raw
  `subprocess.run(...)`, not `_git`, which is in tension with the "one
  environment builder" ruling given in the dispatch context. I resolved
  this in favor of the dispatch context (explicitly stated as overriding
  the brief's impression) and routed init through `_git`. See decision 1.
- The brief lists `_git`'s signature as `_git(repo, *args, stdin=None) ->
  str` without specifying exception handling for the `git init --bare`
  step's own failure (e.g. `ws` not writable). Once init went through
  `_git`, this is handled by `_git`'s existing non-zero-exit ->
  `ProvenanceError` path, so no separate handling was needed.
- No `src/scieflow/core/AGENTS.md` exists (only `research/AGENTS.md`,
  `experiments/AGENTS.md`, `news/AGENTS.md`, `chats/AGENTS.md`, and the
  root `AGENTS.md`/`CLAUDE.md`). My dispatch prompt named neither of the
  two module AGENTS.md files CLAUDE.md's sub-agent line calls out, so I
  did not read either — nothing in the brief or dispatch prompt pointed
  at core-specific conventions beyond the named files to read by example
  (`preview.py`, `sandbox.py`, `drafts.py`), which I did read.

## Test results

1. `uv run pytest tests/core/test_provenance.py -v` before writing the
   implementation: **FAIL** — `ImportError: cannot import name
   'provenance' from 'scieflow.core'`, as expected.
2. After implementation: `uv run pytest tests/core/test_provenance.py -v`
   — **11 passed**.
3. `uv run pytest -q` (full suite, first pass, before the env-refactor in
   decision 1) — **1493 passed, 5 skipped, 6 deselected, 2 warnings** in
   ~130s.
4. After the env-refactor (routing `init --bare` through `_git`): reran
   `uv run pytest tests/core/test_provenance.py -v` — **11 passed** —
   then `uv run pytest -q` again — **1493 passed, 5 skipped, 6 deselected,
   2 warnings** in ~127s, exit code 0.

Baseline given was 1482 passed / 5 skipped; final is 1482 + 11 new = 1493
passed, 5 skipped unchanged, same 2 pre-existing starlette/anyio
deprecation warnings. Suite stays green.

## Self-review findings and what I changed

- **Verified `git --git-dir <path> init --bare` actually creates the
  target repo** the same way `git init --bare <path>` does (manual repro
  in `/tmp`, then cleaned up) before relying on it for decision 1's
  refactor — wanted evidence, not an assumption, since the brief's own
  pseudocode uses the other form.
- **Verified the corrupt-HEAD test is not accidentally vacuous.** The
  brief's `test_a_corrupt_repo_is_rebuilt_rather_than_raising` only
  asserts the root ref exists after rebuild, which would also hold
  trivially if `ensure_repo` did *not* detect the corruption (the original
  root ref would simply still be there, untouched). I manually reproduced
  the corrupt-HEAD scenario against a bare repo outside the test suite and
  confirmed `git --git-dir <repo> rev-parse --is-bare-repository` genuinely
  fails (`fatal: not a git repository`, exit 128) on that corruption, so
  `_is_bare_repo` returning `False` and triggering the `rmtree` path is
  real behavior, not a false pass.
- Moved `import os` to the top of the module rather than importing it
  inline inside `_env()` — no functional change, just matches the rest of
  the file's import style.
- Added error handling to the `git init --bare` step (originally a bare
  `subprocess.run(...)` per the brief's pseudocode, with no failure check)
  — superseded by decision 1's refactor, which gives it `_git`'s handling
  instead of a bespoke check.

## Concerns

None outstanding at the time of the initial report. Full suite was green
at 1493 passed / 5 skipped. See the fix round below for what review
found.

---

# Fix round 1

Commit: `d812ec0` — "fix(core): close provenance's env-leak, root-reparent,
and tree-injection holes".

## What changed, and why

**IMPORTANT 1 — env guarantee untested.** `_env` gained a `parent: dict |
None = None` parameter, exactly mirroring `preview.compile_env`'s own seam:
`src = os.environ if parent is None else parent`, then `_INHERITED` names
are read from `src`. Added
`test_env_never_inherits_the_parent_environment`, which calls
`provenance._env({"PATH": "/bin", "LANG": "C", "ANTHROPIC_API_KEY":
"sk-should-not-travel"})` and asserts `set(env)` equals exactly `{"PATH",
"LANG", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL",
"GIT_COMMITTER_EMAIL", "GIT_CONFIG_NOSYSTEM"}` (seven, not six — see the
`GIT_CONFIG_NOSYSTEM` fix below, added in this same round), plus that
`ANTHROPIC_API_KEY` and `HOME` are absent.

**IMPORTANT 2 — root-commit invariant untested; ruled fix.** `_commit`
gained a keyword-only `when: str | None = None`; when given, it merges
`{"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}` into the
subprocess env via a new `_git(..., extra_env=...)` parameter.
`ensure_repo` now calls `_commit(repo, empty_tree, [], "empty root",
when=_ROOT_COMMIT_DATE)` where `_ROOT_COMMIT_DATE = "@0 +0000"`. Added:
- `test_ensure_repo_never_recreates_the_root_commit_once_set` — the
  ruled test-side guard: `monkeypatch.setattr(provenance, "_commit",
  boom)` after a first `ensure_repo`, then a second `ensure_repo` must not
  raise.
- `test_the_root_commit_is_the_same_sha_in_every_repo` — builds two
  independent repos under `tmp_path` and asserts their `_root` SHAs are
  equal, directly demonstrating the "one well-known SHA" property the
  ruling asked for, not just non-regression.

Date-format note: the brief/ruling didn't specify git's date syntax, and
my first attempt, `"0 +0000"`, failed with `fatal: invalid date format` —
git's raw internal format needs an `@` prefix on the timestamp when fed
through `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` (verified manually across
`"0 +0000"`, `"@0 +0000"`, and two ISO forms; only the `@`-prefixed and
ISO forms parsed). Used `"@0 +0000"`.

**IMPORTANT 3 — `_tree` entry injection.** Rewrote `_tree` to NUL-delimit
records and call `mktree -z` instead of newline-joining and calling plain
`mktree`; a name containing `"\x00"` now raises `ProvenanceError` before
any git call. Added three tests: `test_tree_entry_names_cannot_smuggle_extra_entries`
(the reviewer's exact payload — a name containing an embedded `<mode>
<type> <sha>\t<name>` record — asserted via `ls-tree -z` to land as
exactly one entry with the literal name), `test_tree_entry_names_with_an_embedded_tab_stay_one_entry`,
and `test_tree_refuses_a_name_containing_a_nul_byte`.

**Smaller item 1 — symlink handling.** `ensure_repo` now checks
`repo.is_symlink()` before `repo.exists()`/`repo.is_dir()` (both of which
follow the link) and calls `repo.unlink()` in that case, rather than
falling into the `is_dir()` → `shutil.rmtree` branch, which refuses a
symlink with an uncaught `OSError`. Added
`test_a_symlinked_repo_path_is_rebuilt_not_raised`.

**Smaller item 2 — `GIT_CONFIG_NOSYSTEM`.** Added `env["GIT_CONFIG_NOSYSTEM"]
= "1"` to `_env`. See "Where I diverged from the review" below — the
specific mechanism cited didn't hold up under my own testing, but a
different, real mechanism did, and I kept the fix on that basis.

**Smaller item 3 — document `HOME` omission.** Expanded the `_INHERITED`
comment block to state explicitly that `HOME` is never set, why (blocks a
git subprocess from reading `~/.gitconfig`, which would otherwise let a
developer's own `user.name`/`user.email` silently override the pinned
`_AUTHOR_NAME`/`_AUTHOR_EMAIL`), and what happens if a future change adds
it back.

**Smaller item 4 — argument-list leak in errors.** `_git` now computes
`subcommand = args[0] if args else "?"` and both the timeout and
non-zero-exit `ProvenanceError` messages name only `subcommand`, never
`' '.join(args)`. Added `test_a_git_failure_never_leaks_the_full_argument_list`,
which fails a `commit-tree` call carrying a marked "sensitive" message and
asserts that string is absent from the exception text.

## Where I diverged from the review, and why

The review stated the reviewer "confirmed `commit-tree` honours
`commit.gpgsign = true` from config" on this same host/git version
(2.43.0), and that this was the failure mode `GIT_CONFIG_NOSYSTEM` fixes.
I could not reproduce this. Manually, using `GIT_CONFIG_SYSTEM` to point
at a fake system config with `commit.gpgsign = true` (and separately with
`gpg.program` pointing at a script that would prove invocation), `git
commit-tree` on this host **ignores `commit.gpgsign` entirely** and
succeeds without ever invoking the configured `gpg.program` — confirmed
by `git-commit-tree(1)`: signing only happens when `-S`/`--gpg-sign` is
given explicitly on the command line, which this module's `_commit` never
does. Explicitly passing `-S` does honor `gpg.program` from the same
config (confirmed the config path itself works), so the discrepancy is
specifically about `commit-tree` not auto-signing from config, not about
`GIT_CONFIG_SYSTEM`/config-loading being broken in my test.

Rather than silently drop the fix or silently accept the stated
justification, I looked for a mechanism in the same class that *does*
reproduce, since the module's actual command surface (`hash-object`,
`mktree`, `commit-tree`, `update-ref`, `rev-parse`, `ls-tree`, `show`,
`init`) is git plumbing, not `commit`, and plumbing is generally supposed
to be config-inert. I found one: **`core.hooksPath` set system-wide makes
`update-ref` run an arbitrary `reference-transaction` hook script** on
every ref update — verified by pointing `GIT_CONFIG_SYSTEM` at a config
with `core.hooksPath` set to a directory containing an executable
`reference-transaction` script, running a plain `update-ref
refs/heads/probe <sha>`, and observing the script's stderr output twice
(`prepared` and `committed` stages) with the ref update's own SHA/name on
stdin; the same command with `GIT_CONFIG_NOSYSTEM=1` produced no hook
output at all. This is arguably a more serious instance of the exact
class of hole described (arbitrary script execution vs. a failed/altered
commit), and it fires on `_update_ref`, which every task from here on
calls. I kept `GIT_CONFIG_NOSYSTEM = "1"` on this verified basis and
rewrote the code comment to state plainly what did and didn't reproduce,
rather than repeat an unverified claim in the source.

I did not add an automated regression test for this specific mechanism:
`_env`'s whole design deliberately does not forward arbitrary variables
like `GIT_CONFIG_SYSTEM` from the test process into the git subprocess
(that's the same guarantee `test_env_never_inherits_the_parent_environment`
pins), so there's no way to exercise the real vulnerable path — a genuine
`/etc/gitconfig` — without writing to shared host state, which isn't
appropriate for a test. `test_env_never_inherits_the_parent_environment`'s
exact-key-set assertion does already pin `GIT_CONFIG_NOSYSTEM`'s presence
in the returned dict (falsified below), which is the closest test-level
guarantee available.

## Falsification (mutation testing), each reproduced and reverted

Per the coordinator's mandate, every new/changed assertion was falsified
in place — code mutated to reintroduce the exact hole, target test rerun
to confirm it fails, then reverted (confirmed back to the fixed state by
rereading the file) before moving to the next:

1. **Env guarantee**: replaced `_env`'s body with `dict(src)` (copying the
   parent wholesale) instead of the `_INHERITED`-only comprehension.
   `test_env_never_inherits_the_parent_environment` failed with
   `ANTHROPIC_API_KEY` present in the returned set, exactly as the
   reviewer's `dict(os.environ)` mutation produced 11/11 passed under the
   *old* test suite. Reverted.
2. **Root-commit invariant**: removed the `if _ref_sha(...) is None:` guard
   in `ensure_repo` so `_commit`/`_update_ref` for `_root` ran
   unconditionally on every call. `test_ensure_repo_is_idempotent`
   (existing) still passed — confirming the reviewer's point that it
   cannot catch this — while the new
   `test_ensure_repo_never_recreates_the_root_commit_once_set` correctly
   failed (`AssertionError: _commit must not run once the root ref is
   set`). Reverted.
3. **Tree injection**: reverted `_tree` to the original newline-joined
   `mktree` (no `-z`, no NUL guard).
   `test_tree_entry_names_cannot_smuggle_extra_entries` failed, showing
   the exact two-entry split the reviewer described (`results.tex` and a
   `smuggled.tex` entry pointing at the injected blob);
   `test_tree_refuses_a_name_containing_a_nul_byte` failed with "DID NOT
   RAISE". (`test_tree_entry_names_with_an_embedded_tab_stay_one_entry`
   passed under this mutation too — expected and noted below, not a
   flaw.) Reverted.
4. **Symlink handling**: removed the `is_symlink()` branch in `ensure_repo`,
   restoring the plain `if repo.exists(): ...` structure.
   `test_a_symlinked_repo_path_is_rebuilt_not_raised` failed with an
   uncaught `OSError: Cannot call rmtree on a symbolic link` propagating
   out of the test entirely (not just an assertion failure). Reverted.
5. **`GIT_CONFIG_NOSYSTEM` presence**: removed the
   `env["GIT_CONFIG_NOSYSTEM"] = "1"` line.
   `test_env_never_inherits_the_parent_environment` failed (`Extra items
   in the right set: 'GIT_CONFIG_NOSYSTEM'`), confirming this test also
   pins that key's presence even though no automated test exercises the
   `core.hooksPath` mechanism itself (see above). Reverted.
6. **Argument-list leak**: restored `f"git {' '.join(args)} failed: ..."`
   in `_git`'s non-zero-exit branch.
   `test_a_git_failure_never_leaks_the_full_argument_list` failed with the
   marked sensitive string present in the exception text. Reverted.

One note on falsification 3: the tab-only test (`..._with_an_embedded_tab_stay_one_entry`)
does not fail under the plain-`mktree` mutation, and that's expected, not
a gap — a bare tab (with no embedded newline) never forms a second
newline-delimited record with plain `mktree`; only the newline-containing
payload does. That test instead confirms the `-z` fix doesn't *break*
legitimate names containing tabs, which is a real thing worth pinning
given `_tree`'s own record format also uses tab as the mode/name
separator.

## Test results after the fix round

- `uv run pytest tests/core/test_provenance.py -v` — **19 passed** (11
  original + 8 new).
- `uv run pytest -q` (full suite) — **1501 passed, 5 skipped, 6
  deselected, 2 warnings** in ~128s, exit code 0. (1493 before this round
  + 8 new = 1501, matches exactly.)

Captured tail of the `-q` run:
```
........................................................................ [ 86%]
........................................................................ [ 90%]
........................................................................ [ 95%]
..................................................................       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  .../fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  .../starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1501 passed, 5 skipped, 6 deselected, 2 warnings in 128.04s (0:02:08)

[exited with code 0]
```

## Concerns

One factual correction to the review is carried forward for whoever reads
this next: `commit.gpgsign` does not affect `commit-tree` on git 2.43.0
absent an explicit `-S`, contrary to what the review stated the reviewer
found. `GIT_CONFIG_NOSYSTEM` is still correct to keep — verified via
`core.hooksPath` triggering an arbitrary `reference-transaction` hook on
`update-ref` instead — but a maintainer reading only the original review
text (not this report) would be relying on an unverified claim. No
automated test exercises the `core.hooksPath` mechanism itself, for the
reason given above (it requires a real system-level config file, which
`_env`'s own sealing correctly prevents from being simulated via
environment variables). Otherwise, no outstanding concerns: all six fix
items are implemented, each new/changed assertion was falsified and
reverted, and the full suite is green.

---

# Fix round 2

Commit: `7b5a10f` — "fix(core): pin the root commit's SHA as a literal,
not a same-second comparison".

## What review found

All seven items from fix round 1 came back ADDRESSED, including both
`GIT_CONFIG_SYSTEM`-based reproductions of the gpgsign correction and the
`core.hooksPath` mechanism, and the adversarial `mktree -z` probing (tab,
newline, a 100,000-character name, empty entries, NUL). One finding
remained: `test_the_root_commit_is_the_same_sha_in_every_repo` built two
repos moments apart and compared their root SHAs, which cannot actually
tell whether `_ROOT_COMMIT_DATE` is wired up — two `commit-tree` calls
with identical tree/parents/message/identity collide on SHA within the
same wall-clock second regardless of the date argument, so the test
stayed green even with `when=_ROOT_COMMIT_DATE` deleted from
`ensure_repo` entirely. This was the third time this exact same-second
collision produced false confidence in this task (after
`test_ensure_repo_is_idempotent` in round 1, and the embedded-tab tree
test I flagged myself in round 1's report).

## What changed

Replaced `test_the_root_commit_is_the_same_sha_in_every_repo` with
`test_the_root_commit_is_a_known_sha`, which asserts the root commit's
SHA against a hardcoded literal, `90f8e5daab6c2ed97a857cd6f9a6fe65c1158122`,
rather than comparing two freshly built repos. The docstring explains why
a literal is the right assertion (fully determined by five fixed inputs:
the empty tree, no parents, the message, the identity, and
`_ROOT_COMMIT_DATE`) and recounts the three same-second failures this
task has already produced, so a later reader doesn't "simplify" it back
into a two-repo comparison.

The constant was produced two ways and cross-checked: (1) calling
`provenance.ensure_repo` against a scratch workspace and reading back
`_ref_sha(repo, EMPTY_ROOT_REF)`, and (2) independently, a bare
`git commit-tree` invocation built by hand from the same five inputs
(`GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME=ScieFlow`,
`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_EMAIL=provenance@scieflow.local`,
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE="@0 +0000"`, tree
`4b825dc642cb6eb9a060e54bf8d69288fbee4904`, no parents, message
`"empty root"`). Both produced `90f8e5daab6c2ed97a857cd6f9a6fe65c1158122`.

## Falsification

Removed `when=_ROOT_COMMIT_DATE` from `ensure_repo`'s `_commit` call
(leaving the idempotency guard — `if _ref_sha(...) is None:` — intact,
exactly as the re-reviewer's mutation did) and reran the three related
tests:
- `test_the_root_commit_is_a_known_sha` — **FAILED**, root SHA came back
  as `4173a8fea597c522d39d077e8c3a1c533d5d1022` instead of the pinned
  constant.
- `test_ensure_repo_never_recreates_the_root_commit_once_set` — still
  passed (unaffected; it guards a different invariant).
- `test_ensure_repo_is_idempotent` — still passed, confirming again that
  this test cannot catch a wrong date.

Restored `when=_ROOT_COMMIT_DATE` and reran all three: all green.

## Test results

- `uv run pytest tests/core/test_provenance.py -v` — **19 passed** (same
  count as round 1; one test replaced, not added).
- `uv run pytest -q` — **1501 passed, 5 skipped, 6 deselected, 2
  warnings** in 128.09s, exit code 0 (same counts as round 1's end state,
  as expected for a like-for-like test replacement).

Captured tail:
```
........................................................................ [ 86%]
........................................................................ [ 90%]
........................................................................ [ 95%]
..................................................................       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  .../fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  .../starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1501 passed, 5 skipped, 6 deselected, 2 warnings in 128.09s (0:02:08)

[exited with code 0]
```

## Concerns

None. The monkeypatch guard test
(`test_ensure_repo_never_recreates_the_root_commit_once_set`) was left
exactly as it was, per instruction. No further ambiguity to flag on the
replaced test — its docstring already states plainly why a literal SHA
is required here.
