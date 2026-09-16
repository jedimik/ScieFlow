import imageio.v3 as iio
import numpy as np
from skimage.metrics import (
    mean_squared_error,
    peak_signal_noise_ratio,
    structural_similarity,
)

from . import register


def _load(path):
    img = np.asarray(iio.imread(path), dtype=np.float64)
    if img.ndim == 3:
        img = img[..., :3].mean(axis=2)
    return img


def mse(output, reference):
    return mean_squared_error(_load(reference), _load(output))


def psnr(output, reference):
    return peak_signal_noise_ratio(_load(reference), _load(output), data_range=255)


def ssim(output, reference):
    return structural_similarity(_load(reference), _load(output), data_range=255)


register("mse", mse, higher_is_better=False)
register("psnr", psnr, higher_is_better=True)
register("ssim", ssim, higher_is_better=True)
