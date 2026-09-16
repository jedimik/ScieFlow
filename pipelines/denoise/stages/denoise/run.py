"""Gaussian denoise stage: --input noisy image, writes denoised.png to --output."""
import argparse
from pathlib import Path

import imageio.v3 as iio
import numpy as np
from skimage.filters import gaussian


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sigma", type=float, default=1.0)
    args = parser.parse_args()

    img = np.asarray(iio.imread(args.input), dtype=np.float64)
    denoised = gaussian(img, sigma=args.sigma, preserve_range=True)
    out = Path(args.output) / "denoised.png"
    iio.imwrite(out, np.clip(denoised, 0, 255).astype(np.uint8))
    print(f"sigma={args.sigma} -> {out}")


if __name__ == "__main__":
    main()
