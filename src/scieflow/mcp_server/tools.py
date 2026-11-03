"""MCP tool registry (plan 5.5).

Every tool names the one service function it calls, and calls it through the
`scieflow.core.service` module at call time. A tool's `run` returns the whole payload
(`{"result": ...}` plus any extra keys); the protocol layer sanitises it and bounds its size.
Per-run tools are declared here with their final names, titles, schemas and annotations;
their bodies answer "not available yet" until t5 adds them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scieflow.core import service
from scieflow.core.project import Project

ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
               "idempotentHint": True, "openWorldHint": False}

SLUG = {"type": "string", "minLength": 1, "maxLength": 128}
SINCE = {"type": "string", "maxLength": 64}
TYPES = {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 64}}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200}


class ToolError(Exception):
    """A tool-level failure with a message for the client (`isError: true`)."""


@dataclass(frozen=True)
class Context:
    project: Project
    scope: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    service: str
    run: Callable[[Context, dict], dict]

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.input_schema, "annotations": dict(ANNOTATIONS)}


def schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    out: dict[str, Any] = {"type": "object", "properties": properties,
                           "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


def payload(value: Any) -> dict:
    return {"result": value}


def _not_yet(name: str) -> Callable[[Context, dict], dict]:
    def run(ctx: Context, args: dict) -> dict:
        raise ToolError(f"tool {name!r} is not available yet")

    return run


def _workflows(ctx: Context, args: dict) -> dict:
    return payload(service.workflows())


def _conversational_agents(ctx: Context, args: dict) -> dict:
    return payload(service.conversational_agents(ctx.project))


def _agent_settings(ctx: Context, args: dict) -> dict:
    if args.get("slug") or ctx.scope:
        raise ToolError("tool 'agent_settings' for a run is not available yet")
    return payload(service.agent_settings(ctx.project))


_SLUG_ONLY = schema({"slug": SLUG}, ("slug",))
_OPTIONAL_SLUG = schema({"slug": SLUG})
_NO_ARGS = schema({})

TOOLS: tuple[Tool, ...] = (
    Tool("list_runs", "List runs",
         "Runs with their status and budget. Metadata only; runs at data level L2 are hidden.",
         _NO_ARGS, "list_runs_with_budget", _not_yet("list_runs")),
    Tool("run_detail", "Run detail",
         "One run's status, configuration and workspace summary.",
         _SLUG_ONLY, "run_detail", _not_yet("run_detail")),
    Tool("run_events", "Run events",
         "The most recent event records of one run, optionally filtered by time and type.",
         schema({"slug": SLUG, "since": SINCE, "types": TYPES, "limit": LIMIT}, ("slug",)),
         "run_events", _not_yet("run_events")),
    Tool("open_gates", "Open gates",
         "Open approval gates: of one run when a slug is given, otherwise of every run that "
         "the data rules allow the MCP client to read.",
         _OPTIONAL_SLUG, "open_gates", _not_yet("open_gates")),
    Tool("run_charter", "Run charter",
         "The standing goal of one run and its version history.",
         _SLUG_ONLY, "run_charter", _not_yet("run_charter")),
    Tool("conversation", "Run conversation",
         "The conversation state of one run: its agent, turns in flight and recent messages.",
         _SLUG_ONLY, "conversation_state", _not_yet("conversation")),
    Tool("run_overview", "Run overview",
         "The four read-only panels of one run's overview.",
         _SLUG_ONLY, "run_overview", _not_yet("run_overview")),
    Tool("workbench", "Run workbench",
         "The workbench view of one run: its curation blocks and sources.",
         _SLUG_ONLY, "workbench", _not_yet("workbench")),
    Tool("curation_history", "Curation history",
         "The version history of one run's curation.",
         _SLUG_ONLY, "curation_history", _not_yet("curation_history")),
    Tool("agent_settings", "Agent settings",
         "The agent registry, or the settings resolved for one run when a slug is given.",
         _OPTIONAL_SLUG, "agent_settings", _agent_settings),
    Tool("workflows", "Workflows",
         "The workflows the Start wizard offers.",
         _NO_ARGS, "workflows", _workflows),
    Tool("conversational_agents", "Conversational agents",
         "Agents that can hold a run's conversation.",
         _NO_ARGS, "conversational_agents", _conversational_agents),
)

_BY_NAME = {tool.name: tool for tool in TOOLS}


def find(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def tools_list() -> list[dict]:
    return [tool.describe() for tool in TOOLS]


def _short(text: str) -> str:
    return repr(text[:64])


def _check_value(spec: dict, value: Any) -> str | None:
    kind = spec.get("type")
    if kind == "string":
        if not isinstance(value, str):
            return "must be a string"
        if len(value) < spec.get("minLength", 0):
            return "is too short"
        if len(value) > spec.get("maxLength", len(value)):
            return f"is longer than {spec['maxLength']} characters"
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return "must be an integer"
        if "minimum" in spec and value < spec["minimum"]:
            return f"must be at least {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return f"must be at most {spec['maximum']}"
    elif kind == "array":
        if not isinstance(value, list):
            return "must be an array"
        if len(value) > spec.get("maxItems", len(value)):
            return f"has more than {spec['maxItems']} items"
        for item in value:
            problem = _check_value(spec.get("items", {}), item)
            if problem:
                return f"must be an array whose items {problem}"
    return None


def check_arguments(tool: Tool, arguments: dict) -> str | None:
    """None when the arguments fit the tool's input schema, else a message naming the argument
    (never its value)."""
    props = tool.input_schema.get("properties", {})
    for key in arguments:
        if key not in props:
            return f"unknown argument {_short(str(key))}"
    for key in tool.input_schema.get("required", []):
        if key not in arguments:
            return f"missing required argument {_short(key)}"
    for key, value in arguments.items():
        problem = _check_value(props[key], value)
        if problem:
            return f"argument {_short(key)} {problem}"
    return None
