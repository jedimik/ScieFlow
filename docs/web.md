# The local web app

## What it is

`scieflow serve` is a browser view over the same service layer the CLI and
the coordinator agent use (`scieflow.core.service` — see
[Architecture](architecture.md)). Every route in `scieflow.web` is a thin
caller of that layer and holds no logic of its own, so the app can never
show you a different truth than `scieflow run show` or `scieflow gate list`
would.

The app is no longer a viewer. From the browser you can answer a gate, mark
a phase, advance an iteration, checkpoint and resume a run, record spend,
cancel a job, and change which agent performs which role. For everything
except cancelling a job, that is exactly what the matching CLI command
does, because both reach the same underlying run action: `POST
/runs/r1/act` with `action=checkpoint` goes through `service.checkpoint_run`,
and `scieflow run checkpoint r1` calls `actions.checkpoint_run` directly —
two callers of the one `checkpoint_run` primitive in
`scieflow.core.run.actions`, not two callers of the same function. Either
way the run writes the same `checkpoint` event and ends in the same state.
There is no separate "web logic" to drift out of sync with the terminal —
each `service.*` wrapper the web app calls is a thin pass-through to the
same `actions`/`gates` module the CLI uses, so the two cannot disagree about
what a run's state is, even though only the web currently calls `service`
for this.

Cancelling a job has no CLI command to mirror — a job you started from a
terminal you stopped by killing the process yourself. The browser has no
process to kill, so `POST /runs/<slug>/jobs/<job_id>/cancel` calls
`service.cancel_job`, which reaches the same `jobs.cancel` that a timeout
already uses internally: it sends the kill signal to the job's whole
process group and records `job.cancelled`. It is new *capability*, not a
new code path — the function already existed for the runner's own use.

Starting a run is also something this app does, on the **Start page**
(`/start`): a form for the run's name, workflow, goal, approval mode and the
three budget limits, plus an optional coordinator to hand the run to for its
first turn. It creates the workspace the same way `scieflow run init` does —
both reach `run.init.init_workspace` — so a run started from the browser is
indistinguishable from one started at a terminal. See [Pages](#pages) below
for exactly what it collects, and ["What stays
CLI-only"](#what-stays-cli-only) for what the browser still deliberately does
not do.

## Running it

```bash
uv sync --extra web
uv run scieflow serve
uv run scieflow serve --port 9000
uv run scieflow serve --no-browser
```

`serve` binds `127.0.0.1` and prints a URL:

```
ScieFlow — /path/to/your/project
Open: http://127.0.0.1:8765/?token=<random>
The token in that URL is this session's key; it becomes a cookie
on first open. Ctrl-C to stop.
```

Unless you pass `--no-browser`, it also opens that URL for you. The token in
it is generated fresh each time you run `serve` (`auth.new_token`, 32 random
bytes, URL-safe) and lives only for that process — stopping and restarting
`serve` invalidates it and issues a new one.

## Security model

The app is safe to leave running because of a small set of deliberate
restrictions, not because the network happens to be trusted:

- **Loopback-only bind.** `serve --host` only accepts `127.0.0.1`,
  `localhost` or `::1`; anything else is refused before uvicorn ever starts
  (`auth.loopback_only`). There is no configuration flag to widen this.
- **Token-to-cookie exchange.** The one-time token in the printed URL is
  compared in constant time (`secrets.compare_digest`) against the query
  string on every request. A match issues a session cookie
  (`scieflow_session`, `HttpOnly`, `SameSite=Strict`) and a CSRF cookie
  (`scieflow_csrf`, readable by JavaScript, also `SameSite=Strict`), then
  redirects to the same path with the token stripped out — so the token
  never lingers in browser history, a bookmark or a `Referer` header. A
  wrong token is rejected with 403 and issues nothing. From then on, every
  page and every `/api/v1` route requires the session cookie
  (`auth.require_session`); without one you get a 401 (an HTML page if your
  browser asked for HTML, JSON otherwise).
- **CSRF double-submit.** `GET`/`HEAD`/`OPTIONS` need only the session
  cookie. Every other method needs the `X-CSRF-Token` header (or a
  `csrf_token` form field) to match the `scieflow_csrf` cookie's value — a
  cookie alone, which a browser attaches automatically, is not enough to
  make a state-changing request. The check happens once, centrally, in
  `auth.install_session`'s middleware, for every unsafe request before
  routing decides anything; the middleware records the verdict on
  `request.state.csrf_checked`, and each mutating route's
  `Depends(auth.csrf_protect)` trusts that flag rather than re-parsing the
  body — a router mounted without the middleware fails closed with a 403
  instead of silently passing.
- **A declared inventory of mutating routes.** `tests/web/mutating_paths.py`
  lists every path allowed to accept anything but `GET`/`HEAD`/`OPTIONS`
  (`MUTATING_PATHS`); `tests/web/test_read_only.py` fails the build if a route
  starts mutating without being added to it, or if a listed route stops
  mutating. `tests/web/test_mutations.py` reads the same list — via a sample
  form body per path — to run its session-guard and CSRF-guard tests, and
  asserts the two stay in lockstep. So a new state-changing endpoint can't
  land unnoticed by this doc, and it can't gain an inventory entry without
  also gaining a guard test.
- **No CORS.** The app sends no `Access-Control-Allow-Origin` header at
  all, so no other origin's page can read a response from it, cross-site
  request or not.
- **Every agent dispatch the service layer starts is sandboxed**, under the
  same guarantee documented in [the sandbox reference](sandbox.md).
- **The artifact browser cannot leave the run directory.** Every path a
  request names is resolved to an absolute path and checked to be a strict
  descendant of that run's workspace directory (`scieflow.web.files.resolve`)
  — resolution happens *before* the containment check, so a symlink that
  points outside the run is caught by the same test as a literal `../..` in
  the URL. Artifacts are always served with `X-Content-Type-Options:
  nosniff`, and only a short allowlist of inline-safe types (PNG, JPEG, GIF,
  WebP, PDF) is ever shown in the browser; everything else — HTML, JS, SVG
  (which can itself carry a `<script>`), or anything unrecognised — is
  forced to `application/octet-stream` with an attachment disposition, so a
  file an agent wrote into a run can never execute in the app's own origin.

**Remote access is a tunnel, never a wider bind.** To reach the app from
another machine, forward the port instead of loosening the host:

```bash
ssh -L 8765:127.0.0.1:8765 host
```

or use Tailscale (or an equivalent private-network tool) and tunnel to
`127.0.0.1` on the host running `serve`. `scieflow serve --host 0.0.0.0` (or
any other non-loopback address) is refused outright, with a message pointing
at exactly this.

## Pages

| Page | Route | Shows |
|---|---|---|
| Start | `/start` | A form to create a run: name (becomes `workspace/<name>`, validated before anything is written by both `Project.run_dir` and `init_workspace` itself — a name like `../escape` is refused by either), workflow (recorded onto the run's `config.yml`; defaults to `research-loop`, the one workflow this form's own `init_workspace` call actually produces the shape of), goal, approval mode, the three budget limits, and — optionally — a coordinator to hand the run to for its first turn. Naming one has that turn happen as part of this same request, as an ordinary conversation turn through `service.say`: sandboxed, counted against the budget, on the timeline, cancellable. Leaving it unset just creates the run, which is also what happens if no configured agent can hold a conversation at all — the picker says so rather than offering a launch it can't perform. Staffing (which agent does which role) isn't collected here; that's still [the Agents page](agents.md#the-agents-page)'s job, since a run resolves its staffing when it dispatches, so setting it right after creation is equivalent. Success redirects to the new run's page, using the canonical name the run was actually created under (not necessarily what was typed — a `workspace/` prefix or surrounding whitespace is normalised away). A refusal *before* the run exists re-renders this form with what you typed, rather than redirecting to `?error=...` as every other page on this list does — the goal is free text that can run to paragraphs, and a query string is the wrong place to carry it. A failure *after* the run exists (the chosen coordinator's first turn was refused — a sandbox check, an exhausted budget, an oversized prompt) instead redirects to that run's own page with the error, the same as any other action on an existing run, since re-rendering an empty form here would hide that the run was in fact made. |
| Dashboard | `/` | Every run (slug, kind, phase), a budget bar per dimension, and every open gate across all runs, each with an inline form to answer it on the spot — a `charter-adoption` gate also shows the proposed charter text itself, not just its question. |
| Run page | `/runs/<slug>` | The run's id, iteration and approval mode; its phases with a form to mark one; budget remaining per dimension with a form to record spend; buttons to advance the iteration, checkpoint or resume; the charter panel — current text, an edit form, and (once there is more than one version) a history with a Restore button next to each version other than the current one; a conversation panel — every turn so far, a note if the current agent has quietly lost its session, a box to send the next message (disabled while a turn is running or the chosen agent cannot hold one), and a control to hand the conversation to a different agent (offering only agents that could actually take it — enabled, and able to both start and resume a session), disabled only while a turn is running, never because the current agent can't converse, so a stuck agent can always be replaced; open gates, each with a form to answer it (a `charter-adoption` gate's proposed text is shown above its form, escaped, so adopting is an informed decision); every job it started, linked to its output, with a Cancel button while it runs; and a timeline of the run's events, updated live. Links out to the draft workbench, below. |
| Draft workbench | `/runs/<slug>/drafts` | Every drafting agent's sections, side by side, each with a Compile PDF button; the curation built from them — kept passages and your own written blocks, reorderable, each removable, with a version history and a Restore button next to any earlier one; a note staged for the next round; and the control that sends the round to the merging agent. See below for what makes a kept passage trustworthy, how a round is dispatched, and how the preview compiles. |
| Job output | `/runs/<slug>/jobs/<job_id>` | The job's command, state, exit code and duration, and its captured stdout/stderr. While the job is still running, output streams in live. |
| Artifact browser | `/runs/<slug>/files[?path=...]` | A directory listing under the run; `/runs/<slug>/file?path=...` renders a small text file inline or downloads anything else, per the security model above. |
| Agents | `/agents[?slug=<run>]` | Role assignments in effect (defaults, or one run's if `slug` is given), a form to pick a role and an agent, a preview of the resulting diff, and an Apply button. See [Agent configuration](agents.md#the-agents-page). |

The run page's timeline and a running job's output are both live: each page
opens a browser `EventSource` against the matching `/api/v1` stream (below)
and appends new rows/lines as they arrive, with no page reload and no extra
JavaScript framework.

The conversation panel is the exception: it refreshes when a turn lands
rather than streaming the agent's output as it is produced. Sending a
message posts the form and waits for the reply; the new turn appears only
once that redirect reloads the page. That is a deliberate difference, not a
missing feature — a conversational dispatch's job log is the raw JSON event
stream its `session_cmd`/`resume_cmd` produces, and the readable text a
person would want to read live does not exist yet while the turn is running:
it is only produced by parsing that stream (`scieflow.core.sessions.parse`)
once the job has finished. Streaming the job's own output live would show a
wall of JSON, not the agent's reply.

### The draft workbench

Linked from every run's own page, `/runs/<slug>/drafts` is where you read
what several agents each drafted for the same manuscript, decide which
passages actually deserve to survive, and send that decision back for the
next round. Before a run's `paper-draft` workflow has reached its drafting
phase — the point where `manuscript/drafts/<agent>/` first exists — only the
drafts grid is replaced, by a message naming `paper-draft`; the curation
panel, the staging note, the version history and the "Send this round"
controls (including the agent hand-over and the merge button) all still
render and still work. Writing your own text and sending it as a round
before any agent has drafted anything is a real, supported path, not an
oversight: `merge_round` only refuses when there is neither a kept passage
nor a note, and neither of those requires an agent draft to exist.

A kept passage is a quotation, not a pointer. Selecting text in one of the
drafts shown on the page posts the text itself, plus which agent wrote it
and which section it came from — `run/curation.py` stores exactly that,
never a character offset into the file the text was copied from. That is
the one design fact worth trusting the feature on: every merge round
rewrites the sections it touches, so a stored offset would silently come to
point at different words a round later, or at nothing once a section is
renamed or dropped. A quotation cannot go stale. The cost is that a kept
passage stops tracking later edits to its source — which is correct,
because keeping a passage means choosing *those words*, not a moving
reference to whatever the source happens to say next. Capturing it needs no
editor framework: the page listens for the browser's own text selection and
posts it verbatim, with the agent and section it was taken from.

Your own words go in beside the kept passages as a block that claims no
provenance — there is no agent or section to credit them to. A separate
note, staged alongside the blocks rather than folded into them, is not a
passage at all: it is what you want to say *about* the curated selection —
an instruction for the next round, kept apart from the quoted material it
comments on. Both kinds of block and the note are stored and reproduced
verbatim; nothing in ScieFlow parses or acts on either one.

The whole curated document is versioned exactly like the charter: every
keep, edit, move, removal or note change appends a new version rather than
overwriting the last one, and any earlier version can be restored with a
Restore button next to it in the page's version history — restoring appends
a fresh copy of that version rather than rewinding, so the fact that you
once trimmed the selection down stays on the record. That matters in
practice: trim hard to fit a prompt, have the round go badly, and
yesterday's selection is still one click away.

Sending a round to the merging agent is an ordinary conversation turn — the
very `say` a chat message on the run page dispatches (see [The
conversation](runs.md#the-conversation-talking-to-the-coordinator)):
sandboxed, counted against the run's budget, visible on the timeline and in
its job list, and cancellable from there like any other job. The merging
agent itself is switched with the same hand-over control the run page's
chat panel uses — the workbench's own "Hand the conversation over" form
posts to the run's `/runs/<slug>/say` route. A round only advances once
that turn actually finishes successfully; a turn that fails, times out or
is cancelled still costs budget and still lands on the timeline, but leaves
the curation pointed at the same round rather than at an empty directory
nothing will ever fill.

The Compile PDF button on each draft or completed round assembles a
throwaway document and compiles it with `latexmk`. A section's `.tex` file
has no `\documentclass` of its own, and the run's real `main.tex` expects
the *merged* sections a completed run assembles at Phase 5, not one agent's
draft — so neither is compilable as it stands, and the preview builds its
own copy instead: the run's `manuscript/preamble.tex` when there is one,
else the shipped template's, and a `main.tex` generated from that same
template with its `\input` lines pointed at whichever draft or round is
being looked at. That copy lives in a scratch directory under
`manuscript/curation/preview/` and the run's real `manuscript/` is never
written to — a preview is not an assembly step. A failed compile is a
normal, expected state while drafting: the page shows the compiler's own
error output beside the source rather than hiding it or failing the round.
When `latexmk` itself is not installed, the page says so and falls back to
the source view — the same degrade `paper-draft`'s own Phase 6 already
makes.

Preview compiles are serialised per run, not per draft: every source in a
run shares one scratch TeX cache, so two compiles racing over it could
corrupt it for every later preview. Every Compile button on the page
disables the moment one compile is running anywhere in the run, whichever
draft or round it belongs to. If a compile looks stuck, cancel it from the
run's own job list — it appears there like any other job, tagged
`preview`.

On disk:

```
workspace/<slug>/manuscript/
  drafts/<agent>/<section>.tex        what paper-draft wrote
  curation/document.yml               your curation, versioned
  curation/rounds/<n>/<section>.tex   each merge round's output
  curation/preview/<source>/          throwaway preview builds
```

`<source>` there is `agent:<name>` or `round:<n>` with the colon turned into
a hyphen for the directory name — a preview of Claude's draft lands under
`curation/preview/agent-claude/`, and a preview of round 1 under
`curation/preview/round-1/`.

The compile carries the same sandboxing as every other dispatch, plus two
rules of its own, because the `.tex` it compiles was written by an agent
and so is untrusted input: `-shell-escape` is never passed, since
`\write18` would otherwise turn a preview into arbitrary command execution,
and `-norc` is passed alongside it, since without it `latexmk` would read
and run a `.latexmkrc` (as Perl) from the compile directory — a directory
that lives inside the very run the agent can write to. Either flag's
absence, on its own, would turn a LaTeX preview into a way to run whatever
an agent's draft asked for.

Every form on these pages posts back to the same page
(`303 See Other` on success, so a reload never repeats the action) or
carries you to `?error=<message>` on refusal — the same
`service.ServiceError` message the CLI would print. There is no separate
success/failure JSON to keep in sync with the terminal's exit codes. The
Start page is the one exception: it still redirects on success, but a
refusal re-renders the form directly rather than round-tripping through
`?error=...` (see [Pages](#pages) above).

## The API

Everything under `/api/v1` mirrors the service layer as JSON, requires the
same session cookie as the pages, and is meant for other tools, not just
this app's own pages — it is a stable-enough surface to script against.

| Route | Gives you |
|---|---|
| `GET /api/v1/runs` | Every run: slug, kind, phase, state, last activity. |
| `POST /api/v1/runs` | Create a run's workspace and return its detail — `service.create_run`, the same primitive `scieflow run init` and the Start page both build on (`run.init.init_workspace`). Takes `slug`, `goal`, `workflow` and `approval` only; the three budget limits and a coordinator hand-off are Start-page-only, not exposed here. |
| `GET /api/v1/runs/<slug>` | Status, budget, remaining fractions, open gates, recent jobs and events. |
| `GET /api/v1/runs/<slug>/events` | The run's history, oldest first (`?since=<id>`, repeatable `?type=` with `job.*`-style prefix matching). |
| `GET /api/v1/runs/<slug>/jobs` | Every job the run started. |
| `GET /api/v1/runs/<slug>/charter` | The current charter text plus its whole version history — `service.run_charter`, same function `scieflow run charter <slug>` calls. |
| `GET /api/v1/runs/<slug>/conversation` | The run's conversation: which agent is holding it, its session state, whether a turn is in flight, and every turn so far — `service.conversation_state`. |
| `POST /api/v1/runs/<slug>/conversation` | Send one message; the reply is a sandboxed job that resumes the agent's session — `service.say`. There is no CLI equivalent: the conversation is browser-only today. |
| `POST /api/v1/runs/<slug>/conversation/agent` | Hand the conversation to a different agent, clearing its recorded session — `service.set_conversation_agent`. |
| `GET /api/v1/gates` | Gates still waiting for an answer, optionally `?slug=<run>`. |
| `GET /api/v1/agents` | Effective role assignments and agent settings, with their sources. |
| `GET /api/v1/runs/<slug>/events/stream` | Server-sent events: the timeline, replayed then followed live. |
| `GET /api/v1/jobs/<job_id>/log/stream` | Server-sent events: one frame per line of a job's stdout, as it is written. |
| `POST /api/v1/runs/<slug>/phase` | Set a phase's state — `service.mark_phase`, same as `scieflow run mark`. |
| `POST /api/v1/runs/<slug>/advance` | Start the next iteration — `service.advance_run`, same as `scieflow run advance`. |
| `POST /api/v1/runs/<slug>/checkpoint` | Stop the run gracefully — `service.checkpoint_run`, same as `scieflow run checkpoint`. |
| `POST /api/v1/runs/<slug>/resume` | Clear a stop — `service.resume_run`, same as `scieflow run resume`. |
| `POST /api/v1/runs/<slug>/spend` | Record spend the runner can't measure — `service.record_spend`, same as `scieflow run spend`. |
| `POST /api/v1/runs/<slug>/gates/<gate_id>/answer` | Answer an open gate as the human — `service.answer_gate`, same as `scieflow gate answer`. |
| `POST /api/v1/runs/<slug>/charter` | Replace the charter, keeping the old version in its history — `service.set_charter`, same function `scieflow run charter <slug> --set` calls. |
| `POST /api/v1/runs/<slug>/charter/revert` | Make an earlier version current again by appending a copy of it — `service.revert_charter`, same function `scieflow run charter <slug> --revert` calls. |
| `POST /api/v1/jobs/<job_id>/cancel` | Cancel a running job and its whole process group — `service.cancel_job`; no CLI command mirrors this one (see "What it is" above). |

Every `POST` above needs the CSRF header as well as the session cookie (see
[Security model](#security-model)); the HTML pages use the equivalent
`/runs/<slug>/...` and `/agents` routes instead, which redirect back to a
page rather than returning JSON. Both sets — and no others — are exactly
the mutating-route inventory `tests/web/test_read_only.py` enforces.

The generated OpenAPI schema is at `/api/v1/openapi.json`, and interactive
docs (Swagger UI) are at `/api/v1/docs` — both need the session cookie too.

A session cookie can't be attached with a single `curl` flag the way a
browser attaches it automatically, so scripting the API means capturing the
cookie jar from the token exchange first:

```bash
curl -c cookies.txt "http://127.0.0.1:8765/healthz?token=<token from the printed URL>"
curl -b cookies.txt http://127.0.0.1:8765/api/v1/runs
```

## What stays CLI-only

A few things are terminal-only on purpose, not because nobody got to them:

- **`scieflow chats push` / `chats pull`** — a chat bundle is encrypted with
  your passphrase, and `AGENTS.md` rule 16 forbids an agent starting a backup
  or restore on its own initiative. A prompt for a passphrase has no honest
  place in a browser form. See [Chat backups](chats/index.md).
- **DVC uploads** — a run's workspace can be gigabytes, and the sync rule
  (`AGENTS.md`) requires asking before anything of that size moves. See
  [DVC storage](DVC_STORAGE.md).
- **`--promote`** — the exception that lets a support-tier agent stand in
  for a primary on one role. It exists to be deliberate and explicit (see
  [Agent configuration](agents.md#support-agents-as-primary-per-role)); a
  button that could do it in one click would defeat that purpose.
- **Per-agent field edits, promotions and demotions on the Agents page.**
  The page (below) covers role assignment only — see
  ["The Agents page"](agents.md#the-agents-page). Changing a field like
  `timeout_min`, or granting/removing a `--promote` exception, is still
  `scieflow agent configure`.

Starting a run used to belong on this list; it no longer does — see the
Start page above. Once a run exists, one way or the other, everything else
on this page applies to it, including its conversation.
