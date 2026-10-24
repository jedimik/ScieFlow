# Model Routing

The ScieFlow experiments module routes each workflow phase to the tier that can
do it well: judgment work runs on high-quality models, mechanical work on the
cheapest. The phase→tier map lives in `model-routing.yaml` at the repo root; the
decision procedure agents follow is
`src/scieflow/experiments/skills/model-routing/SKILL.md`.

The routing file names no models and no prices. The model and effort for each
role come from the role assignment: `uv run scieflow agent show [--workspace <slug>]`.

## Tiers

| Tier | Purpose |
|---|---|
| `high` | Judgment — wrong answers are expensive |
| `standard` | Semi-mechanical — competence, not frontier judgment |
| `cheap` | Mechanical, fully specified in advance |

## Phase map

| Phase | Tier | Why |
|---|---|---|
| experiment-design | high | Grid choice and hypotheses shape everything downstream |
| evaluation | high | Metric interpretation and validation verdicts are judgment |
| literature | high | Wrong citations are worse than none |
| report-writing | high | The report is what the user relies on |
| campaign-drafting | standard | YAML from an approved plan — structured but not trivial |
| run-debugging | standard | Log reading needs competence, not frontier reasoning |
| stage-coding | cheap | Implementing a script from a clear spec |
| sweep-execution | cheap | Driving deterministic `scieflow experiment` commands |
| scaffolding | cheap | `scieflow experiment new` template fill-in |
| data-generation | cheap | Running/adjusting generator scripts |

## Delegation

A cheaper-tier piece of work is delegated through the role-bound command, never
a direct model CLI call:

    uv run scieflow agent run --role <role> <agent> <prompt> <transcript>

## Rules that keep it safe

- **Floor:** evaluation verdicts, literature claims, and anything the user
  approves stay on `high` — never silently produce judgment on a cheap model.
- **Escalation:** two cheap-tier failures → escalate one tier for the retry
  (fixed routing + failure escape hatch, not confidence cascades).
- **Effort visibility:** every campaign proposal carries a one-line effort
  estimate (runs, tokens per run, tier).
- **Reproducibility is unaffected:** recorded runs are deterministic `scieflow experiment`
  commands in containers; the tier only picks the agent driving them.

## Background

Per-phase (role-based) routing is the established pattern for agentic
workflows — frontier models for planning and judgment, cheap models for
mechanical steps. Confidence-threshold cascades are avoided because
self-reported confidence is poorly calibrated. See e.g. "Explainable Model
Routing for Agentic Workflows" (arXiv:2604.03527).
