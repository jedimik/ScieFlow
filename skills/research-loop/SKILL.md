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
3. Confirm the workspace with the user, including who does the work: show
   `uv run scieflow agent show --workspace <slug>` and open a `staffing` gate
   (`uv run scieflow gate open <slug> --kind staffing --question "…"`) asking
   whether any role, model or effort should differ for this run. Apply their
   answer with `scieflow agent configure --workspace <slug> … --yes`
   (AGENTS.md rule 13; `--assign ROLE=AGENT@MODEL/EFFORT` for a role-only
   choice). Then begin iteration 1.

## Resuming a run

`uv run scieflow run show <slug>`. If `stopped` is set, follow its `resume`
text, then clear the block with `uv run scieflow run resume <slug> --as-agent` —
phases already `done` stay done. Do not resume past a terminal stop
(`max-iterations`, `converged`) without the user asking for it. Otherwise
continue at the first phase not `done`.

## One iteration (phases in order)

For each phase: `uv run scieflow run mark <slug> <phase> running --as-agent`,
do the work, write the phase artifact to
`workspace/<slug>/iterations/<n>/`, then mark it `done` the same way.

| Phase | Artifact | How |
|---|---|---|
| hypothesize | `hypothesis.md` | You write it: hypothesis + experiment intent, grounded in goal.md and the previous synthesis. |
| experiment | `results-summary.md` | `skills/experiment-cycle/SKILL.md` |
| literature | `literature.md` | `skills/literature-cycle/SKILL.md` |
| synthesize | `synthesis.md` + notebook entry | `skills/synthesis/SKILL.md` |

After each phase:

1. Spend records itself: dispatch wall time is accumulated by the job runner
   and experiment runs are counted by `scieflow experiment run|sweep`. Record
   anything they cannot see with `uv run scieflow run spend <slug>
   --experiment-runs K`. Never hand-edit `budget.yml`.
2. Check budgets with `uv run scieflow run show <slug>`: if any dimension is
   at or below the run's `low_budget_threshold` (default 0.10 — ≤10%
   remaining), finish ONLY the current phase, then
   `uv run scieflow run checkpoint <slug> --reason low-budget \
      --detail "<which dimension>" --as-agent` and report to the user.
   Exhausted dimensions are refused in code — a dispatch exits 75, a sweep is
   refused, and the run is checkpointed for you.

After `synthesize`, apply the stop criteria in this order — anomaly,
max-iterations, converged, low-budget — with `uv run scieflow run checkpoint
<slug> --reason <reason> --as-agent`. If none apply, `uv run scieflow run
advance <slug> --as-agent` (refused, with a checkpoint, once `iterations` is
spent) and continue.

## Approval gates

Approvals are gates (AGENTS.md rule 14): open one with `uv run scieflow gate
open <slug> --kind <kind> --question "…" [--option …] [--in-scope]` and block
on `uv run scieflow gate wait <slug> <id>`.

- `per-campaign`: the experiment-cycle skill opens a `campaign-approval` gate
  for each campaign YAML and waits. Do not proceed without an answer.
- `autonomous`: never exceed goal.md bounds. Inside them you may answer your
  own in-scope gates with `--as-agent --rationale "…"`; kinds marked
  `requires_human` in `schemas/gates.yml` still wait for the user. If the next
  logical experiment falls outside the bounds, open a `scope-change` gate (it
  requires a human) or checkpoint with `--reason user` and ask.
