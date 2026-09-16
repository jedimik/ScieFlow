# Concepts

## Stage

One processing step, defined by a directory:

```
pipelines/<pipeline>/stages/<stage>/
├── stage.yaml        # name, script, output_file, environment, description
├── run.py            # the processing script
└── env/              # generated: <stage>.def, <stage>.sif (gitignored)
```

`stage.yaml`:

```yaml
name: denoise
description: Gaussian denoising of a 2D image
script: run.py
output_file: denoised.png     # primary artifact the script must produce
environment:
  # EITHER conda (optional!):
  # conda: environment.yml
  # OR plain packages:
  base_image: python:3.12-slim
  pip: [numpy, scikit-image, imageio]
  apt: []
```

**Script contract:** invoked as
`python run.py --input <file> --output <dir> --<param> <value> ...`.
It must write `<output_file>` into the `--output` directory and exit
non-zero on failure.

## Campaign

One experiment: which stage, which data, which parameter combinations,
which metrics, what counts as valid. Written by you or proposed by an agent,
approved before running. Lives in `pipelines/<p>/campaigns/*.yaml`
(committed to git). Either a `parameters:` grid (cartesian product, `value:`
entries stay fixed) or a `scenarios:` list of named configurations.

## Run

One stage execution with one parameter set:

```
workspace/<slug>/experiments/<campaign>/
├── campaign.yaml       # resolved copy (absolute paths)
├── results.json        # all runs: params, status, metrics
├── comparison.json     # ranking + validation (scieflow experiment compare)
├── report.md, plots/   # scieflow experiment report
└── runs/<run-id>/
    ├── config.yaml         # resolved params, paths, status, timestamps
    ├── logs/               # stdout.log, stderr.log
    ├── artifacts/          # stage outputs
    ├── metrics.json        # computed metrics
    └── environment.json    # mode, exact command, sif sha256
```

Everything under `experiments/` is generated and gitignored; the campaign
YAML in `pipelines/` is the reproducible source of truth.

## Metrics

Registry of named functions `fn(output_path, reference_path) -> float` with
a `higher_is_better` flag. Built-ins: `ssim`, `psnr`, `mse`. Pipelines add
local metrics in `pipelines/<p>/metrics/*.py` — see
[Adapting to a New Domain](new-domain.md). Metrics are computed by `scieflow experiment`
on the host environment, not inside the stage container — the container
isolates the processing stage itself; metric library versions come from
the ScieFlow installation.
