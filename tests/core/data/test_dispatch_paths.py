"""The dispatch refusal on the three paths (M-B Part A 5.4, 5.11; exit 78, job.refused reason data-level).

The CLI (`agent_run.main`), the service (`service.dispatch_agent`) and the conversation
(`service.say`) must all refuse before any job exists, and each must record the same event.
Every agent here is a synthetic `python -c` command; nothing reaches a real service.
"""

import sys

import pytest
import yaml

from scieflow.core import agent_run, events, jobs, service
from scieflow.core.data import policy
from scieflow.core.project import Project
from scieflow.core.service import ServiceError
from scieflow.core.turn_input import accept_human_submission

EP = "https://llm.example.org/v1"
PY = sys.executable
CLOUD_CMD = f'{PY} -c "print(1)" {{prompt}}'
SAFE_CMD = f'{PY} -c "import sys; print(sys.argv[1:])" --endpoint {{endpoint}} {{prompt}}'
EP_CLOUD_CMD = f'{PY} -c "print(1)" --endpoint {{endpoint}} {{prompt}}'
AGENTS = {
    "cloud-a": {"cmd": CLOUD_CMD, "enabled": True, "timeout_min": 1},
    "safe-a": {"cmd": SAFE_CMD, "enabled": True, "timeout_min": 1},
    "cloud-ep": {"cmd": EP_CLOUD_CMD, "enabled": True, "timeout_min": 1},
    "conv-a": {"cmd": CLOUD_CMD, "enabled": True, "timeout_min": 1, "family": "claude",
               "session_cmd": f'{PY} -c "print(1)" {{prompt}}',
               "resume_cmd": f'{PY} -c "print(1)" --resume {{session}} {{prompt}}'},
}


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def declare(ws, level, path="inputs/x.csv"):
    write_yaml(ws / "data.yml", {
        "version": 1, "default_level": "L0",
        "levels": [{"path": path, "level": level, "kind": "tabular", "reason": "synthetic",
                    "declared_by": "human", "declared_at": "2026-10-26T10:00:00Z"}],
        "pending_releases": [], "released": [], "imports": []})


def write_policy(root, *, enforcement="warn", extra=None):
    text = {"version": 1, "enforcement": enforcement,
            "safe_endpoints": [{"url": EP, "reason": "test"}],
            "safe_agents": {"safe-a": {"endpoint": EP, "reason": "test",
                                       "templates_sha256": policy.pin_sha(AGENTS["safe-a"])}}}
    text.update(extra or {})
    write_yaml(root / "config" / "data-policy.yml", text)


@pytest.fixture
def lab(project, monkeypatch):
    """The synthetic project with a fixed registry, run r1 and a prompt inside it."""
    write_yaml(project / "config" / "agents.yml", {"agents": AGENTS})
    ws = project / "workspace" / "r1"
    write_yaml(ws / "config.yml", {"slug": "r1", "approval": "autonomous", "assignments": {
        "loop.experiment": ["cloud-a", "safe-a", "cloud-ep"]}})
    prompt = ws / "logs" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("synthetic prompt, no real data\n")
    monkeypatch.setattr(Project, "discover", lambda *a, **k: Project(project))
    monkeypatch.chdir(project)
    return project


def refused_events(ws):
    return [(e["data"]["reason"], e["data"]["rule"], e["data"]["level"], e["data"]["agent"],
             e["data"]["boundary"]) for e in events.read(ws, types=("job.refused",))]


def run_cli(root, args):
    ws = root / "workspace" / "r1"
    return ws, [*args, str(ws / "logs" / "prompt.md"), str(ws / "logs" / "t.md"),
                "--role", "loop.experiment"]


# --- CLI path -----------------------------------------------------------------------------


def test_cli_refuses_a_cloud_agent_in_an_l2_run_with_exit_78(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    _, argv = run_cli(lab, ["cloud-a", "--no-sandbox"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert "cloud-a: data protection refused (l2-cloud)" in (ws / "logs" / "t.md").read_text()
    assert refused_events(ws) == [("data-level", "l2-cloud", "L2", "cloud-a", "cloud")]
    assert jobs.list_jobs(Project(lab), ws) == []


def test_cli_refusal_holds_with_the_sandbox_on_too(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    _, argv = run_cli(lab, ["cloud-a"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert refused_events(ws) == [("data-level", "l2-cloud", "L2", "cloud-a", "cloud")]


def test_cli_runs_a_pinned_safe_agent_in_an_l2_run_with_its_endpoint(lab, capsys):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    write_policy(lab)
    _, argv = run_cli(lab, ["safe-a", "--no-sandbox"])
    agent_run.main(argv)
    assert EP in (ws / "logs" / "t.md").read_text()
    assert refused_events(ws) == []
    resolved = events.read(ws, types=("data.level_resolved",))
    assert [(e["data"]["level"], e["data"]["boundary"]) for e in resolved] == [("L2", "safe")]


def test_cli_refuses_an_endpoint_placeholder_on_a_cloud_agent(lab):
    ws = lab / "workspace" / "r1"
    _, argv = run_cli(lab, ["cloud-ep", "--no-sandbox"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert refused_events(ws) == [("data-level", "endpoint-without-safe-entry", "L0",
                                   "cloud-ep", "cloud")]


def test_cli_refuses_an_unsandboxed_cloud_dispatch_while_another_run_is_l2(lab):
    ws = lab / "workspace" / "r1"
    declare(lab / "workspace" / "r2", "L2")
    _, argv = run_cli(lab, ["cloud-a", "--no-sandbox"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert refused_events(ws) == [("data-level", "l2-unsandboxed", "L0", "cloud-a", "cloud")]


def test_cli_refuses_every_dispatch_on_a_malformed_policy(lab):
    ws = lab / "workspace" / "r1"
    write_yaml(lab / "config" / "data-policy.yml", {"version": 2})
    _, argv = run_cli(lab, ["cloud-a", "--no-sandbox"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert "data-policy.yml" in (ws / "logs" / "t.md").read_text()
    assert refused_events(ws) == [("data-level", "policy-invalid", "L0", "cloud-a", "cloud")]


def test_cli_warns_on_an_l1_run_and_refuse_mode_blocks_it(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L1")
    _, argv = run_cli(lab, ["cloud-a", "--no-sandbox"])
    agent_run.main(argv)
    warnings = events.read(ws, types=("data.warning",))
    assert [e["data"]["rule"] for e in warnings] == ["l1-external-sharing"]
    write_policy(lab, enforcement="refuse")
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert refused_events(ws)[-1][1] == "l1-external-sharing"


# --- service path -------------------------------------------------------------------------


def test_service_dispatch_refuses_a_cloud_agent_in_an_l2_run(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    prompt = ws / "logs" / "prompt.md"
    with pytest.raises(ServiceError) as exc:
        service.dispatch_agent(Project(lab), "cloud-a", prompt, ws / "logs" / "t.md",
                               role="loop.experiment")
    assert "l2-cloud" in str(exc.value)
    assert refused_events(ws) == [("data-level", "l2-cloud", "L2", "cloud-a", "cloud")]
    assert jobs.list_jobs(Project(lab), ws) == []


# --- conversation path --------------------------------------------------------------------


def test_say_refuses_a_cloud_turn_in_an_l2_run_and_starts_no_job(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    service.set_conversation_agent(Project(lab), "r1", "conv-a")
    with pytest.raises(ServiceError) as exc:
        service.say(Project(lab), "r1", "hello there")
    assert "l2-cloud" in str(exc.value)
    assert refused_events(ws) == [("data-level", "l2-cloud", "L2", "conv-a", "cloud")]
    assert jobs.list_jobs(Project(lab), ws) == []


# --- M-B t2 fix (review r1 F1, F2): run overrides, the sandboxed safe paths ----------------

CHANGED_CMD = f'{PY} -c "print(2)" --endpoint {{endpoint}} {{prompt}}'
CHANGED_SESSION = f'{PY} -c "print(2)" --endpoint {{endpoint}} {{prompt}}'
CONV_SAFE = {
    "cmd": SAFE_CMD, "family": "claude", "enabled": True, "timeout_min": 1,
    "session_cmd": f'{PY} -c "print(1)" --endpoint {{endpoint}} {{prompt}}',
    "resume_cmd": f'{PY} -c "print(1)" --endpoint {{endpoint}} --resume {{session}} {{prompt}}',
}


def set_run_overrides(ws, overrides):
    data = yaml.safe_load((ws / "config.yml").read_text())
    data["agent_overrides"] = overrides
    write_yaml(ws / "config.yml", data)


def pin_policy(root, agents, *, enforcement="warn"):
    """A policy that pins the final templates of each named agent to the synthetic endpoint."""
    write_yaml(root / "config" / "data-policy.yml", {
        "version": 1, "enforcement": enforcement,
        "safe_endpoints": [{"url": EP, "reason": "test"}],
        "safe_agents": {name: {"endpoint": EP, "reason": "test",
                               "templates_sha256": policy.pin_sha(cfg)}
                        for name, cfg in agents.items()},
    })


def add_conversation_agent(root):
    write_yaml(root / "config" / "agents.yml", {"agents": {**AGENTS, "safe-c": CONV_SAFE}})
    pin_policy(root, {"safe-a": AGENTS["safe-a"], "safe-c": CONV_SAFE})


def give_credentials(root, agents):
    """Fake 0600 token in the suite's credentials dir and a sandbox.yml entry per agent."""
    from scieflow.core import sandbox

    token = sandbox.credentials_dir() / "fixture.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("fake-token-fixture\n")
    token.chmod(0o600)
    entry = {"mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN", "file": "fixture.token",
             "reason": "fake fixture token"}
    write_yaml(root / "config" / "sandbox.yml",
               {"credentials": {agent: dict(entry) for agent in agents}})


def test_cli_refuses_a_pinned_safe_agent_whose_run_override_changes_its_command(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    pin_policy(lab, {"safe-a": AGENTS["safe-a"]})
    set_run_overrides(ws, {"safe-a": {"cmd": CHANGED_CMD}})
    _, argv = run_cli(lab, ["safe-a", "--no-sandbox"])
    with pytest.raises(SystemExit) as exc:
        agent_run.main(argv)
    assert exc.value.code == 78
    assert "pin-mismatch" in (ws / "logs" / "t.md").read_text()
    # 5.4 order: an agent that still names {endpoint} but is cloud is refused by its own rule
    # before the L2 rule; the message carries the pin reason.
    assert refused_events(ws) == [("data-level", "endpoint-without-safe-entry", "L2", "safe-a",
                                   "cloud")]
    assert jobs.list_jobs(Project(lab), ws) == []


def test_say_refuses_a_pinned_safe_turn_whose_run_override_changes_its_session_template(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    add_conversation_agent(lab)
    service.set_conversation_agent(Project(lab), "r1", "safe-c")
    set_run_overrides(ws, {"safe-c": {"session_cmd": CHANGED_SESSION}})
    with pytest.raises(ServiceError) as exc:
        service.say(Project(lab), "r1", "hello there")
    assert "pin-mismatch" in str(exc.value)
    assert refused_events(ws) == [("data-level", "endpoint-without-safe-entry", "L2", "safe-c",
                                   "cloud")]
    assert jobs.list_jobs(Project(lab), ws) == []


def test_a_pinned_safe_turn_in_an_l2_run_is_resolved_with_its_endpoint(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    add_conversation_agent(lab)
    give_credentials(lab, ["safe-a", "safe-c"])
    resolved = agent_run.resolve_turn(Project(lab), "safe-c", run_dir=ws, session=None)
    assert (resolved.data.allowed, resolved.data.boundary) == (True, "safe")
    prepared = agent_run.prepare_turn(resolved, accept_human_submission("hello there"))
    assert EP in prepared.argv
    assert refused_events(ws) == []


def test_cli_runs_a_pinned_safe_agent_sandboxed_in_an_l2_run(lab):
    ws = lab / "workspace" / "r1"
    declare(ws, "L2")
    pin_policy(lab, {"safe-a": AGENTS["safe-a"]})
    give_credentials(lab, ["safe-a"])
    _, argv = run_cli(lab, ["safe-a"])
    agent_run.main(argv)
    assert EP in (ws / "logs" / "t.md").read_text()
    assert refused_events(ws) == []
    resolved = events.read(ws, types=("data.level_resolved",))
    assert [(e["data"]["level"], e["data"]["boundary"]) for e in resolved] == [("L2", "safe")]
