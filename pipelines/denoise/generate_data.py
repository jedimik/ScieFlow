"""Generate synthetic test data: a structured 2D image + a noisy copy."""
import argparse
from pathlib import Path

import imageio.v3 as iio
import numpy as np


def make_truth(size: int = 128) -> np.ndarray:
    y, x = np.mgrid[0:size, 0:size].astype(np.float64) / size
    img = 120 * np.sin(6 * np.pi * x) * np.cos(4 * np.pi * y) + 128
    img[size // 4 : size // 2, size // 4 : size // 2] = 220  # bright square
    yy, xx = y - 0.7, x - 0.7
    img[np.sqrt(yy**2 + xx**2) < 0.15] = 30  # dark disk
    return np.clip(img, 0, 255)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(Path(__file__).parent / "data"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--noise-sigma", type=float, default=25.0)
    parser.add_argument("--size", type=int, default=128)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = make_truth(args.size)
    rng = np.random.default_rng(args.seed)
    noisy = np.clip(truth + rng.normal(0, args.noise_sigma, truth.shape), 0, 255)
    iio.imwrite(out_dir / "truth.png", truth.astype(np.uint8))
    iio.imwrite(out_dir / "noisy.png", noisy.astype(np.uint8))
    print(f"wrote {out_dir}/truth.png and {out_dir}/noisy.png")


if __name__ == "__main__":
    main()
