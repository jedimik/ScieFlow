"""Agent boundary (M-B Part A 5.3): `safe` only when the policy pins the final templates."""

import hashlib
import json

import yaml

from scieflow.core.data import policy
from scieflow.core.data.policy import boundary, pin_sha

EP = "https://llm.example.org/v1"
SAFE_CFG = {"cmd": "{python} -m safe --endpoint {endpoint} {prompt}",
            "enabled": True, "timeout_min": 1}


def make_policy(root, cfg=SAFE_CFG, agent="safe-a", sha=None):
    (root / "config").mkdir(exist_ok=True)
    text = {
        "version": 1,
        "safe_endpoints": [{"url": EP, "reason": "test"}],
        "safe_agents": {agent: {"endpoint": EP, "reason": "test",
                                "templates_sha256": sha or pin_sha(cfg)}},
    }
    (root / "config" / "data-policy.yml").write_text(yaml.safe_dump(text))
    return policy.load(root)


def test_listed_pinned_agent_with_endpoint_is_safe(tmp_path):
    p = make_policy(tmp_path)
    b = boundary(p, "safe-a", SAFE_CFG, root=tmp_path, writable_areas=[])
    assert (b.kind, b.endpoint) == ("safe", EP)


def test_an_unlisted_agent_is_cloud_not_listed(tmp_path):
    p = make_policy(tmp_path)
    b = boundary(p, "cloud-b", SAFE_CFG, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason, b.endpoint) == ("cloud", "not-listed", "")


def test_a_template_without_the_endpoint_placeholder_is_cloud(tmp_path):
    cfg = {"cmd": "{python} -m safe {prompt}"}
    p = make_policy(tmp_path, cfg=cfg)
    b = boundary(p, "safe-a", cfg, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason) == ("cloud", "no-endpoint-placeholder")


def test_a_resume_template_without_the_placeholder_is_cloud(tmp_path):
    cfg = {**SAFE_CFG, "resume_cmd": "{python} -m safe --resume {session}"}
    p = make_policy(tmp_path, cfg=cfg)
    b = boundary(p, "safe-a", cfg, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason) == ("cloud", "no-endpoint-placeholder")


def test_an_override_that_changes_a_template_is_cloud(tmp_path):
    p = make_policy(tmp_path)
    changed = {**SAFE_CFG, "cmd": SAFE_CFG["cmd"] + " --other-tool"}
    b = boundary(p, "safe-a", changed, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason) == ("cloud", "pin-mismatch")


def test_a_non_template_override_does_not_break_the_pin(tmp_path):
    p = make_policy(tmp_path)
    tuned = {**SAFE_CFG, "model": "other-model", "timeout_min": 9}
    b = boundary(p, "safe-a", tuned, root=tmp_path, writable_areas=[])
    assert b.kind == "safe"


def test_a_launcher_inside_the_workspace_is_cloud(tmp_path):
    cfg = {"cmd": "{root}/workspace/r1/tools/x.py --endpoint {endpoint} {prompt}"}
    p = make_policy(tmp_path, cfg=cfg)
    b = boundary(p, "safe-a", cfg, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason) == ("cloud", "launcher-writable")


def test_a_launcher_in_a_writable_grant_is_cloud_and_outside_it_is_safe(tmp_path):
    cfg = {"cmd": "{root}/config/journals/x.py --endpoint {endpoint} {prompt}"}
    p = make_policy(tmp_path, cfg=cfg)
    grant = tmp_path / "config" / "journals"
    assert boundary(p, "safe-a", cfg, root=tmp_path,
                    writable_areas=[grant]).reason == "launcher-writable"
    assert boundary(p, "safe-a", cfg, root=tmp_path, writable_areas=[]).kind == "safe"


def test_an_unparseable_template_still_checks_the_workspace_fail_closed(tmp_path):
    cfg = {"cmd": "'{root}/workspace/r1/x.py --endpoint {endpoint} {prompt}"}
    p = make_policy(tmp_path, cfg=cfg)
    b = boundary(p, "safe-a", cfg, root=tmp_path, writable_areas=[])
    assert (b.kind, b.reason) == ("cloud", "launcher-writable")


def test_pin_covers_the_templates_in_canonical_json(tmp_path):
    expected = hashlib.sha256(json.dumps(
        {"cmd": SAFE_CFG["cmd"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert pin_sha({"cmd": SAFE_CFG["cmd"], "timeout_min": 1}) == expected
    assert len(expected) == 64
