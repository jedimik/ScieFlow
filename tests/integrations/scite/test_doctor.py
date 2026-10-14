"""scite readiness for `scieflow workspace doctor` and `scieflow research scite status`
(ticket 10, plan T1.9): configured or not, SDK group installed or not, the token in the
ScieFlow process environment (dispatches inherit it), and dispatch-harness configs that
register a scite MCP server — by name only, never a secret value."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.integrations.scite import doctor
from scieflow.integrations.scite import mcp as smcp

SECRET = "sk-scite-SECRET-value-123"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    return tmp_path


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_not_configured(root, home, monkeypatch):
    monkeypatch.setattr(smcp, "sdk_available", lambda: False)
    got = doctor.report(root, environ={}, home=home)
    assert got["configured"] is False and got["tier"] is None
    assert "config/scite.yml not found" in got["reason"]
    assert got["sdk_installed"] is False and "uv sync --group scite" in got["install_hint"]
    assert got["token_in_env"] is False and got["harness_registrations"] == []
    assert got["warnings"] == []


def test_configured_mcp_tier_warns_token_in_process_env(root, home, monkeypatch):
    monkeypatch.setattr(smcp, "sdk_available", lambda: True)
    (root / "config" / "scite.yml").write_text("max_mcp_calls_per_round: 5\n")
    got = doctor.report(root, environ={"SCITE_TOKEN": SECRET}, home=home)
    assert got["configured"] is True and got["tier"] == "mcp" and got["sdk_installed"] is True
    assert got["token_in_env"] is True
    assert any("SCITE_TOKEN" in w and "dispatch" in w for w in got["warnings"])
    assert SECRET not in json.dumps(got)


def test_token_file_policy_still_reports_a_stray_env_token(root, home, monkeypatch):
    monkeypatch.setattr(smcp, "sdk_available", lambda: True)
    tf = root / "tok"
    tf.write_text(SECRET)
    (root / "config" / "scite.yml").write_text(yaml.safe_dump({"token_file": str(tf)}))
    got = doctor.report(root, environ={"SCITE_TOKEN": SECRET}, home=home)
    assert got["tier"] == "mcp" and got["token_in_env"] is True
    assert SECRET not in json.dumps(got)


def test_invalid_config_is_reported_not_raised(root, home):
    (root / "config" / "scite.yml").write_text("allowed_tools: [delete_collection]\n")
    got = doctor.report(root, environ={}, home=home)
    assert got["configured"] is False and "allowed_tools" in got["reason"]


def test_harness_registrations_names_only(root, home):
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"scite": {"type": "http", "url": "https://api.scite.ai/mcp",
                                 "headers": {"Authorization": f"Bearer {SECRET}"}},
                       "github": {"command": "gh"}},
        "projects": {"/p": {"mcpServers": {"lit": {"url": "https://api.scite.ai/mcp"}}}},
    }))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.scite]\nurl = "https://api.scite.ai/mcp"\n'
        f'http_headers = {{ Authorization = "Bearer {SECRET}" }}\n'
        '[mcp_servers.other]\ncommand = "x"\n')
    hermes = home / ".hermes"
    (hermes / "profiles" / "rr-dev").mkdir(parents=True)
    (hermes / "config.yaml").write_text(yaml.safe_dump(
        {"mcp_servers": {"scite": {"url": "https://api.scite.ai/mcp",
                                   "headers": {"Authorization": f"Bearer {SECRET}"}}}}))
    (hermes / "profiles" / "rr-dev" / "config.yaml").write_text(yaml.safe_dump(
        {"mcp_servers": {"notes": {"command": "x"}}}))
    got = doctor.report(root, environ={}, home=home)
    regs = got["harness_registrations"]
    assert {(r["harness"], r["server"]) for r in regs} == {
        ("claude", "scite"), ("claude", "lit"), ("codex", "scite"), ("hermes", "scite")}
    assert all(set(r) == {"harness", "file", "server"} for r in regs)
    assert any("coordinator chats only" in w for w in got["warnings"])
    text = json.dumps(got)
    assert SECRET not in text and "Bearer" not in text and "api.scite.ai" not in text


def test_unreadable_harness_files_are_skipped(root, home):
    (home / ".claude.json").write_text("{not json")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text("[[[")
    got = doctor.report(root, environ={}, home=home)
    assert got["harness_registrations"] == []


def test_hermes_home_env_is_honoured(root, home, tmp_path):
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": {"scite-mcp": {}}}))
    got = doctor.report(root, environ={"HERMES_HOME": str(hh)}, home=home)
    assert [(r["harness"], r["server"]) for r in got["harness_registrations"]] == [
        ("hermes", "scite-mcp")]


def test_workspace_doctor_includes_scite(tmp_path, monkeypatch):
    from scieflow.core import workspace as wsmod

    (tmp_path / "config").mkdir()
    (tmp_path / "workspace" / "run-a").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCITE_TOKEN", raising=False)
    report = wsmod.doctor("run-a", root=tmp_path)
    assert report["scite"]["configured"] is False


def test_research_scite_status_cli(tmp_path, monkeypatch):
    from scieflow.research.cli import research

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "scite.yml").write_text("{}\n")
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SCITE_TOKEN", SECRET)
    res = CliRunner().invoke(research, ["scite", "status", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["configured"] is True and data["tier"] == "mcp"
    assert SECRET not in res.output
    res = CliRunner().invoke(research, ["scite", "status"])
    assert res.exit_code == 0 and "configured" in res.output and SECRET not in res.output


def test_project_scoped_harness_registrations(root, home):
    (root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"scite": {"type": "http", "url": "https://api.scite.ai/mcp",
                                  "headers": {"Authorization": f"Bearer {SECRET}"}}}}))
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text('[mcp_servers.lit]\nurl = "https://api.scite.ai/mcp"\n')
    got = doctor.report(root, environ={}, home=home)
    assert {(r["harness"], r["server"]) for r in got["harness_registrations"]} == {
        ("claude", "scite"), ("codex", "lit")}
    assert SECRET not in json.dumps(got)


def test_stray_default_token_is_reported_with_a_custom_token_env(root, home):
    (root / "config" / "scite.yml").write_text("token_env: MY_SCITE\n")
    got = doctor.report(root, environ={"SCITE_TOKEN": SECRET}, home=home)
    assert got["token_in_env"] is True
    assert any("SCITE_TOKEN" in w for w in got["warnings"])
    assert SECRET not in json.dumps(got)
