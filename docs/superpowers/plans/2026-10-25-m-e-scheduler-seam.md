# M-E plan: scheduler seam (round 1)

Pipeline `m-e-plan`, step plan, round 1, epoch 1. Card `t_bfd57df1`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261025_143711_36c62e`).
Written 2026-10-25. Previous review: none (round 1).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt.

## 0. Inputs and references used

Immutable inputs (sha256 re-computed this round, all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…7da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…238 |
| `continuity/milestones.yml` | 3555a57d…95ea |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…f85c |

Scope source: GAP-ANALYSIS.md §8 row M-E and §7; DECISIONS D4 and D19 (Slurm + plain SSH designed
now, experimental, contract-tested on fixtures; no PSI/J).

Code was read read-only from git objects (`git show <ref>:<path>`). No checkout was touched.

- `M` = remote `main` = `6b0a4efa44249a76ab5cbb0802115357eeb71009`
  (`git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main`, 2026-10-25 ~14:40 UTC;
  objects fetched with `git fetch --no-tags --no-write-fetch-head`, no ref created).
  `M` is "Merge pull request #17 from jedimik/dev/m-a-correctness". Its parents are `75a1b6c`
  (the M-A base) and `be99db2` (the M-A tip that T7 reviewed PASS). `git diff be99db2 M` is empty.
  So M-A, including A-4, is on main: **the dependency M-E → M-A is met.**
- One read-only probe: the four `scripts/remote` modules plus `scieflow/core/store.py` were exported
  from `M` with `git archive` into `RUN/scratch/m-e-plan-probe/` and run there with a fake transport.
  That produced today's exact PBS command strings (section 4.2). Nothing in a checkout was run.

All line numbers below are on `M`.

## 1. For the human: what M-E changes, in plain words

Today ScieFlow can run jobs on one kind of cluster: PBS (Metacentrum). The PBS commands are written
straight into `scripts/remote/remote.py`. M-A made a non-PBS `scheduler:` value fail at load instead
of quietly running PBS commands. M-E builds the seam the gap plan asked for (D4).

1. **A scheduler adapter layer.** A new package, `scripts/remote/schedulers/`, holds one small adapter
   per scheduler. Each adapter turns already-checked values (job name, directory, walltime, cpus,
   memory, gpus, queue, scratch, environment) into a fixed command string, and reads that
   scheduler's answer back into one of six states: queued, running, done, failed, cancelled or
   unknown. The safety rules stay where they are, in `policy.py`: allowed directories, operations
   and limits, the character checks, and "check everything before any SSH". No adapter accepts
   free-form scheduler flags.
2. **PBS moves behind the seam unchanged.** The PBS adapter produces exactly today's `qsub`, `qstat`
   and log-file commands, character for character. New "golden" tests record today's strings before
   the move, and the move must keep them green. Your existing `config/remotes.yml` keeps working.
3. **Three new adapters, all marked experimental.**
   - **Slurm** (`sbatch`/`sacct`/`squeue`/`scancel`) is written from the public documentation.
   - **Plain SSH** is for a workstation without a scheduler. It starts a background process, keeps
     its output and exit code in files, and enforces the walltime with `timeout`.
   - **Local** runs the same thing on this machine. It exists so the whole
     submit → status → logs → fetch loop is tested end to end without a cluster.

   An experimental adapter is refused unless you add `allow_experimental_scheduler: true` to that
   remote in `config/remotes.yml`, which is your file. None of them has run against a real cluster.
   A live Slurm test is a later step that you run yourself (D19).
4. **Operation names.** `allowed_ops` can list either today's PBS names (`qsub`, `qstat`) or new
   general names (`submit`, `status`, `logs`). Each adapter maps a general name to its own command,
   so existing files stay valid.
5. **`remote.py schedulers`** lists the adapters and their maturity.
6. **The Snakemake dry-run gate becomes code (B19).**
   - `remote.py dryrun …` submits a dry run (no GPUs, no scratch, `SCIEFLOW_DRYRUN=1` passed to the
     job).
   - `remote.py dag …` saves the finished dry run's log as `remote/dag-<task>.txt`.
   - `remote.py submit --after-dryrun …` refuses a real run unless that saved plan exists and is
     unchanged.
7. **Docs.** There is a new `docs/remote.md` page. The remote-exec skill and the example config are
   updated.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-E-D1 Maturity.** Only `pbs` is certified. `slurm`, `ssh` and `local` are experimental; each
  needs `allow_experimental_scheduler: true` on that remote. That includes `local`, so a stray
  config cannot run scripts on this machine by accident.
- **M-E-D2 No cancel command yet.** Every adapter implements and tests its cancel command (`qdel`,
  `scancel`, a process-group kill). M-E adds **no** `remote.py cancel`, because that would be a new
  remote capability. The skill keeps "never cancel or resubmit a duplicate". Exposing cancel is a
  later decision (natural home: M-D3).
- **M-E-D3 Authentication is unchanged.** Slurm and SSH remotes use the same Kerberos/GSSAPI,
  BatchMode SSH as Metacentrum. Clusters that need SSH keys (for example IT4I Karolina) need a
  separate decision about a key-based `auth:` mode; that is not part of M-E.
- **M-E-D4 Dry-run gate shape**, as in item 6 above. `--after-dryrun` is opt-in per submit; the
  skill makes it mandatory for Snakemake tasks. A dry run counts against the task's attempt ceiling,
  as the skill already says.
- **M-E-D5 Status of an unknown job is refused before SSH.** Today
  `remote.py status <remote> <id>` for an id that is not in `jobs.yml` runs `qstat` and then crashes
  with a Python `KeyError` (`jobs.set_state` raises at `scripts/remote/jobs.py:71`). After M-E it
  prints `UNKNOWN_JOB` and exits 1 with no SSH, for every scheduler. Command strings are unchanged;
  only this crash path changes.
- **M-E-D6 The ledger records the scheduler.** Each new `jobs.yml` entry stores `scheduler:`.
  `status`, `logs` and `dag` refuse an entry whose scheduler differs from the remote's current one
  (a policy refusal, exit 3). Old entries without the field count as `pbs`. Two new ledger states,
  `cancelled` and `unknown`, are added. `unknown` counts as active for the concurrency ceiling, so
  "unknown" never frees a slot.
- **M-E-D7 PBS state letters are kept as they are.** An unexpected PBS `job_state` letter still maps
  to `queued` (`remote.py:26,506`). Changing that would change PBS behaviour. It never maps to
  `done`, and it is listed as a residual.
- **M-E-D8 Resource honesty.**
  - Slurm scratch is only `--tmp`, with scratch type `tmp`; any other scratch type is refused.
  - SSH and local refuse scratch. They do not enforce cpus, memory or gpus, and they print a
    `NOTICE:` saying so. They enforce the walltime with `timeout`.
- **M-E-D9 Fixtures are synthetic and labelled.** There is no live capture of Metacentrum `qstat`
  or of any Slurm output in the repo. The fixtures are transcribed from the field layout the existing
  tests use and from public documentation, and a README in the fixtures directory says so.
- **M-E-D10 No protected-file edit.** `AGENTS.md` rule 11 (`AGENTS.md:97-103`) already routes *all*
  remote execution through `remote.py`, so it stays correct. There is no `m-e-protected-edits.json`.

**Proposed PR** (push and merge go through the operator's publish step after the final review
PASS, as the milestone chain authorises; see `approvals/milestone-chain-human-20261024.md`):
- one PR, head `dev/m-e` → base `main`;
- merged with a merge commit;
- title "M-E Scheduler seam";
- body written by the `branch` step.

## 2. Base, worktree and dependency

- Base = remote `main` at the time step `t0` runs. Today that is `6b0a4ef`.
- `t0` refuses (verdict `BASE_RED`) unless
  `git merge-base --is-ancestor be99db2ace0e560fcdb7c00231e906e4aaae67e2 <base>`, which proves M-A
  is on main.
- `t0` fetches the objects with
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`.
  This creates no ref.
- `t0` then creates the worktree with
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-e RUN/scratch/m-e-wt <base>`.
  The new branch pins the base, so the fetched objects cannot be garbage-collected.
- On a retry, if the worktree already exists on `dev/m-e` at `<base>` (or at `<base>` plus t0's own
  plan commit), t0 reuses it. It never recreates or resets it.
- Work happens only in `RUN/scratch/m-e-wt`. Git commands go through `git -C`/the worktree.
  Nothing touches `/home/ubuntu/Github/ScieFlow` or the checked-out tree of
  `/home/ubuntu/ScieFlow-claude-f2976f52`.
- Commits are local, on `dev/m-e` only. Every review fix is a new commit: no amend, rebase, squash
  or force.
- Scratch output goes to `RUN/scratch/m-e/`: junit, logs, `executions.jsonl` and `pr-body.md`.

## 3. In-flight and protected files

**In-flight paper-lifecycle files.** These are the same nine paths as M-A's `INFLIGHT` constant
(`reviews/m-a-plan.md` §3):

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

M-E edits **none** of them. The gap row says the same: "`scripts/remote/` untouched by in-flight
work". Every implementation, fix and branch step runs this guard and reports
`inflight_guard_exit`:

- `git -C $WT diff --exit-code <base> -- <INFLIGHT>`;
- on a dirty tree, also `git -C $WT diff --exit-code -- <INFLIGHT>`.

**Protected agent-instruction files.** There are none in scope (M-E-D10). `skills/remote-exec/SKILL.md`
is a skill, not an agent-instruction file. M-A also edited skills directly; only `AGENTS.md` needed
a protected edit there.

**Files M-E may change (complete list):**

| File | Change |
|---|---|
| `scripts/remote/errors.py` | new |
| `scripts/remote/schedulers/__init__.py` | new |
| `scripts/remote/schedulers/{base,pbs,slurm,ssh,local}.py` | new |
| `scripts/remote/policy.py` | registry validation, maturity, scheduler-op check |
| `scripts/remote/remote.py` | submit/status/logs through the adapter; new subcommands |
| `scripts/remote/jobs.py` | states, `scheduler` field, active count |
| `scripts/remote/transport.py` | `LocalTransport` and `for_remote` |
| `config/remotes.example.yml` | comments, plus a commented-out Slurm example |
| `skills/remote-exec/SKILL.md` | updated for the seam |
| `docs/remote.md` | new |
| `mkdocs.yml` | one nav line |
| `docs/superpowers/plans/2026-10-25-m-e-scheduler-seam.md` | copy of this plan |
| new tests under `tests/` | listed per ticket |
| `tests/fixtures/scheduler/**` | new |
| `tests/test_remote_policy.py` | only the scheduler test (T1, T2) |
| `tests/test_remote_jobs.py` | additions only |

`tests/test_remote_cli.py` and `tests/test_remote_transport.py` may only gain lines
(`git diff <base>..HEAD -- <file>` shows no removed line). Each review checks this.

## 4. Current code, verified

### 4.1 Where the scheduler lives today

**`scripts/remote/policy.py`**

- `REQUIRED_KEYS` includes `scheduler` (`:15-16`).
- `SCHEDULERS = ("pbs",)` with the comment "M-E turns this into an adapter registry" (`:17-18`).
- `load_remote` refuses a non-string or unknown value with
  "scheduler … is not supported (supported: pbs); other schedulers come with the scheduler seam (M-E)"
  (`:72-78`).
- `Remote` is a dataclass of exactly the seven required keys plus `name` (`:46-55`).
- `check_op` does an exact name match against `allowed_ops` (`:85-90`).
- `check_token`/`check_script`/`check_dir`/`check_queue`/`clamp_resources` (`:93-255`) are the
  input boundary. M-E does not change them.
- `PolicyError` is defined at `:42-43`.

**`scripts/remote/remote.py`** (773 lines)

- `STATE_MAP` for PBS letters is at `:26`.
- `cmd_submit` (`:388-468`):
  - checks the op `qsub` (`:394`), the dir, queue, task and script, an optional `submit_dir`
    (`:401-404`), the name, the environment, and clamping;
  - then the attempt and concurrency ceilings (`:422-433`);
  - then builds `select=` (`:435-439`), `-v` env (`:441-444`), and the `qsub` line (`:445-450`);
  - failure prints `QSUB_FAILED:` (`:452,456`); the job id is the last stdout line (`:454-458`);
  - it records the job in the ledger (`:459-466`) and prints `SUBMITTED: <id> attempt=<n>` (`:467`).
- `cmd_status` (`:481-527`):
  - checks the op `qstat` (`:489`) and the job-id token (`:490`), then runs `qstat -xf <id>` (`:491`);
  - a credentials marker → exit 2 (`:492-493`); a failure → `QSTAT_FAILED` (`:494-496`);
  - it parses `job_state`/`Exit_status` (`:497-506`); no `job_state` → `UNPARSEABLE` exit 1 with
    the ledger untouched (`:499-501`);
  - then `jobs.set_state`, which raises `KeyError` for an id not in the ledger (`jobs.py:64-71`);
  - output `STATE: <s> exit=<e>` (`:511`); `--details` prints allowlisted fields (`:512-526`).
- `cmd_logs` (`:530-553`):
  - checks the op `logs`;
  - looks the job up in the ledger, `UNKNOWN_JOB` if absent (`:533-537`);
  - re-checks the ledger dir and the stem (`:540-546`);
  - runs `cat <stem>.o<seq> <stem>.e<seq>` (`:547-551`).
- `main` (`:614-769`):
  - argparse has 17 subcommands (`:617-621`), each with a `remote` positional (`:623`);
  - `root = parents[2]` (`:701`) and `load_remote` for every subcommand (`:703`);
  - `transport.Transport(remote)` (`:704`);
  - `PolicyError` → `POLICY:` exit 3 (`:766-768`).

**`scripts/remote/transport.py`**

- `Transport.ssh` runs `ssh -o BatchMode=yes -o GSSAPIAuthentication=yes <user>@<host> <command>`
  (`:31-35`).
- `rsync_from`/`rsync_to` (`:37-49`).
- The runner can be injected (`:20-22`).

**`scripts/remote/jobs.py`**

- `STATES = ("queued", "running", "done", "failed")` (`:14`).
- `active_count` counts queued and running (`:36-40`).
- `record_submit` writes the entry fields (`:43-61`). There is no scheduler field.
- `set_state` raises `ValueError` for an unknown state and `KeyError` for an unknown id (`:64-71`).

**Config, skill and project settings**

- `config/remotes.example.yml:10`:
  `scheduler: pbs        # the only supported value; any other is refused`.
- `config/remotes.example.yml:13`: `allowed_ops` lists `qsub, qstat`.
- `skills/remote-exec/SKILL.md`:
  - `:3` describes "PBS jobs";
  - `:18` says "scheduler (only `pbs` is accepted)";
  - `:47-59` is the Snakemake gate in prose;
  - `:96-97` "never qdel (not an allowed op) or resubmit a duplicate".
- `pyproject.toml:72-75`: `pythonpath = ["scripts"]` (tests import `from remote import …`).
- `mkdocs.yml:54-61` is the top-level nav; there is no remote page. `docs/` has no remote page
  (`git ls-tree docs`). `AGENTS.md:97-103` is rule 11.

**Tests on `M`**

- `tests/test_remote_cli.py` has 35 test functions (540 lines).
- `tests/test_remote_policy.py` has 12, including the M-A scheduler test at `:176-185`, which is
  parametrised over `slurm` and `3` and matches `is not supported \(supported: pbs\)`.
- `tests/test_remote_jobs.py` has 4, including `test_active_count_and_set_state` at `:29-39`.
- `tests/test_remote_transport.py` has 2.
- Existing PBS assertions are mostly substring checks (for example `test_remote_cli.py:315-316`);
  only `logs` (`:427-429`) and `pull`/`push` assert full strings. That is why T1 adds full-string
  golden tests first.

### 4.2 Today's PBS strings (probe on `M`, fake transport; these become T1's golden values)

| Case | Exact command (last argv element) |
|---|---|
| submit defaults (dir `/storage/x/repo`, `run.sh`, walltime 01:00:00, cpus 1, mem 4, queue default) | `cd /storage/x/repo && qsub -N run -q default -l walltime=01:00:00 -l select=1:ncpus=1:mem=4gb run.sh` |
| clamp (48h/99 cpus/999 GB → 24h/16/64) + `gpus=1` + `name=paper1-dag` | `cd /storage/x/repo && qsub -N paper1-dag -q default -l walltime=24:00:00 -l select=1:ncpus=16:mem=64gb:ngpus=1 run.sh` (stdout first prints three `CLAMPED:` lines) |
| scratch 60 GB `scratch_ssd` + env `SUBJECT=105216`, `RESULTS_ROOT=/storage/x/results`, cpus 4, mem 16 | `cd /storage/x/repo && qsub -N run -q default -l walltime=01:00:00 -l select=1:ncpus=4:mem=16gb:scratch_ssd=60gb -v SUBJECT=105216,RESULTS_ROOT=/storage/x/results run.sh` |
| `submit_dir=/storage/x/logs` | `cd /storage/x/logs && qsub -N run -q default -l walltime=01:00:00 -l select=1:ncpus=1:mem=4gb /storage/x/repo/run.sh` |
| status | `qstat -xf 101.meta-pbs` |
| logs (entry dir `/storage/x/repo`, job name `run`) | `cd /storage/x/repo && cat run.o101 run.e101 2>/dev/null` |

The other outputs to keep:
- `SUBMITTED: 101.meta-pbs attempt=1`;
- `STATE: running exit=-`;
- the `submit_dir` ledger entry, which has `dir=/storage/x/logs`, `code_dir=/storage/x/repo` and
  `job_name=run`.

## 5. Design (decided)

### 5.1 Package layout and imports

- `scripts/remote/errors.py` defines `class PolicyError(Exception)`. `policy.py` re-exports it
  (`from remote.errors import PolicyError`), so `policy.PolicyError` keeps its identity for every
  existing test and caller. Adapters import `remote.errors`, never `remote.policy`, so the registry
  can be imported by `policy.py` without a cycle.
- `scripts/remote/schedulers/__init__.py` defines:
  - `REGISTRY: dict[str, Scheduler]`, mapping name → adapter instance;
  - `get(name)`;
  - `names()`.
- `base.py` holds the dataclasses and the shared helpers.

### 5.2 Protocol (`schedulers/base.py`)

```
JobSpec(name, dir, script, walltime, cpus, mem_gb, gpus, queue, scratch_type, scratch_gb, env: dict)
        # every field already validated/clamped by policy.py; script is as submitted
JobRef(job_id, dir, job_name)              # from the ledger entry
JobStatus(state, exit_code: str, details: dict, parse_error: bool = False)
        # state in STATES = queued|running|done|failed|cancelled|unknown; exit_code "-" when absent

class Scheduler:
    name: str
    maturity: "certified" | "experimental"
    transport: "ssh" | "local"
    concrete_ops: dict[str, str]           # abstract -> concrete alias, e.g. {"submit": "qsub"}
    array_support: bool                    # recorded only; no array CLI in M-E (M-D3)
    labels: dict[str, str]                 # {"submit": "QSUB_FAILED", "status": "QSTAT_FAILED"}
    detail_fields: tuple[str, ...]         # allow-listed --details fields
    needs_kerberos: bool
    def validate_remote(entry: dict) -> None          # extra per-adapter config checks
    def resource_args(spec: JobSpec) -> str
    def submit_command(spec: JobSpec) -> str
    def notices(spec: JobSpec) -> list[str]           # e.g. unenforced resources
    def parse_submit(stdout: str) -> str              # job id; raises SubmitParseError
    def status_command(job: JobRef) -> str
    def parse_status(stdout: str, job: JobRef) -> JobStatus
    def cancel_command(job: JobRef) -> str
    def log_paths(job: JobRef) -> tuple[str, str]
    def logs_command(job: JobRef) -> str
```

Invariants (asserted by the contract suite):

- (a) An adapter composes only fixed text plus `JobSpec`/`JobRef` fields that `policy.py` already
  validated, each passed through `shlex.quote` where it is a shell word.
- (b) `parse_status` never returns `done` unless the scheduler output positively says the job
  finished with exit code 0.
- (c) Unparseable output gives `parse_error=True`.
- (d) Job ids that `parse_submit` returns for slurm, ssh and local must pass `policy.check_token`.
  PBS keeps today's "last stdout line" (`remote.py:454-458`), byte-for-byte; `check_token` still
  refuses an unsafe id later, at `status`/`logs`.

### 5.3 Policy (`policy.py`)

- `SCHEDULERS` becomes `tuple(sorted(schedulers.REGISTRY))`, so the name is kept for callers.
- In `load_remote`:
  1. The value must be a `str` in `REGISTRY`, else
     `PolicyError("scheduler 'X' is not supported (supported: local, pbs, slurm, ssh)")`. This list
     is the T2 state; in T1 it is `(supported: pbs)`. The "(M-E)" suffix is dropped in T1.
  2. If the adapter is `experimental` and the entry does not have `allow_experimental_scheduler`
     exactly equal to `True`, raise
     `PolicyError("scheduler 'slurm' is experimental (not yet run against a real cluster); set allow_experimental_scheduler: true on remote '<name>' in config/remotes.yml to use it")`.
     A non-bool value for that key is refused.
  3. Then `adapter.validate_remote(entry)`. For `local`: `host` must be `localhost` and `auth` must
     be `none`. For the others: nothing new (M-E-D3).
- `Remote` gains a defaulted field `allow_experimental_scheduler: bool = False`, so existing
  `Remote(...)` constructions in tests stay valid.
- New `check_scheduler_op(remote, abstract)`:
  - `abstract` must be one of `submit`, `status`, `cancel`, `logs`;
  - it passes if `abstract` is in `allowed_ops`, or if the adapter's concrete alias is;
  - otherwise it raises
    `PolicyError("operation 'submit' (qsub) not in allowed_ops for remote 'meta' (allowed: …)")`.
  - Aliases:

    | Adapter | submit | status | cancel | logs |
    |---|---|---|---|---|
    | PBS | `qsub` | `qstat` | `qdel` | `logs` |
    | Slurm | `sbatch` | `sacct` | `scancel` | `logs` |
    | ssh/local | `submit` (no alias) | `status` | `cancel` | `logs` |

  - `check_op` itself is unchanged; `check`, `fetch`, `rsync`, `git-*` and others still use it.
- There is **no** environment variable or option that points `remote.py` at another `remotes.yml`.
  The root stays `Path(__file__).resolve().parents[2]` (`remote.py:701`). An override would let an
  agent swap in its own policy. Tests call `policy.load_remote(tmp_root, …)` and the `cmd_*`
  functions directly.

### 5.4 Ledger (`jobs.py`)

- `STATES = ("queued", "running", "done", "failed", "cancelled", "unknown")`.
- `active_count` counts `queued`, `running` and `unknown` (M-E-D6).
- `record_submit(..., scheduler="pbs", kind="job")` writes `scheduler` and, for dry runs only,
  `kind: dryrun`. The defaulted parameters keep today's callers valid, and every existing key is
  still written.
- New `find(ledger, job_id)` returns the first entry with that id (today's `cmd_logs` semantics,
  `remote.py:534`), or `None`.
- New `entry_scheduler(entry)` returns `entry.get("scheduler", "pbs")`.

### 5.5 Adapters

**PBS** (`pbs.py`, certified, transport `ssh`, `needs_kerberos=True`, `array_support=True`)

- `resource_args`/`submit_command` reproduce §4.2 exactly, including the `select=` order
  (cpus, mem, scratch, ngpus), `-v` env joined with commas and quoted once, and the `submit_dir`
  absolute-script rule.
- `status_command` = `qstat -xf <id>`. `parse_status` keeps today's regexes and mapping
  (`remote.py:497-506`), including the letter → `queued` default (M-E-D7).
- `log_paths` = `<job_name>.o<seq>`, `<job_name>.e<seq>`, where seq = id before the first `.`.
- `logs_command` = today's `cd <dir> && cat … 2>/dev/null`. `cancel_command` = `qdel <id>`.
- `labels` are `QSUB_FAILED`/`QSTAT_FAILED`. `detail_fields` are today's nine (`remote.py:513-523`).

**Slurm** (`slurm.py`, experimental, transport `ssh`, `needs_kerberos=True`, `array_support=True`)

- `submit_command`:
  `cd <dir> && sbatch --parsable -J <name> -p <queue> -t <walltime> -N 1 -n 1 -c <cpus> --mem=<mem>G[ --gres=gpu:<n>][ --tmp=<gb>G] -o slurm-%j.out -e slurm-%j.err[ --export=ALL,<K=V,…>] <script>`.
  The `--export` value is one quoted word, as for PBS `-v`.
- Scratch: if `scratch_gb > 0` and `scratch_type != "tmp"`, raise
  `PolicyError("slurm supports scratch_type 'tmp' only (--tmp)")` (M-E-D8).
- `parse_submit`: last non-empty line, the part before the first `;`, which must be digits;
  otherwise it raises `SubmitParseError`. The caller prints `SBATCH_FAILED:` and exits 1, and
  nothing is recorded.
- `status_command`:
  `sacct -X -n -P -j <id> --format=JobID,State,ExitCode,Elapsed; printf '%s\n' '#squeue'; squeue -h -j <id> -o '%i|%T' 2>/dev/null`.
- `parse_status`:
  - It uses the first sacct row whose `JobID` equals the id. If there is none, it uses the squeue
    row after `#squeue`. If there is neither, the state is `unknown` without a parse error (the job
    is not yet or no longer visible). Output without the `#squeue` marker, or rows with the wrong
    field count, give `parse_error`.
  - State words (only the first word counts, so `CANCELLED by 123` → `cancelled`):

    | Slurm state | ScieFlow state |
    |---|---|
    | PENDING, CONFIGURING, REQUEUED, RESIZING | queued |
    | RUNNING, COMPLETING, SUSPENDED, STOPPED | running |
    | COMPLETED with `ExitCode` `0:0` | done |
    | COMPLETED with any other exit code | failed |
    | FAILED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL, BOOT_FAIL, DEADLINE, PREEMPTED | failed |
    | CANCELLED | cancelled |
    | anything else | unknown |

  - The exit code is the part of `ExitCode` before the `:`. `detail_fields` = (`Elapsed`,).
- `cancel_command` = `scancel <id>`. `log_paths` = `slurm-<id>.out`, `slurm-<id>.err`.
  `logs_command` = `cd <dir> && cat slurm-<id>.out slurm-<id>.err 2>/dev/null`.
- Labels: `SBATCH_FAILED`/`SACCT_FAILED`.

**Plain SSH** (`ssh.py`, experimental, transport `ssh`, `needs_kerberos=True`, `array_support=False`)

- `submit_command`, where `W` is a constant wrapper:
  `cd <dir> && [env <K=V> … ]setsid nohup bash -c <W> <name> <script> <seconds> </dev/null >/dev/null 2>&1 & echo "$!.$(date +%s)"`
  - `W` = `exec >"$0.o$$" 2>"$0.e$$"; timeout "$2" bash "$1"; rc=$?; printf '%s\n' "$rc" >"$0.x$$.tmp" && mv "$0.x$$.tmp" "$0.x$$"`,
    with `$0`=name, `$1`=script, `$2`=walltime seconds. It is passed as one `shlex.quote`d word.
  - `nohup` and `setsid` exec in place in a non-interactive shell, so `$!` is the wrapper's pid and
    its process-group id.
  - Job id = `<pid>.<epoch>`.
- `notices`: `NOTICE: ssh scheduler does not enforce cpus/mem/gpus/queue; walltime is enforced with timeout`.
  Scratch > 0 → `PolicyError("ssh scheduler has no scratch")`. The queue still has to pass
  `check_queue`.
- `parse_submit`: the last line must match `^\d+\.\d+$`.
- `status_command`:
  `cd <dir> && if test -f <name>.x<pid>; then printf 'EXIT %s\n' "$(cat <name>.x<pid>)"; elif kill -0 <pid> 2>/dev/null; then echo RUNNING; elif test -f <name>.c<pid>; then echo CANCELLED; else echo GONE; fi`.
- `parse_status`:

  | Output | State |
  |---|---|
  | `EXIT 0` | done |
  | `EXIT <n>`, n ≠ 0 (124 means walltime) | failed, exit n |
  | `RUNNING` | running |
  | `CANCELLED` | cancelled |
  | `GONE` | failed, exit `-` (the process died without writing an exit file) |
  | anything else | `parse_error` |

- `cancel_command` = `cd <dir> && : > <name>.c<pid> && kill -TERM -- -<pid>`.
- `log_paths` = `<name>.o<pid>`, `<name>.e<pid>`; `logs_command` has the same shape as PBS.
- Labels: `SUBMIT_FAILED`/`STATUS_FAILED`.

**Local** (`local.py`, experimental, transport `local`, `needs_kerberos=False`)

- Same commands and parsing as Plain SSH (it subclasses it), plus `validate_remote` (host
  `localhost`, auth `none`).

If the local end-to-end test (T2) shows that a wrapper detail does not work as designed (for
example pid identity under `setsid`), the implementer may adjust the ssh/local strings. They must
then record the reason and the final golden strings in the T2 result; T2R checks that the change
keeps invariants (a)–(d).

### 5.6 Transport

- `transport.for_remote(remote, runner=None)` returns `LocalTransport` when the adapter's transport
  is `local`, and `Transport` otherwise. `main` uses it instead of `Transport(remote)` (`remote.py:704`).
- `LocalTransport` methods:
  - `ssh(command)` → `runner(["bash", "-c", command])`;
  - `local(argv)` → unchanged;
  - `rsync_from(src, dest)` → `["rsync", "-az", src, dest]`;
  - `rsync_to` → the same flags as `Transport.rsync_to`, with a local destination;
  - `target` → `"localhost"`.
- `Transport` itself is unchanged; `test_remote_transport.py` still passes.

### 5.7 `remote.py`

- `cmd_submit`:
  - Keeps the same check order. The first line becomes `check_scheduler_op(remote, "submit")`.
  - It builds a `JobSpec` after clamping. Before SSH it calls `adapter.resource_args`, so a scratch
    refusal happens with no SSH, and prints `adapter.notices`.
  - It runs `adapter.submit_command`, parses with `adapter.parse_submit` (failure →
    `<labels.submit>: …` exit 1, no ledger), and records `scheduler=adapter.name`.
  - Its signature only gains a defaulted `after_dryrun=False`.
- `cmd_status`:
  - `check_scheduler_op(…, "status")`, then `check_token(job_id)`.
  - Ledger lookup: a missing entry → `UNKNOWN_JOB` exit 1 with no SSH (M-E-D5). An entry with a
    different scheduler → `PolicyError` (M-E-D6).
  - `JobRef` comes from the entry. PBS needs only the id, so `dir`/`job_name` may be absent for
    PBS; ssh and local require them, else `PolicyError("ledger entry lacks dir/job_name")`.
  - Then the SSH call, the credentials check (unchanged), and `adapter.parse_status`:
    - `parse_error` → `UNPARSEABLE:` exit 1, ledger untouched;
    - otherwise `set_state` and `STATE: <state> exit=<code>`.
  - `--details` prints `adapter.detail_fields` (PBS output unchanged).
- `cmd_logs`: `check_scheduler_op(…, "logs")`, the same ledger and token checks as today, and the
  `adapter.logs_command`; the stem and seq checks apply to every adapter.
- `cmd_check`: for `needs_kerberos=False` it prints `OK` and returns 0 without `klist` or SSH.
- New subcommands (`main`):
  - `schedulers [--json]`. It runs **before** `load_remote` and needs no `config/remotes.yml`. It
    prints one line per adapter: `name maturity transport submit=… status=… cancel=… logs=…`.
  - `dryrun <remote> <dir> <script> --workspace WS --task T [--walltime --cpus --mem-gb --queue --name --submit-dir --env]`:
    - it calls `cmd_submit` with gpus 0, scratch none, and `SCIEFLOW_DRYRUN=1` added to the env;
    - if the caller already passes `SCIEFLOW_DRYRUN`, it raises `PolicyError`;
    - the ledger records `kind: dryrun`;
    - the op checked is `submit`.
  - `dag <remote> <job_id> --workspace WS`:
    - op `logs`; the entry must be `kind: dryrun` with ledger state `done` (the caller runs
      `status` first), else `NOT_A_DONE_DRYRUN` exit 1 with no transport call; a scheduler mismatch
      is a `PolicyError` (exit 3);
    - it reads the logs through `adapter.logs_command`, refusing output over 10 MiB
      (`DAG_TOO_LARGE`, exit 1);
    - it writes `WS/remote/dag-<task>.txt` atomically (tmp file + `os.replace`) and stores
      `dag_sha256` on the entry;
    - it prints `DAG_RECORDED: <path> sha256=<hex>`.
  - `submit --after-dryrun`:
    - it requires that the task's latest `kind: dryrun` entry is `done` with a `dag_sha256`, and
      that `WS/remote/dag-<task>.txt` currently hashes to that value;
    - otherwise `NO_DAG: …` exit 1, checked before any SSH.
- Exit codes are unchanged (0/1/2/3/4). `POLICY:` exit 3 covers every new refusal that comes from
  policy or ledger integrity.

### 5.8 Docs and skill

- New `docs/remote.md`, linked as `- Remote execution: remote.md` after `Sandbox` in the
  `mkdocs.yml` nav. It covers:
  - the adapter table (name, maturity, transport, op aliases);
  - the config keys, including `allow_experimental_scheduler`;
  - the abstract ops;
  - the dry-run gate;
  - the six states;
  - the honest limits: Slurm/SSH never run live; SSH does not enforce cpus/mem/gpus; pid reuse;
    Kerberos only; no cancel command; PBS letter default.
- `skills/remote-exec/SKILL.md`:
  - description: "jobs on a configured remote (PBS certified; Slurm/SSH experimental)";
  - setup: scheduler values and the opt-in;
  - Snakemake gate: `dryrun` → `status` → `dag` → `submit --after-dryrun`;
  - "never cancel (no cancel command) or resubmit a duplicate".
- `config/remotes.example.yml`:
  - the `:10` comment becomes `# pbs (certified); slurm, ssh, local are experimental and need allow_experimental_scheduler: true (docs/remote.md)`;
  - the `:13` comment mentions the abstract op names;
  - a commented-out Slurm remote example is added.
  - The file must still load with `load_remote` when copied as `remotes.yml` (tested).

## 6. Tickets

Staffing:
- Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`).
- Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`).
- Provider: anthropic.
- A usage limit is a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no SSH to any real host, no push/PR, no
credentials, no `INFLIGHT` edit, no DD1-sealed file, no `config/sandbox.yml`.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from `$WT=RUN/scratch/m-e-wt`:
  `cd $WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=$WT/src:$WT:$WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp RUN/scratch/m-e/pt <paths>`.
  Once per card, run the import-origin check first:
  `env PYTHONPATH=$WT/src:$WT …/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`.
  Its output must start with `$WT/src/`. pytest's own `pythonpath = ["scripts"]` resolves
  `remote` to `$WT/scripts`; each card confirms this once with
  `-c 'import remote,os;print(os.path.realpath(remote.__file__))'` run from `$WT`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-e/junit RUN/tools/g6_pytest.sh $WT <head-sha> m-e-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path.
- **Sealed probes:**
  - `… g6_pytest.sh $WT <head> m-e-sealed -- tests/probes`;
  - the explicit sealed file `tests/probes/test_k_task8_binding.py`, run by path as M-A T6 did
    (`scratch/m-a/t6-sealed.sh`; 201 passed there).
- **Load-flake nodes** (one rerun, both results recorded): the four nodes named in the installer's
  standing rules. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-e/executions.jsonl` and states its RED and GREEN evidence in its result.

### t0 — worktree, plan copy, green base (Haiku)

1. Section 2: fetch, M-A ancestor check, create the worktree and branch at `base`.
2. **Green-base gate on `base`** (HEAD = base, clean tree):
   - full default suite: **0 failed, 0 errors**;
   - sealed probes: 0 failed.
   - The expected counts on this tree are those of M-A T6 r3 on the identical tree `be99db2`:
     4806 passed, 22 skipped; sealed 201 passed. These are expectations only; t0 records what it
     measures.
   - Only the load-flake rule may clear a failure. Otherwise the verdict is `BASE_RED`, with the
     failing node ids and junit paths, and nothing else is done.
3. Re-verify every file:line cite of section 4 on `base`. Write `reviews/m-e-t0-baseline.md` with
   each cite marked "still present", "moved (new line)" or "changed". Re-run the §4.2 strings with
   the same probe pattern, in `RUN/scratch/m-e/probe/`.
4. Copy `reviews/m-e-plan.md` verbatim to `docs/superpowers/plans/2026-10-25-m-e-scheduler-seam.md`
   and commit it (`docs(plans): M-E scheduler seam implementation plan`). This is the only change.
5. Result: `head` (the plan commit), `base`, the baseline counts and `inflight_guard_exit`.
   Verdicts: `DONE` | `BASE_RED`.

### t1 — golden PBS, errors module, registry, PBS adapter, policy, ledger (Haiku) → t1r (Sonnet)

Files:
- `scripts/remote/errors.py`;
- `scripts/remote/schedulers/{__init__,base,pbs}.py`;
- `scripts/remote/{policy,remote,jobs}.py`;
- `tests/test_remote_pbs_golden.py` (new);
- `tests/test_remote_schedulers.py` (new);
- `tests/test_remote_jobs.py` (additions);
- `tests/test_remote_policy.py` (only `:176-185`, which must still pass with the shortened message).

Order (binding):

1. **Commit A, characterisation only.** Write `tests/test_remote_pbs_golden.py` with the full-string
   cases of §4.2, plus:
   - the `SUBMITTED`/`STATE`/`CLAMPED` stdout lines;
   - the `submit_dir` ledger entry fields;
   - an empty-stdout `QSUB_FAILED` case;
   - an `UNPARSEABLE` case.

   Run it on the unmodified code (dirty tree, then committed): it must pass. Commit it and run it
   once more with `g6_pytest.sh` on that clean commit; that junit is the evidence that the strings
   are today's.
2. **RED → GREEN tests**, written and shown failing before the code:
   1. `remote.schedulers.REGISTRY["pbs"]` exists, with maturity `certified`, transport `ssh` and
      aliases `qsub`/`qstat`/`qdel`/`logs`.
   2. `policy.PolicyError is remote.errors.PolicyError`.
   3. `check_scheduler_op`:
      - `submit` passes with `allowed_ops: [qsub]` and with `[submit]`;
      - it fails with `[qstat]`, and the message names `'submit' (qsub)`;
      - `status` ↔ `qstat`/`status`;
      - an unknown abstract op is refused.
   4. An experimental fake adapter, registered by monkeypatching `REGISTRY`:
      - refused without the opt-in (exact message, §5.3);
      - loaded with `allow_experimental_scheduler: true`;
      - `allow_experimental_scheduler: "yes"` is refused.
   5. `cmd_submit`/`cmd_status`/`cmd_logs` call the adapter: a recording fake adapter shows exactly
      one `submit_command`/`status_command`/`logs_command` call. PBS output is unchanged (the golden
      file stays green).
   6. Status of an id not in the ledger → `UNKNOWN_JOB`, exit 1, zero transport calls (M-E-D5).
   7. A submit records `scheduler: pbs`.
      - An entry with `scheduler: slurm` → status, logs and dag are refused with exit 3 and zero
        transport calls.
      - A legacy entry without the key works as pbs.
   8. `jobs`:
      - `set_state` accepts `cancelled` and `unknown`;
      - `active_count` counts `unknown`;
      - `find` returns the first match.
   9. Unchanged: all 35 `test_remote_cli.py` tests, `test_remote_transport.py`, and the rest of
      `test_remote_policy.py`/`test_remote_jobs.py`, with no assertion removed.
3. **Commit B** (or more): the implementation. Then the INFLIGHT guard.

t1r checklist (Sonnet):
- Golden commit A predates the refactor and its junit is real.
- Every golden string is unchanged at the tip.
- Authorise-before-SSH order holds in submit, status and logs: the op, token, dir and ledger checks
  all come before the first transport call (a test proves it per command).
- No user-supplied text reaches a command unquoted.
- The new refusals cannot be bypassed through ledger edits; the ledger is agent-writable, so its
  fields are re-validated.
- No root/config override exists.
- The `test_remote_cli.py`/`test_remote_transport.py` diff only adds lines.
- `INFLIGHT` guard exit 0.

### t2 — Slurm, SSH, local adapters, LocalTransport, fixtures, contract suite, local end-to-end (Haiku) → t2r (Sonnet)

Files:
- `scripts/remote/schedulers/{slurm,ssh,local}.py`;
- `scripts/remote/schedulers/__init__.py` (register the three adapters);
- `scripts/remote/transport.py`;
- `scripts/remote/remote.py` (`cmd_check` local path; `for_remote` in `main`);
- `tests/fixtures/scheduler/{README.md,pbs/*.txt,slurm/*.txt,ssh/*.txt}`;
- `tests/test_remote_slurm.py`, `tests/test_remote_ssh.py`, `tests/test_remote_scheduler_contract.py`,
  `tests/test_remote_local_e2e.py` (all new);
- `tests/test_remote_policy.py` (scheduler test only).

Fixtures:
- **PBS:** `qstat -xf` texts for Q, R, F with exit 0, F with exit 1, F without `Exit_status`, an
  unexpected letter, and garbage.
- **Slurm:** `sacct`/`squeue` combinations for every state row of §5.5, `CANCELLED by 123`,
  `COMPLETED 1:0`, empty sacct + squeue PENDING, both empty, missing marker, wrong field count; and
  `sbatch --parsable` outputs `12345`, `12345;cluster` and garbage.
- **SSH:** `EXIT 0`, `EXIT 3`, `EXIT 124`, `RUNNING`, `CANCELLED`, `GONE`, garbage.
- The README states the provenance of each file (M-E-D9): synthetic, transcribed from the existing
  test layout or the public documentation (Slurm `sacct`/`squeue` man pages), not a live capture.

RED → GREEN:

1. **Slurm golden strings:** submit (defaults, gpus, `--tmp` with type `tmp`, env, name,
   `submit_dir`), status, cancel, logs.
   - scratch type `scratch_ssd` → `PolicyError` before SSH;
   - `parse_submit` handles `12345;cluster` → `12345`;
   - garbage → `SBATCH_FAILED` exit 1, no ledger row.
2. **Slurm `parse_status`** over every fixture, with the expected state and exit code.
3. **SSH golden strings:** submit with and without env, walltime → seconds, status, cancel, logs.
   - scratch → refused;
   - the `NOTICE:` line is printed;
   - `parse_submit` accepts only `<digits>.<digits>`.
4. **Policy:**
   - `slurm`/`ssh`/`local` without the opt-in → the experimental refusal (exact text);
   - each with the opt-in → loads;
   - `local` with host ≠ `localhost` or auth ≠ `none` → refused;
   - `sge` and `3` → `is not supported (supported: local, pbs, slurm, ssh)`.

   The M-A test at `tests/test_remote_policy.py:176-185` is replaced by these cases. That is the
   only assertion of an existing test that changes; the reason is that `slurm` is now supported as
   experimental (D19).
5. **Contract suite**, parametrised over all four adapters, with a stub transport returning fixture
   stdout:
   - submit → status queued → running → done;
   - failed;
   - cancelled (the cancel command string is token-safe and quoted);
   - a job not in the ledger → `UNKNOWN_JOB`;
   - malformed output → `UNPARSEABLE`, ledger unchanged, **never** `done`;
   - `log_paths` are token-safe;
   - for every adapter, metacharacters in dir, script, task, name, env or job id are refused before
     the first transport call;
   - the concurrency and attempt ceilings behave identically for every adapter.
6. **`transport.for_remote`** returns `LocalTransport` for `local`. Its argv is golden
   (`["bash","-c",cmd]`, local rsync args).
7. **Local end-to-end** (real subprocesses; bash, setsid, nohup, timeout and rsync are present on
   ubuntu runners; not marked slow or live, so it runs in the default suite and in the future CI
   gate). Setup: an `allowed_dirs` root under `tmp_path`, a workspace under `tmp_path`, and
   `load_remote` on a tmp root with a `local` remote plus the opt-in. Cases:
   - a script writing `results/out.txt` and printing to stdout: `cmd_submit` → poll `cmd_status`
     (bounded: at most 10 s at 0.1 s intervals) → `done exit=0`; `cmd_logs` shows the stdout;
     `cmd_fetch` copies `results/` into the workspace;
   - `exit 3` → `failed exit=3`;
   - `sleep 30` plus the adapter's `cancel_command` run through the transport → `cancelled`
     within the bound, and the process group is gone;
   - walltime `00:00:01` with `sleep 30` → `failed exit=124`.

   No test leaves a background process running; a fixture kills its process groups on teardown.

t2r checklist:
- Each adapter's strings match §5.5, or a recorded deviation keeps invariants (a)–(d).
- Fixture provenance is honest.
- Experimental refusal and `local` constraints cannot be bypassed.
- `LocalTransport` adds no capability beyond the caller's own shell, and no root override exists.
- The e2e test cleans up its processes.
- The only changed existing assertion is the one named above.
- `INFLIGHT` guard exit 0.

### t3 — `schedulers`, `dryrun`, `dag`, `--after-dryrun`, docs, skill, example config (Haiku) → t3r (Sonnet)

Files:
- `scripts/remote/remote.py`;
- `scripts/remote/jobs.py` (`kind` and `dag_sha256` only);
- `docs/remote.md` (new);
- `mkdocs.yml` (one nav line);
- `skills/remote-exec/SKILL.md`;
- `config/remotes.example.yml`;
- `tests/test_remote_dryrun.py`, `tests/test_docs_remote.py` (new).

RED → GREEN:

1. `main(["schedulers"])` and `main(["schedulers","--json"])` list all four adapters with maturity,
   transport and aliases. They work in a tree without `config/remotes.yml`: a test monkeypatches
   `policy.load_remote` to raise and proves it is never called.
2. `dryrun`:
   - the composed submit has gpus 0, no scratch and `SCIEFLOW_DRYRUN=1` in the env, for PBS (`-v`)
     and Slurm (`--export`);
   - the ledger row has `kind: dryrun`, and it counts toward the task's attempts;
   - a caller-supplied `SCIEFLOW_DRYRUN` → exit 3;
   - with `allowed_ops` lacking submit and its alias → exit 3.
3. `dag`:
   - refused with no transport call for a non-dryrun or non-`done` entry (`NOT_A_DONE_DRYRUN`,
     exit 1) and for a scheduler mismatch (exit 3); refused for output over 10 MiB
     (`DAG_TOO_LARGE`, exit 1, no file written);
   - otherwise it writes `WS/remote/dag-<task>.txt` with exactly the logs output (atomic write),
     stores `dag_sha256` and prints `DAG_RECORDED`.
4. `submit --after-dryrun`:
   - refused `NO_DAG` (exit 1, zero transport calls) without a done dry run, without a recorded
     DAG, or when the file has been edited (sha mismatch);
   - allowed after a valid `dag`.
5. Docs test (`tests/test_docs_remote.py`):
   - every `REGISTRY` name appears in the `docs/remote.md` adapter table with its maturity;
   - `mkdocs.yml` nav lists `remote.md`;
   - the skill mentions `dryrun`, `dag`, `--after-dryrun` and `allow_experimental_scheduler`, and
     no longer says "only `pbs` is accepted";
   - `config/remotes.example.yml`, copied to a tmp root as `config/remotes.yml`, loads with
     `load_remote(…, "meta")`.
6. The whole `tests/test_remote_*.py` set is green.

t3r checklist:
- `dag` cannot write outside `WS/remote/`; the task name is token-checked before the path is built.
- `--after-dryrun` checks the hash of the file on disk, not only the ledger.
- `SCIEFLOW_DRYRUN` cannot be forged into a real submit that is recorded as a dry run.
- Docs and skill match the code: nothing claims Slurm/SSH were tested live, and no cancel command
  is documented.
- `INFLIGHT` guard exit 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip:
- **Full default suite:** 0 failed, 0 errors. Report absolute counts and the passed/skipped delta
  against t0, each difference explained by added tests.
- **Sealed probes** as in t0: 0 failed.
- **`mkdocs build --strict`** into `RUN/scratch/m-e/site`, if mkdocs is in the venv; otherwise
  record "not available".
- **`INFLIGHT` guard** over `base..tip`: exit 0.
- **History:** `git log base..tip` is linear, with no merge commits and at least one commit per
  ticket.
- **Existing-test integrity:** `git diff base..tip -- tests/test_remote_cli.py tests/test_remote_transport.py`
  has no `-` lines.

Write `RUN/scratch/m-e/pr-body.md` in plain English:
- what M-E adds;
- the decisions M-E-D1..D10;
- the absolute test counts;
- the residuals (section 8);
- "merge with a merge commit".

Write `reviews/m-e-branch-report.md`. Any failure → verdict `RED` with the node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

This is a fresh card that did not review t1–t3 where staffing allows. It covers the whole branch
`base..tip`:

- **Spec:** every item of section 5 and every acceptance criterion of the M-E row:
  - PBS golden strings identical;
  - contract suite green for all adapters;
  - local end-to-end runs in the default suite;
  - Slurm refused without the opt-in;
  - docs.

  Decisions M-E-D1..D10 honoured.
- **Security:**
  - authorise-before-SSH;
  - quoting;
  - ledger re-validation;
  - no policy override;
  - the experimental gate;
  - the dry-run gate integrity;
  - no cancel CLI.
- **Evidence:**
  - re-run a memory-capped subset (`tests/test_remote_*.py`, `tests/test_docs_remote.py`);
  - check the golden commit A predates the refactor;
  - confirm the absolute counts reported by the `branch` step;
  - INFLIGHT guard.

Output: `reviews/m-e-final-review-r<round>.md`, with line 1 `VERDICT: PASS|CHANGES|FAIL`.
- PASS → the installer's `publish` step;
- CHANGES → `final_fix` (Haiku, new commits, and the full default suite re-run on the new tip),
  then back to `final`;
- FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-e-run.yml`, linear)

```
t0        Haiku   worktree + plan copy + green base     DONE→t1, BASE_RED→escalate
t1        Haiku   golden PBS + seam + PBS adapter       DONE→t1r
t1r       Sonnet  review                                PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                     DONE→t1r
t2/t2r/t2f        Slurm/SSH/local + contract + e2e      same shape → t3
t3/t3r/t3f        CLI dryrun/dag/schedulers + docs      same shape → branch
branch    Haiku   full suites + PR body                 DONE→final, RED→escalate
final     Sonnet  whole-branch review                   PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                     DONE→final
(publish, chain: added by tools/milestone_chain.py install)
```

- Caps: `max_cards: 45, bad_result_retries: 1, stall_minutes: 20, question_cards: 3`.
- Results go to `pipelines/results/m-e-run/<step>-r<round>.json`. Every step requires
  `verdict, summary, head, base`. Implementation, fix, branch and final cards also report
  `inflight_guard_exit`.

There are no protected edits, so the installer adds no `pe_*` step.

## 8. Risks and residuals (stated, not hidden)

- **Slurm and SSH have never run against a real system.** Their strings come from documentation,
  and their parsers are tested on synthetic fixtures. Both stay experimental, behind a human
  opt-in, until a human-run live smoke (D19).
- **There is no live Metacentrum `qstat -xf` capture in the fixtures.** The PBS parser is unchanged
  code, but its fixtures are synthetic.
- **The SSH/local status check can be fooled by pid reuse.** If the job dies without writing its
  exit file and the pid is reused before the next status call, `kill -0` reports `running`. That
  needs a crash plus pid reuse.
- **SSH/local do not enforce cpus, memory or gpus.** They say so on every submit.
- **Unexpected PBS letters map to `queued`** (M-E-D7), never to `done`.
- **Kerberos-only SSH** (M-E-D3): key-based clusters cannot be used yet.
- **No cancel command** (M-E-D2): a stuck job is still reported to you.
- **The Slurm `--export=ALL,…` setting passes the login environment to the job.** That is Slurm's
  default without `--export`, too; it is documented.
- **The dry-run gate is opt-in per submit** (`--after-dryrun`). The skill makes it mandatory for
  Snakemake tasks; code cannot know that a task is a Snakemake task.
- **The local end-to-end test relies on Linux `setsid`/`nohup` exec-in-place behaviour.** If a
  runner differs, the test fails loudly. That is acceptable; it is never skipped silently.

## 9. After M-E

Queue order (`continuity/milestones.yml`): the next entry is **M-B Data-protection model** (L: its
own spec and plan, then approval). M-D3 later replaces M-A's interim `backend: remote` refusal with
real remote campaigns over this seam: arrays, `kind: remote` jobs, a post-fetch hash manifest, and
possibly a `cancel` command (M-E-D2). Follow-ups surfaced here, not part of M-E:

- a key-based `auth:` mode for non-Kerberos clusters (M-E-D3);
- a live Slurm smoke run by the human (D19);
- a real `qstat -xf` capture to replace the synthetic PBS fixtures.
