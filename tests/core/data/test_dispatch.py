"""The dispatch decision (M-B Part A 5.4): rules, sandbox inputs, warnings, no writes (t2).

Every value is synthetic. No test calls a real service or launches an agent.
"""

import json

import yaml

from scieflow.core import events, gates
from scieflow.core.data import policy
from scieflow.core.data.dispatch import decide
from scieflow.core.project import Project

EP = "https://llm.example.org/v1"
SAFE_CFG = {"cmd": "{python} -m safe --endpoint {endpoint} {prompt}", "enabled": True,
            "timeout_min": 1}
CLOUD_CFG = {"cmd": "python3 -m stub {prompt}", "enabled": True, "timeout_min": 1}


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def declare(ws, level, path="inputs/x.csv"):
    write_yaml(ws / "data.yml", {
        "version": 1, "default_level": "L0",
        "levels": [{"path": path, "level": level, "kind": "tabular", "reason": "synthetic",
                    "declared_by": "human", "declared_at": "2026-10-26T10:00:00Z"}],
        "pending_releases": [], "released": [], "imports": []})


def safe_policy(root, *, enforcement="warn", cfg=SAFE_CFG, extra=None):
    text = {"version": 1, "enforcement": enforcement,
            "safe_endpoints": [{"url": EP, "reason": "test"}],
            "safe_agents": {"safe-a": {"endpoint": EP, "reason": "test",
                                       "templates_sha256": policy.pin_sha(cfg)}}}
    text.update(extra or {})
    write_yaml(root / "config" / "data-policy.yml", text)


def decide_for(root, agent, cfg, *, sandboxed=True, run="r1", use_endpoint=None):
    project = Project(root)
    run_dir = root / "workspace" / run if run else None
    if use_endpoint is None:
        use_endpoint = any("{endpoint}" in str(cfg.get(k, "")) for k in policy.TEMPLATE_KEYS)
    return decide(project, run_dir, agent, cfg, sandboxed=sandboxed,
                  templates_use_endpoint=use_endpoint)


def snapshot(root):
    return sorted((str(p), p.read_bytes()) for p in root.rglob("*") if p.is_file())


def test_an_undeclared_run_lets_a_cloud_agent_run(project, ws):
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert (d.allowed, d.rule, d.level, d.boundary, d.endpoint) == (
        True, None, "L0", "cloud", "")
    assert d.warnings == () and d.data_hidden == () and d.protected == ()


def test_an_l2_run_refuses_a_cloud_agent_in_every_enforcement_mode(project, ws):
    declare(ws, "L2")
    for mode in ("warn", "refuse"):
        safe_policy(project, enforcement=mode)
        d = decide_for(project, "cloud-a", CLOUD_CFG)
        assert (d.allowed, d.rule, d.level) == (False, "l2-cloud", "L2"), mode


def test_an_l2_run_allows_a_pinned_safe_agent_with_its_endpoint(project, ws):
    declare(ws, "L2")
    safe_policy(project)
    d = decide_for(project, "safe-a", SAFE_CFG)
    assert (d.allowed, d.boundary, d.endpoint) == (True, "safe", EP)
    assert d.data_hidden == ()          # a safe agent sees the run's own L2 data


def test_a_cloud_agent_with_the_endpoint_placeholder_is_refused(project, ws):
    d = decide_for(project, "cloud-a", SAFE_CFG)   # not listed, but uses {endpoint}
    assert (d.allowed, d.rule) == (False, "endpoint-without-safe-entry")


def test_an_unsandboxed_cloud_dispatch_is_refused_when_another_run_is_l2(project, ws):
    declare(project / "workspace" / "r2", "L2")
    d = decide_for(project, "cloud-a", CLOUD_CFG, sandboxed=False)
    assert (d.allowed, d.rule) == (False, "l2-unsandboxed")


def test_an_unsandboxed_cloud_dispatch_is_allowed_when_nothing_in_the_project_is_l2(project, ws):
    d = decide_for(project, "cloud-a", CLOUD_CFG, sandboxed=False)
    assert d.allowed is True


def test_an_l2_project_path_also_makes_an_unsandboxed_cloud_dispatch_refused(project, ws):
    (project / "datasets" / "rbd").mkdir(parents=True)
    safe_policy(project, extra={"project_levels": [
        {"path": "datasets/rbd", "level": "L2", "kind": "tabular", "reason": "synthetic"}]})
    d = decide_for(project, "cloud-a", CLOUD_CFG, sandboxed=False)
    assert (d.allowed, d.rule) == (False, "l2-unsandboxed")


def test_an_absent_l2_project_path_does_not_count(project, ws):
    safe_policy(project, extra={"project_levels": [
        {"path": "datasets/missing", "level": "L2", "kind": "tabular", "reason": "synthetic"}]})
    d = decide_for(project, "cloud-a", CLOUD_CFG, sandboxed=False)
    assert d.allowed is True


def test_a_malformed_policy_refuses_every_dispatch(project, ws):
    write_yaml(project / "config" / "data-policy.yml", {"version": 2})
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert (d.allowed, d.rule) == (False, "policy-invalid")
    assert d.message.startswith("config/data-policy.yml: version")


def test_an_invalid_data_file_is_l2_and_refuses_a_cloud_agent(project, ws):
    (ws / "data.yml").write_text("version: 1\nbogus: true\n")
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert (d.allowed, d.rule, d.level) == (False, "l2-cloud", "L2")


def test_an_l1_run_warns_about_external_sharing_without_a_gate(project, ws):
    declare(ws, "L1")
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert d.allowed is True
    assert [(w.rule) for w in d.warnings] == ["l1-external-sharing"]


def test_enforcement_refuse_turns_the_l1_warning_into_a_refusal(project, ws):
    declare(ws, "L1")
    safe_policy(project, enforcement="refuse")
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert (d.allowed, d.rule) == (False, "l1-external-sharing")


def _answered_gate(ws, gate_id, *, kind, answer, by):
    directory = ws / gates.GATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{gate_id}.json").write_text(json.dumps({
        "id": gate_id, "kind": kind, "state": "answered", "answer": answer,
        "answered_by": by, "options": [], "question": "synthetic"}))


def test_an_approved_external_sharing_gate_clears_the_l1_warning(project, ws):
    declare(ws, "L1")
    _answered_gate(ws, "g1", kind="external-sharing", answer="approve", by="human")
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert d.allowed is True and d.warnings == ()


def test_an_agent_answered_gate_does_not_clear_the_l1_warning(project, ws):
    declare(ws, "L1")
    _answered_gate(ws, "g1", kind="external-sharing", answer="approve", by="agent")
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert [w.rule for w in d.warnings] == ["l1-external-sharing"]


def test_a_cloud_dispatch_hides_other_l2_runs_and_l2_project_paths(project, ws):
    other = project / "workspace" / "r2"
    other.mkdir()
    declare(other, "L2")
    (project / "datasets" / "rbd").mkdir(parents=True)
    safe_policy(project, extra={"project_levels": [
        {"path": "datasets/rbd", "level": "L2", "kind": "tabular", "reason": "synthetic"}]})
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert d.allowed is True
    assert set(d.data_hidden) == {other.resolve(), (project / "datasets" / "rbd").resolve()}


def test_the_protected_paths_are_the_run_declaration_and_released_folder(project, ws):
    declare(ws, "L1")
    (ws / "released").mkdir()
    d = decide_for(project, "cloud-a", CLOUD_CFG)
    assert set(d.protected) == {(ws / "data.yml").resolve(), (ws / "released").resolve()}


def test_decide_writes_nothing(project, ws):
    declare(ws, "L1")
    safe_policy(project, enforcement="warn")
    before = snapshot(project)
    decide_for(project, "cloud-a", CLOUD_CFG)
    decide_for(project, "safe-a", SAFE_CFG)
    assert snapshot(project) == before


def test_decide_emits_no_events(project, ws):
    declare(ws, "L1")
    decide_for(project, "cloud-a", CLOUD_CFG)
    assert events.read(ws) == []
