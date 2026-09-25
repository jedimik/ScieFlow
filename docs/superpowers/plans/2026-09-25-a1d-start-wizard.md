# Web control A1d — the Start wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start a run from the browser — pick a workflow, state the goal, set the budget and approval mode, choose who will coordinate it — and have its coordinator take the first turn.

**Architecture:** A new service function creates the workspace through the same `run.init` the CLI uses, then the existing conversation machinery takes over: the chosen agent is recorded and sent a first message composed from the workflow's skill. The wizard adds no third way to make a run and no second way to talk to one.

**Tech Stack:** Python, the existing service layer, FastAPI + Jinja. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-24-web-control-a1-design.md` — "Pages", the Start entry.

**On staffing.** The spec lists staffing among what the wizard collects. This plan does **not** put
the plan → diff → apply machinery into the wizard: A1a already built an Agents page that does exactly
that, and a second editor for the same YAML is how two editors drift. Instead the wizard *shows* the
chosen workflow's roles and who is currently assigned to each, and links to `/agents` to change them.
A run's staffing is resolved at dispatch time, so setting it immediately after creation is equivalent
to setting it during — and the one place that writes assignments stays one place.

**This completes A1.** A1a made an existing run steerable, A1b gave it a charter, A1c made it conversational; this lets you make one. After it, the programme moves to C (the draft workbench), D (manuscript git sync), B (the run explorer) and E (a container sandbox backend for macOS and native Windows).

**Branch.** Stack this on `feat/a1c-conversation` (PR #3) — it uses `service.say` and `service.set_conversation_agent`, which A1c added.

## Global Constraints

- **Every mutation goes through `scieflow.core.service`.** The route calls the service; it does not call `run.init` or `conversation` itself.
- **Runs are created exactly one way.** `service.create_run` calls `run.init.init_workspace`, the same function `scieflow run init` uses. No second creation path, no hand-built workspace.
- **A slug from a browser is untrusted input.** `init_workspace` does `workspace_root / slug` with **no validation of its own** — `Project.run_dir`'s `SLUG_RE` check is what refuses `../escape` and `a/b`, and it is not on that path. The service must validate before creating anything.
- **Every non-GET route is session-guarded and CSRF-protected**, with CSRF validated centrally in middleware. `tests/web/mutating_paths.py::MUTATING_PATHS` is extended, never shrunk, and each entry needs its `SAMPLES` counterpart.
- **A first turn is an ordinary conversation turn** — `service.say`, so it inherits the sandbox, the budget, the job record and the timeline.
- **Goal text is data, never instructions to ScieFlow.** It is written to `goal.md` and pinned into prompts; nothing interprets it.
- **Nothing regresses.** `uv run pytest -q` stays green (1207 passing at the start of this plan), `./scripts/check_legacy.sh` stays 25/25 `ok`, `uv run --group docs mkdocs build --strict` stays at zero warnings.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **A slug that escapes the workspace.** `init_workspace` joins the slug to `workspace_root` directly, and the wizard is the first thing to feed it a value from an HTTP form. `../escape`, `a/b`, an absolute path and an empty string must all be refused before anything is written. *(Task 1)*
2. **A slug that already exists.** `init_workspace` raises `FileExistsError`; that must become a readable refusal, and must not touch the existing run. *(Task 1)*
3. **Creation that fails partway.** `init_workspace` removes the directory on any exception, but the service does more than call it — if a later step fails, there must be no half-made run and no conversation record pointing at nothing. *(Task 1)*
4. **No conversational agent available.** A registry where nothing is both enabled and session-capable must not offer a launch the app cannot perform; the wizard has to say so instead of failing after the run exists. *(Task 3)*
5. **A goal that is very large, or contains text resembling a prompt delimiter.** It is stored verbatim and pinned into every prompt by the charter/compose path, so it must survive storage and dispatch literally, including when it pushes the first turn's prompt over the argv limit. *(Task 3)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/service.py` | `create_run`, `start_run` and the workflow listing the wizard renders |
| `src/scieflow/web/pages.py` | the Start page and its form post |
| `src/scieflow/web/api.py` | the JSON equivalents |
| `src/scieflow/web/templates/start.html` | the wizard |
| `tests/core/test_create_run.py` | slug safety, duplicates, partial failure |
| `tests/web/test_start_page.py` | the wizard end to end |

---

### Task 1: Creating a run through the service layer

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_create_run.py` (create)

**Interfaces:**
- Consumes: `scieflow.core.run.init.init_workspace(slug, goal_file, workspace_root, overrides, root) -> Path`, `Project.run_dir` (which validates via `SLUG_RE`), `service._ws`, `service.ServiceError`, `scieflow.core.menu.WORKFLOWS`.
- Produces:
  `service.workflows() -> list[dict]` — `[{"name", "ask", "roles"}]` from `menu.WORKFLOWS`, for the wizard to render;
  `service.create_run(project, slug, goal, *, workflow="", approval=None, max_iterations=None, max_experiment_runs=None, max_wall_minutes=None) -> dict` — the created run's detail;
  both raising `ServiceError` for anything a caller should see.

**The slug is the security-relevant part.** `init_workspace` joins it to `workspace_root` with no validation. `Project.run_dir` has the `SLUG_RE` check that refuses `..` and `/`, but nothing calls it on the creation path. Validate by asking `project.run_dir(slug)` for the intended directory and using *that*, so the wizard cannot write outside `workspace/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_create_run.py
"""Creating a run through the service layer.

The wizard is the first thing to hand `run.init` a slug that came from an
HTTP form, and `init_workspace` joins that slug to the workspace root with no
validation of its own — so the refusals below are the load-bearing part of
this module, not edge cases.
"""

import sys
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n')
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: per-campaign\nmax_iterations: 3\n"
        "max_experiment_runs: 10\nmax_wall_minutes: 60\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    (tmp_path / "workspace").mkdir()
    return Project(tmp_path)


def test_create_run_makes_a_workspace_with_its_goal(project):
    detail = service.create_run(project, "r1", "Find a better catalyst.")
    ws = project.run_dir("r1")
    assert (ws / "goal.md").read_text() == "Find a better catalyst."
    assert (ws / "status.yml").exists() and (ws / "budget.yml").exists()
    assert detail["status"]["run"] == "r1"


def test_create_run_applies_the_defaults(project):
    from scieflow.core.run import budget

    service.create_run(project, "r1", "a goal")
    limits = budget.read_budget(project.run_dir("r1"))["limits"]
    assert limits["max_iterations"] == 3 and limits["max_wall_minutes"] == 60


def test_create_run_applies_overrides(project):
    from scieflow.core.run import budget, status

    service.create_run(project, "r1", "a goal", approval="autonomous",
                       max_iterations=7, max_wall_minutes=120)
    ws = project.run_dir("r1")
    assert status.read_status(ws)["approval"] == "autonomous"
    limits = budget.read_budget(ws)["limits"]
    assert limits["max_iterations"] == 7 and limits["max_wall_minutes"] == 120


@pytest.mark.parametrize("bad", ["../escape", "a/b", "/abs", "", "   ", "..",
                                 "workspace/../escape"])
def test_a_slug_that_escapes_the_workspace_is_refused(project, bad):
    """`init_workspace` joins the slug to the workspace root with no checks of
    its own, and this is the first caller whose slug comes from a form."""
    with pytest.raises(service.ServiceError):
        service.create_run(project, bad, "a goal")
    made = [p.name for p in (project.root / "workspace").iterdir()]
    assert made == [], f"something was created outside the intended run: {made}"
    assert not (project.root.parent / "escape").exists()


def test_a_duplicate_slug_is_refused_and_leaves_the_first_run_alone(project):
    service.create_run(project, "r1", "the original goal")
    with pytest.raises(service.ServiceError, match="exists"):
        service.create_run(project, "r1", "a different goal")
    assert (project.run_dir("r1") / "goal.md").read_text() == "the original goal"


def test_an_empty_goal_is_refused(project):
    with pytest.raises(service.ServiceError, match="goal"):
        service.create_run(project, "r1", "   ")
    assert not project.run_dir("r1").exists()


def test_a_failure_partway_leaves_no_half_made_run(project, monkeypatch):
    """`init_workspace` cleans up after itself, but the service does more than
    call it. A run that exists with no status, or a conversation record
    pointing at a workspace that was removed, would both be worse than a
    clean refusal."""
    from scieflow.core.run import init as init_mod

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(init_mod, "init_workspace", explode)
    with pytest.raises(service.ServiceError):
        service.create_run(project, "r1", "a goal")
    assert not project.run_dir("r1").exists()


def test_an_unknown_workflow_is_refused_before_anything_is_written(project):
    with pytest.raises(service.ServiceError, match="workflow"):
        service.create_run(project, "r1", "a goal", workflow="nonesuch")
    assert not project.run_dir("r1").exists()


def test_workflows_lists_the_registry(project):
    names = [w["name"] for w in service.workflows()]
    assert "research-loop" in names and "lit-review" in names
    assert all(w["ask"] for w in service.workflows())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_create_run.py -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'create_run'`.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/service.py`:

```python
def workflows() -> list[dict]:
    """The workflows the Start wizard offers, from the one registry the TUI
    menu already uses — so a workflow added there appears here too."""
    from scieflow.core import menu

    return [{"name": name, "ask": spec.get("ask", ""),
             "roles": list(spec.get("roles") or [])}
            for name, spec in menu.WORKFLOWS.items()]


def create_run(project: Project, slug: str, goal: str, *, workflow: str = "",
               approval: str | None = None, max_iterations: int | None = None,
               max_experiment_runs: int | None = None,
               max_wall_minutes: int | None = None) -> dict:
    """Create a run workspace, the same way `scieflow run init` does.

    The slug is validated by asking `Project.run_dir` for the intended
    directory before anything is written. `init_workspace` joins the slug to
    the workspace root itself with no checks, and this is the first caller
    whose slug can arrive from an HTTP form.
    """
    from scieflow.core import menu
    from scieflow.core.run import init as init_mod

    if not goal or not goal.strip():
        raise ServiceError("a run needs a goal")
    if workflow and workflow not in menu.WORKFLOWS:
        raise ServiceError(
            f"unknown workflow: {workflow} (known: {', '.join(menu.WORKFLOWS)})")
    try:
        target = project.run_dir(slug)          # refuses .., /, and empty
    except ProjectError as exc:
        raise ServiceError(str(exc)) from exc
    if target.exists():
        raise ServiceError(f"a run named {target.name} already exists")

    overrides = {"approval": approval, "max_iterations": max_iterations,
                 "max_experiment_runs": max_experiment_runs,
                 "max_wall_minutes": max_wall_minutes}
    goal_file = Path(tempfile.mkdtemp()) / "goal.md"
    goal_file.write_text(goal)
    try:
        init_mod.init_workspace(target.name, goal_file, project.workspace_root,
                                overrides, project.root)
    except FileExistsError as exc:
        raise ServiceError(f"a run named {target.name} already exists") from exc
    except (OSError, ValueError, KeyError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise ServiceError(f"could not create {target.name}: {exc}") from exc
    finally:
        shutil.rmtree(goal_file.parent, ignore_errors=True)
    return run_detail(project, target.name)
```

Add `shutil` and `tempfile` to the module's imports, and `ProjectError` to the
`scieflow.core.project` import if it is not already there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_create_run.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_create_run.py
git commit -m "feat(core): create a run through the service layer, with a safe slug"
```

---

### Task 2: The Start page

**Files:**
- Create: `src/scieflow/web/templates/start.html`
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/api.py`, `src/scieflow/web/templates/base.html`, `src/scieflow/web/templates/dashboard.html`, `tests/web/mutating_paths.py`
- Test: `tests/web/test_start_page.py` (create)

**Interfaces:**
- Consumes: `service.workflows`, `service.create_run`, `service.conversational_agents` (added by the previous milestone), `pages.MUTATE`, `pages._back`, `auth.csrf_token`.
- Produces: `GET /start`, `POST /start` (page, redirects to the new run), `POST /api/v1/runs` (JSON).

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_start_page.py
"""Starting a run from the browser."""

from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_start_page_offers_every_workflow(client):
    page = client.get("/start").text
    assert "research-loop" in page and "lit-review" in page
    assert 'action="/start"' in page and 'name="csrf_token"' in page


def test_starting_a_run_creates_it_and_goes_to_its_page(client, project):
    response = post(client, "/start", slug="new-run", goal="Find a catalyst.",
                    workflow="research-loop")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/new-run"
    assert (project.run_dir("new-run") / "goal.md").read_text() == "Find a catalyst."


def test_the_budget_and_approval_from_the_form_are_applied(client, project):
    from scieflow.core.run import budget, status

    post(client, "/start", slug="new-run", goal="a goal", workflow="research-loop",
         approval="autonomous", max_iterations="7")
    ws = project.run_dir("new-run")
    assert status.read_status(ws)["approval"] == "autonomous"
    assert budget.read_budget(ws)["limits"]["max_iterations"] == 7


def test_a_bad_slug_is_refused_on_the_page_not_with_a_traceback(client, project):
    response = post(client, "/start", slug="../escape", goal="a goal")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/start?error=")
    assert "slug" in client.get(response.headers["location"]).text.lower()
    assert not (project.root.parent / "escape").exists()


def test_a_duplicate_slug_is_refused_on_the_page(client, project):
    post(client, "/start", slug="dup", goal="the first goal")
    response = post(client, "/start", slug="dup", goal="the second goal")
    assert response.headers["location"].startswith("/start?error=")
    assert (project.run_dir("dup") / "goal.md").read_text() == "the first goal"


def test_an_empty_goal_is_refused_on_the_page(client, project):
    response = post(client, "/start", slug="no-goal", goal="   ")
    assert response.headers["location"].startswith("/start?error=")
    assert not project.run_dir("no-goal").exists()


def test_goal_text_is_escaped_when_the_error_page_echoes_it(client):
    response = post(client, "/start", slug="../bad", goal="<script>alert('x')</script>")
    page = client.get(response.headers["location"]).text
    assert "<script>alert" not in page


def test_the_api_creates_a_run(client, project):
    response = post(client, "/api/v1/runs", slug="via-api", goal="a goal")
    assert response.status_code == 200
    assert response.json()["status"]["run"] == "via-api"


def test_the_dashboard_links_to_start(client):
    assert 'href="/start"' in client.get("/").text


def test_the_wizard_points_at_the_agents_page_for_staffing(client):
    """Staffing has one editor, and it is not this form."""
    assert 'href="/agents"' in client.get("/start").text
```

Extend `tests/web/mutating_paths.py` — `MUTATING_PATHS` **and** `SAMPLES` together, or the generated guard tests fail:

```python
    "/start": {"post"},
    "/api/v1/runs": {"post"},
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_start_page.py tests/web/test_read_only.py -v`
Expected: FAIL — `/start` 404s and the inventory reports the two new paths declared but absent.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/web/pages.py`:

```python
@router.get("/start", response_class=HTMLResponse)
async def start_page(request: Request, error: str = "") -> HTMLResponse:
    project = _project(request)
    return TEMPLATES.TemplateResponse(request, "start.html", {
        "workflows": service.workflows(),
        "agents": service.conversational_agents(project),
        "defaults": project.defaults(),
        "error": error,
        "csrf": auth.csrf_token(request),
    })


@router.post("/start", dependencies=MUTATE)
def start_run(request: Request, slug: str = Form(...), goal: str = Form(...),
              workflow: str = Form(""), approval: str = Form(""),
              max_iterations: int = Form(0), max_experiment_runs: int = Form(0),
              max_wall_minutes: int = Form(0)):
    try:
        service.create_run(
            _project(request), slug, goal, workflow=workflow,
            approval=approval or None,
            max_iterations=max_iterations or None,
            max_experiment_runs=max_experiment_runs or None,
            max_wall_minutes=max_wall_minutes or None)
    except service.ServiceError as exc:
        return RedirectResponse(f"/start?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/runs/{quote(slug)}", status_code=303)
```

Note `start_run` is a plain `def`, not `async def`: creating a run writes
several files under a lock, and a blocking handler on the event loop is the
defect the previous milestone had to fix. Anything that touches the
filesystem belongs in Starlette's threadpool.

In `src/scieflow/web/api.py`, the JSON equivalent:

```python
@router.post("/runs", dependencies=MUTATE, tags=["runs"])
def create_run(request: Request, slug: str = Form(...), goal: str = Form(...),
               workflow: str = Form(""), approval: str = Form("")) -> dict:
    """Create a run workspace and return its detail."""
    return service.create_run(_project(request), slug, goal,
                              workflow=workflow, approval=approval or None)
```

Create `src/scieflow/web/templates/start.html`:

```html
{% extends "base.html" %}
{% block title %}Start a run — ScieFlow{% endblock %}
{% block nav %}<a href="/">← all runs</a>{% endblock %}
{% block content %}
<h1>Start a run</h1>
{% if error %}<p class="state-failed" role="alert">{{ error }}</p>{% endif %}

<form method="post" action="/start">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">

  <p><label>Name <input name="slug" required
       pattern="[A-Za-z0-9][A-Za-z0-9._ -]*"
       title="Letters, digits, dot, underscore, space or dash"></label>
     <span class="dim">becomes <code>workspace/&lt;name&gt;</code></span></p>

  <p><label>Workflow
    <select name="workflow">
      {% for workflow in workflows %}
      <option value="{{ workflow.name }}">{{ workflow.name }} — {{ workflow.ask }}</option>
      {% endfor %}
    </select></label></p>

  <p><label>Goal<br>
    <textarea name="goal" rows="5" required
              placeholder="What this run is for"></textarea></label></p>

  <p><label>Approval
    <select name="approval">
      {% for mode in ["per-campaign", "autonomous"] %}
      <option {% if mode == defaults.approval %}selected{% endif %}>{{ mode }}</option>
      {% endfor %}
    </select></label></p>

  <p class="actions">
    <label>iterations <input name="max_iterations" type="number" min="1"
           value="{{ defaults.max_iterations }}"></label>
    <label>experiment runs <input name="max_experiment_runs" type="number" min="1"
           value="{{ defaults.max_experiment_runs }}"></label>
    <label>wall minutes <input name="max_wall_minutes" type="number" min="1"
           value="{{ defaults.max_wall_minutes }}"></label>
  </p>

  <button>Create the run</button>
</form>
{% endblock %}
```

Add `<a href="/start">start a run</a>` to `base.html`'s nav, and the same link
prominently on `dashboard.html` — an empty dashboard should say how to make
its first run rather than just being empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass; the inventory matches `MUTATING_PATHS` exactly.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web
git commit -m "feat(web): start a run from the browser"
```

---

### Task 3: Launching the coordinator

**Files:**
- Modify: `src/scieflow/core/service.py`, `src/scieflow/web/pages.py`, `src/scieflow/web/templates/start.html`
- Test: `tests/core/test_create_run.py` (append), `tests/web/test_start_page.py` (append)

**Interfaces:**
- Consumes: `service.create_run`, `service.set_conversation_agent`, `service.say`, `service.conversational_agents`, `menu.WORKFLOWS`.
- Produces: `service.start_run(project, slug, goal, agent, *, workflow="", **limits) -> dict` — creates the run, records the agent, and sends the first turn; returns `{"run": ..., "turn": ...}` with `turn` absent when no agent was chosen.

**Creating and launching are separate functions on purpose.** A run can be made without an agent — the registry may have none that can converse — and the wizard must still work in that case, leaving a run someone can pick an agent for later on its own page. `start_run` is the convenience that does both.

**Follow the existing coordinator-prompt convention rather than inventing one.** `src/scieflow/core/menu.py` already composes the prompt that hands a run to a coordinator (look for the function returning `"Read AGENTS.md and … continue per {skill}."`). Read it and match its shape for the *start* case, naming the workflow's `skill` path from `menu.WORKFLOWS`. A second, divergent prompt convention would mean the TUI and the browser tell coordinators different things about the same run.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_create_run.py`)

```python
def test_start_run_creates_the_run_and_takes_the_first_turn(project_with_agent):
    from scieflow.core.run import conversation

    project = project_with_agent
    result = service.start_run(project, "r1", "Find a catalyst.", "stub",
                               workflow="research-loop")
    ws = project.run_dir("r1")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub"
    assert [t["role"] for t in doc["turns"]] == ["human", "agent"]
    assert result["turn"]["role"] == "agent"


def test_the_first_turn_names_the_workflow_skill(project_with_agent):
    """The prompt must follow the convention `menu` already uses to hand a run
    to a coordinator, so the TUI and the browser say the same thing."""
    from scieflow.core.run import conversation

    project = project_with_agent
    service.start_run(project, "r1", "Find a catalyst.", "stub",
                      workflow="research-loop")
    first = conversation.read(project.run_dir("r1"))["turns"][0]["text"]
    assert "skills/research-loop/SKILL.md" in first
    assert "Find a catalyst." in first


def test_start_run_without_an_agent_still_creates_the_run(project):
    """A registry with nothing conversational must still let someone make a
    run — they can choose an agent later on its page."""
    result = service.start_run(project, "r1", "a goal", "")
    assert project.run_dir("r1").exists()
    assert "turn" not in result or result["turn"] is None


def test_start_run_refuses_an_agent_that_cannot_converse_before_creating(project):
    """The run must not exist afterwards: a half-started run with no way to
    talk to it is worse than a refusal."""
    with pytest.raises(service.ServiceError, match="conversation"):
        service.start_run(project, "r1", "a goal", "stub")   # no session_cmd
    assert not project.run_dir("r1").exists()


def test_a_very_large_goal_survives_storage_and_the_first_turn(project_with_agent):
    """The goal is stored verbatim and pinned into every prompt, so it can push
    the first turn's prompt past the argv limit."""
    project = project_with_agent
    goal = "G" * 150_000
    from scieflow.core.run import conversation

    service.start_run(project, "r1", goal, "stub", workflow="research-loop")
    ws = project.run_dir("r1")
    assert (ws / "goal.md").read_text() == goal
    agent_turn = next(t for t in conversation.read(ws)["turns"] if t["role"] == "agent")
    assert agent_turn["job_id"], "the first turn was never dispatched"


def test_goal_text_is_stored_literally(project_with_agent):
    """Braces and delimiter-looking text are data, not templating."""
    project = project_with_agent
    goal = "Use {model} and --- and ## headings; mind `backticks`."
    service.start_run(project, "r1", goal, "stub", workflow="research-loop")
    assert (project.run_dir("r1") / "goal.md").read_text() == goal
```

Add a `project_with_agent` fixture to that file: the same `project` fixture,
with `stub` additionally given `family: claude`, a `session_cmd` and a
`resume_cmd` so it can hold a conversation. The previous milestone configured
`stub` this way in `tests/core/test_service.py` — copy that shape. The plain
`project` fixture keeps `stub` non-conversational, which is what the refusal
test needs.

Append to `tests/web/test_start_page.py`:

```python
def test_choosing_an_agent_launches_the_coordinator(client, project):
    from scieflow.core.run import conversation

    post(client, "/start", slug="launched", goal="a goal",
         workflow="research-loop", agent="stub")
    doc = conversation.read(project.run_dir("launched"))
    assert doc["agent"] == "stub"
    assert doc["turns"], "no first turn was taken"


def test_the_wizard_says_so_when_no_agent_can_converse(client, project, monkeypatch):
    from scieflow.core import service as service_mod

    monkeypatch.setattr(service_mod, "conversational_agents", lambda project: [])
    page = client.get("/start").text
    assert "no agent" in page.lower()
    assert 'name="agent"' not in page or "disabled" in page
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_create_run.py -k start_run -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'start_run'`.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/service.py`:

```python
def start_run(project: Project, slug: str, goal: str, agent: str = "", *,
              workflow: str = "", **limits) -> dict:
    """Create a run and, when an agent is named, have it take the first turn.

    The agent is checked *before* the run is created: a half-started run with
    no way to talk to it is worse than a refusal.
    """
    if agent:
        _check_can_converse(project, agent)      # raises ServiceError
    run = create_run(project, slug, goal, workflow=workflow, **limits)
    if not agent:
        return {"run": run, "turn": None}
    set_conversation_agent(project, slug, agent)
    said = say(project, slug, _opening_prompt(slug, goal, workflow))
    return {"run": run, "turn": said["turn"]}
```

`_check_can_converse(project, agent)` is the per-reason check
`set_conversation_agent` already performs — factor it out of that function and
call it from both, rather than writing the conditions a second time.

`_opening_prompt(slug, goal, workflow)` composes the first message following
`menu`'s existing convention. Read `src/scieflow/core/menu.py` first and
mirror its wording for the start case, naming `menu.WORKFLOWS[workflow]["skill"]`
when a workflow was chosen.

In `src/scieflow/web/pages.py`, take an `agent` field on the Start form and
call `service.start_run` instead of `service.create_run`, keeping the same
redirect and error handling. In `start.html`, add the picker:

```html
  <p class="dim">Roles for this workflow are staffed on the
     <a href="/agents">agents page</a> — a run resolves its staffing when it
     dispatches, so you can set it now or straight after creating the run.</p>

  <p><label>Coordinator
    {% if not agents %}
      <span class="dim">no agent in this project can hold a conversation —
        the run will be created without one, and you can choose later on its
        page once an agent has <code>session_cmd</code> and
        <code>resume_cmd</code> configured.</span>
      <input type="hidden" name="agent" value="">
    {% else %}
      <select name="agent">
        <option value="">none — just create the run</option>
        {% for name in agents %}<option>{{ name }}</option>{% endfor %}
      </select>
    {% endif %}
  </label></p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_create_run.py tests/web/test_start_page.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow tests
git commit -m "feat(core): start a run and hand it to its coordinator"
```

---

### Task 4: The carried follow-up — cancel must not block the server

**Files:**
- Modify: `src/scieflow/web/api.py`, `src/scieflow/web/pages.py`
- Test: `tests/web/test_sse.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: no new interface — `api.cancel` and `pages.cancel_job` become synchronous `def`.

**Why this is in this plan.** The previous milestone found that a conversation turn blocked the whole single-process web server, because `async def` handlers called straight into blocking work on the event loop. That was fixed for the turn routes. Its reviewer then noticed the **cancel** routes have the same shape: `service.cancel_job` → `jobs.cancel` → `_kill_group`, which polls with `time.sleep(0.1)` for up to `KILL_GRACE = 10.0` seconds against a process that ignores `SIGTERM`.

Ten seconds is not thirty minutes, but it blocks the event loop on the very button that exists to get you out of a long-running job — and `docs/web.md` promises the page stays live. It is a one-word change per route and it belongs with the other web work rather than drifting.

- [ ] **Step 1: Write the failing test** (append to `tests/web/test_sse.py`)

```python
def test_the_server_keeps_answering_while_a_cancel_is_in_flight(live, project):
    """`jobs.cancel` polls for up to KILL_GRACE seconds against a process that
    ignores SIGTERM. On the event loop that would freeze every other route —
    including the pages this button exists to get you back to."""
    import threading

    import httpx

    job = _start_ignoring_sigterm(project)       # see the helper below
    done = threading.Event()

    def cancel():
        with httpx.Client(base_url=live.url, cookies=live.cookies) as client:
            client.post(f"/api/v1/jobs/{job.id}/cancel",
                        data={auth.CSRF_FIELD: live.csrf}, timeout=30.0)
        done.set()

    threading.Thread(target=cancel, daemon=True).start()
    time.sleep(0.5)
    assert not done.is_set(), "the cancel finished too fast to prove anything"

    with httpx.Client(base_url=live.url, cookies=live.cookies) as probe:
        started = time.monotonic()
        assert probe.get("/healthz", timeout=2.0).status_code == 200
        assert time.monotonic() - started < 2.0
```

Follow the `live`-server fixture the other tests in that file already use —
a `TestClient` runs the app in-process and will not show this. `_start_ignoring_sigterm`
starts a job whose command traps `SIGTERM` and sleeps, so `_kill_group` has to
wait out its grace period; write it beside the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_sse.py -k cancel_is_in_flight -v`
Expected: FAIL — `/healthz` times out or takes longer than 2 seconds, because the cancel is holding the event loop.

- [ ] **Step 3: Write the implementation**

Change both handlers from `async def` to `def`:

```python
@router.post("/jobs/{job_id}/cancel", dependencies=MUTATE, tags=["jobs"])
def cancel(request: Request, job_id: str) -> dict:
```

```python
@router.post("/runs/{slug}/jobs/{job_id}/cancel", dependencies=MUTATE)
def cancel_job(request: Request, slug: str, job_id: str):
```

Neither body contains `await`, so nothing else changes. Then check the rest of
both modules the same way: any handler that calls a service function which
touches a process, waits on a lock, or walks the filesystem should be a plain
`def`. List in your report every handler you checked and what you concluded —
the previous milestone's fix report claimed "no other handler needs this" and
was wrong, which is why these two are still here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_sse.py
git commit -m "fix(web): cancel a job in the threadpool, not on the event loop"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/web.md`, `docs/runs.md`, `docs/cli.md`
- Test: none — this task's verification is the three commands in Step 2.

- [ ] **Step 1: Write the documentation**

In `docs/web.md`, add the Start page to the Pages table and `POST /api/v1/runs` to the API table. Say what the wizard collects (name, workflow, goal, approval, the three budget limits, and optionally a coordinator), that the name becomes `workspace/<name>` and is validated, and that choosing a coordinator has it take the first turn — while leaving the coordinator unset simply creates the run, which is what happens when no configured agent can hold a conversation.

In `docs/runs.md`, add the browser equivalent beside `scieflow run init`, in the same shape the other lifecycle commands use there. State plainly that both create the workspace through the same `run.init`, because that is true and is the reason the two cannot drift.

In `docs/cli.md`, nothing new is added — but check whether its `run init` entry should cross-reference the Start page now that one exists, and say in your report what you decided.

Check every claim against the code as it shipped. In particular, do not write that the CLI and the browser "go through the same service function" — for run *actions* they do not; `run/cli.py` calls `actions.*` directly. For run *creation* they do both reach `run.init.init_workspace`, which is the true and useful statement here.

- [ ] **Step 2: Verify everything**

Run each and check the exit code explicitly — a pipe would hide a failure:

```bash
uv run pytest -q; echo "pytest: $?"
./scripts/check_legacy.sh; echo "legacy: $?"
uv run --group docs mkdocs build --strict; echo "mkdocs: $?"
```

Expected: `pytest: 0` with no failures, `legacy: 0` with every check `ok`, `mkdocs: 0` with no warnings.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs: starting a run from the browser"
```
