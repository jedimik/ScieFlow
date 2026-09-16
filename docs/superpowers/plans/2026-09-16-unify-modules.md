# Plan: fold ExperimentX + ResearchX into ScieFlow as `scieflow.experiments` and `scieflow.research`

## Context

ScieFlow today drives two git submodules. `vendors/ExperimentX` provides the `expx` CLI for containerized campaigns. `vendors/ResearchX` provides the literature, gap, paper-draft and paper-review workflows. The coordinator dispatches headless sub-agents with `agent_run.py … --cwd vendors/<X>`, and each sub-agent follows that vendor's own AGENTS.md.

Maintaining three repos has costs:
- **Duplicated infrastructure.** ResearchX carries its own `agent_run.py`, `stub_agent.py`, config loader and `agents.yml`, with different timeouts, menus and defaults.
- **Drifting pointers.**
  - ResearchX: the submodule is on `feat/claim-check-support`, 2 commits ahead of the standalone repo's `main`.
  - ExperimentX: the standalone `main` is 1 commit ahead of the submodule.
- **Run data outside ScieFlow.** `vendors/ResearchX/workspace/*` are symlinks into `~/Github/ResearchX/workspace`.

Goal: a single ScieFlow repo that has two modules, one shared agent core, one `scieflow` CLI, and no legacy names (ExperimentX, ResearchX, expx, rxlib, vendors/).

### Decisions (user, 2026-09-16)
| Topic | Decision |
|---|---|
| History | Snapshot copy, no history import |
| Layout | Python package `src/scieflow/{core,experiments,research}` |
| Agent contracts | Keep **per-module AGENTS.md**; root AGENTS.md links to them, so a sub-agent loads only one module's context |
| Shared infra | One core: `agent_run`, `stub_agent`, config loader, one `config/agents.yml` |
| Old workspace files (~83 files in 9 runs cite `vendors/...`) | Leave frozen; document the path mapping |
| Run outputs | Everything under `workspace/<slug>/` |
| Packaging | One pyproject, extras `experiments` / `research`, one `scieflow` CLI |
| Old repos | Archive read-only, after this lands on main |
| Branch | New branch; **do not touch `main`** |

### Baseline (measured 2026-09-16)
- ScieFlow: 248 tests pass (`uv run pytest -q`).
- ResearchX: 77 pass (uv).
- ExperimentX: 48 pass, 2 `slow` tests deselected (`conda run -n expx pytest`; its uv venv lacks imageio).
- Legacy-name hits inside the vendor trees: ExperimentX docs 367 (mostly dated superpowers specs/plans), everything else under 60.

---

## Phase 0 — Branch and snapshot sources

1. Create `feat/unify-modules` from `feat/claim-check`, not from `main`. That base already contains claim-check, the DVC archive work, and the submodule pointer at ResearchX `feat/claim-check-support`, which ScieFlow's claim-check depends on. Your uncommitted `scripts/remote/*`, `tests/test_agent_run.py` and `tests/test_remote_cli.py` edits carry over in the working tree and stay uncommitted. `tests/test_agent_run.py` is also rewritten in Phase 2, so ask before touching it.
2. Snapshot sources are pinned by SHA and exported with `git archive`, so only tracked files come across (no `.venv`, egg-info or untracked locks):
   - ResearchX: `49fb773` (submodule HEAD, newest)
   - ExperimentX: `b771da4` (standalone `main`, newest)
3. Export into a scratch directory first, and record both SHAs in the commit message and in `docs/MIGRATION.md`.

## Phase 1 — Package skeleton and CLI

- `pyproject.toml`: switch from `package = false` to a src-layout build (hatchling) exposing `scieflow = "scieflow.cli:main"`.
  - Base deps: `pyyaml`, `click`.
  - Extras:
    - `experiments`: numpy, scikit-image, imageio, matplotlib
    - `research`: requests, jsonschema
  - Dependency groups: `dev` (pytest, responses), `docs` (mkdocs-material), and the existing `notebooklm` group.
- `src/scieflow/cli.py`: a click root with three groups (`agent`, `experiment`, `research`). Module groups import lazily, so a missing extra gives a clear "install with `uv sync --extra experiments`" message instead of an ImportError.
- pytest config: `testpaths = ["tests"]`, `--import-mode=importlib` (lets duplicate basenames like `test_config.py` coexist in subdirectories), the `slow` marker from ExperimentX, and `addopts = "-m 'not slow'"`. Keep `pythonpath = ["scripts"]` for the ScieFlow-only scripts that stay (see Out of scope).

## Phase 2 — Shared core `src/scieflow/core/`

- `config.py` merges `scripts/sflib/config.py` (repo_root, load_agents, load_defaults, load_run_config, tier_agents) with `rxlib/config.py`. The research workflow defaults (`max_papers`, review/debate rounds, `zotero`…) move from ResearchX's `agents.yml` into `config/defaults.yml` under a `research:` key.
- `agent_run.py` starts from ScieFlow's version (it already adds `--cwd` and `{root}`). It gains ResearchX's `{reasoning}` placeholder and automatic `agent_overrides` from `workspace/<slug>/config.yml`, and is exposed as `scieflow agent run`.
- `stub_agent.py` is a superset: ResearchX's kinds (findings, review, …) plus ScieFlow's.
- `config/agents.yml` is the single registry. It keeps ScieFlow tiers and `stdin_cmd`, and adds ResearchX's `menu:` blocks and `reasoning`. `timeout_min` defaults to ScieFlow's 180; per-run `agent_overrides` can lower it.
- Delete `scripts/agent_run.py`, `scripts/stub_agent.py` and `scripts/sflib/config.py`. Keep `scripts/sflib/archive.py` (DVC). Update the 10 `sflib` import sites to `scieflow.core.config`.
- Tests go in `tests/core/`, merging both suites' `test_agent_run`, `test_config` and `test_stub_agent*`.

## Phase 3 — `scieflow.experiments` (from ExperimentX)

| From (ExperimentX @ b771da4) | To |
|---|---|
| `src/expx/*.py`, `metrics/` | `src/scieflow/experiments/` |
| `AGENTS.md`, `skills/` | `src/scieflow/experiments/AGENTS.md`, `…/skills/` |
| `pipelines/denoise/` (user-extensible content) | repo `pipelines/denoise/` |
| `model-routing.yaml` | `src/scieflow/experiments/model-routing.yaml` (see Risks) |
| `environment.yml` | `envs/experiments.yml` (conda env renamed `scieflow-experiments`) |
| `tests/` | `tests/experiments/` (`pytest.importorskip("numpy")` guard in conftest) |
| `docs/*.md` (not dated superpowers specs/plans) | `docs/experiments/` |
| `CLAUDE.md`, `README.md`, `TODOS`, `VERSION`, `mkdocs.yml` | dropped; content folded into module AGENTS.md / root docs |

- Rename imports `expx.` → `scieflow.experiments.`. The CLI becomes `scieflow experiment {run,sweep,compare,metrics,report,env build,new pipeline,new stage}`.
- `--experiments-dir` loses its bare `experiments` default and must point under `workspace/<slug>/experiments/`. The CLI refuses paths outside `workspace/` unless `--direct` is used for tests. `--pipelines-dir` defaults to the repo `pipelines/`.
- Rewrite skill `literature-support` to delegate to the research module's search commands. Root AGENTS.md rule 6 keeps literature in one place, so the skill is not dropped silently.
- Apptainer image names and container labels containing `expx` are renamed. Check `envbuild.py` for any on-disk `.sif` naming that old runs rely on, and list it in MIGRATION.md.

## Phase 4 — `scieflow.research` (from ResearchX)

| From (ResearchX @ 49fb773) | To |
|---|---|
| `scripts/search_{openalex,arxiv,europepmc,crossref}.py` | `src/scieflow/research/search/{openalex,arxiv,europepmc,crossref}.py` |
| `scripts/{validate_findings,check_citations,zotero_export}.py` | `src/scieflow/research/{validate,citations,zotero}.py` |
| `scripts/rxlib/{http,papers}.py` | `src/scieflow/research/lib/` |
| `scripts/agent_run.py`, `stub_agent.py`, `rxlib/config.py` | merged into core (Phase 2) |
| `schemas/*.json` | `src/scieflow/research/schemas/` (`SCHEMAS_DIR` via `importlib.resources`) |
| `templates/` | `src/scieflow/research/templates/` |
| `AGENTS.md`, `GEMINI.md`, `skills/{lit-review,gap-discovery,paper-draft,paper-review}` | `src/scieflow/research/AGENTS.md` (GEMINI.md folded in), `…/skills/` |
| `config/journals/*` (runtime cache) | repo `config/journals/` |
| `setup/{install,doctor}.sh` | merged into ScieFlow `setup/` |
| `docs/*.md`, `.github/workflows/docs.yml`, `mkdocs.yml` | `docs/research/`, one root `mkdocs.yml` + one docs workflow |
| `tests/` | `tests/research/` |

- CLI: `scieflow research {search openalex|arxiv|europepmc|crossref, validate, check-citations, zotero-export}`. Rewrite all 48 `uv run scripts/<x>.py` invocations in skills, docs and templates to the new commands (`agent_run` → `scieflow agent run`).
- Hard rule 1 in the research AGENTS.md (artifacts in `workspace/<slug>/`) now means ScieFlow's workspace. Research run dirs sit at `workspace/<slug>/research/`.
- ScieFlow `schemas/manifest.yml` and the notebook skill reference the data-package doc at its new path, `docs/research/data-packages.md`.

## Phase 5 — ScieFlow integration

- Root `AGENTS.md`:
  - Roles now dispatch "into a module", and rule 2 uses `uv run scieflow agent run … [--cwd src/scieflow/<module>]`.
  - Rule 6 names `scieflow research search`.
  - Orientation lists both modules with a link to each module AGENTS.md, plus the sentence: *"Sub-agents: read only the module AGENTS.md your prompt names."*
- `CLAUDE.md` gets the same pointer.
- Skills `experiment-cycle`, `literature-cycle` and `notebook`: new `--cwd` values, prompt templates say "operating the ScieFlow experiments/research module", and outputs go to absolute `workspace/<slug>/{experiments,research}/` paths.
- Also update `README.md`, `setup/install.sh` (conda env name, extras), the `pyproject.toml` description, the comments in `scripts/nblm/{resolve,claims}.py`, and `.dvcignore` (drop the `vendors/*` lines; add `/src/`, `/pipelines/`, `/envs/`).
- Remove the submodules: `git submodule deinit -f vendors/*`, `git rm vendors/ExperimentX vendors/ResearchX`, delete `.gitmodules`, and clean `.git/modules/vendors`.

## Phase 6 — Run-data migration (copy, never move or delete)

1. Measure first with `du -shL ~/Github/ResearchX/workspace/*` (the `neuroSnake*` trees were still being measured when this plan was written). Stop and ask if the total exceeds free disk minus 10 GB.
2. Copy with `rsync -aL`, dereferencing symlinks, into `workspace/<slug>/research/` for:
   - `2026-07-globus-pallidus-parcellation`
   - `2026-07-gpi-mask-sensitivity-gap`
   - `2026-07-segsnake-softwarex-paper1-validation`
   - `2026-07-segsnake-target-strategy`
   - `2026-08-segsnake-softwarex-validation-update` (the 25 MB real dir inside the submodule)
   - `2026-09-full_brain`
   - `neuroSnake`
   - `neuroSnakev2`
3. Two slugs collide with existing **DVC-tracked** ScieFlow workspaces: `2026-07-segsnake-target-strategy` and `2026-08-segsnake-softwarex-validation-update`. Their data goes into `research/` subdirectories, so their DVC hashes change. Report this; re-tracking and pushing stays a separate, user-approved step.
4. The originals in `~/Github/ResearchX` and the ExperimentX demo output `experiments/denoise-sigma-sweep` stay where they are until the archive step.

## Phase 7 — Legacy-name gate and migration note

- `docs/MIGRATION.md`: the old→new table (paths, commands, env name, import names), both snapshot SHAs, and a note that the ~83 workspace files citing `vendors/...` are frozen historical records along with how to re-run one.
- The gate must return **zero** hits:
  `grep -rniIE "experimentx|researchx|\bexpx\b|rxlib|vendors/" . --exclude-dir={.git,.venv,workspace,.dvc} --exclude=MIGRATION.md --exclude-dir=docs/superpowers`
  Dated `docs/superpowers/{specs,plans}` are historical records and stay unedited. The vendors' own dated specs and plans are not copied.

## Phase 8 — Archive old repos (deferred until this branch is merged to main by you)

1. Push ResearchX `feat/claim-check-support` or merge it into its `main`, so nothing exists only in a submodule.
2. Commit a README pointer to ScieFlow in both repos, then `gh repo archive jedimik/ExperimentX` and `gh repo archive jedimik/ResearchX`. These are outward-facing, so confirm right before running them.
3. Leave the local clones in place.

---

## Risks and open follow-ups (not blocking)

- **`model-routing.yaml` vs `agents.yml` tiers.** Two routing sources, and ExperimentX's has stale model ids (opus-4-8, gpt-5.2-codex). This plan only moves the file; merging it into the core registry is a follow-up.
- **Metacentrum clones.** A remote ScieFlow checkout still has the submodules. After pulling this branch it needs `git submodule deinit`, and job helpers under old workspaces still cite `vendors/ExperimentX/.venv/bin/python`. New campaigns must use the package venv. Note this in `skills/remote-exec/SKILL.md`.
- **Claude Code parent-directory CLAUDE.md loading.** A sub-agent started in `src/scieflow/<module>` also loads the root CLAUDE.md. Keep that file a two-line pointer so module sub-agents don't pull the full root contract.

## Out of scope

- Moving ScieFlow's own `scripts/` (status, budget, checkpoint, sfx_init, validate, dvc_sync, remote/, nblm/) into the package. It is natural next, but it rewrites rules 4, 11 and 12 and every loop skill.
- Merging or pushing the branch.
- Re-tracking DVC data.

## Verification

1. `uv sync --all-extras --group dev && uv run pytest -q` shows zero failures. The expected count is about 373 before dedup (248 + 77 + 48). Merged core tests reduce that; list removed duplicates in the PR description.
2. `uv run pytest -m slow tests/experiments` passes where apptainer exists, or is reported as skipped.
3. CLI smoke tests:
   - `uv run scieflow --help`
   - `uv run scieflow research search openalex --help`
   - `uv run scieflow experiment sweep pipelines/denoise/campaigns/sigma-sweep.yaml --direct --experiments-dir workspace/_smoke/experiments` (delete `_smoke` afterwards)
   - `uv run scieflow agent run stub <prompt> <transcript> --cwd src/scieflow/research`
4. Loop dry run: `tests/test_dry_run.py` passes, and one manual stub iteration via `skills/research-loop` produces `results-summary.md` and `literature.md` under `workspace/<slug>/`.
5. With the research extra only (`uv sync --extra research`), `scieflow experiment --help` prints the install hint instead of a traceback.
6. The legacy-name gate (Phase 7) returns zero hits, and `mkdocs build --strict` passes.
7. `git status` on `main` is unchanged, and `vendors/` and `.gitmodules` are gone on the branch.
