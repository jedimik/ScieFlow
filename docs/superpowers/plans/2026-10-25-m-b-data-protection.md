# M-B plan: data-protection model (round 1)

Pipeline `m-b-plan`, step plan, round 1, epoch 1. Card `t_86ea0cf7`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261025_220427_3593bf`).
Written 2026-10-25. Previous review: none (round 1).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt.
M-B is an L milestone, so this file holds a spec (Part A) and an implementation plan (Part B).

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

Scope source: GAP-ANALYSIS.md §8 row M-B and §5.1–5.3, §5.5; audit findings A-2, A-7, A-12, A-13
(§3); DECISIONS D9, D10, D11, D23 (and D17 for PDF downloads in L2 runs). The format follows the
approved M-A and M-E plans.

Code was read read-only from git objects. No checkout was touched.

- `M` = remote `main` = `526b3f208698fdacbbe1227072e443f1c6f067af`
  (`git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main`, 2026-10-25 ~22:10 UTC;
  objects fetched with `git fetch --no-tags --no-write-fetch-head`, no ref created).
  `M` is "Merge pull request #18 from jedimik/dev/m-e"; `git diff 9eeb4fa M` is empty.
- Dependencies, all checked with `git merge-base --is-ancestor <sha> M` → exit 0:
  - H-4 (the K line, accepted tip `6152d733728b9ed5bf5b571aef475f8071426386`): the 1,871-line
    allow-list sandbox with `verify()` is on main (`src/scieflow/core/sandbox.py`, 1871 lines).
  - M-A (reviewed tip `be99db2ace0e560fcdb7c00231e906e4aaae67e2`): search goes through the
    provider Gateway; `--role` is required inside a run.
  - M-E (reviewed tip `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`): `scripts/remote` has the
    scheduler seam. M-B touches `remote.py` push/fetch only.
- The in-flight paper-lifecycle diff has **not** landed: `config/agents.yml` on `M` has no
  `kimi-einfra` entry (`config/agents.yml:18-155`). M-B is designed so that it edits none of the
  in-flight files (section 3), so it does not wait for that diff.
- For reading with line numbers, `M` was exported once with `git archive` into
  `RUN/scratch/m-b-plan-probe/main-526b3f2/` (read-only use; nothing executed there).

All line numbers below are on `M`.

## 1. For the human: what M-B changes, in plain words

Today ScieFlow has no idea what kind of data a run holds. Any agent dispatched into a run can read
everything in it, and every other run's files too (the sandbox binds the whole project read-only).
Patient-data protection rests on agent discipline (audit A-2, A-12). M-B adds ScieFlow's own
data-protection model, as you decided in D9–D11 and D23.

1. **You declare data levels.** `scieflow data declare <run> <path> --level L0|L1|L2` writes the
   run's `data.yml`. `L0` = code, metadata, docs; `L1` = aggregates; `L2` = record-level or
   re-identifiable data (tables, imaging, EEG/PSG) **and everything derived from it**. ScieFlow
   cannot know what is in a file (D9), so a level is always your declaration. An agent may raise a
   level, never lower or remove one.
2. **Your policy file says which agents are safe.** A new user-owned file,
   `config/data-policy.yml` (copy it from `config/data-policy.example.yml`), lists your safe
   endpoints and safe agents (e-INFRA, a local Ollama/vLLM, an institutional endpoint…), the
   remotes that may hold L2 (`l2_remotes`), the literature services an L2 run may still query
   (`l2_providers`, default none), and `enforcement: warn|refuse`. ScieFlow never writes this file
   and ships no entries in it (D11). Agents cannot edit it: `config/` is read-only in every sandbox.
3. **Declared L2 is enforced in code (D10).**
   - In a run whose level is L2, every dispatch of a non-safe agent is refused before launch, with
     a new exit code **78** and a `job.refused` event. That holds on all three dispatch paths: the
     `scieflow agent run` CLI, the web/service dispatch and the run conversation.
   - Cloud agents never see an L2 run of the same project: the whole run folder is hidden in their
     sandbox, and `verify()` proves it before launch. The same applies to project-level L2 folders
     you list in the policy (for example a `readable:` data folder).
   - In an L2 run, literature searches, scite, NotebookLM and PDF downloads are refused unless you
     list the service in `l2_providers`; a push of run files to a remote not in `l2_remotes` is
     refused (D23: L2 analysis stays local by default).
4. **Results leave an L2 run only through a release you approve.**
   `scieflow data release <run> <file>` checks the file (no small cells, no identifier or
   free-text columns, no file paths, no DICOM/EEG headers), then opens a new human-only
   `data-release` gate. When you answer `release`, the file is copied to the run's `released/`
   folder with a checksum. `scieflow data import <other-run> --from <run> --release <id>` brings it
   into a run where cloud agents may work.
5. **Everything else only warns (D10).** `scieflow data scan <run>` looks for undeclared data that
   looks like patient data (REDCap exports, identifier columns, birth-number/e-mail/phone values,
   free text, DICOM/EEG headers, unreadable spreadsheet formats). Before each cloud dispatch the run
   is scanned within a time and size limit, and findings become warnings on the run timeline.
   `enforcement: refuse` in your policy turns them into refusals for that project. Once any L2 is
   declared, ScieFlow suggests `refuse` but never sets it (D23).
6. **Tabular de-identification.** `scieflow data ingest <run> <export.csv> --out inputs/<name>.csv`
   drops identifier columns (always `rc`/rodné číslo), drops free text, turns dates into days
   since an anchor date, replaces the record id with a keyed pseudonym (the key stays in your home
   folder, outside every run) and writes a checksummed manifest. The output is still declared L2.
7. **Docs.** New `docs/data-protection.md` (with the per-destination egress table), `SECURITY.md`
   and `PRIVACY.md`; the sandbox and CLI pages are updated; a new rule 18 in `AGENTS.md` (your
   approval needed, below). The web run page shows the run's data level.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-B-D1 A run is as sensitive as its most sensitive declaration.** One L2 declaration makes
  the whole run L2, because files an agent derives from L2 data are L2 too and cannot be tracked
  file by file (GAP §5.1). So no cloud agent is ever dispatched into an L2 run, in `warn` mode too.
  Work with cloud agents happens in another run, fed by releases.
- **M-B-D2 Cloud agents do not see other L2 runs at all.** The GAP row hides declared L2 *paths*;
  M-B hides the whole folder of every L2 run from cloud dispatches, for the same reason as D1.
- **M-B-D3 Safe status comes only from `config/data-policy.yml`.** `config/agents.yml` is an
  in-flight file, and `scieflow agent configure` can rewrite it; M-B does not edit it. An agent is
  safe only when the policy lists it with an endpoint from `safe_endpoints`, its command templates
  pass that endpoint through a new `{endpoint}` placeholder, the templates' sha256 equals the
  `templates_sha256` you pinned (`scieflow data pin <agent>` prints it; it never writes the file),
  and no launcher or script in the command lies in an agent-writable folder (`workspace/` or a
  `writable:` grant). Any per-run or per-role override that changes the command makes the agent
  cloud for that dispatch. The in-flight `kimi-einfra` entry runs a launcher from inside a run's
  `tools/` (A-13); it would only count as safe after you move that launcher under the repo's
  `tools/` and pin it.
- **M-B-D4 Exit code 78** for a data-level refusal of a dispatch (75, 77 and 124 are taken;
  78 is EX_CONFIG). Egress refusals (Gateway, scite, NotebookLM, remote) keep their existing
  `POLICY:`/exit 3.
- **M-B-D5 Fail closed.** A malformed `data.yml` makes that run L2. A malformed
  `config/data-policy.yml` refuses every dispatch (78, rule `policy-invalid`) and every egress
  check (exit 3) until you fix it.
- **M-B-D6 No unsandboxed cloud dispatch while any L2 exists in the project** (an unsandboxed
  process cannot be kept away from other runs). `--no-sandbox` and `unsandboxed_runs:` keep working
  in projects without L2.
- **M-B-D7 External sharing for L1 (closes A-2 for declared data).** A cloud dispatch into an
  L1 run without a human-answered, approving `external-sharing` gate in that run gives a warning;
  under `enforcement: refuse` it is refused. Undeclared (L0) runs behave as today.
- **M-B-D8 The pre-dispatch scan runs for cloud dispatches only**, within the policy's
  `scan:` limits (defaults 2,000 files, 64 KiB per file, 5 s). A warning is put on the timeline once
  per file and rule. Under `refuse`, a finding or an incomplete scan refuses the dispatch.
- **M-B-D9 `data.yml` and `released/` are read-only inside every dispatch of their run**
  (read-only bind mounts, proven by `verify()`), and every `scieflow data` command that writes
  refuses to run inside a dispatch.
- **M-B-D10 The "no L2 id in a cloud draft" rule of GAP §5.2 is enforced structurally**: a cloud
  agent can never sit in an L2 run (D1), and released data reaches its run only by `data import`.
  No draft-validator change.
- **M-B-D11 Ingest covers tables only** (CSV, including REDCap exports). Imaging (DICOM/NIfTI) and
  EEG/PSG de-identification are not in M-B: `scan` detects their headers, and the docs point to an
  external anonymiser whose output you declare L2. `ingest` is a human-terminal command only
  (stricter than GAP §5.3, which also allowed a safe agent).
- **M-B-D12 The pseudonym key** is `~/.config/scieflow/salts/<project>.salt` (32 random bytes,
  mode 0600), created on first ingest. It is outside every run and invisible in every sandbox
  (the home folder is never bound). Lose it and new pseudonyms will not match old ones.
- **M-B-D13 DVC and chat backups are not refused for L2 runs** in M-B. They go to your own storage
  under rule 15's upload consent; the egress table says so and it is a follow-up.
- **M-B-D14 No `level:` field in `inputs/manifest.yml`** (its validator `core/run/validate.py` is
  in-flight); `data.yml` is the only declaration source.
- **M-B-D15 `scieflow run show` is unchanged** (`core/run/cli.py` is in-flight). The terminal view
  is `scieflow data levels <run>`; the web run page gets a data panel.
- **M-B-D16 The resolved level is recorded as a `data.level_resolved` event**, not as a new job
  field.
- **M-B-D17 AGENTS.md rule 18** (a protected file; exact text in section 3). Approving the plan
  approves this edit; the operator applies it before ticket t6.
- **M-B-D18 Safe agents see other L2 runs.** M-B adds no extra hiding for safe dispatches.

**Proposed PR** (push and merge go through the operator's publish step after the final review
PASS, as the milestone chain authorises; `approvals/milestone-chain-human-20261024.md`):
one PR, head `dev/m-b` → base `main`, merged with a merge commit, title
"M-B Data-protection model", body written by the `branch` step.

## 2. Base, worktree and dependency

- Base = remote `main` at the time step `t0` runs. Today that is `526b3f2`.
- `t0` fetches objects with
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`
  (no ref is created) and refuses (verdict `BASE_RED`) unless all three hold:
  `git merge-base --is-ancestor 6152d733728b9ed5bf5b571aef475f8071426386 <base>` (H-4),
  `… be99db2ace0e560fcdb7c00231e906e4aaae67e2 <base>` (M-A),
  `… 9eeb4fad8c01d7c008acd155e81dac3976d62aa7 <base>` (M-E).
- `t0` creates the worktree with
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-b RUN/scratch/m-b-wt <base>`
  (`RUN` = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`). The new
  branch pins the base. On a retry, an existing `RUN/scratch/m-b-wt` on `dev/m-b` at `<base>`
  (or at `<base>` plus t0's own docs commit) is reused, never recreated or reset.
- Work happens only in `RUN/scratch/m-b-wt`; git only through `git -C`/the worktree. Nothing touches
  `/home/ubuntu/Github/ScieFlow` or the checked-out tree of `/home/ubuntu/ScieFlow-claude-f2976f52`.
- Commits are local, on `dev/m-b` only. Every review fix is a new commit: no amend, rebase, squash
  or force.
- Scratch output goes to `RUN/scratch/m-b/`: junit, logs, `executions.jsonl`, `pr-body.md`.

## 3. In-flight and protected files

**In-flight paper-lifecycle files** (the same nine paths as M-A's and M-E's `INFLIGHT`):

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

M-B edits **none** of them (M-B-D3, D14, D15). Every implementation, fix and branch step runs this
guard and reports `inflight_guard_exit`:
- `git -C $WT diff --exit-code <base> -- <INFLIGHT>`;
- on a dirty tree, also `git -C $WT diff --exit-code -- <INFLIGHT>`.

**Sealed and protected files.** `config/sandbox.yml` is not edited (M-B reads it through the
existing loaders only). The DD1-sealed K8 files are not touched. The only protected
agent-instruction edit is **AGENTS.md rule 18** (M-B-D17), listed exactly in
`pipelines/drafts/m-b-protected-edits.json` with `before_step: t6`. The operator applies it to
the worktree before t6; t6 commits it unchanged as its own commit. Text to be inserted after rule 17
(between `    review configure\`; every review is a fresh dispatch.` + blank line and
`## Skills (read the relevant one before acting)`, `AGENTS.md:199-201`):

```
18. **Data protection** (`docs/data-protection.md`). The user declares what
    a run holds with `uv run scieflow data declare <slug> <path> --level
    L0|L1|L2`: `L0` code and metadata, `L1` aggregates, `L2` record-level
    or re-identifiable data and everything derived from it. You may raise a
    level (`--as-agent`), never lower or remove one, and you never edit a
    run's `data.yml` or the user-owned `config/data-policy.yml` (safe
    agents, `l2_remotes`, `l2_providers`, `enforcement`). In an L2 run only
    the safe agents listed there are dispatched; every other dispatch is
    refused (exit 78), and live literature, scite and NotebookLM requests
    and pushes to remotes not in `l2_remotes` are refused (exit 3). A
    refusal is a boundary: never copy, summarise or paste L2 content
    anywhere to get past it. Results leave an L2 run only through `uv run
    scieflow data release` (a human-only `data-release` gate) and `uv run
    scieflow data import` into another run. Warnings from `uv run scieflow
    data scan` (undeclared data that looks like patient data) go to the
    user with a proposed declaration; they are never silenced.
```

**Files M-B may change (complete list):**

| File | Change | Ticket |
|---|---|---|
| `src/scieflow/core/data/__init__.py`, `levels.py`, `policy.py` | new | t1 |
| `src/scieflow/core/data/cli.py` | new (t1), extended | t1, t4, t5 |
| `src/scieflow/core/data/dispatch.py` | new (t2), extended (sandbox sets, scan hook) | t2, t3, t4 |
| `src/scieflow/core/data/scan.py` | new | t4 |
| `src/scieflow/core/data/release.py`, `ingest.py` | new | t5 |
| `src/scieflow/cli.py` | one `GROUPS` entry `data` | t1 |
| `src/scieflow/core/events.py` | `TYPES` additions only | t1 |
| `src/scieflow/core/agent_run.py` | decision, `{endpoint}`, exit 78, `Dispatch.data` | t2, t3 |
| `src/scieflow/core/service.py` | refusal handling on two paths; `_PANELS` `data` panel | t2, t3, t6 |
| `src/scieflow/core/sandbox.py` | `Layout.data_hidden`, `Layout.protected`, `wrap`, `verify`, `dispatch_layout` | t3 |
| `src/scieflow/core/gates.py` | `data-release` answer hook | t5 |
| `schemas/gates.yml` | one kind, additive | t5 |
| `src/scieflow/research/lib/providers.py` | Gateway L2 rule | t4 |
| `src/scieflow/integrations/scite/gateway.py` | L2 rule | t4 |
| `scripts/remote/remote.py` | push/fetch rules only | t4 |
| `scripts/nblm/nblm.py` | L2 refusal in `main` | t4 |
| `src/scieflow/web/templates/run.html` | data panel | t6 |
| `config/data-policy.example.yml` | new | t1 |
| `.gitignore` | one line `config/data-policy.yml` | t1 |
| `docs/data-protection.md`, `SECURITY.md`, `PRIVACY.md` | new | t6 |
| `docs/sandbox.md`, `docs/cli.md`, `mkdocs.yml` | sections, exit-code row, one nav line | t6 |
| `AGENTS.md` | rule 18, operator-applied (protected edit), committed by t6 | t6 |
| `docs/superpowers/plans/2026-10-25-m-b-data-protection.md`, `docs/superpowers/specs/2026-10-25-m-b-data-protection-design.md` | copies of this plan / its Part A | t0 |
| new tests under `tests/core/data/`, plus `tests/core/test_sandbox_data.py`, `tests/test_remote_data.py`, `tests/test_nblm_data.py`, `tests/research/test_gateway_data.py`, `tests/integrations/test_scite_data.py`, `tests/test_docs_data_protection.py` | new | per ticket |

Existing tests: only two existing assertions may change, both additively — the event-vocabulary
pins `tests/core/review/test_gates_events.py:151-153` and `tests/core/test_service.py:1215-1232`
(add the six `data.*` types, nothing removed). Everything else in `tests/` only gains files or
lines (`git diff <base>..HEAD -- tests/` shows no removed line outside those two pins). Each review
checks this.

## 4. Current code, verified

**Dispatch, CLI path** — `src/scieflow/core/agent_run.py`
- `BUDGET_EXIT = 75`, `SANDBOX_EXIT = 77` (`:45-46`); timeout 124 (`:766`). No other exit code.
- `Dispatch` dataclass (`:53-63`): agent, argv, cwd, stdin_text, timeout_s, run_dir, writable,
  env, layout.
- `_template_token` substitutes only `model|reasoning|root|python|session|prompt` (`:96-102`);
  `_template_argv` splits before substitution (`:105-114`).
- `prepare` (`:287-385`): registry entry + run `agent_overrides` merged (`:296-302`); M-A role
  requirement (`:303-307`); role assignment and role override (`:308-317`); argv built
  (`:333-357`); sandbox policy, `disabled`, `writable` (`:358-362`); layout and credential
  (`:368-381`). There is no data concept.
- `main` (`:694-771`): `DispatchError` → `sys.exit(str(e))` (`:712-713`); `SandboxError` →
  transcript + `job.refused reason=sandbox` + exit 77 (`:714-719`, `:727-735`); budget 75
  (`:737-743`); launch (`:745-748`).
- `resolve_turn` (`:545-642`) builds the conversation's layout (`:611-613`); `prepare_turn`
  composes argv from the captured templates (`:668-670`).

**Dispatch, service and conversation paths** — `src/scieflow/core/service.py`
- `dispatch_agent` (`:216-274`): `agent_run.prepare`; `DispatchError`/`SandboxError` →
  `ServiceError`, `job.refused reason=sandbox` for the latter (`:220-227`); verify (`:238-245`).
- `_say_prepared` (`:665-694`): `resolve_turn` + `prepare_turn` + verify; `SandboxError` →
  `job.refused` (`:689-694`).
- `run_overview` panels: `_PANELS = {"inventory", "manuscript", "attribution", "progress"}`
  (`:2348-2349`, `:2352-2373`); `run.html` renders `overview.<panel>` (`templates/run.html:35-117`).

**Sandbox** — `src/scieflow/core/sandbox.py`
- `Layout` (`:118-156`): readable, links, home, launcher_dirs, hidden, installs, binds; `hidden`
  overlays come after the readable binds and before the writable ones.
- `wrap` (`:397-467`): readable binds (`:424-435`) → hidden (`:449`) → writable binds (`:450-451`)
  → scratch pins and masks (`:454-457`) → home (`:458-459`) → credential binds (`:460-461`).
- `_hidden_args` (`:373-394`): tmpfs for a directory, `/dev/null` for a file; refuses a symlink,
  a missing path or a hidden path inside a writable grant.
- `dispatch_layout` (`:1081-1136`): the **project root is bound read-only** (`:1097-1107`), so a
  dispatch can read every other run; `hidden` = project secrets + other runs' agent homes
  (`:1132`, `_secret_masks` `:879-899`, `_other_run_homes` `:902-944`).
- `verify` (`:633-721`): proves the probe ran, a forbidden write was blocked and the checks of
  `_probe_checks` (`:593-606`: `.ssh`, `.hermes`, D-Bus sockets, host markers) are absent. It does
  not know about any data path.
- `config/` is never writable inside a sandbox (`config/sandbox.yml:4-5`); every sandboxed agent
  needs a `credentials:` entry (`config/sandbox.yml:41-67`); C12 environment allow-list
  (`:63`, `dispatch_environment` `:1169-1193`); per-dispatch `UV_CACHE_DIR` = `<run>/.uv-cache`
  (`cache_dir` `:753-763`).
- Network is open (`docs/sandbox.md:49`, `:476-489`).

**Events and gates**
- `events.TYPES` is a closed set (`src/scieflow/core/events.py:20-36`); `emit` refuses unknown
  types (`:44-46`). Two tests pin the set exactly (section 3).
- `schemas/gates.yml:4-22`: 15 kinds, `external-sharing` `requires_human: true` (`:11`). No code
  checks it at dispatch (A-2).
- `gates.answer` (`src/scieflow/core/gates.py:105-127`) runs a kind-specific hook for review kinds
  before the gate is marked answered (`:115-119`); an agent cannot answer a `requires_human` kind
  (`:93-95`).

**Egress points**
- Provider Gateway (`src/scieflow/research/lib/providers.py`): `CATALOGUE` (`:61-75`), the
  `Gateway` (`:244-304`) with `check(provider, url)` (`:292-304`) called before every request;
  `ProviderRefused` is a scite `PolicyError` (exit 3). Search builds it per command
  (`research/search/registry.py:86-94`), unscoped without `--workspace`.
- scite (`src/scieflow/integrations/scite/gateway.py:49-93`): every request belongs to a run
  (`ledger.ws`, `:57-58`). Dispatch detection `in_dispatch` (`integrations/scite/mcp.py:160-166`).
- Remote (`scripts/remote/remote.py`): `cmd_fetch` (`:702-718`), `cmd_push` (`:721-740`),
  `PolicyError` → `POLICY:` exit 3 (`:951-953`); root = `Path(__file__).resolve().parents[2]`
  (`:876`).
- NotebookLM (`scripts/nblm/nblm.py`): `cmd_fetch` (`:94`), `cmd_upload` (`:123`), `cmd_verify`
  (`:179`), `cmd_ask` (`:215`); `PolicyError` → exit 3 (`:320-322`).

**Agent configuration**
- `config/agents.yml:18-155`: no `boundary`/`endpoint` key; nothing marks an agent safe.
- `agent_config.AGENT_FIELDS` (`:64`) lets `scieflow agent configure` change `cmd`/`stdin_cmd`;
  per-run `agent_overrides` may replace `cmd` (`agent_run.py:301-302`); role overrides are model and
  reasoning only (`agent_config.py:89`, `apply_role_override` `:289-306`).

**Other facts**
- `rg -i "classif|de-identif|patient|data-policy|data.yml"` over `src/`, `scripts/`, `config/` →
  no data-level concept (A-12 confirmed on `M`).
- `.gitignore:17-21` lists the user-owned config files (`remotes.yml`, `scite.yml`,
  `integrations.yml`, `chats.yml`).
- CLI groups: `src/scieflow/cli.py:14-56` (lazy `GROUPS`).
- CLI parity table `tests/core/cli_parity.yml` covers public `service.py` functions. M-B adds no
  public service function (the panel builder is private), so the table is unchanged.
- Docs: exit-code table `docs/cli.md:10-19`; nav `mkdocs.yml:54-61`.
- No telemetry exists (`rg -il "telemetry|analytics|sentry" src scripts` → none).
- Test fixture project: `tests/core/conftest.py:87-113` (stub agents, credentials, run `r1`).

---

## Part A — Spec

(t0 copies everything from this heading up to, not including, the heading
"## Part B — Implementation plan" to `docs/superpowers/specs/2026-10-25-m-b-data-protection-design.md`.)

### 5.1 Levels and the run's declaration file

Levels, ordered: `L0` < `L1` < `L1.5` < `L2`.

| Level | Meaning | Declared by |
|---|---|---|
| `L0` | code, docs, metadata; the default | default |
| `L1` | aggregates with small cells suppressed | user |
| `L1.5` | a released or imported artifact (human-approved aggregate) | ScieFlow only, never declarable |
| `L2` | record-level or re-identifiable data (tables, imaging, EEG/PSG, raw outputs) and everything derived from it that is not released | user |

`workspace/<slug>/data.yml` (module `scieflow.core.data.levels`):

```yaml
version: 1
default_level: L0            # L0 | L1 | L2
levels:                      # each entry: one declaration
  - path: inputs/export.csv  # glob relative to the run; no "..", not absolute, not empty
    level: L2                # L0 | L1 | L2
    kind: tabular            # tabular | imaging | eeg | other
    reason: "REDCap export, record level"
    declared_by: human       # human | agent
    declared_at: "2026-10-26T10:00:00Z"
pending_releases: []         # t5: {id, source, sha256, gate, checks, requested_at}
released: []                 # t5: {id, file, sha256, source, gate, approved_by, approved_at, checks}
imports: []                  # t5: {id, file, sha256, from, release, imported_at}
```

- `run_level(ws) -> Level`: the maximum of `default_level`, every `levels[].level`, and `L1.5`
  if `imports` is non-empty. A declared glob counts whether or not it matches a file today.
- `read(ws) -> DataFile | Invalid`: unknown keys, wrong types, a bad path, an unknown level or
  YAML errors give `Invalid(reason)`, and `run_level` of an invalid file is `L2` with that reason
  (M-B-D5). No `data.yml` = `L0`, no declarations.
- Writes go through one function under `store.locked(<ws>/data.yml)` with an atomic replace
  (`store._atomic_write` pattern). Only `scieflow data` commands write it.
- `project_l2_paths(root, policy)` and `l2_runs(root, except_run)` list what is L2 in the
  project: every run under `workspace/` whose `run_level` is `L2` (a symlinked run entry is
  skipped, the same way `_other_run_homes` skips it), and every existing `project_levels` path
  of level `L2` from the policy.

### 5.2 The project policy file (user-owned)

`config/data-policy.yml` (module `scieflow.core.data.policy`; template
`config/data-policy.example.yml`, everything in it commented out; real file gitignored):

```yaml
version: 1
enforcement: warn            # warn | refuse   (D10, D23; default warn)
safe_endpoints:              # D11: only the user adds entries
  - url: https://llm.example.org/v1     # https, or http only for 127.0.0.1 / ::1 / localhost
    reason: "..."
safe_agents:
  my-safe-agent:
    endpoint: https://llm.example.org/v1   # must be one of safe_endpoints
    templates_sha256: "<64 hex>"           # from `scieflow data pin my-safe-agent`
    reason: "..."
l2_remotes:                  # D23: config/remotes.yml entries that may hold L2
  - remote: meta
    reason: "..."
l2_providers:                # services an L2 run may still query (default: none)
  - name: search-europepmc   # a providers.CATALOGUE key, "scite" or "notebooklm"
    reason: "..."
project_levels:              # data outside any run (repo-relative, absolute or ~/ path)
  - path: ~/datasets/rbd-exports
    level: L2
    kind: tabular
    reason: "..."
min_cell: 5                  # release: smallest publishable cell count (int >= 1)
scan: {max_files: 2000, max_bytes_per_file: 65536, max_seconds: 5}
ingest: {extra_drop: []}     # extra identifier column names for `data ingest`
```

- `load(root) -> Policy`. No file → defaults (`warn`, nothing safe, nothing allowed for L2,
  `min_cell` 5, the scan defaults). Unknown keys, a missing `reason`, a bad URL, an agent whose
  endpoint is not in `safe_endpoints`, an unknown `l2_providers` name, a non-token remote name, a
  bad level or path → `PolicyInvalid(message)`; messages name the key, never a value from a data
  file.
- No ScieFlow code path writes this file (D11). A test runs every `scieflow data` subcommand and
  asserts the file's bytes are unchanged.

### 5.3 Agent boundary (`safe` or `cloud`)

`policy.boundary(policy, agent, final_cfg, *, root, writable_areas) -> Boundary(kind, endpoint,
reason)`, computed on the **final** agent configuration that will be launched (registry entry +
run `agent_overrides` + role override). Kinds and reasons, checked in this order:

| Result | Condition |
|---|---|
| `cloud`, reason `not-listed` | agent not in `safe_agents` |
| `cloud`, reason `no-endpoint-placeholder` | any present template among `cmd`, `stdin_cmd`, `session_cmd`, `resume_cmd` lacks `{endpoint}` |
| `cloud`, reason `pin-mismatch` | sha256 of `json.dumps({k: cfg[k] for those keys present}, sort_keys=True, separators=(",", ":"))` ≠ `templates_sha256` (so any override that changes a template makes the agent cloud) |
| `cloud`, reason `launcher-writable` | after substitution, an argv token that is an absolute path lies under `<root>/workspace` or under a `writable:` grant of `config/sandbox.yml` (A-13) |
| `safe`, `endpoint` set | otherwise |

- `{endpoint}` becomes a template placeholder (`_template_token`). It is filled with the policy
  endpoint for a safe agent. A template with `{endpoint}` for a cloud agent is refused (rule
  `endpoint-without-safe-entry`, below) instead of being filled with an empty string.
- `scieflow data pin <agent>` prints the sha256 of the registry entry's templates (no overrides)
  and the line to paste; it writes nothing.
- Honest limit (stated in the docs): ScieFlow proves which endpoint it handed the CLI and what the
  sandbox exposed, not what a third-party binary sends. The sandbox network stays open (A-7, B6).

### 5.4 The dispatch decision

`scieflow.core.data.dispatch.decide(project, run_dir, agent, final_cfg, *, sandboxed: bool,
templates_use_endpoint: bool) -> Decision` is pure apart from reading files (it writes nothing).

`Decision` fields: `allowed`, `rule` (refusal rule or `None`), `message`, `level`, `boundary`,
`endpoint` (`""` for cloud), `data_hidden` (tuple of absolute Paths), `protected` (tuple),
`warnings` (tuple of `Finding(path, rule, detail)`), `enforcement`.

Rules, first refusal wins (all refusals raise `data.DataRefused(decision)` in the caller):

| Rule id | Condition | Result |
|---|---|---|
| `policy-invalid` | `config/data-policy.yml` invalid | refuse |
| `endpoint-without-safe-entry` | a template uses `{endpoint}` and the boundary is `cloud` | refuse |
| `l2-cloud` | `run_level == L2` and boundary `cloud` | refuse, in every enforcement mode |
| `l2-unsandboxed` | not sandboxed, boundary `cloud`, and the project has any L2 run (other than this one) or L2 `project_levels` path | refuse |
| `l1-external-sharing` | `run_level == L1`, boundary `cloud`, and no gate of kind `external-sharing` in the run with `state: answered`, `answered_by: human` and an answer in {`yes`, `approve`, `approved`, `share`} | warning; refuse under `enforcement: refuse` |
| `scan-warning` | boundary `cloud`, `run_level < L2`, the bounded scan (5.6) has findings | warnings; refuse under `refuse` |
| `scan-incomplete` | as above, scan hit a limit | warning; refuse under `refuse` |

Sandbox inputs (only when sandboxed):
- `data_hidden`: for a `cloud` boundary, every L2 run of the project other than this one (whole
  directory) and every existing L2 `project_levels` path; for `safe`, empty (M-B-D18). A path
  inside another listed directory is dropped (the outer overlay covers it).
- `protected`: `<run>/data.yml` if it is a regular file, `<run>/released` if it is a directory,
  for every boundary (M-B-D9).

Callers (`agent_run.prepare`, `agent_run.resolve_turn`) call `decide` after the final agent
configuration and the sandbox on/off state are known and **before** argv and layout are built; they
put the endpoint into the templates and `data_hidden`/`protected` into the layout, and attach the
decision to `Dispatch.data` / `ResolvedTurn.data` (a new trailing defaulted field).
`DataRefused` is defined as `class DataRefused(Exception)` carrying the decision — not a subclass of
`ValueError`, `OSError`, `RuntimeError` (so not of `SandboxError`) or `DispatchError` — so neither
`resolve_turn`'s wrapper (`agent_run.py:641-642`) nor the existing `SandboxError`/`DispatchError`
handlers swallow it; each caller catches it explicitly.

On refusal, on each path:
- CLI (`agent_run.main`): transcript and stderr get
  `<agent>: data protection refused (<rule>): <message>`; `job.refused` with
  `reason="data-level", rule, level, agent, boundary`; exit **78** (`agent_run.DATA_EXIT`). No job
  is created.
- Service (`service.dispatch_agent`) and conversation (`service._say_prepared`): the same
  `job.refused` event, then `ServiceError(message)`.
On success, the caller emits `data.level_resolved` (`level, agent, boundary, hidden: <count>,
protected: <count>`) and one `data.warning` per new warning (5.6 dedupe), then continues.

### 5.5 Sandbox: hiding and read-only protection

`Layout` gains two trailing fields, both default `()` (so every existing construction is valid):
- `data_hidden: tuple[Path, ...]` — overlaid exactly like `hidden` (tmpfs for a directory,
  `/dev/null` for a file) in the same position (after readable binds, before writable binds), with
  the same refusals (symlink, missing, inside a writable grant). A path in both lists is emitted
  once.
- `protected: tuple[Path, ...]` — each bound read-only onto itself (`--ro-bind p p`) **after** the
  writable binds, scratch pins and masks and **before** the agent home. Each must exist, must not be
  a symlink and must lie inside a writable grant; otherwise `SandboxError`. A read-only bind mount
  cannot be written, renamed or unlinked from inside (EROFS/EBUSY).

`dispatch_layout(..., data_hidden=(), protected=())` passes them through. `verify` proves them:
- for each `data_hidden` directory, a host marker file is created inside it (the existing
  `create_marker` pattern; removed in `finally`) and must be **absent** inside the sandbox;
- for each `data_hidden` file, it must be empty inside (`[ -s p ]` false);
- for each `protected` path, it must not be writable inside (`[ -w p ]` false).
A failed proof raises `SandboxUnavailable`, which every path already turns into a `job.refused
reason=sandbox` and exit 77 / `ServiceError`.

A coordinator-scope dispatch (writable = the whole `workspace/`) with any L2 run would have a hidden
path inside its writable grant and is refused by the existing `_hidden_args` rule; no such dispatch
exists on `M` (`writable_for(coordinator=True)` has no launch caller).

### 5.6 Scan (warnings, D10)

`scieflow.core.data.scan.scan(ws, policy, *, clock) -> ScanResult(findings, files_seen,
truncated)`. Deterministic order (sorted relative paths). Skips: `scratch/agent-home/`, any
`.uv-cache/`, `provenance.git/`, `.git/`, `logs/`, `gates/`, `scite/`, `events.jsonl`. Reads at
most `max_bytes_per_file` per file and the first 200 data rows of a table; stops at `max_files` or
`max_seconds` (`truncated: true`). Rules:

| Rule id | Detects |
|---|---|
| `redcap-export` | CSV/TSV header with `record_id` and one of `redcap_event_name`, `redcap_repeat_instrument`, a `*_complete` column |
| `identifier-column` | a header column whose normalised name (NFKD, accents stripped, lower case, non-alphanumerics → `_`) is in `IDENTIFIER_COLUMNS` (English and Czech names: name/first/last/surname, jmeno, prijmeni, rc, rodne_cislo, birth_number, dob, date_of_birth, birth_date, datum_narozeni, address, adresa, ulice, zip, postcode, postal_code, psc, phone, telefon, tel, mobile, mobil, email, e_mail, mail, insurance_number, cislo_pojistence, mrn, medical_record_number, ssn, national_id, passport, ip_address, plus the policy's `ingest.extra_drop`) |
| `identifier-values` | a column with a value shaped like a birth number `^\d{6}/?\d{3,4}$`, an e-mail `^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$` or an international phone number `^\+\d{1,3}[ \d]{8,14}$` |
| `free-text-column` | ≥ 20 non-empty values and (mean length ≥ 40 characters, or ≥ 30 % of values with ≥ 5 words) |
| `dicom-header` | bytes 128–131 are `DICM`, or extension `.dcm` |
| `imaging-file` | `.nii`, `.nii.gz`, `.mgz`, `.img`/`.hdr` |
| `eeg-header` | `.edf`/`.bdf`/`.rec`, or an EDF header (`0` + 7 spaces) whose patient field (bytes 8–87) is not blank or `X X X X`, or `\xffBIOSEMI` |
| `binary-tabular` | `.xlsx`, `.xls`, `.sav`, `.dta`, `.sas7bdat`, `.rds`, `.feather`, `.parquet` ("cannot be inspected") |
| `unreadable` | a file the scan could not read |

A finding records `path` (run-relative), `rule` and `detail` (a column name or a byte offset).
**Never a value**: a test plants a unique value and asserts it appears in no output, event or
cache file.

Warning dedupe: `<run>/scratch/data-scan-reported.json` maps `(path, size, mtime_ns, rule)` to the
event id already emitted. It only suppresses repeated events in `warn` mode; under `refuse` the
pre-dispatch scan never trusts it (scratch is agent-writable).

### 5.7 Egress points

| Egress | Rule (L2 = `run_level` of the run concerned) | Where |
|---|---|---|
| Provider Gateway (literature search, discovery providers) | For an L2 run, `Gateway.check` raises `ProviderRefused("data protection: run <slug> is L2; <provider> is not in l2_providers (config/data-policy.yml)")` unless the provider is listed. An unscoped Gateway inside a dispatch (`in_dispatch()`) whose `UV_CACHE_DIR` is `<root>/workspace/<slug>/.uv-cache` uses that run's level. | `providers.Gateway._setup`/`check` |
| scite (REST and MCP) | `gateway.request` raises `PolicyError` for an L2 `ledger.ws` unless `scite` is listed, before cache or transport | `integrations/scite/gateway.py` |
| NotebookLM (`fetch` = PDF downloads (D17), `upload`, `verify`, `ask`) | refused with `PolicyError` (exit 3) for an L2 `--workspace` unless `notebooklm` is listed, before any session or download | `scripts/nblm/nblm.py` `main` |
| Remote push | from an L2 run to a remote not in `l2_remotes` → `PolicyError` before any transport call, unless the source lies under `<run>/released/` | `remote.py cmd_push` |
| Remote fetch | from a remote **in** `l2_remotes` into a run below L2 → `data.warning` rule `l2-remote-fetch` ("declare the fetched data"); `PolicyError` under `refuse` | `remote.py cmd_fetch` |
| Agent dispatch | section 5.4 | `agent_run`, `service` |
| DVC push, chats push, news | not refused in M-B (M-B-D13); documented in the egress table | — |

An invalid policy file makes every rule above refuse (exit 3), M-B-D5.

### 5.8 Release and import

`scieflow data release <slug> <artifact> [--id ID] [--counts COL[,COL…] | --no-counts]
[--reason TEXT]` (refused inside a dispatch):
1. The artifact must be a regular file inside the run, not a symlink, not under `released/`.
2. Checks (`release.check(path, policy, counts)`), each with an id; any `refuse` stops here and
   prints a report naming path, column and rule, never a value:

   | Check id | Applies to | Refuses when |
   |---|---|---|
   | `small-cell` | CSV/TSV | a count column (named with `--counts`, else any integer column named `n`, `count`, `freq`, `n_*`, `*_n`, `*_count`) has a value `0 < v < min_cell` |
   | `counts-unknown` | CSV/TSV | no count column found and neither `--counts` nor `--no-counts` given |
   | `record-level` | CSV/TSV | an id-like column (`record_id`, `id`, `subject`, `subject_id`, `patient`, `patient_id`, `pid`, `participant`, `participant_id`, or `*_id`) |
   | `identifier-column`, `identifier-values`, `free-text-column` | CSV/TSV | as in 5.6 |
   | `path-values` | CSV/TSV/JSON | a value that looks like a file path (`/…/x.ext`, `C:\…`, `~/…`) |
   | `dicom-header`, `eeg-header`, `binary-tabular` | any | as in 5.6 |
   | `not-inspectable` | `.png`, `.svg`, `.pdf`, `.jpg` | never refuses; the gate question says "human review only" |

3. On pass: `data.yml` gains a `pending_releases` entry (`id`, `source`, `sha256`, `checks`,
   `gate`, `requested_at`), and a gate of the new kind **`data-release`**
   (`schemas/gates.yml`: `{requires_human: true, help: "Release a checked aggregate from a run"}`)
   is opened with options `[release, reject]` and a question that names the source, sha256 and
   check summary.
4. `gates.answer` hook for kind `data-release` (runs before the gate is marked answered, like the
   review hook): only `actor == "human"` can reach it (the kind requires a human). On `release`:
   find the pending entry by gate id in `data.yml`; recompute the source sha256 and re-run the
   checks — a mismatch or a failed check raises `GateError` and the gate stays open; copy the
   source to `released/<id>/<basename>`; move the entry to `released` with `approved_by: human`,
   `approved_at`; emit `data.released` (`id, sha256, outcome: released`). On `reject`: drop the
   pending entry, emit `data.released` with `outcome: rejected`.

`scieflow data import <dest> --from <src> --release <id>` (refused inside a dispatch): the `src`
`released` record must exist and `src/released/<id>/<file>` must hash to its sha256; the file is
copied to `<dest>/inputs/released/<src>-<id>/<basename>`; `<dest>/data.yml` gains an `imports`
entry; `data.imported` is emitted in `<dest>`. An imported artifact is `L1.5`.

### 5.9 Tabular de-identification (`scieflow data ingest`)

`scieflow data ingest <slug> <source> --out <run-relative path> [--profile generic|redcap]
[--id-col record_id] [--date-anchor COL] [--drop COL]… [--keep-free-text]`:
- Refused inside a dispatch; refused for `--profile imaging|eeg` with exit 2 and a pointer to the
  docs (M-B-D11). `--out` must be inside the run, not existing, not under `released/`.
- Reads the CSV with `csv` (dialect sniffed, UTF-8 with BOM accepted). The source is never copied
  into the run.
- Drops: every `IDENTIFIER_COLUMNS` match, the policy's `ingest.extra_drop`, `--drop` columns,
  and always `rc`/`rodne_cislo`; free-text columns (5.6 rule) unless `--keep-free-text`; with the
  `redcap` profile also `redcap_survey_identifier` and `*_timestamp` columns are treated as dates.
- Dates: a column where ≥ 90 % of non-empty values match ISO `YYYY-MM-DD[ HH:MM[:SS]]` or Czech
  `D. M. YYYY` is converted to integer days since that row's `--date-anchor` value. Date columns
  without `--date-anchor` → refused, naming the columns. The anchor column is dropped afterwards.
- Pseudonyms: the `--id-col` value becomes `hmac_sha256(salt, value)[:16]` (hex). The salt is
  `~/.config/scieflow/salts/<project-dir-name>.salt` (32 bytes from `secrets`, created mode 0600,
  directory 0700; a module constant `SALTS_OVERRIDE` lets tests point elsewhere, like
  `sandbox.CREDENTIALS_OVERRIDE`). A salt file that is a symlink, not 0600, or not 32 bytes is
  refused.
- Writes the output CSV and `<out>.ingest.yml`: `source_name` (basename), `source_sha256`,
  `source_mtime`, `scieflow_version`, `rows_in`, `rows_out`, `columns_kept`, `columns_dropped`
  (name + rule), `dates_converted`, `id_column`, `salt_id` (first 8 hex of sha256 of the salt;
  never the salt).
- Declares the output `L2` (`kind: tabular`, `reason: "ingest output"`, `declared_by: human`) and
  emits `data.ingested` (counts only).

### 5.10 CLI (`scieflow data`, D7)

New lazy group `data` (`src/scieflow/cli.py` `GROUPS`). Every subcommand has `--json`. Errors name
the path and the rule, never a value. Exit codes: 0 ok, 1 refused or check failed, 2 usage or not
available, 3 invalid policy file.

| Command | Writes | In a dispatch |
|---|---|---|
| `data declare <slug> <glob> --level L0\|L1\|L2 [--kind K] --reason R [--as-agent] [--remove]` | `data.yml`, `data.declared` | refused |
| `data levels <slug>` | nothing | allowed |
| `data scan <slug>` | `data.warning` events, dedupe file | allowed |
| `data release <slug> <artifact> …` | `data.yml`, gate | refused |
| `data import <dest> --from <src> --release <id>` | dest `data.yml`, file copy | refused |
| `data ingest <slug> <source> --out …` | output, manifest, `data.yml` | refused |
| `data audit <slug>` | nothing | allowed |
| `data pin <agent>` | nothing | allowed |

- `declare`: a human may add, change, lower or `--remove` an entry (lowering or removing needs
  `--reason`; the event records `lowered: true`). `--as-agent` may only add a new entry or raise
  `default_level` (`--default`), never change or remove one. After any change that leaves L2 anywhere in the project while `enforcement` is
  `warn`, it prints `suggestion: set enforcement: refuse in config/data-policy.yml (ScieFlow never
  sets it)` (D23).
- `levels`: run level, `default_level`, declarations, enforcement, the suggestion, the policy
  path, the boundary of every agent assigned in the run (with reason), pending/released/imports
  counts.
- `audit`: declarations, releases (each released file re-hashed: mismatch → exit 1), imports,
  `job.refused` events with `reason: data-level`, `data.warning` count.
- "In a dispatch" means `scieflow.integrations.scite.mcp.in_dispatch()` is true.

### 5.11 Events and exit codes

- New event types (additive): `data.declared`, `data.level_resolved`, `data.warning`,
  `data.released`, `data.imported`, `data.ingested`. `job.refused` gains `reason: data-level`.
- New exit code **78** for `scieflow agent run` (`agent_run.DATA_EXIT`), documented in
  `docs/cli.md`'s exit-code table.

### 5.12 Web (D7: interface only)

`service._PANELS` gains `"data": _data_panel` — a private builder returning `{reason, level,
enforcement, declarations, warnings, released, imports, suggestion}` (it never raises for an
ordinary state, like the other panels). `run.html` renders one line: the level, "safe agents
only" for L2, the warning count and the suggestion. `data-release` gates appear on the dashboard
through the existing gate list. `data.*` events appear on the timeline through the existing event
rendering. No logic in the web layer.

### 5.13 Docs

- `docs/data-protection.md` (nav "Data protection", after "Remote execution"): levels; declaring;
  the policy file reference; setting up a safe agent (an e-INFRA example and a local Ollama example,
  both written as *your* entries); the dispatch rule table (5.4) with every rule id; sandbox hiding;
  scan rule ids; release/import; ingest; exit codes; **the egress table** (destination, what
  leaves, when, control, behaviour in an L2 run) with one row per `providers.CATALOGUE` key and for
  scite, NotebookLM, remote push, remote fetch, agent dispatch, DVC push, chats push and news;
  honest limits (5.15).
- `docs/sandbox.md`: what a cloud dispatch no longer sees; the two new verify proofs.
- `docs/cli.md`: exit code 78; a `scieflow data` section.
- `SECURITY.md` (repo root): what ScieFlow protects and what it does not (sandbox, open network,
  data levels, third-party CLIs), how to report a vulnerability privately (a private GitHub security
  advisory on the repository or the maintainer directly; never a public issue).
- `PRIVACY.md` (repo root): ScieFlow sends no telemetry; every destination is in the egress table;
  the user decides what data enters and declares it (D9); salts and credentials stay in the home
  folder.

### 5.14 Threats and how M-B answers them

| Threat | Answer | Residual |
|---|---|---|
| Cloud agent dispatched into an L2 run | `l2-cloud`, exit 78, on all three paths | — |
| Cloud agent reads another run's L2 data through the read-only project bind | whole L2 run hidden; `verify()` proves a marker inside it is absent | an L2 declaration made *during* a running dispatch takes effect at the next dispatch |
| Agent lowers its own run's level | `data.yml` read-only in every dispatch; `--as-agent` cannot lower | an unsandboxed process (the human's coordinator) can edit files — same trust as `config/` |
| Agent forges a release | release only via a human-answered gate hook; source re-hashed at answer time; records in read-only `data.yml`; import re-hashes | — |
| Agent swaps a safe agent's command for a cloud CLI | pin over the final templates; overrides make the agent cloud | the human can re-pin a wrong command |
| Safe launcher script edited by an agent | `launcher-writable` | a binary outside writable areas is trusted as installed |
| L2 query text sent to a literature service | Gateway/scite/NotebookLM L2 rules | sandbox network is open (B6): a hostile agent can bypass ScieFlow's own clients |
| L2 files pushed to an unapproved remote | `cmd_push` rule | `git-push` of a remote repo is not data from the run |
| Undeclared patient data | scan warnings; `refuse` per project | heuristics miss what they do not know (D9) |

### 5.15 What M-B does not do (stated in the docs)

It cannot know what a file holds (D9). It cannot prove a third-party CLI's traffic. The sandbox
network stays open until B6. Imaging/EEG de-identification, a `level:` in the input manifest,
refusing DVC/chat backups of L2 runs, and per-file taint tracking are not in M-B.

---

## Part B — Implementation plan

## 6. Tickets

Staffing:
- Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`).
- Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`).
- Provider: anthropic. A usage limit is a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network call to any real service (all HTTP and
SSH is faked), no push/PR, no credentials, no `INFLIGHT` edit, no DD1-sealed file, no
`config/sandbox.yml` edit, no real patient data (test data is synthetic and obviously fake).

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from `$WT=RUN/scratch/m-b-wt`:
  `cd $WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=$WT/src:$WT:$WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp RUN/scratch/m-b/pt <paths>`.
  Once per card, run the import-origin check first:
  `env PYTHONPATH=$WT/src:$WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `$WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-b/junit RUN/tools/g6_pytest.sh $WT <head-sha> m-b-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path.
- **Sealed probes:** `… g6_pytest.sh $WT <head> m-b-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py` by path (as M-A T6 / M-E did; 201 passed there).
- **Load-flake nodes** (one rerun, both results recorded): the four nodes named in the installer's
  standing rules. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-b/executions.jsonl` and states its RED and GREEN evidence in its result.
- Sandbox tests use the real `bwrap` (present on this host, `/usr/bin/bwrap`, and used by the
  existing `tests/core/test_sandbox_verify.py`).

### t0 — worktree, plan and spec copy, green base (Haiku)

1. Section 2: fetch, the three ancestor checks, create the worktree and branch at `base`.
2. **Green-base gate on `base`** (HEAD = base, clean tree): full default suite **0 failed,
   0 errors**; sealed probes 0 failed. Expected on this tree (M-E final review r2 on the identical
   tree `9eeb4fa`): about 5033 passed, 21 skipped; sealed 4 + 201 passed. These are expectations;
   t0 records what it measures. Only the load-flake rule may clear a failure; otherwise verdict
   `BASE_RED` with the failing node ids and junit paths, and nothing else is done.
3. Re-verify every file:line cite of section 4 on `base`; write `reviews/m-b-t0-baseline.md` with
   each cite marked "still present", "moved (new line)" or "changed".
4. Copy `reviews/m-b-plan.md` byte for byte to
   `docs/superpowers/plans/2026-10-25-m-b-data-protection.md`, and its Part A (from the line
   `## Part A — Spec` up to, not including, `## Part B — Implementation plan`) to
   `docs/superpowers/specs/2026-10-25-m-b-data-protection-design.md` with this first line:
   `# M-B data-protection model — design (Part A of docs/superpowers/plans/2026-10-25-m-b-data-protection.md)`
   and one blank line. Commit both alone (`docs(plans): M-B data-protection spec and plan`).
5. Result: `head` (the docs commit), `base`, the baseline counts, `inflight_guard_exit`.
   Verdicts `DONE` | `BASE_RED`.

### t1 — levels, policy, boundary, `data declare|levels|pin`, events (Haiku) → t1r (Sonnet)

Files: `src/scieflow/core/data/{__init__,levels,policy,cli}.py`, `src/scieflow/cli.py` (GROUPS),
`src/scieflow/core/events.py` (TYPES), `config/data-policy.example.yml`, `.gitignore`,
`tests/core/data/{test_levels,test_policy,test_boundary,test_data_cli}.py`, the two event pins
(section 3).

RED → GREEN (each test written and shown failing first):
1. `run_level`: no file → L0; max over entries and `default_level`; imports → at least L1.5;
   malformed YAML, unknown key, unknown level, `..`/absolute/empty path → L2 with a reason.
2. `declare` (human): adds an entry with `declared_by: human`, emits `data.declared`
   (`path, level, actor, lowered`); lowering or `--remove` without `--reason` → exit 1; with it →
   done, `lowered: true`.
3. `declare --as-agent`: a new L2 entry → ok; changing, lowering or removing an entry → exit 1,
   file unchanged; raising `--default` → ok, lowering it → exit 1.
4. Every write subcommand refuses inside a dispatch (`SCIEFLOW_DISPATCH=1`) with exit 1 and
   `data.yml` unchanged.
5. Policy loader: absent → defaults; each key's validation (unknown key, missing reason, endpoint
   scheme rules incl. `http://127.0.0.1:11434` accepted and `http://example.org` refused, safe agent
   with an endpoint not in `safe_endpoints`, unknown `l2_providers` name, non-token remote, bad
   `min_cell`, bad `enforcement`, bad `project_levels` entry) → `PolicyInvalid` naming the key.
6. `boundary`: one case per row of the 5.3 table (fixture registry entries; a run override of
   `cmd` → `pin-mismatch`; a launcher token `{root}/workspace/r1/tools/x.py` → `launcher-writable`).
7. `data pin <agent>` prints the sha256 that makes `boundary` return `safe`; `data-policy.yml`
   bytes unchanged.
8. `data levels --json` shape (5.10), including `suggestion` iff some L2 exists and enforcement is
   `warn`; the declare suggestion line.
9. `events.TYPES` contains the six `data.*` types; the two pins updated additively; every other
   event type unchanged.
10. `config/data-policy.example.yml` loads with no active `safe_agents`, `safe_endpoints`,
    `l2_remotes`, `l2_providers` or `project_levels`; `.gitignore` lists `config/data-policy.yml`.
11. No-writer test: run every `scieflow data` subcommand implemented so far against a project with
    a policy file and assert the policy file's bytes are unchanged (extended in t5).

t1r checklist: fail-closed reading; agent cannot lower; no code writes `data-policy.yml`; messages
carry no values; boundary computed on final templates; INFLIGHT guard exit 0; only the two named
existing assertions changed.

### t2 — dispatch decision and refusal on the three paths (Haiku) → t2r (Sonnet)

Files: `src/scieflow/core/data/dispatch.py`, `src/scieflow/core/agent_run.py`,
`src/scieflow/core/service.py` (`dispatch_agent`, `_say_prepared`),
`tests/core/data/test_dispatch.py`, `tests/core/data/test_dispatch_paths.py`.

`decide` implements 5.4 except the scan rules (`scan-warning`/`scan-incomplete` arrive in t4 —
until then the scan hook returns no findings) and `data_hidden`/`protected` are computed but not yet
passed to the sandbox (t3). Endpoint substitution: `_template_token` gains `endpoint`;
`build_argv`/`_template_argv` take `endpoint=""`; `resolve_turn`/`ResolvedTurn` carry it to
`prepare_turn`.

RED → GREEN:
1. CLI path: `scieflow agent run --role loop.experiment stub <prompt> <transcript>` in `r1` after
   `data declare r1 inputs/x.csv --level L2` → exit 78; transcript and stderr name `l2-cloud`;
   `job.refused` with `reason: data-level, rule: l2-cloud, level: L2, agent: stub`; no job record
   created (jobs list unchanged).
2. Service path: `service.dispatch_agent` → `ServiceError`, same event, no job.
3. Conversation path: `service.say` with the fixture conversation agent in an L2 run →
   `ServiceError`, same event.
4. A safe fixture agent (policy entry, pinned, `{endpoint}` in `cmd`, launcher = `{python}` + a
   script under `tests/fixtures/`) in an L2 run → dispatched (sandboxed run with the existing
   fixture credentials); its argv contains the endpoint; `data.level_resolved` has
   `boundary: safe`.
5. `{endpoint}` in a cloud agent's template → `endpoint-without-safe-entry`, exit 78.
6. `--no-sandbox` cloud dispatch in an L0 run while another run is L2 → `l2-unsandboxed`, exit 78;
   the same with no L2 anywhere → dispatched as today; an L2 `project_levels` path also triggers it.
7. L1 run, cloud, no gate → dispatched, one `data.warning` `l1-external-sharing`; with
   `enforcement: refuse` → exit 78; with a human-answered `external-sharing` gate answered
   `approve` → no warning; an agent-answered or open gate does not count.
8. Malformed `config/data-policy.yml` → `policy-invalid`, exit 78, on the CLI path.
9. A role or run override that changes the safe agent's `cmd` in an L2 run → `l2-cloud` (reason
   `pin-mismatch` in the message).
10. Existing dispatch tests unchanged and green (`tests/core/test_agent_run.py`,
    `test_service.py`, `test_role_required.py`, `test_conversation.py`, `test_converse_cli.py`).

t2r checklist: refusal happens before any job/launch and before any write except the event; no
path where `DataRefused` is swallowed or turned into a generic error without the event; endpoint
filled only for safe agents; decision on final templates; exit codes 75/77/124 unchanged; INFLIGHT
guard exit 0.

### t3 — sandbox hiding, read-only protection, verify proofs (Haiku) → t3r (Sonnet)

Files: `src/scieflow/core/sandbox.py`, `src/scieflow/core/agent_run.py` (pass decision sets to
`dispatch_layout` on both paths), `src/scieflow/core/data/dispatch.py` (only if needed),
`tests/core/test_sandbox_data.py`.

RED → GREEN (real bwrap where stated):
1. `Layout(data_hidden=…, protected=…)` validation like the other tuple fields; defaults `()`;
   every existing construction unchanged.
2. `wrap` argv golden checks: `data_hidden` entries emitted with `hidden` (one per path, deduped,
   before the first writable `--bind`); `protected` as `--ro-bind p p` after the masks and before
   the home bind; symlink / missing / outside-grant refusals for `protected`.
3. Real bwrap: run `A` declared L2 holds `inputs/x.csv`; a cloud fixture dispatch in run `B` whose
   command lists `workspace/A` and tries to read the file → listing empty, read fails; `verify()`
   passes on that layout.
4. Real bwrap: a layout whose `wrap` drops `data_hidden` (monkeypatched) → `verify()` raises
   `SandboxUnavailable` (the marker inside `A` is seen); on the CLI path that is exit 77 +
   `job.refused reason=sandbox`.
5. Real bwrap: an L2 `project_levels` directory (a `readable:` grant in the fixture
   `config/sandbox.yml`) is empty for a cloud dispatch and visible for the safe fixture agent.
6. Real bwrap: `data.yml` and `released/` of the run cannot be written, renamed or removed from
   inside (fixture command attempts each; host bytes unchanged); `verify()` proves `protected` not
   writable; a leaky layout (protected dropped) → `SandboxUnavailable`.
7. The conversation path's layout carries the same sets (`resolve_turn`), and its verify
   receives them (extend the pattern of
   `test_s5_the_turn_verify_receives_the_mask_the_launch_receives`).
8. `verify` removes every marker it created inside other runs (also on a refused path).
9. All existing `tests/core/test_sandbox*.py` green, no assertion changed.

t3r checklist: ordering of bwrap arguments (hidden before writable; protected after masks, before
home); verify proofs real (a negative control exists for each); no host marker left behind; no
new writable path; `config/sandbox.yml` untouched; INFLIGHT guard exit 0.

### t4 — scan, pre-dispatch warnings and refuse mode, egress rules (Haiku) → t4r (Sonnet)

Files: `src/scieflow/core/data/scan.py`, `src/scieflow/core/data/dispatch.py` (scan hook),
`src/scieflow/core/data/cli.py` (`scan`), `src/scieflow/research/lib/providers.py`,
`src/scieflow/integrations/scite/gateway.py`, `scripts/remote/remote.py` (`cmd_push`,
`cmd_fetch`, passing `root`), `scripts/nblm/nblm.py` (`main`), tests
`tests/core/data/test_scan.py`, `tests/research/test_gateway_data.py`,
`tests/integrations/test_scite_data.py`, `tests/test_remote_data.py`, `tests/test_nblm_data.py`.

RED → GREEN:
1. One synthetic fixture per scan rule of 5.6 → exactly that finding (path, rule, detail); a
   clean aggregate CSV → none.
2. Bounds: `max_files`, `max_bytes_per_file`, `max_seconds` (injected clock) → `truncated`.
3. Values never leak: a planted unique value is absent from stdout, `--json`, events and the
   dedupe file.
4. Pre-dispatch (CLI path): cloud dispatch into an L0 run with a REDCap-shaped CSV → dispatched,
   one `data.warning`; a second dispatch emits no duplicate; `enforcement: refuse` → exit 78
   `scan-warning`; a truncated scan under `refuse` → `scan-incomplete`; a safe dispatch is not
   scanned.
5. Gateway: L2 run → every `CATALOGUE` provider refused before the (fake) session is called;
   listed in `l2_providers` → allowed; unscoped Gateway with `UV_CACHE_DIR=<root>/workspace/<L2
   run>/.uv-cache` → refused; L0 run and unscoped outside a dispatch → unchanged.
   `scieflow research search … --workspace <L2 run>` → exit 3.
6. scite: `gateway.request` for an L2 run → `PolicyError` before cache and transport; `scite`
   listed → passes.
7. Remote: push from an L2 workspace to a remote not in `l2_remotes` → exit 3, zero transport
   calls; to a listed remote → pushed; source under `released/` → pushed. Fetch from a listed
   remote into an L0 run → `data.warning l2-remote-fetch`, fetched; under `refuse` → exit 3, zero
   transport calls. All existing `tests/test_remote_*.py` green and only gaining lines.
8. NotebookLM: `fetch`, `upload`, `verify`, `ask` with an L2 `--workspace` → exit 3 before any
   session or download; `notebooklm` listed → proceeds to the existing (faked) path.
9. Invalid policy → every rule above refuses (exit 3 / 78).

t4r checklist: no value in any output; scan bounded and deterministic; refuse mode does not trust
the dedupe file; each egress check sits before cache/transport/session; existing remote and nblm
tests only gain lines; INFLIGHT guard exit 0.

### t5 — release, import, ingest, audit, `data-release` gate (Haiku) → t5r (Sonnet)

Files: `src/scieflow/core/data/{release,ingest,cli}.py`, `src/scieflow/core/gates.py` (hook),
`schemas/gates.yml` (one kind), tests `tests/core/data/{test_release,test_import,test_ingest,
test_audit}.py`; extend the no-writer test (t1 item 11).

RED → GREEN:
1. `data-release` kind exists with `requires_human: true`; the other 15 kinds unchanged.
2. Each release check of 5.8 on a synthetic file → refused with that check id (report names
   column and rule, no value); a clean aggregate with `n` ≥ `min_cell` → passes; a PNG → passes with
   `not-inspectable`; no count column and no flag → `counts-unknown`.
3. Flow: `data release` → pending entry + open gate; `gate answer … --as-agent` refused; human
   `gate answer … release` → `released/<id>/<file>` byte-identical, `released` record,
   `data.released outcome: released`; source changed after opening → answer refused, gate still
   open; `reject` → pending entry removed, `outcome: rejected`.
4. Import: copies and records; a tampered released file → exit 1, nothing copied; a missing record
   → exit 1; dest level becomes at least `L1.5`, never L2.
5. Ingest on a synthetic REDCap-shaped CSV: identifiers and `rc` dropped; free text dropped;
   `--keep-free-text` keeps it; dates → days since anchor; date columns without `--date-anchor` →
   exit 1 naming the columns; pseudonyms deterministic for the same salt and different for another;
   salt created 0600 under `SALTS_OVERRIDE`, never inside the run, its bytes in no output; manifest
   fields; output declared L2; `data.ingested`; `--profile imaging` → exit 2; inside a dispatch →
   exit 1.
6. Audit: lists everything; a modified released file → exit 1 naming it.

t5r checklist: release only through the human hook; re-hash at answer time; no symlink or
out-of-run path accepted anywhere; salt handling; no value in messages; INFLIGHT guard exit 0.

### t6 — docs, web panel, AGENTS.md rule 18 (Haiku) → t6r (Sonnet)

Before t6 the operator applies the protected AGENTS.md edit (section 3) to the worktree,
uncommitted. t6 first checks that `git -C $WT diff` shows exactly that edit and nothing else, and
commits it unchanged as its own commit (`docs(agents): rule 18 data protection (human-approved
protected edit) (M-B t6)`). If the diff differs, verdict stays DONE only after reporting the
difference in `deviations` and committing nothing of it — never edit AGENTS.md yourself.

Files: `docs/data-protection.md`, `docs/sandbox.md`, `docs/cli.md`, `mkdocs.yml`, `SECURITY.md`,
`PRIVACY.md`, `src/scieflow/core/service.py` (`_PANELS`), `src/scieflow/web/templates/run.html`,
tests `tests/test_docs_data_protection.py`, `tests/core/data/test_panel.py`.

RED → GREEN:
1. Docs test: the egress table in `docs/data-protection.md` has a row naming every
   `providers.CATALOGUE` key and `scite`, `notebooklm`, `remote push`, `remote fetch`,
   `agent dispatch`, `DVC`, `chats`, `news`, each with a non-empty L2 column; every dispatch rule
   id (5.4), scan rule id (5.6) and release check id (5.8) appears; `mkdocs.yml` nav lists
   `data-protection.md`; `docs/cli.md` exit table has `78`; `SECURITY.md` and `PRIVACY.md` exist
   and link `docs/data-protection.md`; `AGENTS.md` contains `18. **Data protection**`.
2. `service.run_overview(project, "r1")["data"]` has the 5.12 keys; L2 run → `level: "L2"`;
   a broken `data.yml` → `level: "L2"` with a `reason` (no exception); the run page HTML contains
   the level and, for L2, "safe agents only".
3. `mkdocs build --strict` passes if mkdocs is in the venv (else recorded "not available").
4. The whole new test set of t1–t5 still green.

t6r checklist: docs match code (every rule id, exit code, file name); no claim of tested live
safe-agent traffic; honest limits present; AGENTS.md commit equals the approved text byte for byte;
INFLIGHT guard exit 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip:
- full default suite 0 failed / 0 errors; absolute counts and the delta against t0, each
  difference explained by added tests;
- sealed probes as in t0: 0 failed;
- `mkdocs build --strict` into `RUN/scratch/m-b/site` (or "not available");
- INFLIGHT guard over `base..tip`: exit 0;
- linear history: no merge commits, at least one commit per ticket;
- existing-test integrity: `git diff base..tip -- tests/` has removed lines only in the two
  event-vocabulary pins of section 3;
- `config/sandbox.yml`, the DD1-sealed files, `CLAUDE.md` absent from the diff; `AGENTS.md`
  changed only by the rule-18 commit.

Write `RUN/scratch/m-b/pr-body.md` in plain English: what M-B adds, decisions M-B-D1..D18, the
absolute test counts, the residuals (section 8), and "please merge with a merge commit"; no
credentials, prompts or patient data. Write `reviews/m-b-branch-report.md`. Any failure →
verdict `RED` with the node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

A fresh card that did not review t1–t6 where staffing allows; the whole branch `base..tip`:
- **Spec:** every item of Part A and every M-B acceptance criterion of the GAP row:
  cloud dispatch into a declared-L2 run refused with the new code and a `job.refused` event;
  safe dispatch allowed; `verify()` proves an L2 path absent in a cloud sandbox; the Gateway
  refuses live hosts for an L2 run; `release` refuses a table with a cell < `min_cell`; `scan` flags
  a REDCap-shaped CSV with a warning and refuses under `enforcement: refuse`; the docs test for the
  egress table. Decisions M-B-D1..D18 honoured.
- **Security:** the 5.14 table — refusal before launch on all three paths; no lowering by agents;
  read-only `data.yml`/`released/`; release only through the human hook; pin over final templates;
  egress checks before transport; fail-closed parsing; no value in any message; no code writes
  `config/data-policy.yml`.
- **Evidence:** re-run memory-capped on the tip `tests/core/data`, `tests/core/test_sandbox_data.py`,
  `tests/core/test_sandbox*.py`, `tests/test_remote_*.py`, `tests/test_nblm_*.py`,
  `tests/research/test_gateway_data.py`, `tests/integrations/test_scite_data.py`,
  `tests/test_docs_data_protection.py`; confirm the branch step's counts; INFLIGHT guard;
  `pr-body.md` exists and is accurate.

Output `reviews/m-b-final-review-r<round>.md`, line 1 `VERDICT: PASS|CHANGES|FAIL`. PASS → the
installer's `publish` step; CHANGES → `final_fix` (Haiku: new commits, the full default suite and
sealed probes re-run on the new tip), then back to `final`; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-b-run.yml`, linear)

```
t0        Haiku   worktree + plan/spec copy + green base   DONE→t1, BASE_RED→escalate
t1        Haiku   levels, policy, boundary, CLI            DONE→t1r
t1r       Sonnet  review                                   PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                        DONE→t1r
t2/t2r/t2f        dispatch decision on three paths         same shape → t3
t3/t3r/t3f        sandbox hiding, protection, verify       same shape → t4
t4/t4r/t4f        scan, warnings, egress rules             same shape → t5
t5/t5r/t5f        release, import, ingest, audit           same shape → t6 (installer inserts pe_t6)
t6/t6r/t6f        docs, web panel, AGENTS.md rule 18       same shape → branch
branch    Haiku   full suites + PR body                    DONE→final, RED→escalate
final     Sonnet  whole-branch review                      PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                        DONE→final
(pe_t6, publish, chain: added by tools/milestone_chain.py install)
```

- Caps: `max_cards: 70, bad_result_retries: 1, stall_minutes: 20, question_cards: 3`.
- Results go to `pipelines/results/m-b-run/<step>-r<round>.json`. Every step requires
  `verdict, summary, head, base`. Implementation, fix, branch and final cards also report
  `inflight_guard_exit`.
- Protected edits: `pipelines/drafts/m-b-protected-edits.json`, one edit, `before_step: t6`.

## 8. Risks and residuals (stated, not hidden)

- **The sandbox network is open (A-7, B6).** A hostile agent that can read data can send it. M-B
  stops cloud agents from *reading* L2 data and stops ScieFlow's own clients from sending L2-run
  queries; it does not stop a safe agent's CLI from sending anywhere.
- **Safe agents are trusted.** ScieFlow proves the endpoint it handed the CLI, the pinned
  command and that the launcher is outside agent-writable folders, not the CLI's traffic.
- **Declarations are the user's (D9).** Undeclared data only warns unless the project sets
  `refuse`; the scan heuristics are heuristics.
- **Timing.** A declaration made while a dispatch runs applies to the next dispatch; a file added
  to another run after a layout was built is covered because the whole run directory is hidden.
- **Unsandboxed actors** (the human, the coordinator agent the human talks to) can edit `data.yml`
  and `config/`. That is the same trust boundary as `config/` today.
- **Imaging/EEG de-identification, manifest levels, DVC/chat backup refusal** are not in M-B
  (M-B-D11, D13, D14).
- **`kimi-einfra` (in-flight)** will be `cloud` until its launcher moves out of `workspace/` and it
  is pinned (M-B-D3, A-13).
- **Dispatch cost.** The pre-dispatch scan adds up to `max_seconds` per cloud dispatch; the layout
  reads every run's `data.yml`.

## 9. After M-B

Queue order (`continuity/milestones.yml`): the next entry is **M-C1 Literature kernel I** (L: its own
spec and plan, then approval). Its `network: offline|replay|live` setting should default to
`replay`/offline for L2 runs (GAP §5.2 "Providers" row). Follow-ups surfaced here, not part of M-B:
- imaging (pydicom/defacing) and EEG (MNE) de-identification as containerised stages;
- an optional `level:` in `inputs/manifest.yml` once the in-flight `core/run/validate.py` lands;
- DVC/chat backup rules for L2 runs;
- the B6 network allow-list proxy, which would let L2 dispatches be network-confined to the safe
  endpoint.
