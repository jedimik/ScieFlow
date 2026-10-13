"""A run's review settings: the `review:` block of workspace/<slug>/config.yml.

Effective settings are the built-in defaults, overlaid key by key with the
`review:` block of `config/defaults.yml`, then with the run's own block.
Writes go only through `write()` (CLI: `scieflow review configure <slug>`),
which validates the merged result and touches nothing outside `review:`.

Actor rules: the user (`actor="human"`) may set anything. A coordinator agent
(`actor="agent"`) may only de-escalate scite to `off`, and may change
`reviews_per_draft`; the switches that loosen review separation, who decides,
who reviews (`pool`, `matrix_override`) and scite consent are the user's.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import SingleQuotedScalarString

from scieflow.core import agent_config as ac
from scieflow.core import agent_configure as acf
from scieflow.core import store
from scieflow.core.drafts import DraftError, check_name
from scieflow.core.project import Project, ProjectError

SCITE = ("off", "ask", "approved")
DECIDERS = ("human", "coordinator")
COVERS = ("all", "auto")
ACTORS = ("human", "agent")
POOL_FIELDS = ("agent", "covers")

DEFAULTS: dict = {
    "scite": "ask",
    "reviews_per_draft": 2,
    "allow_same_family_review": False,
    "allow_self_review": False,
    "review_decisions": "human",
    "pool": None,               # None: the run's research.draft-reviewers assignment
    "matrix_override": {},      # reviewer -> [authors]
}
KEYS = tuple(DEFAULTS)

#: Keys a coordinator agent may never set (any value).
HUMAN_ONLY = ("allow_same_family_review", "allow_self_review", "review_decisions",
              "pool", "matrix_override")
#: scite values a coordinator agent may set: switching it off is a de-escalation.
AGENT_SCITE = ("off",)


_YAML11_BOOLS = {"y", "yes", "n", "no", "on", "off", "true", "false"}


class SettingsError(ValueError):
    """Invalid review settings or a refused change; nothing was written."""


def _one_of(key: str, value, allowed: tuple) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise SettingsError(f"review.{key}: {value!r} is not one of {', '.join(allowed)}")


def _bool(key: str, value) -> None:
    if not isinstance(value, bool):
        raise SettingsError(f"review.{key}: {value!r} is not one of true, false")


def _name(key: str, value) -> None:
    if not isinstance(value, str):
        raise SettingsError(f"review.{key}: {value!r} is not an agent name")
    try:
        check_name(value)
    except DraftError as exc:
        raise SettingsError(f"review.{key}: {exc}") from exc


def _validate_pool(value) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise SettingsError(f"review.pool: expected a list of {{agent, covers}}, got {value!r}")
    seen = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise SettingsError(f"review.pool: entry {entry!r} is not a mapping with keys "
                                f"{', '.join(POOL_FIELDS)}")
        extra = set(entry) - set(POOL_FIELDS)
        if extra or "agent" not in entry:
            raise SettingsError(f"review.pool: entry {entry!r} must have keys "
                                f"{', '.join(POOL_FIELDS)} (agent required)")
        _name("pool", entry["agent"])
        if "covers" in entry:
            _one_of("pool.covers", entry["covers"], COVERS)
        if entry["agent"] in seen:
            raise SettingsError(f"review.pool: {entry['agent']!r} listed more than once")
        seen.add(entry["agent"])


def _validate_override(value) -> None:
    if not isinstance(value, dict):
        raise SettingsError(f"review.matrix_override: expected a mapping reviewer -> "
                            f"[authors], got {value!r}")
    for reviewer, authors in value.items():
        _name("matrix_override", reviewer)
        if not isinstance(authors, list):
            raise SettingsError(f"review.matrix_override.{reviewer}: expected a list of "
                                f"authors, got {authors!r}")
        for author in authors:
            _name(f"matrix_override.{reviewer}", author)


def validate(block: dict) -> dict:
    """Check a (partial) review block; returns it unchanged or raises SettingsError."""
    if not isinstance(block, dict):
        raise SettingsError(f"review: expected a mapping, got {block!r}")
    for key, value in block.items():
        if key not in DEFAULTS:
            raise SettingsError(f"review: unknown key {key!r} (keys: {', '.join(KEYS)})")
        if key == "scite":
            _one_of(key, value, SCITE)
        elif key == "review_decisions":
            _one_of(key, value, DECIDERS)
        elif key in ("allow_same_family_review", "allow_self_review"):
            _bool(key, value)
        elif key == "reviews_per_draft":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SettingsError(f"review.{key}: {value!r} is not an integer >= 1")
        elif key == "pool":
            _validate_pool(value)
        elif key == "matrix_override":
            _validate_override(value)
    return block


def _check_agents(block: dict, registry: dict) -> None:
    """Every named agent exists; pool members are enabled and declare a model family."""
    def known(name: str) -> dict:
        if name not in registry:
            raise SettingsError(f"review: unknown agent {name!r}")
        return registry[name] or {}

    def reviewer(key: str, name: str) -> None:
        cfg = known(name)
        if not cfg.get("enabled", True):
            raise SettingsError(f"review.{key}: agent {name!r} is disabled")
        if ac.model_family(cfg) is None:
            raise SettingsError(
                f"review.{key}: agent {name!r} declares no model_family "
                "(add model_family: to its entry in config/agents.yml)")

    for entry in block.get("pool") or []:
        reviewer("pool", entry["agent"])
    for name, authors in (block.get("matrix_override") or {}).items():
        reviewer("matrix_override", name)
        for author in authors:
            known(author)


def _run_dir(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        raise SettingsError(str(exc)) from exc
    if not ws.is_dir():
        raise SettingsError(f"no workspace {slug!r} under {project.workspace_root}")
    return ws


def _block(data, where: str) -> dict:
    block = (data or {}).get("review") if isinstance(data, dict) else None
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise SettingsError(f"{where}: review: is not a mapping")
    block = dict(block)
    if block.get("scite") is False:
        # YAML 1.1 (PyYAML) reads a hand-written bare `scite: off` as false.
        block["scite"] = "off"
    return block


def _merge(project: Project, run_block: dict) -> dict:
    defaults = _block(ac._load_yaml(project.root / "config" / "defaults.yml"),
                      "config/defaults.yml")
    merged = {**DEFAULTS, **defaults, **run_block}
    validate(merged)
    return merged


def load(project: Project, slug: str) -> dict:
    """Effective review settings of a run (all keys present)."""
    ws = _run_dir(project, slug)
    run_block = _block(ac._load_yaml(ws / "config.yml"), f"workspace/{slug}/config.yml")
    return _merge(project, run_block)


def check_actor(changes: dict, actor: str) -> None:
    if actor not in ACTORS:
        raise SettingsError(f"actor {actor!r} is not one of {', '.join(ACTORS)}")
    if actor == "human":
        return
    for key, value in changes.items():
        if key in HUMAN_ONLY:
            raise SettingsError(f"review.{key}: only the user may change this setting")
        if key == "scite" and value not in AGENT_SCITE:
            raise SettingsError(
                f"review.scite={value}: only the user may set this; an agent may only "
                "turn scite off")


def _yaml_value(value):
    if isinstance(value, list):
        seq = CommentedSeq()
        for item in value:
            seq.append(_yaml_value(item))
        return seq
    if isinstance(value, dict):
        out = CommentedMap()
        for k, v in value.items():
            out[k] = _yaml_value(v)
        if all(not isinstance(v, (dict, list)) for v in value.values()):
            out.fa.set_flow_style()
        return out
    if isinstance(value, str) and value.lower() in _YAML11_BOOLS:
        # Quoted, so YAML 1.1 readers (PyYAML) see the string, not a boolean.
        return SingleQuotedScalarString(value)
    return value


def write(project: Project, slug: str, changes: dict, actor: str) -> dict:
    """Apply `changes` to the run's `review:` block; returns effective settings.

    A value of None removes the key from the run's block (back to the
    defaults). The whole batch is validated before anything is written.
    """
    if not isinstance(changes, dict):
        raise SettingsError(f"changes: expected a mapping, got {changes!r}")
    unknown = [k for k in changes if k not in DEFAULTS]
    if unknown:
        raise SettingsError(f"review: unknown key {unknown[0]!r} (keys: {', '.join(KEYS)})")
    check_actor(changes, actor)
    validate({k: v for k, v in changes.items() if v is not None})
    ws = _run_dir(project, slug)
    path = ws / "config.yml"
    registry = (ac._load_yaml(project.root / "config" / "agents.yml") or {}).get("agents") or {}
    with store.locked(path):
        before, doc, style = acf._load(path)
        if doc.get("review") is not None and not isinstance(doc.get("review"), dict):
            raise SettingsError(f"workspace/{slug}/config.yml: review: is not a mapping")
        existing = doc.get("review")
        block = existing if isinstance(existing, CommentedMap) else CommentedMap()
        for key, value in list(block.items()):
            # ruamel reads a bare `off` as a string and would write it back
            # bare, which YAML 1.1 readers (PyYAML) take for false: quote it.
            if (isinstance(value, str) and not isinstance(value, SingleQuotedScalarString)
                    and value.lower() in _YAML11_BOOLS):
                block[key] = SingleQuotedScalarString(value)
        for key, value in changes.items():
            if value is None:
                block.pop(key, None)
            elif key not in block or acf._plain(CommentedMap(v=block[key]))["v"] != value:
                block[key] = _yaml_value(value)   # unchanged values keep their formatting
        plain = _block(acf._plain(CommentedMap(review=block)), f"workspace/{slug}/config.yml")
        effective = _merge(project, plain)
        # Only what this write names: a pool that went stale since (agent
        # disabled later) must not block an unrelated change such as scite=off.
        _check_agents({k: plain[k] for k in changes if k in plain}, registry)
        if block:
            doc["review"] = block
        else:
            doc.pop("review", None)
        change = acf._change(path, before, doc, style)
        if change is not None:
            store._atomic_write(path, change.after)
    return effective
