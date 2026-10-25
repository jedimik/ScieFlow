---
name: remote-exec
description: Submit, monitor, fix, and fetch jobs on a configured remote (metacentrum) through scripts/remote/remote.py — PBS certified; Slurm/SSH/local experimental. Kerberos preflight, dry-run gate, git-only fix loop, bounded retries.
---

# Remote Execution Protocol (metacentrum)

You are the **coordinator**. ALL remote access goes through
`uv run scripts/remote/remote.py ...` (AGENTS.md rule 11) — never raw
`ssh`/`scp`. The user's `config/remotes.yml` is the authority on what you
may touch; a `POLICY:` refusal (exit 3) is a hard boundary: report it,
never work around it. `<remote>` below is the remote's name in that file
(e.g. `meta`); `WS` = `workspace/<slug>`.

## Setup (once per host, user-facing)

- The user copies `config/remotes.example.yml` → `config/remotes.yml` and
  fills in host, user, scheduler, allowed_dirs, allowed_ops, limits. Offer to walk
  them through it; never fill in paths you were not given.
- Scheduler values: `pbs` is certified (Metacentrum). `slurm`, `ssh` and
  `local` are experimental; each refuses to load unless that remote also sets
  `allow_experimental_scheduler: true`. Those three have never run against a real
  cluster — say so when the user asks to use them. `remote.py schedulers` lists
  the adapters; `docs/remote.md` has the limits.
- Optional hardening: suggest the user add Claude Code permission
  deny-rules for bare `ssh`/`scp`/`rsync` so the wrapper is the only path.

## Preflight (every session, before anything else)

1. `uv run scripts/remote/remote.py check <remote>`
2. Exit 2 (`NO_TICKET`) → STOP. Tell the user to run `kinit` and wait.
   Never attempt any authentication yourself.
3. Exit 1 → retry once; still failing → report to the user and stop.

## Task loop (per campaign task; autonomous within an approved campaign —
AGENTS.md rule 3 delegation applies, bounded by config/remotes.yml limits)

1. **Sync**: `remote.py pull <remote> <dir> --branch <branch>` for each remote
   repo dir the task needs. The wrapper refuses tracked changes before switching,
   pulls the named branch with `--ff-only`, and reports the resulting branch and
   SHA. Pull failure (dirty tree, diverged) → report; do not force.
   Jobs use the package environment (`uv sync --extra experiments`). A remote
   checkout made before the module merge needs the one-time cleanup in
   `docs/MIGRATION.md` — report it to the user; never delete paths yourself.
   For a runner that requires a completely clean checkout, also run
   `remote.py repo-status <remote> <dir> --include-untracked`; report any path
   and never delete or hide it without the user's authorization.
   When the user has authorized removal and the data must remain recoverable,
   `remote.py move <remote> <source> <destination>` may relocate an exact path
   between allowed roots. It refuses an existing destination; verify or fetch
   the source first and record both paths in the run log.
2. **Snakemake gate** (MANDATORY when the task runs a Snakemake pipeline —
   most of the user's workloads). Four steps, in this order:
   - `remote.py dryrun <remote> <dir> <script> --workspace WS --task <task>
     --walltime 00:30:00 --cpus 1 --mem-gb 4`. It submits the runner in dry-run
     mode: no GPUs, no scratch, and `SCIEFLOW_DRYRUN=1` in the job environment.
     Use the SAME `--task <task>` name as the real run, so the dry run counts
     against that task's attempt ceiling (the ledger counts attempts by task name).
   - `remote.py status <remote> <job_id> --workspace WS` until `STATE: done`.
     A failed dry run → enter the fix loop below; dry-run attempts count against
     the same task ceiling.
   - `remote.py dag <remote> <job_id> --workspace WS`. It writes the dry run's
     log to `WS/remote/dag-<task>.txt` and stores its SHA-256 in the ledger
     (`DAG_RECORDED`). Read the file: it must show a clean exit and the full job
     DAG. This file is the execution plan of record.
   - Only then submit the real run with `--after-dryrun` (step 3). The gate is
     code: it refuses (`NO_DAG`, exit 1, before SSH) unless the task's latest dry
     run is done, its DAG is recorded, and the file on disk still matches.
     Limits: `jobs.yml` and the DAG file are agent-writable, so the gate stops
     accidents and skipped steps, not an agent that rewrites both. Do not edit
     the DAG file by hand.
3. **Submit**:
   `remote.py submit <remote> <dir> <script> --workspace WS --task <task>
   --walltime ... --cpus ... --mem-gb ... [--gpus N] [--queue Q] [--after-dryrun]`
   Snakemake tasks always pass `--after-dryrun`.
   Generic runners may receive non-secret values through repeatable
   `--env NAME=VALUE`; the validated map is recorded in `jobs.yml`. Never pass
   credential contents this way.
   Heed `CLAMPED:` warnings — if a clamp likely breaks the job (e.g.
   walltime halved), tell the user instead of submitting blind.
   Exit 4 (`LIMIT:`) → concurrency: wait and poll; attempts: go to
   "Exhaustion" below.
4. **Monitor**: poll `remote.py status <remote> <job_id> --workspace WS`
   with backoff (start ~2 min, double to ~15 min cap; long jobs need no
   tight polling). `STATE: done` → step 6. `STATE: failed` → step 5.
   `STATE: unknown` keeps its concurrency slot: check the job by hand, and do not
   treat it as finished.
5. **Fix loop** (git-only — never edit files on the remote):
   a. `remote.py logs <remote> <job_id> --workspace WS`; read the error.
   b. Diagnose honestly. Fix the script/pipeline in the LOCAL clone of
      that repo, commit with a message naming the job id, push.
   c. `remote.py pull`, then resubmit (same `--task` name — the ledger
      counts attempts). Snakemake tasks re-enter the dry-run gate.
   d. Never "fix" by deleting checks, silencing errors, or shrinking the
      experiment to make it pass — that is rerun-until-green (AGENTS.md
      rule 5 anomaly policy applies).
6. **Fetch**: `remote.py fetch <remote> <dir>/results/ WS/remote/data/<task>/
   --workspace WS`. Analyze locally; quantitative claims cite fetched
   artifacts by path (provenance, AGENTS.md rule 9). If analysis warrants
   more experiments, new campaigns go through the normal approval
   contract, then re-enter this loop.

## Exhaustion and anomalies

- Attempt ceiling hit (exit 4 on submit, or `max_fix_attempts` fixes
  spent): mark the experiment phase `failed` in `status.yml` (record the
  failed task and last error in `WS/log.md` and the results summary), run
  `uv run scripts/checkpoint.py workspace/<slug> --reason anomaly`, report
  what you tried and the last error. Never shop for workarounds past the
  ceiling.
- Job stuck queued far beyond expectation: report to the user. There is no
  cancel command: never cancel by hand, and never resubmit a duplicate.
- Every submit/status/fix/fetch gets a line in `WS/log.md`; budget: record
  each real (non-dry-run) job as an experiment run (`budget.py`).
