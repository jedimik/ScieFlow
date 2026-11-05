"""The Agents page's form <-> change mapping (T-12 t4).

Pure mapping, no business rule. A submitted form becomes the change texts that
`core.service` parses (`set`, `unset`, `assign`). Each editable control is
rendered with a hidden `init.*` twin holding the value it was rendered with, so
only controls the user changed become changes. Whether a change is allowed, its
value rules and the stale check belong to the service, which also refuses any
forged control this module passes through unchanged.
"""

from __future__ import annotations

import re

from scieflow.core.service import ConfigChangeRefused

#: Form fields that carry no change of their own.
RESERVED = frozenset({"csrf_token", "scope", "slug", "revision", "change"})

#: Keys the browser never writes; a forged one is named as CLI-only.
CLI_ONLY = frozenset({"cmd", "stdin_cmd", "session_cmd", "resume_cmd", "enabled", "promote", "demote"})

#: Characters the assign grammar reads as structure, plus whitespace and control characters.
ROW_SEPARATORS = re.compile(r"[,@/\s\x00-\x1f\x7f]")

AGENT_FIELD = re.compile(r"agent\.(?P<agent>[^.\s]+)\.(?P<field>[A-Za-z0-9_]+)")
NEWS_FIELD = re.compile(r"news\.(?P<field>[A-Za-z0-9_]+)")
ROLE_FIELD = re.compile(r"role\.(?P<role>[A-Za-z0-9_.\-]+)\.(?P<row>[0-9]{1,2})\.(?P<field>agent|model|effort)")


def _single(form, name: str) -> str | None:
    """One text value for `name`; a repeated or non-text field is a refusal."""
    values = form.getlist(name)
    if not values:
        return None
    if len(values) > 1:
        raise ConfigChangeRefused(f"duplicate form field: {name}")
    if not isinstance(values[0], str):
        raise ConfigChangeRefused(f"invalid form field: {name}")
    return values[0]


def _names(form) -> list[str]:
    return list(dict.fromkeys(key for key, _ in form.multi_items()))


def scope_and_slug(form) -> tuple[str, str]:
    """The scope and run slug of a form. A missing scope is refused, never defaulted."""
    scope = _single(form, "scope")
    if not scope:
        raise ConfigChangeRefused("choose a scope first: project defaults, a workspace or news; nothing written")
    return scope, _single(form, "slug") or ""


def revision(form) -> str | None:
    return _single(form, "revision") or None


def change_texts(form) -> list[str]:
    """The canonical change texts an apply form carries (the previewed ones)."""
    return [value for value in form.getlist("change") if isinstance(value, str)]


def submitted_values(form) -> dict[str, str]:
    """The text fields of a posted form, for re-rendering it after a refusal (no token)."""
    out: dict[str, str] = {}
    for name in _names(form):
        value = form.get(name)
        if name not in ("csrf_token", "change") and isinstance(value, str):
            out[name] = value
    return out


def _not_a_control(key: str) -> str:
    if key in CLI_ONLY:
        return f"{key!r} is deliberately CLI-only; change it in a terminal with scieflow agent configure"
    return f"{key!r} is not a browser control; nothing was written"


def changes_from_form(form) -> list[str]:
    """The change texts a preview form asks for: the controls whose value differs from
    the value they were rendered with, plus any canonical `change` texts it carries."""
    singles = {}
    for name in _names(form):
        if name not in RESERVED:
            singles[name] = _single(form, name)
    agents: dict[tuple[str, str], dict[str, str]] = {}
    news: dict[str, str] = {}
    roles: dict[str, dict[int, dict[str, str]]] = {}
    for key, value in singles.items():
        if key.startswith("init."):
            continue
        match = AGENT_FIELD.fullmatch(key)
        if match:
            field = match["field"]
            custom = field.endswith("_custom")
            base = field[: -len("_custom")] if custom else field
            agents.setdefault((match["agent"], base), {})["custom" if custom else "select"] = value.strip()
            continue
        match = NEWS_FIELD.fullmatch(key)
        if match:
            news[match["field"]] = value.strip()
            continue
        match = ROLE_FIELD.fullmatch(key)
        if match:
            roles.setdefault(match["role"], {}).setdefault(int(match["row"]), {})[
                match["field"]] = value.strip()
            continue
        raise ConfigChangeRefused(_not_a_control(key))

    out: list[str] = []
    for (agent, base), slot in sorted(agents.items()):
        merged = slot.get("custom") or slot.get("select") or ""
        init = (singles.get(f"init.agent.{agent}.{base}") or "").strip()
        if merged != init:
            out.append(f"unset {agent}.{base}" if not merged else f"set {agent}.{base}={merged}")
    for field, merged in sorted(news.items()):
        init = (singles.get(f"init.news.{field}") or "").strip()
        if merged != init:
            out.append(f"unset {field}" if not merged else f"set {field}={merged}")
    for role, rows in sorted(roles.items()):
        entries = [rows[index] for index in sorted(rows)]
        for number, entry in enumerate(entries, start=1):
            _check_row(role, number, entry)
        text = role_text(entries)
        init = (singles.get(f"init.role.{role}") or "").strip()
        if text != init:
            out.append(f"unset {role}" if not text else f"assign {role}={text}")
    return out + change_texts(form)


def role_entries(value) -> list[dict]:
    """A stored role value (a name, a mapping, or a list of either) as form entries."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append({"agent": str(item.get("agent") or ""), "model": str(item.get("model") or ""),
                        "effort": str(item.get("reasoning") or "")})
        else:
            out.append({"agent": str(item), "model": "", "effort": ""})
    return out


def _check_row(role: str, number: int, entry: dict) -> None:
    """Refuse a role row the assign grammar cannot carry unchanged (nothing is written).

    The text is the owner's grammar, so a separator typed into a value would change
    its structure (an extra agent, a split model); a model or effort without an agent
    would otherwise vanish silently.
    """
    for field in ("agent", "model", "effort"):
        if ROW_SEPARATORS.search(entry.get(field) or ""):
            raise ConfigChangeRefused(
                f"role {role}, row {number}: {field} may not contain commas, @, /, spaces or "
                "control characters; nothing was written")
    if not (entry.get("agent") or "").strip() and (entry.get("model") or entry.get("effort")):
        raise ConfigChangeRefused(
            f"role {role}, row {number}: choose an agent for the model or effort, or clear the row; "
            "nothing was written")


def role_text(entries) -> str:
    """The `assign` text of a role's rows: AGENT[@MODEL][/EFFORT], comma-separated; blank rows drop.

    An effort without a model is written AGENT@/EFFORT, the owner's form for it."""
    parts = []
    for entry in entries:
        agent = (entry.get("agent") or "").strip()
        if not agent:
            continue
        model = (entry.get("model") or "").strip()
        effort = (entry.get("effort") or "").strip()
        text = agent
        if model or effort:
            text += f"@{model}"
        if effort:
            text += f"/{effort}"
        parts.append(text)
    return ",".join(parts)


def _stored_entries(info: dict) -> list[dict]:
    """A role's stored rows: the agent names of its effective value, each with the model and
    effort its override carries (the owner splits `AGENT@MODEL/EFFORT` the same way)."""
    entries = role_entries(info["value"])
    overrides = info.get("overrides") or {}
    for entry in entries:
        extra = overrides.get(entry["agent"]) or {}
        entry["model"] = entry["model"] or str(extra.get("model") or "")
        entry["effort"] = entry["effort"] or str(extra.get("reasoning") or "")
    return entries


def decorate(view: dict) -> dict:
    """The role rows and their initial text, for rendering a project or workspace view."""
    forms = {}
    for role, info in view["roles"].items():
        entries = _stored_entries(info)
        rows = list(entries)
        if info["many"] or not rows:
            rows.append({"agent": "", "model": "", "effort": ""})
        forms[role] = {"rows": rows, "init": role_text(entries)}
    return view | {"role_forms": forms}

