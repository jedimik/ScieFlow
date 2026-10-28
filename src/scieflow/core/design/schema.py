"""Loads schemas/design.yml (M-D1 plan section 5.1). Fail closed: any problem raises SchemaError.

Only the design package reads the file. reporting.py takes its study types from here, so the
checklist applicability rule and the design model cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SCHEMA = "schemas/design.yml"
_NEEDED = ("format_version", "top_keys", "sap_keys", "item_keys", "enums")


class SchemaError(ValueError):
    """schemas/design.yml cannot be read or lacks a required section."""


def load(root) -> dict:
    path = Path(root) / SCHEMA
    try:
        if path.is_symlink():
            raise SchemaError(f"{SCHEMA}: is a symlink")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except SchemaError:
        raise
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError) as exc:
        raise SchemaError(f"{SCHEMA}: unreadable ({exc.__class__.__name__})") from None
    if not isinstance(raw, dict) or any(key not in raw for key in _NEEDED):
        raise SchemaError(f"{SCHEMA}: missing one of {', '.join(_NEEDED)}")
    return raw


def study_types(root) -> tuple:
    return tuple(load(root)["enums"]["study_types"])
