# Migration: ExperimentX and ResearchX folded into ScieFlow

As of 2026-09-16 (branch `feat/unify-modules`), the two former submodules are
part of ScieFlow itself and are no longer maintained separately:

| Former repository | Snapshot imported | Now |
|---|---|---|
| ExperimentX | `b771da4` (standalone `main`) | `scieflow.experiments` — `src/scieflow/experiments/` |
| ResearchX | `49fb773` (`feat/claim-check-support`) | `scieflow.research` — `src/scieflow/research/` |

The code was copied as a snapshot, not with its git history. The full history
stays in the archived repositories.

## Install

```bash
uv sync --extra experiments --extra research   # both modules (setup/install.sh does this)
uv sync --extra research                       # literature work only
```

The former `expx` conda environment is now `scieflow-experiments`
(`conda env create -f envs/experiments.yml`). It is needed only where stage
container builds rely on conda.

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
| `2026-07-segsnake-target-strategy` | `workspace/2026-07-segsnake-target-strategy-research` *(renamed: slug already used by a DVC-tracked ScieFlow run)* |
| `2026-08-segsnake-softwarex-validation-update` | `workspace/2026-08-segsnake-softwarex-validation-update-research` *(renamed: same reason)* |
| `2026-09-full_brain` | `workspace/2026-09-full_brain` |
| `neuroSnake`, `neuroSnakev2` | `workspace/neuroSnake`, `workspace/neuroSnakev2` |

Renamed runs keep their original `slug:` inside `status.yml`, since the file is a
historical record. `workspace/neuroSnake/` holds absolute symlinks to
`~/Github/SegSnake` and `~/Github/research/SoftwareX_SegSnake`. They are kept
as links and excluded in `.dvcignore`, so DVC never hashes their 1.4T of targets.
None of the migrated runs is DVC-tracked yet.

## Existing ScieFlow runs are frozen

About 83 files in 9 older workspaces (logs, helper scripts, campaign notes)
cite `vendors/ExperimentX/…` or `vendors/ResearchX/…`. They are historical
records and were not rewritten (AGENTS.md rules 1 and 9). To re-run such a
helper, map its paths with the table above. For example:

- `vendors/ExperimentX/.venv/bin/python` → `.venv/bin/python` after
  `uv sync --extra experiments`
- `PYTHONPATH=vendors/ExperimentX/src` → not needed; the package is installed

Known gap at migration time: `2026-07-segsnake-thalamus-mrtrix-validation`
cites `vendors/ExperimentX/pipelines/thalamus-mrtrix/`, which exists in neither
ExperimentX checkout. It was not part of either snapshot.

## Local checkouts made before the merge

On `feat/unify-modules` the submodule links and `.gitmodules` are gone. An
existing clone still has the old checkouts on disk under `vendors/`. They are
untouched and hidden locally via `.git/info/exclude`, so switching back to an
older branch still works. Once nothing needs them:

```bash
git submodule deinit -f vendors/ExperimentX vendors/ResearchX   # on an older branch, or:
rm -rf vendors .git/modules/vendors                              # after this branch is merged
```

Check `vendors/ResearchX/workspace/` and `vendors/*/.superpowers/` for local
files you still want before deleting.

The same applies to remote (metacentrum) clones: pull, then clean up by hand.
Remote jobs use the package environment.
