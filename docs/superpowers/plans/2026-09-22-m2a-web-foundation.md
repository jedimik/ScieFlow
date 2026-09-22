# M2a — Local web app foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scieflow serve` opens a loopback-only web app that shows every run — status, budget, timeline, jobs with live logs, open gates and artifacts — over a JSON API that mirrors the service layer.

**Architecture:** One FastAPI application (`scieflow.web`) whose every route is a thin caller of `scieflow.core.service`, so the browser sees exactly what the CLI and the coordinator agent see. Pages are server-rendered Jinja templates; live output uses the browser's built-in `EventSource` against SSE endpoints. A one-time token in the printed URL becomes an httponly session cookie (Jupyter style), and unsafe methods need a double-submit CSRF header — the machinery ships here so the control plan (M2b) only uses it.

**Tech Stack:** FastAPI, uvicorn, Jinja2 (new `web` extra), `httpx` in the dev group for `TestClient`. No JavaScript framework and no CDN: the app must work with no network. htmx arrives in M2b where forms and partial swaps earn it.

**Spec:** `docs/superpowers/specs/2026-09-22-core-roadmap.md`, "Milestone 2 — local web app (FastAPI + htmx), full control".

## Global Constraints

- **Loopback only.** Bind `127.0.0.1`; refuse any non-loopback host with an error naming SSH tunnel / Tailscale as the remote path. No CORS headers, ever.
- **Token then cookie.** `scieflow serve` prints `http://127.0.0.1:<port>/?token=<token>`. The token is compared with `secrets.compare_digest`, exchanged for an httponly session cookie, and stripped from the URL by a redirect.
- **CSRF on every unsafe method.** Double-submit: a non-httponly `scieflow_csrf` cookie must equal the `X-CSRF-Token` header on POST/PUT/PATCH/DELETE.
- **The service layer is the only way in.** Routes call `scieflow.core.service`; no route re-reads `status.yml`, `events.jsonl` or a job record directly. Add a service function when one is missing.
- **Read-only milestone.** This plan changes no run state. Answering gates, starting runs and applying agent config are M2b.
- **No network at runtime or test time.** Every asset is served from `src/scieflow/web/static/`.
- **Nothing regresses.** `uv run pytest -q` stays green (857 passing at the start of this plan), `scripts/check_legacy.sh` stays all-`ok`, `uv run --group docs mkdocs build --strict` stays at 0 warnings.
- **Python ≥ 3.11** (`requires-python` in `pyproject.toml`).

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/web/__init__.py` | package marker, re-exports `create_app` |
| `src/scieflow/web/app.py` | `create_app(project, token)`: application factory, state, routers, error mapping |
| `src/scieflow/web/auth.py` | loopback guard, token generation, session middleware, CSRF dependency |
| `src/scieflow/web/api.py` | `/api/v1` JSON routes over the service layer |
| `src/scieflow/web/pages.py` | HTML routes: dashboard, run detail, artifact preview |
| `src/scieflow/web/files.py` | artifact path resolution (traversal-proof) and listing |
| `src/scieflow/web/sse.py` | `text/event-stream` endpoints for events and job logs |
| `src/scieflow/web/serve.py` | the `scieflow serve` click command |
| `src/scieflow/web/templates/*.html` | `base.html`, `dashboard.html`, `run.html`, `file.html`, `unauthorized.html` |
| `src/scieflow/web/static/app.css` | the only stylesheet |
| `src/scieflow/core/browser.py` | `open_url(url)` — WSL-aware browser opening, no extra dependency |
| `tests/web/conftest.py` | a tmp project with one populated run, plus a `client` fixture |
| `tests/web/test_serve.py`, `test_auth.py`, `test_api.py`, `test_pages.py`, `test_files.py`, `test_sse.py`, `test_smoke.py` | one file per surface |

---

### Task 1: The `web` extra, the app factory and `scieflow serve`

**Files:**
- Create: `src/scieflow/web/__init__.py`, `src/scieflow/web/app.py`, `src/scieflow/web/auth.py`, `src/scieflow/web/serve.py`, `src/scieflow/core/browser.py`
- Create: `src/scieflow/web/templates/base.html`, `src/scieflow/web/static/app.css`
- Modify: `pyproject.toml` (new `web` extra, `httpx` in the dev group), `src/scieflow/cli.py` (`GROUPS["serve"]`)
- Test: `tests/web/test_serve.py`

**Interfaces:**
- Consumes: `scieflow.core.project.Project`.
- Produces: `auth.loopback_only(host: str) -> str` (raises `ValueError`); `auth.new_token() -> str`;
  `app.create_app(project: Project, token: str) -> FastAPI` (exposes `app.state.project`, `app.state.token`, `app.state.sessions: set[str]`);
  `serve.serve` (click command, `--host/--port/--no-browser`);
  `browser.open_url(url: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_serve.py
import pytest
from click.testing import CliRunner

from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app
from scieflow.web.serve import serve


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host):
    assert auth.loopback_only(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_is_refused(host):
    with pytest.raises(ValueError, match="loopback"):
        auth.loopback_only(host)


def test_serve_refuses_a_non_loopback_bind(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    result = CliRunner().invoke(serve, ["--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "loopback" in result.output and "tunnel" in result.output


def test_tokens_are_long_and_unique():
    tokens = {auth.new_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_healthz_needs_no_session(tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    app = create_app(Project(tmp_path), "tok")
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # liveness only: never leak the filesystem layout or the token
    assert str(tmp_path) not in response.text and "tok" not in response.text


def test_no_cors_headers_are_ever_sent(tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    app = create_app(Project(tmp_path), "tok")
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_serve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scieflow.web'` (and, before that, `fastapi` is not installed yet).

- [ ] **Step 3: Add the dependencies**

In `pyproject.toml`, add the extra after the `chats` extra:

```toml
# Local web app (scieflow.web): `scieflow serve`, loopback only.
web = [
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
]
```

and add `httpx>=0.27` to `[dependency-groups] dev` (FastAPI's `TestClient` needs it):

```toml
dev = ["pytest>=8", "responses>=0.25", "pytest-asyncio>=0.23", "httpx>=0.27"]
```

Then install: `uv sync --all-extras`.

- [ ] **Step 4: Write the implementation**

```python
# src/scieflow/core/browser.py
"""Open a URL in the user's browser, including from WSL.

WSL has no usable default browser: `webbrowser.open` silently does nothing
there, so try the Windows helpers first. Never raise — failing to open a
browser must not take down the command that printed the URL.
"""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from pathlib import Path

WSL_OPENERS = ("wslview", "explorer.exe")


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_url(url: str) -> bool:
    """True when something was launched. Best effort by design."""
    if is_wsl():
        for opener in WSL_OPENERS:
            path = shutil.which(opener)
            if path:
                try:
                    subprocess.Popen([path, url], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    continue
        return False
    try:
        return webbrowser.open(url)
    except Exception:       # a broken BROWSER env must not crash `serve`
        return False
```

```python
# src/scieflow/web/__init__.py
"""The local web app — `scieflow serve`. Loopback only, one user, no CORS."""

from scieflow.web.app import create_app

__all__ = ["create_app"]
```

```python
# src/scieflow/web/auth.py
"""Loopback session auth: a one-time token in the URL becomes a cookie.

`scieflow serve` prints http://127.0.0.1:<port>/?token=<token>, Jupyter
style. Opening it checks the token in constant time, issues an httponly
session cookie and redirects to the same path without the token, so the
token never lingers in history, a bookmark or a referrer. Unsafe methods
additionally need the double-submit CSRF header.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = "scieflow_session"
CSRF_COOKIE = "scieflow_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})
OPEN_PATHS = frozenset({"/healthz"})


def loopback_only(host: str) -> str:
    """The only bind this app accepts. Remote access is a tunnel's job."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"refusing to bind {host!r}: the ScieFlow web app serves loopback "
            "only. For another machine, forward the port over an SSH tunnel "
            "or Tailscale instead of exposing it."
        )
    return host


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _issue_session(app, response) -> None:
    session = secrets.token_urlsafe(32)
    app.state.sessions.add(session)
    response.set_cookie(SESSION_COOKIE, session, httponly=True,
                        samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False,
                        samesite="strict", path="/")


def has_session(request: Request) -> bool:
    session = request.cookies.get(SESSION_COOKIE)
    return bool(session) and session in request.app.state.sessions


def require_session(request: Request) -> None:
    """FastAPI dependency: 401 unless this request carries a live session."""
    if not has_session(request):
        raise HTTPException(status_code=401, detail="no session; open the URL "
                                                    "printed by `scieflow serve`")


def csrf_protect(request: Request) -> None:
    """FastAPI dependency for unsafe methods: double-submit cookie check."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    header = request.headers.get(CSRF_HEADER) or ""
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF token missing or wrong")


def install_session(app) -> None:
    """Register the token → cookie exchange as middleware.

    Middleware, not a dependency, because the exchange has to happen for
    every path — pages and API alike — before routing decides anything.
    """

    @app.middleware("http")
    async def _session(request: Request, call_next):
        supplied = request.query_params.get("token")
        if supplied is not None:
            if not secrets.compare_digest(supplied, request.app.state.token):
                return JSONResponse({"error": "bad token"}, status_code=403)
            clean = str(request.url.remove_query_params("token"))
            response = RedirectResponse(clean, status_code=303)
            _issue_session(request.app, response)
            return response
        return await call_next(request)
```

```python
# src/scieflow/web/app.py
"""The application factory: one FastAPI app over the service layer.

Every route is a thin caller of `scieflow.core.service`, so the browser, the
CLI and the coordinator agent cannot drift apart. Created by
`scieflow serve`; tests build one directly with `create_app(project, token)`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scieflow.core.project import Project
from scieflow.core.service import ServiceError
from scieflow.web import auth

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app(project: Project, token: str) -> FastAPI:
    app = FastAPI(
        title="ScieFlow",
        summary="Local control surface for agent-driven research runs.",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.state.project = project
    app.state.token = token
    app.state.sessions = set()
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    auth.install_session(app)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        """Liveness only — deliberately says nothing about the project."""
        return {"ok": True}

    return app


def project_of(request: Request) -> Project:
    return request.app.state.project
```

```python
# src/scieflow/web/serve.py
"""`scieflow serve` — run the local web app."""

from __future__ import annotations

import click

from scieflow.core.browser import open_url
from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app

DEFAULT_PORT = 8765


@click.command("serve")
@click.option("--port", default=DEFAULT_PORT, show_default=True,
              help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Loopback address to bind; anything else is refused.")
@click.option("--no-browser", is_flag=True, help="Print the URL, open nothing.")
def serve(port: int, host: str, no_browser: bool) -> None:
    """Open the local web app: runs, timelines, jobs, gates (loopback only)."""
    import uvicorn

    try:
        host = auth.loopback_only(host)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    project = Project.discover()
    token = auth.new_token()
    app = create_app(project, token)
    url = f"http://{host}:{port}/?token={token}"
    click.echo(f"ScieFlow — {project.root}")
    click.echo(f"Open: {url}")
    click.echo("The token in that URL is this session's key; it becomes a cookie "
               "on first open. Ctrl-C to stop.")
    if not no_browser:
        open_url(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
```

`src/scieflow/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{% block title %}ScieFlow{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header>
    <a class="brand" href="/">ScieFlow</a>
    <nav>{% block nav %}{% endblock %}</nav>
  </header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`src/scieflow/web/static/app.css`:

```css
:root {
  --bg: #12141a; --fg: #e6e8ee; --dim: #9aa3b2; --line: #262b36;
  --ok: #4ac07a; --warn: #e0b341; --bad: #e06c6c; --link: #7aa7ff;
  font-family: ui-monospace, "JetBrains Mono", SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: light) {
  :root { --bg: #fbfbfd; --fg: #1a1d24; --dim: #5d6472; --line: #dfe3ea; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font-size: 14px; }
header { display: flex; gap: 1.5rem; align-items: baseline;
         padding: .9rem 1.25rem; border-bottom: 1px solid var(--line); }
.brand { font-weight: 700; text-decoration: none; color: var(--fg); }
main { padding: 1.25rem; max-width: 1100px; }
a { color: var(--link); }
h1 { font-size: 1.15rem; } h2 { font-size: 1rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line); }
th { color: var(--dim); font-weight: 500; }
.dim { color: var(--dim); }
.state-done, .state-ok { color: var(--ok); }
.state-running { color: var(--warn); }
.state-failed, .state-timeout, .state-lost { color: var(--bad); }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; }
.bar > span { display: block; height: 100%; background: var(--ok); }
.bar.low > span { background: var(--bad); }
pre.log { background: #0b0d12; color: #d7dbe4; padding: .8rem; overflow: auto;
          max-height: 26rem; border-radius: 4px; }
.gate { border: 1px solid var(--line); border-left: 3px solid var(--warn);
        padding: .6rem .8rem; margin: .5rem 0; }
.gate.human { border-left-color: var(--bad); }
```

In `src/scieflow/cli.py`, add to `GROUPS` after `"run"`:

```python
    "serve": ("scieflow.web.serve", "serve", "web",
              "Open the local web app (loopback only)."),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_serve.py -v && uv run pytest -q`
Expected: 8 passed in the new file; full suite green.
Also check by hand: `uv run scieflow serve --help` shows the options, and `uv run scieflow serve --host 0.0.0.0` refuses.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/scieflow/web src/scieflow/core/browser.py src/scieflow/cli.py tests/web/test_serve.py
git commit -m "feat(web): loopback-only app factory and scieflow serve

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Session and CSRF enforcement

**Files:**
- Modify: `src/scieflow/web/app.py` (mount an authenticated test-visible router is NOT needed; only the handler for unauthorized HTML)
- Create: `src/scieflow/web/templates/unauthorized.html`
- Test: `tests/web/test_auth.py`

**Interfaces:**
- Consumes: `auth.require_session`, `auth.csrf_protect`, `auth.install_session` from Task 1.
- Produces: an `HTTPException(401)` handler that returns HTML for browser requests and JSON for API paths; the guarantee that `require_session` + `csrf_protect` are the only auth primitives later tasks use.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_auth.py
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app

TOKEN = "s3cret-token"


@pytest.fixture
def app(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    application = create_app(Project(tmp_path), TOKEN)

    # exercise the real dependencies through routes that exist only in tests
    @application.get("/guarded", dependencies=[Depends(auth.require_session)])
    async def guarded() -> dict:
        return {"seen": True}

    @application.post("/guarded", dependencies=[Depends(auth.require_session),
                                                Depends(auth.csrf_protect)])
    async def guarded_post() -> dict:
        return {"posted": True}

    return application


def test_no_session_is_401(app):
    with TestClient(app) as client:
        assert client.get("/guarded").status_code == 401


def test_wrong_token_is_403_and_issues_nothing(app):
    with TestClient(app) as client:
        response = client.get("/guarded?token=wrong", follow_redirects=False)
        assert response.status_code == 403
        assert auth.SESSION_COOKIE not in response.cookies
        assert client.get("/guarded").status_code == 401


def test_good_token_redirects_without_the_token_and_sets_cookies(app):
    with TestClient(app) as client:
        response = client.get(f"/guarded?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303
        assert "token" not in response.headers["location"]
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith(auth.SESSION_COOKIE))
        assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
        # the cookie now stands in for the token
        assert client.get("/guarded").json() == {"seen": True}


def test_session_cookie_is_not_the_token(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        assert client.cookies[auth.SESSION_COOKIE] != TOKEN


def test_post_without_csrf_header_is_refused(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        assert client.post("/guarded").status_code == 403


def test_post_with_a_wrong_csrf_header_is_refused(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        response = client.post("/guarded", headers={auth.CSRF_HEADER: "nope"})
        assert response.status_code == 403


def test_post_with_the_matching_csrf_header_is_allowed(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        csrf = client.cookies[auth.CSRF_COOKIE]
        response = client.post("/guarded", headers={auth.CSRF_HEADER: csrf})
        assert response.status_code == 200 and response.json() == {"posted": True}


def test_unauthorized_html_explains_how_to_get_in(app):
    with TestClient(app) as client:
        response = client.get("/guarded", headers={"Accept": "text/html"})
        assert response.status_code == 401
        assert "scieflow serve" in response.text
        assert TOKEN not in response.text          # never echo the secret
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: the HTML test FAILs (a JSON body, no "scieflow serve"); the rest pass from Task 1's implementation.

- [ ] **Step 3: Write the implementation**

`src/scieflow/web/templates/unauthorized.html`:

```html
{% extends "base.html" %}
{% block title %}ScieFlow — not signed in{% endblock %}
{% block content %}
<h1>Not signed in</h1>
<p>This app is reachable only from this machine, and only with the session
key printed when you started it.</p>
<p>Open the URL that <code>scieflow serve</code> printed in your terminal — it
carries a one-time token that becomes a cookie. If you have lost it, stop the
server and start it again.</p>
{% endblock %}
```

In `src/scieflow/web/app.py`, add inside `create_app`, after the `ServiceError` handler:

```python
    from fastapi.exceptions import HTTPException as FastAPIHTTPException

    @app.exception_handler(FastAPIHTTPException)
    async def _http_error(request: Request, exc: FastAPIHTTPException):
        wants_html = ("text/html" in request.headers.get("accept", "")
                      and not request.url.path.startswith("/api/"))
        if exc.status_code == 401 and wants_html:
            return TEMPLATES.TemplateResponse(
                request, "unauthorized.html", status_code=401)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_auth.py -v && uv run pytest -q`
Expected: 8 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_auth.py
git commit -m "feat(web): session cookie exchange, CSRF double-submit, HTML 401

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `/api/v1` over the service layer

**Files:**
- Create: `src/scieflow/web/api.py`, `tests/web/conftest.py`
- Modify: `src/scieflow/web/app.py` (include the router)
- Test: `tests/web/test_api.py`

**Interfaces:**
- Consumes: `service.list_runs`, `service.run_detail`, `service.run_events`, `service.open_gates`, `service.agent_settings`, `jobs.list_jobs`, `auth.require_session`.
- Produces: `api.router` (prefix `/api/v1`, session-guarded) with
  `GET /runs`, `GET /runs/{slug}`, `GET /runs/{slug}/events`, `GET /runs/{slug}/jobs`,
  `GET /gates`, `GET /agents`; and the shared `tests/web/conftest.py` fixtures
  `project` (a tmp project with run `r1`) and `client` (a signed-in `TestClient`).

- [ ] **Step 1: Write the failing test**

```python
# tests/web/conftest.py
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scieflow.core import events, gates, jobs
from scieflow.core.project import Project
from scieflow.core.run import budget, status
from scieflow.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token"
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    """A project with one run that has state, budget, events, a job and a gate."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "assignments:\n  research.outline: stub\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())

    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    project = Project(tmp_path)

    events.emit(ws, "run.created", "human", slug="r1")
    events.emit(ws, "phase.started", "agent", phase="hypothesize")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    jobs.run_blocking(project, [sys.executable, "-c", "print('hello from the job')"],
                      kind="agent", cwd=tmp_path, run_dir=ws, label="stub")
    gates.open_gate(project, ws, "question", "Which dataset?", options=["A", "B"])
    return project


@pytest.fixture
def client(project):
    with TestClient(create_app(project, TOKEN)) as signed_in:
        signed_in.get(f"/healthz?token={TOKEN}")     # exchange token for cookies
        yield signed_in
```

```python
# tests/web/test_api.py
def test_runs_lists_the_run(client):
    body = client.get("/api/v1/runs").json()
    assert [r["slug"] for r in body] == ["r1"]


def test_run_detail_matches_the_service_layer(client, project):
    from scieflow.core import service

    body = client.get("/api/v1/runs/r1").json()
    assert body["status"]["run"] == "r1"
    assert body["remaining"]["iterations"] == 1.0
    assert body == service.run_detail(project, "r1")


def test_unknown_run_is_404_with_a_message(client):
    response = client.get("/api/v1/runs/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["error"]


def test_events_can_be_filtered_by_type(client):
    all_events = client.get("/api/v1/runs/r1/events").json()
    assert [e["type"] for e in all_events][:2] == ["run.created", "phase.started"]
    only_jobs = client.get("/api/v1/runs/r1/events", params={"type": "job.*"}).json()
    assert only_jobs and all(e["type"].startswith("job.") for e in only_jobs)


def test_events_since_returns_the_tail(client):
    all_events = client.get("/api/v1/runs/r1/events").json()
    tail = client.get("/api/v1/runs/r1/events",
                      params={"since": all_events[0]["id"]}).json()
    assert len(tail) == len(all_events) - 1


def test_jobs_lists_the_finished_job(client):
    body = client.get("/api/v1/runs/r1/jobs").json()
    assert len(body) == 1 and body[0]["state"] == "done"
    assert body[0]["duration_s"] is not None


def test_open_gates_across_runs(client):
    body = client.get("/api/v1/gates").json()
    assert len(body) == 1
    assert body[0]["slug"] == "r1" and body[0]["question"] == "Which dataset?"


def test_agents_reports_assignments(client):
    body = client.get("/api/v1/agents").json()
    assert body["assignments"]["research.outline"]["value"] == "stub"


def test_api_requires_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.get("/api/v1/runs").status_code == 401


def test_openapi_schema_is_served(client):
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/runs/{slug}" in schema["paths"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_api.py -v`
Expected: FAIL — every route 404s (`scieflow.web.api` does not exist).

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/web/api.py
"""/api/v1 — the service layer as JSON.

Routes translate HTTP into a service call and back; they hold no logic of
their own, so the browser, the CLI and other apps cannot see different
answers. FastAPI generates the OpenAPI schema at /api/v1/openapi.json, which
is the integration surface other tools use.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from scieflow.core import jobs, service
from scieflow.web import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_session)])


def _project(request: Request):
    return request.app.state.project


@router.get("/runs", tags=["runs"])
async def list_runs(request: Request) -> list[dict]:
    """Every run: slug, kind, phase, state, last activity."""
    return service.list_runs(_project(request))


@router.get("/runs/{slug}", tags=["runs"])
async def run_detail(request: Request, slug: str) -> dict:
    """Status, budget, remaining fractions, open gates, recent jobs and events."""
    return service.run_detail(_project(request), slug)


@router.get("/runs/{slug}/events", tags=["runs"])
async def run_events(request: Request, slug: str,
                     since: str | None = Query(default=None,
                                               description="Only events after this id."),
                     type: list[str] = Query(default=[],  # noqa: A002 - the query name
                                             description="Type filter; 'job.*' matches a prefix.")
                     ) -> list[dict]:
    """The run's history, oldest first."""
    return service.run_events(_project(request), slug, since=since, types=tuple(type))


@router.get("/runs/{slug}/jobs", tags=["runs"])
async def run_jobs(request: Request, slug: str) -> list[dict]:
    """Every job this run started, newest last."""
    from dataclasses import asdict

    project = _project(request)
    ws = service._ws(project, slug)           # raises ServiceError -> 404
    return [asdict(job) for job in jobs.list_jobs(project, ws)]


@router.get("/gates", tags=["gates"])
async def open_gates(request: Request,
                     slug: str | None = Query(default=None,
                                              description="Limit to one run.")
                     ) -> list[dict]:
    """Gates still waiting for an answer, across every run."""
    return service.open_gates(_project(request), slug)


@router.get("/agents", tags=["agents"])
async def agent_settings(request: Request,
                         slug: str | None = Query(default=None,
                                                  description="Resolve for this run.")
                         ) -> dict:
    """Effective role assignments and agent settings, with their sources."""
    return service.agent_settings(_project(request), slug)
```

`service._ws` is module-private; rather than reaching into it, add a public
alias in `src/scieflow/core/service.py` right after `_ws`:

```python
def run_workspace(project: Project, slug: str) -> Path:
    """The run's directory, or ServiceError — the public form of `_ws`."""
    return _ws(project, slug)
```

and use `service.run_workspace(project, slug)` in `run_jobs`.

In `src/scieflow/web/app.py`, include the router before returning the app:

```python
    from scieflow.web import api

    app.include_router(api.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all web tests pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web src/scieflow/core/service.py tests/web
git commit -m "feat(web): /api/v1 read endpoints mirroring the service layer

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The dashboard page

**Files:**
- Create: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/dashboard.html`
- Modify: `src/scieflow/web/app.py` (include the pages router last, so `/` is the dashboard)
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `service.list_runs`, `service.open_gates`, `auth.require_session`, `app.TEMPLATES`.
- Produces: `pages.router` (no prefix, session-guarded) with `GET /` rendering `dashboard.html`;
  the template context keys `runs` (list of run dicts) and `gates` (list of gate dicts each carrying `slug`).

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_pages.py
def test_dashboard_lists_runs_and_open_gates(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "r1" in body
    assert "Which dataset?" in body
    assert '<a href="/runs/r1"' in body


def test_dashboard_shows_budget_remaining(client):
    body = client.get("/").text
    # 0 of 3 iterations spent -> a full bar, labelled
    assert "iterations" in body and "100%" in body


def test_dashboard_needs_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        response = anonymous.get("/", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert "scieflow serve" in response.text


def test_dashboard_with_no_runs_says_so(tmp_path):
    from fastapi.testclient import TestClient

    from scieflow.core.project import Project
    from scieflow.web.app import create_app

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    with TestClient(create_app(Project(tmp_path), "tok")) as client:
        client.get("/healthz?token=tok")
        body = client.get("/").text
    assert "No runs yet" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_pages.py -v`
Expected: FAIL — `GET /` returns 404 (`scieflow.web.pages` does not exist).

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/web/pages.py
"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from scieflow.core import service
from scieflow.web import auth
from scieflow.web.app import TEMPLATES

router = APIRouter(dependencies=[Depends(auth.require_session)])


def _project(request: Request):
    return request.app.state.project


def _percent(fraction: float | None) -> int:
    """A remaining fraction as a whole percent, clamped for the CSS bar."""
    if fraction is None:
        return 0
    return max(0, min(100, round(fraction * 100)))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    project = _project(request)
    runs = service.list_runs(project)
    detail = {}
    for run in runs:
        try:
            full = service.run_detail(project, run["slug"])
        except service.ServiceError:
            continue
        detail[run["slug"]] = {
            "remaining": {dim: _percent(value)
                          for dim, value in (full["remaining"] or {}).items()},
            "stopped": (full["status"] or {}).get("stopped"),
        }
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {
        "runs": runs,
        "detail": detail,
        "gates": service.open_gates(project),
    })
```

`src/scieflow/web/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}ScieFlow — runs{% endblock %}
{% block content %}
<h1>Runs</h1>
{% if not runs %}
  <p class="dim">No runs yet. Start one with
    <code>uv run scieflow run init &lt;slug&gt; --goal goal.md</code>.</p>
{% else %}
<table>
  <tr><th>run</th><th>kind</th><th>phase</th><th>budget left</th><th>updated</th></tr>
  {% for run in runs %}
  <tr>
    <td><a href="/runs/{{ run.slug }}">{{ run.slug }}</a></td>
    <td class="dim">{{ run.kind }}</td>
    <td class="state-{{ run.phase_state or 'none' }}">
      {{ run.phase or "—" }}{% if run.phase_state %} ({{ run.phase_state }}){% endif %}
    </td>
    <td>
      {% for dim, left in (detail[run.slug].remaining if run.slug in detail else {}).items() %}
        <div class="dim">{{ dim }} {{ left }}%</div>
        <div class="bar {% if left <= 10 %}low{% endif %}"><span style="width: {{ left }}%"></span></div>
      {% endfor %}
    </td>
    <td class="dim">{{ (run.updated_at or "")[:16] }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}

<h2>Open gates</h2>
{% if not gates %}
  <p class="dim">Nothing is waiting for you.</p>
{% else %}
  {% for gate in gates %}
  <div class="gate {% if gate.requires_human %}human{% endif %}">
    <a href="/runs/{{ gate.slug }}">{{ gate.slug }}</a>
    <span class="dim">{{ gate.kind }}</span>
    {% if gate.requires_human %}<span class="dim">[needs you]</span>{% endif %}
    <div>{{ gate.question }}</div>
  </div>
  {% endfor %}
{% endif %}
{% endblock %}
```

In `src/scieflow/web/app.py`, include the pages router after the API router:

```python
    from scieflow.web import api, pages

    app.include_router(api.router)
    app.include_router(pages.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_pages.py -v && uv run pytest -q`
Expected: 4 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_pages.py
git commit -m "feat(web): dashboard page with runs, budgets and open gates

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The run page — timeline, jobs, gates

**Files:**
- Modify: `src/scieflow/web/pages.py` (add `GET /runs/{slug}`)
- Create: `src/scieflow/web/templates/run.html`
- Test: `tests/web/test_pages.py` (append)

**Interfaces:**
- Consumes: `service.run_detail`, `service.run_workspace` (Task 3), `jobs.list_jobs`.
- Produces: `GET /runs/{slug}` rendering `run.html`; context keys `slug`, `detail`,
  `jobs` (newest first), `phases` (ordered `(name, state)` pairs), `remaining` (percent ints).

- [ ] **Step 1: Write the failing test** (append to `tests/web/test_pages.py`)

```python
def test_run_page_shows_status_jobs_gates_and_timeline(client):
    response = client.get("/runs/r1")
    assert response.status_code == 200
    body = response.text
    assert "hypothesize" in body                 # phases table
    assert "run.created" in body                 # timeline
    assert "done" in body                        # the finished job
    assert "Which dataset?" in body              # the open gate
    assert "answer" in body.lower()              # tells you how to answer it


def test_run_page_links_each_job_to_its_log(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    body = client.get("/runs/r1").text
    assert f"/runs/r1/jobs/{job.id}" in body


def test_unknown_run_page_is_404(client):
    assert client.get("/runs/nope").status_code == 404


def test_job_log_page_shows_the_output(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    body = client.get(f"/runs/r1/jobs/{job.id}").text
    assert "hello from the job" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_pages.py -v`
Expected: the four new tests FAIL with 404.

- [ ] **Step 3: Write the implementation**

Append to `src/scieflow/web/pages.py`:

```python
@router.get("/runs/{slug}", response_class=HTMLResponse)
async def run_page(request: Request, slug: str) -> HTMLResponse:
    from dataclasses import asdict

    from scieflow.core import jobs as jobs_mod

    project = _project(request)
    detail = service.run_detail(project, slug)          # ServiceError -> 404
    ws = service.run_workspace(project, slug)
    status = detail["status"] or {}
    return TEMPLATES.TemplateResponse(request, "run.html", {
        "slug": slug,
        "detail": detail,
        "phases": list((status.get("phases") or {}).items()),
        "remaining": {dim: _percent(value)
                      for dim, value in (detail["remaining"] or {}).items()},
        "jobs": [asdict(job) for job in reversed(jobs_mod.list_jobs(project, ws))],
    })


@router.get("/runs/{slug}/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, slug: str, job_id: str) -> HTMLResponse:
    from pathlib import Path

    from scieflow.core import jobs as jobs_mod

    project = _project(request)
    service.run_workspace(project, slug)                # validates the slug
    job = jobs_mod.find(project, job_id)
    if job is None:
        raise service.ServiceError(f"no job {job_id}")
    def _read(path: str) -> str:
        try:
            return Path(path).read_text(errors="replace")[-200_000:]
        except OSError:
            return ""
    from dataclasses import asdict

    return TEMPLATES.TemplateResponse(request, "job.html", {
        "slug": slug,
        "job": asdict(job),
        "out": _read(job.log),
        "err": _read(job.err),
        "live": job.state == "running",
    })
```

`src/scieflow/web/templates/run.html`:

```html
{% extends "base.html" %}
{% block title %}{{ slug }} — ScieFlow{% endblock %}
{% block nav %}<a href="/">← all runs</a>{% endblock %}
{% block content %}
<h1>{{ slug }}</h1>
<p class="dim">
  id {{ (detail.status or {}).get("id", "—") }} ·
  iteration {{ (detail.status or {}).get("iteration", "—") }} ·
  approval {{ (detail.status or {}).get("approval", "—") }}
</p>
{% if (detail.status or {}).get("stopped") %}
  <p class="state-failed">stopped: {{ detail.status.stopped.reason }}
     — {{ detail.status.stopped.get("detail", "") }}</p>
{% endif %}

<h2>Phases</h2>
<table>
  {% for phase, state in phases %}
  <tr><td>{{ phase }}</td><td class="state-{{ state }}">{{ state }}</td></tr>
  {% endfor %}
</table>

<h2>Budget</h2>
<table>
  {% for dim, left in remaining.items() %}
  <tr>
    <td>{{ dim }}</td>
    <td style="width: 60%">
      <div class="bar {% if left <= 10 %}low{% endif %}"><span style="width: {{ left }}%"></span></div>
    </td>
    <td class="dim">{{ left }}% left</td>
  </tr>
  {% endfor %}
</table>

<h2>Gates</h2>
{% if not detail.gates %}
  <p class="dim">No gate is waiting.</p>
{% else %}
  {% for gate in detail.gates %}
  <div class="gate {% if gate.requires_human %}human{% endif %}">
    <span class="dim">{{ gate.kind }}{% if gate.requires_human %} · needs you{% endif %}</span>
    <div>{{ gate.question }}</div>
    {% if gate.options %}<div class="dim">options: {{ gate.options|join(", ") }}</div>{% endif %}
    <div class="dim">answer it with
      <code>uv run scieflow gate answer {{ slug }} {{ gate.id }} &lt;answer&gt;</code>
      — answering from this page arrives with the control milestone.</div>
  </div>
  {% endfor %}
{% endif %}

<h2>Jobs</h2>
{% if not jobs %}
  <p class="dim">This run has started no jobs.</p>
{% else %}
<table>
  <tr><th>started</th><th>what</th><th>state</th><th>seconds</th><th></th></tr>
  {% for job in jobs %}
  <tr>
    <td class="dim">{{ (job.started or job.queued or "")[11:19] }}</td>
    <td>{{ job.label or job.kind }}</td>
    <td class="state-{{ job.state }}">{{ job.state }}</td>
    <td class="dim">{{ "%.1f"|format(job.duration_s) if job.duration_s else "—" }}</td>
    <td><a href="/runs/{{ slug }}/jobs/{{ job.id }}">output</a></td>
  </tr>
  {% endfor %}
</table>
{% endif %}

<h2>Timeline</h2>
<table id="timeline">
  {% for event in detail.events|reverse %}
  <tr>
    <td class="dim">{{ event.ts[11:19] }}</td>
    <td>{{ event.type }}</td>
    <td class="dim">{{ event.actor }}</td>
    <td class="dim">{{ event.data }}</td>
  </tr>
  {% endfor %}
</table>
<script>
  const timeline = document.getElementById("timeline");
  const stream = new EventSource("/api/v1/runs/{{ slug }}/events/stream");
  stream.addEventListener("run", (message) => {
    const event = JSON.parse(message.data);
    const row = timeline.insertRow(0);
    row.innerHTML = `<td class="dim">${event.ts.slice(11, 19)}</td>` +
                    `<td>${event.type}</td><td class="dim">${event.actor}</td>` +
                    `<td class="dim"></td>`;
  });
</script>
{% endblock %}
```

`src/scieflow/web/templates/job.html`:

```html
{% extends "base.html" %}
{% block title %}{{ job.label or job.kind }} — {{ slug }}{% endblock %}
{% block nav %}<a href="/runs/{{ slug }}">← {{ slug }}</a>{% endblock %}
{% block content %}
<h1>{{ job.label or job.kind }}</h1>
<p class="dim">
  {{ job.id }} · <span class="state-{{ job.state }}">{{ job.state }}</span>
  {% if job.exit_code is not none %} · exit {{ job.exit_code }}{% endif %}
  {% if job.duration_s %} · {{ "%.1f"|format(job.duration_s) }}s{% endif %}
</p>
<p class="dim"><code>{{ job.argv|join(" ") }}</code></p>

<h2>Output</h2>
<pre class="log" id="out">{{ out }}</pre>
{% if err %}<h2>Errors</h2><pre class="log">{{ err }}</pre>{% endif %}
{% if live %}
<script>
  const out = document.getElementById("out");
  const stream = new EventSource("/api/v1/jobs/{{ job.id }}/log/stream");
  stream.onmessage = (message) => {
    out.textContent += message.data + "\n";
    out.scrollTop = out.scrollHeight;
  };
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass. (The `EventSource` routes arrive in Task 7; the pages degrade to
their server-rendered content until then, which is what these tests assert.)

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_pages.py
git commit -m "feat(web): run page with phases, budget, gates, jobs and timeline

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Artifact browser that cannot escape the run

**Files:**
- Create: `src/scieflow/web/files.py`, `src/scieflow/web/templates/files.html`, `src/scieflow/web/templates/file.html`
- Modify: `src/scieflow/web/pages.py` (two routes), `src/scieflow/web/templates/run.html` (a link)
- Test: `tests/web/test_files.py`

**Interfaces:**
- Consumes: `service.run_workspace`.
- Produces: `files.resolve(ws: Path, relpath: str, allow_root: bool = False) -> Path` (raises `ValueError` on escape, `FileNotFoundError` when absent);
  `files.listing(ws: Path, relpath: str) -> list[dict]` with keys `name, path, is_dir, size`;
  `files.is_text(path: Path) -> bool`; `files.MAX_INLINE = 200_000`;
  routes `GET /runs/{slug}/files` and `GET /runs/{slug}/file`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_files.py
import pytest

from scieflow.web import files


def test_resolve_accepts_a_file_inside_the_run(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.md").write_text("hi")
    assert files.resolve(tmp_path, "a/x.md").name == "x.md"


@pytest.mark.parametrize("bad", ["../secrets.txt", "a/../../secrets.txt",
                                 "/etc/passwd", "a/../..", ""])
def test_resolve_refuses_anything_outside_the_run(tmp_path, bad):
    (tmp_path / "a").mkdir()
    (tmp_path.parent / "secrets.txt").write_text("nope")
    with pytest.raises(ValueError):
        files.resolve(tmp_path, bad)


def test_resolve_refuses_a_symlink_pointing_out(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        files.resolve(tmp_path, "link.txt")


def test_listing_sorts_directories_first(tmp_path):
    (tmp_path / "zdir").mkdir()
    (tmp_path / "a.md").write_text("x")
    names = [entry["name"] for entry in files.listing(tmp_path, "")]
    assert names == ["zdir", "a.md"]


def test_files_page_lists_the_run(client, project):
    ws = project.run_dir("r1")
    (ws / "iterations").mkdir(exist_ok=True)
    (ws / "iterations" / "hypothesis.md").write_text("# H1\n")
    body = client.get("/runs/r1/files").text
    assert "iterations" in body and "status.yml" in body


def test_text_file_is_shown_inline(client, project):
    (project.run_dir("r1") / "note.md").write_text("# a heading\n")
    body = client.get("/runs/r1/file", params={"path": "note.md"}).text
    assert "# a heading" in body


def test_binary_file_is_served_with_its_media_type(client, project):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (project.run_dir("r1") / "figure.png").write_bytes(png)
    response = client.get("/runs/r1/file", params={"path": "figure.png"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_traversal_through_the_route_is_refused(client, project):
    (project.root / "config" / "agents.yml").exists()
    response = client.get("/runs/r1/file", params={"path": "../../config/agents.yml"})
    assert response.status_code == 400
    assert "escapes" in response.text


def test_missing_file_is_404(client):
    assert client.get("/runs/r1/file", params={"path": "nope.md"}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_files.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scieflow.web.files'`.

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/web/files.py
"""Artifact access confined to one run directory.

The browser may read anything a run produced and nothing else. Every path
is resolved (which also follows symlinks) and then checked to be a strict
descendant of the run — so `..`, an absolute path and a symlink pointing out
of the workspace are all refused by the same test.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

MAX_INLINE = 200_000        # bytes of a text file rendered into a page
TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".log", ".err", ".json", ".jsonl", ".yml", ".yaml",
    ".csv", ".tex", ".bib", ".py", ".sh", ".toml", ".cfg", ".ini",
})


def resolve(ws: Path, relpath: str, allow_root: bool = False) -> Path:
    """The absolute path of `relpath` inside `ws`, or ValueError."""
    root = Path(ws).resolve()
    candidate = (root / relpath).resolve()
    if candidate == root:
        if allow_root:
            return candidate
        raise ValueError("path escapes the run: the run root is not a file")
    if root not in candidate.parents:
        raise ValueError(f"path escapes the run: {relpath!r}")
    if not candidate.exists():
        raise FileNotFoundError(relpath)
    return candidate


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def listing(ws: Path, relpath: str = "") -> list[dict]:
    """Directories first, then files, each sorted by name."""
    directory = resolve(ws, relpath, allow_root=True) if relpath else Path(ws).resolve()
    if not directory.is_dir():
        raise ValueError(f"not a directory: {relpath!r}")
    root = Path(ws).resolve()
    entries = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        if child.name.endswith(".lock"):
            continue
        entries.append({
            "name": child.name,
            "path": str(child.relative_to(root)),
            "is_dir": child.is_dir(),
            "size": child.stat().st_size if child.is_file() else 0,
        })
    return entries
```

Append to `src/scieflow/web/pages.py`:

```python
@router.get("/runs/{slug}/files", response_class=HTMLResponse)
async def files_page(request: Request, slug: str, path: str = "") -> HTMLResponse:
    from fastapi import HTTPException

    from scieflow.web import files as files_mod

    ws = service.run_workspace(_project(request), slug)
    try:
        entries = files_mod.listing(ws, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TEMPLATES.TemplateResponse(request, "files.html",
                                      {"slug": slug, "path": path, "entries": entries})


@router.get("/runs/{slug}/file")
async def file_view(request: Request, slug: str, path: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from scieflow.web import files as files_mod

    ws = service.run_workspace(_project(request), slug)
    try:
        target = files_mod.resolve(ws, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such file: {path}") from exc
    if target.is_dir():
        raise HTTPException(status_code=400, detail="that is a directory")
    if files_mod.is_text(target) and target.stat().st_size <= files_mod.MAX_INLINE:
        return TEMPLATES.TemplateResponse(request, "file.html", {
            "slug": slug, "path": path,
            "text": target.read_text(errors="replace"),
        })
    return FileResponse(target, media_type=files_mod.media_type(target))
```

`src/scieflow/web/templates/files.html`:

```html
{% extends "base.html" %}
{% block title %}{{ slug }} files{% endblock %}
{% block nav %}<a href="/runs/{{ slug }}">← {{ slug }}</a>{% endblock %}
{% block content %}
<h1>{{ slug }}/{{ path }}</h1>
<table>
  {% for entry in entries %}
  <tr>
    <td>
      {% if entry.is_dir %}
        <a href="/runs/{{ slug }}/files?path={{ entry.path }}">{{ entry.name }}/</a>
      {% else %}
        <a href="/runs/{{ slug }}/file?path={{ entry.path }}">{{ entry.name }}</a>
      {% endif %}
    </td>
    <td class="dim">{{ entry.size if not entry.is_dir else "" }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

`src/scieflow/web/templates/file.html`:

```html
{% extends "base.html" %}
{% block title %}{{ path }} — {{ slug }}{% endblock %}
{% block nav %}<a href="/runs/{{ slug }}/files">← files</a>{% endblock %}
{% block content %}
<h1>{{ path }}</h1>
<pre class="log">{{ text }}</pre>
{% endblock %}
```

In `run.html`, add under the `<h1>` line:

```html
<p><a href="/runs/{{ slug }}/files">browse this run's files →</a></p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_files.py
git commit -m "feat(web): artifact browser confined to the run directory

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Live streams over SSE

**Files:**
- Create: `src/scieflow/web/sse.py`
- Modify: `src/scieflow/web/app.py` (include the router)
- Test: `tests/web/test_sse.py`

**Interfaces:**
- Consumes: `service.run_workspace`, `service.run_events`, `jobs.find`.
- Produces: `sse.router` (prefix `/api/v1`, session-guarded) with
  `GET /runs/{slug}/events/stream?since=<id>` emitting named `run` events, and
  `GET /jobs/{job_id}/log/stream` emitting one `data:` frame per output line;
  `sse.POLL_S = 0.5`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_sse.py
import json


def read_frames(client, url, want, timeout=10.0):
    """Collect `want` data frames from an SSE endpoint, then disconnect."""
    frames = []
    with client.stream("GET", url, timeout=timeout) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                frames.append(line[len("data:"):].strip())
                if len(frames) >= want:
                    break
    return frames


def test_event_stream_replays_then_follows(client, project):
    from scieflow.core import events

    ws = project.run_dir("r1")
    events.emit(ws, "note.hello", "human", message="from the test")
    frames = read_frames(client, "/api/v1/runs/r1/events/stream", want=3)
    types = [json.loads(frame)["type"] for frame in frames]
    assert types[0] == "run.created"
    assert "note.hello" in types or len(types) == 3


def test_event_stream_since_skips_what_you_have(client, project):
    from scieflow.core import events

    ws = project.run_dir("r1")
    existing = events.read(ws)
    events.emit(ws, "note.later", "human", message="new")
    frames = read_frames(
        client, f"/api/v1/runs/r1/events/stream?since={existing[-1]['id']}", want=1)
    assert json.loads(frames[0])["type"] == "note.later"


def test_job_log_stream_sends_the_output(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    frames = read_frames(client, f"/api/v1/jobs/{job.id}/log/stream", want=1)
    assert frames[0] == "hello from the job"


def test_streams_need_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.get("/api/v1/runs/r1/events/stream").status_code == 401


def test_unknown_run_stream_is_404(client):
    assert client.get("/api/v1/runs/nope/events/stream").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_sse.py -v`
Expected: FAIL — every stream 404s (`scieflow.web.sse` does not exist).

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/web/sse.py
"""Server-sent events: the timeline and job output as they happen.

Polling a file is the right tool here — the writers are separate processes
(agents, sweeps, the CLI), so there is nothing in-process to subscribe to,
and `events.jsonl` and a job log are append-only. A client that goes away is
noticed through `request.is_disconnected()`, so a closed tab stops the loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from scieflow.core import jobs, service
from scieflow.web import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_session)])

POLL_S = 0.5
HEARTBEAT_EVERY = 20        # polls between `: ping` comments (~10s)
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def _events(request: Request, slug: str, since: str | None):
    project = request.app.state.project
    idle = 0
    while True:
        if await request.is_disconnected():
            return
        batch = service.run_events(project, slug, since=since)
        for event in batch:
            since = event["id"]
            yield f"event: run\ndata: {json.dumps(event)}\n\n"
        idle = 0 if batch else idle + 1
        if idle and idle % HEARTBEAT_EVERY == 0:
            yield ": ping\n\n"
        await asyncio.sleep(POLL_S)


async def _log(request: Request, path: Path):
    offset = 0
    idle = 0
    while True:
        if await request.is_disconnected():
            return
        chunk = ""
        if path.exists():
            with path.open("r", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        for line in chunk.splitlines():
            yield f"data: {line}\n\n"
        idle = 0 if chunk else idle + 1
        if idle and idle % HEARTBEAT_EVERY == 0:
            yield ": ping\n\n"
        await asyncio.sleep(POLL_S)


@router.get("/runs/{slug}/events/stream", tags=["runs"])
async def event_stream(request: Request, slug: str,
                       since: str | None = Query(default=None,
                                                 description="Resume after this event id.")
                       ) -> StreamingResponse:
    """The run's timeline, replayed then followed, as `event: run` frames."""
    service.run_workspace(request.app.state.project, slug)      # 404 for a bad slug
    return StreamingResponse(_events(request, slug, since),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/jobs/{job_id}/log/stream", tags=["jobs"])
async def log_stream(request: Request, job_id: str) -> StreamingResponse:
    """One frame per line of a job's stdout, as it is written."""
    job = jobs.find(request.app.state.project, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return StreamingResponse(_log(request, Path(job.log)),
                             media_type="text/event-stream", headers=SSE_HEADERS)
```

In `src/scieflow/web/app.py`:

```python
    from scieflow.web import api, pages, sse

    app.include_router(api.router)
    app.include_router(sse.router)
    app.include_router(pages.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_sse.py -v && uv run pytest -q`
Expected: 5 passed; full suite green. If a stream test hangs, the generator is not
yielding the already-written content before its first sleep — fix the generator, never
the test's timeout.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_sse.py
git commit -m "feat(web): SSE streams for the run timeline and job output

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation and the end-to-end smoke test

**Files:**
- Create: `docs/web.md`, `tests/web/test_smoke.py`
- Modify: `mkdocs.yml` (nav), `README.md` (one paragraph), `docs/cli.md` (`scieflow serve`), `docs/architecture.md` (the web app is no longer "planned")
- Test: `tests/web/test_smoke.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/web/test_smoke.py
"""One pass through the app the way a browser makes it: token in, dashboard,
run page, job output, artifact, live stream."""

import json

from fastapi.testclient import TestClient

from scieflow.core import jobs
from scieflow.web.app import create_app

TOKEN = "smoke-token"


def test_a_browser_session_end_to_end(project):
    (project.run_dir("r1") / "notes.md").write_text("# findings\n")
    with TestClient(create_app(project, TOKEN)) as client:
        landing = client.get(f"/?token={TOKEN}", follow_redirects=False)
        assert landing.status_code == 303 and "token" not in landing.headers["location"]

        dashboard = client.get("/")
        assert dashboard.status_code == 200 and "r1" in dashboard.text

        run_page = client.get("/runs/r1")
        assert "Which dataset?" in run_page.text

        job = jobs.list_jobs(project, project.run_dir("r1"))[0]
        assert "hello from the job" in client.get(f"/runs/r1/jobs/{job.id}").text

        assert "# findings" in client.get("/runs/r1/file",
                                          params={"path": "notes.md"}).text

        with client.stream("GET", "/api/v1/runs/r1/events/stream") as stream:
            first = next(line for line in stream.iter_lines() if line.startswith("data:"))
        assert json.loads(first[len("data:"):])["type"] == "run.created"

        assert client.get("/api/v1/runs").json()[0]["slug"] == "r1"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/web/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 3: Write the documentation**

`docs/web.md` — cover, in this order:

1. **What it is**: a local control surface over the same service layer the CLI uses;
   read-only in this milestone (answering gates and starting runs arrive with the
   control milestone).
2. **Running it**: `uv sync --extra web`, then `uv run scieflow serve [--port N]
   [--no-browser]`; the printed URL carries a one-time token that becomes a cookie.
3. **Security model**: binds `127.0.0.1` only and refuses anything else; no CORS; the
   token is per-process (restarting issues a new one); CSRF double-submit on unsafe
   methods; the artifact browser cannot leave the run directory. Remote access is an
   SSH tunnel (`ssh -L 8765:127.0.0.1:8765 host`) or Tailscale — never a wider bind.
4. **Pages**: dashboard (runs, budget bars, open gates), run page (phases, budget,
   gates, jobs, live timeline), job output (live for a running job), artifact browser.
5. **The API**: `/api/v1` mirrors the service layer, with the generated schema at
   `/api/v1/openapi.json` and interactive docs at `/api/v1/docs`; a short `curl`
   example using the session cookie, and the note that other apps are its audience.
6. **What it does not do yet**: answer gates, start runs, change agent config — with a
   pointer to `docs/cli.md` for each, and to the control milestone.

`mkdocs.yml`: add `- Web app: web.md` right after `- Runs, jobs and gates: runs.md`.

`README.md`: one paragraph after the "Runs, jobs and gates" section:

```markdown
## The local web app

`uv run scieflow serve` opens a browser view of every run — status, budget,
timeline, live job output, open gates and artifacts — on `127.0.0.1` only,
behind a one-time token printed in your terminal. It reads through the same
service layer as the CLI, so it can never show you a different truth. See
[docs/web.md](docs/web.md).
```

`docs/cli.md`: add a `## scieflow serve` section before "Module CLIs", documenting
`--port`, `--host` (loopback only) and `--no-browser`, and noting the `web` extra.

`docs/architecture.md`: in the layering section, change the "Local web app (planned,
M2)" caller to the real thing, and add one line that `scieflow.web` routes are thin
callers holding no logic of their own.

- [ ] **Step 4: Full verification**

```bash
uv run pytest -q
scripts/check_legacy.sh
uv run --group docs mkdocs build --strict
uv run scieflow serve --help
uv run scieflow serve --host 0.0.0.0        # must refuse
```

Expected: suite green (857 + the new web tests), every legacy check `ok`, docs build
with 0 warnings, `serve --help` correct, non-loopback bind refused.

- [ ] **Step 5: Commit**

```bash
git add docs/web.md docs/cli.md docs/architecture.md mkdocs.yml README.md tests/web/test_smoke.py
git commit -m "docs(web): the local web app, its security model and its API

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## What this plan deliberately leaves out

| Deferred | Where it goes |
|---|---|
| Answering gates, starting runs, applying agent config, the Start wizard | M2c — control (after the sandbox plan) |
| Launching a coordinator headless from the browser | M2c — control, and only inside the sandbox |
| Notifications (browser + ntfy/e-mail) | M2c — control |
| htmx and partial swaps | M2c — the first plan with forms |
| Literature, Experiments, Manuscript and Integrations pages | M3/M4/M5, as the data behind them lands |
| Folding the NiceGUI news app into this one | after M2c; it keeps running and gets linked |

## Self-review

- **Spec coverage.** `scieflow serve [--port]` on loopback with a Jupyter-style token,
  cookie, CSRF, no CORS and a non-loopback refusal: Tasks 1–2. Dashboard (runs, phases,
  budget gauges, open gates): Task 4. Run page (timeline from events, gates, jobs with
  live logs over SSE, artifact browser): Tasks 5–7. `/api/v1` mirroring the service
  layer with a generated OpenAPI schema: Task 3. The `web` extra: Task 1. The spec's
  remaining M2 bullets — full control, headless coordinators, notifications, and the
  Literature/Experiments/Manuscript/Integrations pages — are the deferrals listed
  above, by the user's decision to split M2 into foundation and control.
- **Placeholders.** None: every step carries the code or the exact file content it
  needs, and the documentation task lists the sections to write rather than saying
  "document it".
- **Type consistency.** `create_app(project, token)`, `auth.require_session`,
  `auth.csrf_protect`, `files.resolve(ws, relpath, allow_root=False)`,
  `service.run_workspace(project, slug)` and `sse.POLL_S` are used in later tasks
  exactly as defined in the task that introduces them. `service.run_workspace` is new
  in Task 3 and is what Tasks 5–7 call; no task calls the private `service._ws`.
