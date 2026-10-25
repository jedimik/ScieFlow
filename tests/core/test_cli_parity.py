"""Parity between the web service layer and the `scieflow` command tree (M-A T5, A-9).

The table in `cli_parity.yml` says, for every public function of
`scieflow.core.service`, either the command that runs it or why it has none.
Three assertions keep that table honest:

(a) the table's keys are exactly the service's public functions, so a new
    function without an entry (or a stale entry) fails here;
(b) every `cli:` path resolves in the assembled `scieflow` tree, and every
    `allow:` reason is non-empty;
(c) for each `new:` entry, invoking the command reaches exactly one call of
    the service function the table names, so the command is wired to the
    function rather than to something that looks like it.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import jobs, service
from scieflow.core.project import Project

TABLE = yaml.safe_load((Path(__file__).parent / "cli_parity.yml").read_text())["functions"]


def _public_service_functions() -> set[str]:
    return {
        name for name, obj in vars(service).items()
        if inspect.isfunction(obj) and obj.__module__ == "scieflow.core.service"
        and not name.startswith("_")
    }


def _resolve(path: str):
    ctx = click.Context(main)
    cmd = main
    for token in path.split():
        if not isinstance(cmd, click.Group):
            return None
        cmd = cmd.get_command(ctx, token)
        if cmd is None:
            return None
    return cmd


def test_table_covers_exactly_the_public_service_functions():
    assert set(TABLE) == _public_service_functions()


@pytest.mark.parametrize("name", sorted(TABLE))
def test_every_entry_names_a_command_or_a_reason(name):
    entry = TABLE[name]
    if "allow" in entry:
        assert entry["allow"] and entry["allow"].strip(), f"{name}: empty allow reason"
        assert "cli" not in entry, f"{name}: an entry is either allow or cli, not both"
    else:
        assert _resolve(entry["cli"]) is not None, f"{name}: {entry['cli']!r} does not resolve"


@pytest.fixture
def wired(project, monkeypatch, tmp_path):
    """The real project for `Project.discover`, a job of run r1 for the
    ownership check, and no dispatch markers in the environment."""
    monkeypatch.setattr(Project, "discover", lambda *a, **k: project)
    monkeypatch.setattr(jobs, "find", lambda proj, job_id: SimpleNamespace(
        id=job_id, run_dir=str(project.run_dir("r1"))))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    (tmp_path / "goal.md").write_text("Find the limit.\n")
    return project


NEW = sorted(name for name, entry in TABLE.items() if entry.get("new"))


@pytest.mark.parametrize("name", NEW)
def test_new_command_reaches_exactly_the_named_service_function(name, wired, tmp_path, monkeypatch):
    entry = TABLE[name]
    calls = []

    def recorder(*args, **kwargs):
        calls.append(args)
        return {"recorded": name}

    monkeypatch.setattr(service, name, recorder)
    argv = [tok.replace("{tmp}", str(tmp_path)) for tok in entry["args"]]
    result = CliRunner().invoke(main, [*entry["cli"].split(), *argv])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, f"{name}: expected one call, got {len(calls)}"
