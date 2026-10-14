"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.

Every handler in this module is plain `def`, not `async def` — `serve.py`
runs one uvicorn process, and a synchronous handler runs in Starlette's
threadpool instead of blocking that process's single event loop. Most of
these block only briefly (a YAML read, a template render); `say` and
`cancel_job` block far longer and say so in their own docstrings.
`tests/web/async_allowlist.py` and `tests/web/test_async_routes.py` enforce
this for the whole app — the SSE streams in `scieflow.web.sse` are the only
routes that genuinely need to run on the event loop.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import FormData

from scieflow.core import jobs as jobs_mod
from scieflow.core import service
from scieflow.core.project import ProjectError
from scieflow.web import auth, review_views
from scieflow.web.app import TEMPLATES

router = APIRouter(dependencies=[Depends(auth.require_session)])

MUTATE = [Depends(auth.csrf_protect)]


def _project(request: Request):
    return request.app.state.project


#: The send form's allowed `mode` values. 'harness-command' is an explicit
#: intent marker only: with CERTIFIED_POLICIES empty the turn runs as
#: ordinary chat and the page says the capability is not certified — never a
#: claim of command execution.
SEND_MODES = ('chat', 'harness-command')

_DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)')


class _FormError(ValueError):
    """A controlled form-field rejection: 400 for the API surface, a
    redirect-with-error for the page surface. Nothing was written."""


def _single_field(form, name):
    """One value for `name`, or a controlled rejection.

    A request body carrying the same key twice (a hand-crafted body, or a
    browser bug) is ambiguity, not a value: the form parser keeps the last
    one, so the controller must read the multi-dict directly to see the
    duplication at all.
    """
    values = form.getlist(name)
    if not values:
        return None
    if len(values) > 1:
        raise _FormError(f'duplicate form field: {name}')
    value = values[0]
    if type(value) is not str:
        raise _FormError(f'invalid form field: {name}')
    return value


def _revision_field(form, name, *, default=None):
    """A canonical decimal revision string (0-9, no leading zeros), or None.

    An ABSENT field means "no pin" — script clients and the legacy form omit
    it. A PRESENT field is a claim about state, and the rendered forms always
    carry a canonical token: a present-but-empty or non-canonical value is a
    controlled rejection, never a parse-to-int of attacker text.
    """
    raw = _single_field(form, name)
    if raw is None:
        return default
    if _DECIMAL.fullmatch(raw) is None:
        raise _FormError(f'{name} must be a canonical decimal revision (0-9)')
    return raw


def _mode_field(form):
    """The send form's mode: absent defaults to 'chat'; a bad enum rejects."""
    raw = _single_field(form, 'mode')
    if raw is None or raw == '':
        return 'chat'
    if raw not in SEND_MODES:
        raise _FormError(f'unknown mode: {raw!r} (chat or harness-command)')
    return raw


def _bool_field(form, name, *, default=False):
    """A checkbox-shaped field: absent means the default (unchecked)."""
    raw = _single_field(form, name)
    if raw is None or raw == '':
        return default
    if raw.lower() in ('true', 'on', '1', 'yes'):
        return True
    if raw.lower() in ('false', 'off', '0', 'no'):
        return False
    raise _FormError(f'{name} must be a boolean')


async def _conversation_form(request: Request):
    """The raw form multi-dict, once, with the body already cached by the
    CSRF middleware's read (see auth._supplied_csrf) so the route's own
    Form(...) parsing still sees the same bytes.

    This is an `async def` DEPENDENCY, not a handler: FastAPI/Starlette
    resolves dependencies on the event loop, then runs a plain `def`
    handler in the threadpool. Reading the body needs the loop
    (`request.form()`); the handler itself must stay off it (see
    `tests/web/async_allowlist.py`), so the route signature takes
    `form: FormData = Depends(_conversation_form)` and stays `def`.
    """
    return await request.form()


def _submission_from_form(form, *, marked_requires_revision=False):
    """Build the HumanSubmission inside the controller, from the form.

    The client's actor is ignored entirely: identity is the authenticated
    session, and `service.say` pins the actor to 'human' for marked turns.
    """
    from scieflow.core.turn_input import accept_human_submission

    mode = _mode_field(form)
    revision = _revision_field(form, 'selection_revision')
    if mode == 'harness-command' and revision is None:
        # A marked submission must pin the state it means to mark; without
        # that, a stale tab could mark intent against an unknown revision.
        raise _FormError('mode harness-command requires selection_revision')
    try:
        return accept_human_submission(_single_field(form, 'message') or '',
                                       mode=None if mode == 'chat' else mode,
                                       selection_revision=revision)
    except ValueError as exc:
        raise _FormError(str(exc)) from exc


def _as_chat(submission):
    """The same message as an ordinary chat submission: a marked send on a
    deployment with no certified policies runs as charter-first chat, with
    a visible notice — never a silent enablement or a silent refusal.

    The marked submission's selection_revision is carried through, not
    dropped: the downgraded chat send must still refuse on a stale
    revision (check_revision with required=False compares when present),
    so the weaker mode can never mark intent against a selection the user
    no longer sees."""
    from scieflow.core.turn_input import accept_human_submission

    return accept_human_submission(submission.message, mode='chat',
                                   selection_revision=submission.selection_revision)


def _reset_fields_from_form(form):
    """The hand-over form's reset controls, validated as a pair.

    `reset_session` without `confirm_new_session` is a deliberate refusal —
    the confirm checkbox is what makes a session-destroying action
    deliberate — and an explicit reset always requires a revision.
    """
    reset = _bool_field(form, 'reset_session')
    confirm = _bool_field(form, 'confirm_new_session')
    if reset and not confirm:
        raise _FormError('resetting the session requires the new-session confirmation')
    return reset


def _percent(fraction: float | None) -> int:
    """A remaining fraction as a whole percent, clamped for the CSS bar."""
    if fraction is None:
        return 0
    return max(0, min(100, round(fraction * 100)))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    project = _project(request)
    runs = service.list_runs_with_budget(project)
    detail = {run["slug"]: {"remaining": {dim: _percent(value)
                                          for dim, value in (run["remaining"] or {}).items()}}
              for run in runs}
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {
        "runs": runs,
        "detail": detail,
        "gates": service.open_gates(project, slugs=[run["slug"] for run in runs]),
        "csrf": auth.csrf_token(request),
    })


#: The workflow the form's `<select>` shows chosen when nothing else applies
#: — a fresh visit, or a refusal that carried no workflow at all. Not "no
#: workflow": `init_workspace` (what this wizard calls, unconditionally)
#: always writes the loop-shaped `status.yml` `workspace.describe` reads a
#: `kind` of "loop" from — research-loop's own phases and roles — so it is
#: the one entry in `service.workflows()` whose skill actually matches the
#: run this page produces. Without an explicit default here, an HTML
#: `<select>` with none of its `<option>`s marked `selected` silently submits
#: whichever one is listed first (`lit-review`) for a visitor who never
#: touched the dropdown.
DEFAULT_WORKFLOW = "research-loop"


def _start_form(defaults: dict, submitted: dict | None = None) -> dict:
    """The values the start form should show: whatever was just submitted
    and refused, falling back field-by-field to the project's defaults —
    which is also what an empty `submitted` (the first visit) resolves to."""
    submitted = submitted or {}
    return {
        "scite_use": ("on" if "scite_use" not in submitted else submitted["scite_use"]),
        "scite_no_ask": submitted.get("scite_no_ask", ""),
        "slug": submitted.get("slug", ""),
        "goal": submitted.get("goal", ""),
        "workflow": submitted.get("workflow") or DEFAULT_WORKFLOW,
        "agent": submitted.get("agent", ""),
        "approval": submitted.get("approval") or defaults.get("approval", ""),
        "max_iterations": (submitted.get("max_iterations")
                           or defaults.get("max_iterations", "")),
        "max_experiment_runs": (submitted.get("max_experiment_runs")
                                or defaults.get("max_experiment_runs", "")),
        "max_wall_minutes": (submitted.get("max_wall_minutes")
                             or defaults.get("max_wall_minutes", "")),
    }


def _render_start(request: Request, project, *, error: str = "",
                  submitted: dict | None = None) -> HTMLResponse:
    submitted = submitted or {}
    use = "scite_use" not in submitted or bool(submitted["scite_use"])
    scite = review_views.scite_control(project, use=use,
                                       no_ask=bool(submitted.get("scite_no_ask")))
    return TEMPLATES.TemplateResponse(request, "start.html", {
        "workflows": service.workflows(),
        "agents": service.conversational_agents(project),
        "form": _start_form(project.defaults(), submitted),
        "scite": scite,
        "error": error,
        "csrf": auth.csrf_token(request),
    })


@router.get("/start", response_class=HTMLResponse)
def start_page(request: Request) -> HTMLResponse:
    """Plain `def`, not `async def`: `service.workflows()`,
    `service.conversational_agents` and `project.defaults()` all read a YAML
    file straight off disk on the event loop — smaller than the blocking
    calls earlier milestones had to move off it, but the same shape, so it
    goes in the threadpool like the rest of this page's handlers.

    No `error` query parameter: a refusal is now rendered directly by
    `start_run` (see its docstring), not redirected here, so there is
    nothing left that would ever set one."""
    return _render_start(request, _project(request))


@router.post("/start", dependencies=MUTATE)
def start_run(request: Request, slug: str = Form(...), goal: str = Form(...),
              workflow: str = Form(""), agent: str = Form(""), approval: str = Form(""),
              max_iterations: int = Form(0), max_experiment_runs: int = Form(0),
              max_wall_minutes: int = Form(0), scite_present: str = Form(""),
              scite_use: str = Form(""), scite_no_ask: str = Form("")):
    """Plain `def`, not `async def` — see `say` below: creating a run writes
    several files under a lock, and that blocking work belongs in Starlette's
    threadpool, not on the event loop.

    A pre-creation refusal re-renders the form in place rather than
    redirecting to `/start?error=...`: the goal is free text that can run to
    paragraphs, and round-tripping that (plus the slug, workflow, approval
    and three budget numbers) through a query string risks a URL past what a
    server or browser will accept, and leaves it sitting in the URL bar and
    any access log besides. Rendering directly costs this one path the
    post/redirect/get guarantee every other mutation here keeps — reloading
    a refused submission re-POSTs it, and the browser will ask first. Nothing
    is written for this kind of refusal — the same is true whether the agent
    or the slug or the goal is what's rejected.

    A *post*-creation failure (`service.RunStartedError` — the sandbox
    refused the first turn, the budget was already exhausted, the opening
    prompt was oversized) is different: the run itself was made, under the
    slug the error carries, so this redirects to that run's own page with
    the error visible, the same as any other mutation on an existing run —
    the post/redirect/get guarantee holds there too, and resubmitting from a
    blank form would only produce "a run named X already exists" with no way
    back to it.

    A successful submission also redirects, using the canonical slug
    `service.start_run` reports the run was actually created under — which
    can differ from what was typed (`Project.run_dir` strips a `workspace/`
    prefix, a trailing slash, surrounding whitespace) — never the raw form
    value, or a redirect could land on a slug that 404s while the real run
    sits one path segment over.

    Calls `service.start_run` instead of `service.create_run` so a chosen
    agent takes the first turn as part of this same request — the agent is
    checked before anything is created, so a refusal *before* creation
    behaves exactly like the existing refusals: nothing written, form
    re-rendered with what was typed.
    """
    project = _project(request)
    scite = review_views.start_scite(project, {"scite_present": scite_present,
                                               "scite_use": scite_use,
                                               "scite_no_ask": scite_no_ask})
    submitted = {"slug": slug, "goal": goal, "workflow": workflow, "agent": agent,
                "approval": approval,
                "max_iterations": max_iterations or None,
                "max_experiment_runs": max_experiment_runs or None,
                "max_wall_minutes": max_wall_minutes or None,
                "scite_use": scite_use, "scite_no_ask": scite_no_ask}
    try:
        result = service.start_run(
            project, slug, goal, agent, workflow=workflow,
            approval=approval or None,
            max_iterations=max_iterations or None,
            max_experiment_runs=max_experiment_runs or None,
            max_wall_minutes=max_wall_minutes or None)
    except service.RunStartedError as exc:
        # The run exists: its scite choice is still recorded, and a refusal of
        # that write is shown alongside the run's own error.
        problem = " ".join(part for part in (
            str(exc), review_views.write_scite(project, exc.slug, scite)) if part)
        return RedirectResponse(f"/runs/{quote(exc.slug)}?error={quote(problem)}",
                                status_code=303)
    except service.ServiceError as exc:
        return _render_start(request, project, error=str(exc), submitted=submitted)
    problem = review_views.write_scite(project, result["slug"], scite)
    if problem:
        return RedirectResponse(f"/runs/{quote(result['slug'])}?error={quote(problem)}",
                                status_code=303)
    return RedirectResponse(f"/runs/{quote(result['slug'])}", status_code=303)


@router.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request, slug: str = "", error: str = "", ok: str = "",
                assign: list[str] = Query(default=[])) -> HTMLResponse:
    """Plain `def`: staffing and review settings read the run's YAML from disk.

    The review editor's live preview is a POST (`preview_review_settings`), not
    a query on this page: a GET would carry the form's csrf_token in the URL
    (owner finding O2)."""
    project = _project(request)
    review = review_views.editor_context(project, slug) if slug else None
    return _agents_response(request, slug, error=error, ok=ok, assign=assign, review=review)


def _agents_response(request: Request, slug: str, *, error: str = "", ok: str = "",
                     assign=(), review=None) -> HTMLResponse:
    from scieflow.core import agent_config

    project = _project(request)
    preview_staffing, problem = None, error
    if assign:
        try:
            preview_staffing = service.plan_staffing(project, list(assign), slug or None)
        except service.ServiceError as exc:
            problem = str(exc)
    return TEMPLATES.TemplateResponse(request, "agents.html", {
        "slug": slug,
        "settings": service.agent_settings(project, slug or None),
        "roles": list(agent_config.ROLES),
        "runs": service.list_runs(project),
        "assign": list(assign),
        "preview": preview_staffing,
        "review": review,
        "error": problem,
        "ok": ok,
        "csrf": auth.csrf_token(request),
    })


def _run_dir_or_404(project, slug: str):
    """The run's workspace, or 404. The web's only derived path is `project.run_dir`."""
    try:
        ws = project.run_dir(slug)
    except ProjectError:
        raise HTTPException(status_code=404, detail="no such run") from None
    if not ws.is_dir():
        raise HTTPException(status_code=404, detail="no such run")
    return ws


@router.post("/runs/{slug}/review/preview", dependencies=MUTATE)
def preview_review_settings(request: Request, slug: str,
                            form: FormData = Depends(_conversation_form)) -> HTMLResponse:
    """The review editor's live preview: the typed values and the assignment
    they would produce, on the agents page. Writes nothing. The same mapper as
    Apply (`review_views.review_changes_from_form`) builds the preview."""
    project = _project(request)
    _run_dir_or_404(project, slug)
    review = review_views.editor_context(project, slug, submitted=form)
    return _agents_response(request, slug, review=review)


@router.post("/runs/{slug}/review/settings", dependencies=MUTATE)
def apply_review_settings(request: Request, slug: str,
                          form: FormData = Depends(_conversation_form)):
    """Apply the review editor's settings (scite toggle + reviewer pool) for a run.

    One mapper, `review_views.review_changes_from_form`, serves this and the
    live preview. Any refusal (core `ValueError` or a garbage field) goes back
    to the agents page as `?error=`; nothing is written for it."""
    project = _project(request)
    _run_dir_or_404(project, slug)
    target = "/agents?slug=" + quote(slug)
    ok, message = review_views.apply_changes(project, slug, form)
    key = "ok" if ok else "error"
    return RedirectResponse(f"{target}&{key}={quote(message)}", status_code=303)


@router.post("/agents", dependencies=MUTATE)
def apply_agents(request: Request, slug: str = Form(""),
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


@router.get("/runs/{slug}", response_class=HTMLResponse)
def run_page(request: Request, slug: str, error: str = "") -> HTMLResponse:
    from scieflow.web import presentation

    project = _project(request)
    detail = presentation.sanitized(service.run_detail(project, slug))
    ws = service.run_workspace(project, slug)
    for event in detail.get('events', []):
        event['data'] = presentation.text(event.get('data', {}))
    status = detail["status"] or {}
    try:
        state = service.conversation_state(project, slug)
        conversation_view = presentation.conversation_view(state)
        # The canonical decimal token both forms render, and the page's
        # integrity verdict for a malformed stored generation.
        revision_token = str(state['selection_generation'])
        generation_error = None
    except service.ServiceError as exc:
        conversation_view = {}
        revision_token = ''
        generation_error = str(exc)
    return TEMPLATES.TemplateResponse(request, "run.html", {
        "slug": slug,
        "detail": detail,
        "phases": list((status.get("phases") or {}).items()),
        "remaining": {dim: _percent(value)
                      for dim, value in (detail["remaining"] or {}).items()},
        "jobs": [presentation.sanitized(service.job_json(job)) for job in reversed(jobs_mod.list_jobs(project, ws))],
        "charter": presentation.sanitized(service.run_charter(project, slug)),
        "conversation": conversation_view,
        "revision_token": revision_token,
        "generation_error": generation_error,
        "conversation_raw": presentation.verified_raw_url(ws, slug, 'conversation.yml'),
        "agents": service.conversational_agents(project),
        "overview": presentation.sanitized(service.run_overview(project, slug)),
        "review": review_views.run_review_context(project, slug, ws),
        "error": presentation.text(error),
        "csrf": auth.csrf_token(request),
    })


def _owning_job(project, slug: str, job_id: str) -> jobs_mod.Job:
    """The job, if it belongs to this run. Raises `service.ServiceError`
    (-> 404) otherwise.

    One copy, because this is what stops a request reaching a job in a
    different run: two copies is how a later fix lands on one and not the
    other. `ServiceError` — rather than an `HTTPException` here — because
    the app's exception handler already maps it to the same 404 JSON body
    for any caller, GET or POST, matching `run_workspace`'s own use of
    `ServiceError` for "no such run" a line above every call site here.
    """
    ws = service.run_workspace(project, slug)            # validates the slug
    job = jobs_mod.find(project, job_id)
    if job is None or job.run_dir is None or Path(job.run_dir).resolve() != ws.resolve():
        raise service.ServiceError(f"no job {job_id} in {slug}")
    return job


def _back(slug: str, error: str = "") -> RedirectResponse:
    """Post/redirect/get: the browser lands on a fresh read of the page, so
    reloading never repeats the action."""
    target = f"/runs/{slug}"
    if error:
        target += "?error=" + quote(error)
    return RedirectResponse(target, status_code=303)


@router.post("/runs/{slug}/charter", dependencies=MUTATE)
def edit_charter(request: Request, slug: str, action: str = Form("set"),
                 text: str = Form(""), note: str = Form(""),
                 version: int = Form(0)):
    project = _project(request)
    try:
        if action == "revert":
            service.revert_charter(project, slug, version)
        else:
            service.set_charter(project, slug, text, note=note)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


def _drafts_back(slug: str, error: str = "") -> RedirectResponse:
    """Post/redirect/get for the workbench, mirroring `_back` above but
    landing on `/runs/{slug}/drafts` instead of the run page — the page a
    workbench mutation should redisplay is the workbench itself."""
    target = f"/runs/{slug}/drafts"
    if error:
        target += "?error=" + quote(error)
    return RedirectResponse(target, status_code=303)


def _as_int(value: str) -> int:
    """A form string as an `int`, as `service.ServiceError` rather than a
    bare `ValueError` reaching the user as a 500 — `position` and `version`
    both arrive as text from a hidden form field, and both can be anything
    a stale tab or a hand-crafted request cares to send."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise service.ServiceError(f"not a number: {value!r}") from exc


def _manuscript_diff(project, slug: str, a: str, b: str) -> dict | None:
    """The diff panel's data: `None` when there is nothing to diff yet (`a`
    or `b` empty — the common case, before the GET form has ever been
    submitted), otherwise `service.manuscript_diff`'s result, or `{"error":
    str(exc)}` when the ref pair is rejected.

    `ServiceError` is caught here, not left to `drafts_page` — a hostile or
    stale `diff_a`/`diff_b` (an old point from before a rebuild, or a
    `--flag`-shaped string trying to reach `git` as an argument) is an
    ordinary, expected outcome of a GET query parameter a user or a stale
    tab can send, not a reason to 404 or 500 the whole workbench page.
    """
    if not a or not b:
        return None
    try:
        return service.manuscript_diff(project, slug, a, b)
    except service.ServiceError as exc:
        return {"error": str(exc)}


@router.get("/runs/{slug}/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, slug: str, error: str = "",
                diff_a: str = "", diff_b: str = "") -> HTMLResponse:
    """`view` (from `service.workbench`) is spread last so its own `agents`
    key — the draft *authors* — wins over anything with the same name added
    here, and is what the drafts panel and its empty-state check read. The
    merging-agent selector needs a different list — agents that can actually
    hold a conversation, which need not have drafted anything at all — so
    that one is passed under its own name, `conversational`, rather than
    `agents`: a shared key here would let one of the two silently shadow the
    other depending on dict order, which is exactly the defect this
    docstring exists to keep from coming back.

    The same care applies to `provenance` (the manuscript's own git history,
    from `service.manuscript_history`) versus `history` (already taken —
    `service.curation_history`, the curation *document's* version list,
    which the existing version panel renders). The two are unrelated lists
    that happen to both describe "versions of something on this page", so
    they get two names, never one: a shared key here would let one silently
    shadow the other depending on dict order, exactly as `agents`/
    `conversational` above, and this is the specific collision a Task-5 fix
    round on the C plan was spent on.

    `diff_a`/`diff_b` are plain query parameters, not a new route — both the
    history and diff panels are read-only, driven by this same `GET`, so a
    point-selection control is a GET form and needs no CSRF field."""
    project = _project(request)
    try:
        view = service.workbench(project, slug)
    except service.ServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # One `service.preview_of` call per previewable source (every drafting
    # agent, plus every completed merge round) — not a second N+1 to add to
    # the dashboard's carried-forward one, just the one call this page
    # already needs per source it lists.
    sources = [f"agent:{a}" for a in view["agents"]] + [f"round:{n}" for n in view["rounds"]]
    previews = {source: service.preview_of(project, slug, source) for source in sources}
    return TEMPLATES.TemplateResponse(request, "drafts.html", {
        "slug": slug, "error": error,
        "conversation": service.conversation_state(project, slug),
        "conversational": service.conversational_agents(project),
        "history": service.curation_history(project, slug),
        "provenance": service.manuscript_history(project, slug),
        "diff": _manuscript_diff(project, slug, diff_a, diff_b),
        "diff_a": diff_a, "diff_b": diff_b,
        "previews": previews,
        "preview_busy": service.preview_busy(project, slug),
        "csrf": auth.csrf_token(request),
        **view})


@router.post("/runs/{slug}/drafts", dependencies=MUTATE)
def curate(request: Request, slug: str, action: str = Form(...),
           text: str = Form(""), agent: str = Form(""), section: str = Form(""),
           block: str = Form(""), position: str = Form("0"), note: str = Form(""),
           version: str = Form("0")):
    """Every workbench mutation, dispatched on `action` — the same shape
    `edit_charter` and `say` already use, which keeps the mutating-route
    inventory and its guard cases one entry wide.

    `def`, not `async def`: `merge_round` dispatches a whole agent turn and
    would otherwise block this app's single event loop for its entire
    `timeout_min`. `tests/web/test_async_routes.py` enforces it.
    """
    project = _project(request)
    try:
        if action == "keep":
            service.keep_passage(project, slug, text, agent, section)
        elif action == "mine":
            service.add_own_text(project, slug, text)
        elif action == "edit":
            service.edit_curation_block(project, slug, block, text)
        elif action == "move":
            service.move_curation_block(project, slug, block, _as_int(position))
        elif action == "remove":
            service.remove_curation_block(project, slug, block)
        elif action == "note":
            service.set_curation_note(project, slug, note)
        elif action == "revert":
            service.revert_curation(project, slug, _as_int(version))
        elif action == "merge":
            # A non-"done" turn is deliberately not raised by `merge_round`,
            # because it genuinely happened and cost budget -- so discarding
            # its return value made a merge that timed out, failed or was
            # cancelled look exactly like one that never happened: no message,
            # no new column, and the only way to find out was the run's own
            # job list.
            #
            # The verdict comes from the turn's own job state, which is the
            # same predicate `merge_round` gates its `advance_round` on. The
            # earlier shape -- read the round before the call, compare it
            # against the returned one -- needed two reads and had a window
            # between them: a merge completing in another tab moved the round,
            # and a turn that genuinely failed then read as success.
            if service.merge_round(project, slug)["turn"]["job"]["state"] \
                    != service.MERGE_SUCCESS_STATE:
                return _drafts_back(
                    slug, "the merge turn did not succeed, so this round has not "
                          "advanced — it still cost budget; see this run's job "
                          "list for what happened")
        else:
            return _drafts_back(slug, f"unknown action: {action}")
    except service.ServiceError as exc:
        return _drafts_back(slug, str(exc))
    return _drafts_back(slug)


@router.post("/runs/{slug}/preview", dependencies=MUTATE)
def compile_preview(request: Request, slug: str, source: str = Form(...)):
    """Compile one whole draft or completed round into a PDF.

    `def`, not `async def`: `service.compile_preview` -> `preview.run_compile`
    blocks on the whole `latexmk` run (seconds to minutes), and this app runs
    a single uvicorn process — an `async def` handler doing that would freeze
    every other request (`/healthz`, both SSE streams, the Cancel button) for
    the run's entire compile, exactly the defect `tests/web/test_async_routes.py`
    and this module's own docstring exist to keep out.

    A refusal (missing `latexmk`, a malformed `source`, no sections to
    compile, or another preview already running for this run) is a
    `ServiceError`, handled the same way every other workbench mutation
    handles one: post/redirect/get back to the drafts page with the message
    visible, nothing left half-written.
    """
    project = _project(request)
    try:
        service.compile_preview(project, slug, source)
    except service.ServiceError as exc:
        return _drafts_back(slug, str(exc))
    return _drafts_back(slug)


@router.post("/runs/{slug}/say", dependencies=MUTATE)
def say(request: Request, slug: str, action: str = Form("say"),
        message: str = Form(""), agent: str = Form(""),
        form: FormData = Depends(_conversation_form)):
    """Plain `def`, not `async def` — see `scieflow.web.api.say` for why:
    `service.say` blocks for up to the agent's `timeout_min`, and an `async
    def` handler doing that would stall the one event loop this app runs on
    for the whole turn. The raw form multi-dict is read by the async
    `_conversation_form` dependency (resolved on the loop before this
    handler runs in the threadpool); every field check below is pure CPU.

    Task7 variants: the send branch carries optional `mode` and
    `selection_revision` form fields; the hand-over branch carries optional
    `expected_revision`, `reset_session` and `confirm_new_session`. All
    revision fields are canonical decimal strings; malformed, stale or
    missing-when-required values reject with a redirect-and-error, leaving
    state byte-identical. The HumanSubmission is constructed here, inside
    the authenticated controller — a client-supplied actor is never trusted.
    """
    project = _project(request)

    try:
        if action == "agent":
            reset = _reset_fields_from_form(form)
            expected = _revision_field(form, 'expected_revision')
            if reset and expected is None:
                # An explicit reset must pin the state it resets; a
                # revision-less reset could race a concurrent selection.
                raise _FormError('resetting the session requires the current '
                                 'revision (expected_revision)')
            service.set_conversation_agent(
                project, slug, agent, expected_revision=expected,
                reset_session=reset)
        else:
            submission = _submission_from_form(form)
            if submission.mode == 'harness-command':
                # Explicit intent, but the shipped policy tuple is empty:
                # run it as ordinary chat and say the capability is not
                # certified. Never a silent enablement.
                service.say(project, slug, submission.message, "human",
                           human_submission=_as_chat(submission))
                return _back(slug, 'harness-command capability is not certified '
                                   'on this deployment; your message was sent as '
                                   'ordinary chat')
            service.say(project, slug, submission.message, "human",
                       human_submission=submission)
            return _back(slug)
    except _FormError as exc:
        return _back(slug, str(exc))
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/gates/{gate_id}", dependencies=MUTATE)
def answer_gate(request: Request, slug: str, gate_id: str,
                answer: str = Form(...), note: str = Form(""),
                proposal_digest: str = Form("")):
    try:
        service.answer_gate(_project(request), slug, gate_id, answer, note=note,
                            proposal_digest=proposal_digest or None)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/act", dependencies=MUTATE)
def act(request: Request, slug: str, action: str = Form(...),
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
            return _back(slug, f"unknown action {action!r}")
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/jobs/{job_id}/cancel", dependencies=MUTATE)
def cancel_job(request: Request, slug: str, job_id: str):
    """Plain `def`, not `async def` — see `say` above: `service.cancel_job`
    -> `jobs.cancel` -> `_kill_group` polls with `time.sleep(0.1)` for up to
    `KILL_GRACE` (10s) against a process that ignores `SIGTERM`, blocking
    the one event loop for the whole grace period if run here directly."""
    project = _project(request)
    _owning_job(project, slug, job_id)
    try:
        service.cancel_job(project, job_id)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.get("/runs/{slug}/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, slug: str, job_id: str) -> HTMLResponse:
    from scieflow.core.turn_output import display_panel
    from scieflow.web import presentation

    project = _project(request)
    ws = service.run_workspace(project, slug)
    try:
        with jobs_mod.locate_artifacts(project, job_id, run_dir=ws) as location:
            job = location.read_job()
            panels = {}
            for stream in ('stdout', 'stderr'):
                try:
                    with location.open_log(stream) as source:
                        panels[stream] = display_panel(source).text
                except jobs_mod.JobArtifactError:
                    panels[stream] = '[Display unavailable: source invalidated; raw artifact retained]'
            # argv is already materialized by record loading. Do NOT sanitize
            # every argument before the lazy capped aggregate consumes it.
            view = presentation.sanitized({key: value for key, value in service.job_json(job).items() if key != 'argv'})
            view['argv_display'] = presentation.argv_panel(job.argv)
    except jobs_mod.JobArtifactError as exc:
        raise HTTPException(status_code=404, detail='job unavailable') from exc
    audit = (job.turn_audit or {})
    turn_mode = audit.get('input_kind') if isinstance(audit, dict) else None
    if turn_mode not in ('chat', 'harness-command'):
        turn_mode = 'chat' if job.kind != 'turn' else 'unsupported'
    return TEMPLATES.TemplateResponse(request, "job.html", {
        "slug": slug,
        "job": view,
        "out": panels['stdout'],
        "err": panels['stderr'],
        "out_raw": presentation.verified_raw_url(ws, slug, location.relative_log),
        "err_raw": presentation.verified_raw_url(ws, slug, location.relative_err),
        "job_raw": presentation.verified_raw_url(ws, slug, f'jobs/{job_id}.json'),
        "live": job.state == "running",
        "turn_mode": presentation.text(turn_mode),
    })


@router.get("/runs/{slug}/files", response_class=HTMLResponse)
def files_page(request: Request, slug: str, path: str = "") -> HTMLResponse:
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
def file_view(request: Request, slug: str, path: str, download: bool = False):
    from scieflow.core.turn_artifacts import ArtifactError
    from scieflow.core.turn_output import display_panel
    from scieflow.web import files as files_mod, presentation
    from scieflow.web.artifact_response import ArtifactResponse

    ws = service.run_workspace(_project(request), slug)
    try:
        handle, owner = files_mod.acquire_owned(ws, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='path escapes the run or is unsafe') from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='no such file') from exc
    try:
        target = Path(path)  # suffix/name classification ONLY; no filesystem I/O
        if not download and files_mod.is_text(target) and handle.snapshot_size <= files_mod.MAX_INLINE:
            panel = display_panel(handle)
            handle.verify_unchanged()
            return TEMPLATES.TemplateResponse(request, 'file.html', {
                'slug': slug, 'path': presentation.text(path), 'text': panel.text,
                'raw': presentation.raw_url(slug, path)})
        media_type, attachment = files_mod.download_type(target)
        if download:
            media_type, attachment = 'application/octet-stream', True
        response = ArtifactResponse(handle, owner, media_type, target.name if attachment else None)
        owner = None  # Successful response construction transfers the active stack.
        return response
    except ArtifactError as exc:
        raise HTTPException(status_code=400, detail='artifact unavailable') from exc
    finally:
        if owner is not None:
            owner.close()
