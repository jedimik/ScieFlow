"""`config/chats.yml` — user-owned, deny-by-default.

Absent file: the module says how to create it and stops. Present file: it
bounds every store root the module may read and where bundles are written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .model import TOOLS

TOP_LEVEL_KEYS = {
    "stores",
    "bundle_dir",
    "encryption",
    "exclude_extra",
    "max_bundle_gb",
    "remote",
}
STORE_KEYS = {"root", "enabled"}
ENCRYPTION_KEYS = {"method", "recipient"}
REMOTE_KEYS = {"enabled", "dvc_remote", "dir"}
VALID_METHODS = ("age", "gpg", "none")

DEFAULT_ROOTS: dict[str, str] = {
    "claude": "~/.claude",
    "codex": "~/.codex",
    "agy": "~/.gemini/antigravity-cli",
    "gemini": "~/.gemini",
}

EXAMPLE_CONFIG = """\
# ScieFlow chat backup — user-owned, deny-by-default.
# Nothing outside the roots listed here is ever read.

stores:
  claude:
    root: ~/.claude
    enabled: true
  codex:
    root: ~/.codex
    enabled: true
  agy:                              # Antigravity CLI (lives inside ~/.gemini)
    root: ~/.gemini/antigravity-cli
    enabled: true
  gemini:
    root: ~/.gemini
    enabled: true

# Where `scieflow chats backup` writes bundles.
bundle_dir: ~/scieflow-chat-bundles

encryption:
  method: age          # age | gpg | none  ('none' also needs --no-encrypt --yes)
  recipient: null      # age public key; null means passphrase (age -p)

# Extra glob patterns to drop from every bundle, on top of the built-in
# exclusions (credentials, caches, logs, re-installable payloads).
exclude_extra: []

# Refuse to pack a selection larger than this, in gigabytes.
max_bundle_gb: 5

# Optional DVC transport for bundles (`scieflow chats push` / `pull`).
# Off by default: a bundle is your whole conversation history, so putting it
# on shared storage is an explicit choice. It moves bundle FILES only — never
# a workspace or a directory; that stays with scripts/dvc_sync.py.
remote:
  enabled: false
  dvc_remote: null       # null = the repo's DVC core.remote
  dir: workspace/chats   # where .dvc pointers live inside the repo
"""


class ConfigError(Exception):
    """Invalid or missing chats configuration."""


@dataclass(frozen=True)
class StoreConfig:
    name: str
    root: Path
    enabled: bool = True


@dataclass(frozen=True)
class Encryption:
    method: str = "age"
    recipient: str | None = None


@dataclass(frozen=True)
class RemoteConfig:
    enabled: bool = False
    dvc_remote: str | None = None
    dir: str = "workspace/chats"


@dataclass
class ChatsConfig:
    stores: dict[str, StoreConfig]
    bundle_dir: Path
    encryption: Encryption = field(default_factory=Encryption)
    exclude_extra: list[str] = field(default_factory=list)
    max_bundle_gb: float = 5.0
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    def enabled_stores(self, only: tuple[str, ...] = ()) -> list[StoreConfig]:
        names = only or TOOLS
        return [
            self.stores[n]
            for n in names
            if n in self.stores and self.stores[n].enabled and self.stores[n].root.exists()
        ]


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def load_config(path: Path) -> ChatsConfig:
    if not path.exists():
        raise ConfigError(
            f"config file not found: {path}\n"
            "The chats module is deny-by-default: create it first with\n"
            "  uv run scieflow chats init"
        )
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    unknown = set(raw) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown config key: {sorted(unknown)[0]!r}")

    raw_stores = raw.get("stores")
    if not isinstance(raw_stores, dict) or not raw_stores:
        raise ConfigError("config must define a non-empty 'stores' mapping")

    stores: dict[str, StoreConfig] = {}
    for name, entry in raw_stores.items():
        if name not in TOOLS:
            raise ConfigError(f"unknown store {name!r} (known: {', '.join(TOOLS)})")
        if not isinstance(entry, dict):
            raise ConfigError(f"store {name!r} must be a mapping")
        bad = set(entry) - STORE_KEYS
        if bad:
            raise ConfigError(f"store {name!r}: unknown key {sorted(bad)[0]!r}")
        root = entry.get("root", DEFAULT_ROOTS[name])
        if not isinstance(root, str):
            raise ConfigError(f"store {name!r}: 'root' must be a string")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"store {name!r}: 'enabled' must be true or false")
        stores[name] = StoreConfig(name=name, root=_expand(root), enabled=enabled)

    bundle_dir = raw.get("bundle_dir", "~/scieflow-chat-bundles")
    if not isinstance(bundle_dir, str):
        raise ConfigError("'bundle_dir' must be a string")

    raw_enc = raw.get("encryption", {})
    if not isinstance(raw_enc, dict):
        raise ConfigError("'encryption' must be a mapping")
    bad = set(raw_enc) - ENCRYPTION_KEYS
    if bad:
        raise ConfigError(f"encryption: unknown key {sorted(bad)[0]!r}")
    method = raw_enc.get("method", "age")
    if method not in VALID_METHODS:
        raise ConfigError(f"encryption.method must be one of {', '.join(VALID_METHODS)}")
    recipient = raw_enc.get("recipient")
    if recipient is not None and not isinstance(recipient, str):
        raise ConfigError("encryption.recipient must be a string or null")

    exclude_extra = raw.get("exclude_extra", [])
    if not isinstance(exclude_extra, list) or not all(
        isinstance(x, str) for x in exclude_extra
    ):
        raise ConfigError("'exclude_extra' must be a list of glob strings")

    raw_remote = raw.get("remote", {})
    if not isinstance(raw_remote, dict):
        raise ConfigError("'remote' must be a mapping")
    bad = set(raw_remote) - REMOTE_KEYS
    if bad:
        raise ConfigError(f"remote: unknown key {sorted(bad)[0]!r}")
    enabled = raw_remote.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("remote.enabled must be true or false")
    dvc_remote = raw_remote.get("dvc_remote")
    if dvc_remote is not None and not isinstance(dvc_remote, str):
        raise ConfigError("remote.dvc_remote must be a string or null")
    remote_dir = raw_remote.get("dir", "workspace/chats")
    if not isinstance(remote_dir, str) or Path(remote_dir).is_absolute():
        raise ConfigError("remote.dir must be a path relative to the repo root")

    max_gb = raw.get("max_bundle_gb", 5)
    if isinstance(max_gb, bool) or not isinstance(max_gb, (int, float)) or max_gb <= 0:
        raise ConfigError(f"max_bundle_gb must be a positive number, got {max_gb!r}")

    return ChatsConfig(
        stores=stores,
        bundle_dir=_expand(bundle_dir),
        encryption=Encryption(method=method, recipient=recipient),
        exclude_extra=list(exclude_extra),
        max_bundle_gb=float(max_gb),
        remote=RemoteConfig(enabled=enabled, dvc_remote=dvc_remote, dir=remote_dir),
    )
