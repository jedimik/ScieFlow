---
name: model-routing
description: Decide which model tier (and concrete model) should run each workflow phase — judgment on high-quality models, mechanical work on the cheapest
---

# Model Routing

Different phases of the experiment workflow need very different model quality,
and prices differ ~20x between tiers. `model-routing.yaml` at the repo root is
the source of truth: three tiers (`high`, `standard`, `cheap`), a phase→tier
map, indicative prices, and per-CLI delegation recipes.

## Decision procedure

1. **Identify the phase** of the work at hand in `phases` (e.g. writing a stage
   script = `stage-coding`; interpreting QC metrics = `evaluation`). Work that
   spans phases routes each part separately.
2. **Look up the tier and your model.** Find your CLI family (claude / codex /
   gemini / agy) under `tiers.<tier>.models`.
3. **Match or delegate:**
   - Already running at (or near) the right tier → just do the work.
   - You are a high-tier model facing `cheap`/`standard` work that is
     self-contained and fully specified → delegate it using the `delegation`
     recipe for a cheaper model (subagent with explicit model, or a one-shot
     CLI call). Keep orchestration and review yourself.
   - You are a cheap model facing `high` work → **stop and tell the user**;
     never silently produce judgment output on a cheap model.
   - If your CLI cannot switch or delegate models, run the session at the
     highest tier the work requires.

## Cost line in campaign proposals

When proposing a campaign (experiment-designer skill), include a one-line
estimate so the user approves cost along with the plan:

    Est. cost: N runs x ~T tok/run at <cheap model> (~$X) + design/eval on <high model> (~$Y)

Order-of-magnitude is fine; use the YAML prices.

## Escalation rule

If cheap-tier output fails validation or review **twice**, escalate one tier
for the retry instead of retrying at the same tier. Do not build
confidence-based cascades — fixed routing plus this failure escape hatch is
more predictable.

## Staleness rule

Check `as_of`. If older than 3 months — or the campaign is unusually large —
re-verify prices via provider pricing pages / web search, then update the
models, prices, and `as_of` together and commit the change.

## Floor rules (never route down)

- Evaluation verdicts, literature claims, and anything the user will approve
  or rely on stay at `high`.
- Recorded-run mechanics are deterministic `scieflow experiment` commands — the tier only
  affects the agent driving them, never the reproducibility of results.
- When in doubt between two tiers, take the higher one and note the choice.
