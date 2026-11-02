# Remote campaigns

A campaign with `backend: remote` is an ordinary campaign that runs on a
remote host instead of this machine. Its parameters, scenarios, replicates and
analysis are the same. `scieflow experiment sweep` submits it, and
`scieflow experiment watch` collects it. The results (`results.json`,
`results.csv`, `metrics.json`, `manifest.json`) have the same shape as a local
campaign, so `compare`, `report`, `stats`, `tables`, `figures`, `verify` and
the budget work unchanged.

Remote access still goes only through `scripts/remote/remote.py`'s command
functions and its policy gate (AGENTS.md rule 11). Nothing in `scripts/remote/`
changed for this feature.

## Campaign keys

```yaml
backend: remote                 # local (default) | remote
remote:                         # required for backend: remote; refused otherwise
  name: meta                    # an entry of config/remotes.yml
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

- `backend` is `local` or `remote`. Any other value is refused and the message
  lists both.
- `remote` takes exactly the keys `name`, `dir` and `jobs`. `name` must match
  `[\w.\-]+`. `dir` must be absolute, normalized, have no `..` component and
  not be `/`. `jobs` is `per-run` (the default) or `array`. Unknown keys are
  refused by name.
- `resources` takes exactly the keys above. The defaults are the defaults of
  `remote.py submit`. A value that is not an integer where one is required
  (booleans are not integers) is refused by name. Unknown keys are refused by
  name.
- For a remote campaign the campaign name and every run id must match
  `[\w.\-]+`, because they become remote path components and ledger task
  names. A local campaign still accepts any run id.
- A local campaign's `campaign.yaml` is unchanged. A remote one records
  `backend`, `remote` and the normalized `resources`.

## Two shapes: per-run and array

- `jobs: per-run` (default): one scheduler job per run. The runs execute in
  parallel, up to the remote's `max_concurrent_jobs`. The rest are submitted as
  slots free up, on later `watch` passes.
- `jobs: array`: one scheduler job that runs all N runs one after another, in
  the order of the campaign. It works on every adapter, including `local`. Native
  scheduler arrays (`qsub -J`, `sbatch --array`) are not used. The job's
  walltime must cover all N runs.

A remote array job of N tasks counts as N runs. The budget records N once, when
the scheduler accepts the campaign's first job (per-run) or its one job
(array). Runs that are later not submitted stay counted, as a local sweep
counts runs that fail. Only the first accepted job records the spend, so a
later `watch` never records it again.

## Preflight: what is checked before anything is sent

Each check runs before any file is written, any budget is spent or any SSH
call is made. A refusal exits 1, and inside a run it also writes one
`job.refused` event with the rule id.

1. The campaign folder lies inside a run (rule `no-run`), and it does not exist
   yet (rule `campaign-exists`).
2. The local guards of a local sweep: stage seed, exhausted budget, study design
   and analysis guards, and the capacity of the budget for N runs.
3. The remote is loaded from `config/remotes.yml`. A missing file, an unknown
   remote, an unknown scheduler, or an experimental scheduler without its
   opt-in are refused with the `POLICY:` text.
4. The operations are allowed: `check`, `mkdir`, `rsync` and `fetch`, plus
   `submit` and `status` for the scheduler. All missing operations are listed in
   one message. The required ops are `check`, `mkdir`, `rsync`, `fetch`,
   `submit` (`qsub` on PBS) and `status` (`qstat` on PBS).
5. The folder, queue and job names pass the token rules. The generated
   `job.sh` passes the script rule.
6. The resources fit the remote's limits. A request above a limit is refused
   with rule `resources-over-limit`, which names each resource. It is not
   silently cut down: `remote.py` would print `CLAMPED:`, and a remote campaign
   never runs a clamped request.
7. `jobs: array` needs N at most 1000 (the replicate cap). Per-run has no N
   limit beyond the budget.
8. Data protection: the bundle and the input are checked against the L2 rules
   (see [Data protection](#data-protection)).
9. The input and the stage folder contain no symlink, and apptainer needs the
   `.sif` file.

Then the Kerberos ticket is checked once. If it is missing, the campaign is
refused with `NO_TICKET`. The message asks you to run `kinit`. Stop and ask the
user to run it. Nothing is written.

## sweep: stage, push and submit

`scieflow experiment sweep -c <campaign.yaml> --experiments-dir workspace/<slug>/experiments`
runs the preflight, writes the campaign folder and a bundle, pushes the bundle
to the remote, submits the jobs and exits. It does not wait. It prints:

```
submitted <k> of <N> runs on <remote> (<jobs>); collect with: scieflow experiment watch <dir>
```

Exit codes: `0` when at least one run was submitted, or when the concurrency limit held the
rest; `1` when the push failed or nothing could be submitted. Refusals name a
rule and write nothing.

Each submission gets its own remote folder `<dir>/<campaign>-<UTC stamp>`.
Nothing on the remote is ever overwritten. A campaign folder that already exists
is refused, because a remote campaign must not mix with an older one.

## watch: collect

`scieflow experiment watch <campaign_dir> [--once] [--interval S] [--max-minutes M] [--json]`

- `--once`: one pass, then exit. Exit `0` when the campaign is complete,
  exit `4` when it is still running, exit `1` when refused or failed.
- `--interval` (5 to 3600 seconds, default 60): seconds between passes.
- `--max-minutes` (1 to 1440, default 60): stop waiting after this many minutes.
  Then exit `4`, the same "still running" code.
- `--json`: print one JSON summary after the last pass, with the keys
  `campaign`, `campaign_id`, `state`, `complete` and `counts`.

Without `--once`, `watch` repeats its passes until the campaign is complete
(exit `0`) or the time limit is reached (exit `4`). Exit `4` means "wait": run
`watch` again later. It is not a failure.

One pass does this:

1. It re-validates `remote.json` before any SSH call.
2. It checks the Kerberos ticket. Without a ticket it exits 1 and changes
   nothing.
3. It pushes the bundle again if the campaign is still `staged`.
4. It asks the scheduler about each job. A job that is queued, running or
   unknown is still active. `unknown` never counts as finished.
5. For each finished job (done, failed or cancelled) it fetches the run folder
   into `runs/<run_id>/` and checks it (see the next section). It then writes
   the run's folder and results.
6. It submits the pending runs, as slots free up.
7. When every run is finalized or failed to submit, it writes `results.json`
   and `results.csv`, marks the campaign `complete`, closes the core job and
   exits `0`.

Each pass appends one line of counts to the core job's log.

## The post-fetch hash manifest

Each run's job writes `remote/SHA256SUMS` before its exit marker. The sums list
every file under `artifacts/` and `logs/`. After the fetch, ScieFlow checks
them. Every listed file must exist and match. Every fetched file must be listed.
No symlink may appear.

A run that fails the check is recorded as `failed` with the first reason below.
No metrics are computed from it.

| Reason | Meaning |
|---|---|
| `transfer-missing` | `remote/SHA256SUMS` is missing, or a listed file is missing |
| `transfer-hash` | a listed file does not match its recorded sha256 |
| `transfer-extra` | a file under `artifacts/` or `logs/` is not in the list |
| `fetched-symlink` | a symlink appeared in the fetched run folder |
| `no-exit-marker` | the run has no exit marker (for example, it was killed at its walltime) |
| `exit-nonzero` | the stage exited with a non-zero code |
| `no-output` | the stage exited 0 but `artifacts/<output_file>` is not a regular file |
| `job-cancelled` | the scheduler job was cancelled before the run finished |
| `submit-failed` | the run was never submitted; its folder records the decision |

A run that never ran still gets a run folder (`config.yaml` with status
`failed`, and a `manifest.json` with no artifacts). It stays in `results.json`,
so the report shows it as a failure. Failed runs are not resubmitted.

This check is an integrity check of the transfer, not a security proof. The
sums are written by the remote run itself, so they prove that the copy matches
what the remote wrote, not that the remote is honest.

## The campaign folder: remote.json and the run's remote folder

`remote.json` is written by the tool only, and it is read as untrusted. It
records the campaign id, the remote name, the scheduler, the remote folder, the
state (`staged`, `pushed`, `submitting`, `running`, `complete`), the bundle's
sha256, the resources, the core job id, the context, and one row per run with
its task name, job name, job id, state (`pending`, `submitted`,
`submit-failed`, `finalized`) and result. Every `watch` pass re-validates it
against `campaign.yaml`, the policy and the token rules before any SSH call. A
mismatch is refused with `remote-state-invalid`, which names the field.

Like `jobs.yml`, `remote.json` is writable by an agent. The validation catches
a changed run, a changed folder or a bad job id. It does not catch an agent that
rewrites the campaign folder consistently (see R3).

A run's folder keeps a `remote/` subfolder with the markers `started`,
`finished`, `exit`, `environment.txt` and `SHA256SUMS`. The job's log is in
`logs/`. Remote paths and host names are not written in the manifests.

## The core job and why it cannot be cancelled

A remote campaign opens one core job of kind `remote` in its run. It appears in
the run page and the timeline as `<campaign> on <remote>`, with the state
`running` and the note "unsandboxed". The job is not in ScieFlow's sandbox,
because the scheduler runs it.

Pressing Cancel on that row, or running `scieflow run cancel`, shows a refusal:
remote campaigns cannot be cancelled from ScieFlow yet (M-E-D2). The scheduler
job keeps running. Ask the user what to do with it. There is no cancel command
for remote jobs, and you must not cancel a remote job by hand or resubmit a
duplicate of a stuck job. A stuck job is reported.

## Data protection

The bundle and any input or reference that lies in another run are checked
against the L2 rules before they are sent. An L2 run, or an input taken from
an L2 run, goes only to a remote listed in `l2_remotes` in
`config/data-policy.yml`. Otherwise the campaign is refused with the message
of the data protection check.

Fetching from an `l2_remotes` remote into a run below L2 records a data warning
or is refused. Outputs of an L2 run are L2-derived, as for local runs. No metric
value, file content or path goes into an event.

## What the remote must provide

- `bash`, `coreutils` (`sha256sum`, `date`, `uname`), `findutils` (`find`,
  `xargs`) and, for container runs, `apptainer` on the compute node.
- Check them with `uv run scripts/remote/remote.py runtime-info <remote> <dir>`
  before the first campaign. A container run on a site that has only
  `singularity` fails every run with `exit-nonzero`.
- The allowed operations for the remote in `config/remotes.yml` must include
  `check`, `mkdir`, `rsync`, `fetch`, the submit operation (`qsub` on PBS,
  `submit` in the abstract names) and the status operation (`qstat` on PBS,
  `status`).

## Residuals and limits

R1 Never run against a real cluster. The PBS submit strings are the certified
ones and are pinned by golden tests. The job script, the `cd` into the remote
folder, `seq`, `find -print0` with `sort -z` and `xargs -0 -r`, and `sha256sum`
have run only on this host through the `local` adapter. A first real Metacentrum
campaign is a smoke run for a person to perform.

R2 Container runtime on the compute node. Container runs call `apptainer`. A
site with only `singularity`, or one that needs a module loaded, fails every run
with `exit-nonzero`. Check with `runtime-info` first. A `singularity` setting is
a follow-up.

R3 The transfer check is not tamper proof. `SHA256SUMS` is written by the
remote run, and `remote.json` and `jobs.yml` are agent-writable. An agent that
rewrites the campaign folder coherently is not caught.

R4 Packages. The package list is the local capture of the same `.sif` (identical
bytes, its sha256 is in the manifest). Without a local apptainer it is "not
captured". Direct runs on a remote record none.

R5 Array means sequential. `jobs: array` runs one task after another inside one
job, so its walltime must cover all N runs. Native arrays are a follow-up.

R6 No cancel, no resubmit, no clean-up. A stuck job is reported. Remote folders
accumulate until the user removes them.

R7 Clock and markers. `started` and `finished` come from the remote's clock. A
run killed at its walltime has no exit marker, so it is `no-exit-marker` even if
its outputs exist.

R8 Guard reach and the web row. Like the budget and design guards, the remote
guards apply only inside a run, and a remote campaign needs one. The run page
shows a Cancel button on the remote job; it answers with the refusal.

## Not done in this version

No native scheduler arrays, no cancel, no resubmit of failed runs, no remote
clean-up, no Snakemake stages inside a campaign, no remote package capture, no
`singularity` alias, no key-based SSH authentication, and no web panel beyond
the job row. A stage that needs a Snakemake dry run stays a `remote.py` workflow.
These limits are the design decisions M-D3-D2, M-D3-D7 and M-D3-D14 of the
milestone plan (`reviews/m-d3-plan.md`, section 1).
