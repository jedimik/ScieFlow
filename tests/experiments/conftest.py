import importlib.util

import pytest
import yaml

# The experiments module needs the `experiments` extra; skip its tests cleanly
# on machines that only installed other extras.
_MISSING = [m for m in ("numpy", "imageio", "skimage", "matplotlib")
            if importlib.util.find_spec(m) is None]
if _MISSING:
    collect_ignore_glob = ["test_*.py"]
else:
    import imageio.v3 as iio
    import numpy as np

RUN_PY = """\
import argparse
from pathlib import Path

import imageio.v3 as iio
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--gain", type=float, default=1.0)
    args = p.parse_args()
    img = np.asarray(iio.imread(args.input), dtype=np.float64)
    out = np.clip(img * args.gain, 0, 255).astype(np.uint8)
    iio.imwrite(str(Path(args.output) / "result.png"), out)


if __name__ == "__main__":
    main()
"""

STAGE_YAML = """\
name: scale
description: multiply image by gain
script: run.py
output_file: result.png
environment:
  pip: [numpy, imageio]
"""


@pytest.fixture
def project(tmp_path):
    """A minimal project: pipeline 'demo', stage 'scale', campaign 'gain-sweep'.

    gain=1.0 reproduces the reference exactly (ssim 1.0, passes validation);
    gain=0.5 does not (fails ssim >= 0.9).
    """
    stage_dir = tmp_path / "pipelines" / "demo" / "stages" / "scale"
    stage_dir.mkdir(parents=True)
    (stage_dir / "stage.yaml").write_text(STAGE_YAML)
    (stage_dir / "run.py").write_text(RUN_PY)

    data = tmp_path / "pipelines" / "demo" / "data"
    data.mkdir()
    rng = np.random.default_rng(0)
    truth = (rng.random((16, 16)) * 255).astype(np.uint8)
    iio.imwrite(data / "truth.png", truth)
    iio.imwrite(data / "noisy.png", truth)

    campaigns = tmp_path / "pipelines" / "demo" / "campaigns"
    campaigns.mkdir()
    (campaigns / "gain-sweep.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "gain-sweep",
                "pipeline": "demo",
                "stage": "scale",
                "input": "../data/noisy.png",
                "reference": "../data/truth.png",
                "parameters": {"gain": {"grid": [0.5, 1.0]}},
                "metrics": ["ssim", "mse"],
                "rank_by": "ssim",
                "validation": {"ssim": {"min": 0.9}},
            }
        )
    )
    return tmp_path
