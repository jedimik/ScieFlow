---
name: experiment-designer
description: Design an experiment campaign (parameter grid/scenarios, metrics, validation) and get user approval before anything runs
---

# Experiment Designer

## When to use
The user wants to explore how a processing stage behaves under different
parameters, or asks "what should we try next?" after a previous campaign.

## Workflow

1. **Understand the goal.** Ask (briefly) what is being optimized, what data
   is at hand, and what "good" means. If a previous campaign exists, read its
   `report.md` and `comparison.json` first.
2. **Inspect the stage.** Read `pipelines/<p>/stages/<s>/stage.yaml` for the
   available parameters and the script for their actual meaning. Never sweep
   a parameter you cannot explain.
3. **Choose the design:**
   - *Grid* (`parameters:`) when exploring 1–2 numeric parameters. Start
     coarse (5–8 points spanning the plausible range), refine around the
     optimum in a follow-up campaign. Keep total runs ≤ ~30 per campaign.
   - *Scenarios* (`scenarios:`) for qualitatively different configurations.
4. **Pick metrics and validation.** With ground truth: `ssim`, `psnr`, `mse`
   for images. Set `rank_by` to the metric that matches the goal, and
   `validation` thresholds for minimum acceptability. No reference data means
   no metrics — flag this to the user instead of inventing numbers.
5. **Write the campaign YAML** into `pipelines/<p>/campaigns/<name>.yaml`.
   Fill `notes:` with the hypothesis — future agents rely on it.
6. **Present for approval:** the YAML, why this grid/these scenarios, what
   you expect, the run count, and a one-line cost estimate per
   `skills/model-routing/SKILL.md` (runs x tokens at the execution tier +
   judgment phases at high tier). **Wait for explicit approval.**
7. After approval, hand off to `skills/experiment-runner/SKILL.md`.

## Campaign YAML schema

```yaml
name: <unique-campaign-name>       # required
pipeline: <pipeline>               # required
stage: <stage>                     # required
input: <path relative to this file>        # required
reference: <path relative to this file>    # optional ground truth
parameters:                        # XOR with scenarios
  <param>: {grid: [v1, v2, ...]}   # swept
  <param>: {value: v}              # fixed
scenarios:                         # XOR with parameters
  - {name: <label>, <param>: v, ...}
metrics: [ssim, psnr, mse]         # required
rank_by: ssim                      # default: first metric
validation:
  <metric>: {min: x}               # and/or {max: x}
notes: "hypothesis and reasoning"  # required by convention
```
