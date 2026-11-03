# PL plan: land the paper-lifecycle framework work (workflows map, run adopt, workflow-aware phases) (round 1)

Milestone `pl` (size M), queue entry `continuity/milestones.yml` (`after: []`, `m-g2` waits on it).
Planner: Opus 5.5 (card t_54e51c7f, pipeline pl-plan, round 1). Plan only: nothing was committed, pushed or
launched. All code claims were checked read-only against current remote main
**M = `8b374803c2eb6cad0df981d466ba097b973f3c84`** (`git ls-remote` on 2026-10-31, "Merge pull request #24
from jedimik/dev/m-c2"), with `git show M:<path>` and a `git archive M` export.

## 0. Inputs and references used

| Input | Path (under RUN) | sha256 |
|---|---|---|
| Authority | `approvals/pl-landing-human-20261031.md` (human, 2026-10-31: "Land it for me … then M-G2 runs") | — |
| Frozen README | `handoffs/paper-lifecycle-20261031/README.md` | `162643e29381f501078102e152f8523b444bed03ca65266a6fccb5e6952def9a` |
| Frozen diff A ("astra worktree") | `handoffs/paper-lifecycle-20261031/framework-astra-worktree.diff` | `7b734ca169060a3d4a36c473055d16fa6ee824646b11d8a97f4d0332a33697dc` |
| Frozen diff B ("main checkout") | `handoffs/paper-lifecycle-20261031/framework-main-checkout.diff` | `5ee2229b41d7b77f8a04cb6bf83335c6887777eaff72de6dafbf64490a7c1fa8` |
| Frozen test | `handoffs/paper-lifecycle-20261031/test_paper_lifecycle.py` | `aba5e01b77e2bbf784335374a1644acd66129e4b8724a3b0a0ecbde2fd1f0ef0` |
| Gap handoff | `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | `f97be2ce8257dce25fefdbbdb15ff94d20e495504d990f117218f3fb63f1e68a` |
| Decisions D1–D23 | `handoffs/gap-eval-20261023/DECISIONS.md` | `3f643c1b5384c34328cb7ef91e2dac541228732630382febdbc9898a5d2947da` |
| Gap analysis | `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | `833f1f253fe76ab3bd17ca23fd177b318b21b65340d169f44538c0c214b7cc8d` |
| Roadmap | `continuity/roadmap-20261024.md` | `193452d5b5bab754eb904f6457416bfd6cecde951b8f854f03609dc2f77f2238` |
| Format examples | `reviews/m-a-plan.md`, `pipelines/ma-run.yml`, and the recent approved `reviews/m-i-plan.md` / `pipelines/drafts/m-i-run.yml` | — |

Relevant decisions: DECISIONS D2 ("the paper1 uncommitted diff is in-flight work, not plan") — PL is
exactly the step that turns it into landed work, on the human's explicit word (authority above);
GAP-ANALYSIS section 8 rows M-A … M-G ("`core/run/*` in-flight → wait until that diff lands") and A-13
(the `kimi-einfra` agent is run-specific; it stays out). D7 (every capability from the terminal) is met:
everything PL adds is a `scieflow run` command. D9–D11/D23 (data protection) are unaffected: PL changes
no dispatch, sandbox or data rule.

Never used: `/home/ubuntu/Github/ScieFlow` and its workspace worktrees (only the frozen copy).

## 1. For the human: what PL does, in plain words

**What lands.** The other session (the paper1 work in your main checkout, written on 2e93b14 on
2026-10-14 and never committed) taught the framework a second kind of run besides the research loop:
a **paper draft**. PL puts that work on main, unchanged in behaviour:

1. **A workflows map.** `schemas/status.yml` gains `workflows: paper-draft: [intake, outline, draft,
   cross-review, merge, verify, handoff]`. The research loop keeps its four phases.
2. **`scieflow run init SLUG --workflow paper-draft --goal FILE --approval per-campaign`** creates a
   native paper run: goal, config, status (all seven phases pending), `logs/`, `tools/`, `scratch/`, a
   `run.created` event — no notebook, no `iterations/`, and **no budget unless you give all three
   `--max-*` limits** (a partial set is refused). The approval mode must be named; nothing defaults it.
3. **`scieflow run adopt SLUG --workflow paper-draft --approval … [--goal FILE] --as-agent`** gives an
   already prepared paper folder (like the paper1 workspace) native status **without touching anything
   that is already there**: it writes only a missing `config.yml`/`goal.md`, a fresh `status.yml`, and
   appends one `note.run-adopted` event. It refuses if any `status.yml` exists, if config contradicts the
   chosen workflow/approval, and on symlinked or escaping paths. A failed adoption undoes only its own
   writes; if even that cleanup fails, a `.adoption-incomplete` marker blocks a retry until a person has
   looked.
4. **Workflow-aware phases.** `run mark`, `run checkpoint`, `run resume` and `run show` work on paper
   runs with the paper phases; `run advance` (start another iteration) is loop-only and refuses paper
   runs instead of resetting them. Checkpoint messages for paper runs name the paper phase and do not
   mention iterations.

**One decision I made for you (PL-D2).** The frozen copy holds two diffs. The README says diff B (main
checkout) is "the same changes plus the kimi agent hunk". It is not quite: diff B's `init.py` and
`docs/runs.md` carry a **later, safer version of adoption's failure handling** (the
`.adoption-incomplete` retry guard, and every cleanup step attempted even if one fails). Diff A lacks it,
so with diff A a failed cleanup could leave a `config.yml` without a status, and the next adoption would
treat those leftovers as your own approved settings. The queue note ("leave out the run-specific
kimi-einfra agent hunk") only makes sense for diff B. So PL lands **diff B without its
`config/agents.yml` hunk**. All other files are byte-identical between A and B. If you prefer diff A,
say "revise" at the plan gate.

**What had to be adapted, and why.** Main moved 11 milestones since 2e93b14 — but none of them touched
the eight framework files (they were held as "in-flight" by every plan), so the source diff applies
cleanly, byte for byte (section 4.1). The only adaptation is in the **test file**: since 2e93b14 the
sandbox (S4) refuses to dispatch an agent that has no `credentials:` entry, so the two dispatch tests
now use the suite's existing fake-credential fixtures (`grant_credentials`, `child_env`). Every
assertion of the frozen test stays as it was (section 5.2, exact diff).

**What else changes.** Small and stated: the docs (`docs/runs.md` section from the frozen work,
`docs/cli.md` for the two commands, one sentence of `docs/web.md`, one stale code comment in
`web/pages.py`), a tidy error instead of a Python traceback when `run checkpoint` meets a status of a
workflow without native phases, and new regression tests. `config/agents.yml` is **not** changed.

**What it does not do.** No change to how agents are dispatched, sandboxed or budgeted; no new
approval; the paper-draft skill (`research/skills/paper-draft/SKILL.md`) keeps writing its own status
— switching it to `run init --workflow paper-draft` / `run adopt` belongs to M-G2. Initialising or
adopting a run is **not** an approval of its goal or scope.

**Credit.** Every PL commit that carries the other session's code or tests says so in its message
(section 6, exact texts).

**After approval:** Haiku implements test-first, Sonnet reviews each ticket and the whole branch, the
operator's publish step pushes and merges with a merge commit; then M-G2 can start.

## 2. Base, dependencies and worktree

- **Dependencies:** none in the queue (`after: []`). PL needs main to contain the sandbox credential rule
  it adapts to (S4, merged long before M): t0 checks `git merge-base --is-ancestor
  8b374803c2eb6cad0df981d466ba097b973f3c84 <base>`; otherwise `BASE_RED`.
- **Preimage check (t0, recorded, not a stop):** on `base`, the blob of each of the eight framework files
  must still equal the preimage named in diff B's `index` lines (section 4.1). M-G (dev/m-g at ccb2e12)
  and M-D3 (dev/m-d3 at a94916b), the two lanes in flight while this plan was written, touch none of
  these files, nor `docs/cli.md`, `docs/web.md`, `web/pages.py` or `config/agents.yml`
  (`git diff --name-only <merge-base>..HEAD` checked on both worktrees). If a later merge did
  change one, t1 ports that file with `git apply --3way` and resolves by hand keeping both behaviours,
  and records each resolution in `RUN/scratch/pl/port-notes.md`.
- **Worktree:** t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`,
  the object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags
  --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`, then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/pl RUN/scratch/pl-wt <base>`.
- Commits are local on `dev/pl`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/pl/` (junit, logs, `executions.jsonl`, `port-notes.md`, `pr-body.md`); pytest temp dirs
  under `~/.cache/scieflow-pl-<step>/` (outside RUN and outside `~/.hermes`: the sandbox refuses a
  project or temp path that touches `~/.hermes`, and `require_single_workspace_ancestor` in the frozen
  test needs exactly one `workspace` ancestor — both were observed while planning).
- Planning probes (not the branch, not worktrees): `RUN/scratch/pl-plan-probe` (M + diff A) and
  `RUN/scratch/pl-plan-probe-main` (M + diff B minus `config/agents.yml`), both `git archive` exports;
  logs and the test-adaptation diff in `RUN/scratch/pl/plan-probe-*`. Implementers do not use them.

## 3. Files: allowed, protected and never edited

**The eight framework files ("in-flight" for every earlier milestone).** The installer's standing rule
says they may be edited only as an approved plan explicitly allows. **This plan allows exactly:**

| File | Allowed change | Ticket |
|---|---|---|
| `schemas/status.yml` | diff B hunk (workflows map), byte-exact | t1 |
| `src/scieflow/core/run/status.py` | diff B hunk, byte-exact | t1 |
| `src/scieflow/core/run/actions.py` | diff B hunk, byte-exact | t1 |
| `src/scieflow/core/run/checkpoint.py` | diff B hunk, byte-exact | t1 |
| `src/scieflow/core/run/init.py` | diff B hunk (with the retry guard), byte-exact | t1 |
| `src/scieflow/core/run/validate.py` | diff B hunk, byte-exact | t1 |
| `src/scieflow/core/run/cli.py` | diff B hunk byte-exact (t1), then one extra change: `checkpoint` turns `ValueError` into `click.ClickException` (PL-D6) | t1 |
| `docs/runs.md` | diff B hunk byte-exact (t2), then at most one added sentence on other research workflows (PL-D6) | t2 |
| `config/agents.yml` | **not edited** (the `kimi-einfra` hunk stays out, PL-D3) | — |

After t1, `git rev-parse HEAD~0:<path>` of the six byte-exact source/schema files must start with the
postimage ids of diff B (`3539d33` status.yml, `3c171ad` status.py, `a6afca1` actions.py, `6a37cd2`
checkpoint.py, `a9e38bb` init.py, `74479b9` validate.py); `cli.py` must equal postimage `49ef4e5` at
the t1 port commit and differ afterwards only by PL-D6. After t2's port commit `docs/runs.md` equals
postimage `8863193`.

**Never edited:** `config/agents.yml`, `config/sandbox.yml`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`,
`tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, every `AGENTS.md` (no protected edit in PL),
`src/scieflow/research/skills/paper-draft/SKILL.md`, `src/scieflow/core/service.py`,
`src/scieflow/core/sandbox.py`, `src/scieflow/core/agent_run.py`, `src/scieflow/core/events.py`,
`src/scieflow/core/store.py`, `src/scieflow/core/workspace.py`, `pyproject.toml`, `uv.lock`, and every
existing test file.

**Protected edits:** none (no `pipelines/drafts/pl-protected-edits.json`).

**Existing tests that change:** none expected — the focused probe of 21 test files passed unchanged
(section 4.3). **E-extra** (only if the full suite proves it necessary): an existing test whose
*assertion* encodes the old behaviour that PL deliberately changes (section 5.3) is updated minimally,
its node id, reason and diff listed in `reviews/pl-test-changes.md`. Weakening, skipping or deselecting
a test is never allowed.

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-31-pl-paper-lifecycle.md` | copy of this plan | t0 |
| `tests/core/test_paper_lifecycle.py` | new: the frozen test + the fixture adaptation of 5.2, nothing else | t1 |
| `tests/core/test_paper_lifecycle_port.py` | new: port regression tests P1–P5 (5.4) | t1 |
| `tests/web/test_start_paper_draft.py` | new: Start-page test P6 (5.4) | t1 |
| the seven files of the table above (except `docs/runs.md`) | diff B + PL-D6 | t1 |
| `docs/runs.md` | diff B hunk (+ PL-D6 sentence) | t2 |
| `docs/cli.md` | `run init --workflow`, new `run adopt` entry, `run mark`/`checkpoint` sentence | t2 |
| `docs/web.md` | Start row (line 141): the workflow default sentence | t2 |
| `src/scieflow/web/pages.py` | comment above `DEFAULT_WORKFLOW` only (lines 202–211) | t2 |
| `tests/test_docs_paper_lifecycle.py` | new: docs pins (5.5) | t2 |

Hotspots: none expected (no other open lane touches these files); `docs/cli.md` is shared with other
milestones' docs tickets, so t2 edits only the `run` section.

## 4. Current code, verified (on M = 8b37480)

### 4.1 The framework files are byte-identical to the diff's preimage

`git diff 2e93b14 M -- <file>` is empty for all eight framework files, and their blob ids on M equal
the `index` preimages of diff B: `docs/runs.md` e0e8a06, `schemas/status.yml` 4641835,
`core/run/actions.py` c2d0d48, `checkpoint.py` b591a14, `cli.py` f9b0c0c, `init.py` 0f52d2a,
`status.py` c85324d, `validate.py` 60e6db7. `git apply --check --exclude=config/agents.yml` of diff B
on an export of M: all eight patches apply. Diffs A and B differ only in `init.py` (B adds the
`.adoption-incomplete` guard and independent cleanup, postimage `a9e38bb` vs A's `d3002e0`) and
`docs/runs.md` (B documents it, `8863193` vs `13008c0`); the other six hunks are identical; B alone has
the `config/agents.yml` hunk.

### 4.2 What the current code does (the behaviour PL changes)

- Vocabulary: `schemas/status.yml:1-5` has only the loop `phases`; `schemas/status-research.yml:3-7`
  already lists `paper-draft: [intake, outline, draft, cross-review, merge, verify, handoff]` for the
  lenient research validator.
- `status.py:43-54` `new_status` always writes `run:` and the four loop phases; `:73-79` `mark` and
  `:82-87` `next_pending` check only the loop phases; `:90-95` `advance_iteration` resets to loop phases.
- `checkpoint.py:12-22` always writes an iteration/`iterations/`/`budget.yml` resume text; `:25-31`
  `resume_info` likewise.
- `actions.py:27-31` `mark_phase` emits `iteration=st["iteration"]` (KeyError on a status without
  one, after the phase was already written); `:76-82` `advance_iteration` checks the budget first;
  `:57-62` `_refuse` checkpoints on a budget refusal.
- `init.py:19-53` `init_workspace` always loads defaults (`:29`), always writes loop status, budget and
  notebook, and `rmtree`s the directory on any failure (`:50-52`), with an `exists()` check then a
  non-exclusive `mkdir(parents=True)` (`:32-35`). No adoption exists.
- `cli.py:42-65` `run init` has no `--workflow`; `:145-154` `checkpoint` does not catch `ValueError`.
- `validate.py:21-36` `validate_status` requires `run:` and exactly the loop phases; `:39-52`
  `validate_status_research` is the lenient research validator.
- `store.py:39-44` `locked` creates the parent directory and a sibling `.lock`; `:47-57`
  `_atomic_write` (tempfile + `os.replace`) — both used by diff B. `events.py:52-67` `emit` builds the
  envelope `{id, ts (ms), run, slug, type, actor, data}`; adoption builds the same keys itself
  (ts in seconds, from status `created`).

### 4.3 Callers on M that the port must keep working (checked)

- `service.create_run` (`service.py:154-209`) already forwards `workflow` into `init_workspace`
  overrides (`:180-183`) and turns `ValueError` into `ServiceError` (`:198-206`); the frozen test's
  `service.create_run(..., workflow="paper-draft")` and its "explicit --approval" refusal rely on exactly
  that. `service.start_run` (`:1544-1581`) chains `create_run`; the web Start page
  (`web/pages.py:268-330`) and `POST /api/v1/runs` (`web/api.py:71-80`) call them. The Start form
  prefills approval and the three budget limits from `config/defaults.yml` (`web/pages.py:222-233`), so a
  paper run from the form gets the explicit three-limit budget the user sees, or no budget when all
  three are cleared; a partial set is refused and re-renders the form.
- `service.mark_phase` / `advance_run` / `checkpoint_run` (`service.py:1349-1375`) catch `ValueError` →
  `ServiceError`, so the web run page shows the loop-only refusal of `advance` as an error.
- `workspace.describe` (`workspace.py:108-130`) already derives `kind` from `workflow:` (`:110-115`) and
  the current phase from the phase order (`:97-106`): a native paper status lists as kind `paper-draft`,
  phase `intake`. `workspace.list_runs` skips dot-entries (`:141-144`), so `workspace/.<slug>.init.lock`
  files are not listed as runs.
- `menu.WORKFLOWS` (`core/menu.py:49-80`) offers six workflows to the Start page; only `paper-draft`
  gets native paper status — `lit-review`, `gap-discovery`, `paper-review`, `experiment-design` keep
  today's loop-shaped status with `workflow:` in `config.yml` (unchanged).
- Dispatch since 2e93b14: `agent_run.prepare` builds the sandbox layout and then
  `sandbox.attach_credential` (`agent_run.py:388-392`), which refuses an agent without a `credentials:`
  entry (`sandbox.py:1618-1629`). The suite's fixtures for that: `grant_credentials`
  (`tests/conftest.py:46-72`, fake 0600 token) and `child_env` (`:24-43`, fake HOME for child
  processes).
- Docs on M: `docs/cli.md:123-129` documents `run init` without `--workflow` and says "Create a
  research-loop run workspace"; `docs/web.md:141` says research-loop is "the one workflow this form's own
  `init_workspace` call actually produces the shape of"; `web/pages.py:202-211` says the same in a
  comment. After PL all three are stale and t2 corrects them.

### 4.4 Planning probes (evidence, not acceptance)

All memory-capped, `PYTHONPATH=<export>/src:<export>`, basetemp under `~/.cache/`:

1. M + frozen test only: collection error `ImportError: cannot import name 'adopt_workspace'` (RED).
2. M + diff A + frozen test unmodified: 54 passed, 1 failed
   (`test_adopted_dispatch_preparation_and_budget_guards`: `SandboxError: agent 'fixture' has no
   credentials: entry`), 1 skipped (the native dispatch test, same refusal) —
   `RUN/scratch/pl/plan-probe-astra-unadapted.log`.
3. Same + the fixture adaptation of 5.2: 56 passed (the sandboxed subprocess dispatch really ran, not
   skipped).
4. Same, focused set of 21 files (`tests/test_status.py tests/test_validate.py tests/test_checkpoint.py
   tests/test_sfx_init.py tests/test_service_e2e.py tests/test_dry_run.py tests/core/test_create_run.py
   tests/core/test_run_actions.py tests/core/test_service.py tests/core/test_workspace.py
   tests/core/test_charter.py tests/core/test_events.py tests/core/test_project.py
   tests/core/test_paper_lifecycle.py tests/web/test_start_page.py tests/web/test_run_page_actions.py
   tests/web/test_dashboard_reads.py tests/web/test_mutations.py tests/web/test_walkthrough.py
   tests/research/test_e2e_paper_draft.py tests/research/test_e2e_stub.py`): **455 passed, 0 failed**,
   exit 0 — `RUN/scratch/pl/plan-probe-focused-455.log`.
5. M + diff B (minus `config/agents.yml`) + adapted test: 56 passed —
   `RUN/scratch/pl/plan-probe-main-variant.log`; a throwaway guard probe (status write fails, then the
   `config.yml` cleanup fails) raised `adoption failed: …; rollback incomplete: config.yml: …; inspect
   …/.adoption-incomplete before retry`, and the retry was refused with "incomplete adoption" —
   `RUN/scratch/pl/plan-probe-guard.log`.

The full default suite was not run on the probes (it is t0's and branch's job, under the shared lock).

## Part A — Spec

### 5.1 Behaviour (from the frozen work; diff B is the source of truth)

1. `schemas/status.yml` gains the `workflows:` map with `paper-draft` (7 phases); the comment says the
   legacy `phases` stay the research-loop vocabulary, not a union.
2. `status.phases_for(workflow="research-loop", schema=None)`: loop phases for `research-loop`, the
   mapped list otherwise, `ValueError("unknown workflow: …")` for anything else. An absent `workflow:` on
   a status means research-loop.
3. `status.new_status(run, approval, workflow="research-loop")`: loop statuses keep `run:` and are
   byte-shape-identical to today; paper statuses carry `workflow:` and `slug:` instead of `run:`, plus
   `id`, `created`, `approval`, `iteration: 1`, the paper phases pending, `stopped: null`.
4. `mark` and `next_pending` use the status's own workflow; `advance_iteration` (pure and the action)
   calls `require_loop` first — the action does so **before** the budget check, so a paper run is never
   checkpointed by a refused advance.
5. `mark_phase` emits `iteration` only when the status has one (legacy hand-written paper statuses).
6. `checkpoint`/`resume_info` for paper runs: "Resume: read status.yml; paper-draft phase '<next or
   complete>'." (+ " Budget ledger in budget.yml." only when one exists) and "Run active: paper-draft,
   next phase '…'."; loop texts unchanged.
7. `validate_status` checks the identity key (`run` for loop, `slug` otherwise) and the workflow's exact
   phase list; an unknown workflow is one error.
8. `run init --workflow research-loop|paper-draft` (default research-loop). Paper: explicit
   `--approval` required (`ValueError` → "paper-draft initialization requires explicit --approval"); no
   defaults loaded; budget only with all three limits; no `iterations/`, no notebook. Creation is
   serialised by a sibling `workspace/.<slug>.init.lock`; the workspace is claimed by an exclusive
   `mkdir` **outside** the cleanup, so a losing creator never deletes the winner's directory; symlinked
   workspace roots/paths and unsafe slugs are refused.
9. `run adopt SLUG --workflow paper-draft --approval … [--goal FILE] [--as-agent]` → `adopt_workspace`:
   exactly the rules of the docs section in diff B (`docs/runs.md` "Native paper-draft lifecycle"),
   including: existing goal wins over `--goal`; config may omit but not contradict workflow/approval;
   malformed or non-mapping config refused; any existing `status.yml` (even empty) refused with
   "already initialized"; control files and their `.lock`s must be regular files (no symlinks, also
   dangling); init lock + the ordinary store locks on `status.yml`, `config.yml`, `goal.md`,
   `events.jsonl`; workspace re-checked after waiting for the lock; events appended as a byte-identical
   prefix extension; caught write failures roll back only this operation's files and its own event
   append (never a concurrent event, never the directory); the `.adoption-incomplete` guard is created
   before the first write, removed on success or complete rollback, and otherwise refuses further
   adoption until a person removes it after inspection.

### 5.2 The one adaptation of the frozen test (exact)

`tests/core/test_paper_lifecycle.py` = the frozen file plus exactly this change (also saved as
`RUN/scratch/pl/plan-probe-test-adaptation.diff`); no assertion changes:

- `fixture_dispatch(project, with_budget=False)` → `fixture_dispatch(project, grant, with_budget=False)`,
  and after it writes `config/agents.yml` it calls `grant(project.root, "fixture")`.
- `test_adopted_dispatch_preparation_and_budget_guards(paper_project)` takes `grant_credentials` and
  passes it to `fixture_dispatch`.
- `test_native_fixture_dispatch_keeps_sandbox_intact(paper_project)` takes `grant_credentials` and
  `child_env`, passes the first to `fixture_dispatch` and `env=child_env` to `subprocess.run`.

The file keeps its module docstring; t1 adds one comment line under it naming the origin (the paper1
session's work, frozen 2026-10-31) and the adaptation (S4 credentials), so the credit survives in the
tree as well as in the history.

### 5.3 Decisions

- **PL-D1 Port by applying, not retyping.** The preimages match (4.1), so t1 uses `git apply --index
  --exclude=config/agents.yml --exclude=docs/runs.md <diff B>` and t2 `git apply --index
  --include=docs/runs.md <diff B>`; byte-exactness is checked against the postimage ids (section 3).
- **PL-D2 Diff B, not diff A** (section 1): same feature, safer failure handling, consistent with the
  queue note. The README's "same changes" line is recorded as inaccurate in the PR body.
- **PL-D3 The `config/agents.yml` hunk stays out** (run-specific `kimi-einfra` agent pointing into one
  run's workspace; GAP A-13). `git diff base..tip -- config/agents.yml` must be empty.
- **PL-D4 Test adaptation only through existing fixtures** (5.2); the sandbox and credential code are
  not touched, and no test is skipped to make it pass.
- **PL-D5 Accepted behaviour changes** (deliberate, pinned by tests, documented):
  (a) `run init` from the CLI now records `workflow: research-loop` in a new loop run's `config.yml`
  (the Start page already did); (b) a hand-written research-module status with a workflow that has no
  native phases (`lit-review`, `gap-discovery`, `paper-review`) is refused by `run mark` with
  "unknown workflow: <name>" (before: "unknown phase", or a KeyError for loop phase names) and by
  `run checkpoint` (before: KeyError); nothing is written in either case; (c) a paper run from the
  Start page or API needs an approval mode and gets no default budget except what the form submits;
  (d) `workspace/.<slug>.init.lock` files appear next to runs (ignored by run listing; may remain).
- **PL-D6 Two small additions beyond the frozen bytes:** `run checkpoint` wraps `ValueError` as
  `click.ClickException` (a tidy "Error: unknown workflow: lit-review", exit 1, nothing written —
  the frozen code would print a traceback); `docs/runs.md` / `docs/cli.md` say which workflows the
  native `mark`/`checkpoint` support. No other source change.
- **PL-D7 Adoption's event timestamp stays as frozen** (seconds, equal to the status `created`), not
  `emit`'s milliseconds: both are ISO-8601 UTC and no reader depends on the precision; changing it would
  diverge from the frozen behaviour for no gain. Recorded as residual R3.
- **PL-D8 No protected edits and no skill change.** Root/research `AGENTS.md` and the paper-draft
  `SKILL.md` keep their text; pointing the paper-draft protocol at `run init --workflow paper-draft` /
  `run adopt` is M-G2's (it already edits that workflow).
- **PL-D9 Credit in history and tree** (5.2 and the commit texts in section 6).

### 5.4 Port regression tests (new, t1)

`tests/core/test_paper_lifecycle_port.py` (its own copy of the frozen `paper_project` fixture and the
`prepared` helper; synthetic data only):

- **P1 schemas agree:** `schemas/status.yml` `workflows["paper-draft"]` equals
  `schemas/status-research.yml` `workflows["paper-draft"]`; `status.phases_for("research-loop")` equals
  `schemas/status.yml` `phases`; `status.phases_for("lit-review")` raises `ValueError` "unknown
  workflow".
- **P2 loop init unchanged:** `run init loop --goal g` (no `--workflow`) writes a status whose keys are
  exactly `run, id, created, approval, iteration, phases, stopped`, the four loop phases pending,
  `iterations/` and `notebook.md`, a budget from the fixture defaults, and a config equal to the
  defaults plus `workflow: research-loop` (PL-D5a); `validate_status` returns `[]`.
- **P3 advance refused before any budget checkpoint:** a paper run created with all three limits and its
  iteration budget exhausted (`run spend --iterations`); `run advance --as-agent` and
  `service.advance_run` both refuse with "only research-loop runs can advance iterations";
  `status.yml` (no `stopped`), `budget.yml` and `events.jsonl` bytes unchanged (no checkpoint, no
  `job.refused`).
- **P4 other research workflows refused cleanly:** a hand-written `workflow: lit-review` status
  (phases from `status-research.yml`, synthetic); `run mark <slug> brief done --as-agent` and `run
  checkpoint <slug> --reason user --as-agent` each exit 1 with "Error:" and "unknown workflow" in the
  output and no traceback (`result.exception` is `SystemExit`), all files byte-identical;
  `validate_status_research` still returns `[]` for it.
- **P5 adoption retry guard:** diff B's guard, end to end: a status write fails and the cleanup of the
  newly written `config.yml` fails too → `OSError` naming "rollback incomplete" and the guard path;
  `.adoption-incomplete` exists and no `status.yml`; a second `adopt_workspace` and `run adopt` refuse
  ("incomplete adoption", CLI exit 1 with "Error:"); a symlinked `.adoption-incomplete` is refused as a
  control symlink; after the test removes the guard (as the inspecting person would), adoption succeeds
  and `events.jsonl` has the original history plus exactly one `note.run-adopted`.

`tests/web/test_start_paper_draft.py` (with the `tests/web/conftest.py` fixtures, the way
`tests/web/test_start_page.py` posts the form):

- **P6 Start page:** POST `/start` with `workflow=paper-draft`, `approval=per-campaign`, the three
  limits `0` → 303 to `/runs/<slug>`; the run's status has `workflow: paper-draft`, the paper phases,
  no `budget.yml`, and `service.run_detail` shows kind `paper-draft`, phase `intake`. The same POST with
  `approval=` empty → 200 with the form re-rendered and "approval" in the error, no run directory. The
  same POST with only `max_wall_minutes=5` → 200, "all three" in the error, no run directory.

### 5.5 Docs (t2)

- `docs/runs.md`: diff B's hunk byte-exact (synopsis with `--workflow`, "Native paper-draft lifecycle"
  section), then at most one sentence: native `mark`/`checkpoint`/`resume` know research-loop and
  paper-draft statuses; other research workflows keep their own `status.yml` protocol and are refused.
- `docs/cli.md` `run` section (`:123-129` and the `mark`/`checkpoint` entries): `run init` synopsis
  with `[--workflow research-loop|paper-draft]` and one paragraph (paper: approval required, budget all
  three or none, no notebook/iterations, link to `runs.md#native-paper-draft-lifecycle`); a new block
  `uv run scieflow run adopt SLUG --workflow paper-draft --approval per-campaign|autonomous [--goal
  FILE] [--as-agent]` with a two-sentence summary and the same link; `run advance` says it is
  research-loop only.
- `docs/web.md:141`: replace "defaults to `research-loop`, the one workflow this form's own
  `init_workspace` call actually produces the shape of" with: defaults to `research-loop`;
  `paper-draft` creates a native paper run (approval required; the three limits all or none), every
  other workflow gets the loop-shaped status with the workflow recorded in `config.yml`.
- `web/pages.py:202-211`: the comment above `DEFAULT_WORKFLOW` says the same (code unchanged).
- `tests/test_docs_paper_lifecycle.py`: (a) every option of the `run init` and `run adopt` click
  commands (introspected from `scieflow.cli.main`) appears in `docs/cli.md`; (b) `docs/runs.md` has the
  heading `### Native paper-draft lifecycle` and the exact adopt command line of diff B; (c) the
  `paper-draft` phase list in `docs/runs.md` equals `status.phases_for("paper-draft")`; (d) `docs/web.md`
  no longer contains "the one workflow this form's own"; (e) `docs/cli.md` mentions that `run advance`
  is research-loop only.

### 5.6 What PL does not do (stated)

No change to dispatch, sandbox, credentials, data levels, budgets' meaning, gates or events' type set;
no paper-draft skill change; no `config/agents.yml` change; no new web control (an "adopt" button is
not requested); no migration of existing workspaces (adoption is run by a person or coordinator when
wanted); no live model call.

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check
`claude-haiku-5.5`); Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider
anthropic. A usage limit is a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network except t0's `git ls-remote`/fetch, no
push/PR, no credentials, no patient data (synthetic fixtures only), never reads or writes
`/home/ubuntu/Github/ScieFlow` (the frozen copy under `RUN/handoffs/paper-lifecycle-20261031/` is the
only source), never edits a file outside the section-3 file list. Every implementation, fix, branch and
final card runs the **SCOPE guard** and reports `scope_guard_exit`:
`git -C WT diff --name-only <base>..HEAD` ⊆ the section-3 file list, and `git -C WT diff --exit-code
<base> -- config/agents.yml config/sandbox.yml CLAUDE.md AGENTS.md src/scieflow/research/AGENTS.md
src/scieflow/research/skills/paper-draft/SKILL.md tests/probes/k_task8_binding.py
tests/probes/test_k_task8_binding.py tests/fixtures/k_task8_stream_fixture.py` (exit 0).

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/pl-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-pl-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/pl/junit G6_CACHE_DIR=~/.cache/scieflow-pl RUN/tools/g6_pytest.sh WT <head-sha> pl-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the
  standing rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the
  background with completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> pl-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes:** the ones named in the installer's standing rules, one rerun, both results
  recorded. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/pl/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused sets** (clean-tree runner on the ticket's final commit):
  - t1: `tests/core/test_paper_lifecycle.py tests/core/test_paper_lifecycle_port.py
    tests/web/test_start_paper_draft.py` plus the 20 other files of section 4.4 item 4.
  - t2: `tests/test_docs_paper_lifecycle.py tests/core/test_paper_lifecycle.py
    tests/web/test_start_page.py tests/test_docs_journal.py tests/test_docs_design.py
    tests/test_docs_literature_kernel.py tests/test_docs_literature_kernel_ii.py`.

### t0 — worktree, plan copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; require `git merge-base --is-ancestor
   8b374803c2eb6cad0df981d466ba097b973f3c84 <base>`; otherwise `BASE_RED`. `worktree add -b dev/pl
   RUN/scratch/pl-wt <base>` (on a retry reuse it at base or base + own docs commit; never reset).
2. Verify the four frozen inputs' sha256 (section 0). Record, per framework file, whether the blob on
   base equals diff B's preimage (section 4.1) and run `git apply --check --exclude=config/agents.yml
   <diff B>` in WT (no write); write both into `reviews/pl-t0-baseline.md` with: the first-parent merges
   `M..base` and whether any changed a section-3 file; re-verification of every `path:line` cite of
   section 4 on base (still present / moved (new line) / changed).
3. **Green-base gate on base** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and
   junit paths, nothing else.
4. Copy `reviews/pl-plan.md` byte for byte to `docs/superpowers/plans/2026-10-31-pl-paper-lifecycle.md`
   and commit it alone: `docs(plans): PL paper-lifecycle landing plan`.

Verdicts `DONE` | `BASE_RED`. Result keys add `preimages_match` (true/false per file) and `baseline`
counts.

### t1 — port the framework code and its tests, test-first (Haiku) → t1r (Sonnet)

Files: section 3 rows for t1.

1. **RED commit.** Create `tests/core/test_paper_lifecycle.py` = the frozen file + 5.2 (apply
   `RUN/scratch/pl/plan-probe-test-adaptation.diff` to a copy of the frozen file made with `install -m 644`
   (the frozen file is read-only), then add the origin comment line), `tests/core/test_paper_lifecycle_port.py` (P1–P5) and
   `tests/web/test_start_paper_draft.py` (P6). Run them: collection fails on `adopt_workspace` (frozen
   file) and P1–P6 fail for the expected reasons; record each. Commit:
   `test(run): paper-lifecycle tests, ported from the paper1 session (PL t1, RED)` with body:
   "tests/core/test_paper_lifecycle.py is the paper1 session's untracked test (written against 2e93b14,
   2026-10-14; frozen read-only copy handed off 2026-10-31). Only its two dispatch fixtures are adapted
   to the sandbox credential rule (S4): grant_credentials and child_env. New port regression tests
   P1-P6 (plan reviews/pl-plan.md 5.4)."
2. **GREEN port commit.** In WT: `git apply --index --exclude=config/agents.yml --exclude=docs/runs.md
   RUN/handoffs/paper-lifecycle-20261031/framework-main-checkout.diff` (fallback per section 2 only if
   t0 recorded a preimage mismatch). Verify the postimage ids (section 3). Commit:
   `feat(run): native paper-draft lifecycle: workflows map, run adopt, workflow-aware phases (PL t1)`
   with body: "Port of the paper1 session's uncommitted framework work (written on 2e93b14, 2026-10-14,
   in the main checkout; frozen read-only copy handed off 2026-10-31), applied byte for byte: schemas/
   status.yml workflows map; status.phases_for and workflow-aware mark/next_pending/advance; paper
   checkpoint/resume texts; init_workspace --workflow paper-draft; adopt_workspace with the
   .adoption-incomplete retry guard; validate_status per workflow; `scieflow run init --workflow` and
   `scieflow run adopt`. The run-specific kimi-einfra agent hunk of config/agents.yml is left out.
   Credit: the paper1 session."
3. **PL-D6 commit.** `run checkpoint` catches `ValueError` → `click.ClickException` (P4 needs it).
   Commit: `fix(run): tidy refusal when checkpoint meets an unknown workflow (PL t1)`.
4. Focused set green (clean-tree runner on the last commit), SCOPE guard exit 0, postimage check.

Verdict `DONE`. Result keys add `red` (node ids and reasons), `postimages_ok`, `scope_guard_exit`.

**t1r review (Sonnet).** Independent: (a) RED really failed for the stated reasons (reproduce on a `git
archive` export of base with the RED commit's test files copied in, memory-capped; no extra worktree); (b) the six source/schema files equal
diff B's postimages and `cli.py` differs from `49ef4e5` only by PL-D6; `config/agents.yml` untouched;
(c) the frozen test differs from the frozen copy only by 5.2 plus the comment line (`diff` against
`${input_frozen_test}`); (d) P1–P6 are meaningful (each fails when its behaviour is reverted — mutate
one line per test locally, never committed) and synthetic; (e) security properties of 5.1 item 9 still
hold in the code: symlink refusals before any write, re-check after the lock, rollback never touches
pre-existing bytes, guard semantics; (f) focused set re-run green; SCOPE guard exit 0; commit messages
carry the credit. Writes `reviews/pl-t1-review-r<N>.md` (line 1 `VERDICT: PASS|CHANGES|FAIL`).

**t1f fix (Haiku).** Closes every t1r finding with new commits and tests, re-runs the focused set,
SCOPE guard; result names each finding and how it was closed.

### t2 — docs and the stale comment (Haiku) → t2r (Sonnet)

1. **RED commit:** `tests/test_docs_paper_lifecycle.py` (5.5 a–e); run it, record failures; commit
   `test(docs): pins for the paper-lifecycle docs (PL t2, RED)`.
2. **Port commit:** `git apply --index --include=docs/runs.md <diff B>`; verify postimage `8863193`;
   commit `docs(runs): native paper-draft lifecycle and run adopt, from the paper1 session (PL t2)` with
   the same credit sentence as t1's port commit.
3. **Docs commit:** the PL-D6 sentence in `docs/runs.md`, `docs/cli.md`, `docs/web.md:141`, the
   `web/pages.py` comment; commit `docs: run init --workflow, run adopt, Start page paper drafts (PL
   t2)`.
4. Focused set green; `mkdocs build --strict` into `RUN/scratch/pl/site` if mkdocs is in the venv (else
   "not available"); SCOPE guard.

**t2r review (Sonnet):** docs match the code (every option, every refusal described exists; no claim
the code does not make), `docs/runs.md` hunk byte-exact, only the comment changed in `pages.py`
(`git diff` shows no code line), docs tests meaningful, focused set re-run, SCOPE guard; writes
`reviews/pl-t2-review-r<N>.md`. **t2f** as t1f.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by the added tests); SCOPE guard over `base..tip`; linear
history (no merge commits) with the commits of t0–t2 (+ fixes); `git diff base..tip -- tests/` adds
files only (no existing test file changed unless a recorded E-extra entry); postimage check of section
3 re-run on the tip; `config/agents.yml` absent from the diff. Writes `RUN/scratch/pl/pr-body.md`
(plain English for the human: what landed and from whom — credit the paper1 session; PL-D1..D9, with
PL-D2 (diff B, and the README's inaccurate "same changes" line) and PL-D5's behaviour changes spelled
out; absolute test counts; residuals R1–R5; "please merge with a merge commit"; no credentials, prompts
or patient data) and `reviews/pl-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every 5.1 item, 5.2 exactness, 5.3 decisions, 5.4/5.5 tests present and
meaningful), code and security (adoption: no write before every refusal check, symlink and escape
refusals, lock order, rollback ownership, guard; init: exclusive mkdir outside cleanup; no change to
dispatch/sandbox/credentials/data rules; `config/agents.yml` untouched), evidence (re-run memory-capped
the t1 and t2 focused sets; confirm the branch result's absolute counts; re-run the SCOPE guard and the
postimage check; `pr-body.md` accurate and credits the paper1 session). Output
`reviews/pl-final-review-r<N>.md`. PASS → the installer's publish step; CHANGES → final_fix (new commits,
full suites re-run under the lock) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/pl-run.yml`, linear)

```
t0        Haiku   worktree, plan copy, green base, preimages   DONE→t1, BASE_RED→escalate
t1        Haiku   port code + tests test-first                  DONE→t1r
t1r       Sonnet  review                                        PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                             DONE→t1r
t2        Haiku   docs + stale comment                          DONE→t2r
t2r       Sonnet  review                                        PASS→branch, CHANGES→t2f, FAIL→escalate (max 3)
t2f       Haiku   fix (new commits)                             DONE→t2r
branch    Haiku   full suites + PR body                         DONE→final, RED→escalate
final     Sonnet  whole-branch review                           PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                             DONE→final
(publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install; no protected edits)
```

Caps `{max_cards: 40, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/pl-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `scope_guard_exit`. Inputs: handoff, decisions,
gap, and the four frozen files (`frozen_readme`, `frozen_main_diff`, `frozen_astra_diff`,
`frozen_test`); the installer adds `plan`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Research-module statuses.** Hand-written `lit-review` / `gap-discovery` / `paper-review`
  statuses are still outside the native commands (now refused cleanly instead of crashing). A budget
  refusal inside such a run (only if someone gave it a `budget.yml`) still ends in an error from the
  checkpoint attempt, as before (KeyError then, ValueError now).
- **R2 Paper-draft skill not yet native.** Until M-G2, the paper-draft protocol writes its own
  `status.yml`; per-agent phase mappings it may write (`draft: {claude: done, …}`) are overwritten by a
  later `run mark` of that phase, and such a status does not pass the strict `validate_status` (the
  lenient research validator still accepts it).
- **R3 Adoption event timestamp** has second precision (PL-D7).
- **R4 Lock and guard files.** `workspace/.<slug>.init.lock` stays after creation/adoption;
  `.adoption-incomplete` stays after a failed cleanup until a person removes it (by design).
- **R5 Not a cross-file transaction.** A process kill between writes can leave partial metadata; the
  published status stays fail-closed on retry (documented in diff B's docs).

## 9. After PL

- **M-G2** (`after: [m-g, pl]`) plans against PL's merged API: `status.phases_for`, the `workflows`
  map, `adopt_workspace`/`run adopt`, workflow-aware `mark`/`checkpoint`, and wires the paper-draft
  skill to them.
- **Operator follow-ups (not PL's code):** once PL is merged, the installer's standing rule naming the
  eight files as "in-flight paper-lifecycle files" (`tools/milestone_chain.py` `COMMON_RULES`) is stale
  and can be removed or narrowed to `config/agents.yml`; plans written before PL (M-H, M-I) treat those
  files as never-edited — still safe, and their refresh steps will see PL's changes. The frozen README's
  "same changes" line could be annotated with PL-D2's finding.
- Follow-up candidates: an "adopt" control on the run page; `workspace.describe` showing the next
  pending paper phase in the state column (today "started").
