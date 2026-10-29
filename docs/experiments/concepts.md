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
accepts_seed: false           # true when the script takes --seed (replicates need it)
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

Two optional keys add analysis (see [Analysis and Statistics](analysis.md)):

- `replicates: 5` (or `{n: 5, seed: 1000}`) repeats each configuration with
  seeds. The stage must set `accepts_seed: true`.
- `analysis:` names the baseline, the test per metric, the correction, alpha,
  the confidence level and the bootstrap settings.

## Run

One stage execution with one parameter set:

```
workspace/<slug>/experiments/<campaign>/
├── campaign.yaml       # resolved copy (absolute paths)
├── results.json        # all runs: params, status, metrics
├── results.csv         # one row per run and metric (written by sweep and stats)
├── comparison.json     # ranking + validation (scieflow experiment compare)
├── stats.json          # descriptives, tests, families (scieflow experiment stats)
├── report.md, plots/   # scieflow experiment report
├── tables/             # summary and tests (scieflow experiment tables)
├── figures/            # metrics figure and its sidecar (scieflow experiment figures)
├── packages/           # captured package lists of container runs, by sif sha256
└── runs/<run-id>/
    ├── config.yaml         # resolved params, paths, status, timestamps
    ├── logs/               # stdout.log, stderr.log
    ├── artifacts/          # stage outputs
    ├── metrics.json        # computed metrics
    ├── manifest.json       # sha256 of artifacts and metrics, git state, data level
    └── environment.json    # mode, exact command, sif sha256
```

`stats.json` and the tables and figures derived from it are regenerated;
`verify` re-checks the recorded hashes of the manifests.

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
