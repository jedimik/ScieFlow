---
name: research-loop
description: Run or resume a ScieFlow research run — the outer experiment↔literature loop
---

# Research Loop

## Starting a run

1. Draft `goal.md` with the user: research question, pipeline(s), parameter
   bounds, max runs per campaign, budgets, approval mode. In `autonomous`
   mode these bounds ARE the delegation — be precise.
2. `uv run scripts/sfx_init.py <slug> --goal <goal.md> --approval <mode> \
      [--max-iterations N] [--max-experiment-runs N] [--max-wall-minutes N]`
3. Confirm the workspace with the user, then begin iteration 1.

## Resuming a run

Read `workspace/<slug>/status.yml`. If `stopped` is set, follow its `resume`
text (clear `stopped` by continuing normally — phases already `done` stay
done). Otherwise continue at the first phase not `done`.

## One iteration (phases in order)

For each phase: mark it `running` in status.yml, do the work, write the
phase artifact to `workspace/<slug>/iterations/<n>/`, mark it `done`.

| Phase | Artifact | How |
|---|---|---|
| hypothesize | `hypothesis.md` | You write it: hypothesis + experiment intent, grounded in goal.md and the previous synthesis. |
| experiment | `results-summary.md` | `skills/experiment-cycle/SKILL.md` |
| literature | `literature.md` | `skills/literature-cycle/SKILL.md` |
| synthesize | `synthesis.md` + notebook entry | `skills/synthesis/SKILL.md` |

After each phase:

1. Update wall-clock spend and record any experiment runs:
   the coordinator edits `budget.yml` via python:
   `record(b, experiment_runs=K)` and `set_wall_from_clock(b)` (see
   `scripts/budget.py`), or equivalently rewrites the file with the
   incremented `spent` values.
2. Check budgets: if any dimension is in `low_dimensions(b)` (≤10%
   remaining), finish ONLY the current phase, then
   `uv run scripts/checkpoint.py workspace/<slug> --reason low-budget \
      --detail "<which dimension>"` and report to the user.

After `synthesize`: record `iterations: +1` in the budget, then apply stop
criteria in this order — anomaly, max-iterations, converged, low-budget —
using `checkpoint.py` with the matching reason. If none apply,
advance the iteration (`status.advance_iteration`) and continue.

## Approval gates

- `per-campaign`: the experiment-cycle skill pauses for user approval of
  each campaign YAML. Do not proceed without it.
- `autonomous`: never exceed goal.md bounds. If the next logical experiment
  falls outside them, checkpoint with `--reason user` and ask.
