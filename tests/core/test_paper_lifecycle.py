"""Native paper lifecycle tests; every workspace contains synthetic metadata only."""
# Origin: the paper1 session's untracked test (written 2026-10-14 against 2e93b14, frozen 2026-10-31); only the two dispatch fixtures adapted to the S4 credential rule.

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events, store
from scieflow.core.project import Project
from scieflow.core.run import budget, status, validate
from scieflow.core.run.init import adopt_workspace, init_workspace

ROOT = Path(__file__).resolve().parents[2]
PAPER_PHASES = ["intake", "outline", "draft", "cross-review", "merge", "verify", "handoff"]


@pytest.fixture
def paper_project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: autonomous\nmax_iterations: 5\nmax_experiment_runs: 40\n"
        "max_wall_minutes: 120\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "status-research", "gates"):
        (tmp_path / "schemas" / f"{name}.yml").write_bytes(
            (ROOT / "schemas" / f"{name}.yml").read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "_VOCAB", None)
    return Project(tmp_path)


def invoke(*args):
    return CliRunner().invoke(main, list(args))


def test_native_init_paper_draft(paper_project):
    goal = paper_project.root / "synthetic-goal.md"
    goal.write_text("Synthetic metadata lifecycle exercise only.\n")
    result = invoke("run", "init", "paper", "--workflow", "paper-draft",
                    "--goal", str(goal), "--approval", "per-campaign")
    assert result.exit_code == 0, result.output
    ws = paper_project.run_dir("paper")
    shown = invoke("run", "show", "paper", "--json")
    assert shown.exit_code == 0, shown.output
    doc = json.loads(shown.output)
    st = doc["status"]
    assert st["workflow"] == "paper-draft"
    assert st["slug"] == "paper" and "run" not in st
    assert st["approval"] == "per-campaign"
    assert list(st["phases"]) == PAPER_PHASES
    assert set(st["phases"].values()) == {"pending"}
    assert validate.validate_status(st) == []
    assert doc["budget"] is None and not (ws / "budget.yml").exists()
    assert not (ws / "iterations").exists() and not (ws / "notebook.md").exists()
    assert (ws / "goal.md").read_bytes() == goal.read_bytes()
    assert yaml.safe_load((ws / "config.yml").read_text()) == {
        "workflow": "paper-draft", "approval": "per-campaign"}
    assert [e["type"] for e in events.read(ws)] == ["run.created"]


def test_native_paper_phase_lifecycle(paper_project):
    goal = paper_project.root / "synthetic-goal.md"
    goal.write_text("Synthetic lifecycle only.\n")
    assert invoke("run", "init", "paper", "--workflow", "paper-draft", "--goal",
                  str(goal), "--approval", "per-campaign").exit_code == 0
    ws = paper_project.run_dir("paper")
    for phase in PAPER_PHASES:
        result = invoke("run", "mark", "paper", phase, "done", "--as-agent")
        assert result.exit_code == 0, result.output
    before = (ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes()
    for phase in ("experiment", "hypothesize", "unknown"):
        assert invoke("run", "mark", "paper", phase, "done", "--as-agent").exit_code != 0
        assert before == ((ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes())
    # A completed paper is handed off, not silently reset as another loop iteration.
    assert invoke("run", "advance", "paper", "--as-agent").exit_code != 0
    assert before == ((ws / "status.yml").read_bytes(), (ws / "events.jsonl").read_bytes())
    result = invoke("run", "checkpoint", "paper", "--reason", "user", "--as-agent")
    assert result.exit_code == 0, result.output
    assert "complete" in result.output
    assert "iterations/" not in result.output and "budget.yml" not in result.output
    assert invoke("run", "resume", "paper", "--as-agent").exit_code == 0
    assert status.read_status(ws)["stopped"] is None
    assert all(e["actor"] == "agent" for e in events.read(ws)[1:])


def test_legacy_paper_status_without_iteration_can_be_marked(paper_project):
    ws = paper_project.run_dir("legacy-paper")
    ws.mkdir(parents=True)
    legacy = {"workflow": "paper-draft", "slug": ws.name,
              "phases": dict.fromkeys(PAPER_PHASES, "pending")}
    status.write_status(ws, legacy)
    assert validate.validate_status_research(legacy) == []
    result = invoke("run", "mark", ws.name, "intake", "done", "--as-agent")
    assert result.exit_code == 0, (result.output, result.exception)
    assert status.read_status(ws)["phases"]["intake"] == "done"
    assert "iteration" not in status.read_status(ws)
    assert "iteration" not in events.read(ws)[0]["data"]
    assert invoke("run", "checkpoint", ws.name, "--reason", "user", "--as-agent").exit_code == 0
    assert invoke("run", "resume", ws.name, "--as-agent").exit_code == 0


def prepared(project, with_budget=False):
    ws = project.run_dir("prepared")
    (ws / "inputs").mkdir(parents=True)
    (ws / "inputs" / "metadata.txt").write_bytes(b"synthetic input\r\n")
    (ws / "goal.md").write_bytes(b"Existing synthetic goal\r\n")
    (ws / "config.yml").write_text(
        "# Preserve formatting and comments\nworkflow: paper-draft\n"
        "agent_overrides: {fixture: {model: fixture-only}}\n"
        "review: {scite: off, allow_self_review: false}\n")
    (ws / "gates").mkdir()
    (ws / "gates" / "synthetic.yml").write_text("state: answered\nanswer: fixture-only\n")
    (ws / "charter.yml").write_text(
        "current: 1\nversions: [{n: 1, text: Synthetic charter only}]\n")
    (ws / "events.jsonl").write_bytes(b'{"type":"note.synthetic","data":{}}\r\n')
    if with_budget:
        b = budget.new_budget(2, 3, 4)
        budget.record(b, wall_minutes=4, experiment_runs=1)
        budget.write_budget(ws, b)
    return ws


def snapshot(ws):
    return {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")}


@pytest.mark.parametrize("with_budget", [False, True])
def test_native_adopt_preserves_prepared_workspace(paper_project, with_budget):
    ws = prepared(paper_project, with_budget)
    before = snapshot(ws)
    replacement = paper_project.root / "other-goal.md"
    replacement.write_text("Must not replace the existing goal.\n")
    result = invoke("run", "adopt", "prepared", "--workflow", "paper-draft",
                    "--approval", "per-campaign", "--goal", str(replacement), "--as-agent")
    assert result.exit_code == 0, result.output
    assert ws == paper_project.run_dir("prepared")
    after = snapshot(ws)
    for name, data in before.items():
        if name == "events.jsonl":
            assert after[name].startswith(data)
        else:
            assert after[name] == data, name
    st = status.read_status(ws)
    assert st["workflow"] == "paper-draft" and st["approval"] == "per-campaign"
    assert list(st["phases"]) == PAPER_PHASES
    assert set(st["phases"].values()) == {"pending"}
    assert validate.validate_status(st) == []
    assert (ws / "budget.yml").exists() == with_budget
    ev = events.read(ws)[-1]
    assert ev["type"] == "note.run-adopted" and ev["actor"] == "agent"
    assert ev["run"] == st["id"] and ev["data"]["workflow"] == "paper-draft"
    assert invoke("run", "show", "prepared", "--json").exit_code == 0
    assert invoke("run", "adopt", "prepared", "--workflow", "paper-draft",
                  "--approval", "per-campaign").exit_code != 0
    assert snapshot(ws) == after  # no resetting on retry


def adopt_prepared(project, **kwargs):
    return adopt_workspace("prepared", project.workspace_root, workflow="paper-draft",
                           approval="per-campaign", **kwargs)


def test_adopt_creates_only_missing_metadata(paper_project):
    ws = paper_project.run_dir("prepared")
    ws.mkdir(parents=True)
    goal = ws / "approval.md"
    goal.write_bytes(b"Synthetic approved goal\r\n")
    adopt_prepared(paper_project, goal_file=goal)
    assert (ws / "goal.md").read_bytes() == goal.read_bytes()
    assert yaml.safe_load((ws / "config.yml").read_text()) == {
        "workflow": "paper-draft", "approval": "per-campaign"}
    assert not (ws / "budget.yml").exists() and not (ws / "charter.yml").exists()


@pytest.mark.parametrize("content", ["workflow: research-loop\n", "approval: autonomous\n",
                                     "[not, a, mapping]\n", "broken: [\n"])
def test_adopt_refuses_conflicting_or_malformed_config(paper_project, content):
    ws = prepared(paper_project)
    (ws / "config.yml").write_text(content)
    before = snapshot(ws)
    result = invoke("run", "adopt", "prepared", "--workflow", "paper-draft",
                    "--approval", "per-campaign")
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert snapshot(ws) == before


def test_adopt_does_not_need_a_workflow_config_edit(paper_project):
    ws = prepared(paper_project)
    cfg = b"# prepared settings only\nreview: {scite: off}\n"
    (ws / "config.yml").write_bytes(cfg)
    adopt_prepared(paper_project)
    assert (ws / "config.yml").read_bytes() == cfg
    assert status.read_status(ws)["workflow"] == "paper-draft"


@pytest.mark.parametrize("name", ["status.yml", "config.yml", "goal.md", "events.jsonl",
                                  "budget.yml", "charter.yml", "status.yml.lock",
                                  "config.yml.lock", "events.jsonl.lock"])
@pytest.mark.parametrize("dangling", [False, True])
def test_adopt_refuses_control_symlinks(paper_project, name, dangling):
    ws = prepared(paper_project)
    outside = paper_project.root / "untouched"
    if not dangling:
        outside.write_bytes(b"outside sentinel")
    path = ws / name
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        adopt_prepared(paper_project)
    assert path.is_symlink()
    assert not outside.exists() if dangling else outside.read_bytes() == b"outside sentinel"


@pytest.mark.parametrize("slug", ["../escape", "a/b", "/absolute", "..", ""])
def test_adopt_refuses_escaping_slug(paper_project, slug):
    with pytest.raises(ValueError):
        adopt_workspace(slug, paper_project.workspace_root, workflow="paper-draft",
                        approval="per-campaign")
    assert not paper_project.workspace_root.exists()


def test_adopt_refuses_workspace_alias_and_symlink_root(paper_project):
    ws = prepared(paper_project)
    alias = paper_project.run_dir("alias")
    alias.symlink_to(ws, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        adopt_workspace("alias", paper_project.workspace_root, workflow="paper-draft",
                        approval="per-campaign")
    link = paper_project.root / "alias-root"
    link.symlink_to(paper_project.workspace_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        adopt_workspace("prepared", link, workflow="paper-draft", approval="per-campaign")
    assert not (ws / "status.yml").exists()


@pytest.mark.parametrize("name", ["config.yml", "goal.md", "status.yml", "events.jsonl"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_adoption_failure_rolls_back_only_owned_changes(paper_project, monkeypatch,
                                                       name, after_replace):
    ws = prepared(paper_project, with_budget=True)
    (ws / "config.yml").unlink()
    (ws / "goal.md").unlink()
    goal = ws / "approval.md"
    goal.write_text("Synthetic goal\n")
    before = snapshot(ws)
    original = store._atomic_write
    failed = False

    def fail_once(path, text):
        nonlocal failed
        if path.name == name and not failed:
            failed = True
            if after_replace:
                original(path, text)
            raise OSError("injected write failure")
        original(path, text)

    monkeypatch.setattr(store, "_atomic_write", fail_once)
    with pytest.raises(OSError, match="injected"):
        adopt_prepared(paper_project, goal_file=goal)
    assert ws.is_dir() and snapshot(ws) == before
    adopt_prepared(paper_project, goal_file=goal)
    assert status.read_status(ws)["phases"]["intake"] == "pending"
    assert len(events.read(ws)) == 2


def test_paper_operations_require_explicit_approval(paper_project):
    ws = prepared(paper_project)
    before = snapshot(ws)
    for args in (("adopt", "prepared"), ("init", "new", "--goal", str(ws / "goal.md"))):
        result = invoke("run", *args, "--workflow", "paper-draft")
        assert result.exit_code != 0 and "approval" in result.output
    assert snapshot(ws) == before and not paper_project.run_dir("new").exists()


def test_failed_adoption_does_not_erase_concurrent_event(paper_project, monkeypatch):
    ws = prepared(paper_project)
    history = (ws / "events.jsonl").read_bytes()
    original = store._atomic_write
    started = Event()
    futures = []

    def append_note():
        started.set()
        return events.emit(ws, "note.concurrent-fixture", "system")

    with ThreadPoolExecutor(max_workers=1) as pool:
        def fail_once(path, text):
            if path.name == "status.yml" and not futures:
                futures.append(pool.submit(append_note))
                assert started.wait(timeout=5)
                raise OSError("injected concurrent failure")
            original(path, text)

        monkeypatch.setattr(store, "_atomic_write", fail_once)
        with pytest.raises(OSError, match="injected"):
            adopt_prepared(paper_project)
        futures[0].result(timeout=5)
    assert not (ws / "status.yml").exists()
    assert (ws / "events.jsonl").read_bytes().startswith(history)
    assert events.read(ws)[-1]["type"] == "note.concurrent-fixture"
    adopt_prepared(paper_project)
    assert [e["type"] for e in events.read(ws)] == [
        "note.synthetic", "note.concurrent-fixture", "note.run-adopted"]


def test_init_refuses_symlink_workspace_root(paper_project):
    outside = paper_project.root / "outside"
    outside.mkdir()
    paper_project.workspace_root.symlink_to(outside, target_is_directory=True)
    goal = paper_project.root / "goal.md"
    goal.write_text("synthetic\n")
    with pytest.raises(ValueError, match="symlink"):
        init_workspace("paper", goal, paper_project.workspace_root,
                       {"workflow": "paper-draft", "approval": "per-campaign"}, paper_project.root)
    assert list(outside.iterdir()) == []


def test_init_race_never_deletes_an_existing_workspace(paper_project, monkeypatch):
    ws = paper_project.run_dir("paper")
    ws.parent.mkdir()
    original = Path.mkdir
    raced = False

    def concurrent_mkdir(path, *args, **kwargs):
        nonlocal raced
        if path in (ws, ws / "iterations") and not raced:
            raced = True
            original(ws)
            (ws / "user-owned.txt").write_text("preserve me")
            raise FileExistsError("concurrent creator won")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", concurrent_mkdir)
    goal = paper_project.root / "goal.md"
    goal.write_text("synthetic\n")
    with pytest.raises(FileExistsError):
        init_workspace("paper", goal, paper_project.workspace_root,
                       {"workflow": "paper-draft", "approval": "per-campaign"}, paper_project.root)
    assert (ws / "user-owned.txt").read_text() == "preserve me"


def test_adoption_rechecks_workspace_after_waiting_for_lock(paper_project, monkeypatch):
    from scieflow.core.run import init as init_mod

    ws = prepared(paper_project)
    moved = paper_project.root / "moved-synthetic-fixture"
    before = snapshot(ws)
    original = init_mod._initialization_lock

    @contextmanager
    def concurrent_move(path):
        with original(path):
            ws.rename(moved)
            yield

    monkeypatch.setattr(init_mod, "_initialization_lock", concurrent_move)
    with pytest.raises(ValueError, match="existing workspace"):
        adopt_prepared(paper_project)
    assert not ws.exists() and snapshot(moved) == before


def test_concurrent_native_cli_adoption_has_one_winner(paper_project):
    ws = prepared(paper_project)
    before = snapshot(ws)
    cmd = [sys.executable, "-B", "-m", "scieflow.cli", "run", "adopt", "prepared",
           "--workflow", "paper-draft", "--approval", "per-campaign", "--as-agent"]
    with store.locked(ws / "status.yml"):
        procs = [subprocess.Popen(cmd, cwd=paper_project.root, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = [p.communicate(timeout=30) for p in procs]
    assert sorted(p.returncode for p in procs) == [0, 1], results
    assert len(events.read(ws)) == 2
    for name, data in before.items():
        if name != "events.jsonl":
            assert (ws / name).read_bytes() == data


def test_service_paper_creation_and_loop_compatibility(paper_project):
    from scieflow.core import service

    paper = service.create_run(paper_project, "paper", "synthetic", workflow="paper-draft",
                               approval="per-campaign")
    assert paper["run"]["kind"] == "paper-draft" and paper["run"]["phase"] == "intake"
    assert paper["budget"] is None
    with pytest.raises(service.ServiceError, match="explicit --approval"):
        service.create_run(paper_project, "no-intent", "synthetic", workflow="paper-draft")
    goal = paper_project.root / "goal.md"
    goal.write_text("synthetic\n")
    assert invoke("run", "init", "loop", "--goal", str(goal)).exit_code == 0
    ws = paper_project.run_dir("loop")
    st = status.read_status(ws)
    assert "workflow" not in st  # old-format loop documents remain valid
    assert validate.validate_status(st) == []
    before = snapshot(ws)
    for phase in ("intake", "draft", "unknown"):
        assert invoke("run", "mark", "loop", phase, "done", "--as-agent").exit_code != 0
        assert snapshot(ws) == before
    for phase in status.PHASES:
        assert invoke("run", "mark", "loop", phase, "done", "--as-agent").exit_code == 0
    assert invoke("run", "advance", "loop", "--as-agent").exit_code == 0
    assert status.read_status(ws)["iteration"] == 2
    assert invoke("run", "checkpoint", "loop", "--reason", "user", "--as-agent").exit_code == 0
    assert invoke("run", "resume", "loop", "--as-agent").exit_code == 0
    assert budget.read_budget(ws)["budgets"]["max_experiment_runs"] == 40


def test_paper_budget_is_explicit_not_partially_defaulted(paper_project):
    goal = paper_project.root / "goal.md"
    goal.write_text("synthetic\n")
    args = ("run", "init", "paper", "--workflow", "paper-draft", "--goal", str(goal),
            "--approval", "per-campaign")
    assert invoke(*args, "--max-wall-minutes", "5").exit_code != 0
    assert not paper_project.run_dir("paper").exists()
    assert invoke(*args, "--max-wall-minutes", "5", "--max-experiment-runs", "0",
                  "--max-iterations", "1").exit_code == 0
    assert budget.read_budget(paper_project.run_dir("paper"))["budgets"] == {
        "max_wall_minutes": 5, "max_experiment_runs": 0, "max_iterations": 1}


@pytest.mark.parametrize("existing", ["", "workflow: paper-draft\nphases: {}\n",
                                     "run: old-loop\nphases: {}\n"])
def test_adopt_never_replaces_any_existing_status(paper_project, existing):
    ws = prepared(paper_project)
    (ws / "status.yml").write_text(existing)
    before = snapshot(ws)
    with pytest.raises(FileExistsError, match="already initialized"):
        adopt_prepared(paper_project)
    assert snapshot(ws) == before


def fixture_dispatch(project, grant, with_budget=False):
    ws = prepared(project, with_budget)
    (ws / "logs").mkdir()
    (ws / "tools").mkdir()
    helper = ws / "tools" / "ping.py"
    helper.write_text(
        "from pathlib import Path\n"
        f"Path({str(ws / 'fixture-result.txt')!r}).write_text('fixture process only\\n')\n"
        "print('fixture process only; not a provider response')\n")
    registry = {"agents": {"fixture": {"cmd": "{python} " + str(helper),
                                      "enabled": True, "tier": "primary",
                                      "model_family": "fixture", "timeout_min": 1}}}
    (project.root / "config" / "agents.yml").write_text(yaml.safe_dump(registry))
    grant(project.root, "fixture")
    with (ws / "config.yml").open("a") as handle:
        handle.write("assignments: {research.draft-authors: [fixture]}\n")
    adopt_prepared(project)
    prompt = ws / "logs" / "ping.md"
    prompt.write_text("Synthetic dispatch mechanics only.\n")
    return ws, prompt


def require_single_workspace_ancestor(ws):
    # Maintenance worktrees can themselves be under workspace/<slug>. Use a
    # private mount alias for those runs; never bypass the production resolver.
    if sum(p.parent.name == "workspace" for p in (ws, *ws.parents)) != 1:
        pytest.skip("fixture needs a non-nested workspace path (use private mount alias)")


def test_adopted_dispatch_preparation_and_budget_guards(paper_project, grant_credentials):
    from scieflow.core import agent_run, service

    ws, prompt = fixture_dispatch(paper_project, grant_credentials, with_budget=True)
    require_single_workspace_ancestor(ws)
    d = agent_run.prepare(paper_project, "fixture", prompt, role="research.draft-authors")
    assert d.run_dir == ws and ws in d.writable
    assert "Synthetic charter only" in agent_run.compose_prompt(ws, prompt.read_text())
    with pytest.raises(agent_run.DispatchError, match="unknown agent"):
        agent_run.prepare(paper_project, "unregistered", prompt)
    with pytest.raises(agent_run.DispatchError, match="not assigned"):
        agent_run.prepare(paper_project, "fixture", prompt, role="research.outline")
    b = (ws / "budget.yml").read_bytes()
    with pytest.raises(service.ServiceError, match="budget exhausted"):
        service.dispatch_agent(paper_project, "fixture", prompt, ws / "logs" / "ping.out",
                               role="research.draft-authors")
    assert (ws / "budget.yml").read_bytes() == b
    st = status.read_status(ws)
    assert st["stopped"]["reason"] == "low-budget"
    assert "intake" in st["stopped"]["resume"]
    assert not (ws / "fixture-result.txt").exists()
    assert not list((ws / "jobs").glob("*.json"))


def test_native_fixture_dispatch_keeps_sandbox_intact(paper_project, grant_credentials, child_env):
    from scieflow.core import sandbox

    ws, prompt = fixture_dispatch(paper_project, grant_credentials)
    require_single_workspace_ancestor(ws)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scieflow.cli", "agent", "run",
         "--role", "research.draft-authors", "fixture", str(prompt),
         str(ws / "logs" / "ping.out")], cwd=paper_project.root,
        capture_output=True, text=True, timeout=30, env=child_env)
    if result.returncode == 77:
        assert "sandbox" in result.stderr
        assert not (ws / "fixture-result.txt").exists()
        pytest.skip("native sandbox refused this environment: " + result.stderr.strip())
    assert result.returncode == 0, result.stderr
    assert (ws / "fixture-result.txt").read_text() == "fixture process only\n"
    assert sandbox.available()
    assert not (ws / "budget.yml").exists()
