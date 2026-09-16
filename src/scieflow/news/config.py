from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import yaml

from .templates import DEFAULT_TEMPLATE, TEMPLATES

VALID_AGENTS: tuple[str, ...] = ("claude", "codex", "agy")
VALID_REASONING: tuple[str, ...] = ("low", "medium", "high")
MAX_GROUP_DEPTH = 3
GROUP_KEYS = {"name", "interests", "groups"}

EXAMPLE_CONFIG = """\
agent: claude          # default agent: claude | codex | agy
lookback_days: 30      # window for interests never checked before
# timeout: 900         # optional — overrides the per-template default (science 900s, most others 300s)

interests:
  - name: Snakemake
    context: >                            # optional — enables Gaps & Blind Spots
      HPC pipelines with SLURM, containerized rules
    repo: snakemake/snakemake             # optional GitHub hint
    urls:                                 # optional extra sources
      - https://snakemake.readthedocs.io
    keywords: [workflow, bioinformatics]  # optional disambiguation

  - name: Protein language models
    template: science          # research template: tool | science | coding | platform | keyword
    context: >
      PhD work on protein structure prediction
    keywords: [protein, deep learning]
    lookback_days: 60          # optional per-interest override
"""

TOP_LEVEL_KEYS = {"agent", "lookback_days", "timeout", "interests", "groups", "model", "reasoning"}
INTEREST_KEYS = {"name", "context", "repo", "urls", "keywords", "template", "lookback_days"}


class ConfigError(Exception):
    """Invalid or missing configuration."""


@dataclass
class Group:
    name: str
    interests: list[str] = field(default_factory=list)
    groups: list["Group"] = field(default_factory=list)


@dataclass
class Interest:
    name: str
    context: str | None = None
    repo: str | None = None
    urls: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    template: str = DEFAULT_TEMPLATE
    lookback_days: int | None = None


@dataclass
class Config:
    interests: list[Interest]
    agent: str = "claude"
    lookback_days: int = 30
    timeout: int | None = None
    groups: list[Group] = field(default_factory=list)
    model: str | None = None
    reasoning: str | None = None


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    unknown_top_level = set(raw) - TOP_LEVEL_KEYS
    if unknown_top_level:
        key = sorted(unknown_top_level)[0]
        raise ConfigError(f"unknown config key: {key!r}")

    agent = raw.get("agent", "claude")
    if agent not in VALID_AGENTS:
        raise ConfigError(f"agent must be one of {', '.join(VALID_AGENTS)}, got {agent!r}")

    lookback_days = raw.get("lookback_days", 30)
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days < 1:
        raise ConfigError(f"lookback_days must be a positive integer, got {lookback_days!r}")

    timeout = raw.get("timeout")
    if timeout is not None and (
        not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1
    ):
        raise ConfigError(f"timeout must be a positive integer, got {timeout!r}")

    raw_interests = raw.get("interests")
    if not isinstance(raw_interests, list) or not raw_interests:
        raise ConfigError("config must define a non-empty 'interests' list")

    interests: list[Interest] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_interests):
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigError(f"interests[{i}] must be a mapping with a 'name'")
        name = str(item["name"])
        if name in seen:
            raise ConfigError(f"duplicate interest name: {name}")
        seen.add(name)
        unknown_interest_keys = set(item) - INTEREST_KEYS
        if unknown_interest_keys:
            key = sorted(unknown_interest_keys)[0]
            raise ConfigError(f"interest {name!r}: unknown key {key!r}")
        urls = item.get("urls", [])
        keywords = item.get("keywords", [])
        if not isinstance(urls, list):
            raise ConfigError(f"interest {name!r}: 'urls' must be a list")
        if not isinstance(keywords, list):
            raise ConfigError(f"interest {name!r}: 'keywords' must be a list")

        template = item.get("template", DEFAULT_TEMPLATE)
        if template not in TEMPLATES:
            raise ConfigError(
                f"interest {name!r}: template must be one of "
                f"{', '.join(TEMPLATES)}, got {template!r}"
            )
        interest_lookback = item.get("lookback_days")
        if interest_lookback is not None and (
            not isinstance(interest_lookback, int)
            or isinstance(interest_lookback, bool)
            or interest_lookback < 1
        ):
            raise ConfigError(
                f"interest {name!r}: lookback_days must be a positive integer, "
                f"got {interest_lookback!r}"
            )

        interests.append(
            Interest(
                name=name,
                context=item.get("context"),
                repo=item.get("repo"),
                urls=[str(u) for u in urls],
                keywords=[str(k) for k in keywords],
                template=template,
                lookback_days=interest_lookback,
            )
        )

    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigError(f"model must be a non-empty string, got {model!r}")

    reasoning = raw.get("reasoning")
    if reasoning is not None and reasoning not in VALID_REASONING:
        raise ConfigError(
            f"reasoning must be one of {', '.join(VALID_REASONING)}, got {reasoning!r}"
        )

    interest_names = {i.name for i in interests}
    seen_groups: set[str] = set()

    def parse_group(item: object, depth: int) -> Group:
        if depth > MAX_GROUP_DEPTH:
            raise ConfigError(f"group nesting exceeds max depth {MAX_GROUP_DEPTH}")
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigError("each group must be a mapping with a 'name'")
        unknown = set(item) - GROUP_KEYS
        if unknown:
            raise ConfigError(
                f"group {item.get('name')!r}: unknown key {sorted(unknown)[0]!r}"
            )
        name = str(item["name"])
        if name in seen_groups:
            raise ConfigError(f"duplicate group name: {name}")
        seen_groups.add(name)
        members = item.get("interests", [])
        if not isinstance(members, list):
            raise ConfigError(f"group {name!r}: 'interests' must be a list")
        for m in members:
            if m not in interest_names:
                raise ConfigError(f"group {name!r} references unknown interest: {m}")
        subs = item.get("groups", [])
        if not isinstance(subs, list):
            raise ConfigError(f"group {name!r}: 'groups' must be a list")
        return Group(
            name=name,
            interests=[str(m) for m in members],
            groups=[parse_group(s, depth + 1) for s in subs],
        )

    raw_groups = raw.get("groups") or []
    if not isinstance(raw_groups, list):
        raise ConfigError("'groups' must be a list")
    groups = [parse_group(g, 1) for g in raw_groups]

    return Config(
        interests=interests,
        agent=agent,
        lookback_days=lookback_days,
        timeout=timeout,
        groups=groups,
        model=model,
        reasoning=reasoning,
    )


def save_config(config: Config, path: Path) -> None:
    """Write the config back to YAML. Comments in the existing file are not preserved."""
    data: dict = {
        "agent": config.agent,
        "lookback_days": config.lookback_days,
    }

    if config.timeout is not None:
        data["timeout"] = config.timeout
    if config.model:
        data["model"] = config.model
    if config.reasoning:
        data["reasoning"] = config.reasoning

    data["interests"] = []
    for interest in config.interests:
        item: dict = {"name": interest.name}
        if interest.context:
            item["context"] = interest.context
        if interest.repo:
            item["repo"] = interest.repo
        if interest.urls:
            item["urls"] = interest.urls
        if interest.keywords:
            item["keywords"] = interest.keywords
        if interest.template != DEFAULT_TEMPLATE:
            item["template"] = interest.template
        if interest.lookback_days:
            item["lookback_days"] = interest.lookback_days
        data["interests"].append(item)

    def dump_group(group: Group) -> dict:
        item: dict = {"name": group.name}
        if group.interests:
            item["interests"] = list(group.interests)
        if group.groups:
            item["groups"] = [dump_group(g) for g in group.groups]
        return item

    if config.groups:
        data["groups"] = [dump_group(g) for g in config.groups]

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    os.replace(tmp, path)


def group_interest_names(group: Group) -> list[str]:
    names: list[str] = []
    for name in group.interests:
        if name not in names:
            names.append(name)
    for sub in group.groups:
        for name in group_interest_names(sub):
            if name not in names:
                names.append(name)
    return names


def _walk_groups(groups: list[Group]):
    for group in groups:
        yield group
        yield from _walk_groups(group.groups)


def all_group_names(config: Config) -> list[str]:
    return [g.name for g in _walk_groups(config.groups)]


def find_group(config: Config, name: str) -> Group | None:
    for group in _walk_groups(config.groups):
        if group.name == name:
            return group
    return None


def resolve_selection(
    config: Config,
    interests: Sequence[str] | None,
    groups: Sequence[str] | None,
) -> list[str] | None:
    interests = list(interests or [])
    groups = list(groups or [])
    if not interests and not groups:
        return None
    known_groups = set(all_group_names(config))
    unknown_g = sorted(set(groups) - known_groups)
    if unknown_g:
        raise ValueError(f"unknown groups: {', '.join(unknown_g)}")
    known_interests = {i.name for i in config.interests}
    unknown_i = sorted(set(interests) - known_interests)
    if unknown_i:
        raise ValueError(f"unknown interests: {', '.join(unknown_i)}")
    wanted: set[str] = set(interests)
    for gname in groups:
        wanted.update(group_interest_names(find_group(config, gname)))
    return [i.name for i in config.interests if i.name in wanted]
