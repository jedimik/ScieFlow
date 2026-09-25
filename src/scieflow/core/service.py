"""The service layer: every ScieFlow action exists once, here.

The CLI, the TUI menu, the agent skill JSON and (M2) the HTTP API are thin
callers. Everything returns plain JSON-ready data and raises `ServiceError`
for anything a caller should show the user.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from scieflow.core import (
    agent_config,
    agent_configure as acf,
    agent_run,
    config,
    events,
    gates,
    jobs,
    sandbox,
    sessions,
    store,
    workspace,
)
from scieflow.core.gates import ADOPTED, CHARTER_ADOPTION
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import actions, budget, charter, conversation, status

RECENT_JOBS = 20
RECENT_EVENTS = 50


class ServiceError(Exception):
    """A request the service cannot fulfil, with a message for the user."""


class RunStartedError(ServiceError):
    """`start_run` failed *after* the run was already created.

    Everything that can fail once `create_run` has returned — handing the
    conversation to `agent` and taking its first turn — leaves a real run
    behind: the workspace exists and the conversation agent is recorded, even
    though the turn itself did not complete (a sandbox refusal, an exhausted
    budget, an oversized opening prompt). A caller that only catches
    `ServiceError` and re-renders an empty form would tell the user nothing
    was made, when in fact resubmitting will now say "a run named X already
    exists" with no way back to it — so this carries `slug` (the canonical
    name the run was actually created under) for a caller to send the user
    to the run's own page instead.
    """

    def __init__(self, slug: str, message: str):
        super().__init__(message)
        self.slug = slug


PROPOSAL_PREVIEW_LIMIT = 4000        # characters of a proposal shown in a gate form


def _ws(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as e:
        raise ServiceError(str(e)) from e
    if not ws.is_dir():
        raise ServiceError(f"no run workspace/{slug}")
    return ws


def run_workspace(project: Project, slug: str) -> Path:
    """The run's directory, or ServiceError — the public form of `_ws`."""
    return _ws(project, slug)


def job_json(job: jobs.Job) -> dict:
    """`asdict(job)` plus `duration_s`, a property `asdict` drops (not a field)."""
    return {**asdict(job), "duration_s": job.duration_s}


def list_runs(project: Project) -> list[dict]:
    return [asdict(r) for r in workspace.list_runs(project.root)]


def run_detail(project: Project, slug: str) -> dict:
    ws = _ws(project, slug)
    st = status.read_status(ws) if (ws / "status.yml").exists() else None
    b = budget.read_budget(ws) if (ws / "budget.yml").exists() else None
    return {
        "run": asdict(workspace.describe(ws)),
        "status": st,
        "budget": b,
        "remaining": budget.remaining_fraction(b) if b else None,
        "gates": _with_proposal_preview(ws, gates.list_gates(ws, "open")),
        "jobs": [job_json(j) for j in jobs.list_jobs(project, ws)][-RECENT_JOBS:][::-1],
        "events": events.read(ws)[-RECENT_EVENTS:],
    }


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
                 "max_wall_minutes": max_wall_minutes,
                 "workflow": workflow or None}
    # Both the temp dir and the write into it are inside this try/finally too
    # — a failure here (disk full, no permission on the temp dir) must be
    # translated to ServiceError and must not leak the temp directory, the
    # same as a failure inside init_workspace itself.
    tmp_dir: Path | None = None
    try:
        tmp_dir = Path(tempfile.mkdtemp())
        goal_file = tmp_dir / "goal.md"
        goal_file.write_text(goal, encoding="utf-8")
        init_mod.init_workspace(target.name, goal_file, project.workspace_root,
                                overrides, project.root)
        return run_detail(project, target.name)
    except FileExistsError as exc:
        raise ServiceError(f"a run named {target.name} already exists") from exc
    except (OSError, ValueError, KeyError) as exc:
        # No cleanup of `target` here: `init_workspace` already removes it on
        # any exception it raises, and every failure that can reach this
        # branch *before* `init_workspace` runs (the temp dir, the goal-file
        # write) happens while `target` does not exist yet — so a
        # `shutil.rmtree(target, ...)` here would have nothing of this call's
        # own to remove. It could only ever delete a directory this call did
        # not create.
        raise ServiceError(f"could not create {target.name}: {exc}") from exc
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def run_events(project: Project, slug: str, since: str | None = None,
               types: tuple[str, ...] = ()) -> list[dict]:
    return events.read(_ws(project, slug), since=since, types=types)


def dispatch_agent(project: Project, agent: str, prompt_file: Path, transcript: Path, *,
                   cwd: Path | None = None, role: str | None = None,
                   detach: bool = False, session: str | None = None,
                   conversational: bool = False) -> dict:
    try:
        d = agent_run.prepare(project, agent, Path(prompt_file), cwd, role,
                              session=session, conversational=conversational)
    except (agent_run.DispatchError, sandbox.SandboxError) as e:
        raise ServiceError(str(e)) from e
    if d.run_dir is not None:
        try:
            actions.guard_budget(d.run_dir, ("wall_minutes",))
        except actions.BudgetExhausted as e:
            raise ServiceError(f"{e} — run checkpointed") from e
    if d.writable is None:
        if d.run_dir is not None:
            events.emit(d.run_dir, "sandbox.disabled", "human", agent=d.agent,
                        why=f"{sandbox.ALLOWLIST_FILE} unsandboxed_runs")
    else:
        try:
            sandbox.verify(d.writable, d.cwd)
        except sandbox.SandboxError as exc:
            if d.run_dir is not None:
                events.emit(d.run_dir, "job.refused", "system", reason="sandbox",
                            detail=str(exc))
            raise ServiceError(str(exc)) from exc
    # A conversational dispatch is tagged "turn", not "agent" — this is what
    # lets `_turn_in_flight` (below) tell a chat turn apart from every other
    # agent-kind job the run starts (a campaign, a review), which must never
    # make the chat box look busy.
    job, proc = jobs.start(project, d.argv, kind="turn" if conversational else "agent",
                           cwd=d.cwd, run_dir=d.run_dir,
                           label=agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text,
                           sandbox_writable=d.writable, env=d.env)

    def finish() -> jobs.Job:
        done = jobs.wait(job, proc)
        if d.run_dir is not None and done.duration_s is not None:
            actions.record_spend(d.run_dir, wall_minutes=round(done.duration_s / 60, 3))
        out = Path(done.log).read_text()
        note = f"\n{agent}: timed out after {d.timeout_s:.0f}s\n" if done.state == "timeout" else ""
        Path(transcript).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript).write_text(out + note)
        return done

    if detach:
        threading.Thread(target=finish, daemon=True).start()
        return asdict(job)
    return asdict(finish())


def cancel_job(project: Project, job_id: str) -> dict:
    job = jobs.find(project, job_id)
    if job is None:
        raise ServiceError(f"no job {job_id}")
    return asdict(jobs.cancel(job))


TURN_PROMPT_DIR = "logs"


def _turn_in_flight(project: Project, ws: Path) -> bool:
    """True when a turn's own job — not just any agent job — is still
    running for this run.

    `dispatch_agent` tags a conversational dispatch's job `kind="turn"`
    (every other dispatch, including a coordinator's own campaign and review
    work, stays `kind="agent"`), so this matches only that. Before this
    distinction existed, matching `kind == "agent"` made the chat look busy
    for nearly all of an autonomous run's life — every dispatch the
    coordinator itself makes is `kind="agent"` too.

    This is still a plain read of the job records, not a lock: two `say()`
    calls that both read "not busy" before either one's job is recorded can
    still both proceed. It narrows that window to the time between reading
    the conversation record and starting the job, but does not close it — a
    real fix would need a lock held across that whole span (e.g. the
    run-scoped file lock `store.locked` already uses elsewhere), which this
    task does not add.
    """
    return any(job.state == "running" and job.kind == "turn"
              for job in jobs.list_jobs(project, ws))


def conversation_state(project: Project, slug: str) -> dict:
    """The record, plus whether a turn is in flight, whether this agent can
    hold a session at all, and whether it has quietly lost the one it had.

    Named `conversation_state`, not `conversation` — `conversation` is
    already the name of the imported module, and a same-named function here
    would shadow it.
    """
    ws = _ws(project, slug)
    try:
        doc = conversation.read(ws)
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc
    cfg = config.load_agents(project.root).get(doc["agent"], {})
    can_converse = bool(doc["agent"]) and sessions.can_converse(cfg)
    # `record_session(ws, None)` leaves a prior id alone by design, but if
    # there was never one at all — the CLI's configuration promises a
    # session but its runtime output never carried one — every turn after
    # the first re-picks `session_cmd` and the conversation silently starts
    # fresh each time. This is the one state field that says so.
    session_lost = bool(doc["turns"]) and can_converse and not doc["session"]
    return {**doc,
            "busy": _turn_in_flight(project, ws),
            "can_converse": can_converse,
            "session_lost": session_lost}


def say(project: Project, slug: str, message: str, actor: str = "human") -> dict:
    """One conversation turn: a sandboxed job that resumes the agent's session.

    This is an ordinary dispatch — `dispatch_agent` guards the budget, proves
    the sandbox, starts the job, records spend and writes the transcript, and
    (via `agent_run.prepare`) composes the prompt through `compose_prompt`, so
    the run's charter is pinned to this turn exactly as it is to any other
    dispatch. `say` writes the raw message here, not a composed one: `prepare`
    is the one place every dispatch is composed, and composing twice would
    carry two copies of the charter into a single turn.

    Preconditions cheap and free of side effects — the agent is known, it can
    actually hold a session, and the run's wall-minutes budget is not already
    spent — are checked *before* the human turn is written, so a refusal here
    leaves no orphan turn, no orphan `turn.sent` event and no orphan prompt
    file for a message nothing was ever attempted for. A failure *during* the
    dispatch itself (sandbox verify, a crash, a timeout) is different: the
    human turn is already on record by then, and a turn that genuinely
    started and died should still show what was asked.
    """
    if not message or not message.strip():
        raise ServiceError("say something")
    ws = _ws(project, slug)
    try:
        doc = conversation.read(ws)
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc
    if not doc["agent"]:
        raise ServiceError("choose an agent for this run's conversation first")
    if _turn_in_flight(project, ws):
        raise ServiceError("a turn is still running; wait for it or cancel it")

    agents = config.load_agents(project.root)
    agent_cfg = agents.get(doc["agent"])
    if agent_cfg is None:
        raise ServiceError(f"unknown agent: {doc['agent']} (known: {', '.join(agents)})")
    if not sessions.can_converse(agent_cfg):
        raise ServiceError(
            f"{doc['agent']} cannot host a conversation: its configuration has no "
            "session commands (or no registered family), so every turn would start over")
    try:
        actions.guard_budget(ws, ("wall_minutes",))
    except actions.BudgetExhausted as exc:
        raise ServiceError(f"{exc} — run checkpointed") from exc

    prompt_file = Path(ws) / TURN_PROMPT_DIR / f"turn-{store.new_id()}.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(message)

    try:
        conversation.add_turn(ws, role="human", text=message, actor=actor)
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc

    transcript = prompt_file.with_suffix(".out.md")
    job = dispatch_agent(project, doc["agent"], prompt_file, transcript,
                         session=doc["session"], conversational=True)

    parsed = sessions.parse(agent_cfg, Path(transcript).read_text())
    try:
        after_session = conversation.record_session(ws, parsed.id)
        turn = conversation.add_turn(ws, role="agent", text=parsed.text,
                                     job_id=job["id"], actor="agent", state=job["state"])
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc
    if not after_session["session"]:
        # Not just "this turn reported no id" — `record_session(ws, None)`
        # leaves a prior id alone, so this only fires when there was never
        # one at all: the exact silent-restart failure `sessions.py` exists
        # to prevent, now visible on the run's own timeline.
        events.emit(ws, "turn.session_lost", "system", agent=doc["agent"], job=job["id"])
    if parsed.text.strip():
        Path(transcript).write_text(parsed.text)  # the job page shows prose, not JSON
    return {"job": job, "turn": turn}


def open_gates(project: Project, slug: str | None = None) -> list[dict]:
    slugs = [slug] if slug else [r["slug"] for r in list_runs(project)]
    out = []
    for name in slugs:
        ws = _ws(project, name)
        for g in _with_proposal_preview(ws, gates.list_gates(ws, "open")):
            out.append({**g, "slug": name})
    return out


def _resolve_in_run(ws: Path, raw: str) -> Path:
    """`raw` resolved to an absolute path, refusing anything outside `ws`.

    Mirrors the containment check `scieflow.web.files.resolve` applies to a
    browser-requested artifact path — resolve first (normalising `..` and
    following symlinks), then check containment on the *resolved* path
    against the *resolved* run root, never on the raw string — rather than
    importing that web module here, which would be the wrong direction for
    the service layer to depend on. `raw` may already be absolute, as every
    `files` entry `open_gate` stores is: joining an absolute path onto `ws`
    with `Path.__truediv__` discards `ws` and returns the absolute path
    unchanged, so the same containment check still catches an absolute path
    that points outside the run.
    """
    root = Path(ws).resolve()
    try:
        candidate = (root / raw).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"path escapes the run: {raw!r}") from exc
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"path escapes the run: {raw!r}")
    return candidate


def _proposal_path(ws: Path, gate: dict) -> Path:
    """The file a `charter-adoption` gate names, confined to this run.

    `files` is written by whichever agent opened the gate, and the text at
    this path is about to become the charter pinned to every later prompt on
    the run — so a path outside the run is refused rather than read.
    """
    files = gate.get("files") or []
    if not files:
        raise ServiceError("that proposal names no file to adopt")
    if len(files) > 1:
        raise ServiceError(
            "that proposal names more than one file; a charter adoption needs exactly one")
    raw = str(files[0])
    try:
        return _resolve_in_run(ws, raw)
    except ValueError:
        raise ServiceError(f"that proposal's path escapes the run: {raw!r}") from None


def _read_proposal(ws: Path, gate: dict) -> str:
    """The proposal text for a `charter-adoption` gate, resolved and
    validated *before* the gate is answered.

    Order matters. `gates.answer` immediately records `state: "answered"`,
    and refuses to touch a gate that is not `open` — so once the gate is
    answered there is no re-answering it. If a bad proposal (missing,
    unreadable, escaping the run, or empty) were discovered only after
    `gates.answer` ran, the gate would be left permanently asserting an
    adoption that never happened, with no way to fix the file and retry.
    Calling this first, and raising before `gates.answer` is ever called,
    means nothing is recorded until there is a charter worth recording.
    """
    path = _proposal_path(ws, gate)
    try:
        text = path.read_text()
    except OSError as exc:
        raise ServiceError(f"cannot read the proposal at {path}: {exc}") from exc
    if not text.strip():
        raise ServiceError(f"the proposal at {path} is empty")
    return text


def _proposal_preview(ws: Path, gate: dict) -> str | None:
    """A short, safe preview of a `charter-adoption` gate's proposal, so a
    human can see what they are being asked to adopt. Never raises: listing
    an open gate must not break just because its proposal has since gone
    missing, escaped the run, or become unreadable — answering the gate is
    what catches that, not viewing it.
    """
    try:
        text = _proposal_path(ws, gate).read_text()
    except (ServiceError, OSError):
        return None
    if len(text) > PROPOSAL_PREVIEW_LIMIT:
        return text[:PROPOSAL_PREVIEW_LIMIT] + "\n… (truncated)"
    return text


def _proposal_digest(ws: Path, gate: dict) -> str | None:
    """sha256 of the *whole* proposal file — never the truncated preview, or
    a proposal longer than `PROPOSAL_PREVIEW_LIMIT` could never match. This
    is what a gate form carries back so `answer_gate` can tell the file
    changed since it was previewed. Never raises, for the same reason
    `_proposal_preview` doesn't: an unreadable proposal is caught when the
    gate is answered, not when the page merely lists it.
    """
    try:
        text = _proposal_path(ws, gate).read_text()
    except (ServiceError, OSError):
        return None
    return hashlib.sha256(text.encode()).hexdigest()


def _with_proposal_preview(ws: Path, gate_list: list[dict]) -> list[dict]:
    return [{**g, "proposal_text": _proposal_preview(ws, g),
            "proposal_digest": _proposal_digest(ws, g)} if g["kind"] == CHARTER_ADOPTION
           else g for g in gate_list]


def answer_gate(project: Project, slug: str, gate_id: str, answer: str,
                actor: str = "human", rationale: str = "", note: str = "",
                proposal_digest: str | None = None) -> dict:
    """Answer a gate. `proposal_digest`, when given, must match a fresh
    sha256 of the proposal file — carried by the gate form as a hidden
    field from whatever was rendered for the human to read — or the answer
    is refused. Without it (the CLI has no preview step to digest) the check
    is simply skipped, same as before this existed.
    """
    ws = _ws(project, slug)
    try:
        gate_before = gates.get(ws, gate_id)
    except gates.GateError as e:
        raise ServiceError(str(e)) from e
    if gate_before["state"] != "open":
        raise ServiceError(f"gate {gate_id} is not open ({gate_before['state']})")
    proposal_text = None
    if gate_before["kind"] == CHARTER_ADOPTION and answer.strip().lower() in ADOPTED:
        proposal_text = _read_proposal(ws, gate_before)      # before the gate is answered
        if proposal_digest and hashlib.sha256(proposal_text.encode()).hexdigest() != proposal_digest:
            raise ServiceError("the proposal changed since you read it — reload and check "
                               "it again before adopting")
    try:
        gate = gates.answer(project, ws, gate_id, answer, actor, rationale, note)
    except gates.GateError as e:
        raise ServiceError(str(e)) from e
    if proposal_text is not None:
        try:
            charter.set_text(ws, proposal_text, actor,
                             f"adopted from a proposal (gate {gate['id']})")
        except charter.CharterError as exc:
            raise ServiceError(str(exc)) from exc
    return gate


def agent_settings(project: Project, slug: str | None = None) -> dict:
    return agent_config.resolve(project.root, slug).to_json()


def conversational_agents(project: Project) -> list[str]:
    """Registry entries that could actually hold a run's conversation:
    enabled, and configured to both start and resume a session.

    This is what the run page's hand-over picker offers. The full registry
    (`config/agents.yml`) also carries support agents with no `session_cmd`
    at all and agents marked `enabled: false` — offering those as a
    conversation target would let someone hand a live conversation to an
    agent `set_conversation_agent` refuses the very next moment.
    """
    agents = config.load_agents(project.root)
    return sorted(name for name, cfg in agents.items()
                  if cfg.get("enabled", True) and sessions.can_converse(cfg))


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


def run_charter(project: Project, slug: str) -> dict:
    """The run's agreed plan: current text plus the whole version history.

    `charter.snapshot` reads `charter.yml` once, so a concurrent write
    cannot pair one read's `current` with a different read's version text.
    """
    ws = _ws(project, slug)
    try:
        return charter.snapshot(ws)
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc


def set_charter(project: Project, slug: str, text: str,
                actor: str = "human", note: str = "") -> dict:
    ws = _ws(project, slug)
    try:
        return charter.set_text(ws, text, actor, note)
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc


def revert_charter(project: Project, slug: str, version: int,
                   actor: str = "human") -> dict:
    ws = _ws(project, slug)
    try:
        return charter.revert(ws, version, actor)
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc


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


def _check_can_converse(project: Project, agent: str) -> None:
    """The per-reason check for whether `agent` could hold a conversation at
    all: known, enabled, and configured with both session commands and a
    registered family — each reason with its own message naming the actual
    problem.

    Shared by `set_conversation_agent` (an existing run picking its agent)
    and `start_run` (checked *before* the run exists at all, so a refusal
    here never leaves a half-started run behind).
    """
    agents = config.load_agents(project.root)
    if agent not in agents:
        raise ServiceError(f"unknown agent: {agent} (known: {', '.join(agents)})")
    agent_cfg = agents[agent]
    if not agent_cfg.get("enabled", True):
        raise ServiceError(f"{agent} is disabled (config/agents.yml); enable it first")
    if not agent_cfg.get("session_cmd"):
        raise ServiceError(
            f"{agent} cannot host a conversation: no session_cmd in its configuration")
    if not agent_cfg.get("resume_cmd"):
        raise ServiceError(
            f"{agent} cannot host a conversation: no resume_cmd in its configuration")
    if not sessions.can_converse(agent_cfg):
        # The only remaining reason can_converse can fail (given both commands exist)
        # is an invalid family
        raise ServiceError(
            f"{agent} cannot host a conversation: no family in its configuration "
            "or the family is not registered")


def set_conversation_agent(project: Project, slug: str, agent: str,
                           actor: str = "human") -> dict:
    """Choose who holds this run's conversation.

    Switching discards the session id, because one CLI's session means
    nothing to another — the next turn starts a new one. The turns already
    said are history and are kept.
    """
    ws = _ws(project, slug)
    _check_can_converse(project, agent)
    if _turn_in_flight(project, ws):
        raise ServiceError("a turn is still running; wait for it or cancel it")
    try:
        return conversation.set_agent(ws, agent, actor)
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc


def _opening_prompt(slug: str, goal: str, workflow: str) -> str:
    """The first message handed to a coordinator when a run is started with
    an agent already chosen — following the same convention
    `menu.resume_prompt` uses to hand a run to a coordinator, so the TUI and
    the browser tell a coordinator the same things about a run.

    No workflow named is not a second convention: it falls back exactly the
    way `resume_prompt` falls back for a `kind` with no entry in
    `menu.WORKFLOWS` — a skill reference is still named, just the generic
    `src/scieflow/research/AGENTS.md` rather than a workflow-specific skill.

    The goal is pinned in verbatim, at the end, as data — never templated or
    interpreted.
    """
    from scieflow.core import menu

    skill = menu.WORKFLOWS.get(workflow, {}).get("skill", "src/scieflow/research/AGENTS.md")
    return (f"Read AGENTS.md and src/scieflow/research/AGENTS.md. Start the run "
            f"workspace/{slug}: read its goal.md and status.yml and continue per "
            f"{skill}.\n\nGoal: {goal}")


def start_run(project: Project, slug: str, goal: str, agent: str = "", *,
              workflow: str = "", **limits) -> dict:
    """Create a run and, when an agent is named, have it take the first turn.

    The agent is checked *before* the run is created: a half-started run with
    no way to talk to it is worse than a refusal. Creating and launching stay
    separate (`create_run`, `set_conversation_agent`, `say`) — this is just
    the convenience that chains them, so a run can also be made with no agent
    at all when the registry has nothing conversational, leaving a run
    someone can pick an agent for later on its own page.

    Everything after creation uses `run["run"]["slug"]` — the canonical name
    `create_run` actually made the workspace under — not the raw `slug` this
    call was given. `Project.run_dir` legitimately rewrites a slug (a
    `workspace/` prefix stripped, a trailing slash or surrounding whitespace
    trimmed), so the two can differ, and a caller that instead reused the raw
    slug would tell the conversation agent, and eventually the browser's own
    redirect, to look for a run at a path that 404s while the real one sits
    one path segment over.

    A failure once the run exists (`set_conversation_agent` or `say` refusing
    — a sandbox verify failure, an exhausted budget, an oversized opening
    prompt) raises `RunStartedError`, not a plain `ServiceError`: the run is
    real, and the caller should send the user to it, not pretend nothing
    happened.
    """
    if agent:
        _check_can_converse(project, agent)
    run = create_run(project, slug, goal, workflow=workflow, **limits)
    canonical = run["run"]["slug"]
    if not agent:
        return {"run": run, "slug": canonical, "turn": None}
    try:
        set_conversation_agent(project, canonical, agent)
        said = say(project, canonical, _opening_prompt(canonical, goal, workflow))
    except ServiceError as exc:
        raise RunStartedError(canonical, str(exc)) from exc
    return {"run": run, "slug": canonical, "turn": said["turn"]}
