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
| `77` | sandbox could not be established or proven, or a credential, `readable:` entry or hidden path was refused; the dispatch was refused | `scieflow agent run` only (`agent_run.SANDBOX_EXIT`); see [The agent sandbox](sandbox.md) |
| `78` | a dispatch refused by a data-protection rule (an L2 run, a cloud agent, a policy or a scan rule); the dispatch was refused and no job was created | `scieflow agent run` only (`agent_run.DATA_EXIT`); see [Data protection](data-protection.md) |
| `124` | the agent process was killed for running past its timeout, with partial output kept | `scieflow agent run`, and any job `jobs.wait` times out on |
| `2` | `gate wait --timeout` expired without an answer | `scieflow gate wait` |
| `1` | any other refusal (bad slug, unmet precondition, validation problem) | Click's default for a raised `ClickException`, e.g. `run advance` past `iterations`, `run mark` with a bad phase/state |
| `3` | a literature refusal: the query failed the leak check, a source is switched off, or a data-protection rule refused the request (for an L2 run, an unlisted service or a copy of snapshots out of the run) | `scieflow research search` and `search all` (a refused or switched-off source); `scieflow research snapshots copy` (L2 source). See [Literature kernel](research/literature-kernel.md) |
| `6` | a no-egress literature run (`replay` or `offline`) has no recorded answer for the request; nothing was sent | `scieflow research search` and `search all`; see [Literature kernel](research/literature-kernel.md#network-modes) |

## `scieflow data`

The data-protection commands: declare a run's data level, scan for undeclared
patient data, release a checked aggregate, import it into another run, and
de-identify a tabular export. Every subcommand accepts `--json`. Exit codes:
`0` ok, `1` refused or a check failed, `2` usage or not available, `3` invalid
policy file. The rules are in [Data protection](data-protection.md).

```bash
uv run scieflow data levels <slug>
uv run scieflow data declare <slug> 'inputs/*.csv' --level L2 --reason "REDCap export"
uv run scieflow data scan <slug>
uv run scieflow data release <slug> outputs/table1.csv --counts n --reason "counts"
uv run scieflow data import <dest> --from <src> --release <id>
uv run scieflow data ingest <slug> raw/export.csv --out inputs/clean.csv --profile redcap
uv run scieflow data audit <slug>
uv run scieflow data pin <agent>
```

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
(copied to `goal.md`); unset options fall back to `config/defaults.yml`. The
browser has the same thing as a form — the Start page, `/start` (see [The
local web app](web.md#pages)) — with an optional coordinator hand-off besides.

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
uv run scieflow run charter SLUG [--set TEXT] [--note TEXT] [--revert N] [--as-agent]
```
Show or change what this run has agreed to do. With no flags, prints the
current charter text and its version. `--set TEXT` writes a new version
(`--note TEXT` records why, and only makes sense with `--set`); `--revert N`
makes version `N` current again by appending a copy of it, rather than
rewinding — the history of how the goal moved is never lost. `--set` and
`--revert` are mutually exclusive, and an empty `--set` is refused.
**`--as-agent` may only be used to read the charter** — an agent passing it
together with `--set` or `--revert` is refused: a coordinator that could
rewrite its own goal directly would defeat the `charter-adoption` gate's
`requires_human` guarantee. An agent that wants the charter changed opens
that gate instead and lets a human adopt the proposal. See
[The charter](runs.md#the-charter-what-this-run-agreed-to-do) for what gets
pinned to every prompt and how a coordinator can propose one.

```bash
uv run scieflow run events SLUG [--since EVENT_ID] [--type TYPE ...] [--follow] [--json]
```
The run's history. `--type` is repeatable and accepts a `job.*`-style prefix
match; `--follow` tails new events (Ctrl-C to stop).

```bash
uv run scieflow run log SLUG note.NAME [--message TEXT] [--data KEY=VALUE ...]
```
Record a free-form `note.<name>` event — the type must start with `note.`.

### The conversation, curation and previews

The same conversation, curation and preview functions the web workbench
calls, from the terminal. Each command is one service call: admission, budget
and sandbox guards are the existing ones, not a second implementation. Reads
take `--json`. Writes act as the human and refuse to run inside a ScieFlow
dispatch (exit `1`).

```bash
uv run scieflow run conversation SLUG [--json]
uv run scieflow run converse-agent SLUG AGENT [--reset-session] [--expected-revision N]
uv run scieflow run say SLUG (MESSAGE | --file PATH)
```
Show the run's conversation (agent, turns, whether a turn is in flight), choose
its agent, and send one message. `say` prints the turn's job and reply as JSON.
`--expected-revision` refuses the change unless the conversation is still at
that revision.

```bash
uv run scieflow run start SLUG --goal FILE [--agent A] [--workflow W]
    [--max-iterations N] [--max-experiment-runs N] [--max-wall-minutes N]
uv run scieflow run merge-round SLUG
```
Create a run from a goal file, and have the named agent take the first turn
when one is given. `merge-round` sends this round's curation to the
conversation agent. The round advances only when that turn succeeds.

```bash
uv run scieflow run curation show SLUG [--json]
uv run scieflow run curation history SLUG [--json]
uv run scieflow run curation add SLUG (TEXT | --file PATH)
uv run scieflow run curation keep SLUG AGENT SECTION (TEXT | --file PATH)
uv run scieflow run curation edit SLUG BLOCK_ID (TEXT | --file PATH)
uv run scieflow run curation move SLUG BLOCK_ID POSITION
uv run scieflow run curation remove SLUG BLOCK_ID
uv run scieflow run curation note SLUG [TEXT | --file PATH]
uv run scieflow run curation revert SLUG VERSION
```
The curation document: `show` prints each agent's draft, each merged round and
the document itself. Every write appends a version, and `revert` appends a copy
of an earlier one rather than rewinding.

```bash
uv run scieflow run manuscript history SLUG [--json]
uv run scieflow run manuscript diff SLUG A B [--json]
```
The run's provenance points, and a diff between two of them (their `ref`
strings from `history`).

```bash
uv run scieflow run cancel SLUG JOB_ID
```
Cancel a job. A job that belongs to another run is refused and nothing is
cancelled.

```bash
uv run scieflow run preview SLUG SOURCE [--json]
uv run scieflow run preview SLUG SOURCE --compile
```
Without `--compile`, report whether a draft's PDF preview is available and where
it is. With `--compile`, start the compile as a job (it needs `latexmk`).

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
    [--no-sandbox]
```
Run one agent headless. `--cwd` defaults to the repo root. `--role` applies
that role's assigned model/effort override for `AGENT` (see
[Agent configuration](agents.md#roles)); if `AGENT` is not assigned to
`ROLE`, the dispatch is refused before anything runs. `--no-sandbox` runs
without filesystem confinement — a deliberate opt-out, recorded as a
`sandbox.disabled` event on the run's timeline rather than run silently
(see [The agent sandbox](sandbox.md)). Exit codes: `0` ok,
`75` refused on an exhausted `wall_minutes` budget (the run is checkpointed),
`77` refused because the sandbox could not be established or proven, or because a credential, `readable:` entry or hidden path was refused,
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

## `scieflow review`

Draft review: settings, reviewer assignment, status and decisions. The file
layout, actor rules and the Python API behind these commands are in the
[review contract](research/review-contract.md).

```bash
uv run scieflow review configure SLUG [--set KEY=VALUE ...] [--as-agent] [--yes]
uv run scieflow review assign SLUG --round N [--json]
uv run scieflow review prompt SLUG --round N --reviewer R --author A --pass blind|evidence [--write]
uv run scieflow review check SLUG --round N --reviewer R --author A --pass blind|evidence
uv run scieflow review freeze SLUG --round N
uv run scieflow review evidence SLUG --round N [--plan] [--json]
uv run scieflow review matrix SLUG --round N [--check]
uv run scieflow review gates SLUG --round N
uv run scieflow review revise SLUG --round N --author A [--write]
uv run scieflow review coverage SLUG --round N --author A
uv run scieflow review merge-check SLUG
uv run scieflow review status SLUG [--json]
uv run scieflow review decide SLUG --round N --decision CLUSTER=accept|reject|defer ...
    [--note CLUSTER=TEXT ...] [--overrule CLUSTER=REASON ...]
    [--as-agent --rationale TEXT] [--yes]
```
`configure` shows or changes the run's `review:` block; `--as-agent` may only
set `scite=off` and `reviews_per_draft`. `assign` plans who reviews which
draft and writes `review/round-N/assignment.json`. In a paper-review run
(`status.yml` `workflow: paper-review`) the one reviewed author is
`research.submitter`, the default pool is `research.reviewer`, and every
command reads the manuscript under `manuscript/` instead of a draft. `evidence` builds the
round's shared evidence bundle (`review/round-N/evidence/`) from free sources
and at most a few scite MCP calls; `--plan` prints the exact outgoing request
list and sends nothing — see [scite evidence](research/scite.md). Before it
builds the bundle, `evidence` freezes pass A: the sha256 of every valid
`<reviewer>-on-<author>.blind.json` goes into `assignment.json` (`freeze` does
the same on its own). `prompt` builds the pass-A prompt (persona and draft, no
evidence) or the pass-B prompt (the frozen pass A verbatim, the draft, the
bundle in a fenced data block and the rendered review instructions) for one
assigned pair; pass B is refused when pass A was never frozen or changed after
it was frozen. Each prompt is a fresh dispatch with
`--role research.draft-reviewers` (`--role research.reviewer` in paper-review). `check` validates a returned review file
(schema, identity, frozen pass A, every pass-A comment accounted for, evidence
quotes verbatim from the bundle) and records `review.pass_done`. `matrix`
writes `review/round-N/matrix.skeleton.json` (every comment of every valid
pass plus every bundle flag); `--check` validates the coordinator's clustered
`matrix.json`, writes `matrix.md` and records `review.matrix_ready`. `status` lists the review
rounds, open blockers and whether scite is configured. `decide` records
per-cluster decisions on the round's matrix; a blocker cluster needs the
user's `--overrule` reason (a `reject` with a reason overrules it), and an
agent may decide only when the run's `review_decisions` is `coordinator`.

`gates` (also run by `matrix --check`) opens one `review-decision` gate per
undecided cluster (`accept|reject|defer`) and one human-only `review-blocker`
gate per open blocker (`overrule|fixed`, with a `review.blocker` event).
Answering one with `scieflow gate answer` records the decision through the
same writer as `decide`: an overrule needs the user's reason as `--note`,
`fixed` records nothing (the blocker clears when a later round's evidence no
longer flags the reference). `revise` builds an author's revision prompt with
the accepted clusters only; it refuses without recorded decisions (a
hand-written `decisions.json` is ignored). `coverage` checks that every
accepted cluster id heads a section (`## K2`) with text under it in
`review/round-N/response-<author>.md` (exit 1 if not). An agent never
changes a decision the user made. `merge-check` exits 1 while a blocker is open.

## `scieflow workspace`

Read-only views over `workspace/`.

```bash
uv run scieflow workspace list [--json]
```
List runs with kind, state and last activity.

```bash
uv run scieflow workspace doctor SLUG [--json]
```
Read-only health report for one run, including scite readiness (configured,
SDK group installed, token exposed to dispatches, harness MCP registrations
by name).

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
