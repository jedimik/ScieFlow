---
name: evaluator
description: QC methodology — compute metrics, validate results, spot suspicious outcomes, write the campaign report
---

# Evaluator

## Workflow

1. `scieflow experiment compare workspace/<slug>/experiments/<campaign>` — ranking + validation per run.
2. `scieflow experiment report workspace/<slug>/experiments/<campaign>` — generates `report.md` and QC plots.
3. **Look at the artifacts, not just the numbers.** Open the best run's
   output (`workspace/<slug>/experiments/<c>/runs/<id>/artifacts/`) and compare it visually
   against the input and reference. Metrics can be gamed by degenerate
   outputs (e.g., over-smoothing scores well on MSE while destroying detail).
4. **Sanity checks before trusting a result:**
   - Does the metric curve have a plausible shape (e.g., a single optimum
     inside the swept range)? An optimum at the grid edge means the range
     was too narrow — recommend extending it.
   - Are best and worst runs *meaningfully* different, or is the metric flat
     (parameter may not matter)?
   - Any failed runs? Explain them in the report.
5. **Write your evaluation** into the campaign report (append a
   `## Evaluation` section to `report.md`): what was found, whether
   validation passed, what you recommend next (finer grid, different
   parameter, different stage). Recommendations go back through
   `skills/experiment-designer/SKILL.md` — a new campaign needs new approval.

## Metric semantics (built-in)
- `ssim` (higher better): structural similarity to reference — perceptual.
- `psnr` (higher better): log-scale pixel fidelity.
- `mse` (lower better): raw pixel error — sensitive to intensity shifts.

Custom metrics: see `skills/framework-extender/SKILL.md`.
