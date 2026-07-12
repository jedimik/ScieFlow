# Design: agent rebalancing, dual-author drafting, and metacentrum remote execution

Date: 2026-07-12
Repos affected: ScieFlow (this repo), vendors/ResearchX (submodule, separate commits)

Three separable subsystems, in implementation order:

1. Agent rebalancing — demote Gemini to a support tier in both repos; Claude +
   ChatGPT 5.6 Sol (codex) become the only agents for comprehensive work.
2. Dual-author paper drafting with adversarial cross-review — ResearchX only.
3. Remote-execution module for metacentrum.cz — ScieFlow only.

---

## 1. Agent rebalancing (ScieFlow + ResearchX)

### Problem

ResearchX currently treats `claude`, `codex`, and `agy` (Gemini) as equal
peers in every fan-out and role assignment. Gemini is good at large-context
reading and web search but not at comprehensive reasoning tasks; those must go
to Claude and ChatGPT 5.6 Sol. ScieFlow's registry is claude-only (AGENTS.md
rule 10 forbids routing entirely).

### Changes — `config/agents.yml` (both repos)

- Add a `tier` field to every agent entry:
  - `claude` → `tier: primary`
  - `codex` → `tier: primary`, cmd pinned to
    `codex exec --sandbox workspace-write --model gpt-5.6-sol {prompt}`,
    `model: gpt-5.6-sol` (user-confirmed id; one-line edit if it changes).
  - `agy` → `tier: support`, `capabilities: [web-search, large-context]`.
- ScieFlow's registry gains `codex` and `agy` entries mirroring ResearchX
  (same tiers), so the coordinator can dispatch them per the new policy.

### Changes — routing policy (both AGENTS.md files)

Replace ScieFlow rule 10 ("claude-only, no model switching") and add the
equivalent rule to ResearchX:

- **Comprehensive tasks dispatch primary tier only**: synthesis,
  cross-review, drafting, gap reasoning, perspective debate, experiment
  campaigns, reviewer/submitter roles.
- **Support tier (agy) is allowed only for**:
  - literature-search fan-out (stays a peer there — well-scoped task);
  - web-search-heavy tasks (journal profiling) — **never alone**: always
    paired with at least one primary agent whose results cross-check it;
  - long-document condensation where its context window helps.
- Per-run `workspace/<slug>/config.yml` may still narrow the agent set but
  may not promote a support agent into a primary-only role.

### Changes — ResearchX skills

- `lit-review`: search fan-out unchanged (agy stays); cross-review and
  synthesize restricted to primary tier.
- `gap-discovery`: search keeps agy; debate, gap analysis, hypothesis
  generation primary only.
- `paper-draft`: outline and drafting primary only (see §2).
- `paper-review`: reviewer and submitter primary only; journal profiling may
  use agy plus one primary agent (paired rule above).

---

## 2. Dual-author drafting + adversarial cross-review (ResearchX)

### Problem

`paper-draft` Phase 3 dispatches one agent per section. The user wants both
Claude and GPT to author the paper from the literature research, and a
demanding adversarial peer-review pass ("hostile senior researcher hunting
mistakes and demanding more explanation").

### New paper-draft phase structure

Phases: `intake`, `outline`, `draft`, `cross-review`, `merge`, `verify`,
`handoff` (`cross-review` + `merge` replace the old `assemble`; `draft`
changes meaning as described below).

1. **draft** — claude and codex each write a *complete independent draft*
   from the same approved outline + `references.bib` + data manifest.
   Outputs: `manuscript/drafts/claude/`, `manuscript/drafts/codex/`.
2. **cross-review** — each author reviews the *other's* full draft using a
   new adversarial reviewer prompt template (persona: highly demanding
   researcher; hunts factual errors, unsupported claims, weak methodology
   description, missing explanations; every criticism must cite the exact
   location). Reviews are JSON validated with the existing `review` schema
   (extended if needed).
3. **revision** (inside cross-review, up to `max_review_rounds` from config)
   — each author revises its own draft and must *address or explicitly
   rebut every point*; the coordinator validates the response file covers
   all points before accepting the round.
4. **merge** — the coordinator compares the two revised drafts
   section-by-section, assembles the best into `manuscript/sections/`, and
   records which draft won each section and why in `report/merge_log.md`.
5. **verify** — existing compile / citation / provenance checks, unchanged.

### paper-review skill

- Reviewer prompt gets the same demanding adversarial persona.
- Reviewer and submitter roles restricted to primary tier (§1).

---

## 3. Metacentrum remote-execution module (ScieFlow)

### Problem

The user works on metacentrum.cz (PBS scheduler, `qsub`). The experiment
loop must be able to: git-pull designated remote directories, submit jobs,
monitor them, fix failing scripts and resubmit until they run, fetch result
data back for analysis, and iterate — all bounded by user-configurable
restrictions, because this touches a shared cluster under the user's
identity.

### Approach (chosen: deterministic wrapper + skill)

All SSH mechanics live in a script that *enforces* restrictions in code;
the skill holds only the judgment loop. A pure-protocol alternative was
rejected because restrictions would be advisory prose. Matches the repo
philosophy: deterministic mechanics in `scripts/`, judgment in skills.

### `config/remotes.yml` (user-editable, deny-by-default)

```yaml
remotes:
  meta:
    host: <login node, e.g. skirit.metacentrum.cz>   # user fills in
    user: <metacentrum username>
    auth: kerberos          # relies on the host PC's ticket; no keys stored
    scheduler: pbs
    allowed_dirs:           # remote paths the agent may pull/read/fetch/submit from
      - /storage/.../projX
    allowed_ops: [git-pull, qsub, qstat, logs, fetch]   # anything absent is forbidden
    limits:
      max_walltime: "24:00:00"
      max_cpus: 16
      max_mem: 64gb
      queues: [default]     # queues the agent may target
      max_concurrent_jobs: 4
      max_fix_attempts: 3   # automatic fix-and-resubmit ceiling per task
```

Ships as `config/remotes.example.yml`; the real `remotes.yml` is gitignored
(contains the user's username/paths).

### `scripts/remote/remote.py` subcommands

Every subcommand validates the target dir, operation, and resources against
`remotes.yml` *before* any SSH happens, and refuses anything outside the
allowlist with a clear error. SSH uses GSSAPI (Kerberos) — no stored
credentials.

- `check <remote>` — SSH reachability + `klist` ticket validity. Expired or
  missing ticket → exit with an instruction for the user to run `kinit`;
  the agent must stop and ask, never attempt authentication itself.
- `pull <remote> <dir>` — `git pull` in an allowed remote directory.
- `submit <remote> <dir> <script> [--walltime --cpus --mem --queue]` —
  builds the qsub command, clamping every resource to the config caps;
  refuses if `max_concurrent_jobs` would be exceeded. Records the job in
  `workspace/<slug>/remote/jobs.yml` (job id, script, dir, resources,
  attempt count, state).
- `status <remote> [jobid]` — qstat wrapper; updates `jobs.yml`.
- `logs <remote> <jobid>` — fetch stdout/stderr of a finished/failed job.
- `fetch <remote> <path> <dest>` — rsync from an allowed dir into
  `workspace/<slug>/remote/data/` only.

### `skills/remote-exec/SKILL.md` — the loop

1. **Preflight**: `remote.py check`; on ticket failure stop and ask the
   user to `kinit`.
2. **Sync**: `remote.py pull` for each directory the campaign needs.
3. **Snakemake gate** (mandatory whenever the workload is a Snakemake
   pipeline — most of the user's processes are): submit/run
   `snakemake -n` (dry-run) **without GPU resources** first. Proceed to the
   real run only if it exits cleanly and prints the complete job DAG; save
   the DAG output to `workspace/<slug>/remote/dag-<task>.txt` as the
   execution plan of record. A failed dry-run enters the same fix loop
   below.
4. **Submit**: autonomous within an approved campaign (ScieFlow approval
   contract, AGENTS.md rule 3) and within the config limits.
5. **Monitor**: poll `remote.py status` with backoff; on completion fetch
   logs.
6. **Fix loop** (on failure): fetch stderr, diagnose, fix the script
   **locally**, commit + push, `remote.py pull` on meta, resubmit.
   Git-only fix flow — never edit files directly on the remote. Up to
   `max_fix_attempts`; on exhaustion mark the task `failed` and
   `checkpoint.py --reason anomaly` (consistent with existing rule 7 —
   report honestly, never rerun-until-green).
7. **Fetch + analyze**: `remote.py fetch` results into the workspace; hand
   to the normal experiment-cycle analysis. If analysis warrants more
   experiments, new submissions follow the same approval contract and this
   same loop.

### Integration

- Campaign YAML gains `backend: local | remote` (+ optional per-task
  resource requests). The experiment-cycle skill routes `backend: remote`
  campaigns through remote-exec instead of ExperimentX.
- New ScieFlow AGENTS.md hard rule: remote access only through
  `scripts/remote/remote.py`, never raw `ssh`/`scp`; bounded by
  `config/remotes.yml`; Kerberos ticket problems are always escalated to
  the user.
- Optional hardening (documented in README, applied if the user wants it):
  Claude Code permission deny-rules for bare `ssh`/`scp`, making the
  wrapper the only executable path.

### Error handling

- Allowlist violation → wrapper exits non-zero with the violated rule;
  agent must not retry with a workaround.
- SSH transport failure → retry once, then report to user.
- Job stuck in queue beyond a config timeout → report, don't kill without
  instruction.

### Testing (offline, in `tests/`)

Inject a fake SSH transport (command-runner interface on the wrapper) and
cover: allowlist enforcement (dir/op/resource/queue), qsub argument
construction and clamping, concurrent-job ceiling, retry accounting against
`max_fix_attempts`, jobs.yml state transitions, snakemake-gate ordering
(dry-run before real run), and `check`'s ticket-failure path.

---

## Out of scope

- Other clusters / schedulers (metacentrum + PBS only, per user).
- Automatic `kinit` or any credential storage.
- Slurm support, GPU-specific scheduling logic beyond "dry-run requests no
  GPU".
- Changes to ExperimentX.
