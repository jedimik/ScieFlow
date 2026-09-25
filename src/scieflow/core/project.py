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


def clean_slug(slug: str) -> str:
    """The canonical form of a run slug, or `ProjectError`.

    Shared by `Project.run_dir` and `init_workspace`
    (`scieflow.core.run.init`), which both join a slug straight onto a
    workspace root with no checks of their own otherwise — this is the one
    place a slug from any source (an HTTP form, a CLI argument) is turned
    into a directory name.

    `fullmatch`, not `match`: Python's `$` matches just *before* a trailing
    newline as well as at the true end of the string, so `SLUG_RE.match`
    alone would accept a slug ending in "\\n" — a control character that
    then names a directory, breaking shell tooling, DVC archives and log
    grepping. `fullmatch` requires the whole string to match, so a trailing
    (or embedded) control character is refused outright rather than reaching
    a directory name.

    A plain leading/trailing space is not a control character and already
    matches the pattern (space is a legal slug character throughout), so it
    is not refused — it is trimmed instead, the same hygiene a filename
    picker applies, so `"a b "` becomes the directory `"a b"` rather than
    one with an invisible trailing space in its name.
    """
    cleaned = slug.removeprefix("workspace/").rstrip("/")
    if not SLUG_RE.fullmatch(cleaned) or ".." in cleaned:
        raise ProjectError(f"not a run slug: {slug!r}")
    return cleaned.strip()


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
        return self.workspace_root / clean_slug(slug)

    def agents(self) -> dict:
        return config.load_agents(self.root)

    def defaults(self) -> dict:
        return config.load_defaults(self.root)

    def schema(self, name: str) -> dict:
        return yaml.safe_load((self.root / "schemas" / f"{name}.yml").read_text())
