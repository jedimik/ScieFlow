# ScieFlow experiments module — instructions for AI agents

You are operating ScieFlow's experiment framework for signal/image data
processing. The deterministic mechanics live in the `scieflow experiment`
CLI; your job is judgment: designing campaigns, interpreting results,
recommending next steps. Paths below are relative to the ScieFlow repo root,
which is your working directory.

## Non-negotiable rules

1. **Propose → approve → run.** Never execute a campaign the user has not
   approved. Present the campaign YAML and your reasoning first. After
   approval, run the whole campaign without further prompts.
2. **No bare-metal recorded runs.** Recorded experiments run in Apptainer
   containers (`scieflow experiment env build`, then
   `scieflow experiment sweep` / `scieflow experiment run`). The `--direct`
   flag is for development and tests only.
3. **Never bypass `scieflow experiment` for run bookkeeping.** Do not
   hand-write run directories, metrics.json, or results.json.
4. **Report honestly.** Failed runs stay in the report. Do not delete or
   rerun-until-green.
5. **Run outputs live in the run's workspace.** Always pass
   `--experiments-dir workspace/<slug>/experiments` (the command requires it).

## Skills (read the relevant one before acting)

| Task | Skill file |
|---|---|
| Design a new experiment campaign | `src/scieflow/experiments/skills/experiment-designer/SKILL.md` |
| Build environments, execute campaigns | `src/scieflow/experiments/skills/experiment-runner/SKILL.md` |
| QC, metrics, validation, reports | `src/scieflow/experiments/skills/evaluator/SKILL.md` |
| Ground results in literature | `src/scieflow/experiments/skills/literature-support/SKILL.md` |
| Add a stage/metric/pipeline/domain | `src/scieflow/experiments/skills/framework-extender/SKILL.md` |
| Choose which model runs a phase | `src/scieflow/experiments/skills/model-routing/SKILL.md` |

## Orientation

- Concepts: a **stage** (containerized processing step) is run by a
  **campaign** (approved YAML: input, parameter grid or scenarios, metrics,
  validation) producing **runs**
  (`workspace/<slug>/experiments/<campaign>/runs/<id>/`).
- Reference pipeline: `pipelines/denoise/` — read it before creating anything.
- CLI: `scieflow experiment --help`; docs: `docs/experiments/`.
- Environment: `uv sync --extra experiments` for the CLI;
  `conda env create -f envs/experiments.yml` where container builds need
  conda.
- Model economy: match the model tier to the phase
  (`src/scieflow/experiments/model-routing.yaml`) — judgment on high-quality
  models, mechanical work on the cheapest. See the model-routing skill.

## Run directory hygiene

Scripts you write go in `workspace/<slug>/tools/`; tests, temp dirs, envs,
clones and caches go in `workspace/<slug>/scratch/` (excluded from archives).
Keep `logs/` for prompts and transcripts. Never rename or move a run
directory.
