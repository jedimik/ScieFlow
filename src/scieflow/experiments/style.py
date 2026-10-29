"""Figure sizing contract (M-D2 plan 5.7; the documented form is schemas/figure-style.yml).

The defaults and ranges below are the same constants the schema file states; a test pins them.
A --style-file is a YAML mapping whose top-level `figures:` block overrides keys. Unknown keys,
out-of-range values, wrong types, a symlinked or unreadable file are refused, naming the key.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULTS = {
    "single_column_mm": 89,
    "double_column_mm": 183,
    "font_pt": 8,
    "dpi": 300,
    "formats": ["png", "svg"],
}
RANGES = {
    "single_column_mm": (30, 200),
    "double_column_mm": (60, 300),
    "font_pt": (5, 14),
    "dpi": (72, 1200),
}
ALLOWED_FORMATS = ("png", "svg", "pdf")


class StyleError(ValueError):
    pass


def _check(key: str, value):
    if key == "formats":
        if (not isinstance(value, list) or not value
                or not all(isinstance(f, str) for f in value)
                or any(f not in ALLOWED_FORMATS for f in value)
                or len(set(value)) != len(value)):
            raise StyleError(
                f"figures.formats: a non-empty list without repeats of "
                f"{', '.join(ALLOWED_FORMATS)}")
        return list(value)
    low, high = RANGES[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise StyleError(f"figures.{key}: an integer {low}..{high}")
    return value


def load_style(path=None) -> dict:
    """The resolved figure style: the defaults, overridden by the file's figures: block."""
    style = {key: (list(v) if isinstance(v, list) else v) for key, v in DEFAULTS.items()}
    if path is None:
        return style
    path = Path(path)
    if path.is_symlink():
        raise StyleError(f"{path.name}: a symlinked style file is refused")
    try:
        text = path.read_text()
    except FileNotFoundError as e:
        raise StyleError(f"{path.name}: missing") from e
    except OSError as e:
        raise StyleError(f"{path.name}: unreadable") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise StyleError(f"{path.name}: unreadable YAML") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise StyleError(f"{path.name}: must be a mapping")
    for key in data:
        if key != "figures":
            raise StyleError(f"{key}: unknown top-level key (allowed: figures)")
    block = data.get("figures") or {}
    if not isinstance(block, dict):
        raise StyleError("figures: must be a mapping")
    for key, value in block.items():
        if key not in DEFAULTS:
            raise StyleError(f"figures.{key}: unknown key (allowed: {', '.join(DEFAULTS)})")
        style[key] = _check(key, value)
    return style
