"""Write agent configuration changes: defaults, one workspace, or the news module.

Changes are planned in memory, validated against the effective result, shown
as a diff, and only then written. YAML is edited round-trip (ruamel.yaml), so
comments and untouched lines stay as they were. A workspace only ever stores
differences from the defaults: setting a value equal to its default removes
the override.
"""

from __future__ import annotations

import difflib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from scieflow.core import agent_config as ac

NUMBER_FIELDS = {"timeout_min", "timeout"}
BOOL_FIELDS = {"enabled"}


class ConfigureError(Exception):
    """A requested change is invalid; nothing was written."""


@dataclass(frozen=True)
class Op:
    kind: str       # "assign" | "set" | "unset" | "promote" | "demote"
    key: str        # role | "agent.field" | news field
    value: object = None


@dataclass
class FileChange:
    path: Path
    before: str
    after: str

    def diff(self, root: Path | None = None) -> str:
        name = str(self.path.relative_to(root)) if root else str(self.path)
        return "".join(difflib.unified_diff(
            self.before.splitlines(True), self.after.splitlines(True),
            fromfile=f"a/{name}", tofile=f"b/{name}",
        ))


@dataclass
class Plan:
    changes: list[FileChange]
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- parsing -----------------------------------------------------------------

def coerce(field_name: str, raw: str):
    if field_name in NUMBER_FIELDS:
        try:
            number = float(raw)
        except ValueError as exc:
            raise ConfigureError(f"{field_name} must be a number, got {raw!r}") from exc
        return int(number) if number.is_integer() else number
    if field_name in BOOL_FIELDS:
        lowered = raw.strip().lower()
        if lowered not in ("true", "false"):
            raise ConfigureError(f"{field_name} must be true or false, got {raw!r}")
        return lowered == "true"
    return raw


def parse_assign(text: str) -> Op:
    role, sep, raw = text.partition("=")
    role = role.strip()
    if not sep or not raw.strip():
        raise ConfigureError(f"--assign expects ROLE=AGENT[,AGENT], got {text!r}")
    spec = ac.ROLES.get(role)
    if spec is None:
        raise ConfigureError(f"unknown role {role!r} (known: {', '.join(ac.ROLES)})")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if spec.many:
        return Op("assign", role, names)
    if len(names) != 1:
        raise ConfigureError(f"role {role} takes exactly one agent")
    return Op("assign", role, names[0])


def parse_set(text: str, *, news: bool = False) -> Op:
    key, sep, raw = text.partition("=")
    key = key.strip()
    if not sep:
        raise ConfigureError(f"--set expects {'FIELD' if news else 'AGENT.FIELD'}=VALUE, got {text!r}")
    field_name = key if news else key.partition(".")[2]
    if not news and ("." not in key or not key.partition(".")[0] or not field_name):
        raise ConfigureError(f"--set expects AGENT.FIELD=VALUE, got {text!r}")
    return Op("set", key, coerce(field_name, raw.strip()))


def parse_unset(text: str) -> Op:
    return Op("unset", text.strip())


def parse_role_exception(kind: str, text: str) -> Op:
    role = text.strip()
    if role not in ac.ROLES:
        raise ConfigureError(f"unknown role {role!r} (known: {', '.join(ac.ROLES)})")
    return Op(kind, role)


def _exceptions(doc) -> list:
    listed = doc.get("support_as_primary")
    return list(listed) if isinstance(listed, list) else []


def _set_exceptions(doc, roles: list) -> None:
    if roles:
        doc["support_as_primary"] = _flow(roles)
    else:
        doc.pop("support_as_primary", None)


# --- round-trip YAML ---------------------------------------------------------

_STYLES = ((2, 4, 2), (2, 2, 0))


def _yaml(style) -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=style[0], sequence=style[1], offset=style[2])
    return y


def _dump(doc, style) -> str:
    out = io.StringIO()
    _yaml(style).dump(doc, out)
    return out.getvalue()


def _load(path: Path):
    """Load for editing; pick the indentation style that reproduces the file."""
    if not path.exists():
        return "", CommentedMap(), _STYLES[1]
    text = path.read_text()
    for style in _STYLES:
        doc = _yaml(style).load(text)
        if doc is None:
            return text, CommentedMap(), style
        if _dump(doc, style) == text:
            return text, doc, style
    return text, _yaml(_STYLES[0]).load(text), _STYLES[0]


def _plain(doc) -> dict:
    return yaml.safe_load(_dump(doc, _STYLES[0])) or {}


def _flow(value):
    if isinstance(value, list):
        seq = CommentedSeq(value)
        seq.fa.set_flow_style()
        return seq
    return value


def _child(parent: CommentedMap, key: str) -> CommentedMap:
    if not isinstance(parent.get(key), dict):
        parent[key] = CommentedMap()
    return parent[key]


def _prune(parent: CommentedMap, key: str) -> None:
    if key in parent and isinstance(parent[key], dict) and not parent[key]:
        del parent[key]


def _segments(text: str) -> list[tuple[str | None, str]]:
    """Split a mapping document into top-level key blocks.

    Blank lines and column-0 comments directly above a key belong to that key.
    """
    segments: list[tuple[str | None, str]] = []
    key: str | None = None
    current: list[str] = []
    pending: list[str] = []
    for line in text.splitlines(True):
        stripped = line.strip()
        top_level = bool(stripped) and line[0] not in " \t#-" and ":" in line
        if top_level:
            if key is not None or current:
                segments.append((key, "".join(current)))
            key = line.split(":", 1)[0].strip().strip("'\"")
            current, pending = pending + [line], []
        elif not stripped or line.startswith("#"):
            pending.append(line)
        else:
            current += pending + [line]
            pending = []
    current += pending
    if key is not None or current:
        segments.append((key, "".join(current)))
    return segments


def _leading_comments(segment: str) -> str:
    lines = segment.splitlines(True)
    n = 0
    while n < len(lines) and (not lines[n].strip() or lines[n].startswith("#")):
        n += 1
    return "".join(lines[:n])


def _render(before: str, doc, style) -> str:
    """Dump `doc`, keeping the original text of every unchanged top-level key.

    Files written by other tools (PyYAML wraps long strings at 80 columns) do
    not always round-trip exactly; splicing keeps such untouched blocks as
    they were instead of reformatting the whole file.
    """
    full = _dump(doc, style)
    if not before:
        return full
    try:
        old_data = yaml.safe_load(before) or {}
        new_data = yaml.safe_load(full) or {}
    except yaml.YAMLError:
        return full
    if not isinstance(old_data, dict) or not isinstance(new_data, dict):
        return full
    old_segments = dict(_segments(before))
    parts = []
    for key, segment in _segments(full):
        if key in old_segments and key in old_data and old_data.get(key) == new_data.get(key):
            parts.append(old_segments[key])
        elif key in old_segments:
            parts.append(_leading_comments(old_segments[key]) + segment[len(_leading_comments(segment)):])
        else:
            parts.append(segment)
    spliced = "".join(parts)
    try:
        return spliced if yaml.safe_load(spliced) == new_data else full
    except yaml.YAMLError:
        return full


def _change(path: Path, before: str, doc, style) -> FileChange | None:
    if doc:
        after = _render(before, doc, style)
    else:
        after = "{}\n" if before else ""   # keep an emptied file a valid mapping
    return None if after == before else FileChange(path, before, after)


def write(plan: Plan) -> None:
    for change in plan.changes:
        change.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = change.path.with_name(f".{change.path.name}.tmp-{os.getpid()}")
        tmp.write_text(change.after)
        tmp.replace(change.path)


def _refuse_problems(eff: ac.Effective) -> None:
    if eff.problems:
        raise ConfigureError("refused, the result would be invalid:\n  - "
                             + "\n  - ".join(eff.problems))


def _split_agent_key(key: str) -> tuple[str, str]:
    agent, _, field_name = key.partition(".")
    return agent, field_name


# --- targets -----------------------------------------------------------------

def plan_defaults(root: Path, ops: list[Op]) -> Plan:
    agents_path = root / "config" / "agents.yml"
    defaults_path = root / "config" / "defaults.yml"
    agents_before, agents_doc, agents_style = _load(agents_path)
    defaults_before, defaults_doc, defaults_style = _load(defaults_path)
    registry = agents_doc.get("agents") or {}

    for op in ops:
        if op.kind in ("promote", "demote"):
            roles = _exceptions(defaults_doc)
            if op.kind == "promote" and op.key not in roles:
                roles.append(op.key)
            if op.kind == "demote":
                roles = [r for r in roles if r != op.key]
            _set_exceptions(defaults_doc, roles)
        elif op.kind == "assign":
            _child(defaults_doc, "assignments")[op.key] = _flow(op.value)
        elif op.kind == "set":
            agent, field_name = _split_agent_key(op.key)
            if agent not in registry:
                raise ConfigureError(f"unknown agent {agent!r}; new agents are added to config/agents.yml by hand")
            if field_name not in ac.AGENT_FIELDS:
                raise ConfigureError(f"cannot set {field_name!r} (settable: {', '.join(ac.AGENT_FIELDS)})")
            registry[agent][field_name] = op.value
        elif op.kind == "unset":
            if op.key in ac.ROLES:
                raise ConfigureError("the defaults must assign every role; use --assign to change it")
            agent, field_name = _split_agent_key(op.key)
            if agent not in registry or field_name not in ac.AGENT_FIELDS:
                raise ConfigureError(f"cannot unset {op.key!r}")
            registry[agent].pop(field_name, None)

    eff = ac.resolve_data(_plain(agents_doc), _plain(defaults_doc))
    _refuse_problems(eff)
    changes = [c for c in (
        _change(agents_path, agents_before, agents_doc, agents_style),
        _change(defaults_path, defaults_before, defaults_doc, defaults_style),
    ) if c]
    return Plan(changes, warnings=eff.warnings)


def plan_workspace(root: Path, slug: str, ops: list[Op]) -> Plan:
    ws_dir = ac.workspace_dir(root, slug)
    if not ws_dir.is_dir():
        raise ConfigureError(f"no workspace {slug!r} under {root / 'workspace'}")
    path = ws_dir / "config.yml"
    before, doc, style = _load(path)
    registry = ac._load_yaml(root / "config" / "agents.yml")
    defaults = ac._load_yaml(root / "config" / "defaults.yml")
    baseline = ac.resolve_data(registry, defaults)
    registry_agents = registry.get("agents") or {}
    legacy_roles = {role for key, role in ac.LEGACY_ROLE_KEYS.items() if key in doc}
    has_run_set = isinstance(doc.get("agents"), list)

    default_exceptions = defaults.get("support_as_primary") or []
    for op in ops:
        if op.kind in ("promote", "demote"):
            roles = _exceptions(doc)
            if op.kind == "promote":
                if op.key not in roles and op.key not in default_exceptions:
                    roles.append(op.key)
            elif op.key in roles:
                roles = [r for r in roles if r != op.key]
            elif op.key in default_exceptions:
                raise ConfigureError(
                    f"{op.key} is promoted in the defaults; demote it there "
                    "(scieflow agent configure --demote ...)"
                )
            _set_exceptions(doc, roles)
        elif op.kind == "assign":
            spec = ac.ROLES[op.key]
            shadowed = op.key in legacy_roles or (has_run_set and spec.many)
            if op.value == baseline.value(op.key) and not shadowed:
                (doc.get("assignments") or {}).pop(op.key, None)
            else:
                _child(doc, "assignments")[op.key] = _flow(op.value)
            _prune(doc, "assignments")
        elif op.kind in ("set", "unset"):
            if op.kind == "unset" and op.key in ac.ROLES:
                (doc.get("assignments") or {}).pop(op.key, None)
                _prune(doc, "assignments")
                continue
            agent, field_name = _split_agent_key(op.key)
            if agent not in registry_agents:
                raise ConfigureError(f"unknown agent {agent!r}")
            if field_name == "enabled":
                raise ConfigureError(
                    "enabled is global; change it in the defaults, and narrow a run "
                    "through its role assignments instead"
                )
            if field_name not in ac.WORKSPACE_AGENT_FIELDS:
                raise ConfigureError(
                    f"cannot {op.kind} {field_name!r} (fields: {', '.join(ac.WORKSPACE_AGENT_FIELDS)})"
                )
            overrides = _child(doc, "agent_overrides")
            agent_map = _child(overrides, agent)
            if op.kind == "unset" or registry_agents[agent].get(field_name) == op.value:
                agent_map.pop(field_name, None)
            else:
                agent_map[field_name] = op.value
            _prune(overrides, agent)
            _prune(doc, "agent_overrides")

    eff = ac.resolve_data(registry, defaults, _plain(doc))
    _refuse_problems(eff)
    change = _change(path, before, doc, style)
    plan = Plan([change] if change else [], warnings=eff.warnings)
    if change and not (ws_dir / "status.yml").exists():
        plan.notes.append(
            f"workspace/{slug} has no status.yml yet: `scieflow agent run` refuses "
            "prompts in a run folder with config.yml but no status.yml, so create the "
            "run's status.yml before dispatching"
        )
    return plan


NEWS_FIELDS = ("agent", "model", "reasoning", "timeout")


def news_config_path(root: Path) -> Path:
    return root / "config" / "news.yml"


def plan_news(root: Path, ops: list[Op], config_path: Path | None = None) -> Plan:
    """Agent settings of the news module (its own restricted adapter)."""
    import tempfile

    from scieflow.news.config import ConfigError, load_config

    path = config_path or news_config_path(root)
    if not path.exists():
        raise ConfigureError(f"{path} does not exist; create it with `scieflow news init`")
    before, doc, style = _load(path)
    for op in ops:
        if op.kind == "assign":
            raise ConfigureError("the news module has no roles; use --set agent=<name>")
        if op.key not in NEWS_FIELDS:
            raise ConfigureError(f"news setting {op.key!r} unknown (settable: {', '.join(NEWS_FIELDS)})")
        if op.kind == "set":
            if op.key in doc or not isinstance(doc, CommentedMap):
                doc[op.key] = op.value
            else:
                # keep agent settings together at the top, not after the interests
                anchors = [k for k in NEWS_FIELDS if k in doc]
                position = (list(doc).index(anchors[-1]) + 1) if anchors else 0
                doc.insert(position, op.key, op.value)
        else:
            doc.pop(op.key, None)
    change = _change(path, before, doc, style)
    after = change.after if change else before
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        handle.write(after)
    try:
        cfg = load_config(Path(handle.name))
    except ConfigError as exc:
        raise ConfigureError(f"refused, the news config would be invalid: {exc}") from exc
    finally:
        os.unlink(handle.name)
    plan = Plan([change] if change else [])
    if cfg.agent == "agy":
        plan.warnings.append(
            "agy is a support-tier agent (AGENTS.md rule 10); news research runs it "
            "alone, so choose it only on the user's explicit request"
        )
    return plan


def news_settings(root: Path, config_path: Path | None = None) -> dict:
    path = config_path or news_config_path(root)
    data = ac._load_yaml(path)
    return {
        "agent": data.get("agent", "claude"),
        "model": data.get("model"),
        "reasoning": data.get("reasoning"),
        "timeout": data.get("timeout"),
    }
