# ScieFlow

Agent-driven research loop: computational experiments (via
[ExperimentX](vendors/ExperimentX)) hand in hand with literature research
(via [ResearchX](vendors/ResearchX)). Each iteration runs
hypothesis → experiment → literature grounding → synthesis, accumulating a
research notebook that can be handed to ResearchX's paper-draft workflow.

## Quick start

```bash
git clone --recurse-submodules git@github.com:jedimik/ScieFlow.git
cd ScieFlow && setup/install.sh
```

Then ask your agent (e.g. `claude`) to start a research run — it reads
`AGENTS.md` and follows `skills/research-loop/SKILL.md`. Choose the
approval mode per run: `per-campaign` (you approve every experiment
campaign) or `autonomous` (you approve the goal + scope + budget once).

## Layout

- `AGENTS.md` — coordinator contract (read this first)
- `skills/` — loop protocols (research-loop, experiment-cycle,
  literature-cycle, synthesis, notebook)
- `scripts/` — deterministic core (workspace init, dispatch, status,
  budget, validation, checkpoint)
- `config/` — agent registry (Fable-only in v1) + loop defaults
- `vendors/` — ExperimentX and ResearchX submodules
- `workspace/` — one folder per research run (gitignored)

## Development

```bash
uv run pytest -q        # offline test suite (stub agent, no LLM calls)
```

Design spec: `docs/superpowers/specs/2026-07-11-scieflow-design.md`.
