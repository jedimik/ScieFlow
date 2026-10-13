"""config/scite.yml policy: deny-by-default, strict keys, token by presence only."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scieflow.integrations.scite import policy as pol

REPO = Path(__file__).resolve().parents[3]
SECRET = "sk-scite-TOPSECRET-0123456789"


def write_cfg(root: Path, data) -> Path:
    path = root / "config" / "scite.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return path


# --- absent / disabled ----------------------------------------------------------


def test_absent_config_is_off_with_one_reason(tmp_path):
    p = pol.load(tmp_path, environ={})
    assert p.enabled is False
    assert p.tier == "off"
    assert isinstance(p.reason, str) and "config/scite.yml" in p.reason
    assert "\n" not in p.reason


def test_disabled_policy_refuses_every_tool(tmp_path):
    p = pol.load(tmp_path, environ={})
    with pytest.raises(pol.PolicyError) as exc:
        pol.check_tool(p, "search_literature")
    assert exc.value.code == "REFUSED_POLICY"
    assert pol.exit_code(exc.value) == 3


def test_disabled_constructor():
    p = pol.Policy.disabled("because")
    assert (p.enabled, p.tier, p.reason) == (False, "off", "because")


def test_empty_file_is_valid_with_defaults(tmp_path):
    write_cfg(tmp_path, "")
    p = pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET})
    assert p.enabled and p.tier == "mcp"
    assert p.allowed_tools == ("search_literature", "citation_graph")
    assert p.max_mcp_calls_per_round == 5
    assert p.max_mcp_calls_per_run == 20
    assert p.max_mcp_calls_per_day is None
    assert p.max_followups_per_round == 2
    assert p.contested_seeds == 10
    assert p.cache_ttl_days == {"metadata": 30, "search": 7}


def test_shipped_example_loads(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "scite.yml").write_text(
        (REPO / "config" / "scite.example.yml").read_text())
    p = pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET})
    assert p.enabled and p.max_mcp_calls_per_round == 5 and p.max_mcp_calls_per_run == 20


# --- strict validation ----------------------------------------------------------


@pytest.mark.parametrize("data, needle", [
    ({"max_mcp_calls_per_rnd": 3}, "unknown key"),
    ({"cache_ttl_days": {"metadata": 1, "other": 2}}, "unknown key"),
    ({"allowed_tools": ["search_literature", "create_collection"]}, "create_collection"),
    ({"allowed_tools": "search_literature"}, "allowed_tools"),
    ({"max_mcp_calls_per_round": 0}, "max_mcp_calls_per_round"),
    ({"max_mcp_calls_per_run": -1}, "max_mcp_calls_per_run"),
    ({"max_mcp_calls_per_day": True}, "max_mcp_calls_per_day"),
    ({"max_mcp_calls_per_round": "5"}, "max_mcp_calls_per_round"),
    ({"rest_rate_per_s": 0}, "rest_rate_per_s"),
    ({"token_env": "lower-case"}, "token_env"),
    ({"token_file": "relative/path"}, "token_file"),
    ({"token_env": "A", "token_file": "/x"}, "token_env"),
    ([1, 2], "mapping"),
])
def test_invalid_config_refused(tmp_path, data, needle):
    write_cfg(tmp_path, data)
    with pytest.raises(pol.PolicyError) as exc:
        pol.load(tmp_path, environ={})
    assert needle in str(exc.value)
    assert pol.exit_code(exc.value) == 3


def test_unparseable_yaml_refused(tmp_path):
    write_cfg(tmp_path, "a: [unclosed")
    with pytest.raises(pol.PolicyError):
        pol.load(tmp_path, environ={})


def test_tool_outside_allowlist_refused(tmp_path):
    write_cfg(tmp_path, {"allowed_tools": ["search_literature"]})
    p = pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET})
    pol.check_tool(p, "search_literature")
    with pytest.raises(pol.PolicyError) as exc:
        pol.check_tool(p, "citation_graph")
    assert "allowed_tools" in str(exc.value)
    with pytest.raises(pol.PolicyError):
        pol.check_tool(p, "delete_collection")


# --- token by presence ----------------------------------------------------------


def test_missing_token_is_public_tier_and_mcp_refused(tmp_path):
    write_cfg(tmp_path, {})
    p = pol.load(tmp_path, environ={})
    assert p.enabled and p.tier == "public"
    assert "SCITE_TOKEN" in p.reason
    with pytest.raises(pol.PolicyError) as exc:
        pol.check_tool(p, "search_literature")
    assert "SCITE_TOKEN" in str(exc.value)


def test_empty_token_env_counts_as_missing(tmp_path):
    write_cfg(tmp_path, {})
    assert pol.load(tmp_path, environ={"SCITE_TOKEN": "  "}).tier == "public"


def test_custom_token_env(tmp_path):
    write_cfg(tmp_path, {"token_env": "MY_SCITE"})
    assert pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET}).tier == "public"
    assert pol.load(tmp_path, environ={"MY_SCITE": SECRET}).tier == "mcp"


def test_token_file_existence_only(tmp_path, monkeypatch):
    tok = tmp_path / "secret" / "token"
    write_cfg(tmp_path, {"token_file": str(tok)})
    assert pol.load(tmp_path, environ={}).tier == "public"
    tok.parent.mkdir()
    tok.write_text(SECRET)
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: (reads.append(self), real(self, *a, **k))[1])
    p = pol.load(tmp_path, environ={})
    assert p.tier == "mcp"
    assert tok not in reads


def test_token_value_never_in_policy_outputs(tmp_path, capsys):
    write_cfg(tmp_path, {})
    p = pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET})
    texts = [repr(p), str(p), str(pol.describe(p)), yaml.safe_dump(pol.describe(p))]
    for exc_cfg in ({"allowed_tools": ["search_literature"]},):
        write_cfg(tmp_path, exc_cfg)
        q = pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET})
        try:
            pol.check_tool(q, "citation_graph")
        except pol.PolicyError as e:
            texts.append(str(e))
    out = capsys.readouterr()
    texts += [out.out, out.err]
    assert all(SECRET not in t for t in texts)
    assert not any(isinstance(v, str) and SECRET in v for v in vars(p).values())


def test_describe_shape(tmp_path):
    write_cfg(tmp_path, {"max_mcp_calls_per_day": 30})
    d = pol.describe(pol.load(tmp_path, environ={"SCITE_TOKEN": SECRET}))
    assert d["enabled"] is True and d["tier"] == "mcp"
    assert d["budgets"] == {"round": 5, "run": 20, "day": 30}
    assert d["token"] == {"source": "env", "name": "SCITE_TOKEN", "present": True}


# --- gitignore ------------------------------------------------------------------


def test_user_config_is_gitignored():
    r = subprocess.run(["git", "check-ignore", "-q", "config/scite.yml"], cwd=REPO)
    assert r.returncode == 0
    r = subprocess.run(["git", "check-ignore", "-q", "config/scite.example.yml"], cwd=REPO)
    assert r.returncode == 1


# --- review findings: no value echo, unreadable config -----------------------------


@pytest.mark.parametrize("data", [
    {"token_env": SECRET},
    {"token_file": SECRET},
    {"allowed_tools": ["search_literature", SECRET]},
    {SECRET: 1},
    {"cache_ttl_days": {SECRET: 1}},
    {"max_mcp_calls_per_round": SECRET},
    {"rest_rate_per_s": SECRET},
])
def test_misplaced_token_never_echoed(tmp_path, data):
    write_cfg(tmp_path, data)
    try:
        p = pol.load(tmp_path, environ={})
    except pol.PolicyError as exc:
        assert SECRET not in str(exc)
    else:
        pytest.fail(f"accepted a misplaced token: {sorted(data)}")


def test_yaml_error_does_not_quote_the_file(tmp_path):
    write_cfg(tmp_path, f'token_env: "{SECRET}"\nallowed_tools: [unclosed\n')
    with pytest.raises(pol.PolicyError) as exc:
        pol.load(tmp_path, environ={})
    assert SECRET not in str(exc.value)


def test_unreadable_config_is_a_policy_refusal(tmp_path):
    (tmp_path / "config" / "scite.yml").mkdir(parents=True)
    with pytest.raises(pol.PolicyError) as exc:
        pol.load(tmp_path, environ={})
    assert pol.exit_code(exc.value) == 3
    (tmp_path / "config" / "scite.yml").rmdir()
    (tmp_path / "config" / "scite.yml").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(pol.PolicyError):
        pol.load(tmp_path, environ={})
