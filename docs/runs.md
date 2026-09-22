# Runs, jobs and gates

A **run** is one piece of research with its own directory under
`workspace/<slug>/`: a goal, a status, a budget, an append-only history, the
jobs it started, the approvals it asked for, and everything it produced. The
research loop, a paper draft and a literature review are all runs.

Everything on this page is a thin caller of one service layer
(`scieflow.core.service`), so the CLI, the coordinator agent and the coming
local web app act on runs the same way and see the same state.

## The state of a run

```bash
uv run scieflow run list                 # every run: kind, phase, state
uv run scieflow run show <slug>          # status, budget and what to do next
uv run scieflow run show <slug> --json   # the same, for tools
```

`status.yml` is the snapshot: the run's ULID `id`, its iteration, each
phase's state, and a `stopped` block when it checkpointed. Agents never edit
it by hand — they call:

```bash
uv run scieflow run mark <slug> <phase> running|done|failed --as-agent
uv run scieflow run advance <slug> --as-agent          # next iteration
uv run scieflow run checkpoint <slug> --reason low-budget --detail "wall time"
uv run scieflow run resume <slug>                      # clear a stop
uv run scieflow run spend <slug> --experiment-runs 4   # spend the runner can't see
```

Every one of those writes under a lock, validates against `schemas/status.yml`
and records an event.

## History: the event log

`workspace/<slug>/events.jsonl` is the run's history — one JSON object per
line, `{id, ts, run, slug, type, actor, data}`:

```bash
uv run scieflow run events <slug>              # what happened, in order
uv run scieflow run events <slug> --follow     # tail it live
uv run scieflow run events <slug> --type job.finished --json
uv run scieflow run log <slug> note.idea --message "try sigma 2"
```

Types cover the lifecycle (`run.created`, `phase.*`, `iteration.advanced`,
`checkpoint`), jobs (`job.queued|started|finished|failed|timeout|cancelled|lost|refused`),
gates (`gate.opened|answered|withdrawn`), budget (`budget.recorded`) and
integrations. Agents add free-form `note.<name>` events alongside the prose in
`log.md`. `actor` is `human`, `agent` or `system` — so an answer an agent gave
itself can never look like the user's.

## Jobs

Every long-running thing — an agent dispatch, a sweep, a sync — runs as a job
in its own process group, recorded in `workspace/<slug>/jobs/<id>.json` with
its output streamed to `<id>.log` and `<id>.err` **while it runs**:

- output is visible before the process exits, so nothing tails a black box;
- a timeout kills the whole process group and **keeps the partial output**
  (exit code 124);
- cancelling takes down the children too, not just the wrapper;
- a job whose process is gone after a restart is reconciled to `lost`, never
  left claiming to run.

A dispatch inside a run records its duration against the run's budget
automatically.

## Gates: approvals as data

Every approval a protocol requires is a file, not a sentence in a chat window
— which is what lets a coordinator run headless while you stay in control:

```bash
uv run scieflow gate list <slug> --open
uv run scieflow gate show <slug> <id>
uv run scieflow gate answer <slug> <id> approve
uv run scieflow gate wait <slug> <id>      # what the agent blocks on
```

The kinds live in `schemas/gates.yml`:

| Kind | Needs a human? | For |
|---|---|---|
| `campaign-approval` | no | run a proposed experiment campaign |
| `outline-approval` | no | draft from this paper outline |
| `staffing` | no | which agents do which roles for this run |
| `question` | no | a clarifying question with no side effects |
| `claim-check-consent` | **yes** | spend your NotebookLM quota on a claim audit |
| `upload` | **yes** | push data or chats to remote storage |
| `external-sharing` | **yes** | send content to an external service |
| `tier-promotion` | **yes** | let a support agent act as primary for a role |
| `scope-change` | **yes** | leave the approved question, scope or bounds |
| `budget-extension` | **yes** | raise a budget limit |

In a **gated** run (`approval: per-campaign`) you answer every gate. In an
**autonomous** run the coordinator may answer a gate itself — only a kind that
does not require a human, only one it opened as `--in-scope`, and only with a
recorded rationale (`--as-agent --rationale "…"`). Gates marked "needs a human"
block in every mode.

## Budgets are enforced in code

A run's `budget.yml` caps iterations, experiment runs and wall minutes. Since
a headless run has nobody watching the terminal, the limits are enforced by the
code that spends them, not by an agent remembering to check:

| Dimension | Enforced where | What happens when it is spent |
|---|---|---|
| `wall_minutes` | before every agent dispatch | dispatch refused, **exit 75**, run checkpointed |
| `experiment_runs` | before `experiment run` / `sweep` | the sweep is refused, run checkpointed |
| `iterations` | `run advance` | the next iteration is refused, run checkpointed |

Each refusal writes a `job.refused` event and a `low-budget` checkpoint with
resume instructions. Raising a limit is your decision — a `budget-extension`
gate, never an edit an agent makes on its own.

## Staffing: provider, model and effort per role

Which agent performs a role comes from `config/defaults.yml`; a role may also
pin that agent's model and effort **for that role only**:

```yaml
assignments:
  research.draft-authors:
    - {agent: codex, model: gpt-6-astra, reasoning: xhigh}
    - {agent: claude, model: claude-opus-5, reasoning: extended-thinking}
  research.reviewer: codex-review          # plain strings keep working
```

From the command line the same thing is `AGENT@MODEL/EFFORT` (either part
optional):

```bash
uv run scieflow agent configure --assign \
  research.draft-authors=codex@gpt-6-astra/xhigh,claude@claude-opus-5/extended-thinking --yes
```

The dispatch names the role, and the role's choice is applied:

```bash
uv run scieflow agent run --role research.draft-authors codex <prompt> <transcript>
```

An agent that is not assigned to that role is refused, and tier routing still
applies: a support agent on a primary-only role needs the explicit
`--promote` exception. **You choose the provider, model and effort — ScieFlow
never picks or downgrades a model by itself.**
