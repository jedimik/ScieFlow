# Web control A1a — run control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steer a run entirely from the browser — answer gates, mark phases, advance, checkpoint, resume, record spend, cancel jobs, and change which agent performs which role — without touching a terminal.

**Architecture:** Every mutation is a service-layer function first and a route second, so the CLI and the browser share one implementation. The web app's first non-GET routes arrive here, so CSRF gains form-field support (plain HTML forms cannot set a header) and the read-only guarantee test becomes an explicit inventory of mutating routes that must each be session-guarded and CSRF-protected.

**Tech Stack:** FastAPI, Jinja2, the existing service layer. No new dependency; no JavaScript framework — plain HTML forms with a hidden CSRF field and a redirect after POST.

**Spec:** `docs/superpowers/specs/2026-09-24-web-control-a1-design.md`

**Not in this plan.** The spec's A1 covers more than this: starting a run from a wizard, the
coordinator conversation (job-per-turn with resumed agent sessions) and the run charter are **A1b**,
the plan that follows this one. A1a steers runs that already exist, and comes first because it
establishes the write path — service functions, form CSRF, the mutating-route inventory — that every
conversation turn in A1b is built on.

## Global Constraints

- **Every mutation goes through `scieflow.core.service`.** No route may call `run.actions`, `gates` or `jobs` directly; the CLI and the browser must share one implementation.
- **Every non-GET route is session-guarded and CSRF-protected.** CSRF is enforced centrally in `auth.install_session`'s middleware so a route cannot forget it.
- **The read-only test evolves, it does not disappear.** `tests/web/test_read_only.py` currently asserts every route is a safe method. It becomes an inventory: every non-GET path is listed explicitly and must be both guarded and CSRF-protected.
- **Loopback only, no CORS**, unchanged from the read-only milestone.
- **Agent dispatches stay sandboxed.** This plan adds no new dispatch path; `service.dispatch_agent` is untouched.
- **`chats push`/`pull`, DVC uploads and `--promote` do not become buttons** in this plan — they are out of scope entirely.
- **Nothing regresses.** `uv run pytest -q` stays green (1007 passing at the start of this plan), `scripts/check_legacy.sh` stays all `ok`, `uv run --group docs mkdocs build --strict` stays at 0 warnings.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **A form submitted twice** — the browser's back button, a double click, or a retry. Answering an already-answered gate must produce a readable message, not a 500 or a corrupted gate. *(Task 3)*
2. **An action on a run that no longer exists** — a stale tab posting to a deleted slug must give a clean 404, not a traceback. *(Task 2)*
3. **A CSRF token missing because the session expired mid-session** — the browser must get an HTML page explaining how to get back in, not a raw JSON `403` blob. *(Task 2)*
4. **An action the run's state forbids** — advancing when the iteration budget is spent raises `BudgetExhausted`; the browser must show the refusal as a sentence, and the run must be checkpointed exactly as the CLI would leave it. *(Task 1)*
5. **Two tabs mutating the same run** — the store is lock-protected, so neither write corrupts; the second tab must not silently show stale state after its own action. *(Task 3)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/service.py` | the run-lifecycle write functions every caller shares |
| `src/scieflow/web/auth.py` | CSRF accepts a form field as well as a header |
| `src/scieflow/web/api.py` | JSON mutation routes under `/api/v1` |
| `src/scieflow/web/pages.py` | form-post routes that redirect back to the page |
| `src/scieflow/web/templates/run.html` | gate answering and run actions |
| `src/scieflow/web/templates/agents.html` | staffing: plan → diff → apply |
| `tests/web/test_read_only.py` | becomes the mutating-route inventory |
| `tests/web/test_mutations.py` | auth, CSRF and effect for every mutation |

---

### Task 1: Service-layer run actions

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_service.py` (append)

**Interfaces:**
- Consumes: `scieflow.core.run.actions` (`mark_phase(ws, phase, state, actor)`, `advance_iteration(ws, actor)`, `checkpoint_run(ws, reason, detail, actor)`, `resume(ws, actor)`, `record_spend(ws, actor, **spent)`, `BudgetExhausted`), `service.run_workspace`, `ServiceError`.
- Produces: `service.mark_phase(project, slug, phase, state, actor="human") -> dict`;
  `service.advance_run(project, slug, actor="human") -> dict`;
  `service.checkpoint_run(project, slug, reason, detail="", actor="human") -> dict`;
  `service.resume_run(project, slug, actor="human") -> dict`;
  `service.record_spend(project, slug, actor="human", **spent) -> dict`.
  All raise `ServiceError` for a bad slug, an invalid value, or a refusal.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_service.py`)

```python
def test_mark_phase_through_the_service(project):
    from scieflow.core.run import status

    service.mark_phase(project, "r1", "hypothesize", "running")
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "running"


def test_service_run_actions_reject_a_bad_slug(project):
    for call in (
        lambda: service.mark_phase(project, "nope", "hypothesize", "running"),
        lambda: service.advance_run(project, "nope"),
        lambda: service.checkpoint_run(project, "nope", "user"),
        lambda: service.resume_run(project, "nope"),
        lambda: service.record_spend(project, "nope", experiment_runs=1),
    ):
        with pytest.raises(service.ServiceError):
            call()


def test_mark_phase_rejects_an_invalid_state(project):
    with pytest.raises(service.ServiceError, match="state"):
        service.mark_phase(project, "r1", "hypothesize", "banana")


def test_checkpoint_then_resume_round_trip(project):
    from scieflow.core.run import status

    service.checkpoint_run(project, "r1", "user", detail="stepping away")
    assert status.read_status(project.run_dir("r1"))["stopped"]["reason"] == "user"
    service.resume_run(project, "r1")
    assert not status.read_status(project.run_dir("r1")).get("stopped")


def test_advance_refused_when_the_iteration_budget_is_spent(project):
    """A refusal must arrive as ServiceError — and must leave the run
    checkpointed exactly as the CLI leaves it, not half-changed."""
    from scieflow.core.run import budget, status

    ws = project.run_dir("r1")
    budget.write_budget(ws, budget.new_budget(1, 10, 60))
    service.record_spend(project, "r1", iterations=1)
    with pytest.raises(service.ServiceError):
        service.advance_run(project, "r1")
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_record_spend_accumulates(project):
    from scieflow.core.run import budget

    # The fixture's run carries no budget.yml, and record_spend returns None
    # without one — which the service turns into a ServiceError.
    budget.write_budget(project.run_dir("r1"), budget.new_budget(3, 10, 60))
    service.record_spend(project, "r1", experiment_runs=2)
    service.record_spend(project, "r1", experiment_runs=3)
    assert budget.read_budget(project.run_dir("r1"))["spent"]["experiment_runs"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_service.py -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'mark_phase'`.

- [ ] **Step 3: Write the implementation**

Append to `src/scieflow/core/service.py`:

```python
def mark_phase(project: Project, slug: str, phase: str, state: str,
               actor: str = "human") -> dict:
    """Set a phase's state. The browser and the CLI share this path."""
    ws = _ws(project, slug)
    try:
        return actions.mark_phase(ws, phase, state, actor)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def advance_run(project: Project, slug: str, actor: str = "human") -> dict:
    """Start the next iteration; refused (and the run checkpointed) when the
    iteration budget is spent."""
    ws = _ws(project, slug)
    try:
        return actions.advance_iteration(ws, actor)
    except (actions.BudgetExhausted, ValueError) as exc:
        raise ServiceError(str(exc)) from exc


def checkpoint_run(project: Project, slug: str, reason: str, detail: str = "",
                   actor: str = "human") -> dict:
    ws = _ws(project, slug)
    try:
        return actions.checkpoint_run(ws, reason, detail, actor)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def resume_run(project: Project, slug: str, actor: str = "human") -> dict:
    ws = _ws(project, slug)
    try:
        return actions.resume(ws, actor)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def record_spend(project: Project, slug: str, actor: str = "human", **spent) -> dict:
    """Record spend the runner cannot measure (remote jobs, manual work)."""
    ws = _ws(project, slug)
    if not spent:
        raise ServiceError("nothing to record; name at least one budget dimension")
    try:
        result = actions.record_spend(ws, actor, **spent)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if result is None:
        raise ServiceError(f"run {slug} has no budget.yml")
    return result
```

Add `actions` to the module's imports:

```python
from scieflow.core.run import actions, budget, status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_service.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_service.py
git commit -m "feat(core): service-layer run actions the browser and CLI share

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: CSRF for forms, the mutation routes, and the route inventory

**Files:**
- Modify: `src/scieflow/web/auth.py`, `src/scieflow/web/api.py`, `src/scieflow/web/app.py`
- Rewrite: `tests/web/test_read_only.py`
- Test: `tests/web/test_mutations.py` (create)

**Interfaces:**
- Consumes: the Task 1 service functions; `service.answer_gate`, `service.cancel_job` (both exist).
- Produces: `auth.CSRF_FIELD = "csrf_token"` (a form field accepted in place of the header);
  `auth.csrf_token(request) -> str` for templates;
  JSON routes `POST /api/v1/runs/{slug}/phase`, `/advance`, `/checkpoint`, `/resume`, `/spend`,
  `POST /api/v1/runs/{slug}/gates/{gate_id}/answer`, `POST /api/v1/jobs/{job_id}/cancel`;
  `MUTATING_PATHS` in `tests/web/test_read_only.py` as the explicit inventory.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_mutations.py
import pytest

from scieflow.core import events
from scieflow.core.run import status
from scieflow.web import auth


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


MUTATIONS = [
    ("/api/v1/runs/r1/phase", {"phase": "hypothesize", "state": "running"}),
    ("/api/v1/runs/r1/advance", {}),
    ("/api/v1/runs/r1/checkpoint", {"reason": "user"}),
    ("/api/v1/runs/r1/resume", {}),
    ("/api/v1/runs/r1/spend", {"experiment_runs": "1"}),
]


@pytest.mark.parametrize("path, form", MUTATIONS)
def test_every_mutation_needs_a_session(project, path, form):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.post(path, data=form).status_code in (401, 403)


@pytest.mark.parametrize("path, form", MUTATIONS)
def test_every_mutation_needs_csrf(client, path, form):
    assert client.post(path, data=form).status_code == 403


def test_mark_phase_over_http(client, project):
    assert post(client, "/api/v1/runs/r1/phase",
                phase="hypothesize", state="running").status_code == 200
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "running"


def test_checkpoint_and_resume_over_http(client, project):
    assert post(client, "/api/v1/runs/r1/checkpoint", reason="user").status_code == 200
    assert status.read_status(project.run_dir("r1"))["stopped"]["reason"] == "user"
    assert post(client, "/api/v1/runs/r1/resume").status_code == 200
    assert not status.read_status(project.run_dir("r1")).get("stopped")


def test_answer_a_gate_over_http(client, project):
    gate = client.get("/api/v1/gates").json()[0]
    assert post(client, f"/api/v1/runs/r1/gates/{gate['id']}/answer",
                answer="A").status_code == 200
    assert client.get("/api/v1/gates").json() == []
    assert "gate.answered" in [e["type"] for e in events.read(project.run_dir("r1"))]


def test_a_mutation_on_an_unknown_run_is_404(client):
    """A stale tab posting to a deleted slug gets a clean error, not a traceback."""
    assert post(client, "/api/v1/runs/nope/advance").status_code == 404


def test_a_refused_page_post_renders_html_not_a_json_blob(client):
    """A form posted after the session expired must tell the person how to
    get back in. The CSRF check happens in middleware, before routing, so it
    answers directly and never reaches the exception handler that renders the
    page — the middleware has to render it itself."""
    response = client.post("/runs/r1", headers={"Accept": "text/html"})
    assert response.status_code == 403
    assert "scieflow serve" in response.text
    assert "Not signed in" in response.text


def test_an_api_post_still_gets_json(client):
    """Only page paths get HTML; /api stays a JSON surface for other tools."""
    response = client.post("/api/v1/runs/r1/advance",
                           headers={"Accept": "text/html"})
    assert response.status_code == 403
    assert response.json()["error"]


def test_the_route_still_sees_its_form_after_the_middleware_read_it(client, project):
    """The CSRF middleware reads the request body to find the form field, and
    it runs before routing. Starlette's BaseHTTPMiddleware replays a body that
    was already read (`_CachedRequest.wrapped_receive`), so the route's own
    Form(...) parameters still arrive — but only because the middleware calls
    `await request.body()` first. This test fails loudly if that ordering is
    lost or the framework stops replaying, instead of the route silently
    receiving an empty form."""
    from scieflow.core.run import status

    response = post(client, "/api/v1/runs/r1/phase",
                    phase="experiment", state="running")
    assert response.status_code == 200, response.text
    assert status.read_status(project.run_dir("r1"))["phases"]["experiment"] == "running"
```

Rewrite `tests/web/test_read_only.py` so the guarantee becomes an inventory:

```python
"""The app's mutating routes are an explicit, guarded inventory.

This replaces the read-only milestone's "every route is a GET" test. That
claim stopped being true when run control arrived, but the guarantee behind
it did not: a mutation must never appear without a session guard and CSRF
protection, and never without someone noticing. So the set is listed here by
hand, and the test fails both when a listed path stops mutating and when an
unlisted mutation appears.

`app.openapi()["paths"]` is what FastAPI resolves the nested routers down to;
a shallow walk of `app.routes` sees only the top-level mounts and would pass
while seeing almost nothing.
"""

from scieflow.web.app import create_app

SAFE_METHODS = {"get", "head", "options"}

#: Every path that may be mutated, and the methods allowed on it.
MUTATING_PATHS = {
    "/api/v1/runs/{slug}/phase": {"post"},
    "/api/v1/runs/{slug}/advance": {"post"},
    "/api/v1/runs/{slug}/checkpoint": {"post"},
    "/api/v1/runs/{slug}/resume": {"post"},
    "/api/v1/runs/{slug}/spend": {"post"},
    "/api/v1/runs/{slug}/gates/{gate_id}/answer": {"post"},
    "/api/v1/jobs/{job_id}/cancel": {"post"},
}


def _all_methods(project, token="ro-token") -> dict[str, set[str]]:
    paths = create_app(project, token).openapi()["paths"]
    return {path: {m.lower() for m in methods} for path, methods in paths.items()}


def test_enumeration_actually_sees_the_real_routes(project):
    found = _all_methods(project)
    assert "/api/v1/runs/{slug}" in found
    assert "/runs/{slug}" in found
    assert len(found) > 10


def test_the_mutating_routes_are_exactly_the_declared_inventory(project):
    found = _all_methods(project)
    unsafe = {path: methods - SAFE_METHODS
              for path, methods in found.items() if methods - SAFE_METHODS}
    assert unsafe == MUTATING_PATHS, (
        "a mutating route appeared or changed without being declared — add it to "
        "MUTATING_PATHS and make sure it is session-guarded and CSRF-protected")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_mutations.py tests/web/test_read_only.py -v`
Expected: FAIL — the mutation routes 404, and the inventory test reports an empty `unsafe` set.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/web/auth.py`, accept the token as a form field as well as a
header. A plain HTML form cannot set a header, and requiring JavaScript for a
checkpoint button is not a trade worth making.

```python
CSRF_FIELD = "csrf_token"
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def _supplied_csrf(request: Request) -> str:
    """The token from the header, or from a form field when a browser posted
    a form.

    Reading the body here is the delicate part, because the middleware below
    runs BEFORE routing: if the body were consumed, the route would receive
    an empty form. Starlette's `BaseHTTPMiddleware` wraps the request in a
    `_CachedRequest` whose `wrapped_receive` replays `_body` to the app
    downstream — but only once `body()` has actually been called. So call
    `body()` first, and `form()` afterwards reads from that cache for both
    urlencoded and multipart payloads.
    """
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    if not request.headers.get("content-type", "").startswith(FORM_TYPES):
        return ""
    await request.body()            # cache it, so the route still sees it
    form = await request.form()
    return str(form.get(CSRF_FIELD) or "")


def csrf_ok(cookie: str, supplied: str) -> bool:
    return bool(cookie) and bool(supplied) and secrets.compare_digest(cookie, supplied)
```

Replace the two `request.headers.get(CSRF_HEADER)` reads — the one in the
dependency and the one in the middleware — with that helper. The dependency
becomes async:

```python
async def csrf_protect(request: Request) -> None:
    """FastAPI dependency for unsafe methods: double-submit cookie check."""
    if request.method in SAFE_METHODS:
        return
    if not csrf_ok(request.cookies.get(CSRF_COOKIE) or "",
                   await _supplied_csrf(request)):
        raise HTTPException(status_code=403, detail="CSRF token missing or wrong")
```

Add a helper the templates use to place the hidden field:

```python
def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE, "")
```

**The middleware answers refusals itself**, because it runs before routing
and so never reaches the app's `HTTPException` handler. Give
`install_session` a renderer rather than importing templates into `auth`:

```python
def install_session(app, refuse=None) -> None:
    """Register the token → cookie exchange as middleware.

    `refuse(request, status, message)` renders a refusal; it exists because
    this middleware answers before any exception handler can, so without it
    a browser form would get a raw JSON body instead of a page.
    """
    if refuse is None:
        def refuse(request, status, message):
            return JSONResponse({"error": message}, status_code=status)
```

and inside `_session`, replace the inline JSON refusals:

```python
            if not secrets.compare_digest(supplied, request.app.state.token):
                return refuse(request, 403, "bad token")
```

```python
        if request.method not in SAFE_METHODS:
            # Central enforcement: every unsafe request needs the
            # double-submit token, as a header or a form field, whether or
            # not the route that will handle it also declares
            # `Depends(csrf_protect)`.
            if not csrf_ok(request.cookies.get(CSRF_COOKIE) or "",
                           await _supplied_csrf(request)):
                return refuse(request, 403, "CSRF token missing or wrong")
```

In `src/scieflow/web/app.py`, supply the renderer and reuse it for the
exception handler, so a refused page request looks the same however it was
refused:

```python
    def _refuse(request: Request, status: int, message: str):
        wants_html = ("text/html" in request.headers.get("accept", "")
                      and not request.url.path.startswith("/api/"))
        if wants_html and status in (401, 403):
            return TEMPLATES.TemplateResponse(
                request, "unauthorized.html", status_code=status)
        return JSONResponse({"error": message}, status_code=status)

    auth.install_session(app, refuse=_refuse)

    @app.exception_handler(FastAPIHTTPException)
    async def _http_error(request: Request, exc: FastAPIHTTPException):
        return _refuse(request, exc.status_code, exc.detail)
```

In `src/scieflow/web/api.py`, add the mutation routes:

```python
from fastapi import Form

MUTATE = [Depends(auth.csrf_protect)]


@router.post("/runs/{slug}/phase", dependencies=MUTATE, tags=["runs"])
async def set_phase(request: Request, slug: str,
                    phase: str = Form(...), state: str = Form(...)) -> dict:
    """Set a phase's state (pending/running/done/failed)."""
    return service.mark_phase(_project(request), slug, phase, state)


@router.post("/runs/{slug}/advance", dependencies=MUTATE, tags=["runs"])
async def advance(request: Request, slug: str) -> dict:
    """Start the next iteration; refused when the iteration budget is spent."""
    return service.advance_run(_project(request), slug)


@router.post("/runs/{slug}/checkpoint", dependencies=MUTATE, tags=["runs"])
async def checkpoint(request: Request, slug: str, reason: str = Form(...),
                     detail: str = Form("")) -> dict:
    """Stop the run gracefully with resume instructions."""
    return service.checkpoint_run(_project(request), slug, reason, detail)


@router.post("/runs/{slug}/resume", dependencies=MUTATE, tags=["runs"])
async def resume(request: Request, slug: str) -> dict:
    """Clear a stop so the run can continue."""
    return service.resume_run(_project(request), slug)


@router.post("/runs/{slug}/spend", dependencies=MUTATE, tags=["runs"])
async def spend(request: Request, slug: str,
                experiment_runs: int = Form(0), wall_minutes: float = Form(0.0),
                iterations: int = Form(0)) -> dict:
    """Record spend the runner cannot measure."""
    recorded = {k: v for k, v in (("experiment_runs", experiment_runs),
                                  ("wall_minutes", wall_minutes),
                                  ("iterations", iterations)) if v}
    return service.record_spend(_project(request), slug, **recorded)


@router.post("/runs/{slug}/gates/{gate_id}/answer", dependencies=MUTATE, tags=["gates"])
async def answer(request: Request, slug: str, gate_id: str,
                 answer: str = Form(...), note: str = Form("")) -> dict:
    """Answer an open gate as the human."""
    return service.answer_gate(_project(request), slug, gate_id, answer, note=note)


@router.post("/jobs/{job_id}/cancel", dependencies=MUTATE, tags=["jobs"])
async def cancel(request: Request, job_id: str) -> dict:
    """Cancel a running job and its whole process group."""
    return service.cancel_job(_project(request), job_id)
```

`service.cancel_job` looks a job up project-wide, with no run to scope it —
a known carried fast-follow, not something this task changes. It matters
less than it looks: the caller already needs a session for this project, and
Task 3's page route does check that the job belongs to the run in the URL.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass; full suite green. The inventory test now matches `MUTATING_PATHS` exactly.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_mutations.py tests/web/test_read_only.py
git commit -m "feat(web): run mutations over HTTP, CSRF for forms, route inventory

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The run page becomes the working surface

**Files:**
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/run.html`, `src/scieflow/web/templates/dashboard.html`
- Modify: `tests/web/test_read_only.py` (extend `MUTATING_PATHS`)
- Test: `tests/web/test_run_page_actions.py` (create)

**Interfaces:**
- Consumes: the Task 1 service functions, `service.answer_gate`, `service.cancel_job`, `auth.csrf_token(request)`, `auth.CSRF_FIELD`.
- Produces: `POST /runs/{slug}/gates/{gate_id}`, `POST /runs/{slug}/act`, `POST /runs/{slug}/jobs/{job_id}/cancel` — each performs its effect and answers `303 See Other` back to `/runs/{slug}`, carrying `?error=` when the service refused.

**Why page routes rather than posting the forms straight at `/api/v1`:** a browser form posted at a JSON route renders raw JSON in the tab. These routes do the same service call and redirect back to the page, which is also what makes the browser's reload button harmless.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_run_page_actions.py
from scieflow.core.run import status
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_run_page_offers_the_actions(client):
    page = client.get("/runs/r1").text
    assert 'name="csrf_token"' in page
    assert 'action="/runs/r1/act"' in page
    assert "uv run scieflow gate answer" not in page   # the CLI-only note is gone


def test_answering_a_gate_from_the_page(client, project):
    gate = client.get("/api/v1/gates").json()[0]
    response = post(client, f"/runs/r1/gates/{gate['id']}", answer="A")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/r1"
    assert client.get("/api/v1/gates").json() == []


def test_answering_the_same_gate_twice_explains_itself(client):
    """The back button, a double click, a browser retry. The second answer
    must land the user back on the page with a sentence — not a 500, and not
    a second answer recorded."""
    gate = client.get("/api/v1/gates").json()[0]
    post(client, f"/runs/r1/gates/{gate['id']}", answer="A")
    again = post(client, f"/runs/r1/gates/{gate['id']}", answer="B")
    assert again.status_code == 303
    assert again.headers["location"].startswith("/runs/r1?error=")
    assert "not open" in client.get(again.headers["location"]).text


def test_a_second_tab_sees_its_own_action(client, project):
    """Two tabs, both mutating. Neither write corrupts the store, and the
    redirect means the acting tab always re-reads rather than showing the
    state it rendered before."""
    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="running")
    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="done")
    assert "done" in client.get("/runs/r1").text
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "done"


def test_checkpoint_and_resume_from_the_page(client, project):
    post(client, "/runs/r1/act", action="checkpoint", reason="user", detail="lunch")
    assert "stopped: user" in client.get("/runs/r1").text
    post(client, "/runs/r1/act", action="resume")
    assert "stopped: user" not in client.get("/runs/r1").text


def test_an_unknown_action_is_refused(client):
    assert post(client, "/runs/r1/act", action="delete-everything").status_code == 400


def test_cancelling_a_job_from_the_page(client, project, running_job):
    response = post(client, f"/runs/r1/jobs/{running_job.id}/cancel")
    assert response.status_code == 303
    assert "cancelled" in client.get("/runs/r1").text


def test_page_actions_need_csrf(client):
    assert client.post("/runs/r1/act", data={"action": "resume"}).status_code == 403
```

Extend `MUTATING_PATHS` in `tests/web/test_read_only.py`:

```python
    "/runs/{slug}/gates/{gate_id}": {"post"},
    "/runs/{slug}/act": {"post"},
    "/runs/{slug}/jobs/{job_id}/cancel": {"post"},
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_run_page_actions.py tests/web/test_read_only.py -v`
Expected: FAIL — the page routes 404 and the inventory test reports the three new paths missing.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/web/pages.py`:

```python
from fastapi import Form, HTTPException
from fastapi.responses import RedirectResponse

MUTATE = [Depends(auth.csrf_protect)]


def _back(slug: str, error: str = "") -> RedirectResponse:
    """Post/redirect/get: the browser lands on a fresh read of the page, so
    reloading never repeats the action."""
    target = f"/runs/{slug}"
    if error:
        target += "?error=" + quote(error)
    return RedirectResponse(target, status_code=303)


@router.post("/runs/{slug}/gates/{gate_id}", dependencies=MUTATE)
async def answer_gate(request: Request, slug: str, gate_id: str,
                      answer: str = Form(...), note: str = Form("")):
    try:
        service.answer_gate(_project(request), slug, gate_id, answer, note=note)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/act", dependencies=MUTATE)
async def act(request: Request, slug: str, action: str = Form(...),
              phase: str = Form(""), state: str = Form(""),
              reason: str = Form(""), detail: str = Form(""),
              experiment_runs: int = Form(0), wall_minutes: float = Form(0.0),
              iterations: int = Form(0)):
    project = _project(request)
    try:
        if action == "phase":
            service.mark_phase(project, slug, phase, state)
        elif action == "advance":
            service.advance_run(project, slug)
        elif action == "checkpoint":
            service.checkpoint_run(project, slug, reason or "user", detail)
        elif action == "resume":
            service.resume_run(project, slug)
        elif action == "spend":
            recorded = {k: v for k, v in (("experiment_runs", experiment_runs),
                                          ("wall_minutes", wall_minutes),
                                          ("iterations", iterations)) if v}
            service.record_spend(project, slug, **recorded)
        else:
            raise HTTPException(status_code=400, detail=f"unknown action {action!r}")
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/jobs/{job_id}/cancel", dependencies=MUTATE)
async def cancel_job(request: Request, slug: str, job_id: str):
    project = _project(request)
    ws = service.run_workspace(project, slug)
    job = jobs_mod.find(project, job_id)
    if job is None or job.run_dir is None or Path(job.run_dir).resolve() != ws.resolve():
        raise HTTPException(status_code=404, detail=f"no job {job_id} in {slug}")
    try:
        service.cancel_job(project, job_id)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)
```

Add `from urllib.parse import quote` to the imports, and pass the flash plus
the CSRF token into the template from `run_page`:

```python
@router.get("/runs/{slug}", response_class=HTMLResponse)
async def run_page(request: Request, slug: str, error: str = "") -> HTMLResponse:
    ...
    return TEMPLATES.TemplateResponse(request, "run.html", {
        ...
        "error": error,
        "csrf": auth.csrf_token(request),
    })
```

In `run.html`, add the flash directly under the `<h1>`:

```html
{% if error %}<p class="state-failed" role="alert">{{ error }}</p>{% endif %}
```

Replace the gate's CLI note with a form (the `{% if gate.options %}` branch
becomes a select, otherwise a text field):

```html
    <form method="post" action="/runs/{{ slug }}/gates/{{ gate.id }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      {% if gate.options %}
        <select name="answer">
          {% for option in gate.options %}<option>{{ option }}</option>{% endfor %}
        </select>
      {% else %}
        <input name="answer" required>
      {% endif %}
      <input name="note" placeholder="note (optional)">
      <button>Answer</button>
    </form>
```

Add the run actions after the Budget table:

```html
<h2>Actions</h2>
<form method="post" action="/runs/{{ slug }}/act" class="actions">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <select name="phase">
    {% for phase, _ in phases %}<option>{{ phase }}</option>{% endfor %}
  </select>
  <select name="state">
    {% for state in ["pending", "running", "done", "failed"] %}<option>{{ state }}</option>{% endfor %}
  </select>
  <button name="action" value="phase">Mark phase</button>
</form>
<form method="post" action="/runs/{{ slug }}/act" class="actions">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <button name="action" value="advance">Advance iteration</button>
  {% if (detail.status or {}).get("stopped") %}
    <button name="action" value="resume">Resume</button>
  {% else %}
    <input name="detail" placeholder="why you are stopping">
    <button name="action" value="checkpoint">Checkpoint</button>
  {% endif %}
</form>
<form method="post" action="/runs/{{ slug }}/act" class="actions">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <input name="experiment_runs" type="number" min="0" value="0" title="experiment runs">
  <input name="wall_minutes" type="number" min="0" step="0.1" value="0" title="wall minutes">
  <button name="action" value="spend">Record spend</button>
</form>
```

And a cancel button in the jobs table's last cell, for running jobs only:

```html
    <td>
      <a href="/runs/{{ slug }}/jobs/{{ job.id }}">output</a>
      {% if job.state == "running" %}
      <form method="post" action="/runs/{{ slug }}/jobs/{{ job.id }}/cancel" class="inline">
        <input type="hidden" name="csrf_token" value="{{ csrf }}">
        <button>Cancel</button>
      </form>
      {% endif %}
    </td>
```

In `dashboard.html`, the open-gates list gains the same answer form — the
dashboard route must pass `auth.csrf_token(request)` as `csrf` too, and each
gate's form posts to `/runs/{{ gate.slug }}/gates/{{ gate.id }}`.

Add to `static/app.css`:

```css
.actions { display: flex; gap: .5rem; align-items: center; margin: .5rem 0; }
form.inline { display: inline; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass.

`running_job` does not exist yet — add it to `tests/web/conftest.py`:

```python
@pytest.fixture
def running_job(project):
    """A job still running, so it can be cancelled."""
    job = jobs.start(project, [sys.executable, "-c", "import time; time.sleep(300)"],
                     kind="agent", cwd=project.root,
                     run_dir=project.run_dir("r1"), label="sleepy")
    yield job
    if job.state == "running":
        jobs.cancel(job)
```

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web
git commit -m "feat(web): answer gates and steer a run from its page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The Agents page — plan, diff, apply

**Files:**
- Create: `src/scieflow/web/templates/agents.html`
- Modify: `src/scieflow/core/service.py`, `src/scieflow/web/pages.py`, `src/scieflow/web/templates/base.html`
- Modify: `tests/web/test_read_only.py` (extend `MUTATING_PATHS`)
- Test: `tests/web/test_agents_page.py` (create)

**Interfaces:**
- Consumes: `service.agent_settings(project, slug=None)`, `agent_configure.Op`, `plan_defaults(root, ops)`, `plan_workspace(root, slug, ops)`, `Plan.changes[].diff(root)`, `agent_configure.write(plan)`, `ConfigureError`, `agent_config.ROLES`.
- Produces: `service.plan_staffing(project, assignments, slug=None) -> dict` with keys `diff`, `notes`, `warnings`, `empty`; `service.apply_staffing(project, assignments, slug=None) -> dict`; `GET /agents` (optionally `?slug=` and repeated `?assign=role=agent` for the preview) and `POST /agents`.

**Scope:** role assignment only. Per-agent fields (`--set agent.model=…`), promotions and demotions stay on the CLI in this plan; the page links to `docs/agents.md` for them.

**The apply never trusts the posted diff.** The POST carries the same
assignments the preview carried and re-plans from the file on disk, so a
concurrent CLI edit cannot be silently overwritten by a stale preview.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_agents_page.py
"""The Agents page.

Roles come from `agent_config.ROLES` — there is no "coordinator" role; the
fixture project assigns `research.outline`. Assignments are written to
`config/defaults.yml`, not `config/agents.yml`, which holds the registry.
"""

import yaml

from scieflow.web import auth

DEFAULTS = ("config", "defaults.yml")


def test_the_page_lists_every_role_and_its_agent(client):
    page = client.get("/agents").text
    assert "research.outline" in page
    assert "stub" in page
    assert 'name="csrf_token"' in page


def test_previewing_a_change_shows_a_diff_and_writes_nothing(client, project):
    before = (project.root.joinpath(*DEFAULTS)).read_text()
    page = client.get("/agents", params={"assign": "research.outline=stub2"}).text
    assert "--- a/config/defaults.yml" in page
    assert "+++ b/config/defaults.yml" in page
    assert (project.root.joinpath(*DEFAULTS)).read_text() == before


def test_applying_writes_the_change(client, project):
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "assign": "research.outline=stub2"})
    assert response.status_code == 303
    written = yaml.safe_load((project.root.joinpath(*DEFAULTS)).read_text())
    assert written["assignments"]["research.outline"] == "stub2"


def test_an_unknown_role_is_reported_not_raised(client):
    page = client.get("/agents", params={"assign": "wizard=stub"}).text
    assert "unknown role" in page


def test_an_unknown_agent_is_reported_not_written(client, project):
    before = (project.root.joinpath(*DEFAULTS)).read_text()
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "assign": "research.outline=nonesuch"})
    assert response.headers["location"].startswith("/agents?error=")
    assert (project.root.joinpath(*DEFAULTS)).read_text() == before


def test_a_workspace_change_is_scoped_to_that_run(client, project):
    token = client.cookies[auth.CSRF_COOKIE]
    client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "slug": "r1", "assign": "research.outline=stub2"})
    defaults = yaml.safe_load((project.root.joinpath(*DEFAULTS)).read_text())
    assert defaults["assignments"]["research.outline"] == "stub"
    scoped = client.get("/api/v1/agents", params={"slug": "r1"}).json()
    assert scoped["assignments"]["research.outline"]["value"] == "stub2"


def test_applying_nothing_says_so(client):
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False,
                           data={auth.CSRF_FIELD: token})
    assert response.headers["location"].startswith("/agents?error=")


def test_the_agents_page_needs_csrf(client):
    assert client.post(
        "/agents", data={"assign": "research.outline=stub2"}).status_code == 403
```

These tests need a second agent to reassign to. Add one to the registry in
`tests/web/conftest.py`, beside `stub`:

```python
        '  stub2: {cmd: "%s", enabled: true, timeout_min: 1}\n' % STUB
```

Extend `MUTATING_PATHS`:

```python
    "/agents": {"post"},
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_agents_page.py -v`
Expected: FAIL — `/agents` 404s.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/service.py` (add `agent_configure as acf` to the imports):

```python
def _staffing_plan(project: Project, assignments: list[str], slug: str | None):
    try:
        ops = [acf.parse_assign(text) for text in assignments]
        if slug:
            _ws(project, slug)          # validates the slug the same way
            return acf.plan_workspace(project.root, slug, ops)
        return acf.plan_defaults(project.root, ops)
    except acf.ConfigureError as exc:
        raise ServiceError(str(exc)) from exc


def plan_staffing(project: Project, assignments: list[str],
                  slug: str | None = None) -> dict:
    """What changing these role assignments would write, as a diff."""
    plan = _staffing_plan(project, assignments, slug)
    return {
        "diff": "".join(change.diff(project.root) for change in plan.changes),
        "notes": list(plan.notes),
        "warnings": list(plan.warnings),
        "empty": not plan.changes,
    }


def apply_staffing(project: Project, assignments: list[str],
                   slug: str | None = None) -> dict:
    """Re-plan from what is on disk right now, then write."""
    plan = _staffing_plan(project, assignments, slug)
    if not plan.changes:
        raise ServiceError("already configured that way; nothing to write")
    acf.write(plan)
    return {"written": [str(c.path.relative_to(project.root)) for c in plan.changes],
            "warnings": list(plan.warnings)}
```

In `src/scieflow/web/pages.py`:

```python
from fastapi import Query


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, slug: str = "", error: str = "",
                      assign: list[str] = Query(default=[])) -> HTMLResponse:
    from scieflow.core import agent_config

    project = _project(request)
    preview, problem = None, error
    if assign:
        try:
            preview = service.plan_staffing(project, assign, slug or None)
        except service.ServiceError as exc:
            problem = str(exc)
    return TEMPLATES.TemplateResponse(request, "agents.html", {
        "slug": slug,
        "settings": service.agent_settings(project, slug or None),
        "roles": list(agent_config.ROLES),
        "runs": service.list_runs(project),
        "assign": assign,
        "preview": preview,
        "error": problem,
        "csrf": auth.csrf_token(request),
    })


@router.post("/agents", dependencies=MUTATE)
async def apply_agents(request: Request, slug: str = Form(""),
                       assign: list[str] = Form(default=[])):
    target = "/agents" + (f"?slug={quote(slug)}" if slug else "")
    if not assign:
        return RedirectResponse(target + ("&" if slug else "?")
                                + "error=" + quote("choose a role and an agent first"),
                                status_code=303)
    try:
        service.apply_staffing(_project(request), assign, slug or None)
    except service.ServiceError as exc:
        return RedirectResponse(target + ("&" if slug else "?")
                                + "error=" + quote(str(exc)), status_code=303)
    return RedirectResponse(target, status_code=303)
```

Create `src/scieflow/web/templates/agents.html`:

```html
{% extends "base.html" %}
{% block title %}Agents — ScieFlow{% endblock %}
{% block nav %}<a href="/">← all runs</a>{% endblock %}
{% block content %}
<h1>Agents</h1>
{% if error %}<p class="state-failed" role="alert">{{ error }}</p>{% endif %}
<p class="dim">
  {% if slug %}Editing run <strong>{{ slug }}</strong> — a workspace only stores
  what differs from the defaults.{% else %}Editing the project defaults.{% endif %}
  Per-agent fields, promotions and demotions stay on the CLI &mdash; see
  <code>docs/agents.md</code>.
</p>

<form method="get" action="/agents">
  <select name="slug" onchange="this.form.submit()">
    <option value="">project defaults</option>
    {% for run in runs %}
    <option value="{{ run.slug }}" {% if run.slug == slug %}selected{% endif %}>{{ run.slug }}</option>
    {% endfor %}
  </select>
  <noscript><button>Switch</button></noscript>
</form>

<h2>Who does what</h2>
<table>
  <tr><th>role</th><th>agent</th><th>from</th></tr>
  {% for role in roles %}
  {% set current = settings.assignments.get(role) %}
  <tr>
    <td>{{ role }}</td>
    <td>{{ current.value if current else "—" }}</td>
    <td class="dim">{{ current.source if current else "unset" }}</td>
  </tr>
  {% endfor %}
</table>

<h2>Change an assignment</h2>
<form method="get" action="/agents" class="actions">
  <input type="hidden" name="slug" value="{{ slug }}">
  <!-- One select carrying "role=agent", so the preview needs no JavaScript. -->
  <select name="assign">
    {% for role in roles %}{% for name in settings.agents %}
    <option value="{{ role }}={{ name }}">{{ role }} &rarr; {{ name }}</option>
    {% endfor %}{% endfor %}
  </select>
  <button>Preview</button>
</form>
{% if preview %}
  {% if preview.empty %}
    <p class="dim">Already configured that way; nothing to write.</p>
  {% else %}
    <pre class="diff">{{ preview.diff }}</pre>
    {% for warning in preview.warnings %}<p class="state-failed">{{ warning }}</p>{% endfor %}
    <form method="post" action="/agents">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <input type="hidden" name="slug" value="{{ slug }}">
      {% for one in assign %}<input type="hidden" name="assign" value="{{ one }}">{% endfor %}
      <button>Apply these changes</button>
    </form>
  {% endif %}
{% endif %}
{% endblock %}
```

Add `<a href="/agents">agents</a>` to `base.html`'s header nav so the page is
reachable from everywhere.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow tests/web
git commit -m "feat(web): agents page with plan, diff and apply

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Documentation and an end-to-end walkthrough

**Files:**
- Modify: `docs/web.md`, `docs/runs.md`, `docs/agents.md`
- Test: `tests/web/test_walkthrough.py` (create)

**Interfaces:**
- Consumes: everything Tasks 1–4 produced.
- Produces: no new code — the walkthrough is the test that fails if any single page stops being enough to drive a run.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_walkthrough.py
"""One run, driven entirely through the browser.

Each task's own tests prove its route works. This proves the app as a whole
is sufficient: a person who never opens a terminal can answer the gate that
is blocking a run, mark the phase it was blocking, record what it cost and
stop the run for the night — and see each of those on the timeline.
"""

from scieflow.core import events
from scieflow.web import auth


def post(client, path, **form):
    return client.post(path, follow_redirects=False,
                       data={auth.CSRF_FIELD: client.cookies[auth.CSRF_COOKIE], **form})


def test_a_whole_run_driven_from_the_browser(client, project):
    assert "r1" in client.get("/").text

    gate = client.get("/api/v1/gates").json()[0]
    assert post(client, f"/runs/r1/gates/{gate['id']}", answer="A").status_code == 303

    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="done")
    post(client, "/runs/r1/act", action="spend", experiment_runs="2")
    post(client, "/runs/r1/act", action="checkpoint", reason="user", detail="for the night")

    page = client.get("/runs/r1").text
    assert "stopped: user" in page
    assert "for the night" in page

    kinds = [e["type"] for e in events.read(project.run_dir("r1"))]
    for expected in ("gate.answered", "phase.done", "budget.recorded", "checkpoint"):
        assert expected in kinds, f"{expected} missing from {kinds}"
```

- [ ] **Step 2: Run test to verify it fails or passes for the right reason**

Run: `uv run pytest tests/web/test_walkthrough.py -v`
Expected: it may already pass on Tasks 1–4's code, which is the point — it
asserts the whole is sufficient, not that anything new exists. The event
names are the ones `src/scieflow/core/run/actions.py` and `gates.py` emit
today (`phase.<state>`, `checkpoint`, `budget.recorded`, `gate.answered`); if
one fails, read those modules and assert what they emit rather than changing
a module to match the test.

- [ ] **Step 3: Write the documentation**

In `docs/web.md`, replace the read-only framing. The page must say:

- The app is no longer a viewer: gates can be answered, phases marked,
  iterations advanced, runs checkpointed and resumed, spend recorded, jobs
  cancelled, and staffing changed — each doing exactly what the matching CLI
  command does, because both call the same service function.
- What is deliberately still CLI-only and why: `chats push`/`pull` (needs the
  passphrase, and AGENTS.md rule 16 forbids an agent starting it), DVC
  uploads (can be gigabytes), `--promote` (an explicit tier exception), and
  per-agent field edits.
- Starting a run and the coordinator conversation arrive in A1b; this
  milestone steers runs that already exist.
- The safety properties, unchanged: loopback only, a session token, CSRF on
  every mutation, and a test that fails if a mutating route appears without
  being declared.

In `docs/runs.md`, add the browser equivalent beside each CLI command in the
lifecycle section — `scieflow run phase` ↔ the phase form, `run advance` ↔
Advance iteration, `run checkpoint`/`run resume` ↔ the buttons, `run spend` ↔
Record spend, `gate answer` ↔ the gate form.

In `docs/agents.md`, add a short section pointing at `/agents`, stating that
it covers role assignment for the defaults or one workspace, that it shows
the same diff `scieflow agent configure` shows before writing anything, and
that fields, promotions and demotions remain CLI commands.

- [ ] **Step 4: Verify everything**

Run each and check the exit code explicitly — a pipe would hide a failure:

```bash
uv run pytest -q; echo "pytest: $?"
./scripts/check_legacy.sh; echo "legacy: $?"
uv run --group docs mkdocs build --strict; echo "mkdocs: $?"
```

Expected: `pytest: 0` with no failures, `legacy: 0` with every check `ok`,
`mkdocs: 0` with no warnings.

- [ ] **Step 5: Commit**

```bash
git add docs tests/web/test_walkthrough.py
git commit -m "docs(web): the app steers runs; end-to-end walkthrough test

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
