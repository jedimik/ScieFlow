from __future__ import annotations

from pathlib import Path

STAGE_YAML_TEMPLATE = """\
name: {name}
description: describe what this stage does
script: run.py
output_file: result.png
environment:
  # EITHER a conda env file:
  # conda: environment.yml
  # OR plain packages (conda not required):
  base_image: python:3.12-slim
  pip: [numpy, imageio]
  apt: []
"""

RUN_PY_TEMPLATE = '''\
"""Stage script contract: read --input, write <output_file> into --output.

Replace the passthrough below with real processing. Add one argparse
argument per stage parameter; `scieflow experiment` passes campaign params as --<name> <value>.
"""
import argparse
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--strength", type=float, default=1.0)
    args = parser.parse_args()
    shutil.copy(args.input, Path(args.output) / "result.png")


if __name__ == "__main__":
    main()
'''

CAMPAIGN_TEMPLATE = """\
name: {name}-example
pipeline: {name}
stage: process
input: ../data/input.png
reference: ../data/reference.png
parameters:
  strength: {{grid: [0.5, 1.0, 2.0]}}
metrics: [ssim, mse]
rank_by: ssim
validation:
  ssim: {{min: 0.5}}
notes: "Example campaign — replace input/reference and the parameter grid."
"""

README_TEMPLATE = """\
# Pipeline: {name}

- `stages/` — one directory per processing stage (`stage.yaml` + script).
- `campaigns/` — experiment definitions (committed to git).
- `data/` — pipeline input data (gitignored).
- `metrics/` — optional pipeline-local metrics (`METRICS = {{name: (fn, higher_is_better)}}`).

Run: `scieflow experiment env build stages/process && scieflow experiment sweep -c campaigns/example.yaml`
"""


def new_stage(pipeline_dir: str | Path, name: str) -> Path:
    stage_dir = Path(pipeline_dir) / "stages" / name
    if stage_dir.exists():
        raise FileExistsError(f"{stage_dir} already exists")
    stage_dir.mkdir(parents=True)
    (stage_dir / "stage.yaml").write_text(STAGE_YAML_TEMPLATE.format(name=name))
    (stage_dir / "run.py").write_text(RUN_PY_TEMPLATE)
    return stage_dir


def new_pipeline(pipelines_dir: str | Path, name: str) -> Path:
    pipeline_dir = Path(pipelines_dir) / name
    if pipeline_dir.exists():
        raise FileExistsError(f"{pipeline_dir} already exists")
    (pipeline_dir / "campaigns").mkdir(parents=True)
    (pipeline_dir / "data").mkdir()
    (pipeline_dir / "README.md").write_text(README_TEMPLATE.format(name=name))
    (pipeline_dir / "campaigns" / "example.yaml").write_text(
        CAMPAIGN_TEMPLATE.format(name=name)
    )
    new_stage(pipeline_dir, "process")
    return pipeline_dir
