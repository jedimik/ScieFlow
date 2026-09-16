from datetime import date

import pytest

from scieflow.news.agents import AgentError
from scieflow.news.config import Config, Interest
from scieflow.news.db import Database
from scieflow.news.runner import Progress, compute_window, run_queue

TODAY = date(2026, 7, 17)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "db.json")


def config(*names):
    return Config(interests=[Interest(name=n) for n in names], lookback_days=30, timeout=5)


def ok_runner(make_block):
    def _run(agent, prompt, timeout, model=None, reasoning=None):
        # the interest name is quoted in the first prompt line
        name = prompt.split('"')[1]
        return make_block(name)

    return _run


def test_window_first_run_uses_lookback(db):
    assert compute_window(db, "X", TODAY, 30, None, None) == (date(2026, 6, 17), TODAY)


def test_window_uses_last_checked(db):
    db.set_last_checked("X", date(2026, 7, 10))
    assert compute_window(db, "X", TODAY, 30, None, None) == (date(2026, 7, 10), TODAY)


def test_window_since_overrides(db):
    db.set_last_checked("X", date(2026, 7, 10))
    assert compute_window(db, "X", TODAY, 30, date(2026, 7, 1), None) == (
        date(2026, 7, 1),
        TODAY,
    )


def test_window_days_overrides(db):
    assert compute_window(db, "X", TODAY, 30, None, 7) == (date(2026, 7, 10), TODAY)


def test_successful_run_stores_results_and_state(db, make_block):
    events = []
    run_id = run_queue(
        config("Snakemake", "DuckDB"),
        db,
        today=TODAY,
        progress=events.append,
        agent_runner=ok_runner(make_block),
    )
    results = db.results_for_run(run_id)
    assert [r["name"] for r in results] == ["Snakemake", "DuckDB"]
    assert db.get_last_checked("Snakemake") == TODAY
    assert db.get_run(run_id)["failures"] == []
    assert [(e.interest, e.status) for e in events] == [
        ("Snakemake", "running"),
        ("Snakemake", "done"),
        ("DuckDB", "running"),
        ("DuckDB", "done"),
    ]


def test_failure_is_isolated_and_state_untouched(db, make_block):
    calls = []

    def flaky(agent, prompt, timeout, model=None, reasoning=None):
        name = prompt.split('"')[1]
        calls.append(name)
        if name == "Bad":
            raise AgentError("boom")
        return make_block(name)

    run_id = run_queue(
        config("Bad", "Good"), db, today=TODAY, agent_runner=flaky
    )
    assert calls == ["Bad", "Bad", "Good"]  # Bad retried once, Good still ran
    assert db.get_last_checked("Bad") is None
    assert db.get_last_checked("Good") == TODAY
    assert db.get_run(run_id)["failures"] == [{"name": "Bad", "reason": "boom", "raw_output": None}]


def test_retry_succeeds_on_second_attempt(db, make_block):
    attempts = {"n": 0}

    def flaky_once(agent, prompt, timeout, model=None, reasoning=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return "garbage with no header"
        return make_block("X")

    run_id = run_queue(config("X"), db, today=TODAY, agent_runner=flaky_once)
    assert attempts["n"] == 2
    assert db.get_run(run_id)["failures"] == []


def test_only_filters_and_validates(db, make_block):
    run_id = run_queue(
        config("A", "B"),
        db,
        only=["B"],
        today=TODAY,
        agent_runner=ok_runner(make_block),
    )
    assert [r["name"] for r in db.results_for_run(run_id)] == ["B"]
    with pytest.raises(ValueError, match="unknown interests: C"):
        run_queue(config("A"), db, only=["C"], today=TODAY, agent_runner=ok_runner(make_block))


def test_agent_override(db, make_block):
    seen = []

    def spy(agent, prompt, timeout, model=None, reasoning=None):
        seen.append(agent)
        return make_block("A")

    run_id = run_queue(config("A"), db, agent="agy", today=TODAY, agent_runner=spy)
    assert seen == ["agy"]
    assert db.get_run(run_id)["agent"] == "agy"


def grouped_config():
    cfg = Config(
        interests=[Interest(name="A"), Interest(name="B"), Interest(name="C")],
        lookback_days=30,
        timeout=5,
        model="cfg-model",
        reasoning="low",
    )
    from scieflow.news.config import Group

    cfg.groups = [Group(name="g1", interests=["B", "C"])]
    return cfg


def test_groups_select_and_model_passthrough(db, make_block):
    seen = []

    def spy(agent, prompt, timeout, model=None, reasoning=None):
        seen.append((model, reasoning))
        return make_block(prompt.split('"')[1])

    run_id = run_queue(grouped_config(), db, groups=["g1"], today=TODAY, agent_runner=spy)
    assert [r["name"] for r in db.results_for_run(run_id)] == ["B", "C"]
    assert seen == [("cfg-model", "low")] * 2


def test_explicit_model_overrides_config(db, make_block):
    seen = []

    def spy(agent, prompt, timeout, model=None, reasoning=None):
        seen.append((model, reasoning))
        return make_block(prompt.split('"')[1])

    run_queue(
        grouped_config(), db, only=["A"], model="cli-model", reasoning="high",
        today=TODAY, agent_runner=spy,
    )
    assert seen == [("cli-model", "high")]


def test_failure_records_raw_output(db):
    def bad_format(agent, prompt, timeout, model=None, reasoning=None):
        return "I could not find the requested information."

    run_id = run_queue(config("X"), db, today=TODAY, agent_runner=bad_format)
    failure = db.get_run(run_id)["failures"][0]
    assert "no '## X' header" in failure["reason"]
    assert failure["raw_output"] == "I could not find the requested information."


def test_agent_error_leaves_raw_none(db):
    def boom(agent, prompt, timeout, model=None, reasoning=None):
        raise AgentError("exploded")

    run_id = run_queue(config("X"), db, today=TODAY, agent_runner=boom)
    assert db.get_run(run_id)["failures"][0]["raw_output"] is None


def test_science_template_validation_and_lookback(db):
    from scieflow.news.templates import TEMPLATES

    def science_block(agent, prompt, timeout, model=None, reasoning=None):
        name = prompt.split('"')[1]
        lines = [f"## {name}"]
        for s in TEMPLATES["science"].sections:
            lines += [f"### {s}", "_Nothing found._"]
        lines += ["### 🔥 Highlight", "_Nothing found._"]
        return "\n".join(lines) + "\n"

    cfg = Config(
        interests=[Interest(name="PLM", template="science", lookback_days=60)],
        lookback_days=30,
        timeout=5,
    )
    run_id = run_queue(cfg, db, today=TODAY, agent_runner=science_block)
    result = db.results_for_run(run_id)[0]
    assert result["window_start"] == "2026-05-18"  # TODAY - 60, not - 30
    assert "New Papers & Preprints" in result["markdown"]


def test_tool_block_fails_science_validation(db, make_block):
    cfg = Config(interests=[Interest(name="PLM", template="science")], timeout=5)

    def tool_block(agent, prompt, timeout, model=None, reasoning=None):
        return make_block("PLM")  # tool sections — wrong for science

    run_id = run_queue(cfg, db, today=TODAY, agent_runner=tool_block)
    failure = db.get_run(run_id)["failures"][0]
    assert "missing sections" in failure["reason"]


def test_per_template_timeout_used_when_config_timeout_none(db):
    from scieflow.news.templates import TEMPLATES

    def science_block(agent, prompt, timeout, model=None, reasoning=None):
        name = prompt.split('"')[1]
        lines = [f"## {name}"]
        for s in TEMPLATES["science"].sections:
            lines += [f"### {s}", "_Nothing found._"]
        lines += ["### 🔥 Highlight", "_Nothing found._"]
        return "\n".join(lines) + "\n"

    seen = {}

    cfg = Config(
        interests=[
            Interest(name="PLM", template="science"),
            Interest(name="Snakemake", template="tool"),
        ],
        timeout=None,
    )

    def runner(agent, prompt, timeout, model=None, reasoning=None):
        name = prompt.split('"')[1]
        seen[name] = timeout
        if name == "PLM":
            return science_block(agent, prompt, timeout)
        lines = [f"## {name}"]
        for s in TEMPLATES["tool"].sections:
            lines += [f"### {s}", "_Nothing found._"]
        return "\n".join(lines) + "\n"

    run_queue(cfg, db, today=TODAY, agent_runner=runner)
    assert seen["PLM"] == 900
    assert seen["Snakemake"] == 300


def test_explicit_config_timeout_overrides_per_template(db):
    from scieflow.news.templates import TEMPLATES

    seen = {}

    def runner(agent, prompt, timeout, model=None, reasoning=None):
        name = prompt.split('"')[1]
        seen[name] = timeout
        if name == "PLM":
            lines = [f"## {name}"]
            for s in TEMPLATES["science"].sections:
                lines += [f"### {s}", "_Nothing found._"]
            lines += ["### 🔥 Highlight", "_Nothing found._"]
            return "\n".join(lines) + "\n"
        lines = [f"## {name}"]
        for s in TEMPLATES["tool"].sections:
            lines += [f"### {s}", "_Nothing found._"]
        return "\n".join(lines) + "\n"

    cfg = Config(
        interests=[
            Interest(name="PLM", template="science"),
            Interest(name="Snakemake", template="tool"),
        ],
        timeout=120,
    )
    run_queue(cfg, db, today=TODAY, agent_runner=runner)
    assert seen["PLM"] == 120
    assert seen["Snakemake"] == 120
