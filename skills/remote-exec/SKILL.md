---
name: remote-exec
description: Submit, monitor, fix, and fetch PBS jobs on a configured remote (metacentrum) through scripts/remote/remote.py — Kerberos preflight, snakemake dry-run gate, git-only fix loop, bounded retries.
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
  fills in host, user, allowed_dirs, allowed_ops, limits. Offer to walk
  them through it; never fill in paths you were not given.
- Optional hardening: suggest the user add Claude Code permission
  deny-rules for bare `ssh`/`scp`/`rsync` so the wrapper is the only path.

## Preflight (every session, before anything else)

1. `uv run scripts/remote/remote.py check <remote>`
2. Exit 2 (`NO_TICKET`) → STOP. Tell the user to run `kinit` and wait.
   Never attempt any authentication yourself.
3. Exit 1 → retry once; still failing → report to the user and stop.

## Task loop (per campaign task; autonomous within an approved campaign —
AGENTS.md rule 3 delegation applies, bounded by config/remotes.yml limits)

1. **Sync**: `remote.py pull <remote> <dir>` for each remote repo dir the
   task needs. Pull failure (dirty tree, diverged) → report; do not force.
2. **Snakemake gate** (MANDATORY when the task runs a Snakemake pipeline —
   most of the user's workloads):
   - Submit a dry-run first: the pipeline's runner script with
     `snakemake -n` semantics, `--gpus 0`, minimal resources
     (e.g. `--cpus 1 --mem-gb 4 --walltime 00:30:00`), reusing the SAME
     `--task <task>` name as the real run so the dry-run counts against
     that task's attempt ceiling (the ledger counts attempts by task name).
   - Wait for it; `remote.py logs` must show a clean exit AND the full
     job DAG. Save that output to `WS/remote/dag-<task>.txt` — it is the
     execution plan of record.
   - Dry-run failed → enter the fix loop below (dry-run attempts count
     against the same task ceiling).
   - Only after a clean DAG: submit the real run with real resources.
3. **Submit**:
   `remote.py submit <remote> <dir> <script> --workspace WS --task <task>
   --walltime ... --cpus ... --mem-gb ... [--gpus N] [--queue Q]`
   Heed `CLAMPED:` warnings — if a clamp likely breaks the job (e.g.
   walltime halved), tell the user instead of submitting blind.
   Exit 4 (`LIMIT:`) → concurrency: wait and poll; attempts: go to
   "Exhaustion" below.
4. **Monitor**: poll `remote.py status <remote> <job_id> --workspace WS`
   with backoff (start ~2 min, double to ~15 min cap; long jobs need no
   tight polling). `STATE: done` → step 6. `STATE: failed` → step 5.
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
- Job stuck queued far beyond expectation: report to the user; never
  qdel (not an allowed op) or resubmit a duplicate.
- Every submit/status/fix/fetch gets a line in `WS/log.md`; budget: record
  each real (non-dry-run) job as an experiment run (`budget.py`).
