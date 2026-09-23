# CLI reference

Every command below accepts `--help`; this page exists so you don't have to
run it eight times. Concepts (what a run, an event, a job or a gate is) are
covered in [Runs, jobs and gates](runs.md) — this page lists commands, flags
and exit codes.

All examples assume you are at the repo root and use `uv run scieflow …`.

## Exit codes

| Code | Meaning | Where |
|---|---|---|
| `0` | success | everywhere |
| `75` | dispatch refused — the run's `wall_minutes` budget is spent | `scieflow agent run` only (`agent_run.BUDGET_EXIT`); the run is checkpointed automatically |
| `124` | the agent process was killed for running past its timeout, with partial output kept | `scieflow agent run`, and any job `jobs.wait` times out on |
| `2` | `gate wait --timeout` expired without an answer | `scieflow gate wait` |
| `1` | any other refusal (bad slug, unmet precondition, validation problem) | Click's default for a raised `ClickException`, e.g. `run advance` past `iterations`, `run mark` with a bad phase/state |

## `scieflow run`

A run's state, history and lifecycle. Every subcommand needs the run's
slug (`workspace/<slug>`).

```bash
uv run scieflow run list [--json]
```
Every run: kind, phase and state. `--json` for machine-readable output.

```bash
uv run scieflow run init SLUG --goal FILE [--approval per-campaign|autonomous]
    [--max-iterations N] [--max-experiment-runs N] [--max-wall-minutes N]
```
Create a research-loop run workspace. `--goal` must be an existing file
(copied to `goal.md`); unset options fall back to `config/defaults.yml`.

```bash
uv run scieflow run show SLUG [--json]
```
Status, budget and what to do next.

```bash
uv run scieflow run mark SLUG PHASE STATE [--as-agent]
```
Set a phase's state (`pending`/`running`/`done`/`failed`). `--as-agent`
records the change as made by an agent — agents must pass it.

```bash
uv run scieflow run advance SLUG [--as-agent]
```
Start the next iteration. Refused (exit 1, run checkpointed) if the
`iterations` budget is spent.

```bash
uv run scieflow run checkpoint SLUG --reason low-budget|max-iterations|converged|anomaly|user
    [--detail TEXT] [--as-agent]
```
Stop the run gracefully and record resume instructions.

```bash
uv run scieflow run resume SLUG [--as-agent]
```
Clear a stop so the run can continue.

```bash
uv run scieflow run spend SLUG [--experiment-runs N] [--wall-minutes N] [--iterations N] [--as-agent]
```
Record spend the runner cannot see (remote jobs, manual work) — dispatch
wall time and `scieflow experiment run`/`sweep` runs are recorded
automatically already. Pass at least one dimension. Never hand-edit
`budget.yml`.

```bash
uv run scieflow run events SLUG [--since EVENT_ID] [--type TYPE ...] [--follow] [--json]
```
The run's history. `--type` is repeatable and accepts a `job.*`-style prefix
match; `--follow` tails new events (Ctrl-C to stop).

```bash
uv run scieflow run log SLUG note.NAME [--message TEXT] [--data KEY=VALUE ...]
```
Record a free-form `note.<name>` event — the type must start with `note.`.

## `scieflow gate`

Approvals the protocols require, stored as data under `gates/` and
answered from a terminal or (eventually) a browser.

```bash
uv run scieflow gate open SLUG --kind KIND --question TEXT
    [--option TEXT ...] [--file PATH ...] [--in-scope] [--json]
```
Open a gate (agents call this, then `gate wait`). `--kind` must be one of
the kinds in `schemas/gates.yml`. `--in-scope` marks the gate as inside the
approved goal, scope and budget — required before an agent can answer its
own gate in an autonomous run.

```bash
uv run scieflow gate list SLUG [--open] [--json]
```
Gates of a run; `--open` filters to gates still awaiting an answer.

```bash
uv run scieflow gate show SLUG GATE_ID
```
One gate, in full, as JSON.

```bash
uv run scieflow gate answer SLUG GATE_ID ANSWER [--note TEXT] [--as-agent] [--rationale TEXT]
```
Answer an open gate. `--as-agent` is only accepted when the gate does not
require a human, the run is `autonomous`, the gate was opened `--in-scope`,
and `--rationale` is non-empty — otherwise the answer is refused.

```bash
uv run scieflow gate wait SLUG GATE_ID [--timeout SECONDS]
```
Block until the gate is answered and print it as JSON. Without `--timeout`
it waits forever; with one, exits **2** if the gate is still open when the
timeout expires.

## `scieflow agent`

Dispatch headless agent CLIs and manage who performs which role.

```bash
uv run scieflow agent run AGENT PROMPT_FILE TRANSCRIPT_FILE [--cwd DIR] [--role ROLE]
```
Run one agent headless. `--cwd` defaults to the repo root. `--role` applies
that role's assigned model/effort override for `AGENT` (see
[Agent configuration](agents.md#roles)); if `AGENT` is not assigned to
`ROLE`, the dispatch is refused before anything runs. Exit codes: `0` ok,
`75` refused on an exhausted `wall_minutes` budget (the run is checkpointed),
`124` timed out (partial output kept in `TRANSCRIPT_FILE`), otherwise the
agent's own exit code.

```bash
uv run scieflow agent show [--workspace SLUG] [--news] [--json]
```
Effective role assignments and agent settings, with where each value comes
from (default, workspace, or a legacy key). Exits **1** if the resolved
configuration has a problem (unknown/disabled agent, a support-tier agent
in a primary-only role, etc.) — warnings are shown but don't fail.

```bash
uv run scieflow agent configure [--workspace SLUG] [--news]
    [--assign 'ROLE=AGENT[@MODEL][/EFFORT][,…]' ...]
    [--set AGENT.FIELD=VALUE ...] [--promote ROLE ...] [--demote ROLE ...]
    [--unset KEY ...] [--yes]
```
Change agent configuration — the defaults, one workspace, or the news
module. Without any change option, asks interactive questions; with them,
applies directly (what a coordinator agent runs after asking you in chat).
Every change is validated and shown as a diff before writing; `--yes`
writes without confirming. See
[Agent configuration](agents.md) for the `--assign` shorthand and what each
field means.

## `scieflow workspace`

Read-only views over `workspace/`.

```bash
uv run scieflow workspace list [--json]
```
List runs with kind, state and last activity.

```bash
uv run scieflow workspace doctor SLUG [--json]
```
Read-only health report for one run.

```bash
uv run scieflow workspace index
```
Write `workspace/INDEX.md` (generated, not committed).

```bash
uv run scieflow workspace sync-status [SLUG] [--big-gb N] [--json]
```
What a run would upload: new files since its last sync, and files at or
above `--big-gb` (default `1.0`).

## `scieflow serve`

The local web app: a read-only browser view over the same service layer as
everything above. Needs the `web` extra (`uv sync --extra web`). See
[The local web app](web.md) for the pages, the API and the security model.

```bash
uv run scieflow serve [--port N] [--host HOST] [--no-browser]
```

- `--port` — port to listen on (default `8765`).
- `--host` — the address to bind. Only loopback addresses (`127.0.0.1`,
  `localhost`, `::1`) are accepted; anything else is refused before uvicorn
  starts, with a message pointing at an SSH tunnel or Tailscale instead.
  Reach the app from another machine by forwarding the port
  (`ssh -L 8765:127.0.0.1:8765 host`), never by widening this.
- `--no-browser` — print the URL and open nothing (the default opens it for
  you).

The printed URL carries a one-time token that becomes a session cookie on
first open; it is generated fresh each time you run `serve` and stops being
valid when that process exits.

## Module CLIs

Each module has its own command group and its own CLI reference:

- `scieflow experiment` — computational experiments (campaigns, sweeps,
  metrics, reports): see [Experiments CLI reference](experiments/cli.md).
- `scieflow research` — literature search, validation, citations, Zotero
  export: see [Research](research/index.md).
- `scieflow news` — tracking what changed in the tools and topics you
  follow: see [News](news/index.md).
- `scieflow chats` — backing up and restoring agent chats, skills and
  plugins: see [Chats](chats/index.md).
