---
name: model-routing
description: Decide which model tier should run each workflow phase — judgment on high-quality models, mechanical work on the cheapest
---

# Model Routing

Different phases of the experiment workflow need very different model quality.
`model-routing.yaml` at the repo root is the source of truth for the phase→tier
map: three tiers (`high`, `standard`, `cheap`) and one delegation recipe. It
names no models and no prices. The model and effort for a phase come from the
role assignment, shown by `uv run scieflow agent show [--workspace <slug>]`.

## Decision procedure

1. **Identify the phase** of the work at hand in `phases` (e.g. writing a stage
   script = `stage-coding`; interpreting QC metrics = `evaluation`). Work that
   spans phases routes each part separately.
2. **Look up the tier.** `phases.<phase>` gives the tier; `tiers.<tier>.purpose`
   says what that tier is for. Your model and effort are the ones
   `scieflow agent show` assigns to the role you perform (AGENTS.md rule 13).
   Do not choose a model yourself.
3. **Match or delegate:**
   - Already running at (or near) the right tier → just do the work.
   - You are facing `cheap`/`standard` work that is self-contained and fully
     specified → delegate it with the `delegation` recipe:
     `uv run scieflow agent run --role <role> <agent> <prompt> <transcript>`.
     Do not call a model CLI directly; that bypasses the role check. Keep
     orchestration and review yourself.
   - You are a cheap model facing `high` work → **stop and tell the user**;
     never silently produce judgment output on a cheap model.
   - If the assigned agent cannot do the work, run it at the highest tier the
     work requires and say so.

## Effort line in campaign proposals

When proposing a campaign (experiment-designer skill), include a one-line
effort estimate so the user approves it with the plan. This repo carries no
prices, so state run count, tokens per run and tier:

    Est. effort: N runs x ~T tok/run on the <cheap> tier + design/eval on the <high> tier

## Escalation rule

If cheap-tier output fails validation or review **twice**, escalate one tier
for the retry instead of retrying at the same tier. Do not build
confidence-based cascades — fixed routing plus this failure escape hatch is
more predictable.

## Changing a model

The routing file has no dates or model ids, so it does not go stale. A change
of model is a change of role assignment: ask the user, then apply it with
`uv run scieflow agent configure` (AGENTS.md rule 13). Never edit the routing
file to change a model.

## Floor rules (never route down)

- Evaluation verdicts, literature claims, and anything the user will approve
  or rely on stay at `high`.
- Recorded-run mechanics are deterministic `scieflow experiment` commands — the tier only
  affects the agent driving them, never the reproducibility of results.
- When in doubt between two tiers, take the higher one and note the choice.
