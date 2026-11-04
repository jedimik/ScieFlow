# F-2 plan: identity capture and effort delivery (+ F-1 menu fix) (round 1)

Pipeline `f-2-plan`, step plan, round 1, epoch 1. Card `t_b3d094cc`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261102_171531_f14a82`, source kanban).
Written 2026-11-02. Previous review: none (round 1).

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

Scope sources (read in full): `reviews/k-g5-plan.md` section 6.1 (F-1, F-2; also 2.1 G5P-1..G5P-4, 3 R5-1,
4.3, 7 H-1..H-6), `reviews/k-g6-acceptance-report.md` section 1.2 (and B4, B6, B7 it summarises), the T4
recipe `reviews/k-g5-identity-argument.md` (sections 1, 2 and finding T4-F1) with its reference
implementation `scratch/k-g5/t4_identity_check.py` (sha256 fcf8a3c5…e233) and machine record
`scratch/k-g5/t4-identity.json` (b4fef519…6a). The queue entry (`continuity/milestones.yml:29`):
`after: [m-g, m-h, m-i, m-d3]`, size L, "No live probe attempt in this milestone".

Prerequisite plans read (approved, not merged yet): `reviews/m-h-plan.md`, `reviews/m-i-plan.md`,
`reviews/m-d3-plan.md` (their section 3 file lists and never-edited lists).

Code read read-only from git objects (`git show <ref>:<path>`); no checkout was touched.

- `M` = remote `main` = `a42fc2ebd9d3d70a95e6cc212bb10b5b85d47764` (`git ls-remote` on this card; objects
  present locally). It contains M-G (PR #25, `2bcac1d`) and PL (PR #26, `a42fc2e`, the paper-lifecycle
  landing). M-H (run at t0), M-D3 (run at t4 review) and M-I (installed, held) are **not** merged yet.
- Every `path:line` below is on `M`. M-H, M-I and M-D3 do not edit any F-2 production file except
  `src/scieflow/core/menu.py` (M-H t9 adds one `WORKFLOWS` entry at `menu.py:49-81`; F-2 edits
  `menu.py:838-882`), so the cites are expected to hold on the real base; t0 re-verifies every one.

## 1. For the human: what F-2 changes, in plain words

Today the one certified harness command (`/scieflow-menu`) can never be used in a real run, for two code
reasons found in G5 and G6, and the menu skill itself does not work without a shell (R5-1). F-2 fixes
those three things. **It switches nothing on.**

1. **ScieFlow can now work out the full identity of the agent it is about to start (F-2 identity capture).**
   Before a marked turn, ScieFlow fingerprints, without running anything: the claude program file
   (its sha256), its version (read from the install folder name, accepted only for the one program hash
   G5 paired with that version), the project's `AGENTS.md`/`CLAUDE.md`/`.mcp.json`, the agent's own
   settings files, the project's claude skills, `.claude/settings.json`, the hook script and the sandbox
   policy. This is exactly the recipe G5 wrote down (T4). If any input is missing, oversized, a symlink
   where a file is expected, or changes while it is being read, the identity stays incomplete and nothing
   is allowed (fail-closed). No program is executed, nothing is written, no credential is read.
2. **Claude's "extended thinking" now reaches conversation turns (effort delivery, T4-F1).** Today a
   conversation turn cannot get extended thinking at all: the setting is an `env MAX_THINKING_TOKENS=32000`
   prefix on the command, and the conversation path refuses any `env` prefix for safety. After F-2, when
   the conversation agent is a Claude agent set to extended thinking (`reasoning: extended-thinking`, or
   the prefix the menu writes on its `cmd`), ScieFlow puts `MAX_THINKING_TOKENS=32000` into the turn's
   own environment itself. This affects ordinary chat turns too, which is the point: your effort choice
   now means the same thing in the browser conversation as in a one-shot dispatch.
3. **The menu skill works without a shell (F-1, option R5-1b).** The skill's first step is
   `uv run scieflow menu --json`; an agent restricted to read-only tools cannot run it and today
   reconstructs the menu from source code, which can drift. After F-1 the code-owned part of the menu
   (sections, workflows, setting scopes, roles, the apply commands) is kept in a checked-in file
   `skills/scieflow-menu/menu.json`; a test fails whenever it differs from the code. The skill says: with
   a shell run the command as before; without one, read that file, and read the current settings and runs
   from the run's own files.
4. **Two small display fixes the G6 review tied to F-2.** The HTTP API now says in its response when it
   delivered a marked turn as ordinary chat (the browser already shows a notice), and the run page and job
   page show whether a turn was composed command-first or charter-first.
5. **Docs** (`docs/web.md`, `docs/agents.md`, `docs/sandbox.md`, `docs/menu.md`) say all of this, and the
   five missing sentences the G6 reviews listed are added.

**What stays off (on purpose).** After F-2 the certified entry still matches no deployment: its working
directory is the probe tree, and F-1 changes the menu skill's bytes, which are part of the identity. The
web UI and the API keep sending marked turns as ordinary chat. Using the command for real needs, in this
order, your decisions **H-F2-1** (let the web/API run a marked turn when support is enabled), **H-F2-2**
(a new live attempt for a new identity, e.g. this checkout) and a new G5-style review and entry. None of
them is in this run.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **F2-D1 F-1 is R5-1b (snapshot file), not R5-1a (menu inserted into the prompt).** R5-1a would put the
  live menu (including every run's name and state) into the prompt of a cloud agent from outside its
  sandbox, which is a new data flow that M-B's hiding of L2 runs does not cover, and would change prompt
  composition, which the identity does not pin. R5-1b changes only the skill folder, whose bytes the
  identity pins automatically, and adds no data flow. Cost: the snapshot holds only the code-owned part;
  current settings and runs are read from files. (Alternative: R5-1a; say so.)
- **F2-D2 Effort rule for conversation turns.** On for a `family: claude` conversation agent whose
  templates have no `{reasoning}` placeholder when the effective `reasoning` is `extended-thinking`, or
  when it has no `reasoning` and its `cmd` starts with `env MAX_THINKING_TOKENS=32000 ` (what the menu and
  role overrides write, `agent_config.py:87-88,301-305`, `menu.py:590-608`); `reasoning: default` (or any
  other value) turns it off. The value is the code constant `32000`. This is not a sandbox exposure change:
  the sandbox already allows exactly this variable through an `env` prefix (`sandbox.py:1606-1615`), and
  the value is a code constant, not something copied from the host.
- **F2-D3 Capture only for marked turns.** Hashing a ~250 MB program file costs about a second; ordinary
  chat turns do not need the identity, so they keep today's identity record (incomplete, harmless) and pay
  nothing. Effort delivery (D2) applies to every turn.
- **F2-D4 No user name in product code.** The recipe's versions folder `/home/ubuntu/.local/share/claude/
  versions` is computed from the home directory of the process (realpath of `~`), which gives exactly that
  folder on this host. The one evidenced pairing `8968405e… → 2.1.293` is a code constant. (The existing
  entry in `harness_commands.py` still carries host paths, T8B m-5; F-2 does not touch that file.)
- **F2-D5 Stricter than the recipe where the recipe is silent.** Extra fail-closed rules: an oversized or
  unreadable file, a subfolder or a symlinked file inside a linked skill folder, an existing
  `.claude/settings.local.json` or `CLAUDE.local.md` at the working directory, a credential bound inside
  the agent's `.claude/` folder, or a program file that changed between capture and hashing each leave the
  identity incomplete. These can only make an identity incomplete, never make a non-matching one match;
  for the recorded r14 inputs the result is byte-identical to the T4 identity.
- **F2-D6 Web and API keep the downgrade.** Only the response and the display change (item 4). Opening the
  path is H-F2-1.
- **F2-D7 The sealed r14 probe suite stays runnable** by the same means G5-T6 used for the policy entry:
  the existing test-only fixture in `tests/probes/conftest.py` puts production back into its pre-F-2 state
  (no capture) for `test_k_task8_binding.py` only. The sealed files are not edited.
- **F2-D8 `harness_commands.py` is not edited** (zero lines; `CERTIFIED_POLICIES`, matching and the entry
  unchanged), as G5 plan 6.1 requires for F-2.
- **F2-D9 `config/agents.yml` is not edited.** Its claude `menu.reasoning.how` text (`config/agents.yml:39-43`)
  still describes the `cmd` override; the docs say what turns do. Ticket 12 (agent configuration, effort
  dropdown D4) owns that registry text and the `agent configure` warning wording.

**Items for you (not in this run):**
- **H-F2-1** Whether the web UI/API may run a marked turn as a command when `support_for` is enabled for
  the turn's captured identity (today: never). Deployment scope (G5 H-3).
- **H-F2-2** A new live attempt (beyond the agreed cap, G5 H-2) for an identity that exists after F-1/F-2,
  for example this checkout with a pinned binary, followed by a new binding generation, a new G5-style
  review and a new entry. It is planned as a separate, explicitly human-gated item (section 9).
- **H-F2-3** Whether the host paths of the existing entry (T8B m-5) should move out of product source when
  a new entry is written.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head `dev/f-2`
→ base `main`, one PR, merge with a merge commit. Title: "F-2: harness identity capture, Claude effort for
conversation turns, menu skill without a shell (F-1)". The body (written by the branch step) lists the
changes above, the decisions, absolute test counts and the residuals of section 8.

## 2. Base, dependencies and worktree

F-2 starts only after M-H, M-I and M-D3 are merged (queue `after: [m-g, m-h, m-i, m-d3]`; M-G is merged).
t0 checks it:

1. `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`; object-only fetch
   `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`
   (no ref, no FETCH_HEAD).
2. `git merge-base --is-ancestor <sha> <base>` for `6152d733728b9ed5bf5b571aef475f8071426386` (K/H-4) and
   `a42fc2ebd9d3d70a95e6cc212bb10b5b85d47764` (`M`, contains M-G and PL).
3. `RUN/scratch/m-h/publish.json`, `RUN/scratch/m-i/publish.json`, `RUN/scratch/m-d3/publish.json` exist with
   `"merged": true`, and each `pushed_head` is an ancestor of `base`.
4. Otherwise `BASE_RED` with the reason (the supervisor escalates; no polling).

Worktree: `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/f-2 RUN/scratch/f-2-wt <base>`
(on a retry reuse it at `<base>` or `<base>` plus t0's docs commit; never reset). RUN =
`/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
`RUN/scratch/f-2/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
`~/.cache/scieflow-f-2-<step>/`. No ticket touches `/home/ubuntu/Github/ScieFlow` or the checked-out tree of
`/home/ubuntu/ScieFlow-claude-f2976f52`. Commits are local on `dev/f-2`; review fixes are new commits.

## 3. In-flight, never-edited, protected and changed files

**INFLIGHT** (the same 9 paths as every milestone since M-A; PL has landed them, the guard stays as the
installer's standing rule requires). F-2 edits **none** of them:

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

Guard (must exit 0): `git -C WT diff --exit-code <base> -- <INFLIGHT>` and, on a dirty tree,
`git -C WT diff --exit-code -- <INFLIGHT>`.

**Never edited:** `src/scieflow/core/harness_commands.py` (F2-D8), `config/sandbox.yml`, `config/defaults.yml`,
`src/scieflow/core/sandbox.py`, `src/scieflow/core/agent_config.py`, `src/scieflow/core/agent_configure.py`,
`src/scieflow/core/turn_input.py`, `src/scieflow/core/turn_admission.py`, `src/scieflow/core/run/conversation.py`,
`src/scieflow/core/events.py`, `src/scieflow/core/jobs.py`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`,
`tests/fixtures/k_task8_stream_fixture.py`), `tests/probes/test_k_g5_binding_guard.py`, `.claude/settings.json`,
the `.claude/skills/*` links, `scripts/hooks/*`, every `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, `uv.lock`,
`.github/workflows/*`.

**Protected edits:** none. F-2 needs no change to any agent-instruction file (`skills/scieflow-menu/SKILL.md`
is an ordinary skill file; earlier milestones edited skills directly). No `f-2-protected-edits.json`.

**Existing tests that change (and only these):**
- **E1** `tests/test_docs_k_harness.py:44` (D-9) and `:46` (the R5-1 residual): each literal is replaced by
  the new sentence S1 / S2 of section 5.9, because the behaviour they describe changes. Every other literal
  and all four test functions stay; `R53_LIMITATION` and its sha256 are unchanged.
- **E2** `tests/probes/conftest.py:24-27`: the existing autouse fixture `_r14_pre_entry_policy` (applies only
  to `test_k_task8_binding.py`) additionally sets `harness_identity.capture` to
  `harness_identity.uncaptured`, and its docstring says why (F2-D7). Its existing line is unchanged.
- **E-extra** (only if the full suite proves it necessary): a test that pins a literal docs sentence this
  milestone rewrites (`docs/agents.md:116-128`, `docs/menu.md:67-69`, `docs/sandbox.md:204-207`) gets the new
  literal; each is listed in `reviews/f-2-test-changes.md` with node id, reason and diff. Weakening,
  skipping or deselecting a test is never allowed.

**Complete file list** (nothing else changes):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-11-02-f-2-identity-capture.md`, `docs/superpowers/specs/2026-11-02-f-2-identity-capture-design.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/core/harness_identity.py` | new: recipe, bounded capture, effort rule | t1 |
| `tests/core/test_harness_identity.py`, `tests/fixtures/harness_identity_r14.json` | new | t1 |
| `src/scieflow/core/agent_run.py` | `resolve_turn`: effort env, `capture_identity` keyword | t2 |
| `src/scieflow/core/service.py` | `_say_prepared` passes `capture_identity` (one call) | t2 |
| `tests/probes/conftest.py` | E2 | t2 |
| `tests/core/test_f2_turn_identity.py`, `tests/probes/test_f2_capture_guard.py` | new | t2 |
| `src/scieflow/core/menu.py` | `static_menu_json`, `write_static_snapshot`, `menu_json` built from it (output byte-identical) | t3 |
| `skills/scieflow-menu/SKILL.md`, `skills/scieflow-menu/menu.json` (new) | no-shell path; effort sentence | t3 |
| `tests/core/test_menu_snapshot.py` | new | t3 |
| `src/scieflow/web/api.py`, `src/scieflow/web/pages.py`, `src/scieflow/web/templates/run.html`, `src/scieflow/web/templates/job.html` | API downgrade marker; composition order shown | t4 |
| `tests/web/test_f2_web.py` | new | t4 |
| `docs/web.md`, `docs/agents.md`, `docs/sandbox.md`, `docs/menu.md` | docs | t5 |
| `tests/test_docs_k_harness.py` | E1 | t5 |
| `tests/test_docs_f2.py` | new | t5 |

Production lines (estimate): `harness_identity.py` ≤ 200 (G5 6.1 bound for the resolver), `agent_run.py`
≤ 25, `service.py` 1, `menu.py` ≤ 35, web ≤ 30. Hotspots: `agent_run.py` and `service.py` are shared K16
files; F-2's change there is additive (one keyword, one environment entry, one call argument).

## 4. Current code, verified (on M = a42fc2e)

**Identity policy.** `HarnessIdentity` has 15 scalar fields (`src/scieflow/core/harness_commands.py:10-39`).
`CERTIFIED_POLICIES` holds exactly one entry, the T4 identity with host paths
(`harness_commands.py:69-90`: executable `/home/ubuntu/.local/share/claude/versions/2.1.293`, reasoning
`env:MAX_THINKING_TOKENS=32000`, cwd `/home/ubuntu/ScieFlow-probe/k-task8-k-r11`, the three digests
`f027fcca…`, `352742d6…`, `255c46d5…`). `_complete` requires family claude, enabled, `effects_pinned`, every
`*_sha256` 64 lowercase hex and every other text field non-empty (`:111-125`); `support_for` returns enabled
only for an exact match (`:128-143`).

**Production identity (G5P-1).** `resolve_turn` (`src/scieflow/core/agent_run.py:565-685`) builds
`HarnessIdentity(agent, family, True, executable, None, None, templates[0], templates[1], model, reasoning,
str(work), None, None, None, False)` (`:677-679`): always incomplete. `prepare_turn` refuses a marked turn
unless confined and supported (`:696-704`). The effective config is the registry entry merged with the run's
`agent_overrides` (`:580-590`); an `env` first token in `session_cmd`/`resume_cmd` is refused (`:601-603`);
`reasoning` is read as text (`:604-606`). The sandboxed environment comes from `sandbox.dispatch_environment`
(`:662-666`), which keeps only the C12 names (`sandbox.py:62-63,1249-1273`), so a host
`MAX_THINKING_TOKENS` never reaches a sandboxed turn today. The executable realpath and its path facts
(device, inode, size, mtime, ctime) come from `_capture_execution` (`agent_run.py:537-562`, facts type
`sandbox.py:1751-1762`). The turn's protection carries `policy_sha256` (`sandbox.py:1792`,
`resolve_turn_protection` `:1906-1943`); the agent home is `<run>/scratch/agent-home/<agent>`
(`sandbox.py:851-873`).

**Service.** `_say_prepared` calls `resolve_turn(project, doc['agent'], run_dir=ws, session=doc['session'],
create_home=True)` then `prepare_turn` (`src/scieflow/core/service.py:682-684`); `_validate_execution` checks the
identity's executable against the facts (`:500-516`); the launch passes `env=dict(prepared.env_items)`
(`:629-637`); the turn audit stores `composition_order` (`:712-718`). A marked turn without supported admission
is refused (`:373-374`); an unconfined run refuses ordinary sends and hand-overs too
(`turn_admission.py:367-368`, proven by `tests/core/test_service.py:1701-1720`).

**Effort today.** `agent_config.EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "` (`agent_config.py:87-88`);
role overrides rewrite only `cmd`/`stdin_cmd` (`:289-306`); `--set claude.reasoning=…` stores the text and
`validate` warns that it is "only a label" (`:417-432`). The TUI's Claude effort writes the prefix into
`cmd`/`stdin_cmd` (`menu.py:590-608`). The registry's claude entry has no `reasoning` key and templates without
`{reasoning}` (`config/agents.yml:19-43`). The skill says extended thinking means the `cmd` prefix
(`skills/scieflow-menu/SKILL.md:56-58`). Result: no conversation turn can get extended thinking (T4-F1).

**Menu (R5-1).** The skill's step 1 is `uv run scieflow menu --json` (`SKILL.md:13-23`). `menu_json()`
(`menu.py:838-882`) returns `sections` (`TREE`, `:83`), `workflows` (`WORKFLOWS`, `:49`), `agent_settings`
(`scopes` `SCOPES` `:131`, `roles` from `agent_config.ROLES`, `agents` and `effective_defaults` from the user's
config, `apply_with`), `runs` (`service.list_runs`) and `resume_prompt_template`; the command prints it with
`json.dumps(…, indent=2, default=str)` (`:885-895`). `skills/scieflow-menu/` holds only `SKILL.md`; `.claude/skills`
holds the two links `scieflow-menu` and `workspace-sync` (git tree at M).

**Web.** A marked send is always delivered as chat: `_as_chat` (`src/scieflow/web/pages.py:150-163`), the page
handler with its notice (`:729-741`), the API without any marker (`src/scieflow/web/api.py:221-228`). The run page
shows only a mode badge (`templates/run.html:222-226`); the job page shows `turn mode` (`templates/job.html:12`,
`pages.py:827-840`).

**Docs.** `docs/web.md:495-531` is the harness section; `:517` says the production resolver builds an incomplete
identity (D-9) and `:521` the R5-1 residual; `tests/test_docs_k_harness.py:20-55` pins these literals.
`docs/agents.md:116-128` and `docs/menu.md:67-69` describe Claude effort as the `cmd` prefix;
`docs/sandbox.md:204-207` the `env` prefix rules.

**Sealed suite (F2-D7).** The r14 binding expects the production identity shape with no digests
(`tests/probes/k_task8_binding.py:348-352`), binds support to exactly that identity (`:388-414`), wraps
`resolve_turn` forwarding every keyword (`:1038-1044`), and drives marked turns through `service.say`
(`tests/probes/test_k_task8_binding.py:232-266`). It refuses a launch env that already carries
`MAX_THINKING_TOKENS` (`k_task8_binding.py:1070`). Its test project copies only `config/` and `schemas/`
(`test_k_task8_binding.py:108-114`). `tests/probes/conftest.py:24-27` already patches `CERTIFIED_POLICIES` to `()`
for that file only (G5-T6).

**Prerequisites (from their approved plans).** M-H edits `menu.py` (`WORKFLOWS` entry), `agent_config.py`
(role `research.sr-screeners`), `events.py` (`sr.*` types), root `AGENTS.md`; never `agent_run.py`,
`service.py`, `sandbox.py`, `web/*` (m-h-plan section 3). M-I adds `.github/workflows/tests.yml` and
`scripts/ci-tests.sh` (`-m 'not slow and not live'`, sealed suite excluded), validators in `events.emit` and
`jobs.save`, `tests/core/cli_parity.yml` entries, the read-only MCP server; never `service.py`, `sandbox.py`,
`web/*` (m-i-plan section 3). M-D3 edits `jobs.py` and `service.cancel_job` only (m-d3-plan section 3). F-2 adds
no event type, no job or audit field and no public service function, so none of their pins or parity tables
change.

## Part A — Spec

### 5.1 Overview and data flow

```
service.say (marked)                               service.say (ordinary)
  └─ _say_prepared: resolve_turn(..., capture_identity=True)      capture_identity=False
        effective cfg ─► harness_identity.turn_effort(cfg) ─► env[MAX_THINKING_TOKENS]="32000" (both)
        env, execution facts, protection, layout.home ─► harness_identity.capture(...)  (marked only)
              bounded reads, no exec, no write ─► HarnessIdentity (complete or incomplete)
  └─ prepare_turn (unchanged): support_for(identity) ─► enabled only on an exact CERTIFIED_POLICIES match
```

### 5.2 The identity recipe as implemented (`src/scieflow/core/harness_identity.py`)

The module reproduces `reviews/k-g5-identity-argument.md` section 2 field for field; its reference
implementation is `scratch/k-g5/t4_identity_check.py:54-102`. Interfaces:

- Constants: `SCHEMA_DISCOVERY = 'scieflow.harness-identity.discovery/1'`, `SCHEMA_EXTENSIONS =
  'scieflow.harness-identity.extensions/1'`, `SCHEMA_SETTINGS = 'scieflow.harness-identity.settings/1'`,
  `EXTENSIONS_DIR = '.claude/skills'`, `SETTINGS_FILES = ('.claude/settings.json',
  'scripts/hooks/dvc-sync-reminder.py', 'config/sandbox.yml')`, `KNOWN_HOOK_COMMANDS =
  ('python3 "$CLAUDE_PROJECT_DIR/scripts/hooks/dvc-sync-reminder.py"',)`, `VERSION_PAIRINGS =
  {'8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978': '2.1.293'}`, `EFFORT_ENV =
  ('MAX_THINKING_TOKENS', '32000')`, `EXTENDED_THINKING_PREFIX` imported from `agent_config.EXTENDED_THINKING`,
  bounds `MAX_EXECUTABLE_BYTES = 1 << 30`, `MAX_TEXT_BYTES = 1 << 20`, `MAX_DIR_ENTRIES = 64`.
- `canonical_digest(record: dict) -> str` = `sha256(json.dumps(record, sort_keys=True, separators=(',', ':'),
  ensure_ascii=True).encode('ascii'))`.
- `claude_versions_dir() -> Path` = `Path(os.path.realpath(os.path.expanduser('~'))) / '.local' / 'share' /
  'claude' / 'versions'` (F2-D4).
- `derive_version(executable: str, executable_sha256: str | None, *, versions_dir: Path, pairings=VERSION_PAIRINGS)
  -> str | None`: the basename only if `executable` is absolute, `Path(executable).parent == versions_dir`, the
  basename matches `\d+\.\d+\.\d+`, and `pairings.get(executable_sha256) == basename`; else `None`. Pure (the
  caller passes a realpath, so it is never a symlink).
- `reasoning_label(env_items, templates, configured: str) -> str`: `'env:MAX_THINKING_TOKENS=32000'` iff
  `dict(env_items).get('MAX_THINKING_TOKENS') == '32000'` and no template token equals
  `'MAX_THINKING_TOKENS=32000'`; otherwise `configured` (so `''` for an unset reasoning → incomplete).
- `derive(*, agent, family, executable, executable_sha256, version, session_template, resume_template, model,
  reasoning, cwd: str, discovery: dict | None, extensions: dict | None, settings: dict | None,
  sandbox_enabled: bool, policy_sha256: str | None) -> HarnessIdentity` (pure): the three digests are
  `canonical_digest({'schema': …, **record})` of the given records or `None`; `effects_pinned` is `True` only
  when all three records are present, `sandbox_enabled`, and `policy_sha256 == settings['config/sandbox.yml']`.
  `enabled=True` as today.
- IO readers (each returns `None` on any rule violation): `executable_digest(executable, facts)`,
  `read_discovery(cwd, agent_home, *, forbidden=())`, `read_extensions(cwd)`, `read_settings(cwd)`.
- `capture(*, agent, family, executable, session_template, resume_template, model, configured_reasoning, cwd,
  env_items, execution_facts, protection, agent_home, credential_targets=(), versions_dir=None) ->
  HarnessIdentity`: calls the readers and `derive`. `uncaptured(...)` (same keywords) returns today's
  production shape (`agent_run.py:677-679`) and is what ordinary turns get.

Field rules (numbers as in the T4 table):

| # | Field | Rule |
|---|---|---|
| 1-3 | agent, family, enabled | unchanged (`resolve_turn` values, `True`) |
| 4 | executable | the realpath from `_capture_execution` (unchanged) |
| 5 | executable_sha256 | `executable_digest`: open the realpath `O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, `fstat` regular and ≤ `MAX_EXECUTABLE_BYTES`; its `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` must equal the execution fact whose `path == Path(executable)`, before and after a streamed hash; else `None` |
| 6 | version | `derive_version(executable, executable_sha256, versions_dir=claude_versions_dir())` |
| 7-9 | session/resume template, model | unchanged raw strings |
| 10 | reasoning | `reasoning_label(env_items, templates, configured_reasoning)` |
| 11 | cwd | `str(work)` (unchanged) |
| 12 | discovery_sha256 | `read_discovery`: keys `AGENTS.md`, `CLAUDE.md`, `.mcp.json` = sha256 of a regular non-symlink file at cwd, else `None`; `home-claude-settings` = `{name: sha256}` of regular non-symlink files matching `^settings[^/]*\.json$` in `<agent_home>/.claude/` (`{}` when the folder is absent). Requires a sandboxed turn with an agent home; otherwise the whole record is `None` (the host's real `~/.claude` is never read) |
| 13 | extensions_sha256 | `read_extensions`: `<cwd>/.claude/skills` a real directory with ≤ 64 entries; every entry a symlink whose target (relative to the link's folder) resolves to a directory inside `realpath(cwd)`; record `{'dir': '.claude/skills', 'entries': {name: {'symlink': <link text>, 'files': {file: sha256}}}}` over the regular files of that directory |
| 14 | settings_sha256 | `read_settings`: `.claude/settings.json` parses as a JSON object; its hook commands (`[h['command'] for grp in hooks.values() for e in grp for h in e['hooks']]`) equal `KNOWN_HOOK_COMMANDS` exactly; record `{'files': {rel: sha256}}` over `SETTINGS_FILES`, each a regular non-symlink file |
| 15 | effects_pinned | as `derive` |

All reads are bounded (`MAX_TEXT_BYTES` per text file, `MAX_DIR_ENTRIES` per listing), use `O_NOFOLLOW` on the
leaf, never follow a link out of the working directory, and catch `OSError`/`ValueError`/`UnicodeError` into
`None`. No `subprocess`, `os.exec*`, `os.spawn*`, `os.system` or `os.fork` is reachable from the module.

### 5.3 Fail-closed rules beyond the recipe (F2-D5)

Each makes the named record `None` (so the identity is incomplete): a regular file above its size bound; an
unreadable file; a subdirectory, symlinked file or non-regular file inside a linked skill folder; a listing above
`MAX_DIR_ENTRIES`; `.claude/settings.local.json` or `CLAUDE.local.md` present at cwd in any form (settings record);
a credential bind target (`layout.binds` destinations, passed as `credential_targets`) at or under
`<agent_home>/.claude/` (discovery record; a credential is never hashed); a program file whose identity changed
during hashing (field 5). For the r14 inputs none applies (the T4 record has no such file), so the derived
identity equals the T4 identity byte for byte.

### 5.4 Effort delivery (F2-D2)

`harness_identity.turn_effort(cfg: dict) -> tuple[str, str] | None` returns `EFFORT_ENV` iff
`cfg.get('family') == 'claude'`, neither `session_cmd` nor `resume_cmd` contains `{reasoning}`, and either
`cfg.get('reasoning') == 'extended-thinking'`, or `cfg` has no `reasoning` key (or an empty one) and
`str(cfg.get('cmd', '')).startswith(EXTENDED_THINKING_PREFIX)`. Any other `reasoning` value returns `None`.
`resolve_turn` applies it to the environment it already builds (sandboxed and unsandboxed), after
`dispatch_environment`, so the value cannot be dropped by C12 and is the only source of the variable on a
sandboxed turn; the environment then flows unchanged through `ResolvedTurn.env_items`, `PreparedTurn.env_items`
and `jobs.start(env=…)` (`service.py:634`). The argv never carries it. The one-shot `scieflow agent run` path is
unchanged (it keeps the `cmd` prefix).

### 5.5 Wiring and what does not change

- `agent_run.resolve_turn(..., create_home=False, capture_identity=False)`: one new keyword. With `False` the
  identity is `uncaptured(...)`, equal to today's (`agent_run.py:677-679`). With `True` it is `capture(...)`
  over the values `resolve_turn` already holds (effective cfg, final `env_items`, `execution_facts`, the turn
  `protection`, `layout.home`, the layout's credential bind targets). Capture never calls `_capture_execution`,
  `resolve_turn_protection`, `charter.snapshot` or the data decision again, writes nothing, and raises nothing
  new (incomplete instead).
- `service._say_prepared` passes `capture_identity=(submission.mode == 'harness-command')`.
- Unchanged: `prepare_turn`, `harness_commands.py`, `_validate_execution`, admission, the turn audit and job
  record fields, events, the composed prompt bytes, the one-shot dispatch path, `web/*` behaviour (5.7 only
  adds output).

### 5.6 F-1: the menu without a shell (F2-D1, R5-1b)

- `menu.static_menu_json() -> dict` returns, in this key order, `sections`, `workflows` (as in `menu_json`),
  `agent_settings` = {`scopes`, `roles`, `apply_with`} (as in `menu_json`), `resume_prompt_template`, and
  `omitted` = `["agent_settings.agents", "agent_settings.effective_defaults", "runs"]`. Only code-owned values:
  no user config, no run, no path.
- `menu_json()` is rebuilt from it plus the dynamic parts, with exactly today's keys and key order; the
  `scieflow menu --json` output is byte-identical before and after (t3 records both).
- `menu.write_static_snapshot(root: Path | None = None) -> Path` writes
  `skills/scieflow-menu/menu.json` = `json.dumps(static_menu_json(), indent=2, ensure_ascii=True) + "\n"`.
  `tests/core/test_menu_snapshot.py` fails when the checked-in file differs, naming the one-line regeneration
  command `uv run python -c "from scieflow.core import menu; menu.write_static_snapshot()"`.
- `SKILL.md` section 1 becomes: with a shell, run `uv run scieflow menu --json` (unchanged); without one, Read
  `skills/scieflow-menu/menu.json` for the tree, Read `config/agents.yml` (`menu:` blocks: models, effort),
  `config/defaults.yml` and `workspace/<slug>/config.yml` for current settings, and Glob `workspace/*/status.yml`
  for runs; never reconstruct the menu from `src/`. The effort sentence (`SKILL.md:56-58`) states 5.4. The rest
  of the skill is unchanged.
- Identity consequence: the skill folder's bytes change, so `extensions_sha256` of any checkout with the new
  skill differs from the entry's `352742d6…`; a test proves the entry cannot match such an identity. Every later
  change of the code-owned menu (for example a new workflow) changes the snapshot and therefore the identity,
  which is intended: the menu the agent shows is pinned evidence.

### 5.7 Web and API (F2-D6)

- API `POST /api/v1/runs/{slug}/conversation` with `mode=harness-command`: unchanged delivery as chat; the JSON
  response gains `"harness_command": {"requested": true, "delivered_as": "chat", "notice": <the page's notice
  text>}`. A chat request's response is unchanged (no new key).
- Run page: the mode badge is followed by the composition order when the turn audit has one
  (`command-first` / `charter-first`; any other stored value shows `unknown`). Job page: a line
  `composition: …` beside `turn mode`, from `job.turn_audit`, same mapping. Escaped like every other value.

### 5.8 Sealed suite compatibility (F2-D7)

E2 sets `harness_identity.capture = harness_identity.uncaptured` for `test_k_task8_binding.py` only, so the r14
binding sees the production identity it was sealed for. Effort delivery cannot reach it: the probe override
and the copied `config/agents.yml` give claude no `reasoning` and no prefixed `cmd`, so production adds nothing
(and the binding's own check `k_task8_binding.py:1070` would refuse otherwise). F-1 cannot reach it: the test
project copies no `skills/`. `tests/probes/test_f2_capture_guard.py` proves outside that file `capture` is the
real function and the fixture applies to that file alone. The sealed suite must stay at 0 failed.

### 5.9 Docs (exact sentences, pinned by `tests/test_docs_f2.py` and E1)

- **S1** (`docs/web.md`, replaces D-9): "The production resolver now captures the full 15-field identity for a
  marked turn, but the `/scieflow-menu` entry still matches no deployment: its working directory is the probe
  tree and its menu-skill bytes predate the F-1 change, so no other checkout produces the same identity."
- **S2** (`docs/web.md`, replaces the R5-1 residual): "Without a shell, the menu skill reads the code-owned
  menu snapshot `skills/scieflow-menu/menu.json`, which a test keeps equal to the code; current settings and
  runs come from the run's own files."
- **S3** (`docs/agents.md` and `docs/sandbox.md`): "A conversation turn gets Claude's extended thinking from
  `reasoning: extended-thinking` (or the `env MAX_THINKING_TOKENS=32000 ` prefix on the agent's `cmd`): ScieFlow
  adds `MAX_THINKING_TOKENS=32000` to the turn's own environment, because an `env` prefix on `session_cmd` or
  `resume_cmd` is refused."
- **S4** (`docs/web.md`, API table/section): "When the HTTP API receives `mode=harness-command`, its response
  carries `\"harness_command\": {\"requested\": true, \"delivered_as\": \"chat\"}` with the same notice the page
  shows."
- **S5** (`docs/web.md`, T5R m-1): "Below the web UI and API, a marked turn on a platform without the required
  Linux admission primitives is refused; it is never run as chat there."
- **S6** (`docs/web.md`, T8A m-3c): "On Linux, a run listed in `unsandboxed_runs` (or with an unsafe writable
  grant) cannot hold a protected conversation: ordinary sends and hand-overs to it are refused as well."
- **S7** (`docs/web.md`, T5R n-1): "The charter appears exactly once only when the run has a non-empty charter;
  a run without one sends the message alone."
- **S8** (`docs/menu.md`): "Agents without a shell read `skills/scieflow-menu/menu.json`, the code-owned part of
  the same tree (sections, workflows, scopes, roles and the apply commands); `tests/core/test_menu_snapshot.py`
  keeps it equal to the code."

The G6 literals that stay true stay verbatim (D-1..D-8, D-11..D-14, the R5-3 limitation). `docs/agents.md:116-128`
and `docs/menu.md:67-69` keep their one-shot statements and gain S3/S8. No sentence may claim enablement at ROOT
(`tests/test_docs_k_harness.py:72-75` stays).

### 5.10 Security and data properties (checked by every review that touches them)

1. Fail-closed: every rule violation yields an incomplete identity; `support_for` is unchanged, so nothing can be
   enabled except by an exact match with the code-owned tuple, which F-2 does not touch.
2. No execution: the module spawns nothing (subprocess/exec/spawn/system/fork guard test); the version is a
   label from the path and the evidenced pairing only.
3. No writes: capture writes no file, event, job or audit field; ordinary turns are byte-identical except the
   effort environment entry where D2 applies.
4. Bounded, non-secret reads: only the files of 5.2; never `.claude.json`, `sessions/`, credentials, token files,
   run data, or the host's `~/.claude`; a credential bind inside the agent's `.claude/` voids discovery.
5. TOCTOU: the program hash is bound to the admitted path facts, which `_validate_execution` and admission
   re-check before launch (`service.py:687-693,729`); the other files are hashed as read (residual R2).
6. Effort environment: a code constant, applied after the C12 filter; no host value is copied; argv unchanged.
7. Data protection (M-B): unchanged; F-2 adds no data flow to a model (F2-D1) and reads no run content.
8. Web: no enablement; the new response key and display values carry no file content and are escaped.

### 5.11 What F-2 does not do

No live attempt, no new policy entry or pairing row, no change to `CERTIFIED_POLICIES` or its matching, no
web/API execution of marked turns (H-F2-1), no ROOT deployment (G5 H-3), no change to the one-shot effort path,
`config/agents.yml`, `agent configure` or its warning (Ticket 12), no `conversation.read` hardening (T8B m-2), no
job-log descriptor change (T8B m-1), no Starlette pin (T8B m-3), no `_say_legacy` cleanup (T8B n-1), no DOM
timeout change (T8B m-4). Those remain the separately ticketed follow-ups of the G6 report.

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`); Sonnet
5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a
checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only), no push/PR, no
credentials or token files (tests use the existing fake-credential fixtures), no patient data (synthetic
fixtures only), never edits an `INFLIGHT` path or a never-edited file of section 3. Every implementation and fix
card runs the `INFLIGHT` guard and reports its exit code. Tests must be hermetic: no read of the real home,
`/home/ubuntu/.local/share/claude`, the probe tree or the RUN layout (the sealed suite excepted; the r14 fixture uses
those paths only as strings); versions folder and pairings are injected or monkeypatched.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/f-2-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-f-2-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/f-2/junit G6_CACHE_DIR=~/.cache/scieflow-f-2 RUN/tools/g6_pytest.sh WT <head-sha> f-2-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing rules
  (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with completion
  notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> f-2-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes:** the four named in the installer's standing rules, one rerun, both results recorded. No
  rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/f-2/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused sets** (clean-tree runner on the ticket's final commit): t1 `tests/core/test_harness_identity.py
  tests/core/test_harness_commands.py`; t2 `tests/core/test_f2_turn_identity.py tests/core/test_agent_run.py
  tests/core/test_harness_commands.py tests/core/test_k_task4.py tests/core/test_k_task5.py tests/core/test_service.py
  tests/core/test_turn_admission.py tests/core/test_sandbox_credentials.py tests/core/data/test_dispatch_paths.py
  tests/probes` plus the sealed file under the lock; t3 `tests/core/test_menu.py tests/core/test_menu_snapshot.py
  tests/core/test_harness_identity.py`; t4 `tests/web`; t5 `tests/test_docs_k_harness.py tests/test_docs_f2.py` and
  every `tests/test_docs*.py`, plus `mkdocs build --strict` into `RUN/scratch/f-2/site` if mkdocs is in the venv.
- Tests that need a run use the existing fixtures (`tests/core/conftest.py` `project`, `k_conversational_project`,
  `offline_k_support`, `grant_credentials`); no agent CLI is launched except the existing protocol fixture.

### t0 — worktree, plan and spec copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the two ancestor checks and the three publish receipts;
   otherwise `BASE_RED` with the reason. `worktree add -b dev/f-2 RUN/scratch/f-2-wt <base>` (on a retry reuse
   it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed probes 0
   failed (directory run and the explicit sealed file). No expected counts are given; t0 records what it
   measures. Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths.
3. Re-verify every `path:line` cite of section 4 on `base`; write `reviews/f-2-t0-baseline.md` (per cite: still
   present / moved (new line) / changed). Inventories: (a) the merges between `M` and `base` and which files of
   section 3 they changed; (b) every caller of `agent_run.resolve_turn` and every test wrapper of it, with its
   signature (a wrapper that does not forward unknown keywords would break with `capture_identity`; list them);
   (c) every test asserting the production identity shape (expected: `tests/core/test_agent_run.py:1021`) or the
   docs literals of E1/E-extra; (d) `uv run scieflow menu --json` run from WT on base, saved to
   `RUN/scratch/f-2/menu-json-base.json` with its sha256, and the list of files in `skills/scieflow-menu/`;
   (e) whether M-I's CI files (`.github/workflows/tests.yml`, `scripts/ci-tests.sh`) and `tests/core/cli_parity.yml`
   are on base, and whether any test enumerates public service functions or MCP tools that F-2 could affect;
   (f) whether `tests/probes/conftest.py:24-27` is unchanged; (g) the sealed suite's count.
4. Copy `reviews/f-2-plan.md` byte for byte to `docs/superpowers/plans/2026-11-02-f-2-identity-capture.md`, and its
   Part A (from the line `## Part A — Spec` up to, not including, the line `## Part B — Implementation plan`) to
   `docs/superpowers/specs/2026-11-02-f-2-identity-capture-design.md` with this first line:
   `# F-2 identity capture and effort delivery — design (Part A of docs/superpowers/plans/2026-11-02-f-2-identity-capture.md)`
   and one blank line. Commit both alone ("docs(plans): F-2 identity capture spec and plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — the recipe module (Haiku) → t1r (Sonnet)

Files: section 3 rows t1. Write `harness_identity.py` per 5.2-5.4 (`turn_effort` included, not wired yet) and the
fixture `tests/fixtures/harness_identity_r14.json` built from `RUN/scratch/k-g5/t4-identity.json` (b4fef519…):
the `recipe_records`, `identity_t4`, the three digests, the policy sha `a4e7bd2f…` (record the source sha256 in the
fixture's `source` key; the test reads only the fixture).

RED → GREEN:
1. r14 equality: `derive(...)` on the fixture's facts (versions folder `/home/ubuntu/.local/share/claude/versions`
   passed explicitly) equals `identity_t4` field for field **and** equals
   `harness_commands.CERTIFIED_POLICIES[0].identity`; the three `canonical_digest` values equal `f027fcca…`,
   `352742d6…`, `255c46d5…`; `support_for` on it is enabled with the production tuple and disabled with the tuple
   monkeypatched to `()`.
2. Version rule: other hash → `None`; path outside the versions folder (e.g. the lexical launcher) → `None`;
   non-`x.y.z` basename → `None`; a monkeypatched extra pairing is honoured only for its own hash.
3. Every one of the 15 single-field mutations of the derived identity is disabled under the production tuple.
4. IO readers on a temp project (synthetic files): exact records; each violation of 5.2/5.3 (absent file where
   required, symlinked AGENTS.md → `None` value, symlinked or oversized settings file, unknown hook command,
   settings.json not an object, a non-link or escaping link in `.claude/skills`, a subfolder or symlinked file in a
   skill folder, `settings.local.json`/`CLAUDE.local.md` present, a credential target under the agent's `.claude/`,
   more than 64 entries, an unreadable file) yields `None` for the right record and an incomplete identity.
5. `executable_digest`: matching facts → hash; a fact with another inode, size or mtime → `None`; a symlink path →
   `None`; a file rewritten during hashing (monkeypatched read) → `None`.
6. `reasoning_label` and `turn_effort`: every branch of 5.2 row 10 and 5.4, including `reasoning: default` with a
   prefixed `cmd` (off), a `{reasoning}` template (off), and a non-claude family (off).
7. No process: with `subprocess.Popen`, `os.execv`, `os.execve`, `os.posix_spawn`, `os.system`, `os.fork`
   monkeypatched to raise, `capture` on the temp project completes.

t1r checklist: recipe identical to `t4_identity_check.py:54-102` and identity-argument section 2 (schema strings,
key names, JSON form, pairing table); every extra rule only removes completeness (5.3); no write, no process, no
credential, no host home read; bounded reads with `O_NOFOLLOW`; ≤ ~200 production lines; `harness_commands.py`
unchanged; INFLIGHT guard exit 0.

### t2 — wiring and effort delivery (Haiku) → t2r (Sonnet)

Files: section 3 rows t2.

RED → GREEN:
1. `resolve_turn` without the keyword returns the identity of today (`uncaptured`, field for field) — the existing
   `tests/core/test_agent_run.py:1021` stays green unchanged.
2. `resolve_turn(..., capture_identity=True)` on `k_conversational_project` (sandboxed, fake credentials) with a
   test-owned claude-family turn agent whose launcher is an executable script at a temporary
   `…/versions/9.9.9` outside the run directory (the sealed suite's fake-launcher pattern,
   `tests/probes/test_k_task8_binding.py:117-149`), `claude_versions_dir` and `VERSION_PAIRINGS` monkeypatched to
   that folder and the script's hash, and the project given `AGENTS.md`, `.claude/settings.json`, the hook and the
   skill links: every field of 5.2 filled, the identity complete, `support_for` disabled under the production
   tuple ("No exact certified …"), and `prepare_turn` of a marked `/scieflow-menu` raises `DispatchError`; with
   `offline_k_support` bound to the captured identity the marked turn prepares (the existing K path).
3. The same with the run unsandboxed (`unsandboxed_runs`): discovery `None`, `effects_pinned` False, incomplete.
4. Effort: a claude-family turn agent with `reasoning: extended-thinking` gets `MAX_THINKING_TOKENS=32000` in
   `ResolvedTurn.env_items`, `PreparedTurn.env_items` and the `env` handed to `jobs.start` (spy), sandboxed and
   unsandboxed, ordinary and marked; never in argv; with `reasoning: default` or a non-claude family nothing is
   added; a host `MAX_THINKING_TOKENS` never reaches a sandboxed turn without the setting; the captured identity's
   `reasoning` is `env:MAX_THINKING_TOKENS=32000` exactly when delivered.
5. `service.say` with a marked submission passes `capture_identity=True` and an ordinary one `False` (spy on
   `resolve_turn` keywords); no new audit field, event or job field (compare the audit dict keys and
   `events.jsonl` types with an ordinary turn on base behaviour).
6. Capture adds no call to `_capture_execution`, `resolve_turn_protection` or `charter.snapshot` (call counters)
   and writes nothing (`create_home=False` resolution leaves the run tree byte-identical).
7. E2 and `tests/probes/test_f2_capture_guard.py`: the fixture applies to `test_k_task8_binding.py` only; the sealed
   file passes 0 failed under the lock.

t2r checklist: additive change only (one keyword, one env entry, one call argument); default path byte-identical;
every inventory (b) wrapper still works; effort constant applied after C12, never in argv, family/placeholder rule
exact; sealed suite 0 failed with E2 and nothing else changed under `tests/probes`; INFLIGHT guard exit 0.

### t3 — F-1 menu without a shell (Haiku) → t3r (Sonnet)

Files: section 3 rows t3. First record `uv run scieflow menu --json` from WT on the clean pre-change tree (equal
to t0's `menu-json-base.json`).

RED → GREEN:
1. `static_menu_json()` has exactly the keys and order of 5.6 and no value read from `config/` or `workspace/`
   (a test changes `config/agents.yml` menus in a temp repo and the static result is unchanged).
2. `menu_json()` equals the composition of static and dynamic parts and `scieflow menu --json` prints bytes equal
   to the recorded base output (same repo state).
3. `tests/core/test_menu_snapshot.py`: the checked-in `skills/scieflow-menu/menu.json` equals
   `json.dumps(static_menu_json(), indent=2, ensure_ascii=True) + "\n"`; a temp copy with one changed byte fails
   with the regeneration command in the message; `write_static_snapshot(tmp)` writes exactly that text.
4. `SKILL.md`: the no-shell path names `skills/scieflow-menu/menu.json` and the Read/Glob sources of 5.6, says never
   to reconstruct the menu from `src/`, and its effort sentence matches 5.4; the shell path still names
   `uv run scieflow menu --json`; every `scieflow` command the skill names exists in the assembled click tree.
5. Identity: `read_extensions` over a temp copy of the new `.claude/skills` + `skills/scieflow-menu/` +
   `skills/workspace-sync/` yields an `extensions_sha256` different from the entry's `352742d6…`, and an identity
   equal to the entry except that digest is disabled.

t3r checklist: output of `menu --json` byte-identical; the snapshot holds only code-owned values (F2-D1: no run,
no user setting, no path); the skill's other sections unchanged; INFLIGHT guard exit 0.

### t4 — web and API display (Haiku) → t4r (Sonnet)

Files: section 3 rows t4.

RED → GREEN:
1. API marked send: delivered as chat exactly as before (same `service.say` call, spy) and the response carries
   the `harness_command` object of 5.7 with the page's notice text; an ordinary API send's response has no such key.
2. Run page: a marked and an ordinary turn show `command-first` / `charter-first` after the badge; a forged audit
   value renders `unknown`, escaped; a turn without audit shows no composition text.
3. Job page: `composition: …` for a turn job; absent for a non-turn job; same mapping and escaping.
4. The existing web tests stay green unchanged (`tests/web`).

t4r checklist: no web route executes a marked turn; `harness_commands` still not imported by `web/`; no new
mutating route; CSRF/session unchanged; values escaped; INFLIGHT guard exit 0.

### t5 — docs (Haiku) → t5r (Sonnet)

Files: section 3 rows t5. Write S1-S8 of 5.9 verbatim at the named places, keeping every other G6 literal; E1
edits only the two literals; `tests/test_docs_f2.py` pins S1-S8 (reading only this repo's `docs/`, as
`tests/test_docs_k_harness.py:1-13` does), checks that `docs/web.md` still carries the R5-3 limitation and the "never
execute" sentence, and that no docs page claims enablement at ROOT.

RED → GREEN: the new docs test fails before the docs edit and passes after; E1 fails on the old literals and passes
on the new; every `tests/test_docs*.py` passes; `mkdocs build --strict` exit 0 (or "not available").

t5r checklist: every sentence true against the code on the tip (S5 `service.py:373-374`; S6
`turn_admission.py:367-368` and `tests/core/test_service.py:1701-1720`; S7 `agent_run.py:160-162`; S3 the t2 code);
no enablement claim; INFLIGHT guard exit 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (directory and
explicit file; absolute counts and the delta against t0 explained by added tests); the new F-2 test files run once
more under `env -i HOME=<empty dir under ~/.cache/scieflow-f-2-branch> PATH=/usr/bin:/bin` (hermeticity); if
`scripts/ci-tests.sh` is on base, `tests/test_ci_workflow.py` passes; `mkdocs build --strict` into
`RUN/scratch/f-2/site` (or "not available"); INFLIGHT guard over `base..tip`; linear history (no merge commits), at
least one commit per ticket t0..t5; `git diff base..tip -- tests/` removes lines only inside E1, E2 and recorded
E-extra entries (each mapped in the branch report); no never-edited file of section 3 in the diff
(`harness_commands.py` zero lines); `git diff base..tip -- pyproject.toml uv.lock` empty; `scieflow menu --json`
from WT equals `RUN/scratch/f-2/menu-json-base.json` byte for byte. Writes `RUN/scratch/f-2/pr-body.md` (plain
English: the five changes of section 1, what stays off, decisions F2-D1..D9, items H-F2-1..3, absolute counts,
residuals R1-R6, "please merge with a merge commit"; no credentials, prompts or patient data) and
`reviews/f-2-branch-report.md`. Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the G5 6.1 F-2 acceptance — r14 fixture equal field for field to
the T4 identity, each missing or mismatched input incomplete and disabled, subprocess guard, nothing enabled with
an empty or non-matching tuple; the F-1 acceptance — the skill no longer needs a shell, harness-command tests pass,
the entry stays disabled for the changed identity; T4-F1 closed by 5.4; decisions F2-D1..D9), code and security
(5.10), evidence (re-run memory-capped the t1-t5 focused sets and the sealed file; confirm the branch result's
counts; re-run the INFLIGHT guard; `pr-body.md` accurate). Output `reviews/f-2-final-review-r<N>.md`. PASS → the
installer's publish step; CHANGES → final_fix (new commits, full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/f-2-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   recipe module                          DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        wiring, effort delivery, sealed E2     same shape → t3
t3/t3r/t3f        F-1 menu snapshot and skill            same shape → t4
t4/t4r/t4f        web/API display                        same shape → t5
t5/t5r/t5f        docs                                   same shape → branch
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install; no protected edits)
```

Caps `{max_cards: 60, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/f-2-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff, decisions, gap,
`g5plan` (`reviews/k-g5-plan.md`), `identity` (`reviews/k-g5-identity-argument.md`), `g6report`
(`reviews/k-g6-acceptance-report.md`); the installer adds `plan`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Still inert.** No deployment matches the entry after F-2 (S1), and the web/API never send a marked turn
  (H-F2-1). F-2 makes a match possible in principle; it does not produce one.
- **R2 Agent-writable inputs.** The agent's own `.claude/settings*.json` lives in its writable home, and project
  files can change after they were hashed (only the program file is bound to admitted facts). A hostile agent or
  same-user writer can therefore make the recorded identity describe bytes that differ at launch; the same class
  as the accepted R5-3, D8 and A08 residuals. Fail-closed for every change that happens before capture.
- **R3 Recipe scope.** The recipe covers `settings*.json` in the agent home only (D8), project files by name, and
  built-in CLI plugins only through the binary hash; other claude discovery sources are not fingerprinted beyond
  the extra absence checks of 5.3.
- **R4 Static snapshot.** The no-shell menu shows the code-owned tree; current settings and runs come from the
  agent reading files, which is correct but less convenient than the command. Any menu change in code changes the
  snapshot and thus every future identity (intended).
- **R5 Effort semantics.** D2 couples conversation effort to the `cmd` prefix when `reasoning` is unset; the
  `agent configure` warning still says "only a label" (Ticket 12).
- **R6 Hash cost.** A marked turn hashes the program file (~250 MB, about a second) each time; ordinary turns do not.

## 9. After F-2

Queue (`continuity/milestones.yml:30-32`): Ticket 12 (`after: [f-2]`, owns the effort dropdown D4, the registry
`how` text and the configure warning, R5), Ticket 8 and then Ticket 9.

**H-F2-2, the human-gated live attempt (separate item, not scheduled by the chain).** Only on the human's explicit
word: (1) choose the identity to certify (for example this checkout's cwd with a pinned claude binary and a run
override with the D5 tool restriction), (2) pair that binary's hash with its version once (one bounded
`--version` run under the human's consent, as G4 did), (3) a new binding generation (new seal and release
receipt) run with the live cap the human sets, (4) a new G5-style independent certification of the evidence
against the F-2 recipe, (5) a new entry and its review, (6) H-F2-1 for the web/API path. Each is its own approval;
the flow never starts any of them.

Other follow-ups surfaced while planning (from the G6 report, unchanged): `conversation.read` hardening (T8B m-2),
the ordinary job-log read by descriptor (T8B m-1), a Starlette bound (T8B m-3), `_say_legacy` cleanup (T8B n-1),
the DOM timeout (T8B m-4), host paths of the entry (H-F2-3).
