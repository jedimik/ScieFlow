# Task 4 report: the service layer and the guarded sync after a merge round

## What was implemented

`src/scieflow/core/service.py`:

- Imported `provenance` alongside the other `scieflow.core` imports (kept
  alphabetical in the existing `from scieflow.core import (...)` block), so
  `service.provenance.sync` is reachable for the merge-round test's
  monkeypatch, as the brief requires.
- `manuscript_history(project, slug) -> dict`: returns
  `{"available": bool, "points": [...], "reason": str}`. Validates `slug`
  via `_ws` (raises `ServiceError` for an unknown/unsafe run, per the brief).
  If `provenance.available()` is `False`, returns `available=False` with a
  reason mentioning "git". Otherwise syncs (`provenance.sync(ws)`) then reads
  `provenance.points(ws)`; a `provenance.ProvenanceError` from either call
  degrades to `{"available": True, "points": [], "reason": f"no history
  yet: {exc}"}` rather than raising. An empty points list (sync succeeded,
  nothing to show yet) gets the "no history yet — it appears once an agent
  has drafted or a merge round has completed" reason; a non-empty list gets
  `reason=""`.
- `manuscript_diff(project, slug, a, b) -> dict`: validates `slug` via
  `_ws`, then delegates to `provenance.diff(ws, a, b)`, translating
  `provenance.ProvenanceError` to `ServiceError(str(exc))`. Its docstring
  records the memory caveat the brief flagged: `DIFF_LIMIT` bounds the
  returned string, not the memory used getting there, since `provenance._git`
  buffers all of git's stdout via `capture_output=True` before any slicing
  happens, bounded only by a 30s subprocess timeout — this function is now
  the boundary Task 5 exposes over HTTP, so the gap is recorded here rather
  than silently inherited.
- `merge_round`: after `curation.advance_round(ws)` in the success branch,
  wrapped `provenance.sync(ws)` in `try/except provenance.ProvenanceError`,
  emitting a `"provenance.skipped"` event (actor `"system"`, `why=str(exc)`)
  on failure and otherwise proceeding to return the round unchanged. The
  `except` is kept to `provenance.ProvenanceError` specifically, not a bare
  `Exception`, per the brief's explicit instruction (and confirmed correct —
  Task 2/3 made `ProvenanceError` genuinely the only exception type
  `provenance` functions raise, so this is not under-catching).

`tests/core/test_service.py` (appended, verbatim from the brief): the six
`manuscript_history`/`manuscript_diff` tests.

`tests/core/test_merge_round.py` (appended, with one deliberate deviation
from the brief's literal code — see "Ambiguity resolved" below): the two
merge-round provenance-sync tests.

## Ambiguity resolved: the two new merge-round tests needed `conversation.set_agent`

The brief's given code for `test_a_successful_round_syncs_the_provenance_repo`
and `test_a_failing_provenance_sync_does_not_fail_the_round` uses `curated`
and `responder` as fixtures but never calls
`conversation.set_agent(curated, responder)`. I read both fixtures first, as
the brief instructed:

- `curated` sets the run's conversational agent to `"stub"`, which (per the
  existing `test_a_turn_that_runs_but_does_not_succeed_does_not_advance_the_round`
  test in the same file) always fails against a real merge prompt, because
  `stub` only answers when the prompt carries a literal `kind: conversation`
  line.
- `responder` only *registers* a new agent config named `"responder"` in the
  project's `agents.yml` and returns its name — it does not make the run use
  it. The existing `test_the_round_advances_once_the_turn_succeeds` test
  (the brief's own reference for reaching the success path) explicitly calls
  `conversation.set_agent(curated, responder)` before dispatching.

Both new tests assert outcomes that only occur on a **successful** round
(`provenance.repo_path(ws).is_dir()` for the first — the guarded sync only
runs inside the `MERGE_SUCCESS_STATE` branch; `result["round"] == 2` and
`result["turn"]["job"]["state"] == service.MERGE_SUCCESS_STATE` for the
second). Without switching the run to `responder`, the dispatched turn would
run against `stub`, fail, and neither assertion could pass — confirmed by
running the tests as literally given in the brief first (see below): both
failed, but not for the reason the brief anticipates ("no attribute
`manuscript_history`"/`provenance`) — the sync test failed on the
`is_dir()` assertion and the guard test failed with `AttributeError:
module 'scieflow.core.service' has no attribute 'provenance'` (expected,
since `provenance` wasn't imported yet), which didn't actually distinguish
the two hypotheses at that point. Since the brief said "read them before
writing" and calls out these two fixtures specifically, I resolved this by
adding `conversation.set_agent(curated, responder)` as the first line of
each test's body, matching the established pattern — this is what makes
both tests reach the branch they are meant to exercise. I documented the
reasoning inline in each test's docstring rather than silently deviating
from the brief's literal code.

Everything else in the brief (the `skipped`-is-a-dict note, `points()`'s
`parent` key passed through unchanged since neither test nor implementation
touches it, `main` always existing, the single-exception-type contract, the
`DIFF_LIMIT`/memory caveat) matched the codebase exactly as described —
no other deviations were needed.

## Test results

Step 2 (before implementation) — confirmed the 8 new tests fail for the
right reason:

```
uv run pytest tests/core/test_service.py tests/core/test_merge_round.py -k "manuscript or provenance" -v
```
Result: `8 failed, 7 passed` — all 6 `test_service.py` failures were
`AttributeError: module 'scieflow.core.service' has no attribute
'manuscript_history'`/`'manuscript_diff'`; the 2 `test_merge_round.py`
failures were the `is_dir()` assertion failure and
`AttributeError: ... has no attribute 'provenance'` described above.

Step 4 (after implementation):

```
uv run pytest tests/core/test_service.py tests/core/test_merge_round.py -k "manuscript or provenance" -v
```
→ `15 passed` (the 7 pre-existing + all 8 new).

```
uv run pytest tests/core -q
```
→ `658 passed, 3 deselected`.

```
uv run pytest -q
```
→ `1568 passed, 5 skipped, 6 deselected, 2 warnings` (up from the stated
baseline of 1560 passed, 5 skipped — the +8 is exactly the new tests; the
same 2 pre-existing starlette/anyio deprecation warnings, no new ones).

## Falsification transcript (the load-bearing one)

Per the brief and the general falsification requirement, I removed the
`try`/`except` around the guarded `provenance.sync(ws)` call in
`merge_round` (in place, not via a worktree, since scieflow is
editable-installed into the shared venv) and re-ran the guard test:

```
uv run pytest tests/core/test_merge_round.py -k "test_a_failing_provenance_sync_does_not_fail_the_round" -v
```

Result: **FAILED**, with:

```
src/scieflow/core/service.py:561: in merge_round
    provenance.sync(ws)
...
    def boom(ws):
>       raise provenance.ProvenanceError("git exploded")
E       scieflow.core.provenance.ProvenanceError: git exploded
```

— exactly the failure mode the brief specifies: the raised
`ProvenanceError` propagates out of `merge_round` instead of the round
being returned. I then restored the `try`/`except`/`events.emit` guard and
re-ran `tests/core -q` (658 passed) and the full suite (1568 passed, 5
skipped, 6 deselected, 2 warnings) to confirm the restore was correct.

## Self-review

- Checked that `_ws(project, slug)` is called before any `provenance.*`
  call in both new functions, so a malformed `slug` still raises
  `ServiceError` before touching git — matches the "only a genuinely
  malformed slug raises" constraint.
- Checked `events.TYPES` and `events.ACTORS` in `src/scieflow/core/events.py`
  already contain `"provenance.skipped"` and `"system"` respectively (added
  in Task 2/3), so the `events.emit(ws, "provenance.skipped", "system",
  why=str(exc))` call in the guard doesn't raise `ValueError` on an unknown
  type/actor.
- Verified `except provenance.ProvenanceError` is the only exception clause
  added anywhere in this task — no bare `except Exception` — matching the
  brief's explicit warning about swallowing a programming error.
- Verified `manuscript_history`/`manuscript_diff` don't touch `sync()`'s
  `skipped` dict or `points()`'s `parent` key at all — both are passed
  through unchanged, as the brief specifies for this task (Task 5's job).
- Re-read the final diff of `service.py` after the falsify/restore cycle to
  confirm the restored code is byte-identical in structure to what passed
  originally (no leftover artifact from the edit-back).
- No route was added; `manuscript_history`/`manuscript_diff` are the two
  new service-layer functions and nothing else was touched.

## Concerns

None. The one pre-flagged gap (the `DIFF_LIMIT`-is-response-only-not-memory
caveat) is recorded in `manuscript_diff`'s docstring as instructed, not
solved — that's explicitly out of this task's scope per the brief.

---

## Fix round 1 of 5

Two items came back from review, both text-only (no logic change), plus a
required re-run of the full suite.

### 1. `manuscript_history` docstring claimed an unreachable degradation path

The docstring said a sync failure "(a corrupt repo `ensure_repo` couldn't
rebuild, a symlink cycle in an ancestor of the workspace) degrades to the
same 'no history yet' shape rather than raising." The reviewer built
Task 2's regression scenario directly against `manuscript_history` and found
it raises `ServiceError("no run workspace/r1")` from `_ws`'s own
`ws.is_dir()` check — before `manuscript_history`'s body or its `except
provenance.ProvenanceError` clause ever run. This is a filesystem
invariant, not a construction artifact: a cyclic symlink in any ancestor of
`ws` means the OS can never resolve a path beneath it, so `ws.is_dir()` can
never return `True` through such a cycle — `_ws` always intercepts first.
The coordinator identified this as their own framing error from the review
dispatch, not something I introduced independently, but it was still my
docstring text to fix.

Fix: dropped the ancestor-symlink-cycle example from the sync-failure
parenthetical (kept the reachable corrupt-repo case and added "a git
invocation that fails partway through `sync`"), and added a sentence to the
second paragraph stating plainly that `_ws` intercepts the ancestor-cycle
case upstream of this function's body and surfaces it as
`ServiceError("no run workspace/...")`, never as the "no history yet"
degradation. No logic changed; this is a doc-only edit and needed no
falsification, per the coordinator's note.

### 2. `test_manuscript_diff_translates_a_provenance_error` didn't earn its place

As written (per the brief, verbatim), this test called `manuscript_diff`
with no prior sync, so `points()` returned `[]` and it took the *same*
"not a point" whitelist-refusal path as
`test_manuscript_diff_refuses_a_ref_that_is_not_a_point` immediately above
it — just via an empty whitelist instead of a populated one refusing an
unlisted ref — and asserted only `pytest.raises(service.ServiceError)` with
no `match=`, so it couldn't even distinguish that from a different failure.

**Chosen fix: replaced it with a case that drives a genuinely different
`ProvenanceError` origin**, rather than just adding `match=` to the existing
whitelist-refusal route. Reasoning: the whitelist-refusal path already has
a dedicated, well-specified test right above it; a second test hitting the
identical code path (translation-after-whitelist-refusal) via a different
setup would still leave the "translation is unconditional on where inside
`provenance.diff` the error originates" property unverified — the more
interesting failure mode for a boundary function that is really just a
pass-through-plus-translate. So instead the new test monkeypatches
`service.provenance.diff` directly to raise `ProvenanceError("git diff
failed: fatal: bad revision")`, unconditionally on its arguments, and
asserts `pytest.raises(service.ServiceError, match="git diff failed")`.
This proves `manuscript_diff` translates *any* `ProvenanceError`
`provenance.diff` raises, not merely the whitelist-refusal shape, and gives
the test a `match=` tying the raised message through to the translated one.

Falsified in place: removed the `except provenance.ProvenanceError` clause
from `manuscript_diff` (temporarily, leaving a bare `return
provenance.diff(ws, a, b)`), reran the single test, and confirmed it failed
with:

```
src/scieflow/core/service.py:625: in manuscript_diff
    return provenance.diff(ws, a, b)
...
E       scieflow.core.provenance.ProvenanceError: git diff failed: fatal: bad revision
tests/core/test_service.py:754: ProvenanceError
```

— the raised `ProvenanceError` propagating instead of being translated to
`ServiceError`, exactly the failure mode the test exists to catch. Restored
the `try`/`except` afterward.

### Re-run results

```
uv run pytest tests/core -v
```
→ `658 passed, 3 deselected` (tail):
```
tests/core/test_service.py::test_manuscript_history_reports_no_history_for_a_fresh_run PASSED
tests/core/test_service.py::test_manuscript_history_lists_points_after_a_round PASSED
tests/core/test_service.py::test_manuscript_history_degrades_when_git_is_missing PASSED
tests/core/test_service.py::test_manuscript_diff_refuses_a_ref_that_is_not_a_point PASSED
tests/core/test_service.py::test_manuscript_diff_translates_a_provenance_error PASSED
tests/core/test_service.py::test_manuscript_history_refuses_an_unknown_run PASSED
...
====================== 658 passed, 3 deselected in 28.74s ======================
```

```
uv run pytest -q
```
→ tail:
```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1568 passed, 5 skipped, 6 deselected, 2 warnings in 133.86s (0:02:13)
```

Same counts as before this fix round (1568 passed, 5 skipped, 6 deselected,
2 warnings) — a doc correction and a test replacement, no behavioural
change, so this is exactly the expected outcome.

Committed as a follow-up commit on top of `1f2adf9`.
