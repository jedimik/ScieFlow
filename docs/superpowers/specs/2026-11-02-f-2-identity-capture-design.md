# F-2 identity capture and effort delivery — design (Part A of docs/superpowers/plans/2026-11-02-f-2-identity-capture.md)

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

