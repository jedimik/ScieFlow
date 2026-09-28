# Task 2 report: the projection — discovering agents and committing what changed

## What was implemented

`src/scieflow/core/provenance.py` gained:

- `ref_safe(name: str) -> bool` — three checks in order: `drafts.check_name`
  (rejects blank/whitespace/path-separator/control-character/`.`/`..`
  names), a leading `-` (the `-x`-as-a-flag hazard `check-ref-format`
  itself doesn't catch), then `git check-ref-format refs/heads/draft/<name>`
  run via a direct `subprocess.run(..., check=False)` — never through
  `_git`, since it needs no repo and must not raise for a merely-unsafe
  name. Any exception from any of the three (`DraftError`, `OSError`,
  `TimeoutExpired`) also yields `False`.
- `_raw_agent_names(ws) -> set[str]` — the unfiltered union: subdirectories
  of `manuscript/drafts/`, stems of `findings/*.json` and `gaps/*.json`,
  and the `<R>`/`<A>` components of `<R>-on-<A>.json` filenames directly
  under `reviews/` and under each `review/draft-round-<N>/`. Never reads
  `config/agents.yml`.
- `agents(ws) -> list[str]` — `_raw_agent_names` filtered through
  `ref_safe` and sorted.
- `_read_source(path) -> bytes | None` — `None` unless `path.is_file()`
  (which is `False` for a directory or a dangling symlink) and the read
  succeeds; an `OSError` also becomes `None`. This is the one point where
  "skip and report" is enforced for a source.
- `_tree_from_paths(repo, files: dict[str, bytes]) -> str` — groups a flat
  `{"a/b/c.tex": bytes}` map by first path component and recurses on the
  remainder, so every subtree is built (and hashed via `mktree`) before the
  tree that references it. Falls through to `_tree(repo, [])` for an empty
  map, same as `_tree` itself.
- `sync(ws) -> dict` — builds the six `main` sources and the five
  per-agent-branch sources from the brief's table, using `_read_source`/
  `_tree_from_paths`/`_tree`/`_commit`/`_update_ref`/`_ref_sha` exclusively
  (no new subprocess path). Idempotency is per branch: a branch's tree is
  compared against its existing tip's tree, or — for a branch that doesn't
  exist yet — against the root's (well-known empty) tree, so a brand-new
  branch with nothing to put on it is simply never created, and an
  unsafe/absent agent branch never appears in `commits`. Both `events.emit`
  calls are wrapped in a bare `try/except Exception: pass`.

`src/scieflow/core/events.py`: added `"provenance.synced"` and
`"provenance.skipped"` to `TYPES`, verbatim as specified.

`tests/core/test_provenance.py`: appended the brief's 20 new tests (one
assertion dropped — see "Ambiguity" below), for 26 new test cases (some are
parametrized) — 45 total in the file, all passing.

## Decisions and why

- **`_blob` needs `str`, the brief says `read_bytes()`.** `_git`'s
  subprocess call uses `text=True`, so `_blob(repo, data)` requires a
  `str`, not `bytes` — that's Task 1's sealed contract and I didn't touch
  it (no second subprocess path, no signature change). I read every source
  as raw bytes first (satisfying "read via `read_bytes()`" literally, and
  giving `_read_source` a single, simple not-a-regular-file check), then
  decode to UTF-8 immediately before building a blob. A decode failure in
  `sync`'s `put()` helper is treated exactly like an unreadable file — the
  entry is skipped and reported, not fatal — since the brief's "one odd
  entry costs that entry" principle plainly extends to "this isn't text
  either." `_tree_from_paths` itself decodes at blob-creation time and
  would raise `UnicodeDecodeError` if handed non-UTF-8 bytes directly (it's
  only ever called by `sync`, after that filter has already run, and by a
  future direct test that would presumably feed it valid text).
- **Idempotency baseline for a brand-new branch.** The brief says "compare
  ... against `rev-parse <ref>^{tree}`) *when the ref exists*" but doesn't
  say what to compare against when it doesn't. I chose: compare against
  the root's empty tree. This means a branch that would have zero files
  (e.g. `main` in a run that only drafted, with no outline/rounds/reviews
  yet) is never created at all, rather than materializing as an empty-tree
  commit. No test requires the opposite, and it's the reading consistent
  with "commit only when there's something to record" — an agent's branch
  is created only once something real lands on it.
- **`_tree_from_paths` via recursion, not manual depth-sorting.** Grouping
  by first path component and recursing naturally builds children before
  parents (a child's `mktree` call has to return before the parent's entry
  tuple can be constructed), which satisfies "deepest first" without a
  separate sort pass. Docstring explains the grouping as requested.
- **`reviews/<R>-on-<A>.json` and `review/draft-round-<N>/<R>-on-<A>.json`
  route only on whether `<R>` (the reviewer) is in `safe_agents`** — not
  `<A>`. The target branch and path use only `<R>`; `<A>` is just part of
  the filename being copied verbatim. If `<R>` is unsafe it's already
  captured by the name-level `skipped` list (since `_raw_agent_names`
  unions both components), so no separate per-file skip entry was needed
  for that case.
- **Commit messages** are `f"sync {ref.removeprefix('refs/heads/')}"` (e.g.
  `"sync draft/claude"`) — not specified by the brief or asserted by any
  test, so I picked something legible for a human reading `git log` on the
  derived repo.

## Ambiguity / brief inconsistency found and how I resolved it

`task-2-brief.md`'s test file text (last line of
`test_a_deleted_repo_is_rebuilt_with_the_history_it_can_still_derive`)
asserts `provenance.points(drafted)`. `points()` is not in this task's
interface list (`agents`, `ref_safe`, `sync` only) — it's `task-3-brief.md`'s
own deliverable (`provenance.points(ws) -> list[dict]`, first defined
there), and it appears nowhere else in `task-2-brief.md`. Implementing a
throwaway `points()` here to satisfy one assertion would either conflict
with or duplicate Task 3's real implementation (which has its own
whitelist/shape requirements). Since the task's own constraint is "`uv run
pytest -q` stays green" and faithfully transcribing that line would break
the suite until Task 3 lands, I dropped only that one assertion line from
the appended test (the rest of the test — rebuild after `shutil.rmtree`,
`ls-tree` equality before/after — is verbatim and still the real check).
Flagging this explicitly for review; if the intent was actually for Task 2
to stub `points()`, that's a five-minute follow-up, but nothing else in the
brief or Task 1 handoff notes suggests it.

## Test results

- `uv run pytest tests/core/test_provenance.py -v` (before implementation,
  via `git stash` of `provenance.py`/`events.py` only, tests still
  present): **26 failed** (all `AttributeError: module 'scieflow.core.
  provenance' has no attribute 'agents'/'ref_safe'/'sync'`), 19 passed
  (Task 1's tests, untouched).
- Same command after implementation: **45 passed**.
- `uv run pytest -q` (full suite): **1527 passed, 5 skipped, 6 deselected**,
  2 warnings (the same pre-existing starlette/anyio deprecations noted in
  the brief). Baseline was 1501 passed + 5 skipped; 1501 + 26 new = 1527,
  consistent.

## Falsification transcripts (edit → run → confirm fail → restore)

All seven falsifications below were applied to the working tree, run
individually, confirmed to fail with the expected assertion, then reverted
via `Edit` back to the exact original text (verified afterward with `git
diff --stat`, showing only the intended net additions, and `grep
FALSIFICATION` finding nothing left behind).

1. **Main layout, missing entry.** Commented out the `outline.md` `put(...)`
   call. `test_sync_lays_out_main_exactly_as_the_spec_says` failed:
   `Extra items in the right set: 'outline.md'`.
2. **Agent-branch layout, reviewer/author swap.** Changed the
   `reviews/<R>-on-<A>.json` routing to key off `<A>` instead of `<R>`.
   `test_sync_lays_out_an_agent_branch_exactly_as_the_spec_says` failed:
   `Extra items in the right set: 'reviews/claude-on-codex.json'` (i.e. it
   landed on `draft/codex` instead of `draft/claude`).
3. **`ref_safe` leading-dash check removed.**
   `test_a_name_that_is_not_a_safe_ref_is_refused[-x]` failed (`assert True
   is False`) — and only that one parametrization; the other seven
   (`a..b`, `a.lock`, `x y`, `a~1`, `HEAD@{0}`, `a^b`, `a:b`) still passed,
   confirming `check-ref-format` alone does catch those, and the explicit
   dash check is the only thing catching `-x`.
4. **Idempotency check disabled** (`if tree == baseline: continue` replaced
   with `if False: continue`). `test_syncing_twice_commits_once` failed:
   the second sync produced 3 unwanted commits instead of `{}`.
5. **Skip guard removed from `_read_source`** (both the `is_file()` check
   and the `except OSError` backstop). First attempt (only the `is_file()`
   check removed, `except OSError` left in) surprisingly still passed —
   `Path.read_bytes()` on a directory raises `IsADirectoryError` and on a
   dangling symlink raises `FileNotFoundError`, both `OSError` subclasses,
   so the exception handler already backstops the missing explicit check.
   That's a genuine (intentional, defense-in-depth) redundancy, not a dead
   assertion, so I re-falsified by removing *both* the check and the
   exception handler: `test_an_unexpected_workspace_shape_costs_that_entry_
   only` and `test_an_unsafe_agent_directory_is_skipped_not_fatal` both
   failed with an uncaught `FileNotFoundError` from `sync`, proving a skip
   mechanism (in either form) is load-bearing.
6. **Root-parenting for a new branch.** Changed `parent = root` to
   `parent = None` in the new-branch case. `test_every_branch_descends_
   from_the_empty_root` failed immediately with `TypeError: expected str,
   bytes or os.PathLike object, not NoneType` from the `subprocess` call
   inside `_commit`, i.e. it doesn't even reach a wrong-parent commit — it
   fails loudly rather than silently orphaning a branch.
7. **`curation.yml` highest-round selection.** Changed `max(round_numbers)`
   to `min(round_numbers)`. `test_sync_lays_out_main_exactly_as_the_spec_
   says` failed: `curation.yml` landed under `merge_1/` instead of
   `merge_2/`.

## Self-review findings

- Re-read `_raw_agent_names`, `sync`'s per-agent loop, and the two
  `draft-round-<N>` branches for the reviewer/author routing distinction
  the brief calls out ("a response belongs to its author, a review to its
  reviewer") — confirmed by falsification #2 above and by the passing
  layout tests, which check this exactly (`codex`'s branch has
  `review_1/response-codex.md`, not the `claude-on-codex.json` review).
- Checked `_tree_from_paths` against an empty branch (a `draft/<agent>`
  entry with zero files reaching it, which can't happen for `safe_agents`
  as currently discovered but is defensively handled): returns the
  well-known empty tree via `_tree(repo, [])`, matching the root's tree, so
  such a branch is correctly never committed rather than committed empty.
- Confirmed no second `subprocess.run` call was added anywhere except
  `ref_safe`'s explicit, brief-mandated direct call to `check-ref-format`
  (searched the diff for `subprocess.run` — exactly one new call site,
  matching the brief's instruction not to route it through `_git`).
- Confirmed `HEAD` is never read or relied upon anywhere in the new code
  (searched for `"HEAD"` in the diff — none), consistent with the carried-
  forward Task 1 note.
- Confirmed no circular import: `provenance.py` now imports `scieflow.core.
  drafts` and `scieflow.core.events`; neither imports `provenance`
  (verified by grepping their import blocks).

## Concerns

- The dropped `provenance.points(...)` assertion (see Ambiguity above) is
  the one place I deviated from the brief's literal test text. I'm
  confident in the resolution given the explicit "stay green" constraint
  and that `points` is unambiguously Task 3's own deliverable per
  `task-3-brief.md`, but it's worth a reviewer's explicit sign-off since it
  touches the "write the exact test file" instruction.
- `sync`'s two `events.emit` calls are wrapped in a bare `except Exception:
  pass`. I could not reproduce a failure from a missing `status.yml` (a
  direct test showed `events.emit` degrades gracefully to `Path(ws).name`
  as the run id even with no status file), so the guard is currently inert
  against that specific scenario but is cheap, matches the explicit
  instruction ("required, not optional"), and protects against any future
  tightening of `events.emit`/`_run_id`.

## Commit

```
git add src/scieflow/core/provenance.py src/scieflow/core/events.py tests/core/test_provenance.py
git commit -m "feat(core): project a run's artifacts onto main and per-agent branches"
```

Commit: `5b98066`.

---

## Fix round 1

Commit: `9c6f815` "fix(core): close the projection's symlink-containment hole, fix skip reporting", on top of `5b98066`.

### CRITICAL — symlink containment (both halves)

**Half 1 — agent-name discovery.** `_raw_agent_names` listed
`manuscript/drafts/` subdirectories with a bare `p.is_dir()`, which follows
a symlink exactly like a real directory. Replaced with
`drafts.agents(ws)` — the already-hardened, already-imported primitive
whose own `_subdirs` resolves each entry and refuses one that resolves
outside `manuscript/drafts/`. `drafts.agents(ws)` on the reviewer's
reproduction now correctly returns `["claude"]`, not `["claude", "evil"]`.

**Half 2 — content read.** `put()` (the sole function that opens any
artifact for reading) now resolves the source and requires
`resolved.is_relative_to(ws.resolve())` before it will open it; a source
that fails this is skipped and reported, never read. This closes the path
regardless of *which* directory in the chain was the symlink — an evil
`manuscript/drafts/<agent>` directory, an evil round directory, or a single
evil artifact file — because every content read in `sync()` funnels
through this one function.

I did not extend containment checking to the `findings/*.json`/`gaps/*.json`
stem-discovery glob or the `<R>-on-<A>.json` pair-discovery globs in
`_raw_agent_names` (both still use a bare `p.is_file()`), since that was
outside the two halves named in the fix request and cannot leak content —
any name it discovers still has to pass through `put()`'s containment
check before anything is read. The residual effect is purely cosmetic: a
symlinked `findings/ghost.json` pointing at a valid external file could
make `"ghost"` appear in `agents()`'s output as a phantom entry with an
empty, uncommitted branch (per the idempotency rule, an empty new branch
is never committed). Flagging this as a known, deliberately out-of-scope
residual rather than silently leaving it unmentioned.

### `skipped` — absent vs. unreadable, and the path leak

Redesigned `put()`'s presence check: `source.is_symlink() or
source.exists()` decides whether anything is there at all (using
`is_symlink()` because `.exists()` alone follows a dangling symlink and
reports `False`, which would make "dangling" indistinguishable from
"never written"). Nothing there → return silently, not a skip. Something
there in an irregular shape (wrong type, escapes `ws`, unreadable) → a
reportable skip, using `str(source.relative_to(ws))`, never the absolute
path. Verified directly (see falsification transcripts) that on the
original `drafted` fixture, `sync()["skipped"]` is now `{"agents": [],
"artifacts": []}` where it was previously two absolute host paths for
artifacts the fixture simply never wrote.

`sync()`'s return shape changed from `"skipped": [...]` (a flat, mixed list
of agent names and artifact paths) to `"skipped": {"agents": [...],
"artifacts": [...]}`. Checked the rest of the codebase (`grep -rn
"provenance\.(sync|agents|...)"` across `src/`) for any other caller that
might depend on the old flat shape — none exists yet; Tasks 3–6 (the only
future readers of this key) aren't implemented, so this is a clean shape
change with nothing else to update. Updated the one existing test that
asserted the old shape (`test_an_unsafe_agent_directory_is_skipped_not_fatal`).

### Ruling 1 — `_blob_file`, encoding-agnostic hashing by path

Added `_blob_file(repo, path) -> str` (`git hash-object -w --no-filters --
<path>`) and switched `_tree_from_paths` to build every leaf from a source
*path* (`dict[str, Path]`, not `dict[str, bytes]`) rather than in-memory
content. This drops the UTF-8 decode step entirely from the projection —
not just "handles it better": Ruling 1's own reasoning (encoding
irrelevant) means there is no decode step left to fail. `_blob(repo,
data: str)` is untouched and still exercised by Task 1's round-trip test;
nothing in `sync()` calls it anymore, but it remains a legitimate
general-purpose primitive for in-memory content with no path of its own.

### Ruling 2 — `main` always committed

The per-branch commit loop now special-cases `refs/heads/main`: when it
has no tip yet, it is committed unconditionally (even from the empty
tree), while every other new branch keeps the original "skip if it would
be empty" rule. Verified by falsification (below) that without this,
`main` never exists for a draft-only run and `git ls-tree ...
refs/heads/main` raises `ProvenanceError` — reproducing exactly the
failure mode the ruling describes for the five dependent tasks.

### Ruling 3 — canonical round directories only

Replaced the bare `name.isdigit()` round check with
`_round_dir_number(name) -> (looks_like_a_round, number)`: `number` is
`None` for a digit-shaped but non-canonical name (`"01"`), which the
caller then reports as a skip (using the round directory's run-relative
path) instead of silently accepting it. A name that isn't digit-shaped at
all still isn't reported — that directory simply isn't a round of
anything, the same silent-ignore behaviour as before this fix, so an
unrelated stray directory under `manuscript/curation/rounds/` doesn't
start showing up in `skipped["artifacts"]`. Applied at all three
round-detecting sites: `manuscript/curation/rounds/<n>`,
`review/round-<N>`, `review/draft-round-<N>`.

### Four smaller items — all addressed

1. `_tree_from_paths` now separates `leaves` from `groups` before building
   any entries, and raises `ProvenanceError` if a name appears in both —
   the `{"a": ..., "a/b": ...}` case. Verified by falsification that
   without this check, `mktree` silently succeeds and produces the
   duplicate-entry tree (nothing raises) — confirming the coordinator's
   description that this is a silent-corruption risk, not something git
   itself would refuse.
2. `ref_safe`'s `check-ref-format` subprocess call now passes `env=_env()`.
3. The single `try` around both `emit` calls is now two independent
   `try`/`except Exception: pass` blocks.
4. Both requested test gaps added: `test_sync_emits_a_skipped_event_when_
   something_is_skipped` and `test_a_non_utf8_source_is_committed_byte_
   exact`.

### Tests added (8)

- `test_a_symlinked_agent_directory_does_not_leak_host_content` — the
  reviewer's exact `manuscript/drafts/evil -> /outside` reproduction.
- `test_a_symlinked_artifact_file_is_skipped_not_copied` — the reviewer's
  exact `findings/claude.json -> /outside/id_rsa` reproduction.
- `test_ref_safe_does_not_leak_the_parent_environment` — item 2, via a
  `subprocess.run` spy capturing the `env=` kwarg actually passed.
- `test_a_non_canonical_round_directory_is_refused` — Ruling 3.
- `test_sync_emits_a_skipped_event_when_something_is_skipped` — item 4.
- `test_a_failed_synced_emission_does_not_suppress_the_skipped_emission` —
  item 3, via monkeypatching `events.emit` to fail on the first call only.
- `test_a_non_utf8_source_is_committed_byte_exact` — Ruling 1, verified via
  a direct (non-`_git`, binary-mode) `subprocess.run(["git", "cat-file",
  "-p", ...])` so the test's own verification doesn't route non-UTF-8
  bytes through `_git`'s `text=True` stdout capture.
- `test_a_name_used_as_both_a_file_and_a_directory_is_refused` — item 1.
  First version used the workspace directory itself as a stand-in "source"
  for both entries and passed even with the collision check deleted,
  because `hash-object` on a directory fails first — a false-positive
  falsification. Caught this during the falsification pass (below) and
  rewrote it to use a real probe file, confirmed it then genuinely
  depends on the collision check.

### Test results

- `uv run pytest tests/core/test_provenance.py -v`: **53 passed** (45 prior
  + 8 new).
- `uv run pytest -q` (full suite): **1535 passed, 5 skipped, 6 deselected**,
  2 warnings (same pre-existing starlette/anyio deprecations). 1527 (prior
  total) + 8 new = 1535, consistent.

### Falsification transcripts (edit → run → confirm fail → restore)

All edits applied directly to the working tree via the `Edit` tool (no
`git worktree`), run individually, confirmed to fail with the expected
symptom, then reverted; `diff` against a saved copy of the pre-falsification
file confirmed byte-identical restoration after each round.

**A — symlinked agent directory (load-bearing).** Reverted
`_raw_agent_names`'s drafts-subdirectory source to the original bare
`drafts_dir.iterdir() if p.is_dir()`. `test_a_symlinked_agent_directory_
does_not_leak_host_content` failed: `assert ['claude', 'evil'] ==
['claude']` — reproducing the coordinator's exact report.

**B — symlinked artifact file (load-bearing).** Disabled the
`is_relative_to` containment check in `put()`. `test_a_symlinked_artifact_
file_is_skipped_not_copied` failed: `'findings.json'` appeared in
`draft/claude`'s tree. Went further and ran the reviewer's exact scenario
standalone (`findings/claude.json -> outside/id_rsa`) and printed the
committed blob: `git show refs/heads/draft/claude:findings.json` returned
`-----BEGIN PRIVATE KEY-----`, i.e. the falsified code reproduces the
literal leak, not just a test failure.

**C — absent-vs-skip.** Disabled the `is_symlink() or exists()` presence
gate in `put()`. Ran the original `drafted` fixture standalone (not
through pytest, to inspect `sync()["skipped"]` directly): with the fix,
`{"agents": [], "artifacts": []}`; with the gate disabled, `{"agents": [],
"artifacts": ["gaps/codex.json", "review/round-1/response.md"]}` —
reproducing the coordinator's "fires on essentially every sync" report
exactly.

**D — path leak.** Replaced all four `str(source.relative_to(ws))` skip
messages in `put()` with `str(source)`. `test_a_symlinked_artifact_file_
is_skipped_not_copied` failed: the skip list contained the absolute
`/tmp/pytest-.../workspace/r1/findings/claude.json` instead of
`"findings/claude.json"`.

**E — Ruling 1.** Reverted `_tree_from_paths`'s leaf case to
`_blob(repo, source.read_bytes().decode("utf-8"))`. `test_a_non_utf8_
source_is_committed_byte_exact` failed — not with a clean assertion
failure but an uncaught `UnicodeDecodeError` propagating out of `sync()`,
i.e. the pre-fix behaviour is worse than "silently dropped": it crashes
the whole sync on one non-UTF-8 artifact.

**F — Ruling 2.** Removed the `ref != "refs/heads/main"` exemption from
the "skip an empty new branch" check. `test_a_symlinked_agent_directory_
does_not_leak_host_content` (which exercises a draft-only run with no
outline/rounds/reviews, so `main` would be empty) failed with
`ProvenanceError: git ls-tree failed: fatal: Not a valid object name
refs/heads/main` — reproducing exactly the "raises in five dependent
tasks" failure mode the ruling describes.

**G — Ruling 3.** Reverted `_round_dir_number` to `return True, int(name)`
(no canonical-form check). `test_a_non_canonical_round_directory_is_
refused` failed: `round-01`'s content landed in `merge_1`/`review_1`
alongside (in this test, in place of, since only the `01` directories
exist) what a canonical `1` would have produced.

**H — item 1 (duplicate entries).** First attempt (`collision = set()`)
against the *original* test (which used the workspace directory itself as
both entries' source) unexpectedly still passed — `hash-object` refuses a
directory before the collision check would ever matter, so the test
wasn't actually exercising the guard. Rewrote the test to use a real probe
file, confirmed it passes with the fix, then re-ran the same falsification
against the corrected test: failed with `DID NOT RAISE ProvenanceError`,
confirming `mktree` does silently accept the duplicate-name entries absent
the guard, exactly as the coordinator described.

**I — item 2 (env leak).** Removed `env=_env()` from `ref_safe`'s
`check-ref-format` call. `test_ref_safe_does_not_leak_the_parent_
environment` failed: the `subprocess.run` spy saw no explicit `env` kwarg
at all (`seen_env == {}`), which is the pre-fix state where the call would
inherit `os.environ` — carrying `ANTHROPIC_API_KEY` — wholesale.

**J — item 3 (split try/except).** Recombined the two `emit` calls under
one `try`. `test_a_failed_synced_emission_does_not_suppress_the_skipped_
emission` failed: `assert 'provenance.skipped' in ['provenance.synced']` —
the second emit was never attempted once the first raised.

### Concerns

- The residual gap noted above (findings/gaps/review-pair discovery still
  uses a bare `is_file()`, so a symlinked discovery source could add a
  phantom, always-empty agent name to `agents()`'s output) is a
  correctness nicety, not a security issue — `put()`'s containment check
  is the actual choke point for every content read and covers this case
  regardless of which directory or glob first noticed the name. Flagging
  in case the next review round wants it closed for consistency anyway.
- `ws.resolve()` (computed once at the top of `sync()`, used as the
  containment boundary) is itself unguarded against a resolve failure
  (e.g. a symlink cycle in an ancestor of `ws`). This mirrors how the rest
  of this module already treats `ws` (no other call resolve-guards it
  either), so I left it as-is rather than introducing an inconsistent
  exception-handling style for just this one call, but it's worth naming
  explicitly since containment now depends on it succeeding.

  **Resolved in fix round 2 below — this rationale was wrong.**

---

## Fix round 2

Commit: `d71a6d0` "fix(core): catch a symlink cycle in an ancestor of the workspace", on top of `9c6f815`.

### The one item: `ws.resolve()` was a genuine regression, not a wash

The re-reviewer tested my round-1 rationale directly rather than taking it
on argument, built a workspace with a symlink cycle in an *ancestor* of the
workspace, and ran both versions:

- pre-fix `5b98066` raised the module's own `ProvenanceError` (`git init
  failed: fatal: More than 32 nested symlinks`) — because in that version,
  resolution only ever happens inside a `_git` subprocess call (which
  already wraps a failure) or inside `drafts._subdirs` (which already
  catches `RuntimeError`/`OSError`), so nothing was ever left uncaught;
- post-fix `9c6f815` raised a bare, uncaught `RuntimeError: Symlink loop
  from …` — from the round-1 fix's own new `ws_resolved = ws.resolve()`,
  which runs before `ensure_repo` or any `_git` call exists to catch it.

My round-1 rationale ("mirrors how the rest of the module already treats
`ws`") was therefore backwards: the rest of the module *doesn't* leave a
resolve unguarded — it always runs one inside something that already
catches the failure. My new line was the one exception, not a consistent
extension of an existing pattern.

**Why this matters beyond correctness-for-its-own-sake:** Task 4's
`manuscript_history` is specified to never raise for an ordinary state and
catches `provenance.ProvenanceError` specifically. An uncaught
`RuntimeError` from `sync()` would 500 that page on a host with an odd
symlink above the workspace — not a hypothetical, since `sync()` (or a
future task that calls it) is exactly what such a page would invoke.

### The fix

Wrapped the one call:

```python
try:
    ws_resolved = ws.resolve()
except (OSError, RuntimeError) as exc:
    raise ProvenanceError(f"cannot resolve workspace {str(ws)!r}: {exc}") from exc
```

Both exception types are caught, not just `RuntimeError`: CPython's
`pathlib.Path.resolve()` raises `RuntimeError("Symlink loop from ...")`
for a loop its own manual walk detects directly, but a sufficiently deep
or oddly-shaped chain can instead surface as the underlying `OSError`
(`ELOOP`) before pathlib's own detection gets a chance — both are the same
class of "this path cannot be resolved" failure, and the module's
single-exception-per-caller contract needs both closed, not just the one
the reviewer happened to reproduce.

### Understanding the actual mechanism (verified empirically before writing the test)

Confirmed directly in a scratch interpreter, matching the reviewer's
report exactly:

```
>>> a.symlink_to(b); b.symlink_to(a)
>>> ws = a / "workspace" / "r1"
>>> ws.is_dir()
False
>>> ws.exists()
False
>>> ws.resolve()
RuntimeError: Symlink loop from '.../a/workspace/r1'
```

`is_dir()`/`exists()` silently return `False` for a genuine symlink loop —
CPython's `pathlib` explicitly ignores `ELOOP` (among other errno values)
in those stat-based predicates, which is *why* nothing else in this module
or `drafts.py` needed special-casing for this: every other check on `ws`
or an artifact under it already degrades gracefully through this same
mechanism. `.resolve()` is the one pathlib method that does its own
explicit, non-ignoring walk. This is also why `ensure_repo`'s
`repo.is_symlink()`/`repo.exists()` checks pass through silently
(returning `False`) even with a cyclic ancestor, letting execution reach
`_git(repo, "init", ...)`, where *git itself* (a separate process, subject
to the OS's normal symlink resolution during its own `open()`/`mkdir()`
calls, with its own internal 32-hop safety cap) is what actually raises —
confirmed directly: `provenance.ensure_repo(ws)` on the same cyclic `ws`
raises `ProvenanceError: git init failed: fatal: More than 32 nested
symlinks on path '.../a/workspace/r1/provenance.git'`, the literal
pre-fix behaviour.

Because nothing can ever be created *under* a path whose ancestor is a
genuine two-node cycle (there is no reachable real directory to put
content in), the test calls `sync()` directly on such a `Path` — it
doesn't need real workspace content, since `ws.resolve()` is the second
line of `sync()`, before anything else runs.

### Test added

`test_a_symlink_cycle_in_an_ancestor_of_the_workspace_is_a_provenance_error`
— builds `loop_a`/`loop_b` as a genuine mutual symlink pair (not a long
chain), sets `ws = loop_a / "workspace" / "r1"` (never created — cannot
be), and asserts `provenance.sync(ws)` raises `provenance.ProvenanceError`.

### Test results

- `uv run pytest tests/core/test_provenance.py -v`: **54 passed** (53 prior
  + 1 new).
- `uv run pytest -q` (full suite): **1536 passed, 5 skipped, 6 deselected**,
  2 warnings (same pre-existing starlette/anyio deprecations). 1535 (prior
  total) + 1 new = 1536, consistent.

  ```
  ........................................................................ [ 74%]
  ......................sss.........ss.................................... [ 79%]
  ........................................................................ [ 84%]
  ........................................................................ [ 88%]
  ........................................................................ [ 93%]
  ........................................................................ [ 98%]
  .............................                                            [100%]
  =============================== warnings summary ===============================
  .venv/lib/python3.12/site-packages/fastapi/testclient.py:1
    .../fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx`
    with `starlette.testclient` is deprecated; install `httpx2` instead.
      from starlette.testclient import TestClient as TestClient  # noqa

  .venv/lib/python3.12/site-packages/starlette/testclient.py:53
    .../starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal
    alias is deprecated, use anyio.from_thread.BlockingPortal instead.
      _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

  -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
  1536 passed, 5 skipped, 6 deselected, 2 warnings in 131.13s (0:02:11)
  ```

### Falsification transcript (edit → run → confirm fail → restore)

Removed the `try`/`except` wrapper, reverting the line to the bare
`ws_resolved = ws.resolve()` (marked `FALSIFICATION-K` in place).
`test_a_symlink_cycle_in_an_ancestor_of_the_workspace_is_a_provenance_error`
failed exactly as expected — not a clean assertion failure but the bare
exception itself escaping past `pytest.raises(provenance.ProvenanceError)`:

```
    with pytest.raises(provenance.ProvenanceError):
>       provenance.sync(ws)
...
    def check_eloop(e):
        ...
        if e.errno == ELOOP or winerror == _WINERROR_CANT_RESOLVE_FILENAME:
>           raise RuntimeError("Symlink loop from %r" % e.filename)
E           RuntimeError: Symlink loop from '.../loop_a/workspace/r1'
```

Restored via `Edit` and confirmed byte-identical to the pre-falsification
copy via `diff`. Re-ran the full provenance test file (54 passed) and the
full suite (1536 passed, 5 skipped, 6 deselected) after restoring.

### Concerns

None remaining from this round. The two items the coordinator explicitly
marked as not-for-fixing (`test_sync_emits_a_skipped_event_when_something_
is_skipped` passing against pre-fix code, and the `skipped["agents"]`/
`skipped["artifacts"]` sort-timing asymmetry) were left untouched.
