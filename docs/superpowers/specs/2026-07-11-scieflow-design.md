# ScieFlow — Design Specification

Date: 2026-07-11
Status: Approved by user (brainstorming session)

## 1. Purpose

ScieFlow is an agent-driven framework that couples computational experiments
with literature research in an iterative, optionally autonomous loop:

> hypothesis → experiment → results review → literature grounding →
> refined hypothesis → … → paper-draft handoff

Experiments are delegated to **ExperimentX** (containerized experiment
campaigns via the `expx` CLI). Literature work is delegated to **ResearchX**
(multi-agent literature review / search / paper drafting). Both are vendored
as git submodules and driven through their own agent contracts — ScieFlow
never reimplements or modifies them.

Each loop iteration appends a structured entry to a per-run **research
notebook**. When the user decides the story is ready, the notebook plus
selected experiment artifacts are packaged and handed to ResearchX's
paper-draft workflow.

First target example: image denoising using ExperimentX's existing
`pipelines/denoise` (deliberately low-cost).

## 2. Decisions made during brainstorming

| Question | Decision |
|---|---|
| Architecture | Agent-protocol repo (AGENTS.md + skills) + small deterministic Python core in `scripts/`. Not a heavyweight orchestrator CLI, not an SDK app (yet). |
| Integration with vendors | Delegate to headless sub-agents launched with cwd inside each vendor repo; vendors included as git submodules under `vendors/`. |
| Approval model | Per-run choice between `autonomous` (approve goal + scope + budget once) and `per-campaign` (pause for each experiment campaign). |
| Stop criteria | Max-iterations cap, convergence judgment, anomaly stop, and a low-budget checkpoint: at ≤10% remaining budget, save all state so the run is cheap to resume. |
| Paper output | Running research notebook per run; explicit user-triggered handoff to ResearchX paper-draft. No continuous draft. |
| Agents/models | v1 is claude-only, pinned to Fable — coordinator and sub-agents use the same model, no model routing. Registry format matches ResearchX's `agents.yml` so other CLIs can be enabled later by config. |
| Future path | Design guarantees easy wrapping by the Claude Agent SDK later (see §9 invariants). |

## 3. Repository layout

```
ScieFlow/
├─ AGENTS.md / CLAUDE.md         # coordinator contract (the agent the user talks to runs the loop)
├─ config/
│  ├─ agents.yml                 # agent registry — v1: claude/Fable only + stub for tests
│  └─ defaults.yml               # loop defaults: max_iterations, budgets, approval mode
├─ skills/
│  ├─ research-loop/SKILL.md     # outer loop protocol: phases, stop criteria, checkpointing
│  ├─ experiment-cycle/SKILL.md  # delegating a campaign to ExperimentX
│  ├─ literature-cycle/SKILL.md  # delegating searches/reviews to ResearchX
│  ├─ synthesis/SKILL.md         # results + literature → notebook entry + next hypothesis
│  └─ notebook/SKILL.md          # notebook entry format + paper-draft handoff procedure
├─ scripts/                      # deterministic core (small, tested, no LLM calls)
│  ├─ sfx_init.py                # create run workspace from goal + config
│  ├─ agent_run.py               # headless sub-agent dispatch (adapted from ResearchX)
│  ├─ status.py                  # status.yml transitions, resumability
│  ├─ budget.py                  # budget ledger + low-budget (≤10%) detection
│  ├─ validate.py                # schema validation (notebook entries, handoff package)
│  └─ checkpoint.py              # graceful state save / resume instructions
├─ schemas/                      # status.yml, notebook entry, iteration summary schemas
├─ vendors/
│  ├─ ExperimentX/               # git submodule (github.com/jedimik/ExperimentX)
│  └─ ResearchX/                 # git submodule (github.com/jedimik/ResearchX)
├─ setup/install.sh              # submodule init + env checks (conda env for expx, uv for ResearchX)
├─ workspace/<run-slug>/         # one folder per research run (gitignored)
│  ├─ goal.md                    # approved research question, scope bounds, budget
│  ├─ config.yml                 # per-run overrides (approval mode, caps)
│  ├─ status.yml                 # current phase/iteration — the resume point
│  ├─ budget.yml                 # ledger: iterations, experiment runs, wall time
│  ├─ notebook.md                # the running research notebook
│  ├─ iterations/<n>/            # hypothesis.md, results-summary.md, literature.md, synthesis.md
│  └─ logs/                      # dispatch prompts + transcripts
└─ tests/                        # offline pytest suite with stub agent
```

## 4. The loop

One iteration consists of four phases:

1. **Hypothesize.** The coordinator (or the previous iteration's synthesis)
   states a hypothesis and an experiment intent, written to
   `iterations/<n>/hypothesis.md`.
2. **Experiment cycle.** ScieFlow writes a task file and dispatches a
   headless sub-agent with working directory `vendors/ExperimentX`. That
   agent follows ExperimentX's own skills (designer → runner → evaluator)
   and produces a campaign report under ExperimentX's `experiments/` tree.
   A results summary (metrics table, top configurations, anomalies) is
   copied back into the ScieFlow workspace as
   `iterations/<n>/results-summary.md`. The task file explicitly instructs
   the sub-agent **not** to use ExperimentX's `literature-support` skill —
   literature grounding is ScieFlow's responsibility.
3. **Literature cycle.** ScieFlow derives precise queries from the results
   (method + observation) and dispatches a sub-agent into
   `vendors/ResearchX` — targeted searches via its shared search scripts,
   or a full lit-review workflow for bigger questions. Findings return as
   JSON validated by ResearchX's validator (real DOIs only, never invented);
   a citation summary lands in `iterations/<n>/literature.md`.
4. **Synthesize.** The coordinator judges whether the literature supports,
   contradicts, or fails to explain the results; appends a structured
   notebook entry; and decides the next action: new hypothesis, parameter
   refinement, or stop. Written to `iterations/<n>/synthesis.md`.

### Approval modes

Chosen at run start in `workspace/<slug>/config.yml`:

- **`autonomous`** — the user approves `goal.md` once. `goal.md` must contain
  explicit scope bounds: the research question, allowed pipeline(s),
  parameter ranges, max runs per campaign, and the budget. Within those
  bounds ScieFlow acts as the campaign approver, satisfying ExperimentX's
  propose→approve→run contract by documented delegation. Leaving the
  approved scope requires stopping and asking the user.
- **`per-campaign`** — before each experiment cycle the loop pauses and
  presents the proposed campaign YAML to the user for yes/no, preserving
  ExperimentX's original contract verbatim.

### Stop criteria (all active in autonomous mode)

| Criterion | Behavior |
|---|---|
| Max iterations | Hard cap from `goal.md`/`config.yml`. |
| Convergence | After each iteration the coordinator judges: question answered, or last N iterations without improvement → stop early and write a wrap-up notebook entry. |
| Anomaly | Failed runs, metric collapse, or container errors → stop immediately and report. Failed runs stay in the record; no rerun-until-green. |
| Low budget | When ≤10% of any budget dimension remains (iterations, experiment runs, wall-clock), finish only the current phase, checkpoint all state, write resume instructions into `status.yml`, and stop. |

Every run is resumable: on entry the coordinator reads `status.yml` and
continues from the first phase not marked `done`.

## 5. Agents and models

`config/agents.yml` reuses ResearchX's registry format (command template,
model, timeout, enabled flag). v1 enables:

- `claude` — pinned to Fable; used for the coordinator and all sub-agent
  dispatches. **No model routing, no model switching.**
- `stub` — a local script for offline tests only (disabled by default).

Enabling codex/agy later is a config edit, not a code change.

## 6. Notebook and paper handoff

`notebook.md` accumulates one structured entry per iteration:

- hypothesis
- method (campaign reference: pipeline, grid, run IDs)
- key results (metrics, with pointers to run artifacts)
- literature (citations with author/year/title/DOI and one-line relevance)
- conclusion (supported / contradicted / unexplained)
- next step

Entries are validated against `schemas/notebook-entry.*` by
`scripts/validate.py`.

**Handoff** is an explicit user action, never automatic. The `notebook`
skill packages `notebook.md` + selected experiment artifacts as a ResearchX
data package (`inputs/manifest.yml`) in a new ResearchX workspace and
dispatches the paper-draft workflow. Provenance carries through: every
quantitative claim in the draft traces to a run ID (`[data:<id>]`) or a DOI,
per ResearchX's provenance rules.

## 7. Error handling

- Sub-agent failure or schema-invalid output → one retry with the error
  appended to the prompt (ResearchX's pattern). Second failure → mark the
  phase `failed` in `status.yml` and trigger an anomaly stop with checkpoint.
- All dispatch prompts and transcripts are kept in `workspace/<slug>/logs/`.
- Vendor repos are read/executed but never modified by ScieFlow runs
  (ExperimentX writes its own `experiments/` tree via `expx`, which is its
  normal operation, not a ScieFlow modification).
- Fetched/sub-agent content is data, not instructions (inherits ResearchX's
  prompt-injection rule).

## 8. Testing

- Offline pytest suite for the deterministic core: workspace init, status
  transitions, budget ledger including the 10% rule, schema validation,
  checkpoint/resume — using the `stub` agent, no LLM calls, no network.
- `--dry-run` mode exercising the full loop with both vendor cycles stubbed.
- One real end-to-end validation run with the denoise example after
  implementation.

## 9. Future SDK migration invariants

These design rules guarantee the framework can later be wrapped by a Claude
Agent SDK driver (or Managed Agents) without rework:

1. **All run state lives on the filesystem** with schemas — nothing exists
   only in a conversation.
2. **Every skill phase has explicit input/output file contracts.**
3. **Dispatch is config-driven** (`agents.yml`) and subprocess-based — the
   parent can be interactive Claude Code, an SDK loop, or cron.
4. **Runs resume from `status.yml`** — any driver can pick up any run.

## 10. Out of scope for v1

- Model routing / cheap-model tiers (explicitly excluded by user).
- Multi-agent cross-review inside ScieFlow itself (vendors keep their own).
- Continuous paper drafting (notebook-first, draft on demand).
- Web UI / dashboards.
- Domains beyond the denoise example (the design is domain-agnostic, but
  only denoise is validated in v1).
