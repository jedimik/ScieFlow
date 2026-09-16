# Plan: WhatsNEW → `scieflow.news`, plus layered agent configuration

## Context

WhatsNEW (`~/Github/WhatsNEW`, clean, pushed, 53 commits, 181 tests pass) is a local "what changed" tracker. You declare interests in YAML; each `run` sends a research prompt per interest to an agent CLI with web search; results go to TinyDB and render as sectioned markdown reports. It is about 2,400 lines: a click CLI (`init/run/status/export/models/templates/gui`), a NiceGUI web GUI, 5 research templates, and its own agent adapter. That adapter is deliberately restricted: claude gets `--allowedTools WebSearch WebFetch` and no permission bypass.

You want WhatsNEW maintained inside ScieFlow, mainly for its CLI, with the original repo frozen. You also asked for agent configuration you can change interactively:
- global defaults, per-workspace task overrides, and the news config;
- per task, which agent does each phase or role, plus each agent's model and reasoning;
- inheritance from the defaults, with the option to update either the default or one workspace.

### Decisions (user, 2026-09-16)
| Topic | Decision |
|---|---|
| Scope | CLI and GUI; the GUI sits behind its own optional extra |
| Name | `scieflow.news`, command `scieflow news` |
| Data | Interests in `config/news.yml`; database and exports in `workspace/news/` |
| News agents | Own config (`config/news.yml`), own restricted adapter; model changeable |
| Updater UI | CLI wizard plus the same command with flags, so a coordinator agent can ask in chat and then call it |
| Per-task selection | Per phase/role assignment plus per-agent model, reasoning and timeout; unset values inherit |
| Updater targets | Global defaults, a workspace override, the news config |
| Branch | Continue on `feat/unify-modules` (pushed; `main` untouched) |
| Freeze | Snapshot without history now; README pointer and GitHub archive only after you merge, confirmed first |

---

## Part A — News module (snapshot of WhatsNEW `2837e70`)

**Layout**
| From | To |
|---|---|
| `src/whatsnew/*.py` | `src/scieflow/news/` (`agents, config, db, prompts, report, runner, templates, cli`) |
| `src/whatsnew/gui/` | `src/scieflow/news/gui/` |
| `tests/*.py` | `tests/news/` (GUI tests skip when nicegui is absent; the `live` test stays opt-in) |
| `README.md` | `docs/news/index.md` (plus a mkdocs nav entry) and a short `src/scieflow/news/AGENTS.md` for agents |
| `FutureIdeas.md` | `docs/news/future-ideas.md` |
| `whatsnew.yaml` | `config/news.yml` |
| `~/.local/share/whatsnew/whatsnew.json` (16K) | copied once to `workspace/news/news.json`; original kept |
| dated `docs/superpowers` specs and plans | not copied (history stays in the archived repo) |

**Renames:** `import whatsnew` becomes `scieflow.news`. `WHATSNEW_DB` becomes `SCIEFLOW_NEWS_DB`. The export filename changes from `YYYY-MM-DD-whatsnew.md` to `YYYY-MM-DD-news.md`. GUI titles become "ScieFlow News". The legacy-name gate from the previous plan gains `whatsnew`.

**CLI:** `scieflow news {init,run,status,export,models,templates,gui}` is added to the lazy `GROUPS` in `src/scieflow/cli.py`.
- The `--config` default is `<repo>/config/news.yml`, resolved with `scieflow.core.config.repo_root()`.
- The `--db` default comes from `SCIEFLOW_NEWS_DB`, otherwise `<repo>/workspace/news/news.json`.
- The `export -o` default is `<repo>/workspace/news/reports/`.
- `scieflow news gui` without the GUI extra prints the install hint, the same way `LazyGroup` does. The import is already lazy inside `cli.gui`.

**Packaging:** add extras `news = ["tinydb>=4.8", "filelock>=3.12"]` and `news-gui = ["nicegui>=2.0"]`, and `pytest-asyncio` to the `dev` group. pytest gets `asyncio_mode = "auto"` and a `live` marker; `addopts` becomes `-m 'not slow and not live' --import-mode=importlib`.
- **Risk:** WhatsNEW's GUI tests set `pytest_plugins = ["nicegui.testing.user_plugin"]` inside test modules. That breaks when the tests sit below the root `conftest`. Fix: register the plugin conditionally in `tests/news/conftest.py` using `pytest.importorskip`, or in the root `tests/conftest.py` when nicegui is importable.

**Agent adapter stays restricted.** `news/agents.py` keeps its own command lines, because web-only tools are least privilege for a web research task. Only its model list stops being hard-coded: `CURATED_MODELS` for claude and codex reads `menu.models` from `config/agents.yml`, so model ids are maintained in one place. (The current list names stale `gpt-5.2-codex`.) agy keeps live `agy models` discovery.

**Tier note:** WhatsNEW lets agy do web research alone. That conflicts with the spirit of root AGENTS.md rule 10 and your "Gemini support tier only" preference. `scieflow news` is a user-invoked tool, not coordinator dispatch, so it keeps allowing agy. `config/news.yml` stays on `agent: claude`, and the news AGENTS.md tells coordinators not to pick agy on their own.

## Part B — Layered agent configuration

### Model

```
config/agents.yml            registry: cmd, model, reasoning, timeout_min, tier, enabled, menu
config/defaults.yml          assignments: role → agent (or list, for fan-out roles)
workspace/<slug>/config.yml  assignments: {...}      only the differences
                             agent_overrides: {...}  only the differences (already applied by agent_run)
config/news.yml              agent / model / reasoning / timeout (news module's own)
```

The `assignments:` in `config/defaults.yml` replaces today's `agent: claude`. Role names are namespaced:

```yaml
assignments:
  loop.hypothesize: claude
  loop.experiment: claude
  loop.literature: claude
  loop.synthesize: claude
  loop.notebook: claude
  research.search: [claude, codex, agy]      # fan-out
  research.cross-review: [claude, codex]
  research.gap-analysis: [claude, codex]
  research.reviewer: claude
  research.submitter: codex
  research.outline-agent: claude
  research.consistency-agent: claude
  research.journal-profile: [codex, agy]     # support allowed only when paired with a primary
support_roles: [research.search, research.journal-profile, research.condense]
```

The exact role list is taken from the four research `SKILL.md` files and the loop phases while implementing. Any role key a skill names must exist in the catalogue.

### New code: `src/scieflow/core/agent_config.py`
- `effective(root, ws=None) -> EffectiveConfig` merges registry + defaults + workspace and records the **source** of every value (`default`, `workspace`).
  - For migrated research runs, the legacy top-level keys `agents:`, `reviewer:`, `submitter:`, `outline_agent:` and `consistency_agent:` are read as a fallback. They are never written.
- `validate(effective)` checks:
  - known agent;
  - reasoning in that agent's `menu.reasoning.levels` when a list exists;
  - positive timeout;
  - a disabled agent is never assigned;
  - **tier rule 10:** a support agent only in `support_roles`, and in those only when a primary agent is also assigned.
- `apply(target, changes, *, workspace=None)` builds the new document, validates the effective result, and writes atomically. Writes are comment-preserving through **ruamel.yaml** (new core dependency) so your annotated YAML files keep their comments. For a workspace it writes only the keys that differ from the defaults; setting a value equal to the default removes the override. `--unset` removes one.
  - Targets: `default` (`agents.yml` fields and `defaults.yml` assignments), `workspace:<slug>` (`config.yml`), `news` (`config/news.yml` top-level agent keys, validated by `scieflow.news.config.load_config` before writing).

### CLI (`src/scieflow/core/cli.py`)
- `scieflow agent show [--workspace SLUG | --news] [--json]` prints the effective table (role → agent, agent → model/reasoning/timeout) with each value's source.
- `scieflow agent configure [--workspace SLUG | --news]`:
  - **Wizard** (no change flags): asks for the target, then "roles or agent settings", offers choices from `menu`, shows a diff, confirms, writes.
  - **Flags** (for coordinator agents): `--assign ROLE=AGENT[,AGENT]`, `--set AGENT.model=X`, `--set AGENT.reasoning=high`, `--set AGENT.timeout_min=60`, `--set AGENT.enabled=false`, `--unset KEY`, `--yes`. Validation failures exit nonzero with the reason. Nothing is written without `--yes` or an interactive confirmation.
- `agent_run` is unchanged: it already applies `agent_overrides` for prompts inside a workspace that has `status.yml`.

### Contract and skill updates
- **Root `AGENTS.md`:** new rule. Agent choice per role comes from `scieflow agent show --workspace <slug> --json`. Changes are made only through `scieflow agent configure` after asking you. Never hand-edit agent YAML.
- **Loop skills** (`experiment-cycle`, `literature-cycle`, `notebook`, `research-loop`) no longer hard-code `scieflow agent run claude`. They dispatch the agent assigned to `loop.<phase>`.
- **Research `AGENTS.md` run configuration gate:** step 4 "Persist" becomes `scieflow agent configure --workspace <slug> --assign … --set … --yes`.
- **Research skills:** role keys move to `research.*` assignments.
- `scripts/sfx_init.py` stays as it is: workspaces inherit and start with no overrides.

## Commits (on `feat/unify-modules`)
1. `feat(core): layered agent config — resolve, validate, show` (resolver, validation, `agent show`, `assignments:` in defaults, tests)
2. `feat(core): scieflow agent configure — wizard and flags, comment-preserving writes` (ruamel.yaml)
3. `feat(news): bring WhatsNEW in as scieflow.news` (code, tests, extras, lazy group, config and data copy)
4. `feat(news): agent settings in config/news.yml via agent configure; shared model menus`
5. `docs: contracts and skills use role assignments; news docs; MIGRATION.md WhatsNEW section; README`
6. Push.

## Verification
1. `uv sync --all-extras --all-groups && uv run pytest -q` passes with zero failures. That covers the existing 385 tests, WhatsNEW's 181 tests (minus the opt-in live test), and new agent-config tests: inheritance, source attribution, diff-only workspace writes, unset back to default, tier-rule refusals, comment preservation, disabled-agent refusal, and wizard input via `CliRunner(input=...)`.
2. **Install matrix:**
   - `uv sync --extra news` makes `scieflow news run --help` work, and `scieflow news gui` prints the install hint.
   - `uv sync --extra research` makes `scieflow news --help` print the hint.
3. **News end to end:**
   - `scieflow news status` reads `config/news.yml` and the migrated database (dates match the old WhatsNEW `status`).
   - `scieflow news templates` works.
   - `scieflow news run --interest Snakemake` with a fake agent via `agent_runner`, covered by the test suite.
   - One real run only with your OK, because it spends agent quota.
   - `scieflow news export --latest` writes into `workspace/news/reports/`.
   - `scieflow news gui --no-browser --port 8765` serves HTTP 200; stop it afterwards.
4. **Agent config end to end, on a temporary copy of a workspace:**
   - `agent configure --workspace <slug> --assign loop.experiment=codex --set codex.reasoning=high --yes`, then `agent show --workspace <slug>` shows the workspace values and everything else as default.
   - `--assign research.reviewer=agy` is refused with the tier reason.
   - `git diff config/` stays empty for a workspace-only change.
   - A `--news --set model=claude-sonnet-5` change keeps the comments in `config/news.yml`.
5. The legacy-name gate (now including `whatsnew`) returns zero hits outside dated history and `MIGRATION.md`. `mkdocs build --strict` passes.
6. `main` is unchanged; `feat/unify-modules` is pushed.

## Deferred (your confirmation needed later)
- WhatsNEW: README pointer and `gh repo archive jedimik/WhatsNEW`, after merge, together with the other two repos.
- Deleting `~/.local/share/whatsnew` and the WhatsNEW clone.
