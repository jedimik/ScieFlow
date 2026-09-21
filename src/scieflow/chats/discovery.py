"""Cross-store discovery and filtering — the input to every other command."""

from __future__ import annotations

from datetime import datetime, timezone

import click

from .config import ChatsConfig
from .model import ChatRef
from .stores import get_adapter
from .stores.base import StoreError


def adapters(config: ChatsConfig, tools: tuple[str, ...] = (), options: dict | None = None):
    for store in config.enabled_stores(tuple(tools)):
        yield get_adapter(
            store.name, store.root, tuple(config.exclude_extra), options
        )


def discover(
    config: ChatsConfig,
    tools: tuple[str, ...] = (),
    since: datetime | None = None,
    projects: tuple[str, ...] = (),
    options: dict | None = None,
) -> list[ChatRef]:
    found: list[ChatRef] = []
    for adapter in adapters(config, tools, options):
        try:
            found.extend(adapter.discover())
        except StoreError as e:
            click.echo(f"warning: {adapter.tool}: {e}", err=True)
    return _filter(found, since, projects)


def _filter(
    refs: list[ChatRef], since: datetime | None, projects: tuple[str, ...]
) -> list[ChatRef]:
    if since is not None:
        cutoff = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        refs = [r for r in refs if r.updated is None or r.updated >= cutoff]
    if projects:
        needles = tuple(p.lower() for p in projects)
        refs = [
            r
            for r in refs
            if r.project_path and any(n in r.project_path.lower() for n in needles)
        ]
    return sorted(refs, key=lambda r: (r.tool, r.project_path or "", r.chat_id))
