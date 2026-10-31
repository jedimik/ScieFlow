"""M-C2 t7 (plan 5.7; Part B t7 item 4): a news interest with a `literature:` block.

The monitor is replaced by a fake that returns recorded results: no agent is called, no search
runs and no network is used. The news database is a real TinyDB file under tmp_path.
"""

from datetime import date

import pytest

from scieflow.news import runner
from scieflow.research import monitor as research_monitor
from scieflow.news.config import ConfigError, Config, Interest, load_config
from scieflow.news.db import Database

TODAY = date(2026, 10, 2)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "db.json")


def lit_config(name="alerts", **literature):
    return Config(interests=[Interest(name=name, literature=literature or {"workspace": "demo",
                                                                            "monitor": "alerts"})],
                  lookback_days=30, timeout=5)


def no_agent(*_args, **_kwargs):
    raise AssertionError("a literature interest must not call an agent")


def test_config_accepts_a_literature_block(tmp_path):
    path = tmp_path / "news.yml"
    path.write_text("interests:\n  - name: alerts\n    literature:\n"
                    "      workspace: demo\n      monitor: alerts\n", encoding="utf-8")
    config = load_config(path)
    assert config.interests[0].literature == {"workspace": "demo", "monitor": "alerts"}


@pytest.mark.parametrize("block", [
    "{workspace: demo}",
    "{monitor: alerts}",
    "{workspace: demo, monitor: alerts, extra: 1}",
    "{workspace: '', monitor: alerts}",
    "just-a-string",
])
def test_a_literature_block_needs_exactly_workspace_and_monitor(tmp_path, block):
    path = tmp_path / "news.yml"
    path.write_text(f"interests:\n  - name: alerts\n    literature: {block}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_run_stores_the_new_papers_and_calls_no_agent(db, monkeypatch):
    calls = []

    def fake_monitor(workspace, name, *, ts):
        calls.append((workspace, name, ts))
        return [{"title": "Synthetic paper one", "year": 2020, "doi": "10.5555/paper1",
                 "url": ""}]

    monkeypatch.setattr(research_monitor, "run_for_news", fake_monitor)
    run_id = runner.run_queue(lit_config(), db, today=TODAY, agent_runner=no_agent)
    assert calls == [("demo", "alerts", "2026-10-02T00:00:00Z")]
    (result,) = db.results_for_run(run_id)
    assert result["markdown"] == ("## New literature\n\n"
                                  "- Synthetic paper one (2020) https://doi.org/10.5555/paper1\n")
    assert db.get_last_checked("alerts") == TODAY


def test_a_rerun_with_nothing_new_says_so(db, monkeypatch):
    monkeypatch.setattr(research_monitor, "run_for_news",
                        lambda workspace, name, *, ts: [])
    run_id = runner.run_queue(lit_config(), db, today=TODAY, agent_runner=no_agent)
    (result,) = db.results_for_run(run_id)
    assert result["markdown"] == "## New literature\n\nNo new papers since the last run.\n"


def test_a_title_is_sanitised_before_it_is_stored(db, monkeypatch):
    monkeypatch.setattr(research_monitor, "run_for_news", lambda w, n, *, ts: [
        {"title": "Synthetic\u200b paper \x1b[31mred\x1b[0m", "year": 2021, "doi": "10.5555/p9"}])
    run_id = runner.run_queue(lit_config(), db, today=TODAY, agent_runner=no_agent)
    (result,) = db.results_for_run(run_id)
    assert "\x1b" not in result["markdown"] and "\u200b" not in result["markdown"]


def test_a_monitor_failure_is_a_failure_with_no_result(db, monkeypatch):
    from scieflow.research.monitor import MonitorRunFailed

    def failing(workspace, name, *, ts):
        raise MonitorRunFailed(6, "every source is a snapshot miss")

    monkeypatch.setattr(research_monitor, "run_for_news", failing)
    run_id = runner.run_queue(lit_config(), db, today=TODAY, agent_runner=no_agent)
    assert db.results_for_run(run_id) == []
    (run,) = [db.get_run(run_id)]
    assert run["failures"][0]["name"] == "alerts"
    assert db.get_last_checked("alerts") is None


def test_a_missing_research_extra_is_a_failure_not_a_crash(db, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "scieflow.research.monitor", None)
    run_id = runner.run_queue(lit_config(), db, today=TODAY, agent_runner=no_agent)
    assert db.results_for_run(run_id) == []
    assert db.get_run(run_id)["failures"][0]["name"] == "alerts"
