from scieflow.chats.config import load_config
from scieflow.chats.detect import detect, scan_secrets, scan_text
from scieflow.chats.discovery import adapters, discover


def test_scan_text_finds_skills_commands_and_mcp():
    text = (
        '{"name":"Skill","input":{"skill":"demo-skill"}}\n'
        "<command-name>/demo-command</command-name>\n"
        '{"name":"mcp__demo__query"}'
    )
    names, servers = scan_text(text)
    assert names == {"demo-skill", "demo-command"}
    assert servers == {"demo"}


def test_scan_secrets_counts_without_revealing():
    counts = scan_secrets("key sk-abcdefghijklmnopqrstuvwx and AKIAABCDEFGHIJKLMNOP")
    assert counts["openai-key"] == 1
    assert counts["aws-access-key"] == 1


def test_detect_associates_installed_skill_and_plugin(source_config, source_home):
    config = load_config(source_config)
    refs = discover(config)
    by_tool = {a.tool: a for a in adapters(config)}
    found, secrets = detect(config, refs, by_tool)
    kinds = {(a.kind, a.name) for a in found}
    assert ("skill", "demo-skill") in kinds
    assert ("mcp", "demo") in kinds
    skill = next(a for a in found if a.name == "demo-skill" and a.kind == "skill")
    assert skill.source is not None and skill.source.is_dir()
    assert any(key.startswith("claude:") for key in skill.used_by)
    assert secrets == {} or all(isinstance(v, dict) for v in secrets.values())


def test_agy_hits_are_low_confidence(source_config, source_home):
    config = load_config(source_config)
    refs = discover(config, tools=("agy",))
    by_tool = {a.tool: a for a in adapters(config, ("agy",))}
    found, _ = detect(config, refs, by_tool)
    assert all(a.confidence == "low" for a in found)
