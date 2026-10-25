# Remote execution (scheduler seam)

`scripts/remote/remote.py` is the only way ScieFlow reaches a remote host
(AGENTS.md rule 11). Each remote in `config/remotes.yml` names a scheduler.
The scheduler is an adapter in `scripts/remote/schedulers/`. Every policy
check (allowed directories, allowed operations, resource limits, character
rules) runs in `policy.py` before any SSH call. Adapters never decide what is
allowed and never accept free-form scheduler flags.

## Adapters

| Adapter | Maturity | Transport | submit | status | cancel | logs |
|---|---|---|---|---|---|---|
| `pbs` | certified | ssh | `qsub` | `qstat` | `qdel` | `logs` |
| `slurm` | experimental | ssh | `sbatch` | `sacct` | `scancel` | `logs` |
| `ssh` | experimental | ssh | `submit` | `status` | `cancel` | `logs` |
| `local` | experimental | local | `submit` | `status` | `cancel` | `logs` |

The table is printed by `remote.py schedulers` (add `--json` for
machine-readable output). That command reads no config and runs no SSH.

- **certified** (`pbs`): the Metacentrum PBS command strings are unchanged
  and pinned by golden tests.
- **experimental** (`slurm`, `ssh`, `local`): none of these has run against a
  real cluster or host. Their command strings come from documentation or from
  the design, and their parsers are tested on synthetic fixtures. A remote
  that uses one is refused unless it sets the opt-in (below).

## Configuration keys

- `scheduler:` one of `pbs`, `slurm`, `ssh`, `local`. Any other value is refused.
- `allow_experimental_scheduler: true` is required for `slurm`, `ssh` and
  `local`. It must be exactly the boolean `true`; a string such as `"yes"` is
  refused.
- `local` also requires `host: localhost` and `auth: none`. It runs the job on
  the machine that runs ScieFlow, so it is refused without the opt-in too.
- `allowed_ops` accepts either the PBS names (`qsub`, `qstat`, `qdel`, `logs`)
  or the abstract names (`submit`, `status`, `cancel`, `logs`). An operation
  passes when its abstract name or its adapter's alias is listed.
- There is no cancel command in `remote.py` (M-E-D2). The adapters build and
  test their cancel commands for later use. Never cancel a job by hand, and
  never resubmit a duplicate of a stuck job.

## Dry-run gate for Snakemake tasks

1. `remote.py dryrun <remote> <dir> <script> --workspace WS --task T` submits
   the job with no GPUs, no scratch, and `SCIEFLOW_DRYRUN=1` in the job
   environment. `dryrun` only sets `SCIEFLOW_DRYRUN=1`. The runner script must
   check that variable and run `snakemake -n` (or an equivalent dry run). A
   script that ignores it runs the real pipeline on the dry run's small
   allocation. A dry run counts toward the task's attempt ceiling.
2. `remote.py status <remote> <job_id> --workspace WS` until it prints
   `STATE: done`.
3. `remote.py dag <remote> <job_id> --workspace WS` writes the dry run's log to
   `WS/remote/dag-T.txt` and stores its SHA-256 in the ledger
   (`DAG_RECORDED: ...`). A log over 10 MiB is refused.
4. `remote.py submit ... --task T --after-dryrun` refuses (`NO_DAG`, exit 1,
   before SSH) unless the task's latest dry run is done, its DAG is recorded,
   and the file on disk still has that hash.

Limits of the gate. `jobs.yml` and `WS/remote/dag-<task>.txt` are both
agent-writable. The gate protects against accidents and skipped steps. It does
not stop an agent that rewrites both files. The gate is also opt-in per
submit, because the code cannot know that a task is a Snakemake task; the
remote-exec skill makes `--after-dryrun` mandatory for those tasks.

## Ledger states

`queued`, `running`, `done`, `failed`, `cancelled`, `unknown`.

- `done` is set only when the scheduler says the job finished with exit code 0.
- `unknown` means the scheduler cannot say: the job is no longer visible, or
  its output is ambiguous. An `unknown` job still counts toward the concurrency
  ceiling, so it never frees a slot. Check the job by hand before treating it
  as finished.
- A PBS state letter that is not recognised maps to `queued`, never to `done`
  (M-E-D7).

## Honest limits

- Slurm and SSH have never run against a real system. Only a live smoke run,
  done by a person, can move them out of experimental (decision D19).
- Slurm `TIMEOUT` and `OUT_OF_MEMORY` are reported as `failed`, but the exit
  code shown is the one Slurm recorded in `ExitCode`, which can be `0`. So
  `STATE: failed exit=0` is a normal result for those states.
- A failing `sacct` call on Slurm reads as `unknown`, not as an error. The
  `SACCT_FAILED` label is printed only when the SSH call itself fails.
- SSH and local do not enforce cpus, memory or GPUs. Each submit prints a
  `NOTICE:` saying so. They enforce the walltime with `timeout`, and they refuse
  scratch.
- SSH and local status can be fooled by pid reuse. If a job dies without
  writing its exit file and its pid is reused before the next status call,
  `kill -0` reports `running`. This needs a crash and a reused pid together.
- Only Kerberos/GSSAPI SSH authentication is supported (M-E-D3). Clusters that
  need SSH keys cannot be used yet.
- An unexpected PBS state letter is treated as `queued`; the adapter never
  reports it as `done`.
- The Slurm submit passes the login environment to the job (`--export=ALL,...`).
  Slurm does the same without `--export`.
- The local end-to-end test needs Linux `bash`, `setsid`, `nohup`, `timeout`
  and `rsync`. On a runner where these differ, the test fails loudly; it is
  never skipped silently.

## Configuring a remote

Copy `config/remotes.example.yml` to `config/remotes.yml` and fill in your own
values. The example has a commented-out Slurm entry. Uncomment it only after
you have read the limits above.
