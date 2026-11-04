# M-H plan: systematic-review vertical (round 1)

Pipeline `m-h-plan`, step plan, round 1, epoch 1. Card `t_fbfddefa`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261031_150613_199ffc`). Written
2026-10-31. Previous review: none (round 1).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt.

## 0. Inputs and references used

Immutable inputs (sha256 re-computed on this card, all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…7da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…f85c |

Scope source: GAP-ANALYSIS.md section 8 row M-H (`GAP-ANALYSIS.md:295`): "Protocol lock, dual screening
by two agents, κ, adjudication gate, PRISMA flow/checklist, RoB/GRADE worksheets, eval harness + gold
sets"; acceptance "PRISMA counts derive from `events.jsonl`; blinded conflicts surfaced; κ hand-checked;
harness prints Wilson CIs"; depends on M-C2, M-D1; features #9 and #11 (`GAP-ANALYSIS.md:73,75`).
DECISIONS D14 (systematic / living review in scope, `DECISIONS.md:58`), D15 (vendor researcher's pure
modules verbatim, pinned v1.0.0, credited; reimplement transport/cache/ledger), D7 (CLI parity), D9–D11
and D23 (data protection), D16 (PubMed/S2 off by default).

Code read read-only from git objects (`git show <ref>:<path>`, `git grep <ref>`); no checkout was
touched.

- `M` = remote `main` = `8b374803c2eb6cad0df981d466ba097b973f3c84` (`git ls-remote
  git@github.com:jedimik/ScieFlow.git refs/heads/main` on this card; objects present locally). It is the
  merge of PR #24 (M-C2). Every `path:line` below is on `M` unless marked otherwise.
- `R` = the read-only researcher v1.0.0 snapshot `/home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher`
  (snapshot e4ddbf4, the same copy M-C1/M-C2 vendored from; `VENDORED.md` "Pin" on M).
  Upstream sha256 on this card: `core/researcher_core/screen.py` 28072bf60184544de4c1e3d6f4f78e3c9597ffdd2ec8dfb8b181bfea4eeda01f,
  `core/researcher_core/sr_prisma.py` def64bad0fcd69d132ac69df47dfe5d47fc1412a071b021e8e861490daf8d3ca,
  `core/researcher_core/provenance.py` 6c77c217333fc4eea106ae5c8edcabec975503fb214942b109539ef20c3d2002
  (equal to the pin already recorded for `provenance_ts.py` in UPSTREAM.json),
  `core/researcher_core/protocol.py` 19bd7056b2d89435d8ae9e470e30a8976aa4bd73eec872fda8d8d35e8272c5e7
  (not vendored, M-H-D1), excerpt `provenance.py` lines 527–625 sha256
  29ebb44a0a1c9c410f8cee341c683734b4943b422bab14dee693125d94e71836.
- Dependencies: `RUN/scratch/m-c2/publish.json` (`merged: true`, `pushed_head`
  a59df13561a4ee115354a82f95dc301060853c24, main after merge 8b37480) and `RUN/scratch/m-d1/publish.json`
  (`merged: true`, `pushed_head` c350593ee6235049073ab5e7207f881fb60ba307). Both are merged, so the plan
  is not BLOCKED.

## 1. For the human: what M-H adds, in plain words

M-H lets a ScieFlow run carry out a systematic review (and keep it alive as a living review) with the
discipline the method requires, and with every number in the PRISMA report coming from code, not from an
agent.

1. **The protocol is locked before anything is screened.** The review question, inclusion and exclusion
   criteria, the search strategies, the screening settings, the planned appraisal (risk-of-bias tool,
   GRADE) and the planned synthesis are written into the run's study design (`design/design.yml`,
   study type `systematic-review`, new `review:` block) and locked with the existing human-only
   `scieflow design lock` gate (M-D1). Every later change is an amendment with a summary, a rationale and a
   diff, again approved by you (PRISMA 2020 item 24). Search and screening refuse to run on an unlocked or
   edited-after-lock protocol.
2. **Searches run exactly as locked.** `scieflow sr search` runs every locked strategy through the existing
   literature sources and the provider Gateway (leak check, data-level rules, replay mode all apply),
   merges duplicates with the vendored dedupe, numbers the unique records `R00001…`, and records how many
   records each source returned and how many duplicates were removed. A round is recorded only when every
   source answered; a living review re-runs the strategies and only new records enter screening.
3. **Two agents screen independently.** For each batch ScieFlow writes the records (as untrusted data) and
   the locked criteria into a prompt; two different primary agents (role `research.sr-screeners`, default
   codex and claude) each decide include/exclude, with a locked exclusion reason at full-text stage.
   `scieflow sr screen import` accepts the two answer files only together, only when both cover the batch
   exactly, and only when the run's job log shows a finished dispatch of each screener.
4. **Disagreements go to you, blinded.** `scieflow sr conflicts` lists the records the two screeners
   disagree on, showing you the record and the criteria but **not** who voted what, and opens an
   `sr-adjudication` gate that only a human can answer. You record a decision and a rationale per record
   (`scieflow sr adjudicate …`), then answer the gate `resolved`. Cohen's kappa between the two screeners is
   computed from the decisions (`scieflow sr kappa`), never typed in.
5. **The PRISMA flow and checklist are derived.** `scieflow sr prisma` recomputes the PRISMA 2020 flow
   (identified per source, duplicates, screened, excluded, reports sought / not retrieved / assessed,
   excluded with reasons, studies included) from the run's `events.jsonl` every time, and maps the PRISMA
   checklist items to the evidence in the ledger or marks them as yours to write. A consistency check
   (`scieflow sr verify`) refuses everything if the ledger and the files disagree.
6. **Risk-of-bias and GRADE worksheets.** One worksheet per included study for RoB 2, ROBINS-I or
   QUADAS-2, and one GRADE entry per outcome. ScieFlow checks them (every domain judged with support, the
   overall judgement never milder than the worst domain, GRADE certainty computed from the recorded
   downgrades/upgrades) and records them; the judgements themselves are made by a person or an agent.
7. **An evaluation harness with confidence intervals.** `scieflow sr eval` compares each screener (and the
   adjudicated result) with a human-labelled calibration sample and prints sensitivity, specificity and
   accuracy with Wilson 95% confidence intervals. A developer harness (`scripts/evals/run_axes.py`) runs the
   offline gold sets (deduplication, kappa, PRISMA scenario, screening calibration) and prints the same.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-H-D1 The protocol is the `review:` block of the run's locked design.** No second lock ledger: the
  M-D1 design lock (human-only gate, hash-chained ledger, amendment diffs) already re-implements
  researcher's `protocol.py` semantics (M-D1-D2, `src/scieflow/core/design/lock.py:9-12`), so
  `protocol.py` is not vendored. (Alternative: a separate `sr/protocol.yml` with its own lock gate.)
- **M-H-D2 The review ledger is `events.jsonl`.** Six new `sr.*` event types; every multi-event operation
  ends with one `sr.committed` event, and readers ignore operations without it (crash safety). No count is
  stored as truth; files written by `sr prisma --write` are renderings.
- **M-H-D3 Vendor `screen.py` and `sr_prisma.py` verbatim (D15)**, with recorded one-line import patches,
  plus a verbatim excerpt of `provenance.py` (lines 527–625: `PrismaCounts`, `derive_prisma`,
  `_as_count`) and a ScieFlow-authored stand-in ledger interface (`_ledger.py`), the same pattern as M-C2's
  `_deps.py`.
- **M-H-D4 Dual screening is mandatory: exactly two distinct primary agents.** Decisions are
  `include`/`exclude` only (an unsure screener includes at title/abstract stage); a full-text exclusion
  must name a locked exclusion criterion. Two screeners of the same model family is allowed with a printed
  warning. (Single-screener reviews and Fleiss' kappa for three or more are out of scope.)
- **M-H-D5 Adjudication is human only**, through the new `sr-adjudication` gate
  (`requires_human: true`) on a blinded package. No agent adjudicator in M-H.
- **M-H-D6 A search round is all-or-nothing, one page per source.** If any source of any strategy does not
  answer, nothing is recorded. The search modules fetch one page (`openalex.py:64` caps 200 per page), so a
  source that returns as many records as asked is flagged `capped` in the round and the PRISMA report.
  Pagination is a follow-up (F1).
- **M-H-D7 Record ids are a run-wide sequence `R00001…`**, assigned in first-identification order; a
  record already in the corpus is never re-merged retroactively.
- **M-H-D8 Screener independence is procedural, not enforced by the sandbox.** The two screeners get
  identical prompts and separate output files; the import checks the job log for a finished dispatch of
  each. The sandbox can still let one dispatched screener read the other's file if it finished first; a
  sandbox mask for this would be a sandbox change (follow-up F3). Stated as residual R1.
- **M-H-D9 RoB/GRADE are worksheets with deterministic checks**, single assessor, tool definitions shipped
  as `config/sr/*.yml` marked `unverified` until a human checks them against the cited sources (the M-D1
  reporting-checklist pattern). No pooled effect is computed (meta-analysis is out of scope).
- **M-H-D10 Evaluation**: the user-facing `sr eval` (screening calibration against a human-labelled
  sample) and the developer harness `scripts/evals/run_axes.py` (axes dedup, kappa, prisma, screening).
  The identity gold set keeps its existing test (`tests/research/kernel2/test_identity_gold.py`); it is
  not duplicated in the harness.
- **M-H-D11 A new command group `scieflow sr`** (research extra). D7 is met by the CLI; a web panel is a
  follow-up (F4).
- **M-H-D12 Two protected one-line-region edits**, anchored outside the region that M-G's pending
  protected edit of `src/scieflow/research/AGENTS.md` rewrites (section 3), so either milestone can land
  first without breaking the other's exact-text edit.
- **M-H-D13 `fanout.collect`**: the per-source collection loop of `search all` is extracted into a
  function that both `search all` and `sr search` call; `search all` output stays byte-identical.

**Protected edits you approve with this plan** (applied by the operator before step t9, exact text in
section 3): one paragraph sentence appended to the Workflows section of `src/scieflow/research/AGENTS.md`,
and one row added to the skills table of the root `AGENTS.md`.

**Proposed PR (push and merge are done by the operator's publish step after the final review):** head
`dev/m-h` → base `main`, one PR, merged with a merge commit. Title: "M-H: systematic-review vertical
(protocol lock, dual screening, blinded adjudication, PRISMA, RoB/GRADE, evaluation harness)".

## 2. Base, dependencies, lanes and worktree

- **Dependencies** (`continuity/milestones.yml:26` `after: [m-c2, m-d1]`): both merged (section 0).
  M-H calls M-D1's `scieflow.core.design` (`lock.state`, `lock.read_ledger`, `model.check`) and M-C2's
  search registry and fan-out (`search/registry.py`, `search/fanout.py`).
- **Lanes**: `parallel: any` (`continuity/milestones.yml:13`). On this card `m-d3-run` is running and
  `m-g-plan`, `m-i-plan` are planning. Overlaps that the installer's `refresh` step handles if another lane
  merges first: M-I plans an events schema (`core/events.py`, GAP row M-I); M-G's pending protected edit
  rewrites the Workflows region of `src/scieflow/research/AGENTS.md` (`pipelines/drafts/m-g-protected-edits.json`),
  which M-H-D12 avoids. M-D3's files (`core/jobs.py`, `experiments/*`) are not touched by M-H.
- **Worktree**: t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`, the
  object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head
  git@github.com:jedimik/ScieFlow.git refs/heads/main`, then requires `git merge-base --is-ancestor <sha>
  <base>` for H-4 `6152d733728b9ed5bf5b571aef475f8071426386`, M-A `be99db2ace0e560fcdb7c00231e906e4aaae67e2`,
  M-E `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`, M-B `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`, M-C1
  `aa7c3792b0b374ccfa6868bb13018a1974ac3435`, and for **M-D1** and **M-C2**: `RUN/scratch/m-d1/publish.json`
  and `RUN/scratch/m-c2/publish.json` must exist with `merged: true` and each `pushed_head` must be an
  ancestor of `base`. Any failure → verdict `BASE_RED` with the reason. Then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-h RUN/scratch/m-h-wt <base>`.
- Commits are local on `dev/m-h`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-h/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-h-<step>/`.

## 3. In-flight, protected and changed files

**INFLIGHT** (the uncommitted paper-lifecycle work in `/home/ubuntu/Github/ScieFlow`; the same 9 paths as
M-A section 3, M-D1 and M-D2 section 3). M-H edits **none** of them:

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
tree during a card, `git -C WT diff --exit-code -- <INFLIGHT>`.

**Never edited**: `config/sandbox.yml`, the DD1-sealed files (`tests/probes/k_task8_binding.py`,
`tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, every
`AGENTS.md` except through the two protected edits below, `src/scieflow/core/sandbox.py`,
`src/scieflow/core/agent_run.py`, `src/scieflow/core/service.py`, `src/scieflow/core/jobs.py` (read through
`jobs.list_jobs` only), `src/scieflow/core/design/lock.py` (called, not changed), `src/scieflow/web/*`,
the search modules `src/scieflow/research/search/{openalex,crossref,europepmc,arxiv,pubmed,semantic_scholar,datacite,registry}.py`,
every already-vendored file under `src/scieflow/_vendor/researcher_core/` (their UPSTREAM.json entries stay
byte-identical), `pyproject.toml`, `uv.lock`.

**Protected edits** (operator-applied before t9, `pipelines/drafts/m-h-protected-edits.json`; each `old`
text occurs exactly once on M, checked on this card; t0 re-checks on base):

Edit 1 — `src/scieflow/research/AGENTS.md` (Workflows paragraph, `src/scieflow/research/AGENTS.md:196-198`
on M). It anchors on the last sentence of the paragraph, which M-G's pending edit leaves unchanged.

old:
```
they ask for an article draft from their data, follow
`src/scieflow/research/skills/paper-draft/SKILL.md`.
```
new:
```
they ask for an article draft from their data, follow
`src/scieflow/research/skills/paper-draft/SKILL.md`.
When they ask for a systematic or living review (a locked protocol, dual screening by two agents,
blinded adjudication, a PRISMA flow and checklist, risk-of-bias and GRADE worksheets), follow
`src/scieflow/research/skills/systematic-review/SKILL.md`. Its counts, Cohen's kappa and PRISMA
numbers come only from the `uv run scieflow sr` commands; never write `sr/` files or `sr.*` events
yourself, and never adjudicate a screening conflict (a human answers the `sr-adjudication` gate).
```

Edit 2 — root `AGENTS.md` (skills table, `AGENTS.md:242` on M). M-G's pending edit inserts its own row
after the same anchor line; the two inserts commute.

old:
```
| Research a target journal; check and package a manuscript | `skills/journal-research/SKILL.md` |
```
new:
```
| Research a target journal; check and package a manuscript | `skills/journal-research/SKILL.md` |
| Run a systematic or living review (protocol lock, dual screening, PRISMA) | `skills/systematic-review/SKILL.md` |
```

Both files are applied in one operator step (`pe_t9`); t9 commits them in their own commit
("docs(agents): systematic-review workflow and skill row (human-approved protected edits) (M-H t9)").
`tools/milestone_chain.py cmd_edits` writes each edit's file from a fresh read, so two edits of the
**same** file would overwrite each other; M-H has exactly one edit per file.

**Permitted changes to existing tests** (the only existing assertions M-H may change; every other existing
test only gains lines): the closed-vocabulary pins that must name the new values —
`tests/core/review/test_gates_events.py:161-164` (`events.TYPES` equality gains `| set(SR_EVENTS)`),
`tests/core/test_service.py:1215-1246` (the frozenset literal gains the six `sr.*` lines),
`tests/core/data/test_release.py:293` (gate-kind set gains `"sr-adjudication"`), and any further pin that
t0 inventory (b) finds enumerating study-type templates, roles, assignments, workflows or menu entries
(for example `tests/core/conftest.py:45-59` `ASSIGNMENTS` if `tests/core/test_agent_config.py:13` needs the
new role). Each such change is listed in the ticket result.

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-31-m-h-systematic-review.md`, `docs/superpowers/specs/2026-10-31-m-h-systematic-review-design.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/_vendor/researcher_core/{screen,sr_prisma,prisma_counts,_ledger}.py` | new: two vendored modules, one excerpt, one ScieFlow-authored interface | t1 |
| `src/scieflow/_vendor/researcher_core/UPSTREAM.json`, `VENDORED.md`, `CREDITS.md` | three new pinned entries, prose | t1 |
| `tests/vendor/test_researcher_screen.py`, `tests/vendor/test_researcher_sr_prisma.py`, `tests/vendor/test_sr_ledger_interface.py` | new (ported upstream tests + interface tests) | t1 |
| `tests/vendor/test_vendored_excerpts.py`, `tests/vendor/test_vendored_integrity.py` | gain the new entries (only if they enumerate files, t0 inventory (c)) | t1 |
| `schemas/design.yml` | `review` top key, its item keys, four enums | t2 |
| `src/scieflow/core/design/model.py`, `templates.py`, `render.py` | review-block checks, SR template skeleton, design.md section | t2 |
| `tests/fixtures/design/systematic-review.yml`, `tests/core/design/test_review_block.py` | new | t2 |
| `src/scieflow/core/events.py` | six `sr.*` types | t2 |
| `src/scieflow/research/sr/{__init__,errors,protocol,ledger,cli}.py` | new package: locked protocol, ledger adapter, CLI group with `protocol`, `status` | t2 |
| `src/scieflow/cli.py` | one `GROUPS` entry `"sr"` (after `"design"`, `src/scieflow/cli.py:74-79`) | t2 |
| `src/scieflow/research/search/fanout.py` | `collect()` extracted from `run_all` (M-H-D13) | t3 |
| `src/scieflow/research/sr/{records,search}.py` | new: corpus, rounds, `sr search`, `sr records` | t3 |
| `src/scieflow/core/agent_config.py`, `config/defaults.yml` | role `research.sr-screeners`, default `[codex, claude]` | t4 |
| `src/scieflow/research/sr/screening.py` | new: prepare, import, resolved verdicts, kappa, status | t4 |
| `schemas/gates.yml`, `src/scieflow/core/gates.py` | gate kind `sr-adjudication`; one hook branch in `answer` | t5 |
| `src/scieflow/research/sr/adjudication.py` | new: blinded conflicts, `sr adjudicate`, gate hook | t5 |
| `src/scieflow/research/sr/{verify,prisma}.py` | new: `sr verify`, `sr prisma` | t6 |
| `config/sr/{rob2,robins-i,quadas-2,grade}.yml`, `src/scieflow/research/sr/appraisal.py` | new: tool definitions, worksheets | t7 |
| `src/scieflow/research/evals.py`, `src/scieflow/research/sr/calibration.py`, `scripts/evals/run_axes.py` | new: Wilson/confusion, `sr eval`, developer harness | t8 |
| `tests/fixtures/sr/{prisma-scenario.yml,kappa.yml,screening-calibration.yml}` | new gold sets (synthetic) | t6, t8 |
| `tests/research/sr/*`, `tests/research/test_evals.py` | new tests | t2–t9 |
| `src/scieflow/research/skills/systematic-review/SKILL.md`, symlink `skills/systematic-review` → `../src/scieflow/research/skills/systematic-review` | new skill | t9 |
| `docs/research/systematic-review.md` (new), `docs/cli.md` (section `scieflow sr`), `mkdocs.yml` (one nav line after `mkdocs.yml:93`) | docs | t9 |
| `schemas/status-research.yml` (workflow `systematic-review`), `src/scieflow/core/menu.py` (`WORKFLOWS` entry) | workflow registration | t9 |
| `tests/test_docs_systematic_review.py` | new docs test | t9 |
| `src/scieflow/research/AGENTS.md`, `AGENTS.md` | the two protected edits, operator-applied, committed by t9 | t9 |

## 4. Current code, verified (on M = 8b37480)

**Design lock (M-D1).** `src/scieflow/core/design/lock.py`: header states the re-implementation of
researcher's `protocol.py` (`:9-12`); ledger `design/lock.jsonl`, locked copies `design/locked/v<n>`
(`:34-37`); `read_ledger` (`:118-144`) with record keys (`:39-41`); `lock`/`amend` open a `design-lock`
gate as the agent (`:380-445`); `on_answer` refuses a non-human actor (`:450-452`) and is the only ledger
writer (`:514-524`); `state()` returns `none|invalid|draft|pending|locked|modified|ledger-invalid`
(`:536-565`). Study types include `systematic-review` (`schemas/design.yml:35-36`); top keys required
`version, title, study_type, question, variables, sap` and optional list (`schemas/design.yml:5-9`);
unknown top keys are errors (`src/scieflow/core/design/model.py:436-447`); `check` (`model.py:364-433`);
TODO values raise only `todo` errors in a fresh template (`tests/core/design/test_cli_check.py:48-55`,
parametrised over all ten study types including `systematic-review`); `templates.template`
(`src/scieflow/core/design/templates.py:12-59`); `render.render` (`render.py:99`). PRISMA 2020 checklist
`config/reporting/prisma-2020.yml` (item ids 1…27 incl. `16a/16b`, `24a/24b/24c`; `verification.status:
unverified`, lines 10-14); `applies_to: ["systematic-review"]` (line 7).

**Gates.** `src/scieflow/core/gates.py`: `DESIGN_LOCK_KIND` (`:35`), `open_gate` (`:72-95`), agents may
not answer `requires_human` gates (`:98-107`), `answer` runs per-kind hooks before marking the gate
answered (`:110-144`, the design-lock hook at `:132-135` lazy-imports `scieflow.core.design.lock`).
Kinds come from `schemas/gates.yml` (`design-lock` line 26, `journal-sources` line 29). The CLI
`gate answer` goes through `service.answer_gate` (`gates.py:221-247`, `service.py:1230-1260`), which does
not itself detect a dispatched caller; human-only CLI writes use `refuse_in_dispatch`
(`src/scieflow/integrations/scite/mcp.py:160-175`, used by `core/run/converse_cli.py:46-48`).

**Events.** `src/scieflow/core/events.py`: closed vocabulary `TYPES` (`:20-44`), `emit(ws, type_, actor,
**data)` assigns `id` (`store.new_id`) and `ts` (`:52-67`), `read` in file order (`:75-84`). Pinned
exactly by `tests/core/review/test_gates_events.py:161-164` and `tests/core/test_service.py:1215-1246`.

**Search.** `src/scieflow/research/search/fanout.py`: canonical `ORDER` (`:34`), `run_all` (`:55-111`) —
query checked once before any source (`:58`), PubMed's own check (`:61-64`), per-source isolation and
statuses (`:66-84`), CSL conversion with id `source:rank` (`:86-91`, `lib/kernel.py:20-38`), vendored
`dedupe` and `rank_records` (`:93-104`), `exit_code` (`:114-123`). `registry.call` maps refusals to exit
codes (`search/registry.py:182-198`), `registry.gateway` builds a run-scoped Gateway (`:166-174`). Each
search module fetches one page: `openalex.py:62-69` (`per-page` ≤ 200), `crossref.py:51-53`,
`europepmc.py:36-40`, `arxiv.py:50-54`, `pubmed.py:140-146` (`MAX_PAGE`), `semantic_scholar.py:61-64`
(`MAX_LIMIT`). Vendored `dedupe` decisions carry `kept_id`/`duplicate_id` (`R:core/researcher_core/dedupe.py:164-177`,
`:386-419`); `CSLRecord` is a plain (non-frozen) dataclass (`_vendor/researcher_core/model.py:523-524`).

**Vendoring.** `VENDORED.md` table and rules (patches recorded in `UPSTREAM.json`, integrity checked by
`tests/vendor/test_vendored_integrity.py`); `_deps.py` is the precedent for a ScieFlow-authored, unpinned
stand-in module (`_vendor/researcher_core/_deps.py:1-9`); `_fuzz.py` provides `token_sort_ratio` and
`ratio` (`_fuzz.py:34,44`). Upstream imports to patch: `screen.py:47` `from rapidfuzz import fuzz`,
`screen.py:50` `from .provenance import ProvenanceEvent, ProvenanceLedger, RunContext`, `sr_prisma.py:51`
`from .provenance import ProvenanceEvent, ProvenanceLedger, derive_prisma`. The ledger surface they use:
`ledger.append(event)` (`screen.py:187,448`), `ledger.iter_events(run_id=, type=)` (`screen.py:196`,
`sr_prisma.py:394`), `ledger.events(run_id=)` (`sr_prisma.py:589`), `run.event(type, payload, ts, *,
source_response_hashes=, event_id=)` (`R:provenance.py:423-441`), event fields `type, payload, event_id,
ts` (`R:provenance.py:283-294`). Upstream tests `R:core/tests/test_screen.py` (26 tests) and
`test_sr_prisma.py` (19 tests) build `ProvenanceLedger(tmp_path/…sqlite3)` and `RunContext(run_id=…)`.

**Agents, roles, workflows.** `ROLES` (`src/scieflow/core/agent_config.py:40-55`), family-compared
`REVIEW_ROLES` (`:60`); defaults `config/defaults.yml:57-73`; `tests/core/test_agent_config.py:13` requires
`set(assignments) == set(ROLES)`. Jobs record `label=agent`, `kind="agent"`, state and times
(`core/agent_run.py:796-797`, `core/jobs.py:33-58`, `list_jobs` `:321`). Research workflows
`schemas/status-research.yml:3-7`; menu `WORKFLOWS` (`src/scieflow/core/menu.py:48-81`); top-level groups
(`src/scieflow/cli.py:14-80`). Skills table in root `AGENTS.md:231-242`; research Workflows section
`src/scieflow/research/AGENTS.md:180-198`. Root skills are symlinks into the research module
(`skills/journal-research` → `../src/scieflow/research/skills/journal-research`).

**Sanitiser.** `src/scieflow/core/sanitize.py` re-exports the vendored sanitiser (`fence_untrusted`,
`sanitize_json_strings`, …) and adds `fence_text` (`:34-38`).

**No existing systematic-review code**: `git grep -n "screening\|adjudicat\|kappa" M -- src` finds only
the vendored `monitor.py` docstring mentions; no `sr` group, no Wilson implementation (`git grep -i
wilson M -- src` finds only an eponym list in `discovery/abstraction.py:85`).

## Part A — Spec

### 5.1 Overview and data flow

```
design/design.yml (study_type systematic-review, review: block)
   └─ scieflow design lock  (human gate, M-D1)            → design/lock.jsonl, design/locked/v<n>/
scieflow sr search  → Gateway (leak check, L2 rules, replay) → sr/rounds/<n>/{hits.jsonl,records.jsonl,round.json}
                                                              events: sr.retrieval, sr.dedup_decision, sr.committed
scieflow sr screen prepare → sr/screening/<stage>/<batch>/batch.json + prompts/sr-<stage>-<batch>-<agent>.md
scieflow agent run --role research.sr-screeners <agent> …   (twice, two agents)
scieflow sr screen import → events: sr.screening_decision ×2n, sr.committed
scieflow sr conflicts → sr/adjudication/<stage>/<gate>/conflicts.{json,md} + gate sr-adjudication (human)
scieflow sr adjudicate (human) → decisions.json;  gate answer resolved → events: sr.adjudication, sr.committed
scieflow sr rob|grade … → sr/rob/<R>.yml, sr/grade.yml; events: sr.appraisal, sr.committed
scieflow sr verify | prisma | kappa | eval | status   (read-only derivations)
```

All SR files live under `workspace/<slug>/sr/` (prompts under `workspace/<slug>/prompts/`, research rule 2).
Stages are exactly `title-abstract` and `full-text` (the vendored `SCREENING_STAGES`).

### 5.2 The protocol: the `review:` block of the locked design (M-H-D1)

`schemas/design.yml` gains: `top_keys.optional += [review]`; `item_keys`:
`review: {required: [eligibility, strategies, screening, appraisal, synthesis], optional: [framework, registration, living]}`,
`eligibility: {required: [include, exclude], optional: []}`, `criterion: {required: [id, text], optional: []}`,
`strategy: {required: [id, query, sources], optional: [from_year, limit]}`,
`screening: {required: [], optional: [batch_size, prioritise]}`,
`appraisal: {required: [rob_tool, grade], optional: []}`, `synthesis: {required: [method], optional: [notes]}`,
`registration: {required: [registry], optional: [id]}`; `enums`:
`review_sources: [openalex, crossref, europepmc, arxiv, pubmed, semantic-scholar, datacite]` (must equal
`fanout.ORDER`, test in t3), `rob_tools: [rob2, robins-i, quadas-2, none]`,
`synthesis_methods: [narrative, meta-analysis]`, `registries: [prospero, osf, inplasy, other, none]`,
`frameworks: [pico, pecos, spider, other]`.

Example:

```yaml
review:
  framework: pico
  eligibility:
    include: [{id: i1, text: "Adults with isolated REM sleep behaviour disorder"}]
    exclude: [{id: e1, text: "Wrong population"}, {id: e2, text: "No outcome of interest"}]
  strategies:
    - {id: s1, query: "REM sleep behavior disorder AND alpha-synuclein", sources: [openalex, europepmc], from_year: 2000, limit: 200}
  screening: {batch_size: 50, prioritise: false}
  appraisal: {rob_tool: robins-i, grade: true}
  synthesis: {method: narrative, notes: "..."}
  registration: {registry: prospero, id: "CRD42…"}
  living: false
```

Checks in `model.check` (rule names in brackets; `core/design` must not import `scieflow.research`):
`review` required when `study_type == systematic-review` and an error for any other type [`review`];
`include` and `exclude` each a non-empty list; criterion ids match `ID_PATTERN` (`model.py:32`) and are
unique across both lists [`ids`]; `text` non-empty, ≤ 500 characters; `strategies` non-empty, unique ids,
`query` non-empty ≤ 1000 characters, `sources` a non-empty list of distinct `review_sources`, `from_year`
int 1900–2100, `limit` int 1–200 (default 100); `batch_size` int 1–500 (default 50); `prioritise` bool
(default false); `rob_tool` enum; `grade` bool; `synthesis.method` enum, `meta-analysis` adds a warning
"M-H computes no pooled effect" [`synthesis`]; `registration.registry` enum; `living` bool. A
systematic-review design without `prisma-2020` in `reporting_guidelines` gets a warning [`guideline-sr`].
TODO values give only `todo` errors (existing pattern), so the fresh template still passes
`test_init_template_reports_only_todo_errors`.

`templates.template("systematic-review", …)` adds a `review:` skeleton (one include and one exclude
criterion with `text: TODO`, one strategy with `query: TODO` and `sources: [openalex, europepmc]`,
`appraisal: {rob_tool: TODO, grade: true}`, `synthesis: {method: TODO}`, `screening: {batch_size: 50}`)
and `reporting_guidelines: [prisma-2020]`. `render.render` adds a "Review protocol" section (criteria
table, strategies table, screening, appraisal, synthesis, registration, living) to `design.md`.

The hash and lock cover the `review` block automatically (`locked_content`, `lock.py:85-87`), so a protocol
change is a design amendment with a diff (`lock.py:408-445`) and appears as `amendment` in the PRISMA
checklist (5.3).

`scieflow.research.sr.protocol.require_locked(project, ws) -> Locked(version, content_hash, review,
design, criteria_include, criteria_exclude)`: reads `lock.state(project, ws)` and refuses (`SRError`, exit
1) unless `status == "locked"` and the study type is `systematic-review`; messages: `none|draft|pending` →
"lock the protocol first: scieflow design lock <slug>"; `modified` → "design.yml differs from its lock
(version N); amend it with scieflow design amend or restore it"; `ledger-invalid`/`invalid` → refuse with
the state's problem. The review block is read from the in-force locked copy
`design/locked/v<n>/design.yml` (equal to the working file when the state is `locked`). `sr protocol
show <slug> [--json]` prints the locked review block, version, hash and amendment trail; `sr protocol
check <slug>` additionally runs `registry.check_query` (and `check_pubmed_query` for strategies naming
`pubmed`) on every strategy query and reports which would be refused (nothing is sent).

### 5.3 The review ledger: events, commits, adapter (M-H-D2)

New event types in `core/events.py` `TYPES` (comment "Added by M-H (systematic review), additive."):

| Type | data (besides `op`, `protocol_version`) | Written by |
|---|---|---|
| `sr.retrieval` | `round, strategy, source, query, record_ids` (hit ids), `capped` | `sr search` |
| `sr.dedup_decision` | `round, losers` (hit ids that are not new records) | `sr search` |
| `sr.screening_decision` | `screener_id, record_id, stage, decision, reason, batch` | `sr screen import` (vendored `record_screening_decision`) |
| `sr.adjudication` | `record_id, stage, decision, reason, rationale, resolves, gate` | gate hook (vendored `record_adjudication`) |
| `sr.appraisal` | `kind` (`rob`/`grade`), `record_id`/`outcome`, judgements, `overall`/`certainty` | `sr rob record`, `sr grade record` |
| `sr.committed` | `kind` (`search|screening|adjudication|rob|grade`), `files` ({relpath: sha256}), counts, kind-specific ids (`round`, `stage`, `batch`, `gate`), `protocol_hash` | every writer, last |

Rules: every write operation draws one `op` id (`store.new_id()`), emits its detail events, then exactly one
`sr.committed` with the same `op`. A detail event counts only if a later `sr.committed` names its `op`;
detail events of an uncommitted `op` (a crashed operation) are ignored and listed by `sr status`/`sr
verify` as a warning. Two `sr.committed` with the same `op` → ledger-invalid. Event data never carries a
record title, abstract, worksheet support text or a screener's free text; queries appear in
`sr.retrieval` (they are the locked protocol's text and passed the leak check); the human adjudication
rationale appears in `sr.adjudication` (the vendored payload requires it). Payload keys `ws`, `type_` and
`actor` are refused by the adapter (they would collide with `events.emit`'s parameters).

`scieflow.research.sr.ledger`:
- `EventsLedger(ws, *, actor, op, extra=None)` implements the vendored ledger interface (5.4): `append`
  maps the vendored type (`retrieval`, `dedup_decision`, `screening_decision`, `adjudication`) to `sr.<type>`,
  adds `op` and `extra` (for example `batch`, `gate`, `reason`, `protocol_version`) to the payload, calls
  `events.emit(ws, …, actor, **payload)` and returns the event as stored (its `event_id` = the emitted
  `id`); any other type is refused. `commit(kind, **data)` emits `sr.committed`.
- `view(ws)` returns a read-only ledger whose `iter_events(run_id=None, type=None)` yields, in file order:
  first one synthesized `protocol_locked` (design ledger record 1) and one `amendment` per later design
  record (payload `{version, content_hash}`, `event_id` `design-v<seq>`; never written to `events.jsonl`),
  then every committed `sr.*` detail event mapped back to its vendored type. Its `append` refuses.
- `run_context(op, extra)` returns the `_ledger.RunContext` the vendored writers take; `ts` passed to the
  vendored functions is the current UTC time (the stored `ts` is `events.emit`'s own).

### 5.4 Vendoring (M-H-D3, D15)

| File (in `researcher_core/`) | Upstream | Upstream sha256 | Patch |
|---|---|---|---|
| `screen.py` | `core/researcher_core/screen.py` | 28072bf60184544de4c1e3d6f4f78e3c9597ffdd2ec8dfb8b181bfea4eeda01f | `from rapidfuzz import fuzz` → `from ._fuzz import fuzz`; `from .provenance import ProvenanceEvent, ProvenanceLedger, RunContext` → `from ._ledger import ProvenanceEvent, ProvenanceLedger, RunContext` |
| `sr_prisma.py` | `core/researcher_core/sr_prisma.py` | def64bad0fcd69d132ac69df47dfe5d47fc1412a071b021e8e861490daf8d3ca | `from .provenance import ProvenanceEvent, ProvenanceLedger, derive_prisma` → `from ._ledger import ProvenanceEvent, ProvenanceLedger, derive_prisma` |
| `prisma_counts.py` (excerpt) | `core/researcher_core/provenance.py` lines 527–625 | file 6c77c217…2002, excerpt 29ebb44a0a1c9c410f8cee341c683734b4943b422bab14dee693125d94e71836 | ScieFlow header only (docstring naming the excerpt, `from __future__ import annotations`, `from collections.abc import Iterable, Sequence`, `from dataclasses import dataclass, field`, `from typing import Any`) |

`_ledger.py` is ScieFlow-authored and unpinned (like `_deps.py`), standard library plus `.prisma_counts`
only: a frozen `ProvenanceEvent(run_id, type, ts, payload, event_id="")`; `RunContext(run_id, extra=())`
whose `event(type, payload, ts, *, source_response_hashes=(), event_id="")` returns a `ProvenanceEvent`
with `payload = {**payload, **extra}`; `ProvenanceLedger` (base: `append`, `iter_events(run_id=None,
type=None)`, `events(run_id=None, type=None)`; `append`/`iter_events` raise `NotImplementedError` naming
`scieflow.research.sr.ledger`); `MemoryLedger(ProvenanceLedger)` (list-backed, append order, assigns
`event_id = sha256(canonical JSON of (seq, type, payload, ts))` when empty, for tests and the harness);
re-exports `PrismaCounts`, `derive_prisma` from `prisma_counts`. `protocol.py` is not vendored (M-H-D1);
VENDORED.md says so and names `core/design/lock.py`.

Ported upstream tests: `tests/vendor/test_researcher_screen.py` (from `R:core/tests/test_screen.py`) and
`tests/vendor/test_researcher_sr_prisma.py` (from `test_sr_prisma.py`) with `ledger` fixtures replaced by
`MemoryLedger` and imports rewritten; every removed test (for example one that depends on SQLite
content-addressed ids or `DuplicateEventError`) is listed with its reason in the file header (the M-C2
practice). `tests/vendor/test_sr_ledger_interface.py` covers `_ledger.py`.

### 5.5 Search rounds and the corpus (M-H-D6, D7, D13)

`fanout.collect(gw, query, *, sources=None, limit=25, from_year=None) -> (chosen, statuses, answers)` is
the loop of `run_all` lines 58–84 moved into a function; `run_all` calls it and is otherwise unchanged
(existing fan-out tests pass unchanged; a new test compares `search all` output before/after on a recorded
fixture byte for byte).

`scieflow sr search <slug> [--network MODE] [--as-agent] [--json]`:
1. `require_locked`; `sr verify` must pass; round number `n` = committed search operations + 1. If `n ≥ 2`
   the protocol must have `living: true` or its version must differ from the last round's version (an
   amendment); otherwise refuse "a non-living review searches once; amend the protocol or set living".
2. Every strategy query is checked first (`registry.check_query`; `check_pubmed_query` for strategies with
   `pubmed`); any refusal → exit 3, nothing sent for any strategy.
3. One Gateway for the run (`registry.gateway(slug, network_mode=…)`, so L2 and replay rules apply); for
   each strategy in protocol order `fanout.collect(…, sources=strategy.sources, limit=strategy.limit,
   from_year=strategy.from_year)`. If any status is not `ok`, nothing is written and the command exits with
   `fanout.exit_code(all statuses)` (3 refused/off, 6 replay miss, 1 failure).
4. Hits get ids `<n>/<strategy>/<source>:<rank>`; each becomes a `CSLRecord` via `kernel.to_csl` with its
   `id` set to the hit id. One vendored `dedupe` call over all hits of the round **and** the CSL records of
   the existing corpus (ids `R…`). Connected components of the decisions (`kept_id`↔`duplicate_id`) are
   groups. A group containing a corpus record maps to that record (the earliest if several; a warning
   names the pair, M-H-D7); every hit in it is a loser. A group without a corpus record becomes one new
   record (next `R` id in order of its first hit: strategy order, canonical source order, rank); its other
   hits are losers.
5. Invariant (bug guard, refuse if broken): `len(hits) − len(losers) == len(new records)`.
6. Files (atomic writes): `sr/rounds/<n>/hits.jsonl` (hit id, strategy, source, rank, and the paper-record
   fields `doi, title, year, venue, abstract, url`), `sr/rounds/<n>/records.jsonl` (new records: `id`,
   `round`, `hits`, `doi, title, year, venue, abstract, url`, `sources`), `sr/rounds/<n>/round.json`
   (`round`, `protocol_version`, `protocol_hash`, per strategy and source: status, count, `capped`,
   totals `identified`, `duplicates`, `new_records`). `capped` = count ≥ the requested limit, or ≥ the
   module's page cap (t0 inventory (e) lists each module's cap).
7. Events: per (strategy, source) one `sr.retrieval`; one `sr.dedup_decision`; one `sr.committed`
   (`kind: search`, `round`, `files` = the three files' sha256, counts).

The corpus is the concatenation of `sr/rounds/*/records.jsonl` in round order (`sr records <slug>
[--round N] [--json]` lists it; records are printed through `sanitize.sanitize_json_strings`). Cached
search answers (Gateway TTL) are a stated residual for living updates (R4).

### 5.6 Dual screening (M-H-D4, D8)

Role `research.sr-screeners` (`Role(True, False, "systematic-review dual screeners (exactly two agents)")`),
default `config/defaults.yml` `research.sr-screeners: [codex, claude]`. The run's effective assignment
(`agent_config`) must list exactly two distinct enabled primary agents (refuse otherwise); the same
`model_family`/`family` prints a warning (independent errors are the point of dual screening).

`scieflow sr screen prepare <slug> --stage title-abstract|full-text [--size N] [--prioritise/--no-prioritise] [--as-agent]`:
- `require_locked`; `sr verify` passes; refuses while a batch of the same stage is prepared but not
  committed. `full-text` refuses while any title-abstract conflict lacks a committed adjudication.
- Queue: title-abstract — corpus records without committed decisions of both screeners at that stage;
  full-text — records whose resolved title-abstract verdict is `include`, without committed full-text
  decisions, **and** with a report file `sr/reports/<R>.{pdf,txt,md,html}` (a regular file, not a
  symlink). Records sought but without a report are not queued (they become "reports not retrieved").
- Order: corpus order; with prioritise (flag, else `review.screening.prioritise`) the vendored
  `rank_queue` (order only, nothing dropped). Batch = the first `N` (`--size`, else `batch_size`).
- Writes `sr/screening/<stage>/<batch>/batch.json` (`batch` = `store.new_id()`, `stage`, `record_ids`,
  `screeners`, `protocol_version`, `protocol_hash`, `prepared_at`) and two prompts
  `prompts/sr-<stage>-<batch>-<agent>.md`, identical except the output path line. The prompt holds: the
  task; the locked criteria (ids and text); the stage rules (title-abstract: when unsure, include; reason
  optional; full-text: an exclusion names exactly one exclude criterion id; read the report file named per
  record); the records as untrusted data (`sanitize.fence_untrusted` around the JSON list of `{id, title,
  year, venue, abstract, doi, report}`; research rule 7 restated); the output format; "do not open any
  other file under sr/".
- Output `sr/screening/<stage>/<batch>/<agent>.json`: `{"format": 1, "screener": "<agent>", "batch":
  "<id>", "decisions": [{"record_id": "R00001", "decision": "include"|"exclude", "reason": "<criterion id>"|""}]}`.

`scieflow sr screen import <slug> --stage S --batch B [--as-agent]` validates everything before writing
anything: batch exists and is not committed; both files are regular files, parse, `format == 1`,
`screener` equals the file stem and is one of `batch.screeners`, `batch` matches; decisions cover exactly
the batch ids, once each; `decision ∈ {include, exclude}`; `reason` is `""` for an include, an exclude
criterion id or `""` for a title-abstract exclude, an exclude criterion id for a full-text exclude.
**Dispatch evidence**: for each screener a job in `jobs.list_jobs(project, run_dir=ws)` with `kind ==
"agent"`, `label == <agent>`, `state == "done"` and `started ≥ prepared_at`; otherwise refuse ("no finished
dispatch of <agent> since the batch was prepared; dispatch it with scieflow agent run --role
research.sr-screeners"). Then every decision through the vendored `record_screening_decision` on an
`EventsLedger` (`extra = {batch, protocol_version}`), then `sr.committed` (`kind: screening`, `stage`,
`batch`, `files` = sha256 of `batch.json`, both prompts, both outputs, `jobs` = {agent: job id}).

Resolved verdicts (`screening.resolved(view, stage) -> {record_id: include|exclude|pending}`): agreement →
that decision; disagreement → the committed adjudication if one resolves exactly the current two decision
event ids, else `pending`. A test asserts that the counts equal the vendored `derive_prisma_flow` stage
summary on the same events.

`scieflow sr kappa <slug> --stage S [--json]`: vendored `cohens_kappa` and `screening_streams` over
`view(ws)`; prints `n`, the 2×2 table, observed and expected agreement and kappa to 6 decimals, the note
(undefined cases). `scieflow sr screen status <slug> [--json]`: per stage corpus size, decided by both,
open batches, conflicts, adjudicated, pending, kappa; uncommitted operations.

### 5.7 Blinded conflicts and the adjudication gate (M-H-D5)

`schemas/gates.yml` gains `sr-adjudication: {requires_human: true, help: "Adjudicate blinded screening
conflicts of a systematic review"}` (comment line "Systematic review (M-H 5.7)").

`scieflow sr conflicts <slug> --stage S [--as-agent]`: refuses while an `sr-adjudication` gate for that
stage is open. Conflicts = vendored `screen_conflicts(view, …, stage, corpus=<record id → record>,
profile=<locked criteria>)` minus records with a committed adjudication resolving their current decision
ids. None → prints "no conflicts", exit 0. Else writes `sr/adjudication/<stage>/<gate>/conflicts.json`
(`Conflict.to_json_dict()` per record: record, criteria and the opaque decision event ids) and
`conflicts.md` (per record: id, title, year, venue, abstract inside a fence, the criteria; no event ids,
**no screener names, decisions or reasons**), and opens the gate (`gates.open_gate`, actor per
`--as-agent`, `gate_id` drawn first so the folder name equals the gate id) with question "Adjudicate <k>
blinded <stage> conflicts in <slug> (sha256 <first 12 hex of conflicts.json>)?", options
`[resolved, reject]`, files the two conflict files.

`scieflow sr adjudicate <slug> <gate-id> <record-id> --decision include|exclude [--reason <exclude id>] --rationale TEXT`:
human only — `refuse_in_dispatch()` first, no `--as-agent` option; the gate is open and of kind
`sr-adjudication`; the record is in its `conflicts.json`; reason rules as in 5.6; rationale 1–2000
characters. Writes or replaces the entry in `decisions.json` (atomic) and prints how many remain.

Gate hook `scieflow.research.sr.adjudication.on_answer(project, ws, gate, answer, actor)`, called from
`gates.answer` for kind `sr-adjudication` before the gate is marked answered (lazy import, the
`design-lock` pattern; an `ImportError` becomes `GateError("answering sr-adjudication gates needs the
research extra")`, so the gate is never answered without its hook):
- `actor != "human"` → `GateError`; `refuse_in_dispatch()` → `GateError`.
- `reject` → nothing recorded (the files stay for audit).
- `resolved` → the sha256 of `conflicts.json` starts with the digest in the question; every conflict has a
  decision; each conflict's `decision_event_ids` still equal the two screeners' latest committed decisions
  (else `GateError` "the screening changed since the gate opened; reject this gate and run sr conflicts
  again"); idempotent if an `sr.committed` with this `gate` exists (crash window); then the vendored
  `record_adjudication` per conflict on an `EventsLedger(actor="human", extra={gate, reason,
  protocol_version})`, then `sr.committed` (`kind: adjudication`, `stage`, `gate`, `files` = sha256 of
  `conflicts.json` and `decisions.json`).

### 5.8 `sr verify` (integrity, fail closed)

`scieflow sr verify <slug> [--json]`, read-only. Errors: a duplicate `sr.committed` op; a committed file
whose sha256 differs or that is missing or a symlink; committed decision events that differ from the output
files they were imported from (screener, record, stage, decision, reason, count); adjudication events that
differ from `decisions.json`, or whose gate file is not `sr-adjudication` answered `resolved` by `human`
(an open gate with an existing commit is accepted only as the crash window and reported); a commit whose
`protocol_version`/`protocol_hash` does not match a design ledger record; corpus ids not a gapless
sequence over rounds; `round.json` totals that disagree with the round's events. Warnings: uncommitted
detail events. Exit 0 clean, 1 with errors. Every SR write command and `sr prisma` run it first and refuse
with "sr-ledger-invalid: <problem>" (exit 1) until a human repairs the run.

### 5.9 PRISMA flow and checklist

`scieflow sr prisma <slug> [--json] [--write]`: `sr verify` first; flow = vendored `derive_prisma_flow`
over `view(ws)` events; checklist = vendored `derive_prisma_checklist` over the same events and flow.
Additions computed by ScieFlow: per-round identification (from committed `sr.retrieval` events), capped
sources, kappa per stage, the screening statement ("two independent agent screeners: <a>, <b>; conflicts
adjudicated by a human; screener independence is procedural (docs)"), protocol version trail. Output
`--json` or text; `--write` writes `sr/prisma/flow.json`, `sr/prisma/flow.md` (text boxes and a Mermaid
`flowchart TD` block with the PRISMA 2020 boxes), `sr/prisma/checklist.md` (every item of
`config/reporting/prisma-2020.yml`: id, topic, status `derived|missing|author-supplied` from the vendored
checklist, evidence text; items the vendored checklist does not cover are `author-supplied`), each stamped
`derived_from: {events: <count>, sha256: <hash of the committed sr events and design ledger records>,
at: <ts>}`. Nothing in `sr/prisma/` is read back as a source of counts.

### 5.10 Risk-of-bias and GRADE worksheets (M-H-D9)

Tool definitions (`config/sr/<tool>.yml`, format: `version: 1`, `id`, `name`, `source: {citation, doi}`,
`verification: {status: unverified, note, verified_by: null, verified_at: null}`, `domains: [{id, label}]`,
`judgements` (ordered, mildest first), optional `applicability: {domains, judgements}`, `overall: {rule:
worst|none, extra: [...]}`):

| Tool | Domains | Judgements | Overall rule |
|---|---|---|---|
| `rob2` (Sterne et al., BMJ 2019;366:l4898) | D1 randomization process, D2 deviations from intended interventions, D3 missing outcome data, D4 measurement of the outcome, D5 selection of the reported result | low, some-concerns, high | `worst`: overall ≥ worst domain; higher only with a rationale |
| `robins-i` (Sterne et al., BMJ 2016;355:i4919) | confounding, selection of participants, classification of interventions, deviations from intended interventions, missing data, measurement of outcomes, selection of the reported result | low, moderate, serious, critical (+ `no-information`, outside the order) | `worst` over the ordered judgements; `no-information` overall only if some domain is `no-information` and none is serious or critical |
| `quadas-2` (Whiting et al., Ann Intern Med 2011;155:529-536) | patient selection, index test(s), reference standard, flow and timing; applicability for the first three | low, high, unclear | `none`: overall optional; `low` only if every domain is `low` |
| `grade` (Guyatt et al., BMJ 2008;336:924-926; Balshem et al., J Clin Epidemiol 2011;64:401-406) | downgrade: risk of bias, inconsistency, indirectness, imprecision, publication bias; upgrade: large effect, dose-response, plausible confounding | downgrade none/serious/very-serious (0/−1/−2); large effect none/large/very-large (0/+1/+2); dose-response and plausible confounding none/present (0/+1) | start 4 (randomised) or 2 (non-randomised); certainty = clamp(start − down + up, 1, 4) → very low/low/moderate/high; upgrades only for non-randomised evidence |

Commands: `sr rob init <slug> [--as-agent]` (tool from the locked protocol; refuses when `none`; one
`sr/rob/<R>.yml` per included study — resolved `include` at full-text — that has none yet; never
overwrites); `sr rob check <slug> [--json]` (domains exactly the tool's; judgement in the tool's list;
`support` non-empty per domain; overall rule; errors name file, domain and rule, never the support text);
`sr rob record <slug> [--as-agent]` (check passes for every included study → one `sr.appraisal` per study
with judgements and overall, then `sr.committed` with every worksheet's sha256; a later record supersedes).
`sr grade init|check|record|table <slug>`: `sr/grade.yml` has one entry per SAP outcome id of the locked
design (`outcome`, `studies` ⊆ included, `design: randomised|non-randomised`, downgrade/upgrade levels each
with a `reason` when not none, `effect_text` author-supplied); `check` computes certainty and refuses an
upgrade on randomised evidence; `table --write` renders `sr/grade/summary-of-findings.md`.

Pinned examples (t7 tests): rob2 domains `[low, some-concerns, low, low, low]` → overall `low` is an error,
`some-concerns` is fine, `high` needs a rationale; grade randomised + risk of bias serious + imprecision
serious → `low`; non-randomised + large effect large → `moderate`; randomised + three very-serious →
`very low` (clamped); randomised + dose-response present → error.

### 5.11 Evaluation harness, gold sets and Wilson intervals (M-H-D10)

`scieflow.research.evals`: `Z95 = statistics.NormalDist().inv_cdf(0.975)` (1.9599639845400536);
`wilson(k, n, z=Z95) -> Interval(k, n, point, low, high)` (Wilson 1927, J Am Stat Assoc 22:209-212;
`n == 0` → `ValueError`; bounds clipped to [0, 1]); `Confusion` for binary labels with `sensitivity`,
`specificity`, `accuracy`, `ppv`, each an `Interval`; `format_interval` → `"k/n = 0.900000 [0.595850,
0.982124]"`. Pinned (6 decimals, abs tol 1e-6): 8/10 → [0.490162, 0.943318]; 9/10 → [0.595850, 0.982124];
10/10 → [0.722467, 1]; 0/50 → [0, 0.071348]; 0/100 → [0, 0.036993]; 27/30 → [0.743789, 0.965400];
24/30 → [0.626943, 0.904949]; 36/40 → [0.769482, 0.960420]; 34/40 → [0.709277, 0.929388];
110/110 → [0.966256, 1].

`scieflow sr eval <slug> --gold PATH --stage S [--json]`: the gold file must resolve inside the run (no
symlink, no `..`), format `{format: 1, stage, labels: {R00001: include|exclude, …}}` (a human-labelled
calibration sample; synthetic in tests); for each screener stream and for the resolved verdict, the
confusion over records that are both labelled and decided, printed with Wilson intervals; labelled records
not yet decided are counted and named.

`scripts/evals/run_axes.py [--axis dedup|kappa|prisma|screening|all] [--json]` (developer harness, repo
checkout, offline, imports `scieflow` from the repo): `dedup` — the 210 pairs of
`tests/fixtures/kernel/dedup.yaml` through the vendored dedupe: same-pair merge rate 110/110 [0.966256, 1],
different-pair false merges 0/100 [0, 0.036993]; `kappa` — the tables of `tests/fixtures/sr/kappa.yml`
(5.12) reproduce exactly; `prisma` — `tests/fixtures/sr/prisma-scenario.yml` replayed on a `MemoryLedger`
reproduces the hand-computed flow (5.12); `screening` — `tests/fixtures/sr/screening-calibration.yml` (40
synthetic records, 10 include / 30 exclude; screener A TP 9 FN 1 FP 3 TN 27, screener B TP 10 FN 0 FP 6
TN 24) prints sensitivity 9/10 and 10/10, specificity 27/30 and 24/30, accuracy 36/40 and 34/40 with the
pinned intervals. Exit 0 when every axis meets its gate, 1 otherwise; each line names the axis.

### 5.12 Hand-checked reference values (gold, synthetic)

Cohen's kappa (Cohen 1960, Educ Psychol Meas 20:37-46), table cells a = both include, b = A include/B
exclude, c = A exclude/B include, d = both exclude:

| a, b, c, d | n | p_o | p_e | kappa |
|---|---|---|---|---|
| 20, 5, 10, 15 | 50 | 0.7 | 0.5 | 0.4 |
| 40, 10, 5, 45 | 100 | 0.85 | 0.5 | 0.7 |
| 10, 5, 3, 30 | 48 | 40/48 | 1350/2304 | 95/159 = 0.597484 |
| 5, 1, 1, 6 | 13 | 11/13 | 85/169 | 58/84 = 0.690476 |
| 10, 0, 0, 0 | 10 | 1 | 1 | undefined (note), vendored |

PRISMA scenario (round 1): strategy s1 sources openalex 30 hits and europepmc 20 hits, strategy s2 source
crossref 10 hits → identified 60 (by source 30/20/10); duplicates 12 → 48 records R00001–R00048.
Title-abstract: both screeners decide all 48; agree include 10, agree exclude 30, conflicts 8 (A include/B
exclude 5, A exclude/B include 3; kappa = row 3), adjudicated 4 include, 4 exclude → screened 48, excluded
34, reports sought 14. Reports present for 13 → reports not retrieved 1, assessed 13. Full-text: agree
include 5, agree exclude 6 (reasons e1 ×4, e2 ×2), conflicts 2 (kappa = row 4), adjudicated 1 include and
1 exclude with reason e2 → excluded 7 (e1 4, e2 3), studies included 6. Round 2 (living): openalex 5 hits,
2 duplicates of the corpus → 3 new records R00049–R00051; cumulative identified 65, duplicates 14, after
deduplication 51.

### 5.13 CLI surface, exit codes and events

`scieflow sr` (registered in `src/scieflow/cli.py` `GROUPS`, extra `research`, help "Systematic review:
protocol, search, dual screening, adjudication, PRISMA, appraisal."):
`protocol show|check`, `search`, `records`, `screen prepare|import|status`, `kappa`, `conflicts`,
`adjudicate`, `verify`, `prisma`, `rob init|check|record`, `grade init|check|record|table`, `eval`,
`status`. Every read command has `--json`. Exit codes: 0 ok; 1 refused or invalid (`SRError`, message names
the rule and the path or id, never record text or a query); 2 usage (click); 3 search policy refusal; 6
replay miss (search only, as `search all`). Writers take `--as-agent` (events actor `agent`, else `human`),
except `sr adjudicate` (human only).

### 5.14 Data protection and untrusted content

Search goes only through `registry.gateway`/`fanout.collect`, so the M-B Gateway rules (an L2 run may reach
no live host, `l2_providers`) and the M-C1 network modes apply unchanged; no other network path is added.
Screener dispatches are ordinary `scieflow agent run` dispatches, so M-B's dispatch refusal for declared L2
data and sandbox masking apply unchanged. Record text (titles, abstracts) is untrusted: fenced in prompts,
printed through `sanitize_json_strings`, never written into events or messages. Screener outputs are
validated against closed sets (ids, decisions, criterion ids), so an injected record can at most flip a
decision, which dual screening and human adjudication are there to catch.

### 5.15 Docs, skill, menu and workflow

Skill `src/scieflow/research/skills/systematic-review/SKILL.md` (symlinked as `skills/systematic-review`):
staffing gate (research AGENTS run-configuration gate, role `research.sr-screeners`); `scieflow design init
<slug> --type systematic-review`, fill the review block, `design check`, `design lock` (human gate);
`scieflow research network <slug> record` before searching (replayable); `sr protocol check`, `sr search`;
title-abstract loop (`sr screen prepare` → two dispatches `uv run scieflow agent run --role
research.sr-screeners <agent> workspace/<slug>/prompts/sr-…-<agent>.md workspace/<slug>/logs/…` → `sr screen
import` → `sr conflicts` → wait for the human gate); place reports under `sr/reports/` (existing full-text
tools or the user); full-text loop; `sr rob …`, `sr grade …`; `sr prisma --write`; living updates;
the rules (never edit `sr/` files or emit `sr.*` events; never adjudicate; never compute counts or kappa
yourself; screeners never open other screeners' files; numbers in a manuscript come from `sr prisma`).
`docs/research/systematic-review.md`: the method, commands, files, events, gate, verify, residuals R1–R8.
`docs/cli.md` gains a `scieflow sr` section; `mkdocs.yml` one nav line ("Systematic Review:
research/systematic-review.md") after line 93. `schemas/status-research.yml` workflow `systematic-review:
[protocol, search, screen-title-abstract, screen-full-text, appraise, report]`; menu `WORKFLOWS` entry
`systematic-review` (skill path, ask "Review question for the systematic review", roles
`["research.sr-screeners"]`; `sr search` dispatches no agent).

### 5.16 Security and integrity properties (reviewed in every ticket that touches them)

| Property | Where |
|---|---|
| Adjudication only by a human: gate `requires_human`, hook refuses non-human and dispatched callers, `sr adjudicate` refuses in a dispatch and has no `--as-agent` | 5.7 |
| Blinding: `conflicts.md` and the gate question carry no screener name, decision or reason (test greps both files for every screener name and for `include`/`exclude` outside the criteria text) | 5.7 |
| Dual screening: exactly two distinct agents, both outputs imported together, dispatch evidence required | 5.6 |
| Fail closed: unlocked or modified protocol, verify errors, partial search rounds, invariant breaks all refuse before any write | 5.2, 5.5, 5.8 |
| Derived numbers: counts, kappa and certainty computed from events/worksheets each time; no stored count is read back | 5.3, 5.9, 5.10 |
| No untrusted text in events or messages; prompts fence record text | 5.3, 5.14 |
| Paths: every path is built from validated ids (`R\d{5}`, `store.new_id`, stage enum, agent names from the assignment); `--gold` must resolve inside the run; symlinks refused where files are read or written | all |
| No new network path, no new dependency (`_fuzz`, `statistics`), no sandbox change | 5.4, 5.11, 5.14 |
| Vendored files byte-identical except the recorded patches (`test_vendored_integrity`) | 5.4 |

### 5.17 What M-H does not do (stated)

No meta-analysis computation or forest plots; no pagination beyond one page per source per strategy
(capped sources flagged); no data-extraction forms; single-assessor RoB/GRADE; no agent adjudicator; no
sandbox mask between screeners; no automatic full-text retrieval (existing tools or the user place reports
in `sr/reports/`); no PRISMA image rendering (Mermaid text only); no registry submission; no web panel; no
Fleiss' kappa.

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is
a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only; every search in
tests is mocked or replayed), no push/PR, no credentials, no patient data (synthetic fixtures only), never
edits an `INFLIGHT` path or a never-edited file of section 3 (the t9 protected edits are applied by the
operator). Every implementation and fix card runs the `INFLIGHT` guard and reports its exit code.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-h-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-h-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-h/junit G6_CACHE_DIR=~/.cache/scieflow-m-h RUN/tools/g6_pytest.sh WT <head-sha> m-h-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with
  completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> m-h-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes**: the four named in the installer's standing rules, one rerun, both results recorded.
  No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-h/executions.jsonl` and states RED and GREEN evidence in its result.
- Focused sets per ticket (clean-tree runner on the ticket's final commit): t1 `tests/vendor`; t2
  `tests/core/design tests/research/sr tests/core/review/test_gates_events.py tests/core/test_service.py`;
  t3 `tests/research/sr tests/research/kernel tests/research/kernel2 tests/test_docs_literature_kernel.py`;
  t4 `tests/research/sr tests/core/test_agent_config.py tests/core/test_agent_configure.py
  tests/core/test_staffing.py tests/core/test_reviewer_roles.py tests/core/test_menu.py`; t5
  `tests/research/sr tests/core/review/test_gates_events.py tests/core/data/test_release.py
  tests/core/design/test_gate_hook.py`; t6, t7 `tests/research/sr`; t8 `tests/research/sr
  tests/research/test_evals.py tests/vendor/test_dedup_gold.py`; t9 `tests/research/sr tests/core/test_menu.py
  tests/core/test_create_run.py tests/test_docs_systematic_review.py tests/test_docs_role.py
  tests/test_docs_journal.py tests/test_docs_literature_kernel_ii.py tests/test_docs_design.py`.
- Tests that need a run use the existing project fixtures (`tests/core/conftest.py`,
  `tests/core/design/conftest.py`); searches use mocked Gateway transports or recorded snapshots
  (`network: replay`); dispatch evidence uses job records written through `scieflow.core.jobs` test
  helpers (no agent is launched); human gate answers are made in-test with `actor="human"`.

### t0 — worktree, plan and spec copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the five ancestor checks and the M-D1 and M-C2 publish
   receipts; otherwise `BASE_RED` with the reason. `worktree add -b dev/m-h RUN/scratch/m-h-wt <base>` (on a
   retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. No expected counts are given; t0 records what it measures. Only the load-flake rule may
   clear a failure; otherwise `BASE_RED` with node ids and junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 on `base`; write `reviews/m-h-t0-baseline.md` (per cite:
   still present / moved (new line) / changed). Inventories: (a) whether base contains M-D3, M-G or M-I
   (their publish receipts) and which files of section 3 they changed; (b) every existing test that pins
   events types, gate kinds, roles, assignments, study-type templates, workflows or menu entries (file:line;
   these are the permitted pin updates of section 3); (c) whether `tests/vendor/test_vendored_integrity.py`
   or `test_vendored_excerpts.py` enumerate the pinned files; (d) every test file that imports
   `scieflow.research.search.fanout`; (e) each search module's page cap constant; (f) the upstream sha256 of
   `screen.py`, `sr_prisma.py`, `provenance.py` and of the excerpt lines 527–625 in the researcher snapshot
   equal section 0; (g) each protected edit's `old` text occurs exactly once on base.
4. Copy `reviews/m-h-plan.md` byte for byte to `docs/superpowers/plans/2026-10-31-m-h-systematic-review.md`,
   and its Part A (from the line `## Part A — Spec` up to, not including, the line
   `## Part B — Implementation plan`) to `docs/superpowers/specs/2026-10-31-m-h-systematic-review-design.md`
   with this first line:
   `# M-H systematic-review vertical — design (Part A of docs/superpowers/plans/2026-10-31-m-h-systematic-review.md)`
   and one blank line. Commit both alone ("docs(plans): M-H systematic-review spec and plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — vendored screening and PRISMA modules (Haiku) → t1r (Sonnet)

Files: section 3 rows t1. Copy the two upstream files byte for byte from the researcher snapshot, apply
exactly the recorded patches, write the excerpt with its header, write `_ledger.py`; add the three
UPSTREAM.json entries (`upstream`, `upstream_sha256`, `sha256`, `patches`, and `lines`/`excerpt_sha256`/
`header` for the excerpt), the VENDORED.md rows and the "not vendored: protocol.py" line, a CREDITS.md
paragraph.

RED → GREEN:
1. Integrity: every new file's sha256 equals its UPSTREAM.json pin; reversing the patches yields the
   upstream sha256; the excerpt minus its header equals lines 527–625 (sha256 29ebb44a…1836).
2. Ported upstream screening tests on `MemoryLedger`: conflicts blinded (no vote field), adjudication needs
   two event ids and a rationale, kappa formula and undefined cases, single-screener disclosure,
   `rank_queue` keeps every id; the removed tests listed with reasons.
3. Ported PRISMA tests: flow derived from events; deleting a screening event changes the flow; checklist
   statuses.
4. `_ledger` interface: `RunContext.event` merges `extra`; base `ProvenanceLedger` refuses with the named
   replacement; `MemoryLedger` order and ids deterministic; no import of `scieflow.core` from `_vendor`.
5. Kappa rows 1–4 of section 5.12 through vendored `cohens_kappa` on `MemoryLedger` (exact fractions,
   1e-12).

t1r checklist: verbatim copies and patches exactly as recorded; no other vendored file changed; ported tests
not weakened beyond the listed removals; `_ledger.py` standard library only; INFLIGHT guard exit 0.

### t2 — protocol in the design, ledger adapter, events, `sr` group (Haiku) → t2r (Sonnet)

Files: section 3 rows t2.

RED → GREEN:
1. A systematic-review design without `review` → error rule `review`; a cohort design with `review` → error;
   the new fixture `tests/fixtures/design/systematic-review.yml` checks clean; each rule of 5.2 has a
   failing case (duplicate criterion id across include/exclude, unknown source, empty sources, limit 0,
   `meta-analysis` warning, missing `prisma-2020` warning).
2. `design init --type systematic-review` then `check` → only `todo` errors (the existing parametrised test
   stays green; a new test asserts the skeleton keys).
3. `design.md` shows the review section; the design hash changes when a criterion text changes.
4. `require_locked`: none/draft/pending/modified/ledger-invalid/other study type each refused with its
   message; a locked SR design returns version, hash and the review block read from `design/locked/v<n>/`.
5. `events.TYPES` has the six `sr.*` types (pins updated as section 3 permits); `EventsLedger.append` maps
   types, adds `op` and `extra`, refuses other types and colliding keys; `view` yields synthesized
   `protocol_locked`/`amendment` (from a design ledger with one lock and one amendment) and only committed
   detail events; a duplicate commit op is reported.
6. `scieflow sr --help` lists the group; `sr protocol show --json` and `sr protocol check` (a strategy query
   the leak check refuses is reported, nothing sent; mocked registry); `sr status` on an empty run.

t2r checklist: `core/design` imports nothing from `scieflow.research`; template still yields only `todo`
errors; lock semantics unchanged (`tests/core/design` green, no edit to `lock.py`); events additive only;
INFLIGHT guard exit 0.

### t3 — search rounds and the corpus (Haiku) → t3r (Sonnet)

Files: section 3 rows t3.

RED → GREEN:
1. `fanout.collect` extracted: every existing fan-out test unchanged and green; `search all` output on a
   recorded fixture byte-identical before and after.
2. `schemas/design.yml` `review_sources` equals `fanout.ORDER`.
3. Round 1 of the 5.12 scenario through mocked sources: identified 60, by source 30/20/10, duplicates 12,
   records R00001–R00048; the three files and the events of 5.5; the invariant holds; corpus order
   deterministic (same input twice → identical files).
4. A failing or refused source in any strategy → nothing written, exit per `fanout.exit_code`; a refused
   query → exit 3 and zero HTTP calls for every strategy; replay miss → exit 6.
5. Round 2: refused for a non-living protocol unless amended; with `living: true` the 5.12 round-2 numbers;
   a hit duplicating a corpus record never creates a new id.
6. `capped` set when a source returns the requested limit.
7. Unlocked or modified protocol → refused before any request.
8. An L2 run (declared through `scieflow data declare`) → the Gateway refuses live hosts as today (no new
   path).

t3r checklist: no network outside the Gateway; nothing written on partial rounds; no record text in events
or messages; fan-out behaviour unchanged; INFLIGHT guard exit 0.

### t4 — dual screening, roles, kappa (Haiku) → t4r (Sonnet)

Files: section 3 rows t4.

RED → GREEN:
1. Role `research.sr-screeners` in `ROLES`, default `[codex, claude]`; `scieflow agent show` lists it;
   configure accepts two primary agents and refuses a support agent (tier rule).
2. `prepare`: batch and the two prompts; prompts identical except the output path; records fenced; criteria
   present; refused while a batch is open, on an unlocked protocol, with a one-agent or a same-agent
   assignment; same-family warning; full-text queue only TA-included records with a report file; a
   symlinked report is not a report; prioritise reorders without dropping.
3. `import`: every validation of 5.6 has a refusing case (missing file, wrong screener, wrong batch,
   missing or extra record, bad decision, include with a reason, full-text exclude without a criterion,
   unknown criterion, no finished job, job started before `prepared_at`); a valid pair writes 2n decision
   events and one commit; re-import refused; an interrupted import (detail events without commit) is
   ignored by `view` and a new import succeeds.
4. `resolved` counts equal the vendored flow's stage summary.
5. `sr kappa` reproduces section 5.12 rows 3 and 4 from imported decisions; the undefined case prints the
   note.

t4r checklist: dual screening and dispatch-evidence rules exactly as 5.6; prompts carry no other screener's
output; no record text in events; INFLIGHT guard exit 0.

### t5 — blinded conflicts and the adjudication gate (Haiku) → t5r (Sonnet)

Files: section 3 rows t5.

RED → GREEN:
1. `sr conflicts` on the 5.12 title-abstract decisions → 8 conflicts, `conflicts.json`/`conflicts.md`, one
   open `sr-adjudication` gate whose id is the folder name; neither file nor the question contains a
   screener name or a vote (GAP acceptance: blinded conflicts surfaced); a second call refuses while the
   gate is open.
2. `sr adjudicate` refused with `SCIEFLOW_DISPATCH=1`; refused for a record outside the conflicts, a bad
   reason or an empty rationale; writes `decisions.json`.
3. Gate answers: by an agent → refused (gate stays open); `resolved` with a missing decision → refused;
   after a new screening import changed a conflict → refused; `resolved` complete → 8 `sr.adjudication`
   events (actor human) and one commit, gate answered; repeating the hook after a crash between commit and
   gate update is idempotent; `reject` records nothing.
4. Full-text `prepare` refuses while a title-abstract conflict is unadjudicated, and works afterwards.
5. Gate-kind pin updated; an `ImportError` of the hook module → `GateError`, gate stays open.

t5r checklist: human-only paths (5.16 row 1); blinding; hook runs before the gate is marked answered; no
existing gate behaviour changed; INFLIGHT guard exit 0.

### t6 — verify and PRISMA (Haiku) → t6r (Sonnet)

Files: section 3 rows t6 and `tests/fixtures/sr/prisma-scenario.yml`.

RED → GREEN:
1. The 5.12 scenario end to end through the commands (mocked search, fake job records, in-test human
   answers) → `sr prisma --json` equals the hand numbers: identified 60 (30/20/10), duplicates 12, after
   48, screened 48, excluded 34, sought 14, not retrieved 1, assessed 13, excluded 7 (e1 4, e2 3), included 6;
   with round 2: identified 65, duplicates 14, after 51 (GAP acceptance: PRISMA counts derive from
   `events.jsonl`).
2. Removing the commit event of one screening batch from `events.jsonl` changes the flow (the batch no
   longer counts) and `sr verify` warns; editing a committed output file → `sr verify` error and every
   writer and `sr prisma` refuse with `sr-ledger-invalid`.
3. Checklist: items 7, 16a, 24a, 24b derived when the evidence exists (24b after a design amendment);
   `checklist.md` lists every item of `config/reporting/prisma-2020.yml`; `--write` stamps `derived_from`
   and nothing is read back.
4. `flow.md` contains a Mermaid block whose numbers equal the JSON.

t6r checklist: numbers come only from events; verify is fail-closed and read-only; INFLIGHT guard exit 0.

### t7 — RoB and GRADE worksheets (Haiku) → t7r (Sonnet)

Files: section 3 rows t7.

RED → GREEN:
1. Tool files load strictly (unknown key, duplicate domain, empty judgements refused); each is
   `verification.status: unverified`.
2. `rob init` creates one worksheet per included study (6 in the scenario), never overwrites, refuses for
   `rob_tool: none`.
3. `rob check` and the pinned examples of 5.10 for rob2, robins-i (no-information rule) and quadas-2 (low
   overall only if all low); error messages never contain support text.
4. `rob record` → 6 `sr.appraisal` events and a commit; a changed worksheet after record → `sr verify` error.
5. `grade` examples of 5.10; studies must be included; certainty computed, never read from the file;
   `table --write` renders the summary-of-findings table.

t7r checklist: deterministic checks exactly as 5.10; no judgement invented by code; INFLIGHT guard exit 0.

### t8 — evaluation harness, Wilson intervals, `sr eval` (Haiku) → t8r (Sonnet)

Files: section 3 rows t8 and `tests/fixtures/sr/{kappa.yml,screening-calibration.yml}`.

RED → GREEN:
1. `wilson` pinned values of 5.11; `n == 0` refused; bounds in [0, 1].
2. `Confusion` metrics on the calibration fixture: the pinned intervals for screeners A and B.
3. `sr eval` on a run built from the calibration fixture prints the same lines; a gold path outside the run
   or a symlink is refused; undecided labelled records are named.
4. `scripts/evals/run_axes.py --axis all` exits 0 and its output contains, per axis, the pinned interval
   strings (GAP acceptance: harness prints Wilson CIs); a corrupted kappa table in a temporary copy of the
   fixture makes the `kappa` axis fail with exit 1.

t8r checklist: formulas cited and pinned; no network; harness offline; INFLIGHT guard exit 0.

### t9 — skill, docs, workflow, menu, protected edits (Haiku) → t9r (Sonnet)

Files: section 3 rows t9. Before t9 the operator applies the two protected edits (`pe_t9`); t9 commits them
alone first, then its own work.

RED → GREEN:
1. `tests/test_docs_systematic_review.py`: the skill names every `sr` command it uses and each exists in the
   assembled click tree; every `scieflow agent run` invocation in the skill carries `--role
   research.sr-screeners` (also `tests/test_docs_role.py`); `docs/cli.md` has the `scieflow sr` section;
   the docs page lists residuals R1–R8; `mkdocs.yml` nav line; the research AGENTS paragraph and the root
   skills row equal the approved texts of section 3 byte for byte; the `skills/systematic-review` symlink
   resolves to the skill folder.
2. `schemas/status-research.yml` workflow and menu entry: creating a `systematic-review` run through the
   existing create-run path writes `workflow: systematic-review` with the six phases.
3. End-to-end with the stub flow of t6 driven only through the documented commands.

t9r checklist: protected edits byte-exact and only those; docs match code; no AGENTS.md change beyond them;
INFLIGHT guard exit 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by added tests); `mkdocs build --strict` into
`RUN/scratch/m-h/site` (or "not available"); INFLIGHT guard over `base..tip`; linear history (no merge
commits), at least one commit per ticket t0..t9; `git diff base..tip -- tests/` removes no line except the
permitted pin updates of section 3 (listed); no never-edited file of section 3 in the diff; the two
AGENTS.md files changed only by the t9 protected-edit commit and equal to the approved text; `git diff
base..tip -- pyproject.toml uv.lock` empty; `scripts/evals/run_axes.py --axis all` exit 0. Writes
`RUN/scratch/m-h/pr-body.md` (plain English: the seven capabilities, decisions M-H-D1..D13, absolute
counts, residuals R1–R8, the method references, "please merge with a merge commit"; no credentials,
prompts or patient data) and `reviews/m-h-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the four GAP acceptance criteria — PRISMA counts from
`events.jsonl`, blinded conflicts surfaced, kappa hand-checked, harness prints Wilson CIs; decisions
M-H-D1..D13; protected edits byte-exact), code and security (5.16), vendoring (byte-exact plus recorded
patches), evidence (re-run memory-capped `tests/research/sr tests/vendor tests/core/design
tests/research/test_evals.py tests/test_docs_systematic_review.py` and the harness; confirm the branch
result's absolute counts; re-run the INFLIGHT guard; `pr-body.md` accurate). Output
`reviews/m-h-final-review-r<N>.md`. PASS → the installer's publish step; CHANGES → final_fix (new commits,
full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-h-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   vendored screen/PRISMA modules         DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        protocol, ledger, events, sr group     same shape → t3
t3/t3r/t3f        search rounds, corpus                  same shape → t4
t4/t4r/t4f        dual screening, roles, kappa           same shape → t5
t5/t5r/t5f        blinded conflicts, adjudication gate   same shape → t6
t6/t6r/t6f        verify, PRISMA                         same shape → t7
t7/t7r/t7f        RoB, GRADE                             same shape → t8
t8/t8r/t8f        evals, Wilson, sr eval, harness        same shape → t9   (installer inserts pe_t9)
t9/t9r/t9f        skill, docs, workflow, menu, edits     same shape → branch
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(pe_t9, publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 100, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/m-h-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff, decisions,
gap; the installer adds `plan`. Protected edits: `pipelines/drafts/m-h-protected-edits.json`, two edits
(two files), `before_step: t9`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Screener independence is procedural (M-H-D8).** Both screeners can read the run folder; a screener
  dispatched second could open the first's output. Identical prompts, separate files, the prompt rule and
  the dispatch-evidence check reduce, not exclude, this. A per-dispatch sandbox mask is follow-up F3.
- **R2 File-based integrity.** `sr verify` detects disagreement between events and files, but an actor
  that rewrites outputs, commits and events coherently is not caught (same class as M-D1 R2, M-D2 R2). The
  interactive coordinator can also answer a human-only gate on the CLI without `--as-agent` (true for
  every `requires_human` gate today); the dispatch heuristic stops dispatched agents only.
- **R3 One page per source (M-H-D6).** Identification is incomplete when a source has more hits than one
  page; `capped` makes it visible in the round and the PRISMA report. Pagination is F1.
- **R4 Search cache.** A living update inside the Gateway's search cache TTL returns the cached answer and
  finds nothing new; the skill tells the coordinator to check the cache age (docs) — a cache bypass for
  `sr search` is F2.
- **R5 Leak check on strategy queries.** Boolean or numeric strategy syntax can be refused by the search leak
  check; `sr protocol check` shows this before locking. Never bypassed.
- **R6 Tool definitions unverified.** RoB 2, ROBINS-I, QUADAS-2 and GRADE labels were written from the cited
  papers offline; they are marked `unverified` until a human checks them, as M-D1's reporting checklists.
- **R7 Agents as screeners.** Agent screening accuracy is not established; `sr eval` against a human
  calibration sample is the tool to measure it, and dual screening plus human adjudication is the
  safeguard. ScieFlow does not certify an agent as a screener.
- **R8 PRISMA scope.** The flow covers database searches; "other methods" (citation searching, websites)
  are not recorded yet (`scieflow research snowball` exists but does not write `sr.*` events) — F5.

## 9. After M-H

Queue (`continuity/milestones.yml`): M-I (`after: [m-c1]`) may add an events schema that must include the
six `sr.*` types; F-2 waits on M-H (`continuity/milestones.yml:28`). Follow-ups surfaced while planning:
F1 pagination per source; F2 a cache bypass or TTL override for `sr search`; F3 a per-dispatch sandbox mask
of `sr/screening/` for screener dispatches (sandbox change, needs its own approval); F4 a web panel for the
review (D7: the CLI is complete); F5 "other methods" identification (snowballing into the corpus) and
data-extraction forms; F6 a pooled-effect computation (meta-analysis) once a statistics dependency is
approved; F7 dual RoB assessment with its own kappa; F8 the Workflows table row in
`src/scieflow/research/AGENTS.md` once M-G's protected edit has landed (M-H-D12 keeps M-H outside that
region).
