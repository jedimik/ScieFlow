# Ticket 22 — provenance read-cost implementation plan

> **Execution:** subagent-driven, using Astra6/Ultra coordination and independent review, with fresh Sol 6.1/High implementation contexts. Read the installed Superpowers subagent-driven-development skill. No model fallback. No commits, pushes, merges or uploads without separate permission.

**Status:** proposed implementation plan; user review required before production edits.

**Goal:** render the manuscript overview without repeating history traversal, and make tree subprocess count independent of the number of rounds, while keeping provenance's whole-string reference whitelist authoritative.

**Specification:** `.scratch/scieflow-remaining-scope/issues/22-provenance-read-cost.md`; existing overview design at `docs/superpowers/specs/2026-10-03-b-run-explorer-design.md`. The user authorized starting the next-ticket sequence on 2026-10-08. That does not imply approval of an unseen new design or authorize integration.

**Architecture:** retain `provenance.diff(ws, a, b)` as the validating API. Capture an internal snapshot of fully qualified relevant branch names, full commit object IDs and commit metadata. Enumerate each branch's tree by that captured object ID with one recursive, NUL-delimited listing, never by re-resolving its mutable name. Introduce a provenance-owned, request-scoped read context used by the manuscript panel. Memoize genuinely immutable point records keyed by repository identity and the captured ref names/tips. Recheck the ref fingerprint before reuse; changed/deleted refs invalidate it. Inside `diff`, validate the complete public point strings, then translate them to object-ID/path operands from that same checked snapshot. Return fresh lists/dictionaries to callers; expose no editable cache or caller-supplied whitelist. No persistent/global cross-request cache, cache file, schema, route, dependency or background service.

**Why this shape:** an `allowed=` parameter either introduces a new authority boundary or has to repeat the enumeration it is meant to avoid. A context owned by provenance lets `diff()` keep its existing whole-string membership check and derive its authority only from Git. A cheap ref fingerprint is acceptable; repeating tree traversal is not. The service selects the same points as before and never calls `_git`.

**Technology:** existing Python standard library, Git CLI, pytest; the verified local Python 3.12.3 environment. Python 3.14 compatibility is a separate unresolved follow-up.

## Invariants and scope

- Reads must never call `sync`, `ensure_repo`, or write Git objects/refs.
- Keep public point fields/order, existing legitimate comparisons and empty/no-Git/broken-history page behavior.
- Preserve the existing whole-string whitelist tests unchanged. Test option-shaped strings, unknown refs, revision expressions, aliases and branch/tag ambiguity against real Git.
- Cache lifetime is one panel read. Nested scopes, different workspaces, concurrent requests, exceptions and ref changes must not leak authority or stale data.
- Parse `ls-tree -r -t -z` as NUL-delimited records, not lines. Classify complete path components and Git object types, not substring matches.
- Count actual Git subprocesses after fixture setup. Do not use timing assertions as correctness tests.
- Keep LaTeX, sandbox policy, shared-cache protection, research staffing, draft-page synchronization and upload behavior untouched.

## Snapshot, transport and failure contracts

The initial independent review is retained at `workspace/cloud-dev-tickets-20261008/reviews/ticket22-plan-review.md`. These requirements resolve its R1/R2 design gaps; review is not evidence of an implemented fix.

- **One observed generation:** capture full `refs/heads/main` and `refs/heads/draft/...` names with object IDs. Read metadata/tree content from those IDs (metadata supplied by the same ref observation is also valid). Never stamp an enumeration with a later fingerprint. In `diff`, use the validated snapshot's object ID plus the exact authorized path; preserve public `a`/`b`, labels, order, `commit`/`at`, absence of `parent` for top-level points, and result/truncation fields. This is an observed ref table, not a promised transaction across concurrent writers. No retry-until-stable or per-point `rev-parse` workaround.
- **Identity and lifetime:** include normalized absolute repository location plus the observed directory device/inode in identity. Re-stat for a cache hit; disappearance/replacement invalidates the memo. Identical object IDs in different repositories do not share cache state. Use a `ContextVar` with a nonmutable empty default, immutable/replaced scope state, a fresh scope per entry and token reset in `finally`. Restoring an outer scope still requires current ref revalidation. Unscoped readers remain supported and retain nothing between calls.
- **Lossless listing:** use binary capture for recursive tree records through the same sealed-environment/timeout/error discipline as `_git`; leave unrelated text consumers unchanged. Split bytes on NUL and header/path on the first tab. Do not apply universal-newline conversion, strip pathname bytes or decode irrelevant descendant filenames. Convert relevant textual components deliberately; unrepresentable control metadata must become the established typed failure, never an uncaught decoding error or replacement-character authority.
- **Exact grammar:** only actual tree entries create merge/review/sections directory points; root outline must be an actual blob. Use complete ASCII components. Round suffixes follow the writer's canonical nonnegative decimal rule, `0` or a nonzero digit followed by ASCII digits: no leading zero, Unicode digit, sign, final newline or normalization. Ignore malformed/noncanonical round-like names rather than invent an alias. Retain legitimate empty-tree points. Nested names, gitlinks and blobs/symlinks named like directories do not manufacture points.
- **Failure:** no Git/missing repo keeps the current result. A failed initial ref read invalidates the memo and follows the existing empty-history result, without caching it as a successful empty snapshot. A valid empty ref table is distinguishable internally from that failure. A recursive listing failure after readable refs is a `ProvenanceError`, with no partial snapshot cached. A failed revalidation never reuses prior authority. If comparison fails after the panel obtained points, retain its round/section summary and degrade only the comparison note. No repair, writer lock or per-round fallback traversal.
- **Process budget:** use at most one ref enumeration and one recursive tree listing per relevant branch on a cold successful read; batch metadata in the ref observation or bound any extra metadata work by branch count. A unchanged-snapshot panel comparison permits one fingerprint recheck and one `git diff`, but no second `ls-tree` or metadata traversal. Pin both `ls-tree` and total Git-command counts for the fixed 2-/20-round fixtures; declared counts must reflect executed measurements, not the old static figures.

## Environment and evidence

All development evidence belongs under `workspace/cloud-dev-tickets-20261008/`: `logs/` for prompts/transcripts, `tools/` for helpers, `scratch/` for worktrees, fixtures, test output and benchmark data. Plans/specifications remain in the repository's established `docs/superpowers/` locations.

Use the bootstrap's verified Python 3.12 environment. Test fixtures must have repo-free ancestry via the already exercised bind-mount helper; a symlink is not equivalent. Reuse that helper's implementation in the development evidence area rather than weakening containment tests. Every check gets an immutable fresh output name and a real exit-code receipt. Do not overwrite failed evidence or install another Python version.

## Task 1 — bounded point enumeration

**Modify:** `src/scieflow/core/provenance.py`, `tests/core/test_provenance.py`.

1. Add a real-Git regression with equivalent four-agent histories at 2 and 20 rounds. Count `ls-tree` subprocesses, excluding fixture construction, and assert the count is bounded by branch count rather than rounds. Assert the expected ordered refs/content fields, not just the counter.
2. Run the new regression against unchanged production code. Save its failing assertion and exit status; a setup/import error is not RED evidence.
3. Replace per-round tree queries with an object-ID-bound, binary-captured recursive NUL-delimited listing per relevant branch. Retain existing validation and point vocabulary. Add real-Git coverage for tabs, LF/CR, quotes, Unicode, raw non-UTF-8 descendant names, final-newline/non-ASCII round digits, canonical zero, wrong object types and empty trees. Assert the complete point set and no invented points.
4. Run targeted provenance tests and record GREEN. Refactor only with the tests green.
5. Fresh independent spec review, then code/security review. Resolve findings with regression tests and repeat the relevant review before the next task. No commits.

## Task 2 — provenance-owned read reuse

**Modify:** `src/scieflow/core/provenance.py`, `src/scieflow/core/service.py`, `tests/core/test_provenance.py`, `tests/core/test_service.py`. Extend web regression tests only if required to pin the integration.

1. Add a manuscript-panel subprocess-counter regression that requires a real comparison result. It must detect a second history traversal, and fail for that reason before the implementation.
2. Add the internal immutable memo and public read-scope context manager according to the contracts above. Use lazy scope entry around the panel's normal `points()` / `diff()` path, preserving its early no-Git/no-repo checks; never wrap every overview panel in eager Git work. Keep the public three-argument `diff` signature, membership validation and object-ID-bound operands.
3. Add adversarial coverage in small RED/GREEN slices: mutate/clear/append to returned points (including an option-shaped ref and a real but non-vocabulary file path); reject an external whitelist; move refs during cache fill, between validation and diff, and A→B→A; add/delete refs; fail fingerprint/listing reads after success; replace a repo at the same path; nest/unwind scopes; run sequential and simultaneous requests; distinguish repositories with identical tips. Tags named `main` and `draft/<agent>` must point to different content from the branches as a meaningful collision control. Invalid inputs in either diff position must never reach `git diff`; valid comparisons must still run and report consistent metadata/content. Ref churn must not cause an unbounded retry.
4. Preserve all current security tests byte-for-byte. Rerun provenance, service, overview-web and `tests/web/test_provenance_panel.py` tests. Confirm all four no-history states, valid empty and broken initial repositories, failed revalidation/listing/comparison behavior, page escaping, public shapes and read-only counters.
5. Fresh independent spec review followed by code/security review; resolve findings before final verification.

## Task 3 — measurements and final verification

**Modify:** measured-cost discussion in `docs/superpowers/specs/2026-10-03-b-run-explorer-design.md`; development ledger/evidence. Routine documentation may be delegated to Sol after the coordinator has checked the measurements.

1. Save a reproducible synthetic 20-round / 4-agent / 6-section / 2000-event benchmark. Measure the base revision and changed revision on this host with the same fixture and method. Save raw samples, medians, Git-command counts, environment and source identity. Also report a 2-round comparison. The fixture is synthetic, not a real research result.
2. Measure `points`, a standalone `diff`, the manuscript panel, the overview band, and full `GET /runs/r1`. Never compare these new-host timings as if they were a controlled experiment against the old ~159ms figure. Keep historical context clearly dated and replace current claims with the new measured values.
3. Parent/coordinator independently runs the full offline suite using the fixture mount, plus `bash scripts/check_legacy.sh`, strict MkDocs build, and `git diff --check`. Inspect JUnit/receipts; optional NotebookLM skips and live/slow deselections remain explicit.
4. Whole-change independent review from the merged base `0d7df13e5567ffba1bfe989765346079aff7de47`. Inspect sandbox/LaTeX/provenance/cache positive controls in real test output. No claims of fresh verification based only on the historical 1675-test baseline.
5. Mark ticket 22 complete only when every checkbox in the source ticket has evidence. Keep changes uncommitted and identify their working tree. Update the backlog/ledger, publish a safe result through the exact project binding, and verify Discord read-back separately from Desktop persistence. Visible Windows receiver rendering is not yet verified.

## Approval boundaries and subsequent tickets

K / ticket 16 and F+G / ticket 11 are design work next, not implicitly approved implementations. Save each proposed design, surface unresolved user-facing choices, obtain explicit approval, then write/execute its implementation plan. Ticket 12 must not start from an unapproved ticket-11 design. No arbitrary shell surface is part of K; the previously chosen option C is explicitly human-marked selected-manager harness input with the charter retained.
