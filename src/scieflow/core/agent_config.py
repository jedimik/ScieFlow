"""Layered agent configuration: registry, role assignments, per-run overrides.

Three layers, later ones win and only hold differences:

1. ``config/agents.yml`` — the registry: per-agent cmd, model, reasoning,
   timeout_min, tier, enabled, menu.
2. ``config/defaults.yml`` ``assignments:`` — which agent (or agents, for
   fan-out roles) performs each role in ROLES.
3. ``workspace/<slug>/config.yml`` — ``assignments:`` and ``agent_overrides:``
   for one run.

``support_as_primary:`` (a list of roles, in the defaults or a run) is the
explicit, per-role exception that lets a support-tier agent act as a primary
agent for that role only. Run exceptions add to the default ones.

`resolve` merges them and records where every value came from; `validate`
enforces the catalogue and tier routing (root AGENTS.md rule 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scieflow.core import config


@dataclass(frozen=True)
class Role:
    many: bool          # fan-out role: a list of agents
    support_ok: bool    # support-tier agents allowed (always paired with a primary)
    help: str


ROLES: dict[str, Role] = {
    "loop.experiment": Role(False, False, "experiment-cycle sub-agent: campaign design and run"),
    "loop.literature": Role(False, False, "literature-cycle sub-agent"),
    "loop.paper-draft": Role(False, False, "notebook paper handoff: paper-draft workflow"),
    "research.search": Role(True, True, "lit-review / gap-discovery literature search fan-out"),
    "research.cross-review": Role(True, False, "lit-review cross-review of findings"),
    "research.gap-analysis": Role(True, False, "gap-discovery gap analysis fan-out"),
    "research.debate": Role(True, False, "perspective debate participants"),
    "research.journal-profile": Role(True, True, "journal profiling (web search)"),
    "research.reviewer": Role(False, False, "paper-review reviewer"),
    "research.submitter": Role(False, False, "paper-review submitter (differs from reviewer)"),
    "research.outline": Role(False, False, "paper-draft outline author"),
    "research.draft-authors": Role(True, False, "paper-draft independent draft authors"),
    "research.consistency": Role(False, False, "paper-draft consistency pass"),
}

AGENT_FIELDS = ("model", "reasoning", "timeout_min", "cmd", "stdin_cmd", "enabled")
# `enabled` is global: a run narrows participation through its assignments.
WORKSPACE_AGENT_FIELDS = tuple(f for f in AGENT_FIELDS if f != "enabled")

# Keys older research runs wrote at the top level of config.yml. Read only.
LEGACY_ROLE_KEYS = {
    "reviewer": "research.reviewer",
    "submitter": "research.submitter",
    "outline_agent": "research.outline",
    "consistency_agent": "research.consistency",
}

DEFAULT = "default"
WORKSPACE = "workspace"
LEGACY = "workspace (legacy key)"


@dataclass
class Setting:
    value: object
    source: str


@dataclass
class Effective:
    agents: dict[str, dict[str, Setting]]
    tiers: dict[str, str]
    menus: dict[str, dict]
    assignments: dict[str, Setting]
    support_as_primary: dict[str, str] = field(default_factory=dict)   # role -> source
    problems: list[str] = field(default_factory=list)   # block writes, exit 1
    warnings: list[str] = field(default_factory=list)   # shown, never block

    def value(self, role: str):
        setting = self.assignments.get(role)
        return None if setting is None else setting.value

    def to_json(self) -> dict:
        return {
            "assignments": {r: {"value": s.value, "source": s.source}
                            for r, s in self.assignments.items()},
            "agents": {
                name: {f: {"value": s.value, "source": s.source} for f, s in fields.items()}
                | {"tier": {"value": self.tiers.get(name), "source": DEFAULT}}
                for name, fields in self.agents.items()
            },
            "support_as_primary": dict(self.support_as_primary),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
        }


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def workspace_dir(root: Path, slug: str) -> Path:
    return root / "workspace" / slug


def resolve_data(registry: dict, defaults: dict, workspace: dict | None = None) -> Effective:
    """Merge already-parsed documents (used before writing, to validate)."""
    entries = registry.get("agents") or {}
    agents = {
        name: {f: Setting(entry[f], DEFAULT) for f in AGENT_FIELDS if f in entry}
        for name, entry in entries.items()
    }
    tiers = {name: entry.get("tier") for name, entry in entries.items()}
    menus = {name: entry.get("menu") or {} for name, entry in entries.items()}
    assignments = {role: Setting(value, DEFAULT)
                   for role, value in (defaults.get("assignments") or {}).items()}

    ws = workspace or {}
    run_set = ws.get("agents")
    if isinstance(run_set, list):
        for role, setting in list(assignments.items()):
            spec = ROLES.get(role)
            if spec and spec.many and isinstance(setting.value, list):
                narrowed = [a for a in setting.value if a in run_set]
                # A legacy run set that shares no agent with a role never used
                # that role; leave it at the default instead of emptying it.
                if narrowed:
                    assignments[role] = Setting(narrowed, LEGACY)
    for key, role in LEGACY_ROLE_KEYS.items():
        if key in ws:
            assignments[role] = Setting(ws[key], LEGACY)
    for role, value in (ws.get("assignments") or {}).items():
        assignments[role] = Setting(value, WORKSPACE)
    for name, overrides in (ws.get("agent_overrides") or {}).items():
        if not isinstance(overrides, dict):
            continue
        target = agents.setdefault(name, {})
        for f, value in overrides.items():
            target[f] = Setting(value, WORKSPACE)

    exceptions: dict[str, str] = {}
    for doc, source in ((defaults, DEFAULT), (ws, WORKSPACE)):
        listed = doc.get("support_as_primary") or []
        if isinstance(listed, str):
            listed = [listed]
        for role in listed if isinstance(listed, list) else []:
            exceptions.setdefault(str(role), source)

    eff = Effective(agents=agents, tiers=tiers, menus=menus, assignments=assignments,
                    support_as_primary=exceptions)
    eff.problems, eff.warnings = validate(eff)
    return eff


def resolve(root: Path, slug: str | None = None) -> Effective:
    registry = _load_yaml(root / "config" / "agents.yml")
    defaults = _load_yaml(root / "config" / "defaults.yml")
    ws = _load_yaml(workspace_dir(root, slug) / "config.yml") if slug else None
    return resolve_data(registry, defaults, ws)


def validate(eff: Effective) -> tuple[list[str], list[str]]:
    """Return (problems, warnings). Problems break dispatch or tier routing."""
    problems: list[str] = []
    warnings: list[str] = []

    for role in ROLES:
        if role not in eff.assignments:
            problems.append(f"role {role}: no agent assigned")
    for role, setting in eff.assignments.items():
        spec = ROLES.get(role)
        if spec is None:
            problems.append(f"unknown role {role!r} (known: {', '.join(ROLES)})")
            continue
        names = setting.value
        if spec.many:
            if isinstance(names, str):
                names = [names]
            if not isinstance(names, list) or not names:
                problems.append(f"role {role}: needs a non-empty list of agents")
                continue
            if len(set(names)) != len(names):
                problems.append(f"role {role}: an agent is listed twice")
        else:
            if not isinstance(names, str) or not names:
                problems.append(f"role {role}: needs exactly one agent name")
                continue
            names = [names]
        promoted = role in eff.support_as_primary
        tiers_here = set()
        for name in names:
            if name not in eff.agents or name not in eff.tiers:
                problems.append(f"role {role}: unknown agent {name!r}")
                continue
            enabled = eff.agents[name].get("enabled")
            if enabled is not None and enabled.value is False:
                problems.append(f"role {role}: agent {name!r} is disabled")
            tier = eff.tiers.get(name)
            if tier == "support" and promoted:
                warnings.append(
                    f"role {role}: support-tier {name!r} acts as a primary agent here "
                    f"(explicit exception, {eff.support_as_primary[role]})"
                )
                tier = "primary"
            tiers_here.add(tier)
            if tier == "support" and not spec.support_ok:
                problems.append(
                    f"role {role}: {name!r} is a support-tier agent; this role is "
                    "primary-only unless the role is promoted with --promote "
                    f"{role} (AGENTS.md rule 10)"
                )
        if "support" in tiers_here and spec.support_ok and "primary" not in tiers_here:
            problems.append(
                f"role {role}: a support-tier agent must be paired with a primary "
                "agent (AGENTS.md rule 10)"
            )

    for role, source in eff.support_as_primary.items():
        if role not in ROLES:
            problems.append(f"support_as_primary: unknown role {role!r}")
            continue
        assigned = eff.value(role)
        names = assigned if isinstance(assigned, list) else [assigned]
        if not any(eff.tiers.get(n) == "support" for n in names):
            warnings.append(
                f"support_as_primary: {role} ({source}) has no support-tier agent "
                "assigned; the exception is unused"
            )

    reviewer, submitter = eff.value("research.reviewer"), eff.value("research.submitter")
    if reviewer is not None and reviewer == submitter:
        problems.append("research.reviewer and research.submitter must be different agents")

    for name, fields in eff.agents.items():
        if name not in eff.tiers:
            problems.append(f"agent_overrides: unknown agent {name!r}")
            continue
        cmd = fields.get("cmd")
        if cmd is None or not isinstance(cmd.value, str) or not cmd.value.strip():
            problems.append(f"agent {name}: cmd is required")
        timeout = fields.get("timeout_min")
        if timeout is not None and (
            isinstance(timeout.value, bool)
            or not isinstance(timeout.value, (int, float))
            or timeout.value <= 0
        ):
            problems.append(f"agent {name}: timeout_min must be a positive number")
        model = fields.get("model")
        if model is not None and (not isinstance(model.value, str) or not model.value.strip()):
            problems.append(f"agent {name}: model must be a non-empty string")
        enabled = fields.get("enabled")
        if enabled is not None and not isinstance(enabled.value, bool):
            problems.append(f"agent {name}: enabled must be true or false")
        reasoning = fields.get("reasoning")
        if reasoning is not None:
            templates = " ".join(
                str(fields[f].value) for f in ("cmd", "stdin_cmd") if f in fields
            )
            if "{reasoning}" not in templates:
                warnings.append(
                    f"agent {name}: cmd has no {{reasoning}} placeholder, so reasoning "
                    f"{reasoning.value!r} is only a label unless the cmd sets it (menu `how`)"
                )
            levels = ((eff.menus.get(name) or {}).get("reasoning") or {}).get("levels")
            if isinstance(levels, list) and reasoning.value not in levels:
                warnings.append(
                    f"agent {name}: reasoning {reasoning.value!r} is not in the menu "
                    f"levels {levels}; passed through as given"
                )
    return problems, warnings


def format_table(eff: Effective, title: str) -> str:
    lines = [title, "", "Roles:"]
    width = max(len(r) for r in ROLES)
    for role in ROLES:
        setting = eff.assignments.get(role)
        shown = "-" if setting is None else (
            ", ".join(setting.value) if isinstance(setting.value, list) else str(setting.value)
        )
        source = "" if setting is None else setting.source
        if role in eff.support_as_primary:
            source += f"  [support as primary: {eff.support_as_primary[role]}]"
        lines.append(f"  {role:<{width}}  {shown:<24}  {source}")
    lines += ["", "Agents:"]
    for name, fields in eff.agents.items():
        tier = eff.tiers.get(name) or "?"
        lines.append(f"  {name} ({tier})")
        for f in AGENT_FIELDS:
            if f in fields and f not in ("cmd", "stdin_cmd"):
                lines.append(f"    {f:<12} {fields[f].value!s:<28} {fields[f].source}")
    if eff.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in eff.warnings]
    if eff.problems:
        lines += ["", "Problems:"] + [f"  - {p}" for p in eff.problems]
    return "\n".join(lines)


def repo_root() -> Path:
    return config.repo_root()
