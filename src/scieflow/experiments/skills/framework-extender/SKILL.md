---
name: framework-extender
description: Add a new stage, metric, pipeline, or data domain without touching the experiments core
---

# Framework Extender

The core (`src/scieflow/experiments/`) is domain-agnostic. New domains = new pipelines,
stages, and metrics. You should almost never modify `src/scieflow/experiments/`.

## New pipeline (new dataset or domain)
`scieflow experiment new pipeline <name>` scaffolds `pipelines/<name>/` with a default
stage, example campaign, and README. Replace the stage script's passthrough
logic and adjust `stage.yaml`.

## New stage (new processing step)
`scieflow experiment new stage <name> --pipeline <p>`, then:
1. Edit `stage.yaml`: description, `output_file`, environment — either
   `conda: environment.yml` (put the file next to stage.yaml) or
   `base_image` + `pip`/`apt` lists. Conda is optional.
2. Edit `run.py`: argparse with `--input`, `--output`, plus one argument per
   parameter. Write `<output_file>` into the `--output` directory. Exit
   non-zero on failure — the runner records it as a failed run.
3. `scieflow experiment env build pipelines/<p>/stages/<name>` and probe with
   `scieflow experiment run -c <campaign> -p <param>=<value>`.

## New metric
Create `pipelines/<p>/metrics/<file>.py`:

```python
def my_metric(output_path, reference_path) -> float:
    ...

METRICS = {"my_metric": (my_metric, True)}  # (fn, higher_is_better)
```

It is auto-loaded for that pipeline's runs; reference it in campaign
`metrics:`. Signature: `fn(output_path, reference_path) -> float`.
Metrics useful across pipelines belong in `src/scieflow/experiments/metrics/` with tests.

## Non-image data
Nothing in campaigns/runs/reports assumes images — only the built-in
metrics do. For 1D signals: stage reads/writes e.g. `.npy`, and you add
pipeline-local metrics (e.g., SNR) as above.
