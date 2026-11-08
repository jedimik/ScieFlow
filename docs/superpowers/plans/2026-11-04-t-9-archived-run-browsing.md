# T-9 plan: archived-run browsing, the implementation (Ticket 9, programme item B2) (round 1)

Pipeline `t-9-plan`, step plan, round 1, epoch 1. Card `t_df01f205`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261104_084834_313173`, source kanban).
Written 2026-11-04. Previous review: none (round 1).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no claude/codex/agy.

## 0. Inputs and references used

Immutable inputs (sha256 recomputed on this card, all equal the card pins):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…7da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |

Scope sources (read in full):

- `handoffs/pilot-roadmap-20261024/pilot-roadmap-20261011.md` section 3 (sha256 2ffee5f3…319c): Ticket 9 is "Plan
  and implement the approved design, with required tests/reviews. Archived runs visible alongside local ones and
  openable from the browser; bounded listings, consent-aware downloads and readable failure states. Missing archived
  manuscript history is explicitly described as regenerating after pull/sync, not shown as an empty history."
- Ticket 9: `/home/ubuntu/ScieFlow-claude-f2976f52/.scratch/scieflow-remaining-scope/issues/09-implement-archived-run-browsing.md`
  (sha256 82b87b62…44ac). Its seven definition-of-done items are mapped to tests in Part A 5.13.
- **The approved design (binding):** `reviews/t-8-plan.md` (sha256 85e16ea6…2853, review r2 PASS
  `reviews/t-8-plan-review-r2.md`, approved `approvals/t-8-plan-approved-human-20261104.md`: "Approve the design as
  written (incl. T8-D5: browser may start DVC downloads)"; T8-D1..D18 approved; H-T8-1..4 open; "the AGENTS.md rule
  15 sentence is decided in Ticket 9"). Its Part A becomes
  `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md` when the T-8 run merges (T-8 plan section 3).
  Cited below as **B2:<section>** (e.g. B2:5.8).
- **T-12 (prerequisite):** `reviews/t-12-plan.md` (sha256 5910047d…64f8, approved
  `approvals/t-12-plan-approved-human-20261103.md` "approve with UI rule"). Seams this plan uses: `core/control_read.py`
  (descriptor-anchored bounded reads, T-12 5.6), `workspace.describe_from(slug, status, config, updated, updated_at)`
  (T-12 5.7), `store.exclusive`/`held_locks` (T-12 5.3, not used here but in the same file), the 409 pattern of the
  T-12 API, and the binding **human UI rule** (no browser dialogs; every new control matches its nearest sibling).
- `reviews/f-2-plan.md` (sha256 6f32e994…b55b, approved): F-2 touches none of the archive code; it shifts lines in
  `core/service.py`, `web/pages.py`, `web/api.py` (F-2 plan section 3).
- Queue `continuity/milestones.yml`: `t-9` is `after: [t-8, t-12]`; both are `after: [f-2]`.
- DECISIONS D7 (CLI parity: the web app is only an interface) and D9-D11/D23 (data protection) apply. GAP-ANALYSIS
  has no B2 row; its only archive point is the no-export invariant (A-15, Q4/D17: `scratch/` is never packed), which
  this milestone does not change.

Code read read-only from git objects (`git archive <sha> | tar -x` into the profile's cache scratch and
`git show <sha>:<path>`); no checkout was touched.

- `M` = remote `main` = `e8c035db7f363052ee54201485cd1c4b069a2b7d` (`git ls-remote` on this card; objects present
  locally). It contains M-I (PR #29, `111f0e1`) and M-H (PR #30, `e8c035d`; `RUN/scratch/m-h/publish.json`
  `merged: true`). F-2, T-8 and T-12 are **not** merged (no `RUN/scratch/{f-2,t-8,t-12}/publish.json`).
- Between the T-8 plan's base `97d4bd0` and `M`, `scripts/dvc_sync.py`, `scripts/sflib/archive.py`, `core/store.py`,
  `core/project.py`, `core/service.py`, `web/*` and `.gitignore` are unchanged (`git diff --stat`); `core/jobs.py`
  gained the record check on save (+2 lines after `:283`), `core/workspace.py` gained the M-I record findings
  (+1 line after `:22`, more after `:287`), `tests/core/cli_parity.yml` gained M-I's entries, `AGENTS.md` gained two
  M-H/M-I hunks outside rule 15.
- Every `path:line` below is on `M`. F-2, T-8 and T-12 will shift some of them; t0 re-verifies every cite of section 4
  and Part A on the real base (section 6 t0 step 4).
- `dvc` is not installed on this host. As in T-8, DVC's own behaviour is *(not exercised here)*; every T-9 test uses
  a fake `dvc` executable placed first on `PATH` by the test.

## 1. For the human: what Ticket 9 changes, in plain words

Ticket 9 builds the archive-browsing design you approved on 2026-11-04 (Ticket 8). After it:

1. **The dashboard shows "Archived runs"** below your local runs: every run that exists as a zip pointer in git
   (`workspace/_archives/<slug>.zip.dvc`), with its size and state (*not downloaded*, *downloading*, *ready*, …).
   A local run that also has an archive gets an *archived* badge. Listing never contacts S3 and never runs `dvc`.
2. **Each archived run has its own page** (`/archives/<slug>`). It says what is known without downloading (size,
   checksum, whether `dvc` is installed, whether this checkout has a DVC remote configured) and offers *Download*.
3. **Download, then browse in place.** *Download* fetches the one zip as a cancellable background job, checks its
   size and checksum, and then you can open the run's folders and files straight out of the zip, read-only, with the
   run's summary (state, phase, budget left, data level) read from the archive. Nothing is unpacked.
4. **Asking twice at 1 GiB.** Below 1 GiB the button that shows the size is the consent. At 1 GiB or more the page
   first shows a confirmation panel (exact bytes, free disk, destination) and only *Confirm* starts it. An archive of
   unknown size, and every "download again over an existing file", always goes through the panel or the terminal.
5. **Not enough disk is a refusal** with both numbers (each receiving filesystem keeps 1 GiB free after the transfer,
   with 5% headroom).
6. **Restore** is a separate button that unpacks a verified zip into `workspace/<slug>` only when that folder is
   absent or empty; the run then appears as a normal local run.
7. **Failures read as sentences**, never as dvc's raw output: dvc missing, no remote, no or refused credentials,
   remote unreachable, archive missing on the remote, disk full, interrupted, wrong size or checksum, another
   operation running. A server restart or a killed download shows "interrupted" and the next click tidies up.
8. **Manuscript history** on an archive page is exactly one sentence (the approved one): "Manuscript history is not
   kept in archives. It is rebuilt from the run's drafts after you restore the run: open its drafts page, or complete a
   merge round."
9. **Everything also works from the terminal** (`scieflow workspace archives`, `scieflow workspace archive
   show|ls|cat|fetch|restore|cancel`); `--yes` is needed at 1 GiB or more, and the commands refuse inside an agent
   dispatch. Agents never see archive contents (the MCP server is unchanged).

**Decisions taken under Rule D that you may veto at the approval gate** (full list Part A 5.2): T9-D2 (the service
functions take two extra keywords the design did not name: `yes` for the terminal's `--yes` and `wait` for the
terminal's foreground wait), T9-D3 (a verify-only download job is marked `--verify-only` so it can never touch the
network), T9-D6 (the data-level parser is split so it can read `data.yml` from inside a zip; behaviour unchanged),
T9-D9 (the archive folder's file names are all enumerated so the list sorts correctly; per-archive reads stay capped
at 500), T9-D10 (archives are not added to the read-only MCP server).

**Your decisions at this gate (not Rule D):**

- **H-T9-1 = H-T8-3, the protected `AGENTS.md` edit.** Approving this plan approves this sentence, appended to root
  `AGENTS.md` rule 15 by the operator before step t6 (exact text in section 3 and
  `pipelines/drafts/t-9-protected-edits.json`): "A download of 1 GiB or more (`scieflow workspace archive
  fetch|restore … --yes`) is the user's decision too: ask before passing `--yes`." Veto it and t6 adds the same
  sentence to `skills/workspace-sync/SKILL.md` only.
- **H-T9-2 = H-T8-4, the manuscript sentence.** This plan uses the approved design sentence verbatim (item 8 above),
  which Ticket 9's checklist words as "regenerates after pull and sync". Same fact. If you want the word
  "regenerates", say so at this gate: the design document and its pinned test then change first (a round-2 plan).

**Still open, not in this run:** H-T8-1 (a remote listing: what is on S3 but has no pointer here), H-T8-2 (deleting a
downloaded copy from the browser; terminal: delete the zip, then `dvc gc`), R5 of T-8 (the loopback app now starts a
network transfer with your credentials; approved as T8-D5).

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head `dev/t-9` → base
`main`, one PR, merge with a merge commit. Title: "Ticket 9: archived-run browsing (B2): list, download, browse in
place, restore". The body (written by the branch step) is plain English: the nine points above, decisions T9-D1..D16,
H-T9-1/2, absolute test counts, the residuals of section 8.

## 2. Base, dependencies and worktree

T-9 starts only after T-8 and T-12 are merged (queue `after: [t-8, t-12]`; both wait for F-2, which waits for M-H,
M-I and M-D3, all three merged now). t0 checks:

1. `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`; object-only fetch
   `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`.
2. `git merge-base --is-ancestor e8c035db7f363052ee54201485cd1c4b069a2b7d <base>` (`M`).
3. `RUN/scratch/f-2/publish.json`, `RUN/scratch/t-8/publish.json` and `RUN/scratch/t-12/publish.json` exist with
   `"merged": true`, and each `pushed_head` is an ancestor of `base`.
4. On `base`: `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md` exists and its Part A block (from
   `## Part A — Spec` up to, not including, `## Appendix — line references on base`) equals the Part A block of
   `reviews/t-8-plan.md` (from `## Part A — Spec` up to, not including, `## Part B — Implementation plan`) byte for
   byte; `src/scieflow/core/control_read.py` exists and `workspace.describe_from` is defined.
5. Otherwise `BASE_RED` with the reason (the supervisor escalates; no polling).

Worktree: `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/t-9 RUN/scratch/t-9-wt <base>` (on a
retry reuse it at `<base>` or `<base>` plus t0's docs commit; never reset). RUN =
`/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in `RUN/scratch/t-9/`
(junit, logs, `executions.jsonl`, `allowed-paths.txt`, `pr-body.md`); pytest temp dirs under
`~/.cache/scieflow-t-9-<step>/`. No step touches `/home/ubuntu/Github/ScieFlow` or the checked-out tree of
`/home/ubuntu/ScieFlow-claude-f2976f52`. Commits are local on `dev/t-9`; review fixes are new commits.

No parallel lane is expected (T-9 is the last queue entry); if main moves, the installer's refresh step handles it.

## 3. In-flight, never-edited, protected and changed files

**INFLIGHT** (the same 9 paths as every milestone since M-A; the installer's standing rule guards them). T-9 edits
none of them:

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

Guard (must exit 0): `git -C WT diff --exit-code <base> -- <INFLIGHT>` and, on a dirty tree,
`git -C WT diff --exit-code -- <INFLIGHT>`.

**Never edited:** `config/**`, `schemas/**`, `src/scieflow/core/sandbox.py`, `src/scieflow/core/jobs.py`,
`src/scieflow/core/records.py`, `src/scieflow/core/project.py`, `src/scieflow/core/provenance.py`,
`src/scieflow/core/turn_*.py`, `src/scieflow/core/run/**`, `src/scieflow/core/agent_*.py`, `src/scieflow/mcp_server/**`,
`src/scieflow/integrations/**`, `src/scieflow/web/auth.py`, `src/scieflow/web/app.py`, `src/scieflow/web/files.py`,
`src/scieflow/web/artifact_response.py`, `src/scieflow/web/templates/base.html`, every `AGENTS.md` except the one
protected edit below, `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md`
(T-8's), `mkdocs.yml`, `pyproject.toml`, `uv.lock`, `.github/**`, `.claude/**`, `.gitignore`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`),
everything under `tests/probes/`, and every existing test file except E1/E2 below.

**Protected edit (H-T9-1, applied by the operator before t6; `pipelines/drafts/t-9-protected-edits.json`):** root
`AGENTS.md`, rule 15. Old text (occurs once on `M`, `AGENTS.md:159-160`; T-8, T-12 and F-2 do not edit `AGENTS.md`):

```
    offer moving rebuildable output to `scratch/` instead. Never upload
    anything the user did not agree to, and say afterwards what was left out.
```

New text:

```
    offer moving rebuildable output to `scratch/` instead. Never upload
    anything the user did not agree to, and say afterwards what was left out.
    A download of 1 GiB or more (`scieflow workspace archive fetch|restore …
    --yes`) is the user's decision too: ask before passing `--yes`.
```

t6 checks the file differs from base by exactly those two added lines, then commits it alone.

**Existing tests that change (and only these):**
- **E1** `tests/web/mutating_paths.py`: six new entries in both `MUTATING_PATHS` and `SAMPLES` (t5).
- **E2** `tests/core/cli_parity.yml`: seven new entries, each `new: true` with args (t4).
- **E-extra** (only if the full suite proves it necessary, each recorded in `reviews/t-9-test-changes.md` with node
  id, reason and diff): a literal this milestone rewrites (the `docs/cli.md` "Read-only views over `workspace/`."
  line, the `docs/web.md` "What stays CLI-only" list). Weakening, skipping or deselecting a test is never allowed.
  `tests/test_dvc_sync.py`, `tests/test_archive.py`, `tests/web/test_dashboard_reads.py`, `tests/core/test_jobs.py`,
  `tests/mcp_server/*` and T-8's `tests/test_docs_b2_spec.py`/`tests/test_b2_premises.py` must stay green unchanged.

**Complete file list** (nothing else changes; new test files all start `test_b2_`, new fixture modules `b2_`):

| File | Change | Step |
|---|---|---|
| `docs/superpowers/plans/2026-11-04-t-9-archived-run-browsing.md`, `docs/superpowers/specs/2026-11-04-t-9-archived-run-browsing-implementation.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/core/store.py` | `Busy`, `try_locked(path)` | t1 |
| `src/scieflow/core/archives.py` (new) | constants, pointers, discovery, states, liveness, reconcile, classifier, disk rule | t1 |
| `src/scieflow/core/control_read.py` | `read_bytes(dir_fd, name, cap)` only if T-12's module has no public bounded byte reader (t0 inventory (c)) | t1 |
| `tests/core/test_b2_store_try_locked.py`, `tests/core/test_b2_constants.py`, `tests/core/test_b2_pointers_states.py`, `tests/core/test_b2_job_liveness.py`, `tests/core/test_b2_classify_disk.py`, `tests/fixtures/b2_archives.py` (new) | | t1 |
| `src/scieflow/core/archives.py` | central-directory pre-check, zip index + LRU, entries, member reads, control members, summary | t2 |
| `src/scieflow/core/data/levels.py` | `parse_text`, `level_of` extracted; `read`/`level_info` behaviour unchanged | t2 |
| `tests/core/test_b2_zip_index.py`, `tests/core/test_b2_members.py`, `tests/core/test_b2_summary.py`, `tests/core/test_b2_levels_parse.py` (new) | | t2 |
| `scripts/dvc_sync.py` | `fetch`, `restore`; `--state-dir` on push/pull/fetch/restore; operation lock; `LOCKED_EXIT`; `run_cmd_tail` | t3 |
| `scripts/sflib/archive.py` | `md5_file`, `write_record` | t3 |
| `tests/test_b2_dvc_fetch_restore.py`, `tests/test_b2_dvc_locks.py`, `tests/fixtures/b2_fake_dvc.py` (new) | | t3 |
| `src/scieflow/core/service.py` | seven public functions, `ArchiveRefused` | t4 |
| `src/scieflow/core/archives_cli.py` (new), `src/scieflow/core/workspace.py` (register two commands, at most 6 lines at the end) | CLI | t4 |
| `tests/core/test_b2_service.py`, `tests/core/test_b2_service_jobs.py`, `tests/core/test_b2_cli.py`, `tests/core/test_b2_mcp_hidden.py` (new), `tests/core/cli_parity.yml` (E2) | | t4 |
| `src/scieflow/web/pages.py`, `src/scieflow/web/api.py`, `src/scieflow/web/templates/dashboard.html`, `src/scieflow/web/templates/archive.html`, `src/scieflow/web/templates/archive_files.html`, `src/scieflow/web/templates/archive_file.html` (the last three new) | routes and pages | t5 |
| `tests/web/test_b2_dashboard.py`, `tests/web/test_b2_archive_page.py`, `tests/web/test_b2_archive_browse.py`, `tests/web/test_b2_actions.py`, `tests/web/test_b2_failures.py`, `tests/web/test_b2_ui_rule.py` (new), `tests/web/mutating_paths.py` (E1) | | t5 |
| `AGENTS.md` (protected edit, applied by the operator), `docs/web.md`, `docs/DVC_STORAGE.md`, `docs/cli.md`, `skills/workspace-sync/SKILL.md` | docs | t6 |
| `tests/test_b2_docs.py` (new) | | t6 |

**Scope guard** (must exit 0; run by every implementation, fix, branch and final card): t0 writes
`RUN/scratch/t-9/allowed-paths.txt` with exactly the repository paths named in the table above, one per line (every
cell can name several paths; `control_read.py` included even if t1 does not need it); then
`test -z "$(git -C WT diff --name-only <base>..HEAD | grep -v -x -F -f RUN/scratch/t-9/allowed-paths.txt | grep -v -E '^tests/(core/|web/)?test_b2_[a-z0-9_]+\.py$|^tests/fixtures/b2_[a-z0-9_]+\.py$|^reviews/')"`.
A recorded E-extra entry is added to `allowed-paths.txt` by the card that records it (and named in its result).

Production lines (estimate): `archives.py` 650, `service.py` +260, `archives_cli.py` 170, `dvc_sync.py` +170,
`sflib/archive.py` +35, `pages.py` +170, `api.py` +70, templates 220, `store.py` +20, `levels.py` ±15,
`control_read.py` +25. Hotspots: `service.py`, `pages.py`, `api.py` (shared with F-2 and T-12); T-9 only adds new
functions and routes, and edits one existing function, `pages.dashboard` (one service call and two template keys).

## 4. Current code, verified (on M = e8c035d) and the prerequisites as planned

### 4.1 Archive files, pointers, `dvc_sync.py` (unchanged since T-8's base; T-8 section 4.1-4.2 holds)

- `scripts/sflib/archive.py:17` `ARCHIVE_DIR`; `:43-49` `archive_path`/`pointer_path`; `:52-57` `archived_slugs`;
  `:121-152` `build_zip` (`ZIP_STORED` `:124`, `.partial` `:135`, replace `:149`, unlink on error `:151`);
  `:155-165` `verify_zip`; `:172-182` `ensure_space` (`headroom` `:173`); `:185-190` `_check_members`;
  `:193-194` `_is_link`; `:213-241` `extract_zip` (refusal `:216-217`, staging `:219`); `:244-268` `link_to_cache`
  (`.link` temp `:261`).
- `scripts/dvc_sync.py:27-32` puts `scripts/` on `sys.path` and imports `scieflow.core.config` and `sflib.archive`;
  `:35-48` `load_env_into_environ`; `:90-93` `run_cmd` (inherits stdout/stderr); `:140-141` `_remote_args`;
  `:144-191` `push_archive`; `:194-219` `pull_archive` (`dvc pull` `:204`); `:247-278` `cmd_push` (per-slug loop
  `:271-274`); `:300-317` `cmd_pull` (loop `:310-313`); `:329-343` `workspace_summary`; `:346-378` `build_parser`;
  `:381-…` `main` (`config.repo_root()` `:382`, `.env` `:387-388`).
- Tests drive `dvc_sync` by monkeypatching `run_cmd` (`tests/test_dvc_sync.py:95-102`) and pass no state directory
  (`:197-345`), so the new `state_dir` keyword must default (T8-D14).

### 4.2 Core

- `core/store.py:22` `LOCK_TIMEOUT = 30`; `:26-36` `new_id` (26-character Crockford ULID); `:39-44` `locked` (waits);
  `:47-57` `_atomic_write`; `:60-63` `write_text`. No non-blocking lock (T8-D14 adds `try_locked`).
- `core/jobs.py:27-28` `STATES`/`FINAL`; `:277-278` `jobs_dir` (`run_dir=None` → `state_dir/jobs`); `:281-282`
  `_record_path`; `:285-288` `save` now calls `records.check_job` first (M-I); `:291-292` `load`; `:302-320`
  `peek_state`; `:323-335` `list_jobs` (no run dir: every record of the project and of every run); `:338-339`
  `find`; `:348-355` `_alive`; `:358-409` `start` (direct argv when not sandboxed `:381-386`, `start_new_session`
  `:390`); `:437-447` `wait`; `:465-476` `cancel`; `:520-529` `reconcile` (writes `lost` and `finished`).
- `core/records.py:171-209` `check_job`: `kind` must match `[a-z][a-z0-9_-]{0,31}` (`:29`, `:185-186`, so
  `archive-fetch`/`archive-restore` pass) and a final state needs `finished` (`:208-209`); `reconcile_archive_job`
  therefore sets `finished` with `lost` (as `jobs.reconcile` does).
- `core/project.py:19` `SLUG_RE`, `:26-52` `clean_slug`, `:68-71` `state_dir` (`$SCIEFLOW_STATE_DIR` or
  `<root>/.scieflow`); `tests/conftest.py:10-11` isolates `SCIEFLOW_STATE_DIR` per test.
- `core/workspace.py:24` `MODULE_DIRS`; `:31-43` `Run`; `:51-56` `_load`; `:59-76` `_state`; `:79-95` `_updated`,
  `_updated_at`; `:109-132` `describe`; `:135-155` `list_runs` (skips `.`/`_`, `news`, `chats` `:144`); `:231-237`
  `_archive_mod` (imports `sflib.archive`, falling back to `config.repo_root()/scripts`); `:327-329` the `workspace`
  click group (registered in `cli.py:56-61`).
- `core/run/budget.py:40-41` `read_budget`; `:82-87` `remaining_fraction` (raises `KeyError`/`TypeError` on a bad
  shape).
- `core/data/levels.py:19` `DATA_FILE`; `:111-136` `_check(raw)`; `:138-164` `read(ws)` (path based: symlink,
  absent, unreadable, YAML error, empty → `Invalid`); `:167-175` `level_info` (invalid → `L2`, `:170-171`).
- `integrations/scite/mcp.py:160-166` `in_dispatch` (already imported by core: `core/run/converse_cli.py:46-48`).

### 4.3 Service and web

- `core/service.py:53` `ServiceError`; `:80-87` `_ws`; `:104-127` `list_runs_with_budget` (the dashboard's whole
  read cost, pinned by `tests/web/test_dashboard_reads.py:92-131`, which counts opens and listings keyed by
  `workspace/<run>/…` and forbids anything but `status.yml`, `config.yml`, `budget.yml` and gate files per run);
  `:258-281` agent jobs waited on by a daemon thread (`:279`); `:284-291` `cancel_job` (uses `jobs.find`);
  `:934-1033` `merge_round` (syncs provenance `:1022`); `:1036-1073` `manuscript_history` (syncs `:1064`); `:2366-2397`
  `_data_panel`; `:2404-2425` `run_overview` (never syncs).
- `web/app.py:55-57` every `ServiceError` becomes HTTP 404 JSON; so a refusal that must read as 409 or re-render a page
  is caught in its route (T9-D2).
- `web/pages.py:33` router requires the session; `:35` `MUTATE`; `:187-200` `dashboard`; `:761-786` `act` (form
  POST pattern); `:844-858` `files_page`; `:861-893` `file_view` (`display_panel`, `ArtifactResponse`).
- `web/api.py:24` router (`/api/v1`, session); `:172-183` `cancel` (plain `def`, the reason in its docstring).
- `web/files.py:14` `MAX_INLINE = 200_000`; `:15-18` `TEXT_SUFFIXES`; `:23-25` `INLINE_SAFE_TYPES`; `:81-94`
  `download_type`; `:117` `.lock` names hidden.
- `web/artifact_response.py:32` `X-Content-Type-Options: nosniff`. `core/turn_output.py:15` `CHUNK_BYTES = 65536`,
  `:475-477` `display_chunks`.
- Templates: `dashboard.html:10-27` the runs table (`<table>`, `class="dim"`), `:30-67` gates; `files.html:6-19` the
  files table; `run.html:289-301` action forms (`<form method="post" class="actions">`, hidden `csrf_token`, plain
  `<button>`); `run.html:6` error paragraph `<p class="state-failed" role="alert">`. These are the siblings the UI rule
  binds.
- `tests/web/mutating_paths.py:18-43` `MUTATING_PATHS`, `:48-49` `PLACEHOLDERS` (`slug: r1`), `:52-81` `SAMPLES`;
  `tests/web/test_read_only.py` and `tests/web/test_mutations.py` drive guards from them;
  `tests/web/test_async_routes.py` + `tests/web/async_allowlist.py` forbid undeclared `async def` handlers.
- `tests/core/test_cli_parity.py:52-53` (a) the table covers exactly the public service functions; `:56-63` (b) every
  `cli:` resolves; `:66-95` (c) each `new:` entry's command, invoked with its `args` and the service function replaced
  by a recorder returning `{"recorded": name}`, exits 0 and calls it exactly once. So every new CLI command must accept
  a dict without the keys it normally prints (T9-D7).
- `mcp_server/tools.py:164` `TOOLS` (a fixed tuple; runs come from `list_runs_with_budget`, `:120-123`);
  `tests/mcp_server/test_mcp_parity.py` checks each tool's service function has a `cli:` parity entry.

### 4.4 Docs and rules

- `docs/web.md:137` "Pages", `:446` "The API", `:530-549` "What stays CLI-only" (DVC uploads `:538-540`).
- `docs/DVC_STORAGE.md:126-165` "3.5 Archive Mode"; `:167` "## 4." (the new §3.6 goes between).
- `docs/cli.md:506-538` the `scieflow workspace` section ("Read-only views over `workspace/`." `:508`).
- `skills/workspace-sync/SKILL.md:42-53` the upload-asking table.
- `AGENTS.md:150-160` rule 15 (uploads).
- `.gitignore:10-12` `workspace/_archives/*.dvc` tracked, the rest ignored; `:24` `.dvc/config`; `:40` `.scieflow/`.
- `mkdocs.yml:51-52` `exclude_docs: superpowers/`.

### 4.5 Prerequisites as planned (not on M; t0 records their real shape on base)

- **T-8** commits the design document (B2 spec), `tests/test_docs_b2_spec.py` (pins Part A byte-equal to the plan, the
  16-constant table, the 12 failure codes, the manuscript sentence) and `tests/test_b2_premises.py` (P6, P7, P8, P11,
  P12). It changes no production file.
- **T-12** adds `core/control_read.py` with `CONTROL_CAP`, `ControlRead(status, data, reason)`, `open_dir(root,
  parts)`, `read_yaml(dir_fd, name, cap)`, `stat_entry`, `read_link`, `scan_dir(dir_fd, limit)` (T-12 5.6);
  `workspace.describe_from(slug, status, config, updated, updated_at)` called by `describe` (T-12 5.7);
  `store.exclusive`/`held_locks`/`LockSet` (T-12 5.3); `/workspace` and `/runs/{slug}/health` pages, six
  `MUTATING_PATHS` entries and six `cli_parity.yml` entries; `templates/base.html` gains a `workspace` nav link. T-12
  does not touch archives, the dashboard or `dvc_sync.py` (T-12 section 3 file list).
- **F-2** shifts lines in `service.py`, `pages.py`, `api.py`, `run.html` (F-2 section 3); nothing T-9 calls changes.

## Part A — Spec

### 5.1 Relation to the approved design

The B2 design (T-8 Part A, committed as `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md`) is
binding: its decisions T8-D1..D18, states (B2:5.5), read rules (B2:5.6, 5.6.1), page (B2:5.7), actions (B2:5.8),
failure codes and sentences (B2:5.9), degradation (B2:5.10), CLI and names (B2:5.11, 5.12), constants (B2:5.13),
state files (B2:5.14) and security properties (B2:5.15). This Part A adds only what the design left to Ticket 9:
module layout, function signatures, return shapes, template names and the test matrix. Where anything below seems to
contradict B2, B2 wins and the reviewer reports it as a finding. Line corrections in the B2 spec's appendix apply.

### 5.2 Decisions (T9-D1..D16)

- **T9-D1 One core module.** `src/scieflow/core/archives.py` holds everything of B2:5.11's "pointer parsing, states,
  the classifier, bounds, the zip index, `effective_job` and `reconcile_archive_job`"; it imports neither `web` nor
  `service`. `scripts/sflib/archive.py` gains only `md5_file` and `write_record` (used by `dvc_sync.py`).
  `dvc_sync.py` imports `scieflow.core.archives` (it already imports `scieflow.core.config`, `dvc_sync.py:31`) for
  constants, paths, the classifier and the record shape, so one definition serves both processes.
- **T9-D2 Service signatures and refusals.** The design's signatures (B2:5.8) gain keyword-only `yes: bool = False`
  and `wait: bool = False` on `fetch_archive` and `restore_archive`. `yes=True` is the terminal's `--yes`: it stands
  for consent at any size, for an unknown size and for a replace (B2:5.4); the web never passes it. `wait=True` (the
  terminal) waits for the job in the foreground and returns its outcome; `wait=False` (the web) starts a daemon
  thread that waits, as `service.py:279` does. Every refusal is `ArchiveRefused(ServiceError)` with `reason` (a code of
  B2:5.9), `message` (its fixed sentence) and `details` (numbers only: sizes, free bytes, the exact `--yes` command).
  Pages catch it and re-render the archive page; the API returns `409 {"reason", "message"}`; the CLI prints the
  message and exits 1. An unknown slug or a slug without a pointer is a plain `ServiceError` (404, `app.py:55-57`).
- **T9-D3 Verify-only is explicit.** For state `unverified` the service starts `dvc_sync.py fetch SLUG --verify-only
  --state-dir DIR`; with `--verify-only` the subcommand never invokes `dvc` (it fails `verify-size` instead). The
  liveness rule (B2:5.6.1 step 4) tells `verifying` from `downloading` by that argv flag.
- **T9-D4 Captured dvc output.** `fetch` runs `dvc` through the new `run_cmd_tail(cmd, cwd, tail=DVC_OUTPUT_TAIL)`:
  stdout and stderr merged, copied line by line to the subcommand's stdout (the job log keeps everything), the last
  `DVC_OUTPUT_TAIL` bytes kept in memory; `archives.classify(exit_code, tail: bytes) -> str` is pure and is the only
  classifier (B2:5.9 order, case-insensitive). Launch `FileNotFoundError` → `dvc-missing`. `push`/`pull` keep
  `run_cmd`.
- **T9-D5 `locked` from the job.** A finished archive job with exit code `LOCKED_EXIT` reads as `locked`. Before
  starting a job the service also probes the operation lock (`store.try_locked(<state_dir>/archives/<slug>)`, entered
  and left at once) and refuses `locked` without starting anything when a `push`/`pull` holds it; the job re-checks
  under its own lock (the probe is advisory, the job's lock decides).
- **T9-D6 Data level from bytes.** `levels.parse_text(text: str) -> DataFile | Invalid` (YAML parse + `_check`, the
  same `Invalid` reasons as `read`) and `levels.level_of(got) -> LevelInfo` (the body of `level_info`) are extracted;
  `read` and `level_info` call them and keep their behaviour (all existing `tests/core/data/test_levels.py` green
  unchanged). A `data.yml` member is decoded as UTF-8 (`Invalid("data.yml: cannot be read")` on error) and parsed
  with `parse_text`; an oversized member is `too large to read here` and its level reads `L2` (fail-closed, as
  an unreadable file in `read`).
- **T9-D7 CLI module.** `src/scieflow/core/archives_cli.py` defines `archives_cmd` and the `archive` group; the end
  of `core/workspace.py` registers both on the `workspace` group. `archives_cli.py` imports `service` inside each
  command only (no import cycle: `service` imports `workspace`). Every command prints `--json` output as the service
  dict, otherwise a sentence built with `.get` so a parity recorder dict exits 0 (section 4.3).
- **T9-D8 Templates.** `archive.html` (the archive page with its panels), `archive_files.html` (entries),
  `archive_file.html` (one text member); the dashboard section is in `dashboard.html`. No new CSS class, no
  JavaScript, no `|safe`, no browser dialog; controls copy their siblings (section 4.3).
- **T9-D9 Listing.** All names in `workspace/_archives/` are enumerated (names only, streamed, no stat) so the list
  sorts by slug and the "N more not shown" count is exact; per-pointer reads happen only for the first `LIST_CAP`
  names after sorting. Legacy `workspace/<slug>.dvc` pointers come from the same single listing of `workspace/` and are
  read only when `workspace/<slug>` does not exist (T8-D2).
- **T9-D10 Not on the MCP server.** No MCP tool is added, and `list_runs_with_budget` (the MCP runs tool's source) is
  unchanged, so archived-only runs and archive contents never reach an agent (T8-D18). A test pins it.
- **T9-D11 Member responses.** Text members are rendered from `display_chunks` over `CHUNK_BYTES` reads; other
  members are a Starlette `StreamingResponse` over a sync generator (run in the threadpool, plain `def` handler) of
  `CHUNK_BYTES` reads with `download_type` of the member's `PurePosixPath` name, `X-Content-Type-Options: nosniff` and,
  for non-inline types and `download=1`, `Content-Disposition: attachment; filename="<basename>"` (quoted, control
  characters and quotes replaced by `_`). No range support.
- **T9-D12 Bounded file reads.** Pointers, verification records, `<slug>.job` and the one job record are read through
  `control_read.open_dir` + a bounded byte read (`read_bytes`, or T-12's equivalent if t0 finds one public),
  no-follow, regular files only, `cap + 1` bytes; JSON and YAML are parsed only after the bound holds.
- **T9-D13 Disk rule.** Receiving locations: the DVC cache `<root>/.dvc/cache` (or its nearest existing ancestor) and
  `workspace/_archives/` for a download, `workspace/` for a restore; grouped by `os.stat(...).st_dev`; requirement per
  group `ceil(received × DISK_HEADROOM) + DISK_RESERVE_BYTES`, free from `shutil.disk_usage`. A custom DVC
  `cache.dir` is not read (ScieFlow reads no DVC configuration content, B2:5.6); residual R5.
- **T9-D14 Record after link.** `fetch` writes the verification record after `link_to_cache`, with the final
  `os.stat` of `<slug>.zip` (a hard link changes the inode), so a `ready` copy stays `ready`.
- **T9-D15 Restore re-checks.** `dvc_sync.py restore` re-reads pointer, zip stat and record under the operation lock
  and refuses `not-ready` unless they match; then `verify_zip` (full CRC pass), then `extract_zip` without `force`.
- **T9-D16 Names.** Everything named in B2 keeps its name; the additions are only: `ArchiveRefused`, `yes`/`wait`
  keywords, `--verify-only`, `run_cmd_tail`, `md5_file`, `write_record`, `levels.parse_text`/`level_of`,
  `control_read.read_bytes` (conditional), `archives_cli.py`, and the template names of T9-D8.

### 5.3 Module map

```
browser (/ , /archives/<slug>, …/files, …/file)          CLI scieflow workspace archives | archive …
   │ pages.py / api.py (def handlers; POST under MUTATE)        │ archives_cli.py (thin)
   ▼                                                            ▼
service.archived_runs / archive_detail / archive_entries / archive_member / fetch_archive / restore_archive / cancel_archive_job
   │ (refusals: ArchiveRefused; start lock; jobs.start; daemon wait or foreground wait)
   ▼
core/archives.py ── pointers, states, effective_job (read-only), reconcile_archive_job, classify, disk, zip index
   │                    uses control_read (bounded no-follow reads), store.try_locked, levels.parse_text,
   │                    workspace.describe_from, budget.remaining_fraction
   ▼
jobs.start(argv=[python, -B, scripts/dvc_sync.py, fetch|restore, SLUG, …, --state-dir, DIR], run_dir=None)
   ▼
scripts/dvc_sync.py fetch|restore ── operation lock, dvc pull (run_cmd_tail), size + MD5, link_to_cache,
                                     verification record / outcome (sflib.archive.write_record)
```

### 5.4 `core/archives.py` interfaces

Constants: exactly the 16 names and values of B2:5.13 as module attributes (`CONSENT_BYTES` … `LOCKED_EXIT`), plus
`ARCHIVE_STATE = "archives"` (the state subdirectory) and `JOB_KINDS = ("archive-fetch", "archive-restore")`.
`tests/core/test_b2_constants.py` parses the committed B2 spec's 5.13 table and asserts every value equals the module
attribute, and `INLINE_MAX_BYTES == web.files.MAX_INLINE`.

Paths (pure): `state_dir_for(project) -> Path` (= `project.state_dir / "archives"`); `lock_path(state, slug)`
(`<slug>`, so `FileLock` uses `<slug>.lock`), `start_lock_path` (`<slug>.start`), `record_path` (`<slug>.json`),
`job_pointer_path` (`<slug>.job`).

Pointers:
- `Pointer(slug, kind: "zip"|"directory"|"unreadable", md5, size: int|None, dvc2: bool, reason: str|None, path)`.
- `read_pointer(dir_fd, name, slug, *, legacy=False) -> Pointer`: bounded by `POINTER_MAX_BYTES`, `yaml.safe_load`;
  B2:5.5 rules (one `outs` entry, 32 lowercase hex `md5`, `path == "<slug>.zip"`, optional int `size >= 0`, optional
  `hash: md5`; a `.dir` md5 in an `_archives` pointer, a symlink, oversize or any other shape → `unreadable` with the
  reason word; legacy pointers need a `.dir` md5 → `directory` with `size` and `nfiles`).
- `discover(project) -> Discovery(rows: list[Pointer], total, shown, skipped_names, cap_note)`: T9-D9; names not
  passing `project.clean_slug` are counted in `skipped_names`, never listed.

Local state (B2:5.5): `local_state(project, ptr) -> LocalState(state, relation, zip_stat, record, job, leftovers,
symlink_note)` with `state` ∈ `not-downloaded|downloading|incomplete|unverified|verifying|different|outdated|ready|
restoring`, `relation` ∈ `archived-only|also-local|alias`. Inputs only: `lstat` of `_archives/<slug>.zip` (and the
B2:5.5 symlink rule: realpath inside `<root>/.dvc/cache/` else `incomplete` with the note), bounded read of the record
(`RECORD_MAX_BYTES`), `effective_job`, `lstat` of `workspace/<slug>`, `lstat` of the four leftover patterns of
B2:5.7.6 (the two `workspace/.<slug>.*` patterns by a name filter over the `workspace/` listing already made). Order of
precedence: a running job's state first, then the file states in the B2 table order.

Liveness (B2:5.6.1, exact): `effective_job(project, slug) -> JobView(id, kind, running: bool, verify_only: bool,
outcome: str|None, exit_code)` — pure read; `reconcile_archive_job(project, slug) -> bool` — the only writer of
`lost` for archive jobs, `state="lost"`, `finished=jobs._now()`, `jobs.save` (so `records.check_job` holds), no event;
called only under the start lock by the service's fetch/restore/cancel.

Classifier and disk: `classify(exit_code: int|None, tail: bytes) -> str` (B2:5.9, first match wins; `None`/signal →
`interrupted`; exit 127 → `dvc-missing`; anything unmatched → `dvc-failed`); `SENTENCES: dict[code, str]` with the
exact B2:5.9 and refusal sentences (`<n>`, `<log path>`, `<12 chars>` filled by `sentence(code, **values)`);
`disk_check(root, receivers: list[tuple[Path, int]]) -> DiskCheck(ok, groups=[{path, received, needed, free}])` (T9-D13).

Zip reading (state `ready` only; each opens the zip read-only with `O_NOFOLLOW`, except the accepted cache-symlink
case of B2:5.5):
- `precheck_central_directory(fd, size) -> (entries, cd_bytes)`: reads at most the last 65,557 bytes, finds the
  end-of-central-directory record, follows the Zip64 locator/record when present; refuses `too-many-entries` above
  `MAX_ARCHIVE_ENTRIES` or `MAX_CENTRAL_DIRECTORY_BYTES`, `unreadable-zip` when no record is found. Runs before
  `zipfile.ZipFile` parses anything.
- `index(project, slug) -> ZipIndex`: one `zipfile.ZipFile` open per `(st_dev, st_ino, st_size, st_mtime_ns)` key,
  kept in a process-wide LRU of `INDEX_CACHE` entries guarded by a `threading.Lock`; `ZipIndex` holds the member list
  classified `file|dir|link|unsafe|encrypted|unsupported` (unsafe = the `_check_members` rule applied to the name
  alone, without touching the filesystem; encrypted = flag bit 0; unsupported = compression not stored/deflated), the
  implied directories, totals and counts. Nothing is cached on disk.
- `entries(idx, path, page) -> {path, page, pages, total, entries: [{name, path, kind, size, target?}], hidden_locks,
  unsafe_count, note}`: B2:5.6 (directories first, then files, by name; `.lock` hidden; `PAGE_ENTRIES` per page with
  "entries 501-1000 of 2,345"; a link shows its target text from the member bytes, bounded by 4096, never followed).
- `open_member(idx, path) -> (ZipInfo, reader)`: `path` must equal a central-directory name exactly and be a `file`
  entry, else `no-such-member`; the reader yields `CHUNK_BYTES` blocks; `zipfile.BadZipFile`/CRC error →
  `corrupt-member` (inline) or the stream ends and is logged (download).
- `summary(idx) -> dict`: the control members of B2:5.6 (each only when `file_size <= CONTROL_MAX_BYTES`, else
  "too large to read here"; unparsable → "unreadable"; absent → "not in this archive"); `workspace.describe_from(slug,
  status, config, updated, updated_at)` with `updated`/`updated_at` from the members' `ZipInfo.date_time` labelled as
  the packing machine's local date (B2:5.6 last bullet); budget remaining via `budget.remaining_fraction` (errors →
  "unreadable"); data level via T9-D6; totals.

### 5.5 Small additions outside `archives.py`

- `store.Busy(Exception)`; `store.try_locked(path)`: a context manager over `FileLock(str(path) + ".lock",
  timeout=0)`; `filelock.Timeout` → `Busy`; the parent directory is created only by callers on write paths (a GET never
  calls it). `store.locked` and `LOCK_TIMEOUT` unchanged.
- `control_read.read_bytes(dir_fd, name, cap) -> ControlRead` (data = bytes): the open/`fstat`/`S_ISREG`/`cap + 1`/
  re-`fstat` rules of T-12 5.6 without parsing; added only if T-12's module has no public equivalent (t0 inventory (c));
  `read_yaml` unchanged either way.
- `levels.parse_text`, `levels.level_of` (T9-D6).
- `sflib.archive.md5_file(path, chunk) -> str` (streamed); `sflib.archive.write_record(path, data: dict)` (JSON,
  atomic: temp file in the same directory, `fsync`, `os.replace`; creates the parent).

### 5.6 `scripts/dvc_sync.py`

- All four of `push`, `pull`, `fetch`, `restore` accept `--state-dir DIR` (default `Project(<root>).state_dir`).
  `cmd_push`/`cmd_pull` gain keyword `state_dir: Path | None = None` (same default), and wrap each archive-mode slug's
  `push_archive`/`pull_archive` call in `store.try_locked(<state>/archives/<slug>)`; `Busy` → print
  `archives.sentence("locked")` with the slug, rc `LOCKED_EXIT` for that slug (counted by the existing loop).
  Directory-mode slugs take no lock (they never touch `_archives/`).
- `fetch SLUG [--replace] [--verify-only] [--remote NAME] [--state-dir DIR]` (B2:5.8 plus T9-D3): operation lock
  (busy → `locked` sentence, exit `LOCKED_EXIT`, nothing written); pointer must be `zip`; unless `--verify-only` or
  (size matches and not `--replace`): `run_cmd_tail(["dvc", "pull", *_remote_args(remote), *(["--force"] if replace
  else []), <pointer rel>])`; non-zero → outcome `failed` with `classify(rc, tail)`; then size check (`verify-size`),
  `md5_file` with `MD5_CHUNK_BYTES` (`verify-md5`), `link_to_cache` (T9-D14), record `{pointer_md5, size, st_dev,
  st_ino, st_mtime_ns, verified_at, linked, outcome: "ready", reason: null, exit_code: 0, pid}`; every failure writes
  `{outcome: "failed", reason, exit_code, pid}` (keeping a previous verification only when the file stat still
  matches it, so a failed re-download over an untouched `ready` copy stays `ready`, B2:5.5); exits 0 on `ready`, 1 on
  a failure. Never extracts, never deletes a zip.
- `restore SLUG [--state-dir DIR]`: operation lock; T9-D15; `extract_zip(zip, workspace/<slug>)` without force;
  outcome `{outcome: "restored"|"failed", reason, exit_code, pid}` merged into the record; exit 0/1.
- `main()` dispatches the two new subcommands like `push`/`pull`; `.env` loading unchanged (`:387-388`).

### 5.7 Service (`src/scieflow/core/service.py`)

New exception `ArchiveRefused(ServiceError)` (`reason`, `message`, `details`). New public functions (each also in
`tests/core/cli_parity.yml`, E2):

| Function | Returns | CLI (parity, `new: true`, args) |
|---|---|---|
| `archived_runs(project)` | `{rows: [{slug, kind, size, md5_12, state, relation, outcome, reason, command?}], total, shown, cap, cap_note, skipped_names}` | `workspace archives` `[]` |
| `archive_detail(project, slug)` | header, `environment {dvc, remote_config}`, `disk [...]`, `state`, `relation`, `outcome`, `actions [...]`, `leftovers [...]`, `manuscript` (the B2:5.7.4 sentence), and in state `ready`: `data_level {level, reason, banner}`, `summary {…}`, `totals {…}` or `browse_refusal` | `workspace archive show` `["r1"]` |
| `archive_entries(project, slug, path="", page=1)` | 5.4 `entries` | `workspace archive ls` `["r1"]` |
| `archive_member(project, slug, path, *, mode="view")` | `view`: `{path, size, text, complete, reason}`; `raw`: `{path, size, media_type, attachment, filename, stream}` | `workspace archive cat` `["r1", "status.yml"]` |
| `fetch_archive(project, slug, *, confirm_size=None, replace=False, yes=False, wait=False)` | `{slug, state, started, job_id, message}` (+ `outcome`, `reason` when `wait`) | `workspace archive fetch` `["r1"]` |
| `restore_archive(project, slug, *, confirm_size=None, yes=False, wait=False)` | same shape | `workspace archive restore` `["r1"]` |
| `cancel_archive_job(project, slug)` | `{slug, cancelled, state, message}` | `workspace archive cancel` `["r1"]` |

Rules (order matters; every refusal starts nothing and writes nothing):
1. `slug` through `project.clean_slug`; no pointer for it → `ServiceError` (404).
2. `fetch`/`restore`/`cancel` inside a dispatch (`in_dispatch()`) → `in-dispatch`.
3. Enter `store.try_locked(start_lock_path)` (creating `<state_dir>/archives/` first; `Busy` → `locked`); under it:
   `reconcile_archive_job`; a still-running job → `locked`; the operation-lock probe (T9-D5).
4. `fetch`: non-`zip` pointer → `not-zip-pointer`; state `ready` → no-op `{started: false, state: "ready"}`; state
   `unverified` → verify-only job (no consent, no disk check, no dvc check); a local zip that is not `ready` and
   `replace` false → `replace-required`; `dvc` not on `PATH` → `dvc-missing`; unknown size and not `yes` →
   `size-unknown` (details carry `scieflow workspace archive fetch <slug> --yes`); disk rule (known size) →
   `no-space` with the groups; consent: (`size >= CONSENT_BYTES` or `replace`) and not `yes` → `confirm_size` absent →
   `confirm-required`, differs from the size computed now → `confirm-mismatch`.
5. `restore`: state not `ready` → `not-ready`; relation not `archived-only` or `workspace/<slug>` non-empty →
   `run-exists`; unpacked size (sum of `file_size`) through the disk rule and the consent rule (no `replace` here).
6. Start: `jobs.start(project, argv, kind=…, cwd=project.root, run_dir=None, label=slug)` with argv
   `[sys.executable, "-B", str(project.root / "scripts" / "dvc_sync.py"), "fetch"|"restore", slug, (--replace),
   (--verify-only), "--state-dir", str(project.state_dir)]`; launch `FileNotFoundError` → `dvc-missing`; atomic write
   of the job id to `<slug>.job`; release the start lock; then `wait` → `jobs.wait` in the foreground and the outcome
   from `effective_job`; else a daemon thread runs `jobs.wait`.
7. `cancel`: under the start lock, reconcile; a running job → `jobs.cancel` on the record read by path (never
   `jobs.find`), `{cancelled: true, state: <file state>}`; none → `{cancelled: false}`.
8. GET functions (`archived_runs`, `archive_detail`, `archive_entries`, `archive_member`) never write, lock, start a
   process or open a socket; any unexpected read error degrades to a reason (B2:5.10 "Every GET degrades, never
   raises"); only an unknown slug is an error.

### 5.8 CLI (`scieflow workspace …`)

`archives [--json]`; `archive show SLUG [--json]`; `archive ls SLUG [DIR] [--page N] [--json]`; `archive cat SLUG
MEMBER [--out FILE]` (raw bytes; to stdout only when stdout is not a terminal or the member is text, else exit 1 with
"use --out FILE"); `archive fetch SLUG [--yes] [--replace]`; `archive restore SLUG [--yes]`; `archive cancel SLUG`.
`fetch`/`restore` call the service with `yes=<flag>`, `wait=True` and print the outcome sentence; a refusal prints
its message (for `confirm-required` the size and the exact `--yes` command) and exits 1. `workspace list` is
unchanged.

### 5.9 Web

Routes (all plain `def`, under the session router; POSTs under `MUTATE`; `{slug}` through `clean_slug`, unknown → 404):

| Method | Path | Handler | Renders / returns |
|---|---|---|---|
| GET | `/` | `dashboard` (edited) | adds `archives = service.archived_runs(project)` and `archived_local = {slugs with relation also-local}`; section *Archived runs* after the runs table, before *Open gates*; badge `<span class="dim">archived</span>` linking to `/archives/<slug>` on local rows |
| GET | `/archives/{slug}` | `archive_page` | `archive.html` from `archive_detail` |
| GET | `/archives/{slug}/files?path=&page=` | `archive_files` | `archive_files.html` from `archive_entries` |
| GET | `/archives/{slug}/file?path=&download=` | `archive_file` | `archive_file.html` (text) or the stream (T9-D11) |
| POST | `/archives/{slug}/fetch` | `archive_fetch` | form `confirm_size`, `replace`; success → 303 to `/archives/{slug}`; `ArchiveRefused` → `archive.html` re-rendered (200) with `refusal`; `confirm-required` renders the confirmation panel |
| POST | `/archives/{slug}/restore` | `archive_restore` | form `confirm_size`; as above |
| POST | `/archives/{slug}/cancel` | `archive_cancel` | 303 to `/archives/{slug}` |
| GET | `/api/v1/archives`, `/api/v1/archives/{slug}`, `/api/v1/archives/{slug}/entries?path=&page=` | | the service dicts as JSON |
| POST | `/api/v1/archives/{slug}/fetch`, `/restore`, `/cancel` | | the service dict; `ArchiveRefused` → `409 {"reason", "message"}` |

E1 adds the six POST paths to `MUTATING_PATHS` and `SAMPLES` (`{}` for cancel, `{"confirm_size": "1"}` for fetch and
restore; the guard tests are refused before routing).

The confirmation panel (B2:5.4): exact bytes and the human size, free and needed bytes per receiving filesystem, the
destination path, "runs until done or cancelled", a hidden `confirm_size` equal to the computed bytes (and `replace=1`
when replacing), a *Confirm* button and a *Cancel* link back to the page; all in the page, no dialog. Refusals render
as `<p class="state-failed" role="alert">` with the fixed sentence; the job log is named by its project-relative
path, never shown. The L2 banner and the manuscript sentence are exactly B2:5.7.2 and 5.7.4. Every value is
autoescaped; the CSRF token is only in POST bodies.

### 5.10 Data written (B2:5.14, unchanged)

`workspace/_archives/<slug>.zip` (download), `<state_dir>/archives/<slug>.json` (record and last outcome, written only
by `dvc_sync.py fetch|restore` under the operation lock), `<slug>.job` (service, under the start lock), the lock
files `<slug>.lock`, `<slug>.start.lock` (and the sibling `.lock` files `store.write_text` may leave for `.job`),
`<state_dir>/jobs/<id>.{json,log,err}`; `workspace/<slug>` only by Restore. No GET writes anything, including no
directory creation.

### 5.11 Security and data properties (each has a test; Ticket 9's reviews check each)

1. GETs (dashboard, archive page, entries, member, the three GET APIs, `workspace archives`/`archive show|ls|cat`):
   `subprocess.Popen` and `socket.socket.connect` patched to raise; the state directory and `workspace/` trees (names,
   bytes, mtimes) identical before and after; `jobs.reconcile`, `jobs.find`, `jobs.list_jobs` and `store.try_locked`
   patched to raise.
2. Credentials: the web process never opens `.env`, `.dvc/config`, `.dvc/config.local` or anything under `~/.aws`
   (an `io.open`/`os.open` spy over the GET and POST paths); `.dvc/config*` are only `stat`ed; dvc output never
   rendered (a fake dvc printing a marker string; the marker never appears in any page or API body).
3. Members: exact central-directory names only; `../x`, absolute and drive names never served; no extraction on
   browse; links never followed; text autoescaped (`<script>` in a member renders as text); SVG and HTML members are
   attachments with `nosniff`.
4. Mutations: POST + session + CSRF (`test_mutations.py`), in `MUTATING_PATHS`/`SAMPLES`, refused in a dispatch,
   consent bound to exact bytes (`confirm-mismatch`), disk rule, non-blocking locks (two concurrent POSTs start one
   job; a held start lock answers `locked` in under one second).
5. Bounds: every B2:5.13 bound has a boundary test (cap, cap + 1).
6. Data protection: archive content reaches no model, remote or agent; no dispatch/conversation/scan/release control
   on archive pages; the MCP server lists no archive tool and its runs tool omits archived-only slugs (T9-D10); an
   `L2` archive shows the banner; downloads are not refused by level.
7. UI rule: no `alert(`, `confirm(`, `prompt(` or `<dialog` in the new templates; every new control uses an existing
   sibling's element and classes (the reviewers check visually from the template diff).

### 5.12 Docs (exact sentences, pinned by `tests/test_b2_docs.py`)

- **W1** `docs/web.md`, new section "### Archived runs" under "## Pages": "Archived runs are listed from the pointer
  files git already carries; opening a page never contacts the DVC remote or starts `dvc`."
- **W2** same section: "Download fetches the one zip and checks its size and checksum; it never unpacks it. Restore
  is a separate button and unpacks only into an absent or empty `workspace/<slug>`."
- **W3** same section: "At 1 GiB (1,073,741,824 bytes) or more the page asks you to confirm the exact size first; from
  the terminal the same needs `--yes`."
- **W4** same section: the B2:5.7.4 manuscript sentence, verbatim.
- **W5** `docs/web.md` "What stays CLI-only": the **DVC uploads** bullet stays; a new bullet: "**Pulls of old
  per-file DVC pointers** (`workspace/<slug>.dvc`) — one such pull can be tens of thousands of objects; use `uv run
  scripts/dvc_sync.py pull <slug>`."; the API table gains the six archive rows.
- **D1** `docs/DVC_STORAGE.md`, new "### 3.6 Browse and download from the web app": "Downloads from the web app or
  `scieflow workspace archive fetch` use this checkout's own DVC configuration and credentials, as `dvc_sync.py pull`
  does; ScieFlow never reads the credentials itself."
- **D2** same section: "Each filesystem that receives a download must keep 1 GiB free afterwards, with 5% headroom;
  otherwise the download is refused."
- **C1** `docs/cli.md` `## scieflow workspace`: the line "Read-only views over `workspace/`." becomes "Read-only views
  over `workspace/`, plus downloading and restoring archived runs (`archive fetch|restore`)." and the seven commands of
  5.8 are documented.
- **S1** `skills/workspace-sync/SKILL.md`, after the upload table: "A download of 1 GiB or more (`scieflow workspace
  archive fetch|restore … --yes`) is the user's decision: ask before passing `--yes`; inside a dispatch the commands
  refuse."
- **A1** root `AGENTS.md` rule 15: the protected edit of section 3 (H-T9-1), byte for byte.
The docs test also checks that no page claims a browser upload, a browser delete or a remote listing, and that
`mkdocs build --strict` stays clean (branch step).

### 5.13 Ticket 9 definition of done → tests

| Ticket 9 item | Test |
|---|---|
| Behaviour matches ticket 08's approved spec, point by point | t1-t6 tests; the final review maps every B2 section to a test node (table in `reviews/t-9-final-review-r<N>.md`) |
| A download that crosses the consent threshold asks before starting | `tests/web/test_b2_actions.py::test_fetch_at_threshold_renders_confirmation_and_starts_nothing` (pointer `size = CONSENT_BYTES`; no job record, no `<slug>.job`), `::test_fetch_below_threshold_starts_on_one_click` (`CONSENT_BYTES - 1`), `tests/core/test_b2_cli.py::test_fetch_at_threshold_needs_yes` |
| Every failure named in ticket 08 reaches the page as a stated reason, not a traceback: missing remote, missing credentials, interrupted pull, `.partial` present, older schema | `tests/web/test_b2_failures.py`: one test each with the fake dvc (`no-remote`, `no-credentials`, `credentials-refused`, `remote-unreachable`, `missing-on-remote`, `disk-full`), a killed job (`interrupted`, both on GET without a write and after the next POST as `lost`), a `<slug>.zip.partial` leftover, a DVC 2 pointer, an `unreadable` pointer, an archive without `status.yml`/`budget.yml`/`data.yml`, an old archive with `provenance.git/`; each asserts the fixed sentence in the page and `"Traceback" not in body` |
| Manuscript history reads as regenerating after pull and sync, never as an empty history | `tests/web/test_b2_archive_page.py::test_manuscript_sentence_and_no_history_panel` (the exact B2:5.7.4 sentence; no history list/diff form); H-T9-2 |
| Handlers are `def`; mutating routes in `MUTATING_PATHS` and `SAMPLES` with CSRF | existing `test_async_routes.py`, `test_read_only.py`, `test_mutations.py` (E1) green |
| No unbounded read: listing paginated or capped, the cap stated when hit | `tests/core/test_b2_pointers_states.py::test_list_cap_and_note` (`LIST_CAP + 1` pointers), `tests/web/test_b2_archive_browse.py::test_entries_paginate_and_say_so` (`PAGE_ENTRIES + 1`), `tests/core/test_b2_zip_index.py::test_too_many_entries_refused_before_parse` |
| Full suite green | branch step: full default suite and sealed probes 0 failed / 0 errors |

### 5.14 What T-9 does not do

No streaming from the remote, no remote listing (H-T8-1), no deletion of downloads or leftovers (H-T8-2), no browser
upload or push, no browser pull of legacy per-file pointers, no replace of a local run from the browser (no `force`),
no progress percentage, no MCP tool, no change to `/workspace` (T-12) or to `workspace list`, no change to
`jobs.py`/`records.py` (pid reuse stays T-8's R9), no DVC configuration reading.

## Part B — Implementation plan

## 6. Steps

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`); Sonnet 5.5
@ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a checkpointed
pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only; the real `dvc` is never
run, only the test's fake), no push/PR, no credentials or token files (tests write fake `.env`/`.dvc/config` files
only to prove they are never read), no patient data (synthetic archives only), never edits a never-edited file of
section 3, runs the INFLIGHT guard and the scope guard and reports both exit codes. Tests are hermetic: temporary
projects only, `SCIEFLOW_STATE_DIR` isolated (`tests/conftest.py:10-11`), `PATH` prefixed with the fake dvc's directory
only by tests that need it, clocks injected, no read of the real home or the RUN layout.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/t-9-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-t-9-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/t-9/junit G6_CACHE_DIR=~/.cache/scieflow-t-9 RUN/tools/g6_pytest.sh WT <head-sha> t-9-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing rules
  (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with completion
  notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> t-9-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **mkdocs:** `cd WT && /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/mkdocs build --strict -d RUN/scratch/t-9/site`
  (memory-capped; "not available" if mkdocs is not in the venv).
- **INFLIGHT guard** and **scope guard:** section 3.
- **Load-flake nodes:** the four named in the installer's standing rules, one rerun, both results recorded. No
  rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/t-9/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused sets** (clean-tree runner on the ticket's final commit):
  t1 `tests/core/test_b2_store_try_locked.py tests/core/test_b2_constants.py tests/core/test_b2_pointers_states.py tests/core/test_b2_job_liveness.py tests/core/test_b2_classify_disk.py tests/core/test_store.py tests/core/test_jobs.py tests/core/test_control_read.py tests/test_docs_b2_spec.py tests/test_b2_premises.py`;
  t2 `tests/core/test_b2_zip_index.py tests/core/test_b2_members.py tests/core/test_b2_summary.py tests/core/test_b2_levels_parse.py tests/core/data/test_levels.py tests/core/test_workspace.py tests/test_archive.py`;
  t3 `tests/test_b2_dvc_fetch_restore.py tests/test_b2_dvc_locks.py tests/test_dvc_sync.py tests/test_archive.py tests/test_noexport.py`;
  t4 `tests/core/test_b2_service.py tests/core/test_b2_service_jobs.py tests/core/test_b2_cli.py tests/core/test_b2_mcp_hidden.py tests/core/test_cli_parity.py tests/core/test_service.py tests/mcp_server`;
  t5 `tests/web`;
  t6 every `tests/test_docs*.py`, `tests/test_b2_docs.py`, `tests/test_docs_role.py`, plus mkdocs.

### t0 — worktree, plan and spec copy, green base, re-verification (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the ancestor check, the three publish receipts and the B2-spec
   and T-12 seam checks; otherwise `BASE_RED` with the reason. `worktree add -b dev/t-9 RUN/scratch/t-9-wt <base>`
   (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed probes 0
   failed (directory run and the explicit sealed file). No expected counts; t0 records what it measures. Only the
   load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths.
3. Write `RUN/scratch/t-9/allowed-paths.txt` (section 3).
4. Re-verify on `base` every `path:line` cite of section 4 **and** Part A: build the list from the committed plan copy
   with a grep over both blocks (section 4 up to `## Part A — Spec`, and Part A up to `## Part B — Implementation
   plan`), resolve a bare `:N` cite to the file named last before it, de-duplicate, record the list used. Write
   `reviews/t-9-t0-baseline.md` (per cite: still present / moved (new line) / changed) with inventories: (a) the merges
   between `M` and `base` and which section-4 files they changed; (b) the B2 spec on base: its appendix bullets
   (line corrections) and whether `tests/test_docs_b2_spec.py` and `tests/test_b2_premises.py` pass on base (run
   once); (c) `core/control_read.py` on base: its public names and signatures, and whether a public bounded byte reader
   exists (decides whether t1 adds `read_bytes`); `workspace.describe_from`'s exact signature; (d)
   `tests/web/mutating_paths.py`, `tests/web/async_allowlist.py`, `tests/core/cli_parity.yml` on base (names only); (e)
   `dashboard.html`, `run.html` action forms and `files.html` on base (the sibling elements and classes the UI rule
   binds; T-12 may have added some); (f) `git --version`, and `dvc` on PATH (recorded, not required); (g) the root
   `AGENTS.md` old text of the protected edit occurs exactly once on base.
5. Copy `reviews/t-9-plan.md` byte for byte to `docs/superpowers/plans/2026-11-04-t-9-archived-run-browsing.md`, and
   its Part A (from the line `## Part A — Spec` up to, not including, the line `## Part B — Implementation plan`) to
   `docs/superpowers/specs/2026-11-04-t-9-archived-run-browsing-implementation.md` with the first line
   `# T-9 archived-run browsing — implementation design (Part A of docs/superpowers/plans/2026-11-04-t-9-archived-run-browsing.md; the binding design is docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md)`
   and one blank line. Commit both alone ("docs(plans): Ticket 9 archived-run browsing plan and implementation design
   (T-9 t0)").

Verdicts `DONE` | `BASE_RED`. Result keys: `verdict, summary, head` (the docs commit; on BASE_RED the base), `base,
baseline {default, sealed}, inventories {a..g}, inflight_guard_exit, scope_guard_exit`.

### t1 — core read model: locks, constants, pointers, states, liveness, classifier, disk (Haiku) → t1r (Sonnet)

Files: section 3 rows t1. RED → GREEN:
1. `store.try_locked`: free → enters and releases; held by another process (a helper subprocess holding the
   `FileLock`) → `Busy` within 0.5 s, never waiting `LOCK_TIMEOUT` (monotonic clock assertion); `store.locked`
   unchanged (`tests/core/test_store.py` green).
2. Constants: the 16 values equal the committed B2 spec table (parsed) and `INLINE_MAX_BYTES == MAX_INLINE`.
3. Pointers: every B2:5.5 kind and every `unreadable` reason (YAML error, not a mapping, zero/two `outs`, bad md5,
   path mismatch, `.dir` md5 in `_archives`, symlink, `POINTER_MAX_BYTES + 1`, negative/non-int size); DVC 2 pointer
   (no `hash`) reads as `zip` with `dvc2: true`; legacy `.dir` pointer with and without a local run directory; invalid
   file names counted in `skipped_names`.
4. Discovery: sorted; `LIST_CAP + 1` pointers → `shown == 500`, `total == 501`, the note "1 more not shown (cap 500)";
   only the first 500 pointers are opened (an open spy); no subprocess, no socket, no write.
5. States: each row of the B2:5.5 table built from files (no zip, short zip, full-size zip without record, record for
   the current stat and md5, record for an older md5, MD5-mismatch record, a running job of each kind incl.
   `--verify-only`); a stat change invalidates `ready`; the symlink rule (target inside `.dvc/cache/` accepted, outside
   → `incomplete` with the note, never opened); relations `archived-only`, `also-local`, `alias`; leftovers listed by
   stat only.
6. Liveness (B2:5.6.1): bad `<slug>.job` content → `job-pointer-unreadable`; missing/unparsable/wrong-kind/wrong-label
   record → no job; running + alive pid → running; running + dead pid (a reaped child's pid) → not running with the
   pid-matched outcome or `interrupted`, **and nothing written** (tree compare); each `FINAL` state's outcome; failed
   with exit `LOCKED_EXIT` → `locked` (T9-D5); `jobs.find`/`list_jobs`/`reconcile` patched to raise, never called;
   oversize records (`JOB_RECORD_MAX_BYTES + 1`) → no job. `reconcile_archive_job`: a dead running record becomes
   `lost` with `finished` set (record passes `records.check_job`), an alive or final one is untouched, a dead pid is
   never signalled.
7. `classify`: one fixture line per B2:5.9 row (each match string), order (a line matching two rows gets the first),
   case-insensitivity, only the last `DVC_OUTPUT_TAIL` bytes considered (a match before the tail is ignored), exit 127,
   `None`; unmatched → `dvc-failed`; `sentence()` fills the placeholders.
8. Disk: one filesystem receiving twice the size (cache + `_archives`), two filesystems (monkeypatched `st_dev`),
   exact boundary (free == needed → ok, needed − 1 → refused), the reserve and headroom values.
9. If t0 found no public bounded byte reader: `control_read.read_bytes` tests mirroring T-12's `read_yaml` tests
   (missing, symlink, FIFO non-blocking, directory, cap, cap + 1, changed).

t1r checklist: B2:5.5 and 5.6.1 implemented exactly (liveness writes nothing; only `reconcile_archive_job` writes
`lost`); no `jobs.find`/`list_jobs`/`reconcile` in `archives.py` (grep); every read bounded and no-follow; constants
equal the spec; `try_locked` never waits; existing store/jobs/control_read tests green unchanged; scope and INFLIGHT
guards exit 0.

### t2 — zip reading: pre-check, index, entries, members, summary, data level (Haiku) → t2r (Sonnet)

Files: section 3 rows t2. RED → GREEN (archives built in tests with `sflib.archive.build_zip` and raw `zipfile`):
1. Pre-check: a normal zip, a Zip64 zip (forced with `force_zip64`), a zip whose EOCD claims `MAX_ARCHIVE_ENTRIES + 1`
   entries or a central directory of `MAX_CENTRAL_DIRECTORY_BYTES + 1` (crafted bytes) → `too-many-entries` with
   `zipfile.ZipFile` never constructed (spy); garbage → `unreadable-zip`; at most 65,557 tail bytes read (a read
   spy) plus the Zip64 record.
2. Index and LRU: one `ZipFile` per stat key across repeated calls; a changed file (new mtime) re-parses; at most
   `INDEX_CACHE` kept (the fifth evicts the oldest); thread-safe under 8 concurrent threads.
3. Entries: implied parents, explicit `dir/` entries, directories first then files by name, `.lock` hidden, links
   shown with target text and not followed, unsafe names (`../x`, `/abs`, `C:x`, `a/../../x`) counted and never
   listed, encrypted and unsupported-compression members listed as such; `PAGE_ENTRIES + 1` entries → two pages and
   the "entries 501-501 of 501" note; page out of range → empty with a note.
4. Members: exact-name lookup only (`./status.yml`, `status.yml/`, a traversal string → `no-such-member`); a
   directory, link, unsafe, encrypted or unsupported entry → refused; a stored member altered on disk →
   `corrupt-member` (P7); a deflated member is read only up to its declared size; reads are `CHUNK_BYTES` blocks.
5. Summary: complete archive (loop run) gives the same kind/state/phase/iteration/lineage as `workspace.describe` on
   the extracted tree (except `updated`/`updated_at`, which come from `ZipInfo.date_time` and are labelled); missing
   `status.yml` → kind `none` "no status.yml"; each control member missing / `CONTROL_MAX_BYTES + 1` / unparsable →
   its exact word; budget malformed → "unreadable"; data level: absent → "undeclared", `L2` → banner, invalid → `L2`
   with reason, oversized → `L2` (T9-D6).
6. `levels.parse_text`/`level_of`: equal results to `read`/`level_info` on every fixture of
   `tests/core/data/test_levels.py` written to a temp file (parametrised over the same inputs); that file green
   unchanged.

t2r checklist: no read before the bound; no extraction; exact-name member access; LRU memory-only and locked; no
`|safe` relevance yet; `describe_from` reused (no copy of `describe`'s logic); `levels` behaviour unchanged; guards.

### t3 — `dvc_sync.py` fetch and restore, locks (Haiku) → t3r (Sonnet)

Files: section 3 rows t3. `tests/fixtures/b2_fake_dvc.py` writes an executable `dvc` into a temp dir: it reads a JSON
script from an env var (the test's own tmp path) saying which bytes to write to the pointer's zip path, which line to
print on stdout/stderr, how long to sleep and which exit code to return; it records its argv. RED → GREEN:
1. `fetch` happy path: zip written, size and MD5 checked, `link_to_cache` attempted (and succeeding with a fake cache
   object), record written with the final stat and the subprocess's pid; exit 0; nothing extracted.
2. Each failure: dvc exit with each fixture line → the record's `reason` equals `classify`'s code; size mismatch →
   `verify-size`; MD5 mismatch → `verify-md5`; no `dvc` on PATH → `dvc-missing`; the job log keeps dvc's full output
   while the record keeps only the code.
3. `--verify-only` never invokes dvc (argv log empty) and verifies; `--replace` passes `--force` to dvc.
4. A failed re-download over an untouched `ready` copy keeps the verification (still `ready`); over a changed file it
   does not.
5. Locks: with `<state>/archives/<slug>.lock` held by a helper process, `fetch`, `restore`, `push` and `pull` of that
   slug print the `locked` sentence, exit/return `LOCKED_EXIT` for it, and write nothing; other slugs in the same
   `push`/`pull` proceed; directory-mode slugs take no lock; `--state-dir` overrides; without it
   `Project(root).state_dir` is used.
6. `restore`: a `ready` copy restores into an absent and into an empty `workspace/<slug>`; non-empty → `run-exists`,
   untouched; a record/stat mismatch → `not-ready`; a CRC-broken zip → failed, no `workspace/<slug>` and no staging
   directory left (P6).
7. The existing `tests/test_dvc_sync.py` and `tests/test_archive.py` are green unchanged; the parser accepts the new
   options and still refuses `--all` for push/pull.

t3r checklist: no extraction or deletion in `fetch`; record written only under the operation lock and only by these
subcommands; tail bounded; classifier imported from core (one definition); `.env` handling unchanged; no credential
file read by ScieFlow code (only dvc reads them); guards.

### t4 — service and CLI (Haiku) → t4r (Sonnet)

Files: section 3 rows t4. RED → GREEN:
1. `archived_runs`, `archive_detail`, `archive_entries`, `archive_member` shapes on fixtures; GETs never write, lock,
   start a process or connect (5.11 item 1, spies and tree compare); every degraded input returns a reason, none
   raises except the unknown slug (404 `ServiceError`).
2. Refusal order of 5.7 rules 1-5, one test per code: `in-dispatch` (both `SCIEFLOW_DISPATCH=1` and the sandbox
   cache-dir heuristic), `locked` (held start lock: answered in under one second; held operation lock: the probe),
   `not-zip-pointer`, `replace-required`, `dvc-missing`, `size-unknown` (web) vs allowed with `yes` (CLI),
   `no-space` with both figures, `confirm-required` at `CONSENT_BYTES` and not at `CONSENT_BYTES - 1`,
   `confirm-mismatch`, replace always needing consent, `not-ready`, `run-exists`; each starts nothing (no job record,
   no `<slug>.job`).
3. Jobs (with the fake dvc): `fetch_archive(wait=True)` → outcome `ready`; `wait=False` returns at once with a
   `job_id` and the daemon thread finishes it; state `ready` → no-op; `unverified` → `--verify-only` argv; two
   concurrent calls (threads) → exactly one job; `cancel_archive_job` kills a sleeping fake dvc's group and the outcome
   becomes `interrupted`; a dead running record is turned `lost` by the next fetch/restore/cancel and never by a GET;
   restore end-to-end makes the run local (then `list_runs_with_budget` lists it and the archive relation is
   `also-local`).
4. CLI: `workspace archives [--json]`, `archive show|ls|cat|fetch|restore|cancel` on fixtures; `fetch` at the
   threshold without `--yes` prints the size and the exact `--yes` command and exits 1 having started nothing; with
   `--yes` it runs; inside a dispatch it refuses; `cat` refuses a binary member to a terminal without `--out`.
5. Parity (E2): seven entries, `new: true`, args as 5.7; `tests/core/test_cli_parity.py` green (the recorder dict exits
   0 for each).
6. MCP (T9-D10): `tools.TOOLS` names no archive service function; with an archived-only fixture slug the runs tool's
   output does not contain it; `tests/mcp_server` green unchanged.

t4r checklist: 5.7 order and every refusal starts nothing; locks non-blocking; liveness read-only on GETs;
`ArchiveRefused` carries only codes, fixed sentences and numbers; `yes` never reachable from the web (no form field or
API parameter maps to it); `in_dispatch` on all three mutations; parity and MCP tests green; guards.

### t5 — web pages, API and the failure matrix (Haiku) → t5r (Sonnet)

Files: section 3 rows t5. RED → GREEN:
1. Dashboard: *Archived runs* section with rows (slug link, size, state, outcome), the cap note at `LIST_CAP + 1`, the
   *archived* badge on an also-local run; `tests/web/test_dashboard_reads.py` green unchanged (no extra read or
   listing inside any run); no archives → the section says "No archived runs in this checkout." in `class="dim"`.
2. Archive page: header, environment lines (dvc found / not installed; DVC remote configuration found / not found,
   advisory), disk lines, actions per state (B2:5.5 table, every state), the leftover lines, the manuscript sentence
   and no history panel, the data level and the L2 banner, the run summary words.
3. Browse: directory navigation, pagination note, a text member inline (autoescaped `<script>`), a PNG inline, an
   SVG/HTML member as attachment with `nosniff` and `Content-Disposition`, `download=1`, `no-such-member` 404, a
   corrupt member → `corrupt-member` sentence; browse before `ready` → `not-ready`.
4. Actions: the consent tests of 5.13 (threshold, below, unknown size → `size-unknown` with the command, replace →
   panel, `confirm-mismatch`), success → 303, refusal → page with the sentence (200), API 409 `{reason, message}`,
   cancel; the CSRF token never in a URL (`test_agents_page.py`'s query check pattern); two quick POSTs start one job.
5. Failure matrix `tests/web/test_b2_failures.py` (5.13 row 3): each named failure's sentence on the page and no
   `Traceback`, dvc's marker text never shown, the log path shown.
6. Read-only proofs on all GET pages and GET APIs (5.11 item 1) and the credential-file spy (item 2).
7. UI rule (`tests/web/test_b2_ui_rule.py`): no `alert(`, `confirm(`, `prompt(`, `<dialog`, `<script` or `|safe` in
   the three new templates and the dashboard diff; the new buttons are plain `<button>` inside `<form method="post"
   class="actions">` like `run.html`; tables are plain `<table>` like `files.html`; error text uses
   `class="state-failed" role="alert"` like `run.html:6`.
8. Inventories: E1 six paths; `test_read_only.py`, `test_mutations.py`, `test_async_routes.py` green unchanged.

t5r checklist: plain `def` handlers; every POST under `MUTATE` and in both inventories; no business rule in pages
or API beyond mapping (the service refuses forged input: `yes` or `force` in a form is ignored); escaping; no CSRF in
queries; GETs write nothing; the UI rule checked visually against the siblings named in t0 inventory (e); guards.

### t6 — docs and the protected edit (Haiku) → t6r (Sonnet)

Before t6 the operator applies the protected root `AGENTS.md` edit (installer step `pe_t6`). First check that root
`AGENTS.md` differs from base only by the two added lines of section 3, byte for byte; if not,
`kanban_block(kind="needs_input")` and stop. Commit that file alone ("docs(agents): rule 15 names archive downloads
of 1 GiB or more as the user's decision (human-approved protected edit) (T-9 t6)"); never edit any `AGENTS.md`
yourself. Then RED: `tests/test_b2_docs.py` pinning W1-W5, D1-D2, C1, S1 and A1 of 5.12 (reading only this
repository's files, as `tests/test_docs_k_harness.py` does) fails; write the docs; GREEN; every `tests/test_docs*.py`
and `tests/test_docs_role.py` green; `mkdocs build --strict` exit 0. Every literal pinned by an existing docs test
stays (T-12's T1-T8, F-2's S1-S8; the t0 baseline lists them).

t6r checklist: every sentence true against the code on the tip (W1 GET paths, W2 `fetch`/`restore`, W3 the constant,
D1 `jobs.start` argv and no credential read, D2 `disk_check`, C1 the command tree); the AGENTS.md change is exactly
the approved text and the only `AGENTS.md` change; guards.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip of `dev/t-9`: full default suite 0 failed / 0 errors (absolute counts and the delta against
t0, explained by the new test files and recorded E-extra entries); sealed probes 0 failed (directory and explicit
file); the new `test_b2_*` files once more under `env -i HOME=<empty dir under ~/.cache/scieflow-t-9-branch>
PATH=/usr/bin:/bin` (hermeticity; the fake dvc is placed by the tests themselves); `tests/test_ci_workflow.py`;
`mkdocs build --strict` into `RUN/scratch/t-9/site`; INFLIGHT and scope guards; linear history (no merge commits, at
least one commit per step t0..t6); `git diff base..tip -- tests/` adds only `test_b2_*`/`b2_*` files and changes no
existing test except E1, E2 and recorded E-extra entries; `git diff base..tip -- pyproject.toml uv.lock` empty; no new
`|safe` under `src/scieflow/web/templates`; `git diff base..tip -- AGENTS.md` equals the approved two lines. Writes
`RUN/scratch/t-9/pr-body.md` (plain English: the nine points of section 1, decisions T9-D1..D16, H-T9-1/2, absolute
counts, the residuals of section 8, "please merge with a merge commit"; no credentials, prompts or patient data) and
`reviews/t-9-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: (1) spec: every section of the B2 design (5.1-5.16) mapped to code and a test node (a table
in the review), Ticket 9's definition of done item by item (5.13), T8-D1..D18 and T9-D1..D16 honoured, B2:5.15 and
5.11 properties each verified by reading the code and running its test; (2) code and security: GET purity, bounded
no-follow reads, exact-name member access, consent bound to exact bytes, non-blocking locks, `lost` written only on
POST/CLI, no credential read, no dvc output rendered, CSRF/session/inventories, plain `def`, autoescaping, no browser
dialog, UI siblings, MCP unchanged; (3) evidence: re-run memory-capped the t1-t6 focused sets and the explicit sealed
file under the lock, confirm the branch result's counts, re-run both guards and mkdocs, `pr-body.md` accurate.
Output `reviews/t-9-final-review-r<N>.md`. PASS → the installer's publish step; CHANGES → final_fix (new commits, the
full default suite and sealed probes re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/t-9-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   core read model                        DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        zip reading                            same shape → t3
t3/t3r/t3f        dvc_sync fetch/restore, locks          same shape → t4
t4/t4r/t4f        service and CLI                        same shape → t5
t5/t5r/t5f        web pages, API, failure matrix         same shape → t6
t6/t6r/t6f        docs + protected AGENTS.md edit        same shape → branch   (pe_t6 inserted by the installer)
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install; pe_t6 from t-9-protected-edits.json)
```

Caps `{max_cards: 75, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/t-9-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit` and `scope_guard_exit`. Inputs:
handoff, decisions, gap, `pilot` (`handoffs/pilot-roadmap-20261024/pilot-roadmap-20261011.md`), `t8plan`
(`reviews/t-8-plan.md`, the approved design), `t8approval` (`approvals/t-8-plan-approved-human-20261104.md`),
`t12plan` (`reviews/t-12-plan.md`), `t12approval` (`approvals/t-12-plan-approved-human-20261103.md`, the UI rule);
the installer adds `plan`. The ticket 08/09 files live outside RUN; the briefs name them by absolute path and sha256.

## 8. Risks and residuals (stated, not hidden)

- **R1 DVC behaviour unverified here.** `dvc` is not installed; cache layout, `--force` semantics and error wording
  come from documentation and the existing code. The classifier is pinned by fixtures; real wording that differs
  falls to `dvc-failed` with the log path, never to a wrong specific reason (T-8 R1).
- **R2 Planned against approved plans, not merged code.** T-8's spec, T-12's `control_read`/`describe_from` and the UI
  siblings are taken from their approved plans; t0 records their real shape and the tickets follow the baseline file.
  A seam that differs in substance goes back to the human through the reviewer, never silently reshaped.
- **R3 The browser starts a network transfer with your credentials** (T8-D5, approved); bounded by session, CSRF,
  exact-byte consent, the disk rule, the locks and the no-dispatch rule; the loopback DNS-rebinding residual stays
  (T-12 H-T12-3).
- **R4 Pid reuse** (T-8 R9): liveness is `jobs._alive` (pid only), as for every ScieFlow job.
- **R5 Disk rule is pessimistic and cache-location blind.** Twice the size on one filesystem; a custom DVC
  `cache.dir` is not read, so the cache group may be the wrong filesystem (the job still fails `disk-full` cleanly).
- **R6 MD5 verify reads the whole file once**, as a cancellable job (T-8 R3).
- **R7 Name enumeration of `_archives/` is complete** (T9-D9): thousands of pointers cost one streamed listing per
  dashboard view; per-pointer reads stay capped.
- **R8 Downloaded copies accumulate** (H-T8-2); restore leaves the zip (hard-linked when possible).
- **R9 No real archive on main**: everything is tested on synthetic archives and a fake dvc; the first real download
  is the human's.

## 9. After T-9

T-9 is the last entry of the milestone queue. Next in the agreed sequence (pilot roadmap section 4): Tickets 13-15,
design-only browser modules; the final handoff (relocation to `/home/ubuntu/Github/ScieFlow`) stays the human's
decision. Open items carried forward: H-T8-1 (remote listing), H-T8-2 (browser delete), R5 (DVC `cache.dir`).
