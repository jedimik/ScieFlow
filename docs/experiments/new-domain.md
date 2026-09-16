# Adapting to a New Domain

The core is data-type agnostic: campaigns, runs, sweeps, ranking, validation
and reports never assume images. Only the built-in metrics (`ssim`, `psnr`,
`mse`) are image-specific. Adapting to a new domain — 1D signals, volumes,
point clouds, text — means one pipeline + its metrics.

## 1. New pipeline

```bash
scieflow experiment new pipeline ecg-filtering
```

## 2. Stage I/O in your domain's format

The framework passes file paths; the stage decides the format. A 1D signal
stage can read/write `.npy`:

```python
signal = np.load(args.input)
filtered = my_filter(signal, cutoff=args.cutoff)
np.save(Path(args.output) / "filtered.npy", filtered)
```

Set `output_file: filtered.npy` in `stage.yaml`.

## 3. Domain metrics

Create `pipelines/ecg-filtering/metrics/signal.py`:

```python
import numpy as np


def snr_db(output_path, reference_path) -> float:
    out, ref = np.load(output_path), np.load(reference_path)
    noise = out - ref
    return float(10 * np.log10(np.sum(ref**2) / np.sum(noise**2)))


METRICS = {"snr_db": (snr_db, True)}  # (fn, higher_is_better)
```

Pipeline-local metrics are auto-loaded for that pipeline's runs. Use them in
campaigns: `metrics: [snr_db]`.

Metrics useful across many pipelines belong in `src/scieflow/experiments/metrics/` with
tests — that is the only case where extending the core is expected.

## 4. Everything else is unchanged

Campaign schema, approval workflow, `scieflow experiment sweep/compare/report`, run
records, and the agent skills all work as-is. The agent skills reference no
image-specific behavior except the built-in metric list.
