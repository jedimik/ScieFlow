# Writing a New Stage

A stage is the unit you add when you want a new processing step.

## 1. Scaffold

```bash
scieflow experiment new stage sharpen --pipeline denoise
```

Creates `pipelines/denoise/stages/sharpen/` with a loadable `stage.yaml`
and a passthrough `run.py`.

## 2. Declare the environment

In `stage.yaml`, either plain packages (no conda needed):

```yaml
environment:
  base_image: python:3.12-slim
  pip: [numpy, scikit-image, imageio]
  apt: []
```

or a conda environment (put `environment.yml` next to `stage.yaml`):

```yaml
environment:
  conda: environment.yml
```

## 3. Implement the script

Contract: `python run.py --input <file> --output <dir> --<param> <value> ...`

- one argparse argument per stage parameter,
- write `output_file` (from stage.yaml) into `--output`,
- exit non-zero on failure — the runner records a failed run with logs.

## 4. Build and probe

```bash
scieflow experiment env build pipelines/denoise/stages/sharpen
scieflow experiment run -c pipelines/denoise/campaigns/<campaign>.yaml -p amount=0.5
```

Iterate with `--direct` while developing; switch to containerized runs for
anything you record.

## 5. Write a campaign

Add `pipelines/denoise/campaigns/<name>.yaml` (schema in
[Concepts](concepts.md)) and run `scieflow experiment sweep`.
