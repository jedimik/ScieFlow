# Model Routing

Agent time is billed per token, and model prices differ by roughly 20x between
the cheapest and the most capable tiers. The ScieFlow experiments module routes each workflow phase
to the cheapest model tier that can do it well: judgment work runs on
high-quality models, mechanical work on the cheapest. The mapping lives in
`model-routing.yaml` at the repo root; the decision procedure agents follow is
`src/scieflow/experiments/skills/model-routing/SKILL.md`.

## Tiers

| Tier | Purpose | Example models (indicative $/MTok in/out) |
|---|---|---|
| `high` | Judgment — wrong answers are expensive | claude-opus-4-8 ($5/$25), gpt-5.2-codex ($1.75/$14), gemini-3.1-pro ($2/$12) |
| `standard` | Semi-mechanical — competence, not frontier judgment | claude-sonnet-5 ($3/$15), gemini-3-flash ($0.50/$3) |
| `cheap` | Mechanical, fully specified in advance | claude-haiku-4-5 ($1/$5), gemini-3.1-flash-lite ($0.25/$1.50) |

Prices carry an `as_of` date in the YAML and a refresh rule: re-verify when the
date is older than 3 months or before an unusually large campaign.

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

## Rules that keep it safe

- **Floor:** evaluation verdicts, literature claims, and anything the user
  approves stay on `high` — never silently produce judgment on a cheap model.
- **Escalation:** two cheap-tier failures → escalate one tier for the retry
  (fixed routing + failure escape hatch, not confidence cascades).
- **Cost visibility:** every campaign proposal carries a one-line cost estimate.
- **Reproducibility is unaffected:** recorded runs are deterministic `scieflow experiment`
  commands in containers; the tier only picks the agent driving them.

## Background

Per-phase (role-based) routing is the established pattern for agentic
workflows — frontier models for planning and judgment, cheap models for
mechanical steps — with reported cost reductions of 45–85% at ~95% quality
retention. Confidence-threshold cascades are avoided because self-reported
confidence is poorly calibrated. See e.g. "Explainable Model Routing for
Agentic Workflows" (arXiv:2604.03527) and current provider pricing pages.
