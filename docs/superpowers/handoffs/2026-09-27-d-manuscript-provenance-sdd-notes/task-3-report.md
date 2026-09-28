# Task 3 report: reading — points and diffs

Commit: `fc295e2` on `feat/d-manuscript-provenance`
("feat(core): list provenance points and diff between them, refs whitelisted")

## What was implemented

`src/scieflow/core/provenance.py`:

- `DIFF_LIMIT = 200_000`.
- `points(ws) -> list[dict]` — a pure reader. Returns `[]` immediately if
  `repo_path(ws)` doesn't exist (never calls `ensure_repo`/`sync`). Otherwise:
  - `_main_points(repo)`: reads `refs/heads/main`'s tip (sha + committer date,
    once, via `_branch_tip`) and its top-level tree (`ls-tree --name-only`).
    Emits one point per `merge_<n>` directory (newest first), **plus** a
    finer `main:merge_<n>/sections` point when that round has a `sections`
    subdirectory, one point per `review_<n>` directory (newest first), and
    one for `outline.md` if present (placed last — it predates every round).
  - `_draft_points(repo, agent)`: same idea for `refs/heads/draft/<agent>` —
    a `sections` point (current draft) first, then `review_<n>` points
    newest-first.
  - `_draft_branches(repo)`: which agents to iterate, discovered via
    `git for-each-ref refs/heads/draft/*` — **not** `agents(ws)`. See
    "decision" below.
  - Ordering: `main`'s points first, then draft branches (agents sorted by
    name), newest-round-first within each branch.
- `_point_treeish(point)`: `"<branch>:<path>"` → `"refs/heads/<branch>:<path>"`.
- `diff(ws, a, b) -> dict`: builds `allowed = {p["ref"] for p in points(ws)}`
  and raises `ProvenanceError(f"not a point: {a!r}")` / same for `b` on
  whole-string mismatch, **before** the `git diff` subprocess runs. Then
  `_git(repo, "diff", "--no-color", _point_treeish(a), _point_treeish(b),
  errors="replace")`, truncated to `DIFF_LIMIT` chars with a stated
  truncation line, `truncated` flag set accordingly.
- `_git` gained one new parameter: `errors: str = "strict"`, passed straight
  through to `subprocess.run`'s own `errors=` (valid for `text=True` mode).
  Every existing caller keeps the default; `diff` alone passes `"replace"`.
  This is not a second subprocess path — same sealed `_git`, same `_env()`,
  one new parameter.

`tests/core/test_provenance.py`: 54 pre-existing collected test items →
73 after this task (net +19: 12 new `def test_...` functions, one of
which — the hostile-ref check — parametrizes to 7 cases: 12 + 7 = 19).
See "Test results" below for how this was verified.

## Decisions and why

1. **`main:merge_<n>/sections` as an extra point, beyond the brief's literal
   "one point per `merge_<n>` directory."** The brief's own
   `test_a_draft_against_a_round_diff_works` diffs
   `"draft/claude:sections"` against `"main:merge_1/sections"` — a path one
   level *inside* the `merge_1` point, never itself listed as a top-level
   `ls-tree` entry of `main`. Under the coordinator's whole-string-equality
   ruling, `"main:merge_1/sections"` can only be a valid `b` if `points()`
   actually emits it. Emitting a finer `merge_<n>/sections` sub-point
   alongside the round-level `merge_<n>` point (only when that subdirectory
   exists) satisfies the brief's literal instruction ("a point per
   `merge_<n>` directory" is still true) *and* makes the round's sections
   comparable, depth-for-depth, against a draft's `sections` — which is the
   only way that test's diff produces a meaningful (not garbled) comparison.
   I did not change the test; I concluded the point vocabulary needed one
   more member, and `test_points_lists_what_the_run_has` only checks
   membership (`"x" in refs`), never a closed set, so this doesn't conflict
   with it.

2. **`_draft_branches` reads `for-each-ref`, not `agents(ws)`.** The brief
   says "`points(ws)` reads the repo, not the workspace, so it reflects what
   is actually committed." Discovering *which* draft branches to report by
   re-scanning the workspace (`agents(ws)`) would violate that the moment a
   workspace's artifacts drift after a sync (an agent's draft directory
   removed, say) — the branch is still committed and should still be listed.
   I added `test_points_reflects_the_repo_even_if_the_workspace_drifts` to
   pin this and falsified it (see below).

3. **`errors="replace"` implemented as a new parameter on the sealed `_git`**,
   not a second `subprocess.run` call. This keeps "`_git` is the only
   subprocess path for repo operations" literally true — one function, one
   environment builder, one new optional argument that every other caller
   ignores by leaving it at the strict default.

## The two corrections, applied

1. **Binary test replaced.** `test_a_binary_artifact_diffs_without_dumping_bytes`
   is gone. In its place, `test_a_binary_file_in_a_round_is_never_committed_or_reachable_by_diff`
   writes `figure.pdf` alongside a legitimate `extra.tex` in `rounds/2/`,
   syncs, and asserts:
   - `extra.tex` is present (`merge_2/sections/extra.tex` in the `ls-tree`
     listing);
   - `figure.pdf` is absent by name;
   - **the exact file set** under `refs/heads/main:merge_2/sections` is
     `{"results.tex", "extra.tex"}` — not just an absence-of-substring check
     (see the falsification note below on why this stronger form was
     necessary);
   - a diff across that round has no NUL byte and no reference to
     `figure.pdf`.

2. **Rebuild-then-points assertion added**, as its own test:
   `test_points_are_stable_after_the_repo_is_rebuilt` syncs a populated run,
   captures `{p["ref"] for p in points(...)}`, `shutil.rmtree`s the repo,
   syncs again, and asserts the ref set is unchanged.

## Beyond the brief (added on my own initiative)

- `test_a_run_whose_repo_was_never_synced_has_no_points` — pins that
  `points()` never calls `ensure_repo`/creates a repo as a side effect of
  being asked "what's here."
- `test_points_reflects_the_repo_even_if_the_workspace_drifts` — pins
  decision #2 above. Removes *every* workspace trace of "claude" (drafts
  dir, `findings/claude.json`, `gaps/claude.json`, both review artifacts
  naming claude as reviewer) so `agents(drafted) == ["codex"]`, then asserts
  `points()` still reports `draft/claude:*`.
- `test_a_non_utf8_change_does_not_crash_the_diff` — exercises the
  `errors="replace"` path directly: a round's `.tex` file changes from
  ASCII to latin-1-encoded content between rounds; asserts the diff
  succeeds and contains a replacement character rather than raising.

## Test results

```
uv run pytest tests/core/test_provenance.py -k "points or diff or hostile or truncated or binary" -v
```
Before implementation: 10 failed (`AttributeError: module 'scieflow.core.provenance' has no attribute 'points'/'diff'/'DIFF_LIMIT'`).
After implementation: 10 passed.

```
uv run pytest tests/core/test_provenance.py -v
```
**73 passed in 4.31s** (final run, after all falsification restores) — up
from 54 pre-existing collected items (verified by collecting the pre-Task-3
snapshot of this file directly). 13 new `def test_...` functions were
added, one of which (`test_a_ref_that_is_not_a_listed_point_is_refused`)
parametrizes to 7 cases, giving 12 + 7 = 19 new collected items: 54 + 19 =
73, matching exactly.

```
uv run pytest -q
```
**1555 passed, 5 skipped, 6 deselected, 2 warnings in ~131s** — up from the
stated baseline of 1536 passed / 5 skipped by exactly 19 (the net new tests
in this file), same skip count, same 2 pre-existing starlette/anyio
deprecation warnings, same `-m 'not slow and not live'` deselection count.
Full suite green.

## Falsification transcripts (edit → run → confirm fail → restore)

1. **Whitelist check removed** (`diff()`'s two `if ... not in allowed: raise`
   lines deleted). Ran
   `pytest -k "not_a_listed_point or hostile_ref"` (8 tests): **7 of 7**
   `test_a_ref_that_is_not_a_listed_point_is_refused[...]` parametrizations
   failed — 6 with `AssertionError: Regex pattern did not match` (the raw
   git error surfaced instead of `"not a point"`), the empty-string case with
   a git `fatal: invalid object name 'refs/heads/'.` wrapped as
   `ProvenanceError` but not matching. `test_no_file_is_created_by_a_hostile_ref`
   still passed *incidentally* — `_point_treeish` prefixes `refs/heads/`
   onto the branch component before the string ever reaches git, so
   `--output=/tmp/pwned` becomes `refs/heads/--output=/tmp/pwned:`, which
   git rejects as an invalid object name rather than treating as a flag.
   That's a second, structural layer of defense, but it does **not**
   substitute for the whitelist — the `match="not a point"` assertions
   caught the missing gate every time. Restored; full file re-passed.

2. **Truncation logic removed** (`truncated = True` / slicing branch
   deleted, `truncated` left `False` unconditionally). Ran
   `test_a_large_diff_is_truncated_and_says_so`: failed with
   `assert False is True`. Restored; re-passed.

3. **`errors="replace"` reverted to strict** (removed the kwarg from
   `diff`'s `_git` call). Ran `test_a_non_utf8_change_does_not_crash_the_diff`:
   failed with an uncaught `UnicodeDecodeError` propagating out of
   `subprocess.run`/`_translate_newlines` — confirmed this is a real crash
   risk, not decorative. Restored; re-passed.

4. **Round `.tex` glob widened to `*`** (simulating a regression that
   iterates every file in a round directory, not just `.tex`). Ran
   `test_a_binary_file_in_a_round_is_never_committed_or_reachable_by_diff`:
   **initially still passed** — because `sync`'s target-path construction
   hardcodes the `.tex` suffix (`f"merge_{n}/sections/{tex.stem}.tex"`)
   regardless of the source's real extension, so the PDF's bytes would
   still get committed, just renamed to `figure.tex`, and my first draft of
   this test only checked for the literal string `"figure.pdf"` — which
   never appears under that regression either. I strengthened the test to
   assert the **exact** file set under `merge_2/sections` via a second,
   path-scoped `ls-tree`, re-ran the same probe, and got the expected
   failure (`AssertionError: ... Extra items in the left set: 'figure.tex'`).
   Restored the glob; re-passed. This was the one place falsification
   caught a genuine gap in my own test, not just confirmed an already-solid
   assertion.

5. **`_draft_branches(repo)` swapped for `agents(ws)`** (simulating the
   workspace-scan design the brief's wording explicitly warns against).
   First attempt at `test_points_reflects_the_repo_even_if_the_workspace_drifts`
   only deleted `manuscript/drafts/claude/` and **still passed** under the
   probe — `agents(ws)` re-derives "claude" from `findings/claude.json`,
   `gaps/claude.json`, and the two review artifacts naming claude as
   reviewer, all still present in the fixture. I extended the test to strip
   every one of those traces and assert `agents(drafted) == ["codex"]` as a
   precondition; re-ran the probe and got the expected failure (`draft/claude:*`
   missing from `after`). Restored; re-passed.

## Self-review findings and what changed as a result

- Falsification #4 and #5 both exposed the same class of issue: my first
  draft of a new test asserted something narrower than the property I
  actually wanted, and passed even under the regression it was meant to
  catch. Both were caught by the falsification step itself (not by a
  separate reviewer) and strengthened before commit — the exact-file-set
  check in #4, and the full-workspace-trace removal + precondition assert
  in #5.
- Confirmed `diff()` never introduces a second exception type: every path
  through `points()` → `_git`/`_ref_sha` either already returns `None`/`[]`
  on failure or raises `ProvenanceError`; `diff` itself raises only
  `ProvenanceError`.
- Confirmed no second `subprocess.run` was added; `ref_safe`'s existing
  `check-ref-format` call remains the only sanctioned exception, unchanged.

## Concerns

- None outstanding. The one genuine design call worth a second look in
  review is decision #1 (the extra `main:merge_<n>/sections` point) —
  it's necessary to make the brief's own draft-vs-round test pass under
  the whole-string-equality ruling, but it is an addition beyond the
  brief's literal "one point per `merge_<n>` directory" wording, so it's
  worth confirming this reading of the intent is the one you want.

---

# Fix round 1

Commit: `6dbf267` on `feat/d-manuscript-provenance`

The coordinator's review confirmed spec compliance and the security core
(21 hostile values, two independent layers, zero reaching `git` argv, zero
filesystem effect) and confirmed the `main:merge_<n>/sections` addition was
*necessary*, not merely consistent, with a concrete coarse-diff transcript.
Three Important findings and three Minors were raised; all six are
addressed below. Four further minors were explicitly deferred by the
coordinator (one `ls-tree` per merge round; `DIFF_LIMIT` bounding response
size rather than peak memory, carried to Task 5; the misaligned
continuation line; and finding 9, which is Task 2 behaviour the coordinator
is deliberately leaving as-is) — none of those required action here.

## Important 1 — the draft-vs-round test couldn't tell a real diff from a garbled one

Confirmed via the reviewer's own mutation-testing approach. Strengthened
`test_a_draft_against_a_round_diff_works` to assert hunk shape, not just
substring presence:

```python
assert "-claude's results" in result["text"]
assert "+merged v1" in result["text"]
assert "deleted file" not in result["text"], (
    "the two sides were compared at different depths")
assert "new file" not in result["text"], (
    "the two sides were compared at different depths")
assert result["text"].count("diff --git") == 1
```

No implementation change was needed — `_point_treeish` already resolves
`main:merge_1/sections` correctly; only the test was too weak to prove it.

**Falsification (the reviewer's exact mutation):** added
`path = path.removesuffix("/sections")` to `_point_treeish`, resolving the
finer point coarsely — exactly the bug the sub-point exists to prevent.
Re-ran the strengthened test: failed on the `"deleted file" not in
result["text"]` assertion, with the actual coarse `deleted file mode
100644` / `+++ /dev/null` diff shown in the failure output — the same
shape the reviewer's own transcript identified. Restored `_point_treeish`;
re-ran, passed.

## Important 2 — no test pinned points() ordering or a closed set

Added `test_points_are_a_closed_set_in_a_stated_order`, asserting the full
ordered `ref` list for the `drafted` fixture in one equality check (derived
by running `points()` against the fixture directly and confirmed against
`_sync_lays_out_*`'s known tree contents):

```
main:merge_2, main:merge_2/sections, main:merge_1, main:merge_1/sections,
main:review_1, main:outline.md,
draft/claude:sections, draft/claude:review_1,
draft/codex:sections, draft/codex:review_1
```

**Falsification:** reordered `points()` to run the draft-branch loop before
`_main_points`. Confirmed `test_points_are_a_closed_set_in_a_stated_order`
failed (list reversed at index 0), and — as the coordinator predicted —
confirmed `test_points_lists_what_the_run_has` (the pre-existing
membership-only test) still *passed* under the same mutation, demonstrating
the exact gap the new test closes. Restored the loop order; re-ran, both
passed.

## Important 3 — added a `parent` field to sub-points

`_main_points`'s `main:merge_<n>/sections` entry now carries
`"parent": f"main:merge_{n}"`; every other point has no `parent` key.
`diff`'s whitelist is untouched — still a flat `{p["ref"] for p in
points(ws)}` — per the coordinator's explicit instruction. Added
`test_a_sub_point_names_its_parent_point`, asserting the sub-point's
`parent` and that every other point's `.get("parent")` is `None`.

**Falsification:** dropped the `"parent"` key from the sub-point dict.
Confirmed the new test failed with `KeyError: 'parent'`. Restored; re-ran,
passed.

## Minor 1 — `_git` now pins `encoding="utf-8"` alongside `errors=`

`errors=` only governs what happens to an already-undecodable byte;
`encoding=` decides which codec is tried first, and was previously left to
`subprocess.run`'s locale-dependent default. Added `encoding="utf-8"` to
the one `subprocess.run` call inside `_git`. Added
`test_git_always_pins_utf8_decoding`, spying on `subprocess.run` and
asserting every `_git`-routed call carries `encoding="utf-8"`.

**Falsification:** removed `encoding="utf-8"` from the call. Confirmed the
new test failed (`assert False` — `None != "utf-8"` for the observed
calls). Restored; re-ran, passed.

## Minor 2 — truncation now cuts on a line boundary

`diff`'s truncation previously cut at exactly character `DIFF_LIMIT`,
wherever that fell inside a line. Now: `cut = text.rfind("\n", 0,
DIFF_LIMIT)`, falling back to `DIFF_LIMIT` only if the entire prefix is one
line with no newline at all (the pathological single-giant-line case,
already covered by the pre-existing `test_a_large_diff_is_truncated_and_
says_so`). Added `test_a_large_diff_truncates_on_a_line_boundary`: 20,000
lines of 21 bytes each (21 does not evenly divide 200,000), asserting the
last surviving content line matches the full `line-<7 digits>-fill` pattern
via `re.fullmatch` rather than being a fragment.

**Falsification:** reverted the cut to plain `cut = DIFF_LIMIT`. Confirmed
the new test failed with the last line reading `'+li'` — a literal
mid-line fragment. Restored the newline-seeking cut; re-ran, passed.

## Minor 3 — `_draft_branches` now uses `%(refname:lstrip=3)`

Replaced `%(refname:short)` + `removeprefix("draft/")` with
`%(refname:lstrip=3)`, which strips exactly the three leading path
components (`refs`, `heads`, `draft`) from the ref's real name regardless
of what else exists in the repo. Verified the ambiguity directly against
this host's git 2.43.0 before writing the test: with a tag `draft/claude`
alongside the branch `refs/heads/draft/claude`,
`%(refname:short)` reports the branch as `heads/draft/claude` (not
`draft/claude`), while `%(refname:lstrip=3)` correctly reports `claude`
regardless. Added `test_draft_branch_discovery_is_not_confused_by_a_same_named_tag`,
creating exactly that tag and asserting `draft/claude:sections` still
appears in `points()`.

**Falsification:** reverted `_draft_branches` to the old
`%(refname:short)` + `removeprefix` form. Confirmed the new test failed
(`draft/claude:sections` missing from `refs`). Restored `lstrip=3`; re-ran,
passed.

## Report cleanup

Fixed the garbled test-count sentence in "What was implemented" (previously
read "71→73... actually 73 total... up from 61 before"). It now reads: 54
pre-existing collected items → 73 after Task 3 (12 new `def test_...`
functions, one parametrized to 7 cases: 12 + 7 = 19 net new items),
matching the exact number independently re-verified by collecting the
pre-Task-3 snapshot of the test file directly.

## Test results (fix round 1)

```
uv run pytest tests/core/test_provenance.py -v
```
**78 passed in 4.83s** — up from 73 (net +5: the four new fix-round-1 tests
plus the Minor 3 regression test).

```
uv run pytest -q
```
**1560 passed, 5 skipped, 6 deselected, 2 warnings in 133.39s** — up from
1555 by exactly +5, same skip count, same 2 pre-existing
starlette/anyio warnings.

## Self-review findings (fix round 1)

- Every one of the six falsifications reproduced the exact failure mode
  the coordinator's review described (the Important 1 mutation reproduced
  the coordinator's own transcript almost verbatim: `deleted file mode
  100644` / `+++ /dev/null`), which is a stronger confirmation than a
  falsification that merely "fails somehow."
- No implementation change was needed for Important 1 — the bug the
  coordinator described was in test coverage, not in `_point_treeish`
  itself, which was already correct. Confirmed this explicitly before
  writing the falsification, so as not to "fix" code that wasn't broken.

## Concerns

None outstanding.
