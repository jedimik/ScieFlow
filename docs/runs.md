# Runs, jobs and gates

A **run** is one piece of research with its own directory under
`workspace/<slug>/`: a goal, a status, a budget, an append-only history, the
jobs it started, the approvals it asked for, and everything it produced. The
research loop, a paper draft and a literature review are all runs.

Everything on this page is a thin caller of one set of run primitives
(`scieflow.core.run.actions` and `scieflow.core.run.gates`), so the CLI, the
coordinator agent and the local web app (`scieflow serve`, see [The local web
app](web.md)) all read and change the same state through them. The CLI calls
those functions directly; the web app calls them through a thin wrapper layer
(`scieflow.core.service`) that exists for the browser's sake. Every command
below has a browser equivalent on the run's page — marking a phase,
advancing, checkpointing, resuming, recording spend and answering a gate all
go through the same underlying run action either way, so the two never
disagree about what a run's state is.

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
and records an event. Each has a browser form on `/runs/<slug>` that does
the same thing:

| CLI command | On the run page |
|---|---|
| `run mark` | the phase form — pick a phase and a state, "Mark phase" |
| `run advance` | "Advance iteration" |
| `run checkpoint` | the "Checkpoint" button (a reason of `user` and whatever you type as detail) |
| `run resume` | the "Resume" button, shown in its place once the run is stopped |
| `run spend` | the "Record spend" form |
| `gate answer` | each open gate's own answer form, on the run page and the dashboard |

The only difference is who is recorded as `actor`: a command run with
`--as-agent` logs `agent`, the browser's forms log `human` — same as running
the command without `--as-agent` yourself.

## The charter: what this run agreed to do

`workspace/<slug>/charter.yml` holds a run's standing goal — a short,
human-curated statement of what the run is for, versioned like everything
else here. Unlike status and budget, nothing computes it; it exists because a
long agent conversation drifts from where it started, and the fix is to
re-state the goal rather than trust the conversation to remember it. Every
prompt a run sends an agent is composed with the current charter pinned to
the top (`agent_run.compose_prompt`) — that is the entire reason the feature
exists. A run with no charter composes exactly as it always has.

A revert does not rewind: it appends a fresh version that copies an earlier
one's text, so the sequence of how the goal moved stays on the record instead
of being erased. "Current" is always whichever version is newest.

```bash
uv run scieflow run charter <slug>                       # show the current text and its version
uv run scieflow run charter <slug> --set "..." --note "narrowed scope to X"
uv run scieflow run charter <slug> --revert 3            # make version 3 current again
```

| CLI command | On the run page |
|---|---|
| `run charter <slug>` | the charter panel, always visible |
| `run charter <slug> --set` | the panel's edit form |
| `run charter <slug> --revert` | a "Restore" button next to that version in its history |

Unlike the other `run` subcommands above, `run charter` calls
`service.run_charter`, `service.set_charter` and `service.revert_charter`
directly — the very same functions the browser's charter panel calls —
rather than going through `scieflow.core.run.actions`. Those wrappers already
existed for the browser, and calling them from the CLI too was the shortest
correct path to the one `charter` primitive underneath, so here the CLI and
the browser genuinely share a function call, not just a common primitive.

The charter is data, never instructions to ScieFlow itself: it changes what
an agent is told, never what any `scieflow` command does.

### A coordinator may propose a charter

A human is not the only author. A coordinator agent can write a plan to a
file inside its own run and open a `charter-adoption` gate naming that file —
`open_gate` has no field for a payload, so the proposal travels the way every
other document a gate points to already travels, as a file the gate's
`files` names. That gate is marked `requires_human: true`, the same as
`scope-change` and for the same reason: adopting a charter redefines what the
run is *for*, so the agent that wrote the proposal cannot also adopt it. The
run page shows the proposed text itself alongside the gate's question, so
answering "adopt" is an informed decision, not a click on an agent-written
question with no idea what it commits to.

The proposal file must resolve inside the run — the same containment rule
the artifact browser applies to a path a browser request names, applied here
to a path an agent's gate record names, since this text is about to become
the charter pinned to every later prompt the run sends. A path that resolves
outside the run is refused, never read.

Answering `adopt` does the file read and validation — missing, unreadable,
empty, or escaping the run are all refused the same way — *before* the gate
itself is recorded as answered, and only then writes the charter, attributed
to whoever answered with a note recording which gate it came from. That
ordering runs both ways: the gate is never recorded as answered by an
adoption that could not also produce a charter, and the charter is never
written before the decision to adopt it is on the record. A gate whose
proposal turns out to be unreadable is refused and stays open, so fixing the
file and answering again is possible; nothing is left half-decided.
Answering `decline` (or anything else) never touches the proposal file and
leaves the charter untouched.

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

An agent dispatch also runs filesystem-sandboxed by default — confined to
its own run — and is refused (exit 77) if that confinement cannot be
proven; see [The agent sandbox](sandbox.md).

## Gates: approvals as data

Every approval a protocol requires is a file, not a sentence in a chat window
— which is what lets a coordinator run headless while you stay in control:

```bash
uv run scieflow gate list <slug> --open
uv run scieflow gate show <slug> <id>
uv run scieflow gate answer <slug> <id> approve
uv run scieflow gate wait <slug> <id>      # what the agent blocks on
```

`gate answer` is the one command with two browser homes, not one: an open
gate's question, options and an answer form appear on both the dashboard
(`/`, across every run) and the run page (`/runs/<slug>`), so answering the
gate blocking a run has never needed you to know which run it belongs to
first.

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
| `charter-adoption` | **yes** | adopt a proposed plan as this run's charter |

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
