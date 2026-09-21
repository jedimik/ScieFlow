"""Two miniature fake homes: one to back up from, one to restore into.

Home A belongs to `alice`, home B to `bob`, so every test exercises the
cross-machine case (different user, different absolute paths) rather than the
easy same-path one.
"""

import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

# The chats module needs the `chats` extra for the manifest schema check;
# questionary is only required for the interactive picker.
_HAS_SCHEMA = importlib.util.find_spec("jsonschema") is not None
if not _HAS_SCHEMA:
    collect_ignore_glob = ["test_*.py"]

CLAUDE_CHAT = "11111111-1111-4111-8111-111111111111"
CODEX_CHAT = "22222222-2222-4222-8222-222222222222"
AGY_CHAT = "33333333-3333-4333-8333-333333333333"
GEMINI_CHAT = "44444444-4444-4444-8444-444444444444"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _jsonl(rows) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


# -- individual stores ---------------------------------------------------
def build_claude(home: Path, project: str) -> None:
    root = home / ".claude"
    slug = project.replace("/", "-")
    _write(
        root / "projects" / slug / f"{CLAUDE_CHAT}.jsonl",
        _jsonl(
            [
                {
                    "type": "user",
                    "cwd": project,
                    "sessionId": CLAUDE_CHAT,
                    "timestamp": "2026-09-01T10:00:00.000Z",
                    "uuid": "u1",
                    "message": {"role": "user", "content": "run the pdf skill"},
                },
                {
                    "type": "assistant",
                    "cwd": project,
                    "sessionId": CLAUDE_CHAT,
                    "timestamp": "2026-09-01T10:00:05.000Z",
                    "uuid": "u2",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Skill", "input": {"skill": "demo-skill"}},
                            {"type": "tool_use", "name": "mcp__demo__query", "input": {}},
                        ],
                    },
                },
                {"type": "ai-title", "aiTitle": "PDF work", "uuid": "u3"},
            ]
        ),
    )
    _write(
        root / "history.jsonl",
        _jsonl([{"display": "run the pdf skill", "sessionId": CLAUDE_CHAT,
                 "project": project, "timestamp": 1756720000}]),
    )
    _write(root / "settings.json", json.dumps(
        {"model": "opus", "enabledPlugins": {"demo@market": True},
         "env": {"SECRET_TOKEN": "sk-should-not-travel"}}))
    _write(root / ".credentials.json", json.dumps({"oauth": "top-secret"}))
    _write(root / "skills" / "demo-skill" / "SKILL.md", "---\nname: demo-skill\n---\nbody\n")
    _write(root / "plugins" / "installed_plugins.json", json.dumps(
        {"version": 1, "plugins": {"demo@market": [
            {"scope": "user", "installPath": str(root / "plugins" / "cache" / "demo"),
             "version": "1.2.3"}]}}))
    _write(root / "plugins" / "known_marketplaces.json", json.dumps(
        {"market": {"source": {"source": "github", "repo": "acme/market"}}}))
    _write(root / "plugins" / "cache" / "market" / "demo" / "1.2.3" / "skills"
           / "bundled-skill" / "SKILL.md", "---\nname: bundled-skill\n---\n")
    _write(
        home / ".claude.json",
        json.dumps(
            {
                "userID": "should-not-travel",
                "machineID": "should-not-travel",
                "cachedChangelog": "x",
                "projects": {
                    project: {
                        "lastSessionId": CLAUDE_CHAT,
                        "mcpServers": {"demo": {"command": "demo", "env": {"TOKEN": "sk-live"}}},
                    }
                },
                "githubRepoPaths": {"acme/thing": project},
            }
        ),
    )


def build_codex(home: Path, project: str, *, with_rows: bool = True) -> None:
    root = home / ".codex"
    rollout = root / "sessions" / "2026" / "09" / "01" / f"rollout-2026-09-01T10-00-00-{CODEX_CHAT}.jsonl"
    _write(
        rollout,
        _jsonl(
            [
                {"type": "session_meta", "ordinal": 0, "timestamp": 1756720000,
                 "payload": {"id": CODEX_CHAT, "cwd": project, "cli_version": "1.0"}},
                {"type": "event", "ordinal": 1, "timestamp": 1756720010,
                 "payload": {"text": "<command-name>/demo-command</command-name>"}},
            ]
        ),
    )
    _write(root / "config.toml", (
        'model = "gpt-6"\n\n'
        f'[projects."{project}"]\ntrust_level = "trusted"\n\n'
        '[projects."/somewhere/else"]\ntrust_level = "trusted"\n'
    ))
    _write(root / "history.jsonl",
           _jsonl([{"session_id": CODEX_CHAT, "text": "hello", "ts": 1756720000}]))
    state = sqlite3.connect(root / "state_5.sqlite")
    state.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,"
        " created_at INTEGER, updated_at INTEGER, source TEXT, model_provider TEXT,"
        " cwd TEXT, title TEXT, sandbox_policy TEXT, approval_mode TEXT,"
        " created_at_ms INTEGER, updated_at_ms INTEGER, preview TEXT,"
        " first_user_message TEXT, project_id TEXT, archived INTEGER DEFAULT 0)"
    )
    if with_rows:
        state.execute(
            "INSERT INTO threads (id, rollout_path, created_at, updated_at, source,"
            " model_provider, cwd, title, sandbox_policy, approval_mode, created_at_ms,"
            " updated_at_ms, preview, first_user_message, project_id, archived)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (CODEX_CHAT, str(rollout), 1756720000, 1756720100, "cli", "openai", project,
             "Demo thread", "workspace-write", "on-request", 1756720000000,
             1756720100000, "hello", "hello", None),
        )
    state.commit()
    state.close()
    history = sqlite3.connect(root / "thread_history_1.sqlite")
    history.execute(
        "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT,"
        " rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT,"
        " PRIMARY KEY (thread_id, turn_id, item_id))"
    )
    if with_rows:
        history.execute(
            "INSERT INTO thread_items VALUES (?,?,?,?,?,?)",
            (CODEX_CHAT, "t1", "i1", 1, 1756720010000,
             json.dumps({"cwd": project, "text": "hi"})),
        )
    history.commit()
    history.close()


def build_agy(home: Path, project: str, *, with_rows: bool = True) -> None:
    root = home / ".gemini" / "antigravity-cli"
    root.mkdir(parents=True, exist_ok=True)
    conv = sqlite3.connect(root / "conversations" / f"{AGY_CHAT}.db") if False else None
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    conv = sqlite3.connect(root / "conversations" / f"{AGY_CHAT}.db")
    conv.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_payload BLOB)")
    conv.execute("INSERT INTO steps VALUES (0, ?)",
                 (b"\x08\x01demo-skill was invoked here\x10\x02",))
    conv.commit()
    conv.close()
    summaries = sqlite3.connect(root / "conversation_summaries.db")
    summaries.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY,"
        " title TEXT, preview TEXT, step_count INTEGER, last_modified_time TEXT,"
        " workspace_uris TEXT, app_data_dir TEXT, last_user_input_time TEXT)"
    )
    if with_rows:
        summaries.execute(
            "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?)",
            (AGY_CHAT, "Agy demo", "preview", 7, "2026-09-01T10:00:00+00:00",
             json.dumps([f"file://{project}"]), str(root), "2026-09-01T09:00:00+00:00"),
        )
    summaries.commit()
    summaries.close()
    _write(root / "history.jsonl",
           _jsonl([{"display": "agy prompt", "timestamp": 1756720000,
                    "type": "user", "workspace": project}]))
    _write(root / "settings.json", json.dumps({"trustedWorkspaces": [project]}))
    _write(root / "antigravity-oauth-token", "top-secret")


def build_gemini(home: Path, project: str) -> None:
    import hashlib

    root = home / ".gemini"
    slug = Path(project).name.lower()
    _write(root / "projects.json", json.dumps({"projects": {project: slug}}))
    _write(
        root / "tmp" / slug / "chats" / f"session-2026-09-01-{GEMINI_CHAT[:8]}.jsonl",
        _jsonl(
            [
                {"sessionId": GEMINI_CHAT,
                 "projectHash": hashlib.sha256(project.encode()).hexdigest(),
                 "startTime": "2026-09-01T10:00:00.000Z",
                 "lastUpdated": "2026-09-01T10:05:00.000Z", "kind": "main"},
                {"id": "m1", "timestamp": "2026-09-01T10:00:01.000Z", "type": "user",
                 "content": [{"text": "hello gemini"}]},
            ]
        ),
    )
    _write(root / "settings.json", json.dumps({"ide": {}}))
    _write(root / "oauth_creds.json", json.dumps({"token": "top-secret"}))


def build_home(home: Path, project: str, *, populated: bool = True) -> Path:
    build_claude(home, project) if populated else (home / ".claude" / "projects").mkdir(
        parents=True, exist_ok=True
    )
    build_codex(home, project, with_rows=populated)
    build_agy(home, project, with_rows=populated)
    if populated:
        build_gemini(home, project)
    else:
        (home / ".gemini" / "tmp").mkdir(parents=True, exist_ok=True)
        _write(home / ".gemini" / "projects.json", json.dumps({"projects": {}}))
    return home


def write_config(path: Path, home: Path, bundle_dir: Path, **overrides) -> Path:
    import yaml

    doc = {
        "stores": {
            "claude": {"root": str(home / ".claude"), "enabled": True},
            "codex": {"root": str(home / ".codex"), "enabled": True},
            "agy": {"root": str(home / ".gemini" / "antigravity-cli"), "enabled": True},
            "gemini": {"root": str(home / ".gemini"), "enabled": True},
        },
        "bundle_dir": str(bundle_dir),
        "encryption": {"method": "age", "recipient": None},
        "exclude_extra": [],
        "max_bundle_gb": 5,
    }
    doc.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc))
    return path


# -- fixtures ------------------------------------------------------------
@pytest.fixture
def source_home(tmp_path, monkeypatch):
    """Alice's machine, fully populated."""
    home = tmp_path / "home" / "alice"
    project = str(home / "proj")
    Path(project).mkdir(parents=True, exist_ok=True)
    build_home(home, project)
    monkeypatch.setenv("SCIEFLOW_CHATS_HOME", str(home))
    return home, project


@pytest.fixture
def target_home(tmp_path):
    """Bob's machine: same tools installed, no chats yet."""
    home = tmp_path / "home" / "bob"
    project = str(home / "proj")
    Path(project).mkdir(parents=True, exist_ok=True)
    build_home(home, project, populated=False)
    return home, project


@pytest.fixture
def source_config(tmp_path, source_home):
    home, _ = source_home
    return write_config(tmp_path / "cfg" / "chats.yml", home, tmp_path / "bundles")


@pytest.fixture
def target_config(tmp_path, target_home):
    home, _ = target_home
    return write_config(tmp_path / "cfg" / "chats-target.yml", home, tmp_path / "bundles")


@pytest.fixture
def chat_id_map():
    return {
        "claude": CLAUDE_CHAT,
        "codex": CODEX_CHAT,
        "agy": AGY_CHAT,
        "gemini": GEMINI_CHAT,
    }


@pytest.fixture
def new_uuid():
    return lambda: str(uuid.uuid4())
