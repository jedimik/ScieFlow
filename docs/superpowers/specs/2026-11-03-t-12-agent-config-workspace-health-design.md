# T-12 agent configuration and workspace health — design (Part A of docs/superpowers/plans/2026-11-03-t-12-agent-config-workspace-health.md)

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

