# Quickstart

Run the reference denoise experiment end-to-end (~5 minutes + one container
build).

## 1. Install

```bash
conda env create -f envs/experiments.yml
conda activate scieflow-experiments
scieflow experiment --version
```

Requires Apptainer (`apptainer --version`) for containerized runs.

## 2. Generate test data

```bash
python pipelines/denoise/generate_data.py
```

Writes `pipelines/denoise/data/truth.png` (clean synthetic image) and
`noisy.png` (gaussian noise added).

## 3. Build the stage container

```bash
scieflow experiment env build pipelines/denoise/stages/denoise
```

Generates `env/denoise.def` from the stage's environment spec and builds
`env/denoise.sif`.

## 4. Run the campaign

```bash
scieflow experiment sweep -c pipelines/denoise/campaigns/sigma-sweep.yaml
```

Seven containerized runs (sigma 0.5 … 3.5), each recorded under
`experiments/denoise-sigma-sweep/runs/`.

## 5. Compare and report

```bash
scieflow experiment compare experiments/denoise-sigma-sweep
scieflow experiment report experiments/denoise-sigma-sweep
```

Open `experiments/denoise-sigma-sweep/report.md` — ranked results table,
validation status, and the SSIM-vs-sigma QC plot with the optimum starred.

## 6. Let an agent drive

In any supported agent CLI, from the repo root:

> Design a follow-up campaign to refine the optimal sigma from
> experiments/denoise-sigma-sweep.

The agent reads `AGENTS.md`/`CLAUDE.md`, follows the designer skill, and
proposes a campaign YAML for your approval before running anything.
