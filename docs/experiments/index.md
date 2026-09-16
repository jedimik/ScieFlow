# ScieFlow experiments module

An agent-driven framework for computing experiments on signal and image data.

You have data at some processing stage. You want to run the next stage under
different parameter combinations and scenarios, then QC the outputs, compute
metrics, and validate. The ScieFlow experiments module makes that loop reproducible and lets AI
agents (Claude Code, Codex, Gemini CLI, agy, ...) drive it: they design
campaigns, you approve, they run, evaluate, report, and recommend what to
try next — grounded in scientific literature where possible.

## How it fits together

- **`scieflow experiment` CLI** — the deterministic mechanics: build one Apptainer container
  per stage, run parameter sweeps, record every run to the filesystem,
  compute metrics, generate reports.
- **Agent skills** (`src/scieflow/experiments/skills/`) — markdown instructions that teach any agent
  CLI the workflow and its rules (propose → approve → run; containers only;
  honest reporting).
- **Pipelines** (`pipelines/`) — your domains. The reference pipeline is
  Gaussian image denoising; adding a new domain means adding a pipeline,
  never touching the core.

## Reproducibility guarantees

- Every recorded run executes inside an Apptainer container built from the
  stage's declared environment (conda env file optional — plain pip/apt
  specs work too).
- Every run directory contains the resolved parameters, logs, artifacts,
  metrics, and an environment snapshot (container hash, exact command).
- Campaigns are plain YAML, committed to git; re-running one reproduces the
  experiment.

Start with the [Quickstart](quickstart.md).
