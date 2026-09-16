# Pipeline: denoise (reference example)

Gaussian denoising of a synthetic noisy image — the framework's reference
pipeline. End-to-end:

    python pipelines/denoise/generate_data.py       # data/truth.png, data/noisy.png
    scieflow experiment env build pipelines/denoise/stages/denoise # build the container
    scieflow experiment sweep -c pipelines/denoise/campaigns/sigma-sweep.yaml
    scieflow experiment report experiments/denoise-sigma-sweep

Layout: `stages/denoise/` (stage.yaml + run.py), `campaigns/` (committed
experiment definitions), `data/` (generated, gitignored).
