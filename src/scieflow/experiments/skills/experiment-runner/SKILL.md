---
name: experiment-runner
description: Build stage container environments and execute approved campaigns via `scieflow experiment`
---

# Experiment Runner

## Precondition
The user has approved a campaign YAML. If not, stop — use
`skills/experiment-designer/SKILL.md` first.

## Workflow

1. **Build the environment** (once per stage, cached):
   `scieflow experiment env build pipelines/<p>/stages/<s>` — generates `env/<s>.def` from
   the stage's environment spec and builds `env/<s>.sif`. Use `--force` only
   after the environment spec changed.
2. **Verify inputs exist.** Check the campaign's `input`/`reference` paths.
   For the reference pipeline, `python pipelines/denoise/generate_data.py`
   creates the data.
3. **Run the campaign:** `scieflow experiment sweep -c pipelines/<p>/campaigns/<name>.yaml --experiments-dir workspace/<slug>/experiments`.
   Pass `--experiments-dir workspace/<slug>/experiments`; everything is recorded under
   `workspace/<slug>/experiments/<campaign-name>/`.
4. **Check for failures:** `results.json` lists per-run `status`. For a
   failed run read `workspace/<slug>/experiments/<c>/runs/<id>/logs/stderr.log`. A parameter
   combination that crashes the stage is a *finding* — keep it in the report
   and note the cause; do not silently drop or retry it.
5. Hand off to `skills/evaluator/SKILL.md`.

## Rules
- Recorded runs are containerized. `--direct` exists for developing a new
  stage script and for the test suite — never for results you will report.
- One-off probe of a single combination: `scieflow experiment run -c <campaign> -p k=v`.
- Do not edit anything under `workspace/<slug>/experiments/` by hand.
- Environment builds and sweep execution are `sweep-execution` (cheap-tier)
  phases — safe to delegate to a cheaper model per
  `skills/model-routing/SKILL.md`; evaluation of the results is not.
