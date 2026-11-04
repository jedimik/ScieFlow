# T-12 plan: agent configuration and workspace health in the browser (incl. the D4 effort dropdown) (round 1)

Pipeline `t-12-plan`, step plan, round 1, epoch 1. Card `t_dfadd17b`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261103_053954_53770b`, source kanban).
Written 2026-11-03. Previous review: none (round 1).

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

- `handoffs/pilot-roadmap-20261024/pilot-roadmap-20261011.md` section 2 (tickets 11/12, F+G).
- The approved F+G design: `approvals/fg-agent-config-workspace-health-design.md` (sha256 8e13dd90…9c18, the
  snapshot the human approved on 2026-10-09, `approvals/k-fg-human-review-20261009.md` sha256 6dc4b7b2…bca7, gate
  `01M4FQ217N0TRVRK322JGTFT32`). The copy on main, `docs/superpowers/specs/2026-10-08-f-g-agent-config-workspace-health-design.md`,
  differs from the snapshot only in its last line (line 353, a status sentence); the snapshot is binding and
  line numbers are the same in both. Its
  historical "PROPOSED" header is superseded by that approval. It is called **FG** below (`FG:<line>`).
- Ticket 12 invariants: `.scratch/scieflow-remaining-scope/issues/12-implement-agent-config-and-workspace-health.md`.
- **D4 (effort dropdown):** the human's G4 answer D4 (`approvals/k-task8-g4-decisions-20261014.json`, sha256 7ae3b119…9078):
  "effort can be for ScieFlow processing dynamically changed based on user input. Will be nice to have some
  dropdown menu to select based on subscription (every sub has different effort names)", and the operator note
  in `approvals/k-task8-g4-plan-amendment-20261014.md` (sha256 b07fc126…2356, section A3): the request belongs to
  the F+G design, "which today treats Claude reasoning as label-only", and "needs that design amended and
  approved before implementation". This plan carries that amendment (Part A section 5.2, decision T12-D1); the
  human's approval of this plan is the approval of the amendment.
- DECISIONS D7 (the web app is only an interface; CLI parity) and D9-D11/D23 (data protection) apply. The
  DECISIONS entry numbered D4 is about schedulers; it is unrelated to the "D4" of this milestone's title, which
  is the G4 answer above.

Prerequisite plan read (approved, not merged yet): `reviews/f-2-plan.md` (sha256 6f32e994…b55b, approved
`approvals/f-2-plan-approved-human-20261102.md`). Its section 3 file list, F2-D2 (effort env for conversation
turns), F2-D9 ("Ticket 12 owns that registry text and the `agent configure` warning wording") and residual R5.
Also read for overlap: `reviews/m-h-plan.md` and `reviews/m-i-plan.md` (F-2's own prerequisites).

Code read read-only from git objects (`git archive <sha> | tar -x` into the profile's cache scratch, and
`git show <sha>:<path>`); no checkout was touched.

- `M` = remote `main` = `f606bd108d8b3f2b5fdabc4f66f960af907d4478` (`git ls-remote` on this card; objects present
  locally). It contains PL (PR #26, `a42fc2e`) and M-D3 (PR #27, `RUN/scratch/m-d3/publish.json` `merged: true`).
  M-H (running), M-I (installed, held) and F-2 (installed, held) are **not** merged yet.
- Every `path:line` below is on `M`. F-2 edits `agent_run.py` (`resolve_turn`), `service.py` (`_say_prepared`),
  `menu.py` (static snapshot), `web/api.py`, `web/pages.py`, `templates/run.html`, `templates/job.html` and four
  docs pages; M-H adds a role to `agent_config.ROLES` and a workflow to `menu.py`; M-I adds `cli_parity.yml`
  entries and the MCP server. Their line shifts are expected; t0 re-verifies every cite on the real base.

## 1. For the human: what T-12 changes, in plain words

Today the browser's Agents page can only change **which agent does which role**. Everything else needs
`scieflow agent configure`, and there is no browser view of workspace health. T-12 builds the design you
approved on 2026-10-09 (F+G), plus the effort dropdown you asked for in G4 (D4).

1. **Edit agent settings in the browser, with an explicit scope.** On the Agents page you choose *Project
   defaults*, *Workspace `<run>`* or *News settings*. For each agent you can set its model, its effort
   (reasoning) and its timeout; for each role, which agent(s) do it, each with an optional model and effort for
   that role only. Every value shows what is stored here, what is inherited, what is in effect and where it comes
   from. News has its own form (timeout in **seconds**, its own effort names).
2. **The effort dropdown (D4).** Each agent's effort choices come from its own list in `config/agents.yml`
   (`menu.reasoning.levels`), so a Codex profile on one subscription can offer `low … max` while another offers
   different names; you edit that list to match your subscription. For Claude the choices are *default* and
   *extended thinking*, and choosing *extended thinking* now really takes effect everywhere: ScieFlow adds the
   32,000-token thinking budget itself, on one-shot dispatches as well as conversation turns (F-2 did turns).
   Nothing edits the agent's command line any more, neither the browser nor the terminal menu.
3. **Preview, then apply, and never a stale write.** Preview writes nothing and shows the changed keys, notes and
   warnings. Apply carries the preview's revision; if any file the preview read has changed since (someone ran
   `agent configure`, the menu or another browser tab), nothing is written and you are asked to preview again.
   The same protection now applies to `scieflow agent configure` (including `--yes`) and both menu save paths,
   because all of them write through one shared owner with one project lock.
4. **A read-only workspace index** (`/workspace`): every run with kind, state, last activity, aliases and lineage,
   without writing `INDEX.md`. Broken or oversized control files are named, never silently shown as empty.
5. **Run health** (`/runs/<run>/health`): the cheap checks at once (missing control files, legacy config keys, an
   old per-file DVC pointer, alias identity); the expensive ones (rebuildable folder sizes, absolute self-links,
   scripts in `logs/`) only when you press *Check now*, bounded to 10,000 entries and about two seconds, and
   marked *partial* with lower bounds when the budget stops them. Nothing is repaired, cleaned, uploaded or sent
   to a model or remote.
6. **What stays on the terminal (as you approved):** command templates (`cmd`, `stdin_cmd`), project `enabled`,
   and support-as-primary promotion and demotion. They are shown read-only, with the CLI command to use.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **T12-D1 The D4 amendment is part of this plan.** Approving this plan approves Part A section 5.2, which amends
  FG:55-58 ("General reasoning is sometimes only a label … Do not write templates/environment to 'make reasoning
  work' from the browser"). The amendment keeps that rule for templates (the browser still never writes a `cmd`)
  and instead makes `reasoning` itself effective for Claude, in code. (Alternative: keep Claude effort label-only
  in the browser; say so.)
- **T12-D2 Claude effort becomes a real setting, one rule everywhere.** An agent's *effort mode* is
  `placeholder` when one of its command templates contains `{reasoning}` (Codex, agy: unchanged), `thinking`
  when its model family is `claude` and no template has the placeholder (claude, claude-paper), otherwise `none`
  (stub). In `thinking` mode `reasoning: extended-thinking` adds the existing `env MAX_THINKING_TOKENS=32000 `
  prefix to `cmd`/`stdin_cmd` **in memory** for a one-shot dispatch, and `reasoning: default` removes it; a role's
  own effort still wins (the existing `apply_role_override`). No `reasoning` key keeps the command exactly as
  configured, so `claude-paper` keeps extended thinking from its `cmd` prefix. This is the same mechanism role
  overrides already use and the sandbox already allows (`sandbox.py:1606-1615`), so it is not a sandbox exposure
  change. It agrees with F-2's turn rule (F2-D2). Behaviour change: a run that stored `reasoning: default` or
  `extended-thinking` on a Claude agent (today "only a label", with a warning) now gets what it says.
- **T12-D3 The dropdown's choices.** `placeholder` agents: their `menu.reasoning.levels`, else
  `low, medium, high` (today's `menu.DEFAULT_LEVELS`), plus a custom text box because the CLI permits any value
  (an off-menu value keeps today's warning). `thinking` agents: exactly `default` and `extended-thinking`; the
  browser refuses any other value (the CLI still accepts it, with the existing "only a label" warning). `none`
  agents: no effort control in the browser. News: its own `low, medium, high` (`news/config.py:13`).
- **T12-D4 The terminal menu writes `reasoning`, not `cmd`.** Today the menu's Claude effort rewrites `cmd` and
  `stdin_cmd` (`menu.py:590-612`). With T12-D2 that would fight a stored `reasoning`, so the menu now sets
  `reasoning` (one existing test changes, E2).
- **T12-D5 One shared write owner.** `agent_configure.write(plan)` becomes the compare/replan/apply owner for every
  caller (`cli.py:286`, `menu.py:491`, `menu.py:647`, `service.py:1316`, and the new browser routes): project lock
  `config/.agent-configure.lock`, then the sibling `<file>.lock` of every file read or written, in path order;
  everything re-read and compared with the preview's revision; only then the approved files are replaced. A
  `Plan` not made by a planner is refused.
- **T12-D6 Every browser apply needs its preview's revision**, the existing role-assignment form included
  (`POST /agents`); two existing page tests first fetch the revision (E1).
- **T12-D7 Structured preview for the new forms.** The new preview lists changed keys with before/after values and
  never shows a command-template value, instead of a raw unified diff whose context lines would show `cmd` lines
  (FG:303). The existing assignment preview keeps its diff (no silent change, FG:304-305).
- **T12-D8 `GET /api/v1/agents` unchanged without `scope`.** It still returns the full effective configuration,
  command templates included (FG:304-305; M-I's MCP tool uses it). With `scope=project|workspace|news` it returns
  the new browser view, which never includes templates.
- **T12-D9 Safe reads for index and health.** A new module reads control files through directory descriptors,
  no-follow, non-blocking, regular files only, at most 256 KiB (+1 byte to detect oversize) before parsing
  (FG:230-245). Index: top-level only, at most 10,000 entries (FG:247-249). Scan: 10,000 entries aggregate and a
  two-second cooperative target (FG:264-269). `scieflow workspace doctor` stays byte-for-byte as today; scite
  readiness (part of the CLI doctor) is not run health and is not shown on the web health page.
- **T12-D10 Browser config writes refuse odd files.** A target must be a regular non-symlink file inside the project
  (or absent where the CLI would create it), at most 1 MiB; a run slug that is an alias (symlink) is refused for
  writes ("edit the run it points to"). The CLI keeps today's behaviour.
- **T12-D11 No run scan for "who inherits".** A project preview says in words that runs without their own override
  inherit the new value and runs with one keep it (FG:78); it does not read every run (FG:65).
- **T12-D12 JSON counterparts.** `POST /api/v1/agents/preview|apply`, `GET /api/v1/workspace`,
  `GET /api/v1/runs/{slug}/health`, `POST /api/v1/runs/{slug}/health/check` (FG:133-134 proposed them).
- **T12-D13 `config/agents.yml` (an in-flight path) is edited in exactly three places:** the header comment that
  says `menu:` "is read by coordinators, not by code" (it is read by code today, `menu.py:389-396`,
  `agent_config.py:427-432`), and the Claude and claude-paper `menu.reasoning.how` texts (F2-D9 hands them to
  T-12). The exact text is in `reviews/t-12-inflight-guard.py`; the guard refuses anything else.
- **T12-D14 Six new public service functions, no new CLI command.** Their parity entries name the existing
  commands that already do the same thing (`agent show`, `agent configure`, `workspace list`, `workspace doctor`),
  as `plan_staffing`/`apply_staffing` already do (`tests/core/cli_parity.yml:35-36`).

**Items for you (not in this run):**
- **H-T12-1** FG's alternative "scoped typed-confirmation exception editing" (promote/demote in the browser).
- **H-T12-2** Browser editing of project `enabled` (FG:60-69 deferral).
- **H-T12-3** Shared HTTP Host/Origin hardening (FG:146-155), unchanged here.
- **H-T12-4** Whether measured scan times justify a full-scan job (FG:278-280).
- **H-T12-5** Whether `GET /api/v1/agents` without `scope` should stop returning command templates (an API break).

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head `dev/t-12` →
base `main`, one PR, merge with a merge commit. Title: "T-12: agent configuration and workspace health in the
browser, Claude effort as a real setting (D4)". The body (written by the branch step) lists the changes above,
decisions, absolute test counts and the residuals of section 8.

## 2. Base, dependencies and worktree

T-12 starts only after F-2 is merged (queue `continuity/milestones.yml:30`, `after: [f-2]`; F-2 itself waits for
M-H, M-I, M-D3). t0 checks:

1. `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`; object-only fetch
   `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`.
2. `git merge-base --is-ancestor f606bd108d8b3f2b5fdabc4f66f960af907d4478 <base>` (`M`).
3. `RUN/scratch/f-2/publish.json`, `RUN/scratch/m-h/publish.json` and `RUN/scratch/m-i/publish.json` exist with
   `"merged": true`, and each `pushed_head` is an ancestor of `base`.
4. Otherwise `BASE_RED` with the reason (the supervisor escalates; no polling).

Worktree: `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/t-12 RUN/scratch/t-12-wt <base>` (on a
retry reuse it at `<base>` or `<base>` plus t0's docs commit; never reset). RUN =
`/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in `RUN/scratch/t-12/`
(junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under `~/.cache/scieflow-t-12-<step>/`. No ticket
touches `/home/ubuntu/Github/ScieFlow` or the checked-out tree of `/home/ubuntu/ScieFlow-claude-f2976f52`. Commits are
local on `dev/t-12`; review fixes are new commits.

Parallel lane: Ticket 8 (`t-8`, design-only, also `after: [f-2]`) may run at the same time; it writes a design
document only. The installer's refresh step handles main moving.

## 3. In-flight, never-edited, protected and changed files

**INFLIGHT** (the same 9 paths as every milestone since M-A; PL has landed them, the installer's standing rule
still guards them). T-12 edits **one** of them, `config/agents.yml`, in exactly the three hunks of T12-D13, and
none of the other eight:

```
config/agents.yml            (only the three hunks in reviews/t-12-inflight-guard.py)
docs/runs.md
schemas/status.yml
src/scieflow/core/run/actions.py
src/scieflow/core/run/checkpoint.py
src/scieflow/core/run/cli.py
src/scieflow/core/run/init.py
src/scieflow/core/run/status.py
src/scieflow/core/run/validate.py
```

Guard (must exit 0): `python3 -B RUN/reviews/t-12-inflight-guard.py RUN/scratch/t-12-wt <base>` (t0), and with
`--require-applied` from t1 on. It checks the eight other paths with `git diff --exit-code <base> -- …` (committed,
staged and unstaged changes all count) and that `config/agents.yml` is base text or base text with exactly the
three hunks. It was run on this card against a scratch clone of `M`: exit 0 unchanged; exit 0 with
`--require-applied` after applying the hunks (only the two `how` strings and a comment differ when parsed); exit 1
for an extra edit of `docs/runs.md` or of `config/agents.yml`.

**Never edited:** `config/sandbox.yml`, `config/defaults.yml`, `src/scieflow/core/sandbox.py`,
`src/scieflow/core/harness_commands.py`, `src/scieflow/core/harness_identity.py` (F-2), `src/scieflow/core/turn_*.py`,
`src/scieflow/core/run/conversation.py`, `src/scieflow/core/events.py`, `src/scieflow/core/jobs.py`,
`src/scieflow/news/*` (read only: `VALID_*`, `load_config`), `src/scieflow/web/auth.py`, `src/scieflow/web/app.py`,
`src/scieflow/web/sse.py`, `src/scieflow/web/review_views.py`, `scripts/sflib/*`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`),
everything under `tests/probes/`, `skills/**` (so F-2's menu-skill bytes and snapshot stay as merged), `.claude/**`,
every `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, `uv.lock`, `.github/workflows/*`, `mkdocs.yml`.

**Protected edits:** none. No agent-instruction file changes (root rule 13 already says agents change settings
only through `agent configure` after asking; the browser is the human's interface). No
`t-12-protected-edits.json`.

**Existing tests that change (and only these):**
- **E1** `tests/web/test_agents_page.py:39-45` and `:62-69`: before posting to `/agents`, each test GETs the preview
  page for the same assignment and posts its hidden `revision` (T12-D6). Their assertions are unchanged.
- **E2** `tests/core/test_menu.py:87-91` `test_claude_extended_thinking_prefixes_cmd`: renamed
  `test_claude_extended_thinking_sets_reasoning`; the scripted answers are unchanged; the assertion becomes
  `reg["agents"]["claude"]["reasoning"] == "extended-thinking"` and the `cmd` unchanged (T12-D4).
- **E3** `tests/web/mutating_paths.py` (test data): six new entries in both `MUTATING_PATHS` and `SAMPLES` (t4, t6).
- **E4** `tests/core/cli_parity.yml` (test data): six new entries (t3, t5, t6).
- **E-extra** (only if the full suite proves it necessary): a test that pins a literal this milestone rewrites
  (`menu --json` `effort_levels` of a `none`-mode agent, a docs sentence of t7, the reasoning warning text for a
  `thinking` agent). Each goes into `reviews/t-12-test-changes.md` with node id, reason and diff. Weakening,
  skipping or deselecting a test is never allowed. `tests/core/test_agent_config.py:69-75` must stay green
  unchanged (the new warning keeps the words "only a label").

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-11-03-t-12-agent-config-workspace-health.md`, `docs/superpowers/specs/2026-11-03-t-12-agent-config-workspace-health-design.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/core/agent_config.py` | `effort_mode`, `effort_levels_for`, `apply_effort`, `THINKING_LEVELS`, `DEFAULT_LEVELS`; validate: thinking wording, finite timeout | t1 |
| `src/scieflow/core/agent_run.py` | `prepare`: one `apply_effort` call before the role override | t1 |
| `src/scieflow/core/menu.py` | `effort_levels` delegates; `DEFAULT_LEVELS` re-exported; `_pick_effort` sets `reasoning` | t1 |
| `src/scieflow/core/agent_configure.py` | `coerce` refuses non-finite numbers | t1 |
| `config/agents.yml` | the three T12-D13 hunks | t1 |
| `tests/core/test_t12_effort.py` (new), `tests/core/test_menu.py` (E2) | | t1 |
| `src/scieflow/core/agent_configure.py` | read tracer, `Plan` scope/ops/deps/revision, `preview`, `apply` (= `write`), errors | t2 |
| `src/scieflow/core/store.py` | `exclusive(lock_path, timeout)`, `held_locks(paths, timeout)` → `LockSet.replace` | t2 |
| `src/scieflow/core/cli.py` | `configure`: preview + apply, stale/busy/partial → `ClickException` | t2 |
| `src/scieflow/core/menu.py` | both save paths: preview + apply, errors shown | t2 |
| `src/scieflow/core/service.py` | `plan_staffing` adds `revision`; `apply_staffing(…, revision=None)` requires it | t2 |
| `src/scieflow/web/pages.py` (`apply_agents` only), `src/scieflow/web/templates/agents.html` (one hidden input) | revision on the legacy apply | t2 |
| `.gitignore` | one line `config/.agent-configure.lock` | t2 |
| `tests/core/test_configure_owner.py`, `tests/core/test_store_locks.py` (new), `tests/web/test_agents_page.py` (E1) | | t2 |
| `src/scieflow/core/service.py` | `agent_config_view`, `plan_agent_changes`, `apply_agent_changes`, `ConfigChangeRefused`, `StaleConfiguration` | t3 |
| `tests/core/test_t12_agent_service.py` (new), `tests/core/cli_parity.yml` (E4, three entries) | | t3 |
| `src/scieflow/web/agent_views.py` (new: form ↔ change texts), `src/scieflow/web/pages.py`, `src/scieflow/web/api.py`, `src/scieflow/web/templates/agents.html` | scoped forms, preview/apply routes, API | t4 |
| `tests/web/test_t12_agents_page.py`, `tests/web/test_t12_agents_api.py` (new), `tests/web/mutating_paths.py` (E3, four entries) | | t4 |
| `src/scieflow/core/control_read.py` (new), `src/scieflow/core/workspace_health.py` (new: index), `src/scieflow/core/workspace.py` (extract `describe_from`, alias helper; CLI output unchanged), `src/scieflow/core/service.py` (`workspace_index`) | index | t5 |
| `src/scieflow/web/pages.py`, `src/scieflow/web/api.py`, `src/scieflow/web/templates/workspace.html` (new), `src/scieflow/web/templates/base.html` (one nav link) | index routes | t5 |
| `tests/core/test_control_read.py`, `tests/core/test_workspace_index.py`, `tests/web/test_t12_workspace_page.py` (new), `tests/core/cli_parity.yml` (E4, one entry) | | t5 |
| `src/scieflow/core/workspace_health.py` (health, bounded scan), `src/scieflow/core/workspace.py` (shared finding helpers; CLI output unchanged), `src/scieflow/core/service.py` (`run_health`, `scan_run_health`) | health | t6 |
| `src/scieflow/web/pages.py`, `src/scieflow/web/api.py`, `src/scieflow/web/templates/health.html` (new), `src/scieflow/web/templates/workspace.html` (health links) | health routes | t6 |
| `tests/core/test_run_health.py`, `tests/web/test_t12_health_page.py` (new), `tests/web/mutating_paths.py` (E3, two entries), `tests/core/cli_parity.yml` (E4, two entries) | | t6 |
| `docs/web.md`, `docs/agents.md`, `docs/menu.md` | docs | t7 |
| `tests/test_docs_t12.py` (new) | | t7 |

Production lines (estimate): `agent_config.py` +60, `agent_configure.py` +170, `store.py` +45, `service.py` +260,
`agent_views.py` 150, `pages.py` +150, `api.py` +70, `control_read.py` 150, `workspace_health.py` 330,
`workspace.py` ±30. Hotspots: `service.py`, `pages.py` and `api.py` are shared files (F-2, M-I touched them); T-12's
changes are additive new functions and routes; the only edits of existing functions are `plan_staffing`,
`apply_staffing` and `apply_agents` (t2).

## 4. Current code, verified (on M = f606bd1)

**Configuration layers and validation.** `agent_config.py`: `ROLES` `:40-55`; `AGENT_FIELDS` and
`WORKSPACE_AGENT_FIELDS` `:64-66`; `EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "` `:87-88`;
`ROLE_OVERRIDE_FIELDS` `:89`; `model_family` (falls back to `family`) `:97-109`; `Effective.to_json` (includes
`cmd`/`stdin_cmd` values) `:134-149`; `resolve_data` (legacy keys, run overrides, exceptions, role overrides)
`:184-253`; `apply_role_override` (Claude: prefix added for `extended-thinking`, removed otherwise) `:289-306`;
`validate` `:309-433`: timeout `:404-410` refuses only `bool`, non-number and `<= 0` (so `inf`/`nan` pass), the
reasoning warning "is only a label unless the cmd sets it (menu `how`)" `:417-426` and the menu-levels warning
`:427-432`.

**Planner and writer.** `agent_configure.py`: `Op` `:33-37`, `FileChange` `:40-51`, `Plan` (changes, notes,
warnings only) `:54-58`; `coerce` (`float()` accepts `inf`/`nan`) `:63-75`; `write` = one `store.write_text` per
change, no comparison `:275-277`; `plan_defaults` `:329-373` (reads `config/agents.yml` and `config/defaults.yml`
`:330-333`); `plan_workspace` `:376-464` (refuses a missing run dir `:378-379`, `enabled` `:427-431`, equality pruning
`:438-441`, legacy-shadow exception `:409-415`, the "no status.yml yet" note `:458-463`); `NEWS_FIELDS` `:467`;
`plan_news` `:474-515` (missing file `:481-482`, validation through `news.config.load_config` on a temp file
`:501-508`, agy warning `:510-514`); `news_settings` `:518-526`. `store.py`: `locked` = `FileLock(<path>.lock)` `:39-44`,
`_atomic_write` `:47-57`, `write_text` (locks, then writes) `:60-63`; `update_yaml` `:77-84`. `.gitignore:39-44`
ignores `*.yml.lock` etc., not `config/.agent-configure.lock`.

**Writers today (4).** `cli.py:286` (`configure`), `menu.py:491` (agent save), `menu.py:647` (news save),
`service.py:1316` (`apply_staffing`). No test calls `write` or builds a `Plan` directly (grep on `M`). CLI
`configure` `cli.py:211-287` (news/workspace exclusive `:240-241`, non-interactive without `--yes` refused
`:279-282`); `show` `:29-64`.

**Effort today.** One-shot `prepare` merges the run override `agent_run.py:304-305` and applies the role override
`:311-322`; `{reasoning}` is substituted only into templates that contain it (`:93-95`). The registry's `claude`
has no `reasoning` and no placeholder; its `how` says to override the whole `cmd` (`config/agents.yml:37-43`);
`claude-paper` has the prefix in `cmd` and `how` says to drop it (`:86-105`); the header says `menu:` "is read by
coordinators, not by code" (`:10-13`). The TUI's effort list `menu.effort_levels` returns the menu levels for a
`{reasoning}` `cmd`, else `["default", "extended-thinking"]` for **any** other agent (stub included) `menu.py:386-396`;
its Claude effort rewrites `cmd`/`stdin_cmd` `menu.py:590-612`. The sandbox allows an `env` prefix that sets only
`MAX_THINKING_TOKENS` `sandbox.py:1606-1615`. Tests: `tests/core/test_agent_config.py:69-75` (Claude `max` warns
"only a label"), `tests/core/test_menu.py:87-91` (TUI writes the prefix), `tests/core/test_staffing.py:57-63`
(`apply_role_override` both ways). News: its own adapter maps `low/medium/high` to thinking tokens
`news/agents.py:10,56-57`; `VALID_REASONING` `news/config.py:13`; timeout positive integer `:119-123`;
`curated_models` reads `config.repo_root()` (process cwd) `news/agents.py:21-36`.

**Service and web.** `service._ws` (`project.run_dir` + `is_dir`, follows symlinks) `service.py:80-87`;
`agent_settings` `:1268-1269`; `_staffing_plan`/`plan_staffing`/`apply_staffing` (replans, never compares)
`:1287-1318`. `ServiceError` → HTTP 404 JSON for every route (`web/app.py:55-57`); `HTTPException` → `{"error": …}`
with its status (`:59-61`). Pages: router with `require_session` `pages.py:33`, `MUTATE = [csrf_protect]` `:35`,
`_conversation_form` (async dependency, plain-`def` handler) `:113-126`, `agents_page` (GET preview through
`?assign=`) `:343-353`, `_agents_response` `:356-378`, `_run_dir_or_404` `:381-389`, `apply_agents` `:420-433`.
API `GET /api/v1/agents` `api.py:92-101`. Template `agents.html`: the "Per-agent fields, promotions and demotions stay
on the CLI" header `:8-13`, run selector `:15-23`, GET preview form `:39-48`, POST apply with `csrf_token` `:56-61`.
Nav `templates/base.html:13`. Route inventories `tests/web/mutating_paths.py:18-43` and `:52-81`; async rule
`tests/web/test_async_routes.py:57-87`; CSRF never in a query `tests/web/test_agents_page.py:15-28`.

**Workspace.** `workspace._load` (whole file, follows links, any error → `{}`) `workspace.py:50-55`; `_state`
`:58-75`; `_updated_at` `:89-94`; `_current_phase` `:97-105`; `describe` `:108-131`; `list_runs` (eager
`sorted(ws.iterdir())`, `entry.resolve()` follows aliases) `:134-154`; `moved_entries` `:157-166`; `_size` (stops
silently after 200,000 files) `:239-250`; `doctor` (walk to depth four, junk sizes, self-links, legacy keys, logs
scripts, per-file pointer, scite) `:253-301`; `index` writes `INDEX.md` `:343-373`; `doctor_cmd` `:409-439`.
`archive._skip_dir` is name-based (`scripts/sflib/archive.py:72-77`); `_archive_mod` falls back to `config.repo_root()`
(cwd) `workspace.py:230-236`. Descriptor-anchored reads exist elsewhere (`core/data/scan.py:110-118`,
`core/turn_artifacts.py:131-160,382-442`) but neither offers a bounded directory listing for this use.

**Parity table.** `tests/core/cli_parity.yml` holds one entry per public `service` function (`agent_settings: cli:
agent show`, `plan_staffing`/`apply_staffing: cli: agent configure` `:34-36`); test (a) fails for a public
function without an entry.

**Prerequisites (from their approved plans).** F-2 adds `harness_identity.turn_effort` (conversation turns:
`reasoning: extended-thinking`, or no `reasoning` and a prefixed `cmd`; `reasoning: default` off) and does not
touch `agent_config.py`, `agent_configure.py`, `agent_run.prepare`, `config/agents.yml` (F2-D8, F2-D9). M-H adds
role `research.sr-screeners` and a `WORKFLOWS` entry. M-I adds `cli_parity.yml` entries, read-only CLI views and
an MCP server exposing `agent_settings` unchanged (m-i-plan 5.4). T-12 changes none of their pins: no event type,
no job field, `agent_settings` output unchanged.

## Part A — Spec

### 5.1 Overview

```
browser (/agents, /workspace, /runs/<slug>/health)        CLI configure / TUI save
   │ forms → change texts (web/agent_views.py)                  │
   ▼                                                           ▼
service.plan_agent_changes / apply_agent_changes ──► agent_configure.preview / apply  ◄── one owner, one lock order
   (browser allowlist, scopes, file checks)                    planners (read tracer) → Plan(revision)
service.workspace_index / run_health / scan_run_health ──► workspace_health ──► control_read (descriptor reads)
agent_run.prepare ──► agent_config.apply_effort (one-shot Claude effort)
```

### 5.2 Design amendment A-D4: effort as a real, scoped setting (T12-D1..D4)

Interfaces in `src/scieflow/core/agent_config.py`:

- `THINKING_LEVELS = ("default", "extended-thinking")`; `DEFAULT_LEVELS = ("low", "medium", "high")` (moved from
  `menu.py`, re-exported there as a list for compatibility).
- `effort_mode(cfg: dict) -> str`: `"placeholder"` if any of `cmd`, `stdin_cmd`, `session_cmd`, `resume_cmd` (as
  text) contains `{reasoning}`; else `"thinking"` if `model_family(cfg) == "claude"`; else `"none"`.
- `effort_levels_for(cfg: dict, menu: dict) -> list[str]`: placeholder → `menu.reasoning.levels` as strings, else
  `DEFAULT_LEVELS`; thinking → `THINKING_LEVELS`; none → `[]`. `menu.effort_levels(eff, name)` returns
  `effort_levels_for` of the effective values (only `none`-mode agents change: `[]` instead of the Claude pair).
- `apply_effort(cfg: dict) -> dict` (pure, returns a copy): only in `thinking` mode and only when
  `cfg.get("reasoning")` is `default` or `extended-thinking`: for each of `cmd`, `stdin_cmd` present as text,
  remove a leading `EXTENDED_THINKING`, then add it back for `extended-thinking`. Every other case returns the
  copy unchanged.
- `agent_run.prepare`: `agent_cfg = agent_config.apply_effort(agent_cfg)` right after the run override merge
  (`agent_run.py:304-305`) and before the role override, so precedence is registry → run override → agent effort →
  role effort. The conversation turn path is F-2's (`resolve_turn`, `turn_effort`) and is not touched; a test pins
  that `turn_effort(cfg)` is set exactly when `apply_effort` would add the prefix, for the same configs.
- `validate` (`agent_config.py:417-432`): for a `thinking` agent, `default`/`extended-thinking` raise no warning; any
  other value warns `agent <name>: Claude effort is 'default' or 'extended-thinking'; reasoning <v!r> is only a
  label`; `placeholder` and `none` agents keep today's warnings and texts. The menu-levels warning is unchanged.
  `timeout_min` must also be finite (`math.isfinite`); `coerce` refuses `inf`/`nan` ("must be a finite number").
- TUI `_pick_effort` (`menu.py:574-612`): in `thinking` mode the two choices keep their values `default`/`extended`
  and labels; "current" is the effective state (`reasoning`, else the `cmd` prefix); the queued op is
  `set <name>.reasoning=default|extended-thinking`. Placeholder mode is unchanged.
- Dropdown rules for the browser: section 5.4.

### 5.3 The shared configurator owner (T12-D5, FG:172-218)

In `src/scieflow/core/agent_configure.py`:

- **Read tracer.** `_Reads(root)` records every planning read: `text(path)` → contents or `None`, recording
  `sha256:<hex>` or `absent`; `exists(path)`/`is_dir(path)` record the same digest form (a file consulted only for
  presence is still recorded by its content digest; a directory by `dir`/`absent`). Keys are canonical absolute
  paths. The planners take `reads=` (internal keyword) and route **every** read through it: `_load`, the
  `ac._load_yaml` calls in `plan_workspace`, `ws_dir.is_dir()`, `status.yml` presence, the news file (the
  temporary validation copy is not a dependency). A test proves the recorded set per scope: project =
  `{agents.yml, defaults.yml}`; workspace = those + run dir + `config.yml` + `status.yml`; news = `news.yml`.
- **Plan.** Gains `root`, `scope` (`default|workspace|news`), `slug`, `config_path`, `ops` (tuple), `deps`
  (dict), `revision`. `revision = sha256(canonical JSON {v: REVISION_VERSION, code: CODE_FINGERPRINT, root, scope,
  slug, config_path, ops: [[kind, key, value]…], deps, targets: {path: sha256(after)}})`. `CODE_FINGERPRINT` =
  sha256 of canonical JSON of `ROLES` (all four attributes), `AGENT_FIELDS`, `WORKSPACE_AGENT_FIELDS`,
  `ROLE_OVERRIDE_FIELDS`, `NEWS_FIELDS`, `EXTENDED_THINKING`, `THINKING_LEVELS` and news `VALID_AGENTS`,
  `VALID_REASONING`, `TOP_LEVEL_KEYS`, so a code-owned schema change invalidates a preview (FG:185-186).
- **Locks** (`store.py`): `exclusive(lock_path, timeout)` (one `FileLock` on that exact path) and
  `held_locks(paths, timeout) -> LockSet` (deduplicated, sorted absolute paths, `FileLock(<path>.lock)` each, acquired
  once in order, released in reverse). `LockSet.replace(path, text)` refuses a path it does not hold and calls
  `_atomic_write` (never `write_text`, so no nested `FileLock` on a held path). Timeout `CONFIGURE_LOCK_TIMEOUT =
  10` s; contention → `BusyError(ConfigureError)` "another configuration change is being written; preview again".
- **`preview(scope, root, ops, *, slug=None, config_path=None) -> Plan`**: under
  `exclusive(root/config/.agent-configure.lock)` only, runs the planner with a fresh tracer. Released before return.
- **`apply(plan) -> list[Path]`** (and `write = apply`): refuse a plan without `revision` ("not produced by a
  planner"). Under the project lock: replan from `plan.scope/slug/config_path/ops` with a fresh tracer; take
  `held_locks(files of deps ∪ targets)`; re-read every dependency under those locks and require the same digests;
  require `fresh.revision == plan.revision`; otherwise `StaleError(ConfigureError)` "the configuration changed
  since it was previewed; nothing written; preview again" — before any replacement, `--yes` included. Then replace
  the targets in path order. A failure on target *i* raises `PartialWriteError(ConfigureError)` with `written`
  (targets before *i*), `uncertain` (*i*) and `unchanged` (after *i*); no rollback is promised (FG:209-213).
- **Callers.** CLI `configure`: `plan = acf.preview(...)`; after confirmation `acf.apply(plan)`; every
  `ConfigureError` → `ClickException`. TUI agent and news save: same, errors printed in red, queued ops kept.
  `service.apply_staffing` and the new service functions: `apply`. The wizard's per-answer validation keeps calling
  the planners without locks (nothing is written there).
- **What it does not promise** (FG:204-205,215-218): arbitrary editors and agents writing files directly can still
  race after the comparison; in-flight jobs keep their argv; dispatch reads do not take these locks.

### 5.4 Service interfaces (`src/scieflow/core/service.py`)

New exceptions: `ConfigChangeRefused(ServiceError)` (invalid or not allowed; API 400) and
`StaleConfiguration(ServiceError)` (stale, busy or partial; API 409). New public functions:

- `agent_config_view(project, scope="project", slug=None) -> dict`. `scope` ∈ `project|workspace|news`;
  `workspace` requires `slug`, `news`/`project` refuse one. Returns `{scope, slug, project_root, agents, roles,
  exceptions, problems, warnings}` (project/workspace) or `{scope, available, settings, defaults_meaning, agents,
  levels, warnings}` (news). Per agent: `tier`, `enabled` (read-only), `effort: {mode, levels, current, custom_ok,
  how}`, `models` (menu suggestions), and for `model`, `reasoning`, `timeout_min`: `{stored, inherited, effective,
  source, editable, why_not}`; `cmd`/`stdin_cmd` only as `{configured: bool, cli_only: true}`, never their text.
  Per role: `many`, `support_ok`, `help`, `value`, `source` (`default`, `workspace`, `workspace (legacy key)`,
  `default (= research.draft-authors)`), `overrides: {agent: {model?, reasoning?, source}}`, `exception` (read-only
  support-as-primary source or `None`). News: `available: false` with "initialize news with `scieflow news init`"
  when `config/news.yml` is missing (FG:96); unset model/reasoning mean "the agent CLI's own default", unset timeout
  "the per-template default (seconds)" (FG:91-93); agy carries the support-tier note (FG:97-98).
- `plan_agent_changes(project, scope, changes: list[str], slug=None) -> dict` and
  `apply_agent_changes(project, scope, changes, revision, slug=None) -> dict`. `changes` use the CLI's own syntax so
  the CLI's parsing and coercion are reused (FG:159): `assign ROLE=AGENT[@MODEL][/EFFORT][,…]`,
  `set AGENT.FIELD=VALUE`, `unset KEY` (news: `set FIELD=VALUE`, `unset FIELD`). Plan returns `{scope, slug,
  changes (canonical), revision, files, structured: [{file, key, before, after}], notes, warnings, inheritance,
  empty}`; apply returns `{written, warnings}`.

**Browser allowlist** (server-side; a disabled control is not a constraint, FG:53):

| Request | project | workspace | news |
|---|---|---|---|
| `set/unset AGENT.model`, `AGENT.reasoning`, `AGENT.timeout_min` | yes | yes (unset = reset to inherit) | — |
| `assign ROLE=…` (entries with optional model/effort) | yes | yes | refused: unsupported (no roles) |
| `unset ROLE` | refused by the planner (required) except `research.draft-reviewers` | yes (reset) | refused: unsupported |
| `AGENT.cmd`, `AGENT.stdin_cmd` | refused: **deliberately CLI-only** (executable templates) | same | — |
| `AGENT.enabled` | refused: **deliberately CLI-only in this slice** | refused: **unsupported** (`enabled` is global) | — |
| `promote`/`demote` | refused: **deliberately CLI-only** | same | — |
| any other field, unknown agent or role | refused: **unsupported** | same | — |
| `set/unset agent|model|reasoning|timeout` | — | — | yes |
| any other news key | — | — | refused: **unsupported in news settings** |

Value rules: model non-empty, at most 200 characters, no control characters; effort per T12-D3 (at most 64
characters, no control characters; a `thinking` agent only `default`/`extended-thinking`, a `none` agent none);
`timeout_min` via `coerce`, finite and > 0 (minutes); news `timeout` a positive integer (seconds), `agent` ∈
`VALID_AGENTS`, `reasoning` ∈ `VALID_REASONING`, both via `load_config` as today; at most 50 changes. Refusal
messages name the kind ("deliberately CLI-only" or "unsupported") and the CLI command where there is one.

Scope and file rules: the scope is explicit on every write (missing or unknown → refused, never project);
`workspace` canonicalises the slug through `Project.run_dir` and requires an existing real directory (an alias is
refused, T12-D10); news uses the server-selected `config/news.yml` (no `config_path` from HTTP, FG:125); before
planning, each fixed input and target (`config/agents.yml`, `config/defaults.yml`, `config/news.yml`,
`workspace/<slug>/config.yml`, `status.yml`) must be absent or a regular, non-symlink file of at most 1 MiB inside
`project.root`; for a project apply the resulting plan may touch only one of `agents.yml` / `defaults.yml` (browser
only, FG:207-208). Diagnostics never quote file content: a YAML error becomes "`<rel path>`: not valid YAML (line
N)", a non-mapping "`<rel path>`: not a mapping".

Structured preview (T12-D7): computed from the parsed before/after documents of each changed file; only keys whose
value differs; the value of any key named `cmd`, `stdin_cmd`, `session_cmd` or `resume_cmd` is shown as
"(command template, not shown)". `inheritance` for a project change: "Runs that do not override <key> will use
the new value; runs with their own override keep it." (no scan, T12-D11). A workspace reset shows the planner's
effective result and its source, so a legacy key that becomes visible again is labelled (FG:86-88).

Existing functions: `plan_staffing` gains the key `revision` (additive); `apply_staffing(project, assignments,
slug=None, revision=None)` refuses without a revision ("preview the change first") and applies through the owner.
`agent_settings` is unchanged.

Parity entries (E4): `agent_config_view: cli: agent show`; `plan_agent_changes` and `apply_agent_changes: cli:
agent configure`; `workspace_index: cli: workspace list`; `run_health` and `scan_run_health: cli: workspace doctor`.

### 5.5 Web routes, forms and API

Pages (all plain `def`, all under `require_session`; POSTs under `MUTATE`):

| Route | Purpose |
|---|---|
| `GET /agents?scope=project|workspace|news[&slug=]` | Scoped read view and edit forms. No `scope`: `slug` given → workspace, else project (compatibility aliases, FG:75-77). `?assign=` keeps today's GET role preview. |
| `POST /agents/preview` | Form → change texts (`web/agent_views.py`) → `plan_agent_changes`; re-renders the page with the structured preview, hidden canonical `change` fields and `revision`. Writes nothing. |
| `POST /agents/apply` | `scope`, `slug`, `change`×n, `revision` → `apply_agent_changes`; success 303 to `/agents?scope=…[&slug=…]&ok=…`; refusal re-renders the page with the submitted safe fields and the reason (FG:132). |
| `POST /agents` (existing) | Now also requires `revision` (from the GET preview); missing → redirect with "preview the change first". |
| `GET /workspace` | Index (5.7). |
| `GET /runs/{slug}/health` | Cheap findings (5.8). |
| `POST /runs/{slug}/health/check` | Bounded scan, rendered directly; nothing stored. |

API: `GET /api/v1/agents` unchanged without `scope`; with `scope` the view (T12-D8). `POST /api/v1/agents/preview`
and `/apply` (form fields as the page; 400 `ConfigChangeRefused`, 409 `StaleConfiguration`). `GET /api/v1/workspace`,
`GET /api/v1/runs/{slug}/health`, `POST /api/v1/runs/{slug}/health/check`. New mutating paths (E3):
`/agents/preview`, `/agents/apply`, `/api/v1/agents/preview`, `/api/v1/agents/apply`, `/runs/{slug}/health/check`,
`/api/v1/runs/{slug}/health/check`, each in `MUTATING_PATHS` and `SAMPLES` (FG:137-139), even the read-only scan.

Forms: one form per agent (model `<select>` of menu suggestions + "custom" text box; effort `<select>` built from
`effort.levels` with "inherit"/"not set", plus a custom box only when `custom_ok`; timeout `<input type=number>`),
one per role (agent pickers, one row per entry for fan-out roles, each with optional model and effort), a news form
(agent, model, reasoning, timeout in seconds), all `method="post"` with `name="csrf_token"`; free-text values never
go into a query string (FG:129). `enabled`, `cmd`/`stdin_cmd` and support-as-primary exceptions are shown as text
with the CLI command. Every value is autoescaped; no `|safe`; no new JavaScript. Header text (`agents.html:8-13`)
says which edits are available and what stays CLI-only. Nav (`base.html:13`) gains a `workspace` link.

### 5.6 Safe control reads (`src/scieflow/core/control_read.py`)

- `CONTROL_CAP = 256 * 1024`; `ControlRead(status, data, reason)` with status ∈ `ok|missing|symlink|not-regular|
  oversized|unreadable|malformed|invalid-shape|changed|unsupported`.
- `open_dir(root: Path, parts: tuple[str, ...]) -> int`: opens `/` and every component with
  `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` (relative to the previous descriptor); a symlink component fails.
- `read_yaml(dir_fd, name, cap=CONTROL_CAP) -> ControlRead`: `open(name, O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,
  dir_fd=…)`; `fstat` must be `S_ISREG`; read at most `cap + 1` bytes (oversized if more, never parsed); `fstat`
  again and compare `(st_dev, st_ino, st_size, st_mtime_ns)` → `changed`; UTF-8 decode and `yaml.safe_load` →
  `malformed` on error; not a mapping → `invalid-shape`. `ENOENT` → missing, `ELOOP` → symlink, `EACCES`/`EPERM` →
  unreadable, `ENXIO` (FIFO without writer) or non-regular → not-regular. Reasons are category words and the
  relative name only, never content.
- `stat_entry(dir_fd, name) -> str` (no-follow): `file|dir|symlink|other|missing`; `read_link(dir_fd, name)`.
- `scan_dir(dir_fd, limit)`: streams `os.scandir(dir_fd)` entries one by one (no listing, no sort) and stops at
  `limit`.
- Linux primitives required (`O_NOFOLLOW`, `O_DIRECTORY`, `dir_fd` support); otherwise every read is
  `unsupported` (refused, not empty).

### 5.7 Workspace index (`workspace_health.index(project, *, limit=10_000)`)

Pins `project.root/workspace` with `open_dir`; streams at most `limit` top-level entries; skips names starting with
`.` or `_` (counted as inspected) and `news`, `chats` (`workspace.py:24,143`). For a symlink entry: `read_link`; a
target of `_misc/…` is a *moved* entry (as `moved_entries`); a single relative name without `/` (or `./name`) is an
alias of that sibling; anything else is an *external alias*, shown with "points outside workspace/; not followed"
(FG:226). For a real directory: `read_yaml(status.yml)`, `read_yaml(config.yml)`, no-follow stats of
`status.yml`, `log.md`, `notebook.md`, `events.jsonl` for last activity, then `workspace.describe_from(slug, status,
config, updated, updated_at)` (extracted from `describe`, which calls it, so the CLI output is unchanged) for kind,
state, phase and lineage. A control that is not `ok` adds a finding `{file, status}` and leaves the derived
fields `unknown`, never a reassuring default (FG:243,287-288). Collected rows are sorted; the result is
`{observed_at, complete, inspected, limit, omitted_note, runs, external_aliases, moved, lineage}`. It never writes
`INDEX.md` or state, never recurses, never follows an alias. `service.workspace_index(project)` wraps it.

### 5.8 Run health (`workspace_health.health` and `.scan`)

- Classification of `/runs/<slug>`: `Project.run_dir` canonical slug; `stat_entry` on the workspace dir: symlink →
  `{slug, alias_of | external_alias}` and nothing else (no scan, FG:226); missing/not a dir → `ServiceError` (404).
- `health(project, slug) -> dict` (GET, cheap, fixed reads only, FG:253-254): `kind`, `state` (from `status.yml`),
  `missing` (the `LOOP_FILES`/`RESEARCH_FILES` of `workspace.py:25-26` checked with `stat_entry`; a symlink or
  non-regular control is listed as such), `legacy_config_keys` (same key list as `doctor`, through a shared helper
  `workspace.legacy_keys(config)`), `per_file_dvc_pointer` (`stat_entry(workspace, <slug>.dvc)`), and
  `junk_dirs`, `absolute_self_links`, `scripts_in_logs` with status `not-checked`. Every check carries
  `status ∈ complete|not-checked|refused` plus the control-read finding where one applies.
- `scan(project, slug, *, max_entries=10_000, max_seconds=2.0, clock=time.monotonic) -> dict` (POST): an explicit
  depth-first walk with `open_dir`/`scan_dir` descriptors, never following a symlink, depth limit four as `doctor`
  (`workspace.py:280`), junk classification by `archive._skip_dir` (module loaded with the project root, never the
  process cwd), junk sizes by a nested streaming walk, the self-link predicate `workspace.is_self_link(target,
  run_path)` (shared with `doctor`), and `logs/*.py` by streaming `logs/`. **One budget** counts every yielded entry
  of all three walks (skipped ones and repeated enumeration included); the clock is checked between operations;
  when either limit is reached the scan stops before the next operation. Result per check: `complete` with the
  value, or `partial` with a lower bound (`at_least`), plus `entries_observed`, `stop_reason` (`entries`, `time`,
  `refused:<category>` or `none`), `observed_at` and the note "results may change while a run is active". A
  partial value is never reported as a total and an unchecked check never as passed (FG:270-271). No job, SSE,
  subprocess, model, remote or git call.
- Parity: on fixtures that fit in the budget, `scan` + `health` give the same `missing`, `legacy_config_keys`,
  `junk_dirs` (sizes), `absolute_self_links`, `scripts_in_logs` and `per_file_dvc_pointer` as `workspace.doctor`
  (FG:275-276). `doctor` and `doctor_cmd` keep their behaviour and output, the 200,000-file `_size` cap included
  (FG:273-275).

### 5.9 Docs (exact sentences, pinned by `tests/test_docs_t12.py`)

- **T1** (`docs/agents.md`, effort): "Claude effort is `reasoning: default` or `reasoning: extended-thinking`: ScieFlow
  adds `MAX_THINKING_TOKENS=32000` itself (the `env` prefix of a one-shot dispatch, or a conversation turn's
  environment), so choosing it never edits the `cmd`."
- **T2** (`docs/agents.md`): "The effort choices for an agent are its own `menu.reasoning.levels` in
  `config/agents.yml`; list the effort names your subscription uses there."
- **T3** (`docs/web.md`, What stays CLI-only): "Executable command templates (`cmd`, `stdin_cmd`), project `enabled`,
  and support-as-primary promotion and demotion stay CLI-only; a workspace cannot set `enabled` at all."
- **T4** (`docs/web.md`): "Every browser apply carries the revision of its preview; if any file the preview read has
  changed since, nothing is written and the page asks you to preview again."
- **T5** (`docs/web.md`): "The workspace index and the health page only read: they never write `INDEX.md`, repair,
  clean up, upload or contact a remote or a model."
- **T6** (`docs/web.md`): "A health check is partial when it stops at its budget (10,000 entries or about two
  seconds); a partial size is a lower bound, never a total."
- **T7** (`docs/web.md`, API): "`GET /api/v1/agents` without `scope` still returns the full effective configuration,
  command templates included; with `scope=` it returns the browser view, which never includes them."
- **T8** (`docs/menu.md`): "The menu's Claude effort choice sets `reasoning`; it no longer edits the agent's `cmd`."

Also: `docs/web.md` page table (row "Agents", new rows "Workspace" and "Run health"), API table (new rows),
"What stays CLI-only" (`docs/web.md:530-549`: the "Per-agent field edits…" bullet is replaced by T3; `--promote`
stays), `docs/agents.md` "The Agents page" (`:250-276`) rewritten for scopes, inherited/effective values, preview
and apply, reset meaning (project unset removes an optional registry field, not "restore shipped defaults"; a
workspace reset removes only that override, FG:82-85), news units; "Effort per provider" (`:114-128`) gains T1/T2.
Every literal pinned by an existing docs test stays (F-2's S1-S8 included; t0 lists them). No sentence claims HTTP
hardening, enabled editing, promotion in the browser or a health check of scientific validity, model access or
remotes (FG:286).

### 5.10 Security and data properties (checked by every review that touches them)

1. Server-side allowlist (5.4) for page and API alike; forged excluded/unsupported operations refused with their
   kind; nothing reaches a planner that the matrix forbids.
2. No new authority: the browser never writes a command template, `enabled` or an exception; effort becomes
   effective only through the existing, sandbox-allowed prefix (T12-D2) and F-2's turn env.
3. Stale protection: every apply compares the full traced read-set (absence included), scope, ops, targets, result
   and code fingerprint under the lock protocol; no blind write remains on any of the five write paths.
4. Locks: project lock first, sibling locks once in path order, already-held replacement, no lock held across human
   confirmation or model execution; contention refuses.
5. Paths: request-bound `Project` only (no `config.repo_root()` on a request path; the news model suggestions come
   from the registry already read for the view, not `news.agents.curated_models`); slug canonicalised and contained;
   alias and non-regular targets refused; news path server-selected.
6. Control reads: descriptor-anchored, no-follow, non-blocking, regular-file, byte-bounded before parse; distinct
   findings; no content in diagnostics.
7. Cost: GETs do fixed bounded work (index: ≤ 10,000 entries, two control reads per run); scans only on an explicit
   POST with one aggregate budget; CLI defaults untouched.
8. Web: plain `def` handlers; session and CSRF on every POST (also the read-only scan and preview); CSRF token never
   in a query; autoescaped output, no `|safe`; existing guards unchanged; the DNS-rebinding residual stays
   documented (FG:141-144).
9. Data protection (M-B): unchanged; T-12 sends nothing to a model and reads no run content beyond control files
   (`status.yml`, `config.yml`) and directory metadata.

### 5.11 What T-12 does not do

No promote/demote or `enabled` editing in the browser (H-T12-1, H-T12-2), no command-template or environment editor,
no new agent, tier, family or session command, no live model discovery or probe, no Host/Origin hardening
(H-T12-3), no repair/cleanup/upload/remote/model call, no stored findings, no background scan job (H-T12-4), no
change to `GET /api/v1/agents` without `scope` (H-T12-5), no change to news's own command lines or the turn path,
no archive browsing (Tickets 8/9).

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`); Sonnet 5.5
@ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a checkpointed
pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only), no push/PR, no
credentials or token files (tests use the existing fake-credential fixtures), no patient data (synthetic fixtures
only), never edits a never-edited file of section 3, runs the INFLIGHT guard and reports its exit. Tests are
hermetic: temporary projects only, no read of the real home or the RUN layout; clocks and budgets are injected.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/t-12-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-t-12-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/t-12/junit G6_CACHE_DIR=~/.cache/scieflow-t-12 RUN/tools/g6_pytest.sh WT <head-sha> t-12-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing rules
  (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with completion
  notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> t-12-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **INFLIGHT guard:** `python3 -B RUN/reviews/t-12-inflight-guard.py WT <base>` (t0) / `… --require-applied` (t1 on).
- **Load-flake nodes:** the four named in the installer's standing rules, one rerun, both results recorded. No
  rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/t-12/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused sets** (clean-tree runner on the ticket's final commit):
  t1 `tests/core/test_t12_effort.py tests/core/test_agent_config.py tests/core/test_staffing.py tests/core/test_menu.py tests/core/test_agent_run.py tests/core/test_agent_configure.py` plus `tests/core/test_harness_identity.py` if F-2 put it on base;
  t2 `tests/core/test_configure_owner.py tests/core/test_store_locks.py tests/core/test_agent_configure.py tests/core/test_menu.py tests/core/test_store.py tests/web/test_agents_page.py tests/core/test_staffing.py`;
  t3 `tests/core/test_t12_agent_service.py tests/core/test_service.py tests/core/test_cli_parity.py tests/core/test_configure_owner.py`;
  t4 `tests/web`;
  t5 `tests/core/test_control_read.py tests/core/test_workspace_index.py tests/core/test_workspace.py tests/web/test_t12_workspace_page.py tests/core/test_cli_parity.py`;
  t6 `tests/core/test_run_health.py tests/core/test_workspace.py tests/web tests/core/test_cli_parity.py`;
  t7 every `tests/test_docs*.py`, plus `mkdocs build --strict` into `RUN/scratch/t-12/site` if mkdocs is in the venv.
- Tests that need a project use the existing fixtures (`tests/web/conftest.py` `project`/`client`,
  `tests/core/conftest.py` `repo`/`write_ws`, `tests/core/test_menu.py` `ScriptedUI`); no agent CLI is launched
  except the existing stub.

### t0 — worktree, plan and spec copy, green base, inventories (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the ancestor check and the three publish receipts; otherwise
   `BASE_RED` with the reason. `worktree add -b dev/t-12 RUN/scratch/t-12-wt <base>` (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed probes 0
   failed (directory run and the explicit sealed file). No expected counts are given; t0 records what it measures.
   Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths.
3. Re-verify every `path:line` cite of section 4 on `base`; write `reviews/t-12-t0-baseline.md` (per cite: still
   present / moved (new line) / changed). Inventories: (a) the merges between `M` and `base` and which section-3
   files they changed; (b) every caller of `agent_configure.write`/`plan_*`/`news_settings` in `src/` and every test
   that drives them (CLI `configure`, TUI saves, `service.plan_staffing`/`apply_staffing`), and any code building a
   `Plan` or `FileChange`; (c) every test asserting effort behaviour: "only a label", `effort_levels`, the TUI's
   `cmd` prefix, a one-shot argv of a `claude`-family agent with `reasoning` set, `turn_effort` (F-2); (d) how
   `core/review`'s `set_review_settings` writes `workspace/<slug>/config.yml` (which lock, if any); (e) every docs
   test literal in `docs/web.md`, `docs/agents.md`, `docs/menu.md` (F-2's S1-S8 and earlier pins); (f) the public
   `service` functions on base against `tests/core/cli_parity.yml`, and whether the M-I MCP tool list or its parity
   test would need an entry for a new function; (g) `tests/web/mutating_paths.py` and
   `tests/web/async_allowlist.py` on base; (h) `scieflow menu --json` from WT on base (run as
   `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -c "from scieflow.cli import main; main(['menu', '--json'], prog_name='scieflow')"`) saved to
   `RUN/scratch/t-12/menu-json-base.json` with its sha256; (i) the sealed suite's count; (j) whether
   `config/agents.yml` on base still contains the three old hunk texts exactly once (the guard on base exits 0).
4. Copy `reviews/t-12-plan.md` byte for byte to `docs/superpowers/plans/2026-11-03-t-12-agent-config-workspace-health.md`,
   and its Part A (from the line `## Part A — Spec` up to, not including, the line `## Part B — Implementation plan`) to
   `docs/superpowers/specs/2026-11-03-t-12-agent-config-workspace-health-design.md` with this first line:
   `# T-12 agent configuration and workspace health — design (Part A of docs/superpowers/plans/2026-11-03-t-12-agent-config-workspace-health.md)`
   and one blank line. Commit both alone ("docs(plans): T-12 agent configuration and workspace health spec and plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — effort as a real setting (A-D4) (Haiku) → t1r (Sonnet)

Files: section 3 rows t1. Apply the three `config/agents.yml` hunks exactly (the guard's `--require-applied` then
exits 0).

RED → GREEN:
1. `effort_mode`: codex/agy/codex-paper/codex-review → `placeholder`; claude, claude-paper → `thinking`; stub → `none`;
   an agent with `model_family: claude` and a `{reasoning}` template → `placeholder`; one with only `family: claude` →
   `thinking`.
2. `effort_levels_for`: menu levels as strings; `DEFAULT_LEVELS` without levels; the Claude pair; `[]` for `none`.
   `menu.effort_levels` equals it for every agent of the repo registry; `menu --json` may differ from
   `menu-json-base.json` only in `none`-mode agents' `effort_levels`; on the repo registry it lists no such agent
   (checked on `M`: claude, claude-paper → the Claude pair; codex profiles → their five levels; agy → low/medium/high),
   so it is expected byte-identical (both sha256 recorded in the result).
3. `apply_effort`: prefix added once for `extended-thinking` (also when already present: never doubled), removed for
   `default`, unchanged without `reasoning`, for a label value, and for `placeholder`/`none` agents; `cmd` and
   `stdin_cmd` both; input dict not mutated.
4. One-shot `prepare` (`agent_run`): a claude-family test agent with `reasoning: extended-thinking` in the registry,
   in a run's `agent_overrides`, or both: the launched argv starts with `env MAX_THINKING_TOKENS=32000`; with
   `reasoning: default` and a prefixed `cmd` it does not; a role override `/default` beats an agent-level
   `extended-thinking` and `/extended-thinking` beats `default`; codex argv unchanged byte for byte; a sandboxed
   dispatch with a credential still passes `_check_prefix`.
5. Agreement with F-2: for a table of configs, `turn_effort(cfg) is not None` exactly when `apply_effort(cfg)["cmd"]`
   starts with the prefix (skip only if F-2's function is absent on base, recorded).
6. `validate`: a `thinking` agent with `default`/`extended-thinking` → no reasoning warning; `max` → the new warning
   containing "only a label" (`test_agent_config.py:69-75` unchanged and green); codex `ultra` warning unchanged;
   `timeout_min: .inf` / `.nan` in YAML → problem; `coerce("timeout_min", "inf")` and `"nan"` refused.
7. TUI (E2): the scripted Claude "extended" choice queues `set claude.reasoning=extended-thinking`, writes `reasoning`,
   leaves `cmd`/`stdin_cmd` unchanged; "current" reflects the stored `reasoning` first.
8. `config/agents.yml` parses; only the three texts differ from base (guard `--require-applied` exit 0).

t1r checklist: rule identical in `apply_effort`, `effort_mode`, `validate` and the TUI; precedence registry → run →
agent effort → role effort; no change to the turn path, `harness_identity.py`, `sandbox.py` or news; argv of
non-Claude agents byte-identical; the sealed suite 0 failed under the lock; INFLIGHT guard (`--require-applied`) exit 0.

### t2 — the shared configurator owner (Haiku) → t2r (Sonnet)

Files: section 3 rows t2.

RED → GREEN:
1. Read-set: `preview` records exactly the sets of 5.3 per scope (and absence: a missing `config.yml`/`status.yml`
   is recorded as `absent`); a dependency created after the preview (e.g. `status.yml`) makes `apply` stale.
2. Stale refusal, before any replacement, for each of: a dependency's content changed, a dependency removed or
   created, a non-target dependency changed (e.g. `agents.yml` during a workspace apply), a different op list,
   scope or slug, and a monkeypatched `CODE_FINGERPRINT`; every target's bytes read back unchanged; CLI
   `configure --yes` with a file changed between its preview and apply (monkeypatched hook) exits 1 and writes nothing.
3. Happy path: `apply(preview(...))` writes exactly the planned `after` text; comments and unrelated keys preserved
   (existing round-trip tests stay green).
4. Locks: the project lock is taken before any sibling lock; sibling locks in sorted path order, each once (a spy on
   `FileLock.acquire`); `LockSet.replace` of a path not held raises; no `store.write_text` call inside `apply` (spy);
   a held project lock from another process (a helper subprocess holding `config/.agent-configure.lock`) makes
   `apply` and `preview` raise `BusyError` within the timeout (timeout monkeypatched small).
5. Partial failure: `_atomic_write` failing on the second of two targets → `PartialWriteError` with `written`=[first],
   `uncertain`=[second], `unchanged`=[]; the first file holds the new bytes and the second the old ones.
6. A `Plan(changes=[…])` built by hand → `apply` refuses, nothing written.
7. Callers: CLI `configure` (interactive and `--yes`), TUI agent save and news save, `service.apply_staffing` all go
   through `apply` (spies); TUI stale → red message, queued ops kept, nothing written; `service.plan_staffing` returns
   `revision`; `apply_staffing` without revision → `ServiceError`; with the preview's revision → writes; with a stale
   one → `ServiceError` naming "preview again".
8. Page (E1): the GET preview page carries one hidden `revision`; the two E1 tests pass after fetching it; posting
   without revision redirects with an error and writes nothing.
9. `.gitignore` ignores `config/.agent-configure.lock`.

t2r checklist: no write path left that skips the comparison (grep for `store.write_text`/`_atomic_write` callers in
`agent_configure`); no lock held while waiting for confirmation (code read of CLI/TUI); lock order and single
acquisition proven; the read-set is traced, not hand-listed; partial outcomes reported honestly; existing
`test_agent_configure.py`, `test_menu.py`, `test_staffing.py` green; INFLIGHT guard exit 0.

### t3 — service layer for scoped settings (Haiku) → t3r (Sonnet)

Files: section 3 rows t3.

RED → GREEN:
1. `agent_config_view` per scope on the core `repo` fixture: stored/inherited/effective/source for model, reasoning,
   timeout_min (workspace override vs registry), role values with each source label (default, workspace, legacy key,
   follows authors), role overrides, exceptions read-only with source, `enabled` read-only, effort block per mode;
   no `cmd`/`stdin_cmd`/`session_cmd`/`resume_cmd` text anywhere in the JSON (recursive string search for the
   templates' bytes); news view with and without `config/news.yml`.
2. Allowlist matrix: every row of the 5.4 table, page-independent: excluded → message contains "deliberately
   CLI-only"; unsupported → "unsupported"; forged `promote`/`demote`, `cmd`, `enabled` (project and workspace),
   unknown field/agent/role, news `timeout_min`/roles/`cmd`.
3. Value rules: negative, zero, non-numeric, `inf`, `nan` timeout; empty, too long and control-character model and
   effort; a `thinking` agent's `max`; news timeout `1.5`, `0`, `"x"`; news reasoning `xhigh`; each refused with its
   reason and every target file read back unchanged (ticket 12 DoD).
4. Scope rules: write without scope, unknown scope, workspace without slug, `news`+slug, `project`+slug, `../x`
   slug, alias (symlink) slug, missing run → refused; a symlinked or oversized (> 1 MiB) `config/agents.yml` or run
   `config.yml` → refused with a safe message; malformed YAML → "not valid YAML (line N)" with no file content in
   the message.
5. Isolation: a workspace change touches only that run's `config.yml` — `defaults.yml`, `agents.yml`, `news.yml` and a
   second run's `config.yml` are byte-identical afterwards; a news change touches only `news.yml`; a project change
   touches one file family; mixed registry + assignment changes → refused in the browser, while CLI `configure`
   still applies the same mix (FG:209).
6. Reset semantics: workspace `unset AGENT.FIELD` removes only that override; setting a workspace value equal to
   the registry value removes the delta; a role reset that exposes a legacy key previews the legacy value with
   source `workspace (legacy key)`; project `unset ROLE` refused (required) and `unset AGENT.model` removes the
   optional registry field.
7. Structured preview: changed keys only; a `cmd` key value appears as "(command template, not shown)"; the
   project inheritance sentence; notes (no `status.yml`) and warnings preserved; preview writes nothing (mtimes
   and bytes unchanged).
8. Apply: revision round-trip writes; stale → `StaleConfiguration`; busy → `StaleConfiguration`; partial →
   `StaleConfiguration` with written/uncertain lists.
9. Parity (E4): three entries; `tests/core/test_cli_parity.py` green unchanged.

t3r checklist: every 5.4 rule enforced in the service, not the template; diagnostics content-free; request-bound
`Project` only (no `config.repo_root()`, no `curated_models` on these paths); view secret-safe; revision binds scope,
slug and canonical changes; INFLIGHT guard exit 0.

### t4 — web Agents page and API (Haiku) → t4r (Sonnet)

Files: section 3 rows t4.

RED → GREEN:
1. `GET /agents` (no scope), `?slug=r1`, `?scope=workspace&slug=r1`, `?scope=news` render the right scope with the
   bound project root shown; `?scope=news&slug=r1` and `?scope=workspace` without slug show an error, no form
   writes; existing `test_agents_page.py` green (E1 only).
2. Forms: each agent form has the effort `<select>` with exactly that agent's `effort.levels` (a test registry with
   two Codex profiles listing different names shows each its own list — the D4 acceptance) and a custom box only
   for `placeholder` agents; the Claude form offers exactly default / extended thinking; a `none` agent has no
   effort control; timeout `type=number`; news form with seconds. Every POST form has `name="csrf_token"`; the token
   never appears in a GET form or link.
3. `POST /agents/preview` with model/effort/timeout edits → structured preview, hidden canonical `change` fields and
   `revision`; nothing written. `POST /agents/apply` → 303 to the fresh scoped read with `ok`; stored values read
   back changed; stale (file edited between preview and apply) → page re-rendered with the stale message and
   nothing written.
4. Forged posts (no matching form control): `cmd`, `enabled`, `promote`, a news `timeout_min`, a role in news,
   scope missing → refused with the right kind, nothing written, both page and API.
5. API: `GET /api/v1/agents` without scope equals `service.agent_settings` output (unchanged, still has `cmd`);
   with `scope=` equals `agent_config_view` and has no template text; `POST /api/v1/agents/preview` → 200; invalid
   → 400 `{"error": …}`; `apply` stale → 409.
6. Escaping: an agent named `<script>x</script>`, a model `"><img src=x onerror=1>`, a run slug with `<b>` and an
   error message with markup render as text (no unescaped `<script`/`<img` in the page; no `|safe` in any
   template, grep test).
7. Inventories (E3, four paths): `test_read_only.py`, `test_mutations.py` (session and CSRF guards for each new path)
   and `test_async_routes.py` green unchanged.

t4r checklist: handlers plain `def`; every new POST under `MUTATE` and in both inventories; no business rule in
`agent_views.py` beyond mapping (the service refuses forged input); no CSRF in queries; free text only in POST
bodies; escaping; INFLIGHT guard exit 0.

### t5 — safe control reads and the workspace index (Haiku) → t5r (Sonnet)

Files: section 3 rows t5.

RED → GREEN:
1. `control_read.read_yaml` on a temp dir: ok; missing; symlink (to a file inside and outside); FIFO (returns
   promptly, `not-regular`, no block); directory; 256 KiB exactly → ok, 256 KiB + 1 → `oversized` and the parser is
   never called (spy on `yaml.safe_load`); invalid UTF-8 / YAML → `malformed`; a list → `invalid-shape`; mode 000
   → `unreadable` (skipped when running as root); a file replaced between the two `fstat` calls (monkeypatched) →
   `changed`. Reasons contain no content (a secret-looking string in the file never appears in the result).
2. `open_dir` refuses a symlinked component; `scan_dir` stops at its limit without listing the rest (a counting
   fake `scandir`).
3. Index on a fixture workspace: loop and research runs (kind, state, last activity, lineage as `workspace.list_runs`
   gives for the same tree), an alias (symlink to a sibling) grouped under its run, an external alias shown and not
   followed (a spy proves no open under its target), a `_misc/` moved entry, `news/`, `chats/`, `_x`, `.x` skipped, a
   run with a malformed `status.yml` and one with an oversized `config.yml` shown with their findings and `unknown`
   fields (never "no status.yml" or a default state).
4. Bound: 10,005 entries (cheap empty dirs or a fake `scan_dir`) → `complete: false`, `inspected == 10_000`, an
   omission note; no `iterdir`, `glob`, `listdir` or `sorted` over the whole directory (spy).
5. No write: the workspace tree (bytes, names and mtimes) is identical after `GET /workspace`; no `INDEX.md`.
6. `workspace.describe` and `list_runs`/`index` CLI output on the existing fixtures identical to base
   (`tests/core/test_workspace.py` green unchanged).
7. Page and API: `GET /workspace` lists runs with links to `/runs/<slug>` and `/runs/<slug>/health` (escaped slugs),
   `GET /api/v1/workspace` equals `service.workspace_index`; nav link present; parity (E4) entry.

t5r checklist: every control read descriptor-anchored and bounded before parse; distinct findings; no alias
followed; streamed, bounded enumeration; CLI unchanged; plain `def`; INFLIGHT guard exit 0.

### t6 — run health: cheap checks and the bounded scan (Haiku) → t6r (Sonnet)

Files: section 3 rows t6.

RED → GREEN:
1. `health` on fixtures (loop, research, `none`): `missing`, `legacy_config_keys`, `per_file_dvc_pointer` equal
   `workspace.doctor`'s values; `junk_dirs`, `absolute_self_links`, `scripts_in_logs` are `not-checked`, never 0 or
   empty; a symlinked or FIFO control is reported as such; an alias slug → alias info only (spy: no open below it);
   an external alias → "not followed"; missing run → 404.
2. `scan` complete on the same fixtures: every category equal to `doctor` (junk sizes included), status `complete`,
   `stop_reason: none`.
3. Budget: 10,001 entries spread across ordinary traversal, a nested junk dir and `logs/` → stops at 10,000 with
   `stop_reason: entries`; entries in all three walks are counted (three fixtures, each exhausting the budget in a
   different walk); a fake monotonic clock passing 2.0 s → `stop_reason: time` before the next operation; partial
   junk sizes are `at_least` lower bounds; checks not reached are `not-checked`.
4. Depth four respected; a symlinked directory inside the run is never descended (spy) and an absolute self-link
   counts as in `doctor`; a FIFO in the run never blocks.
5. No side effects: tree bytes/mtimes identical after scan; no subprocess (monkeypatched `subprocess.Popen` raises);
   `archive` module obtained with the project root (cwd set elsewhere in the test).
6. CLI unchanged: `scieflow workspace doctor <slug>` text and `--json` output identical to base on the existing
   fixtures (`tests/core/test_workspace.py` green unchanged; a byte comparison against base output recorded).
7. Page and API: `GET /runs/<slug>/health` shows the cheap results and a *Check now* POST form with `csrf_token`;
   `POST /runs/<slug>/health/check` renders the scan with complete/partial/not-checked, lower bounds, entry count,
   stop reason and timestamp; API GET/POST return the same JSON; E3 two paths; guard tests green; parity (E4) two
   entries.

t6r checklist: one aggregate budget across all walks; cooperative time check between operations; honest
partial/not-checked states; doctor categories and CLI defaults unchanged; no follow, no block, no write, no
external call; plain `def`; INFLIGHT guard exit 0.

### t7 — docs (Haiku) → t7r (Sonnet)

Files: section 3 rows t7. Write T1-T8 of 5.9 verbatim at the named places and the other 5.9 updates; keep every
literal pinned by an existing docs test; `tests/test_docs_t12.py` pins T1-T8 (reading only this repo's `docs/`, as
`tests/test_docs_k_harness.py` does), checks that the "What stays CLI-only" section no longer says per-agent field
edits are terminal-only (ticket 12 DoD) while still listing `--promote`, and that no page claims browser
promotion, `enabled` editing or Host/Origin checks.

RED → GREEN: the new docs test fails before the docs edit and passes after; every `tests/test_docs*.py` passes;
`mkdocs build --strict` exit 0 (or "not available").

t7r checklist: every sentence true against the code on the tip (T1 `agent_config.apply_effort` and F-2's turn env;
T2 `effort_levels_for`; T3 the 5.4 matrix; T4 `agent_configure.apply`; T5/T6 `workspace_health`; T7 `api.py`; T8
`menu._pick_effort`); F-2's pinned sentences intact; INFLIGHT guard exit 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (directory and
explicit file; absolute counts and the delta against t0 explained by added tests and recorded E-extra entries);
the new T-12 test files once more under `env -i HOME=<empty dir under ~/.cache/scieflow-t-12-branch>
PATH=/usr/bin:/bin` (hermeticity); `tests/test_ci_workflow.py` if it exists on base; `mkdocs build --strict` into
`RUN/scratch/t-12/site` (or "not available"); the INFLIGHT guard `--require-applied` over the tip; linear history
(no merge commits), at least one commit per ticket t0..t7; `git diff base..tip -- tests/` removes lines only inside
E1, E2 and recorded E-extra entries (E3/E4 are additions; each mapped in the branch report); no never-edited file of
section 3 in the diff; `git diff base..tip -- pyproject.toml uv.lock` empty; `grep -rn '|safe'
src/scieflow/web/templates` shows no new occurrence; `scieflow workspace doctor --json` for a fixture run equals the
base output. Writes `RUN/scratch/t-12/pr-body.md` (plain English: the six changes of section 1, what stays CLI-only,
decisions T12-D1..D14, items H-T12-1..5, absolute counts, residuals R1-R8, "please merge with a merge commit"; no
credentials, prompts or patient data) and `reviews/t-12-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; FG's acceptance list FG:307-335 point by point; ticket 12's
definition of done; the D4 acceptance — per-agent effort lists from the registry, Claude effort effective on
one-shot and turn paths, no template written by browser or TUI; decisions T12-D1..D14), code and security (5.10),
evidence (re-run memory-capped the t1-t7 focused sets and the sealed file; confirm the branch result's counts; re-run
the INFLIGHT guard; `pr-body.md` accurate). Output `reviews/t-12-final-review-r<N>.md`. PASS → the installer's
publish step; CHANGES → final_fix (new commits, full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/t-12-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   effort as a real setting (A-D4)        DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        configurator owner                     same shape → t3
t3/t3r/t3f        service layer                          same shape → t4
t4/t4r/t4f        web Agents page and API                same shape → t5
t5/t5r/t5f        control reads, workspace index         same shape → t6
t6/t6r/t6f        run health, bounded scan               same shape → t7
t7/t7r/t7f        docs                                   same shape → branch
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install; no protected edits)
```

Caps `{max_cards: 80, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/t-12-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff, decisions, gap,
`fgdesign` (`approvals/fg-agent-config-workspace-health-design.md`), `fgapproval`
(`approvals/k-fg-human-review-20261009.md`), `g4decisions` (`approvals/k-task8-g4-decisions-20261014.json`),
`amendment` (`approvals/k-task8-g4-plan-amendment-20261014.md`), `f2plan` (`reviews/f-2-plan.md`), `guard`
(`reviews/t-12-inflight-guard.py`); the installer adds `plan`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Cooperating writers only.** The lock protocol serialises ScieFlow's configurator, `store` writers that use
  the same sibling locks, and itself; a hand edit or an agent writing a config file directly can still race after
  the comparison (FG:204-205). The review-settings editor (`core/review`) stays a separate writer of a run's
  `config.yml`; t0 records whether it takes the sibling lock (if not, its writes are still detected as stale by
  digest, but not serialised).
- **R2 Not crash-atomic.** A CLI/TUI change that writes both `agents.yml` and `defaults.yml` can stop half-way; the
  result says which file was written (FG:209-211).
- **R3 Effort semantics change.** Runs that stored `reasoning: default|extended-thinking` on a Claude agent change
  behaviour (T12-D2); `none`-mode agents lose the meaningless Claude pair in `menu --json`.
- **R4 Menus are suggestions.** Effort and model lists are what you put in `config/agents.yml`; nothing checks your
  subscription live (FG:54,58).
- **R5 Config reads are not descriptor-anchored.** The browser config path checks file type, containment and size
  before planning and the revision detects a swap before writing; the planner's own reads still use paths
  (control reads for index/health are descriptor-anchored).
- **R6 Index cost.** Up to 10,000 entries with two 256 KiB control reads each; filesystem calls can still block
  (FG:257,269).
- **R7 Scan budget is cooperative.** One blocking call can exceed two seconds; the defaults are design defaults,
  not measurements (FG:269-272).
- **R8 HTTP hardening unchanged.** DNS-rebinding residual as documented (FG:141-144); `GET /api/v1/agents` without
  `scope` still returns command templates (H-T12-5).

## 9. After T-12

Queue (`continuity/milestones.yml:31-32`): Ticket 8 (archive browsing design, may run in parallel) and Ticket 9
(`after: [t-8, t-12]`). Follow-ups surfaced while planning: H-T12-1..5; descriptor-anchored reads for the
configurator itself (R5); the review-settings editor joining the configurator lock protocol (R1).
