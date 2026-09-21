# Migration: ExperimentX, ResearchX and WhatsNEW folded into ScieFlow

As of 2026-09-16 (branch `feat/unify-modules`), the two former submodules and
the standalone WhatsNEW tool are part of ScieFlow itself and are no longer
maintained separately:

| Former repository | Snapshot imported | Now |
|---|---|---|
| ExperimentX | `b771da4` (standalone `main`) | `scieflow.experiments` — `src/scieflow/experiments/` |
| ResearchX | `49fb773` (`feat/claim-check-support`) | `scieflow.research` — `src/scieflow/research/` |
| WhatsNEW | `2837e70` (`main`) | `scieflow.news` — `src/scieflow/news/` |

The code was copied as a snapshot, not with its git history. The full history
stays in the archived repositories.

## Install

```bash
uv sync --extra experiments --extra research   # both modules (setup/install.sh does this)
uv sync --extra research                       # literature work only
uv sync --extra news --extra news-gui          # news CLI + web GUI
```

The former `expx` conda environment is now `scieflow-experiments`
(`conda env create -f envs/experiments.yml`). It is needed only where stage
container builds rely on conda.

## Old chats keep working

About 190 Claude and Codex chats from before the merge call the old entry
points. So that any of them can simply be resumed, **every old command below
still works**: it prints one line, `legacy: <old> → <new> (see
docs/MIGRATION.md)`, and delegates to the new command
(`src/scieflow/core/legacy.py`). Set `SCIEFLOW_LEGACY_QUIET=1` to hide the
notice. `scripts/check_legacy.sh` re-checks every shape.

- `scripts/agent_run.py`, `scripts/stub_agent.py`, `scripts/search_*.py`,
  `scripts/validate_findings.py`, `scripts/check_citations.py`,
  `scripts/zotero_export.py` — thin forwarders. `agent_run.py` drops a
  `--cwd vendors/<X>` so the agent runs from the repo root, where that code
  now lives.
- `expx …` and `whatsnew …` are console scripts again (`uv run expx`,
  `uv run whatsnew`). `expx run|sweep` without `--experiments-dir` stops with
  the exact corrected command.
- `from sflib.config import …` (used by ~17 helper scripts inside older runs)
  re-exports `scieflow.core.config`.
- Root `skills/<name>/` links to every skill that moved under
  `src/scieflow/{research,experiments}/skills/`, so `skills/lit-review/SKILL.md`
  and friends resolve.
- `RESEARCHX_MAILTO`, `RESEARCHX_PROMPT_ARGV_LIMIT` and `WHATSNEW_DB` are read
  as fallbacks for their `SCIEFLOW_*` names.

The old checkouts (`~/Github/{ExperimentX,ResearchX,WhatsNEW}`,
`vendors/{ExperimentX,ResearchX}`) are **kept**, each with a local commit that
puts a "moved to ScieFlow" banner at the top of its `AGENTS.md`, `CLAUDE.md`,
`GEMINI.md` and `README.md` (not pushed). Claude Code re-reads `CLAUDE.md`
when a chat resumes, so a chat started there is redirected. A resumed Codex
session may keep the instructions it started with; the shims above are what
keep it working.

## Commands

| Before | After |
|---|---|
| `uv run scripts/agent_run.py <agent> <prompt> <transcript> --cwd vendors/<X>` | `uv run scieflow agent run <agent> <prompt> <transcript>` (from the repo root) |
| `expx run / sweep …` | `scieflow experiment run / sweep … --experiments-dir workspace/<slug>/experiments` |
| `expx compare / metrics / report / env build / new …` | `scieflow experiment compare / metrics / report / env build / new …` |
| `uv run scripts/search_openalex.py "q"` (also arxiv, europepmc, crossref) | `uv run scieflow research search openalex "q"` |
| `uv run scripts/validate_findings.py <file> --schema …` | `uv run scieflow research validate <file> --schema …` |
| `uv run scripts/check_citations.py --workspace …` | `uv run scieflow research check-citations --workspace …` |
| `uv run scripts/zotero_export.py --workspace …` | `uv run scieflow research zotero-export --workspace …` |
| `python scripts/stub_agent.py` | `python -m scieflow.core.stub_agent` |
| `whatsnew init / run / status / export / models / templates / gui` | `scieflow news init / run / status / export / models / templates / gui` |
| `WHATSNEW_DB=…` | `SCIEFLOW_NEWS_DB=…` |
| editing agent YAML by hand | `scieflow agent show` / `scieflow agent configure` (see `docs/agents.md`) |

## Paths

| Before | After |
|---|---|
| `vendors/ExperimentX/src/expx/` | `src/scieflow/experiments/` (`import scieflow.experiments`) |
| `vendors/ExperimentX/pipelines/` | `pipelines/` |
| `vendors/ExperimentX/experiments/<campaign>/` | `workspace/<slug>/experiments/<campaign>/` (`--experiments-dir` is now required) |
| `vendors/ExperimentX/{AGENTS.md,skills/,model-routing.yaml}` | `src/scieflow/experiments/{AGENTS.md,skills/,model-routing.yaml}` |
| `vendors/ExperimentX/environment.yml` | `envs/experiments.yml` |
| `vendors/ResearchX/scripts/search_*.py` | `src/scieflow/research/search/*.py` |
| `vendors/ResearchX/scripts/rxlib/` | `src/scieflow/research/lib/` (config helpers: `scieflow.research.config`) |
| `vendors/ResearchX/{schemas,templates,skills}/`, `AGENTS.md` | `src/scieflow/research/{schemas,templates,skills}/`, `AGENTS.md` |
| `vendors/ResearchX/config/journals/` | `config/journals/` |
| `vendors/ResearchX/workspace/<slug>/` | `workspace/<slug>/` (each research run is its own workspace) |
| `vendors/*/docs/` | `docs/experiments/`, `docs/research/` (one mkdocs site) |
| `scripts/agent_run.py`, `scripts/stub_agent.py`, `scripts/sflib/config.py` | `src/scieflow/core/` |

## Configuration

- **Role assignments replace `agent: claude`.** `config/defaults.yml` no longer
  has a single `agent:` key; `assignments:` names the agent for each role
  (`loop.experiment`, `research.reviewer`, …). A run overrides roles and
  per-agent settings in its `config.yml` via `scieflow agent configure
  --workspace <slug>`.
- **Old run keys are honoured**, so a resumed run keeps the agents it ran
  with. They show as `workspace (legacy key)` in `scieflow agent show
  --workspace <slug>`, and an explicit `assignments:` entry always wins:

  | Old key in a run's `config.yml` | Role(s) it sets |
  |---|---|
  | `agent: X` (every older loop run) | `loop.experiment`, `loop.literature`, `loop.paper-draft` |
  | `agents: [...]` | narrows each fan-out role to those agents |
  | `reviewer`, `submitter`, `outline_agent`, `consistency_agent` | `research.reviewer`, `.submitter`, `.outline`, `.consistency` |
  | `draft_agents` | `research.draft-authors` |
  | `journal_profile_agents`, or `journal_profiler` (+ `primary_profile_crosscheck`) | `research.journal-profile` |
  | `support_idea_agent` | no role — reported as a warning |
- **One agent registry:** `config/agents.yml`. It keeps ScieFlow's models,
  commands and timeouts and gains ResearchX's `menu:` blocks. Codex now
  receives `-c model_reasoning_effort={reasoning}` (default `medium`).
- **Research workflow defaults** (`max_papers`, review and debate rounds,
  gap caps, outline approval, Zotero library) moved from the ResearchX
  registry's `defaults:` block to `research:` in `config/defaults.yml`.
- **Renamed environment variables:** `RESEARCHX_MAILTO` → `SCIEFLOW_MAILTO`
  and `RESEARCHX_PROMPT_ARGV_LIMIT` → `SCIEFLOW_PROMPT_ARGV_LIMIT`.
- **Zotero:** the default export collection is now `ScieFlow/<slug>`, not
  `ResearchX/<slug>`. Existing collections are untouched. Set `zotero.collection`
  in a run's `config.yml` to keep writing to an old one.
- **Run overrides:** `agent_overrides` in `workspace/<slug>/config.yml` apply
  only when that run also has `status.yml`. Research runs always create one;
  this is ScieFlow's stricter ownership check.

## Run data moved from ResearchX

Copied (not moved) with `rsync -a` and verified with `diff -r`. The originals
remain in `~/Github/ResearchX/workspace/` and in the local
`vendors/ResearchX/workspace/` checkout.

| Former run | ScieFlow workspace |
|---|---|
| `2026-07-globus-pallidus-parcellation` | `workspace/2026-07-globus-pallidus-parcellation` |
| `2026-07-gpi-mask-sensitivity-gap` | `workspace/2026-07-gpi-mask-sensitivity-gap` |
| `2026-07-segsnake-softwarex-paper1-validation` | `workspace/2026-07-segsnake-softwarex-paper1-validation` |
| `2026-07-segsnake-target-strategy` | `workspace/2026-07-segsnake-target-strategy` (`…-research` is an alias) |
| `2026-08-segsnake-softwarex-validation-update` | `workspace/2026-08-segsnake-softwarex-validation-update` (`…-research` and `… copy` are aliases) |
| `2026-09-full_brain` | `workspace/_misc/2026-09-full_brain` (not a run; old path is a link) |
| `neuroSnake`, `neuroSnakev2` | `workspace/_misc/neuroSnake` (old path is a link), `workspace/neuroSnakev2` |

The plain and `-research` copies turned out byte-identical (the plain one was
restored by a DVC checkout), as was a stray `… copy`. On 2026-09-21 each set
became one real directory — the dated plain slug, matching `status.slug` —
with the others as symlinks, so every path any chat cites still resolves. The
spare copies are kept in `workspace/_misc/duplicates/`; delete them when you
like. Non-runs (`neuroSnake`, `2026-09-full_brain`,
`2026-08-segsnake-clean-release`) moved to `workspace/_misc/`, also with a
link left behind. `workspace/_misc/neuroSnake/` holds absolute symlinks to
`~/Github/SegSnake` and `~/Github/research/SoftwareX_SegSnake`, excluded in
`.dvcignore`. `scieflow workspace list` shows aliases under their run.

## Existing ScieFlow runs are frozen

About 619 files in 11 older workspaces (mostly `logs/`, plus 34 helper
scripts and `deployment/*.json` requests) cite `vendors/ExperimentX/…` or
`vendors/ResearchX/…`. They are historical
records and were not rewritten (AGENTS.md rules 1 and 9). To re-run such a
helper, map its paths with the table above. For example:

- `vendors/ExperimentX/.venv/bin/python` → `.venv/bin/python` after
  `uv sync --extra experiments`
- `PYTHONPATH=vendors/ExperimentX/src` → not needed; the package is installed

Known gaps at migration time: `2026-07-segsnake-thalamus-mrtrix-validation`
and ~20–28 Codex sessions cite `vendors/ExperimentX/pipelines/thalamus-mrtrix/`,
`vendors/ExperimentX/paper/`, `vendors/ExperimentX/docs/proposals/`,
`tests/test_thalamus_mrtrix_*` and `experiments/thalamus-mrtrix-*`. None of
these exists in any ExperimentX checkout or snapshot — they were local,
uncommitted work — so no shim can restore them.

`expx collect` and `expx route` (used in 16 Codex sessions) never existed in
the imported snapshot and have no replacement.

## Local checkouts made before the merge

On `feat/unify-modules` the submodule links and `.gitmodules` are gone. An
existing clone still has the old checkouts on disk under `vendors/`, hidden
locally via `.git/info/exclude`. **Keep them**: about 150 Codex and 34 Claude
chats were started inside `vendors/`, and a chat can only be resumed from the
directory it started in. Each carries the "moved to ScieFlow" banner described
above. Delete them only when you are sure none of those chats matters.

Workspace `.dvc` pointers removed in commit `c300bb9` ("archive mode only"):
`2026-07-segsnake-target-strategy`, `2026-08-segsnake-clean-release`,
`2026-08-segsnake-softwarex-validation-update` and its `copy`. Runs are now
synced one at a time as zips (`docs/DVC_STORAGE.md`).

The same applies to remote (metacentrum) clones: pull, then clean up by hand.
Remote jobs use the package environment.

## WhatsNEW

| Before | After |
|---|---|
| `src/whatsnew/` (`import whatsnew`) | `src/scieflow/news/` (`import scieflow.news`) |
| `./whatsnew.yaml` | `config/news.yml` (copied from the WhatsNEW checkout) |
| `~/.local/share/whatsnew/whatsnew.json` | `workspace/news/news.json` (copied once; the original is untouched) |
| `reports/YYYY-MM-DD-whatsnew.md` | `workspace/news/reports/YYYY-MM-DD-news.md` |
| `uv tool install .` → `whatsnew` | `uv run scieflow news` from the ScieFlow checkout |
| cron: `cd WhatsNEW && uv run whatsnew run` | `cd ScieFlow && uv run scieflow news run` |

Reports and the GUI are titled "ScieFlow News". The claude/codex/agy command
lines are unchanged: news research still gives claude web tools only. Model
suggestions now come from each agent's `menu.models` in `config/agents.yml`,
and `scieflow agent configure --news` changes the news agent settings with
validation. The GUI needs the `news-gui` extra. If you installed `whatsnew`
globally with `uv tool install`, remove it with `uv tool uninstall whatsnew`
once you have switched, and update any cron entries.
