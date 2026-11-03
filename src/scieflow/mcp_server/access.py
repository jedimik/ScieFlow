"""Access rules of the MCP server (M-I plan 5.6).

The MCP client is always a cloud agent (`MCP_CLIENT` is never a pinned safe agent). Every read
of run content therefore passes M-B's dispatch decision first: an L2 run, a run the decision
refuses, and a run whose decision is not `cloud` is never read, never named, and only counted
(`hidden_runs`). The decision only reads files; it records no event (no `data.warning`, no
`job.refused`), so the server writes nothing (plan 5.6 item 7).
"""

from __future__ import annotations

from pathlib import Path

from scieflow.core.data import dispatch, levels
from scieflow.core.project import Project

MCP_CLIENT = "scieflow-mcp-client"
CLOUD_ONLY = "the MCP client is always a cloud agent"


class Refused(Exception):
    """A run the MCP client may not read. The message names a rule, never a data value."""

    def __init__(self, rule: str, message: str):
        super().__init__(f"refused ({rule}): {message}")
        self.rule = rule


def check_run(project: Project, ws: Path) -> tuple:
    """The data decision for reading one run; returns its warnings (Findings) when served.

    Raises `Refused` for an L2 run, a refused decision, or a decision whose boundary is not cloud.
    """
    if levels.run_level(ws) == "L2":
        raise Refused("l2-cloud", f"run {ws.name} is L2; {CLOUD_ONLY}")
    decision = dispatch.decide(project, ws, MCP_CLIENT, {}, sandboxed=True,
                               templates_use_endpoint=False)
    if not decision.allowed:
        raise Refused(decision.rule or "refused", decision.message)
    if decision.boundary != "cloud":
        raise Refused("mcp-not-cloud", CLOUD_ONLY)
    return tuple(decision.warnings)


def visible_runs(project: Project, rows: list[dict], scope: str | None) -> tuple[list, int]:
    """`list_runs` rows minus L2 runs (by level only: metadata, no data decision), and the count
    of hidden runs. Under a scope only the scoped run is considered."""
    shown: list[dict] = []
    hidden = 0
    for row in rows:
        if scope is not None and row["slug"] != scope:
            continue
        if levels.run_level(project.run_dir(row["slug"])) == "L2":
            hidden += 1
            continue
        shown.append(row)
    return shown, hidden


def served_runs(project: Project, slugs: list[str]) -> tuple[list[str], list[tuple], int]:
    """The run slugs the aggregated `open_gates` may read, their warnings as (slug, Finding)
    pairs, and the number of runs refused (never read, never named)."""
    kept: list[str] = []
    warnings: list[tuple] = []
    hidden = 0
    for slug in slugs:
        ws = project.run_dir(slug)
        if not ws.is_dir():
            continue  # vanished since it was listed: neither served nor counted
        try:
            found = check_run(project, ws)
        except Refused:
            hidden += 1
            continue
        kept.append(slug)
        warnings.extend((slug, finding) for finding in found)
    return kept, warnings, hidden
