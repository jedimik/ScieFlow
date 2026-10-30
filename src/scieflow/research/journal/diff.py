"""`scieflow journal diff` and `scieflow journal style` (M-F plan 5.6). Deterministic; no network.

diff: field-level difference between the adopted profile (config/journals/<slug>.yml) and the run's
draft (journal/profile.draft.yml). Lines: `~ path: old -> new`, `+ path: value`, `- path`; facts blocks
included. style: the run's figures block as an M-D2 style file (journal/figure-style.yml).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scieflow.core.project import Project
from scieflow.research.journal import profile, state


class StyleError(state.StateError):
    """A refusal: exit 1, nothing written."""


def flatten(value, path: str = "") -> dict:
    """Every leaf of a profile keyed by its dotted path (list items as [n]); empty containers are leaves."""
    out: dict = {}
    if isinstance(value, dict) and value:
        for key in value:
            out.update(flatten(value[key], f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            out.update(flatten(item, f"{path}[{index}]"))
    else:
        out[path] = value
    return out


def _show(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def compare(adopted: dict, draft: dict) -> list[dict]:
    """Rows {op, path, old, new} in path order: `~` changed, `+` only in the draft, `-` only adopted."""
    old, new = flatten(adopted), flatten(draft)
    rows = []
    for path in sorted(set(old) | set(new)):
        if path in old and path in new:
            if old[path] != new[path]:
                rows.append({"op": "~", "path": path, "old": old[path], "new": new[path]})
        elif path in new:
            rows.append({"op": "+", "path": path, "old": None, "new": new[path]})
        else:
            rows.append({"op": "-", "path": path, "old": old[path], "new": None})
    return rows


def line(row: dict) -> str:
    if row["op"] == "~":
        return f"~ {row['path']}: {_show(row['old'])} -> {_show(row['new'])}"
    if row["op"] == "+":
        return f"+ {row['path']}: {_show(row['new'])}"
    return f"- {row['path']}"


def _figures_ok(figures: dict, ranges: dict) -> str | None:
    """Why the figures block is outside schemas/figure-style.yml (None when it is inside)."""
    if set(figures) != set(ranges):
        return f"the keys must be exactly {', '.join(sorted(ranges))}"
    for key, bounds in ranges.items():
        value = figures[key]
        if key == "formats":
            if not isinstance(value, list) or not value or not set(value) <= set(bounds):
                return f"formats must be a non-empty list of {', '.join(bounds)}"
        elif not isinstance(value, int) or isinstance(value, bool) or not bounds[0] <= value <= bounds[1]:
            return f"{key} must be an integer from {bounds[0]} to {bounds[1]}"
    return None


def write_style(project: Project, run: Path) -> Path:
    """journal/figure-style.yml from the run's pinned profile (journal/profile.yml) figures block.

    The pinned profile was validated in full when it was adopted; here only the figures block is
    checked against schemas/figure-style.yml (keys and ranges), so no quote is needed for it."""
    jdir = state.journal_dir(run)
    pinned = jdir / "profile.yml"
    if not pinned.is_file() or pinned.is_symlink():
        raise StyleError("refused: no pinned profile in this run; run `scieflow journal adopt` first")
    data = profile.load_profile(pinned)
    figures = data.get("figures")
    if not isinstance(figures, dict):
        raise StyleError("refused: the profile has no figures block")
    why = _figures_ok(figures, profile.figure_ranges(project.root))
    if why is not None:
        raise StyleError(f"refused: the figures block does not match schemas/figure-style.yml: {why}")
    target = jdir / "figure-style.yml"
    state.write_text(target, yaml.safe_dump({"figures": figures}, sort_keys=False, allow_unicode=True))
    return target

