"""The cross-machine round trip: alice's bundle restored onto bob's machine."""

import json
import sqlite3

import pytest
from click.testing import CliRunner

from scieflow.chats.cli import chats
from tests.chats.conftest import AGY_CHAT, CLAUDE_CHAT, CODEX_CHAT


def run(args):
    return CliRunner().invoke(chats, args)


@pytest.fixture
def bundle(tmp_path, source_config, source_home):
    out = tmp_path / "bundle.zip"
    result = run(["backup", "--config", str(source_config), "--all",
                  "--no-encrypt", "--yes", "--out", str(out)])
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture
def restore_env(monkeypatch, target_home, bundle):
    """Point the module at bob's home so the default path mapping is alice->bob."""
    home, _ = target_home
    monkeypatch.setenv("SCIEFLOW_CHATS_HOME", str(home))
    monkeypatch.setattr("scieflow.chats.stores.base.running_tools", lambda *a, **k: [])
    return home


def test_dry_run_writes_nothing(tmp_path, restore_env, target_config, bundle):
    home = restore_env
    before = sorted(p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file())
    result = run(["restore", "--config", str(target_config), str(bundle)])
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    after = sorted(p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file())
    assert before == after
    assert not list((home / ".claude" / "projects").glob("*/*.jsonl"))


def test_apply_rewrites_claude_slug_and_cwd(restore_env, target_config, target_home, bundle):
    home = restore_env
    _, project = target_home
    result = run(["restore", "--config", str(target_config), str(bundle),
                  "--apply", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output

    expected_slug = project.replace("/", "-")
    transcript = home / ".claude" / "projects" / expected_slug / f"{CLAUDE_CHAT}.jsonl"
    assert transcript.exists(), sorted(p.name for p in (home / ".claude" / "projects").iterdir())
    text = transcript.read_text()
    assert "/home/alice" not in text and "alice" not in text
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert all(r.get("cwd", project) == project for r in rows)


def test_apply_merges_claude_global_json(restore_env, target_config, target_home, bundle):
    home = restore_env
    _, project = target_home
    run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    doc = json.loads((home / ".claude.json").read_text())
    assert project in doc["projects"]
    assert "alice" not in json.dumps(doc)


def test_apply_upserts_codex_rows_and_rewrites_rollout(
    restore_env, target_config, target_home, bundle
):
    home = restore_env
    _, project = target_home
    run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])

    conn = sqlite3.connect(home / ".codex" / "state_5.sqlite")
    row = conn.execute(
        "SELECT cwd, rollout_path FROM threads WHERE id = ?", (CODEX_CHAT,)
    ).fetchone()
    conn.close()
    assert row is not None, "codex thread row not restored"
    cwd, rollout_path = row
    assert cwd == project
    assert "alice" not in rollout_path
    assert (home / rollout_path).exists() or __import__("pathlib").Path(rollout_path).exists()

    history = sqlite3.connect(home / ".codex" / "thread_history_1.sqlite")
    count = history.execute(
        "SELECT COUNT(*) FROM thread_items WHERE thread_id = ?", (CODEX_CHAT,)
    ).fetchone()[0]
    history.close()
    assert count == 1


def test_apply_appends_codex_project_block(restore_env, target_config, target_home, bundle):
    home = restore_env
    _, project = target_home
    run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    config = (home / ".codex" / "config.toml").read_text()
    assert f'[projects."{project}"]' in config
    assert "alice" not in config


def test_apply_upserts_agy_summary_with_new_workspace(
    restore_env, target_config, target_home, bundle
):
    home = restore_env
    _, project = target_home
    run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    root = home / ".gemini" / "antigravity-cli"
    assert (root / "conversations" / f"{AGY_CHAT}.db").exists()
    conn = sqlite3.connect(root / "conversation_summaries.db")
    uris = conn.execute(
        "SELECT workspace_uris FROM conversation_summaries WHERE conversation_id = ?",
        (AGY_CHAT,),
    ).fetchone()
    conn.close()
    assert uris is not None
    assert json.loads(uris[0]) == [f"file://{project}"]


def test_apply_recomputes_gemini_project_hash(restore_env, target_config, target_home, bundle):
    import hashlib

    home = restore_env
    _, project = target_home
    run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    slug = __import__("pathlib").Path(project).name.lower()
    chats_dir = home / ".gemini" / "tmp" / slug / "chats"
    files = list(chats_dir.glob("*.jsonl"))
    assert files, sorted(p.name for p in (home / ".gemini" / "tmp").iterdir())
    header = json.loads(files[0].read_text().splitlines()[0])
    assert header["projectHash"] == hashlib.sha256(project.encode()).hexdigest()
    mapping = json.loads((home / ".gemini" / "projects.json").read_text())["projects"]
    assert project in mapping


def test_existing_chat_is_not_clobbered(restore_env, target_config, target_home, bundle):
    home = restore_env
    _, project = target_home
    slug = project.replace("/", "-")
    existing = home / ".claude" / "projects" / slug / f"{CLAUDE_CHAT}.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("MINE\n")
    result = run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    assert result.exit_code == 0, result.output
    assert existing.read_text() == "MINE\n"
    assert "skip-exists" in result.output


def test_overwrite_replaces_it(restore_env, target_config, target_home, bundle):
    home = restore_env
    _, project = target_home
    slug = project.replace("/", "-")
    existing = home / ".claude" / "projects" / slug / f"{CLAUDE_CHAT}.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("MINE\n")
    run(["restore", "--config", str(target_config), str(bundle),
         "--apply", "--overwrite", "--yes"])
    assert existing.read_text() != "MINE\n"


def test_explicit_map_overrides(restore_env, target_config, target_home, bundle):
    home = restore_env
    elsewhere = str(home / "elsewhere")
    result = run(["restore", "--config", str(target_config), str(bundle),
                  "--map", f"{home}/proj={elsewhere}"])
    assert result.exit_code == 0, result.output
    assert elsewhere in result.output


def test_refuses_while_a_target_cli_is_running(monkeypatch, target_config, bundle, target_home):
    monkeypatch.setenv("SCIEFLOW_CHATS_HOME", str(target_home[0]))
    monkeypatch.setattr("scieflow.chats.stores.base.running_tools", lambda *a, **k: ["codex"])
    result = run(["restore", "--config", str(target_config), str(bundle), "--apply", "--yes"])
    assert result.exit_code != 0
    assert "still running" in result.output


def test_gemini_slug_matches_the_cli_rule():
    from scieflow.chats.stores.gemini import _slug_for

    assert _slug_for("/home/u/Github/research/SoftwareX_SegSnake") == "softwarex-segsnake"
    assert _slug_for("/home/u/Github/SegSnake") == "segsnake"
    assert _slug_for("/mnt/c/Users/krajc") == "krajc"
