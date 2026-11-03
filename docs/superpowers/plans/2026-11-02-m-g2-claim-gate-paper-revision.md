# M-G2 plan: claim check in the paper-draft verify phase and a paper-revision workflow (M-G ticket t8)

Pipeline `m-g2-plan`, step plan, round 2, epoch 1. Card `t_78fff58c`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261102_134317_52d56e`). Written
2026-11-02. Size M: one implementation plan; the spec part is section 5 (no separate spec file).

This is a plan only. No code was changed in the repository, nothing was committed, pushed or merged, no
live attempt was made. The only network call was the read-only `git ls-remote`. Planning probes ran in
throwaway `git archive` exports under `RUN/scratch/` (section 4.5). `/home/ubuntu/Github/ScieFlow` was
neither read nor written.

**Round 2 of this epoch is a fresh plan, not a continuation** (operator ruling
`approvals/auto-rulings/m-g2-plan-plan-e1-1.1.md`). Round 1 never ran in this epoch; there is no earlier
review to close. The first plan of 2026-10-31 (BLOCKED because the paper-lifecycle work was not on main;
archived at `continuity/archive/m-g2-plan-blocked-20261031`) was used only as background. Every claim
below was re-verified against the current code.

## 1. For the human: what M-G2 adds, in plain words

M-G1 (merged 2026-11-02) gave ScieFlow a claim ledger (`scieflow manuscript claims …`) and the
journal-letter commands (`scieflow manuscript revision …`). PL (approved by you on 2026-11-02, running
now) gives paper drafts their own native phases (`run init --workflow paper-draft`, `run adopt`,
`run mark`). What is still missing is the link between the two: a paper run can record "verify: done"
while its manuscript still has a wrong number, and a journal revision has no phases of its own. M-G2
adds exactly that, and nothing else:

1. **The verify phase checks the claims.** In a paper run, `scieflow run mark <run> verify done` (and the
   same action in the browser) first runs M-G1's claim check on the run's manuscript. With any claim
   error — a number that is not in its evidence, an orphan number, a retracted or blocking reference,
   stale evidence, a run from changed code, unreleased L2 data — it refuses, says how many errors and
   which commands show them, and leaves the status unchanged. If the check cannot run, it refuses too
   (fail closed). Every other phase, and every research-loop run, behaves exactly as today.
2. **A paper-revision workflow.** `scieflow run init <run> --workflow paper-revision --approval …` (or
   `run adopt … --workflow paper-revision` for a folder you prepared) creates a run with six phases:
   `intake` (bring in the submitted manuscript and its data, store the decision letter), `structure` (an
   agent turns the letter into reviewer comments, verbatim quotes only), `revise` (change the
   manuscript), `respond` (answer every comment), `verify` (build and claim check), `resubmit`
   (package). `respond done` is refused until the revision coverage check passes for the latest letter;
   `verify done` has the same claim check as a paper draft.
3. **The paper-draft protocol uses the native lifecycle.** The paper-draft skill creates its run with
   `run init`/`run adopt` and moves phases with `run mark` instead of writing `status.yml` by hand
   (PL left this to M-G2). One short sentence in the research `AGENTS.md` (a protected edit you approve
   here) says the same for every paper run.

There is no `--force`. A finding you accept is waived by you with `claims waive`; a blocking reference
clears through the human-only `review-blocker` gate; unreleased L2 data through `scieflow data release`.
ScieFlow copies nothing between runs. Everything runs locally: no new dependency, no network, no new
gate kind, no new event type.

**Decisions taken under Rule D that you may veto at the approval gate** (full list in section 5.9):

- **One gate seam** in `actions.mark_phase`, which every path (CLI, service, web API, run page) already
  goes through. The browser therefore gets the same refusals; it still cannot *create* a paper-revision
  run (the Start page offers only the menu workflows; the CLI is complete, decision D7).
- **A paper-draft run without any `manuscript/` folder still passes verify** (recorded as
  `no-manuscript`), because PL's landed lifecycle test marks every paper-draft phase done in a run with
  no manuscript and must stay green unchanged. A paper-revision run without a manuscript is refused.
- **Phase order of paper-revision:** `revise` before `respond`, because the respond check needs the
  manuscript changes to exist.
- **One protected edit** (research `AGENTS.md` rule 5, exact text in section 3.3). Nothing else in any
  `AGENTS.md`/`CLAUDE.md` changes.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head
`dev/m-g2` → base `main`, one PR, merged with a merge commit, title "M-G2 Claim check in the verify phase
and a paper-revision workflow". The body (written by the branch step) lists items 1–3 above, decisions
M-G2-D1..D15, absolute test counts and residuals R1–R7.

## 0a. Inputs and references used

Immutable inputs (sha256 re-computed on this card; all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…47da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |
| `continuity/milestones.yml` (not pinned) | entry `m-g2`: `after: [m-g, pl]`, note "plan against reviews/pl-plan.md if PL is approved but not merged" |

Scope sources: `reviews/m-g-plan.md` (approved) decisions M-G-D15, M-G-D17, M-G-D19, section 5.13 ("`check_run`
public so M-G2 needs no change in the claims package") and section 10 (the M-G2 handoff: 10.2 round-1
wiring design, 10.3 round-1 ticket t8); human ruling `approvals/m-g-split-human-20261030.md` (t8 → M-G2);
`approvals/pl-landing-human-20261031.md` and `approvals/pl-plan-approved-human-20261102.md` (PL approved,
decision PL-D2: diff B without the `config/agents.yml` hunk); `reviews/pl-plan.md` (approved, review r1
PASS) section 9 ("M-G2 … wires the paper-draft skill to them") and PL-D8 ("pointing the paper-draft
protocol at `run init --workflow paper-draft` / `run adopt` is M-G2's"); `reviews/pl-t0-baseline.md` (PL t0
on base `2bcac1d`). GAP-ANALYSIS section 8 row M-G (`:294`, "rebase on the in-flight paper-lifecycle diff")
and row #13 (`:77`, revision workflow). Decisions that apply: **D7** (CLI parity, `DECISIONS.md:25`), **D9–D11,
D23** (data protection; M-G2 adds no egress and no data value to any message), **D18** (blocking identity
verdicts go through the human-only `review-blocker` gate, `DECISIONS.md:65`; the verify gate inherits this
through M-G1's C005).

### Code references: what is on main and what is PL's planned code

- **`M:`** = current remote main **`2bcac1d514f6c2235071d8fe3ece19a47f83b7dc`** ("Merge pull request #25 from
  jedimik/dev/m-g", `git ls-remote` on 2026-11-02; M-G1 merged, `RUN/scratch/m-g/publish.json`
  `merged: true`, `pushed_head bee967d`). Read with `git show`/`git archive` (export
  `RUN/scratch/m-g2-plan-probe-r2`).
- **`PL:`** = PL's **planned** code: `M` + the frozen diff B (`handoffs/paper-lifecycle-20261031/
  framework-main-checkout.diff`, sha256 5ee2229b…) without its `config/agents.yml` hunk, applied in the
  export `RUN/scratch/m-g2-plan-probe-pl` with `patch -p1`. The resulting blob ids equal the postimage ids
  that PL's plan pins (section 3 of `reviews/pl-plan.md`): `schemas/status.yml` 3539d33, `status.py`
  3c171ad, `actions.py` a6afca1, `checkpoint.py` 6a37cd2, `init.py` a9e38bb, `validate.py` 74479b9,
  `cli.py` 49ef4e5, `docs/runs.md` 8863193. PL then adds only PL-D6 (`run checkpoint` wraps `ValueError`;
  it shifts `cli.py` lines after `:174` by a few lines), docs (`docs/cli.md`, `docs/web.md`, a
  `web/pages.py` comment) and new tests. **Every `PL:` anchor is not on main yet**; t0 refuses to start
  unless PL is merged and every anchor is present on the real base.
- **`G1:`** = M-G1 as merged (all on `M`).

PL's t0 (`reviews/pl-t0-baseline.md`) confirmed on base `2bcac1d`: all eight framework files equal diff B's
preimages, and M-G1 changed none of them (only `docs/cli.md` overlaps, a PL t2 file).

## 2. Base, dependencies and worktree

- **Dependencies** (`continuity/milestones.yml`: `{id: m-g2, after: [m-g, pl]}`). This run starts only after
  PL is merged; its t0 branches from main as it is then. t0 requires: (a) the earlier milestone shas of
  section 4.1 ancestors of `base`; (b) **M-G1 merged**: `RUN/scratch/m-g/publish.json` `merged: true`,
  `pushed_head` (`bee967d…`) an ancestor of `base`; (c) **PL merged**: `RUN/scratch/pl/publish.json`
  `merged: true`, its `pushed_head` an ancestor of `base`, and every anchor P1–P9 of section 4.2 present on
  `base`; (d) the M-G1 surface G-1..G-5 of section 4.3 present on `base`. Missing → `BASE_RED` with the
  reason; present but different in a way that breaks a design point of section 5 → `BASE_RED`
  "re-plan needed: <anchor> differs" (never improvise around it).
- **Lanes:** `parallel: any`. M-D3 (running) touches none of M-G2's files (checked: `git diff --name-only`
  of `RUN/scratch/m-d3-wt` against its merge base with `M` has no hit in `core/run/`, `schemas/status*`,
  `docs/runs.md`, `docs/cli.md`, `docs/research/{claims,revision,workflows/paper-draft}.md`, the two
  skills or the research `AGENTS.md`). The approved protected edits of M-H (research `AGENTS.md` routing
  paragraph, root skills table) and M-D3/M-I (root `AGENTS.md`) touch other regions than M-G2's rule-5
  edit. Overlap from a later merge is handled by the installer's refresh-before-merge.
- **Worktree:** t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`, the
  object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head
  git@github.com:jedimik/ScieFlow.git refs/heads/main`, the checks above, then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-g2 RUN/scratch/m-g2-wt <base>`.
- Commits local on `dev/m-g2`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output
  `RUN/scratch/m-g2/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs
  `~/.cache/scieflow-m-g2-<step>/` (outside RUN and `~/.hermes`).

## 3. Files: allowed edits, never edited, protected edit, complete list

### 3.1 The paper-lifecycle files (PL's; "in-flight" in the installer's standing rules)

The standing rule says these may be edited only as an approved plan explicitly allows. **This plan
allows exactly:**

| Path | Allowed change | Ticket |
|---|---|---|
| `schemas/status.yml` | one line under `workflows:` after `paper-draft` (`PL:schemas/status.yml:5`): `  paper-revision: [intake, structure, revise, respond, verify, resubmit]` | t1 |
| `src/scieflow/core/run/status.py` | one constant `PAPER_WORKFLOWS = ("paper-draft", "paper-revision")` after `phases_for` (`PL:status.py:43-50`); no logic change | t1 |
| `src/scieflow/core/run/init.py` | `PL:init.py:31` `== "paper-draft"` → `in status_mod.PAPER_WORKFLOWS` (keeping the workflow name); messages `:33`, `:38` name the workflow (byte-identical for paper-draft); `:61` passes the workflow; `:102-103` accept both paper workflows, refusal text "adoption supports only paper-draft and paper-revision" | t1 |
| `src/scieflow/core/run/checkpoint.py` | `PL:checkpoint.py:15-16, 35-36` membership in `PAPER_WORKFLOWS`; texts use the status's workflow name (byte-identical for paper-draft) | t1 |
| `src/scieflow/core/run/cli.py` | `init --workflow` choices (`PL:cli.py:46`) and `adopt --workflow` choices (`PL:cli.py:73`) gain `paper-revision`; the `init` docstring (`PL:cli.py:52`) says "Paper runs (paper-draft, paper-revision) require explicit --approval; budgets are opt-in." Nothing else (PL-D6's lines stay as PL wrote them) | t1 |
| `src/scieflow/core/run/actions.py` | `phase_gates` added to the existing import line (`M:actions.py:14`) and one call `phase_gates.check(ws, phase, state, actor)` as the first statement of `mark_phase` (`PL:actions.py:27`) | t2 |
| `src/scieflow/core/run/validate.py` | **no change** (validates through `phases_for`, `PL:validate.py:22-43`) | — |
| `docs/runs.md` | a new subsection after PL's "Native paper-draft lifecycle" (`PL:docs/runs.md:42-102`), and, in PL's PL-D6 sentence on which workflows the native commands know, `paper-revision` added | t3 |
| `config/agents.yml` | **never edited** | — |

### 3.2 Never edited

`config/agents.yml`, `config/sandbox.yml`, `config/defaults.yml`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`,
`tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, the root `AGENTS.md`, every `AGENTS.md` other
than the one protected edit of 3.3 (which the operator applies), `src/scieflow/core/events.py`,
`src/scieflow/core/gates.py`, `src/scieflow/core/service.py`, `src/scieflow/core/menu.py`,
`src/scieflow/core/workspace.py`, `src/scieflow/core/review/*`, `src/scieflow/core/data/*`,
`src/scieflow/web/*`, `src/scieflow/cli.py`, `schemas/gates.yml`, `src/scieflow/research/manuscript/**` (M-G1's
claims package included: it is called, never changed — M-G1 5.13), `src/scieflow/research/revision/*`
(G-3 below: the non-writing entry point already exists, so no keyword is added), `src/scieflow/_vendor/*`,
`pyproject.toml`, `uv.lock`, `src/scieflow/core/run/validate.py`, and every existing test file — PL's
`tests/core/test_paper_lifecycle.py`, `tests/core/test_paper_lifecycle_port.py`,
`tests/web/test_start_paper_draft.py`, `tests/test_docs_paper_lifecycle.py` included — except the one test
function of M-G2-D11. M-G2's tests are new files.

### 3.3 Protected edit (operator-applied before t3; listed in `pipelines/drafts/m-g2-protected-edits.json`)

File `src/scieflow/research/AGENTS.md`, rule 5 (`M:src/scieflow/research/AGENTS.md:109-110`; the old text
occurs exactly once on `M`, checked). Old:

```
5. Track progress in `workspace/<slug>/status.yml` and append notable events
   (dispatches, failures, retries, timings) to `workspace/<slug>/log.md`.
```

New (the old two lines unchanged, four lines added):

```
5. Track progress in `workspace/<slug>/status.yml` and append notable events
   (dispatches, failures, retries, timings) to `workspace/<slug>/log.md`.
   A `paper-draft` or `paper-revision` run has native status: create it with
   `uv run scieflow run init` or `run adopt`, change its phases only with `uv run
   scieflow run mark <slug> <phase> <state> --as-agent`, and never edit its
   `status.yml` by hand.
```

Why: the gates of section 5 sit in `run mark`; a coordinator that edits `status.yml` by hand bypasses
them (residual R3). The paper-draft skill says the same (t3), but the research `AGENTS.md` is what every
research coordinator reads first. t3 commits the operator-applied edit alone and pins the sentence in
its docs test. No other protected edit; the M-G1 rule 12 and routing text (`M:research/AGENTS.md:180-218`)
already route decision letters to the journal-revision skill, which M-G2 extends.

### 3.4 Complete file list (nothing else changes)

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-11-02-m-g2-claim-gate-paper-revision.md` | byte copy of this plan | t0 |
| `schemas/status.yml`, `schemas/status-research.yml` | the `paper-revision` row (5.1) | t1 |
| `src/scieflow/core/run/{status,init,checkpoint,cli}.py` | the rows of 3.1 | t1 |
| `tests/core/test_paper_revision_lifecycle.py` | new | t1 |
| `src/scieflow/core/run/phase_gates.py` | new (5.3–5.5) | t2 |
| `src/scieflow/core/run/actions.py` | import + one call | t2 |
| `tests/core/test_phase_gates.py` | new (seam, scope, fail closed, with the check stubbed) | t2 |
| `tests/research/manuscript/claims/test_verify_gate.py` | new (real check on M-G1's synthetic fixtures) | t2 |
| `tests/research/revision/test_respond_gate.py` | new (real coverage on M-G1's synthetic letter) | t2 |
| `tests/web/test_phase_gate_web.py` | new (API and run page) | t2 |
| `src/scieflow/research/AGENTS.md` | the protected edit of 3.3 (applied by the operator, committed by t3) | t3 |
| `docs/runs.md`, `docs/cli.md`, `docs/research/claims.md`, `docs/research/revision.md`, `docs/research/workflows/paper-draft.md` | docs (5.7) | t3 |
| `src/scieflow/research/skills/paper-draft/SKILL.md`, `src/scieflow/research/skills/journal-revision/SKILL.md` | skills (5.8) | t3 |
| `tests/test_docs_paper_revision.py` | new docs/skills test | t3 |
| `tests/test_docs_claims_revision.py` | exactly one test function replaced (M-G2-D11) | t3 |

**Hotspots (flagged):** `src/scieflow/core/run/{init,cli,checkpoint}.py` and `docs/runs.md` (PL just landed
them), `docs/cli.md` (shared with every milestone's docs ticket; M-G2 edits only the `run` section),
`src/scieflow/research/skills/paper-draft/SKILL.md`, `docs/research/{claims,revision}.md` (M-G1 wrote them),
`src/scieflow/research/AGENTS.md` (M-H has an approved protected edit in another paragraph).

## 4. Current code, verified

### 4.1 On main `M` (`2bcac1d`)

- Prerequisite ancestors of `M` (all `git merge-base --is-ancestor` exit 0 on this card): H-4 `6152d73`, M-A
  `be99db2`, M-E `9eeb4fa`, M-B `05d4d10`, M-C1 `aa7c379`, M-D1 `c350593`, M-D2 `5f051d0`, M-F `9634f28`,
  M-C2 `a59df13`, M-G1 `bee967d`.
- The single writer of phase states is `actions.mark_phase(ws, phase, state, actor="agent")`
  (`M:core/run/actions.py:27-31`: `_update_status` under the status lock, `:23-24`, then `events.emit`).
  Callers: CLI `run mark` (`M:core/run/cli.py:91-101`, `ValueError` → `ClickException`, exit 1); service
  `mark_phase` (`M:core/service.py:1349-1356`, `ValueError` → `ServiceError`); web API `POST
  /api/v1/runs/{slug}/phase` (`M:web/api.py:104-108`) and the run page `POST /runs/{slug}/act` with
  `action=phase` (`M:web/pages.py:761-770`), both through the service. `git grep` finds no other caller
  of `mark_phase` or `status.mark` in `src/` or `scripts/`. A `ServiceError` in the API becomes JSON
  `{"error": …}` with status 404 (`M:web/app.py:55-57`) — the existing mapping for every service refusal.
- `Project.discover(start)` → `config.repo_root(start)` (`M:core/project.py:59-61`; `M:core/config.py:16-27`:
  nearest ancestor holding `config/agents.yml`, only that path searched when `start` is given; missing →
  `FileNotFoundError`); `workspace_root = root/"workspace"` (`project.py:63-65`).
- `events.emit(ws, type_, actor, **data)` accepts any `note.<name>` (`M:core/events.py:52-56`); actors
  `human|agent|system` (`:19`).
- `service.create_run(…, workflow=…)` refuses a workflow not in `menu.WORKFLOWS` (`M:core/service.py:170-172`;
  `M:core/menu.py:49`, `paper-draft` at `:65-66`, no `paper-revision`).
- `workspace.describe` derives the kind from `workflow:` and the current phase from the status's own phase
  order (`M:core/workspace.py:97-106`, `:108-117`): a paper-revision run lists as kind `paper-revision`
  without code change. The menu's resume hint falls back to the research `AGENTS.md` for a kind not in
  `menu.WORKFLOWS` (`M:core/menu.py:249`; residual R6).
- `schemas/status-research.yml:3-7` lists `paper-draft` (`:6`) for the lenient research validator
  `validate_status_research` (`PL:validate.py:46-59`); no test pins its workflow list.
- Docs on `M`: `docs/research/claims.md:14-16` ("Status: the check is a command, not a lifecycle gate. The
  verify phase does not run it yet, and the `paper-revision` workflow is not wired (M-G2).") and
  `:195-196`; `docs/research/revision.md:183`; `docs/research/workflows/paper-draft.md:110-122` (Phase 6,
  step 3 the claim check); `docs/cli.md:148-157` (`run init` without `--workflow`; PL t2 rewrites it) and
  `:162-166` (`run mark`).
- The paper-draft skill on `M`: "Phases run in order; each is resumable via `status.yml`" (`:14-15`);
  Phase 1 step 1 creates the folder (`:19-21`), step 6 writes `status.yml` by hand (`:86-87`); Phase 3
  "mark that author `failed` in `status.yml`" (`:173`); Phase 4 "mark the round `incomplete` in
  `status.yml`" (`:281`) and "Update `status.yml` (`cross_review_rounds: …`)" (`:286-289`); Phase 6 verify
  `:324-357`, step 3 the claim check (`:339-344`, M-G1's text); Phase 7 handoff `:359-377`. No code reads
  `cross_review_rounds` or a per-author phase mapping (`git grep` in `src/`).
- Research `AGENTS.md` on `M`: rule 5 `:109-110`; rule 12 and the workflow table/routing (M-G1's protected
  edits) `:180-218`.

### 4.2 PL's planned code (anchors P1–P9; `PL:` lines from the diff-B probe; t0 re-verifies on base)

| Anchor | What M-G2 relies on | `PL:` |
|---|---|---|
| P1 | `schemas/status.yml` `workflows: {paper-draft: [intake, outline, draft, cross-review, merge, verify, handoff]}` | `schemas/status.yml:4-5` |
| P2 | `status.phases_for(workflow="research-loop", schema=None)`, unknown workflow → `ValueError("unknown workflow: …")`; `new_status(run, approval, workflow)` writes `workflow:`/`slug:` for non-loop runs; `mark`/`next_pending` use `phases_for(st.get("workflow", "research-loop"))`; `require_loop` | `core/run/status.py:43-50, 53-65, 84-98, 101-112` |
| P3 | `init_workspace`: `paper = overrides.get("workflow") == "paper-draft"`, explicit approval, budget all-or-none, `new_status(…, "paper-draft" if paper else "research-loop")` | `core/run/init.py:21-46, 49-71` |
| P4 | `adopt_workspace(slug, workspace_root, *, workflow, approval, goal_file=None, actor="human")`, refusing `workflow != "paper-draft"`; `note.run-adopted` carries `{workflow, approval}` | `core/run/init.py:93-103, 163-166` |
| P5 | `checkpoint`/`resume_info` special-case `st.get("workflow") == "paper-draft"` | `core/run/checkpoint.py:12-27, 30-38` |
| P6 | `run init --workflow [research-loop|paper-draft]`; `run adopt --workflow [paper-draft] --approval … [--goal] [--as-agent]`; `run mark` catches `ValueError` | `core/run/cli.py:42-68, 71-88, 114-124` |
| P7 | `mark_phase` emits without `iteration` when the status has none | `core/run/actions.py:27-34` |
| P8 | `validate_status` checks phases with `phases_for` and the identity key `slug` for non-loop runs | `core/run/validate.py:22-43` |
| P9 | PL's tests: `tests/core/test_paper_lifecycle.py` (`test_native_paper_phase_lifecycle` marks all seven paper-draft phases done in a run with **no** `manuscript/`, `:70-92`; its `paper_project` fixture copies only `status`, `status-research`, `gates` schemas, `:25-38`), `tests/core/test_paper_lifecycle_port.py`, `tests/web/test_start_paper_draft.py`, `tests/test_docs_paper_lifecycle.py`; `docs/runs.md` "### Native paper-draft lifecycle" (`PL:docs/runs.md:42`) | — |

### 4.3 M-G1 surface (G-1..G-5; all on `M`)

- **G-1** `claims.compile.check_run(project, run_folder, main="main.tex") -> CheckResult`
  (`M:research/manuscript/claims/compile.py:550-553`): builds the ledger in memory and checks it; writes
  nothing. `CheckResult` (`:474-501`): `findings`, `waived`, `errors` (property, severity `error`, `:483`),
  `warnings`, `status_unreadable: bool` (`:480`, set when the review blockers or the evidence bundle cannot
  be read, `:504-541`). Human waivers are already applied (`_apply_waivers`, `:448`). It raises
  `ledger.ClaimsError(rule, message)` (`M:claims/ledger.py:35-41`, messages name no value) when the ledger
  cannot be built (for example no `main.tex`). It runs one local `git merge-base` (code drift, `:387`),
  nothing else external.
- **G-2** The package refusal (`M:research/manuscript/package.py:147-161`): `ClaimsError` → refused with its
  message; any other exception → refused "(fail closed)"; `result.status_unreadable` → refused;
  `result.errors` → refused "N claim error(s): run scieflow manuscript claims check <run>". The gate uses
  the same predicate (it does not call the private `_refuse_claims`).
- **G-3** Revision coverage: `coverage.evaluate(run, round_) -> {version, round, ok, comments, problems}`
  computes **without writing** (`M:research/revision/coverage.py:214-238`); `write` stores `coverage.json`
  and emits `note.revision_coverage` (`:241-250`). So M-G2 needs **no** change in the revision package (the
  first plan's optional `write=` keyword is dropped). Rounds: `letter.existing_rounds(run)` → sorted round
  numbers of `revision/round-<N>/` folders, refusing a linked `revision/` or round folder
  (`M:research/revision/letter.py:218-233`); `LetterError(rule, message)` (`:40-46`, messages carry no letter
  text). `evaluate` raises `LetterError` for a missing/linked/invalid `adopted.json` or `response.md`
  (`M:research/revision/respond.py:26-47, 95-104`).
- **G-4** `claims check <run>` needs a built ledger (`check_ledger` refuses `ledger-missing`/`ledger-stale`,
  `M:claims/compile.py:556-575`; CLI `M:research/manuscript/cli.py:261-295`). The gate's refusal therefore
  names `claims build` and then `claims check`.
- **G-5** M-G1's docs test pins the "not yet wired" statement:
  `M:tests/test_docs_claims_revision.py:184-187` `test_verify_gate_is_documented_as_not_yet_wired` (asserts
  "verify" and "M-G2" in `docs/research/claims.md`). M-G1's other docs pins that M-G2's t3 must keep:
  `:247-268` (journal-revision skill phrases, "**Never waive", "Never ingest a letter or adopt a draft",
  "Never edit `report/claims/`"), `:278-283` (paper-draft skill: "scieflow manuscript claims build",
  "scieflow manuscript claims check", "claim errors", no "3-5 numeric"), `:293-301` (rule 12, workflow row,
  routing and root row byte-exact).
- Test fixtures to reuse (not change): `synth` (`M:tests/research/manuscript/conftest.py:255-270`: a
  temporary project with all `schemas/`, a stub `config/agents.yml`, run `workspace/ms-run` with a
  manuscript, `monkeypatch.chdir`), re-exported for the revision tests
  (`M:tests/research/revision/conftest.py:3`); `t3_support.results_run`/`body`
  (`M:tests/research/manuscript/claims/t3_support.py:24-34`, a data package with marker values `0.9134`,
  `7341.25`); `t6_support.adopted_round`, `respond`, `fill_all`, `change_methods`
  (`M:tests/research/revision/t6_support.py:25-57`); web `project`/`client`
  (`M:tests/web/conftest.py:33-92`).

### 4.4 Paths into `mark_phase` that M-G2 must keep working

- Every existing caller marks phases of research-loop runs or of PL's paper-draft lifecycle: on `M`,
  `tests/core/test_run_actions.py:19-41`, `tests/core/test_service.py:168-189`,
  `tests/web/test_mutations.py:53-56`, `tests/test_service_e2e.py:45-56` (all loop runs); PL's
  `test_native_paper_phase_lifecycle` (paper-draft `verify done` with no `manuscript/`) and
  `test_legacy_paper_status_without_iteration_can_be_marked` (`intake` only). No existing test marks
  `verify done` in a run that has a manuscript, and no test marks `respond`. t0 re-checks this on base,
  PL's tests included.

### 4.5 Planning probes (evidence, not acceptance)

All memory-capped, `PYTHONPATH=<export>/src:<export>:<export>/scripts`, basetemp under
`~/.cache/scieflow-m-g2-plan/`, in the throwaway export `RUN/scratch/m-g2-plan-probe-pl` (= `M` + diff B
without `config/agents.yml` + the frozen test + a ~90-line sketch of section 5.3–5.5 + the paper-revision
schema row). The sketch is not the implementation and is not handed to t2.

1. Verify gate on M-G1's fixtures: a clean results sentence → `mark_phase(ws, "verify", "done")` passes, one
   `note.phase_gate {outcome: passed}` then `phase.done`; the same sentence with a wrong number → refused
   "verify refused: 1 claim error(s); run `scieflow manuscript claims build ms-run` then `scieflow
   manuscript claims check ms-run`", `status.yml` byte-identical, no marker value in any event. The default
   `synth` article has 0 errors and 3 `doi-missing` warnings (warnings never refuse).
2. Respond gate on M-G1's letter fixture (paper-revision status): no `revision/` → refused; adopted round 1
   with the unfilled starter → refused "round 1 coverage has 10 problem(s)"; after `fill_all` and
   `change_methods` → passes; `coverage.json` absent before and after (nothing written).
   Log: `RUN/scratch/m-g2/plan-probe-gates.log` (3 passed, exit 0).
3. With the gate wired and the schema row added: `tests/core/test_paper_lifecycle.py` (frozen, not adapted;
   its two dispatch tests need PL's credential adaptation and were deselected with `-k "not dispatch"`),
   `test_run_actions`, `test_service`, `web/test_mutations`, `test_status`, `test_checkpoint`,
   `test_create_run`, `test_service_e2e`, `tests/research/manuscript/claims`, `tests/research/revision`,
   `test_docs_claims_revision`: **818 passed, 27 deselected, exit 0**. Log:
   `RUN/scratch/m-g2/plan-probe-focused.log`.

## 5. Design (the spec part)

### 5.1 paper-revision vocabulary

`schemas/status.yml` `workflows:` gains `paper-revision: [intake, structure, revise, respond, verify,
resubmit]` after `paper-draft`; `schemas/status-research.yml` `workflows:` gains the same row after
`paper-draft` (M-G2-D12). `status.PAPER_WORKFLOWS = ("paper-draft", "paper-revision")`. Everything that
reads phases (`mark`, `next_pending`, `validate_status`, `run show`, `workspace.describe`) already goes
through `phases_for` or the status's own phase order (P2, P8, 4.1) and needs no change.

| Phase | What happens (journal-revision skill, 5.8) | Gate on `done` |
|---|---|---|
| `intake` | the user brings in the manuscript as submitted (`manuscript/`), its evidence (a data package or `scieflow data import` of a release; never another run's `experiments/` or `inputs/`), and stores the letter with `revision ingest` (the user's) | — |
| `structure` | `revision prompt` (`--role research.submitter`), sub-agent draft, coordinator `revision adopt` | — |
| `revise` | `revision respond` writes the starter; the authors change the manuscript | — |
| `respond` | the authors answer every comment in `response.md`; `revision coverage` | coverage (5.5) |
| `verify` | `manuscript build`, `claims build`, `claims check` | claims (5.4) |
| `resubmit` | `revision letter`, `manuscript package` (refuses on claim errors itself) | — |

### 5.2 Creation, adoption, checkpoint texts

- `init_workspace`: `workflow = overrides.get("workflow")`; `paper = workflow in status_mod.PAPER_WORKFLOWS`;
  the rest of PL's paper branch unchanged; `new_status(ws.name, cfg["approval"], workflow if paper else
  "research-loop")`; messages `f"{workflow} initialization requires explicit --approval"` and
  `f"supply all three budget limits, or none, for {workflow}"` (byte-identical for paper-draft).
- `adopt_workspace`: `if workflow not in status_mod.PAPER_WORKFLOWS: raise ValueError("adoption supports
  only paper-draft and paper-revision")`; everything else unchanged (`note.run-adopted` already carries the
  workflow).
- `checkpoint`: `if st.get("workflow") in PAPER_WORKFLOWS: resume = f"Resume: read status.yml;
  {st['workflow']} phase '{pending or 'complete'}'."` (+ PL's budget sentence); `resume_info` likewise
  `f"Run active: {st['workflow']}, next phase '{pending or 'complete'}'."`.
- CLI choices: `run init --workflow [research-loop|paper-draft|paper-revision]`, `run adopt --workflow
  [paper-draft|paper-revision]`; the `init` docstring of 3.1.
- `service.create_run(workflow="paper-revision")` stays refused ("unknown workflow", `M:service.py:170-172`):
  not on the menu or the Start page (M-G2-D9).

### 5.3 The gate seam (`src/scieflow/core/run/phase_gates.py`)

```
class PhaseGateRefused(ValueError)            # CLI exit 1; ServiceError in the service and web
def check(ws, phase, state, actor) -> None    # first statement of actions.mark_phase
```

`check` returns at once unless `state == "done"`. It reads `status.read_status(ws)`; a missing or
unreadable status returns without doing anything (the existing `mark_phase` path then fails exactly as
today, writing nothing). With `workflow = st.get("workflow", "research-loop")`:

| workflow | phase | gate |
|---|---|---|
| `paper-draft`, `paper-revision` | `verify` | claims (5.4) |
| `paper-revision` | `respond` | coverage (5.5) |
| anything else | any | none — no further I/O |

`phase` names are not validated here (`status.mark` does it afterwards, unchanged). Each gate run emits
exactly one `note.phase_gate` event with the marking `actor` before it returns or raises (5.6); on a
refusal nothing else is written and `status.yml` is byte-identical. Research modules are imported lazily
inside the gate with `importlib.import_module("scieflow.research.manuscript.claims.compile")` and
`importlib.import_module("scieflow.research.revision.coverage")` / `…revision.letter` (so an `ImportError`,
including a `None` entry in `sys.modules`, is caught and refuses), following the core-imports-research-lazily
precedent (`M:core/review/evidence.py:510, 557`). The project for `check_run` is `Project.discover(Path(ws))`
(4.1); a `FileNotFoundError` refuses (fail closed). Refusal helpers are typed `-> NoReturn`.

Messages name the run slug, the phase, counts, a rule id and the command to run; never a manuscript
excerpt, an evidence value, a letter quote or a blocker detail.

### 5.4 Claims gate (verify)

1. `man = ws / "manuscript"`. A symlink at `man`, or something that is not a directory → refused
   `"verify refused: manuscript must be a folder inside the run, not a link or a file"`, outcome `refused`.
2. Absent: in a `paper-draft` run → outcome `no-manuscript`, the gate passes (M-G2-D5); in a
   `paper-revision` run → refused `"verify refused: a paper-revision run needs its manuscript/ folder"`,
   outcome `refused`.
3. Import the claims module (5.3); `ImportError` → refused `"verify refused: the claim check could not run
   (ImportError); fail closed"`, outcome `failed-closed`.
4. `result = check_run(Project.discover(ws), ws)`.
   - `ClaimsError` → refused `f"verify refused: claims [{exc.rule}]: {exc.message}"`, outcome `refused`, the
     event carries `rule`.
   - any other exception → refused `f"verify refused: the claim check could not run ({type(exc).__name__});
     fail closed"`, outcome `failed-closed`.
5. `result.status_unreadable` → refused `"verify refused: the review status could not be read; fail
   closed"`, outcome `refused`.
6. `result.errors` → refused `f"verify refused: {n} claim error(s); run `scieflow manuscript claims build
   {slug}` then `scieflow manuscript claims check {slug}`"`, outcome `refused`.
7. Otherwise outcome `passed` (warnings never refuse; waived findings are not errors because `check_run`
   applies the human waivers; C005 clears exactly when the `review-blocker` gate does, M-G1 5.6, D18).

The gate writes nothing under `report/claims/` and emits no `note.claims_checked` (that event belongs to
`claims check`, `M:claims/report.py:70-73`).

### 5.5 Coverage gate (paper-revision respond)

1. Import the revision modules (5.3); `ImportError` → refused, outcome `failed-closed`.
2. `rounds = letter.existing_rounds(ws)` (refuses a linked `revision/` or round folder with
   `LetterError("symlink", …)`). None → refused `f"respond refused: no decision letter ingested; the user
   runs `scieflow manuscript revision ingest {slug} --letter FILE`"`, outcome `refused`.
3. `N = max(rounds)`; `doc = coverage.evaluate(ws, N)` (writes nothing).
   - `LetterError` (from step 2 or 3) → refused `f"respond refused: [{exc.rule}] {exc.message}"`, outcome
     `refused`, the event carries `rule` (and `round` when known).
   - any other exception → refused `f"respond refused: the coverage check could not run
     ({type(exc).__name__}); fail closed"`, outcome `failed-closed`.
4. `doc["ok"]` false → refused `f"respond refused: round {N} coverage has {k} problem(s); run `scieflow
   manuscript revision coverage {slug} --round {N}`"` (k = error-level problems), outcome `refused`.
   Otherwise outcome `passed`. The event carries `round`.

The gate writes no `coverage.json` and emits no `note.revision_coverage` (those belong to `revision
coverage`, G-3).

### 5.6 The event

`note.phase_gate` with the marking actor (`human` from the CLI without `--as-agent` and from the service's
default, `agent` with `--as-agent`) and data `{phase, workflow, gate: "claims"|"coverage", outcome:
"passed"|"refused"|"failed-closed"|"no-manuscript", errors: int, warnings: int}` plus `round` (coverage,
when known) and `rule` (a `ClaimsError`/`LetterError` rule id). Counts and ids only. No new event type
(`events.emit` accepts `note.*`), no new gate kind, `schemas/gates.yml` untouched.

### 5.7 Docs

- `docs/runs.md`, new subsection "### Paper-revision runs and phase gates" right after PL's "Native
  paper-draft lifecycle": the six phases in order (5.1 table), `run init … --workflow paper-revision` and
  `run adopt … --workflow paper-revision` (explicit approval, opt-in budget, the same as paper-draft), the
  verify gate (both paper workflows) and the respond gate with their refusal messages, "fail closed", the
  `no-manuscript` rule for paper-draft only, no override and the human paths (waivers, the `review-blocker`
  gate, `data release`), the `note.phase_gate` event, that the browser marks phases with the same gates but
  cannot create a paper-revision run. PL's PL-D6 sentence (which workflows the native commands know) gains
  `paper-revision`.
- `docs/cli.md` (`run` section only): the `run init` and `run adopt` synopses list `paper-revision` among the
  `--workflow` choices; the `run mark` entry gains one sentence: in a paper run `verify done` (and `respond
  done` in a paper-revision run) is refused until the claim check (the revision coverage) passes, with a
  link to the new `runs.md` subsection.
- `docs/research/claims.md`: the status paragraph (`:14-16`) says the verify phase of a paper run now runs
  the check through `run mark <slug> verify done` (and `package` refuses as before); the residual bullet
  (`:195-196`) is replaced by the gate's own residuals (R1 no-manuscript paper drafts, R2 check-then-write
  window, R3 hand-edited status).
- `docs/research/revision.md`: the bullet `:183` is replaced by a pointer to the new section "## The
  paper-revision workflow" (the phase table of 5.1, how to start one, the respond and verify gates, the
  evidence rule of M-G-D15, revising in place in the draft run as the alternative without phase tracking).
- `docs/research/workflows/paper-draft.md`: Phase 1 says the run is created with `run init --workflow
  paper-draft` or `run adopt`; Phase 6 step 3 adds that `run mark <slug> verify done` is refused while the
  claim check has an error.

### 5.8 Skills

- `paper-draft/SKILL.md`:
  - header (`:14-15`): phases are native — `uv run scieflow run show <slug>` on entry; mark each phase
    `running` when it starts and `done` (or `failed`) when it ends with `uv run scieflow run mark <slug>
    <phase> <state> --as-agent`; never edit `status.yml` by hand.
  - Phase 1 step 1: create the run first — `uv run scieflow run init <slug> --workflow paper-draft --goal
    <file> --approval <mode>` when the user gave a goal file, otherwise create `workspace/<slug>/` with
    `goal.md` (the user's request in their words) and run `uv run scieflow run adopt <slug> --workflow
    paper-draft --approval <mode> --as-agent`; the approval mode is the user's (ask, never default); then
    create the intake subfolders. Before the run configuration gate (step 3), because `agent run` refuses a
    folder with `config.yml` but no `status.yml`.
  - Phase 1 step 6: replaced by "the status was created in step 1; do not write it".
  - Phase 3 `:173`, Phase 4 `:281`, `:286-289`: per-author failures, incomplete rounds and per-round
    recommendations go to `log.md` and `uv run scieflow run log <slug> note.<name> --data KEY=VALUE …`
    (`note.draft_author_failed`, `note.cross_review_round`), not into `status.yml`.
  - Phase 6, after step 3: "then `uv run scieflow run mark <slug> verify done --as-agent`; it refuses while
    the claim check has errors — fix them (one fix round per step 3), never edit `report/claims/` or
    `status.yml`, never waive".
  - Keep every phrase M-G1's docs test pins (G-5).
- `journal-revision/SKILL.md`: new section "## Paper-revision workflow" before "## Report to the user":
  when to start one (a decision letter for a manuscript that was submitted, when the user wants phase
  tracking; otherwise revise in place in the draft run with Phases A–D); `run init <slug> --workflow
  paper-revision --goal <file> --approval <mode>` or `run adopt`; the phase table with M-G1's commands;
  the user brings in the manuscript as submitted and its data (a data package or `scieflow data import`
  of a release; never copying another run's `experiments/` or `inputs/`); `revision ingest` is the user's;
  `run mark <slug> respond done --as-agent` and `verify done` are refused until coverage and the claim check
  pass; `resubmit` = `revision letter` + `manuscript package`. Hard rules unchanged.

### 5.9 Decisions (Rule D)

- **M-G2-D1 One gate seam** in `actions.mark_phase` (5.3); every caller goes through it (4.1).
- **M-G2-D2 The verify gate** reuses M-G1's `check_run` and the package's refusal predicate (G-2); it writes
  nothing.
- **M-G2-D3 paper-revision phases** `[intake, structure, revise, respond, verify, resubmit]` (round-1 design
  10.2 had `respond` before `revise`; reordered because coverage requires a `Change:` file that really
  changed, `M:coverage.py:165-187`).
- **M-G2-D4 The respond gate** checks the highest existing round with `coverage.evaluate` (G-3); no round →
  refused.
- **M-G2-D5 A paper-draft run without a `manuscript/` folder passes verify** (`no-manuscript`), because PL's
  `test_native_paper_phase_lifecycle` must stay green unchanged (P9). A paper-revision run without one is
  refused (no compatibility reason to allow it). Residual R1.
- **M-G2-D6 No override flag.** The human paths are M-G1's (waivers, the `review-blocker` gate, data
  release).
- **M-G2-D7 One event `note.phase_gate`, counts only** (5.6).
- **M-G2-D8 Creation** of a paper-revision run uses PL's `run init`/`run adopt` unchanged in behaviour.
  ScieFlow copies nothing from another run (M-G-D15).
- **M-G2-D9 Not on the menu or the Start page** (`menu.py`, `service.py` untouched); follow-up F1.
- **M-G2-D10 One protected edit** (3.3).
- **M-G2-D11 One existing test function changes, by design.** `test_verify_gate_is_documented_as_not_yet_wired`
  (`M:tests/test_docs_claims_revision.py:184-187`) pins a statement M-G2 makes false. t3 replaces that one
  function with `test_verify_gate_is_documented` (asserts "run mark", "verify done" and "fail closed" in
  `docs/research/claims.md` and "not wired" absent). No other existing test line changes.
- **M-G2-D12 `schemas/status-research.yml` gains `paper-revision`** with the same phases (the lenient
  validator accepts paper-revision statuses as it accepts paper-draft ones).
- **M-G2-D13 The gate runs before the status lock**; residual R2.
- **M-G2-D14 No change to M-G1 code.** `coverage.evaluate` already exists (G-3); the first plan's
  `write=` keyword is dropped.
- **M-G2-D15 The paper-draft skill uses the native lifecycle** (5.8; PL-D8 assigned this to M-G2): run
  creation by `run init`/`run adopt`, phases by `run mark`, per-author and per-round notes by `run log`.

### 5.10 Security and integrity (checked in every review)

| Property | How |
|---|---|
| Fail closed | linked or odd `manuscript/`/`revision/` paths, import errors, project not found, any exception in the check or the coverage → refused |
| Status untouched on refusal | the gate raises before `_update_status`; tests compare `status.yml` bytes and that `events.jsonl` gained exactly one `note.phase_gate` line |
| No data in messages or events | counts, rule ids, slug, round; tests search every message and event for the fixtures' marker values (`0.9134`, `7341.25`), a reviewer quote of the letter fixture and the retracted reference title |
| No egress, no new dependency | local check only (M-G1's check runs one local `git merge-base`) |
| Research-loop runs and other phases unchanged | the gate returns before any I/O beyond reading the status; existing tests green unchanged |
| Human paths only | no `--force`; waivers, the `review-blocker` gate and data release stay the human's |
| No cross-run reads | the gate reads only the run's own folder and the project's review state for that run |
| Agent instructions | skills and the rule-5 sentence never tell an agent to edit `status.yml`, `report/claims/`, or to waive |

### 5.11 Not in M-G2 (stated)

No menu or Start-page entry for paper-revision (F1); no gate on `handoff`/`resubmit` (the package step
refuses on claim errors itself; F3); no `structure` gate (coverage implies adoption); no automatic copying
of a draft run into a revision run (F2); no change to M-G1's packages; no new agent role (`research.submitter`
is M-G1's); no change to how phases look in the web app.

## 6. Tickets (implementation plan)

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is
a checkpointed pause, no model swap. Every card: no claude/codex/agy, no live probe, no network (git
ls-remote/fetch in t0 only), no push/PR, no credentials, no patient data (synthetic fixtures only); never
edits a file of 3.2; edits the paper-lifecycle files only as 3.1 allows; never writes any `AGENTS.md` (t3
only commits the operator-applied edit of 3.3).

**SCOPE guard** (every implementation, fix, branch and final card; report `scope_guard_exit`, must be 0):
`git -C WT diff --name-only <base>..HEAD` ⊆ the complete file list of 3.4, and
`git -C WT diff --exit-code <base> HEAD -- config/agents.yml config/sandbox.yml CLAUDE.md AGENTS.md
src/scieflow/core/run/validate.py src/scieflow/core/service.py src/scieflow/core/menu.py src/scieflow/web
src/scieflow/research/manuscript src/scieflow/research/revision tests/probes/k_task8_binding.py
tests/probes/test_k_task8_binding.py tests/fixtures/k_task8_stream_fixture.py` exits 0. Before t3 the
research `AGENTS.md` must also be unchanged; from t3 on, its diff must equal exactly the 3.3 edit.

### Test commands (all memory-capped)

- RED/GREEN while editing (dirty tree), from WT = `RUN/scratch/m-g2-wt`:
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-g2-<step>/pt <paths>`.
  Once per card first: `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'` must print a path under `WT/src/`.
- Committed checkpoint (clean tree): `G6_OUT_DIR=RUN/scratch/m-g2/junit G6_CACHE_DIR=~/.cache/scieflow-m-g2 RUN/tools/g6_pytest.sh WT <head-sha> m-g2-<step> -- -m 'not slow and not live' <paths>`.
- Full default suite: the same runner with `tests`, under the shared lock (`flock -w 7200
  /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with completion notification
  and a log file. Sealed probes: `… g6_pytest.sh WT <head> m-g2-sealed -- tests/probes` and
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- Load-flake nodes: those of the installer's standing rules, one rerun, both results recorded. No
  rerun-until-green; never skip or deselect a test to get green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-g2/executions.jsonl` and states RED and GREEN evidence in its result.
- Focused sets (clean-tree runner on the ticket's final commit; t0 confirms each path exists on base, a
  missing one is recorded and dropped, never invented):
  - t1: `tests/core/test_paper_revision_lifecycle.py tests/core/test_paper_lifecycle.py
    tests/core/test_paper_lifecycle_port.py tests/core/test_run_actions.py tests/core/test_create_run.py
    tests/core/test_service.py tests/test_status.py tests/test_validate.py tests/test_checkpoint.py
    tests/test_sfx_init.py tests/web/test_start_paper_draft.py`
  - t2: `tests/core/test_phase_gates.py tests/research/manuscript/claims/test_verify_gate.py
    tests/research/revision/test_respond_gate.py tests/web/test_phase_gate_web.py
    tests/core/test_paper_revision_lifecycle.py tests/core/test_paper_lifecycle.py
    tests/core/test_run_actions.py tests/core/test_service.py tests/web/test_mutations.py
    tests/web/test_run_page_actions.py tests/test_service_e2e.py tests/research/manuscript/claims
    tests/research/revision`
  - t3: `tests/test_docs_paper_revision.py tests/test_docs_claims_revision.py
    tests/test_docs_paper_lifecycle.py tests/test_docs_journal.py tests/test_docs_role.py
    tests/research/test_e2e_paper_draft.py`
- Test projects: the `paper_project` pattern (a temporary folder with `config/agents.yml`,
  `config/defaults.yml`, copies of `schemas/*.yml`, `monkeypatch.chdir`, `status._VOCAB` reset), defined anew
  in each new core test file; the claims and revision integration tests use M-G1's `synth` and its
  helpers (4.3), writing the paper status with `status.new_status(…)` after resetting `status._VOCAB`.

### t0 — base, dependencies, worktree, baseline, plan copy (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the ancestor checks (4.1 list); M-G1 and PL
   `publish.json` `merged: true` with `pushed_head` ancestors of base; anchors P1–P9 (4.2) and G-1..G-5 (4.3)
   on base. Missing → `BASE_RED` ("PL not merged" / "M-G1 not merged" / "<anchor> missing"); present but
   different in a way that breaks a section-5 design point → `BASE_RED` "re-plan needed". Then
   `worktree add -b dev/m-g2 RUN/scratch/m-g2-wt <base>` (retry: reuse at base or base + own docs commit,
   never reset).
2. Green-base gate on base (clean): full default suite **0 failed, 0 errors**; sealed probes 0 failed;
   load-flake rule only; otherwise `BASE_RED` with node ids and junit paths.
3. Write `reviews/m-g2-t0-baseline.md`: every `M:`/`PL:` cite of section 4 on base (present / moved with the
   new line / changed); P1–P9 with real lines, including the exact PL-D6 sentence in `docs/runs.md` and
   PL's `run init`/`run adopt` text in `docs/cli.md`; G-1..G-5 as found; every test on base that marks a
   paper-workflow phase done (4.4) and whether its run has a `manuscript/`; any test pinning the `run
   init`/`run adopt` help text, the adopt refusal message or the paper checkpoint texts; that the 3.3 old
   text occurs exactly once in `src/scieflow/research/AGENTS.md`.
4. Copy `reviews/m-g2-plan.md` byte for byte to
   `docs/superpowers/plans/2026-11-02-m-g2-claim-gate-paper-revision.md`; commit it alone ("docs(plans): M-G2
   claim gate and paper-revision workflow plan (M-G2 t0)").
Verdicts `DONE` | `BASE_RED`.

### t1 — paper-revision vocabulary, init, adopt, checkpoint (Haiku) → t1r (Sonnet) → t1f

Files: `schemas/status.yml`, `schemas/status-research.yml`, `src/scieflow/core/run/{status,init,checkpoint,cli}.py`
(only 3.1's rows), `tests/core/test_paper_revision_lifecycle.py`.

RED commit first (the new test file, failing for the stated reasons), then GREEN commit(s). RED → GREEN:
1. `run init rev --workflow paper-revision --goal g.md --approval per-campaign`: status `workflow:
   paper-revision`, `slug: rev`, no `run:`, the six phases pending in order, `validate_status` `[]` and
   `validate_status_research` `[]`; no `budget.yml`, `iterations/`, `notebook.md`; config `{workflow,
   approval}`; one `run.created`. Without `--approval` → refused naming `paper-revision`; a partial budget →
   refused naming `paper-revision`; all three limits → budget written.
2. `run adopt prepared --workflow paper-revision --approval per-campaign --as-agent`: every prepared file
   byte-identical, `note.run-adopted` with `workflow: paper-revision`; `--workflow research-loop` → click
   usage error (exit 2); an existing status → refused; `adopt_workspace(…, workflow="lit-review")` →
   "adoption supports only paper-draft and paper-revision".
3. Phases: each of the six marked `running`/`failed` and `intake`, `structure`, `revise`, `resubmit` marked
   `done` (the gated `respond`/`verify` `done` are t2's); a paper-draft or loop phase name → refused,
   status bytes unchanged; `run advance` refused (`require_loop`); `checkpoint` text "paper-revision phase
   '<next>'", `resume_info` "Run active: paper-revision, next phase …"; the budget sentence only with a
   budget.
4. Compatibility: paper-draft messages and texts byte-identical (compare with the literal strings PL's
   tests and code use); research loop unchanged; `service.create_run(…, workflow="paper-revision")` refused
   "unknown workflow" (D9); PL's tests green unchanged.
5. `status.PAPER_WORKFLOWS == ("paper-draft", "paper-revision")`, equal to the non-loop keys of
   `schemas/status.yml` `workflows:`; `schemas/status-research.yml` `workflows["paper-revision"]` equals
   `status.phases_for("paper-revision")`.

t1r checklist: only 3.1's lines changed (diff of each file read line by line); paper-draft texts
byte-identical except the adopt refusal; `validate.py` untouched; no gate logic; RED reproduced (the test
file on base fails for the stated reasons); focused set re-run; SCOPE guard 0.

### t2 — phase gates: verify claims gate and respond coverage gate (Haiku) → t2r (Sonnet) → t2f

Files: `src/scieflow/core/run/phase_gates.py`, `src/scieflow/core/run/actions.py` (import + one call), and the
four new test files of 3.4. RED commit first (all four test files), then GREEN.

RED → GREEN:
1. **Seam** (`tests/core/test_phase_gates.py`, `check_run`/`evaluate` stubbed with monkeypatch): `mark_phase`
   calls `phase_gates.check` before writing (a refusing stub leaves `status.yml` byte-identical); the table of
   5.3 exactly — `verify running|failed|pending` never call the check (call counter 0); every other paper
   phase marked done → no call; a research-loop run → no call and no `note.phase_gate`; a status without
   `workflow` → no call; an unreadable status → no call and the old error.
2. **Fail closed** (same file): `check_run` raising `RuntimeError` → refused `failed-closed`;
   `sys.modules["scieflow.research.manuscript.claims.compile"] = None` → refused `failed-closed`; the same
   for the revision modules on `respond`; `Project.discover` raising `FileNotFoundError` → refused;
   `manuscript` a symlink (to a valid folder) → refused; `manuscript` a file → refused; `revision` a symlink →
   refused; a stubbed result with `status_unreadable=True` and no errors → refused.
3. **Verify gate, real check** (`tests/research/manuscript/claims/test_verify_gate.py`, `synth` +
   `t3_support.results_run`): paper-draft clean sentence → `run mark ms-run verify done --as-agent` exit 0,
   phase done, one `note.phase_gate {gate: claims, outcome: passed, errors: 0}` with actor `agent`; a planted
   wrong number (C002) → exit 1, output contains "1 claim error", "scieflow manuscript claims build ms-run"
   and "scieflow manuscript claims check ms-run", `status.yml` byte-identical, events gained only the refused
   `note.phase_gate`; after fixing the number → done. A user waiver of that C002 (M-G1 `claims waive` as the
   human, outside a dispatch) → passes. A C005 blocked reference (M-G1 `test_blocked_reference` fixture
   pattern) → refused; review-blocker state unreadable (M-G1 t3 pattern) → refused. No `main.tex` →
   refused with the `ClaimsError` rule. No `manuscript/` in paper-draft → passes, outcome `no-manuscript`; in
   paper-revision → refused. The same pass/refuse pair for a paper-revision run. Nothing new under
   `report/claims/`.
4. **Respond gate, real coverage** (`tests/research/revision/test_respond_gate.py`, `synth` + `t6_support`,
   paper-revision status): no `revision/` → refused naming `revision ingest`; round 1 adopted, starter
   unfilled → refused naming round 1, the problem count and `revision coverage ms-run --round 1`; filled and
   changed → passes, event `round: 1`; two rounds (round 1 ok, round 2 ingested but not adopted) → refused
   for round 2 with `[adopted-missing]`; `coverage.json` and `note.revision_coverage` absent before and after
   every gate run. A paper-draft run has no `respond` phase (`status.mark` refuses it, unchanged).
5. **Service and web** (`tests/web/test_phase_gate_web.py`, web `project`/`client` + a paper-draft run with a
   manuscript, `check_run` stubbed to one error): `service.mark_phase` → `ServiceError` with the message;
   `POST /api/v1/runs/<slug>/phase` → 404 JSON whose `error` holds the message (the existing ServiceError
   mapping, 4.1), status unchanged; `POST /runs/<slug>/act` (`action=phase`) → the page's error path,
   status unchanged.
6. **No data leak**: every message and every `note.phase_gate` of tests 3 and 4 is searched for the marker
   values (`0.9134`, `7341.25`), the planted wrong value, a reviewer quote of M-G1's letter fixture and the
   retracted reference's title; none appears.

t2r checklist: one call in `mark_phase` before `_update_status` and nothing else in `actions.py`; gate table
exactly 5.3; 5.4/5.5 step by step (order of checks, messages, outcomes); fail closed everywhere; D5 only for
an absent folder in paper-draft; `importlib` lazy imports; no write besides the one event; messages and
events without data; M-G1 code unchanged; existing tests unchanged; RED reproduced; focused set re-run;
SCOPE guard 0.

### t3 — docs, skills and the protected edit (Haiku) → t3r (Sonnet) → t3f

Before t3 the operator applies the 3.3 edit to WT (`pe_t3` step). t3 first checks that
`git -C WT diff -- src/scieflow/research/AGENTS.md` is exactly that edit and commits it alone ("docs(agents):
research rule 5, paper runs change phases only through run mark (human-approved protected edit) (M-G2
t3)"). Then the RED commit of `tests/test_docs_paper_revision.py` plus the M-G2-D11 replacement in
`tests/test_docs_claims_revision.py`, then the docs and skills commits.

RED → GREEN:
1. `docs/runs.md` has "### Paper-revision runs and phase gates" after "### Native paper-draft lifecycle";
   it names the six phases in the order of `status.phases_for("paper-revision")`, `run init … --workflow
   paper-revision`, `run adopt … --workflow paper-revision`, both gates with the exact refusal message stems
   of 5.4/5.5 ("verify refused:", "respond refused:"), "fail closed", the `no-manuscript` rule, the human
   paths and `note.phase_gate`; PL's PL-D6 sentence names `paper-revision`.
2. `docs/cli.md`: every `--workflow` choice of `run init` and `run adopt` (introspected from the click
   commands) appears in the `run` section; the `run mark` entry names the gates.
3. `docs/research/claims.md` and `revision.md` no longer say "not wired"; they name `run mark <slug> verify
   done` and the respond gate; `revision.md` has "## The paper-revision workflow" with the phase table;
   `workflows/paper-draft.md` names `run init --workflow paper-draft`/`run adopt` and the verify gate.
4. Skills (5.8): paper-draft has no instruction to write or update `status.yml` by hand (no "`status.yml`:
   workflow", no "in `status.yml`" mark/update instruction), names `run init --workflow paper-draft`, `run
   adopt`, `run mark <slug> verify done --as-agent`, `run log`; journal-revision has "## Paper-revision
   workflow" with the six phases, both gates, `revision ingest` as the user's, the evidence rule and
   `resubmit`; neither skill tells an agent to edit `status.yml` or `report/claims/` or to waive; every
   phrase pinned by M-G1's docs test stays.
5. The research `AGENTS.md` contains the 3.3 sentence and M-G1's rule 12/workflow row/routing stay
   byte-exact (M-G1's test green unchanged).
6. The D11 replacement: the diff of `tests/test_docs_claims_revision.py` removes exactly
   `test_verify_gate_is_documented_as_not_yet_wired` and adds exactly `test_verify_gate_is_documented`.
7. `tests/test_docs_journal.py`, `tests/test_docs_role.py`, PL's `tests/test_docs_paper_lifecycle.py` and the
   rest of M-G1's docs test green unchanged; `mkdocs build --strict` into `RUN/scratch/m-g2/site` (the venv
   has mkdocs).

t3r checklist: docs ↔ code (every flag and message quoted exists; no claim the code does not make); the
skills never route around a gate; the protected-edit commit equals 3.3 byte for byte and is the only
`AGENTS.md` change; only the D11 test function changed among existing tests; focused set re-run; SCOPE
guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts; the delta against t0 explained by the added tests); `mkdocs build --strict`; SCOPE guard 0;
linear history (no merge commits) with at least one commit per ticket t0–t3; `git diff base..tip --
tests/` changes no existing file except the D11 function; `git diff base..tip -- '*AGENTS.md' CLAUDE.md`
equals exactly the 3.3 edit; `git diff base..tip -- pyproject.toml uv.lock` empty. Writes
`RUN/scratch/m-g2/pr-body.md` (plain English: items 1–3 of section 1, decisions M-G2-D1..D15, the protected
edit, absolute counts, residuals R1–R7, follow-ups F1–F4, "please merge with a merge commit"; no
credentials, prompts or patient data) and `reviews/m-g2-branch-report.md`. Any failure → `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: design (section 5 item by item, decisions D1–D15, PL's tests green unchanged,
the D11 change only), code and security (5.10), evidence (re-run memory-capped on the tip the t1, t2 and
t3 focused sets; confirm the branch result's absolute counts; re-run the SCOPE guard; `pr-body.md`
accurate). Output `reviews/m-g2-final-review-r<N>.md`. PASS → the installer's publish step; CHANGES →
final_fix (new commits, full suites re-run under the lock) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-g2-run.yml`, linear)

```
t0        Haiku   base, deps (M-G1 + PL merged), worktree, plan copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   paper-revision vocabulary/init/adopt/checkpoint                   DONE→t1r
t1r       Sonnet  review                                        PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                             DONE→t1r
t2/t2r/t2f        verify claims gate + respond coverage gate    same shape → pe_t3 → t3
t3/t3r/t3f        protected-edit commit, docs and skills        same shape → branch
branch    Haiku   full suites + PR body                         DONE→final, RED→escalate
final     Sonnet  whole-branch review                           PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                             DONE→final
(pe_t3 from m-g2-protected-edits.json; publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 50, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}` (13 drafted steps).
Results `pipelines/results/m-g2-run/<step>-r${round}.json`; every step requires `verdict, summary, head,
base`; implementation, fix, branch and final cards also report `scope_guard_exit`. Inputs: handoff,
decisions, gap, `mgplan` (`reviews/m-g-plan.md`), `plplan` (`reviews/pl-plan.md`); the installer adds
`plan`.

## 8. Risks and residuals (stated, not hidden)

- **R1 No manuscript, no claims gate in a paper draft** (D5). A paper-draft run whose `manuscript/` folder
  is absent can mark `verify done`; the event records `no-manuscript`, and `manuscript package` still
  refuses without a manuscript.
- **R2 Check-then-write window** (D13). A manuscript changed between the gate and the status write is not
  caught; the package refusal re-checks at packaging time.
- **R3 File-based integrity.** `status.yml` and `events.jsonl` are files a run's agents could edit by hand
  (root rule 4 and the new research rule-5 sentence forbid it); a hand-edited `verify: done` is not caught
  (same class as M-G1 R2). Host-side signing stays a follow-up (F4).
- **R4 Revision runs need their evidence brought in.** A manuscript whose `[run:]` evidence lives in the
  draft run's `experiments/` does not resolve in a separate revision run (M-G-D15); the skill offers
  revising in place or bringing results in as a data package or release import.
- **R5 Planned against PL's approved plan.** Section 4.2 is PL's planned code (diff B postimages); the
  merged code may differ (PL fixes after review). t0 refuses on any anchor drift that breaks the design.
- **R6 Browser and menu parity.** The browser marks phases with the same gates, but cannot create a
  paper-revision run (D9), and the menu's resume hint for a paper-revision run points at the research
  `AGENTS.md` (which routes decision letters to the journal-revision skill).
- **R7 Web status code.** A refused gate in the JSON API returns 404 with the message, as every
  `ServiceError` does today (`M:web/app.py:55-57`); changing that mapping is out of scope.

## 9. After M-G2

Queue: F-2 (`after: [m-g, m-h, m-i, m-d3]`) does not wait for M-G2. Follow-ups: **F1** a menu and Start-page
entry for paper-revision (`core/menu.py` `WORKFLOWS`); **F2** `run init --workflow paper-revision --from
<draft-run>` copying the submitted manuscript and package (needs a data-protection review for L2 inputs);
**F3** a `handoff`/`resubmit` gate that requires a fresh package; **F4** host-side integrity for status and
events (with M-G1's F7). Operator note: once PL and M-G2 are merged, the installer's standing rule naming the
"in-flight paper-lifecycle files" can be narrowed to `config/agents.yml`.
