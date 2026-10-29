"""Shared synthetic fixtures for the M-D2 t1 replicate tests (no patient data).

Loaded by the test modules through importlib (the tests run with
--import-mode=importlib, so they do not share a package). The seeded stage
adds seeded Gaussian noise (sigma 5) on top of the gain-scaled image, so the
output depends on `--seed` and on nothing else.
"""

from __future__ import annotations

import yaml

SEEDED_STAGE_YAML = """\
name: seeded
description: multiply image by gain and add seeded noise
script: run.py
output_file: result.png
accepts_seed: true
environment:
  pip: [numpy, imageio]
"""

SEEDED_RUN_PY = """\
import argparse
from pathlib import Path

import imageio.v3 as iio
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()
    img = np.asarray(iio.imread(args.input), dtype=np.float64)
    rng = np.random.default_rng(args.seed)
    noise = rng.normal(0.0, 5.0, size=img.shape)
    out = np.clip(img * args.gain + noise, 0, 255).astype(np.uint8)
    iio.imwrite(str(Path(args.output) / "result.png"), out)


if __name__ == "__main__":
    main()
"""


def add_seeded_stage(root):
    """Write pipelines/demo/stages/seeded next to the `project` fixture's data."""
    stage_dir = root / "pipelines" / "demo" / "stages" / "seeded"
    stage_dir.mkdir(parents=True)
    (stage_dir / "stage.yaml").write_text(SEEDED_STAGE_YAML)
    (stage_dir / "run.py").write_text(SEEDED_RUN_PY)
    return stage_dir


def campaign_dict(name="rep-sweep", stage="seeded", parameters=None, **extra):
    data = {
        "name": name,
        "pipeline": "demo",
        "stage": stage,
        "input": "../data/noisy.png",
        "reference": "../data/truth.png",
        "parameters": parameters if parameters is not None
        else {"gain": {"grid": [0.5, 1.0]}},
        "metrics": ["ssim", "mse"],
        "rank_by": "ssim",
    }
    data.update(extra)
    return data


def write_campaign(root, data):
    campaigns = root / "pipelines" / "demo" / "campaigns"
    campaigns.mkdir(parents=True, exist_ok=True)
    path = campaigns / f"{data['name']}.yaml"
    path.write_text(yaml.safe_dump(data))
    return path
