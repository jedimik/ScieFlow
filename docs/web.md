# The local web app

## What it is

`scieflow serve` is a browser view over the same service layer the CLI and
the coordinator agent use (`scieflow.core.service` — see
[Architecture](architecture.md)). Every route in `scieflow.web` is a thin
caller of that layer and holds no logic of its own, so the app can never
show you a different truth than `scieflow run show` or `scieflow gate list`
would.

This milestone's app is **read-only**. You can watch a run — its status,
budget, timeline, job output, gates and artifacts — but you cannot act on
one from the browser yet: answering a gate, starting a run and changing
agent configuration are all still terminal-only. See
["What it does not do yet"](#what-it-does-not-do-yet) below.

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
  cookie. Any other method also needs the `X-CSRF-Token` header to match the
  `scieflow_csrf` cookie's value (`auth.csrf_protect`) — a cookie alone,
  which a browser attaches automatically, is not enough to make a
  state-changing request.
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
| Dashboard | `/` | Every run (slug, kind, phase), a budget bar per dimension, and every open gate across all runs. |
| Run page | `/runs/<slug>` | The run's id, iteration and approval mode; its phases; budget remaining per dimension; open gates (with the `scieflow gate answer` command to use, since answering here is not built yet); every job it started, linked to its output; and a timeline of the run's events, updated live. |
| Job output | `/runs/<slug>/jobs/<job_id>` | The job's command, state, exit code and duration, and its captured stdout/stderr. While the job is still running, output streams in live. |
| Artifact browser | `/runs/<slug>/files[?path=...]` | A directory listing under the run; `/runs/<slug>/file?path=...` renders a small text file inline or downloads anything else, per the security model above. |

The run page's timeline and a running job's output are both live: each page
opens a browser `EventSource` against the matching `/api/v1` stream (below)
and appends new rows/lines as they arrive, with no page reload and no extra
JavaScript framework.

## The API

Everything under `/api/v1` mirrors the service layer as JSON, requires the
same session cookie as the pages, and is meant for other tools, not just
this app's own pages — it is a stable-enough surface to script against.

| Route | Gives you |
|---|---|
| `GET /api/v1/runs` | Every run: slug, kind, phase, state, last activity. |
| `GET /api/v1/runs/<slug>` | Status, budget, remaining fractions, open gates, recent jobs and events. |
| `GET /api/v1/runs/<slug>/events` | The run's history, oldest first (`?since=<id>`, repeatable `?type=` with `job.*`-style prefix matching). |
| `GET /api/v1/runs/<slug>/jobs` | Every job the run started. |
| `GET /api/v1/gates` | Gates still waiting for an answer, optionally `?slug=<run>`. |
| `GET /api/v1/agents` | Effective role assignments and agent settings, with their sources. |
| `GET /api/v1/runs/<slug>/events/stream` | Server-sent events: the timeline, replayed then followed live. |
| `GET /api/v1/jobs/<job_id>/log/stream` | Server-sent events: one frame per line of a job's stdout, as it is written. |

The generated OpenAPI schema is at `/api/v1/openapi.json`, and interactive
docs (Swagger UI) are at `/api/v1/docs` — both need the session cookie too.

A session cookie can't be attached with a single `curl` flag the way a
browser attaches it automatically, so scripting the API means capturing the
cookie jar from the token exchange first:

```bash
curl -c cookies.txt "http://127.0.0.1:8765/healthz?token=<token from the printed URL>"
curl -b cookies.txt http://127.0.0.1:8765/api/v1/runs
```

## What it does not do yet

This milestone is deliberately read-only. The following are all
terminal-only for now, and arrive with the control milestone (M2c):

- **Answering a gate** — use `uv run scieflow gate answer` (see
  [`docs/cli.md`](cli.md#scieflow-gate)). The run page shows you the exact
  command for each open gate.
- **Starting a run** — use `uv run scieflow run init` (see
  [`docs/cli.md`](cli.md#scieflow-run)).
- **Changing agent configuration** — use `uv run scieflow agent configure`
  (see [`docs/cli.md`](cli.md#scieflow-agent) and
  [Agent configuration](agents.md)).

Dispatching a coordinator headless from the browser, and browser/`ntfy`
notifications, are also deferred to the control milestone.
