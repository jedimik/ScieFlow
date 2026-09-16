import numpy as np
import imageio.v3 as iio
import pytest

from scieflow.experiments.metrics import REGISTRY, compute_metrics, get_metric, load_local_metrics


@pytest.fixture
def images(tmp_path):
    rng = np.random.default_rng(0)
    truth = (rng.random((32, 32)) * 255).astype(np.uint8)
    noisy = np.clip(
        truth.astype(float) + rng.normal(0, 20, truth.shape), 0, 255
    ).astype(np.uint8)
    t, n = tmp_path / "truth.png", tmp_path / "noisy.png"
    iio.imwrite(t, truth)
    iio.imwrite(n, noisy)
    return n, t


def test_builtins_registered():
    assert get_metric("ssim").higher_is_better is True
    assert get_metric("mse").higher_is_better is False
    assert get_metric("psnr").higher_is_better is True


def test_unknown_metric_lists_available():
    with pytest.raises(KeyError, match="ssim"):
        get_metric("nope")


def test_identical_images_are_perfect(tmp_path, images):
    _, truth = images
    m = compute_metrics(["ssim", "mse"], truth, truth)
    assert m["ssim"] == pytest.approx(1.0)
    assert m["mse"] == pytest.approx(0.0)


def test_noisy_image_scores_worse(images):
    noisy, truth = images
    m = compute_metrics(["ssim", "mse", "psnr"], noisy, truth)
    assert m["ssim"] < 1.0
    assert m["mse"] > 0.0


def test_rgba_alpha_excluded_from_grayscale(tmp_path):
    rgb = np.full((32, 32, 3), 128, dtype=np.uint8)
    rgba = np.dstack([rgb, np.full((32, 32), 255, dtype=np.uint8)])
    rgba[10:20, 10:20, 3] = 0  # patch of full transparency

    rgb_path = tmp_path / "rgb.png"
    rgba_path = tmp_path / "rgba.png"
    iio.imwrite(rgb_path, rgb)
    iio.imwrite(rgba_path, rgba)

    m = compute_metrics(["mse"], rgba_path, rgb_path)
    assert m["mse"] == pytest.approx(0.0)


def test_load_local_metrics(tmp_path):
    (tmp_path / "custom.py").write_text(
        "def zero(output, reference):\n"
        "    return 0.0\n"
        "METRICS = {'always_zero': (zero, False)}\n"
    )
    load_local_metrics(tmp_path)
    assert "always_zero" in REGISTRY
    assert get_metric("always_zero").fn(None, None) == 0.0
