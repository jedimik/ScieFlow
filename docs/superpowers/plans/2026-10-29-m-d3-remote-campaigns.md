# M-D3 plan: a remote campaign is a campaign (round 1)

Pipeline `m-d3-plan`, step plan, round 1, epoch 1. Card `t_32dc928d`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261029_192654_d3c21f`). Written
2026-10-29. Previous review: none (round 1). Size M: one plan (spec in Part A, implementation plan in
Part B); t0 copies it to `docs/superpowers/plans/2026-10-29-m-d3-remote-campaigns.md`. No separate spec file
(that is for L milestones).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no network call
except the read-only `git ls-remote` that fixes the reference main.

## 0. Inputs and references used

Immutable inputs (sha256 re-computed on this card; all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…47da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |
| `continuity/milestones.yml` (not pinned) | de3bf17f…2f8b (m-d3 entry line 23: `after: [m-d2]`) |

Scope source: GAP-ANALYSIS.md section 8 row **M-D3** ("`Campaign.backend`, `kind: remote` jobs, fetched
run dirs with `manifest.json`, same compare/report/budget, post-fetch hash manifest, `watch` loop";
acceptance "A remote (local-adapter) campaign yields `results.json` identical in shape to a local one;
budget counts N for an array job"; depends on M-E, M-D2; conflicts `core/jobs.py`), audit finding **A-3**
(`backend: remote` was silently ignored; M-A made it a refusal, "real `backend` + `kind: remote` jobs in
M-D3", GAP-ANALYSIS.md:109), section 7 (scheduler seam), **D19** (Slurm and plain SSH experimental,
contract-tested on fixtures, no PSI/J) and **D21** ("One replicate = one experiment run; a remote array job
of N tasks = N runs"). D9–D11 and D23 (data protection: an L2 run may use a remote only when the user lists
it in `l2_remotes`) apply to every push. Where the GAP text and DECISIONS differ, DECISIONS win.

Prerequisite plans and their merges (both merged into main; read for the API this milestone binds to):

| Milestone | Pushed head | PR / main after merge | Used for |
|---|---|---|---|
| M-E scheduler seam | `9eeb4fad8c01d7c008acd155e81dac3976d62aa7` | #18 / `526b3f2` | adapters, ledger, `remote.py` command functions, M-E-D2 (no cancel) |
| M-D2 analysis | `5f051d04989a80b340e0f1542f543c0ffa68a760` | #22 / `6880751` | manifests, `verify`, results shape, budget pre-check, D21 doc line |

Code read read-only from git objects (`git archive 6880751 … | tar -x` into the card's scratch directory,
`git show`, `git grep`); no checkout was touched. `M` = remote main =
`688075183c285a5bca43bec490b7d85d2556e9f0` (`git ls-remote git@github.com:jedimik/ScieFlow.git
refs/heads/main`, 2026-10-29; the M-D2 merge commit, PR #22, `RUN/scratch/m-d2/publish.json`
`main_after_merge`). Every `path:line` below is on `M`. `git merge-base --is-ancestor` confirms that
H-4 `6152d73`, M-A `be99db2`, M-E `9eeb4fa`, M-B `05d4d10`, M-C1 `aa7c379`, M-D1 `c350593` and M-D2
`5f051d0` are ancestors of `M`. **The milestone can start: both prerequisites are merged.** (Verdict DONE.)

Lane note: `continuity/milestones.yml` has `parallel: pairs` with pairs `[m-e, m-b]`, `[m-c1, m-d1]` only,
so M-D3 runs alone. `m-f-run` is at t0 now and `m-c2-run` waits for a lane, both ahead of M-D3 in the
queue, so the real base will probably contain M-F and M-C2. Their file lists (`reviews/m-f-plan.md`
section 3, `reviews/m-c2-plan.md` section 3) do not overlap M-D3's, with three shared files edited in
different regions: root `AGENTS.md` (M-F edits the skills table, `AGENTS.md:241`; M-D3 edits rule 11,
`:97-99`), `mkdocs.yml` (M-F/M-C2 add Research nav lines; M-D3 adds one Experiments nav line) and nothing
else. t0 records which of them the base contains.

## 1. For the human: what M-D3 adds, in plain words

Today a campaign marked `backend: remote` is refused (M-A); a remote experiment has to be driven by hand
through `remote.py` (pull, submit, poll, fetch), and its results never become a normal campaign: no
`results.json`, no manifests, no `stats`, no `verify`, and the budget is recorded by hand. M-D3 makes a
remote campaign an ordinary campaign that simply runs somewhere else.

1. **The campaign says where it runs.** A campaign can say
   `backend: remote`, name a remote from your `config/remotes.yml` and a folder on it, and ask for
   resources (walltime, CPUs, memory, GPUs, queue, scratch). Everything else is the same campaign:
   parameters, scenarios, replicates, analysis.
2. **`scieflow experiment sweep` submits it.** Before anything is copied or submitted, ScieFlow checks
   everything it can check locally: the remote exists and is allowed, the folder is inside your allowed
   folders, every needed operation is allowed, the resources fit your limits (a request above a limit is
   **refused**, not silently cut down), the budget has room for all runs, the study design allows the
   campaign, and the data protection rules allow the data to leave (an L2 run only to a remote you listed
   in `l2_remotes`). It then checks your Kerberos ticket, packs the stage code, the container image and
   the input into one folder, copies it to the remote, and submits the jobs. It does not wait.
3. **Two ways to submit.** Default: one scheduler job per run, so runs go in parallel (up to your
   `max_concurrent_jobs`; the rest are submitted as slots free up). Or `jobs: array`: one scheduler job
   that runs all N runs one after another — useful for many short runs. Either way **N runs are N runs of
   the budget** (your D21), recorded once, when the scheduler accepts the campaign's first job.
4. **`scieflow experiment watch` collects it.** It asks the scheduler about each job, submits waiting runs
   when there is room, and copies each finished run back. Each run on the remote writes a checksum list
   of its outputs and logs; after copying, ScieFlow checks every file against it (the "post-fetch hash
   manifest"). A run whose files do not match, or that left a link instead of a file, is recorded as
   **failed** with the reason, and no metrics are computed from it. Metrics, `manifest.json`,
   `results.json` and `results.csv` are then written exactly as for a local campaign, so `compare`,
   `report`, `stats`, `tables`, `figures`, `verify` and the budget work unchanged. `watch --once` does one
   pass; without it, `watch` polls until the campaign is complete or a time limit is reached.
5. **The campaign shows up as a job.** Inside a run, a remote campaign is one entry of kind `remote` in
   the run's job list (run page, timeline). It cannot be cancelled from ScieFlow (as decided in M-E:
   there is no cancel command yet); pressing Cancel says so.

Nothing in `scripts/remote/` changes: M-D3 calls the same `remote.py` command functions (same policy
checks, same ledger `jobs.yml`, same attempt and concurrency ceilings, same Kerberos handling) from inside
`scieflow experiment`. The certified PBS command strings are untouched.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-D3-D1 Same gate, no new remote code.** `scieflow experiment sweep|watch` call `remote.py`'s own
  command functions in-process (`cmd_check`, `cmd_mkdir`, `cmd_push`, `cmd_submit`, `cmd_status`,
  `cmd_fetch`, `scripts/remote/remote.py:50-73,297-308,744-764,476-568,571-606,702-719`) with the
  remote loaded by `policy.load_remote` (`scripts/remote/policy.py:61-101`); results are read from their
  exit codes and from the ledger, never by parsing free text. `scripts/remote/*` is not edited. Root
  `AGENTS.md` rule 11 names the two commands (one protected edit, section 3).
- **M-D3-D2 "Array" = one job that runs the N runs in order.** `jobs: array` submits one ordinary
  scheduler job whose script runs every run one after another; it works on every adapter, including the
  `local` one the tests use. Native scheduler arrays (`qsub -J`, `sbatch --array`) are **not** used: they
  need array-aware job ids (`12345[].server` fails today's token rule, `policy.py:40,130-134`) and status
  parsing that cannot be verified without a live cluster, and they would change the certified PBS adapter
  (`scripts/remote/schedulers/pbs.py:57-67`). The adapters' `array_support` flags stay as recorded by M-E.
  Native arrays are follow-up F1. (Alternative: implement native arrays now as experimental; say so.)
- **M-D3-D3 Budget = N, recorded once, at the first accepted job.** The local pre-checks run first
  (exhausted budget and `N > remaining` both refuse before anything, `src/scieflow/experiments/cli.py:87-93,
  151-173`). When the scheduler accepts the campaign's first job (per-run) or its one job (array), N runs
  are recorded with one `budget.recorded` event (D21). Runs that are later not submitted (a submit error)
  stay counted: the campaign was accepted as N runs, exactly as a local sweep records N even when runs fail.
- **M-D3-D4 Code reaches the remote as a hashed bundle, not through git.** The stage folder, the
  container image (`<stage>/env/<stage>.sif`) and the input are copied into a bundle under the run's
  `scratch/remote/` and pushed with `remote.py push --checksum` semantics; the bundle's tree hash and the
  manifest's script/stage/sif hashes identify exactly what ran. Rule 11's "fixes reach the remote via git"
  keeps applying to repositories; a campaign bundle is tool-written, never edited on the remote.
  (Alternative: require a remote git checkout pinned at the local commit; it cannot carry the `.sif`.)
- **M-D3-D5 Everything is computed locally.** The remote needs only `bash`, coreutils (`sha256sum`,
  `date`, `uname`), findutils and, for container runs, `apptainer` on the compute node. Metrics, manifests
  and results are computed on this machine after the fetch, from the fetched files and the context captured
  at submit time (`src/scieflow/experiments/manifest.py:251-277`).
- **M-D3-D6 No silent clamping.** `remote.py submit` clamps over-limit resources with a `CLAMPED:` line
  (`policy.py:237-292`, `remote.py:501-511`). A campaign is refused instead when any resource would be
  clamped, naming the resource and the limit.
- **M-D3-D7 No cancel, no resubmit, no remote delete.** M-E-D2 stands (no cancel command). A failed run is
  a finding; M-D3 never resubmits it (rerun-until-green is forbidden, root `AGENTS.md` rule 5) and never
  deletes remote files. A stuck job is reported; `remote.py` stays the tool for the human-supervised
  fix-and-resubmit loop (`skills/remote-exec/SKILL.md:91-99`).
- **M-D3-D8 A remote campaign lives inside a run, in a fresh folder.** `--experiments-dir` must lie inside
  `workspace/<slug>/` (the ledger, budget, data levels and core job all live there; `remote.py push|fetch`
  already require it, `remote.py:709-711,750-754`). A campaign folder that already exists is refused (a
  local sweep may overwrite its own folder; a remote one must not mix with it). Each submission gets its
  own remote folder `<dir>/<campaign>-<UTC stamp>`, so nothing on the remote is ever overwritten.
- **M-D3-D9 Post-fetch hash manifest.** Each run's script writes `remote/SHA256SUMS` (every file under
  `artifacts/` and `logs/`) before its exit marker. After the fetch every listed file must exist and match,
  every fetched file must be listed, and no symlink may appear; otherwise the run is `failed` with reason
  `transfer-hash`, `transfer-missing`, `transfer-extra` or `fetched-symlink`, and no metrics are computed.
  It is an integrity check of the transfer, not a security proof against a hostile remote.
- **M-D3-D10 One core job of kind `remote` per campaign.** `src/scieflow/core/jobs.py` gains two small
  functions that record and update a job that has no local process (pid `null`), so the campaign appears
  in the run's job list and timeline with the existing `job.*` events. `jobs.cancel` refuses such a job
  and `service.cancel_job` turns that into the normal `ServiceError` message (the web page and
  `scieflow run cancel` already show that error, `src/scieflow/web/pages.py:790-801`,
  `src/scieflow/core/run/converse_cli.py:289-297`). `jobs.reconcile`
  never marks it lost (it skips jobs without a pid, `src/scieflow/core/jobs.py:465-474`).
- **M-D3-D11 No new event types.** M-C2 edits the closed list (`src/scieflow/core/events.py:20-42`). M-D3
  uses the existing `job.queued/started/finished/failed`, `job.refused` (reason `remote` or the existing
  `budget`/`design`) and the free-form `note.experiment_remote` (counts, states, run ids and job states —
  never metric values or file contents).
- **M-D3-D12 Manifests stay free of hosts, users and absolute paths.** A remote run's manifest has every
  M-D2 key (`manifest.py:35-39`) plus a `remote` block: remote name, scheduler, submission shape, task
  index and the transfer check. The remote folder (which contains your user name) and the scheduler job id
  (which contains the server name) are kept only in the campaign's `remote.json` and in `jobs.yml`, as
  `jobs.yml` already does (`scripts/remote/jobs.py:62-75`).
- **M-D3-D13 Watch exit codes.** `0` complete (results written), `4` still in progress ("wait", the same
  number `remote.py` uses for a full queue, `remote.py:538-548`), `1` refused or failed (`POLICY:` and
  `NO_TICKET:` messages keep their `remote.py` wording; on `NO_TICKET` you are asked to `kinit`).
- **M-D3-D14 Out of scope (follow-ups, section 9):** native scheduler arrays; cancel; automatic resubmit
  of failed runs; Snakemake stages inside campaigns (the dry-run gate stays a `remote.py` workflow);
  capturing the package list on the remote (the local capture of the same `.sif` is used, or "not
  captured"); a `singularity` alias for sites without `apptainer`; key-based SSH auth (M-E-D3); remote
  clean-up; a web panel beyond the job row.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head
`dev/m-d3` → base `main`, one PR, merged with a merge commit. Title "M-D3 Remote campaigns: sweep submits,
watch collects, same results". The body (written by the branch step) lists the five capabilities,
decisions M-D3-D1..D14, absolute test counts and the residuals.

## 2. Base, dependencies and worktree

- **Dependencies** (`continuity/milestones.yml:23` `after: [m-d2]`; GAP row also names M-E): both merged
  (section 0). t0 re-checks them on the real base: `git merge-base --is-ancestor` of H-4
  `6152d733728b9ed5bf5b571aef475f8071426386`, M-A `be99db2ace0e560fcdb7c00231e906e4aaae67e2`, M-E
  `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`, M-B `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`, M-C1
  `aa7c3792b0b374ccfa6868bb13018a1974ac3435`, M-D1 `c350593ee6235049073ab5e7207f881fb60ba307` and M-D2
  `5f051d04989a80b340e0f1542f543c0ffa68a760` (the pushed heads in `RUN/scratch/<m>/publish.json`), and
  `RUN/scratch/m-d2/publish.json` with `"merged": true`. Any failure → verdict `BASE_RED` with the reason.
- **Worktree**: t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`, the
  object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head
  git@github.com:jedimik/ScieFlow.git refs/heads/main`, then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-d3 RUN/scratch/m-d3-wt <base>`.
- Commits are local on `dev/m-d3`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-d3/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-d3-<step>/`.

## 3. In-flight, protected and changed files

**INFLIGHT** (the uncommitted paper-lifecycle work in `/home/ubuntu/Github/ScieFlow`; the same 9 paths as
M-A, M-D1 and M-D2 section 3). M-D3 edits **none** of them:

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
tree during a card, `git -C WT diff --exit-code -- <INFLIGHT>`. M-D3 *calls* `actions.run_for_path`
(`src/scieflow/core/run/actions.py:85-91`), `actions.guard_budget` (`:65-73`), `actions.record_spend`
(`:48-54`) and `budget.read_budget`; it changes none of them.

**Never edited**: `config/sandbox.yml`, the DD1-sealed files (`tests/probes/k_task8_binding.py`,
`tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, every
`AGENTS.md` except root `AGENTS.md` through the protected edit below, `CREDITS.md`, `VENDORED.md`,
`src/scieflow/core/events.py`, **everything under `scripts/remote/`** (M-D3-D1), `src/scieflow/core/data/*`
(M-B's: called, not changed), `src/scieflow/core/design/*`, `src/scieflow/experiments/manifest.py`,
`src/scieflow/experiments/envbuild.py`, `src/scieflow/web/*`, `pyproject.toml`, `uv.lock`.

**Protected edit (operator-applied before t5, `pipelines/drafts/m-d3-protected-edits.json`)** — root
`AGENTS.md`, rule 11, first three lines (`AGENTS.md:97-99` on M; M-F edits only `:241` of this file).
Exact text (the dash is U+2014, as in the file):

old:
```
11. Remote execution (metacentrum) goes ONLY through
    `uv run scripts/remote/remote.py` per `skills/remote-exec/SKILL.md` —
    never raw `ssh`/`scp`. `config/remotes.yml` (user-owned,
```
new:
```
11. Remote execution (metacentrum) goes ONLY through
    `uv run scripts/remote/remote.py` per `skills/remote-exec/SKILL.md`
    or, for a `backend: remote` campaign, `scieflow experiment sweep` and
    `scieflow experiment watch`, which call the same policy gate
    (`docs/experiments/remote-campaigns.md`) — never raw `ssh`/`scp`.
    `config/remotes.yml` (user-owned,
```
The old text occurs exactly once on M (checked on this card; the tool refuses otherwise; t0 re-checks it on
base). t5 commits the applied file alone ("docs(agents): rule 11 names the remote campaign commands
(human-approved protected edit) (M-D3 t5)").

**Existing tests whose assertions change (and only these):** the two tests that pin M-A's interim refusal,
`tests/experiments/test_campaign_validation.py:27-29` (`test_backend_remote_is_refused_with_pointer`) and
`:32-35` (`test_remote_only_keys_are_refused_with_pointer`). M-D3 removes exactly the behaviour they pin
(GAP A-3), so t1 replaces them with `test_backend_remote_needs_a_remote_block` and
`test_remote_keys_need_backend_remote` (section 6 t1 case 2). Every other existing test only gains lines.

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-29-m-d3-remote-campaigns.md` | copy of this plan | t0 |
| `src/scieflow/experiments/campaign.py` | `backend: remote`, `RemoteTarget`, `Resources`, validation, `to_dict`; the interim refusal goes | t1 |
| `src/scieflow/experiments/sweep.py` | extract `run_entry` and `write_results` (local output byte-identical) | t1 |
| `src/scieflow/experiments/runner.py` | extract `stage_argv` used by `build_command` (output identical) | t1 |
| `src/scieflow/experiments/cli.py` | t1 `run` refuses a remote campaign and `sweep` never runs one locally; t3 remote `sweep`; t4 `watch` | t1, t3, t4 |
| `src/scieflow/experiments/remote_bundle.py` | new: task plan, remote layout, task/job scripts, bundle, transfer check | t2 |
| `src/scieflow/experiments/remote_campaign.py` | new: preflight, `remote.json`, stage + push + submit (t3), watch pass, fetch, finalize (t4) | t3, t4 |
| `src/scieflow/core/jobs.py` | `record_external`, `update_external`, `append_log`; `cancel` refuses kind `remote` | t3 |
| `src/scieflow/core/service.py` | `cancel_job` turns that refusal into `ServiceError` | t3 |
| `tests/experiments/test_campaign_validation.py` | the two tests above replaced | t1 |
| `tests/experiments/test_remote_campaign_load.py`, `test_shared_writers.py` | new | t1 |
| `tests/experiments/test_remote_bundle.py`, `test_remote_scripts_run.py`, `test_transfer_check.py` | new | t2 |
| `tests/experiments/remote_support.py`, `test_remote_preflight.py`, `test_remote_submit.py`, `tests/core/test_jobs_external.py`, `tests/web/test_remote_job_row.py` | new | t3 |
| `tests/experiments/test_remote_watch.py`, `test_remote_e2e_local.py` | new | t4 |
| `docs/experiments/remote-campaigns.md` (new), `docs/experiments/cli.md`, `docs/experiments/concepts.md`, `docs/remote.md`, `mkdocs.yml` (one nav line) | docs | t5 |
| `skills/experiment-cycle/SKILL.md`, `skills/remote-exec/SKILL.md`, `src/scieflow/experiments/skills/{experiment-runner,experiment-designer}/SKILL.md`, `config/remotes.example.yml` (comment lines only) | skills, example | t5 |
| `tests/test_docs_remote_campaigns.py` | new | t5 |
| `AGENTS.md` | rule 11, operator-applied, committed by t5 | t5 |

Hotspots: `src/scieflow/core/jobs.py` and `src/scieflow/core/service.py` are shared K16 files; M-D3's change
there is additive (three functions, one refusal, two lines in `cancel_job`).

## 4. Current code, verified (on M = 6880751)

**Campaign loader.** `KNOWN_KEYS` already contains `backend` (`src/scieflow/experiments/campaign.py:18-22`);
`REMOTE_ONLY_KEYS = ("remote", "resources")` and the interim `REMOTE_REFUSAL` (`:23-27`); `_check_keys`
refuses `backend: remote` or any remote-only key with that pointer, refuses any other backend than `local`,
then unknown keys (`:32-44`). `load` (`:119-185`), `configurations` (`:187-215`), `expand` (`:217-232`,
replicate ids `<config>_r<k>`), `to_dict` (`:234-252`, what `sweep` copies into `campaign.yaml`). Run ids
are `"{i:03d}_{slug}"` from parameter values or scenario names (`:194`, `:213-214`), so they may hold any
character a value has; `manifest._is_run_id` even allows `/` (`src/scieflow/experiments/manifest.py:380-389`).

**Local execution.** `build_command` (`src/scieflow/experiments/runner.py:22-50`): inner argv `python
<stage.dir>/<script> --input <in> --output <artifacts> --<k> <v>… [--seed s]`; direct replaces `python`
with `sys.executable` (`:37-38`); apptainer binds the stage dir, the input's parent and the run dir
(`:45-49`) and refuses a missing `.sif` (`:40-44`). `execute_run` (`:68-126`) writes `config.yaml` (keys
`campaign, run_id, params, input, reference, output_file, metrics, pipeline_dir, status, started`, then
`returncode, finished`, plus `config_id, replicate, seed` for seeded runs, `:86-109`), `logs/`,
`environment.json` (`:111-113`), `metrics.json` only for a completed run with a reference (`:115-118`)
and `manifest.json` (`:120-125`). Status rule: completed iff exit 0 and `artifacts/<output_file>` exists
(`:107`). `recompute_metrics` (`:129-182`) reads only `config.yaml`, so it works on any run folder with
those keys. `sif_path` = `<stage>/env/<stage>.sif` (`src/scieflow/experiments/stage.py:74-75`).

**Sweep and results.** `run_sweep` (`src/scieflow/experiments/sweep.py:14-68`) refuses planted links at
its outputs (`:21-26`), writes `campaign.yaml` with `pipeline_dir` (`:28-33`), captures the context once
(`:34`), builds one entry per run `{run_id, config_id, replicate, seed, params, mode, status, metrics,
manifest_sha256}` (`:40-56`), writes `results.json` `{campaign, rank_by, mode, runs[, replicates]}`
atomically (`:57-66`) and `results.csv` (`:67`). This is the shape a remote campaign must reproduce.

**Manifests and verify.** `REQUIRED_KEYS` (`manifest.py:35-39`); `capture_context` (`:251-277`: code git
state, input/reference digests, stage hashes, packages, local environment, data level); `run_manifest`
(`:285-312`); `atomic_write` refuses links and uses `O_EXCL` (`:337-353`); `verify_campaign`
(`:447-490`) checks manifest hash, artifacts (missing/hash/extra, `:419-444`) and metrics hash; extra
manifest keys are ignored (`:480`). The module promises no host, user, environment variable or absolute
path in a manifest (`:3-5`).

**Experiments CLI.** `_budget_run` (`src/scieflow/experiments/cli.py:80-84`), `_guard` (exhausted,
checkpoints, `:87-93`), `_design_guard` (`:96-108`), `_analysis_guard` (`:116-140`), `_guard_capacity`
(insufficient → `job.refused reason: budget rule: insufficient`, no checkpoint, `:151-173`), `run`
(`:176-213`), `sweep` (guards, then `run_sweep`, then `record_spend(len(expand()))`, `:216-243`),
`verify` (`:298-317`).

**Remote seam (M-E), all unchanged by M-D3.** `remote.py` exit codes 0/1/2/3/4 (`scripts/remote/remote.py:4-7`);
`cmd_check` (Kerberos local + remote; `local` adapter skips it, `:50-73`); `cmd_mkdir` (op `mkdir`, normalized
path, `:297-308`); `cmd_submit` (op submit/qsub, `check_dir`, queue, token, script, env assignments, clamp
with `CLAMPED:` prints, adapter `resource_args`, attempts and concurrency ceilings → 4, ssh, `parse_submit`,
`jobs.record_submit`, `SUBMITTED:`, `:476-568`); `cmd_status` (unknown id → 1 before SSH, credential
failure → 2, `jobs.set_state`, `:571-606`); `cmd_fetch` (op fetch, dest inside the workspace, M-B
`fetch_check`, `rsync -az`, `:702-719`); `cmd_push` (op rsync, source inside the workspace, M-B `push_check`,
`--checksum` option, `:744-764`); `main` loads the remote from the repo root and maps `PolicyError` → 3
(`:900-903`, `:975-977`). Policy: `load_remote` (`scripts/remote/policy.py:61-101`, experimental opt-in
`:87-92`), `check_op` (`:104-109`), `check_scheduler_op` (`:112-127`), `check_token` `[\w.\-]+`
(`:40`, `:130-134`), `check_script` (`:166-173`), env values `[\w./\-]+` (`:44`, `:176-202`), `check_dir`
(`:205-217`), `check_queue` (`:220-226`), `clamp_resources` (`:237-292`). Ledger: `workspace/<slug>/remote/jobs.yml`,
`ACTIVE_STATES` include `unknown` (`scripts/remote/jobs.py:14-33`), `attempts`/`active_count` (`:36-44`),
`record_submit` (`:56-80`). Adapters: `JobSpec` (`scripts/remote/schedulers/base.py:46-60`); PBS submit
string (`schedulers/pbs.py:57-67`); SSH wrapper runs `timeout <s> bash <script>` in the job dir and writes
`<name>.x<pid>` (`schedulers/ssh.py:21-22,56-66`); `local` = SSH strings through `LocalTransport`
(`schedulers/local.py:12-22`, `transport.py:54-96`). The local end-to-end test pattern (real `setsid`,
teardown kills sessions, tools required not skipped) is `tests/test_remote_local_e2e.py:1-80`.

**Data protection (M-B).** `egress.push_check` refuses a push touching an L2 run unless the remote is in
`l2_remotes` or the source is under `released/` (`src/scieflow/core/data/egress.py:69-84`); `fetch_check`
warns or refuses a fetch from an `l2_remotes` remote into a lower run (`:87-105`); `remote.py` calls both
before any transport (`scripts/remote/remote.py:712,722-741,757`). `root_of_run(ws)` = `ws.parent.parent`
(`egress.py:31-33`).

**Jobs and service.** Core `STATES`/`FINAL` (`src/scieflow/core/jobs.py:27-28`), `Job` (`:34-51`), `_emit`
(`:340-343`), `start` always launches a subprocess (`:356-407`), `cancel` sets `cancelled` and kills the pid
if any (`:453-462`), `reconcile` only touches jobs with a pid (`:465-474`). `service.run_detail` lists the
run's jobs (`src/scieflow/core/service.py:139`), `service.cancel_job` (`:284-288`); the run page shows a
Cancel button for every running job (`src/scieflow/web/templates/run.html:369-374`) and shows a
`ServiceError` as a message (`src/scieflow/web/pages.py:790-801`).

**Events.** Closed `TYPES` (`src/scieflow/core/events.py:20-42`) include `job.*`, `job.refused`,
`budget.recorded`; `note.<name>` is accepted.

**Import path.** pytest puts `scripts/` on `sys.path` (`pyproject.toml:73`); the package itself does not
import `remote` today (`git grep -n "import remote\|from remote" 6880751 -- src` finds nothing). Precedent
for importing a `scripts/` module from the package: `src/scieflow/core/workspace.py:234`.

**Docs and skills that describe the interim state.** `skills/experiment-cycle/SKILL.md:12-24` (remote
campaigns driven by hand), `:88-94` (budget by hand for remote jobs); `skills/remote-exec/SKILL.md:100-117`
(fetch, no cancel, budget by hand); `docs/remote.md:3-4` ("`remote.py` is the only way…");
`docs/experiments/analysis.md:31` (the D21 sentence pinned by `tests/test_docs_analysis.py:176-182`);
`config/remotes.example.yml` (allowed_ops list without `mkdir`, `rsync`). Docs tests that pin text M-D3 must
keep: `tests/test_docs_analysis.py:123-127,164-182`, `tests/test_docs_remote.py:20-59`.

---

## Part A — Spec

### 5.1 Campaign keys (`campaign.py`)

```yaml
backend: remote                 # local (default) | remote
remote:                         # required for backend: remote; refused otherwise
  name: meta                    # an entry of config/remotes.yml (token)
  dir: /storage/brno2/home/USER/scieflow-campaigns   # absolute remote folder, inside allowed_dirs
  jobs: per-run                 # per-run (default) | array
resources:                      # optional; refused without backend: remote
  walltime: "01:00:00"          # per job (per-run: per run; array: the whole array)
  cpus: 1
  mem_gb: 4
  gpus: 0
  queue: default
  scratch_type: none
  scratch_gb: 0
```

- `backend` ∈ {`local`, `remote`}; any other value: `backend 'X' is not supported (supported: local,
  remote)` (the existing test matches `backend 'slurm'`). `remote`/`resources` without `backend: remote`
  → `CampaignError` "remote and resources need backend: remote". `backend: remote` without a `remote`
  mapping → `CampaignError` naming `remote`.
- `remote`: a mapping with exactly the keys `name` (non-empty, `[\w.\-]+`), `dir` (string matching
  `[\w./\-]+`, absolute, equal to its `posixpath.normpath`, no `..` component, not `/`), optional `jobs`
  (`per-run` | `array`, default `per-run`). Unknown keys refused by name.
- `resources`: optional mapping; keys exactly those above; defaults are `remote.py submit`'s defaults
  (`remote.py:835-842`): walltime `01:00:00` (`HH:MM:SS`, digits), cpus 1 (int ≥ 1), mem_gb 4 (int ≥ 1),
  gpus 0 (int ≥ 0), queue `default` (token), scratch_type `none` (token), scratch_gb 0 (int ≥ 0).
  Booleans are not integers. Unknown keys refused by name.
- For `backend: remote` the campaign `name` and every run id of `expand()` must match `[\w.\-]+` (they
  become remote path components and ledger task names); otherwise `CampaignError` naming the first bad run
  id. Local campaigns keep accepting any run id.
- `Campaign` gains `backend: str = "local"`, `remote: RemoteTarget | None`, `resources: Resources | None`
  (frozen dataclasses). `to_dict` adds `backend`, `remote` and the normalized `resources` (defaults filled)
  **only** for remote campaigns, so a local campaign's `campaign.yaml` is byte-identical to today, and a
  remote `campaign.yaml` reloads to an equal campaign (round trip).
- Remote policy checks (allowed dirs, ops, queues, limits) are **not** done at load (they need
  `config/remotes.yml`); they are the sweep's preflight (5.4).

### 5.2 Shared writers (`sweep.py`, `runner.py`) — no behaviour change

- `runner.stage_argv(stage_dir: str, script: str, spec, input_path: str, artifacts_dir: str,
  python: str = "python") -> list[str]`: exactly the inner argv of `build_command` (`runner.py:25-36`),
  with path arguments as given (strings). `build_command` uses it; its output is unchanged for direct and
  apptainer.
- `sweep.run_entry(spec, run_dir, mode) -> dict`: the entry of `sweep.py:40-56` (reads `config.yaml`
  status, `metrics.json` if present, the manifest sha256).
- `sweep.write_results(campaign_dir, campaign, runs, mode) -> Path`: the document of `sweep.py:57-66` plus
  `results.csv`; `run_sweep` uses both. A remote campaign's finalize uses the same two functions, so the
  shape is identical by construction.

### 5.3 Remote layout, bundle and scripts (`remote_bundle.py`)

**Campaign id and paths.** `campaign_id = f"{campaign.name}-{stamp}"`, `stamp` = UTC `%Y%m%dT%H%M%SZ`
(one function, monkeypatchable). Remote campaign folder `R = posixpath.join(remote.dir, campaign_id)`.
Local bundle `B = <run>/scratch/remote/<campaign_id>/` (scratch: rebuildable, left out of archives, root
`AGENTS.md` rule 1). Layout, identical under `B` and `R`:

```
bundle/stage/...            copy of the stage folder (script, stage.yaml, env/<stage>.sif for apptainer)
bundle/input/<basename>     copy of the campaign input (file or folder)
job.sh                      generated, constant template
tasks/<k>.sh                generated, one per run (k = 0..N-1, the order of expand())
runs/<run_id>/              written on the remote by task k
```

**Bundle builder** `build_bundle(campaign, stage, tasks, mode, dest, *, remote_root, python)`:
refuses an existing `dest`; refuses a symlink anywhere in the stage folder or the input (a link would be
pushed as a link and point nowhere on the remote); copies with hard links where the filesystem allows,
else copies (no `.sif` is duplicated on the same filesystem); skips `__pycache__`; for `apptainer` requires
`sif_path(stage)` to exist (same message as `runner.py:40-44`); writes `job.sh` and `tasks/<k>.sh` with
mode 0o755; returns `manifest.tree_digest(dest)`.

**Task argv** `task_argv(stage, spec, layout, mode, python)`: `stage_argv` with remote paths
(`R/bundle/stage`, `R/bundle/input/<basename>`, `R/runs/<run_id>/artifacts`). `apptainer`:
`["apptainer", "exec", "--bind", …sorted binds…, R/bundle/stage/env/<stage>.sif] + inner` with binds
{stage dir, input's parent, run dir} exactly as `runner.py:45-49`. `direct`: inner with `python` =
`sys.executable` for the `local` scheduler, else `python3` (development only; never ranked, M-A A-6).

**Task script** `tasks/<k>.sh` (every interpolated value through `shlex.quote`; `<argv>` is the quoted
argv joined by spaces):

```bash
#!/bin/bash
# Generated by scieflow experiment sweep (M-D3). Do not edit.
set -u
cd <R> || exit 97
run=runs/<run_id>
if [ -e "$run/remote/exit" ]; then exit 0; fi
mkdir -p "$run/artifacts" "$run/logs" "$run/remote" || exit 96
date -u +%Y-%m-%dT%H:%M:%S+00:00 > "$run/remote/started"
{ printf 'system=%s\n' "$(uname -s)"; printf 'machine=%s\n' "$(uname -m)"; printf 'runtime=%s\n' "$(<runtime> --version 2>&1 | head -n 1)"; } > "$run/remote/environment.txt"
<argv> > "$run/logs/stdout.log" 2> "$run/logs/stderr.log" < /dev/null
rc=$?
date -u +%Y-%m-%dT%H:%M:%S+00:00 > "$run/remote/finished"
( cd "$run" && find artifacts logs -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) > "$run/remote/SHA256SUMS.tmp" && mv "$run/remote/SHA256SUMS.tmp" "$run/remote/SHA256SUMS"
printf '%s\n' "$rc" > "$run/remote/exit.tmp" && mv "$run/remote/exit.tmp" "$run/remote/exit"
exit "$rc"
```

`<runtime>` is `apptainer` (apptainer mode) or the direct interpreter. No host name (`uname -n`) is ever
written. The exit marker is written last and atomically, so its presence means the checksum list is
complete.

**Job script** `job.sh` (constant except `<R>` and `<N>`):

```bash
#!/bin/bash
# Generated by scieflow experiment sweep (M-D3). Do not edit.
cd <R> || exit 97
if [ -n "${SCIEFLOW_TASK-}" ]; then
  case "$SCIEFLOW_TASK" in ''|*[!0-9]*) exit 98;; esac
  exec bash "tasks/$SCIEFLOW_TASK.sh"
fi
rc=0
for k in $(seq 0 <N-1>); do bash "tasks/$k.sh" || rc=1; done
exit "$rc"
```

Per-run jobs set `SCIEFLOW_TASK=<k>` through `remote.py`'s validated `--env` path (PBS `-v`, Slurm
`--export=ALL,…`, SSH `env …`, `pbs.py:58-61`, `slurm.py:54-57`, `ssh.py:57-60`); the array job sets
nothing and runs every task in order. The scheduler working directory does not matter: `job.sh` changes
to `R` itself (PBS runs a spooled copy from `$HOME`).

**Transfer check** `verify_transfer(run_dir) -> (ok, reason, files)` (local side): `remote/SHA256SUMS`
must exist (`transfer-missing` otherwise); every line must match `^[0-9a-f]{64}  (artifacts|logs)/[^\n\\]+$`
(a line `sha256sum` escapes with `\` is refused); paths must be unique, inside `artifacts/` or `logs/`, no
`..`; every listed file must exist as a regular file with that sha256 (`transfer-hash`); every regular file
under `artifacts/` and `logs/` must be listed (`transfer-extra`); any symlink under the run folder →
`fetched-symlink`. Marker parsing `read_markers(run_dir)`: `exit` = one line of digits (else
`no-exit-marker`), `started`/`finished` parse with `datetime.fromisoformat` (else the fetch time, flagged),
`environment.txt` lines `^(system|machine|runtime)=([\w .,:()+\-/]{0,120})$` (others ignored; missing →
null).

### 5.4 Preflight (`remote_campaign.preflight`) — before anything is written or sent

In this order; any failure is a refusal (`click` exit 1) that writes nothing, spends nothing and runs no
SSH, and (inside a run) emits one `job.refused` with `reason: remote` and the rule id, or the existing
`budget`/`design` reasons for those guards:

1. The campaign folder lies inside a run (`actions.run_for_path`) — rule `no-run`; the folder does not
   exist yet — rule `campaign-exists`.
2. The local guards, exactly as for a local sweep: `_check_stage_seed`, `_guard`, `_design_guard`,
   `_analysis_guard`, `_guard_capacity(budget_run, N)`.
3. `policy.load_remote(root, remote.name)` with `root = egress.root_of_run(run)` (missing file, unknown
   remote, bad scheduler, experimental without opt-in all come from there) — `POLICY:` text kept.
4. Ops: `check_op` for `check` (needed for every adapter: `cmd_check` checks the op before it decides
   whether the adapter needs Kerberos, `remote.py:51-55`), `mkdir`, `rsync`, `fetch`;
   `check_scheduler_op` for `submit` and `status`. All missing ops are listed in one message.
5. `check_dir(remote.dir)` and `check_dir(R)`; `check_queue`; `check_token` on the job names `sf-<k>` /
   `sf-array`, the task names `<campaign_id>.<k>` / `<campaign_id>.array` and every run id; `check_script("job.sh")`.
6. `clamp_resources` must return no warning (M-D3-D6) — rule `resources-over-limit` naming each warning;
   `adapter.resource_args(JobSpec(...))` must not raise (e.g. SSH has no scratch).
7. `jobs: array` needs `N ≤ 1000` (the replicate cap) — no scheduler opt-in is needed (it is one ordinary
   job). `jobs: per-run` has no N limit beyond the budget; slots come from `max_concurrent_jobs`.
8. Data protection: `egress.push_check(root, B, remote.name)` (the bundle lies inside the run),
   `egress.push_check(root, campaign.input, …)` and, when set, the reference — so an L2 input that lives in
   another run is refused too. Message from M-B kept.
9. Inputs: the input and the stage folder contain no symlink; apptainer needs the `.sif`.

Then `cmd_check` once (Kerberos; `local` prints `OK` without SSH). Exit 2 → refusal `NO_TICKET` with
`remote.py`'s instruction ("ask the user to run `kinit`"); nothing written.

### 5.5 Stage, push and submit (`sweep`, t3)

1. Capture the context once (`manifest.capture_context(campaign, stage, mode, campaign_dir)`; it writes
   `packages/<sha12>.txt` for apptainer exactly as a local sweep does).
2. Write `campaign.yaml` (as `sweep.py:28-33`), build the bundle into `B`, write `remote.json` (5.6) with
   state `staged`, every task `pending`, then open the core job (5.8). Emit
   `note.experiment_remote kind: staged` (counts, campaign id, remote name).
3. Run one **pass** (5.7) without waiting: push (`cmd_mkdir R`, `cmd_push(B/, R, checksum=True)`), then
   submit. Per-run: tasks in index order while `cmd_submit` returns 0; return 4 (concurrency) stops
   submitting for this pass (the rest stay `pending`); return 1 marks that task `submit-failed` (reason
   `submit-failed`) and continues; return 3 stops with the `POLICY:` refusal. Array: one `cmd_submit` of
   `job.sh` without `SCIEFLOW_TASK`, name `sf-array`, task `<campaign_id>.array`; every task records the
   same job id. Resources from the campaign (already known not to clamp), `kind` `job`, `submit_dir`
   none (the job runs in `R`).
4. The job id of a successful submit is read from the ledger (`jobs.find`-by-task: the last entry whose
   `task` equals the task name), never from stdout. **Duplicate guard:** before submitting a task, if the
   ledger already has an entry for its task name (a crash after the submit but before `remote.json` was
   saved), that job id is adopted and nothing is submitted.
5. **Budget (D21, M-D3-D3):** after the first successful submit of the campaign, inside the run,
   `actions.record_spend(run, experiment_runs=N)` once; `remote.json` `budget_recorded: N`. Never again
   for this campaign.
6. Print `submitted <k> of <N> runs on <remote> (<jobs>); collect with: scieflow experiment watch <dir>`
   and exit 0, or exit 1 if the push failed (state stays `staged`; `watch` retries the push) or nothing
   could be submitted for a reason other than a full queue.

`remote.py`'s own stdout/stderr lines (`SUBMITTED:`, `CLAMPED:`, `NOTICE:`, `PUSHED:`…) are captured
(`contextlib.redirect_stdout/stderr`) and relayed to stderr prefixed `remote: `; they never decide
anything.

### 5.6 `remote.json` (campaign folder, written only by the tool, read as untrusted)

```json
{"version": 1, "campaign": "<name>", "campaign_id": "<name>-<stamp>", "remote": "<name>",
 "scheduler": "<adapter>", "remote_dir": "<R>", "jobs": "per-run|array", "mode": "apptainer|direct",
 "resources": {"walltime": "...", "cpus": 1, "mem_gb": 4, "gpus": 0, "queue": "...",
               "scratch_type": "none", "scratch_gb": 0},
 "state": "staged|pushed|submitting|running|complete", "created": "<iso>", "bundle": {"sha256": "...", "files": 0},
 "budget_recorded": 0, "core_job": "<id>|null", "context": { …capture_context… },
 "tasks": [{"index": 0, "run_id": "...", "config_id": "...", "replicate": null, "seed": null,
            "params": {}, "task": "<campaign_id>.<k>|<campaign_id>.array", "job_name": "sf-<k>|sf-array",
            "job_id": null, "state": "pending|submitted|submit-failed|finalized",
            "status": null, "reason": null}]}
```

Written atomically (`manifest.atomic_write`, canonical JSON) after every state change. Every `watch` pass
re-validates it before any SSH: version 1; `campaign` equals the folder's `campaign.yaml` name; the tasks
equal `Campaign.load(campaign.yaml).expand()` in order (run ids, config ids, replicates, seeds, params);
`remote` loads from policy and its scheduler equals `scheduler`; `remote_dir` passes `check_dir` and equals
`<remote.dir of campaign.yaml>/<campaign_id>`; job ids, job names and task names pass `check_token`; the
task shape matches `jobs`. Any mismatch → refusal `remote-state-invalid` naming the field, nothing done.

### 5.7 The watch pass (`watch`, t4)

`scieflow experiment watch <campaign_dir> [--once] [--interval S] [--max-minutes M] [--json]`;
`--interval` 5..3600 s (default 60), `--max-minutes` 1..1440 (default 60). One pass:

1. Load and validate `remote.json` (5.6); `state: complete` → print the summary, exit 0, no SSH.
2. `cmd_check` (as in 5.4); `NO_TICKET` → exit 1, nothing changed.
3. `staged` → push (5.5 step 3); failure → exit 1.
4. **Status.** For every distinct job id of `submitted` tasks, `cmd_status`. Return 2 → stop the pass
   (`NO_TICKET`, exit 1); return 1 → that job is left as is for this pass (a stderr line names it). The new
   ledger state is read from `jobs.yml`. `queued`/`running`/`unknown` → still active (`unknown` never
   counts as finished, M-E-D6).
5. **Fetch and finalize** every task whose job is `done`, `failed` or `cancelled` (array: every task of the
   one job): `cmd_fetch(R/runs/<run_id>/, <campaign_dir>/runs/<run_id>/)` (refuses a symlinked or non-dir
   destination first). Fetch exit ≠ 0 → the task stays `submitted` for the next pass, unless the job is
   `cancelled`, then `failed`/`job-cancelled`. Then finalize (5.9); task → `finalized`.
6. **Submit** pending tasks (per-run) as in 5.5 steps 3–5.
7. All tasks `finalized` or `submit-failed` → finalize the never-run tasks (5.9), write `results.json` and
   `results.csv` with `sweep.write_results` (`mode` from `remote.json`), state `complete`, close the core
   job (`done`, exit code 0), emit `note.experiment_remote kind: complete` with counts. Exit 0.
8. Otherwise append one counts line to the core job's log; `--once` → exit 4; the loop sleeps `--interval`
   and repeats until complete (0) or `--max-minutes` passed (4).

`--json` prints `{"campaign", "campaign_id", "state", "complete", "counts": {"pending", "submitted",
"finalized", "submit_failed", "completed", "failed"}}` (sorted keys) after the last pass.

### 5.8 The core job (`core/jobs.py`, `core/service.py`, t3)

- `jobs.record_external(project, *, kind, label, run_dir, argv, cwd) -> Job`: `state "running"`,
  `queued`/`started` now, `pid None`, `sandboxed False`, empty `.log`/`.err` files created, record saved,
  `job.queued` and `job.started` emitted (no `pid` field).
- `jobs.update_external(job, state, exit_code=None) -> Job`: only `done` or `failed`; saves and emits
  `job.finished`/`job.failed` with `exit_code` and `duration_s`; refuses (`ValueError`) a job that has a
  pid or is already final.
- `jobs.append_log(job, line)`: appends one line to `job.log` (counts only).
- `jobs.cancel`: a job of kind `remote` → `jobs.NotCancellable` (subclass of `ValueError`) with "remote
  campaigns cannot be cancelled from ScieFlow yet (M-E-D2); the scheduler job keeps running — ask the user";
  no state change, no event. `service.cancel_job` catches it and raises `ServiceError(str(exc))`.
- The remote campaign opens one such job (kind `remote`, label `<campaign> on <remote>`, argv
  `["scieflow", "experiment", "watch", "<campaign_dir>"]`, cwd the campaign folder) and stores its id in
  `remote.json`. `jobs.reconcile` leaves it alone (no pid). Web: no change; the row shows `running` and
  "unsandboxed" (true: the remote job is not in ScieFlow's sandbox), and Cancel shows the refusal.

### 5.9 Finalizing a run (t4)

For a fetched task: `read_markers`, `verify_transfer`. Status `completed` iff the exit marker is `0`, the
transfer check passed and `artifacts/<output_file>` is a regular file; else `failed` with the first reason
of: `transfer-missing`, `transfer-hash`, `transfer-extra`, `fetched-symlink`, `no-exit-marker`,
`exit-nonzero`, `no-output`. Then, in the run folder (each write refuses links, `manifest.atomic_write` or
the same check):

- `config.yaml`: exactly the local keys and order of `runner.py:86-109` (input/reference are the **local**
  paths, so `recompute_metrics` works), status, `returncode` (int or null), `started`/`finished` from the
  markers, plus `backend: remote`, `remote: <name>` and, when failed, `reason`.
- `environment.json`: `{"mode", "command": <remote argv>, "recorded", "remote": {"name", "scheduler",
  "jobs", "task_index", "job_id"}, "platform": {"system", "machine"}, "runtime"}`.
- `metrics.json`: only for `completed` with a reference — `compute_metrics` locally (as `runner.py:115-118`).
- `manifest.json`: `manifest.run_manifest(...)` with the stored context, its `environment` replaced by
  `{"mode", "python": <runtime version for direct, else null>, "platform": {"system", "machine"}, "sif",
  "sif_sha256", "apptainer_version": <runtime version for apptainer, else null>}` (the M-D2 keys), plus
  `"remote": {"name", "scheduler", "jobs", "task_index", "transfer": {"verified": bool, "reason", "files",
  "sums_sha256"}}`. No remote folder, no job id, no host.
- A task that never ran (`submit-failed`, or a cancelled job without markers): an empty run folder is
  created with `config.yaml` (status `failed`, reason, `returncode: null`, started = finished = the time of
  the decision), `environment.json`, `manifest.json` (artifacts `[]`, transfer not verified) — so the run
  stays in the report as a failure (experiments `AGENTS.md` rule 4).

The results entry comes from `sweep.run_entry`. `compare`, `report`, `stats`, `verify` and
`scieflow experiment metrics <run_dir>` work on the campaign unchanged.

### 5.10 Data protection (M-B, D9–D11, D23)

Push of the bundle and of an input that lies in any run: `push_check` (5.4 step 8) — an L2 run, or input
from an L2 run, goes only to a remote in `l2_remotes`. Fetch: `cmd_fetch` applies `fetch_check` (warning or
refusal for a fetch from an `l2_remotes` remote into a run below L2). Outputs of an L2 run are L2-derived
as for local runs; `data_level` in each manifest is the local run's level. Nothing else leaves the machine:
no metric values, no file contents and no paths in events.

### 5.11 Docs and skills (t5)

- New `docs/experiments/remote-campaigns.md` (nav entry under Experiments after "Analysis and
  Statistics"): the campaign keys and defaults; the preflight list; the two shapes and D21 ("A remote
  array job of N tasks counts as N runs"; recorded once at the first accepted job); `sweep` then `watch`
  (options, exit codes 0/4/1, `--json`); the remote layout; the post-fetch hash manifest and the failure
  reasons; `remote.json` (tool-written, re-validated, agent-writable like `jobs.yml`); the core job and
  why it cannot be cancelled; data protection; the remote's requirements (bash, coreutils, findutils,
  apptainer — check with `remote.py runtime-info`); the residuals R1–R8; the required `allowed_ops`
  (`check`, `mkdir`, `rsync`, `submit`/`qsub`, `status`/`qstat`, `fetch`).
- `docs/experiments/cli.md`: the sweep section gains a remote paragraph; a new section
  `## scieflow experiment watch`. `docs/experiments/concepts.md`: the Campaign section names `backend:
  remote`; the Run tree shows `remote.json` and a run's `remote/` folder. `docs/remote.md`: line 3–4
  becomes "… is the only way ScieFlow reaches a remote host, directly or through `scieflow experiment
  sweep|watch` for a remote campaign (AGENTS.md rule 11)", and a short "Remote campaigns" section links
  the new page. `config/remotes.example.yml`: comment lines naming the ops a remote campaign needs (the
  YAML values are unchanged).
- `skills/experiment-cycle/SKILL.md` "Backend selection": `backend: remote` is a normal campaign — dispatch
  it like a local one; the sub-agent runs `sweep`, then `watch` until complete; results-summary contract
  unchanged; the line "records its own runs" stays (pinned) and remote campaign runs are recorded by the
  sweep too; only work driven by hand through `remote.py` (not a campaign) goes through `scieflow run
  spend`. `skills/remote-exec/SKILL.md`: a "Campaigns" paragraph — a campaign with `backend: remote` is run
  with `scieflow experiment sweep|watch`, never by submitting its runs with `remote.py`; the Snakemake gate
  and fix loop stay for repository tasks. `experiment-runner`: steps for a remote campaign (preflight
  refusals are boundaries; `NO_TICKET` → stop and ask for `kinit`; `watch` exit 4 = wait; `verify`
  afterwards). `experiment-designer`: the schema block shows `backend`, `remote`, `resources`.
- Root `AGENTS.md` rule 11: the protected edit (section 3).

### 5.12 Security and integrity properties (reviewed in every ticket that touches them)

1. Every refusal happens before any write, spend or SSH call (5.4), and a refusal names a rule, never a
   value read from a data file.
2. Remote access only through `remote.py`'s command functions and `policy.load_remote`; no `subprocess`
   call in `remote_campaign.py` or `remote_bundle.py` (bundle copying is pure Python); `scripts/remote/*`
   unchanged.
3. Every value in a generated script is `shlex.quote`d; every remote path is `check_dir`-ed or built only
   from a `check_dir`-ed base plus `[\w.\-]` tokens; job ids are only read from the ledger.
4. `remote.json` is untrusted and fully re-validated against `campaign.yaml`, the policy and the token
   rules on every pass (5.6).
5. Fetch writes only into `<campaign_dir>/runs/<run_id>/` (destination checked: inside the campaign folder,
   not a symlink); fetched symlinks fail the run and nothing reads through them; metrics only from a
   verified, completed run.
6. Never a duplicate submission (ledger adoption, 5.5 step 4); never a resubmission, cancel or remote delete.
7. Manifests: no host, user, remote folder, job id or environment variable (5.9, M-D3-D12).
8. Budget: one `record_spend(N)` per campaign, only after the first accepted job; pre-checks before any
   work.
9. Data protection: `push_check` on the bundle and on inputs from other runs; `fetch_check` via `cmd_fetch`.
10. Outputs only inside the campaign folder and the run's `scratch/remote/`; every output write refuses a
    planted link.

### 5.13 What M-D3 does not do (stated)

No native scheduler arrays, cancel, resubmit or remote clean-up (M-D3-D2, D7); no Snakemake campaign stages;
no remote package capture; no `singularity` alias; no key-based auth; no web panel beyond the job row; no
change to `scripts/remote/*`, `manifest.py`, `events.py` or the in-flight files.

---

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a
checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only; the `local`
adapter runs only on this host), no push/PR, no credentials, no patient data (synthetic fixtures only), never
edits an `INFLIGHT` path, a sealed file, `config/sandbox.yml`, `events.py`, `scripts/remote/*`,
`src/scieflow/core/data/*`, `src/scieflow/core/design/*`, `manifest.py`, `envbuild.py`, `src/scieflow/web/*`,
`pyproject.toml`, `uv.lock`, `CREDITS.md`/`VENDORED.md`, `CLAUDE.md` or any `AGENTS.md` (the t5 protected
edit is applied by the operator). Every implementation and fix card runs the `INFLIGHT` guard and reports its
exit code.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-d3-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-d3-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-d3/junit G6_CACHE_DIR=~/.cache/scieflow-m-d3 RUN/tools/g6_pytest.sh WT <head-sha> m-d3-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with
  completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> m-d3-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes**: the four named in the installer's standing rules, one rerun, both results recorded.
  No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-d3/executions.jsonl` and states RED and GREEN evidence in its result.
- Focused sets per ticket (clean-tree runner on the ticket's final commit): t1, t2 `tests/experiments`;
  t3 `tests/experiments tests/core/test_jobs.py tests/core/test_jobs_external.py tests/test_remote_cli.py
  tests/test_remote_pbs_golden.py tests/web/test_remote_job_row.py tests/web/test_run_page_actions.py`;
  t4 `tests/experiments tests/core/test_jobs_external.py tests/test_remote_cli.py tests/test_remote_local_e2e.py`;
  t5 `tests/experiments tests/test_docs_remote.py tests/test_docs_analysis.py tests/test_docs_design.py
  tests/test_docs_role.py tests/test_docs_remote_campaigns.py`.
- Test sweeps run `--direct` (no apptainer on this host). `stats`/`compare`/`report` exclude direct runs by
  default (M-A A-6), so tests that analyse a campaign pass `--include-direct`.
- Remote fixtures: `tests/experiments/remote_support.py` (t3) builds a temporary project root with
  `config/remotes.yml`, `workspace/<slug>/` with `status.yml` and `budget.yml`, a tiny seeded stage (writes a
  PNG derived from the input and the seed; declares `accepts_seed: true`), and (a) a `local` remote
  (`host: localhost`, `auth: none`, `allow_experimental_scheduler: true`, allowed dir under the pytest temp
  dir) for real runs, and (b) a `pbs` remote driven by a recording fake transport (the pattern of
  `tests/test_remote_cli.py:20`) for golden command strings and scripted scheduler answers. Tests never
  reach a real host, never use the real `config/remotes.yml`, and kill every session they start (the
  teardown of `tests/test_remote_local_e2e.py`). Required tools (`bash setsid timeout rsync sha256sum find
  xargs`) missing → the test fails, never skips.

### t0 — worktree, plan copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the seven ancestor checks and the M-D2 publish
   receipt; otherwise `BASE_RED` with the reason. `worktree add -b dev/m-d3 RUN/scratch/m-d3-wt <base>`
   (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. t0 records what it measures (base = main after M-D2, and probably M-F/M-C2). Only the
   load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 on `base`; write `reviews/m-d3-t0-baseline.md` (per cite:
   still present / moved (new line) / changed). Inventories: (a) whether base contains M-F and M-C2 (their
   `RUN/scratch/<m>/publish.json` if present) and whether either changed a file of section 3's list; (b)
   every existing test that pins the interim remote refusal, `campaign.yaml` content, `results.json` keys,
   `remote.py` output or `jobs.cancel` behaviour; (c) whether the protected edit's `old` text occurs exactly
   once in root `AGENTS.md`; (d) `git grep -n "import remote\|from remote" base -- src` (expected empty).
4. Copy `reviews/m-d3-plan.md` byte for byte to `docs/superpowers/plans/2026-10-29-m-d3-remote-campaigns.md`
   and commit it alone ("docs(plans): M-D3 remote campaigns plan").

Verdicts `DONE` | `BASE_RED`.

### t1 — campaign keys and shared writers (Haiku) → t1r (Sonnet)

Files: `campaign.py`, `sweep.py` (`run_entry`, `write_results`), `runner.py` (`stage_argv`), `cli.py`
(`run` refusal; `sweep` refuses a remote campaign with "remote campaigns are submitted by sweep once
M-D3 t3 lands" — replaced in t3, no test pins that sentence), tests `test_remote_campaign_load.py`,
`test_shared_writers.py`, and the two replaced tests in `test_campaign_validation.py`.

RED → GREEN:
1. A campaign with `backend: remote`, `remote: {name: meta, dir: /data/x}` loads; `resources` defaults
   equal 5.1; `jobs` default `per-run`; `to_dict` → `campaign.yaml` → load gives an equal campaign; a local
   campaign's `to_dict` has no `backend`/`remote`/`resources` keys (equal to today's dict).
2. Replaced tests: `backend: remote` without `remote` → `CampaignError` naming `remote`; `remote: {...}` or
   `resources: {...}` without `backend: remote` → "need backend: remote"; `backend: slurm` still refused
   with `backend 'slurm'` and now lists `local, remote`.
3. Validation, each → `CampaignError` naming the key: `remote: meta` (a string), unknown remote key,
   `name: "a b"`, `dir` relative / with `..` / `/` / `a;b` / not normalized, `jobs: parallel`, unknown
   resource key, `cpus: 0`, `cpus: true`, `mem_gb: "4"`, `gpus: -1`, `walltime: "1h"`, `queue: "a b"`,
   `scratch_gb: -1`; a remote campaign whose scenario name makes a run id with a space → refused naming
   the run id; the same scenario in a local campaign still loads.
4. `stage_argv` with the paths of `build_command` gives `build_command`'s inner argv (direct: first element
   swapped; apptainer: the tail after the sif); `tests/experiments/test_runner.py` unchanged and green.
5. `write_results`/`run_entry`: a local direct sweep's `results.json` and `results.csv` bytes are equal to
   those produced by the pre-refactor code path for the same runs (the test builds the expected document
   from the same run folders with the literal dict of `sweep.py:40-66`); replicate campaigns keep
   `replicates`; `tests/experiments/test_sweep.py` and every other experiments test unchanged and green.
6. `scieflow experiment run -c <remote campaign>` → exit 1 "runs locally only; submit a backend: remote
   campaign with sweep", no run folder; `sweep -c <remote campaign> --direct` in t1 → exit 1, no `runs/`
   folder, no spend, nothing executed (a spy on `runner.execute_run` is never called).

t1r checklist: local campaigns byte-compatible (`campaign.yaml`, `results.json`, `results.csv`, argv);
validation complete and names keys; only the two named tests changed; no remote I/O; INFLIGHT guard 0.

### t2 — bundle, generated scripts, transfer check (Haiku) → t2r (Sonnet)

Files: `remote_bundle.py` (new), tests `test_remote_bundle.py`, `test_remote_scripts_run.py`,
`test_transfer_check.py`.

RED → GREEN:
1. `task_argv` for a seeded spec: apptainer argv = `apptainer exec --bind <R>/bundle/input --bind
   <R>/bundle/stage --bind <R>/runs/<id> <R>/bundle/stage/env/<stage>.sif python <R>/bundle/stage/<script>
   --input … --output <R>/runs/<id>/artifacts --gain 0.5 --seed 3` (binds sorted, as `runner.py:45-49`);
   direct with the `local` scheduler uses `sys.executable`, otherwise `python3`.
2. Golden text: `job.sh` for N = 3 and `tasks/0.sh` (direct and apptainer) equal the 5.3 templates exactly;
   a parameter value `a'b; touch PWNED` and a script name with a space stay single argv elements (the
   rendered line, parsed with `shlex.split`, equals the argv).
3. `build_bundle`: layout of 5.3; the `.sif` is a hard link when on the same filesystem (same inode), a copy
   otherwise (monkeypatched `os.link` raising `OSError`); `__pycache__` skipped; scripts 0o755; existing
   destination → refused; a symlink in the stage folder or in an input folder → refused naming the path;
   apptainer without `.sif` → refused; returned digest = `manifest.tree_digest(dest)`.
4. **Scripts run** (`test_remote_scripts_run.py`, real bash, no scheduler): render a direct bundle for a
   2-run campaign into a temporary "remote" folder and run `SCIEFLOW_TASK=1 bash job.sh`: only run 1 has
   `remote/{exit,started,finished,environment.txt,SHA256SUMS}`, exit marker `0`, `sha256sum -c` of
   `SHA256SUMS` from the run folder passes, artifacts exist, `environment.txt` has the three keys and no
   host name (`socket.gethostname()` not in it); `bash job.sh` (array) then runs run 0 and skips run 1
   (marker present, run 1's files unchanged); `SCIEFLOW_TASK=x` → exit 98; a failing stage → exit marker
   non-zero, `SHA256SUMS` still complete.
5. `verify_transfer` on that output → ok; tamper an artifact → `transfer-hash`; delete a listed file →
   `transfer-hash`; add an unlisted file → `transfer-extra`; plant a symlink → `fetched-symlink`; remove
   `SHA256SUMS` → `transfer-missing`; a line with `..`, an absolute path, a `\`-escaped name, a bad hash →
   refused; `read_markers` rules of 5.3 (bad exit marker → `no-exit-marker`).

t2r checklist: argv parity with `build_command`; quoting complete; no host/user in any remote file; no
subprocess in the module; refusals before writing; INFLIGHT guard 0.

### t3 — preflight, stage, push, submit; the core job (Haiku) → t3r (Sonnet)

Files: `remote_campaign.py` (new: import helper for `remote` — insert `<config.repo_root()>/scripts` into
`sys.path` only when `import remote` fails, as `workspace.py:234`; `preflight`, `RemoteState`
load/validate/save, `stage_and_submit`, the submit part of the pass), `cli.py` (`sweep` remote branch),
`core/jobs.py`, `core/service.py`, tests `remote_support.py`, `test_remote_preflight.py`,
`test_remote_submit.py`, `tests/core/test_jobs_external.py`, `tests/web/test_remote_job_row.py`.

RED → GREEN:
1. Preflight refusals, each before any write and without any transport call (the fake transport records
   none; no `remote.json`, no `scratch/remote/`, no spend): outside a run (`no-run`); existing campaign
   folder; no `config/remotes.yml`; unknown remote; experimental scheduler without opt-in; each missing op
   (all listed at once); `dir` outside `allowed_dirs`; queue not allowed; walltime/cpus/mem/gpus over the
   limit (`resources-over-limit`, no `CLAMPED` submit); scratch on the `ssh` adapter; budget exhausted
   (checkpoint, as local) and insufficient (`job.refused` budget/insufficient); design/analysis guards as
   for a local sweep; an L2 run with the remote not in `l2_remotes` (M-B message); an input inside another
   L2 run; a symlink in the input; apptainer without `.sif`. Inside a run each emits one `job.refused` with
   its reason.
2. `NO_TICKET`: a Kerberos `pbs` remote whose fake `klist -s` fails → exit 1 with the `kinit` instruction,
   nothing written.
3. **PBS golden** (certified adapter, fake transport): a 3-run per-run campaign → the recorded calls are,
   in order, `klist -s`, the remote ticket check, `mkdir -p -- <R>`, `rsync -az --partial --checksum <B>/
   <user>@<host>:<R>/`, then `cd <R> && qsub -N sf-0 -q default -l walltime=01:00:00 -l
   select=1:ncpus=1:mem=4gb -v SCIEFLOW_TASK=0 job.sh` and the same for 1 and 2 (exact strings);
   `jobs.yml` has three entries with tasks `<campaign_id>.0..2`, scheduler `pbs`; `remote.json` tasks carry
   the fake job ids; `budget.yml` `spent.experiment_runs` +3 with exactly one `budget.recorded` event.
4. Concurrency: `max_concurrent_jobs: 2`, 5 runs → 2 `submitted`, 3 `pending`, budget +5 (once), exit 0
   with "submitted 2 of 5". Submit failure on the second (fake qsub exit 1) → task 1 `submit-failed`, task 2
   still submitted. Array shape → exactly one `qsub` of `job.sh` without `-v`, name `sf-array`, every task
   the same job id, budget +N. No successful submit at all → no spend.
5. Duplicate guard: a ledger that already has `<campaign_id>.0` (simulated crash: `remote.json` without the
   job id) → the next submit pass adopts that id and sends no `qsub` for task 0.
6. Push failure (fake rsync exit 1) → exit 1, state `staged`, no submit, no spend.
7. `remote.json`: canonical, atomic; its validation refuses (before any transport call) a changed
   `remote_dir`, a `remote_dir` outside `allowed_dirs`, a changed run id or params, an unsafe job id, a
   scheduler different from the remote's, a wrong version (`remote-state-invalid` naming the field).
8. Core job: after the sweep, `service.run_detail` lists one job of kind `remote`, state `running`, pid
   `null`, label `<campaign> on <remote>`; events `job.queued` and `job.started` for it;
   `service.cancel_job` → `ServiceError` with the M-E-D2 sentence, record unchanged, no `job.cancelled`;
   `jobs.reconcile` leaves it `running`; `update_external(done)` emits `job.finished`; `update_external`
   on a job with a pid → `ValueError`. `tests/core/test_jobs.py` unchanged and green.
9. Web (`tests/web/test_remote_job_row.py`, the `project`/`client` fixtures of `tests/web/conftest.py`):
   the run page renders the remote job row; POST cancel → redirect with the refusal message; the job page
   renders its log. No `src/scieflow/web` change.
10. Local adapter smoke: a 2-run direct campaign on the `local` remote submits two real jobs (ledger ids
    `<pid>.<epoch>`), the teardown kills their sessions.

t3r checklist: refusal order and "nothing before preflight"; only `remote.py` functions touch the remote;
golden PBS strings byte-exact; one spend; no duplicate; core job semantics; `scripts/remote/*` unchanged;
INFLIGHT guard 0.

### t4 — watch: status, fetch, transfer check, finalize, results (Haiku) → t4r (Sonnet)

Files: `remote_campaign.py` (pass steps 4–8, finalize), `cli.py` (`watch`), tests `test_remote_watch.py`,
`test_remote_e2e_local.py`.

RED → GREEN (`test_remote_watch.py`, fake PBS transport whose `rsync_from` copies from a prepared folder
that plays the remote, and whose `qstat` answers come from `tests/fixtures/scheduler/pbs/*.txt`):
1. A `done` job → fetched, transfer verified, run `completed`, `metrics.json` computed locally, manifest has
   every `REQUIRED_KEYS` key plus `remote` (no `remote_dir`, no job id, no host name or user name strings in
   the file), `config.yaml` keys = local keys + `backend`, `remote`; after all tasks: `results.json` and
   `results.csv` written, state `complete`, core job `done`, exit 0; a second `watch` → exit 0, no transport
   call.
2. Failure reasons: exit marker `3` → `failed`/`exit-nonzero` (no metrics); no exit marker on a `failed`
   job → `no-exit-marker`; tampered artifact → `transfer-hash` and no metrics; planted symlink in the
   fetched artifacts → `fetched-symlink`, metrics never read it (a spy on `compute_metrics` is not called);
   missing output → `no-output`; `cancelled` job without markers → `failed`/`job-cancelled` run folder with
   `config.yaml`, `manifest.json`; `submit-failed` task → failed run folder; all stay in `results.json`
   with status `failed`.
3. `queued`/`running`/`unknown` → not fetched, `--once` exit 4, one counts line in the core job log;
   `cmd_status` return 1 for one job → others still processed; return 2 → exit 1 `NO_TICKET`, nothing
   changed; fetch failure → task stays `submitted`, next pass retries.
4. Pending tasks are submitted as slots free (pass 1: 2 of 5; jobs finish; pass 2 submits the next two),
   budget unchanged after the first spend.
5. The fetch destination: a planted symlink at `runs/<id>` → refused for that task (`fetched-symlink`), no
   write through it; every write into the run folder refuses a planted link at the target and `.tmp`.
6. `--json` document as 5.7; `--interval`/`--max-minutes` ranges; the loop with a monkeypatched `sleep`
   stops at complete (0) or at the time limit (4).
7. Existing campaign tools on the result: `verify` exit 0; `stats --include-direct`, `compare
   --include-direct`, `report --include-direct` exit 0; `scieflow experiment metrics <run_dir>` on a
   fetched run updates it coherently (M-D2 recompute rules) and `verify` reports `metrics-recomputed`.

**Acceptance (GAP row), `test_remote_e2e_local.py` (real `local` adapter, real subprocesses, ≤ 120 s, every
session killed in teardown):**
8. **"A remote (local-adapter) campaign yields `results.json` identical in shape to a local one."** The same
   seeded 2-configuration × 2-replicate campaign, swept once locally (`--direct`) and once as `backend:
   remote` on the `local` remote (`--direct`), `watch` until exit 0 (bounded loop of `watch --once` with
   0.5 s pauses): top-level keys equal; per-run entry keys equal; run ids, config ids, replicates, seeds,
   params and statuses equal; metrics equal (deterministic stage, same input); the artifacts of each run are
   byte-equal; `results.csv` headers equal; both campaigns pass `verify`.
9. **"Budget counts N for an array job" (D21).** A 2-configuration × 3-replicate campaign with `jobs: array`
   on the `local` remote: after `sweep`, `budget.yml` `spent.experiment_runs` = 6 with one `budget.recorded`
   event, and `jobs.yml` has exactly one entry; after `watch`, `results.json` has 6 runs, all `completed`,
   and the budget is still 6.
10. A per-run campaign of 3 runs with `max_concurrent_jobs: 1` completes through repeated passes (the
    concurrency path end to end).

t4r checklist: every 5.9 rule and reason; metrics only from verified completed runs; shape identity by
construction (`write_results`); no value in events; links refused; exit codes; INFLIGHT guard 0.

### t5 — docs, skills, example config, rule 11 (Haiku) → t5r (Sonnet)

Before t5 the operator applies the protected edit (installer step `pe_t5`); t5 commits that file alone.

Files: section 3's docs and skills rows, `mkdocs.yml`, `config/remotes.example.yml` (comments only), root
`AGENTS.md` (commit of the applied edit), `tests/test_docs_remote_campaigns.py`.

RED → GREEN:
1. `docs/experiments/remote-campaigns.md` exists, is in the `mkdocs.yml` nav right after the Analysis page,
   and names every campaign key and default of 5.1, `per-run` and `array`, the sentence "A remote array job
   of N tasks counts as N runs", `watch`, `--once`, `--interval`, `--max-minutes`, `--json`, exit codes `0`,
   `4`, `1`, every failure reason of 5.9, `remote.json`, `SHA256SUMS`, "cannot be cancelled", `l2_remotes`,
   `runtime-info`, the required ops, and residuals R1–R8.
2. `docs/experiments/cli.md` has `## scieflow experiment watch` and the sweep section names `backend:
   remote`; every flag documented there exists in `watch --help` (extend the M-D2 help-flag test pattern in
   the new test file, not in `test_docs_analysis.py`); `concepts.md` shows `remote.json` and `remote/`.
3. `docs/remote.md` names `scieflow experiment sweep` and links `experiments/remote-campaigns.md`; every
   phrase pinned by `tests/test_docs_remote.py` stays.
4. Skills: experiment-cycle no longer says remote campaigns are refused or driven by hand (the strings
   "is refused by `scieflow experiment`" and "drive the campaign's tasks yourself" are gone), says
   `sweep` then `watch`, and keeps a line with "records its own runs"; remote-exec has the Campaigns
   paragraph; experiment-runner names `watch` and `NO_TICKET`; the designer schema block shows `backend`,
   `remote:` and `resources:`.
5. `config/remotes.example.yml` still loads with `policy.load_remote` (its values unchanged) and a comment
   names `mkdir` and `rsync` for remote campaigns.
6. Root `AGENTS.md` contains the new rule 11 text of section 3 byte for byte and the old first lines are
   gone; `tests/test_docs_role.py`, `tests/test_docs_design.py`, `tests/test_docs_analysis.py`,
   `tests/test_docs_remote.py` still pass.
7. `mkdocs build --strict` into `RUN/scratch/m-d3/site` if mkdocs is in the venv (else recorded "not
   available").

t5r checklist: docs ↔ code (every flag and exit code exists); D21 sentence; honest limits stated; rule 11
byte-exact; no other `AGENTS.md` change; INFLIGHT guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by added tests); `mkdocs build --strict` (or "not available");
INFLIGHT guard over `base..tip`; linear history (no merge commits), at least one commit per ticket t0..t5;
`git diff base..tip -- tests/` removes no line except inside the two replaced tests of
`tests/experiments/test_campaign_validation.py` (section 3); `config/sandbox.yml`, sealed files,
`events.py`, `scripts/remote/`, `src/scieflow/core/data/`, `src/scieflow/core/design/`, `manifest.py`,
`envbuild.py`, `src/scieflow/web/`, `CREDITS.md`, `VENDORED.md`, `CLAUDE.md` and every `AGENTS.md` except
the t5 commit of root `AGENTS.md` absent from the diff; `git diff base..tip -- pyproject.toml uv.lock`
empty. Writes `RUN/scratch/m-d3/pr-body.md` (plain English: the five capabilities, decisions
M-D3-D1..D14, absolute counts, residuals R1–R8, "please merge with a merge commit"; no credentials,
prompts or patient data) and `reviews/m-d3-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the two GAP acceptance criteria — results.json shape
identity on the `local` adapter and budget N for an array job (D21) — and decisions M-D3-D1..D14; the
protected edit byte-exact), code and security (section 5.12: refusal order, only `remote.py` functions touch
the remote, quoting, `remote.json` re-validation, fetch confinement and symlinks, no duplicate submit, one
spend, manifest privacy, data-protection checks), evidence (re-run memory-capped: `tests/experiments
tests/core/test_jobs_external.py tests/test_remote_cli.py tests/test_remote_local_e2e.py
tests/test_docs_remote_campaigns.py tests/web/test_remote_job_row.py`, confirm the branch result's absolute
counts, re-run the INFLIGHT guard, `pr-body.md` accurate). Output `reviews/m-d3-final-review-r<N>.md`. PASS
→ the installer's publish step; CHANGES → final_fix (new commits, full suites re-run) → final; FAIL →
escalate.

## 7. Pipeline (`pipelines/drafts/m-d3-run.yml`, linear)

```
t0        Haiku   worktree, plan copy, green base         DONE→t1, BASE_RED→escalate
t1        Haiku   campaign keys, shared writers           DONE→t1r
t1r       Sonnet  review                                  PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                       DONE→t1r
t2/t2r/t2f        bundle, scripts, transfer check         same shape → t3
t3/t3r/t3f        preflight, stage, push, submit, job     same shape → t4
t4/t4r/t4f        watch, fetch, finalize, results         same shape → t5   (installer inserts pe_t5)
t5/t5r/t5f        docs, skills, example, rule 11          same shape → branch
branch    Haiku   full suites + PR body                   DONE→final, RED→escalate
final     Sonnet  whole-branch review                     PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                       DONE→final
(pe_t5, publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 65, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/m-d3-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff, decisions,
gap; the installer adds `plan`. Protected edits: `pipelines/drafts/m-d3-protected-edits.json`, one edit,
`before_step: t5`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Never run against a real cluster.** The PBS submit strings are the certified ones (golden test), but
  the job script, `cd` into the remote folder, `seq`, `find -print0`/`sort -z`/`xargs -0 -r` and
  `sha256sum` have only run on this host through the `local` adapter. A first real Metacentrum campaign is a
  human-run smoke (follow-up F2), like M-E's Slurm smoke.
- **R2 Container runtime on the compute node.** Container runs call `apptainer`; a site that provides only
  `singularity` (or needs a module loaded) fails every run with `exit-nonzero` — visible in the logs and the
  report. Check with `remote.py runtime-info` first; an alias is follow-up F3.
- **R3 Transfer check, not tamper proof.** `SHA256SUMS` is written by the remote run itself, so it proves
  the copy matches what the remote wrote, not that the remote is honest. `remote.json`, like `jobs.yml`, is
  agent-writable; it is re-validated against `campaign.yaml` and the policy, but an agent that rewrites the
  campaign folder coherently is not caught (same class as M-D2 R2).
- **R4 Packages.** The package list is the local capture of the same `.sif` (identical bytes, sha256 in the
  manifest); without local apptainer it is "not captured". Direct runs on a remote record none.
- **R5 Array = sequential.** `jobs: array` runs one task after another inside one job; its walltime must
  cover all N. Native arrays are F1.
- **R6 No cancel, no resubmit, no clean-up.** A stuck job is reported (M-E-D2); remote folders accumulate
  until the user removes them.
- **R7 Clock and markers.** `started`/`finished` come from the remote's clock; a run killed by the walltime
  has no exit marker and is `no-exit-marker` even if its outputs exist.
- **R8 Guard reach and the web row.** Like the budget and design guards, everything applies only inside a
  run (remote campaigns require one). The run page shows a Cancel button on the remote job; it answers with
  the refusal.

## 9. After M-D3

Queue (`continuity/milestones.yml`): M-G (`after: [m-d2, m-c2, m-f]`) resolves `[run:<id>]` claims against
manifests, now including remote runs; F-2 lists M-D3 among its prerequisites. Follow-ups surfaced while
planning: F1 native scheduler arrays (`qsub -J`, `sbatch --array`) with array-aware ids and status, as an
experimental opt-in after a live check; F2 a human-run Metacentrum smoke campaign (one small per-run and one
array campaign) with its own ledger entry; F3 a `container_runtime: apptainer|singularity` remote setting;
F4 a cancel command for remote jobs and campaigns (M-E-D2, needs a decision); F5 a supervised
fix-and-resubmit for failed campaign runs; F6 remote package capture; F7 remote clean-up of finished
campaign folders (needs a new allowed op); F8 a web panel for a remote campaign's progress (D7: the CLI is
complete).
