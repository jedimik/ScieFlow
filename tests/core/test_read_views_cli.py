"""Read-only terminal views of the web dashboard aggregates (M-I t3, plan 5.3).

`scieflow run overview`, `run budgets`, `run workflows` and `agent conversational`
each call exactly one service function. Their `--json` output equals that
function's result; their text output is one line per item; they write nothing.
Synthetic fixtures only (the `project` fixture of tests/core/conftest.py).
"""

import json
import os
from pathlib import Path

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import service
from scieflow.core.project import Project
from scieflow.core.run import budget


def _invoke(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def _as_project(monkeypatch, project):
    monkeypatch.setattr(Project, "discover", lambda *a, **k: project)
    monkeypatch.chdir(project.root)
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)


def _plain(value):
    """What `--json` prints: the service value after json round-trip."""
    return json.loads(json.dumps(value, indent=2, default=str))


def _tree(root: Path) -> dict:
    """Every file under root with its bytes, so a read that writes anything shows."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


def test_overview_json_equals_the_service_and_text_is_one_line_per_panel(project, monkeypatch):
    _as_project(monkeypatch, project)
    expected = service.run_overview(project, "r1")

    as_json = _invoke("run", "overview", "r1", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == _plain(expected)

    text = _invoke("run", "overview", "r1")
    assert text.exit_code == 0, text.output
    lines = text.output.splitlines()
    assert lines == [
        f"{name}: {panel.get('reason') or 'ok'}" for name, panel in expected.items()
    ]


def test_budgets_json_equals_the_service_and_text_shows_the_smallest_fraction(project, monkeypatch):
    _as_project(monkeypatch, project)
    ws = project.run_dir("r1")
    b = budget.new_budget(max_iterations=10, max_experiment_runs=10, max_wall_minutes=100000)
    budget.write_budget(ws, budget.record(b, iterations=5))  # iterations 50% left: the smallest

    as_json = _invoke("run", "budgets", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == _plain(service.list_runs_with_budget(project))

    text = _invoke("run", "budgets")
    assert text.exit_code == 0, text.output
    lines = text.output.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("r1 ") and lines[0].endswith(" 50%")


def test_budgets_text_says_no_budget_when_the_run_has_none(project, monkeypatch):
    _as_project(monkeypatch, project)

    text = _invoke("run", "budgets")

    assert text.exit_code == 0, text.output
    assert text.output.splitlines()[0].endswith("no budget")


def test_workflows_json_equals_the_service_and_text_is_name_and_roles(project, monkeypatch):
    _as_project(monkeypatch, project)
    expected = service.workflows()

    as_json = _invoke("run", "workflows", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == _plain(expected)

    text = _invoke("run", "workflows")
    assert text.exit_code == 0, text.output
    lines = text.output.splitlines()
    assert len(lines) == len(expected)
    for line, workflow in zip(lines, expected):
        assert line.startswith(f"{workflow['name']}: ")


def test_conversational_agents_json_equals_the_service_and_text_is_one_name_per_line(project, monkeypatch):
    _as_project(monkeypatch, project)
    expected = service.conversational_agents(project)

    as_json = _invoke("agent", "conversational", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == _plain(expected)

    text = _invoke("agent", "conversational")
    assert text.exit_code == 0, text.output
    assert text.output.splitlines() == expected
    assert "sleepy" not in text.output.splitlines()  # no session_cmd: cannot hold a conversation


def test_unknown_slug_is_exit_one_with_the_service_message(project, monkeypatch):
    _as_project(monkeypatch, project)

    result = CliRunner().invoke(main, ["run", "overview", "nope"])

    assert result.exit_code == 1, result.output
    assert "no run workspace/nope" in result.output


def test_each_read_view_writes_nothing(project, monkeypatch):
    _as_project(monkeypatch, project)
    before = _tree(project.root)

    for args in (
        ["run", "overview", "r1"],
        ["run", "overview", "r1", "--json"],
        ["run", "budgets"],
        ["run", "budgets", "--json"],
        ["run", "workflows"],
        ["run", "workflows", "--json"],
        ["agent", "conversational"],
        ["agent", "conversational", "--json"],
    ):
        assert CliRunner().invoke(main, args).exit_code == 0, args

    assert _tree(project.root) == before


def test_run_help_lists_the_three_new_views():
    result = _invoke("run", "--help")

    assert result.exit_code == 0
    for name in ("overview", "budgets", "workflows"):
        assert name in result.output, name
