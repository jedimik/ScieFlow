import shutil

import pytest
from click.testing import CliRunner

from scieflow.chats.cli import chats
from scieflow.cli import main as scieflow_main


def test_group_is_reachable_from_the_root_cli():
    result = CliRunner().invoke(scieflow_main, ["chats", "--help"])
    assert result.exit_code == 0, result.output
    assert "Back up and restore agent chats" in result.output


def test_missing_config_explains_how_to_create_one(tmp_path):
    result = CliRunner().invoke(chats, ["scan", "--config", str(tmp_path / "nope.yml")])
    assert result.exit_code != 0
    assert "scieflow chats init" in result.output
    assert "Traceback" not in result.output


def test_init_scaffolds_then_refuses_to_clobber(tmp_path):
    target = tmp_path / "chats.yml"
    runner = CliRunner()
    assert runner.invoke(chats, ["init", "--config", str(target)]).exit_code == 0
    assert "stores:" in target.read_text()
    second = runner.invoke(chats, ["init", "--config", str(target)])
    assert second.exit_code != 0
    assert "already exists" in second.output


def test_unknown_config_key_is_a_clean_error(tmp_path):
    cfg = tmp_path / "bad.yml"
    cfg.write_text("stores: {claude: {root: ~/.claude}}\nnonsense: 1\n")
    result = CliRunner().invoke(chats, ["scan", "--config", str(cfg)])
    assert result.exit_code != 0
    assert "unknown config key: 'nonsense'" in result.output


def test_scan_json_output(source_config, source_home):
    import json

    result = CliRunner().invoke(chats, ["scan", "--config", str(source_config), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert {r["tool"] for r in rows} == {"claude", "codex", "agy", "gemini"}


def test_plan_and_all_are_mutually_exclusive(tmp_path, source_config, source_home):
    result = CliRunner().invoke(
        chats,
        ["backup", "--config", str(source_config), "--all", "--plan", str(tmp_path / "p.yml")],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@pytest.mark.skipif(not shutil.which("age") and not shutil.which("gpg"),
                    reason="needs age or gpg")
def test_encryption_backend_is_reported(tmp_path, source_config, source_home):
    from scieflow.chats import crypto

    method, _warning = crypto.pick("age")
    assert method in ("age", "gpg")
