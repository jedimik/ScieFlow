"""A ScieFlow project: the repo root and everything derived from it.

`config.repo_root()` reads the current directory, which a server handling
requests — or a test — cannot rely on. New code takes a `Project` instead;
the CLI builds one with `Project.discover()`, tests with `Project(tmp_path)`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow.core import config

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")


class ProjectError(ValueError):
    """A path or name that does not belong to this project."""


@dataclass(frozen=True)
class Project:
    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> "Project":
        return cls(config.repo_root(start).resolve())

    @property
    def workspace_root(self) -> Path:
        return self.root / "workspace"

    @property
    def state_dir(self) -> Path:
        """Project-level runtime state (jobs outside any run). Gitignored."""
        override = os.environ.get("SCIEFLOW_STATE_DIR")
        return Path(override) if override else self.root / ".scieflow"

    def run_dir(self, slug: str) -> Path:
        cleaned = slug.removeprefix("workspace/").rstrip("/")
        if not SLUG_RE.match(cleaned) or ".." in cleaned:
            raise ProjectError(f"not a run slug: {slug!r}")
        return self.workspace_root / cleaned

    def agents(self) -> dict:
        return config.load_agents(self.root)

    def defaults(self) -> dict:
        return config.load_defaults(self.root)

    def schema(self, name: str) -> dict:
        return yaml.safe_load((self.root / "schemas" / f"{name}.yml").read_text())
