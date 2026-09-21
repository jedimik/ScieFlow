"""What the selected chats referenced, and what they might leak.

Both passes read the same transcripts, so they share one streaming walk: skill
/ plugin / MCP association on the way in, secret shapes on the way past.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ChatsConfig
from .model import Artifact, ChatRef

SKILL_RE = re.compile(r'"skill"\s*:\s*"([A-Za-z0-9_:.\-]+)"')
COMMAND_RE = re.compile(r"<command-name>\s*/?([A-Za-z0-9_:.\-]+)")
MCP_RE = re.compile(r"mcp__([A-Za-z0-9_\-]+)__")

#: Shapes that are worth a warning before a transcript leaves the machine.
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{24,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
}


def scan_text(text: str) -> tuple[set[str], set[str]]:
    """(skill-or-command names, MCP server names) referenced in one transcript."""
    names = set(SKILL_RE.findall(text)) | set(COMMAND_RE.findall(text))
    return names, set(MCP_RE.findall(text))


def scan_secrets(text: str) -> dict[str, int]:
    return {
        label: len(matches)
        for label, pattern in SECRET_PATTERNS.items()
        if (matches := pattern.findall(text))
    }


# -- what is installed on this machine ---------------------------------
def installed_skills(config: ChatsConfig) -> dict[str, Path]:
    """Skill name -> directory. Symlinks are resolved so bundles stay flat."""
    found: dict[str, Path] = {}
    for tool in ("claude", "codex"):
        store = config.stores.get(tool)
        if store is None or not store.root.exists():
            continue
        base = store.root / "skills"
        if base.is_dir():
            for entry in base.iterdir():
                if entry.name.startswith("."):
                    continue
                target = entry.resolve()
                if target.is_dir():
                    found.setdefault(entry.name, target)
        for skill_md in (store.root / "plugins" / "cache").glob("*/*/*/skills/*/SKILL.md"):
            plugin = skill_md.parents[3].name
            name = skill_md.parent.name
            found.setdefault(f"{plugin}:{name}", skill_md.parent)
            found.setdefault(name, skill_md.parent)
    return found


def installed_plugins(config: ChatsConfig) -> dict[str, dict]:
    """`name@marketplace` -> the registry entry, straight from the store."""
    store = config.stores.get("claude")
    if store is None:
        return {}
    path = store.root / "plugins" / "installed_plugins.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    plugins = raw.get("plugins")
    return plugins if isinstance(plugins, dict) else {}


# -- association --------------------------------------------------------
def detect(
    config: ChatsConfig,
    refs: list[ChatRef],
    adapters_by_tool: dict,
    with_secrets: bool = True,
) -> tuple[list[Artifact], dict[str, dict[str, int]]]:
    """Artifacts the selected chats referenced, plus per-chat secret counts."""
    skills = installed_skills(config)
    plugins = installed_plugins(config)
    plugin_names = {key.split("@", 1)[0]: key for key in plugins}

    hits: dict[tuple[str, str], Artifact] = {}
    secrets: dict[str, dict[str, int]] = {}

    for ref in refs:
        adapter = adapters_by_tool.get(ref.tool)
        if adapter is None:
            continue
        low = ref.tool == "agy"  # protobuf bodies: substring evidence only
        for text in adapter.transcript_texts(ref):
            names, servers = scan_text(text)
            for name in names:
                _record_name(hits, name, ref, skills, plugins, plugin_names, low)
            for server in servers:
                _add(hits, "mcp", server, None, ref, low)
            if with_secrets:
                for label, count in scan_secrets(text).items():
                    bucket = secrets.setdefault(ref.key, {})
                    bucket[label] = bucket.get(label, 0) + count
    return sorted(hits.values(), key=lambda a: (a.kind, a.name)), secrets


def _record_name(hits, name, ref, skills, plugins, plugin_names, low) -> None:
    prefix = name.split(":", 1)[0] if ":" in name else None
    if name in skills:
        _add(hits, "skill", name, skills[name], ref, low)
    elif prefix and prefix in plugin_names:
        _add(hits, "skill", name, skills.get(name), ref, low)
    if prefix and prefix in plugin_names:
        key = plugin_names[prefix]
        entry = plugins.get(key) or [{}]
        first = entry[0] if isinstance(entry, list) and entry else {}
        artifact = _add(hits, "plugin", key, None, ref, low)
        artifact.version = first.get("version")
        artifact.marketplace = key.split("@", 1)[-1]


def _add(hits, kind, name, source, ref, low) -> Artifact:
    artifact = hits.get((kind, name))
    if artifact is None:
        artifact = Artifact(
            kind=kind, name=name, source=source, confidence="low" if low else "high"
        )
        hits[(kind, name)] = artifact
    if source is not None and artifact.source is None:
        artifact.source = source
    if not low:
        artifact.confidence = "high"
    if ref.key not in artifact.used_by:
        artifact.used_by.append(ref.key)
    return artifact
