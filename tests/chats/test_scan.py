from scieflow.chats.config import load_config
from scieflow.chats.discovery import discover


def test_discovers_one_chat_per_store(source_config, source_home):
    _, project = source_home
    refs = discover(load_config(source_config))
    by_tool = {r.tool: r for r in refs}
    assert set(by_tool) == {"claude", "codex", "agy", "gemini"}
    for ref in refs:
        assert ref.project_path == project, ref.tool
        assert ref.files or ref.tool == "codex"


def test_project_filter_and_since(source_config, source_home):
    config = load_config(source_config)
    assert discover(config, projects=("proj",))
    assert discover(config, projects=("nothing-like-this",)) == []


def test_credentials_are_never_collected(source_config, source_home):
    from scieflow.chats.discovery import adapters

    config = load_config(source_config)
    refs = discover(config)
    members = []
    for adapter in adapters(config):
        picked = [r for r in refs if r.tool == adapter.tool]
        members += [s.member for s in adapter.collect(picked)]
        members += [s.member for s in adapter.sidecars(picked)]
    joined = " ".join(members)
    for leak in (".credentials.json", "auth.json", "oauth_creds.json",
                 "antigravity-oauth-token"):
        assert leak not in joined


def test_settings_env_is_stripped(source_config, source_home):
    import json

    from scieflow.chats.discovery import adapters

    config = load_config(source_config)
    refs = discover(config, tools=("claude",))
    adapter = next(a for a in adapters(config, ("claude",)))
    settings = next(
        s for s in adapter.sidecars(refs) if s.member.endswith("settings.json")
    )
    assert "env" not in json.loads(settings.data)


def test_global_json_is_filtered_and_mcp_env_redacted(source_config, source_home):
    import json

    from scieflow.chats.discovery import adapters

    config = load_config(source_config)
    refs = discover(config, tools=("claude",))
    adapter = next(a for a in adapters(config, ("claude",)))
    spec = next(s for s in adapter.sidecars(refs) if "claude.json.filtered" in s.member)
    doc = json.loads(spec.data)
    assert set(doc) <= {"projects", "githubRepoPaths"}
    assert "userID" not in doc and "machineID" not in doc
    project = next(iter(doc["projects"].values()))
    assert project["mcpServers"]["demo"]["env"]["TOKEN"] == "<redacted>"
