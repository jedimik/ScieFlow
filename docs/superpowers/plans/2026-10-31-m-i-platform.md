# M-I plan: platform — CI test gate, read-only MCP server, event and job schemas (round 2)

Pipeline `m-i-plan`, step plan, round 2, epoch 1. Card `t_3f9e5849`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261031_154715_d3471f`). Round 1 was
card `t_02757d67`. Written 2026-10-31. Previous review: `reviews/m-i-plan-review-r1.md` (CHANGES, one
blocking finding; closed in this round, see "Round-2 changes" below). Size M: one plan (spec in Part A, implementation plan in
Part B); t0 copies it to `docs/superpowers/plans/2026-10-31-m-i-platform.md`. No separate spec file
(that is for L milestones).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no network call
except the read-only `git ls-remote` that fixes the reference main.

## Round-2 changes (review r1 closed)

- **Finding 1 (blocking) — closed.** `open_gates` without `slug` now runs M-B's cloud-agent decision for
  every run it would list (not only the L2 level check) and leaves out every run whose decision is a
  refusal (L1 without sharing under `enforcement: refuse`, an invalid policy, a non-cloud boundary),
  counting it in a `hidden_runs` field without naming it; runs allowed with warnings carry their warnings
  per run. It calls `service.open_gates(project, slugs=<allowed>)` (`src/scieflow/core/service.py:1100-1123`)
  so no refused run's gate or charter-proposal text (`_with_proposal_preview`, `:1224-1227`) is ever read.
  Changed: M-I-D12, section 1 item 3, section 4 (data protection), 5.5 (`open_gates` row and payload),
  5.6 item 3 and new item 4b, 5.8 item 5, t0 inventory (i) (`list_runs` is metadata only), the t5 fixture
  and RED/GREEN items 2 and 3 (new aggregate cases), the t5r checklist, the final review list, and the t0,
  t5, t5r and final briefs.
- **N1 (non-blocking) — taken.** The t4 AST check bans the top-level module `mcp` exactly (`import mcp`,
  `import mcp.x`, `from mcp import …`, `from mcp.x import …`), not names that merely contain `mcp`, so
  `from scieflow.integrations.scite.mcp import in_dispatch` stays allowed. t4 item 7 and the t4/t4r briefs
  say so.
- N2–N4: no change needed (the reviewer agreed).

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
| `continuity/milestones.yml` (not pinned) | m-i entry line 27: `after: [m-c1]` |

Scope source: GAP-ANALYSIS.md section 8 row **M-I** ("CI test workflow, MCP read-only server over service,
events/jobs schema validation, CHANGELOG/CITATION.cff"; acceptance "CI green on a PR; MCP tools listed and
callable with the stub agent; invalid event refused"; depends on M-A, "MCP after tickets 13–15 designs if
they overlap"; conflicts `.github/`, `core/events.py`), section 2 rows **#19** (CI gate structure) and **#20**
(MCP server over the service layer, read tools first), audit finding **A-11** (no CI test gate,
GAP-ANALYSIS.md:117), and the D-platform report row D28 (events/jobs schema, validation in `events.emit`).
**D22** fixes the CI gate: "ubuntu only, `-m 'not slow and not live'`, sealed probes excluded, no live
canaries". D7 (CLI parity) and D9–D11/D23 (data protection) apply to the MCP server. The approved M-A plan
assigned to M-I the read-only views behind the five `allow: … (M-I)` entries of the CLI parity table
(`reviews/m-a-plan.md` sections 5 T5 and 8). Where GAP text and DECISIONS differ, DECISIONS win.

Code read read-only from git objects (`git show`, `git grep`, `git log`); no checkout was touched. `M` =
remote main = `8b374803c2eb6cad0df981d466ba097b973f3c84` (`git ls-remote
git@github.com:jedimik/ScieFlow.git refs/heads/main`, 2026-10-31; the M-C2 merge commit, PR #24). Every
`path:line` below is on `M`.

**The milestone can start: its prerequisite M-C1 is merged** (`RUN/scratch/m-c1/publish.json`
`"merged": true`, PR #20; M-A, M-E, M-B, M-D1, M-D2, M-F and M-C2 are merged too). (Verdict DONE.)

Lane note: `continuity/milestones.yml` now has `parallel: any`. `m-d3-run` is running (t0) and edits
`src/scieflow/core/jobs.py`, `src/scieflow/core/service.py` and root `AGENTS.md` rule 11 (`reviews/m-d3-plan.md`
section 3); `m-g` and `m-h` are planning. M-I's base may therefore contain M-D3. M-I edits `core/jobs.py`
only inside `save` (section 3), never `core/service.py`, and edits a different `AGENTS.md` paragraph. M-I
does not change the event vocabulary `events.TYPES`, so milestones that add event types merge cleanly.
t0 records what the base contains.

## 1. For the human: what M-I adds, in plain words

1. **Every pull request is tested on GitHub.** Today the only GitHub workflow builds the docs
   (`.github/workflows/docs.yml`); the ~7,800 tests run only when an agent or you run them by hand. After
   M-I a workflow `Tests` runs on every pull request, on every push to `main`, and on demand: Ubuntu only,
   the normal offline test suite without the slow, live and scite-live tests and without the sealed probe
   suite — exactly your D22. It uses no secrets and only reads the repository. The command it runs is one
   script, `scripts/ci-tests.sh`, which you (or an agent) can run locally to get the same answer.
2. **Run history is checked when it is written.** Every event a run records (`events.jsonl`) and every job
   record (`jobs/<id>.json`) is checked before it is written: known type, valid actor, a proper
   `note.<name>`, plain JSON values only, and the key fields each kind of event must carry (for example a
   `job.finished` event must name its job). An invalid event is refused instead of silently written.
   `scieflow workspace doctor <run>` now also lists any line of an existing log that is unreadable or
   invalid (line number and rule; never the content). The rules are documented in `schemas/events.yml` and
   `schemas/jobs.yml`.
3. **A read-only MCP server for your interactive coordinator.** `scieflow mcp serve` lets Hermes,
   Claude Desktop or Codex read ScieFlow runs through twelve typed read-only tools (run list, run details,
   events, open gates, charter, conversation, run overview, workbench, curation history, agent settings,
   workflows, conversation-capable agents) instead of shelling out. It runs only as a local subprocess of
   your client (no network port), it writes nothing, and it never starts inside an agent dispatch. **It
   shows only what a sandboxed cloud agent would be allowed to see:** runs you declared L2 are hidden and
   refused, and the other runs pass the same data-protection check a cloud agent dispatch gets (M-B) —
   also when the client asks for the open gates of all runs at once: a run the check refuses is left out
   and only counted. All text it returns goes through the same injection sanitiser as literature search
   results.
4. **Four small terminal commands** so that everything the MCP server shows is also reachable from the
   terminal (your D7): `scieflow run overview <run>`, `scieflow run budgets`, `scieflow run workflows` and
   `scieflow agent conversational` — views that only the web app had.
5. **`CHANGELOG.md` and `CITATION.cff`** at the top of the repository: what changed on `main` since the
   0.2.0 package version (one line per merged pull request), and how to cite ScieFlow.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-I-D1 CI scope.** GitHub-hosted `ubuntu-24.04` only (the same Ubuntu as this host); triggers
  `pull_request`, `push` to `main`, `workflow_dispatch`. Marker expression
  `not slow and not live and not scite_live`: D22's expression plus `scite_live`, which is a live canary
  (one real scite call, `pyproject.toml:86`) and D22 excludes live canaries. The sealed probe file is
  excluded twice: by its conftest (`tests/probes/conftest.py:20`) and by an explicit
  `--ignore=tests/probes/test_k_task8_binding.py`. `permissions: contents: read`, no secrets, no cache, no
  uploaded artifacts, `persist-credentials: false`. Actions pinned to the same major tags as `docs.yml`
  (`actions/checkout@v4`, `astral-sh/setup-uv@v5`, `.github/workflows/docs.yml:39,41`).
- **M-I-D2 One script is the gate.** The workflow calls `scripts/ci-tests.sh`; everything CI needs
  besides system packages lives in that script, so a local run reproduces CI.
- **M-I-D3 The sandbox runs in CI.** The workflow installs `bubblewrap` and enables unprivileged user
  namespaces on the runner (`sysctl kernel.apparmor_restrict_unprivileged_userns=0`, the documented
  Ubuntu 24.04 setting), then proves `bwrap` works before the tests. Sandbox tests are never skipped or
  deselected to make CI pass.
- **M-I-D4 Merge does not wait for CI.** The operator's publish step merges right after the final review
  PASS (`tools/milestone_chain.py:634-647`); it does not wait for GitHub checks. The pre-merge evidence is a
  local reproduction of the exact CI command in a clean, shallow clone with a fresh offline-built
  environment and an empty home folder (section 5.1). The first real GitHub run is on the M-I pull request
  itself. (If you want publish to wait for the checks, that is a change to the operator's tool, not to this
  branch; say so and the operator does it separately.)
- **M-I-D5 Rules in code, documented in `schemas/`.** The validators are plain Python in a new
  `scieflow.core.records` module (no new dependency; `jsonschema` is only in optional extras,
  `pyproject.toml:17-20`). `schemas/events.yml` and `schemas/jobs.yml` are the documented form, and a test
  pins that code and files agree (the M-D2 `figure-style.yml` pattern, `src/scieflow/experiments/style.py:1`).
- **M-I-D6 Events are refused at write time.** `events.emit` keeps its two existing checks and messages
  (`src/scieflow/core/events.py:53-56`) and adds: a `note.<name>` name must match
  `[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}`; data values must be JSON (null, bool, number, string, list, object
  with string keys) after three coercions that keep today's meaning (a path becomes its text, a tuple a
  list, a number-like value such as numpy's int64 an `int`/`float`); NaN and infinity are refused; per-type
  required keys (M-I-D7). The closed type list stays `events.TYPES`, unchanged.
- **M-I-D7 Required keys only where every emitter already agrees** (table in 5.2). Types not in the table
  are checked for the envelope only.
- **M-I-D8 Jobs are checked when saved.** `jobs.save` refuses an invalid record; loading stays lenient
  (`list_jobs` already skips unreadable records, `src/scieflow/core/jobs.py:321-333`). `kind` is a
  lowercase token, not a closed list (M-D3 adds kind `remote`).
- **M-I-D9 Old logs never break reading.** Readers are unchanged; `workspace doctor` reports invalid lines
  and records.
- **M-I-D10 MCP server written with the standard library.** The official MCP SDK (`mcp`, only in the optional
  `scite` group, `pyproject.toml:63`) is not installed in the project environment and cannot be installed
  offline here (its `pydantic-core` wheel is not in the cache; checked on this card). The server speaks the
  stdio transport (one JSON-RPC 2.0 message per line) with the `initialize`, `ping`, `tools/list` and
  `tools/call` methods only. No HTTP transport, so it can never be a second network door.
- **M-I-D11 Read tools only.** Twelve tools over existing `scieflow.core.service` functions; no write
  tool. Write tools would be an exposure change and need your decision later.
- **M-I-D12 The MCP client is treated as a cloud agent.** It is some model you run, so ScieFlow cannot
  know it is safe. L2 runs are left out of the run list (only their count is shown) and every per-run
  tool on an L2 run is refused. For every other run, each per-run tool first runs M-B's dispatch decision
  for a cloud agent (`src/scieflow/core/data/dispatch.py:177-274`): refusals refuse the tool, warnings are
  returned with the result. The one tool that reads gate content across runs (`open_gates` without a
  run) runs the same decision for every run first and leaves out, and only counts, every refused run. The
  run list itself shows status metadata only (state, phase, dates — no file content) for every run that is
  not L2. No event is written (the server writes nothing). There is no way in M-I to mark an MCP client as
  safe.
- **M-I-D13 Never inside a dispatch.** `scieflow mcp serve` refuses to start when it looks like a ScieFlow
  dispatch (the existing detector, `src/scieflow/integrations/scite/mcp.py:160-175`): it could read other
  runs that the sandbox hides from a sub-agent. No `.mcp.json` is committed (it would offer the server to
  every agent CLI started in the repository). Root `AGENTS.md` gains one bullet (protected edit, section 3).
- **M-I-D14 Sanitised and bounded output.** Every tool result passes
  `scieflow.core.sanitize.sanitize_json_strings` (the M-C1 sanitiser used for search output,
  `src/scieflow/research/search/fanout.py:156`) and is capped at 1,000,000 bytes of JSON; a larger result
  is an error that tells the client to narrow the request.
- **M-I-D15 No read log.** Read-only means no writes, so the server keeps no record of what a client read.
  (Your coordinator with a shell can already read the same files; the data rule above is what limits a
  client without a shell.)
- **M-I-D16 Terminal parity (D7).** Four read-only commands for the service functions only the web app
  reached: `run overview` (`service.run_overview`), `run budgets` (`service.list_runs_with_budget`),
  `run workflows` (`service.workflows`) and `agent conversational` (`service.conversational_agents`).
  `preview_busy` stays web-only and is not an MCP tool: it repairs stale job records, which is a write
  (`src/scieflow/core/service.py:1874-1918`).
- **M-I-D17 CHANGELOG and citation.** `CHANGELOG.md` (Keep a Changelog layout) lists, under
  "Unreleased", every pull request merged on `main` since the 0.2.0 version line appeared (2026-09-16,
  commit `41a986e`), newest first, plus M-I; no version bump (a release is your call). `CITATION.cff`
  (CFF 1.2.0) names "Tomáš Krajča" as in the git history, version 0.2.0, the repository URL; no e-mail, no
  ORCID and no licence field, because the repository has no LICENSE file. You may add a licence and an
  ORCID later.
- **M-I-D18 No overlap with tickets 13–15.** Those are designs for browser modules (experiments,
  literature/Zotero, news) and are not scheduled; the MCP server only reads existing service functions and
  changes nothing in `src/scieflow/web/`. The GAP condition "MCP after tickets 13–15 designs if they
  overlap" therefore does not hold M-I back.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head `dev/m-i`
→ base `main`, one PR, merged with a merge commit. Title "M-I Platform (CI gate, MCP read-only server,
schemas)". The body (written by the branch step) lists the five changes, decisions M-I-D1..D18, absolute
test counts, the CI reproduction result and the residuals.

## 2. Base, dependencies and worktree

- **Dependencies** (`continuity/milestones.yml:27` `after: [m-c1]`; GAP row: M-A): both merged. t0
  re-checks on the real base with `git merge-base --is-ancestor`: H-4
  `6152d733728b9ed5bf5b571aef475f8071426386`, M-A `be99db2ace0e560fcdb7c00231e906e4aaae67e2`, M-B
  `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`, M-C1 `aa7c3792b0b374ccfa6868bb13018a1974ac3435`, and M-C2's
  merge `8b374803c2eb6cad0df981d466ba097b973f3c84`; and `RUN/scratch/m-c1/publish.json` with
  `"merged": true`. Any failure → verdict `BASE_RED` with the reason.
- **Worktree**: t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`, the
  object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head
  git@github.com:jedimik/ScieFlow.git refs/heads/main`, then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-i RUN/scratch/m-i-wt <base>`.
- Commits are local on `dev/m-i`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-i/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-i-<step>/`; the CI reproduction under `~/.cache/scieflow-m-i-ci/` (outside RUN, so
  no test can find the RUN layout by walking up).

## 3. In-flight, protected and changed files

**INFLIGHT** (the uncommitted paper-lifecycle work in `/home/ubuntu/Github/ScieFlow`; the same 9 paths as
M-A … M-D3 section 3). M-I edits **none** of them:

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
tree during a card, `git -C WT diff --exit-code -- <INFLIGHT>`. Note: `scieflow run log` (in-flight
`src/scieflow/core/run/cli.py:247-259`) calls `events.emit` and now gets the stricter note-name check through
it; the file itself is not edited (an invalid name there ends in a refusal, which is the intended
behaviour). The run's event documentation is in `docs/runs.md` (in-flight); M-I documents records in
`docs/architecture.md` instead.

**Never edited**: `config/sandbox.yml`, the DD1-sealed files (`tests/probes/k_task8_binding.py`,
`tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, every
`AGENTS.md` except root `AGENTS.md` through the protected edit below, `CREDITS.md`, `VENDORED.md`,
`.github/workflows/docs.yml`, `pyproject.toml`, `uv.lock`, `src/scieflow/core/service.py`,
`src/scieflow/core/data/*`, `src/scieflow/core/sandbox.py`, `src/scieflow/web/*`,
`src/scieflow/integrations/*`, `src/scieflow/_vendor/*`, `scripts/remote/*`, and the `TYPES` and `ACTORS`
sets of `src/scieflow/core/events.py:19-44`.

**Protected edit (operator-applied before t6, `pipelines/drafts/m-i-protected-edits.json`)** — root
`AGENTS.md`, Orientation, after the "Runs:" bullet (`AGENTS.md:263-265` on M; M-D3 edits rule 11 at
`:97-99`, a different paragraph). Exact text (the dashes are U+2014 and the ellipsis U+2026, as in the file):

old:
```
- Runs: `scieflow workspace list | doctor <slug>`. Commands from before the
  module merge (`scripts/agent_run.py`, `expx`, `whatsnew`, …) still work and
  print their new name — use the new one (`docs/MIGRATION.md`).
```
new:
```
- Runs: `scieflow workspace list | doctor <slug>`. Commands from before the
  module merge (`scripts/agent_run.py`, `expx`, `whatsnew`, …) still work and
  print their new name — use the new one (`docs/MIGRATION.md`).
- An interactive coordinator (Hermes, Claude Desktop, Codex) may read runs
  through the read-only MCP server `scieflow mcp serve` (`docs/mcp.md`). It
  shows only what a sandboxed cloud agent may see (L2 runs are hidden),
  writes nothing, and refuses to start inside a dispatch; every action still
  goes through the CLI. Sub-agents never use it.
```
The old text occurs exactly once on M (checked on this card; the tool refuses otherwise; t0 re-checks it
on base). t6 commits the applied file alone ("docs(agents): orientation names the read-only MCP server
(human-approved protected edit) (M-I t6)").

**Existing tests that change (and only these):**

- **E1** `tests/web/test_pages.py:55-68` (`test_run_page_escapes_a_malicious_event_type_server_side`) emits
  the type `note.<img src=x onerror=alert(1)>` through `events.emit` (`:64-65`). M-I-D6 refuses that name
  at write time, which is the point of the milestone. The test's purpose — a corrupt or hand-edited
  `events.jsonl` line must still render escaped, as its own docstring says (`:58-60`) — stays: t2 replaces
  only the `events.emit(...)` line by appending the equivalent record straight to the run's
  `events.jsonl` with `scieflow.core.store.append_jsonl` (same keys as an emitted event). Its three
  assertions are unchanged. t2 adds a separate test that `events.emit` refuses that name.
- **E2** `tests/core/cli_parity.yml` (test data of `tests/core/test_cli_parity.py`): the entries
  `list_runs_with_budget`, `workflows`, `conversational_agents`, `run_overview` (`:20-22,24`) change from
  `allow: … (M-I)` to `cli:` entries with `new: true` and `args`; `preview_busy` (`:23`) keeps `allow:` with
  the reason reworded to drop the "(M-I)" owner tag; the header comment "true when M-A T5 adds this
  command" becomes "true when M-A T5 or M-I adds this command". `test_cli_parity.py` itself is not edited.
- **E-extra** (only if the full suite proves it necessary): an existing test that builds an invalid event
  or job record on purpose for some other check, and fails only because the record is now refused, is
  converted the same way as E1 (the record written directly to the file it lives in), assertions
  unchanged; each one is listed in `reviews/m-i-test-changes.md` with node id, reason and diff. Weakening,
  skipping or deselecting a test is never allowed. A product emitter that writes an invalid event is a
  product bug: it is fixed in the emitter (and that file added to the t2 result), not hidden.

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-31-m-i-platform.md` | copy of this plan | t0 |
| `.github/workflows/tests.yml` | new: the CI gate (5.1) | t1 |
| `scripts/ci-tests.sh` | new, mode 755: the CI test command (5.1) | t1 |
| `tests/test_ci_workflow.py` | new: static D22 checks of workflow and script | t1 |
| `docs/testing.md`, `mkdocs.yml` (one nav line) | new page: tests, CI, local reproduction | t1 |
| `src/scieflow/core/records.py` | new: event and job validators, read checks | t2 |
| `src/scieflow/core/events.py` | `emit` calls the validator (TYPES/ACTORS unchanged) | t2 |
| `src/scieflow/core/jobs.py` | `save` calls the validator | t2 |
| `src/scieflow/core/workspace.py` | `doctor` reports `invalid_events`, `invalid_jobs` | t2 |
| `schemas/events.yml`, `schemas/jobs.yml` | new: documented form | t2 |
| `tests/core/test_records.py`, `tests/core/test_events_validation.py`, `tests/core/test_jobs_validation.py`, `tests/core/test_doctor_records.py` | new | t2 |
| `tests/web/test_pages.py` | E1 | t2 |
| `src/scieflow/core/run/converse_cli.py` | `overview`, `budgets`, `workflows` commands, `new_commands()` | t3 |
| `src/scieflow/core/cli.py` | `agent conversational` | t3 |
| `tests/core/cli_parity.yml` | E2 | t3 |
| `tests/core/test_read_views_cli.py` | new | t3 |
| `src/scieflow/mcp_server/__init__.py`, `protocol.py`, `tools.py`, `access.py`, `cli.py` | new package | t4, t5 |
| `src/scieflow/cli.py` | one `GROUPS` entry `mcp` | t4 |
| `tests/mcp_server/test_protocol.py`, `test_cli.py`, `test_project_tools.py` | new | t4 |
| `tests/mcp_server/conftest.py`, `test_run_tools.py`, `test_access.py`, `test_readonly.py`, `test_mcp_parity.py`, `test_mcp_e2e.py` | new | t5 |
| `docs/mcp.md`, `docs/cli.md`, `docs/architecture.md`, `mkdocs.yml` (one nav line) | docs | t6 |
| `CHANGELOG.md`, `CITATION.cff` | new | t6 |
| `tests/test_docs_platform.py` | new | t6 |
| `AGENTS.md` | Orientation bullet, operator-applied, committed by t6 | t6 |

Hotspots: `src/scieflow/core/jobs.py` (M-D3 adds functions there; M-I changes only `save`) and
`src/scieflow/core/events.py` (other milestones add types to `TYPES`; M-I leaves the set alone and reads
it at call time).

## 4. Current code, verified (on M = 8b37480)

**CI.** `.github/workflows/` holds only `docs.yml`: it builds the site with `mkdocs build --strict` on
pushes to main that touch docs (`.github/workflows/docs.yml:17-24,35-45`), `permissions` include
`pages: write` and `id-token: write` (`:26-29`), `runs-on: ubuntu-latest` (`:37`). No test workflow (A-11
holds). `pyproject.toml:75` `addopts = "-m 'not slow and not live and not scite_live' --import-mode=importlib"`;
markers at `:83-87`; dependency group `dev` at `:55`, the `scite` group (MCP SDK) at `:63`. The sealed
probe file is excluded from directory collection by `collect_ignore = ['test_k_task8_binding.py']`
(`tests/probes/conftest.py:20`), pinned by `tests/probes/test_probe_collection.py`. Tests that need system
tools: bubblewrap is checked with `shutil.which("bwrap")` in eleven test modules (for example
`tests/core/test_sandbox.py:9`), but `tests/test_service_e2e.py:21-60` dispatches the stub agent through
the sandbox without a skip; `latexmk` tests skip when it is missing (`tests/core/test_preview.py:159`);
`tests/test_remote_local_e2e.py:28` requires `bash setsid timeout rsync sha256sum find xargs` and fails,
never skips. The README install line is `uv sync --all-extras` (`README.md:16`). On this host bwrap works
with `kernel.apparmor_restrict_unprivileged_userns = 1` because of the local AppArmor profile; a fresh
runner needs the sysctl (M-I-D3). The last full default run (M-C2 branch, tip a59df13) was 7,779 passed,
22 skipped, 7 deselected, 0 failed; sealed `tests/probes` 4 passed (`RUN/scratch/m-c2/executions.jsonl`).
An offline `uv sync --frozen --all-extras --group dev --dry-run` against M's lock file resolves from the
local uv cache (checked on this card in a scratch export).

**Events.** `src/scieflow/core/events.py`: `ACTORS` `:19`, `TYPES` `:20-44` (closed list; `note.<name>`
is free-form), `emit` `:52-67` refuses an unknown type (`:53-54`, message "unknown event type …") and an
unknown actor (`:55-56`), builds `{id, ts, run, slug, type, actor, data}` (`:57-65`) and appends it with
`store.append_jsonl` (`:66`), which serialises with `default=str` (`src/scieflow/core/store.py:87-91`), so
any object is silently turned into text today. `read` (`:75-84`) uses `store.read_jsonl`, which silently
skips lines that are not JSON (`store.py:94-106`). `tests/core/review/test_gates_events.py:161-164` pins
`TYPES` exactly (M-I does not change it). `store.new_id` is a 26-character Crockford ULID
(`store.py:26-37`). Emitters: 100 `emit` call sites under `src/` (git grep on M); the per-type keys in 5.2
were read from all of them (for example `jobs._emit` always passes `job, kind, label`,
`src/scieflow/core/jobs.py:340-343`; `gates.py:94` `gate, kind, question`; `gates.py:142`
`gate, kind, answer`; every `job.refused` passes `reason`, e.g. `core/data/dispatch.py:70`,
`core/run/actions.py:61`, `experiments/cli.py:105,137,169`).

**Jobs.** `Job` dataclass `src/scieflow/core/jobs.py:33-51` (fields `id, kind, argv, cwd, label, run_dir,
state, pid, queued, started, finished, exit_code, timeout_s, log, err, sandboxed, turn_audit`),
`STATES` `:27`; `save` `:285-286` writes `asdict(job)` with no check; `load` `:289-290`; `list_jobs`
`:321-333` skips unreadable records; ids from `store.new_id()` (`:365`); kinds in use `agent`, `turn`,
`preview` (`core/service.py:258,632`, `preview.py`).

**Doctor.** `workspace.doctor` (`src/scieflow/core/workspace.py:253-301`) returns a dict of read-only
findings; `doctor_cmd` prints them (`:409-439`). Existing tests check individual keys only
(`tests/core/test_workspace.py:50-60`, `tests/integrations/scite/test_doctor.py:116`).

**Service functions the MCP tools call** (`src/scieflow/core/service.py`): `list_runs_with_budget`
`:104-126`, `run_detail` `:129-141`, `workflows` `:144-151`, `run_events` `:212-214`, `conversation_state`
`:316-341`, `open_gates` `:1100-1123`, `agent_settings` `:1265-1266`, `conversational_agents` `:1269-1281`,
`run_charter` `:1318-1328`, `workbench` `:1584-1621`, `curation_history` `:1704-1707`, `run_overview`
`:2401-2422` ("Read-only, and must never call `provenance.sync`"). Not used: `manuscript_history`
(`:1033-1070`, calls `provenance.sync`, a write), `preview_busy` (`:1874-1918`, calls `jobs.reconcile`, a
write), `preview_of`, `manuscript_diff` (buffers a whole git diff, `:1073-1097`). Errors are `ServiceError`
(`:53`). There are 44 public functions; `tests/core/cli_parity.yml` maps each (five `allow: … (M-I)`
entries at `:20-24`).

**CLI.** Root groups `src/scieflow/cli.py:14-80` (lazy; the `data` and `design` entries at `:68-79` show the
shape); `scieflow run` is assembled by `src/scieflow/core/run/converse_cli.py`: `new_commands()` `:318-330`,
`build_run_group` `:333-349` (an existing name wins, with a warning), `run = …` `:352`; helpers `_project`,
`_call`, `_show` `:32-72`. `scieflow agent` group `src/scieflow/core/cli.py:9-11`. The parity test
`tests/core/test_cli_parity.py` checks the table keys, resolves every `cli:` path, and invokes each
`new: true` command with the service function monkeypatched to a recorder.

**Data protection.** `dispatch.decide(project, run_dir, agent, final_cfg, *, sandboxed,
templates_use_endpoint)` (`src/scieflow/core/data/dispatch.py:177-181`) is "pure apart from reading files";
for a cloud agent it refuses an L2 run (`l2-cloud`, `:212-215`), handles L1 without an approved
external-sharing gate (`:226-233`), runs the scan (`:235-244`), and refuses on an invalid policy
(`policy-invalid`, `:190-193`). `levels.run_level(ws)` returns the run's level, `L2` when `data.yml` is
invalid (fail closed; `src/scieflow/core/data/levels.py:166-178`). Events are written only by
`record_refusal`/`record_decision` (`:66-89`), which the MCP server never calls.
`service.open_gates(project, slug=None, *, slugs=None)` (`src/scieflow/core/service.py:1100-1123`) reads
one run, the given `slugs` (an empty list returns `[]`: the run list is rebuilt only when `slugs is None`,
`:1111-1112`), or every run; for each open `charter-adoption` gate it adds `proposal_text` and
`proposal_digest` read from the run's proposal file (`_with_proposal_preview`, `:1224-1227`;
`_proposal_path` confines the path to the run, `:1150-1167`). So the aggregate is file content of each run,
not metadata. `service.list_runs_with_budget` (`:104-126`) reads only `status.yml`, `config.yml` and
`budget.yml` (its docstring: "No event log, gate, proposal preview or job listing"); `workspace.describe`
(`src/scieflow/core/workspace.py:108-131`) returns slug, kind, state, updated, lineage, id, phase,
phase_state, iteration, stopped_reason and updated_at — status/config fields only.

**Sanitiser and dispatch detector.** `src/scieflow/core/sanitize.py:12-31` re-exports the vendored
`sanitize_json_strings` (`src/scieflow/_vendor/researcher_core/sanitize.py:331-343`, whose docstring names
"the MCP server" as a caller). `in_dispatch` / `refuse_in_dispatch`
(`src/scieflow/integrations/scite/mcp.py:160-175`) detect `SCIEFLOW_DISPATCH=1` or the per-dispatch
`UV_CACHE_DIR`; `core/data/cli.py:49-54` and `core/run/converse_cli.py:46-50` reuse them. Importing that
module does not need the SDK (`sdk_available` uses `find_spec`, `:177-182`).

**Stub agent and project root.** `src/scieflow/core/stub_agent.py` (zero tokens, zero network; `kind:
conversation` mode for `service.say`). `config.repo_root()` = nearest ancestor with `config/agents.yml`,
current directory first (`src/scieflow/core/config.py:16-34`), so a server started with `cwd` = a test
project serves that project. Existing patterns: `tests/core/conftest.py` (`STUB`, `REGISTRY`, fixture
credentials), `tests/test_service_e2e.py`, CLI subprocesses as `[sys.executable, "-m", "scieflow.cli", …]`
(`tests/core/test_agent_run.py:322`).

**Root AGENTS.md** Orientation `AGENTS.md:244-267`; the "Runs:" bullet `:263-265`.

**Repository facts for CHANGELOG/CITATION.** No `LICENSE`, `CHANGELOG.md` or `CITATION.cff` at the root
(`git ls-tree M`). `pyproject.toml:3` `version = "0.2.0"`, introduced by `41a986e` (2026-09-16, "build:
make scieflow an installable package"). First-parent merges on M since then: PRs #1–#8 and #10–#13,
#15–#24 (`git log --first-parent`; #9 and #14 were not merged into main by a first-parent merge).
Commit author names include "Tomáš Krajča".

## Part A — Spec

### 5.1 CI gate

**Workflow `.github/workflows/tests.yml`** (exact content; t1 may only change the timeout per the rule
below):

```yaml
name: Tests

# The CI test gate (DECISIONS D22): Ubuntu only, the offline default suite
# without slow, live or scite_live tests and without the sealed probe suite.
# No secrets; read-only token. The same command runs locally:
# scripts/ci-tests.sh (docs/testing.md).

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: tests-${{ github.ref }}
  cancel-in-progress: true

jobs:
  tests:
    runs-on: ubuntu-24.04
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: System tools for the sandbox and remote tests
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends bubblewrap rsync
          sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
          bwrap --ro-bind / / --dev /dev true
      - name: Install the project
        run: uv sync --frozen --python 3.12 --all-extras --group dev
      - name: Tests
        run: scripts/ci-tests.sh
```

**Script `scripts/ci-tests.sh`** (mode 755; exact content apart from comments):

```bash
#!/usr/bin/env bash
# The CI test gate (DECISIONS D22). GitHub runs exactly this after
# `uv sync --frozen --python 3.12 --all-extras --group dev`; run it the same way locally.
# Extra arguments go to pytest (for example --junitxml=PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
exec uv run --frozen --no-sync python -m pytest -q -p no:cacheprovider \
  -m 'not slow and not live and not scite_live' \
  --ignore=tests/probes/test_k_task8_binding.py \
  "$@" tests
```

Environment rule: anything the tests need besides apt packages goes into the script (so the local
reproduction is the same). If the reproduction shows the suite needs, for example, a git identity, t1 sets
it inside the script for the pytest process only (`GIT_AUTHOR_NAME` … environment variables), never in the
user's global config, and documents why. Timeout rule: t1 measures the reproduction's wall time T; the
workflow keeps 120 minutes when T ≤ 80 minutes, otherwise `ceil(1.5 × T)` minutes.

**Local reproduction (the pre-merge evidence for "CI green on a PR", M-I-D4)**, run by t1 on its final
commit and by `branch` on the tip, under the shared full-suite lock, memory-capped:

1. `git clone --quiet --depth 1 --no-local --branch dev/m-i file:///home/ubuntu/ScieFlow-claude-f2976f52
   ~/.cache/scieflow-m-i-ci/clone-<sha12>` (shallow like `actions/checkout`); require the clone's HEAD = the
   expected head.
2. Fresh environment, offline, from the lock file:
   `cd <clone> && env HOME=~/.cache/scieflow-m-i-ci/home-<sha12> UV_CACHE_DIR=/home/ubuntu/.cache/uv
   UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT=~/.cache/scieflow-m-i-ci/venv /home/ubuntu/.local/bin/uv sync
   --frozen --python /usr/bin/python3.12 --all-extras --group dev` (the home folder is new and empty).
3. `env -i HOME=<that empty home> USER=$(id -un) LANG=C.UTF-8 CI=true
   PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin UV_CACHE_DIR=/home/ubuntu/.cache/uv
   UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT=~/.cache/scieflow-m-i-ci/venv bash scripts/ci-tests.sh
   --junitxml=RUN/scratch/m-i/junit/ci-repro-<step>.xml` inside `flock -w 7200
   /home/ubuntu/.cache/scieflow-full-suite.lock systemd-run --user --scope --quiet -p MemoryMax=2500M -p
   MemorySwapMax=0 …`, started in the background with a log file.
4. Pass = exit 0 and the junit shows 0 failures and 0 errors. If step 2 cannot build offline (a wheel
   missing from the cache), record "offline environment not available" with the uv message and run step 3
   with `UV_PROJECT_ENVIRONMENT` pointing at the shared `.venv` (`/home/ubuntu/ScieFlow-claude-f2976f52/.venv`)
   instead; the clean clone and the empty home stay. Never install into the shared `.venv`.

A test that fails only in the reproduction is a hermeticity finding: the fix makes the test (or the
script's environment) hermetic; it is an E-extra entry (section 3) when it touches an existing test.

**Static checks (`tests/test_ci_workflow.py`)** — PyYAML reads the key `on` as `True`; the test handles
it:
1. Exactly one job; `runs-on` is `ubuntu-24.04`; no `strategy`/`matrix`; `timeout-minutes` present.
2. Top-level `permissions == {"contents": "read"}`; no job-level `permissions`; the text has no
   `secrets.`, no `pull_request_target`, no `${{ github.event` (no untrusted event data in `run:`).
3. Triggers are exactly `pull_request`, `push` (branches `[main]`), `workflow_dispatch`.
4. `uses:` values are exactly `actions/checkout@v4` and `astral-sh/setup-uv@v5`; checkout has
   `persist-credentials: false`.
5. The install step runs `uv sync --frozen` with `--all-extras` and `--group dev` and no other `--group`;
   the test step runs exactly `scripts/ci-tests.sh`.
6. The script exists, is executable, contains the marker expression
   `not slow and not live and not scite_live`, contains `--ignore=tests/probes/test_k_task8_binding.py`,
   runs `pytest` on `tests`, and selects no live test (no `-m live`, no `-m scite_live`, no `--run-live`);
   every marker declared in `pyproject.toml` `markers` appears negated (`not <marker>`) in the script's
   expression, so a future marker forces a decision here.
7. The marker expression in the script equals the `-m` expression of `pyproject.toml` `addopts` (they stay
   in step).

### 5.2 Records: events and jobs (`scieflow.core.records`)

**Errors.** `RecordInvalid(ValueError)` with `rule` and `where` attributes; `EventInvalid` and `JobInvalid`
subclass it. Messages name the field or data key path and the rule, never the offending value (M-B rule).
Existing messages of `events.emit` (unknown type, unknown actor) are kept verbatim; their checks run first.

**`check_event(event: dict) -> dict`** (called by `events.emit` after building the envelope and before
`store.append_jsonl`; returns the normalised event that is written and returned):

| Rule id | Check |
|---|---|
| `envelope-keys` | keys exactly `id, ts, run, slug, type, actor, data` |
| `id` | 26 Crockford base32 characters |
| `ts` | parses with `datetime.fromisoformat` and has a UTC offset |
| `run`, `slug` | non-empty strings |
| `type` | in `events.TYPES` (read at call time) or `note.` + name matching `[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}` (`note-name`) |
| `actor` | in `events.ACTORS` |
| `data-object` | `data` is a mapping with string keys (recursively) |
| `data-value` | every value is null, bool, int, float, str, list or object after the coercions; anything else (set, bytes, arbitrary object) refused; the message names the key path (`data.refs[2]`) |
| `data-finite` | no NaN or infinity anywhere |
| `required-data` | the type's required keys (table below) are present |

Coercions (they keep today's text for the common case): `os.PathLike`/`PurePath` → `str(path)`; tuple →
list; a `numbers.Integral` that is not `bool` → `int`; a `numbers.Real` → `float`. Nothing else is
coerced. Bool stays bool.

Required data keys (M-I-D7; every emitter on M passes them; t0 re-verifies each row against every
`emit` call site on base and the t2 card drops a row whose emitters disagree, recording it):

| Types | Required keys |
|---|---|
| `job.queued`, `job.started`, `job.finished`, `job.failed`, `job.timeout`, `job.cancelled`, `job.lost` | `job`, `kind`, `label` |
| `job.refused` | `reason` |
| `gate.opened` | `gate`, `kind`, `question` |
| `gate.answered` | `gate`, `kind`, `answer` |
| `gate.withdrawn` | `gate`, `kind` |
| `phase.pending`, `phase.started`, `phase.done`, `phase.failed` | `phase` |
| `iteration.advanced` | `iteration` |
| `checkpoint` | `reason` |
| `charter.set` | `version` |
| `curation.changed`, `curation.round` | `version` |
| `data.warning` | `rule`, `path` |
| `data.level_resolved` | `level`, `boundary` |
| `network.set` | `mode` |
| `literature.snapshot`, `literature.monitor` | `action` |

**`check_job(record: dict) -> None`** (called by `jobs.save` on `asdict(job)` before writing):

| Rule id | Check |
|---|---|
| `job-keys` | keys exactly the `Job` dataclass fields (read from `dataclasses.fields(Job)` at call time, so a field added by another milestone is accepted only when it is a real field) |
| `job-id` | `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` |
| `job-kind` | `[a-z][a-z0-9_-]{0,31}` |
| `job-state` | in `jobs.STATES` |
| `job-argv` | list of strings |
| `job-strings` | `cwd`, `label`, `queued`, `log`, `err` strings; `run_dir`, `started`, `finished` string or null |
| `job-numbers` | `pid`, `exit_code` int (not bool) or null; `timeout_s` finite number ≥ 0 or null |
| `job-flags` | `sandboxed` bool; `turn_audit` object or null (its content is already validated by `Job.__post_init__`) |
| `job-final` | a state in `jobs.FINAL` has `finished` set |

t0 checks the `job-final` row against every `save` path on base (`_finish`, `cancel`, `reconcile`, and
M-D3's functions if the base has them); a path that saves a final state without `finished` drops the row
(recorded), it is not "fixed" in M-I.

**Read checks** (never raise on content): `check_event_lines(path) -> list[Problem]` reads
`events.jsonl` line by line and reports `{line, rule}` for a line that is not JSON (`json`), not an object
(`object`), or fails the read rules: keys `id, ts, type, actor, data` present (`run`/`slug` optional, for
older logs), type known or a valid note, actor known, data an object. `check_job_files(dir) ->
list[Problem]` reports `{file, rule}` for every `jobs/*.json` that does not parse or fails `check_job`
except `job-final`. Neither function reads more than 50 MB per file (larger → one problem `too-large`).

**Doctor.** `workspace.doctor` adds `"invalid_events": [...]` and `"invalid_jobs": [...]` (at most 20
entries each, plus `"invalid_events_total"`, `"invalid_jobs_total"`); text output prints one line per
problem kind with the first three line numbers / file names. Doctor stays read-only.

**Documented form.** `schemas/events.yml`: `version: 1`, `envelope` (keys, id/ts/actor rules, the note
pattern), `data` (allowed kinds, coercions, finite rule), `types_source: scieflow.core.events.TYPES`,
`required_data` (the table), `read_check` (required keys). `schemas/jobs.yml`: `version: 1`, `fields` with
their type rule, `id_pattern`, `kind_pattern`, `states_source: scieflow.core.jobs.STATES`, `final_rule`.
`tests/core/test_records.py` asserts both files equal the module's constants and that every
`required_data` key is in `events.TYPES`.

### 5.3 Read-only CLI views (D7)

Thin commands, each calling exactly one service function through the module attribute
(`service.<name>`) so the parity test's recorder sees the call; `--json` on all; `ServiceError` → exit 1.

| Command | Service function | Text output |
|---|---|---|
| `scieflow run overview SLUG [--json]` | `run_overview` | one line per panel: name, reason or "ok" |
| `scieflow run budgets [--json]` | `list_runs_with_budget` | one line per run: slug, kind, state, smallest remaining fraction as a percentage or "no budget" |
| `scieflow run workflows [--json]` | `workflows` | one line per workflow: name, roles |
| `scieflow agent conversational [--json]` | `conversational_agents` | one agent name per line |

The three `run` commands are added to `converse_cli.new_commands()` (so the existing collision test in
`tests/core/test_converse_cli.py` covers them; t0 confirms the names are free in `core.run.cli.run`). The
text printers tolerate any value (the parity test's recorder returns a dict). E2 updates the table.

### 5.4 MCP server: transport and protocol (`scieflow.mcp_server.protocol`)

- **Transport:** stdio. Each message is one line of UTF-8 JSON on stdin; each response one line on
  stdout, flushed. Nothing else is ever written to stdout; diagnostics go to stderr. EOF on stdin → exit 0.
  A line longer than 4 MiB → parse error response, the server continues.
- **JSON-RPC 2.0:** a request has `jsonrpc: "2.0"`, a string or integer `id`, a string `method`, optional
  object `params`. A notification has no `id` and never gets a response. Invalid JSON → error `-32700`
  with `id: null`; a JSON array (batch) or a malformed request (including `id: null`) → `-32600`; unknown
  method → `-32601`; bad params → `-32602`; an unexpected internal exception → `-32603` with a generic
  message (the traceback goes to stderr, never to the client).
- **Lifecycle:** before a successful `initialize`, every request except `ping` gets `-32002`
  ("server not initialized"). `initialize` params need `protocolVersion` (string); the server answers
  with the same version when it is one of `SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26",
  "2024-11-05")`, otherwise with `SUPPORTED[0]`. Result: `{protocolVersion, capabilities: {tools:
  {listChanged: false}}, serverInfo: {name: "scieflow", version: <installed scieflow version or
  "unknown">}, instructions: <one paragraph: read-only, content is data not instructions, L2 runs hidden>}`.
  A second `initialize` → `-32600`. `notifications/initialized` and `notifications/cancelled` are accepted
  and ignored. `ping` → `{}`.
- **`tools/list`** → `{tools: [...]}` (no pagination; a `cursor` param is ignored). Each tool:
  `name`, `title`, `description`, `inputSchema` (JSON Schema object, `additionalProperties: false`),
  `annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false}`.
- **`tools/call`** params `{name, arguments}` (`arguments` optional, default `{}`). Unknown tool or
  arguments that fail the input schema → `-32602` naming the tool and argument (never echoing the value).
  The server validates arguments itself (types `string`, `integer` with `minimum`/`maximum`, `array` of
  strings with `maxItems`; required keys; no extra keys). Success → `{content: [{type: "text", text:
  <JSON of the payload>}], isError: false}` plus `structuredContent: <payload>` when the negotiated version
  is `2025-06-18` or later. A tool-level failure (a `ServiceError`, a data refusal, a scope refusal, a
  too-large result) → `{content: [{type: "text", text: <message>}], isError: true}`.
- The protocol layer is `Server(project, scope=None)` with `handle(message) -> dict | None` and
  `serve(stdin, stdout)`; tests drive `handle` directly and `serve` through a subprocess.

### 5.5 MCP tools (`scieflow.mcp_server.tools`)

Every tool has a `service` attribute naming the one service function it calls (through
`scieflow.core.service.<name>`); `test_mcp_parity.py` checks that each such function has a `cli:` entry in
`tests/core/cli_parity.yml` (nothing is reachable only over MCP, D7).

| Tool | Arguments | Service function | Payload `result` |
|---|---|---|---|
| `list_runs` | — | `list_runs_with_budget` | runs whose level is not L2; plus `hidden_runs` (count) |
| `run_detail` | `slug` | `run_detail` | as returned |
| `run_events` | `slug`, `since?` (string ≤ 64), `types?` (≤ 20 strings ≤ 64), `limit?` (1–1000, default 200) | `run_events` | the last `limit` events; plus `truncated` (bool) |
| `open_gates` | `slug?` | `open_gates` | with `slug`: that run's gates (decision first, 5.6 item 4); without: the gates of every run whose decision allows it (5.6 item 4b), plus `hidden_runs` (count) |
| `run_charter` | `slug` | `run_charter` | as returned |
| `conversation` | `slug` | `conversation_state` | as returned |
| `run_overview` | `slug` | `run_overview` | as returned |
| `workbench` | `slug` | `workbench` | as returned |
| `curation_history` | `slug` | `curation_history` | as returned |
| `agent_settings` | `slug?` | `agent_settings` | as returned |
| `workflows` | — | `workflows` | as returned |
| `conversational_agents` | — | `conversational_agents` | as returned |

Payload shape: `{"result": …}` plus, for per-run tools, `"data_warnings": [{"rule", "path"}]` when the data
decision allowed with warnings (5.6). `open_gates` without `slug` carries `"data_warnings": [{"slug",
"rule", "path"}]` (one entry per warning of each served run) and `"hidden_runs": <int>`; the payload never
names a hidden run. `list_runs` runs no data decision: it is metadata only (section 4: status/config
fields, no file content) and hides L2 by level. Project-level tools (`workflows`, `conversational_agents`,
`agent_settings` without `slug`) run no data decision; `agent_settings` with `slug` does.

### 5.6 MCP access rules (`scieflow.mcp_server.access`, `scieflow.mcp_server.cli`)

1. **Dispatch refusal (M-I-D13).** `scieflow mcp serve` and `scieflow mcp tools` call
   `in_dispatch()` first; when true → exit 1 with "scieflow mcp: refused inside a dispatch (a sub-agent may
   not read across runs)"; stdin is not read.
2. **Scope.** `scieflow mcp serve --workspace SLUG` (the slug must be an existing run, cleaned by
   `Project.run_dir`) limits every tool to that run: a different `slug` → tool error "this server serves
   only run SLUG"; `list_runs` and `open_gates` without `slug` show only that run; `agent_settings` without
   `slug` uses it.
3. **Hidden runs.** "Hidden" means: left out of the payload, never named, counted in `hidden_runs`.
   `list_runs` (metadata only) hides a run whose `levels.run_level(ws)` is `L2` (by level; an invalid
   `data.yml` is L2, fail closed). `open_gates` without `slug` hides every L2 run **and every run whose
   item-4 decision is a refusal or not `cloud`** (item 4b). Under scope (item 2) both consider only the
   scoped run.
4. **Per-run data decision (M-I-D12).** Before calling the service function of a per-run tool:
   `decision = dispatch.decide(project, ws, MCP_CLIENT, {}, sandboxed=True, templates_use_endpoint=False)`
   with the reserved name `MCP_CLIENT = "scieflow-mcp-client"`. `decision.allowed` false → tool error
   "refused (<rule>): <message>". `decision.boundary != "cloud"` → tool error "refused (mcp-not-cloud): the
   MCP client is always a cloud agent". Allowed with warnings → the payload carries `data_warnings` (rule and
   path only). `record_refusal` / `record_decision` / `report_findings` are never called (no events).
4b. **Aggregated gates.** `open_gates` without `slug`: take the run slugs from `service.list_runs` (in
   scope only), drop the L2 runs, run the item-4 decision for each remaining run, keep only the runs with
   `allowed` true and `boundary == "cloud"`, and then call `service.open_gates(project, slugs=<kept>)` —
   always a list, also when empty (never `slugs=None`, which would read every run). A refused run is
   counted in `hidden_runs`; its gates and proposal text are never read. Warnings of the kept runs go into
   `data_warnings` with their slug. A run that vanishes between listing and reading is skipped by the
   service (`service.py:1115-1120`) and not counted. The decision of one run never affects another.
5. **Sanitiser (M-I-D14).** The payload passes `sanitize.sanitize_json_strings` before it is serialised;
   error texts pass `sanitize_string`.
6. **Size cap.** Serialised payload > 1,000,000 bytes → tool error "result too large (N bytes); narrow it
   (run_events: since/types/limit)".
7. **Read-only guarantee.** The server writes nothing: no event, no job, no ledger, no cache, no gate. The
   test (5.8) compares the project tree before and after every tool call; lock files named `*.lock` that a
   read may create through `store.locked` are the only allowed difference (they hold no content). A tool
   whose service function writes anything else is removed from the tool list by t5 and recorded in its result
   (no tool is expected to be removed: section 4 lists the functions with writes, and they are not tools).
8. **CLI.** `scieflow mcp serve [--workspace SLUG]` runs `protocol.serve(sys.stdin, sys.stdout)` for
   `Project.discover()`; `scieflow mcp tools [--json]` prints the tool list (names, titles, input schemas)
   without starting the server. Root `GROUPS` entry: `"mcp": ("scieflow.mcp_server.cli", "mcp", None,
   "Read-only MCP server for an interactive coordinator (stdio).")`.

### 5.7 Docs, CHANGELOG, citation

- `docs/testing.md` (t1): how to run the suite, the markers, the sealed probe exclusion, what CI runs and
  why (D22), how to reproduce CI locally, the M-I-D4 note that merge does not wait for CI. Nav after "CLI
  reference".
- `docs/mcp.md` (t6): what the server is; the twelve tools with arguments; the access rules of 5.6 in plain
  words (cloud-agent rule, L2 hidden, sanitiser, size cap, read-only, no dispatch use, scope); how to
  register it in a client as a generic `mcpServers` entry (`"command": "uv", "args": ["--directory",
  "<path to ScieFlow>", "run", "scieflow", "mcp", "serve"]`) without claiming a specific client's file
  format; that it is not offered to sub-agents and no `.mcp.json` is shipped; the honest limits (5.9).
  Nav after "Data protection".
- `docs/cli.md` (t6): sections for `scieflow mcp`, the three `run` views, `agent conversational`, and
  the doctor's new findings; the exit-code table unchanged (all refusals are exit 1).
- `docs/architecture.md` (t6): after the "Where state lives on disk" table (`docs/architecture.md:62-80`),
  a short "Records are checked when written" paragraph linking `schemas/events.yml` and `schemas/jobs.yml`.
- `CHANGELOG.md` (t6): header, "All notable changes …", `## [Unreleased]` with `### Added` (M-I's five
  items) and `### Merged pull requests` (one line per first-parent PR merge on base since `41a986e`,
  newest first, "#N <branch-derived title> (YYYY-MM-DD)"), then `## [0.2.0] - 2026-09-16` "First
  installable package; earlier history is in the git log".
- `CITATION.cff` (t6): `cff-version: 1.2.0`, `message`, `title: "ScieFlow"`, `type: software`,
  `authors: [{family-names: "Krajča", given-names: "Tomáš"}]`, `version: "0.2.0"`, `repository-code:
  "https://github.com/jedimik/ScieFlow"`, `abstract` (one sentence from the README), `keywords`. No other
  personal data.

### 5.8 Security and integrity properties (reviewed in every ticket that touches them)

1. The workflow cannot leak: no secrets, read-only token, no `pull_request_target`, no event data in shell,
   checkout without persisted credentials, no uploads.
2. CI excludes every live path (D22) and the sealed suite, statically checked.
3. An invalid event or job is refused before anything is written; messages never contain values.
4. Reading old logs never fails; problems are reported by `doctor` only.
5. The MCP server has no network listener, no write path and no subprocess; it refuses inside a dispatch;
   L2 runs are hidden and refused; every read of run content — each per-run tool and every run of the
   aggregated `open_gates` — passes M-B's cloud-agent decision first, and a refused run is neither read
   nor named; the run list is status metadata only; output is sanitised and bounded; tracebacks never
   reach the client.
6. Nothing is reachable only through MCP (parity test), and the four CLI views call exactly their service
   function.

### 5.9 What M-I does not do (stated)

No write tools, MCP resources or prompts; no HTTP/SSE transport; no MCP client authentication; no way to
declare an MCP client safe; no audit of reads; no lint/type/coverage/benchmark gates (researcher's CI has
them; follow-up); no SHA-pinned actions (follow-up for both workflows); no Windows/macOS CI (D22); publish
does not wait for CI (M-I-D4); no version bump or release; no real-client interop test (the SDK cannot be
installed offline here; first real use from Hermes or Claude Code is a human smoke, follow-up).

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a
checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only; the CI
reproduction is offline), no push/PR, no credentials, no patient data (synthetic fixtures only), never edits
an `INFLIGHT` path or a file of section 3 "Never edited" (the t6 protected edit is applied by the
operator). Every implementation and fix card runs the `INFLIGHT` guard and reports its exit code.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-i-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-i-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-i/junit G6_CACHE_DIR=~/.cache/scieflow-m-i RUN/tools/g6_pytest.sh WT <head-sha> m-i-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with
  completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> m-i-sealed -- tests/probes` (4 passed on M), also under the
  lock.
- **CI reproduction:** section 5.1 steps 1–4 (t1 and branch only).
- **Load-flake nodes**: the four named in the installer's standing rules, one rerun, both results recorded.
  No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-i/executions.jsonl` and states RED and GREEN evidence in its result.
- Focused sets per ticket (clean-tree runner on the ticket's final commit): t1 `tests/test_ci_workflow.py
  tests/probes/test_probe_collection.py`; t2 `tests/core tests/web/test_pages.py tests/test_service_e2e.py
  tests/integrations/scite/test_doctor.py`; t3 `tests/core/test_cli_parity.py tests/core/test_converse_cli.py
  tests/core/test_read_views_cli.py`; t4, t5 `tests/mcp_server tests/core/test_cli_parity.py`; t6
  `tests/test_docs_platform.py tests/test_docs_role.py tests/test_ci_workflow.py tests/mcp_server`.
- MCP subprocess tests start the server as `[sys.executable, "-m", "scieflow.cli", "mcp", "serve"]` with
  `cwd` = the test project root, `SCIEFLOW_DISPATCH` and `UV_CACHE_DIR` removed from the environment,
  a 30 s timeout per exchange, and kill the process in teardown.

### t0 — worktree, plan copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the five ancestor checks and the M-C1 publish receipt;
   otherwise `BASE_RED` with the reason. `worktree add -b dev/m-i RUN/scratch/m-i-wt <base>` (on a retry
   reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and
   junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 on `base`; write `reviews/m-i-t0-baseline.md` (per cite:
   still present / moved (new line) / changed). Inventories, all in that file: (a) whether base contains
   M-D3 (`RUN/scratch/m-d3/publish.json`) or other milestones merged after M (`git log --first-parent
   M..base`) and whether any changed a file of section 3's list; (b) every `events.emit` call site under
   `src/` and `scripts/` on base with its type and data keys, checked against the 5.2 required-keys table
   (per row: holds / fails at <file:line>); (c) every `jobs.save` path and whether a final state always
   has `finished`; (d) every test under `tests/` that calls `events.emit`, `jobs.save` or builds a `Job`,
   and which of them would violate 5.2 (expected: only E1); (e) that `overview`, `budgets`, `workflows`
   are free in `core.run.cli.run.commands` and `conversational` in the `agent` group, and `mcp` in
   `GROUPS`; (f) whether the protected edit's `old` text occurs exactly once in root `AGENTS.md`; (g) the
   result of `UV_OFFLINE=1 uv sync --frozen --dry-run --all-extras --group dev` on a `git archive` export of
   base in `~/.cache/scieflow-m-i-ci/probe` (offline environment feasible or not); (h) the first-parent PR
   merges since `41a986e` (for t6); (i) that `service.list_runs_with_budget` and `workspace.describe` still
   read only `status.yml`, `config.yml` and `budget.yml` and return only the status/config fields listed in
   section 4 (no file content), and that `service.open_gates(project, slugs=[])` still returns `[]` without
   listing runs — if either no longer holds, t0 records it and t5 applies the item-4 decision to `list_runs`
   too (refused runs hidden and counted) instead of relying on metadata-only.
4. Copy `reviews/m-i-plan.md` byte for byte to `docs/superpowers/plans/2026-10-31-m-i-platform.md` and
   commit it alone ("docs(plans): M-I platform plan").

Verdicts `DONE` | `BASE_RED`.

### t1 — CI gate (Haiku) → t1r (Sonnet)

Files: `.github/workflows/tests.yml`, `scripts/ci-tests.sh`, `tests/test_ci_workflow.py`,
`docs/testing.md`, `mkdocs.yml` (one nav line).

RED → GREEN:
1. `tests/test_ci_workflow.py` with the seven checks of 5.1, written first and failing (no files).
2. The workflow and script with the exact 5.1 content; the checks pass.
3. Negative self-tests of the checker (each on a modified in-memory copy, not on disk): adding `secrets.X`,
   `pull_request_target`, a `macos-latest` job, a matrix, `-m live` in the script, or removing the
   `--ignore` each makes the corresponding check fail.
4. `docs/testing.md` exists, is in the nav, and names `scripts/ci-tests.sh`, the marker expression, the
   sealed file, `ubuntu-24.04` and "does not wait".
5. **CI reproduction** (5.1) on t1's final commit: exit 0, 0 failed, 0 errors; record wall time T,
   passed/skipped counts, and the offline-environment outcome. Apply the timeout rule. A failure is
   handled per 5.1 (hermeticity fix, E-extra) and re-run; t1 is not done until the reproduction is green.
6. `tests/probes/test_probe_collection.py` still green.

t1r checklist: workflow text equals 5.1 (except the timeout rule); checker really fails on each negative;
no secret or write permission; reproduction evidence real (junit, clone HEAD = head, empty home, offline
outcome stated); no test skipped or deselected beyond D22; INFLIGHT guard 0.

### t2 — event and job records (Haiku) → t2r (Sonnet)

Files: `src/scieflow/core/records.py`, `src/scieflow/core/events.py` (`emit` only), `src/scieflow/core/jobs.py`
(`save` only), `src/scieflow/core/workspace.py` (`doctor`, `doctor_cmd`), `schemas/events.yml`,
`schemas/jobs.yml`, tests of the file list, E1.

RED → GREEN:
1. **Acceptance "invalid event refused":** `events.emit(ws, "note.<img src=x>")`, `"note."`,
   `"note.a b"`, `data={"x": {1, 2}}`, `data={"x": b"raw"}`, `data={"x": object()}`, `data={"x":
   float("nan")}`, a `job.finished` without `job`, a `gate.answered` without `answer` each raise
   `EventInvalid` (a `ValueError`) with the rule id in the message, the value absent from the message, and
   `events.jsonl` unchanged (byte-equal before/after). Unknown type and unknown actor keep their existing
   messages (`tests/core/test_events.py` unchanged and green).
2. Allowed: every type of `events.TYPES` with its required keys; `note.idea`, `note.design_warning`,
   `note.a:b`; a `Path` value is written as its text; a tuple as a list; `numpy.int64(3)` as `3` (numpy is
   in the experiments extra; skip-free because CI installs all extras); nested lists and objects; `None`.
3. `check_event` is called on the exact record written: a monkeypatched `store.append_jsonl` receives the
   normalised event, and `emit` returns that same dict.
4. Jobs: `jobs.save` refuses each `check_job` rule (bad state, bool pid, non-string argv item, final
   state without `finished`, extra key via a forged dict passed to the validator, unsafe id, kind
   `"Agent"`), the record file untouched; `jobs.start` → `wait` of a real short process and `cancel` still
   save valid records; `kind="remote"` with `pid=None` is valid; `load` of an invalid record on disk still
   returns it through `list_jobs` exactly as today (lenient read).
5. Read checks: an `events.jsonl` with a non-JSON line, an array line, a line missing `data`, an unknown
   type and a valid older line without `slug` → problems for the first four only (line numbers right);
   `jobs/` with a broken file and a bad state → two problems; a 51 MB file → `too-large` without reading it
   whole.
6. `workspace doctor --json` shows `invalid_events`, `invalid_jobs` and the totals; the text output shows
   them; a clean run shows empty lists; doctor writes nothing (tree compare).
7. `schemas/events.yml` and `schemas/jobs.yml` equal the module constants; every `required_data` type is in
   `events.TYPES`; `tests/core/review/test_gates_events.py` unchanged and green (TYPES unchanged).
8. E1: the test now appends the hostile record directly and its three assertions pass unchanged; a new
   test proves `emit` refuses that type.
9. The t2-focused set and then the **full default suite** on the clean t2 tip: 0 failed / 0 errors (this
   is where every product emitter is exercised; a failure is handled per section 3 E-extra / emitter fix).

t2r checklist: rules exactly 5.2; no value in any message; existing `emit` messages unchanged; TYPES and
ACTORS untouched; readers unchanged; every changed existing test is E1 or a recorded E-extra with
unchanged assertions; full-suite evidence real; INFLIGHT guard 0.

### t3 — read-only CLI views (Haiku) → t3r (Sonnet)

Files: `src/scieflow/core/run/converse_cli.py`, `src/scieflow/core/cli.py`, `tests/core/cli_parity.yml`
(E2), `tests/core/test_read_views_cli.py`.

RED → GREEN:
1. E2 first: the parity test fails (the four `cli:` paths do not resolve yet).
2. The four commands of 5.3; `tests/core/test_cli_parity.py` (unchanged) passes, including its
   recorder test (c) for the four new entries.
3. `test_read_views_cli.py` on a real test project (the `project` fixture pattern): `run overview r1
   --json` equals `service.run_overview(project, "r1")`; `run budgets --json` equals
   `service.list_runs_with_budget(project)`; `run workflows --json` equals `service.workflows()`; `agent
   conversational --json` equals `service.conversational_agents(project)`; text output lines as 5.3;
   unknown slug → exit 1 with the service message; each command writes nothing (tree compare).
4. `tests/core/test_converse_cli.py` (collision test) unchanged and green; `scieflow run --help` lists the
   three new commands.

t3r checklist: each command calls exactly one service function and nothing else; no write; parity table
honest (`preview_busy` reason accurate); INFLIGHT guard 0.

### t4 — MCP protocol, CLI and project tools (Haiku) → t4r (Sonnet)

Files: `src/scieflow/mcp_server/{__init__,protocol,tools,cli}.py` (the access module may start as a stub
that t5 completes), `src/scieflow/cli.py` (one `GROUPS` entry), `tests/mcp_server/test_protocol.py`,
`test_cli.py`, `test_project_tools.py`.

RED → GREEN (in-process `Server.handle` unless stated):
1. Lifecycle: `ping` before init → `{}`; `tools/list` before init → `-32002`; `initialize` with
   `2025-06-18` → that version, `serverInfo.name == "scieflow"`, `capabilities.tools.listChanged is False`;
   `2024-11-05` → echoed; `1999-01-01` → `SUPPORTED[0]`; missing `protocolVersion` → `-32602`; second
   `initialize` → `-32600`; notifications return `None`.
2. JSON-RPC errors: `-32700` (bad JSON, `id: null`), `-32600` (array, `id: null`, missing `jsonrpc`),
   `-32601` (unknown method), string and integer ids echoed exactly.
3. `tools/list`: twelve tools with the names of 5.5, each with `inputSchema.type == "object"`,
   `additionalProperties: false`, and the four annotations; `scieflow mcp tools --json` prints the same
   list.
4. `tools/call` for `workflows`, `conversational_agents`, `agent_settings` (no slug) on a test project:
   payload `result` equals the service function's value; `structuredContent` present for `2025-06-18`,
   absent for `2025-03-26`; unknown tool → `-32602`; an extra argument, a wrong type, a missing required
   argument → `-32602` naming the argument; a service function raising `ServiceError` → `isError: true`
   with its message; one raising `RuntimeError` → `-32603` with a generic message, the traceback only on
   stderr.
5. `serve` over pipes (subprocess, real `scieflow.cli`): initialize → initialized → tools/list → EOF →
   exit 0 within 5 s; every stdout line is one JSON object; a 5 MiB line gets `-32700` and the next request
   is still answered.
6. `scieflow mcp serve` and `scieflow mcp tools` with `SCIEFLOW_DISPATCH=1` → exit 1, the refusal text,
   stdin not read (the subprocess gets a valid initialize on stdin and prints no JSON).
7. AST check on `src/scieflow/mcp_server/`: no import of `socket`, `http`, `urllib`, `requests`, `httpx`,
   `subprocess`, `mcp` — matched on the **top-level module name exactly** (`alias.name.split(".")[0]` for
   `import`, `module.split(".")[0]` for `from … import` with `level == 0`), so `from
   scieflow.integrations.scite.mcp import in_dispatch` is allowed and `import mcp`, `import mcp.types`, `from
   mcp.server import …` are flagged (the test asserts both directions on small in-memory sources); no call of `open(..., "w"/"a")`, `write_text`, `write_bytes`, `mkdir`,
   `store.write_*`, `store.append_jsonl`, `events.emit`, `jobs.save`.

t4r checklist: protocol rules exactly 5.4; nothing but JSON-RPC on stdout; no traceback to the client;
dispatch refusal before any read; no network or write capability (AST check meaningful); INFLIGHT guard 0.

### t5 — MCP run tools and access rules (Haiku) → t5r (Sonnet)

Files: `src/scieflow/mcp_server/{tools,access}.py`, `tests/mcp_server/conftest.py`, `test_run_tools.py`,
`test_access.py`, `test_readonly.py`, `test_mcp_parity.py`, `test_mcp_e2e.py`.

Fixture (`conftest.py`): a temporary project root with `config/agents.yml` holding the stub agent (the
`tests/core/conftest.py` `STUB`/`REGISTRY` pattern, fixture credentials), a run `r1` populated through the
service layer with the stub agent: one dispatched stub job (`service.dispatch_agent`, done), one
conversation turn (`service.say` with the stub's `kind: conversation` mode), an open gate, a charter
version, a curation block, a few `note.*` events; a second run `secret` with `data.yml` declaring an L2
path (written with the M-B API used by M-B's tests); a third run `l1` declared L1 without an
external-sharing gate, holding an open `charter-adoption` gate whose proposal file contains a distinctive
synthetic marker sentence (opened with `gates.open_gate(project, ws, "charter-adoption", …, files=[proposal])`,
the pattern of `tests/core/test_charter.py:175-182`); `r1` also has an open gate (above).

RED → GREEN:
1. Each per-run tool on `r1` returns the service function's value (after sanitising, which leaves this
   clean fixture unchanged); `run_events` honours `since`, `types` (prefix `*`), `limit`, and sets
   `truncated`.
2. L2: `list_runs` omits `secret` and reports `hidden_runs: 1`; `open_gates` without slug omits it and
   counts it in `hidden_runs`; every per-run tool with `slug: "secret"` → `isError` with `l2-cloud`; a run
   with an invalid `data.yml` is treated as L2 (hidden and refused).
3. L1 without sharing: under `enforcement: warn` the tool succeeds with `data_warnings` containing
   `l1-external-sharing`; under `enforcement: refuse` it is refused with that rule. An invalid
   `config/data-policy.yml` → every per-run tool refused `policy-invalid`. A policy that lists
   `scieflow-mcp-client` as a safe agent does not make the client safe (refused or still `cloud`, never
   served as safe).
   **Aggregated `open_gates` (5.6 item 4b):** (a) under `enforcement: refuse`, `open_gates` without slug
   returns `r1`'s gate, contains no entry whose `slug` is `l1` (nor `secret`), no `data_warnings` entry for
   them, and `l1`'s proposal marker sentence nowhere in the serialised response, and `hidden_runs == 2`
   (`secret`, `l1`); (b) under
   `enforcement: warn` it returns both `r1`'s and `l1`'s gates and `data_warnings` holds
   `{"slug": "l1", "rule": "l1-external-sharing", …}`; (c) with an invalid `config/data-policy.yml` the
   result is `[]`, `hidden_runs` equals the number of runs, and no proposal marker appears; (d) a spy on
   `service._with_proposal_preview` (or on the proposal file's read) proves `l1`'s proposal is never read in
   cases (a) and (c); (e) with the service's `open_gates` monkeypatched to a recorder, case (c) records
   `slugs=[]` (a list, never `None`).
4. No events: `events.jsonl` of every run byte-equal before and after all calls, including refusals and
   warnings (no `data.warning`, no `job.refused`).
5. Sanitiser: a charter text and a curation block containing an injection phrase that
   `sanitize_string` redacts come back with the redaction marker; the files on disk are unchanged.
6. Size cap: a run with > 1,000,000 bytes of events and `limit: 1000` of large notes → `isError` "result
   too large"; with a small `limit` it succeeds.
7. Scope: a server with `scope="r1"`: `slug: "l1"` → `isError` scope message; `list_runs` shows only `r1`;
   `agent_settings` without slug uses `r1`.
8. **Read-only proof (`test_readonly.py`)**: snapshot every file under the project root (path, size,
   mtime_ns, sha256), call every tool with valid arguments for every run (including refusals), snapshot
   again: identical apart from new `*.lock` files; also with `SCIEFLOW_STATE_DIR` pointing into the
   project and checked.
9. **Parity (`test_mcp_parity.py`)**: every tool's `service` name has a `cli:` entry in
   `tests/core/cli_parity.yml`; for every tool, a `tools/call` with the service function monkeypatched to a
   recorder records exactly one call to it.
10. **Acceptance "MCP tools listed and callable with the stub agent" (`test_mcp_e2e.py`, subprocess,
    ≤ 120 s)**: start `scieflow mcp serve` in the fixture project; initialize (`2025-06-18`); `tools/list`
    returns the twelve tools; call every tool with valid arguments for `r1`: all `isError: false`;
    `run_detail` shows the stub job with state `done`; `conversation` shows the stub turn; `run_events`
    contains `job.finished` for that job; `list_runs` hides `secret`; `run_detail` on `secret` is
    `isError` with `l2-cloud`; EOF → exit 0; stderr has no traceback.

t5r checklist: the data decision runs before every per-run service call and on every path, including
every run of the aggregated `open_gates` (a refused run is never read, never named, only counted; an empty
allowed set passes `slugs=[]`); `list_runs` returns metadata only; nothing is
written (proof meaningful: it would fail if a tool wrote); L2 hidden and refused, policy-invalid fails
closed; sanitiser and cap applied to every payload; scope cannot be bypassed (`open_gates`, `list_runs`,
`agent_settings`); parity; INFLIGHT guard 0.

### t6 — docs, CHANGELOG, citation, AGENTS.md (Haiku) → t6r (Sonnet)

Before t6 the operator applies the protected edit (installer step `pe_t6`); t6 commits that file alone
first.

Files: `docs/mcp.md`, `docs/cli.md`, `docs/architecture.md`, `mkdocs.yml` (one nav line), `CHANGELOG.md`,
`CITATION.cff`, `tests/test_docs_platform.py`, root `AGENTS.md` (commit of the applied edit).

RED → GREEN (`tests/test_docs_platform.py`):
1. `docs/mcp.md` exists, is in the nav, names all twelve tools (equal to the server's tool list, read from
   the module), `--workspace`, "read-only", "L2", "cloud agent", "refuses to start inside a dispatch",
   "sanitis", the size cap, `scieflow mcp serve`, `scieflow mcp tools`, and has no `.mcp.json` file
   instruction for the repository root.
2. `docs/cli.md` has `## \`scieflow mcp\`` and names `run overview`, `run budgets`, `run workflows`,
   `agent conversational`, `invalid_events`; every flag documented for these commands exists in their
   `--help`.
3. `docs/architecture.md` links `schemas/events.yml` and `schemas/jobs.yml`.
4. `CHANGELOG.md` has `## [Unreleased]` and `## [0.2.0]`, mentions `#24`, and contains no e-mail address.
   `CITATION.cff` parses as YAML; `cff-version == "1.2.0"`; `version` equals `pyproject.toml`'s version;
   `authors` non-empty; no `email`, `orcid` or `license` key; `repository-code` is the GitHub URL.
5. Root `AGENTS.md` contains the new bullet of section 3 byte for byte, the old three lines are still
   there once, and nothing else in the file changed (`git diff` of that commit = the inserted lines);
   `tests/test_docs_role.py` and the other `tests/test_docs_*.py` still pass.
6. `mkdocs build --strict` into `RUN/scratch/m-i/site` if mkdocs is in the venv (else recorded "not
   available").

t6r checklist: docs ↔ code (tools, flags, rules, exit codes); honest limits stated; CHANGELOG lines match
the inventory (h) of t0; CITATION has no personal data beyond the name; AGENTS.md edit byte-exact and the
only `AGENTS.md` change; INFLIGHT guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by added tests); the **CI reproduction** of 5.1 on the tip
(exit 0, 0 failed, 0 errors; counts equal to the default suite's except for the sealed-probe and
marker differences, which are explained); `mkdocs build --strict` (or "not available"); INFLIGHT guard over
`base..tip`; linear history (no merge commits), at least one commit per ticket t0..t6; `git diff
base..tip -- tests/` removes no line except inside E1, E2 and recorded E-extra entries (map each removed
hunk); `config/sandbox.yml`, sealed files, every file of section 3 "Never edited", `CLAUDE.md` and every
`AGENTS.md` except the t6 commit of root `AGENTS.md` absent from the diff; `events.TYPES` and `ACTORS`
unchanged (`git diff base..tip -- src/scieflow/core/events.py` touches only `emit`). Writes
`RUN/scratch/m-i/pr-body.md` (plain English: the five changes, decisions M-I-D1..D18, absolute counts, the
CI reproduction result and what it does not prove, residuals R1–R8, "please merge with a merge commit"; no
credentials, prompts or patient data) and `reviews/m-i-branch-report.md`. Any failure → verdict `RED` with
node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the three GAP acceptance criteria — CI gate green in the
clean reproduction and statically D22-compliant, MCP tools listed and callable with the stub agent, invalid
event refused — and decisions M-I-D1..D18; the protected edit byte-exact), code and security (5.8: workflow
safety, refusal before write, no value in messages, MCP read-only/no network/dispatch refusal/L2/data
decision before every read of run content, including every run of the aggregated `open_gates` (refused
runs not read, not named, only counted)/sanitiser/cap/scope, parity), evidence (re-run memory-capped: `tests/test_ci_workflow.py
tests/core/test_records.py tests/core/test_events_validation.py tests/core/test_jobs_validation.py
tests/core/test_doctor_records.py tests/core/test_cli_parity.py tests/core/test_read_views_cli.py
tests/mcp_server tests/test_docs_platform.py tests/web/test_pages.py`; confirm the branch result's absolute
counts and CI reproduction; re-run the INFLIGHT guard; `pr-body.md` accurate). Output
`reviews/m-i-final-review-r<N>.md`. PASS → the installer's publish step; CHANGES → final_fix (new commits,
full suites and the CI reproduction re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-i-run.yml`, linear)

```
t0        Haiku   worktree, plan copy, green base, inventories   DONE→t1, BASE_RED→escalate
t1        Haiku   CI gate + local reproduction                   DONE→t1r
t1r       Sonnet  review                                         PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                              DONE→t1r
t2/t2r/t2f        event and job records, doctor                  same shape → t3
t3/t3r/t3f        read-only CLI views, parity table              same shape → t4
t4/t4r/t4f        MCP protocol, CLI, project tools               same shape → t5
t5/t5r/t5f        MCP run tools, access rules, e2e               same shape → t6   (installer inserts pe_t6)
t6/t6r/t6f        docs, CHANGELOG, CITATION, AGENTS.md           same shape → branch
branch    Haiku   full suites + CI reproduction + PR body        DONE→final, RED→escalate
final     Sonnet  whole-branch review                            PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                              DONE→final
(pe_t6, publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 75, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/m-i-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff, decisions,
gap; the installer adds `plan`. Protected edits: `pipelines/drafts/m-i-protected-edits.json`, one edit,
`before_step: t6`.

## 8. Risks and residuals (stated, not hidden)

- **R1 CI is proven locally, not on GitHub, before the merge.** The clean-clone reproduction proves the
  command, the lock file, the extras and hermeticity on Ubuntu 24.04; it cannot prove the GitHub runner's
  image (apt packages, AppArmor sysctl, CPU time). The first real run is on the M-I pull request; publish
  merges without waiting (M-I-D4). If that run is red, a follow-up fixes the workflow.
- **R2 Runner minutes.** Every PR push and every push to main runs the full suite (tens of minutes on a
  private repository's quota); `cancel-in-progress` drops superseded runs.
- **R3 Stricter events can surface latent bugs.** A code path that emitted a non-JSON value (silently
  turned into text before) or omitted a required key now fails loudly. The full suite exercises the known
  emitters; an untested path (or a milestone merged later, such as M-D3, M-G or M-H) may still hit it — its
  refresh or its own suite will show it.
- **R4 Note names.** `scieflow run log <slug> note.<name>` now refuses names with spaces or markup; the
  in-flight `core/run/cli.py` is not edited, so such a refusal shows Python's error output rather than a
  tidy message until the paper-lifecycle work lands.
- **R5 MCP client trust.** The server treats every client as a cloud agent; a user who runs a safe local
  model as MCP client still cannot read L2 runs through it (no override in M-I). Conversely the server
  cannot stop a client with a shell from reading files directly.
- **R6 Protocol interop.** The server follows the MCP stdio specification for the listed versions and is
  tested against spec-shaped messages only; no official client could be installed offline here. A human
  smoke with Hermes or Claude Code is follow-up F1.
- **R7 Sanitiser false positives.** Injection-looking phrases in a charter or draft come back redacted in
  MCP output (the same behaviour as search output); the files are unchanged.
- **R8 Scan cost.** Each per-run MCP call runs M-B's scan of that run (bounded by the policy's scan
  limits), so very large runs answer slowly.

## 9. After M-I

Queue (`continuity/milestones.yml`): F-2 waits on M-G, M-H, M-I and M-D3. Follow-ups surfaced while
planning (not in M-I): F1 real-client MCP smoke (human); F2 publish waits for the PR's CI checks (operator
tool, if you want it); F3 SHA-pinned actions for both workflows, `uv audit`/`pip-audit`, lint and type
gates; F4 MCP write tools (opt-in, exposure decision); F5 a safe-MCP-client declaration in
`config/data-policy.yml` (human-owned) if you ever run a safe local model as coordinator; F6 tidy refusal
message for `scieflow run log` once `core/run/cli.py` is no longer in flight; F7 a LICENSE file (your
choice), then CITATION.cff's `license`.
