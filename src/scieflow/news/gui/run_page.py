from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from nicegui import ui

from .. import agents
from ..config import (
    VALID_AGENTS,
    VALID_REASONING,
    ConfigError,
    all_group_names,
    load_config,
    resolve_selection,
)
from ..runner import Progress, run_queue
from .context import GuiContext

DEFAULT_OPTION = "(config default)"


def reasoning_enabled(agent: str) -> bool:
    """agy folds reasoning into its model names, so the reasoning control is meaningless for it."""
    return agent != "agy"


@dataclass
class RunState:
    events: list[Progress] = field(default_factory=list)
    running: bool = False
    run_id: int | None = None
    error: str | None = None
    interests: list[str] = field(default_factory=list)


def execute_run(
    ctx: GuiContext,
    agent: str,
    state: RunState,
    model: str | None = None,
    reasoning: str | None = None,
    only: list[str] | None = None,
    groups: list[str] | None = None,
) -> None:
    """Run the research queue. Blocking — call from a worker thread."""
    try:
        config = load_config(ctx.config_path)
        db = ctx.open_db()
        state.run_id = run_queue(
            config,
            db,
            agent=agent,
            only=only,
            groups=groups,
            model=model,
            reasoning=reasoning,
            progress=state.events.append,
        )
    except Exception as e:  # any failure must reach the UI, not a dead thread
        state.error = str(e)
    finally:
        state.running = False


# Module-level so a live run survives page navigation/reload and a second
# client can't start a concurrent duplicate run.
_STATE = RunState()
_START_LOCK = threading.Lock()


def try_start(state: RunState, lock: threading.Lock) -> bool:
    """Atomically claim the right to start a run.

    Returns False if a run is already in progress; otherwise resets the
    state's fields and marks it running (returning True) before the lock
    is released, so a concurrent caller can never slip in between the
    check and the flag being set.
    """
    with lock:
        if state.running:
            return False
        state.events.clear()
        state.error = None
        state.run_id = None
        state.running = True
        return True


def start_run(
    ctx: GuiContext,
    state: RunState,
    *,
    agent: str,
    model: str | None,
    reasoning: str | None,
    interests_sel: list[str],
    groups_sel: list[str],
) -> str | None:
    """Resolve the selection, claim the run, and spawn the worker thread.

    Returns an error message on failure (bad config, unknown group/interest
    names, or a run already in progress) or None on success. `state.interests`
    is set to the resolved names before the thread starts, since
    `visible_rows` renders the pending queue from it immediately.
    """
    try:
        config = load_config(ctx.config_path)
        names = resolve_selection(config, interests_sel or None, groups_sel or None)
    except (ConfigError, ValueError) as e:
        return str(e)
    if not try_start(state, _START_LOCK):
        return "a run is already in progress"
    state.interests = names if names is not None else [i.name for i in config.interests]
    threading.Thread(
        target=execute_run,
        args=(ctx, agent, state),
        kwargs={
            "model": model,
            "reasoning": reasoning,
            "only": interests_sel or None,
            "groups": groups_sel or None,
        },
        daemon=True,
    ).start()
    return None


def queue_rows(
    interest_names: list[str], events: list[Progress]
) -> list[tuple[str, str, str]]:
    """(interest, status, detail) per configured interest; latest event wins, else 'pending'."""
    latest: dict[str, Progress] = {p.interest: p for p in events}
    rows = []
    for name in interest_names:
        p = latest.get(name)
        rows.append((name, p.status if p else "pending", p.detail if p else ""))
    return rows


def visible_rows(state: RunState) -> list[tuple[str, str, str]]:
    """Rows refresh() should render.

    Once a run has started (running=True) — even before the first Progress
    event arrives from the worker thread — the full pending queue must show,
    not a blank/empty state. Once the run finishes, rows stay visible via
    state.events until the next run clears them.
    """
    if state.events or state.running:
        return queue_rows(state.interests, state.events)
    return []


def _model_options(db, agent: str) -> list[str]:
    cached = db.get_models(agent)
    models = cached["models"] if cached else []
    return [DEFAULT_OPTION, *models]


# Serializes model-refresh runs so two overlapping clicks (or a slow refresh
# plus a fast one) can't race each other's cache writes / UI updates.
_REFRESH_LOCK = threading.Lock()


def _refresh_models(
    ctx: GuiContext,
    agent: str,
    current_agent: Callable[[], str],
    apply_options: Callable[[list[str]], None],
    notify: Callable[[str, str], None],
) -> None:
    """Discover and cache models for `agent`; runs in a background thread.

    Opens its own `Database` (rather than sharing the page's) since this
    runs on a worker thread — same convention as `execute_run`.

    Two guards, factored out as plain arguments so they're unit-testable
    without a browser:

    - Concurrency: a non-blocking acquire of `_REFRESH_LOCK`. If another
      refresh is already in flight, this call returns immediately without
      touching discovery/cache/UI at all — the click handler is expected to
      have already warned the user before spawning this thread, so no
      cross-thread notify is needed here.
    - Staleness: `current_agent()` is called *after* discovery completes
      (which may take a while) and compared against `agent`. If the user
      switched the agent select while this refresh was running, the
      discovered models are still cached via `db.set_models` (the cache is
      agent-keyed, so the write is always valid) but `apply_options` is
      skipped so the now-stale list never overwrites the select showing the
      newly-chosen agent's models.
    """
    if not _REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        try:
            models = agents.discover_models(agent)
            ctx.open_db().set_models(agent, models)
        except Exception as e:
            notify(f"model discovery failed: {e}", "negative")
            return
        if current_agent() == agent:
            apply_options(models)
            notify(f"refreshed models for {agent!r}", "positive")
    finally:
        _REFRESH_LOCK.release()


def build(ctx: GuiContext) -> None:
    ui.label("Run").classes("text-2xl font-bold")
    if not ctx.config_path.exists():
        with ui.card().classes(
            "w-full rounded-2xl border border-gray-700/40 shadow-sm"
            " items-center text-center gap-2 p-6"
        ):
            ui.icon("settings").classes("text-4xl text-gray-500")
            ui.label("No config yet — create one on the Interests page.").classes(
                "text-gray-400"
            )
            ui.button(
                "Go to Interests", on_click=lambda: ui.navigate.to("/")
            ).props("flat")
        return
    try:
        config = load_config(ctx.config_path)
    except ConfigError as e:
        ui.label(f"Config error: {e}").classes("text-red-600")
        return

    state = _STATE
    db = ctx.open_db()
    with ui.row().classes("items-center gap-4"):
        agent_select = ui.select(
            list(VALID_AGENTS), value=config.agent, label="Agent"
        ).classes("w-40")
        model_select = ui.select(
            _model_options(db, config.agent),
            value=DEFAULT_OPTION,
            label="Model",
            with_input=True,
            new_value_mode="add-unique",
        ).classes("w-48")

        def apply_reasoning_enabled(agent: str) -> None:
            if reasoning_enabled(agent):
                reasoning_select.props(remove="disable")
            else:
                reasoning_select.props("disable")

        def on_agent_change() -> None:
            model_select.set_options(
                _model_options(db, agent_select.value), value=DEFAULT_OPTION
            )
            apply_reasoning_enabled(agent_select.value)

        agent_select.on_value_change(on_agent_change)

        def refresh_models() -> None:
            if _REFRESH_LOCK.locked():
                ui.notify("a model refresh is already running", type="warning")
                return
            client = ui.context.client
            agent = agent_select.value

            def apply_options(models: list[str]) -> None:
                model_select.set_options([DEFAULT_OPTION, *models], value=DEFAULT_OPTION)

            def notify(message: str, notify_type: str) -> None:
                with client:
                    ui.notify(message, type=notify_type)

            threading.Thread(
                target=_refresh_models,
                args=(ctx, agent, lambda: agent_select.value, apply_options, notify),
                daemon=True,
            ).start()
            ui.notify(f"refreshing models for {agent!r}…")

        ui.button(icon="refresh", on_click=refresh_models).props("flat round").tooltip(
            "Discover models for the selected agent"
        )
        reasoning_select = ui.select(
            [DEFAULT_OPTION, *VALID_REASONING], value=DEFAULT_OPTION, label="Reasoning"
        ).classes("w-40")
        reasoning_select.tooltip("agy folds reasoning into model names")
        apply_reasoning_enabled(config.agent)
        run_button = ui.button("Start run", on_click=lambda: start()).props(
            "icon=play_arrow"
        )
        if state.running:
            run_button.disable()

    with ui.row().classes("items-center gap-4 w-full"):
        groups_select = ui.select(
            all_group_names(config), value=[], label="Groups", multiple=True
        ).classes("min-w-64").props("use-chips")
        interests_select = ui.select(
            [i.name for i in config.interests],
            value=[],
            label="Interests",
            multiple=True,
        ).classes("min-w-64").props("use-chips")
    ui.label(
        "Leave both empty to run all interests."
    ).classes("text-gray-500 text-sm")

    progress_holder = ui.column().classes("w-full gap-0")
    status_column = ui.column().classes("w-full gap-2")

    def start() -> None:
        model = model_select.value
        if model == DEFAULT_OPTION:
            model = None
        reasoning = reasoning_select.value
        if reasoning == DEFAULT_OPTION:
            reasoning = None
        err = start_run(
            ctx,
            state,
            agent=agent_select.value,
            model=model,
            reasoning=reasoning,
            interests_sel=list(interests_select.value or []),
            groups_sel=list(groups_select.value or []),
        )
        if err is not None:
            ui.notify(err, type="negative")
            return
        run_button.disable()

    _last_snapshot: list[tuple | None] = [None]

    def refresh() -> None:
        snapshot = (len(state.events), state.running, state.run_id, state.error)
        if snapshot == _last_snapshot[0]:
            return
        _last_snapshot[0] = snapshot

        progress_holder.clear()
        status_column.clear()

        rows = visible_rows(state)
        with progress_holder:
            if rows:
                total = len(rows)
                terminal = sum(1 for _, status, _ in rows if status in ("done", "failed"))
                ui.linear_progress(
                    value=terminal / total if total else 0, show_value=False
                ).classes("w-full")

        with status_column:
            if rows:
                with ui.column().classes("w-full gap-1"):
                    for name, status, detail in rows:
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(name).classes("w-40 truncate")
                            if status == "pending":
                                ui.badge("pending").props("outline color=grey")
                            elif status == "running":
                                ui.spinner(size="sm")
                                ui.badge("running").props("color=primary")
                            elif status == "done":
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("check_circle").classes("text-positive")
                                    ui.badge("done").props("color=positive")
                            elif status == "failed":
                                ui.badge("failed").props("color=negative")
                                if detail:
                                    ui.label(detail).classes(
                                        "truncate max-w-md text-negative text-sm"
                                    ).tooltip(detail)
            elif not state.running:
                with ui.column().classes("w-full items-center text-gray-500 gap-2 py-8"):
                    ui.icon("rocket_launch").classes("text-4xl")
                    ui.label("Pick an agent and start a run.")

            if state.error:
                ui.label(f"Run failed: {state.error}").classes("text-red-600")
            if not state.running and state.run_id is not None:
                ui.label("Run complete.").classes("font-bold")
                ui.button(
                    "View reports",
                    icon="article",
                    on_click=lambda: ui.navigate.to("/reports"),
                ).props("flat")
        if not state.running and (state.run_id is not None or state.error):
            run_button.enable()

    ui.timer(0.5, refresh)
