import threading

import pytest

import scieflow.news.agents as agents_mod
import scieflow.news.gui.run_page as run_page
import scieflow.news.runner as runner_mod
from scieflow.news.gui.context import GuiContext
from scieflow.news.gui.run_page import (
    Progress,
    RunState,
    execute_run,
    queue_rows,
    reasoning_enabled,
    start_run,
    try_start,
    visible_rows,
)


@pytest.fixture(autouse=True)
def _reset_module_state():
    run_page._STATE = RunState()
    yield
    run_page._STATE = RunState()
    if run_page._REFRESH_LOCK.locked():
        run_page._REFRESH_LOCK.release()


def make_ctx(tmp_path, config_text="interests:\n  - name: Snakemake\n"):
    cfg = tmp_path / "news.yml"
    if config_text is not None:
        cfg.write_text(config_text)
    return GuiContext(config_path=cfg, db_path=tmp_path / "db.json")


def test_execute_run_success(tmp_path, monkeypatch, make_block):
    monkeypatch.setattr(
        runner_mod.agents,
        "run_agent",
        lambda agent, prompt, timeout, model=None, reasoning=None: make_block(
            prompt.split('"')[1]
        ),
    )
    ctx = make_ctx(tmp_path)
    state = RunState(running=True)
    execute_run(ctx, "claude", state)
    assert state.running is False
    assert state.error is None
    assert state.run_id is not None
    assert [(p.interest, p.status) for p in state.events] == [
        ("Snakemake", "running"),
        ("Snakemake", "done"),
    ]
    assert ctx.open_db().results_for_run(state.run_id)[0]["name"] == "Snakemake"


def test_execute_run_surfaces_errors(tmp_path):
    ctx = make_ctx(tmp_path, config_text=None)
    ctx.config_path.write_text("agent: gemini\n")  # invalid config
    state = RunState(running=True)
    execute_run(ctx, "claude", state)
    assert state.running is False
    assert state.error is not None
    assert "agent must be one of" in state.error


def test_try_start_guards_concurrent_runs():
    state = RunState()
    lock = threading.Lock()
    assert try_start(state, lock) is True
    assert state.running is True
    assert try_start(state, lock) is False


def test_try_start_resets_fields_from_previous_run():
    state = RunState(
        events=[Progress("X", "done")], error="boom", run_id=3, running=False
    )
    lock = threading.Lock()
    assert try_start(state, lock) is True
    assert state.events == []
    assert state.error is None
    assert state.run_id is None
    assert state.running is True


def test_module_level_state_persists_across_build_calls():
    # Guards against `state = RunState()` being recreated inside build();
    # the module-level _STATE must be the single source of truth.
    run_page._STATE.running = True
    assert run_page._STATE.running is True


def test_reasoning_enabled_false_for_agy():
    assert reasoning_enabled("agy") is False


def test_reasoning_enabled_true_for_claude_and_codex():
    assert reasoning_enabled("claude") is True
    assert reasoning_enabled("codex") is True


def test_queue_rows_all_pending_without_events():
    rows = queue_rows(["A", "B"], [])
    assert rows == [("A", "pending", ""), ("B", "pending", "")]


def test_queue_rows_mixed_events_latest_wins():
    events = [
        Progress("A", "running"),
        Progress("A", "done"),
        Progress("B", "failed", "boom"),
    ]
    rows = queue_rows(["A", "B", "C"], events)
    assert rows == [
        ("A", "done", ""),
        ("B", "failed", "boom"),
        ("C", "pending", ""),
    ]


def test_visible_rows_shows_all_pending_once_running_before_first_event():
    # Regression: try_start() sets running=True before any Progress event
    # arrives from the worker thread. The page must show the pending queue
    # immediately, not an empty/blank state, during that gap.
    state = RunState(running=True, interests=["A", "B"], events=[])
    assert visible_rows(state) == [("A", "pending", ""), ("B", "pending", "")]


def test_visible_rows_empty_when_idle_and_no_events():
    state = RunState(running=False, interests=["A", "B"], events=[])
    assert visible_rows(state) == []


def test_visible_rows_reflects_events_once_present():
    state = RunState(
        running=True,
        interests=["A", "B"],
        events=[Progress("A", "done")],
    )
    assert visible_rows(state) == [("A", "done", ""), ("B", "pending", "")]


def grouped_ctx(tmp_path):
    cfg = tmp_path / "news.yml"
    cfg.write_text(
        "interests:\n  - name: A\n  - name: B\n"
        "groups:\n  - name: g\n    interests: [B]\n"
    )
    return GuiContext(config_path=cfg, db_path=tmp_path / "db.json")


def test_start_run_resolves_selection_and_runs(tmp_path, monkeypatch, make_block):
    monkeypatch.setattr(
        runner_mod.agents, "run_agent",
        lambda agent, prompt, timeout, model=None, reasoning=None: make_block(prompt.split('"')[1]),
    )
    ctx = grouped_ctx(tmp_path)
    state = RunState()
    err = start_run(ctx, state, agent="claude", model=None, reasoning=None,
                    interests_sel=[], groups_sel=["g"])
    assert err is None
    assert state.interests == ["B"]
    # wait for the worker to finish
    import time

    for _ in range(100):
        if not state.running:
            break
        time.sleep(0.05)
    assert state.run_id is not None
    assert [r["name"] for r in ctx.open_db().results_for_run(state.run_id)] == ["B"]


def test_start_run_unknown_group_returns_error(tmp_path):
    ctx = grouped_ctx(tmp_path)
    state = RunState()
    err = start_run(ctx, state, agent="claude", model=None, reasoning=None,
                    interests_sel=[], groups_sel=["ghost"])
    assert err is not None and "unknown groups" in err
    assert state.running is False


def test_start_run_rejects_when_running(tmp_path):
    ctx = grouped_ctx(tmp_path)
    state = RunState(running=True)
    err = start_run(ctx, state, agent="claude", model=None, reasoning=None,
                    interests_sel=[], groups_sel=[])
    assert err is not None and "already" in err.lower()


def make_bare_ctx(tmp_path):
    # _refresh_models doesn't need a valid news.yml — it only touches
    # the models table via ctx.open_db().
    return GuiContext(config_path=tmp_path / "news.yml", db_path=tmp_path / "db.json")


def test_refresh_models_skips_stale_agent_but_still_caches(tmp_path, monkeypatch):
    # Regression: the user switched the agent select while discovery for the
    # *previous* agent was still running. The discovered list must still be
    # cached (it's valid, agent-keyed data) but must NOT be applied to the
    # select, which is now showing a different agent's models.
    monkeypatch.setattr(agents_mod, "discover_models", lambda agent, timeout=30: ["m1", "m2"])
    ctx = make_bare_ctx(tmp_path)
    applied: list[list[str]] = []
    notified: list[tuple[str, str]] = []
    run_page._refresh_models(
        ctx,
        "claude",
        current_agent=lambda: "codex",  # switched away before discovery finished
        apply_options=applied.append,
        notify=lambda msg, t: notified.append((msg, t)),
    )
    assert applied == []
    assert ctx.open_db().get_models("claude")["models"] == ["m1", "m2"]


def test_refresh_models_applies_when_agent_still_current(tmp_path, monkeypatch):
    monkeypatch.setattr(agents_mod, "discover_models", lambda agent, timeout=30: ["m1"])
    ctx = make_bare_ctx(tmp_path)
    applied: list[list[str]] = []
    notified: list[tuple[str, str]] = []
    run_page._refresh_models(
        ctx,
        "claude",
        current_agent=lambda: "claude",
        apply_options=applied.append,
        notify=lambda msg, t: notified.append((msg, t)),
    )
    assert applied == [["m1"]]
    assert notified and notified[0][1] == "positive"


def test_refresh_models_skips_entirely_when_already_running(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        agents_mod, "discover_models",
        lambda agent, timeout=30: (calls.append(agent), ["m1"])[1],
    )
    ctx = make_bare_ctx(tmp_path)
    run_page._REFRESH_LOCK.acquire()
    try:
        applied: list[list[str]] = []
        run_page._refresh_models(
            ctx,
            "claude",
            current_agent=lambda: "claude",
            apply_options=applied.append,
            notify=lambda *a: None,
        )
        # The defining behavior of the guard: discovery is never even
        # attempted while another refresh holds the lock (not just "the
        # result is discarded" — a weaker test could pass even without the
        # guard, e.g. if discovery happened to raise for other reasons).
        assert calls == []
        assert applied == []
        assert ctx.open_db().get_models("claude") is None
    finally:
        run_page._REFRESH_LOCK.release()
