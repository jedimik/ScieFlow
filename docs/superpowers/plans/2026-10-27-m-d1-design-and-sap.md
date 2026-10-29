# M-D1 plan: study design and statistical analysis plan (round 1)

Pipeline `m-d1-plan`, step plan, round 1, epoch 1. Card `t_7b40c299`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261027_225644_5779d1`).
Written 2026-10-27. Previous review: none (round 1). Size M: one plan with the design decisions it needs
(no separate spec; repo rule for L only).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no network call.

## 0. Inputs and references used

Immutable inputs (sha256 re-computed on this card; all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…7da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `continuity/milestones.yml` | 91532b22…2c0b at card start (see note) |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |

Scope source: GAP-ANALYSIS.md section 8 row **M-D1** and section **5.4**; DECISIONS D9–D12, D14, D21,
D23 where they touch design.

Note on the queue input: `continuity/milestones.yml` was rewritten by someone else during this card
(mtime 2026-10-27 23:03:31 UTC; now sha256 de3bf17f…). This card did not write it. The new version adds
the human-owned lane setting `parallel: pairs` and `parallel_pairs: [[m-e, m-b], [m-c1, m-d1]]`
(lines 13-14); the m-d1 entry is unchanged (now line 19: `after: [m-b]`). The plan holds for both
versions; the pairs setting confirms that M-D1 may share the lanes with M-C1.

Two planning artifacts written by this card (run-root, not repo files; the run pipeline pins them as
inputs with their sha256, the implementer copies them, nobody edits them):

| Artifact | What | Used by |
|---|---|---|
| `reviews/m-d1-power-reference.json` | 22 power reference cases computed on this host (section 5.4) | t1 (copied to `tests/fixtures/power/reference.json`) |
| `reviews/m-d1-reporting-skeletons.yml` | 8 reporting-guideline checklist skeletons, 256 items (section 5.3) | t2 (turned into `config/reporting/*.yml`) |

Code read read-only from git objects (`git show <ref>:<path>`, `git grep <ref>`); no checkout was touched.
`M` = remote main = `99ee8ec80691a643bec589c972212790b8213358` (`git ls-remote
git@github.com:jedimik/ScieFlow.git refs/heads/main`, 2026-10-27; the M-B merge commit, PR #19). It
contains H-4 `6152d73`, M-A `be99db2`, M-E `9eeb4fa` and M-B `05d4d10` (`git merge-base --is-ancestor`,
all exit 0). Every `path:line` below is on `M` unless marked otherwise. `R:` = the read-only researcher
snapshot `/home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher/` (v1.0.0, MIT).

## 1. For the human: what M-D1 adds, in plain words

M-D1 lets a run carry a **study design with a statistical analysis plan (SAP)** that ScieFlow reads and
enforces, and gives agents a **sample-size calculator** so they never do that arithmetic themselves.

1. **A design file the code reads.** `workspace/<slug>/design/design.yml` describes the study: type
   (RCT, cohort, case-control, cross-sectional, diagnostic accuracy, prediction model, systematic review,
   animal, economic evaluation, or computational), the question, variables and their roles, groups,
   timeline, sampling, randomisation and blinding, the sample size, the SAP (outcomes with their test,
   estimands, missing-data strategy, multiplicity, sensitivity and interim analyses, the
   discovery/confirmation split for exploratory outcomes) and the data levels the study needs. A readable
   `design.md` is generated from it. `scieflow design init|check|render|show` create, check and display
   it; `check` lists errors (lock is impossible) and warnings (advice).
2. **You lock the design; then it binds the campaigns.** `scieflow design lock <slug>` opens a
   **`design-lock` gate that only a human can answer**. On your `approve`, ScieFlow hash-locks the file
   (and its power calculation) in an append-only ledger. From then on:
   - an experiment campaign whose metrics are not outcomes of the locked SAP is **refused**;
   - every campaign is refused while `design.yml` differs from its lock (an edit without an amendment);
   - a change goes through `scieflow design amend <slug> --summary … --rationale …`, which attaches the
     diff and opens a new human-only gate. Nothing is ever re-locked or silently edited.
   A **computational** design (the image-processing pipelines ScieFlow has today) only **warns** for a
   missing lock or an out-of-SAP campaign, as the gap plan asks. A run without a design file behaves
   exactly as today.
3. **Sample size in code: `scieflow experiment power`.** Two-sample, paired and one-sample t-tests,
   one-way ANOVA, two proportions, correlation, and a survival approximation (Schoenfeld). It solves for
   the sample size, the power, or (for t/ANOVA/proportions) the smallest detectable effect, prints the
   assumptions, and writes a JSON file the design refers to. `scieflow design check` **recomputes** that
   file, so a hand-edited number is caught.
4. **Reporting-guideline checklists as data.** `config/reporting/` holds CONSORT 2010, STROBE, STARD 2015,
   TRIPOD+AI, PRISMA 2020, SPIRIT 2013, ARRIVE 2.0 and CHEERS 2022 as item lists (item number, a short
   label in our own words, the manuscript section, the kind of evidence). The design names the guideline(s)
   it will follow; M-F later uses the same files to check a manuscript. **They are marked
   `unverified`**: I wrote them from knowledge without network access, so they must be checked against
   the official checklists before anyone relies on them for a submission (residual R4).

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-D1-D1 Power without a new dependency.** The gap row says "statsmodels". statsmodels is not
  installed and not in `uv.lock`; adding it needs an online lock refresh and changes the shared venv
  both lanes use. M-D1 implements the statsmodels formulas over **scipy**, which the `experiments` extra
  already pulls in (`scikit-image` depends on it, `uv.lock:2955` block). Reference values were computed on
  this host and cross-checked against independent published integers (Cohen 1988, G*Power, Schoenfeld
  1983); a test compares against real statsmodels whenever it is installed (skipped today). M-D2, which
  needs statsmodels for its models, can add it and turn that test on. (Alternative: add statsmodels now;
  say so.)
- **M-D1-D2 Lock semantics re-implemented, not vendored.** researcher's `protocol.py`
  (`R:core/researcher_core/protocol.py`, sha256 19bd7056…c5e7) is bound to researcher's provenance
  ledger (it imports `provenance` and `model`, `R:…/protocol.py:39-49`); D15 says vendor *pure* modules and
  re-implement the ledger. M-D1 re-implements its rules exactly (lock once; amendments only, each with a
  required summary and rationale and the superseded hash; version derived from the count of lock +
  amendment records, `"1.0"`, `"2.0"`, …; the hash is SHA-256 of canonical JSON, keys sorted,
  `R:…/model.py:45-70`) over a ScieFlow ledger file. Credit goes in the module docstring and the docs page.
  `CREDITS.md`/`VENDORED.md` are not touched: they are created by the parallel M-C1 lane and list copied
  code (follow-up F1).
- **M-D1-D3 Who answers.** `design-lock` is `requires_human: true` in every mode (gap 5.4); the answer
  hook also refuses a non-human actor itself, like the M-B `data-release` hook.
- **M-D1-D4 No new event types.** The parallel M-C1 lane edits the closed event vocabulary and its two
  test pins right now (`src/scieflow/core/events.py:20-40`, `tests/core/review/test_gates_events.py:156-158`,
  `tests/core/test_service.py:1215-1239`). M-D1 records with existing types only: the lock approval is
  the existing `gate.answered` event plus the ledger record; a refused campaign is `job.refused` with
  `reason: design` (the same type the budget refusal uses, `src/scieflow/core/run/actions.py:61`); a
  computational-design warning is the free-form `note.design_warning` (`events.py:49`). This avoids a
  three-file merge conflict between the lanes.
- **M-D1-D5 Checklists are skeletons, unverified.** Official item wording is not reproduced (licence and
  accuracy); ids and section mapping only, each file `verification.status: unverified` until a human
  records a check. CONSORT 2025 and SPIRIT 2025 exist and are not included (R4).
- **M-D1-D6 Integrity refuses for every study type.** A locked design whose file no longer matches its
  lock, or a ledger that does not verify, refuses campaigns even for computational designs (an integrity
  failure, not a methodological choice). Only "not locked yet" and "out of SAP" are warn-only for
  computational designs.
- **M-D1-D7 Campaign ↔ SAP link = metric names.** Campaigns name `metrics` (`campaign.py:80`) and have no
  test field; an SAP outcome may carry `metric: <campaign metric name>`. "Out of SAP" means a campaign
  metric that no SAP outcome names. The test-per-outcome check arrives with M-D2's `stats.json`
  (no campaign test exists to compare today).
- **M-D1-D8 One protected edit.** Root `AGENTS.md` gains rule 19 "Study design" (section 3); applied by
  the operator before t5. No other agent-instruction file changes.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head
`dev/m-d1` → base `main`, one PR, merged with a merge commit. Title "M-D1 Design and SAP". The body
(written by the branch step) lists the four capabilities, decisions M-D1-D1..D8, absolute test counts and
the residuals.

## 2. Base, dependencies and the parallel lane

- **Dependency M-B is merged**: `RUN/scratch/m-b/publish.json` `merged: true`, `main_after_merge`
  99ee8ec; the queue lists `m-d1 after: [m-b]` (`continuity/milestones.yml:19` in the current version,
  `:14` in the card-start version). M-D1 can start.
- **M-C1 runs in the other lane** (allowed pair M-C1 ∥ M-D1, GAP section 8 order note). Its branch
  `dev/m-c1` (worktree `RUN/scratch/m-c1-wt`, base 99ee8ec) changes, among others,
  `src/scieflow/core/events.py`, `src/scieflow/core/data/dispatch.py`, `docs/cli.md`, `mkdocs.yml`,
  `docs/data-protection.md` and creates `CREDITS.md`/`VENDORED.md` (its plan, `reviews/m-c1-plan.md`
  section 3 file table). M-D1's overlap is kept to three small hunks (hotspots, flagged on the card):
  - `src/scieflow/core/data/dispatch.py:251-259` (the `protected` list; M-C1 adds `network.yml` there,
    M-D1 adds the design ledger) — keep-both resolution;
  - `docs/cli.md` (M-D1 adds a new `scieflow design` section after `scieflow data`, never touches the
    exit-code table that M-C1 extends);
  - `mkdocs.yml` (M-D1 adds one line under Experiments; M-C1 adds one under Research).
  Whichever lane merges second gets the installer's `refresh` step (merge of the new main, both sides
  kept, full suites re-run).
- **Worktree**: t0 creates `RUN/scratch/m-d1-wt` with
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-d1 RUN/scratch/m-d1-wt <base>`
  where `<base>` = `git ls-remote … refs/heads/main` at t0 time (object-only fetch
  `git fetch --no-tags --no-write-fetch-head <remote> refs/heads/main` first). t0 requires the four
  ancestors H-4 `6152d733728b9ed5bf5b571aef475f8071426386`, M-A `be99db2ace0e560fcdb7c00231e906e4aaae67e2`,
  M-E `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`, M-B `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`. If M-C1
  has merged before t0, base simply includes it; t0 then re-checks the hotspot cites on that base.
  Commits are local on `dev/m-d1`; no rebase, squash, amend or force; fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-d1/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-d1-<step>/`.

## 3. In-flight, protected and changed files

**INFLIGHT** (the uncommitted paper-lifecycle work in `/home/ubuntu/Github/ScieFlow`; same 9 paths as
M-A section 3). M-D1 edits **none** of them:

```
config/agents.yml
docs/runs.md
schemas/status.yml
src/scieflow/core/run/actions.py
src/scieflow/core/run/checkpoint.py
src/scieflow/core/run/cli.py
src/scieflow/core/run/init.py
src/scieflow/core/run/status.py
src/scieflow/core/run/validate.py
```

Guard (must exit 0): `git -C WT diff --exit-code <base> -- <INFLIGHT>` (committed range) and, on a dirty
tree during a card, `git -C WT diff --exit-code -- <INFLIGHT>`. M-D1 *calls* `actions.run_for_path`
(`src/scieflow/core/run/actions.py:85-91`) and reads `charter.read` (`src/scieflow/core/run/charter.py:36`);
it does not change them. No phase is added (phases live in the in-flight `schemas/status.yml`).

**Never edited**: `config/sandbox.yml`, the DD1-sealed files (`tests/probes/k_task8_binding.py`,
`tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, any
`AGENTS.md` except through the protected edit below, `CREDITS.md`, `VENDORED.md`, `src/scieflow/core/events.py`.

**Protected edit (operator-applied before t5, `pipelines/drafts/m-d1-protected-edits.json`)** — root
`AGENTS.md`, new rule 19 inserted between rule 18 and the Skills table (`AGENTS.md:215-218`). Exact text:

old:
```
    data scan` (undeclared data that looks like patient data) go to the
    user with a proposed declaration; they are never silenced.

## Skills (read the relevant one before acting)
```
new:
```
    data scan` (undeclared data that looks like patient data) go to the
    user with a proposed declaration; they are never silenced.

19. **Study design** (`docs/experiments/design.md`). A run that holds
    `design/design.yml` is bound by it once a human answers its
    `design-lock` gate (`uv run scieflow design lock <slug>`): campaigns
    whose metrics are not outcomes of the locked analysis plan are refused,
    and so is every campaign while the file differs from its lock. Change a
    locked design only through `uv run scieflow design amend <slug>
    --summary … --rationale …` (a new human-only gate); never edit
    `design/lock.jsonl` or `design/locked/`, and never answer a
    `design-lock` gate yourself. Sample sizes come from `uv run scieflow
    experiment power`, never from your own arithmetic.

## Skills (read the relevant one before acting)
```
The old text occurs exactly once on M (the tool refuses otherwise). t5 commits the applied file in its
own commit ("docs(agents): rule 19 study design (human-approved protected edit) (M-D1 t5)").

**Complete file list** (nothing else changes; tests only gain files or lines except the one pin named):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-27-m-d1-design-and-sap.md` | copy of this plan | t0 |
| `src/scieflow/experiments/power.py` | new: power calculations | t1 |
| `src/scieflow/experiments/cli.py` | t1: `power` command; t5: design guard in `run` and `sweep` | t1, t5 |
| `docs/experiments/cli.md` | `## scieflow experiment power` section | t1 |
| `tests/fixtures/power/reference.json` | copy of `reviews/m-d1-power-reference.json` | t1 |
| `tests/experiments/test_power.py`, `tests/experiments/test_power_cli.py`, `tests/experiments/test_power_statsmodels.py` | new | t1 |
| `config/reporting/index.yml`, `config/reporting/{consort-2010,strobe,stard-2015,tripod-ai,prisma-2020,spirit-2013,arrive-2.0,cheers-2022}.yml` | new | t2 |
| `schemas/reporting-checklist.yml` | new: format description (enums) | t2 |
| `src/scieflow/core/design/__init__.py`, `src/scieflow/core/design/reporting.py` | new (package docstring; registry/checklist loader + validator) | t2 |
| `src/scieflow/core/design/cli.py` | new: t2 `guidelines`; t3 `init check render show`; t4 `lock amend`, `show` lock state | t2, t3, t4 |
| `src/scieflow/cli.py` | one `GROUPS` entry `design` (after `data`, `src/scieflow/cli.py:56-61`) | t2 |
| `tests/core/design/test_reporting.py`, `tests/fixtures/reporting/skeletons.yml` (byte copy of `reviews/m-d1-reporting-skeletons.yml`) | new | t2 |
| `schemas/design.yml` | new: format description (enums, keys) | t3 |
| `src/scieflow/core/design/model.py`, `src/scieflow/core/design/render.py`, `src/scieflow/core/design/templates.py` | new | t3 |
| `tests/core/design/test_model.py`, `tests/core/design/test_render.py`, `tests/core/design/test_cli_check.py`, `tests/fixtures/design/*.yml` | new | t3 |
| `schemas/gates.yml` | one kind `design-lock` (after `data-release`, `schemas/gates.yml:24`) | t4 |
| `src/scieflow/core/gates.py` | constant `DESIGN_LOCK_KIND` + one hook call in `answer` (next to `:123-126`) | t4 |
| `src/scieflow/core/design/lock.py` | new: ledger, pending, lock, amend, verify, `on_answer`, `campaign_verdict` | t4 (verdict t5) |
| `src/scieflow/core/data/dispatch.py` | `protected` gains `design/lock.jsonl` and `design/locked` (`:251-259`) | t4 |
| `tests/core/data/test_release.py` | the kind pin `:293` gains `"design-lock"` (the one existing assertion that changes) | t4 |
| `tests/core/design/test_lock.py`, `tests/core/design/test_gate_hook.py`, `tests/core/design/test_sandbox_design.py` | new | t4 |
| `skills/experiment-cycle/SKILL.md`, `skills/research-loop/SKILL.md`, `src/scieflow/experiments/skills/experiment-designer/SKILL.md` | design sections | t5 |
| `docs/experiments/design.md` (new), `docs/cli.md` (new `scieflow design` section), `mkdocs.yml` (one nav line) | docs | t5 |
| `AGENTS.md` | rule 19, operator-applied, committed by t5 | t5 |
| `tests/experiments/test_design_guard.py`, `tests/test_docs_design.py` | new | t5 |

## 4. Current code, verified (on M = 99ee8ec)

**Gates.** Kinds are data in `schemas/gates.yml` (`data-release` at `:24`, `states` `:25`), loaded by
`gates.kinds` → `project.schema("gates")` (`src/scieflow/core/gates.py:44-45`,
`src/scieflow/core/project.py:82-83`). `open_gate` (`gates.py:70-93`) stores `requires_human` from the
table and emits `gate.opened`. `answer` (`gates.py:108-135`) runs kind hooks **before** it marks the gate
answered — review kinds (`:118-122`) and `data-release` (`:123-126`, `release.on_answer`); a hook's
`GateError` leaves the gate open. Agents never answer `requires_human` kinds (`:96-105`). The CLI answer
and the web both go through `service.answer_gate` (`src/scieflow/core/service.py:1230-1262`), which calls
`gates.answer` (`:1253`). The dashboard lists every open gate generically (`service.open_gates`,
`service.py:1100-1122`). The kind-set pin: `tests/core/data/test_release.py:285-293`
(`set(table) == BASE_KINDS | {"data-release"}`).

**Events.** Closed vocabulary `TYPES` (`src/scieflow/core/events.py:20-40`); free-form `note.<name>`
accepted (`:49-50`); `job.refused` exists (`:25`) and the budget refusal uses it with `reason="budget"`
(`src/scieflow/core/run/actions.py:57-62`). Vocabulary pins: `tests/core/review/test_gates_events.py:156-158`,
`tests/core/test_service.py:1215-1239` (M-D1 leaves both alone, M-D1-D4).

**Experiments CLI.** `run` (`src/scieflow/experiments/cli.py:79-104`) and `sweep` (`:107-130`) load the
campaign, find the owning run with `_budget_run` (`:58-62`, `actions.run_for_path`,
`actions.py:85-91`: the nearest `workspace/<name>/` with `status.yml`), guard the budget (`_guard`
`:65-71`) and record spend after the runs. No study-design concept exists anywhere
(`git grep -n -i "design.yml\|design-lock\|power" 99ee8ec -- src` finds only unrelated words).
`Campaign` keys: `KNOWN_KEYS` `src/scieflow/experiments/campaign.py:16-20`; `metrics` required
(`:71-73`), `rank_by` defaults to the first metric (`:91`).

**Dependencies.** `experiments` extra = numpy, scikit-image, imageio, matplotlib (`pyproject.toml:10-15`);
no scipy line, but `uv.lock` `scikit-image` (`uv.lock:2955` block) depends on scipy 1.17.1 (py<3.12) /
1.18.1 (py≥3.12); the venv has scipy 1.18.1 and no statsmodels/pandas/patsy (checked by import on this
host). jsonschema is only in the `research`/`chats` extras (`pyproject.toml:17-20`), so core code
validates by hand, as `core/data/levels.py` does (`:79-135`, fail-closed `read` `:138-165`).

**CLI groups.** `src/scieflow/cli.py:14-62` (`GROUPS`, lazy; `data` entry `:56-61`, no extra). A new
group needs only a `GROUPS` entry. `tests/core/test_cli_parity.py` covers public service functions only
(M-D1 adds none).

**Data protection (M-B).** Run level `levels.run_level` (`src/scieflow/core/data/levels.py:167-179`;
`ORDER` `:20`). Dispatch makes run files read-only inside a sandbox through `Decision.protected`
(`src/scieflow/core/data/dispatch.py:43`, built at `:251-259`: `data.yml` and `released/` when they exist
and are not symlinks); the sandbox binds them read-only and `verify()` proves it (`core/sandbox.py:403-420`,
`:703-704`). `gates/` and `events.jsonl` are not protected (residual R2).

**Charter.** `charter.read(ws)` → `{"current": int, "versions": [...]}` (`src/scieflow/core/run/charter.py:36-55`);
M-D1 records `current` on each lock record, read-only.

**Skills and rules.** `skills/research-loop/SKILL.md:37-42` (phase table; `hypothesize` writes
`hypothesis.md`), `skills/experiment-cycle/SKILL.md:12-38` (backend, per-campaign gate),
`src/scieflow/experiments/skills/experiment-designer/SKILL.md:25-30` (metrics), `:37-55` (campaign YAML).
Root `AGENTS.md:137-145` rule 14 (gates), `:201-216` rule 18 (data protection), `:218` Skills table.
`tests/test_docs_data_protection.py:94` pins rule 18's first line (unchanged by M-D1).

**Docs.** `docs/cli.md:10-20` (exit codes), `:22-39` (`scieflow data`), `:41` (`scieflow run`);
`docs/experiments/cli.md` sections `:7-68`; `mkdocs.yml:64-73` (Experiments nav).

**researcher (read-only).** `R:core/researcher_core/protocol.py` (432 lines): `lock_protocol` refuses a
second lock, `amend_protocol` requires lock + non-empty summary and rationale and records
`previous_hash`/`previous_version`, versions derived from the event count, `check_protocol` compares the
in-force hash; `R:core/researcher_core/model.py:45-70` `canonical_json` (sort_keys, `(",", ":")`,
`ensure_ascii=False`, `allow_nan=False`) and `content_hash` (SHA-256 hex).

## 5. Design

### 5.1 `design/design.yml` (format v1; described by `schemas/design.yml`)

`schemas/design.yml` is a YAML description read by the code (like `schemas/manifest.yml`), not JSON
Schema. It holds the enumerations and key lists below; `model.py` reads it through
`Project.schema("design")` and implements the rules by hand (no jsonschema in core).

```yaml
version: 1                                  # required, int 1
title: <str>                                # required
study_type: <study_types>                   # required
question: <str>                             # required
pico: {population, intervention, comparator, outcome, timeframe}   # optional strings
variables:                                  # required, >= 1
  - {id, name, role: <variable_roles>, type: <variable_types>, unit?, description?}
groups: [{id, name, description?, allocation?: <number>}]          # rct: >= 2
timeline: [{id, label, at}]                                         # optional (visits)
sampling: {method: <sampling>, inclusion: [str], exclusion: [str], frame?}
randomisation: {method: <str>, unit: <str>, stratified_by: [variable ids], block_size?: int}  # rct: required
blinding: {participants: bool, assessors: bool, analysts: bool}
sample_size:                                # one of three forms
  {method: power, power_file: design/power/<name>.json}
  {method: fixed, n: <int > 0>, rationale: <str>}
  {method: not-applicable, rationale: <str>}
sap:                                        # required
  estimands: [{id, outcome: <outcome id>, population: str, summary_measure: str, intercurrent_events?: str}]
  outcomes:  [{id, variable: <variable id>, priority: <outcome_priorities>, test: <tests>,
               covariates?: [variable ids], estimand?: <estimand id>, metric?: <campaign metric name>}]
  missing_data: {strategy: <missing_data>, details?: str}
  multiplicity: {method: <multiplicity>, alpha: <0 < x < 1>}
  sensitivity: [{id, description}]
  interim: {planned: bool, rules?: str}
  exploratory: {split: {fraction: <0 < x < 1>, seed: int, stratify_by: [variable ids]}, fdr: bh}
reporting_guidelines: [<ids in config/reporting/index.yml>]
data: {levels_needed: [L0|L1|L2], sources?: [{path: <run-relative glob>, level: L0|L1|L2}]}
notes: <str>
```

Enumerations (`schemas/design.yml`):

- `study_types`: rct, cohort, case-control, cross-sectional, diagnostic-accuracy, prediction-model,
  systematic-review, animal, economic-evaluation, computational
- `variable_roles`: exposure, intervention, outcome, covariate, confounder, mediator, effect-modifier,
  stratifier, predictor, index-test, reference-standard
- `variable_types`: continuous, binary, categorical, ordinal, count, time-to-event
- `outcome_priorities`: primary, secondary, exploratory
- `tests`: t-test-ind, welch-t-test, t-test-paired, t-test-one, anova-oneway, ancova, chi-square,
  fisher-exact, mann-whitney, wilcoxon-signed-rank, kruskal-wallis, linear-regression,
  logistic-regression, mixed-model, cox-regression, log-rank, pearson, spearman, roc-auc,
  sensitivity-specificity, descriptive, other
- `missing_data`: complete-case, multiple-imputation, mixed-model, inverse-probability-weighting,
  worst-case, not-applicable, other
- `multiplicity`: none, bonferroni, holm, hochberg, bh-fdr, hierarchical, gatekeeping
- `sampling`: consecutive, random, stratified-random, convenience, census, other
- `top_keys.required` / `top_keys.optional` as listed above.

### 5.2 Reading and checking (`src/scieflow/core/design/model.py`)

`load(ws) -> Design | Absent | Invalid(reason)`, fail-closed like `levels.read`
(`core/data/levels.py:138-165`): a symlinked or unreadable file, a YAML error (including
`ValueError`/`RecursionError`), an empty file or a non-mapping is `Invalid`; only a plain absent name is
`Absent`. `check(project, ws, design) -> Report(errors: list[Problem], warnings: list[Problem])`,
`Problem = (rule, where, message)`; messages name a key path and a rule, never a free-text value.

`content(design)` = the parsed mapping; it must be JSON-compatible: str, int, float (finite), bool,
null, list, mapping with str keys (a YAML date, NaN or inf is error `json-value` naming the key path:
"quote dates"). `content_hash(obj)` = SHA-256 hex of `json.dumps(obj, sort_keys=True,
separators=(",", ":"), ensure_ascii=False, allow_nan=False)` (M-D1-D2). The **locked content** is
`{"design": <design.yml mapping>, "power": <power-file mapping or null>}`, so a locked power calculation
cannot change either.

Errors (lock impossible; for non-computational designs they also refuse campaigns, 5.6):

| Rule | When |
|---|---|
| `keys` | unknown top-level key, missing required key, `version` ≠ 1 |
| `json-value` | non-JSON value (see above) |
| `enum` | a value outside its enumeration |
| `todo` | a string equal to `TODO` or starting with `TODO:` (left by `design init`) |
| `id` | an id not matching `^[a-z][a-z0-9_-]{0,40}$`, or duplicated within its list |
| `ref` | `outcomes[].variable`, `covariates`, `stratified_by`, `split.stratify_by` not a variable id; `estimands[].outcome` not an outcome id; `outcomes[].estimand` not an estimand id |
| `primary` | non-computational and no outcome with `priority: primary` |
| `test` | an outcome without `test` |
| `rct` | `study_type: rct` with < 2 groups or no `randomisation` |
| `sample-size` | `sample_size` present but none of the three forms; `power_file` not run-relative under `design/`, contains `..`, is a symlink, or is missing/unreadable |
| `power-mismatch` | the power file's `result` differs from a recomputation of its `inputs` beyond the tolerances of 5.4 (or its family/solve is unknown) |
| `metric` | the same `metric` on two outcomes |
| `alpha` | `multiplicity.alpha` not in (0, 1) |
| `exploratory` | non-computational, an outcome with `priority: exploratory`, and no `sap.exploratory.split` with `fdr: bh` (rr D14: mandatory discovery/confirmation split) |
| `guideline` | an id in `reporting_guidelines` not in `config/reporting/index.yml` |
| `levels` | a `data.levels_needed` or `sources[].level` value outside L0/L1/L2; a `sources[].path` that fails `levels.path_problem` (`core/data/levels.py:68-76`) |

Warnings (advice; never block a lock):

| Rule | When |
|---|---|
| `no-sample-size` | non-computational without `sample_size` |
| `power-unverified` | a power file exists but `scieflow.experiments.power` cannot be imported (the `experiments` extra is missing) — the recomputation is skipped, not passed |
| `guideline-fit` | a listed guideline whose `applies_to` does not include `study_type` |
| `no-guideline` | non-computational and `reporting_guidelines` empty ("journal research, M-F, will name it", D12) |
| `guideline-unverified` | a listed checklist with `verification.status: unverified` |
| `level-undeclared` | `data.levels_needed` contains L2 but `levels.run_level(ws)` < L2: "declare it with `scieflow data declare`" — ScieFlow never declares it itself (D10, D11) |
| `missing-data` | non-computational without `sap.missing_data` |
| `multiplicity` | more than one primary outcome and no `sap.multiplicity` (or `method: none`) |
| `outcome-role` | an outcome's variable whose role is not `outcome`, `index-test` or `reference-standard` |

`render(design, report, hash) -> str` (`render.py`): deterministic Markdown, first line
`<!-- generated by scieflow design render from design.yml sha256 <hash>; do not edit -->`, then fixed
sections in this order: title and study type, question and PICO, variables (table), groups, timeline,
sampling, randomisation and blinding, sample size (the power file's family, inputs, result and
assumptions), SAP (estimands, outcomes table with priority/test/metric, missing data, multiplicity,
sensitivity, interim, exploratory split), reporting guidelines (each with its verification status), data
levels, check result (error/warning counts and rule ids). Same input → byte-identical output (no clock).

`templates.py`: `template(study_type, title) -> str` — a commented YAML skeleton for each study type, every
free-text field `TODO`, so `check` reports `todo` errors until filled; `computational` has no PICO and
`sample_size: {method: not-applicable, rationale: TODO}`.

### 5.3 Reporting checklists (`config/reporting/`, `schemas/reporting-checklist.yml`, `core/design/reporting.py`)

`config/reporting/index.yml`: `{version: 1, guidelines: [<id>, …]}` listing the eight ids in skeleton
order. `config/reporting/<id>.yml` (one per guideline), built by t2 from
`reviews/m-d1-reporting-skeletons.yml` (ids, names, versions, `applies_to`, `granularity`, `source`, items
copied exactly; nothing added or reworded):

```yaml
version: 1
id: consort-2010
name: CONSORT 2010
version_label: "2010"            # the skeleton's `version`
applies_to: [rct]
granularity: full                # full | top-level
source: {citation: "<as in the skeleton>", doi: "<as in the skeleton>"}
verification:
  status: unverified             # unverified | verified
  note: "Item ids and labels were written offline from knowledge (M-D1 planning). Check them against the official checklist (source.doi) before relying on them."
  verified_by: null
  verified_at: null
items:
  - {id: "1a", topic: "Title says randomised trial", sections: [title], evidence: statement}
```
(`group: essential|recommended` is an optional item key, used by ARRIVE 2.0.)

`schemas/reporting-checklist.yml` holds `sections: [title, abstract, introduction, methods, results,
discussion, other]`, `evidence: [statement, number, table, figure, diagram, registration]`,
`granularity: [full, top-level]`, `verification_status: [unverified, verified]`, `item_groups:
[essential, recommended]`, the required keys.

`reporting.py`: `registry(root) -> list[Checklist]`, `get(root, id)`, `validate_all(root) -> list[Problem]`.
Fail-closed loading; `validate_all` reports: index unreadable; a listed id without its file; a
`config/reporting/*.yml` file (other than `index.yml`) not listed (orphan); `id` ≠ file stem; unknown or
missing keys; enum violations; `applies_to` ⊄ `study_types` of `schemas/design.yml`; empty `items`;
duplicate item ids; `topic` empty or longer than 60 characters; `verification.status: verified` without
`verified_by` and `verified_at`; `doi` not matching `^10\.\d{4,9}/\S+$` (null allowed). Item counts per
file are pinned in the t2 test to the skeleton (37, 34, 34, 27, 42, 33, 21, 28).

CLI: `scieflow design guidelines [--json]` lists id, name, applies_to, item count, verification status;
`--check` runs `validate_all` (exit 0 none, 1 problems listed). Read-only; the files are repo files.

### 5.4 Power (`src/scieflow/experiments/power.py`, `scieflow experiment power`)

Formulas (statsmodels.stats.power semantics; scipy only, lazy import inside the module):

| Family | Effect input | n meaning | Power function |
|---|---|---|---|
| `t-ind` | `--effect-size` d (Cohen) | n1 = group 1; n2 = ratio·n1 | `ttest_power(d, nobs=1/(1/n1+1/n2), df=n1+n2-2)` |
| `t-paired`, `t-one` | d | number of pairs/subjects | `ttest_power(d, nobs=n, df=n-1)` |
| `anova` | `--effect-size` f (Cohen), `--groups k` ≥ 2 | total n | `ncf.sf(f.isf(α, k-1, n-k), k-1, n-k, f²·n)` |
| `proportions` | `--p1 --p2` (h = 2·asin√p1 − 2·asin√p2, sign dropped) or `--effect-size` h | n1; n2 = ratio·n1 | `normal_power(h, nobs=1/(1/n1+1/n2))` |
| `correlation` | `--r` (|r| used) | number of pairs | `normal_power(atanh|r|, nobs=n-3)` |
| `survival` | `--hazard-ratio`, `--allocation` p (default 0.5), optional `--event-probability` | events D (n = D / event probability when given) | `Φ(√(D·p(1-p))·|ln HR| − z₁₋α/₂) + Φ(−√(D·p(1-p))·|ln HR| − z₁₋α/₂)`; D for a target power = `(z₁₋α/₂ + z_power)² / (p(1-p)(ln HR)²)` (Schoenfeld 1983) |

with `ttest_power(d, nobs, df) = nct.sf(t.isf(a, df), df, d√nobs) [+ nct.cdf(t.ppf(a, df), df, d√nobs) when
two-sided]` and `normal_power(d, nobs) = norm.sf(norm.isf(a) − d√nobs) [+ norm.cdf(norm.ppf(a) − d√nobs)
when two-sided]`, where `a = α/2` for two-sided and `α` for `larger`.

Options: `FAMILY`, `--alpha` (default 0.05; 0 < α ≤ 0.5), `--power` (α < power < 1), `--n`, `--events`
(survival), effect inputs as above, `--ratio` (default 1, > 0; t-ind/proportions), `--groups` (anova),
`--alternative two-sided|larger` (default two-sided; t-*, proportions, correlation; anova and survival
are two-sided/F only; `smaller` is refused with "state the effect as a positive size and use larger"),
`--json`, `--out PATH` (writes the JSON atomically; refuses an existing path unless `--force`, refuses a
symlink). Exactly one of {n (or events), power, effect} is unknown and is solved: `n` (all families),
`power` (all), `effect` (t-*, anova, proportions; not correlation/survival). Anything else → usage error
(exit 2).

Solving: bracket from the family minimum (t-ind n1 = 2, t-one/t-paired n = 2, anova n = k + 1,
correlation n = 4, proportions n = 1, survival D = 1; effect from 0.05) and double the upper end until
the power reaches the target, then `brentq(xtol=1e-12)`. scipy 1.18.1's noncentral t returns NaN in the
lower-tail term at large noncentrality (observed on this host: `nct.cdf(t.ppf(0.025, 9998), 9998, 25.0)`
is `nan`, while the upper tail is 1.0). Rule: when that lower-tail term is NaN **and** the
noncentrality is ≥ 10, the term is 0.0 (for d > 0 it is below Φ(z_{α/2} − 10) ≈ 3·10⁻³³ there), so an
over-powered study (solving for power) reports 1.0; the bracket grows from below so solving never enters that
region; any other NaN is `PowerError("numerical failure for these inputs")`, never a silent 1.0. Upper bound
10,000,000 (n/D) or effect 100 → `PowerError("needs more than …")`. Domain errors (`p1 == p2`, `HR == 1`,
`ratio ≤ 0`, `n` at or below the family minimum, …) are `PowerError` → exit 1 with the rule; click
type errors → exit 2.

Output (`--json`; deterministic, `sort_keys=True`, no timestamp):
```json
{"version": 1, "tool": "scieflow experiment power", "family": "t-ind", "solve": "n",
 "inputs": {"alpha": 0.05, "power": 0.8, "effect_size": 0.5, "ratio": 1.0, "alternative": "two-sided"},
 "result": {"n1": 63.76561019095245, "n1_required": 64, "n2_required": 64, "n_total_required": 128},
 "method": "noncentral t (statsmodels TTestIndPower semantics), scipy <version>",
 "assumptions": ["normally distributed outcome in each group", "equal variances", "independent observations"]}
```
Result keys per family: t-ind/proportions `n1`, `n1_required` (ceil), `n2_required` = ceil(ratio·n1),
`n_total_required`; t-one/t-paired/correlation `n`, `n_required`; anova `n_total`, `n_per_group_required` =
ceil(n_total/k), `n_total_required` = k·that; survival `events`, `events_required`, and with
`--event-probability` also `n`, `n_required`; solve power → `power`; solve effect → `effect_size`;
proportions always records the derived `effect_size` (h) in `inputs`. Without `--json` a short table and
the assumptions. `recompute(doc) -> doc` is the function `design check` uses (5.2 `power-mismatch`):
it re-solves `inputs` and compares `result` with the tolerances below.

Reference values (`reviews/m-d1-power-reference.json` → `tests/fixtures/power/reference.json`): 22 cases
over all families and solve modes; tolerances n/events/effect relative 1e-6, power absolute 1e-8. They are
**this implementation's formulas computed on this host with scipy 1.18.1**, not statsmodels output, and
were cross-checked against independent integers: d = 0.2/0.5/0.8 → 393.4/63.77/25.52 per group (Cohen
1988: 393, 64, 26); paired d = 0.5 → 33.37; ANOVA f = 0.25, k = 3 → 157.19 total (G*Power: 159 = 3 × 53);
r = 0.30 → 84.93 (Cohen: 85); HR 0.5 → 65.35 events (Schoenfeld: ≈ 66). `test_power_statsmodels.py` uses
`pytest.importorskip("statsmodels")` and compares every case with the corresponding statsmodels call
(`TTestIndPower`, `TTestPower`, `FTestAnovaPower`, `NormalIndPower`; correlation and survival have no
statsmodels counterpart and are skipped there).

### 5.5 Lock, amend and the ledger (`src/scieflow/core/design/lock.py`)

Files in the run (`workspace/<slug>/design/`):

| Path | Writer | Protected in dispatches |
|---|---|---|
| `design.yml`, `design.md`, `power/*.json` | agent or human (CLI) | no |
| `pending.json` | `design lock` / `design amend` | no |
| `amendments/v<N>.diff` | `design amend` | no |
| `lock.jsonl` | only the gate answer hook (host side) | **yes** (when it exists) |
| `locked/v<N>/{design.yml,power.json}` | only the gate answer hook | **yes** (the `locked` directory, when it exists) |

Ledger record (one JSON line, appended under `store.locked`):
`{"seq": n, "kind": "lock"|"amendment", "version": "<n>.0", "content_hash": "<64 hex>",
"previous_hash": "" | "<64 hex>", "previous_version": "" | "<n-1>.0", "summary": "", "rationale": "",
"gate": "<gate id>", "approved_by": "human", "approved_at": "<iso>", "charter_version": <int|null>,
"copy": "design/locked/v<n>"}` — `seq` 1, 2, 3 … without gaps; `version` derived from `seq`.

Operations:

- `lock(project, ws)` (CLI `design lock`): refuse if the ledger already has a record ("already locked
  (version …): use `scieflow design amend`"), if a pending gate is still open ("answer or reject gate …
  first"; a pending whose gate is answered, withdrawn or missing is stale and removed), or if `check` has
  errors (listed). Then render `design.md`, write `pending.json` `{gate, kind: "lock", content_hash,
  opened_at}`, and open gate `design-lock` with options `[approve, reject]`, files
  `[design/design.yml, design/design.md]` (+ the power file), question
  `"Lock the study design of <slug> (sha256 <first 12>, <n> warnings)?"`. Returns the gate.
- `amend(project, ws, summary, rationale)`: requires a lock, the current content hash ≠ the in-force hash
  (else "nothing to amend"), non-empty summary and rationale (rule from `R:…/protocol.py` `amend_protocol`),
  no open pending, zero `check` errors. Writes `amendments/v<N+1>.diff` (unified diff of the canonical YAML
  dumps of `locked/v<N>/design.yml` and the current file, plus the power file if it changed), `pending.json`
  with kind `amendment`, summary, rationale, and opens the gate with the diff attached and question
  `"Amend the locked study design of <slug> to version <N+1>.0 (sha256 <12>): <summary>?"`.
- `on_answer(project, ws, gate, answer, actor)` — called from `gates.answer` for kind `design-lock`
  **before** the gate is marked answered (`gates.py:123-126` pattern). `actor != "human"` → `GateError`.
  `reject` → remove `pending.json` (if it names this gate); nothing else. `approve` → require
  `pending.json` naming this gate, the current content hash equal to the pending hash **and** to the
  12-character prefix in the gate question, and zero `check` errors; otherwise `GateError` ("the design
  changed since the gate opened; reject it and run `scieflow design lock|amend` again") and the gate
  stays open. Idempotent: a ledger record already naming this gate → no second record. Writes the copy
  under `locked/v<n>/`, appends the record (`charter_version` = `charter.read(ws)["current"]`, or null on
  `CharterError`), removes `pending.json`. The `gate.answered` event that `gates.answer` emits afterwards is
  the timeline entry (M-D1-D4).
- `state(project, ws) -> DesignState` with `status` one of `none` (no design.yml, no ledger), `invalid`
  (design.yml `Invalid`, no ledger), `draft` (valid or not, no ledger record), `pending` (draft with an
  open lock gate), `locked` (current hash = in-force hash), `modified` (current ≠ in-force, or design.yml
  deleted after a lock), `ledger-invalid`; plus `version`, `in_force_hash`, `current_hash`, `study_type`,
  `report`, `pending_gate`, `sap_metrics`.
- `verify_ledger(ws)` → `ledger-invalid` when: the file is a symlink or unreadable; a line is not a JSON
  object with exactly the keys above; `seq` not 1..n in order; `version` ≠ `<seq>.0`; `kind` not `lock` for
  seq 1 and `amendment` after; `previous_hash`/`previous_version` not equal to the prior record;
  `approved_by` ≠ `human`; the referenced gate file is missing, not kind `design-lock`, not answered
  `approve` by `human` (the crash window between the hook's append and `gates.answer`'s update is
  therefore fail-closed until the human answers that open gate again — idempotent); the copy directory is
  missing or `content_hash` ≠ the hash of the copy's content.
- `campaign_verdict(project, ws, metrics) -> Verdict(refused: bool, rule, message, warnings)` — 5.6.
- CLI: `scieflow design lock <slug> [--json]`, `scieflow design amend <slug> --summary S --rationale R
  [--json]`, and `show` gains status, version, hashes, pending gate and the trail (all records, oldest
  first). `lock`/`amend` open gates as actor `agent` (as `scieflow gate open` does, `gates.py:174-178`).
- Gate kind (`schemas/gates.yml`, after `data-release`):
  `design-lock: {requires_human: true, help: "Lock or amend a run's study design and analysis plan"}`;
  `gates.py`: `DESIGN_LOCK_KIND = "design-lock"` and, in `answer`, after the data-release hook:
  `if gate["kind"] == DESIGN_LOCK_KIND: from scieflow.core.design import lock; lock.on_answer(project,
  ws, gate, answer, actor)`.
- Dispatch protection (`core/data/dispatch.py:251-259`): after the `released` block, append
  `ws/design/lock.jsonl` (is_file, not symlink) and `ws/design/locked` (is_dir, not symlink), resolved,
  like `released/`. A sandboxed agent therefore cannot write the ledger or the locked copies once they
  exist; `verify()` proves it (t4 test with the real bwrap).

### 5.6 Campaign refusal (`scieflow experiment run|sweep`)

`experiments/cli.py`: after `_guard(budget_run)` in `run` (`:95`) and `sweep` (`:118`), call
`_design_guard(budget_run, campaign)`:

```
if run_dir is None: return                          # not inside a run: unchanged
v = lock.campaign_verdict(project, run_dir, campaign.metrics)
for w in v.warnings: echo "warning: design: <w.message>" to stderr; emit note.design_warning(rule, campaign)
if v.refused: emit job.refused(reason="design", rule=v.rule, campaign=campaign.name); raise ClickException(v.message)
```

`campaign_verdict` rules, in this order (first refusal wins; `computational` is `study_type ==
computational` of the current file, or of the in-force copy when the current file is unreadable):

| State | Rule id | Non-computational | Computational |
|---|---|---|---|
| `none` | — | allowed (today's behaviour) | allowed |
| `ledger-invalid` | `design-ledger-invalid` | **refuse** | **refuse** (M-D1-D6) |
| `modified` | `design-modified` | **refuse** ("design.yml differs from its lock (version …); amend it with `scieflow design amend` or restore it") | **refuse** |
| `invalid` (unreadable, study type unknown) | `design-invalid` | **refuse** (fail closed) | — |
| `draft`/`pending` with errors | `design-invalid` | **refuse** | warn |
| `draft`/`pending` | `design-unlocked` | **refuse** ("lock it first: `scieflow design lock`") | warn |
| `locked`, a campaign metric not named by any SAP outcome `metric` | `out-of-sap` | **refuse** (names the metrics, "amend the SAP to add them") | warn |
| `locked`, all metrics named | — | allowed | allowed |

`campaign_verdict` returns "allowed" at once, reading no schema and writing nothing, when the run has
neither `design/design.yml` nor `design/lock.jsonl` (so every existing run and test is unaffected). The
project is `Project(run_dir.parent.parent)` (the root that holds `workspace/<slug>`; `run_for_path`
guarantees that shape; `Project.discover` would need `config/agents.yml` above the run, which a test
fixture root does not have).
Refusal happens before any run, spend or container start. Messages name the rule, the campaign name and
metric names (campaign configuration, not data). Bypass by pointing `--experiments-dir` outside the run is
the same residual the budget guard has (R3).

### 5.7 CLI surface and exit codes

New group `design` (`src/scieflow/cli.py` `GROUPS`, no extra; `help: "Study design and analysis plan:
check, lock, amend."`): `init <slug> --type T [--title S] [--force]`, `check <slug> [--json]`,
`render <slug>`, `show <slug> [--json]`, `lock <slug> [--json]`, `amend <slug> --summary --rationale
[--json]`, `guidelines [--json] [--check]`. `experiment power …` in the experiments group (needs the
`experiments` extra for scipy). Exit codes: 0 ok; 1 refused or check errors (Click's `ClickException`,
documented row in `docs/cli.md:20`); 2 usage. No new code in the exit-code table (no edit there, which
also avoids the M-C1 hunk). `--json` on every read; JSON output is deterministic. `init` refuses an
existing file unless `--force` and never overwrites a locked design (`modified` would follow; refuse
`--force` when a ledger exists).

### 5.8 Skills, docs and rule 19

- `skills/research-loop/SKILL.md`: a "Study design" paragraph under the phase table — in iteration 1, for
  any non-computational question, the `hypothesize` phase also writes `design/design.yml`
  (`scieflow design init <slug> --type …`), sizes it with `scieflow experiment power … --out
  design/power/<name>.json`, runs `scieflow design check` until no errors, then `scieflow design lock` and
  `gate wait`; the `experiment` phase starts only after the human approved. Never do the arithmetic.
- `skills/experiment-cycle/SKILL.md`: section "Locked design" (the refusal table of 5.6 in two
  sentences; campaign metrics must be SAP outcome `metric`s; an out-of-SAP campaign needs
  `scieflow design amend`, a human-only gate).
- `src/scieflow/experiments/skills/experiment-designer/SKILL.md` step 4: "If the run has
  `design/design.yml`, use only metrics that SAP outcomes name (`scieflow design show <slug> --json`
  `sap_metrics`)."
- `docs/experiments/design.md` (new; nav "Study Design and SAP" after "CLI Reference"): format, check
  rules, lock/amend/ledger, campaign rule, power families and formulas with their references, reporting
  checklists and their **unverified** status, the data-level warning, residuals R1–R6, credits
  (researcher `protocol.py` semantics, MIT, re-implemented; statsmodels power formulas, BSD-3,
  re-implemented over scipy).
- `docs/cli.md`: section `## \`scieflow design\`` after the `scieflow data` section (before `:41`), and
  `power` under `docs/experiments/cli.md` (t1).
- Root `AGENTS.md` rule 19: the protected edit of section 3.

### 5.9 Security and integrity properties (reviewed in every ticket that touches them)

| Threat | Property |
|---|---|
| An agent locks or amends a design itself | `design-lock` requires a human (schema + `gates._check_agent_may_answer` + `on_answer` actor check) |
| An agent edits a locked design to fit results | content hash ≠ in-force → every campaign refused (`design-modified`), for every study type |
| An agent rewrites the ledger or locked copies | both protected read-only inside dispatches once they exist (`verify()` proven); `verify_ledger` cross-checks gate, chain and copy hashes; residual R2 |
| A stale gate approves a different file | `on_answer` compares the current hash with `pending.json` and the gate question prefix; mismatch keeps the gate open |
| A hand-edited sample size | `power-mismatch` error (recomputation), locked together with the design |
| A model doing statistics in context | rule 19 + skills: power only via `scieflow experiment power` |
| Patient data in messages | messages name rules, key paths, campaign and metric names only; design files are L0 metadata, scanned like any run file by M-B |
| Undeclared L2 need | `level-undeclared` warning only; ScieFlow never declares or lowers a level (D10, D11) |
| Fail-open parsing | every loader fail-closed (`Invalid`, `ledger-invalid`) and a refusal for non-computational designs |

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`,
provider anthropic); Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`). Usage limits are
checkpointed pauses, no model swap. Every card: no claude/codex/agy, no live probe, no network (git
ls-remote/fetch in t0 only), no push/PR, no credentials, no patient data (synthetic fixtures only), never
edits an `INFLIGHT` path, a sealed file, `config/sandbox.yml`, `events.py`, `CREDITS.md`/`VENDORED.md` or
any `AGENTS.md`. Every implementation and fix card runs the `INFLIGHT` guard and reports its exit code.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-d1-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-d1-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-d1/junit G6_CACHE_DIR=~/.cache/scieflow-m-d1 RUN/tools/g6_pytest.sh WT <head-sha> m-d1-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`).
- **Sealed probes:** `… g6_pytest.sh WT <head> m-d1-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py` (on 99ee8ec: 4 + 201 passed), also under the lock.
- **Load-flake nodes**: the four named in the installer's standing rules, one rerun, both results
  recorded. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-d1/executions.jsonl` and states RED and GREEN evidence in its result.
- Sandbox tests use the real `bwrap` (`/usr/bin/bwrap`), as `tests/core/test_sandbox_data.py` does.

### t0 — worktree, plan copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the four ancestor checks (else `BASE_RED` with the
   reason); `worktree add -b dev/m-d1 RUN/scratch/m-d1-wt <base>` (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. Expected on 99ee8ec (M-C1 t0 measured the identical tree): 5395 passed, 22 skipped;
   sealed 4 + 201 passed. If M-C1 merged first, the counts differ; t0 records what it measures. Only the
   load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 and the hotspot lines of section 2 on `base`; write
   `reviews/m-d1-t0-baseline.md` (per cite: still present / moved (new line) / changed). Inventories:
   (a) whether `base` contains M-C1 (`git merge-base --is-ancestor` of the M-C1 tip named in
   `RUN/scratch/m-c1/publish.json` if present; else "not merged"); (b) every existing test that pins the
   gate-kind set, the `scieflow` group list or root `AGENTS.md` rule numbering (expected: only
   `tests/core/data/test_release.py:293`); (c) whether `old` of the protected edit occurs exactly once in
   `AGENTS.md` on `base`.
4. Copy `reviews/m-d1-plan.md` byte for byte to
   `docs/superpowers/plans/2026-10-27-m-d1-design-and-sap.md`; commit it alone
   ("docs(plans): M-D1 design and SAP plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — `scieflow experiment power` (Haiku) → t1r (Sonnet)

Files: `src/scieflow/experiments/power.py`, `src/scieflow/experiments/cli.py` (the `power` command
only), `docs/experiments/cli.md`, `tests/fixtures/power/reference.json` (byte copy of
`reviews/m-d1-power-reference.json`; t1 checks its sha256 against the run input first),
`tests/experiments/test_power.py`, `test_power_cli.py`, `test_power_statsmodels.py`.

RED → GREEN:
1. Every case of `reference.json` reproduces within its tolerance through `power.solve(...)` (module API)
   and through `scieflow experiment power … --json` (CliRunner); `n1_required`/`n_required`/
   `events_required` equal the ceilings; the cross-check integers hold (64, 393, 26 per group; 159 total
   for ANOVA k = 3; 85 for r = 0.30; 66 events for HR 0.5). (GAP acceptance: power results match
   reference values.)
2. `test_power_statsmodels.py`: `importorskip("statsmodels")`; for the t/anova/proportions cases, the
   statsmodels classes give the same numbers within tolerance. Skipped on this host (recorded).
3. Exactly-one-unknown: none unknown or two unknown → exit 2 with the rule; `--alternative smaller` → exit
   1; `p1 == p2`, `HR == 1`, `ratio 0`, `n` ≤ family minimum, `alpha 0.6`, `power ≤ alpha` → exit 1 naming
   the input.
4. Numerical safety: a monkeypatched power function returning NaN → `PowerError`, never a result; an
   effect so small that n > 10⁷ → `PowerError`; a t-ind solve at d = 0.05 succeeds (bracket from below,
   no NaN region); `t-ind` power at d = 0.5, n1 = 5000 is 1.0 (the lower-tail NaN rule of 5.4) and a
   NaN lower tail at noncentrality < 10 (monkeypatched) is `PowerError`.
5. Determinism: two identical calls give byte-identical `--json` output; `--out` writes the same bytes,
   refuses an existing file without `--force` and a symlink always.
6. `recompute(doc)` returns the same `result` for every reference case and detects a changed
   `n1_required` (used by t3's `power-mismatch`).
7. Docs: `docs/experiments/cli.md` has a `## scieflow experiment power` section naming all seven
   families and the solve modes (string test).

t1r checklist: formulas equal 5.4 (read the code against the table, both tails for two-sided); NaN never
becomes a power; inputs validated before any computation; output carries assumptions and method; no new
dependency in `pyproject.toml`/`uv.lock`; tests meaningful (not tautological against the module's own
output: the fixture is pinned); INFLIGHT guard 0.

### t2 — reporting checklists and the `design` group (Haiku) → t2r (Sonnet)

Files: `config/reporting/index.yml` + the eight `config/reporting/<id>.yml` (built from
`reviews/m-d1-reporting-skeletons.yml`, sha256 checked first; `version` → `version_label`, the other
fields and every item copied exactly, `verification` block of 5.3 added verbatim), `schemas/reporting-checklist.yml`,
`src/scieflow/core/design/{__init__,reporting,cli}.py`, `src/scieflow/cli.py` (one `GROUPS` entry),
`tests/core/design/test_reporting.py`. (`schemas/design.yml` comes in t3; until then t2 reads the study-type
list from a constant in `reporting.py` that t3 replaces by the schema read, with a test that both agree.)

RED → GREEN:
1. `validate_all(repo)` returns no problem for the shipped files (GAP acceptance: checklist files
   validate); item counts 37, 34, 34, 27, 42, 33, 21, 28; every file `verification.status: unverified`.
2. Each corruption on a temporary copy is reported with its rule: orphan file, listed id without a file,
   id ≠ stem, duplicate item id, bad section/evidence enum, empty topic, 61-character topic, verified
   without `verified_by`, bad DOI, unknown key, unreadable YAML, symlinked file.
3. Every item of every shipped file equals the skeleton (ids, topics, sections, evidence, group) — the
   test loads the copied skeleton from `tests/fixtures/reporting/skeletons.yml` (byte copy of the run
   input) so later edits to checklists are deliberate.
4. `scieflow design guidelines --json` lists the eight with status; `--check` exits 0; on a corrupted copy
   (`--root` test hook or monkeypatched root) exits 1 with the rules.
5. `scieflow --help` lists `design`; the lazy group loads without the `experiments`/`research` extras
   (no scipy/jsonschema import at module import time).

t2r checklist: files equal the skeleton exactly (no invented or reworded item); the unverified status and
note are present on every file; validator fail-closed; INFLIGHT guard 0.

### t3 — design model, checks, render, init/check/show (Haiku) → t3r (Sonnet)

Files: `schemas/design.yml`, `src/scieflow/core/design/{model,render,templates}.py`, `cli.py`
(`init check render show`), `tests/core/design/test_model.py`, `test_render.py`, `test_cli_check.py`,
synthetic fixtures `tests/fixtures/design/{rct,cohort,computational,diagnostic}.yml` and one power file.

RED → GREEN:
1. Each fixture loads and checks with 0 errors; their warnings are exactly the expected rule ids.
2. One test per error rule of 5.2 (a minimal mutation of a valid fixture → exactly that rule, message
   names the key path, never the free-text value); fail-closed loading (symlink, unreadable, YAML error,
   empty, non-mapping, a YAML date → `json-value`).
3. One test per warning rule, including `level-undeclared` against a run whose `data.yml` declares
   nothing (and none when it declares an L2 path), and `power-unverified` with `scieflow.experiments.power`
   made unimportable.
4. `power-mismatch`: a power file whose `n1_required` was edited → error; the unedited file → none.
5. `content_hash`: key order and YAML formatting do not change it; any value change does; equals the
   canonical-JSON SHA-256 of 5.2 (pinned hex for one fixture).
6. `render` is byte-identical across two calls, starts with the generated-from line carrying the hash,
   and contains every section heading of 5.2 in order.
7. `design init <slug> --type rct` writes a template whose `check` reports only `todo` (and
   template-expected) errors; refuses an existing file; `--force` refused when a ledger exists (t4
   completes that case; t3 tests the plain refusal).
8. `design check --json` and `design show --json` are deterministic; `show` reports `status` `none`,
   `invalid` or `draft` (lock states come in t4); exit codes 0/1 as 5.7.
9. `reporting.py`'s study-type constant is replaced by the `schemas/design.yml` read; the t2 tests still
   pass.

t3r checklist: every rule of 5.2 implemented and tested; no value echo; fail-closed; schema file and code
agree (a test diffs the enumerations); INFLIGHT guard 0.

### t4 — lock, amend, gate hook, ledger protection (Haiku) → t4r (Sonnet)

Files: `schemas/gates.yml`, `src/scieflow/core/gates.py`, `src/scieflow/core/design/lock.py`, `cli.py`
(`lock amend`, `show` lock state, `init --force` refusal with a ledger), `src/scieflow/core/data/dispatch.py`
(protected entries only), `tests/core/data/test_release.py` (the one pin at `:293`),
`tests/core/design/test_lock.py`, `test_gate_hook.py`, `test_sandbox_design.py`.

RED → GREEN:
1. Lock round trip: `design lock` on a valid design → open `design-lock` gate (`requires_human`), pending
   written; human `approve` through `service.answer_gate` → ledger record seq 1 version `1.0`, copy under
   `locked/v1/`, pending removed, `show` = `locked`. (GAP acceptance: lock hash test.)
2. Agent cannot answer: `gate answer … --as-agent` refused (autonomous run, in-scope gate); `on_answer`
   with `actor="agent"` raises `GateError` itself.
3. Stale gate: edit `design.yml` after `lock`, then `approve` → `GateError`, gate still open, no ledger
   line; `reject` → pending removed.
4. Second lock refused with the amend hint; `lock` with errors refused (rules listed); `lock` while a
   pending gate is open refused; a stale pending (gate answered elsewhere) is cleared.
5. Amend: unchanged file → "nothing to amend"; empty summary or rationale refused; a change → diff file,
   gate; approve → seq 2, version `2.0`, `previous_hash` = v1 hash, trail lists both. (GAP acceptance:
   amend hash test.)
6. Tamper detection (`verify_ledger`): edited line, removed line, seq gap, wrong version, missing copy,
   copy hash mismatch, gate file deleted, gate answered `reject`, gate answered by `agent`, symlinked
   ledger → `ledger-invalid`; the crash window (record present, gate still open) → `ledger-invalid`, and a
   second human `approve` of that gate completes it without a second record (idempotent).
7. Locked power file: editing `design/power/*.json` after the lock → `modified`.
8. Dispatch protection: with `lock.jsonl` and `locked/` present, `dispatch.decide` lists both in
   `protected`; with the real bwrap, `verify()` proves they are not writable inside a cloud dispatch
   sandbox, while `design.yml` stays writable; absent files are not listed; a symlinked `locked` is not
   listed. All M-B dispatch tests unchanged and green.
9. Gate-kind pin: `tests/core/data/test_release.py:293` becomes
   `assert set(table) == BASE_KINDS | {"data-release", "design-lock"}` (the only existing line changed).

t4r checklist: hook order (before the gate is marked answered), idempotency, fail-closed ledger, no
agent path to a ledger write, the dispatch change limited to the protected list, every M-B test green,
section 5.9 rows for lock/amend; INFLIGHT guard 0.

### t5 — campaign refusal, skills, docs, rule 19 (Haiku) → t5r (Sonnet)

Before t5 the operator applies the protected edit (installer step `pe_t5`); t5 commits that file alone.

Files: `src/scieflow/experiments/cli.py` (`_design_guard`), `src/scieflow/core/design/lock.py`
(`campaign_verdict`), `skills/experiment-cycle/SKILL.md`, `skills/research-loop/SKILL.md`,
`src/scieflow/experiments/skills/experiment-designer/SKILL.md`, `docs/experiments/design.md`, `docs/cli.md`,
`mkdocs.yml`, `AGENTS.md` (commit of the applied edit), `tests/experiments/test_design_guard.py`,
`tests/test_docs_design.py`.

RED → GREEN (campaigns run `--direct` with the existing `project` fixture of
`tests/experiments/conftest.py`, whose demo campaign `gain-sweep.yaml` has `metrics: [ssim, mse]`, and a run
made as in `tests/experiments/test_budget_counting.py:9-14`; the fixture root gets a copy of the repo's
`schemas/` so the design and gate schemas load; locks are made through `lock.lock` + `service.answer_gate`
as a human, never by writing the ledger):
1. Locked RCT design whose outcomes name `metric: ssim` only; the demo campaign → `sweep` exit 1,
   message names `out-of-sap` and `mse`, no run directory, no spend, one `job.refused` event with
   `reason: design`. (GAP acceptance: a campaign whose outcome is not in the SAP is refused.)
2. A locked design whose outcomes name both `ssim` and `mse` → the same sweep runs as today.
3. Unlocked non-computational design → `design-unlocked` refusal; locked then edited → `design-modified`
   refusal; broken ledger → `design-ledger-invalid`; unreadable design.yml → `design-invalid`.
4. Computational design: unlocked or out-of-SAP → runs, stderr warning, one `note.design_warning`
   event; locked then edited → refused (M-D1-D6).
5. No `design/` in the run, or `--experiments-dir` outside any run → behaviour and events identical to
   today (existing tests in `tests/experiments/` unchanged and green).
6. `scieflow experiment run` (single run) obeys the same verdicts as `sweep`.
7. Docs test: `docs/experiments/design.md` exists, is in the `mkdocs.yml` nav, names every `scieflow
   design` subcommand, every power family, the unverified status, both credits; `docs/cli.md` has the
   `scieflow design` section; root `AGENTS.md` contains rule 19's first line
   (`19. **Study design** (\`docs/experiments/design.md\`).`); the three skills mention
   `scieflow design lock` / the SAP-metric rule; `tests/test_docs_role.py` still passes (no role-less
   `agent run` invocation added).
8. `mkdocs build --strict` into `RUN/scratch/m-d1/site` if mkdocs is in the venv (else recorded "not
   available").

t5r checklist: the guard runs before any execute/spend; refusal table of 5.6 exactly; warnings only for
computational; docs ↔ code; rule 19 equals the approved text byte for byte; INFLIGHT guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by added tests); `mkdocs build --strict` (or "not
available"); INFLIGHT guard over `base..tip`; linear history, at least one commit per ticket t0..t5;
`git diff base..tip -- tests/` removes lines only at `tests/core/data/test_release.py:293`;
`config/sandbox.yml`, sealed files, `events.py`, `CREDITS.md`, `VENDORED.md`, `CLAUDE.md` and every
`AGENTS.md` except the root rule-19 commit absent from the diff; `git diff base..tip -- pyproject.toml
uv.lock` empty. Writes `RUN/scratch/m-d1/pr-body.md` (plain English: the four capabilities, decisions
M-D1-D1..D8, absolute counts, residuals R1–R6, credits, "please merge with a merge commit") and
`reviews/m-d1-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every section-5 item; the GAP acceptance criteria — power results match
the reference values, lock/amend hash tests, out-of-SAP campaign refused, checklist files validate;
decisions honoured; rule 19 byte-exact), code and security (section 5.9 table, fail-closed loaders, hook
order, dispatch protection proven by `verify()`, no value echo), evidence (re-run memory-capped:
`tests/experiments/test_power*.py tests/core/design tests/experiments/test_design_guard.py
tests/core/data tests/test_docs_design.py`, confirm the branch result's absolute counts, re-run the
INFLIGHT guard, `pr-body.md` accurate). Output `reviews/m-d1-final-review-r<N>.md`. PASS → the installer's
publish step; CHANGES → final_fix (new commits, full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-d1-run.yml`, linear)

```
t0        Haiku   worktree, plan copy, green base     DONE→t1, BASE_RED→escalate
t1        Haiku   experiment power                    DONE→t1r
t1r       Sonnet  review                              PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix                                 DONE→t1r
t2/t2r/t2f        reporting checklists, design group  same shape → t3
t3/t3r/t3f        design model, check, render         same shape → t4
t4/t4r/t4f        lock, amend, gate hook, protection  same shape → t5   (installer inserts pe_t5)
t5/t5r/t5f        campaign refusal, skills, docs      same shape → branch
branch    Haiku   whole-branch checks + PR body       DONE→final, RED→escalate
final     Sonnet  final whole-branch review           PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix                                 DONE→final
```

Caps `{max_cards: 60, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/m-d1-run/<step>-r${round}.json`; implementation and fix results require `verdict,
summary, head, base`; reviews `verdict, summary, head, base` with PASS/CHANGES/FAIL. Inputs: handoff,
decisions, gap, `powerref` (`reviews/m-d1-power-reference.json`), `reporting`
(`reviews/m-d1-reporting-skeletons.yml`); the installer adds `plan`. The installer also adds the protected
edit step before t5, `publish`, `refresh`/`refresh_review` and `chain`, and appends the standing rules to
every brief.

## 8. Risks and residuals (stated, not hidden)

- **R1 No real statsmodels comparison yet.** The reference values are this implementation's formulas on
  scipy 1.18.1, cross-checked against published integers; the statsmodels comparison test is skipped
  until statsmodels is installed (M-D2). Two independent integer checks per family are not a proof of
  every corner (e.g. large ratios).
- **R2 File-based state is writable by a sandboxed agent before protection exists.** `gates/` and
  `events.jsonl` are never protected, and `lock.jsonl`/`locked/` only once they exist; an agent could
  forge a first ledger plus a matching answered gate file. `verify_ledger` raises the bar (chain, gate,
  copy hashes) but cannot prove a human acted. Same class as M-B's `data.yml`; a host-side signed ledger
  is a follow-up (F2).
- **R3 Guard reach.** Like the budget guard, the campaign refusal applies only when `--experiments-dir`
  lies inside a run; a campaign recorded elsewhere is not checked (and not part of the run).
- **R4 Checklists unverified and partly superseded.** Written offline from knowledge; topics are our own
  labels; DOIs not resolved; TRIPOD+AI and SPIRIT 2013 top-level only; CONSORT 2025 and SPIRIT 2025 not
  included. M-F (journal research, Gateway fetch) or a human verification must precede any submission
  use; every file and `design check` say so.
- **R5 Test-per-outcome not enforced.** Campaigns have no test; M-D2's `stats.json` adds the check.
- **R6 Survival power is an approximation** (Schoenfeld; proportional hazards, no accrual/dropout model);
  stated in the output's assumptions.
- Hotspots with the M-C1 lane: `core/data/dispatch.py:251-259`, `docs/cli.md`, `mkdocs.yml` (section 2);
  resolved by the installer's refresh step.

## 9. After M-D1

Queue (`continuity/milestones.yml`): M-D2 (`after: [m-d1]`) builds `scieflow.experiments.stats` on the
locked SAP (tests per outcome, `stats.json`, replicates per D21) and can add statsmodels (R1); M-F uses
`config/reporting/` for `manuscript check --guideline` and journal research (D12) and is where checklist
verification belongs (R4); M-H reuses the lock semantics for SR protocols. Follow-ups surfaced while
planning: F1 a `CREDITS.md` line for the re-implemented researcher semantics once M-C1's file exists;
F2 host-side integrity for gate files and ledgers (R2); F3 a run-page design panel in the web app (D7:
the CLI is complete; the dashboard already lists `design-lock` gates); F4 CONSORT 2025 / SPIRIT 2025
checklists after verification.
