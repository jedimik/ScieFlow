# Task 6 report — Documentation

## What was documented, and where

- `docs/web.md` — new `### Manuscript history: the provenance repo` section,
  inserted at the end of `### The draft workbench` subsection (right after
  the "No one of these, on its own, stops a LaTeX preview…" paragraph, before
  the general "Every form on these pages…" coda that closes out `## Pages`).
  Covers, in order: the bare repo's location and why bare/plumbing-only
  (nothing for an agent to corrupt, no checkout dance); the `main` vs
  `draft/<agent>` layout as an on-disk block; the `curation.yml`
  most-recent-round-only caveat (see "wrong in the plan" below); why
  `merge_N`/`review_N` are two independent counters named apart; that branch
  names are discovered from the run's own artifacts, never
  `config/agents.yml`, and that unsafe names are skipped per-agent and
  reported, never failing the whole sync; the three diff commands, each
  labelled with what it answers; a full explanation of why the second diff
  needs `:sections` on *both* sides (the path-disjoint-diff failure mode);
  that the repo is derived/never authoritative and why that is exactly what
  makes a best-effort sync the right trade (never fails a merge round); that
  it's local-only (no push/fetch/remote, DVC still carries the bytes); and
  that `git` is an optional dependency with a plain-language degrade.
  Also touched up the Pages table's Draft-workbench row to mention the new
  panel and point at "below."
- `docs/runs.md` — added a short cross-reference paragraph at the end of
  `## History: the event log`, distinguishing the run's own event history
  from a `paper-draft`/`paper-review` run's separate manuscript history, and
  linking to `web.md#manuscript-history-the-provenance-repo`.
- `docs/cli.md` — **not touched**, confirmed deliberately. This plan adds no
  CLI command; the feature is a git repo plus two read-only web panels
  (`service.manuscript_history`, `service.manuscript_diff`, both consumed
  only from `drafts_page`'s `GET` route). `grep -n "provenance"
  docs/cli.md` returns nothing, and I verified no new `scieflow` subcommand
  exists anywhere touching `provenance.py` (only `src/scieflow/core/service.py`
  and `src/scieflow/web/pages.py` import it, per `grep -rn "provenance"
  src/scieflow/`).
- `AGENTS.md` — checked again (`grep -n -i "web\b|serve|drafts|workbench|git\b"
  AGENTS.md`); it has no inventory of web surfaces, only the two unrelated
  hits noted in the brief (line 84, "web-search auxiliaries"; line 98,
  "git (local edit → push → pull)" — chat-backup guidance, unrelated to
  this feature). Added nothing there, as instructed.

## Where the plan/brief summary was wrong about the code

1. **`curation.yml` does not appear under every `merge_N/` directory** — the
   plan's own ASCII layout block (in the spec and in the brief) implies one
   `curation.yml` per round. The actual code
   (`provenance.sync`, the `# manuscript/curation/document.yml -> main:
   merge_<n>/curation.yml, highest n present` block) only ever writes it
   under the *highest* round number present at the time a given sync runs,
   because there is exactly one live `document.yml` on disk, not one per
   round. I confirmed this against
   `tests/core/test_provenance.py::test_sync_lays_out_main_exactly_as_the_spec_says`,
   which asserts the full tree listing for a two-round fixture and shows
   only `merge_2/curation.yml`, no `merge_1/curation.yml`. I documented this
   explicitly as a caveat rather than reproducing the plan's diagram
   verbatim, including the subtlety that round 1's copy still exists in the
   *historical* commit made while round 1 was current — it's just not part
   of `main`'s current tip at that path anymore.
2. Everything else in the task's summary (bare repo, plumbing-only writes,
   `main`/`draft/<agent>` split, two independent round counters, artifact-
   discovered agent names, the three diff commands, the path-disjoint-diff
   failure mode for the second diff, derived/never-authoritative +
   best-effort sync, local-only, `git` optional) checked out exactly against
   `src/scieflow/core/provenance.py`, `src/scieflow/core/service.py` (lines
   540–629), `src/scieflow/web/pages.py` (lines 334–382) and
   `src/scieflow/web/templates/drafts.html` (lines 94–128), and against the
   tests in `tests/core/test_provenance.py` and
   `tests/web/test_provenance_panel.py`. I did not find any other statement
   in the brief or spec that the code contradicts.

## Commands run, with output

```
$ uv run --group docs mkdocs build --strict
INFO    -  Cleaning site directory
INFO    -  Building documentation to directory: /home/jedimik/Github/ScieFlow/site
INFO    -  Documentation built in 0.64 seconds
```
(Zero warnings. The only stderr text is Material for MkDocs's own unrelated
"MkDocs 2.0 will introduce backward-incompatible changes" advisory banner,
not a build warning.) Also verified the new anchor resolves:
`grep -o 'id="manuscript-history[^"]*"' site/web.html` →
`id="manuscript-history-the-provenance-repo"`, and
`grep -o 'href="web\.html#manuscript-history[^"]*"' site/runs.html` →
`href="web.html#manuscript-history-the-provenance-repo"`.

```
$ ./scripts/check_legacy.sh 2>&1 | grep -c "^ok"
25
```

```
$ uv run pytest -q
1577 passed, 5 skipped, 6 deselected, 2 warnings in 137.63s (0:02:17)
```
The 2 warnings are the pre-existing `StarletteDeprecationWarning`
(httpx/starlette.testclient) and `DeprecationWarning`
(`anyio.abc.BlockingPortal`) — unchanged from the stated baseline.

```
$ git add docs && git commit -m "docs: the manuscript's git history, and how to read it" ...
[feat/d-manuscript-provenance 07efbb6] docs: the manuscript's git history, and how to read it
 2 files changed, 143 insertions(+), 1 deletion(-)
```

## Concerns

None that block this task. One thing worth a maintainer's eye later (not a
doc defect, just a note): the `curation.yml`-under-highest-round-only
behavior means a person diffing `main:merge_1` against `main:merge_2` will
see `curation.yml` only appear as an addition on the `merge_2` side, never
as a modification from a `merge_1` copy — which is correct given how `sync`
projects the tree, and I described it that way, but it's easy for a future
reader of the code (not the docs) to assume otherwise from the spec's
diagram alone.

---

## Fix Round 1: Documentation Accuracy Corrections

Reviewer verdict: task quality needed fixes, with one Critical, one Important,
and one Minor.

### Review findings addressed

- **Critical:** `docs/web.md` claimed the workbench diff form prevents
  mismatched-depth comparisons by offering only compatible points. The actual
  template renders both selects from the full `provenance.points` list, so the
  UI can produce the same path-disjoint diff the prose warned about for hand-run
  git commands. The docs now state that the picker offers every point and tell
  readers to recognize a mismatched-depth comparison by the paired `deleted file`
  / `new file` shape with no line-level comparison.
- **Important:** the `draft/<agent>:review_N` material was attributed to
  `paper-review`. That branch content comes from `paper-draft`'s adversarial
  cross-review phase; only `main:review_N` comes from `paper-review`'s
  review-and-response cycle. The layout block and explanatory paragraph now
  distinguish those two cases.
- **Minor:** the layout block still gave the wrong first impression for
  `curation.yml` by saying it was "as it stood for that round" under
  `merge_1/`. The line now points to the caveat immediately below.

### Spec correction

Added dated correction notes to
`docs/superpowers/specs/2026-09-27-manuscript-provenance-design.md` rather than
rewriting the original design text. The notes cover both known design-record
errors:

- `review_N/` is branch-specific in the implementation: `main` comes from
  `paper-review`; `draft/<agent>` comes from `paper-draft` cross-review.
- `curation.yml` is written under whichever `merge_N/` is highest at sync time,
  not under every merge round at the current tip.

### Verification

```
$ uv run --group docs mkdocs build --strict
INFO    -  Cleaning site directory
INFO    -  Building documentation to directory: /home/jedimik/Github/ScieFlow/site
INFO    -  Documentation built in 0.77 seconds
```

```
$ ./scripts/check_legacy.sh
25/25 ok
```

```
$ uv run pytest -q
1577 passed, 5 skipped, 6 deselected, 2 warnings in 141.89s (0:02:21)
```

The two warnings are the pre-existing `StarletteDeprecationWarning` and
`DeprecationWarning` from the test client stack.

## Fix Round 1 Concerns

None.
