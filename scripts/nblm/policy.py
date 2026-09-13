"""Load config/notebooklm.yml and enforce its deny-by-default allowlists.

The single sanctioned gate for the claim-check module: every nblm.py
subcommand authorizes here before any download, upload, or question.
PolicyError means the user's config forbids the operation — report it,
never work around it.

This module never reads the session-state file's contents; it only checks
that the user has created one.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REQUIRED_KEYS = ["session_state", "notebook_prefix", "allowed_ops",
                 "source_hosts", "limits"]
REQUIRED_LIMITS = [
    "max_sources_per_notebook",
    "max_source_mb",
    "max_questions_per_run",
    "max_questions_per_day",
    "max_claims_per_question",
    "reask_threshold",
]
_COUNT_LIMITS = [k for k in REQUIRED_LIMITS if k != "reask_threshold"]

# Notebook titles and derived filenames are interpolated into paths and into
# the upstream API; keep the alphabet small and predictable.
_SAFE_NAME_RE = re.compile(r"^[\w.\-]+$")
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9<>\[\]+]+$")


class PolicyError(Exception):
    """Operation refused by config/notebooklm.yml."""


@dataclass
class Profile:
    name: str
    session_state: str
    notebook_prefix: str
    allowed_ops: list
    source_hosts: list
    limits: dict


def load_verdicts(root: Path) -> list[str]:
    """Verdict vocabulary, ordered best -> worst (schemas/claim-audit.yml)."""
    path = root / "schemas" / "claim-audit.yml"
    data = yaml.safe_load(path.read_text())
    return list(data["verdicts"])


def load_profile(root: Path, name: str) -> Profile:
    path = root / "config" / "notebooklm.yml"
    if not path.exists():
        raise PolicyError(
            f"{path} not found — copy config/notebooklm.example.yml to "
            "config/notebooklm.yml and fill it in"
        )
    data = yaml.safe_load(path.read_text()) or {}
    entry = (data.get("profiles") or {}).get(name)
    if entry is None:
        raise PolicyError(f"profile '{name}' not defined in {path}")
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise PolicyError(f"profile '{name}' missing keys: {', '.join(missing)}")
    missing = [k for k in REQUIRED_LIMITS if k not in entry["limits"]]
    if missing:
        raise PolicyError(f"profile '{name}' limits missing: {', '.join(missing)}")
    profile = Profile(name=name, **{k: entry[k] for k in REQUIRED_KEYS})
    _validate_limits(root, profile)
    return profile


def _validate_limits(root: Path, profile: Profile) -> None:
    for key in _COUNT_LIMITS:
        value = profile.limits[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise PolicyError(
                f"limit '{key}' must be a positive integer, got {value!r}"
            )
    verdicts = load_verdicts(root)
    threshold = profile.limits["reask_threshold"]
    if threshold not in verdicts:
        raise PolicyError(
            f"reask_threshold '{threshold}' is not a verdict "
            f"(allowed: {', '.join(verdicts)})"
        )


def check_op(profile: Profile, op: str) -> None:
    if op not in profile.allowed_ops:
        raise PolicyError(
            f"operation '{op}' not in allowed_ops for profile "
            f"'{profile.name}' (allowed: {', '.join(profile.allowed_ops)})"
        )


def session_state_path(profile: Profile) -> Path:
    """The user's session file. Existence only — contents are never read."""
    path = Path(profile.session_state).expanduser()
    if not path.is_absolute():
        raise PolicyError(
            f"session_state must be an absolute path: '{profile.session_state}'"
        )
    return path


def check_notebook_name(profile: Profile, name: str) -> str:
    if not _SAFE_NAME_RE.match(name):
        raise PolicyError(
            f"unsafe notebook name (allowed: letters, digits, '.', '-', '_'): "
            f"'{name}'"
        )
    if not name.startswith(profile.notebook_prefix):
        raise PolicyError(
            f"notebook '{name}' does not start with notebook_prefix "
            f"'{profile.notebook_prefix}' for profile '{profile.name}'"
        )
    return name


def check_doi(doi: str) -> str:
    if not _DOI_RE.match(doi):
        raise PolicyError(f"not a DOI: '{doi}'")
    return doi


def doi_slug(doi: str) -> str:
    """Filesystem-safe stem for a source file, derived from its DOI."""
    check_doi(doi)
    return re.sub(r"[^\w.\-]+", "-", doi).strip("-")


def check_source_url(profile: Profile, url: str) -> str:
    """Authorize a download URL against source_hosts. Exact host match only."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise PolicyError(f"source URL must be https: '{url}'")
    if parts.username or parts.password:
        raise PolicyError("source URL must not carry credentials")
    host = (parts.hostname or "").lower()
    if host not in [h.lower() for h in profile.source_hosts]:
        raise PolicyError(
            f"host '{host}' not in source_hosts for profile '{profile.name}' "
            f"(allowed: {', '.join(profile.source_hosts)})"
        )
    return url


def check_inside_workspace(workspace: Path, path: Path, what: str) -> Path:
    """Rule 1: every artifact this module writes stays in workspace/<slug>/."""
    ws = workspace.resolve()
    resolved = path.resolve()
    if resolved != ws and ws not in resolved.parents:
        raise PolicyError(f"{what} '{path}' is outside the workspace '{ws}'")
    return resolved


def check_source_size(profile: Profile, size_bytes: int, what: str) -> int:
    cap = profile.limits["max_source_mb"] * 1024 * 1024
    if size_bytes > cap:
        raise PolicyError(
            f"{what} is {size_bytes / 1048576:.1f} MB, over the "
            f"max_source_mb ceiling of {profile.limits['max_source_mb']} MB"
        )
    return size_bytes
