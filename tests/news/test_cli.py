from click.testing import CliRunner

import scieflow.news.runner as runner_mod
from scieflow.news.agents import AgentError
from scieflow.news.cli import news as main

MINIMAL = "interests:\n  - name: Snakemake\n"


def invoke(args, tmp_path, monkeypatch, make_block=None, config_text=MINIMAL):
    cfg = tmp_path / "news.yml"
    if config_text is not None:
        cfg.write_text(config_text)
    monkeypatch.setenv("SCIEFLOW_NEWS_DB", str(tmp_path / "db.json"))
    if make_block is not None:
        monkeypatch.setattr(
            runner_mod.agents,
            "run_agent",
            lambda agent, prompt, timeout, model=None, reasoning=None: make_block(
                prompt.split('"')[1]
            ),
        )
    return CliRunner().invoke(main, args + ["--config", str(cfg)])


def test_init_creates_example_config(tmp_path, monkeypatch):
    result = invoke(["init"], tmp_path, monkeypatch, config_text=None)
    assert result.exit_code == 0
    text = (tmp_path / "news.yml").read_text()
    assert "interests:" in text and "Snakemake" in text


def test_init_refuses_overwrite(tmp_path, monkeypatch):
    result = invoke(["init"], tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_run_prints_report_and_progress(tmp_path, monkeypatch, make_block):
    result = invoke(["run"], tmp_path, monkeypatch, make_block=make_block)
    assert result.exit_code == 0, result.output
    assert "## Snakemake" in result.output
    assert "# ScieFlow news report" in result.output


def test_run_rejects_days_below_one(tmp_path, monkeypatch):
    result = invoke(["run", "--days", "0"], tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "invalid" in result.output.lower()


def test_run_rejects_since_plus_days(tmp_path, monkeypatch):
    result = invoke(
        ["run", "--since", "2026-07-01", "--days", "7"], tmp_path, monkeypatch
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_run_unknown_interest(tmp_path, monkeypatch, make_block):
    result = invoke(
        ["run", "--interest", "Nope"], tmp_path, monkeypatch, make_block=make_block
    )
    assert result.exit_code != 0
    assert "unknown interests: Nope" in result.output


def test_run_config_error_is_clean(tmp_path, monkeypatch):
    result = invoke(["run"], tmp_path, monkeypatch, config_text="agent: gemini\n")
    assert result.exit_code != 0
    assert "agent must be one of" in result.output
    assert "Traceback" not in result.output


def test_status_shows_never_then_date(tmp_path, monkeypatch, make_block):
    result = invoke(["status"], tmp_path, monkeypatch)
    assert "Snakemake: last checked never" in result.output
    invoke(["run"], tmp_path, monkeypatch, make_block=make_block)
    result = invoke(["status"], tmp_path, monkeypatch)
    assert "never" not in result.output


def test_export_latest_writes_file(tmp_path, monkeypatch, make_block):
    invoke(["run"], tmp_path, monkeypatch, make_block=make_block)
    out_dir = tmp_path / "reports"
    result = invoke(
        ["export", "--latest", "-o", str(out_dir)], tmp_path, monkeypatch
    )
    assert result.exit_code == 0, result.output
    files = list(out_dir.glob("*-news.md"))
    assert len(files) == 1
    assert "## Snakemake" in files[0].read_text()


def test_export_requires_exactly_one_selector(tmp_path, monkeypatch):
    result = invoke(["export"], tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "exactly one of --run or --latest" in result.output


def test_export_no_runs(tmp_path, monkeypatch):
    result = invoke(["export", "--latest"], tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "no runs" in result.output


def test_run_total_failure_exits_nonzero(tmp_path, monkeypatch):
    def boom(agent, prompt, timeout, model=None, reasoning=None):
        raise AgentError("agent exploded")

    monkeypatch.setattr(runner_mod.agents, "run_agent", boom)
    cfg = tmp_path / "news.yml"
    cfg.write_text(MINIMAL)
    monkeypatch.setenv("SCIEFLOW_NEWS_DB", str(tmp_path / "db.json"))
    result = CliRunner().invoke(main, ["run", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "## ⚠ Failed" in result.output


def test_run_partial_failure_exits_zero(tmp_path, monkeypatch, make_block):
    def flaky(agent, prompt, timeout, model=None, reasoning=None):
        if '"Bad"' in prompt:
            raise AgentError("agent exploded")
        return make_block(prompt.split('"')[1])

    monkeypatch.setattr(runner_mod.agents, "run_agent", flaky)
    config_text = "interests:\n  - name: Snakemake\n  - name: Bad\n"
    result = invoke(["run"], tmp_path, monkeypatch, config_text=config_text)
    assert result.exit_code == 0, result.output
    assert "## Snakemake" in result.output
    assert "## ⚠ Failed" in result.output
    assert "- **Bad**" in result.output


def test_gui_command_registered():
    result = CliRunner().invoke(main, ["--help"])
    assert "gui" in result.output
    result = CliRunner().invoke(main, ["gui", "--help"])
    assert "--port" in result.output


def test_gui_no_browser_flag():
    result = CliRunner().invoke(main, ["gui", "--help"])
    assert "--no-browser" in result.output


def test_run_group_flag(tmp_path, monkeypatch, make_block):
    config_text = (
        "interests:\n  - name: Snakemake\n  - name: DuckDB\n"
        "groups:\n  - name: bio\n    interests: [Snakemake]\n"
    )
    monkeypatch.setattr(
        runner_mod.agents, "run_agent",
        lambda agent, prompt, timeout, model=None, reasoning=None: make_block(prompt.split('"')[1]),
    )
    result = invoke(["run", "--group", "bio"], tmp_path, monkeypatch, config_text=config_text)
    assert result.exit_code == 0, result.output
    assert "## Snakemake" in result.output
    assert "## DuckDB" not in result.output


def test_run_unknown_group_clean_error(tmp_path, monkeypatch):
    result = invoke(["run", "--group", "ghost"], tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "unknown groups: ghost" in result.output


def test_models_command_uses_cache_and_refresh(tmp_path, monkeypatch):
    import scieflow.news.cli as cli_mod

    monkeypatch.setenv("SCIEFLOW_NEWS_DB", str(tmp_path / "db.json"))
    calls = []

    def fake_discover(agent, timeout=30):
        calls.append(agent)
        return [f"{agent}-model"]

    monkeypatch.setattr(cli_mod.agents, "discover_models", fake_discover)
    result = CliRunner().invoke(main, ["models", "--agent", "agy"])
    assert result.exit_code == 0, result.output
    assert "agy: agy-model" in result.output
    assert calls == ["agy"]
    # cached now — no second discovery without --refresh
    result = CliRunner().invoke(main, ["models", "--agent", "agy"])
    assert calls == ["agy"]
    result = CliRunner().invoke(main, ["models", "--agent", "agy", "--refresh"])
    assert calls == ["agy", "agy"]


def test_models_command_empty_list_hints_refresh(tmp_path, monkeypatch):
    import scieflow.news.cli as cli_mod

    monkeypatch.setenv("SCIEFLOW_NEWS_DB", str(tmp_path / "db.json"))
    monkeypatch.setattr(cli_mod.agents, "discover_models", lambda agent, timeout=30: [])
    result = CliRunner().invoke(main, ["models", "--agent", "claude"])
    assert result.exit_code == 0, result.output
    assert "(none found — try --refresh)" in result.output


def test_templates_command():
    result = CliRunner().invoke(main, ["templates"])
    assert result.exit_code == 0
    assert "tool — Software tool" in result.output
    assert "science — Scientific topic" in result.output
    assert "New Papers & Preprints" in result.output


def test_gui_without_gui_extra_prints_install_hint(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "nicegui" or name.startswith("nicegui."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    import sys

    for mod in [m for m in sys.modules if m.startswith("scieflow.news.gui")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = CliRunner().invoke(main, ["gui", "--no-browser"])
    assert result.exit_code != 0
    assert "uv sync --extra news-gui" in result.output


def test_default_paths_live_in_the_repo(monkeypatch):
    from scieflow.core import config
    from scieflow.news import cli as news_cli

    monkeypatch.delenv("SCIEFLOW_NEWS_DB", raising=False)
    root = config.repo_root()
    assert news_cli.default_config_path() == root / "config" / "news.yml"
    assert news_cli.resolve_db_path(None) == root / "workspace" / "news" / "news.json"


def test_news_group_is_reachable_from_root_cli():
    from scieflow.cli import main as root_main

    result = CliRunner().invoke(root_main, ["news", "--help"])
    assert result.exit_code == 0, result.output
    assert "templates" in result.output
